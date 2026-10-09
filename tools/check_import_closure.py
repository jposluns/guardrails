#!/usr/bin/env python3
"""Import-closure, vendor-provenance and workflow-pin gate (rules mindep, liccmp, secvde, secsup).

Every import in this tree's Python resolves to the standard library of the declared floor version, to an
in-repo module, or to a vendored path with recorded provenance and licence; and every GitHub Actions step
in .github/workflows (and every action.yml or action.yaml in the tree) pins a full commit SHA. An invented
or typosquatted package name therefore cannot enter through a Python import, and material with no
identifiable licence cannot enter as vendored Python.

Rules (each self-test vector is caught by exactly its rule, and passes with that rule removed):
  import-closure     an import (an import or from statement anywhere in the file, lazy, guarded or under
                     TYPE_CHECKING included, and an importlib.import_module or __import__ call whose name is
                     a string literal) resolves, by its first dotted component, in this order: a module or
                     package beside the importing file; the standard library; a module or package in any
                     other in-repo code directory (a directory holding a scanned .py file outside a _vendor
                     directory); a module or package directly inside a _vendor directory. Anything else is a
                     finding: a third-party or invented name. A relative import must name a module or
                     package that exists under its base directory (or the base must be a package).
  relative-escape    a relative import whose base directory lies outside the repository root, or, for a
                     vendored file, outside its own vendored package root.
  vendor-provenance  each _vendor directory: every NAME.provenance.toml there records [package] name,
                     version and purl, [license] spdx and vendored_path (a file listed in the manifest),
                     and [vendored_tree] root (one directory directly inside that _vendor directory); its
                     sibling NAME.manifest.sha256 exists, and every row's file exists with the recorded
                     sha256; every file under the root is listed; every .py file under the _vendor
                     directory lies under a recorded root; and an import that resolves into a _vendor
                     directory resolves to a recorded root.
  action-pin         every `uses:` value is a local ./PATH (in-repo), a docker:// image pinned by
                     @sha256:<64 hex>, or OWNER/REPO[/PATH]@<40 lowercase hex>. A tag, a branch, a short SHA
                     or an expression is a finding.
  disposition        each VENDORED_OPTIONAL_IMPORTS row (a third-party import inside byte-exact vendored code
                     that this tree does not edit) is live and true: the file is under a recorded vendored
                     root, the import occurs there, and either every occurrence sits in the body of a try
                     whose handler catches ImportError (guarded-optional), or no static or literal import
                     anywhere else in the tree reaches that module (unreached-module). A row that no longer
                     matches, or whose condition fails, is a finding. A dispositioned import is printed on
                     every run, never hidden.

Fail closed (exit 2, cannot evaluate): the standard-library set is read from the interpreter
(sys.stdlib_module_names), never from a hand list, and the gate refuses to run if that attribute is absent,
empty or not a frozenset of names; it also refuses unless the interpreter's MAJOR.MINOR equals the floor
declared in .aiqt/core/python-floor.toml, since that set belongs to the running version. Also exit 2: a
.py file, workflow or action file, provenance record or manifest that is not a regular file (a symlink, a
FIFO), is unreadable, is not UTF-8 where text is required, or does not parse; a directory that cannot be
listed; a symlinked directory in the walk; a manifest row that is not one sha256sum row; an absent
workflow directory; and a workflow or action line naming the word uses that is not one plain or quoted
`uses:` key line (a flow mapping, a block scalar, an anchor), judged as written, script text included (a
disclosed over-rejection).

  check_import_closure.py              scan this repository
  check_import_closure.py --self-test  fixture trees: every rule vector is red with its rule and green
                                       without it, every cannot-evaluate vector exits 2, and the live tree
                                       passes

Exit convention: 0 clean; 1 a finding; 2 usage or cannot-evaluate.

DISCLOSED RESIDUAL. A dynamic import whose name is computed (importlib.import_module or __import__ given
anything but a string literal, importlib.util.spec_from_file_location, a sys.path edit followed by such a
call, exec of generated source) is not evaluated: the gate counts those call sites and prints the count,
but cannot name their targets. Resolution is by the first dotted component and by directory presence: the
gate proves the name is in-repo, standard library or vendored, not that the runtime sys.path reaches that
particular directory (a misrouted in-repo import fails at import time; it does not introduce a
dependency). Only .py files are scanned; Python launched from another suffix, other languages, and
install commands (pip, npm) written into scripts or documentation are outside the surface. Vendored
non-Python files outside a recorded root (data fixtures) are outside the vendor-provenance surface. The
gate records that a vendored package carries an SPDX identifier and a licence text; it does not judge
licence compatibility, check the package against a registry, or consult vulnerability advisories. A SHA
pin proves the action reference cannot move, not that the commit it names is trustworthy. The workflow
scan is a line model, not a YAML parser.
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
SKIPPED_DIR_NAMES = frozenset((".git", "__pycache__", ".venv", "venv", "node_modules"))
VENDOR_DIR_NAME = "_vendor"
PROVENANCE_SUFFIX = ".provenance.toml"
MANIFEST_SUFFIX = ".manifest.sha256"
ACTION_FILE_NAMES = ("action.yml", "action.yaml")
FLOOR_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
MANIFEST_ROW_RE = re.compile(r"(?P<digest>[0-9a-f]{64})  (?P<path>\S(?:.*\S)?)")
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
REMOTE_ACTION_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@(?P<ref>[^@\s]+)")
DOCKER_PINNED_RE = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
USES_WORD_RE = re.compile(r"(?i)(?<![A-Za-z0-9_-])uses(?![A-Za-z0-9_-])")
USES_LINE_RE = re.compile(r"(?i)[ ]*(?:-[ ]+)?uses[ ]*:[ ]+(?P<value>\S.*?)[ \t]*")
PLAIN_VALUE_RE = re.compile(r"(?P<value>[^\s'\"\[\]{}&*!|>%@`#,][^\s#]*)(?:[ \t]+#.*)?")
QUOTED_VALUE_RE = re.compile(r"(?:'(?P<single>[^']*)'|\"(?P<double>[^\"\\]*)\")(?:[ \t]+#.*)?")
RULES = ("import-closure", "relative-escape", "vendor-provenance", "action-pin", "disposition")
IMPORT_ERRORS = ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")

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
    """Every file under root as (repo-relative posix path, Path), pruning SKIPPED_DIR_NAMES; fail-closed on
    an unlistable directory or a symlinked directory (never silently skipped)."""
    def _raise(exc):
        raise CannotEvaluate("cannot list a directory: %s" % exc)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIR_NAMES)
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                raise CannotEvaluate("%s: a symlinked directory is not walked"
                                     % Path(dirpath, d).relative_to(root).as_posix())
        for name in sorted(filenames):
            if name in SKIPPED_DIR_NAMES:
                continue
            path = Path(dirpath) / name
            out.append((path.relative_to(root).as_posix(), path))
    return out


def _vendor_dir_of(rel):
    """The repo-relative _vendor directory a path lies under, or None."""
    parts = rel.split("/")[:-1]
    if VENDOR_DIR_NAME not in parts:
        return None
    return "/".join(parts[:parts.index(VENDOR_DIR_NAME) + 1])


def _catches_import_error(handler):
    kind = handler.type
    if kind is None:
        return True
    kinds = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(isinstance(k, ast.Name) and k.id in IMPORT_ERRORS for k in kinds)


def _literal_dynamic_name(call):
    """For an importlib.import_module, import_module or __import__ call: the string-literal name, or "" when
    the name is computed. None for any other call."""
    func = call.func
    named = isinstance(func, ast.Name) and func.id in ("__import__", "import_module")
    attribute = isinstance(func, ast.Attribute) and func.attr == "import_module" and \
        isinstance(func.value, ast.Name) and func.value.id == "importlib"
    if not (named or attribute):
        return None
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return ""


def extract_imports(tree):
    """Import records (lineno, module, level, names, guarded) and the count of computed dynamic imports."""
    records = []
    computed = [0]

    def visit(node, guarded):
        if isinstance(node, ast.Import):
            for alias in node.names:
                records.append((node.lineno, alias.name, 0, (), guarded))
        elif isinstance(node, ast.ImportFrom):
            records.append((node.lineno, node.module or "", node.level,
                            tuple(a.name for a in node.names), guarded))
        elif isinstance(node, ast.Call):
            name = _literal_dynamic_name(node)
            if name == "" or (name is not None and name.startswith(".")):
                computed[0] += 1
            elif name is not None:
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
    return records, computed[0]


def _module_present(directory, name):
    """True if NAME.py, a package NAME, or a namespace directory NAME holding .py files is in directory."""
    if not name or (directory / (name + ".py")).is_file():
        return bool(name)
    package = directory / name
    return package.is_dir() and ((package / "__init__.py").is_file() or any(package.glob("*.py")))


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
    """The base directory of a relative import, as a repo-relative posix path (it may start with ..)."""
    base = own
    for _ in range(level - 1):
        base = base.parent
    return base, os.path.relpath(base, root).replace(os.sep, "/")


def scan_python(root, files, stdlib, records, rules):
    """Parse every .py file and classify every import. Returns (findings, stats, by_file)."""
    findings = []
    stats = dict(files=0, imports=0, stdlib=0, repo=0, vendored=0, computed=0, dispositioned=0)
    root = Path(root)
    py = [(rel, path) for rel, path in files if rel.endswith(".py")]
    code_dirs = sorted(set(path.parent for rel, path in py if _vendor_dir_of(rel) is None))
    vendor_dirs = sorted(set(root / v for v in (_vendor_dir_of(rel) for rel, _ in files) if v is not None))
    by_file = dict()
    for rel, path in py:
        source = _regular_bytes(path, rel)
        try:
            tree = ast.parse(source, rel)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            raise CannotEvaluate("%s: does not parse: %s" % (rel, exc))
        imports, computed = extract_imports(tree)
        stats["files"] += 1
        stats["computed"] += computed
        by_file[rel] = imports
        own = path.parent
        vroot = _vendored_root_of(rel, records)
        for lineno, module, level, names, _guarded in imports:
            stats["imports"] += 1
            if level:
                base, base_rel = _relative_base(root, own, level)
                limit = vroot.rstrip("/") if vroot else None
                escaped = base_rel == ".." or base_rel.startswith("../") or \
                    (limit is not None and base_rel != limit and not base_rel.startswith(limit + "/"))
                if escaped:
                    if "relative-escape" in rules:
                        findings.append(("relative-escape", rel, lineno, "relative import (level %d) escapes %s"
                                         % (level, "its vendored root " + vroot if vroot else "the repository")))
                    continue
                if module:
                    ok = _module_present(base, module.split(".")[0])
                else:
                    ok = (base / "__init__.py").is_file() or all(_module_present(base, n) for n in names)
                if not ok:
                    findings.append(("import-closure", rel, lineno,
                                     "relative import names no module under %s" % base_rel))
                    continue
                stats["vendored" if vroot else "repo"] += 1
                continue
            top = module.split(".")[0]
            if vroot is not None and top == records[vroot]:
                stats["vendored"] += 1
            elif _module_present(own, top):
                stats["repo"] += 1
            elif top in stdlib:
                stats["stdlib"] += 1
            elif any(_module_present(d, top) for d in code_dirs):
                stats["repo"] += 1
            else:
                hits = [d for d in vendor_dirs if _module_present(d, top)]
                recorded = [d for d in hits if (d / top).relative_to(root).as_posix() + "/" in records]
                if recorded:
                    stats["vendored"] += 1
                elif hits:
                    if "vendor-provenance" in rules:
                        findings.append(("vendor-provenance", rel, lineno,
                                         "import %r resolves into %s with no provenance record"
                                         % (top, hits[0].relative_to(root).as_posix())))
                else:
                    findings.append(("import-closure", rel, lineno,
                                     "import %r is not the standard library of the floor version, an in-repo "
                                     "module, or a vendored path with recorded provenance (third-party or "
                                     "invented name)" % top))
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


def _uses_value(line):
    """The literal value of a strict `uses:` key line, or None if the line is not one."""
    match = USES_LINE_RE.fullmatch(line)
    if match is None:
        return None
    raw = match.group("value")
    quoted = QUOTED_VALUE_RE.fullmatch(raw)
    if quoted is not None:
        value = quoted.group("single") if quoted.group("single") is not None else quoted.group("double")
        return value if value and not any(c.isspace() for c in value) else None
    plain = PLAIN_VALUE_RE.fullmatch(raw)
    return plain.group("value") if plain is not None else None


def scan_workflows(root, files, rules):
    """Every `uses:` in .github/workflows/*.yml or *.yaml (any case) and in every action.yml or action.yaml."""
    findings = []
    count = 0
    depth = WORKFLOWS_REL.count("/") + 1
    targets = [(rel, path) for rel, path in files
               if (rel.startswith(WORKFLOWS_REL + "/") and rel.count("/") == depth
                   and rel.lower().endswith((".yml", ".yaml")))
               or rel.rsplit("/", 1)[-1].lower() in ACTION_FILE_NAMES]
    if not any(rel.startswith(WORKFLOWS_REL + "/") for rel, _ in targets):
        raise CannotEvaluate("%s holds no .yml or .yaml workflow (cannot evaluate the pin rule)" % WORKFLOWS_REL)
    for rel, path in targets:
        for number, line in enumerate(_text(path, rel).split("\n"), 1):
            if not USES_WORD_RE.search(line) or line.lstrip().startswith("#"):
                continue
            value = _uses_value(line[:-1] if line.endswith("\r") else line)
            if value is None:
                raise CannotEvaluate("%s:%d: a line naming uses that is not one plain or quoted `uses:` key "
                                     "line (judged as written)" % (rel, number))
            count += 1
            if value.startswith("./"):
                continue
            if value.startswith("docker://"):
                pinned = DOCKER_PINNED_RE.fullmatch(value) is not None
            else:
                remote = REMOTE_ACTION_RE.fullmatch(value)
                pinned = remote is not None and COMMIT_SHA_RE.fullmatch(remote.group("ref")) is not None
            if not pinned and "action-pin" in rules:
                findings.append(("action-pin", rel, number,
                                 "uses %r is not pinned to a full 40-hex commit SHA (a tag or branch can move)"
                                 % value))
    return findings, count


def scan(root, rules=RULES, sys_module=sys, version_info=None, dispositions=VENDORED_OPTIONAL_IMPORTS):
    """The whole verdict over root. Returns (findings, dispositioned lines, stats); raises CannotEvaluate."""
    stdlib = stdlib_names(sys_module)
    check_interpreter(load_floor(root), version_info if version_info is not None else sys.version_info)
    files = _walk(Path(root))
    records, vendor_findings = _load_vendor(root, files)
    py_findings, stats, by_file = scan_python(root, files, stdlib, records, rules)
    py_findings, shown = apply_dispositions(py_findings, by_file, records, dispositions, rules, stats)
    wf_findings, stats["uses"] = scan_workflows(root, files, rules)
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
               "%(vendored)d vendored, %(dispositioned)d dispositioned), %(computed)d dynamic imports with "
               "computed names not evaluated (disclosed residual), %(uses)d action references" % stats)
    if findings:
        print("IMPORT CLOSURE: FAILED: %d finding(s); %s" % (len(findings), summary), file=sys.stderr)
        return 1
    print("IMPORT CLOSURE: OK: %s" % summary)
    return 0


# ---------------------------------------------------------------- self-test

_PIN = "0123456789abcdef0123456789abcdef01234567"
_VENDORED = (
    "lib/_vendor/pkg/__init__.py", "lib/_vendor/pkg/mod.py", "lib/_vendor/pkg/sub/__init__.py",
    "lib/_vendor/pkg/sub/leaf.py", "lib/_vendor/pkg/opt.py", "lib/_vendor/pkg/far.py",
    "lib/_vendor/licenses/pkg-MIT.txt",
)
_PROVENANCE = ('[package]\nname = "pkg"\nversion = "1.0"\npurl = "pkg:pypi/pkg@1.0"\n'
               '[license]\nspdx = "%s"\nvendored_path = "lib/_vendor/licenses/pkg-MIT.txt"\n'
               '[vendored_tree]\nroot = "lib/_vendor/pkg/"\n')


def _fixture(base, extra=(), drop=(), rehash=True):
    """A clean fixture tree under base, then the extra rows (rel, str or bytes) written over it and the drop
    rels left out. The vendored manifest hashes the final bytes unless rehash is false (then the clean
    bytes, so an edited vendored file mismatches)."""
    rows = dict((
        (FLOOR_REL, 'python-floor = "%d.%d"\n' % tuple(sys.version_info[:2])),
        ("tools/a.py", "import os\nimport b\nimport importlib\nimportlib.import_module('json')\n"
                       "from c import d\nimport pkg.mod\n__import__(os.environ['X'])\n"),
        ("tools/b.py", "import sys\n"),
        ("lib/c.py", "d = 1\n"),
        ("lib/_vendor/pkg/__init__.py", "from . import mod\n"),
        ("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\n"),
        ("lib/_vendor/pkg/sub/__init__.py", ""),
        ("lib/_vendor/pkg/sub/leaf.py", "from .. import mod\n"),
        ("lib/_vendor/pkg/opt.py", "try:\n    import shinylib\nexcept ImportError:\n    shinylib = None\n"),
        ("lib/_vendor/pkg/far.py", "import heavylib\n"),
        ("lib/_vendor/licenses/pkg-MIT.txt", "MIT License\n"),
        ("lib/_vendor/pkg-1.0.provenance.toml", _PROVENANCE % "MIT"),
        (WORKFLOWS_REL + "/q.yml", "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@%s # v4\n"
                                   "      - uses: './local'\n      - name: x\n        run: echo ok\n" % _PIN),
    ))
    clean = dict(rows)
    rows.update(extra)
    hashed = rows if rehash else clean
    manifest = "".join("%s  %s\n" % (hashlib.sha256(hashed[rel].encode("utf-8")).hexdigest(), rel)
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

# (name, rule, extra rows, dropped rels, dispositions or None for the fixture's, rehash)
_RULE_VECTORS = (
    ("third-party import", "import-closure", (("tools/t.py", "import requests\n"),), (), None, True),
    ("invented name in a lazy import", "import-closure",
     (("tools/t.py", "def f():\n    import reqeusts\n"),), (), None, True),
    ("literal dynamic import of a third-party name", "import-closure",
     (("tools/t.py", "import importlib\nimportlib.import_module('yaml')\n"),), (), None, True),
    ("relative import escaping the repo", "relative-escape",
     (("tools/t.py", "from ... import x\n"),), (), None, True),
    ("relative import escaping its vendored root", "relative-escape",
     (("lib/_vendor/pkg/sub/leaf.py", "from ... import c\n"),), (), None, True),
    ("unpinned action by tag", "action-pin",
     ((WORKFLOWS_REL + "/p.yml", "steps:\n  - uses: actions/checkout@v4\n"),), (), None, True),
    ("unpinned action by short SHA", "action-pin",
     ((WORKFLOWS_REL + "/p.yml", "steps:\n  - uses: \"actions/checkout@0123456\"\n"),), (), None, True),
    ("unpinned docker image", "action-pin",
     ((WORKFLOWS_REL + "/p.YAML", "steps:\n  - uses: docker://alpine:3\n"),), (), None, True),
    ("unpinned action in a local action file", "action-pin",
     (("acts/x/action.yml", "runs:\n  steps:\n    - uses: actions/cache@main\n"),), (), None, True),
    ("vendored file missing provenance", "vendor-provenance",
     (("lib/_vendor/pkg/extra.py", "x = 1\n"),), (), None, True),
    ("vendored package with no provenance record", "vendor-provenance",
     (("lib/_vendor/other/__init__.py", "x = 1\n"), ("tools/t.py", "import other\n")), (), None, True),
    ("vendored record with no licence", "vendor-provenance",
     (("lib/_vendor/pkg-1.0.provenance.toml", _PROVENANCE % ""),), (), None, True),
    ("vendored bytes differ from the recorded digest", "vendor-provenance",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nimport os\n"),), (), None, False),
    ("vendored manifest missing", "vendor-provenance",
     (), ("lib/_vendor/pkg-1.0.manifest.sha256",), None, True),
    ("stale disposition row", "disposition", (), (),
     _FIXTURE_DISPOSITIONS + (("lib/_vendor/pkg/mod.py", "gone", "guarded-optional"),), True),
    ("guarded-optional row over an unguarded import", "disposition",
     (("lib/_vendor/pkg/opt.py", "import shinylib\n"),), (), None, True),
    ("unreached-module row whose module is reached", "disposition",
     (("tools/t.py", "from pkg import far\n"),), (), None, True),
    ("disposition row outside a vendored root", "disposition",
     (("tools/t.py", "try:\n    import shinylib\nexcept ImportError:\n    pass\n"),), (),
     _FIXTURE_DISPOSITIONS + (("tools/t.py", "shinylib", "guarded-optional"),), True),
)


class _NoStdlibSys:
    """A sys stand-in without stdlib_module_names: the standard-library source is unavailable."""


class _EmptyStdlibSys:
    stdlib_module_names = frozenset()


# (name, extra rows, dropped rels, scan keyword overrides, special)
_CANNOT_VECTORS = (
    ("standard-library source unavailable", (), (), dict(sys_module=_NoStdlibSys()), None),
    ("standard-library source empty", (), (), dict(sys_module=_EmptyStdlibSys()), None),
    ("interpreter is not the floor version", (), (), dict(version_info=(sys.version_info[0], 99)), None),
    ("floor source missing", (), (FLOOR_REL,), dict(), None),
    ("unparseable Python file", (("tools/t.py", "def (:\n"),), (), dict(), None),
    ("Python file that is not UTF-8", (("tools/t.py", b"x = '\xff'\n"),), (), dict(), None),
    ("Python file with a NUL byte", (("tools/t.py", b"x = 1\x00\n"),), (), dict(), None),
    ("symlinked Python file", (), (), dict(), "symlink"),
    ("FIFO named like a Python file", (), (), dict(), "fifo"),
    ("uses inside a flow mapping", ((WORKFLOWS_REL + "/p.yml", "steps:\n  - {uses: a/b@v1}\n"),), (), dict(), None),
    ("uses value in a block scalar", ((WORKFLOWS_REL + "/p.yml", "steps:\n  - uses: >\n      a/b@v1\n"),),
     (), dict(), None),
    ("malformed provenance record", (("lib/_vendor/pkg-1.0.provenance.toml", "[package\n"),), (), dict(), None),
    ("malformed manifest row", (("lib/_vendor/pkg-1.0.manifest.sha256", "abc lib/_vendor/pkg/mod.py\n"),),
     (), dict(), None),
    ("no workflow to evaluate", (), (WORKFLOWS_REL + "/q.yml",), dict(), None),
)


def _scan_rc(base, rules=RULES, **kwargs):
    kwargs.setdefault("dispositions", _FIXTURE_DISPOSITIONS)
    try:
        findings, _shown, _stats = scan(base, rules=rules, **kwargs)
    except CannotEvaluate:
        return 2, []
    return (1 if findings else 0), findings


def self_test_main():
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="import-closure-"))
    try:
        clean = _fixture(tmp / "clean")
        rc, findings = _scan_rc(clean)
        if rc != 0:
            failures.append("clean fixture: expected 0, got %d %r" % (rc, findings))
        else:
            _f, shown, stats = scan(clean, dispositions=_FIXTURE_DISPOSITIONS)
            if len(shown) != 2 or (stats["computed"], stats["vendored"], stats["repo"], stats["uses"]) != (1, 4, 2, 2):
                failures.append("clean fixture: dispositions or residual not reported: %r %r" % (shown, stats))
        print("GREEN clean fixture")
        for index, (name, rule, extra, drop, dispositions, rehash) in enumerate(_RULE_VECTORS):
            base = _fixture(tmp / ("v%d" % index), extra, drop, rehash)
            kwargs = dict() if dispositions is None else dict(dispositions=dispositions)
            rc, findings = _scan_rc(base, **kwargs)
            if rc != 1 or not any(f[0] == rule for f in findings):
                failures.append("%s: expected a %s finding, got rc %d %r" % (name, rule, rc, findings))
            rc_off, findings_off = _scan_rc(base, rules=tuple(r for r in RULES if r != rule), **kwargs)
            if rc_off != 0:
                failures.append("%s: with %s removed the vector must pass (it fails only by that rule), got "
                                "rc %d %r" % (name, rule, rc_off, findings_off))
            print("RED %s -> %s; GREEN without %s" % (name, rule, rule))
        for index, (name, extra, drop, kwargs, special) in enumerate(_CANNOT_VECTORS):
            base = _fixture(tmp / ("c%d" % index), extra, drop)
            if special == "symlink":
                os.symlink(base / "tools" / "b.py", base / "tools" / "t.py")
            elif special == "fifo":
                os.mkfifo(base / "tools" / "t.py")
            rc, findings = _scan_rc(base, **kwargs)
            if rc != 2:
                failures.append("%s: expected cannot-evaluate (2), got %d %r" % (name, rc, findings))
            print("CANNOT-EVALUATE %s -> exit 2" % name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if run(ROOT) != 0:
        failures.append("live: the real tree does not pass")
    if failures:
        for failure in failures:
            print("SELF-TEST FAIL: %s" % failure, file=sys.stderr)
        return 1
    print("SELF-TEST PASS: %d rule vectors red with their rule and green without it, %d cannot-evaluate "
          "vectors exit 2, the live tree passes" % (len(_RULE_VECTORS), len(_CANNOT_VECTORS)))
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
