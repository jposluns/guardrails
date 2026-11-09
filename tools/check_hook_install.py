#!/usr/bin/env python3
"""Read-only drift check for an installed copy of the core hook dispatcher (aiqt_hooks.py).

  check_hook_install.py --installed PATH --repo DIR --ref REF --target COMMIT|TAG
  check_hook_install.py --self-test [--execution-report ABS_PATH]

Exit: 0 MATCH (the installed bytes equal the member at the verified target), 1 DRIFT (a well-formed
mismatch, reported with what the copy does match), 2 CANNOT-EVALUATE (with the reason).

The installed path is checked with lstat before anything opens it: a symbolic link is followed and both the
link and its target are reported, and anything that is not a regular file (a FIFO, a device, a directory, a
dangling or looping link) or is larger than MAX_INSTALLED_BYTES is cannot-evaluate. The comparison is of raw
bytes only, never of decoded text.

--target names the pinned commit (or tag) the operator installs from; it alone defines what is expected. The
installed copy's own content and the manifest's release-version never choose it. --ref is the published tip
the history scan reads; it is required because this tool cannot tell offline how fresh a ref is.

A. Target validation. The target's .aiqt/manifest.toml and .aiqt/release/root.txt are read with git cat-file
blob (no checkout filter applies), with replacement refs disabled; the sha256 of the manifest bytes must
equal root.txt, and the manifest's [[sources]] row for the member must give the size and sha256 of the
member's blob at the target. The target must be reachable from --ref.

B. Identification. The installed copy's git blob id is computed in the repository's object format. Every
commit reachable from --ref is read (the first-parent line and the side branches, merges included, so bytes
a merge resolution created are seen), and a commit whose member or plugin-copy blob equals that id while its
first parent's does not is reported as a match, flagged first-parent or not, with every v* tag whose tree
carries the blob, labelled "tag (not attested)". Every blob id hit is confirmed by the sha256 of its content.
For a first-parent match the report gives the first later first-parent change of the member and how many
first-parent changes the copy is behind --ref.

Cannot-evaluate: the installed path is missing, unreadable, not a regular file, a bad link or over the cap;
git is missing or fails, or --ref or --target does not resolve; the target is not reachable from --ref; the
target lacks the member, root.txt or a manifest row for the member; a manifest or releases.toml fails to
parse (RecursionError, MemoryError and ValueError handled at the parse site) or is missing; the root does
not match; the manifest row does not match the blob; a blob id matches but the sha256 does not; the clone
is shallow and nothing matched (a shallow clone cannot prove "no match"); releases.toml at --ref carries a
[[release]] row (attested rows are not evaluated by this version). When nothing matches the report says "no
match among the commits examined" with the scan range; it never claims the copy's origin is unknown in
general.

LIMITS. Without a root obtained from an independent channel, this detects accident, not tamper: whoever can
rewrite the repository can rewrite the manifest and root together. A byte match means the copy has not
drifted from the target; it is not a check that the hook behaves correctly. The tool never writes and
never touches the network: git runs with lazy fetch, prompts and optional locks off.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_hook_install.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
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
import signal
import stat
import subprocess
import tempfile
import threading
import tomllib
from pathlib import Path

MEMBER = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
PLUGIN_ALIAS = "plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py"
MANIFEST_REL = ".aiqt/manifest.toml"
ROOT_REL = ".aiqt/release/root.txt"
RELEASES_REL = ".aiqt/core/releases.toml"
MAX_INSTALLED_BYTES = 16 << 20
MAX_BLOB_BYTES = 64 << 20
GIT_TIMEOUT = 300
ROOT_RE = re.compile(rb"sha256:([0-9a-f]{64})\n?")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
EXIT_BY_VERDICT = {"MATCH": 0, "DRIFT": 1, "CANNOT-EVALUATE": 2}
LIMITS = ("accident, not tamper, without an independent root: whoever can rewrite the repository can rewrite "
          "the manifest and its root together. A byte match means the copy has not drifted from the target; "
          "it is not a check that the hook behaves correctly.")
# Inherited variables that would point git at another repository, object store or ref namespace; they are
# dropped so the repository read is the one --repo names.
_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                      "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
                      "GIT_REPLACE_REF_BASE", "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE", "GIT_CEILING_DIRECTORIES",
                      "GIT_DISCOVERY_ACROSS_FILESYSTEM")


class CannotEvaluate(Exception):
    """An input or a git read that cannot be judged."""


class GitFailure(CannotEvaluate):
    """git is missing, timed out or exited nonzero."""


def _git_env():
    env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_VARS}
    env.update({"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    return env


def _git(repo, args, data=None, ok=(0,)):
    """(returncode, stdout bytes) of one read-only git command in repo; GitFailure on any other exit."""
    try:
        proc = subprocess.run(
            ["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
             "--no-replace-objects", "-C", str(repo), *args],
            input=data, capture_output=True, timeout=GIT_TIMEOUT, env=_git_env())
    except FileNotFoundError:
        raise GitFailure("git is not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GitFailure("git {} timed out after {} s".format(args[0], GIT_TIMEOUT)) from None
    except OSError as exc:
        raise GitFailure("git could not be started ({})".format(exc)) from None
    if proc.returncode not in ok:
        raise GitFailure("git {} exited {}: {}".format(
            args[0], proc.returncode, proc.stderr.decode("utf-8", "replace").strip()[:400]))
    return proc.returncode, proc.stdout


def _over_cap(size, cap):
    """True when size is over the cap; exactly the cap is still read."""
    return size > cap


def _read_installed(path):
    """The installed copy's facts and bytes; CannotEvaluate for anything but a readable regular file within the
    cap. lstat comes first, so a FIFO or device is never opened."""
    path = str(path)
    try:
        link = os.lstat(path)
    except FileNotFoundError:
        raise CannotEvaluate("installed path {}: missing".format(path)) from None
    except OSError as exc:
        raise CannotEvaluate("installed path {}: cannot stat ({})".format(path, exc)) from None
    is_link = stat.S_ISLNK(link.st_mode)
    try:
        real = os.path.realpath(path, strict=True)
        info = os.stat(real)
    except (OSError, RuntimeError) as exc:
        raise CannotEvaluate("installed path {}: a dangling or looping symbolic link ({})".format(path, exc)) from None
    if not stat.S_ISREG(info.st_mode):
        raise CannotEvaluate("installed path {}: not a regular file".format(path))
    if _over_cap(info.st_size, MAX_INSTALLED_BYTES):
        raise CannotEvaluate("installed path {}: {} bytes, over the {} byte cap".format(
            path, info.st_size, MAX_INSTALLED_BYTES))
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise CannotEvaluate("installed path {}: unreadable ({})".format(path, exc)) from None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise CannotEvaluate("installed path {}: changed while it was read".format(path))
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_INSTALLED_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("installed path {}: unreadable ({})".format(path, exc)) from None
    finally:
        if fd is not None:
            os.close(fd)
    if _over_cap(len(data), MAX_INSTALLED_BYTES):
        raise CannotEvaluate("installed path {}: over the {} byte cap".format(path, MAX_INSTALLED_BYTES))
    return {"path": path, "symlink": is_link, "realpath": real, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}, data


def _parse_toml(data, where):
    """The parsed TOML document; CannotEvaluate on bytes that are not UTF-8 TOML, the parser's resource errors
    included."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    doc, problem = None, None
    try:
        doc = tomllib.loads(text)
    except (RecursionError, MemoryError, ValueError) as exc:
        problem = "{}: not valid TOML ({}: {})".format(where, type(exc).__name__, exc)
    if problem is not None:
        raise CannotEvaluate(problem)
    return doc


