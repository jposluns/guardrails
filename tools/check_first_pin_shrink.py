#!/usr/bin/env python3
"""First-pin shrink gate: neither obligation list of the first-pin demonstration loses a member relative
to the merge base with main, unless the change carries a recorded maintainer authorization.

tools/gen_first_pin_demo.py DERIVES delivered-prefix-obligations and floor-profile-obligations in
.aiqt/release/first-pin-demonstration.toml from AGENTS.md: a rule is listed only while its whole span
lies inside the first check_release_build.CAP_BYTES bytes. Lengthening an early rule can push a later
rule past the cap, and regeneration then drops it from both lists while the drift gate sees a fresh
build and check_release_build checks only that the lists are consistent. tools/check_must_deliver.py
already catches such a shrink of any rule its own list (.aiqt/core/must-deliver.toml) names, because
that rule then ends past the cap; it does not block a change that also removes the rule's row from
that list. This gate is the second layer, for a shrink that also removes the must-deliver row: it
compares the two lists at the merge base with the lists in the working tree and fails when either
list has lost a member.

Base. The base is the unique merge base of HEAD and the main ref (default origin/main, or --main REF),
read from git objects only: every git call carries --no-replace-objects, so a replacement ref cannot
substitute another object for the base demonstration, and runs with every GIT_ variable removed from
its environment and GIT_GRAFT_FILE pinned to os.devnull, so neither an ambient variable nor a graft
file can redirect the repository or rewrite the history merge-base walks. A main ref or HEAD that does
not resolve, more than one merge base, a demonstration absent at the base, and a base demonstration
that does not parse to the exact key set are each cannot-evaluate (exit 2), never "nothing to compare".

Authorization. A removal is accepted only through a [[reduction]] row in .aiqt/core/first-pin-reductions.toml,
a reviewed in-tree file. Each row names the list, the corpus-id removed, and the agents-sha256 of the
demonstration it authorizes (so it covers one exact AGENTS.md, never a later one), plus an
authorization table of exactly authorizer, utc and reason: the same sub-table tools/pin.py records
for an authorized rollback, carve-out or un-adopt. The file is a required input: absent, unreadable,
symlinked, non-UTF-8, malformed or outside that schema is exit 2.

DISCLOSED RESIDUAL (disclose-guard-residuals): the gate checks that an authorization row is present and
well formed, not who wrote it; that the row is the maintainer's is established by the review of the
change that adds it. A list member renamed is read as a removal plus an addition. A merge base equal
to HEAD (a push to main) compares the working tree with HEAD itself, so the gate bites on the change
that reaches main, not after it. The working-tree demonstration is trusted to be the fresh build the
first-pin drift gate holds; this gate does not rebuild it.

  check_first_pin_shrink.py [--main REF]   check the working tree against the merge base with REF
  check_first_pin_shrink.py --self-test    build synthetic git trees and assert each vector
  check_first_pin_shrink.py [--self-test] --execution-report ABS_PATH
                                           the self-test, also writing its execution report, finalized
                                           at interpreter exit (tools/_selftest_exit_report.py)

Exit convention: 0 no list lost a member, or every removal is authorized; 1 an unauthorized removal;
2 an unreadable, malformed or unresolvable input (fail-closed).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_first_pin_shrink.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_rules import CID_RE  # noqa: E402
# The consumer's contract, single-sourced: the demonstration path and its exact key set.
from check_release_build import FIRST_PIN_DEMO_KEYS, FIRST_PIN_DEMO_REL  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO_REL = FIRST_PIN_DEMO_REL
AUTH_REL = ".aiqt/core/first-pin-reductions.toml"
LIST_KEYS = ("delivered-prefix-obligations", "floor-profile-obligations")
DEFAULT_MAIN = "origin/main"
AUTH_TOP_REQUIRED = frozenset({"format-version"})
AUTH_TOP_KEYS = frozenset({"format-version", "reduction"})
ROW_KEYS = frozenset({"list", "id", "agents-sha256", "authorization"})
AUTHORIZATION_KEYS = frozenset({"authorizer", "utc", "reason"})
SHA_RE = re.compile(r"\A[0-9a-f]{64}\Z")
OID_RE = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
UTC_RE = re.compile(r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
# The global options every git call carries; a module attribute so the self-test can show the
# replacement-ref vector red with the option removed.
GIT_BASE_ARGS = ("--no-replace-objects",)
GIT_TIMEOUT = 30


class CannotEvaluate(Exception):
    """An input the gate cannot read, parse or resolve; run() reports it as exit 2."""


def _clean_env():
    """os.environ with every GIT_ variable removed, then GIT_GRAFT_FILE pinned to os.devnull: an
    ambient GIT_DIR, GIT_INDEX_FILE or GIT_OBJECT_DIRECTORY cannot point the read at another repository,
    and no graft file can rewrite the parents merge-base walks (--no-replace-objects covers
    refs/replace/* only, not grafts)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_GRAFT_FILE"] = os.devnull
    return env


def _git(root, args):
    """stdout bytes of `git <GIT_BASE_ARGS> -C root <args>`. A launch failure, a timeout or a non-zero
    exit is CannotEvaluate, never an empty result."""
    try:
        proc = subprocess.run(["git", *GIT_BASE_ARGS, "-C", str(root), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=GIT_TIMEOUT, env=_clean_env())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise CannotEvaluate("git {} could not run ({})".format(args[0], type(exc).__name__))
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CannotEvaluate("git {} exited {} ({})".format(
            args[0], proc.returncode, detail[0] if detail else "no message"))
    return proc.stdout


def _oid(data, what):
    text = data.decode("ascii", "replace").strip()
    if not OID_RE.match(text):
        raise CannotEvaluate("{} did not resolve to an object id".format(what))
    return text


def merge_base(root, main_ref):
    """The unique merge base of HEAD and main_ref, as an object id."""
    head = _oid(_git(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]), "HEAD")
    try:
        main = _oid(_git(root, ["rev-parse", "--verify", "--quiet", main_ref + "^{commit}"]),
                    "the main ref {!r}".format(main_ref))
    except CannotEvaluate as exc:
        raise CannotEvaluate("the main ref {!r} does not resolve to a commit ({})".format(main_ref, exc))
    try:
        lines = _git(root, ["merge-base", "--all", head, main]).decode("ascii", "replace").split()
    except CannotEvaluate as exc:
        # git merge-base exits 1 with no output when the two commits share no history.
        raise CannotEvaluate("no merge base of HEAD and {} could be computed ({})".format(main_ref, exc))
    if len(lines) != 1:
        raise CannotEvaluate("HEAD and {} have {} merge bases; exactly one is required".format(
            main_ref, len(lines)))
    return _oid(lines[0].encode("ascii"), "the merge base")


def _read_base_bytes(root, base):
    """The demonstration blob at the base commit. Absent there is CannotEvaluate: the list it would
    hold cannot be compared."""
    listed = _git(root, ["ls-tree", "--name-only", base, "--", DEMO_REL]).decode("utf-8", "replace")
    if listed.strip() != DEMO_REL:
        raise CannotEvaluate("{} is absent at the merge base {}".format(DEMO_REL, base))
    return _git(root, ["cat-file", "blob", "{}:{}".format(base, DEMO_REL)])


def _read_tree_bytes(path):
    """The one read of a working-tree input, a module attribute so the self-test can inject a
    PermissionError on every runner."""
    return path.read_bytes()


def _read_regular(path, rel):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise CannotEvaluate("{} does not exist".format(rel))
    except OSError as exc:
        raise CannotEvaluate("cannot stat {} ({})".format(rel, exc))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("{} is not a regular file (a symlink, directory or special entry)".format(rel))
    try:
        return _read_tree_bytes(path)
    except OSError as exc:
        raise CannotEvaluate("{} is unreadable ({})".format(rel, exc))


def _toml(raw, where):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{} is not valid UTF-8 ({})".format(where, exc))
    try:
        return tomllib.loads(text)
    # ValueError and RecursionError too: tomllib raises a bare ValueError on an integer literal past
    # the int-string digit limit and a RecursionError on deep nesting; neither is a TOMLDecodeError.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{} does not parse ({})".format(where, exc))


def parse_demo(raw, where):
    """The validated demonstration: exactly FIRST_PIN_DEMO_KEYS, a 64-hex agents-sha256, and each list
    a list of distinct well-formed corpus-ids."""
    data = _toml(raw, where)
    if set(data) != FIRST_PIN_DEMO_KEYS:
        raise CannotEvaluate("{} must carry exactly the keys {}, found {}".format(
            where, sorted(FIRST_PIN_DEMO_KEYS), sorted(data)))
    if not isinstance(data["agents-sha256"], str) or not SHA_RE.match(data["agents-sha256"]):
        raise CannotEvaluate("{}: agents-sha256 is not a 64-hex digest".format(where))
    for key in LIST_KEYS:
        ids = data[key]
        if not isinstance(ids, list) or not all(isinstance(i, str) and CID_RE.match(i) for i in ids):
            raise CannotEvaluate("{}: {} must be a list of corpus-ids".format(where, key))
        if len(set(ids)) != len(ids):
            raise CannotEvaluate("{}: {} lists an id more than once".format(where, key))
    return data


def load_base(root, main_ref):
    """The validated demonstration at the merge base of HEAD and main_ref."""
    base = merge_base(root, main_ref)
    return base, parse_demo(_read_base_bytes(root, base), "{} at the merge base {}".format(DEMO_REL, base))


def _utc_ok(value):
    if not isinstance(value, str) or not UTC_RE.match(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def read_authorizations(root):
    """The set of (list, id, agents-sha256) the reviewed reductions file authorizes."""
    data = _toml(_read_regular(root / AUTH_REL, AUTH_REL), AUTH_REL)
    if not AUTH_TOP_REQUIRED <= set(data) <= AUTH_TOP_KEYS:
        raise CannotEvaluate("{} must carry format-version and optionally reduction, found {}".format(
            AUTH_REL, sorted(data)))
    version = data["format-version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1, found {!r}".format(AUTH_REL, version))
    rows = data.get("reduction", [])
    if not isinstance(rows, list):
        raise CannotEvaluate("{}: reduction must be an array of [[reduction]] tables".format(AUTH_REL))
    granted = set()
    for index, row in enumerate(rows, 1):
        where = "{} reduction row {}".format(AUTH_REL, index)
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise CannotEvaluate("{} must carry exactly the keys {}, found {!r}".format(
                where, sorted(ROW_KEYS), sorted(row) if isinstance(row, dict) else row))
        if row["list"] not in LIST_KEYS:
            raise CannotEvaluate("{}: list must be one of {}, found {!r}".format(where, LIST_KEYS, row["list"]))
        if not isinstance(row["id"], str) or not CID_RE.match(row["id"]):
            raise CannotEvaluate("{}: id {!r} is not a well-formed corpus-id".format(where, row["id"]))
        if not isinstance(row["agents-sha256"], str) or not SHA_RE.match(row["agents-sha256"]):
            raise CannotEvaluate("{}: agents-sha256 is not a 64-hex digest".format(where))
        auth = row["authorization"]
        if not isinstance(auth, dict) or set(auth) != AUTHORIZATION_KEYS:
            raise CannotEvaluate("{}: authorization must carry exactly {}".format(
                where, sorted(AUTHORIZATION_KEYS)))
        for key in ("authorizer", "reason"):
            if not isinstance(auth[key], str) or not auth[key].strip():
                raise CannotEvaluate("{}: authorization.{} must be a non-empty string".format(where, key))
        if not _utc_ok(auth["utc"]):
            raise CannotEvaluate("{}: authorization.utc must be a UTC time YYYY-MM-DDTHH:MM:SSZ, found "
                                 "{!r}".format(where, auth["utc"]))
        entry = (row["list"], row["id"], row["agents-sha256"])
        if entry in granted:
            raise CannotEvaluate("{}: repeats an earlier row".format(where))
        granted.add(entry)
    return granted


def removed_members(base, head):
    """Per list, the base members absent from the head list, in base order."""
    return {key: [rid for rid in base[key] if rid not in set(head[key])] for key in LIST_KEYS}


def authorized(granted, key, rid, agents_sha):
    """True when a reductions row names this list, this id and the head demonstration's digest."""
    return (key, rid, agents_sha) in granted


def run(root, main_ref):
    """Exit 0 when neither list lost a member or every removal is authorized, 1 on an unauthorized
    removal, 2 on an input that cannot be read or resolved. Parameterized on root so the self-test
    drives it against synthetic repositories, never the real tree."""
    try:
        head = parse_demo(_read_regular(root / DEMO_REL, DEMO_REL), DEMO_REL)
        granted = read_authorizations(root)
        base_commit, base = load_base(root, main_ref)
    except CannotEvaluate as exc:
        print("error: {}; cannot evaluate, fail-closed".format(exc), file=sys.stderr)
        return 2
    removed = removed_members(base, head)
    findings, covered = [], []
    for key in LIST_KEYS:
        for rid in removed[key]:
            (covered if authorized(granted, key, rid, head["agents-sha256"]) else findings).append((key, rid))
    print("first-pin-shrink: base {} ({}); {}".format(base_commit, main_ref, ", ".join(
        "{} {} -> {}".format(key, len(base[key]), len(head[key])) for key in LIST_KEYS)))
    for key, rid in covered:
        print("  authorized removal: {} from {} ({})".format(rid, key, AUTH_REL))
    for key, rid in findings:
        print("first-pin-shrink: FAIL: {} left {} with no authorization row in {} for agents-sha256 {}".format(
            rid, key, AUTH_REL, head["agents-sha256"]))
    if findings:
        return 1
    print("first-pin-shrink: no unauthorized removal")
    return 0


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    argv = list(argv)
    # The execution gate launches the self-test as `--execution-report ABS_PATH` (its registry row carries
    # no args); `--self-test --execution-report ABS_PATH` is accepted too. Either form needs an absolute path.
    report_path = None
    if len(argv) == 3 and argv[0] == "--self-test" and argv[1] == "--execution-report" \
            and os.path.isabs(argv[2]):
        report_path, argv = argv[2], ["--self-test"]
    elif len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        report_path, argv = argv[1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return self_test()
    if argv == []:
        return run(ROOT, DEFAULT_MAIN)
    if len(argv) == 2 and argv[0] == "--main" and argv[1] and not argv[1].startswith("-"):
        return run(ROOT, argv[1])
    print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
    return 2


# --- self-test ------------------------------------------------------------------------------------
# Synthetic git repositories only, never the real tree; every fixture git call runs under
# _git_fixture_env.fixture_git_lifecycle (hermetic HOME, config and maintenance pins). The only external
# executable reached is git, isolated that way: no credentials, no remote, writes inside the fixture.
# Each fixture commits a base demonstration on main, then edits the working tree, and run() compares.
# Vectors (each outcome-changing rule is also shown red with that rule reverted by attribute patching):
#   shrink/unauthorized-fails       a member removed from both lists, no row: exit 1 naming both;
#   shrink/authorized-passes        the same with one row per list bound to the head digest: exit 0;
#   shrink/other-digest-fails       rows bound to another digest authorize nothing: exit 1;
#   shrink/one-list-only-fails      a row for the delivered list only leaves the floor removal: exit 1;
#   unchanged/passes, grown/passes  no member lost: exit 0;
#   base/* cannot-evaluate          an unresolvable main ref, no merge base, a base without the
#                                   demonstration, and a base demonstration that does not parse: exit 2;
#   base/replace-ref-ignored        a replacement ref swapping the base blob for the shrunk list is not
#                                   read, so the removal still fails (exit 1);
#   head/*, auth/*                  an unreadable or malformed working-tree input: exit 2;
#   argv/unrecognized               a bad argument list: exit 2.

SUITE_ID = "first-pin-shrink-selftest"
EXECUTED = []
FAILURES = []
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_FULL = ["prjint1", "ruleaa", "rulebb", "rulecc"]


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _demo_text(ids, sha=_SHA_A):
    def _list(key):
        return ["{} = [".format(key)] + ['  "{}",'.format(i) for i in ids] + ["]"]
    return "\n".join(['agents-sha256 = "{}"'.format(sha)] + _list(LIST_KEYS[0]) + _list(LIST_KEYS[1])) + "\n"


def _auth_text(rows):
    lines = ["format-version = 1"]
    for key, rid, sha in rows:
        lines += ["", "[[reduction]]", 'list = "{}"'.format(key), 'id = "{}"'.format(rid),
                  'agents-sha256 = "{}"'.format(sha),
                  'authorization = {{ authorizer = "maintainer", utc = "2026-11-08T12:00:00Z", '
                  'reason = "rule {} moved past the cap" }}'.format(rid)]
    return "\n".join(lines) + "\n"


def _fx(root, args):
    proc = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=GIT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError("self-test git {} failed: {}".format(args[0], proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "replace").strip()


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp, base_demo=None, base_raw=None):
    """A fixture repository whose main branch holds one commit with the base demonstration (or the raw
    base_raw text, or no demonstration when both are None) and an empty reductions file."""
    root = Path(tmp) / "repo"
    root.mkdir()
    _fx(root, ["init", "-q", "-b", "main"])
    _fx(root, ["config", "user.name", "fixture"])
    _fx(root, ["config", "user.email", "fixture@example.invalid"])
    if base_demo is not None or base_raw is not None:
        _write(root, DEMO_REL, base_raw if base_raw is not None else _demo_text(base_demo))
    _write(root, AUTH_REL, _auth_text([]))
    _fx(root, ["add", "-A"])
    _fx(root, ["commit", "-q", "-m", "base"])
    _fx(root, ["checkout", "-q", "-b", "work"])
    return root


def _quiet(fn, *args):
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(*args)
    return code, out.getvalue() + err.getvalue()


def _case(head_ids, rows=(), base_ids=None, main_ref="main", head_sha=_SHA_A):
    import tempfile
    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp, _FULL if base_ids is None else base_ids)
        _write(root, DEMO_REL, _demo_text(head_ids, head_sha))
        _write(root, AUTH_REL, _auth_text(rows))
        return _quiet(run, root, main_ref)


def _self_test_isolated():
    # Each reverted rule is a module function or constant swapped by its constant name and restored in
    # a finally block (attribute patching only, never generated code).
    import tempfile
    shrunk = ["prjint1", "ruleaa", "rulecc"]
    both = [(LIST_KEYS[0], "rulebb", _SHA_A), (LIST_KEYS[1], "rulebb", _SHA_A)]
    saved_removed, saved_authorized = removed_members, authorized

    code, out = _case(shrunk)
    check("shrink/unauthorized-fails", code == 1 and out.count("FAIL: rulebb left") == 2, (code, out))
    globals()["removed_members"] = lambda base, head: {key: [] for key in LIST_KEYS}
    try:
        code, out = _case(shrunk)
    finally:
        globals()["removed_members"] = saved_removed
    check("revert/shrink-rule-removed-red", code == 0, (code, out))

    code, out = _case(shrunk, both)
    check("shrink/authorized-passes", code == 0 and out.count("authorized removal: rulebb") == 2, (code, out))
    globals()["authorized"] = lambda granted, key, rid, sha: False
    try:
        code, out = _case(shrunk, both)
    finally:
        globals()["authorized"] = saved_authorized
    check("revert/authorization-rule-removed-red", code == 1, (code, out))

    other = [(key, rid, _SHA_B) for key, rid, _sha in both]
    code, out = _case(shrunk, other)
    check("shrink/other-digest-fails", code == 1 and out.count("FAIL: rulebb left") == 2, (code, out))
    globals()["authorized"] = lambda granted, key, rid, sha: any(g[:2] == (key, rid) for g in granted)
    try:
        code, out = _case(shrunk, other)
    finally:
        globals()["authorized"] = saved_authorized
    check("revert/digest-binding-removed-red", code == 0, (code, out))

    code, out = _case(shrunk, both[:1])
    check("shrink/one-list-only-fails", code == 1 and out.count("FAIL: rulebb left") == 1
          and "left {}".format(LIST_KEYS[1]) in out, (code, out))

    code, out = _case(list(_FULL))
    check("unchanged/passes", code == 0, (code, out))
    code, out = _case(_FULL + ["ruledd"])
    check("grown/passes", code == 0, (code, out))
    globals()["removed_members"] = lambda base, head: {
        key: (list(base[key]) if base[key] != head[key] else []) for key in LIST_KEYS}
    try:
        code, out = _case(_FULL + ["ruledd"])
    finally:
        globals()["removed_members"] = saved_removed
    check("revert/only-removals-count-red", code == 1, (code, out))

    code, out = _case(shrunk, main_ref="no-such-ref")
    check("base/unresolvable-main-cannot-evaluate", code == 2 and "no-such-ref" in out, (code, out))
    empty = {"agents-sha256": _SHA_A, LIST_KEYS[0]: [], LIST_KEYS[1]: []}
    saved_load_base = load_base
    globals()["load_base"] = lambda root, ref: ("0" * 40, empty)
    try:
        code, out = _case(shrunk, main_ref="no-such-ref")
    finally:
        globals()["load_base"] = saved_load_base
    check("revert/base-fail-closed-removed-red", code == 0, (code, out))

    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp, _FULL)
        _fx(root, ["checkout", "-q", "--orphan", "island"])
        _write(root, DEMO_REL, _demo_text(shrunk))
        _fx(root, ["add", "-A"])
        _fx(root, ["commit", "-q", "-m", "unrelated root"])
        code, out = _quiet(run, root, "main")
    check("base/no-merge-base-cannot-evaluate", code == 2 and "no merge base of HEAD" in out, (code, out))

    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp)
        _write(root, DEMO_REL, _demo_text(shrunk))
        code, out = _quiet(run, root, "main")
    check("base/absent-at-base-cannot-evaluate", code == 2 and "absent at the merge base" in out, (code, out))

    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp, base_raw="agents-sha256 = [\n")
        _write(root, DEMO_REL, _demo_text(shrunk))
        code, out = _quiet(run, root, "main")
    check("base/malformed-cannot-evaluate", code == 2 and "does not parse" in out, (code, out))

    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp, _FULL)
        _write(root, DEMO_REL, _demo_text(shrunk))
        blob = _fx(root, ["rev-parse", "main:" + DEMO_REL])
        fake = _fx(root, ["hash-object", "-w", str(root / DEMO_REL)])
        _fx(root, ["replace", blob, fake])
        code, out = _quiet(run, root, "main")
        check("base/replace-ref-ignored", code == 1 and out.count("FAIL: rulebb left") == 2, (code, out))
        saved_args = GIT_BASE_ARGS
        globals()["GIT_BASE_ARGS"] = ()
        try:
            code, out = _quiet(run, root, "main")
        finally:
            globals()["GIT_BASE_ARGS"] = saved_args
        check("revert/replace-objects-flag-removed-red", code == 0, (code, out))

    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    saved_read = _read_tree_bytes
    globals()["_read_tree_bytes"] = _deny
    try:
        code, out = _case(shrunk)
    finally:
        globals()["_read_tree_bytes"] = saved_read
    check("head/unreadable-cannot-evaluate", code == 2 and "unreadable" in out, (code, out))
    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp, _FULL)
        _write(root, DEMO_REL, _demo_text(shrunk).replace("agents-sha256", "agents-digest"))
        code, out = _quiet(run, root, "main")
    check("head/wrong-keys-cannot-evaluate", code == 2 and "exactly the keys" in out, (code, out))

    with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
        root = _repo(tmp, _FULL)
        (root / AUTH_REL).unlink()
        code, out = _quiet(run, root, "main")
    check("auth/absent-cannot-evaluate", code == 2 and "does not exist" in out, (code, out))
    good = _auth_text(both)
    for name, text, needle in (
            ("auth/unknown-row-key-cannot-evaluate", good.replace('list = "', 'note = "x"\nlist = "', 1),
             "exactly the keys"),
            ("auth/unknown-list-cannot-evaluate", good.replace('list = "delivered', 'list = "deliverd', 1),
             "list must be one of"),
            ("auth/empty-authorizer-cannot-evaluate", good.replace('authorizer = "maintainer"', 'authorizer = " "', 1),
             "authorization.authorizer"),
            ("auth/bad-utc-cannot-evaluate", good.replace("2026-11-08T12:00:00Z", "2026-13-08T12:00:00Z", 1),
             "authorization.utc"),
            ("auth/repeated-row-cannot-evaluate", good + good.split("\n", 1)[1], "repeats an earlier row"),
            ("auth/format-version-cannot-evaluate", good.replace("format-version = 1", "format-version = 1.0"),
             "format-version")):
        with tempfile.TemporaryDirectory(prefix="first-pin-shrink-selftest-") as tmp:
            root = _repo(tmp, _FULL)
            _write(root, DEMO_REL, _demo_text(shrunk))
            _write(root, AUTH_REL, text)
            code, out = _quiet(run, root, "main")
        check(name, code == 2 and needle in out, (code, out))

    got = [_quiet(main, argv)[0]
           for argv in (["--bogus"], ["--main"], ["--main", "-x"], ["--self-test", "--bogus"])]
    check("argv/unrecognized", got == [2, 2, 2, 2], got)

    if FAILURES:
        print("SELF-TEST FAIL: check_first_pin_shrink {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_first_pin_shrink {} checks (an unauthorized removal from either list fails "
          "and the same removal authorized per list for the head digest passes; another digest or one list "
          "only does not authorize; unchanged and grown lists pass; an unresolvable main ref, no merge base, "
          "a base without the demonstration and a malformed base are exit 2; a replacement ref is not read; "
          "unreadable or malformed working-tree inputs and bad arguments are exit 2; each outcome-changing "
          "rule is red with it reverted)".format(len(EXECUTED)))
    return 0


def self_test():
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle():
        return _self_test_isolated()


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate reads the exit status of every
# git child it starts, in its live run and in its self-test, so under an ignored SIGCHLD a failing git
# would read as a success; the guard runs at the entry line, before main() dispatches either path.
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
    sys.exit(_child_status_refusal_exit("check_first_pin_shrink.py") or main())
