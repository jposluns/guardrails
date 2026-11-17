#!/usr/bin/env python3
"""Must-deliver prefix gate: every rule listed in .aiqt/core/must-deliver.toml stays WHOLE inside the
first check_release_build.CAP_BYTES bytes of AGENTS.md.

The first-pin demonstration (tools/gen_first_pin_demo.py) DERIVES its delivered list from AGENTS.md, so
that list shrinks silently when the rules grow and every byte-identity gate stays green: a rule can fall
out of the delivered prefix with no check failing. This gate closes that gap for the rules a maintainer
has declared must stay delivered. The list is hand-edited policy, like .aiqt/core/instruction-budget.toml:
additions may be proposed in any PR and stay free; a removal must be declared (see LIST RATCHET below).

LIST RATCHET. The list is compared with the list at a comparison base: --base REF, or with no flag the
merge base of HEAD and origin/main (no base that resolves is exit 2, never "nothing to compare"). The
base file is read from the base commit through the same strict parser as the working file; a base
commit without it, or with it as a symlink or malformed, is exit 2. A rule listed at the base and no
longer listed is exit 1 naming it, unless a [[removal]] row (exactly id and reason) names it; each
such removal is printed with its reason. A [[removal]] row that is duplicated, also listed in [[rule]],
has an empty reason or carries an unknown key is exit 2. Removal rows may stay after the merge.

The spans come from gen_first_pin_demo.measure, the ONE span algorithm the demonstration generator also
uses: AGENTS.md must be byte-identical to a fresh composition of the rule corpus and the block registry
(a stale AGENTS.md is exit 2, never measured), offsets are UTF-8 bytes made absolute against the composed
rule region, and a rule is delivered only when its end offset (exclusive) is at most the cap. This gate
never reads the demonstration's own lists; it recomputes everything. The cap is read from
check_release_build.CAP_BYTES at call time, never copied here.

The declared file carries exactly format-version, warn-margin-bytes and [[rule]] rows of exactly id and
reason, and optionally [[removal]] rows of exactly id and reason; every listed id must be a corpus-id
with a measured span. An absent, unreadable, symlinked, non-UTF-8,
malformed or EMPTY list is exit 2 (a missing input is a failure, never "nothing to check"), per the
check-fails-closed-on-unreadable rule. The gate prints every listed rule's end offset and margin on every
run, and a WARNING (still exit 0) when the tightest margin drops under warn-margin-bytes, so growth is
visible before a listed rule falls out.

DISCLOSED RESIDUAL (disclose-guard-residuals): a PR that removes a rule from the list AND adds a
[[removal]] row for it passes this gate (the row turns a silent deletion into a named, reasoned record the
gate prints; review must judge the removal itself); the ratchet sees only what changed since the base it
is given, so it is as good as that base (a stale or wrong --base compares against the wrong list); a
listed rule cut down to a stub still passes (content is not judged);
the gate measures the repository file against the configured cap, not any platform's actual truncation
behaviour or a budget other instruction files may share; and a client that checks AGENTS.md out with CRLF
line endings inflates what a reader there receives, which this gate does not model (.gitattributes pins
AGENTS.md to eol=lf).

  check_must_deliver.py [--base REF]  check the live tree (default base: merge base with origin/main)
  check_must_deliver.py --self-test   build synthetic trees and assert each vector is discriminated

Exit convention: 0 every listed rule is delivered and no rule left the list undeclared; 1 a listed rule
ends past the cap, or a rule listed at the base is no longer listed with no [[removal]] row; 2 an
unreadable, malformed or ambiguous input, or a comparison base that does not resolve (fail-closed).
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
import subprocess
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from gen_rules import CID_RE  # noqa: E402
from gen_first_pin_demo import measure, DemoError  # noqa: E402  the ONE span algorithm, shared
import check_release_build  # noqa: E402  the cap, read as check_release_build.CAP_BYTES at call time

DECL_REL = ".aiqt/core/must-deliver.toml"
TOP_KEYS = frozenset({"format-version", "warn-margin-bytes", "rule"})
OPTIONAL_TOP_KEYS = frozenset({"removal"})
ROW_KEYS = frozenset({"id", "reason"})
REMOVAL_KEYS = frozenset({"id", "reason"})
DEFAULT_BASE_REMOTE = "origin/main"
GIT_TIMEOUT = 60  # seconds per git call; a call that does not finish is exit 2, never a pass


def _read_decl_bytes(path):
    """The one read of the declaration's bytes, a module attribute so the self-test can inject a
    PermissionError here on every runner, including one where a mode-000 file stays readable."""
    return path.read_bytes()


def read_declared(path):
    """The validated must-deliver list: ([rule ids], warn_margin_bytes, {removed id: reason}). Absent, a
    symlink or non-regular entry, unreadable, not UTF-8, not TOML, or outside the exact schema is a
    DemoError (exit 2): the list is the gate's whole policy input, so nothing else may stand in for it."""
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
    return parse_declared(raw, DECL_REL)


