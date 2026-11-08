#!/usr/bin/env python3
"""Site fast-lane scope decision for the Quality workflow.

The maintainer's recorded decision (2026-11-03) authorizes skipping the Quality workflow's heavy
jobs when, and only when, the run is a pull request whose head is a merge-train-* branch of this
repository and every path it changes is in the site allowlist: under site/ or opf/site/, or one
of the three files a site-only change regenerates (.aiqt/manifest.toml, .aiqt/release/root.txt,
.aiqt/release/announce-snippet.txt). This tool makes that decision and nothing else; the scope
job of .github/workflows/quality.yml runs the base tree's copy of this tool (git
--no-replace-objects show HEAD^1:tools/ci_site_scope.py into the runner's temporary directory,
as that job's first step after checkout and Python setup, before any step that runs the tested
tree's code), so editing this file inside a train changes the decision only after that edit
merges; a same-repository train can still edit the workflow itself, which no pull_request
workflow covers, so this ordering is merge-lane discipline, not isolation from a hostile train.
The Quality gates job passes a skipped heavy job only when this decision read true and the run
is such a train.

The decision is a pure function over the changed-path list, fail-closed: an EMPTY list, any path
outside the allowlist, a path that is not a plain repo-relative file path (absolute, empty, parent
climbing, a backslash, a trailing slash, or not a string), or any error reading the diff decides
false, the full suite. The allowlisted trees admit only paths strictly below them, so the bare
directory names "site" and "site/" decide false. A rename out of an allowlisted tree is caught
because the diff is read with rename detection off (--no-renames): both sides of a rename appear
as a delete and an add, and the non-site side decides false.

The CLI reads the changed paths from git against the merge base it is given with:
git --no-replace-objects diff --name-only --no-renames -z <merge-base> HEAD.
It prints exactly one line, site_only=true or site_only=false, and exits 0 only on a clean
decision; it exits 2 on cannot-evaluate (a bad revision, a git failure, undecodable path bytes, a
missing git), which the workflow treats as site_only=false.

The scope job runs --ci-decide: it reads EVENT_NAME, HEAD_REF, HEAD_REPO, BASE_REPO, HEAD_SHA
and GITHUB_OUTPUT from the environment, appends site_only=true or site_only=false to
GITHUB_OUTPUT, and exits 0. Any event but pull_request, a head ref that is not a merge-train-*
branch, a head repository that is empty or not the base repository, a checked-out HEAD whose
raw commit object does not record exactly two parents or whose second parent is not HEAD_SHA (the
pull request's head commit), and any cannot-evaluate from the parent read or the diff read, each
writes false, so the full suite runs. The parents are read from HEAD's raw commit object (git
--no-replace-objects cat-file commit HEAD, parsing the header's parent lines), and the diff is
read against the first of them, HEAD^1 as that object records it, with replacement objects
disabled on both reads, so no replace ref can redirect a read the decision rests on, and an
info/grafts file cannot either (the raw object read ignores grafts, and the diff names the
first parent by its full id); the scope job's extraction of this file (HEAD^1:...) disables
replace refs only, so under a grafts file that revision can differ from the one the diff is read
against, and a fresh CI checkout has no grafts file. GitHub
documents the pull_request checkout as a test merge commit (refs/pull/N/merge) whose parents are
the base-branch tip the merge was built on and the pull request's head (documented behaviour, not
verified here), and requiring exactly two parents, the second equal to HEAD_SHA, checks that shape
instead of assuming it, so HEAD^1..HEAD is read as the tested tree's change over its base only
when the checkout really is such a merge.
The repository read is the process working directory, because the scope job runs this file from
outside the tree; this entry imports nothing beside the standard library, so the one extracted
file runs alone. It exits nonzero only when it cannot write the decision at all, which fails the
scope job and so fails Quality gates (fail-closed, never a silent fast lane).

This tool also carries the CI selection plan in SHADOW MODE: a pure select(paths, registry)
function over the changed-path list and a parsed tools/ci_checks.toml registry, which maps each
changed path to the registry steps whose triggers claim it and derives a per-job run/skip plan.
The plan is advisory only: nothing reads it, no output records it, and no job is skipped or
added by it. The scope job extracts the BASE tree's registry (git --no-replace-objects show
HEAD^1:tools/ci_checks.toml, the same base-copy discipline as the decision tool itself, inside
the same decision step, so it still runs before any step that runs the tested tree's code) and
runs --ci-select --shadow, which prints the plan to the log and appends it to the step summary
and always exits 0: a malformed or missing base registry, any cannot-evaluate, and any event
that is not a merge-train pull request each print a cannot-evaluate or full-run plan and never
fail or change the run. Selection is fail-closed toward the full run: an empty diff, a path
shape that is not plain repo-relative, a changed CI or selector file (FULL_RUN_PATHS), a path
no trigger claims, and a path claimed only by universal triggers (a trigger that matches it by
its depth alone, every segment ** or any name: **, **/*, */**, and a bounded-depth wildcard
such as * or */* for paths at its own depth) each decide the full run.
Trigger globs match per path segment (** spans whole segments and needs at least one; * and ?
never cross a /), exactly as tools/check_ci_selection.py declares them. --explain --base REV
prints the same plan locally, over the diff against REV and the working tree's registry.

Usage:
  ci_site_scope.py --merge-base REV        decide against REV, print site_only=true|false
  ci_site_scope.py --ci-decide             the scope job entry (environment-driven, above)
  ci_site_scope.py --ci-select --shadow    the scope job's advisory selection plan (above)
  ci_site_scope.py --explain --base REV    print the selection plan against REV locally
  ci_site_scope.py --self-test             the self-test
  ci_site_scope.py --execution-report ABS  the self-test, also writing the executed check ids
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: ci_site_scope.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: ci_site_scope.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

# No further import here: the scope job extracts this file ALONE from the base tree and runs
# --ci-decide outside the repository, so the decision path must need nothing beside the standard
# library. The self-test's exit-report and fixture-environment siblings are imported lazily
# (_exit_report, _git_env), never on the --ci-decide path.


def _exit_report():
    """The self-test's exit-report sibling, imported only when a self-test entry runs."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _selftest_exit_report
    return _selftest_exit_report


ROOT = Path(__file__).resolve().parents[1]
EXIT_OK, EXIT_CANNOT_EVALUATE = 0, 2
GIT_TIMEOUT = 60

# The site allowlist: the two site trees, strictly below each prefix, and the exact files a
# site-only change regenerates. Module globals, so the self-test's revert cases can patch each
# rule out and observe its vector flip.
SITE_TREES = ("site/", "opf/site/")
SITE_REGENERATED = frozenset({
    ".aiqt/manifest.toml",
    ".aiqt/release/root.txt",
    ".aiqt/release/announce-snippet.txt",
})
# Site-tree files a suite that can be SKIPPED reads as a verdict input, enumerated by the
# gate-discipline coverage account recorded with this change: opf/tools/check_opf_homes.py (live
# gate and self-test, the opf-tooling-a job and the git-fixture-env config lanes) reads the five
# OPF homes contract pages and has no always-run sibling, so those five exclusions are
# load-bearing. tools/check_opf_declaration.py (live gate and self-test) reads
# opf/site/tooling.html as a declaration surface AND walks the whole tree decoding every .md and
# .html file (restating_documents), site pages included; because of that whole-tree walk, the
# opf-declaration live gate and its self-test also run in the always-run site job, so a
# site-only change that turns them red fails every lane (the execution-reconciled registration
# stays in the selftest-execution job), and the tooling.html exclusion is retained as a
# deliberate conservative margin, not because an always-run read is missing.
# tools/check_python_floor.py reads site/development.html, site/install.html, opf/site/adopt.md
# and opf/site/adopt.html; its live gate runs in the always-run registry job in every run and in
# two of the three config lanes (the malformed lane excludes the bare launch, which stays in the
# combined and system lanes), so those pages need no exclusion either. What the fast lane
# deliberately gives up for a site-only train is every check in the eleven skipped jobs
# (opf-tooling-a, opf-tooling-b, opf-tooling-c, selftest-execution, git-fixture-env-core,
# opf-host-patch, the four git-fixture-env host-patch jobs and byte-canon-matrix): every step of
# those jobs is skipped, and of their commands only the child-status probe, the opf-declaration
# live gate and its selftest-execution suite, and check_byte_canon.py (live gate and self-test)
# also run, as the same command, in an always-run job. Every other command in them runs nowhere
# else in CI on such a train: the OPF tooling live gates and self-tests of the three
# opf-tooling jobs, the opf.py self-test, check_selftest_execution.py --self-test and every
# other selftest-execution suite those jobs run, check_entry_guard.py,
# check_child_status_coverage.py, every git-fixture-env suite, and check_python_host_pin.py
# beyond the registry job's --jobs-only run. It also gives up the skipped jobs' host,
# configuration and platform repetitions: the host-patch jobs' exact host Python, the config
# lanes' hostile git configurations, and byte-canon-matrix's platforms.
SITE_HEAVY_READ = frozenset({
    "opf/site/adopt.html",
    "opf/site/adopt.md",
    "opf/site/core-files.html",
    "opf/site/disclosure.html",
    "opf/site/record-indexes.html",
    "opf/site/tooling.html",
})
REQUIRE_NONEMPTY = True
# The merge lane's train branches: the fast lane engages only for a pull request whose head is
# such a branch of this repository, so a labelled non-train or fork pull request never earns a
# passing skip. The bare prefix itself ("merge-train-") counts as a train head, matching the
# heavy jobs' startsWith(github.head_ref, 'merge-train-') condition.
TRAIN_PREFIX = "merge-train-"
# The parent count --ci-decide requires of the checked-out commit's raw object: on a
# pull_request event the checkout is the tested two-parent merge commit, whose first parent is
# the base-branch tip it was built on and whose second must be exactly the pull request's head
# commit (HEAD_SHA). Any other count, zero, one, or three and more, is not that merge, so the
# decision is cannot-evaluate, false (module docstring). --ci-decide diffs against the first
# parent that object records.
MERGE_PARENT_COUNT = 2
# Every git read the decision rests on (head_parents, changed_paths) runs with
# --no-replace-objects, so a replace ref can neither swap HEAD's parent list nor the trees the
# diff compares.
HEX_DIGITS = frozenset(b"0123456789abcdef")

# --- CI selection in shadow mode (module docstring, SHADOW MODE) ---
# Module globals, so the self-test's revert cases can patch each rule out and observe its
# vector flip. Every rule is fail-closed toward the full run; none of them, and nothing else
# in this mode, reads or writes the site_only decision above.
# A change to CI control or to the selection machinery itself always decides the full run.
FULL_RUN_PATHS = (
    ".github/**",
    ".gitattributes",
    "tools/ci_checks.toml",
    "tools/ci_site_scope.py",
    "tools/check_ci_selection.py",
    "tools/check_ci_parity.py",
    "tools/selftest_checks.toml",
    "tools/run_all_checks.sh",
)
# A path matched by no trigger of any entry decides the full run.
REQUIRE_CLAIMED = True
# A path whose every matching trigger is universal (_universal_glob: a trigger that matches it by
# depth alone, such as **, **/*, */**, or a bounded-depth * or */* at its own depth) decides the
# full run: no entry claims it specifically, so the mapping for it is effectively undeclared.
REQUIRE_SPECIFIC_CLAIM = True
# An entry nobody has reviewed is always selected, whatever its triggers say.
ALWAYS_RUN_UNREVIEWED = True
REGISTRY_MAX_BYTES = 4 * 1024 * 1024
SHADOW_HEADER = ("ci-select shadow plan (advisory: nothing reads it, and no job is skipped "
                 "or added by it)")


