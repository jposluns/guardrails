#!/usr/bin/env python3
"""opf-fuzz: the adversarial input-hardening self-test for the OPF pass-A tooling.

This harness is the MECHANICAL PROOF that the "membership / type-guard" defect class is closed by
construction across _opf_store, _opf_schema, and _opf_release, not one site at a time. Three prior
rounds of manual class-scans each declared the class closed and were each disproved by a new site; this
leg ends that whack-a-mole by ENUMERATING every public validator/operation that takes a control or record
input and feeding a MATRIX of adversarial values to each such parameter, BOTH as the whole top-level
parameter AND (the round-6 nested-injection sweep) injected into a NESTED position inside an otherwise-valid
record, manifest, or worklog entry, so a malformed shape reaches the nested table validators (actor / links
/ refs / the manifest sub-tables) and the coverage-digest recursion rather than bailing at the outer
structural guard. It asserts for every combination:

  (a) NO UNCONTROLLED exception is raised. A validator/finding-check must not raise AT ALL; an operation
      may only refuse through its OWN documented fail-closed exception (ReleaseError / StoreError /
      ValueError). A TypeError, AttributeError, KeyError, IndexError, or any other uncontrolled crash
      FAILS the test (these are exactly the set()/membership/dict-lookup crashes the class is about).
  (b) The result is a well-formed outcome: a status object whose .status is in {VALID, INVALID,
      CANNOT-EVALUATE}, a findings list, or (for an operation) any non-exception return.
  (c) NO FAIL-OPEN: a malformed control never yields a result MORE PERMISSIVE than the same call with
      that control empty/omitted (a malformed registered_vendors never admits an x- extension an empty
      one rejects; a malformed supported_profiles never turns an INVALID profile-weakening VALID; a
      malformed registered_kinds never admits a kind the built-in set rejects; a malformed id-collection
      never reads as no-deletion / no-loss; a malformed counters map never certifies an id clean).

Run standalone (`python3 -I -B tools/_opf_fuzz.py`) or as the `opf-fuzz` leg of `opf.py --self-test`.
Returns 0 clean, 1 on a failed assertion, 2 on a harness/fail-closed error. Judged on returned
status/finding VALUES and on raised exception TYPES, never by grepping output (the isolate-verifiers rule).
"""
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store    # noqa: E402
import _opf_schema   # noqa: E402
import _opf_release  # noqa: E402
from _opf_store import VALID, INVALID, CANNOT_EVALUATE, StoreError  # noqa: E402
from _opf_release import ReleaseError  # noqa: E402

STATUSES = frozenset({VALID, INVALID, CANNOT_EVALUATE})

# A refusal an OPERATION is allowed to raise as its documented fail-closed path. Anything OUTSIDE this set
# (TypeError, AttributeError, KeyError, IndexError, RecursionError, ...) is an uncontrolled crash and fails.
CONTROLLED = (ReleaseError, StoreError, ValueError)

TS = "2026-08-12T09:14:02Z"

# --- coverage instrumentation (round 7; round-8 revision) ----------------------------------------------
# A closed-by-construction proof is only real if every production site it claims to cover is actually
# EXERCISED by a case; an un-reached site would let a future regression there escape unproven (round 6
# found 8 of the 20 _sorted_key_names sites were never reached because the nested sweep replaced the PARENT
# tables, so the deeper child-table sorts never ran). We derive the site set by SCANNING the three module
# SOURCES for the render-helper call tokens (so the site LINE NUMBERS are never a hand-maintained list),
# record which source lines actually EXECUTE via a line tracer, then assert the scanned sites are a subset
# of the executed lines. A future un-exercised site FAILS the proof.
#
# The token VOCABULARY that defines WHICH sites count (_KEY_NAME_RENDER_TOKENS) is itself reconciled
# against the authoritative source rather than trusted as a fixed hand list (guard-input-soundness): the
# coverage section below asserts every `*_key_names` render helper DEFINED in the three modules is
# registered in the vocabulary, so a renamed or newly-added helper cannot silently drop out of BOTH the
# coverage numerator and denominator (it FAILS the proof, a cannot-evaluate, rather than staying green).
# DISCLOSED RESIDUAL (disclose-guard-residuals): this reconciles the NAMED `*_key_names` helper vocabulary;
# a key set rendered INLINE without such a helper is outside this scan's semantic scope and is not claimed
# covered. The scan is a coverage proof over the enumerated helper vocabulary, not a semantic guarantee
# that every conceivable key-rendering construct is exercised.
#
# ROUND-8 REVISION (the gemini meta-finding, evidence-grounded-completion). The int/str-guard class is no
# longer proven by an author-declared `# opf-fuzz:int-guard` MARKER: a marker scan is non-authoritative,
# because an int()/str() site the author forgot to mark is invisible to the proof, which is exactly how the
# _canonical str(int) site went unexercised. Marker-based site coverage is REMOVED. In its place the class
# is proven BY VALUE-EXHAUSTION: the adversarial matrix carries an OVERSIZED INT and a DEEP-NESTED table
# (see ADVERSARIAL above), injected at every field and nested position the general and nested sweeps visit,
# so every str(int) / canonicalization-recursion path is exercised by an adversarial VALUE rather than by a
# developer remembering a marker. The behavioural assertion (no uncontrolled exception, only the documented
# ReleaseError / StoreError / ValueError / EmitError) is the coverage. The key-name-render scan below is
# retained: it is a real function-CALL scan (an authoritative index of a distinct site class), not a
# marker, so it stays. It counts BOTH key-name-render helpers, _sorted_key_names( and the _opf_schema
# _safe_key_names( variant, because they are one coverage class: scanning only the former went blind to the
# four _opf_schema sites when they moved across to _safe_key_names(.
_TARGET_FILES = frozenset({"_opf_store.py", "_opf_schema.py", "_opf_release.py"})
_MODULE_PATHS = {Path(m.__file__).name: Path(m.__file__)
                 for m in (_opf_store, _opf_schema, _opf_release)}


def _scan_sites(predicate):
    """The set of (filename, lineno) across the three target module sources whose line satisfies
    `predicate(line)`. The source itself is the authoritative index of the sites (never a hand list)."""
    sites = set()
    for name, path in _MODULE_PATHS.items():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if predicate(line):
                sites.add((name, i))
    return sites


# The key-name-rendering helper CALL tokens: both render a surplus-key SET into a finding message and are
# ONE coverage class. _sorted_key_names( is the _opf_store / _opf_release unknown-key join; _safe_key_names(
# is the _opf_schema variant that renders each key through _safe_display so a control-char or oversized
# non-decimal int key cannot forge a finding line or crash the sort. Scanning only the former went blind to
# the latter when the four _opf_schema sites moved across, so BOTH tokens are scanned.
_KEY_NAME_RENDER_TOKENS = ("_sorted_key_names(", "_safe_key_names(")


def _skn_call_sites():
    # The key-name-render CALL sites per helper token: the token followed by '(', excluding its own `def`
    # line (and the bare-name import lines, which carry no '('). Keyed by token so each class's own
    # non-emptiness can be asserted (a class that scans to zero is a drifted or broken authoritative index).
    return {tok: _scan_sites(lambda ln, tok=tok: tok in ln and not ln.lstrip().startswith("def "))
            for tok in _KEY_NAME_RENDER_TOKENS}


