"""Compose the plain-Markdown adapter files (AGENTS.md, GEMINI.md, .github/copilot-instructions.md) from
the rule corpus and the reviewed block registry .aiqt/core/adapter-blocks.toml. Stdlib only.

Requires Python 3.14 or newer, the floor in .aiqt/core/python-floor.toml. No __main__: tools/gen_agents.py
and tools/gen_adapters.py are the entrypoints and share run() below, and the self-test vectors live in
gen_agents.py --self-test. conformance.py and gen_first_pin_demo.py reuse load_registry and compose,
re-rooted at their own root.

Layouts. The registry gives each of the three targets exactly one:
  legacy    the bytes the generators have always written: the generator header, then each rule body,
            blank-line separated. No block may be declared for a legacy target.
  composed  the same header (PREFIX, not wrapped), then marker-delimited segments separated by one blank
            line: the before-rules blocks by order, the AIQT-RULES segment holding the rule bodies, then
            the after-rules blocks by order. Each segment is
              <!-- NAME:BEGIN (generated sha256=<64 lowercase hex>) -->
              inner text
              <!-- NAME:END -->
            (the _gen_common._markers convention, with the digest inside the BEGIN parenthetical), where
            the digest is SHA-256 over the UTF-8 inner bytes, both marker lines and their LFs excluded.
The AIQT rules are fixed in code (the AIQT-RULES segment), never a registry row, so deleting a row can
never drop the rules while the drift check stays green.

Write-mode guard. Before anything is written, every target whose bytes would change is judged (guard): a
legacy target holding a marker-like line, or a composed target holding text outside a block, a malformed,
nested, repeated or unknown marker, an edited header, or a block whose inner text no longer matches the
digest in its own marker, is refused (exit 2) with nothing written. The deliberate escape hatch is to
delete the target file, which shows in the diff, and regenerate. --check never consults the guard for
its verdict: it compares the full desired bytes with the raw bytes on disk (so a CRLF conversion is
drift), and the guard only adds a diagnosis.

Target paths. A target is never read or written through a symlink: every directory between the root
and the target is opened beneath the previous directory's descriptor (os.open with O_DIRECTORY and
O_NOFOLLOW and dir_fd), and the target itself is opened beneath the final descriptor with O_NOFOLLOW and
O_NONBLOCK, for the read and for the write-permission probe alike, and that descriptor is fstat-checked
to be a regular file. A symlink or non-regular file met while the path is opened, including one swapped
in after an earlier check, is exit 2 in both modes. A write goes to a new temporary file created
exclusively beside the target through the same descriptor (keeping an existing target's ordinary
permission bits) and os.replace renames it over the target through that descriptor, so the target holds
either its old bytes or its new bytes. Whatever is swapped in at the target name after the write probe is
not refused but replaced by that rename: a symlink there is itself replaced, never followed, so nothing
outside the tree is written (a directory there makes the rename fail, exit 2). Each directory is opened
O_RDONLY, which needs read permission as well as search permission, so a directory between the root and a
target that this user can search but not read is exit 2 in both modes (the per-component lstat walk
before descriptors needed search permission only). A platform without dir_fd support is exit 2 in both
modes: there is no path-based fallback.

No rule corpus. An absent or unreachable .aiqt/core/rules/ is exit 2 in both modes with nothing written
or deleted: the generators never delete a target. To retire an adapter, review it and delete it by hand.

Registry fields. Every field is type-checked before it is used, so a wrong type (an array or table where a
string or integer belongs) is a ComposeError naming the field, never a TypeError.

Exit convention (run): 0 in sync or written; 1 drift (--check); 2 a missing, malformed or unreadable
registry, block source, corpus or target, a symlinked or non-regular target, a write-mode refusal, or
a failed write.

DISCLOSED RESIDUALS. The digest is not a security boundary: anyone who can edit a block and its digest,
or the sources, registry and generator together, passes the guard; diff review is the control. Target
reads and writes resolve beneath directory descriptors, so a directory swapped for a symlink mid-run
cannot redirect them. What remains is narrower. The repository root itself is still opened by path once
per target operation, and the registry and block sources are judged by a per-component lstat walk before
they are read by path, so an attacker who can replace the repository root, or who can race a registry or
block-source read, is out of scope (either one could edit those inputs directly, and diff review is the
control there too). A descriptor pins a directory, not its place in the tree: a directory between the
root and a target that is renamed out of the tree after the walk opened it still receives the write, at
its new place (the repository root's own ancestry race, one level down; whoever can rename it could
write into it directly). The targets of one run are renamed one at a time, not as a set. A successful
write keeps only the target's ordinary permission bits (mode & 0o777): its set-user-ID, set-group-ID and
sticky bits are dropped (a write by an unprivileged process drops the set-ID bits anyway), and owner,
group, ACLs and extended attributes are not preserved (the renamed file takes the writer's defaults). An
existing target this user cannot write (no write permission) is refused (exit 2), as the plain overwrite
before descriptors was. A write that fails after its temporary file (a dot-prefixed name ending in .tmp)
exists and before the rename completes is exit 2 and names that file in the refusal; a run interrupted or
killed after the creation and before the rename completes leaves it too. In each case the target keeps
its old bytes and the temporary file is left beside it (an exception other than OSError raised inside the
try around the write and the rename carries its name as a note): the engine never deletes a file by name,
because another process can replace that name between the creation and an unlink, and an identity check
before the unlink races the same way, so the leftover is for a reviewer to remove by hand. Once the
rename has completed, a failure to close the target's directory descriptor (the one step after it) is
still exit 2, with that close error's own text, although the target already holds its new bytes and no
temporary file remains: the old-bytes and left-file statements above cover only a failure before the
rename completes. A colliding name at creation is retried, bounded, with a fresh random
token, and the colliding file is kept. A process that replaces the temporary name before the rename has
its own bytes renamed over the target; it could equally write the target directly, so this grants
nothing (diff review is the control). Descriptors are closed exactly once on every ordinary success and
error path. A KeyboardInterrupt (or another asynchronous exception) that lands between an open and its
single close can leave that descriptor open in three windows: after the open returns and before the try
that owns the descriptor (for the directory descriptor _target_dir_fd returns, until its caller's try);
at the walk's hand-off from a parent directory descriptor to its child (from the child's open until the
parent's close runs); and inside a finally block or a close helper before its os.close runs. One that
lands after the temporary file is created but outside the try that adds the note (before that try is
entered, or while the OSError refusal is being built) leaves the temporary file beside the target with
nothing naming it. An interrupted run is a process about to exit, whose descriptors the kernel closes,
so these windows are not guarded. A legacy target still loses a hand edit on regeneration unless it
holds a marker-like line; --check still catches the edit in CI. A change to a generator's header is
refused for a composed target that already carries the old header; delete and regenerate after reviewing
it.
"""
import errno
import hashlib
import os
import re
import secrets
import stat
import sys
from collections import namedtuple
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import os
    if tuple(sys.version_info[:2]) < (3, 14):
        # Reached only through an importer that carries no floor guard yet (every guarded
        # entrypoint refuses an older Python that can start it first; one that cannot start
        # it fails with Python's own error before reaching here): the version is the
        # problem, so say so. The message is a literal, as the fallback grammar in
        # tools/check_python_floor.py requires (it admits no call), so it names no version.
        try:
            sys.stderr.write(
                "error: the adapter composer requires Python 3.14 or newer, and this interpreter is "
                "older. Nothing was run (cannot evaluate).\n")
            sys.stderr.flush()
        except BaseException:
            pass
    else:  # every Python 3.14 ships tomllib, so this installation is incomplete
        try:
            sys.stderr.write(
                "error: the adapter composer cannot import tomllib, part of the Python standard "
                "library; this installation is incomplete. Nothing was run (cannot evaluate).\n")
            sys.stderr.flush()
        except BaseException:
            pass
    os._exit(2)

