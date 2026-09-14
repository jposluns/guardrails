#!/usr/bin/env python3
"""Advisory-contract wording gate: best-effort heuristic; the authoritative behavior is the code and the
--self-test.

A wording check over the registered advisory documentation surfaces: the two WARN-only detectors and the
shared input collector (their comment and docstring prose), plus the gate residues for timer-restore,
path-classification, and advisory-contract in the gate manifest and the enforceability ledger. It fails
(exit 1) when an absolute-guarantee word or phrase appears in a scanned prose region, so the
zero-prose-guarantee doctrine does not silently regress into a fresh unconditional claim. It also
reconciles the three gates' rule memberships so a coordinated removal of a residue from the manifest and
ledger is surfaced. It fails closed (exit 2) on a missing, unreadable, non-UTF-8, unparseable, or
structurally invalid required input, and on a non-isolated run.

Potential gaps: a novel paraphrase that avoids the vocabulary, prose outside the scanned regions, semantic
errors inside otherwise word-clean text, and changes to this checker or its word list; human review remains
part of the control.
"""
import sys


def _interpreter_isolated(flags):
    # Resolver
    return bool(flags.isolated) or bool(
        getattr(flags, "safe_path", 0) and flags.ignore_environment and flags.no_user_site)


if not _interpreter_isolated(sys.flags):
    sys.stderr.write("check_advisory_contract: refusing to run non-isolated; launch it as "
                     "`python3 -I -B tools/check_advisory_contract.py` (a sibling file could otherwise "
                     "shadow a stdlib import and neuter this gate)\n")
    raise SystemExit(2)

import ast  # noqa: E402
import importlib.util  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import tokenize  # noqa: E402
import tomllib  # noqa: E402
from pathlib import Path  # noqa: E402

# Resolver
_HELPER_MODULE = None


def _advisory_inputs():
    # Input collection: load the shared descriptor-bound reader from the trusted tool installation by
    # explicit file location (not via sys.path, which the isolation guard removes).
    global _HELPER_MODULE
    if _HELPER_MODULE is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "_advisory_inputs.py")
        spec = importlib.util.spec_from_file_location("aiqt_advisory_inputs", path)
        if spec is None or spec.loader is None:
            raise OSError("cannot load the shared input collector at {}".format(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _HELPER_MODULE = mod
    return _HELPER_MODULE


# The prohibited absolute-guarantee vocabulary. Single words are matched whole-word, case-insensitive;
# phrases are matched as normalized substrings. The list is deliberately narrow (assertive absolute
# guarantees), so it does not fire on ordinary best-effort explanatory prose that avoids these terms.
WORDS = (
    "never", "always", "guarantee", "guarantees", "guaranteed", "guaranteeing",
    "faithful", "faithfully", "unconditional", "unconditionally", "infallible",
    "exhaustive", "iff", "ensure", "ensures", "ensured", "ensuring",
)
PHRASES = ("never blocks", "cannot fail", "cannot escape", "under no circumstances", "without exception")
_WORD_RES = tuple((w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)) for w in WORDS)
_NO_EVER = re.compile(r"\bno\b[\s\S]{0,40}?\bever\b", re.IGNORECASE)

DETECTOR_FILES = (
    "tools/check_timer_restore.py",
    "tools/check_path_classification.py",
    "tools/_advisory_inputs.py",
)
MANIFEST_REL = ".aiqt/core/gates/manifest.toml"
LEDGER_REL = ".aiqt/enforceability.json"
# Diagnostics: the profiled gates and the rule memberships each must carry, reconciled across manifest and
# ledger so a coordinated residue removal is surfaced.
EXPECTED_MEMBERS = {
    "timer-restore": {"tmrrst"},
    "path-classification": {"secspr", "chkfcl"},
    "advisory-contract": {"clmobs", "dscres"},
}
PROFILED_GATE_IDS = frozenset(EXPECTED_MEMBERS)


class _Refusal(Exception):
    # Diagnostics
    def __init__(self, where, detail):
        super().__init__(detail)
        self.where = where
        self.detail = detail


def _hits(text, words=_WORD_RES, phrases=PHRASES):
    # Diagnostics: the prohibited words and phrases present in one normalized prose region.
    low = text.lower()
    found = []
    for w, rx in words:
        if rx.search(text):
            found.append(w)
    for ph in phrases:
        if ph in low:
            found.append(ph)
    if _NO_EVER.search(text):
        found.append("no ... ever")
    return found


def _normalize(text):
    # Scope traversal: fold case-insensitive apostrophe/whitespace presentation so a wrapped or spaced
    # phrase still matches; case folding is done inside _hits.
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text)


