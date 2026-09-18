#!/usr/bin/env python3
"""The pure byte-canon scanner, the ONE shared source (OPF-SELF-CONTAIN).

This holds the pure, filesystem-free byte legs of the byte-canon gate: `scan_bytes` and the forbidden
codepoint sets it scans for. It was extracted from `check_byte_canon.py` so the OPF tooling under
`opf/tools/` is dependency-closed (imports nothing upward into `tools/`), while AIQT's
`tools/check_byte_canon.py` re-imports these primitives from here, keeping ONE source with no fork. The
release-scope loading, policy parsing, allowance/hardbreak validation, and containment matrix stay in
`check_byte_canon.py` (they reach into AIQT's `_gen_common`/`gen_manifest`/`_containment`). Offline,
stdlib only, pure.
"""
import re

ZERO_WIDTH = tuple(chr(c) for c in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
BIDI = tuple(chr(c) for c in (0x061C, 0x200E, 0x200F)
             + tuple(range(0x202A, 0x202F)) + tuple(range(0x2066, 0x206A)))
FORBIDDEN = {ch.encode("utf-8"): ch for ch in ZERO_WIDTH + BIDI}
FORBIDDEN_RE = re.compile(b"|".join(re.escape(b) for b in sorted(FORBIDDEN)))
BOM = b"\xef\xbb\xbf"


# --- pure legs (always exercised by --self-test) ----------------------------------------------------

def _is_two_space_break(body):
    """True iff `body` (one line, any trailing CR already removed) ends in EXACTLY two spaces after
    non-space content: the CommonMark / markdownlint MD009 two-space hard break. A single space, three
    or more spaces, a tab or other whitespace, and an all-whitespace line are all False, so nothing but
    the exact hard break is ever permitted."""
    stripped = body.rstrip()
    return bool(stripped) and body[len(stripped):] == "  "


def scan_bytes(data, allowances=(), hardbreak=False):
    """The 3.1 legs over one file's raw bytes. allowances is a tuple of (start, end, codepoint-set)
    byte-range rows already validated for this file. hardbreak, when True, permits an EXACTLY-two-trailing-
    space CommonMark hard break on any non-blank line (markdownlint MD009 br_spaces=2) for this path, and
    nothing else; every other trailing-whitespace vector still fails. Returns a list of finding
    strings. A file that does not decode as UTF-8 yields a finding (the thing asserted against), never an
    error."""
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
            if hardbreak and _is_two_space_break(body):
                continue  # a permitted CommonMark two-space hard break on a hardbreak-allowed path
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
