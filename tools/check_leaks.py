#!/usr/bin/env python3
"""Leak-denylist gate: block internal host specifics and known internal codenames from public content.

Two layers (defense in depth):
  1. STRUCTURAL patterns (encoded here, generic, reveal no host specifics): absolute host paths under
     this host's roots, RFC1918 private IPs, GitHub token/PAT shapes, and worker-account id shapes.
  2. A HASHED codename denylist (tools/leak-hashes.txt): SHA-256 hashes only, never the words. Text is
     normalized (camelCase split; lowercased; every non-alphanumeric run, including `_`, `-`, and spaces,
     is a separator) and 1..maxn word n-grams are hashed and matched, so a codename written with a space,
     hyphen, underscore, or CamelCase all match the same hash.

HONEST SCOPE. This catches ACCIDENTAL reintroduction of internal terms and host specifics; it is not an
exfiltration control. The SHA-256 hashes are UNSALTED and low-entropy n-grams are dictionary-reversible,
so this is OBSCURITY, not secrecy (a salt cannot help: a public gate must hash arbitrary public text, so
any salt would also be public). Deliberate obfuscation (mid-word splits, HTML entities, zero-width chars)
can evade the hash layer; a ZIP archive is unpacked and its members scanned, but another compressed
format (a gzip, bzip2 or 7z file), a ZIP that begins after leading bytes (a self-extracting ZIP) inside a
walked file not named .zip or inside an archive member, and compressed bytes an archive holds in a comment
or an extra field are only byte-scanned, so text compressed inside them is not seen; the structural path
patterns are scoped to this host's Linux layout. The dual-family verifiers and the private plaintext check
are the compensating layers. A line carrying a `leak-allow` marker is exempt from the STRUCTURAL layer.

SCANNED SET. The walk yields every file in the working tree except the .git, node_modules and
__pycache__ subtrees (the denylist file included). Each yielded path is first CLASSIFIED by a stat probe
(a metadata check, not a content read), then its content is read as bytes and handled one of four ways:
  - it is a ZIP archive (a name ending in .zip in any case, or content opening with a ZIP signature,
    whatever that content decodes as): an archive whose stat size is over MAX_ARCHIVE_FILE_SIZE is
    CANNOT EVALUATE before its bytes are read (a file not named .zip has only its first four bytes read,
    to see its signature); otherwise its own bytes are scanned in the lossless byte view (so member
    names, the archive comment and any bytes outside the members are read too), and it is opened with
    the standard-library zipfile module entirely in memory (nothing is extracted to disk and no member
    name is used as a filesystem path), each member's content decoded by the three rules below and
    scanned, a finding named <archive>!<member>:<line>. Before any member is read, the central directory
    is checked against MAX_ARCHIVE_MEMBERS and MAX_ARCHIVE_TOTAL_SIZE (declared uncompressed bytes), and
    each member against the encryption flag, a compression method outside ARCHIVE_METHODS and a
    nested-archive name; then the byte spans of the parsed records (each member's local header with its
    name and extra field, its data and any data descriptor, the central directory, any ZIP64 end record
    and locator, and the end record with its comment) must be disjoint and cover the file exactly from
    its first byte to its last, so leading bytes, a gap, an overlap, trailing bytes or a second archive
    in the same file are CANNOT EVALUATE naming the archive. An archive over any bound, a corrupt or
    encrypted archive, a member that cannot be read or decoded, a stored member whose stored bytes are
    not exactly its declared size and its content, a deflated member whose declared compressed bytes
    are not consumed by exactly one complete deflate stream of exactly its declared size (re-inflated
    independently, so a directory understating a size or a second stream after the first cannot hide
    text), and a nested archive member (by its name or by a ZIP signature opening its content) are each
    CANNOT EVALUATE, naming the archive or <archive>!<member>. An archive's scan stops at its first
    refusal, so when several members fail, only the first is named;
  - it decodes as UTF-8: it is scanned, whatever its suffix (a .csv, .example or .sha256 included);
  - it starts with a UTF-16 or UTF-32 byte-order mark: the declared encoding is decided by a
    successful decode, never by the 4-byte prefix alone (the UTF-32 LE mark embeds the UTF-16 LE
    mark, and a valid UTF-16 stream whose first character is U+0000 carries the same four bytes:
    UTF-32 is tried first, UTF-16 is the fallback where its 2-byte mark is present); a stream
    decoding as neither candidate (an odd length, an unpaired surrogate) is a read error (exit 2,
    naming the file), never an ineffective clean scan. A decodable BOM-marked file is scanned in
    EVERY wide view -- UTF-16 LE and BE each at byte offsets 0 and 1 (every phase of a 2-byte unit),
    and UTF-32 LE and BE each at byte offsets 0 to 3 (every phase of a 4-byte unit), each decoded
    best-effort with undecodable units replaced -- AND in the lossless byte view, so a
    mismatched-endian or misaligned mark cannot hide UTF-16 or UTF-32 text from every view at once
    (each unit size is covered at every byte phase); counted as one
    lossless decode (a wide encoding WITHOUT such a mark still falls to the byte decode below, where
    the patterns cannot match across its wide characters; a disclosed residual);
  - anything else holding a non-UTF-8 byte, whatever its suffix or that suffix's case (a missing suffix
    included), is decoded losslessly (surrogateescape keeps every byte; none is dropped or replaced)
    and scanned.
SYMLINK RULES, stated exactly: a symlink to a regular file is followed (the stat probe reads the target)
and the target's content is scanned; a dangling or unresolvable file symlink is a read error (exit 2); a
non-regular target (a FIFO, a device) is CANNOT EVALUATE and its content is never opened; a SYMLINKED
DIRECTORY is never followed and never silently dropped: it is CANNOT EVALUATE, the subtree behind it
unread.
CANNOT EVALUATE is never a skip: each such input is named on a CANNOT EVALUATE line and the run exits 2,
so PASS is printed only when every walked input was read. The PASS and FAIL lines state the scanned-file
count and the ZIP archive and member counts.
Exit 0 clean, 1 on any finding or a malformed hashes file, 2 on a read error (unreadable dir/file,
fail-closed) or on any CANNOT EVALUATE input.

  check_leaks.py              scan the working tree
  check_leaks.py --self-test  build fixture trees and assert the scanned-set contract above
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_leaks.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import io
import re
import stat
import struct
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

SKIP_DIRS = {".git", "node_modules", "__pycache__"}
# Declared text suffixes. This gate no longer keys its decode on them (every non-UTF-8 file that is not a
# ZIP archive is decoded losslessly and scanned, whatever its suffix); the set stays because the
# internal-names gate imports it as its own scope filter.
TEXT_SUFFIXES = {".md", ".mdc", ".py", ".sh", ".yml", ".yaml", ".toml", ".json", ".txt",
                 ".html", ".css", ".js", ".cfg", ".ini", ".conf", ".svg"}
# A ZIP archive is unpacked and every member scanned, never skipped. It is recognized by a name ending in
# ZIP_SUFFIX (case-insensitively) or by content opening with a ZIP signature (a local file header, an
# empty archive's end record, a split-archive marker), the same test marking a nested archive member.
ZIP_SUFFIX = ".zip"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
# Archive bounds: the archive's own size is checked on the stat probe before its bytes are read, the other
# two from the central directory before any member is read; over any of them is CANNOT EVALUATE.
MAX_ARCHIVE_MEMBERS = 1000  # far above the two-member shipped skill packs; keeps one archive's work finite
# What is bounded is the declared uncompressed bytes per archive (each member read stops one byte past its
# declared size) and, below, the archive's own size; memory is not capped as such. Measured, not a
# guarantee: one gate run over an archive holding a single stored member at this bound (a UTF-16 LE
# byte-order mark, then about 930,000 distinct eight-character words) peaked at 673 MiB resident (VmHWM;
# CPython 3.14.4, Linux x86-64, the shipped four-hash denylist); a repeated English sentence peaked at 431 MiB.
MAX_ARCHIVE_TOTAL_SIZE = 16 * 1024 * 1024
MAX_ARCHIVE_FILE_SIZE = MAX_ARCHIVE_TOTAL_SIZE + 1024 * 1024  # room for stored members at the total bound plus records
# zipfile caps each deflate read at the size asked for, but its bzip2, LZMA and Zstandard readers expand a
# whole compressed chunk at once, so a member stored any other way is CANNOT EVALUATE, never read.
ARCHIVE_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)

_OCT = r'(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
STRUCTURAL = [
    (re.compile(r'(?<![\w.])/(?:opt|var/lib|mnt|home)/[A-Za-z0-9_.][A-Za-z0-9_./-]*'), "absolute host path"),
    (re.compile(r'\b10\.' + _OCT + r'\.' + _OCT + r'\.' + _OCT + r'\b'), "private IP (10/8)"),
    (re.compile(r'\b172\.(?:1[6-9]|2[0-9]|3[01])\.' + _OCT + r'\.' + _OCT + r'\b'), "private IP (172.16/12)"),
    (re.compile(r'\b192\.168\.' + _OCT + r'\.' + _OCT + r'\b'), "private IP (192.168/16)"),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'), "GitHub PAT"),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'), "GitHub token"),
    (re.compile(r'\b(?:claude|codex)[-_](?:max|team|teams|worker|pro)[-_][a-z0-9][a-z0-9_-]*\b', re.I),
     "worker-account id shape"),
]

_CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
_WORD = re.compile(r'[a-z0-9]+')      # '_' is a SEPARATOR, so under_score == with-hyphen == with space.
_HEX64 = re.compile(r'^[0-9a-f]{64}$')


def _tokens(text):
    return _WORD.findall(_CAMEL.sub(" ", text).lower())


def ngram_forms(text, maxn):
    toks = _tokens(text)
    grams = set()
    for n in range(1, maxn + 1):
        for i in range(len(toks) - n + 1):
            grams.add("-".join(toks[i:i + n]))
    return grams


def term_hash(term):   # the generator (store/gen-leak-hashes.py) imports this for the shared normalization.
    return hashlib.sha256("-".join(_tokens(term)).encode("utf-8")).hexdigest()


def load_denylist(root):
    """Return (hashes, maxn, bad_lines). bad_lines names any non-hash entry (integrity failure)."""
    f = root / "tools" / "leak-hashes.txt"
    hashes, maxn, bad, maxn_set = set(), 3, [], False
    try:
        content = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        # An ABSENT denylist is intended (no extra terms -> empty set). An UNREADABLE denylist (or an
        # unreadable parent) raises a non-FileNotFound OSError that propagates to main's fail-closed
        # try (exit 2), so it can never read as an empty denylist and pass clean.
        content = None
    if content is not None:
        for number, line in enumerate(content.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                parts = s.split()
                if len(parts) >= 2 and parts[0] == "#" and parts[1] == "maxn":
                    # exactly ONE "# maxn N", N a bounded positive integer; a duplicate, non-positive,
                    # oversized, or malformed directive fails closed (each would silently weaken or disable
                    # the codename layer). len<=3 is checked before int() so an overlong run cannot raise.
                    if maxn_set:
                        bad.append("leak-hashes.txt:{}: duplicate '# maxn' directive".format(number))
                    elif (len(parts) == 3 and parts[2].isascii() and parts[2].isdigit()
                          and 1 <= len(parts[2]) <= 3 and 1 <= int(parts[2]) <= 100):
                        maxn, maxn_set = int(parts[2]), True
                    else:
                        bad.append("leak-hashes.txt:{}: malformed '# maxn N' (need one integer 1..100)".format(number))
                continue
            if _HEX64.match(s):
                hashes.add(s)
            else:
                bad.append("leak-hashes.txt:{}: not a 64-hex hash (possible plaintext or trailing token)".format(number))
    return hashes, maxn, bad


def scan_text(text, hashes, maxn, honor_leak_allow=True):
    """Shared blob scanner: returns (line_or_None, label) findings (STRUCTURAL carries a 1-based line; a
    hash match carries None). honor_leak_allow (file gate True, message gate False) exempts a `leak-allow`
    line from STRUCTURAL. Keeps STRUCTURAL and the hash denylist applied in exactly one place."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if honor_leak_allow and "leak-allow" in line:
            continue
        found += [(number, label) for pattern, label in STRUCTURAL if pattern.search(line)]
    if hashes:
        for gram in ngram_forms(text, maxn):
            if hashlib.sha256(gram.encode("utf-8")).hexdigest() in hashes:
                found.append((None, "internal codename (hash match)"))
                break
    return found


