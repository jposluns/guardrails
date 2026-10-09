#!/usr/bin/env python3
"""Import-closure, vendor-provenance, dynamic-import and workflow-pin gate (rules mindep, liccmp, secvde, secsup).

Every import statement in this tree's Python resolves, by the search roots of the program that imports it, to the
standard library of the declared floor version, to a module file in this repository, or to a vendored package with
recorded provenance and licence; every reference to the dynamic-import machinery (an occurrence of a watched name
or import-system member) is one a reviewed row lists exactly, by its lines and text; every action reference in
.github/workflows (and in every action.yml or action.yaml in the tree) has the form of a full commit SHA; and every
container image there is pinned by digest. A third-party or invented package name therefore cannot enter through an
import statement in a scanned file or as a string literal passed to an importer call, a watched-name occurrence
cannot enter unless a reviewed row lists that exact reference, and vendored Python cannot enter without a
recognised SPDX identifier whose licence text it carries.

Search roots. A file's search roots are its own directory (Python puts a script's directory first on
sys.path; this tree's programs insert it explicitly, since python3 -I leaves it out), except when that
directory is a package (it holds __init__.py, so it is not itself on sys.path); each extra root that
IMPORT_SEARCH_ROOTS records for that file; and, for a file under a _vendor directory, that _vendor
directory alone (a vendored package is imported by its own name from there). A module is present in a root
only as NAME.py or a regular package NAME/__init__.py among the walked files: a namespace directory never
resolves an absolute import, since an installed regular package of the same name precedes it.

Rules (each self-test vector is caught by exactly its rule, and passes with that rule removed):
  import-closure     an absolute import statement (an import or from statement anywhere in the file, lazy,
                     guarded or under TYPE_CHECKING included; and a string-literal name at an importer call, see
                     dynamic-import) resolves by its first dotted component to a module
                     present in one of the file's search roots, or to the standard library. A name present in
                     none of them is a finding: a third-party or invented name. A same-named file outside the
                     file's search roots never resolves it. A relative import must name a module or package that
                     exists under its base directory (or the base must be a package). Each IMPORT_SEARCH_ROOTS
                     row is held live: its file is scanned, its root lies beyond the file's own directory, the
                     file calls sys.path.insert or sys.path.append and names every component of the root's path
                     relative to the file's directory as a string literal somewhere in the file, and at least one
                     of its imports resolves through that root; a row that fails any of these is a finding.
  dynamic-import     dynamic imports are not resolved (a literal name excepted, below); a reference to the
                     dynamic-import machinery is refused unless a reviewed row lists that exact reference. The
                     watched names are DYNAMIC_IMPORT_NAMES (__import__, __builtins__, importlib, imp, runpy,
                     zipimport, pkgutil, pydoc, the loader, finder and runner names reachable from them, and the
                     import-system hooks __path__, meta_path and path_hooks) and every undotted name sys.modules
                     gives a module of IMPORT_SYSTEM_MODULES (the frozen _frozen_importlib and
                     _frozen_importlib_external, and _imp, among them); the members are the last component of
                     every dotted such name and every function, class or module defined in those modules
                     (_bootstrap, _gcd_import, _find_and_load, BuiltinImporter, SourceLoader among them), less
                     INERT_IMPORT_SYSTEM_MEMBERS. Both sets are read from the modules on every run, never from a
                     hand list. A site is a reference node holding a watched name as an identifier anywhere in
                     the syntax tree (a name read or bound, an attribute, an imported module or alias, a
                     definition, parameter or keyword name), a member as an attribute name, or either as a
                     dotted component of a string or bytes literal that is a dotted identifier chain
                     (getattr(builtins, "__import__"), sys.modules["_frozen_importlib"]). The reference node is
                     an alias's import statement, a def or class by its header, a parameter, keyword or literal,
                     or the expression climbed through the attribute access, call and subscript the name is the
                     target of. A site is allowed only when a DYNAMIC_IMPORT_SITES row (file, enclosing scope,
                     references, reason) names its file and its enclosing scope and lists its reference exactly:
                     its lines and its unparsed text, as LINE: TEXT or FIRST-LAST: TEXT. The enclosing scope is
                     the classes, defs and lambdas (<lambda>) around it joined by dots, <module> at module level;
                     a definition's name, decorators, defaults, annotations, bases and keywords belong to the
                     scope that runs the definition, and only its parameters and body to its own scope. Each row
                     is held live: it gives a reason, lists at least one reference of that form, repeats none,
                     and every reference it lists occurs there; a row that fails any of these is a finding. A
                     call that reaches __import__, import_module, _gcd_import, find_spec, run_module or locate by
                     name, by attribute, or by getattr or a subscript with a literal name, and passes a string
                     literal absolute name at level 0, is also an import record for import-closure, whether its
                     site is allowlisted or not. No alias is resolved: an alias is itself a site.
  relative-escape    a relative import whose base directory lies outside the repository root, or, for a
                     vendored file, outside its own vendored package root.
  vendor-provenance  each _vendor directory: every NAME.provenance.toml there records [package] name,
                     version and purl (a pkg: URL ending in /NAME@VERSION), [license] spdx and vendored_path,
                     and [vendored_tree] root (one directory directly inside that _vendor directory, named for the
                     package: its name, case aside, with - and . read as _); spdx is one identifier from
                     SPDX_LICENCE_MARKERS, or several joined by AND or by OR (not both); the licence file lies
                     under that _vendor directory, is listed in the manifest, and its text contains every marker
                     sentence of every identifier named (case and whitespace aside); the sibling
                     NAME.manifest.sha256 exists, and every row's file exists with the recorded sha256; every file
                     under the root is listed; every .py file under the _vendor directory lies under a recorded
                     root; and an import that resolves into a _vendor directory resolves to a recorded root.
  action-pin         every `uses:` value is a local ./PATH that stays inside the repository once normalised, a
                     docker:// image pinned by @sha256:<64 hex>, or OWNER/REPO[/PATH]@<40 lowercase hex>; every
                     `image:` or `container:` scalar is pinned by @sha256:<64 hex> (a docker:// prefix allowed)
                     or, in an action file only, names a walked file relative to that file's directory (the
                     local-file exception: a Dockerfile the action builds). A tag, a branch, a short SHA or an
                     expression is a finding.
  disposition        each VENDORED_OPTIONAL_IMPORTS row (a third-party import inside byte-exact vendored code
                     that this tree does not edit) is live and true: the file is under a recorded vendored
                     root, the import occurs there, and either every occurrence sits in the body of a try
                     whose handler is bare or names ImportError, ModuleNotFoundError, Exception or
                     BaseException (guarded-optional), or no import statement anywhere else in the tree reaches
                     that module, a from-import's names included (unreached-module). A row that no longer
                     matches, or whose condition fails, is a finding. A dispositioned import is printed on
                     every run, never hidden.

Workflow and action files are read by a line classifier for a subset of YAML 1.2, not a full parser. A line is
accepted only as: blank; a comment; a block mapping key (plain, or quoted with no escape and no quote inside)
followed by nothing, a comment, a plain or quoted scalar, a flow sequence of scalars none of which holds a
colon, or a block scalar header (| or >, with an optional chomping indicator); a block sequence item (one dash)
holding such a key, such a scalar or flow sequence, or nothing; or block scalar content, whose extent follows
YAML's indentation rule. A key named uses (in any case) is checked wherever it sits, and so is a key named image
or container. Any other line cannot be classified and exits 2, so an unpinned action cannot hide behind syntax
the classifier does not read: an escaped, alias or complex key, an anchor, alias or tag, a flow mapping, a
multi-line flow or plain scalar (a line indented deeper than the key, or the dash, of the preceding line when
that line's value is an inline scalar, even a line that starts with a dash, since YAML reads it as a
continuation of that scalar), a document marker or directive, a second dash on one line, a block scalar as a
sequence item, a uses or image key without an inline scalar, a container key holding a block scalar or a flow
sequence, and a scalar on a structural line holding the word uses followed by a colon (a disclosed
over-rejection). A bare CR, a C0 or C1 control character other than tab, LF and the CR of a CRLF, U+2028, U+2029
and U+FEFF anywhere in the file also exit 2, since a YAML 1.1 reader breaks lines at some of them.

Fail closed (exit 2, cannot evaluate): the standard-library set is read from the interpreter
(sys.stdlib_module_names), never from a hand list, and the gate refuses to run if that attribute is absent,
empty or not a frozenset of nonempty names; it also refuses unless the interpreter's MAJOR.MINOR equals the
floor declared in .aiqt/core/python-floor.toml, since that set belongs to the running version. Also exit 2:
a .py file, workflow or action file, provenance record, manifest or licence text that is not a regular file
(a symlink, a FIFO), is unreadable, is not UTF-8 where text is required, or does not parse; a directory that
cannot be listed; a symlinked directory in the walk; a manifest row that is not one sha256sum row (64 hex,
two spaces, a path not listed before); an absent workflow directory; an absolute import whose first
component is present in two of the file's search roots, or in one of them and the standard library
(ambiguous: which file it reaches depends on sys.path order, which the gate does not model); and a workflow
or action line outside the accepted YAML subset (above).

  check_import_closure.py              scan this repository
  check_import_closure.py --self-test  fixture trees: every rule vector is red with its rule and green
                                       without it (one per watched name and import-system member among them),
                                       every cannot-evaluate vector exits 2, every site vector finds exactly
                                       the sites (scope and reference) it names, every mutant in MUTANTS (one
                                       check removed) fails the vector it names, removing any one watched name
                                       or member fails its vector, and the live tree passes

Exit convention: 0 clean; 1 a finding; 2 usage or cannot-evaluate.

DISCLOSED RESIDUAL. Dynamic imports: an allowlisted site is reviewed, not evaluated; the gate resolves only a
string-literal absolute name at an importer call, so a computed name is never judged by import-closure, and the
unreached-module condition is static (an allowlisted site can still reach the module at runtime: the vendored
marko.helpers, an allowlisted site, loads an extension by a computed name). A row allows one reference, not what
becomes of its value: a listed reference that is not called in place (an importer returned, stored, passed or bound
by an alias, or a lambda holding a call) lets whatever later calls that value import without a site of its own, so
such a row is reviewed for where the value goes, and a finding says when a reference is not a call. References with
the same text on the same lines are one reference. A row names line numbers, so an edit that moves a listed
reference makes the row stale and the reference a finding until the row is rewritten (the re-review the binding
asks for). The members are read from IMPORT_SYSTEM_MODULES, not from every importer: the members of
importlib.resources and importlib.metadata are reached only through a watched importlib reference, and a member's
name used for a local object (a def, a parameter, a plain name) is not a site. Not seen: exec, eval or compile of a
string, whose source the gate does not read as Python; a watched name assembled at runtime from pieces (a computed
string handed to getattr, vars, globals or sys.modules); an import function reached through the object graph
without naming it (a function's __globals__, a frame, the garbage collector); and other standard-library functions
that import by name as a side effect (pickle loading a class reference, logging.config factories, unittest name
loaders, among others): the watched set covers the import system's own modules, not every importer by name in the
standard library. A native library loaded through ctypes, and Python source handed to a subprocess or written to a
file, are outside the surface. Search roots: the own-directory root and the IMPORT_SEARCH_ROOTS rows are the gate's
model of sys.path; a row is held live by a sys.path.insert or sys.path.append call and the root's path components
as string literals anywhere in the file, not by evaluating that call's argument; the gate does not evaluate a
sys.path expression or the order of insertions (it refuses a name present in two places instead), it does not model
an in-code insertion of a path outside the tree (a site-packages directory, which can then shadow an in-repo name),
and it does not see a program launched with another path (PYTHONPATH, python3 -m from another directory, or python3
-I without the own-directory insertion, under which a sibling import fails or reaches an installed module of that
name). Third-party code committed outside a _vendor directory and reached through a recorded root counts as
in-repo, without provenance; the rows are reviewed in this file. Surface: only .py files are scanned; Python
launched from another suffix, other languages, and install commands (pip, npm) written into scripts or
documentation are outside it. The walk reads the working tree, not the git index, so an untracked local file can
change a local verdict (CI checks out clean). The walk does not enter .git, .venv, venv or node_modules at the
repository root (each run names the ones present); a directory of those names deeper in the tree is walked. A *.pyc
file inside a __pycache__ directory is not read. Vendored non-Python files outside a recorded root (data fixtures)
are outside the vendor-provenance surface. Licences: the gate holds that a vendored package names recognised SPDX
identifiers and that its licence text carries each identifier's marker sentences; it does not prove the text
complete or unmodified, judge licence compatibility, check the package against a registry, or consult vulnerability
advisories. A package whose import name differs from its distribution name cannot be recorded (the root must be
named for the package). Pins: the gate checks a reference's form; it does not ask GitHub whether a 40-hex ref names
a commit rather than a branch or tag of that name, or whether the commit is trustworthy. The local-file exception
accepts an action file's image naming a walked file (a Dockerfile); the base images that file names (its FROM
lines) are not checked. A key named image or container inside an action's with: inputs is checked too (an
over-rejection). The classifier follows YAML 1.2 for its subset, and a local uses path is confined by normalising
it; GitHub's own parser and runner were not run against the vectors.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_import_closure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import ast
import hashlib
import importlib.util
import os
import re
import shutil
import stat
import tempfile
import tomllib
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOOR_REL = ".aiqt/core/python-floor.toml"
WORKFLOWS_REL = ".github/workflows"
# Not walked, each anchored at the repository root only (a directory of the same name deeper in the tree is
# walked); every run names the ones present.
SKIPPED_ROOT_NAMES = (".git", ".venv", "venv", "node_modules")
BYTECODE_DIR_NAME = "__pycache__"
VENDOR_DIR_NAME = "_vendor"
PROVENANCE_SUFFIX = ".provenance.toml"
MANIFEST_SUFFIX = ".manifest.sha256"
ACTION_FILE_NAMES = ("action.yml", "action.yaml")
FLOOR_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
MANIFEST_ROW_RE = re.compile(r"(?P<digest>[0-9a-f]{64})  (?P<path>\S(?:.*\S)?)")
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
REMOTE_ACTION_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@(?P<ref>[^@\s]+)")
DOCKER_PINNED_RE = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
IMAGE_PINNED_RE = re.compile(r"(?:docker://)?[^@\s]+@sha256:[0-9a-f]{64}")
RULES = ("import-closure", "dynamic-import", "relative-escape", "vendor-provenance", "action-pin", "disposition")
IMPORT_ERRORS = ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")

# The accepted YAML subset (see the docstring). Anything else on a structural line cannot be classified.
# U+2028, U+2029 and U+FEFF are written by code point, so no invisible character sits in this source.
YAML_FORBIDDEN_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f%s]|\r(?!\n)"
                               % "".join(map(chr, (0x2028, 0x2029, 0xFEFF))))
YAML_LINE_RE = re.compile(r"(?P<indent> *)(?P<dash>-(?: +|$))?(?P<body>.*)")
YAML_KEY_RE = re.compile(r"(?P<key>[A-Za-z0-9_][A-Za-z0-9_.\-/]*|'[A-Za-z0-9_.\-/ ]*'|\"[A-Za-z0-9_.\-/ ]*\")"
                         r" *:(?: +(?P<value>.*))?")
YAML_TAIL = r"(?:[ \t]+#.*)?"
YAML_BLOCK_RE = re.compile(r"[|>][+-]?" + YAML_TAIL)
YAML_SINGLE_RE = re.compile(r"'(?P<value>(?:[^']|'')*)'" + YAML_TAIL)
YAML_DOUBLE_RE = re.compile(r"\"(?P<value>[^\"\\]*)\"" + YAML_TAIL)
YAML_FLOW_ITEM = r"(?:[A-Za-z0-9_.\-/]+|'[^':\\]*'|\"[^\":\\]*\")"
YAML_FLOW_SEQ_RE = re.compile(r"\[ *(?:%s *(?:, *%s *)*)?\]" % (YAML_FLOW_ITEM, YAML_FLOW_ITEM) + YAML_TAIL)
YAML_PLAIN_START = "[]{},#&*!|>'\"%@`"
USES_KEYLIKE_RE = re.compile(r"(?i)(?<![A-Za-z0-9_-])uses['\"]?[ \t]*:")
PIN_KEYS = ("uses", "image", "container")

# SPDX licence identifiers the vendor-provenance rule accepts, each with the sentences its licence text must
# contain (compared case-insensitively with runs of whitespace collapsed).
SPDX_LICENCE_MARKERS = (
    ("0BSD", ("Permission to use, copy, modify, and/or distribute this software for any purpose with or "
              "without fee is hereby granted",)),
    ("Apache-2.0", ("Apache License", "Version 2.0, January 2004")),
    ("BSD-2-Clause", ("Redistribution and use in source and binary forms, with or without modification, are "
                      "permitted provided that the following conditions are met",)),
    ("BSD-3-Clause", ("Redistribution and use in source and binary forms, with or without modification, are "
                      "permitted provided that the following conditions are met", "Neither the name of")),
    ("ISC", ("this software for any purpose with or without fee is hereby granted, provided that the above "
             "copyright notice and this permission notice appear in all copies",)),
    ("MIT", ("Permission is hereby granted, free of charge, to any person obtaining a copy",)),
    ("MPL-2.0", ("Mozilla Public License Version 2.0",)),
    ("PSF-2.0", ("PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",)),
    ("Unlicense", ("This is free and unencumbered software released into the public domain",)),
    ("Zlib", ("This software is provided 'as-is', without any express or implied warranty",)),
)

# The dynamic-import rule's watched names (see the docstring). Written as one string so the table itself is no
# site: a watched name is a site only as an identifier or as a whole dotted string literal.
DYNAMIC_IMPORT_NAMES = frozenset("""
    __import__ __builtins__ __loader__ __path__ __spec__ importlib imp pkgutil pydoc runpy zipimport
    import_module find_spec find_loader load_module exec_module module_from_spec spec_from_file_location
    spec_from_loader SourceFileLoader SourcelessFileLoader ExtensionFileLoader PathFinder FileFinder zipimporter
    run_module run_path resolve_name get_loader get_importer iter_importers walk_packages extend_path locate
    meta_path path_hooks path_importer_cache
