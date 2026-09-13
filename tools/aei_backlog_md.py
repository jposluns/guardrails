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
that yields ZERO items is an enumeration ERROR (exit 3), NOT an empty enumeration, UNLESS it affirms
emptiness with the sentinel line '<!-- aei: empty backlog -->': a non-empty file the grammar does not
recognize (a markdown table or any other format) and an unaffirmed empty file both fail closed, so an
unrecognized backlog can never read as a drained/empty actionable set. Emptiness is AFFIRMED, never
inferred from absence. An unreadable backlog is an error. The enumerator reads the real file; it
accepts no item list from its caller.
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

# A zero-item enumeration is VALID only when the backlog AFFIRMS emptiness with this sentinel line;
# otherwise (a non-empty file in an unrecognized format such as a markdown table, or an unaffirmed
# empty file) zero items is a cannot-evaluate error (exit 3), never a silently drained backlog (rule
# grdinp, check-fails-closed-on-unreadable). The sentinel is a markdown/HTML comment; internal
# whitespace is tolerated.
SENTINEL_RE = re.compile(r"^<!--\s*aei:\s*empty[ -]backlog\s*-->$")
COMMENT_RE = re.compile(r"^<!--.*-->$")


def has_sentinel(text):
    """True if any line is the declared-empty sentinel."""
    return any(SENTINEL_RE.match(line.strip()) for line in text.splitlines())


def has_content(text):
    """True if any line is non-blank and not a whole-line comment (the sentinel included)."""
    for line in text.splitlines():
        s = line.strip()
        if s and not COMMENT_RE.match(s):
            return True
    return False


def parse(text):
    """(items, errors). Errors are strings; any error means the enumeration must not be emitted."""
    items, errors, seen = [], [], set()
    for n, line in enumerate(text.splitlines(), 1):
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
    text = raw.decode("utf-8", "replace")
    items, errors = parse(text)
    if errors:
        for e in errors:
            print("enumerator error: " + e, file=sys.stderr)
        return 3
    if not items and not has_sentinel(text):
        # Zero items with no sentinel: a format mismatch (content the grammar does not recognize) or an
        # unaffirmed empty file, never a drained backlog. FAIL CLOSED (exit 3, no --aei JSON emitted), so
        # the stop guard denies the wind-down rather than reading it as a drained/empty actionable set.
        if has_content(text):
            print("enumerator error: backlog has content but zero items were recognized; the grammar "
                  "is a dash-bullet checkbox line (e.g. '- <ID> [ ] <title>', tokens [ |.|o|O|x|BLOCKED]); "
                  "a table or other format is unrecognized. To declare an empty backlog, add the "
                  "sentinel line '<!-- aei: empty backlog -->'.", file=sys.stderr)
        else:
            print("enumerator error: backlog is empty but does not affirm emptiness; add the sentinel "
                  "line '<!-- aei: empty backlog -->' to declare an empty backlog (emptiness is "
                  "affirmed, never inferred from absence).", file=sys.stderr)
        return 3
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
    # enumerates unchanged; (b) a TABLE-format file with content is a cannot-evaluate error (exit 3) with
    # NO drained-empty JSON; (c) the declared-empty sentinel is a valid empty enumeration (exit 0, []);
    # (d) an unaffirmed empty/whitespace-only/comment-only file fails closed (exit 3); (e) a malformed
    # line stays the existing error (exit 3).
    table = "| ID | State |\n| --- | --- |\n| A-1 | open |\n| A-2 | open |\n"
    sentinel_only = "<!-- aei: empty backlog -->\n"
    sentinel_with_text = "# Backlog\nAll items complete.\n<!-- aei: empty backlog -->\n"
    recognized = "# backlog\n- A-1 [ ] first\n- A-2 [x] done\n"

    def run_backlog(tmp, name, body):
        p = Path(tmp) / name
        p.write_text(body, encoding="utf-8")
        return subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               "--backlog", str(p), "--aei"], capture_output=True, timeout=30)

    with tempfile.TemporaryDirectory(prefix="aiqt-aei-backlog-") as tmp:
        malformed_ok = all(run_backlog(tmp, "m-{}.md".format(i), t).returncode == 3
                           for i, t in enumerate(malformed))
        # (b)+(d): content-yields-zero (table) and unaffirmed empty/whitespace/comment-only all fail
        # closed with exit 3 AND no drained-empty JSON on stdout.
        failclose_ok = True
        for j, t in enumerate((table, "", "   \n\n", "<!-- other note -->\n")):
            r = run_backlog(tmp, "fc-{}.md".format(j), t)
            failclose_ok = failclose_ok and r.returncode == 3 and r.stdout.strip() == b""
        # (c): the sentinel affirms an empty enumeration (exit 0, empty items), with or without text.
        affirm_ok = True
        for j, t in enumerate((sentinel_only, sentinel_with_text)):
            r = run_backlog(tmp, "s-{}.md".format(j), t)
            affirm_ok = (affirm_ok and r.returncode == 0
                         and json.loads(r.stdout)["items"] == [])
        # (a): a recognized dash-bullet backlog still enumerates exactly as before.
        r = run_backlog(tmp, "ok.md", recognized)
        recognized_ok = r.returncode == 0 and len(json.loads(r.stdout)["items"]) == 2
    exit3 = malformed_ok and failclose_ok and affirm_ok and recognized_ok
    if ok and errs2 and errs3 and empty_items == [] and not empty_errs and exit3:
        print("self-test OK")
        return 0
    print("SELF-TEST FAIL", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