def _display(rel):
    """A printable form of a relative path: a non-UTF-8 file name prints with backslash escapes instead of
    raising on output."""
    return str(rel).encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def _member_display(name):
    """A printable form of an archive member name: every non-printable character (a newline included) is
    backslash-escaped, so a crafted member name cannot forge an output line."""
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in name)


class CannotEvaluate(Exception):
    """An input the gate cannot read in full: a non-regular file, or an archive or archive member that
    read_archive refuses. Never a skip: scan_tree names it with its reason (and the member, when one
    member is the cause), and run() exits 2."""

    def __init__(self, reason, member=None):
        super().__init__(reason)
        self.member = member


def _stat(path):
    """The classification probe: stat(), FOLLOWING a file symlink (the file rule), for the mode and the
    size. A module-level seam so the self-test can report a non-regular mode (case 6) and an archive size
    (case 5s) deterministically on every runner."""
    return path.stat()


def _read_head(path):
    """The first four bytes, read only from a file over MAX_ARCHIVE_FILE_SIZE to see whether its content
    opens with a ZIP signature before the rest of it is read."""
    with path.open("rb") as handle:
        return handle.read(4)


def _read_bytes(path):
    """The content read. A module-level seam so the self-test can raise a read error deterministically
    (case 7) on a runner whose chmod 0 cannot bite (root or CAP_DAC_OVERRIDE)."""
    return path.read_bytes()


# The wide views scanned for every decodable BOM-marked file, beside the declared decode and the
# lossless byte view: UTF-16 LE and BE at byte offsets 0 and 1 (every phase of a 2-byte unit) and
# UTF-32 LE and BE at byte offsets 0 to 3 (every phase of a 4-byte unit), so UTF-16 or UTF-32 text at
# ANY byte alignment behind a mismatched-endian or misaligned mark is decoded by at least one view
# (the guaranteed bound is AT LEAST ONE view: an ASCII arrangement typically also reads in a second,
# endian-swapped or phase-shifted view, but a non-ASCII wide text can show in exactly one).
# The self-test asserts this enumeration DIRECTLY and the views read_scan_text returns (case 3v):
# several arrangements match in more than one view, so no single planted file can discriminate
# every entry.
WIDE_VIEWS = (("utf-16-le", 0), ("utf-16-le", 1), ("utf-16-be", 0), ("utf-16-be", 1),
              ("utf-32-le", 0), ("utf-32-le", 1), ("utf-32-le", 2), ("utf-32-le", 3),
              ("utf-32-be", 0), ("utf-32-be", 1), ("utf-32-be", 2), ("utf-32-be", 3))


def decode_views(data, name):
    """Decode one file's (or one archive member's) bytes for the scan. Returns (texts, lossless): texts
    is a tuple of the decoded views to scan; lossless is True when non-UTF-8 content was decoded without
    dropping or replacing a byte. The policy, in order: UTF-8 content is scanned whatever the suffix;
    content behind a UTF-16 or UTF-32 byte-order mark has its declared encoding decided by a successful
    decode (UTF-32 first, then UTF-16 where its 2-byte mark is present), never by the 4-byte prefix
    alone, a stream decoding as neither candidate raising OSError naming `name` (fail-closed, never an
    ineffective clean scan), and the decodable content scanned in every WIDE_VIEWS view (UTF-16 LE and
    BE at byte offsets 0 and 1, UTF-32 LE and BE at byte offsets 0 to 3, undecodable units replaced) AND
    in the lossless surrogateescape byte view, so a fake, a mismatched-endian or a misaligned mark cannot
    hide UTF-16 or UTF-32 text from every view at once (each unit size is covered at every byte phase);
    anything else is decoded losslessly with surrogateescape and scanned, whatever its suffix."""
    try:
        return (data.decode("utf-8"),), False
    except UnicodeDecodeError:
        pass
    bom_encoding = None
    if data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        # The UTF-32 LE mark embeds the UTF-16 LE mark, and a valid UTF-16 stream whose first
        # character is U+0000 carries the same four bytes: decide by a successful decode (UTF-32
        # first, UTF-16 as the fallback where its 2-byte mark is present), never by the prefix alone.
        bom_encoding = "utf-32"
        if data[:2] == b"\xff\xfe":
            try:
                data.decode("utf-32")
            except UnicodeDecodeError:
                bom_encoding = "utf-16"
    elif data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        bom_encoding = "utf-16"
    if bom_encoding is not None:
        try:
            wide = data.decode(bom_encoding)
        except UnicodeDecodeError as exc:
            raise OSError("{}: byte-order mark declares {} (decided by decode, not by the prefix "
                          "alone) but the stream does not decode as it ({}); fail-closed"
                          .format(name, bom_encoding, exc))
        views = [wide, data.decode("utf-8", errors="surrogateescape")]
        # Every wide view beside the declared one (WIDE_VIEWS: each unit size at every byte phase): a
        # mark that misstates the endianness or sits over a stream misaligned by any byte offset
        # cannot hide UTF-16 or UTF-32 text from all of them at once. Best-effort decodes:
        # undecodable units are replaced, never raised (the declared decode above is the fail-closed
        # one, and the byte view stays lossless).
        for enc, off in WIDE_VIEWS:
            views.append(data[off:].decode(enc, errors="replace"))
        return tuple(views), True
    return (data.decode("utf-8", errors="surrogateescape"),), True


def _is_zip(name, data):
    """True for a ZIP archive: a name ending in ZIP_SUFFIX (case-insensitively) or content opening with a
    ZIP signature."""
    return name.lower().endswith(ZIP_SUFFIX) or data[:4] in ZIP_SIGNATURES


def _local_header(data, info):
    """(begin, extra) for a member's local header: begin is where the member's data starts, past the
    header's 30 fixed bytes, its name and its extra field at the lengths the local header itself declares
    (they can differ from the central directory's), and extra is that extra field. None when no local
    header sits at the member's offset."""
    start = info.header_offset
    if start < 0 or len(data) < start + 30 or data[start:start + 4] != b"PK\x03\x04":
        return None
    name_len, extra_len = struct.unpack("<HH", data[start + 26:start + 30])
    begin = start + 30 + name_len + extra_len
    return begin, data[begin - extra_len:begin]


def _member_data_matches(data, info, content):
    """True when the member's declared compressed bytes hold exactly `content` and nothing else. zipfile
    stops at the size the central directory declares and at the end of the first deflate stream, so a
    directory understating a size (beside a CRC of the shortened text) or a second stream after the first
    would otherwise hide text from the scan: the bytes are re-read from the local header and checked
    independently. A stored member's bytes must be exactly its declared size and `content`; a deflated
    member's must be consumed by exactly one complete deflate stream (its end reached, no input left
    unused or unconsumed) whose output, bounded at one byte past the declared size, is exactly that size
    and `content`."""
    header = _local_header(data, info)
    if header is None:
        return False
    begin = header[0]
    raw = data[begin:begin + info.compress_size]
    if len(raw) != info.compress_size:
        return False
    if info.compress_type == zipfile.ZIP_STORED:
        return len(raw) == info.file_size and raw == content
    inflater = zlib.decompressobj(-15)
    out = inflater.decompress(raw, info.file_size + 1)
    return (inflater.eof and not inflater.unconsumed_tail and not inflater.unused_data
            and len(out) == info.file_size and out == content)


