#!/usr/bin/env python3
"""Declaration gate for tools/ci_checks.toml, the registry of every run: step in .github/workflows/quality.yml.

  check_ci_selection.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_ci_selection.py --self-test  assert every rule over in-memory fixtures, then the live tree
  check_ci_selection.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

The registry declares, for each run: step (named by its job id and its exact step name), the step's
categories, its scope, the trigger globs whose change would select it, whether a person has reviewed the
declaration, and an optional weekly-only flag with the reference to the maintainer authorization that
allows it. This gate only checks the declarations; nothing selects or skips a step from them yet.

A finding (exit 1), each printed with its rule id:
  missing-entry          a run: step in quality.yml has no entry
  unknown-step           an entry names no run: step in quality.yml
  duplicate-entry        two entries name one step
  bad-field              a field is missing, of the wrong type, unknown, or outside its enum
  empty-triggers         triggers are empty for a scope that needs them (all but SCOPES_WITHOUT_TRIGGERS)
  dead-glob              a trigger glob matches no tracked file
  import-not-triggered   a script the step runs, or a repository module in its static import closure,
                         matches none of the entry's triggers
  weekly-only-unauthorized  weekly-only = true with no weekly-only-authorization reference
  undetermined-over-ceiling more entries have scope "undetermined" than UNDETERMINED_CEILING
  input-not-triggered    for a reviewed = true entry only: a path or glob in the step's read-and-launch
                         inventory (tools/ci_inputs.py), or a repository script the inventory sees it
                         launch, matches none of the entry's triggers; a glob datum D/** is covered
                         only by the same glob, by **, or by a containing E/**
  unresolved-input       for a reviewed = true entry only: an unresolved read or launch site in the
                         step's inventory has no current disposition in tools/ci_inputs_dispositions.toml

Every entry is reviewed = false today, so the two inventory rules hold no entry yet; the gate still
validates tools/ci_inputs_dispositions.toml on every run and reports the reviewed-entry count.

Globs match tracked paths segment by segment: `**` spans any number of segments, `*` and `?` stay inside
one segment. The import closure is static: from each repository .py path the step's run: text names, every
`import X` or `from X import ...` (absolute, first name component) that resolves to a tracked X.py in the
importing file's own directory (sys.path[0] under `python3 -I`), followed transitively. A module loaded
through importlib, a sys.path entry added at run time, or a script launched as a subprocess is outside it.

Cannot evaluate (exit 2), never a pass: a dispositions file that is missing, unreadable, oversized,
not TOML or outside its schema (tools/ci_inputs.py reads it under the same bounds as the registry);
a registry or workflow that is missing, not a regular file,
unreadable, over MAX_BYTES or not UTF-8; a registry that is not TOML (TOMLDecodeError or a bare
ValueError), nests too deeply (RecursionError) or exhausts memory (MemoryError); a format-version other
than the integer 1; any workflow character, line or value outside the accepted grammar below, a run:
step without a name, or one job holding two run: steps of one name; an anchor, alias or tag at a value
start or flow-collection entry on a line outside a block scalar; a '#' inside an open flow collection
and not preceded by a space on such a line (YAML starts a comment at a '#' right after '[', '{' or ',',
so in `runs-on: [#]` the `#]` is a comment and the collection stays open, where this reader would count
the line closed; outside any flow collection a spaceless '#', as in `args: a,#b` or `run: echo x[#]`,
stays text for YAML and for this reader alike); a value starting with | or > that is not a block
scalar header followed by nothing or a comment; a blank line before a run: block's first
content line indented deeper than that content line; a relative .py path in run: text that names no
tracked file; a tracked-file listing that fails or is empty; a source in an import closure that cannot
be read or parsed.

The workflow is read by a line reader, not a YAML parser, so the gate stays standard-library only. The
reader is a positive grammar: it accepts exactly the shapes named here, and everything else is refused
(exit 2), never skipped or guessed at. Accepted, in full:
  characters   printable ASCII (U+0020 to U+007E) and the line feed, nothing else; the first CR, NEL,
               U+2028, U+2029, form feed, vertical tab, tab, NUL, other C0 or C1 control, BOM or other
               non-ASCII character is refused naming its line and character, before any line is read
  lines        blank lines; full-line comments; top-level `key:` or `key: value` lines with one
               `jobs:` block; job ids at indent 2 matching [A-Za-z_][A-Za-z0-9_-]*, each given once;
               job keys at indent 4 (values other than a bare `steps:` are carried, not read); opaque
               lines deeper than 4 under a job key other than steps:; steps under `steps:` starting
               `- ` at indent 6 with step keys at indent 8, each key given once per step; nested
               lines deeper than 8 under a step key other than name or run
  open values  on every line read or skipped (top-level keys, lines before jobs:, job ids, job keys,
               step keys and the opaque lines under with:, env: or any other key), a quoted scalar
               (' or "), flow sequence ([) or flow mapping ({) opened where a value or key starts,
               or anywhere inside a flow collection, closes on that same line (\\" and '' close
               nothing); YAML would continue an open one onto the following lines, which this reader
               would take as keys of their own, so it is refused. An anchor (&), alias (*) or tag (!)
               at a value start or at the start of a flow-collection entry on such a line is refused
               outright rather than modelled: YAML ends an anchor or alias name at whitespace or a flow
               indicator (, [ ] { }), so a skip to the next space would run past a flow indicator and
               miss a scalar opened after it. No workflow uses one; the closed value `[&a,"x"]` is
               refused by this same rule, which is acceptable because it carries an anchor. A '#'
               inside an open flow collection and not preceded by a space is refused the same way:
               directly after '[', '{' or ',' YAML starts a comment there (so `[#]` stays open)
               where this reader would read text, and no workflow carries one; outside any flow
               collection a spaceless '#' stays text for both readers. The content lines of a
               block scalar (a value of | or > with its indicators, under any key) are text, never
               flow syntax, so none of these rules runs on them; every line deeper than the key that
               opened the block is skipped unread
  scalars     a one-line name or run value that is single-quoted ('' doubling, no other quote),
               double-quoted (no escapes, no inner quote), or plain starting with a letter,
               underscore, dot or slash and not a value YAML types (null, ~, true/false/yes/no/on/off
               in any case, an integer, float, hex, octal, binary or sexagesimal form with or without
               underscores, a date or timestamp form) and holding no mapping indicator; a run: block
               under |, |-, > or >- whose content is every line at or beyond the indent of its first
               content line, ending at the first line at or under the key's indent or at a dedented
               comment line (a comment, never run text; a dedented non-comment line is refused); a
               blank line before the first content line indented deeper than that content line is
               refused, because YAML rejects it while a line reader would take it for an empty line
  keys         a job id, job key or step key that YAML would type (on, off, yes, no, true, false,
               null in any case) is refused; `jobs:` twice, a job id twice, or a step key twice in
               one step is refused because YAML would keep only the last
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_ci_selection.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import os
import posixpath
import re
import stat
import subprocess
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_ci_selection.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "tools/ci_checks.toml"
DISPOSITIONS = "tools/ci_inputs_dispositions.toml"
WORKFLOW = ".github/workflows/quality.yml"
MAX_BYTES = 4 * 1024 * 1024

CATEGORIES = ("ci", "secrets", "site", "opf", "opf-closure", "corpus", "generated", "release", "records",
              "hooks", "git-env", "python-host", "platform", "probe")
SCOPES = ("per-file", "sub-tree", "whole-repo", "history", "self-test", "probe", "setup", "aggregator",
          "undetermined")
# A setup step and the aggregator check no repository input, and an undetermined step runs on every
# change until its declaration is written by hand, so these three may carry no triggers.
SCOPES_WITHOUT_TRIGGERS = ("setup", "aggregator", "undetermined")
# The most entries that may stay undetermined; raising it is a reviewed edit to this gate.
UNDETERMINED_CEILING = 1

REQUIRED_FIELDS = {"job": str, "step": str, "categories": list, "scope": str, "triggers": list, "reviewed": bool}
OPTIONAL_FIELDS = {"weekly-only": bool, "weekly-only-authorization": str}

JOB_RE = re.compile(r"  ([A-Za-z_][A-Za-z0-9_-]*):")
KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: +(.*))?")
PY_PATH_RE = re.compile(r"(?<![A-Za-z0-9_./-])[A-Za-z0-9_.][A-Za-z0-9_./-]*\.py(?![A-Za-z0-9_./-])")
BLOCK_MARKERS = ("|", "|-", ">", ">-")
# A block scalar header: | or > with an optional chomping indicator (+ or -) and an optional
# indentation indicator (1-9), in either order. YAML reads no plain scalar starting with | or >, so a
# value starting there is a block scalar header or a YAML parse error, never text.
BLOCK_HEADER_RE = re.compile(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?")
# Every character the accepted grammar needs: printable ASCII and the line feed. Anything else (CR,
# NEL, U+2028, U+2029, form feed, vertical tab, tab, NUL, any other C0 or C1 control, a BOM, all other
# non-ASCII) can split, hide or pad a line or a scalar where YAML and a line reader disagree, so the
# first such character is refused before any line is read.
ACCEPTED_CHARS = frozenset(map(chr, range(0x20, 0x7F)))
# Plain values YAML 1.1 reads as something other than the literal text: null and the booleans (the
# yes/no/on/off words included, in any case), integer, float, hex, octal, binary and sexagesimal
# number forms (underscores included), and date or timestamp forms. A plain scalar, job id, job key or
# step key matching one is refused; quoting a scalar makes it text.
TYPED_PLAIN_RE = re.compile(
    r"(?i:~|null|true|false|yes|no|on|off)"
    r"|[-+]?[0-9][0-9_]*(?::[0-9][0-9_]*)*"
    r"|[-+]?(?:[0-9][0-9_]*)?\.[0-9_]*(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+"
    r"|[-+]?\.(?i:inf)|\.(?i:nan)"
    r"|[-+]?0(?i:x)[0-9a-fA-F_]+|[-+]?0(?i:b)[01_]+"
    r"|[0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:[Tt ][ -~]*)?")
# The plain one-line scalars the gate accepts, positively: a letter, underscore, dot or slash first
# (never a digit, sign or YAML indicator, so no number, date, anchor, alias, tag, flow or block form
# can begin one), printable ASCII after. TYPED_PLAIN_RE still refuses the word and dotted forms
# (null, yes, .inf) whose first character this pattern allows.
PLAIN_SAFE_RE = re.compile(r"[A-Za-z_./][ -~]*")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, oversized, not UTF-8, not TOML, or not in a shape read."""


