#!/usr/bin/env python3
"""Render .aiqt/core/renderers.toml: the manifest-covered RENDERER/GENERATOR DECLARATION (VER-CORE 6.5,
R9-5). Offline, stdlib only, fail-closed.

For each declared adapter renderer (gen_agents, gen_adapters, gen_claude, gen_cursor, gen_rules, gen_skill,
gen_worker_pack) this tool STATICALLY PARSES (ast, never import) the generator's module-level RENDERER_DECL
literal ({"renderer-id": <str>, "semantics-revision": <int>}) and its GENSRC_OUTPUTS targets (recovered
through gen_gensrc's own validated loader, the house import-reuse pattern), computes the ORDERED PACK-LOCAL
IMPORT CLOSURE (the entrypoint plus every tools/<module>.py it transitively imports, entrypoint first then
the rest bytewise), and computes a FRAMED generator-code digest over per-file records "<path>\\t<bytes>\\t
<sha256>\\n" in closure order, NEVER a raw concatenation (so a boundary-shifting edit stays detectable).
Any edit anywhere in a closure changes the framed digest, forcing a declaration diff at the Step-4 delta
gate; check_manifest.py recomputes the ARTIFACTS roster from these targets.

Fail-closed (GateError -> exit 2): a tools/*.py that declares RENDERER_DECL but is absent from RENDERERS
(a --check against a fresh run proves the file current, never the roster complete, since both read the
same RENDERERS); a tools/*.py that binds or reads RENDERER_DECL other than in ONE single-target top-level
assignment or annotated assignment to the bare name, or that spells it as an identifier in any
string-valued AST field, as a string the bounded fold computes equal to the name (a comparison against
the string or a third or later call argument aside; the fold also reads a supported decode of spelled
bytes, a REFUSAL-ONLY input, so a name assembled through one is refused like a spelled one, under the
same two exemptions, each NODE-LOCAL: it accepts only the outer value in that position, while a
descendant equal to the name is judged in its own position, so decoded bytes that already spell it are
refused where the decode reads them, as its receiver or as the first argument of str), or as
a dot- or colon-separated component of a
folded path string, or that keys a RECOGNIZED module-namespace access (a subscript of a bare vars() or
globals() call, or a setattr or delattr call on a sys.modules entry) with anything the bounded fold
cannot reduce, call-free, to a constant string (a call-derived fold never ACCEPTS a key; _declaration,
the single predicate discovery and the reader share; a
spelling the fold cannot see is NOT refused and is in the disclosed residual); a tools/*.py that cannot
be read, declares a source encoding other than UTF-8 or cannot be parsed (any parse error class,
including a parser stack overflow on deep nesting), or a *.py entry that is not a regular file
(_declared_renderer_stems, which also discloses the spelled-read and reflective-write residual); a
missing/malformed RENDERER_DECL or GENSRC_OUTPUTS; a pack-local
import that cannot be statically resolved (a relative import, or a wildcard `from <pack-local> import *`);
an unreadable closure member; or any other cannot-evaluate. DISCLOSED RESIDUAL (disclose-guard-residuals):
the closure is computed from statically-parsed Import/ImportFrom nodes only. A truly-dynamic pack-local
import (importlib.import_module, __import__, or globals() manipulation) is beyond static analysis; it is
not used by any declared generator today, and the delta gate's fail-closed-on-incomplete-closure leg is
the backstop, but this generator does not detect that class and discloses it here rather than implying it.

  gen_renderers.py             regenerate .aiqt/core/renderers.toml
  gen_renderers.py --check     exit 1 if the committed file differs from a fresh regeneration; write nothing
  gen_renderers.py --self-test build synthetic generators and assert the fail-closed invariants
  gen_renderers.py --root DIR  operate on DIR instead of the repo root (fixtures)

Exit convention (matches the repo's gates): 0 clean; 1 drift; 2 malformed/unreadable input or any
cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_renderers.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import codecs
import hashlib
import io
import os
import stat
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import linked_dir_failures, repo_root, reconcile  # noqa: E402
from gen_rules import SLUG_RE  # noqa: E402  the authoritative renderer-id slug syntax
import gen_gensrc  # noqa: E402  reuse its validated GENSRC_OUTPUTS loader, never a second parser

GENSRC_OUTPUTS = (
    {"target": ".aiqt/core/renderers.toml", "kind": "file",
     "sources": ("tools/",), "regenerate": "python3 tools/gen_renderers.py"},
)
RENDERERS_REL = ".aiqt/core/renderers.toml"
# The declared adapter renderers, in a fixed hand-kept order (not sorted by renderer-id or by file name) for a
# deterministic render; reordering changes the bytes of renderers.toml. build_rows fails closed when a
# tools/*.py file declares RENDERER_DECL but is absent here (_declared_renderer_stems).
RENDERERS = ("gen_agents", "gen_adapters", "gen_claude", "gen_cursor", "gen_rules", "gen_skill",
             "gen_worker_pack")


class GateError(Exception):
    """A fail-closed condition (malformed declaration, unresolvable import, unreadable input): exit 2."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _toml_str(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# Every exception class reading a hostile *.py can raise: OSError (unreadable: a permission, a directory, a
# vanished file) and MemoryError (too large to hold). Each is a cannot-evaluate reported as a READ failure.
_READ_ERRORS = (OSError, MemoryError)
# Every exception class parsing it can raise: SyntaxError (incl. a bad coding cookie or invalid UTF-8),
# ValueError (incl. UnicodeDecodeError), and MemoryError or RecursionError (a parser or compiler stack overflow
# on very deep nesting). Each is a cannot-evaluate (GateError, exit 2), never a traceback.
_PARSE_ERRORS = (SyntaxError, ValueError, MemoryError, RecursionError)


def _parse_module(path, where, purpose):
    """Parse a tools/*.py to an AST from its raw BYTES, as Python's own import does (a BOM and a PEP 263
    coding cookie are honoured, never bypassed by a UTF-8 text read), after refusing a declared source
    encoding other than UTF-8: a UTF-7 cookie, say, can turn a line that reads as a comment in UTF-8 into a
    statement. Fail-closed (GateError) on that cookie, on every exception class in _READ_ERRORS (reported as
    a read failure) and on every exception class in _PARSE_ERRORS (reported as a parse failure)."""
    try:
        data = path.read_bytes()
    except _READ_ERRORS as exc:
        raise GateError("{}: cannot read it to scan for {} ({}: {})".format(
            where, purpose, type(exc).__name__, exc))
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        if codecs.lookup(encoding).name not in ("utf-8", "utf-8-sig"):
            raise GateError("{}: declares the source encoding {!r}; only UTF-8 is supported, so it cannot be "
                            "scanned for {} and is refused".format(where, encoding, purpose))
        return ast.parse(data, filename=where)
    except _PARSE_ERRORS as exc:
        raise GateError("{}: cannot parse it to scan for {} ({}: {})".format(
            where, purpose, type(exc).__name__, exc))


def _read_renderer_decl(path, where):
    """Recover a generator's single module-level RENDERER_DECL literal by parsing to an AST and evaluating
    ONLY that literal (ast.literal_eval); the module is never imported. The binding is found by
    _declaration, the same predicate discovery uses, so the two accept exactly the same binding set.
    Fail-closed (GateError) when there is not exactly one top-level assignment or annotated assignment to
    RENDERER_DECL (or any other use of it), when that binding has no value or a non-literal value, or when
    the shape is wrong ({'renderer-id': <slug str>, 'semantics-revision': <non-negative int>})."""
    bindings = _declaration(_parse_module(path, where, "RENDERER_DECL"), where)
    if not bindings:
        raise GateError("{}: expected exactly one top-level RENDERER_DECL assignment or annotated "
                        "assignment, found none".format(where))
    value = bindings[0][1]
    if value is None:
        raise GateError("{}: RENDERER_DECL is annotated but never assigned a value".format(where))
    try:
        decl = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise GateError("{}: RENDERER_DECL must be a literal dict ({})".format(where, exc))
    if not isinstance(decl, dict) or set(decl) != {"renderer-id", "semantics-revision"}:
        raise GateError("{}: RENDERER_DECL keys must be exactly renderer-id/semantics-revision".format(where))
    rid = decl["renderer-id"]
    rev = decl["semantics-revision"]
    # renderer-id is the authoritative slug (this round's #3): reject a non-slug (e.g. "Bad ID") at the
    # source RENDERER_DECL, so a malformed id can never be rendered into a "fresh" renderers.toml.
    if not isinstance(rid, str) or not SLUG_RE.fullmatch(rid):
        raise GateError("{}: renderer-id {!r} is not a valid slug".format(where, rid))
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise GateError("{}: semantics-revision must be a non-negative integer".format(where))
    return rid, rev


# A readable name for the construct that binds RENDERER_DECL, for the fail-closed message.
_BINDER_FORMS = {"Assign": "an assignment", "AnnAssign": "an annotated assignment",
                 "AugAssign": "an augmented assignment", "NamedExpr": "an assignment expression (walrus)",
                 "Delete": "a del statement", "For": "a for-loop target", "AsyncFor": "a for-loop target",
                 "comprehension": "a comprehension target", "withitem": "a with-as target",
                 "TypeAlias": "a type alias statement"}
_BLOCK_FORMS = {"If": "an if/else block", "Try": "a try block", "TryStar": "a try block",
                "With": "a with block", "AsyncWith": "a with block", "For": "a for block",
                "AsyncFor": "a for block", "While": "a while block", "Match": "a match block",
                "FunctionDef": "a def block", "AsyncFunctionDef": "a def block", "ClassDef": "a class block",
                "Lambda": "a lambda body"}
# The scopes among _BLOCK_FORMS: only their BODY runs inside them. A decorator, a default, an annotation or
# a base is evaluated in the enclosing scope, so a binder there is not "inside" the def, class or lambda.
_SCOPE_FORMS = ("FunctionDef", "AsyncFunctionDef", "ClassDef", "Lambda")


def _top_level_bindings(tree):
    """Every (target, value) pair binding the bare name RENDERER_DECL as a target of a top-level Assign
    (each such target of a chained assignment counts) or AnnAssign (value None for a bare annotation)."""
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        found.extend((t, node.value) for t in targets if isinstance(t, ast.Name) and t.id == "RENDERER_DECL")
    return found


def _declaration(tree, where):
    """THE supported RENDERER_DECL declaration, the single predicate shared by discovery
    (_declared_renderer_stems) and the reader (_read_renderer_decl) so both accept exactly the same binding
    set: at most ONE single-target top-level Assign or AnnAssign to the bare name, and no other use of it
    that _unsupported_binding sees. Returns a list of zero or one (target, value) pair. Fail-closed
    (GateError) on an unsupported use (_unsupported_binding) and on more than one top-level binding, since a
    later rebinding (an annotated one, say) or an in-place change to the declared dict (a subscript store or
    an update call, which needs a read or an alias _unsupported_binding refuses) would make the value Python
    runs differ from the value read. The one exception is the reflective residual _declared_renderer_stems
    discloses."""
    form = _unsupported_binding(tree)
    if form is not None:
        raise GateError("{}: unsupported use of RENDERER_DECL: {}; the only supported use is one "
                        "single-target top-level assignment or annotated assignment to the bare name, never "
                        "read, rebound or otherwise named".format(where, form))
    bindings = _top_level_bindings(tree)
    if len(bindings) > 1:
        raise GateError("{}: RENDERER_DECL is bound {} times at top level; exactly one top-level assignment "
                        "or annotated assignment is a supported declaration".format(where, len(bindings)))
    return bindings


