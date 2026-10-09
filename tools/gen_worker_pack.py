#!/usr/bin/env python3
"""Generate the AIQT worker-pack read surface from its .aiqt/core/ source (the source-and-adapter machinery).

The worker pack is the fixed preamble the dispatcher prepends to every worker brief across
all three worker families. Its SOURCE OF TRUTH is .aiqt/core/profiles/worker-pack.md (the updater's write
root is .aiqt/core/), whose frontmatter records the pack's lineage: a `restates:` flow sequence naming
every corpus rule the pack condenses, so corpus governance covers the pack. The published read surface is
the GENERATED file .claude/worker-pack.md: the source BODY byte-verbatim (everything after the frontmatter
terminator, with the house-style blank line after the frontmatter stripped), so the injected bytes are
exactly the reviewed pack text and carry no frontmatter. Fail-closed: a malformed source, a `restates:`
corpus-id that no longer resolves in .aiqt/core/rules/ (retiring or renaming a restated rule id forces a
pack review; an edit that keeps the id does not trip the lineage check), or an unreadable input is
exit 2, never a fresh render over an unvalidated lineage.
  gen_worker_pack.py           regenerate .claude/worker-pack.md
  gen_worker_pack.py --check   fail (exit 1) on drift, compared over the raw published bytes; exit 2 on
                               a malformed source or a read/write failure
  gen_worker_pack.py --self-test  assert the published bytes equal an independently specified expected
                                  body, the planted-drift and CRLF-only legs go red (exit 1), and the
                                  lineage and source-decode cases fail closed (exit 2)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    try:
        sys.stderr.write(
            "error: gen_worker_pack.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    except BaseException:
        pass
    raise SystemExit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from gen_rules import parse_source, load_corpus, CID_RE, SLUG_RE  # noqa: E402

SOURCE_REL = ".aiqt/core/profiles/worker-pack.md"
TARGET_REL = ".claude/worker-pack.md"
REQUIRED_KEYS = frozenset(("pack-id", "origin", "restates"))

# Declares this generator's output for the gensrc registry (tools/gen_gensrc.py); additive metadata only,
# it does not affect what this generator produces. The rules corpus is a validation-only read (the
# `restates:` lineage check never affects the rendered bytes), so it is not a declared source.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "worker-pack", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": ".claude/worker-pack.md", "kind": "file",
     "sources": (".aiqt/core/profiles/worker-pack.md",),
     "regenerate": "python3 tools/gen_worker_pack.py"},
)


def render(root):
    """The published pack text for the tree at root. Validates the source frontmatter (exactly pack-id/
    origin/restates; a kebab-case pack-id; origin pack; restates a non-empty duplicate-free flow sequence
    of corpus-ids) and resolves every `restates:` id against the live .aiqt/core/rules/ corpus through
    gen_rules.load_corpus (the authoritative loader, never a second parser). Returns the source body with
    the house-style blank line after the frontmatter stripped, so the published bytes are exactly the
    reviewed pack text. Raises ValueError on a malformed source or an unresolved lineage id; OSError on an
    unreadable input; the caller maps both to exit 2."""
    src = root / SOURCE_REL
    fm = parse_source(src)
    if set(fm) != set(REQUIRED_KEYS):
        raise ValueError("{}: frontmatter keys must be exactly {}".format(
            src.name, "/".join(sorted(REQUIRED_KEYS))))
    if not isinstance(fm["pack-id"], str) or not SLUG_RE.fullmatch(str(fm["pack-id"])):
        raise ValueError("{}: pack-id must be kebab-case".format(src.name))
    if fm["origin"] != "pack":
        raise ValueError("{}: origin must be pack".format(src.name))
    restates = fm["restates"]
    if not isinstance(restates, list) or not restates:
        raise ValueError("{}: restates must be a non-empty flow sequence of corpus-ids".format(src.name))
    seen = set()
    for cid in restates:
        if not isinstance(cid, str) or not CID_RE.fullmatch(cid):
            raise ValueError("{}: restates entry {!r} is not a corpus-id".format(src.name, cid))
        if cid in seen:
            raise ValueError("{}: restates lists corpus-id {!r} more than once".format(src.name, cid))
        seen.add(cid)
    known = set(str(fm_["corpus-id"]) for _src, fm_, _rel in load_corpus(root / ".aiqt" / "core" / "rules"))
    missing = sorted(seen - known)
    if missing:
        raise ValueError("{}: restates corpus-id(s) {} no longer resolve in .aiqt/core/rules/; "
                         "retiring or renaming a restated rule id forces a pack review".format(
                             src.name, ", ".join(missing)))
    text = src.read_text(encoding="utf-8")
    body = text[text.find("\n---\n", 4) + 5:].lstrip("\n")
    if not body.startswith("# "):
        raise ValueError("{}: pack body has no '# ' H1 title".format(src.name))
    return body


def run(root, check):
    """Reconcile the published pack under root against its source. Exit 0 in sync, 1 on drift (check
    mode), 2 on a malformed source or a read/write failure. Parameterized on root (the gen_rules idiom)
    so the self-test drives it against a synthetic tempdir tree, never the real repo. Check mode
    compares the RAW BYTES of the published target against the rendered body: the registered residue of
    the worker-pack-drift gate promises byte identity, and the shared text-mode reconcile decodes with
    universal newlines, which would let a CRLF-only rewrite pass, so the check compares bytes to keep
    that promise literal. Regeneration still writes through reconcile."""
    try:
        body = render(root)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    target = root / TARGET_REL
    if check:
        try:
            current = target.read_bytes() if target.exists() else None
        except OSError as exc:
            print("error: cannot read {} ({}); fail-closed".format(target, exc), file=sys.stderr)
            return 2
        if current != body.encode("utf-8"):
            print("drift: {} is out of date; run tools/gen_worker_pack.py".format(TARGET_REL))
            return 1
        return 0
    if reconcile(target, body, check):
        print("drift: {} is out of date; run tools/gen_worker_pack.py".format(TARGET_REL))
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic trees in a private tempdir prove the gate's own invariants (the sibling generators' idiom):
#   (a) a conformant tree generates, the published bytes equal _EXPECTED_BODY (spelled out independently
#       of the source fixture, so a truncating or rewriting render cannot also rewrite the expectation),
#       and --check is drift-clean;
#   (b) a PLANTED DRIFTED target fails --check (exit 1): the case that goes red if the drift gate is
#       neutered, so the gate itself is guarded;
#   (c) a restates: corpus-id absent from the corpus fails closed (exit 2), the forced-pack-review leg;
#   (d) a source missing the restates key fails closed (exit 2);
#   (e) a CRLF-only rewrite of the published target fails --check (exit 1): the leg that goes red if the
#       raw-byte comparison regresses to a newline-normalizing text compare;
#   (f) an invalid-UTF-8 SOURCE fails closed (exit 2); an invalid-UTF-8 published target is plain byte
#       drift under the raw-byte comparison, covered by the (b) and (e) legs.

_RULE_SRC = """---
corpus-id: {cid}
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: worker-pack-selftest-{n}
---

