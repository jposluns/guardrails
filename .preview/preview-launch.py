#!/usr/bin/env python3
"""Launcher for the preview hooks: the file every preview hook registration runs. Stdlib only, offline.

USAGE. python3 -I -S -B /ABSOLUTE/PATH/TO/preview-launch.py MODE [ARG ...], where MODE names one
preview hook in PREVIEW_HOOKS (its file name with each hyphen written as an underscore and no .py:
stamp_truth_stop runs stamp-truth-stop.py). The hook file must sit beside this launcher. The launcher
acquires that hook file ONCE (opened without following a symbolic link, required to be a regular
file and non-empty, read, compiled) and runs the acquired bytes in this same process as __main__ (a
new module installed as sys.modules["__main__"], as a direct launch presents it; three attributes
still differ from a direct launch: __package__ is '' rather than None, __loader__ is None rather
than a SourceFileLoader, and __builtins__ is the builtins dict rather than the module, so only a
hook that reads one of those behaves differently), with argv
[<hook path>, ARG ...], so the hook otherwise behaves as when launched directly (MODE --self-test
runs the hook's own self-test); an exception the hook does not catch keeps its direct-launch exit semantics
(the interpreter prints the traceback and exits 1, and a SystemExit passes through unchanged).
  preview-launch.py --self-test   this launcher's own self-test

WHY. Several preview hooks use syntax newer than some Python 3 interpreters can compile, and Python
compiles a whole file before its first statement runs, so on such an interpreter a hook would stop
with a SyntaxError and exit 1, which Claude Code reads as a non-blocking error: a PreToolUse call would
proceed unchecked. This launcher is written in the LAUNCHER SUBSET: a closed, minimal list of Python
3.4 grammar forms (held by launcher_subset_findings below, which its self-test and
tools/check_python_floor.py both run), so every Python 3 that accepts -I compiles it. Below the floor
it refuses here, in the hook form of the canonical guard: a mode in FLOOR_FAIL_OPEN_MODES
(clock_inject on PostToolUse and PostToolUseFailure, where the tool has already run, and
stamp_truth_stop on Stop, where a block would hold every stop with no block cap;
tools/check_python_floor.py pins this literal and probes every other mode for exit 2) warns on exit
0, and every other argv (the four PreToolUse hooks, an unknown mode, no mode) refuses with exit 2,
which denies the call. At or above the floor, the hook file is acquired once (_acquire_hook: opened
with O_NOFOLLOW relative to a file descriptor of this launcher's own directory, fstat-checked to be
a regular file, read, compiled with the hook's path as file name) and ONLY that acquired content is
run, so removing or replacing the file after any check cannot divert what runs; a failure to open,
fstat, read or compile is refused by the same mode rule above (exit 0 with a warning for a fail-open
mode, exit 2 otherwise), never by an unhandled exception's exit 1, which would let a PreToolUse call
proceed unchecked. Every refusal here (the floor guard and the unknown-mode refusal
included) decides its exit status FIRST, runs ALL its diagnostic work (imports, string
formatting, serialization, encoding, unbuffered os.write delivery with a bounded retry of a
partial write) inside one try/except BaseException that swallows everything, and ends with
os._exit(status), never SystemExit: no diagnostic fault (a MemoryError included), no state of
stdout or stderr (closed before Python starts, so the sys stream is None, closed after, or a pipe
whose reader is gone), no bytes already waiting in a stream buffer (os._exit skips the
interpreter's shutdown flush, so a refusal discards such bytes rather than letting a failed flush
replace the status with 120) and no atexit handler can change that status. Two bounds hold,
stated exactly: a FULL BLOCKING PIPE on the diagnostic stream can block the os.write until the
reader drains it (O_NONBLOCK is never set on a shared descriptor, so the exit is delayed, never
changed), and an EXTERNAL SIGNAL that terminates the process ends it outside any exit-status
guarantee. A successful hook run is untouched: it keeps the interpreter's normal stream flushing
and exit semantics. RESIDUAL: on a platform without O_NOFOLLOW the open follows a symbolic link, and
the regular-file and compile checks still hold; the open of the hook's DIRECTORY resolves its path
following symbolic links (a symlinked install must keep working, and the launcher runs with the
calling user's own privilege), with O_NOFOLLOW kept on the final component, and where the platform
does not support dir_fd for os.open the hook is opened by its full name, still with O_NOFOLLOW on
the final component; and a writer whose in-place rewrite of the SAME inode is still in progress
when the launcher reads it (never a rename, removal or replacement, which the descriptor
acquisition covers) can expose a partial hook: an empty or uncompilable prefix is refused, a prefix that still compiles runs.
The steps before the acquisition refusal decides its status run outside any protected block: the
module imports (sys before the floor guard; ast, io, itertools, os, stat, tokenize, types and
warnings after it), the HERE path computation and the _hook path computation (hook_path), so a
fault in one of them (a MemoryError, for example) exits 1, which does not block a PreToolUse call.
"""
import sys