# A fold longer than this is dropped; the cap bounds the memory a long + chain can take. A value over the
# cap is simply not folded: a spelled name or path that long stays in the residual
# _declared_renderer_stems discloses, and a recognized module-namespace access keyed by it is reported as
# unfoldable (over-reporting, the safe direction).
_FOLD_CAP = 64


def _ascii_compatible(codec):
    """Whether codec decodes EVERY single ASCII byte, probed one byte at a time, to that character. utf-16
    and cp500 fail the probe outright; unicode_escape and utf-7 fail it because a lone backslash or a
    lone + is an incomplete sequence (a whole-string probe would let unicode_escape through although an
    escape pair means a different string at run time); an unknown or non-text codec raises and is
    declined the same way. A declined codec is never folded and its decode stays in the residual
    _declared_renderer_stems discloses."""
    try:
        return all(bytes([i]).decode(codec) == chr(i) for i in range(128))
    except (LookupError, ValueError):
        return False


def _folded_decode(node, folded, from_bytes):
    """The str value of a decode call the bounded fold computes EXACTLY, by decoding the spelled bytes
    with the named codec, or None. Folded: <folded bytes>.decode() with no argument (utf-8),
    <folded bytes>.decode(<codec literal>) and a two-argument str(<folded bytes>, <codec literal>), each
    with no keyword and only an ASCII-compatible codec literal (_ascii_compatible). The route test is
    syntactic (a shadowed str or a bytes subclass still folds), which can only over-refuse, the safe
    direction. Every other decode spelling stays unfolded and is in the disclosed residual: codecs.decode,
    an unbound bytes.decode(...) call, a computed or keyword codec, an errors argument, a one- or
    three-argument str(...), and a codec literal that is not ASCII-compatible. Spelled bytes the named
    codec refuses are not folded either: at run time that expression raises instead of producing a name.
    The folded value is REFUSAL-ONLY input: _folded_strings marks every call-derived fold, and
    _unsupported_binding never ACCEPTS a module-namespace key through one."""
    if node.keywords:
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr == "decode" and len(node.args) <= 1:
        receiver, codec_args = node.func.value, node.args
    elif isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 2:
        receiver, codec_args = node.args[0], node.args[1:]
    else:
        return None
    if id(receiver) not in from_bytes:
        return None
    codec = "utf-8"
    if codec_args:
        if not (isinstance(codec_args[0], ast.Constant) and isinstance(codec_args[0].value, str)):
            return None
        codec = codec_args[0].value
    if not _ascii_compatible(codec):
        return None
    try:
        return folded[id(receiver)].encode("latin-1").decode(codec)
    except ValueError:
        return None


def _folded_strings(tree):
    """({id(node): str}, the set of ids whose fold passed through a CALL) for every expression node whose
    string value a BOUNDED constant fold computes: a str constant (an implicit concatenation is already
    one), a bytes constant decoded as latin-1 (every byte decodes, so a spelled bytes name or path is
    judged by the same rules as a str one; a consumer such as pickle reads spelled bytes directly, with
    no decode in the source), an f-string whose every part is such a STR value (a replacement field with
    no conversion other than !s and no format spec; a bytes-valued field renders b'...' at run time, bare
    or under !s, so it is never folded), a + of two such operands of the SAME kind (str+str or
    bytes+bytes; a str-plus-bytes + is a run-time TypeError, so it is never folded), and a supported
    decode of a folded BYTES node to the exact str the run time produces (_folded_decode: .decode() bare
    or with one ASCII-compatible codec literal, and a two-argument str(<bytes>, <codec literal>)), so a
    name decoded piecewise and then concatenated folds like a spelled one. A call-derived fold, marked in
    the second member transitively, is REFUSAL-ONLY input: a local rebinding of str or of a method name
    makes a syntactic call fold unsound in the ACCEPTING direction, so _unsupported_binding refuses
    through it and never accepts a module-namespace key through it. Folded bottom-up over the reversed
    breadth-first walk, where every child precedes its parent, so a long chain needs no recursion; a
    value longer than _FOLD_CAP is dropped."""
    folded = {}
    from_bytes = set()
    from_call = set()
    for node in reversed(list(ast.walk(tree))):
        is_bytes = False
        tainted = False
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                value = node.value
            elif isinstance(node.value, bytes):
                value = node.value.decode("latin-1")
                is_bytes = True
            else:
                value = None
        elif isinstance(node, ast.FormattedValue):
            value = (folded.get(id(node.value))
                     if node.conversion in (-1, ord("s")) and node.format_spec is None
                     and id(node.value) not in from_bytes else None)
            tainted = id(node.value) in from_call
        elif isinstance(node, ast.JoinedStr):
            parts = [folded.get(id(part)) for part in node.values]
            value = None if any(part is None for part in parts) else "".join(parts)
            tainted = any(id(part) in from_call for part in node.values)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = folded.get(id(node.left)), folded.get(id(node.right))
            is_bytes = id(node.left) in from_bytes
            tainted = id(node.left) in from_call or id(node.right) in from_call
            if left is None or right is None or is_bytes != (id(node.right) in from_bytes):
                value = None
            else:
                value = left + right
        elif isinstance(node, ast.Call):
            value = _folded_decode(node, folded, from_bytes)
            tainted = True
        else:
            continue
        if value is not None and len(value) <= _FOLD_CAP:
            folded[id(node)] = value
            if is_bytes:
                from_bytes.add(id(node))
            if tainted:
                from_call.add(id(node))
    return folded, from_call


def _unsupported_binding(tree):
    """The description of the first use of RENDERER_DECL in `tree` other than ONE single-target top-level
    Assign or AnnAssign to the bare name, or None when there is none. The identifier scan is bounded by
    construction, never node shape by node shape: for EVERY walked node, EVERY string-valued or
    list-of-string field it carries (ast.iter_fields) is refused when the reserved name is one of its
    dot-separated components. One rule therefore covers a def/class name, a parameter, a type parameter, an
    except-as, a match capture, MatchMapping.rest, MatchClass.kwd_attrs, a global/nonlocal name, an
    attribute, a keyword-argument name, an import alias or module in any dotted position (import-as,
    import RENDERER_DECL.sub, from RENDERER_DECL import x), and any string field a later Python adds. A
    Name and a Constant are the two nodes the rule routes around. A Name, because the declaration itself
    is one: the allowed top-level targets pass, any other Store/Del is refused naming its binder form, and
    a Load of the bare name is refused because RENDERER_DECL[...] = ... or RENDERER_DECL.update(...)
    changes in place the value the reader recovered; a chained assignment is refused because its other
    target is an alias. A Constant, because the bounded fold below judges its value.

    A string or bytes CONSTANT is governed by the bounded fold, never the identifier rule (a bytes
    constant folds decoded as latin-1, so a spelled bytes name or path is judged like a str one, while a
    bytes-valued f-string replacement field, which renders b'...' at run time, and a str-plus-bytes +,
    a run-time TypeError, are never folded): any expression
    _folded_strings computes to exactly 'RENDERER_DECL' is refused unless _inert allows it as one of
    the TWO EXEMPT USES, a comparison operand or a third or later positional argument of a call with no
    starred argument before it (each exemption is NODE-LOCAL: _inert reads only the node's own parent, so
    a descendant of an exempt value that folds to the name, such as the spelled bytes a decode reads, is
    judged in its own position and refused there unless that position is itself exempt), and any folded
    value whose dot- or colon-separated components include the
    name is refused with NO exemption, because a resolver splits such a path at run time and reaches the
    declared value through it (pkgutil.resolve_name('pkg.RENDERER_DECL'), pkgutil.resolve_name
    ('pkg:RENDERER_DECL'), operator.attrgetter('m.RENDERER_DECL')); this file's own scan never
    spells such a path in a foldable string. Dot and colon are the ONLY separators this rule knows; a
    spelled path in any other resolver syntax is NOT detected and is part of the residual
    _declared_renderer_stems discloses. A RECOGNIZED module-namespace access holds its key to a
    stronger rule: inside a subscript of a bare, argument-free vars() or globals() call (in ANY context,
    since a read reaches the same dict an assignment writes and a del removes from it), and in the name
    position of a bare setattr or delattr call whose first argument is a sys.modules subscript, only a
    key the fold reduces WITHOUT A CALL to a non-reserved constant string is accepted; every other key
    (a call, a conversion builtin, an f-string holding a call, a name, anything unfolded, and any
    call-derived fold, the supported decode forms included) is refused, because a computed key can be
    RENDERER_DECL at run time and no fold of a computed form survives a local rebinding: the decode fold
    is REFUSAL-ONLY input, feeding the exact-name and dot-component rules, never acceptance. Both
    exemptions are required by
    this file's own scan
    (self-test case (q)): the walk compares folded values and str fields against the literal name, and
    _parse_module takes it as the third positional argument (purpose). The exemptions are allowed uses, NOT
    proof of inertness: an overloaded comparison method (ANY comparison method receives the spelled name as
    its operand; __eq__, __ne__, __lt__ and __contains__ are examples, never a closed list) and a
    helper taking it as a third or later argument (functools.partial(setattr, module, 'RENDERER_DECL'))
    each consume the literal name at run time and can store or read through it; both routes are part of the
    residual _declared_renderer_stems discloses."""
    allowed = {id(t) for t, _ in _top_level_bindings(tree)}
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    folded, from_call = _folded_strings(tree)

    def _where(node):
        # The innermost enclosing compound statement whose body holds the binder, if any.
        child, anc = node, parents.get(id(node))
        while anc is not None and not isinstance(anc, ast.Module):
            name = type(anc).__name__
            if name in _BLOCK_FORMS:
                if name not in _SCOPE_FORMS or child is anc.body or (
                        isinstance(anc.body, list) and any(child is stmt for stmt in anc.body)):
                    return " inside " + _BLOCK_FORMS[name]
            child, anc = anc, parents.get(id(anc))
        return ""

    def _stores(node):
        return isinstance(node.ctx, (ast.Store, ast.Del))

    def _names_decl(node):
        return folded.get(id(node)) == "RENDERER_DECL"

    def _key_fold(node):
        # The ACCEPTING fold for a recognized module-namespace key: only a call-free fold accepts a key,
        # since a call-derived one (a supported decode, say) means whatever a local rebinding says; the
        # full fold still REFUSES through _names_decl and the dot-component rule.
        return None if id(node) in from_call else folded.get(id(node))

    def _inert(node):
        # A comparison operand, or a third or later positional argument with no starred argument before it:
        # every direct namespace write (setattr, operator.setitem, dict.__setitem__, a bound __setitem__ or
        # setdefault) takes its key first or second, so a later argument (the purpose this file passes to
        # _parse_module, say) is not that key; a helper that takes it later is in the disclosed residual.
        parent = parents.get(id(node))
        if isinstance(parent, ast.Compare):
            return True
        if not isinstance(parent, ast.Call):
            return False
        for index, arg in enumerate(parent.args):
            if isinstance(arg, ast.Starred):
                return False
            if arg is node:
                return index >= 2
        return False

    def _module_namespace(expr):
        # A subscript of a bare, argument-free vars() or globals() call reaches the scanned module's own
        # namespace dict in every context; a namespace reached any other way (an imported name, an alias,
        # a vars(x) with an argument) is in the residual _declared_renderer_stems discloses.
        return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
                and expr.func.id in ("vars", "globals") and not expr.args and not expr.keywords)

    def _sys_modules_entry(expr):
        # A sys.modules[...] subscript, the one module-object spelling this scan recognizes as a setattr
        # or delattr receiver; a module held in any other expression is in the same residual.
        return (isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Attribute)
                and expr.value.attr == "modules" and isinstance(expr.value.value, ast.Name)
                and expr.value.value.id == "sys")

    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) > 1
                and any(id(t) in allowed for t in node.targets)):
            return "a chained assignment (each other target is an alias of the declared value)"
    for node in ast.walk(tree):
        if _names_decl(node) and not _inert(node):
            return ("the string 'RENDERER_DECL' outside a comparison or a later call argument (a first or "
                    "second call argument such as globals().setdefault('RENDERER_DECL', ...), a dict key, or "
                    "a value a store can use)" + _where(node))
        value = folded.get(id(node))
        if (value is not None and value != "RENDERER_DECL"
                and "RENDERER_DECL" in value.replace(":", ".").split(".")):
            return ("a string whose dot- or colon-separated components include RENDERER_DECL (a resolver "
                    "such as pkgutil.resolve_name or operator.attrgetter splits such a path at run time "
                    "and can reach the declared value through it)" + _where(node))
        if isinstance(node, ast.Name):
            if node.id != "RENDERER_DECL" or id(node) in allowed:
                continue
            if not _stores(node):
                return ("a read of the bare name (through which RENDERER_DECL[...] = ... or "
                        "RENDERER_DECL.update(...) changes the value in place)" + _where(node))
            binder, unpack = parents.get(id(node)), False
            while isinstance(binder, (ast.Tuple, ast.List, ast.Starred)):
                unpack = True
                binder = parents.get(id(binder))
            kind = type(binder).__name__
            form = _BINDER_FORMS.get(kind, "a {} statement".format(kind))
            if unpack:
                form = "a tuple, list or starred unpacking in " + form
            if kind in ("comprehension", "withitem"):
                binder = parents.get(id(binder))
            return form + _where(binder)
        if isinstance(node, ast.Constant):
            continue  # a string constant is judged by the fold rule above, never by the identifier rule
        if isinstance(node, ast.alias) and node.asname is None and node.name == "*":
            return "a wildcard import" + _where(node)
        if isinstance(node, ast.Subscript):
            if _stores(node) and _names_decl(node.slice):
                return ("a subscript store or delete keyed 'RENDERER_DECL' (such as vars() or globals())"
                        + _where(node))
            if _module_namespace(node.value) and _key_fold(node.slice) is None:
                return ("a vars() or globals() subscript whose key the bounded fold cannot reduce, "
                        "call-free, to a constant string (a computed key can name RENDERER_DECL at run "
                        "time; only a key that folds without a call to a non-reserved constant string "
                        "is accepted)" + _where(node))
        elif isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name) and node.func.id in ("setattr", "delattr")
                    and len(node.args) >= 2):
                if _names_decl(node.args[1]):
                    return "a {} call naming RENDERER_DECL".format(node.func.id) + _where(node)
                if _sys_modules_entry(node.args[0]) and _key_fold(node.args[1]) is None:
                    return ("a {} call on a sys.modules entry whose attribute name the bounded fold "
                            "cannot reduce, call-free, to a constant string (a computed name can be "
                            "RENDERER_DECL at run time)".format(node.func.id) + _where(node))
        for field, value in ast.iter_fields(node):
            for item in (value if isinstance(value, list) else (value,)):
                if isinstance(item, str) and "RENDERER_DECL" in item.split("."):
                    return ("the identifier RENDERER_DECL in the {} field of an ast.{} node (every "
                            "string-valued or list-of-string AST field is refused, whatever the node "
                            "shape)".format(field, type(node).__name__) + _where(node))
    return None


