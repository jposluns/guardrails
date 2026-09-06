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


def parse(text, version=2):
    """(items, errors). Errors are strings; any error means the enumeration must not be emitted. Under
    version 2 the close/prep annotations are strict; under version 1 they are ignored and the v1 item
    shape is produced byte-identically."""
    items, errors, seen = [], [], set()
    for n, line in enumerate(text.splitlines(), 1):
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
        rest = line[offsets[lead + m.start("rest")]:].rstrip()
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
            close = {"reason": cm.group("reason"), "ref": cm.group("ref")}
            # B3/FIX2/FIX B: an OPTIONAL '::' paths list (repo-relative, comma-separated) binds a `fixed`
            # close to the item BY CONTENT using the SAME field the JSON validator and the liveness join
            # read (close.paths), so a routed fixed close RE-ENUMERATES as terminal. It is parsed ONLY from
            # the anchored close-tail unit (the paths immediately following the close at END of line), never
            # from a ':: paths=' earlier in the title, which stays literal title text and cannot smuggle a
            # verdict-bearing path into the close.
            if cm.group("paths"):
                paths = [p.strip() for p in cm.group("paths").split(",") if p.strip()]
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
            blocker = {"kind": bm.group("kind"), "ref": bm.group("ref")}
            om = OBSERVED_RE.search(rest)
            if om:
                blocker["observed_at_utc"] = om.group("t")
            em = EVIDENCE_RE.search(rest)
            if em:
                blocker["evidence"] = em.group("e").strip()
            pm = PREP_RE.search(rest)
            prm = PREP_REF_RE.search(rest)
            if pm:
                prep_complete = (pm.group("v") == "complete")
            if prm:
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
    items, errors = parse(raw.decode("utf-8", "replace"), version=version)
    if errors:
        for e in errors:
            print("enumerator error: " + e, file=sys.stderr)
        return 3
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
    if ok_fail:
        for f in ok_fail:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
