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

Write-mode guard. Before anything is written, every target's current bytes are judged (guard): a legacy
target holding a marker-like line, or a composed target holding text outside a block, a malformed,
nested, repeated or unknown marker, an edited header, or a block whose inner text no longer matches the
digest in its own marker, is refused (exit 2) with nothing written. The deliberate escape hatch is to
delete the target file, which shows in the diff, and regenerate. --check never consults the guard for
its verdict: it compares the full desired bytes with the raw bytes on disk (so a CRLF conversion is
drift), and the guard only adds a diagnosis.

Exit convention (run): 0 in sync or written; 1 drift (--check); 2 a missing, malformed or unreadable
registry, block source, corpus or target, or a write-mode refusal.

DISCLOSED RESIDUALS. The digest is not a security boundary: anyone who can edit a block and its digest,
or the sources, registry and generator together, passes the guard; diff review is the control. Writes
are neither atomic nor locked: a partial write fails --check and the guard refuses it, so the recovery
is to delete and regenerate. A legacy target still loses a hand edit on regeneration unless it holds a
marker-like line; --check still catches the edit in CI. A change to a generator's header is refused for
a composed target that already carries the old header; delete and regenerate after reviewing it.
"""
import hashlib
import os
import re
import stat
import sys
from collections import namedtuple
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    if tuple(sys.version_info[:2]) < (3, 14):
        # Reached only through an importer that carries no floor guard yet (every guarded
        # entrypoint refuses an older Python that can start it first; one that cannot start
        # it fails with Python's own error before reaching here): the version is the
        # problem, so name it.
        sys.stderr.write(
            "error: the adapter composer requires Python 3.14 or newer; this is Python %d.%d.%d "
            "(%s). Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    else:  # every Python 3.14 ships tomllib, so this installation is incomplete
        sys.stderr.write(
            "error: the adapter composer cannot import tomllib, part of the Python standard "
            "library; this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

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


def _regular_file(root, rel, what):
    """The path of rel under root, which must be a regular file with no symlink anywhere along it.
    Absent is a ComposeError (every input here is required); any other stat failure propagates as
    OSError, so an unreadable ancestor fails closed rather than reading as absent."""
    path, parts, st = Path(root), rel.split("/"), None
    for i, part in enumerate(parts):
        path = path / part
        try:
            st = os.lstat(path)
        except FileNotFoundError:
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


def load_registry(root):
    """Read and validate REGISTRY_REL under root, then read and validate every declared block source.
    Raises ComposeError (or OSError) on any failure, before any target is read."""
    root = Path(root)
    path = _regular_file(root, REGISTRY_REL, "the adapter block registry")
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
        rel, layout = row["path"], row["layout"]
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
        if b["owner"] not in OWNERS:
            raise ComposeError("{}: block {} owner {!r} is refused; only {} blocks are accepted".format(
                where, bid, b["owner"], list(OWNERS)))
        tgt = b["target"]
        if tgt not in layouts:
            raise ComposeError("{}: block {} names an unknown target {!r}".format(where, bid, tgt))
        if layouts[tgt] != "composed":
            raise ComposeError("{}: block {} is declared for {}, whose layout is {}; a block needs the "
                               "composed layout".format(where, bid, tgt, layouts[tgt]))
        if b["position"] not in POSITIONS:
            raise ComposeError("{}: block {} position {!r} is not one of {}".format(
                where, bid, b["position"], POSITIONS))
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
        if (tgt, b["position"], order) in seen_orders:
            raise ComposeError("{}: two blocks share order {} at {} of {}".format(
                where, order, b["position"], tgt))
        seen_ids.add((tgt, bid))
        seen_orders.add((tgt, b["position"], order))
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


def _exists(path):
    """Fail-closed existence probe: Path.exists() swallows EACCES, so an unreadable parent would mask a
    present target as absent. Path.stat() raises on EACCES; absent -> False."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _no_corpus(registry, outs, check):
    """No rule corpus: a legacy target left on disk is drift (--check) or an orphan to remove; a composed
    target is never deleted (exit 2, the file kept). Every target is judged before any unlink."""
    kept = [rel for rel, _h, out in outs if registry.layouts[rel] == "composed" and _exists(out)]
    if kept:
        for rel in kept:
            print("error: {} is a composed target and the rule corpus is absent; the file is kept (restore "
                  "the corpus, or review and delete the file)".format(rel))
        return 2
    drift = False
    for rel, _h, out in outs:
        if _exists(out):
            if check:
                print("drift: {} exists with no sources".format(rel))
                drift = True
            else:
                out.unlink()
    return 1 if (check and drift) else 0


def run(root, check, targets, regen, corpus_bodies):
    """The single check/write engine. targets: [(rel, header lines)]; regen: the command named on drift;
    corpus_bodies(src_dir): the ordered rule bodies. Registry, corpus and every target are read and
    composed first; --check compares raw bytes (exit 1 on any difference); write mode guards ALL targets
    before writing any, so one refusal (exit 2) writes nothing."""
    root = Path(root)
    try:
        registry = load_registry(root)
        outs = [(rel, header, root / rel) for rel, header in targets]
        # dir_present (not is_dir): an unreadable .aiqt/ parent fails closed as exit 2, never as an absent
        # corpus (which would delete the targets as orphans).
        src_dir = root.joinpath(*RULES_PARTS)
        if not dir_present(src_dir):
            return _no_corpus(registry, outs, check)
        bodies = corpus_bodies(src_dir)
        plans = []
        for rel, header, out in outs:
            composed = compose(rel, header, bodies, registry)
            plans.append((rel, out, composed, out.read_bytes() if _exists(out) else None))
        if check:
            drift = False
            for rel, _out, composed, current in plans:
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
        refusals = [(rel, guard(current, composed, registry)) for rel, _o, composed, current in plans]
        refusals = [(rel, reason) for rel, reason in refusals if reason]
        if refusals:
            for rel, reason in refusals:
                print("error: {}: {}".format(rel, reason))
            print("nothing was written")
            return 2
        for _rel, out, composed, current in plans:
            if current != composed.data:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(composed.data)
        return 0
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