def _prose_regions(rel, raw):
    # Scope traversal: the comment tokens and the module/class/function docstrings of one Python source, as
    # (label, text) pairs. Non-docstring string literals (fixture data, runtime messages, arguments) are
    # not prose and are excluded. A decode or parse failure is a located refusal (cannot-evaluate).
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Refusal(rel, "not valid UTF-8: {}".format(exc))
    try:
        tree = ast.parse(text, filename=rel)
    except (SyntaxError, ValueError) as exc:
        raise _Refusal(rel, "does not parse: {}".format(exc))
    regions = []
    doc = ast.get_docstring(tree, clean=False)
    if doc:
        regions.append(("{}:module-docstring".format(rel), doc))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                regions.append(("{}:{}:{}".format(rel, getattr(node, "name", "?"),
                                                  getattr(node, "lineno", 0)), d))
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                regions.append(("{}:comment:{}".format(rel, tok.start[0]), tok.string))
    except (tokenize.TokenError, IndentationError) as exc:
        raise _Refusal(rel, "cannot tokenize: {}".format(exc))
    return regions, text


def _scan_python(root, rel, findings):
    # Input collection
    helper = _advisory_inputs()
    try:
        raw = helper.read_source_file(os.path.join(root, rel))
    except Exception as exc:  # noqa: BLE001
        raise _Refusal(rel, "cannot read required input: {}".format(exc))
    regions, text = _prose_regions(rel, raw)
    for label, region in regions:
        for hit in _hits(_normalize(region)):
            findings.append((label, hit))
    # Diagnostics: a whole-source phrase pass covers the few high-signal phrases that could appear in a
    # runtime message string outside a comment or docstring.
    low = _normalize(text).lower()
    for ph in PHRASES:
        if ph in low:
            findings.append(("{}:source".format(rel), ph))


def _load_manifest(root):
    # Input collection
    helper = _advisory_inputs()
    try:
        raw = helper.read_source_file(os.path.join(root, MANIFEST_REL))
        data = tomllib.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise _Refusal(MANIFEST_REL, "cannot read or parse the gate manifest: {}".format(exc))
    if not isinstance(data, dict):
        raise _Refusal(MANIFEST_REL, "the gate manifest is not a table")
    gates = data.get("gate")
    if not isinstance(gates, list):
        raise _Refusal(MANIFEST_REL, "the gate manifest has no [[gate]] array")
    return gates


def _no_dup_pairs(pairs):
    # Diagnostics
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key {!r}".format(k))
        out[k] = v
    return out


def _load_ledger(root):
    # Input collection
    helper = _advisory_inputs()
    try:
        raw = helper.read_source_file(os.path.join(root, LEDGER_REL))
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_dup_pairs)
    except Exception as exc:  # noqa: BLE001
        raise _Refusal(LEDGER_REL, "cannot read or parse the enforceability ledger: {}".format(exc))
    if not isinstance(data, dict):
        raise _Refusal(LEDGER_REL, "the enforceability ledger is not a JSON object")
    if data.get("version") != 1:
        raise _Refusal(LEDGER_REL, "unexpected ledger version {!r}".format(data.get("version")))
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise _Refusal(LEDGER_REL, "the ledger has no rules array")
    return rules


