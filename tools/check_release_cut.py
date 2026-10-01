#!/usr/bin/env python3
"""Release-intent gate; exit 0 pass, 1 policy finding, 2 cannot evaluate.

The optional .aiqt/release-cut.toml has exactly five fields:
  schema = 1
  operation = "append"
  previous-version = "1.0.5"
  next-version = "1.0.6"
  base-changelog-sha256 = "<SHA-256 of the complete comparison-base blob>"
Initialization instead requires operation = "initialize", previous-version = "",
and base-changelog-sha256 = "absent", against proven absence only.

A declaration is author-controlled intent, not authorization or readiness.
This reusable pack author tool is opt-in for adopters of this changelog schema.
For pull_request events or when GITHUB_BASE_REF is set, require a merge HEAD
whose raw first parent equals the resolved origin/GITHUB_BASE_REF tip, and use
that parent as the comparison base. Missing or mismatched bindings fail closed.
In GitHub Actions, only pull_request and push are supported. Push requires
--protected equal to refs/remotes/origin/GITHUB_REF_NAME (origin/BRANCH is
normalized), and --base equal to the workflow's PUSH_BEFORE event value.
The event must name a branch, with HEAD equal to its protected tracking tip.
Every raw first-parent transition in base..HEAD is checked separately, including
its declaration. A zero before is accepted only
for a proven root HEAD; new branches with pre-existing history are refused.
Local --protected must resolve to the same commit as origin/HEAD's target;
omitting it selects that target. Local --base must equal the unique merge base
of HEAD and that protected tip. A merge base equal to HEAD with a different
target is refused. Local tip checks compare the last first-parent transition.
Explicit refs must resolve and cannot be origin/HEAD; PR runs reject overrides.
An accepted --base enables the first-parent range check locally. Empty ranges,
non-first-parent bases and malformed or missing history fail closed.
Full event-payload authentication, authority of the workflow environment and
local origin/HEAD configuration, and remote freshness remain outside coverage.
Without --base, local tip checks cover only the last transition; other local/PR
checks compare endpoints. Execution-report emission and registration in
tools/selftest_checks.toml are deferred; wrapper coverage is not claimed.
Endpoint comparison cannot see transient changes inside a squash or prove
atomicity of its intermediate commits. Other version
sources and semantic release scope remain outside coverage. Existing version,
artifact, generation and release gates remain necessary. Trusted executable
provenance, gate code, repository configuration and protected review are assumed.
Committed/index bytes bypass checkout filters; working bytes are examined as
stored, so a transforming checkout filter can cause refusal. Submodules at
either input path are refused, not traversed. Snapshots are sampled separately,
not transactionally; concurrent writers must be excluded by the caller.

Usage: python3 -I -B tools/check_release_cut.py [--root DIR] [--protected REF] [--base OID]
       python3 -I -B tools/check_release_cut.py --self-test --red-on-revert
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opf" / "tools"))
from _semver import _parse  # noqa: E402

CHANGELOG = "changelog.toml"
DECLARATION = ".aiqt/release-cut.toml"
PATHS = (CHANGELOG, DECLARATION)
TIMEOUT = 30
OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
DECL_KEYS = {
    "schema", "operation", "previous-version", "next-version",
    "base-changelog-sha256",
}


class CannotEvaluate(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise CannotEvaluate(message)


def digest(raw):
    return "absent" if raw is None else hashlib.sha256(raw).hexdigest()


def document(raw, label):
    require(isinstance(raw, bytes), label + ": expected bytes")
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise CannotEvaluate(label + ": invalid UTF-8/TOML: " + str(exc)) from exc


def version(value, label):
    require(isinstance(value, str), label + ": expected string SemVer")
    parsed = _parse(value)
    require(parsed is not None, label + ": invalid bare SemVer")
    return parsed


def parse_changelog(raw, label):
    data = document(raw, label)
    require(set(data) == {"title", "note", "release"},
            label + ": expected title, note and release fields")
    for key in ("title", "note"):
        require(isinstance(data[key], str), label + ": " + key + " must be a string")
    rows = data["release"]
    require(isinstance(rows, list) and bool(rows), label + ": no release tables")
    for index, row in enumerate(rows):
        where = "{}: release[{}]".format(label, index)
        require(isinstance(row, dict), where + ": expected table")
        require(set(row) <= {"title", "version", "date", "items", "tag", "refs", "artifacts"},
                where + ": unsupported release field")
        version(row.get("version"), where)
        require(isinstance(row.get("title"), str), where + ": missing string title")
        if "date" in row:
            # datetime is a date subclass, but TOML timestamps are not local dates.
            require(isinstance(row["date"], str) or type(row["date"]) is datetime.date,
                    where + ": date must be a string or local date")
        if "tag" in row:
            require(isinstance(row["tag"], str), where + ": tag must be a string")
        for key in ("items", "refs"):
            if key in row:
                require(isinstance(row[key], list)
                        and all(isinstance(item, str) for item in row[key]),
                        where + ": " + key + " must be an array of strings")
        if "artifacts" in row:
            require(isinstance(row["artifacts"], dict)
                    and all(isinstance(value, str) for value in row["artifacts"].values()),
                    where + ": artifacts must map paths to strings")
    return rows


def parse_declaration(raw, label="declaration"):
    """Accept absent, TOML bytes, or an already parsed table; validate each form."""
    if raw is None:
        return None
    data = document(raw, label) if isinstance(raw, bytes) else raw
    require(isinstance(data, dict) and set(data) == DECL_KEYS,
            label + ": expected exactly the five declaration fields")
    require(type(data["schema"]) is int and data["schema"] == 1,
            label + ": unsupported schema (expected integer 1)")
    for key in DECL_KEYS - {"schema"}:
        require(isinstance(data[key], str), label + ": " + key + " must be a string")
    version(data["next-version"], label + ": next-version")
    operation = data["operation"]
    require(operation in ("append", "initialize"), label + ": unsupported operation")
    if operation == "initialize":
        require(data["previous-version"] == "" and data["base-changelog-sha256"] == "absent",
                label + ": initialize requires empty previous-version and absent digest")
    else:
        version(data["previous-version"], label + ": previous-version")
        require(re.fullmatch(r"[0-9a-f]{64}", data["base-changelog-sha256"]) is not None,
                label + ": append requires a lowercase SHA-256 digest")
    return dict(data)


def evaluate_transition(base_changelog_bytes_or_absent, head_changelog_bytes,
                        base_decl, head_decl):
    """Return (exit_code, detail). Only the caller can prove that None is absence."""
    try:
        base = ([] if base_changelog_bytes_or_absent is None else
                parse_changelog(base_changelog_bytes_or_absent, "base changelog"))
        head = parse_changelog(head_changelog_bytes, "head changelog")
        base_decl = parse_declaration(base_decl, "base declaration")
        head_decl = parse_declaration(head_decl, "head declaration")
    except (CannotEvaluate, RecursionError) as exc:
        return 2, "cannot evaluate: " + str(exc)
    # Parse every document before deciding policy, including present stale declarations.
    for label, rows in (("base", base), ("head", head)):
        parsed = [_parse(row["version"]) for row in rows]
        if any(right <= left for left, right in zip(parsed, parsed[1:])):
            return 1, label + ": duplicate or non-increasing release identities"
    identities = lambda rows: [row["version"] for row in rows]
    before, after = identities(base), identities(head)
    if after == before:
        if base_decl != head_decl:
            return 1, "declaration changed without a release transition"
        return 0, "release identities unchanged"
    if len(after) != len(before) + 1 or after[:-1] != before:
        return 1, "only one appended identity is permitted (no relabel, insert, delete or reorder)"
    expected_digest = digest(base_changelog_bytes_or_absent)
    if head_decl is not None and head_decl["base-changelog-sha256"] != expected_digest:
        return 1, "declaration digest does not match the complete base changelog"
    authorized = (
        head_decl is not None and head_decl != base_decl
        and head_decl["operation"] == ("append" if base else "initialize")
        and head_decl["previous-version"] == (base[-1]["version"] if base else "")
        and head_decl["next-version"] == head[-1]["version"]
    )
    if not authorized:
        return 1, "missing, unchanged or mismatched release declaration"
    # Diagnostics must remain total even when a policy guard is mutated away.
    return 0, "release transition accepted"


def git_environment():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C",
    })
    return env


def git(root, *args):
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-c", "core.commitGraph=false",
             "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True, env=git_environment(), timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git invocation: " + str(exc)) from exc
    require(result.returncode == 0, "git {}: {}".format(
        args[0], result.stderr.decode("utf-8", "replace").strip()))
    return result.stdout


def one_line(raw, label):
    require(raw.endswith(b"\n") and raw.count(b"\n") == 1,
            label + ": expected one output line")
    try:
        value = raw[:-1].decode("utf-8")
    except UnicodeError as exc:
        raise CannotEvaluate(label + ": non-UTF-8 output") from exc
    require(bool(value), label + ": empty output")
    return value


def oid(raw, label):
    require(OID.fullmatch(raw) is not None, label + ": invalid object identity")
    return raw.decode("ascii")


def resolve(root, ref):
    value = one_line(git(root, "rev-parse", "--verify", "--end-of-options",
                         ref + "^{commit}"), "resolve " + ref)
    return oid(value.encode(), ref)


def commit_header(root, revision):
    raw = git(root, "cat-file", "commit", revision)
    require(b"\n\n" in raw, revision + ": malformed commit")
    headers = raw.split(b"\n\n", 1)[0].splitlines()
    require(bool(headers) and headers[0].startswith(b"tree "), "missing commit tree")
    tree = oid(headers[0][5:], "commit tree")
    require(git(root, "cat-file", "-t", tree) == b"tree\n", "missing commit tree object")
    parents = []
    for line in headers:
        if line.startswith(b"parent "):
            parent = oid(line[7:], "commit parent")
            require(git(root, "cat-file", "-t", parent) == b"commit\n",
                    "missing parent commit object")
            parents.append(parent)
    return tree, parents


def records(raw, label):
    if not raw:
        return []
    require(raw.endswith(b"\0"), label + ": missing NUL terminator")
    return raw[:-1].split(b"\0")


def tree_blob(root, tree, path):
    """Walk exact path components; absence needs a successful parent-tree listing."""
    components = path.split("/")
    for index, component in enumerate(components):
        entries = records(git(root, "ls-tree", "-z", tree, "--", component), path)
        if not entries:
            return None
        require(len(entries) == 1, path + ": ambiguous tree entry")
        fields, separator, name = entries[0].partition(b"\t")
        require(separator and name == component.encode(), path + ": malformed tree entry")
        parts = fields.split(b" ")
        require(len(parts) == 3, path + ": malformed tree header")
        mode, kind, raw_oid = parts
        identity = oid(raw_oid, path)
        if index < len(components) - 1:
            require(mode == b"040000" and kind == b"tree", path + ": non-directory ancestor")
            tree = identity
        else:
            require(mode in (b"100644", b"100755") and kind == b"blob",
                    path + ": expected regular blob, not symlink/tree/gitlink")
            return git(root, "cat-file", "blob", identity)


def index_snapshot(root):
    # Do not ask diff/status whether a path changed: skip-worktree, assume-unchanged,
    # filters and an unstaged repair must not suppress the staged bytes.
    entries = records(git(root, "ls-files", "--stage", "-z"), "index")
    found = {}
    for entry in entries:
        header, separator, name = entry.partition(b"\t")
        require(bool(separator), "malformed index record")
        for path in PATHS:
            encoded = path.encode()
            related = (name == encoded or name.startswith(encoded + b"/")
                       or encoded.startswith(name + b"/"))
            if not related:
                continue
            fields = header.split(b" ")
            require(len(fields) == 3, path + ": malformed index header")
            mode, raw_oid, stage = fields
            require(name == encoded and stage == b"0"
                    and mode in (b"100644", b"100755") and path not in found,
                    path + ": unmerged or non-regular index entry")
            found[path] = git(root, "cat-file", "blob", oid(raw_oid, path))
    return tuple(found.get(path) for path in PATHS)


def _close_fd_propagating(fd):
    """Close a descriptor on a FAIL-CLOSED path: the close error PROPAGATES. Single close (P1, #378):
    exactly ONE os.close; if it raises, the number counts as released (close(2) on Linux releases it early,
    even when the close then reports EINTR or EIO, and a retry can close another thread's reused
    descriptor: man 2 close), so it is never probed or closed again, and the ORIGINAL close error
    propagates unchanged. Inlined from opf/tools/_journal._close_fd_propagating (the same body) so this
    tool keeps working without opf/tools present (copied, mutated, or shipped alone)."""
    os.close(fd)


def _close_fd_yielding(fd):
    """Close a descriptor from an `except` handler or a `finally` block without letting a close error
    REPLACE the exception already in flight there: when an exception is unwinding through, or being handled
    in, the CALLING frame, the same single close still runs (P1: one os.close, the number released either
    way and never touched again) but its close error is dropped so the ORIGINAL exception keeps
    propagating; on the normal path this is exactly _close_fd_propagating, so a close error still fails
    closed. Inlined from opf/tools/_journal._close_fd_yielding (the same body) so this tool keeps working
    without opf/tools present."""
    tb = sys.exc_info()[2]
    if tb is None or tb.tb_frame is not sys._getframe(1):
        _close_fd_propagating(fd)
        return
    try:
        _close_fd_propagating(fd)
    except OSError:
        pass                                      # the in-flight exception wins; the fd was still released


def working_blob(root, path):
    """POSIX no-follow, descriptor-relative traversal; unsupported platforms refuse. The final component's
    file object is made with closefd=False, so it never closes fd: not when os.fdopen refuses a directory
    (FileIO leaves fd open), not when it fails after creating its raw file (io.open closes that file), and
    not when a dropped object is collected. The finally's close is the one close of fd on every path, so
    who closes fd is never in doubt (P1, #378)."""
    require(hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd, "safe filesystem traversal unavailable")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        components = path.split("/")
        for component in components[:-1]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            except FileNotFoundError:
                return None
            parent, directory = directory, child   # held first: a raising close cannot strand child
            _close_fd_propagating(parent)
        try:
            fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            with os.fdopen(fd, "rb", closefd=False) as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),
                        path + ": working input is not a regular file")
                return stream.read()
        finally:
            _close_fd_yielding(fd)
    finally:
        _close_fd_yielding(directory)