def _declared_renderer_stems(tools_dir):
    """Every tools/<stem>.py carrying a top-level RENDERER_DECL declaration as _declaration (shared with the
    reader) defines it, listed by an explicit os.scandir (a glob returns nothing on an unreadable directory
    and would pass clean) and parsed with ast, never imported. Discovery is flat: only the entries directly
    in tools/ whose names end in '.py'; a subdirectory's contents are out of scope.

    Fail-closed (GateError) on an unlistable directory; on a *.py entry that is not a regular file once
    symlinks are followed (a dangling or looping symlink, a symlink to a directory or other non-file, or a
    directory, FIFO, socket or device named *.py), since a candidate is enumerated by NAME before its type
    is checked and is never skipped; on an unreadable or unparsable *.py file or one declaring a source
    encoding other than UTF-8 (_parse_module); on any binding, read or spelling of RENDERER_DECL the
    walk sees other than ONE single-target top-level Assign or AnnAssign to the bare name (_declaration),
    each of which could hide a declaration or change it in place; and on a RECOGNIZED module-namespace
    access (a subscript of a bare vars() or globals() call in any context, or a setattr or delattr call
    on a sys.modules entry) whose key the bounded fold cannot reduce, call-free, to a non-reserved
    constant string, since a computed key can spell the reserved name at run time. A symlink to a regular file is read
    through. RENDERER_DECL is a RESERVED name: the walk is conservative and also refuses an unrelated
    function-local reuse of it, or the string anywhere but a comparison or a third or later call argument,
    which fails in the safe direction.

    DISCLOSED RESIDUAL (disclose-guard-residuals): the walk sees the name only where the source spells it, in
    a string-valued or list-of-string AST field (an identifier, an attribute, an import name, a
    keyword-argument name, a pattern or parameter name, or any such field a later Python adds), or as a
    string the bounded fold of _folded_strings computes (a str or bytes constant, bytes decoded as
    latin-1, an f-string of str-valued parts, a same-kind + chain, and a supported decode of folded
    bytes), where both a fold equal to the name
    and a fold whose dot- or colon-separated components include it (a pkgutil.resolve_name or
    operator.attrgetter path) are refused. What remains is beyond this static scan, which does not detect
    it and does not claim to: a SPELLED name or path framed by any resolver syntax other than dot- or
    colon-separated components, such as a str.format or string.Formatter replacement-field path, by
    bracket field ('0[RENDERER_DECL]') or by a brace-framed attribute dot ('{0.RENDERER_DECL}', where the
    braces keep the dot-component rule from matching): string.Formatter().format resolves the brace-framed
    template and reaches the declared value, and that brace-framed format route is the one that passes,
    while the unbraced get_field spelling of the same field ('0.RENDERER_DECL') is a plain dot-separated
    path the dot-component rule refuses; or pickle opcode text (a GLOBAL frame is newline-separated and
    a STACK_GLOBAL frame length-prefixed, so a spelled pickle reaches a module attribute with no dot or
    colon in sight);
    a computed name or key consumed by any route outside the two RECOGNIZED module-namespace forms: the
    rule is that a subscript of a bare vars() or globals() call and the name argument of a bare setattr
    or delattr on a sys.modules entry accept only a key the bounded fold reduces, call-free, to a
    non-reserved constant string, at the stated cost of over-reporting a benign computed (or
    longer-than-_FOLD_CAP) key there. A name the fold DOES read, one assembled through the supported
    decode forms included (a no-keyword <folded bytes>.decode() with no argument or with one
    ASCII-compatible codec literal, and a no-keyword two-argument str(<folded bytes>, <ASCII-compatible
    codec literal>)), is held to the same exact-name rule as a spelled name, whatever route consumes it,
    and the two exact-name EXEMPT USES below apply to a decode-derived string too. Each exemption is
    NODE-LOCAL: it accepts only the outer value that sits directly in the exempt position (a comparison
    operand, or a third or later positional argument with no starred argument before it), and every
    descendant of that value is still checked on its own, in its own position. So the comparison operand
    (('RENDERER_' + b'DECL'.decode()) == 'x') and the third argument of
    f(None, None, 'RENDERER_' + b'DECL'.decode()) are accepted, since no part of either folds to the
    name; but bytes a decode reads, an operand of an inner + and an f-string part are never in an exempt
    position, so such a descendant equal to the name is refused whatever the outer value's position:
    the decode receiver in b'RENDERER_DECL'.decode() == 'x' and in f(None, None,
    b'RENDERER_DECL'.decode()), and the first argument of str in f(None, None,
    str(b'RENDERER_DECL', 'ascii')), are each refused. The dot- and colon-component rule has no
    exemption, so a fold whose components include the name is refused in those positions too. The decode
    fold is REFUSAL-ONLY input and never accepts a key. What stays unseen is a name
    computed by a route the fold
    does not read (a join, %-formatting, str.format, a case change, chr, a decode of computed bytes,
    codecs.decode, an unbound bytes.decode(...) call, a computed or keyword codec, an errors argument, a
    one- or three-argument str(...), a codec that fails the per-byte ASCII probe, or any fold longer
    than _FOLD_CAP) and handed to a resolver (pkgutil.resolve_name, operator.attrgetter, importlib), to
    a bound update, setdefault or __setitem__, to a qualified or aliased setattr, or to a namespace
    reached through an imported name or alias; the
    name, spelled or folded (a decode-derived fold included), consumed through one of the two EXEMPT
    USES, by a comparison operand
    (ANY overloaded comparison method receives the spelled name and can store or read through it; __eq__,
    __ne__, __lt__ and __contains__ are examples, never a closed list) or
    by a helper taking it as a third or later positional argument that then stores or reads, such as
    functools.partial(setattr, module, 'RENDERER_DECL'); a bulk copy that never
    names it, such as globals().update(vars(other)); exec, eval or compile of source text; an import-time
    side effect of another module; a module-level __getattr__ that answers the name without binding it; and
    an in-place change to the declared dict reached without spelling the name (by iterating
    globals().values(), say), which would make the value Python runs differ from the value read."""
    try:
        with os.scandir(tools_dir) as it:
            names = sorted(e.name for e in it if e.name.endswith(".py"))
    except OSError as exc:
        raise GateError("tools/: cannot list for RENDERER_DECL ({})".format(exc))
    stems = []
    for name in names:
        where = "tools/" + name
        path = tools_dir / name
        try:
            mode = os.stat(path).st_mode  # follows a symlink: a dangling or looping link raises here
        except OSError as exc:
            raise GateError("{}: cannot resolve this *.py entry to scan it for RENDERER_DECL (a dangling "
                            "or looping symlink?) ({})".format(where, exc))
        if not stat.S_ISREG(mode):
            raise GateError("{}: this *.py entry is not a regular file (or a symlink to one), so it cannot "
                            "be scanned for RENDERER_DECL and is refused rather than skipped".format(where))
        if _declaration(_parse_module(path, where, "RENDERER_DECL"), where):
            stems.append(name[:-3])
    return stems


