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
The optional .aiqt/pending-fixes.toml binds a hand-kept pending-fix list to one
cut: schema = 1, release = "<the version being cut>", and a fix array of
tables, each carrying an id and a state from open, done and deferred; a
deferred item also carries a rationale and target. Every id, rationale and
target must be a string holding a non-whitespace character. Appending a
release identity is refused while any listed item is open, naming each open
item; a cut that removes a record still listing open items is refused the
same way, naming them. A present record that is malformed, unreadable, or a
symlink cannot be evaluated on any transition; the release binding is checked
only on a cut, where a record bound to another release cannot be evaluated.
When neither snapshot holds a record, nothing changes. The list is hand-kept,
so it shows currency for the items it names, never completeness of what is
wrong.

A declaration is author-controlled intent, not authorization or readiness.
This reusable pack author tool is opt-in for adopters of this changelog schema.
For pull_request events or when GITHUB_BASE_REF is set, require a merge HEAD
whose raw first parent equals the resolved origin/GITHUB_BASE_REF tip, and use
that parent as the comparison base. Missing or mismatched bindings fail closed.
In GitHub Actions, only pull_request, push, workflow_dispatch and schedule are
supported. Push requires --protected equal to refs/remotes/origin/GITHUB_REF_NAME
(origin/BRANCH is normalized), and --base equal to the workflow's PUSH_BEFORE
event value. A workflow_dispatch or schedule run has no pushed range: it requires
the same --protected and refuses --base, and compares HEAD with its raw first
parent (a root HEAD with no parent compares against no snapshot).
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
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_release_cut.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import datetime
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opf" / "tools"))
from _semver import _parse  # noqa: E402

CHANGELOG = "changelog.toml"
DECLARATION = ".aiqt/release-cut.toml"
PENDING = ".aiqt/pending-fixes.toml"
PATHS = (CHANGELOG, DECLARATION, PENDING)
TIMEOUT = 30
# GitHub Actions events with no pushed range: the run checks the tip transition alone.
TIP_EVENTS = ("workflow_dispatch", "schedule")
OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
DECL_KEYS = {
    "schema", "operation", "previous-version", "next-version",
    "base-changelog-sha256",
}
PENDING_KEYS = {"schema", "release", "fix"}
PENDING_STATES = ("open", "done", "deferred")


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
    except (ValueError, RecursionError, MemoryError) as exc:
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


def parse_pending(raw, label="pending fixes"):
    """Accept absent or TOML bytes for the optional pending-fix record; validate the whole record."""
    if raw is None:
        return None
    data = document(raw, label)
    require(isinstance(data, dict) and set(data) == PENDING_KEYS,
            label + ": expected exactly the schema, release and fix fields")
    require(type(data["schema"]) is int and data["schema"] == 1,
            label + ": unsupported schema (expected integer 1)")
    version(data["release"], label + ": release")
    require(isinstance(data["fix"], list), label + ": fix must be an array of tables")
    identifiers = set()
    for index, item in enumerate(data["fix"]):
        where = "{}: fix[{}]".format(label, index)
        require(isinstance(item, dict), where + ": expected table")
        require(item.get("state") in PENDING_STATES,
                where + ": state must be open, done or deferred")
        keys = {"id", "state", "rationale", "target"} if item["state"] == "deferred" else {"id", "state"}
        require(set(item) == keys,
                where + ": a deferred item carries exactly id, state, rationale and target;"
                " an open or done item exactly id and state")
        for key in keys - {"state"}:
            require(isinstance(item[key], str) and bool(item[key].strip()),
                    where + ": " + key + " must be a nonblank string")
        require(item["id"] not in identifiers, where + ": duplicate fix id")
        identifiers.add(item["id"])
    return dict(data)