def first_parent_range(root, head, before):
    """Bind a nonempty range to raw commit parents, never graph-limited traversal."""
    before = oid(before.encode(), "--base")
    require(len(before) == len(head), "--base object format differs from HEAD")
    if before == "0" * len(head):
        require(not commit_header(root, head)[1],
                "zero --base requires a proven root HEAD")
        return [(None, head)]
    require(resolve(root, before) == before, "--base must name a commit object")
    require(before != head, "--base must precede HEAD")
    transitions = []
    current = head
    while current != before:
        _, parents = commit_header(root, current)
        require(bool(parents), "--base is not on HEAD's first-parent chain")
        parent = parents[0]
        transitions.append((parent, current))
        current = parent
    return list(reversed(transitions))


def default_protected_ref(root):
    protected = one_line(git(root, "symbolic-ref", "refs/remotes/origin/HEAD"), "origin/HEAD")
    require(protected.startswith("refs/remotes/origin/")
            and protected != "refs/remotes/origin/HEAD",
            "origin/HEAD does not name an origin tracking branch")
    git(root, "check-ref-format", protected)
    return protected


def context(root, protected=None, before=None):
    root = Path(one_line(git(root, "rev-parse", "--show-toplevel"), "repository root"))
    require(root.is_absolute(), "repository root must be absolute")
    require(git(root, "rev-parse", "--is-shallow-repository") == b"false\n",
            "shallow or unrecognizable ancestry")
    graft_path = Path(one_line(git(root, "rev-parse", "--path-format=absolute",
                                   "--git-path", "info/grafts"), "grafts path"))
    require(graft_path.is_absolute(), "non-absolute grafts path")
    # Even an empty or symlinked graft file is refused: no graph substitution surface.
    try:
        graft_path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise CannotEvaluate("grafts file present; ancestry cannot be trusted")
    base_ref = os.environ.get("GITHUB_BASE_REF", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    actions = os.environ.get("GITHUB_ACTIONS") == "true"
    if actions:
        require(event in ("pull_request", "push"), "unsupported GitHub Actions event: " + event)
        if event == "push":
            require(protected is not None and before is not None,
                    "push requires explicit --protected and --base")
    ci_context = event == "pull_request" or bool(base_ref)
    require(not ci_context or before is None, "PR comparison rejects --base")
    require(not ci_context or bool(base_ref), "PR comparison requires GITHUB_BASE_REF")
    if protected is not None:
        require(not ci_context, "PR comparison derives its target from GITHUB_BASE_REF")
        if protected.startswith("origin/"):
            protected = "refs/remotes/" + protected
        require(protected.startswith("refs/")
                and protected != "refs/remotes/origin/HEAD",
                "--protected must name a full ref or origin branch, not origin/HEAD")
        git(root, "check-ref-format", protected)
    elif base_ref:
        protected = "refs/remotes/origin/" + base_ref
        git(root, "check-ref-format", protected)
        require(protected != "refs/remotes/origin/HEAD",
                "GITHUB_BASE_REF must name an origin tracking branch")
        # An explicit but unresolved PR target must not fall back to another branch.
    else:
        protected = default_protected_ref(root)
    target, head = resolve(root, protected), resolve(root, "HEAD")
    if not ci_context:
        if actions and event == "push":
            branch = os.environ.get("GITHUB_REF_NAME", "")
            require(os.environ.get("GITHUB_REF_TYPE") == "branch" and bool(branch),
                    "push requires a branch GITHUB_REF_NAME and GITHUB_REF_TYPE=branch")
            expected = "refs/remotes/origin/" + branch
            git(root, "check-ref-format", expected)
            require(protected == expected,
                    "push --protected must equal " + expected)
            event_before = oid(os.environ.get("PUSH_BEFORE", "").encode(), "PUSH_BEFORE")
            require(before == event_before, "push --base must equal workflow PUSH_BEFORE")
        else:
            default = default_protected_ref(root)
            require(target == resolve(root, default),
                    "local --protected must resolve to the origin/HEAD target: " + default)
    # Traverse the complete reachable commit ancestry, with shallow/grafts/graph and
    # replacement substitution disabled. Missing parents make rev-list fail.
    ancestry = git(root, "rev-list", "--parents", head, target)
    require(bool(ancestry) and ancestry.endswith(b"\n"), "unreadable ancestry")
    for line in ancestry.splitlines():
        for value in line.split(b" "):
            oid(value, "ancestry")
    head_tree, parents = commit_header(root, head)
    transitions = []
    if ci_context:
        require(len(parents) >= 2, "PR comparison requires a merge HEAD")
        require(parents[0] == target,
                "PR merge first parent does not match " + protected)
        base = parents[0]
    else:
        bases = git(root, "merge-base", "--all", head, target).splitlines()
        require(len(bases) == 1, "expected exactly one merge base")
        base = oid(bases[0], "merge base")
        require(base != head or head == target,
                "HEAD is behind the target; no honest comparison base")
        if actions and event == "push":
            require(head == target, "push HEAD does not match --protected target")
        if before is not None:
            if not actions:
                require(before == base, "local --base must equal the protected merge base: " + base)
            # The raw first-parent walk also proves nonzero --base is an ancestor
            # of the push target, whose identity was bound to HEAD above.
            transitions = first_parent_range(root, head, before)
            base = transitions[0][0]
        elif head == target:
            # Non-PR target identity was checked even when --base was supplied.
            base = parents[0] if parents else None
    base_tree = commit_header(root, base)[0] if base is not None else None
    return root, {
        "head": head, "protected_ref": protected, "target": target,
        "base": base, "head_tree": head_tree, "base_tree": base_tree,
        "transitions": transitions,
    }


def local_report(root, protected=None, before=None):
    root, binding = context(root, protected, before)
    comparisons = []
    snapshot_base = binding["base_tree"]
    for parent, revision in binding["transitions"]:
        snapshot_base = commit_header(root, parent)[0] if parent is not None else None
        comparisons.append(("commit:" + revision, snapshot_base, commit_header(root, revision)[0]))
    results = []
    names = ("HEAD", "index", "working")
    comparisons.extend((name, snapshot_base, binding["head_tree"]) for name in names)
    for name, base_tree, head_tree in comparisons:
        try:
            base = ((None, None) if base_tree is None else
                    tuple(tree_blob(root, base_tree, path) for path in PATHS))
            if name == "HEAD" or name.startswith("commit:"):
                candidate = tuple(tree_blob(root, head_tree, path) for path in PATHS)
            elif name == "index":
                candidate = index_snapshot(root)
            else:
                candidate = tuple(working_blob(root, path) for path in PATHS)
            code, detail = evaluate_transition(base[0], candidate[0], base[1], candidate[1])
            results.append({
                "snapshot": name, "code": code, "detail": detail,
                "base_changelog_sha256": digest(base[0]),
                "head_changelog_sha256": digest(candidate[0]),
                "base_declaration_sha256": digest(base[1]),
                "head_declaration_sha256": digest(candidate[1]),
            })
        except (OSError, ValueError, RecursionError) as exc:
            results.append({"snapshot": name, "code": 2, "detail": str(exc)})
    return {"code": max(row["code"] for row in results), "context": binding,
            "snapshots": results}


# SELF-TEST: mutation targets are restricted to the production prefix above.


def fixture_changelog(versions, item="note"):
    text = 'title = "Changelog"\nnote = "Fixture"\n'
    for value in versions:
        text += ('\n[[release]]\ntitle = "Release"\nversion = ' + json.dumps(value)
                 + '\ndate = "2000-01-01"\nitems = [' + json.dumps(item) + ']\n')
    return text.encode()


def fixture_declaration(base, previous="1.0.0", following="1.1.0", **changes):
    data = {
        "schema": 1, "operation": "initialize" if base is None else "append",
        "previous-version": "" if base is None else previous,
        "next-version": following, "base-changelog-sha256": digest(base),
    }
    data.update(changes)
    return ("\n".join(key + " = " + json.dumps(value) for key, value in data.items()) + "\n").encode()


def test_cases():
    """Transition policy, local ancestry, PR merge binding and explicit push refs."""
    base = fixture_changelog(["1.0.0"])
    cut = fixture_changelog(["1.0.0", "1.1.0"])
    declaration = fixture_declaration(base)
    history = fixture_declaration(base, **{"base-changelog-sha256": "f" * 64})
    cases = {}

    def add(name, expected, before=base, after=base, old=None, new=None, tweak=None):
        require(name not in cases, "duplicate self-test ID: " + name)
        cases[name] = (expected, before, after, old, new, tweak)

    add("ordinary-content", 0)
    add("notes", 0, after=fixture_changelog(["1.0.0"], "curated note"))
    add("title-date", 0, after=base.replace(b'"Release"', b'"Published"')
        .replace(b'"2000-01-01"', b'"2000-02-02"'))
    native = base.replace(b'"2000-01-01"', b"2000-01-01")
    add("native-date-maintenance", 0, before=native,
        after=native.replace(b'items = ["note"]', b'items = ["curated note"]'))
    add("native-date-edit", 0, before=native,
        after=native.replace(b"2000-01-01", b"2000-02-02"))
    add("date-string-to-native", 0, after=native)
    add("date-native-to-string", 0, before=native)
    add("formatting", 0, after=b"# formatting only\n\n" + base)
    add("metadata", 0, after=base + (
        'tag = "v1.0.0"\nrefs = ["pr:17"]\n[release.artifacts]\n'
        '"site/downloads/fixture.zip" = "sha256:' + "0" * 64 + '"\n').encode())
    add("historical-declaration", 0, old=history, new=history)
    add("cosmetic-declaration", 0, old=history, new=b"# reformatted\n" + history)
    add("declared-append", 0, after=cut, new=declaration)
    add("initialize", 0, before=None, new=fixture_declaration(None, following="1.0.0"))
    add("root-initialize", 0, before=None, new=fixture_declaration(None, following="1.0.0"),
        tweak="root")
    add("first-publication", 0, after=base + b'tag = "v1.0.0"\n')
    add("consumer-edits", 0, after=cut, new=declaration, tweak="consumer")
    add("merge-cut", 0, after=cut, new=declaration, tweak="merge")
    add("squash-cut", 0, after=cut, new=declaration, tweak="squash")
    add("undeclared", 1, after=cut)  # Fixture VERSION is consistent with the appended identity.
    add("unchanged-declaration", 1, after=cut, old=declaration, new=declaration)
    add("stale-digest", 1, after=cut, new=history)
    add("wrong-previous", 1, after=cut, new=fixture_declaration(base, previous="0.9.0"))
    add("wrong-next", 1, after=cut, new=fixture_declaration(base, following="1.2.0"))
    add("false-initialize", 1, after=cut, new=fixture_declaration(None))
    add("undeclared-initialize", 1, before=None)
    # Without a declaration change, the identity mutant reaches this case's oracle.
    add("relabel", 1, after=fixture_changelog(["1.1.0"]))
    add("declared-relabel", 1, after=fixture_changelog(["1.1.0"]), new=declaration)
    longer = fixture_changelog(["1.0.0", "1.2.0"])
    for name, versions in (
        ("historical-rewrite", ["1.0.1", "1.2.0"]),
        ("insertion", ["1.0.0", "1.1.0", "1.2.0"]),
        ("deletion", ["1.0.0"]),
        ("reorder", ["1.2.0", "1.0.0"]),
        ("duplicate", ["1.0.0", "1.2.0", "1.2.0"]),
        ("decrease", ["1.0.0", "1.2.0", "1.1.0"]),
        ("multi-append", ["1.0.0", "1.2.0", "1.3.0", "1.4.0"]),
    ):
        add(name, 1, before=longer, after=fixture_changelog(versions),
            new=fixture_declaration(longer, previous="1.2.0", following=versions[-1]))
    add("declaration-only", 1, new=declaration)
    add("declaration-delete", 1, old=history)
    add("bad-base-order", 1, before=fixture_changelog(["1.1.0", "1.0.0"]))
    bad_logs = {
        "toml": b"[",
        "utf8": b"\xff",
        "empty": b"",
        "no-rows": b'title = "C"\nnote = "N"\nrelease = []\n',
        "missing-version": base.replace(b'version = "1.0.0"\n', b""),
        "invalid-version": base.replace(b'"1.0.0"', b'"01.0.0"'),
        "typed-version": base.replace(b'"1.0.0"', b"1"),
        "renderer-title": base.replace(b'title = "Release"', b"title = 7"),
        "renderer-items": base.replace(b'items = ["note"]', b"items = [7]"),
        "date-number": base.replace(b'"2000-01-01"', b"7"),
        "date-timestamp": base.replace(b'"2000-01-01"', b"2000-01-01T00:00:00"),
        "date-offset-timestamp": base.replace(b'"2000-01-01"', b"2000-01-01T00:00:00Z"),
        "tag-native-date": base + b"tag = 2000-01-01\n",
        "metadata-refs": base + b"refs = 7\n",
        "metadata-artifacts": base + b'artifacts = ["wrong"]\n',
    }
    for label, raw in bad_logs.items():
        add("head-" + label, 2, after=raw)
        add("base-" + label, 2, before=raw)
    bad_declarations = {
        "toml": b"[", "utf8": b"\xff", "empty": b"",
        "schema": declaration.replace(b"schema = 1", b"schema = 2"),
        "boolean": declaration.replace(b"schema = 1", b"schema = true"),
        "field-type": declaration.replace(b'next-version = "1.1.0"', b"next-version = 7"),
        "unknown": declaration + b"extra = 1\n",
        "operation": declaration.replace(b'"append"', b'"publish"'),
        "digest": declaration.replace(digest(base).encode(), b"absent"),
        "semver": declaration.replace(b'"1.1.0"', b'"1.1.0-rc1"'),
    }
    for label, raw in bad_declarations.items():
        add("head-decl-" + label, 2, new=raw)
        add("base-decl-" + label, 2, old=raw)
    for name in ("missing-head", "missing-object", "broken-ancestry", "unreadable",
                 "head-symlink", "head-gitlink", "working-symlink", "working-directory",
                 "parent-symlink", "unmerged-index", "unresolved-base", "ambiguous-base",
                 "shallow", "grafts", "git-failure", "ci-missing-protected",
                 "ci-missing-base-ref", "ci-invalid-base-ref"):
        add(name, 2, tweak=name)
    add("ci-origin-head", 2, tweak="ci-origin-head")
    add("ci-nonmerge", 2, tweak="ci-nonmerge")
    add("ci-base-ref", 0, tweak="ci-base-ref")
    add("ci-merge-cut", 0, after=cut, new=declaration, tweak="ci-merge")
    add("ci-merge-undeclared", 1, after=cut, tweak="ci-merge")
    relabel = fixture_changelog(["1.0.0", "1.2.0"])
    stale_declaration = fixture_declaration(base, following="1.2.0")
    for name, expected in (("ci-stale-tracking", 2), ("ci-fresh-tracking", 1),
                           ("base-ref-only-stale", 2), ("base-ref-only-fresh", 1)):
        add(name, expected, after=relabel, new=stale_declaration, tweak=name)
    add("push-protected", 0, tweak="push-protected")
    add("push-protected-full", 0, tweak="push-protected-full")
    add("push-declared", 0, after=cut, new=declaration, tweak="push-protected")
    add("push-undeclared", 1, after=cut, tweak="push-protected")
    for name in ("push-bare", "push-empty-ref", "push-invalid-ref", "push-missing-ref",
                 "push-origin-head", "push-origin-head-full", "ci-explicit-protected"):
        add(name, 2, tweak=name)
    for name in ("descendant-branch", "descendant-tag", "candidate-self", "behind-default",
                 "ancestor-branch", "ancestor-origin", "ancestor-tag", "ancestor-base",
                 "candidate-self-base", "local-missing-default"):
        add(name, 2, after=cut, tweak=name)
    for name in ("local-alias-branch", "local-alias-tag", "local-base"):
        add(name, 1, after=cut, tweak=name)
    add("local-base-declared", 0, after=cut, new=declaration, tweak="local-base")
    for name in ("push-identity-branch", "push-identity-origin", "push-identity-tag",
                 "push-before-narrowed", "push-before-missing", "push-before-invalid",
                 "push-ref-name-missing", "push-ref-type-tag"):
        add(name, 2, after=cut, tweak=name)
    for name in ("event-dispatch", "event-merge-group", "event-missing"):
        add(name, 2, tweak=name)
    add("push-noop-undeclared", 1, after=cut, tweak="push-noop")
    add("push-noop-declared", 0, after=cut, new=declaration, tweak="push-noop")
    add("push-two-cuts", 0, after=cut, new=declaration, tweak="push-two-cuts")
    add("push-repaired", 1, after=cut, tweak="push-repaired")
    add("push-root", 0, before=None, new=fixture_declaration(None, following="1.0.0"),
        tweak="push-root")
    for name in ("push-no-base", "push-base-empty", "push-base-short", "push-base-missing",
                 "push-base-zero", "push-base-self", "push-base-second-parent",
                 "push-base-unrelated", "push-base-tag-object", "push-base-wrong-format",
                 "push-target-mismatch", "push-base-broken-history", "ci-explicit-base"):
        add(name, 2, tweak=name)
    add("protected-tip", 1, after=cut, tweak="protected")
    add("staged-hidden", 1, tweak="staged")
    return cases


def fixture_environment(home):
    # Deterministic, private configuration, and no inherited Git redirection/config.
    env = git_environment()
    for key in list(env):
        if key == "CI" or key.startswith("GITHUB_"):
            del env[key]
    env.update({
        "HOME": str(home), "XDG_CONFIG_HOME": str(home / "xdg"),
        "GIT_AUTHOR_NAME": "Release Fixture", "GIT_COMMITTER_NAME": "Release Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    })
    return env


def fixture_repo(parent, name, case, env):
    expected, before, after, old, new, tweak = case
    root = parent / name
    root.mkdir()

    def command(*args, payload=None):
        result = subprocess.run(
            ["git", "-C", str(root), "-c", "core.hooksPath=" + str(parent / "no-hooks"),
             "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
            input=payload, capture_output=True, env=env, timeout=TIMEOUT,
        )
        require(result.returncode == 0,
                name + ": fixture git failed: " + result.stderr.decode("utf-8", "replace"))
        return result.stdout.strip().decode("ascii")

    def write(log, decl):
        for path, data in ((CHANGELOG, log), (DECLARATION, decl)):
            target = root / path
            if data is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        # The undeclared-append fixture also carries a consistent generated VERSION.
        versions = re.findall(rb'^version = "([0-9.]+)"$', log or b"", re.MULTILINE)
        (root / "VERSION").write_bytes((versions[-1] if versions else b"0.0.0") + b"\n")

    def commit(message):
        # This broad staging is confined to a newly owned, isolated fixture repository.
        command("add", "--all", "--", ".")
        command("commit", "--allow-empty", "-q", "-m", message)
        return command("rev-parse", "HEAD")

    command("init", "-q", "-b", "main", "--template=")
    (root / "seed.txt").write_text("fixture\n", encoding="utf-8")
    (root / ".aiqt").mkdir()
    if tweak in ("root", "push-root"):
        write(after, new)
        head = commit("initialized")
        command("update-ref", "refs/remotes/origin/main", head)
        command("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        return root
    write(before, old)
    base = commit("base")
    command("update-ref", "refs/remotes/origin/main", base)
    command("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    command("switch", "-q", "-c", "feature")
    binding_fixture = tweak in (
        "ci-stale-tracking", "ci-fresh-tracking",
        "base-ref-only-stale", "base-ref-only-fresh",
    )
    if binding_fixture:
        # The target advanced, but the candidate declares against the old tracking tip.
        write(fixture_changelog(["1.0.0", "1.1.0"]), None)
        first_parent = commit("advanced target")
    write(after, new)
    (root / "consumer.txt").write_text("ordinary consumer edit\n", encoding="utf-8")
    if tweak == "squash":
        # A preparation commit exists, but the reviewed endpoint is one squashed commit.
        write(after, old)
        commit("preparation")
        write(after, new)
    head = commit("candidate")
    tree = command("rev-parse", head + "^{tree}")
    if binding_fixture:
        head = command("commit-tree", tree, "-p", first_parent, "-p", head,
                       payload=b"PR merge\n")
        command("update-ref", "HEAD", head)
        if tweak in ("ci-fresh-tracking", "base-ref-only-fresh"):
            command("update-ref", "refs/remotes/origin/main", first_parent)
    elif tweak in ("merge", "squash", "ci-merge", "ci-base-ref"):
        args = ["commit-tree", tree, "-p", base]
        if tweak in ("merge", "ci-merge", "ci-base-ref"):
            other = command("commit-tree", tree, "-p", base, payload=b"other\n")
            args += ["-p", other]
        head = command(*args, payload=b"integration\n")
        command("update-ref", "HEAD", head)
        if tweak == "ci-base-ref":
            command("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    elif tweak == "staged":
        (root / CHANGELOG).write_bytes(fixture_changelog(["1.0.0", "1.1.0"]))
        command("add", "--", CHANGELOG)
        (root / CHANGELOG).write_bytes(after)
    elif tweak in ("ancestor-branch", "ancestor-origin", "ancestor-tag", "ancestor-base",
                   "candidate-self-base"):
        for ref in ("refs/heads/attacker", "refs/remotes/origin/attacker", "refs/tags/attacker"):
            command("update-ref", ref, head)
        commit("no-op after undeclared append")
    elif tweak in ("local-alias-branch", "local-alias-tag"):
        command("update-ref", "refs/heads/alias", base)
        command("update-ref", "refs/tags/alias", base)
    elif tweak == "local-missing-default":
        command("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    elif tweak in ("descendant-branch", "descendant-tag", "candidate-self", "behind-default"):
        if tweak == "candidate-self":
            commit("no-op after undeclared append")
        else:
            future = command("commit-tree", tree, "-p", head, payload=b"descendant\n")
            ref = "refs/tags/attacker" if tweak == "descendant-tag" else "refs/heads/attacker"
            command("update-ref", ref, future)
            if tweak == "behind-default":
                command("update-ref", "refs/remotes/origin/main", future)
    elif tweak == "protected" or (tweak and tweak.startswith("push-")):
        if tweak in ("push-identity-branch", "push-identity-origin", "push-identity-tag",
                     "push-before-narrowed"):
            command("update-ref", "refs/heads/before", head)
            head = commit("no-op after undeclared append")
            for ref in ("refs/heads/attacker", "refs/remotes/origin/attacker", "refs/tags/attacker"):
                command("update-ref", ref, head)
        elif tweak == "push-noop":
            head = commit("no-op after cut")
        elif tweak == "push-two-cuts":
            write(fixture_changelog(["1.0.0", "1.1.0", "1.2.0"]),
                  fixture_declaration(after, previous="1.1.0", following="1.2.0"))
            head = commit("second declared cut")
        elif tweak == "push-repaired":
            write(before, old)
            head = commit("revert undeclared cut")
        elif tweak in ("push-base-second-parent", "push-base-unrelated"):
            other = command("commit-tree", tree, payload=b"other root\n")
            command("update-ref", "refs/heads/before", other)
            if tweak == "push-base-second-parent":
                head = command("commit-tree", tree, "-p", head, "-p", other,
                               payload=b"merge\n")
                command("update-ref", "HEAD", head)
        elif tweak == "push-base-tag-object":
            command("tag", "-a", "before", base, "-m", "annotated before")
        elif tweak == "push-base-broken-history":
            (root / ".git" / "objects" / base[:2] / base[2:]).unlink()
        if tweak != "push-target-mismatch":
            command("update-ref", "refs/remotes/origin/main", head)
        if tweak != "protected" and tweak not in ("push-origin-head", "push-origin-head-full"):
            command("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    elif tweak == "missing-head":
        (root / CHANGELOG).unlink()
        commit("delete changelog")
    elif tweak in ("head-symlink", "working-symlink", "working-directory"):
        (root / CHANGELOG).unlink()
        if tweak == "working-directory":
            (root / CHANGELOG).mkdir()
        else:
            (root / CHANGELOG).symlink_to("seed.txt")
        if tweak == "head-symlink":
            commit("symlink changelog")
    elif tweak == "parent-symlink":
        (root / ".aiqt").rmdir()
        (root / "outside").mkdir()
        (root / ".aiqt").symlink_to("outside", target_is_directory=True)
    elif tweak == "head-gitlink":
        command("update-index", "--cacheinfo", "160000," + base + "," + CHANGELOG)
        command("commit", "-q", "-m", "gitlink input")
    elif tweak == "unmerged-index":
        blob = command("rev-parse", head + ":" + CHANGELOG)
        command("update-index", "--force-remove", "--", CHANGELOG)
        command("update-index", "--index-info",
                payload=("100644 " + blob + " 1\t" + CHANGELOG + "\n").encode())
    elif tweak == "unresolved-base":
        command("update-ref", "-d", "refs/remotes/origin/main")
    elif tweak == "ci-missing-protected":
        command("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    elif tweak == "ambiguous-base":
        a = command("commit-tree", tree, "-p", base, payload=b"a\n")
        b = command("commit-tree", tree, "-p", base, payload=b"b\n")
        first = command("commit-tree", tree, "-p", a, "-p", b, payload=b"first\n")
        second = command("commit-tree", tree, "-p", b, "-p", a, payload=b"second\n")
        command("update-ref", "refs/remotes/origin/main", first)
        command("update-ref", "HEAD", second)
    elif tweak == "shallow":
        (root / ".git" / "shallow").write_text(base + "\n", encoding="ascii")
    elif tweak == "grafts":
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "grafts").write_text(base + "\n", encoding="ascii")
    elif tweak in ("missing-object", "broken-ancestry"):
        identity = (command("rev-parse", head + ":" + CHANGELOG)
                    if tweak == "missing-object" else base)
        (root / ".git" / "objects" / identity[:2] / identity[2:]).unlink()
    return root


# Inject EACCES at the opened file's read boundary, even under root; the real
# traversal/open/fstat path still runs. This is fault injection, not a chmod claim.
UNREADABLE_DRIVER = """
import os, runpy, sys
from unittest.mock import patch
original = os.fdopen
class Unreadable:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def __exit__(self, *args): return self.stream.__exit__(*args)
    def fileno(self): return self.stream.fileno()
    def read(self): raise PermissionError("injected unreadable working file")
def fdopen(*args, **kwargs): return Unreadable(original(*args, **kwargs))
sys.argv = sys.argv[1:]
with patch("os.fdopen", side_effect=fdopen):
    runpy.run_path(sys.argv[0], run_name="__main__")
"""


def observe(script, root, tweak, env):
    env = dict(env)
    if tweak == "git-failure":
        empty = root / "empty-path"
        empty.mkdir(exist_ok=True)
        env["PATH"] = str(empty)
    if tweak and tweak.startswith("ci-"):
        env.update({"CI": "true", "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "pull_request"})
        if tweak in ("ci-base-ref", "ci-merge", "ci-nonmerge",
                     "ci-stale-tracking", "ci-fresh-tracking", "ci-explicit-protected",
                     "ci-explicit-base"):
            env["GITHUB_BASE_REF"] = "main"
        elif tweak == "ci-missing-base-ref":
            env["GITHUB_BASE_REF"] = "missing"
        elif tweak == "ci-invalid-base-ref":
            env["GITHUB_BASE_REF"] = "../main"
    if tweak in ("base-ref-only-stale", "base-ref-only-fresh"):
        env["GITHUB_BASE_REF"] = "main"
    args = [sys.executable, "-I", "-B"]
    if tweak == "unreadable":
        args += ["-c", UNREADABLE_DRIVER]
    args += [str(script), "--root", str(root)]
    if tweak and tweak.startswith("push-"):
        env.update({"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push",
                    "GITHUB_BASE_REF": "", "GITHUB_REF_NAME": "main",
                    "GITHUB_REF_TYPE": "branch"})
        event_before = one_line(git(root, "rev-parse", "refs/heads/main"), "fixture event before")
        env["PUSH_BEFORE"] = "0" * len(event_before) if tweak == "push-root" else event_before
        if tweak == "push-before-missing":
            del env["PUSH_BEFORE"]
        elif tweak == "push-before-invalid":
            env["PUSH_BEFORE"] = "invalid"
        elif tweak == "push-ref-name-missing":
            del env["GITHUB_REF_NAME"]
        elif tweak == "push-ref-type-tag":
            env["GITHUB_REF_TYPE"] = "tag"
        if tweak != "push-bare":
            protected = {
                "push-protected-full": "refs/remotes/origin/main",
                "push-identity-branch": "refs/heads/attacker",
                "push-identity-origin": "origin/attacker",
                "push-identity-tag": "refs/tags/attacker",
                "push-empty-ref": "",
                "push-invalid-ref": "origin/../main",
                "push-missing-ref": "origin/missing",
                "push-origin-head": "origin/HEAD",
                "push-origin-head-full": "refs/remotes/origin/HEAD",
            }.get(tweak, "origin/main")
            args += ["--protected", protected]
        if tweak != "push-no-base":
            # refs/heads/main retains the pre-push commit even after tracking refs move.
            before = one_line(git(root, "rev-parse", "refs/heads/main"), "fixture before")
            if tweak in ("push-root", "push-base-zero"):
                before = "0" * len(before)
            elif tweak == "push-base-empty":
                before = ""
            elif tweak == "push-base-short":
                before = before[:12]
            elif tweak == "push-base-missing":
                before = "f" * len(before)
            elif tweak == "push-base-self":
                before = resolve(root, "HEAD")
            elif tweak in ("push-base-second-parent", "push-base-unrelated"):
                before = resolve(root, "refs/heads/before")
            elif tweak == "push-base-tag-object":
                before = one_line(git(root, "rev-parse", "refs/tags/before"), "tag object")
            elif tweak == "push-base-wrong-format":
                before = "0" * (64 if len(before) == 40 else 40)
            elif tweak == "push-before-narrowed":
                before = resolve(root, "refs/heads/before")
            # Existing range fixtures bind their event to the supplied invalid range,
            # so the range validator, not the new equality guard, remains exercised.
            if tweak.startswith("push-base-"):
                env["PUSH_BEFORE"] = before
            args += ["--base", before]
    if tweak == "ci-explicit-protected":
        args += ["--protected", "origin/main"]
    if tweak == "ci-explicit-base":
        args += ["--base", resolve(root, "refs/heads/main")]
    if tweak in ("descendant-branch", "descendant-tag", "candidate-self"):
        args += ["--protected", {
            "descendant-branch": "refs/heads/attacker",
            "descendant-tag": "refs/tags/attacker",
            "candidate-self": "refs/heads/feature",
        }[tweak]]
    local_refs = {
        "ancestor-branch": "refs/heads/attacker",
        "ancestor-origin": "origin/attacker",
        "ancestor-tag": "refs/tags/attacker",
        "candidate-self-base": "refs/heads/feature",
        "local-alias-branch": "refs/heads/alias",
        "local-alias-tag": "refs/tags/alias",
        "local-missing-default": "origin/main",
    }
    if tweak in local_refs:
        args += ["--protected", local_refs[tweak]]
    if tweak in ("ancestor-base", "candidate-self-base"):
        args += ["--base", resolve(root, "refs/heads/attacker")]
    elif tweak == "local-base":
        args += ["--base", resolve(root, "refs/heads/main")]
    if tweak and tweak.startswith("event-"):
        env.update({"GITHUB_ACTIONS": "true", "GITHUB_BASE_REF": "", "GITHUB_EVENT_NAME": {
            "event-dispatch": "workflow_dispatch", "event-merge-group": "merge_group",
            "event-missing": "",
        }[tweak]})
    result = subprocess.run(args, capture_output=True, env=env, timeout=TIMEOUT)
    require(result.returncode in (0, 1, 2) and not result.stderr,
            "self-test child crashed: " + result.stderr.decode("utf-8", "replace"))
    try:
        report = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise CannotEvaluate("self-test child did not emit a verdict") from exc
    require(type(report.get("code")) is int and report["code"] == result.returncode,
            "self-test child verdict/exit mismatch")
    return report


class OracleFailure(AssertionError):
    def __init__(self, case_id):
        self.case_id = case_id
        super().__init__(case_id)


def check(case_id, condition):
    if not condition:
        raise OracleFailure(case_id)


def _close_vectors(base):
    """#378: the vectors for this tool's _close_fd_yielding copy and its sites in working_blob: the directory
    close, and the final component's close, which the finally makes on every path, including when os.fdopen
    refuses a directory and when os.fdopen fails after its raw file exists (io.open then closes that file).
    A close that fails while an exception unwinds lets that exception through as the same object; one that
    fails on the normal path raises; neither leaves a descriptor open. The wrapping-failure vector is also
    run against the pre-fix body (fdopen taking fd, its handler closing fd) and must be red by NOFIRE alone:
    the failed wrapper's own close released fd first, so the handler's close was a second close. Returns
    (failures, runs)."""
    import inspect
    import _close_selftest
    base.mkdir()
    (base / "blob").write_bytes(b"blob")
    (base / "adir").mkdir()
    ns = globals()
    sent = _close_selftest._StSentinel("in flight at working_blob")

    def blob(raise_sent):
        def call(fault):
            real_fdopen, real_require = os.fdopen, ns["require"]
            start = _close_selftest._st_fd_table()

            def fdopen_spy(fd, *args, **kwargs):
                # working_blob holds exactly two new descriptors here: the file and its directory
                directory = set(_close_selftest._st_fd_table()) - set(start) - {fd}
                require(len(directory) == 1, "close vector: cannot identify working_blob's directory")
                fault.arm(directory.pop())
                return real_fdopen(fd, *args, **kwargs)

            def require_spy(condition, message):
                if raise_sent and message.endswith("working input is not a regular file"):
                    raise sent
                real_require(condition, message)
            os.fdopen, ns["require"] = fdopen_spy, require_spy
            try:
                working_blob(str(base), "blob")
            finally:
                os.fdopen, ns["require"] = real_fdopen, real_require
        return call

    def final_blob(name, wrap, site=None):
        def call(fault):
            real_fdopen = os.fdopen

            def fdopen_spy(fd, *args, **kwargs):
                fault.arm(fd)                     # the final component's descriptor
                handle = real_fdopen(fd, *args, **kwargs)
                if wrap:
                    handle.close()                # as io.open closes the raw file of a wrapper that failed
                    raise sent
                return handle
            os.fdopen = fdopen_spy
            try:
                (site or working_blob)(str(base), name)
            finally:
                os.fdopen = real_fdopen
        return call

    vectors = (("check_release_cut site working_blob: finally while an exception unwinds", True, "AR",
                blob(True), lambda e: e is sent),
               ("check_release_cut site working_blob: normal path", False, "BR", blob(False), None),
               ("check_release_cut site working_blob: finally when fdopen refuses a directory",
                True, "AR", final_blob("adir", False), lambda e: type(e) is IsADirectoryError),
               ("check_release_cut site working_blob: finally when os.fdopen fails after its raw file exists",
                True, "AR", final_blob("blob", True), lambda e: e is sent),
               ("check_release_cut site working_blob: the final component's close on the normal path",
                False, "BR", final_blob("blob", False), None)
               ) + _close_selftest._st_helper_vectors(ns)
    failures, runs = _close_selftest._st_close_check(ns, vectors)
    source = inspect.getsource(working_blob)
    new = ('        try:\n            with os.fdopen(fd, "rb", closefd=False) as stream:\n'
           "                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),\n"
           '                        path + ": working input is not a regular file")\n'
           "                return stream.read()\n        finally:\n            _close_fd_yielding(fd)\n")
    old = ('        try:\n            stream = os.fdopen(fd, "rb")\n        except BaseException:\n'
           "            _close_fd_yielding(fd)\n            raise\n        with stream:\n"
           "            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode),\n"
           '                    path + ": working input is not a regular file")\n            return stream.read()\n')
    if source.count(new) != 1:
        return failures + ["check_release_cut working_blob revert: target found {} times".format(
            source.count(new))], runs
    reverted = dict(ns)
    exec(compile(source.replace(new, old), __file__, "exec"), reverted)
    red = _close_selftest._st_close_run(final_blob("blob", True, reverted["working_blob"]), True,
                                        lambda e: e is sent, False)
    runs += 1
    if [problem.split(":")[0] for problem in red] != ["NOFIRE"]:
        failures.append("check_release_cut site working_blob under the pre-fix fdopen ownership: expected red by "
                        "NOFIRE alone, got {}".format(red or "green"))
    return failures, runs


# The exact bytes of tools/_close_selftest.py ahead of its harness: the docstring and the two imports.
_CLOSE_HARNESS_PREAMBLE = '''"""Self-test harness for the #378 close vectors in the tools that carry a local copy of
_close_fd_propagating and _close_fd_yielding (check_footer, check_gensrc_failclose, check_overclaim,
check_release_cut, gen_crosswalk) or of _close_fd_propagating alone (import_cwe). It is a copy of the harness at the end of opf/tools/_journal.py, kept
here so those tools' --self-test runs without opf/tools present; keep the two in step. Each tool passes its
own module namespace, so the vectors and flips exercise that tool's helper copy and its own site.
check_release_cut compares this whole file: the harness byte for byte against _journal's, and what comes
ahead of it (this docstring and the two imports) against the exact bytes it records.

Imported only by a self-test; nothing here runs on a production path."""

import os
import sys

'''


def _close_harness_in_step(journal, copy):
    """#378: tools/_close_selftest.py is a copy of the close-vector harness at the end of
    opf/tools/_journal.py; from class _StSentinel through _st_watch_drop_check (where the copy ends and
    _journal goes on to its own site vectors) the two must stay byte-identical. The whole copy is compared:
    what precedes the harness must be _CLOSE_HARNESS_PREAMBLE exactly, so no statement can be inserted
    ahead of it (a rebound os.close there would disarm every vector)."""
    start, stop = "\nclass _StSentinel(", "\n\n\ndef _st_site_vectors("
    require(journal.count(start) == journal.count(stop) == copy.count(start) == 1,
            "close harness: _journal.py or _close_selftest.py lost its harness anchors")
    return copy == _CLOSE_HARNESS_PREAMBLE + journal[journal.index(start):journal.index(stop)] + "\n"


# #378 P1, the alias-aware close sweep: a best-effort static tripwire, not a proof. The double-close guarantee
# rests on the P1 helpers (one os.close per number, never probed or closed again) and on the release-then-raise
# vectors that drive each site; the sweep only flags the four shapes below, through the alias grammar below.
# Re-run it from the repository root with:
#   cd tools && python3 -c 'import check_release_cut as c; print(*c._close_sweep(".."), sep="\n")'
# Its rows are (path, line, function, shape, closed, other). The shapes:
#   TRY     a close in a try body that the same try's handlers, else block or finally closes again, or a close
#           in a handler or the else block that the finally closes again, directly or through an alias (a
#           try-star statement, try: ... except* ..., is a try here and in AFTER);
#   AFTER   a close in a try body under a handler that does not re-raise, closed again by a statement that can
#           follow the try;
#   REBIND  a close made by a call statement or by a with statement's exit, followed by a re-binding of the
#           closed name or attribute in a statement that can follow it (close-then-rebind, in a loop or not),
#           reported whether or not a cleanup reaches it;
#   ATTR    a close of self.<attr> by a call statement, followed the same way by its re-binding while another
#           method of the class closes the same attribute.
# The statements that can follow a statement are the rest of its own block, then the rest of every block
# enclosing it, out to the function body (a close in an if, with, loop, match case or try block pairs with a
# re-binding or a close after that block); for a statement in a try body, the try's else block comes first,
# and for a statement in a for or while body (async for too), the loop's else block comes first.
# Blocks are found from each statement's own fields (every list of statements, a handler's or a match case's
# body included), so every compound statement kind is walked, and a nested function, lambda or class is never
# entered (each function and method is swept on its own). A close is a call whose callee name contains "close"
# (os.close, every _close_* helper) of its first argument, an argument-less x.close() of x, and the exit of a
# with statement over os.fdopen(k), open(k) or socket.fromfd(k) when k (a name, an attribute chain or an element
# key) is an alias of a key a close call in the function passes as its first argument, unless the call passes
# closefd=False (that object never closes the descriptor). A re-binding is an =, augmented or annotated
# assignment to the key, alone or as an element of a target tuple.
# The alias grammar, in full. Keys are a name, an attribute chain (self.fd) and a container's element key ([]c);
# two keys are aliases only when these rules join them, transitively and flow-insensitively over the function,
# except that a moved source is a new key after its move, within the move's region (rule 2):
#   1. a plain re-binding, a = b or a = self.b (an augmented c += d too), which also joins the two keys' element
#      keys, so a container bound to a second name (saved = fds) shares its elements;
#   2. a tuple or list assignment whose right side is a display of the same length binds each target to its
#      element, each source taking its value from before the statement and each target its key after it. A
#      target the right side reads, anywhere in it, that is not re-bound to itself is MOVED (fd, a = a, None; a
#      swap a, b = b, a; fds, a = [a], None): from the end of that statement to the end of its REGION, every
#      use of it, and of an attribute chain under it, is a new key that only its new value and later rules
#      join. The region is the innermost block enclosing the move that runs on only some of the paths through
#      its statement (an if or elif body or else, a for or while body or else, a match case, a try's handler
#      or else block); a try body, a finally and a with body are not such blocks, and a move with no such block
#      around it holds to the end of the function. After its region the source is the key it was before the
#      move again, so a move in one branch of an if does not clear the source after the if (a later close of
#      it pairs with a close of the value it had before the move: the conservative join). When moves cleared
#      more than one prefix of a key, the latest of them before the use wins (old, a.fd = a.fd, None; then
#      saved, a = a, None: a later a.fd is under a's new key, not a.fd's). An element that is
#      a moved name is handed on: the targets it fills join each other (b, c, a = a, a, None), and join the
#      name as it was before the move only when a rule-1 re-binding outside a tuple on an EARLIER line made it
#      an alias (d = a, then fd, a = a, None joins fd and d); otherwise the move is an ownership transfer, not
#      an alias. Every other element binds its target as a plain assignment of it would under rules 1, 3, 6
#      and 7 (fds, other = [a], None joins []fds and a, and in fds, a = [a], None []fds joins a as it was before
#      the move). So a cleared source never aliases the value it handed on: after a = self.fd; fd, a = a, None,
#      a later `if a is not None: os.close(a)` is a new key (the ownership-first idiom), while a source
#      re-bound to itself (b, a = a, a) keeps its value;
#   3. a container's element key joins each element of a tuple, list, set or dict display bound to it, or
#      appended, inserted or added into it, or a value stored into it by subscript; and the element keys a
#      container expression (rule 4) supplies when that expression is bound to it, or extended or updated
#      into it;
#   4. a container expression has its wrappers removed first (c.values(), c.items(), c.keys(), c.copy(),
#      reversed(c), list(c), tuple(c), sorted(c), set(c), nested), and what is left is classified: a + b
#      concatenation and an or / and expression (each operand classified the same way), a display, or a
#      named container;
#   5. a loop variable joins the elements of the container expression (rule 4) it iterates;
#   6. a file object os.fdopen(k), open(k) or socket.fromfd(k) wraps k in (k a name, an attribute chain or an
#      element key), bound to a name (alone or as a tuple element) or as a with target, and the target of
#      `with k as f`, join k when k is an alias of a key a close call in the function passes as its first
#      argument, unless the call passes closefd=False; so the object's close, and the with statement's exit,
#      count against every alias of k;
#   7. a keyword capture: ns = SimpleNamespace(pidfd=fd) makes ns.pidfd an alias of fd.
# Residual, plainly: any alias form outside that grammar is not followed, and a double close through it passes
# the sweep unseen. The known gaps it leaves: a moved name's earlier alias through anything but a rule-1
# re-binding outside a tuple on an earlier line (a container, a loop variable, a keyword capture, a tuple
# element), because keys other than a moved source are not renewed per binding, and joining those merges
# ownership transfers (tried at this tree, it reports 2 false TRY hits in check_opf_import's _spelled_route,
# where `prev, cur = cur, nfd` runs in a loop with nfd re-bound each pass); an element target, which is never
# moved (a swap through c[i]); the order of statements beyond the following rule and the move rule (a
# two-statement transfer `fd = x; x = None` is still reported, as only a tuple move clears its source; within
# its region a move clears its source for every later position in the function's text whether or not the path
# there runs it: a move in a try body holds in that try's handlers and finally, though an exception raised in
# the body before the move reaches them with the source not yet moved, so a close of an alias of the source
# above the move (b = a; os.close(b); fd, a = a, None) is not paired with the source's close in that finally
# (a close of the source itself there is still reported, as REBIND, since the move re-binds it); a use above a
# move in a loop body is read as the value before the move even on the next iteration; a statement after a
# return, raise or os._exit still counts as following, a loop's else follows its body even past a break, a
# loop's next iteration does not follow, and a try's finally is not a following statement of its body); a
# close inside a lambda, which is never swept; a repeated
# close with no try between (os.close(a); os.close(a)), which no shape pairs; a close made through a callee
# whose name lacks "close" or through a function passed as a value; a second close in another function (a
# caller's cleanup, or another method outside the ATTR shape); a with statement's exit in the ATTR shape, and a
# re-binding by a for or with target, a match capture, an assignment expression (:=), del or import, which no
# shape counts; an element closed by index (fds[i] is one key per container, and a nested container shares the
# outer element key only by subscript); a container appended or inserted into another as one element; a tuple
# unpacked from anything but a display (a, b = pair), or a nested target tuple; a starred element or target
# ([*c]); a conditional expression (c if x else d); an alias made by an assignment expression, a match capture
# or a with target over anything but a key or a rule-6 wrapper; a comprehension or a container returned by any
# call other than the rule-4 wrappers; and descriptors held by objects the sweep does not know wrap one
# (sockets, subprocess pipes, selectors).
_CLOSE_SWEEP_DIRS = ("tools", "opf/tools")


def _cs_key(node):
    """'x' for a name, 'self.x' for an attribute chain of names, '[]d' for an element of the container d."""
    import ast
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _cs_key(node.value)
        return base and base + "." + node.attr
    if isinstance(node, ast.Subscript):
        base = _cs_key(node.value)
        return base and "[]" + base.lstrip("[]")
    return None


def _cs_close_arg(node):
    """The key a close call closes, or None."""
    import ast
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    if "close" not in name.lower() or name in ("closerange", "closed"):
        return None
    if node.args:
        return _cs_key(node.args[0])
    if isinstance(func, ast.Attribute) and not node.keywords:
        return _cs_key(func.value)
    return None


def _cs_wrapped(node):
    """The keyed expression (a name, attribute chain or element) os.fdopen(k) / open(k) / socket.fromfd(k)
    wraps, or None."""
    import ast
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    if name not in ("fdopen", "open", "fromfd") or not node.args or not _cs_key(node.args[0]):
        return None
    if any(kw.arg == "closefd" and isinstance(kw.value, ast.Constant) and kw.value.value is False
           for kw in node.keywords):
        return None                               # closefd=False: the object never closes the descriptor
    return node.args[0]


def _cs_walk(nodes):
    """Every node under `nodes`, never entering a nested function, lambda or class."""
    import ast
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _cs_is_try(node):
    """A try statement, or a try-star (except*) one, which pairs and follows exactly as a try does."""
    import ast
    return isinstance(node, ast.Try) or type(node).__name__ == "TryStar"


def _cs_blocks(node):
    """Each statement block directly under `node` with the field holding it: every field that is a list of
    statements, and every such list inside a non-statement child (a handler's body, a match case's body), so
    every compound statement kind is walked without naming its attributes."""
    import ast
    for field, value in ast.iter_fields(node):
        if not isinstance(value, list) or not value:
            continue
        if all(isinstance(item, ast.stmt) for item in value):
            yield field, value
            continue
        for item in value:
            if isinstance(item, ast.AST) and not isinstance(item, (ast.stmt, ast.expr)):
                for _inner, block in _cs_blocks(item):
                    yield field, block


def _cs_regions(nodes, limit=None):
    """Each statement under `nodes` (never inside a nested function or class) with the end of the innermost
    block enclosing it that runs on only some of the paths through its statement: an if's body or else, a
    loop's body or else, a match case, and a try's handler or else block. None when no such block encloses
    it: a try body, a finally and a with body run on every path that reaches their statement, so they are
    not one."""
    import ast
    for stmt in nodes:
        yield stmt, limit
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field, block in _cs_blocks(stmt):
            every = field == "finalbody" or field == "body" and (_cs_is_try(stmt)
                                                                 or isinstance(stmt, (ast.With, ast.AsyncWith)))
            yield from _cs_regions(block, limit if every else (block[-1].end_lineno, block[-1].end_col_offset))


def _cs_container(node):
    """`node` with every container wrapper removed: c.values(), c.items(), c.keys(), c.copy(), reversed(c),
    list(c), tuple(c), sorted(c) and set(c), however nested."""
    import ast
    while True:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("values", "items", "keys", "copy"):
            node = node.func.value
        elif isinstance(node, ast.Call) and _cs_key(node.func) in ("reversed", "list", "tuple", "sorted", "set") \
                and node.args:
            node = node.args[0]
        else:
            return node


def _cs_at(node):
    return node.lineno, node.col_offset


def _cs_aliases(fn):
    """The function's alias classes, by union-find over every alias the sweep follows, as (same, held):
    same(node, key, other_node, other_key) tests two keys used at those nodes, and held(node, key) whether a
    key wrapped at `node` is an alias of a key some close call closes (rule 6)."""
    import ast
    parent = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        if a and b:
            parent[find(a)] = find(b)
            if not a.startswith("[]") and not b.startswith("[]"):
                union("[]" + a, "[]" + b)         # two names for one container share its elements

    def displays(node):
        """Each (targets, values) pair of a tuple or list assignment from a display of the same length."""
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            for target in node.targets:
                if isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == len(node.value.elts):
                    yield target, node.value

    def cleared(target, value):
        """The sources a display assignment moves: each target its right side reads, unless it is re-bound to
        itself (b, a = a, a keeps a)."""
        read = {_cs_key(n) for n in ast.walk(value)} - {None}
        return {_cs_key(t) for t, v in zip(target.elts, value.elts)
                if _cs_key(t) in read and _cs_key(t) != _cs_key(v) and not _cs_key(t).startswith("[]")}

    # Rule 2: each moved source is a new key from its move's end to the end of its region (_cs_regions), the
    # innermost block enclosing the move that runs on only some paths; after that block's join it is the
    # key it was before the move again.
    limits = {id(stmt): limit for stmt, limit in _cs_regions(fn.body)}
    moved = {}
    for node in _cs_walk(fn.body):
        for target, value in displays(node):
            for source in cleared(target, value):
                moved.setdefault(source, []).append(((node.end_lineno, node.end_col_offset), limits.get(id(node))))

    def version(k, at):
        """`k` as used at position `at`: a key, or an attribute chain under one, that a move cleared on an
        earlier position within the move's region is that move's new key; when moves cleared more than one
        prefix of `k` (a.fd, then a), the latest of them wins, so a name moved after its attribute renews
        the whole chain."""
        if not k or k.startswith("[]"):
            return k and "[]" + version(k[2:], at)
        parts = k.split(".")
        latest = None
        for n in range(1, len(parts) + 1):
            prefix = ".".join(parts[:n])
            ends = [end for end, limit in moved.get(prefix, ()) if end <= at and (limit is None or at <= limit)]
            if ends and (latest is None or max(ends) >= latest[0]):
                latest = max(ends), prefix
        if latest is None:
            return k
        return "{}@{}:{}".format(latest[1], *latest[0]) + k[len(latest[1]):]

    def key(node, at=None):
        return version(_cs_key(node), at or _cs_at(node))

    def elements(node):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [key(e) for e in node.elts if key(e)]
        if isinstance(node, ast.Dict):
            return [key(e) for e in node.values if key(e)]
        return []

    def contents(node):
        """The element keys a container expression supplies. Its wrappers come off first (_cs_container), and
        what is left is classified: both sides of a + concatenation and every operand of an or / and (each
        examined the same way, so list(a + b) and sorted(a or b) join too), a display's elements, or the
        element key of the container it names."""
        bare = _cs_container(node)
        if isinstance(bare, ast.BinOp) and isinstance(bare.op, ast.Add):
            return contents(bare.left) + contents(bare.right)
        if isinstance(bare, ast.BoolOp):
            return [k for operand in bare.values for k in contents(operand)]
        named = key(bare)
        return elements(bare) + (["[]" + named.lstrip("[]")] if named else [])

    wraps = []                                    # rule 6's candidates, joined once their key is a closed one

    def bind(target, value):
        """Join the key `target` to a value that is not itself a key (rules 3, 6 and 7)."""
        for element in contents(value):           # a display, list(c), c.copy(), c or {}, a + b
            union("[]" + target.lstrip("[]"), element)
        if _cs_wrapped(value) is not None:
            wraps.append((target, key(_cs_wrapped(value))))
        if isinstance(value, ast.Call):
            for kw in value.keywords:
                if kw.arg and key(kw.value):
                    union(target + "." + kw.arg, key(kw.value))

    moves, plain = [], []

    for node in _cs_walk(fn.body):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            value, bound = node.value, (node.end_lineno, node.end_col_offset)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
                        and len(target.elts) == len(value.elts):
                    # The right side is evaluated before any binding, so each source is the key it was
                    # until this statement and each target the key it is after it (a moved source's new
                    # key); a moved value is handed on (fd, x = x, None), not shared with its old name.
                    gone, fans = cleared(target, value), {}
                    for t, v in zip(target.elts, value.elts):
                        if not key(t, bound):
                            continue
                        if _cs_key(v) in gone:
                            if _cs_key(t) != _cs_key(v):
                                fans.setdefault(key(v), []).append(key(t, bound))
                        elif key(v):
                            union(key(t, bound), key(v))
                        else:
                            bind(key(t, bound), v)    # each other element binds as a plain assignment does
                    moves += [(source, targets, node.lineno) for source, targets in fans.items()]
                    continue
                if not key(target, bound):
                    continue
                if key(value):
                    union(key(target, bound), key(value))
                    plain.append((key(target, bound), key(value), node.lineno))
                else:
                    bind(key(target, bound), value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("append", "extend", "insert", "add", "update") and key(node.func.value):
            box = "[]" + key(node.func.value).lstrip("[]")
            for arg in node.args[1:] if node.func.attr == "insert" else node.args:
                if node.func.attr in ("extend", "update"):
                    items = contents(arg)             # the elements of another container, not its name
                else:
                    items = [key(arg)] if key(arg) else elements(arg)
                for element in items:
                    union(box, element)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                inner = _cs_wrapped(item.context_expr)
                source = key(item.context_expr) or (inner is not None and key(inner))
                if item.optional_vars is not None and key(item.optional_vars) and source:
                    wraps.append((key(item.optional_vars), source))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and key(node.target):
            for element in contents(node.iter):
                union(key(node.target), element)
    # A moved value goes to every target it fills (b, c, a = a, a, None): those targets are aliases of each
    # other, and of a name a plain re-binding on an EARLIER line made an alias of the source (d = a above the
    # move), so they then join the source's class as it was before the move; the cleared source itself is a
    # new key from the move on, so a later cleanup of it (if a is not None: os.close(a)) joins none of them.
    for source, targets, line in moves:
        for target in targets[1:]:
            union(targets[0], target)               # destinations that receive one value share it
        if any(source in (k, v) and k != v and at < line for k, v, at in plain):
            union(targets[0], source)               # the source's earlier plain alias holds the value too
    closed = [version(_cs_close_arg(n), _cs_at(n)) for n in _cs_walk(fn.body) if _cs_close_arg(n) and n.args]

    def closing(k):
        """Whether `k` is an alias of a key some close call in the function closes."""
        return find(k) in {find(c) for c in closed}

    joined = True
    while joined:                                 # a wrapper joins once its key is an alias of a closed one
        joined = [(a, b) for a, b in wraps if find(a) != find(b) and closing(b)]
        for a, b in joined:
            union(a, b)
    return (lambda n, a, m, b: find(version(a, _cs_at(n))) == find(version(b, _cs_at(m))),
            lambda node, k: closing(version(k, _cs_at(node))))


def _cs_closes(nodes, held=None):
    """Each close under `nodes` with the key it closes; a with statement's exit counts when `held` says the key
    its wrapper wraps is a closed descriptor."""
    import ast
    found = []
    for node in _cs_walk(nodes):
        if _cs_close_arg(node):
            found.append((node, _cs_close_arg(node)))
        elif held and isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                inner = _cs_wrapped(item.context_expr)
                if inner is not None and held(node, _cs_key(inner)):
                    found.append((node, _cs_key(inner)))
    return found


def _cs_rebinds(stmt, key):
    import ast
    for node in _cs_walk([stmt]):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if any(_cs_key(t) == key for t in [target] + list(getattr(target, "elts", []))):
                    return True
    return False


def _cs_sequence(nodes, after=()):
    """Each statement under `nodes` (never inside a nested function or class) with the statements that can run
    after it: the rest of its own block, then the rest of every block that encloses it, out to `nodes`; for a
    statement in a try body, the try's else block comes first (it runs when the body completes), and for a
    statement in a for or while body (async for too), the loop's else block (it runs when the loop ends)."""
    import ast
    for i, stmt in enumerate(nodes):
        following = list(nodes[i + 1:]) + list(after)
        yield stmt, following
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field, block in _cs_blocks(stmt):
            tried = field == "body" and (_cs_is_try(stmt) or isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)))
            yield from _cs_sequence(block, list(stmt.orelse) + following if tried else following)


def _cs_function(rel, fn):
    import ast
    same, held = _cs_aliases(fn)
    rows = []
    for node in _cs_walk(fn.body):
        if _cs_is_try(node):
            handled = [s for h in node.handlers for s in h.body]
            cleanup = _cs_closes(handled + list(node.orelse) + list(node.finalbody), held)
            pairs = [(c, k, cleanup) for c, k in _cs_closes(node.body, held)]
            pairs += [(c, k, _cs_closes(node.finalbody, held))
                      for c, k in _cs_closes(handled + list(node.orelse), held)]
            rows += [(rel, c.lineno, fn.name, "TRY", k, o) for c, k, later in pairs for a, o in later
                     if same(c, k, a, o)]
    for stmt, following in _cs_sequence(fn.body):
        if isinstance(stmt, ast.Expr) and _cs_close_arg(stmt.value) \
                and any(_cs_rebinds(after, _cs_close_arg(stmt.value)) for after in following):
            rows.append((rel, stmt.lineno, fn.name, "REBIND", _cs_close_arg(stmt.value), ""))
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            rows += [(rel, stmt.lineno, fn.name, "REBIND", k, "") for c, k in _cs_closes([stmt], held)
                     if c is stmt and any(_cs_rebinds(after, k) for after in following)]
        if _cs_is_try(stmt) and any(not (h.body and isinstance(h.body[-1], ast.Raise)) for h in stmt.handlers):
            rows += [(rel, c.lineno, fn.name, "AFTER", k, o) for c, k in _cs_closes(stmt.body, held)
                     for a, o in _cs_closes(following, held) if same(c, k, a, o)]
    return rows


def _cs_class(rel, cls):
    import ast
    methods = [m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    closes = [(call, key) for m in methods for call, key in _cs_closes(m.body) if key.startswith("self.")]
    rows = []
    for m in methods:
        for stmt, following in _cs_sequence(m.body):
            key = isinstance(stmt, ast.Expr) and _cs_close_arg(stmt.value)
            if key and key.startswith("self.") and any(_cs_rebinds(a, key) for a in following) \
                    and any(k == key and c is not stmt.value for c, k in closes):
                rows.append((rel, stmt.lineno, "{}.{}".format(cls.name, m.name), "ATTR", key, ""))
    return rows


def _close_sweep_source(rel, text):
    import ast
    tree = ast.parse(text, rel)
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            rows += _cs_class(rel, node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows += _cs_function(rel, node)
    return rows


def _close_sweep(root):
    """The sweep over tools/*.py and opf/tools/*.py (not _vendor) beneath `root`: sorted rows."""
    rows = set()
    for directory in _CLOSE_SWEEP_DIRS:
        for path in sorted((Path(root) / directory).glob("*.py")):
            rows.update(_close_sweep_source(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")))
    return sorted(rows)


# Every hit of the sweep at this commit, keyed (path, function, shape, closed, other) with its count, and its
# disposition. A hit the table does not hold, or a held hit no longer found, fails the self-test, so a new
# double-close shape needs a disposition here before it can land. A "part C" row may be absent: it is a site
# that only off-path part C, merging after #378, carries.
_CS_LEGS = ("false positive: sequential self-test legs; each later binding is a fresh {}, and no try closes "
            "the old one again")
_CLOSE_SWEEP_DISPOSITIONS = (
    ("opf/tools/_opf_oplock.py", "propagating", "AFTER", "fd", "fd", 1,
     "part C: false positive, part C's _st_reclose_yielding, the deliberate RECLOSE body its T-p2 flip runs; the "
     "site exists only in part C (found by this sweep at 15f2f498, absent here)"),
    ("opf/tools/_journal.py", "body", "AFTER", "fd", "fd", 1,
     "false positive: the harness's R flip (reclose), the deliberate pre-P1 body each R leg must turn red"),
    ("tools/_close_selftest.py", "body", "AFTER", "fd", "fd", 1,
     "false positive: the harness copy's R flip (reclose), byte-identical to _journal's"),
    ("opf/tools/_opf_adopt_observe.py", "guarded", "TRY", "stack", "stack", 1,
     "false positive: contextlib.ExitStack.close pops each callback before it runs it, so a repeat runs none twice"),
    ("opf/tools/_opf_adopt_observe.py", "run_case", "TRY", "client", "client", 1,
     "false positive: the client closed here is never appended to backlog_clients, which the cleanup loop closes"),
    ("opf/tools/_opf_oplock.py", "_bind_repository_view", "REBIND", "fd", "", 1,
     "false positive: _FdOwner.close removes the number from the owner before os.close, so its exit never repeats it"),
    ("opf/tools/_opf_oplock.py", "_open_machine_dir", "REBIND", "fd", "", 1,
     "false positive: an _FdOwner.close (the number leaves the owner before os.close), then fd = nfd, the child "
     "the owner adopted first"),
    ("opf/tools/_opf_oplock.py", "_open_store_home", "REBIND", "parent", "", 1,
     "false positive: an _FdOwner.close (the number leaves the owner before os.close), then parent = fd, the child "
     "the owner adopted first"),
    ("opf/tools/_opf_oplock.py", "_probe_init_machine_dir", "REBIND", "fd", "", 1,
     "false positive: an _FdOwner.close (the number leaves the owner before os.close), then fd = nfd, the child "
     "the owner adopted first"),
    ("opf/tools/_opf_oplock.py", "_st_f8_4_body", "TRY", "dir_fd", "dir_fd", 1,
     "false positive: _st_f8_4_interrupted_close(dir_fd) closes its own staging descriptor, never dir_fd"),
    ("opf/tools/_opf_oplock.py", "_st_in_child", "AFTER", "rfd", "rfd", 1,
     "false positive: a self-test fork helper; the swallowed close runs only in the child, which ends in os._exit "
     "before the parent's close of its own rfd"),
    ("opf/tools/check_opf_import.py", "_RunDir.close", "ATTR", "self.cwd_fd", "", 1,
     "false positive: every caller closes a _RunDir once, and __init__'s close raises before close() can run"),
    ("opf/tools/check_opf_import.py", "close", "REBIND", "self.cwd_fd", "", 1,
     "false positive: as the ATTR row, close() runs once per _RunDir"),
    ("opf/tools/check_opf_import.py", "_check_staged_run", "REBIND", "store_fd", "", 2,
     "false positive: a failed close in the handler propagates out of the function; nothing closes store_fd again"),
    ("opf/tools/check_opf_import.py", "_spelled_route", "REBIND", "prev", "", 1,
     "false positive: prev is closed only after `prev, cur = cur, nfd` handed cur on; its later re-binding is the "
     "next such move, and the finally closes only cur"),
    ("opf/tools/check_opf_import.py", "_self_test_isolated", "REBIND", "jfd", "", 1,
     _CS_LEGS.format("journal-directory open")),
    ("opf/tools/_opf_import.py", "_write_ingest_review_bundle", "REBIND", "store_root_fd", "", 1,
     "false positive: closed in the finally of the staged-read step; the later binding is a fresh store-root open "
     "with its own cleanup, and no try closes the old number again"),
    ("opf/tools/_opf_import.py", "_capture_ingest_review", "REBIND", "rd", "", 1,
     "false positive: rd.close() in a finally; the later binding is a fresh gate._RunDir, and no try closes the "
     "old one again"),
    ("opf/tools/_opf_import.py", "_self_test_d5", "REBIND", "jfd", "", 1, _CS_LEGS.format("journal-root open")),
    ("opf/tools/_opf_import.py", "_self_test_d5", "REBIND", "rfd", "", 1, _CS_LEGS.format("store-root open")),
    ("opf/tools/_opf_ingest.py", "review_bundle", "REBIND", "fd", "", 3, _CS_LEGS.format("open_fd(root)")),
    ("opf/tools/_opf_init_operation.py", "_health_check", "REBIND", "pfd", "", 1,
     "false positive: closed in a finally; the later binding is a fresh _open_parent with its own finally, and no "
     "try closes the old number again"),
    ("opf/tools/_opf_init_operation.py", "_release_all", "REBIND", "run.root_fd", "", 1,
     "false positive: the close's OSError is caught and run.root_fd cleared right after; _release_all runs once, "
     "from run_init_operation's finally, and nothing else closes run.root_fd"),
    ("opf/tools/_opf_init_operation.py", "_release_all", "REBIND", "run.sub", "", 1,
     "false positive: close_operation's InitSubstrateError is caught and run.sub cleared right after, and "
     "close_operation refuses a handle already closed, so its descriptors are never closed twice"),
    ("opf/tools/_opf_init_operation.py", "_physical_tests", "REBIND", "rfd", "", 1, _CS_LEGS.format("os.open")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "REBIND", "jr_fd", "", 3,
     _CS_LEGS.format("journal-root open (or None until one)")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "REBIND", "root_fd", "", 7,
     _CS_LEGS.format("_open_dir_nofollow")),
    ("opf/tools/_opf_emit.py", "_boom_close7", "AFTER", "fd", "fd", 1,
     "false positive: a self-test close stub; its swallowed close is followed by a raise in the same block, so the "
     "later close never runs after it"),
    ("opf/tools/check_opf_homes.py", "_staged_root_self_test", "REBIND", "jfd", "", 1,
     _CS_LEGS.format("journal-root open")),
    ("tools/gen_crosswalk.py", "_walk_components", "TRY", "prev", "fd", 1,
     "false positive: fd is re-bound to nxt before prev is closed, so the handler closes nxt, never prev"),
    ("tools/pin.py", "do_recover", "REBIND", "fd", "", 3,
     "false positive: each close follows an ownership move (fd, root_fd = root_fd, None) and a return ends its "
     "block; each later binding of fd is another early return's own move"),
    ("opf/tools/opf.py", "_watchdog_completion_case", "AFTER", "child", "child", 4,
     _CS_LEGS.format("_FixtureProcess")),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "child", "", 5, _CS_LEGS.format("_FixtureProcess")),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "child.pidfd", "", 1,
     "false positive: a self-test leg's hygiene close; no try in the function closes it again"),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "fake", "", 1,
     _CS_LEGS.format("types.SimpleNamespace stand-in")),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "fd", "", 9, _CS_LEGS.format("pidfd")),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "guardian_fd", "", 6, _CS_LEGS.format("pidfd")),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "leader_fd", "", 1, _CS_LEGS.format("pidfd")),
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "subject_fd", "", 1, _CS_LEGS.format("pidfd")),
) + tuple(("opf/tools/_opf_init_substrate.py", name, "REBIND", "sub", "", 1,
           "false positive: a self-test step; sub is re-bound to a fresh substrate, the old one never closed again")
          for name in ("_t_s1_sibling_home", "_t_s2_capability_gate", "_t_s13_midread_containment",
                       "_t_s16_staging_sweep", "_t_s20_distinct_nested_homes"))

# Synthetic shapes the sweep must find (or, for the ownership transfers and closefd=False, must not): each is
# (label, source, expected shapes).
_CLOSE_SWEEP_SHAPES = (
    ("tuple loop alias (begin_operation)", "def f(a, b):\n    try:\n        os.close(a)\n        a = None\n"
     "    except BaseException:\n        for x in (a, b):\n            if x is not None:\n                os.close(x)\n"
     "        raise\n", ["REBIND", "TRY"]),
    ("ownership transfer", "def f(a):\n    try:\n        fd, a = a, None\n        os.close(fd)\n    finally:\n"
     "        if a is not None:\n            os.close(a)\n", []),
    ("plain re-binding alias", "def f(a):\n    b = a\n    try:\n        os.close(b)\n    finally:\n"
     "        os.close(a)\n", ["TRY"]),
    ("list captured by append", "def f(a):\n    fds = []\n    fds.append(a)\n    try:\n        os.close(a)\n"
     "    finally:\n        for fd in reversed(fds):\n            os.close(fd)\n", ["TRY"]),
    ("close-then-rebind loop", "def f(cur, names):\n    try:\n        for n in names:\n"
     "            nxt = os.open(n, 0, dir_fd=cur)\n            os.close(cur)\n            cur = nxt\n    finally:\n"
     "        os.close(cur)\n", ["REBIND", "TRY"]),
    ("swallowed close, closed later", "def f(a):\n    try:\n        _close_fd_propagating(a)\n    except OSError:\n"
     "        pass\n    _close_fd_quietly(a)\n", ["AFTER"]),
    ("fdopen wraps the descriptor", "def f(fd):\n    try:\n        with os.fdopen(fd) as fh:\n"
     "            fh.read()\n    finally:\n        os.close(fd)\n", ["TRY"]),
    ("fdopen with closefd=False never closes", "def f(fd):\n    try:\n"
     "        with os.fdopen(fd, 'rb', closefd=False) as fh:\n            fh.read()\n    finally:\n"
     "        os.close(fd)\n", []),
    ("keyword capture", "def f(fd):\n    ns = SimpleNamespace(pidfd=fd)\n    try:\n        os.close(fd)\n"
     "    finally:\n        os.close(ns.pidfd)\n", ["TRY"]),
    ("attribute across methods", "class C:\n    def close(self):\n        os.close(self.fd)\n"
     "        self.fd = None\n\n    def __del__(self):\n        if self.fd is not None:\n"
     "            os.close(self.fd)\n", ["ATTR", "REBIND"]),
    ("tuple assignment keeping its source", "def f(a):\n    b, a = a, a\n    try:\n        os.close(b)\n"
     "    finally:\n        os.close(a)\n", ["TRY"]),
    ("container bound to a second name", "def f(a):\n    fds = [a]\n    saved = fds\n    try:\n"
     "        os.close(a)\n    finally:\n        for fd in saved:\n            os.close(fd)\n", ["TRY"]),
    ("extend from another container", "def f(a):\n    source = [a]\n    fds = []\n    fds.extend(source)\n"
     "    try:\n        os.close(a)\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("+= of another container", "def f(a):\n    source = [a]\n    fds = []\n    fds += source\n"
     "    try:\n        os.close(a)\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("list() copy of another container", "def f(a):\n    source = [a]\n    fds = list(source)\n"
     "    try:\n        os.close(a)\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("attribute closed in an if, re-bound after it", "class C:\n    def close(self):\n"
     "        if self.fd is not None:\n            os.close(self.fd)\n        self.fd = None\n\n"
     "    def __del__(self):\n        if self.fd is not None:\n            os.close(self.fd)\n", ["ATTR", "REBIND"]),
    ("else closed, finally closes again", "def f(fd):\n    try:\n        x = 1\n    except BaseException:\n"
     "        raise\n    else:\n        os.close(fd)\n    finally:\n        os.close(fd)\n", ["TRY"]),
    ("fan-out tuple from a cleared source", "def f(a):\n    b, c, a = a, a, None\n    try:\n        os.close(b)\n"
     "    finally:\n        os.close(c)\n", ["TRY"]),
    ("transfer from a source with an earlier alias", "def f(a):\n    d = a\n    fd, a = a, None\n    try:\n"
     "        os.close(fd)\n    finally:\n        os.close(d)\n", ["TRY"]),
    ("+ concatenation of another container", "def f(a):\n    source = [a]\n    fds = source + []\n    try:\n"
     "        os.close(a)\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("concatenation inside a list() wrapper", "def f(a):\n    source = [a]\n    fds = list(source + [])\n"
     "    try:\n        os.close(a)\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("update from another container", "def f(a):\n    source = {'k': a}\n    fds = {}\n    fds.update(source)\n"
     "    try:\n        os.close(a)\n    finally:\n        for fd in fds.values():\n            os.close(fd)\n",
     ["TRY"]),
    ("body closed, else closes again", "def f(fd):\n    try:\n        os.close(fd)\n    except OSError:\n"
     "        raise\n    else:\n        os.close(fd)\n", ["TRY"]),
    ("body closed, else re-binds", "def f(fd):\n    try:\n        os.close(fd)\n    except OSError:\n"
     "        raise\n    else:\n        fd = None\n", ["REBIND"]),
    ("ownership-first idiom from an earlier alias", "def f(self):\n    a = self.fd\n    try:\n"
     "        fd, a = a, None\n        os.close(fd)\n    finally:\n        if a is not None:\n"
     "            os.close(a)\n", []),
    ("move re-binds its source to another descriptor", "def f(a, b):\n    fd, a = a, b\n    try:\n"
     "        os.close(b)\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("container display in a tuple assignment", "def f(a):\n    fds, other = [a], None\n    try:\n"
     "        os.close(a)\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("fdopen of an attribute", "def f(self):\n    try:\n        with os.fdopen(self.fd) as fh:\n"
     "            fh.read()\n    finally:\n        os.close(self.fd)\n", ["TRY"]),
    ("fdopen of a container element", "def f(fds):\n    try:\n        with os.fdopen(fds[0]) as fh:\n"
     "            fh.read()\n    finally:\n        for fd in fds:\n            os.close(fd)\n", ["TRY"]),
    ("with exit over an alias of the closed descriptor", "def f(a):\n    b = a\n    try:\n"
     "        with os.fdopen(b) as fh:\n            fh.read()\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("fdopen object of an alias closed", "def f(a):\n    b = a\n    fh = os.fdopen(b)\n    try:\n"
     "        fh.close()\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("with exit, then re-bound", "def f(fd, other):\n    with os.fdopen(fd) as fh:\n        fh.read()\n"
     "    fd = other\n    os.close(fd)\n", ["REBIND"]),
    ("try-star swallowed close, closed later", "def f(a):\n    try:\n        _close_fd_propagating(a)\n"
     "    except* OSError:\n        pass\n    _close_fd_quietly(a)\n", ["AFTER"]),
    ("try-star body closed, handler closes again", "def f(a):\n    try:\n        os.close(a)\n"
     "    except* OSError:\n        os.close(a)\n        raise\n", ["TRY"]),
    ("close in a match case, re-bound after the match", "def f(x, fd):\n    match x:\n        case 1:\n"
     "            os.close(fd)\n        case _:\n            pass\n    fd = None\n", ["REBIND"]),
    ("enclosing name moved after its attribute", "def f(a, fd):\n    old, a.fd = a.fd, None\n"
     "    saved, a = a, None\n    a = SimpleNamespace(fd=fd)\n    try:\n        os.close(a.fd)\n    finally:\n"
     "        os.close(fd)\n", ["TRY"]),
    ("move in one branch of an if, closed after the join", "def f(a, c):\n    try:\n        if not c:\n"
     "            os.close(a)\n        else:\n            fd, a = a, None\n            os.close(fd)\n"
     "    finally:\n        if a is not None:\n            os.close(a)\n", ["TRY"]),
    ("for else after a swallowed close", "def f(fd, xs):\n    for x in xs:\n        try:\n"
     "            os.close(fd)\n        except OSError:\n            pass\n    else:\n        os.close(fd)\n",
     ["AFTER"]),
    ("while else after a swallowed close", "def f(fd, c):\n    while c:\n        try:\n"
     "            os.close(fd)\n        except OSError:\n            pass\n    else:\n        os.close(fd)\n",
     ["AFTER"]),
    ("for else re-binds", "def f(fd, xs):\n    for x in xs:\n        os.close(fd)\n    else:\n"
     "        fd = None\n", ["REBIND"]),
    ("while else re-binds", "def f(fd, c):\n    while c:\n        os.close(fd)\n    else:\n"
     "        fd = None\n", ["REBIND"]),
)
_CS_LOOP_ELSE = ("for else after a swallowed close", "while else after a swallowed close", "for else re-binds",
                 "while else re-binds")

# A synthetic corpus with one function per compound statement kind, closes and re-bindings in every block:
# the sweep must run over each, and over every tools/ and opf/tools/ source, without raising.
_CLOSE_SWEEP_CORPUS = (
    ("if", "def f(fd, c):\n    if c:\n        os.close(fd)\n    elif fd:\n        fd = None\n    else:\n"
     "        os.close(fd)\n    fd = 1\n"),
    ("for", "def f(fds):\n    for fd in fds:\n        os.close(fd)\n        fd = None\n    else:\n"
     "        os.close(fds[0])\n"),
    ("async for", "async def f(fds):\n    async for fd in fds:\n        os.close(fd)\n    else:\n"
     "        fd = None\n"),
    ("while", "def f(fd):\n    while fd:\n        os.close(fd)\n        fd = None\n    else:\n"
     "        os.close(fd)\n"),
    ("with", "def f(fd):\n    with os.fdopen(fd) as fh, open(fd) as gh:\n        fh.close()\n    fd = None\n"
     "    os.close(fd)\n"),
    ("async with", "async def f(fd):\n    async with os.fdopen(fd) as fh:\n        fh.close()\n"
     "    fd = None\n    os.close(fd)\n"),
    ("try", "def f(fd):\n    try:\n        os.close(fd)\n    except OSError:\n        os.close(fd)\n"
     "    except (ValueError, TypeError) as exc:\n        pass\n    else:\n        os.close(fd)\n"
     "    finally:\n        os.close(fd)\n"),
    ("try-star", "def f(fd):\n    try:\n        os.close(fd)\n    except* OSError:\n        os.close(fd)\n"
     "    else:\n        fd = None\n    finally:\n        os.close(fd)\n"),
    ("match", "def f(x, fd):\n    match x:\n        case 0 | 1:\n            os.close(fd)\n        case None:\n"
     "            fd = None\n        case [a, *rest]:\n            os.close(a)\n"
     "        case {'k': v, **kw}:\n            os.close(v)\n        case Point(x=px, y=0) if px:\n"
     "            os.close(px)\n        case str() as s:\n            s.close()\n        case _:\n"
     "            pass\n    os.close(fd)\n"),
    ("nested function", "def f(fd):\n    def inner():\n        os.close(fd)\n        fd2 = fd\n    try:\n"
     "        inner()\n    finally:\n        os.close(fd)\n"),
    ("class", "def f(fd):\n    class Holder:\n        def close(self):\n            os.close(self.fd)\n"
     "            self.fd = None\n    return Holder\n"),
    ("lambda", "def f(fd):\n    closer = lambda: os.close(fd)\n    try:\n        closer()\n    finally:\n"
     "        os.close(fd)\n"),
    ("comprehension", "def f(fds):\n    [os.close(fd) for fd in fds if fd]\n    {fd: os.close(fd) for fd in fds}\n"
     "    list(os.close(fd) for fd in fds)\n    fds = None\n"),
)

# Each fix to the sweep's alias and pairing rules, put back by the self-test under --red-on-revert: (name, the
# fixed text, its pre-fix text, the shapes that must then be the only ones red).
_CLOSE_SWEEP_REVERTS = (
    ("sweep-tuple-source",
     'if _cs_key(t) in read and _cs_key(t) != _cs_key(v) and not _cs_key(t).startswith("[]")}',
     'if _cs_key(t) in read and not _cs_key(t).startswith("[]")}', ("tuple assignment keeping its source",)),
    ("sweep-container-elements",
     'union("[]" + a, "[]" + b)         # two names for one container share its elements', "pass",
     ("container bound to a second name", "+= of another container")),
    ("sweep-extend-elements", 'if node.func.attr in ("extend", "update"):', 'if node.func.attr in ("update",):',
     ("extend from another container",)),
    ("sweep-update-elements", 'if node.func.attr in ("extend", "update"):', 'if node.func.attr in ("extend",):',
     ("update from another container",)),
    ("sweep-concatenation", "return contents(bare.left) + contents(bare.right)", "return []",
     ("+ concatenation of another container", "concatenation inside a list() wrapper")),
    ("sweep-unwrap-first", "if isinstance(bare, ast.BinOp) and isinstance(bare.op, ast.Add):",
     "if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):", ("concatenation inside a list() wrapper",)),
    ("sweep-fan-out", "union(targets[0], target)               # destinations that receive one value share it",
     "pass", ("fan-out tuple from a cleared source",)),
    ("sweep-fan-out-earlier-alias",
     "union(targets[0], source)               # the source's earlier plain alias holds the value too", "pass",
     ("transfer from a source with an earlier alias",)),
    ("sweep-copy-elements", "for element in contents(value):           # a display",
     "for element in elements(value):           #",
     ("list() copy of another container", "+ concatenation of another container",
      "concatenation inside a list() wrapper")),
    ("sweep-enclosing-blocks", "following = list(nodes[i + 1:]) + list(after)", "following = list(nodes[i + 1:])",
     ("attribute closed in an if, re-bound after it", "body closed, else re-binds",
      "close in a match case, re-bound after the match") + _CS_LOOP_ELSE),
    ("sweep-else-finally", "for c, k in _cs_closes(handled + list(node.orelse), held)]",
     "for c, k in _cs_closes(handled, held)]", ("else closed, finally closes again",)),
    ("sweep-body-else", "cleanup = _cs_closes(handled + list(node.orelse) + list(node.finalbody), held)",
     "cleanup = _cs_closes(handled + list(node.finalbody), held)", ("body closed, else closes again",)),
    ("sweep-else-follows-body", "_cs_sequence(block, list(stmt.orelse) + following if tried else following)",
     "_cs_sequence(block, following)", ("body closed, else re-binds",) + _CS_LOOP_ELSE),
    ("sweep-loop-else-follows-body",
     'tried = field == "body" and (_cs_is_try(stmt) or isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)))',
     'tried = field == "body" and _cs_is_try(stmt)', _CS_LOOP_ELSE),
    ("sweep-generic-blocks",
     "        for field, block in _cs_blocks(stmt):\n"
     "            tried = field == \"body\" and (_cs_is_try(stmt) or isinstance(stmt, (ast.For, ast.AsyncFor, "
     "ast.While)))\n",
     "        blocks = [getattr(stmt, field, None) for field in (\"body\", \"orelse\", \"finalbody\")]\n"
     "        blocks += [h.body for h in getattr(stmt, \"handlers\", ())]"
     " + [c.body for c in getattr(stmt, \"cases\", ())]\n"
     "        for block in [b for b in blocks if isinstance(b, list) and b and isinstance(b[0], ast.stmt)]:\n"
     "            tried = block is stmt.body and (bool(getattr(stmt, \"handlers\", ()))\n"
     "                                            or isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)))\n",
     ("close in a match case, re-bound after the match", "robustness corpus: match")),
    ("sweep-try-star-after", "if _cs_is_try(stmt) and any(", "if isinstance(stmt, ast.Try) and any(",
     ("try-star swallowed close, closed later",)),
    ("sweep-tuple-display", "bind(key(t, bound), v)    # each other element binds as a plain assignment does",
     "pass", ("container display in a tuple assignment",)),
    ("sweep-wrapped-key", "or not _cs_key(node.args[0]):", "or not isinstance(node.args[0], ast.Name):",
     ("fdopen of an attribute", "fdopen of a container element")),
    ("sweep-wrapper-aliases", "return find(k) in {find(c) for c in closed}", "return k in closed",
     ("fdopen of a container element", "with exit over an alias of the closed descriptor",
      "fdopen object of an alias closed")),
    ("sweep-with-rebind", "if isinstance(stmt, (ast.With, ast.AsyncWith)):", "if False:",
     ("with exit, then re-bound",)),
    ("sweep-move-clears-source",
     "ends = [end for end, limit in moved.get(prefix, ()) if end <= at and (limit is None or at <= limit)]",
     "ends = []", ("ownership-first idiom from an earlier alias",)),
    ("sweep-move-in-its-region", "if end <= at and (limit is None or at <= limit)]", "if end <= at]",
     ("move in one branch of an if, closed after the join",)),
    ("sweep-latest-prefix-move", "if ends and (latest is None or max(ends) >= latest[0]):", "if ends:",
     ("enclosing name moved after its attribute",)),
    ("sweep-target-after-move", "union(key(t, bound), key(v))", "union(key(t), key(v))",
     ("move re-binds its source to another descriptor",)),
)


def _cs_robustness_failures(sources):
    """Each (label, text) source the sweep raises on, named with the innermost statements that raise when each
    is swept on its own (as a function body, and as a class when it is one). Any exception is a failure."""
    import ast
    import types
    failures = []
    for label, text in sources:
        try:
            _close_sweep_source(label, text)
            continue
        except Exception as exc:                      # a crash is a defect, never a pass or a skip
            error = exc
        try:
            tree = ast.parse(text, label)
        except SyntaxError:
            tree = ast.Module(body=[], type_ignores=[])
        culprits = []
        for stmt in ast.walk(tree):
            if isinstance(stmt, ast.stmt):
                try:
                    _cs_function(label, types.SimpleNamespace(name="<probe>", body=[stmt]))
                    if isinstance(stmt, ast.ClassDef):
                        _cs_class(label, stmt)
                except Exception:
                    culprits.append(stmt)
        inner = [s for s in culprits if not any(o is not s and o in ast.walk(s) for o in culprits)]
        where = ", ".join("line {} (a {} statement)".format(s.lineno, type(s).__name__) for s in inner)
        failures.append("{} raised {!r} at {}".format(label, error, where or "no single statement"))
    return failures


def _cs_shapes_of(source):
    """The sorted shapes the sweep reports over `source`, or what it raised."""
    try:
        return sorted(row[3] for row in _close_sweep_source("<shape>", source))
    except Exception as exc:
        return "raised {!r}".format(exc)


def _close_sweep_shapes_red():
    """The labels of the synthetic shapes this sweep gets wrong, and of the corpus kinds it raises on."""
    return [label for label, source, shapes in _CLOSE_SWEEP_SHAPES if _cs_shapes_of(source) != shapes] + \
        ["robustness corpus: " + kind for kind, source in _CLOSE_SWEEP_CORPUS
         if _cs_robustness_failures([(kind, source)])]


def _close_sweep_check(root):
    """The sweep over its robustness corpus and every swept source without raising, then against its synthetic
    shapes and this tree's recorded dispositions. Returns failures."""
    import collections
    sources = [("<corpus {}>".format(kind), source) for kind, source in _CLOSE_SWEEP_CORPUS]
    for directory in _CLOSE_SWEEP_DIRS:
        for path in sorted((Path(root) / directory).glob("*.py")):
            sources.append((path.relative_to(root).as_posix(), path.read_text(encoding="utf-8")))
    failures = ["close sweep robustness: " + failure for failure in _cs_robustness_failures(sources)]
    if failures:
        return failures                                   # the rows cannot be compared while the sweep raises
    for label, source, shapes in _CLOSE_SWEEP_SHAPES:
        got = _cs_shapes_of(source)
        if got != shapes:
            failures.append("close sweep shape {}: expected {}, got {}".format(label, shapes, got))
    hits = collections.Counter(row[:1] + row[2:] for row in _close_sweep(root))
    recorded = {entry[:5]: entry for entry in _CLOSE_SWEEP_DISPOSITIONS}
    for key, count in sorted(hits.items()):
        if key not in recorded:
            failures.append("close sweep: an unrecorded hit {} x{}".format(key, count))
        elif count != recorded[key][5]:                   # a "part C" row, when present, keeps its count
            failures.append("close sweep: {} found {} times, recorded {}".format(key, count, recorded[key][5]))
    for key, entry in sorted(recorded.items()):
        if key not in hits and not entry[6].startswith("part C"):
            failures.append("close sweep: the recorded hit {} is no longer found; update its disposition".format(key))
    return failures


def self_test(red_on_revert):
    """Isolate fixture git calls, including in-process production helpers."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle():
        return _self_test_isolated(red_on_revert)


def _self_test_isolated(red_on_revert):
    cases = test_cases()
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="aiqt-release-cut-") as temporary:
        parent = Path(temporary)
        home = parent / "home"
        home.mkdir()
        (parent / "no-hooks").mkdir()
        env = fixture_environment(home)
        fixtures = {}
        for case_id, case in cases.items():
            root = fixture_repo(parent, case_id, case, env)
            fixtures[case_id] = root
            report = observe(script, root, case[-1], env)
            check(case_id, report["code"] == case[0])
            if case_id in ("ci-base-ref", "ci-merge-cut", "ci-merge-undeclared",
                           "ci-fresh-tracking", "base-ref-only-fresh"):
                binding = report["context"]
                _, parents = commit_header(root, binding["head"])
                check(case_id + "-first-parent",
                      len(parents) >= 2 and binding["base"] == parents[0] == binding["target"])
            if case_id in ("push-protected", "push-protected-full", "push-declared",
                           "push-undeclared"):
                binding = report["context"]
                _, parents = commit_header(root, binding["head"])
                check(case_id + "-first-parent",
                      binding["head"] == binding["target"] and binding["base"] == parents[0])
            if case_id in ("push-noop-undeclared", "push-noop-declared", "push-two-cuts",
                           "push-repaired", "push-root"):
                transitions = report["context"]["transitions"]
                rows = [row for row in report["snapshots"] if row["snapshot"].startswith("commit:")]
                check(case_id + "-range", len(transitions) == (1 if case_id == "push-root" else 2)
                      and [row["snapshot"] for row in rows]
                      == ["commit:" + revision for _, revision in transitions])
            if case_id == "push-protected":
                bare = observe(script, root, "push-bare", env)
                check("push-protected-without-argument",
                      bare["code"] == 2 and "push requires explicit" in bare["detail"])
            if case_id in ("push-origin-head", "push-origin-head-full"):
                check(case_id + "-ambiguous", "not origin/HEAD" in report["detail"])
            if case_id in ("ancestor-branch", "ancestor-origin", "ancestor-tag",
                           "candidate-self", "candidate-self-base",
                           "descendant-branch", "descendant-tag"):
                check(case_id + "-identity", "local --protected must resolve" in report["detail"])
            if case_id == "ancestor-base":
                check(case_id + "-binding", "local --base must equal" in report["detail"])
            if case_id in ("push-identity-branch", "push-identity-origin", "push-identity-tag"):
                check(case_id + "-identity", "push --protected must equal" in report["detail"])
            if case_id == "push-before-narrowed":
                check(case_id + "-binding", "push --base must equal" in report["detail"])
            if case_id == "ci-explicit-protected":
                check(case_id + "-binding", "derives its target" in report["detail"])
            if case_id == "staged-hidden":
                check("staged-hidden-snapshots",
                      [(r["snapshot"], r["code"]) for r in report["snapshots"]]
                      == [("HEAD", 0), ("index", 1), ("working", 0)])
            if case_id == "unreadable":
                check("unreadable-read-boundary",
                      any("injected unreadable working file" in row["detail"]
                          for row in report["snapshots"]))
            print("PASS " + case_id)
        close_failures, close_runs = _close_vectors(parent / "close")
        for failure in close_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("close-vectors", not close_failures)
        print("PASS close-vectors runs=" + str(close_runs))
        journal = (script.parents[1] / "opf" / "tools" / "_journal.py").read_text(encoding="utf-8")
        copy = (script.parent / "_close_selftest.py").read_text(encoding="utf-8")
        check("close-harness-in-step", _close_harness_in_step(journal, copy))
        require(copy.count("raise self.err") == 1, "close harness: drift flip anchor is not unique")
        check("close-harness-drift-red",
              not _close_harness_in_step(journal, copy.replace("raise self.err", "return None")))
        require(copy.count("\nimport sys\n") == 1, "close harness: preamble flip anchor is not unique")
        check("close-harness-preamble-red", not _close_harness_in_step(
            journal, copy.replace("\nimport sys\n", "\nimport sys\nos.close = lambda fd: None\n")))
        import _close_selftest
        drop_failures, drop_runs = _close_selftest._st_watch_drop_check()
        for failure in drop_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("close-harness-watch-drop-red", not drop_failures)
        print("PASS close-harness-in-step watch-drop-runs=" + str(drop_runs))
        sweep_failures = _close_sweep_check(script.parents[1])
        for failure in sweep_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("close-sweep", not sweep_failures)
        print("PASS close-sweep shapes={} corpus={} recorded={}".format(
            len(_CLOSE_SWEEP_SHAPES), len(_CLOSE_SWEEP_CORPUS), len(_CLOSE_SWEEP_DISPOSITIONS)))
        if red_on_revert:
            source = script.read_text(encoding="utf-8")
            marker = "# SELF-TEST:" + " mutation targets are restricted to the production prefix above."
            require(source.count(marker) == 1, "self-test mutation boundary is not unique")
            production, tests = source.split(marker)
            sweep, table, rest = tests.partition("\n_CS_LEGS = (")    # revert the sweep's code, not this table
            require(bool(table), "sweep revert boundary is missing")
            for name, old, new, labels in _CLOSE_SWEEP_REVERTS:
                require(sweep.count(old) == 1, name + ": sweep revert must match exactly once")
                mutant = {"__name__": "check_release_cut_" + name.replace("-", "_"), "__file__": str(script)}
                exec(compile(production + marker + sweep.replace(old, new, 1) + table + rest, str(script), "exec"),
                     mutant)
                red = mutant["_close_sweep_shapes_red"]()
                check(name, sorted(red) == sorted(labels))
                print("RED " + name + " at " + "; ".join(red))
            mutations = (
                ("declaration", "if not authorized:", "if False:", "undeclared", 0),
                ("declaration-initialize", "if not authorized:", "if False:",
                 "undeclared-initialize", 0),
                ("identity", 'identities = lambda rows: [row["version"] for row in rows]',
                 "identities = lambda rows: list(range(len(rows)))", "relabel", 0),
                ("digest",
                 'if head_decl is not None and head_decl["base-changelog-sha256"] != expected_digest:',
                 "if False:", "stale-digest", 0),
                ("input", 'return 2, "cannot evaluate: " + str(exc)',
                 'return 0, "cannot evaluate: " + str(exc)', "head-toml", 0),
                ("notes", 'identities = lambda rows: [row["version"] for row in rows]',
                 "identities = lambda rows: rows", "notes", 1),
                ("index", 'names = ("HEAD", "index", "working")',
                 'names = ("HEAD", "working")', "staged-hidden", 0),
                ("first-parent-binding", "if ci_context:", "if False:",
                 "ci-stale-tracking", 0),
                ("first-parent-tip", "require(parents[0] == target,",
                 "require(True,", "ci-stale-tracking", 1),
                ("explicit-protected", "root, binding = context(root, protected, before)",
                 "root, binding = context(root, None, before)", "push-protected", 2),
                ("descendant-target", "require(base != head or head == target,",
                 "require(True,", "behind-default", 0),
                ("candidate-self", "require(target == resolve(root, default),",
                 "require(True,", "candidate-self", 0),
                ("protected-identity", "require(target == resolve(root, default),",
                 "require(True,", "ancestor-branch", 0),
                ("local-base-binding", 'require(before == base,',
                 "require(True,", "ancestor-base", 0),
                ("push-identity", "require(protected == expected,",
                 "require(True,", "push-identity-origin", 1),
                ("push-before-binding", "require(before == event_before,",
                 "require(True,", "push-before-narrowed", 0),
                ("push-range", 'for parent, revision in binding["transitions"]:',
                 'for parent, revision in binding["transitions"][-1:]:', "push-noop-undeclared", 0),
                ("ci-event", 'require(event in ("pull_request", "push"),',
                 "require(True,", "event-dispatch", 0),
            )
            parser_bytes = (script.parents[1] / "opf" / "tools" / "_semver.py").read_bytes()
            for name, old, new, case_id, mutated_code in mutations:
                require(production.count(old) == 1, name + ": mutation must match exactly once")
                location = parent / ("mutation-" + name)
                (location / "tools").mkdir(parents=True)
                (location / "opf" / "tools").mkdir(parents=True)
                mutant = location / "tools" / script.name
                mutant.write_text(production.replace(old, new, 1) + marker + tests, encoding="utf-8")
                (location / "opf" / "tools" / "_semver.py").write_bytes(parser_bytes)
                report = observe(mutant, fixtures[case_id], cases[case_id][-1], env)
                require(report["code"] == mutated_code,
                        name + ": mutation did not produce its designated behavioral result")
                # Re-run the SAME exit-code oracle with the SAME expected code.
                # Only its own assertion may fail; crashes and unrelated failures are errors.
                try:
                    check(case_id, report["code"] == cases[case_id][0])
                except OracleFailure as exc:
                    require(exc.case_id == case_id, name + ": unrelated RED assertion")
                else:
                    raise CannotEvaluate(name + ": mutation survived its own oracle")
                print("RED " + name + " at " + case_id)
        print("PASS release-cut-selftest cases=" + ",".join(cases)
              + " red-on-revert=" + str(red_on_revert).lower())
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--protected", help="full ref or origin/BRANCH (non-PR runs only)")
    parser.add_argument("--base", help="full before commit OID for a non-PR first-parent range")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--red-on-revert", action="store_true")
    args = parser.parse_args(argv)
    if args.red_on_revert and not args.self_test:
        parser.error("--red-on-revert requires --self-test")
    try:
        if args.self_test:
            return self_test(args.red_on_revert)
        report = local_report(args.root, args.protected, args.base)
    except OracleFailure as exc:
        print("FAIL self-test assertion " + exc.case_id, file=sys.stderr)
        return 1
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError) as exc:
        report = {"code": 2, "detail": str(exc)}
    print(json.dumps(report, sort_keys=True))
    return report["code"]


if __name__ == "__main__":
    sys.exit(main())
