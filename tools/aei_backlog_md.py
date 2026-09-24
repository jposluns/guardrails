#!/usr/bin/env python3
"""The generic AEI reference enumerator for a markdown-checkbox backlog (GD-112/GD-164; rule grdinp).

Emits AEI v2 by default (version 2) and v1 behind --v1 (byte-identical to the shipped v1 contract, kept
for the migration test). v2 carries the close object and closed vocabulary, the human-decision prep
fields, and the optional advance hint, all from checkbox annotations.

Line grammar (one item per dash bullet; anything else is prose and ignored):
  - <ID> [x]        <title> :: close:<reason>=<ref>[ :: paths=<p1>,<p2>]  closed (v2 REQUIRES close)
  - <ID> [ |.|o|O]  <title>                             open, granted (all seed tokens mean open)
  - <ID> [BLOCKED]  <title> :: blocker:<kind>=<ref>[ :: observed=<iso>][ :: evidence=<text>]
  - ... a human-decision blocker MAY carry :: prep=complete|incomplete [ :: prep_ref=<ref> ]
  - ... an open item MAY carry :: advance=<free text> (the provider's next-step hint; display only)
  - ... any item line may end with ':: proposed' (enumerated, never compelling)

STRICT: a dash-bullet line that starts with an id-shaped token and a bracket, ANY dash-bullet bearing
a checkbox marker ([ ] [x] [X] [.] [o] [O] [BLOCKED]), or ANY line bearing a ':: blocker:' prefix, is
an enumeration ERROR (exit 3) when it fails the grammar, never a silently dropped item (so an id-less
checkbox or a malformed blocker clause can never shrink the open-set the stop guard trusts). A backlog
that yields ZERO items is an enumeration ERROR (exit 3), NOT an empty enumeration, UNLESS it is a valid
affirmed-empty backlog: a file whose ONLY non-blank line is the column-0 sentinel line
'<!-- aei: empty backlog -->' (internal whitespace tolerated, the hyphen spelling rejected; blank lines
allowed). Every other non-blank line is operative content and fails closed: a heading, a comment
carrying content, a whole-line comment holding operative text, a markdown table, an alternate bullet, a
fenced example, indented code, prose, or an indented/quoted sentinel that is not at column 0. So an
unrecognized backlog can never read as a drained/empty actionable set, and a sentinel can never mask
unrecognized work (whether beside it or masquerading as it). Emptiness is AFFIRMED by the column-0
sentinel, never inferred from absence. An unreadable backlog is an error. The enumerator reads the real
file; it accepts no item list from its caller.

Close vocabulary: fixed, no-fix-needed, not-buildable, maintainer-deferred. Under v2 a [x] line MUST
carry a close annotation with a known reason and a non-blank ref, a human-decision blocker MUST carry
prep=complete|incomplete, and prep=complete MUST carry a non-blank prep_ref; a violation is an
enumeration ERROR (exit 3), never a silently dropped or under-specified item (grdinp: the guard's
input can never shrink the open-set or fabricate a ground). Under --v1 close/prep/advance annotations
are ignored and the v1 shape is emitted unchanged. A `fixed` close MAY carry a trailing
':: paths=<comma-separated repo-relative paths>' list, parsed into close.paths (the same field the JSON
enumeration and the liveness join read); paths containing a ':' are the disclosed grammar residual.

STAGE MAPPING NOTE (adopter-internal, NEVER in shipped rule/hook text): an adopter fleet maps its own
pipeline stages onto open-plus-advance-hint here and in migration documentation only. The pack ships
no stage vocabulary; the advance hint is opaque free text.

  aei_backlog_md.py --backlog PATH --aei          emit AEI v2 JSON on stdout
  aei_backlog_md.py --backlog PATH --aei --v1     emit AEI v1 JSON (migration/regression)
  aei_backlog_md.py --self-test                   grammar and fail-closed vectors
"""
import datetime
import hashlib
import json
import re
import sys
import unicodedata

ITEM_RE = re.compile(r"^-\s+(?P<id>[A-Za-z][A-Za-z0-9_.-]*)\s+"
                     r"\[(?P<tok>x|X| |\.|o|O|BLOCKED)\]\s+(?P<rest>.+?)\s*$")
CANDIDATE_RE = re.compile(r"^-\s+\S+\s+\[")
CHECKBOX_RE = re.compile(r"^-\s+.*\[(?:x|X| |\.|o|O|BLOCKED)\]")
BLOCKER_PREFIX_RE = re.compile(r"::\s*blocker:")
BLOCKER_RE = re.compile(r"::\s*blocker:(?P<kind>[a-z-]+)=(?P<ref>\S+)")
OBSERVED_RE = re.compile(r"::\s*observed=(?P<t>\S+)")
EVIDENCE_RE = re.compile(r"::\s*evidence=(?P<e>[^:]+?)(?:\s*::|$)")
# FIX B: the close annotation and its OPTIONAL trailing paths list are ONE anchored unit at END of line,
# so ':: paths=' is read as close.paths ONLY when it immediately follows the close (never smuggled from an
# earlier ':: paths=' in the title). ref is non-greedy so the optional paths group binds; a path containing
# a ':' truncates at that ':' (the disclosed grammar residual, unchanged).
CLOSE_TAIL_RE = re.compile(
    r"::\s*close:(?P<reason>[a-z-]+)=(?P<ref>\S+?)"
    r"(?:\s*::\s*paths=(?P<paths>[^:]+?))?\s*$")
PREP_RE = re.compile(r"::\s*prep=(?P<v>complete|incomplete)\b")
PREP_REF_RE = re.compile(r"::\s*prep_ref=(?P<r>\S+)")
ADVANCE_RE = re.compile(r"::\s*advance=(?P<a>[^:]+?)(?:\s*::|$)")
KINDS = ("tracked-task", "human-decision", "external", "foreign-lease", "not-before")
CLOSE_REASONS = ("fixed", "no-fix-needed", "not-buildable", "maintainer-deferred")

