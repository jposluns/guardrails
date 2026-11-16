#!/usr/bin/env python3
"""Self-test check removal gate (selftest-removal): a change that removes a registered self-test check id
lands only with that id's coverage accounted for in the same change.

A removed test case cannot fail, so a run of the suite after the removal is green whether or not the case
still mattered. This gate reads the expected-check-ids lists of tools/selftest_checks.toml at the change's
comparison base and at its head, and refuses (exit 1) a NET REMOVAL: a (suite id, check id) pair registered
at the base and not at the head (an id moved to another suite counts as removed from its old suite; an id
added anywhere is not read), unless the accounting record .aiqt/check-removals.toml at the head names that
pair in one of the three states of the gate-discipline rule, in an entry this change wrote:

  schema = 1
  [[removal]]
  suite = "<suite id>"
  id = "<removed check id>"
  state = "covered"            covered-by-suite and covered-by-id name a check id registered at the head
  state = "unreachable"        capability-removed (the change that removes what the case depends on) and
                               observed (the attempt that confirms the path is gone), each non-blank
  state = "disclosed"          authorization (the maintainer's recorded authorization reference), non-blank

An entry the base record already holds unchanged was not written by this change and accounts for nothing; a
covered-by pair that is not registered at the head (removed in this change too, or never registered) covers
nothing. Each of those is a finding (exit 1), and so is a removed pair with no entry.

THE BASE is the one tools/check_release_cut.py derives (its context(), imported, not copied): in a
pull_request run (GITHUB_BASE_REF set) the merge HEAD's raw first parent, which must be the resolved
origin/GITHUB_BASE_REF tip; on a push, --protected origin/<branch> and --base <the event's before> (the
pushed range from before to HEAD); on a workflow_dispatch or schedule run, --protected alone (HEAD against
its first parent); locally, the unique merge base of HEAD and origin/HEAD's target. Every refusal of that
derivation (no origin/HEAD, a shallow or grafted history, more than one merge base, HEAD behind the target,
an unsupported event) is CANNOT_EVALUATE here too. The registry and the record are read from the base and
head commit trees through the same module's tree_blob (exact path components; a symlink, tree or gitlink
in their place is refused), never from the index or the working tree.

Exit 0: no net removal, or every removed pair accounted for (each accounted pair is printed).
Exit 1: an unaccounted removed pair, a stale entry, or a covered-by pair that does not survive.
Exit 2 (CANNOT_EVALUATE): the base cannot be derived or read, a git read fails, the registry is absent at
the head while the base holds it (absent at both ends, nothing is removed), or the registry or the record is not UTF-8, not TOML, or not the shape above (an unknown key or
state, a missing or blank field, a repeated pair, a format-version other than 1). Never a pass.

  check_selftest_removal.py [--root DIR] [--protected REF] [--base OID]
  check_selftest_removal.py --self-test [--execution-report ABS_PATH]

RESIDUALS: it reads registered ids, not test code, so a case kept in the registry while its body is
weakened, a check whose assertion is emptied, and a test file outside the registry are not seen; an id
renamed in the registry reads as a removal and an addition, so it needs an entry (covered by the new id);
the record proves that an accounting was written, never that it is true (the covered-by case may not catch
what the removed one did, and the authorization reference is not looked up); the comparison is of the two
endpoints, so a removal and re-addition inside the range is not seen; and, like its base derivation, it
trusts the git binary, the repository configuration and origin/HEAD.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_selftest_removal.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_release_cut as cut  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "tools/selftest_checks.toml"
RECORD = ".aiqt/check-removals.toml"
STATES = {
    "covered": ("covered-by-suite", "covered-by-id"),
    "unreachable": ("capability-removed", "observed"),
    "disclosed": ("authorization",),
}
ENTRY_KEYS = frozenset(("suite", "id", "state")) | frozenset(key for keys in STATES.values() for key in keys)
EXIT_OK, EXIT_REFUSE, EXIT_CANNOT_EVALUATE = 0, 1, 2


class CannotEvaluate(ValueError):
    """An input that cannot be judged: an underivable base, a failed git read, or a malformed file."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def parse_toml(raw, label):
    """The decoded TOML document of raw bytes, or CannotEvaluate (never a partial read)."""
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError, MemoryError, ValueError) as exc:
        raise CannotEvaluate("{}: not UTF-8 TOML ({})".format(label, type(exc).__name__)) from None


