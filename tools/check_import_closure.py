#!/usr/bin/env python3
"""Import-closure, vendor-provenance and workflow-pin gate (rules mindep, liccmp, secvde, secsup).

Every import in this tree's Python resolves, by the search roots of the program that imports it, to the
standard library of the declared floor version, to a module file in this repository, or to a vendored
package with recorded provenance and licence; every action reference in .github/workflows (and in every
action.yml or action.yaml in the tree) has the form of a full commit SHA; and every container image there
is pinned by digest. A third-party or invented package name therefore cannot enter through an import
statement or a string-literal dynamic import in a scanned file, and vendored Python cannot enter without a
recognised SPDX identifier whose licence text it carries.

Search roots. A file's search roots are its own directory (Python puts a script's directory first on
sys.path; this tree's programs insert it explicitly, since python3 -I leaves it out), except when that
directory is a package (it holds __init__.py, so it is not itself on sys.path); each extra root that
IMPORT_SEARCH_ROOTS records for that file; and, for a file under a _vendor directory, that _vendor
directory alone (a vendored package is imported by its own name from there). A module is present in a root
only as NAME.py or a regular package NAME/__init__.py among the walked files: a namespace directory never
resolves an absolute import, since an installed regular package of the same name precedes it.

Rules (each self-test vector is caught by exactly its rule, and passes with that rule removed):
  import-closure     an absolute import (an import or from statement anywhere in the file, lazy, guarded or
                     under TYPE_CHECKING included, and a resolved dynamic import, below) resolves by its first
                     dotted component to a module present in one of the file's search roots, or to the
                     standard library. A name present in none of them is a finding: a third-party or
                     invented name. A same-named file outside the file's search roots never resolves it. A
                     relative import must name a module or package that exists under its base directory (or
                     the base must be a package). Each IMPORT_SEARCH_ROOTS row is held live: its file is
                     scanned, its root lies beyond the file's own directory, the file calls
                     sys.path.insert or sys.path.append and names every component of the root's path
                     relative to the file's directory as a string literal, and at least one of its imports
                     resolves through that root; a row that fails any of these is a finding.
  relative-escape    a relative import whose base directory lies outside the repository root, or, for a
                     vendored file, outside its own vendored package root.
  vendor-provenance  each _vendor directory: every NAME.provenance.toml there records [package] name,
                     version and purl (a pkg: URL ending in /NAME@VERSION), [license] spdx and vendored_path,
                     and [vendored_tree] root (one directory directly inside that _vendor directory); spdx is
                     one identifier from SPDX_LICENCE_MARKERS, or several joined by AND or by OR (not both);
                     the licence file is listed in the manifest, and its text contains every marker sentence
                     of every identifier named (case and whitespace aside); the sibling NAME.manifest.sha256
                     exists, and every row's file exists with the recorded sha256; every file under the root
                     is listed; every .py file under the _vendor directory lies under a recorded root; and an
                     import that resolves into a _vendor directory resolves to a recorded root.
  action-pin         every `uses:` value is a local ./PATH (in-repo), a docker:// image pinned by
                     @sha256:<64 hex>, or OWNER/REPO[/PATH]@<40 lowercase hex>; every `image:` or
                     `container:` scalar is pinned by @sha256:<64 hex> (a docker:// prefix allowed) or, in an
                     action file, names a walked file relative to that file's directory. A tag, a branch, a
                     short SHA or an expression is a finding.
  disposition        each VENDORED_OPTIONAL_IMPORTS row (a third-party import inside byte-exact vendored code
                     that this tree does not edit) is live and true: the file is under a recorded vendored
                     root, the import occurs there, and either every occurrence sits in the body of a try
                     whose handler is bare or names ImportError, ModuleNotFoundError, Exception or
                     BaseException (guarded-optional), or no import statement and no resolved dynamic import
                     anywhere else in the tree reaches that module (unreached-module). A row that no longer
                     matches, or whose condition fails, is a finding. A dispositioned import is printed on
                     every run, never hidden.

Dynamic imports. Import aliases are resolved per file (import importlib as x; from importlib import
import_module as y; from importlib import util; a star import from one of the modules below), and getattr
with a string-literal attribute name is read as that attribute. A call to importlib.import_module,
importlib.__import__, importlib.util.find_spec or __import__ (builtins.__import__) whose name, positional or
the name keyword, is a string-literal absolute dotted name (and whose level, if given, is the literal 0) is
resolved like an import statement; an unbound bare name import_module is read as importlib.import_module.
Every other dynamic-import site is counted and printed, not resolved: such a call with any other name
(computed, relative, a nonzero or computed level, a starred or double-starred argument); a call to exec,
eval, importlib.util.spec_from_file_location, importlib.util.spec_from_loader,
importlib.machinery.SourceFileLoader, importlib.machinery.SourcelessFileLoader,
importlib.machinery.ExtensionFileLoader, runpy.run_module, runpy.run_path, pkgutil.resolve_name or
zipimport.zipimporter; any other reference to one of these callables or to the four resolved ones (stored,
passed, or reached by getattr with a literal name); and getattr on importlib, importlib.util,
importlib.machinery, builtins, runpy, pkgutil or zipimport with a computed attribute name.

Workflow and action files are read by a line classifier for a subset of YAML 1.2, not a full parser. A line is
accepted only as: blank; a comment; a block mapping key (plain, or quoted with no escape and no quote inside)
followed by nothing, a comment, a plain or quoted scalar, a flow sequence of scalars none of which holds a
colon, or a block scalar header (| or >, with an optional chomping indicator); a block sequence item (one dash)
holding such a key, such a scalar or flow sequence, or nothing; or block scalar content, whose extent follows
YAML's indentation rule. A key named uses (in any case) is checked wherever it sits, and so is a key named image
or container. Any other line cannot be classified and exits 2, so an unpinned action cannot hide behind syntax
the classifier does not read: an escaped, alias or complex key, an anchor, alias or tag, a flow mapping, a
multi-line flow or plain scalar, a document marker or directive, a second dash on one line, a block scalar as a
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
                                       without it, every cannot-evaluate vector exits 2, every
                                       dynamic-import count vector resolves and counts what it names, every
                                       mutant in MUTANTS (one check removed) fails the vector it names, and
                                       the live tree passes

Exit convention: 0 clean; 1 a finding; 2 usage or cannot-evaluate.

DISCLOSED RESIDUAL. Dynamic imports: a counted site is not evaluated (the gate prints the count but cannot
name its target); a dynamic-import callable reached another way (a module's __dict__, vars(), a name bound
by assignment from another module, private importlib internals) is neither resolved nor counted; Python
source handed to a subprocess or written to a file is outside the surface. The unreached-module condition is
static: a counted computed-name call can still reach the module at runtime (the vendored marko.helpers loads
an extension by a computed name). Search roots: the own-directory root and the IMPORT_SEARCH_ROOTS rows are
the gate's model of sys.path; it does not evaluate a sys.path expression or the order of insertions (it
refuses a name present in two places instead), and it does not see a program launched with another path
(PYTHONPATH, python3 -m from another directory, or python3 -I without the own-directory insertion, under
which a sibling import fails or reaches an installed module of that name). Third-party code committed
outside a _vendor directory and reached through a recorded root counts as in-repo, without provenance; the
rows are reviewed in this file. Surface: only .py files are scanned; Python launched from another suffix,
other languages, and install commands (pip, npm) written into scripts or documentation are outside it. The
walk reads the working tree, not the git index, so an untracked local file can change a local verdict (CI
checks out clean). The walk does not enter .git, .venv, venv or node_modules at the repository root (each
run names the ones present); a directory of those names deeper in the tree is walked. A *.pyc file inside a
__pycache__ directory is not read. Vendored non-Python files outside a recorded root (data fixtures) are
outside the vendor-provenance surface. Licences: the gate holds that a vendored package names recognised
SPDX identifiers and that its licence text carries each identifier's marker sentences; it does not prove the
text complete or unmodified, judge licence compatibility, check the package against a registry, or consult
vulnerability advisories. Pins: the gate checks a reference's form; it does not ask GitHub whether a 40-hex
ref names a commit rather than a branch or tag of that name, or whether the commit is trustworthy. A key
named image or container inside an action's with: inputs is checked too (an over-rejection). The
classifier follows YAML 1.2 for its subset; GitHub's own parser was not run against the vectors.
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
RULES = ("import-closure", "relative-escape", "vendor-provenance", "action-pin", "disposition")
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

# Dynamic-import callables by qualified name, after import aliases are resolved (see the docstring).
RESOLVED_DYNAMIC = frozenset(("importlib.import_module", "importlib.__import__", "importlib.util.find_spec",
                              "builtins.__import__"))
COUNTED_DYNAMIC = frozenset((
    "builtins.exec", "builtins.eval", "importlib.util.spec_from_file_location", "importlib.util.spec_from_loader",
    "importlib.machinery.SourceFileLoader", "importlib.machinery.SourcelessFileLoader",
    "importlib.machinery.ExtensionFileLoader", "runpy.run_module", "runpy.run_path", "pkgutil.resolve_name",
    "zipimport.zipimporter",
))
DYNAMIC_MODULES = frozenset(q.rsplit(".", 1)[0] for q in RESOLVED_DYNAMIC | COUNTED_DYNAMIC)
# A bare name that no import in the file binds is read as this qualified name.
BARE_NAMES = dict((("__import__", "builtins.__import__"), ("exec", "builtins.exec"), ("eval", "builtins.eval"),
                   ("getattr", "builtins.getattr"), ("import_module", "importlib.import_module")))

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


def _aliases(tree):
    """Each local name an absolute import binds anywhere in the file, mapped to the qualified names it is
    bound to (a set: one name can be bound differently in different scopes)."""
    out = dict()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                out.setdefault(local, set()).add(alias.name if alias.asname else local)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            for alias in node.names:
                if alias.name != "*":
                    out.setdefault(alias.asname or alias.name, set()).add(node.module + "." + alias.name)
                    continue
                for qual in sorted(RESOLVED_DYNAMIC | COUNTED_DYNAMIC | DYNAMIC_MODULES):
                    if qual.startswith(node.module + ".") and "." not in qual[len(node.module) + 1:]:
                        out.setdefault(qual.rsplit(".", 1)[1], set()).add(qual)
    return out


def _getattr_call(node, aliases):
    return isinstance(node, ast.Call) and len(node.args) >= 2 and \
        "builtins.getattr" in _qualnames(node.func, aliases)


def _qualnames(node, aliases):
    """The qualified names an expression may denote: a name through the file's import aliases (an unbound
    name through BARE_NAMES), an attribute chain, or getattr with a string-literal attribute name."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id) or {BARE_NAMES.get(node.id, node.id)}
    if isinstance(node, ast.Attribute):
        return set(q + "." + node.attr for q in _qualnames(node.value, aliases))
    if _getattr_call(node, aliases) and isinstance(node.args[1], ast.Constant) and \
            isinstance(node.args[1].value, str):
        return set(q + "." + node.args[1].value for q in _qualnames(node.args[0], aliases))
    return set()


