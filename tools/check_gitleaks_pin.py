#!/usr/bin/env python3
"""Require the gitleaks version .github/workflows/quality.yml pins on the local PATH. Offline, stdlib only.

Quality runs on a pull request only when a label is added to it, and the merge lane labels only the train
pull request, so a member head gets no Quality run at all. Its evidence is its named suites plus every
generated-file --check and the manifest check, run locally before its push (a process rule, not enforced
per head), and the train pull request's Quality run on the identical tree is the standing full evidence.
Whenever the full tools/run_all_checks.sh runs, it includes the gitleaks scan only when gitleaks is
installed, so this gate makes the scan required there: tools/run_all_checks.sh runs it right after its
gitleaks block, so the user-local ~/.local/bin fallback that block appends to PATH applies here too, and
the run FAILS, never ends in a pass with the scan not run.

  check_gitleaks_pin.py              check the local gitleaks against the pin
  check_gitleaks_pin.py --self-test  run the deterministic vectors (stub binaries, no real gitleaks)

The pin is the GITLEAKS_VERSION: X.Y.Z entry of the env: of the one step named Secrets (gitleaks) in
quality.yml, the step that downloads that version and verifies it by checksum. The whole file is read only
inside a closed subset of YAML, and every line outside it is refused, naming the line. The subset holds:
block mappings and block sequences, each key or item at the column of its collection and each nested
collection indented past the key or item that holds it (a - item may open a mapping or another sequence
on its own line); scalars on their key's or item's line, either plain (holding no ': ' and not ending in
a colon, a ' #' starting its comment), single-quoted or double-quoted and closed on that line, or a flow
sequence of plain words ([a, b]) opened and closed on that line; block scalars introduced by | or > with
their indicators, whose text is every later line that is blank or indented past that key or item, each
text line indented at least as far as the text (the header's digit past that column, or else the first
text line; a blank line above that first line indented past it is refused); comments and blank lines.
Every key is a plain identifier written exactly as name: with a space or the line's end after the colon,
and a line indents with spaces only and holds no tab before its first ' #'. So a flow mapping, any other
flow sequence, a quoted scalar left open at the end of its line, a tag, an anchor, an alias, a document
marker or directive, a merge key and a key spelled any other way (quoted, tagged, anchored, an alias,
explicit with ?, or with a space before its colon) are each outside it. Within every mapping a repeated
key is refused, naming both lines, because a YAML reader keeps at most one copy of it. Inside that subset
every line sits where a YAML reader places it, so the step is the item of a job's steps: list under the
top-level jobs: mapping whose name: reads Secrets (gitleaks), plain or quoted (its escapes decoded); a
step name: given as a block scalar, or holding an escape YAML does not define, is refused. Only that
entry decides: any other line of quality.yml naming GITLEAKS_VERSION (another step's or job's env:, a run
script assigning it), other than a comment and a ${GITLEAKS_VERSION} expansion in that step's run: script,
is refused, so a value set elsewhere can never stand in for the step's own. The file is read as LF lines
once one trailing carriage return is removed from each, and every line is screened before any is parsed:
a line that still carries a carriage return, or carries U+0085, U+2028 or U+2029, is refused, because a
YAML reader may break a line there and read a key this line reader would not see; and so is a line that
carries any other character outside YAML's printable set (tab, LF, CR, U+0020-U+007E, U+0085,
U+00A0-U+D7FF, U+E000-U+FFFD and U+10000-U+10FFFF), that is a C0 control other than tab, LF and CR, DEL,
a C1 control other than U+0085, a surrogate, U+FFFE or U+FFFF, because a YAML reader refuses the whole
file there. The local binary is the first
gitleaks on PATH; its version is the whole output of gitleaks version, read as X.Y.Z with an optional
leading v. It fails (exit 1) when no gitleaks is on PATH and when the version it reports differs from the
pin, so a member push cannot rest on a local run that skipped the scan or ran another version. Any other
argument is refused (exit 2).

Exit convention (matches the repo's gates):
  0  the gitleaks on PATH reports the pinned version
  1  gitleaks is not on PATH, or it reports another version
  2  cannot evaluate: quality.yml is unreadable; a line of it still carries a carriage return once its
     one trailing CR is removed, or carries U+0085, U+2028 or U+2029, or carries any other character
     outside YAML's printable set; a line of it is outside the accepted subset above, or repeats a key of
     its mapping; it holds no step item named Secrets (gitleaks) or more than one, or a step name: this
     reader does not decode; that step has no env: or no run:, or an env: that is not a block mapping; its
     env: has no GITLEAKS_VERSION entry, or one whose value is not a bare X.Y.Z; another line names
     GITLEAKS_VERSION; gitleaks version fails, times out or prints anything but one version; or the
     arguments are not recognized

NOT CHECKED: the binary's checksum (CI verifies the downloaded archive; this gate trusts the version a
local binary reports, so a binary that misreports its version passes), the scan's own result (the gitleaks
block of tools/run_all_checks.sh runs it), a gitleaks reached other than through PATH, a version the
workflow reaches without naming GITLEAKS_VERSION literally (a hard-coded download URL, or a name built at
run time and written to GITHUB_ENV or assigned in a script), and two keys a YAML 1.1 reader resolves to the
same boolean or null (on and ON, say), which this reader compares as written.
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
# The gitleaks step's name: and the name its env: pins.
STEP_NAME = "Secrets (gitleaks)"
PIN_KEY = "GITLEAKS_VERSION"
# A key: a plain identifier written exactly as name:, with the spaces after its colon or the line's end.
KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: +|$)")
# What may follow a quoted scalar, a block scalar header or a flow sequence on its line: nothing, or spaces
# and then a comment.
TAIL = r"(?: +(?:#.*)?)?"
TAIL_RE = re.compile(TAIL)
# A quoted scalar its line closes.
QUOTED_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"")
# The escapes of a double-quoted scalar: each one-character escape with its value, and the hexadecimal ones
# with their digit counts.
ESCAPES = {"0": "\x00", "a": "\x07", "b": "\x08", "t": "\t", "\t": "\t", "n": "\n", "v": "\x0b", "f": "\x0c",
           "r": "\r", "e": "\x1b", " ": " ", "\"": "\"", "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
           "L": "\u2028", "P": "\u2029"}
HEX_ESCAPE_RE = re.compile(r"x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}")
# A block scalar header: | or >, its indentation digit and chomping indicator in either order, then the tail.
BLOCK_RE = re.compile(r"[|>](?:([1-9])[+-]?|[+-]([1-9])?)?" + TAIL)
# The one flow collection read: a flow sequence of plain words, opened and closed on its line.
FLOW_WORDS_RE = re.compile(r"(\[[A-Za-z0-9_][A-Za-z0-9_.-]*(?:, [A-Za-z0-9_][A-Za-z0-9_.-]*)*\])" + TAIL)
# A YAML document marker or directive, at column 0.
DOCUMENT_RE = re.compile(r"(?:---|\.\.\.)(?: |$)|%")
# The pin entry, once its indentation is removed.
PIN_RE = re.compile(r"GITLEAKS_VERSION: ([0-9]+\.[0-9]+\.[0-9]+)")
# The only spelling of the name the step's run: script may carry: its expansion.
EXPANSION = "${GITLEAKS_VERSION}"
# The whole output of gitleaks version, once surrounding whitespace is removed.
VERSION_RE = re.compile(r"v?([0-9]+\.[0-9]+\.[0-9]+)")
VERSION_TIMEOUT = 60
# Characters a YAML reader may take as a line break where this reader splits on LF alone, so a key
# hidden behind one would be read there and not here; each is refused once a line's one trailing CR
# is removed.
LINE_BREAKS = ("\r", "\x85", "\u2028", "\u2029")
# A character outside YAML's printable set (tab, LF, CR, U+0020-U+007E, U+0085, U+00A0-U+D7FF, U+E000-U+FFFD
# and U+10000-U+10FFFF): a C0 control other than tab, LF and CR, DEL, a C1 control other than U+0085, a
# surrogate, U+FFFE or U+FFFF. A YAML reader refuses the whole file there, where this reader would read on.
NONPRINTABLE_RE = re.compile(r"[^\t\n\r\x20-\x7e\x85\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def _value(rest):
    """(kind, text) for rest, a line's text after its sequence indicators and its key, or (None, why not).

    kind is "empty" (nothing but a comment, if anything), "block" (a block scalar header; text is its
    indentation digit, or "" when YAML takes the indentation from the text), "plain" or "quoted" (a scalar;
    text is the scalar without its comment) or "flow" (a flow sequence of plain words)."""
    lead = rest[:1]
    if not rest or lead == "#":
        return "empty", ""
    if lead in ("|", ">"):
        header = BLOCK_RE.fullmatch(rest)
        if header is None:
            return None, "writes a block scalar header other than | or > with its indicators"
        return "block", header.group(1) or header.group(2) or ""
    if lead in ("'", "\""):
        quoted = QUOTED_RE.match(rest)
        if quoted is None:
            return None, "opens a quoted scalar it does not close"
        if TAIL_RE.fullmatch(rest, quoted.end()) is None:
            return None, "writes more than a comment after the quoted scalar it closes (a quoted key, say)"
        return "quoted", quoted.group()
    if lead == "[":
        flow = FLOW_WORDS_RE.fullmatch(rest)
        if flow is None:
            return None, "opens a flow sequence that is not one line of plain words"
        return "flow", flow.group(1)
    if lead == "{":
        return None, "opens a flow mapping"
    if lead == "!":
        return None, "writes a tag"
    if lead == "&":
        return None, "writes an anchor"
    if lead == "*":
        return None, "writes an alias"
    if lead in ("?", ":", "-") and rest[1:2] in ("", " "):
        return None, "writes an explicit key (?), a value indicator (:) or a sequence item where a value belongs"
    if lead in (",", "]", "}", "%", "@", "`"):
        return None, "starts a scalar with a character YAML reserves"
    plain = rest.split(" #", 1)[0].rstrip(" ")
    if ": " in plain or plain.endswith(":"):
        return None, "writes a key other than as a plain identifier followed by a colon and a space"
    return "plain", plain


def _open(stack, column, kind):
    """(the mapping (kind dict) or sequence (kind list) that a key or sequence item at column continues or
    opens, None), or (None, why not). stack holds the open nodes, innermost last, each (column, node, open):
    a mapping or sequence, or an entry whose value is still open (a key or item with nothing after it on its
    line). A key or item past an open entry's column opens that entry's value; at its column the entry stays
    empty and the collection holding it continues."""
    while stack and stack[-1][0] > column:
        stack.pop()
    if not stack:
        return None, "is indented less than the first line of the document"
    top, node, is_open = stack[-1]
    if is_open and top < column:
        node[1] = kind()
        stack[-1] = (column, node[1], False)
        return node[1], None
    # An indentless sequence. A sibling refusal also covers this case: the open key's mapping sits at the
    # item's column, so without this check the kind check below still refuses the line, as a sequence item
    # at the column of the keys above it. This check names the line for what it is.
    if is_open and kind is list and len(stack) > 1 and type(stack[-2][1]) is dict:
        return None, "holds a sequence item at the column of the key whose value it would be"
    if is_open:
        stack.pop()
        top, node, is_open = stack[-1]
    if top < column:
        return None, "is indented past a line whose value is complete (a continuation line, say)"
    if type(node) is not kind:
        return None, "holds a {} at the column of the {} above it".format(
            "key" if kind is dict else "sequence item", "sequence items" if kind is dict else "keys")
    return node, None


def _text_end(lines, header, column, digit):
    """(the index after the last text line of the block scalar whose header is on lines[header], None), or
    (None, why not). Its text is every later line that is blank or indented past column, the column of its key
    or sequence item. YAML takes the text's indentation from the header's digit past that column, or else from
    the first text line that is not blank, so a line indented past column but less than that, and a blank line
    above that first text line indented past it, are refused."""
    width = column + int(digit) if digit else None
    blank = 0
    end = header + 1
    while end < len(lines):
        text = lines[end].lstrip(" ")
        indent = len(lines[end]) - len(text)
        if not text:
            blank = max(blank, indent)
        elif indent <= column:
            break
        else:
            if width is None:
                if blank > indent:
                    return None, ("a blank line above line {}, the first text line of the block scalar on line "
                                  "{}, is indented past it".format(end + 1, header + 1))
                width = indent
            if indent < width:
                return None, ("line {} is indented past the key of the block scalar on line {} but less than "
                              "its text".format(end + 1, header + 1))
        end += 1
    return end, None


def _parse(lines):
    """(the root node of lines, None) when every line is inside the accepted subset, or (None, why not, naming
    the line). A mapping is a dict from key to entry, a sequence a list of entries, and an entry a list [the
    index of its line, its value]; a value is a mapping, a sequence, None when empty, (kind, text) for a
    scalar on its line (_value) or ("block", the index of its header line, the index after its text)."""
    root = [None, None]
    stack = [(-1, root, True)]
    skip = 0
    for index, line in enumerate(lines):
        if index < skip:
            continue
        text = line.lstrip(" ")
        indent = len(line) - len(text)
        if not text or text.startswith("#"):
            continue
        # A sibling refusal also covers this case: such a line holds no key or sequence item, so without this
        # check it is still refused, as a scalar with no key or item before it, a reserved first character (%),
        # a ': ' in a plain scalar or a tab outside a comment. This check names the line for what it is.
        if indent == 0 and DOCUMENT_RE.match(text):
            return None, "line {} is a YAML document marker or directive".format(index + 1)
        if "\t" in text.split(" #", 1)[0]:
            return None, "line {} carries a tab outside a comment".format(index + 1)
        entry = None
        offset = 0
        while text[offset:offset + 1] == "-" and text[offset + 1:offset + 2] in ("", " "):
            node, why = _open(stack, indent + offset, list)
            if why is not None:
                return None, "line {} {}".format(index + 1, why)
            entry = [index, None]
            node.append(entry)
            stack.append((indent + offset, entry, True))
            offset += 1
            while text[offset:offset + 1] == " ":
                offset += 1
        key = KEY_RE.match(text, offset)
        if key is not None:
            node, why = _open(stack, indent + offset, dict)
            if why is not None:
                return None, "line {} {}".format(index + 1, why)
            name = key.group(1)
            if name in node:
                return None, ("lines {} and {} both hold the {}: key of one mapping, of which a YAML reader keeps "
                              "at most one copy".format(node[name][0] + 1, index + 1, name))
            entry = [index, None]
            node[name] = entry
            stack.append((indent + offset, entry, True))
            offset = key.end()
        kind, value = _value(text[offset:])
        if entry is None:
            return None, "line {} {}".format(index + 1, value if kind is None else (
                "holds a scalar with no key or sequence item before it on its line"))
        if kind is None:
            return None, "line {} {}".format(index + 1, value)
        if kind == "empty":
            continue
        column = stack.pop()[0]
        if kind == "block":
            skip, why = _text_end(lines, index, column, value)
            if why is not None:
                return None, why
            entry[1] = ("block", index, skip)
        else:
            entry[1] = (kind, value)
    return root[1], None


def _unquote(text):
    """The value of the one-line quoted scalar text, or None when it holds an escape YAML does not define."""
    if text[0] == "'":
        return text[1:-1].replace("''", "'")
    parts = []
    index = 1
    while index < len(text) - 1:
        if text[index] != "\\":
            parts.append(text[index])
            index += 1
            continue
        code = text[index + 1]
        if code in ESCAPES:
            parts.append(ESCAPES[code])
            index += 2
            continue
        digits = HEX_ESCAPE_RE.match(text, index + 1)
        if digits is None or int(digits.group()[1:], 16) > 0x10FFFF:
            return None
        parts.append(chr(int(digits.group()[1:], 16)))
        index = digits.end()
    return "".join(parts)


def _step_items(root):
    """(the mappings of the step items named Secrets (gitleaks), None), or (None, why not): an item counts when
    it is in the steps: list of a job id key of the top-level jobs: mapping and its name: reads Secrets
    (gitleaks), plain or quoted. A step name: this reader does not decode (a block scalar, or a double-quoted
    scalar with an escape YAML does not define) is refused."""
    items = []
    jobs = root.get("jobs") if isinstance(root, dict) else None
    for job in (jobs[1].values() if jobs is not None and isinstance(jobs[1], dict) else ()):
        steps = job[1].get("steps") if isinstance(job[1], dict) else None
        for item in (steps[1] if steps is not None and isinstance(steps[1], list) else ()):
            name = item[1].get("name") if isinstance(item[1], dict) else None
            if name is None or not isinstance(name[1], tuple):
                continue
            kind, text = name[1][:2]
            if kind == "quoted":
                text = _unquote(text)
            if kind == "block" or text is None:
                return None, "line {} writes a step name: this reader does not decode".format(name[0] + 1)
            if text == STEP_NAME:
                items.append(item[1])
    return items, None


def read_pin(workflow_text):
    """(version, None) for the GITLEAKS_VERSION entry of the gitleaks step's env:, or (None, why not)."""
    lines = [line[:-1] if line.endswith("\r") else line for line in workflow_text.split("\n")]
    for index, line in enumerate(lines):
        if any(mark in line for mark in LINE_BREAKS):
            return None, "line {} carries a carriage return, U+0085, U+2028 or U+2029".format(index + 1)
        outside = NONPRINTABLE_RE.search(line)
        if outside is not None:
            return None, "line {} carries U+{:04X}, a character outside YAML's printable set".format(
                index + 1, ord(outside.group()))
    root, why = _parse(lines)
    if why is not None:
        return None, why
    steps, why = _step_items(root)
    if why is not None:
        return None, why
    if len(steps) != 1:
        return None, "{} steps are named Secrets (gitleaks), not one".format(len(steps))
    env, run = steps[0].get("env"), steps[0].get("run")
    if env is None or run is None:
        return None, "the Secrets (gitleaks) step has {} env: and {} run: keys, not one each".format(
            int(env is not None), int(run is not None))
    if not isinstance(env[1], dict):
        return None, "the step's env: on line {} is not a block mapping".format(env[0] + 1)
    entry = env[1].get(PIN_KEY)
    if entry is None:
        return None, "the step's env: has no {} entry".format(PIN_KEY)
    run_end = run[1][2] if isinstance(run[1], tuple) and run[1][0] == "block" else run[0] + 1
    for index, line in enumerate(lines):
        if (PIN_KEY not in line or index == entry[0] or line.lstrip(" ").startswith("#")
                or (run[0] <= index < run_end and PIN_KEY not in line.replace(EXPANSION, ""))):
            continue
        return None, ("line {} names {} outside the step's env: entry, a comment and the step's {} "
                      "expansions".format(index + 1, PIN_KEY, EXPANSION))
    pin = PIN_RE.fullmatch(lines[entry[0]].lstrip(" "))
    if pin is None:
        return None, "the step's {} value on line {} is not a bare X.Y.Z".format(PIN_KEY, entry[0] + 1)
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
        # an env: that is not a block mapping, a pin nested deeper than an env: entry, a run script
        # assigning the name, and a quoted or escaped second key that YAML reads as GITLEAKS_VERSION.
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
                ("env-not-a-mapping", workflow.replace("        env:\n" + pin_line + sha_line,
                                                       "        env: x\n")),
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

        # The step is read only where YAML reads it, as a step item of a job's steps: list under the
        # top-level jobs:. Each of these holds the step's lines, its pin included, only where YAML reads no
        # step (the real step removed), so a reader that takes any line reading as the step once its
        # indentation is removed passes each: inside another step's run: | text, inside a job id's block
        # scalar, after a document marker that opens a root block scalar, after a run: value that opens a
        # quote its line does not close, as an item of a steps: key under another step's with:, as an item
        # of a job's needs:, under a sequence item of jobs:, under a top-level key other than jobs:, and as
        # an item indented less than the first item of its steps: list.
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
        # The step's lines inside another step's run: | text are text, so they hold no step; beside the real
        # step, that text's copy of the pin is a line naming GITLEAKS_VERSION outside the step's env:.
        beside = workflow + script_step + indented(step, 4)
        check("step-in-run-text-is-text", read_pin(in_script)[1], "0 steps are named Secrets (gitleaks), not one")
        check("step-copy-beside-real-step", read_pin(beside)[1],
              "line {} names GITLEAKS_VERSION outside the step's env: entry, a comment and the step's "
              "${{GITLEAKS_VERSION}} expansions".format(beside.split("\n").index("    " + pin_line[:-1]) + 1))
        # Controls: a run: | block that ends at the step's item, quoted values closed on their lines and a
        # comment at column 0 between steps each leave the step read.
        for name, text in (
                ("control-scalar-before-step", head + script_step + step),
                ("control-closed-quotes", head + "      - uses: actions/setup-python@v5\n"
                 "        name: \"Set up \\\"Python\\\"\"\n"
                 "        with:\n          python-version: '3.14'\n" + step),
                ("control-column-0-comment", head + other + "# a note\n" + step)):
            check(name, evaluate(text, pinned_path)[0], 0)
        # A key repeated within one mapping, at any level and among the step's own keys: a YAML reader keeps
        # at most one copy, so a reader that reads the step or its pin under either copy passes each. The
        # step or pin sits under the first copy, or under the last one when the first is empty or holds
        # something else.
        shell_line = "        shell: bash\n"
        lint_job = "  lint:\n    runs-on: ubuntu-latest\n    steps:\n" + other
        second_env = "        env:\n          OTHER: x\n"
        for name, text in (
                ("repeated-jobs-key", workflow + "jobs:\n" + lint_job),
                ("repeated-job-id", workflow + "  secrets:\n    runs-on: ubuntu-latest\n    steps:\n" + other),
                ("repeated-steps-key", workflow + "    steps:\n" + other),
                ("repeated-key-step-under-last-copy", "jobs:\n" + workflow),
                ("repeated-step-env-pin-under-first-copy", workflow.replace(sha_line, sha_line + second_env)),
                ("repeated-step-env-pin-under-last-copy",
                 workflow.replace(shell_line, shell_line + second_env, 1)),
                ("repeated-step-run-key", workflow.replace(sha_line, sha_line + "        run: echo\n")),
                ("repeated-step-name-key", workflow.replace(shell_line, shell_line + "        name: x\n", 1)),
                ("repeated-key-off-the-step-path", "name: a\nname: b\n" + workflow)):
            check(name, evaluate(text, pinned_path)[0], 2)
        check("repeated-key-names-lines", read_pin(workflow + "    steps:\n" + other)[1],
              "lines 4 and {} both hold the steps: key of one mapping, of which a YAML reader keeps at most one "
              "copy".format(workflow.count("\n") + 1))
        check("repeated-step-key-names-lines", read_pin(workflow.replace(sha_line, sha_line + second_env))[1],
              "lines 8 and 11 both hold the env: key of one mapping, of which a YAML reader keeps at most one "
              "copy")
        # Controls: a distinct sibling at each level (a top-level key beside jobs:, a job id beside the
        # step's job and a job key beside its steps:), another job's steps: key, and keys that only start
        # with jobs, steps or the job id, or carry jobs or steps as their value, each leave the step read.
        for name, text in (
                ("control-distinct-siblings", "name: Quality\non: [pull_request]\njobs:\n" + lint_job
                 + "  secrets:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n" + step),
                ("control-key-prefix-and-value", "name: jobs\njobs-note: x\n" + head.replace(
                    "    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n    steps-note: steps\n")
                 + step + "  secrets-2:\n    runs-on: ubuntu-latest\n    steps:\n" + other)):
            check(name, evaluate(text, pinned_path)[0], 0)

        curl_line = step.splitlines(True)[-1]

        def in_job(text):
            return workflow.replace("    runs-on: ubuntu-latest\n", "    runs-on: ubuntu-latest\n" + text, 1)

        def before_step(text):
            return workflow.replace(step, text + step, 1)

        def spans(opening, closing):
            # A step before the gitleaks step ends with the line opening, which YAML continues across the
            # gitleaks step to closing, put after the last line of that step's run: | text.
            return (head + "      - name: Secret scan self-test\n        shell: bash\n" + opening
                    + step.replace(curl_line, curl_line + closing) + "\n" + other)

        # Every line outside the accepted subset is refused, naming it, the spellings above of a repeated key
        # included. In each of these a reader without the refusals the text meets (one, or two where a line
        # holding no key follows), taking each construct for a plain value and skipping each line that holds
        # no key, reads the step and the pin the stub reports, and passes: a flow mapping or flow sequence
        # whose quote YAML continues across the gitleaks step, which then sits inside that scalar (closed in
        # the step's run: text, or at its key column), and each on one line; a quoted scalar left open at the
        # end of its line; a tag, an anchor and an alias; a document marker or directive; a key spelled other
        # than name: (spaced, quoted, tagged, explicit or merge), on an item and as a second env: of the step;
        # a sequence item after a key; a scalar that starts with a reserved character or holds ': '; an
        # indentless sequence; a key or item at the column of the other kind; a line indented past a complete
        # value or less than the document; a plain scalar continued on the next line; a tab outside a
        # comment; a block scalar header with text after it; block scalar text indented less than its first
        # line or below a blank line indented further; and a step name: this reader does not decode, beside
        # an escaped one that decodes to the step's own name.
        for name, text in (
                ("flow-mapping-quote-spans-step", spans("        env: {NOTE: 'x\n", "          y'}\n")),
                ("flow-mapping-sequence-spans-step", spans("        env: {NOTE: ['x\n", "          y']}\n")),
                ("flow-mapping-closed-at-key-column", spans("        env: {NOTE: 'x\n", "        y'}\n")),
                ("flow-mapping-one-line", in_job("    defaults: {run: {shell: bash}}\n")),
                ("flow-sequence-quote-spans-step", spans("        args: ['x\n", "          y']\n")),
                ("flow-sequence-quoted-word", in_job("    needs: ['lint']\n")),
                ("flow-sequence-nested", in_job("    needs: [lint, [site]]\n")),
                ("flow-sequence-colon", in_job("    needs: [lint: site]\n")),
                ("single-quote-left-open", spans("        args: 'x\n", "          y'\n")),
                ("double-quote-left-open", spans("        args: \"x\n", "          y\"\n")),
                ("tag", in_job("    timeout-minutes: !!int 5\n")),
                ("anchor", in_job("    timeout-minutes: &minutes 5\n")),
                ("alias", in_job("    timeout-minutes: *minutes\n")),
                ("document-end-marker", workflow + "...\n"),
                ("directive", "%YAML 1.1\n" + workflow),
                ("spaced-key-copy-of-entry", workflow.replace(sha_line, sha_line + "        env :\n" + sha_line)),
                ("spaced-key-second-env",
                 workflow.replace(sha_line, sha_line + "        env :\n          OTHER: x\n")),
                ("quoted-key-second-env", workflow.replace(
                    sha_line, sha_line + "        \"env\":\n          \"GITLEAKS_\\x56ERSION\": 8.30.2\n")),
                ("tagged-key-second-env", workflow.replace(
                    sha_line, sha_line + "        !!str env:\n          \"GITLEAKS_\\x56ERSION\": 8.30.2\n")),
                ("quoted-key-on-item", before_step("      - \"uses\": actions/checkout@v4\n")),
                ("explicit-key-on-item", before_step("      - ? uses\n")),
                ("merge-key-on-item", before_step("      - <<: *defaults\n")),
                ("spaced-key-on-item", before_step("      - uses : actions/checkout@v4\n")),
                ("value-indicator-on-item", before_step("      - : x\n")),
                ("sequence-item-after-key", in_job("    needs: - lint\n")),
                ("reserved-lead", in_job("    timeout-minutes: @5\n")),
                ("colon-in-plain-value", in_job("    timeout-minutes: 5: x\n")),
                ("indentless-steps-list", head + "".join(
                    line[2:] if line.strip() else line for line in (step + "\n" + other).splitlines(True))),
                ("key-at-item-column", workflow + "      note: x\n"),
                ("item-at-key-column", in_job("    - x\n")),
                ("indented-past-complete-value", in_job("      group: x\n")),
                ("indented-less-than-document", indented(workflow, 2) + "name: x\n"),
                ("plain-continuation-line", workflow.replace(other_run, other_run + "          --self-test\n")),
                ("tab-after-value", in_job("    timeout-minutes: 5\t# minutes\n")),
                ("tab-separator-in-item", before_step("      - uses#x:\tactions/checkout@v4\n")),
                ("block-header-text", workflow.replace(
                    other_run, "        run: |  x\n          python3 -I -B tools/check_leaks.py\n")),
                ("block-text-less-indented", workflow.replace(
                    "          set -euo pipefail\n", "            set -euo pipefail\n")),
                ("block-blank-above-text",
                 workflow.replace("        run: |\n", "        run: |\n              \n")),
                ("step-name-escaped-reads-as-step",
                 before_step("      - name: \"Secrets \\x28gitleaks)\"\n        run: echo\n")),
                ("step-name-undefined-escape",
                 before_step("      - name: \"Secrets \\q(gitleaks)\"\n        run: echo\n")),
                ("step-name-block-scalar",
                 before_step("      - name: >-\n          Secrets (gitleaks)\n        run: echo\n")),
                ("repeated-jobs-key-double-quoted", workflow + "\"jobs\": {}\n"),
                ("repeated-job-id-single-quoted", workflow + "  'secrets': {}\n"),
                ("repeated-steps-key-tag-anchor", workflow + "    !!str &copy steps: []\n"),
                ("repeated-steps-key-space-before-colon", workflow + "    steps : []\n"),
                ("repeated-jobs-key-tab-separator", workflow + "jobs:\t{}\n"),
                ("repeated-job-id-escaped", workflow + "  \"secr\\x65ts\": {}\n"),
                ("repeated-jobs-key-alias", "name: &copy jobs\n" + workflow + "*copy : {}\n"),
                ("repeated-jobs-key-explicit", workflow + "? jobs\n: {}\n")):
            check(name, evaluate(text, pinned_path)[0], 2)
        # The refusal names the line of the construct.
        for name, text, opening, why in (
                ("flow-mapping-names-line", spans("        env: {NOTE: 'x\n", "          y'}\n"),
                 "        env: {NOTE: 'x", "opens a flow mapping"),
                ("open-quote-names-line", spans("        args: 'x\n", "          y'\n"),
                 "        args: 'x", "opens a quoted scalar it does not close"),
                ("spaced-key-names-line", workflow.replace(sha_line, sha_line + "        env :\n" + sha_line),
                 "        env :",
                 "writes a key other than as a plain identifier followed by a colon and a space")):
            check(name, read_pin(text)[1], "line {} {}".format(text.split("\n").index(opening) + 1, why))
        # Controls: each accepted construct leaves the step read. Nested block sequences; plain scalars
        # holding a # with no space before it, a colon with no space after it, brackets, braces and quotes
        # past their first character, a comment holding ': ', and a leading -, : or ? before a non-space;
        # quoted scalars holding '', escapes, ': ' and ' #' before a comment; the step's name: single- or
        # double-quoted; block scalars with each indicator, whose text holds lines that read as a step, a
        # comment, an open quote and a flow collection, with an indentation digit below a deeper first line;
        # comment lines at, past and before the keys' column, trailing comments and blank lines holding
        # spaces; one-line flow sequences of plain words; and keys holding - and _ or left empty.
        block_step = ("      - name: Block scalars\n"
                      "        shell: bash\n"
                      "        run: |-\n"
                      "          - name: Secrets (gitleaks)\n"
                      "          # text, not a comment\n"
                      "            key: 'open {flow: [x\n"
                      "\n"
                      "        with:\n"
                      "          a: >+\n"
                      "            folded &anchor *alias !tag\n"
                      "          b: |2\n"
                      "              deeper first\n"
                      "            then at the digit\n"
                      "          c: >-2\n"
                      "            x\n"
                      "          d: |+1\n"
                      "           one past\n")
        comments = workflow.replace(
            shell_line, shell_line + "            # past the keys\n  # before the keys\n   \n", 1).replace(
            "        env:\n", "        env:  # the pin\n", 1).replace(
            "      - name: Leak denylist\n", "      - name: Leak denylist  # trailing\n")
        for name, text in (
                ("control-nested-sequences", in_job("    note:\n      - - lint\n        - site\n      - - x\n")),
                ("control-plain-scalars", in_job(
                    "    note-a: a#b c:d https://x.invalid/{x}[y] it's ${{ github.x }}  # see: below\n"
                    "    note-b: -x\n    note-c: :x\n    note-d: ?x\n")),
                ("control-quoted-scalars",
                 in_job("    note-a: 'it''s: #'  # c\n    note-b: \"say \\\"hi\\\": # \\t\"\n")),
                ("control-single-quoted-step-name",
                 workflow.replace("- name: Secrets (gitleaks)", "- name: 'Secrets (gitleaks)'")),
                ("control-double-quoted-step-name",
                 workflow.replace("- name: Secrets (gitleaks)", "- name: \"Secrets (gitleaks)\"")),
                ("control-block-scalars", before_step(block_step)),
                ("control-comments-and-blank-lines", "# column 0\n" + comments),
                ("control-flow-sequence-of-plain-words",
                 in_job("    needs: [lint, site]  # both\n    tags: [ubuntu-latest, macos-latest]\n")),
                ("control-keys", "_top: x\n" + in_job("    run-name_2-x: y\n    empty:\n    after-empty: z\n"))):
            check(name, evaluate(text, pinned_path)[0], 0)
        # A carriage return, U+0085, U+2028 or U+2029 inside a line can hide a second key from this LF
        # reader that a YAML reader breaks out (the step's value stays 8.30.1 here, so a reader without
        # the refusal passes each); a trailing CR on every line (a CRLF file) is removed and reads as LF.
        hidden = "          \"GITLEAKS_\\x56ERSION\": '8.30.2'\n"
        check("crlf-file", evaluate(workflow.replace("\n", "\r\n"), pinned_path)[0], 0)
        for name, mark in (("cr-hides-key", "\r"), ("nel-hides-key", "\x85"),
                           ("ls-hides-key", "\u2028"), ("ps-hides-key", "\u2029")):
            check(name, evaluate(workflow.replace(sha_line, sha_line[:-1] + mark + hidden), pinned_path)[0], 2)
        # The same marks in a comment line, which no other refusal reads, so a reader without this one reads
        # the step and passes each.
        comment = "      # Bump"
        for name, mark in (("cr-in-comment", "\r"), ("nel-in-comment", "\x85"),
                           ("ls-in-comment", "\u2028"), ("ps-in-comment", "\u2029")):
            check(name, read_pin(workflow.replace(comment, comment + mark, 1)),
                  (None, "line 5 carries a carriage return, U+0085, U+2028 or U+2029"))
        # A character outside YAML's printable set: a YAML reader refuses the whole file, but a reader without
        # the screen reads the step and passes each, the character sitting in a plain scalar (another step's
        # name:), a comment, a quoted scalar, the step's run: text and a plain value.
        for name, text in (
                ("form-feed-in-plain-scalar",
                 workflow.replace("- name: Leak denylist", "- name: Leak\x0cdenylist")),
                ("c0-control-in-comment", workflow.replace(comment, comment + "\x01", 1)),
                ("del-in-quoted-scalar", in_job("    note: 'a\x7fb'\n")),
                ("c1-control-in-run-text", workflow.replace("set -euo pipefail\n", "set -euo pipefail\x9b\n")),
                ("noncharacter-in-plain-value", in_job("    note: a\ufffeb\n"))):
            check(name, evaluate(text, pinned_path)[0], 2)
        check("nonprintable-names-line", read_pin(in_job("    note: a\x0cb\n")),
              (None, "line 4 carries U+000C, a character outside YAML's printable set"))
        # Each end of each range of that set, in a comment: just inside it the step is read, just outside it
        # the line is refused (CR, U+0085, U+2028 and U+2029, inside it, are refused above as line breaks, and
        # LF ends the line).
        for point, inside in ((0x08, False), (0x09, True), (0x0B, False), (0x0C, False), (0x0E, False),
                              (0x1F, False), (0x20, True), (0x7E, True), (0x7F, False), (0x80, False),
                              (0x84, False), (0x86, False), (0x9F, False), (0xA0, True), (0xD7FF, True),
                              (0xD800, False), (0xDFFF, False), (0xE000, True), (0xFFFD, True), (0xFFFE, False),
                              (0xFFFF, False), (0x10000, True), (0x10FFFF, True)):
            check("printable-set-end-U+{:04X}".format(point),
                  read_pin(workflow.replace(comment, comment + chr(point), 1)),
                  ("8.30.1", None) if inside else (
                      None, "line 5 carries U+{:04X}, a character outside YAML's printable set".format(point)))
        # A second step named Secrets (gitleaks) with no env: after the real one (a reader that takes the first
        # such step passes it), and the step with its env: but no run:.
        check("second-step-without-env",
              read_pin(workflow + "      - name: Secrets (gitleaks)\n        run: echo\n"),
              (None, "2 steps are named Secrets (gitleaks), not one"))
        check("step-without-run",
              read_pin(workflow.replace("        run: |\n          set -euo pipefail\n" + curl_line, "")),
              (None, "the Secrets (gitleaks) step has 1 env: and 0 run: keys, not one each"))
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