def load_registry(raw, label):
    """The set of (suite id, check id) pairs a registry's bytes hold, or CannotEvaluate."""
    if raw is None:
        raise CannotEvaluate("{}: {} is absent".format(label, REGISTRY))
    document = parse_toml(raw, label)
    if document.get("format-version") != 1 or isinstance(document.get("format-version"), bool):
        raise CannotEvaluate("{}: format-version must be the integer 1".format(label))
    suites = document.get("suite")
    if not isinstance(suites, list) or not suites:
        raise CannotEvaluate("{}: no [[suite]] rows".format(label))
    pairs = set()
    for row in suites:
        if not isinstance(row, dict) or not _text(row.get("id")) or not isinstance(
                row.get("expected-check-ids"), list):
            raise CannotEvaluate("{}: a [[suite]] row without an id and an expected-check-ids list".format(label))
        for check_id in row["expected-check-ids"]:
            if not _text(check_id):
                raise CannotEvaluate("{}: suite {!r} lists a check id that is not a string".format(label, row["id"]))
            pairs.add((row["id"], check_id))
    return pairs


def load_record(raw, label):
    """{(suite id, check id): entry} for an accounting record's bytes ({} when absent), or CannotEvaluate."""
    if raw is None:
        return {}
    document = parse_toml(raw, label)
    if set(document) - {"schema", "removal"} or document.get("schema") != 1 or isinstance(
            document.get("schema"), bool):
        raise CannotEvaluate("{}: the record holds schema = 1 and [[removal]] entries only".format(label))
    rows = document.get("removal", [])
    if not isinstance(rows, list):
        raise CannotEvaluate("{}: removal is not an array of tables".format(label))
    entries = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - ENTRY_KEYS:
            raise CannotEvaluate("{}: a [[removal]] entry with an unknown key".format(label))
        state = row.get("state")
        if not _text(row.get("suite")) or not _text(row.get("id")) or state not in STATES:
            raise CannotEvaluate("{}: a [[removal]] entry needs suite, id and a state of {}".format(
                label, ", ".join(STATES)))
        if set(row) - {"suite", "id", "state"} != set(STATES[state]) or not all(
                _text(row[key]) for key in STATES[state]):
            raise CannotEvaluate("{}: a {} entry for {!r} carries exactly {}, each non-blank".format(
                label, state, row["id"], ", ".join(STATES[state])))
        key = (row["suite"], row["id"])
        if key in entries:
            raise CannotEvaluate("{}: {!r} in suite {!r} is named twice".format(label, row["id"], row["suite"]))
        entries[key] = row
    return entries


def net_removed(base_pairs, head_pairs):
    """The pairs registered at the base and not at the head."""
    return base_pairs - head_pairs


def survives(entry, head_pairs):
    """Whether a covered entry's covered-by pair is registered at the head."""
    return (entry["covered-by-suite"], entry["covered-by-id"]) in head_pairs


def fresh(entry, base_entry):
    """Whether this change wrote the entry: the base record does not hold it unchanged."""
    return entry != base_entry


def judge(base_registry, head_registry, base_record, head_record):
    """(code, lines) for the four blobs (bytes, or None when absent) of a base and a head; CannotEvaluate when
    any cannot be read."""
    base_pairs = set() if base_registry is None else load_registry(base_registry, "base")
    # absent at both ends, nothing was registered and nothing is removed; absent at the head only, the whole
    # registry went, which this gate cannot account for pair by pair
    head_pairs = set() if base_registry is None and head_registry is None else load_registry(head_registry, "head")
    base_entries, head_entries = load_record(base_record, "base"), load_record(head_record, "head")
    findings, accounted = [], []
    for suite, check_id in sorted(net_removed(base_pairs, head_pairs)):
        where = "{!r} (suite {!r})".format(check_id, suite)
        entry = head_entries.get((suite, check_id))
        if entry is None:
            findings.append("{} is removed from {} and {} names no accounting for it".format(where, REGISTRY, RECORD))
        elif not fresh(entry, base_entries.get((suite, check_id))):
            findings.append("{} is removed, but its {} entry is unchanged from the base: record the accounting with "
                            "this change".format(where, RECORD))
        elif entry["state"] == "covered" and not survives(entry, head_pairs):
            findings.append("{} is marked covered by {!r} (suite {!r}), which is not registered at the head".format(
                where, entry["covered-by-id"], entry["covered-by-suite"]))
        else:
            accounted.append("{} removed, accounted as {}".format(where, entry["state"]))
    if findings:
        return EXIT_REFUSE, ["FAIL: " + line for line in findings] + ["INFO: " + line for line in accounted]
    return EXIT_OK, ["INFO: " + line for line in accounted]