def read_text(path, where):
    """The UTF-8 text of the regular file at path, at most MAX_BYTES; CannotEvaluate otherwise."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(where, exc)) from None
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(where))
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(where, exc)) from None
    if len(data) > MAX_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_BYTES))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None


def load_registry(text, where):
    """The parsed registry table; every parse failure is CannotEvaluate, never an empty registry."""
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError on a deeply nested
    # array or inline table; neither is a TOMLDecodeError.
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: is not valid TOML: {}: {}".format(where, type(exc).__name__, exc)) from None
    # type() first: True == 1 and 1.0 == 1 in Python, and neither is the integer 1 TOML must carry.
    if type(data.get("format-version")) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version is not 1".format(where))
    unknown = sorted(set(data) - {"format-version", "check"})
    if unknown:
        raise CannotEvaluate("{}: unknown top-level keys {}".format(where, unknown))
    checks = data.get("check", [])
    if not isinstance(checks, list) or not all(isinstance(entry, dict) for entry in checks):
        raise CannotEvaluate("{}: check is not an array of tables".format(where))
    return checks


def _refuse_characters(text, where):
    """Refuse the first character outside the accepted set, naming its line and character. The grammar
    needs only printable ASCII and the line feed; every other character (CR, NEL, U+2028, U+2029, form
    feed, vertical tab, tab, NUL, any other C0 or C1 control, a BOM, all other non-ASCII) can make YAML
    read other lines or other text than a line reader."""
    line, column = 1, 0
    for char in text:
        if char == "\n":
            line += 1
            column = 0
            continue
        column += 1
        if char not in ACCEPTED_CHARS:
            raise CannotEvaluate(
                "{}:{}: character U+{:04X} at column {} is outside the accepted grammar "
                "(printable ASCII and the line feed)".format(where, line, ord(char), column))


def _scalar(value, where):
    """A one-line YAML scalar the gate accepts, positively: single-quoted ('' doubling, no other inner
    quote), double-quoted (no escapes, no inner quote), or plain from PLAIN_SAFE_RE (cut at a ' #'
    comment) that is no TYPED_PLAIN_RE value and holds no mapping indicator. Everything else is
    refused: YAML reads it as something other than the literal text on the line."""
    value = value.strip()
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'") or "'" in value[1:-1].replace("''", ""):
            raise CannotEvaluate("{}: single-quoted scalar not read by this gate".format(where))
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"') or "\\" in value or '"' in value[1:-1]:
            raise CannotEvaluate("{}: double-quoted scalar not read by this gate".format(where))
        return value[1:-1]
    value = value.split(" #", 1)[0].rstrip()
    if not PLAIN_SAFE_RE.fullmatch(value) or ": " in value or value.endswith(":"):
        raise CannotEvaluate("{}: plain scalar {!r} is outside the accepted grammar".format(where, value))
    if TYPED_PLAIN_RE.fullmatch(value):
        raise CannotEvaluate("{}: plain scalar {!r} is a value YAML types, not text; quote it to make "
                             "it text".format(where, value))
    return value


def _quoted_end(text, start):
    """The index just past the quote closing the YAML quoted scalar opened at text[start], or None when
    it does not close on this line. A double-quoted scalar skips the character after each backslash
    (so \\" closes nothing); a single-quoted one reads '' as one quote."""
    quote, index = text[start], start + 1
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\":
            index += 2
            continue
        if char == quote:
            if quote == "'" and text[index + 1:index + 2] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return None


def _block_scalar_indent(stripped, indent, where):
    """The indent of the key that opens a block scalar on this line (`key: |`, with any leading `- `
    item dashes, | or > with their indicators), or None. The content lines of a block scalar are text,
    never flow syntax: YAML reads no comment, key, flow collection, anchor, alias or tag inside them,
    so the reader skips every following line indented deeper than the returned indent, which is where
    YAML ends the scalar too (its content must be indented past the key). A value that starts with |
    or > but is not a well-formed header followed by nothing or a comment is refused: YAML reads no
    plain scalar starting with a block indicator, so such a line is a parse error, never text.
    Detection stays conservative, so it can never skip a line YAML reads as structure: a header shape
    it does not model (a bare `- |` item, a quoted or non-identifier key) opens no skip here, and that
    block's content lines stay scanned line by line and refused where the scanned rules refuse them.
    That is a fail-closed over-refusal: YAML reads those content lines as text, and this gate may
    refuse the workflow (exit 2) rather than skip a line it cannot prove is text; it never passes a
    misread one. A header whose comment lacks the space before the '#', as in `script: |#c`, is
    refused as outside the header grammar the same way: PyYAML's two loaders read that form
    differently (a header with a comment, or a parse error), so the gate refuses both readings
    rather than model either, another fail-closed over-refusal. A `- key: |` item's returned indent
    includes the `- ` offset: the item's later keys sit at that deeper indent, and a skip measured
    from the dash's own indent would swallow them (a sibling name:/run: pair, so a whole step) as
    block content."""
    offset = 0
    body = stripped
    while body.startswith("- "):
        trimmed = body[2:].lstrip(" ")
        offset += len(body) - len(trimmed)
        body = trimmed
    match = KEY_RE.fullmatch(body)
    if match is None or not match.group(2) or not match.group(2).startswith(("|", ">")):
        return None
    value = match.group(2)
    rest = value[BLOCK_HEADER_RE.match(value).end():]
    if rest and not (rest.startswith(" ")
                     and (not rest.strip(" ") or rest.lstrip(" ").startswith("#"))):
        raise CannotEvaluate("{}: a value starting with {!r} is a block scalar header or a YAML parse "
                             "error, and this one is outside the header grammar read by this "
                             "gate".format(where, value[0]))
    return indent + offset


def _value_left_open(content, where):
    """Whether one workflow line (indentation removed) opens a quoted scalar (' or "), a flow sequence
    ([) or a flow mapping ({) at a place a YAML value or key starts, or anywhere inside a flow
    collection, and does not close it on that line. YAML continues such a value onto the following
    lines whatever their indentation, so a line this reader takes as a key of its own (a job key, a
    job id, a top-level key) would be text inside the value, and what follows would be read under
    another key than YAML reads it. A ' #' outside quotes starts a comment, so a flow collection still
    open there is open. The reading is conservative: a quote anywhere inside a flow collection is read
    as opening a quoted scalar.

    A '#' with no space before it is refused (CannotEvaluate, naming where) anywhere inside an open
    flow collection. PyYAML starts a comment at a '#' that directly follows a flow indicator, so in
    `runs-on: [#]` the `#]` is a comment and the sequence stays open onto the following lines, while
    a reader that took the '#' for text would count the ']' and call the line closed; the same holds
    after ','. One rule covers the flow-indicator case too: every '[', '{' or ',' where YAML reads a
    flow indicator is one this reader has counted, so the collection is already open (depth > 0) at
    a '#' directly after it. Inside an open flow collection every other spaceless '#' (as in `{a:#}`)
    is refused too rather than modelled: where PyYAML does not read a comment there it rejects the
    document, so refusing is bounded and fail closed, like the anchor rule below. Outside any flow
    collection a spaceless '#' stays text for YAML and for this reader alike: in `args: a,#b` and
    `echo x[#]` the ',' and '[' are not at a value start and not inside a flow collection, so they
    are text, not flow indicators, for both readers, and no comment starts after them.

    An anchor (&), alias (*) or tag (!) found at a value start or at the start of a flow-collection
    entry is refused (CannotEvaluate, naming where). Modelling where YAML ends such a node property is
    error prone: the name ends at whitespace OR at a flow indicator (, [ ] { }), so scanning to the
    next space would run past a flow indicator that re-opens a collection and let a quoted scalar
    opened after it (for example `[&a,"x ]`) be read as closed when YAML continues it onto later lines.
    No workflow under .github/workflows uses an anchor, alias or tag, so refusing the whole line is a
    bounded fail-closed rule rather than a partial model. It also gives the otherwise-valid closed
    value `[&a,"x"]` a named refusal, which is acceptable: it carries an anchor."""
    depth, at_start, index = 0, True, 0
    while index < len(content):
        char = content[index]
        after = content[index + 1:index + 2]
        if char == "#":
            previous = content[index - 1] if index else ""
            if depth and previous != " ":
                raise CannotEvaluate(
                    "{}: a '#' inside an open flow collection and not preceded by a space is not "
                    "read by this gate; directly after '[', '{{' or ',' YAML starts a comment there "
                    "and the collection would stay open onto the following lines".format(where))
            if index == 0 or previous == " ":
                break
            at_start = False
            index += 1
            continue
        if char in "\"'" and (at_start or depth):
            end = _quoted_end(content, index)
            if end is None:
                return True
            index, at_start = end, False
            continue
        if char in "[{" and (at_start or depth):
            depth, at_start = depth + 1, True
        elif char in "]}" and depth:
            depth, at_start = depth - 1, False
        elif char == "," and depth:
            at_start = True
        elif char == " ":
            pass
        elif char in "&*!" and at_start:
            raise CannotEvaluate("{}: an anchor, alias or tag ({!r}) at a value start or flow-collection "
                                 "entry is not read by this gate".format(where, char))
        elif char in "-?" and at_start and after in ("", " "):
            pass
        elif char == ":" and (after in ("", " ") or (depth and after in ",[]{}")):
            at_start = True
        else:
            at_start = False
        index += 1
    return depth > 0


def workflow_run_steps(text, where=WORKFLOW):
    """The run: steps of a workflow as (job, step name, run text, line), in file order. The accepted
    grammar is stated in the module docstring; a character, line or value outside it is CannotEvaluate,
    never skipped or guessed at."""
    _refuse_characters(text, where)
    lines = text.split("\n")
    steps, current = [], None
    in_jobs, job, in_steps = False, None, False
    # seen_jobs refuses a second jobs: block and job_ids a repeated job id; job_keys refuses a repeated
    # job key (YAML keeps the last of each, which this reader would not); last_key is the step key a
    # deeper line would continue.
    seen_jobs, job_ids, job_keys, last_key = False, set(), None, None

    def flush():
        # The step's own bookkeeping keys start with a space, so no YAML key can collide with them.
        if current is not None and "run" in current:
            if not current.get("name"):
                raise CannotEvaluate("{}:{}: run: step without a name".format(where, current[" line"]))
            steps.append((current[" job"], current["name"], current["run"], current[" line"]))

    index = 0
    block_indent = None
    while index < len(lines):
        line = lines[index]
        number = index + 1
        index += 1
        stripped = line.strip()
        if block_indent is not None:
            # The content lines of a block scalar are text, never flow syntax, so every line deeper
            # than the key that opened the block is skipped unread (a blank line never ends a block);
            # the first non-blank line at or above that indent ends the block and is read normally.
            if not stripped or len(line) - len(line.lstrip(" ")) > block_indent:
                continue
            block_indent = None
        if not stripped or stripped.startswith("#"):
            continue
        # Every line read or skipped below, top-level keys, lines before jobs:, job ids, job keys, step
        # keys and the opaque lines under with:, env: or any other nested key alike: a value left open
        # here would make YAML read the following lines as part of it.
        if _value_left_open(stripped, "{}:{}".format(where, number)):
            raise CannotEvaluate("{}:{}: a quoted scalar or flow collection opened on this line does not "
                                 "close on it; YAML would continue it onto the following lines".format(where, number))
        indent = len(line) - len(line.lstrip(" "))
        block_indent = _block_scalar_indent(stripped, indent, "{}:{}".format(where, number))
        if indent == 0:
            flush()
            current, job, in_steps = None, None, False
            head = line.split(" #", 1)[0].rstrip()
            match = KEY_RE.fullmatch(head)
            if match is None or (match.group(1) == "jobs" and (head != "jobs:" or seen_jobs)):
                raise CannotEvaluate("{}:{}: top-level line not read by this gate".format(where, number))
            in_jobs = head == "jobs:"
            seen_jobs = seen_jobs or in_jobs
            continue
        if not in_jobs:
            continue
        if indent in (1, 3):
            raise CannotEvaluate("{}:{}: indentation not read by this gate".format(where, number))
        if indent == 2:
            flush()
            current, in_steps, job_keys = None, False, set()
            match = JOB_RE.fullmatch(line.split(" #", 1)[0].rstrip())
            if match is None or TYPED_PLAIN_RE.fullmatch(match.group(1)):
                raise CannotEvaluate("{}:{}: job id not in the accepted id grammar".format(where, number))
            job = match.group(1)
            if job in job_ids:
                raise CannotEvaluate("{}:{}: job id {} given twice (YAML would keep only the "
                                     "last)".format(where, number, job))
            job_ids.add(job)
            continue
        if job is None:
            raise CannotEvaluate("{}:{}: content under jobs: before any job".format(where, number))
        if not job_keys and indent != 4:
            raise CannotEvaluate("{}:{}: job {} body not at indent 4".format(where, number, job))
        if indent == 4:
            flush()
            current = None
            # A job key is a plain key, with at most a comment after a bare steps: (YAML reads
            # `steps: # note` as `steps:`); a flow list, a compact list at indent 4 or a quoted key is refused.
            match = KEY_RE.fullmatch(stripped.split(" #", 1)[0].rstrip())
            if match is None or TYPED_PLAIN_RE.fullmatch(match.group(1)) \
                    or (match.group(1) == "steps" and match.group(2) is not None):
                raise CannotEvaluate("{}:{}: job key not read by this gate".format(where, number))
            if match.group(1) in job_keys:
                raise CannotEvaluate("{}:{}: job {} gives {} twice".format(where, number, job, match.group(1)))
            job_keys.add(match.group(1))
            in_steps = match.group(1) == "steps"
            continue
        if not in_steps:
            continue
        if indent > 8:
            # Deeper lines belong to a nested key (with:, env:) and are skipped; under a one-line name or
            # run value they would continue that scalar, so the value read would be wrong.
            if current is None or last_key in ("name", "run"):
                raise CannotEvaluate("{}:{}: continued step {} not read by this gate".format(where, number, last_key))
            continue
        if indent == 6 and stripped.startswith("- "):
            flush()
            current = {" job": job, " line": number}
            body = stripped[2:]
        elif indent == 8 and current is not None:
            body = stripped
        else:
            raise CannotEvaluate("{}:{}: step layout not read by this gate".format(where, number))
        match = KEY_RE.fullmatch(body)
        if match is None or TYPED_PLAIN_RE.fullmatch(match.group(1)):
            raise CannotEvaluate("{}:{}: step key not read by this gate".format(where, number))
        key, value = match.group(1), match.group(2) or ""
        if key in current:
            raise CannotEvaluate("{}:{}: step key {} given twice".format(where, number, key))
        last_key = key
        if key == "name":
            current["name"] = _scalar(value, "{}:{}".format(where, number))
        elif key == "run":
            if value.strip() in BLOCK_MARKERS:
                # The run: block is read here, not skipped, so the generic block-scalar skip must not
                # also run past it.
                block_indent = None
                # The first content line sets the block's content indentation (YAML's auto-detection);
                # the block is every later line at or beyond it, blank lines included, and ends at the
                # first line at or under the key's indent or at a dedented comment line (a comment,
                # never run text). A dedented non-comment line is outside the grammar.
                block, content_indent, leading_blanks = [], None, []
                while index < len(lines):
                    raw = lines[index]
                    if not raw.strip():
                        # A blank line before the first content line records its own indentation. YAML
                        # detects a block scalar's indentation from the first non-empty line and rejects
                        # a leading blank line indented deeper than that; a reader that treated every
                        # blank line as empty would accept it and read a shorter, different block.
                        if content_indent is None and raw:
                            leading_blanks.append((index + 1, len(raw)))
                        block.append("")
                        index += 1
                        continue
                    line_indent = len(raw) - len(raw.lstrip(" "))
                    if line_indent <= 8:
                        break
                    if content_indent is None:
                        content_indent = line_indent
                        deeper = [number for number, blank in leading_blanks if blank > content_indent]
                        if deeper:
                            raise CannotEvaluate("{}:{}: a blank line before the first content line of a "
                                                 "run: block is indented deeper than that content line; "
                                                 "YAML rejects it".format(where, deeper[0]))
                    elif line_indent < content_indent:
                        if raw.lstrip(" ").startswith("#"):
                            index += 1
                            break
                        raise CannotEvaluate("{}:{}: line under a run: block is dedented below its "
                                             "content and is no comment".format(where, index + 1))
                    block.append(raw.strip())
                    index += 1
                current["run"] = "\n".join(block).strip()
            else:
                current["run"] = _scalar(value, "{}:{}".format(where, number))
        else:
            current[key] = value
    flush()
    if not steps:
        raise CannotEvaluate("{}: no run: steps found".format(where))
    seen = set()
    for step_job, name, _run, number in steps:
        if (step_job, name) in seen:
            raise CannotEvaluate("{}:{}: job {} holds two run: steps named {!r}".format(where, number, step_job, name))
        seen.add((step_job, name))
    return steps


def glob_match(glob, path):
    """Whether path fully matches a segment glob (`**` spans segments; `*` and `?` stay in one).
    The match tracks the set of reachable path segments and matches one segment without backtracking,
    so its time stays polynomial in the glob and path lengths; a backtracking regex is exponential on a
    glob with many wildcards."""
    names = path.split("/")
    parts = glob.split("/")
    reach = {0}
    for position, part in enumerate(parts):
        if part == "**":
            if position == len(parts) - 1:
                # A trailing ** takes every remaining segment, at least one.
                return any(start < len(names) for start in reach)
            # ** before another segment takes zero or more whole nonempty segments.
            nxt = set()
            for start in reach:
                end = start
                while True:
                    nxt.add(end)
                    if end >= len(names) or not names[end]:
                        break
                    end += 1
            reach = nxt
        else:
            reach = {start + 1 for start in reach if start < len(names) and _segment_match(part, names[start])}
        if not reach:
            return False
    return len(names) in reach


def _segment_match(part, name):
    """Whether one path segment matches one glob segment of `*` and `?`: the greedy match that returns
    to the last `*` on a mismatch, O(len(part) * len(name))."""
    p = n = 0
    star, mark = -1, 0
    while n < len(name):
        if p < len(part) and part[p] == "*":
            star, mark = p, n
            p += 1
        elif p < len(part) and (part[p] == "?" or part[p] == name[n]):
            p += 1
            n += 1
        elif star >= 0:
            p, mark = star + 1, mark + 1
            n = mark
        else:
            return False
    while p < len(part) and part[p] == "*":
        p += 1
    return p == len(part)


def glob_shape_error(glob):
    """Why a trigger string is not a repository-relative glob, or None."""
    if not glob or glob.startswith("/") or "\\" in glob:
        return "not a repository-relative glob"
    if any(segment in ("", ".", "..") for segment in glob.split("/")):
        return "has an empty, '.' or '..' segment"
    if any(ord(char) < 0x21 or ord(char) == 0x7F for char in glob):
        return "carries a space or control character"
    return None


def run_scripts(run, tracked, where):
    """The tracked .py paths a step's run text names, with `./` and `..` collapsed. A relative .py path
    that names no tracked file is CannotEvaluate: its import closure could not be followed."""
    found = set()
    for token in PY_PATH_RE.findall(run):
        path = posixpath.normpath(token)
        if path not in tracked:
            raise CannotEvaluate("{}: run text names {}, which is not a tracked file".format(where, token))
        found.add(path)
    return sorted(found)


def import_closure(primary, tracked, read_source, cache=None):
    """primary plus every tracked module its static imports reach in the importing file's own directory.
    cache maps a path to its direct tracked imports, so a module shared by many steps is parsed once."""
    cache = {} if cache is None else cache
    seen, pending = set(), [primary]
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if rel not in cache:
            cache[rel] = _direct_imports(rel, tracked, read_source)
        pending.extend(path for path in cache[rel] if path not in seen)
    return seen


def _direct_imports(rel, tracked, read_source):
    """The tracked modules rel imports directly from its own directory."""
    found = set()
    source = read_source(rel)
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({}: {})".format(rel, type(exc).__name__, exc)) from None
    base = rel.rpartition("/")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            candidate = (base + "/" if base else "") + name.split(".")[0] + ".py"
            if candidate in tracked:
                found.add(candidate)
    return found


def _entry_findings(index, entry):
    """(bad-field findings, usable) for one registry entry; usable entries take part in the other rules."""
    findings = []
    label = "check #{}".format(index + 1)
    if isinstance(entry.get("job"), str) and isinstance(entry.get("step"), str):
        label = "check #{} ({} / {})".format(index + 1, entry["job"], entry["step"])
    for key, kind in REQUIRED_FIELDS.items():
        if key not in entry:
            findings.append(("bad-field", "{}: missing field {}".format(label, key)))
        elif not isinstance(entry[key], kind):
            findings.append(("bad-field", "{}: field {} is not a {}".format(label, key, kind.__name__)))
    for key, kind in OPTIONAL_FIELDS.items():
        if key in entry and not isinstance(entry[key], kind):
            findings.append(("bad-field", "{}: field {} is not a {}".format(label, key, kind.__name__)))
    for key in sorted(set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)):
        findings.append(("bad-field", "{}: unknown field {}".format(label, key)))
    for key in ("job", "step"):
        if isinstance(entry.get(key), str) and not entry[key].strip():
            findings.append(("bad-field", "{}: field {} is empty".format(label, key)))
    if isinstance(entry.get("scope"), str) and entry["scope"] not in SCOPES:
        findings.append(("bad-field", "{}: scope {!r} is outside {}".format(label, entry["scope"], SCOPES)))
    categories = entry.get("categories")
    if isinstance(categories, list):
        if not categories:
            findings.append(("bad-field", "{}: categories is empty".format(label)))
        for category in categories:
            if not isinstance(category, str) or category not in CATEGORIES:
                findings.append(("bad-field", "{}: category {!r} is outside {}".format(label, category, CATEGORIES)))
        if len(set(map(repr, categories))) != len(categories):
            findings.append(("bad-field", "{}: categories repeat".format(label)))
    triggers = entry.get("triggers")
    if isinstance(triggers, list):
        for glob in triggers:
            reason = glob_shape_error(glob) if isinstance(glob, str) else "is not a string"
            if reason is not None:
                findings.append(("bad-field", "{}: trigger {!r} {}".format(label, glob, reason)))
        if len(set(map(repr, triggers))) != len(triggers):
            findings.append(("bad-field", "{}: triggers repeat".format(label)))
    authorization = entry.get("weekly-only-authorization")
    if isinstance(authorization, str) and not authorization.strip():
        findings.append(("bad-field", "{}: weekly-only-authorization is empty".format(label)))
    if "weekly-only-authorization" in entry and entry.get("weekly-only") is not True:
        findings.append(("bad-field", "{}: weekly-only-authorization without weekly-only = true".format(label)))
    return findings, label, not findings


