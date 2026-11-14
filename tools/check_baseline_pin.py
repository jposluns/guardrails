#!/usr/bin/env python3
"""Offline gate for the pinned copy of the shared site template (baseline package).

  check_baseline_pin.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_baseline_pin.py --self-test  assert every refusal and a pass over tempdir fixtures
  check_baseline_pin.py [--self-test] --execution-report ABS_PATH

Each pin records, in the package README's format, the baseline version (`version = "X.Y.Z"`) and a [files]
table from each copied file's repository path to its SHA-256. Every path in a pin lies under one copy root, a
directory whose last 2 components are assets/baseline. PINS registers every pin with the 1 copy root it is
bound to (.baseline/pin.toml for site/draft/assets/baseline, .baseline/opfiles-pin.toml for
opf/site/draft/assets/baseline while staging); a pin that names any other root, and a registry that repeats
a pin or a root, are cannot-evaluate, so 2 pins can never validate the same copy and leave the other one
unchecked. For each registered pair the gate hashes every pinned file under the copy root and reports a
file whose bytes differ, a
pinned file that is missing, a file under a copy root that the pin does not list (a stray), and a
symbolic link or other non-regular entry anywhere in a copy root, including the root itself (exit 1).

The one exception to the copy-root rule is BUILD_TOOLS: the template's build tools that a site copies outside
its served tree and pins in the same [files] table (only .baseline/check_palette.py, the palette gate). A
pin may list such a path at its own repository path, beside its copy-root files; the gate hashes it as 1
regular file (only its last component is checked for a symbolic link; a linked parent directory is
followed), and reports it missing, a symbolic link, not a regular file
or differing (exit 1), or unreadable (exit 2). Its directory gets no stray scan, since .baseline/ also holds
the pins. Any other path outside a copy root is still cannot-evaluate, and a pin must list at least 1
copy-root file. A pin that is missing, unreadable, over MAX_PIN_BYTES, not UTF-8, not TOML or not of the pinned shape, and
a copied file that cannot be read, are cannot-evaluate (exit 2), never a pass.

Every registered pin must name the same version, and that version must equal the site template version
recorded in .aiqt/attribution.toml, table [vendored."site-template"], key version: the single in-tree
source tools/gen_notice.py renders the root NOTICE from (the NOTICE is generated output, so it is not
read). The same table's homepage, the source archive URL, must end in /baseline-<version>.tar.gz for that
recorded version, so the archive cannot name another version. The file is read under the scanned root. An
attribution file that is missing, unreadable, not a regular file, over MAX_ATTRIBUTION_BYTES, not UTF-8 or
not TOML, a missing [vendored."site-template"] table, a version there that is missing or not an X.Y.Z
string, a homepage there that is missing, not a string or not ending that way, a pin whose version
differs from the recorded version, and 2 pins that name different versions are cannot-evaluate (exit 2).
The gate reads only the repository; it never touches the network.

While the OPF tree's copy of this gate (opf/tools/check_baseline_pin.py) exists, the self-test also holds it
to this gate: it loads the copy in process (never launching it), runs both gates over one fixture tree per
case the two self-tests share (each tree also meets the copy's own required licence, notice, tool and
attribution rules) and requires the copy's exit code to equal this gate's on every case; and it requires
opf/.baseline/pin.toml to equal .baseline/opfiles-pin.toml with the opf/ prefix removed from each path (the
same version and copy-root digests, and the palette-tool entry compared by digest). A copy that is present
but cannot be loaded fails the self-test; only an absent copy skips the parity checks, with a printed
reason, and the execution gate then reports their ids as not run.

The OPF pair is transitional. PINS[1] with .baseline/opfiles-pin.toml, and the parity checks above, exist
only while the OPF tree lives in this repository. Once the OPF tree's own gate is accepted and the OPF tree
moves to its own repository, they are removed together in one change: the PINS[1] entry, the file
.baseline/opfiles-pin.toml, the parity section (OPF_COPY_REL through _parity_self_test) and its call in
_self_test, the parity/ check ids under baseline-pin-selftest in tools/selftest_checks.toml, the rows
recorded for the parity load (this file's DYNAMIC_LOADS_REVIEWED row in tools/check_child_status_coverage.py,
its _load_opf_copy row in tools/check_dangerous_api.py and its _parity_codes row in
tools/tomllib_parse_sites.toml), and that registry's row for the OPF copy's own _self_test. The gate's check
of PINS[0] (.baseline/pin.toml over site/draft/assets/baseline), its build-tool check and its attribution
and homepage checks stay. Nothing here is removed before that move.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_baseline_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import hashlib
import importlib.util
import io
import os
import re
import stat
import tempfile
import tomllib
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Every pin and the 1 copy root it is bound to, in check order; both columns are unique. The second pair,
# the OPF copy's, is transitional and is removed when the OPF tree leaves this repository (module docstring).
PINS = (
    (".baseline/pin.toml", "site/draft/assets/baseline"),
    (".baseline/opfiles-pin.toml", "opf/site/draft/assets/baseline"),
)
PIN_REL = PINS[0][0]
MAX_PIN_BYTES = 1 << 20
CHUNK = 1 << 16
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
# A copy root or a file path: relative, "/"-separated, no empty, "." or ".." component.
PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
TOP_KEYS = {"version", "files"}
# The copy root's own last components: a pinned path is <prefix>assets/baseline/<file>, and its copy root is
# <prefix>assets/baseline.
COPY_DIR = "assets/baseline"
# The build tools a pin may list outside any copy root, each at exactly this repository path: the template's
# palette gate, copied from the package's tools/, never served, and run by tools/run_all_checks.sh and the
# Quality workflow (template specification section 4 item 3 also requires it in the Pages build command,
# which is set outside this repository).
BUILD_TOOLS = (".baseline/check_palette.py",)
# The in-tree record of the template version every pin must name: the [vendored."site-template"] table of
# the attribution file tools/gen_notice.py renders the root NOTICE from, read under the scanned root.
ATTRIBUTION_REL = ".aiqt/attribution.toml"
ATTRIBUTION_BLOCK = "site-template"
ATTRIBUTION_WHERE = '{} [vendored."{}"]'.format(ATTRIBUTION_REL, ATTRIBUTION_BLOCK)
MAX_ATTRIBUTION_BYTES = 1 << 20


class CannotEvaluate(Exception):
    """An input that cannot be judged: a missing, unreadable, oversized or malformed pin or attribution file,
    or an unreadable file."""


def _safe_rel(value):
    return (isinstance(value, str) and value != ""
            and all(PART_RE.fullmatch(part) and part not in (".", "..") for part in value.split("/")))


def _read_pin_bytes(path):
    """The pin's bytes, at most MAX_PIN_BYTES + 1 of them (so an oversized pin is seen, never read whole)."""
    with open(path, "rb") as handle:
        return handle.read(MAX_PIN_BYTES + 1)


def parse_pin(data, where):
    """The pin as (version, [(root, {path: sha256})], {build tool path: sha256}); CannotEvaluate on any
    malformed input."""
    if len(data) > MAX_PIN_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_PIN_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    doc, problem = None, None
    try:
        doc = tomllib.loads(text)
    except (RecursionError, MemoryError, ValueError) as exc:
        problem = "{}: not valid TOML ({}: {})".format(where, type(exc).__name__, exc)
    if problem is not None:
        raise CannotEvaluate(problem)
    unknown = sorted(set(doc) - TOP_KEYS)
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(where, ", ".join(unknown)))
    version = doc.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise CannotEvaluate("{}: version must be an X.Y.Z string".format(where))
    files = doc.get("files")
    if not isinstance(files, dict) or not files:
        raise CannotEvaluate("{}: a non-empty [files] table is required".format(where))
    root, pinned, tools = None, {}, {}
    for path, digest in files.items():
        if path in BUILD_TOOLS:
            if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
                raise CannotEvaluate("{}: {}: the SHA-256 must be 64 lowercase hex digits".format(where, path))
            tools[path] = digest
            continue
        if not _safe_rel(path):
            raise CannotEvaluate("{}: unsafe file path {!r}".format(where, path))
        head, sep, rel = path.partition(COPY_DIR + "/")
        if not sep or not rel or (head and not head.endswith("/")):
            raise CannotEvaluate("{}: {} is not under an {} copy root".format(where, path, COPY_DIR))
        if root is None:
            root = head + COPY_DIR
        elif head + COPY_DIR != root:
            raise CannotEvaluate("{}: {} is under a second copy root (the first is {})".format(where, path, root))
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise CannotEvaluate("{}: {}: the SHA-256 must be 64 lowercase hex digits".format(where, path))
        pinned[rel] = digest
    if root is None:
        raise CannotEvaluate("{}: no file under an {} copy root".format(where, COPY_DIR))
    return version, [(root, pinned)], tools


def _read_attribution_bytes(path):
    """The attribution file's bytes, at most MAX_ATTRIBUTION_BYTES + 1 of them."""
    with open(path, "rb") as handle:
        return handle.read(MAX_ATTRIBUTION_BYTES + 1)