def evaluate_transition(base_changelog_bytes_or_absent, head_changelog_bytes,
                        base_decl, head_decl, base_pending=None, head_pending=None):
    """Return (exit_code, detail). Only the caller can prove that None is absence."""
    try:
        base = ([] if base_changelog_bytes_or_absent is None else
                parse_changelog(base_changelog_bytes_or_absent, "base changelog"))
        head = parse_changelog(head_changelog_bytes, "head changelog")
        base_decl = parse_declaration(base_decl, "base declaration")
        head_decl = parse_declaration(head_decl, "head declaration")
        base_pending = parse_pending(base_pending, "base pending fixes")
        head_pending = parse_pending(head_pending, "head pending fixes")
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
    if head_pending is not None:
        # The record proves currency only for the one cut it names, never completeness.
        if head_pending["release"] != after[-1]:
            return 2, ("cannot evaluate: pending fixes: record is bound to release "
                       + head_pending["release"] + ", not to the appended " + after[-1])
        open_items = [item["id"] for item in head_pending["fix"] if item["state"] == "open"]
        if open_items:
            return 1, "pending fixes still open: " + ", ".join(open_items)
    elif base_pending is not None:
        # Removing the record does not resolve its items: a cut that deletes a
        # record still listing open items is refused like one that leaves them open.
        removed_open = [item["id"] for item in base_pending["fix"] if item["state"] == "open"]
        if removed_open:
            return 1, ("pending fixes still open: " + ", ".join(removed_open)
                       + " (record removed, not resolved)")
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
        require(event in ("pull_request", "push") + TIP_EVENTS,
                "unsupported GitHub Actions event: " + event)
        if event == "push":
            require(protected is not None and before is not None,
                    "push requires explicit --protected and --base")
        elif event in TIP_EVENTS:
            # No pushed range exists (the event carries no before), so the run compares the
            # checked-out tip with its raw first parent, never a caller-chosen base.
            require(protected is not None and before is None,
                    event + " requires explicit --protected and refuses --base")
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
        if actions and event in ("push",) + TIP_EVENTS:
            branch = os.environ.get("GITHUB_REF_NAME", "")
            require(os.environ.get("GITHUB_REF_TYPE") == "branch" and bool(branch),
                    event + " requires a branch GITHUB_REF_NAME and GITHUB_REF_TYPE=branch")
            expected = "refs/remotes/origin/" + branch
            git(root, "check-ref-format", expected)
            require(protected == expected,
                    event + " --protected must equal " + expected)
            if event == "push":
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
        if actions and event in ("push",) + TIP_EVENTS:
            require(head == target, event + " HEAD does not match --protected target")
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
            base = ((None,) * len(PATHS) if base_tree is None else
                    tuple(tree_blob(root, base_tree, path) for path in PATHS))
            if name == "HEAD" or name.startswith("commit:"):
                candidate = tuple(tree_blob(root, head_tree, path) for path in PATHS)
            elif name == "index":
                candidate = index_snapshot(root)
            else:
                candidate = tuple(working_blob(root, path) for path in PATHS)
            code, detail = evaluate_transition(base[0], candidate[0], base[1], candidate[1],
                                               base[2], candidate[2])
            results.append({
                "snapshot": name, "code": code, "detail": detail,
                "base_changelog_sha256": digest(base[0]),
                "head_changelog_sha256": digest(candidate[0]),
                "base_declaration_sha256": digest(base[1]),
                "head_declaration_sha256": digest(candidate[1]),
                "base_pending_fixes_sha256": digest(base[2]),
                "head_pending_fixes_sha256": digest(candidate[2]),
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


def fixture_pending(release="1.1.0", *items):
    text = "schema = 1\nrelease = " + json.dumps(release) + "\n"
    if not items:
        return (text + "fix = []\n").encode()
    for item in items:
        text += "\n[[fix]]\n" + "".join(key + " = " + json.dumps(value) + "\n" for key, value in item)
    return text.encode()


def test_cases():
    """Transition policy, local ancestry, PR merge binding and explicit push refs."""
    base = fixture_changelog(["1.0.0"])
    cut = fixture_changelog(["1.0.0", "1.1.0"])
    declaration = fixture_declaration(base)
    history = fixture_declaration(base, **{"base-changelog-sha256": "f" * 64})
    cases = {}

    def add(name, expected, before=base, after=base, old=None, new=None,
            fixes=None, base_fixes=None, tweak=None):
        require(name not in cases, "duplicate self-test ID: " + name)
        cases[name] = (expected, before, after, old, new, fixes, base_fixes, tweak)

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
    open_fix = fixture_pending("1.1.0", (("id", "loose-end"), ("state", "open")))
    done_fix = fixture_pending("1.1.0", (("id", "loose-end"), ("state", "done")))
    deferred_fix = fixture_pending("1.1.0", (("id", "loose-end"), ("state", "deferred"),
                                             ("rationale", "waiting on the next cycle"),
                                             ("target", "1.2.0")))
    add("pending-open", 1, after=cut, new=declaration, fixes=open_fix)
    add("pending-open-second", 1, after=cut, new=declaration,
        fixes=fixture_pending("1.1.0", (("id", "groundwork"), ("state", "done")),
                              (("id", "loose-end"), ("state", "open"))))
    add("pending-open-initialize", 1, before=None,
        new=fixture_declaration(None, following="1.0.0"),
        fixes=fixture_pending("1.0.0", (("id", "loose-end"), ("state", "open"))))
    add("pending-done-initialize", 0, before=None,
        new=fixture_declaration(None, following="1.0.0"),
        fixes=fixture_pending("1.0.0", (("id", "loose-end"), ("state", "done"))))
    add("pending-bound-elsewhere", 2, after=cut, new=declaration,
        fixes=fixture_pending("1.0.0", (("id", "loose-end"), ("state", "done"))))
    add("pending-done", 0, after=cut, new=declaration, fixes=done_fix)
    add("pending-deferred", 0, after=cut, new=declaration, fixes=deferred_fix)
    add("pending-empty", 0, after=cut, new=declaration, fixes=fixture_pending())
    add("pending-open-no-cut", 0, fixes=open_fix)
    add("pending-progress", 0, base_fixes=open_fix, fixes=done_fix)
    add("pending-open-removed", 1, after=cut, new=declaration, base_fixes=open_fix)
    add("pending-done-removed", 0, after=cut, new=declaration, base_fixes=done_fix)
    add("pending-symlink", 2, tweak="pending-symlink")
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
    bad_pending = {
        "toml": b"[", "utf8": b"\xff", "empty": b"",
        "schema": done_fix.replace(b"schema = 1", b"schema = 2"),
        "boolean": done_fix.replace(b"schema = 1", b"schema = true"),
        "release": done_fix.replace(b'"1.1.0"', b'"01.1.0"'),
        "unknown": b"extra = 1\n" + done_fix,
        "item-unknown": done_fix + b"extra = 1\n",
        "state": done_fix.replace(b'"done"', b'"fixed"'),
        "blank-id": done_fix.replace(b'"loose-end"', b'""'),
        "typed-id": done_fix.replace(b'"loose-end"', b"7"),
        "space-id": done_fix.replace(b'"loose-end"', b'" "'),
        "duplicate-id": done_fix + b'\n[[fix]]\nid = "loose-end"\nstate = "done"\n',
        "missing-fix": b'schema = 1\nrelease = "1.1.0"\n',
        "typed-fix": b'schema = 1\nrelease = "1.1.0"\nfix = 7\n',
        "typed-item": b'schema = 1\nrelease = "1.1.0"\nfix = [7]\n',
        "extra-field": done_fix + b'target = "1.2.0"\n',
        "missing-rationale": deferred_fix.replace(b'rationale = "waiting on the next cycle"\n', b""),
        "missing-target": deferred_fix.replace(b'target = "1.2.0"\n', b""),
        "blank-rationale": deferred_fix.replace(b'"waiting on the next cycle"', b'""'),
        "space-rationale": deferred_fix.replace(b'"waiting on the next cycle"', b'" "'),
        "space-target": deferred_fix.replace(b'"1.2.0"', b'" "'),
    }
    for label, raw in bad_pending.items():
        add("head-fix-" + label, 2, fixes=raw)
        add("base-fix-" + label, 2, base_fixes=raw)
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
    for name in ("event-merge-group", "event-missing"):
        add(name, 2, tweak=name)
    for event in ("dispatch", "schedule"):
        add("tip-" + event, 0, tweak="tip-" + event)
        add("tip-" + event + "-declared", 0, after=cut, new=declaration, tweak="tip-" + event)
        add("tip-" + event + "-undeclared", 1, after=cut, tweak="tip-" + event)
        for name in ("base", "bare", "identity", "ref-tag"):
            add("tip-" + event + "-" + name, 2, after=cut, tweak="tip-" + event + "-" + name)
        add("tip-" + event + "-target-mismatch", 2, tweak="tip-" + event + "-target-mismatch")
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
    expected, before, after, old, new, fixes, base_fixes, tweak = case
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

    def write(log, decl, pending=None):
        for path, data in ((CHANGELOG, log), (DECLARATION, decl), (PENDING, pending)):
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
        write(after, new, fixes)
        head = commit("initialized")
        command("update-ref", "refs/remotes/origin/main", head)
        command("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        return root
    write(before, old, base_fixes)
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
    write(after, new, fixes)
    (root / "consumer.txt").write_text("ordinary consumer edit\n", encoding="utf-8")
    if tweak == "squash":
        # A preparation commit exists, but the reviewed endpoint is one squashed commit.
        write(after, old, base_fixes)
        commit("preparation")
        write(after, new, fixes)
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
    elif tweak == "protected" or (tweak and tweak.startswith(("push-", "tip-"))):
        if tweak in ("push-identity-branch", "push-identity-origin", "push-identity-tag",
                     "push-before-narrowed", "tip-dispatch-identity", "tip-schedule-identity"):
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
            write(before, old, base_fixes)
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
        if tweak not in ("push-target-mismatch", "tip-dispatch-target-mismatch",
                         "tip-schedule-target-mismatch"):
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
    elif tweak == "pending-symlink":
        (root / PENDING).symlink_to("seed.txt")
        commit("symlink pending fixes")
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
    if tweak and tweak.startswith("tip-"):
        env.update({"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_BASE_REF": "",
                    "GITHUB_EVENT_NAME": ("schedule" if tweak.startswith("tip-schedule")
                                          else "workflow_dispatch"),
                    "GITHUB_REF_NAME": "main", "GITHUB_REF_TYPE": "branch"})
        env.pop("PUSH_BEFORE", None)
        if tweak.endswith("-ref-tag"):
            env["GITHUB_REF_TYPE"] = "tag"
        if not tweak.endswith("-bare"):
            args += ["--protected", "origin/attacker" if tweak.endswith("-identity") else "origin/main"]
        if tweak.endswith("-base"):
            args += ["--base", resolve(root, "refs/heads/main")]
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
            "event-merge-group": "merge_group", "event-missing": "",
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


def _close_census_eio(harness, seam="fstat", code=None):
    """The copy's descriptor census fails closed: a held /dev/null descriptor is listed by
    harness._st_fd_table; with its census read failing errno `code` (EIO by default; os.<seam>: its fstat, or
    the readlink of its /proc/self/fd link) the table must raise harness._StCensusError naming it, never omit
    it as closed. The one exception is ENOENT on the link read (the number has no entry), where the table must
    list the descriptor with no kind (None). An independent fstat must still see it open afterwards, else the
    check proves nothing."""
    import errno
    code = errno.EIO if code is None else code
    fd = os.open(os.devnull, os.O_RDONLY)
    real_fstat, real_readlink = os.fstat, os.readlink
    real_seam = real_readlink if seam == "readlink" else real_fstat

    def failing(number, *args, **kwargs):
        if number in (fd, "/proc/self/fd/{}".format(fd)):
            raise OSError(code, "injected census read failure")
        return real_seam(number, *args, **kwargs)
    try:
        listed = fd in harness._st_fd_table()
        if seam == "readlink":
            os.readlink = failing
        else:
            os.fstat = failing
        try:
            table = harness._st_fd_table()
            got = "omitted" if fd not in table else "no-kind" if table[fd][2] is None else "kind"
        except harness._StCensusError as exc:
            got = "raised" if "descriptor {}:".format(fd) in str(exc) else "unnamed"
        finally:
            os.fstat, os.readlink = real_fstat, real_readlink
        try:
            real_fstat(fd)
            still_open = True
        except OSError:
            still_open = False
    finally:
        os.close(fd)
    want = "no-kind" if (seam, code) == ("readlink", errno.ENOENT) else "raised"
    return listed and still_open and got == want


def _close_anon_reuse_close_fault(harness):
    """The copy's harness._st_anon_reuse leaves no descriptor open when its epoll's close raises. select.epoll
    is wrapped so close() closes the real epoll and then raises OSError(EIO); the call must raise that error,
    and harness._st_fd_table must read as it did before the call. None where there is no epoll or eventfd."""
    import errno
    import select
    if not hasattr(select, "epoll") or not hasattr(os, "eventfd"):
        return None
    real_epoll = select.epoll

    class RaisingEpoll:
        def __init__(self):
            self.real = real_epoll()

        def fileno(self):
            return self.real.fileno()

        def close(self):
            self.real.close()
            raise OSError(errno.EIO, "injected epoll close failure")

    before = harness._st_fd_table()
    select.epoll = RaisingEpoll
    try:
        harness._st_anon_reuse(harness._st_fd_table)
        raised = False
    except OSError as exc:
        raised = exc.errno == errno.EIO
    finally:
        select.epoll = real_epoll
    return raised and harness._st_fd_table() == before


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
#   AFTER   a close in a try body under a handler that does not re-raise, or a close in a handler, the else block
#           or the finally that control can pass before it falls off that block's end (a block whose last
#           statement is a raise or a return gives none, however deeply the close is nested in it), closed again
#           by a statement that can follow the try;
#   REBIND  a close made by a call statement or by a with statement's exit, followed by a re-binding of the
#           closed name or attribute, or of any key in its alias class (fd = self.pidfd; os.close(fd);
#           self.pidfd = None re-binds the closed number's attribute), in a statement that can follow it
#           (close-then-rebind, in a loop or not), reported whether or not a cleanup reaches it;
#   ATTR    a close of self.<attr> by a call statement, followed the same way by its re-binding while another
#           method of the class closes the same attribute.
# The statements that can follow a statement are the rest of its own block, then the rest of every block
# enclosing it, out to the function body (a close in an if, with, loop, match case or try block pairs with a
# re-binding or a close after that block); for a statement in a try body, the try's else block comes first,
# and for a statement in a for or while body (async for too), the loop's else block comes first.
# Blocks are found from each statement's own fields (every list of statements, a handler's or a match case's
# body included), so every compound statement kind is walked, and a nested function, lambda or class is never
# entered (each function and method is swept on its own). A close is a call whose callee name contains "close"
# (os.close, every _close_* helper; not closerange or closed) of its first argument, an argument-less,
# keyword-less x.close() of x, and the exit of a with statement over an item rule 6 counts (below). A re-binding
# is an =, augmented or annotated assignment to the key, alone or as an element of a target tuple.
# The alias grammar, in full. Keys are a name, an attribute chain (self.fd) and a container's element key ([]c,
# one per container, whatever the index or dict key). Two keys are aliases only when these rules join them,
# transitively and flow-insensitively over the whole function: statement order, branches and joins play no
# part, so every alias a name is given anywhere in the function holds at every use of it:
#   1. a plain re-binding, a = b or a = self.b (an augmented c += d, and an annotated a: T = b, too), which also
#      joins the two keys' element keys, so a container bound to a second name (saved = fds) shares its
#      elements; and a chained assignment, a = b = v, which binds one object to every target, so its keyed
#      targets join each other whatever v is (a call such as os.open(p, 0), a name, a display), and each also
#      takes v as a single assignment of it would;
#   2. a tuple or list assignment whose right side is a tuple or list display of the same length, with no starred
#      element on either side, binds each target to its element exactly as a plain assignment of that element would
#      (rule 1, or rules 3, 6 and 7, or this rule again for a nested target tuple or list): fd, a = a, None joins
#      fd and a, a swap a, b = b, a joins a and b, (a, b), c = (fd, x), None joins a and fd, and fds, other = [a],
#      None joins []fds and a. Every other unpacking assignment (from a set or dict display, a starred target or
#      element, a length mismatch, or a right side that is no display: a name, an attribute, an element or a call)
#      joins every key it binds (each target, each key of a nested target tuple or list, and a starred target's
#      element key) to every key its right side can yield: the element keys that side supplies as a container
#      expression (rule 4: a display's elements, a dict display's keys and values, or the element key of the
#      container it names, wrappers removed, just as a loop over it joins, rule 5), and the element keys of the
#      container each starred element of a display names; so a, = {fd}, a, = {fd: None}, *a, b = fd, x, a, b, c =
#      *fds, None and a, b = pair all join (conservative: a target is joined to every element, not only its own).
#      There is no move rule: a source handed on keeps its alias class after the move, in every branch and after
#      every join, so a later close of it pairs with every close of the value it handed on (conservative: it
#      reports more, never less). The P1 ownership-first idiom is therefore reported on purpose: fd, a = a, None;
#      os.close(fd) under a cleanup `if a is not None: os.close(a)`, and prev, cur = cur, nxt; os.close(prev) under
#      a cleanup of cur, are TRY (or AFTER) hits, and each tree site of the idiom is a recorded false positive in
#      the dispositions below, with its reason;
#   3. a container's element key joins each element of a tuple, list or set display, and each key and each
#      value of a dict display ({a: x} and {k: a} alike), bound to it or appended, inserted or added into it; a
#      value stored into it by subscript (c[i] = a; the index i is not joined); and the element keys a
#      container expression (rule 4) supplies when that expression is bound to it, or extended or updated
#      into it;
#   4. a container expression has its wrappers removed first (c.values(), c.items(), c.keys(), c.copy(),
#      reversed(c), list(c), tuple(c), sorted(c), set(c), nested), and what is left is classified: a + b
#      concatenation and an or / and expression (each operand classified the same way), a display, or a
#      named container. A dict's keys and values share its one element key, so iterating c.keys(), c.values()
#      or c.items() reads both;
#   5. a loop's target key, and each key in a loop's target tuple (for fd, _ in c.items()), joins the elements
#      of the container expression (rule 4) it iterates, a comprehension's too;
#   6. a file object os.fdopen(k), open(k) or socket.fromfd(k) wraps k in (k a name, an attribute chain or an
#      element key), bound to a key (alone or as a tuple element) or as a with target, and the target of
#      `with k as f` (k a key), join k once k is an alias of a key a close call in the function passes as its
#      first argument; a call passing closefd=False wraps nothing (that object never closes the descriptor). A
#      with statement's exit closes k when its item is such a wrapper of k and k is such an alias, and closes
#      its context key when that key is in the class of a key this rule joined (fh = os.fdopen(a); with fh as
#      g: ... closes a); so the object's close, and the with statement's exit, count against every alias of k;
#   7. a keyword capture: ns = SimpleNamespace(pidfd=fd) makes ns.pidfd an alias of fd.
# Residual, plainly: any alias form outside that grammar is not followed, and a double close through it passes
# the sweep unseen. The known gaps it leaves: an element target, which joins only the container's one element
# key (a swap through c[i]); the order of statements beyond the following rule (a statement after a return,
# raise or os._exit still counts as following, a loop's else follows its body even past a break, a loop's next
# iteration does not follow, and a try's finally is not a following statement of its body); a close inside a
# lambda, which is never swept; a repeated close with no try between (os.close(a); os.close(a)), which no shape
# pairs; a close in a handler, else block or finally whose block ends in a raise or a return, closed again
# after the try along a break or continue that leaves the block before that end, which AFTER leaves unpaired;
# the ATTR shape, which still matches only the exact closed attribute, not its alias class; a close made
# through a callee whose name lacks "close" or through a function passed as a value; a second close in another
# function (a caller's cleanup, or another method outside the ATTR shape); a with statement's exit in the ATTR
# shape, and a re-binding by a for or with target, a match capture, an assignment expression (:=), del or
# import, which no shape counts; an element closed by index (fds[i] is one key per container, and a nested
# container shares the outer element key only by subscript); a container appended or inserted into another as
# one element; a nested or starred target in a loop (for (a, b), c in x; for a, *b in x), and a re-binding by a
# nested or starred target element in an assignment ((a, b), c = ...; a, *b = ...); a starred element of any
# display but an unpacking's right side itself ([*c] bound, appended, iterated, or nested inside that right
# side; and a dict display's **d joins d itself, not d's elements, in an unpacking too); a conditional
# expression (c if x else d); an alias made by an assignment expression, a match capture or a with target over
# anything but a key or a rule-6 wrapper; a comprehension or a container returned by any call other than the
# rule-4 wrappers, whether bound, iterated or unpacked (a, b = helper(fd) joins neither to fd); a close in
# module-level code or in a class body outside any method, which receives no close-pair analysis;
# and descriptors held by objects the sweep does not know wrap one (sockets, subprocess pipes, selectors).
# The flow-insensitive join is also the sweep's main source of false positives: one name re-bound to unrelated
# descriptors in turn (sequential self-test legs, a walk's prev, cur = cur, nxt) is one class, so each such
# tree site is disposed below.
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


def _cs_aliases(fn):
    """The function's alias classes, by union-find over every alias the sweep follows, flow-insensitively, as
    (same, held): same(key, other_key) tests two keys, and held(item) is the key a with item's exit closes
    (rule 6), or None."""
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

    def elements(node):
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return [_cs_key(e) for e in node.elts if _cs_key(e)]
        if isinstance(node, ast.Dict):
            return [_cs_key(e) for e in node.keys + node.values if _cs_key(e)]   # {a: x} and {k: a} alike
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
        named = _cs_key(bare)
        return elements(bare) + (["[]" + named.lstrip("[]")] if named else [])

    wraps = []                                    # rule 6's candidates, joined once their key is a closed one

    def bind(target, value):
        """Join the key `target` to a value that is not itself a key (rules 3, 6 and 7)."""
        for element in contents(value):           # a display, list(c), c.copy(), c or {}, a + b
            union("[]" + target.lstrip("[]"), element)
        if _cs_wrapped(value) is not None:
            wraps.append((target, _cs_key(_cs_wrapped(value))))
        if isinstance(value, ast.Call):
            for kw in value.keywords:
                if kw.arg and _cs_key(kw.value):
                    union(target + "." + kw.arg, _cs_key(kw.value))

    def leaves(target):
        """The keys an unpacking target binds: a keyed element, each key of a nested tuple or list, and the
        element key of a starred one (*rest is a list of what it takes)."""
        if isinstance(target, (ast.Tuple, ast.List)):
            return [k for t in target.elts for k in leaves(t)]
        if isinstance(target, ast.Starred):
            return ["[]" + k.lstrip("[]") for k in leaves(target.value)]
        return [_cs_key(target)] if _cs_key(target) else []

    def yields(node):
        """Every key unpacking `node` can hand a target: the element keys it supplies as a container expression
        (rule 4: a display's elements, a dict display's keys and values, the element key of a named or wrapped
        container), and the element keys of the container each starred element of a display names."""
        starred = [e.value for e in getattr(node, "elts", ()) if isinstance(e, ast.Starred)]
        return contents(node) + [k for e in starred for k in contents(e)]

    def assign(target, value):
        """Rules 1 and 2 for one target of an assignment of `value`."""
        if isinstance(target, (ast.Tuple, ast.List)):
            parts = getattr(value, "elts", [])
            if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(parts) \
                    and not any(isinstance(e, ast.Starred) for e in target.elts + parts):
                for t, v in zip(target.elts, parts):   # rule 2: each element as a plain assignment
                    assign(t, v)
                return
            for key in leaves(target):                # anything else: each target takes all the value yields
                for element in yields(value):
                    union(key, element)
        elif _cs_key(target) and _cs_key(value):
            union(_cs_key(target), _cs_key(value))
        elif _cs_key(target):
            bind(_cs_key(target), value)

    for node in _cs_walk(fn.body):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            chained = [_cs_key(t) for t in targets if _cs_key(t)]
            for other in chained[1:]:
                union(chained[0], other)          # rule 1: a = b = v binds one object to every target
            for target in targets:
                assign(target, value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("append", "extend", "insert", "add", "update") and _cs_key(node.func.value):
            box = "[]" + _cs_key(node.func.value).lstrip("[]")
            for arg in node.args[1:] if node.func.attr == "insert" else node.args:
                if node.func.attr in ("extend", "update"):
                    items = contents(arg)             # the elements of another container, not its name
                else:
                    items = [_cs_key(arg)] if _cs_key(arg) else elements(arg)
                for element in items:
                    union(box, element)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                inner = _cs_wrapped(item.context_expr)
                source = _cs_key(item.context_expr) or (inner is not None and _cs_key(inner))
                if item.optional_vars is not None and _cs_key(item.optional_vars) and source:
                    wraps.append((_cs_key(item.optional_vars), source))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for target in [node.target] + list(getattr(node.target, "elts", [])):   # for fd, _ in c.items()
                for element in contents(node.iter) if _cs_key(target) else ():
                    union(_cs_key(target), element)
    closed = [_cs_close_arg(n) for n in _cs_walk(fn.body) if _cs_close_arg(n) and n.args]

    def closing(k):
        """Whether `k` is an alias of a key some close call in the function closes."""
        return find(k) in {find(c) for c in closed}

    joined = True
    while joined:                                 # a wrapper joins once its key is an alias of a closed one
        joined = [(a, b) for a, b in wraps if find(a) != find(b) and closing(b)]
        for a, b in joined:
            union(a, b)

    def held(item):
        """The key a with item's exit closes: the key its rule-6 wrapper wraps, when that is an alias of a
        closed key; or its context key, when that is in the class of a key rule 6 joined (fh = os.fdopen(a);
        with fh as g); else None."""
        inner = _cs_wrapped(item.context_expr)
        if inner is not None:
            return _cs_key(inner) if closing(_cs_key(inner)) else None
        k = _cs_key(item.context_expr)
        return k if k and find(k) in {find(a) for a, b in wraps if closing(b)} else None
    return (lambda a, b: find(a) == find(b)), held


def _cs_closes(nodes, held=None):
    """Each close under `nodes` with the key it closes; a with statement's exit counts for each item `held`
    names the key of (rule 6)."""
    import ast
    found = []
    for node in _cs_walk(nodes):
        if _cs_close_arg(node):
            found.append((node, _cs_close_arg(node)))
        elif held and isinstance(node, (ast.With, ast.AsyncWith)):
            found += [(node, held(item)) for item in node.items if held(item)]
    return found


def _cs_falling(nodes, held=None):
    """Each close (with its key, as _cs_closes) under the block `nodes` that control can pass before it falls
    off the block's end: none when the block's last statement is a raise or a return; else each statement's own
    closes (a with statement's exit, a close in an if or while test), and for a compound statement those of each
    of its blocks, found the same way, so a nested block ending in a raise or a return gives none."""
    import ast
    if not nodes or isinstance(nodes[-1], (ast.Raise, ast.Return)):
        return []
    found = []
    for stmt in nodes:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        blocks = [block for _field, block in _cs_blocks(stmt)]
        inside = {id(n) for block in blocks for n in _cs_walk(block)}
        found += [(c, k) for c, k in _cs_closes([stmt], held) if id(c) not in inside]
        for block in blocks:
            found += _cs_falling(block, held)
    return found


def _cs_rebinds(stmt, key, same=None):
    """Whether `stmt` re-binds `key` or, given the function's alias test `same`, any key in its alias class
    (fd = self.pidfd; os.close(fd); self.pidfd = None re-binds the closed number's attribute)."""
    import ast
    same = same or (lambda a, b: a == b)
    for node in _cs_walk([stmt]):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                keys = [_cs_key(t) for t in [target] + list(getattr(target, "elts", []))]
                if any(k and same(k, key) for k in keys):
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
                     if same(k, o)]
    for stmt, following in _cs_sequence(fn.body):
        if isinstance(stmt, ast.Expr) and _cs_close_arg(stmt.value) \
                and any(_cs_rebinds(after, _cs_close_arg(stmt.value), same) for after in following):
            rows.append((rel, stmt.lineno, fn.name, "REBIND", _cs_close_arg(stmt.value), ""))
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            rows += [(rel, stmt.lineno, fn.name, "REBIND", k, "") for c, k in _cs_closes([stmt], held)
                     if c is stmt and any(_cs_rebinds(after, k, same) for after in following)]
        if _cs_is_try(stmt):
            swallowing = [h for h in stmt.handlers if not (h.body and isinstance(h.body[-1], ast.Raise))]
            body = list(stmt.body) if swallowing else []
            ended = [f for b in [h.body for h in stmt.handlers] + [stmt.orelse, stmt.finalbody]
                     for f in _cs_falling(b, held)]
            rows += [(rel, c.lineno, fn.name, "AFTER", k, o) for c, k in _cs_closes(body, held)
                     for a, o in _cs_closes(following, held) if same(k, o)]
            rows += [(rel, c.lineno, fn.name, "AFTER", k, o) for c, k in ended
                     for a, o in _cs_closes(following, held) if same(k, o)]
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
    import _optlevel
    tree = _optlevel.parse(text, rel)
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
_CS_FRESH = ("false positive: sequential {}; every later close of the name follows a fresh binding of it in that "
             "close's own block (a later {}), so it closes that number, never the one this finally closed")
_CS_JOINED = ("false positive: {} {} only through the one element key of {}, so the alias-aware re-binding of the "
              "later {} is a {}, not a descriptor")
_CS_P1 = ("false positive, the P1 ownership-first idiom, reported on purpose (rule 2 keeps a moved source in its "
          "alias class): {}")
_CLOSE_SWEEP_DISPOSITIONS = (
    ("opf/tools/_opf_oplock.py", "propagating", "AFTER", "fd", "fd", 1,
     "part C: false positive, part C's _st_reclose_yielding, the deliberate RECLOSE body its T-p2 flip runs; the "
     "site exists only in part C (found by this sweep at 15f2f498, absent here)"),
    ("opf/tools/_journal.py", "body", "AFTER", "fd", "fd", 1,
     "false positive: the harness's R flip (reclose), the deliberate pre-P1 body each R leg must turn red"),
    ("tools/_close_selftest.py", "body", "AFTER", "fd", "fd", 1,
     "false positive: the harness copy's R flip (reclose), byte-identical to _journal's"),
    ("opf/tools/_opf_check.py", "_q_reclose", "AFTER", "fd", "fd", 1,
     "false positive: #377's RECLOSE flip, the deliberate pre-P1 confirm-then-reclose body its "
     "p1-close-fd-quietly-reclose-flip legs must turn red"),
    ("opf/tools/_opf_store.py", "_r7_flip_reclose", "AFTER", "fd", "fd", 1,
     "false positive: the r7 one-close legs' RECLOSE flip, the deliberate pre-P1 _close_fd_propagating body each "
     "r7-*-one-close-red-under-reclose leg must turn red"),
    ("opf/tools/_opf_store.py", "_r7w_reclose", "AFTER", "fd", "fd", 1,
     "false positive: #377's RECLOSE flip, the deliberate pre-P1 _close_fd_exc_safe body the "
     "r7-working-reclose-flip legs must turn red"),
    ("opf/tools/_opf_store.py", "_r7w_vector", "AFTER", "[]seen", "[]seen", 1,
     "false positive: seen[\"pfd\"] and seen[\"wfd\"] share only the one element key of the dict seen; the parent "
     "and the child opened beneath it (dir_fd=pfd) are held at once, so they are two numbers, and each is closed "
     "once, the parent's only while it still names the vector's unrelated file"),
    ("opf/tools/opf.py", "reclose", "AFTER", "fd", "fd", 1,
     "false positive: #377's RECLOSE flip in _close_exc_safe_vectors_self_test, the deliberate pre-P1 "
     "confirm-then-reclose body every V1 and V2 vector must turn red under"),
    ("opf/tools/opf.py", "_close", "REBIND", "fd", "", 1,
     "false positive: the V1 fault stub's own release-then-reuse in _close_exc_safe_vectors_self_test: "
     "real_close(fd) is the injected release, os.dup2 then puts the unrelated file on the number, and "
     "state.number = fd records that new holder; the stub never closes the released descriptor again, and "
     "p1_fails closes state.number only while it still names the unrelated file"),
    ("opf/tools/_opf_adopt_observe.py", "guarded", "TRY", "stack", "stack", 1,
     "false positive: contextlib.ExitStack.close pops each callback before it runs it, so a repeat runs none twice"),
    ("opf/tools/_opf_adopt_observe.py", "run_case", "TRY", "client", "client", 1,
     "false positive: the client closed here is never appended to backlog_clients, which the cleanup loop closes"),
    ("opf/tools/_opf_adopt_observe.py", "run_case", "TRY", "[]resolver_sockets", "[]resolver_sockets", 2,
     "false positive: these are socket.socket objects; the first close() sets their fileno to -1, so the "
     "finally's second close() of each closes no descriptor, even after the number is reused"),
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
    ("opf/tools/_opf_check.py", "self_test", "REBIND", "sfd", "", 1,
     _CS_LEGS.format("os.open of a staged-ids fixture root")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "REBIND", "jr_fd", "", 3,
     _CS_LEGS.format("journal-root open (or None until one)")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "REBIND", "root_fd", "", 10,
     _CS_LEGS.format("_open_dir_nofollow or _open_product_root")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "REBIND", "jfd", "", 1,
     _CS_LEGS.format("journal-root open")),
    ("opf/tools/_opf_adopt_apply.py", "run_adopt_transaction", "TRY", "mine_held", "mine_held", 1,
     "false positive: _close_held and _close_held_into pop each number out of the list before its one close, "
     "so the final close-out's pass over a list the release path already emptied closes none twice"),
    ("opf/tools/_opf_emit.py", "_boom_close7", "AFTER", "fd", "fd", 1,
     "false positive: a self-test close stub; its swallowed close is followed by a raise in the same block, so the "
     "later close never runs after it"),
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
    ("opf/tools/opf.py", "_watchdog_completion_case", "REBIND", "subject_fd", "", 2,
     _CS_LEGS.format("pidfd (or, for fake.subject_pidfd, a fresh types.SimpleNamespace stand-in's field), the "
                     "alias class joining them only through each leg's keyword capture subject_pidfd=")),
    ("opf/tools/_opf_emit.py", "_guardian", "TRY", "fd", "subject_fd", 1,
     _CS_P1.format("fd, subject_fd = subject_fd, None runs before os.close(fd), so the handler's `if subject_fd "
                   "is not None: os.close(subject_fd)` never reaches the closed number")),
    ("opf/tools/_opf_init_observe.py", "graft_snapshot", "TRY", "prev", "fd", 1,
     _CS_P1.format("prev, fd = fd, next_fd runs before os.close(prev), so the finally closes next_fd, never prev")),
    ("opf/tools/_opf_init_substrate.py", "begin_operation", "TRY", "closing", "open_fd", 1,
     _CS_P1.format("closing, plan_fd = plan_fd, None runs before the close of closing, so the unwind loop over "
                   "(plan_fd, op_fd, ops_fd) skips plan_fd, and the body never closes op_fd or ops_fd")),
    ("tools/check_crosswalk.py", "_open_archive_dir", "TRY", "prev", "fd", 1,
     _CS_P1.format("prev, fd = fd, nxt runs before the close of prev, so the handler closes nxt, never prev")),
    ("tools/gen_skill.py", "_open_dir_fd", "TRY", "prev", "fd", 1,
     _CS_P1.format("prev, fd = fd, nxt runs before os.close(prev), so the handler closes nxt (or nothing, once "
                   "an absent component left fd None), never prev")),
    ("tools/orch_register.py", "_dirs_created", "TRY", "prev", "dir_fd", 1,
     _CS_P1.format("prev, dir_fd = dir_fd, nxt runs before os.close(prev), so the handler closes nxt, never "
                   "prev")),
    ("tools/orch_register.py", "_dirs_created", "AFTER", "dir_fd", "dir_fd", 1,
     "false positive: the unwind handler's wrapped close (its OSError suppressed so the in-flight refusal "
     "wins) sits in a handler whose own block ends in a raise, so the wrapped close after the try never "
     "runs after it"),
    ("tools/orch_register.py", "_made_dirs_swept", "REBIND", "prev", "", 2,
     _CS_P1.format("the sweep's every close is _swept_closed(prev, ...), and prev joins cur_fd and nxt_fd; "
                   "each of these two body closes (the descent handoff and the stopped-descent release) "
                   "runs only after the statement that took its number OUT of the slots (cur_fd, cur_on, "
                   "nxt_fd, nxt_on = nxt_fd, nxt_on, None, None, or prev, nxt_fd = nxt_fd, None), so no "
                   "slot holds the closed number even when the close raises any exception class, and each "
                   "later re-binding of prev takes a different, still-open number out of a slot")),
    ("tools/orch_register.py", "_made_dirs_swept", "TRY", "prev", "prev", 4,
     _CS_P1.format("the three body closes (descent handoff, stopped-descent release, climb handoff) each "
                   "close a number already re-bound out of both slots, and the finally closes only what a "
                   "slot still holds, clearing that slot first (prev, nxt_fd = nxt_fd, None in the nested "
                   "try, prev, cur_fd = cur_fd, None in its finally), so the finally never reaches a number "
                   "a body close closed, and its nxt_fd close (the fourth line) and cur_fd close are two "
                   "slots, the second attempted even when the first raises")),
    ("tools/check_release_cut.py", "working_blob", "TRY", "parent", "directory", 1,
     _CS_P1.format("parent, directory = directory, child runs before the close of parent, so the finally closes "
                   "child, never parent")),
    ("tools/_adapter_compose.py", "_target_dir_fd", "TRY", "parent", "directory", 1,
     _CS_P1.format("parent, directory = directory, child runs before the close of parent, so the finally (guarded "
                   "by handed, which a successful hand-over sets) closes child, never parent")),
    ("tools/pin.py", "do_recover", "TRY", "fd", "root_fd", 4,
     _CS_P1.format("each fd, root_fd = root_fd, None runs before os.close(fd), so the handler's `if root_fd is "
                   "not None` guard never reaches the closed number")),
    ("tools/pin.py", "do_recover", "AFTER", "fd", "root_fd", 4,
     _CS_P1.format("each fd, root_fd = root_fd, None; os.close(fd) is followed by a return in its block, so the "
                   "close of root_fd after the try never runs after it")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "AFTER", "jr_fd", "jr_fd", 3,
     _CS_FRESH.format("self-test legs", "leg's journal-root open, or its jr_fd = None ahead of one")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "AFTER", "root_fd", "root_fd", 10,
     _CS_FRESH.format("self-test legs", "leg's _open_dir_nofollow or _open_product_root")),
    ("opf/tools/_opf_adopt_apply.py", "_self_test_checks", "AFTER", "jfd", "jfd", 1,
     _CS_FRESH.format("self-test legs", "leg's journal-root open")),
    ("opf/tools/_opf_emit.py", "run_bounded", "AFTER", "rfd", "rfd", 1,
     "false positive: the start-failure handler's nested finally closes rfd and wfd, and the rest of that handler "
     "ends in a raise or a return on every path, so the closes after the try never run after it"),
    ("opf/tools/_opf_emit.py", "run_bounded", "AFTER", "wfd", "wfd", 1,
     "false positive: as the rfd row; the handler's own block ends in a return, so the later wfd close never runs "
     "after it"),
    ("opf/tools/_opf_init_operation.py", "_health_check", "AFTER", "pfd", "pfd", 1,
     _CS_FRESH.format("steps", "_open_parent")),
    ("opf/tools/_opf_init_operation.py", "_physical_tests", "AFTER", "rfd", "rfd", 1,
     _CS_FRESH.format("self-test legs", "os.open")),
    ("opf/tools/_opf_check.py", "self_test", "AFTER", "sfd", "sfd", 1,
     _CS_FRESH.format("self-test legs", "leg's os.open of a staged-ids fixture root")),
    ("tools/gen_skill.py", "self_test_main", "REBIND", "race_fd", "", 1,
     _CS_LEGS.format("_open_root of the race fixture")),
    ("tools/gen_skill.py", "self_test_main", "AFTER", "race_fd", "race_fd", 1,
     _CS_FRESH.format("self-test legs", "leg's _open_root of the race fixture")),
    ("opf/tools/_journal.py", "_fake_close", "REBIND", "fd", "", 1,
     "false positive: the harness's fault stub publishes the number it released to its reuser thread "
     "(state[\"fd\"] = fd); the re-binding is that store into state, whose one element key rule 3 joins to fd, "
     "and no try closes fd again"),
    ("tools/_close_selftest.py", "_fake_close", "REBIND", "fd", "", 1,
     "false positive: the harness copy's fault stub, byte-identical to _journal's"),
    ("opf/tools/_opf_oplock.py", "handler", "REBIND", "wfd", "", 3,
     _CS_JOINED.format("the child branch's record[\"wfd\"] = wfd and the parent's record[\"events\"].append(...)",
                       "join wfd to the wait status (and report and hung)", "record", "status binding",
                       "wait status")),
    ("opf/tools/_opf_oplock.py", "_forked_transfer", "REBIND", "wfd", "", 1,
     _CS_JOINED.format("the child branch's seen[\"wfd\"] = wfd", "joins wfd to the parent's later stores",
                       "seen", "seen[\"status\"] and seen[\"report\"]", "wait status and a report string")),
    ("opf/tools/_opf_oplock.py", "_st_f8_4_body", "REBIND", "[]seen", "", 1,
     _CS_JOINED.format("report = seen[\"report\"]", "joins report to the closed seen[\"fd\"]", "seen",
                       "report bindings", "report string")),
) + tuple(("opf/tools/_opf_init_substrate.py", name, "REBIND", "sub", "", 1,
           "false positive: a self-test step; sub is re-bound to a fresh substrate, the old one never closed again")
          for name in ("_t_s1_sibling_home", "_t_s2_capability_gate", "_t_s13_midread_containment",
                       "_t_s16_staging_sweep", "_t_s20_distinct_nested_homes"))

# Synthetic shapes the sweep must find (or, for closefd=False, must not): each is (label, source, expected
# shapes). The ownership transfers (the P1 ownership-first idiom) are required hits, deliberately conservative:
# rule 2 keeps a moved source in its alias class, so the sweep reports them.
_CLOSE_SWEEP_SHAPES = (
    ("tuple loop alias (begin_operation)", "def f(a, b):\n    try:\n        os.close(a)\n        a = None\n"
     "    except BaseException:\n        for x in (a, b):\n            if x is not None:\n                os.close(x)\n"
     "        raise\n", ["REBIND", "TRY"]),
    ("ownership transfer", "def f(a):\n    try:\n        fd, a = a, None\n        os.close(fd)\n    finally:\n"
     "        if a is not None:\n            os.close(a)\n", ["TRY"]),            # deliberately conservative
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
     "            os.close(a)\n", ["TRY"]),            # deliberately conservative
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
     "    finally:\n        if a is not None:\n            os.close(a)\n", ["TRY", "TRY"]),
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
    ("with exit in a try body, then a move, finally closes the source", "def f(a):\n    try:\n"
     "        with os.fdopen(a) as fh:\n            fh.read()\n        fd, a = a, None\n    finally:\n"
     "        os.close(a)\n", ["REBIND", "TRY"]),
    ("close result assigned, then a move", "def f(a):\n    try:\n        r = os.close(a)\n"
     "        fd, a = a, None\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("close as an if test, then a move", "def f(a):\n    try:\n        if not _close_fd(a):\n            pass\n"
     "        fd, a = a, None\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("fdopen object closed, then a move", "def f(a):\n    try:\n        fh = os.fdopen(a)\n        fh.close()\n"
     "        fd, a = a, None\n    finally:\n        os.close(a)\n", ["REBIND", "TRY"]),
    ("move in an if branch, its new alias closed after the join", "def f(a, b, c):\n    if c:\n"
     "        old, a = a, b\n    try:\n        os.close(b)\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("dict display key iterated by keys()", "def f(a):\n    fds = {a: None}\n    try:\n        os.close(a)\n"
     "    finally:\n        for fd in fds.keys():\n            os.close(fd)\n", ["TRY"]),
    ("dict display key iterated by items()", "def f(a):\n    fds = {a: 1}\n    try:\n        os.close(a)\n"
     "    finally:\n        for fd, _ in fds.items():\n            os.close(fd)\n", ["TRY"]),
    ("with exit over an alias of a wrapper", "def f(a):\n    fh = os.fdopen(a)\n    try:\n"
     "        with fh as g:\n            g.read()\n    finally:\n        os.close(a)\n", ["TRY"]),
    ("chained assignment from a call", "def f(p):\n    a = b = os.open(p, 0)\n    try:\n        os.close(a)\n"
     "    finally:\n        os.close(b)\n", ["TRY"]),
    ("unpacking a set display", "def f(fd):\n    a, = {fd}\n    try:\n        os.close(a)\n    finally:\n"
     "        os.close(fd)\n", ["TRY"]),
    ("unpacking a dict display", "def f(fd):\n    a, = {fd: None}\n    try:\n        os.close(a)\n    finally:\n"
     "        os.close(fd)\n", ["TRY"]),
    ("unpacking into a starred target", "def f(fd, x):\n    *a, b = fd, x\n    try:\n        for y in a:\n"
     "            os.close(y)\n    finally:\n        os.close(fd)\n", ["TRY"]),
    ("unpacking with a length mismatch", "def f(fd, x):\n    fds = [fd, x]\n    a, b, c = *fds, None\n    try:\n"
     "        os.close(a)\n    finally:\n        os.close(fd)\n", ["TRY"]),
    ("unpacking from a call", "def f(fd):\n    fds = [fd]\n    a, = list(fds)\n    try:\n        os.close(a)\n"
     "    finally:\n        os.close(fd)\n", ["TRY"]),
    ("unpacking from a name", "def f(fd):\n    pair = fd, None\n    a, b = pair\n    try:\n        os.close(a)\n"
     "    finally:\n        os.close(fd)\n", ["TRY"]),
    ("unpacking into a nested target", "def f(fd, x):\n    (a, b), c = (fd, x), None\n    try:\n"
     "        os.close(a)\n    finally:\n        os.close(fd)\n", ["TRY"]),
    ("alias closed, its attribute re-bound", "def f(self):\n    fd = self.pidfd\n    if fd is not None:\n"
     "        os.close(fd)\n        self.pidfd = None\n", ["REBIND"]),
    ("attribute cleared before its alias is closed", "def f(self):\n    fd, self.pidfd = self.pidfd, None\n"
     "    if fd is not None:\n        os.close(fd)\n", []),            # the P1 ownership-first idiom
    ("handler closed, closed after the try", "def f(a):\n    try:\n        x = 1\n    except OSError:\n"
     "        os.close(a)\n    os.close(a)\n", ["AFTER"]),
    ("finally closed, closed after the try", "def f(a):\n    try:\n        x = 1\n    finally:\n"
     "        os.close(a)\n    os.close(a)\n", ["AFTER"]),
    ("else closed, closed after the try", "def f(a):\n    try:\n        x = 1\n    except OSError:\n"
     "        raise\n    else:\n        os.close(a)\n    os.close(a)\n", ["AFTER"]),
    ("re-raising handler closed, closed after the try", "def f(a):\n    try:\n        x = 1\n"
     "    except OSError:\n        if a:\n            os.close(a)\n        raise\n    os.close(a)\n", []),
    ("finally closed in a returning nested handler, closed after the try", "def f(a):\n    try:\n        x = 1\n"
     "    finally:\n        try:\n            y = 2\n        except OSError:\n            os.close(a)\n"
     "            return\n    os.close(a)\n", []),
)
_CS_UNPACK = ("unpacking a set display", "unpacking a dict display", "unpacking into a starred target",
              "unpacking with a length mismatch", "unpacking from a call", "unpacking from a name")
_CS_AFTER_ENDED = ("handler closed, closed after the try", "finally closed, closed after the try",
                   "else closed, closed after the try")
_CS_LOOP_ELSE = ("for else after a swallowed close", "while else after a swallowed close", "for else re-binds",
                 "while else re-binds")

# A synthetic corpus with one function per compound statement kind, closes and re-bindings in every block,
# and one per simple statement kind the others do not already hold (a return, an assignment, an expression and
# pass appear in them; break and continue sit inside a loop): the sweep must run over each, and over every
# tools/ and opf/tools/ source, without raising.
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
    ("nested async function", "def f(fd):\n    async def inner():\n        os.close(fd)\n        fd2 = fd\n"
     "    try:\n        inner()\n    finally:\n        os.close(fd)\n"),
    ("global", "def f(fd):\n    global G\n    G = fd\n    os.close(G)\n"),
    ("nonlocal", "def f(fd):\n    def g():\n        nonlocal fd\n        os.close(fd)\n        fd = None\n"
     "    g()\n"),
    ("del", "def f(fd, fds):\n    os.close(fd)\n    del fd, fds[0]\n"),
    ("assert", "def f(fd):\n    assert fd >= 0, fd\n    os.close(fd)\n"),
    ("import", "def f(fd):\n    import os\n    from os import close as c\n    c(fd)\n    os.close(fd)\n"),
    ("raise", "def f(fd):\n    try:\n        os.close(fd)\n    except OSError as exc:\n"
     "        raise ValueError(fd) from exc\n    raise\n"),
    ("annotation only", "def f(self, fd, fds):\n    x: int\n    self.y: int\n    fds[0]: int\n    os.close(fd)\n"),
    ("augmented assignment", "def f(self, fd, fds):\n    os.close(fd)\n    fd += 1\n    self.fd -= 1\n"
     "    fds[0] |= fd\n"),
    ("break", "def f(fds):\n    for fd in fds:\n        os.close(fd)\n        break\n    while fds:\n"
     "        break\n"),
    ("continue", "def f(fds):\n    for fd in fds:\n        if fd:\n            continue\n        os.close(fd)\n"
     "    while fds:\n        continue\n"),
) + ((("type alias", "def f(fd):\n    type Fds = list[int]\n    os.close(fd)\n"),)
     if sys.version_info >= (3, 12) else ())        # a type alias statement parses from Python 3.12 on