def _scan_manifest(root, findings):
    # Input collection: scan the profiled gates' residues and reconcile their rule memberships.
    gates = _load_manifest(root)
    seen = {}
    for g in gates:
        if not isinstance(g, dict):
            raise _Refusal(MANIFEST_REL, "a [[gate]] entry is not a table")
        gid = g.get("id")
        if gid is not None and not isinstance(gid, str):
            raise _Refusal(MANIFEST_REL, "a [[gate]] entry has a non-string id {!r}".format(gid))
        if gid in PROFILED_GATE_IDS:
            residue = g.get("residue", "")
            if not isinstance(residue, str) or not residue.strip():
                raise _Refusal(MANIFEST_REL, "gate {!r} has no residue text".format(gid))
            for hit in _hits(_normalize(residue)):
                findings.append(("{}:{}:residue".format(MANIFEST_REL, gid), hit))
            rules = g.get("rules")
            if not isinstance(rules, list):
                raise _Refusal(MANIFEST_REL, "gate {!r} has no rules list".format(gid))
            for r in rules:
                if not isinstance(r, str):
                    raise _Refusal(MANIFEST_REL,
                                   "gate {!r} has a non-string rule member {!r}".format(gid, r))
            seen[gid] = set(rules)
    for gid, want in EXPECTED_MEMBERS.items():
        if gid not in seen:
            raise _Refusal(MANIFEST_REL, "profiled gate {!r} is missing from the manifest".format(gid))
        if seen[gid] != want:
            raise _Refusal(MANIFEST_REL, "gate {!r} rules {} do not match the expected {}".format(
                gid, sorted(seen[gid]), sorted(want)))


def _scan_ledger(root, findings):
    # Input collection: scan the profiled gates' ledger residues and reconcile the rule->gate occurrences.
    rules = _load_ledger(root)
    occurrences = {}
    for entry in rules:
        if not isinstance(entry, dict):
            raise _Refusal(LEDGER_REL, "a ledger rule entry is not an object")
        cid = entry.get("corpus-id")
        if not isinstance(cid, str):
            raise _Refusal(LEDGER_REL, "a ledger rule entry has a non-string corpus-id {!r}".format(cid))
        gates = entry.get("gates", [])
        if not isinstance(gates, list):
            raise _Refusal(LEDGER_REL, "rule {!r} has a non-list gates value".format(cid))
        for g in gates:
            if not isinstance(g, dict):
                raise _Refusal(LEDGER_REL, "rule {!r} has a gate entry that is not an object".format(cid))
            gid = g.get("id")
            if gid is not None and not isinstance(gid, str):
                raise _Refusal(LEDGER_REL,
                               "rule {!r} has a gate with a non-string id {!r}".format(cid, gid))
            if gid in PROFILED_GATE_IDS:
                residue = g.get("residue", "")
                if not isinstance(residue, str) or not residue.strip():
                    raise _Refusal(LEDGER_REL, "gate {!r} under rule {!r} has no residue".format(gid, cid))
                for hit in _hits(_normalize(residue)):
                    findings.append(("{}:{}:{}:residue".format(LEDGER_REL, cid, gid), hit))
                occurrences.setdefault(gid, set()).add(cid)
    for gid, want in EXPECTED_MEMBERS.items():
        got = occurrences.get(gid, set())
        if got != want:
            raise _Refusal(LEDGER_REL, "gate {!r} ledger occurrences {} do not match the expected {}"
                           .format(gid, sorted(got), sorted(want)))


def check(root):
    """Scan the registered surfaces and reconcile the profiled memberships. Returns 0 (clean), 1 (a
    prohibited-wording finding), or 2 (a located cannot-evaluate). Best-effort heuristic; the authoritative
    behavior is the code and the --self-test."""
    findings = []
    try:
        for rel in DETECTOR_FILES:
            _scan_python(root, rel, findings)
        _scan_manifest(root, findings)
        _scan_ledger(root, findings)
    except _Refusal as ref:
        print("cannot-evaluate: {}: {}".format(ref.where, ref.detail))
        print("RESULT: cannot-evaluate; fail-closed")
        return 2
    except Exception as exc:  # noqa: BLE001
        print("cannot-evaluate: {}: unexpected error: {}".format(root, exc))
        print("RESULT: cannot-evaluate; fail-closed")
        return 2
    if findings:
        for where, hit in sorted(set(findings)):
            print("FINDING: {}: prohibited advisory wording {!r}".format(where, hit))
        print("RESULT: {} prohibited-wording finding(s)".format(len(set(findings))))
        return 1
    print("RESULT: advisory-contract wording clean over the registered surfaces")
    return 0