def parse_declared(raw, label):
    """The one strict parser of the declaration's bytes, shared by the working file and the file at the
    comparison base (label names which in every message). Returns ([rule ids], warn_margin_bytes,
    {removed id: reason}); anything outside the exact schema is a DemoError (exit 2)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DemoError("{} is not valid UTF-8 ({})".format(label, exc))
    try:
        data = tomllib.loads(text)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table; neither is a TOMLDecodeError.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise DemoError("{} does not parse ({})".format(label, exc))
    if not TOP_KEYS <= set(data) or not set(data) <= TOP_KEYS | OPTIONAL_TOP_KEYS:
        raise DemoError("{} must carry exactly the keys {} (and optionally {}), found {}".format(
            label, sorted(TOP_KEYS), sorted(OPTIONAL_TOP_KEYS), sorted(data)))
    version = data["format-version"]
    # The type is checked before the value: TOML 1.0 parses to a float that compares equal to 1, and a
    # bool is an int subclass.
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise DemoError("{}: format-version must be the integer 1, found {!r}".format(label, version))
    warn = data["warn-margin-bytes"]
    if isinstance(warn, bool) or not isinstance(warn, int) or warn < 1:
        raise DemoError("{}: warn-margin-bytes must be a positive integer, found {!r}".format(label, warn))
    rows = data["rule"]
    if not isinstance(rows, list) or not rows:
        raise DemoError("{}: rule must be a non-empty array of [[rule]] tables; an empty list is a "
                        "failure, never \"nothing to check\"".format(label))
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise DemoError("{}: each [[rule]] row must carry exactly the keys {}, found {!r}".format(
                label, sorted(ROW_KEYS), row))
        rid, reason = row["id"], row["reason"]
        if not isinstance(rid, str) or not CID_RE.match(rid):
            raise DemoError("{}: id {!r} is not a well-formed corpus-id".format(label, rid))
        if not isinstance(reason, str) or not reason.strip():
            raise DemoError("{}: rule {} must carry a non-empty reason string".format(label, rid))
        if rid in ids:
            raise DemoError("{}: rule {} is listed more than once".format(label, rid))
        ids.append(rid)
    removals = {}
    if "removal" in data:
        removal_rows = data["removal"]
        if not isinstance(removal_rows, list) or not removal_rows:
            raise DemoError("{}: removal must be a non-empty array of [[removal]] tables (omit the key "
                            "when no removal is recorded)".format(label))
        for row in removal_rows:
            if not isinstance(row, dict) or set(row) != REMOVAL_KEYS:
                raise DemoError("{}: each [[removal]] row must carry exactly the keys {}, found {!r}".format(
                    label, sorted(REMOVAL_KEYS), row))
            rid, reason = row["id"], row["reason"]
            if not isinstance(rid, str) or not CID_RE.match(rid):
                raise DemoError("{}: removal id {!r} is not a well-formed corpus-id".format(label, rid))
            if not isinstance(reason, str) or not reason.strip():
                raise DemoError("{}: removal {} must carry a non-empty reason string".format(label, rid))
            if rid in removals:
                raise DemoError("{}: removal {} is recorded more than once".format(label, rid))
            if rid in ids:
                raise DemoError("{}: removal {} is also listed in [[rule]]; a rule is either listed or "
                                "removed".format(label, rid))
            removals[rid] = reason
    return ids, warn, removals


# --- the comparison base (every git return code is checked; a nonzero exit is never an empty result) --

def _git_env():
    """The ambient environment without any GIT_-prefixed variable, so a GIT_DIR, GIT_INDEX_FILE or
    GIT_OBJECT_DIRECTORY set by a hook or a wrapper cannot point the comparison at another repository,
    with replace refs off so a replacement object cannot stand in for the base commit."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _git(root, args):
    """Run git -C root with args; returns the CompletedProcess (bytes). A missing git binary or a call
    past GIT_TIMEOUT is a DemoError (exit 2): the comparison cannot run."""
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=_git_env(),
                              timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DemoError("git {} could not run ({})".format(args[0], exc))