""".split())
# The import system's own modules. The watched set is read from them on every run (see the docstring): every
# name sys.modules gives one of them (the frozen _frozen_importlib and _frozen_importlib_external among them) is
# watched like a name in DYNAMIC_IMPORT_NAMES, and every function, class or module defined in them is watched as
# a member: as an attribute name or a string literal component, the positions a member is reached by.
IMPORT_SYSTEM_MODULES = tuple("""
    importlib importlib._bootstrap importlib._bootstrap_external importlib.abc importlib.machinery importlib.util
    _imp pkgutil runpy zipimport
""".split())
# Members of those modules that neither import nor load a module and whose names this tree uses for its own
# objects: (name, why it is not watched). Each must still be a member, or the gate cannot evaluate.
INERT_IMPORT_SYSTEM_MEMBERS = (
    ("abc", "the leaf name of importlib.abc is also the standard-library abc module; importlib.abc is reached "
     "only through a watched importlib reference, and its loader and finder classes are watched by name"),
    ("acquire_lock", "takes the import lock (_imp); it imports nothing"),
    ("lock_held", "reports whether the import lock is held (_imp); it imports nothing"),
    ("release_lock", "releases the import lock (_imp); it imports nothing"),
)
# Calls that import or locate a module named by their first argument: a string-literal absolute name there is
# resolved like an import statement wherever the call sits, an allowlisted site included (see the docstring).
LITERAL_IMPORTERS = frozenset("__import__ import_module _gcd_import find_spec run_module locate".split())
# The positional index of the level argument, for the importers that take one.
LITERAL_IMPORTER_LEVELS = dict(zip("__import__ _gcd_import".split(), (4, 2)))
DOTTED_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
REFERENCE_RE = re.compile(r"(?P<first>[1-9][0-9]*)(?:-(?P<last>[1-9][0-9]*))?: \S.*")
MODULE_SCOPE = "<module>"
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

# The extra search roots of each program that imports across directories (beyond its own directory): each
# row is (repo-relative importing file, repo-relative root directory), a sys.path insertion that file makes.
# The import-closure rule holds every row live (see the docstring).
IMPORT_SEARCH_ROOTS = (
    ("opf/tools/_commonmark_headings.py", "opf/tools/_vendor"),
    ("tools/check_byte_canon.py", "opf/tools"),
    ("tools/check_crosswalk.py", "opf/tools"),
    ("tools/check_manifest.py", "opf/tools"),
    ("tools/check_release_cut.py", "opf/tools"),
    ("tools/check_versions.py", "opf/tools"),
    ("tools/doctor.py", "opf/tools"),
    ("tools/migrate.py", "opf/tools"),
    ("tools/orch_doctor.py", ".aiqt/core/hooks/scripts"),
    ("tools/orch_preflight.py", ".aiqt/core/hooks/scripts"),
    ("tools/pin.py", "opf/tools"),
    ("tools/selftest_aiqt_hooks.py", ".aiqt/core/hooks/scripts"),
    ("tools/selftest_git_fixture_env.py", "opf/tools"),
    ("tools/selftest_orch_hooks.py", ".aiqt/core/hooks/scripts"),
)

# Third-party imports inside byte-exact vendored code (opf/tools/_vendor is not edited here; its provenance
# record states that no extra is vendored and no extension is loaded on the parse path). Each row is
# (repo-relative file, first dotted component, condition). The disposition rule holds each row live and its
# condition true on every run, and every dispositioned import is printed, so the hit stays visible to the
# owner of the vendored tree.
VENDORED_OPTIONAL_IMPORTS = (
    ("opf/tools/_vendor/marko/element.py", "objprint", "guarded-optional"),
    ("opf/tools/_vendor/marko/ext/codehilite.py", "pygments", "unreached-module"),
    ("opf/tools/_vendor/marko/ext/toc.py", "slugify", "guarded-optional"),
)
CONDITIONS = ("guarded-optional", "unreached-module")

# The reviewed dynamic-import sites: (repo-relative file, enclosing scope, the exact references allowed there, each
# LINE: TEXT or FIRST-LAST: TEXT with the reference's unparsed source, why they are legitimate). Dynamic imports are
# not resolved; a reference no row lists is a finding, a moved or edited reference included, and the
# dynamic-import rule holds every row live (see the docstring).
DYNAMIC_IMPORT_SITES = (
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_sha256_hex",
     ("8723: __import__('hashlib').sha256(text.encode('utf-8')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_state_dir_from_registry",
     ("9148: __import__('hashlib').sha256(root.encode('utf-8', 'replace')).hexdigest()[:16]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_register_wake",
     ("10265: __import__('hashlib').sha256(prompt.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "orch_ask_guard",
     ("10404: __import__('hashlib').sha256(text.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "orch_dispatch_ledger",
     ("11646-11647: __import__('hashlib').sha256((basis + _orch_now().isoformat()).encode('utf-8', "
      "'replace')).hexdigest()[:12]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_blob_id",
     ("11909: __import__('hashlib').new(fmt)",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_worktree_entry",
     ("11930: __import__('errno')",
      "11953: __import__('hashlib').new(fmt)"),
     "inline __import__ of the standard-library module errno and hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_decide",
     ("12335: __import__('threading').Thread(target=_work, name='review-dispatch-pin', daemon=True)",),
     "inline __import__ of the standard-library module threading, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_binding_digest",
     ("12660: __import__('hashlib').sha256(blob.encode('ascii')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_record_path",
     ("12695: __import__('hashlib').sha256(json.dumps([identity, rel]).encode('utf-8', 'surrogateescape'))",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "orch_prompt_stamp",
     ("13947-13948: __import__('hashlib').sha256((prompt or '').encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_chained_rows",
     ("14166: __import__('hashlib').sha256",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".preview/char-policy-write.py", "_self_test",
     ("698: import importlib.util",
      "1103: '__builtins__'",
      "1105: '__builtins__'"),
     "vectors name __builtins__ as data for the rebinding check; imports importlib.util for the sibling load"),
    (".preview/char-policy-write.py", "_self_test.sibling_gate",
     ("727: importlib.util.spec_from_file_location('_char_policy_sibling_gate', path)",
      "728: importlib.util.module_from_spec(spec)",
      "729: spec.loader.exec_module(module)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling gate it compares"),
    (".preview/char-policy-write.py", "_self_test.rebound",
     ("966: '__builtins__'",),
     "the rebinding analysis watches stores to __builtins__; data, not an import"),
    (".preview/clock-inject.py", "_self_test.T.test_r13_shared_lease_code_identical_to_stop_hook",
     ("852: import importlib.util",
      "855: importlib.util.spec_from_file_location('sts_sibling', sib)",
      "856: importlib.util.module_from_spec(spec)",
      "860: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/future-stamp-write.py", "_self_test",
     ("3528: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    (".preview/future-stamp-write.py", "_self_test.T.test_shared_grammar_identical_to_sibling",
     ("4040: importlib.util.spec_from_file_location('sts_sibling', sib)",
      "4041: importlib.util.module_from_spec(spec)",
      "4045: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/future-stamp-write.py", "_self_test.T.test_r13_code_quote_helpers_identical_to_sibling",
     ("4559: importlib.util.spec_from_file_location('sts_sibling13', sib)",
      "4560: importlib.util.module_from_spec(spec)",
      "4564: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/stamp-truth-stop.py", "_self_test",
     ("2082: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    (".preview/stamp-truth-stop.py", "_self_test.T.test_r13_shared_lease_code_identical_to_clock_inject",
     ("3523: importlib.util.spec_from_file_location('ci_sibling', sib)",
      "3524: importlib.util.module_from_spec(spec)",
      "3528: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/stamp-truth-stop.py", "_self_test.T.test_shared_grammar_identical_to_sibling",
     ("3542: importlib.util.spec_from_file_location('fsw_sibling', sib)",
      "3543: importlib.util.module_from_spec(spec)",
      "3547: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    ("opf/tools/_opf_adopt_observe.py", "_cancellation_self_test",
     ("1304: builtins.__import__",
      "1836: '__import__'"),
     "self-test saves and patches builtins.__import__ to prove no lazy import runs, then restores it"),
    ("opf/tools/_opf_check.py", "self_test.fn_date",
     ("3546: __import__('datetime').date(2026, 6, 1)",),
     "inline __import__ of the standard-library module datetime, a string literal"),
    ("opf/tools/_opf_emit.py", "_load_byte_canon_authority",
     ("572: import importlib.util",
      "574: importlib.util.spec_from_file_location('_opf_emit_byte_canon_authority', path)",
      "577: importlib.util.module_from_spec(spec)",
      "580: spec.loader.exec_module(module)"),
     "loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the byte-canon "
     "authority"),
    ("opf/tools/_opf_emit.py", "_fixture_preload",
     ("1534: import importlib",
      "1536: importlib.import_module(name)"),
     "imports the fixed standard-library names of _FIXTURE_GUARDIAN_MODULES before a fork"),
    ("opf/tools/_opf_emit.py", "_st_guardian_close_reuse",
     ("3426: import importlib.util",
      "3436-3437: importlib.util.spec_from_file_location('_opf_emit_close_harness', Path(__file__).resolve().parent "
      "/ '_journal.py')",
      "3438: importlib.util.module_from_spec(spec)",
      "3439: spec.loader.exec_module(_journal)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "close harness"),
    ("opf/tools/_opf_schema.py", "self_test",
     ("1796: imp",
      "1797: imp['actor']",
      "1798: imp['refs']",
      "1799: imp['links']",
      "1800: imp"),
     "a local variable named imp (an import record), not the imp module"),
    ("opf/tools/_opf_store.py", MODULE_SCOPE,
     ("127: 'imp'",),
     "the string imp is a run-id prefix, not the imp module"),
    ("opf/tools/_opf_worklog_regressions.py", "_upgrade_preflight_regressions",
     ("908: opf._bootstrap()",),
     "calls opf.py's own _bootstrap function (the opf module of this tree), not importlib._bootstrap"),
    ("opf/tools/_vendor/marko/cli.py", MODULE_SCOPE,
     ("6: import importlib",),
     "byte-exact vendored Marko: its command-line entry imports importlib"),
    ("opf/tools/_vendor/marko/cli.py", "import_class",
     ("16: importlib.import_module(module)",),
     "byte-exact vendored Marko: its command-line entry imports a class named on its command line; not on this "
     "tree's parse path"),
    ("opf/tools/_vendor/marko/helpers.py", MODULE_SCOPE,
     ("10: from importlib import import_module",),
     "byte-exact vendored Marko: imports import_module for load_extension"),
    ("opf/tools/_vendor/marko/helpers.py", "load_extension",
     ("125: import_module(f'marko.ext.{name}')",
      "130: import_module(name)"),
     "byte-exact vendored Marko: loads an extension by a computed name; this tree passes none (disclosed residual)"),
    ("opf/tools/check_opf_doctor.py", "_claude_hook_self_test",
     ("2285: import importlib.util",
      "3207: importlib.util.spec_from_file_location('_opf_claude_hook_classifier', hook)",
      "3208: importlib.util.module_from_spec(hook_spec)",
      "3209: hook_spec.loader.exec_module(hook_mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "Claude hook classifier"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors",
     ("2607: 'imp'",
      "2647: 'imp'",
      "2678: 'imp'"),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors.<lambda>.<lambda>",
     ("2633: 'imp'",
      "2673: 'imp'"),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors.<lambda>",
     ("2727: 'imp'",),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_init_contract.py", MODULE_SCOPE,
     ("24: import runpy",),
     "imports runpy for _checks"),
    ("opf/tools/check_opf_init_contract.py", "_checks",
     ("184: runpy.run_path(str(ROOT / 'opf/tools/_opf_init_contract.py'))",),
     "runs opf/tools/_opf_init_contract.py of this tree by its path to read its namespace"),
    ("opf/tools/check_opf_prompt_pack.py", "_close_vectors",
     ("359: import importlib.util",
      "361-362: importlib.util.spec_from_file_location('_prompt_pack_close_harness', Path(__file__).resolve().parent "
      "/ '_journal.py')",
      "363: importlib.util.module_from_spec(spec)",
      "364: spec.loader.exec_module(harness)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "close harness"),
    ("opf/tools/check_opf_record.py", "_self_test_isolated",
     ("5977: opf._bootstrap()",),
     "calls opf.py's own _bootstrap function (the opf module of this tree), not importlib._bootstrap"),
    ("opf/tools/check_opf_upgrade.py", "_suite_isolated",
     ("591: opf._bootstrap()",),
     "calls opf.py's own _bootstrap function (the opf module of this tree), not importlib._bootstrap"),
    ("opf/tools/check_opf_upgrade.py", "_suite_isolated",
     ("1927: import importlib.util as _importlib_util",
      "1971: _importlib_util.spec_from_file_location('_opf_u25c_flip', _path25c)",
      "1972: _importlib_util.module_from_spec(_spec25c)",
      "1973: _spec25c.loader.exec_module(_mod25c)"),
     "self-test loads a scratch flip of an upgrade file, written from this tree's source, by its path"),
    ("opf/tools/opf.py", MODULE_SCOPE,
     ("300: '__builtins__'",),
     "names __builtins__ as a namespace the entry check refuses stores through; data, not an import"),
    ("opf/tools/opf.py", "_self_test_entry_gap",
     ("447: '__builtins__'",),
     "the entry check reads bindings of __builtins__; not an import"),
    ("opf/tools/opf.py", "_import_body_findings",
     ("15337: '__builtins__'",
      "15395: '__builtins__'"),
     "the namespace check reads bindings of __builtins__; not an import"),
    ("opf/tools/opf.py", "_import_namespace_findings",
     ("15521: '__builtins__'",),
     "compares a function's __builtins__ with the builtins dictionary; not an import"),
    ("opf/tools/opf.py", "_cli_self_test._import_leg",
     ("16431: probe.__builtins__",
      "16437: '__builtins__'"),
     "vectors bind __builtins__ to prove the namespace check refuses it; not an import"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_sha256_hex",
     ("8723: __import__('hashlib').sha256(text.encode('utf-8')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_state_dir_from_registry",
     ("9148: __import__('hashlib').sha256(root.encode('utf-8', 'replace')).hexdigest()[:16]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_register_wake",
     ("10265: __import__('hashlib').sha256(prompt.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "orch_ask_guard",
     ("10404: __import__('hashlib').sha256(text.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "orch_dispatch_ledger",
     ("11646-11647: __import__('hashlib').sha256((basis + _orch_now().isoformat()).encode('utf-8', "
      "'replace')).hexdigest()[:12]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_blob_id",
     ("11909: __import__('hashlib').new(fmt)",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_worktree_entry",
     ("11930: __import__('errno')",
      "11953: __import__('hashlib').new(fmt)"),
     "inline __import__ of the standard-library module errno and hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_decide",
     ("12335: __import__('threading').Thread(target=_work, name='review-dispatch-pin', daemon=True)",),
     "inline __import__ of the standard-library module threading, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_binding_digest",
     ("12660: __import__('hashlib').sha256(blob.encode('ascii')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_record_path",
     ("12695: __import__('hashlib').sha256(json.dumps([identity, rel]).encode('utf-8', 'surrogateescape'))",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "orch_prompt_stamp",
     ("13947-13948: __import__('hashlib').sha256((prompt or '').encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_chained_rows",
     ("14166: __import__('hashlib').sha256",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("tools/_selftest_exit_report.py", MODULE_SCOPE,
     ("178: import importlib.machinery",
      "179: import importlib.util"),
     "imports importlib.machinery and importlib.util for the runner loader"),
    ("tools/_selftest_exit_report.py", MODULE_SCOPE,
     ("213: threading.Thread._bootstrap",),
     "threading's own Thread._bootstrap method, bound to run every thread; not importlib._bootstrap"),
    ("tools/_selftest_exit_report.py", "_record_subinterp_files",
     ("396: importlib.util.find_spec(name)",),
     "find_spec locates the files of fixed standard-library extension modules without importing them"),
    ("tools/_selftest_exit_report.py", MODULE_SCOPE,
     ("422: importlib.machinery.SourceFileLoader",),
     "subclasses SourceFileLoader to launch a registered runner from its source, as a direct launch does"),
    ("tools/_selftest_exit_report.py", "_bootstrap_main",
     ("444: main.__loader__",
      "446: loader.exec_module(main)"),
     "executes the registered runner through that loader as __main__"),
    ("tools/check_entry_guard.py", MODULE_SCOPE,
     ("84: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_entry_guard.py", "_load_mutant",
     ("686: importlib.util.spec_from_file_location('entry_guard_mutant_{}'.format(number), path)",
      "687: importlib.util.module_from_spec(spec)",
      "690: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_import_closure.py", MODULE_SCOPE,
     ("182: import importlib.util",
      "659: 'self_test._NestedMissingFinder.find_spec'",
      "680: 'self_test._NestedMissingFinder.find_spec'"),
     "this gate's tables and self-test data name watched names as strings; it imports importlib.util for _load_mutant"),
    ("tools/check_import_closure.py", "watched_names",
     ("890: importlib.import_module(name)",),
     "loads the fixed standard-library modules of IMPORT_SYSTEM_MODULES to read the watched set from them"),
    ("tools/check_import_closure.py", "_load_mutant",
     ("2335: importlib.util.spec_from_file_location('import_closure_mutant_%d' % number, path)",
      "2336: importlib.util.module_from_spec(spec)",
      "2339: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_instruction_budget.py", "_mutant",
     ("1386: import importlib.util",
      "1402: importlib.util.spec_from_file_location('check_instruction_budget_mutant', path)",
      "1403: importlib.util.module_from_spec(spec)",
      "1407: spec.loader.exec_module(mutant)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_instruction_budget.py", "self_test",
     ("1655: imp",
      "1656: imp"),
     "a local variable named imp (an import fixture tree), not the imp module"),
    ("tools/check_manifest.py", "_self_test_main_isolated",
     ("387: __import__('subprocess').run(['git', '--version'], capture_output=True).returncode",),
     "inline __import__ of the standard-library module subprocess, a string literal"),
    ("tools/check_overclaim.py", "_asset_closure_self_test",
     ("3627: 'imp.css'",
      "3632: 'imp.css'"),
     "the string imp.css is a fixture file name, not the imp module"),
    ("tools/check_python_floor.py", MODULE_SCOPE,
     ("192: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_python_floor.py", "_rule_reverts",
     ("1966: importlib.util.spec_from_file_location('python_floor_copy', copy)",
      "2045: importlib.util.module_from_spec(spec)",
      "2046: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_selftest_execution.py", MODULE_SCOPE,
     ("179: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_selftest_execution.py", "self_test",
     ("1585-1587: importlib.util.spec_from_file_location('_selftest_exit_report_probe', "
      "str(Path(__file__).resolve().parent / '_selftest_exit_report.py'))",
      "1588: importlib.util.module_from_spec(spec)",
      "1589: spec.loader.exec_module(probe)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): "
     "tools/_selftest_exit_report.py as a probe"),
    ("tools/gen_crosswalk.py", "_fdopen_vectors",
     ("648: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/gen_crosswalk.py", "_fdopen_vectors.flipped",
     ("722: importlib.util.spec_from_file_location('_crosswalk_fdopen_flip_' + flip_path.stem, flip_path)",
      "723: importlib.util.module_from_spec(spec)",
      "725: spec.loader.exec_module(reverted)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/migrate.py", "self_test",
     ("867: import importlib.util",
      "894: importlib.util.spec_from_file_location('_migrate_no_tomllib', os.path.abspath(__file__))",
      "896: importlib.util.module_from_spec(nt_spec)",
      "896: nt_spec.loader.exec_module(importlib.util.module_from_spec(nt_spec))",
      "931: sys.meta_path.insert(0, nd_finder)",
      "933: importlib.util.spec_from_file_location('_migrate_nested_missing', os.path.abspath(__file__))",
      "935: importlib.util.module_from_spec(nd_spec)",
      "935: nd_spec.loader.exec_module(importlib.util.module_from_spec(nd_spec))",
      "942: sys.meta_path.remove(nd_finder)"),
     "self-test reloads this file by its path, once under a meta_path finder that fails tomllib, to prove the "
     "missing-dependency path"),
    ("tools/migrate.py", "self_test._NestedMissingFinder",
     ("919: def find_spec",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/migrate.py", "self_test._NestedMissingFinder.find_spec",
     ("920: importlib.util.spec_from_loader(name, self)",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/migrate.py", "self_test._NestedMissingFinder",
     ("925: def exec_module",),
     "the self-test finder's loader method, which raises to simulate a missing dependency"),
    ("tools/pin.py", "self_test",
     ("1340: import importlib.util",
      "1394: importlib.util.spec_from_file_location('_pin_no_tomllib', os.path.abspath(__file__))",
      "1396: importlib.util.module_from_spec(nt_spec)",
      "1396: nt_spec.loader.exec_module(importlib.util.module_from_spec(nt_spec))",
      "1430: sys.meta_path.insert(0, nd_finder)",
      "1432: importlib.util.spec_from_file_location('_pin_nested_missing', os.path.abspath(__file__))",
      "1434: importlib.util.module_from_spec(nd_spec)",
      "1434: nd_spec.loader.exec_module(importlib.util.module_from_spec(nd_spec))",
      "1441: sys.meta_path.remove(nd_finder)"),
     "self-test reloads this file by its path, once under a meta_path finder that fails tomllib, to prove the "
     "missing-dependency path"),
    ("tools/pin.py", "self_test._NestedMissingFinder",
     ("1418: def find_spec",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/pin.py", "self_test._NestedMissingFinder.find_spec",
     ("1419: importlib.util.spec_from_loader(name, self)",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/pin.py", "self_test._NestedMissingFinder",
     ("1424: def exec_module",),
     "the self-test finder's loader method, which raises to simulate a missing dependency"),
    ("tools/selftest_orch_hooks.py", "_main_isolated",
     ("3256: import importlib",
      "3259: importlib.import_module('orch_doctor')",
      "3755: __import__('io').StringIO()"),
     "imports the in-tree orch_doctor by its literal name through a recorded search root; inline __import__ of the "
     "standard-library io"),
)


class CannotEvaluate(Exception):
    """A fail-closed condition: the verdict cannot be computed (exit 2)."""


def stdlib_names(sys_module=sys):
    """The standard-library top-level names, from the interpreter only. Refuses if the source is missing
    or malformed, so a hand list never stands in for it."""
    names = getattr(sys_module, "stdlib_module_names", None)
    if not isinstance(names, frozenset) or not names or not all(isinstance(n, str) and n for n in names):
        raise CannotEvaluate("sys.stdlib_module_names is unavailable or malformed on this interpreter; the "
                             "standard-library set has no other source")
    return names


def _regular_bytes(path, rel):
    """The bytes of a regular file, refusing a symlink, FIFO or other non-regular file without opening it."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("%s: cannot stat: %s" % (rel, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("%s: not a regular file (a symlink or special file)" % rel)
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise CannotEvaluate("%s: unreadable: %s" % (rel, exc))


