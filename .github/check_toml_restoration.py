#!/usr/bin/env python3
"""Private, read-only TOML restoration checker for the gated apply chain (first draft slice).

The checker compares the staged index against the pinned HEAD and reports whether any changed
.toml key path returns to a value that recorded history moved away from. It reads index blobs,
never the working tree, and it takes the pinned HEAD and protected base as explicit arguments:
a missing or unresolvable base is cannot-evaluate, never a guessed fallback.

Usage:
  check_toml_restoration.py --repo <absolute path> --head <full commit ID> --base <full commit ID>
  check_toml_restoration.py --self-test

Each checking option is required exactly once. --head and --base are full lowercase hexadecimal
object IDs of the repository's object-format length (sha1: 40; sha256: 64), never ref names or
abbreviations; --head must equal the repository's HEAD commit, and --base must name a present
commit object. -h, --help, an option prefix, a repeated option, a positional argument and any
other argument are usage errors (exit 2). --self-test is accepted only as the sole argument.

Exit codes:
  0  no restoration was found within the declared coverage
  1  a restoration was found and needs a maintainer decision
  2  the checker could not evaluate, an availability restriction refused the input, or the
     arguments were unusable; exit 2 takes precedence over exit 1 and cannot be overridden

Report: stdout carries one machine-readable report whose first line states its kind:
  report-kind: result          then zero or more "finding: ..." lines, zero or more "gap: ..."
                               lines, and one "findings-set-digest: <sha256 hex>" line (the
                               digest of the sorted finding lines joined by LF)
  report-kind: refusal         then one or more "refusal: ..." lines naming the refused input
                               (the snapshot or endpoint, the path and the error class where a
                               parse was refused)
  report-kind: internal-error  then one "internal-error: <class>: ..." line
The one exit-2 path with no stdout report is the child-status guard's refusal at the entry
line, which writes a single stderr line and exits before main() runs.

Every git command runs with -C <repo> and --no-optional-locks, in a child environment with
every inherited GIT_* variable removed and GIT_OPTIONAL_LOCKS=0 set, so no read refreshes the
index or takes its lock. The checker never writes the index and never runs write-tree.

Parse errors are typed at each parse site: ValueError (TOMLDecodeError and UnicodeDecodeError
included) means the snapshot does not parse (a bridged gap in history, a refusal at an
endpoint); RecursionError and MemoryError mean the snapshot could not be evaluated and refuse
the run at any snapshot, history included, because they depend on the host and a bridged gap
could hide a departure.

Scope of this first draft slice: the checker core only, that is the history rules (the boundary
on HEAD's first-parent chain, the availability restriction over that whole chain down to the
root, ordinary-commit and merge attribution, gap bridging, the endpoint parses and the 1,000
touching-commit cap), the canonical value encoding, the exact and occurrence restoration rules
for scalars and arrays (arrays of tables as whole-encoding occurrence elements), the exit-order
rule, and the scope rules for changed .toml paths, unresolved index entries and literal key-path
tuples. No apply script, unit check log, decision handling or orchestration rule is part of this
slice.

Known limits that apply to this core (fixed design choices, not defects):
  - Only paths ending in .toml are covered; a rename counts as a deletion plus an addition, so
    moves across files or keys are not followed (--no-renames is passed explicitly).
  - Values that mean the same thing but are spelled differently count as different values (an
    offset date-time for the same instant written with another offset among them); NaN spellings
    are normalized to one encoding.
  - A candidate that restores one field inside an array-of-tables element while changing another
    field of it is not flagged; order-only partial restorations are not flagged either.
  - A merge resolution that chooses an identical imported value cannot be told apart from a
    deliberate choice, and a bridged gap that spans an update merge counts as branch history.
  - Changes made and undone entirely inside unparseable snapshots are not seen, and a boundary
    snapshot that does not parse leaves the starting values unknown until the first snapshot
    that parses; every bridged gap is reported.
  - The encoding sees only what tomllib returns, so parser precision bounds value identity.
  - If HEAD's version of a changed file does not parse, every candidate touching it exits 2;
    no repair route through the apply chain is defined.
  - With an unresolved boundary, every changed .toml path that any first-parent commit touches
    (the root included, compared with the empty tree) exits 2.
  - Shallow history, or a missing commit or tree object anywhere on HEAD's first-parent chain
    down to its root, exits 2 whenever any changed .toml path exists, even for objects below a
    resolved boundary; more than 1,000 distinct touching first-parent commits in the window,
    counted once over the union of evaluated paths, exits 2; neither first-parent walk is
    capped and their cost is unmeasured.

--self-test builds hermetic git repositories under a temporary directory it creates and removes,
runs the checker over them in child processes, and pairs each covered row with a mutant copy
(one source region replaced, loaded through importlib) whose verdict must change. Its expected
check ids are the module-level literal tuple _EXPECTED_CHECK_IDS, compared as an exact set with
the executed ids at the end of each run (a disclosed deviation from the registry, which cannot
hold a .github runner's ids).
"""
import sys

HISTORY_CAP = 1000

MISSING = object()   # a missing key, or a missing file, has a distinct value meaning "missing"
GAP = object()       # a historical snapshot that does not parse (ValueError)
_UNKNOWN_PARENT = object()


class GitError(Exception):
    """The repository cannot be read at all (cannot evaluate)."""


class Unavailable(Exception):
    """A needed git object is missing (the availability restriction refuses the input)."""


class Refused(Exception):
    """The input is refused: the report is a refusal naming what could not be used."""


class _Cannot:
    """A snapshot that could not be evaluated (RecursionError or MemoryError at the parse)."""

    def __init__(self, error_class):
        self.error_class = error_class


def _bind_modules():
    """Bind every module the checker needs other than sys as a module global. This runs inside
    main()'s one try, so a failed import is an internal error (exit 2), never a bare traceback
    read as a finding."""
    global datetime, hashlib, importlib, math, os, signal, subprocess, tempfile, time, tomllib
    import datetime
    import hashlib
    import importlib.util
    import math
    import os
    import signal
    import subprocess
    import tempfile
    import time
    import tomllib


# ---- git plumbing ----------------------------------------------------------------------------