def _blob_ids(repo, specs):
    """{spec: (oid, size)} for each existing blob spec (rev:path); a missing one is absent from the result."""
    if not specs:
        return {}
    _rc, out = _git(repo, ["cat-file", "--batch-check"], data="".join(s + "\n" for s in specs).encode("utf-8"))
    lines = out.decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != len(specs):
        raise GitFailure("git cat-file --batch-check answered {} lines for {} queries".format(len(lines), len(specs)))
    found = {}
    for spec, line in zip(specs, lines):
        parts = line.split(" ")
        if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
            found[spec] = (parts[0], int(parts[2]))
    return found


def _blob(repo, oid, size, where):
    if _over_cap(size, MAX_BLOB_BYTES):
        raise CannotEvaluate("{}: {} bytes, over the {} byte cap".format(where, size, MAX_BLOB_BYTES))
    _rc, data = _git(repo, ["cat-file", "blob", oid])
    if len(data) != size:
        raise CannotEvaluate("{}: git cat-file returned {} bytes for a {} byte blob".format(where, len(data), size))
    return data


def _file_at(repo, rev, rel):
    found = _blob_ids(repo, [rev + ":" + rel])
    if rev + ":" + rel not in found:
        return None
    oid, size = found[rev + ":" + rel]
    return oid, _blob(repo, oid, size, "{} at {}".format(rel, rev))


