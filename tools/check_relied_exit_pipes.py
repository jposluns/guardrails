#!/usr/bin/env python3
"""Relied-exit-pipes gate for adopter-declared commands whose exit a pipe discards. Offline, stdlib only,
fail-closed.

A script that pipes a RELIED-ON command (a producer whose own termination status is the evidence later
steps rest on) into another command discards that producer's exit: the pipeline reports the LAST stage's
status, so under `set -e` a failed producer still reads as success and the script keeps going (gatdis:
when a command's own termination status is the evidence a verdict rests on, that status is taken only
from a construct that faithfully propagates it). This gate flags an adopter-declared relied-on command
sitting in a NON-FINAL stage of a pipeline (`|` or `|&`), at top level or inside a `$( )` or backquote
command-substitution body, in an adopter-declared shell surface.

The gate is ADOPTER-PARAMETERIZED: it ships NO command, subcommand, or path of its own. The adopter
authors `.aiqt/relied-exit-pipes.toml`, declaring which script surfaces to scan and which command words
(optionally narrowed to subcommands) are relied on. With NO configuration the gate prints NOT APPLICABLE
and exits clean; a present but unusable configuration never does (fail-closed). A pipe whose left side
is not a declared command never fires, which keeps false alarms low: `git log | head` is not a finding.

TWO EXEMPTIONS clear a flagged pipeline:
  E1  pipefail is provably on: an earlier TOP-LEVEL `set` in the same file turns it on (`set -o
      pipefail`, or a cluster such as `set -euo pipefail`), with no `set +o pipefail` at any level in
      between. A set inside a function body, an `if` block, or a subshell does not count (it may never
      run, or runs in another scope).
  E2  the script reads the command's own exit: the pipeline is a top-level statement and the VERY NEXT
      simple command reads PIPESTATUS for that stage's index, or saves the whole PIPESTATUS array.
Any final stage counts as a sink, not only a truncating one: the exit is lost whatever the last stage is.

Configuration schema (`.aiqt/relied-exit-pipes.toml`), adopter-authored:

  format-version = 1                 the exact integer 1 (true and 1.0 do not pass)

  [[binding]]
  id = "row-writer"                  a stable, unique kebab-case identifier
  paths = ["path/to/file", "dir/**"]  repo-relative surfaces: an exact file, or a directory prefix
                                      ending in /**; each selector must match at least one readable
                                      regular file, with no symlinks and no escaping the repository
  commands = ["record-tool"]          relied-on command basenames (matched at command-word position,
                                      after NAME=value assignments and the common wrappers)
  subcommands = ["write"]             OPTIONAL: when present, only these first operands count as relied

  check_relied_exit_pipes.py               scan the surfaces the configuration declares
  check_relied_exit_pipes.py --self-test   deterministic self-test over synthetic trees

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE (no configuration present)
  1  a real finding (a declared relied-on command in a non-final pipeline stage, neither exemption held)
  2  cannot evaluate, fail-closed: a malformed or unreadable configuration, an unmatched path, command
     or subcommand selector, a symlinked or non-UTF-8 declared surface, a declared command inside shell
     the gate cannot parse (an unquoted heredoc, a here-string, a process substitution, an unterminated
     construct), or a declared command inside the raw text of an `sh -c`, `bash -c` or `eval` argument.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_relied_exit_pipes.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import collections
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_relied_exit_pipes.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

CONFIG_REL = ".aiqt/relied-exit-pipes.toml"
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")                       # a kebab-case binding identifier
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")     # a NAME=value assignment before a command
BINDING_REQUIRED = frozenset(("id", "paths", "commands"))
BINDING_OPTIONAL = frozenset(("subcommands",))
# Wrapper words skipped at command-word position: the real command follows them.
WRAPPERS = frozenset(("env", "nice", "timeout", "time", "command", "exec", "nohup", "stdbuf", "sudo"))
# Shell reserved words that keep the command-word position open (the real command follows).
RESERVED_SKIP = frozenset(("if", "then", "else", "elif", "do", "while", "until", "for", "case",
                           "{", "}", "!", "coproc"))
BLOCK_OPEN = frozenset(("if", "while", "until", "for", "case", "{"))
BLOCK_CLOSE = frozenset(("fi", "done", "esac", "}"))
PIPE_SEPS = ("|", "|&")
MAX_SUBST_DEPTH = 20

# A scanning context: text is the (possibly masked) shell text, base the absolute offset of its first
# character in the original file, is_top True only for the file's own top level (a substitution body is
# not top level: E2 never applies there and a `set -o pipefail` inside one is not a top-level enable),
# and truncated the local offset where extraction stopped on an unterminated construct (None when whole).
_Ctx = collections.namedtuple("_Ctx", ("text", "base", "is_top", "truncated"))


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable configuration or declared surface is never treated as clean."""


_AIQT_HOOKS = None


def _hooks():
    """The repository's shared shell lexer module (aiqt_hooks), imported lazily so the NOT APPLICABLE
    path needs no lexer, and fail-closed when it cannot be imported."""
    global _AIQT_HOOKS
    if _AIQT_HOOKS is None:
        hooks_dir = Path(__file__).resolve().parents[1] / ".aiqt" / "core" / "hooks" / "scripts"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        try:
            import aiqt_hooks
        except BaseException as exc:
            raise GateError("cannot import the shared shell lexer aiqt_hooks ({})".format(exc))
        _AIQT_HOOKS = aiqt_hooks
    return _AIQT_HOOKS


# --- substitution extraction -------------------------------------------------------------------------
# The shared lexer reads an unquoted `$( )` as a word boundary plus grouping and a backquote as an
# ordinary opaque character, so a pipeline INSIDE a substitution body would leak into, or hide from, the
# top-level reading. This extractor pulls each `$( )` and backquote body out as its own context and masks
# the span in the parent with a LENGTH- AND NEWLINE-PRESERVING quoted filler, so every offset and line
# number keeps indexing the original file. Single-quoted and ANSI-C-quoted text, comments, and heredoc
# bodies are never extracted from (their content is data, or is handled by the lexer's own refusal).

