#!/usr/bin/env python3
"""Shared --outcome-report seam for the self-test suites the discrimination-audit gate drives.

A suite that routes every fixture through ONE `check(name, cond)` choke point adopts this seam so
`tools/check_discrimination_audit.py` can read a STRUCTURED per-id outcome instead of grepping
`FAILED:` lines (10-QUALI-lightweight-verifier-workers, 10-QUALI-verifier-delivery-completeness). It is
the discrimination-audit sibling of the `--execution-report` convention in check_selftest_execution.py.

Adoption is two lines plus one call, and changes NOTHING about the suite's own 0/1 exit or its output
when `--outcome-report` is not passed:

    from _audit_outcome import OutcomeReport
    _outcome = OutcomeReport("<suite-id>")          # parses --outcome-report <abs-path> from sys.argv
    def check(name, cond):
        ...existing bookkeeping...
        _outcome.record(name, bool(cond))
    ...
    _outcome.write()                                # no-op unless --outcome-report was passed

The report is a JSON object written for EVERY check reached:

    {"format_version": 1, "suite": "<id>", "checks": {"<id>": "passed" | "failed"}}

and the suite still returns its normal exit code. The gate reads the report as its authoritative verdict
evidence (never the suite's prose): it caches the pristine (unparsed) baseline, then asserts that a
claimed negative fixture flips from "passed" to "failed" (or becomes absent) under a deliberate guard
neuter. A report that is missing, malformed, wrong-suite, or duplicate-keyed is cannot-evaluate to the
gate, never a pass, so this writer stays deliberately small and total: it records the outcome of every
check and writes one well-formed object.

`--outcome-report` is the ONLY flag this seam consumes; every other argv token is left untouched for the
host suite, so a suite whose __main__ ignores argv keeps working. A repeated check id is a suite-authoring
defect (two fixtures cannot share one id); the gate's static classifier refuses a duplicate check id
before any run, so this writer simply records last-wins and leaves that refusal to the gate.

DELIVERY ON CRASH. The gate deliberately reverts a production guard, and a reverted guard can let a LATER
fixture raise (the guard existed to prevent exactly that). So the report must be delivered even when the
suite raises before its explicit write(): the constructor registers an atexit flush, which the interpreter
runs during shutdown after an unhandled exception too, so every check reached BEFORE the crash (including a
claimed fixture that flipped to "failed") is still captured. This is what lets the gate see a genuine
passed->failed flip rather than a missing report when a guard revert is load-bearing enough to crash a
sibling fixture.
"""
import atexit
import json
import sys


class OutcomeReport:
    """Collects per-check outcomes and, only when `--outcome-report <path>` was passed, writes the
    structured report the discrimination-audit gate reads. Constructed once per suite run."""

    FLAG = "--outcome-report"

    def __init__(self, suite_id, argv=None):
        if not isinstance(suite_id, str) or not suite_id:
            raise ValueError("OutcomeReport suite_id must be a non-empty string")
        self.suite_id = suite_id
        self.path = self._parse_path(sys.argv[1:] if argv is None else list(argv))
        self.checks = {}
        # Deliver whatever was recorded even if the suite raises before its explicit write(): atexit
        # handlers run during interpreter shutdown after an unhandled exception too (only a signal kill,
        # os._exit, or a fatal interpreter error skip them). A guard the discrimination-audit gate reverts
        # can make a LATER fixture crash, so this is what preserves the flip evidence of the checks that
        # already ran. Registered only when a report was actually requested.
        if self.path is not None:
            atexit.register(self.write)

    def _parse_path(self, argv):
        """Return the --outcome-report path (supporting both `--outcome-report P` and
        `--outcome-report=P`), or None when the flag is absent. A flag with no following path is a usage
        error rather than a silent no-op, so a mis-launched child fails loudly instead of writing nothing
        (which the gate would then read as a missing report and fail closed anyway)."""
        i = 0
        path = None
        while i < len(argv):
            token = argv[i]
            if token == self.FLAG:
                if i + 1 >= len(argv):
                    raise SystemExit("{} requires a path argument".format(self.FLAG))
                path = argv[i + 1]
                i += 2
                continue
            if token.startswith(self.FLAG + "="):
                path = token[len(self.FLAG) + 1:]
                if not path:
                    raise SystemExit("{} requires a non-empty path argument".format(self.FLAG))
                i += 1
                continue
            i += 1
        return path

    def record(self, name, passed):
        """Record one check's outcome. `passed` is coerced to a bool so the recorded value is always
        exactly "passed" or "failed"."""
        self.checks[name] = "passed" if passed else "failed"

    def write(self):
        """Write the structured report iff --outcome-report was passed; otherwise a no-op. Writes one
        well-formed JSON object; the gate validates its shape and rejects anything malformed."""
        if self.path is None:
            return
        payload = {"format_version": 1, "suite": self.suite_id, "checks": self.checks}
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