FLOOR_FAIL_OPEN_MODES = ("clock_inject", "stamp_truth_stop")

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    _floor_status = 2
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        _floor_status = 0

    def _floor_tail(number, data, attempt):
        if data and attempt < 4:
            _floor_tail(number, data[os.write(number, data):], attempt + 1)
    try:
        _floor_refusal = (
            "error: preview-launch.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        try:
            _floor_tail(2, _floor_refusal.encode("utf-8", "backslashreplace"), 1)
        except BaseException:
            pass
        if _floor_status == 0:
            import json
            _floor_tail(1, (json.dumps(dict(systemMessage=(
                "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
                "(non-blocking by design on this event)." % (sys.argv[1], _floor_refusal.strip())))) + "\n"
                ).encode("utf-8", "backslashreplace"), 1)
    except BaseException:
        pass
    os._exit(_floor_status)

import ast
import io
import itertools
import os
import stat
import tokenize
import types
import warnings

PREVIEW_HOOKS = ("clock_inject", "future_stamp_write", "record_remove_check", "stamp_truth_stop",
                 "unbounded_wait", "ungated_record")
HERE = os.path.dirname(os.path.abspath(__file__))


def _deliver_tail(number, data, attempt):
    """Write data to descriptor number with at most three os.write calls in all (the first write
    and at most two retries of a PARTIAL write; os.write may return short when a signal arrives
    mid-write or a pipe is nearly full): the attempts are
    bounded and counted up, never a loop, so the launcher subset holds. A write that BLOCKS (a full
    blocking pipe whose reader has not drained it) blocks here until the reader drains it, delaying
    the refusal's exit, never changing its status: O_NONBLOCK is never set on a shared standard
    descriptor. An external signal that terminates the process ends it outside any exit-status
    guarantee."""
    if data and attempt < 4:
        _deliver_tail(number, data[os.write(number, data):], attempt + 1)


def _deliver(number, text):
    """Best-effort diagnostic delivery for a refusal. os.write is unbuffered, so no byte waits in a
    stream buffer whose failed flush at interpreter shutdown could replace an exit status with 120,
    and the one except BaseException swallows EVERY failure of the delivery (a descriptor closed
    before Python started, closed or broken later, an encoding fault, out of memory, any exception
    at all): the caller decides the refusal's exit status BEFORE calling this and ends with
    os._exit(status), so nothing here can change it (with descriptor 2 closed before Python starts,
    sys.stderr is None, so a sys.stderr.write here would raise and exit 1, which does not block a
    PreToolUse call). The floor guard above cannot call a helper (it runs first, and
    tools/check_python_floor.py pins it by AST to HOOK_GUARD_TEMPLATE), so it carries the same form
    inline: _floor_tail and one try/except BaseException around all its diagnostic work."""
    try:
        _deliver_tail(number, text.encode("utf-8", "backslashreplace"), 1)
    except BaseException:
        pass


def hook_path(mode):
    return os.path.join(HERE, mode.replace("_", "-") + ".py")


# BEGIN LAUNCHER-SUBSET ALLOWLIST. tools/check_python_floor.py and .preview/preview-launch.py carry
# this block byte-identical (the floor gate's self-test launcher/allowlist-parity holds the two
# equal; the preview launcher is self-contained, so it cannot import the gate's copy). The block
# itself stays inside the subset it enforces (list(map(...)) and list(filter(...)) stand in for
# loops), and assumes module-level `import ast`, `import io`, `import itertools`, `import tokenize`
# and `import warnings` in its host file.
SUBSET_VERSION = (3, 4)
# The CLOSED, MINIMAL subset of node types a launcher may use, in today's AST spelling. Source: the
# Parser/Python.asdl and Grammar/Grammar files of CPython 3.4 (3.4 is the launcher floor: the first
# Python that accepts -I). Every name listed here is a statement, expression, operator or context
# form the Python 3.4 grammar already holds in the same source spelling with the same meaning (each
# has been in the grammar unchanged since Python 3.0 for the ASCII-only spellings _subset_line
# admits: identifier and escape classification follows the interpreter's Unicode database, so a
# spelling proven only under a newer database is refused), and _subset_node below refuses by form
# every spelling of a listed node that a modern parser accepts and Python 3.4 does not (enumerated
# there).
# Every node name outside this tuple is refused by name, whether it is newer than 3.4 (JoinedStr,
# NamedExpr, AnnAssign, Match, TryStar, the Async nodes, ...) or merely not needed by a launcher
# (For, While, With, Lambda, ClassDef, the comprehensions, Starred, Dict, Set, IfExp, Delete,
# AugAssign, ImportFrom, Yield, Global, Nonlocal, Assert, Break, Continue, ...).
SUBSET_NODES = (
    "Add", "And", "Assign", "Attribute", "BinOp", "BitOr", "BoolOp", "Call", "Compare", "Constant",
    "Eq", "ExceptHandler", "Expr", "FunctionDef", "Gt", "GtE", "If", "Import", "In", "Is", "IsNot",
    "List", "Load", "Lt", "LtE", "Mod", "Module", "Name", "Not", "NotEq", "NotIn", "Or", "Pass",
    "Raise", "Return", "Slice", "Store", "Subscript", "Try", "Tuple", "UnaryOp", "alias", "arg",
    "arguments", "keyword")
# The only modules a launcher may import: one per statement, unaliased, each in the Python 3.4
# standard library.
SUBSET_IMPORTS = ("ast", "io", "itertools", "json", "os", "stat", "sys", "tokenize", "types",
                  "warnings")


def _span_token(bounds, token):
    """token when it is an operator inside bounds (a ((line, column), (line, column)) pair, the
    tokenizer's own coordinates), None otherwise: the operator tokens of one except expression's
    source span."""
    if token.type == tokenize.OP and bounds[0] <= token.start and token.end <= bounds[1]:
        return token
    return None


def _paren_step(state, token):
    """One operator token of an except expression's span: state[0] is the open-bracket stack,
    state[1] flips to 1 at a comma outside every bracket pair, the one spelling of an except tuple
    the source does not parenthesize as a WHOLE (so `except (OSError), ValueError:` is caught,
    where a check of the first character alone is not: its tuple starts with a parenthesis that
    encloses only one element). The pop is guarded: a span is one complete expression, so its
    brackets balance, and the guard only keeps a hypothetical stray closer from raising here."""
    if token.string in ("(", "[", "{"):
        state[0].append(1)
    if token.string in (")", "]", "}") and state[0]:
        state[0].pop()
    if token.string == "," and not state[0]:
        state[1] = 1


def _unparenthesized(tokens, node):
    """1 when node (an except handler's Tuple, with position attributes) is spelled without
    parentheses enclosing the whole tuple (PEP 758, 3.14): a comma outside every bracket pair
    within the node's own span. Judged on the tokenizer's coordinates, never on a split of the
    source text, so an exotic line ending cannot shift which text is read (and _subset_line
    refuses a carriage return outright)."""
    bounds = ((node.lineno, node.col_offset), (node.end_lineno, node.end_col_offset))
    state = [[], 0]
    list(map(_paren_step, itertools.repeat(state),
             list(filter(None, list(map(_span_token, itertools.repeat(bounds), tokens))))))
    return state[1]


def _subset_node(found, rel, tokens, node):
    """One finding per construct of node outside the launcher subset (tokens is the file's token
    list: the AST alone cannot show the PEP 758 parentheses, so the except-tuple rule scans the
    tokens of the tuple's own span). The residual of the closed
    list, a LISTED node type carrying a form Python 3.4 does not compile, is enumerated and refused
    here: a Constant from an f- or t-string or with a numeric underscore (_subset_token; the value
    types here are 3.0 forms), every PEP 448 call shape (Starred and dict unpacking are refused by
    name above, a ** keyword by arg None here), every decorator, annotation, default and non-plain
    parameter on FunctionDef/arguments/arg (so no 3.9 decorator grammar and no 3.8 parameter forms
    are reachable), try else/finally (so no continue-through-finally grammar change is reachable),
    a non-Load list or tuple context (no unpacking targets), a bare except, an except tuple not
    parenthesized in the source (PEP 758, 3.14: the one listed-node spelling the AST cannot show,
    so _unparenthesized scans the tuple's own token span for a comma outside every bracket pair,
    which also refuses parentheses enclosing only part of the tuple, as in
    `except (OSError), ValueError:`, on the tokenizer's own line numbering), a bare or chained
    raise, and any import outside SUBSET_IMPORTS; a call or a parameter list with more than 255
    entries is refused (the pre-3.7 limit). A 3.14 identifier is never a Python 3.4 keyword
    (3.14's keyword list adds to 3.4's and removes nothing), and _subset_line restricts the whole
    source to ASCII, so a Name or attribute spelling the floor's own Unicode database does not
    classify cannot pass."""
    name = type(node).__name__
    line = getattr(node, "lineno", 0)
    if name not in SUBSET_NODES:
        found.append("%s:%d: a %s node is outside the launcher subset of the Python %d.%d grammar"
                     % ((rel, line, name) + SUBSET_VERSION))
        return
    if name == "Constant" and not (node.value is None or type(node.value) in (bool, int, str)):
        found.append("%s:%d: only a str, int, bool or None constant is in the launcher subset"
                     % (rel, line))
    if name == "FunctionDef" and (node.decorator_list or node.returns
                                  or getattr(node, "type_params", None)):
        found.append("%s:%d: a decorated, annotated or type-parameterized function is outside "
                     "the launcher subset" % (rel, line))
    if name == "arguments" and (getattr(node, "posonlyargs", None) or node.vararg
                                or node.kwonlyargs or node.kw_defaults or node.kwarg
                                or node.defaults):
        found.append("%s:%d: only plain positional parameters (no *, **, defaults, keyword-only "
                     "or positional-only parameters) are in the launcher subset" % (rel, line))
    if name == "arg" and node.annotation is not None:
        found.append("%s:%d: an annotated parameter is outside the launcher subset" % (rel, line))
    if name == "keyword" and node.arg is None:
        found.append("%s:%d: ** argument unpacking is outside the launcher subset" % (rel, line))
    if name == "Assign" and (len(node.targets) != 1
                             or type(node.targets[0]).__name__ not in ("Attribute", "Name",
                                                                       "Subscript")):
        found.append("%s:%d: only an assignment to one name, attribute or subscript is in the "
                     "launcher subset" % (rel, line))
    if name == "Try" and (node.finalbody or node.orelse or not node.handlers):
        found.append("%s:%d: only plain try/except (no else, no finally) is in the launcher "
                     "subset" % (rel, line))
    if name == "ExceptHandler" and node.type is None:
        found.append("%s:%d: a bare except clause is outside the launcher subset" % (rel, line))
    if name == "ExceptHandler" and type(node.type).__name__ == "Tuple" \
            and _unparenthesized(tokens, node.type):
        found.append("%s:%d: an except tuple that is not parenthesized in the source is outside "
                     "the launcher subset (PEP 758 is newer than Python %d.%d)"
                     % ((rel, line) + SUBSET_VERSION))
    if name == "Raise" and (node.exc is None or node.cause is not None):
        found.append("%s:%d: only `raise <exception>` (no bare raise, no `from`) is in the "
                     "launcher subset" % (rel, line))
    if name == "Import" and (len(node.names) != 1 or node.names[0].asname is not None
                             or node.names[0].name not in SUBSET_IMPORTS):
        found.append("%s:%d: only `import <module>`, one unaliased module per statement, of %s "
                     "is in the launcher subset" % (rel, line, ", ".join(SUBSET_IMPORTS)))
    if name in ("List", "Tuple") and type(node.ctx).__name__ != "Load":
        found.append("%s:%d: a list or tuple outside a load context (an unpacking target) is "
                     "outside the launcher subset" % (rel, line))
    if name == "Call" and len(node.args) + len(node.keywords) > 255:
        found.append("%s:%d: a call with more than 255 arguments is outside the launcher subset "
                     "(the pre-3.7 limit)" % (rel, line))
    if name == "arguments" and len(node.args) > 255:
        found.append("%s:%d: more than 255 parameters are outside the launcher subset "
                     "(the pre-3.7 limit)" % (rel, line))


def _subset_token(found, rel, token):
    """The newer spellings the AST cannot show on a listed node: an f- or t-string opener (a
    placeholder-free one can parse to a plain Constant) and a numeric underscore (PEP 515, 3.6)
    both read back as a Constant the walk accepts; a backslash-N named escape resolves against the
    interpreter's Unicode name table (Python 3.4 ships Unicode 6.3), so it is refused whole, raw
    strings included; and a comment that could carry a coding declaration (PEP 263 reads the first
    two lines) is refused, so no non-utf-8 codec name can reach an older interpreter."""
    kind = tokenize.tok_name[token.type]
    if kind in ("FSTRING_START", "TSTRING_START") \
            or (token.type == tokenize.NUMBER and "_" in token.string):
        found.append("%s:%d: %r is newer than Python %d.%d"
                     % ((rel, token.start[0], token.string) + SUBSET_VERSION))
    if token.type == tokenize.STRING and ("\\" + "N{") in token.string:
        found.append("%s:%d: a backslash-N named escape resolves against the interpreter's "
                     "Unicode name table and is outside the launcher subset"
                     % (rel, token.start[0]))
    if kind == "COMMENT" and token.start[0] < 3 and "coding" in token.string:
        found.append("%s:%d: a comment that could carry a coding declaration is outside the "
                     "launcher subset" % (rel, token.start[0]))


def _subset_line(found, rel, pair):
    """A non-ASCII character anywhere in the source (pair is one (index, line) from enumerate):
    identifier and escape classification follows the interpreter's Unicode database (Python 3.4
    ships Unicode 6.3), so the subset is ASCII-only, comments and string contents included. The
    length test is the Python 3.4 spelling of str.isascii (3.7 and newer; only the spelling is
    old: this allowlist as a whole still needs 3.8 for ast.parse feature_version and Constant
    nodes, so the floor interpreter cannot run the self-check): each ASCII character is one UTF-8
    byte and every other code point is more, so a line is ASCII exactly when its UTF-8 encoding is
    as long as the line. A carriage return anywhere (a CR or CRLF line ending, or a lone CR inside
    a line) is refused outright: the parser and the tokenizer read it as a line break, a split on
    newline does not, and the subset accepts no text whose line numbering the two could read
    apart."""
    if len(pair[1]) != len(pair[1].encode("utf-8")):
        found.append("%s:%d: a non-ASCII character is outside the launcher subset (Python %d.%d "
                     "ships an older Unicode database)" % ((rel, pair[0] + 1) + SUBSET_VERSION))
    if "\r" in pair[1]:
        found.append("%s:%d: a carriage return is outside the launcher subset (the parser and a "
                     "newline split would read the line numbering apart)" % (rel, pair[0] + 1))


def _subset_depth(state, token):
    """Track bracket nesting (state[0] is the open-bracket stack, state[1] the deepest size seen;
    ast.parse already accepted the text, so the brackets balance): an old parser's fixed stack caps
    how deep brackets may nest, so the subset caps them far below any shipped limit."""
    if token.type == tokenize.OP and token.string in ("(", "[", "{"):
        state[0].append(1)
    if token.type == tokenize.OP and token.string in (")", "]", "}"):
        state[0].pop()
    if len(state[0]) > state[1]:
        state[1] = len(state[0])


def _subset_not(token):
    """One `not` keyword. The 3.4 grammar's not_test is right-recursive ('not' not_test), so each
    `not` in a chain holds one frame of the same fixed parser stack the bracket cap protects;
    launcher_subset_findings caps the file's TOTAL count, which bounds every chain wherever it
    sits and however it is wrapped, far below any shipped limit."""
    return token.type == tokenize.NAME and token.string == "not"


def launcher_subset_findings(rel, text):
    """Each construct in text outside the LAUNCHER SUBSET (SUBSET_NODES with the per-form checks of
    _subset_node, the token scan of _subset_token, the ASCII-only line scan of _subset_line and
    the bracket-depth cap of _subset_depth and the `not`-count cap of _subset_not), as
    "rel:line: message" strings; [] accepts.
    This is a CLOSED ALLOWLIST, never a list of known-newer constructs: only the listed node types
    and forms pass, so a construct this check has never heard of is refused by name.
    The guarantee, stated exactly: a text accepted here ([]) uses only forms the Python 3.4
    grammar and compiler hold with today's meaning, and it also compiles under THIS interpreter's
    own compiler (the compile below: ast.parse alone accepts text the compiler refuses, `return`
    at module level for example, so a parse-only pass would overstate the guarantee); what an
    older interpreter does with a text REFUSED here is outside the guarantee.
    ast.parse(feature_version=...) is only best-effort below its documented lowest supported
    version, so it is kept as a belt, never as the guarantee. Raises SyntaxError,
    tokenize.TokenError or ValueError when text does not parse under THIS interpreter."""
    found = []
    lines = text.split("\n")
    list(map(_subset_line, itertools.repeat(found), itertools.repeat(rel),
             list(enumerate(lines))))
    tree = ast.parse(text)
    try:
        compile(text, rel, "exec")
    except (SyntaxError, ValueError) as exc:
        found.append("%s: parses but does not compile on this interpreter (the subset accepts "
                     "only text the compiler itself accepts): %s" % (rel, exc))
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    saved_filters = warnings.filters[:]
    warnings.simplefilter("ignore")
    try:
        ast.parse(text, feature_version=SUBSET_VERSION)
    except SyntaxError as exc:
        found.append("%s:%s: does not compile under the Python %d.%d grammar: %s"
                     % ((rel, exc.lineno) + SUBSET_VERSION + (exc.msg,)))
    warnings.filters = saved_filters
    list(map(_subset_node, itertools.repeat(found), itertools.repeat(rel),
             itertools.repeat(tokens), list(ast.walk(tree))))
    list(map(_subset_token, itertools.repeat(found), itertools.repeat(rel), tokens))
    depth = [[], 0]
    list(map(_subset_depth, itertools.repeat(depth), tokens))
    if depth[1] > 32:
        found.append("%s: brackets nested deeper than 32 levels are outside the launcher subset "
                     "(an old parser's fixed stack)" % rel)
    if len(list(filter(_subset_not, tokens))) > 32:
        found.append("%s: more than 32 `not` keywords are outside the launcher subset (each "
                     "chained `not` holds one frame of an old parser's fixed stack)" % rel)
    return found
# END LAUNCHER-SUBSET ALLOWLIST


def _acquire_hook(path):
    """Acquire the hook file ONCE: open it without following a symbolic link on the final component
    (O_NOFOLLOW, relative to a file descriptor of its directory where the platform supports dir_fd
    for os.open, otherwise by its full name; the directory open itself follows symbolic links so a
    symlinked install keeps working, and requires a directory where the platform has O_DIRECTORY;
    O_NONBLOCK so a FIFO cannot hold the open), require a regular file of the OPENED descriptor
    (fstat, so no rename, removal or replacement after the open can swap what was judged), read
    that descriptor's bytes, refuse an empty read, and compile the bytes with path as the file
    name. The directory descriptor is closed on every path, the inner open's failure included.
    Returns the compiled code object, or a failure as a (reason, exception or None) tuple, never
    formatting the exception here: an exception's text is computed by its own __str__, which can
    itself raise (a MemoryError, a SystemExit, anything), so the dispatch below formats it only
    inside the refusal's protected block, after the exit status is decided. The dispatch also
    calls this inside a try/except BaseException, so no failure here, caught or not, surfaces as
    an unhandled exception's exit 1 (a non-blocking error to a PreToolUse call)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        if os.open in os.supports_dir_fd:
            directory_fd = os.open(os.path.dirname(path) or ".",
                                   os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                fd = os.open(os.path.basename(path), flags, dir_fd=directory_fd)
            except (OSError, ValueError, MemoryError) as exc:
                os.close(directory_fd)
                raise exc
            os.close(directory_fd)
        else:
            fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return ("it is not a regular file", None)
        handle = os.fdopen(fd, "rb")
        data = handle.read()
        handle.close()
    except (OSError, ValueError, MemoryError) as exc:
        return ("cannot open or read it without following a symbolic link", exc)
    if not data:
        return ("it is empty (zero bytes were read)", None)
    try:
        return compile(data, path, "exec")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return ("cannot compile it", exc)


def _missing_sibling(mode):
    if os.path.isfile(hook_path(mode)):
        return ""
    return "missing sibling hook %s" % hook_path(mode)


def _stderr_failure(failure):
    sys.stderr.write("SELF-TEST FAIL: %s\n" % failure)


def self_test():
    """The launcher's own invariants: its whole source stays inside the launcher subset
    (launcher_subset_findings above, byte-identical to the copy in tools/check_python_floor.py,
    whose self-test holds the two equal), every fail-open mode is a known hook, and each hook named
    in PREVIEW_HOOKS sits beside it (required when AIQT_HOOKS_REQUIRE_SIBLINGS is 1)."""
    failures = []
    source = ""
    try:
        handle = open(__file__, "rb")
        source = handle.read().decode("utf-8")
        handle.close()
    except (OSError, ValueError) as exc:
        failures.append("cannot read this launcher: %s" % exc)
    if source:
        try:
            failures.extend(launcher_subset_findings(os.path.basename(__file__), source))
        except (SyntaxError, tokenize.TokenError, ValueError) as exc:
            failures.append("cannot tokenize or parse this launcher: %s" % exc)
    if not set(FLOOR_FAIL_OPEN_MODES) <= set(PREVIEW_HOOKS) or list(PREVIEW_HOOKS) != sorted(set(PREVIEW_HOOKS)):
        failures.append("FLOOR_FAIL_OPEN_MODES must name known hooks; PREVIEW_HOOKS must be sorted, unique")
    if os.environ.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
        failures.extend(list(filter(None, list(map(_missing_sibling, PREVIEW_HOOKS)))))
    list(map(_stderr_failure, failures))
    verdict = "PASS"
    if failures:
        verdict = "FAIL"
    sys.stdout.write("SELF-TEST %s\n" % verdict)
    if failures:
        return 1
    return 0


if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
    raise SystemExit(self_test())
if len(sys.argv) < 2 or sys.argv[1] not in PREVIEW_HOOKS:
    try:
        _deliver(2, "error: preview-launch.py: the first argument must name one of %s. Nothing was run.\n"
                 % ", ".join(PREVIEW_HOOKS))
    except BaseException:
        pass
    os._exit(2)
_hook = hook_path(sys.argv[1])
# The same mode rule as the floor guard: a hook file that cannot be acquired (missing, a symbolic
# link, not a regular file, empty, unreadable, uncompilable) must not surface as an unhandled
# exception's exit 1 (a non-blocking error that lets a PreToolUse call proceed). Only the acquired
# content runs: removing, renaming or replacing the file after the open changes nothing; an
# in-place rewrite of the same inode still in progress at the read is the module docstring's
# stated residual. The refusal's exit status is decided FIRST, before the acquisition runs; the
# acquisition itself runs inside a try/except BaseException (an exception it does not map is
# refused by the mode rule too, as "the acquisition raised"); every diagnostic step (formatting the
# acquisition error, whose __str__ can raise anything, a SystemExit included, the message,
# the import of json, serialization, delivery) runs inside one try/except BaseException, with the bare reason as the
# fallback when the error's text cannot be formatted; the refusal ends with os._exit, which no
# shutdown flush, stream-buffer state or atexit handler can change.
_refusal_status = 2
if sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
    _refusal_status = 0
_got = ("the acquisition raised", None)
try:
    _got = _acquire_hook(_hook)
except BaseException:
    pass
if isinstance(_got, tuple):
    try:
        _reason = _got[0]
        try:
            if _got[1] is not None:
                _reason = "%s: %s" % (_got[0], _got[1])
        except BaseException:
            pass
        _missing = ("error: preview-launch.py: cannot acquire the hook file %s (%s). "
                    "Nothing was run (cannot evaluate).\n" % (_hook, _reason))
        _deliver(2, _missing)
        if _refusal_status == 0:
            import json
            _deliver(1, json.dumps(dict(systemMessage=(
                "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
                "(non-blocking by design on this event)." % (sys.argv[1], _missing.strip())))) + "\n")
    except BaseException:
        pass
    os._exit(_refusal_status)
sys.argv = [_hook] + sys.argv[2:]
# The acquired content runs in a NEW module installed as sys.modules["__main__"] (as a direct
# launch presents it), so code that resolves names through the main module (a dataclass string
# annotation, for example) sees the hook's globals, never this launcher's. An exception the hook
# raises and does not catch propagates out of the exec statement: the traceback prints and the
# process exits 1, exactly as when the hook file is launched directly (a direct `python3 -I`
# launch does not put the hook's directory on sys.path, and neither does this).
_module = types.ModuleType("__main__")
_module.__file__ = _hook
_module.__package__ = ""
_module.__cached__ = None
sys.modules["__main__"] = _module
exec(_got, _module.__dict__)
