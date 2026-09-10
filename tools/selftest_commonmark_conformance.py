#!/usr/bin/env python3
"""CommonMark 0.31.2 conformance replay against the EXACT vendored Marko bytes (OPF U5).

Renders every official CommonMark 0.31.2 spec example with the vendored marko and compares the output to
the spec's expected HTML, so the vendored parser is proven to be genuine CommonMark 0.31.2 and not a
substituted or drifted copy. This is a SEPARATE harness from the parse-only gate path: it deliberately
renders HTML (via a fresh marko.Markdown()) to compare against the spec, which the gate adapter never does.

The spec example fixture (the CommonMark 0.31.2 examples JSON, a list of {markdown, html, example, ...}) is
an ORCHESTRATOR DEPENDENCY: it is acquired and vendored by the orchestrator at the path named below. This
harness FAILS CLOSED (never skips) if the fixture is absent, unreadable, malformed, or empty
(check-fails-closed-on-unreadable; guard-input-soundness).

Usage:
    python3 -I -B tools/selftest_commonmark_conformance.py
        Run the replay under the CURRENT interpreter. Exit 0 clean, 1 on a divergence outside the recorded
        baseline, 2 on a fail-closed harness error (missing fixture, wrong vendored bytes, etc.).
    python3 -I -B tools/selftest_commonmark_conformance.py --interpreters <interp> [<interp> ...]
        OPTIONAL maintainer facility (NOT gate-wired): re-spawn this same harness under each NAMED interpreter
        and aggregate. A named interpreter that is absent is a fail-closed error, not a skip. The pack is
        standardized on CPython 3.14 and the gate uses the single-interpreter form above; a cross-version
        matrix was declined by the maintainer (2026-09-10).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _commonmark_headings as ch        # noqa: E402  reuse the pinned, containment-checked marko loader

# Named fixture path (orchestrator-vendored). The harness reads ONLY this path and fails closed if absent.
SPEC_FIXTURE = os.path.join(ch._VENDOR_DIR, "commonmark-spec-0.31.2", "spec-0.31.2-examples.json")

# The set of spec example numbers marko 2.2.4 is known to render differently from the 0.31.2 expected HTML.
# ORCHESTRATOR-FILLED after MEASURING the replay against the real vendored bytes. Measured on CPython 3.14.4
# (the host interpreter) against the vendored marko 2.2.4 bytes: 16 render divergences out of 652 examples,
# none heading-related. The OPF changelog adapter (tools/_commonmark_headings.py) uses marko's PARSER only,
# never its renderer, so none of these divergences touches OPF heading extraction; they are recorded here to
# keep the conformance replay honest rather than presenting an unmeasured 100%-pass claim as fact. This
# baseline is verified on CPython 3.14 ONLY: the host and the quality CI workflow that gates U5 both run it on
# 3.14, and a cross-version 3.12+3.14 matrix was declined by the maintainer (2026-09-10) as backward. The
# replay fails closed when the observed divergence SET (which examples diverge) is not exactly this set, so a
# re-vendoring or a 3.14 change that moves any example into or out of divergence is caught; a change to an
# already-divergent example's OUTPUT that leaves it divergent is a disclosed residual the set comparison does
# not see. marko is pure-Python with no version-gated rendering
# path; should a specific older interpreter ever matter, the --interpreters facility can re-measure it.
# Divergence classes: tabs (9), paragraphs
# (226), list items (280-284, 294, 296), lists (307, 315, 319-321, 323), hard line breaks (645).
KNOWN_DIVERGENT_EXAMPLES = frozenset({9, 226, 280, 281, 282, 283, 284, 294, 296, 307, 315, 319, 320, 321, 323, 645})

# A sanity floor so a truncated fixture cannot pass as a clean run. ORCHESTRATOR-PINNED to the exact example
# count of the committed 0.31.2 fixture; None here means "require at least one and print the count to pin".
EXPECTED_EXAMPLE_COUNT = 652  # CommonMark 0.31.2 spec.json example count (measured from the vendored fixture)


def _load_examples():
    """Load and validate the spec example fixture. Fails closed (HeadingScanError-style RuntimeError) on an
    absent, unreadable, malformed, or empty fixture, or one whose records lack the required keys."""
    try:
        with open(SPEC_FIXTURE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise RuntimeError("spec fixture {} is absent or unreadable: {}".format(SPEC_FIXTURE, exc))
    except ValueError as exc:
        raise RuntimeError("spec fixture {} is not valid JSON: {}".format(SPEC_FIXTURE, exc))
    if not isinstance(data, list) or not data:
        raise RuntimeError("spec fixture {} is empty or not a list".format(SPEC_FIXTURE))
    for i, ex in enumerate(data):
        if not isinstance(ex, dict) or "markdown" not in ex or "html" not in ex:
            raise RuntimeError("spec fixture record {} is malformed (needs markdown + html keys)".format(i))
    if EXPECTED_EXAMPLE_COUNT is not None and len(data) != EXPECTED_EXAMPLE_COUNT:
        raise RuntimeError("spec fixture has {} examples, expected {}".format(len(data), EXPECTED_EXAMPLE_COUNT))
    return data


def _classify(examples, renderer):
    """Render each example and classify it. Returns (divergent, crashed, seen). A render CRASH is recorded
    SEPARATELY from an output DIFFERENCE so a crash can never be absorbed into the known-divergent baseline;
    `seen` is every evaluated example id, for the fixture-identity check. Extracted so _self_check can
    exercise the crash-never-absorbed and identity logic on synthetic input (change-carries-a-check)."""
    divergent, crashed, seen = [], [], []
    for ex in examples:
        number = ex.get("example")
        seen.append(number)
        try:
            rendered = renderer.convert(ex["markdown"])
        except Exception as exc:          # a render crash is a hard failure, NOT a recorded divergence
            crashed.append((number, "render error: {}".format(exc)))
            continue
        if rendered != ex["html"]:
            divergent.append((number, "output differs from expected"))
    return divergent, crashed, seen


def _matrix_worst(returncodes):
    """The worst (highest) severity over the interpreter legs. A NEGATIVE returncode is a signal kill, so
    abs() keeps it a failure rather than letting max() rank it below success. Empty -> 0 (no legs ran)."""
    return max((abs(rc) for rc in returncodes), default=0)


def _self_check():
    """Exercise the classification and matrix-aggregation logic the gate's verdict rests on, so a regression
    of the crash-never-absorbed, fixture-identity, or signal-killed-leg hardening fails THIS gate rather than
    passing silently (change-carries-a-check). Returns 0 on success, 2 on a logic regression."""
    class _R:
        def convert(self, md):
            if md == "BOOM":
                raise RuntimeError("injected render fault")
            return md
    r = _R()
    problems = []
    div, crashed, _seen = _classify(
        [{"example": 9, "markdown": "BOOM", "html": "x"},
         {"example": 10, "markdown": "a", "html": "a"}], r)
    if not crashed or crashed[0][0] != 9:
        problems.append("a render crash was not recorded as a crash")
    if div:
        problems.append("a crash was absorbed as an output divergence")
    _, _, seen_dup = _classify(
        [{"example": 1, "markdown": "a", "html": "a"},
         {"example": 1, "markdown": "a", "html": "a"}], r)
    if sorted((n for n in set(seen_dup) if seen_dup.count(n) > 1), key=lambda n: (n is None, n)) != [1]:
        problems.append("a duplicate example id was not detected")
    _, _, seen_nul = _classify([{"example": None, "markdown": "a", "html": "a"}], r)
    if seen_nul.count(None) != 1:
        problems.append("a null example id was not detected")
    if _matrix_worst([-9, 0]) != 9:
        problems.append("a signal-killed matrix leg (returncode -9) did not rank as a failure")
    if _matrix_worst([0, 0]) != 0:
        problems.append("a clean matrix run did not rank as success")
    # production-verdict guard: with the baseline reproduced (so unexpected/missing are empty), each of a
    # duplicate id, a null id, a render crash, and a MIXED null+duplicate (which also exercises the None-safe
    # sort key) drives the verdict to REJECT (exit 1) in isolation, and a wholly clean classification is
    # ACCEPTED (exit 0); removing the identity/crash guard, or the None-safe sort key, from _verdict fails here.
    baseline_div = [(n, "x") for n in KNOWN_DIVERGENT_EXAMPLES]
    clean_seen = list(KNOWN_DIVERGENT_EXAMPLES)
    if _verdict(baseline_div, [], clean_seen)[0] != 0:
        problems.append("the verdict wrongly rejected a clean classification")
    if _verdict(baseline_div, [], clean_seen + [clean_seen[0]])[0] != 1:
        problems.append("the verdict did not reject a duplicate example id")
    if _verdict(baseline_div, [], clean_seen + [None])[0] != 1:
        problems.append("the verdict did not reject a null example id")
    if _verdict(baseline_div, [(9999, "boom")], clean_seen)[0] != 1:
        problems.append("the verdict did not reject a render crash")
    try:
        if _verdict(baseline_div, [], clean_seen + [clean_seen[0], None, None])[0] != 1:
            problems.append("the verdict did not reject a mixed null+duplicate classification")
    except Exception as exc:
        problems.append("the verdict raised on a mixed null+duplicate classification: {}".format(exc))
    for p in problems:
        print("CONFORMANCE SELF-CHECK FAIL: {}".format(p), file=sys.stderr)
    return 2 if problems else 0


def _verdict(divergent, crashed, seen):
    """The gate verdict over one classification. Returns (exit_code, observed, unexpected, missing,
    duplicates, null_ids); exit_code is 1 when any crash, unexpected or stale divergence, duplicate id, or
    null id is present, else 0. Extracted so _self_check can prove the PRODUCTION rejection (not only the
    classification) fails when the identity or crash guard, or the None-safe sort key, is removed."""
    observed = frozenset(n for n, _why in divergent)
    unexpected = observed - KNOWN_DIVERGENT_EXAMPLES
    missing = KNOWN_DIVERGENT_EXAMPLES - observed
    duplicates = sorted((n for n in set(seen) if seen.count(n) > 1), key=lambda n: (n is None, n))
    null_ids = seen.count(None)
    code = 1 if (crashed or duplicates or null_ids or unexpected or missing) else 0
    return code, observed, unexpected, missing, duplicates, null_ids


def _run_single():
    """Run the replay under the current interpreter. Returns an exit code (0/1/2)."""
    try:
        marko = ch._load_marko()          # containment + version 2.2.4 check on the vendored bytes
        examples = _load_examples()
    except (ch.HeadingScanError, RuntimeError) as exc:
        print("CONFORMANCE SELF-TEST ERROR ({}): {}; fail-closed".format(
            sys.version.split()[0], exc), file=sys.stderr)
        return 2

    renderer = marko.Markdown()           # default HTMLRenderer, no extensions; single-threaded use here
    divergent, crashed, seen = _classify(examples, renderer)
    code, observed, unexpected, missing, duplicates, null_ids = _verdict(divergent, crashed, seen)

    print("CONFORMANCE ({}): {} examples, {} divergent, baseline {}".format(
        sys.version.split()[0], len(examples), len(observed), len(KNOWN_DIVERGENT_EXAMPLES)))
    if EXPECTED_EXAMPLE_COUNT is None:
        print("  NOTE: pin EXPECTED_EXAMPLE_COUNT to {} once the fixture is committed".format(len(examples)))
    if crashed:
        for number, why in sorted(crashed, key=lambda d: (d[0] is None, d[0])):
            print("  RENDER CRASH example {}: {}".format(number, why))
    if duplicates or null_ids:
        print("  FIXTURE IDENTITY: {} duplicate example id(s) {}, {} null id(s)".format(
            len(duplicates), duplicates, null_ids))
    if unexpected:
        for number, why in sorted((d for d in divergent if d[0] in unexpected), key=lambda d: (d[0] is None, d[0])):
            print("  UNEXPECTED DIVERGENCE example {}: {}".format(number, why))
    if missing:
        # A recorded divergence that no longer reproduces is also a mismatch: the baseline must be re-measured.
        print("  BASELINE STALE: examples no longer divergent: {}".format(sorted(missing)))
    if code:
        return 1
    print("CONFORMANCE ({}): PASS".format(sys.version.split()[0]))
    return 0


def _run_matrix(interpreters):
    """Re-spawn this harness under each named interpreter and aggregate (an OPTIONAL, not gate-wired,
    maintainer facility). A missing named interpreter is a fail-closed error, not a skip. The gate itself
    runs the single-interpreter form; the pack is standardized on CPython 3.14."""
    script = os.path.abspath(__file__)
    codes = []
    for interp in interpreters:
        exe = shutil_which(interp)
        if exe is None:
            print("CONFORMANCE MATRIX ERROR: required interpreter {!r} not found; fail-closed".format(interp),
                  file=sys.stderr)
            codes.append(2)
            continue
        proc = subprocess.run([exe, "-I", "-B", script], capture_output=False)
        codes.append(proc.returncode)
    return _matrix_worst(codes)


def shutil_which(name):
    """A minimal PATH lookup (stdlib shutil.which), factored so the intent is explicit."""
    import shutil
    return shutil.which(name)


def main(argv):
    selfcheck = _self_check()
    if selfcheck:
        return selfcheck
    if argv and argv[0] == "--interpreters":
        interpreters = argv[1:]
        if not interpreters:
            print("CONFORMANCE MATRIX ERROR: --interpreters needs at least one interpreter; fail-closed",
                  file=sys.stderr)
            return 2
        return _run_matrix(interpreters)
    if argv:
        print("CONFORMANCE ERROR: unknown argument {!r}; fail-closed".format(argv[0]), file=sys.stderr)
        return 2
    return _run_single()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
