#!/usr/bin/env python3
"""Must-deliver prefix gate: every rule listed in .aiqt/core/must-deliver.toml stays WHOLE inside the
first check_release_build.CAP_BYTES bytes of AGENTS.md.

The first-pin demonstration (tools/gen_first_pin_demo.py) DERIVES its delivered list from AGENTS.md, so
that list shrinks silently when the rules grow and every byte-identity gate stays green: a rule can fall
out of the delivered prefix with no check failing. This gate closes that gap for the rules a maintainer
has declared must stay delivered. The list is hand-edited policy, like .aiqt/core/instruction-budget.toml:
additions may be proposed in any PR; a removal is a reviewed change that this gate does not block (the
schema has no field that could record an approval; see the disclosed residual below).

The spans come from gen_first_pin_demo.measure, the ONE span algorithm the demonstration generator also
uses: AGENTS.md must be byte-identical to a fresh composition of the rule corpus and the block registry
(a stale AGENTS.md is exit 2, never measured), offsets are UTF-8 bytes made absolute against the composed
rule region, and a rule is delivered only when its end offset (exclusive) is at most the cap. This gate
never reads the demonstration's own lists; it recomputes everything. The cap is read from
check_release_build.CAP_BYTES at call time, never copied here.

The declared file carries exactly format-version, warn-margin-bytes and [[rule]] rows of exactly id and
reason; every id must be a corpus-id with a measured span. An absent, unreadable, symlinked, non-UTF-8,
malformed or EMPTY list is exit 2 (a missing input is a failure, never "nothing to check"), per the
check-fails-closed-on-unreadable rule. The gate prints every listed rule's end offset and margin on every
run, and a WARNING (still exit 0) when the tightest margin drops under warn-margin-bytes, so growth is
visible before a listed rule falls out.

DISCLOSED RESIDUAL (disclose-guard-residuals): a PR that removes a rule from the list passes this gate
(review must judge the removal); a listed rule cut down to a stub still passes (content is not judged);
the gate measures the repository file against the configured cap, not any platform's actual truncation
behaviour or a budget other instruction files may share; and a client that checks AGENTS.md out with CRLF
line endings inflates what a reader there receives, which this gate does not model (.gitattributes pins
AGENTS.md to eol=lf).

  check_must_deliver.py              check the live tree
  check_must_deliver.py --self-test  build synthetic trees and assert each vector is discriminated

Exit convention: 0 every listed rule is delivered; 1 a listed rule ends past the cap; 2 an unreadable,
malformed or ambiguous input (fail-closed).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_must_deliver.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import stat
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from gen_rules import CID_RE  # noqa: E402
from gen_first_pin_demo import measure, DemoError  # noqa: E402  the ONE span algorithm, shared
import check_release_build  # noqa: E402  the cap, read as check_release_build.CAP_BYTES at call time

DECL_REL = ".aiqt/core/must-deliver.toml"
TOP_KEYS = frozenset({"format-version", "warn-margin-bytes", "rule"})
ROW_KEYS = frozenset({"id", "reason"})


def _read_decl_bytes(path):
    """The one read of the declaration's bytes, a module attribute so the self-test can inject a
    PermissionError here on every runner, including one where a mode-000 file stays readable."""
    return path.read_bytes()


def read_declared(path):
    """The validated must-deliver list: ([rule ids], warn_margin_bytes). Absent, a symlink or
    non-regular entry, unreadable, not UTF-8, not TOML, or outside the exact schema is a DemoError
    (exit 2): the list is the gate's whole policy input, so nothing else may stand in for it."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise DemoError("{} does not exist; the must-deliver policy cannot be read".format(DECL_REL))
    except OSError as exc:
        raise DemoError("cannot stat {} ({})".format(DECL_REL, exc))
    if not stat.S_ISREG(st.st_mode):
        raise DemoError("{} is not a regular file (a symlink, directory or special entry)".format(DECL_REL))
    try:
        raw = _read_decl_bytes(path)
    except OSError as exc:
        raise DemoError("{} is unreadable ({})".format(DECL_REL, exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DemoError("{} is not valid UTF-8 ({})".format(DECL_REL, exc))
    try:
        data = tomllib.loads(text)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise DemoError("{} does not parse ({})".format(DECL_REL, exc))
    if set(data) != TOP_KEYS:
        raise DemoError("{} must carry exactly the keys {}, found {}".format(
            DECL_REL, sorted(TOP_KEYS), sorted(data)))
    version = data["format-version"]
    # The type is checked before the value: TOML 1.0 parses to a float that compares equal to 1, and a
    # bool is an int subclass.
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise DemoError("{}: format-version must be the integer 1, found {!r}".format(DECL_REL, version))
    warn = data["warn-margin-bytes"]
    if isinstance(warn, bool) or not isinstance(warn, int) or warn < 1:
        raise DemoError("{}: warn-margin-bytes must be a positive integer, found {!r}".format(DECL_REL, warn))
    rows = data["rule"]
    if not isinstance(rows, list) or not rows:
        raise DemoError("{}: rule must be a non-empty array of [[rule]] tables; an empty list is a "
                        "failure, never \"nothing to check\"".format(DECL_REL))
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise DemoError("{}: each [[rule]] row must carry exactly the keys {}, found {!r}".format(
                DECL_REL, sorted(ROW_KEYS), row))
        rid, reason = row["id"], row["reason"]
        if not isinstance(rid, str) or not CID_RE.match(rid):
            raise DemoError("{}: id {!r} is not a well-formed corpus-id".format(DECL_REL, rid))
        if not isinstance(reason, str) or not reason.strip():
            raise DemoError("{}: rule {} must carry a non-empty reason string".format(DECL_REL, rid))
        if rid in ids:
            raise DemoError("{}: rule {} is listed more than once".format(DECL_REL, rid))
        ids.append(rid)
    return ids, warn


def run(root):
    """Check the tree at root. Exit 0 when every listed rule's whole span lies inside the cap, 1 when a
    listed rule ends past it, 2 on an unreadable, malformed or ambiguous input. Parameterized on root so
    the self-test drives it against synthetic trees, never the real repo."""
    try:
        ids, warn = read_declared(root / DECL_REL)
        agents, spans, _apex_id = measure(root)
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    cap = check_release_build.CAP_BYTES  # at call time, so the consumer's cap is the only cap
    span_by_id = {rid: (start, end) for rid, start, end in spans}
    missing = [rid for rid in ids if rid not in span_by_id]
    if missing:
        print("error: {} lists {} with no measured rule span (renamed, removed, or never a corpus-id); "
              "fail-closed".format(DECL_REL, ", ".join(missing)), file=sys.stderr)
        return 2
    print("must-deliver: AGENTS.md {} bytes; cap {} (check_release_build.CAP_BYTES)".format(
        len(agents), cap))
    listed = sorted(((cap - span_by_id[rid][1], rid) for rid in ids))
    for margin, rid in listed:
        print("  {:8s}end {:6d}  margin {}".format(rid, span_by_id[rid][1], margin))
    over = [(rid, span_by_id[rid][1]) for margin, rid in listed if margin < 0]
    delivered_count = len(ids) - len(over)
    for rid, end in over:
        print("must-deliver: FAIL: rule {} ends at {}, {} bytes past the cap {}".format(
            rid, end, end - cap, cap))
    tightest_margin, tightest_rid = listed[0]
    if over:
        print("must-deliver: {} of {} listed rules delivered".format(delivered_count, len(ids)))
    else:
        print("must-deliver: {} listed rules delivered; tightest margin {} bytes ({})".format(
            len(ids), tightest_margin, tightest_rid))
    unlisted_over = [(rid, end) for rid, _start, end in spans if rid not in ids and end > cap]
    if unlisted_over:
        rid, end = unlisted_over[0]
        print("must-deliver: first unlisted rule past the cap: {} ends {} ({} past)".format(
            rid, end, end - cap))
    else:
        print("must-deliver: no unlisted rule ends past the cap")
    if not over and tightest_margin < warn:
        print("WARNING: tightest margin {} is below warn-margin-bytes {}".format(tightest_margin, warn))
    return 1 if over else 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root())


# --- self-test ------------------------------------------------------------------------------------
# Synthetic temp trees only, never the real tree, built with gen_first_pin_demo's fixture helpers
# (_write_tree, _tuned) against the real CAP_BYTES. Each vector is a discriminating fixture: it fails
# against a mutant that removes the check it covers (named per vector). Vectors:
#   T1  positive control: a listed rule ending 10 bytes inside the cap is exit 0, its margin line and the
#       tightest-margin summary printed exactly (a mutant with inverted exits, or margin computed as
#       cap - start, fails here);
#   T2  boundaries: a listed rule ending EXACTLY at the cap is exit 0; one ending ONE byte past is exit 1
#       naming the rule and the overage (a mutant testing end < cap fails the first; end <= cap + 1
#       passes the second and fails here);
#   T3  a listed rule STRADDLING the cap is exit 1 (a mutant testing the span START passes a straddler);
#   T4  spans are UTF-8 BYTES: a listed rule whose character span ends inside the cap and whose byte span
#       ends past it is exit 1 (a mutant measuring str offsets passes it);
#   T5  a stale AGENTS.md (corpus edited after it was written) is exit 2 from measure's identity proof,
#       never measured. A mutant without that proof still exits 2 on this fixture, because the edited rule
#       body is then not found in the stale bytes ("body is not found"), so the vector catches it only
#       by asserting the identity proof's own message ("not the gen_agents rendering");
#   T6  a committed demonstration listing the rule as delivered while the fresh spans put it past the cap
#       is exit 1 (a mutant reading delivered-prefix-obligations from the file passes);
#   T7  an id with no span (zzzzzz) and a clause-id (prjint1.1) are each exit 2 naming the id. zzzzzz is
#       well-formed and is refused by run's missing-span check (a mutant that silently skips unknown ids
#       passes it). prjint1.1 is refused earlier, by read_declared's corpus-id shape check (CID_RE); with
#       that check removed the missing-span check still refuses it, so this half fails only when both
#       checks are removed;
#   T8  an absent list, rule = [], and a misspelt [[rules]] table are each exit 2 (a mutant treating an
#       absent or empty list as "nothing to check" returns 0);
#   T9  an unknown row key, an integer id, an empty reason, and a duplicate id are each exit 2 (a mutant
#       ignoring unknown keys or deduplicating silently passes). The top-level values: format-version
#       1.0, 0, 2 and "1", and warn-margin-bytes 0 and -1, are each exit 2 naming the key, while
#       warn-margin-bytes 1 passes (a mutant comparing format-version by value alone passes 1.0, one
#       coercing it with int() passes "1" and 1.0, one without the value check passes 0 and 2, one
#       without the lower bound on warn-margin-bytes passes 0 and -1, and one testing warn < 2 fails 1);
#   T10 a symlinked list, a directory, invalid UTF-8, bad TOML, an injected PermissionError at the
#       declaration read, and a mode-000 list are each exit 2 (a catch-all returning 0 passes them all;
#       a read error not reported as unreadable fails the last two). The symlink case asserts the
#       regular-file check's message; the directory case does not, because without that check the read
#       raises IsADirectoryError, which also maps to exit 2. The injected error runs on every runner; the
#       mode-000 case runs only where the mode binds the runner, and the PASS line names it when skipped;
#   T11 the cap is read from check_release_build.CAP_BYTES at call time: patching it lower flips the
#       verdict to exit 1 (a mutant with a locally copied cap keeps exit 0);
#   T12 a before-rules registry block pushing a listed rule past the cap is exit 1 (a mutant reading
#       offsets relative to the rule region, without the rules_start shift, passes);
#   T13 the margin report: the unlisted-rule line, and a WARNING line under warn-margin-bytes that still
#       exits 0 (a mutant whose warning exits non-zero, or margin off by one, fails).

import gen_first_pin_demo as gfpd  # noqa: E402  fixture helpers (_write_tree, _tuned) and CAP_BYTES


def _decl(root, rows, warn=512, raw=None):
    path = root / Path(DECL_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        lines = ["format-version = 1", "warn-margin-bytes = {}".format(warn)]
        for rid, reason in rows:
            lines += ["", "[[rule]]", 'id = {!r}'.format(rid).replace("'", '"'),
                      'reason = {!r}'.format(reason).replace("'", '"')]
        raw = "\n".join(lines) + "\n"
    path.write_text(raw, encoding="utf-8")
    return path


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    CAP = check_release_build.CAP_BYTES

    def capture(root):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = run(root)
        return code, buf.getvalue()

    failures = []
    skipped = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-check-must-deliver-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        base_specs = [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400)]
        both = [("prjint1", "the apex"), ("ruleaa", "fixture rule")]

        # T1. Positive control: ruleaa ends CAP - 10; margin lines and the summary are exact.
        t1 = tmp / "t1"
        sp = gfpd._tuned(t1, base_specs, "ruleaa", CAP - 10)
        _decl(t1, both)
        code, out = capture(t1)
        if code != 0:
            failures.append("T1: expected exit 0, got {}\n{}".format(code, out))
        if "ruleaa  end {:6d}  margin 10".format(sp["ruleaa"][1]) not in out:
            failures.append("T1: the ruleaa margin line is absent or wrong:\n{}".format(out))
        if "must-deliver: 2 listed rules delivered; tightest margin 10 bytes (ruleaa)" not in out:
            failures.append("T1: the tightest-margin summary is absent or wrong:\n{}".format(out))

        # T2. Boundaries: end == CAP passes; end == CAP + 1 fails naming the rule and the overage.
        t2 = tmp / "t2"
        sp = gfpd._tuned(t2, base_specs, "ruleaa", CAP)
        _decl(t2, both)
        code, out = capture(t2)
        if sp["ruleaa"][1] != CAP or code != 0:
            failures.append("T2: a rule ending exactly at the cap must pass (fixture end {}, exit {})\n{}"
                            .format(sp["ruleaa"][1], code, out))
        sp = gfpd._tuned(t2, base_specs, "ruleaa", CAP + 1)
        code, out = capture(t2)
        if sp["ruleaa"][1] != CAP + 1 or code != 1:
            failures.append("T2: a rule ending one byte past the cap must fail (fixture end {}, exit {})\n{}"
                            .format(sp["ruleaa"][1], code, out))
        elif "FAIL: rule ruleaa ends at {}, 1 bytes past the cap {}".format(CAP + 1, CAP) not in out:
            failures.append("T2: the failure does not name the rule and its overage:\n{}".format(out))

        # T3. A listed straddler fails. rulebb starts inside the cap and ends past it.
        t3 = tmp / "t3"
        sp = gfpd._tuned(t3, base_specs, "ruleaa", CAP - 10)
        if not (sp["rulebb"][0] < CAP < sp["rulebb"][1]):
            failures.append("T3: fixture did not straddle the cap: {}".format(sp))
        _decl(t3, both + [("rulebb", "straddles")])
        code, out = capture(t3)
        if code != 1 or "FAIL: rule rulebb" not in out:
            failures.append("T3: a listed straddling rule must be exit 1 naming it, got {}\n{}".format(
                code, out))

        # T4. Bytes, not characters: U+00E9 padding puts ruleaa's character end inside the cap and its
        # byte end past it (the same construction as the generator's own multi-byte vector).
        t4 = tmp / "t4"
        two = "é"
        mb = [("prjint1", True, 0), ("ruleaa", False, two), ("rulebb", False, 100)]
        gfpd._write_tree(t4, mb)
        raw = (t4 / "AGENTS.md").read_bytes()
        lead = "Obligation text ruleaa".encode("utf-8")
        pad_b = raw.find(lead) + len(lead)
        pad_c = len(raw[:pad_b].decode("utf-8"))
        a0 = (CAP - 1 - pad_b) % 2
        m = CAP - 50 - pad_c - a0 - 1
        sp = gfpd._write_tree(t4, [("prjint1", True, 0), ("ruleaa", False, "a" * a0 + two * m),
                                   ("rulebb", False, 100)])
        raw = (t4 / "AGENTS.md").read_bytes()
        char_end = len(raw[:sp["ruleaa"][1]].decode("utf-8"))
        if not (char_end <= CAP < sp["ruleaa"][1]):
            failures.append("T4: fixture did not split characters from bytes: char end {}, byte span {}"
                            .format(char_end, sp["ruleaa"]))
        _decl(t4, both)
        code, out = capture(t4)
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T4: byte-measured spans must fail ruleaa (char span inside the cap), got {}\n{}"
                            .format(code, out))

        # T5. A stale AGENTS.md is exit 2: the corpus changes after AGENTS.md was written.
        t5 = tmp / "t5"
        gfpd._tuned(t5, base_specs, "ruleaa", CAP - 10)
        agents_before = (t5 / "AGENTS.md").read_bytes()
        rule_src = t5 / ".aiqt" / "core" / "rules" / "ruleaa.md"
        rule_src.write_text(rule_src.read_text(encoding="utf-8") + "A fresh sentence.\n", encoding="utf-8")
        (t5 / "AGENTS.md").write_bytes(agents_before)
        _decl(t5, both)
        code, out = capture(t5)
        if code != 2 or "not the gen_agents rendering" not in out:
            failures.append("T5: a stale AGENTS.md must be exit 2, never measured, got {}\n{}".format(
                code, out))

        # T6. A committed demonstration claiming ruleaa delivered is ignored: fresh spans decide.
        t6 = tmp / "t6"
        gfpd._tuned(t6, base_specs, "ruleaa", CAP + 50)
        demo = t6 / Path(gfpd.DEMO_REL)
        demo.parent.mkdir(parents=True, exist_ok=True)
        demo.write_text('agents-sha256 = "{}"\ndelivered-prefix-obligations = ["prjint1", "ruleaa"]\n'
                        'floor-profile-obligations = ["prjint1", "ruleaa"]\n'.format("0" * 64),
                        encoding="utf-8")
        _decl(t6, both)
        code, out = capture(t6)
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T6: the demonstration's delivered list must not be read; fresh spans must "
                            "fail ruleaa, got {}\n{}".format(code, out))

        # T7. An id with no span, and a clause-id, are each exit 2 naming the id.
        t7 = tmp / "t7"
        gfpd._tuned(t7, base_specs, "ruleaa", CAP - 10)
        for bad in ("zzzzzz", "prjint1.1"):
            _decl(t7, both + [(bad, "no such rule")])
            code, out = capture(t7)
            if code != 2 or bad not in out:
                failures.append("T7: id {!r} must be exit 2 naming it, got {}\n{}".format(bad, code, out))

        # T8. Absent list, empty rule array, misspelt table: each exit 2.
        t8 = tmp / "t8"
        gfpd._tuned(t8, base_specs, "ruleaa", CAP - 10)
        code, out = capture(t8)  # no must-deliver.toml written yet
        if code != 2 or "does not exist" not in out:
            failures.append("T8: an absent list must be exit 2, got {}\n{}".format(code, out))
        _decl(t8, [], raw="format-version = 1\nwarn-margin-bytes = 512\nrule = []\n")
        code, out = capture(t8)
        if code != 2 or "non-empty" not in out:
            failures.append("T8: an empty rule array must be exit 2, got {}\n{}".format(code, out))
        _decl(t8, [], raw='format-version = 1\nwarn-margin-bytes = 512\n[[rules]]\nid = "prjint1"\n'
                          'reason = "x"\n')
        code, out = capture(t8)
        if code != 2:
            failures.append("T8: a misspelt [[rules]] table must be exit 2, got {}\n{}".format(code, out))

        # T9. Unknown row key, integer id, empty reason, duplicate id, and out-of-schema top-level values:
        # each exit 2; warn-margin-bytes 1, the smallest accepted value, passes.
        t9 = tmp / "t9"
        gfpd._tuned(t9, base_specs, "ruleaa", CAP - 10)
        bad_rows = (
            ("unknown key", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = "prjint1"\n'
                            'reason = "x"\nextra = 1\n'),
            ("integer id", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = 1\nreason = "x"\n'),
            ("empty reason", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = "prjint1"\n'
                             'reason = " "\n'),
            ("duplicate id", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = "prjint1"\n'
                             'reason = "x"\n[[rule]]\nid = "prjint1"\nreason = "y"\n'),
            ("unknown top-level key", 'format-version = 1\nwarn-margin-bytes = 512\ncap = 1\n[[rule]]\n'
                                      'id = "prjint1"\nreason = "x"\n'),
            ("boolean format-version", 'format-version = true\nwarn-margin-bytes = 512\n[[rule]]\n'
                                       'id = "prjint1"\nreason = "x"\n'),
            ("boolean warn margin", 'format-version = 1\nwarn-margin-bytes = true\n[[rule]]\n'
                                    'id = "prjint1"\nreason = "x"\n'),
        )
        for label, raw in bad_rows:
            _decl(t9, [], raw=raw)
            code, out = capture(t9)
            if code != 2:
                failures.append("T9 {}: expected exit 2, got {}\n{}".format(label, code, out))
        version_msg = "format-version must be the integer 1"
        warn_msg = "warn-margin-bytes must be a positive integer"
        value_rows = (
            ("float format-version 1.0", "format-version = 1.0\nwarn-margin-bytes = 512\n", version_msg),
            ("format-version 0", "format-version = 0\nwarn-margin-bytes = 512\n", version_msg),
            ("format-version 2", "format-version = 2\nwarn-margin-bytes = 512\n", version_msg),
            ("string format-version", 'format-version = "1"\nwarn-margin-bytes = 512\n', version_msg),
            ("warn margin 0", "format-version = 1\nwarn-margin-bytes = 0\n", warn_msg),
            ("warn margin -1", "format-version = 1\nwarn-margin-bytes = -1\n", warn_msg),
        )
        for label, head, want in value_rows:
            _decl(t9, [], raw=head + '[[rule]]\nid = "prjint1"\nreason = "x"\n')
            code, out = capture(t9)
            if code != 2 or want not in out:
                failures.append("T9 {}: expected exit 2 with {!r}, got {}\n{}".format(label, want, code, out))
        _decl(t9, both, warn=1)  # the smallest positive margin is accepted
        code, out = capture(t9)
        if code != 0:
            failures.append("T9 warn margin 1: expected exit 0, got {}\n{}".format(code, out))

        # T10. Symlink, directory, invalid UTF-8, bad TOML, injected read error, mode 000: each exit 2.
        t10 = tmp / "t10"
        gfpd._tuned(t10, base_specs, "ruleaa", CAP - 10)
        decl = _decl(t10, both)
        aside = t10 / "aside.toml"
        aside.write_text(decl.read_text(encoding="utf-8"), encoding="utf-8")
        decl.unlink()
        os.symlink(aside, decl)
        code, out = capture(t10)
        if code != 2 or "not a regular file" not in out:
            failures.append("T10: a symlinked list must be exit 2, got {}\n{}".format(code, out))
        decl.unlink()
        decl.mkdir()
        code, out = capture(t10)
        if code != 2:
            failures.append("T10: a directory at the list path must be exit 2, got {}\n{}".format(code, out))
        decl.rmdir()
        decl.write_bytes(b"format-version = 1\n\xff\n")
        code, out = capture(t10)
        if code != 2 or "not valid UTF-8" not in out:
            failures.append("T10: an invalid-UTF-8 list must be exit 2, got {}\n{}".format(code, out))
        decl.write_text("format-version = = 1\n", encoding="utf-8")
        code, out = capture(t10)
        if code != 2 or "does not parse" not in out:
            failures.append("T10: a non-TOML list must be exit 2, got {}\n{}".format(code, out))
        _decl(t10, both)
        # The injected PermissionError at the declaration read runs on every runner.
        this = sys.modules[__name__]
        real_read = this._read_decl_bytes

        def denied(path):
            raise PermissionError(13, "Permission denied (injected by the self-test)", str(path))

        this._read_decl_bytes = denied
        try:
            code, out = capture(t10)
        finally:
            this._read_decl_bytes = real_read
        if code != 2 or "is unreadable" not in out or "injected by the self-test" not in out:
            failures.append("T10: an injected PermissionError at the declaration read must be exit 2 "
                            "reporting the list unreadable, got {}\n{}".format(code, out))
        os.chmod(decl, 0)
        try:
            if os.access(decl, os.R_OK):  # root or a DAC bypass: mode 000 is still readable
                print("SELF-TEST NOTE: mode-000 vector not applicable (file still readable)")
                skipped.append("the filesystem mode-000 list (this runner can read a mode-000 file; the "
                               "injected PermissionError covered the unreadable case)")
            else:
                code, out = capture(t10)
                if code != 2 or "is unreadable" not in out:
                    failures.append("T10: a mode-000 list must be exit 2, got {}\n{}".format(code, out))
        finally:
            os.chmod(decl, 0o644)

        # T11. The cap is check_release_build.CAP_BYTES at call time: patched lower, the verdict flips.
        t11 = tmp / "t11"
        gfpd._tuned(t11, base_specs, "ruleaa", CAP - 10)
        _decl(t11, both)
        code, out = capture(t11)
        if code != 0:
            failures.append("T11: the unpatched leg must pass, got {}\n{}".format(code, out))
        check_release_build.CAP_BYTES = CAP - 100
        try:
            code, out = capture(t11)
        finally:
            check_release_build.CAP_BYTES = CAP
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T11: lowering check_release_build.CAP_BYTES must flip the verdict to "
                            "exit 1, got {}\n{}".format(code, out))

        # T12. A before-rules registry block pushes ruleaa (CAP - 10 in the legacy layout) past the cap.
        t12 = tmp / "t12"
        specs = base_specs
        delta = CAP - 10 - gfpd._write_tree(t12, specs)["ruleaa"][1]
        specs = [(c, a, p + delta if c == "ruleaa" else p) for c, a, p in specs]
        before = ("OPF-SELFTEST", "AGENTS.md", "before-rules", 1, "opf/blocks/selftest.md",
                  "A reviewed block placed before the rules.\n")
        sp = gfpd._write_tree(t12, specs,
                              gfpd.adapter_compose.registry_text([("AGENTS.md", "composed")], [before]),
                              [before])
        if not sp["ruleaa"][1] > CAP:
            failures.append("T12: the before-rules block did not push ruleaa past the cap: {}".format(sp))
        _decl(t12, both)
        code, out = capture(t12)
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T12: absolute offsets must fail ruleaa once a before-rules block pushes it "
                            "past the cap, got {}\n{}".format(code, out))

        # T13. The unlisted-rule report and the warning: under warn-margin-bytes is a WARNING, still 0.
        t13 = tmp / "t13"
        sp = gfpd._tuned(t13, [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400),
                               ("rulecc", False, 30000)], "ruleaa", CAP - 10)
        _decl(t13, both, warn=64)
        code, out = capture(t13)
        if code != 0:
            failures.append("T13: expected exit 0, got {}\n{}".format(code, out))
        if "WARNING: tightest margin 10 is below warn-margin-bytes 64" not in out:
            failures.append("T13: the warning line is absent or wrong:\n{}".format(out))
        want = "first unlisted rule past the cap: rulebb ends {} ({} past)".format(
            sp["rulebb"][1], sp["rulebb"][1] - CAP)
        if want not in out:
            failures.append("T13: the unlisted-rule line is absent or wrong (want {!r}):\n{}".format(
                want, out))
        _decl(t13, both, warn=10)  # margin 10 is NOT below 10: no warning
        code, out = capture(t13)
        if code != 0 or "WARNING" in out:
            failures.append("T13: margin equal to warn-margin-bytes must not warn, got {}\n{}".format(
                code, out))
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
    print("SELF-TEST PASS: a listed rule ending at the cap passes and one ending one byte past, "
          "straddling it, or byte-measured past it fails naming the rule; a stale AGENTS.md and every "
          "malformed (a non-integer or unsupported format-version, a warn margin below 1), absent, empty, "
          "symlinked, unreadable (an injected read error) or unparseable list fail closed (exit 2); the "
          "demonstration's own lists are never read; an unknown or clause id fails closed naming it; the "
          "cap is read from check_release_build.CAP_BYTES at call time; a before-rules block moves the "
          "spans it measures; and the margin report, the unlisted-rule line and the warn-margin WARNING "
          "(still exit 0) are printed exactly. {}".format(
              "SKIPPED on this runner: {}.".format("; ".join(skipped)) if skipped
              else "No case was skipped (the mode-000 list ran too)."))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate reads child exit statuses
# through the modules it imports, so under an ignored SIGCHLD it would read a failing child as a pass;
# it refuses at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_must_deliver.py") or main())