def _local_imports(path, tools_dir, where):
    """The set of pack-local module stems (a tools/<stem>.py exists) directly imported by `path`. Parses
    Import/ImportFrom nodes only. Fail-closed (GateError) on a relative import (level > 0) or a wildcard
    `from <pack-local> import *`, both of which a static closure cannot resolve honestly."""
    tree = _parse_module(path, where, "its imports")
    stems = set()

    def _consider(root):
        if (tools_dir / (root + ".py")).is_file():
            stems.add(root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _consider(alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise GateError("{}: a relative import cannot be resolved into the pack-local closure "
                                "(fail-closed)".format(where))
            root = (node.module or "").partition(".")[0]
            is_wildcard = any(alias.name == "*" for alias in node.names)
            if is_wildcard and (tools_dir / (root + ".py")).is_file():
                raise GateError("{}: a wildcard import of the pack-local module {!r} cannot be resolved "
                                "into the closure (fail-closed)".format(where, root))
            if root:
                _consider(root)
    return stems


def compute_closure(entry_stem, tools_dir):
    """The ordered pack-local import closure of tools/<entry_stem>.py: the entrypoint first, then every
    transitively-imported tools/<module>.py bytewise by repo-relative path. Returns a list of repo-relative
    paths ('tools/<stem>.py'). Fail-closed on an unresolvable import or unreadable member."""
    entry_rel = "tools/{}.py".format(entry_stem)
    seen = {entry_stem}
    frontier = [entry_stem]
    while frontier:
        stem = frontier.pop()
        deps = _local_imports(tools_dir / (stem + ".py"), tools_dir, "tools/{}.py".format(stem))
        for dep in sorted(deps):
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    rest = sorted("tools/{}.py".format(s) for s in seen if s != entry_stem)
    return [entry_rel] + rest


def framed_code_digest(closure, root):
    """SHA-256 over the concatenated framed records '<path>\\t<bytes>\\t<sha256>\\n' per closure file in
    order (never a raw byte concatenation), so a per-file change or a cross-boundary byte move both change
    the digest. Fail-closed on an unreadable member."""
    records = []
    for rel in closure:
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            raise GateError("cannot read closure member {} ({})".format(rel, exc))
        records.append("{}\t{}\t{}\n".format(rel, len(data), _sha256(data)))
    return _sha256("".join(records).encode("utf-8"))


def build_rows(root):
    """One row per declared renderer: renderer-id, entrypoint, semantics-revision, targets (from the
    generator's GENSRC_OUTPUTS, verbatim including a tree's trailing '/'), the ordered import closure, and
    the framed code-digest. Fail-closed on an incomplete roster (a tools/*.py declares RENDERER_DECL but is
    absent from RENDERERS), any malformed declaration, or an unresolvable import."""
    tools_dir = root / "tools"
    unlisted = [stem for stem in _declared_renderer_stems(tools_dir) if stem not in RENDERERS]
    if unlisted:
        raise GateError("renderer roster incomplete: {} declare(s) RENDERER_DECL but are absent from "
                        "RENDERERS; add each to RENDERERS".format(
                            ", ".join("tools/{}.py".format(s) for s in unlisted)))
    rows = []
    seen_ids = set()
    for stem in RENDERERS:
        entry = tools_dir / (stem + ".py")
        where = "tools/{}.py".format(stem)
        if not entry.is_file():
            raise GateError("{}: declared renderer generator is absent".format(where))
        rid, rev = _read_renderer_decl(entry, where)
        if rid in seen_ids:
            raise GateError("duplicate renderer-id {!r}".format(rid))
        seen_ids.add(rid)
        try:
            decl = gen_gensrc._read_declaration(entry, where)
        except (ValueError, OSError) as exc:
            raise GateError("{}: cannot read GENSRC_OUTPUTS ({})".format(where, exc))
        targets = [e["target"] for e in decl]
        closure = compute_closure(stem, tools_dir)
        rows.append({"renderer-id": rid, "entrypoint": "tools/{}.py".format(stem),
                     "semantics-revision": rev, "targets": targets, "closure": closure,
                     "code-digest": framed_code_digest(closure, root)})
    return rows


def render(rows):
    lines = ["# .aiqt/core/renderers.toml: the RENDERER/GENERATOR DECLARATION (VER-CORE 6.5, R9-5).",
             "# GENERATED by tools/gen_renderers.py from each adapter generator's RENDERER_DECL, its",
             "# GENSRC_OUTPUTS targets, and its ordered pack-local import closure; do not hand-edit.",
             "# code-digest is SHA-256 over framed '<path>\\t<bytes>\\t<sha256>' records in closure order,",
             "# so any edit anywhere in a generator's closure forces a declaration diff (6.5).",
             "",
             "format-version = 1",
             ""]
    for row in rows:
        lines.append("[[renderer]]")
        lines.append("renderer-id = {}".format(_toml_str(row["renderer-id"])))
        lines.append("entrypoint = {}".format(_toml_str(row["entrypoint"])))
        lines.append("semantics-revision = {}".format(row["semantics-revision"]))
        lines.append("targets = [{}]".format(", ".join(_toml_str(t) for t in row["targets"])))
        lines.append("closure = [{}]".format(", ".join(_toml_str(c) for c in row["closure"])))
        lines.append("code-digest = {}".format(_toml_str(row["code-digest"])))
        lines.append("")
    return "\n".join(lines)


def run(root, check):
    try:
        text = render(build_rows(root))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if reconcile(root / RENDERERS_REL, text, check, root):
        print("drift: {} is out of date; run tools/gen_renderers.py".format(RENDERERS_REL),
              file=sys.stderr)
        return 1
    if not check:
        print("wrote {} ({} renderers)".format(RENDERERS_REL, text.count("[[renderer]]")))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    root = repo_root()
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            print("usage: gen_renderers.py [--check] [--root DIR] | --self-test", file=sys.stderr)
            return 2
        root = Path(args[i + 1]).resolve()
    return run(root, "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic generators in a tempdir prove this generator's own fail-closed invariants:
#   (a) a conformant set generates then re-checks drift-clean, and two runs are byte-identical (determinism);
#   (b) a helper edit inside a closure changes the framed code-digest (closure completeness);
#   (c) a mutated renderers.toml is caught by --check (exit 1);
#   (d) a wildcard import of a pack-local module fails closed (exit 2), an unresolvable-closure case;
#   (e) a missing/malformed RENDERER_DECL fails closed (exit 2).
#   (g) a tools/*.py that declares RENDERER_DECL but is absent from RENDERERS fails --check (exit 2), even
#       when renderers.toml is otherwise current (roster completeness, not only freshness).
#   (h) each of the _UNSUPPORTED_BINDINGS forms of RENDERER_DECL in an unlisted tools/*.py (unpacking,
#       starred, conditional, try, with, loop, walrus, augmented, del, import-as, wildcard import, global,
#       nonlocal, except-as, match capture, def, class, parameter, type parameter, comprehension target,
#       type alias, a store through the module object by attribute, vars()/globals() subscript or
#       setattr, a literal-name store through any other call (qualified or aliased setattr, __setattr__,
#       setdefault, __setitem__, dict.__setitem__, operator.setitem), a dict-display or keyword update, a
#       variable holding the name, an f-string, split f-string, a concatenated, spelled-bytes or
#       split-bytes key (folded decoded as latin-1, each reaching the reserved name before any run-time
#       decode), a key assembled by decoding spelled bytes and then concatenating (decoded bytes + str,
#       str + decoded bytes, decoded + decoded, and a two-argument str(bytes, codec): the decode fold
#       reads each, refusal-only, so each reaches the reserved name), a computed name assembled the same
#       way and handed to a bound setdefault (not a recognized namespace form; the exact-name rule
#       refuses it through the fold), an import naming it, an
#       import from it (the module position), an import of a package rooted at it (the dotted module
#       position), a match-mapping rest, a match-star name, and a later argument that a starred argument
#       before it can shift to the key position)
#       fails --check (exit 2) instead of reading as "no declaration".
#   (i) a *.py entry that is a dangling symlink, a looping symlink, a symlink to a directory, a directory,
#       or a FIFO fails --check (exit 2) instead of being skipped.
#   (j) discovery and the reader share one binding predicate: a listed generator declaring RENDERER_DECL by
#       an annotated assignment alone renders that declaration, while an ordinary declaration followed by an
#       annotated (or a second plain) rebinding, or a bare annotation, fails closed (exit 2), and the reader
#       alone also refuses two bindings; each _MUTATED in-place change to the declared dict (a subscript
#       store, an update call, a chained-assignment alias, a module attribute read, a class-pattern keyword
#       read, a pkgutil.resolve_name path string, an operator.attrgetter path string spelled as str, as
#       bytes decoded at run time, split across a bytes + bytes concatenation or assembled from a str
#       piece plus a decoded piece, a subscript key
#       decoded from spelled bytes piecewise and then concatenated, and a setdefault read keyed by a str
#       piece plus a two-argument str(bytes, codec) piece) also fails closed, so
#       none of these routes can make the value Python runs differ from the value read.
#   (k) a PEP 263 cookie cannot hide a declaration: a UTF-7 cookie that makes a UTF-8 comment a declaration,
#       and any other non-UTF-8 cookie, fail --check (exit 2); a UTF-8 BOM is parsed as Python parses it.
#   (l) a parser or compiler stack overflow (RecursionError or MemoryError) while parsing a tools/*.py, or
#       while evaluating a RENDERER_DECL literal, fails with exit 2 (GateError), never a traceback. The
#       overflow is INJECTED at the gate's ast.parse and ast.literal_eval calls rather than provoked by a
#       deeply nested body: the depth at which CPython overflows is an interpreter implementation limit
#       (parser stack, C stack, recursion checks) that changes between patch releases, so a nesting body
#       that overflows on one interpreter parses cleanly on another and the case would flip.
#   (m) the refusal names where a binder runs: a walrus in a decorator, a default or a class base runs in
#       the enclosing scope, so it is not reported as inside the def or class; one in a body is.
#   (n) an unreadable *.py is reported as "cannot read it to scan for", an unparsable one as "cannot parse
#       it to scan for"; the FULL prefix is asserted, so an older or shorter wording fails the case.
#   (o) a comparison against the string RENDERER_DECL, a third positional argument (where no direct namespace
#       write takes its key), a longer string mentioning it, an f-string whose !r or !a conversion or
#       format spec renders a different string at run time, a bytes-valued replacement field (bare, with
#       !s, or a bytes + bytes concatenation: each renders b'...' at run time) or a mixed-kind + in
#       either order (str + bytes and bytes + str, each a run-time TypeError) stays allowed; the fold
#       must not over-match any of them. A name assembled through a supported decode stays allowed as a
#       comparison operand and as a third positional argument when no part of it folds to the name.
#   (p) the bounded string fold keeps no value longer than _FOLD_CAP.
#   (q) this file passes its own scan (a renderer-free tools/*.py that discovery reads like any other).
#   (r) a RECOGNIZED module-namespace access (a vars() or globals() subscript in any context, del
#       included, or a setattr or delattr call on a sys.modules entry) is accepted only when its key
#       folds, call-free, to a non-reserved constant string: a key built from a method call, a
#       conversion builtin, a call whose meaning depends on a local rebinding, or an f-string holding a
#       call is reported with the unfoldable-key text, a computed delattr name and a computed del of a
#       globals() or vars() subscript are reported the same way, a locally rebound call carried into a
#       subscript key through either side of a + or through an f-string, or used as a setattr or delattr
#       name, is reported with the matching text, while a constant non-reserved key, a
#       folded non-reserved concatenation and a constant-name setattr on a sys.modules entry stay
#       accepted.
#   (s) each exemption is node-local: a supported decode of bytes that already spell the name, as a
#       comparison operand or a third positional argument, and a two-argument str of such bytes as a
#       third positional argument, are each reported with the exact-name text, because the inner bytes
#       sit in a position that is not exempt.
#   (t) a self-test that cannot create its temporary directory exits 2 and leaves RENDERERS the same
#       object it found, so an in-process caller never inherits the self-test roster.

_HELPER = "VALUE = 1\n"

_ENTRY_GOOD = ('import sys\n'
               'from pathlib import Path\n'
               'from selfhelper import VALUE\n'
               'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
               'GENSRC_OUTPUTS = (\n'
               '    {"target": "OUT.md", "kind": "file",\n'
               '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
               ')\n')

_ENTRY_NODECL = ('from selfhelper import VALUE\n'
                 'GENSRC_OUTPUTS = (\n'
                 '    {"target": "OUT.md", "kind": "file",\n'
                 '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                 ')\n')

_ENTRY_WILDCARD = ('from selfhelper import *\n'
                   'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
                   'GENSRC_OUTPUTS = (\n'
                   '    {"target": "OUT.md", "kind": "file",\n'
                   '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                   ')\n')

_ENTRY_BADID = ('from selfhelper import VALUE\n'
                'RENDERER_DECL = {"renderer-id": "Bad ID", "semantics-revision": 1}\n'
                'GENSRC_OUTPUTS = (\n'
                '    {"target": "OUT.md", "kind": "file",\n'
                '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                ')\n')


_ENTRY_UNLISTED = ('RENDERER_DECL = {"renderer-id": "beta", "semantics-revision": 1}\n'
                   'GENSRC_OUTPUTS = (\n'
                   '    {"target": "BETA.md", "kind": "file",\n'
                   '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_beta.py"},\n'
                   ')\n')


_DECL_LITERAL = '{"renderer-id": "beta", "semantics-revision": 1}'
# (h) one unlisted tools/gen_beta.py body per unsupported static binding form; each must fail closed.
_UNSUPPORTED_BINDINGS = (
    ("tuple unpacking", "RENDERER_DECL, = ({},)\n".format(_DECL_LITERAL)),
    ("list unpacking", "[RENDERER_DECL] = [{}]\n".format(_DECL_LITERAL)),
    ("starred unpacking", "*RENDERER_DECL, = ({},)\n".format(_DECL_LITERAL)),
    ("conditional", "if True:\n    RENDERER_DECL = {}\n".format(_DECL_LITERAL)),
    ("try block", "try:\n    RENDERER_DECL = {}\nexcept Exception:\n    pass\n".format(_DECL_LITERAL)),
    ("with block", "import contextlib\nwith contextlib.nullcontext({}) as RENDERER_DECL:\n    pass\n"
                   .format(_DECL_LITERAL)),
    ("for loop", "for RENDERER_DECL in ({},):\n    pass\n".format(_DECL_LITERAL)),
    ("assignment expression", "(RENDERER_DECL := {})\n".format(_DECL_LITERAL)),
    ("augmented assignment", "RENDERER_DECL |= {}\n".format(_DECL_LITERAL)),
    ("del", "del RENDERER_DECL\n"),
    ("import-as", "from selfhelper import VALUE as RENDERER_DECL\n"),
    ("wildcard import", "from selfhelper import *\n"),
    ("global statement", "def f():\n    global RENDERER_DECL\n    RENDERER_DECL = {}\n"
                         .format(_DECL_LITERAL)),
    ("nonlocal statement", "def f():\n    RENDERER_DECL = 0\n    def g():\n        nonlocal RENDERER_DECL\n"
                           "    return g\n"),
    ("except-as", "try:\n    pass\nexcept Exception as RENDERER_DECL:\n    pass\n"),
    ("match capture", "match {}:\n    case RENDERER_DECL:\n        pass\n".format(_DECL_LITERAL)),
    ("def", "def RENDERER_DECL():\n    pass\n"),
    ("class", "class RENDERER_DECL:\n    pass\n"),
    ("parameter", "def f(RENDERER_DECL):\n    pass\n"),
    ("type parameter", "def f[RENDERER_DECL]():\n    pass\n"),
    ("comprehension target", "[0 for RENDERER_DECL in ({},)]\n".format(_DECL_LITERAL)),
    ("type alias", "type RENDERER_DECL = dict\n"),
    ("sys.modules attribute", "import sys\nsys.modules[__name__].RENDERER_DECL = {}\n".format(_DECL_LITERAL)),
    ("vars() subscript", "vars()['RENDERER_DECL'] = {}\n".format(_DECL_LITERAL)),
    ("globals() subscript", "globals()['RENDERER_DECL'] = {}\n".format(_DECL_LITERAL)),
    ("setattr call", "import sys\nsetattr(sys.modules[__name__], 'RENDERER_DECL', {})\n"
                     .format(_DECL_LITERAL)),
    ("qualified setattr call", "import builtins, sys\n"
                               "builtins.setattr(sys.modules[__name__], 'RENDERER_DECL', {})\n"
                               .format(_DECL_LITERAL)),
    ("aliased setattr call", "import sys\nfrom builtins import setattr as put\n"
                             "put(sys.modules[__name__], 'RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("__setattr__ call", "import sys\nsys.modules[__name__].__setattr__('RENDERER_DECL', {})\n"
                         .format(_DECL_LITERAL)),
    ("setdefault call", "globals().setdefault('RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("__setitem__ call", "globals().__setitem__('RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("dict.__setitem__ call", "dict.__setitem__(globals(), 'RENDERER_DECL', {})\n".format(_DECL_LITERAL)),
    ("operator.setitem call", "import operator\noperator.setitem(globals(), 'RENDERER_DECL', {})\n"
                              .format(_DECL_LITERAL)),
    ("keyword update", "globals().update(RENDERER_DECL={})\n".format(_DECL_LITERAL)),
    ("name held in a variable", "key = 'RENDERER_DECL'\nglobals()[key] = {}\n".format(_DECL_LITERAL)),
    ("dict-display update", "globals().update({{('RENDERER_DECL'): {}}})\n".format(_DECL_LITERAL)),
    ("f-string subscript key", "vars()[f'RENDERER_DECL'] = {}\n".format(_DECL_LITERAL)),
    ("split f-string subscript key", "vars()[f'RENDERER_{{({!r})}}'] = {}\n".format("DECL", _DECL_LITERAL)),
    ("concatenated subscript key", "globals()['RENDERER_' + 'DECL'] = {}\n".format(_DECL_LITERAL)),
    ("bytes subscript key", "vars()[b'RENDERER_DECL'.decode()] = {}\n".format(_DECL_LITERAL)),
    ("split-bytes subscript key", "vars()[(b'RENDERER_' + b'DECL').decode()] = {}\n".format(_DECL_LITERAL)),
    ("decoded-bytes-plus-str subscript key",
     "vars()[b'RENDERER_'.decode() + 'DECL'] = {}\n".format(_DECL_LITERAL)),
    ("str-plus-decoded-bytes subscript key",
     "vars()['RENDERER_' + b'DECL'.decode()] = {}\n".format(_DECL_LITERAL)),
    ("decoded-plus-decoded subscript key",
     "vars()[b'RENDERER_'.decode() + b'DECL'.decode()] = {}\n".format(_DECL_LITERAL)),
    ("two-argument-str subscript key",
     "vars()['RENDERER_' + str(b'DECL', 'ascii')] = {}\n".format(_DECL_LITERAL)),
    ("setdefault keyed by a decoded concatenation",
     "globals().setdefault('RENDERER_' + b'DECL'.decode(), {})\n"),
    ("import naming it", "from gen_alpha import RENDERER_DECL as other\nother['semantics-revision'] = 99\n"),
    ("import from it", "from RENDERER_DECL import x\n"),
    ("dotted import root", "import RENDERER_DECL.sub\n"),
    ("match mapping rest", "match {}:\n    case {{**RENDERER_DECL}}:\n        pass\n".format(_DECL_LITERAL)),
    ("match star", "match [{}]:\n    case [*RENDERER_DECL]:\n        pass\n".format(_DECL_LITERAL)),
    ("later argument after a starred one", "import sys\n"
                                           "setattr(*[], *[sys.modules[__name__]], 'RENDERER_DECL', {})\n"
                                           .format(_DECL_LITERAL)),
)

# (j) listed tools/gen_alpha.py bodies: the shared declaration predicate (one Assign or AnnAssign, no
# more); each refused body carries the diagnostic its refusal must name.
_ALPHA_DECL = 'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
_ENTRY_ANNOTATED = _ENTRY_GOOD.replace(_ALPHA_DECL, _ALPHA_DECL.replace(" = ", ": dict = ", 1))
_REBOUND = (
    ("annotated rebinding", _ENTRY_GOOD + 'RENDERER_DECL: dict = {"renderer-id": "rebound", '
                                          '"semantics-revision": 99}\n',
     "bound 2 times at top level"),
    ("plain rebinding", _ENTRY_GOOD + 'RENDERER_DECL = {"renderer-id": "rebound", '
                                      '"semantics-revision": 99}\n',
     "bound 2 times at top level"),
    ("bare annotation", _ENTRY_GOOD.replace(
        _ALPHA_DECL,
        "# a bare annotation binds no value, so the reader must refuse it\nRENDERER_DECL: dict\n"),
     "annotated but never assigned a value"),
)
# (j) listed tools/gen_alpha.py bodies that change the declared dict in place after it is defined, so the value
# Python runs would differ from the value read; each must fail closed.
_MUTATED = (
    ("a subscript store", _ENTRY_GOOD + 'RENDERER_DECL["semantics-revision"] = 99\n'),
    ("an update call", _ENTRY_GOOD
     + 'RENDERER_DECL.update([("semantics-revision", 99)])  # an in-place update\n'),
    ("a chained-assignment alias", _ENTRY_GOOD.replace(_ALPHA_DECL, "ALIAS = " + _ALPHA_DECL)
     + 'ALIAS["semantics-revision"] = 99\n'),
    ("a module attribute read", _ENTRY_GOOD
     + 'sys.modules[__name__].RENDERER_DECL["semantics-revision"] = 99\n'),
    ("a class-pattern keyword read", _ENTRY_GOOD
     + 'match sys.modules[__name__]:\n'
       '    case object(RENDERER_DECL=alias):\n'
       '        alias["semantics-revision"] = 99\n'),
    ("a pkgutil.resolve_name path string", _ENTRY_GOOD
     + "import pkgutil\n"
       "pkgutil.resolve_name(__name__ + ':RENDERER_DECL')['semantics-revision'] = 99\n"),
    ("an operator.attrgetter path string", _ENTRY_GOOD
     + "import operator, types\n"
       "operator.attrgetter('m.RENDERER_DECL')(types.SimpleNamespace(m=sys.modules[__name__]))"
       "['semantics-revision'] = 99\n"),
    ("a bytes operator.attrgetter path", _ENTRY_GOOD
     + "import operator, types\n"
       "operator.attrgetter(b'm.RENDERER_DECL'.decode())"
       "(types.SimpleNamespace(m=sys.modules[__name__]))['semantics-revision'] = 99\n"),
    ("a split-bytes operator.attrgetter path", _ENTRY_GOOD
     + "import operator, types\n"
       "operator.attrgetter((b'm.RENDERER_' + b'DECL').decode())"
       "(types.SimpleNamespace(m=sys.modules[__name__]))['semantics-revision'] = 99\n"),
    ("a decode-then-concatenate subscript key", _ENTRY_GOOD
     + "vars()[b'RENDERER_'.decode() + b'DECL'.decode()]['semantics-revision'] = 99\n"),
    ("a decoded operator.attrgetter concatenation", _ENTRY_GOOD
     + "import operator, types\n"
       "operator.attrgetter('m.RENDERER_' + b'DECL'.decode())"
       "(types.SimpleNamespace(m=sys.modules[__name__]))['semantics-revision'] = 99\n"),
    ("a setdefault read keyed by a decoded concatenation", _ENTRY_GOOD
     + "globals().setdefault('RENDERER_' + str(b'DECL', 'ascii'), None)['semantics-revision'] = 99\n"),
)

# (k) unlisted tools/gen_beta.py bodies (bytes) a coding cookie must not hide; '+AAo-' is UTF-7 for a newline.
_COOKIES = (
    ("UTF-7 cookie hiding a declaration",
     "# -*- coding: utf-7 -*-\n# +AAo-RENDERER_DECL = {}\n".format(_DECL_LITERAL).encode("ascii")),
    ("latin-1 cookie", b"# -*- coding: latin-1 -*-\nVALUE = 1\n"),
)
_BOM_DECL = b"\xef\xbb\xbf" + "RENDERER_DECL = {}\n".format(_DECL_LITERAL).encode("ascii")

# (l) (label, the ast attribute the overflow is injected at, the exception class). The parse injection fires
# only for tools/zz_deep.py, an unlisted file; the literal_eval injection fires on the first literal the gate
# evaluates, the listed tools/gen_alpha.py RENDERER_DECL.
_DEEP = (("parser RecursionError", "parse", RecursionError),
         ("parser MemoryError", "parse", MemoryError),
         ("literal RecursionError", "literal_eval", RecursionError),
         ("literal MemoryError", "literal_eval", MemoryError))
_DEEP_FILE = "tools/zz_deep.py"

# (m) (source, the expected _unsupported_binding description).
_WALRUS = "an assignment expression (walrus)"
_WHERE_MESSAGES = (
    ("@(RENDERER_DECL := (lambda f: f))\ndef f():\n    pass\n", _WALRUS),
    ("def f(x=(RENDERER_DECL := 1)):\n    pass\n", _WALRUS),
    ("class C((RENDERER_DECL := object)):\n    pass\n", _WALRUS),
    ("def f():\n    (RENDERER_DECL := 1)\n", _WALRUS + " inside a def block"),
    ("def g():\n    def f(x=(RENDERER_DECL := 1)):\n        pass\n", _WALRUS + " inside a def block"),
)

# (o) unlisted tools/gen_beta.py bodies whose only use of the name is one of the two exempt uses (allowed
# uses, NOT proof of inertness) or a string the fold must not over-match: a comparison, a longer-string
# mention, a third or later positional argument, three f-strings (a !r conversion, a !a conversion and a
# format spec each render a DIFFERENT string at run time), three bytes-valued replacement fields (bare,
# with !s, and a bytes + bytes concatenation: each renders b'...' at run time, never the decoded text)
# and a mixed-kind + in each order (str + bytes and bytes + str, each a run-time TypeError, so neither
# produces the name at all; folding any of these as if it were the plain value would refuse a harmless
# mention), a one-argument str(bytes) concatenation (str(b'DECL') renders "b'DECL'", never the decoded
# text, so the decode fold never reads a one-argument str) and a non-ASCII-compatible decode
# concatenation (b'DECL'.decode('utf-16') produces a DIFFERENT string than the latin-1 reading; folding
# it by reusing the latin-1 value would refuse a string the run time never produces); each must stay
# allowed, and each bytes control goes red if the bytes fold is extended back into replacement fields,
# mixed +, one-argument str or a non-ASCII-compatible codec. The last two put a name assembled through a
# supported decode in each exempt position, where no part of it folds to the name on its own (the
# exemption is node-local, case (s)); each goes red under a scan that refuses every call-derived fold in
# an exempt position.
_ALLOWED_USES = (
    ("comparison", "def is_decl(name):\n    return name == 'RENDERER_DECL' or 'RENDERER_DECL' != name\n"),
    ("mention in a longer string", "NOTE = 'this module carries no RENDERER_DECL'\n"),
    ("third positional argument", "def scan(path, where, purpose):\n    return purpose\n"
                                  "scan(None, None, 'RENDERER_DECL')\n"),
    ("f-string !r mention", "NOTE = f\"RENDERER_{'DECL'!r}\"\n"),
    ("f-string !a mention", "NOTE = f\"RENDERER_{'DECL'!a}\"\n"),
    ("f-string format-spec mention", "NOTE = f\"RENDERER_{'DECL':.2}\"\n"),
    ("f-string bytes replacement field", "NOTE = f\"RENDERER_{b'DECL'}\"\n"),
    ("f-string bytes !s replacement field", "NOTE = f\"RENDERER_{b'DECL'!s}\"\n"),
    ("f-string concatenated-bytes replacement field", "NOTE = f\"RENDERER_{b'DE' + b'CL'}\"\n"),
    ("str-plus-bytes concatenation", "NOTE = 'RENDERER_' + b'DECL'\n"),
    ("bytes-plus-str concatenation", "NOTE = b'RENDERER_' + 'DECL'\n"),
    ("one-argument str of bytes concatenation", "NOTE = 'RENDERER_' + str(b'DECL')\n"),
    ("non-ASCII-compatible decode concatenation", "NOTE = 'RENDERER_' + b'DECL'.decode('utf-16')\n"),
    ("decode-derived comparison operand", "NOTE = ('RENDERER_' + b'DECL'.decode()) == 'x'\n"),
    ("decode-derived third positional argument", "def f(a, b, c):\n    return c\n"
                                                 "f(None, None, 'RENDERER_' + b'DECL'.decode())\n"),
)

# (r) unlisted tools/gen_beta.py bodies keying a module namespace with something the fold cannot reduce,
# call-free, to a constant string; each must be reported with the unfoldable-key text, and each goes red
# when its own guard is weakened ('renderer_decl'.upper() really computes the reserved name at run time;
# the rebound str makes even the supported decode fold of str(<bytes>, <codec>) mean whatever the
# rebinding says, so a call-derived fold never ACCEPTS a key, and that fixture goes red if acceptance
# reads the full fold instead of the call-free one; the computed delattr name goes red when delattr is
# dropped from the call check; each computed del goes red when ast.Del is carved out of the subscript
# rule). The five rebound-call fixtures put that rebound call where the fold must CARRY its call taint
# (_REBOUND_STR makes it compute the reserved name at run time): the keys on the left and on the right
# of a + go red when the + step stops propagating the taint, the key inside an f-string replacement
# field goes red when either the replacement-field step or the f-string step does, and the setattr and
# delattr names go red when that call leg reads the full fold for acceptance instead of the call-free one.
_SUBSCRIPT_KEY_TEXT = ("a vars() or globals() subscript whose key the bounded fold cannot reduce, "
                       "call-free, to a constant string")
_SETATTR_KEY_TEXT = ("a setattr call on a sys.modules entry whose attribute name the bounded fold "
                     "cannot reduce, call-free, to a constant string")
_DELATTR_KEY_TEXT = ("a delattr call on a sys.modules entry whose attribute name the bounded fold "
                     "cannot reduce, call-free, to a constant string")
# A str rebound to a function that returns the reserved name: str(b'x', 'ascii') then computes
# RENDERER_DECL at run time, while the supported decode fold reads the same call as 'x'.
_REBOUND_STR = "str = lambda *a: '_'.join(('RENDERER', 'DECL'))\n"
_UNFOLDABLE_KEYS = (
    ("method-call key", "vars()['renderer_decl'.upper()] = {}\n".format(_DECL_LITERAL),
     _SUBSCRIPT_KEY_TEXT),
    ("conversion-builtin key", "globals()[str(123)] = {}\n".format(_DECL_LITERAL), _SUBSCRIPT_KEY_TEXT),
    ("locally-rebound-call key", "from selfhelper import VALUE as str\n"
                                 "vars()[str(b'x', 'ascii')] = {}\n".format(_DECL_LITERAL),
     _SUBSCRIPT_KEY_TEXT),
    ("f-string-holding-a-call key",
     "globals()[f\"{{'renderer_decl'.upper()}}\"] = {}\n".format(_DECL_LITERAL), _SUBSCRIPT_KEY_TEXT),
    ("setattr computed name", "import sys\nsetattr(sys.modules[__name__], 'renderer_decl'.upper(), {})\n"
                              .format(_DECL_LITERAL), _SETATTR_KEY_TEXT),
    ("delattr computed name", "import sys\ndelattr(sys.modules[__name__], 'renderer_decl'.upper())\n",
     _DELATTR_KEY_TEXT),
    ("del of a computed globals() key", "del globals()['renderer_decl'.upper()]\n", _SUBSCRIPT_KEY_TEXT),
    ("del of a computed vars() key", "del vars()['renderer_decl'.upper()]\n", _SUBSCRIPT_KEY_TEXT),
    ("rebound-call key left of a +",
     _REBOUND_STR + "globals()[str(b'x', 'ascii') + ''] = {}\n".format(_DECL_LITERAL), _SUBSCRIPT_KEY_TEXT),
    ("rebound-call key right of a +",
     _REBOUND_STR + "globals()['' + str(b'x', 'ascii')] = {}\n".format(_DECL_LITERAL), _SUBSCRIPT_KEY_TEXT),
    ("rebound-call key in an f-string",
     _REBOUND_STR + "globals()[f\"{{str(b'x', 'ascii')}}\"] = {}\n".format(_DECL_LITERAL),
     _SUBSCRIPT_KEY_TEXT),
    ("rebound-call setattr name",
     _REBOUND_STR + "import sys\nsetattr(sys.modules[__name__], str(b'x', 'ascii'), {})\n"
     .format(_DECL_LITERAL), _SETATTR_KEY_TEXT),
    ("rebound-call delattr name",
     _REBOUND_STR + "import sys\ndelattr(sys.modules[__name__], str(b'x', 'ascii'))\n", _DELATTR_KEY_TEXT),
)
# (r) allowed controls: a key the fold reduces, call-free, to a non-reserved constant string stays
# accepted, on the subscript leg and the setattr leg both (the setattr control goes red under a mutant
# that refuses every setattr on a sys.modules entry whatever its name folds to).
_FOLDED_KEYS = (
    ("constant non-reserved key", "globals()['BETA_FLAG'] = {}\n".format(_DECL_LITERAL)),
    ("concatenated non-reserved key", "vars()['BETA_' + 'FLAG'] = {}\n".format(_DECL_LITERAL)),
    ("constant sys.modules setattr", "import sys\nsetattr(sys.modules[__name__], 'BETA_FLAG', 1)\n"),
)

# (s) unlisted tools/gen_beta.py bodies whose outer value sits in an exempt position while a DESCENDANT
# folds to the reserved name in a position that is not exempt (the receiver of a decode, the first
# argument of str): each exemption is node-local, so each must be reported with the exact-name text, and
# each goes red under a scan that extends an exemption from an exempt value to its descendants.
_EXACT_NAME_TEXT = "the string 'RENDERER_DECL' outside a comparison or a later call argument"
_NESTED_NAMES = (
    ("spelled-bytes decode as a comparison operand", "NOTE = b'RENDERER_DECL'.decode() == 'x'\n"),
    ("spelled-bytes decode as a third positional argument",
     "def f(a, b, c):\n    return c\nf(None, None, b'RENDERER_DECL'.decode())\n"),
    ("two-argument str of spelled bytes as a third positional argument",
     "def f(a, b, c):\n    return c\nf(None, None, str(b'RENDERER_DECL', 'ascii'))\n"),
)


def _fixture(base, entry_body, helper_body=_HELPER):
    tools = base / "tools"
    tools.mkdir(parents=True)
    (base / ".aiqt" / "core").mkdir(parents=True)
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (tools / "gen_alpha.py").write_text(entry_body, encoding="utf-8")
    (tools / "selfhelper.py").write_text(helper_body, encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    global RENDERERS

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    def run_refused(root, check):
        # The exit code AND the stderr text, for a vector that must be refused FOR ITS OWN REASON: asserting
        # only exit 2 would keep a vector green when it is refused for an unrelated reason (an unreadable or
        # unparsable fixture, say) while the guard under test no longer sees it.
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                rc = run(root, check)
            except SystemExit as exc:
                rc = "raised SystemExit({!r})".format(exc.code)
        return rc, err.getvalue()

    def run_nested():
        # A nested self-test run (case (t)): its exit code AND its stderr text, so an exit 2 for an
        # unrelated reason cannot pass for the temporary-directory refusal.
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = self_test_main()
        return rc, err.getvalue()

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-renderers-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    # RENDERERS is replaced only once the temporary directory exists, so the early return above leaves an
    # in-process caller's roster in place and every later exit passes through the restoring finally (t).
    saved = RENDERERS
    RENDERERS = ("gen_alpha",)
    failures = []
    try:
        # (a) conformant + determinism. The second generation must SUCCEED before its output is compared,
        # and the comparison is over BYTES (read_text would normalize line endings, so an output whose
        # only drift is CRLF endings would pass as text-equal): were its return ignored, a failed second
        # run that writes nothing would compare the first output with itself, so a nondeterminism defect
        # could escape whenever regeneration fails. The two injection controls exercise the
        # _determinism_failure HELPER itself, not its call site (a check rerouted around the helper would
        # leave them green): one proves a reported failure is recorded, never a silent pass; one proves a
        # generation that returns 0 after changing only the line endings fails the byte comparison.
        good = tmp / "good"
        _fixture(good, _ENTRY_GOOD)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant: regeneration expected drift-clean exit 0")
        first = (good / RENDERERS_REL).read_bytes()
        # .aiqt/ above renderers.toml symlinked outside the root fails closed (exit 2) in --check and write mode
        # with the file there untouched; symlinked inside the root, it is read and the file rewritten there.
        failures.extend(linked_dir_failures(tmp, "aiqt", lambda base: _fixture(base, _ENTRY_GOOD), run, ".aiqt",
                                            RENDERERS_REL))

        def _determinism_failure(generate):
            rc = generate(good, False)
            if rc != 0:
                return "determinism: second generation expected exit 0, got {}".format(rc)
            if (good / RENDERERS_REL).read_bytes() != first:
                return "determinism: two runs are not byte-identical"
            return None

        problem = _determinism_failure(run_quiet)
        if problem is not None:
            failures.append(problem)
        if _determinism_failure(lambda root, check: 2) != \
                "determinism: second generation expected exit 0, got 2":
            failures.append("determinism control: an injected failing second generation (no write) expected "
                            "a recorded failure, not a comparison of the first output with itself")

        def _crlf_generate(root, check):
            # a successful generation whose output then differs only in line endings: rc 0, bytes drifted
            rc = run_quiet(root, check)
            target = root / RENDERERS_REL
            target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
            return rc

        if _determinism_failure(_crlf_generate) != "determinism: two runs are not byte-identical":
            failures.append("determinism control: a second generation returning 0 after changing only the "
                            "line endings expected the byte-identity failure (a text comparison would "
                            "normalize the endings away)")
        (good / RENDERERS_REL).write_bytes(first)

        # (b) a helper edit inside the closure changes the framed code-digest, compared over BYTES like
        # (a). The generation must SUCCEED before its output is compared (the same shape as (a) and (c));
        # the two injection controls exercise the _closure_failure HELPER itself, not its call site: one
        # proves that output written by a run that then reports failure is never accepted, and one proves
        # that a generation returning 0 after writing the UNEDITED output back over the edited one fails the
        # unchanged-code-digest comparison (reverting that comparison to read_text would compare str
        # against bytes and pass vacuously; this control is what turns that revert red).
        edited = tmp / "edited"
        _fixture(edited, _ENTRY_GOOD, helper_body="VALUE = 2\n")

        # This control keeps its OWN bytes baseline rather than sharing (a)'s first: were first shared
        # here, reverting the determinism section to text (its baseline, comparison and restoration
        # together) would turn this comparison and the injected writer below into a str/bytes TypeError
        # that aborts the run before the collected CRLF failure is printed, instead of leaving the CRLF
        # line-ending control as the one that goes red.
        unedited = (good / RENDERERS_REL).read_bytes()

        def _closure_failure(generate):
            rc = generate(edited, False)
            if rc != 0:
                return "closure completeness: generation expected exit 0, got {}".format(rc)
            if (edited / RENDERERS_REL).read_bytes() == unedited:
                return "closure completeness: a helper edit did not change the code-digest"
            return None

        problem = _closure_failure(run_quiet)
        if problem is not None:
            failures.append(problem)
        if _closure_failure(lambda root, check: (run_quiet(root, check), 2)[1]) != \
                "closure completeness: generation expected exit 0, got 2":
            failures.append("closure control: an injected failure after a real generation expected a "
                            "recorded failure, not acceptance of the written output")
        if _closure_failure(lambda root, check: ((root / RENDERERS_REL).write_bytes(unedited), 0)[1]) != \
                "closure completeness: a helper edit did not change the code-digest":
            failures.append("closure control: a generation that returns 0 after writing the unedited output "
                            "back over the edited one expected the unchanged-code-digest failure (the "
                            "comparison branch itself, not only the rc branch, must be exercised)")

        # (c) a mutated renderers.toml is caught by --check. The regeneration prerequisite records its own
        # failure: were it skipped silently, a drift-detection defect could escape whenever regeneration
        # fails, so the drift assertion runs only after a successful regeneration.
        if run_quiet(good, check=False) != 0:
            failures.append("mutated renderers.toml: prerequisite regeneration expected exit 0")
        else:
            target = good / RENDERERS_REL
            target.write_text(target.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated renderers.toml expected exit 1 (drift)")

        # (d) a wildcard import of a pack-local module fails closed (exit 2).
        wild = tmp / "wild"
        _fixture(wild, _ENTRY_WILDCARD)
        if run_quiet(wild, check=False) != 2:
            failures.append("wildcard pack-local import expected exit 2 (fail-closed)")

        # (e) a missing RENDERER_DECL fails closed (exit 2).
        nodecl = tmp / "nodecl"
        _fixture(nodecl, _ENTRY_NODECL)
        if run_quiet(nodecl, check=False) != 2:
            failures.append("missing RENDERER_DECL expected exit 2 (fail-closed)")

        # (f) a non-slug renderer-id in RENDERER_DECL ("Bad ID") fails closed (exit 2), so a malformed id
        # can never be rendered into a fresh renderers.toml (this round's #3).
        badid = tmp / "badid"
        _fixture(badid, _ENTRY_BADID)
        if run_quiet(badid, check=False) != 2:
            failures.append("non-slug renderer-id 'Bad ID' expected exit 2 (fail-closed, this round's #3)")

        # (g) roster completeness: renderers.toml is generated current for the listed gen_alpha, then
        # tools/gen_beta.py appears declaring RENDERER_DECL without being listed in RENDERERS. A freshness
        # comparison alone would pass, since both sides read the same RENDERERS; --check must fail closed.
        unlisted = tmp / "unlisted"
        _fixture(unlisted, _ENTRY_GOOD)
        if run_quiet(unlisted, check=False) != 0:
            failures.append("roster completeness: fixture generation expected exit 0")
        (unlisted / "tools" / "gen_beta.py").write_text(_ENTRY_UNLISTED, encoding="utf-8")
        if run_quiet(unlisted, check=True) != 2:
            failures.append("an unlisted tools/gen_beta.py declaring RENDERER_DECL expected --check exit 2 "
                            "(roster incomplete, fail-closed)")

        # (h) an unsupported static binding of RENDERER_DECL is refused, never read as absence.
        for label, body in _UNSUPPORTED_BINDINGS:
            case = tmp / ("binding-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("binding {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            if label == "dotted import root":
                # the imported fixture package really exists, so under a weakened scan the import would
                # genuinely rebind the name at run time; discovery itself is flat and never enters it
                pkg = Path(case, "tools", "RENDERER_DECL")
                pkg.mkdir()
                (pkg / "__init__.py").write_text("", encoding="utf-8")
                (pkg / "sub.py").write_text("VALUE = 1\n", encoding="utf-8")
            rc, err = run_refused(case, check=True)
            if rc != 2 or "unsupported use of RENDERER_DECL" not in err:
                failures.append("an unlisted tools/gen_beta.py binding RENDERER_DECL by {} expected --check "
                                "exit 2 naming an unsupported use (fail-closed), got exit {} with "
                                "diagnostic tail {!r}".format(label, rc, err.strip()[-160:]))

        # (i) a *.py entry that is not a regular file is refused, never skipped.
        def _dangling(p):
            p.symlink_to("missing.py")

        def _looping(p):
            p.symlink_to(p.name)

        def _to_dir(p):
            (p.parent / "adir").mkdir()
            p.symlink_to("adir")

        def _fifo(p):
            os.mkfifo(str(p))

        for label, make in (("dangling symlink", _dangling), ("looping symlink", _looping),
                            ("symlink to a directory", _to_dir), ("directory", Path.mkdir),
                            ("FIFO", _fifo)):
            case = tmp / ("entry-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("entry {}: fixture generation expected exit 0".format(label))
            try:
                make(case / "tools" / "gen_beta.py")
            except (OSError, AttributeError, NotImplementedError) as exc:
                failures.append("entry {}: cannot build the fixture ({})".format(label, exc))
                continue
            if run_quiet(case, check=True) != 2:
                failures.append("a tools/gen_beta.py that is a {} expected --check exit 2 (non-regular "
                                "*.py entry, fail-closed)".format(label))

        # (j) one declaration predicate for discovery and the reader; each refusal vector asserts its own
        # diagnostic, like (h), so an unrelated refusal (an unparsable fixture, say) cannot keep it green.
        annotated = tmp / "decl-annotated"
        _fixture(annotated, _ENTRY_ANNOTATED)
        if run_quiet(annotated, check=False) != 0 or 'renderer-id = "alpha"' not in \
                (annotated / RENDERERS_REL).read_text(encoding="utf-8"):
            failures.append("a listed generator declaring RENDERER_DECL by an annotated assignment alone "
                            "expected generation exit 0 rendering renderer-id alpha (shared predicate)")
        for label, body, expected in _REBOUND:
            case = tmp / ("decl-" + label.replace(" ", "-"))
            _fixture(case, body)
            rc, err = run_refused(case, check=False)
            if rc != 2 or expected not in err:
                failures.append("a listed generator with {} of RENDERER_DECL expected generation exit 2 "
                                "naming its refusal ({!r}), got exit {} with diagnostic tail {!r} "
                                "(fail-closed for its own reason, never an unrelated one)"
                                .format(label, expected, rc, err.strip()[-160:]))
        rebound = tmp / "decl-annotated-rebinding" / "tools" / "gen_alpha.py"
        if not rebound.is_file():
            failures.append("the reader-alone fixture decl-annotated-rebinding/tools/gen_alpha.py is "
                            "missing, so the two-binding refusal was never exercised")
        else:
            try:
                _read_renderer_decl(rebound, "gen_alpha.py")
                failures.append("the reader alone expected GateError on an annotated rebinding of "
                                "RENDERER_DECL")
            except GateError as exc:
                if "bound 2 times at top level" not in str(exc):
                    failures.append("the reader alone expected the GateError to name the rebinding ('bound "
                                    "2 times at top level'), got {!r}".format(str(exc)))
        for label, body in _MUTATED:
            case = tmp / ("mutated-" + label.replace(" ", "-"))
            _fixture(case, body)
            rc, err = run_refused(case, check=False)
            if rc != 2 or "unsupported use of RENDERER_DECL" not in err:
                failures.append("a listed generator changing RENDERER_DECL in place through {} expected "
                                "generation exit 2 naming an unsupported use (the value run would differ "
                                "from the value read), got exit {} with diagnostic tail {!r}"
                                .format(label, rc, err.strip()[-160:]))
            elif (case / RENDERERS_REL).exists():
                failures.append("a listed generator changing RENDERER_DECL in place through {} was refused "
                                "but still wrote {}".format(label, RENDERERS_REL))
            else:
                sentinel = b"# a pre-existing renderers.toml a refused run must leave byte-identical\n"
                (case / RENDERERS_REL).write_bytes(sentinel)
                rc, err = run_refused(case, check=False)
                if rc != 2 or "unsupported use of RENDERER_DECL" not in err:
                    failures.append("a listed generator changing RENDERER_DECL in place through {} "
                                    "expected exit 2 again, for its own reason, with a pre-existing "
                                    "renderers.toml, got exit {} with diagnostic tail {!r}"
                                    .format(label, rc, err.strip()[-160:]))
                elif (case / RENDERERS_REL).read_bytes() != sentinel:
                    failures.append("a listed generator changing RENDERER_DECL in place through {} was "
                                    "refused but changed a pre-existing {} (it must stay byte-identical)"
                                    .format(label, RENDERERS_REL))

        # (k) a coding cookie cannot hide a declaration; a UTF-8 BOM parses as Python parses it.
        for label, data in _COOKIES:
            case = tmp / ("cookie-" + label.split()[0])
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("cookie {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_bytes(data)
            if run_quiet(case, check=True) != 2:
                failures.append("an unlisted tools/gen_beta.py with a {} expected --check exit 2 "
                                "(fail-closed)".format(label))
        bom = tmp / "cookie-bom"
        _fixture(bom, _ENTRY_GOOD)
        (bom / "tools" / "gen_beta.py").write_bytes(_BOM_DECL)
        try:
            if _declared_renderer_stems(bom / "tools") != ["gen_alpha", "gen_beta"]:
                failures.append("a UTF-8 BOM before a declaration expected discovery of gen_beta")
        except GateError as exc:
            failures.append("a UTF-8 BOM before a declaration expected discovery, got GateError ({})"
                            .format(exc))

        # (l) a parser or compiler stack overflow is a GateError (exit 2), never a traceback. The overflow is
        # injected (see the case note above), so the case is identical on every interpreter and turns red if
        # MemoryError or RecursionError is dropped from either mapping.
        for label, attr, exc_class in _DEEP:
            case = tmp / ("deep-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            (case / "tools" / "zz_deep.py").write_text("VALUE = 1\n", encoding="utf-8")
            real = getattr(ast, attr)

            def overflow(*args, _real=real, _attr=attr, _exc=exc_class, **kwargs):
                if _attr == "literal_eval" or kwargs.get("filename") == _DEEP_FILE:
                    raise _exc("injected {} overflow".format(_attr))
                return _real(*args, **kwargs)

            setattr(ast, attr, overflow)
            try:
                rc = run_quiet(case, check=False)
            except (MemoryError, RecursionError) as exc:
                rc = "raised {}".format(type(exc).__name__)
            finally:
                setattr(ast, attr, real)
            if rc != 2:
                failures.append("an injected {} ({}) expected exit 2 (GateError), got {}"
                                .format(label, "tools/gen_alpha.py RENDERER_DECL" if attr == "literal_eval"
                                        else _DEEP_FILE, rc))

        # (m) an accurate refusal message.
        for source, expected in _WHERE_MESSAGES:
            got = _unsupported_binding(ast.parse(source))
            if got != expected:
                failures.append("binding message for {!r}: expected {!r}, got {!r}"
                                .format(source, expected, got))

        # (n) a file that cannot be read is reported as a read failure, an unparsable one as a parse failure. A
        # directory is a read failure on every platform and uid (a mode-0 file is readable by root); the
        # MemoryError read is injected through an object whose read_bytes raises it. The FULL diagnostic
        # prefix ("cannot read it to scan for", "cannot parse it to scan for") is asserted, so an older or
        # shorter wording fails the case.
        class _Unreadable:
            def read_bytes(self):
                raise MemoryError("injected read overflow")

        messages = tmp / "messages"
        (messages / "unreadable.py").mkdir(parents=True)
        (messages / "unparsable.py").write_bytes(b"(\n")
        for name, expected in (("unreadable.py", "cannot read it to scan for"),
                               ("oversized.py", "cannot read it to scan for"),
                               ("unparsable.py", "cannot parse it to scan for")):
            path = _Unreadable() if name == "oversized.py" else messages / name
            try:
                _parse_module(path, "tools/" + name, "RENDERER_DECL")
                failures.append("tools/{} expected GateError ({})".format(name, expected))
            except GateError as exc:
                if not str(exc).startswith("tools/{}: {} ".format(name, expected)):
                    failures.append("tools/{} expected a message starting {!r}, got {!r}"
                                    .format(name, expected, str(exc)))

        # (o) every control stays allowed: a comparison against the string, a third positional argument, a
        # longer string mentioning it, the f-string !r, !a and format-spec mentions, the three bytes
        # replacement fields, the two mixed-kind + orders, the one-argument str(bytes) concatenation and
        # the non-ASCII-compatible decode concatenation; each is an exempt use or a non-matching
        # string, never proof of inertness.
        for label, body in _ALLOWED_USES:
            case = tmp / ("allowed-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("allowed {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            if run_quiet(case, check=True) != 0:
                failures.append("an unlisted tools/gen_beta.py with a {} of RENDERER_DECL expected --check "
                                "exit 0 (an exempt use or a non-matching string, never proof of "
                                "inertness)".format(label))

        # (p) the fold keeps no value longer than _FOLD_CAP, so a long + chain of long strings stays bounded.
        chain = " + ".join([repr("x" * _FOLD_CAP)] * 4)
        longest = max(len(value) for value in _folded_strings(ast.parse(chain))[0].values())
        if longest > _FOLD_CAP:
            failures.append("the string fold kept a {}-character value, over _FOLD_CAP {}"
                            .format(longest, _FOLD_CAP))

        # (q) this file passes its own scan: it spells the reserved name only in comparisons and as a third
        # positional argument.
        try:
            own = _unsupported_binding(_parse_module(Path(__file__), "tools/gen_renderers.py", "its own scan"))
        except GateError as exc:
            own = str(exc)
        if own is not None:
            failures.append("tools/gen_renderers.py expected to pass its own RENDERER_DECL scan, got {!r}"
                            .format(own))

        # (r) a recognized module-namespace access is accepted only when its key folds to a non-reserved
        # constant string; each reported fixture asserts the unfoldable-key text itself, so a refusal for
        # an unrelated reason cannot keep it green.
        for label, body, expected in _UNFOLDABLE_KEYS:
            case = tmp / ("key-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("namespace key {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            rc, err = run_refused(case, check=True)
            if rc != 2 or expected not in err:
                failures.append("an unlisted tools/gen_beta.py keying a module namespace by a {} expected "
                                "--check exit 2 reporting {!r}, got exit {} with diagnostic tail {!r}"
                                .format(label, expected, rc, err.strip()[-160:]))
        for label, body in _FOLDED_KEYS:
            case = tmp / ("key-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("namespace key {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            if run_quiet(case, check=True) != 0:
                failures.append("an unlisted tools/gen_beta.py keying a module namespace by a {} expected "
                                "--check exit 0 (the key folds to a non-reserved constant string)"
                                .format(label))

        # (s) each exemption is node-local: a descendant of an exempt value that folds to the name is judged
        # in its own position and refused there; each fixture asserts the exact-name text itself, so a
        # refusal for an unrelated reason cannot keep it green.
        for label, body in _NESTED_NAMES:
            case = tmp / ("nested-" + label.replace(" ", "-"))
            _fixture(case, _ENTRY_GOOD)
            if run_quiet(case, check=False) != 0:
                failures.append("nested name {}: fixture generation expected exit 0".format(label))
            (case / "tools" / "gen_beta.py").write_text(body, encoding="utf-8")
            rc, err = run_refused(case, check=True)
            if rc != 2 or _EXACT_NAME_TEXT not in err:
                failures.append("an unlisted tools/gen_beta.py holding a {} expected --check exit 2 "
                                "reporting {!r} (an exemption is node-local), got exit {} with diagnostic "
                                "tail {!r}".format(label, _EXACT_NAME_TEXT, rc, err.strip()[-160:]))

        # (t) a self-test that cannot create its temporary directory exits 2 and leaves RENDERERS as it found
        # it, the same object, so an in-process caller never inherits the self-test roster. A tuple distinct
        # from the one the self-test installs stands in for the caller's roster, and tempfile.mkdtemp raises
        # PermissionError for the nested run; both are restored before the result is judged.
        outer, probe, real_mkdtemp = RENDERERS, ("gen_probe",), tempfile.mkdtemp

        def _no_tmp(*args, **kwargs):
            raise PermissionError("injected: no writable temporary directory")

        RENDERERS, tempfile.mkdtemp = probe, _no_tmp
        try:
            rc, err = run_nested()
        finally:
            leaked, RENDERERS, tempfile.mkdtemp = RENDERERS, outer, real_mkdtemp
        if rc != 2 or "no writable temporary directory" not in err or leaked is not probe:
            failures.append("a self-test whose temporary directory cannot be created expected exit 2 "
                            "reporting no writable temporary directory with RENDERERS still the "
                            "caller's object, got exit {!r}, RENDERERS {!r} and diagnostic tail {!r}"
                            .format(rc, leaked, err.strip()[-160:]))
    finally:
        RENDERERS = saved
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant renderer set generates and regenerates drift-clean and is "
          "deterministic; a helper edit inside a closure changes the framed code-digest; a mutated "
          "renderers.toml fails --check (exit 1); and a wildcard pack-local import, a missing "
          "RENDERER_DECL, a non-slug renderer-id, an unlisted generator declaring RENDERER_DECL, {} "
          "unsupported RENDERER_DECL binding or literal-name forms, 5 non-regular *.py entries, {} rebindings "
          "or bare annotations, {} in-place changes to a listed declaration (each writing nothing and "
          "leaving a pre-existing renderers.toml byte-identical), {} non-UTF-8 coding cookies and "
          "{} injected parser or literal stack overflows each fail closed (exit 2), while an annotated "
          "declaration alone renders, a UTF-8 BOM parses, {} refusal messages name where the binder runs, an "
          "unreadable file is reported as a read failure, {} exempt or non-matching uses of the name stay "
          "allowed (allowed uses, never proof of inertness), {} computed module-namespace keys are reported "
          "as unfoldable while {} folded non-reserved keys stay accepted, {} descendants of an exempt value "
          "that fold to the name are refused (each exemption is node-local), a self-test that cannot create "
          "its temporary directory exits 2 leaving RENDERERS unchanged, the string fold stays bounded, "
          "and this file passes its own scan".format(
              len(_UNSUPPORTED_BINDINGS), len(_REBOUND), len(_MUTATED), len(_COOKIES), len(_DEEP),
              len(_WHERE_MESSAGES), len(_ALLOWED_USES), len(_UNFOLDABLE_KEYS), len(_FOLDED_KEYS),
              len(_NESTED_NAMES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