from _standards import dir_present  # noqa: E402

REGISTRY_REL = ".aiqt/core/adapter-blocks.toml"
RULES_PARTS = (".aiqt", "core", "rules")
# The three targets the registry must declare, exactly once each. Adding a target is a reviewed change
# here and in the generator that renders it.
TARGETS = ("AGENTS.md", "GEMINI.md", ".github/copilot-instructions.md")
LAYOUTS = ("legacy", "composed")
POSITIONS = ("before-rules", "after-rules")
# Only pack-owned OPF blocks are accepted. AGENTS.md and GEMINI.md are pack-immutable, so adopter bytes
# in them need a spec-level class change first.
OWNERS = ("opf",)
RULES_ID = "AIQT-RULES"
RESERVED_PREFIX = "AIQT-"
FORMAT_VERSION = 1
TOP_KEYS = frozenset(("format-version", "retired-blocks", "target", "block"))
TOP_REQUIRED = frozenset(("format-version", "retired-blocks", "target"))
TARGET_KEYS = frozenset(("path", "layout"))
BLOCK_KEYS = frozenset(("id", "owner", "target", "position", "order", "source"))
# Characters a block source or the registry may not hold, written as code points so no literal invisible
# character sits in this file.
FORBIDDEN_CHARS = ((chr(0x0D), "a carriage return"), (chr(0xFEFF), "a byte-order mark"), (chr(0), "a NUL"))

ID_RE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*")
# A whole marker line as compose() writes it (fullmatch on one line, the LF already split off).
STRICT_RE = re.compile(r"<!-- ([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*):(BEGIN \(generated sha256=([0-9a-f]{64})\)|END) -->")
# Anything that looks like a marker; a loose match that is not a strict match is an error, so a malformed
# or smuggled marker cannot pass as ordinary text.
LOOSE_RE = re.compile(r"\s*<!--\s*[A-Za-z0-9_-]+\s*:\s*(?:BEGIN|END)\b")

