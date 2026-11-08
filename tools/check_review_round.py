#!/usr/bin/env python3
"""Review-round merge gate (review-round): a review round counts toward a merge only when every
required reviewer leg of it is a clean, counted delivery.

  check_review_round.py --manifest FILE --head SHA [--attempts FILE] [--decisions FILE]
                        [--table PATH]
  check_review_round.py --self-test [--execution-report ABS_PATH]

DRAFT STAGE. This file carries the round-manifest parser and validator only. Leg evaluation (each
listed delivery judged by check_verifier_delivery's own evaluate()), the attempts ledger and the
maintainer decision records are later steps; until they land, a round whose manifest validates is
CANNOT_EVALUATE (exit 2), never a pass, and --attempts or --decisions is refused the same way, never
silently ignored. Exit 0 and exit 1 below describe the finished gate and are not reachable at this
stage. A --repo DIR option was accepted here and never read, so the draft stage drops it rather than
carry a dead option; the leg-evaluation step, whose evaluator reads the reviewed repository,
reintroduces it.

Exit statuses:
  0  every input validates and every required leg is satisfied: at least one of its attempts is PASS
     with the delivery verdict NO BLOCKERS, none of its attempts is an open blocker, and every
     attempt reviewed the manifest's revision.
  1  inputs validate and at least one required leg is unsatisfied.
  2  cannot evaluate, and it wins over exit 1: a usage error; a malformed, oversized or unreadable
     manifest, capability table, brief, ledger or decision record; an unknown or repeated family; a
     panel below the diversity floor (two families, three when the manifest declares the change
     critical); legs that do not carry exactly one leg per required family; a repeated leg id; a
     brief whose bytes do not hash to the manifest's brief-sha256; --head not equal to the
     manifest's revision.

The round manifest (TOML) declares, with exactly these keys: format-version (the integer 1; a bool
or float is refused), round and leg ids ([a-z0-9][a-z0-9._-]{0,63}), revision (the reviewed commit,
40 lower-hex, so only SHA-1 repositories are gated, the same bound as check_verifier_delivery's
SHA_RE; a 64-hex SHA-256 object id is refused, never evaluated; it must equal --head), repository
(an owner/name id,
[A-Za-z0-9][A-Za-z0-9._-]{0,99} on each side of one '/', so a control character is refused; reported,
compared with nothing), [policy] (rule =
"vrfdiv", the only panel rule in v1, so a recorded single-family fallback is refused; critical, a
bool that raises the family floor from 2 to 3; required, the family names), and one [[leg]] per
required family (id, family, brief, brief-sha256, delivery; each path absolute with no '..'
component and no NUL byte, refused before any open because os.open raises ValueError, not the
OSError the shared reader handles, on a NUL). The required set comes from [policy], never from the
legs that happen to be present, so
a missing family stays visible. Family names are the name rows of the capability table, read
through check_brief_capability.load_table (that table's one reader, never a second copy); the
--table seam exists for the self-test's neutral fixture families. The manifest's sha256 is computed
here and reported: every later ledger row and decision record must repeat it, but when the manifest
was written cannot be established (manifest order: not established).

DISCLOSED RESIDUAL (the residue of the finished gate; the draft stage above narrows it further):
It reads one review round: a manifest of required verifier legs, an optional attempts ledger and
optional maintainer decision records, all written by the operator or maintainer, and evaluates
every listed delivery with check_verifier_delivery's own evaluator. A leg counts only when one of
its deliveries is PASS with the verdict NO BLOCKERS; exit 0 only when every required leg counts,
exit 1 when any does not, exit 2 when it cannot evaluate, and exit 2 wins over 1. What it does not
catch:
  * It cannot establish when the manifest or ledger was written, or that rounds, attempts or
    deliveries left out of them did not exist. A re-dispatch the ledger omits, or a second manifest
    for the same revision, is invisible to it.
  * It cannot authenticate a decision record. The maintainer field is a claim, and a record covers
    a whole delivery, not each finding in it.
  * It cannot tell whether a clean verdict is correct. A clean verdict over a listed finding
    passes, and a quote proves only that the text exists in the reviewed tree.
  * It cannot read the findings of a degraded earlier attempt.
  * Family identity rests on the harness status line in the delivery text, which a worker whose
    reply becomes the delivery could forge.
  * It cannot know whether a change is critical. The manifest declares that.
  * It refuses a recorded single-family fallback panel that the diversity rule permits (an
    over-refusal), and any verdict phrased other than NO BLOCKERS.
  * It binds the reviewed revision to the head it is given, not to what is merged: it blocks a
    merge only where the merge step runs it, stops on any non-zero exit and merges exactly that
    head.
  * The repository holds no round, so CI runs only its self-test.
ENFORCEABILITY (class c, partial).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_review_round.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_brief_capability  # noqa: E402  (load_table, the family table's one reader; the shared refusal)
import check_verifier_delivery  # noqa: E402  (HARNESS_FIRST_RE; evaluate() from the leg-evaluation step on)

# The shared refusal type and the one bounded regular-file reader (non-blocking open, regular-file-only,
# read in chunks, closed exactly once), reused so this gate adds no descriptor site of its own; that
# reader's size bound is this gate's manifest and brief bound.
CannotEvaluate = check_brief_capability.CannotEvaluate
_read_regular = check_brief_capability._read_regular

ROOT = Path(__file__).resolve().parents[1]
TABLE_REL = check_brief_capability.TABLE_REL
TOOL = "check_review_round"
# The diversity floor: two required families, three when the manifest declares the change critical.
FLOOR = 2
CRITICAL_FLOOR = 3
# The one panel rule of v1; any other panel, a recorded single-family fallback included, is refused.
PANEL_RULE = "vrfdiv"
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
SHA40_RE = re.compile(r"[0-9a-f]{40}\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
# The repository id: owner/name, each side 1 to 100 characters from a closed ASCII set, so a control
# character, an escape, a path step or any other shape is refused by the pattern itself.
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
MANIFEST_KEYS = ("format-version", "leg", "policy", "repository", "revision", "round")
POLICY_KEYS = ("critical", "required", "rule")
LEG_KEYS = ("brief", "brief-sha256", "delivery", "family", "id")
# A digest proves nothing about when the manifest was written or what was left out of it.
ORDER_NOTE = "manifest order: not established"

USAGE = ("usage: check_review_round.py --manifest FILE --head SHA [--attempts FILE] "
         "[--decisions FILE] [--table PATH]\n"
         "       check_review_round.py --self-test [--execution-report ABS_PATH]")


def _checked_no_nul(path, what):
    """The path, or CannotEvaluate when it carries a NUL byte: os.open raises ValueError, not the
    OSError the shared reader handles, on such a path, so the refusal must come before any open."""
    if isinstance(path, str) and "\x00" in path:
        raise CannotEvaluate("{}: the path contains a NUL byte".format(what))
    return path


def _load_family_table(table_path):
    """The family table (name -> capability row) through check_brief_capability.load_table."""
    return check_brief_capability.load_table(_checked_no_nul(table_path, "capability table"))


def parse_manifest(data):
    """The round manifest's TOML document, or CannotEvaluate on anything the parse cannot read (every
    parse failure class is handled here, in the parsing function itself)."""
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("round manifest: not valid UTF-8 TOML: {}".format(exc))


def _checked_id(value, what):
    if not isinstance(value, str) or not ID_RE.match(value):
        raise CannotEvaluate("round manifest: {} must be an id of 1 to 64 characters, "
                             "[a-z0-9][a-z0-9._-]*".format(what))
    return value


def validate_manifest(doc, table):
    """The validated round as a dict, or CannotEvaluate naming the first rule the manifest breaks."""
    if not isinstance(doc, dict):
        raise CannotEvaluate("round manifest: the document must be a TOML table")
    version = doc.get("format-version")
    # the exact integer 1: a bool (true == 1) or a float (1.0 == 1) is refused
    if type(version) is not int or version != 1:
        raise CannotEvaluate("round manifest: format-version must be exactly the integer 1 (a bool or "
                             "float is refused)")
    if sorted(doc) != sorted(MANIFEST_KEYS):
        raise CannotEvaluate("round manifest: the top level must have exactly the keys {}".format(
            ", ".join(sorted(MANIFEST_KEYS))))
    round_id = _checked_id(doc["round"], "round")
    revision = doc["revision"]
    if not isinstance(revision, str) or not SHA40_RE.match(revision):
        raise CannotEvaluate("round manifest: revision must be a full 40-hex lower-case commit SHA")
    repository = doc["repository"]
    if not isinstance(repository, str) or not REPO_RE.match(repository):
        raise CannotEvaluate("round manifest: repository must be an owner/name id, "
                             "[A-Za-z0-9][A-Za-z0-9._-]{0,99} on each side of one '/' (a control "
                             "character or any other string is refused)")
    policy = doc["policy"]
    if not isinstance(policy, dict) or sorted(policy) != sorted(POLICY_KEYS):
        raise CannotEvaluate("round manifest: [policy] must have exactly the keys {}".format(
            ", ".join(sorted(POLICY_KEYS))))
    if policy["rule"] != PANEL_RULE:
        raise CannotEvaluate("round manifest: policy.rule must be {!r}; any other panel, a recorded "
                             "single-family fallback included, is refused in v1".format(PANEL_RULE))
    critical = policy["critical"]
    if type(critical) is not bool:
        raise CannotEvaluate("round manifest: policy.critical must be a bool")
    required = policy["required"]
    if not isinstance(required, list) or not required \
            or not all(isinstance(name, str) for name in required):
        raise CannotEvaluate("round manifest: policy.required must be a non-empty list of family names")
    for name in required:
        if name not in table:
            raise CannotEvaluate("round manifest: policy.required names {!r}, which is not a family of "
                                 "the capability table".format(name))
    if len(set(required)) != len(required):
        raise CannotEvaluate("round manifest: policy.required repeats a family")
    floor = CRITICAL_FLOOR if critical else FLOOR
    if len(required) < floor:
        raise CannotEvaluate("round manifest: the panel is below the diversity floor: {} required "
                             "families where the floor is {} ({})".format(
                                 len(required), floor, "critical" if critical else "not critical"))
    legs = doc["leg"]
    if not isinstance(legs, list) or not legs or not all(isinstance(leg, dict) for leg in legs):
        raise CannotEvaluate("round manifest: [[leg]] rows are required")
    out_legs, leg_ids, leg_families = [], set(), []
    for index, leg in enumerate(legs, 1):
        if sorted(leg) != sorted(LEG_KEYS):
            raise CannotEvaluate("round manifest: leg row {} must have exactly the keys {}".format(
                index, ", ".join(sorted(LEG_KEYS))))
        leg_id = _checked_id(leg["id"], "leg row {} id".format(index))
        if leg_id in leg_ids:
            raise CannotEvaluate("round manifest: leg id {!r} is repeated".format(leg_id))
        leg_ids.add(leg_id)
        family = leg["family"]
        if not isinstance(family, str):
            raise CannotEvaluate("round manifest: leg {!r}: family must be a string".format(leg_id))
        leg_families.append(family)
        for key in ("brief", "delivery"):
            path = leg[key]
            if not isinstance(path, str) or "\x00" in path or not os.path.isabs(path) \
                    or ".." in Path(path).parts:
                raise CannotEvaluate("round manifest: leg {!r}: {} must be an absolute path with no "
                                     "'..' component and no NUL byte".format(leg_id, key))
        digest = leg["brief-sha256"]
        if not isinstance(digest, str) or not HEX64_RE.match(digest):
            raise CannotEvaluate("round manifest: leg {!r}: brief-sha256 must be 64 lower-hex "
                                 "characters".format(leg_id))
        out_legs.append(dict(leg))
    # required has no repeats here, so sorted equality is one leg per required family and no extra leg
    if sorted(leg_families) != sorted(required):
        raise CannotEvaluate("round manifest: the [[leg]] rows must carry exactly one leg per required "
                             "family; the required set comes from [policy], never from the legs present")
    return {"round": round_id, "revision": revision, "repository": repository, "critical": critical,
            "required": sorted(required), "legs": out_legs}


def validate_round(manifest_path, head, table_path):
    """The validated round, with its manifest-sha256, after the brief-integrity and --head checks; or
    CannotEvaluate naming what failed."""
    table = _load_family_table(table_path)
    data = _read_regular(_checked_no_nul(manifest_path, "round manifest"), "round manifest")
    summary = validate_manifest(parse_manifest(data), table)
    summary["manifest-sha256"] = hashlib.sha256(data).hexdigest()
    if not isinstance(head, str) or not SHA40_RE.match(head):
        raise CannotEvaluate("--head must be a full 40-hex lower-case commit SHA")
    if head != summary["revision"]:
        raise CannotEvaluate("--head {} is not the manifest's revision {}: the gate binds the reviewed "
                             "revision to the head it is given".format(head, summary["revision"]))
    for leg in summary["legs"]:
        got = hashlib.sha256(_read_regular(leg["brief"], "leg {!r} brief".format(leg["id"]))).hexdigest()
        if got != leg["brief-sha256"]:
            raise CannotEvaluate("round manifest: leg {!r}: the brief's bytes hash to {}, not the "
                                 "manifest's brief-sha256 {}".format(leg["id"], got, leg["brief-sha256"]))
    summary["notes"] = [ORDER_NOTE]
    return summary


def _harness_family(line):
    """The family= value of a delivery's first harness status line, or None when the line is not that
    line (check_verifier_delivery.HARNESS_FIRST_RE, whose named group this gate reads; the
    leg-evaluation step compares it with the leg's family)."""
    match = check_verifier_delivery.HARNESS_FIRST_RE.fullmatch(line)
    return None if match is None else match.group("family")


def _report(verdict, reasons, summary=None):
    report = {"tool": TOOL, "verdict": verdict, "reasons": reasons}
    if summary is not None:
        report["round"] = summary
    print(json.dumps(report, indent=2))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it, so the report's exit_code is the status the process really exits with.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    if argv and argv[0] in ("--self-test", "--execution-report"):
        rest = argv[1:] if argv[0] == "--self-test" else list(argv)
        if not rest:
            return self_test()
        if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
            return self_test(rest[1])
        print(USAGE, file=sys.stderr)
        return 2
    options = {}
    pairs = list(argv)
    while len(pairs) >= 2 and pairs[0] in ("--manifest", "--head", "--attempts",
                                           "--decisions", "--table") and pairs[0] not in options:
        options[pairs[0]] = pairs[1]
        pairs = pairs[2:]
    if pairs or not all(key in options for key in ("--manifest", "--head")):
        _report("CANNOT_EVALUATE", ["usage: --manifest and --head are each required exactly "
                                    "once; --attempts, --decisions and --table at most once"])
        print(USAGE, file=sys.stderr)
        return 2
    if "--attempts" in options:
        _report("CANNOT_EVALUATE", ["the attempts ledger is not read at this draft stage of the gate; "
                                    "it is refused, never silently ignored"])
        return 2
    if "--decisions" in options:
        _report("CANNOT_EVALUATE", ["decision records are not read at this draft stage of the gate; "
                                    "they are refused, never silently ignored"])
        return 2
    try:
        summary = validate_round(options["--manifest"], options["--head"],
                                 options.get("--table", str(ROOT / TABLE_REL)))
    except CannotEvaluate as exc:
        _report("CANNOT_EVALUATE", [str(exc)])
        return 2
    _report("CANNOT_EVALUATE", ["leg evaluation is not implemented at this draft stage: the round "
                                "manifest validates, and a validated round is refused, never passed"],
            summary)
    return 2


# ---------- self-test ----------

SUITE_ID = "review-round-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
REV = "a1" * 20
OTHER_REV = "b2" * 20


def _expected_check_ids():
    """The registered expected check ids of this suite, or None after printing (the caller exits 2):
    the execution set is reconciled in-run against the same hand-authored registry the execution gate
    reads, the posture the sibling check_verifier_delivery takes."""
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


class _MemoryBytes:
    """Bytes whose decode raises MemoryError: no manifest text can raise it without exhausting memory,
    so the parse handler's MemoryError arm is driven here."""

    def decode(self, encoding):
        raise MemoryError("injected allocation failure")


def _parse_memory_refused():
    try:
        parse_manifest(_MemoryBytes())
    except CannotEvaluate as exc:
        return "not valid UTF-8 TOML" in str(exc)
    except MemoryError:
        return False
    return False


def _brief_fixture(base):
    path = base / "brief.md"
    text = "A fixture review brief.\nBRIEF-PIN: {}\n\n1. Inspect the tree.\n".format(REV)
    path.write_text(text, encoding="utf-8")
    return str(path), hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_text(brief, brief_sha, families=("alpha", "beta"), critical=False, required=None,
                   version="1", rule=PANEL_RULE, leg_ids=None):
    required_names = list(families) if required is None else list(required)
    head = ["format-version = {}".format(version), 'round = "fixture-pass"',
            'revision = "{}"'.format(REV), 'repository = "fixture/round"', "", "[policy]",
            'rule = "{}"'.format(rule), "critical = {}".format("true" if critical else "false"),
            "required = [{}]".format(", ".join('"{}"'.format(name) for name in required_names))]
    rows = []
    for index, family in enumerate(families, 1):
        leg_id = (leg_ids or {}).get(index, "leg-{}".format(index))
        rows += ["", "[[leg]]", 'id = "{}"'.format(leg_id), 'family = "{}"'.format(family),
                 'brief = "{}"'.format(brief), 'brief-sha256 = "{}"'.format(brief_sha),
                 'delivery = "{}.delivery-{}"'.format(brief, index)]
    return "\n".join(head + rows) + "\n"


def _outcome(base, text, head=REV, table_text=None, skip_table=False):
    """("ok", "") or ("cannot-evaluate", reason) of validate_round on a written fixture; text None
    leaves the manifest file absent."""
    tmp = Path(tempfile.mkdtemp(dir=base))
    manifest = tmp / "manifest.toml"
    if text is not None:
        manifest.write_text(text, encoding="utf-8")
    table = tmp / "table.toml"
    if not skip_table:
        table.write_text(check_brief_capability.TABLE_FIXTURE if table_text is None else table_text,
                         encoding="utf-8")
    try:
        validate_round(str(manifest), head, str(table))
    except CannotEvaluate as exc:
        return ("cannot-evaluate", str(exc))
    return ("ok", "")


def _direct(manifest_path, head, table_path):
    """("ok", "") or ("cannot-evaluate", reason) of validate_round on caller-supplied paths, for the
    rows whose refusal must come before any file is opened."""
    try:
        validate_round(manifest_path, head, table_path)
    except CannotEvaluate as exc:
        return ("cannot-evaluate", str(exc))
    return ("ok", "")


def _refused(outcome, needle):
    return outcome[0] == "cannot-evaluate" and needle in outcome[1]


def _main_quiet(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(argv)
    return code, out.getvalue()


def _self_test_cases(base):
    brief, brief_sha = _brief_fixture(base)
    valid_two = _manifest_text(brief, brief_sha)
    wide_table = (check_brief_capability.TABLE_FIXTURE
                  + check_brief_capability._family_row("delta", "yes", "no", "yes", "none", "no", "no"))
    # the manifest cannot-evaluate rows
    check("round/manifest-unreadable-cannot-evaluate", _refused(_outcome(base, None), "cannot open"), True)
    check("round/manifest-malformed-cannot-evaluate",
          _refused(_outcome(base, "= not toml ="), "not valid UTF-8 TOML"), True)
    check("round/manifest-toml-recursion-cannot-evaluate",
          _refused(_outcome(base, "x = " + "[" * 4000 + "]" * 4000 + "\n"), "not valid UTF-8 TOML"), True)
    check("round/manifest-toml-memory-cannot-evaluate", _parse_memory_refused(), True)
    check("round/manifest-unknown-key-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('repository = "fixture/round"',
                                                    'repository = "fixture/round"\nextra = 1')),
                   "exactly the keys"), True)
    check("round/manifest-version-bool-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, version="true")),
                   "format-version"), True)
    check("round/head-differs-from-revision-cannot-evaluate",
          _refused(_outcome(base, valid_two, head=OTHER_REV), "is not the manifest's revision"), True)
    check("round/unknown-family-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "epsilon"))),
                   "not a family"), True)
    check("round/duplicate-family-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "alpha"))),
                   "repeats a family"), True)
    check("round/legs-differ-from-required-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "beta"),
                                                 required=("alpha", "gamma"))),
                   "one leg per required family"), True)
    check("round/duplicate-leg-id-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, leg_ids={1: "leg-x", 2: "leg-x"})),
                   "is repeated"), True)
    check("round/brief-hash-mismatch-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, "0" * 64)), "hash to"), True)
    check("round/brief-path-relative-cannot-evaluate",
          _refused(_outcome(base, _manifest_text("relative/brief.md", brief_sha)), "absolute path"), True)
    check("round/table-unreadable-cannot-evaluate",
          _refused(_outcome(base, valid_two, skip_table=True), "capability table"), True)
    # one row per validator refusal, each with a source flip that removes only that refusal
    check("round/round-id-format-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('round = "fixture-pass"',
                                                    'round = "{}"'.format("a" * 65))),
                   "round must be an id"), True)
    check("round/round-id-at-length-validates",
          _outcome(base, valid_two.replace('round = "fixture-pass"',
                                           'round = "{}"'.format("a" * 64)))[0], "ok")
    check("round/revision-format-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('revision = "{}"'.format(REV),
                                                    'revision = "{}"'.format("g" * 40))),
                   "revision must be a full 40-hex"), True)
    check("round/revision-sha256-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('revision = "{}"'.format(REV),
                                                    'revision = "{}"'.format("a1" * 32))),
                   "revision must be a full 40-hex"), True)
    check("round/repository-shape-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('repository = "fixture/round"',
                                                    'repository = "Not An Id/../x"')),
                   "owner/name"), True)
    check("round/repository-control-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('repository = "fixture/round"',
                                                    'repository = "fixture/ro\\nund"')),
                   "owner/name"), True)
    check("round/repository-at-length-validates",
          _outcome(base, valid_two.replace('repository = "fixture/round"',
                                           'repository = "{}/{}"'.format("a" * 100, "b" * 100)))[0],
          "ok")
    check("round/repository-over-length-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('repository = "fixture/round"',
                                                    'repository = "{}/{}"'.format("a" * 101, "b" * 100))),
                   "owner/name"), True)
    check("round/version-two-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, version="2")),
                   "format-version"), True)
    check("round/policy-keys-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('rule = "vrfdiv"', 'rule = "vrfdiv"\nextra = 1')),
                   "[policy] must have exactly"), True)
    check("round/policy-rule-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, rule="consensus")),
                   "policy.rule must be"), True)
    check("round/critical-type-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace("critical = false", "critical = 1")),
                   "policy.critical must be a bool"), True)
    check("round/required-type-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('required = ["alpha", "beta"]',
                                                    'required = "alpha"')),
                   "non-empty list of family names"), True)
    check("round/legs-type-cannot-evaluate",
          _refused(_outcome(base, valid_two.split("\n\n[[leg]]", 1)[0]
                            .replace("\n\n[policy]", "\nleg = 3\n\n[policy]")),
                   "rows are required"), True)
    check("round/leg-keys-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('id = "leg-1"', 'id = "leg-1"\nextra = 1')),
                   "leg row 1 must have exactly"), True)
    check("round/leg-id-format-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, leg_ids={1: "Bad Id"})),
                   "leg row 1 id must be an id"), True)
    check("round/leg-family-type-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace('family = "alpha"', "family = 3")),
                   "family must be a string"), True)
    check("round/brief-sha-format-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, "0" * 63)), "64 lower-hex"), True)
    check("round/head-format-cannot-evaluate",
          _refused(_outcome(base, valid_two, head="xyz"), "--head must be a full 40-hex"), True)
    check("round/brief-path-dotdot-cannot-evaluate",
          _refused(_outcome(base, _manifest_text("/tmp/../absent.md", brief_sha)),
                   "absolute path with no"), True)
    # NUL-byte path refusals: without them, os.open raises ValueError past the exit contract
    check("round/brief-path-nul-cannot-evaluate",
          _refused(_outcome(base, _manifest_text("/tmp/a\\u0000b", brief_sha)), "NUL"), True)
    check("round/delivery-path-nul-cannot-evaluate",
          _refused(_outcome(base, valid_two.replace(".delivery-1", ".deli\\u0000very-1")), "NUL"), True)
    nul_tmp = Path(tempfile.mkdtemp(dir=base))
    nul_table = nul_tmp / "table.toml"
    nul_table.write_text(check_brief_capability.TABLE_FIXTURE, encoding="utf-8")
    nul_manifest = nul_tmp / "manifest.toml"
    nul_manifest.write_text(valid_two, encoding="utf-8")
    check("round/manifest-path-nul-cannot-evaluate",
          _refused(_direct(str(nul_tmp) + "/mani\x00fest.toml", REV, str(nul_table)), "NUL"), True)
    check("round/table-path-nul-cannot-evaluate",
          _refused(_direct(str(nul_manifest), REV, str(nul_tmp) + "/ta\x00ble.toml"), "NUL"), True)
    # the floor boundaries: just outside refuses, exactly at and just inside validate
    check("round/panel-below-floor-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, families=("alpha",))),
                   "diversity floor"), True)
    check("round/critical-two-families-cannot-evaluate",
          _refused(_outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "beta"),
                                                 critical=True)),
                   "diversity floor"), True)
    check("round/floor-exactly-two-validates", _outcome(base, valid_two)[0], "ok")
    check("round/floor-three-validates",
          _outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "beta", "gamma")))[0], "ok")
    check("round/critical-three-validates",
          _outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "beta", "gamma"),
                                        critical=True))[0], "ok")
    check("round/critical-four-validates",
          _outcome(base, _manifest_text(brief, brief_sha, families=("alpha", "beta", "gamma", "delta"),
                                        critical=True), table_text=wide_table)[0], "ok")
    # the CLI, in process: usage and the draft stage's fail-closed refusals
    check("round/usage-cannot-evaluate", _main_quiet([])[0], 2)
    tmp = Path(tempfile.mkdtemp(dir=base))
    manifest = tmp / "manifest.toml"
    manifest.write_text(valid_two, encoding="utf-8")
    table = tmp / "table.toml"
    table.write_text(check_brief_capability.TABLE_FIXTURE, encoding="utf-8")
    argv = ["--manifest", str(manifest), "--head", REV, "--table", str(table)]
    code, out = _main_quiet(argv)
    check("round/validated-round-cannot-evaluate-draft", (code, "leg evaluation" in out), (2, True))
    code, out = _main_quiet(argv + ["--repo", str(tmp)])
    check("round/repo-option-refused-cannot-evaluate", (code, "usage" in out), (2, True))
    code, out = _main_quiet(argv + ["--attempts", str(manifest)])
    check("round/attempts-not-implemented-cannot-evaluate", (code, "attempts ledger" in out), (2, True))
    code, out = _main_quiet(argv + ["--decisions", str(manifest)])
    check("round/decisions-not-implemented-cannot-evaluate", (code, "decision records" in out), (2, True))
    # the named group this gate reads from the checker's harness first-line grammar
    check("group/harness-family-named-group",
          (_harness_family("WORKER_STATUS: PASS (account=a, rc=0, family=b, model=c, effort=d)"),
           _harness_family("WORKER_STATUS_FAKE (account=a, rc=0, family=b, model=c, effort=d)")),
          ("b", None))


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    try:
        base = Path(tempfile.mkdtemp(prefix="review-round-selftest-"))
        try:
            _self_test_cases(base)
        finally:
            shutil.rmtree(base)
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (one row per round-manifest validator refusal, "
          "the floor and length boundary rows, and the CLI's fail-closed draft-stage refusals); "
          "execution set reconciled against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This tool imports evaluators that start
# git and capped scanner children and, from the leg-evaluation step on, reads their exit status
# (subprocess, os.wait4), so under an ignored SIGCHLD a failing child would read as a pass; the guard
# runs at the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("check_review_round.py") or main())
