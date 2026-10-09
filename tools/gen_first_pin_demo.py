#!/usr/bin/env python3
"""Generate the first-pin delivered-prefix-superset demonstration .aiqt/release/first-pin-demonstration.toml.

tools/check_release_build.py --pre-tag applies _first_pin_findings to a genesis candidate; its evidence
names a demonstration TOML inside the candidate tree that carries EXACTLY agents-sha256,
delivered-prefix-obligations and floor-profile-obligations (FIRST_PIN_DEMO_KEYS), bound to the candidate's
AGENTS.md digest, with the delivered list a non-empty subset of the floor list (_demo_superset_findings).
This generator BUILDS that file from the candidate's own AGENTS.md and rule corpus and CHECKS it with the
consumer's own _demo_superset_findings before writing, so the obligation lists are derived, never authored
(PD-FIRST-PIN-FLOOR-PROFILE, option 1).

Obligation identifier. An obligation is a RULE, named by its `corpus-id` front-matter field in
.aiqt/core/rules/*.md: the id gen_rules.load_corpus validates as unique (CID_RE), the id
.aiqt/core/clauses.toml rows carry as `corpus-id` to name the rule a clause belongs to, and the id the gate
manifest's `rules` lists cite. A clause-id (for example prjint1.1) names a clause INSIDE a rule, not a
rule, so it is not an obligation identifier here; the slug is a path component, not the id.

Rule span. tools/gen_agents.py renders AGENTS.md as a fixed header followed by each rule's body
(gen_agents.body_of: the source text after the front matter, stripped, its title demoted one level) in
gen_agents.sort_key order, the bodies separated by blank lines. A rule's span is the byte range of that
body, UTF-8 encoded, inside the AGENTS.md bytes. It is located only after AGENTS.md is proven to be
byte-identical to gen_agents.render over the same corpus (so the file is that rendering and nothing else),
by an in-order scan that requires each body to occur EXACTLY ONCE in the file and every gap between
consecutive spans to be whitespace only. Any other shape is ambiguous and fails closed.

Delivered. A rule is delivered when its WHOLE span lies inside the first CAP_BYTES bytes, i.e. its end
offset (exclusive) is at most CAP_BYTES. A rule that starts inside the cap but ends past it STRADDLES the
cap: a reader capped at CAP_BYTES receives only a truncated part of its text, so its full text does NOT
lie inside the cap and it is NOT delivered (the decision's wording "full text lies inside"). The floor
profile is the delivered rules plus the apex rule (the single apex: true rule). A corpus with no apex, or
more than one, is ambiguous and fails closed; so does a demonstration with no delivered rule at all. The
apex must also be the FIRST rule span (gen_agents.sort_key places it first); any other order fails closed.
So in every successful build the apex is delivered and the floor equals the delivered list; obligations()
still adds an apex that is not delivered, and the self-test drives that path directly.

DISCLOSED RESIDUAL (disclose-guard-residuals): the delivered set is computed from AGENTS.md bytes and the
gen_agents layout; it is not a measurement of any particular reader's truncation behaviour, and the
AGENTS.md size premise (larger than the cap) is checked by check_release_build, not here, so an AGENTS.md
under the cap yields a demonstration in which every rule is delivered.

  gen_first_pin_demo.py              regenerate .aiqt/release/first-pin-demonstration.toml
  gen_first_pin_demo.py --check      exit 1 if the committed file differs from a fresh build; write nothing
  gen_first_pin_demo.py --self-test  build synthetic trees and assert each vector is discriminated

Exit convention: 0 clean; 1 drift (--check); 2 an unreadable or ambiguous input (fail-closed).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    try:
        sys.stderr.write(
            "error: gen_first_pin_demo.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    except BaseException:
        pass
    raise SystemExit(2)

import hashlib
import os
import stat
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus, CID_RE  # noqa: E402
import gen_agents  # noqa: E402  the AGENTS.md layout (sort_key, body_of, render) is single-sourced there
# The consumer's contract, single-sourced: the cap, the exact key set, and the superset check it applies.
from check_release_build import (CAP_BYTES, FIRST_PIN_DEMO_KEYS, FIRST_PIN_DEMO_REL,  # noqa: E402
                                 _demo_superset_findings)

DEMO_REL = FIRST_PIN_DEMO_REL  # the only path the consumer's pre-tag step accepts
AGENTS_REL = "AGENTS.md"
RULES_PARTS = (".aiqt", "core", "rules")

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata only,
# it does not affect what this generator produces. No RENDERER_DECL: this is not an adapter renderer.
GENSRC_OUTPUTS = (
    {"target": ".aiqt/release/first-pin-demonstration.toml", "kind": "file",
     "sources": ("AGENTS.md", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_first_pin_demo.py"},
)


class DemoError(ValueError):
    """An unreadable or ambiguous input; the caller maps it to exit 2."""


def read_agents(path):
    """The raw AGENTS.md bytes. Absent, a symlink or non-regular entry, unreadable, or not valid UTF-8 is a
    DemoError: the demonstration is bound to these exact bytes, so nothing else may stand in for them."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise DemoError("{} does not exist; the demonstration cannot be bound".format(AGENTS_REL))
    except OSError as exc:
        raise DemoError("cannot stat {} ({})".format(AGENTS_REL, exc))
    if not stat.S_ISREG(st.st_mode):
        raise DemoError("{} is not a regular file (a symlink, directory or special entry)".format(AGENTS_REL))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DemoError("{} is unreadable ({})".format(AGENTS_REL, exc))
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DemoError("{} is not valid UTF-8 ({})".format(AGENTS_REL, exc))
    return raw