def _segment_match(part, name):
    """Whether one path segment matches one glob segment of * and ?: the greedy match that
    returns to the last * on a mismatch, so * and ? never cross a / (the caller splits on it)."""
    p = n = 0
    star, mark = -1, 0
    while n < len(name):
        if p < len(part) and part[p] == "*":
            star, mark = p, n
            p += 1
        elif p < len(part) and (part[p] == "?" or part[p] == name[n]):
            p += 1
            n += 1
        elif star >= 0:
            p, mark = star + 1, mark + 1
            n = mark
        else:
            return False
    while p < len(part) and part[p] == "*":
        p += 1
    return p == len(part)


def _glob_match(glob, path):
    """Whether path fully matches a segment glob: ** spans whole segments (a trailing ** needs
    at least one), * and ? stay inside one segment. The same semantics as
    tools/check_ci_selection.py's glob_match, restated here because this file is extracted and
    run alone (module docstring) and so can import no sibling."""
    names = path.split("/")
    parts = glob.split("/")
    reach = {0}
    for position, part in enumerate(parts):
        if part == "**":
            if position == len(parts) - 1:
                return any(start < len(names) for start in reach)
            nxt = set()
            for start in reach:
                end = start
                while True:
                    nxt.add(end)
                    if end >= len(names) or not names[end]:
                        break
                    end += 1
            reach = nxt
        else:
            reach = {start + 1 for start in reach
                     if start < len(names) and _segment_match(part, names[start])}
        if not reach:
            return False
    return len(names) in reach


def _segment_matches_any_name(part):
    """Whether one glob segment other than the ** token matches every segment name: it holds
    only * and ? characters, at least one *, and at most one ? (a ? needs exactly one character,
    and every name of a plain path has at least one), so ***, *? and ?* match any name and ?
    or ??* do not."""
    return bool(part) and set(part) <= {"*", "?"} and "*" in part and part.count("?") <= 1


def _universal_glob(glob):
    """Whether a trigger claims every path it matches by that path's depth alone, whatever the
    names, under _glob_match's meaning: every segment is ** or matches any name. select() asks
    this only of triggers that already match the path, so a True here means the trigger would
    match every other path at that depth too, and so says nothing specific about this one.
    ** itself, **/* and **/** match every plain path; */** misses only the top-level files,
    which it cannot claim at all. A bounded-depth wildcard is universal for paths at its own
    depth: * (or *** or *?) matches every top-level file and */* every two-segment path, so one
    such trigger in the registry would otherwise make every path at that depth read as
    specifically claimed. Judged by meaning, not by one spelling, so none of them passes as a
    specific claim; a segment with a literal character, or a ? that needs exactly one
    character, is specific."""
    return all(part == "**" or _segment_matches_any_name(part) for part in glob.split("/"))


def _glob_shape_error(glob):
    """Why a trigger string is not a repository-relative glob, or None (the shape rules of
    tools/check_ci_selection.py, restated for the extracted-alone copy)."""
    if not glob or glob.startswith("/") or "\\" in glob:
        return "is not a repository-relative glob"
    if any(segment in ("", ".", "..") for segment in glob.split("/")):
        return "has an empty, '.' or '..' segment"
    if any(ord(char) < 0x21 or ord(char) == 0x7F for char in glob):
        return "carries a space or control character"
    return None


def _registry_entries(registry):
    """((job, step, triggers, reviewed) rows, None) from a parsed registry, or (None, reason)
    when any field the selection reads is missing or malformed (fail-closed, cannot evaluate).
    Fields the selection does not read (categories, scope, the weekly keys) are left to the
    declaration gate, tools/check_ci_selection.py."""
    if not isinstance(registry, dict):
        return None, "the registry is not a table"
    version = registry.get("format-version")
    # type() first: True == 1 in Python, and a boolean is not the integer 1 TOML must carry.
    if type(version) is not int or version != 1:
        return None, "the registry's format-version is not the integer 1"
    checks = registry.get("check")
    if not isinstance(checks, list) or not checks:
        return None, "the registry's check is not a non-empty array of tables"
    rows = []
    for index, entry in enumerate(checks):
        label = "registry check #{}".format(index + 1)
        if not isinstance(entry, dict):
            return None, label + " is not a table"
        job, step = entry.get("job"), entry.get("step")
        if not isinstance(job, str) or not job.strip():
            return None, label + " has no job name"
        if not isinstance(step, str) or not step.strip():
            return None, label + " has no step name"
        triggers = entry.get("triggers")
        if not isinstance(triggers, list) or not all(isinstance(glob, str) for glob in triggers):
            return None, label + " triggers is not a list of strings"
        for glob in triggers:
            reason = _glob_shape_error(glob)
            if reason is not None:
                return None, "{} trigger {!r} {}".format(label, glob, reason)
        reviewed = entry.get("reviewed")
        if not isinstance(reviewed, bool):
            return None, label + " reviewed is not a boolean"
        rows.append((job, step, tuple(triggers), reviewed))
    return rows, None


def select(paths, registry):
    """The pure selection plan over the changed paths and a parsed registry (tools/
    ci_checks.toml as tomllib parses it): (plan, None), or (None, reason) when the inputs
    cannot be judged at all. The plan is a dict: "full_run" is None or the reason every job
    runs, "paths" is the tuple of paths decided over (a rename read with detection off records
    both sides here), and "jobs" maps each registry job, in first-appearance order, to
    {"run": bool, "why": str}. Fail-closed: an empty list, a path that is not a plain
    repo-relative file path, a FULL_RUN_PATHS match, an unclaimed path and a path claimed only
    by universal triggers (_universal_glob) each decide the full run; an unreviewed entry and
    an entry with no triggers are always selected. The site_only decision above is never read
    or written."""
    rows, problem = _registry_entries(registry)
    if problem is not None:
        return None, problem
    try:
        items = list(paths)
    except TypeError:
        return None, "the changed-path list is not iterable"
    jobs = {}
    for job, _step, _triggers, _reviewed in rows:
        jobs.setdefault(job, {"run": False, "why": ""})
    reason = None
    if not items:
        reason = "the diff is empty, so the change cannot be mapped to any trigger"
    else:
        for path in items:
            if not _plain_relative(path):
                reason = "{!r} is not a plain repo-relative file path".format(path)
                break
            if any(_glob_match(glob, path) for glob in FULL_RUN_PATHS):
                reason = "{} is CI control or selection machinery (FULL_RUN_PATHS)".format(path)
                break
            claims = [glob for _job, _step, triggers, _reviewed in rows
                      for glob in triggers if _glob_match(glob, path)]
            if REQUIRE_CLAIMED and not claims:
                reason = "{} matches no trigger of any registry entry".format(path)
                break
            if REQUIRE_SPECIFIC_CLAIM and claims and all(
                    _universal_glob(glob) for glob in claims):
                reason = ("{} is claimed only by the universal ** glob or an equivalent that "
                          "matches by depth alone".format(path))
                break
    if reason is not None:
        for job in jobs:
            jobs[job] = {"run": True, "why": "full run: " + reason}
        return {"full_run": reason, "paths": tuple(items), "jobs": jobs}, None
    for job, step, triggers, reviewed in rows:
        if jobs[job]["run"]:
            continue
        if ALWAYS_RUN_UNREVIEWED and not reviewed:
            jobs[job] = {"run": True,
                         "why": "step {!r} is unreviewed, so it always runs".format(step)}
            continue
        if not triggers:
            jobs[job] = {"run": True,
                         "why": "step {!r} declares no triggers, so it always runs".format(step)}
            continue
        hit = next(((glob, path) for glob in triggers for path in items
                    if _glob_match(glob, path)), None)
        if hit is not None:
            jobs[job] = {"run": True, "why": "trigger {!r} of step {!r} matches {!r}".format(
                hit[0], step, hit[1])}
    for job, verdict in jobs.items():
        if not verdict["run"]:
            verdict["why"] = "no trigger of its steps matches a changed path"
    return {"full_run": None, "paths": tuple(items), "jobs": jobs}, None


def plan_lines(plan):
    """The printable plan, deterministic over its input: the verdict line, one line per changed
    path (both sides of a rename appear, because the diff is read with rename detection off),
    and one line per job with its reason. The same text goes to the log and the step summary."""
    if plan["full_run"] is not None:
        lines = ["plan: full run ({})".format(plan["full_run"])]
    else:
        lines = ["plan: selective over {} changed path(s)".format(len(plan["paths"]))]
    for path in plan["paths"]:
        lines.append("  path {!r}".format(path))
    for job, verdict in plan["jobs"].items():
        lines.append("  job {}: {} ({})".format(
            job, "run" if verdict["run"] else "skip", verdict["why"]))
    return lines