# Each fix to the sweep's alias and pairing rules, put back by the self-test under --red-on-revert: (name, the
# fixed text, its pre-fix text, the shapes that must then be the only ones red). A leg may instead disable the
# rule that catches a required shape (a close counted inside an assignment's value or an if test, the rule-6
# binding of a wrapper to a key, the chained-assignment join), so that shape must then go missing.
_CLOSE_SWEEP_REVERTS = (
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
    ("sweep-try-star-after", "        if _cs_is_try(stmt):\n            swallowing",
     "        if isinstance(stmt, ast.Try):\n            swallowing",
     ("try-star swallowed close, closed later",)),
    ("sweep-tuple-elements", "for t, v in zip(target.elts, parts):   # rule 2", "for t, v in ():  #",
     ("ownership transfer", "tuple assignment keeping its source", "fan-out tuple from a cleared source",
      "transfer from a source with an earlier alias", "ownership-first idiom from an earlier alias",
      "move re-binds its source to another descriptor", "container display in a tuple assignment",
      "move in one branch of an if, closed after the join",
      "move in an if branch, its new alias closed after the join", "unpacking into a nested target")),
    ("sweep-wrapped-key", "or not _cs_key(node.args[0]):", "or not isinstance(node.args[0], ast.Name):",
     ("fdopen of an attribute", "fdopen of a container element")),
    ("sweep-wrapper-aliases", "return find(k) in {find(c) for c in closed}", "return k in closed",
     ("fdopen of a container element", "with exit over an alias of the closed descriptor",
      "fdopen object of an alias closed")),
    ("sweep-with-rebind", "if isinstance(stmt, (ast.With, ast.AsyncWith)):", "if False:",
     ("with exit, then re-bound", "with exit in a try body, then a move, finally closes the source")),
    ("sweep-dict-keys", "for e in node.keys + node.values if _cs_key(e)]", "for e in node.values if _cs_key(e)]",
     ("dict display key iterated by keys()", "dict display key iterated by items()", "unpacking a dict display")),
    ("sweep-loop-target-tuple", 'for target in [node.target] + list(getattr(node.target, "elts", [])):',
     "for target in [node.target]:", ("dict display key iterated by items()",)),
    ("sweep-with-object-exit", "return k if k and find(k) in {find(a) for a, b in wraps if closing(b)} else None",
     "return None", ("with exit over an alias of a wrapper",)),
    ("sweep-close-in-assignment", "        if _cs_close_arg(node):\n            found.append",
     "        if _cs_close_arg(node) and not any(node in ast.walk(a.value) for a in _cs_walk(nodes)\n"
     "                                           if isinstance(a, ast.Assign)):\n            found.append",
     ("close result assigned, then a move",)),
    ("sweep-close-in-if-test", "        if _cs_close_arg(node):\n            found.append",
     "        if _cs_close_arg(node) and not any(node in ast.walk(a.test) for a in _cs_walk(nodes)\n"
     "                                           if isinstance(a, ast.If)):\n            found.append",
     ("close as an if test, then a move",)),
    ("sweep-wrapper-binding", "wraps.append((target, _cs_key(_cs_wrapped(value))))", "pass",
     ("fdopen object of an alias closed", "fdopen object closed, then a move", "with exit over an alias of a wrapper")),
    ("sweep-chained-targets", "union(chained[0], other)          # rule 1", "pass  #",
     ("chained assignment from a call",)),
    ("sweep-unpack-fallback", "for key in leaves(target):                # anything else",
     "for key in ():  #", _CS_UNPACK),
    ("sweep-unpack-nested", "                    assign(t, v)\n", "                    _cs_key(t) and assign(t, v)\n",
     ("unpacking into a nested target",)),
    ("sweep-unpack-starred-exact", "and not any(isinstance(e, ast.Starred) for e in target.elts + parts):",
     "and True:", ("unpacking into a starred target",)),
    ("sweep-unpack-starred-target", 'return ["[]" + k.lstrip("[]") for k in leaves(target.value)]', "return []",
     ("unpacking into a starred target",)),
    ("sweep-unpack-starred-value", "return contents(node) + [k for e in starred for k in contents(e)]",
     "return contents(node)", ("unpacking with a length mismatch",)),
    ("sweep-unpack-non-display", "return contents(node) + [k for e in starred for k in contents(e)]",
     "return elements(node) + [k for e in starred for k in contents(e)]",
     ("unpacking from a call", "unpacking from a name")),
    ("sweep-set-elements", "if isinstance(node, (ast.Tuple, ast.List, ast.Set)):",
     "if isinstance(node, (ast.Tuple, ast.List)):", ("unpacking a set display",)),
    ("sweep-rebind-aliases", "if any(k and same(k, key) for k in keys):", "if any(k == key for k in keys):",
     ("alias closed, its attribute re-bound", "fdopen object closed, then a move")),
    ("sweep-after-ended", 'rows += [(rel, c.lineno, fn.name, "AFTER", k, o) for c, k in ended',
     'rows += [(rel, c.lineno, fn.name, "AFTER", k, o) for c, k in ()', _CS_AFTER_ENDED),
    ("sweep-after-handler", "for b in [h.body for h in stmt.handlers] + [stmt.orelse, stmt.finalbody]",
     "for b in [stmt.orelse, stmt.finalbody]", ("handler closed, closed after the try",)),
    ("sweep-after-else", "for b in [h.body for h in stmt.handlers] + [stmt.orelse, stmt.finalbody]",
     "for b in [h.body for h in stmt.handlers] + [stmt.finalbody]", ("else closed, closed after the try",)),
    ("sweep-after-finally", "for b in [h.body for h in stmt.handlers] + [stmt.orelse, stmt.finalbody]",
     "for b in [h.body for h in stmt.handlers] + [stmt.orelse]", ("finally closed, closed after the try",)),
    ("sweep-after-falls-off", "if not nodes or isinstance(nodes[-1], (ast.Raise, ast.Return)):", "if not nodes:",
     ("re-raising handler closed, closed after the try",
      "finally closed in a returning nested handler, closed after the try")),
)


def _cs_robustness_failures(sources):
    """Each (label, text) source the sweep raises on, named with the innermost statements that raise when each
    is swept on its own (as a function body, and as a class when it is one). Any exception is a failure."""
    import ast
    import _optlevel
    import types
    failures = []
    for label, text in sources:
        try:
            _close_sweep_source(label, text)
            continue
        except Exception as exc:                      # a crash is a defect, never a pass or a skip
            error = exc
        try:
            tree = _optlevel.parse(text, label)
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


# A census read error injected through a tool's own self-test: a held /dev/null descriptor whose fstat fails
# EIO when the descriptor census (_st_fd_table) reads it, and only when the census runs under the function
# named by the second argument ("*": any census). The real self-test runs as __main__; the held number is
# printed first so the report can be matched to it.
CENSUS_DRIVER = """
import errno, os, runpy, sys
path, target = sys.argv[1], sys.argv[2]
held = os.open(os.devnull, os.O_RDONLY)
real_fstat = os.fstat
def failing(number, *args, **kwargs):
    frame = sys._getframe(1)
    if number == held and frame.f_code.co_name == "_st_fd_table":
        while frame is not None and target not in ("*", frame.f_code.co_name):
            frame = frame.f_back
        if frame is not None:
            raise OSError(errno.EIO, "injected census read failure")
    return real_fstat(number, *args, **kwargs)
print("census-held " + str(held), file=sys.stderr, flush=True)
os.fstat = failing
sys.argv = [path] + sys.argv[3:]
sys.path.insert(0, os.path.dirname(path))
runpy.run_path(path, run_name="__main__")
"""