def _coverage_problems(data, infos, start_dir):
    """Where the archive's parsed records fail to tile its bytes: an empty list when their spans are
    disjoint and cover `data` exactly from offset 0 to its end, else one printable problem per gap,
    overlap or overrun, with its offsets. The spans come from the parsed records only: each member's
    local header (with its name and extra field), its data (the directory's compressed size) and, where
    flag bit 3 is set, its data descriptor (an optional signature, a CRC and two sizes, 8 bytes each when
    the local header carries a ZIP64 extra field); the central directory; any ZIP64 end record and
    locator; and the end-of-central-directory record with its comment. So leading bytes (a
    self-extracting stub, or a second archive ahead of this one), a local entry the directory does not
    list, a gap between members and trailing bytes are each a problem."""
    size = len(data)
    # The end record, located the way zipfile locates it: the last 22 bytes when they hold one with no
    # comment, else the last signature in the final 22 + 65535 bytes.
    if data[size - 22:size - 18] == b"PK\x05\x06" and data[size - 2:] == b"\x00\x00":
        end = size - 22
    else:
        end = data.rfind(b"PK\x05\x06", max(size - 22 - 0xFFFF, 0))
    spans = [(end, end + 22 + struct.unpack("<H", data[end + 20:end + 22])[0],
              "end-of-central-directory record and comment")]
    locator = end - 20
    if locator >= 0 and data[locator:locator + 4] == b"PK\x06\x07":
        # zipfile reads the ZIP64 end record where the locator points, or just before the locator when no
        # record signature is there (prepended data).
        pointer = struct.unpack("<Q", data[locator + 8:locator + 16])[0]
        record = pointer if data[pointer:pointer + 4] == b"PK\x06\x06" else locator - 56
        spans.append((record, record + 12 + struct.unpack("<Q", data[record + 4:record + 12])[0],
                      "ZIP64 end-of-central-directory record"))
        spans.append((locator, end, "ZIP64 end-of-central-directory locator"))
        directory_size = struct.unpack("<Q", data[record + 40:record + 48])[0]
    else:
        directory_size = struct.unpack("<I", data[end + 12:end + 16])[0]
    spans.append((start_dir, start_dir + directory_size, "central directory"))
    for info in infos:
        member = _member_display(info.filename)
        header = _local_header(data, info)
        if header is None:
            return ["no local header at offset {}, where the directory places {}".format(info.header_offset,
                                                                                        member)]
        begin, extra = header
        stop = begin + info.compress_size
        spans.append((info.header_offset, begin, "local header of " + member))
        spans.append((begin, stop, "data of " + member))
        if info.flag_bits & 0x08:
            length = 12
            while len(extra) >= 4:
                tag, field_len = struct.unpack("<HH", extra[:4])
                if tag == 0x0001:
                    length = 20
                extra = extra[4 + field_len:]
            if data[stop:stop + 4] == b"PK\x07\x08":
                length += 4
            spans.append((stop, stop + length, "data descriptor of " + member))
    problems, position = [], 0
    for start, stop, what in sorted(spans):
        if start > position:
            problems.append("{} byte(s) at offsets {} to {} belong to no record"
                            .format(start - position, position, start - 1))
        elif start < position:
            problems.append("the {} at offsets {} to {} overlaps the record before it"
                            .format(what, start, stop - 1))
        if stop > size:
            problems.append("the {} at offsets {} to {} runs past the archive's last byte (offset {})"
                            .format(what, start, stop - 1, size - 1))
        position = max(position, stop)
    if position < size:
        problems.append("{} byte(s) at offsets {} to {} belong to no record"
                        .format(size - position, position, size - 1))
    return problems


