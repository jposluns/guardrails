"""Read-only, bounded raw Git history primitives for PR4.

An absence proof covers only commits reachable from a pinned HEAD and one
product prefix. It says nothing about rewritten-away history, undisclosed moves,
unmerged refs or other products. No filesystem or Git mutation is performed.
"""
from dataclasses import dataclass
import hashlib
import math
import re
import time

FIRST_ADOPTION = "FIRST_ADOPTION"
COMPLETED_ADOPTION = "COMPLETED_ADOPTION"
COMMITTED_DELETION = "COMMITTED_DELETION"
RESUME_ONLY = "RESUME_ONLY"
REFUSED = "REFUSED"
CANNOT_EVALUATE = "CANNOT_EVALUATE"


class ObservationError(Exception):
    def __init__(self, code, input_name, detail):
        super().__init__(detail)
        self.code, self.input_name, self.detail = code, input_name, detail


@dataclass(frozen=True)
class Limits:
    commits: int = 10000
    objects: int = 100000
    payload_bytes: int = 1 << 20
    aggregate_bytes: int = 64 << 20
    entries: int = 100000
    depth: int = 128
    call_seconds: float = 10.0
    total_seconds: float = 60.0

    def validate(self):
        ceilings = Limits()
        for name in ("commits", "objects", "payload_bytes",
                     "aggregate_bytes", "entries", "depth"):
            v = getattr(self, name)
            if type(v) is not int or not 0 < v <= getattr(ceilings, name):
                raise ObservationError(CANNOT_EVALUATE, "limits." + name,
                                       "positive bounded integer required")
        for name in ("call_seconds", "total_seconds"):
            v = getattr(self, name)
            if type(v) not in (int, float) or not math.isfinite(v) or not (
                    0 < v <= getattr(ceilings, name)):
                raise ObservationError(CANNOT_EVALUATE, "limits." + name,
                                       "finite positive bounded duration required")
        if self.call_seconds > self.total_seconds:
            raise ObservationError(CANNOT_EVALUATE, "limits",
                                   "call duration exceeds total duration")


class Budget:
    def __init__(self, limits, clock=time.monotonic):
        if type(limits) is not Limits:
            raise ObservationError(CANNOT_EVALUATE, "limits", "Limits required")
        limits.validate()
        self.limits, self.clock = limits, clock
        self.deadline = clock() + limits.total_seconds
        self.counts = {"commits": 0, "objects": 0,
                       "aggregate_bytes": 0, "entries": 0}

    def tick(self, what=None, amount=0):
        if self.clock() >= self.deadline:
            raise ObservationError(CANNOT_EVALUATE, "time", "observation time limit")
        if what is not None:
            if type(amount) is not int or amount < 0 or what not in self.counts:
                raise ObservationError(CANNOT_EVALUATE, "budget", "invalid charge")
            self.counts[what] += amount
            if self.counts[what] > getattr(self.limits, what):
                raise ObservationError(CANNOT_EVALUATE, what, "observation limit")

    def timeout(self):
        self.tick()
        return min(self.limits.call_seconds, self.deadline - self.clock())


def oid_ok(oid, fmt):
    return (fmt in ("sha1", "sha256") and type(oid) is str
            and re.fullmatch("[0-9a-f]{" + str(40 if fmt == "sha1" else 64) + "}", oid)
            is not None)


def parse_commit(raw, fmt):
    """Parse structural headers; signed continuation headers are nonstructural."""
    headers, sep, _message = raw.partition(b"\n\n")
    if not sep or b"\0" in headers:
        raise ObservationError(CANNOT_EVALUATE, "commit", "malformed header framing")
    tree, parents, previous = None, [], None
    for i, line in enumerate(headers.split(b"\n")):
        if line.startswith(b" "):
            if previous is None or previous in (b"tree", b"parent"):
                raise ObservationError(CANNOT_EVALUATE, "commit",
                                       "structural header continuation")
            continue
        key, space, value = line.partition(b" ")
        if not space or not key or any(c < 33 or c > 126 for c in key):
            raise ObservationError(CANNOT_EVALUATE, "commit", "malformed header")
        previous = key
        if key not in (b"tree", b"parent"):
            continue
        try:
            oid = value.decode("ascii", "strict")
        except UnicodeError:
            oid = None
        if not oid_ok(oid, fmt):
            raise ObservationError(CANNOT_EVALUATE, "commit", "invalid structural OID")
        if key == b"tree":
            if i != 0 or tree is not None:
                raise ObservationError(CANNOT_EVALUATE, "commit", "tree header order")
            tree = oid
        else:
            if tree is None or oid in parents:
                raise ObservationError(CANNOT_EVALUATE, "commit", "invalid parent header")
            parents.append(oid)
    if tree is None:
        raise ObservationError(CANNOT_EVALUATE, "commit", "missing tree")
    return tree, tuple(parents)