def _match_subst_close(text, i, n):
    """The index of the `)` closing the `$(` whose body starts at i, or -1 when unterminated. Honors
    nested parentheses, single and double quotes, backslash escapes, backquotes, and nested `$(`."""
    depth = 1
    word_break = True
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            word_break = False
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j == -1:
                return -1
            i = j + 1
            word_break = False
            continue
        if c == '"':
            i += 1
            while i < n:
                d = text[i]
                if d == "\\":
                    i += 2
                    continue
                if d == '"':
                    break
                if d == "`":
                    j = i + 1
                    while j < n and text[j] != "`":
                        j += 2 if text[j] == "\\" else 1
                    if j >= n:
                        return -1
                    i = j + 1
                    continue
                if d == "$" and i + 1 < n and text[i + 1] == "(":
                    j = _match_subst_close(text, i + 2, n)
                    if j == -1:
                        return -1
                    i = j + 1
                    continue
                i += 1
            if i >= n:
                return -1
            i += 1
            word_break = False
            continue
        if c == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                return -1
            i = j + 1
            word_break = False
            continue
        if c == "#" and word_break:
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        word_break = c in " \t\n;&|("
        i += 1
    return -1


def _extract(text):
    """One extraction level over one context's text. Returns (masked_text, subs, truncated): masked_text
    is text with every top-level-of-this-context `$( )` and backquote substitution replaced by a quoted
    filler of IDENTICAL length and newline positions; subs is a list of (body_text, local_offset) pairs,
    each to be scanned as its own context; truncated is the local offset where the walk stopped on an
    unterminated quote or substitution (None when the whole text was walked). Heredoc bodies (quoted or
    not) are skipped, never extracted from: a quoted body is literal data, and an unquoted heredoc makes
    the lexer refuse the opener, so the declared-word check over the unparsed tail still sees the
    ORIGINAL body text."""
    out = list(text)
    subs = []
    n = len(text)
    i = 0
    in_dq = False
    word_break = True
    pending = []  # queued (delimiter, strip_tabs) heredoc bodies to skip at the next newline

    def mask(a, b):
        out[a] = "'"
        for k in range(a + 1, b - 1):
            if text[k] != "\n":
                out[k] = "x"
        out[b - 1] = "'"

    while i < n:
        c = text[i]
        if c == "\n" and not in_dq and pending:
            i += 1
            for delim, strip in pending:
                while i < n:
                    nl = text.find("\n", i)
                    line_end = n if nl == -1 else nl
                    line = text[i:line_end]
                    if strip:
                        line = line.lstrip("\t")
                    i = line_end + 1 if nl != -1 else n
                    if line == delim:
                        break
            pending = []
            word_break = True
            continue
        if c == "\\":
            i += 2
            word_break = False
            continue
        if c == "'" and not in_dq:
            j = text.find("'", i + 1)
            if j == -1:
                return "".join(out), subs, i
            i = j + 1
            word_break = False
            continue
        if c == "$" and not in_dq and i + 1 < n and text[i + 1] == "'":
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                return "".join(out), subs, i
            i = j + 1
            word_break = False
            continue
        if c == '"':
            in_dq = not in_dq
            i += 1
            word_break = False
            continue
        if c == "`":
            j = i + 1
            body = []
            while j < n and text[j] != "`":
                if text[j] == "\\" and j + 1 < n and text[j + 1] in "`\\$":
                    body.append(text[j + 1])
                    j += 2
                else:
                    body.append(text[j])
                    j += 1
            if j >= n:
                return "".join(out), subs, i
            subs.append(("".join(body), i + 1))
            mask(i, j + 1)
            i = j + 1
            word_break = False
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            j = _match_subst_close(text, i + 2, n)
            if j == -1:
                return "".join(out), subs, i
            subs.append((text[i + 2:j], i + 2))
            mask(i, j + 1)
            i = j + 1
            word_break = False
            continue
        if in_dq:
            i += 1
            continue
        if c == "#" and word_break:
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "<" and text[i:i + 3] == "<<<":
            i += 3
            word_break = True
            continue
        if c == "<" and text[i:i + 2] == "<<":
            k = i + 2
            strip = False
            if k < n and text[k] == "-":
                strip = True
                k += 1
            while k < n and text[k] in " \t":
                k += 1
            delim = []
            while k < n and text[k] not in " \t\n;&|()<>":
                if text[k] in "'\"":
                    q = text[k]
                    e = text.find(q, k + 1)
                    if e == -1:
                        return "".join(out), subs, i
                    delim.append(text[k + 1:e])
                    k = e + 1
                elif text[k] == "\\" and k + 1 < n:
                    delim.append(text[k + 1])
                    k += 2
                else:
                    delim.append(text[k])
                    k += 1
            if delim:
                pending.append(("".join(delim), strip))
            i = k
            word_break = True
            continue
        word_break = c in " \t\n;&|("
        i += 1
    return "".join(out), subs, None


def _contexts(text):
    """Every scanning context of one file: the top level, then each substitution body, recursively.
    Fail-closed on pathological nesting."""
    result = []

    def descend(t, base, is_top, depth):
        if depth > MAX_SUBST_DEPTH:
            raise GateError("command substitutions nested deeper than {} levels; cannot evaluate"
                            .format(MAX_SUBST_DEPTH))
        masked, subs, truncated = _extract(t)
        result.append(_Ctx(masked, base, is_top, truncated))
        for body, off in subs:
            descend(body, base + off, False, depth + 1)

    descend(text, 0, True, 0)
    return result


# --- command-word and pipeline reading ---------------------------------------------------------------