def _repo_root():
    p = Path(__file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return anc
    return Path.cwd()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        return self_test_main()
    root = None
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            sys.stderr.write("check_advisory_contract: --root requires a path\n")
            return 2
        root = args[i + 1]
    return check(root if root is not None else str(_repo_root()))


# --- self-test ----------------------------------------------------------------------------------------
_CLEAN_DOC = ('"""best-effort heuristic advisory; the authoritative behavior is the code and the '
              '--self-test."""\n')
_CLEAN_TIMER_RESIDUE = ("best-effort heuristic advisory; the authoritative behavior is the code and the "
                        "--self-test. False positives and false negatives remain possible.")
_CLEAN_PATH_RESIDUE = _CLEAN_TIMER_RESIDUE
_CLEAN_AC_RESIDUE = ("A wording and structure check over the registered advisory documentation surfaces. "
                     "Human review remains part of the control.")


def _write_synthetic(root, timer_residue=None, path_residue=None, ac_residue=None,
                     timer_doc=None, helper_doc=None):
    # Fixture cases: a minimal conforming tree carrying exactly the scoped surfaces.
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / ".aiqt/core/gates").mkdir(parents=True, exist_ok=True)
    (root / ".aiqt").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "check_timer_restore.py").write_text(timer_doc or _CLEAN_DOC, encoding="utf-8")
    (root / "tools" / "check_path_classification.py").write_text(_CLEAN_DOC, encoding="utf-8")
    (root / "tools" / "_advisory_inputs.py").write_text(helper_doc or _CLEAN_DOC, encoding="utf-8")
    manifest = ""
    for gid, rules, residue in (
            ("timer-restore", ["tmrrst"], timer_residue or _CLEAN_TIMER_RESIDUE),
            ("path-classification", ["secspr", "chkfcl"], path_residue or _CLEAN_PATH_RESIDUE),
            ("advisory-contract", ["clmobs", "dscres"], ac_residue or _CLEAN_AC_RESIDUE)):
        rules_toml = ", ".join('"{}"'.format(r) for r in rules)
        manifest += ('[[gate]]\nid = "{}"\nscript = "tools/x.py"\nrules = [{}]\nplatform = "ci"\n'
                     'default = "warn"\nclass = "c"\nresidue = {}\n\n'
                     .format(gid, rules_toml, _toml_str(residue)))
    (root / ".aiqt/core/gates/manifest.toml").write_text(manifest, encoding="utf-8")
    ledger = {
        "version": 1,
        "boundary": "synthetic",
        "rules": [
            {"corpus-id": "tmrrst", "gates": [{"id": "timer-restore",
                                               "residue": timer_residue or _CLEAN_TIMER_RESIDUE}]},
            {"corpus-id": "secspr", "gates": [{"id": "path-classification",
                                               "residue": path_residue or _CLEAN_PATH_RESIDUE}]},
            {"corpus-id": "chkfcl", "gates": [{"id": "path-classification",
                                               "residue": path_residue or _CLEAN_PATH_RESIDUE}]},
            {"corpus-id": "clmobs", "gates": [{"id": "advisory-contract",
                                               "residue": ac_residue or _CLEAN_AC_RESIDUE}]},
            {"corpus-id": "dscres", "gates": [{"id": "advisory-contract",
                                               "residue": ac_residue or _CLEAN_AC_RESIDUE}]},
        ],
    }
    (root / ".aiqt/enforceability.json").write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n",
                                                    encoding="utf-8")