def _registry_from_file(path):
    """(parsed registry, None) read from path, or (None, reason) on any read or parse failure,
    never an exception: the shadow plan must stay cannot-evaluate, not a crash."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(REGISTRY_MAX_BYTES + 1)
    except OSError as exc:
        return None, "cannot read the registry at {}: {}".format(path, exc)
    if len(data) > REGISTRY_MAX_BYTES:
        return None, "the registry at {} is larger than {} bytes".format(path, REGISTRY_MAX_BYTES)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "the registry at {} is not UTF-8: {}".format(path, exc)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError)
    # on an integer literal past CPython's int-string limit, and a RecursionError on a deeply
    # nested array or inline table; MemoryError on exhaustion. Each is cannot-evaluate here.
    try:
        return tomllib.loads(text), None
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "the registry at {} is not valid TOML: {}: {}".format(
            path, type(exc).__name__, exc)


def ci_select():
    """The scope job's shadow-selection entry: read the same environment as --ci-decide plus
    CHECKS_REGISTRY (the path the workflow extracted the BASE tree's tools/ci_checks.toml to),
    print the plan to the log, append it to GITHUB_STEP_SUMMARY when that is set, and ALWAYS
    exit 0: the plan is advisory, nothing reads it, GITHUB_OUTPUT is never written, and no
    failure here may fail or change the run. Any event that is not a merge-train pull request
    is a full-run plan; a missing or malformed registry, a head that is not the tested
    two-parent merge, and any diff-read error are each a cannot-evaluate plan. Like
    --ci-decide, this path must stay import-free beyond the standard library, because the
    extracted base copy runs alone."""
    lines = [SHADOW_HEADER]
    event = os.environ.get("EVENT_NAME", "")
    head_ref = os.environ.get("HEAD_REF", "")
    head_repo = os.environ.get("HEAD_REPO", "")
    base_repo = os.environ.get("BASE_REPO", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    registry_path = os.environ.get("CHECKS_REGISTRY", "")
    plan = None
    note = ""
    # A cannot-evaluate plan changes nothing: the jobs still run or skip by the site_only
    # decision --ci-decide already wrote, which a cannot-evaluate here does not touch (a
    # malformed or missing base registry leaves a site_only=true decision standing).
    unchanged = "the plan is advisory and the run follows site_only"
    if not train_head(event, head_ref, head_repo, base_repo):
        note = ("plan: full run (event {!r} with head ref {!r} of repository {!r} (base {!r}) "
                "is not a merge-train-* pull request of this repository)".format(
                    event, head_ref, head_repo, base_repo))
    elif not registry_path:
        note = "plan: cannot evaluate (CHECKS_REGISTRY is not set); {}".format(unchanged)
    else:
        parents, problem = merge_parents(os.getcwd())
        if problem is not None:
            note = "plan: cannot evaluate ({}); {}".format(problem, unchanged)
        elif not head_sha or parents[1] != head_sha:
            note = ("plan: cannot evaluate (the checked-out merge commit's second parent {!r} "
                    "is not the pull request's head commit {!r}); {}".format(
                        parents[1], head_sha, unchanged))
        else:
            paths, problem = changed_paths(os.getcwd(), parents[0])
            if problem is None:
                registry, problem = _registry_from_file(registry_path)
                if problem is None:
                    plan, problem = select(paths, registry)
            if problem is not None:
                note = "plan: cannot evaluate ({}); {}".format(problem, unchanged)
    if plan is not None:
        lines.extend(plan_lines(plan))
    else:
        lines.append(note)
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write("```\n" + text + "\n```\n")
        except OSError as exc:
            print("cannot append the plan to GITHUB_STEP_SUMMARY: {}".format(exc),
                  file=sys.stderr)
    return 0


def explain(root, base):
    """The local plan: read the changed paths against base (rename detection off, both sides
    recorded), read the WORKING TREE's tools/ci_checks.toml, and print the plan with every
    reason. Exits 0 on a clean plan, 2 on cannot-evaluate (a bad revision, a missing or
    malformed registry); unlike --ci-select --shadow this is a local query, so failing loudly
    is correct here."""
    paths, problem = changed_paths(root, base)
    if problem is None:
        registry, problem = _registry_from_file(
            str(Path(root) / "tools" / "ci_checks.toml"))
        if problem is None:
            plan, problem = select(paths, registry)
    if problem is not None:
        print("cannot evaluate: {}".format(problem), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    for line in plan_lines(plan):
        print(line)
    return EXIT_OK


def _plain_relative(path):
    """True only for a plain repo-relative file path: a non-empty str, no absolute or drive form,
    no backslash, no empty segment, and no . or .. segment."""
    if not isinstance(path, str) or not path:
        return False
    if path.startswith("/") or "\\" in path or ":" in path.split("/", 1)[0]:
        return False
    segments = path.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            return False
    return True


def _allowed(path):
    """True only for a path the site allowlist admits: strictly below one of SITE_TREES and not a
    file a heavy suite reads, or one of SITE_REGENERATED exactly."""
    if path in SITE_HEAVY_READ:
        return False
    for prefix in SITE_TREES:
        if path.startswith(prefix) and len(path) > len(prefix):
            return True
    return path in SITE_REGENERATED


def train_head(event, head_ref, head_repo, base_repo):
    """True only for a pull_request event whose head ref starts with TRAIN_PREFIX and whose head
    repository is non-empty and exactly the base repository. Anything else, a missing value
    included, is False (the full suite)."""
    if event != "pull_request":
        return False
    if not isinstance(head_ref, str) or not head_ref.startswith(TRAIN_PREFIX):
        return False
    if not isinstance(head_repo, str) or not head_repo:
        return False
    return head_repo == base_repo


def site_only(paths):
    """The pure decision: True only when paths is a non-empty list of plain repo-relative paths,
    every one admitted by the allowlist. Anything else decides False (the full suite)."""
    try:
        items = list(paths)
    except TypeError:
        return False
    if REQUIRE_NONEMPTY and not items:
        return False
    for path in items:
        if not _plain_relative(path):
            return False
        if not _allowed(path):
            return False
    return True


def changed_paths(root, merge_base):
    """(paths, None) read from git against merge_base, or (None, reason) on any error. Rename
    detection is off, so both sides of a rename appear; -z gives exact NUL-separated paths;
    replacement objects are disabled, so a replace ref cannot redirect either side."""
    if not isinstance(merge_base, str) or not merge_base or merge_base.startswith("-"):
        return None, "merge base must be a non-empty revision, not an option"
    argv = ["git", "--no-replace-objects", "-C", str(root), "diff", "--name-only",
            "--no-renames", "-z", merge_base, "HEAD", "--"]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "cannot run git: {}".format(exc)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, "git diff exited {}: {}".format(proc.returncode, tail[-1] if tail else "")
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "undecodable path bytes in the diff: {}".format(exc)
    return [item for item in text.split("\0") if item], None


def _object_id(value):
    """True only for a full lowercase hexadecimal object id (SHA-1 or SHA-256 length)."""
    return len(value) in (40, 64) and all(byte in HEX_DIGITS for byte in value)


def head_parents(root):
    """(HEAD's parent ids, in order, as its raw commit object records them, None), or (None,
    reason) when git cannot answer or the object's header cannot be parsed. The object is read
    with replacement objects disabled, and its parents are the parent lines that directly follow
    the tree line, exactly where git writes them; a parent line anywhere else in the header, a
    malformed id, or a header with no end is unparseable, never read as a smaller list."""
    argv = ["git", "--no-replace-objects", "-C", str(root), "cat-file", "commit", "HEAD"]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "cannot run git: {}".format(exc)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, "git cat-file commit HEAD exited {}: {}".format(
            proc.returncode, tail[-1] if tail else "")
    header, separator, _ = proc.stdout.partition(b"\n\n")
    if not separator:
        return None, "HEAD's commit object has no header end, so its parents cannot be read"
    lines = header.split(b"\n")
    if not lines[0].startswith(b"tree "):
        return None, "HEAD's commit object does not begin with a tree line, so it is unparseable"
    parents = []
    index = 1
    while index < len(lines) and lines[index].startswith(b"parent "):
        value = lines[index][len(b"parent "):]
        if not _object_id(value):
            return None, "HEAD's commit object has a malformed parent line"
        parents.append(value.decode("ascii"))
        index += 1
    for line in lines[index:]:
        if line.startswith(b"parent "):
            return None, ("HEAD's commit object has a parent line outside the parent block, so "
                          "its parents cannot be read unambiguously")
    return parents, None


def merge_parents(root):
    """((first parent, second parent), None) only when HEAD's raw commit object records exactly
    MERGE_PARENT_COUNT parents; otherwise (None, reason): git could not answer, the header is
    unparseable, or the count is not two, and each says which. The caller treats every reason
    as cannot-evaluate."""
    parents, problem = head_parents(root)
    if problem is not None:
        return None, problem
    if len(parents) != MERGE_PARENT_COUNT:
        if not parents:
            shape = "HEAD has no parent (a root commit)"
        elif len(parents) == 1:
            shape = "HEAD has one parent (a linear commit)"
        else:
            shape = "HEAD has {} parents, not exactly two".format(len(parents))
        return None, shape + ", so the checkout is not the tested two-parent merge commit"
    return (parents[0], parents[1]), None


def decide(root, merge_base):
    """The CLI decision: print site_only=true|false; exit 0 on a clean decision, 2 otherwise."""
    paths, problem = changed_paths(root, merge_base)
    if problem is not None:
        print("cannot evaluate: {}".format(problem), file=sys.stderr)
        print("site_only=false")
        return EXIT_CANNOT_EVALUATE
    print("site_only=true" if site_only(paths) else "site_only=false")
    return EXIT_OK


def ci_decide():
    """The scope job entry: read EVENT_NAME, HEAD_REF, HEAD_REPO, BASE_REPO, HEAD_SHA and
    GITHUB_OUTPUT from the environment, append site_only=true or site_only=false to
    GITHUB_OUTPUT, print the decision, exit 0. Any event but pull_request, a non-train or fork
    head (train_head), a HEAD that does not record exactly two parents or whose second parent is
    not HEAD_SHA (merge_parents), and any cannot-evaluate, each decides false (the full suite).
    The diff is read against the first parent HEAD records, in the
    process working directory, because the scope job runs the base tree's copy of this file from
    outside the tree (module docstring); this function must stay import-free beyond the standard
    library, because that copy is extracted alone. Exits 1 only when the decision cannot be
    written at all, which fails the scope job."""
    output = os.environ.get("GITHUB_OUTPUT", "")
    if not output:
        print("cannot write the decision: GITHUB_OUTPUT is not set", file=sys.stderr)
        return 1
    event = os.environ.get("EVENT_NAME", "")
    head_ref = os.environ.get("HEAD_REF", "")
    head_repo = os.environ.get("HEAD_REPO", "")
    base_repo = os.environ.get("BASE_REPO", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    verdict = False
    if not train_head(event, head_ref, head_repo, base_repo):
        note = ("event {!r} with head ref {!r} of repository {!r} (base {!r}) is not a "
                "merge-train-* pull request of this repository, so it never takes the fast "
                "lane".format(event, head_ref, head_repo, base_repo))
    else:
        parents, problem = merge_parents(os.getcwd())
        if problem is not None:
            note = "cannot evaluate, treated as false: {}".format(problem)
        elif not head_sha or parents[1] != head_sha:
            note = ("cannot evaluate, treated as false: the checked-out merge commit's second "
                    "parent {!r} is not the pull request's head commit {!r}".format(
                        parents[1], head_sha))
        else:
            paths, problem = changed_paths(os.getcwd(), parents[0])
            if problem is not None:
                note = "cannot evaluate, treated as false: {}".format(problem)
            else:
                verdict = site_only(paths)
                note = "clean decision over {} changed paths".format(len(paths))
    line = "site_only=true" if verdict else "site_only=false"
    try:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as exc:
        print("cannot write the decision to GITHUB_OUTPUT: {}".format(exc), file=sys.stderr)
        return 1
    print("{} ({})".format(line, note))
    return 0


# SELF-TEST: production code ends here. Every fixture below is synthetic. Each case asserts
# through the instrumented check() choke point, and the executed id set is reconciled against
# tools/selftest_checks.toml (suite ci-site-scope-selftest), both in-run and by
# tools/check_selftest_execution.py. The revert/ cases patch one rule out of this module's
# globals (no code is generated or evaluated) and require its fixture to flip.

SUITE_ID = "ci-site-scope-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def _patched(**replacements):
    """Replace module-level names (a rule patched out) for the duration of the block, then
    restore them."""
    import contextlib

    @contextlib.contextmanager
    def manager():
        names, absent = globals(), object()
        saved = dict((name, names.get(name, absent)) for name in replacements)
        names.update(replacements)
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is absent:
                    del names[name]
                else:
                    names[name] = value
    return manager()


def _git_env():
    """The hermetic environment for fixture git and for the CLI children the self-test launches:
    the shared fixture scrub (tools/_git_fixture_env.py: no inherited GIT_* variable, HOME and
    XDG_CONFIG_HOME pointed at a scratch home, the global and system config pinned to os.devnull,
    the no-auto-maintenance pins) plus a fixed identity, so no git call of the self-test reads the
    caller's configuration. Imported here, never on the --ci-decide path, which must run alone."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    return git_fixture_env(GIT_AUTHOR_NAME="fixture", GIT_COMMITTER_NAME="fixture",
                           GIT_AUTHOR_EMAIL="fixture@example.invalid",
                           GIT_COMMITTER_EMAIL="fixture@example.invalid")