def _ci_inputs():
    """The inventory module, imported from this file's own directory on first use (the import sits
    here, not at the top, because ci_inputs imports this module's closure helpers at its own top).
    This running module registers itself under its import name first, so a __main__ run hands
    ci_inputs the same module object and the same CannotEvaluate class, never a second instance."""
    sys.modules.setdefault("check_ci_selection", sys.modules[__name__])
    directory = str(Path(__file__).resolve().parent)
    if directory not in sys.path:
        sys.path.append(directory)
    import ci_inputs
    return ci_inputs


def evaluate(workflow_text, registry_text, tracked, read_source, dispositions_text=None):
    """Every (rule id, message) finding of the registry against the workflow and the tracked paths.
    dispositions_text, when given, is validated and feeds the two reviewed-entry inventory rules;
    None (the in-memory fixtures) reads as no dispositions."""
    steps = workflow_run_steps(workflow_text)
    checks = load_registry(registry_text, REGISTRY)
    inputs_module = _ci_inputs() if dispositions_text is not None else None
    dispositions = inputs_module.load_dispositions(dispositions_text) if inputs_module else []
    tracked = frozenset(tracked)
    if not tracked:
        raise CannotEvaluate("the tracked-file set is empty; an empty tree is never assumed")
    findings = []
    by_step = {}
    for index, entry in enumerate(checks):
        entry_findings, label, usable = _entry_findings(index, entry)
        findings.extend(entry_findings)
        if isinstance(entry.get("job"), str) and isinstance(entry.get("step"), str):
            by_step.setdefault((entry["job"], entry["step"]), []).append((index, entry, label, usable))
    runs = {(job, name): (run, number) for job, name, run, number in steps}
    for job, name, _run, number in steps:
        if (job, name) not in by_step:
            findings.append(("missing-entry", "{}:{}: run: step {} / {} has no entry".format(
                WORKFLOW, number, job, name)))
    for key, rows in sorted(by_step.items()):
        if key not in runs:
            for _index, _entry, label, _usable in rows:
                findings.append(("unknown-step", "{}: names no run: step in {}".format(label, WORKFLOW)))
        if len(rows) > 1:
            findings.append(("duplicate-entry", "{} / {}: named by {} entries ({})".format(
                key[0], key[1], len(rows), ", ".join("#{}".format(row[0] + 1) for row in rows))))
    undetermined = 0
    live, closures, imports = {}, {}, {}
    for key, rows in sorted(by_step.items()):
        for _index, entry, label, usable in rows:
            if not usable:
                continue
            triggers = entry["triggers"]
            scope = entry["scope"]
            if scope == "undetermined":
                undetermined += 1
            if not triggers and scope not in SCOPES_WITHOUT_TRIGGERS:
                findings.append(("empty-triggers", "{}: scope {} needs triggers".format(label, scope)))
            for glob in triggers:
                if glob not in live:
                    # A glob with no wildcard names one path, so set membership decides it.
                    live[glob] = (glob in tracked if "*" not in glob and "?" not in glob
                                  else any(glob_match(glob, path) for path in tracked))
                if not live[glob]:
                    findings.append(("dead-glob", "{}: trigger {!r} matches no tracked file".format(label, glob)))
            if entry.get("weekly-only") is True and not entry.get("weekly-only-authorization"):
                findings.append(("weekly-only-unauthorized",
                                 "{}: weekly-only = true has no weekly-only-authorization reference".format(label)))
            if key not in runs:
                continue
            primaries = run_scripts(runs[key][0], tracked, "{}:{}".format(WORKFLOW, runs[key][1]))
            closure = set()
            for primary in primaries:
                if primary not in closures:
                    closures[primary] = import_closure(primary, tracked, read_source, imports)
                closure |= closures[primary]
            for path in sorted(closure):
                if not any(glob_match(glob, path) for glob in triggers):
                    findings.append(("import-not-triggered",
                                     "{}: {} is in the check's import closure but matches no trigger".format(
                                         label, path)))
            if entry.get("reviewed") is True:
                if inputs_module is None:
                    inputs_module = _ci_inputs()
                inventory = inputs_module.step_inventory(
                    runs[key][0], tracked, read_source, dispositions,
                    "{}:{}".format(WORKFLOW, runs[key][1]), imports)
                for row in inventory["data"]:
                    if not any(inputs_module.trigger_covers(glob, row["path"]) for glob in triggers):
                        findings.append(("input-not-triggered",
                                         "{}: {} ({}) is in the step's read-and-launch inventory but "
                                         "matches no trigger".format(label, row["path"], row["site"])))
                for script in inventory["launches"]:
                    if not any(glob_match(glob, script) for glob in triggers):
                        findings.append(("input-not-triggered",
                                         "{}: launched script {} matches no trigger".format(label, script)))
                for site in inventory["unresolved"]:
                    findings.append(("unresolved-input",
                                     "{}: unresolved site {} ({}) has no current disposition".format(
                                         label, site["site"], site["reason"])))
    if undetermined > UNDETERMINED_CEILING:
        findings.append(("undetermined-over-ceiling", "{} entries are undetermined; the ceiling is {}".format(
            undetermined, UNDETERMINED_CEILING)))
    return findings