# FIX A (round 8): removing ONLY U+FEFF (the round-7 lstrip) left every other invisible able to
# un-anchor a bullet and silently drop the item -> an empty enumeration -> a manufactured stop. grdinp:
# do NOT enumerate one codepoint. The whole Unicode format class (category Cf) covers U+FEFF, U+200B-200F,
# U+2060-2064, U+00AD, the bidi controls, etc.; the explicit set is a safety net for a runtime whose table
# does not yet class U+180E / U+200C / U+200D as Cf. C1 (round 9): the strip normalizes only the
# STRUCTURAL prefix (to anchor the bullet); the TITLE is recovered verbatim from the original line via the
# offset map, so a legitimate ZWJ/ZWNJ inside a title (an emoji ZWJ sequence, or a script that uses ZWNJ/
# ZWJ for shaping/spelling) is PRESERVED rather than laundered out of visible content. A legitimate ASCII
# line has no Cf char, yields an identity map, and is returned byte-identical.
_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\u180e\ufeff")

# A zero-item enumeration is VALID only when the file is an affirmed-empty backlog: a file whose ONLY
# non-blank line is the column-0 sentinel below (blank lines allowed). Every OTHER non-blank line is
# operative content the grammar did not recognize (a heading, a comment carrying content, a markdown
# table, an alternate bullet, a fenced example, indented code, prose, or an indented/quoted sentinel not
# at column 0) and fails closed as a cannot-evaluate error (exit 3), so a sentinel can never mask
# unrecognized work as a silently drained backlog (rule grdinp, check-fails-closed-on-unreadable). The
# sentinel is a markdown/HTML comment matched against ln.rstrip(), so trailing whitespace is tolerated but
# LEADING whitespace is NOT (a column-0 sentinel affirms, an indented one is content). Internal whitespace
# between 'empty' and 'backlog' is tolerated (one or more spaces or a tab), but the hyphen spelling
# 'empty-backlog' is REJECTED.
SENTINEL_RE = re.compile(r"^<!--\s*aei:\s*empty\s+backlog\s*-->$")