def parse_tree(raw, fmt):
    """Raw tree bytes: no checkout, pathspec, textconv or decoding replacement."""
    width = 20 if fmt == "sha1" else 32 if fmt == "sha256" else 0
    if not width:
        raise ObservationError(CANNOT_EVALUATE, "tree", "unsupported object format")
    out, pos = {}, 0
    while pos < len(raw):
        space = raw.find(b" ", pos)
        nul = raw.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 1 + width > len(raw):
            raise ObservationError(CANNOT_EVALUATE, "tree", "truncated tree entry")
        mode, name = raw[pos:space], raw[space + 1:nul]
        if (mode not in (b"40000", b"100644", b"100755", b"120000", b"160000")
                or not name or name in (b".", b"..")
                or b"/" in name or name in out):
            raise ObservationError(CANNOT_EVALUATE, "tree", "invalid or duplicate entry")
        out[name] = (int(mode, 8), raw[nul + 1:nul + 1 + width].hex())
        pos = nul + 1 + width
    return out


class RawHistory:
    """The reader callback is (kind, exact_oid, budget) -> exact bytes.

    The production callback must enforce streaming output bounds. Object hashes
    are independently checked here; replacing bytes never changes their identity.
    """
    def __init__(self, read_object, *, object_format, budget):
        if object_format not in ("sha1", "sha256"):
            raise ObservationError(CANNOT_EVALUATE, "object_format", "unsupported format")
        self.read_object, self.fmt, self.budget = read_object, object_format, budget
        self.cache, self.commits, self.trees = {}, {}, {}

    def object(self, kind, oid):
        self.budget.tick()
        if kind not in ("commit", "tree", "blob") or not oid_ok(oid, self.fmt):
            raise ObservationError(CANNOT_EVALUATE, "object", "invalid type or OID")
        key = (kind, oid)
        if key in self.cache:
            return self.cache[key]
        self.budget.tick("objects", 1)
        raw = self.read_object(kind, oid, self.budget)
        if type(raw) is not bytes or len(raw) > self.budget.limits.payload_bytes:
            raise ObservationError(CANNOT_EVALUATE, oid, "invalid or oversized object")
        self.budget.tick("aggregate_bytes", len(raw))
        header = kind.encode("ascii") + b" " + str(len(raw)).encode("ascii") + b"\0"
        if hashlib.new(self.fmt, header + raw).hexdigest() != oid:
            raise ObservationError(CANNOT_EVALUATE, oid, "object identity mismatch")
        self.cache[key] = raw
        return raw

    def commit(self, oid):
        if oid not in self.commits:
            self.budget.tick("commits", 1)
            self.commits[oid] = parse_commit(self.object("commit", oid), self.fmt)
        return self.commits[oid]

    def tree(self, oid):
        if oid not in self.trees:
            entries = parse_tree(self.object("tree", oid), self.fmt)
            self.budget.tick("entries", len(entries))
            self.trees[oid] = entries
        return self.trees[oid]

    def reachable(self, root):
        todo, seen = [root], set()
        while todo:
            self.budget.tick()
            oid = todo.pop()
            if oid in seen:
                continue
            _tree, parents = self.commit(oid)
            seen.add(oid)
            todo.extend(reversed(parents))
        return frozenset(seen)

    def first_parent(self, root):
        seen, out = set(), []
        while root is not None:
            self.budget.tick()
            if root in seen:
                raise ObservationError(CANNOT_EVALUATE, root, "ancestry cycle")
            seen.add(root)
            out.append(root)
            _tree, parents = self.commit(root)
            root = parents[0] if parents else None
        return tuple(out)

    def at(self, commit, path):
        if (type(path) is not str or not path or path.startswith("/")
                or any(p in ("", ".", "..") for p in path.split("/"))):
            raise ObservationError(CANNOT_EVALUATE, "path", "noncanonical path")
        try:
            parts = [p.encode("utf-8", "strict") for p in path.split("/")]
        except UnicodeError:
            raise ObservationError(CANNOT_EVALUATE, "path", "unrepresentable path")
        if len(parts) > self.budget.limits.depth:
            raise ObservationError(CANNOT_EVALUATE, path, "path depth limit")
        tree, _parents = self.commit(commit)
        for i, name in enumerate(parts):
            entry = self.tree(tree).get(name)
            if entry is None:
                return None
            mode, oid = entry
            if i == len(parts) - 1:
                return entry
            if mode != 0o40000:
                raise ObservationError(REFUSED, path, "non-directory ancestor")
            tree = oid

    def presence(self, commit, *, prefix, roots):
        import _opf_store
        import _opf_init_contract
        required = (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL,
                    _opf_store.WORKING_DIRNAME)
        if type(roots) is not tuple or roots != required:
            raise ObservationError(CANNOT_EVALUATE, "scope", "resolver presence roster required")
        if type(prefix) is not str or (prefix and _opf_init_contract._bad_relpath(prefix)):
            raise ObservationError(CANNOT_EVALUATE, "prefix", "invalid product prefix")
        return any(self.at(commit, (prefix + "/" if prefix else "") + p) is not None
                   for p in roots)

    def prove_absence(self, root, *, prefix, roots):
        reachable = self.reachable(root)
        present = tuple(sorted(c for c in reachable
                               if self.presence(c, prefix=prefix, roots=roots)))
        return {"root": root, "product_prefix": prefix,
                "reachable": tuple(sorted(reachable)), "present": present,
                "complete": True, "absent": not present}

    def latest_deletion(self, root, *, prefix, roots):
        chain = self.first_parent(root)
        if self.presence(root, prefix=prefix, roots=roots):
            raise ObservationError(REFUSED, root, "HEAD is not absent")
        for i, oid in enumerate(chain[1:], 1):
            if self.presence(oid, prefix=prefix, roots=roots):
                deletion = chain[i - 1]
                if self.commit(deletion)[1][0] != oid:
                    raise ObservationError(CANNOT_EVALUATE, deletion,
                                           "deletion parent does not reconcile")
                return {"seed_commit": oid, "deletion_commit": deletion,
                        "absent_chain": chain[:i]}
        return None

    def side_activity(self, root, selected, *, prefix, roots):
        difference = self.reachable(root) - self.reachable(selected)
        return tuple(sorted(c for c in difference
                            if self.presence(c, prefix=prefix, roots=roots)))


