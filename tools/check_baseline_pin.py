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
symbolic link or other non-regular entry anywhere in a copy root, including the root itself (exit 1). A
pin that is missing, unreadable, over MAX_PIN_BYTES, not UTF-8, not TOML or not of the pinned shape, and
a copied file that cannot be read, are cannot-evaluate (exit 2), never a pass.

Every registered pin must name the same version, and that version must equal the site template version
recorded in .aiqt/attribution.toml, table [vendored."site-template"], key version: the single in-tree
source tools/gen_notice.py renders the root NOTICE from (the NOTICE is generated output, so it is not
read). The file is read under the scanned root. An attribution file that is missing, unreadable, not a
regular file, over MAX_ATTRIBUTION_BYTES, not UTF-8 or not TOML, a missing [vendored."site-template"]
table, a version there that is missing or not an X.Y.Z string, a pin whose version differs from it, and
2 pins that name different versions are cannot-evaluate (exit 2). The gate reads only the repository; it
never touches the network.
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
import io
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Every pin and the 1 copy root it is bound to, in check order; both columns are unique.
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
    """The pin as (version, [(root, {path: sha256})]); CannotEvaluate on any malformed input."""
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
    root, pinned = None, {}
    for path, digest in files.items():
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
    return version, [(root, pinned)]


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
        version, roots = parse_pin(data, pin_rel)
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
          "or linked entry, and every pin names the site template version recorded in {}".format(
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


# The fixture's template version, named by every fixture pin and by the fixture attribution file; it
# differs from the live one, so a pass shows the version is read under the scanned root.
FIXTURE_VERSION = "1.2.1"


def _pin_text(files, root=FIXTURE_ROOT, version=FIXTURE_VERSION, extra=""):
    lines = ['version = "{}"'.format(version), extra, "", "[files]"]
    for rel in sorted(files):
        lines.append('"{}/{}" = "{}"'.format(root, rel, hashlib.sha256(files[rel]).hexdigest()))
    return "\n".join(lines) + "\n"


def _attribution_text(version=FIXTURE_VERSION, block=ATTRIBUTION_BLOCK):
    """A fixture attribution file: another vendored block first, so the version is taken from the named one."""
    return ('[vendored."marko"]\nname = "Marko"\nversion = "9.9.9"\n\n'
            '[vendored."{}"]\nname = "Shared site template (baseline package)"\nversion = "{}"\n'
            'verified = true\n').format(block, version)


def _fixture(tmp, files=None, pin=None, root=FIXTURE_ROOT, pin_rel=PIN_REL, attribution=None):
    """A fixture tree under tmp: the copied files under root, the pin at pin_rel (text, bytes, None for
    the exact pin of FIXTURE_FILES under root, or False for absent) and the attribution file (text, bytes,
    None for one recording FIXTURE_VERSION, or False to write none)."""
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


def _scan_case(files=None, pin=None, mutate=None, pins=FIXTURE_PINS, second=None, attribution=None):
    """scan() over a fixture; second, when given, is (files, pin) for the second registered pair."""
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp, files, pin, attribution=attribution)
        if second is not None:
            _fixture(tmp, second[0], second[1], root=SECOND_ROOT, pin_rel=SECOND_PIN, attribution=False)
        if mutate is not None:
            mutate(Path(tmp))
        return scan(tmp, pins)


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
          "cannot-evaluate; a pin whose version differs from the attribution file's site template version, "
          "2 pins naming different versions, and an attribution file that is missing, unreadable, not a "
          "regular file, non-UTF-8, non-TOML, over its size bound, without the site template table or with "
          "a missing or misshapen version there are cannot-evaluate; the exit codes are 0, 1 and 2; the "
          "live tree passes)".format(len(EXECUTED)))
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