def _git_err(proc):
    return proc.stderr.decode("utf-8", "replace").strip() or "exit {}".format(proc.returncode)


def _object_id(text):
    """text as a full hexadecimal object id (SHA-1 or SHA-256), or None."""
    oid = text.strip()
    if len(oid) in (40, 64) and all(c in "0123456789abcdef" for c in oid):
        return oid
    return None


def resolve_base(root, base):
    """(commit, label) of the comparison base: the explicit ref, or with base None the merge base of
    HEAD and origin/main. A base that does not resolve to one commit is a DemoError (exit 2), never
    "nothing to compare"."""
    if base is None:
        proc = _git(root, ["merge-base", "HEAD", DEFAULT_BASE_REMOTE])
        commit = _object_id(proc.stdout.decode("ascii", "replace")) if proc.returncode == 0 else None
        if commit is None:
            raise DemoError("no comparison base resolves: git merge-base HEAD {} failed ({}); pass "
                            "--base REF or fetch {}".format(DEFAULT_BASE_REMOTE, _git_err(proc),
                                                            DEFAULT_BASE_REMOTE))
        return commit, "merge base of HEAD and {} ({})".format(DEFAULT_BASE_REMOTE, commit[:12])
    if not base or base.startswith("-"):
        raise DemoError("--base {!r} is not a ref".format(base))
    proc = _git(root, ["rev-parse", "--verify", "--quiet", base + "^{commit}"])
    commit = _object_id(proc.stdout.decode("ascii", "replace")) if proc.returncode == 0 else None
    if commit is None:
        raise DemoError("cannot resolve the comparison base {!r} to a commit".format(base))
    return commit, "{} ({})".format(base, commit[:12])


def read_base_declared(root, commit, label):
    """The declaration as listed at the base commit, read through parse_declared. A base commit without
    the file, or with it as anything but a regular blob (a symlink, a tree, a submodule), is a DemoError
    (exit 2): the base list is the ratchet's whole reference, so its absence is never an empty list."""
    where = "{} at the base {}".format(DECL_REL, label)
    proc = _git(root, ["ls-tree", "-z", "--full-tree", commit, "--", DECL_REL])
    if proc.returncode != 0:
        raise DemoError("git ls-tree failed for {} ({})".format(where, _git_err(proc)))
    entries = [e for e in proc.stdout.split(b"\0") if e]
    if not entries:
        raise DemoError("{} does not exist; the list at the base cannot be read".format(where))
    meta, _tab, path = entries[0].partition(b"\t")
    fields = meta.split()
    if (len(entries) != 1 or path != DECL_REL.encode("ascii") or len(fields) != 3
            or fields[0] not in (b"100644", b"100755") or fields[1] != b"blob"):
        raise DemoError("{} is not a regular file (a symlink, directory or other entry)".format(where))
    proc = _git(root, ["cat-file", "blob", fields[2].decode("ascii", "replace")])
    if proc.returncode != 0:
        raise DemoError("{} is unreadable ({})".format(where, _git_err(proc)))
    return parse_declared(proc.stdout, where)


def ratchet(root, base):
    """Compare the working list with the list at the comparison base. Exit 0 when every rule listed at
    the base is still listed or named by a [[removal]] row, 1 when one is neither (named), 2 when the
    base does not resolve or either list cannot be read. Additions are free."""
    try:
        ids, _warn, removals = read_declared(root / DECL_REL)
        commit, label = resolve_base(root, base)
        base_ids, _base_warn, _base_removals = read_base_declared(root, commit, label)
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    dropped = [rid for rid in base_ids if rid not in ids]
    undeclared = [rid for rid in dropped if rid not in removals]
    added = [rid for rid in ids if rid not in base_ids]
    print("must-deliver: list ratchet against base {}: {} listed there, {} listed now ({} added, {} "
          "removed)".format(label, len(base_ids), len(ids), len(added), len(dropped)))
    for rid in dropped:
        if rid in removals:
            print("must-deliver: removal: rule {} left the list since the base; [[removal]] reason: {}".format(
                rid, removals[rid]))
    for rid in sorted(removals):
        if rid not in dropped:
            print("must-deliver: removal row kept: rule {} (not listed at the base); reason: {}".format(
                rid, removals[rid]))
    for rid in undeclared:
        print("must-deliver: FAIL: rule {} is listed at the base {} and is no longer listed, with no "
              "[[removal]] row naming it".format(rid, label))
    return 1 if undeclared else 0