Registry = namedtuple("Registry", "layouts blocks retired")
# Every target legacy, no block: what an install that predates the registry renders (load_registry with
# absent_is_legacy, for tools/conformance.py).
LEGACY_REGISTRY = Registry({rel: "legacy" for rel in TARGETS}, (), frozenset())
Block = namedtuple("Block", "id owner target position order source text")
# data: the full desired bytes; rules_start/rules_end: byte offsets of the rule region (the AIQT-RULES
# inner text when composed); prefix: the header bytes; legacy: the legacy rendering of the same corpus.
Composed = namedtuple("Composed", "target layout prefix data rules_start rules_end legacy")


class ComposeError(ValueError):
    """A malformed or unreadable registry, block source or composition input; callers map it to exit 2."""


def _check_rel(rel, what):
    """A repo-relative POSIX path: a non-empty string, not absolute, no backslash or NUL, and no empty,
    '.' or '..' part."""
    if not isinstance(rel, str) or not rel:
        raise ComposeError("{} must be a non-empty string, got {!r}".format(what, rel))
    if rel.startswith("/") or "\\" in rel or chr(0) in rel:
        raise ComposeError("{} {!r} is not a repo-relative POSIX path".format(what, rel))
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ComposeError("{} {!r} has an empty, '.' or '..' part".format(what, rel))


def _regular_file(root, rel, what, absent_ok=False):
    """The path of rel under root, which must be a regular file with no symlink anywhere along it.
    Absent is a ComposeError (every input here is required), or None when absent_ok; any other stat
    failure propagates as OSError, so an unreadable ancestor fails closed rather than reading as absent."""
    path, parts, st = Path(root), rel.split("/"), None
    for i, part in enumerate(parts):
        path = path / part
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            if absent_ok:
                return None
            raise ComposeError("{} {} does not exist".format(what, rel)) from None
        if stat.S_ISLNK(st.st_mode):
            raise ComposeError("{} {}: {} is a symlink".format(what, rel, "/".join(parts[:i + 1])))
    if not stat.S_ISREG(st.st_mode):
        raise ComposeError("{} {} is not a regular file".format(what, rel))
    return path