def _text(path, rel):
    try:
        return _regular_bytes(path, rel).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("%s: not UTF-8: %s" % (rel, exc))


def _toml(path, rel):
    try:
        return tomllib.loads(_text(path, rel))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("%s: does not parse as TOML: %s" % (rel, exc))


def load_floor(root):
    data = _toml(Path(root) / FLOOR_REL, FLOOR_REL)
    floor = data.get("python-floor")
    match = FLOOR_RE.fullmatch(floor) if isinstance(floor, str) else None
    if match is None:
        raise CannotEvaluate("%s: python-floor is absent or not MAJOR.MINOR" % FLOOR_REL)
    return int(match.group(1)), int(match.group(2))


def check_interpreter(floor, version_info):
    running = tuple(version_info[:2])
    if running != tuple(floor):
        raise CannotEvaluate("the standard-library set comes from this interpreter (Python %d.%d), which is not "
                             "the declared floor (Python %d.%d); run the gate under Python %d.%d"
                             % (running + tuple(floor) + tuple(floor)))


def _walk(root):
    """Every file under root as (repo-relative posix path, Path), leaving out SKIPPED_ROOT_NAMES at the root
    only and *.pyc files in a __pycache__ directory; fail-closed on an unlistable directory or a symlinked
    directory (never silently skipped)."""
    def _raise(exc):
        raise CannotEvaluate("cannot list a directory: %s" % exc)
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
        top = Path(dirpath) == root
        dirnames[:] = sorted(d for d in dirnames if not (top and d in SKIPPED_ROOT_NAMES))
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                raise CannotEvaluate("%s: a symlinked directory is not walked"
                                     % Path(dirpath, d).relative_to(root).as_posix())
        cache = Path(dirpath).name == BYTECODE_DIR_NAME
        for name in sorted(filenames):
            if (top and name in SKIPPED_ROOT_NAMES) or (cache and name.endswith(".pyc")):
                continue
            path = Path(dirpath) / name
            out.append((path.relative_to(root).as_posix(), path))
    return out


def _join(directory, name):
    return name if not directory else directory + "/" + name