def gate(root, base):
    """The whole gate: the prefix check (run) and the list ratchet, both always run so every finding is
    printed. The worse exit wins: 2 over 1 over 0."""
    return max(run(root), ratchet(root, base))


def run(root):
    """Check the tree at root. Exit 0 when every listed rule's whole span lies inside the cap, 1 when a
    listed rule ends past it, 2 on an unreadable, malformed or ambiguous input. Parameterized on root so
    the self-test drives it against synthetic trees, never the real repo."""
    try:
        ids, warn, _removals = read_declared(root / DECL_REL)
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
    base = None
    if len(args) == 2 and args[0] == "--base":
        base = args[1]
    elif len(args) == 1 and args[0].startswith("--base="):
        base = args[0][len("--base="):]
    elif args:
        print("error: unknown arguments {}; usage: check_must_deliver.py [--base REF] | --self-test".format(
            args), file=sys.stderr)
        return 2
    return gate(repo_root(), base)


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
# The list ratchet (T14 to T20) runs gate() against temporary git repositories built with the same
# helpers and committed through _git_fixture_env's hermetic environment:
#   T14 the base lists ruleaa and the working list drops it with no [[removal]] row: exit 1 naming ruleaa
#       (a mutant that never reads the base, comparing the working list with itself, passes);
#   T15 the same drop with a [[removal]] row naming ruleaa: exit 0, the removal and its reason printed (a
#       mutant that rejects every removal fails);
#   T16 a [[removal]] row with an empty reason, an extra key, a duplicate, or an id also in [[rule]]: each
#       exit 2 with its own message (a mutant without that one check exits 0);
#   T17 a --base that does not resolve, and no origin/main with no --base: each exit 2; with origin/main
#       present and no --base, the merge base is read and the drop is exit 1 (a mutant treating a missing
#       base as nothing to compare passes the first two);
#   T18 a base commit without the list, and one whose list is malformed: each exit 2 (a mutant treating an
#       absent or unreadable base list as nothing to compare passes);
#   T19 the working list adds an id the base does not list: exit 0 (a mutant failing on any difference
#       between the two lists fails);
#   T20 ruleaa grows past the cap, the working list drops it, and the first-pin demonstration is
#       regenerated without it: exit 1 naming ruleaa from the ratchet, with no prefix FAIL line (a mutant
#       comparing against the demonstration's delivered list instead of the base list passes).

import gen_first_pin_demo as gfpd  # noqa: E402  fixture helpers (_write_tree, _tuned) and CAP_BYTES