# Each census-mapping row carries its own kill bound in whole seconds: the slower of two runs of that entry
# measured under CENSUS_DRIVER, times a safety factor of 4, rounded up to a multiple of 10 s, and never under a
# floor of 30 s (the floor keeps interpreter start-up on a loaded host from mattering to the short entries).
# Measured on 2026-11-08 from 19:08 to 19:12 UTC, one entry at a time, two runs each, on 80 CPUs at a 1-minute
# load of 20 to 23: check_footer 4.02 s and 3.83 s; check_overclaim 0.52 s and 0.49 s; check_gensrc_failclose
# 104.82 s and 107.42 s (its census read is in its last vectors, so the run does almost all its work first);
# gen_crosswalk 0.48 s and 0.41 s; import_cwe 0.18 s and 0.17 s; _journal under any census 0.09 s and 0.08 s;
# _journal under _st_fd_table_eio 0.35 s and 0.36 s. So check_gensrc_failclose gets 4 * 107.42 = 429.68, rounded
# up to 430 s, and every other entry gets the 30 s floor (4 * 4.02 = 16.08 s at most). The factor allows a run
# four times slower than its slower measured run, about a host four times as loaded.
# The bounds must also fit inside the whole run: tools/selftest_git_fixture_env.py kills
# `check_release_cut.py --self-test --red-on-revert` at 2700 s (its CONFIG_MEMBER_BOUNDS entry), and a run
# killed there never reaches the named failure below. A killed entry spends its bound, then at most
# _CENSUS_KILL_WAIT s draining its pipes and at most _CENSUS_KILL_WAIT s polling its group empty, so an entry
# costs at worst its bound plus 2 * _CENSUS_KILL_WAIT s. _census_table_failures refuses, before anything runs,
# an empty table, a row bound above _CENSUS_BOUND_CAP, and a table whose bounds plus 2 * _CENSUS_KILL_WAIT s
# per entry sum above _CENSUS_TOTAL_CAP, so a table edit cannot silently break this arithmetic: any table that
# runs spends at most _CENSUS_TOTAL_CAP = 720 s on its entries, post-kill waits included. This table's bounds
# sum to 430 + 6 * 30 = 610 s, 610 + 7 * 2 * 5 = 680 s with the post-kill waits.
# The census rows spend their own drains and waits on top of the table, each _census_run they make bounded at
# 2 * TIMEOUT = 60 s plus the two post-kill waits (70 s at worst): with every one of those bounds spent in
# full, the bound rows cost at worst 70 + 20 + 5 = 95 s (their 60 s and 10 s entries plus the grandchild
# check), the interrupt and unreaped rows 75 s each, the exit-code row 70 s, the reaped-exit row 75 s, the
# drain-reaped row 130 s (its wrapper waits up to 60 s for the leader's exit before the drain runs), the
# construction row 135 s (drain, exit-or-record wait, grandchild check), the sigchld row and the two kernel
# sigchld rows about 0 s (nothing is launched), the two exit-outlived rows 25 s each (their 10 s bound,
# both post-kill waits and the grandchild check), the two exit-at-bound rows and the three full-pipe
# exit-at-bound rows 40 s each (their TIMEOUT s pause, which spans their 3 s bound, and both post-kill
# waits) and the record publication row 40 s (its TIMEOUT s bound and both post-kill waits): about 945 s
# together. The revert flips, run only under --red-on-revert, cost at worst 60 s (interrupt), 70 s (exit
# code), 75 s (reaped exit), 130 s (drain reaped), 135 s (construction), 70 s (sigchld), 60 s and 70 s (the
# two kernel sigchld flips, which launch), 25 s each (the two exit-outlived flips), 44 s each (the two
# exit-at-bound flips: their pause and post-kill waits plus the 4 s their copy sleeps before setting its
# deadline), 42 s (the full-pipe flip: the same plus the 2 s its copy sleeps) and 40 s (the record
# publication flip): about 890 s together.
# Outside the census, the fixture cases and the mutation replays run their children through observe, each
# call under subprocess.run's TIMEOUT = 30 s bound: the 242 fixture cases of test_cases() make 243 observe
# calls (one each, and push-protected's push-bare call), at most 243 * 30 = 7290 s, and the 29 mutation
# replays, run only under --red-on-revert, make one observe call each, at most 29 * 30 = 870 s. A call that
# reaches its bound raises and ends the run, so each call can spend at most just under its bound.
# Not summed here, each with no bound summed above: the fixture builds' git calls (each under its own
# TIMEOUT s bound, several per case, so their bounds would add thousands of seconds), observe's own git
# reads, the close checks, entry-restore and the close-sweep reverts (in process). They are taken as
# MEASURED: a plain --self-test run took 283.5 s on 2026-11-08 at a 1-minute load of 25 to 33, before the
# kernel sigchld, exit-outlived, exit-at-bound and record publication rows were added, the seven entries
# about 113 s of it (their second measured runs above), so the rest (the fixture cases, close checks and
# entry-restore, the observe calls' own time included, so counted twice here) about 171 s; and one
# --self-test --red-on-revert run on 2026-11-09 from 12:06:49 to 12:12:56 UTC, on 80 CPUs at a 1-minute load
# of 23.9 at its start and 29.6 at its end, took 367.0 s wall and passed, its replays (the close-sweep
# reverts and the mutation replays) 12.1 s of it (from its entry-restore line to its last line).
# The worst case over those terms: a --self-test run ends by at most 680 (table) + 945 (census rows) + 7290
# (fixture observe calls) = 8915 s (BOUND) plus the parts not summed (about 171 s, MEASURED), about 9086 s;
# a --red-on-revert run by at most 8915 + 890 (flips) + 870 (mutation replays) = 10675 s (BOUND) plus the
# parts not summed (about 171 + 12.1 = 183.1 s, MEASURED), about 10858.1 s. Both exceed the 1200 s default
# member bound, as do the census bounds alone (680 + 945 + 890 = 2515 s), so the member carries its own
# 2700 s entry in tools/selftest_git_fixture_env.py. That entry is BELOW the worst case ON PURPOSE, a
# deliberate shorter deadline under the kill-timeout rule: the 30 s observe bounds summed are a ceiling no
# run approaches, and a bound near it would let a hung run hold the config lane for over three hours. It is
# sized from measured runs with a stated margin: about 7.4 times the slowest (367.0 s, --red-on-revert, the
# entry's own comment records the runs), and it still covers every census bound above spent in full plus
# the measured rest (2515 + 183.1 = 2698.1 s). A run that reaches it is killed at its bound and reported as
# that lane's deadline (cannot evaluate, attributed to the bound), never as a failure of this self-test.
_CENSUS_BOUND_CAP = 600
_CENSUS_TOTAL_CAP = 720
# The most a killed entry waits after its group kill, once for its pipes and once more for its group to empty.
_CENSUS_KILL_WAIT = 5
# The most _census_run reads from one pipe on the last pass after the leader's exit before it takes a pipe
# still not at its end of file as held by a descendant: 16 MiB, 16 times the default fs.pipe-max-size of
# 1 MiB (the most an unprivileged process can raise its pipe to unless that limit is raised), so a pipe an
# exited leader left full is read to its end of file unless its size was raised above 16 MiB.
_CENSUS_LAST_READ_CAP = 16 * 1024 * 1024

# Every self-test that maps a descriptor census error (_StCensusError) to cannot-evaluate, each with the census
# caller that reaches its clause: (path, census caller, self-test arguments, kill bound in seconds).
_CENSUS_MAPPINGS = (
    ("tools/check_footer.py", "*", ("--self-test",), 30),
    ("tools/check_overclaim.py", "*", ("--self-test",), 30),
    ("tools/check_gensrc_failclose.py", "*", ("--self-test",), 430),
    ("tools/gen_crosswalk.py", "*", ("--self-test",), 30),
    ("tools/import_cwe.py", "*", ("--self-test",), 30),
    ("opf/tools/_journal.py", "*", ("--self-test",), 30),                     # the close-vector clause
    ("opf/tools/_journal.py", "_st_fd_table_eio", ("--self-test",), 30),      # the descriptor-helper clause
)


def _census_table_failures(mappings):
    """The refusals of a census-mapping table, read before any entry runs. An empty table is refused: a table
    that maps nothing checks nothing. Each row's bound must be a plain int (its type exactly int, so a bool, a
    float such as inf or nan, None or a str is refused), above 0 s and at most _CENSUS_BOUND_CAP s; when every
    row passes, the bounds plus 2 * _CENSUS_KILL_WAIT s for every entry (the most a killed entry spends after
    its kill) must together be at most _CENSUS_TOTAL_CAP s, which bounds what the entries spend in their drain
    and post-kill waits; the fork and exec of each entry's leader before its wait begins, and each wait's poll
    overshoot, lie outside that arithmetic and are not bounded by it. A refused bound of -1000000000 or
    above is shown as itself (-1000000000 included); every bound below -1000000000 (-1000000001 as much as
    one whose conversion to text would raise) is shown as below -1000000000. Returns [] when the table may
    run."""
    failures = []
    if not mappings:
        failures.append("census-mapping table: it is empty")
    for rel, target, _, bound in mappings:
        label = "%s (census under %s)" % (rel, target)
        if type(bound) is not int:
            failures.append("%s: its bound is a %s, not a plain int number of seconds" % (label, type(bound).__name__))
        elif not bound > 0:
            shown = str(bound) if bound >= -10 ** 9 else "below -1000000000"
            failures.append("%s: its bound %s s does not exceed 0 s" % (label, shown))
        elif bound > _CENSUS_BOUND_CAP:
            failures.append("%s: its bound exceeds the %d s cap" % (label, _CENSUS_BOUND_CAP))
    if not failures:
        total = sum(row[3] for row in mappings)
        spend = 2 * _CENSUS_KILL_WAIT * len(mappings)
        if total + spend > _CENSUS_TOTAL_CAP:
            failures.append("census-mapping table: its bounds sum to %d s and its %d entries can each spend "
                            "2 * %d s more after a kill, %d s together, above the %d s total cap"
                            % (total, len(mappings), _CENSUS_KILL_WAIT, total + spend, _CENSUS_TOTAL_CAP))
    return [failure + "; nothing run" for failure in failures]


