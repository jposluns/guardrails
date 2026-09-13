#!/usr/bin/env python3
"""The generic AEI v1 reference enumerator for a markdown-checkbox backlog (GD-112; rule grdinp).

Line grammar (one item per dash bullet; anything else is prose and ignored):
  - <ID> [x]        <title>              closed
  - <ID> [ |.|o|O]  <title>              open, granted (the seed-progression tokens all mean open)
  - <ID> [BLOCKED]  <title> :: blocker:<kind>=<ref>[ :: observed=<iso-utc>][ :: evidence=<text>]
  - ... any item line may end with ':: proposed' (enumerated, never compelling)
The 6-token state model ([x] [O] [o] [.] [ ] [BLOCKED]) reimplements the semantics of the reference
host parser supplied with the GD-112 brief: [x] is done; [O]/[o]/[.]/[ ] are open at different seed
stages (a distinction the guard does not need, so all map to open); [BLOCKED] requires a structured
blocker suffix, because an unproven BLOCKED marker must classify as actionable, never as excused.

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
  aei_backlog_md.py --backlog PATH --aei     emit the AEI v1 JSON on stdout
  aei_backlog_md.py --self-test              grammar and fail-closed vectors
"""
import datetime
import hashlib
import json
import re
import sys

ITEM_RE = re.compile(r"^-\s+(?P<id>[A-Za-z][A-Za-z0-9_.-]*)\s+"
                     r"\[(?P<tok>x|X| |\.|o|O|BLOCKED)\]\s+(?P<rest>.+?)\s*$")
CANDIDATE_RE = re.compile(r"^-\s+\S+\s+\[")
# any dash-bullet whose bracket holds a task token, even with NO id, is a task not prose (fail-closed):
CHECKBOX_RE = re.compile(r"^-\s+.*\[(?:x|X| |\.|o|O|BLOCKED)\]")
BLOCKER_PREFIX_RE = re.compile(r"::\s*blocker:")
BLOCKER_RE = re.compile(r"::\s*blocker:(?P<kind>[a-z-]+)=(?P<ref>\S+)")
OBSERVED_RE = re.compile(r"::\s*observed=(?P<t>\S+)")
EVIDENCE_RE = re.compile(r"::\s*evidence=(?P<e>[^:]+?)(?:\s*::|$)")
KINDS = ("tracked-task", "human-decision", "external", "foreign-lease", "not-before")

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


def parse(text):
    """(items, errors). Errors are strings; any error means the enumeration must not be emitted."""
    items, errors, seen = [], [], set()
    for n, line in enumerate(_physical_lines(text), 1):
        stripped = line.strip()
        if not (CANDIDATE_RE.match(stripped) or CHECKBOX_RE.match(stripped)
                or BLOCKER_PREFIX_RE.search(stripped)):
            continue
        m = ITEM_RE.match(stripped)
        if not m:
            errors.append("line {}: item-shaped line fails the grammar: {!r}".format(
                n, line.strip()[:120]))
            continue
        iid, tok, rest = m.group("id"), m.group("tok"), m.group("rest")
        if iid in seen:
            errors.append("line {}: duplicate id {}".format(n, iid))
            continue
        seen.add(iid)
        proposed = bool(re.search(r"::\s*proposed\s*$", rest))
        rest = re.sub(r"\s*::\s*proposed\s*$", "", rest)
        blocker = None
        blocker_prefix = BLOCKER_PREFIX_RE.search(rest)
        bm = BLOCKER_RE.search(rest)
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
            rest = rest[:bm.start()].rstrip()
        elif tok == "BLOCKED":
            # a bare BLOCKED marker is legal input; with no structured proof it enumerates
            # open-with-no-blocker and the guard classifies it actionable (never excused by glyph)
            pass
        state = "closed" if tok.lower() == "x" else ("proposed" if proposed else "open")
        items.append({"id": iid, "title": rest, "state": state,
                      "granted": not proposed, "blocker": blocker})
    return items, errors


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    if "--backlog" not in argv or "--aei" not in argv:
        print("usage: aei_backlog_md.py --backlog PATH --aei | --self-test", file=sys.stderr)
        return 2
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
    items, errors = parse(text)
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
    print(json.dumps({"version": 1, "generated_at_utc": now,
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
    items, errors = parse(sample)
    ok = (not errors and len(items) == 8
          and sum(1 for i in items if i["state"] == "open" and i["granted"]) == 6
          and next(i for i in items if i["id"] == "A-5")["state"] == "closed"
          and next(i for i in items if i["id"] == "B-1")["blocker"]["kind"] == "external"
          and next(i for i in items if i["id"] == "B-2")["blocker"] is None
          and next(i for i in items if i["id"] == "P-1")["state"] == "proposed")
    _items, errs2 = parse("- badline [Q] unknown token\n")
    _items3, errs3 = parse("- D-1 [ ] a\n- D-1 [ ] b\n")
    empty_items, empty_errs = parse("")
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
    recognized = "# backlog\n- A-1 [ ] first\n- A-2 [x] done\n"
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
    two_item = "- DONE [x] a\n- OPEN [ ] b\n"

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
    if ok and errs2 and errs3 and empty_items == [] and not empty_errs and exit3:
        print("self-test OK")
        return 0
    print("SELF-TEST FAIL", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