class GitObjects:
    """Production exact-object adapter; owns no writer or filesystem descriptor.

    Absolute Git executable selection retains _opf_observe's documented PATH
    lookup boundary. Metadata containment retains _opf_store's POSIX boundary.
    Repeated graft observations do not eliminate same-UID or ABA races.
    """
    def __init__(self, git, product_root):
        import os
        if (type(git) is not str or not os.path.isabs(git)
                or type(product_root) is not str or not os.path.isabs(product_root)):
            raise ObservationError(CANNOT_EVALUATE, "binding",
                                   "absolute executable and product root required")
        self.git, self.product_root = git, product_root

    def run(self, args, budget):
        import _opf_observe
        outcome = _opf_observe._run_git(
            self.git, self.product_root, args, timeout=budget.timeout(),
            max_output_bytes=budget.limits.payload_bytes)
        budget.tick()
        if not outcome.completed:
            raise ObservationError(CANNOT_EVALUATE, "git " + args[0],
                                   outcome.err or "Git read failed")
        if outcome.rc != 0:
            raise ObservationError(CANNOT_EVALUATE, "git " + args[0],
                                   "required evidence does not resolve: " + outcome.err)
        return outcome.out

    def __call__(self, kind, oid, budget):
        typ = self.run(["cat-file", "-t", oid], budget)
        if typ != kind.encode("ascii") + b"\n":
            raise ObservationError(CANNOT_EVALUATE, oid, "unexpected object type")
        size = self.run(["cat-file", "-s", oid], budget)
        if not re.fullmatch(rb"(0|[1-9][0-9]*)\n", size):
            raise ObservationError(CANNOT_EVALUATE, oid, "malformed object size")
        if int(size) > budget.limits.payload_bytes:
            raise ObservationError(CANNOT_EVALUATE, oid, "object payload limit")
        raw = self.run(["cat-file", kind, oid], budget)
        if len(raw) != int(size):
            raise ObservationError(CANNOT_EVALUATE, oid, "object size changed")
        return raw

    def graft_snapshot(self, budget):
        import os
        import _opf_store
        raw = self.run(["rev-parse", "--git-path", "info/grafts"], budget)
        try:
            path = raw.decode("utf-8", "strict")
        except UnicodeError:
            raise ObservationError(CANNOT_EVALUATE, "grafts", "undecodable path")
        if not path.endswith("\n") or "\n" in path[:-1] or "\0" in path:
            raise ObservationError(CANNOT_EVALUATE, "grafts", "malformed path")
        path = path[:-1]
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(self.product_root, path))
        parent, name = os.path.split(path)
        # Walk from /, preserving evidence for every existing ancestor. A missing
        # ancestor proves absence only after its parent was opened without links.
        fd = None
        identities = []
        try:
            fd = _opf_store._open_dir_nofollow("/")
            for component in parent.split("/"):
                if not component:
                    continue
                try:
                    next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY
                                      | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                except FileNotFoundError:
                    return (path, tuple(identities), "absent-ancestor", component)
                os.close(fd)
                fd = next_fd
                st = os.fstat(fd)
                identities.append((st.st_dev, st.st_ino))
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return (path, tuple(identities), "absent")
            raise ObservationError(CANNOT_EVALUATE, path,
                                   "grafts entry exists; preserved, never interpreted")
        except OSError as exc:
            raise ObservationError(CANNOT_EVALUATE, path,
                                   "cannot classify grafts safely: " + str(exc))
        finally:
            if fd is not None:
                os.close(fd)