def blobs(root, tree):
    """(registry, record) bytes in a commit tree (None for an absent path or a None tree)."""
    if tree is None:
        return None, None
    return cut.tree_blob(root, tree, REGISTRY), cut.tree_blob(root, tree, RECORD)


def evaluate(root, protected=None, before=None):
    """(code, lines, binding) for the change at root, its base derived by check_release_cut.context()."""
    try:
        root, binding = cut.context(root, protected, before)
        base_registry, base_record = blobs(root, binding["base_tree"])
        head_registry, head_record = blobs(root, binding["head_tree"])
    except (cut.CannotEvaluate, OSError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("the base or the change cannot be read: {}".format(exc)) from None
    code, lines = judge(base_registry, head_registry, base_record, head_record)
    return code, lines, binding


def run(root, protected=None, before=None):
    try:
        code, lines, binding = evaluate(root, protected, before)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: selftest-removal: {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    for line in lines:
        print(line)
    span = "{}..{}".format(binding["base"] or "(root)", binding["head"])
    if code == EXIT_REFUSE:
        print("REFUSE: selftest-removal: {} removes registered self-test check ids without accounting; add a "
              "[[removal]] entry for each to {} in this change (covered by a surviving id, unreachable, or "
              "disclosed with the maintainer's authorization reference), or restore the id".format(span, RECORD))
    else:
        print("PASS: selftest-removal: {}: no unaccounted removal of a registered self-test check id".format(span))
    return code


# SELF-TEST: production code ends here. Every case asserts through the instrumented check() choke point, and
# the executed id set is reconciled against tools/selftest_checks.toml (suite selftest-removal-selftest) by
# tools/check_selftest_execution.py. The revert/ cases patch one part of this module's globals (no code is
# generated or evaluated) and require their fixture to flip.

SUITE_ID = "selftest-removal-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _registry(*suites):
    """Registry bytes for (suite id, (check id, ...)) rows."""
    return "format-version = 1\n".encode() + b"".join(
        '[[suite]]\nid = "{}"\nrunner = "tools/x.py"\nexpected-check-ids = [{}]\n'.format(
            suite, ", ".join('"{}"'.format(i) for i in ids)).encode() for suite, ids in suites)


def _record(*entries):
    """Record bytes for entries given as dicts of string fields."""
    return b"schema = 1\n" + b"".join(
        b"[[removal]]\n" + "".join('{} = "{}"\n'.format(k, v) for k, v in entry.items()).encode()
        for entry in entries)


BASE = _registry(("s", ("a", "b", "c")), ("t", ("x",)))
COVERED = {"suite": "s", "id": "c", "state": "covered", "covered-by-suite": "s", "covered-by-id": "a"}
UNREACHABLE = {"suite": "s", "id": "c", "state": "unreachable", "capability-removed": "change 1",
               "observed": "the path refuses"}
DISCLOSED = {"suite": "s", "id": "c", "state": "disclosed", "authorization": "decision 7"}
WITHOUT_C = _registry(("s", ("a", "b")), ("t", ("x",)))


def _code(*blobs_):
    try:
        return judge(*blobs_)[0]
    except CannotEvaluate:
        return EXIT_CANNOT_EVALUATE


@contextlib.contextmanager
def _patched(**replacements):
    names = globals()
    saved = {name: names[name] for name in replacements}
    names.update(replacements)
    try:
        yield
    finally:
        names.update(saved)


def _fixture_env():
    """The shared git fixture environment (tools/_git_fixture_env.py: no GIT_ variable, an empty scratch home,
    global and system config pinned to os.devnull), with a fixed identity and no CI or GitHub event variable,
    so the base derivation under test takes its local path."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                          GIT_COMMITTER_EMAIL="t@example.invalid", GIT_TERMINAL_PROMPT="0")
    for key in [key for key in env if key.startswith("GITHUB_") or key == "CI"]:
        del env[key]
    return env


def _git(repo, _env, *args):
    """A fixture git call in the shared fixture environment with the fixed identity (the CI and GitHub event
    variables of _fixture_env matter only to the code under test, so _env is not read here)."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                          GIT_COMMITTER_EMAIL="t@example.invalid", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=60)
    if result.returncode != 0:
        raise OSError("fixture git {} failed: {}".format(args[0], result.stderr.decode("utf-8", "replace")))
    return result.stdout.decode("utf-8").strip()


def _commit(repo, env, files, message):
    for rel, data in files.items():
        path = Path(repo) / rel
        if data is None:
            path.unlink()
            continue
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", message)
    return _git(repo, env, "rev-parse", "HEAD")


def _run_quiet(root, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return run(root, *args)


def _repo_cases(tmp):
    """The end-to-end cases: a fixture repository whose origin/HEAD names its protected line, so the base is
    derived by check_release_cut.context() exactly as a local run derives it."""
    env = _fixture_env()
    repo = Path(tmp) / "repo"
    os.makedirs(repo)
    _git(repo, env, "init", "-q", "-b", "main")
    base = _commit(repo, env, {REGISTRY: BASE}, "base")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, env, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, env, "switch", "-q", "-c", "feature")
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        _commit(repo, env, {REGISTRY: WITHOUT_C}, "drop c")
        check("repo/unaccounted-removal-refused", _run_quiet(repo) == EXIT_REFUSE)
        _commit(repo, env, {RECORD: _record(COVERED)}, "account for c")
        check("repo/accounted-removal-passes", _run_quiet(repo) == EXIT_OK)
        _commit(repo, env, {REGISTRY: None}, "drop the registry")
        check("repo/registry-absent-at-head-cannot-evaluate", _run_quiet(repo) == EXIT_CANNOT_EVALUATE)
        _git(repo, env, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        check("repo/base-underivable-cannot-evaluate", _run_quiet(repo) == EXIT_CANNOT_EVALUATE)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    plain = Path(tmp) / "plain"
    os.makedirs(plain)
    check("repo/not-a-repository-cannot-evaluate", _run_quiet(plain) == EXIT_CANNOT_EVALUATE)


def self_test():
    check("pass/no-removal", _code(BASE, BASE, None, None) == EXIT_OK)
    check("pass/addition-only", _code(BASE, _registry(("s", ("a", "b", "c", "d")), ("t", ("x",))), None, None)
          == EXIT_OK)
    check("pass/registry-absent-at-base", _code(None, BASE, None, None) == EXIT_OK)
    check("pass/registry-absent-at-both", _code(None, None, None, None) == EXIT_OK)
    check("refuse/unaccounted-removal", _code(BASE, WITHOUT_C, None, None) == EXIT_REFUSE)
    check("refuse/moved-suite-counts", _code(BASE, _registry(("s", ("a", "b")), ("t", ("x", "c"))), None, None)
          == EXIT_REFUSE)
    check("pass/covered-by-survivor", _code(BASE, WITHOUT_C, None, _record(COVERED)) == EXIT_OK)
    check("refuse/covered-by-removed", _code(BASE, _registry(("s", ("b",)), ("t", ("x",))), None,
                                             _record(COVERED)) == EXIT_REFUSE)
    check("refuse/covered-by-never-registered", _code(BASE, WITHOUT_C, None, _record(dict(COVERED, **{
        "covered-by-id": "never-registered"}))) == EXIT_REFUSE)
    check("refuse/covered-by-itself", _code(BASE, WITHOUT_C, None, _record(dict(COVERED, **{
        "covered-by-id": "c"}))) == EXIT_REFUSE)
    check("pass/unreachable", _code(BASE, WITHOUT_C, None, _record(UNREACHABLE)) == EXIT_OK)
    check("pass/disclosed", _code(BASE, WITHOUT_C, None, _record(DISCLOSED)) == EXIT_OK)
    check("refuse/entry-other-suite", _code(BASE, WITHOUT_C, None, _record(dict(DISCLOSED, suite="t")))
          == EXIT_REFUSE)
    check("refuse/entry-unchanged-from-base", _code(BASE, WITHOUT_C, _record(DISCLOSED), _record(DISCLOSED))
          == EXIT_REFUSE)
    check("pass/entry-rewritten-in-change", _code(BASE, WITHOUT_C, _record(DISCLOSED), _record(dict(
        DISCLOSED, authorization="decision 8"))) == EXIT_OK)
    check("refuse/one-of-two-unaccounted", _code(BASE, _registry(("s", ("a",)), ("t", ("x",))), None,
                                                 _record(DISCLOSED)) == EXIT_REFUSE)
    for name, registry in (("cannot/registry-absent-at-head", None),
                           ("cannot/registry-not-toml", b"format-version = 1\n[[suite\n"),
                           ("cannot/registry-not-utf8", b"format-version = 1\n# \xff\n"),
                           ("cannot/registry-format-version", BASE.replace(b"format-version = 1", b"format-version = 2")),
                           ("cannot/registry-ids-not-list", b'format-version = 1\n[[suite]]\nid = "s"\n'
                                                            b'expected-check-ids = "a"\n'),
                           ("cannot/registry-id-not-string", b'format-version = 1\n[[suite]]\nid = "s"\n'
                                                             b'expected-check-ids = [1]\n'),
                           ("cannot/registry-nested-too-deep", b"format-version = 1\nx = " + b"[" * 100000 + b"]"
                            * 100000 + b"\n")):
        check(name, _code(BASE, registry, None, None) == EXIT_CANNOT_EVALUATE)
    check("cannot/base-registry-malformed", _code(b"[[suite\n", BASE, None, None) == EXIT_CANNOT_EVALUATE)
    for name, record in (("cannot/record-unknown-state", _record(dict(DISCLOSED, state="waived"))),
                         ("cannot/record-missing-field", _record({"suite": "s", "id": "c", "state": "disclosed"})),
                         ("cannot/record-blank-field", _record(dict(DISCLOSED, authorization="  "))),
                         ("cannot/record-unknown-key", _record(dict(DISCLOSED, note="x"))),
                         ("cannot/record-other-state-field", _record(dict(DISCLOSED, observed="x"))),
                         ("cannot/record-repeated-pair", _record(DISCLOSED, DISCLOSED)),
                         ("cannot/record-schema", _record(DISCLOSED).replace(b"schema = 1", b"schema = 2")),
                         ("cannot/record-not-toml", b"schema = 1\n[[removal\n")):
        check(name, _code(BASE, WITHOUT_C, None, record) == EXIT_CANNOT_EVALUATE)
    check("cannot/record-malformed-without-removal", _code(BASE, BASE, None, b"schema = 3\n")
          == EXIT_CANNOT_EVALUATE)
    # each part patched out flips its fixture
    with _patched(net_removed=lambda base_pairs, head_pairs: set()):
        check("revert/net-removal-red", _code(BASE, WITHOUT_C, None, None) == EXIT_OK)
    with _patched(survives=lambda entry, head_pairs: True):
        check("revert/survivor-red", _code(BASE, WITHOUT_C, None, _record(dict(COVERED, **{
            "covered-by-id": "never-registered"}))) == EXIT_OK)
    with _patched(fresh=lambda entry, base_entry: True):
        check("revert/fresh-entry-red", _code(BASE, WITHOUT_C, _record(DISCLOSED), _record(DISCLOSED)) == EXIT_OK)
    with _patched(STATES=dict(STATES, waived=())):
        check("revert/closed-states-red", _code(BASE, WITHOUT_C, None, _record(
            {"suite": "s", "id": "c", "state": "waived"})) == EXIT_OK)
    with _patched(load_registry=lambda raw, label: set()):
        check("revert/registry-fail-closed-red", _code(BASE, None, None, None) == EXIT_OK)
    tmp = tempfile.mkdtemp(prefix="aiqt-selftest-removal-")
    try:
        _repo_cases(tmp)
    except (OSError, subprocess.SubprocessError) as exc:
        FAILURES.append("repo fixture: {}".format(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    got = [_main_exit(argv) for argv in (["--bogus"], ["--root"], ["--self-test", "--root", "x"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2, 2], got)
    if FAILURES:
        print("SELF-TEST FAIL: check_selftest_removal {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_selftest_removal {} checks (no removal, an addition and a registry new at the "
          "base pass; an unaccounted removal, a move between suites, a stale entry, an entry for another suite and "
          "a covered-by pair that does not survive refuse; covered, unreachable and disclosed entries pass; a "
          "registry or record that is absent at the head, not TOML, not UTF-8 or misshapen is cannot-evaluate; "
          "the base is derived by check_release_cut.context() in a fixture repository, and an underivable base "
          "is cannot-evaluate; each part patched out flips its fixture)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def main(argv):
    if argv[-2:-1] == ["--execution-report"] and os.path.isabs(argv[-1]) and argv[:-2] in ([], ["--self-test"]):
        sys.path.append(str(Path(__file__).resolve().parent))
        import _selftest_exit_report
        _selftest_exit_report.arm(argv[-1], SUITE_ID, EXECUTED)
        return self_test()
    if argv == ["--self-test"]:
        return self_test()
    options, args = {}, list(argv)
    while args:
        arg = args.pop(0)
        if arg in ("--root", "--protected", "--base") and args and arg not in options:
            options[arg] = args.pop(0)
        else:
            print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
    return run(options.get("--root", os.getcwd()), options.get("--protected"), options.get("--base"))


if __name__ == "__main__":
    _code_ = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code_)