def _command_word(argv):
    """The command word of one segment: (index, basename, opens_before) or (None, None, opens_before).
    Skips shell reserved words that keep the command position open (counting the block openers among
    them, so a command inside this segment's own `if` or `while` head reads as one level deeper),
    NAME=value assignments, the common wrappers, an option word directly after a wrapper, and a
    duration-like operand after `timeout`."""
    opens = 0
    i = 0
    after_wrapper = False
    after_timeout = False
    while i < len(argv):
        w = argv[i]
        if w in RESERVED_SKIP:
            if w in BLOCK_OPEN:
                opens += 1
            i += 1
            continue
        if ENV_ASSIGN_RE.match(w):
            i += 1
            continue
        base = w.rsplit("/", 1)[-1] if w else ""
        if base in WRAPPERS:
            after_wrapper = True
            after_timeout = base == "timeout"
            i += 1
            continue
        if after_wrapper and w.startswith("-"):
            i += 1
            continue
        if after_timeout and w[:1].isdigit():
            after_timeout = False
            i += 1
            continue
        return i, base, opens
    return None, None, opens


def _first_operand(argv, cmd_i):
    """The first non-option operand after the command word (the subcommand position), or None."""
    j = cmd_i + 1
    while j < len(argv):
        w = argv[j]
        if w == "--":
            j += 1
            continue
        if w.startswith("-"):
            j += 1
            continue
        return w
    return None


def _declared_command(base, commands):
    """True when the resolved command basename is one the adopter declared relied-on."""
    return base in commands


def _subcommand_ok(subcommands, sub):
    """True when the binding's subcommand narrowing admits this invocation."""
    return sub is not None and sub in subcommands


def _nonfinal(stages):
    """The pipeline stages whose exit the pipe discards: every stage but the last."""
    return stages[:-1]


def _set_pipefail_effect(argv):
    """The pipefail effect of one `set` command's argv (argv[0] == 'set'): 'on', 'off', or None.
    Reads `-o pipefail`, `+o pipefail`, and clusters such as `-euo pipefail` or `-Eeo pipefail`
    (each 'o' in a cluster consumes the next operand as an option name)."""
    effect = None
    i = 1
    n = len(argv)
    while i < n:
        w = argv[i]
        if len(w) >= 2 and w[0] in "-+" and w[1] != "-":
            sign = w[0]
            consumed = i
            for ch in w[1:]:
                if ch == "o" and consumed + 1 < n and argv[consumed + 1][:1] not in ("-", "+", ""):
                    consumed += 1
                    if argv[consumed] == "pipefail":
                        effect = "on" if sign == "-" else "off"
            i = consumed + 1
        else:
            i += 1
    return effect


def _e1_exempt(events_sorted, offset):
    """True when pipefail is provably on at `offset`: an earlier top-level enable with no later disable
    (at any level) in between. events_sorted is the file's (offset, 'on'|'off') list, sorted."""
    state = False
    for off, kind in events_sorted:
        if off >= offset:
            break
        state = kind == "on"
    return state


def _e2_exempt(top_level, next_raw, stage_index):
    """True when the script reads the flagged stage's own exit: the pipeline is a top-level statement
    and the very next simple command reads PIPESTATUS at that stage's index, or saves the whole array
    (PIPESTATUS[@] or PIPESTATUS[*]). Lexical only: the gate does not prove the script acts on it."""
    if not top_level or next_raw is None:
        return False
    if re.search(r"PIPESTATUS\[\s*{}\s*\]".format(stage_index), next_raw):
        return True
    return bool(re.search(r"PIPESTATUS\[\s*[@*]\s*\]", next_raw))


def _judge(pending, events):
    """The final findings: every pending non-final relied stage whose pipeline the E1 pipefail
    exemption does not clear (E2 was already applied where it could hold)."""
    ordered = sorted(events)
    return [rec for rec in pending if not _e1_exempt(ordered, rec["offset"])]


def _declared_regex(commands):
    """A word-boundary regex matching any declared command name, for the fail-closed checks over text
    the gate cannot read as parsed shell."""
    alts = "|".join(re.escape(c) for c in sorted(commands))
    return re.compile(r"(?<![A-Za-z0-9_./-])(?:{})(?![A-Za-z0-9_./-])".format(alts))