def read_seed_basis(product_root, *, git, pinned_head, evidence_commit,
                    prefix, object_format, max_first_parent=10000):
    """Raw first-parent seed-consumer seam; deliberately does not select E.

    This does not establish adoption qualification or authorize initialization.
    The full PR4 classifier must supply that separate authority.
    """
    if not oid_ok(pinned_head, object_format) or not oid_ok(evidence_commit, object_format):
        raise ObservationError(CANNOT_EVALUATE, "seed", "invalid commit OID")
    if (type(prefix) is not str or prefix.startswith("/")
            or (prefix and any(p in ("", ".", "..") for p in prefix.split("/")))):
        raise ObservationError(CANNOT_EVALUATE, "prefix", "noncanonical product prefix")
    limits = Limits(commits=max_first_parent)
    budget = Budget(limits)
    source = GitObjects(git, product_root)
    grafts = source.graft_snapshot(budget)
    history = RawHistory(source, object_format=object_format, budget=budget)
    # An absent/unresolvable evidence object is CANNOT-EVALUATE ("does not resolve"),
    # distinct from an existing commit that is off the first-parent line: resolve it
    # first so an absent OID is not misreported as an off-chain refusal.
    source.run(["cat-file", "-e", evidence_commit], budget)
    chain = history.first_parent(pinned_head)
    if evidence_commit not in chain:
        raise ObservationError(REFUSED, evidence_commit,
                               "evidence is not on the pinned first-parent line")
    import _opf_store
    path = "/".join(p for p in (prefix, _opf_store.WORKING_DIRNAME,
                                _opf_store.DEFAULT_MACHINE_SUBDIR, "counters.toml") if p)
    entry = history.at(evidence_commit, path)
    if entry is None:
        raise ObservationError(CANNOT_EVALUATE, path, "required seed path does not resolve")
    mode, blob_oid = entry
    if mode not in (0o100644, 0o100755):
        raise ObservationError(REFUSED, path, "seed is not a regular blob")
    blob = history.object("blob", blob_oid)
    if source.graft_snapshot(budget) != grafts:
        raise ObservationError(CANNOT_EVALUATE, "grafts", "metadata changed during observation")
    budget.tick()
    return blob, {"commit": evidence_commit, "blob": blob_oid, "path": path,
                  "content_digest": "sha256:" + hashlib.sha256(blob).hexdigest()}