def _literal_import_name(call):
    """The string-literal absolute dotted name of a dynamic-import call, or None when the name is computed,
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
    if len(args) > 4 and not (isinstance(args[4], ast.Constant) and args[4].value == 0):
        return None
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None
    if not all(part.isidentifier() for part in name.value.split(".")):
        return None
    return name.value


def extract_imports(tree):
    """Import records (lineno, module, level, names, guarded) and the count of unresolved dynamic-import
    sites (see the docstring)."""
    records = []
    counted = [0]
    aliases = _aliases(tree)
    callees = set(id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call))
    dynamic = RESOLVED_DYNAMIC | COUNTED_DYNAMIC

    def visit(node, guarded):
        if isinstance(node, ast.Import):
            for alias in node.names:
                records.append((node.lineno, alias.name, 0, (), guarded))
        elif isinstance(node, ast.ImportFrom):
            records.append((node.lineno, node.module or "", node.level,
                            tuple(a.name for a in node.names), guarded))
        if isinstance(node, ast.Call):
            quals = _qualnames(node.func, aliases)
            if quals & RESOLVED_DYNAMIC:
                name = _literal_import_name(node)
                if name is None:
                    counted[0] += 1
                else:
                    records.append((node.lineno, name, 0, (), guarded))
            elif quals & COUNTED_DYNAMIC:
                counted[0] += 1
            if _getattr_call(node, aliases) and not (isinstance(node.args[1], ast.Constant) and
                                                     isinstance(node.args[1].value, str)) and \
                    _qualnames(node.args[0], aliases) & DYNAMIC_MODULES:
                counted[0] += 1
        reference = isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load) or \
            _getattr_call(node, aliases)
        if reference and id(node) not in callees and _qualnames(node, aliases) & dynamic:
            counted[0] += 1
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
    return records, counted[0]


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
            if licence not in rows:
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


def scan_python(root, files, stdlib, records, rules, search_roots=IMPORT_SEARCH_ROOTS):
    """Parse every .py file and classify every import. Returns (findings, stats, by_file)."""
    findings = []
    stats = dict(files=0, imports=0, stdlib=0, repo=0, vendored=0, computed=0, dispositioned=0)
    root = Path(root)
    file_set = set(rel for rel, _ in files)
    py = [(rel, path) for rel, path in files if rel.endswith(".py")]
    extra = dict()
    for rel, sroot in search_roots:
        extra.setdefault(rel, []).append(sroot)
    by_file, trees, used = dict(), dict(), set()
    for rel, path in py:
        source = _regular_bytes(path, rel)
        try:
            tree = ast.parse(source, rel)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            raise CannotEvaluate("%s: does not parse: %s" % (rel, exc))
        trees[rel] = tree
        imports, computed = extract_imports(tree)
        stats["files"] += 1
        stats["computed"] += computed
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
    return findings, stats, by_file


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
                    continue
                if value.startswith("docker://"):
                    pinned = DOCKER_PINNED_RE.fullmatch(value) is not None
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
         search_roots=IMPORT_SEARCH_ROOTS):
    """The whole verdict over root. Returns (findings, dispositioned lines, stats); raises CannotEvaluate."""
    stdlib = stdlib_names(sys_module)
    check_interpreter(load_floor(root), version_info if version_info is not None else sys.version_info)
    files = _walk(Path(root))
    records, vendor_findings = _load_vendor(root, files)
    py_findings, stats, by_file = scan_python(root, files, stdlib, records, rules, search_roots)
    py_findings, shown = apply_dispositions(py_findings, by_file, records, dispositions, rules, stats)
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
               "%(vendored)d vendored, %(dispositioned)d dispositioned), %(computed)d dynamic-import sites "
               "counted, not resolved (disclosed residual), %(uses)d action references, %(images)d container "
               "images; not walked at the root: %(skipped)s" % stats)
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
    ("literal dynamic import of a third-party name", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module('yaml')\n"),), (), {}, True),
    ("aliased importlib", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib as il\nil.import_module('requests')\n"),), (), {}, True),
    ("aliased import_module", "import-closure", "tools/t.py",
     (("tools/t.py", "from importlib import import_module as load\nload('requests')\n"),), (), {}, True),
    ("import_module name keyword", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module(name='requests')\n"),), (), {}, True),
    ("__import__ through builtins", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\nbuiltins.__import__('requests')\n"),), (), {}, True),
    ("importlib.__import__", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.__import__('requests')\n"),), (), {}, True),
    ("find_spec through importlib.util", "import-closure", "tools/t.py",
     (("tools/t.py", "from importlib import util\nutil.find_spec('requests')\n"),), (), {}, True),
    ("getattr with a literal name", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\ngetattr(importlib, 'import_module')('requests')\n"),), (), {}, True),
    ("__import__ level keyword 0", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', level=0)\n"),), (), {}, True),
    ("__import__ fifth argument 0", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', None, None, (), 0)\n"),), (), {}, True),
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
    ("vendored root nested too deep", "vendor-provenance", "lib/_vendor/other-1.0.provenance.toml",
     (("lib/_vendor/other-1.0.provenance.toml", _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT", "lib/_vendor/o/sub/")),
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
    ("disposition row outside a vendored root", "disposition", "tools/t.py",
     (("tools/t.py", "try:\n    import shinylib\nexcept ImportError:\n    pass\n"),), (),
     dict(dispositions=_FIXTURE_DISPOSITIONS + (("tools/t.py", "shinylib", "guarded-optional"),)), True),
    ("disposition row with an unknown condition", "disposition", "lib/_vendor/pkg/far.py", (), (),
     dict(dispositions=(_FIXTURE_DISPOSITIONS[0], ("lib/_vendor/pkg/far.py", "heavylib", "optional"))), True),
)

# (name, source, the import names it must resolve, the dynamic-import sites it must count)
_COUNT_VECTORS = (
    ("computed import_module name", "import importlib\nimportlib.import_module(name)\n", ("importlib",), 1),
    ("relative literal name", "import importlib\nimportlib.import_module('.x', 'pkg')\n", ("importlib",), 1),
    ("formatted name", "import importlib\nimportlib.import_module(f'marko.ext.{n}')\n", ("importlib",), 1),
    ("starred argument after a literal name", "__import__('x', *a)\n", (), 1),
    ("double-starred argument after a literal name", "__import__('x', **k)\n", (), 1),
    ("__import__ nonzero level", "__import__('x', None, None, (), 1)\n", (), 1),
    ("__import__ level keyword 1", "__import__('x', level=1)\n", (), 1),
    ("spec_from_file_location", "import importlib.util\nimportlib.util.spec_from_file_location('m', p)\n",
     ("importlib.util",), 1),
    ("aliased spec_from_file_location", "from importlib.util import spec_from_file_location as s\ns('m', p)\n",
     ("importlib.util",), 1),
    ("SourceFileLoader", "from importlib import machinery\nmachinery.SourceFileLoader('m', p)\n",
     ("importlib",), 1),
    ("exec of source", "exec(src)\n", (), 1),
    ("eval of source", "eval(src)\n", (), 1),
    ("exec through builtins", "import builtins\nbuiltins.exec(src)\n", ("builtins",), 1),
    ("runpy.run_path", "import runpy\nrunpy.run_path(p)\n", ("runpy",), 1),
    ("zipimporter", "import zipimport\nzipimport.zipimporter(p)\n", ("zipimport",), 1),
    ("stored import_module", "import importlib\nf = importlib.import_module\nf('requests')\n", ("importlib",), 1),
    ("stored bare __import__", "f = __import__\n", (), 1),
    ("getattr with a literal name, stored", "import importlib\ng = getattr(importlib, 'import_module')\n",
     ("importlib",), 1),
    ("getattr with a computed name", "import importlib\ngetattr(importlib, n)('x')\n", ("importlib",), 1),
    ("star import of runpy", "from runpy import *\nrun_path(p)\n", ("runpy",), 1),
    ("literal names resolve, uncounted",
     "import importlib as il\nil.import_module('a')\nimportlib.import_module(name='b')\n__import__('c', level=0)\n",
     ("importlib", "a", "b", "c"), 0),
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
    ("symlinked Python file", (), (), dict(), "symlink"),
    ("FIFO named like a Python file", (), (), dict(), "fifo"),
    ("symlinked directory", (), (), dict(), "symlinked-dir"),
    ("unlistable directory", (("tools/locked/x.txt", "x\n"),), (), dict(), "unlistable"),
    ("import present in two search roots", (("tools/c.py", "d = 2\n"),), (), dict(), None),
    ("in-repo module named like a standard-library module", (("tools/json.py", "x = 1\n"),), (), dict(), None),
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
)


def _vector_cases(tmp):
    """Build every vector's fixture tree once. Returns [(name, expected, rule, rel, base, kwargs, special)]:
    expected is 1 (a finding of rule at rel), 2 (cannot evaluate) or "count" (base is then the source)."""
    cases = []
    for index, (name, rule, at, extra, drop, kwargs, rehash) in enumerate(_RULE_VECTORS):
        base = _fixture(tmp / ("v%d" % index), tuple(row for row in extra if row[1] is not None), drop, rehash)
        if (_MANIFEST, None) in extra:
            manifest = base / _MANIFEST
            manifest.write_bytes(manifest.read_bytes() + ("%s  lib/_vendor/pkg/gone.py\n" % ("0" * 64)).encode())
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
    for name, source, names, count in _COUNT_VECTORS:
        cases.append((name, "count", names, count, source, dict(), None))
    return cases


def _scan_rc(module, base, rules=None, special=None, **kwargs):
    kwargs.setdefault("dispositions", _FIXTURE_DISPOSITIONS)
    kwargs.setdefault("search_roots", _FIXTURE_ROOTS)
    original = os.scandir

    def _locked(path="."):
        if os.fspath(path).endswith(os.sep + "locked"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return original(path)
    if special == "unlistable":
        os.scandir = _locked
    try:
        findings, _shown, _stats = module.scan(base, rules=module.RULES if rules is None else rules, **kwargs)
    except module.CannotEvaluate:
        return 2, []
    finally:
        os.scandir = original
    return (1 if findings else 0), findings


def _case_failure(module, case):
    """None if the case holds against module (this gate or a mutant of it), else what went wrong."""
    name, expected, rule, at, base, kwargs, special = case
    if expected == "count":
        records, counted = module.extract_imports(ast.parse(base))
        got = (tuple(r[1] for r in records), counted)
        return None if got == (rule, at) else "expected names and count %r, got %r" % ((rule, at), got)
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
    ("alias-import-as", "out.setdefault(local, set()).add(alias.name if alias.asname else local)",
     "out.setdefault(local, set()).add(local)", "aliased importlib"),
    ("alias-from-import", 'out.setdefault(alias.asname or alias.name, set()).add(node.module + "." + alias.name)',
     "out.setdefault(alias.asname or alias.name, set()).add(alias.name)", "aliased import_module"),
    ("alias-star-import", 'if qual.startswith(node.module + ".") and "." not in qual[len(node.module) + 1:]:',
     "if False:", "star import of runpy"),
    ("bare-builtin-names", "return aliases.get(node.id) or {BARE_NAMES.get(node.id, node.id)}",
     "return aliases.get(node.id) or {node.id}", "exec of source"),
    ("attribute-chain", 'return set(q + "." + node.attr for q in _qualnames(node.value, aliases))', "return set()",
     "literal dynamic import of a third-party name"),
    ("getattr-literal", "if _getattr_call(node, aliases) and isinstance(node.args[1], ast.Constant) and \\",
     "if False and \\", "getattr with a literal name"),
    ("name-keyword", 'if keyword.arg == "name" and name is None:', "if False:", "import_module name keyword"),
    ("level-keyword", 'if keyword.arg == "level" and not', "if False and not", "__import__ level keyword 1"),
    ("level-positional", "if len(args) > 4 and not (", "if False and not (", "__import__ nonzero level"),
    ("starred-argument", "if any(isinstance(a, ast.Starred) for a in args) or", "if False or",
     "starred argument after a literal name"),
    ("double-starred-argument", " or any(k.arg is None for k in call.keywords):", ":",
     "double-starred argument after a literal name"),
    ("dotted-name-shape", "if not all(part.isidentifier() for part in name.value.split(\".\")):", "if False:",
     "relative literal name"),
    ("computed-counted", "                if name is None:\n                    counted[0] += 1\n",
     "                if name is None:\n                    pass\n", "computed import_module name"),
    ("loader-call-counted", "elif quals & COUNTED_DYNAMIC:", "elif False:", "spec_from_file_location"),
    ("getattr-computed-counted", "_qualnames(node.args[0], aliases) & DYNAMIC_MODULES:", "False:",
     "getattr with a computed name"),
    ("reference-counted", "if reference and id(node) not in callees and _qualnames(node, aliases) & dynamic:",
     "if False:", "stored import_module"),
    ("callee-not-a-reference", "if reference and id(node) not in callees and", "if reference and",
     "literal names resolve, uncounted"),
    # workflow classification and pins
    ("yaml-forbidden-characters", "    if bad is not None:\n", "    if False:\n",
     "bare CR line break after a comment"),
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
     "multi-line plain scalar"),
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
    ("licence-listed", "            if licence not in rows:\n", "            if False:\n",
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
    return "COUNT %s -> %d counted" % (case[0], case[3])


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
            _f, shown, stats = scan(clean, dispositions=_FIXTURE_DISPOSITIONS, search_roots=_FIXTURE_ROOTS)
            got = (len(shown), stats["computed"], stats["vendored"], stats["repo"], stats["uses"], stats["images"])
            if got != (2, 1, 4, 2, 2, 1):
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if run(ROOT) != 0:
        failures.append("live: the real tree does not pass")
    if failures:
        for failure in failures:
            print("SELF-TEST FAIL: %s" % failure, file=sys.stderr)
        return 1
    print("SELF-TEST PASS: %d rule vectors red with their rule and green without it, %d cannot-evaluate "
          "vectors exit 2, %d dynamic-import count vectors hold, %d mutants (one check removed each) caught "
          "by their vectors, the live tree passes"
          % (len(_RULE_VECTORS), len(_CANNOT_VECTORS), len(_COUNT_VECTORS), len(MUTANTS)))
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