def rule_spans(agents, rules):
    """Locate each (rule_id, body_bytes) of `rules`, in AGENTS.md order, inside `agents`; return
    [(rule_id, start, end)] with end exclusive. Each body must occur exactly once, after the previous span,
    separated from it by whitespace only; anything else is ambiguous (DemoError)."""
    spans, cursor = [], 0
    for rid, body in rules:
        if not body.strip():
            raise DemoError("rule {} renders an empty body; its span is undefined".format(rid))
        start = agents.find(body, cursor)
        if start < 0:
            raise DemoError("rule {} body is not found in {} after the previous rule".format(rid, AGENTS_REL))
        if agents.find(body, 0) != start or agents.find(body, start + 1) >= 0:
            raise DemoError("rule {} body occurs more than once in {}; its span is ambiguous".format(
                rid, AGENTS_REL))
        if spans and agents[cursor:start].strip():
            raise DemoError("non-whitespace bytes lie between rule {} and the previous rule".format(rid))
        spans.append((rid, start, start + len(body)))
        cursor = start + len(body)
    if agents[cursor:].strip():
        raise DemoError("non-whitespace bytes follow the last rule in {}".format(AGENTS_REL))
    return spans


def obligations(spans, apex_id, cap):
    """(delivered, floor): delivered is every rule whose whole span ends at or before `cap` (a straddling
    rule is not delivered); floor is delivered plus the apex rule id."""
    delivered = [rid for rid, _start, end in spans if end <= cap]
    floor = list(delivered) if apex_id in delivered else delivered + [apex_id]
    return delivered, floor


def render_demo(agents_sha, delivered, floor, cap):
    def _list(key, ids):
        return ["{} = [".format(key)] + ['  "{}",'.format(i) for i in ids] + ["]"]
    lines = ["# Generated by tools/gen_first_pin_demo.py from AGENTS.md and .aiqt/core/rules/; do not hand-edit.",
             "# The first-pin demonstration read by tools/check_release_build.py: delivered-prefix-obligations",
             "# are the rule ids (corpus-id) whose full text lies inside the first {} bytes of AGENTS.md;".format(
                 cap),
             "# floor-profile-obligations are those plus the apex rule id.",
             'agents-sha256 = "{}"'.format(agents_sha)]
    return "\n".join(lines + _list("delivered-prefix-obligations", delivered)
                     + _list("floor-profile-obligations", floor)) + "\n"