def _scan_context(ctx, original_text, rel, binding, telemetry, events, pending, declared_re):
    """Scan one context's segments: record set-pipefail events and command/subcommand telemetry, raise
    GateError (exit 2) on a declared command inside unparseable shell or inside an sh -c / bash -c /
    eval argument, and append a pending finding for every declared relied-on command in a non-final
    pipeline stage that E2 does not clear."""
    hooks = _hooks()
    commands = set(binding["commands"])
    subcommands = set(binding.get("subcommands") or ())
    try:
        segments = hooks._lex_command(ctx.text)
        complete, in_progress = True, False
    except ValueError:
        segments, complete, in_progress = hooks._lex_command(ctx.text, partial=True)

    offs = []
    cursor = 0
    for seg in segments:
        if seg.raw:
            pos = ctx.text.find(seg.raw, cursor)
            if pos < 0:
                pos = cursor
            offs.append(pos)
            cursor = pos + len(seg.raw)
        else:
            offs.append(cursor)

    kept = list(segments)
    unparsed_at = None
    if in_progress:
        kept = kept[:-1]
        unparsed_at = offs[-1]
    elif not complete:
        unparsed_at = cursor
    if ctx.truncated is not None and (unparsed_at is None or ctx.truncated < unparsed_at):
        unparsed_at = ctx.truncated
    if unparsed_at is not None:
        tail = ctx.text[unparsed_at:]
        if declared_re.search(tail):
            line = original_text.count("\n", 0, min(ctx.base + unparsed_at, len(original_text))) + 1
            raise GateError("{}:{}: binding {!r}: a declared command sits in shell this gate cannot "
                            "parse (an unquoted heredoc, a here-string, a process substitution, or an "
                            "unterminated construct); cannot evaluate".format(rel, line, binding["id"]))

    def line_of(abs_offset):
        return original_text.count("\n", 0, min(abs_offset, len(original_text))) + 1

    block_depth = 0
    paren_depth = 0
    run_stages = []  # (segment index, absolute offset, relied, basename, effective depth)
    for idx, seg in enumerate(kept):
        lead = len(seg.raw) - len(seg.raw.lstrip()) if seg.raw else 0
        abs_off = ctx.base + offs[idx] + lead
        argv = seg.argv
        cmd_i, base, opens_before = _command_word(argv)
        eff_depth = block_depth + paren_depth + opens_before
        relied = False
        if base is not None:
            if base == "eval" or (base in ("sh", "bash") and "-c" in argv[cmd_i + 1:]):
                joined = " ".join(argv[cmd_i + 1:])
                if declared_re.search(joined):
                    label = "eval" if base == "eval" else base + " -c"
                    raise GateError("{}:{}: binding {!r}: a declared command appears inside the raw "
                                    "text of an {} argument, which this gate cannot read as parsed "
                                    "shell; cannot evaluate".format(rel, line_of(abs_off),
                                                                    binding["id"], label))
            if _declared_command(base, commands):
                telemetry["commands"].add(base)
                sub = _first_operand(argv, cmd_i)
                if subcommands:
                    if _subcommand_ok(subcommands, sub):
                        telemetry["subcommands"].add(sub)
                        relied = True
                else:
                    relied = True
            if base == "set":
                effect = _set_pipefail_effect(argv[cmd_i:])
                if effect == "off":
                    events.append((abs_off, "off"))
                elif effect == "on" and ctx.is_top and eff_depth == 0:
                    events.append((abs_off, "on"))
        run_stages.append((idx, abs_off, relied, base, eff_depth))
        if seg.sep_after not in PIPE_SEPS:
            if len(run_stages) > 1:
                nstages = len(run_stages)
                last_idx, _o, _r, last_base, _d = run_stages[-1]
                final_word = last_base or (kept[last_idx].argv[0] if kept[last_idx].argv else "?")
                next_raw = kept[idx + 1].raw if idx + 1 < len(kept) else None
                top_level = ctx.is_top and run_stages[0][4] == 0
                for k, (sidx, soff, srelied, sbase, _sd) in enumerate(_nonfinal(run_stages)):
                    if not srelied:
                        continue
                    if _e2_exempt(top_level, next_raw, k):
                        continue
                    pending.append({"offset": soff, "rel": rel, "line": line_of(soff),
                                    "binding": binding["id"], "command": sbase,
                                    "stage": k, "nstages": nstages, "final": final_word})
            run_stages = []
        delta = 0
        for w in argv:
            if w in BLOCK_OPEN:
                delta += 1
            elif w in BLOCK_CLOSE:
                delta -= 1
            elif w in RESERVED_SKIP or ENV_ASSIGN_RE.match(w):
                continue
            else:
                break
        block_depth = max(0, block_depth + delta)
        if seg.sep_after == "(":
            paren_depth += 1
        elif seg.sep_after == ")":
            paren_depth = max(0, paren_depth - 1)


# --- configuration and surface loading (pattern copied from check_derived_command_parameters.py) -----

def _req_str_list(table, key, where):
    value = table.get(key)
    if not isinstance(value, list) or not value:
        raise GateError("{}: {!r} must be a non-empty array of strings".format(where, key))
    seen = set()
    for element in value:
        if not isinstance(element, str) or not element.strip():
            raise GateError("{}: {!r} contains a non-string or empty element".format(where, key))
        # A duplicate selector is a malformed control (grdinp): it double-counts a match and cannot be
        # reconciled soundly against the source, so fail closed rather than silently dedup and pass.
        if element in seen:
            raise GateError("{}: {!r} contains a duplicate element {!r}".format(where, key, element))
        seen.add(element)
    return value