class _FixtureError(Exception):
    """A self-test fixture's own git command failed: the vector cannot run (exit 2, never a pass)."""


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
    from _git_fixture_env import git_fixture_env

    CAP = check_release_build.CAP_BYTES

    def capture(root):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = run(root)
        return code, buf.getvalue()

    def capture_gate(root, base):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = gate(root, base)
        return code, buf.getvalue()

    def fixture_git(root, *args):
        env = git_fixture_env(GIT_AUTHOR_NAME="must-deliver self-test",
                              GIT_AUTHOR_EMAIL="selftest@example.invalid",
                              GIT_COMMITTER_NAME="must-deliver self-test",
                              GIT_COMMITTER_EMAIL="selftest@example.invalid")
        try:
            proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env,
                                  timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _FixtureError("fixture git {} could not run ({})".format(args[0], exc))
        if proc.returncode != 0:
            raise _FixtureError("fixture git {} failed in {}: {}".format(args[0], root, _git_err(proc)))
        return proc.stdout.decode("utf-8", "replace").strip()

    def commit_base(root):
        """Commit the tree at root as the fixture's one commit; returns its object id."""
        fixture_git(root, "init", "-q")
        fixture_git(root, "add", "-A")
        fixture_git(root, "commit", "-q", "-m", "fixture base")
        return fixture_git(root, "rev-parse", "HEAD")

    def raw_decl(rules, removals=(), extra=""):
        lines = ["format-version = 1", "warn-margin-bytes = 512"]
        for rid in rules:
            lines += ["", "[[rule]]", 'id = "{}"'.format(rid), 'reason = "fixture rule"']
        for rid, reason in removals:
            lines += ["", "[[removal]]", 'id = "{}"'.format(rid), 'reason = "{}"'.format(reason)]
        return "\n".join(lines) + "\n" + extra

    failures = []
    skipped = []
    fixture_error = None
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

        # T4. Bytes, not characters: 100 U+00E9 in a before-rules block (a rule body is ASCII) put ruleaa's
        # character end inside the cap and its byte end past it (the generator's own multi-byte vector).
        t4 = tmp / "t4"
        two = "é"
        mb_block = ("OPF-SELFTEST", "AGENTS.md", "before-rules", 1, "opf/blocks/selftest.md", two * 100 + "\n")
        mb_registry = gfpd.adapter_compose.registry_text([("AGENTS.md", "composed")], [mb_block])

        def mb_specs(fill):
            return [("prjint1", True, 0), ("ruleaa", False, fill), ("rulebb", False, 100)]

        sp = gfpd._write_tree(t4, mb_specs(20000), mb_registry, [mb_block])
        sp = gfpd._write_tree(t4, mb_specs(20000 + CAP + 50 - sp["ruleaa"][1]), mb_registry, [mb_block])
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

        # The list ratchet. Each fixture commits a base listing prjint1 and ruleaa (ruleaa CAP - 10),
        # then edits the working list; the base is passed as its full object id unless stated.
        drop_msg = "FAIL: rule ruleaa is listed at the base"

        def ratchet_fixture(name, base_rules=("prjint1", "ruleaa")):
            root = tmp / name
            gfpd._tuned(root, base_specs, "ruleaa", CAP - 10)
            _decl(root, [], raw=raw_decl(base_rules))
            return root, commit_base(root)

        # T14. Dropped with no [[removal]] row: exit 1 naming ruleaa.
        t14, base14 = ratchet_fixture("t14")
        _decl(t14, [], raw=raw_decl(["prjint1"]))
        code, out = capture_gate(t14, base14)
        if code != 1 or drop_msg not in out:
            failures.append("T14: a rule dropped from the list with no [[removal]] row must be exit 1 "
                            "naming it, got {}\n{}".format(code, out))

        # T15. Dropped with a [[removal]] row: exit 0, the removal and its reason printed.
        t15, base15 = ratchet_fixture("t15")
        _decl(t15, [], raw=raw_decl(["prjint1"], [("ruleaa", "fixture removal reason")]))
        code, out = capture_gate(t15, base15)
        if (code != 0 or "removal: rule ruleaa left the list since the base; [[removal]] reason: "
                         "fixture removal reason" not in out):
            failures.append("T15: a drop with a [[removal]] row must be exit 0 printing the removal, got "
                            "{}\n{}".format(code, out))

        # T16. Malformed [[removal]] rows: each exit 2 with its own message.
        t16, base16 = ratchet_fixture("t16")
        bad_removals = (
            ("empty reason", raw_decl(["prjint1"], [("ruleaa", " ")]),
             "removal ruleaa must carry a non-empty reason"),
            ("extra key", raw_decl(["prjint1"], [("ruleaa", "x")], extra="approved = true\n"),
             "each [[removal]] row must carry exactly the keys"),
            ("duplicate", raw_decl(["prjint1"], [("ruleaa", "x"), ("ruleaa", "y")]),
             "removal ruleaa is recorded more than once"),
            ("also in [[rule]]", raw_decl(["prjint1", "ruleaa"], [("ruleaa", "x")]),
             "removal ruleaa is also listed in [[rule]]"),
        )
        for label, raw, want in bad_removals:
            _decl(t16, [], raw=raw)
            code, out = capture_gate(t16, base16)
            if code != 2 or want not in out:
                failures.append("T16 {}: expected exit 2 with {!r}, got {}\n{}".format(label, want, code, out))

        # T17. An unresolvable --base, and no origin/main with no --base: each exit 2. With origin/main
        # present and no --base, the merge base is the comparison base and the drop is caught.
        t17, base17 = ratchet_fixture("t17")
        _decl(t17, [], raw=raw_decl(["prjint1"]))
        code, out = capture_gate(t17, "no-such-ref")
        if code != 2 or "cannot resolve the comparison base 'no-such-ref'" not in out:
            failures.append("T17: an unresolvable --base must be exit 2, got {}\n{}".format(code, out))
        code, out = capture_gate(t17, None)
        if code != 2 or "no comparison base resolves" not in out:
            failures.append("T17: no origin/main and no --base must be exit 2, got {}\n{}".format(code, out))
        fixture_git(t17, "update-ref", "refs/remotes/origin/main", base17)
        code, out = capture_gate(t17, None)
        if code != 1 or drop_msg not in out or "merge base of HEAD and origin/main" not in out:
            failures.append("T17: with origin/main present the default base must catch the drop (exit 1), "
                            "got {}\n{}".format(code, out))

        # T18. A base commit without the list, and one whose list is malformed: each exit 2.
        t18 = tmp / "t18"
        gfpd._tuned(t18, base_specs, "ruleaa", CAP - 10)
        base18 = commit_base(t18)  # no must-deliver.toml at the base
        _decl(t18, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t18, base18)
        if code != 2 or "at the base" not in out or "does not exist" not in out:
            failures.append("T18: a base commit without the list must be exit 2, got {}\n{}".format(code, out))
        _decl(t18, [], raw="format-version = 1\nwarn-margin-bytes = 512\nrule = []\n")
        fixture_git(t18, "add", "-A")
        fixture_git(t18, "commit", "-q", "-m", "fixture malformed base")
        base18b = fixture_git(t18, "rev-parse", "HEAD")
        _decl(t18, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t18, base18b)
        if code != 2 or "at the base" not in out or "non-empty" not in out:
            failures.append("T18: a malformed list at the base must be exit 2, got {}\n{}".format(code, out))

        # T19. An addition is free: the base lists prjint1 only, the working list adds ruleaa.
        t19, base19 = ratchet_fixture("t19", base_rules=("prjint1",))
        _decl(t19, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t19, base19)
        if code != 0 or "(1 added, 0 removed)" not in out:
            failures.append("T19: adding a listed rule must be exit 0, got {}\n{}".format(code, out))

        # T20. ruleaa grows past the cap; the working list drops it and the demonstration is regenerated
        # without it, so every byte-identity surface agrees. Only the ratchet still fails, naming ruleaa.
        t20 = tmp / "t20"
        gfpd._tuned(t20, base_specs, "ruleaa", CAP - 10)
        demo20 = t20 / Path(gfpd.DEMO_REL)
        demo20.parent.mkdir(parents=True, exist_ok=True)
        demo20.write_text(gfpd.build(t20), encoding="utf-8")

        def demo_delivered():
            """The demonstration's delivered list, or None when it does not parse (a T20 failure)."""
            try:
                return tomllib.loads(demo20.read_text(encoding="utf-8"))["delivered-prefix-obligations"]
            except (ValueError, RecursionError, MemoryError, KeyError):
                return None

        delivered = demo_delivered()
        if delivered is None or "ruleaa" not in delivered:
            failures.append("T20: the base demonstration does not list ruleaa as delivered")
        _decl(t20, [], raw=raw_decl(["prjint1", "ruleaa"]))
        base20 = commit_base(t20)
        gfpd._tuned(t20, base_specs, "ruleaa", CAP + 50)
        demo20.write_text(gfpd.build(t20), encoding="utf-8")
        _decl(t20, [], raw=raw_decl(["prjint1"]))
        code, out = capture_gate(t20, base20)
        delivered = demo_delivered()
        if delivered is None or "ruleaa" in delivered:
            failures.append("T20: the regenerated demonstration still lists ruleaa as delivered")
        if code != 1 or drop_msg not in out or "FAIL: rule ruleaa ends at" in out:
            failures.append("T20: a rule dropped from the list together with a regenerated demonstration "
                            "must be exit 1 from the ratchet naming it, got {}\n{}".format(code, out))
    except _FixtureError as exc:
        fixture_error = str(exc)
    finally:
        for dirpath, _dirnames, filenames in os.walk(tmp):
            for fn in filenames:
                try:
                    os.chmod(os.path.join(dirpath, fn), 0o644)
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)

    if fixture_error is not None:
        print("SELF-TEST ERROR: a list-ratchet fixture could not be built ({}); cannot evaluate".format(
            fixture_error), file=sys.stderr)
        return 2
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
          "spans it measures; the margin report, the unlisted-rule line and the warn-margin WARNING "
          "(still exit 0) are printed exactly; and against a committed base, a rule dropped from the list "
          "with no [[removal]] row fails naming it (also with the demonstration regenerated without it), "
          "a declared removal passes and is printed, an addition passes, and a malformed removal row, an "
          "unresolvable or missing base and an absent or malformed base list fail closed (exit 2). "
          "{}".format(
              "SKIPPED on this runner: {}.".format("; ".join(skipped)) if skipped
              else "No case was skipped (the mode-000 list ran too)."))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The modules it imports hold child launch
# and wait calls, which that gate's import scan counts, so under an ignored SIGCHLD a failing child they
# start would read as a pass. The gate's list ratchet starts git children (the base reads) and its
# self-test starts git children too (the fixture commits), each judged by its exit status; the guard runs
# at the entry line, before main() dispatches either, so every launch on either path is covered.
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