def build(root, cap=CAP_BYTES):
    """Build and check the demonstration text for the tree at root. Raises DemoError (or the corpus
    loader's ValueError/OSError) on an unreadable or ambiguous input."""
    agents = read_agents(root / AGENTS_REL)
    src_dir = root.joinpath(*RULES_PARTS)
    if not dir_present(src_dir):
        raise DemoError("the rule corpus {} is absent".format("/".join(RULES_PARTS)))
    pairs = [(src, fm) for src, fm, _ in load_corpus(src_dir)]
    pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
    if gen_agents.render(pairs).encode("utf-8") != agents:
        raise DemoError("{} is not the gen_agents rendering of the rule corpus (run tools/gen_agents.py); "
                        "the rule spans cannot be located unambiguously".format(AGENTS_REL))
    apexes = [str(fm["corpus-id"]) for _src, fm in pairs if fm.get("apex") is True]
    if len(apexes) != 1:
        raise DemoError("the rule corpus must carry exactly one apex rule, found {}".format(len(apexes)))
    rules = []
    for src, fm in pairs:
        rid = str(fm["corpus-id"])
        if not CID_RE.match(rid):
            raise DemoError("rule id {!r} is not a well-formed corpus-id".format(rid))
        rules.append((rid, gen_agents.body_of(src).encode("utf-8")))
    spans = rule_spans(agents, rules)
    if spans[0][0] != apexes[0]:
        raise DemoError("the apex rule {} is not the first rule in {} (found {} first); the floor profile "
                        "cannot be derived from an unexpected layout".format(apexes[0], AGENTS_REL, spans[0][0]))
    delivered, floor = obligations(spans, apexes[0], cap)
    if not delivered:
        raise DemoError("no rule lies whole inside the first {} bytes of {}".format(cap, AGENTS_REL))
    agents_sha = hashlib.sha256(agents).hexdigest()
    text = render_demo(agents_sha, delivered, floor, cap)
    demo = tomllib.loads(text)
    findings = _demo_superset_findings(demo, agents_sha, DEMO_REL)
    if set(demo) != FIRST_PIN_DEMO_KEYS or findings:
        raise DemoError("the built demonstration fails its consumer check: {}".format(findings))
    return text


def run(root, check):
    """Reconcile DEMO_REL under root. Exit 0 in sync, 1 on drift (check mode), 2 on an unreadable or
    ambiguous input or a target that does not resolve inside the tree. Parameterized on root so the
    self-test drives it against synthetic trees, never the real repo."""
    try:
        text = build(root)
        out = root / DEMO_REL
        real_root = os.path.realpath(root)
        if os.path.islink(out) or not os.path.realpath(out).startswith(real_root + os.sep):
            raise DemoError("{} does not resolve inside the tree".format(DEMO_REL))
        if not check:
            out.parent.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if reconcile(out, text, check):  # reconcile fail-closes (exit 2) on an OSError or invalid-UTF-8 target
        print("drift: {}".format(DEMO_REL))
        print("run tools/gen_first_pin_demo.py to regenerate")
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ------------------------------------------------------------------------------------
# Synthetic temp trees only, never the real tree. Each fixture writes a schema-valid rule corpus and an
# AGENTS.md rendered from it by gen_agents.render, with rule bodies padded to place spans at exact offsets
# against the real CAP_BYTES. Vectors, each asserted on its exact outcome:
#   0. obligations() directly: an apex ending past the cap is not delivered but is added to the floor;
#      a rule ending at CAP_BYTES + 1 is not delivered and one ending at CAP_BYTES is;
#   1. a rule STRADDLING the cap is not delivered, while the rule before it (ending 10 bytes inside) is;
#   2. a rule ending EXACTLY at the cap is delivered (the boundary is inclusive of the last byte), and one
#      ending ONE byte past it (its last byte is byte CAP_BYTES) is not;
#   2b. spans are measured in UTF-8 BYTES, not characters: a rule padded with two-byte characters whose
#      character span ends inside the cap but whose byte span ends past it (a two-byte character straddling
#      the cap) is not delivered;
#   2c. an apex that is not the first rule span (a patched gen_agents.sort_key) fails closed (exit 2);
#   3. AGENTS.md under the cap delivers every rule, in AGENTS.md order, and floor equals delivered;
#   4. a missing apex fails closed (exit 2, nothing written), as does an apex that itself straddles the cap;
#   5. a digest mismatch (a hand-edited agents-sha256, or AGENTS.md regenerated after the demonstration)
#      is drift (exit 1), and the consumer's _demo_superset_findings flags the hand-edited binding;
#   6. an unreadable AGENTS.md (mode 000, a directory, absent, invalid UTF-8) and an AGENTS.md that is not
#      the gen_agents rendering each fail closed (exit 2).