def parse_attribution(data, where=ATTRIBUTION_REL):
    """The site template version the attribution file records; CannotEvaluate on any malformed input."""
    if len(data) > MAX_ATTRIBUTION_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_ATTRIBUTION_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    doc, problem = None, None
    try:
        doc = tomllib.loads(text)
    except (RecursionError, MemoryError, ValueError) as exc:
        problem = "{}: not valid TOML ({}: {})".format(where, type(exc).__name__, exc)
    if problem is not None:
        raise CannotEvaluate(problem)
    vendored = doc.get("vendored")
    block = vendored.get(ATTRIBUTION_BLOCK) if isinstance(vendored, dict) else None
    if not isinstance(block, dict):
        raise CannotEvaluate('{}: no [vendored."{}"] table, so the version every pin must name is '
                             'unknown'.format(where, ATTRIBUTION_BLOCK))
    version = block.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise CannotEvaluate('{}: [vendored."{}"] version must be an X.Y.Z string'.format(
            where, ATTRIBUTION_BLOCK))
    # The source archive must be the recorded version's own, so the version and the archive cannot drift apart.
    homepage = block.get("homepage")
    ending = "/baseline-{}.tar.gz".format(version)
    if not isinstance(homepage, str) or not homepage.endswith(ending):
        raise CannotEvaluate('{}: [vendored."{}"] homepage must end in {} for version {}, but it is {}'.format(
            where, ATTRIBUTION_BLOCK, ending, version, "missing" if homepage is None else repr(homepage)))
    return version


def _attribution_version(root):
    """(version, None) read from the attribution file under root, or (None, the cannot-evaluate line)."""
    path = Path(root) / ATTRIBUTION_REL
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise CannotEvaluate("{}: not a regular file".format(ATTRIBUTION_REL))
        return parse_attribution(_read_attribution_bytes(path)), None
    except FileNotFoundError:
        return None, "{}: missing".format(ATTRIBUTION_REL)
    except OSError as exc:
        return None, "{}: unreadable ({})".format(ATTRIBUTION_REL, exc)
    except CannotEvaluate as exc:
        return None, str(exc)


def _version_errors(root, versions):
    """Cannot-evaluate lines for pins that name different versions, or a version other than the one the
    attribution file records; versions is [(pin_rel, version)] for every pin that parsed."""
    expected, problem = _attribution_version(root)
    if problem is not None:
        return [problem]
    errors = []
    if versions:
        first_rel, first_version = versions[0]
        for pin_rel, version in versions[1:]:
            if version != first_version:
                errors.append("the pins disagree: {} names version {} but {} names version {}; every "
                              "registered pin must name the same version".format(
                                  first_rel, first_version, pin_rel, version))
    for pin_rel, version in versions:
        if version != expected:
            errors.append("{}: version {} differs from the site template version {} recorded in {}".format(
                pin_rel, version, expected, ATTRIBUTION_WHERE))
    return errors


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(base, findings, errors, where):
    """Every regular file under base as a sorted list of relative paths; a symlink or other non-regular
    entry is a finding and is never followed."""
    found = []
    pending = [""]
    while pending:
        rel_dir = pending.pop()
        try:
            with os.scandir(os.path.join(base, rel_dir) if rel_dir else base) as entries:
                listing = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            errors.append("{}/{}: cannot list ({})".format(where, rel_dir, exc))
            continue
        for entry in listing:
            rel = entry.name if not rel_dir else rel_dir + "/" + entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                errors.append("{}/{}: cannot stat ({})".format(where, rel, exc))
                continue
            if stat.S_ISLNK(mode):
                findings.append("{}/{}: is a symbolic link".format(where, rel))
            elif stat.S_ISDIR(mode):
                pending.append(rel)
            elif stat.S_ISREG(mode):
                found.append(rel)
            else:
                findings.append("{}/{}: not a regular file".format(where, rel))
    return sorted(found)


def _registry_errors(pins):
    """Cannot-evaluate lines for a registry that is empty or repeats a pin or a copy root."""
    if not pins:
        return ["the pin registry is empty"]
    errors = []
    for column, what in ((0, "pin"), (1, "copy root")):
        values = [pair[column] for pair in pins]
        for value in sorted({value for value in values if values.count(value) > 1}):
            errors.append("the pin registry lists the {} {} more than once".format(what, value))
    return errors


def scan(root, pins=PINS):
    """(findings, errors) for every registered pin under root; errors make the run cannot-evaluate."""
    findings, errors = [], []
    problems = _registry_errors(pins)
    if problems:
        return findings, problems
    versions = []
    for pin_rel, assigned in pins:
        _scan_pin(root, pin_rel, assigned, findings, errors, versions)
    errors.extend(_version_errors(root, versions))
    return findings, errors


def _scan_pin(root, pin_rel, assigned, findings, errors, versions):
    """Check 1 pin against its assigned copy root, appending to findings and errors, and (pin_rel, version)
    to versions once the pin parses."""
    pin_path = Path(root) / pin_rel
    try:
        mode = os.lstat(pin_path).st_mode
        if not stat.S_ISREG(mode):
            raise CannotEvaluate("{}: not a regular file".format(pin_rel))
        data = _read_pin_bytes(pin_path)
        version, roots, tools = parse_pin(data, pin_rel)
        versions.append((pin_rel, version))
    except FileNotFoundError:
        errors.append("{}: missing".format(pin_rel))
        return
    except OSError as exc:
        errors.append("{}: unreadable ({})".format(pin_rel, exc))
        return
    except CannotEvaluate as exc:
        errors.append(str(exc))
        return
    for copy_root, _pinned in roots:
        if copy_root != assigned:
            errors.append("{}: names the copy root {}, but its registered root is {}".format(
                pin_rel, copy_root, assigned))
            return
    for copy_root, pinned in roots:
        base = Path(root) / copy_root
        try:
            mode = os.lstat(base).st_mode
        except FileNotFoundError:
            findings.extend("{}/{}: missing".format(copy_root, rel) for rel in sorted(pinned))
            continue
        except OSError as exc:
            errors.append("{}: cannot stat ({})".format(copy_root, exc))
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            findings.append("{}: copy root is a symbolic link or not a directory".format(copy_root))
            continue
        present = _inventory(base, findings, errors, copy_root)
        for rel in sorted(set(present) - set(pinned)):
            findings.append("{}/{}: stray file, not listed in {}".format(copy_root, rel, pin_rel))
        for rel in sorted(pinned):
            if rel not in present:
                if not any(line.startswith("{}/{}:".format(copy_root, rel)) for line in findings):
                    findings.append("{}/{}: missing".format(copy_root, rel))
                continue
            try:
                got = _sha256(base / rel)
            except OSError as exc:
                errors.append("{}/{}: unreadable ({})".format(copy_root, rel, exc))
                continue
            if got != pinned[rel]:
                findings.append("{}/{}: SHA-256 {} differs from the pinned {}".format(
                    copy_root, rel, got, pinned[rel]))
    for rel in sorted(tools):
        _check_tool(root, rel, tools[rel], findings, errors)


def _check_tool(root, rel, digest, findings, errors):
    """Check 1 pinned build tool: a regular file at its own path whose bytes match the pin. Only the last
    path component is checked for a symbolic link (os.lstat); a symbolic link among its parent directories,
    such as .baseline/ itself, is followed, and the file is then opened by path."""
    path = Path(root) / rel
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        findings.append("{}: missing".format(rel))
        return
    except OSError as exc:
        errors.append("{}: cannot stat ({})".format(rel, exc))
        return
    if stat.S_ISLNK(mode):
        findings.append("{}: is a symbolic link".format(rel))
        return
    if not stat.S_ISREG(mode):
        findings.append("{}: not a regular file".format(rel))
        return
    try:
        got = _sha256(path)
    except OSError as exc:
        errors.append("{}: unreadable ({})".format(rel, exc))
        return
    if got != digest:
        findings.append("{}: SHA-256 {} differs from the pinned {}".format(rel, got, digest))


def run_gate(root, pins=PINS):
    findings, errors = scan(root, pins)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: every file pinned in {} matches its SHA-256 under its registered copy root, with no stray "
          "or linked entry, every pinned build tool is a regular file with its pinned SHA-256, and every pin names the site template version recorded in {}, whose homepage ends in that version's archive name".format(
              ", ".join(pin_rel for pin_rel, _root in pins), ATTRIBUTION_WHERE))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "baseline-pin-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


