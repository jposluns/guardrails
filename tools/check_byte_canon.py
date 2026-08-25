#!/usr/bin/env python3
"""Byte-canonicalization, checkout-stability, and platform-matrix gate (VER-CORE Section 3). Offline,
stdlib only, fail-closed.

Enacts 3.1 (canonical byte format over every released text file), 3.3 (the shipped .gitattributes
covers every pack-owned path), 3.4 (published vectors, run by --self-test), and 3.6 (the
supported-platform matrix keyed off the _containment capability probe). Scope is the concern-1
release-file inventory taken from gen_manifest's validated loader (the check_gensrc_failclose to
gen_gensrc house pattern: reuse the loader, never fork a second parser); the text/binary split is the
ownership map's [checkout].binary roster. Coverage is evaluated through git's own attribute engine
(`git check-attr`), not a hand parser: the shipped .gitattributes is generated and drift-gated by
gen_manifest, and the engine evaluation additionally catches a nested tracked .gitattributes
overriding the root one.

check_no_dashes.py stays separate (3.1). Hashing elsewhere stays byte-literal (3.2): this gate
canonicalizes at release; no verifier normalizes.

  check_byte_canon.py               run the gate over the release scope
  check_byte_canon.py --self-test   pure cases plus the published 3.4 vectors (alias: --selftest)

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (a non-canonical released text file, including a non-UTF-8 one, or a 3.3
     coverage gap: the file is exactly what this gate asserts against)
  2  malformed or unreadable required input (the ownership map, .gitattributes, the policy, the
     vectors, a git failure, a stale or unused allowance, or a capability probe contradicting a
     supported-platform claim), fail-closed
"""
import base64
import hashlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
import _containment  # noqa: E402
import gen_manifest  # noqa: E402  reuse the validated scope loader; never a second parser

POLICY_REL = ".aiqt/core/gates/byte-canon.toml"
VECTORS_REL = ".aiqt/core/gates/canon-vectors/vectors.toml"
SUPPORTED = ("linux", "macos")
ROADMAP = ("windows",)
# Every vector class the published set may carry (3.4); each fail class names the scan leg it exercises.
VECTOR_CLASSES = ("pass", "crlf", "bare-cr", "trailing-whitespace", "zero-width", "bidi", "bidi-mark",
                  "word-joiner", "bom", "no-final-newline", "double-final-newline", "invalid-utf8",
                  "empty")
# Classes that MUST appear in the published set (a missing one is fail-closed): one per released leg,
# including the newly-covered bidi marks and the word joiner so those controls cannot silently drop.
REQUIRED_VECTOR_CLASSES = ("pass", "crlf", "trailing-whitespace", "zero-width", "bidi", "bidi-mark",
                           "word-joiner", "invalid-utf8", "empty")
# Each non-"pass" class is bound to a substring of the SPECIFIC scan_bytes finding it must provoke, so a
# vector's class name proves its own scan leg fired, not merely that some finding did. The overlapping
# codepoint classes name their exact U+codepoint (the U+{:04X} the scan emits), so swapping one class's
# payload for another class's bytes no longer passes. Source of these substrings is scan_bytes' own text.
CLASS_FINDING = {
    "crlf": "carriage return present",
    "bare-cr": "carriage return present",
    "trailing-whitespace": "trailing whitespace",
    "zero-width": "U+200B",
    "word-joiner": "U+2060",
    "bidi": "U+202E",
    "bidi-mark": "U+200E",
    "bom": "leading UTF-8 BOM",
    "no-final-newline": "no trailing newline",
    "double-final-newline": "more than one trailing newline",
    "invalid-utf8": "not valid UTF-8",
    "empty": "empty file",
}