def _git_env():
    """A child environment with every inherited GIT_* variable removed and optional locks off,
    so the index and objects read are the --repo repository's own and no read takes a lock."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(repo, args, input_bytes=None):
    return subprocess.run(["git", "-C", repo, "--no-optional-locks"] + list(args),
                          input=input_bytes, capture_output=True, timeout=600, env=_git_env())


def _rev_parse(repo, ref):
    probe = _git(repo, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    if probe.returncode != 0:
        return None
    return probe.stdout.strip().decode("ascii", "replace")


class _ObjectReader:
    """One persistent `git cat-file --batch` child; commit and tree bodies are cached."""

    def __init__(self, repo, hash_bytes):
        self.hash_bytes = hash_bytes
        self._proc = subprocess.Popen(["git", "-C", repo, "--no-optional-locks",
                                       "cat-file", "--batch"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, env=_git_env())
        self._cache = {}

    def get(self, sha):
        if sha in self._cache:
            return self._cache[sha]
        stdin = self._proc.stdin
        stdout = self._proc.stdout
        try:
            stdin.write(sha.encode("ascii") + b"\n")
            stdin.flush()
            header = stdout.readline()
        except (OSError, ValueError) as exc:
            raise GitError("git cat-file --batch is not answering: {0!r}".format(exc))
        if not header:
            raise GitError("git cat-file --batch ended unexpectedly")
        fields = header.decode("ascii", "replace").split()
        if len(fields) == 2 and fields[1] == "missing":
            return None
        if len(fields) != 3:
            raise GitError("unexpected cat-file header: {0!r}".format(header))
        size = int(fields[2])
        body = b""
        while len(body) < size:
            piece = stdout.read(size - len(body))
            if not piece:
                raise GitError("git cat-file --batch ended mid-object")
            body += piece
        stdout.read(1)
        result = (fields[1], body)
        if fields[1] in ("commit", "tree"):
            self._cache[sha] = result
        return result

    def close(self):
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=60)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self._proc.kill()
            try:
                self._proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                pass


def _parse_tree(body, hash_bytes):
    entries = {}
    offset = 0
    while offset < len(body):
        space = body.index(b" ", offset)
        nul = body.index(b"\x00", space)
        mode = body[offset:space].decode("ascii")
        name = body[space + 1:nul].decode("utf-8", "replace")
        sha = body[nul + 1:nul + 1 + hash_bytes].hex()
        entries[name] = (mode, sha)
        offset = nul + 1 + hash_bytes
    return entries


def read_commit(reader, sha):
    got = reader.get(sha)
    if got is None or got[0] != "commit":
        return None
    header = got[1].split(b"\n\n", 1)[0].decode("utf-8", "replace")
    tree = None
    parents = []
    for line in header.splitlines():
        if line.startswith("tree "):
            tree = line[5:].strip()
        elif line.startswith("parent "):
            parents.append(line[7:].strip())
    if tree is None:
        return None
    return tree, parents


def path_blob(reader, tree_sha, path):
    """The blob id of path inside the tree, or None where the path is absent."""
    sha = tree_sha
    parts = path.split("/")
    for position, part in enumerate(parts):
        got = reader.get(sha)
        if got is None:
            raise Unavailable("tree object missing: " + sha)
        if got[0] != "tree":
            return None
        hit = _parse_tree(got[1], reader.hash_bytes).get(part)
        if hit is None:
            return None
        mode, sha = hit
        if position < len(parts) - 1 and mode != "40000":
            return None
    if mode == "40000":
        return None
    return sha


def blob_content(reader, blob_sha):
    got = reader.get(blob_sha)
    if got is None:
        raise Unavailable("blob object missing: " + blob_sha)
    if got[0] != "blob":
        raise Unavailable("object {0} is not a blob ({1})".format(blob_sha, got[0]))
    return got[1]


# ---- chain analysis (H1): the first-parent chain, touches and object availability -------------


def _tree_problems(reader, tree_sha, seen):
    problems = []
    stack = [tree_sha]
    while stack:
        sha = stack.pop()
        if sha in seen:
            continue
        seen.add(sha)
        got = reader.get(sha)
        if got is None:
            problems.append("tree object missing: " + sha)
            continue
        if got[0] != "tree":
            problems.append("object {0} is not a tree ({1})".format(sha, got[0]))
            continue
        for mode, child in _parse_tree(got[1], reader.hash_bytes).values():
            if mode == "40000":
                stack.append(child)
    return problems


def commit_touches(reader, entry, parent_entry, paths):
    """The changed paths this commit touches: its blob differs from the first parent's blob."""
    touches = set()
    for path in paths:
        try:
            own_blob = path_blob(reader, entry["tree"], path)
        except Unavailable:
            continue  # the object problem is recorded by the availability scan
        if parent_entry is _UNKNOWN_PARENT:
            continue  # the first parent's commit object is missing; recorded by the chain scan
        if parent_entry is None:
            parent_blob = None  # a root commit is compared with the empty tree
        else:
            try:
                parent_blob = path_blob(reader, parent_entry["tree"], path)
            except Unavailable:
                continue
        if own_blob != parent_blob:
            touches.add(path)
    return touches


def analyze_chain(reader, head_sha, paths):
    """HEAD's whole first-parent chain down to its root, per-commit touches of the changed
    paths, and every commit or tree object problem as (chain position, text)."""
    chain = []
    problems = []
    sha = head_sha
    while sha is not None:
        parsed = read_commit(reader, sha)
        if parsed is None:
            problems.append((len(chain), "commit object missing on the first-parent chain: " + sha))
            break
        chain.append({"sha": sha, "tree": parsed[0], "parents": parsed[1]})
        sha = parsed[1][0] if parsed[1] else None
    seen_trees = set()
    for position, entry in enumerate(chain):
        for text in _tree_problems(reader, entry["tree"], seen_trees):
            problems.append((position, "commit {0}: {1}".format(entry["sha"], text)))
    touched = {}
    for position, entry in enumerate(chain):
        if position + 1 < len(chain):
            parent_entry = chain[position + 1]
        elif entry["parents"]:
            parent_entry = _UNKNOWN_PARENT
        else:
            parent_entry = None
        touched[entry["sha"]] = commit_touches(reader, entry, parent_entry, paths)
    return chain, touched, problems


def _boundary_index(repo, chain, base_sha):
    """The chain position of the nearest first-parent commit that is an ancestor of the
    protected base, or None when no such commit exists."""
    listed = _git(repo, ["rev-list", base_sha])
    if listed.returncode == 0:
        ancestors = set(listed.stdout.decode("ascii", "replace").split())
        for index, entry in enumerate(chain):
            if entry["sha"] == base_sha or entry["sha"] in ancestors:
                return index
        return None  # no commit on the first-parent chain is an ancestor of the protected base
    # The base's own history cannot be listed (an object below it may be missing), so ask per
    # commit and treat an undecidable ancestry as "not an ancestor": an unresolved boundary
    # fails closed with exit 2.
    for index, entry in enumerate(chain):
        if entry["sha"] == base_sha:
            return index
        probe = _git(repo, ["merge-base", "--is-ancestor", entry["sha"], base_sha])
        if probe.returncode == 0:
            return index
    return None  # the fallback found no ancestor on the chain either


# ---- value identity (V1, S3): canonical encoding and key-path flattening ----------------------