FIXTURE_ROOT = "site/draft/assets/baseline"
FIXTURE_FILES = {"baseline.css": b"body{}\n", "fonts/a.woff2": b"wOF2\x00\x01", "marks/NOTICE.md": b"# n\n"}
# The single-pair registry most cases use, and the 2-pair registry the live tree uses.
FIXTURE_PINS = ((PIN_REL, FIXTURE_ROOT),)
SECOND_PIN = PINS[1][0]
SECOND_ROOT = PINS[1][1]
TWO_PINS = ((PIN_REL, FIXTURE_ROOT), (SECOND_PIN, SECOND_ROOT))
# The build tool a fixture pins outside its copy root, at the registered path.
TOOL_REL = BUILD_TOOLS[0]
FIXTURE_TOOLS = {TOOL_REL: b"import sys\n"}


# The fixture's template version, named by every fixture pin and by the fixture attribution file; it
# differs from the live one, so a pass shows the version is read under the scanned root.
FIXTURE_VERSION = "1.2.1"


def _pin_text(files, root=FIXTURE_ROOT, version=FIXTURE_VERSION, extra="", tools=None):
    lines = ['version = "{}"'.format(version), extra, "", "[files]"]
    for rel in sorted(tools or {}):
        lines.append('"{}" = "{}"'.format(rel, hashlib.sha256(tools[rel]).hexdigest()))
    for rel in sorted(files):
        lines.append('"{}/{}" = "{}"'.format(root, rel, hashlib.sha256(files[rel]).hexdigest()))
    return "\n".join(lines) + "\n"


def _attribution_text(version=FIXTURE_VERSION, block=ATTRIBUTION_BLOCK, homepage=None):
    """A fixture attribution file: another vendored block first, so the version is taken from the named one.
    homepage is the archive URL (None for the version's own archive, False to write none)."""
    if homepage is None:
        homepage = "https://example.org/baseline/baseline-{}.tar.gz".format(version)
    return ('[vendored."marko"]\nname = "Marko"\nversion = "9.9.9"\n\n'
            '[vendored."{}"]\nname = "Shared site template (baseline package)"\nversion = "{}"\n{}'
            'verified = true\n').format(block, version,
                                        "" if homepage is False else 'homepage = "{}"\n'.format(homepage))


def _fixture(tmp, files=None, pin=None, root=FIXTURE_ROOT, pin_rel=PIN_REL, attribution=None, tools=None):
    """A fixture tree under tmp: the copied files under root, the pin at pin_rel (text, bytes, None for
    the exact pin of FIXTURE_FILES under root, or False for absent), the attribution file (text, bytes,
    None for one recording FIXTURE_VERSION, or False to write none) and tools ({repository path: bytes}
    written at their own paths, outside the copy root)."""
    for rel, data in (tools or {}).items():
        os.makedirs((Path(tmp) / rel).parent, exist_ok=True)
        with open(Path(tmp) / rel, "wb") as handle:
            handle.write(data)
    if attribution is None:
        attribution = _attribution_text()
    if attribution is not False:
        os.makedirs((Path(tmp) / ATTRIBUTION_REL).parent, exist_ok=True)
        with open(Path(tmp) / ATTRIBUTION_REL, "wb") as handle:
            handle.write(attribution.encode("utf-8") if isinstance(attribution, str) else attribution)
    files = FIXTURE_FILES if files is None else files
    for rel, data in files.items():
        target = Path(tmp) / root / rel
        os.makedirs(target.parent, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    if pin is None:
        pin = _pin_text(FIXTURE_FILES, root=root)
    if pin is not False:
        os.makedirs(Path(tmp) / ".baseline", exist_ok=True)
        with open(Path(tmp) / pin_rel, "wb") as handle:
            handle.write(pin.encode("utf-8") if isinstance(pin, str) else pin)
    return tmp


def _scan_case(files=None, pin=None, mutate=None, pins=FIXTURE_PINS, second=None, attribution=None, tools=None):
    """scan() over a fixture; second, when given, is (files, pin) for the second registered pair; tools
    are build-tool files written at their own paths."""
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp, files, pin, attribution=attribution, tools=tools)
        if second is not None:
            _fixture(tmp, second[0], second[1], root=SECOND_ROOT, pin_rel=SECOND_PIN, attribution=False)
        if mutate is not None:
            mutate(Path(tmp))
        return scan(tmp, pins)


# The OPF tree's copy of this gate and its pin. While the copy exists, the self-test holds it to this gate's
# exit code on the fixture cases both self-tests share, and holds its pin to this repository's OPF pin with
# OPF_PREFIX removed from each path. This section through _parity_self_test is transitional: it is removed
# with PINS[1] when the OPF tree leaves this repository (module docstring).
OPF_COPY_REL = "opf/tools/check_baseline_pin.py"
OPF_PIN_REL = "opf/.baseline/pin.toml"
OPF_PREFIX = "opf/"
# The names the parity check reads from the copy: its gate entry, the paths and keys its fixtures must meet,
# its self-test's check ids, and the 3 readers a case may make fail (patched in both gates alike).
OPF_COPY_NAMES = ("run_gate", "ATTRIBUTION_REL", "REQUIRED_NOTICES", "ATTRIBUTION_TEXT_KEYS", "EXPECTED",
                  "_read_pin_bytes", "_read_attribution_bytes", "_sha256")
PARITY_READERS = ("_read_pin_bytes", "_read_attribution_bytes", "_sha256")
# The shared check ids with no parity case, each with the reason: a registry or tool constant each gate pins
# for its own tree, and each gate's own live tree (the pin comparison covers the OPF copy's). Every other
# shared id is a parity case, and every case compares the 2 exit codes, so exit/codes-0-1-2 is covered by
# requiring all 3 codes among the cases.
PARITY_EXCLUDED = {
    "registry/live-pairs": "each gate registers its own pins",
    "tool/registered-path": "a constant of each gate",
    "exit/codes-0-1-2": "every case compares the 2 exit codes",
    "live/real-tree-passes": "each gate's own live tree; the pin comparison holds the copy's pin",
}
PARITY_ROOT_LINK = "elsewhere"


def _load_opf_copy(root):
    """(module, None) for the OPF copy under root, (None, None) when it is absent, or (None, the reason) when
    it is present but cannot be loaded: not a regular file, unreadable, failing to import, or without a name
    the parity check reads. The copy is loaded in process and never launched."""
    path = Path(root) / OPF_COPY_REL
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, "{}: cannot stat ({})".format(OPF_COPY_REL, exc)
    if not stat.S_ISREG(mode):
        return None, "{}: not a regular file".format(OPF_COPY_REL)
    try:
        spec = importlib.util.spec_from_file_location("_opf_baseline_pin_copy", str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as exc:
        return None, "{}: does not load ({}: {})".format(OPF_COPY_REL, type(exc).__name__, exc)
    missing = [name for name in OPF_COPY_NAMES if not hasattr(module, name)]
    if missing:
        return None, "{}: does not define {}".format(OPF_COPY_REL, ", ".join(missing))
    paths = (module.ATTRIBUTION_REL,) + tuple(module.REQUIRED_NOTICES)
    if not all(_safe_rel(rel) for rel in paths) or not all(
            isinstance(key, str) and key for key in module.ATTRIBUTION_TEXT_KEYS):
        return None, "{}: an attribution path, notice path or attribution key is misshapen".format(OPF_COPY_REL)
    return module, None


def _parity_files(copy):
    """The fixture copy: FIXTURE_FILES plus each licence or notice file the copy requires every pin to list."""
    files = dict(FIXTURE_FILES)
    for rel in copy.REQUIRED_NOTICES:
        files.setdefault(rel, "notice {}\n".format(rel).encode("utf-8"))
    return files


def _parity_attribution(copy, **kwargs):
    """_attribution_text(**kwargs) with each attribution key the copy requires, as a non-empty string, added
    to the last table (the named block) where this gate's fixture does not already set it."""
    text = _attribution_text(**kwargs)
    present = ("name", "version", "homepage", "verified")
    return text + "".join('"{}" = "parity {}"\n'.format(key, key)
                          for key in copy.ATTRIBUTION_TEXT_KEYS if key not in present)


def _parity_link_file(tmp, _attributions):
    target = tmp / FIXTURE_ROOT / "baseline.css"
    os.unlink(target)
    os.symlink(tmp / PIN_REL, target)


def _parity_link_dir(tmp, _attributions):
    os.symlink(tmp / FIXTURE_ROOT / "fonts", tmp / FIXTURE_ROOT / "fonts2")


def _parity_link_root(tmp, _attributions):
    os.rename(tmp / FIXTURE_ROOT, tmp / PARITY_ROOT_LINK)
    os.symlink(tmp / PARITY_ROOT_LINK, tmp / FIXTURE_ROOT)


def _parity_fifo(tmp, _attributions):
    os.mkfifo(tmp / FIXTURE_ROOT / "pipe")


def _parity_link_tool(tmp, _attributions):
    os.rename(tmp / TOOL_REL, tmp / "real-tool.py")
    os.symlink(tmp / "real-tool.py", tmp / TOOL_REL)


def _parity_fifo_tool(tmp, _attributions):
    os.unlink(tmp / TOOL_REL)
    os.mkfifo(tmp / TOOL_REL)


def _parity_link_attribution(tmp, attributions):
    for number, rel in enumerate(attributions):
        real = tmp / "real-attribution-{}.toml".format(number)
        os.rename(tmp / rel, real)
        os.symlink(real, tmp / rel)


def _deny_reader(_original):
    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))
    return _deny