# The forbidden codepoints (3.1), built via chr() so this source file never carries one itself.
# ZERO_WIDTH: U+200B, U+200C, U+200D, U+2060 (WORD JOINER), U+FEFF. BIDI: the complete Unicode
# Bidi_Control set (U+061C, U+200E, U+200F, U+202A..U+202E, U+2066..U+2069).
ZERO_WIDTH = tuple(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
BIDI = tuple(chr(c) for c in (0x061C, 0x200E, 0x200F)
             + tuple(range(0x202A, 0x202F)) + tuple(range(0x2066, 0x206A)))
FORBIDDEN = {ch.encode("utf-8"): ch for ch in ZERO_WIDTH + BIDI}
FORBIDDEN_RE = re.compile(b"|".join(re.escape(b) for b in sorted(FORBIDDEN)))
BOM = b"\xef\xbb\xbf"


class GateError(Exception):
    """An input the gate cannot read, parse, or trust. Caught at run() and reported as exit 2."""


# --- pure legs (always exercised by --self-test) ----------------------------------------------------

def scan_bytes(data, allowances=()):
    """The 3.1 legs over one file's raw bytes. allowances is a tuple of (start, end, codepoint-set)
    byte-range rows already validated for this file. Returns a list of finding strings. A file that
    does not decode as UTF-8 yields a finding (it is the thing asserted against), never an error."""
    findings = []
    has_bom = data.startswith(BOM)
    if has_bom:
        findings.append("leading UTF-8 BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return findings + ["not valid UTF-8 ({})".format(exc)]
    if b"\r" in data:
        findings.append("carriage return present; line endings must be LF only")
    if not data:
        findings.append("empty file; a released text file ends with exactly one newline")
    elif not data.endswith(b"\n"):
        findings.append("no trailing newline; exactly one is required")
    elif data.endswith(b"\n\n"):
        findings.append("more than one trailing newline; exactly one is required")
    lines = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")
    for lineno, line in enumerate(lines, start=1):
        body = line[:-1] if line.endswith("\r") else line  # CR is reported once, above
        if body != body.rstrip():
            findings.append("line {}: trailing whitespace".format(lineno))
    for m in FORBIDDEN_RE.finditer(data):
        if has_bom and m.start() == 0:
            continue  # the BOM is already reported as a BOM, not double-reported as U+FEFF
        ch = FORBIDDEN[m.group(0)]
        if any(s <= m.start() < e and ch in cps for s, e, cps in allowances):
            continue
        kind = "zero-width" if ch in ZERO_WIDTH else "bidirectional control"
        findings.append("byte offset {}: {} character U+{:04X} outside any allowance".format(
            m.start(), kind, ord(ch)))
    return findings


def coverage_findings(scope_paths, binary_set, effective):
    """The 3.3 leg over the effective attribute map {path: {"text": v, "eol": v}}. A non-binary
    in-scope path must be text=set eol=lf; a [checkout].binary path must be text=unset (the binary
    macro). Anything else, including unspecified, is a finding: an uncovered pack-owned path is a
    FAIL (spec 3.3)."""
    findings = []
    for path in sorted(scope_paths):
        got = effective.get(path, {})
        text_v, eol_v = got.get("text"), got.get("eol")
        if path in binary_set:
            if text_v != "unset":
                findings.append("{}: declared [checkout].binary but effective text attribute is "
                                "{!r}; expected unset via the binary attribute".format(path, text_v))
        elif text_v != "set" or eol_v != "lf":
            findings.append("{}: not pinned text eol=lf (effective text={!r} eol={!r}); an "
                            "uncovered pack-owned path fails checkout stability".format(
                                path, text_v, eol_v))
    return findings


# --- inputs -----------------------------------------------------------------------------------------

def release_scope(root):
    """(in-scope path set, binary roster set) from the step-2 single source. classify() returns
    ({path: class}, excluded); the in-scope path set is the keys of the class map (RECONCILED against
    the merged step 2: gen_manifest.classify returns a two-tuple)."""
    try:
        exclusions, release, namespace, binary_set = gen_manifest.load_ownership(root)
        tracked = gen_manifest.git_tracked(root)
        classes, _excluded = gen_manifest.classify(tracked, exclusions, release, namespace, root)
    except gen_manifest.GateError as exc:
        raise GateError("scope enumeration failed: {}".format(exc))
    return set(classes), set(binary_set)


POLICY_TOP_KEYS = frozenset(("format-version", "platforms", "allowance"))
POLICY_PLATFORM_KEYS = frozenset(("supported", "roadmap"))
ALLOWANCE_KEYS = frozenset(("path", "sha256", "start", "end", "codepoints", "rationale"))


def load_policy(root):
    """Strict parse of the platform/allowance policy. format-version must be exactly 1, the platform
    sets must equal this gate's constants (single-source discipline: the shipped artifact and the
    enforcing gate cannot drift), and no unknown top-level or [platforms] key is tolerated. Any
    schema or type error is fail-closed (exit 2). Returns the raw allowance rows."""
    try:
        data = load_toml(root / POLICY_REL)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read {} ({})".format(POLICY_REL, exc))
    if type(data.get("format-version")) is not int or data.get("format-version") != 1:
        raise GateError("{}: format-version must be exactly 1".format(POLICY_REL))
    unknown = set(data) - POLICY_TOP_KEYS
    if unknown:
        raise GateError("{}: unknown top-level key(s) {}".format(POLICY_REL, sorted(unknown)))
    plats = data.get("platforms")
    if not isinstance(plats, dict):
        raise GateError("{}: no [platforms] table".format(POLICY_REL))
    unknown = set(plats) - POLICY_PLATFORM_KEYS
    if unknown:
        raise GateError("{}: unknown [platforms] key(s) {}".format(POLICY_REL, sorted(unknown)))
    if tuple(plats.get("supported", ())) != SUPPORTED or tuple(plats.get("roadmap", ())) != ROADMAP:
        raise GateError("{}: platform sets disagree with the gate constants (supported {} roadmap "
                        "{})".format(POLICY_REL, SUPPORTED, ROADMAP))
    rows = data.get("allowance", [])
    if not isinstance(rows, list):
        raise GateError("{}: [[allowance]] is not an array".format(POLICY_REL))
    return rows


def validate_allowances(root, rows, scope_paths):
    """Validate every allowance row against the live tree; return {path: ((start, end, cpset), ...)}.
    A malformed, stale (digest mismatch), out-of-range, overlapping, unused, or over-broad row is
    fail-closed: a mis-stated allowance must never silently widen the permitted set."""
    permitted_names = {"U+{:04X}".format(ord(c)) for c in ZERO_WIDTH + BIDI}
    out = {}
    for i, row in enumerate(rows, 1):
        where = "{} allowance #{}".format(POLICY_REL, i)
        if not isinstance(row, dict):
            raise GateError("{}: not a table".format(where))
        unknown = set(row) - ALLOWANCE_KEYS
        if unknown:
            raise GateError("{}: unknown key(s) {}".format(where, sorted(unknown)))
        path, digest = row.get("path"), row.get("sha256")
        start, end = row.get("start"), row.get("end")
        cps, rationale = row.get("codepoints"), row.get("rationale")
        # type() not isinstance() for the offsets: bool is a subclass of int, and a bool offset must
        # be rejected, not silently coerced (True == 1) into a byte position.
        if not (isinstance(path, str) and isinstance(digest, str)
                and type(start) is int and type(end) is int
                and isinstance(cps, list) and cps and all(isinstance(c, str) for c in cps)
                and isinstance(rationale, str) and rationale):
            raise GateError("{}: missing or malformed field".format(where))
        if path not in scope_paths:
            raise GateError("{}: path {!r} is not an in-scope release path".format(where, path))
        if not set(cps) <= permitted_names:
            raise GateError("{}: codepoints outside the forbidden sets".format(where))
        try:
            data = (root / path).read_bytes()
        except OSError as exc:
            raise GateError("{}: cannot read {} ({})".format(where, path, exc))
        if hashlib.sha256(data).hexdigest() != digest:
            raise GateError("{}: stale (sha256 mismatch against {})".format(where, path))
        if not (0 <= start < end <= len(data)):
            raise GateError("{}: byte range out of bounds".format(where))
        cpset = frozenset(chr(int(n[2:], 16)) for n in cps)
        span = data[start:end].decode("utf-8", errors="ignore")
        if not any(c in span for c in cpset):
            raise GateError("{}: unused (no named codepoint occurs in the span)".format(where))
        for s, e, _c in out.get(path, ()):
            if s < end and start < e:
                raise GateError("{}: overlaps another allowance for {}".format(where, path))
        out.setdefault(path, ())
        out[path] = out[path] + ((start, end, cpset),)
    return out


def effective_attributes(root, scope_paths):
    """{path: {"text": v, "eol": v}} via `git check-attr -z --stdin`, the engine evaluation. A
    process-launch failure (git absent) is fail-closed like the other git legs, and the NUL-delimited
    response must have exactly the shape requested (one record per requested path x attribute, plus
    the trailing empty field); a malformed response is fail-closed rather than a partial map."""
    if not (root / ".gitattributes").is_file():
        raise GateError(".gitattributes is missing; 3.3 coverage cannot be evaluated")
    attrs = ("text", "eol")
    ordered = sorted(scope_paths)
    payload = b"".join(p.encode("utf-8") + b"\0" for p in ordered)
    try:
        proc = subprocess.run(["git", "-C", str(root), "check-attr", "-z", "--stdin", *attrs],
                              input=payload, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("git check-attr could not be launched ({})".format(exc))
    if proc.returncode != 0:
        raise GateError("git check-attr failed: {}".format(
            proc.stderr.decode("utf-8", errors="replace").strip()))
    fields = proc.stdout.split(b"\0")
    # -z output is <path>\0<attr>\0<value>\0 per record; the trailing \0 makes a final empty field.
    expected = 3 * len(ordered) * len(attrs) + 1
    if len(fields) != expected or fields[-1] != b"":
        raise GateError("git check-attr returned a malformed response "
                        "({} fields, expected {})".format(len(fields), expected))
    out = {}
    seen = set()
    try:
        for j in range(0, len(fields) - 1, 3):
            path, attr, value = fields[j].decode("utf-8"), fields[j + 1].decode(), fields[j + 2].decode()
            if path not in scope_paths or attr not in attrs or (path, attr) in seen:
                raise GateError("git check-attr returned an unexpected record ({!r}, {!r})".format(
                    path, attr))
            seen.add((path, attr))
            out.setdefault(path, {})[attr] = value
    except UnicodeDecodeError as exc:
        raise GateError("git check-attr returned undecodable output ({})".format(exc))
    if seen != {(p, a) for p in scope_paths for a in attrs}:
        raise GateError("git check-attr response did not cover exactly the requested path/attribute set")
    return out


def check_matrix(root):
    """The 3.6 leg: policy consistency plus the capability probe. On a supported platform a failed
    probe falsifies the supported-platform claim (exit 2). On any other platform this read-only gate
    runs in the labelled degraded mode (3.6a, forward-compatibility; no supported platform triggers
    it) and the state-changing operation classes refuse."""
    plat = {"linux": "linux", "darwin": "macos"}.get(sys.platform)
    contained = _containment.probe()
    if plat in SUPPORTED:
        if not contained:
            raise GateError("platform {!r} is declared supported but the containment probe failed; "
                            "the supported-platform claim is falsified (3.6)".format(plat))
        print("platform-matrix: {} supported; containment primitive present".format(plat))
    else:
        print("platform-matrix: platform {!r} is not supported (roadmap: {}); read-only "
              "verification runs {}; state-changing operations refuse here".format(
                  sys.platform, ", ".join(ROADMAP),
                  _containment.mode_for(_containment.READ_ONLY, contained)))


# --- the gate ---------------------------------------------------------------------------------------

def run(root):
    try:
        scope, binary = release_scope(root)
        allow_rows = load_policy(root)
        allowances = validate_allowances(root, allow_rows, scope)
        check_matrix(root)
        findings = coverage_findings(scope, binary, effective_attributes(root, scope))
        for path in sorted(scope - binary):
            try:
                data = (root / path).read_bytes()
            except OSError as exc:
                raise GateError("cannot read in-scope file {} ({})".format(path, exc))
            findings += ["{}: {}".format(path, f) for f in scan_bytes(data, allowances.get(path, ()))]
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} byte-canon finding(s)".format(len(findings)))
        for f in findings:
            print("  " + f)
        return 1
    print("PASS: every released text file is byte-canonical; .gitattributes covers the release "
          "scope; the platform matrix holds")
    return 0


# --- self-test --------------------------------------------------------------------------------------

VECTOR_ROW_KEYS = frozenset(("name", "class", "payload-base64", "sha256"))
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _load_vectors(root):
    try:
        data = load_toml(root / VECTORS_REL)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read {} ({})".format(VECTORS_REL, exc))
    rows = data.get("vector")
    if not isinstance(rows, list) or not rows:
        raise GateError("{}: no [[vector]] array".format(VECTORS_REL))
    for k, row in enumerate(rows, 1):
        where = "{} vector #{}".format(VECTORS_REL, k)
        if not isinstance(row, dict):
            raise GateError("{}: not a table".format(where))
        unknown = set(row) - VECTOR_ROW_KEYS
        if unknown:
            raise GateError("{}: unknown key(s) {}".format(where, sorted(unknown)))
        name, cls, payload = row.get("name"), row.get("class"), row.get("payload-base64")
        if not (isinstance(name, str) and name and isinstance(payload, str)):
            raise GateError("{}: missing or malformed name/payload".format(where))
        if cls not in VECTOR_CLASSES:
            raise GateError("{}: unknown vector class {!r}".format(where, cls))
        try:
            base64.b64decode(payload, validate=True)
        except ValueError as exc:  # binascii.Error (bad base64) subclasses ValueError
            raise GateError("{}: payload is not valid base64 ({})".format(where, exc))
        sha = row.get("sha256")
        if cls == "pass" and not isinstance(sha, str):
            raise GateError("{}: a pass vector must carry its expected sha256".format(where))
        # A present sha256 (always so for "pass") must be exactly 64 lowercase hex; a malformed digest
        # is a schema error (exit 2), not a downstream exit-1 digest mismatch.
        if sha is not None and (not isinstance(sha, str) or SHA256_RE.match(sha) is None):
            raise GateError("{}: sha256 must be exactly 64 lowercase hex characters".format(where))
    classes = {r["class"] for r in rows}
    missing = [c for c in REQUIRED_VECTOR_CLASSES if c not in classes]
    if missing:
        raise GateError("{}: required vector class(es) missing: {}".format(
            VECTORS_REL, ", ".join(missing)))
    return rows


def self_test_main():
    failures = []
    # Pure 3.1 cases: (name, bytes, expect_finding).
    cases = [
        ("clean", b"a\n", False),
        ("crlf", b"a\r\nb\n", True),
        ("bare-cr", b"a\rb\n", True),
        ("bom", BOM + b"a\n", True),
        ("trailing-space", b"a \n", True),
        ("trailing-tab", b"a\t\n", True),
        ("trailing-nbsp", "a\u00a0\n".encode("utf-8"), True),
        ("no-final-newline", b"a", True),
        ("double-final-newline", b"a\n\n", True),
        ("empty", b"", True),
        ("zero-width", "a{}b\n".format(chr(0x200B)).encode("utf-8"), True),
        ("word-joiner", "a{}b\n".format(chr(0x2060)).encode("utf-8"), True),
        ("bidi", "a{}b\n".format(chr(0x202E)).encode("utf-8"), True),
        ("bidi-mark-lrm", "a{}b\n".format(chr(0x200E)).encode("utf-8"), True),
        ("bidi-mark-rlm", "a{}b\n".format(chr(0x200F)).encode("utf-8"), True),
        ("bidi-mark-alm", "a{}b\n".format(chr(0x061C)).encode("utf-8"), True),
        ("invalid-utf8", b"\xff\xfe\n", True),
    ]
    for name, data, expect in cases:
        got = bool(scan_bytes(data))
        if got != expect:
            failures.append("scan_bytes[{}] finding={}; expected {}".format(name, got, expect))
    # An allowance clears exactly its span and codepoint, nothing else.
    zw = "a{}b\n".format(chr(0x200B)).encode("utf-8")
    off = zw.find(chr(0x200B).encode("utf-8"))
    if scan_bytes(zw, ((off, off + 3, frozenset(chr(0x200B))),)):
        failures.append("an in-span allowance failed to clear its codepoint")
    if not scan_bytes(zw, ((0, 1, frozenset(chr(0x200B))),)):
        failures.append("an out-of-span allowance wrongly cleared a codepoint")
    # Coverage logic, synthetic effective maps.
    eff = {"a.md": {"text": "set", "eol": "lf"}, "b.zip": {"text": "unset", "eol": "unspecified"},
           "c.md": {"text": "unspecified", "eol": "unspecified"}}
    if coverage_findings({"a.md"}, set(), eff):
        failures.append("coverage flagged a pinned text path")
    if coverage_findings({"b.zip"}, {"b.zip"}, eff):
        failures.append("coverage flagged a declared binary path")
    if not coverage_findings({"c.md"}, set(), eff):
        failures.append("coverage passed an uncovered path")
    if not coverage_findings({"b.zip"}, set(), eff):
        failures.append("coverage passed a binary-attributed path missing from the roster")
    # Mode table: every class x both containment states, unknown fails closed in BOTH states.
    for cls in (_containment.READ_ONLY,) + _containment.MUTATING_CLASSES:
        for cont, expect in ((True, "contained"),
                             (False, _containment.DEGRADED if cls == _containment.READ_ONLY
                              else "fail-closed")):
            got = _containment.mode_for(cls, cont)
            if got != expect:
                failures.append("mode_for({!r}, {}) = {!r}; expected {!r}".format(
                    cls, cont, got, expect))
    for cont in (True, False):
        if _containment.mode_for("unknown-class", cont) != "fail-closed":
            failures.append("an unknown operation class did not fail closed (contained={})".format(cont))
    # probe() never raises: an injected capability-lookup error fails safe to False, not an exception.
    class _RaisingMembership:
        def __contains__(self, item):
            raise RuntimeError("injected capability-lookup failure")
    saved_supports = _containment.os.supports_dir_fd
    _containment.os.supports_dir_fd = _RaisingMembership()
    try:
        if _containment.probe() is not False:
            failures.append("probe did not fail safe to False on an internal capability error")
    except Exception as exc:
        failures.append("probe raised instead of failing safe: {!r}".format(exc))
    finally:
        _containment.os.supports_dir_fd = saved_supports
    # load_policy strict schema (FIX 2): a valid policy passes; each malformation fails closed.
    def _policy_rejects(fake_data):
        g = globals()
        saved = g["load_toml"]
        g["load_toml"] = lambda _path: fake_data
        try:
            load_policy(repo_root())
            return False
        except GateError:
            return True
        except Exception as exc:  # any non-GateError schema error is a fail-closed miss
            failures.append("load_policy raised {!r} instead of GateError".format(exc))
            return True
        finally:
            g["load_toml"] = saved
    base_policy = {"format-version": 1,
                   "platforms": {"supported": list(SUPPORTED), "roadmap": list(ROADMAP)},
                   "allowance": []}
    if _policy_rejects(base_policy):
        failures.append("a valid policy was wrongly rejected")
    if not _policy_rejects(dict(base_policy, **{"format-version": 999})):
        failures.append("format-version 999 was not rejected")
    if not _policy_rejects(dict(base_policy, surprise=1)):
        failures.append("an unknown top-level policy key was not rejected")
    if not _policy_rejects(dict(base_policy,
                                platforms={"supported": list(SUPPORTED), "roadmap": list(ROADMAP),
                                           "surprise": 1})):
        failures.append("an unknown [platforms] key was not rejected")
    # validate_allowances row schema (FIX 2): an unknown key and a bool offset each fail closed.
    # The path does not exist, so a downstream read would also raise GateError; assert the SPECIFIC
    # schema rejection reason so the test isolates the key/type check, not the file-read failure.
    good_row = {"path": "x", "sha256": "0" * 64, "start": 0, "end": 1,
                "codepoints": ["U+200B"], "rationale": "r"}
    try:
        validate_allowances(repo_root(), [dict(good_row, extra=1)], {"x"})
        failures.append("an unknown allowance-row key was not rejected")
    except GateError as exc:
        if "unknown key" not in str(exc):
            failures.append("an unknown allowance-row key was not rejected on its own terms: {}".format(exc))
    try:
        validate_allowances(repo_root(), [dict(good_row, start=True)], {"x"})
        failures.append("a boolean allowance offset was not rejected")
    except GateError as exc:
        if "malformed" not in str(exc):
            failures.append("a boolean allowance offset was not rejected on its own terms: {}".format(exc))
    # FIX A: a nested / non-string codepoints element must fail closed (GateError -> exit 2), never
    # reach set(cps) as an unhashable list and raise an uncaught TypeError -> traceback -> exit 1.
    try:
        validate_allowances(repo_root(), [dict(good_row, codepoints=[["U+200B"]])], {"x"})
        failures.append("a nested allowance codepoint element was not rejected")
    except GateError as exc:
        if "malformed" not in str(exc):
            failures.append("a nested allowance codepoint element was not rejected on its own terms: "
                            "{}".format(exc))
    except Exception as exc:  # a non-GateError (e.g. TypeError) is the exact pre-fix escape
        failures.append("a nested allowance codepoint raised {!r} instead of GateError".format(exc))
    # effective_attributes fail-closed (FIX 4): launch failure and malformed response each -> GateError.
    def _boom(*_a, **_k):
        raise FileNotFoundError("injected git launch failure")

    class _Proc:
        def __init__(self, stdout):
            self.returncode, self.stdout, self.stderr = 0, stdout, b""
    saved_run = subprocess.run
    try:
        subprocess.run = _boom
        try:
            effective_attributes(repo_root(), {"README.md"})
            failures.append("a check-attr launch failure did not fail closed")
        except GateError:
            pass
        except Exception as exc:
            failures.append("check-attr launch failure raised {!r} not GateError".format(exc))
        subprocess.run = lambda *a, **k: _Proc(b"README.md\x00text\x00set\x00")  # too few fields
        try:
            effective_attributes(repo_root(), {"README.md"})
            failures.append("a malformed check-attr response did not fail closed")
        except GateError:
            pass
        except Exception as exc:
            failures.append("malformed check-attr response raised {!r} not GateError".format(exc))
    finally:
        subprocess.run = saved_run
    # _load_vectors strict rows (FIX 6): a malformed row and a missing required class each fail closed.
    def _vectors_reject(fake_rows):
        g = globals()
        saved = g["load_toml"]
        g["load_toml"] = lambda _path: {"vector": fake_rows}
        try:
            _load_vectors(repo_root())
            return False
        except GateError:
            return True
        except Exception as exc:
            failures.append("_load_vectors raised {!r} instead of GateError".format(exc))
            return True
        finally:
            g["load_toml"] = saved
    # A baseline set that already carries every required class, so a malformed-row test isolates the
    # row schema check rather than tripping the separate missing-required-class check.
    valid_vectors = [{"name": c, "class": c, "payload-base64": "YQo="} for c in REQUIRED_VECTOR_CLASSES]
    valid_vectors[0]["sha256"] = "0" * 64  # the "pass" class row must carry an expected digest
    if _vectors_reject([dict(r) for r in valid_vectors]):
        failures.append("a fully-valid vector set was wrongly rejected")
    if not _vectors_reject([dict(r) for r in valid_vectors]
                           + [{"name": "n", "class": "nonsense-class", "payload-base64": "YQo="}]):
        failures.append("a vector with an unknown class was not rejected")
    no_payload = [dict(r) for r in valid_vectors]
    no_payload[1].pop("payload-base64")
    if not _vectors_reject(no_payload):
        failures.append("a vector missing its payload was not rejected")
    no_sha = [dict(r) for r in valid_vectors]
    no_sha[0].pop("sha256")
    if not _vectors_reject(no_sha):
        failures.append("a pass vector missing its expected sha256 was not rejected")
    if not _vectors_reject([dict(r) for r in valid_vectors[1:]]):  # drops the required "pass" class
        failures.append("a vector set missing a required class was not rejected")
    # FIX C: an unknown vector-row key fails closed (exit 2), not a silent exit-0 accept.
    unknown_key = [dict(r) for r in valid_vectors]
    unknown_key[1]["surprise"] = 1
    if not _vectors_reject(unknown_key):
        failures.append("an unknown vector-row key was not rejected")
    # FIX C: a malformed sha256 (wrong length, non-hex, or uppercase) on a pass vector is a schema
    # error (exit 2) at load, not a downstream exit-1 digest mismatch.
    for bad in ("0" * 63, "0" * 65, "g" * 64, "A" * 64):
        bad_sha = [dict(r) for r in valid_vectors]
        bad_sha[0]["sha256"] = bad
        if not _vectors_reject(bad_sha):
            failures.append("a malformed pass-vector sha256 {!r} was not rejected".format(bad))
    # FIX B completeness guard (fail-closed, exit 2): every non-pass class MUST carry a CLASS_FINDING
    # binding, so a class added to VECTOR_CLASSES cannot silently ship unbound. It runs before any
    # CLASS_FINDING lookup below, so a gap fails closed here rather than raising a bare KeyError later.
    unbound = [c for c in VECTOR_CLASSES if c != "pass" and c not in CLASS_FINDING]
    if unbound:
        print("error: vector class(es) {} have no CLASS_FINDING binding; fail-closed".format(
            ", ".join(unbound)), file=sys.stderr)
        return 2
    # FIX B: the class->finding binding is exercised, not bool(findings). A cross-class payload swap
    # (codex round-2 replaced every failure payload with identical CRLF bytes) must now be flagged:
    # a non-CR class bound against a CRLF-only finding set fails its binding, while the CR classes and
    # each class against its own scan leg pass. This is the durable in-code mutation guard; the class
    # lists derive from CLASS_FINDING itself, so a removed binding is caught by the guard above.
    crlf_found = scan_bytes(b"bad\r\n")
    for victim in [c for c in CLASS_FINDING if c not in ("crlf", "bare-cr")]:
        if any(CLASS_FINDING[victim] in f for f in crlf_found):
            failures.append("class {!r} wrongly bound to a CRLF-only finding set".format(victim))
    own_bytes = {"crlf": b"bad\r\n", "bare-cr": b"bad\rx\n", "trailing-whitespace": b"bad \n",
                 "zero-width": "a{}b\n".format(chr(0x200B)).encode("utf-8"),
                 "word-joiner": "a{}b\n".format(chr(0x2060)).encode("utf-8"),
                 "bidi": "a{}b\n".format(chr(0x202E)).encode("utf-8"),
                 "bidi-mark": "a{}b\n".format(chr(0x200E)).encode("utf-8"),
                 "bom": BOM + b"a\n", "no-final-newline": b"abc", "double-final-newline": b"abc\n\n",
                 "invalid-utf8": b"\xff\xfe\n", "empty": b""}
    for cls, sub in CLASS_FINDING.items():
        if not any(sub in f for f in scan_bytes(own_bytes[cls])):
            failures.append("class {!r} failed to bind to its own scan leg".format(cls))
    # The published vectors (3.4), run through the same scan. Each non-pass class is bound to the
    # SPECIFIC finding its scan leg emits (CLASS_FINDING), so a class name proves its own leg fired: a
    # cross-class payload swap (codex round-2 replaced every failure payload with identical CRLF bytes)
    # no longer passes.
    try:
        for row in _load_vectors(repo_root()):
            data = base64.b64decode(row["payload-base64"], validate=True)
            found = scan_bytes(data)
            if row["class"] == "pass":
                if found:
                    failures.append("vector {}: expected clean, got {}".format(row["name"], found))
                elif hashlib.sha256(data).hexdigest() != row.get("sha256"):
                    failures.append("vector {}: digest mismatch".format(row["name"]))
            elif not any(CLASS_FINDING[row["class"]] in f for f in found):
                failures.append("vector {}: class {!r} did not provoke its scan leg (findings: {})".format(
                    row["name"], row["class"], found))
    except (GateError, KeyError, ValueError) as exc:
        print("error: vectors unusable ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: byte-format legs, allowance scoping, coverage logic, the containment "
          "mode table and probe fail-safe, the policy and vector schema fail-closed legs, and the "
          "published vectors all hold")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:  # --selftest: the spec spelling (3.4)
        return self_test_main()
    if args:
        print("usage: check_byte_canon.py [--self-test]", file=sys.stderr)
        return 2
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