def _census_run(args, bound):
    """Run CENSUS_DRIVER in this interpreter on args (the callee path, the census caller and the self-test
    arguments) as the leader of a new session, and so of its own process group, with stdin from /dev/null,
    stdout and stderr captured, and this process's environment less CENSUS_PAUSE_GATE (a self-test gate,
    never passed on from outside). Before anything is launched, the SIGCHLD disposition of this process is
    read twice: signal.getsignal (Python's handler table) and the kernel's own process-wide SigIgn mask in
    /proc/self/status, which also sees a SIG_IGN installed outside the signal module (through libc, or
    inherited across exec). When either read reports SIG_IGN, the run fails closed as (None, b"", note)
    with no leader constructed at all, because whenever SIGCHLD is ignored the kernel can reap an exited
    leader at any point (between the drain's waitid and a kill included), unpinning a group id that a kill
    could then reach once reused; when the kernel mask cannot be read (no /proc, or no readable SigIgn
    line), the run also fails closed with no launch, since the disposition is then unknown. Refusing before
    the launch means no group kill here can follow a kernel reap from a disposition already set when the
    leader is constructed; it does not rule one out for the reapers the reads cannot see. Those are:
    SA_NOCLDWAIT set without SIG_IGN (neither read reports that flag), a SIG_IGN set after the reads, and
    another reaper in this same process (a thread or a signal handler calling os.wait*); any of them can
    still unpin the group id. Against an unpinned id the drain fails closed twice below, each time it would
    otherwise trust the leader's identity: its os.waitid's ChildProcessError return, for a reap it observes,
    and the final reap's, for a reap landing between the last waitid and that reap; a reap it never observes
    (one landing after the drain's last waitid, before the finally's kill, on a path that kills) stays open
    under those undetected reapers. Barring them, the
    leader stays this process's unreaped child until the kill decision is
    made: the drain below reads both pipes itself (a selector over the two of them) and watches for the
    leader's exit with os.waitid and WNOWAIT, which detects the exit without reaping, so until this function
    reaps it an exited leader stays a zombie, its exit code stays readable, and its group id cannot be freed
    for reuse. When the leader has exited and both pipes have hit end of file, a state the drain reads at
    the top of each pass, before that pass reads the deadline (set to bound seconds after the leader's
    construction and the mask restore below, so the bound covers only the drain; the fork and exec before it
    lie outside it), the leader is reaped with os.waitpid, and (exit code, stderr bytes, "") returned with
    the code taken from that reap itself, even when that state is first seen on the pass where the deadline
    has already run out or on the one last pass the drain makes once it has: that pass runs the same
    os.waitid again and, when the leader has exited, reads each pipe still registered without blocking
    until its end of file, until it is empty, or until it has given more than _CENSUS_LAST_READ_CAP bytes
    on that pass, all before the group kill is decided; nothing is killed on that path (a descendant that
    had released the output pipes can outlive the run, and one that left the group is never reached). Whenever something other than this function
    reaps the leader first (the kernel, with SIGCHLD ignored at that point despite the checks above, so
    SA_NOCLDWAIT or a SIG_IGN set after them, or another reaper in this same process), whichever of the
    drain's os.waitid or that final os.waitpid runs next raises ChildProcessError: that run fails closed as
    (None, b"", note) with no kill, because a group id no longer pinned by an unreaped leader can be freed
    and reused, so signalling it could reach someone else's processes, and because the reaped leader's exit
    status is gone, so no exit code is returned in its place (a missing status is never reported as 0).
    On every other path out of the drain (the bound spent, or an exception such as
    an interrupt raised inside it) the leader is, barring the undetected reapers above, an unreaped
    child, so the group id still names this group:
    the cleanup sends the whole group SIGKILL and the leader is reaped only after that (after a bound-spent
    kill, by communicate's wait below, or by the Popen exit's wait only when communicate times out on a
    pipe still held; on an exception, by the Popen exit as the exception propagates; on a KeyboardInterrupt
    that exit waits for it only up to 0.25 s and leaves a leader not yet gone by then to the subprocess
    module's later cleanup, which reaps it once). That kill is a best effort with two residuals this function does not close: a raising handler for
    a second pending signal (a second interrupt among them) can fire at the kill call itself and skip it, and
    a raising handler for a signal other than SIGINT can fire between the leader's construction and the
    drain, before the cleanup is armed; either leaves the group running with no kill. Only after a bound-spent
    kill (an exception propagates past all of this, draining nothing) the pipes are put back to blocking and
    drained for up to _CENSUS_KILL_WAIT s (in case a process that left the group still holds them), the group
    is polled with signal 0 every 0.05 s until no member is left, for up to _CENSUS_KILL_WAIT s (an orphaned
    member is reaped by whatever adopted it), and it returns (None, b"", note), note naming what was still
    there, or "". The bound can also run out after the leader has exited, while a descendant still holds a
    pipe (the last pass found a pipe empty but not at its end of file, or still not at it after more than
    _CENSUS_LAST_READ_CAP bytes): the drain observed that exit with WNOWAIT by that last pass, so the leader is still an unreaped zombie
    when the group is killed, and the kill cannot change its status. It is reaped with os.waitpid only after
    the kill, and after the same pipe drain and group poll the run returns (exit code, stderr bytes, note),
    the code the leader's own, the stderr everything read including that drain, and the note opening with
    "a descendant still held its output pipes" (then what was still there), so a caller reports the callee's
    own outcome and the descendant. What this guarantees: a leader whose exit the last pass's os.waitid
    sees is never reported as a bound kill, and the descendant note is added only when a pipe was still
    open on that pass: empty with its write end still held, or still giving data after more than
    _CENSUS_LAST_READ_CAP bytes (so a pipe an exited leader left holding more than that, possible only
    where its size was raised above that cap, also gets the note with no descendant). One window stays
    open, a disclosed residual: a leader that exits after the last pass's os.waitid and before the group
    kill is reported as killed at its bound, (None, b"", note). The kill cannot change that leader's
    status, and the reap after the kill reads it (communicate's wait, or the Popen exit's wait only when
    communicate times out on a pipe still held), but this function returns no code for it; a status of -9
    could not be told from the group kill's SIGKILL in any case. Should the post-kill os.waitpid find the leader already reaped (an undetected
    reaper), its status is gone and the run returns (None, b"", note) with the note opening with "cannot
    evaluate:". SIGINT is blocked in this thread from before the leader's construction until the drain is
    entered, so a KeyboardInterrupt from SIGINT cannot land between the fork and the cleanup that kills the
    group. The leader inherits that mask through fork and exec, so it and every descendant run with SIGINT
    blocked unless something they run unblocks it: the callee self-tests this function runs therefore run
    with SIGINT blocked, and a callee self-test that tested SIGINT propagation would see it blocked. That
    consequence is stated here rather than undone in the
    child, since the census children run in their own session, never as a terminal's foreground group, and
    are ended by SIGKILL, which no mask blocks."""
    ignored = signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN
    if not ignored:
        # signal.getsignal reads Python's handler table, not the kernel disposition, so a SIG_IGN
        # installed outside the signal module is invisible to it: read the kernel's process-wide
        # SigIgn mask too, and fail closed where it cannot be read.
        try:
            with open("/proc/self/status", "r", encoding="ascii") as status:
                for line in status:
                    if line.startswith("SigIgn:"):
                        ignored = bool(int(line.split(":", 1)[1], 16) >> (signal.SIGCHLD - 1) & 1)
                        break
                else:
                    raise ValueError("no SigIgn line")
        except (OSError, ValueError) as exc:
            return None, b"", ("cannot evaluate: the kernel SIGCHLD disposition could not be read from "
                               "/proc/self/status (%r), so a kernel reap of an exited leader cannot be "
                               "ruled out; no leader was launched" % (exc,))
    if ignored:
        return None, b"", ("cannot evaluate: SIGCHLD is set to be ignored in this process, so the kernel "
                           "could reap an exited leader at any point and a group kill could reach a reused "
                           "group id; no leader was launched")
    notes = []
    ended = False
    exited = False
    code = None
    err = bytearray()
    # CENSUS_PAUSE_GATE is the paused self-test run's gate (only _census_paused_run sets it, in the child's
    # environment alone): a value inherited from outside is never passed on, so no stand-in reads it
    env = {key: value for key, value in os.environ.items() if key != "CENSUS_PAUSE_GATE"}
    unblock = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        proc = subprocess.Popen([sys.executable, "-I", "-B", "-c", CENSUS_DRIVER, *args], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=env)
    except BaseException:
        signal.pthread_sigmask(signal.SIG_SETMASK, unblock)
        raise
    with proc:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, unblock)   # a pending SIGINT now raises inside this try
            deadline = time.monotonic() + bound
            with selectors.DefaultSelector() as pipes:
                for pipe in (proc.stdout, proc.stderr):
                    os.set_blocking(pipe.fileno(), False)
                    pipes.register(pipe, selectors.EVENT_READ)
                last = False                      # the bound is spent: one more pass, then the kill
                while True:
                    if not exited:
                        try:
                            exited = os.waitid(os.P_PID, proc.pid,
                                               os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
                        except ChildProcessError:
                            # something other than this drain reaped the leader (the kernel,
                            # with SIGCHLD ignored, or another reaper in this process), so its
                            # exit cannot be read and the group id is no longer pinned: fail
                            # closed with no kill, which could reach a reused id
                            ended = True
                            return None, b"", ("cannot evaluate: the leader was reaped out from "
                                               "under the drain (by the kernel, with SIGCHLD "
                                               "ignored, or by another reaper in this process), "
                                               "so its exit could not be read and no group kill "
                                               "was sent")
                    if exited and last:
                        # The leader has exited and the bound is spent: read each pipe still registered
                        # without blocking, until its end of file or until it is empty, so a pipe whose
                        # end of file was not read yet is not taken for one a descendant still holds.
                        # The byte cap bounds this pass when something keeps writing: a pipe that gave
                        # more than _CENSUS_LAST_READ_CAP bytes here without its end of file stays
                        # registered, so one holding at most that much is always read to its end.
                        for key in list(pipes.get_map().values()):
                            taken = 0
                            while taken <= _CENSUS_LAST_READ_CAP:
                                try:
                                    chunk = os.read(key.fd, 65536)
                                except BlockingIOError:   # empty but still open: something holds it
                                    break
                                if not chunk:
                                    pipes.unregister(key.fileobj)
                                    break
                                taken += len(chunk)
                                if key.fileobj is proc.stderr:
                                    err += chunk
                    if exited and not pipes.get_map():
                        ended = True              # from here the kill stays off: never a kill after the reap
                        try:
                            status = os.waitpid(proc.pid, 0)[1]   # seen with WNOWAIT, a zombie until here
                        except ChildProcessError:
                            # the leader was reaped between the WNOWAIT observation and this reap,
                            # so the observed status is gone: fail closed, never a made-up code
                            return None, b"", ("cannot evaluate: the leader was reaped out from under "
                                               "the drain between the exit observation and the reap, "
                                               "so its exit could not be read and no group kill was "
                                               "sent")
                        code = os.waitstatus_to_exitcode(status)
                        proc.returncode = code    # the Popen exit must not wait again for a reaped child
                        return code, bytes(err), ""
                    if last:
                        break
                    left = deadline - time.monotonic()
                    if left <= 0:
                        # one more pass of the checks above before the kill: the leader can have
                        # exited since this pass's waitid, or its pipes' end of file not been read yet
                        last = True
                        continue
                    for key, _ in pipes.select(min(left, 0.05)):
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:   # a wake with nothing left to read
                            continue
                        if not chunk:
                            pipes.unregister(key.fileobj)
                        elif key.fileobj is proc.stderr:
                            err += chunk
        finally:
            if not ended:                                 # every other path out of the drain: the leader is
                try:                                      # an unreaped child here (an observed reap returned
                    os.killpg(proc.pid, signal.SIGKILL)   # above; the docstring's undetected reapers stay
                except ProcessLookupError:                # open), so the group id still names this group,
                    pass                                  # exited leader or not
        if exited:                                        # the bound ran out after the leader's own exit
            # The exit was observed with WNOWAIT before the kill, so the leader is still an unreaped
            # zombie whose status the kill cannot change: reap it now (after the kill, so the group id
            # stayed pinned for it) and keep its own exit code.
            try:
                code = os.waitstatus_to_exitcode(os.waitpid(proc.pid, 0)[1])
            except ChildProcessError:
                notes.append("cannot evaluate: the leader had exited on its own but was reaped out from "
                             "under the drain before its exit status could be read")
            else:
                proc.returncode = code    # the Popen exit must not wait again for a reaped child
        for pipe in (proc.stdout, proc.stderr):
            os.set_blocking(pipe.fileno(), True)          # communicate must not see EAGAIN from the drain's
        try:                                              # non-blocking setting on a spurious selector wake
            err += proc.communicate(timeout=_CENSUS_KILL_WAIT)[1] or b""
        except subprocess.TimeoutExpired:
            notes.append("its output pipes were still held %d s after the group kill" % _CENSUS_KILL_WAIT)
    deadline = time.monotonic() + _CENSUS_KILL_WAIT
    while True:
        try:
            os.killpg(proc.pid, 0)
        except (ProcessLookupError, PermissionError):
            break
        if time.monotonic() >= deadline:
            notes.append("its process group %d still had a member %d s after the kill"
                         % (proc.pid, _CENSUS_KILL_WAIT))
            break
        time.sleep(0.05)
    if code is not None:
        notes.insert(0, "a descendant still held its output pipes when the bound ran out, after the callee "
                        "had exited, so its whole process group was killed")
        return code, bytes(err), "; ".join(notes)
    return None, b"", "; ".join(notes)


def _census_mapping_check(root, mappings=_CENSUS_MAPPINGS):
    """Each self-test in mappings, run under CENSUS_DRIVER, must end exit 2 with its cannot-evaluate
    report naming the held descriptor ("SELF-TEST ERROR: descriptor census cannot evaluate descriptor N:"),
    never a traceback (exit 1) or a pass. The table is read first: when _census_table_failures refuses the table
    (an empty one, any row bound, or the bound total with its post-kill waits), those refusals are the failures
    and no entry runs at all. Otherwise each entry
    runs in its own session under its row's explicit bound in seconds (_census_run); one that outlives it has
    its whole process group killed and awaited, and is a failure naming the entry and the bound; one refused
    before any launch (SIGCHLD set to be ignored in this process) or whose leader was reaped out from under
    the drain is a failure naming that cause; one whose callee exited on its own while a descendant still
    held its output pipes at the bound is a failure naming the callee's own exit code, whether its report
    was there, and the descendant. Returns
    (failures, runs), runs counting the entries handed to _census_run (a pre-launch refusal counts its
    entry too)."""
    failures = _census_table_failures(mappings)
    if failures:
        return failures, 0
    for rel, target, argv, bound in mappings:
        label = "%s (census under %s)" % (rel, target)
        code, err, note = _census_run([str(root / rel), target, *argv], bound)
        if code is None:
            if note.startswith("cannot evaluate:"):
                failures.append("%s: %s; an unevaluable run is a failure, not a pass" % (label, note))
            else:
                failures.append("%s: killed at its %d s bound before it ended, with its whole process group%s; a killed "
                                "run is a failure, not a pass" % (label, bound, " (" + note + ")" if note else ""))
            continue
        err = err.decode("utf-8", "replace")
        held = re.match(r"census-held (\d+)\n", err, re.ASCII)
        want = held and ("SELF-TEST ERROR: descriptor census cannot evaluate descriptor " + held.group(1)
                         + ": OSError(5, 'injected census read failure')")
        if note:
            # the callee ended on its own but left a descendant past the bound: report its own outcome
            # and that descendant, never a bound kill of a callee that had already ended
            reported = bool(want) and want in err and "Traceback" not in err
            failures.append("%s: ended on its own with exit %d %s its cannot-evaluate report, but %s; a run "
                            "that leaves a descendant past its %d s bound is a failure, not a pass"
                            % (label, code, "with" if reported else "without", note, bound))
            continue
        if code != 2 or not want or want not in err or "Traceback" in err:
            failures.append("%s: expected exit 2 with its cannot-evaluate report, got exit %d: %s"
                            % (label, code, err.strip().splitlines()[-1:]))
    return failures, len(mappings)


# A stand-in callee for the census-mapping bound rows: it sleeps argv[1] seconds, then makes its census read and
# reports the injected error the way a real self-test does. Given argv[2], it first writes there its own pid and
# process group, or, given argv[3] as well, those of a grandchild it starts that inherits (and so holds) the
# output pipes and sleeps argv[4] seconds, 120 when absent (bounded either way, so a failed group kill leaves
# nothing running for long; a row that finds the recorded pid still in the recorded group kills it itself,
# and never signals a pid that left it). The record is written whole to argv[2] + ".partial", in the same
# directory, and only then moved onto argv[2] with os.replace, so a row that sees the record (some poll
# for it while the stand-in runs) always reads both numbers, never a file created but not yet written.
# Given argv[5] as well, it exits with that code after its sleep, with no census read and no report. With
# CENSUS_PAUSE_GATE set (only _census_paused_run sets it; _census_run strips a value inherited from
# outside), it first blocks on one read of that descriptor and closes it, so the run decides when it may
# go on and its census read never sees that descriptor.
_CENSUS_STANDIN = """import os, subprocess, sys, time
def _st_fd_table():
    for number in range(3, 256):
        try:
            os.fstat(number)
        except OSError as exc:
            if exc.errno != 9:
                return number, exc
    return None, None
gate = os.environ.get("CENSUS_PAUSE_GATE")
if gate:
    os.read(int(gate), 1)
    os.close(int(gate))
if len(sys.argv) > 2:
    pid = os.getpid()
    if len(sys.argv) > 3:
        pid = subprocess.Popen([sys.executable, "-I", "-c",
                                "import time; time.sleep(" + (sys.argv[4] if len(sys.argv) > 4 else "120")
                                + ")"]).pid
    partial = sys.argv[2] + ".partial"
    with open(partial, "w", encoding="ascii") as record:
        record.write("%d %d" % (pid, os.getpgid(0)))
    os.replace(partial, sys.argv[2])
time.sleep(float(sys.argv[1]))
if len(sys.argv) > 5:
    sys.exit(int(sys.argv[5]))
number, exc = _st_fd_table()
if exc is None:
    sys.exit(0)
print("SELF-TEST ERROR: descriptor census cannot evaluate descriptor %d: %r" % (number, exc), file=sys.stderr)
sys.exit(2)
"""


def _census_bound_rows(parent):
    """The census-mapping bound rows, on the stand-in callee. Run: a 60 s bound on a run that takes about 0.1 s
    on an idle host passes (that slack, some 600 times the idle run, keeps the row from failing on a loaded
    host); a 10 s bound on a run that starts a grandchild and then sleeps 60 s is a failure naming the entry and
    the bound, and the grandchild must be gone when the check returns (10 s leaves the stand-in time to start it
    and record its pid on a loaded host; the recorded pid is signalled only while it is still in the recorded
    group). Refused before any launch, each a named failure with no run: an empty table; a bound of 0, -1,
    -10 ** 9 + 1, -10 ** 9 or -10 ** 9 - 1 (not above 0 s; the first two shown as themselves, -1000000000
    included, the last as below -1000000000) or -10 ** 5000 (shown the same summarised way); True, 1.5, inf,
    nan, None or "60" (not a plain int); 601, 10 ** 30 or 10 ** 5000 (above the cap); a table of one good and
    one refused row (the good row must not run either); and two good rows whose sum plus the post-kill waits
    tops the total cap by 1 s. The table check alone takes each end: 1, 2, the cap - 1 and the cap pass, as do
    two rows whose sum plus those waits comes to the total cap - 1 and to the total cap exactly, and the real
    table must pass too. Returns (failures, rows checked)."""
    standin = parent / "census-standin"
    standin.mkdir()
    (standin / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    rel = "census-standin/standin.py"
    label = rel + " (census under *)"
    ran, grandchild = standin / "ran", standin / "grandchild"

    def row(bound, *argv):
        return (rel, "*", argv or ("0",), bound)

    zero = label + ": its bound %s s does not exceed 0 s; nothing run"
    not_int = label + ": its bound is a %s, not a plain int number of seconds; nothing run"
    capped = label + ": its bound exceeds the %d s cap; nothing run" % _CENSUS_BOUND_CAP
    over = _CENSUS_TOTAL_CAP - 4 * _CENSUS_KILL_WAIT - _CENSUS_BOUND_CAP + 1
    cases = (
        ("pass", (row(60),), [], 1),
        ("killed", (row(10, "60", str(grandchild), "spawn"),),
         [label + ": killed at its 10 s bound before it ended, with its whole process group; a killed run is a "
          "failure, not a pass"], 1),
        ("empty", (), ["census-mapping table: it is empty; nothing run"], 0),
        ("zero", (row(0),), [zero % 0], 0),
        ("negative", (row(-1),), [zero % -1], 0),
        ("negative-edge-inside", (row(-10 ** 9 + 1),), [zero % (-10 ** 9 + 1)], 0),
        ("negative-edge-at", (row(-10 ** 9),), [zero % (-10 ** 9)], 0),
        ("negative-edge-outside", (row(-10 ** 9 - 1),), [zero % "below -1000000000"], 0),
        ("huge-negative", (row(-10 ** 5000),), [zero % "below -1000000000"], 0),
        ("bool", (row(True),), [not_int % "bool"], 0),
        ("float", (row(1.5),), [not_int % "float"], 0),
        ("inf", (row(float("inf")),), [not_int % "float"], 0),
        ("nan", (row(float("nan")),), [not_int % "float"], 0),
        ("none", (row(None),), [not_int % "NoneType"], 0),
        ("str", (row("60"),), [not_int % "str"], 0),
        ("cap-plus-one", (row(_CENSUS_BOUND_CAP + 1),), [capped], 0),
        ("huge", (row(10 ** 30),), [capped], 0),
        ("huger", (row(10 ** 5000),), [capped], 0),
        ("one-refused", (row(60, "0", str(ran)), row(0)), [zero % 0], 0),
        ("total-plus-one", (row(_CENSUS_BOUND_CAP), row(over)),
         ["census-mapping table: its bounds sum to %d s and its 2 entries can each spend 2 * %d s more after "
          "a kill, %d s together, above the %d s total cap; nothing run"
          % (_CENSUS_BOUND_CAP + over, _CENSUS_KILL_WAIT, _CENSUS_BOUND_CAP + over + 4 * _CENSUS_KILL_WAIT,
             _CENSUS_TOTAL_CAP)], 0),
    )
    failures = []
    for name, table, want, want_runs in cases:
        got, runs = _census_mapping_check(parent, table)
        if (got, runs) != (want, want_runs):
            failures.append("census bound row %s: expected %r with %d runs, got %r with %d runs"
                            % (name, want, want_runs, got, runs))
    try:
        pid, group = (int(field) for field in grandchild.read_text(encoding="ascii").split())
    except (OSError, ValueError) as exc:
        failures.append("census bound row killed: the stand-in recorded no grandchild pid: %r" % exc)
    else:
        try:                                # a pid that left the recorded group was reused; never signal it
            if os.getpgid(pid) == group:
                failures.append("census bound row killed: its grandchild %d still ran after the check returned"
                                % pid)
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if ran.exists():
        failures.append("census bound row one-refused: its good row ran although the table was refused")
    ends = ((1,), (2,), (_CENSUS_BOUND_CAP - 1,), (_CENSUS_BOUND_CAP,),
            (_CENSUS_BOUND_CAP, over - 2), (_CENSUS_BOUND_CAP, over - 1))
    for bounds in ends:
        got = _census_table_failures(tuple(row(bound) for bound in bounds))
        if got:
            failures.append("census bound table %r: expected no refusal, got %r" % (bounds, got))
    got = _census_table_failures(_CENSUS_MAPPINGS)
    if got:
        failures.append("census-mapping table: the real table is refused: %r" % got)
    return failures, len(cases) + len(ends) + 1


def _census_grandchild_gone(record, label):
    """The grandchild record a stand-in wrote at `record`, read and enforced: the recorded pid must leave the
    recorded group within _CENSUS_KILL_WAIT s. An unreadable record is a failure naming it; a survivor is a
    failure, killed here once it is confirmed still in the recorded group (a pid that left that group was
    reused and is never signalled), so no grandchild outlives the caller. Returns the failures."""
    failures = []
    try:
        pid, group = (int(field) for field in record.read_text(encoding="ascii").split())
    except (OSError, ValueError) as exc:
        return ["%s: the stand-in recorded no grandchild pid: %r" % (label, exc)]
    deadline = time.monotonic() + _CENSUS_KILL_WAIT
    while True:
        try:
            alive = os.getpgid(pid) == group
        except (ProcessLookupError, PermissionError):
            alive = False
        if not alive or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if alive:
        failures.append("%s: its grandchild %d survived the interrupted wait" % (label, pid))
        try:                                # a pid that left the recorded group was reused; never signal it
            if os.getpgid(pid) == group:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return failures


_CENSUS_RECORD_PLACEHOLDER = "placeholder"


def _census_record_atomic_row(parent):
    """The stand-in's record publication, checked through the file it replaces: the record path is first
    created holding a placeholder and hard-linked to a witness name, then the stand-in (no grandchild, a 0 s
    sleep) runs under _census_run with a TIMEOUT s bound. A record moved into place with os.replace leaves
    the old file, and so the witness, untouched; a record written in place (opened on the record path
    itself, which truncates the file before the numbers are in it) writes through the witness too. The row
    requires a run that ended (no note), the witness still holding the placeholder, the record a different
    file holding exactly the stand-in's own pid and group (its own group's leader, so the two are equal), and
    no file left beside them. It fails the in-place write only; a write that removes the record and then
    creates it again is just as partial to a reader in between but leaves the witness alone, and no row here
    tells it apart. Returns the failures."""
    label = "census record publication row"
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    record, witness = parent / "grandchild", parent / "witness"
    record.write_text(_CENSUS_RECORD_PLACEHOLDER, encoding="ascii")
    try:
        os.link(record, witness)
    except OSError as exc:
        return ["%s: the witness link could not be made, so nothing was checked: %r" % (label, exc)]
    code, err, note = _census_run([str(parent / "standin.py"), "*", "0", str(record)], TIMEOUT)
    if note:
        return ["%s: the stand-in run did not end cleanly: %r (exit %r)" % (label, note, code)]
    failures = []
    try:
        held = witness.read_text(encoding="ascii")
        same = os.stat(record).st_ino == os.stat(witness).st_ino
        fields = record.read_text(encoding="ascii").split()
    except OSError as exc:
        return ["%s: the record or its witness could not be read: %r" % (label, exc)]
    if held != _CENSUS_RECORD_PLACEHOLDER or same:
        failures.append("%s: the stand-in wrote its record in place (the witness of the old record now reads "
                        "%r), so a row polling for the record can read it created but not yet written"
                        % (label, held))
    if len(fields) != 2 or not all(field.isdigit() for field in fields) or fields[0] != fields[1]:
        failures.append("%s: expected the record to hold the stand-in's own pid and group, got %r"
                        % (label, fields))
    left = sorted(entry.name for entry in parent.iterdir())
    if left != ["grandchild", "standin.py", "witness"]:
        failures.append("%s: expected only the stand-in, its record and the witness, found %r" % (label, left))
    return failures


def _census_interrupt_row(parent):
    """The interrupted wait against _census_run: the stand-in leader starts a grandchild that inherits and so
    holds the output pipes for up to 120 s, records that grandchild's pid and group, sleeps 1 s and exits; the
    exit's SIGCHLD raises KeyboardInterrupt in this thread inside the drain wait, before anything has reaped
    the exited leader. The cleanup must kill the group on that path, while the leader is still its unreaped
    zombie, so the grandchild must be gone once the interrupt reaches here; a survivor is a failure, killed
    here once it is confirmed still in the recorded group. SIGCHLD is blocked across the handler install and
    across its restore (and unblocked in between, and afterwards as it was): signal.signal first runs a
    pending handler, so an unblocked restore could raise before restoring and leave the raising handler
    installed. The block holds off only a signal delivered after it; a handler already pending when the
    restore begins runs inside the blocking call itself (pthread_sigmask runs pending handlers too), so the
    restore sits in a finally of that call and the mask reset in a finally of the restore, and both still
    happen when that handler raises. Returns the failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    record = parent / "grandchild"

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    failures = []
    interrupted = False
    unblock = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
    previous = signal.signal(signal.SIGCHLD, interrupt)
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, unblock)     # the handler goes live only inside this try
        _census_run([str(parent / "standin.py"), "*", "1", str(record), "spawn"], 2 * TIMEOUT)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})   # an already pending handler runs here
        finally:
            try:
                # nothing delivered after the block runs here; a None previous handler was installed
                # from C and cannot be reinstalled from Python, so the default disposition stands in
                signal.signal(signal.SIGCHLD, signal.SIG_DFL if previous is None else previous)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, unblock)
    if not interrupted:
        failures.append("census interrupt row: the wait ended with no interrupt; nothing was reproduced")
    return failures + _census_grandchild_gone(record, "census interrupt row")


def _census_interrupt_unreaped_row(parent):
    """The interrupt row again, watching the kill itself: os.killpg is wrapped (and restored in a finally) so
    that when _census_run's cleanup kills the stand-in leader's group, the wrapper first asks os.waitid, with
    WNOWAIT so the wrapper itself reaps nothing, whether that leader is an exited, still-unreaped child of
    this process. On the interrupted wait the leader has already exited (its exit raised the interrupt), so
    the one group kill must find exactly that; no kill, a reaped leader (ChildProcessError) or a still-running
    one is a failure. SIGCHLD is blocked around the handler install and restore, each restore step in a
    finally of the one before it, as in _census_interrupt_row, and the real kill still goes out. The row then
    reads the grandchild record the stand-in wrote and requires that grandchild gone from the recorded group
    within _CENSUS_KILL_WAIT s, killing a survivor itself once it is confirmed still in that group, so
    nothing here outlives the row. Returns the failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    record = parent / "grandchild"
    seen = []
    real_killpg = os.killpg

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    def watching(pgid, sig):
        if sig == signal.SIGKILL:
            try:
                waited = os.waitid(os.P_PID, pgid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                seen.append("a reaped leader")
            else:
                seen.append("an exited, unreaped leader" if waited is not None else "a still-running leader")
        return real_killpg(pgid, sig)

    failures = []
    interrupted = False
    unblock = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
    previous = signal.signal(signal.SIGCHLD, interrupt)
    os.killpg = watching
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, unblock)     # the handler goes live only inside this try
        _census_run([str(parent / "standin.py"), "*", "1", str(record), "spawn"], 2 * TIMEOUT)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        os.killpg = real_killpg
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})   # an already pending handler runs here
        finally:
            try:
                # nothing delivered after the block runs here; a None previous handler was installed
                # from C and cannot be reinstalled from Python, so the default disposition stands in
                signal.signal(signal.SIGCHLD, signal.SIG_DFL if previous is None else previous)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, unblock)
    if not interrupted:
        failures.append("census interrupt unreaped row: the wait ended with no interrupt; nothing was "
                        "reproduced")
    if seen != ["an exited, unreaped leader"]:
        failures.append("census interrupt unreaped row: expected one group kill finding an exited, unreaped "
                        "leader, saw %r" % (seen,))
    return failures + _census_grandchild_gone(record, "census interrupt unreaped row")