def _fixture_git(path, *args, env):
    # The maintenance pins ride the argv in option position, so a fixture command can never spawn
    # a detached maintenance child that outlives the fixture directory.
    subprocess.run(["git", "-C", str(path), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                    "-c", "maintenance.auto=false", *args], check=True, capture_output=True,
                   timeout=GIT_TIMEOUT, env=env)


def _plant_tool(repo):
    """Copy this tool and its exit-report sibling into repo/tools, so the planted copy's own ROOT
    is the fixture repo and the CLI runs there exactly as the workflow runs it here."""
    here = Path(__file__).resolve().parent
    os.makedirs(repo / "tools", exist_ok=True)
    shutil.copy(here / "ci_site_scope.py", repo / "tools" / "ci_site_scope.py")
    shutil.copy(here / "_selftest_exit_report.py", repo / "tools" / "_selftest_exit_report.py")


def _fixture_repo(base, env):
    """A synthetic repo: one base commit holding a site file, a tools file, the three regenerated
    records, and a planted copy of this tool. Returns (repo path, base revision)."""
    repo = base / "repo"
    os.makedirs(repo / "site")
    os.makedirs(repo / "opf" / "site")
    os.makedirs(repo / ".aiqt" / "release")
    os.makedirs(repo / "tools")
    _plant_tool(repo)
    (repo / "site" / "index.html").write_text("a\n")
    (repo / "opf" / "site" / "a.css").write_text("a\n")
    (repo / ".aiqt" / "manifest.toml").write_text("a\n")
    (repo / ".aiqt" / "release" / "root.txt").write_text("a\n")
    (repo / ".aiqt" / "release" / "announce-snippet.txt").write_text("a\n")
    (repo / "tools" / "t.py").write_text("a\n")
    _fixture_git(repo, "init", "-q", "-b", "main", env=env)
    _fixture_git(repo, "add", "-A", env=env)
    _fixture_git(repo, "commit", "-q", "-m", "base", env=env)
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    return repo, out.stdout.strip()


def _fixture_rev(repo, rev, env):
    """The full object id rev resolves to in the fixture repo."""
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", rev], check=True,
                         capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)
    return out.stdout.strip()


def _fixture_parent_count(repo, env):
    """How many parents the fixture's HEAD has, read by git rather than by the tool, so a case
    pins the shape it builds."""
    out = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "rev-list", "--parents",
                          "-n", "1", "HEAD"], check=True, capture_output=True, text=True,
                         timeout=GIT_TIMEOUT, env=env)
    return len(out.stdout.split()) - 1


def _fixture_literal_head(repo, body, env):
    """Write body as a commit object without git's own format checks and detach HEAD onto it, so
    a case can present a header shape git itself never writes."""
    proc = subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                           "-c", "maintenance.auto=false", "hash-object", "-t", "commit",
                           "--literally", "-w", "--stdin"], input=body, check=True,
                          capture_output=True, timeout=GIT_TIMEOUT, env=env)
    with open(repo / ".git" / "HEAD", "w", encoding="ascii") as handle:
        handle.write(proc.stdout.decode("ascii").strip() + "\n")


def _cli(repo, merge_base):
    """Run the planted CLI copy as a child exactly as the workflow does; (exit code, stdout)."""
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(repo / "tools" / "ci_site_scope.py"),
         "--merge-base", merge_base],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=str(repo), env=_git_env())
    return proc.returncode, proc.stdout.strip()


def _cli_outcome(repo, merge_base):
    rc, out = _cli(repo, merge_base)
    return rc, out.splitlines()[-1] if out else ""


def _ci_decide_outcome(repo, event, output, head_ref="merge-train-1",
                       head_repo="example/repo", base_repo="example/repo", head_sha=None,
                       tool=None):
    """Run the planted copy's --ci-decide (or the copy at tool, for the extracted-alone cases)
    with a controlled environment (a merge-train head of this repository unless a case overrides
    it; None leaves a variable unset); (exit code, what the output file holds afterwards, or None
    when it does not exist)."""
    return _ci_decide_run(repo, event, output, head_ref, head_repo, base_repo, head_sha,
                          tool)[:2]


def _ci_decide_run(repo, event, output, head_ref="merge-train-1", head_repo="example/repo",
                   base_repo="example/repo", head_sha=None, tool=None):
    """_ci_decide_outcome's run, also returning the decision line --ci-decide printed, whose
    parenthesised note says why the decision is what it is; (exit code, output file contents
    or None, printed stdout)."""
    env = _git_env()
    for name in ("GITHUB_OUTPUT", "EVENT_NAME", "HEAD_REF", "HEAD_REPO", "BASE_REPO",
                 "HEAD_SHA"):
        env.pop(name, None)
    if event is not None:
        env["EVENT_NAME"] = event
    if head_ref is not None:
        env["HEAD_REF"] = head_ref
    if head_repo is not None:
        env["HEAD_REPO"] = head_repo
    if base_repo is not None:
        env["BASE_REPO"] = base_repo
    if head_sha is not None:
        env["HEAD_SHA"] = head_sha
    if output is not None:
        env["GITHUB_OUTPUT"] = str(output)
    target = repo / "tools" / "ci_site_scope.py" if tool is None else tool
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(target), "--ci-decide"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=str(repo), env=env)
    recorded = None
    if output is not None and os.path.isfile(str(output)):
        with open(str(output), encoding="utf-8") as handle:
            recorded = handle.read()
    return proc.returncode, recorded, proc.stdout


def _ci_select_run(repo, event, head_ref="merge-train-1", head_repo="example/repo",
                   base_repo="example/repo", head_sha=None, registry=None, output=None,
                   summary=None):
    """Run the planted copy's --ci-select --shadow with a controlled environment, as
    _ci_decide_run runs --ci-decide (None leaves a variable unset); (exit code, printed stdout,
    whether the GITHUB_OUTPUT target exists afterwards, so a case can pin that the shadow plan
    never writes an output)."""
    env = _git_env()
    for name in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "EVENT_NAME", "HEAD_REF", "HEAD_REPO",
                 "BASE_REPO", "HEAD_SHA", "CHECKS_REGISTRY"):
        env.pop(name, None)
    for name, value in (("EVENT_NAME", event), ("HEAD_REF", head_ref), ("HEAD_REPO", head_repo),
                        ("BASE_REPO", base_repo), ("HEAD_SHA", head_sha),
                        ("CHECKS_REGISTRY", registry), ("GITHUB_OUTPUT", output),
                        ("GITHUB_STEP_SUMMARY", summary)):
        if value is not None:
            env[name] = str(value)
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(repo / "tools" / "ci_site_scope.py"),
         "--ci-select", "--shadow"],
        capture_output=True, text=True, timeout=GIT_TIMEOUT, cwd=str(repo), env=env)
    written = output is not None and os.path.isfile(str(output))
    return proc.returncode, proc.stdout, written