def _deny_tool_reader(original):
    def _deny(path):
        if str(path).endswith(TOOL_REL):
            raise PermissionError(13, "read refused by the self-test", str(path))
        return original(path)
    return _deny


def _own_readers():
    """This gate's 3 readers, in PARITY_READERS order."""
    return (_read_pin_bytes, _read_attribution_bytes, _sha256)


def _bind_own_readers(readers):
    """Bind this gate's 3 readers, in PARITY_READERS order (a parity case's reader failure, then the originals)."""
    global _read_pin_bytes, _read_attribution_bytes, _sha256
    _read_pin_bytes, _read_attribution_bytes, _sha256 = readers


def _copy_readers(copy):
    """The copy's 3 readers, in PARITY_READERS order."""
    return (copy._read_pin_bytes, copy._read_attribution_bytes, copy._sha256)


def _bind_copy_readers(copy, readers):
    """Bind the copy's 3 readers, in PARITY_READERS order."""
    copy._read_pin_bytes, copy._read_attribution_bytes, copy._sha256 = readers


def _parity_cases(copy):
    """[(shared check id, case)] mirroring each fixture case of this gate's self-test that the copy's
    self-test shares, built so the exact tree also meets the copy's own rules. A case is a dict of
    _parity_codes inputs; an absent key takes the exact fixture's value."""
    files = _parity_files(copy)
    good = _pin_text(files, tools=FIXTURE_TOOLS)
    second = _pin_text(files, root=SECOND_ROOT, tools=FIXTURE_TOOLS)
    both = {"pins": TWO_PINS, "second": (files, second)}
    attribution = _parity_attribution(copy)
    css_digest = hashlib.sha256(files["baseline.css"]).hexdigest()
    css_key = '"{}/baseline.css"'.format(FIXTURE_ROOT)
    tool_digest = hashlib.sha256(FIXTURE_TOOLS[TOOL_REL]).hexdigest()
    version_line = 'version = "{}"'.format(FIXTURE_VERSION)
    archive = "https://example.org/baseline/baseline-{}.tar.gz".format(FIXTURE_VERSION)
    ending = "/baseline-{}.tar.gz".format(FIXTURE_VERSION)
    pin_pad = MAX_PIN_BYTES - len(good.encode("utf-8"))
    attribution_pad = MAX_ATTRIBUTION_BYTES - len(attribution.encode("utf-8"))
    cases = [
        ("pass/fixture-exact", {}),
        ("refuse/differing-file", {"files": dict(files, **{"baseline.css": b"body{ }\n"})}),
        ("refuse/differing-file-same-length", {"files": dict(files, **{"fonts/a.woff2": b"wOF2\x00\x02"})}),
        ("refuse/missing-file", {"files": {rel: data for rel, data in files.items() if rel != "marks/NOTICE.md"}}),
        ("refuse/stray-file", {"files": dict(files, **{"search.js": b"x\n"})}),
        ("refuse/stray-file-nested", {"files": dict(files, **{"reference/landing.html": b"<p>\n"})}),
        ("refuse/symlink-file", {"mutate": _parity_link_file}),
        ("refuse/symlink-dir", {"mutate": _parity_link_dir}),
        ("refuse/symlink-root", {"mutate": _parity_link_root}),
        ("refuse/fifo-not-regular", {"mutate": _parity_fifo}),
        ("refuse/root-missing", {"files": {}}),
        ("refuse/pin-missing-cannot-evaluate", {"pin": False}),
        ("refuse/pin-unreadable-cannot-evaluate", {"deny": ("_read_pin_bytes", _deny_reader)}),
        ("refuse/file-unreadable-cannot-evaluate", {"deny": ("_sha256", _deny_reader)}),
        ("malformed/not-utf8", {"pin": good.encode("utf-8") + b"# \xff\n"}),
        ("malformed/not-toml", {"pin": good + "[files\n"}),
        ("malformed/version-missing", {"pin": good.replace(version_line + "\n", "")}),
        ("malformed/version-not-string", {"pin": good.replace(version_line, "version = 1")}),
        ("malformed/version-shape", {"pin": _pin_text(files, version="1.2", tools=FIXTURE_TOOLS)}),
        ("malformed/unknown-key", {"pin": _pin_text(files, extra='colour = "blue"', tools=FIXTURE_TOOLS)}),
        ("malformed/no-files", {"pin": version_line + "\n"}),
        ("malformed/files-not-table", {"pin": version_line + '\nfiles = "baseline.css"\n'}),
        ("malformed/outside-copy-root",
         {"pin": _pin_text(files, root="site/draft/assets/other", tools=FIXTURE_TOOLS)}),
        ("malformed/copy-dir-inside-a-name",
         {"pin": _pin_text(files, root="site/draft/xassets/baseline", tools=FIXTURE_TOOLS)}),
        ("malformed/absolute-path",
         {"pin": _pin_text(files, root="/site/draft/assets/baseline", tools=FIXTURE_TOOLS)}),
        ("malformed/unsafe-path", {"pin": good.replace(css_key, '"{}/../baseline.css"'.format(FIXTURE_ROOT))}),
        ("malformed/short-digest", {"pin": good.replace(css_digest, css_digest[:-1])}),
        ("malformed/upper-digest", {"pin": good.replace(css_digest, css_digest.upper())}),
        ("malformed/duplicate-path", {"pin": good + '{} = "{}"\n'.format(css_key, "0" * 64)}),
        ("malformed/two-copy-roots", {"pin": good + '"site/assets/baseline/x" = "{}"\n'.format("0" * 64)}),
        ("malformed/recursion-error", {"loads": (RecursionError, "[files]")}),
        ("malformed/memory-error", {"loads": (MemoryError, "[files]")}),
        ("version/attribution-recursion-error", {"loads": (RecursionError, "[vendored.")}),
        ("version/attribution-memory-error", {"loads": (MemoryError, "[vendored.")}),
        ("bound/pin-size-under", {"pin": good + "#" + "x" * (pin_pad - 3) + "\n"}),
        ("bound/pin-size-at", {"pin": good + "#" + "x" * (pin_pad - 2) + "\n"}),
        ("bound/pin-size-over", {"pin": good + "#" + "x" * (pin_pad - 1) + "\n"}),
        ("registry/two-pairs-pass", both),
        ("registry/second-copy-differing",
         dict(both, second=(dict(files, **{"baseline.css": b"body{ }\n"}), second))),
        ("registry/second-copy-stray", dict(both, second=(dict(files, **{"search.js": b"x\n"}), second))),
        ("registry/second-pin-missing", dict(both, second=(files, False))),
        ("registry/pin-names-other-root", dict(both, second=(files, good))),
        ("registry/first-pin-names-second-root", dict(both, pin=second)),
        ("registry/duplicate-root", dict(both, pins=((PIN_REL, FIXTURE_ROOT), (SECOND_PIN, FIXTURE_ROOT)))),
        ("registry/duplicate-pin", dict(both, pins=((PIN_REL, FIXTURE_ROOT), (PIN_REL, SECOND_ROOT)))),
        ("registry/empty", dict(both, pins=())),
        ("tool/pinned-passes", {}),
        ("tool/both-pins-pass", both),
        ("tool/differing-refused", {"tools": {TOOL_REL: b"import os\n"}}),
        ("tool/missing-refused", {"tools": {}}),
        ("tool/symlink-refused", {"mutate": _parity_link_tool}),
        ("tool/fifo-not-regular-refused", {"mutate": _parity_fifo_tool}),
        ("tool/unreadable-cannot-evaluate", {"deny": ("_sha256", _deny_tool_reader)}),
        ("tool/other-baseline-path-malformed",
         {"pin": good.replace('"{}"'.format(TOOL_REL), '".baseline/check_other.py"')}),
        ("tool/near-miss-path-malformed",
         {"pin": good.replace('"{}"'.format(TOOL_REL), '"baseline/check_palette.py"')}),
        ("tool/only-tool-malformed", {"pin": '{}\n\n[files]\n"{}" = "{}"\n'.format(version_line, TOOL_REL,
                                                                                    tool_digest)}),
        ("tool/upper-digest-malformed", {"pin": good.replace(tool_digest, tool_digest.upper())}),
        ("version/pin-differs-from-attribution", {"attribution": _parity_attribution(copy, version="1.2.2")}),
        ("version/both-pins-differ-from-attribution",
         dict(both, attribution=_parity_attribution(copy, version="1.2.2"))),
        ("version/pins-disagree",
         dict(both, second=(files, _pin_text(files, root=SECOND_ROOT, version="1.2.2", tools=FIXTURE_TOOLS)))),
        ("version/attribution-missing", {"attribution": False}),
        ("version/attribution-unreadable", {"deny": ("_read_attribution_bytes", _deny_reader)}),
        ("version/attribution-not-regular", {"mutate": _parity_link_attribution}),
        ("version/attribution-not-utf8", {"attribution": attribution.encode("utf-8") + b"# \xff\n"}),
        ("version/attribution-not-toml", {"attribution": attribution + "[vendored\n"}),
        ("version/attribution-block-missing", {"attribution": _parity_attribution(copy, block="other-template")}),
        ("version/attribution-vendored-not-table", {"attribution": 'vendored = "site-template"\n'}),
        ("version/attribution-block-not-table",
         {"attribution": '[vendored]\n"site-template" = "{}"\n'.format(FIXTURE_VERSION)}),
        ("version/attribution-version-missing", {"attribution": attribution.replace(version_line + "\n", "")}),
        ("version/attribution-version-not-string",
         {"attribution": attribution.replace(version_line, "version = 1")}),
        ("version/attribution-version-shape", {"attribution": _parity_attribution(copy, version="1.2")}),
        ("bound/attribution-size-at", {"attribution": attribution + "#" + "x" * (attribution_pad - 2) + "\n"}),
        ("bound/attribution-size-over",
         {"attribution": attribution + "#" + "x" * (attribution_pad - 1) + "\n"}),
        ("homepage/matching-passes", {"attribution": _parity_attribution(copy, homepage=archive)}),
        ("homepage/other-version-refused",
         {"attribution": _parity_attribution(copy, homepage=archive.replace(FIXTURE_VERSION, "1.2.2"))}),
        ("homepage/missing-refused", {"attribution": _parity_attribution(copy, homepage=False)}),
        ("homepage/other-file-name-refused",
         {"attribution": _parity_attribution(copy, homepage=archive.replace(".tar.gz", ".zip"))}),
        ("homepage/name-prefix-refused",
         {"attribution": _parity_attribution(copy, homepage=archive.replace(ending, "/x" + ending[1:]))}),
        ("homepage/trailing-suffix-refused", {"attribution": _parity_attribution(copy, homepage=archive + ".sig")}),
        ("homepage/not-string-refused",
         {"attribution": attribution.replace('homepage = "{}"'.format(archive), "homepage = 1")}),
    ]
    return cases