def _toml_str(s):
    # Fixture cases: a TOML basic-string literal for a residue value.
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def self_test_main():
    import shutil
    import subprocess
    import tempfile

    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-advisory-contract-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        # Fixture cases: a conforming synthetic tree is clean (exit 0).
        base = tmp / "clean"
        _write_synthetic(base)
        rc = _quiet_check(str(base))
        if rc != 0:
            failures.append("clean synthetic tree: expected 0, got {}".format(rc))

        # Fixture cases: each word family injected into a docstring, a comment, and a residue is caught (1).
        for word in ("never", "always", "guaranteed", "faithful", "unconditional", "iff", "ensures",
                     "exhaustive"):
            d = tmp / ("doc_" + word)
            _write_synthetic(d, timer_doc='"""best-effort; this {} holds."""\n'.format(word))
            if _quiet_check(str(d)) != 1:
                failures.append("docstring word {!r}: expected 1".format(word))
            c = tmp / ("com_" + word)
            _write_synthetic(c, timer_doc='"""ok."""\n# a note that {} applies\n'.format(word))
            if _quiet_check(str(c)) != 1:
                failures.append("comment word {!r}: expected 1".format(word))
            r = tmp / ("res_" + word)
            _write_synthetic(r, timer_residue="best-effort; this {} holds.".format(word))
            if _quiet_check(str(r)) != 1:
                failures.append("residue word {!r}: expected 1".format(word))

        # Fixture cases: phrase and wrapped/spaced variants, and an unlisted paraphrase that stays clean.
        for phrase, expect in (("never blocks", 1), ("cannot fail", 1), ("under no circumstances", 1),
                               ("no output is ever written", 1),
                               ("nested bodies are left untouched", 0)):
            d = tmp / ("phr_" + re.sub(r"\W+", "_", phrase))
            _write_synthetic(d, timer_doc='"""note: {}."""\n'.format(phrase))
            if _quiet_check(str(d)) != expect:
                failures.append("phrase {!r}: expected {}".format(phrase, expect))

        # Fixture cases: mixed case is caught.
        mc = tmp / "mixedcase"
        _write_synthetic(mc, timer_doc='"""this NeVeR happens."""\n')
        if _quiet_check(str(mc)) != 1:
            failures.append("mixed-case: expected 1")

        # Fixture cases: a coordinated manifest membership change is a cannot-evaluate (2).
        mm = tmp / "badmembers"
        _write_synthetic(mm)
        man = (mm / ".aiqt/core/gates/manifest.toml").read_text(encoding="utf-8")
        man = man.replace('rules = ["tmrrst"]', 'rules = ["tmrrst", "extra"]')
        (mm / ".aiqt/core/gates/manifest.toml").write_text(man, encoding="utf-8")
        if _quiet_check(str(mm)) != 2:
            failures.append("bad manifest membership: expected 2")

        # Fixture cases: a malformed ledger is a cannot-evaluate (2).
        bl = tmp / "badledger"
        _write_synthetic(bl)
        (bl / ".aiqt/enforceability.json").write_text("{ not json", encoding="utf-8")
        if _quiet_check(str(bl)) != 2:
            failures.append("malformed ledger: expected 2")

        # Fixture cases: a valid-JSON non-dict ledger (an array) is a located cannot-evaluate (2), not an
        # uncaught traceback: the decoded document's structural type is confirmed before it is consumed.
        ndl = tmp / "nondict_ledger"
        _write_synthetic(ndl)
        (ndl / ".aiqt/enforceability.json").write_text("[]\n", encoding="utf-8")
        if _quiet_check(str(ndl)) != 2:
            failures.append("non-dict ledger: expected 2")

        # Fixture cases: a ledger rule entry of the wrong type (a non-object record) is a cannot-evaluate (2).
        nor = tmp / "nonobj_ledger_record"
        _write_synthetic(nor)
        led = json.loads((nor / ".aiqt/enforceability.json").read_text(encoding="utf-8"))
        led["rules"].append("not-an-object")
        (nor / ".aiqt/enforceability.json").write_text(
            json.dumps(led, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if _quiet_check(str(nor)) != 2:
            failures.append("non-object ledger record: expected 2")

        # Fixture cases: a ledger gate id of the wrong type is a cannot-evaluate (2), never an unhashable
        # membership-test crash.
        nid = tmp / "nonstr_ledger_id"
        _write_synthetic(nid)
        led = json.loads((nid / ".aiqt/enforceability.json").read_text(encoding="utf-8"))
        led["rules"][0]["gates"][0]["id"] = ["timer-restore"]
        (nid / ".aiqt/enforceability.json").write_text(
            json.dumps(led, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if _quiet_check(str(nid)) != 2:
            failures.append("non-string ledger gate id: expected 2")

        # Fixture cases: a manifest [[gate]] value that is not a table is a cannot-evaluate (2) rather than
        # an uncaught traceback (the same structural discipline on the manifest loader).
        ntm = tmp / "nontable_manifest"
        _write_synthetic(ntm)
        (ntm / ".aiqt/core/gates/manifest.toml").write_text('gate = ["not-a-table"]\n', encoding="utf-8")
        if _quiet_check(str(ntm)) != 2:
            failures.append("non-table manifest gate: expected 2")

        # Fixture cases: a symlinked parent component on the gate's own read path is a cannot-evaluate (2),
        # never a clean pass, because read_source_file resolves the parent component-by-component under
        # no-follow directory descriptors.
        slp = tmp / "symlink_parent"
        _write_synthetic(slp)
        (slp / "tools").rename(slp / "tools_real")
        os.symlink("tools_real", slp / "tools")
        if _quiet_check(str(slp)) != 2:
            failures.append("symlinked tools parent: expected 2")

        # Fixture cases: a missing required input is a cannot-evaluate (2).
        ms = tmp / "missing"
        _write_synthetic(ms)
        (ms / "tools" / "_advisory_inputs.py").unlink()
        if _quiet_check(str(ms)) != 2:
            failures.append("missing required input: expected 2")

        # Fixture cases: disabling the word list stops the injection from being caught (the list is
        # load-bearing).
        globals_words = _WORD_RES
        try:
            probe = tmp / "probe"
            _write_synthetic(probe, timer_doc='"""this never holds."""\n')
            found = _hits(_normalize('"""this never holds."""'), words=())
            if found:
                failures.append("empty word list still flagged {!r} (list not load-bearing)".format(found))
        finally:
            assert globals_words is _WORD_RES

        # Fixture cases: a CLI 0 -> 1 -> 0 transition over the real repo, an injected copy, and restoration.
        this = str(Path(__file__).resolve())

        def cli(root):
            proc = subprocess.run([sys.executable, "-I", "-B", this, "--root", root],
                                  capture_output=True, env=dict(os.environ))
            return proc.returncode

        clean_root = tmp / "cli_clean"
        _write_synthetic(clean_root)
        if cli(str(clean_root)) != 0:
            failures.append("CLI clean: expected 0")
        inj_root = tmp / "cli_inj"
        _write_synthetic(inj_root, timer_doc='"""this never happens."""\n')
        if cli(str(inj_root)) != 1:
            failures.append("CLI injected: expected 1 (RED-flip)")
        _write_synthetic(inj_root)
        if cli(str(inj_root)) != 0:
            failures.append("CLI restored: expected 0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        for f in failures:
            print("SELF-TEST FAIL: {}".format(f), file=sys.stderr)
        return 1
    print("SELF-TEST PASS: the conforming synthetic surfaces are clean, each prohibited word family and "
          "phrase injected into a docstring, comment, and residue is caught, an unlisted clean paraphrase "
          "is accepted, coordinated membership and malformed-input cases fail closed to a cannot-evaluate, "
          "a valid-JSON non-dict or structurally-malformed ledger record or id, a non-table manifest gate, "
          "and a symlinked parent on the read path each fail closed to a located cannot-evaluate rather "
          "than an uncaught traceback or a clean pass, and the CLI shows a 0->1->0 transition on injection "
          "and restoration; these are fixture-bound invariants, not a general account of the checker's "
          "behavior.")
    return 0


def _quiet_check(root):
    # Fixture cases: check() with stdout suppressed, returning only the exit code.
    from contextlib import redirect_stdout
    with redirect_stdout(io.StringIO()):
        return check(root)


if __name__ == "__main__":
    raise SystemExit(main())