def _dir_of(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _vendor_dir_of(rel):
    """The repo-relative _vendor directory a path lies under, or None."""
    parts = rel.split("/")[:-1]
    if VENDOR_DIR_NAME not in parts:
        return None
    return "/".join(parts[:parts.index(VENDOR_DIR_NAME) + 1])


def _module_present(file_set, directory, name, namespace=False):
    """True if NAME.py or a regular package NAME/__init__.py is a walked file in directory; with namespace,
    also a directory NAME holding a walked .py file (a relative import searches only its own package)."""
    if not name:
        return False
    if _join(directory, name + ".py") in file_set or _join(directory, name + "/__init__.py") in file_set:
        return True
    prefix = _join(directory, name) + "/"
    return namespace and any(f.startswith(prefix) and f.endswith(".py") and "/" not in f[len(prefix):]
                             for f in file_set)


def _catches_import_error(handler):
    kind = handler.type
    if kind is None:
        return True
    kinds = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(isinstance(k, ast.Name) and k.id in IMPORT_ERRORS for k in kinds)


def _callee_name(func):
    """The name a call reaches its function by: a name, an attribute, getattr with a literal name, or a subscript
    with a literal key; None otherwise."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "getattr" and \
            len(func.args) == 2 and isinstance(func.args[1], ast.Constant):
        return func.args[1].value
    if isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Constant):
        return func.slice.value
    return None


def _literal_import_name(call, callee):
    """The string-literal absolute dotted name a LITERAL_IMPORTERS call names, or None when the name is computed,
    relative, starred, or given a level that is not the literal 0."""
    args = call.args
    if any(isinstance(a, ast.Starred) for a in args) or any(k.arg is None for k in call.keywords):
        return None
    name = args[0] if args else None
    for keyword in call.keywords:
        if keyword.arg == "name" and name is None:
            name = keyword.value
        if keyword.arg == "level" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value == 0):
            return None
    level = LITERAL_IMPORTER_LEVELS.get(callee)
    if level is not None and len(args) > level and not (isinstance(args[level], ast.Constant) and
                                                        args[level].value == 0):
        return None
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None
    if not all(part.isidentifier() for part in name.value.split(".")):
        return None
    return name.value


def extract_imports(tree):
    """Import records (lineno, module, level, names, guarded) for every import statement in the file, and for
    every LITERAL_IMPORTERS call with a string-literal absolute name (resolved at an allowlisted site too)."""
    records = []

    def visit(node, guarded):
        if isinstance(node, ast.Import):
            for alias in node.names:
                records.append((node.lineno, alias.name, 0, (), guarded))
        elif isinstance(node, ast.ImportFrom):
            records.append((node.lineno, node.module or "", node.level,
                            tuple(a.name for a in node.names), guarded))
        elif isinstance(node, ast.Call) and _callee_name(node.func) in LITERAL_IMPORTERS:
            name = _literal_import_name(node, _callee_name(node.func))
            if name is not None:
                records.append((node.lineno, name, 0, (), guarded))
        if isinstance(node, (ast.Try, ast.TryStar)):
            body_guarded = guarded or any(_catches_import_error(h) for h in node.handlers)
            for child in node.body:
                visit(child, body_guarded)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, guarded)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return records


def watched_names(modules=IMPORT_SYSTEM_MODULES, inert=INERT_IMPORT_SYSTEM_MEMBERS):
    """(names, members) the dynamic-import rule watches, read from the import system's modules themselves:
    names are DYNAMIC_IMPORT_NAMES and every undotted name sys.modules gives one of the modules; members are the
    last component of every dotted such name and every function, class or module defined in one of them, less
    the inert members. Fail-closed if a module does not load or an inert name is not a member."""
    loaded = []
    for name in modules:
        try:
            loaded.append(importlib.import_module(name))
        except ImportError as exc:
            raise CannotEvaluate("import-system module %s does not load (%s): the watched set has no other source"
                                 % (name, exc))
    aliases = set(k for k, v in list(sys.modules.items()) if any(v is m for m in loaded))
    members = set(a.rsplit(".", 1)[1] for a in aliases if "." in a)
    for module in loaded:
        for key, value in vars(module).items():
            if isinstance(value, (types.FunctionType, types.BuiltinFunctionType, type, types.ModuleType)) and \
                    (getattr(value, "__module__", None) in aliases or getattr(value, "__name__", None) in aliases):
                members.add(key)
    stale = sorted(n for n, _reason in inert if n not in members)
    if stale:
        raise CannotEvaluate("INERT_IMPORT_SYSTEM_MEMBERS names %s, which is not a member of the import system's "
                             "modules on this interpreter" % stale[0])
    names = DYNAMIC_IMPORT_NAMES | frozenset(a for a in aliases if "." not in a)
    return names, frozenset(members) - names - frozenset(n for n, _reason in inert)


def _scoped_children(node, scope):
    """(child, scope, descend) for each child of node. A def, class or lambda runs its body, and binds its
    parameters, in a scope of its own (a lambda's is <lambda>); everything else it holds (decorators, defaults,
    annotations, bases, keywords, type parameters) runs where the definition runs, in the enclosing scope. A
    parameter is a leaf there: its annotation is pushed apart, in the enclosing scope."""
    if not isinstance(node, SCOPE_NODES):
        return [(child, scope, True) for child in ast.iter_child_nodes(node)]
    inner = "<lambda>" if isinstance(node, ast.Lambda) else node.name
    inner = inner if scope == MODULE_SCOPE else scope + "." + inner
    out = []
    for field, value in ast.iter_fields(node):
        for child in value if isinstance(value, list) else (value,):
            if field == "body":
                out.append((child, inner, True))
            elif isinstance(child, ast.arguments):
                for arg in child.posonlyargs + child.args + [child.vararg] + child.kwonlyargs + [child.kwarg]:
                    if arg is not None:
                        out.append((arg, inner, False))
                        if arg.annotation is not None:
                            out.append((arg.annotation, scope, True))
                out.extend((d, scope, True) for d in child.defaults + child.kw_defaults if d is not None)
            elif isinstance(child, ast.AST):
                out.append((child, scope, True))
    return out


def _site_reference(node, parents):
    """The node a watched name is reviewed as: an alias's import statement, or the expression climbed through
    the attribute access, call and subscript the name is the target of (a def, class, parameter, keyword or
    literal is its own node)."""
    if isinstance(node, ast.alias):
        return parents[id(node)]
    while True:
        parent = parents.get(id(node))
        if not (isinstance(parent, ast.Attribute) and parent.value is node or
                isinstance(parent, ast.Call) and parent.func is node or
                isinstance(parent, ast.Subscript) and parent.value is node):
            return node
        node = parent


def reference_text(node):
    """(first line, last line, text) of a site's reference node: a def or class by its header line, an except
    clause by its bound name, anything else by its whole span and its unparsed source."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        head = {ast.FunctionDef: "def ", ast.AsyncFunctionDef: "async def ", ast.ClassDef: "class "}[type(node)]
        return node.lineno, node.lineno, head + node.name
    if isinstance(node, ast.ExceptHandler):
        return node.lineno, node.lineno, "except as " + node.name
    return node.lineno, node.end_lineno, ast.unparse(node)


def reference_key(first, last, text):
    """The reference a DYNAMIC_IMPORT_SITES row lists: LINE: TEXT, or FIRST-LAST: TEXT over several lines."""
    return "%s: %s" % (first if first == last else "%d-%d" % (first, last), text)


def dynamic_sites(tree, names, members):
    """(reference, enclosing scope, watched names, called) for every dynamic-import site in the file: each
    reference node holding an occurrence of a watched name, as an identifier anywhere in the tree, or as a dotted
    component of a string or bytes literal that is a dotted identifier chain; a member counts as an attribute
    name or a literal component only. called is true when the reference is a call. No alias is resolved: an
    alias is itself a site, and whatever later uses the name it binds is not."""
    parents = dict()
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    found = dict()
    stack = [(tree, MODULE_SCOPE, True)]
    while stack:
        node, scope, descend = stack.pop()
        for field, value in ast.iter_fields(node):
            for item in value if isinstance(value, list) else (value,):
                if isinstance(item, bytes):
                    item = item.decode("latin-1")
                if not isinstance(item, str) or DOTTED_NAME_RE.fullmatch(item) is None:
                    continue
                member = isinstance(node, ast.Constant) or (isinstance(node, ast.Attribute) and field == "attr")
                hits = set(part for part in item.split(".") if part in names or (member and part in members))
                if hits:
                    reference = _site_reference(node, parents)
                    found.setdefault(id(reference), (reference, scope, set()))[2].update(hits)
        if descend:
            stack.extend(_scoped_children(node, scope))
    out = []
    for reference, scope, hits in found.values():
        out.append((reference_key(*reference_text(reference)), scope, " ".join(sorted(hits)),
                    isinstance(reference, ast.Call)))
    return sorted(out, key=lambda site: (int(REFERENCE_RE.fullmatch(site[0]).group("first")), site))


def check_dynamic_sites(sites, allowlist):
    """dynamic-import findings: every site (rel, reference, scope, names, called) no allowlist row lists exactly
    (its file, its enclosing scope and its reference: lines and text), and every row that gives no reason, lists
    no reference or a malformed one, repeats a reference, or lists a reference that does not occur there."""
    findings = []
    listed = dict()
    for rel, function, references, reason in allowlist:
        problem = None
        if not reason.strip():
            problem = "gives no reason"
        elif not references or not all(isinstance(r, str) and REFERENCE_RE.fullmatch(r) for r in references):
            problem = "lists no reference, or one not of the form LINE: TEXT or FIRST-LAST: TEXT"
        elif any((rel, function, r) in listed for r in references) or len(set(references)) != len(references):
            problem = "repeats a reference"
        if problem is not None:
            findings.append(("dynamic-import", rel, 0, "DYNAMIC_IMPORT_SITES row (%r, %r) %s" % (rel, function,
                                                                                               problem)))
            continue
        for reference in references:
            listed[(rel, function, reference)] = False
    for rel, reference, function, names, called in sites:
        if (rel, function, reference) in listed:
            listed[(rel, function, reference)] = True
            continue
        findings.append(("dynamic-import", rel, int(REFERENCE_RE.fullmatch(reference).group("first")),
                         "%s in %s is a dynamic-import site no DYNAMIC_IMPORT_SITES row lists (dynamic imports are "
                         "not resolved, and a row allows one exact reference: review it and list %r under (%r, "
                         "%r) with a reason, or remove it)%s"
                         % (names, function, reference, rel, function,
                            "" if called else "; the reference is not called in place, so what it is returned, "
                            "stored or passed to runs without a site of its own")))
    for (rel, function, reference), used in sorted(listed.items()):
        if not used:
            findings.append(("dynamic-import", rel, 0, "DYNAMIC_IMPORT_SITES row (%r, %r) is stale: no reference %r "
                             "occurs there (a moved or edited reference is listed again, and reviewed again)"
                             % (rel, function, reference)))
    return findings


def _spdx_ids(expression):
    """The identifiers of an SPDX expression the gate accepts (one identifier from SPDX_LICENCE_MARKERS, or
    several joined by AND or by OR), or None."""
    words = expression.split(" ")
    ids, operators = words[0::2], set(words[1::2])
    known = dict(SPDX_LICENCE_MARKERS)
    if len(words) % 2 == 0 or len(operators) > 1 or not operators <= frozenset(("AND", "OR")):
        return None
    if not all(i in known for i in ids):
        return None
    return ids


def _normalised(text):
    return " ".join(text.split()).casefold()


def _load_vendor(root, files):
    """Validate every _vendor directory. Returns (records, findings): records maps each recorded root
    (repo-relative, trailing slash) to its top-level package name."""
    findings = []
    records = dict()
    file_set = set(rel for rel, _ in files)
    vendor_dirs = sorted(set(v for v in (_vendor_dir_of(rel) for rel in file_set) if v is not None))
    for vdir in vendor_dirs:
        record_files = sorted(rel for rel in file_set if rel.startswith(vdir + "/") and
                              rel.count("/") == vdir.count("/") + 1 and rel.endswith(PROVENANCE_SUFFIX))
        for rec_rel in record_files:
            data = _toml(Path(root) / rec_rel, rec_rel)
            tables = dict()
            for key in ("package", "license", "vendored_tree"):
                tables[key] = data.get(key) if isinstance(data.get(key), dict) else dict()
            missing = []
            for dotted in ("package.name", "package.version", "package.purl", "license.spdx",
                           "license.vendored_path", "vendored_tree.root"):
                table, key = dotted.split(".")
                value = tables[table].get(key)
                if not (isinstance(value, str) and value.strip()):
                    missing.append(dotted)
            rroot = tables["vendored_tree"].get("root")
            if not isinstance(rroot, str) or not rroot.endswith("/") or not rroot.startswith(vdir + "/") or \
                    rroot.count("/") != vdir.count("/") + 2 or ".." in rroot.split("/"):
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "vendored_tree.root %r is not one directory directly inside %s/" % (rroot, vdir)))
                continue
            # The claimed root resolves imports even when the record fails below, so a defective record is
            # judged by this rule alone, never also as unresolved imports.
            records[rroot] = rroot.rstrip("/").rsplit("/", 1)[1]
            if missing:
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "provenance record lacks %s (no identifiable licence or source)"
                                 % ", ".join(missing)))
                continue
            package = tables["package"]
            if re.sub(r"[-.]", "_", package["name"]).casefold() != records[rroot].casefold():
                findings.append(("vendor-provenance", rec_rel, 0, "vendored_tree.root %s is not named for package %r"
                                 % (rroot, package["name"])))
            purl_re = r"pkg:[a-z][a-z0-9.+-]*/(?:[^/@\s]+/)*%s@%s" % (re.escape(package["name"]),
                                                                      re.escape(package["version"]))
            if re.fullmatch(purl_re, package["purl"]) is None:
                findings.append(("vendor-provenance", rec_rel, 0, "package.purl %r does not name %s@%s"
                                 % (package["purl"], package["name"], package["version"])))
            ids = _spdx_ids(tables["license"]["spdx"])
            if ids is None:
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "license.spdx %r is not one identifier from SPDX_LICENCE_MARKERS, or several "
                                 "joined by AND or by OR" % tables["license"]["spdx"]))
            licence = tables["license"]["vendored_path"]
            man_rel = rec_rel[:-len(PROVENANCE_SUFFIX)] + MANIFEST_SUFFIX
            if man_rel not in file_set:
                findings.append(("vendor-provenance", rec_rel, 0, "per-file manifest %s is missing" % man_rel))
                continue
            rows = dict()
            for number, line in enumerate(_text(Path(root) / man_rel, man_rel).splitlines(), 1):
                match = MANIFEST_ROW_RE.fullmatch(line)
                if match is None or match.group("path") in rows:
                    raise CannotEvaluate("%s:%d: not one sha256sum row (64 hex, two spaces, a unique path)"
                                         % (man_rel, number))
                rows[match.group("path")] = match.group("digest")
            for path_rel, digest in sorted(rows.items()):
                if path_rel not in file_set:
                    findings.append(("vendor-provenance", man_rel, 0, "listed file %s is missing" % path_rel))
                elif hashlib.sha256(_regular_bytes(Path(root) / path_rel, path_rel)).hexdigest() != digest:
                    findings.append(("vendor-provenance", path_rel, 0,
                                     "bytes differ from the sha256 recorded in %s" % man_rel))
            if not licence.startswith(vdir + "/"):
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "licence text %s does not lie under %s/" % (licence, vdir)))
            elif licence not in rows:
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "licence text %s is not listed in %s" % (licence, man_rel)))
            elif ids is not None and licence in file_set:
                text = _normalised(_text(Path(root) / licence, licence))
                for ident, markers in SPDX_LICENCE_MARKERS:
                    absent = [m for m in markers if _normalised(m) not in text]
                    if ident in ids and absent:
                        findings.append(("vendor-provenance", licence, 0,
                                         "licence text lacks the %s marker sentence %r" % (ident, absent[0])))
            for rel in sorted(file_set):
                if rel.startswith(rroot) and rel not in rows:
                    findings.append(("vendor-provenance", rel, 0,
                                     "vendored file has no recorded provenance (not listed in %s)" % man_rel))
        for rel in sorted(file_set):
            if rel.startswith(vdir + "/") and rel.endswith(".py") and not any(rel.startswith(r) for r in records):
                findings.append(("vendor-provenance", rel, 0,
                                 "vendored Python file lies under no recorded provenance root"))
    return records, findings


def _vendored_root_of(rel, records):
    for rroot in records:
        if rel.startswith(rroot):
            return rroot
    return None


def _dotted(rel, rroot):
    """The dotted module name of a file under a recorded vendored root (marko/ext/toc.py is marko.ext.toc)."""
    parts = rel[len(rroot.rstrip("/").rsplit("/", 1)[0]) + 1:].split("/")
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _relative_base(root, own, level):
    """The base directory of a relative import, as a repo-relative posix path ("" for the root; it may start
    with ..)."""
    base = own
    for _ in range(level - 1):
        base = base.parent
    rel = os.path.relpath(base, root).replace(os.sep, "/")
    return base, ("" if rel == "." else rel)


def _search_roots(rel, file_set, extra):
    """The ordered, distinct search roots of one file (see the docstring)."""
    vdir = _vendor_dir_of(rel)
    if vdir is not None:
        return [vdir]
    own = _dir_of(rel)
    roots = [] if _join(own, "__init__.py") in file_set else [own]
    for sroot in extra.get(rel, ()):
        if sroot not in roots:
            roots.append(sroot)
    return roots