def load_config(path):
    """Parse and fully validate the adopter configuration into a list of binding dicts. Fail-closed
    (GateError -> exit 2) on any malformed input: an unreadable or unparseable file, an unknown
    top-level key, a version that is not the exact integer 1, no bindings, a binding that is not a
    table, an unknown or missing binding field, an empty roster, a malformed id, or a duplicate id.
    An absent file is the caller's NOT APPLICABLE case, handled before this loader."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise GateError("cannot read the configuration {} ({})".format(CONFIG_REL, exc))
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's int-string limit, and a RecursionError on a deeply nested array or
    # inline table.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("configuration {} does not parse ({})".format(CONFIG_REL, exc))
    extra = set(data) - {"format-version", "binding"}
    if extra:
        raise GateError("configuration {}: unknown top-level key(s): {}".format(
            CONFIG_REL, ", ".join(sorted(extra))))
    version = data.get("format-version")
    if version is not True and version == 1 and isinstance(version, int):
        pass  # the exact integer 1
    else:
        raise GateError("configuration {}: format-version must be the exact integer 1".format(CONFIG_REL))
    bindings = data.get("binding")
    if not isinstance(bindings, list) or not bindings:
        raise GateError("configuration {}: at least one [[binding]] is required".format(CONFIG_REL))
    seen_ids = set()
    out = []
    for entry in bindings:
        if not isinstance(entry, dict):
            raise GateError("configuration {}: every [[binding]] must be a table".format(CONFIG_REL))
        missing = BINDING_REQUIRED - set(entry)
        if missing:
            raise GateError("configuration {}: a [[binding]] is missing key(s): {}".format(
                CONFIG_REL, ", ".join(sorted(missing))))
        unknown = set(entry) - BINDING_REQUIRED - BINDING_OPTIONAL
        if unknown:
            raise GateError("configuration {}: a [[binding]] has unknown key(s): {}".format(
                CONFIG_REL, ", ".join(sorted(unknown))))
        bid = entry.get("id")
        if not isinstance(bid, str) or not ID_RE.match(bid):
            raise GateError("configuration {}: binding id {!r} is not a kebab-case identifier".format(
                CONFIG_REL, bid))
        if bid in seen_ids:
            raise GateError("configuration {}: duplicate binding id {!r}".format(CONFIG_REL, bid))
        seen_ids.add(bid)
        where = "configuration {} binding {!r}".format(CONFIG_REL, bid)
        binding = {
            "id": bid,
            "paths": _req_str_list(entry, "paths", where),
            "commands": _req_str_list(entry, "commands", where),
        }
        if "subcommands" in entry:
            binding["subcommands"] = _req_str_list(entry, "subcommands", where)
        out.append(binding)
    return out


def resolve_surface(root, selector):
    """Resolve one path selector to the list of readable regular files it names, fail-closed. A selector
    is an exact repo-relative file path, or a directory prefix ending in /** (every regular file beneath
    it). Every resolved path must stay inside root (an absolute or escaping selector fails closed), must
    not be a symlink, and must decode as UTF-8 when read by the caller; a selector matching zero files
    fails closed (an unmatched declared surface is never 'nothing to scan'). Returns a sorted list."""
    root_resolved = root.resolve()
    if Path(selector).is_absolute():
        raise GateError("configuration {}: path selector {!r} is absolute; a repo-relative selector is "
                        "required".format(CONFIG_REL, selector))
    if selector.endswith("/**"):
        prefix = selector[:-3]
        base = (root / prefix)
        if not base.resolve().is_relative_to(root_resolved):
            raise GateError("configuration {}: path selector {!r} escapes the repository".format(
                CONFIG_REL, selector))
        if base.is_symlink() or not base.is_dir():
            raise GateError("configuration {}: directory-prefix selector {!r} is not a directory".format(
                CONFIG_REL, selector))
        try:
            found = sorted(walk_files(base, {".git", "__pycache__"}))
        except OSError as exc:
            raise GateError("configuration {}: cannot list surface {!r} ({})".format(
                CONFIG_REL, selector, exc))
        files = []
        for path in found:
            if path.is_symlink():
                raise GateError("configuration {}: surface {!r} contains a symlink {}".format(
                    CONFIG_REL, selector, path.relative_to(root)))
            files.append(path)
        if not files:
            raise GateError("configuration {}: path selector {!r} matched no readable regular file".format(
                CONFIG_REL, selector))
        return files
    target = root / selector
    if not target.resolve().is_relative_to(root_resolved):
        raise GateError("configuration {}: path selector {!r} escapes the repository".format(
            CONFIG_REL, selector))
    if target.is_symlink():
        raise GateError("configuration {}: path selector {!r} is a symlink".format(CONFIG_REL, selector))
    if not target.is_file():
        raise GateError("configuration {}: path selector {!r} matched no readable regular file".format(
            CONFIG_REL, selector))
    return [target]


def scan_file(root, path, binding, telemetry):
    """Scan one declared surface file for a binding's relied-on commands in non-final pipeline stages.
    Read fail-closed: an OSError or a non-UTF-8 file is exit 2 (an unreadable declared surface is never
    a clean skip). Returns a list of finding strings."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GateError("configuration {} binding {!r}: cannot read declared surface {} ({})".format(
            CONFIG_REL, binding["id"], path.relative_to(root), exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GateError("configuration {} binding {!r}: declared surface {} is not valid UTF-8".format(
            CONFIG_REL, binding["id"], path.relative_to(root)))
    rel = path.relative_to(root).as_posix()
    declared_re = _declared_regex(binding["commands"])
    events = []
    pending = []
    try:
        for ctx in _contexts(text):
            _scan_context(ctx, text, rel, binding, telemetry, events, pending, declared_re)
    except RecursionError:
        raise GateError("configuration {} binding {!r}: surface {} exceeds the scanner's nesting bounds; "
                        "cannot evaluate".format(CONFIG_REL, binding["id"], rel))
    findings = []
    for rec in _judge(pending, events):
        findings.append("{}:{}: binding {!r}: relied-on command {!r} (stage {} of {}) pipes into {!r}; "
                        "the pipe discards its exit status, so read it through pipefail or PIPESTATUS, "
                        "or drop the pipe".format(rec["rel"], rec["line"], rec["binding"], rec["command"],
                                                  rec["stage"] + 1, rec["nstages"], rec["final"]))
    return findings


def run(root):
    """Scan the declared surfaces under root. Returns the exit code 0/1/2. An absent configuration is
    NOT APPLICABLE (exit 0); a present configuration is loaded, its surfaces resolved and scanned."""
    config_path = root / CONFIG_REL
    try:
        # A symlinked configuration is fail-closed, never followed (same stance as the declared
        # surfaces); only a genuinely absent path is the NOT APPLICABLE case.
        if config_path.is_symlink():
            raise GateError("configuration {} is a symlink; a symlinked configuration is not "
                            "followed".format(CONFIG_REL))
        if not config_path.exists():
            print("NOT APPLICABLE: no {} configuration; the relied-exit-pipes gate is adopter-declared "
                  "and inert without one".format(CONFIG_REL))
            return 0
        bindings = load_config(config_path)
        findings = []
        scanned = 0
        for binding in bindings:
            files = []
            seen_files = set()
            for selector in binding["paths"]:
                for path in resolve_surface(root, selector):
                    if path in seen_files:
                        raise GateError("configuration {} binding {!r}: path selector {!r} overlaps "
                                        "another selector at {}".format(CONFIG_REL, binding["id"],
                                                                        selector, path.relative_to(root)))
                    seen_files.add(path)
                    files.append(path)
            telemetry = {"commands": set(), "subcommands": set()}
            for path in sorted(files):
                scanned += 1
                findings.extend(scan_file(root, path, binding, telemetry))
            # Reconcile the config's selectors against the real scanned source (grdinp): a command or
            # subcommand selector that matches NOTHING (a typo, or a stale surface) cannot be evaluated,
            # so it is a cannot-evaluate that fails closed (exit 2), never a silent clean pass.
            unmatched_commands = [c for c in binding["commands"] if c not in telemetry["commands"]]
            if unmatched_commands:
                raise GateError("configuration {} binding {!r}: command selector(s) {} matched no "
                                "command word in the scanned surface (cannot-evaluate)".format(
                                    CONFIG_REL, binding["id"], ", ".join(map(repr, unmatched_commands))))
            unmatched_subs = [s for s in binding.get("subcommands", ())
                              if s not in telemetry["subcommands"]]
            if unmatched_subs:
                raise GateError("configuration {} binding {!r}: subcommand selector(s) {} matched no "
                                "invocation in the scanned surface (cannot-evaluate)".format(
                                    CONFIG_REL, binding["id"], ", ".join(map(repr, unmatched_subs))))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    print("scanned {} declared surface file(s) across {} binding(s)".format(scanned, len(bindings)))
    if findings:
        print("FAIL: {} relied-exit-pipes finding(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: no declared relied-on command loses its exit status to a pipe")
    return 0


# --- self-test ---------------------------------------------------------------------------------------
# Every refuse case R1..R12 runs against the real gate (exit 1) AND against a mutant that never reports
# (exit 0), so each case is shown to discriminate. Every control C1..C11 runs against the real gate
# (exit 0) and names the over-broad implementation it catches; where that implementation is a patchable
# predicate, the over-broad mutant is also run and must exit 1. Every cannot-evaluate case X1..X4 must
# exit 2. A case that cannot run (no tempdir, no symlink support) is a NAMED failure, never a skip.

def _run_quiet(root):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root)


def _not_run(failures, case, cause):
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


CFG = ('format-version = 1\n\n[[binding]]\nid = "row-writer"\npaths = ["cmd/job.sh"]\n'
       'commands = ["tool"]\nsubcommands = ["write"]\n')
CFG_NOSUB = ('format-version = 1\n\n[[binding]]\nid = "row-writer"\npaths = ["cmd/job.sh"]\n'
             'commands = ["tool"]\n')


def self_test_main(_mkdtemp=None, _symlink=None):  # noqa: C901  a flat sequence of cases
    failures = []
    this = sys.modules[__name__]

    # --- pure-logic boundary checks (always run) ---
    for argv, want in ((["set", "-o", "pipefail"], "on"),
                       (["set", "-euo", "pipefail"], "on"),
                       (["set", "-Eeo", "pipefail"], "on"),
                       (["set", "+o", "pipefail"], "off"),
                       (["set", "-e"], None),
                       (["set", "-o"], None),
                       (["set", "-o", "errexit", "-o", "pipefail"], "on")):
        if _set_pipefail_effect(argv) != want:
            failures.append("_set_pipefail_effect({!r}) expected {!r}".format(argv, want))
    for argv, want_base in ((["tool", "write"], "tool"),
                            (["/usr/local/bin/tool", "write"], "tool"),
                            (["env", "A=1", "tool", "write"], "tool"),
                            (["nice", "tool", "write"], "tool"),
                            (["timeout", "30", "tool", "write"], "tool"),
                            (["if", "tool", "write"], "tool"),
                            (["A=1"], None)):
        _i, base, _o = _command_word(argv)
        if base != want_base:
            failures.append("_command_word({!r}) expected base {!r}, got {!r}".format(
                argv, want_base, base))
    masked, subs, trunc = _extract("row=$(tool write | tail -1)\n")
    if len(masked) != len("row=$(tool write | tail -1)\n") or trunc is not None:
        failures.append("_extract did not preserve length on a $( ) substitution")
    if subs != [("tool write | tail -1", 6)]:
        failures.append("_extract did not pull the $( ) body at its offset, got {!r}".format(subs))
    _m2, subs2, _t2 = _extract("row=`tool write` done\n")
    if subs2 != [("tool write", 5)]:
        failures.append("_extract did not pull the backquote body, got {!r}".format(subs2))
    # E1 boundary: an enable strictly before the pipeline exempts; at or after it does not.
    if not _e1_exempt([(5, "on")], 6) or _e1_exempt([(6, "on")], 6) or _e1_exempt([(7, "on")], 6):
        failures.append("_e1_exempt boundary: only a strictly earlier enable may exempt")
    if _e1_exempt([(1, "on"), (3, "off")], 6):
        failures.append("_e1_exempt: a later disable between enable and pipeline must cancel")

    # --- end-to-end layer over real synthetic trees -------------------------------------------------
    import shutil
    import tempfile

    symlink = _symlink or Path.symlink_to
    try:
        base_tmp = Path((_mkdtemp or tempfile.mkdtemp)(prefix="aiqt-relied-pipes-selftest-"))
    except OSError as exc:
        base_tmp = None
        _not_run(failures, "every end-to-end case over a real synthetic tree",
                 "no writable temp directory ({})".format(exc))

    if base_tmp is not None:
        def _fresh(tag, config, surface_files):
            r = base_tmp / tag
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            if config is not None:
                (r / ".aiqt" / "relied-exit-pipes.toml").write_text(config, encoding="utf-8")
            for rel, body in surface_files.items():
                p = r / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(body, bytes):
                    p.write_bytes(body)
                else:
                    p.write_text(body, encoding="utf-8")
            return r

        def _case(name, config, body, expected, note):
            r = _fresh(name, config, {"cmd/job.sh": body})
            rc = _run_quiet(r)
            if rc != expected:
                failures.append("{}: expected exit {}, got {} ({})".format(name, expected, rc, note))
            return r

        try:
            # --- refuse cases R1..R12 (exit 1), each later re-run against the never-report mutant ---
            refuse = [
                ("R1", CFG, "set -e\ntool write --row 7 | tail -1\ntool write --row 8\n",
                 "the seed: a row writer piped into tail under set -e"),
                ("R2", CFG, "set -e\nrow=$(tool write --row 7 | tail -1)\n",
                 "a $( ) substitution body pipeline"),
                ("R3", CFG, "set -e\nrow=`tool write --row 7 | tail -1`\n",
                 "a backquote substitution body pipeline"),
                ("R4", CFG, "tool write | grep -q ok && next-step\n",
                 "a grep -q sink inside an AND list"),
                ("R5", CFG, "if tool write | grep -q ok; then\n  echo ok\nfi\n",
                 "a pipeline in an if condition"),
                ("R6", CFG, "tool write |& tee /abs/log\n",
                 "a |& into tee: any final stage counts, not only a truncating sink"),
                ("R7", CFG, "/usr/local/bin/tool write | head -1\nenv A=1 tool write | tail -1\n"
                            "nice tool write | tail -1\n",
                 "an absolute path and the env/nice wrappers still resolve the command"),
                ("R8", CFG, "set -o pipefail\nset +o pipefail\ntool write | tail -1\n",
                 "a set +o pipefail between the enable and the pipeline cancels E1"),
                ("R9a", CFG, "f() {\n  set -o pipefail\n}\ntool write | tail -1\n",
                 "pipefail set only inside a function body is not a top-level enable"),
                ("R9b", CFG, "if true; then\n  set -o pipefail\nfi\ntool write | tail -1\n",
                 "pipefail set only inside an if block is not a top-level enable"),
                ("R10a", CFG, "tool write | tail -1\nrc=${PIPESTATUS[1]}\n",
                 "a PIPESTATUS[1] read does not cover the declared stage 0"),
                ("R10b", CFG, "tool write | tail -1\necho between\nrc=${PIPESTATUS[0]}\n",
                 "a PIPESTATUS[0] read after an intervening command is too late"),
                ("R11", CFG, "tool write \\\n  | tail -1\n",
                 "a line continuation still joins into one pipeline"),
                ("R12", CFG, "tool write | sort | tail -1\n",
                 "a three-stage pipeline: any non-final stage counts"),
                ("Rb1", CFG, "tool write | tail -1\nset -o pipefail\n",
                 "boundary: an enable AFTER the pipeline does not exempt it"),
            ]
            roots = {}
            for name, config, body, note in refuse:
                roots[name] = _case(name, config, body, 1, note)

            # Each refuse case must FAIL (exit 0) against the mutant that removes the reporting it
            # covers: a gate that never reports anything.
            real_judge = this._judge
            this._judge = lambda pending, events: []
            try:
                for name, _config, _body, _note in refuse:
                    rc = _run_quiet(roots[name])
                    if rc != 0:
                        failures.append("{} under the never-report mutant: expected exit 0, got {} "
                                        "(the case does not discriminate the mutant)".format(name, rc))
            finally:
                this._judge = real_judge

            # --- controls C1..C11 (exit 0), each naming the over-broad implementation it catches ---
            # C1: no configuration is NOT APPLICABLE. Catches a gate that requires a config.
            r = base_tmp / "C1"
            (r / "cmd").mkdir(parents=True, exist_ok=True)
            if _run_quiet(r) != 0:
                failures.append("C1: an absent configuration expected NOT APPLICABLE exit 0 "
                                "(catches a gate that requires a config)")

            # C2: an undeclared producer piped into head. Catches 'flag every pipe'.
            c2 = _case("C2", CFG_NOSUB, "git log --oneline | head -1\ntool write --row 9\n", 0,
                       "catches 'flag every pipe'")
            # C3: a provably-on pipefail exempts, in all three set spellings. Catches 'ignore pipefail'.
            c3a = _case("C3a", CFG, "set -euo pipefail\ntool write | tail -1\n", 0,
                        "catches 'ignore pipefail' (cluster form)")
            _case("C3b", CFG, "set -o pipefail\ntool write | tail -1\n", 0,
                  "catches 'ignore pipefail' (spelled-out form)")
            _case("C3c", CFG, "set -Eeo pipefail\ntool write | tail -1\n", 0,
                  "catches 'ignore pipefail' (-Eeo cluster form)")
            # C4: the very next command reads PIPESTATUS[0]. Catches 'ignore PIPESTATUS'.
            c4 = _case("C4", CFG, "tool write | tail -1\nrc=${PIPESTATUS[0]}\n", 0,
                       "catches 'ignore PIPESTATUS'")
            # C5: the declared command in the FINAL stage. Catches 'flag any stage'.
            c5 = _case("C5", CFG, "printf '%s\\n' hi | tool write\n", 0,
                       "catches 'flag any stage'")
            # C6: the declared text inside a quoted echo argument and inside a comment. Catches a
            # raw-regex scan.
            _case("C6", CFG, "echo \"tool write | tail -1\"\n# tool write | tail -1\n"
                             "tool write --row 9\n", 0, "catches a raw-regex scan")
            # C7: an OR list is not a pipe. Catches 'read || as a pipe'.
            _case("C7", CFG, "tool write || exit 1\n", 0, "catches 'read || as a pipe'")
            # C8: pipes inside a single-quoted program string. Catches a raw-regex scan.
            _case("C8", CFG, "CI_STATUS_JQ='map(.x) | length | tostring'\ntool write --row 9\n", 0,
                  "catches a raw-regex scan over quoted program text")
            # C9: an undeclared subcommand is not relied on. Catches 'ignore subcommands'.
            c9 = _case("C9", CFG, "tool list | head -1\ntool write --row 9\n", 0,
                       "catches 'ignore subcommands'")
            # C10: a quoted heredoc body is literal data. Catches heredoc-blind lexing.
            _case("C10", CFG, "cat <<'EOF'\ntool write | tail -1\nEOF\ntool write --row 9\n", 0,
                  "catches heredoc-blind lexing")
            # C11: a redirect is not a pipe. Catches 'treat a redirect as a pipe'.
            _case("C11", CFG, "tool write > /abs/out.log\ntail -1 /abs/out.log\n", 0,
                  "catches 'treat a redirect as a pipe'")

            # Over-broad mutants: each patched predicate makes its control fixture fire (exit 1), so
            # the control is shown to discriminate the over-broad implementation it names.
            overbroad = [
                ("C2", c2, "_declared_command", lambda base, commands: bool(base),
                 "flag every pipe"),
                ("C3a", c3a, "_e1_exempt", lambda events, offset: False, "ignore pipefail"),
                ("C4", c4, "_e2_exempt", lambda top, raw, k: False, "ignore PIPESTATUS"),
                ("C5", c5, "_nonfinal", lambda stages: stages, "flag any stage"),
                ("C9", c9, "_subcommand_ok", lambda subs, sub: True, "ignore subcommands"),
            ]
            for name, root_dir, attr, broad, label in overbroad:
                real = getattr(this, attr)
                setattr(this, attr, broad)
                try:
                    rc = _run_quiet(root_dir)
                finally:
                    setattr(this, attr, real)
                if rc != 1:
                    failures.append("{} under the over-broad '{}' mutant: expected exit 1, got {} "
                                    "(the control does not discriminate it)".format(name, label, rc))

            # --- cannot-evaluate cases X1..X4 (exit 2) ---
            _case("X1a", CFG, "echo hello\n", 2,
                  "a declared command matching nothing is cannot-evaluate")
            _case("X1b", CFG, "tool list\n", 2,
                  "a declared subcommand matching nothing is cannot-evaluate")
            _case("X2a", CFG, "tool write <<EOF\nbody\nEOF\n", 2,
                  "an unquoted heredoc beside a declared command is cannot-evaluate")
            _case("X2b", CFG, "tool write <(echo x)\n", 2,
                  "a process substitution beside a declared command is cannot-evaluate")
            _case("X3a", CFG, "bash -c 'tool write | tail -1'\n", 2,
                  "a declared command inside a bash -c string is cannot-evaluate")
            _case("X3b", CFG, "eval \"tool write | tail -1\"\n", 2,
                  "a declared command inside an eval argument is cannot-evaluate")
            _case("X4a", CFG, b"\xff\xfe not utf-8", 2,
                  "a non-UTF-8 declared surface is cannot-evaluate")
            _case("X4b", "not toml [ at all\n", "tool write --row 9\n", 2,
                  "a malformed configuration is cannot-evaluate")
            _case("X4c", CFG.replace("format-version = 1", "format-version = 1.0"),
                  "tool write --row 9\n", 2, "a 1.0 format-version is cannot-evaluate")
            _case("X4d", CFG.replace("format-version = 1", "format-version = true"),
                  "tool write --row 9\n", 2, "a boolean format-version is cannot-evaluate")
            _case("X4e", CFG.replace('paths = ["cmd/job.sh"]', 'paths = ["cmd/none.sh"]'),
                  "tool write --row 9\n", 2, "an unmatched path selector is cannot-evaluate")

            # X4: a symlinked declared surface (a NOT RUN failure without symlink support).
            r = _fresh("X4-symlink", CFG, {"cmd/real.sh": "tool write --row 9\n"})
            link = r / "cmd" / "job.sh"
            try:
                symlink(link, r / "cmd" / "real.sh")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X4 the symlinked declared-surface case",
                         "no symlink support ({})".format(exc))
            else:
                if _run_quiet(r) != 2:
                    failures.append("X4-symlink: a symlinked declared surface expected exit 2")

            # A present-but-dangling symlink CONFIGURATION fails closed, never NOT APPLICABLE.
            r = base_tmp / "X4-dangling-config"
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            cfg_link = r / ".aiqt" / "relied-exit-pipes.toml"
            try:
                symlink(cfg_link, r / ".aiqt" / "does-not-exist.toml")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, "X4 the dangling-symlink configuration case",
                         "no symlink support ({})".format(exc))
            else:
                if _run_quiet(r) != 2:
                    failures.append("X4-dangling-config: a dangling-symlink configuration expected "
                                    "fail-closed exit 2")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: the refuse cases R1-R12 (the seed, substitution bodies, AND lists, if "
          "conditions, |&, wrappers and absolute paths, a cancelled or non-top-level pipefail, a "
          "mismatched or late PIPESTATUS read, a line continuation, a mid-pipeline stage, and the "
          "enable-after-pipeline boundary) each fire and each catches the never-report mutant; the "
          "controls C1-C11 (no config, undeclared producers, every accepted pipefail spelling, an "
          "adjacent PIPESTATUS read, a final-stage command, quoted and commented text, an OR list, a "
          "quoted program string, an undeclared subcommand, a quoted heredoc body, and a redirect) "
          "each stay clean and the patchable ones catch their over-broad mutants; and the "
          "cannot-evaluate cases X1-X4 (unmatched selectors, unparseable shell beside a declared "
          "command, sh -c/bash -c/eval strings, and malformed or unreadable inputs) each fail closed")
    return 0


def _parse_args(argv):
    self_test = False
    for arg in argv:
        if arg == "--self-test":
            self_test = True
        else:
            print("usage: check_relied_exit_pipes.py [--self-test]; fail-closed", file=sys.stderr)
            return None
    return (self_test,)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    (self_test,) = parsed
    if self_test:
        return self_test_main()
    root = Path(__file__).resolve().parents[1]
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
