#!/usr/bin/env python3
"""The parse child of tools/_input_declarations.py, and nothing else's: it reads one UTF-8 text from
stdin, parses it with the pinned vendored CommonMark parser in THIS process, and writes one JSON document
to stdout, either {"tree": <the serialized document>}, {"declaration_error": <a fail-closed reading
refusal, a vendored-parser pin or import failure, or a parent module imported from anywhere but this
file's own directory>} or {"error": <any other parse failure>}. It applies no deadline and no size or
structure bound of its own: the parent (_input_declarations._child_parse) refuses an oversized or
degenerate text before starting this process, starts it in its own session with an empty environment,
kills its whole process group at the parse deadline and after it exits, refuses a non-zero exit status,
and trusts only a complete well-formed JSON document of the serialized shape, so a kill, a crash or a
partial write here is the parent's fail-closed refusal. This file carries no __main__ guard and is not an
entry point: it holds its parent module's whole behaviour, runs only under python3 -I (so its directory
is not on the module path), and is useless standing alone. Its own directory is inserted at the FRONT of
the module path, so an installed module of the same name on a later entry cannot shadow the parent, and
the imported module's file must be the one beside this file, so a module of that name imported before
this script ran (a site .pth line, say) is refused, never used."""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import _input_declarations  # noqa: E402

_IMPORTED = getattr(_input_declarations, "__file__", None)
_BESIDE = os.path.realpath(os.path.join(_HERE, "_input_declarations.py"))
if _IMPORTED is None or os.path.realpath(_IMPORTED) != _BESIDE:
    _payload = {"declaration_error": "the parse child imported its parent module from {!r}, not from {!r} "
                                     "beside it".format(_IMPORTED, _BESIDE)}
else:
    try:
        _text = sys.stdin.buffer.read().decode("utf-8")
        _payload = {"tree": _input_declarations._serialize(_input_declarations._parse_document(_text))}
    except _input_declarations.DeclarationError as _exc:
        _payload = {"declaration_error": str(_exc)}
    except BaseException as _exc:   # a recursion error included: the parent turns this into its refusal
        _payload = {"error": "{}: {}".format(type(_exc).__name__, _exc)}
sys.stdout.write(json.dumps(_payload))