def tracked_files(root):
    """The tracked relpaths under root from `git ls-files -z --cached`, pinned like check_internal_names'
    read: the GIT_* environment is dropped, core.fsmonitor=false, protocol.allow=never and
    GIT_NO_LAZY_FETCH=1. No git, a nonzero exit, a timeout, an empty listing or an undecodable path is
    CannotEvaluate, never an empty tree."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        proc = subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                               "ls-files", "-z", "--cached"], capture_output=True, timeout=120, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("cannot list the tracked files ({})".format(exc)) from None
    if proc.returncode != 0:
        raise CannotEvaluate("git ls-files failed (exit {}): {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    try:
        rels = {record.decode("utf-8") for record in proc.stdout.split(b"\x00") if record}
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("git ls-files listed a path that is not UTF-8 ({})".format(exc)) from None
    if not rels:
        raise CannotEvaluate("git ls-files listed no tracked files; an empty tree is never assumed")
    return rels


def run_gate(root):
    root = Path(root)
    try:
        registry_text = read_text(root / REGISTRY, REGISTRY)
        findings = evaluate(read_text(root / WORKFLOW, WORKFLOW), registry_text,
                            tracked_files(root), lambda rel: read_text(root / rel, rel),
                            read_text(root / DISPOSITIONS, DISPOSITIONS))
        entries = load_registry(registry_text, REGISTRY)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    for rule, message in findings:
        print("FAIL [{}]: {}".format(rule, message))
    if findings:
        print("RESULT: FAIL: {} finding(s) in {}".format(len(findings), REGISTRY))
        return 1
    reviewed = sum(1 for entry in entries if entry.get("reviewed") is True)
    print("PASS: {} declares every run: step of {} exactly once, with valid fields, live triggers and "
          "covered import closures; {} of {} entries are reviewed = true and held to the two "
          "inventory rules".format(REGISTRY, WORKFLOW, reviewed, len(entries)))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "ci-selection-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


FIXTURE_WORKFLOW = (
    "name: Fixture\n"
    "on:\n"
    "  pull_request:\n"
    "jobs:\n"
    "  lint:\n"
    "    name: Lint\n"
    "    needs:\n"
    "      - setup\n"
    "    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "        with:\n"
    "          fetch-depth: 0\n"
    "      - name: Probe\n"
    "        run: python3 -I -B tools/probe.py\n"
    "      - name: 'Gate self-test'\n"
    "        shell: bash\n"
    "        run: python3 -I -B tools/gate.py --self-test\n"
    "  sum:\n"
    "    steps:\n"
    "      - name: Hostile setting\n"
    "        run: |\n"
    "          git config --global core.autocrlf true\n"
    "      - name: Aggregate\n"
    "        run: echo done\n")
FIXTURE_TRACKED = ("tools/probe.py", "tools/gate.py", "tools/_helper.py", "tools/_deep.py", "docs/a.md",
                   "docs/sub/b.md")
FIXTURE_SOURCES = {
    "tools/probe.py": "import sys\n",
    "tools/gate.py": "import os\nfrom _helper import thing\nimport json.decoder\n",
    "tools/_helper.py": "import _deep\nthing = 1\n",
    "tools/_deep.py": "x = 1\n",
}


def _entry(job, step, scope, triggers, categories=("ci",), extra="", reviewed=False):
    return ("[[check]]\njob = {!r}\nstep = {!r}\ncategories = [{}]\nscope = {!r}\ntriggers = [{}]\n"
            "reviewed = {}\n{}\n").format(
        job, step, ", ".join(repr(c) for c in categories), scope, ", ".join(repr(t) for t in triggers),
        "true" if reviewed else "false", extra).replace("'", '"')


FIXTURE_ENTRIES = {
    "probe": ("lint", "Probe", "probe", ["tools/probe.py"]),
    "gate": ("lint", "Gate self-test", "self-test", ["tools/gate.py", "tools/_helper.py", "tools/_deep.py"]),
    "setup": ("sum", "Hostile setting", "setup", []),
    "aggregate": ("sum", "Aggregate", "aggregator", []),
}


def _registry(changes=None, extra=""):
    """A fixture registry: FIXTURE_ENTRIES with changes applied (a key mapped to None drops that entry)."""
    entries = dict(FIXTURE_ENTRIES)
    entries.update(changes or {})
    body = "".join(_entry(*row) for row in entries.values() if row is not None)
    return "format-version = 1\n\n" + body + extra


def _rules(registry, workflow=FIXTURE_WORKFLOW, tracked=FIXTURE_TRACKED, sources=None, dispositions=None):
    """The sorted rule ids evaluate() reports, or ('cannot-evaluate', message)."""
    sources = FIXTURE_SOURCES if sources is None else sources

    def read_source(rel):
        if rel not in sources:
            raise CannotEvaluate("{}: unreadable (fixture)".format(rel))
        return sources[rel]

    try:
        return sorted(rule for rule, _message in evaluate(workflow, registry, tracked, read_source,
                                                          dispositions))
    except CannotEvaluate as exc:
        return ("cannot-evaluate", str(exc))


UNREGISTERED_PROBE = "      - name: Unregistered\n        run: python3 -I -B tools/probe.py\n"


def _open_value_workflow(site, opener, closer):
    """The fixture workflow with a value opened by opener on a line at site and closed by closer two lines
    later. YAML continues the value over the line between, which this reader would take as a key of its
    own, so YAML reads a run: step the reader would skip (job-key, step-key, with-line), files a step
    under another job than the reader does (swallow-job-id), or reads another on: block (top-level);
    without the open-value refusal the fixture registry passes every one."""
    if site == "job-key":
        # A job key swallowing a top-level key: the reader leaves jobs: and skips the steps: after it.
        return FIXTURE_WORKFLOW + "  extra:\n    runs-on: {}\nenv:\n  {}\n    steps:\n".format(
            opener, closer) + UNREGISTERED_PROBE
    if site == "step-key":
        # A step key other than name or run swallowing an indent-4 line the reader takes as a job key.
        return FIXTURE_WORKFLOW + "        working-directory: {}\n    env:\n      {}\n".format(
            opener, closer) + UNREGISTERED_PROBE
    if site == "with-line":
        return FIXTURE_WORKFLOW + "      - uses: actions/cache@v4\n        with:\n          key: {}\n    env:\n" \
            "      {}\n".format(opener, closer) + UNREGISTERED_PROBE
    if site == "uses-item":
        # A "- uses:" step item: the value opened on the item line continues onto later lines.
        return FIXTURE_WORKFLOW + "      - uses: {}\n        with:\n          k: v\n    env:\n      {}\n".format(
            opener, closer) + UNREGISTERED_PROBE
    if site == "swallow-job-id":
        # A job key swallowing the next job id: YAML files the step under extra, the reader under other.
        return FIXTURE_WORKFLOW + "  extra:\n    runs-on: {}\n  other:\n    env:\n      {}\n    steps:\n".format(
            opener, closer) + UNREGISTERED_PROBE
    if site == "top-level":
        # A top-level key before jobs: swallowing an on: block.
        return FIXTURE_WORKFLOW.replace("name: Fixture\n", "name: {}\non:\n  push:\n  {}\n".format(
            opener, closer), 1)
    raise ValueError(site)


INPUT_FIXTURE_WORKFLOW = (
    "name: Inputs\n"
    "on:\n"
    "  pull_request:\n"
    "jobs:\n"
    "  lint:\n"
    "    steps:\n"
    "      - name: Inventory\n"
    "        run: python3 -I -B tools/inv.py\n")
INPUT_FIXTURE_TRACKED = ("tools/inv.py", "tools/launched.py", "tools/_leaf.py", "docs/a.md", "VERSION")
INPUT_ANCHOR = 'from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n'
INPUT_READ = INPUT_ANCHOR + '(ROOT / "docs" / "a.md").read_text()\n'
INPUT_LISTING = "import os\n" + INPUT_ANCHOR + 'os.listdir(ROOT / "tools")\n'
INPUT_UNRESOLVED = 'def f(p):\n    open(p, "rb")\n'
INPUT_CWD = ('import subprocess\nimport sys\n'
             'subprocess.run([sys.executable, "tools/launched.py"], cwd=somewhere)\n')
INPUT_DIR_FD = 'import os\nos.open("payload", 0, dir_fd=7)\n'
INPUT_SETATTR = ("import os\n" + INPUT_ANCHOR
                 + 'setattr(os, "ROOT", 1)\nopen(ROOT / "VERSION", "rb")\n')
INPUT_DISPOSITION = ('format-version = 1\n\n[[disposition]]\nfile = "tools/inv.py"\nqualname = "f"\n'
                     'fingerprint = "{}"\nkind = "scratch"\nrationale = "fixture"\n')


def _input_rules(source, triggers, reviewed=True, dispositions=None, sources=None):
    """The rule ids for one reviewed-entry inventory fixture over the one-step workflow above."""
    all_sources = {"tools/inv.py": source}
    all_sources.update(sources or {})
    registry = "format-version = 1\n\n" + _entry("lint", "Inventory", "self-test", triggers,
                                                 reviewed=reviewed)
    return _rules(registry, INPUT_FIXTURE_WORKFLOW, INPUT_FIXTURE_TRACKED, all_sources, dispositions)


def _inventory_checks():
    """The read-and-launch inventory checks (tools/ci_inputs.py and the two reviewed-entry rules).
    They run under this gate's registered suite, and ci_inputs.py --self-test runs the same set."""
    inv = _ci_inputs()

    def sites(source):
        return sorted((row["class"], tuple(row["paths"]), tuple(row["scripts"]))
                      for row in inv.classify_module("tools/inv.py", source))

    got = sites(INPUT_READ)
    check("inputs/anchored-parents-resolves", got == [("2", ("docs/a.md",), ())], got)
    got = sites("import os\nHERE = os.path.dirname(os.path.abspath(__file__))\n"
                "R = os.path.dirname(HERE)\nopen(os.path.join(R, \"VERSION\"), \"rb\")\n")
    check("inputs/anchored-dirname-chain-resolves", got == [("2", ("VERSION",), ())], got)
    got = sites("from pathlib import Path\nR = Path(__file__).parent.parent\n"
                "(R / \"VERSION\").read_bytes()\n")
    check("inputs/anchored-parent-chain-resolves", got == [("2", ("VERSION",), ())], got)
    got = sites('open("VERSION", "rb")\n')
    check("inputs/literal-read-class-1", got == [("1", ("VERSION",), ())], got)
    got = sites(INPUT_CWD)
    check("inputs/literal-under-cwd-unresolved", got == [("3b", (), ())], got)
    got = sites("import subprocess\nimport sys\n" + INPUT_ANCHOR
                + 'subprocess.run([sys.executable, "tools/launched.py"], cwd=str(ROOT))\n')
    check("inputs/cwd-root-keeps-class-1", got == [("1", (), ("tools/launched.py",))], got)
    got = sites('import os\nos.chdir("/x")\nopen("VERSION", "rb")\n')
    check("inputs/literal-after-chdir-unresolved", got == [("3b", (), ())], got)
    got = sites(INPUT_DIR_FD)
    check("inputs/dir-fd-unresolved", got == [("3b", (), ())], got)
    got = sites('import tempfile\nd = tempfile.mkdtemp()\nopen(d + "/x", "rb")\n')
    check("inputs/scratch-mkdtemp", got == [("3a", (), ())], got)
    got = sites('import tempfile\nwith tempfile.TemporaryDirectory() as d:\n    open(d, "rb")\n')
    check("inputs/scratch-tempdir-with", got == [("3a", (), ())], got)
    got = sites(INPUT_UNRESOLVED)
    check("inputs/param-no-caller-unresolved", got == [("3b", (), ())], got)
    got = sites(INPUT_ANCHOR + 'def f(p):\n    open(p, "rb")\nf(ROOT / "VERSION")\n')
    check("inputs/param-root-callers-resolve", got == [("2", ("VERSION",), ())], got)
    got = sites(INPUT_ANCHOR + 'def f(p):\n    open(p, "rb")\nf(ROOT / "VERSION")\nh = f\n')
    check("inputs/function-value-unresolved", got == [("3b", (), ())], got)
    got = sites(INPUT_ANCHOR + 'def g(q):\n    global ROOT\n    ROOT = q\nopen(ROOT / "VERSION", "rb")\n')
    check("inputs/global-rebound-unresolved", got == [("3b", (), ())], got)
    got = sites(INPUT_SETATTR)
    check("inputs/setattr-rebound-unresolved", got == [("3b", (), ())], got)
    got = sites(INPUT_ANCHOR + 'for rel in ("VERSION", "docs/a.md"):\n    open(ROOT / rel, "rb")\n')
    check("inputs/loop-literal-unrolls", got == [("2", ("VERSION", "docs/a.md"), ())], got)
    got = sites(INPUT_ANCHOR + 'for rel in names:\n    open(ROOT / rel, "rb")\n')
    check("inputs/loop-opaque-unresolved", got == [("3b", (), ())], got)
    got = sites(INPUT_LISTING)
    check("inputs/listing-becomes-glob", got == [("2", ("tools/**",), ())], got)
    got = sites("import subprocess\n" + INPUT_ANCHOR + 'subprocess.run(["git", "-C", str(ROOT), "log"])\n')
    check("inputs/git-root-whole-repo", got == [("2", ("**",), ())], got)
    got = sites('import subprocess\nimport tempfile\nt = tempfile.mkdtemp()\n'
                'subprocess.run(["git", "-C", t, "init"])\n')
    check("inputs/git-scratch", got == [("3a", (), ())], got)
    got = sites("open(*parts)\n")
    check("inputs/unknown-shape-starred", got == [("3b", (), ())], got)
    got = sites("import subprocess\nsubprocess.run(**kw)\n")
    check("inputs/unknown-shape-kwargs", got == [("3b", (), ())], got)

    launcher = ("import subprocess\nimport sys\n" + INPUT_ANCHOR
                + 'subprocess.run([sys.executable, "-I", "-B", str(ROOT / "tools" / "launched.py")])\n')
    launched = ("import _leaf\nimport subprocess\nimport sys\n" + INPUT_ANCHOR
                + 'subprocess.run([sys.executable, str(ROOT / "tools" / "inv.py")])\n')
    fixture_sources = {"tools/inv.py": launcher, "tools/launched.py": launched, "tools/_leaf.py": "x = 1\n"}

    def read_fixture(rel):
        if rel not in fixture_sources:
            raise CannotEvaluate("{}: unreadable (fixture)".format(rel))
        return fixture_sources[rel]

    record = inv.step_inventory("python3 -I -B tools/inv.py", INPUT_FIXTURE_TRACKED, read_fixture,
                                [], "fixture")
    check("inputs/launch-followed-with-cycle",
          record["launches"] == ["tools/inv.py", "tools/launched.py"]
          and "tools/_leaf.py" in record["closure"] and record["unresolved"] == []
          and record["data"] == [], record)

    for name, text in (("inputs/dispositions-bad-toml", "not toml ["),
                       ("inputs/dispositions-deep-nesting", "x = " + "[" * 4000 + "]" * 4000),
                       ("inputs/dispositions-unknown-kind", INPUT_DISPOSITION.format("0" * 64).replace(
                           '"scratch"', '"mystery"'))):
        try:
            inv.load_dispositions(text)
            got = "loaded"
        except CannotEvaluate as exc:
            got = str(exc)
        check(name, got != "loaded", got)

    got = _input_rules(INPUT_READ, ["tools/inv.py"])
    check("refuse/input-not-triggered", got == ["input-not-triggered"], got)
    got = _input_rules(INPUT_READ, ["tools/inv.py"], reviewed=False)
    check("pass/input-unreviewed-ignored", got == [], got)
    got = _input_rules(INPUT_READ, ["tools/inv.py", "docs/a.md"])
    check("pass/input-covered", got == [], got)
    got = _input_rules(INPUT_LISTING, ["tools/inv.py", "tools/launched.py", "tools/_leaf.py"])
    check("refuse/input-glob-not-covered", got == ["input-not-triggered"], got)
    got = _input_rules(INPUT_LISTING, ["tools/**"])
    check("pass/input-glob-covered-by-double-star", got == [], got)
    got = _input_rules(INPUT_UNRESOLVED, ["tools/inv.py"])
    check("refuse/unresolved-input", got == ["unresolved-input"], got)
    fingerprint = inv.classify_module("tools/inv.py", INPUT_UNRESOLVED)[0]["fingerprint"]
    got = _input_rules(INPUT_UNRESOLVED, ["tools/inv.py"],
                       dispositions=INPUT_DISPOSITION.format(fingerprint))
    check("pass/unresolved-input-dispositioned", got == [], got)
    got = _input_rules(INPUT_UNRESOLVED, ["tools/inv.py"], dispositions=INPUT_DISPOSITION.format("0" * 64))
    check("refuse/unresolved-input-stale-fingerprint", got == ["unresolved-input"], got)
    got = _input_rules(INPUT_READ, ["tools/inv.py", "docs/a.md"], dispositions="not toml [")
    check("cannot-evaluate/dispositions-not-toml", tuple(got[:1]) == ("cannot-evaluate",), got)

    # Each flip disables one classifier or matching rule in process and shows the covering check
    # above turn red (the case then wrongly resolves); the original attribute is always restored.
    saved = inv.CWD_KWARGS
    try:
        inv.CWD_KWARGS = frozenset()
        got = sites(INPUT_CWD)
    finally:
        inv.CWD_KWARGS = saved
    check("flip/cwd-rule-detected", got == [("1", (), ("tools/launched.py",))], got)
    saved = inv.DIR_FD_KWARGS
    try:
        inv.DIR_FD_KWARGS = frozenset()
        got = sites(INPUT_DIR_FD)
    finally:
        inv.DIR_FD_KWARGS = saved
    check("flip/dir-fd-rule-detected", got == [("1", ("payload",), ())], got)
    saved = inv._rebound
    try:
        inv._rebound = lambda facts, name: False
        got = sites(INPUT_SETATTR)
    finally:
        inv._rebound = saved
    check("flip/rebind-rule-detected", got == [("2", ("VERSION",), ())], got)
    saved = inv.disposition_matches
    try:
        inv.disposition_matches = lambda row, rel, site: (row["file"] == rel
                                                          and row["qualname"] == site["qualname"])
        got = _input_rules(INPUT_UNRESOLVED, ["tools/inv.py"],
                           dispositions=INPUT_DISPOSITION.format("0" * 64))
    finally:
        inv.disposition_matches = saved
    check("flip/stale-fingerprint-rule-detected", got == [], got)