_APEX = "---\ncorpus-id: prjint1\norigin: pack\nfamily: aiqt\napex: true\nslug: project-integrity\n---\n"
_RULE = "---\ncorpus-id: {cid}\norigin: pack\nfamily: aiqt\ntier: 10\nfacet: ACCUR\nslug: {slug}\n---\n"


def _write_tree(root, specs):
    """specs: [(corpus_id, is_apex, pad)], pad an ASCII byte count or a literal fill string; write the corpus
    and its gen_agents AGENTS.md. Returns the spans dict {corpus_id: (start, end)} measured on the written
    AGENTS.md."""
    rdir = root.joinpath(*RULES_PARTS)
    rdir.mkdir(parents=True, exist_ok=True)
    for old in rdir.iterdir():
        old.unlink()
    for cid, is_apex, pad in specs:
        head = _APEX if is_apex else _RULE.format(cid=cid, slug="fixture-" + cid)
        fill = pad if isinstance(pad, str) else "a" * pad  # an int pads with ASCII; a str is the literal fill
        body = "\n# Rule {}\n\nObligation text {}{}.\n".format(cid, cid, fill)
        (rdir / ("{}.md".format(cid))).write_text(head + body, encoding="utf-8")
    pairs = [(src, fm) for src, fm, _ in load_corpus(rdir)]
    pairs.sort(key=lambda pf: gen_agents.sort_key(pf[1]))
    text = gen_agents.render(pairs)
    (root / AGENTS_REL).write_text(text, encoding="utf-8")
    raw = text.encode("utf-8")
    return {str(fm["corpus-id"]): (raw.find(gen_agents.body_of(src).encode("utf-8")),
                                   raw.find(gen_agents.body_of(src).encode("utf-8"))
                                   + len(gen_agents.body_of(src).encode("utf-8"))) for src, fm in pairs}


