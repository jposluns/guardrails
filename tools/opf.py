#!/usr/bin/env python3
"""opf: the DevProcess (OPF) reference-tooling dispatcher (OPF core-tooling, skeleton from U1).

  opf.py --self-test                run every registered OPF helper self-test (the CI leg)
  opf.py <verb> [--root DIR] ...    a store verb (default --root: the cwd product repository root)

This is the SKELETON dispatcher the OPF core-tooling units grow into. U1 lands it with the store-side
helper self-test wired in; the store verbs (`render`, `import`, `doctor`, and the rest of the spec's
command vocabulary) are recognized names that report NOT-YET-IMPLEMENTED and fail closed (exit 2) until
their unit lands, so a stub can never read as a passing operation.

Adopter-rooted, like doctor.py/migrate.py/conformance.py: an OPF verb operates on a PRODUCT repository
root named by --root (default: the cwd), never on this pack's own tree via `_gen_common.repo_root()`.
The pack is not a DevProcess adopter, so a live `opf.py <verb> --root .` here reports NOT APPLICABLE
once the verbs land; the assurance rides the `--self-test` leg over synthetic stores (spec-honest,
mirroring the crosswalk/doctor/migrate legs in run_all_checks.sh).

Deliberately NOT named tools/gen_*.py: the generated-source registry (gen_gensrc.py) discovers gen_*.py
and validates fixed repo-relative targets, but OPF renders into an adopter --root with no fixed
repo-relative target, so this family gates as self-tests instead (the U1 build-plan section 5 rule).

Launched isolated (-I -B) per the Python-launcher-isolation gate; sibling helpers are imported through
the sys.path insert idiom the repo's tools share.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store  # noqa: E402  U1: store resolution + discovery + manifest base/profile schema

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_MALFORMED = 2

# Registered helper self-tests, run by `opf.py --self-test`. Each is (label, callable) returning a
# 0/1/2 exit code (0 clean, 1 finding, 2 cannot-evaluate). Later units append their own helper here.
SELF_TESTS = (
    ("opf-store", _opf_store.self_test),
)

# The spec's command vocabulary (spec 1). Each lands in its own unit; until then a verb fails closed.
KNOWN_VERBS = ("init", "import", "doctor", "render", "migrate", "sync")


def run_self_tests():
    """Run every registered helper self-test in order, forwarding each result. The aggregate exit code
    is the WORST outcome (2 cannot-evaluate > 1 finding > 0 clean): one degraded or failing helper fails
    the whole leg, never masked by a later clean one."""
    worst = EXIT_OK
    for label, fn in SELF_TESTS:
        print("== opf self-test: {} ==".format(label))
        code = fn()
        if code == EXIT_MALFORMED:
            worst = EXIT_MALFORMED
        elif code == EXIT_FINDING and worst != EXIT_MALFORMED:
            worst = EXIT_FINDING
    return worst


def main():
    args = sys.argv[1:]
    if args == ["--self-test"]:
        return run_self_tests()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return EXIT_MALFORMED
    verb = args[0]
    if verb in KNOWN_VERBS:
        # A recognized verb whose unit has not landed: fail closed (exit 2), never a silent success, so
        # a stub is never mistaken for a completed operation.
        print("opf {}: not yet implemented in this build (fail-closed)".format(verb), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf: unknown verb {!r}; known verbs: {}".format(verb, ", ".join(KNOWN_VERBS)), file=sys.stderr)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