def _census_exit_code_row(parent):
    """An ordinary census run's exit code, read through the unreaped leader: the stand-in ends exit 2 with
    its cannot-evaluate report, and _census_run must return exactly that code with no note. The drain watches
    the exit with WNOWAIT, so the leader is still this process's unreaped zombie when the final os.waitpid
    reads the code; a drain that reaps the leader first (a waitid without WNOWAIT) leaves that reap nothing,
    so the run comes back as the fail-closed reaped-leader note, which this row reports. The run is made
    with an outer CENSUS_PAUSE_GATE of 1000, a descriptor the stand-in does not hold (os.environ restored in
    a finally): _census_run must strip it from the child's environment, and a stand-in that read it would
    fail that read and exit 1 with a traceback, which this row reports. Returns the failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    outer = os.environ.get("CENSUS_PAUSE_GATE")
    os.environ["CENSUS_PAUSE_GATE"] = "1000"
    try:
        code, err, note = _census_run([str(parent / "standin.py"), "*", "0"], 2 * TIMEOUT)
    finally:
        if outer is None:
            os.environ.pop("CENSUS_PAUSE_GATE", None)
        else:
            os.environ["CENSUS_PAUSE_GATE"] = outer
    err = err.decode("utf-8", "replace")
    if code != 2 or note:
        return ["census exit code row: expected exit 2 with no note from the stand-in, got %r with note %r"
                % (code, note)]
    if "SELF-TEST ERROR: descriptor census cannot evaluate descriptor" not in err:
        return ["census exit code row: the stand-in run carried no cannot-evaluate report: %r"
                % err.strip()[-200:]]
    return []


def _census_reaped_exit_row(parent):
    """An exit status lost to an outside reap between the drain's WNOWAIT observation and its own reap: the
    stand-in exits 2 while a grandchild it started holds the output pipes open, so the drain observes the
    exit while the pipes are still held, and a thread then reaps the leader inside that window (released by
    the wrapped os.waitid as soon as it first observes the exit, and joined before the observation is handed
    back to the drain, so the reap always lands between the observation and the drain's reap). _census_run
    must fail closed with the reaped-leader cannot-evaluate note, never return a made-up exit 0 in place of
    the stand-in's 2, and the thread must have read that 2 itself (the status went somewhere, just not
    here). Returns the failures."""
    import threading
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    record = parent / "grandchild"
    real_popen = subprocess.Popen
    real_waitid = os.waitid
    leader = []
    reaped = []
    observed = threading.Event()

    def capturing(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        leader.append(proc.pid)
        return proc

    def reap():
        if observed.wait(2 * TIMEOUT) and leader:
            try:
                reaped.append(os.waitpid(leader[0], 0))
            except ChildProcessError as exc:
                reaped.append(exc)

    def observing(idtype, pid, flags):
        result = real_waitid(idtype, pid, flags)
        if result is not None and leader and pid == leader[0] and not observed.is_set():
            observed.set()
            thread.join(2 * TIMEOUT)
        return result

    thread = threading.Thread(target=reap)
    thread.start()
    failures = []
    subprocess.Popen, os.waitid = capturing, observing
    try:
        code, err, note = _census_run([str(parent / "standin.py"), "*", "0.5", str(record), "spawn", "3"],
                                      2 * TIMEOUT)
    finally:
        subprocess.Popen, os.waitid = real_popen, real_waitid
        observed.set()
        thread.join(2 * TIMEOUT)
    if (code, err) != (None, b"") or "reaped out from under the drain between the exit observation" not in note:
        failures.append("census reaped exit row: expected the fail-closed reaped-leader note, got %r with "
                        "note %r" % (code, note))
    if len(reaped) != 1 or not isinstance(reaped[0], tuple):
        failures.append("census reaped exit row: the racing reaper did not reap the leader: %r" % (reaped,))
    elif os.waitstatus_to_exitcode(reaped[0][1]) != 2:
        failures.append("census reaped exit row: the racing reaper read exit %r, not the stand-in's 2"
                        % (os.waitstatus_to_exitcode(reaped[0][1]),))
    return failures + _census_grandchild_gone(record, "census reaped exit row")


def _census_drain_reaped_row(parent):
    """The drain entered with the leader already reaped: subprocess.Popen is wrapped (and restored in a
    finally) so the wrapper reaps the leader itself as soon as it has exited, before the constructor
    returns. The drain's first os.waitid then raises ChildProcessError, and _census_run must fail closed
    with the reaped-leader cannot-evaluate note and send no kill at all (os.killpg is wrapped too, recording
    each call without passing it to the real os.killpg, so a disarmed copy under the revert flip sends
    nothing either; a kill against the unpinned group id would be the failure this guard exists to
    prevent). Returns the failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    real_popen = subprocess.Popen
    real_killpg = os.killpg
    kills = []

    def reaping(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        deadline = time.monotonic() + 2 * TIMEOUT
        while True:
            try:
                pid = os.waitpid(proc.pid, os.WNOHANG)[0]
            except ChildProcessError:
                return proc                       # already reaped elsewhere; the drain still sees ECHILD
            if pid == proc.pid or time.monotonic() >= deadline:
                return proc    # reaped, or not exited in time (the row then reports the missing refusal)
            time.sleep(0.05)

    def counting(pgid, sig):
        kills.append((pgid, sig))    # recorded only, never sent: the reaped leader's group id is unpinned

    failures = []
    subprocess.Popen, os.killpg = reaping, counting
    try:
        code, err, note = _census_run([str(parent / "standin.py"), "*", "0"], 2 * TIMEOUT)
    finally:
        subprocess.Popen, os.killpg = real_popen, real_killpg
    if (code, err) != (None, b"") or "reaped out from under the drain (by the kernel" not in note:
        failures.append("census drain reaped row: expected the in-drain fail-closed refusal, got %r with "
                        "note %r" % (code, note))
    if kills:
        failures.append("census drain reaped row: a kill was sent although the leader was already reaped: "
                        "%r" % (kills,))
    return failures


def _census_construction_interrupt_row(parent):
    """A SIGINT that lands during the leader's construction: subprocess.Popen is wrapped (and restored in a
    finally) so the wrapper records the leader's pid and sends this process its own SIGINT as soon as the
    real constructor returns. The drain blocks SIGINT across the construction, so that interrupt must stay
    pending until the mask restore inside the drain's try and raise there, where the cleanup kills the
    group; the kill lands before the stand-in interpreter is up, so the stand-in usually never writes its
    grandchild record. The row then waits until the recorded leader has exited (seen with os.waitid and
    WNOWAIT, reaping nothing; one already reaped by the Popen exit counts as exited) or the record exists,
    whichever comes first, for at most 2 * TIMEOUT s, never a fixed window a loaded host could outrun: the
    stand-in writes the record before it exits, so a leader that exited without writing it can never write
    it, while under a reverted SIGINT block (the interrupt then raises inside the wrapper, before the
    cleanup is armed, and nothing kills the group) the stand-in runs on and its record appears however
    slowly the host runs it. A record that exists must name a grandchild gone from the recorded group within
    _CENSUS_KILL_WAIT s (_census_grandchild_gone; a survivor is a failure, killed there once it is confirmed
    still in the recorded group). The row installs the default SIGINT handler for its own interrupt
    (restored in the same finally), so a host that started this suite with SIGINT ignored cannot turn the
    row into a 2 * TIMEOUT wait. When SIGINT is already blocked in this thread at row start, the row runs
    nothing and returns a named skip instead: its own SIGINT would stay pending past the row (the drain
    restores the caller's mask, which keeps it blocked, so nothing here would ever consume it) and would
    land on whatever unblocks SIGINT later. Returns (failures, outcome), outcome "ran" or the named skip."""
    if signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, set()):
        return [], "skipped: SIGINT was blocked in this thread at row start"
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    record = parent / "grandchild"
    real_popen = subprocess.Popen
    leader = []

    def arming(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        leader.append(proc.pid)
        os.kill(os.getpid(), signal.SIGINT)   # pending while SIGINT is blocked; raises at the drain's unmask
        return proc

    failures = []
    interrupted = False
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    subprocess.Popen = arming
    try:
        _census_run([str(parent / "standin.py"), "*", "1", str(record), "spawn"], 2 * TIMEOUT)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        subprocess.Popen = real_popen
        signal.signal(signal.SIGINT, signal.SIG_DFL if previous is None else previous)
    if not interrupted:
        failures.append("census construction interrupt row: the construction-time SIGINT raised no "
                        "interrupt; nothing was reproduced")
    if not leader:
        return failures + ["census construction interrupt row: no leader was constructed; nothing was "
                           "reproduced"], "ran"
    deadline = time.monotonic() + 2 * TIMEOUT
    exited = False
    while True:
        if record.exists():                   # a record can only appear when the group kill was skipped
            failures += _census_grandchild_gone(record, "census construction interrupt row")
            break
        if exited:
            break         # the stand-in writes the record before it exits, so it can never appear now
        try:
            exited = os.waitid(os.P_PID, leader[0], os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            exited = True                     # already reaped by the Popen exit: exited all the same
        if not exited and time.monotonic() >= deadline:
            failures.append("census construction interrupt row: the leader %d had neither exited nor "
                            "written its record %d s after the interrupt" % (leader[0], 2 * TIMEOUT))
            break
        if not exited:
            time.sleep(0.05)
    return failures, "ran"


def _census_sigchld_ignored_row(parent):
    """The pre-launch refusal of _census_run: with this process's SIGCHLD set to SIG_IGN (restored in a
    finally), the run must return the fail-closed cannot-evaluate note naming the ignored SIGCHLD and must
    construct no leader at all. subprocess.Popen is wrapped (and restored in a finally) to count
    constructions, so a launch is a failure even when the note comes back right: a refusal read only inside
    the drain would still have launched a leader whose group kill could follow a kernel reap. Returns the
    failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    real_popen = subprocess.Popen
    launched = []

    def counting(*args, **kwargs):
        launched.append(args)
        return real_popen(*args, **kwargs)

    failures = []
    previous = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    subprocess.Popen = counting
    try:
        code, err, note = _census_run([str(parent / "standin.py"), "*", "0"], 2 * TIMEOUT)
    finally:
        subprocess.Popen = real_popen
        # a None previous handler was installed from C and cannot be reinstalled from Python:
        # restore the default disposition rather than raise and leave SIG_IGN installed
        signal.signal(signal.SIGCHLD, signal.SIG_DFL if previous is None else previous)
    if launched:
        failures.append("census sigchld ignored row: a leader was launched although SIGCHLD was set to be "
                        "ignored (%d constructions)" % len(launched))
    if (code, err) != (None, b"") or not note.startswith("cannot evaluate: SIGCHLD is set to be ignored"):
        failures.append("census sigchld ignored row: expected the pre-launch fail-closed refusal, got %r "
                        "with note %r" % (code, note))
    return failures


def _census_kernel_sigchld_row(parent, mode):
    """The kernel half of the pre-launch SIGCHLD check, which signal.getsignal cannot stand in for. Mode
    "libc": SIGCHLD is set to SIG_IGN through libc's signal() (ctypes), outside the signal module, so
    signal.getsignal still reports the default disposition while the kernel ignores SIGCHLD (the row checks
    that getsignal read first; a non-default one reproduces nothing and is a failure); the run must return
    the "SIGCHLD is set to be ignored" refusal. Mode "unreadable": the kernel status read fails, a
    PermissionError injected for /proc/self/status by an open bound in the running _census_run's module
    globals (removed in a finally), and the run must return the "kernel SIGCHLD disposition could not be
    read" cannot-evaluate refusal. Either way subprocess.Popen is wrapped (and restored in a finally) to
    count constructions, and any launch is a failure. The libc disposition is put back in a finally to the
    one libc's signal() returned. Returns the failures."""
    import builtins
    import ctypes
    import errno
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    label = "census kernel sigchld %s row" % mode
    real_popen = subprocess.Popen
    real_open = builtins.open    # never the module-global open bound below, which would call itself
    launched = []

    def counting(*args, **kwargs):
        launched.append(args)
        return real_popen(*args, **kwargs)

    def failing(file, *args, **kwargs):
        if file == "/proc/self/status":
            raise PermissionError(errno.EACCES, "injected unreadable kernel status", file)
        return real_open(file, *args, **kwargs)

    run = _census_run
    scope = run.__globals__
    argv = [str(parent / "standin.py"), "*", "0"]
    if mode == "libc":
        try:
            libc_signal = ctypes.CDLL(None, use_errno=True).signal
        except (OSError, AttributeError) as exc:
            return ["%s: libc signal() could not be loaded: %r" % (label, exc)]
        libc_signal.restype = ctypes.c_void_p
        libc_signal.argtypes = (ctypes.c_int, ctypes.c_void_p)
        previous = libc_signal(signal.SIGCHLD, int(signal.SIG_IGN))
        if previous == ctypes.c_void_p(-1).value:
            return ["%s: libc signal() refused SIG_IGN (errno %d)" % (label, ctypes.get_errno())]
        subprocess.Popen = counting
        try:
            seen = signal.getsignal(signal.SIGCHLD)
            code, err, note = run(argv, 2 * TIMEOUT)
        finally:
            subprocess.Popen = real_popen
            libc_signal(signal.SIGCHLD, previous)
        want = "cannot evaluate: SIGCHLD is set to be ignored"
        if seen != signal.SIG_DFL:
            return ["%s: signal.getsignal read %r, not the default, under the libc SIG_IGN; nothing was "
                    "reproduced" % (label, seen)]
    else:
        if "open" in scope:
            return ["%s: the census module already binds a global open; nothing was reproduced" % label]
        scope["open"] = failing
        subprocess.Popen = counting
        try:
            code, err, note = run(argv, 2 * TIMEOUT)
        finally:
            subprocess.Popen = real_popen
            del scope["open"]
        want = "cannot evaluate: the kernel SIGCHLD disposition could not be read"
    failures = []
    if launched:
        failures.append("%s: a leader was launched although the kernel read should have refused it (%d "
                        "constructions)" % (label, len(launched)))
    if (code, err) != (None, b"") or not note.startswith(want):
        failures.append("%s: expected the pre-launch refusal %r, got %r with note %r" % (label, want, code, note))
    return failures


def _census_exit_outlived_row(parent, exit_code):
    """A callee that ends on its own while a descendant still holds its output pipes past the bound: the
    stand-in starts a grandchild that inherits the pipes and sleeps 120 s, records it, and exits at once,
    with exit 1 and no report (exit_code 1) or exit 2 with its cannot-evaluate report (exit_code 2). The
    mapping check runs it under a 10 s bound (time for the stand-in to start and exit on a loaded host) and
    must report exactly the callee's own exit code, whether its report was there, and the descendant, never
    a bound kill of a callee that had already ended. The grandchild must then be gone from the recorded
    group (_census_grandchild_gone; a survivor is a failure, killed there once it is confirmed still in
    that group). Returns the failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    record = parent / "grandchild"
    argv = ("0", str(record), "spawn", "120") + (() if exit_code == 2 else (str(exit_code),))
    label = "census exit %d outlived row" % exit_code
    want = ["standin.py (census under *): ended on its own with exit %d %s its cannot-evaluate report, but a "
            "descendant still held its output pipes when the bound ran out, after the callee had exited, so "
            "its whole process group was killed; a run that leaves a descendant past its 10 s bound is a "
            "failure, not a pass" % (exit_code, "with" if exit_code == 2 else "without")]
    got, runs = _census_mapping_check(parent, (("standin.py", "*", argv, 10),))
    failures = []
    if (got, runs) != (want, 1):
        failures.append("%s: expected %r with 1 run, got %r with %d runs" % (label, want, got, runs))
    return failures + _census_grandchild_gone(record, label)


def _census_exit_at_bound_row(parent, race):
    """A callee that ends on its own, with no descendant, while its bound runs out: the stand-in sleeps 1 s
    and exits 2 with its cannot-evaluate report, under a 3 s bound, through _census_paused_run: the drain's
    first os.waitid on the leader, which finds it still running, pauses once until the leader has exited
    (seen with WNOWAIT, reaping nothing) and at least the bound after that os.waitid was entered, so at or
    past the drain's deadline, or until its TIMEOUT check ends it (the order of those checks is stated
    there). Race "stale": the wrapper then hands back the result read before the pause (still running),
    the leader's exit landing between the drain's os.waitid and its deadline check. Race "unread": the
    wrapper hands back a fresh read (exited), the exit landing after the drain's last select, so both pipes
    are still registered with their end of file unread. Either way _census_run must return exit 2 with no
    note and the stand-in's report: never a bound kill, and never a descendant that does not exist. A pause
    that never saw the leader exit past the deadline reproduced nothing and is a failure. Returns the
    failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    label = "census exit at bound %s row" % race
    code, err, note, paused = _census_paused_run([str(parent / "standin.py"), "*", "1"], race == "stale")
    if paused != ["exited past the deadline"]:
        return ["%s: the pause did not reproduce the race (%r); nothing was reproduced" % (label, paused)]
    err = err.decode("utf-8", "replace")
    if code != 2 or note or "SELF-TEST ERROR: descriptor census cannot evaluate descriptor" not in err:
        return ["%s: expected exit 2 with no note and the stand-in's report, got %r with note %r and stderr "
                "%r" % (label, code, note, err.strip()[-200:])]
    return []


# The stand-in of _census_exit_at_bound_full_row: it raises its stdout pipe to 1 MiB (exit 3 with a line
# naming the refusal when that is refused or grants less than argv[1] bytes), writes argv[1] bytes to it,
# which then fit without a reader, prints a cannot-evaluate report on stderr and exits 2. It starts no
# descendant, so once it has exited nothing else holds either pipe. Under _census_paused_run it first waits
# at the run's gate, as _CENSUS_STANDIN does (an ordinary _census_run strips that variable).
_CENSUS_FULL_STANDIN = """import fcntl, os, sys
gate = os.environ.get("CENSUS_PAUSE_GATE")
if gate:
    os.read(int(gate), 1)
    os.close(int(gate))
size = int(sys.argv[1])
try:
    granted = fcntl.fcntl(1, getattr(fcntl, "F_SETPIPE_SZ", 1031), 1048576)
except OSError as exc:
    granted = exc
if type(granted) is not int or granted < size:
    print("pipe size not granted: %r" % (granted,), file=sys.stderr)
    sys.exit(3)
view = memoryview(b"x" * size)
while view:
    view = view[os.write(1, view):]
print("SELF-TEST ERROR: descriptor census cannot evaluate descriptor 3: stand-in report", file=sys.stderr)
sys.exit(2)
"""


def _census_exit_at_bound_full_row(parent, size, cap=None):
    """A callee that ends on its own, with no descendant, while its bound runs out, leaving `size` bytes in
    a stdout pipe it raised to 1 MiB (_CENSUS_FULL_STANDIN), under the same pause as the "unread" race of
    _census_exit_at_bound_row (_census_paused_run with a fresh read), so the last pass finds the leader
    exited and those bytes still ahead of the pipe's end of file. With cap given, _CENSUS_LAST_READ_CAP is
    set to it for the run in the module the _census_run in use reads it from (restored in a finally). A pipe holding at most the cap must be read to its end of
    file: _census_run must return exit 2, no note and the stand-in's report. A pipe holding more than the
    cap must be taken as held: exit 2, the stand-in's report and the note opening with "a descendant still
    held its output pipes". A pause that never saw the leader exit past the deadline reproduced nothing and
    is a failure. Returns (failures, outcome): outcome "ran", or the skip reason when the stand-in was
    refused the larger pipe (exit 3; the host's pipe limits, not the drain), with no failures."""
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_FULL_STANDIN, encoding="utf-8")
    label = "census exit at bound full row (%d bytes, cap %s)" % (size, "default" if cap is None else cap)
    scope = _census_run.__globals__    # the module the _census_run in use reads its cap from (a flip's copy)
    real_cap = scope["_CENSUS_LAST_READ_CAP"]
    if cap is not None:
        scope["_CENSUS_LAST_READ_CAP"] = cap
    try:
        code, err, note, paused = _census_paused_run([str(parent / "standin.py"), "*", str(size)], False)
    finally:
        scope["_CENSUS_LAST_READ_CAP"] = real_cap
    err = err.decode("utf-8", "replace")
    if code == 3 and "pipe size not granted" in err:
        return [], "the stand-in's stdout pipe could not be raised to 1 MiB: " + err.strip()[-200:]
    if paused != ["exited past the deadline"]:
        return (["%s: the pause did not reproduce the race (%r); nothing was reproduced" % (label, paused)],
                "ran")
    held = cap is not None and size > cap
    noted = note.startswith("a descendant still held its output pipes") if held else note == ""
    if code != 2 or not noted or "SELF-TEST ERROR: descriptor census cannot evaluate descriptor" not in err:
        return ["%s: expected exit 2 with %s and the stand-in's report, got %r with note %r and stderr %r"
                % (label, "the descendant note" if held else "no note", code, note, err.strip()[-200:])], "ran"
    return [], "ran"