def canon(value):
    """One versioned canonical encoding (toml-restore-canon/1) used for value equality."""
    if value is MISSING:
        return "missing"
    if isinstance(value, bool):
        return "b:true" if value else "b:false"
    if isinstance(value, int):
        return "i:{0}".format(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "f:nan"  # every NaN spelling has one encoding
        if math.isinf(value):
            return "f:inf" if value > 0 else "f:-inf"
        return "f:" + value.hex()  # exact bit value, so -0.0 and 0.0 differ
    if isinstance(value, str):
        return "s:{0}:{1}".format(len(value), value)
    if isinstance(value, datetime.datetime):
        # an offset date-time keeps its local fields and its offset, so the same instant
        # written with a different offset is a different value
        tag = "odt" if value.tzinfo is not None else "ldt"
        return tag + ":" + value.isoformat()
    if isinstance(value, datetime.date):
        return "ld:" + value.isoformat()
    if isinstance(value, datetime.time):
        return "lt:" + value.isoformat()
    if isinstance(value, list):
        return "a:[" + ",".join(canon(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = sorted("{0}:{1}={2}".format(len(key), key, canon(item))
                       for key, item in value.items())
        return "t:{" + ",".join(parts) + "}"
    raise GitError("unsupported TOML value type: " + type(value).__name__)


def flatten(doc):
    """Leaves by key-path tuple: scalars, whole arrays and empty tables are leaves."""
    out = {}
    stack = [((key,), value) for key, value in doc.items()]
    while stack:
        key_path, value = stack.pop()
        if isinstance(value, dict) and value:
            for key, child in value.items():
                stack.append((key_path + (key,), child))
        else:
            out[key_path] = value
    return out


def parse_snapshot(blob_bytes):
    """A parsed document, {} for a missing file (a snapshot that parses, every key missing),
    GAP where the bytes do not parse as UTF-8 TOML (ValueError, TOMLDecodeError and
    UnicodeDecodeError included), or a _Cannot naming the error class where the parse could
    not be evaluated on this host (RecursionError, MemoryError). Each class is handled at
    this one parse site and never re-raised."""
    if blob_bytes is None:
        return {}
    try:
        text = blob_bytes.decode("utf-8")
        return tomllib.loads(text)
    except ValueError:
        # the snapshot does not parse: a bridged gap in history, a refusal at an endpoint
        return GAP
    except RecursionError:
        return _Cannot("RecursionError")
    except MemoryError:
        return _Cannot("MemoryError")


def _key_text(key_path):
    return "(" + ", ".join(repr(part) for part in key_path) + ")"


def _element_encodings(value):
    """R2 elements: a missing or non-array value counts as the empty array."""
    if isinstance(value, list):
        return [canon(item) for item in value]
    return []


def _counts(items):
    counts = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


# ---- history evaluation (H3-H5, R1, R2) --------------------------------------------------------


def _record_departure(departed, removed_pairs, key, old_value, new_value, old_enc):
    departed.setdefault(key, set()).add(old_enc)
    old_items = _element_encodings(old_value)
    if not old_items:
        return
    new_counts = _counts(_element_encodings(new_value))
    old_counts = _counts(old_items)
    for enc, old_n in old_counts.items():
        new_n = new_counts.get(enc, 0)
        for rank in range(new_n + 1, old_n + 1):
            removed_pairs.setdefault(key, set()).add((enc, rank))


def _record_commit_transitions(reader, entry, parent_doc, own_doc, path,
                               departed, removed_pairs, provenance, snapshot):
    other_flats = []
    if len(entry["parents"]) > 1:
        for parent_sha in entry["parents"][1:]:
            parsed = read_commit(reader, parent_sha)
            if parsed is None:
                raise Unavailable("commit object missing for merge parent " + parent_sha)
            other_doc = snapshot(path_blob(reader, parsed[0], path),
                                 "merge parent {0}".format(parent_sha))
            if other_doc is not GAP:
                other_flats.append(flatten(other_doc))
    parent_flat = flatten(parent_doc)
    own_flat = flatten(own_doc)
    for key in set(parent_flat) | set(own_flat):
        old_value = parent_flat.get(key, MISSING)
        new_value = own_flat.get(key, MISSING)
        old_enc = canon(old_value)
        new_enc = canon(new_value)
        if old_enc == new_enc:
            continue
        if other_flats:
            imported = any(canon(flat.get(key, MISSING)) == new_enc for flat in other_flats)
            if imported:
                # the merge imports the value; a departure is recorded only when the
                # first-parent value was set by a branch commit
                if provenance.get(key) == "branch":
                    _record_departure(departed, removed_pairs, key, old_value, new_value, old_enc)
                provenance[key] = "imported"
                continue
        _record_departure(departed, removed_pairs, key, old_value, new_value, old_enc)
        provenance[key] = "branch"


def evaluate_history(reader, chain, boundary_index, path, touch_positions, kept_shas,
                     doc_cache, gap_notes):
    """Departed value encodings and removed (element, rank) pairs per key, walking the
    window's touching commits oldest to newest; each commit is compared with its own first
    parent's snapshot, bridging unparseable snapshots back toward the boundary. A snapshot
    that could not be evaluated (RecursionError, MemoryError) refuses the run (Refused)."""
    departed = {}
    removed_pairs = {}
    provenance = {}
    boundary_blob = path_blob(reader, chain[boundary_index]["tree"], path)
    touch_blobs = [path_blob(reader, chain[idx]["tree"], path) for idx in touch_positions]

    def snapshot(blob_sha, context):
        if blob_sha is None:
            return {}
        if blob_sha not in doc_cache:
            doc_cache[blob_sha] = parse_snapshot(blob_content(reader, blob_sha))
        doc = doc_cache[blob_sha]
        if isinstance(doc, _Cannot):
            raise Refused("{0} at {1}: the snapshot could not be evaluated "
                          "({2})".format(path, context, doc.error_class))
        return doc

    for list_pos, idx in enumerate(touch_positions):
        entry = chain[idx]
        if entry["sha"] not in kept_shas:
            continue  # outside the evaluated commit set
        own_doc = snapshot(touch_blobs[list_pos], "commit " + entry["sha"])
        if own_doc is GAP:
            continue  # an unparseable snapshot is a gap; the next parsing snapshot bridges it
        parent_doc = None
        gap_count = 0
        back = list_pos - 1
        while True:
            if back >= 0:
                blob = touch_blobs[back]
                context = "commit " + chain[touch_positions[back]]["sha"]
            else:
                blob = boundary_blob
                context = "the boundary commit " + chain[boundary_index]["sha"]
            doc = snapshot(blob, context)
            if doc is not GAP:
                parent_doc = doc
                break
            gap_count += 1
            if back < 0:
                # the boundary snapshot does not parse: the starting values are unknown
                # until the first snapshot that parses, so no transition is recorded here
                break
            back -= 1
        if gap_count:
            gap_notes.append("{0}: {1} unparseable snapshot(s) bridged before commit {2}".format(
                path, gap_count, entry["sha"]))
        if parent_doc is None:
            continue
        _record_commit_transitions(reader, entry, parent_doc, own_doc, path,
                                   departed, removed_pairs, provenance, snapshot)
    return departed, removed_pairs


def findings_for(path, head_doc, cand_doc, departed, removed_pairs):
    """R1 and R2 findings for the key paths the candidate changes relative to HEAD."""
    findings = []
    head_flat = flatten(head_doc)
    cand_flat = flatten(cand_doc)
    for key in sorted(set(head_flat) | set(cand_flat)):
        head_value = head_flat.get(key, MISSING)
        cand_value = cand_flat.get(key, MISSING)
        head_enc = canon(head_value)
        cand_enc = canon(cand_value)
        if cand_enc == head_enc:
            continue  # only key paths the candidate changes are evaluated
        departed_encs = departed.get(key, set())
        if cand_enc in departed_encs:
            findings.append("R1 restoration in {0}: key {1} returns to a value a recorded "
                            "transition moved away from".format(path, _key_text(key)))
        removed = removed_pairs.get(key, set())
        if removed and isinstance(cand_value, list):
            head_counts = _counts(_element_encodings(head_value))
            cand_counts = _counts(_element_encodings(cand_value))
            for enc, cand_n in sorted(cand_counts.items()):
                head_n = head_counts.get(enc, 0)
                for rank in range(head_n + 1, cand_n + 1):
                    if (enc, rank) in removed:
                        findings.append("R2 restoration in {0}: key {1} re-adds a removed array "
                                        "element occurrence (rank {2})".format(
                                            path, _key_text(key), rank))
    return findings


# ---- the check (P1, P3, P4, S1, S2, E1) --------------------------------------------------------


def _print_report(kind, lines):
    """The machine-readable stdout report (one per run): the kind line, then the content."""
    print("report-kind: " + kind)
    for line in lines:
        print(line)


def _findings_digest(findings):
    """The findings-set digest: sha256 over the sorted finding lines joined by LF."""
    return hashlib.sha256("\n".join(sorted(findings)).encode("utf-8")).hexdigest()


def _read_index(repo):
    listed = _git(repo, ["ls-files", "-s", "-z"])
    if listed.returncode != 0:
        raise GitError("cannot read the index: " + listed.stderr.decode("utf-8", "replace"))
    stage_map = {}
    conflicts = set()
    for record in listed.stdout.split(b"\x00"):
        if not record:
            continue
        meta, _tab, path_bytes = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split()
        path = path_bytes.decode("utf-8", "replace")
        if len(fields) != 3:
            raise GitError("unreadable index record: {0!r}".format(record))
        if fields[2] != "0":
            conflicts.add(path)
        else:
            stage_map[path] = fields[1]
    return stage_map, conflicts


def _changed_toml_paths(repo, head_sha):
    diffed = _git(repo, ["diff-index", "--cached", "--name-only", "-z", "--no-renames",
                         head_sha])
    if diffed.returncode != 0:
        raise GitError("cannot diff the index against HEAD: "
                       + diffed.stderr.decode("utf-8", "replace"))
    return {path.decode("utf-8", "replace") for path in diffed.stdout.split(b"\x00")
            if path and path.endswith(b".toml")}


def run_check(repo, head, base, hash_bytes):
    """The whole check; returns the exit code (0, 1 or 2) and prints the stdout report."""
    try:
        return _check(repo, head, base, hash_bytes)
    except Refused as exc:
        print("error: toml-restoration checker: refused: {0}".format(exc), file=sys.stderr)
        _print_report("refusal", ["refusal: " + str(exc)])
        return 2
    except GitError as exc:
        print("error: toml-restoration checker: {0}".format(exc), file=sys.stderr)
        _print_report("refusal", ["refusal: the repository cannot be read: {0}".format(exc)])
        return 2


def _check(repo, head, base, hash_bytes):
    problems = []
    findings = []
    gap_notes = []
    head_sha = _rev_parse(repo, "HEAD")
    if head_sha is None:
        raise Refused("HEAD cannot be resolved to a commit in {0!r}".format(repo))
    if head_sha != head:
        raise Refused("--head {0} does not equal the repository's HEAD commit {1}".format(
            head, head_sha))
    typed = _git(repo, ["cat-file", "-t", base])
    if typed.returncode != 0 or typed.stdout.strip() != b"commit":
        raise Refused("the protected base {0} is not a present commit object; no fallback "
                      "is guessed".format(base))
    base_sha = base
    stage_map, conflicts = _read_index(repo)
    for path in sorted(conflicts):
        problems.append("unresolved index entry (stage above 0): " + path)
    changed = _changed_toml_paths(repo, head_sha)
    if not changed:
        if problems:
            for text in problems:
                print("error: toml-restoration checker: " + text, file=sys.stderr)
            _print_report("refusal", ["refusal: " + text for text in problems])
            return 2
        print("PASS: toml-restoration checker: no changed .toml path between HEAD and the "
              "index", file=sys.stderr)
        _print_report("result", ["findings-set-digest: " + _findings_digest([])])
        return 0
    shallow_probe = _git(repo, ["rev-parse", "--is-shallow-repository"])
    if shallow_probe.returncode != 0:
        problems.append("cannot determine whether the repository history is shallow")
    shallow_repo = shallow_probe.returncode == 0 and shallow_probe.stdout.strip() == b"true"
    if shallow_repo:
        problems.append("history availability: the repository history is shallow")
    reader = _ObjectReader(repo, hash_bytes)
    try:
        parsed_head = read_commit(reader, head_sha)
        if parsed_head is None:
            raise Refused("the HEAD commit object cannot be read")
        head_tree = parsed_head[0]
        chain, touched, chain_problems = analyze_chain(reader, head_sha, sorted(changed))
        boundary_index = _boundary_index(repo, chain, base_sha)
        relevant = list(chain_problems)  # the whole first-parent chain, down to the root
        for _position, text in relevant:
            problems.append("history availability: " + text)
        if boundary_index is not None:
            window_positions = list(range(boundary_index - 1, -1, -1))  # oldest first
        else:
            window_positions = []
        touch_positions = {}
        for path in sorted(changed):
            touch_positions[path] = [idx for idx in window_positions
                                     if path in touched.get(chain[idx]["sha"], set())]
        evaluated = {path for path in changed if touch_positions[path]}
        if boundary_index is None:
            for path in sorted(changed):
                if any(path in touched.get(entry["sha"], set()) for entry in chain):
                    problems.append("unresolved boundary: no first-parent commit is an ancestor "
                                    "of the protected base, and history touches " + path)
        cap_commits = [chain[idx] for idx in window_positions
                       if touched.get(chain[idx]["sha"], set()) & evaluated]
        if len(cap_commits) > HISTORY_CAP:
            problems.append("history cap exceeded: {0} first-parent commits touch the changed paths (cap {1})".format(len(cap_commits), HISTORY_CAP))
        kept_shas = {entry["sha"] for entry in cap_commits}
        doc_cache = {}
        for path in sorted(changed):
            try:
                head_blob = path_blob(reader, head_tree, path)
                head_doc = parse_snapshot(blob_content(reader, head_blob)
                                          if head_blob else None)
                cand_sha = stage_map.get(path)
                cand_doc = parse_snapshot(blob_content(reader, cand_sha)
                                          if cand_sha else None)
                if isinstance(head_doc, _Cannot):
                    raise Refused("HEAD endpoint {0}: the snapshot could not be evaluated "
                                  "({1})".format(path, head_doc.error_class))
                if isinstance(cand_doc, _Cannot):
                    raise Refused("candidate endpoint {0}: the snapshot could not be evaluated "
                                  "({1})".format(path, cand_doc.error_class))
                if head_doc is GAP:
                    problems.append("HEAD endpoint does not parse: " + path)
                    continue
                if cand_doc is GAP:
                    problems.append("candidate endpoint does not parse: " + path)
                    continue
                if path not in evaluated:
                    # Option A: a path with no history in the window (or none proven under an
                    # unresolved boundary) is evaluated against HEAD alone: no transitions, so
                    # no finding; only the endpoint parse checks above apply.
                    continue
                departed, removed_pairs = evaluate_history(
                    reader, chain, boundary_index, path, touch_positions[path],
                    kept_shas, doc_cache, gap_notes)
                file_findings = findings_for(path, head_doc, cand_doc, departed, removed_pairs)
                findings.extend(file_findings)
            except Unavailable as exc:
                problems.append("object unavailable: {0} ({1})".format(path, exc))
                continue
    finally:
        reader.close()
    for text in problems:
        print("error: toml-restoration checker: " + text, file=sys.stderr)
    for text in findings:
        print("FAIL: toml-restoration checker: " + text, file=sys.stderr)
    if problems:
        _print_report("refusal", ["refusal: " + text for text in problems])
        return 2
    report = ["finding: " + text for text in findings]
    report += ["gap: " + text for text in gap_notes]
    report.append("findings-set-digest: " + _findings_digest(findings))
    _print_report("result", report)
    if findings:
        return 1
    print("PASS: toml-restoration checker: no restoration finding within the declared coverage "
          "({0} changed .toml path(s))".format(len(changed)), file=sys.stderr)
    return 0


# ---- the command line --------------------------------------------------------------------------


def _parse_cli(args):
    """(repo, head, base) for a checking run, or the usage-refusal text (a str). Each option
    is required exactly once; -h, --help, a prefix, a repeat, a positional argument and any
    other argument are usage errors."""
    values = {"--repo": [], "--head": [], "--base": []}
    index = 0
    while index < len(args):
        name = args[index]
        if name in values:
            if index + 1 >= len(args):
                return "missing value for " + name
            values[name].append(args[index + 1])
            index += 2
            continue
        return "unknown or misplaced argument {0!r}".format(name)
    for name in ("--repo", "--head", "--base"):
        if len(values[name]) != 1:
            return "option {0} is required exactly once (got {1})".format(
                name, len(values[name]))
    return values["--repo"][0], values["--head"][0], values["--base"][0]


def _id_problems(repo, head, base):
    """(None, hash_bytes) when --head and --base are full lowercase hexadecimal object IDs of
    the repository's object-format length; otherwise (refusal text, None)."""
    fmt = _git(repo, ["rev-parse", "--show-object-format"])
    if fmt.returncode != 0:
        return ("the repository at {0!r} cannot be read ({1})".format(
            repo, fmt.stderr.decode("utf-8", "replace").strip()), None)
    name = fmt.stdout.strip().decode("ascii", "replace")
    length = {"sha1": 40, "sha256": 64}.get(name)
    if length is None:
        return "unsupported object format {0!r}".format(name), None
    for option, value in (("--head", head), ("--base", base)):
        if len(value) != length or any(c not in "0123456789abcdef" for c in value):
            return ("{0} must be a full lowercase hexadecimal object ID of length {1} for "
                    "object format {2} (got {3!r})".format(option, length, name, value)), None
    return None, length // 2


def _run_cli(args):
    parsed = _parse_cli(args)
    if isinstance(parsed, str):
        _print_report("refusal", ["refusal: usage: " + parsed])
        print("usage: check_toml_restoration.py --repo <absolute path> --head <full commit ID> "
              "--base <full commit ID> | --self-test", file=sys.stderr)
        return 2
    repo, head, base = parsed
    if not os.path.isabs(repo):
        _print_report("refusal", ["refusal: --repo must be an absolute path "
                                  "(got {0!r})".format(repo)])
        return 2
    problem, hash_bytes = _id_problems(repo, head, base)
    if problem is not None:
        _print_report("refusal", ["refusal: " + problem])
        print("error: toml-restoration checker: " + problem, file=sys.stderr)
        return 2
    return run_check(repo, head, base, hash_bytes)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        _bind_modules()
        if args == ["--self-test"]:
            code = self_test()
        else:
            code = _run_cli(args)
        sys.stdout.flush()
        return code
    except Exception as exc:  # noqa: BLE001  the one catch-all (entry-guard shape): an internal error is exit 2, never a bare traceback read as a finding
        try:
            _print_report("internal-error",
                          ["internal-error: {0}: {1!r}".format(type(exc).__name__, exc)])
            print("error: toml-restoration checker: internal error ({0!r}); cannot "
                  "evaluate".format(exc), file=sys.stderr)
            sys.stdout.flush()
        except BaseException:
            pass
        return 2


# ---- self-test ---------------------------------------------------------------------------------

_BAD_TOML = "k = [\n"

# Each flip removes one rule by replacing one exact source region of the checker code before
# this self-test section; the self-test asserts the replaced text occurs exactly once and that
# the mutant's verdict changes as the plan expects.
_FLIPS = {
    "skip_bad_endpoint": (
        '                if head_doc is GAP:\n'
        '                    problems.append("HEAD endpoint does not parse: " + path)\n'
        '                    continue',
        '                if head_doc is GAP:\n'
        '                    continue'),
    "first_finding": (
        '                findings.extend(file_findings)',
        '                findings.extend(file_findings)\n'
        '                if findings:\n'
        '                    return 1'),
    "start_oldest": (
        '        return None  # no commit on the first-parent chain is an ancestor of the protected base',
        '        return len(chain) - 1'),
    "unavailable_as_no_history": (
        '                problems.append("object unavailable: {0} ({1})".format(path, exc))',
        '                pass'),
    "ignore_shallow": (
        '    if shallow_repo:',
        '    if False:'),
    "root_touches_nothing": (
        '            parent_blob = None  # a root commit is compared with the empty tree',
        '            continue'),
    "missing_as_no_touch": (
        '    return chain, touched, problems',
        '    return chain, touched, []'),
    "window_only_availability": (
        '        relevant = list(chain_problems)  # the whole first-parent chain, down to the root',
        '        relevant = [item for item in chain_problems '
        'if boundary_index is None or item[0] <= boundary_index]'),
    "cap_ge": (
        '        if len(cap_commits) > HISTORY_CAP:',
        '        if len(cap_commits) >= HISTORY_CAP:'),
    "keep_newest": (
        '            problems.append("history cap exceeded: {0} first-parent commits touch the changed paths (cap {1})".format(len(cap_commits), HISTORY_CAP))',
        '            del cap_commits[:len(cap_commits) - HISTORY_CAP]'),
    "no_r1": (
        '        if cand_enc in departed_encs:',
        '        if False:'),
    "any_change_with_history": (
        '        if cand_enc in departed_encs:',
        '        if departed_encs:'),
    "ignore_missing": (
        '        if old_enc == new_enc:',
        '        if old_enc == new_enc or old_value is MISSING or new_value is MISSING:'),
    "read_worktree": (
        '                cand_sha = stage_map.get(path)\n'
        '                cand_doc = parse_snapshot(blob_content(reader, cand_sha)\n'
        '                                          if cand_sha else None)',
        '                cand_sha = stage_map.get(path)\n'
        '                try:\n'
        '                    with open(os.path.join(repo, path), "rb") as handle:\n'
        '                        cand_doc = parse_snapshot(handle.read())\n'
        '                except OSError:\n'
        '                    cand_doc = parse_snapshot(None)'),
    "read_ours": (
        '        if fields[2] != "0":\n'
        '            conflicts.add(path)\n'
        '        else:\n'
        '            stage_map[path] = fields[1]',
        '        if fields[2] == "0" or fields[2] == "2":\n'
        '            stage_map[path] = fields[1]'),
    "raw_text_values": (
        '        return tomllib.loads(text)',
        '        return {"__raw__": text}'),
    "include_non_toml": (
        '            if path and path.endswith(b".toml")}',
        '            if path}'),
    "follow_renames": (
        '                if path not in evaluated:\n'
        '                    # Option A: a path with no history in the window (or none proven under an\n'
        '                    # unresolved boundary) is evaluated against HEAD alone: no transitions, so\n'
        '                    # no finding; only the endpoint parse checks above apply.\n'
        '                    continue\n'
        '                departed, removed_pairs = evaluate_history(\n'
        '                    reader, chain, boundary_index, path, touch_positions[path],\n'
        '                    kept_shas, doc_cache, gap_notes)',
        '                hist_path, hist_positions = path, touch_positions[path]\n'
        '                if path not in evaluated:\n'
        '                    hist_path = None\n'
        '                    for other in sorted(changed):\n'
        '                        if (other != path and stage_map.get(other) is None\n'
        '                                and touch_positions[other]):\n'
        '                            hist_path, hist_positions = other, touch_positions[other]\n'
        '                            break\n'
        '                    if hist_path is None:\n'
        '                        continue\n'
        '                departed, removed_pairs = evaluate_history(\n'
        '                    reader, chain, boundary_index, hist_path, hist_positions,\n'
        '                    kept_shas, doc_cache, gap_notes)'),
    "parse_errors_propagate": (
        '    except ValueError:\n'
        '        # the snapshot does not parse: a bridged gap in history, a refusal at an endpoint\n'
        '        return GAP\n'
        '    except RecursionError:\n'
        '        return _Cannot("RecursionError")\n'
        '    except MemoryError:\n'
        '        return _Cannot("MemoryError")',
        '    except FloatingPointError:\n'
        '        return GAP'),
    "drop_catch_all": (
        '    except Exception as exc:  # noqa: BLE001  the one catch-all (entry-guard shape): an internal error is exit 2, never a bare traceback read as a finding',
        '    except Exception as exc:  # noqa: BLE001  flip: the catch-all re-raises\n'
        '        raise'),
    "module_level_imports": (
        '    import tomllib',
        '    import tomllib\n'
        '\n'
        '\n'
        '_bind_modules()'),
}

_CHILD_RUNNER = (
    "import importlib.util, sys\n"
    "@PRE@\n"
    "spec = importlib.util.spec_from_file_location('toml_restoration_under_test', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "@POST@\n"
    "sys.exit(module.main(['--repo', sys.argv[2], '--head', sys.argv[3], "
    "'--base', sys.argv[4]]))\n"
)

# T56(a)/(b) and T57(a)/(b): patch the callee, the tomllib.loads binding the parse site calls,
# as an attribute of the tomllib module in the child process (never at the checker's module
# scope), so the error starts inside the parse site's own try.
_PATCH_POISON = (
    "import tomllib\n"
    "_orig_loads = tomllib.loads\n"
    "def _patched_loads(text, *args, **kwargs):\n"
    "    if 'poison' in text:\n"
    "        raise {0}('patched parse callee')\n"
    "    return _orig_loads(text, *args, **kwargs)\n"
    "tomllib.loads = _patched_loads"
)

# T58(a): patch an internal function on the evaluation path (the canonical encoder) as an
# attribute of the loaded module, inside the child process.
_PATCH_CANON = (
    "def _patched_canon(*args, **kwargs):\n"
    "    raise RuntimeError('patched internal error')\n"
    "module.canon = _patched_canon"
)

# T58(b): before main() runs (and before the module loads, which the module-level-imports flip
# turns into a load failure), make `import tomllib` fail in the child process.
_PATCH_NO_TOMLLIB = "sys.modules['tomllib'] = None"

# The expected self-test check ids (a module-level literal tuple, separate from the call sites;
# a disclosed deviation from the tools/selftest_checks.toml registry, which cannot register a
# .github runner's ids). The run exits 1 on any set difference with the executed ids.
_EXPECTED_CHECK_IDS = (
    "flip-unique:any_change_with_history",
    "flip-unique:cap_ge",
    "flip-unique:drop_catch_all",
    "flip-unique:first_finding",
    "flip-unique:follow_renames",
    "flip-unique:ignore_missing",
    "flip-unique:ignore_shallow",
    "flip-unique:include_non_toml",
    "flip-unique:keep_newest",
    "flip-unique:missing_as_no_touch",
    "flip-unique:module_level_imports",
    "flip-unique:no_r1",
    "flip-unique:parse_errors_propagate",
    "flip-unique:raw_text_values",
    "flip-unique:read_ours",
    "flip-unique:read_worktree",
    "flip-unique:root_touches_nothing",
    "flip-unique:skip_bad_endpoint",
    "flip-unique:start_oldest",
    "flip-unique:unavailable_as_no_history",
    "flip-unique:window_only_availability",
    "rows:complete",
    "row:A1a:strict", "row:A1a:flip",
    "row:A1b:strict", "row:A1b:flip",
    "row:A2:strict", "row:A2:flip",
    "row:A3a:strict", "row:A3a:flip",
    "row:A3b:strict", "row:A3b:flip",
    "row:A4:strict", "row:A4:flip",
    "row:T10:strict", "row:T10:flip",
    "row:T11:strict", "row:T11:flip",
    "row:T12:strict", "row:T12:flip",
    "row:T26:strict", "row:T26:flip",
    "row:T27:strict", "row:T27:flip",
    "row:T32:strict", "row:T32:flip",
    "row:T33:strict", "row:T33:flip",
    "row:T34:strict", "row:T34:flip",
    "row:T35:strict", "row:T35:flip",
    "row:T36:strict", "row:T36:flip",
    "row:T50:strict", "row:T50:flip",
    "row:T51a:strict", "row:T51a:flip",
    "row:T51b:strict", "row:T51b:flip",
    "row:T55a:strict", "row:T55a:flip",
    "row:T55b:strict", "row:T55b:flip",
    "row:T55c:strict", "row:T55c:flip",
    "row:T56a:strict", "row:T56a:flip",
    "row:T56b:strict", "row:T56b:flip",
    "row:T56c:strict", "row:T56c:flip",
    "row:T57a:strict", "row:T57a:flip",
    "row:T57b:strict", "row:T57b:flip",
    "row:T57c:strict", "row:T57c:flip",
    "row:T58a:strict", "row:T58a:flip",
    "row:T58b:strict", "row:T58b:flip",
    "usage:no-arguments",
    "usage:missing-repo",
    "usage:missing-head",
    "usage:missing-base",
    "usage:unknown-option",
    "usage:short-help",
    "usage:long-help",
    "usage:option-prefix",
    "usage:repeated-repo",
    "usage:positional-argument",
    "usage:self-test-with-extra",
    "usage:relative-repo",
    "ids:head-bad-length",
    "ids:head-uppercase",
    "ids:head-not-head",
    "ids:base-absent",
)


def _hermetic_env(home_dir):
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": os.environ.get("TMPDIR", home_dir),
        "HOME": home_dir,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "1700000000 +0000",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_DATE": "1700000000 +0000",
    }


def _run_fixture_git(root, args, env, input_bytes=None):
    completed = subprocess.run(["git", "-C", root] + list(args), input=input_bytes,
                               capture_output=True, env=env, timeout=600)
    if completed.returncode != 0:
        raise RuntimeError("fixture git {0} failed: {1}".format(
            args, completed.stderr.decode("utf-8", "replace")))
    return completed.stdout


class _Fixture:
    def __init__(self, root, env):
        self.root = str(root)
        self.env = env
        os.makedirs(self.root, exist_ok=True)
        _run_fixture_git(self.root, ["init", "-q"], env)
        _run_fixture_git(self.root, ["symbolic-ref", "HEAD", "refs/heads/main"], env)

    def blob(self, text):
        data = text.encode("utf-8") if isinstance(text, str) else text
        return _run_fixture_git(self.root, ["hash-object", "-w", "--stdin"], self.env,
                                input_bytes=data).strip().decode("ascii")

    def commit(self, files, parents=(), message="c"):
        lines = "".join("100644 blob {0}\t{1}\n".format(self.blob(content), name)
                        for name, content in sorted(files.items()))
        tree = _run_fixture_git(self.root, ["mktree"], self.env,
                                input_bytes=lines.encode("utf-8")).strip().decode("ascii")
        args = ["commit-tree", tree, "-m", message]
        for parent in parents:
            args += ["-p", parent]
        return _run_fixture_git(self.root, args, self.env).strip().decode("ascii")

    def set_main(self, sha):
        _run_fixture_git(self.root, ["update-ref", "refs/heads/main", sha], self.env)

    def stage(self, commit_sha, overrides):
        _run_fixture_git(self.root, ["read-tree", commit_sha], self.env)
        for path, content in sorted(overrides.items()):
            if content is None:
                _run_fixture_git(self.root, ["update-index", "--force-remove", "--", path],
                                 self.env)
            else:
                sha = self.blob(content)
                _run_fixture_git(self.root, ["update-index", "--add", "--cacheinfo",
                                             "100644,{0},{1}".format(sha, path)], self.env)

    def index_info(self, lines):
        _run_fixture_git(self.root, ["update-index", "--index-info"], self.env,
                         input_bytes=lines.encode("utf-8"))

    def delete_object(self, sha):
        os.remove(os.path.join(self.root, ".git", "objects", sha[:2], sha[2:]))

    def commit_tree_sha(self, commit_sha):
        return _run_fixture_git(self.root, ["rev-parse", commit_sha + "^{tree}"],
                                self.env).strip().decode("ascii")


def _toml_k(value):
    return 'other = 0\nk = "{0}"\n'.format(value)


def _linear_fixture(base_dir, env, name, contents, candidate_files):
    fixture = _Fixture(os.path.join(base_dir, name), env)
    shas = []
    parents = []
    for position, files in enumerate(contents):
        sha = fixture.commit(files, parents=parents, message="c{0}".format(position))
        shas.append(sha)
        parents = [sha]
    fixture.set_main(shas[-1])
    fixture.stage(shas[-1], candidate_files)
    return fixture, shas


def _long_fixture(base_dir, env, name, total):
    """long(n, n): n commits after c0, each touching a.toml and b.toml, built with
    git fast-import; the oldest touching commit moves k from A to B."""
    fixture = _Fixture(os.path.join(base_dir, name), env)
    out = bytearray()
    mark = 0

    def add_blob(text):
        nonlocal mark
        mark += 1
        data = text.encode("utf-8")
        out.extend(b"blob\nmark :%d\ndata %d\n" % (mark, len(data)))
        out.extend(data + b"\n")
        return mark

    def add_commit(message, file_marks):
        data = message.encode("utf-8")
        out.extend(b"commit refs/heads/main\n")
        out.extend(b"author fixture <fixture@example.invalid> 1700000000 +0000\n")
        out.extend(b"committer fixture <fixture@example.invalid> 1700000000 +0000\n")
        out.extend(b"data %d\n%s\n" % (len(data), data))
        for path, blob_mark in file_marks:
            out.extend(b"M 100644 :%d %s\n" % (blob_mark, path.encode("ascii")))
        out.extend(b"\n")

    add_commit("c0", [("a.toml", add_blob(_toml_k("A"))), ("b.toml", add_blob("n = 0\n"))])
    for index in range(1, total + 1):
        b_mark = add_blob("n = {0}\n".format(index))
        a_mark = add_blob('other = 0\nk = "B"\nj = {0}\n'.format(index))
        add_commit("c{0}".format(index), [("a.toml", a_mark), ("b.toml", b_mark)])
    started = time.monotonic()
    _run_fixture_git(fixture.root, ["fast-import", "--quiet"], env, input_bytes=bytes(out))
    seconds = time.monotonic() - started
    head = _run_fixture_git(fixture.root, ["rev-parse", "main"], env).strip().decode("ascii")
    base = _run_fixture_git(fixture.root, ["rev-parse", "main~{0}".format(total)],
                            env).strip().decode("ascii")
    fixture.stage(head, {"a.toml": 'other = 0\nk = "A"\nj = {0}\n'.format(total)})
    return fixture, head, base, seconds


def _build_rows(base_dir, env, notes):
    """One entry per covered row: (row id, repo root, head, base, want, flip name, flip want,
    spec). The spec holds report assertions ("has", "not_has", "gaps" and their flip_ forms)
    and the child patches ("pre", "post")."""
    rows = []

    def row(row_id, fixture_root, head, base, want, flip_name, flip_want, **spec):
        rows.append((row_id, fixture_root, head, base, want, flip_name, flip_want, spec))

    fixture, shas = _linear_fixture(base_dir, env, "t10",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("A")})
    row("T10", fixture.root, shas[1], shas[0], 1, "no_r1", 0)

    fixture, shas = _linear_fixture(base_dir, env, "t11",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("C")})
    row("T11", fixture.root, shas[1], shas[0], 0, "any_change_with_history", 1)

    fixture, shas = _linear_fixture(base_dir, env, "t12",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": "other = 0\n"}],
                                    {"a.toml": _toml_k("A")})
    row("T12", fixture.root, shas[1], shas[0], 1, "ignore_missing", 0)

    fixture, shas = _linear_fixture(base_dir, env, "t26",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _BAD_TOML}],
                                    {"a.toml": _toml_k("A")})
    row("T26", fixture.root, shas[1], shas[0], 2, "skip_bad_endpoint", 0)

    fixture, shas = _linear_fixture(
        base_dir, env, "t27",
        [{"a.toml": _toml_k("A"), "b.toml": 'k = "P"\n'},
         {"a.toml": _toml_k("B"), "b.toml": 'k = "P"\n'}],
        {"a.toml": _toml_k("A"), "b.toml": _BAD_TOML})
    row("T27", fixture.root, shas[1], shas[0], 2, "first_finding", 1)

    started = time.monotonic()
    fixture, head, base, build_seconds = _long_fixture(base_dir, env, "t32", 1000)
    notes.append("T32 fixture: 1,000 commits built with git fast-import in {0:.2f}s "
                 "(whole build {1:.2f}s)".format(build_seconds, time.monotonic() - started))
    row("T32", fixture.root, head, base, 1, "cap_ge", 2)

    started = time.monotonic()
    fixture, head, base, build_seconds = _long_fixture(base_dir, env, "t33", 1001)
    notes.append("T33 fixture: 1,001 commits built with git fast-import in {0:.2f}s "
                 "(whole build {1:.2f}s)".format(build_seconds, time.monotonic() - started))
    row("T33", fixture.root, head, base, 2, "keep_newest", 0)

    fixture = _Fixture(os.path.join(base_dir, "t34"), env)
    base = fixture.commit({"x.toml": "p = 0\n"}, message="p0")
    r0 = fixture.commit({"a.toml": _toml_k("A")}, message="r0")
    r1 = fixture.commit({"a.toml": _toml_k("B")}, parents=[r0], message="r1")
    fixture.set_main(r1)
    fixture.stage(r1, {"a.toml": _toml_k("A")})
    row("T34", fixture.root, r1, base, 2, "start_oldest", 1)

    fixture, shas = _linear_fixture(base_dir, env, "t35",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": 'other = 0\nk = "A"\n# restored\n'})
    fixture.delete_object(fixture.blob(_toml_k("A")))
    row("T35", fixture.root, shas[1], shas[0], 2, "unavailable_as_no_history", 0)

    fixture, shas = _linear_fixture(base_dir, env, "t36",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("C")})
    with open(os.path.join(fixture.root, ".git", "shallow"), "w", encoding="ascii") as handle:
        handle.write(shas[0] + "\n")
    row("T36", fixture.root, shas[1], shas[0], 2, "ignore_shallow", 0)

    fixture = _Fixture(os.path.join(base_dir, "t50"), env)
    base = fixture.commit({"x.toml": "p = 0\n"}, message="p0")
    r0 = fixture.commit({"a.toml": _toml_k("A"), "b.toml": "n = 0\n"}, message="r0")
    r1 = fixture.commit({"a.toml": _toml_k("A"), "b.toml": "n = 1\n"}, parents=[r0],
                        message="r1")
    fixture.set_main(r1)
    fixture.stage(r1, {"a.toml": _toml_k("B")})
    row("T50", fixture.root, r1, base, 2, "root_touches_nothing", 0)

    for variant, kind in (("a", "commit"), ("b", "tree")):
        fixture = _Fixture(os.path.join(base_dir, "t51" + variant), env)
        shas = []
        parents = []
        for index in range(4):
            files = {"a.toml": _toml_k("A"), "b.toml": "n = {0}\n".format(index)}
            sha = fixture.commit(files, parents=parents, message="c{0}".format(index))
            shas.append(sha)
            parents = [sha]
        fixture.set_main(shas[3])
        fixture.stage(shas[3], {"a.toml": _toml_k("B")})
        doomed = shas[2] if kind == "commit" else fixture.commit_tree_sha(shas[2])
        fixture.delete_object(doomed)
        row("T51" + variant, fixture.root, shas[3], shas[0], 2, "missing_as_no_touch", 0)

    for variant, kind, candidate in (
            ("a", "commit", {"a.toml": _toml_k("C")}),
            ("b", "tree", {"a.toml": _toml_k("C")}),
            ("c", "commit", {"new.toml": _toml_k("A")})):
        fixture = _Fixture(os.path.join(base_dir, "t55" + variant), env)
        shas = []
        parents = []
        for index in range(5):
            a_value = "B" if index >= 3 else "A"
            files = {"a.toml": _toml_k(a_value), "b.toml": "n = {0}\n".format(index)}
            sha = fixture.commit(files, parents=parents, message="c{0}".format(index))
            shas.append(sha)
            parents = [sha]
        fixture.set_main(shas[4])
        fixture.stage(shas[4], candidate)
        doomed = shas[1] if kind == "commit" else fixture.commit_tree_sha(shas[1])
        fixture.delete_object(doomed)
        row("T55" + variant, fixture.root, shas[4], shas[2], 2,
            "window_only_availability", 0)

    # A1: the candidate is the index, never the working tree.
    fixture, shas = _linear_fixture(base_dir, env, "a1a",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("A")})
    with open(os.path.join(fixture.root, "a.toml"), "w", encoding="ascii") as handle:
        handle.write(_toml_k("C"))
    row("A1a", fixture.root, shas[1], shas[0], 1, "read_worktree", 0)

    fixture, shas = _linear_fixture(base_dir, env, "a1b",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("C")})
    with open(os.path.join(fixture.root, "a.toml"), "w", encoding="ascii") as handle:
        handle.write(_toml_k("A"))
    row("A1b", fixture.root, shas[1], shas[0], 0, "read_worktree", 1)

    # A2: an unresolved index entry (stage above 0) is cannot-evaluate, whatever its stages
    # would restore.
    fixture, shas = _linear_fixture(base_dir, env, "a2",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {})
    blob_a = fixture.blob(_toml_k("A"))
    fixture.index_info("0 {0} 0\ta.toml\n100644 {1} 2\ta.toml\n".format(
        "0" * len(blob_a), blob_a))
    row("A2", fixture.root, shas[1], shas[0], 2, "read_ours", 1,
        has=["report-kind: refusal", "unresolved index entry"])

    # A3(a): a comment-only branch change undone by the candidate is no restoration (values,
    # not raw text, are compared).
    fixture, shas = _linear_fixture(base_dir, env, "a3a",
                                    [{"a.toml": 'k = "A"\n'},
                                     {"a.toml": 'k = "A" # note\n'}],
                                    {"a.toml": 'k = "A"\n'})
    row("A3a", fixture.root, shas[1], shas[0], 0, "raw_text_values", 1)

    # A3(b): a non-.toml path is out of scope even when its text restores.
    fixture, shas = _linear_fixture(base_dir, env, "a3b",
                                    [{"notes.txt": _toml_k("A")},
                                     {"notes.txt": _toml_k("B")}],
                                    {"notes.txt": _toml_k("A")})
    row("A3b", fixture.root, shas[1], shas[0], 0, "include_non_toml", 1)

    # A4: a rename counts as a deletion plus an addition; the new path has no history.
    fixture, shas = _linear_fixture(base_dir, env, "a4",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": None, "c.toml": _toml_k("A")})
    row("A4", fixture.root, shas[1], shas[0], 0, "follow_renames", 1)

    # T56: a historical snapshot whose parse raises; (a) and (b) via the patched callee, (c) a
    # real blob that is not valid UTF-8.
    poison_text = 'poison = 1\nother = 0\nk = "X"\n'
    for variant, error_class in (("a", "RecursionError"), ("b", "MemoryError")):
        fixture, shas = _linear_fixture(
            base_dir, env, "t56" + variant,
            [{"a.toml": _toml_k("A")}, {"a.toml": poison_text}, {"a.toml": _toml_k("B")}],
            {"a.toml": _toml_k("C")})
        row("T56" + variant, fixture.root, shas[2], shas[0], 2, "parse_errors_propagate", 2,
            has=["report-kind: refusal", error_class, "a.toml", "commit"],
            not_has=["report-kind: internal-error"],
            flip_has=["report-kind: internal-error", error_class],
            flip_not_has=["report-kind: refusal"], flip_gaps=0,
            pre=_PATCH_POISON.format(error_class))
    fixture, shas = _linear_fixture(
        base_dir, env, "t56c",
        [{"a.toml": _toml_k("A")}, {"a.toml": b'k = "\xff\xfe"\n'}, {"a.toml": _toml_k("B")}],
        {"a.toml": _toml_k("C")})
    row("T56c", fixture.root, shas[2], shas[0], 0, "parse_errors_propagate", 2,
        has=["report-kind: result"], gaps=1,
        flip_has=["report-kind: internal-error"],
        flip_not_has=["report-kind: refusal"], flip_gaps=0)

    # T57: the candidate endpoint's parse raises; same patch route as T56.
    for variant, error_class in (("a", "RecursionError"), ("b", "MemoryError")):
        fixture, shas = _linear_fixture(
            base_dir, env, "t57" + variant,
            [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
            {"a.toml": poison_text})
        row("T57" + variant, fixture.root, shas[1], shas[0], 2, "parse_errors_propagate", 2,
            has=["report-kind: refusal", "candidate endpoint", error_class, "a.toml"],
            not_has=["report-kind: internal-error"],
            flip_has=["report-kind: internal-error", error_class],
            flip_not_has=["report-kind: refusal"],
            pre=_PATCH_POISON.format(error_class))
    fixture, shas = _linear_fixture(
        base_dir, env, "t57c",
        [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
        {"a.toml": b'k = "\xff\xfe"\n'})
    row("T57c", fixture.root, shas[1], shas[0], 2, "parse_errors_propagate", 2,
        has=["report-kind: refusal", "candidate endpoint", "does not parse", "a.toml"],
        not_has=["report-kind: internal-error"],
        flip_has=["report-kind: internal-error"],
        flip_not_has=["report-kind: refusal"])

    # T58: the catch-all inside main() and the imports inside main().
    fixture, shas = _linear_fixture(base_dir, env, "t58a",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("A")})
    row("T58a", fixture.root, shas[1], shas[0], 2, "drop_catch_all", 1,
        has=["report-kind: internal-error", "RuntimeError"],
        flip_not_has=["report-kind: internal-error"],
        post=_PATCH_CANON)
    fixture, shas = _linear_fixture(base_dir, env, "t58b",
                                    [{"a.toml": _toml_k("A")}, {"a.toml": _toml_k("B")}],
                                    {"a.toml": _toml_k("A")})
    row("T58b", fixture.root, shas[1], shas[0], 2, "module_level_imports", 1,
        has=["report-kind: internal-error", "tomllib"],
        flip_not_has=["report-kind: internal-error"],
        pre=_PATCH_NO_TOMLLIB)

    return rows


def _run_child(module_path, repo, head, base, env, children, pre="pass", post="pass"):
    program = _CHILD_RUNNER.replace("@PRE@", pre).replace("@POST@", post)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", program, module_path, repo, head, base],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        start_new_session=True, text=True)
    children.append(child.pid)
    try:
        out, err = child.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        out, err = child.communicate()
        return 124, out, err
    return child.returncode, out, err


def _report_issues(out, has, not_has, gaps):
    """The report assertions a row's spec asks for, as a list of problems (empty when met)."""
    issues = []
    for text in has:
        if text not in out:
            issues.append("missing {0!r}".format(text))
    for text in not_has:
        if text in out:
            issues.append("unexpected {0!r}".format(text))
    if gaps is not None:
        got = sum(1 for line in out.splitlines() if line.startswith("gap: "))
        if got != gaps:
            issues.append("{0} gap line(s), expected {1}".format(got, gaps))
    return issues


def self_test():
    try:
        return _self_test_cases()
    except Exception as exc:  # noqa: BLE001  fail-closed self-test backstop, never a false 0
        print("toml-restoration checker self-test: cannot evaluate: unexpected error "
              "({0!r})".format(exc), file=sys.stderr)
        return 2


def _self_test_cases():
    failures = []
    executed = []
    notes = []
    children = []

    def record(check_id, ok, detail):
        executed.append(check_id)
        print("{0} {1}".format("PASS" if ok else "FAIL", check_id))
        if not ok:
            failures.append("{0}: {1}".format(check_id, detail))

    with open(__file__, "r", encoding="utf-8") as handle:
        source = handle.read()
    with tempfile.TemporaryDirectory(prefix="toml-restoration-selftest-") as tmp:
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        env = _hermetic_env(home)
        mutants_dir = os.path.join(tmp, "mutants")
        os.makedirs(mutants_dir)
        mutant_paths = {}
        # the flip table quotes its target regions, so uniqueness is counted (and the
        # replacement applied) over the checker code before this self-test section only
        code_part, marker, tail_part = source.partition("# ---- self-test ")
        for flip_name, (old_text, new_text) in sorted(_FLIPS.items()):
            occurrences = code_part.count(old_text)
            record("flip-unique:" + flip_name,
                   occurrences == 1, "{0} occurrence(s)".format(occurrences))
            mutant_path = os.path.join(mutants_dir, flip_name + ".py")
            with open(mutant_path, "w", encoding="utf-8") as handle:
                handle.write(code_part.replace(old_text, new_text) + marker + tail_part)
            mutant_paths[flip_name] = mutant_path

        rows = _build_rows(os.path.join(tmp, "repos"), env, notes)
        covered = {entry[0] for entry in rows}
        wanted = {"T10", "T11", "T12", "T26", "T27", "T32", "T33", "T34", "T35", "T36",
                  "T50", "T51a", "T51b", "T55a", "T55b", "T55c",
                  "A1a", "A1b", "A2", "A3a", "A3b", "A4",
                  "T56a", "T56b", "T56c", "T57a", "T57b", "T57c", "T58a", "T58b"}
        record("rows:complete", covered == wanted,
               "missing {0}, extra {1}".format(sorted(wanted - covered),
                                               sorted(covered - wanted)))
        for row_id, repo, head, base, want, flip_name, flip_want, spec in rows:
            started = time.monotonic()
            pre = spec.get("pre", "pass")
            post = spec.get("post", "pass")
            code, out, err = _run_child(__file__, repo, head, base, env, children, pre, post)
            issues = _report_issues(out, spec.get("has", ()), spec.get("not_has", ()),
                                    spec.get("gaps"))
            record("row:{0}:strict".format(row_id), code == want and not issues,
                   "exit {0} (want {1}); report issues {2}; stdout {3!r}; stderr {4!r}".format(
                       code, want, issues, out[-400:], err[-400:]))
            code, out, err = _run_child(mutant_paths[flip_name], repo, head, base, env,
                                        children, pre, post)
            issues = _report_issues(out, spec.get("flip_has", ()), spec.get("flip_not_has", ()),
                                    spec.get("flip_gaps"))
            record("row:{0}:flip".format(row_id), code == flip_want and not issues,
                   "flip {0}: exit {1} (want {2}); report issues {3}; stdout {4!r}; "
                   "stderr {5!r}".format(flip_name, code, flip_want, issues, out[-400:],
                                         err[-400:]))
            notes.append("{0}: strict and flip runs took {1:.2f}s together".format(
                row_id, time.monotonic() - started))

        # the command-line contract: every usage or ID problem exits 2 and never runs a check
        t10_root, t10_head, t10_base = rows[0][1], rows[0][2], rows[0][3]
        for check_id, argv in (
                ("usage:no-arguments", []),
                ("usage:missing-repo", ["--head", t10_head, "--base", t10_base]),
                ("usage:missing-head", ["--repo", t10_root, "--base", t10_base]),
                ("usage:missing-base", ["--repo", t10_root, "--head", t10_head]),
                ("usage:unknown-option", ["--repo", t10_root, "--head", t10_head,
                                          "--base", t10_base, "--bogus"]),
                ("usage:short-help", ["-h"]),
                ("usage:long-help", ["--help"]),
                ("usage:option-prefix", ["--re", t10_root, "--head", t10_head,
                                         "--base", t10_base]),
                ("usage:repeated-repo", ["--repo", t10_root, "--repo", t10_root,
                                         "--head", t10_head, "--base", t10_base]),
                ("usage:positional-argument", ["--repo", t10_root, "--head", t10_head,
                                               "--base", t10_base, t10_root]),
                ("usage:self-test-with-extra", ["--self-test", "--repo", t10_root]),
                ("usage:relative-repo", ["--repo", ".", "--head", t10_head,
                                         "--base", t10_base]),
                ("ids:head-bad-length", ["--repo", t10_root, "--head", t10_head[:12],
                                         "--base", t10_base]),
                ("ids:head-uppercase", ["--repo", t10_root, "--head", "A" + t10_head[1:],
                                        "--base", t10_base]),
                ("ids:head-not-head", ["--repo", t10_root, "--head", t10_base,
                                       "--base", t10_base]),
                ("ids:base-absent", ["--repo", t10_root, "--head", t10_head,
                                     "--base", "0" * len(t10_head)])):
            got = main(list(argv))
            record(check_id, got == 2, "exit {0} (want 2) for argv {1!r}".format(got, argv))

    for note in notes:
        print("note: " + note)
    print("note: {0} checker child process(es) started in new sessions; each was waited on "
          "before the self-test ended".format(len(children)))
    code = 0
    if failures:
        print("SELF-TEST FAIL ({0} of {1} check(s)):".format(len(failures), len(executed)))
        for failure in failures:
            print("  - " + failure)
        code = 1
    expected = set(_EXPECTED_CHECK_IDS)
    if len(set(executed)) != len(executed) or set(executed) != expected:
        print("SELF-TEST FAIL: executed check ids do not reconcile with _EXPECTED_CHECK_IDS: "
              "{0} expected, {1} executed; missing {2}; extra or duplicate {3}".format(
                  len(_EXPECTED_CHECK_IDS), len(executed),
                  sorted(expected - set(executed)),
                  sorted(set(item for item in executed
                             if item not in expected or executed.count(item) > 1))))
        code = 1
    if code == 0:
        print("SELF-TEST PASS: {0} check(s) over {1} fixture row(s), each paired with its plan "
              "flip as a source mutant loaded through importlib".format(
                  len(executed), len(_EXPECTED_CHECK_IDS) and len(
                      [item for item in _EXPECTED_CHECK_IDS if item.startswith("row:")]) // 2))
    return code


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py,
# which tools/check_child_status_coverage.py pins this copy to by AST. This checker starts git
# children (subprocess) and reads their exit status, in its checking run and in its self-test,
# so under an ignored SIGCHLD a failing child would read as a pass; the guard runs at the entry
# line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("check_toml_restoration.py") or main())