# The registry the CLI selection cases extract for the planted tool: three reviewed entries so
# the trigger matching itself decides, with the universal glob claimed by a reviewed whole-repo
# scan exactly as the real registry's secrets scans claim it.
SELECT_REGISTRY_TOML = (
    'format-version = 1\n'
    '\n'
    '[[check]]\n'
    'job = "site"\n'
    'step = "Site gate"\n'
    'categories = ["site"]\n'
    'scope = "sub-tree"\n'
    'triggers = ["site/**", "opf/site/**", ".aiqt/**"]\n'
    'reviewed = true\n'
    '\n'
    '[[check]]\n'
    'job = "tools"\n'
    'step = "Tool gate"\n'
    'categories = ["ci"]\n'
    'scope = "sub-tree"\n'
    'triggers = ["tools/**"]\n'
    'reviewed = true\n'
    '\n'
    '[[check]]\n'
    'job = "scan"\n'
    'step = "Whole-repo scan"\n'
    'categories = ["secrets"]\n'
    'scope = "whole-repo"\n'
    'triggers = ["**"]\n'
    'reviewed = true\n')


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _exit_report().arm(report_path, SUITE_ID, EXECUTED)
    expected = _expected_check_ids()
    if expected is None:
        return 2
    # ---------- the pure decision, every branch ----------
    check("decide/empty-list-false", site_only([]), False)
    check("decide/not-iterable-false", site_only(None), False)
    check("decide/site-file-true", site_only(["site/index.html"]), True)
    check("decide/site-nested-true", site_only(["site/a/b/c.js"]), True)
    check("decide/opf-site-file-true", site_only(["opf/site/a.css"]), True)
    check("decide/regen-manifest-true", site_only([".aiqt/manifest.toml"]), True)
    check("decide/regen-root-true", site_only([".aiqt/release/root.txt"]), True)
    check("decide/regen-announce-true", site_only([".aiqt/release/announce-snippet.txt"]), True)
    check("decide/every-allowlisted-true",
          site_only(["site/index.html", "opf/site/a.css", ".aiqt/manifest.toml",
                     ".aiqt/release/root.txt", ".aiqt/release/announce-snippet.txt"]), True)
    check("decide/non-site-path-false", site_only(["tools/t.py"]), False)
    check("decide/one-outside-poisons-false", site_only(["site/index.html", "tools/t.py"]), False)
    # the prefix boundary: just inside, exactly at, and just outside each tree
    check("decide/tree-boundary",
          [site_only(["site/x"]), site_only(["site/"]), site_only(["site"]),
           site_only(["sitex/a"]), site_only(["site.html"])],
          [True, False, False, False, False])
    check("decide/opf-tree-boundary",
          [site_only(["opf/site/x"]), site_only(["opf/site/"]), site_only(["opf/site"]),
           site_only(["opf/sitex/a"])],
          [True, False, False, False])
    # the six site-tree pages a heavy suite reads as a verdict input are excluded
    check("decide/heavy-read-page-false",
          [site_only([path]) for path in sorted(SITE_HEAVY_READ)], [False] * 6)
    check("decide/heavy-read-poisons-false",
          site_only(["site/index.html", "opf/site/tooling.html"]), False)
    # the regenerated records match exactly, never as a prefix or a sibling
    check("decide/regen-exact-only",
          [site_only([".aiqt/manifest.toml.bak"]), site_only([".aiqt/release/root.txt2"]),
           site_only([".aiqt/release/"]), site_only([".aiqt/manifest.tom"])],
          [False, False, False, False])
    # path shapes that are not plain repo-relative file paths
    check("decide/absolute-false", site_only(["/site/index.html"]), False)
    check("decide/parent-climb-false", site_only(["site/../tools/t.py"]), False)
    check("decide/dot-segment-false", site_only(["site/./a"]), False)
    check("decide/empty-segment-false", site_only(["site//a"]), False)
    check("decide/backslash-false", site_only(["site\\a"]), False)
    check("decide/drive-colon-false", site_only(["c:/site/a"]), False)
    check("decide/empty-string-false", site_only([""]), False)
    check("decide/non-string-false", site_only([b"site/a"]), False)
    # ---------- the train gate every --ci-decide verdict must pass ----------
    check("train/train-head-true",
          train_head("pull_request", "merge-train-1", "o/r", "o/r"), True)
    check("train/non-pull-request-false",
          [train_head("push", "merge-train-1", "o/r", "o/r"),
           train_head("", "merge-train-1", "o/r", "o/r")], [False, False])
    check("train/non-train-head-false",
          train_head("pull_request", "feature-x", "o/r", "o/r"), False)
    check("train/fork-head-false",
          train_head("pull_request", "merge-train-1", "fork/r", "o/r"), False)
    check("train/empty-repo-false",
          [train_head("pull_request", "merge-train-1", "", ""),
           train_head("pull_request", "merge-train-1", None, None)], [False, False])
    # the prefix boundary: just inside, exactly at, and just outside it
    check("train/prefix-boundary",
          [train_head("pull_request", "merge-train-x", "o/r", "o/r"),
           train_head("pull_request", "merge-train-", "o/r", "o/r"),
           train_head("pull_request", "merge-train", "o/r", "o/r"),
           train_head("pull_request", "merge-trainx", "o/r", "o/r"),
           train_head("pull_request", "", "o/r", "o/r")],
          [True, True, False, False, False])
    # ---------- revert cases: each rule patched out flips its vector ----------
    with _patched(_allowed=lambda path: True):
        check("revert/allowlist-dropped-red", site_only(["tools/t.py"]), True)
    with _patched(REQUIRE_NONEMPTY=False):
        check("revert/nonempty-rule-dropped-red", site_only([]), True)
    with _patched(_plain_relative=lambda path: isinstance(path, str) and bool(path)):
        check("revert/path-shape-rule-dropped-red", site_only(["site/../site/a"]), True)
    with _patched(SITE_TREES=("site/", "opf/site/", "tools/")):
        check("revert/tree-widened-red", site_only(["tools/t.py"]), True)
    with _patched(SITE_REGENERATED=frozenset({".aiqt/manifest.toml", "VERSION"})):
        check("revert/regenerated-widened-red", site_only(["VERSION"]), True)
    with _patched(SITE_HEAVY_READ=frozenset()):
        check("revert/heavy-read-exclusion-dropped-red",
              site_only(["opf/site/tooling.html"]), True)
    with _patched(TRAIN_PREFIX=""):
        check("revert/train-prefix-dropped-red",
              train_head("pull_request", "feature-x", "o/r", "o/r"), True)
    # ---------- the pure selection plan (shadow mode), every rule ----------
    import fnmatch

    def _select_entry(job, step, triggers, reviewed=True):
        return {"job": job, "step": step, "categories": ["ci"], "scope": "sub-tree",
                "triggers": triggers, "reviewed": reviewed}
    select_rows = [
        _select_entry("ci", "Workflow gate", [".github/**"]),
        _select_entry("lint", "Tool gate", ["tools/*.py"]),
        _select_entry("lint", "Docs gate", ["docs/**"]),
        _select_entry("site", "Site gate", ["site/**", "opf/site/**", ".aiqt/**"]),
        _select_entry("scan", "Whole-repo scan", ["**"]),
        _select_entry("deep", "Deep tool gate", ["tools/**"]),
        _select_entry("draft", "Unreviewed gate", ["opf/**"], reviewed=False),
        _select_entry("agg", "Aggregate", []),
    ]
    select_registry = {"format-version": 1, "check": select_rows}
    # the same registry without the universal-glob scan, so an unclaimed path exists
    narrow_registry = {"format-version": 1,
                       "check": [row for row in select_rows if row["job"] != "scan"]}

    def _runs(paths, registry):
        plan, problem = select(paths, registry)
        if problem is not None or plan is None:
            return problem
        return (plan["full_run"],
                {job: verdict["run"] for job, verdict in plan["jobs"].items()})
    check("select/selective-single-tool-change", _runs(["tools/x.py"], select_registry),
          (None, {"ci": False, "lint": True, "site": False, "scan": True, "deep": True,
                  "draft": True, "agg": True}))
    check("select/mixed-change-selects-both",
          _runs(["tools/x.py", "site/a.css"], select_registry),
          (None, {"ci": False, "lint": True, "site": True, "scan": True, "deep": True,
                  "draft": True, "agg": True}))
    empty_plan, empty_problem = select([], select_registry)
    check("select/empty-diff-full-run",
          (empty_problem, empty_plan["full_run"] is not None,
           all(verdict["run"] for verdict in empty_plan["jobs"].values())),
          (None, True, True))
    check("select/not-iterable-cannot-evaluate",
          select(None, select_registry)[0] is None
          and select(None, select_registry)[1] is not None, True)
    check("select/bad-path-full-run",
          [select([path], select_registry)[0]["full_run"] is not None
           for path in ("site/../a", "/site/a", "", "site//a")],
          [True, True, True, True])
    check("select/unknown-path-full-run",
          "matches no trigger" in (select(["README.md"], narrow_registry)[0]["full_run"] or ""),
          True)
    check("select/only-universal-claim-full-run",
          "claimed only by the universal" in (
              select(["README.md"], select_registry)[0]["full_run"] or ""),
          True)
    # a trigger is universal by its meaning, not by one spelling: every spelling that matches a
    # path by its depth alone (every segment ** or any name) is universal, the bounded-depth
    # wildcards * and */* included, and each boundary is tested just inside, at and outside it
    # (a ? that needs exactly one character against *? and ?* that need at least one, a literal
    # or suffix in any segment)
    universal_spellings = ("**", "**/*", "*/**", "**/**", "***/**", "**/*?", "*/*/**",
                           "*", "***", "*?", "?*", "*/*", "*/*/*")
    specific_spellings = ("docs/**", "**/*.md", "**/?", "**/??*", "**/a*", "?/**", "?", "??*",
                          "*.md", "*/?", "a*/*")
    check("select/universal-glob-by-meaning",
          ([_universal_glob(glob) for glob in universal_spellings],
           [_universal_glob(glob) for glob in specific_spellings]),
          ([True] * len(universal_spellings), [False] * len(specific_spellings)))
    # the classification agrees with the matcher on sampled paths of one to four segments: a
    # universal spelling gives one answer for every sampled path of the same depth (it matches
    # all of them or none) and matches at least one sampled path, while every specific spelling
    # matches one sampled path and misses another of the same depth; of the unbounded ones,
    # only **, **/* and **/** also reach a top-level file
    sampled_paths = ("a", "README.md", "x.y", "ab", "a/b", "ab/cd", "x.y/z", "docs/q",
                     "a/b/c", "deep/er/file.txt", "a/b/c/d", "deep/er/path/file.txt")

    def _answers_by_depth(glob):
        answers = {}
        for path in sampled_paths:
            answers.setdefault(path.count("/"), set()).add(_glob_match(glob, path))
        return list(answers.values())
    check("select/universal-glob-agrees-with-matching",
          ([all(len(seen) == 1 for seen in _answers_by_depth(glob))
            and any(True in seen for seen in _answers_by_depth(glob))
            for glob in universal_spellings],
           [any(len(seen) == 2 for seen in _answers_by_depth(glob))
            for glob in specific_spellings],
           [_glob_match(glob, "README.md") for glob in ("**", "**/*", "**/**", "*/**")]),
          ([True] * len(universal_spellings), [True] * len(specific_spellings),
           [True, True, True, False]))

    def _spelled_registry(glob):
        # the narrow registry plus a reviewed whole-repo scan spelled as glob
        return {"format-version": 1, "check": narrow_registry["check"] + [
            _select_entry("scan", "Whole-repo scan", [glob])]}
    # README.md and an unlisted two-segment path, each claimed only by a spelling other than
    # the bare **, decide the full run; */** cannot claim a top-level file at all, so only its
    # two-segment path is a universal-only claim (README.md is unclaimed under it); a
    # bounded-depth wildcard claims only the paths at its own depth, each of them by depth alone
    spelled_vectors = (("**/*", "README.md"), ("**/*", "misc/a.txt"),
                       ("*/**", "misc/a.txt"),
                       ("**/**", "README.md"), ("**/**", "misc/a.txt"),
                       ("*", "README.md"), ("***", "README.md"), ("*?", "README.md"),
                       ("*/*", "misc/a.txt"))
    bounded_spellings = ("*", "***", "*?", "*/*")
    check("select/universal-spellings-full-run",
          ["claimed only by the universal" in (
              select([path], _spelled_registry(glob))[0]["full_run"] or "")
           for glob, path in spelled_vectors],
          [True] * len(spelled_vectors))
    check("select/universal-spelling-cannot-claim-top-level",
          "matches no trigger" in (
              select(["README.md"], _spelled_registry("*/**"))[0]["full_run"] or ""),
          True)
    # a bounded-depth wildcard claims nothing at another depth (those paths stay unclaimed),
    # and a path it shares with a specific trigger (tools/x.py, claimed by tools/*.py too)
    # still selects by that specific claim
    check("select/bounded-wildcard-claims-only-its-depth",
          ("matches no trigger" in (
              select(["README.md"], _spelled_registry("*/*"))[0]["full_run"] or ""),
           "matches no trigger" in (
               select(["misc/a.txt"], _spelled_registry("*"))[0]["full_run"] or ""),
           select(["tools/x.py"], _spelled_registry("*/*"))[0]["full_run"],
           select(["tools/x.py"], _spelled_registry("*/*"))[0]["jobs"]["lint"]["run"]),
          (True, True, None, True))
    check("select/full-run-paths",
          [select([path], select_registry)[0]["full_run"] is not None
           for path in (".github/workflows/quality.yml", ".gitattributes",
                        "tools/ci_checks.toml", "tools/ci_site_scope.py",
                        "tools/check_ci_selection.py", "tools/check_ci_parity.py",
                        "tools/selftest_checks.toml", "tools/run_all_checks.sh")],
          [True] * 8)
    # segment semantics: * and ? never cross a /, with each boundary tested just inside, at,
    # and outside it
    check("select/glob-star-within-segment",
          [_glob_match("tools/*.py", "tools/x.py"), _glob_match("tools/*.py", "tools/sub/x.py"),
           _glob_match("*", "a"), _glob_match("*", "a/b"), _glob_match("a?c", "abc"),
           _glob_match("a?c", "a/c")],
          [True, False, True, False, True, False])
    check("select/glob-doublestar-boundary",
          [_glob_match("docs/**", "docs/a"), _glob_match("docs/**", "docs/a/b"),
           _glob_match("docs/**", "docs"), _glob_match("docs/**", "docsx/a"),
           _glob_match("**", "a"), _glob_match("**", "a/b/c")],
          [True, True, False, False, True, True])
    check("select/registry-malformed-cannot-evaluate",
          [select(["site/a"], registry)[0] is None for registry in (
              None,
              {"format-version": 2, "check": select_rows},
              {"format-version": True, "check": select_rows},
              {"format-version": 1},
              {"format-version": 1, "check": [None]},
              {"format-version": 1, "check": [dict(select_rows[0], job=7)]},
              {"format-version": 1, "check": [dict(select_rows[0], triggers="x")]},
              {"format-version": 1, "check": [dict(select_rows[0], triggers=[7])]},
              {"format-version": 1, "check": [dict(select_rows[0], triggers=["/a"])]},
              {"format-version": 1, "check": [dict(select_rows[0], triggers=["a/../b"])]},
              {"format-version": 1, "check": [dict(select_rows[0], reviewed="yes")]})],
          [True] * 11)
    selective = select(["site/a.css"], select_registry)[0]
    check("select/unreviewed-always-runs",
          (selective["full_run"], selective["jobs"]["draft"]["run"],
           "unreviewed" in selective["jobs"]["draft"]["why"]),
          (None, True, True))
    check("select/no-triggers-always-runs",
          (selective["jobs"]["agg"]["run"],
           "declares no triggers" in selective["jobs"]["agg"]["why"]),
          (True, True))
    check("select/skip-why-recorded", selective["jobs"]["ci"],
          {"run": False, "why": "no trigger of its steps matches a changed path"})
    # the site_only decision is unchanged by the selection: the same vectors decide the same
    # before and after a select() pass, and equal the recorded pre-selection truths
    unchanged_vectors = (["site/index.html"], ["tools/t.py"], ["site/index.html", "tools/t.py"],
                         [], ["opf/site/a.css"], [".aiqt/manifest.toml"],
                         ["opf/site/tooling.html"])
    unchanged_want = [True, False, False, False, True, True, False]
    before_selection = [site_only(vector) for vector in unchanged_vectors]
    for vector in unchanged_vectors:
        select(vector, select_registry)
    check("select/site-only-unchanged-before-and-after",
          (before_selection, [site_only(vector) for vector in unchanged_vectors]),
          (unchanged_want, unchanged_want))
    # ---------- selection revert cases: each rule patched out flips its vector ----------
    with _patched(FULL_RUN_PATHS=()):
        check("revert/full-run-paths-dropped-red",
              select([".github/workflows/quality.yml"], select_registry)[0]["full_run"] is None,
              True)
    with _patched(REQUIRE_CLAIMED=False):
        check("revert/unknown-path-rule-dropped-red",
              select(["README.md"], narrow_registry)[0]["full_run"] is None, True)
    with _patched(REQUIRE_SPECIFIC_CLAIM=False):
        check("revert/universal-claim-rule-dropped-red",
              select(["README.md"], select_registry)[0]["full_run"] is None, True)
    # the universal rule judged by one spelling (only the exact string **) lets each other
    # spelling's universal-only claim pass as specific: one flip per spelling, each turning the
    # full run above into a selective plan
    with _patched(_universal_glob=lambda glob: glob == "**"):
        for check_id, glob in (
                ("revert/universal-by-spelling-doublestar-star-red", "**/*"),
                ("revert/universal-by-spelling-star-doublestar-red", "*/**"),
                ("revert/universal-by-spelling-doublestar-doublestar-red", "**/**")):
            check(check_id,
                  [select([path], _spelled_registry(glob))[0]["full_run"] is None
                   for spelled, path in spelled_vectors if spelled == glob],
                  [True] * sum(1 for spelled, _path in spelled_vectors if spelled == glob))
    # the universal rule restricted to spellings with a ** segment reads a bounded-depth
    # wildcard as a specific claim, so each bounded spelling's universal-only claim above turns
    # into a selective plan
    by_depth_rule = _universal_glob
    with _patched(_universal_glob=lambda glob: (
            "**" in glob.split("/") and by_depth_rule(glob))):
        check("revert/universal-bounded-depth-dropped-red",
              [select([path], _spelled_registry(glob))[0]["full_run"] is None
               for glob, path in spelled_vectors if glob in bounded_spellings],
              [True] * len(bounded_spellings))
    with _patched(_glob_match=lambda glob, path: fnmatch.fnmatchcase(path, glob)):
        # fnmatch lets * cross a /, so the segment rule's vector flips to a lint match
        check("revert/segment-glob-crossing-red",
              select(["tools/sub/x.py"], select_registry)[0]["jobs"]["lint"]["run"], True)
    with _patched(ALWAYS_RUN_UNREVIEWED=False):
        check("revert/unreviewed-rule-dropped-red",
              select(["site/a.css"], select_registry)[0]["jobs"]["draft"]["run"], False)
    # ---------- the CLI over synthetic repos, as the workflow runs it ----------
    # The probe runs under the fixture environment too: git reads the caller's global config even
    # for --version, so a malformed caller config would otherwise read as git being unavailable.
    env = _git_env()
    git_probe = 1
    try:
        git_probe = subprocess.run(["git", "--version"], capture_output=True,
                                   timeout=GIT_TIMEOUT, env=env).returncode
    except OSError:
        git_probe = 1
    if git_probe != 0:
        FAILURES.append("cli/: git is unavailable, the CLI vectors cannot run")
    else:
        with tempfile.TemporaryDirectory() as scratch:
            repo, base = _fixture_repo(Path(scratch), env)
            (repo / "site" / "index.html").write_text("b\n")
            (repo / "opf" / "site" / "a.css").write_text("b\n")
            (repo / ".aiqt" / "manifest.toml").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "site change", env=env)
            check("cli/site-only-true-exit-0", _cli_outcome(repo, base), (0, "site_only=true"))
            check("cli/empty-diff-false-exit-0", _cli_outcome(repo, "HEAD"),
                  (0, "site_only=false"))
            check("cli/bad-revision-exit-2", _cli_outcome(repo, "no-such-revision"),
                  (2, "site_only=false"))
            check("cli/option-revision-exit-2", _cli_outcome(repo, "--cached"),
                  (2, "site_only=false"))
            # ---------- the --ci-decide entry the scope job runs ----------
            # the head still holds only site-allowlisted changes over HEAD^1 here, but it is a
            # LINEAR commit: with no second parent the checkout is not the tested two-parent
            # merge, so the decision is cannot-evaluate, false, whatever the paths say
            check("cidecide/linear-head-no-second-parent-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-linear",
                                     head_sha="0" * 40),
                  (0, "site_only=false\n"))
            check("cidecide/push-false",
                  _ci_decide_outcome(repo, "push", Path(scratch) / "gh-output-push"),
                  (0, "site_only=false\n"))
            check("cidecide/non-train-head-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-nontrain",
                                     head_ref="feature-x"),
                  (0, "site_only=false\n"))
            check("cidecide/fork-head-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-fork",
                                     head_repo="fork/repo"),
                  (0, "site_only=false\n"))
            check("cidecide/missing-head-env-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-noenv",
                                     head_ref=None, head_repo=None, base_repo=None),
                  (0, "site_only=false\n"))
            check("cidecide/no-output-exit-1",
                  _ci_decide_outcome(repo, "pull_request", None), (1, None))
            unwritable = Path(scratch) / "gh-output-dir"
            os.makedirs(unwritable)
            check("cidecide/unwritable-output-exit-1",
                  _ci_decide_outcome(repo, "pull_request", unwritable)[0], 1)
            (repo / "tools" / "t.py").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "tool change", env=env)
            check("cli/mixed-change-false-exit-0", _cli_outcome(repo, base),
                  (0, "site_only=false"))
            check("cidecide/pull-request-mixed-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-mixed"),
                  (0, "site_only=false\n"))
        with tempfile.TemporaryDirectory() as scratch:
            repo, base = _fixture_repo(Path(scratch), env)
            # the root commit has no parent at all, so the second-parent rule reads it as
            # cannot-evaluate, written as false with exit 0
            check("cidecide/no-first-parent-false-exit-0",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-root",
                                     head_sha="0" * 40),
                  (0, "site_only=false\n"))
            # a true merge commit shaped as the tested merge ref: the first parent is the
            # base-branch tip, the second the train head, so HEAD^1..HEAD is exactly the
            # train's site-only change; the decision requires exactly two parents, the second
            # equal to the head SHA the environment passes
            _fixture_git(repo, "checkout", "-q", "-b", "merge-train-1", env=env)
            (repo / "site" / "index.html").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "train site change", env=env)
            train_tip = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env).stdout.strip()
            _fixture_git(repo, "checkout", "-q", "main", env=env)
            _fixture_git(repo, "merge", "-q", "--no-ff", "-m", "merge", "merge-train-1",
                         env=env)
            check("cidecide/train-head-true",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-merge", head_sha=train_tip),
                  (0, "site_only=true\n"))
            # the boundary of the second-parent rule: the same merge decides false when the
            # environment names another commit as the head, and when it names none
            check("cidecide/second-parent-mismatch-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-mismatch", head_sha=base),
                  (0, "site_only=false\n"))
            check("cidecide/missing-head-sha-false",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-nosha"),
                  (0, "site_only=false\n"))
            # the scope job's real launch shape: the tool extracted ALONE into a directory
            # outside any repository, run with the working directory set to the repo it
            # judges; the planted copy above carries its exit-report sibling, so only this
            # case catches a --ci-decide path that imports one
            alone = Path(scratch) / "outside-any-repo"
            os.makedirs(alone)
            shutil.copy(Path(__file__).resolve(), alone / "ci_site_scope_base.py")
            check("cidecide/isolated-tool-outside-repo-true",
                  _ci_decide_outcome(repo, "pull_request",
                                     Path(scratch) / "gh-output-isolated",
                                     head_sha=train_tip,
                                     tool=alone / "ci_site_scope_base.py"),
                  (0, "site_only=true\n"))
        with tempfile.TemporaryDirectory() as scratch:
            # a valid two-parent merge shaped as the tested merge ref, HEAD_SHA its second
            # parent, whose train side changes a tools/ file: the parent rule passes, so only
            # the path decision over HEAD^1..HEAD can make it false
            repo, base = _fixture_repo(Path(scratch), env)
            _fixture_git(repo, "checkout", "-q", "-b", "merge-train-2", env=env)
            (repo / "site" / "index.html").write_text("b\n")
            (repo / "tools" / "t.py").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "train tool change", env=env)
            train_tip = _fixture_rev(repo, "HEAD", env)
            _fixture_git(repo, "checkout", "-q", "main", env=env)
            _fixture_git(repo, "merge", "-q", "--no-ff", "-m", "merge", "merge-train-2",
                         env=env)
            check("cidecide/two-parent-merge-tools-change-false",
                  (_fixture_parent_count(repo, env),
                   _ci_decide_outcome(repo, "pull_request",
                                      Path(scratch) / "gh-output-tools-merge",
                                      head_sha=train_tip)),
                  (2, (0, "site_only=false\n")))
        with tempfile.TemporaryDirectory() as scratch:
            # a three-parent (octopus) merge whose second parent is HEAD_SHA and whose every
            # change is site-only: reading HEAD^2 alone would accept it, the exact-two rule
            # does not. The base branch moves first, so the merge records all three parents.
            repo, base = _fixture_repo(Path(scratch), env)
            _fixture_git(repo, "checkout", "-q", "-b", "merge-train-3", env=env)
            (repo / "site" / "index.html").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "train site change", env=env)
            train_tip = _fixture_rev(repo, "HEAD", env)
            _fixture_git(repo, "checkout", "-q", "-b", "side", "main", env=env)
            (repo / "opf" / "site" / "a.css").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "side site change", env=env)
            _fixture_git(repo, "checkout", "-q", "main", env=env)
            (repo / ".aiqt" / "manifest.toml").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "base moves", env=env)
            _fixture_git(repo, "merge", "-q", "--no-ff", "-m", "octopus", "merge-train-3",
                         "side", env=env)
            rc, recorded, three_parent_note = _ci_decide_run(
                repo, "pull_request", Path(scratch) / "gh-output-octopus", head_sha=train_tip)
            check("cidecide/three-parent-merge-false",
                  (_fixture_parent_count(repo, env), _fixture_rev(repo, "HEAD^2", env) == train_tip,
                   rc, recorded),
                  (3, True, 0, "site_only=false\n"))
        with tempfile.TemporaryDirectory() as scratch:
            # every cannot-evaluate cause of the parent read decides false with a note naming
            # that cause: a git failure (no repository) is not reported as a missing parent,
            # and a header git never writes is unparseable, never read as a shorter list
            notes = []
            notrepo = Path(scratch) / "notrepo"
            os.makedirs(notrepo)
            notes.append((_ci_decide_run(notrepo, "pull_request",
                                         Path(scratch) / "gh-output-notrepo",
                                         head_sha="0" * 40,
                                         tool=Path(__file__).resolve()),
                          "git cat-file commit HEAD exited"))
            repo, base = _fixture_repo(Path(scratch), env)
            notes.append((_ci_decide_run(repo, "pull_request",
                                         Path(scratch) / "gh-output-root-note",
                                         head_sha="0" * 40),
                          "HEAD has no parent (a root commit)"))
            (repo / "site" / "index.html").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "linear site change", env=env)
            notes.append((_ci_decide_run(repo, "pull_request",
                                         Path(scratch) / "gh-output-linear-note",
                                         head_sha="0" * 40),
                          "HEAD has one parent (a linear commit)"))
            notes.append(((rc, recorded, three_parent_note), "HEAD has 3 parents, not exactly two"))
            tree = _fixture_rev(repo, "HEAD^{tree}", env).encode("ascii")
            author = b"author fixture <fixture@example.invalid> 0 +0000\n"
            literal = (
                (b"tree " + tree + b"\nparent " + base.encode("ascii") + b"\nparent zz\n" +
                 author + b"\nmalformed\n", "malformed parent line"),
                (b"tree " + tree + b"\nparent " + base.encode("ascii") + b"\n" + author +
                 b"parent " + base.encode("ascii") + b"\n\nstray\n",
                 "parent line outside the parent block"),
                (b"tree " + tree + b"\nparent " + base.encode("ascii") + b"\nparent " +
                 base.encode("ascii") + b"\n" + author.rstrip(b"\n"), "no header end"),
                (b"parent " + base.encode("ascii") + b"\nparent " + base.encode("ascii") +
                 b"\ntree " + tree + b"\n" + author + b"\nno tree first\n",
                 "does not begin with a tree line"),
            )
            for index, (body, phrase) in enumerate(literal):
                _fixture_literal_head(repo, body, env)
                notes.append((_ci_decide_run(repo, "pull_request",
                                             Path(scratch) / "gh-output-literal-{}".format(index),
                                             head_sha=base),
                              phrase))
            check("cidecide/parent-read-causes-false-and-named",
                  [(rc, recorded, "cannot evaluate" in out and phrase in out)
                   for (rc, recorded, out), phrase in notes],
                  [(0, "site_only=false\n", True)] * 8)
        with tempfile.TemporaryDirectory() as scratch:
            # the decision reads HEAD's raw commit object and nothing that rewrites it: a valid
            # site-only two-parent merge, HEAD_SHA its second parent, still decides true when its
            # header also carries a gpgsig or a mergetag header (multi-line values whose
            # continuation lines start with a space; each carries one that reads "parent <id>"
            # after that space, which is header value text, never a parent line), when an
            # info/grafts file gives HEAD other parents, and when a replace ref swaps the base
            repo, base = _fixture_repo(Path(scratch), env)
            _fixture_git(repo, "checkout", "-q", "-b", "merge-train-4", env=env)
            (repo / "site" / "index.html").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "train site change", env=env)
            train_tip = _fixture_rev(repo, "HEAD", env)
            _fixture_git(repo, "checkout", "-q", "main", env=env)
            _fixture_git(repo, "merge", "-q", "--no-ff", "-m", "merge", "merge-train-4",
                         env=env)
            merge = _fixture_rev(repo, "HEAD", env)
            tree = _fixture_rev(repo, "HEAD^{tree}", env).encode("ascii")
            signature = b"fixture <fixture@example.invalid> 0 +0000\n"
            header = (b"tree " + tree + b"\nparent " + base.encode("ascii") + b"\nparent " +
                      train_tip.encode("ascii") + b"\nauthor " + signature + b"committer " +
                      signature)
            signed = (header + b"gpgsig -----BEGIN PGP SIGNATURE-----\n \n parent " +
                      base.encode("ascii") + b"\n -----END PGP SIGNATURE-----\n\nmerge\n")
            tagged = (header + b"mergetag object " + train_tip.encode("ascii") +
                      b"\n type commit\n tag v1\n tagger " + signature + b" \n parent " +
                      base.encode("ascii") + b" is the base this tag was cut against\n\nmerge\n")
            for check_id, body in (("cidecide/gpgsig-merge-true", signed),
                                   ("cidecide/mergetag-merge-true", tagged)):
                _fixture_literal_head(repo, body, env)
                check(check_id,
                      (_fixture_parent_count(repo, env),
                       _ci_decide_outcome(repo, "pull_request",
                                          Path(scratch) / ("gh-output-" + check_id[9:]),
                                          head_sha=train_tip)),
                      (2, (0, "site_only=true\n")))
            # info/grafts gives HEAD one parent, and git's own parent read follows it (the
            # fixture's count is 1); --no-replace-objects does not disable grafts, so only the
            # raw object read keeps the decision on the two recorded parents
            _fixture_git(repo, "checkout", "-q", "--detach", merge, env=env)
            os.makedirs(repo / ".git" / "info", exist_ok=True)
            with open(repo / ".git" / "info" / "grafts", "w", encoding="ascii") as handle:
                handle.write("{} {}\n".format(merge, base))
            check("cidecide/grafted-parents-raw-object-true",
                  (_fixture_parent_count(repo, env),
                   _ci_decide_outcome(repo, "pull_request", Path(scratch) / "gh-output-grafts",
                                      head_sha=train_tip)),
                  (1, (0, "site_only=true\n")))
            os.remove(repo / ".git" / "info" / "grafts")
            # a replace ref swaps the base for a commit whose tools/ file differs, so a diff
            # that honoured it would see a tools/ change (git's own diff does); the decision
            # reads the base as recorded
            _fixture_git(repo, "checkout", "-q", "-b", "replacement", base, env=env)
            (repo / "tools" / "t.py").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "replacement base", env=env)
            replacement = _fixture_rev(repo, "HEAD", env)
            _fixture_git(repo, "checkout", "-q", "--detach", merge, env=env)
            _fixture_git(repo, "replace", base, replacement, env=env)
            honoured = subprocess.run(
                ["git", "-C", str(repo), "diff", "--name-only", "--no-renames", base, "HEAD"],
                check=True, capture_output=True, text=True, timeout=GIT_TIMEOUT,
                env=env).stdout.split()
            check("cidecide/replaced-base-raw-object-true",
                  ("tools/t.py" in honoured,
                   _ci_decide_outcome(repo, "pull_request", Path(scratch) / "gh-output-replace",
                                      head_sha=train_tip)),
                  (True, (0, "site_only=true\n")))
        with tempfile.TemporaryDirectory() as scratch:
            repo, base = _fixture_repo(Path(scratch), env)
            _fixture_git(repo, "mv", "site/index.html", "tools/index.html", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "rename out", env=env)
            check("cli/rename-out-of-tree-false-exit-0", _cli_outcome(repo, base),
                  (0, "site_only=false"))
            _fixture_git(repo, "mv", "tools/index.html", "opf/site/index.html", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "rename within allowlist", env=env)
            head_minus_one = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD^"], check=True, capture_output=True,
                text=True, timeout=GIT_TIMEOUT, env=env).stdout.strip()
            check("cli/rename-into-tree-false-exit-0", _cli_outcome(repo, head_minus_one),
                  (0, "site_only=false"))
        with tempfile.TemporaryDirectory() as scratch:
            # not a repo at all: the diff read fails, the decision is cannot-evaluate
            bare = Path(scratch) / "notrepo"
            os.makedirs(bare)
            _plant_tool(bare)
            check("cli/outside-repo-exit-2", _cli_outcome(bare, "HEAD"),
                  (2, "site_only=false"))
        with tempfile.TemporaryDirectory() as scratch:
            # ---------- the --ci-select --shadow entry the scope job runs ----------
            # a merge-train merge whose train side adds, deletes and renames within the site
            # trees: the plan is selective, both rename sides and every changed path are
            # recorded, the summary receives the same text, and GITHUB_OUTPUT is NEVER written
            repo, base = _fixture_repo(Path(scratch), env)
            registry_file = Path(scratch) / "ci_checks_base.toml"
            registry_file.write_text(SELECT_REGISTRY_TOML, encoding="utf-8")
            _fixture_git(repo, "checkout", "-q", "-b", "merge-train-5", env=env)
            (repo / "site" / "new.html").write_text("a\n")
            _fixture_git(repo, "add", "site/new.html", env=env)
            _fixture_git(repo, "rm", "-q", "opf/site/a.css", env=env)
            _fixture_git(repo, "mv", "site/index.html", "site/renamed.html", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "train site change", env=env)
            train_tip = _fixture_rev(repo, "HEAD", env)
            _fixture_git(repo, "checkout", "-q", "main", env=env)
            _fixture_git(repo, "merge", "-q", "--no-ff", "-m", "merge", "merge-train-5",
                         env=env)
            summary_file = Path(scratch) / "summary.md"
            rc, out, wrote = _ci_select_run(
                repo, "pull_request", head_sha=train_tip, registry=registry_file,
                output=Path(scratch) / "never-output", summary=summary_file)
            with open(summary_file, encoding="utf-8") as handle:
                summary_text = handle.read()
            check("ciselect/train-plan-selective-and-summary",
                  (rc, wrote, "plan: selective over 4 changed path(s)" in out,
                   "job site: run" in out, "job tools: skip" in out, "job scan: run" in out,
                   SHADOW_HEADER in out, SHADOW_HEADER in summary_text),
                  (0, False, True, True, True, True, True, True))
            check("ciselect/addition-deletion-rename-recorded",
                  [needle in out for needle in
                   ("'site/new.html'", "'opf/site/a.css'", "'site/index.html'",
                    "'site/renamed.html'")],
                  [True, True, True, True])
            # every non-train shape is a full-run plan, exit 0, with no output written
            check("ciselect/push-schedule-dispatch-full-run",
                  [_ci_select_run(repo, event, head_sha=train_tip, registry=registry_file,
                                  output=Path(scratch) / ("no-output-" + event))[::2]
                   + ("plan: full run" in _ci_select_run(
                       repo, event, head_sha=train_tip, registry=registry_file)[1],)
                   for event in ("push", "schedule", "workflow_dispatch")],
                  [(0, False, True)] * 3)
            check("ciselect/fork-head-full-run",
                  (lambda result: (result[0], "plan: full run" in result[1]))(
                      _ci_select_run(repo, "pull_request", head_repo="fork/repo",
                                     head_sha=train_tip, registry=registry_file)),
                  (0, True))
            check("ciselect/non-train-head-full-run",
                  (lambda result: (result[0], "plan: full run" in result[1]))(
                      _ci_select_run(repo, "pull_request", head_ref="feature-x",
                                     head_sha=train_tip, registry=registry_file)),
                  (0, True))
            # a missing, malformed or undeclared base registry is a cannot-evaluate plan that
            # never fails the step and never writes an output
            check("ciselect/missing-registry-cannot-evaluate",
                  (lambda result: (result[0], result[2],
                                   "plan: cannot evaluate" in result[1],
                                   "cannot read the registry" in result[1]))(
                      _ci_select_run(repo, "pull_request", head_sha=train_tip,
                                     registry=Path(scratch) / "absent.toml",
                                     output=Path(scratch) / "no-output-missing")),
                  (0, False, True, True))
            broken_file = Path(scratch) / "broken.toml"
            broken_file.write_text("format-version = [\n", encoding="utf-8")
            check("ciselect/malformed-registry-cannot-evaluate",
                  (lambda result: (result[0], result[2],
                                   "plan: cannot evaluate" in result[1],
                                   "not valid TOML" in result[1]))(
                      _ci_select_run(repo, "pull_request", head_sha=train_tip,
                                     registry=broken_file,
                                     output=Path(scratch) / "no-output-broken")),
                  (0, False, True, True))
            check("ciselect/unset-registry-cannot-evaluate",
                  (lambda result: (result[0], "CHECKS_REGISTRY is not set" in result[1]))(
                      _ci_select_run(repo, "pull_request", head_sha=train_tip)),
                  (0, True))
            # the runs behind every cannot-evaluate note of ci_select(), each with the cause its
            # note names: an absent, a broken and an unset registry and, on this two-parent
            # merge, a head sha that is not its second parent; the linear head of the next
            # fixture adds the parent-read failure
            follows_runs = [
                (cause, _ci_select_run(repo, "pull_request", head_sha=head_sha,
                                       registry=registry)[1])
                for cause, head_sha, registry in (
                    ("cannot read the registry", train_tip, Path(scratch) / "absent.toml"),
                    ("not valid TOML", train_tip, broken_file),
                    ("CHECKS_REGISTRY is not set", train_tip, None),
                    ("is not the pull request's head commit", "0" * 40, registry_file))]
        with tempfile.TemporaryDirectory() as scratch:
            # a linear head is cannot-evaluate for the shadow plan too, still exit 0
            repo, base = _fixture_repo(Path(scratch), env)
            (repo / "site" / "index.html").write_text("b\n")
            _fixture_git(repo, "add", "-A", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "linear site change", env=env)
            registry_file = Path(scratch) / "ci_checks_base.toml"
            registry_file.write_text(SELECT_REGISTRY_TOML, encoding="utf-8")
            check("ciselect/linear-head-cannot-evaluate",
                  (lambda result: (result[0], "plan: cannot evaluate" in result[1]))(
                      _ci_select_run(repo, "pull_request", head_sha="0" * 40,
                                     registry=registry_file)),
                  (0, True))
            follows_runs.append(
                ("HEAD has one parent (a linear commit)",
                 _ci_select_run(repo, "pull_request", head_sha="0" * 40,
                                registry=registry_file)[1]))
            # the five runs reach all four cannot-evaluate notes of ci_select() (unset registry,
            # parent read, second-parent mismatch, registry read); each output names its cause,
            # states what really happens (the run follows the site_only decision already
            # written, which a site-only train can decide true) and never claims the full suite
            # runs
            check("ciselect/cannot-evaluate-states-run-follows-site-only",
                  [(cause in out, "the plan is advisory and the run follows site_only" in out,
                    "full suite" in out)
                   for cause, out in follows_runs],
                  [(True, True, False)] * 5)
        with tempfile.TemporaryDirectory() as scratch:
            # ---------- the local --explain --base REV query ----------
            repo, base = _fixture_repo(Path(scratch), env)
            _fixture_git(repo, "mv", "site/index.html", "tools/index.html", env=env)
            (repo / "site" / "new.html").write_text("a\n")
            _fixture_git(repo, "add", "site/new.html", env=env)
            _fixture_git(repo, "rm", "-q", "opf/site/a.css", env=env)
            _fixture_git(repo, "commit", "-q", "-m", "mixed change", env=env)

            def _explain_outcome(target_repo, base_rev):
                proc = subprocess.run(
                    [sys.executable, "-I", "-B",
                     str(target_repo / "tools" / "ci_site_scope.py"),
                     "--explain", "--base", base_rev],
                    capture_output=True, text=True, timeout=GIT_TIMEOUT,
                    cwd=str(target_repo), env=_git_env())
                return proc.returncode, proc.stdout, proc.stderr
            # no registry in the working tree yet: a local cannot-evaluate fails loudly
            rc, out, err = _explain_outcome(repo, base)
            check("explain/missing-registry-exit-2",
                  (rc, "cannot evaluate" in err, "cannot read the registry" in err),
                  (2, True, True))
            (repo / "tools" / "ci_checks.toml").write_text(SELECT_REGISTRY_TOML,
                                                           encoding="utf-8")
            rc, out, err = _explain_outcome(repo, base)
            check("explain/plan-selects-and-records-both-rename-sides",
                  (rc, "plan: selective over 4 changed path(s)" in out,
                   "'site/index.html'" in out, "'tools/index.html'" in out,
                   "'site/new.html'" in out, "'opf/site/a.css'" in out,
                   "job site: run" in out, "job tools: run" in out),
                  (0, True, True, True, True, True, True, True))
            rc, out, err = _explain_outcome(repo, "no-such-revision")
            check("explain/bad-revision-exit-2", (rc, "cannot evaluate" in err), (2, True))
            (repo / "tools" / "ci_checks.toml").write_text("format-version = [\n",
                                                           encoding="utf-8")
            rc, out, err = _explain_outcome(repo, base)
            check("explain/malformed-registry-exit-2",
                  (rc, "not valid TOML" in err), (2, True))
    # usage errors are cannot-evaluate through main, with no decision line; the usage text goes to
    # the error stream, captured here so this suite's own error stream stays clean for the
    # execution-reconciliation harness
    import contextlib
    import io

    def _usage_outcome(argv):
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            code = main(argv)
        return code, stream.getvalue().startswith("usage: ci_site_scope.py")
    check("launch/no-arguments-exit-2", _usage_outcome([]), (2, True))
    check("launch/unknown-flag-exit-2", _usage_outcome(["--unknown"]), (2, True))
    check("launch/merge-base-missing-value-exit-2", _usage_outcome(["--merge-base"]), (2, True))
    check("launch/empty-merge-base-exit-2", _usage_outcome(["--merge-base", ""]), (2, True))
    check("launch/ci-select-without-shadow-exit-2",
          [_usage_outcome(["--ci-select"]), _usage_outcome(["--shadow"]),
           _usage_outcome(["--ci-select", "--shadow", "extra"])],
          [(2, True)] * 3)
    check("launch/explain-missing-base-exit-2",
          [_usage_outcome(["--explain"]), _usage_outcome(["--explain", "--base"]),
           _usage_outcome(["--explain", "--base", ""])],
          [(2, True)] * 3)

    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("FAIL: ci_site_scope self-test")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("PASS: ci_site_scope self-test: {} unique checks executed (the pure decision admits only "
          "non-empty lists of plain repo-relative paths strictly below site/ or opf/site/ or "
          "exactly one of the three regenerated records, each boundary tested just inside, at, and "
          "outside; the train gate admits only a pull_request from a merge-train-* head of this "
          "repository; each rule patched out flips its vector; the CLI decides over synthetic "
          "repos, reads renames with detection off, and exits 2 on a bad revision, an "
          "option-shaped revision, a missing repo and a usage error; --ci-decide requires a HEAD "
          "whose raw commit object records exactly two parents, the second HEAD_SHA, deciding "
          "false on a root commit, a linear head, a three-parent merge, a mismatched or missing "
          "head SHA, a directory that is not a repository and an unparseable commit header, each "
          "with a note naming its cause, false on a valid two-parent merge that changes a tools/ "
          "file, and true on the matching site-only merge, also when the tool is extracted alone "
          "outside any repository, when the merge carries a gpgsig or a mergetag header, when an "
          "info/grafts file rewrites HEAD's parents and when a replace ref swaps the base; the "
          "shadow selection maps each changed path to the registry triggers that claim it per "
          "path segment, decides the full run on an empty diff, a bad path shape, a CI or "
          "selector file, an unclaimed path and a path claimed only by ** or another universal "
          "spelling (**/*, */**, **/**, and a bounded-depth * or */* at its own depth, judged "
          "by meaning), each selection rule patched out flips its vector, the site_only "
          "decision reads the same before and after a selection pass, --ci-select --shadow "
          "exits 0 and writes no output on every event, head shape and registry failure, every "
          "cannot-evaluate note names its cause and states that the run follows site_only, "
          "recording both sides of a rename, and --explain "
          "prints the same plan locally, exiting 2 on a bad revision or a missing or malformed "
          "registry); "
          "execution set "
          "reconciled against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


USAGE = ("usage: ci_site_scope.py [--merge-base REV | --ci-decide | --ci-select --shadow | "
         "--explain --base REV | --self-test | --execution-report ABS_PATH]")


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return self_test(argv[1])
    if len(argv) == 2 and argv[0] == "--merge-base" and argv[1]:
        return decide(ROOT, argv[1])
    if argv == ["--ci-decide"]:
        return ci_decide()
    if argv == ["--ci-select", "--shadow"]:
        return ci_select()
    if len(argv) == 3 and argv[0] == "--explain" and argv[1] == "--base" and argv[2]:
        return explain(ROOT, argv[2])
    print(USAGE, file=sys.stderr)
    return EXIT_CANNOT_EVALUATE


if __name__ == "__main__":
    if sys.argv[1:] in (["--ci-decide"], ["--ci-select", "--shadow"]):
        # Dispatched before any sibling import: the scope job runs this file alone, extracted
        # from the base tree into a directory outside the repository, so neither entry's path
        # may depend on a module that was not extracted with it.
        sys.exit(main(sys.argv[1:]))
    _exit_report().exit_with(main(sys.argv[1:]))