# Worker-pack self-test rule {n}

A minimal rule so the lineage check has a corpus to resolve against.
"""

_PACK_SRC = """---
pack-id: worker-pack
origin: pack
restates: [{restates}]
---

# Self-test worker pack

1. A minimal pack line. (selfw1)
"""

# Case (a) compares the published target against this body, spelled out INDEPENDENTLY of _PACK_SRC
# (never sliced from it), so a render that truncates or rewrites the body goes red here.
_EXPECTED_BODY = "# Self-test worker pack\n\n1. A minimal pack line. (selfw1)\n"


def _build(base, restates):
    rules = base / ".aiqt" / "core" / "rules"
    rules.mkdir(parents=True)
    for n, cid in enumerate(("selfw1", "selfw2"), 1):
        (rules / "quali-worker-pack-selftest-{}.md".format(n)).write_text(
            _RULE_SRC.format(cid=cid, n=n), encoding="utf-8")
    (base / ".aiqt" / "core" / "profiles").mkdir(parents=True)
    (base / ".aiqt" / "core" / "profiles" / "worker-pack.md").write_text(
        _PACK_SRC.format(restates=restates), encoding="utf-8")
    (base / ".claude").mkdir()


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # reconcile fails closed by raising SystemExit(2); catch it and return a sentinel so an
        # unexpected raise registers as a FAILURE rather than aborting the self-test or exiting green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-worker-pack-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # (a) conformant: generate, byte-compare against the independently spelled expected body, then --check.
        good = tmp / "good"
        _build(good, "selfw1, selfw2")
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        target = good / TARGET_REL
        if not target.is_file() or target.read_bytes() != _EXPECTED_BODY.encode("utf-8"):
            failures.append("conformant tree: published pack does not byte-equal the expected body")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")

        # (b) the PLANTED DRIFT case: a mutated published target must fail --check (exit 1). This case
        #     goes red if the drift comparison is neutered, so the gate guards itself.
        target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        if run_quiet(good, check=True) != 1:
            failures.append("planted drifted target expected exit 1 (drift)")

        # (c) a restates: id absent from the corpus fails closed (exit 2): the forced-pack-review leg.
        gone = tmp / "gone"
        _build(gone, "selfw1, selfw9")
        if run_quiet(gone, check=True) != 2:
            failures.append("restates id absent from the corpus expected exit 2 (fail-closed)")

        # (d) a source missing the restates key fails closed (exit 2).
        nokey = tmp / "nokey"
        _build(nokey, "selfw1")
        src = nokey / ".aiqt" / "core" / "profiles" / "worker-pack.md"
        src.write_text(src.read_text(encoding="utf-8").replace(
            "restates: [selfw1]\n", ""), encoding="utf-8")
        if run_quiet(nokey, check=True) != 2:
            failures.append("source missing restates expected exit 2 (fail-closed)")

        # (e) a CRLF-only rewrite of the published target fails --check (exit 1): red if the raw-byte
        #     comparison regresses to a newline-normalizing text compare.
        crlf = tmp / "crlf"
        _build(crlf, "selfw1, selfw2")
        if run_quiet(crlf, check=False) != 0:
            failures.append("crlf case: initial generation expected exit 0")
        crlf_target = crlf / TARGET_REL
        crlf_target.write_bytes(crlf_target.read_bytes().replace(b"\n", b"\r\n"))
        if run_quiet(crlf, check=True) != 1:
            failures.append("CRLF-only published target expected exit 1 (drift)")

        # (f) an invalid-UTF-8 SOURCE fails closed (exit 2): the decode leg on the input side.
        undec = tmp / "undec"
        _build(undec, "selfw1, selfw2")
        (undec / ".aiqt" / "core" / "profiles" / "worker-pack.md").write_bytes(b"\xff\xfe not utf-8")
        if run_quiet(undec, check=True) != 2:
            failures.append("invalid-UTF-8 source expected exit 2 (fail-closed)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant tree generates with the published bytes equal to the expected "
          "body and regenerates drift-clean; a planted drifted target and a CRLF-only published target "
          "each fail --check (exit 1, red without the byte-exact gate); and an unresolved restates "
          "corpus-id, a source missing restates, and an invalid-UTF-8 source all fail closed (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