def _row_problem(rel, sroot, trees, used):
    """Why an IMPORT_SEARCH_ROOTS row is not live, or None."""
    if rel not in trees:
        return "names no scanned Python file"
    relpath = os.path.relpath(sroot, _dir_of(rel) or ".").replace(os.sep, "/")
    parts = [p for p in relpath.split("/") if p not in ("..", ".")]
    if not parts:
        return "the root is not beyond the file's own directory"
    literals, inserts = set(), False
    for node in ast.walk(trees[rel]):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value)
            literals.update(node.value.split("/"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("insert", "append") and isinstance(node.func.value, ast.Attribute) and \
                node.func.value.attr == "path" and isinstance(node.func.value.value, ast.Name) and \
                node.func.value.value.id == "sys":
            inserts = True
    if not inserts:
        return "the file makes no sys.path.insert or sys.path.append call"
    unnamed = [p for p in parts if p not in literals]
    if unnamed:
        return "the file names no string literal %r of the root's path" % unnamed[0]
    if (rel, sroot) not in used:
        return "stale: no import of the file resolves through the root"
    return None


def scan_python(root, files, stdlib, records, rules, search_roots, watched):
    """Parse every .py file and classify every import. Returns (findings, stats, by_file, sites)."""
    findings = []
    stats = dict(files=0, imports=0, stdlib=0, repo=0, vendored=0, dispositioned=0)
    root = Path(root)
    file_set = set(rel for rel, _ in files)
    py = [(rel, path) for rel, path in files if rel.endswith(".py")]
    extra = dict()
    for rel, sroot in search_roots:
        extra.setdefault(rel, []).append(sroot)
    by_file, trees, used, sites = dict(), dict(), set(), []
    for rel, path in py:
        source = _regular_bytes(path, rel)
        try:
            tree = ast.parse(source, rel)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            raise CannotEvaluate("%s: does not parse: %s" % (rel, exc))
        trees[rel] = tree
        imports = extract_imports(tree)
        sites.extend((rel,) + site for site in dynamic_sites(tree, *watched))
        stats["files"] += 1
        by_file[rel] = imports
        vroot = _vendored_root_of(rel, records)
        roots = _search_roots(rel, file_set, extra)
        for lineno, module, level, names, _guarded in imports:
            stats["imports"] += 1
            if level:
                base, base_rel = _relative_base(root, path.parent, level)
                limit = vroot.rstrip("/") if vroot else None
                escaped = base_rel == ".." or base_rel.startswith("../") or \
                    (limit is not None and base_rel != limit and not base_rel.startswith(limit + "/"))
                if escaped:
                    if "relative-escape" in rules:
                        findings.append(("relative-escape", rel, lineno, "relative import (level %d) escapes %s"
                                         % (level, "its vendored root " + vroot if vroot else "the repository")))
                    continue
                if module:
                    ok = _module_present(file_set, base_rel, module.split(".")[0], namespace=True)
                else:
                    ok = _join(base_rel, "__init__.py") in file_set or \
                        all(_module_present(file_set, base_rel, n, namespace=True) for n in names)
                if not ok:
                    findings.append(("import-closure", rel, lineno,
                                     "relative import names no module under %s" % (base_rel or ".")))
                    continue
                stats["vendored" if vroot else "repo"] += 1
                continue
            top = module.split(".")[0]
            hits = [r for r in roots if _module_present(file_set, r, top)]
            if len(hits) + (top in stdlib) > 1:
                places = ["the standard library"] * (top in stdlib) + [r or "." for r in hits]
                raise CannotEvaluate("%s:%d: import %r is ambiguous: present in %s (which one it reaches depends "
                                     "on sys.path order)" % (rel, lineno, top, ", ".join(places)))
            if not hits:
                if top in stdlib:
                    stats["stdlib"] += 1
                else:
                    findings.append(("import-closure", rel, lineno,
                                     "import %r is not the standard library of the floor version, or a module in "
                                     "a search root of this file (%s); third-party or invented name"
                                     % (top, ", ".join(r or "." for r in roots) or "none")))
                continue
            used.add((rel, hits[0]))
            target = _join(hits[0], top)
            if _vendor_dir_of(target + "/") is None:
                stats["repo"] += 1
            elif target + "/" in records:
                stats["vendored"] += 1
            elif "vendor-provenance" in rules:
                findings.append(("vendor-provenance", rel, lineno,
                                 "import %r resolves to %s, which no provenance record covers" % (top, target)))
    for rel, sroot in search_roots:
        problem = _row_problem(rel, sroot, trees, used)
        if problem is not None:
            findings.append(("import-closure", rel, 0, "IMPORT_SEARCH_ROOTS row (%r, %r): %s"
                             % (rel, sroot, problem)))
    return findings, stats, by_file, sites


def _import_targets(rel, imports, records):
    """(lineno, dotted target) for every absolute or resolvable relative import in one file, each from-import
    name also expanded (from a.b import c gives a.b and a.b.c)."""
    rroot = _vendored_root_of(rel, records)
    out = []
    for lineno, module, level, names, _guarded in imports:
        if level:
            if rroot is None:
                continue
            package = _dotted(rel, rroot).split(".")
            if not rel.endswith("/__init__.py"):
                package = package[:-1]
            package = package[:len(package) - (level - 1)]
            target = ".".join(package + ([module] if module else []))
        else:
            target = module
        out.append((lineno, target))
        out.extend((lineno, target + "." + name) for name in names)
    return out


def apply_dispositions(findings, by_file, records, dispositions, rules, stats):
    """Suppress the import-closure findings each row covers; under the disposition rule, refuse a stale,
    misplaced or untrue row. Returns (findings, printed dispositioned lines)."""
    shown, extra = [], []
    covered = set()
    for rel, top, condition in dispositions:
        rroot = _vendored_root_of(rel, records)
        prefix = "import %r is " % top
        hits = [f for f in findings if f[0] == "import-closure" and f[1] == rel and f[3].startswith(prefix)]
        problem = None
        if condition not in CONDITIONS:
            problem = "unknown condition %r" % condition
        elif rroot is None:
            problem = "the file is not under a recorded vendored root"
        elif not hits:
            problem = "stale: no unresolved import of %r occurs there" % top
        elif condition == "guarded-optional":
            unguarded = [r for r in by_file.get(rel, ()) if r[1].split(".")[0] == top and not r[2] and not r[4]]
            if unguarded:
                problem = "line %d imports %r outside a try that catches ImportError" % (unguarded[0][0], top)
        else:
            dotted = _dotted(rel, rroot)
            for other, imports in sorted(by_file.items()):
                reached = [n for n, t in _import_targets(other, imports, records)
                           if other != rel and (t == dotted or t.startswith(dotted + "."))]
                if reached:
                    problem = "%s:%d imports %s, so the module is reached" % (other, reached[0], dotted)
                    break
        if problem is None or "disposition" not in rules:
            covered.update(hits)
            shown.extend("DISPOSITIONED [%s] %s:%d: %s" % (condition, f[1], f[2], f[3]) for f in hits)
        if problem is not None and "disposition" in rules:
            extra.append(("disposition", rel, 0, "VENDORED_OPTIONAL_IMPORTS row (%r, %r): %s"
                          % (rel, top, problem)))
    stats["dispositioned"] = len(covered)
    return [f for f in findings if f not in covered] + extra, shown


def _plain_scalar(text):
    """A YAML plain scalar with its comment cut, or None if text cannot be one on a single line."""
    end = re.search(r"[ \t]#", text)
    value = (text[:end.start()] if end else text).rstrip(" \t")
    if not value or value[0] in YAML_PLAIN_START or \
            (value[0] in "-?:" and (len(value) == 1 or value[1] in " \t")):
        return None
    if re.search(r":(?:[ \t]|$)", value):
        return None
    return value


def _yaml_value(text):
    """(kind, scalar) for the text after a key's colon or a sequence dash: ("none", None) for nothing or a
    comment, ("block", None) for a block scalar header, ("flow", text) for a flow sequence of scalars,
    ("scalar", decoded value) for a plain or quoted scalar; None outside the accepted subset."""
    if not text or text.startswith("#"):
        return "none", None
    if YAML_BLOCK_RE.fullmatch(text):
        return "block", None
    single = YAML_SINGLE_RE.fullmatch(text)
    if single is not None:
        return "scalar", single.group("value").replace("''", "'")
    double = YAML_DOUBLE_RE.fullmatch(text)
    if double is not None:
        return "scalar", double.group("value")
    if YAML_FLOW_SEQ_RE.fullmatch(text):
        return "flow", text
    plain = _plain_scalar(text)
    return None if plain is None else ("scalar", plain)


def yaml_pin_scalars(text, rel):
    """(line number, key, value) for every key in PIN_KEYS (any case) with its inline scalar, over a
    workflow or action file read by the accepted YAML subset; CannotEvaluate on any line outside it."""
    bad = YAML_FORBIDDEN_RE.search(text)
    if bad is not None:
        raise CannotEvaluate("%s:%d: character %r (a bare CR, a control character, U+2028, U+2029 or U+FEFF) "
                             "is outside the accepted YAML subset"
                             % (rel, text.count("\n", 0, bad.start()) + 1, bad.group()))
    out = []
    block = None
    scalar_column = None
    for number, line in enumerate(text.split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        if block is not None:
            if not line.strip(" \t"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            parent, content = block
            if content is None and indent > parent:
                block = (parent, indent)
                continue
            if content is not None and indent >= content:
                continue
            block = None
        if not line.strip(" \t") or line.lstrip(" ").startswith("#"):
            continue
        shape = YAML_LINE_RE.fullmatch(line)
        dash, body = shape.group("dash"), shape.group("body")
        column = len(shape.group("indent")) + len(dash or "")
        if scalar_column is not None and len(shape.group("indent")) > scalar_column:
            raise CannotEvaluate("%s:%d: a line indented under an inline scalar, which YAML reads as a continuation "
                                 "of that scalar (judged as written)" % (rel, number))
        scalar_column = None
        key = YAML_KEY_RE.fullmatch(body)
        if key is not None:
            name = key.group("key")
            name = name[1:-1] if name[0] in "'\"" else name
            kind = _yaml_value(key.group("value") or "")
        elif dash is not None:
            name, kind = None, _yaml_value(body)
            if kind is not None and kind[0] == "block":
                kind = None
        else:
            name, kind = None, None
        if kind is None:
            raise CannotEvaluate("%s:%d: a line outside the accepted YAML subset (judged as written)"
                                 % (rel, number))
        if kind[0] == "block":
            block = (column, None)
        elif kind[0] in ("scalar", "flow"):
            scalar_column = column if key is not None else len(shape.group("indent"))
        if kind[0] in ("scalar", "flow") and USES_KEYLIKE_RE.search(kind[1]):
            raise CannotEvaluate("%s:%d: a scalar holding uses followed by a colon (judged as written)"
                                 % (rel, number))
        if name is not None and name.lower() in PIN_KEYS:
            if kind[0] != "scalar" and not (name.lower() == "container" and kind[0] == "none"):
                raise CannotEvaluate("%s:%d: a %s key without an inline scalar value (a container key may hold a "
                                     "nested mapping)" % (rel, number, name))
            if kind[0] == "scalar":
                out.append((number, name.lower(), kind[1]))
    return out


def scan_workflows(root, files, rules):
    """Every uses, image and container key in .github/workflows/*.yml or *.yaml (any case) and in every
    action.yml or action.yaml."""
    findings = []
    counts = dict(uses=0, images=0)
    file_set = set(rel for rel, _ in files)
    depth = WORKFLOWS_REL.count("/") + 1
    targets = [(rel, path) for rel, path in files
               if (rel.startswith(WORKFLOWS_REL + "/") and rel.count("/") == depth
                   and rel.lower().endswith((".yml", ".yaml")))
               or rel.rsplit("/", 1)[-1].lower() in ACTION_FILE_NAMES]
    if not any(rel.startswith(WORKFLOWS_REL + "/") for rel, _ in targets):
        raise CannotEvaluate("%s holds no .yml or .yaml workflow (cannot evaluate the pin rule)" % WORKFLOWS_REL)
    for rel, path in targets:
        action_file = rel.rsplit("/", 1)[-1].lower() in ACTION_FILE_NAMES
        for number, key, value in yaml_pin_scalars(_text(path, rel), rel):
            if key == "uses":
                counts["uses"] += 1
                if value.startswith("./"):
                    local = os.path.normpath(value).replace(os.sep, "/")
                    pinned = local != ".." and not local.startswith("../")
                    what = "uses %r leaves the repository (a local path must stay inside it)" % value
                elif value.startswith("docker://"):
                    pinned = DOCKER_PINNED_RE.fullmatch(value) is not None
                    what = "uses %r is not pinned by @sha256 digest (a tag can move)" % value
                else:
                    remote = REMOTE_ACTION_RE.fullmatch(value)
                    pinned = remote is not None and COMMIT_SHA_RE.fullmatch(remote.group("ref")) is not None
                    what = "uses %r is not pinned to a full 40-hex commit SHA (a tag or branch can move)" % value
            else:
                counts["images"] += 1
                local = os.path.normpath(os.path.join(_dir_of(rel), value)).replace(os.sep, "/")
                pinned = IMAGE_PINNED_RE.fullmatch(value) is not None or \
                    (action_file and not value.startswith("/") and local in file_set)
                what = "%s %r is not pinned by @sha256 digest (a tag can move)" % (key, value)
            if not pinned and "action-pin" in rules:
                findings.append(("action-pin", rel, number, what))
    return findings, counts


def scan(root, rules=RULES, sys_module=sys, version_info=None, dispositions=VENDORED_OPTIONAL_IMPORTS,
         search_roots=IMPORT_SEARCH_ROOTS, allowlist=DYNAMIC_IMPORT_SITES, import_system=IMPORT_SYSTEM_MODULES,
         inert=INERT_IMPORT_SYSTEM_MEMBERS):
    """The whole verdict over root. Returns (findings, dispositioned lines, stats); raises CannotEvaluate."""
    stdlib = stdlib_names(sys_module)
    check_interpreter(load_floor(root), version_info if version_info is not None else sys.version_info)
    watched = watched_names(import_system, inert)
    files = _walk(Path(root))
    records, vendor_findings = _load_vendor(root, files)
    py_findings, stats, by_file, sites = scan_python(root, files, stdlib, records, rules, search_roots, watched)
    py_findings, shown = apply_dispositions(py_findings, by_file, records, dispositions, rules, stats)
    py_findings += check_dynamic_sites(sites, allowlist)
    stats.update(dynamic=len(sites), entries=len(allowlist), names=len(watched[0]), members=len(watched[1]))
    wf_findings, counts = scan_workflows(root, files, rules)
    stats.update(counts)
    stats["skipped"] = ", ".join(n for n in SKIPPED_ROOT_NAMES if os.path.lexists(Path(root) / n)) or "none"
    findings = [f for f in vendor_findings + py_findings + wf_findings if f[0] in rules]
    return sorted(set(findings)), shown, stats


def run(root):
    try:
        findings, shown, stats = scan(root)
    except CannotEvaluate as exc:
        print("IMPORT CLOSURE: CANNOT EVALUATE: %s" % exc, file=sys.stderr)
        return 2
    for line in shown:
        print(line)
    for rule, rel, lineno, message in findings:
        print("FINDING [%s] %s:%d: %s" % (rule, rel, lineno, message), file=sys.stderr)
    summary = ("%(files)d Python files, %(imports)d imports (%(stdlib)d standard library, %(repo)d in-repo, "
               "%(vendored)d vendored, %(dispositioned)d dispositioned), %(dynamic)d dynamic-import sites (references "
               "to %(names)d watched names and %(members)d import-system members; not resolved, a literal name "
               "excepted), each listed exactly by one of %(entries)d reviewed rows, %(uses)d action references, "
               "%(images)d container images; not walked at the root: %(skipped)s" % stats)
    if findings:
        print("IMPORT CLOSURE: FAILED: %d finding(s); %s" % (len(findings), summary), file=sys.stderr)
        return 1
    print("IMPORT CLOSURE: OK: %s" % summary)
    return 0


# ---------------------------------------------------------------- self-test

_PIN = "0123456789abcdef0123456789abcdef01234567"
_BS = "\\"
_VENDORED = (
    "lib/_vendor/pkg/__init__.py", "lib/_vendor/pkg/mod.py", "lib/_vendor/pkg/sub/__init__.py",
    "lib/_vendor/pkg/sub/leaf.py", "lib/_vendor/pkg/opt.py", "lib/_vendor/pkg/far.py",
    "lib/_vendor/licenses/pkg-MIT.txt",
)
_PROVENANCE = ('[package]\nname = "pkg"\nversion = "1.0"\npurl = "%s"\n'
               '[license]\nspdx = "%s"\nvendored_path = "lib/_vendor/licenses/pkg-MIT.txt"\n'
               '[vendored_tree]\nroot = "%s"\n')
_GOOD_PROVENANCE = _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT", "lib/_vendor/pkg/")
_MIT = ("MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this "
        "software, to deal in the Software without restriction.\n")
_A_PY = ("import os\nimport sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))\n"
         "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib', '_vendor'))\n"
         "import b\nimport importlib\nimportlib.import_module('json')\nfrom c import d\nimport pkg.mod\n"
         "__import__(os.environ['X'])\n")
_WORKFLOW = ("on:\n  push:\n    branches: [main]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
             "      - uses: actions/checkout@%s # v4\n      - uses: './local'\n      - name: x\n"
             "        run: |\n          echo uses: here\n\n          - uses: actions/checkout@v4\n"
             "      - \"name\": y\n        run: echo ok\n    container:\n      image: node@sha256:%s\n"
             % (_PIN, "ab" * 32))


def _fixture(base, extra=(), drop=(), rehash=True):
    """A clean fixture tree under base, then the extra rows (rel, str or bytes) written over it and the drop
    rels left out. The vendored manifest hashes the final bytes unless rehash is false (then the clean
    bytes, so an edited vendored file mismatches)."""
    rows = dict((
        (FLOOR_REL, 'python-floor = "%d.%d"\n' % tuple(sys.version_info[:2])),
        ("tools/a.py", _A_PY),
        ("tools/b.py", "import sys\n"),
        ("lib/c.py", "d = 1\n"),
        ("lib/_vendor/pkg/__init__.py", "from . import mod\n"),
        ("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\n"),
        ("lib/_vendor/pkg/sub/__init__.py", ""),
        ("lib/_vendor/pkg/sub/leaf.py", "from .. import mod\n"),
        ("lib/_vendor/pkg/opt.py", "try:\n    import shinylib\nexcept ImportError:\n    shinylib = None\n"),
        ("lib/_vendor/pkg/far.py", "import heavylib\n"),
        ("lib/_vendor/licenses/pkg-MIT.txt", _MIT),
        ("lib/_vendor/pkg-1.0.provenance.toml", _GOOD_PROVENANCE),
        (WORKFLOWS_REL + "/q.yml", _WORKFLOW),
    ))
    clean = dict(rows)
    rows.update(extra)
    hashed = rows if rehash else clean
    manifest = "".join("%s  %s\n" % (hashlib.sha256(hashed[rel] if isinstance(hashed[rel], bytes) else
                                                    hashed[rel].encode("utf-8")).hexdigest(), rel)
                       for rel in _VENDORED)
    rows.setdefault("lib/_vendor/pkg-1.0.manifest.sha256", manifest)
    for rel, text in rows.items():
        if rel in drop:
            continue
        path = Path(base) / rel
        os.makedirs(path.parent, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return Path(base)


_FIXTURE_DISPOSITIONS = (
    ("lib/_vendor/pkg/opt.py", "shinylib", "guarded-optional"),
    ("lib/_vendor/pkg/far.py", "heavylib", "unreached-module"),
)
_FIXTURE_ROOTS = (("tools/a.py", "lib"), ("tools/a.py", "lib/_vendor"))
_T_ROOTS = _FIXTURE_ROOTS + (("tools/t.py", "lib/_vendor"),)
_FIXTURE_SITES = (("tools/a.py", MODULE_SCOPE, ("6: import importlib", "7: importlib.import_module('json')",
                                                "10: __import__(os.environ['X'])"), "the fixture's own sites"),)
_T_VENDOR = "import os, sys\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib', '_vendor'))\n"
_WF = WORKFLOWS_REL + "/p.yml"
_REC = "lib/_vendor/pkg-1.0.provenance.toml"
_MANIFEST = "lib/_vendor/pkg-1.0.manifest.sha256"
_LICENCE = "lib/_vendor/licenses/pkg-MIT.txt"

# (name, rule, rel the finding must name, extra rows, dropped rels, scan keyword overrides, rehash). An extra
# row (_MANIFEST, None) appends a manifest row for a file that does not exist.
_RULE_VECTORS = (
    ("third-party import", "import-closure", "tools/t.py", (("tools/t.py", "import requests\n"),), (), {}, True),
    ("invented name in a lazy import", "import-closure", "tools/t.py",
     (("tools/t.py", "def f():\n    import reqeusts\n"),), (), {}, True),
    ("disconnected shadow: a same-named file in an unrelated directory", "import-closure", "tools/t.py",
     (("tools/t.py", "import requests\n"), ("unused/requests.py", "")), (), {}, True),
    ("disconnected shadow: a same-named package in a test directory", "import-closure", "tools/t.py",
     (("tools/t.py", "import requests\n"), ("tests/fixtures/requests/__init__.py", "x = 1\n")), (), {}, True),
    ("namespace directory beside the importer", "import-closure", "tools/t.py",
     (("tools/t.py", "import requests\n"), ("tools/requests/helper.py", "x = 1\n")), (), {}, True),
    ("sibling import from inside a package", "import-closure", "lib/pk/m.py",
     (("lib/pk/__init__.py", ""), ("lib/pk/m.py", "import n\n"), ("lib/pk/n.py", "")), (), {}, True),
    ("vendored file importing beside itself", "import-closure", "lib/_vendor/pkg/mod.py",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nimport opt\n"),), (), {}, True),
    # A literal name at a dynamic-import call is resolved like an import statement, at an allowlisted site too.
    ("literal dynamic import of a third-party name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module('yaml')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("1: import importlib", "2: importlib.import_module('yaml')"), "r"),)), True),
    ("__import__ of a third-party name inside an allowlisted function", "import-closure", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__('requests')",), "r"),)), True),
    ("__import__ level keyword 0 at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', level=0)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("1: __import__('requests', level=0)",), "r"),)),
     True),
    ("__import__ fifth argument 0 at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', None, None, (), 0)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("1: __import__('requests', None, None, (), 0)",), "r"),)), True),
    ("_gcd_import of a third-party name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib._bootstrap._gcd_import('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("1: import importlib", "2: importlib._bootstrap._gcd_import('requests')"),
                                       "r"),)), True),
    ("literal dynamic import of a third-party name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module('json')\n"),), (), {}, True),
    ("aliased importlib", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib as il\nil.import_module('json')\n"),), (), {}, True),
    ("aliased import_module", "dynamic-import", "tools/t.py",
     (("tools/t.py", "from importlib import import_module as load\nload('requests')\n"),), (), {}, True),
    ("import_module name keyword", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module(name='json')\n"),), (), {}, True),
    ("__import__ through builtins", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\nbuiltins.__import__('json')\n"),), (), {}, True),
    ("__import__ as an importlib attribute", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.__import__('json')\n"),), (), {}, True),
    ("find_spec through importlib.util", "dynamic-import", "tools/t.py",
     (("tools/t.py", "from importlib import util\nutil.find_spec('json')\n"),), (), {}, True),
    ("getattr with a literal name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\ngetattr(importlib, 'import_module')('json')\n"),), (), {}, True),
    # Round-2 reproductions: each escaped the gate before dynamic imports were refused outside the allowlist.
    ("function-local alias beside a module-level __import__", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def unrelated():\n    from builtins import print as __import__\n__import__('json')\n"),),
     (), {}, True),
    ("__import__ fromlist keyword reaching an unreached module", "dynamic-import", "tools/t.py",
     (("tools/t.py", _T_VENDOR + "import pkg\n__import__('pkg', fromlist=['far'])\n"),), (),
     dict(search_roots=_T_ROOTS), True),
    ("__import__ positional fromlist reaching an unreached module", "dynamic-import", "tools/t.py",
     (("tools/t.py", _T_VENDOR + "import pkg\n__import__('pkg', None, None, ['far'])\n"),), (),
     dict(search_roots=_T_ROOTS), True),
    ("__import__ computed fromlist", "dynamic-import", "tools/t.py",
     (("tools/t.py", "__import__('json', fromlist=names)\n"),), (), {}, True),
    ("__import__ through __builtins__", "dynamic-import", "tools/t.py",
     (("tools/t.py", "__builtins__.__import__('json')\n"),), (), {}, True),
    ("PathFinder spec executed by its loader", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib.machinery, importlib.util\n"
       "s = importlib.machinery.PathFinder.find_spec('json')\nm = importlib.util.module_from_spec(s)\n"
       "s.loader.exec_module(m)\n"),), (), {}, True),
    ("importlib.resources by package name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib.resources\nimportlib.resources.files('requests')\n"),), (), {}, True),
    ("pydoc locate by name", "dynamic-import", "tools/t.py", (("tools/t.py", "import pydoc\npydoc.locate('json')\n"),),
     (), {}, True),
    ("importlib through sys.modules", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import sys\nsys.modules['importlib'].import_module('json')\n"),), (), {}, True),
    ("importlib bound to another name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nm = importlib\nm.import_module('json')\n"),), (), {}, True),
    ("package __path__ extended outside the tree", "dynamic-import", "tools/pk/__init__.py",
     (("tools/pk/__init__.py", "__path__.append('/usr/lib/python3/dist-packages')\n"),
      ("tools/t.py", "import pk.yaml\n")), (), {}, True),
    ("pkgutil.extend_path on a package", "dynamic-import", "tools/pk/__init__.py",
     (("tools/pk/__init__.py", "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n"),
      ("tools/t.py", "import pk.thing\n")), (), {}, True),
    ("getattr with a literal string name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, '__import__')('json')\n"),), (), {}, True),
    ("vars with a literal string key", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\nvars(builtins)['__import__']('json')\n"),), (), {}, True),
    ("getattr with a bytes literal name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, b'__import__'.decode())('requests')\n"),), (), {}, True),
    ("string naming an importlib submodule", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import sys\nsys.modules['importlib.util']\n"),), (), {}, True),
    # Round-3 reproductions: each passed while a row allowed its names throughout a function, or while the
    # import system's own entry points were not watched.
    ("default argument evaluated outside its function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f(x=__import__('json')):\n    return x\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("1: __import__('json')",), "reviewed body of f"),)), True),
    ("decorator evaluated outside its function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "@((lambda x: lambda f: f)(__import__('json')))\ndef f():\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("1: __import__('json')",), "reviewed body of f"),)), True),
    ("attribute decorator evaluated outside its function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "@__import__('json').loads\ndef f():\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("1: __import__('json').loads",), "r"),)), True),
    ("class base evaluated outside its class", "dynamic-import", "tools/t.py",
     (("tools/t.py", "class C(__import__('json').JSONDecoder):\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "C", ("1: __import__('json').JSONDecoder",), "r"),)), True),
    ("importer returned from a function with a reviewed call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    __import__('json')\n    return __import__\n\ndef g():\n    return f()('json')\n\n"
       "result = g().__name__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__('json')",), "reviewed body of f"),)), True),
    ("lambda returned from a function with a reviewed call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    __import__('json')\n    return lambda: __import__('json')\n\ng = f()\n"
       "result = g().__name__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__('json')",), "reviewed body of f"),)), True),
    ("_gcd_import through an allowlisted importlib", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib.util\nimportlib._bootstrap._gcd_import('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("1: import importlib.util",), "r"),)), True),
    ("_frozen_importlib named directly", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import _frozen_importlib\n_frozen_importlib._gcd_import('json')\n"),), (), {}, True),
    ("frozen importlib module imported by its frozen name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import _frozen_importlib_external\n"),), (), {}, True),
    ("_frozen_importlib through sys.modules", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import sys\nsys.modules['_frozen_importlib']._gcd_import('json')\n"),), (), {}, True),
    ("_imp imported", "dynamic-import", "tools/t.py", (("tools/t.py", "import _imp\n"),), (), {}, True),
    # The allowlist: a row allows exact references (lines and text) in its own file and scope, and is held live.
    ("allowlisted reference repeated in another function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json')\ndef g():\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__('json')",), "r"),)), True),
    ("allowlist row naming a method by its bare name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "class C:\n    def g(self):\n        return __import__\ndef g():\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "g", ("3: __import__",), "r"),)), True),
    ("module-level row for a site inside a function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("2: __import__",), "r"),)), True),
    ("allowlisted reference moved to another line", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    x = 1\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__('json')",), "r"),)), True),
    ("allowlisted line holding another expression", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json').dumps\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__('json')",), "r"),)), True),
    ("stale allowlist row", "dynamic-import", "tools/t.py", (("tools/t.py", "def f():\n    return 1\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__",), "r"),)), True),
    ("allowlist row listing a reference that does not occur", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__", "3: x.importlib"), "r"),)), True),
    ("allowlist row with no reason", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__",), " "),)), True),
    ("allowlist row listing no reference", "dynamic-import", "tools/t.py", (("tools/t.py", "x = 1\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", (), "r"),)), True),
    ("allowlist row with a malformed reference", "dynamic-import", "tools/t.py", (("tools/t.py", "x = 1\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("two: x",), "r"),)), True),
    ("allowlist row repeated", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2: __import__",), "r"),
                                      ("tools/t.py", "f", ("2: __import__",), "s"))), True),
    ("relative import naming no module beside a non-package file", "import-closure", "tools/t.py",
     (("tools/t.py", "from . import nothere\n"),), (), {}, True),
    ("relative from-import of a missing module", "import-closure", "lib/_vendor/pkg/sub/leaf.py",
     (("lib/_vendor/pkg/sub/leaf.py", "from .. import mod\nfrom .gone import x\n"),), (), {}, True),
    ("search-root row with no sys.path call", "import-closure", "tools/t.py",
     (("tools/t.py", "import pkg\n_ = ('lib', '_vendor')\n"),), (), dict(search_roots=_T_ROOTS), True),
    ("search-root row whose root the file never names", "import-closure", "tools/t.py",
     (("tools/t.py", "import sys\nsys.path.append('lib')\nimport pkg\n"),), (), dict(search_roots=_T_ROOTS),
     True),
    ("stale search-root row", "import-closure", "tools/t.py", (("tools/t.py", _T_VENDOR),), (),
     dict(search_roots=_T_ROOTS), True),
    ("search-root row naming no scanned file", "import-closure", "tools/t.py", (), (),
     dict(search_roots=_T_ROOTS), True),
    ("search-root row whose root is no directory", "import-closure", "tools/t.py",
     (("tools/t.py", _T_VENDOR.replace("'lib', '_vendor'", "'gone'") + "import os\n"),), (),
     dict(search_roots=_FIXTURE_ROOTS + (("tools/t.py", "gone"),)), True),
    ("search-root row naming the file's own directory", "import-closure", "tools/t.py",
     (("tools/t.py", "import sys\nsys.path.append('tools')\nimport b\n"),), (),
     dict(search_roots=_FIXTURE_ROOTS + (("tools/t.py", "tools"),)), True),
    ("Python under a nested venv directory", "import-closure", "tools/venv/evil.py",
     (("tools/venv/evil.py", "import requests\n"),), (), {}, True),
    ("relative import escaping the repo", "relative-escape", "tools/t.py",
     (("tools/t.py", "from ... import x\n"),), (), {}, True),
    ("relative import escaping its vendored root", "relative-escape", "lib/_vendor/pkg/sub/leaf.py",
     (("lib/_vendor/pkg/sub/leaf.py", "from ... import c\n"),), (), {}, True),
    ("unpinned action by tag", "action-pin", _WF, ((_WF, "steps:\n  - uses: actions/checkout@v4\n"),), (), {},
     True),
    ("unpinned action by short SHA", "action-pin", _WF,
     ((_WF, "steps:\n  - uses: \"actions/checkout@0123456\"\n"),), (), {}, True),
    ("unpinned action under a quoted key", "action-pin", _WF,
     ((_WF, "steps:\n  - 'uses': actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action under a capitalised key", "action-pin", _WF,
     ((_WF, "steps:\n  - Uses: actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action as a later key of an item", "action-pin", _WF,
     ((_WF, "steps:\n  - name: a\n    uses: actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action after a block scalar ends", "action-pin", _WF,
     ((_WF, "steps:\n  - run: |\n      echo a\n  - uses: actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action after an empty block scalar", "action-pin", _WF,
     ((_WF, "steps:\n  - run: >-\n    uses: actions/checkout@v4\n"),), (), {}, True),
    ("quoted uses value with inner whitespace", "action-pin", _WF,
     ((_WF, "steps:\n  - uses: 'actions/checkout@%s x'\n" % _PIN),), (), {}, True),
    ("unpinned docker image", "action-pin", WORKFLOWS_REL + "/p.YAML",
     ((WORKFLOWS_REL + "/p.YAML", "steps:\n  - uses: docker://alpine:3\n"),), (), {}, True),
    ("unpinned action in a local action file", "action-pin", "acts/x/action.yml",
     (("acts/x/action.yml", "runs:\n  steps:\n    - uses: actions/cache@main\n"),), (), {}, True),
    ("unpinned job container", "action-pin", _WF, ((_WF, "jobs:\n  t:\n    container: node:18\n"),), (), {},
     True),
    ("unpinned service image", "action-pin", _WF,
     ((_WF, "jobs:\n  t:\n    services:\n      db:\n        image: postgres:latest\n"),), (), {}, True),
    ("workflow image naming a walked file", "action-pin", _WF,
     ((_WF, "jobs:\n  t:\n    container: ../../tools/b.py\n"),), (), {}, True),
    ("action file image naming no walked file", "action-pin", "acts/y/action.yml",
     (("acts/y/action.yml", "runs:\n  using: docker\n  image: Dockerfile\n"),), (), {}, True),
    ("local uses path leaving the repository", "action-pin", _WF, ((_WF, "steps:\n  - uses: ./../../elsewhere\n"),),
     (), {}, True),
    ("vendored file missing provenance", "vendor-provenance", "lib/_vendor/pkg/extra.py",
     (("lib/_vendor/pkg/extra.py", "x = 1\n"),), (), {}, True),
    ("vendored package with no provenance record", "vendor-provenance", "tools/t.py",
     (("lib/_vendor/other/__init__.py", "x = 1\n"), ("tools/t.py", _T_VENDOR + "import other\n")), (),
     dict(search_roots=_T_ROOTS), True),
    ("vendored Python under no recorded root", "vendor-provenance", "lib/_vendor/stray.py",
     (("lib/_vendor/stray.py", "x = 1\n"),), (), {}, True),
    ("vendored record with no licence", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "", "lib/_vendor/pkg/")),), (), {}, True),
    ("vendored record with no version", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace('version = "1.0"\n', "")),), (), {}, True),
    ("vendored record with no purl", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace('purl = "pkg:pypi/pkg@1.0"\n', "")),), (), {}, True),
    ("purl naming another package", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/other@1.0", "MIT", "lib/_vendor/pkg/")),), (), {}, True),
    ("invalid SPDX identifier", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "not-a-license", "lib/_vendor/pkg/")),), (), {}, True),
    ("SPDX expression mixing AND and OR", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT AND ISC OR 0BSD", "lib/_vendor/pkg/")),), (), {}, True),
    ("SPDX expression with an unknown operator", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT WITH 0BSD", "lib/_vendor/pkg/")),), (), {}, True),
    ("SPDX expression ending in an operator", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT AND", "lib/_vendor/pkg/")),), (), {}, True),
    ("empty licence text", "vendor-provenance", _LICENCE, ((_LICENCE, b""),), (), {}, True),
    ("licence text lacking a named licence", "vendor-provenance", _LICENCE,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT OR Apache-2.0", "lib/_vendor/pkg/")),), (), {}, True),
    ("licence text not listed in the manifest", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace("licenses/pkg-MIT.txt", "licenses/absent.txt")),), (), {}, True),
    ("vendored root not named for its package", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace('name = "pkg"', 'name = "other"').replace("pypi/pkg@", "pypi/other@")),), (),
     {}, True),
    ("licence text outside its _vendor directory", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace(_LICENCE, "COPYING")), ("COPYING", _MIT), ("+manifest", "COPYING")), (), {},
     True),
    ("vendored root nested too deep", "vendor-provenance", "lib/_vendor/other-1.0.provenance.toml",
     (("lib/_vendor/other-1.0.provenance.toml",
       _PROVENANCE.replace('name = "pkg"', 'name = "sub"') % ("pkg:pypi/sub@1.0", "MIT", "lib/_vendor/o/sub/")),
      ("lib/_vendor/other-1.0.manifest.sha256", "%s  %s\n" % (hashlib.sha256(_MIT.encode("utf-8")).hexdigest(),
                                                               _LICENCE))), (), {}, True),
    ("vendored bytes differ from the recorded digest", "vendor-provenance", "lib/_vendor/pkg/mod.py",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nimport os\n"),), (), {}, False),
    ("listed vendored file missing", "vendor-provenance", _MANIFEST, ((_MANIFEST, None),), (), {}, True),
    ("vendored manifest missing", "vendor-provenance", _REC, (), (_MANIFEST,), {}, True),
    ("stale disposition row", "disposition", "lib/_vendor/pkg/mod.py", (), (),
     dict(dispositions=_FIXTURE_DISPOSITIONS + (("lib/_vendor/pkg/mod.py", "gone", "guarded-optional"),)), True),
    ("guarded-optional row over an unguarded import", "disposition", "lib/_vendor/pkg/opt.py",
     (("lib/_vendor/pkg/opt.py", "import shinylib\n"),), (), {}, True),
    ("guarded-optional row whose handler is not ImportError", "disposition", "lib/_vendor/pkg/opt.py",
     (("lib/_vendor/pkg/opt.py", "try:\n    import shinylib\nexcept ValueError:\n    shinylib = None\n"),), (),
     {}, True),
    ("unreached-module row whose module is reached", "disposition", "lib/_vendor/pkg/far.py",
     (("tools/t.py", _T_VENDOR + "from pkg import far\n"),), (), dict(search_roots=_T_ROOTS), True),
    ("import in a try's else is not guarded", "disposition", "lib/_vendor/pkg/opt.py",
     (("lib/_vendor/pkg/opt.py",
       "try:\n    import os\nexcept ImportError:\n    os = None\nelse:\n    import shinylib\n"),), (), {}, True),
    ("relative import reaching an unreached module", "disposition", "lib/_vendor/pkg/far.py",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nfrom . import far\n"),), (), {}, True),
    ("disposition row outside a vendored root", "disposition", "tools/t.py",
     (("tools/t.py", "try:\n    import shinylib\nexcept ImportError:\n    pass\n"),), (),
     dict(dispositions=_FIXTURE_DISPOSITIONS + (("tools/t.py", "shinylib", "guarded-optional"),)), True),
    ("disposition row with an unknown condition", "disposition", "lib/_vendor/pkg/far.py", (), (),
     dict(dispositions=(_FIXTURE_DISPOSITIONS[0], ("lib/_vendor/pkg/far.py", "heavylib", "optional"))), True),
)

def _name_vectors(names, members):
    """One rule vector per watched name and member, each its only site: removing it from the watched set must turn
    its vector green (the self-test removes each in turn)."""
    return tuple(("watched name " + name, "dynamic-import", "tools/t.py",
                  (("tools/t.py", "def f(o):\n    return o.%s\n" % name),), (), {}, True)
                 for name in sorted(names | members))


# (name, source, the sites dynamic_sites must find, exactly, each as SCOPE | REFERENCE)
_SITE_VECTORS = (
    ("alias in a function, use at module level",
     "def unrelated():\n    from builtins import print as __import__\n__import__('json')\n",
     ("unrelated | 2: from builtins import print as __import__", "<module> | 3: __import__('json')")),
    ("submodule import and from-import", "import importlib.util as u\nfrom importlib import machinery\n",
     ("<module> | 1: import importlib.util as u", "<module> | 2: from importlib import machinery")),
    ("parameter and attribute in a nested class method",
     "class A:\n    class B:\n        def m(self, run_path=None):\n            return self.find_spec\n",
     ("A.B.m | 3: run_path", "A.B.m | 4: self.find_spec")),
    ("whole dotted string and bytes literals only", "x = 'importlib.util'\ny = 'use importlib'\nz = b'__import__'\n",
     ("<module> | 1: 'importlib.util'", "<module> | 3: b'__import__'")),
    ("global and keyword names", "def f():\n    global imp\n    g(run_module=1)\n",
     ("f | 2: global imp", "f | 3: run_module=1")),
    ("names outside the set", "import os\nos.path.join('a')\nimporter = 1\n", ()),
    ("async def scope", "async def f():\n    return __import__('json')\n", ("f | 2: __import__('json')",)),
    ("definition-time expressions in the enclosing scope",
     "class C(__import__('json').JSONDecoder):\n    @x.find_spec\n    def m(self, a: importlib = __import__):\n"
     "        return 1\n",
     ("<module> | 1: __import__('json').JSONDecoder", "C | 2: x.find_spec", "C | 3: __import__", "C | 3: importlib")),
    ("a def's name in the enclosing scope", "class C:\n    def find_spec(self):\n        pass\n",
     ("C | 2: def find_spec",)),
    ("lambda body in a scope of its own", "f = lambda: __import__('json')\n", ("<lambda> | 1: __import__('json')",)),
    ("one reference per attribute chain and call",
     "s = importlib.util.spec_from_file_location('x',\n    'y')\n",
     ("<module> | 1-2: importlib.util.spec_from_file_location('x', 'y')",)),
    ("import-system members as attributes and literals only",
     "def _gcd_import(_load):\n    return _gcd_import(_load)\nx = o._load\ny = '_gcd_import'\n",
     ("<module> | 3: o._load", "<module> | 4: '_gcd_import'")),
)


class _NoStdlibSys:
    """A sys stand-in without stdlib_module_names: the standard-library source is unavailable."""


class _EmptyStdlibSys:
    stdlib_module_names = frozenset()


class _BlankStdlibSys:
    stdlib_module_names = frozenset(("os", ""))


class _ListStdlibSys:
    stdlib_module_names = ["os"]


# (name, extra rows, dropped rels, scan keyword overrides, special)
_CANNOT_VECTORS = (
    ("standard-library source unavailable", (), (), dict(sys_module=_NoStdlibSys()), None),
    ("standard-library source empty", (), (), dict(sys_module=_EmptyStdlibSys()), None),
    ("standard-library source holding an empty name", (), (), dict(sys_module=_BlankStdlibSys()), None),
    ("standard-library source not a frozenset", (), (), dict(sys_module=_ListStdlibSys()), None),
    ("interpreter is not the floor version", (), (), dict(version_info=(sys.version_info[0], 99)), None),
    ("floor source missing", (), (FLOOR_REL,), dict(), None),
    ("floor not MAJOR.MINOR", ((FLOOR_REL, 'python-floor = "3"\n'),), (), dict(), None),
    ("unparseable Python file", (("tools/t.py", "def (:\n"),), (), dict(), None),
    ("Python file that is not UTF-8", (("tools/t.py", b"x = '\xff'\n"),), (), dict(), None),
    ("Python file with a NUL byte", (("tools/t.py", b"x = 1\x00\n"),), (), dict(), None),
    ("unreadable Python file", (("tools/t.py", "x = 1\n"),), (), dict(), "unreadable-file"),
    ("symlinked Python file", (), (), dict(), "symlink"),
    ("FIFO named like a Python file", (), (), dict(), "fifo"),
    ("symlinked directory", (), (), dict(), "symlinked-dir"),
    ("unlistable directory", (("tools/locked/x.txt", "x\n"),), (), dict(), "unlistable"),
    ("import present in two search roots", (("tools/c.py", "d = 2\n"),), (), dict(), None),
    ("in-repo module named like a standard-library module",
     (("tools/json.py", "x = 1\n"), ("tools/t.py", "import json\n")), (), dict(), None),
    ("uses inside a flow mapping", ((_WF, "steps:\n  - {uses: a/b@v1}\n"),), (), dict(), None),
    ("uses value in a block scalar", ((_WF, "steps:\n  - uses: >\n      a/b@v1\n"),), (), dict(), None),
    ("uses key with its value on the next line", ((_WF, "steps:\n  - uses:\n      a/b@v1\n"),), (), dict(), None),
    ("escaped key in a workflow",
     ((_WF, "jobs:\n  x:\n    steps:\n      - \"u" + _BS + "u0073es\": actions/checkout@v4\n"),), (), dict(), None),
    ("escaped key in an action file",
     (("acts/x/action.yml", "runs:\n  steps:\n    - \"" + _BS + "x75ses\": a/b@v1\n"),), (), dict(), None),
    ("alias as a key", ((_WF, "x-k: &k \"" + _BS + "x75ses\"\nsteps:\n  - *k : actions/checkout@v4\n"),), (),
     dict(), None),
    ("complex key", ((_WF, "steps:\n  - ? uses\n    : actions/checkout@v4\n"),), (), dict(), None),
    ("bare CR line break after a comment", ((_WF, "steps:\n  # pin\r  - uses: actions/checkout@v4\n"),), (),
     dict(), None),
    ("U+2028 after a comment", ((_WF, "steps:\n  # c" + chr(0x2028) + "  - uses: a/b@v1\n"),), (), dict(), None),
    ("anchored value", ((_WF, "steps:\n  - uses: &a actions/checkout@v4\n"),), (), dict(), None),
    ("flow sequence holding a mapping", ((_WF, "jobs:\n  t:\n    with: [image: node:18]\n"),), (), dict(), None),
    ("uses key whose value is a flow sequence", ((_WF, "steps:\n  - uses: [a/b]\n"),), (), dict(), None),
    ("escaped uses value", ((_WF, "steps:\n  - uses: \"actions/checkout" + _BS + "x40v4\"\n"),), (), dict(), None),
    ("multi-line plain scalar", ((_WF, "steps:\n  - name: a\n      b\n"),), (), dict(), None),
    ("bare scalar line", ((_WF, "steps: []\nplain text\n"),), (), dict(), None),
    ("plain scalar continued by a dash line",
     ((_WF, "jobs:\n  t:\n    container: node@sha256:%s\n      - x\n" % ("ab" * 32)),), (), dict(), None),
    ("uses value continued by dash lines", ((_WF, "steps:\n  - uses: a/b@%s\n        -\n       - x\n" % _PIN),), (),
     dict(), None),
    ("sequence item continued by a deeper dash line", ((_WF, "steps:\n  - a@v1\n    - x\n"),), (), dict(), None),
    ("unterminated quoted scalar", ((_WF, "steps:\n  - name: 'a\n      b'\n"),), (), dict(), None),
    ("document marker", ((_WF, "---\nsteps: []\n"),), (), dict(), None),
    ("nested sequence dash", ((_WF, "steps:\n  - - uses: actions/checkout@v4\n"),), (), dict(), None),
    ("block scalar as a sequence item", ((_WF, "steps:\n  - |\n    uses: a/b@v1\n"),), (), dict(), None),
    ("plain scalar holding a uses key", ((_WF, "steps:\n  - uses:actions/checkout@v4\n"),), (), dict(), None),
    ("plain value holding a colon and space", ((_WF, "steps:\n  - run: echo a: b\n"),), (), dict(), None),
    ("plain value starting with an indicator", ((_WF, "steps:\n  - run: @x\n"),), (), dict(), None),
    ("plain value that is a lone dash", ((_WF, "steps:\n  - run: -\n"),), (), dict(), None),
    ("tab-indented line", ((_WF, "steps:\n\t- uses: actions/checkout@v4\n"),), (), dict(), None),
    ("image key with a nested value", ((_WF, "jobs:\n  t:\n    container:\n      image: node:18\n"
                                              "    services:\n      db:\n        image:\n"),), (), dict(), None),
    ("workflow that is not UTF-8", ((_WF, b"steps: \xff\n"),), (), dict(), None),
    ("malformed provenance record", ((_REC, "[package\n"),), (), dict(), None),
    ("malformed manifest row", ((_MANIFEST, "abc lib/_vendor/pkg/mod.py\n"),), (), dict(), None),
    ("duplicate manifest path", (), (), dict(), "duplicate-row"),
    ("licence text that is not UTF-8", ((_LICENCE, b"MIT \xff\n"),), (), dict(), None),
    ("no workflow to evaluate", (), (WORKFLOWS_REL + "/q.yml",), dict(), None),
    ("import-system module that does not load", (), (),
     dict(import_system=IMPORT_SYSTEM_MODULES + ("no_such_import_system_module",)), None),
    ("inert member that is no member", (), (),
     dict(inert=INERT_IMPORT_SYSTEM_MEMBERS + (("no_such_member", "r"),)), None),
)


def _vector_cases(tmp):
    """Build every vector's fixture tree once. Returns [(name, expected, rule, rel, base, kwargs, special)]:
    expected is 1 (a finding of rule at rel), 2 (cannot evaluate) or "sites" (base is then the source)."""
    cases = []
    for index, (name, rule, at, extra, drop, kwargs, rehash) in enumerate(_RULE_VECTORS +
                                                                          _name_vectors(*watched_names())):
        base = _fixture(tmp / ("v%d" % index), tuple(row for row in extra if row[1] is not None and
                                                     not row[0].startswith("+")), drop, rehash)
        manifest = base / _MANIFEST
        if (_MANIFEST, None) in extra:
            manifest.write_bytes(manifest.read_bytes() + ("%s  lib/_vendor/pkg/gone.py\n" % ("0" * 64)).encode())
        for listed in (row[1] for row in extra if row[0] == "+manifest"):
            digest = hashlib.sha256((base / listed).read_bytes()).hexdigest()
            manifest.write_bytes(manifest.read_bytes() + ("%s  %s\n" % (digest, listed)).encode())
        cases.append((name, 1, rule, at, base, kwargs, None))
    for index, (name, extra, drop, kwargs, special) in enumerate(_CANNOT_VECTORS):
        base = _fixture(tmp / ("c%d" % index), extra, drop)
        if special == "symlink":
            os.symlink(base / "tools" / "b.py", base / "tools" / "t.py")
        elif special == "fifo":
            os.mkfifo(base / "tools" / "t.py")
        elif special == "symlinked-dir":
            os.symlink(base / "lib", base / "tools" / "linked")
        elif special == "duplicate-row":
            manifest = base / _MANIFEST
            manifest.write_bytes(manifest.read_bytes() + manifest.read_bytes().split(b"\n", 1)[0] + b"\n")
        cases.append((name, 2, None, None, base, kwargs, special))
    for name, source, sites in _SITE_VECTORS:
        cases.append((name, "sites", sites, None, source, dict(), None))
    return cases


def _scan_rc(module, base, rules=None, special=None, **kwargs):
    kwargs.setdefault("dispositions", _FIXTURE_DISPOSITIONS)
    kwargs.setdefault("search_roots", _FIXTURE_ROOTS)
    kwargs.setdefault("allowlist", _FIXTURE_SITES)
    original = os.scandir
    original_read = Path.__dict__.get("read_bytes")
    inherited_read = Path.read_bytes

    def _unreadable(path):
        if os.fspath(path).endswith(os.sep + "t.py"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return inherited_read(path)

    def _locked(path="."):
        if os.fspath(path).endswith(os.sep + "locked"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return original(path)
    if special == "unlistable":
        os.scandir = _locked
    if special == "unreadable-file":
        Path.read_bytes = _unreadable
    try:
        findings, _shown, _stats = module.scan(base, rules=module.RULES if rules is None else rules, **kwargs)
    except module.CannotEvaluate:
        return 2, []
    finally:
        os.scandir = original
        if original_read is None:
            if "read_bytes" in Path.__dict__:
                del Path.read_bytes
        else:
            Path.read_bytes = original_read
    return (1 if findings else 0), findings


def _case_failure(module, case):
    """None if the case holds against module (this gate or a mutant of it), else what went wrong."""
    name, expected, rule, at, base, kwargs, special = case
    if expected == "sites":
        got = tuple("%s | %s" % (site[1], site[0]) for site in module.dynamic_sites(ast.parse(base),
                                                                                   *module.watched_names()))
        return None if got == rule else "expected sites %r, got %r" % (rule, got)
    rc, findings = _scan_rc(module, base, special=special, **dict(kwargs))
    if expected == 2:
        return None if rc == 2 else "expected cannot-evaluate (2), got %d %r" % (rc, findings)
    if rc != 1 or not any(f[0] == rule and f[1] == at for f in findings):
        return "expected a %s finding at %s, got rc %d %r" % (rule, at, rc, findings)
    rc_off, findings_off = _scan_rc(module, base, rules=tuple(r for r in RULES if r != rule), special=special,
                                    **dict(kwargs))
    if rc_off != 0:
        return "with %s removed the vector must pass (it fails only by that rule), got rc %d %r" % (
            rule, rc_off, findings_off)
    return None


_SELF_TEST_MARKER = "\n# " + "-" * 64 + " self-test\n"

# Each mutant removes one check (or one fail-closed path) from the gate half of this file's source, the part
# before the self-test: (mutant id, the exact source text, its replacement, the vector that must then fail).
# The self-test loads every mutant as a scratch module and requires its named vector to fail.
MUTANTS = (
    # the walk and the inputs
    ("symlinked-dir-refused", "if os.path.islink(os.path.join(dirpath, d)):", "if False:", "symlinked directory"),
    ("unlistable-dir-refused", "os.walk(root, onerror=_raise)", "os.walk(root)", "unlistable directory"),
    ("skip-root-anchored", "if not (top and d in SKIPPED_ROOT_NAMES)", "if not (d in SKIPPED_ROOT_NAMES)",
     "Python under a nested venv directory"),
    ("unreadable-refused", 'raise CannotEvaluate("%s: unreadable: %s" % (rel, exc))', 'return b""',
     "unreadable Python file"),
    ("regular-file-only", "if not stat.S_ISREG(mode):", "if False:", "symlinked Python file"),
    ("utf8-refused", 'raise CannotEvaluate("%s: not UTF-8: %s" % (rel, exc))',
     'return _regular_bytes(path, rel).decode("utf-8", "replace")',
     "workflow that is not UTF-8"),
    ("toml-refused", 'raise CannotEvaluate("%s: does not parse as TOML: %s" % (rel, exc))', "return dict()",
     "malformed provenance record"),
    ("python-parse-refused", 'raise CannotEvaluate("%s: does not parse: %s" % (rel, exc))', "continue",
     "unparseable Python file"),
    ("stdlib-frozenset", "if not isinstance(names, frozenset) or not names or", "if not names or",
     "standard-library source not a frozenset"),
    ("stdlib-nonempty", "if not isinstance(names, frozenset) or not names or",
     "if not isinstance(names, frozenset) or",
     "standard-library source empty"),
    ("stdlib-names-valid", " or not all(isinstance(n, str) and n for n in names):", ":",
     "standard-library source holding an empty name"),
    ("floor-shape", 'raise CannotEvaluate("%s: python-floor is absent or not MAJOR.MINOR" % FLOOR_REL)',
     "return 3, 14", "floor not MAJOR.MINOR"),
    ("floor-equal", "if running != tuple(floor):", "if False:", "interpreter is not the floor version"),
    # search roots
    ("package-dir-not-a-root", 'roots = [] if _join(own, "__init__.py") in file_set else [own]', "roots = [own]",
     "sibling import from inside a package"),
    ("vendored-file-roots", "        return [vdir]\n", "        return [vdir, _dir_of(rel)]\n",
     "vendored file importing beside itself"),
    ("regular-module-only", "return namespace and any(", "return any(", "namespace directory beside the importer"),
    ("search-roots-only", "hits = [r for r in roots if _module_present(file_set, r, top)]",
     "hits = [r for r in sorted(set(_dir_of(f) for f in file_set)) if _module_present(file_set, r, top)][:1]",
     "disconnected shadow: a same-named file in an unrelated directory"),
    ("ambiguity-refused", "if len(hits) + (top in stdlib) > 1:", "if False:", "import present in two search roots"),
    ("stdlib-collision-refused", "if len(hits) + (top in stdlib) > 1:", "if len(hits) > 1:",
     "in-repo module named like a standard-library module"),
    ("unresolved-is-finding", "                if top in stdlib:\n                    stats[\"stdlib\"] += 1\n"
     "                else:\n", "                if True:\n                    stats[\"stdlib\"] += 1\n"
     "                else:\n", "third-party import"),
    ("row-file-scanned", 'return "names no scanned Python file"', "return None",
     "search-root row naming no scanned file"),
    ("row-beyond-own", "    if not parts:\n", "    if False:\n", "search-root row naming the file's own directory"),
    ("row-sys-path-call", "    if not inserts:\n", "    if False:\n", "search-root row with no sys.path call"),
    ("row-root-named", "    if unnamed:\n", "    if False:\n", "search-root row whose root the file never names"),
    ("row-live", "    if (rel, sroot) not in used:\n", "    if False:\n", "stale search-root row"),
    # relative imports
    ("relative-module-exists", "                if not ok:\n", "                if False:\n",
     "relative from-import of a missing module"),
    ("relative-escape-repo", 'escaped = base_rel == ".." or base_rel.startswith("../") or', "escaped = False or",
     "relative import escaping the repo"),
    ("relative-escape-vendored",
     "(limit is not None and base_rel != limit and not base_rel.startswith(limit + \"/\"))",
     "False", "relative import escaping its vendored root"),
    # dynamic imports
    ("site-identifiers", "if not isinstance(item, str) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "if not isinstance(item, str) or not isinstance(node, ast.Constant) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "function-local alias beside a module-level __import__"),
    ("site-string-literals", "if not isinstance(item, str) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "if not isinstance(item, str) or isinstance(node, ast.Constant) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "getattr with a literal string name"),
    ("site-bytes-literals", 'item = item.decode("latin-1")', "pass", "getattr with a bytes literal name"),
    ("site-dotted-parts", 'for part in item.split(".") if', "for part in (item,) if",
     "string naming an importlib submodule"),
    ("site-scope-entered", "    if not isinstance(node, SCOPE_NODES):\n", "    if True:\n",
     "module-level row for a site inside a function"),
    ("site-scope-async", "SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)",
     "SCOPE_NODES = (ast.FunctionDef, ast.ClassDef, ast.Lambda)", "async def scope"),
    ("site-scope-lambda", "SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)",
     "SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)", "lambda body in a scope of its own"),
    ("site-scope-nested", 'inner = inner if scope == MODULE_SCOPE else scope + "." + inner', "inner = inner",
     "allowlist row naming a method by its bare name"),
    ("site-definition-outer",
     "            elif isinstance(child, ast.AST):\n                out.append((child, scope, True))",
     "            elif isinstance(child, ast.AST):\n                out.append((child, inner, True))",
     "decorator evaluated outside its function"),
    ("site-defaults-outer", "out.extend((d, scope, True) for d in", "out.extend((d, inner, True) for d in",
     "default argument evaluated outside its function"),
    ("site-annotations-outer", "out.append((arg.annotation, scope, True))",
     "out.append((arg.annotation, inner, True))", "definition-time expressions in the enclosing scope"),
    ("site-parameters-inner", "out.append((arg, inner, False))", "out.append((arg, scope, False))",
     "parameter and attribute in a nested class method"),
    ("site-reference-climbed", "            return node\n        node = parent\n",
     "            return node\n        return node\n", "one reference per attribute chain and call"),
    ("site-alias-statement", "        return parents[id(node)]\n", "        return node\n",
     "submodule import and from-import"),
    ("site-members-watched",
     'member = isinstance(node, ast.Constant) or (isinstance(node, ast.Attribute) and field == "attr")',
     "member = False", "watched name _gcd_import"),
    ("site-members-positions",
     'member = isinstance(node, ast.Constant) or (isinstance(node, ast.Attribute) and field == "attr")',
     "member = True", "import-system members as attributes and literals only"),
    ("watched-module-aliases", "aliases = set(k for k, v in list(sys.modules.items()) if any(v is m for m in loaded))",
     "aliases = set(modules)", "frozen importlib module imported by its frozen name"),
    ("watched-members-derived", "                members.add(key)\n", "                pass\n",
     "watched name _gcd_import"),
    ("watched-modules-load",
     'raise CannotEvaluate("import-system module %s does not load (%s): the watched set has no other source"\n'
     '                                 % (name, exc))', "continue", "import-system module that does not load"),
    ("watched-inert-live", "    if stale:\n        raise CannotEvaluate(\"INERT_IMPORT_SYSTEM_MEMBERS",
     "    if False:\n        raise CannotEvaluate(\"INERT_IMPORT_SYSTEM_MEMBERS", "inert member that is no member"),
    ("literal-name-resolved", "                records.append((node.lineno, name, 0, (), guarded))\n",
     "                pass\n", "__import__ of a third-party name inside an allowlisted function"),
    ("literal-callee-attribute", "    if isinstance(func, ast.Attribute):\n        return func.attr\n",
     "    if False:\n        return func.attr\n",
     "literal dynamic import of a third-party name at an allowlisted site"),
    ("site-listed-exactly", "        if (rel, function, reference) in listed:\n",
     "        if any(r == rel and f == function for r, f, _x in listed):\n",
     "importer returned from a function with a reviewed call"),
    ("site-uncovered-finding", '        findings.append(("dynamic-import", rel, int(REFERENCE_RE',
     '        (("dynamic-import", rel, int(REFERENCE_RE', "function-local alias beside a module-level __import__"),
    ("sites-checked", "    py_findings += check_dynamic_sites(sites, allowlist)\n", "",
     "__import__ through __builtins__"),
    ("row-reason", "        if not reason.strip():\n", "        if False:\n", "allowlist row with no reason"),
    ("row-references",
     "        elif not references or not all(isinstance(r, str) and REFERENCE_RE.fullmatch(r) for r in "
     "references):\n", "        elif False:\n", "allowlist row listing no reference"),
    ("row-unique",
     "        elif any((rel, function, r) in listed for r in references) or len(set(references)) != "
     "len(references):\n", "        elif False:\n", "allowlist row repeated"),
    ("row-stale", "        if not used:\n", "        if False:\n", "stale allowlist row"),
    # workflow classification and pins
    ("yaml-forbidden-characters", "    if bad is not None:\n", "    if False:\n",
     "bare CR line break after a comment"),
    ("scalar-continuation-refused",
     'if scalar_column is not None and len(shape.group("indent")) > scalar_column:', "if False:",
     "plain scalar continued by a dash line"),
    ("item-continuation-column", 'scalar_column = column if key is not None else len(shape.group("indent"))',
     "scalar_column = column", "sequence item continued by a deeper dash line"),
    ("block-scalar-ends", "if content is not None and indent >= content:", "if content is not None:",
     "unpinned action after a block scalar ends"),
    ("block-scalar-empty", "if content is None and indent > parent:", "if content is None:",
     "unpinned action after an empty block scalar"),
    ("quoted-key-decoded", "name = name[1:-1] if name[0] in \"'\\\"\" else name", "name = name",
     "unpinned action under a quoted key"),
    ("key-any-case", "if name is not None and name.lower() in PIN_KEYS:", "if name is not None and name in PIN_KEYS:",
     "unpinned action under a capitalised key"),
    ("unclassified-line-refused", "            name, kind = None, None\n",
     "            name, kind = None, (\"none\", None)\n",
     "bare scalar line"),
    ("item-block-scalar-refused",
     "            if kind is not None and kind[0] == \"block\":\n                kind = None\n",
     "            if False:\n                kind = None\n", "block scalar as a sequence item"),
    ("uses-keylike-refused", 'if kind[0] in ("scalar", "flow") and USES_KEYLIKE_RE.search(kind[1]):', "if False:",
     "plain scalar holding a uses key"),
    ("pin-key-scalar-only", 'if kind[0] != "scalar" and not (', 'if kind[0] == "block" and not (',
     "uses key whose value is a flow sequence"),
    ("plain-indicator-start", "if not value or value[0] in YAML_PLAIN_START or \\", "if not value or \\",
     "plain value starting with an indicator"),
    ("plain-lone-dash", "(value[0] in \"-?:\" and (len(value) == 1 or value[1] in \" \\t\")):", "False:",
     "plain value that is a lone dash"),
    ("plain-colon-space", 'if re.search(r":(?:[ \\t]|$)", value):', "if False:",
     "plain value holding a colon and space"),
    ("quoted-key-no-escape", "|\\\"[A-Za-z0-9_.\\-/ ]*\\\")\"", "|\\\"[^\\\"]*\\\")\"", "escaped key in a workflow"),
    ("quoted-value-no-escape", "YAML_DOUBLE_RE = re.compile(r\"\\\"(?P<value>[^\\\"\\\\]*)\\\"\" + YAML_TAIL)",
     "YAML_DOUBLE_RE = re.compile(r\"\\\"(?P<value>[^\\\"]*)\\\"\" + YAML_TAIL)", "escaped uses value"),
    ("flow-item-no-colon", "YAML_FLOW_ITEM = r\"(?:[A-Za-z0-9_.\\-/]+|",
     "YAML_FLOW_ITEM = r\"(?:[A-Za-z0-9_.\\-/: ]+|",
     "flow sequence holding a mapping"),
    ("action-files-scanned", "               or rel.rsplit(\"/\", 1)[-1].lower() in ACTION_FILE_NAMES]",
     "               ]",
     "unpinned action in a local action file"),
    ("workflow-suffix-any-case", "and rel.lower().endswith((\".yml\", \".yaml\")))",
     "and rel.endswith((\".yml\", \".yaml\")))",
     "unpinned docker image"),
    ("workflow-present", "if not any(rel.startswith(WORKFLOWS_REL + \"/\") for rel, _ in targets):", "if False:",
     "no workflow to evaluate"),
    ("local-uses-confined", 'pinned = local != ".." and not local.startswith("../")', "pinned = True",
     "local uses path leaving the repository"),
    ("action-commit-sha",
     "pinned = remote is not None and COMMIT_SHA_RE.fullmatch(remote.group(\"ref\")) is not None",
     "pinned = remote is not None", "unpinned action by tag"),
    ("docker-digest", "pinned = DOCKER_PINNED_RE.fullmatch(value) is not None", "pinned = True",
     "unpinned docker image"),
    ("image-digest", "pinned = IMAGE_PINNED_RE.fullmatch(value) is not None or \\", "pinned = True or \\",
     "unpinned job container"),
    ("image-local-action-only", "(action_file and not value.startswith(\"/\") and local in file_set)",
     "(not value.startswith(\"/\") and local in file_set)", "workflow image naming a walked file"),
    ("image-local-walked", "(action_file and not value.startswith(\"/\") and local in file_set)",
     "(action_file and not value.startswith(\"/\"))", "action file image naming no walked file"),
    # vendor provenance
    ("record-fields-required", '("package.name", "package.version", "package.purl", "license.spdx",',
     '("package.name", "license.spdx",', "vendored record with no version"),
    ("vendored-root-named", 'if re.sub(r"[-.]", "_", package["name"]).casefold() != records[rroot].casefold():',
     "if False:", "vendored root not named for its package"),
    ("licence-under-vendor", '            if not licence.startswith(vdir + "/"):\n', "            if False:\n",
     "licence text outside its _vendor directory"),
    ("record-root-shape", 'rroot.count("/") != vdir.count("/") + 2 or ".." in rroot.split("/"):', "False:",
     "vendored root nested too deep"),
    ("purl-names-package", 'if re.fullmatch(purl_re, package["purl"]) is None:', "if False:",
     "purl naming another package"),
    ("spdx-recognised", "            if ids is None:\n", "            if False:\n", "invalid SPDX identifier"),
    ("spdx-known-ids", "    if not all(i in known for i in ids):\n", "    if False:\n", "invalid SPDX identifier"),
    ("spdx-one-operator", " or len(operators) > 1 or", " or", "SPDX expression mixing AND and OR"),
    ("spdx-operator-names", ' or not operators <= frozenset(("AND", "OR")):', ":",
     "SPDX expression with an unknown operator"),
    ("spdx-odd-words", "if len(words) % 2 == 0 or", "if", "SPDX expression ending in an operator"),
    ("licence-markers", "if ident in ids and absent:", "if False:", "empty licence text"),
    ("licence-listed", "            elif licence not in rows:\n", "            elif False:\n",
     "licence text not listed in the manifest"),
    ("manifest-present", "            if man_rel not in file_set:\n", "            if False:\n",
     "vendored manifest missing"),
    ("manifest-unique-path", 'if match is None or match.group("path") in rows:', "if match is None:",
     "duplicate manifest path"),
    ("listed-file-present", "                if path_rel not in file_set:\n", "                if False:\n",
     "listed vendored file missing"),
    ("listed-file-digest", ".hexdigest() != digest:", ".hexdigest() != digest and False:",
     "vendored bytes differ from the recorded digest"),
    ("root-files-listed", "if rel.startswith(rroot) and rel not in rows:", "if False:",
     "vendored file missing provenance"),
    ("vendored-python-under-root",
     'if rel.startswith(vdir + "/") and rel.endswith(".py") and not any(rel.startswith(r) for r in records):',
     "if False:", "vendored Python under no recorded root"),
    ("vendor-import-recorded", 'elif target + "/" in records:', "elif True:",
     "vendored package with no provenance record"),
    # dispositions
    ("disposition-condition-known", "if condition not in CONDITIONS:", "if False:",
     "disposition row with an unknown condition"),
    ("disposition-under-root", "elif rroot is None:", "elif False:", "disposition row outside a vendored root"),
    ("disposition-live", "elif not hits:", "elif False:", "stale disposition row"),
    ("disposition-guarded", "            if unguarded:\n", "            if False:\n",
     "guarded-optional row over an unguarded import"),
    ("disposition-handler-type", "return any(isinstance(k, ast.Name) and k.id in IMPORT_ERRORS for k in kinds)",
     "return True", "guarded-optional row whose handler is not ImportError"),
    ("try-else-unguarded", "                visit(child, guarded)\n            return\n",
     "                visit(child, body_guarded)\n            return\n", "import in a try's else is not guarded"),
    ("relative-targets-vendored", "            if rroot is None:\n                continue\n",
     "            if True:\n                continue\n", "relative import reaching an unreached module"),
    ("disposition-unreached", "                if reached:\n", "                if False:\n",
     "unreached-module row whose module is reached"),
)


def _load_mutant(source, directory, number):
    """Write one mutant module to the scratch directory and load it with importlib."""
    path = Path(directory) / ("import_closure_mutant_%d.py" % number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("import_closure_mutant_%d" % number, path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _case_line(case):
    if case[1] == 1:
        return "RED %s -> %s; GREEN without it" % (case[0], case[2])
    if case[1] == 2:
        return "CANNOT-EVALUATE %s -> exit 2" % case[0]
    return "SITES %s -> %d found" % (case[0], len(case[2]))


def self_test_main():
    failures = []
    this = sys.modules[__name__]
    tmp = Path(tempfile.mkdtemp(prefix="import-closure-"))
    try:
        clean = _fixture(tmp / "clean")
        rc, findings = _scan_rc(this, clean)
        if rc != 0:
            failures.append("clean fixture: expected 0, got %d %r" % (rc, findings))
        else:
            _f, shown, stats = scan(clean, dispositions=_FIXTURE_DISPOSITIONS, search_roots=_FIXTURE_ROOTS,
                                    allowlist=_FIXTURE_SITES)
            got = (len(shown), stats["dynamic"], stats["vendored"], stats["repo"], stats["uses"], stats["images"])
            if got != (2, 3, 4, 2, 2, 1):
                failures.append("clean fixture: dispositions or residual not reported: %r %r" % (shown, stats))
        print("GREEN clean fixture")
        cases = _vector_cases(tmp)
        for case in cases:
            problem = _case_failure(this, case)
            if problem is not None:
                failures.append("%s: %s" % (case[0], problem))
            print(_case_line(case))
        by_name = dict((case[0], case) for case in cases)
        gate_source, marker, rest = Path(__file__).read_text(encoding="utf-8").partition(_SELF_TEST_MARKER)
        mutant_dir = tmp / "mutants"
        os.makedirs(mutant_dir)
        for number, (mutant, old, new, vector) in enumerate(MUTANTS, 1):
            if gate_source.count(old) != 1 or vector not in by_name:
                failures.append("mutant %s: anchor found %d times, vector %r %s" % (
                    mutant, gate_source.count(old), vector, "known" if vector in by_name else "unknown"))
                continue
            try:
                module = _load_mutant(gate_source.replace(old, new) + marker + rest, mutant_dir, number)
            except SyntaxError as exc:
                failures.append("mutant %s: does not compile: %s" % (mutant, exc))
                continue
            try:
                problem = _case_failure(module, by_name[vector])
            except Exception as exc:
                problem = "raised %s" % type(exc).__name__
            if problem is None:
                failures.append("mutant %s: vector %r still holds with the check removed" % (mutant, vector))
            else:
                print("MUTANT %s caught by %s (%s)" % (mutant, vector, problem.split(",")[0]))
        # Each watched name and member removed in turn from a scratch copy: its own vector must then fail.
        copy = _load_mutant(gate_source + marker + rest, mutant_dir, 0)
        names, members = watched_names()
        for name in sorted(names | members):
            copy.watched_names = lambda *_args, _name=name: (names - {_name}, members - {_name})
            if _case_failure(copy, by_name["watched name " + name]) is None:
                failures.append("watched name %s: its vector still holds with the name removed" % name)
            else:
                print("REMOVED %s caught by its vector" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if run(ROOT) != 0:
        failures.append("live: the real tree does not pass")
    if failures:
        for failure in failures:
            print("SELF-TEST FAIL: %s" % failure, file=sys.stderr)
        return 1
    names, members = watched_names()
    print("SELF-TEST PASS: %d rule vectors red with their rule and green without it (%d of them one per watched "
          "name or import-system member), %d cannot-evaluate vectors exit 2, %d dynamic-import site vectors hold, "
          "%d mutants (one check removed each) caught by their vectors, %d watched names and %d members each "
          "removed and caught, the live tree passes"
          % (len(_RULE_VECTORS) + len(names | members), len(names | members), len(_CANNOT_VECTORS),
             len(_SITE_VECTORS), len(MUTANTS), len(names), len(members)))
    return 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test_main()
    if argv:
        print("usage: check_import_closure.py [--self-test]", file=sys.stderr)
        return 2
    return run(ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