def read_archive(data):
    """Unpack one ZIP archive held in memory, yielding (member, texts) for each member in directory order:
    member is its printable name and texts its decode_views views. Nothing is extracted to disk and no
    member name is used as a filesystem path. Before any member is read, the central directory is
    checked: more than MAX_ARCHIVE_MEMBERS members, more than MAX_ARCHIVE_TOTAL_SIZE declared uncompressed
    bytes, an encrypted member, a compression method outside ARCHIVE_METHODS, a member named as an
    archive, or parsed records that do not tile the archive's bytes exactly (_coverage_problems) each
    raise CannotEvaluate. So do a corrupt archive, a member that cannot be read (each read asks for at
    most one byte past the member's declared size, and zipfile stops at that size and checks the CRC), a
    member whose declared compressed bytes are not exactly its content (_member_data_matches), a member
    whose content opens with a ZIP signature, and a member whose byte-order mark fails its decode.
    CannotEvaluate carries the member's name when one member is the cause; the first refusal ends the
    archive's scan, so a later failing member is not named."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # zipfile reports corruption as BadZipFile, ValueError, EOFError and others
        raise CannotEvaluate("not a readable ZIP archive: {}: {}".format(type(exc).__name__, exc))
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise CannotEvaluate("{} members, over MAX_ARCHIVE_MEMBERS ({}); no member was read"
                                 .format(len(infos), MAX_ARCHIVE_MEMBERS))
        total = sum(info.file_size for info in infos)
        if total > MAX_ARCHIVE_TOTAL_SIZE:
            raise CannotEvaluate("{} uncompressed bytes declared, over MAX_ARCHIVE_TOTAL_SIZE ({}); no "
                                 "member was read".format(total, MAX_ARCHIVE_TOTAL_SIZE))
        for info in infos:
            member = _member_display(info.filename)
            if info.flag_bits & 0x1:
                raise CannotEvaluate("encrypted member; no member was read", member)
            if info.compress_type not in ARCHIVE_METHODS:
                raise CannotEvaluate("compression method {} is neither stored nor deflated; no member was "
                                     "read".format(info.compress_type), member)
            if _is_zip(info.filename, b""):
                raise CannotEvaluate("nested archive member, not unpacked; no member was read", member)
        try:
            problems = _coverage_problems(data, infos, archive.start_dir)
        except struct.error as exc:
            problems = ["a record cannot be parsed: {}".format(exc)]
        if problems:
            raise CannotEvaluate("the parsed records do not cover the archive's bytes exactly: {}; no member "
                                 "was read".format("; ".join(problems)))
        for info in infos:
            member = _member_display(info.filename)
            try:
                with archive.open(info) as handle:
                    content = handle.read(info.file_size + 1)
            except Exception as exc:  # BadZipFile (a CRC or header mismatch), zlib.error, EOFError and others
                raise CannotEvaluate("member cannot be read: {}: {}".format(type(exc).__name__, exc), member)
            try:
                matches = _member_data_matches(data, info, content)
            except (struct.error, zlib.error) as exc:
                raise CannotEvaluate("member data cannot be checked: {}: {}".format(type(exc).__name__, exc),
                                     member)
            if not matches:
                raise CannotEvaluate("member data does not match its declared size and content (stored bytes, "
                                     "or one complete deflate stream with nothing after it), so it was not "
                                     "read in full", member)
            if _is_zip("", content):
                raise CannotEvaluate("nested archive member (its content opens with a ZIP signature), not "
                                     "unpacked", member)
            try:
                texts, _lossless = decode_views(content, member)
            except OSError as exc:
                raise CannotEvaluate("member does not decode: {}".format(exc), member)
            yield member, texts


def read_scan_text(path):
    """Read one walked file for the scan. Returns (texts, lossless, archive): texts and lossless as
    decode_views returns them, and archive None; or, for a ZIP archive (_is_zip), texts holding the
    archive's own bytes in the lossless byte view (so member names, the archive comment and any bytes
    outside the members are scanned), lossless False, and archive the read_archive generator over its
    members. A non-regular file (the stat probe follows a file symlink, so a symlink to a regular file
    reads as its target) raises CannotEvaluate before its content is opened, and so does a ZIP archive
    whose stat size is over MAX_ARCHIVE_FILE_SIZE, before its bytes are read (past its first four when
    only its signature marks it). A read error raises OSError for the caller's fail-closed exit 2 (a
    dangling symlink included)."""
    status = _stat(path)
    if not stat.S_ISREG(status.st_mode):
        raise CannotEvaluate("not a regular file; its content was never opened")
    if status.st_size > MAX_ARCHIVE_FILE_SIZE and (_is_zip(path.name, b"") or _is_zip("", _read_head(path))):
        raise CannotEvaluate("a ZIP archive of {} bytes, over MAX_ARCHIVE_FILE_SIZE ({}); its bytes were not "
                             "read".format(status.st_size, MAX_ARCHIVE_FILE_SIZE))
    data = _read_bytes(path)
    if _is_zip(path.name, data):
        return (data.decode("utf-8", errors="surrogateescape"),), False, read_archive(data)
    texts, lossless = decode_views(data, path)
    return texts, lossless, None


def scan_tree(root):
    """Scan every file under root, unpacking each ZIP archive. Returns (findings, scanned, lossless,
    archives, members, unevaluated): unevaluated names each input that could not be read in full (a
    non-regular file, a symlinked directory, an archive or <archive>!<member> that read_archive refuses),
    with its reason, and run() exits 2 on any. Raises OSError (or UnicodeDecodeError on the denylist) on a
    read error."""
    # load_denylist reads a required file (the leak-hash denylist); run keeps this call inside its
    # fail-closed try so an unreadable denylist is a clean exit 2, not a traceback.
    hashes, maxn, findings = load_denylist(root)
    findings = list(findings)
    scanned, lossless, archives, members, unevaluated = 0, 0, 0, 0, []
    symlink_dirs = []

    def scan_unit(name, texts):
        seen = set()
        for text in texts:
            for number, label in scan_text(text, hashes, maxn, honor_leak_allow=True):
                seen.add("{}:{}: {}".format(name, number, label) if number is not None
                         else "{}: {}".format(name, label))
        findings.extend(sorted(seen))

    for path in sorted(walk_files(root, SKIP_DIRS, symlink_dirs=symlink_dirs)):
        rel = _display(path.relative_to(root))
        read_members = 0
        try:
            texts, decoded_lossless, archive = read_scan_text(path)
            scan_unit(rel, texts)
            for member, member_texts in archive or ():
                scan_unit(rel + "!" + member, member_texts)
                read_members += 1
        except CannotEvaluate as exc:
            unevaluated.append("{}{} ({})".format(rel, "" if exc.member is None else "!" + exc.member, exc))
            continue
        scanned += 1
        lossless += decoded_lossless
        if archive is not None:
            archives += 1
            members += read_members
    for link in symlink_dirs:
        unevaluated.append("{} (symlinked directory; not followed, so the subtree behind it was not read)"
                           .format(_display(link.relative_to(root))))
    unevaluated.sort()
    return findings, scanned, lossless, archives, members, unevaluated


def run(root):
    try:
        findings, scanned, lossless, archives, members, unevaluated = scan_tree(root)
    except (OSError, UnicodeDecodeError) as exc:
        # An unreadable/corrupt denylist OR an unreadable directory/file is a read failure on a required
        # input, not a clean skip: fail closed (exit 2) so a leak gate never reports clean without having
        # read its denylist and scanned every subtree. UnicodeDecodeError covers a non-UTF-8 denylist.
        print("error: cannot read a required input (denylist or tree) ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    counts = ("{} file(s) scanned ({} non-UTF-8 text file(s) decoded losslessly), including {} ZIP archive(s) "
              "with {} member(s) scanned".format(scanned, lossless, archives, members))
    if unevaluated:
        # An input not read in full is never a skip: PASS would claim content the gate did not read, so the
        # run fails closed (exit 2), naming each such input and still reporting what the read content held.
        for item in unevaluated:
            print("CANNOT EVALUATE (not read in full): " + item, file=sys.stderr)
        print("error: {} input(s) could not be read in full, each named on a CANNOT EVALUATE line, so the "
              "tree cannot pass; {}; {} possible leak(s) in the content that was read; fail-closed"
              .format(len(unevaluated), counts, len(findings)), file=sys.stderr)
        for finding in sorted(set(findings)):
            print("  " + finding, file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} possible leak(s) of internal host specifics or codenames; {}".format(len(findings), counts))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: no internal host specifics or known codenames in the scanned working tree, every walked "
          "input read; " + counts)
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(Path(__file__).resolve().parents[1])


# --- self-test ----------------------------------------------------------------------------------------
# Fixture trees in a tempdir, each driving the real run() so the scan is never mocked. Every case runs
# on every runner: the two environment-dependent behaviours (a non-regular file, an unreadable file) are
# exercised through deterministic fault injection on the module seams _stat_mode/_read_bytes instead of
# a conditional skip, so a PASS is never printed while a guarded case did not run.
#   1. a clean tree passes (exit 0) and its PASS line states the scanned-file and ZIP archive counts,
#   2. a private IP in a .csv is a finding (exit 1); 2b. a private IP in a Latin-1 .tsv (a suffix on no
#      list) is decoded losslessly and found,
#   3. a host path in a Latin-1 .md is a finding, decoded losslessly; 3b. the same in a Latin-1
#      README.MD (an upper-case suffix) is found, not skipped; 3c. a host path in a UTF-16 file with a
#      byte-order mark is decoded as UTF-16 and found; 3d. a fake UTF-16 byte-order mark over
#      single-byte text hides nothing (the lossless byte view is scanned beside the wide views);
#      3e/3f. an odd-length or unpaired-surrogate BOM-declared UTF-16 stream fails closed (exit 2,
#      naming the file); 3g. a host path in a valid UTF-32 file with a byte-order mark is decoded as
#      UTF-32 and found; 3h. a malformed BOM-declared UTF-32 stream fails closed (exit 2, naming the
#      file); 3i. a valid UTF-16 stream whose first character is U+0000 (its first four bytes equal
#      the UTF-32 LE mark) is scanned as UTF-16, decided by a successful decode and never by the
#      prefix alone; 3j. a UTF-16 LE mark over big-endian text is found in the wide views (the
#      UTF-16-BE offset-0 and the odd-offset UTF-16-LE view both show it); 3k. a misaligned stream
#      behind a UTF-16 LE mark is found in the wide views (the odd-offset UTF-16-LE and the
#      UTF-16-BE offset-0 view both show it); 3l-3p. UTF-32 text behind a mismatched or misaligned
#      mark (a UTF-16 LE or BE mark over UTF-32 text, and a UTF-32 or UTF-16 mark over a stream
#      misaligned by one, two or three bytes) is found in a UTF-32 view at offsets 0 to 3; 3v. the
#      WIDE_VIEWS enumeration (each unit size at every byte phase) is asserted DIRECTLY, because
#      several arrangements match in more than one view and no single planted file discriminates
#      every entry, and the views read_scan_text RETURNS are asserted too (count, order and decoder
#      per view, against a planted BOM-marked stream: the constant assertion alone cannot catch a
#      reader that stops using an entry),
#   4. a codename in a Latin-1 .txt matches the hash layer (exit 1); 4b. the denylist file itself is
#      scanned (a private IP in one of its comment lines is a finding); 4c. a suffixless Latin-1 file is
#      decoded losslessly and its host path found,
#   5. a .zip that is not a readable archive is CANNOT EVALUATE (exit 2, naming it), never a skip;
#      5b. the suffix match is case-insensitive (a corrupt .ZIP whose content does not open with a ZIP
#      signature, so only the suffix makes it an archive, is the same CANNOT EVALUATE); 5c. a
#      private IP in a deflated member, and a host path in a Latin-1 member and in a UTF-16 member, are
#      each a finding named <archive>!<member>:<line> (exit 1: members go through the walked-file
#      decode); 5d. a clean archive passes (exit 0) and is counted as scanned with its members; 5e. a
#      ZIP under another name (its content opens with a ZIP signature) is unpacked too; 5f. a private IP
#      in the archive comment is found in the archive's own byte view; 5g/5h. each archive bound is
#      checked just inside, exactly at and just outside it (the member count, and the total
#      uncompressed size): inside and at pass, outside is CANNOT EVALUATE with no member read (a
#      member planted with a private IP yields no member finding); 5i-5l. an encrypted member, a member
#      compressed with an unsupported method, a nested archive member (by name, and by a ZIP signature
#      opening its content), a member that cannot be read (a CRC mismatch) and a member whose
#      byte-order mark fails its decode are each CANNOT EVALUATE naming <archive>!<member> (exit 2);
#      5m. a directory understating a deflated member's size (beside a CRC of the shortened text) is
#      CANNOT EVALUATE, not a PASS over the text past the declared size; 5n. a member whose declared
#      compressed bytes carry a second deflate stream after the first is CANNOT EVALUATE naming
#      <archive>!<member>; 5o. a deflated local entry absent from the directory ahead of a clean
#      archive's members, a second archive after the first in one file, a gap between members,
#      trailing bytes after the end record, two directory records naming one local entry and an end
#      record whose declared comment runs past the file are each CANNOT EVALUATE naming the archive and
#      the offsets, with no member read; 5p. records that tile pass (data descriptors written by zipfile
#      to a stream it cannot seek, one with ZIP64 sizes, and one without its optional signature; local
#      extra fields the directory lacks; a ZIP64 end record and locator); 5q. a deflate stream that
#      never ends, stored bytes past the declared size, and a stored and a deflated member whose
#      directory overstates the size are each CANNOT EVALUATE naming <archive>!<member>; 5r. _member_data_matches refuses compressed bytes
#      declared past the end of the data; 5s. the archive-size bound is checked just inside, exactly at
#      and just outside it on the stat probe's size, outside CANNOT EVALUATE before the bytes are read
#      (under a .zip name and by signature alone), and a text file over it is still read and scanned,
#   6. a non-regular file (injected FIFO mode) is CANNOT EVALUATE (exit 2, naming it), its content
#      NEVER read (the read seam raises for it, so a read-before-classification regression shows as a
#      read error, not the CANNOT EVALUATE line); 6b. a symlink to a regular file is followed and its
#      target scanned (exit 1); 6c. a symlinked directory is CANNOT EVALUATE (exit 2, naming it), never
#      silently dropped; 6d. a dangling symlink fails closed (exit 2),
#   7. an unreadable file (injected PermissionError) fails closed (exit 2).
# Cases 1 through 6 each fail on the original scan. Against the first revision this replaces,
# 2b, 3b, 3c and 6c are new findings or named skips, 6 and 7 run where that revision skipped, and 4b,
# 4c, 5b, 6b and 6d pin behaviours a mutant could drop (the denylist walk, the suffixless decode, the
# case-insensitive allow-list, the stat-follows-a-file-symlink rule, the dangling-symlink error).
# Against the second revision this replaces, 3d and 3g are new findings and 3e, 3f and 3h pin the
# fail-closed malformed-BOM contract, while the raising read seam in 6 makes a read-before-SKIP
# regression fail rather than pass. Against the third revision this replaces, 3i pins the
# decode-decided (never prefix-decided) BOM classification and 3j and 3k pin the every-wide-view
# scan (a mismatched-endian and a misaligned mark), while 3e, 3f and 3h now also assert the file is
# named. Against the fourth revision this replaces, 3l-3o pin the UTF-32 views at byte offsets 0 to
# 3 (each of those four planted texts hid from every then-scanned view; 3p was already found by that
# revision and stays as the phase-3 control) and 3v asserts the WIDE_VIEWS enumeration itself and the
# views read_scan_text returns. Against the gate that skipped every undecodable .zip, a non-regular file
# and a symlinked directory with a PASS (exit 0), 1, 5, 5b, 6 and 6c now assert the counts or the exit 2
# that replaced each skip, and 5c-5m are new; each of these fails on that gate. Against the gate that
# accepted a member whose first deflate stream matched and never accounted for the archive's bytes,
# 5n, each 5o vector, the two overstated-size 5q members and 5s fail: all but 5s passed there (exit 0),
# 5n and the first four 5o vectors over a private IP held in deflated bytes no member stream read, and
# 5s's archive was read in full. 5p passes on both and guards the span arithmetic, the other 5q members
# and 5r pin conditions that gate already held, and 5b now fails when the suffix match is made
# case-sensitive. The planted host path and private IP are assembled from
# pieces so this source stays clean under its own scan.
_HOST_PATH = "/" + "opt/" + "fixture-host/private/path"
_PRIVATE_IP = "10." + "20.30.40"


def self_test_main():
    import io
    import os
    import shutil
    import struct
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def run_capture(root):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = run(root)
        return rc, out.getvalue() + err.getvalue()

    def tree(name, files):
        base = tmp / name
        for rel, data in files.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return base

    def zip_blob(members, method=zipfile.ZIP_DEFLATED, comment=b""):
        """A ZIP archive built in memory, with a fixed timestamp so its bytes are reproducible."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in members:
                archive.writestr(zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0)), data, compress_type=method)
            archive.comment = comment
        return buffer.getvalue()

    def deflate(data, final=True):
        """A raw deflate stream of data; final=False ends it with a sync flush instead of a final block, so
        the stream never reaches its end."""
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        return packer.compress(data) + packer.flush(zlib.Z_FINISH if final else zlib.Z_SYNC_FLUSH)

    def entry(name, content, method=zipfile.ZIP_DEFLATED, stored=None, size=None, before=b"", extra=b"",
              listed=1, descriptor=b""):
        """One raw_zip member. Its data defaults to content as the method writes it, and its declared size
        and CRC to content's; before is placed ahead of its local header, extra is its local header's extra
        field (the directory's stays empty), listed is how many directory records name it (0 leaves it out),
        and a non-empty descriptor follows its data and sets flag bit 3."""
        if stored is None:
            stored = deflate(content) if method == zipfile.ZIP_DEFLATED else content
        return (name.encode("ascii"), method, stored, zlib.crc32(content),
                len(content) if size is None else size, before, extra, listed, descriptor)

    def raw_zip(entries, zip64=False):
        """A ZIP archive assembled record by record, for layouts zipfile's writer never produces. Offsets
        count every byte written, so the directory points at each listed member's local header; zip64=True
        puts a ZIP64 end record and its locator ahead of the end record."""
        out, directory, count = bytearray(), bytearray(), 0
        for name, method, stored, crc, size, before, extra, listed, descriptor in entries:
            out += before
            offset, flags = len(out), 0x08 if descriptor else 0
            out += struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, flags, method, 0, 0x21, crc, len(stored), size,
                               len(name), len(extra)) + name + extra + stored + descriptor
            directory += listed * (struct.pack("<4s6H3L5H2L", b"PK\x01\x02", 20, 20, flags, method, 0, 0x21, crc,
                                               len(stored), size, len(name), 0, 0, 0, 0, 0, offset) + name)
            count += listed
        start = len(out)
        out += directory
        if zip64:
            record = len(out)
            out += struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, len(directory), start)
            out += struct.pack("<4sLQL", b"PK\x06\x07", 0, record, 1)
        out += struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, count, count, len(directory), start, 0)
        return bytes(out)

    class WriteOnly:
        """A stream with no tell or seek, so zipfile's writer sets flag bit 3 on each member and follows
        its data with a data descriptor."""

        def __init__(self):
            self.data = bytearray()

        def write(self, chunk):
            self.data += chunk
            return len(chunk)

        def flush(self):
            pass

    def patch_directory(blob, index, offset, fmt, value):
        """Rewrite one field of the index-th central directory record, at its offset from the record's
        start: the flag word (8), the compression method (10), the CRC (16) or the uncompressed size (24)."""
        end = blob.rindex(b"PK\x05\x06")
        pos = struct.unpack("<I", blob[end + 16:end + 20])[0]
        for _ in range(index):
            pos += 46 + sum(struct.unpack("<HHH", blob[pos + 28:pos + 34]))
        return blob[:pos + offset] + struct.pack(fmt, value) + blob[pos + offset + struct.calcsize(fmt):]

    def expect_cannot_evaluate(label, what, rc, out, name, reason):
        """Exit 2 with a CANNOT EVALUATE line naming `name` and a reason starting `reason`, never a skip
        or a PASS."""
        line = "CANNOT EVALUATE (not read in full): {} ({}".format(name, reason)
        if rc != 2 or line not in out or "SKIP" in out or "PASS" in out:
            failures.append("{} expected exit 2 with a CANNOT EVALUATE line naming {} ({}...), never a "
                            "skip or a PASS (case {}; got {}: {!r})".format(what, name, reason, label, rc, out))

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-leaks-gate-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    clean_files = {"docs/a.md": b"# Plain\n\nNothing private here.\n", "site/data.csv": b"id,name\n1,alpha\n"}
    failures = []
    try:
        # 1. a clean tree passes and states its counts, every walked input read.
        rc, out = run_capture(tree("clean", clean_files))
        if (rc != 0 or "2 file(s) scanned (0 non-UTF-8" not in out
                or "including 0 ZIP archive(s) with 0 member(s) scanned" not in out
                or "every walked input read" not in out):
            failures.append("a clean tree expected exit 0 with 2 files scanned, 0 ZIP archives, every "
                            "walked input read (case 1; got {}: {!r})".format(rc, out))

        # 2. a private IP in a UTF-8 .csv is a finding (scanned whatever the suffix).
        rc, out = run_capture(tree("csv", dict(clean_files, **{
            "site/downloads/plant.csv": ("id,host\n1," + _PRIVATE_IP + "\n").encode("ascii")})))
        if rc != 1 or "plant.csv:2: private IP (10/8)" not in out:
            failures.append("a private IP in a .csv expected exit 1 naming plant.csv:2 (got {}: {!r})"
                            .format(rc, out))

        # 2b. a private IP in a Latin-1 .tsv (a suffix on no list) is decoded losslessly and found.
        files = dict(clean_files)
        files["data/plant.tsv"] = b"id\thost\n1\t" + _PRIVATE_IP.encode("ascii") + b"\n\xe9\n"
        rc, out = run_capture(tree("tsv", files))
        if (rc != 1 or "plant.tsv:2: private IP (10/8)" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a private IP in a Latin-1 .tsv expected exit 1, decoded losslessly "
                            "(got {}: {!r})".format(rc, out))

        # 3. a host path in a .md holding a Latin-1 byte is a finding, decoded losslessly.
        rc, out = run_capture(tree("latin1", dict(clean_files, **{
            "docs/latin1.md": b"# caf\xe9\n\nsee " + _HOST_PATH.encode("ascii") + b"\n"})))
        if (rc != 1 or "latin1.md:3: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a Latin-1 .md expected exit 1 naming latin1.md:3, decoded "
                            "losslessly (got {}: {!r})".format(rc, out))

        # 3b. an upper-case suffix is no silent skip: a host path in a Latin-1 README.MD is found.
        files = dict(clean_files)
        files["docs/README.MD"] = b"caf\xe9 see " + _HOST_PATH.encode("ascii") + b"\n"
        rc, out = run_capture(tree("upper-md", files))
        if rc != 1 or "docs/README.MD:1: absolute host path" not in out:
            failures.append("a host path in a Latin-1 README.MD expected exit 1 "
                            "(got {}: {!r})".format(rc, out))

        # 3c. a UTF-16 file with a byte-order mark is decoded as UTF-16 and scanned: a host path in it
        #     is found, and the file counts as a lossless decode.
        files = dict(clean_files)
        files["docs/wide.md"] = ("# note\nsee " + _HOST_PATH + "\n").encode("utf-16")
        rc, out = run_capture(tree("utf16", files))
        if (rc != 1 or "docs/wide.md:2: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a UTF-16 .md with a BOM expected exit 1, decoded as UTF-16 "
                            "(got {}: {!r})".format(rc, out))

        # 3d. a fake UTF-16 byte-order mark over single-byte text cannot hide it: both the wide view
        #     and the lossless byte view are scanned, so the host path behind the mark is found.
        files = dict(clean_files)
        files["docs/fake.md"] = b"\xff\xfe" + ("see " + _HOST_PATH + " \n").encode("ascii")
        rc, out = run_capture(tree("fake-bom", files))
        if (rc != 1 or "docs/fake.md:1: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path behind a fake UTF-16 BOM expected exit 1 via the byte view "
                            "(got {}: {!r})".format(rc, out))

        # 3e/3f. a BOM-declared UTF-16 stream that does not decode as UTF-16 (an odd length; an
        #     unpaired surrogate) fails closed (exit 2) naming the file, never an ineffective clean
        #     scan.
        files = dict(clean_files)
        files["docs/odd.md"] = ("see " + _HOST_PATH + "\n").encode("utf-16") + b"x"
        rc, out = run_capture(tree("utf16-odd", files))
        if rc != 2 or "odd.md" not in out:
            failures.append("an odd-length BOM-declared UTF-16 stream expected exit 2 naming odd.md, "
                            "fail-closed (got {}: {!r})".format(rc, out))
        files = dict(clean_files)
        files["docs/surrogate.md"] = ("see " + _HOST_PATH + "\n").encode("utf-16") + b"\x00\xd8"
        rc, out = run_capture(tree("utf16-surrogate", files))
        if rc != 2 or "surrogate.md" not in out:
            failures.append("an unpaired-surrogate BOM-declared UTF-16 stream expected exit 2 naming "
                            "surrogate.md, fail-closed (got {}: {!r})".format(rc, out))

        # 3g. a UTF-32 byte-order mark is honoured (probed before the UTF-16 prefix it embeds): a host
        #     path in a valid UTF-32 file is found in the wide view and counted as a lossless decode.
        files = dict(clean_files)
        files["docs/wide32.md"] = ("# note\nsee " + _HOST_PATH + "\n").encode("utf-32")
        rc, out = run_capture(tree("utf32", files))
        if (rc != 1 or "docs/wide32.md:2: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a UTF-32 .md with a BOM expected exit 1, decoded as UTF-32 "
                            "(got {}: {!r})".format(rc, out))

        # 3h. a BOM-declared UTF-32 stream that does not decode as UTF-32 fails closed (exit 2).
        files = dict(clean_files)
        files["docs/bad32.md"] = b"\xff\xfe\x00\x00" + b"\x01"
        rc, out = run_capture(tree("utf32-bad", files))
        if rc != 2 or "bad32.md" not in out:
            failures.append("a malformed BOM-declared UTF-32 stream expected exit 2 naming bad32.md, "
                            "fail-closed (got {}: {!r})".format(rc, out))

        # 3i. a valid UTF-16 stream whose FIRST character is U+0000 carries the 4-byte UTF-32 LE mark
        #     as its prefix: the declared encoding is decided by a successful decode (UTF-32 fails,
        #     the UTF-16 fallback decodes), so the host path is found, never refused as a misdeclared
        #     UTF-32 stream.
        files = dict(clean_files)
        files["docs/nulfirst.md"] = ("\x00see " + _HOST_PATH + "\n").encode("utf-16")
        rc, out = run_capture(tree("utf16-nul-first", files))
        if (rc != 1 or "docs/nulfirst.md:1: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a U+0000-leading UTF-16 stream expected exit 1, decided by decode and "
                            "scanned as UTF-16 (got {}: {!r})".format(rc, out))

        # 3j. a UTF-16 LE mark over BIG-ENDIAN text: the declared LE decode succeeds but garbles the
        #     text and the byte view interleaves NULs; the host path shows in the UTF-16-BE offset-0
        #     view AND in the odd-offset UTF-16-LE view (two views, not one, so this vector alone
        #     discriminates neither: the required view set is asserted directly by case 3v).
        files = dict(clean_files)
        files["docs/bemasked.md"] = b"\xff\xfe" + ("see " + _HOST_PATH + "\n").encode("utf-16-be")
        rc, out = run_capture(tree("utf16-be-masked", files))
        if (rc != 1 or "docs/bemasked.md:1: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in big-endian text behind a UTF-16 LE mark expected exit 1 "
                            "via the UTF-16-BE view (got {}: {!r})".format(rc, out))

        # 3k. a UTF-16 LE mark over a MISALIGNED LE stream (one byte inserted after the mark, one
        #     appended to keep the length even): the declared decode succeeds garbled; the host path
        #     shows in the odd-offset UTF-16-LE view AND in the UTF-16-BE offset-0 view (two views,
        #     not one, so this vector alone discriminates neither: case 3v asserts the view set).
        files = dict(clean_files)
        files["docs/shifted.md"] = (b"\xff\xfe" + b"a"
                                    + ("see " + _HOST_PATH + "\n").encode("utf-16-le") + b"\x00")
        rc, out = run_capture(tree("utf16-shifted", files))
        if (rc != 1 or "docs/shifted.md:1: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a misaligned stream behind a UTF-16 LE mark expected "
                            "exit 1 via the odd-offset view (got {}: {!r})".format(rc, out))

        # 3l-3p. UTF-32 text behind a mismatched or misaligned mark: a UTF-16 LE mark over UTF-32 LE
        #     text (3l; the stream sits at byte offset 2) and a UTF-16 BE mark over UTF-32 BE text
        #     (3m) -- each declared decode succeeds as garbled UTF-16 -- and a UTF-32 LE mark with
        #     one byte (3n; padded so the declared UTF-32 decode still succeeds garbled) or two bytes
        #     (3o; its whole-stream UTF-32 decode fails on an out-of-range unit, so the declared decode
        #     is the UTF-16 fallback, succeeding garbled) inserted after it, and a UTF-16 LE mark with
        #     one byte inserted over a UTF-32 LE stream (3p; the stream sits at byte offset 3). Each
        #     host path is visible only in
        #     UTF-32 views; 3l-3o hid from every view the previous revision scanned (each passed
        #     clean before the offset-1..3 views existed), while 3p is the phase-3 control (an LE
        #     stream at offset 3 also reads in the UTF-32-BE offset-0 view, so no planted file
        #     discriminates a single view: case 3v asserts the enumeration).
        for label, name, blob in (
                ("3l", "docs/u32le-off2.md",
                 b"\xff\xfe" + ("see " + _HOST_PATH + "\n").encode("utf-32-le")),
                ("3m", "docs/u32be-off2.md",
                 b"\xfe\xff" + ("see " + _HOST_PATH + "\n").encode("utf-32-be")),
                ("3n", "docs/u32le-off1.md",
                 b"\xff\xfe\x00\x00" + b"a" + ("see " + _HOST_PATH + "\n").encode("utf-32-le")
                 + b"\x00\x00\x00"),
                ("3o", "docs/u32le-off2b.md",
                 b"\xff\xfe\x00\x00" + b"ab" + ("see " + _HOST_PATH + "\n").encode("utf-32-le")
                 + b"\x00\x00"),
                ("3p", "docs/u32le-off3.md",
                 b"\xff\xfe" + b"a" + ("see " + _HOST_PATH + "\n").encode("utf-32-le")
                 + b"\x00")):
            files = dict(clean_files)
            files[name] = blob
            rc, out = run_capture(tree("u32-" + label, files))
            if (rc != 1 or "{}:1: absolute host path".format(name) not in out
                    or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
                failures.append("a host path in UTF-32 text behind a mismatched or misaligned mark "
                                "({}) expected exit 1 naming {} via a UTF-32 view "
                                "(got {}: {!r})".format(label, name, rc, out))

        # 3v. the required view enumeration is asserted DIRECTLY: several arrangements (3j and 3k
        #     included) match in more than one view, so no single planted file can discriminate
        #     every entry; dropping any view fails here even where the planted files still match
        #     elsewhere.
        expected_views = (("utf-16-le", 0), ("utf-16-le", 1), ("utf-16-be", 0), ("utf-16-be", 1),
                          ("utf-32-le", 0), ("utf-32-le", 1), ("utf-32-le", 2), ("utf-32-le", 3),
                          ("utf-32-be", 0), ("utf-32-be", 1), ("utf-32-be", 2), ("utf-32-be", 3))
        if sys.modules[__name__].WIDE_VIEWS != expected_views:
            failures.append("WIDE_VIEWS must enumerate UTF-16 LE/BE at byte offsets 0-1 and UTF-32 "
                            "LE/BE at byte offsets 0-3 (got {!r})".format(
                                sys.modules[__name__].WIDE_VIEWS))
        #     The constant assertion alone cannot catch a reader that stops USING an entry (a loop
        #     over WIDE_VIEWS[1:] leaves the constant intact), so the views read_scan_text actually
        #     RETURNS are asserted too: a planted BOM-marked stream is read through the real
        #     function, and the returned views are compared by position against the decodes the
        #     enumeration requires (count, order and decoder per view; the declared decode and the
        #     lossless byte view lead; the probe bytes make every expected view distinct, so a
        #     swapped, dropped or re-decoded view cannot compare equal).
        probe_dir = tmp / "views-probe"
        probe_dir.mkdir()
        probe_path = probe_dir / "probe.md"
        probe_bytes = b"\xff\xfe" + bytes([  # a UTF-16-LE, a UTF-16-BE, a UTF-32-LE and a
            0x41, 0, 0x42, 0, 0x43, 0, 0x44, 0,    # UTF-32-BE quadrant, so all fourteen expected
            0, 0x45, 0, 0x46, 0, 0x47, 0, 0x48,    # views decode to distinct texts and a swapped
            0x49, 0, 0, 0, 0x4a, 0, 0, 0,          # view can never compare equal
            0, 0, 0, 0x4b, 0, 0, 0, 0x4c])
        probe_path.write_bytes(probe_bytes)
        probe_texts, probe_lossless, probe_archive = read_scan_text(probe_path)
        want_views = [probe_bytes.decode("utf-16"),
                      probe_bytes.decode("utf-8", errors="surrogateescape")]
        for enc, off in expected_views:
            want_views.append(probe_bytes[off:].decode(enc, errors="replace"))
        if (probe_archive is not None or probe_lossless is not True
                or not isinstance(probe_texts, tuple) or list(probe_texts) != want_views):
            failures.append("read_scan_text must return the declared decode, the lossless byte "
                            "view, then every WIDE_VIEWS decode in order ({} view(s) returned; "
                            "views equal by position: {!r})".format(
                                0 if probe_texts is None else len(probe_texts),
                                None if probe_texts is None else
                                [t == w for t, w in zip(probe_texts, want_views)]))

        # 4. a codename in a .txt holding a Latin-1 byte matches the hash layer.
        rc, out = run_capture(tree("codename", dict(clean_files, **{
            "tools/leak-hashes.txt": (term_hash("fixture codename") + "\n").encode("ascii"),
            "notes/latin1.txt": b"caf\xe9 FixtureCodename\n"})))
        if rc != 1 or "notes/latin1.txt: internal codename (hash match)" not in out:
            failures.append("a codename in a Latin-1 .txt expected exit 1 by hash match (got {}: {!r})"
                            .format(rc, out))

        # 4b. the denylist file itself is walked and scanned like any other file: a private IP in a
        #     comment line of tools/leak-hashes.txt is a finding.
        files = dict(clean_files)
        files["tools/leak-hashes.txt"] = ("# near " + _PRIVATE_IP + "\n").encode("ascii")
        rc, out = run_capture(tree("denylist-scanned", files))
        if rc != 1 or "tools/leak-hashes.txt:1: private IP (10/8)" not in out:
            failures.append("a private IP inside leak-hashes.txt expected exit 1 "
                            "(got {}: {!r})".format(rc, out))

        # 4c. a suffixless file holding a Latin-1 byte is decoded losslessly: a host path in it is found.
        files = dict(clean_files)
        files["LICENSE"] = b"caf\xe9 at " + _HOST_PATH.encode("ascii") + b"\n"
        rc, out = run_capture(tree("suffixless", files))
        if rc != 1 or "LICENSE:1: absolute host path" not in out:
            failures.append("a host path in a Latin-1 suffixless file expected exit 1 "
                            "(got {}: {!r})".format(rc, out))

        # 5/5b. a .zip that is not a readable archive is CANNOT EVALUATE, never a skip, whatever the
        #     suffix's case. 5b's content does not open with a ZIP signature, so only the case-insensitive
        #     suffix match makes it an archive (a case-sensitive match would scan it as text and pass).
        for label, name, blob in (("5", "site/downloads/pack.zip", b"PK\x03\x04\xff\xfe\x00binary"),
                                  ("5b", "site/downloads/pack2.ZIP", b"not an archive\xff\xfe\x00binary")):
            files = dict(clean_files)
            files[name] = blob
            rc, out = run_capture(tree("zip-corrupt-" + label, files))
            expect_cannot_evaluate(label, "a corrupt archive", rc, out, name, "not a readable ZIP archive")

        # 5c. members are unpacked and decoded like walked files: a private IP in a deflated member, a
        #     host path in a Latin-1 member and one in a UTF-16 member are each named <archive>!<member>.
        files = dict(clean_files)
        files["site/downloads/plant.zip"] = zip_blob((
            ("docs/plant.txt", ("id\n1," + _PRIVATE_IP + "\n").encode("ascii")),
            ("docs/latin1.md", b"caf\xe9 see " + _HOST_PATH.encode("ascii") + b"\n"),
            ("docs/wide.md", ("see " + _HOST_PATH + "\n").encode("utf-16"))))
        rc, out = run_capture(tree("zip-plant", files))
        wanted = ("site/downloads/plant.zip!docs/plant.txt:2: private IP (10/8)",
                  "site/downloads/plant.zip!docs/latin1.md:1: absolute host path",
                  "site/downloads/plant.zip!docs/wide.md:1: absolute host path",
                  "including 1 ZIP archive(s) with 3 member(s) scanned")
        if rc != 1 or any(want not in out for want in wanted):
            failures.append("a private IP and host paths in deflated, Latin-1 and UTF-16 archive members "
                            "expected exit 1, each named <archive>!<member>:<line> (case 5c; got {}: {!r})"
                            .format(rc, out))

        # 5d. a clean archive passes and is counted as scanned, with its members.
        files = dict(clean_files)
        files["site/downloads/clean.zip"] = zip_blob((("SKILL.md", b"# Skill\n\nNothing private.\n"),
                                                      ("LICENSE", b"Plain licence text.\n")))
        rc, out = run_capture(tree("zip-clean", files))
        if (rc != 0 or "3 file(s) scanned" not in out
                or "including 1 ZIP archive(s) with 2 member(s) scanned" not in out):
            failures.append("a clean archive expected exit 0, counted as scanned with its 2 members "
                            "(case 5d; got {}: {!r})".format(rc, out))

        # 5e. a ZIP under another name is recognized by its signature and unpacked.
        files = dict(clean_files)
        files["site/downloads/pack.bin"] = zip_blob((("plant.txt", ("ip " + _PRIVATE_IP + "\n").encode("ascii")),))
        rc, out = run_capture(tree("zip-signature", files))
        if rc != 1 or "site/downloads/pack.bin!plant.txt:1: private IP (10/8)" not in out:
            failures.append("a ZIP named pack.bin expected exit 1 with its member finding (case 5e; got "
                            "{}: {!r})".format(rc, out))

        # 5f. the archive's own bytes are scanned too: a private IP in its comment is a finding.
        files = dict(clean_files)
        files["site/downloads/comment.zip"] = zip_blob((("a.txt", b"plain\n"),),
                                                       comment=("note " + _PRIVATE_IP).encode("ascii"))
        rc, out = run_capture(tree("zip-comment", files))
        if (rc != 1 or not re.search(r"site/downloads/comment\.zip:\d+: private IP \(10/8\)", out)
                or "including 1 ZIP archive(s) with 1 member(s) scanned" not in out):
            failures.append("a private IP in an archive comment expected exit 1 from the archive's byte "
                            "view (case 5f; got {}: {!r})".format(rc, out))

        # 5g. the member-count bound, just inside, exactly at and just outside it. Outside, the first
        #     member carries a private IP: no member finding proves the bound was checked from the
        #     directory before any member was read.
        limit = MAX_ARCHIVE_MEMBERS
        for label, count in (("5g-inside", limit - 1), ("5g-at", limit), ("5g-outside", limit + 1)):
            first = ("ip " + _PRIVATE_IP + "\n").encode("ascii") if count > limit else b"x\n"
            members = [("m0.txt", first)] + [("m{}.txt".format(i), b"x\n") for i in range(1, count)]
            files = dict(clean_files)
            files["site/downloads/many.zip"] = zip_blob(members)
            rc, out = run_capture(tree("zip-" + label, files))
            if count > limit:
                expect_cannot_evaluate(label, "an archive over the member-count bound", rc, out,
                                       "site/downloads/many.zip",
                                       "{} members, over MAX_ARCHIVE_MEMBERS".format(count))
                if "many.zip!" in out:
                    failures.append("an archive over the member-count bound had a member read (case {}; "
                                    "got {}: {!r})".format(label, rc, out))
            elif rc != 0 or "with {} member(s) scanned".format(count) not in out:
                failures.append("an archive of {} members (bound {}) expected exit 0 with every member "
                                "scanned (case {}; got {}: {!r})".format(count, limit, label, rc, out))

        # 5h. the total-uncompressed-size bound, just inside, exactly at and just outside it: the second
        #     member is NUL bytes (one line, deflated to about 16 KiB) sized so the members' total lands on
        #     each value. Outside, the first member carries a private IP: no <archive>!<member> finding
        #     proves no member was read.
        limit = MAX_ARCHIVE_TOTAL_SIZE
        for label, total in (("5h-inside", limit - 1), ("5h-at", limit), ("5h-outside", limit + 1)):
            first = ("ip " + _PRIVATE_IP + "\n").encode("ascii") if total > limit else b"plain\n"
            files = dict(clean_files)
            files["site/downloads/big.zip"] = zip_blob((("a.txt", first),
                                                        ("b.txt", bytes(total - len(first)))))
            rc, out = run_capture(tree("zip-" + label, files))
            if total > limit:
                expect_cannot_evaluate(label, "an archive over the total-size bound", rc, out,
                                       "site/downloads/big.zip",
                                       "{} uncompressed bytes declared, over MAX_ARCHIVE_TOTAL_SIZE"
                                       .format(total))
                if "big.zip!" in out:
                    failures.append("an archive over the total-size bound had a member read (case {}; "
                                    "got {}: {!r})".format(label, rc, out))
            elif rc != 0 or "with 2 member(s) scanned" not in out:
                failures.append("an archive declaring {} uncompressed bytes (bound {}) expected exit 0 "
                                "with both members scanned (case {}; got {}: {!r})"
                                .format(total, limit, label, rc, out))

        # 5i-5l. an archive or member the gate cannot read in full is CANNOT EVALUATE naming
        #     <archive>!<member>: an encrypted member (5i), an unsupported compression method (5j, bzip2),
        #     a nested archive by name (5k; its content is not an archive, so only the name decides, and
        #     the member before it carries a private IP that no finding may show, since the directory is
        #     checked before any member is read) and by content (5k), a member failing its CRC (5l; one
        #     stored byte flipped) and a member whose byte-order mark fails its decode (5l).
        plain = zip_blob((("a.txt", b"plain text\n"),), method=zipfile.ZIP_STORED)
        flipped = 30 + len("a.txt")
        planted = ("ip " + _PRIVATE_IP + "\n").encode("ascii")
        for label, name, blob, member, reason in (
                ("5i", "locked.zip", patch_directory(plain, 0, 8, "<H", 0x0001), "a.txt",
                 "encrypted member"),
                ("5j", "bzip.zip", patch_directory(plain, 0, 10, "<H", 12), "a.txt",
                 "compression method 12 is neither stored nor deflated"),
                ("5k", "outer.zip", zip_blob((("a.txt", planted), ("inner.zip", b"not an archive\n"))),
                 "inner.zip", "nested archive member, not unpacked; no member was read"),
                ("5k", "outer2.zip", zip_blob((("a.txt", b"x\n"), ("inner.bin", zip_blob((("x.txt", b"x\n"),))))),
                 "inner.bin", "nested archive member (its content opens with a ZIP signature)"),
                ("5l", "crc.zip", plain[:flipped] + bytes([plain[flipped] ^ 0x01]) + plain[flipped + 1:],
                 "a.txt", "member cannot be read: BadZipFile"),
                ("5l", "bom.zip", zip_blob((("odd.md", ("see x\n").encode("utf-16") + b"x"),)), "odd.md",
                 "member does not decode")):
            files = dict(clean_files)
            files["site/downloads/" + name] = blob
            rc, out = run_capture(tree("zip-{}-{}".format(label, name), files))
            expect_cannot_evaluate(label, "an archive the gate cannot read in full", rc, out,
                                   "site/downloads/{}!{}".format(name, member), reason)
            if "site/downloads/{}!a.txt:".format(name) in out:
                failures.append("a member was read before the directory checks refused {} (case {}; got "
                                "{}: {!r})".format(name, label, rc, out))

        # 5m. a directory that understates a deflated member's size, beside a CRC of the shortened text,
        #     cannot hide the rest of the member: zipfile alone stops at the declared size and the CRC
        #     agrees, so the member's data is re-inflated and must end there, or it is CANNOT EVALUATE
        #     (here the private IP past the declared size would otherwise pass unread).
        hidden = ("note\nip " + _PRIVATE_IP + "\n").encode("ascii")
        blob = zip_blob((("a.txt", hidden),))
        blob = patch_directory(patch_directory(blob, 0, 16, "<I", zlib.crc32(hidden[:5])), 0, 24, "<I", 5)
        files = dict(clean_files)
        files["site/downloads/short.zip"] = blob
        rc, out = run_capture(tree("zip-understated", files))
        expect_cannot_evaluate("5m", "a member whose directory understates its size", rc, out,
                               "site/downloads/short.zip!a.txt",
                               "member data does not match its declared size")

        # 5n. a member whose declared compressed bytes carry a second deflate stream after the first:
        #     zipfile stops at the first stream's end, whose size and CRC the directory declares, so the
        #     private IP in the second would pass unread; the member must be one complete stream.
        secret = ("ip " + _PRIVATE_IP + "\n").encode("ascii")
        files = dict(clean_files)
        files["site/downloads/twostream.zip"] = raw_zip([entry("a.txt", b"note\n",
                                                               stored=deflate(b"note\n") + deflate(secret))])
        rc, out = run_capture(tree("zip-two-streams", files))
        expect_cannot_evaluate("5n", "a member holding a second deflate stream", rc, out,
                               "site/downloads/twostream.zip!a.txt", "member data does not match")

        # 5o. bytes the parsed records do not tile are CANNOT EVALUATE naming the archive and the offsets,
        #     and no member is read: a deflated local entry ahead of a clean archive's members but absent
        #     from its directory, a ZIP holding the private IP followed by a clean ZIP in one file, a gap
        #     between two members, and trailing bytes after the end record each hold the private IP only
        #     in deflated bytes no member stream reads, so the archive's own byte view does not show it;
        #     two directory records naming one local entry overlap, and an end record declaring a longer
        #     comment than the file holds runs past its last byte.
        clean_pack = zip_blob((("SKILL.md", b"# Skill\n"), ("LICENSE", b"Plain licence text.\n")))
        secret_pack = zip_blob((("a.txt", secret),))
        hidden = len(deflate(secret))
        first = 30 + len("a.txt") + len(deflate(b"plain\n"))
        uncovered = "{} byte(s) at offsets {} to {} belong to no record"
        for label, name, blob, problem in (
                ("5o-unlisted", "unlisted.zip",
                 raw_zip([entry("hidden.txt", secret, listed=0), entry("SKILL.md", b"# Skill\n")]),
                 uncovered.format(40 + hidden, 0, 39 + hidden)),
                ("5o-second", "second.zip", secret_pack + clean_pack,
                 uncovered.format(len(secret_pack), 0, len(secret_pack) - 1)),
                ("5o-gap", "gap.zip",
                 raw_zip([entry("a.txt", b"plain\n"), entry("b.txt", b"plain\n", before=deflate(secret))]),
                 uncovered.format(hidden, first, first + hidden - 1)),
                ("5o-trailing", "trailing.zip", clean_pack + deflate(secret),
                 uncovered.format(hidden, len(clean_pack), len(clean_pack) + hidden - 1)),
                ("5o-overlap", "overlap.zip", raw_zip([entry("a.txt", b"plain\n", listed=2)]),
                 "the local header of a.txt at offsets 0 to 34 overlaps the record before it"),
                ("5o-past-end", "pastend.zip", clean_pack[:-2] + b"\x05\x00",
                 "the end-of-central-directory record and comment at offsets {} to {} runs past the "
                 "archive's last byte (offset {})".format(len(clean_pack) - 22, len(clean_pack) + 4,
                                                          len(clean_pack) - 1))):
            files = dict(clean_files)
            files["site/downloads/" + name] = blob
            rc, out = run_capture(tree("zip-" + label, files))
            expect_cannot_evaluate(label, "an archive whose records do not tile its bytes", rc, out,
                                   "site/downloads/" + name,
                                   "the parsed records do not cover the archive's bytes exactly: " + problem)
            if "site/downloads/{}!".format(name) in out:
                failures.append("a member was read from an archive whose records do not tile its bytes "
                                "(case {}; got {}: {!r})".format(label, rc, out))

        # 5p. layouts whose records do tile pass: members written by zipfile to a stream it cannot seek
        #     (flag bit 3, each member's data followed by a data descriptor with its signature; the third
        #     written with force_zip64, so a ZIP64 extra field in its local header and 8-byte sizes in its
        #     descriptor), a data descriptor without its optional signature, members whose local headers
        #     carry an extra field the directory does not (their data begins past that field), and a ZIP64
        #     end record and locator ahead of the end record.
        stream = WriteOnly()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(zipfile.ZipInfo("SKILL.md", (2020, 1, 1, 0, 0, 0)), b"# Skill\n",
                             compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr(zipfile.ZipInfo("LICENSE", (2020, 1, 1, 0, 0, 0)), b"Plain licence text.\n",
                             compress_type=zipfile.ZIP_STORED)
            with archive.open(zipfile.ZipInfo("wide.txt", (2020, 1, 1, 0, 0, 0)), "w", force_zip64=True) as handle:
                handle.write(b"plain\n")
        with zipfile.ZipFile(io.BytesIO(bytes(stream.data))) as archive:
            if not all(info.flag_bits & 0x08 for info in archive.infolist()):
                failures.append("the data-descriptor fixture has a member without flag bit 3 (case 5p)")
        stamp = b"UT\x05\x00\x01\x00\x00\x00\x00"  # an extended-timestamp extra field, 9 bytes
        for label, name, blob, count in (
                ("5p-descriptor", "descriptor.zip", bytes(stream.data), 3),
                ("5p-unsigned-descriptor", "unsigned.zip",
                 raw_zip([entry("a.txt", b"plain\n", descriptor=struct.pack(
                     "<3L", zlib.crc32(b"plain\n"), len(deflate(b"plain\n")), 6))]), 1),
                ("5p-extra", "extra.zip", raw_zip([entry("a.txt", b"plain\n", extra=stamp),
                                                   entry("b.txt", b"plain\n", zipfile.ZIP_STORED, extra=stamp)]), 2),
                ("5p-zip64", "zip64.zip", raw_zip([entry("a.txt", b"plain\n")], zip64=True), 1)):
            files = dict(clean_files)
            files["site/downloads/" + name] = blob
            rc, out = run_capture(tree("zip-" + label, files))
            if rc != 0 or "including 1 ZIP archive(s) with {} member(s) scanned".format(count) not in out:
                failures.append("an archive whose records tile its bytes expected exit 0 with its {} member(s) "
                                "scanned (case {}; got {}: {!r})".format(count, label, rc, out))

        # 5q. each member-stream condition, pinned by a member that breaks only it: a deflate stream that
        #     never reaches its end (a sync flush and no final block; zipfile reads it whole and its CRC
        #     agrees), a stored member with bytes past its declared size, and a stored and a deflated member
        #     whose directory declares one byte more than their data holds. Each member holds only clean
        #     text, so a gate missing the condition passes where each must be CANNOT EVALUATE.
        for label, name, member in (
                ("5q-unfinished", "unfinished.zip",
                 entry("a.txt", b"plain\n", stored=deflate(b"plain\n", final=False))),
                ("5q-stored-past", "storedpast.zip",
                 entry("a.txt", b"plain\n", zipfile.ZIP_STORED, stored=b"plain\nmore\n")),
                ("5q-stored-short", "storedshort.zip", entry("a.txt", b"plain\n", zipfile.ZIP_STORED, size=7)),
                ("5q-deflated-short", "deflatedshort.zip", entry("a.txt", b"plain\n", size=7))):
            files = dict(clean_files)
            files["site/downloads/" + name] = raw_zip([member])
            rc, out = run_capture(tree("zip-" + label, files))
            expect_cannot_evaluate(label, "a member that is not exactly its declared data", rc, out,
                                   "site/downloads/{}!a.txt".format(name), "member data does not match")

        # 5r. the compressed-length check, which the coverage rule pre-empts in a full run (a data span past
        #     the archive's end never tiles), is pinned on _member_data_matches directly: a directory
        #     declaring one compressed byte more than the bytes left after a member's complete data.
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            blob = zip_blob((("a.txt", b"plain\n"),), method=method)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                info = archive.infolist()[0]
            cut = blob[:_local_header(blob, info)[0] + info.compress_size]
            info.compress_size += 1
            if _member_data_matches(cut, info, b"plain\n"):
                failures.append("a member declaring compressed bytes past the end of the data matched its "
                                "content (case 5r, method {})".format(method))

        # 5s. the archive-size bound, just inside, exactly at and just outside it, checked on the stat
        #     probe's size before the archive's bytes are read: the stat seam reports each size for a small
        #     clean archive, and the read seam raises for it outside the bound, so a read before the check
        #     fails the case. Outside is CANNOT EVALUATE under a .zip name and for a ZIP known only by its
        #     signature (its first four bytes alone read); a text file reported over the bound is still read
        #     and scanned, since the bound is the archive's alone.
        module = sys.modules[__name__]
        real_stat, real_read = module._stat, module._read_bytes
        limit = MAX_ARCHIVE_FILE_SIZE

        def run_sized(base, name, size, refuse_read):
            def sized_stat(path):
                status = real_stat(path)
                if path.name == name:
                    return os.stat_result(tuple(status)[:6] + (size,) + tuple(status)[7:])
                return status

            def bounded_read(path):
                if path.name == name and refuse_read:
                    raise OSError("self-test seam: {} was read past the archive-size bound".format(name))
                return real_read(path)
            module._stat, module._read_bytes = sized_stat, bounded_read
            try:
                return run_capture(base)
            finally:
                module._stat, module._read_bytes = real_stat, real_read

        for label, name, size in (("5s-inside", "sized.zip", limit - 1), ("5s-at", "sized.zip", limit),
                                  ("5s-outside", "sized.zip", limit + 1),
                                  ("5s-outside-signature", "sized.bin", limit + 1)):
            files = dict(clean_files)
            files["site/downloads/" + name] = zip_blob((("a.txt", b"plain\n"),))
            rc, out = run_sized(tree("zip-" + label, files), name, size, size > limit)
            if size > limit:
                expect_cannot_evaluate(label, "an archive over the archive-size bound", rc, out,
                                       "site/downloads/" + name,
                                       "a ZIP archive of {} bytes, over MAX_ARCHIVE_FILE_SIZE".format(size))
            elif rc != 0 or "including 1 ZIP archive(s) with 1 member(s) scanned" not in out:
                failures.append("an archive of {} bytes (bound {}) expected exit 0 with its member scanned "
                                "(case {}; got {}: {!r})".format(size, limit, label, rc, out))
        files = dict(clean_files)
        files["docs/big.md"] = secret
        rc, out = run_sized(tree("text-over-archive-bound", files), "big.md", limit + 1, False)
        if rc != 1 or "docs/big.md:1: private IP (10/8)" not in out:
            failures.append("a text file over the archive-size bound expected exit 1 with its finding, read "
                            "and scanned (case 5s; got {}: {!r})".format(rc, out))

        # 6. a non-regular file is CANNOT EVALUATE (exit 2, naming it), its content never read.
        #    Deterministic: the stat seam reports a FIFO mode for one fixture file, so the case runs on
        #    every runner (no /dev/null dependency). The read seam RAISES for that file, so a scan that
        #    reads the content before (or instead of) classifying it fails this case outright (a read
        #    error and no CANNOT EVALUATE line), not only when the planted private IP happens to
        #    surface; on a real FIFO such a read could block the gate.
        files = dict(clean_files)
        files["docs/fifo.md"] = ("fd " + _PRIVATE_IP + "\n").encode("ascii")
        base = tree("nonreg", files)
        module = sys.modules[__name__]
        saved_stat = module._stat
        def _fifo_stat(path, _real=saved_stat):
            if path.name == "fifo.md":
                return os.stat_result((stat.S_IFIFO | 0o644,) + tuple(_real(path))[1:])
            return _real(path)
        saved_read6 = module._read_bytes
        def _refuse_fifo_read(path, _real=saved_read6):
            if path.name == "fifo.md":
                raise OSError("self-test seam: the non-regular fifo.md was content-read; it must be "
                              "classified before any read")
            return _real(path)
        module._stat = _fifo_stat
        module._read_bytes = _refuse_fifo_read
        try:
            rc, out = run_capture(base)
        finally:
            module._stat = saved_stat
            module._read_bytes = saved_read6
        expect_cannot_evaluate("6", "a non-regular file", rc, out, "docs/fifo.md",
                               "not a regular file; its content was never opened")
        if "self-test seam" in out:
            failures.append("a non-regular file's content was read before it was classified (case 6; got "
                            "{}: {!r})".format(rc, out))

        # 6b. a symlink to a regular file is followed and its target scanned (the file rule).
        base = tree("symfile", dict(clean_files))
        (tmp / "target.txt").write_bytes(("ip " + _PRIVATE_IP + "\n").encode("ascii"))
        os.symlink(str(tmp / "target.txt"), str(base / "docs" / "linked.txt"))
        rc, out = run_capture(base)
        if rc != 1 or "docs/linked.txt:1: private IP (10/8)" not in out:
            failures.append("a symlink to a regular file expected exit 1 on the target content "
                            "(got {}: {!r})".format(rc, out))

        # 6c. a symlinked directory is CANNOT EVALUATE (exit 2, naming it), never silently dropped.
        base = tree("symdir", dict(clean_files))
        (tmp / "behind").mkdir(exist_ok=True)
        os.symlink(str(tmp / "behind"), str(base / "docs" / "linkdir"))
        rc, out = run_capture(base)
        expect_cannot_evaluate("6c", "a symlinked directory", rc, out, "docs/linkdir",
                               "symlinked directory; not followed")

        # 6d. a dangling file symlink is a read error: fail-closed (exit 2), never a clean skip.
        base = tree("dangle", dict(clean_files))
        os.symlink(str(base / "docs" / "gone.md"), str(base / "docs" / "dangling.md"))
        rc, out = run_capture(base)
        if rc != 2:
            failures.append("a dangling symlink expected exit 2, fail-closed (got {}: {!r})".format(rc, out))

        # 7. an unreadable file fails closed (exit 2). Deterministic: the read seam raises the
        #    PermissionError such a file raises, so the case runs on every runner (a chmod-0 probe would
        #    be skipped under a DAC-bypassing root and the guarded defect could ship meanwhile).
        base = tree("unread", dict(clean_files))
        saved_read = module._read_bytes
        def _deny_read(path, _real=saved_read):
            if path.name == "a.md":
                raise PermissionError(13, "injected permission denied", str(path))
            return _real(path)
        module._read_bytes = _deny_read
        try:
            rc, out = run_capture(base)
        finally:
            module._read_bytes = saved_read
        if rc != 2:
            failures.append("an unreadable file expected exit 2, fail-closed (got {}: {!r})".format(rc, out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a clean tree passes and states its scanned-file and archive counts; a private IP in a "
          ".csv or a Latin-1 .tsv, a host path in a Latin-1 .md, a Latin-1 README.MD, a UTF-16 or UTF-32 "
          "file with a byte-order mark, a UTF-16 stream starting with U+0000 (decided by a successful "
          "decode, never the 4-byte prefix alone), single-byte text behind a fake UTF-16 mark (the "
          "byte view is scanned beside the wide views), big-endian or misaligned text behind a UTF-16 "
          "mark, UTF-32 text behind a mismatched or misaligned mark (the wide views cover UTF-16 LE "
          "and BE at byte offsets 0-1 and UTF-32 LE and BE at byte offsets 0-3, the enumeration "
          "itself and the views read_scan_text returns, count, order and decoder per view, both "
          "asserted), a suffixless Latin-1 file or a symlinked regular "
          "file's target, "
          "a codename in a Latin-1 .txt, a private IP inside the denylist file, a private IP or host path "
          "in a deflated, Latin-1 or UTF-16 ZIP member (named <archive>!<member>:<line>), in a ZIP under "
          "another name, or in an archive comment are each a finding (exit 1); a clean archive passes, "
          "counted with its members, and so do records that tile an archive (data descriptors, ZIP64 sizes "
          "and end records, local extra fields); each archive bound (member count, total declared size, and "
          "the archive's own size, read by stat before its bytes) passes just inside and at it and is CANNOT "
          "EVALUATE just outside it, with no member read; a corrupt archive (in either suffix case), an "
          "archive whose records do not tile its bytes (an unlisted entry, a second archive, a gap, trailing "
          "bytes, overlapping records, a comment past the end), an encrypted, unsupported-method, nested, CRC-failing, undecodable or size-misstated "
          "member, a member with a second deflate stream or one that never ends, an injected "
          "non-regular file (its content never read: its read seam raises if it is) and a symlinked "
          "directory are each CANNOT EVALUATE (exit 2, naming it), never a skip; and a dangling symlink, "
          "an injected unreadable file, and a BOM-declared stream that decodes as neither declared "
          "candidate each fail closed (exit 2, naming the file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
