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

The 3.1 scan forbids the zero-width and bidirectional control codepoints held in _byte_canon.FORBIDDEN
and, in this gate only, a literal U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR (str.splitlines()
splits on both), held in _byte_canon.LINE_SEPARATORS. The separators stay out of FORBIDDEN because opf's
view generator reads that set (opf's record emitter escapes both sets); no allowance clears them. Like the
rest of the 3.1 scan, the separator scan reads every in-scope path except the [checkout].binary roster, and
it matches only the UTF-8 encoding of each separator: a file in another encoding (UTF-16, say) that holds
one is not reported.

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
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_byte_canon.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import base64
import hashlib
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# tools/_pack_byte_canon.py and tools/_pack_containment.py are the pack's own copies of the shared pure
# byte-canon scanner and the containment probe (origin and SHA-256 pinned in tools/pack-copies-pin.toml),
# so this gate runs without opf/ present.
from _gen_common import repo_root, load_toml  # noqa: E402
import _pack_containment as _containment  # noqa: E402
import gen_manifest  # noqa: E402  reuse the validated scope loader; never a second parser
# The pure byte legs and forbidden-codepoint sets come from the pack's pinned copy in _pack_byte_canon.
# While opf/ is in this repository, pack_copy_findings below compares that copy with its origin
# opf/tools/_byte_canon.py on every run, so this gate and the OPF tooling scan identical bytes and a fork
# of either side is caught; once opf/ leaves, the copy stands alone against its pin row.
from _pack_byte_canon import (  # noqa: E402
    ZERO_WIDTH, BIDI, FORBIDDEN, FORBIDDEN_RE, BOM, LINE_SEPARATORS, scan_bytes, _is_two_space_break,
)

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

# U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR (_byte_canon.LINE_SEPARATORS): str.splitlines()
# splits on both, so a literal one inside a string or comment changes how a tool that splits lines with
# str.splitlines() reads the file (a reader that splits only on LF, such as _has_two_space_break below, is
# unaffected; an editor can decode the escape text into the character itself). Of the release-scope gates,
# only THIS one refuses them; they are never added to _byte_canon.FORBIDDEN, which also drives opf's view
# generator (it refuses a render carrying any member), so widening it would make opf refuse to render or
# record in an adopter's store that holds one. No allowance can clear them; write the escape text instead.
LINE_SEPARATORS_RE = re.compile(b"|".join(re.escape(b) for b in sorted(LINE_SEPARATORS)))


class GateError(Exception):
    """An input the gate cannot read, parse, or trust. Caught at run() and reported as exit 2."""


# --- pure legs (scan_bytes / _is_two_space_break and the forbidden sets are imported from _byte_canon) --

def scan_line_separators(data):
    """The repository-only leg over one file's raw bytes: one finding per literal U+2028 or U+2029. It
    runs on the raw bytes, so it reports them in a file that is not valid UTF-8 too; scan_bytes reports
    the decode failure itself. Returns a list of finding strings."""
    return ["byte offset {}: line or paragraph separator U+{:04X} (str.splitlines() splits on it); "
            "write the escape text".format(m.start(), ord(LINE_SEPARATORS[m.group(0)]))
            for m in LINE_SEPARATORS_RE.finditer(data)]



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


POLICY_TOP_KEYS = frozenset(("format-version", "platforms", "allowance", "hardbreak"))
HARDBREAK_KEYS = frozenset(("path", "rationale"))
POLICY_PLATFORM_KEYS = frozenset(("supported", "roadmap"))
ALLOWANCE_KEYS = frozenset(("path", "sha256", "start", "end", "codepoints", "rationale"))


def load_policy(root):
    """Strict parse of the platform/allowance policy. format-version must be exactly 1, the platform
    sets must equal this gate's constants (single-source discipline: the shipped artifact and the
    enforcing gate cannot drift), and no unknown top-level or [platforms] key is tolerated. Any
    schema or type error is fail-closed (exit 2). Returns (raw allowance rows, raw hardbreak rows)."""
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
    hb_rows = data.get("hardbreak", [])
    if not isinstance(hb_rows, list):
        raise GateError("{}: [[hardbreak]] is not an array".format(POLICY_REL))
    return rows, hb_rows


PACK_COPIES_REL = "tools/pack-copies-pin.toml"
PACK_COPY_KEYS = frozenset(("origin", "sha256"))
PACK_EXCLUDE_KEYS = frozenset(("path", "reason"))
PACK_COPY_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


def _origin_ok(origin):
    """A pin row's origin must be a plain relative path inside opf/: nonempty, not absolute, no empty,
    '.' or '..' component, and at least one component below opf/."""
    if type(origin) is not str or not origin or origin.startswith("/"):
        return False
    parts = origin.split("/")
    if "" in parts or "." in parts or ".." in parts:
        return False
    return parts[0] == "opf" and len(parts) >= 2


def _swept_copy_files(root):
    """Every file under tools/_pack_* or tools/_vendor/ (the trees the pin file governs), as sorted
    root-relative paths. __pycache__ byte-code directories are not part of the pinned tree and are
    skipped. A symlink anywhere in that scope is GateError: the pin holds bytes, and a link aliases
    them."""
    tools_dir = root / "tools"
    try:
        tops = sorted(tools_dir.iterdir())
    except OSError as exc:
        raise GateError("cannot list tools/ ({})".format(exc))
    out = []
    stack = [p for p in tops if p.name.startswith("_pack_") or p.name == "_vendor"]
    while stack:
        p = stack.pop()
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            raise GateError("{}: a symlink inside the pinned-copy scope tools/_pack_*, tools/_vendor/"
                            .format(rel))
        if p.is_dir():
            if p.name == "__pycache__":
                continue
            try:
                stack.extend(sorted(p.iterdir()))
            except OSError as exc:
                raise GateError("cannot list {} ({})".format(rel, exc))
        else:
            out.append(rel)
    return sorted(out)


def pack_copy_digest(rel, origin, data):
    """The SHA-256 a pack copy's bytes are held to against its row. tools/check_python_floor.py requires every
    guarded surface to open with the floor guard naming its own file, so a copy whose file name differs from its
    origin's refuses under its own name: that one refusal name, found exactly once as the guard writes it, is put
    back to the origin's file name before hashing, and every other byte must still equal the origin's. A copy
    without that refusal, or with it more than once, is hashed as it stands."""
    own = rel.rsplit("/", 1)[-1].encode("utf-8")
    original = origin.rsplit("/", 1)[-1].encode("utf-8")
    refusal = b'"error: ' + own + b' requires Python '
    if own != original and data.count(refusal) == 1:
        data = data.replace(refusal, b'"error: ' + original + b' requires Python ', 1)
    return hashlib.sha256(data).hexdigest()


def pack_copy_findings(root):
    """The pinned pack copies under tools/ (tools/pack-copies-pin.toml). Each pinned copy must still hash to
    the SHA-256 its row records (the origin's digest at copy time, taken as pack_copy_digest describes), so a
    copy edited or replaced apart from its row is a finding. While the origin is present in this repository
    (opf/ has not yet moved to its own repository), the copy is ALSO compared with the origin's live bytes,
    under the same own-name refusal normalization, so editing an origin without re-copying is a finding too;
    once opf/ is absent the copies stand alone against their rows. Fail-closed (GateError, exit 2): a
    malformed pin file, a malformed origin path, a pinned copy or origin that is a symlink, a file under
    tools/_pack_* or tools/_vendor/ that no [files] row names (an [[exclude]] row with a path and a reason
    may deliberately leave such a file unpinned), and an [[exclude]] row naming no file in that scope."""
    try:
        data = load_toml(root / PACK_COPIES_REL)
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("cannot read {} ({})".format(PACK_COPIES_REL, exc))
    rows = data.get("files")
    if not set(data) <= {"files", "exclude"} or not isinstance(rows, dict) or not rows:
        raise GateError("{}: exactly one non-empty [files] table is required (plus an optional "
                        "[[exclude]] array)".format(PACK_COPIES_REL))
    ex_rows = data.get("exclude", [])
    if not isinstance(ex_rows, list):
        raise GateError("{}: [[exclude]] is not an array".format(PACK_COPIES_REL))
    excluded = set()
    for i, ex in enumerate(ex_rows, 1):
        if (not isinstance(ex, dict) or set(ex) != PACK_EXCLUDE_KEYS
                or type(ex["path"]) is not str or type(ex["reason"]) is not str
                or not ex["reason"] or ex["path"] in rows or ex["path"] in excluded):
            raise GateError("{}: malformed, duplicate, or pinned-path exclude row #{}".format(
                PACK_COPIES_REL, i))
        excluded.add(ex["path"])
    findings = []
    for rel in sorted(rows):
        row = rows[rel]
        if (not rel.startswith("tools/") or ".." in rel.split("/") or not isinstance(row, dict)
                or set(row) != PACK_COPY_KEYS or not isinstance(row["origin"], str)
                or not isinstance(row["sha256"], str) or PACK_COPY_SHA_RE.match(row["sha256"]) is None):
            raise GateError("{}: malformed row {!r}".format(PACK_COPIES_REL, rel))
        if not _origin_ok(row["origin"]):
            raise GateError("{}: row {!r} has a malformed origin path {!r} (must be a plain relative "
                            "path under opf/)".format(PACK_COPIES_REL, rel, row["origin"]))
        copy_path = root / rel
        if copy_path.is_symlink():
            raise GateError("{}: pinned pack copy is a symlink".format(rel))
        try:
            digest = pack_copy_digest(rel, row["origin"], copy_path.read_bytes())
        except OSError as exc:
            findings.append("{}: pinned pack copy cannot be read ({})".format(rel, exc))
            continue
        if digest != row["sha256"]:
            findings.append("{}: pack copy drifted from its pinned origin digest ({} pins {} for {})".format(
                rel, PACK_COPIES_REL, row["sha256"], row["origin"]))
        origin_path = root / row["origin"]
        if origin_path.is_symlink():
            raise GateError("{}: origin {} is a symlink".format(rel, row["origin"]))
        if origin_path.is_file():
            try:
                origin_digest = hashlib.sha256(origin_path.read_bytes()).hexdigest()
            except OSError as exc:
                raise GateError("{}: origin {} is present but cannot be read ({})".format(
                    rel, row["origin"], exc))
            if digest != origin_digest:
                findings.append("{}: pack copy and its present origin {} disagree (a forked copy); "
                                "update the copy and its pin row together".format(rel, row["origin"]))
    swept = _swept_copy_files(root)
    for path in swept:
        if path not in rows and path not in excluded:
            raise GateError("{}: {} is in the pinned-copy scope but no [files] row or [[exclude]] row "
                            "names it".format(PACK_COPIES_REL, path))
    stale = excluded - set(swept)
    if stale:
        raise GateError("{}: exclude row(s) name no file in the pinned-copy scope: {}".format(
            PACK_COPIES_REL, ", ".join(sorted(stale))))
    return findings


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


def _has_two_space_break(data):
    """Whether `data` carries at least one two-space hard-break line (the used-once check for an
    allowance). Undecodable bytes carry none: scan_bytes reports the decode failure separately."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = text.split("\n")[:-1] if text.endswith("\n") else text.split("\n")
    return any(_is_two_space_break(l[:-1] if l.endswith("\r") else l) for l in lines)


def validate_hardbreak(root, rows, scope_paths):
    """Validate the two-space hard-break allowances; return the set of in-scope paths on which the
    trailing-whitespace leg permits an EXACTLY-two-space CommonMark hard break (and nothing else). Each
    row binds an in-scope release path and a rationale; a duplicate, out-of-scope, malformed, or unused
    row (the file carries no such break, so the allowance is stale) is fail-closed. This is the sole,
    disclosed residual of the trailing-whitespace leg: every other trailing-whitespace vector (one
    space, three or more, a tab, a blank line) still fails everywhere, including on a listed path."""
    out = set()
    for i, row in enumerate(rows, 1):
        where = "{} hardbreak #{}".format(POLICY_REL, i)
        if not isinstance(row, dict):
            raise GateError("{}: not a table".format(where))
        unknown = set(row) - HARDBREAK_KEYS
        if unknown:
            raise GateError("{}: unknown key(s) {}".format(where, sorted(unknown)))
        path, rationale = row.get("path"), row.get("rationale")
        if not (isinstance(path, str) and isinstance(rationale, str) and rationale):
            raise GateError("{}: missing or malformed field".format(where))
        if path not in scope_paths:
            raise GateError("{}: path {!r} is not an in-scope release path".format(where, path))
        if path in out:
            raise GateError("{}: duplicate hardbreak allowance for {}".format(where, path))
        try:
            data = (root / path).read_bytes()
        except OSError as exc:
            raise GateError("{}: cannot read {} ({})".format(where, path, exc))
        if not _has_two_space_break(data):
            raise GateError("{}: unused (no two-space hard break occurs in {})".format(where, path))
        out.add(path)
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
        allow_rows, hb_rows = load_policy(root)
        allowances = validate_allowances(root, allow_rows, scope)
        hardbreak_paths = validate_hardbreak(root, hb_rows, scope)
        check_matrix(root)
        findings = coverage_findings(scope, binary, effective_attributes(root, scope))
        findings += pack_copy_findings(root)
        for path in sorted(scope - binary):
            try:
                data = (root / path).read_bytes()
            except OSError as exc:
                raise GateError("cannot read in-scope file {} ({})".format(path, exc))
            findings += ["{}: {}".format(path, f) for f in
                         scan_bytes(data, allowances.get(path, ()), path in hardbreak_paths)
                         + scan_line_separators(data)]
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} byte-canon finding(s)".format(len(findings)))
        for f in findings:
            print("  " + f)
        return 1
    print("PASS: every released text file is byte-canonical; .gitattributes covers the release "
          "scope; the platform matrix holds; every pinned pack copy matches its pin row and, while "
          "its origin is in the tree, the origin itself")
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
    # The pure byte legs and forbidden sets this gate uses ARE the objects of the pack's pinned copy
    # _pack_byte_canon (held to its origin by pack_copy_findings while opf/ is in this repository). A
    # re-implementation that shadowed the import with a local definition would fork silently from that
    # pinned copy; this identity check fails closed if it ever does.
    import _pack_byte_canon as _bc
    if not (scan_bytes is _bc.scan_bytes and FORBIDDEN is _bc.FORBIDDEN
            and FORBIDDEN_RE is _bc.FORBIDDEN_RE and BOM is _bc.BOM
            and ZERO_WIDTH is _bc.ZERO_WIDTH and BIDI is _bc.BIDI
            and LINE_SEPARATORS is _bc.LINE_SEPARATORS
            and _is_two_space_break is _bc._is_two_space_break):
        failures.append("byte-canon primitives are not the single _byte_canon source (a silent fork)")
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
    # Line and paragraph separators: the repository-only leg flags each literal U+2028 and U+2029, in a
    # string and in a comment, one finding per occurrence. They stay outside _byte_canon.FORBIDDEN (opf's
    # view generator reads that set), so scan_bytes alone must still pass them.
    for name, text, cp in (
            ("line-separator-in-string", 'x = "a{}b"\n'.format(chr(0x2028)), "U+2028"),
            ("paragraph-separator-in-string", 'x = "a{}b"\n'.format(chr(0x2029)), "U+2029"),
            ("line-separator-in-comment", "# a{}b\n".format(chr(0x2028)), "U+2028"),
            ("paragraph-separator-in-comment", "# a{}b\n".format(chr(0x2029)), "U+2029")):
        data = text.encode("utf-8")
        want = "byte offset {}: line or paragraph separator {} ".format(
            data.find(chr(int(cp[2:], 16)).encode("utf-8")), cp)
        if [f[:len(want)] for f in scan_line_separators(data)] != [want]:
            failures.append("scan_line_separators[{}] did not report {} once at its byte offset "
                            "({!r})".format(name, cp, want))
        if scan_bytes(data):
            failures.append("scan_bytes[{}] reported a separator; it must stay out of the shared "
                            "forbidden set".format(name))
    both = "a{}b{}c\n".format(chr(0x2028), chr(0x2029)).encode("utf-8")
    if [f.split(":")[0] for f in scan_line_separators(both)] != ["byte offset 1", "byte offset 5"]:
        failures.append("scan_line_separators did not report each of two separators once at its "
                        "byte offset")
    # The gate's own run() path, over an isolated temp tree: run() must report each separator under its
    # path and byte offset and exit 1, and pass the same tree without them. Only the inputs that need git
    # or the live policy (the scope, the policy, the attribute map, the platform probe) are replaced; the
    # per-file read and judgement are run()'s own, so a run() that drops the separator scan fails here.
    import contextlib as _cl
    import io as _io
    import tempfile as _tf
    sep_files = {"ls.py": ('x = "a{}b"\n'.format(chr(0x2028)), "byte offset 6: ", "U+2028"),
                 "ps.md": ("# a{}b\n".format(chr(0x2029)), "byte offset 3: ", "U+2029")}
    g = globals()
    saved_inputs = {k: g[k] for k in ("release_scope", "load_policy", "effective_attributes",
                                      "check_matrix", "pack_copy_findings")}
    try:
        with _tf.TemporaryDirectory() as _d:
            _root = Path(_d)
            (_root / "clean.txt").write_bytes(b"clean\n")
            for name, (text, _off, _cp) in sep_files.items():
                (_root / name).write_bytes(text.encode("utf-8"))
            g["load_policy"] = lambda _r: ([], [])
            g["effective_attributes"] = lambda _r, s: {p: {"text": "set", "eol": "lf"} for p in s}
            g["check_matrix"] = lambda _r: None
            g["pack_copy_findings"] = lambda _r: []
            for scope, want_rc, want_lines in (
                    ({"clean.txt"}, 0, []),
                    ({"clean.txt"} | set(sep_files), 1,
                     ["  {}: {}line or paragraph separator {} ".format(name, off, cp)
                      for name, (_t, off, cp) in sorted(sep_files.items())])):
                g["release_scope"] = lambda _r, _s=scope: (set(_s), set())
                out = _io.StringIO()
                with _cl.redirect_stdout(out), _cl.redirect_stderr(out):
                    rc = run(_root)
                lines = [l for l in out.getvalue().splitlines() if l.startswith("  ")]
                if rc != want_rc or [l[:len(w)] for l, w in zip(lines, want_lines)] != want_lines \
                        or len(lines) != len(want_lines):
                    failures.append("run() over a temp tree with {} gave exit {} and findings {}; "
                                    "expected exit {} and {}".format(sorted(scope), rc, lines, want_rc,
                                                                     want_lines))
    finally:
        g.update(saved_inputs)
    for cp in (0x2028, 0x2029):
        if chr(cp) in FORBIDDEN.values():
            failures.append("U+{:04X} is in _byte_canon.FORBIDDEN, which also drives opf's view "
                            "generator".format(cp))
        if LINE_SEPARATORS.get(chr(cp).encode("utf-8")) != chr(cp):
            failures.append("U+{:04X} is missing from _byte_canon.LINE_SEPARATORS".format(cp))
    if len(LINE_SEPARATORS) != 2:
        failures.append("_byte_canon.LINE_SEPARATORS holds {} entries; expected exactly U+2028 and "
                        "U+2029".format(len(LINE_SEPARATORS)))
    # Control: a printable non-ASCII letter (U+00E9) passes both legs, and the escape text passes too.
    for name, data in (("e-acute", 'x = "caf{}"\n'.format(chr(0x00E9)).encode("utf-8")),
                       ("escape-text", b'x = "a\\u2028b\\u2029c"\n')):
        if scan_bytes(data) or scan_line_separators(data):
            failures.append("control[{}] was flagged".format(name))
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
    # load_policy strict schema: a valid policy passes; each malformation fails closed.
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
    if not _policy_rejects(dict(base_policy, hardbreak={"not": "a list"})):
        failures.append("a non-array hardbreak policy value was not rejected")
    # validate_allowances row schema: an unknown key and a bool offset each fail closed.
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
    # A nested / non-string codepoints element must fail closed (GateError -> exit 2), never
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
    # No allowance row can clear a line or paragraph separator.
    for cp in ("U+2028", "U+2029"):
        try:
            validate_allowances(repo_root(), [dict(good_row, codepoints=[cp])], {"x"})
            failures.append("an allowance naming {} was not rejected".format(cp))
        except GateError as exc:
            if "outside the forbidden sets" not in str(exc):
                failures.append("an allowance naming {} was not rejected on its own terms: {}".format(
                    cp, exc))
    # Two-space CommonMark hard break: cleared only with hardbreak=True, and ONLY for exactly two
    # trailing spaces on a non-blank line. Every other trailing-whitespace vector still fails, even on a
    # hardbreak-allowed path. This is the durable guard for the SKILL.md five-line header render.
    if scan_bytes(b"Version: 1  \nrest\n", (), True):
        failures.append("a two-space hard break was not cleared under a hardbreak allowance")
    if not scan_bytes(b"Version: 1  \nrest\n", (), False):
        failures.append("a two-space trailing run cleared without a hardbreak allowance")
    if not scan_bytes(b"Version: 1   \nrest\n", (), True):
        failures.append("a three-space trailing run wrongly cleared as a hard break")
    if not scan_bytes(b"Version: 1\t\nrest\n", (), True):
        failures.append("a trailing tab wrongly cleared as a hard break")
    if not scan_bytes(b"Version: 1 \nrest\n", (), True):
        failures.append("a ONE-space trailing run wrongly cleared as a two-space hard break")
    if not scan_bytes(("Version: 1" + chr(0x00A0) + "\nrest\n").encode("utf-8"), (), True):
        failures.append("a trailing non-breaking space wrongly cleared as a hard break")
    if not scan_bytes(b"  \nrest\n", (), True):
        failures.append("an all-space line wrongly cleared as a hard break")
    if not _has_two_space_break(b"Version: 1  \nx\n"):
        failures.append("_has_two_space_break missed a present hard break")
    if _has_two_space_break(b"Version: 1\nx\n"):
        failures.append("_has_two_space_break saw a hard break where none exists")
    # validate_hardbreak schema: an unknown key, a missing field, and an out-of-scope path fail closed.
    hb_good = {"path": "x", "rationale": "r"}
    for bad, reason in ((dict(hb_good, extra=1), "unknown key"),
                        ({"path": "x"}, "malformed"),
                        (dict(hb_good, rationale=""), "malformed")):
        try:
            validate_hardbreak(repo_root(), [bad], {"x"})
            failures.append("validate_hardbreak accepted a bad row ({})".format(reason))
        except GateError as exc:
            if reason not in str(exc):
                failures.append("validate_hardbreak rejected a row on wrong terms: {}".format(exc))
    try:
        validate_hardbreak(repo_root(), [hb_good], set())
        failures.append("validate_hardbreak accepted an out-of-scope path")
    except GateError as exc:
        if "in-scope" not in str(exc):
            failures.append("validate_hardbreak out-of-scope rejection on wrong terms: {}".format(exc))
    # validate_hardbreak read-dependent legs: a valid used row passes; duplicate, unused/stale, and
    # unreadable rows each fail closed. Exercised against a temp root so the mutation guard is real.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        _root = Path(_d)
        (_root / "f.md").write_text("Version: 1  \nx\n")  # carries a two-space hard break
        if validate_hardbreak(_root, [{"path": "f.md", "rationale": "r"}], {"f.md"}) != {"f.md"}:
            failures.append("validate_hardbreak rejected a valid used hardbreak row")
        try:
            validate_hardbreak(_root, [{"path": "f.md", "rationale": "r"},
                                       {"path": "f.md", "rationale": "r"}], {"f.md"})
            failures.append("validate_hardbreak accepted a duplicate path")
        except GateError as exc:
            if "duplicate" not in str(exc):
                failures.append("validate_hardbreak duplicate rejection on wrong terms: {}".format(exc))
        (_root / "g.md").write_text("Version: 1\nx\n")  # no two-space break -> unused/stale
        try:
            validate_hardbreak(_root, [{"path": "g.md", "rationale": "r"}], {"g.md"})
            failures.append("validate_hardbreak accepted a stale (unused) allowance")
        except GateError as exc:
            if "unused" not in str(exc):
                failures.append("validate_hardbreak unused rejection on wrong terms: {}".format(exc))
        try:
            validate_hardbreak(_root, [{"path": "missing.md", "rationale": "r"}], {"missing.md"})
            failures.append("validate_hardbreak accepted an unreadable path")
        except GateError as exc:
            if "cannot read" not in str(exc):
                failures.append("validate_hardbreak unreadable rejection on wrong terms: {}".format(exc))
    # effective_attributes fail-closed: launch failure and malformed response each -> GateError.
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
    # _load_vectors strict rows: a malformed row and a missing required class each fail closed.
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
    # An unknown vector-row key fails closed (exit 2), not a silent exit-0 accept.
    unknown_key = [dict(r) for r in valid_vectors]
    unknown_key[1]["surprise"] = 1
    if not _vectors_reject(unknown_key):
        failures.append("an unknown vector-row key was not rejected")
    # A malformed sha256 (wrong length, non-hex, or uppercase) on a pass vector is a schema
    # error (exit 2) at load, not a downstream exit-1 digest mismatch.
    for bad in ("0" * 63, "0" * 65, "g" * 64, "A" * 64):
        bad_sha = [dict(r) for r in valid_vectors]
        bad_sha[0]["sha256"] = bad
        if not _vectors_reject(bad_sha):
            failures.append("a malformed pass-vector sha256 {!r} was not rejected".format(bad))
    # Completeness guard (fail-closed, exit 2): every non-pass class MUST carry a CLASS_FINDING
    # binding, so a class added to VECTOR_CLASSES cannot silently ship unbound. It runs before any
    # CLASS_FINDING lookup below, so a gap fails closed here rather than raising a bare KeyError later.
    unbound = [c for c in VECTOR_CLASSES if c != "pass" and c not in CLASS_FINDING]
    if unbound:
        print("error: vector class(es) {} have no CLASS_FINDING binding; fail-closed".format(
            ", ".join(unbound)), file=sys.stderr)
        return 2
    # The class->finding binding is exercised, not bool(findings). A cross-class payload swap
    # (every failure payload replaced with identical CRLF bytes) must be flagged:
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
    # cross-class payload swap (every failure payload replaced with identical CRLF bytes)
    # does not pass.
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
    # The pinned pack copies (tools/pack-copies-pin.toml), over a temp root: a copy at its pinned digest is
    # quiet; one changed byte, a missing copy and a malformed digest each fire.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _d:
        _root = Path(_d)
        (_root / "tools").mkdir()
        _body = b"x = 1\n"
        (_root / "tools" / "_pack_x.py").write_bytes(_body)
        _pin = '[files."tools/_pack_x.py"]\norigin = "opf/tools/_x.py"\nsha256 = "{}"\n'
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_body).hexdigest()), encoding="utf-8")
        if pack_copy_findings(_root) != []:
            failures.append("pack copy at its pinned digest was not quiet")
        (_root / "tools" / "_pack_x.py").write_bytes(b"x = 2\n")
        if len(pack_copy_findings(_root)) != 1:
            failures.append("a drifted pack copy did not fire")
        (_root / "tools" / "_pack_x.py").unlink()
        if len(pack_copy_findings(_root)) != 1:
            failures.append("a missing pack copy did not fire")
        (_root / PACK_COPIES_REL).write_text(_pin.format("0" * 63), encoding="utf-8")
        try:
            pack_copy_findings(_root)
            failures.append("a malformed pack-copy digest was not fail-closed")
        except GateError:
            pass
        # A copy whose floor guard refuses under its own name is held to the origin's digest with that one name
        # put back; any other changed byte, and the own-name refusal twice, still fire.
        _guard = b'x = 1\n"error: {} requires Python 3.14 or newer"\n'
        _origin_body = _guard.replace(b"{}", b"_x.py")
        _copy_body = _guard.replace(b"{}", b"_pack_x.py")
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_origin_body).hexdigest()),
                                             encoding="utf-8")
        for _label, _body2, _want in (("a copy whose guard names its own file", _copy_body, 0),
                                      ("a copy whose guard still names the origin", _origin_body, 0),
                                      ("a renamed guard plus one other changed byte",
                                       _copy_body.replace(b"x = 1", b"x = 2"), 1),
                                      ("a renamed guard written twice", _copy_body + _copy_body[6:], 1)):
            (_root / "tools" / "_pack_x.py").write_bytes(_body2)
            if len(pack_copy_findings(_root)) != _want:
                failures.append("pack copy: {} gave the wrong finding count".format(_label))
        # While the origin is PRESENT in the tree, the copy is also held to the origin's live bytes, so a
        # fork of either side fires until opf/ leaves; the own-name refusal normalization governs this
        # comparison too.
        (_root / "opf" / "tools").mkdir(parents=True)
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_body).hexdigest()),
                                             encoding="utf-8")
        (_root / "tools" / "_pack_x.py").write_bytes(_body)
        (_root / "opf" / "tools" / "_x.py").write_bytes(_body)
        if pack_copy_findings(_root) != []:
            failures.append("a pack copy matching its present origin was not quiet")
        (_root / "opf" / "tools" / "_x.py").write_bytes(b"x = 3\n")
        _got = pack_copy_findings(_root)
        if len(_got) != 1 or "disagree" not in _got[0]:
            failures.append("an origin edited apart from its copy did not fire while present")
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_origin_body).hexdigest()),
                                             encoding="utf-8")
        (_root / "tools" / "_pack_x.py").write_bytes(_copy_body)
        (_root / "opf" / "tools" / "_x.py").write_bytes(_origin_body)
        if pack_copy_findings(_root) != []:
            failures.append("own-name refusal normalization was not applied against the present origin")
        (_root / "opf" / "tools" / "_x.py").unlink()
        # A malformed origin path (empty, absolute, a '..' component, outside opf/, or opf/ itself with
        # nothing below it) fails closed.
        for _bad in ("", "/opf/tools/_x.py", "opf/../tools/_x.py", "tools/_y.py", "opf"):
            (_root / PACK_COPIES_REL).write_text(
                '[files."tools/_pack_x.py"]\norigin = "{}"\nsha256 = "{}"\n'.format(_bad, "0" * 64),
                encoding="utf-8")
            try:
                pack_copy_findings(_root)
                failures.append("a malformed origin path {!r} was not fail-closed".format(_bad))
            except GateError:
                pass
        # A pinned copy that is a symlink fails closed on its own row, even when the copy sits outside
        # the swept tools/_pack_* and tools/_vendor/ trees (whose sweep refuses symlinks separately).
        _pin_aux = (_pin + '\n[files."tools/aux_copy.py"]\norigin = "opf/tools/_aux.py"\n'
                    'sha256 = "{}"\n'.format("0" * 64))
        (_root / PACK_COPIES_REL).write_text(_pin_aux.format(hashlib.sha256(_body).hexdigest()),
                                             encoding="utf-8")
        (_root / "tools" / "_pack_x.py").unlink()
        (_root / "tools" / "_pack_x.py").write_bytes(_body)
        try:
            (_root / "tools" / "aux_copy.py").symlink_to(_root / PACK_COPIES_REL)
            _sym_ok = True
        except OSError:
            _sym_ok = False
        if _sym_ok:
            try:
                pack_copy_findings(_root)
                failures.append("a pinned pack copy that is a symlink was not fail-closed")
            except GateError:
                pass
            (_root / "tools" / "aux_copy.py").unlink()
        # A file in the pinned-copy scope that no row names fails closed; an [[exclude]] row with a
        # reason clears exactly it; each malformed exclude row (no reason, an empty reason, a pinned
        # path) fails closed while the excluded file exists, so the row schema is what fires.
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_body).hexdigest()),
                                             encoding="utf-8")
        (_root / "tools" / "_vendor").mkdir()
        (_root / "tools" / "_vendor" / "stray.txt").write_bytes(b"s\n")
        try:
            pack_copy_findings(_root)
            failures.append("an unpinned file under tools/_vendor/ was not fail-closed")
        except GateError:
            pass
        _excl = '\n[[exclude]]\npath = "tools/_vendor/stray.txt"\nreason = "left unpinned deliberately"\n'
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_body).hexdigest()) + _excl,
                                             encoding="utf-8")
        if pack_copy_findings(_root) != []:
            failures.append("a deliberately excluded unpinned file was not quiet")
        for _bad_excl in ('\n[[exclude]]\npath = "tools/_vendor/stray.txt"\n',
                          '\n[[exclude]]\npath = "tools/_vendor/stray.txt"\nreason = ""\n',
                          '\n[[exclude]]\npath = "tools/_pack_x.py"\nreason = "also pinned"\n' + _excl):
            (_root / PACK_COPIES_REL).write_text(
                _pin.format(hashlib.sha256(_body).hexdigest()) + _bad_excl, encoding="utf-8")
            try:
                pack_copy_findings(_root)
                failures.append("a malformed exclude row was not fail-closed: {!r}".format(_bad_excl))
            except GateError:
                pass
        # A symlink inside the swept scope fails closed even when an exclude row names it, so the sweep's
        # own symlink refusal (not the unpinned-file refusal) is what fires.
        if _sym_ok:
            (_root / "tools" / "_vendor" / "stray.txt").unlink()
            (_root / "tools" / "_vendor" / "stray.txt").symlink_to(_root / PACK_COPIES_REL)
            (_root / PACK_COPIES_REL).write_text(
                _pin.format(hashlib.sha256(_body).hexdigest()) + _excl, encoding="utf-8")
            try:
                pack_copy_findings(_root)
                failures.append("a symlink inside the swept scope was not fail-closed")
            except GateError:
                pass
            (_root / "tools" / "_vendor" / "stray.txt").unlink()
            (_root / "tools" / "_vendor" / "stray.txt").write_bytes(b"s\n")
        # A stale exclude row (naming no file in the swept scope) fails closed; __pycache__ byte-code
        # under the scope is skipped.
        (_root / "tools" / "_vendor" / "stray.txt").unlink()
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_body).hexdigest()) + _excl,
                                             encoding="utf-8")
        try:
            pack_copy_findings(_root)
            failures.append("a stale exclude row (naming no file in scope) was not fail-closed")
        except GateError:
            pass
        (_root / PACK_COPIES_REL).write_text(_pin.format(hashlib.sha256(_body).hexdigest()),
                                             encoding="utf-8")
        (_root / "tools" / "_vendor" / "__pycache__").mkdir()
        (_root / "tools" / "_vendor" / "__pycache__" / "stray.pyc").write_bytes(b"\x00")
        if pack_copy_findings(_root) != []:
            failures.append("__pycache__ byte-code under the pinned-copy scope was not skipped")
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: byte-format legs, the line and paragraph separator leg (direct and through "
          "run() over a temp tree), allowance scoping, "
          "coverage logic, the containment "
          "mode table and probe fail-safe, the policy and vector schema fail-closed legs, the "
          "published vectors, and the pinned pack-copy legs (the pin row, the live-origin comparison "
          "while the origin is present, and their refusals) all hold")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:  # --selftest: the spec spelling (3.4)
        return self_test_main()
    if args:
        print("usage: check_byte_canon.py [--self-test]; fail-closed", file=sys.stderr)
        return 2
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