def _parity_codes(copy, case):
    """[this gate's exit code, the copy's] (run_gate) over 1 fresh fixture tree for case, with the case's
    reader failure or parser error applied to both gates alike; their report lines go to a buffer."""
    files = case.get("files", _parity_files(copy))
    attribution = case.get("attribution", _parity_attribution(copy))
    attributions = (ATTRIBUTION_REL, copy.ATTRIBUTION_REL)
    own, copied = _own_readers(), _copy_readers(copy)
    original_loads = tomllib.loads
    with tempfile.TemporaryDirectory(prefix="baseline-pin-parity-") as tmp, \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        _fixture(tmp, files, case.get("pin", _pin_text(_parity_files(copy), tools=FIXTURE_TOOLS)),
                 attribution=attribution, tools=case.get("tools", FIXTURE_TOOLS))
        if attribution is not False:
            target = Path(tmp) / copy.ATTRIBUTION_REL
            os.makedirs(target.parent, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(attribution.encode("utf-8") if isinstance(attribution, str) else attribution)
        if case.get("second") is not None:
            _fixture(tmp, case["second"][0], case["second"][1], root=SECOND_ROOT, pin_rel=SECOND_PIN,
                     attribution=False, tools={})
        if case.get("mutate") is not None:
            case["mutate"](Path(tmp), attributions)
        try:
            if case.get("deny") is not None:
                name, make = case["deny"]
                index = PARITY_READERS.index(name)
                _bind_own_readers(tuple(make(reader) if number == index else reader
                                        for number, reader in enumerate(own)))
                _bind_copy_readers(copy, tuple(make(reader) if number == index else reader
                                               for number, reader in enumerate(copied)))
            if case.get("loads") is not None:
                exc_type, marker = case["loads"]

                def _raise(text, _exc_type=exc_type, _marker=marker, _loads=original_loads):
                    if _marker not in text:
                        return _loads(text)
                    raise _exc_type("raised by the self-test")

                tomllib.loads = _raise
            pins = case.get("pins", FIXTURE_PINS)
            return [run_gate(tmp, pins), copy.run_gate(tmp, pins)]
        finally:
            tomllib.loads = original_loads
            _bind_own_readers(own)
            _bind_copy_readers(copy, copied)


def _parity_mismatches(copy, cases):
    """([(case id, this gate's exit code, the copy's exit code)] for each case where they differ, the set of
    this gate's exit codes over the cases)."""
    mismatches, seen = [], set()
    for name, case in cases:
        codes = _parity_codes(copy, case)
        seen.add(codes[0])
        if codes[0] != codes[1]:
            mismatches.append((name, codes[0], codes[1]))
    return mismatches, seen


def _parity_case_gaps(shared, cases):
    """Lines naming each shared check id with neither a parity case nor a PARITY_EXCLUDED reason, each case
    that is not a shared id, and each exclusion that is not a shared id."""
    names = [name for name, _case in cases]
    gaps = ["{}: a shared case with no parity case".format(name)
            for name in sorted(set(shared) - set(names) - set(PARITY_EXCLUDED))]
    gaps.extend("{}: a parity case that is not shared by both self-tests".format(name)
                for name in sorted(set(names) - set(shared)))
    gaps.extend("{}: excluded but not shared by both self-tests".format(name)
                for name in sorted(set(PARITY_EXCLUDED) - set(shared)))
    gaps.extend("{}: more than 1 parity case".format(name) for name in sorted({n for n in names
                                                                              if names.count(n) > 1}))
    return gaps


def _opf_pin_problems(root):
    """Lines naming each way the OPF pin under root differs from this repository's OPF pin (PINS[1]) with
    OPF_PREFIX removed from each path: the version, the copy root, each copy-root file's digest, and each
    pinned build tool, whose 2 entries name different files (this repository's and the OPF tree's palette
    gate) and are compared by digest. A pin that is missing or does not parse is 1 problem."""
    parsed = []
    for rel in (PINS[1][0], OPF_PIN_REL):
        path = Path(root) / rel
        try:
            if not stat.S_ISREG(os.lstat(path).st_mode):
                return ["{}: not a regular file".format(rel)]
            parsed.append(parse_pin(_read_pin_bytes(path), rel))
        except OSError as exc:
            return ["{}: unreadable ({})".format(rel, exc)]
        except CannotEvaluate as exc:
            return [str(exc)]
    (parent_version, parent_roots, parent_tools), (opf_version, opf_roots, opf_tools) = parsed
    problems = []
    if parent_version != opf_version:
        problems.append("{} names version {} but {} names version {}".format(
            OPF_PIN_REL, opf_version, PINS[1][0], parent_version))
    (parent_root, parent_files), (opf_root, opf_files) = parent_roots[0], opf_roots[0]
    if parent_root != OPF_PREFIX + opf_root:
        problems.append("{} names the copy root {}, but {} with {} removed is {}".format(
            OPF_PIN_REL, opf_root, parent_root, OPF_PREFIX, parent_root.removeprefix(OPF_PREFIX)))
    for rel in sorted(set(parent_files) | set(opf_files)):
        if parent_files.get(rel) != opf_files.get(rel):
            problems.append("{}/{}: {} in {} but {} in {}".format(
                opf_root, rel, opf_files.get(rel, "absent"), OPF_PIN_REL, parent_files.get(rel, "absent"),
                PINS[1][0]))
    for rel in sorted(set(parent_tools) | set(opf_tools)):
        if parent_tools.get(rel) != opf_tools.get(rel):
            problems.append("{}: digest {} in {} but {} in {}".format(
                rel, opf_tools.get(rel, "absent"), OPF_PIN_REL, parent_tools.get(rel, "absent"), PINS[1][0]))
    return problems


def _parity_pin_case(parent_pin, opf_pin):
    """_opf_pin_problems over a tree holding only the 2 given pin texts."""
    with tempfile.TemporaryDirectory(prefix="baseline-pin-parity-") as tmp:
        for rel, text in ((PINS[1][0], parent_pin), (OPF_PIN_REL, opf_pin)):
            os.makedirs((Path(tmp) / rel).parent, exist_ok=True)
            with open(Path(tmp) / rel, "wb") as handle:
                handle.write(text.encode("utf-8"))
        return _opf_pin_problems(tmp)


def _parity_self_test(shared_ids):
    """The parity checks: each comparison's own refusal over fixtures (always run), then, while the OPF copy
    exists, the copy against this gate on the shared cases and the OPF pin against this repository's."""
    # A stand-in copy that passes every tree: the verdict comparison must name its divergence.
    stand_in = types.SimpleNamespace(run_gate=lambda root, pins: 0, ATTRIBUTION_REL="attribution.toml",
                                     REQUIRED_NOTICES=(), ATTRIBUTION_TEXT_KEYS=(), EXPECTED=frozenset(),
                                     _read_pin_bytes=_read_pin_bytes,
                                     _read_attribution_bytes=_read_attribution_bytes, _sha256=_sha256)
    mismatches, _seen = _parity_mismatches(stand_in, _parity_cases(stand_in))
    check("parity/verdict-divergence-detected", ("refuse/differing-file", 1, 0) in mismatches
          and ("malformed/not-toml", 2, 0) in mismatches and not any(
              name == "pass/fixture-exact" for name, _parent, _copy in mismatches), mismatches)

    cases = _parity_cases(stand_in)
    shared = set(name for name, _case in cases) | set(PARITY_EXCLUDED) | {"refuse/unlisted-case"}
    gaps = _parity_case_gaps(shared, cases)
    check("parity/shared-case-gap-detected", gaps == ["refuse/unlisted-case: a shared case with no parity case"],
          gaps)

    files = _parity_files(stand_in)
    parent_pin = _pin_text(files, root=OPF_PREFIX + FIXTURE_ROOT, tools=FIXTURE_TOOLS)
    opf_pin = _pin_text(files, tools=FIXTURE_TOOLS)
    css_digest = hashlib.sha256(files["baseline.css"]).hexdigest()
    tool_digest = hashlib.sha256(FIXTURE_TOOLS[TOOL_REL]).hexdigest()
    problems = _parity_pin_case(parent_pin, opf_pin)
    check("parity/pin-identical-after-strip-passes", problems == [], problems)
    problems = _parity_pin_case(parent_pin, opf_pin.replace(css_digest, "0" * 64))
    check("parity/pin-file-digest-divergence-detected", len(problems) == 1
          and problems[0].startswith(FIXTURE_ROOT + "/baseline.css: " + "0" * 64), problems)
    problems = _parity_pin_case(parent_pin, _pin_text(files, root=OPF_PREFIX + FIXTURE_ROOT, tools=FIXTURE_TOOLS))
    check("parity/pin-path-prefix-divergence-detected", len(problems) == 1
          and "names the copy root " + OPF_PREFIX + FIXTURE_ROOT in problems[0], problems)
    problems = _parity_pin_case(parent_pin, opf_pin.replace(tool_digest, "0" * 64))
    check("parity/pin-tool-digest-divergence-detected", problems == ["{}: digest {} in {} but {} in {}".format(
        TOOL_REL, "0" * 64, OPF_PIN_REL, tool_digest, PINS[1][0])], problems)
    problems = _parity_pin_case(parent_pin, opf_pin.replace(FIXTURE_VERSION, "1.2.2", 1))
    check("parity/pin-version-divergence-detected", len(problems) == 1 and "names version 1.2.2" in problems[0],
          problems)

    # Absent skips; present but not loadable (a directory, a file that does not import, a file without the
    # names the check reads) fails.
    with tempfile.TemporaryDirectory(prefix="baseline-pin-parity-") as tmp:
        absent = _load_opf_copy(tmp)
        target = Path(tmp) / OPF_COPY_REL
        os.makedirs(target)
        broken = [_load_opf_copy(tmp)]
        os.rmdir(target)
        for text in ("def broken(:\n", "raise SystemExit(3)\n", "ATTRIBUTION_REL = 'attribution.toml'\n"):
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(text)
            broken.append(_load_opf_copy(tmp))
    check("parity/absent-copy-skips", absent == (None, None), absent)
    check("parity/unloadable-copy-fails", len(broken) == 4 and all(
        module is None and isinstance(reason, str) and reason.startswith(OPF_COPY_REL + ": ")
        for module, reason in broken), broken)

    copy, reason = _load_opf_copy(ROOT)
    if copy is None and reason is None:
        print("SKIP: parity with the OPF copy: {} is absent, so no copy is held to this gate's verdicts or "
              "pin".format(OPF_COPY_REL))
        return
    check("parity/copy-present-loads", copy is not None, reason)
    if copy is None:
        return
    cases = _parity_cases(copy)
    shared = set(shared_ids) & set(copy.EXPECTED)
    gaps = _parity_case_gaps(shared, cases)
    check("parity/shared-cases-covered", gaps == [], gaps)
    mismatches, seen = _parity_mismatches(copy, cases)
    check("parity/copy-gives-parent-verdict", mismatches == [] and seen == {0, 1, 2}, (mismatches, seen))
    problems = _opf_pin_problems(ROOT)
    check("parity/pin-matches-parent-pin", problems == [], problems)


def _self_test():
    findings, errors = _scan_case()
    check("pass/fixture-exact", findings == [] and errors == [], (findings, errors))

    changed = dict(FIXTURE_FILES, **{"baseline.css": b"body{ }\n"})
    findings, errors = _scan_case(files=changed)
    check("refuse/differing-file", errors == [] and len(findings) == 1
          and findings[0].startswith(FIXTURE_ROOT + "/baseline.css: SHA-256"), (findings, errors))

    # One flipped byte in a binary file, the same length, is still a difference.
    changed = dict(FIXTURE_FILES, **{"fonts/a.woff2": b"wOF2\x00\x02"})
    findings, errors = _scan_case(files=changed)
    check("refuse/differing-file-same-length", errors == [] and len(findings) == 1
          and "fonts/a.woff2: SHA-256" in findings[0], (findings, errors))

    missing = {rel: data for rel, data in FIXTURE_FILES.items() if rel != "marks/NOTICE.md"}
    findings, errors = _scan_case(files=missing)
    check("refuse/missing-file", errors == [] and findings == [FIXTURE_ROOT + "/marks/NOTICE.md: missing"],
          (findings, errors))

    stray = dict(FIXTURE_FILES, **{"search.js": b"x\n"})
    findings, errors = _scan_case(files=stray)
    check("refuse/stray-file", errors == [] and len(findings) == 1 and "search.js: stray file" in findings[0],
          (findings, errors))

    stray = dict(FIXTURE_FILES, **{"reference/landing.html": b"<p>\n"})
    findings, errors = _scan_case(files=stray)
    check("refuse/stray-file-nested", errors == [] and len(findings) == 1
          and "reference/landing.html: stray file" in findings[0], (findings, errors))

    def _link_file(tmp):
        target = tmp / FIXTURE_ROOT / "baseline.css"
        os.unlink(target)
        os.symlink(tmp / PIN_REL, target)

    findings, errors = _scan_case(mutate=_link_file)
    check("refuse/symlink-file", errors == [] and findings == [FIXTURE_ROOT + "/baseline.css: is a symbolic link"],
          (findings, errors))

    def _link_dir(tmp):
        os.symlink(tmp / FIXTURE_ROOT / "fonts", tmp / FIXTURE_ROOT / "fonts2")

    findings, errors = _scan_case(mutate=_link_dir)
    check("refuse/symlink-dir", errors == [] and findings == [FIXTURE_ROOT + "/fonts2: is a symbolic link"],
          (findings, errors))

    def _link_root(tmp):
        real = tmp / "elsewhere"
        os.rename(tmp / FIXTURE_ROOT, real)
        os.symlink(real, tmp / FIXTURE_ROOT)

    findings, errors = _scan_case(mutate=_link_root)
    check("refuse/symlink-root", errors == [] and len(findings) == 1 and "copy root is a symbolic link" in findings[0],
          (findings, errors))

    def _fifo(tmp):
        os.mkfifo(tmp / FIXTURE_ROOT / "pipe")

    findings, errors = _scan_case(mutate=_fifo)
    check("refuse/fifo-not-regular", errors == [] and findings == [FIXTURE_ROOT + "/pipe: not a regular file"],
          (findings, errors))

    findings, errors = _scan_case(files={})
    check("refuse/root-missing", errors == [] and len(findings) == len(FIXTURE_FILES)
          and all(line.endswith(": missing") for line in findings), (findings, errors))

    findings, errors = _scan_case(pin=False)
    check("refuse/pin-missing-cannot-evaluate", findings == [] and errors == [PIN_REL + ": missing"],
          (findings, errors))

    original = _read_pin_bytes

    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_read_pin_bytes"] = _deny
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_read_pin_bytes"] = original
    check("refuse/pin-unreadable-cannot-evaluate", findings == [] and len(errors) == 1
          and "unreadable" in errors[0], (findings, errors))

    original_hash = _sha256

    def _deny_hash(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_sha256"] = _deny_hash
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_sha256"] = original_hash
    check("refuse/file-unreadable-cannot-evaluate", findings == [] and len(errors) == len(FIXTURE_FILES)
          and all("unreadable" in line for line in errors), (findings, errors))

    good = _pin_text(FIXTURE_FILES)
    _css_digest = hashlib.sha256(FIXTURE_FILES["baseline.css"]).hexdigest()
    css_key = '"{}/baseline.css"'.format(FIXTURE_ROOT)
    for name, pin in (
            ("malformed/not-utf8", good.encode("utf-8") + b"# \xff\n"),
            ("malformed/not-toml", good + "[files\n"),
            ("malformed/version-missing", good.replace('version = "1.2.1"\n', "")),
            ("malformed/version-not-string", good.replace('version = "1.2.1"', "version = 1")),
            ("malformed/version-shape", _pin_text(FIXTURE_FILES, version="1.2")),
            ("malformed/unknown-key", _pin_text(FIXTURE_FILES, extra='colour = "blue"')),
            ("malformed/no-files", 'version = "1.2.1"\n'),
            ("malformed/files-not-table", 'version = "1.2.1"\nfiles = "baseline.css"\n'),
            ("malformed/outside-copy-root", _pin_text(FIXTURE_FILES, root="site/draft/assets/other")),
            ("malformed/copy-dir-inside-a-name", _pin_text(FIXTURE_FILES, root="site/draft/xassets/baseline")),
            ("malformed/absolute-path", _pin_text(FIXTURE_FILES, root="/site/draft/assets/baseline")),
            ("malformed/unsafe-path", good.replace(css_key, '"{}/../baseline.css"'.format(FIXTURE_ROOT))),
            ("malformed/short-digest", good.replace(_css_digest, _css_digest[:-1])),
            ("malformed/upper-digest", good.replace(_css_digest, _css_digest.upper())),
            ("malformed/duplicate-path", good + '{} = "{}"\n'.format(css_key, "0" * 64)),
            ("malformed/two-copy-roots", good + '"site/assets/baseline/x" = "{}"\n'.format("0" * 64))):
        findings, errors = _scan_case(pin=pin)
        check(name, findings == [] and len(errors) == 1, (findings, errors))

    # The parser's resource errors are handled at each parse site, never propagated. The injection raises
    # only for the document that carries the marker (a pin's [files] table or the attribution file's
    # [vendored. tables) and parses the other one, so each case reaches 1 parse site.
    for name, exc_type, marker, where in (
            ("malformed/recursion-error", RecursionError, "[files]", PIN_REL),
            ("malformed/memory-error", MemoryError, "[files]", PIN_REL),
            ("version/attribution-recursion-error", RecursionError, "[vendored.", ATTRIBUTION_REL),
            ("version/attribution-memory-error", MemoryError, "[vendored.", ATTRIBUTION_REL)):
        original_loads = tomllib.loads

        def _raise(text, _exc_type=exc_type, _marker=marker, _loads=original_loads):
            if _marker not in text:
                return _loads(text)
            raise _exc_type("raised by the self-test")

        tomllib.loads = _raise
        try:
            findings, errors = _scan_case()
        finally:
            tomllib.loads = original_loads
        check(name, findings == [] and len(errors) == 1 and errors[0].startswith(where + ":")
              and exc_type.__name__ in errors[0], (findings, errors))

    # The pin size bound at its ends: one byte under, exactly at, and one byte over MAX_PIN_BYTES.
    pad = MAX_PIN_BYTES - len(good.encode("utf-8"))
    for name, extra, refused in (("bound/pin-size-under", pad - 1, False), ("bound/pin-size-at", pad, False),
                                 ("bound/pin-size-over", pad + 1, True)):
        # A trailing comment changes only the size; its "#" and newline are part of the padding.
        body = good + "#" + "x" * (extra - 2) + "\n"
        try:
            parse_pin(body.encode("utf-8"), "fixture")
            got = False
        except CannotEvaluate:
            got = True
        check(name, got == refused and len(body.encode("utf-8")) == len(good.encode("utf-8")) + extra,
              (len(body), got))

    # The registry: every pin is bound to its own copy root. Both pairs pass together; a defect in the
    # second copy is reported against the second root and pin; a pin naming another root (so 2 pins would
    # validate 1 copy) and a registry repeating a pin or a root are cannot-evaluate.
    check("registry/live-pairs", PINS == ((".baseline/pin.toml", "site/draft/assets/baseline"),
                                          (".baseline/opfiles-pin.toml", "opf/site/draft/assets/baseline")),
          PINS)
    findings, errors = _scan_case(pins=TWO_PINS, second=(None, None))
    check("registry/two-pairs-pass", findings == [] and errors == [], (findings, errors))

    changed = dict(FIXTURE_FILES, **{"baseline.css": b"body{ }\n"})
    findings, errors = _scan_case(pins=TWO_PINS, second=(changed, None))
    check("registry/second-copy-differing", errors == [] and len(findings) == 1
          and findings[0].startswith(SECOND_ROOT + "/baseline.css: SHA-256"), (findings, errors))

    stray = dict(FIXTURE_FILES, **{"search.js": b"x\n"})
    findings, errors = _scan_case(pins=TWO_PINS, second=(stray, None))
    check("registry/second-copy-stray", errors == [] and findings == [
        "{}/search.js: stray file, not listed in {}".format(SECOND_ROOT, SECOND_PIN)], (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, second=(None, False))
    check("registry/second-pin-missing", findings == [] and errors == [SECOND_PIN + ": missing"],
          (findings, errors))

    # The second pin names the first copy root: it would re-validate the first copy and leave the second
    # unchecked, so it is refused even though every digest it lists matches.
    findings, errors = _scan_case(pins=TWO_PINS, second=(None, _pin_text(FIXTURE_FILES)))
    check("registry/pin-names-other-root", findings == [] and len(errors) == 1
          and errors[0].startswith(SECOND_PIN + ": names the copy root " + FIXTURE_ROOT), (findings, errors))

    # The first pin names a root that is not its own, with the files present there.
    findings, errors = _scan_case(pins=TWO_PINS, second=(None, None),
                                  pin=_pin_text(FIXTURE_FILES, root=SECOND_ROOT))
    check("registry/first-pin-names-second-root", findings == [] and len(errors) == 1
          and "registered root is " + FIXTURE_ROOT in errors[0], (findings, errors))

    for name, pins in (("registry/duplicate-root", ((PIN_REL, FIXTURE_ROOT), (SECOND_PIN, FIXTURE_ROOT))),
                       ("registry/duplicate-pin", ((PIN_REL, FIXTURE_ROOT), (PIN_REL, SECOND_ROOT))),
                       ("registry/empty", ())):
        findings, errors = _scan_case(pins=pins, second=(None, None))
        check(name, findings == [] and len(errors) == 1 and "registry" in errors[0], (findings, errors))

    # A build tool pinned outside the copy root: hashed as 1 regular file at its own path, with no stray scan
    # of its directory (the pin itself sits beside it); only a BUILD_TOOLS path may sit outside a copy root.
    check("tool/registered-path", BUILD_TOOLS == (".baseline/check_palette.py",), BUILD_TOOLS)
    tool_pin = _pin_text(FIXTURE_FILES, tools=FIXTURE_TOOLS)
    findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS)
    check("tool/pinned-passes", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, pin=tool_pin, tools=FIXTURE_TOOLS,
                                  second=(None, _pin_text(FIXTURE_FILES, root=SECOND_ROOT, tools=FIXTURE_TOOLS)))
    check("tool/both-pins-pass", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case(pin=tool_pin, tools={TOOL_REL: b"import os\n"})
    check("tool/differing-refused", errors == [] and len(findings) == 1
          and findings[0].startswith(TOOL_REL + ": SHA-256"), (findings, errors))

    findings, errors = _scan_case(pin=tool_pin)
    check("tool/missing-refused", errors == [] and findings == [TOOL_REL + ": missing"], (findings, errors))

    def _link_tool(tmp):
        real = tmp / "real-tool.py"
        os.rename(tmp / TOOL_REL, real)
        os.symlink(real, tmp / TOOL_REL)

    findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS, mutate=_link_tool)
    check("tool/symlink-refused", errors == [] and findings == [TOOL_REL + ": is a symbolic link"],
          (findings, errors))

    def _fifo_tool(tmp):
        os.unlink(tmp / TOOL_REL)
        os.mkfifo(tmp / TOOL_REL)

    findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS, mutate=_fifo_tool)
    check("tool/fifo-not-regular-refused", errors == [] and findings == [TOOL_REL + ": not a regular file"],
          (findings, errors))

    def _deny_tool_hash(path):
        if str(path).endswith(TOOL_REL):
            raise PermissionError(13, "read refused by the self-test", str(path))
        return original_hash(path)

    globals()["_sha256"] = _deny_tool_hash
    try:
        findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS)
    finally:
        globals()["_sha256"] = original_hash
    check("tool/unreadable-cannot-evaluate", findings == [] and len(errors) == 1
          and errors[0].startswith(TOOL_REL + ": unreadable"), (findings, errors))

    # Only the registered path is exempt: a sibling under .baseline/, a near-miss name and a copy-root-less
    # pin stay cannot-evaluate; a malformed tool digest is refused like any other.
    tool_digest = hashlib.sha256(FIXTURE_TOOLS[TOOL_REL]).hexdigest()
    for name, pin in (
            ("tool/other-baseline-path-malformed",
             tool_pin.replace('"{}"'.format(TOOL_REL), '".baseline/check_other.py"')),
            ("tool/near-miss-path-malformed",
             tool_pin.replace('"{}"'.format(TOOL_REL), '"baseline/check_palette.py"')),
            ("tool/only-tool-malformed",
             'version = "{}"\n\n[files]\n"{}" = "{}"\n'.format(FIXTURE_VERSION, TOOL_REL, tool_digest)),
            ("tool/upper-digest-malformed", tool_pin.replace(tool_digest, tool_digest.upper()))):
        findings, errors = _scan_case(pin=pin, tools=FIXTURE_TOOLS)
        check(name, findings == [] and len(errors) == 1 and errors[0].startswith(PIN_REL + ":"), (findings, errors))

    # The version rule: every pin names the version the attribution file records, and the pins agree. Each
    # case's digests all match, so only the version can refuse it.
    findings, errors = _scan_case(attribution=_attribution_text(version="1.2.2"))
    check("version/pin-differs-from-attribution", findings == [] and errors == [
        "{}: version {} differs from the site template version 1.2.2 recorded in {}".format(
            PIN_REL, FIXTURE_VERSION, ATTRIBUTION_WHERE)], (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, second=(None, None), attribution=_attribution_text(version="1.2.2"))
    check("version/both-pins-differ-from-attribution", findings == [] and len(errors) == 2
          and errors[0].startswith(PIN_REL + ": version " + FIXTURE_VERSION + " differs")
          and errors[1].startswith(SECOND_PIN + ": version " + FIXTURE_VERSION + " differs"), (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, second=(None, _pin_text(FIXTURE_FILES, root=SECOND_ROOT,
                                                                         version="1.2.2")))
    check("version/pins-disagree", findings == [] and len(errors) == 2 and errors[0] == (
        "the pins disagree: {} names version {} but {} names version 1.2.2; every registered pin must name "
        "the same version".format(PIN_REL, FIXTURE_VERSION, SECOND_PIN))
          and errors[1].startswith(SECOND_PIN + ": version 1.2.2 differs"), (findings, errors))

    findings, errors = _scan_case(attribution=False)
    check("version/attribution-missing", findings == [] and errors == [ATTRIBUTION_REL + ": missing"],
          (findings, errors))

    original_attribution = _read_attribution_bytes
    globals()["_read_attribution_bytes"] = _deny
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_read_attribution_bytes"] = original_attribution
    check("version/attribution-unreadable", findings == [] and len(errors) == 1
          and errors[0].startswith(ATTRIBUTION_REL + ": unreadable"), (findings, errors))

    def _link_attribution(tmp):
        real = tmp / "real-attribution.toml"
        os.rename(tmp / ATTRIBUTION_REL, real)
        os.symlink(real, tmp / ATTRIBUTION_REL)

    findings, errors = _scan_case(mutate=_link_attribution)
    check("version/attribution-not-regular", findings == []
          and errors == [ATTRIBUTION_REL + ": not a regular file"], (findings, errors))

    good_attribution = _attribution_text()
    for name, attribution in (
            ("version/attribution-not-utf8", good_attribution.encode("utf-8") + b"# \xff\n"),
            ("version/attribution-not-toml", good_attribution + "[vendored\n"),
            ("version/attribution-block-missing", _attribution_text(block="other-template")),
            ("version/attribution-vendored-not-table", 'vendored = "site-template"\n'),
            ("version/attribution-block-not-table", '[vendored]\n"site-template" = "{}"\n'.format(FIXTURE_VERSION)),
            ("version/attribution-version-missing",
             good_attribution.replace('version = "{}"\n'.format(FIXTURE_VERSION), "")),
            ("version/attribution-version-not-string",
             good_attribution.replace('version = "{}"'.format(FIXTURE_VERSION), "version = 1")),
            ("version/attribution-version-shape", _attribution_text(version="1.2"))):
        findings, errors = _scan_case(attribution=attribution)
        check(name, findings == [] and len(errors) == 1 and errors[0].startswith(ATTRIBUTION_REL + ":"),
              (findings, errors))

    # The attribution size bound at its ends: exactly at MAX_ATTRIBUTION_BYTES passes, one byte over refuses.
    pad = MAX_ATTRIBUTION_BYTES - len(good_attribution.encode("utf-8"))
    for name, extra, refused in (("bound/attribution-size-at", pad, False),
                                 ("bound/attribution-size-over", pad + 1, True)):
        body = good_attribution + "#" + "x" * (extra - 2) + "\n"
        findings, errors = _scan_case(attribution=body)
        check(name, findings == [] and len(body.encode("utf-8")) == MAX_ATTRIBUTION_BYTES + extra - pad
              and errors == ([ATTRIBUTION_REL + ": larger than {} bytes".format(MAX_ATTRIBUTION_BYTES)]
                             if refused else []), (len(body), findings, errors))

    # The homepage rule: the attribution table's archive URL ends in the recorded version's own archive name.
    # Every pin matches, so only the homepage can refuse a case.
    archive = "https://example.org/baseline/baseline-{}.tar.gz".format(FIXTURE_VERSION)
    findings, errors = _scan_case(attribution=_attribution_text(homepage=archive))
    check("homepage/matching-passes", findings == [] and errors == [], (findings, errors))

    ending = "/baseline-{}.tar.gz".format(FIXTURE_VERSION)
    for name, homepage, found in (
            ("homepage/other-version-refused", archive.replace(FIXTURE_VERSION, "1.2.2"), None),
            ("homepage/missing-refused", False, "missing"),
            ("homepage/other-file-name-refused", archive.replace(".tar.gz", ".zip"), None),
            ("homepage/name-prefix-refused", archive.replace(ending, "/x" + ending[1:]), None),
            ("homepage/trailing-suffix-refused", archive + ".sig", None)):
        findings, errors = _scan_case(attribution=_attribution_text(homepage=homepage))
        check(name, findings == [] and errors == [
            '{}: [vendored."{}"] homepage must end in {} for version {}, but it is {}'.format(
                ATTRIBUTION_REL, ATTRIBUTION_BLOCK, ending, FIXTURE_VERSION, found or repr(homepage))],
              (findings, errors))

    findings, errors = _scan_case(attribution=_attribution_text().replace('homepage = "{}"'.format(archive),
                                                                         "homepage = 1"))
    check("homepage/not-string-refused", findings == [] and len(errors) == 1
          and errors[0].startswith(ATTRIBUTION_REL + ":") and errors[0].endswith("but it is 1"), (findings, errors))

    # The gate's own report lines go to a buffer: a self-test run writes nothing to its error stream.
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp, \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stream:
        _fixture(tmp)
        codes = [run_gate(tmp, FIXTURE_PINS)]
        with open(Path(tmp) / FIXTURE_ROOT / "baseline.css", "ab") as handle:
            handle.write(b"x")
        codes.append(run_gate(tmp, FIXTURE_PINS))
        os.unlink(Path(tmp) / PIN_REL)
        codes.append(run_gate(tmp, FIXTURE_PINS))
        codes.append("CANNOT EVALUATE" in stream.getvalue())
    check("exit/codes-0-1-2", codes == [0, 1, 2, True], codes)

    got = run_gate(ROOT)
    check("live/real-tree-passes", got == 0, got)

    _parity_self_test(list(EXECUTED))

    if FAILURES:
        print("SELF-TEST FAIL: check_baseline_pin {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_baseline_pin {} checks (the exact fixture passes; a differing file, a "
          "missing file, a stray file, a symbolic link at a file, a directory or the root, a FIFO and a "
          "missing root each fail; a missing, unreadable, non-UTF-8, non-TOML or misshapen pin and an "
          "unreadable copied file are cannot-evaluate; the pin size bound holds one byte under, at and "
          "over; 2 registered pairs pass together, a second copy's defect is reported against its own root "
          "and pin, and a pin naming another root or a registry repeating a pin or a root is "
          "cannot-evaluate; a pinned build tool passes in 1 or both pins and fails when differing, missing, "
          "a symbolic link or not a regular file, is cannot-evaluate when unreadable, and any other path "
          "outside a copy root, a tool-only pin or a misshapen tool digest is cannot-evaluate; a pin whose version differs from the attribution file's site template version, "
          "2 pins naming different versions, and an attribution file that is missing, unreadable, not a "
          "regular file, non-UTF-8, non-TOML, over its size bound, without the site template table or with "
          "a missing or misshapen version there are cannot-evaluate; a homepage ending in the recorded "
          "version's archive name passes, and one naming another version, another file name or a prefixed "
          "name, missing or not a string is cannot-evaluate; the exit codes are 0, 1 and 2; the "
          "live tree passes; each parity comparison names a divergence, and while the OPF copy exists it "
          "loads, gives this gate's exit code on every shared case and its pin equals the OPF pin with "
          "the opf/ prefix removed)".format(len(EXECUTED)))
    return 0


def main():
    argv = sys.argv[1:]
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


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