def _census_paused_run(argv, stale):
    """_census_run on argv under a 3 s bound with os.waitid wrapped (and restored in a finally, with
    subprocess.Popen, wrapped to record the leader's pid) so that the drain's first os.waitid on the leader,
    which finds it still running, pauses once until the leader has exited (seen with WNOWAIT, reaping
    nothing) and at least the bound has passed since that os.waitid was entered (or a TIMEOUT check ends it).
    _census_run calls os.waitid only in its drain, after it has set its deadline to the bound past a
    time.monotonic() read, so the time read on entering that os.waitid is no earlier than that read, and a
    pause that ends at least the bound after it ends at or past the deadline, however long the parent was
    descheduled between the leader's construction and the deadline's initialization. With stale true the
    wrapper then hands back the result read before the pause (still running); otherwise a fresh read
    (exited). The leader is held at a gate this run controls, so it cannot exit before that first os.waitid
    has returned: the wrapped subprocess.Popen hands it the read end of a pipe (pass_fds, its number in
    CENSUS_PAUSE_GATE), both stand-ins block on one read of it before they do anything else, and the wrapper
    writes the byte that releases it only once that first os.waitid has returned. Both ends are closed in a
    finally, so a leader still at the gate reads its end of file and goes on. A leader found exited at the
    first os.waitid all the same reproduced nothing. Otherwise the pause reads its TIMEOUT s end just after
    the release byte is written, then polls about every 0.05 s, each poll in this order: first, when the
    bound has passed since the first os.waitid was entered and an os.waitid with WNOHANG and WNOWAIT sees
    the leader exited, it has reproduced the state and ends; only otherwise, once that end has been reached,
    it ends having reproduced nothing. A poll that runs late past that end (the parent descheduled) and
    finds both conditions of the first test still counts as reproduced.
    Returns (code, err, note, paused), paused ["exited past the deadline"] when the pause reproduced that
    state."""
    bound = 3
    real_popen = subprocess.Popen
    real_waitid = os.waitid
    leader = []
    paused = []
    gate_read, gate_write = os.pipe()

    def capturing(*args, **kwargs):
        kwargs["pass_fds"] = (gate_read,)
        kwargs["env"] = dict(os.environ, CENSUS_PAUSE_GATE=str(gate_read))
        proc = real_popen(*args, **kwargs)
        leader.append(proc.pid)
        return proc

    def pausing(idtype, pid, flags):
        # read before the call, so no earlier than the read the drain's deadline was set from: _census_run
        # calls os.waitid only in its drain, after setting that deadline
        entered = time.monotonic()
        result = real_waitid(idtype, pid, flags)
        if not leader or pid != leader[0] or paused:
            return result
        os.write(gate_write, b"\0")    # this first waitid has returned: only now may the leader go on to exit
        if result is not None:
            paused.append("the leader had already exited at the first waitid")
            return result
        end = time.monotonic() + TIMEOUT
        while True:
            # the deadline is at most entered + bound (a later read plus the same bound, rounded alike), so
            # once this holds the drain's next `deadline - time.monotonic()` is at most 0: the bound is spent
            past = time.monotonic() >= entered + bound
            if past and real_waitid(idtype, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                paused.append("exited past the deadline")
                break
            if time.monotonic() >= end:
                paused.append("the leader had not exited past the deadline %d s into the pause" % TIMEOUT)
                break
            time.sleep(0.05)
        return result if stale else real_waitid(idtype, pid, flags)

    try:
        subprocess.Popen, os.waitid = capturing, pausing
        try:
            code, err, note = _census_run(argv, bound)
        finally:
            subprocess.Popen, os.waitid = real_popen, real_waitid
    finally:
        os.close(gate_write)    # a leader still at the gate reads its end of file here and goes on
        os.close(gate_read)
    return code, err, note, paused


# The driver appended to the flipped _census_run copy: the interrupt row of _census_interrupt_row, run in a
# child interpreter, reporting on stdout whether the recorded grandchild survived the interrupted wait (a
# survivor is killed once it is confirmed still in the recorded group, so nothing outlives the report).
_CENSUS_FLIP_DRIVER = """

def _interrupt(signum, frame):
    raise KeyboardInterrupt

workdir = sys.argv[1]
record = os.path.join(workdir, "grandchild")
signal.signal(signal.SIGCHLD, _interrupt)
interrupted = False
try:
    _census_run([os.path.join(workdir, "standin.py"), "*", "1", record, "spawn"], 60)
except KeyboardInterrupt:
    interrupted = True
signal.signal(signal.SIGCHLD, signal.SIG_DFL)
if not interrupted:
    print("no-interrupt")
    sys.exit(1)
with open(record, "r", encoding="ascii") as handle:
    pid, group = (int(field) for field in handle.read().split())
deadline = time.monotonic() + _CENSUS_KILL_WAIT
alive = True
while True:
    try:
        alive = os.getpgid(pid) == group
    except (ProcessLookupError, PermissionError):
        alive = False
    if not alive or time.monotonic() >= deadline:
        break
    time.sleep(0.05)
if alive:
    try:
        if os.getpgid(pid) == group:
            os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    print("grandchild-survived")
else:
    print("grandchild-gone")
"""


def _census_interrupt_flip_red(parent, script):
    """The interrupt row against a copy of _census_run whose cleanup is put back to deciding the group kill
    only after the leader can be reaped: the guard that turns the kill off only on the normal return is
    replaced with one that first polls the leader (proc.poll() reaps an exited one) and kills only while it is
    unreaped, taken from this file's own text with only that guard flipped and run as a child interpreter on a
    scratch script. On the interrupted wait the leader has exited, the poll reaps it, the kill is skipped, and
    the recorded grandchild must survive (the scratch driver then kills it, once it is confirmed still in the
    recorded group). Returns the failures: [] when the flipped copy let the grandchild survive, a named
    failure when it did not reproduce the skipped kill."""
    source = script.read_text(encoding="utf-8")
    count = source.count("\ndef _census_run(")
    require(count != 0, "census interrupt flip: its function anchor is absent")
    require(count == 1, "census interrupt flip: its function anchor is repeated")
    begin = source.index("\ndef _census_run(")
    body = source[begin:source.index("\ndef ", begin + 1)]
    anchor = "if not ended:"
    count = body.count(anchor)
    require(count != 0, "census interrupt flip: its kill-condition anchor is absent")
    require(count == 1, "census interrupt flip: its kill-condition anchor is repeated")
    parent.mkdir()
    (parent / "standin.py").write_text(_CENSUS_STANDIN, encoding="utf-8")
    (parent / "flip.py").write_text(
        "import os, selectors, signal, subprocess, sys, time\n"
        + "CENSUS_DRIVER = " + repr(CENSUS_DRIVER) + "\n"
        + "_CENSUS_KILL_WAIT = " + repr(_CENSUS_KILL_WAIT) + "\n"
        + "_CENSUS_LAST_READ_CAP = " + repr(_CENSUS_LAST_READ_CAP) + "\n"
        + body.replace(anchor, "if not ended and proc.poll() is None:", 1)
        + _CENSUS_FLIP_DRIVER, encoding="utf-8")
    try:
        report = subprocess.run([sys.executable, "-I", "-B", str(parent / "flip.py"), str(parent)],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=2 * TIMEOUT)
    except subprocess.TimeoutExpired:
        return ["census interrupt flip: its child interpreter did not end within %d s" % (2 * TIMEOUT)]
    outcome = report.stdout.decode("utf-8", "replace").strip()
    if report.returncode != 0 or outcome != "grandchild-survived":
        return ["census interrupt flip: expected the flipped copy to leave the grandchild running, got exit "
                "%d with %r (stderr %r)" % (report.returncode, outcome,
                                            report.stderr.decode("utf-8", "replace").strip()[-200:])]
    return []


def _census_run_flipped(parent, script, name, old, new, also=()):
    """A copy of _census_run taken from this file's own text with only `old` replaced by `new`, and each
    further (old, new) pair in `also` replaced the same way, written to a scratch module and loaded through
    the import machinery (never registered in sys.modules), so a flip row runs the real drain's text with
    exactly one revert (and only the stated additions). Returns that copy's _census_run."""
    import importlib.util
    source = script.read_text(encoding="utf-8")
    count = source.count("\ndef _census_run(")
    require(count != 0, name + ": its function anchor is absent")
    require(count == 1, name + ": its function anchor is repeated")
    begin = source.index("\ndef _census_run(")
    body = source[begin:source.index("\ndef ", begin + 1)]
    for anchor, replacement in ((old, new), *also):
        count = body.count(anchor)
        require(count != 0, name + ": its revert anchor is absent")
        require(count == 1, name + ": its revert anchor is repeated")
        body = body.replace(anchor, replacement, 1)
    parent.mkdir()
    location = parent / "flipped.py"
    location.write_text(
        "import os, selectors, signal, subprocess, sys, time\n"
        + "CENSUS_DRIVER = " + repr(CENSUS_DRIVER) + "\n"
        + "_CENSUS_KILL_WAIT = " + repr(_CENSUS_KILL_WAIT) + "\n"
        + "_CENSUS_LAST_READ_CAP = " + repr(_CENSUS_LAST_READ_CAP) + "\n"
        + body, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("check_release_cut_" + name.replace(" ", "_"), location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._census_run


def _census_exit_code_flip_red(parent, script):
    """The exit-code row against a copy of _census_run whose drain reaps on sight: WNOWAIT dropped from the
    drain's os.waitid, this file's own text with only that flag removed. The waitid then reaps the exited
    leader, the final os.waitpid can no longer read its status and fails closed, and the real
    _census_exit_code_row, run with this module's _census_run swapped for the copy (restored in a finally),
    must report that fail-closed note in place of the stand-in's exit 2. Returns the failures: [] when the
    row turned red, a named failure when it did not."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(parent, script, "census exit code flip",
                                  "os.WEXITED | os.WNOHANG | os.WNOWAIT", "os.WEXITED | os.WNOHANG")
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_exit_code_row(parent / "row")
    finally:
        this._census_run = real
    if len(got) != 1 or "reaped out from under the drain between the exit observation" not in got[0]:
        return ["census exit code flip: expected the row to report the reaped leader as the fail-closed "
                "note, got %r" % (got,)]
    return []


def _census_construction_flip_red(parent, script):
    """The construction row against a copy of _census_run whose SIGINT block is put back to blocking
    nothing: the block's {signal.SIGINT} replaced by an empty set, this file's own text with only that set
    emptied. The construction-time SIGINT then raises inside the wrapped constructor, before the cleanup is
    armed, nothing kills the group, the stand-in runs on and records its grandchild, and the real row, run
    with this module's _census_run swapped for the copy (restored in a finally) and reading the record
    through the shared _census_grandchild_gone, must report that grandchild surviving (and kill it). When
    the row skips (SIGINT already blocked at row start) this flip returns that same outcome unexercised.
    Returns (failures, outcome): ([], "ran") when the row turned red, a named failure when it did not."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(parent, script, "census construction flip",
                                  "{signal.SIGINT})", "set())")
    real = this._census_run
    this._census_run = flipped
    try:
        got, outcome = _census_construction_interrupt_row(parent / "row")
    finally:
        this._census_run = real
    if outcome != "ran":
        return [], outcome
    if not any("survived the interrupted wait" in failure for failure in got):
        return ["census construction flip: expected the row to report its grandchild surviving the skipped "
                "group kill, got %r" % (got,)], outcome
    return [], outcome


def _census_record_atomic_flip_red(parent):
    """The record publication row against a stand-in whose record is put back to being written in place:
    the write to argv[2] + ".partial" and the os.replace onto argv[2] replaced by one open of argv[2] itself,
    this module's own stand-in text with only that write changed. The real _census_record_atomic_row, run
    with this module's _CENSUS_STANDIN swapped for that text (restored in a finally), must report the
    in-place write through its witness. Returns the failures: [] when the row turned red, a named failure
    when it did not."""
    this = sys.modules[__name__]
    old = ('    partial = sys.argv[2] + ".partial"\n'
           '    with open(partial, "w", encoding="ascii") as record:\n'
           '        record.write("%d %d" % (pid, os.getpgid(0)))\n'
           '    os.replace(partial, sys.argv[2])\n')
    new = ('    with open(sys.argv[2], "w", encoding="ascii") as record:\n'
           '        record.write("%d %d" % (pid, os.getpgid(0)))\n')
    real = this._CENSUS_STANDIN
    count = real.count(old)
    require(count != 0, "census record publication flip: its write anchor is absent")
    require(count == 1, "census record publication flip: its write anchor is repeated")
    parent.mkdir()
    this._CENSUS_STANDIN = real.replace(old, new, 1)
    try:
        got = _census_record_atomic_row(parent / "row")
    finally:
        this._CENSUS_STANDIN = real
    if not any("wrote its record in place" in failure for failure in got):
        return ["census record publication flip: expected the row to report the in-place write, got %r"
                % (got,)]
    return []


def _census_sigchld_flip_red(parent, script):
    """The SIGCHLD-ignored row against a copy of _census_run with only the pre-launch disposition check
    disarmed: its condition replaced by False, this file's own text otherwise untouched. With SIGCHLD set to
    SIG_IGN the copy launches its leader anyway, and the real _census_sigchld_ignored_row, run with this
    module's _census_run swapped for the copy (restored in a finally), must report that launch. Returns the
    failures: [] when the row turned red, a named failure when it did not."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(parent, script, "census sigchld flip",
                                  "if ignored:", "if False:")
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_sigchld_ignored_row(parent / "row")
    finally:
        this._census_run = real
    if not any("a leader was launched although SIGCHLD was set to be ignored" in failure for failure in got):
        return ["census sigchld flip: expected the row to report a launched leader, got %r" % (got,)]
    return []


def _census_reaped_exit_flip_red(parent, script):
    """The reaped-exit row against a copy of _census_run whose final reap is put back to the Popen wait: the
    drain's own os.waitpid replaced by proc.wait(), whose missing-child fallback reports 0 for a child
    someone else reaped. Under the row's racing reaper the copy then returns exit 0 with no note for a
    stand-in that exited 2, and the real _census_reaped_exit_row, run with this module's _census_run swapped
    for the copy (restored in a finally), must report that 0. Returns the failures: [] when the row turned
    red, a named failure when it did not."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(parent, script, "census reaped exit flip",
                                  "status = os.waitpid(proc.pid, 0)[1]",
                                  "status = proc.wait() << 8")
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_reaped_exit_row(parent / "row")
    finally:
        this._census_run = real
    if not any("got 0 with note ''" in failure for failure in got):
        return ["census reaped exit flip: expected the row to report the swallowed reap read as 0, got %r"
                % (got,)]
    return []


def _census_drain_reaped_flip_red(parent, script):
    """The drain-reaped row against a copy of _census_run whose in-drain ChildProcessError guard is
    disarmed: its except clause narrowed to an exception os.waitid never raises there, this file's own text
    otherwise untouched. The already-reaped leader's ChildProcessError then escapes the drain instead of
    failing closed, and the real row, run with this module's _census_run swapped for the copy (restored in
    a finally), must see that escape in place of the refusal. Returns the failures: [] when the escape
    confirmed the disarmed guard, a named failure when the row still got the refusal."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(
        parent, script, "census drain reaped flip",
        "except ChildProcessError:\n                            # something other",
        "except FileNotFoundError:\n                            # something other")
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_drain_reaped_row(parent / "row")
    except ChildProcessError:
        return []
    finally:
        this._census_run = real
    return ["census drain reaped flip: expected the reap to escape the disarmed guard, got %r" % (got,)]


def _census_kernel_sigchld_flip_red(parent, script, mode):
    """The kernel SIGCHLD row of `mode` against a copy of _census_run with only the kernel SigIgn read
    removed: its guard `if not ignored:` replaced by `if False:`, this file's own text otherwise untouched,
    so the signal.getsignal check stays armed. Under the libc SIG_IGN, which getsignal cannot see, and under
    the unreadable kernel status, the copy then launches its leader, and the real
    _census_kernel_sigchld_row, run with this module's _census_run swapped for the copy (restored in a
    finally), must report that launch. Returns the failures: [] when the row turned red, a named failure
    when it did not."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(parent, script, "census kernel sigchld flip", "if not ignored:", "if False:")
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_kernel_sigchld_row(parent / "row", mode)
    finally:
        this._census_run = real
    if not any("a leader was launched although the kernel read should have refused it" in failure
               for failure in got):
        return ["census kernel sigchld flip (%s): expected the row to report a launched leader, got %r"
                % (mode, got)]
    return []


def _census_exit_outlived_flip_red(parent, script, exit_code):
    """The exit-outlived row for `exit_code` against a copy of _census_run whose post-kill reap of an
    already exited leader is disarmed: its guard `if exited:` replaced by `if False:`, this file's own text
    otherwise untouched. The copy then discards the leader's own exit code after the group kill and returns
    no code, and the real _census_exit_outlived_row, run with this module's _census_run swapped for the
    copy (restored in a finally), must report the callee as killed at its bound. Returns the failures: []
    when the row turned red, a named failure when it did not."""
    this = sys.modules[__name__]
    flipped = _census_run_flipped(parent, script, "census exit outlived flip", "if exited:", "if False:")
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_exit_outlived_row(parent / "row", exit_code)
    finally:
        this._census_run = real
    if not any("killed at its 10 s bound before it ended" in failure for failure in got):
        return ["census exit outlived flip (exit %d): expected the row to report a bound kill, got %r"
                % (exit_code, got)]
    return []


def _census_exit_at_bound_flip_red(parent, script, race):
    """The exit-at-bound row of `race` against a copy of _census_run, taken from this file's own text, with
    two changes: that race's guard removed, and a 4 s sleep added. Race "stale": the last pass's os.waitid
    is skipped (`if not exited:` becomes `if not exited and not last:`), so the exit that landed after the
    drain's earlier os.waitid goes unseen and the copy kills the group and returns no code. Race "unread":
    the last pass's pipe reads are removed (`if exited and last:` becomes `if False:`), so the unread end of
    file leaves both pipes registered and the copy adds the descendant note. The copy also sleeps 4 s just
    before it sets its deadline, a parent descheduled between the leader's construction and that
    initialization. That sleep is what makes this flip itself fail, and so the self-test, when
    _census_paused_run's own fixes are reverted, and only on an ordinary schedule, since both detections
    depend on timing. With the pause anchored back to the leader's construction it ends before the real
    deadline, the drain sees the exit, or reads the end of file, on an ordinary pass, and the row passes
    despite the guard's removal; but a parent descheduled again on entering the drain's os.waitid for
    long enough (3 s more has been enough) pushes the pause past the deadline after all, and the flip
    passes. With the leader's gate removed or released early, the row reports that nothing was reproduced
    only when the leader starts and ends its 1 s sleep within those 4 s and so has exited before the first
    os.waitid. The real _census_exit_at_bound_row, run with this module's _census_run swapped for the copy
    (restored in a finally), must report that race's outcome. Returns the failures: [] when the row turned
    red, a named failure when it did not."""
    this = sys.modules[__name__]
    if race == "stale":
        old, new, want = "if not exited:", "if not exited and not last:", "got None with note ''"
    else:
        old, new, want = "if exited and last:", "if False:", "with note 'a descendant still held its output pipes"
    delay = ("deadline = time.monotonic() + bound",
             "time.sleep(4)\n            deadline = time.monotonic() + bound")
    flipped = _census_run_flipped(parent, script, "census exit at bound flip " + race, old, new, (delay,))
    real = this._census_run
    this._census_run = flipped
    try:
        got = _census_exit_at_bound_row(parent / "row", race)
    finally:
        this._census_run = real
    if not any(want in failure for failure in got):
        return ["census exit at bound flip (%s): expected the row to report %r, got %r" % (race, want, got)]
    return []


