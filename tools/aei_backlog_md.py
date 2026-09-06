#!/usr/bin/env python3
"""The generic AEI reference enumerator for a markdown-checkbox backlog (GD-112/GD-164; rule grdinp).

Emits AEI v2 by default (version 2) and v1 behind --v1 (byte-identical to the shipped v1 contract, kept
for the migration test). v2 carries the close object and closed vocabulary, the human-decision prep
fields, and the optional advance hint, all from checkbox annotations.

Line grammar (one item per dash bullet; anything else is prose and ignored):
  - <ID> [x]        <title> :: close:<reason>=<ref>     closed (v2 REQUIRES the close annotation)
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
are ignored and the v1 shape is emitted unchanged.

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

ITEM_RE = re.compile(r"^-\s+(?P<id>[A-Za-z][A-Za-z0-9_.-]*)\s+"
                     r"\[(?P<tok>x|X| |\.|o|O|BLOCKED)\]\s+(?P<rest>.+?)\s*$")
CANDIDATE_RE = re.compile(r"^-\s+\S+\s+\[")
CHECKBOX_RE = re.compile(r"^-\s+.*\[(?:x|X| |\.|o|O|BLOCKED)\]")
BLOCKER_PREFIX_RE = re.compile(r"::\s*blocker:")
BLOCKER_RE = re.compile(r"::\s*blocker:(?P<kind>[a-z-]+)=(?P<ref>\S+)")
OBSERVED_RE = re.compile(r"::\s*observed=(?P<t>\S+)")
EVIDENCE_RE = re.compile(r"::\s*evidence=(?P<e>[^:]+?)(?:\s*::|$)")
CLOSE_RE = re.compile(r"::\s*close:(?P<reason>[a-z-]+)=(?P<ref>\S+)")
PREP_RE = re.compile(r"::\s*prep=(?P<v>complete|incomplete)\b")
PREP_REF_RE = re.compile(r"::\s*prep_ref=(?P<r>\S+)")
ADVANCE_RE = re.compile(r"::\s*advance=(?P<a>[^:]+?)(?:\s*::|$)")
KINDS = ("tracked-task", "human-decision", "external", "foreign-lease", "not-before")
CLOSE_REASONS = ("fixed", "no-fix-needed", "not-buildable", "maintainer-deferred")


def parse(text, version=2):
    """(items, errors). Errors are strings; any error means the enumeration must not be emitted. Under
    version 2 the close/prep annotations are strict; under version 1 they are ignored and the v1 item
    shape is produced byte-identically."""
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
        close = None
        cm = CLOSE_RE.search(rest)
        if cm:
            if cm.group("reason") not in CLOSE_REASONS:
                errors.append("line {}: unknown close reason {!r}".format(n, cm.group("reason")))
                continue
            close = {"reason": cm.group("reason"), "ref": cm.group("ref")}
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
    if ok_fail:
        for f in ok_fail:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