def _tuned(root, specs, which, target_end):
    """Write specs, then re-pad rule `which` so its span ends exactly at target_end (ASCII padding shifts
    the end byte for byte). Returns the measured spans."""
    spans = _write_tree(root, specs)
    delta = target_end - spans[which][1]
    specs = [(c, a, p + delta if c == which else p) for c, a, p in specs]
    return _write_tree(root, specs)


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def capture(root, check):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            try:
                code = run(root, check)
            except SystemExit as exc:  # reconcile's fail-closed path raises SystemExit(2)
                return "raised SystemExit({!r})".format(exc.code), buf.getvalue()
        return code, buf.getvalue()

    def demo_of(root):
        return tomllib.loads((root / DEMO_REL).read_text(encoding="utf-8"))

    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-first-pin-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        # 0. obligations() directly. An apex that ends past the cap is not delivered, so the floor must add
        # it; a rule ending one byte past the cap is not delivered; one ending exactly at it is.
        for label, spans, apex, want in (
                ("apex past the cap", [("ruleaa", 0, 10), ("prjint1", 20, CAP_BYTES + 5)], "prjint1",
                 (["ruleaa"], ["ruleaa", "prjint1"])),
                ("rule ending at cap + 1", [("prjint1", 0, 10), ("ruleaa", 20, CAP_BYTES + 1)], "prjint1",
                 (["prjint1"], ["prjint1"])),
                ("rule ending at the cap", [("prjint1", 0, 10), ("ruleaa", 20, CAP_BYTES)], "prjint1",
                 (["prjint1", "ruleaa"], ["prjint1", "ruleaa"]))):
            got = obligations(spans, apex, CAP_BYTES)
            if got != want:
                failures.append("obligations ({}): expected {}, got {}".format(label, want, got))

        # 1. Straddle: ruleaa ends 10 bytes inside the cap; rulebb starts inside it and ends past it.
        st = tmp / "straddle"
        sp = _tuned(st, [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400),
                         ("rulecc", False, 30000)], "ruleaa", CAP_BYTES - 10)
        if not (sp["ruleaa"][1] == CAP_BYTES - 10 and sp["rulebb"][0] < CAP_BYTES < sp["rulebb"][1]):
            failures.append("straddle fixture did not place the spans as intended: {}".format(sp))
        code, out = capture(st, False)
        if code != 0:
            failures.append("straddle: generate expected exit 0, got {}\n{}".format(code, out))
        else:
            d = demo_of(st)
            if d.get("delivered-prefix-obligations") != ["prjint1", "ruleaa"]:
                failures.append("straddle: delivered must be [prjint1, ruleaa] (rulebb straddles the cap), "
                                "got {}".format(d.get("delivered-prefix-obligations")))
            if d.get("floor-profile-obligations") != ["prjint1", "ruleaa"]:
                failures.append("straddle: the apex is delivered, so the floor must equal the delivered list "
                                "[prjint1, ruleaa], got {}".format(
                    d.get("floor-profile-obligations")))
            agents_sha = hashlib.sha256((st / AGENTS_REL).read_bytes()).hexdigest()
            if _demo_superset_findings(d, agents_sha, DEMO_REL):
                failures.append("straddle: the consumer check rejects the generated demonstration")
            code, out = capture(st, True)
            if code != 0:
                failures.append("straddle: --check after generate expected exit 0, got {}\n{}".format(code, out))

        # 2. Boundary: ruleaa ends EXACTLY at the cap (its last byte is byte CAP_BYTES - 1): delivered.
        bd = tmp / "boundary"
        sp = _tuned(bd, [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400)],
                    "ruleaa", CAP_BYTES)
        code, out = capture(bd, False)
        if sp["ruleaa"][1] != CAP_BYTES or code != 0:
            failures.append("boundary: fixture end {} / generate exit {}\n{}".format(sp["ruleaa"][1], code, out))
        elif demo_of(bd).get("delivered-prefix-obligations") != ["prjint1", "ruleaa"]:
            failures.append("boundary: a rule ending exactly at the cap must be delivered, got {}".format(
                demo_of(bd).get("delivered-prefix-obligations")))

        # 2 (cont.). One byte past: ruleaa ends at CAP_BYTES + 1 (its last byte is byte CAP_BYTES, which a
        # reader capped at CAP_BYTES never receives): NOT delivered.
        op = tmp / "onepast"
        sp = _tuned(op, [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400)],
                    "ruleaa", CAP_BYTES + 1)
        code, out = capture(op, False)
        if sp["ruleaa"][1] != CAP_BYTES + 1 or code != 0:
            failures.append("one past: fixture end {} / generate exit {}\n{}".format(sp["ruleaa"][1], code, out))
        elif (demo_of(op).get("delivered-prefix-obligations") != ["prjint1"]
              or demo_of(op).get("floor-profile-obligations") != ["prjint1"]):
            failures.append("one past: a rule ending one byte past the cap must not be delivered, got {}".format(
                demo_of(op)))

        # 2b. Bytes, not characters. ruleaa is padded with U+00E9 (two UTF-8 bytes each) so that its
        # CHARACTER span ends inside the cap while its BYTE span ends far past it, with one U+00E9 straddling
        # the cap (its first byte is byte CAP_BYTES - 1, its second byte CAP_BYTES). A generator measuring
        # str offsets would deliver ruleaa; measuring bytes, only the apex is delivered.
        mb = tmp / "multibyte"
        two = "\u00e9"

        def mb_specs(fill):
            return [("prjint1", True, 0), ("ruleaa", False, fill), ("rulebb", False, 100)]

        _write_tree(mb, mb_specs(two))
        raw = (mb / AGENTS_REL).read_bytes()
        lead = "Obligation text ruleaa".encode("utf-8")
        pad_b = raw.find(lead) + len(lead)                  # byte offset of ruleaa's padding
        pad_c = len(raw[:pad_b].decode("utf-8"))            # its character offset
        a0 = (CAP_BYTES - 1 - pad_b) % 2                    # one ASCII byte aligns a U+00E9 onto the cap
        m = CAP_BYTES - 50 - pad_c - a0 - 1                 # character end CAP_BYTES - 50 (the "." follows)
        sp = _write_tree(mb, mb_specs("a" * a0 + two * m))
        raw = (mb / AGENTS_REL).read_bytes()
        char_end = len(raw[:sp["ruleaa"][1]].decode("utf-8"))
        if not (raw[CAP_BYTES - 1:CAP_BYTES + 1] == two.encode("utf-8")
                and char_end <= CAP_BYTES < sp["ruleaa"][1]):
            failures.append("multi-byte fixture did not place the spans as intended: char end {}, byte span "
                            "{}, bytes at the cap {!r}".format(char_end, sp["ruleaa"],
                                                              raw[CAP_BYTES - 1:CAP_BYTES + 1]))
        code, out = capture(mb, False)
        if code != 0:
            failures.append("multi-byte: generate expected exit 0, got {}\n{}".format(code, out))
        elif (demo_of(mb).get("delivered-prefix-obligations") != ["prjint1"]
              or demo_of(mb).get("floor-profile-obligations") != ["prjint1"]):
            failures.append("multi-byte: spans must be measured in UTF-8 bytes (ruleaa's character span ends "
                            "inside the cap, its byte span past it), got {}".format(demo_of(mb)))

        # 2c. The apex is not the first rule span: fail closed. gen_agents.sort_key always places the apex
        # first, so the fixture patches it (restored in finally) to sort the apex last for both the fixture
        # render and the build.
        real_sort_key = gen_agents.sort_key
        gen_agents.sort_key = lambda fm: (3, 0, 0, "") if fm.get("apex") is True else real_sort_key(fm)
        try:
            al = tmp / "apexlast"
            _write_tree(al, [("prjint1", True, 0), ("ruleaa", False, 10)])
            code, out = capture(al, False)
        finally:
            gen_agents.sort_key = real_sort_key
        if code != 2 or (al / DEMO_REL).exists() or "is not the first rule" not in out:
            failures.append("apex not first: expected exit 2 with nothing written, got {}\n{}".format(code, out))

        # 3. Under the cap: every rule is delivered, in AGENTS.md order; floor equals delivered.
        uc = tmp / "undercap"
        _write_tree(uc, [("rulecc", False, 10), ("prjint1", True, 0), ("ruleaa", False, 10)])
        code, out = capture(uc, False)
        if (uc / AGENTS_REL).stat().st_size >= CAP_BYTES or code != 0:
            failures.append("under-cap: generate expected exit 0 on a small AGENTS.md, got {}\n{}".format(
                code, out))
        else:
            d = demo_of(uc)
            if (d.get("delivered-prefix-obligations") != ["prjint1", "ruleaa", "rulecc"]
                    or d.get("floor-profile-obligations") != ["prjint1", "ruleaa", "rulecc"]):
                failures.append("under-cap: expected every rule delivered and floor == delivered, got {}".format(d))

        # 4. Missing apex: exit 2 and nothing written. An apex that itself straddles the cap: exit 2.
        na = tmp / "noapex"
        _write_tree(na, [("ruleaa", False, 10), ("rulebb", False, 10)])
        code, out = capture(na, False)
        if code != 2 or (na / DEMO_REL).exists() or "exactly one apex" not in out:
            failures.append("missing apex: expected exit 2 with nothing written, got {}\n{}".format(code, out))
        ba = tmp / "bigapex"
        _write_tree(ba, [("prjint1", True, CAP_BYTES), ("ruleaa", False, 10)])
        code, out = capture(ba, False)
        if code != 2 or (ba / DEMO_REL).exists() or "no rule lies whole" not in out:
            failures.append("apex past the cap: expected exit 2 (no rule delivered), got {}\n{}".format(code, out))

        # 5. Digest mismatch. (a) A hand-edited agents-sha256 is drift and the consumer flags the binding.
        dm = tmp / "digest"
        _write_tree(dm, [("prjint1", True, 0), ("ruleaa", False, 10)])
        capture(dm, False)
        real = hashlib.sha256((dm / AGENTS_REL).read_bytes()).hexdigest()
        demo_path = dm / DEMO_REL
        demo_path.write_text(demo_path.read_text(encoding="utf-8").replace(real, "0" * 64), encoding="utf-8")
        code, out = capture(dm, True)
        if code != 1:
            failures.append("hand-edited digest: --check expected exit 1, got {}\n{}".format(code, out))
        if not any("not bound" in f for f in _demo_superset_findings(demo_of(dm), real, DEMO_REL)):
            failures.append("hand-edited digest: the consumer check did not flag the unbound digest")
        # (b) AGENTS.md regenerated from a changed corpus after the demonstration: drift, then clean on regen.
        capture(dm, False)
        _write_tree(dm, [("prjint1", True, 0), ("ruleaa", False, 11)])
        code, out = capture(dm, True)
        if code != 1:
            failures.append("stale binding: --check expected exit 1 after AGENTS.md changed, got {}\n{}".format(
                code, out))
        code, out = capture(dm, False)
        if code != 0 or demo_of(dm).get("agents-sha256") != hashlib.sha256(
                (dm / AGENTS_REL).read_bytes()).hexdigest():
            failures.append("stale binding: regenerate did not rebind the digest ({})\n{}".format(code, out))

        # 6. Unreadable or ambiguous AGENTS.md: each fails closed (exit 2).
        def unreadable(name, mutate, reason):
            ur = tmp / name
            _write_tree(ur, [("prjint1", True, 0), ("ruleaa", False, 10)])
            note = mutate(ur / AGENTS_REL)
            try:
                if note is None:
                    code, out = capture(ur, False)
                    if code != 2 or (ur / DEMO_REL).exists() or reason not in out:
                        failures.append("{}: expected exit 2 ({}) with nothing written, got {}\n{}".format(
                            name, reason, code, out))
            finally:
                if (ur / AGENTS_REL).is_file():
                    os.chmod(ur / AGENTS_REL, 0o644)

        def mode000(p):
            os.chmod(p, 0)
            if os.access(p, os.R_OK):  # root or a DAC bypass: mode 000 is still readable, observed, not assumed
                print("SELF-TEST NOTE: mode-000 vector not applicable (file still readable)")
                return "skipped"
            return None

        def as_dir(p):
            p.unlink()
            p.mkdir()

        def absent(p):
            p.unlink()

        def bad_utf8(p):
            p.write_bytes(p.read_bytes() + b"\xff\n")

        def hand_edited(p):
            p.write_text(p.read_text(encoding="utf-8") + "\nAn appended line.\n", encoding="utf-8")

        for name, mutate, reason in (("mode000", mode000, "is unreadable"),
                                     ("directory", as_dir, "not a regular file"),
                                     ("absent", absent, "does not exist"),
                                     ("invalid-utf8", bad_utf8, "not valid UTF-8"),
                                     ("hand-edited", hand_edited, "not the gen_agents rendering")):
            unreadable(name, mutate, reason)
    finally:
        for dirpath, _dirnames, filenames in os.walk(tmp):
            for fn in filenames:
                try:
                    os.chmod(os.path.join(dirpath, fn), 0o644)
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: obligations() adds an undelivered apex to the floor; a rule straddling the cap, "
          "or ending one byte past it, is not delivered and one ending exactly at it is; spans are measured "
          "in UTF-8 bytes, not characters; an apex that is not the first rule fails closed (exit 2); an "
          "AGENTS.md under the cap delivers every rule; a missing apex and an apex past the cap fail closed "
          "(exit 2); a digest mismatch is drift (exit 1) and the consumer flags it; an unreadable, absent, "
          "non-regular, non-UTF-8 or hand-edited AGENTS.md fails closed (exit 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