def _resolve(repo, value, flag):
    if not value or value.startswith("-"):
        raise CannotEvaluate("{} {!r}: not a revision".format(flag, value))
    rc, out = _git(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", value + "^{commit}"], ok=(0, 1))
    commit = out.decode("ascii", "replace").strip()
    if rc != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise CannotEvaluate("{} {}: does not resolve to a commit".format(flag, value))
    return commit


def _is_shallow(repo):
    _rc, out = _git(repo, ["rev-parse", "--is-shallow-repository"])
    return out.strip() == b"true"


def _attested_rows(doc):
    rows = doc.get("release", [])
    return rows if isinstance(rows, list) else [rows]


def _check_root(manifest, root_bytes):
    match = ROOT_RE.fullmatch(root_bytes)
    if match is None:
        raise CannotEvaluate("{} at the target: not a sha256:<64 hex> line".format(ROOT_REL))
    got = hashlib.sha256(manifest).hexdigest()
    if got != match.group(1).decode("ascii"):
        raise CannotEvaluate("{} at the target: sha256 {} of the manifest bytes differs from the root {}".format(
            MANIFEST_REL, got, match.group(1).decode("ascii")))


def _member_row(doc):
    rows = [row for row in doc.get("sources", []) if isinstance(row, dict) and row.get("path") == MEMBER]
    if len(rows) != 1:
        raise CannotEvaluate("{} at the target: {} [[sources]] rows for {} (exactly 1 required)".format(
            MANIFEST_REL, len(rows), MEMBER))
    row = rows[0]
    if not isinstance(row.get("bytes"), int) or not isinstance(row.get("sha256"), str) \
            or not DIGEST_RE.fullmatch(row["sha256"]):
        raise CannotEvaluate("{} at the target: the {} row lacks a byte count or a sha256".format(MANIFEST_REL, MEMBER))
    return row


def _check_row(row, member):
    got = (len(member), hashlib.sha256(member).hexdigest())
    if (row["bytes"], row["sha256"]) != got:
        raise CannotEvaluate("{} at the target: the {} row ({} bytes, sha256 {}) differs from its blob ({} bytes, "
                             "sha256 {})".format(MANIFEST_REL, MEMBER, row["bytes"], row["sha256"], *got))


def _target_verdict(installed, target):
    """True when the installed bytes equal the member blob at the verified target, byte for byte."""
    return (installed["sha256"], installed["bytes"]) == (target["member-sha256"], target["member-bytes"])


def _scanned(row):
    """Every reachable commit is examined, merges included: a merge resolution can create new bytes."""
    return True


def _commits(repo, ref, first_parent):
    """[(commit, committer time, parents)] oldest first; the first-parent line when first_parent is set."""
    args = ["rev-list", "--reverse", "--timestamp", "--parents"] + (["--first-parent"] if first_parent else [])
    _rc, out = _git(repo, args + ["--end-of-options", ref])
    rows = []
    for line in out.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit():
            raise GitFailure("git rev-list printed an unexpected line {!r}".format(line[:120]))
        rows.append((parts[1], int(parts[0]), parts[2:]))
    return rows


def _identify(repo, ref, blob_id, sha256):
    """(matches, stale, tags, examined) for the installed blob id across the history reachable from ref."""
    first = _commits(repo, ref, True)
    every = _commits(repo, ref, False)
    on_line = {commit for commit, _t, _p in first}
    wanted = set()
    for commit, _t, parents in every:
        for rel in (MEMBER, PLUGIN_ALIAS):
            wanted.add(commit + ":" + rel)
            if parents:
                wanted.add(parents[0] + ":" + rel)
    tag_rows = []
    _rc, out = _git(repo, ["for-each-ref", "--format=%(refname)%09%(objectname)%09%(*objectname)", "refs/tags/"])
    for line in out.decode("utf-8", "replace").splitlines():
        name, obj, peeled = (line.split("\t") + ["", ""])[:3]
        if name.startswith("refs/tags/v"):
            tag_rows.append((name[len("refs/tags/"):], peeled or obj))
            wanted.add((peeled or obj) + ":" + MEMBER)
    blobs = _blob_ids(repo, sorted(wanted))
    confirmed = set()

    def hit(spec):
        if spec not in blobs or blobs[spec][0] != blob_id:
            return False
        if blob_id not in confirmed:
            data = _blob(repo, blob_id, blobs[spec][1], "blob " + blob_id)
            if hashlib.sha256(data).hexdigest() != sha256:
                raise CannotEvaluate("blob {} matches the installed blob id but its sha256 differs".format(blob_id))
            confirmed.add(blob_id)
        return True

    matches = []
    for commit, when, parents in every:
        if not _scanned({"commit": commit, "parents": parents}):
            continue
        for rel in (MEMBER, PLUGIN_ALIAS):
            if hit(commit + ":" + rel) and not (parents and hit(parents[0] + ":" + rel)):
                matches.append({"commit": commit, "path": rel, "first-parent": commit in on_line,
                                "committer-time": when, "parents": len(parents)})
    changes = []
    previous = None
    for commit, when, _parents in first:
        current = blobs.get(commit + ":" + MEMBER, (None, 0))[0]
        if current != previous:
            changes.append((commit, when, current))
        previous = current
    stale = None
    line_hits = [index for index, (_c, _w, oid) in enumerate(changes) if oid == blob_id]
    if line_hits:
        last = line_hits[-1]
        later = changes[last + 1:]
        stale = {"matched-change": changes[last][0], "changes-behind": len(later),
                 "superseded-by": later[0][0] if later else None,
                 "superseded-at": later[0][1] if later else None,
                 "later-change-times": [when for _c, when, _o in later]}
    tags = [{"tag": name, "commit": commit, "label": "tag (not attested)"}
            for name, commit in sorted(tag_rows) if hit(commit + ":" + MEMBER)]
    examined = {"commits": len(every), "first-parent-commits": len(first),
                "range": "every commit reachable from " + ref}
    return matches, stale, tags, examined


def evaluate(installed_path, repo, ref, target):
    """The report dict; its "verdict" is MATCH, DRIFT or CANNOT-EVALUATE."""
    report = {"installed": None, "reference": None, "target": None, "matches": [], "tags": [], "stale": None,
              "examined": None, "verdict": "CANNOT-EVALUATE", "reasons": [], "limits": LIMITS}
    try:
        report["installed"], data = _read_installed(installed_path)
        _rc, out = _git(repo, ["rev-parse", "--show-object-format"])
        fmt = out.decode("ascii", "replace").strip()
        if fmt not in ("sha1", "sha256"):
            raise CannotEvaluate("unknown object format {!r}".format(fmt))
        blob_id = hashlib.new(fmt, b"blob %d\x00" % len(data) + data).hexdigest()
        report["installed"]["blob-id"] = blob_id
        shallow = _is_shallow(repo)
        ref_commit = _resolve(repo, ref, "--ref")
        target_commit = _resolve(repo, target, "--target")
        report["reference"] = {"repo": str(repo), "ref": ref, "commit": ref_commit, "shallow": shallow,
                               "object-format": fmt}
        rc, _out = _git(repo, ["merge-base", "--is-ancestor", target_commit, ref_commit], ok=(0, 1))
        if rc != 0:
            raise CannotEvaluate("--target {} is not reachable from --ref {}".format(target, ref))
        releases = _file_at(repo, ref_commit, RELEASES_REL)
        if releases is None:
            raise CannotEvaluate("{} is missing at --ref, so whether any release is attested is unknown".format(
                RELEASES_REL))
        if _attested_rows(_parse_toml(releases[1], RELEASES_REL + " at --ref")):
            raise CannotEvaluate("{} at --ref: attested rows present; not evaluated by this version".format(
                RELEASES_REL))
        manifest = _file_at(repo, target_commit, MANIFEST_REL)
        root = _file_at(repo, target_commit, ROOT_REL)
        member = _file_at(repo, target_commit, MEMBER)
        for rel, found in ((MANIFEST_REL, manifest), (ROOT_REL, root), (MEMBER, member)):
            if found is None:
                raise CannotEvaluate("{} is missing at the target".format(rel))
        _check_root(manifest[1], root[1])
        doc = _parse_toml(manifest[1], MANIFEST_REL + " at the target")
        row = _member_row(doc)
        _check_row(row, member[1])
        report["target"] = {"commit": target_commit, "manifest-root": hashlib.sha256(manifest[1]).hexdigest(),
                            "root-verified": True, "member-bytes": len(member[1]),
                            "member-sha256": hashlib.sha256(member[1]).hexdigest(),
                            "row-sha256": row["sha256"],
                            "release-version": doc.get("release-version"),
                            "release-version-use": "reported only; never used to choose or judge"}
        matches, stale, tags, examined = _identify(repo, ref_commit, blob_id, report["installed"]["sha256"])
        report.update(matches=matches, stale=stale, tags=tags, examined=examined)
        if _target_verdict(report["installed"], report["target"]):
            report["verdict"] = "MATCH"
        elif not matches and shallow:
            raise CannotEvaluate("no match among the commits examined, and the clone is shallow, so the history "
                                 "cannot prove there is none")
        else:
            report["verdict"] = "DRIFT"
            if not matches:
                report["reasons"].append("no match among the commits examined ({})".format(examined["range"]))
    except CannotEvaluate as exc:
        report["verdict"] = "CANNOT-EVALUATE"
        report["reasons"].append(str(exc))
    return report


def _summary(report):
    verdict = report["verdict"]
    if verdict == "MATCH":
        return "MATCH: the installed bytes equal {} at the verified target {}; {}".format(
            MEMBER, report["target"]["commit"], LIMITS)
    if verdict == "DRIFT":
        return "DRIFT: the installed bytes differ from the target; {} matching commit(s); {}".format(
            len(report["matches"]), LIMITS)
    return "CANNOT-EVALUATE: " + "; ".join(report["reasons"])


def run(argv):
    """Parse the operator arguments, print the JSON report and the summary line, and return the exit code."""
    options, rest = {}, list(argv)
    while rest:
        flag = rest.pop(0)
        if flag not in ("--installed", "--repo", "--ref", "--target") or not rest or flag in options:
            print("error: usage: check_hook_install.py --installed PATH --repo DIR --ref REF --target COMMIT|TAG "
                  "(or --self-test); fail closed", file=sys.stderr)
            return 2
        options[flag] = rest.pop(0)
    if len(options) != 4:
        print("error: --installed, --repo, --ref and --target are all required; fail closed", file=sys.stderr)
        return 2
    report = evaluate(options["--installed"], options["--repo"], options["--ref"], options["--target"])
    print(json.dumps(report, indent=2, sort_keys=True))
    print(_summary(report))
    return EXIT_BY_VERDICT[report["verdict"]]


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record the
# instrumented check() helper appends to; the execution gate reconciles the report against the registry.
SUITE_ID = "hook-install-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


V1 = b"#!/usr/bin/env python3\n# fixture hook, first version\nprint('v1')\n"
V2 = b"#!/usr/bin/env python3\n# fixture hook, side-branch version\nprint('v2')\n"
V2M = b"#!/usr/bin/env python3\n# fixture hook, merge-resolution version\nprint('v2 merged')\n"
V3 = b"#!/usr/bin/env python3\n# fixture hook, third version\nprint('v3')\n"
UNKNOWN = b"#!/usr/bin/env python3\n# bytes no commit ever carried\nprint('elsewhere')\n"


@contextlib.contextmanager
def _hermetic_env(home):
    """os.environ for fixture git runs: no system or global config, HOME inside the fixture, fixed identity and
    dates; restored afterwards."""
    saved = dict(os.environ)
    os.environ.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                       "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                       "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                       "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
                       "LC_ALL": "C"})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _fx(repo, *args, data=None):
    """One fixture git command (writes only inside the fixture); its stdout."""
    proc = subprocess.run(
        ["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-C", str(repo), *args],
        input=data, capture_output=True, timeout=GIT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError("fixture git {} failed: {}".format(args, proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "replace").strip()


def _put(repo, rel, data):
    path = Path(repo) / rel
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _manifest_bytes(member, row_sha=None, extra=b""):
    rows = "".join('[[sources]]\npath = "{}"\nbytes = {}\nsha256 = "{}"\n\n'.format(
        rel, len(member), row_sha or hashlib.sha256(member).hexdigest()) for rel in (MEMBER, PLUGIN_ALIAS))
    return ('format-version = 1\nrelease-version = "1.0.5"\ngenesis = true\n\n' + rows).encode("ascii") + extra


def _release(repo, member, manifest=None, root_of=None, releases=b"format-version = 1\n"):
    """Write a fixture release tree: the member and its plugin copy, the manifest, its root and releases.toml."""
    manifest = _manifest_bytes(member) if manifest is None else manifest
    _put(repo, MEMBER, member)
    _put(repo, PLUGIN_ALIAS, member)
    _put(repo, MANIFEST_REL, manifest)
    _put(repo, ROOT_REL, "sha256:{}\n".format(hashlib.sha256(manifest if root_of is None else root_of)
                                              .hexdigest()).encode("ascii"))
    _put(repo, RELEASES_REL, releases)


def _commit(repo, message):
    _fx(repo, "add", "-A")
    _fx(repo, "commit", "-q", "-m", message)
    return _fx(repo, "rev-parse", "HEAD")


def _build(tmp):
    """The fixture: A (v1, tag vA) and X on main, B (v2) on a side branch from A, M merging B with a resolution
    that writes v2', C (v3) at main; branches off C carry one broken target each."""
    repo = Path(tmp) / "repo"
    os.makedirs(repo)
    _fx(repo, "init", "-q", "-b", "main")
    ids = {}
    _release(repo, V1)
    ids["A"] = _commit(repo, "A")
    _fx(repo, "tag", "-a", "vA", "-m", "vA")
    _put(repo, "other.txt", b"x\n")
    ids["X"] = _commit(repo, "X")
    _fx(repo, "checkout", "-q", "-b", "side", ids["A"])
    _release(repo, V2)
    ids["B"] = _commit(repo, "B")
    _fx(repo, "checkout", "-q", "main")
    _fx(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    _release(repo, V2M)
    ids["M"] = _commit(repo, "M")
    _release(repo, V3)
    ids["C"] = _commit(repo, "C")
    good = _manifest_bytes(V3)
    deep = b"deep = " + b"[" * 6000 + b"]" * 6000 + b"\n"
    for branch, kwargs in (
            ("bad-root", {"manifest": good.replace(b'"1.0.5"', b'"1.0.6"'), "root_of": good}),
            ("bad-row", {"manifest": _manifest_bytes(V3, row_sha="0" * 64)}),
            ("bad-toml", {"manifest": good + b"[sources\n"}),
            ("deep-toml", {"manifest": good + deep}),
            ("attested", {"releases": b'format-version = 1\n\n[[release]]\nversion = "1.0.5"\n'})):
        _fx(repo, "checkout", "-q", "-b", branch, ids["C"])
        _release(repo, V3, **kwargs)
        ids[branch] = _commit(repo, branch)
        _fx(repo, "checkout", "-q", "main")
    return repo, ids


def _installed(tmp, data, name="installed.py"):
    path = Path(tmp) / name
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _case(path, repo, ref, target):
    """(exit code, report) of one in-process run; the installed copy's sha256 must be unchanged by it."""
    before = _sha(path) if os.path.isfile(path) and not stat.S_ISFIFO(os.lstat(path).st_mode) else None
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = run(["--installed", str(path), "--repo", str(repo), "--ref", ref, "--target", target])
    text = out.getvalue()
    report = json.loads(text[:text.rindex("}") + 1])
    if before is not None and _sha(path) != before:
        FAILURES.append("the installed copy changed during a run: " + str(path))
    return code, report


@contextlib.contextmanager
def _patched(name, value):
    original = globals()[name]
    globals()[name] = value
    try:
        yield
    finally:
        globals()[name] = original


def _child_run(args, env=None, timeout=60):
    """(returncode or "TIMEOUT", stdout) of this tool run as a child in its own session; the whole group is
    killed when it ends or times out."""
    proc = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=env)
    try:
        out, _err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _err = proc.communicate()
        code = "TIMEOUT"
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    return code, out.decode("utf-8", "replace")


STAND_IN = """#!{python} -I
import json, subprocess, sys
args = sys.argv[1:]
with open({log!r}, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if "rev-list" in args:
    sys.stderr.write("stand-in git: rev-list refused\\n")
    sys.exit(128)
sys.exit(subprocess.call([{real!r}] + args))
"""
# The subcommands the tool runs, in order, up to the first history read (the stand-in refuses it).
EXPECTED_CALLS = ["rev-parse", "rev-parse", "rev-parse", "rev-parse", "merge-base", "cat-file", "cat-file",
                  "cat-file", "cat-file", "cat-file", "cat-file", "cat-file", "cat-file", "rev-list"]
GLOBAL_OPTIONS = ["-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                  "--no-replace-objects", "-C"]


def _self_test():
    with tempfile.TemporaryDirectory(prefix="hook-install-selftest-") as tmp, _hermetic_env(tmp):
        repo, ids = _build(tmp)
        _vectors(tmp, repo, ids)
    if FAILURES:
        print("SELF-TEST FAIL: check_hook_install {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_hook_install {} checks (clean controls match at C and at tag vA, reported as a "
          "tag not attested; vectors 1 to 12 and each flip: CRLF drift, identity never by release-version, "
          "root and row checks, a merge-created match on the first-parent line, exact staleness counts, a "
          "shallow clone without a match, FIFO, directory and dangling link refused without blocking, the size "
          "cap one under, at and over, malformed and deeply nested TOML, attested rows, a failing stand-in git "
          "with its call log; replacement refs ignored; the exit codes are 0, 1 and 2)".format(len(EXECUTED)))
    return 0


def _vectors(tmp, repo, ids):
    v3 = _installed(tmp, V3, "v3.py")
    v1 = _installed(tmp, V1, "v1.py")
    v2m = _installed(tmp, V2M, "v2m.py")

    # Clean controls.
    code, rep = _case(v3, repo, "main", ids["C"])
    check("clean/match-at-c", code == 0 and rep["verdict"] == "MATCH" and rep["target"]["root-verified"]
          and rep["stale"]["changes-behind"] == 0, rep)
    code, rep = _case(v1, repo, "main", "vA")
    check("clean/match-at-tag", code == 0 and rep["tags"] == [{"tag": "vA", "commit": ids["A"],
                                                              "label": "tag (not attested)"}], rep)
    check("clean/report-limits", "not tamper" in rep["limits"] and "not a check that the hook behaves" in rep["limits"],
          rep["limits"])

    # 1: a CRLF copy of C is drift, never equal through a text read.
    crlf = _installed(tmp, V3.replace(b"\n", b"\r\n"), "crlf.py")
    code, rep = _case(crlf, repo, "main", ids["C"])
    check("v1/crlf-drift", code == 1 and rep["verdict"] == "DRIFT", rep)
    real_read = _read_installed

    def _text_read(path):
        facts, data = real_read(path)
        data = data.replace(b"\r\n", b"\n")
        facts.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return facts, data
    with _patched("_read_installed", _text_read):
        code, _rep = _case(crlf, repo, "main", ids["C"])
    check("v1/crlf-drift-flip", code != 1, code)

    # 2: a copy of A against target C is drift that names A; release-version never identifies it.
    code, rep = _case(v1, repo, "main", ids["C"])
    check("v2/older-copy-drift", code == 1 and [m["commit"] for m in rep["matches"] if m["path"] == MEMBER]
          == [ids["A"]], rep)
    with _patched("_target_verdict", lambda installed, target: target["release-version"] == "1.0.5"):
        code, _rep = _case(v1, repo, "main", ids["C"])
    check("v2/older-copy-drift-flip", code != 1, code)

    # 3: a manifest edited by one byte at the target fails the root check.
    code, rep = _case(v3, repo, "bad-root", "bad-root")
    check("v3/root-mismatch", code == 2 and "differs from the root" in " ".join(rep["reasons"]), rep)
    with _patched("_check_root", lambda manifest, root: None):
        code, _rep = _case(v3, repo, "bad-root", "bad-root")
    check("v3/root-mismatch-flip", code != 2, code)

    # 4: the target's manifest row differs from its blob.
    code, rep = _case(v3, repo, "bad-row", "bad-row")
    check("v4/row-mismatch", code == 2 and "differs from its blob" in " ".join(rep["reasons"]), rep)
    with _patched("_check_row", lambda row, member: None):
        code, _rep = _case(v3, repo, "bad-row", "bad-row")
    check("v4/row-mismatch-flip", code != 2, code)

    # 5: bytes a merge resolution created are matched on the first-parent line.
    code, rep = _case(v2m, repo, "main", ids["C"])
    check("v5/merge-created-match", code == 1 and {"commit": ids["M"], "first-parent": True} in
          [{"commit": m["commit"], "first-parent": m["first-parent"]} for m in rep["matches"]], rep)
    v2 = _installed(tmp, V2, "v2.py")
    code, rep = _case(v2, repo, "main", ids["C"])
    check("v5/side-branch-match", code == 1 and [(m["commit"], m["first-parent"]) for m in rep["matches"]
                                                 if m["path"] == MEMBER] == [(ids["B"], False)]
          and rep["stale"] is None, rep)
    with _patched("_scanned", lambda row: len(row["parents"]) < 2):
        code, rep = _case(v2m, repo, "main", ids["C"])
    check("v5/merge-created-match-flip", not any(m["commit"] == ids["M"] for m in rep["matches"]), rep)

    # 6: staleness counts, a copy carried just before a change and one at the change.
    counts = {}
    for name, path in (("A", v1), ("M", v2m), ("C", v3)):
        _code, rep = _case(path, repo, "main", ids["C"])
        counts[name] = (rep["stale"]["changes-behind"], rep["stale"]["superseded-by"])
    check("v6/stale-counts", counts == {"A": (2, ids["M"]), "M": (1, ids["C"]), "C": (0, None)}, counts)
    check("v6/carried-before-change", ids["X"] not in [m["commit"] for m in _case(v1, repo, "main", ids["C"])[1]
                                                        ["matches"]], ids)
    real_identify = _identify

    def _off_by_one(*args):
        matches, stale, tags, examined = real_identify(*args)
        if stale is not None:
            stale = dict(stale, **{"changes-behind": stale["changes-behind"] + 1})
        return matches, stale, tags, examined
    with _patched("_identify", _off_by_one):
        _code, rep = _case(v1, repo, "main", ids["C"])
    check("v6/stale-counts-flip", rep["stale"]["changes-behind"] != 2, rep["stale"])

    # 7: a shallow clone with no match cannot prove there is none; with a match it still evaluates.
    shallow = Path(tmp) / "shallow"
    _fx(tmp, "clone", "-q", "--depth", "1", "--no-local", "file://" + str(repo), str(shallow))
    unknown = _installed(tmp, UNKNOWN, "unknown.py")
    code, rep = _case(unknown, shallow, "HEAD", "HEAD")
    check("v7/shallow-no-match", code == 2 and "shallow" in " ".join(rep["reasons"]), rep)
    code, rep = _case(v3, shallow, "HEAD", "HEAD")
    check("v7/shallow-match", code == 0, rep)
    code, rep = _case(unknown, repo, "main", ids["C"])
    check("v7/full-no-match-drift", code == 1 and "no match among the commits examined" in " ".join(rep["reasons"]),
          rep)
    with _patched("_is_shallow", lambda repo: False):
        code, _rep = _case(unknown, shallow, "HEAD", "HEAD")
    check("v7/shallow-no-match-flip", code != 2, code)

    # 8: a FIFO (a child run with a timeout), a directory and a dangling link are refused without blocking.
    fifo = Path(tmp) / "fifo.py"
    os.mkfifo(fifo)
    code, out = _child_run(["--installed", str(fifo), "--repo", str(repo), "--ref", "main", "--target", "main"])
    check("v8/fifo-refused", code == 2 and "not a regular file" in out, (code, out[-400:]))
    code, rep = _case(Path(tmp), repo, "main", "main")
    check("v8/directory-refused", code == 2 and "not a regular file" in " ".join(rep["reasons"]), rep)
    dangling = Path(tmp) / "dangling.py"
    os.symlink(Path(tmp) / "absent.py", dangling)
    code, rep = _case(dangling, repo, "main", "main")
    check("v8/dangling-link-refused", code == 2 and "dangling" in " ".join(rep["reasons"]), rep)
    linked = Path(tmp) / "linked.py"
    os.symlink(v3, linked)
    code, rep = _case(linked, repo, "main", ids["C"])
    check("v8/link-followed-and-reported", code == 0 and rep["installed"]["symlink"]
          and rep["installed"]["realpath"] == os.path.realpath(v3), rep["installed"])
    done = []

    def _open_first():
        with open(fifo, "rb") as handle:
            done.append(handle.read())
    worker = threading.Thread(target=_open_first, daemon=True)
    worker.start()
    worker.join(2)
    blocked = worker.is_alive()
    if blocked:
        os.close(os.open(fifo, os.O_WRONLY))
        worker.join(10)
    check("v8/fifo-refused-flip", blocked, done)

    # 9: the size cap one under, exactly at and one over.
    sizes = {}
    for name, size in (("under", MAX_INSTALLED_BYTES - 1), ("at", MAX_INSTALLED_BYTES),
                       ("over", MAX_INSTALLED_BYTES + 1)):
        path = Path(tmp) / ("size-" + name)
        with open(path, "wb") as handle:
            handle.truncate(size)
        code, rep = _case(path, repo, "main", ids["C"])
        sizes[name] = code
        os.unlink(path)
    check("v9/size-cap-ends", sizes == {"under": 1, "at": 1, "over": 2}, sizes)
    with _patched("_over_cap", lambda size, cap: size >= cap):
        path = _installed(tmp, b"", "size-at")
        with open(path, "wb") as handle:
            handle.truncate(MAX_INSTALLED_BYTES)
        code, _rep = _case(path, repo, "main", ids["C"])
        os.unlink(path)
    check("v9/size-cap-ends-flip", code == 2, code)

    # 10: invalid and deeply nested TOML at the target, and injected parser resource errors, are
    # cannot-evaluate without a traceback.
    for name, ref in (("v10/invalid-toml", "bad-toml"), ("v10/deep-toml", "deep-toml")):
        code, rep = _case(v3, repo, ref, ref)
        check(name, code == 2 and "not valid TOML" in " ".join(rep["reasons"]), rep)
    for name, exc_type in (("v10/recursion-error", RecursionError), ("v10/memory-error", MemoryError)):
        original_loads = tomllib.loads

        def _raise(text, _exc_type=exc_type, _loads=original_loads):
            if "[[sources]]" not in text:
                return _loads(text)
            raise _exc_type("raised by the self-test")
        tomllib.loads = _raise
        try:
            code, rep = _case(v3, repo, "main", ids["C"])
        finally:
            tomllib.loads = original_loads
        check(name, code == 2 and exc_type.__name__ in " ".join(rep["reasons"]), rep)
    with _patched("_parse_toml", lambda data, where: tomllib.loads(data.decode("utf-8"))):
        try:
            _case(v3, repo, "bad-toml", "bad-toml")
            raised = False
        except ValueError:
            raised = True
    check("v10/invalid-toml-flip", raised, raised)

    # 11: an attested row in releases.toml at --ref is not evaluated by this version.
    code, rep = _case(v3, repo, "attested", ids["C"])
    check("v11/attested-rows", code == 2 and "not evaluated by this version" in " ".join(rep["reasons"]), rep)
    with _patched("_attested_rows", lambda doc: []):
        code, _rep = _case(v3, repo, "attested", ids["C"])
    check("v11/attested-rows-flip", code != 2, code)

    # 12: a stand-in git on a replaced PATH logs its calls and refuses the history read.
    bindir = Path(tmp) / "bin"
    os.makedirs(bindir)
    log = Path(tmp) / "git-calls.log"
    real_git = shutil.which("git")
    _put(bindir, "git", STAND_IN.format(python=sys.executable, log=str(log), real=real_git).encode("utf-8"))
    os.chmod(bindir / "git", 0o755)
    env = dict(os.environ, PATH=str(bindir))
    code, out = _child_run(["--installed", str(v3), "--repo", str(repo), "--ref", "main", "--target", ids["C"]],
                           env=env)
    with open(log, encoding="utf-8") as handle:
        calls = [json.loads(line) for line in handle.read().splitlines()]
    prefix = GLOBAL_OPTIONS + [str(repo)]
    check("v12/stand-in-git-fails", code == 2 and "rev-list" in out, (code, out[-400:]))
    check("v12/stand-in-call-log", all(call[:len(prefix)] == prefix for call in calls)
          and [call[len(prefix)] for call in calls] == EXPECTED_CALLS, calls)
    real_commits = _commits

    def _failure_as_empty(*args):
        try:
            return real_commits(*args)
        except GitFailure:
            return []
    with _patched("_commits", _failure_as_empty):
        saved_path = os.environ["PATH"]
        os.environ["PATH"] = str(bindir)
        try:
            code, _rep = _case(v3, repo, "main", ids["C"])
        finally:
            os.environ["PATH"] = saved_path
    check("v12/stand-in-git-fails-flip", code != 2, code)

    # Replacement refs are ignored: a replace ref that maps v3's blob to v1's changes a plain read, not this one.
    blob_v3 = _fx(repo, "rev-parse", ids["C"] + ":" + MEMBER)
    blob_v1 = _fx(repo, "rev-parse", ids["A"] + ":" + MEMBER)
    _fx(repo, "replace", blob_v3, blob_v1)
    plain = _fx(repo, "cat-file", "blob", ids["C"] + ":" + MEMBER)
    code, rep = _case(v3, repo, "main", ids["C"])
    check("replace/ignored", plain.encode("utf-8") == V1.strip() and code == 0, (plain, rep))

    # Arguments: a missing or repeated flag, and a revision that looks like an option, fail closed.
    with contextlib.redirect_stderr(io.StringIO()):
        codes = [run([]), run(["--installed", str(v3)]), run(["--installed", str(v3)] * 2)]
    code, rep = _case(v3, repo, "--all", ids["C"])
    check("exit/codes-0-1-2", codes == [2, 2, 2] and code == 2 and sizes["at"] == 1, (codes, rep))


def main(argv=None):
    if argv is None:
        # The process entry, ended through the execution-report finalizer when the self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    if argv and argv[0] in ("--self-test", "--execution-report"):
        rest = argv[1:] if argv[0] == "--self-test" else list(argv)
        if rest and not (len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1])):
            print("error: unrecognized arguments {!r}; fail closed".format(rest), file=sys.stderr)
            return 2
        if rest:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(rest[1], SUITE_ID, EXECUTED)
        return _self_test()
    return run(argv)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This tool reads the exit status of the git
# it runs, so under an ignored SIGCHLD it would read a failing git as a success; it refuses at its entry
# line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("check_hook_install.py") or main())