def _strict_text(raw, what):
    """Decode raw as strict UTF-8 holding no CR, byte-order mark or NUL."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ComposeError("{} is not valid UTF-8 ({})".format(what, exc)) from None
    for bad, name in FORBIDDEN_CHARS:
        if bad in text:
            raise ComposeError("{} contains {}".format(what, name))
    return text


def _check_source_text(text, rel):
    """A block source: non-empty, ending in exactly one LF, no leading or trailing blank line, and no
    marker-like line."""
    if not text:
        raise ComposeError("block source {} is empty".format(rel))
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise ComposeError("block source {} must end in exactly one LF".format(rel))
    lines = text[:-1].split("\n")
    if not lines[0].strip() or not lines[-1].strip():
        raise ComposeError("block source {} begins or ends with a blank line".format(rel))
    for n, line in enumerate(lines, 1):
        if LOOSE_RE.match(line):
            raise ComposeError("block source {} line {} is a marker-like line".format(rel, n))


def _plain_int(value):
    return type(value) is int  # bool is an int subclass; refuse it


def _valid_id(value):
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def _string(row, key, label):
    """row[key], which must be a string; any other TOML type (an array, a table, a number, a boolean, a
    date) is a ComposeError naming the field, so no later membership test or dict lookup sees it."""
    value = row[key]
    if not isinstance(value, str):
        raise ComposeError("{}: {} {} must be a string, got {} {!r}".format(
            REGISTRY_REL, label, key, type(value).__name__, value))
    return value


def load_registry(root, absent_is_legacy=False):
    """Read and validate REGISTRY_REL under root, then read and validate every declared block source.
    Raises ComposeError (or OSError) on any failure, before any target is read. With absent_is_legacy, an
    absent registry (no file and no symlink at its path) is LEGACY_REGISTRY instead of an error; a present
    registry is validated in full either way."""
    root = Path(root)
    path = _regular_file(root, REGISTRY_REL, "the adapter block registry", absent_ok=absent_is_legacy)
    if path is None:
        return LEGACY_REGISTRY
    text = _strict_text(path.read_bytes(), REGISTRY_REL)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ComposeError("{}: {}".format(REGISTRY_REL, exc)) from None
    except RecursionError as exc:
        raise ComposeError("{}: TOML nesting is too deep to parse ({})".format(REGISTRY_REL, exc)) from None
    where = REGISTRY_REL
    if set(data) - TOP_KEYS or TOP_REQUIRED - set(data):
        raise ComposeError("{}: top-level keys must be {} (block optional); got {}".format(
            where, sorted(TOP_KEYS), sorted(data)))
    if not _plain_int(data["format-version"]) or data["format-version"] != FORMAT_VERSION:
        raise ComposeError("{}: format-version must be {}".format(where, FORMAT_VERSION))

    rows = data["target"]
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ComposeError("{}: target must be an array of tables".format(where))
    layouts = {}
    for row in rows:
        if set(row) != TARGET_KEYS:
            raise ComposeError("{}: a target row must have exactly the keys {}; got {}".format(
                where, sorted(TARGET_KEYS), sorted(row)))
        rel = _string(row, "path", "a target row")
        layout = _string(row, "layout", "target {}".format(rel))
        if rel not in TARGETS:
            raise ComposeError("{}: unknown target {!r}".format(where, rel))
        if rel in layouts:
            raise ComposeError("{}: target {} is declared twice".format(where, rel))
        if layout not in LAYOUTS:
            raise ComposeError("{}: target {} layout {!r} is not one of {}".format(where, rel, layout, LAYOUTS))
        layouts[rel] = layout
    if set(layouts) != set(TARGETS):
        raise ComposeError("{}: the targets must be exactly {}; missing {}".format(
            where, list(TARGETS), sorted(set(TARGETS) - set(layouts))))

    retired = data["retired-blocks"]
    if not isinstance(retired, list):
        raise ComposeError("{}: retired-blocks must be an array".format(where))
    for rid in retired:
        if not _valid_id(rid) or rid.startswith(RESERVED_PREFIX):
            raise ComposeError("{}: retired-blocks entry {!r} is not a valid, unreserved block id".format(
                where, rid))
    if len(set(retired)) != len(retired):
        raise ComposeError("{}: retired-blocks lists an id twice".format(where))

    raw_blocks = data.get("block", [])
    if not isinstance(raw_blocks, list) or not all(isinstance(b, dict) for b in raw_blocks):
        raise ComposeError("{}: block must be an array of tables".format(where))
    rows, seen_ids, seen_orders = [], set(), set()
    for b in raw_blocks:
        if set(b) != BLOCK_KEYS:
            raise ComposeError("{}: a block row must have exactly the keys {}; got {}".format(
                where, sorted(BLOCK_KEYS), sorted(b)))
        bid = b["id"]
        if not _valid_id(bid):
            raise ComposeError("{}: block id {!r} is not of the form {}".format(where, bid, ID_RE.pattern))
        if bid.startswith(RESERVED_PREFIX):
            raise ComposeError("{}: block id {} uses the reserved prefix {}".format(where, bid, RESERVED_PREFIX))
        label = "block {}".format(bid)
        owner = _string(b, "owner", label)
        tgt = _string(b, "target", label)
        position = _string(b, "position", label)
        if owner not in OWNERS:
            raise ComposeError("{}: block {} owner {!r} is refused; only {} blocks are accepted".format(
                where, bid, owner, list(OWNERS)))
        if tgt not in layouts:
            raise ComposeError("{}: block {} names an unknown target {!r}".format(where, bid, tgt))
        if layouts[tgt] != "composed":
            raise ComposeError("{}: block {} is declared for {}, whose layout is {}; a block needs the "
                               "composed layout".format(where, bid, tgt, layouts[tgt]))
        if position not in POSITIONS:
            raise ComposeError("{}: block {} position {!r} is not one of {}".format(
                where, bid, position, POSITIONS))
        order = b["order"]
        if not _plain_int(order) or order <= 0:
            raise ComposeError("{}: block {} order must be a positive integer, got {!r}".format(
                where, bid, order))
        src = b["source"]
        _check_rel(src, "block {} source".format(bid))
        if src in TARGETS or src == REGISTRY_REL:
            raise ComposeError("{}: block {} source {} is a target or the registry itself".format(
                where, bid, src))
        if (tgt, bid) in seen_ids:
            raise ComposeError("{}: block {} is declared twice for {}".format(where, bid, tgt))
        if (tgt, position, order) in seen_orders:
            raise ComposeError("{}: two blocks share order {} at {} of {}".format(
                where, order, position, tgt))
        seen_ids.add((tgt, bid))
        seen_orders.add((tgt, position, order))
        rows.append(b)
    clash = set(retired) & set(b["id"] for b in rows)
    if clash:
        raise ComposeError("{}: retired-blocks also declares {}".format(where, sorted(clash)))

    blocks = []
    for b in rows:
        spath = _regular_file(root, b["source"], "block {} source".format(b["id"]))
        stext = _strict_text(spath.read_bytes(), "block source " + b["source"])
        _check_source_text(stext, b["source"])
        blocks.append(Block(b["id"], b["owner"], b["target"], b["position"], b["order"], b["source"], stext))
    return Registry(layouts, tuple(blocks), frozenset(retired))


def registry_text(layouts=(), blocks=(), retired=()):
    """A registry text with every target legacy except those named in layouts ((path, layout) pairs);
    blocks are (id, target, position, order, source, unused) rows with owner "opf". For synthetic trees
    (the self-tests of gen_agents, conformance and gen_first_pin_demo); load_registry validates it."""
    chosen = dict(layouts)
    lines = ["format-version = " + str(FORMAT_VERSION),
             "retired-blocks = [" + ", ".join('"' + r + '"' for r in retired) + "]", ""]
    for path in TARGETS:
        lines += ["[[target]]", 'path = "' + path + '"', 'layout = "' + chosen.get(path, "legacy") + '"', ""]
    for bid, target, position, order, source, _unused in blocks:
        lines += ["[[block]]", 'id = "' + bid + '"', 'owner = "opf"', 'target = "' + target + '"',
                  'position = "' + position + '"', "order = " + str(order), 'source = "' + source + '"', ""]
    return "\n".join(lines)

def legacy_render(header, bodies):
    """The legacy layout, byte for byte what gen_agents/gen_adapters have always written."""
    return "\n".join(list(header) + [b + "\n" for b in bodies]).rstrip() + "\n"


def _begin(name, inner):
    return "<!-- {}:BEGIN (generated sha256={}) -->".format(
        name, hashlib.sha256(inner.encode("utf-8")).hexdigest())


def _end(name):
    return "<!-- {}:END -->".format(name)


def compose(target_rel, header, bodies, registry):
    """The desired bytes of target_rel for the ordered rule bodies under registry (a Composed). Raises
    ComposeError on an unregistered target, or for a composed target on an empty corpus or a rule body
    holding a marker-like line."""
    layout = registry.layouts.get(target_rel)
    if layout is None:
        raise ComposeError("{} is not a registered adapter target".format(target_rel))
    prefix = "\n".join(header) + "\n"
    legacy = legacy_render(header, bodies).encode("utf-8")
    if layout == "legacy":
        return Composed(target_rel, layout, prefix.encode("utf-8"), legacy,
                        len(prefix.encode("utf-8")), len(legacy), legacy)
    if not bodies:
        raise ComposeError("{} is a composed target and the rule corpus holds no rule".format(target_rel))
    for body in bodies:
        for line in body.split("\n"):
            if LOOSE_RE.match(line):
                raise ComposeError("a rule body holds a marker-like line {!r}; it cannot be composed into "
                                   "{}".format(line, target_rel))
    mine = sorted((b for b in registry.blocks if b.target == target_rel), key=lambda b: b.order)
    segments = ([(b.id, b.text[:-1]) for b in mine if b.position == "before-rules"]
                + [(RULES_ID, "\n\n".join(bodies))]
                + [(b.id, b.text[:-1]) for b in mine if b.position == "after-rules"])
    out = prefix.encode("utf-8")
    start = end = None
    for i, (name, inner) in enumerate(segments):
        if i:
            out += b"\n\n"
        out += (_begin(name, inner) + "\n").encode("utf-8")
        if name == RULES_ID:
            start = len(out)
        out += inner.encode("utf-8")
        if name == RULES_ID:
            end = len(out)
        out += ("\n" + _end(name)).encode("utf-8")
    out += b"\n"
    return Composed(target_rel, layout, prefix.encode("utf-8"), out, start, end, legacy)


def guard(current, composed, registry):
    """None when writing composed.data over current bytes loses nothing hand-made, else the reason for a
    refusal. current None (an absent file) may always be created."""
    if current is None:
        return None
    try:
        text = current.decode("utf-8")
    except UnicodeDecodeError:
        return "is not valid UTF-8; delete the file and regenerate"
    lines = text.split("\n")
    marker_like = [n for n, line in enumerate(lines, 1) if LOOSE_RE.match(line)]
    if composed.layout == "legacy":
        if marker_like:
            return ("line {} is a marker-like line in a legacy-layout target (a hand-pasted block); move "
                    "the content into a registered source or delete the file".format(marker_like[0]))
        return None
    if not marker_like:
        # The one-time migration from the legacy layout: only the exact legacy rendering of this corpus.
        if current == composed.legacy:
            return None
        return ("holds content that is not the generated rendering; move it into a registered source or "
                "delete the file")
    if chr(0x0D) in text:
        return "contains a carriage return; delete the file and regenerate"
    prefix = composed.prefix.decode("utf-8")
    if not text.startswith(prefix):
        return "the generated header was edited; move the content into a registered source or delete the file"
    rows = text[len(prefix):].split("\n")
    if rows[-1] == "":
        rows.pop()
    allowed = ({RULES_ID} | set(b.id for b in registry.blocks if b.target == composed.target)
               | registry.retired)
    open_id, digest, inner, seen = None, None, [], set()
    for n, line in enumerate(rows, prefix.count("\n") + 1):
        m = STRICT_RE.fullmatch(line)
        if m is None:
            if LOOSE_RE.match(line):
                return "line {} is a malformed marker".format(n)
            if open_id is not None:
                inner.append(line)
            elif line:
                return "line {} is text outside any block; move it into a registered source".format(n)
            continue
        name = m.group(1)
        if m.group(2) != "END":
            if open_id is not None:
                return "line {}: block {} begins inside the open block {}".format(n, name, open_id)
            if name in seen:
                return "line {}: block {} appears more than once".format(n, name)
            if name not in allowed:
                return ("line {}: block {} is neither {}, a block declared for this target, nor a retired "
                        "block".format(n, name, RULES_ID))
            open_id, digest, inner = name, m.group(3), []
            seen.add(name)
            continue
        if open_id is None:
            return "line {}: END of {} with no open block".format(n, name)
        if name != open_id:
            return "line {}: END of {} does not close the open block {}".format(n, name, open_id)
        if hashlib.sha256("\n".join(inner).encode("utf-8")).hexdigest() != digest:
            return ("block {} was edited by hand: its content no longer matches the digest in its BEGIN "
                    "marker; move the change into its source or delete the file".format(name))
        open_id = None
    if open_id is not None:
        return "block {} is not closed before the end of the file".format(open_id)
    if RULES_ID not in seen:
        return "the {} block is missing".format(RULES_ID)
    return None


def _close_fd_propagating(fd):
    """Close a descriptor on a FAIL-CLOSED path: the close error PROPAGATES. Exactly ONE os.close; if it
    raises, the number counts as released (close(2) on Linux releases it early, even when the close then
    reports EINTR or EIO, and a retry can close another thread's reused descriptor: man 2 close), so it
    is never probed or closed again, and the ORIGINAL close error propagates unchanged. The same body as
    tools/check_release_cut.py's copy of opf/tools/_journal._close_fd_propagating."""
    os.close(fd)


def _close_fd_yielding(fd):
    """Close a descriptor from an `except` handler or a `finally` block without letting a close error
    REPLACE the exception already in flight there: when an exception is unwinding through, or being
    handled in, the CALLING frame, the same single close still runs (one os.close, the number released
    either way and never touched again) but its close error is dropped so the ORIGINAL exception keeps
    propagating; on the normal path this is exactly _close_fd_propagating, so a close error still fails
    closed. The same body as tools/check_release_cut.py's copy of opf/tools/_journal._close_fd_yielding."""
    tb = sys.exc_info()[2]
    if tb is None or tb.tb_frame is not sys._getframe(1):
        _close_fd_propagating(fd)
        return
    try:
        _close_fd_propagating(fd)
    except OSError:
        pass                                      # the in-flight exception wins; the fd was still released


# The dir_fd capability probe, bound at import so it reflects the platform, not a later rebinding of an
# os attribute (the self-test wraps os.mkdir). os.rename, not os.replace, is the member to probe:
# os.supports_dir_fd lists the renameat capability under rename, and os.replace shares it.
_DIR_FD_REQUIRES = (os.open, os.stat, os.mkdir, os.rename)


def _require_dir_fd():
    """Refuse (ComposeError, which run() maps to exit 2) where descriptor-relative resolution is
    unavailable: without dir_fd on open/stat/mkdir/rename plus O_DIRECTORY and O_NOFOLLOW there
    is no way to pin a directory against a concurrent swap, and this engine never falls back to a
    path-based target lookup."""
    if (any(fn not in os.supports_dir_fd for fn in _DIR_FD_REQUIRES)
            or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW")):
        raise ComposeError(
            "this platform cannot resolve a target beneath a directory descriptor (os.open, os.stat, "
            "os.mkdir and os.rename with dir_fd, plus O_DIRECTORY and O_NOFOLLOW, are "
            "required); a target is never read or written through a swappable path, so nothing was read "
            "or written")


def _symlink_refusal(rel, upto):
    return ComposeError("target {}: {} is a symlink; a target is never read or written through a "
                        "symlink".format(rel, upto))


def _target_open_refusal(exc, rel):
    """The engine's refusal, naming rel from the root, for an open of the final component that failed
    because the name no longer holds a regular file: a symlink (ELOOP, or EMLINK on some BSDs, from
    O_NOFOLLOW), a FIFO with no reader, a socket or a device with nothing behind it (ENXIO, from the
    write probe's O_NONBLOCK or from opening a socket), or a directory (EISDIR, from the write probe's
    O_WRONLY). None for any other errno, which the caller re-raises unchanged."""
    if exc.errno in (errno.ELOOP, errno.EMLINK):
        return _symlink_refusal(rel, rel)
    if exc.errno == errno.ENXIO:
        return ComposeError("target {} is not a regular file (a FIFO with no reader, a socket, or a device "
                            "with nothing behind it)".format(rel))
    if exc.errno == errno.EISDIR:
        return ComposeError("target {} is not a regular file (a directory)".format(rel))
    return None


def _open_dir_component(dir_fd, part, rel, upto):
    """One directory component opened beneath dir_fd with O_DIRECTORY|O_NOFOLLOW: a symlink here is
    refused (ELOOP, or ENOTDIR when O_DIRECTORY judges the link first), and so is a non-directory; a
    no-follow stat only picks which message names the refusal that already happened. FileNotFoundError
    propagates for the caller to decide: absent is legal on a read, and mkdir ground on a write."""
    try:
        return os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.EMLINK, errno.ENOTDIR):
            raise
        try:
            st = os.stat(part, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            raise ComposeError("target {}: {} is a symlink or not a directory; a target is never read "
                               "or written through it".format(rel, upto)) from None
        if stat.S_ISLNK(st.st_mode):
            raise _symlink_refusal(rel, upto) from None
        raise ComposeError("target {}: {} is not a directory".format(rel, upto)) from None


def _target_dir_fd(root, rel, make_dirs):
    """(directory descriptor, final name, lstat) of target rel under root. Every directory between root
    and the target is opened beneath the previous one's descriptor (O_DIRECTORY|O_NOFOLLOW), so a
    symlink anywhere along the path, even one swapped in while the run is in flight, is a ComposeError,
    never followed; with make_dirs a missing directory is made with mkdir beneath the same descriptor
    and then opened the same no-follow way, so a symlink planted against the mkdir is refused too. The
    lstat (os.stat with follow_symlinks=False, beneath the descriptor) is None when the target is
    absent; a symlink or non-regular file at the final name is a ComposeError. Returns (None, name,
    None) when a directory above the target is absent and make_dirs is false. The caller closes the
    returned descriptor; on every other ordinary path this function closes it itself, exactly once (an
    interrupt before the try that owns a descriptor, between this return and the caller's try, at the
    hand-off from a parent descriptor to its child, or inside a close before os.close runs is out of
    scope: see DISCLOSED RESIDUALS)."""
    _require_dir_fd()
    _check_rel(rel, "target")
    parts = rel.split("/")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    handed = False
    try:
        for i, part in enumerate(parts[:-1]):
            upto = "/".join(parts[:i + 1])
            try:
                child = _open_dir_component(directory, part, rel, upto)
            except FileNotFoundError:
                if not make_dirs:
                    return None, parts[-1], None
                try:
                    os.mkdir(part, dir_fd=directory)
                except FileExistsError:
                    pass  # raced in by another process; the no-follow open below judges the winner
                child = _open_dir_component(directory, part, rel, upto)
            parent, directory = directory, child  # held first: a raising close cannot strand child
            _close_fd_propagating(parent)
        try:
            st = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None:
            if stat.S_ISLNK(st.st_mode):
                raise _symlink_refusal(rel, rel)
            if not stat.S_ISREG(st.st_mode):
                raise ComposeError("target {} is not a regular file".format(rel))
        handed = True
        return directory, parts[-1], st
    finally:
        if not handed:
            _close_fd_yielding(directory)


def _read_target(root, rel):
    """The current bytes of target rel under root, or None when it (or a directory above it) is absent.
    The final component is opened beneath the directory descriptor from _target_dir_fd with O_NOFOLLOW
    (a symlink swapped in after the stat is refused, never followed) and O_NONBLOCK (a FIFO swapped in
    cannot block the open), and the open descriptor is fstat-checked to still be a regular file before
    any file object is made on it (an O_RDONLY open admits a directory, which a file object would refuse
    with raw errno text naming the descriptor number; the fstat refuses it in the engine's wording naming
    rel); an open refused because the name holds a symlink, socket or device is the engine's refusal
    naming rel.
    The file object is made with closefd=False, so the finally's close is the one close of fd on every
    ordinary path."""
    directory, name, st = _target_dir_fd(root, rel, make_dirs=False)
    if directory is None:
        return None
    try:
        if st is None:
            return None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except OSError as exc:
            refusal = _target_open_refusal(exc, rel)
            if refusal is not None:
                raise refusal from None
            raise
        try:
            mode = os.fstat(fd).st_mode
            if not stat.S_ISREG(mode):
                raise ComposeError("target {} is not a regular file{}".format(
                    rel, " (a directory)" if stat.S_ISDIR(mode) else ""))
            with os.fdopen(fd, "rb", closefd=False) as fh:
                return fh.read()
        finally:
            _close_fd_yielding(fd)
    finally:
        _close_fd_yielding(directory)


# How many fresh random temporary names a write tries when a name collides. The loop only ever runs
# past its first lap when a file at the 64-random-bit candidate name already exists, and a colliding
# file is never deleted: it is someone else's.
_TMP_TRIES = 16


def _write_target(root, rel, data):
    """Write data to target rel under root, atomically and never through a path a concurrent process
    can swap: the target's directory descriptor comes from _target_dir_fd (missing directories made
    beneath the held descriptor), an existing target is probed for write permission beneath it (a
    read-only target refuses with the PermissionError a plain overwrite raised) with O_NOFOLLOW (a
    symlink swapped in since the walk is refused, never followed) and O_NONBLOCK (a FIFO swapped in
    cannot block the open), and the probe descriptor is fstat-checked to still be a regular file (a
    symlink, a FIFO with no reader or a directory refused at the open is the engine's refusal naming
    rel, as a non-regular file found by the fstat is); data
    goes to a new temporary file (O_CREAT|O_EXCL|O_NOFOLLOW beneath the descriptor; a colliding name is
    retried with a fresh random token up to _TMP_TRIES times) which takes the existing target's
    ordinary permission bits (mode & 0o777; set-ID and sticky bits are dropped) via fchmod, and
    os.replace renames it over the target through the same descriptor. Nothing is ever deleted: a
    pre-existing file at a colliding name is never touched, and on a failure after the temporary file
    exists and before the rename completes that file is left in place and named in the ComposeError,
    since the name may no longer be this call's file. A failed close of the directory descriptor after
    a completed rename propagates its OSError (exit 2) with the target already rewritten (see
    DISCLOSED RESIDUALS). Any other exception raised inside the try around the write
    and the rename carries the name as a note; one that lands after the creation but outside that try (an
    interrupt before the try is entered, or while the ComposeError is built) leaves the file unnamed (see
    DISCLOSED RESIDUALS). The temporary file object is made with closefd=False, so the finally's close is
    the one close of tfd on every ordinary path."""
    directory, name, st = _target_dir_fd(root, rel, make_dirs=True)
    try:
        if st is not None:
            # The refusal a plain open-for-write gave before descriptors: an existing target without
            # write permission is PermissionError (exit 2), before any temporary file exists.
            try:
                probe = os.open(name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except OSError as exc:
                refusal = _target_open_refusal(exc, rel)
                if refusal is not None:
                    raise refusal from None
                raise
            try:
                if not stat.S_ISREG(os.fstat(probe).st_mode):
                    raise ComposeError("target {} is not a regular file".format(rel))
            finally:
                _close_fd_yielding(probe)
        tmp = tfd = None
        for _ in range(_TMP_TRIES):
            candidate = ".{}.{}.tmp".format(name, secrets.token_hex(8))
            try:
                tfd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o666, dir_fd=directory)
            except FileExistsError:
                continue  # someone else's file; leave it alone and try a fresh name
            tmp = candidate
            break
        if tmp is None:
            raise ComposeError("target {}: {} random temporary names beside it already hold files; "
                               "none were deleted (they are not this run's)".format(rel, _TMP_TRIES))
        tmp_rel = "/".join(rel.split("/")[:-1] + [tmp])
        try:
            try:
                with os.fdopen(tfd, "wb", closefd=False) as fh:
                    if st is not None:
                        os.fchmod(fh.fileno(), stat.S_IMODE(st.st_mode) & 0o777)
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
            finally:
                _close_fd_yielding(tfd)
            os.replace(tmp, name, src_dir_fd=directory, dst_dir_fd=directory)
        except OSError as exc:
            # Never unlinked: since the create another process may have put its own file at that name.
            raise ComposeError("target {}: the write failed ({}); the target is unchanged and the "
                               "temporary file {} was left beside it, not deleted (review it and remove it "
                               "by hand)".format(rel, exc, tmp_rel)) from exc
        except BaseException as exc:
            exc.add_note("unless the rename completed, the temporary file {} was left beside the target, "
                         "not deleted".format(tmp_rel))
            raise
    finally:
        _close_fd_yielding(directory)


def run(root, check, targets, regen, corpus_bodies):
    """The single check/write engine. targets: [(rel, header lines)]; regen: the command named on drift;
    corpus_bodies(src_dir): the ordered rule bodies. Registry, corpus and every target are read (never
    through a symlink) and composed first; --check compares raw bytes (exit 1 on any difference); write
    mode guards ALL targets whose bytes would change before writing any, so one refusal (exit 2) writes
    nothing. No corpus is exit 2 with nothing written or deleted."""
    root = Path(root)
    try:
        registry = load_registry(root)
        # dir_present (not is_dir): an unreadable .aiqt/ parent fails closed as exit 2, like an absent one.
        src_dir = root.joinpath(*RULES_PARTS)
        if not dir_present(src_dir):
            print("error: the rule corpus {}/ is absent; nothing was written or deleted (restore the corpus; "
                  "to retire an adapter, review it and delete it by hand)".format("/".join(RULES_PARTS)))
            return 2
        bodies = corpus_bodies(src_dir)
        plans = []
        for rel, header in targets:
            composed = compose(rel, header, bodies, registry)
            plans.append((rel, composed, _read_target(root, rel)))
        if check:
            drift = False
            for rel, composed, current in plans:
                if current != composed.data:
                    print("drift: {}".format(rel))
                    reason = guard(current, composed, registry)
                    if reason:
                        print("  note: a regeneration would refuse {}: {}".format(rel, reason))
                    drift = True
            if drift:
                print("run {} to regenerate".format(regen))
                return 1
            return 0
        # A target already in sync is not rewritten, so it is not judged: nothing hand-made can be lost.
        changes = [(rel, composed, current) for rel, composed, current in plans if current != composed.data]
        refusals = [(rel, guard(current, composed, registry)) for rel, composed, current in changes]
        refusals = [(rel, reason) for rel, reason in refusals if reason]
        if refusals:
            for rel, reason in refusals:
                print("error: {}: {}".format(rel, reason))
            print("nothing was written")
            return 2
        for rel, composed, _current in changes:
            _write_target(root, rel, composed.data)
        return 0
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