def _census_exit_at_bound_full_flip_red(parent, script):
    """The full-pipe row at 983041 bytes against a copy of _census_run, taken from this file's own text,
    with two changes: its last-pass reads put back to at most 16 reads of 65536 bytes per pipe (the
    byte-capped loop's header replaced by `for _ in range(16):`), and a 2 s sleep added just before it sets
    its deadline, a parent descheduled between the leader's construction and that initialization. The 16
    reads then stop one byte short of the end of file and the copy adds the descendant note with no
    descendant, and the real _census_exit_at_bound_full_row, run with this module's _census_run swapped for
    the copy (restored in a finally), must report that note. The sleep is what makes this flip itself
    fail, and so the self-test, when _CENSUS_FULL_STANDIN's gate is removed, and only on an ordinary
    schedule, since that detection depends on timing: the ungated stand-in, which writes its bytes into the
    raised pipe and exits at once, has exited before the first os.waitid only when it starts and ends
    within those 2 s, and the row then reports that nothing was reproduced. Returns (failures, outcome):
    ([], "ran") when the row turned red, a named failure when it did not, and the row's skip reason with no
    failures when the stand-in's pipe could not be raised."""
    this = sys.modules[__name__]
    delay = ("deadline = time.monotonic() + bound",
             "time.sleep(2)\n            deadline = time.monotonic() + bound")
    flipped = _census_run_flipped(parent, script, "census exit at bound full flip",
                                  "while taken <= _CENSUS_LAST_READ_CAP:", "for _ in range(16):", (delay,))
    real = this._census_run
    this._census_run = flipped
    try:
        got, outcome = _census_exit_at_bound_full_row(parent / "row", 983041)
    finally:
        this._census_run = real
    if outcome != "ran":
        return [], outcome
    if not any("with note 'a descendant still held its output pipes" in failure for failure in got):
        return ["census exit at bound full flip: expected the row to report the descendant note, got %r"
                % (got,)], outcome
    return [], outcome


def _close_harness_checks(parent, script):
    """The close-vector, close-harness and close-sweep checks of the self-test (run through _close_stage)."""
    import _close_selftest
    close_failures, close_runs = _close_vectors(parent / "close")
    for failure in close_failures:
        print("FAIL " + failure, file=sys.stderr)
    check("close-vectors", not close_failures)
    print("PASS close-vectors runs=" + str(close_runs))
    journal = (script.parents[1] / "opf" / "tools" / "_journal.py").read_text(encoding="utf-8")
    copy = (script.parent / "_close_selftest.py").read_text(encoding="utf-8")
    check("close-harness-in-step", _close_harness_in_step(journal, copy))
    check("close-harness-census-eio", _close_census_eio(_close_selftest))
    check("close-harness-census-readlink-eio", _close_census_eio(_close_selftest, "readlink"))
    # Every other read error fails closed too (each seam's one tolerated errno included for the other seam), and
    # ENOENT on the link read alone lists the descriptor with no kind.
    import errno
    for seam, codes in (("fstat", (errno.EACCES, errno.EPERM, errno.ENOENT)),
                        ("readlink", (errno.EACCES, errno.EPERM, errno.EBADF, errno.ENOENT))):
        for code in codes:
            check("close-harness-census-{}-{}".format(seam, errno.errorcode[code].lower()),
                  _close_census_eio(_close_selftest, seam, code))
    # An eventfd put at an epoll descriptor's number reads as a change by its anonymous-inode kind; the flip, a
    # table of (st_dev, st_ino) alone, reads it as unchanged wherever the two share an inode.
    anon = _close_selftest._st_anon_reuse(_close_selftest._st_fd_table)
    if anon is not None:
        check("close-harness-anon-reuse", anon[0])
        stat_only = _close_selftest._st_anon_reuse(
            lambda: dict((fd, ident[:2]) for fd, ident in _close_selftest._st_fd_table().items()))
        check("close-harness-anon-reuse-flip-red", not anon[1] or not stat_only[0])
        check("close-harness-anon-reuse-close-fault", _close_anon_reuse_close_fault(_close_selftest))
    require(copy.count("raise self.err") == 1, "close harness: drift flip anchor is not unique")
    check("close-harness-drift-red",
          not _close_harness_in_step(journal, copy.replace("raise self.err", "return None")))
    require(copy.count("\nimport sys\n") == 1, "close harness: preamble flip anchor is not unique")
    check("close-harness-preamble-red", not _close_harness_in_step(
        journal, copy.replace("\nimport sys\n", "\nimport sys\nos.close = lambda fd: None\n")))
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


def _close_stage(parent, script):
    """The close-harness checks, with a descriptor census that cannot read a descriptor anywhere in them (the
    close vectors, the census EIO case's own unpatched read, the watch-drop check) mapped to CannotEvaluate,
    which main() reports as exit 2."""
    import _close_selftest
    try:
        _close_harness_checks(parent, script)
    except _close_selftest._StCensusError as exc:  # the descriptor census cannot read one: cannot-evaluate
        raise CannotEvaluate(str(exc)) from exc


def _close_stage_leg(parent, script, target):
    """One leg of _close_stage_mapping_check: the census read of a held descriptor fails EIO only while the
    census runs under `target`. Returns the failure, or None."""
    import errno
    import io
    this = sys.modules[__name__]
    stage = parent / ("census-" + target)
    stage.mkdir()
    held = os.open(os.devnull, os.O_RDONLY)
    real_fstat, real_self_test, real_stdout = os.fstat, this.self_test, sys.stdout

    def failing(number, *args, **kwargs):
        frame = sys._getframe(1)
        if number == held and frame.f_code.co_name == "_st_fd_table":
            while frame is not None and frame.f_code.co_name != target:
                frame = frame.f_back
            if frame is not None:
                raise OSError(errno.EIO, "injected census read failure")
        return real_fstat(number, *args, **kwargs)
    captured = io.StringIO()
    try:
        os.fstat, sys.stdout = failing, captured
        this.self_test = lambda red_on_revert: _close_stage(stage, script)
        code = main(["--self-test"])
        got = json.loads(captured.getvalue().splitlines()[-1])   # the report follows the stage's PASS lines
    except Exception as exc:  # noqa: BLE001 - the verdict channel
        code, got = None, dict(detail="escaped: " + repr(exc))
    finally:
        os.fstat, this.self_test, sys.stdout = real_fstat, real_self_test, real_stdout
        os.close(held)
    named = "descriptor census cannot evaluate descriptor %d:" % held
    if code != 2 or got.get("code") != 2 or named not in got.get("detail", ""):
        return ("census error under %s: expected exit 2 with the cannot-evaluate report, got %r %r"
                % (target, code, got))
    return None


def _close_stage_mapping_check(parent, script):
    """This tool's own mapping, in process: with the census read of a held descriptor failing EIO under each
    of _close_vectors, _close_census_eio (its first, unpatched read) and _st_watch_drop_check, main() run over
    _close_stage must return 2 with the cannot-evaluate report naming that descriptor; an escaping exception
    (a census error read past the mapping) is a failure. Returns the failures."""
    legs = [_close_stage_leg(parent, script, target)
            for target in ("_close_vectors", "_close_census_eio", "_st_watch_drop_check")]
    return [failure for failure in legs if failure]


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
        skips = []
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
            if case_id in ("tip-dispatch", "tip-dispatch-declared", "tip-dispatch-undeclared",
                           "tip-schedule", "tip-schedule-declared", "tip-schedule-undeclared"):
                binding = report["context"]
                _, parents = commit_header(root, binding["head"])
                check(case_id + "-first-parent",
                      binding["head"] == binding["target"] and binding["base"] == parents[0]
                      and binding["transitions"] == [])
            if case_id in ("tip-dispatch-base", "tip-schedule-base", "tip-dispatch-bare",
                           "tip-schedule-bare"):
                check(case_id + "-refusal", "requires explicit --protected and refuses --base"
                      in report["detail"])
            if case_id in ("event-merge-group", "event-missing"):
                check(case_id + "-refusal", "unsupported GitHub Actions event" in report["detail"])
            if case_id in ("tip-dispatch-identity", "tip-schedule-identity"):
                check(case_id + "-identity", "--protected must equal" in report["detail"])
            if case_id in ("tip-dispatch-target-mismatch", "tip-schedule-target-mismatch"):
                check(case_id + "-binding",
                      "HEAD does not match --protected target" in report["detail"])
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
            if case_id in ("pending-open", "pending-open-second", "pending-open-initialize",
                           "pending-open-removed"):
                check(case_id + "-names-open-items",
                      any("pending fixes still open: " in row["detail"]
                          and "loose-end" in row["detail"] and "groundwork" not in row["detail"]
                          for row in report["snapshots"]))
            if case_id == "pending-bound-elsewhere":
                check(case_id + "-binding",
                      any("bound to release 1.0.0, not to the appended 1.1.0" in row["detail"]
                          for row in report["snapshots"]))
            if case_id == "unreadable":
                check("unreadable-read-boundary",
                      any("injected unreadable working file" in row["detail"]
                          for row in report["snapshots"]))
            print("PASS " + case_id)
        _close_stage(parent, script)
        stage_failures = _close_stage_mapping_check(parent, script)
        for failure in stage_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("close-stage-census-mapping", not stage_failures)
        mapping_failures, mapping_runs = _census_mapping_check(script.parents[1])
        for failure in mapping_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-mapping", not mapping_failures)
        print("PASS census-mapping runs=" + str(mapping_runs) + " close-stage-legs=3")
        bound_failures, bound_rows = _census_bound_rows(parent)
        for failure in bound_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-mapping-bound", not bound_failures)
        print("PASS census-mapping-bound rows=" + str(bound_rows) + " entries=" + str(len(_CENSUS_MAPPINGS))
              + " bound-sum=" + str(sum(row[3] for row in _CENSUS_MAPPINGS)))
        interrupt_failures = _census_interrupt_row(parent / "census-interrupt")
        for failure in interrupt_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-interrupt-kill", not interrupt_failures)
        print("PASS census-interrupt-kill")
        unreaped_failures = _census_interrupt_unreaped_row(parent / "census-interrupt-unreaped")
        for failure in unreaped_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-interrupt-kill-unreaped", not unreaped_failures)
        print("PASS census-interrupt-kill-unreaped")
        record_failures = _census_record_atomic_row(parent / "census-record-atomic")
        for failure in record_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-record-atomic", not record_failures)
        print("PASS census-record-atomic")
        exit_code_failures = _census_exit_code_row(parent / "census-exit-code")
        for failure in exit_code_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-exit-code-unreaped", not exit_code_failures)
        print("PASS census-exit-code-unreaped")
        reaped_exit_failures = _census_reaped_exit_row(parent / "census-exit-reaped")
        for failure in reaped_exit_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-exit-reaped-under-drain", not reaped_exit_failures)
        print("PASS census-exit-reaped-under-drain")
        drain_reaped_failures = _census_drain_reaped_row(parent / "census-drain-reaped")
        for failure in drain_reaped_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-drain-reaped", not drain_reaped_failures)
        print("PASS census-drain-reaped")
        construction_failures, construction_outcome = _census_construction_interrupt_row(
            parent / "census-interrupt-construction")
        for failure in construction_failures:
            print("FAIL " + failure, file=sys.stderr)
        # a skipped row is reported as a skip, never recorded as a passed check id
        if construction_outcome == "ran":
            check("census-interrupt-kill-construction", not construction_failures)
            print("PASS census-interrupt-kill-construction")
        else:
            skips.append("census-interrupt-kill-construction")
            print("SKIP census-interrupt-kill-construction (" + construction_outcome + ")")
        sigchld_failures = _census_sigchld_ignored_row(parent / "census-sigchld-ignored")
        for failure in sigchld_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("census-sigchld-ignored", not sigchld_failures)
        print("PASS census-sigchld-ignored")
        for mode in ("libc", "unreadable"):
            kernel_failures = _census_kernel_sigchld_row(parent / ("census-sigchld-" + mode), mode)
            for failure in kernel_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-sigchld-kernel-" + mode, not kernel_failures)
            print("PASS census-sigchld-kernel-" + mode)
        for exit_code in (1, 2):
            outlived_failures = _census_exit_outlived_row(parent / ("census-exit-outlived-%d" % exit_code),
                                                          exit_code)
            for failure in outlived_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-exit-%d-outlived" % exit_code, not outlived_failures)
            print("PASS census-exit-%d-outlived" % exit_code)
        for race in ("stale", "unread"):
            race_failures = _census_exit_at_bound_row(parent / ("census-exit-at-bound-" + race), race)
            for failure in race_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-exit-at-bound-" + race, not race_failures)
            print("PASS census-exit-at-bound-" + race)
        # A pipe an exited leader left holding more than 16 reads' worth (983041 bytes, no descendant) is
        # read to its end of file; at the cap set to 983040 bytes, 983040 bytes are read to the end and
        # 983041 bytes are taken as held.
        for name, size, cap in (("full", 983041, None), ("cap-at", 983040, 983040),
                                ("cap-over", 983041, 983040)):
            full_failures, full_outcome = _census_exit_at_bound_full_row(
                parent / ("census-exit-at-bound-" + name), size, cap)
            for failure in full_failures:
                print("FAIL " + failure, file=sys.stderr)
            # a skipped row is reported as a skip, never recorded as a passed check id
            if full_outcome == "ran":
                check("census-exit-at-bound-" + name, not full_failures)
                print("PASS census-exit-at-bound-" + name)
            else:
                skips.append("census-exit-at-bound-" + name)
                print("SKIP census-exit-at-bound-" + name + " (" + full_outcome + ")")
        if red_on_revert:
            flip_failures, flip_outcome = _census_exit_at_bound_full_flip_red(
                parent / "census-exit-at-bound-full-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            # a skipped flip is reported as a skip, never recorded as a passed check id
            if flip_outcome == "ran":
                check("census-exit-at-bound-full-red", not flip_failures)
                print("RED census-exit-at-bound-full at the last pass's 16-read loop")
            else:
                skips.append("census-exit-at-bound-full-red")
                print("SKIP census-exit-at-bound-full-red (" + flip_outcome + ")")
            for mode in ("libc", "unreadable"):
                flip_failures = _census_kernel_sigchld_flip_red(parent / ("census-sigchld-flip-" + mode),
                                                                script, mode)
                for failure in flip_failures:
                    print("FAIL " + failure, file=sys.stderr)
                check("census-sigchld-kernel-" + mode + "-red", not flip_failures)
                print("RED census-sigchld-kernel-" + mode + " at the kernel SigIgn read")
            for exit_code in (1, 2):
                flip_failures = _census_exit_outlived_flip_red(
                    parent / ("census-exit-outlived-flip-%d" % exit_code), script, exit_code)
                for failure in flip_failures:
                    print("FAIL " + failure, file=sys.stderr)
                check("census-exit-%d-outlived-red" % exit_code, not flip_failures)
                print("RED census-exit-%d-outlived at the post-kill reap of an exited leader" % exit_code)
            for race, guard in (("stale", "the last pass's waitid"), ("unread", "the last pass's pipe reads")):
                flip_failures = _census_exit_at_bound_flip_red(parent / ("census-exit-at-bound-flip-" + race),
                                                               script, race)
                for failure in flip_failures:
                    print("FAIL " + failure, file=sys.stderr)
                check("census-exit-at-bound-" + race + "-red", not flip_failures)
                print("RED census-exit-at-bound-" + race + " at " + guard)
            flip_failures = _census_interrupt_flip_red(parent / "census-interrupt-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-interrupt-kill-red", not flip_failures)
            print("RED census-interrupt-kill at the reap-before-kill cleanup")
            flip_failures = _census_exit_code_flip_red(parent / "census-exit-code-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-exit-code-unreaped-red", not flip_failures)
            print("RED census-exit-code-unreaped at the drain's WNOWAIT reap order")
            flip_failures = _census_reaped_exit_flip_red(parent / "census-exit-reaped-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-exit-reaped-under-drain-red", not flip_failures)
            print("RED census-exit-reaped-under-drain at the final reap's missing-status guard")
            flip_failures = _census_drain_reaped_flip_red(parent / "census-drain-reaped-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-drain-reaped-red", not flip_failures)
            print("RED census-drain-reaped at the drain's reaped-leader guard")
            flip_failures, flip_outcome = _census_construction_flip_red(
                parent / "census-construction-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            # a skipped flip is reported as a skip, never recorded as a passed check id
            if flip_outcome == "ran":
                check("census-interrupt-kill-construction-red", not flip_failures)
                print("RED census-interrupt-kill-construction at the construction-time SIGINT block")
            else:
                skips.append("census-interrupt-kill-construction-red")
                print("SKIP census-interrupt-kill-construction-red (" + flip_outcome + ")")
            flip_failures = _census_record_atomic_flip_red(parent / "census-record-atomic-flip")
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-record-atomic-red", not flip_failures)
            print("RED census-record-atomic at the stand-in's os.replace of its record")
            flip_failures = _census_sigchld_flip_red(parent / "census-sigchld-flip", script)
            for failure in flip_failures:
                print("FAIL " + failure, file=sys.stderr)
            check("census-sigchld-ignored-red", not flip_failures)
            print("RED census-sigchld-ignored at the pre-launch SIGCHLD check")
        # An in-process call of self_test() hands the caller's environment back exactly, with the
        # git fixture scrub and the git wrapper in effect inside, after a return and after an
        # exception: the entry point is run in a child around a stand-in _self_test_isolated,
        # once with red_on_revert True and once with it False. The stand-in must receive the
        # value passed each time, so a constant passed on in place of the parameter fails one run.
        from _entry_restore_selftest import entry_restore_failures
        restore_failures = (
            entry_restore_failures(script, "self_test", "_self_test_isolated", args=(True,))
            + entry_restore_failures(script, "self_test", "_self_test_isolated", args=(False,)))
        for failure in restore_failures:
            print("FAIL " + failure, file=sys.stderr)
        check("entry-restores-caller-env", not restore_failures)
        print("PASS entry-restores-caller-env")
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
                ("ci-event", 'require(event in ("pull_request", "push") + TIP_EVENTS,',
                 "require(True,", "event-merge-group", 0),
                ("tip-event-base", "require(protected is not None and before is None,",
                 "require(True,", "tip-dispatch-base", 1),
                ("tip-dispatch-target",
                 'if actions and event in ("push",) + TIP_EVENTS:\n            require(head == target,',
                 'if actions and event == "push":\n            require(head == target,',
                 "tip-dispatch-target-mismatch", 0),
                ("tip-schedule-target",
                 'if actions and event in ("push",) + TIP_EVENTS:\n            require(head == target,',
                 'if actions and event == "push":\n            require(head == target,',
                 "tip-schedule-target-mismatch", 0),
                ("pending-open-items", "if open_items:", "if False:", "pending-open", 0),
                ("pending-binding", 'if head_pending["release"] != after[-1]:', "if False:",
                 "pending-bound-elsewhere", 0),
                ("pending-head-input",
                 'head_pending = parse_pending(head_pending, "head pending fixes")',
                 "head_pending = None", "head-fix-toml", 0),
                ("pending-base-input",
                 'base_pending = parse_pending(base_pending, "base pending fixes")',
                 "base_pending = None", "base-fix-toml", 0),
                ("pending-removed", "elif base_pending is not None:",
                 "elif False:", "pending-open-removed", 0),
                ("pending-top-keys",
                 "require(isinstance(data, dict) and set(data) == PENDING_KEYS,",
                 "require(isinstance(data, dict) and set(data) >= PENDING_KEYS,",
                 "head-fix-unknown", 0),
                ("pending-item-strings",
                 "require(isinstance(item[key], str) and bool(item[key].strip()),",
                 "require(True,", "head-fix-typed-id", 0),
                ("pending-item-blank",
                 "require(isinstance(item[key], str) and bool(item[key].strip()),",
                 "require(isinstance(item[key], str) and bool(item[key]),",
                 "head-fix-space-id", 0),
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
              + " red-on-revert=" + str(red_on_revert).lower()
              + (" skipped=" + ",".join(skips) if skips else ""))
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