# These characters are line boundaries to str.splitlines and to Unicode but are NOT physical newlines, so
# they can smuggle a second item onto one physical line or embed content inside a sentinel; reject them
# rather than guess. Ordinary tab (U+0009), space, CR, and LF are NOT in the set and are never rejected.
SEPARATOR_CHARS = frozenset("\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


def _physical_lines(text):
    """Split on physical line endings only (CR, LF, CRLF), never Unicode line separators or control
    characters, so a separator embedded in a line cannot create a phantom column-0 line."""
    return re.split(r"\r\n|\r|\n", text)


def _strip_invisibles_mapped(line):
    """(stripped, offsets): the line with every zero-width / Cf format char removed (so a bullet led or
    interleaved by one still ANCHORS), and offsets[i] = the index in the ORIGINAL line of stripped[i],
    with a trailing sentinel offsets[len(stripped)] == len(line). The offset map lets parse() recover the
    TITLE verbatim from the original line (C1), so a legitimate ZWJ/ZWNJ inside a title is preserved while
    the structural prefix is still normalized to anchor the bullet."""
    kept, offsets = [], []
    for idx, ch in enumerate(line):
        if ch in _ZERO_WIDTH or unicodedata.category(ch) == "Cf":
            continue
        kept.append(ch)
        offsets.append(idx)
    offsets.append(len(line))
    return "".join(kept), offsets


def _ref_unsafe(s):
    """True when a verdict-bearing span is NOT entirely safe graphic-ASCII (every char in U+0021..U+007E).
    Round 11 (FIX 5): an ASCII-graphic ALLOWLIST replaces the r10 zero-width/Cf DENYLIST and is EXTENDED to
    the prep_ref and EACH close.paths entry. The close/blocker ref, prep_ref, and paths grammars all match
    `\\S+` / `[^:]+` (any non-whitespace, non-colon), and a variation selector (U+FE00-FE0F, U+E0100-E01EF),
    CGJ (U+034F), a Hangul filler, a Braille blank, or any other invisible or non-ASCII char is
    non-whitespace, so the r10 denylist (category Cf plus a fixed zero-width set) let all of those launder a
    distinct ref/path into a valid close, blocker, prep, or paths verdict (fail-OPEN). A verdict-bearing span
    reconciled against the repository or the backlog must be pure graphic-ASCII; anything else is an
    enumeration error (exit 3). The display-only TITLE is NOT passed through this predicate and keeps its
    invisibles verbatim (C1). This is separate from _strip_invisibles_mapped, which normalizes only the
    STRUCTURAL bullet prefix and still uses _ZERO_WIDTH. Mirrors the engine _orch_ref_unsafe."""
    return not all("\u0021" <= ch <= "\u007e" for ch in s)


def _text_unsafe(s):
    """True when a verdict-bearing FREE-TEXT span (a blocker's evidence text) is NOT entirely printable
    ASCII (every char in U+0020..U+007E). Round 12: the _ref_unsafe allowlist widened only by the ordinary
    space, since evidence is prose; an invisible, a variation selector, a combining mark, a filler, or any
    other non-ASCII char is an enumeration error (exit 3) rather than evidence that merely looks present.
    Mirrors the engine _orch_text_unsafe."""
    return not all("\u0020" <= ch <= "\u007e" for ch in s)


def parse(text, version=2):
    """(items, errors). Errors are strings; any error means the enumeration must not be emitted. Under
    version 2 the close/prep annotations are strict; under version 1 they are ignored and the v1 item
    shape is produced byte-identically."""
    items, errors, seen = [], [], set()
    for n, line in enumerate(_physical_lines(text), 1):
        # FIX A (round 8) + C1 (round 9): normalize the STRUCTURAL region so a bullet led or interleaved by
        # an invisible still anchors (a laundered bullet re-anchors instead of being dropped to prose -> an
        # empty enumeration -> a manufactured stop), but recover the TITLE verbatim from the ORIGINAL line
        # so a legitimate ZWJ/ZWNJ inside a title is PRESERVED rather than corrupted. The whole-line strip
        # anchored the bullet but laundered visible title content; the offset map separates the two.
        stripped_full, offsets = _strip_invisibles_mapped(line)
        stripped = stripped_full.strip()
        if not (CANDIDATE_RE.match(stripped) or CHECKBOX_RE.match(stripped)
                or BLOCKER_PREFIX_RE.search(stripped)):
            continue
        m = ITEM_RE.match(stripped)
        if not m:
            errors.append("line {}: item-shaped line fails the grammar: {!r}".format(
                n, line.strip()[:120]))
            continue
        iid, tok = m.group("id"), m.group("tok")
        # C1: id/token anchor off the normalized line, but the TITLE (rest) is taken VERBATIM from the
        # ORIGINAL line at the matched offset. The leading whitespace .strip() removed is re-added via
        # `lead`; rstrip() mirrors ITEM_RE's trailing `\s*$`. An invisible that survives inside a close/
        # blocker annotation makes that annotation fail its grammar (fail-closed, exit 3), never a silent
        # laundering into validity (grdinp).
        lead = len(stripped_full) - len(stripped_full.lstrip())
        # C1 + r10 FIX 5(a): derive the title start from the END of the structural separator (the last
        # whitespace of `- <ID> [tok] `), one past it in the ORIGINAL line, so any invisible between the
        # checkbox and the first VISIBLE title char is RETAINED verbatim. Mapping via m.start("rest")
        # pointed at the first RETAINED char, dropping a stripped title-leading invisible.
        rest = line[offsets[lead + m.start("rest") - 1] + 1:].rstrip()
        if iid in seen:
            errors.append("line {}: duplicate id {}".format(n, iid))
            continue
        seen.add(iid)
        proposed = bool(re.search(r"::\s*proposed\s*$", rest))
        rest = re.sub(r"\s*::\s*proposed\s*$", "", rest)
        close = None
        cm = CLOSE_TAIL_RE.search(rest)
        if cm:
            if cm.group("reason") not in CLOSE_REASONS:
                errors.append("line {}: unknown close reason {!r}".format(n, cm.group("reason")))
                continue
            if _ref_unsafe(cm.group("ref")):
                errors.append("line {}: close ref carries a non graphic-ASCII char".format(n))
                continue
            close = {"reason": cm.group("reason"), "ref": cm.group("ref")}
            # B3/FIX2/FIX B: an OPTIONAL '::' paths list (repo-relative, comma-separated) binds a `fixed`
            # close to the item BY CONTENT using the SAME field the JSON validator and the liveness join
            # read (close.paths), so a routed fixed close RE-ENUMERATES as terminal. It is parsed ONLY from
            # the anchored close-tail unit (the paths immediately following the close at END of line), never
            # from a ':: paths=' earlier in the title, which stays literal title text and cannot smuggle a
            # verdict-bearing path into the close.
            if cm.group("paths"):
                paths = [p.strip() for p in cm.group("paths").split(",") if p.strip()]
                if any(_ref_unsafe(p) for p in paths):
                    errors.append("line {}: close.paths entry carries a non graphic-ASCII char".format(n))
                    continue
                if paths:
                    close["paths"] = paths
            rest = rest[:cm.start()].rstrip()
        advance = None
        am = ADVANCE_RE.search(rest)
        if am:
            advance = am.group("a").strip()
            rest = re.sub(r"\s*::\s*advance=[^:]+", "", rest).rstrip()
        blocker = None
        blocker_prefix = BLOCKER_PREFIX_RE.search(rest)
        bm = BLOCKER_RE.search(rest)
        prep_complete = None
        prep_ref = None
        if blocker_prefix and not bm:
            errors.append("line {}: blocker clause fails the grammar: {!r}".format(
                n, rest[blocker_prefix.start():][:120]))
            continue
        if bm:
            if bm.group("kind") not in KINDS:
                errors.append("line {}: unknown blocker kind {!r}".format(n, bm.group("kind")))
                continue
            if _ref_unsafe(bm.group("ref")):
                errors.append("line {}: blocker ref carries a non graphic-ASCII char".format(n))
                continue
            blocker = {"kind": bm.group("kind"), "ref": bm.group("ref")}
            om = OBSERVED_RE.search(rest)
            if om:
                blocker["observed_at_utc"] = om.group("t")
            em = EVIDENCE_RE.search(rest)
            if em:
                if _text_unsafe(em.group("e").strip()):
                    errors.append("line {}: blocker evidence carries a non printable-ASCII char".format(n))
                    continue
                blocker["evidence"] = em.group("e").strip()
            pm = PREP_RE.search(rest)
            prm = PREP_REF_RE.search(rest)
            if pm:
                prep_complete = (pm.group("v") == "complete")
            if prm:
                if _ref_unsafe(prm.group("r")):
                    errors.append("line {}: prep_ref carries a non graphic-ASCII char".format(n))
                    continue
                prep_ref = prm.group("r")
            rest = rest[:bm.start()].rstrip()
        elif tok == "BLOCKED":
            pass  # a bare BLOCKED marker enumerates open-with-no-blocker (classified actionable)
        state = "closed" if tok.lower() == "x" else ("proposed" if proposed else "open")

        if version == 2:
            if state == "closed" and close is None:
                errors.append("line {}: v2 closed item {} has no :: close:<reason>=<ref> annotation"
                              .format(n, iid))
                continue
            if state != "closed" and close is not None:
                errors.append("line {}: v2 non-closed item {} carries a close annotation".format(n, iid))
                continue
            if blocker and blocker["kind"] == "human-decision":
                if prep_complete is None:
                    errors.append("line {}: v2 human-decision item {} lacks :: prep=complete|incomplete"
                                  .format(n, iid))
                    continue
                if prep_complete and not prep_ref:
                    errors.append("line {}: v2 human-decision item {} is prep=complete but has no "
                                  ":: prep_ref=<ref>".format(n, iid))
                    continue

        item = {"id": iid, "title": rest, "state": state,
                "granted": not proposed, "blocker": blocker}
        if version == 2:
            if close is not None:
                item["close"] = close
            if blocker and blocker["kind"] == "human-decision" and prep_complete is not None:
                blocker["prep_complete"] = prep_complete
                if prep_complete and prep_ref:
                    blocker["prep_ref"] = prep_ref
            if advance:
                item["advance"] = advance
        items.append(item)
    return items, errors


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if "--backlog" not in argv or "--aei" not in argv:
        print("usage: aei_backlog_md.py --backlog PATH --aei [--v1] | --self-test", file=sys.stderr)
        return 2
    version = 1 if "--v1" in argv else 2
    path = argv[argv.index("--backlog") + 1]
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        print("enumerator error: backlog unreadable: {}".format(exc), file=sys.stderr)
        return 3
    # STRICT decode: invalid UTF-8 is a cannot-evaluate (exit 3), never silently replaced and its line
    # dropped, which would lose a task. The raw bytes are still hashed for the revision below.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print("enumerator error: backlog is not valid UTF-8: {}: {}".format(path, exc), file=sys.stderr)
        return 3
    # Reject any nonphysical line-boundary / separator control character before parsing or the
    # empty-validation, so it protects BOTH the item grammar (a separator cannot smuggle a second item
    # onto one physical line) and the sentinel path (a separator cannot embed content inside a sentinel).
    if SEPARATOR_CHARS.intersection(text):
        print("enumerator error: backlog contains a nonphysical line-boundary or separator control "
              "character (VT, FF, FS, GS, RS, NEL, LINE/PARAGRAPH SEPARATOR): {}".format(path),
              file=sys.stderr)
        return 3
    items, errors = parse(text, version=version)
    if errors:
        for e in errors:
            print("enumerator error: " + e, file=sys.stderr)
        return 3
    if not items:
        # A zero-item backlog is valid only when it affirms emptiness with the sentinel and carries no
        # operative content: every non-blank line must be a column-0 sentinel line (blank lines allowed).
        # Any other non-blank line is operative content the grammar did not recognize: fail closed.
        nonblank = [ln for ln in _physical_lines(text) if ln.strip()]
        others = [ln for ln in nonblank if not SENTINEL_RE.match(ln.rstrip())]
        if others:
            print("enumerator error: backlog has content but zero items were recognized; the grammar "
                  "is a dash-bullet checkbox line (e.g. '- <ID> [ ] <title>', tokens [ |.|o|O|x|BLOCKED]); "
                  "a table, heading, comment, indented block, or other line is unrecognized content. Express "
                  "the items in the dash-bullet grammar, or reduce the file to only the sentinel line to "
                  "declare an empty backlog.", file=sys.stderr)
            return 3
        if not nonblank:
            print("enumerator error: backlog is empty but does not affirm emptiness; a valid empty backlog "
                  "is a file whose only non-blank line is the sentinel '<!-- aei: empty backlog -->' "
                  "(emptiness is affirmed, never inferred from absence).", file=sys.stderr)
            return 3
        # Every non-blank line is a column-0 sentinel: a valid affirmed-empty backlog.
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps({"version": version, "generated_at_utc": now,
                      "source": {"locator": path,
                                 "revision": "sha256:" + hashlib.sha256(raw).hexdigest(),
                                 "observed_at_utc": now},
                      "items": [{k: v for k, v in it.items() if v is not None}
                                for it in items]}))
    return 0