def _self_test():
    check("pass/fixture-clean", _rules(_registry()) == [], _rules(_registry()))
    _inventory_checks()

    steps = workflow_run_steps(FIXTURE_WORKFLOW)
    check("workflow/reads-run-steps-only", [(job, name) for job, name, _run, _line in steps] == [
        ("lint", "Probe"), ("lint", "Gate self-test"), ("sum", "Hostile setting"), ("sum", "Aggregate")], steps)
    check("workflow/block-run-text", steps[2][2] == "git config --global core.autocrlf true", steps[2])

    got = _rules(_registry({"probe": None}))
    check("refuse/missing-entry", got == ["missing-entry"], got)
    got = _rules(_registry(extra=_entry("lint", "Gone", "probe", ["tools/probe.py"])))
    check("refuse/unknown-step", got == ["unknown-step"], got)
    got = _rules(_registry(extra=_entry(*FIXTURE_ENTRIES["probe"])))
    check("refuse/duplicate-entry", got == ["duplicate-entry"], got)

    got = _rules(_registry({"probe": ("lint", "Probe", "everything", ["tools/probe.py"])}))
    check("refuse/scope-outside-enum", got == ["bad-field"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py"], ("ci", "misc"))}))
    check("refuse/category-outside-enum", got == ["bad-field"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py"], ())}))
    check("refuse/categories-empty", got == ["bad-field"], got)
    got = _rules(_registry().replace('scope = "setup"', 'scope = "setup"\nowner = "x"'))
    check("refuse/unknown-field", got == ["bad-field"], got)
    got = _rules(_registry().replace("reviewed = false", 'reviewed = "no"', 1))
    check("refuse/reviewed-not-boolean", got == ["bad-field"], got)
    got = _rules(_registry().replace("reviewed = false\n", "", 1))
    check("refuse/missing-field", got == ["bad-field"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "/tools/probe.py"])}))
    check("refuse/trigger-not-relative", got == ["bad-field"], got)

    got = _rules(_registry({"probe": ("lint", "Probe", "probe", [])}))
    check("refuse/empty-triggers", sorted(set(got)) == ["empty-triggers", "import-not-triggered"], got)
    got = _rules(_registry({"setup": ("sum", "Hostile setting", "undetermined", [])}))
    check("pass/empty-triggers-allowed-scope", got == [], got)

    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "tools/gone/**"])}))
    check("refuse/dead-glob", got == ["dead-glob"], got)
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "docs/*.md"])}))
    check("pass/live-glob", got == [], got)

    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/gate.py", "tools/_deep.py"])}))
    check("refuse/import-not-triggered", got == ["import-not-triggered"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/gate.py", "tools/_helper.py"])}))
    check("refuse/transitive-import-not-triggered", got == ["import-not-triggered"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/_helper.py", "tools/_deep.py"])}))
    check("refuse/run-script-not-triggered", got == ["import-not-triggered"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/*.py"])}))
    check("pass/import-covered-by-glob", got == [], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "whole-repo", ["**"])}))
    check("pass/import-covered-by-double-star", got == [], got)

    weekly = ("lint", "Probe", "probe", ["tools/probe.py"], ("ci",))
    got = _rules(_registry({"probe": weekly + ("weekly-only = true",)}))
    check("refuse/weekly-only-unauthorized", got == ["weekly-only-unauthorized"], got)
    got = _rules(_registry({"probe": weekly + ('weekly-only = true\nweekly-only-authorization = ""',)}))
    check("refuse/weekly-only-empty-authorization", sorted(set(got)) == ["bad-field"], got)
    got = _rules(_registry({"probe": weekly + ('weekly-only = true\nweekly-only-authorization = "record 7"',)}))
    check("pass/weekly-only-authorized", got == [], got)

    # The ceiling at its ends: one under, exactly at, and one over UNDETERMINED_CEILING.
    rows = [("lint", "Probe"), ("sum", "Hostile setting"), ("sum", "Aggregate")]
    keys = ["probe", "setup", "aggregate"]
    for name, count, refused in (("bound/undetermined-under", UNDETERMINED_CEILING - 1, False),
                                 ("bound/undetermined-at", UNDETERMINED_CEILING, False),
                                 ("bound/undetermined-over", UNDETERMINED_CEILING + 1, True)):
        changes = {key: row + ("undetermined", ["tools/probe.py"])
                   for key, row in zip(keys[:count], rows[:count])}
        got = _rules(_registry(changes))
        check(name, (got == ["undetermined-over-ceiling"]) if refused else (got == []), (count, got))

    check("glob/star-stays-in-segment", not glob_match("docs/*.md", "docs/sub/b.md")
          and glob_match("docs/*.md", "docs/a.md"), "docs/*.md")
    check("glob/double-star-spans", all(glob_match(g, "docs/sub/b.md") for g in (
        "**", "docs/**", "**/b.md", "docs/**/b.md")) and not glob_match("docs/**/b.md", "b.md"), "**")
    check("glob/question-one-char", glob_match("docs/?.md", "docs/a.md")
          and not glob_match("docs/?.md", "docs/ab.md"), "?")
    # Globs a backtracking regex takes exponential time on: many stars in one segment, stars split by a
    # repeated letter, and many ** segments, each against a long path it does not match.
    long_name = "tools/" + "a" * 60 + ".py"
    check("glob/many-wildcards-bounded", not glob_match("tools/" + "*" * 24 + "Z.py", long_name)
          and not glob_match("tools/" + "*a" * 24 + "Z.py", long_name)
          and not glob_match("**/" * 24 + "Z.py", "/".join("a" * 40) + "/b.py")
          and glob_match("tools/" + "*a" * 24 + "*.py", long_name), long_name)
    # The dead-glob walk runs this glob against long_name, a tracked path long enough that a
    # backtracking regex would not finish the mismatch, so the check holds glob_match itself to its
    # non-backtracking bound, not only the finding.
    got = _rules(_registry({"probe": ("lint", "Probe", "probe", ["tools/probe.py", "tools/" + "*a" * 24 + "Z.py"])}),
                 tracked=FIXTURE_TRACKED + (long_name,))
    check("refuse/many-star-dead-glob", got == ["dead-glob"], got)

    got = _rules(_registry() + "[[check\n")
    check("cannot-evaluate/toml-decode-error", got[0] == "cannot-evaluate" and "TOMLDecodeError" in got[1], got)
    got = _rules(_registry() + "big = " + "9" * 5000 + "\n")
    check("cannot-evaluate/toml-bare-valueerror", got[0] == "cannot-evaluate" and "ValueError" in got[1], got)
    real_loads = tomllib.loads
    for name, error in (("cannot-evaluate/toml-recursion", RecursionError),
                        ("cannot-evaluate/toml-memory", MemoryError)):
        def _raise(_text, error=error):
            raise error("raised by the self-test")
        tomllib.loads = _raise
        try:
            got = _rules(_registry())
        finally:
            tomllib.loads = real_loads
        check(name, got[0] == "cannot-evaluate" and error.__name__ in got[1], got)
    got = _rules(_registry().replace("format-version = 1", "format-version = 2"))
    check("cannot-evaluate/format-version", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), tracked=())
    check("cannot-evaluate/no-tracked-files", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), sources={k: v for k, v in FIXTURE_SOURCES.items() if k != "tools/_helper.py"})
    check("cannot-evaluate/closure-source-unreadable", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), sources=dict(FIXTURE_SOURCES, **{"tools/_deep.py": "def (\n"}))
    check("cannot-evaluate/closure-source-unparseable", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "      - name: Aggregate\n        run: echo done\n", "      - run: echo done\n"))
    check("cannot-evaluate/unnamed-run-step", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace("Hostile setting", "Aggregate"))
    check("cannot-evaluate/duplicate-step-name", got[0] == "cannot-evaluate", got)
    got = _rules(_registry(), workflow="jobs:\n  lint:\n    steps:\n      - uses: actions/checkout@v4\n")
    check("cannot-evaluate/no-run-steps", got[0] == "cannot-evaluate", got)

    # Workflow shapes YAML reads as a step, a name or a run text other than the one the line reader
    # would take; each is cannot-evaluate, never a pass on a misread step.
    extra = FIXTURE_WORKFLOW + "  extra:\n    runs-on: ubuntu-latest\n"
    unregistered = "      - name: Unregistered\n        run: echo unregistered\n"
    probe_step = "      - name: Probe\n        run: python3 -I -B tools/probe.py\n"
    for name, workflow in (
        ("cannot-evaluate/shape-compact-steps-list",
         extra + "    steps:\n    - name: Unregistered\n      run: echo unregistered\n"),
        ("cannot-evaluate/shape-flow-steps", extra + "    steps: [{name: Unregistered, run: echo unregistered}]\n"),
        ("cannot-evaluate/shape-job-body-indent-6",
         FIXTURE_WORKFLOW + "  extra:\n      runs-on: ubuntu-latest\n      steps:\n"
         "        - name: Unregistered\n          run: echo unregistered\n"),
        ("cannot-evaluate/shape-second-jobs-block", FIXTURE_WORKFLOW + "jobs: # again\n  extra:\n    steps:\n"
         + unregistered),
        ("cannot-evaluate/shape-repeated-job-key", FIXTURE_WORKFLOW + "    steps:\n" + unregistered),
        ("cannot-evaluate/shape-block-run-comment", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: | # note\n          python3 -I -B tools/gate.py\n")),
        ("cannot-evaluate/shape-block-run-keep", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: |+\n          python3 -I -B tools/gate.py\n")),
        ("cannot-evaluate/shape-block-run-indent-indicator", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: |2\n            python3 -I -B tools/gate.py\n")),
        ("cannot-evaluate/shape-plain-run-continued", FIXTURE_WORKFLOW.replace(
            "        run: python3 -I -B tools/probe.py\n", "        run: python3 -I -B\n          tools/gate.py\n")),
        ("cannot-evaluate/shape-run-untracked-script", FIXTURE_WORKFLOW.replace("tools/probe.py", "tools/absent.py")),
        ("cannot-evaluate/shape-name-continued", FIXTURE_WORKFLOW.replace(
            probe_step, "      - name: Probe\n          (renamed)\n        run: python3 -I -B tools/probe.py\n")),
        ("cannot-evaluate/shape-name-folded", FIXTURE_WORKFLOW.replace(
            "      - name: Probe\n", "      - name: >-\n          Probe\n")),
        ("cannot-evaluate/shape-name-anchor", FIXTURE_WORKFLOW.replace("      - name: Probe\n", "      - name: &n Probe\n")),
        ("cannot-evaluate/shape-name-quote-inside", FIXTURE_WORKFLOW.replace("'Gate self-test'", "'Gate' self-test'")),
    ):
        got = _rules(_registry(), workflow=workflow)
        check(name, tuple(got[:1]) == ("cannot-evaluate",), got)
    got = _rules(_registry(), workflow=extra + "    steps: # note\n" + unregistered)
    check("refuse/missing-entry-after-steps-comment", got == ["missing-entry"], got)
    got = _rules(_registry({"gate": ("lint", "Gate self-test", "self-test", ["tools/_helper.py", "tools/_deep.py"])}),
                 workflow=FIXTURE_WORKFLOW.replace("tools/gate.py --self-test", "./tools/gate.py --self-test"))
    check("refuse/dot-slash-script-not-triggered", got == ["import-not-triggered"], got)

    # A quoted scalar or flow collection left open across lines is cannot-evaluate on every line the
    # reader reads or skips; the same values closed on their own line still pass.
    # The escape forms (\" inside double quotes, '' inside single quotes) close nothing. The
    # swallow-job-id registry names other / Unregistered, the job the reader would file the step under.
    other_entry = _entry("other", "Unregistered", "probe", ["tools/probe.py"])
    for name, site, opener, closer in (
        ("cannot-evaluate/shape-open-double-quote-job-key", "job-key", '".', '"'),
        ("cannot-evaluate/shape-open-double-quote-step-key", "step-key", '".', '"'),
        ("cannot-evaluate/shape-open-double-quote-with-line", "with-line", '".', '"'),
        ("cannot-evaluate/shape-open-single-quote-job-key", "job-key", "'.", "'"),
        ("cannot-evaluate/shape-open-single-quote-step-key", "step-key", "'.", "'"),
        ("cannot-evaluate/shape-open-single-quote-with-line", "with-line", "'.", "'"),
        ("cannot-evaluate/shape-open-flow-sequence-job-key", "job-key", "[a,", "]"),
        ("cannot-evaluate/shape-open-flow-sequence-step-key", "step-key", "[a,", "]"),
        ("cannot-evaluate/shape-open-flow-sequence-with-line", "with-line", "[a,", "]"),
        ("cannot-evaluate/shape-open-flow-mapping-job-key", "job-key", "{a: 1,", "}"),
        ("cannot-evaluate/shape-open-flow-mapping-step-key", "step-key", "{a: 1,", "}"),
        ("cannot-evaluate/shape-open-flow-mapping-with-line", "with-line", "{a: 1,", "}"),
        ("cannot-evaluate/shape-open-double-quote-escaped-close", "step-key", '".\\"', '"'),
        ("cannot-evaluate/shape-open-single-quote-doubled-close", "step-key", "'.''", "'"),
        ("cannot-evaluate/shape-open-job-key-swallows-job-id", "swallow-job-id", '"ubuntu', '"'),
        ("cannot-evaluate/shape-open-top-level-key", "top-level", '"Fixture', '"'),
    ):
        got = _rules(_registry(extra=other_entry if site == "swallow-job-id" else ""),
                     workflow=_open_value_workflow(site, opener, closer))
        check(name, tuple(got[:1]) == ("cannot-evaluate",), got)
    closed = FIXTURE_WORKFLOW.replace("    name: Lint\n", "    name: Lint\n    runs-on: [ubuntu-latest, 'mac']\n"
                                      "    env: {A: \"1\", B: '2'} # note\n").replace(
        "          fetch-depth: 0\n", "          fetch-depth: 0\n          python-version: '3.14'\n"
        "          ref: \"it\\\"s\"\n          tags: [main]\n").replace(
        "        shell: bash\n", "        shell: 'bash'\n        if: \"x\" # note\n        env: {A: [1, 2]}\n")
    got = _rules(_registry(), workflow=closed)
    check("pass/open-value-closed-on-line", got == [], got)

    # An anchor (&), alias (*) or tag (!) at a value start or at the start of a flow-collection entry is
    # refused on every line outside a block scalar. Each of the four bypass forms (a flow sequence
    # opening a double- or single-quoted scalar, a nested flow sequence, and a flow mapping, each led by
    # an anchor so a skip-to-space would run past a flow indicator) at each of four sites (a job key, a
    # step key, a with: line and a "- uses:" item) is refused. With the fix reverted the fixture
    # registry passes every one; what PyYAML reads then differs by fixture: on the dq and sq forms at
    # every site, and the seq and map forms at the job-key, step-key and with: sites, it reads a run:
    # step the reader skipped, while the seq and map forms at the "- uses:" item it rejects as parse
    # errors, so there the reverted gate would print PASS on a workflow YAML cannot read at all.
    for name, site, opener, closer in (
            ("cannot-evaluate/anchor-dq-job-key", "job-key", '[&a,"x ]', 'x"]'),
            ("cannot-evaluate/anchor-dq-step-key", "step-key", '[&a,"x ]', 'x"]'),
            ("cannot-evaluate/anchor-dq-with-line", "with-line", '[&a,"x ]', 'x"]'),
            ("cannot-evaluate/anchor-dq-uses-item", "uses-item", '[&a,"x ]', 'x"]'),
            ("cannot-evaluate/anchor-sq-job-key", "job-key", "[&a,'x ]", "x']"),
            ("cannot-evaluate/anchor-sq-step-key", "step-key", "[&a,'x ]", "x']"),
            ("cannot-evaluate/anchor-sq-with-line", "with-line", "[&a,'x ]", "x']"),
            ("cannot-evaluate/anchor-sq-uses-item", "uses-item", "[&a,'x ]", "x']"),
            ("cannot-evaluate/anchor-seq-job-key", "job-key", "[&a,[ ],", "]"),
            ("cannot-evaluate/anchor-seq-step-key", "step-key", "[&a,[ ],", "]"),
            ("cannot-evaluate/anchor-seq-with-line", "with-line", "[&a,[ ],", "]"),
            ("cannot-evaluate/anchor-seq-uses-item", "uses-item", "[&a,[ ],", "]"),
            ("cannot-evaluate/anchor-map-job-key", "job-key", "{&a:[ ],", "}"),
            ("cannot-evaluate/anchor-map-step-key", "step-key", "{&a:[ ],", "}"),
            ("cannot-evaluate/anchor-map-with-line", "with-line", "{&a:[ ],", "}"),
            ("cannot-evaluate/anchor-map-uses-item", "uses-item", "{&a:[ ],", "}")):
        got = _rules(_registry(), workflow=_open_value_workflow(site, opener, closer))
        check(name, tuple(got[:1]) == ("cannot-evaluate",), (name, got))
    # An alias and a tag at a value start are refused too; the alias (*) was not caught by this gate
    # before the anchor-property refusal was added.
    for name, value in (("cannot-evaluate/alias-at-value-start", "*a"),
                        ("cannot-evaluate/tag-at-value-start", "!!str x")):
        got = _rules(_registry(), workflow=FIXTURE_WORKFLOW + "        working-directory: {}\n".format(value))
        check(name, tuple(got[:1]) == ("cannot-evaluate",), got)

    # A '#' directly after '[', '{' or ',' (no space between), and a '#' inside an open flow collection
    # not preceded by a space, are where PyYAML starts a comment this reader would read as text: in
    # `runs-on: [#]` the `#]` is a comment and the sequence stays open onto the following lines, while
    # a reader counting the ']' calls the line closed. Each is refused outright at each of the four
    # sites. With the fix reverted the fixture registry passes every one of these; PyYAML then reads a
    # run: step the reader skipped for '[#]' and '[x,#]' at the job-key, step-key and with: sites, and
    # rejects the "- uses:" forms and every '{a:#}' form as parse errors, so there the reverted gate
    # would print PASS on a workflow YAML cannot read at all.
    for name, site, opener, closer in (
            ("cannot-evaluate/flow-comment-after-open-job-key", "job-key", "[#]", "]"),
            ("cannot-evaluate/flow-comment-after-open-step-key", "step-key", "[#]", "]"),
            ("cannot-evaluate/flow-comment-after-open-with-line", "with-line", "[#]", "]"),
            ("cannot-evaluate/flow-comment-after-open-uses-item", "uses-item", "[#]", "]"),
            ("cannot-evaluate/flow-comment-after-comma-job-key", "job-key", "[x,#]", "]"),
            ("cannot-evaluate/flow-comment-after-comma-step-key", "step-key", "[x,#]", "]"),
            ("cannot-evaluate/flow-comment-after-comma-with-line", "with-line", "[x,#]", "]"),
            ("cannot-evaluate/flow-comment-after-comma-uses-item", "uses-item", "[x,#]", "]"),
            ("cannot-evaluate/flow-comment-in-mapping-job-key", "job-key", "{a:#}", "}"),
            ("cannot-evaluate/flow-comment-in-mapping-step-key", "step-key", "{a:#}", "}"),
            ("cannot-evaluate/flow-comment-in-mapping-with-line", "with-line", "{a:#}", "}"),
            ("cannot-evaluate/flow-comment-in-mapping-uses-item", "uses-item", "{a:#}", "}")):
        got = _rules(_registry(), workflow=_open_value_workflow(site, opener, closer))
        check(name, tuple(got[:1]) == ("cannot-evaluate",), (name, got))
    # A '#' after a space is still a comment: a closed flow value followed by ' # note' passes, and a
    # ' #' inside a still-open flow collection is still refused as left open, never read as text.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "    name: Lint\n", "    name: Lint\n    runs-on: [ubuntu-latest] # note\n"))
    check("pass/flow-comment-after-space", got == [], got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "    name: Lint\n", "    name: Lint\n    runs-on: [a #]\n"))
    check("cannot-evaluate/flow-comment-after-space-leaves-open", tuple(got[:1]) == ("cannot-evaluate",), got)
    # Outside any flow collection a spaceless '#' is text for YAML and for this reader alike: a '[',
    # '{' or ',' not at a value start and not inside a flow collection is text, not a flow indicator,
    # so no comment starts after it. Each plain form passes: under with:, and in a plain run: value
    # after ',' and after '['. PyYAML loads all three as the literal text.
    results = [
        _rules(_registry(), workflow=FIXTURE_WORKFLOW
               + "      - uses: actions/x@v1\n        with:\n          args: a,#b\n"),
        _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace("run: echo done", "run: echo a,#b")),
        _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace("run: echo done", "run: echo x[#]")),
    ]
    check("pass/spaceless-hash-outside-flow", all(got == [] for got in results), results)

    # The content lines of a block scalar (| or > with their indicators, under any key) are text, never
    # flow syntax: a script: | block under with: holding lines the anchor, alias, tag, flow-comment or
    # leading-quote rules refuse is accepted, while each of the same lines outside any block scalar is
    # still refused.
    script_lines = ("* item", "- *bold* text", "return { ok: !failed };",
                    "!process.env.CI && x()", "'tis fine")
    script_step = ("      - uses: actions/github-script@v7\n        with:\n          script: |\n"
                   + "".join("            {}\n".format(text) for text in script_lines))
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW + script_step)
    check("pass/block-scalar-content-skipped", got == [], got)
    results = []
    for text in script_lines:
        hostile = FIXTURE_WORKFLOW + ("      - uses: actions/github-script@v7\n        with:\n"
                                      "          {}\n".format(text))
        results.append((text, _rules(_registry(), workflow=hostile)))
    check("cannot-evaluate/block-scalar-lines-outside-block",
          all(tuple(got[:1]) == ("cannot-evaluate",) for _text, got in results), results)
    # The block ends at the first non-blank line at or above the opening key's indent: a refusable
    # line at that indent right after the block is still read and refused.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW + script_step + "          key: [#]\n")
    check("cannot-evaluate/block-scalar-ends-at-equal-indent", tuple(got[:1]) == ("cannot-evaluate",), got)
    # A value starting with | or > that is not a well-formed header (nothing or a comment after the
    # indicators) is a YAML parse error, never text, and is refused.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW
                 + "      - uses: actions/github-script@v7\n        with:\n          script: |x\n")
    check("cannot-evaluate/block-scalar-header-outside-grammar", tuple(got[:1]) == ("cannot-evaluate",), got)
    # Folded headers with indicators open a block too, and the skip also covers a block scalar at a
    # step key, a top-level key and a key nested under a job key other than steps:.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW
                 + "      - uses: actions/github-script@v7\n        with:\n          script: >-\n"
                 "            * folded\n")
    check("pass/block-scalar-folded-with-indicator", got == [], got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW
                 + "      - uses: actions/checkout@v4\n        if: |\n          !cancelled()\n")
    check("pass/block-scalar-step-key", got == [], got)
    got = _rules(_registry(), workflow="description: |\n  * item\n" + FIXTURE_WORKFLOW)
    check("pass/block-scalar-top-level", got == [], got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "    steps:\n", "    env:\n      NOTE: |\n        * item\n    steps:\n", 1))
    check("pass/block-scalar-under-job-key", got == [], got)
    # A `- key: |` step item: the block's skip indent includes the `- ` offset, so the item's own
    # later keys at the key's indent (here a name:/run: pair, a whole run: step YAML reads) end the
    # block and are read; the step then needs a registry entry. A skip measured from the dash's
    # indent would swallow those keys as block content and hide the step. The |1 header (an
    # indentation indicator) opens the same skip.
    results = []
    for header, content in (("|", "          x\n"), ("|1", "         x\n")):
        hidden_item = (FIXTURE_WORKFLOW + "      - env: {}\n".format(header) + content
                       + "        name: Hidden\n        run: echo hidden\n")
        results.append((header, _rules(_registry(), workflow=hidden_item)))
    check("refuse/step-item-block-scalar-missing-entry",
          all(got == ["missing-entry"] for _header, got in results), results)
    # The same `- key: |` item inside a with: list: the sibling key line at the item's key indent
    # ends the block and is still scanned, so a refusable value there is refused, never skipped.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW
                 + "      - uses: actions/x@v1\n        with:\n          args:\n"
                 "            - env: |\n                text\n              key: [#]\n")
    check("cannot-evaluate/with-list-item-block-scalar-sibling-scanned",
          tuple(got[:1]) == ("cannot-evaluate",), got)

    # A blank line before the first content line of a run: block, indented deeper than that content line,
    # is a YAML parse error; the gate refuses it. The boundary (content indent 10): a blank one space
    # deeper (11) is refused; a blank equal to (10) or shallower than (9) the content indent is kept.
    def _run_block_blank(blank_spaces, content_spaces=10):
        return FIXTURE_WORKFLOW.replace(
            "        run: |\n          git config --global core.autocrlf true\n",
            "        run: |\n" + " " * blank_spaces + "\n" + " " * content_spaces
            + "git config --global core.autocrlf true\n")
    got = _rules(_registry(), workflow=_run_block_blank(11))
    check("cannot-evaluate/run-block-leading-blank-deeper", tuple(got[:1]) == ("cannot-evaluate",), got)
    got = _rules(_registry(), workflow=_run_block_blank(10))
    check("pass/run-block-leading-blank-equal", got == [], got)
    got = _rules(_registry(), workflow=_run_block_blank(9))
    check("pass/run-block-leading-blank-shallower", got == [], got)

    # Characters outside the accepted grammar, refused before any line is read, each naming its line
    # and character. First the line breaks YAML honours where a line reader does not: each would hide
    # one line inside another.
    results = []
    for breaker in ("\r", "\u0085", "\u2028", "\u2029"):
        hidden = FIXTURE_WORKFLOW.replace(
            "          git config --global core.autocrlf true\n",
            "          git config --global core.autocrlf true" + breaker + "          echo hidden\n")
        results.append(_rules(_registry(), workflow=hidden))
    check("cannot-evaluate/char-hides-line",
          all(got[0] == "cannot-evaluate" and "U+" in got[1] for got in results), results)
    # Then the rest of the refused set: a BOM, a form feed in indentation or alone on a line, a
    # vertical tab, a tab, a trailing no-break space (YAML keeps it, a line reader would strip it),
    # a NUL and a C1 control.
    mutations = (
        "\ufeff" + FIXTURE_WORKFLOW,
        FIXTURE_WORKFLOW.replace("      - name: Probe\n", "   \f   - name: Probe\n"),
        FIXTURE_WORKFLOW.replace("      - name: Probe\n", "      \f\n      - name: Probe\n"),
        FIXTURE_WORKFLOW.replace("- name: Probe", "- name: Pro\vbe"),
        FIXTURE_WORKFLOW.replace("- name: Probe", "- name:\tProbe"),
        FIXTURE_WORKFLOW.replace("- name: Probe", "- name: Probe\u00a0"),
        FIXTURE_WORKFLOW.replace("- name: Probe", "- name: Pro\x00be"),
        FIXTURE_WORKFLOW.replace("- name: Probe", "- name: Pro\x9bbe"),
    )
    results = [_rules(_registry(), workflow=workflow) for workflow in mutations]
    check("cannot-evaluate/char-outside-grammar",
          all(got[0] == "cannot-evaluate" and "U+" in got[1] for got in results), results)

    # Plain scalars YAML types as something other than text are refused unless quoted; a quoted one
    # stays the literal step name.
    results = []
    for value in ("null", "~", "yes", "Yes", "TRUE", "off", "1_000", "1.50", "1e3", ".inf",
                  "2026-01-01", "2026-01-01 09:30:00", "12:30:45", "0x1F"):
        typed = FIXTURE_WORKFLOW.replace("      - name: Probe\n", "      - name: {}\n".format(value))
        results.append((value, _rules(_registry(), workflow=typed)))
    check("cannot-evaluate/typed-plain-scalar",
          all(got[0] == "cannot-evaluate" for _value, got in results), results)
    got = _rules(_registry({"probe": ("lint", "null", "probe", ["tools/probe.py"])}),
                 workflow=FIXTURE_WORKFLOW.replace("      - name: Probe\n", "      - name: 'null'\n"))
    check("pass/quoted-typed-scalar-is-text", got == [], got)

    # A job id YAML would type (or outside the id grammar) and a repeated job id are each refused:
    # YAML would keep another key, or only the last body.
    results = []
    for job_id in ("null", "on", "Off", "007"):
        extra_job = FIXTURE_WORKFLOW + "  {}:\n    steps:\n      - name: Extra\n        run: echo extra\n".format(job_id)
        results.append((job_id, _rules(_registry(), workflow=extra_job)))
    check("cannot-evaluate/typed-job-id",
          all(got[0] == "cannot-evaluate" for _job_id, got in results), results)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW
                 + "  lint:\n    steps:\n      - name: Extra\n        run: echo extra\n")
    check("cannot-evaluate/repeated-job-id", got[0] == "cannot-evaluate" and "twice" in got[1], got)

    # A comment line dedented below a run: block's content ends the block as a comment, never as run
    # text; a dedented non-comment line there is outside the grammar.
    dedented = FIXTURE_WORKFLOW.replace(
        "        run: |\n          git config --global core.autocrlf true\n",
        "        run: |\n            git config --global core.autocrlf true\n          # a note, not run text\n")
    steps = workflow_run_steps(dedented)
    check("workflow/block-comment-dedent-ends-run",
          steps[2][2] == "git config --global core.autocrlf true"
          and _rules(_registry(), workflow=dedented) == [], steps[2])
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "        run: |\n          git config --global core.autocrlf true\n",
        "        run: |\n            git config --global core.autocrlf true\n          dedented content\n"))
    check("cannot-evaluate/block-dedent-under-content", got[0] == "cannot-evaluate", got)

    # The run: block reader clears the generic block-scalar skip before it starts: when a dedented
    # comment ends the block, the next line can sit deeper than the run: key, and a stale skip from
    # the run: header would swallow that line where the reader refuses a continued run: step.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "        run: |\n          git config --global core.autocrlf true\n",
        "        run: |\n            git config --global core.autocrlf true\n"
        "          # a note, not run text\n          after: x\n"))
    check("cannot-evaluate/run-block-reset-after-dedented-comment", tuple(got[:1]) == ("cannot-evaluate",), got)

    # A run: text naming a path through `..` resolves against the tracked set: one collapsing to a
    # tracked path inside the tree passes; one leaving the tree names no tracked file.
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "python3 -I -B tools/probe.py", "python3 -I -B tools/../tools/probe.py"))
    check("path/run-dot-dot-collapses", got == [], got)
    got = _rules(_registry(), workflow=FIXTURE_WORKFLOW.replace(
        "python3 -I -B tools/probe.py", "python3 -I -B ../outside/probe.py"))
    check("cannot-evaluate/run-dot-dot-escapes", got[0] == "cannot-evaluate", got)

    for name, value in (("cannot-evaluate/format-version-true", "true"),
                        ("cannot-evaluate/format-version-float", "1.0")):
        got = _rules(_registry().replace("format-version = 1", "format-version = " + value))
        check(name, tuple(got[:1]) == ("cannot-evaluate",), got)
    authorization = 'weekly-only-authorization = "record 7"'
    got = [_rules(_registry({"probe": weekly + (flag + authorization,)})) for flag in ("", "weekly-only = false\n")]
    check("refuse/authorization-without-weekly-only", got == [["bad-field"], ["bad-field"]], got)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="ci-selection-selftest-") as tmp:
        missing, directory, wide = Path(tmp) / "absent", Path(tmp) / "dir", Path(tmp) / "wide"
        os.makedirs(directory)
        with open(wide, "wb") as handle:
            handle.write(b"\xff\n")
        results = []
        for path in (missing, directory, wide):
            try:
                read_text(path, path.name)
                results.append("read")
            except CannotEvaluate as exc:
                results.append(str(exc))
    check("cannot-evaluate/unreadable-input", len(results) == 3 and "cannot stat" in results[0]
          and "not a regular file" in results[1] and "not UTF-8" in results[2], results)

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    got = _main_exit([])
    check("live/real-tree-passes", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_ci_selection {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_ci_selection {} checks (a fixture registry passes; a missing, unknown or "
          "duplicate entry, a field outside its enum or schema, empty triggers where the scope needs them, a "
          "dead glob (held against a path long enough to need the non-backtracking match), an untriggered "
          "run script (also through ./) or direct or transitive import, an unauthorized weekly-only and an "
          "authorization without weekly-only each fail; the undetermined ceiling holds one under, at and "
          "over; globs with many wildcards finish; TOML decode, bare ValueError, RecursionError and "
          "MemoryError, a format-version of 2, true or 1.0, unreadable inputs, the {} workflow shapes "
          "tested (an unnamed step, a duplicate step name, no run steps, and compact, flow, off-indent, "
          "repeated or second-block steps, block run headers with a comment, keep or indent indicator, a "
          "continued run or name, an untracked script, a folded, anchored or badly quoted name, and a "
          "quoted, flow sequence or flow mapping value left open across lines at a job key, a step key, "
          "a line under with: or a top-level key; a closed one passes), each of four anchor-led bypass "
          "forms at a job key, step key, with: line and \"- uses:\" item, an alias and a tag at a value "
          "start, a '#' directly after '[' or ',' and a '#' inside an open flow mapping with no space "
          "before it at each of those four sites (a '#' after a space stays a comment, inside a "
          "still-open collection stays a left-open refusal, and outside any flow collection a "
          "spaceless '#' after ',' or '[' stays text, under with: and in a plain run: value), a "
          "value starting with | or > outside the "
          "block-scalar header grammar, and a blank line before a run: block's first content line "
          "indented one space deeper "
          "than it (a blank equal to or shallower than it passes), every "
          "character outside the accepted grammar (a CR, NEL, U+2028 or U+2029 that would hide a line, a "
          "BOM, form feed, vertical tab, tab, NUL, C1 control or no-break space), a plain scalar or job id "
          "YAML types, a repeated job id, a dedented non-comment line under a run: block and a run path "
          "through .. that leaves the tree are cannot-evaluate; a quoted typed scalar stays text; a "
          "dedented comment ends a run: block as a comment; a block scalar's content lines (under a "
          "with: script, a step key, a top-level key, a key under a job key, a `- key: |` step item "
          "and a `- key: |` item in a with: list) are skipped as text while the same lines outside "
          "one are refused, and the block ends at its key's own indent, dash offset included, so a "
          "step after a `- key: |` item header stays read and needs an entry, a with: list sibling "
          "line stays scanned, and after a run: block ended by a dedented comment the stale skip is "
          "cleared so a deeper line there is refused as a continued step; a .. "
          "path that collapses to a tracked file "
          "passes; the read-and-launch inventory checks hold (anchored, literal, scratch, parameter, "
          "loop, rebinding, cwd, dir_fd, git, listing, launch-following, unknown-shape and "
          "disposition vectors, the reviewed-entry rules input-not-triggered and unresolved-input, "
          "an invalid dispositions file as cannot-evaluate, and four in-process rule flips each "
          "turning a covering check red); an unrecognized argument exits 2; the live tree "
          "passes)".format(len(EXECUTED), 3 + sum(1 for name in EXECUTED if name.startswith("cannot-evaluate/shape-"))))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(ROOT)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate starts git for the tracked-file
# listing and reads its exit status (subprocess), so under an ignored SIGCHLD a failing listing would read
# as a success; the guard runs at the entry line, before main() dispatches.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_ci_selection.py") or main())