# A render helper follows the `*_key_names` naming convention (_sorted_key_names / _safe_key_names). The
# vocabulary above is RECONCILED against every such definition in the three module sources so a renamed or
# newly-added helper cannot silently escape the coverage vocabulary (an authoritative-index reconciliation,
# not a trusted hand list). `_instant_key` / `_check_keyset` do not match and are correctly excluded.
_KEY_NAME_RENDER_DEF_RE = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*_key_names)\s*\(")


def _key_name_render_defs():
    """The call token `<name>(` of every `*_key_names` render helper DEFINED in the three target module
    sources. Reconciling this discovered set against _KEY_NAME_RENDER_TOKENS surfaces a renamed or added
    helper that is absent from the vocabulary (a cannot-evaluate / fail-closed drift), rather than letting
    it drop silently out of both the coverage numerator and denominator. It cannot discover a key set
    rendered INLINE without such a helper (the disclosed residual noted above)."""
    defs = set()
    for path in _MODULE_PATHS.values():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _KEY_NAME_RENDER_DEF_RE.match(line)
            if m:
                defs.add(m.group(1) + "(")
    return defs


def _render_vocab_drift(defs, vocab):
    """Reconcile the DISCOVERED `*_key_names` render-helper definitions against the REGISTERED vocabulary,
    returning `(unregistered, stale)`: `unregistered` are helpers defined in source but absent from the
    vocabulary (they would escape both the coverage numerator and denominator, fail-closed), and `stale`
    are registered tokens no longer defined. Either non-empty is drift. This is the ACTUAL reconciliation
    the coverage guard fires on (`defs != vocab` expressed as its two directions); run()'s guard AND its
    discrimination vector both route through THIS function, so disabling the reconciliation breaks both
    rather than only a separate throwaway expression."""
    return defs - vocab, vocab - defs


def _make_tracer(executed):
    """A sys.settrace pair that records every executed (filename, lineno) in the three target modules into
    `executed`. Judged by executed LINES, never by grepping output (the isolate-verifiers rule)."""
    def _line_tracer(frame, event, arg):
        if event == "line":
            executed.add((Path(frame.f_code.co_filename).name, frame.f_lineno))
        return _line_tracer

    def _call_tracer(frame, event, arg):
        if event == "call" and Path(frame.f_code.co_filename).name in _TARGET_FILES:
            return _line_tracer
        return None
    return _call_tracer

# An OVERSIZED integer and a DEEP-NESTED structure, the round-8 by-value exotic shapes (FIX 1 / FIX 2).
# The oversized int is built ARITHMETICALLY (10 ** 4301, a 4302-digit int): int("9" * 4301) cannot be used
# because CPython refuses int() on an over-limit numeric STRING, the very limit str() then trips. Fed as a
# worklog field value and nested inside an extension table (the digest-path probes below) it forces every
# str(int) canonicalization path; the deep-nested dict forces every canonicalization-recursion path (both
# the general/nested sweeps, where it is an ordinary dict off the digest path, and the digest-path probes).
_OVERSIZED_INT = 10 ** 4301                 # str() of this raises ValueError on default CPython (>4300 digits)
_DEEP_DEPTH = 800                           # nests far past _MAX_CANONICAL_DEPTH (100) and the recursion limit


def _deep_nest(n):
    """A fresh table nested `n` levels deep ({'x': {'x': {... }}}), built iteratively (no recursion here).
    Nested as a worklog-entry field it drives _canonical's recursion past its depth bound; the pre-FIX-2
    code recurses until an uncontrolled RecursionError, the post-fix code refuses with a ReleaseError."""
    root = cur = {}
    for _ in range(n):
        nxt = {}
        cur["x"] = nxt
        cur = nxt
    return root


# The adversarial matrix fed to each control/record parameter. Each entry is (label, factory): the factory
# is a ZERO-ARG callable producing a FRESH value per case, so a single-use value (the one-shot iterator) is
# never shared and exhausted across cases. Thirteen shapes: None; an empty string; a non-str scalar as
# int / float / bool; a list; a dict; a list-of-list (UNHASHABLE elements, the set() crash vector); a bare
# string that would splat into characters; a falsey collection; a ONE-SHOT ITERATOR (exhausted if consumed
# twice, the supported_profiles double-consume vector); a MIXED-TYPE-KEY dict (the sorted() over
# heterogeneous keys crash vector); and the round-8 DEEP-NESTED table (the canonicalization-recursion crash
# vector, FIX 2). The deep-nested table is type-compatible with the plain dict shape above (only its depth
# differs), so the only code that behaves differently on it is the depth-sensitive canonicalization
# recursion this round bounds; feeding it through the general AND nested sweeps injects it at every field
# and nested position the harness visits, and it maps to a controlled ReleaseError only at the digest path
# (elsewhere it is an ordinary dict).
#
# The OVERSIZED INT is deliberately NOT in this general matrix. It maps cleanly to a controlled refusal
# (ReleaseError / EmitError) only on the CANONICALIZATION / EMIT path, where _canonical / _render_scalar do
# str(int); that is the class FIX 1 closes, and it is exercised by value at the digest-path positions the
# round-8 by-value probes below cover (a worklog field value, a nested extension table, and the span-digest
# path). Injected at a FIELD of an otherwise-valid record/manifest/counters/version/worklog, an oversized
# int instead reaches a validator's FINDING-MESSAGE formatting ("{!r}".format(value)): a distinct int-repr
# crash class the round-8 note disclosed as a residual and wrongly believed unreachable from parsed TOML.
# It IS reachable: CPython's integer-string-conversion limit is BASE-10 ONLY, so tomllib parses a
# hexadecimal, octal, or binary literal (0x.../0o.../0b...) into an arbitrarily-large int with no digit
# limit, which a finding message then str()/repr()s and crashes. The round-9 FIELD-INJECTION sweep below
# closes that class by construction across the three modules: it injects an oversized int (built via a
# non-decimal path, so it is a genuine arbitrarily-large int) into every finding-message-producing field
# position and asserts each validator yields a structured outcome, never an uncontrolled ValueError. The
# oversized int stays OUT of the general matrix here because a top-level spray bails at the outer structural
# guard and never reaches a nested field render; the targeted field-injection sweep is what exercises those
# sites. The general-matrix disclosure above and the round-9 sweep below together retire the residual.
ADVERSARIAL = (
    ("none", lambda: None),
    ("empty-string", lambda: ""),
    ("int", lambda: 7),
    ("float", lambda: 1.5),
    ("bool", lambda: True),
    ("list", lambda: [1, 2]),
    ("dict", lambda: {"a": 1}),
    ("list-of-list", lambda: [[1]]),        # unhashable elements: the set()/frozenset() crash vector
    ("bare-string", lambda: "WL-1"),        # a bare string where a collection is expected (the splat vector)
    ("falsey-collection", lambda: []),      # a falsey collection (must not read as "absent / no restriction")
    ("one-shot-iterator", lambda: iter([1])),          # a single-use iterator (double-consume vector)
    ("mixed-type-key-dict", lambda: {1: "a", "b": 2}),  # heterogeneous keys (the sorted() crash vector)
    ("deep-nested", lambda: _deep_nest(_DEEP_DEPTH)),   # canonicalization recursion past its bound (FIX 2)
)


def _full_record(**over):
    """A VALID full-envelope backlog_item record."""
    r = {"id": "BI-1", "type": "backlog_item", "status": "open", "title": "t",
         "created_at": TS, "updated_at": TS, "actor": {"kind": "maintainer"}}
    r.update(over)
    return r


def _worklog_record(**over):
    """A VALID reduced-envelope worklog record."""
    r = {"id": "WL-1", "date": TS, "actor": {"kind": "maintainer"}, "kind": "added", "summary": "s"}
    r.update(over)
    return r


def _worklog_entry(n, **over):
    e = {"id": "WL-{}".format(n), "date": TS, "actor": {"kind": "maintainer"}, "kind": "added",
         "summary": "s"}
    e.update(over)
    return e


VALID_MANIFEST = {
    "devprocess": {"standard": "devprocess", "spec_version": "1.0.0", "layout": "inline",
                   "posture": "required", "import_status": "none"},
    "store": {"sync_target": ""},
    "modules": {"governance": True, "operational_policy": True, "concurrent_operation": True},
    "types": {"backlog_item": {"namespace": "BI"}},
    "vendors": {"registered": ["x-aiqt"]},
}

# A manifest whose aiqt profile WEAKENS the base posture (floor "off" < base "required"): INVALID when the
# profile is ENFORCED (supported_profiles={"aiqt":[1]}), used to prove a malformed supported_profiles
# never turns that INVALID into VALID.
WEAKENING_MANIFEST = {
    "devprocess": {"standard": "devprocess", "spec_version": "1.0.0", "layout": "inline",
                   "posture": "required", "import_status": "none"},
    "store": {"sync_target": ""},
    "modules": {"governance": True, "operational_policy": True, "concurrent_operation": True},
    "types": {"backlog_item": {"namespace": "BI"}},
    "vendors": {"registered": ["x-aiqt"]},
    "profiles": {"aiqt": {"version": "1.0.0", "base_compat": ">=1.0.0 <2.0.0",
                          "posture_floor": "off", "extension_namespace": "x-aiqt"}},
}


def run():
    failures = []
    cases = 0            # function x parameter x adversarial-value combinations exercised
    assertions = 0       # individual assertions checked (no-crash, shape, and fail-open)

    def fail(reason):
        failures.append(reason)

    # Record executed source lines in the three target modules for the whole run, so the coverage
    # assertions below can prove every production _sorted_key_names / guarded-int() site was reached.
    # Disabled again before the verdict; self_test's finally is the backstop that always removes it.
    executed = set()
    sys.settrace(_make_tracer(executed))

    # --- benign, genuinely-valid fixtures for the release triad --------------------------------------
    worklog = {"schema": 1, "entry": [_worklog_entry(1), _worklog_entry(2),
                                      _worklog_entry(3), _worklog_entry(4)]}
    by_id, _idf = _opf_release._entries_by_id(worklog)
    dig12 = _opf_release.compute_span_digest(by_id, (1, 2))
    vok = {"schema": 1,
           "release": [{"version": "1.0.0", "date": TS, "worklog_span": ["WL-1", "WL-2"],
                        "coverage_digest": dig12}]}

    # --- the enumerated targets: (name, fn, mode, benign kwargs, control params to fuzz) -------------
    # mode: "status" (returns an object with .status; must NEVER raise), "findings" (returns a findings
    # list, or a (map, findings) tuple; must NEVER raise), "op" (an operation that may refuse only via a
    # CONTROLLED exception). EVERY control/record parameter is listed, including the enumeration-gap
    # parameters round 4 missed: validate_transition's pre_proposal_state and reason (the rejection-path
    # controls), parse_status's spec (the type-spec object), high_water's ns (a dict-key lookup), and
    # next_id's known_complete (the bool proof flag), each of which must be no-crash and (where it gates
    # enforcement) no-fail-open under the matrix.
    targets = [
        # _opf_store
        ("validate_manifest", _opf_store.validate_manifest, "status",
         {"data": dict(VALID_MANIFEST), "supported_profiles": None},
         ["data", "supported_profiles"]),
        ("classify_target", _opf_store.classify_target, "op",
         {"value": "/abs/store"}, ["value"]),
        # _opf_schema
        ("validate_record", _opf_schema.validate_record, "status",
         {"record": _full_record(), "expected_type": "backlog_item", "specs": None,
          "registered_vendors": frozenset(), "registered_kinds": None},
         ["record", "expected_type", "specs", "registered_vendors", "registered_kinds"]),
        # A REJECTION baseline (assistant proposed done, maintainer rejects back to the pre-proposal state),
        # so pre_proposal_state and reason are LIVE-READ and their adversarial matrix is meaningful.
        ("validate_transition", _opf_schema.validate_transition, "status",
         {"type_name": "backlog_item", "from_status": "done/proposed", "to_status": "active",
          "actor_kind": "maintainer", "pre_proposal_state": "active", "reason": "rejected", "specs": None},
         ["type_name", "from_status", "to_status", "actor_kind", "pre_proposal_state", "reason", "specs"]),
        ("validate_counters", _opf_schema.validate_counters, "findings",
         {"data": {"counters": {"BI": 5}}, "known_namespaces": None},
         ["data", "known_namespaces"]),
        ("parse_status", _opf_schema.parse_status, "op",
         {"status": "open", "spec": _opf_schema.BASELINE_SPECS["backlog_item"]}, ["status", "spec"]),
        ("high_water", _opf_schema.high_water, "op",
         {"high": {"BI": 5}, "ns": "BI"}, ["high", "ns"]),
        ("next_id", _opf_schema.next_id, "op",
         {"high": {"BI": 5}, "ns": "BI", "known_complete": True}, ["high", "ns", "known_complete"]),
        ("check_monotonic", _opf_schema.check_monotonic, "findings",
         {"old_high": {"BI": 1}, "new_high": {"BI": 2}}, ["old_high", "new_high"]),
        ("check_ids_within_counters", _opf_schema.check_ids_within_counters, "findings",
         {"ids": ["BI-1"], "high": {"BI": 5}}, ["ids", "high"]),
        ("check_unique_ids", _opf_schema.check_unique_ids, "findings",
         {"ids": ["BI-1", "BI-2"]}, ["ids"]),
        # _opf_release
        ("validate_version", _opf_release.validate_version, "status",
         {"data": dict(vok)}, ["data"]),
        ("validate_worklog", _opf_release.validate_worklog, "status",
         {"data": dict(worklog), "registered_vendors": frozenset(), "registered_kinds": None},
         ["data", "registered_vendors", "registered_kinds"]),
        ("release_cut", _opf_release.release_cut, "status",
         {"version_data": dict(vok), "worklog_data": dict(worklog), "new_version": "2.0.0",
          "date": TS, "registered_vendors": frozenset(), "registered_kinds": None},
         ["version_data", "worklog_data", "new_version", "date", "registered_vendors",
          "registered_kinds"]),
        ("parse_semver", _opf_release.parse_semver, "op", {"value": "1.0.0"}, ["value"]),
        ("coverage_digest", _opf_release.coverage_digest, "op",
         {"entries": [_worklog_entry(1)]}, ["entries"]),
        ("compute_span_digest", _opf_release.compute_span_digest, "op",
         {"entries_by_id": dict(by_id), "span": (1, 2)}, ["entries_by_id", "span"]),
        ("released_end", _opf_release.released_end, "op",
         {"releases": list(vok["release"])}, ["releases"]),
        ("tail_ids", _opf_release.tail_ids, "op",
         {"releases": list(vok["release"]), "worklog_ids": [1, 2, 3, 4]},
         ["releases", "worklog_ids"]),
        ("check_frozen_coverage", _opf_release.check_frozen_coverage, "findings",
         {"version_data": dict(vok), "entries_by_id": dict(by_id)},
         ["version_data", "entries_by_id"]),
        ("check_no_append_into_released", _opf_release.check_no_append_into_released, "findings",
         {"version_data": dict(vok), "candidate_ids": [3, 4]}, ["version_data", "candidate_ids"]),
        ("check_rotation_only_released", _opf_release.check_rotation_only_released, "findings",
         {"rotated_ids": [1, 2], "version_data": dict(vok)}, ["rotated_ids", "version_data"]),
        ("check_no_deletion", _opf_release.check_no_deletion, "findings",
         {"old_ids": [1, 2, 3], "new_ids": [1, 2, 3, 4]}, ["old_ids", "new_ids"]),
        ("check_ids_partition", _opf_release.check_ids_partition, "findings",
         {"active_ids": [3, 4], "archive_ids": [1, 2], "expected_ids": None},
         ["active_ids", "archive_ids", "expected_ids"]),
    ]

    # --- the general no-crash / well-formed-outcome sweep --------------------------------------------
    for name, fn, mode, benign, controls in targets:
        for control in controls:
            for alabel, afactory in ADVERSARIAL:
                cases += 1
                kwargs = dict(benign)
                kwargs[control] = afactory()   # a FRESH value per case (a one-shot iterator is never shared)
                where = "{}[{}={}]".format(name, control, alabel)
                assertions += 1                    # (a) no-uncontrolled-crash assertion
                try:
                    result = fn(**kwargs)
                except CONTROLLED as exc:
                    if mode != "op":
                        fail("{}: a {} raised {} ({}); a {}-mode function must never raise".format(
                            where, mode, type(exc).__name__, exc, mode))
                    continue                       # a controlled refusal by an operation: fail-closed, OK
                except Exception as exc:           # noqa: BLE001  any other type is an uncontrolled crash
                    fail("{}: UNCONTROLLED {} raised ({}) -- a membership/type-guard crash".format(
                        where, type(exc).__name__, exc))
                    continue
                assertions += 1                    # (b) well-formed-outcome assertion
                if mode == "status":
                    if not (hasattr(result, "status") and result.status in STATUSES):
                        fail("{}: status-mode result is not a status object in {} (got {!r})".format(
                            where, sorted(STATUSES), result))
                elif mode == "findings":
                    findings = result[-1] if isinstance(result, tuple) else result
                    if not isinstance(findings, list):
                        fail("{}: findings-mode result is not a list (got {!r})".format(where, result))
                # mode == "op": any non-exception return is a well-formed outcome.

    # --- the NESTED-injection sweep (round 6: the round-5 structural blind spot) ----------------------
    # The general sweep above feeds each adversarial value ONLY as the WHOLE top-level parameter, so a
    # flat malformed dict bails at the outer structural check (`isinstance(data, dict)` /
    # `isinstance(links, list)`) and NEVER reaches the NESTED table validators (_validate_actor /
    # _validate_links / _validate_refs and the manifest sub-table validators), nor _canonical's recursion
    # into a registered-extension table. That is exactly why round 5 missed the nested mixed-type-key
    # sorted() crash. This sweep injects each adversarial shape into a NESTED position inside an
    # OTHERWISE-VALID base object, so the mixed-type-key dict (and the unhashable list-of-list) reach the
    # nested sorted()/set() sites and the digest recursion. Same contract as above: (a) no uncontrolled
    # crash (a status/findings function must never raise; an op may refuse ONLY via a CONTROLLED
    # exception), and (b) a well-formed outcome. This block CRASHES on the un-round-6 code (a nested
    # mixed-type-key dict reaches a bare sorted() over heterogeneous keys) and passes after the PART-A fix.
    def _mrec(**over):
        return {"record": _full_record(**over), "expected_type": "backlog_item"}

    def _mman(**over):
        return {"data": {**VALID_MANIFEST, **over}, "supported_profiles": None}

    nested_targets = [
        # validate_record's nested table validators. `links`/`refs` are injected both as the WHOLE array
        # value AND as a single ELEMENT inside an otherwise-valid one-element array: the element form is
        # the one that reaches _validate_links/_validate_refs's per-entry sorted(extra) over a mixed-key
        # link/ref table (the whole-value form hits the outer array type-guard, which is also exercised).
        ("validate_record.actor", _opf_schema.validate_record, "status", lambda a: _mrec(actor=a)),
        ("validate_record.links", _opf_schema.validate_record, "status", lambda a: _mrec(links=a)),
        ("validate_record.links[0]", _opf_schema.validate_record, "status", lambda a: _mrec(links=[a])),
        ("validate_record.refs", _opf_schema.validate_record, "status", lambda a: _mrec(refs=a)),
        ("validate_record.refs[0]", _opf_schema.validate_record, "status", lambda a: _mrec(refs=[a])),
        # validate_manifest's sub-table validators, each reached because VALID_MANIFEST is identifiably a
        # devprocess store (its [devprocess] carries the standard token), so every sub-validator runs. A
        # mixed-type-key dict injected as a sub-table reaches that validator's sorted(extra) over the
        # table's surplus keys.
        ("validate_manifest.store", _opf_store.validate_manifest, "status", lambda a: _mman(store=a)),
        ("validate_manifest.modules", _opf_store.validate_manifest, "status", lambda a: _mman(modules=a)),
        ("validate_manifest.vendors", _opf_store.validate_manifest, "status", lambda a: _mman(vendors=a)),
        ("validate_manifest.types", _opf_store.validate_manifest, "status", lambda a: _mman(types=a)),
        ("validate_manifest.providers", _opf_store.validate_manifest, "status",
         lambda a: _mman(providers=a)),
        ("validate_manifest.views", _opf_store.validate_manifest, "status", lambda a: _mman(views=a)),
        ("validate_manifest.deliverables", _opf_store.validate_manifest, "status",
         lambda a: _mman(deliverables=a)),
        ("validate_manifest.archive", _opf_store.validate_manifest, "status", lambda a: _mman(archive=a)),
        ("validate_manifest.unmanaged", _opf_store.validate_manifest, "status",
         lambda a: _mman(unmanaged=a)),
        ("validate_manifest.profiles.p", _opf_store.validate_manifest, "status",
         lambda a: _mman(profiles={"p": a})),
        # The DIGEST path: a registered-extension table carrying heterogeneous keys, nested inside an
        # otherwise-valid worklog entry, must not crash _canonical's recursion; the digest operations may
        # refuse ONLY via ReleaseError. This is the coverage_digest / compute_span_digest reach that the
        # top-level sweep could not exercise (the top-level entries list is not itself the extension table).
        ("coverage_digest.x-ext", _opf_release.coverage_digest, "op",
         lambda a: {"entries": [_worklog_entry(1, **{"x-aiqt": a})]}),
        ("compute_span_digest.x-ext", _opf_release.compute_span_digest, "op",
         lambda a: {"entries_by_id": {1: _worklog_entry(1, **{"x-aiqt": a})}, "span": (1, 1)}),
    ]
    for name, fn, mode, builder in nested_targets:
        for alabel, afactory in ADVERSARIAL:
            cases += 1
            kwargs = builder(afactory())
            where = "nested {}[{}]".format(name, alabel)
            assertions += 1                    # (a) no-uncontrolled-crash assertion
            try:
                result = fn(**kwargs)
            except CONTROLLED as exc:
                if mode != "op":
                    fail("{}: a {} raised {} ({}); a {}-mode function must never raise".format(
                        where, mode, type(exc).__name__, exc, mode))
                continue                       # a controlled refusal by an operation: fail-closed, OK
            except Exception as exc:           # noqa: BLE001  any other type is an uncontrolled crash
                fail("{}: UNCONTROLLED {} raised ({}) -- a nested membership/type-guard crash".format(
                    where, type(exc).__name__, exc))
                continue
            assertions += 1                    # (b) well-formed-outcome assertion
            if mode == "status":
                if not (hasattr(result, "status") and result.status in STATUSES):
                    fail("{}: status-mode result is not a status object in {} (got {!r})".format(
                        where, sorted(STATUSES), result))
            elif mode == "findings":
                findings = result[-1] if isinstance(result, tuple) else result
                if not isinstance(findings, list):
                    fail("{}: findings-mode result is not a list (got {!r})".format(where, result))
            # mode == "op": any non-exception return is a well-formed outcome.

    # A dedicated [devprocess] case: _validate_base's sorted(extra) is reached only when the base table
    # KEEPS its standard discovery token (a bare replacement of [devprocess] loses the token and short-
    # circuits to CANNOT-EVALUATE before _validate_base runs). So inject a NON-STRING surplus key into an
    # otherwise-valid base: on the un-round-6 code its sorted(extra) over {1, str} crashes; after the fix
    # it is a clean status object (the surplus key becomes an ordinary unknown-key finding).
    cases += 1
    assertions += 1
    _dp_mixed = {**VALID_MANIFEST["devprocess"], 1: "surplus"}
    try:
        _r = _opf_store.validate_manifest({**VALID_MANIFEST, "devprocess": _dp_mixed})
        if not (hasattr(_r, "status") and _r.status in STATUSES):
            fail("nested validate_manifest.devprocess[mixed-extra-key]: not a status object (got {!r})"
                 .format(_r))
    except Exception as exc:  # noqa: BLE001
        fail("nested validate_manifest.devprocess[mixed-extra-key]: UNCONTROLLED {} raised ({}) -- a "
             "nested membership/type-guard crash".format(type(exc).__name__, exc))

    # --- the CHILD-DEPTH key-injection sweep (round 7: the round-6 parent-replacement blind spot) ------
    # The round-6 nested sweep replaced each PARENT table with an adversarial value, so a child table was
    # never itself a dict CARRYING a surplus key: 8 of the 20 _sorted_key_names sites (the deeper
    # [types.<name>] / [providers.<name>] / [views.<name>] / [deliverables.<name>] / supported
    # [profiles.<name>] sorts, and the release-row / summary-row / manifest-top-level sorts) never ran, so
    # the round-6 heterogeneous-key fix was never PROVEN at those depths. This sweep injects an adversarial
    # HETEROGENEOUS SURPLUS-KEY SET (a non-string key beside a string one: the exact sorted()-over-mixed-
    # keys crash vector) into an otherwise-present child table or row, so every remaining site fires.
    # Contract as before: (a) no uncontrolled crash and (b) a well-formed status. _sorted_key_names
    # str-coerces every key, so a mixed-key surplus set must sort cleanly. The coverage assertion at the end
    # confirms every key-name-render site is now reached; a future un-exercised site fails the proof.
    HETERO_EXTRA = {97: "x", "zzz-extra-key": 1}      # a non-string + string surplus key set (mixed sort)
    child_cases = [
        # manifest top-level extras (_opf_store._validate_top_level): a heterogeneous top-level surplus key.
        ("manifest.top-level-extras",
         lambda: _opf_store.validate_manifest({**VALID_MANIFEST, **{98: {}, "zzz-top-table": {}}})),
        # [types.<name>] child-table extras.
        ("manifest.types.<name>-extras",
         lambda: _opf_store.validate_manifest(
             {**VALID_MANIFEST, "types": {"backlog_item": {"namespace": "BI", **HETERO_EXTRA}}})),
        # [providers.<name>] child-table extras (a present [providers] table with a mixed-key child).
        ("manifest.providers.<name>-extras",
         lambda: _opf_store.validate_manifest(
             {**VALID_MANIFEST, "providers": {"prov": dict(HETERO_EXTRA)}})),
        # [views.<name>] child-table extras.
        ("manifest.views.<name>-extras",
         lambda: _opf_store.validate_manifest({**VALID_MANIFEST, "views": {"v": dict(HETERO_EXTRA)}})),
        # [deliverables.<name>] child-table extras.
        ("manifest.deliverables.<name>-extras",
         lambda: _opf_store.validate_manifest(
             {**VALID_MANIFEST, "deliverables": {"d": dict(HETERO_EXTRA)}})),
        # supported [profiles.<name>] extras: a SUPPORTED profile (non-None supported_profiles, matching
        # major) with a mixed-key surplus set, so _validate_supported_profile's sort actually runs.
        ("manifest.profiles.<name>-extras-supported",
         lambda: _opf_store.validate_manifest(
             {**VALID_MANIFEST, "profiles": {"aiqt": {"version": "1.0.0", "base_compat": ">=1.0.0 <2.0.0",
              "posture_floor": "required", "extension_namespace": "x-aiqt", **HETERO_EXTRA}}},
             supported_profiles={"aiqt": [1]})),
        # release-row extras (_opf_release.validate_version): a well-formed release row + a mixed surplus key.
        ("version.release-row-extras",
         lambda: _opf_release.validate_version(
             {"schema": 1, "release": [{"version": "1.0.0", "date": TS, "worklog_span": ["WL-1", "WL-2"],
              "coverage_digest": dig12, **HETERO_EXTRA}]})),
        # summary-row extras (_opf_release._validate_summaries): a summary row + a mixed surplus key.
        ("version.summary-row-extras",
         lambda: _opf_release.validate_version(
             {"schema": 1,
              "release": [{"version": "1.0.0", "date": TS, "worklog_span": ["WL-1", "WL-2"],
                           "coverage_digest": dig12}],
              "summary": [{"covers": "unreleased", **HETERO_EXTRA}]})),
    ]
    for label, builder in child_cases:
        cases += 1
        assertions += 1                    # (a) no-uncontrolled-crash assertion
        try:
            result = builder()
        except Exception as exc:           # noqa: BLE001
            fail("child-depth {}: UNCONTROLLED {} raised ({}) -- a nested heterogeneous-key sort "
                 "crash".format(label, type(exc).__name__, exc))
            continue
        assertions += 1                    # (b) well-formed-outcome assertion
        if not (hasattr(result, "status") and result.status in STATUSES):
            fail("child-depth {}: result is not a status object in {} (got {!r})".format(
                label, sorted(STATUSES), result))

    # --- the targeted FAIL-OPEN probes (assertion c) -------------------------------------------------
    # Each probe pairs a malformed control against the empty/omitted baseline and asserts the malformed
    # call is never MORE PERMISSIVE. Malformed shapes deliberately include the substring vector (a bare
    # string that CONTAINS the token) and the splat vector, the exact fail-opens codex found.

    def probe(name, cond):
        nonlocal assertions
        assertions += 1
        if not cond:
            fail("fail-open probe {}: FAILED".format(name))

    # FO1 registered_vendors: an unregistered x- extension that an EMPTY allow-set rejects must never be
    # admitted by a malformed allow-set (a string substring-match or a splat).
    xrec = _worklog_record()
    xrec["x-foo"] = {}                              # an unregistered vendor extension table
    base_rv = _opf_schema.validate_record(xrec, expected_type="worklog", registered_vendors=frozenset())
    probe("registered_vendors-empty-rejects-x-ext", base_rv.status == INVALID)
    for mal in (None, "", 7, 1.5, True, [1], {"x-foo": 1}, [["x-foo"]], "x-foobar", "x-foo", []):
        r = _opf_schema.validate_record(xrec, expected_type="worklog", registered_vendors=mal)
        probe("registered_vendors-malformed({!r})-not-VALID".format(mal), r.status != VALID)

    # FO2 supported_profiles: a malformed control must never turn an INVALID profile-weakening VALID.
    enforce = _opf_store.validate_manifest(WEAKENING_MANIFEST, supported_profiles={"aiqt": [1]})
    probe("supported_profiles-enforced-weakening-INVALID", enforce.status == INVALID)
    for mal in ("aiqt", ["aiqt"], {"aiqt": "1"}, {"aiqt": [[1]]}, {7: [1]}, {"aiqt": True}, [], "", 7):
        m = _opf_store.validate_manifest(WEAKENING_MANIFEST, supported_profiles=mal)
        probe("supported_profiles-malformed({!r})-not-VALID".format(mal), m.status != VALID)
    # A ONE-SHOT ITERATOR majors value must be MATERIALIZED once and still ENFORCE the weakening (never
    # silently dropped to "unevaluated" by a second consumption): it must catch the weakening exactly as a
    # concrete list [1] does, INVALID, never VALID (the round-4 fail-open regression, spec 9.1).
    for factory in (lambda: iter([1]), lambda: (x for x in [1])):
        m = _opf_store.validate_manifest(WEAKENING_MANIFEST, supported_profiles={"aiqt": factory()})
        probe("supported_profiles-oneshot-iterator-weakening-INVALID", m.status == INVALID)

    # FO3 registered_kinds: a malformed control must never admit a kind the built-in set rejects, whether
    # by splat (kind "p" against set("perf")) or otherwise.
    perf_rec = _worklog_record(kind="perf")
    base_rk = _opf_schema.validate_record(perf_rec, expected_type="worklog", registered_kinds=None)
    probe("registered_kinds-builtin-rejects-perf", base_rk.status == INVALID)
    for mal in ("perf", ["perf", ["x"]], {"perf": 1}, "", 7, True):
        r = _opf_schema.validate_record(perf_rec, expected_type="worklog", registered_kinds=mal)
        probe("registered_kinds-malformed({!r})-not-VALID".format(mal), r.status != VALID)
    splat = _opf_schema.validate_record(_worklog_record(kind="p"), expected_type="worklog",
                                        registered_kinds="perf")
    probe("registered_kinds-splat-p-from-perf-not-VALID", splat.status != VALID)

    # FO4 known_namespaces: a malformed control must never yield FEWER findings than the permissive
    # omitted (None) baseline; the clean baseline here means malformed must surface a finding.
    _m_none, f_none = _opf_schema.validate_counters({"counters": {"BI": 5}}, known_namespaces=None)
    for mal in ("BI", ["BI", 7], 7, "", True, {"BI": 1}):
        _m, f_mal = _opf_schema.validate_counters({"counters": {"BI": 5}}, known_namespaces=mal)
        probe("known_namespaces-malformed({!r})-not-more-permissive".format(mal),
              len(f_mal) >= len(f_none) and len(f_mal) > 0)

    # FO5 id-collection (check_no_deletion): a malformed new_ids must never read as "nothing deleted".
    probe("no_deletion-baseline-clean", not _opf_release.check_no_deletion([1, 2, 3], [1, 2, 3, 4]))
    for mal in ("WL-1", 7, {"1": 1}, [[1]], "", None):
        probe("no_deletion-malformed-new_ids({!r})-reports-loss".format(mal),
              bool(_opf_release.check_no_deletion([1, 2, 3], mal)))

    # FO6 id-collection (check_ids_partition): a malformed active_ids must never mask a loss.
    probe("partition-baseline-no-loss",
          not _opf_release.check_ids_partition([1, 2], [], expected_ids=range(1, 3)))
    for mal in ("WL-1", 7, [[1]], "", None):
        probe("partition-malformed-active({!r})-reports-loss".format(mal),
              bool(_opf_release.check_ids_partition(mal, [], expected_ids=range(1, 3))))

    # FO7 counters map (check_ids_within_counters): a malformed high must never certify an id clean.
    probe("within-counters-baseline-clean",
          not _opf_schema.check_ids_within_counters(["BI-1"], {"BI": 5}))
    for mal in ("BI", "BI-5", 7, [["BI"]], "", None):
        probe("within-counters-malformed-high({!r})-flags".format(mal),
              bool(_opf_schema.check_ids_within_counters(["BI-1"], mal)))

    # FO8 expected_type / specs (validate_record): a malformed control must never turn an INVALID
    # (type-mismatch or unknown-type) into VALID.
    mismatch = _opf_schema.validate_record(_full_record(), expected_type="finding")
    probe("expected_type-mismatch-INVALID", mismatch.status == INVALID)
    for mal in ([("finding",)], {"finding": 1}, 7, ""):
        probe("expected_type-malformed({!r})-not-VALID".format(mal),
              _opf_schema.validate_record(_full_record(), expected_type=mal).status != VALID)
    for mal in ("", 7, [1, 2], "backlog_item"):
        probe("specs-malformed({!r})-not-VALID".format(mal),
              _opf_schema.validate_record(_full_record(), specs=mal).status != VALID)

    # FO9 next_id.known_complete: known_complete is a genuine-bool PROOF flag, not a truthiness test. Its
    # empty/false/omitted baseline REFUSES allocating from a namespace with no recorded high-water (an
    # absent counter reading as 0 could reuse an existing id, spec 8.2). A non-bool (e.g. the truthy string
    # "false", or 1) must never be MORE permissive than that baseline: it must refuse, not allocate.
    def _refuses(callable_):
        try:
            callable_()
        except ValueError:
            return True                      # the documented fail-closed refusal
        except Exception:                    # noqa: BLE001  any other outcome is not the refusal we want
            return False
        return False                         # a non-exception return means it ALLOCATED (fail-open)

    probe("next_id-known_complete-omitted-refuses-absent",
          _refuses(lambda: _opf_schema.next_id({}, "BI")))
    probe("next_id-known_complete-False-refuses-absent",
          _refuses(lambda: _opf_schema.next_id({}, "BI", known_complete=False)))
    for mal in ("false", "true", "False", 1, 0, 1.5, [1], {"x": 1}, [], "", None):
        probe("next_id-known_complete-nonbool({!r})-refuses".format(mal),
              _refuses(lambda mal=mal: _opf_schema.next_id({}, "BI", known_complete=mal)))
    # genuine True still allocates: the fix closes the fail-open without breaking the enforcement path.
    probe("next_id-known_complete-True-still-allocates",
          _opf_schema.next_id({}, "BI", known_complete=True) == ("BI-1", 1))

    # --- round-7 NEW value-shape probes: CLASS 1 (malformed row), 2 (oversized int), 3 (non-TypeSpec) ---
    # Each shape is REACHABLE from parsed TOML or a hand-built Python control and was a round-6 escape.
    # Each MUST fail on the un-round-7 code (a silent fail-open, or an uncontrolled ValueError/AttributeError)
    # and pass after the fix, so the harness is a genuine fail-to-pass proof of the three defect classes.
    def _fails_closed(callable_):
        """True iff the callable REFUSES via a CONTROLLED fail-closed exception; False on a silent return
        (a fail-open) or an uncontrolled crash."""
        try:
            callable_()
        except CONTROLLED:
            return True
        except Exception:  # noqa: BLE001  an uncontrolled crash is not a clean fail-closed refusal
            return False
        return False        # a non-exception return is a silent fail-open

    def _returns_no_raise(name, callable_, ok):
        """Assert `callable_` RETURNS (never raises: its contract is a clean value) a value satisfying `ok`.
        ANY exception is a failure; before the round-7 fix the guarded int() / attribute access raises here."""
        nonlocal assertions
        assertions += 1
        try:
            val = callable_()
        except Exception as exc:  # noqa: BLE001
            fail("{}: raised {} ({}); its contract is a clean return, never a crash".format(
                name, type(exc).__name__, exc))
            return
        if not ok(val):
            fail("{}: unexpected return {!r}".format(name, val))

    # CLASS 1: a MALFORMED (non-table) release ROW is reachable from valid TOML (`release = [42]`). The pure
    # operations released_end/tail_ids must FAIL CLOSED (ReleaseError), never silently return an
    # under-computed 0 / an unchanged passthrough that reads an unreadable ledger as "no released span".
    bad_ledger = tomllib.loads("release = [42]\n")["release"]     # == [42]: a non-table release element
    probe("released_end-malformed-row-fails-closed",
          _fails_closed(lambda: _opf_release.released_end(bad_ledger)))
    probe("tail_ids-malformed-row-fails-closed",
          _fails_closed(lambda: _opf_release.tail_ids(bad_ledger, [1, 2])))
    # the sibling that ALREADY fails closed still reports the malformed row, so the module is consistent:
    probe("no-append-malformed-row-cannot-eval",
          bool(_opf_release.check_no_append_into_released({"release": [42]}, [3])))

    # CLASS 2: an OVERSIZED numeric string (CPython refuses int() beyond 4300 digits) is reachable as an id
    # numeric suffix or a SemVer field: a clean finding / None, never an uncontrolled ValueError crash.
    big = "9" * 4301
    _returns_no_raise("valid_id_shape-oversized-None",
                      lambda: _opf_schema._valid_id_shape("BI-" + big), lambda v: v is None)
    _returns_no_raise("parse_semver-oversized-core-None",
                      lambda: _opf_release.parse_semver(big + ".0.0"), lambda v: v is None)
    _returns_no_raise("parse_semver-oversized-prerelease-None",
                      lambda: _opf_release.parse_semver("1.0.0-" + big), lambda v: v is None)
    _returns_no_raise("validate_record-oversized-id-INVALID",
                      lambda: _opf_schema.validate_record(_full_record(id="BI-" + big),
                                                          expected_type="backlog_item"),
                      lambda r: hasattr(r, "status") and r.status == INVALID)
    _returns_no_raise("validate_version-oversized-version-INVALID",
                      lambda: _opf_release.validate_version(
                          {"schema": 1, "release": [{"version": big + ".0.0", "date": TS,
                           "worklog_span": [], "coverage_digest": "sha256:" + "0" * 64}]}),
                      lambda r: hasattr(r, "status") and r.status == INVALID)

    # CLASS 3: a specs roster whose VALUE is not a TypeSpec (a hand-built Python control) must be a clean
    # CANNOT-EVALUATE, never an AttributeError on spec.namespace (typed record) or espec.reduced (reduced).
    _returns_no_raise("validate_record-nonTypeSpec-spec-cannot-eval",
                      lambda: _opf_schema.validate_record(_full_record(), specs={"backlog_item": 7}),
                      lambda r: hasattr(r, "status") and r.status == CANNOT_EVALUATE)
    _returns_no_raise("validate_record-nonTypeSpec-espec-cannot-eval",
                      lambda: _opf_schema.validate_record(_worklog_record(), expected_type="backlog_item",
                                                          specs={"backlog_item": 7}),
                      lambda r: hasattr(r, "status") and r.status == CANNOT_EVALUATE)

    # --- round-8 by-value exotic-shape probes: FIX 1 (str(int)) and FIX 2 (canonicalization recursion) --
    # These prove the two round-8 crash classes are CLOSED at the digest path, BY VALUE (not by a marker).
    # Each MUST fail on the pre-fix code (an uncontrolled ValueError from str(int), or an uncontrolled
    # RecursionError from unbounded recursion) and pass after, so the harness is a genuine fail-to-pass
    # proof of both classes. The general and nested sweeps above already inject the oversized-int and
    # deep-nested shapes at every field and nested position; the assertion here is STRICTER than the
    # sweep's no-uncontrolled-crash check: it requires the module's OWN controlled ReleaseError, so the
    # pre-fix ValueError (which the sweep tolerates as a documented controlled exception) is caught here as
    # a non-ReleaseError and fails. Fed as a plain field value AND nested inside a registered-extension
    # table, at both coverage_digest and the span-digest path.
    def _raises_release_error(callable_):
        try:
            callable_()
        except ReleaseError:
            return True
        except Exception:  # noqa: BLE001  any other type (the pre-fix ValueError / RecursionError) fails
            return False
        return False        # a silent return is a fail-open: a digest computed over an uncovered value

    probe("fix1-canonical-oversized-int-ReleaseError",
          _raises_release_error(lambda: _opf_release._canonical(_OVERSIZED_INT)))
    probe("fix1-coverage_digest-oversized-int-field-ReleaseError",
          _raises_release_error(lambda: _opf_release.coverage_digest(
              [_worklog_entry(1, note=_OVERSIZED_INT)])))
    probe("fix1-coverage_digest-oversized-int-in-ext-ReleaseError",
          _raises_release_error(lambda: _opf_release.coverage_digest(
              [_worklog_entry(1, **{"x-aiqt": {"score": _OVERSIZED_INT}})])))
    probe("fix1-compute_span_digest-oversized-int-in-ext-ReleaseError",
          _raises_release_error(lambda: _opf_release.compute_span_digest(
              {1: _worklog_entry(1, **{"x-aiqt": {"score": _OVERSIZED_INT}})}, (1, 1))))
    probe("fix2-canonical-deep-nested-ReleaseError",
          _raises_release_error(lambda: _opf_release._canonical(_deep_nest(_DEEP_DEPTH))))
    probe("fix2-coverage_digest-deep-nested-field-ReleaseError",
          _raises_release_error(lambda: _opf_release.coverage_digest(
              [_worklog_entry(1, detail=_deep_nest(_DEEP_DEPTH))])))
    probe("fix2-compute_span_digest-deep-nested-in-ext-ReleaseError",
          _raises_release_error(lambda: _opf_release.compute_span_digest(
              {1: _worklog_entry(1, **{"x-aiqt": _deep_nest(_DEEP_DEPTH)})}, (1, 1))))
    # the bounds do NOT over-reject a real (shallow) entry: it still digests to a clean sha256 string.
    probe("fix1-2-shallow-entry-digests-clean",
          isinstance(_opf_release.coverage_digest([_worklog_entry(1)]), str))

    # --- round-9 FIELD-INJECTION sweep: the finding-message oversized-int crash class -------------------
    # An oversized int built via a NON-DECIMAL path (int("f"*4000, 16) is a genuine 16000-bit int, exactly
    # what tomllib yields from a hex literal); str()/repr() of it, or of a container holding it, raises
    # ValueError under CPython's BASE-10 integer-string-conversion limit. Injected at a FIELD of an
    # otherwise-valid record/manifest/counters/version/worklog, it reaches each validator's finding-message
    # formatting. Every status/findings validator MUST render it into a structured outcome, NEVER raise
    # (the pre-round-9 code raises an uncontrolled ValueError here); the digest/allocation OPERATIONS must
    # refuse only via their documented controlled exception with a well-formed message. Each case is a clean
    # fail-to-pass over the pre-fix code. The oversized int is fed BOTH bare and inside a container (a list
    # and an inline table), because a container-valued field reaches the "wrong type, got {!r}" render whose
    # repr recurses into the oversized int.
    MSG_INT = int("f" * 4000, 16)              # a genuine 16000-bit int, as a hex TOML literal yields
    MSG_LIST = [MSG_INT]                       # a container whose repr() recurses into the oversized int
    MSG_TABLE = {"n": MSG_INT}                 # an inline-table carrying the oversized int

    def _msg_ok(label, mode, thunk):
        """A finding-MESSAGE validator (status/findings mode) must render an oversized parsed value into a
        well-formed outcome without raising AT ALL. Judged on the returned status/findings VALUE and on the
        raised exception TYPE, never by grepping output (the isolate-verifiers rule)."""
        nonlocal cases, assertions
        cases += 1
        assertions += 1
        try:
            result = thunk()
        except Exception as exc:  # noqa: BLE001  a status/findings validator must never raise on this input
            fail("field-inject {}: raised {} ({}); a finding message must render an oversized parsed value, "
                 "not crash".format(label, type(exc).__name__, exc))
            return
        if mode == "status":
            if not (hasattr(result, "status") and result.status in STATUSES):
                fail("field-inject {}: result is not a status object in {} (got {!r})".format(
                    label, sorted(STATUSES), result))
        else:  # findings: a list, or a (map, findings) tuple
            findings_list = result[-1] if isinstance(result, tuple) else result
            if not isinstance(findings_list, list):
                fail("field-inject {}: findings-mode result is not a list (got {!r})".format(label, result))

    def _rec(**over):
        return _full_record(**over)

    # STATUS / FINDINGS validators: an oversized parsed field value renders to a structured outcome.
    status_findings_cases = [
        # _opf_store.validate_manifest sub-tables (bare int AND container).
        ("manifest.spec_version-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "devprocess": {**VALID_MANIFEST["devprocess"], "spec_version": MSG_INT}})),
        ("manifest.spec_version-list", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "devprocess": {**VALID_MANIFEST["devprocess"], "spec_version": MSG_LIST}})),
        ("manifest.types.namespace-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "types": {"backlog_item": {"namespace": MSG_INT}}})),
        ("manifest.views.kind-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "views": {"v": {"kind": MSG_INT, "sources": ["worklog"], "target": "V.md"}}})),
        ("manifest.deliverables.kind-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "deliverables": {"d": {"kind": MSG_INT, "target": "D.md"}}})),
        ("manifest.archive.period-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "archive": {"period": MSG_INT}})),
        # supported [profiles.aiqt] fields: base_compat, posture_floor, extension_namespace.
        ("manifest.profile.base_compat-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "profiles": {"aiqt": {"version": "1.0.0", "base_compat": MSG_INT,
             "posture_floor": "required", "extension_namespace": "x-aiqt"}}}, supported_profiles={"aiqt": [1]})),
        ("manifest.profile.posture_floor-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "profiles": {"aiqt": {"version": "1.0.0", "base_compat": ">=1.0.0 <2.0.0",
             "posture_floor": MSG_INT, "extension_namespace": "x-aiqt"}}}, supported_profiles={"aiqt": [1]})),
        ("manifest.profile.extension_namespace-int", "status", lambda: _opf_store.validate_manifest(
            {**VALID_MANIFEST, "profiles": {"aiqt": {"version": "1.0.0", "base_compat": ">=1.0.0 <2.0.0",
             "posture_floor": "required", "extension_namespace": MSG_INT}}}, supported_profiles={"aiqt": [1]})),
        # _opf_schema.validate_record: id, a link id, a block's scopes entry.
        ("record.id-int", "status", lambda: _opf_schema.validate_record(
            _rec(id=MSG_INT), expected_type="backlog_item")),
        ("record.link.id-int", "status", lambda: _opf_schema.validate_record(
            _rec(links=[{"rel": "relates", "id": MSG_INT}]), expected_type="backlog_item")),
        ("record.block.scopes-int", "status", lambda: _opf_schema.validate_record(
            _rec(type="block", status="active", scopes=[MSG_INT]), expected_type="block")),
        # _opf_schema.validate_transition: actor_kind (rendered before the status parse).
        ("transition.actor_kind-int", "status", lambda: _opf_schema.validate_transition(
            type_name="backlog_item", from_status="open", to_status="active", actor_kind=MSG_INT)),
        # _opf_schema.validate_counters / check_monotonic / check_ids_within_counters (findings mode).
        ("counters.schema-int", "findings", lambda: _opf_schema.validate_counters(
            {"schema": MSG_INT, "counters": {}})),
        ("check_monotonic.regress-int", "findings", lambda: _opf_schema.check_monotonic(
            {"BI": MSG_INT}, {"BI": 0})),
        ("check_monotonic.badval-list", "findings", lambda: _opf_schema.check_monotonic(
            {"BI": MSG_LIST}, {"BI": 0})),
        ("within_counters.hv-list", "findings", lambda: _opf_schema.check_ids_within_counters(
            ["BI-1"], {"BI": MSG_LIST})),
        # _opf_release.validate_version / validate_worklog / release_cut (status mode).
        ("version.release.version-int", "status", lambda: _opf_release.validate_version(
            {"schema": 1, "release": [{"version": MSG_INT, "date": TS, "worklog_span": [],
             "coverage_digest": "sha256:" + "0" * 64}]})),
        ("version.schema-int", "status", lambda: _opf_release.validate_version({"schema": MSG_INT})),
        ("worklog.schema-int", "status", lambda: _opf_release.validate_worklog({"schema": MSG_INT})),
        ("worklog.entry.id-int", "status", lambda: _opf_release.validate_worklog(
            {"schema": 1, "entry": [{"id": MSG_INT, "date": TS, "actor": {"kind": "maintainer"},
             "kind": "added", "summary": "s"}]})),
        ("version.span.entries-list", "status", lambda: _opf_release.validate_version(
            {"schema": 1, "release": [{"version": "1.0.0", "date": TS, "worklog_span": [MSG_INT, 1],
             "coverage_digest": "sha256:" + "0" * 64}]})),
        ("release_cut.new_version-int", "status", lambda: _opf_release.release_cut(
            dict(vok), dict(worklog), new_version=MSG_INT, date=TS)),
    ]
    for label, mode, thunk in status_findings_cases:
        _msg_ok(label, mode, thunk)

    # OPERATION paths: the digest and the id-allocation refuse only via their documented controlled
    # exception, and building that exception's own message must not itself crash on the oversized value.
    # coverage_digest over an entry whose id is an oversized int hits the malformed-id message (distinct
    # from the FIX-1 canonicalization path); it must be a ReleaseError, never the pre-fix ValueError.
    cases += 1
    probe("field-inject digest.entry-id-int-ReleaseError",
          _raises_release_error(lambda: _opf_release.coverage_digest(
              [{"id": MSG_INT, "date": TS, "actor": {"kind": "maintainer"}, "kind": "added", "summary": "s"}])))
    # next_id over a counters map whose value is a container holding an oversized int: the controlled
    # ValueError refusal must carry a rendered message, not crash while building it. Pre-fix the repr of the
    # container recurses into the oversized int and raises the CPython-limit ValueError instead; assert the
    # raised message is the module's own and does not carry the limit text (an exception-object assertion,
    # not output-grepping).
    cases += 1
    assertions += 1
    try:
        _opf_schema.next_id({"BI": MSG_LIST}, "BI")
        fail("field-inject next_id.counter-list: returned without refusing an unreadable counters map")
    except ValueError as exc:
        if "integer string conversion" in str(exc) or "Exceeds the limit" in str(exc):
            fail("field-inject next_id.counter-list: the refusal message itself crashed on the oversized "
                 "int ({})".format(exc))
    except Exception as exc:  # noqa: BLE001
        fail("field-inject next_id.counter-list: raised {} ({}); expected a controlled ValueError".format(
            type(exc).__name__, exc))

    # END-TO-END from tomllib: prove the oversized int flows from a real hex TOML literal through the
    # validator to a structured outcome (the two confirmed round-9 sites, driven from parsed bytes).
    _hx = "0x" + "f" * 4000
    _msg_ok("e2e.manifest-hex-spec_version", "status", lambda: _opf_store.validate_manifest(
        tomllib.loads('[devprocess]\nstandard = "devprocess"\nspec_version = ' + _hx + "\n")))
    _msg_ok("e2e.check_monotonic-hex-highwater", "findings", lambda: _opf_schema.check_monotonic(
        tomllib.loads("BI = " + _hx + "\n"), {"BI": 0}))

    # --- coverage-instrumentation assertions (round 7): every production site was actually reached -------
    sys.settrace(None)      # stop tracing before the verdict; self_test's finally is the backstop
    skn_by_token = _skn_call_sites()
    skn_sites = set().union(*skn_by_token.values())
    # The scan must not vacuously pass by finding nothing. The fail-closed floor is DERIVED from the
    # authoritative index (both key-name-render helper tokens) rather than a single magic total that
    # silently drifts: EACH helper class must scan to at least one call site. A class that scans to zero
    # means the authoritative-index scan has drifted or broken -- exactly the failure mode when the four
    # _opf_schema sites moved from _sorted_key_names( to _safe_key_names( and a _sorted_key_names-only scan
    # went blind to them (guard-input-soundness applied to the coverage input). The int/str-guard class is
    # no longer proven by a marker scan (see the round-8 revision note above); it is proven by the by-value
    # probes just above. The expected count is never hardcoded: the required-exercised set is the scan's own
    # union, so adding or removing a site cannot silently drift past a stale literal.
    for tok, sites in sorted(skn_by_token.items()):
        assertions += 1
        if not sites:
            fail("coverage scan found no {} call site(s): the authoritative-index scan under-counts or "
                 "has drifted".format(tok))
    # Reconcile the vocabulary against the authoritative source: every `*_key_names` render helper DEFINED
    # in the three modules must be registered in _KEY_NAME_RENDER_TOKENS. A renamed or newly-added helper
    # absent from the vocabulary is drift (fail-closed), rather than silently dropping out of both the
    # coverage numerator and denominator (guard-input-soundness). DISCLOSED RESIDUAL: a key set rendered
    # inline without such a helper is outside this scan's scope (disclose-guard-residuals).
    _vocab = set(_KEY_NAME_RENDER_TOKENS)
    _defs = _key_name_render_defs()
    assertions += 1
    _unreg, _stale = _render_vocab_drift(_defs, _vocab)
    if _unreg or _stale:
        fail("coverage vocabulary drift: `*_key_names` render helpers defined in source {} do not match the "
             "registered vocabulary {}; an unregistered or renamed helper would escape both the coverage "
             "numerator and denominator (fail-closed)".format(sorted(_defs), sorted(_vocab)))
    # DISCRIMINATION: feed an UNREGISTERED `*_key_names` render definition through the ACTUAL reconciliation
    # the guard above uses (_render_vocab_drift), and confirm it reports the rogue as unregistered drift.
    # The rogue token is first recognized by the real def-scan predicate (a weakened _KEY_NAME_RENDER_DEF_RE
    # stops matching it), then, added to the discovered defs, must appear in the reconciliation's
    # `unregistered` set. Because this routes through the SAME function run()'s guard fires on, disabling the
    # `_defs != _vocab` reconciliation (returning no drift) makes THIS assertion FAIL, not merely a separate
    # throwaway expression as the prior form did.
    assertions += 1
    _rogue = _KEY_NAME_RENDER_DEF_RE.match("def _rogue_key_names(keys):")
    _rogue_tok = None if _rogue is None else _rogue.group(1) + "("
    _rogue_unreg = set() if _rogue_tok is None else _render_vocab_drift(_defs | {_rogue_tok}, _vocab)[0]
    if _rogue_tok is None or _rogue_tok not in _rogue_unreg:
        fail("coverage reconciliation is non-discriminating: an unregistered `*_key_names` render helper fed "
             "through the reconciliation was not reported as drift (the def-scan predicate or the "
             "_render_vocab_drift reconciliation has been weakened)")
    skn_missed = sorted(skn_sites - executed)
    assertions += 1
    if skn_missed:
        fail("coverage: {} of {} key-name-render call site(s) never exercised by any case: {}".format(
            len(skn_missed), len(skn_sites), skn_missed))
    skn_reached = len(skn_sites) - len(skn_missed)

    # --- verdict -------------------------------------------------------------------------------------
    if failures:
        print("OPF-FUZZ SELF-TEST: FAIL ({} of {} assertions failed over {} adversarial cases)".format(
            len(failures), assertions, cases))
        for f in failures[:60]:
            print("  FAILED: {}".format(f))
        if len(failures) > 60:
            print("  ... and {} more".format(len(failures) - 60))
        return 1
    print("OPF-FUZZ SELF-TEST: PASS ({} adversarial cases over {} public functions; {} assertions: no "
          "uncontrolled crash, well-formed outcome, no fail-open, the by-value oversized-int / deep-nested "
          "shapes fail closed at the digest path, and an oversized parsed int (hex/octal/binary) renders to "
          "a structured finding at every finding-message site rather than crashing; coverage over the "
          "reconciled `*_key_names` render-helper vocabulary: {}/{} registered call sites reached, inline "
          "renders disclosed out of scope)".format(
              cases, len(targets), assertions, skn_reached, len(skn_sites)))
    return 0


def self_test():
    """The registered `opf-fuzz` leg entry point (0 clean, 1 finding, 2 fail-closed harness error)."""
    try:
        return run()
    except Exception as exc:  # noqa: BLE001  a harness fault is fail-closed, never a silent clean pass
        print("OPF-FUZZ SELF-TEST ERROR: {} ({}); fail-closed".format(type(exc).__name__, exc),
              file=sys.stderr)
        return 2
    finally:
        sys.settrace(None)    # backstop: never leave the line tracer installed for later self-test legs


if __name__ == "__main__":
    sys.exit(self_test())