def self_test():
    import subprocess
    import tempfile
    from pathlib import Path
    ok_fail = []
    v2sample = (
        "# backlog\n"
        "- A-1 [ ] first open\n"
        "- A-2 [.] seeded open :: advance=synthesize the plan\n"
        "- A-5 [x] done :: close:no-fix-needed=R-1\n"
        "- A-6 [x] shipped :: close:fixed=commit-abc\n"
        "- B-1 [BLOCKED] waiting :: blocker:external=ci-42 :: observed=2026-08-29T00:00:00+00:00\n"
        "- H-1 [ ] decision :: blocker:human-decision=PD-9 :: prep=complete :: prep_ref=plan.md\n"
        "- H-2 [ ] decision :: blocker:human-decision=PD-8 :: prep=incomplete\n"
        "- P-1 [ ] a proposal :: proposed\n")
    items, errors = parse(v2sample, version=2)
    a5 = next((i for i in items if i["id"] == "A-5"), {})
    h1 = next((i for i in items if i["id"] == "H-1"), {})
    h2 = next((i for i in items if i["id"] == "H-2"), {})
    a2 = next((i for i in items if i["id"] == "A-2"), {})
    good = (not errors and len(items) == 8
            and a5.get("close") == {"reason": "no-fix-needed", "ref": "R-1"}
            and next(i for i in items if i["id"] == "A-6")["close"]["reason"] == "fixed"
            and h1.get("blocker", {}).get("prep_complete") is True
            and h1.get("blocker", {}).get("prep_ref") == "plan.md"
            and h2.get("blocker", {}).get("prep_complete") is False
            and a2.get("advance") == "synthesize the plan"
            and next(i for i in items if i["id"] == "P-1")["state"] == "proposed")
    if not good:
        ok_fail.append("v2 happy-path parse mismatch")
    # v2 fail-closed cases -> exit 3
    bad = (
        "- X-1 [x] closed with no close annotation\n",          # closed w/o close
        "- X-2 [x] a :: close:wont-fix=R\n",                    # unknown close reason
        "- X-3 [ ] a :: close:fixed=R\n",                       # close on a non-closed item
        "- X-4 [ ] d :: blocker:human-decision=PD :: prep=complete\n",  # complete, no prep_ref
        "- X-5 [ ] d :: blocker:human-decision=PD\n",           # human-decision, no prep
    )
    with tempfile.TemporaryDirectory(prefix="aiqt-aei2-") as tmp:
        for i, text in enumerate(bad):
            p = Path(tmp) / "b-{}.md".format(i)
            p.write_text(text, encoding="utf-8")
            r = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                                "--backlog", str(p), "--aei"], capture_output=True, timeout=30)
            if r.returncode != 3:
                ok_fail.append("v2 fail-closed case {} should exit 3, got {}".format(i, r.returncode))
        # --v1 ignores close/prep annotations and emits v1 shape (migration)
        p = Path(tmp) / "v1.md"
        p.write_text("- A-5 [x] done :: close:no-fix-needed=R-1\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                            "--backlog", str(p), "--aei", "--v1"], capture_output=True,
                           text=True, timeout=30)
        payload = json.loads(r.stdout) if r.returncode == 0 else {}
        if r.returncode != 0 or payload.get("version") != 1 \
                or any("close" in it for it in payload.get("items", [])):
            ok_fail.append("--v1 should emit version 1 with no close field")
    empty_items, empty_errs = parse("", version=2)
    if empty_items != [] or empty_errs:
        ok_fail.append("empty backlog should be a valid empty enumeration")
    # FIX A (round 7): a line-leading BOM (U+FEFF) never silently drops an item. str.strip does not remove
    # U+FEFF, so a BOM leading ANY line (byte 0, or the first line after a header) must be stripped per
    # line before the bullet screen, or the item is skipped as prose -> an empty enumeration -> a
    # manufactured stop. A mid-content U+FEFF stays literal.
    hdr_bom = "# header\n\ufeff- A-1 [x] done :: close:no-fix-needed=R-1\n"
    hb_items, hb_errs = parse(hdr_bom, version=2)
    hb1 = next((i for i in hb_items if i["id"] == "A-1"), {})
    if hb_errs or hb1.get("state") != "closed" \
            or hb1.get("close") != {"reason": "no-fix-needed", "ref": "R-1"}:
        ok_fail.append("a BOM before a LATER bullet line must enumerate the item, not drop it "
                       "(got items={} errs={})".format(hb_items, hb_errs))
    b0_bom = "\ufeff- A-2 [x] done :: close:fixed=commit-z :: paths=tools/x.py\n"
    b0_items, b0_errs = parse(b0_bom, version=2)
    b0 = next((i for i in b0_items if i["id"] == "A-2"), {})
    if b0_errs or b0.get("state") != "closed" \
            or b0.get("close", {}).get("paths") != ["tools/x.py"]:
        ok_fail.append("a byte-0 BOM before the first bullet must enumerate the item "
                       "(got items={} errs={})".format(b0_items, b0_errs))
    # FIX A (round 8): a line led by ANY invisible (not only U+FEFF) still enumerates the item; the
    # round-7 lstrip("\ufeff") dropped every other zero-width/format char to prose. Removing the whole Cf
    # class (plus the explicit set) closes the class by construction. Every fixture uses a Python ESCAPE.
    for _label, _lead in (("zwsp", "\u200b"), ("word-joiner", "\u2060"),
                          ("zwnj", "\u200c"), ("mongolian-vs", "\u180e")):
        _src = _lead + "- A-1 [x] done :: close:no-fix-needed=R-1\n"
        _it = next((i for i in parse(_src, version=2)[0] if i["id"] == "A-1"), {})
        if _it.get("state") != "closed":
            ok_fail.append("a line led by {} must enumerate the item, not drop it (got {})"
                           .format(_label, _it))
    # interleaved BOM/space before the dash, and a BOM AFTER the dash, both still enumerate.
    _inter = next((i for i in parse(
        "\ufeff \ufeff- A-1 [x] done :: close:no-fix-needed=R-1\n", version=2)[0]
        if i["id"] == "A-1"), {})
    if _inter.get("state") != "closed":
        ok_fail.append("an interleaved BOM/space lead must enumerate the item (got {})".format(_inter))
    _after = next((i for i in parse(
        "-\ufeff A-2 [x] done :: close:fixed=commit-z :: paths=tools/x.py\n", version=2)[0]
        if i["id"] == "A-2"), {})
    if _after.get("state") != "closed" or _after.get("close", {}).get("paths") != ["tools/x.py"]:
        ok_fail.append("a BOM after the dash must still enumerate the item (got {})".format(_after))
    # a legitimate ASCII line is unchanged (no Cf char removed).
    _plain = next((i for i in parse("- A-7 [ ] plain title\n", version=2)[0]
                   if i["id"] == "A-7"), {})
    if _plain.get("title") != "plain title":
        ok_fail.append("a legitimate ASCII line must be unchanged (got {!r})".format(_plain.get("title")))
    # C1 (round 9): a mid-content invisible in the TITLE is now PRESERVED verbatim (the whole-line strip
    # corrupted legitimate ZWJ/ZWNJ; only the STRUCTURAL prefix is normalized). This REPLACES the round-8
    # "mid-content invisible removed" assertion, which corrupted visible title content.
    _mid = next((i for i in parse("- A-3 [ ] ti\ufefftle stays\u200bvisible\n", version=2)[0]
                 if i["id"] == "A-3"), {})
    if _mid.get("title") != "ti\ufefftle stays\u200bvisible":
        ok_fail.append("a mid-content title invisible must be preserved verbatim (got {!r})"
                       .format(_mid.get("title")))
    # C1: a legitimate ZWJ emoji sequence in the title is preserved BYTE-FOR-BYTE. U+1F469 U+200D U+1F4BB
    # is the "woman technologist" grapheme; the whole-line Cf strip removed the ZWJ and split it into two
    # glyphs. The structural strip still anchors the bullet; the title is recovered from the original line.
    _zwj = next((i for i in parse("- I-1 [ ] \U0001f469\u200d\U0001f4bb release\n", version=2)[0]
                 if i["id"] == "I-1"), {})
    if _zwj.get("title") != "\U0001f469\u200d\U0001f4bb release":
        ok_fail.append("a title ZWJ sequence must be preserved verbatim (got {!r})"
                       .format(_zwj.get("title")))
    # a LEADING invisible still anchors the item WHILE the ZWJ title is preserved (both invariants hold).
    _zwj_lead = next((i for i in parse(
        "\u200b- I-2 [ ] \U0001f469\u200d\U0001f4bb ok\n", version=2)[0] if i["id"] == "I-2"), {})
    if _zwj_lead.get("state") != "open" or _zwj_lead.get("title") != "\U0001f469\u200d\U0001f4bb ok":
        ok_fail.append("a leading invisible must anchor the item AND preserve a ZWJ title (got {!r})"
                       .format(_zwj_lead))
    # FIX2/B3: a `fixed` close MAY carry a '::' paths list, parsed into close.paths (the same field the
    # JSON validator and the join read), so a routed fixed close re-enumerates as terminal.
    pitems, perrs = parse("- A-9 [x] shipped :: close:fixed=commit-z :: paths=tools/x.py,tools/y.py\n",
                          version=2)
    a9 = next((i for i in pitems if i["id"] == "A-9"), {})
    if perrs or a9.get("close") != {"reason": "fixed", "ref": "commit-z",
                                    "paths": ["tools/x.py", "tools/y.py"]}:
        ok_fail.append("v2 fixed close with a paths list must parse close.paths (got {})".format(a9))
    # a path-less fixed close still parses with NO paths key (byte-identical to before)
    p6 = next((i for i in parse("- A-6 [x] s :: close:fixed=commit-abc\n", version=2)[0]
               if i["id"] == "A-6"), {})
    if "paths" in p6.get("close", {}):
        ok_fail.append("a path-less fixed close must not synthesize close.paths")
    # FIX B: a ':: paths=' that appears in the TITLE (not immediately part of the close annotation) is NOT
    # parsed as close.paths and cannot smuggle a verdict-bearing path; it stays literal title text.
    smug = next((i for i in parse(
        "- A-1 [x] title :: paths=tools/x.py :: close:fixed=abc\n", version=2)[0]
        if i["id"] == "A-1"), {})
    if "paths" in smug.get("close", {}):
        ok_fail.append("a ':: paths=' in the title must NOT yield close.paths (title-smuggle)")
    if "paths=tools/x.py" not in smug.get("title", ""):
        ok_fail.append("a misplaced ':: paths=' must remain literal title text (title-smuggle)")
    # a correctly-placed close paths (immediately after the close) still parses close.paths.
    good_place = next((i for i in parse(
        "- A-2 [x] t :: close:fixed=def :: paths=tools/z.py\n", version=2)[0]
        if i["id"] == "A-2"), {})
    if good_place.get("close", {}).get("paths") != ["tools/z.py"]:
        ok_fail.append("a correctly-placed close paths must parse close.paths (got {})".format(good_place))
    # r10 FIX 5(a): a title-LEADING invisible is now RETAINED verbatim (the r9 offset pointed at the first
    # RETAINED char, dropping a stripped invisible at the title boundary). Python ESCAPE, no literal char.
    _tl = next((i for i in parse("- A-1 [ ] \u200dtitle\n", version=2)[0] if i["id"] == "A-1"), {})
    if _tl.get("title") != "\u200dtitle":
        ok_fail.append("a title-leading invisible must be retained verbatim (got {!r})"
                       .format(_tl.get("title")))
    # r10 FIX 5(b): a Cf/zero-width char in a verdict-bearing CLOSE or BLOCKER ref FAILS CLOSED (an error
    # is recorded; main() exits 3), never laundered into a valid close/blocker.
    if not parse("- A-1 [x] done :: close:no-fix-needed=R-\u200b1\n", version=2)[1]:
        ok_fail.append("an invisible-bearing close ref must fail closed (record an error)")
    if not parse("- A-1 [BLOCKED] wait :: blocker:external=ci-\u200b42\n", version=2)[1]:
        ok_fail.append("an invisible-bearing blocker ref must fail closed (record an error)")
    # r11 FIX 5: the r10 zero-width/Cf DENYLIST is REPLACED by an ASCII-graphic ALLOWLIST, extended to the
    # prep_ref and EACH close.paths entry, and now catching a VARIATION SELECTOR (U+FE0F) the denylist
    # missed. Every fixture uses a Python ESCAPE, never a literal invisible byte.
    if not parse("- A-1 [x] done :: close:no-fix-needed=R\ufe0f1\n", version=2)[1]:
        ok_fail.append("a VS-bearing close ref must fail closed (allowlist)")
    if not parse("- A-1 [BLOCKED] wait :: blocker:external=ci\ufe0f42\n", version=2)[1]:
        ok_fail.append("a VS-bearing blocker ref must fail closed (allowlist)")
    if not parse("- H-3 [ ] d :: blocker:human-decision=PD :: prep=complete :: prep_ref=plan\ufe0f.md\n",
                 version=2)[1]:
        ok_fail.append("a VS-bearing prep_ref must fail closed (allowlist)")
    if not parse("- H-4 [ ] d :: blocker:human-decision=PD :: prep=complete :: prep_ref=plan\u200b.md\n",
                 version=2)[1]:
        ok_fail.append("a ZWSP-bearing prep_ref must fail closed (allowlist)")
    if not parse("- A-1 [x] done :: close:fixed=abc :: paths=tools/gen_hooks.py\ufe0f\n", version=2)[1]:
        ok_fail.append("a VS-bearing close.paths entry must fail closed (allowlist)")
    if not parse("- A-1 [x] done :: close:fixed=abc :: paths=tools/gen_hooks.py\u200b\n", version=2)[1]:
        ok_fail.append("a ZWSP-bearing close.paths entry must fail closed (allowlist)")
    # r12: blocker EVIDENCE is free text but verdict-bearing (it substantiates a block), so it takes the
    # printable-ASCII allowlist: an invisible-only or look-alike evidence fails closed, plain prose passes.
    if not parse("- B-1 [BLOCKED] w :: blocker:external=ci-42 :: evidence=\u200b\n", version=2)[1]:
        ok_fail.append("a ZWSP-only evidence must fail closed (allowlist)")
    if not parse("- B-1 [BLOCKED] w :: blocker:external=ci-42 :: evidence=run\ufe0f pending\n",
                 version=2)[1]:
        ok_fail.append("a VS-bearing evidence must fail closed (allowlist)")
    _ev = parse("- B-1 [BLOCKED] w :: blocker:external=ci-42 :: evidence=run 42 pending\n", version=2)
    if _ev[1] or _ev[0][0]["blocker"].get("evidence") != "run 42 pending":
        ok_fail.append("a printable-ASCII evidence must pass verbatim (got {})".format(_ev))
    _clean = parse("- A-1 [x] done :: close:fixed=commit-abc123 :: paths=tools/gen_hooks.py,a/b.py\n",
                   version=2)
    if _clean[1] or _clean[0][0].get("close", {}).get("paths") != ["tools/gen_hooks.py", "a/b.py"]:
        ok_fail.append("a clean graphic-ASCII close ref+paths must pass (got {})".format(_clean))
    # The v1 grammar and the main-level fail-closed vectors (the affirmed-empty sentinel, the
    # nonphysical-separator and strict-UTF-8 rejections). A closed fixture line carries a v2 close
    # annotation, since main() emits v2 by default and a bare [x] is a v2 error.
    sample = ("# backlog\n"
              "- A-1 [ ] first open\n"
              "- A-2 [.] seeded open\n"
              "- A-3 [o] planned open\n"
              "- A-4 [O] ready open\n"
              "- A-5 [x] done\n"
              "- B-1 [BLOCKED] waiting :: blocker:external=ci-42 :: "
              "observed=2026-08-29T00:00:00+00:00 :: evidence=run pending\n"
              "- B-2 [BLOCKED] bare blocked, no proof\n"
              "- P-1 [ ] a proposal :: proposed\n")
    v1_items, v1_errors = parse(sample, version=1)
    v1_ok = (not v1_errors and len(v1_items) == 8
          and sum(1 for i in v1_items if i["state"] == "open" and i["granted"]) == 6
          and next(i for i in v1_items if i["id"] == "A-5")["state"] == "closed"
          and next(i for i in v1_items if i["id"] == "B-1")["blocker"]["kind"] == "external"
          and next(i for i in v1_items if i["id"] == "B-2")["blocker"] is None
          and next(i for i in v1_items if i["id"] == "P-1")["state"] == "proposed")
    _items, errs2 = parse("- badline [Q] unknown token\n", version=1)
    _items3, errs3 = parse("- D-1 [ ] a\n- D-1 [ ] b\n", version=1)
    v1_empty_items, v1_empty_errs = parse("", version=1)
    # id-less checkboxes and malformed blocker clauses must ERROR (exit 3), never drop to an empty set.
    malformed = ("- [ ] ship\n", "- [x] done\n", "- [.] idless seed\n", "- [O] idless ready\n",
                 "A :: blocker:EXTERNAL=ci-42\n", "A :: blocker:external_foo=ci\n")
    # FIX (main-level, exit code + JSON, exercised via subprocess): (a) a recognized dash-bullet backlog
    # enumerates unchanged; (b) a valid affirmed-empty backlog is a file whose ONLY non-blank line is the
    # column-0 sentinel (blank lines allowed), tolerating internal whitespace (spaces or a tab) but NOT the
    # hyphen spelling: exit 0, empty items; (c) EVERY other non-blank line is operative content and fails
    # closed (exit 3, no drained-empty JSON), WHETHER OR NOT the sentinel is also present: a heading, a
    # whole-line comment carrying content, a comment masking work between comment markers, a 4-space code
    # block, a unicode-prefixed line, a table, an alternate bullet, a fenced example, or prose, and an
    # indented (non-column-0) sentinel is itself content, not an affirmation; (d) an unaffirmed
    # empty/whitespace-only/comment-only file fails closed (exit 3); (e) a malformed line stays the
    # existing error (exit 3).
    table = "| ID | State |\n| --- | --- |\n| A-1 | open |\n| A-2 | open |\n"
    sentinel = "<!-- aei: empty backlog -->\n"
    recognized = "# backlog\n- A-1 [ ] first\n- A-2 [x] done :: close:fixed=c1\n"
    # (b) exit-0 affirmations: bare column-0 sentinel, the same with blank lines, internal-whitespace
    # variants alone (spaces or a tab between 'empty' and 'backlog'), and two sentinels on separate
    # PHYSICAL lines (one or more column-0 sentinel lines is a valid empty backlog).
    affirm_cases = (sentinel,
                    "\n" + sentinel + "\n",
                    "<!-- aei: empty  backlog -->\n",
                    "<!-- aei: empty\tbacklog -->\n",
                    sentinel + sentinel)
    # (c)+(d) exit-3 fail-closed: table/empty/whitespace/comment-only with no sentinel; the sentinel PLUS
    # operative content (a heading, a table, a '*'-bullet, a fenced example, prose, a comment masking work
    # between markers, a 4-space code block, or a unicode-prefixed heading), which the sentinel can never
    # mask; a bare heading + sentinel; an indented (non-column-0) sentinel alone; the hyphen spelling; and a
    # sentinel that is column-0 only because a Unicode line separator or control character precedes it on
    # ONE physical line (U+2028, U+2029, U+0085/NEL, form feed, vertical tab, U+001C-U+001E, and
    # indent+form-feed). Splitting on physical newlines keeps the separator INSIDE the line, so its leading
    # non-space breaks the ^-anchored sentinel match: the line is operative content, never a phantom
    # column-0 sentinel that would bypass the check.
    failclose_cases = (table, "", "   \n\n", "<!-- other note -->\n",
                       "# Backlog\n" + sentinel,
                       table + sentinel,
                       "* A-1 [ ] unfinished\n" + sentinel,
                       "```\n" + sentinel + "```\n",
                       "All items complete.\n" + sentinel,
                       sentinel + "<!-- a -->WORK<!-- b -->\n",
                       sentinel + "    # DO WORK\n",
                       sentinel + " # DO WORK\n",
                       "#\n" + sentinel,
                       "    " + sentinel,
                       "<!-- aei: empty-backlog -->\n",
                       "\u2028" + sentinel,
                       "\u2029" + sentinel,
                       "\u0085" + sentinel,
                       "\x0c" + sentinel,
                       "\x0b" + sentinel,
                       "\x1c" + sentinel,
                       "\x1d" + sentinel,
                       "\x1e" + sentinel,
                       "    \x0c" + sentinel)
    # (f) #1 REGRESSION + #3: a nonphysical line boundary on ONE physical line must not collapse two items
    # into one (dropping the second) and must not smuggle content into a sentinel; each fails closed
    # (exit 3, empty stdout), never a 1-item exit 0 or a phantom empty affirmation. Covers VT, FF, FS, GS,
    # RS, NEL, and LINE/PARAGRAPH SEPARATOR. The embedded-separator sentinel closes #3 directly.
    separators = ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
    collapse_cases = tuple("- DONE [x] complete" + sep + "- OPEN [ ] still work\n"
                           for sep in separators) + ("<!-- aei: empty\u2028backlog -->\n",)
    # (g) #2a MALFORMED INPUT: invalid UTF-8 must fail closed (exit 3), never be silently replace-decoded
    # and its line dropped, losing a task.
    invalid_utf8 = b"- DONE [x] complete\n\xff- OPEN [ ] still work\n"
    # (h) a NORMAL two-item backlog with a real newline between items still yields BOTH items (no over-fire).
    two_item = "- DONE [x] a :: close:fixed=c1\n- OPEN [ ] b\n"

    def run_backlog(tmp, name, body):
        p = Path(tmp) / name
        p.write_text(body, encoding="utf-8")
        return subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               "--backlog", str(p), "--aei"], capture_output=True, timeout=30)

    with tempfile.TemporaryDirectory(prefix="aiqt-aei-backlog-") as tmp:
        malformed_ok = all(run_backlog(tmp, "m-{}.md".format(i), t).returncode == 3
                           for i, t in enumerate(malformed))
        # (b)+(d): every fail-closed case exits 3 AND emits no drained-empty JSON on stdout.
        failclose_ok = True
        for j, t in enumerate(failclose_cases):
            r = run_backlog(tmp, "fc-{}.md".format(j), t)
            failclose_ok = failclose_ok and r.returncode == 3 and r.stdout.strip() == b""
        # (c): the sentinel over scaffolding affirms an empty enumeration (exit 0, empty items).
        affirm_ok = True
        for j, t in enumerate(affirm_cases):
            r = run_backlog(tmp, "s-{}.md".format(j), t)
            affirm_ok = (affirm_ok and r.returncode == 0
                         and json.loads(r.stdout)["items"] == [])
        # (a): a recognized dash-bullet backlog still enumerates exactly as before.
        r = run_backlog(tmp, "ok.md", recognized)
        recognized_ok = r.returncode == 0 and len(json.loads(r.stdout)["items"]) == 2
        # (f): every nonphysical-separator collapse case (and the embedded-separator sentinel) fails
        # closed (exit 3, empty stdout), never a 1-item exit 0.
        collapse_ok = True
        for j, t in enumerate(collapse_cases):
            r = run_backlog(tmp, "cs-{}.md".format(j), t)
            collapse_ok = collapse_ok and r.returncode == 3 and r.stdout.strip() == b""
        # (g): invalid UTF-8 fails closed (exit 3, empty stdout), never a silently dropped line.
        p_bad = Path(tmp) / "bad-utf8.md"
        p_bad.write_bytes(invalid_utf8)
        r_bad = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                "--backlog", str(p_bad), "--aei"], capture_output=True, timeout=30)
        invalid_ok = r_bad.returncode == 3 and r_bad.stdout.strip() == b""
        # (h): a normal two-item backlog with a real newline still enumerates BOTH items (no over-fire).
        r_two = run_backlog(tmp, "two.md", two_item)
        two_ok = r_two.returncode == 0 and len(json.loads(r_two.stdout)["items"]) == 2
    exit3 = (malformed_ok and failclose_ok and affirm_ok and recognized_ok
             and collapse_ok and invalid_ok and two_ok)
    if not (v1_ok and errs2 and errs3 and v1_empty_items == [] and not v1_empty_errs and exit3):
        ok_fail.append("v1 grammar or main-level fail-closed vectors (affirmed-empty sentinel, "
                       "nonphysical separators, strict UTF-8) mismatch")
    if ok_fail:
        for f in ok_fail:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
