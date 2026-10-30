"""Loader for the standards id-manifests under .aiqt/standards/ (the crosswalk's no-fabrication source
of truth). Stdlib only, offline: no network and no dependency outside the repo.

Each manifest is one external framework: a pinned edition plus the enumerated set of canonical control
ids that a rule's `map-<key>` frontmatter is permitted to cite. `gen_rules` derives its MAP_KEYS from
these files (a key exists only if its manifest does); `check_mappings` validates every mapped id against
them. An id that is not in its manifest cannot ship, so a fabricated mapping is structurally impossible.

Requires Python 3.14 or newer, the floor in .aiqt/core/python-floor.toml.
"""
import errno
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:
    import os
    if tuple(sys.version_info[:2]) < (3, 14):
        # Reached only through an importer that carries no floor guard yet (every guarded
        # entrypoint refuses an older Python that can start it first; one that cannot start
        # it fails with Python's own error before reaching here): the version is the
        # problem, so say so. The message is a literal, as the fallback grammar in
        # tools/check_python_floor.py requires (it admits no call), so it names no version.
        try:
            sys.stderr.write(
                "error: the standards loader requires Python 3.14 or newer, and this interpreter is "
                "older. Nothing was run (cannot evaluate).\n")
            sys.stderr.flush()
        except BaseException:
            pass
    else:  # every Python 3.14 ships tomllib, so this installation is incomplete
        try:
            sys.stderr.write(
                "error: the standards loader cannot import tomllib, part of the Python standard library; "
                "this installation is incomplete. Nothing was run (cannot evaluate).\n")
            sys.stderr.flush()
        except BaseException:
            pass
    os._exit(2)

# Required top-level fields in every manifest. Attribution (licence, source-url) is intentionally NOT
# required here: it is added, per-source verified, with the public NOTICE. This file's job is to make
# an id verifiable, not to assert a licence.
REQUIRED = ("map-key", "name", "publisher", "edition", "kind", "status", "citation-unit",
            "id-pattern", "source-artefact", "retrieved", "catalogue")
KINDS = {"risk", "control", "guidance", "technique", "weakness"}   # how the public page words the relation
STATUSES = {"stable", "beta", "snapshot"}                 # edition stability, surfaced as a badge
CATALOGUES = {"full", "subset"}   # is the id set the complete pinned-edition catalogue, or curated


class ManifestError(ValueError):
    """A malformed standards manifest. check_mappings maps this to exit 2 (fail-closed)."""


def _req_str(data, key, path):
    """A required non-empty string field. Fail-closed so a non-string TOML value (int, list, date,
    bool) raises ManifestError here rather than a raw TypeError deeper in (e.g. at re.compile or a set
    membership test)."""
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ManifestError("{}: field '{}' must be a non-empty string".format(path.name, key))
    return value


def natkey(text):
    """Sort key that orders embedded numbers numerically (LLM2 before LLM10, V2 before V14)."""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r'(\d+)', text)]


class Manifest:
    __slots__ = ("path", "map_key", "name", "publisher", "edition", "kind", "status",
                 "citation_unit", "id_pattern", "source_artefact", "retrieved", "catalogue", "url",
                 "ids", "id_set", "titles")

    def __init__(self, path, data):
        self.path = path
        for key in REQUIRED:
            if key not in data:
                raise ManifestError("{}: missing required field '{}'".format(path.name, key))
        self.map_key = _req_str(data, "map-key", path)
        expected = "map-" + path.stem
        if self.map_key != expected:
            raise ManifestError("{}: map-key '{}' must equal 'map-<filename>' ('{}')".format(
                path.name, self.map_key, expected))
        self.name = _req_str(data, "name", path)
        self.publisher = _req_str(data, "publisher", path)
        # edition and retrieved are display metadata: coerce a TOML scalar (a bare version int like 2026
        # or a native date) to str rather than require a quoted string, so a natural manifest is accepted.
        self.edition = str(data["edition"])
        self.kind = _req_str(data, "kind", path)
        if self.kind not in KINDS:
            raise ManifestError("{}: kind '{}' must be one of {}".format(
                path.name, self.kind, "/".join(sorted(KINDS))))
        self.status = _req_str(data, "status", path)
        if self.status not in STATUSES:
            raise ManifestError("{}: status '{}' must be one of {}".format(
                path.name, self.status, "/".join(sorted(STATUSES))))
        # catalogue declares whether the vendored id set is the COMPLETE catalogue of the pinned edition
        # at the declared citation-unit ("full") or a curated selection ("subset"). It drives the public
        # crosswalk's coverage wording: only a "full" manifest may render an "N of M" edition denominator.
        # Validated against the closed set exactly like kind/status; missing or invalid fails closed.
        self.catalogue = _req_str(data, "catalogue", path)
        if self.catalogue not in CATALOGUES:
            raise ManifestError("{}: catalogue '{}' must be one of {}".format(
                path.name, self.catalogue, "/".join(sorted(CATALOGUES))))
        self.citation_unit = _req_str(data, "citation-unit", path)
        self.source_artefact = _req_str(data, "source-artefact", path)
        self.retrieved = str(data["retrieved"])
        # url is OPTIONAL (not in REQUIRED): the canonical landing page for the standard. When present
        # it must be an https:// URL with a non-empty host; anything else fails closed here rather than
        # shipping a malformed or non-https link. Absent -> None. Reachability is a separate, non-blocking
        # audit (check_standards_urls_live.py), never checked offline here.
        url = data.get("url")
        if url is not None:
            if not isinstance(url, str):
                raise ManifestError("{}: field 'url' must be a string".format(path.name))
            if any(ch.isspace() or ord(ch) < 0x20 for ch in url):
                raise ManifestError(
                    "{}: field 'url' must not contain whitespace or control characters".format(path.name))
            # Parse and validate the port inside the guard: urlparse (and .port on a bad port) can raise a
            # raw ValueError, which fails closed here as a ManifestError rather than crashing. Residual: this
            # is a best-effort offline check for scheme + host + parseable authority + valid port + no
            # whitespace/control; it does not confirm the host resolves or is the right page (the separate
            # non-blocking check_standards_urls_live.py audits reachability), nor every RFC-3986 edge.
            try:
                parsed = urlparse(url)
                _ = parsed.port
            except ValueError:
                raise ManifestError("{}: field 'url' is not a parseable URL".format(path.name))
            if parsed.scheme != "https" or not parsed.hostname:
                raise ManifestError(
                    "{}: field 'url' must be an https:// URL with a host".format(path.name))
        self.url = url
        try:
            self.id_pattern = re.compile(_req_str(data, "id-pattern", path))
        except re.error as exc:
            raise ManifestError("{}: id-pattern is not a valid regex: {}".format(path.name, exc))
        rows = data.get("id")
        if not isinstance(rows, list) or not rows:
            raise ManifestError("{}: at least one [[id]] entry is required".format(path.name))
        self.ids, self.titles = [], {}
        for row in rows:
            if not isinstance(row, dict):
                raise ManifestError("{}: every [[id]] must be a table".format(path.name))
            code = row.get("code")
            if not isinstance(code, str) or not code:
                raise ManifestError("{}: every [[id]] needs a non-empty string 'code'".format(path.name))
            if not self.id_pattern.fullmatch(code):
                raise ManifestError("{}: id '{}' does not match id-pattern {!r}".format(
                    path.name, code, self.id_pattern.pattern))
            if code in self.titles:
                raise ManifestError("{}: duplicate id '{}'".format(path.name, code))
            title = row.get("title", "")
            if not isinstance(title, str):
                raise ManifestError("{}: title for '{}' must be a string".format(path.name, code))
            self.titles[code] = title
            self.ids.append(code)
        if self.ids != sorted(self.ids, key=natkey):
            raise ManifestError("{}: [[id]] entries must be in natural-sorted order".format(path.name))
        self.id_set = set(self.ids)


# The probe primitive, named so a self-test can refuse it without filesystem permissions (a
# chmod-based unreadable-parent fixture is invisible to root); the check_rule_placement.py self-test
# patches it.
_lstat = os.lstat

# The link resolution primitives the walk uses on a symlink it meets, bound at import as _lstat is, so a
# self-test that stands in for the os module inside a probe module still sees every call the walk makes.
_realpath = os.path.realpath
_readlink = os.readlink

# The dir_fd capability, bound at import so it reflects the platform, not a later rebinding of an os
# attribute (a self-test may wrap os.open); _adapter_compose binds its _DIR_FD_REQUIRES the same way.
_DIR_FD_OK = (os.open in os.supports_dir_fd and os.lstat in os.supports_dir_fd
              and os.readlink in os.supports_dir_fd)

# The search-only open flag for a search_only walk, bound at import for the same reason: O_PATH where
# the platform has it (a directory opened for search needs no read permission), else 0, so the walk
# opens for reading as the default probe does. A symlink is read through an O_PATH descriptor for the
# link itself, so where the platform has no O_PATH every symlink is refused (_read_link).
_SEARCH = getattr(os, "O_PATH", 0)

# Every directory the walk opens below its root, and every directory a symlink's target is walked
# through, is opened beneath its parent's descriptor with these flags (search added where it applies).
_STEP_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

# The most symlinks one resolution may meet, the kernel's own bound (Linux MAXSYMLINKS); one more is a
# loop, refused as the kernel refuses it.
_MAX_LINKS = 40


def dir_present(path, *, root, search_only=False):
    """Fail-closed three-way directory probe. Return True only when every inspected component is a
    real directory by lstat, or a symlink that resolves to a real directory inside root (read as that
    directory, as dir_or_absent reads it). Return False only on GENUINE absence: no directory entry
    at an inspected component (FileNotFoundError; a dangling symlink is a present entry, so it never
    reads as absent). Anything else RAISES, so a caller can never read 'cannot tell' or 'wrong type'
    as absent. An unreachable path raises OSError (an unreadable ancestor such as a `chmod 0` .aiqt/
    parent: Path.is_dir()/exists() SWALLOW EACCES and return False, so an unreadable input dir would
    read as an absent, transition-safe input and pass clean; the probe raises on EACCES, from lstat
    or from opening a directory it must walk through). An inspected entry that EXISTS but cannot be
    read as a directory raises NotADirectoryError (an OSError, so the callers' existing fail-closed
    handlers refuse it): a regular file, a FIFO, a device, or a symlink that is dangling, whose
    target is not a directory, or whose target resolves outside root. A symlink is never resolved by
    name: it is read beneath the descriptor of the directory holding it, through a descriptor for the
    link itself whose st_dev and st_ino must equal the lstat that judged it, and its target is walked
    beneath the descriptors the walk holds (_follow): a relative target from that directory, '..'
    stepping back to the held parent and never above root (a target that climbs above root is
    refused, even one that names root again on its way back in), an absolute one from root's own
    descriptor once it lies lexically beneath root as named or as resolved, a component that names a
    directory the walk already holds (by the name it opened it under) read through that descriptor,
    every other component judged and opened as below, and every symlink met on the way read the same
    way. A target that is missing at any component reads as a dangling symlink, never as absence;
    one that meets more than 40 symlinks raises OSError (ELOOP).

    root is required and keyword-only: the directory the caller already trusts (its resolved
    repository or --root directory, or a directory it just created), which path must lie beneath.
    root itself is opened as the caller named it; EVERY component of path below root is inspected,
    each bound beneath the previous component's directory descriptor (lstat with dir_fd, then
    os.open with O_DIRECTORY|O_NOFOLLOW), so a wrong-type entry, or a symlink that is dangling or
    resolves to a non-directory or outside root, at ANY ancestor (a symlinked .claude or .aiqt
    included) is refused, never followed and never read as absent, and a real directory swapped for
    a symlink between its lstat and its open is refused by the open, while a genuinely absent
    ancestor still reads as absence. A component whose lstat found it but which is gone by its open
    (removed or renamed in between) raises OSError naming it: the tree changed under the probe,
    which is never absence. Every component the walk opens must also be the directory its lstat
    judged, its descriptor's st_dev and st_ino equal to that lstat's, so one replaced by another
    real directory in between (which O_NOFOLLOW admits) raises OSError naming it too, never walked
    into: its contents, or its lack of the next component read as absence, never stand in for the
    tree judged. There is no form without a root: a probe that judged only the final component
    would trust, and so follow, every ancestor, and read a dangling one as absence.
    A path that is not beneath root (or that climbs with '..') raises OSError (EINVAL), so the
    callers' handlers refuse it too. The probe judges the tree only while it runs: a later read of
    the same path by name is not bound to it (open_dir returns a descriptor to read through
    instead). Pairs with ensure_listable: dir_present catches an unreachable or wrong-type entry,
    ensure_listable catches a reachable-but-unlistable directory.

    search_only opens root and every directory the walk passes through for search only (O_PATH where
    the platform has it), a directory a symlink's target passes through or names included, so a
    traversable but unreadable directory on the way does not raise; the default opens them for
    reading, so such a directory raises EACCES. Every judgement is the same either way: each
    component is judged by lstat, and O_PATH with O_DIRECTORY|O_NOFOLLOW refuses a symlink as
    O_RDONLY does."""
    return _walk(path, root, _SEARCH if search_only else 0, False) is not None


def open_dir(path, *, root, search_only=False):
    """Probe path as dir_present does and return a descriptor for it, opened for reading, which the
    caller owns and closes; return None on genuine absence. Every refusal dir_present makes, this
    makes, and path must lie strictly beneath root (root itself raises EINVAL). The final component
    is judged by lstat like every other, then opened beneath its parent's descriptor with
    O_DIRECTORY|O_NOFOLLOW, and the opened directory's st_dev and st_ino must equal that lstat's,
    as every directory the walk opens below root must: a symlink swapped in between the judgement
    and the open is refused by the open, another directory swapped in, at path or at any ancestor
    below root, raises OSError naming it, and so does a final directory removed in between, so the
    descriptor is always the directory the probe judged, reached through the ancestors it judged,
    and its absence is never read from a changed tree. A caller that lists and reads through it
    (os.scandir on the descriptor, os.open with dir_fd) is then bound to that directory whatever
    later happens to its name. search_only opens root and the directories the walk passes through
    as dir_present's does; the final directory is opened for reading either way, since its caller
    lists it (one a symlink names, root included, is opened again for reading beneath the
    descriptor that holds it)."""
    return _walk(path, root, _SEARCH if search_only else 0, True)


def open_chain(path, *, root):
    """open_dir, returning every descriptor its walk holds rather than only the last: the list of
    (descriptor, name) pairs from root's own (named "") down to path's, each directory the physical
    parent of the next and each name the one the next was opened under beneath it (a symlink on the
    way is read as the directory it names, so the list runs through that directory's real parents),
    all opened for reading; None on genuine absence. The caller owns and closes every one
    (close_chain). A caller that walks on below path extends the list with each directory it opens
    beneath the last, by its name, and resolves a symlink it lists there with follow_link beneath
    the list, never by name from root."""
    return _walk(path, root, 0, _CHAIN)


def close_chain(chain):
    """Close the descriptor of every (descriptor, name) pair in the list chain, the last first, as
    open_chain and follow_link return them to their caller."""
    for held, _name in reversed(chain):
        os.close(held)


def follow_link(chain, name, judged, path, root):
    """Resolve the symlink name listed beneath chain[-1], judged by judged (its lstat, as the
    caller's listing took it), where chain is the (descriptor, name) list open_chain returned for
    root, or one extended from it: (kept, new), the directory the link names being the last of
    chain[:kept] + new, every one opened for reading, new a list of pairs opened here and owned by
    the caller (close_chain). Anything but a symlink to a real directory inside root raises
    NotADirectoryError naming path, and a link replaced since judged raises OSError naming it
    (_follow)."""
    kept, new, new_names = _follow([held for held, _step in chain], [step for _held, step in chain],
                                   name, judged, path, Path(root), 0)
    return kept, list(zip(new, new_names))


# The hold that makes _walk return every descriptor it holds (open_chain) rather than the last.
_CHAIN = "chain"


def _refused(path):
    """The refusal of a symlink that cannot be read as a directory inside the root, naming path."""
    return NotADirectoryError(errno.ENOTDIR, "a symlink where a directory is required", str(path))


def _read_link(dir_fd, name, judged, path):
    """The target of the symlink name beneath dir_fd, read through a descriptor for the link itself
    (O_PATH|O_NOFOLLOW) whose st_dev and st_ino must equal judged's (the lstat that judged it), so a
    link replaced in between (by another link or anything else) raises OSError(ESTALE) naming path,
    and one removed in between raises the same, never read as absence. Where the platform has no
    O_PATH a link cannot be read bound to its judgement, so it raises OSError(ENOSYS) naming path."""
    if not _SEARCH:
        raise OSError(errno.ENOSYS, "reading a symlink bound to the lstat that judged it (O_PATH) is "
                      "unavailable on this platform, so it is never followed", str(path))
    try:
        link_fd = os.open(name, _SEARCH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        raise OSError(errno.ESTALE, "the symlink the probe judged is gone (removed or renamed in "
                      "between)", str(path))
    try:
        opened = os.fstat(link_fd)
        if not stat.S_ISLNK(opened.st_mode) or (opened.st_dev, opened.st_ino) != (judged.st_dev,
                                                                                 judged.st_ino):
            raise OSError(errno.ESTALE, "the symlink opened is not the one the probe judged (it was "
                          "replaced in between)", str(path))
        return _readlink("", dir_fd=link_fd)
    finally:
        os.close(link_fd)


def _beneath(target, base):
    """The components of the absolute symlink target below the probe root base, as named (made
    absolute) or as resolved, whichever it lies lexically beneath; None when it lies beneath neither,
    which _follow refuses as outside root. The root's path is resolved by name when the link is
    followed, and decides only whether the target is taken as beneath root, never what is read: the
    components returned are walked beneath root's held descriptor."""
    parts = Path(target).parts
    for named in (base.absolute(), Path(_realpath(base))):
        if parts[:len(named.parts)] == named.parts:
            return parts[len(named.parts):]
    return None


def _follow(fds, names, name, judged, path, base, search):
    """Resolve the symlink name, judged by lstat beneath fds[-1], where fds holds the descriptors of
    the directories from the probe root base (fds[0]) down to it, each the physical parent of the
    next, and names[i] is the name fds[i] was opened under beneath fds[i - 1]. The link is read
    through a descriptor for itself (_read_link), and its target is walked beneath held descriptors
    only, never by name from the root: a relative target from fds[-1], an absolute one from fds[0]
    once it lies lexically beneath base (_beneath); '..' steps back to the held parent and never
    above fds[0]; a component naming, beneath a held directory, the next directory held below it (by
    the name it was opened under) is that held directory, never looked up again; every symlink met
    on the way is read the same way (at most _MAX_LINKS in all); and every other directory reached
    is judged by lstat, opened beneath its parent's descriptor with O_DIRECTORY|O_NOFOLLOW (search
    added) and checked by st_dev and st_ino against that lstat. Return (kept, new, new_names): the
    directory the link names is the last of fds[:kept] + new, new opened here, each under the name
    at the same place in new_names, and owned by the caller. A target missing at any component,
    climbing above the root, lying outside it or naming a non-directory raises NotADirectoryError
    naming path; a directory replaced or removed between its lstat and its open raises
    OSError(ESTALE) naming path. Nothing it opens is left open when it raises."""
    kept, new, new_names, spare = len(fds), [], [], []
    pending = []
    links = 0
    target = _read_link(fds[-1], name, judged, path)
    try:
        while target is not None:
            links += 1
            if links > _MAX_LINKS:
                raise OSError(errno.ELOOP, "more symlinks than one resolution may meet", str(path))
            if not target:
                raise _refused(path)
            parts = Path(target).parts
            if Path(target).is_absolute():
                parts = _beneath(target, base)
                if parts is None:
                    raise _refused(path)
                kept = 1
                spare.extend(new)
                del new[:], new_names[:]
            pending.extend(reversed(parts))
            target = None
            while pending and target is None:
                part = pending.pop()
                if part == "..":
                    if new:
                        spare.append(new.pop())
                        new_names.pop()
                    elif kept > 1:
                        kept -= 1
                    else:
                        raise _refused(path)
                    continue
                if not new and kept < len(fds) and part == names[kept]:
                    # The next directory the walk already holds, by the name it was opened under:
                    # read through that descriptor, so a swap of it after its open changes nothing.
                    kept += 1
                    continue
                here = new[-1] if new else fds[kept - 1]
                try:
                    st = _lstat(part, dir_fd=here)
                except FileNotFoundError:
                    raise _refused(path)
                if stat.S_ISLNK(st.st_mode):
                    # Read beneath the directory holding it; its target is walked from there.
                    target = _read_link(here, part, st, path)
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    raise _refused(path)
                try:
                    new.append(os.open(part, _STEP_FLAGS | search, dir_fd=here))
                except FileNotFoundError:
                    raise OSError(errno.ESTALE, "a directory the symlink's target names is gone "
                                  "(removed or renamed in between)", str(path))
                new_names.append(part)
                reached = os.fstat(new[-1])
                if (reached.st_dev, reached.st_ino) != (st.st_dev, st.st_ino):
                    raise OSError(errno.ESTALE, "a directory the symlink's target names is not the one "
                                  "the probe judged (it was replaced in between)", str(path))
    except BaseException:
        spare.extend(new)
        del new[:]
        raise
    finally:
        for gone in reversed(spare):
            os.close(gone)
    return kept, new, new_names


def _walk(path, root, search, hold):
    """The walk dir_present, open_dir and open_chain share. Return None on genuine absence;
    otherwise True, or with hold the final directory's descriptor, opened for reading whatever
    search is, the only one it leaves open, or with hold _CHAIN every descriptor it holds, root's
    first, each paired with its name (open_chain). Every directory it opens below root is checked by
    st_dev and st_ino against the lstat that judged it; root itself is opened as named. A symlink at
    a component is resolved beneath the descriptors the walk holds (_follow) and read as the
    directory it names, when that is a real directory inside root; any other symlink is refused."""
    path = Path(path)
    base = Path(root)
    try:
        parts = path.relative_to(base).parts
    except ValueError:
        parts = None
    if parts is None or ".." in parts or (hold and not parts):
        raise OSError(errno.EINVAL, "not a path beneath the probe root {}".format(base), str(path))
    if not _DIR_FD_OK:
        raise OSError(
            errno.ENOSYS, "directory probing bound beneath a descriptor (os.open, os.lstat and "
            "os.readlink with dir_fd) is unavailable on this platform", str(path))
    walked = base
    fds = []
    names = [""]   # the name each directory in fds was opened under beneath the one before it
    spare = []
    linked = False
    try:
        try:
            fds.append(os.open(str(base), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | search))
            for i, name in enumerate(parts):
                walked = walked / name
                st = _lstat(name, dir_fd=fds[-1])
                linked = stat.S_ISLNK(st.st_mode)
                if linked:
                    # The directories '..' in its target steps back past are no longer on the way;
                    # they are closed with the rest when the walk ends.
                    kept, new, new_names = _follow(fds, names, name, st, walked, base, search)
                    spare.extend(fds[kept:])
                    fds[kept:] = new
                    names[kept:] = new_names
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    raise NotADirectoryError(
                        errno.ENOTDIR, "a non-directory entry where a directory is required", str(walked))
                if hold or i + 1 < len(parts):
                    # The next component is judged beneath THIS component's descriptor; O_NOFOLLOW
                    # refuses even a symlink swapped in between the lstat above and this open. The
                    # final component is opened only when it is held (for reading, as its caller
                    # lists it): otherwise its lstat is the whole judgement.
                    try:
                        fds.append(os.open(name, _STEP_FLAGS | (search if i + 1 < len(parts) else 0),
                                           dir_fd=fds[-1]))
                    except FileNotFoundError:
                        # The lstat above found this entry, so it is gone only because the tree
                        # changed in between: that is never absence. Not ENOENT, which the handler
                        # below would read as absence.
                        raise OSError(errno.ESTALE, "the entry the probe judged is gone (removed or "
                                      "renamed in between)", str(walked))
                    # O_NOFOLLOW admits another real directory swapped in between the lstat above
                    # and this open, so every directory opened, an ancestor as well as the held final
                    # component, must be the one judged: otherwise the walk would go on inside the
                    # replacement, reading its tree, or its missing child as absence.
                    opened = os.fstat(fds[-1])
                    if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                        raise OSError(errno.ESTALE, "the directory opened is not the one the probe "
                                      "judged (it was replaced in between)", str(walked))
                    names.append(name)
        except FileNotFoundError:
            return None
        if hold and linked and search:
            # The final directory was named by a symlink, so it is held by a search-only descriptor
            # (its own, or root's or an ancestor's when the target names one): open it again for
            # reading through '.' beneath that descriptor, which names that directory and no other.
            try:
                fds.append(os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=fds[-1]))
            except FileNotFoundError:
                raise OSError(errno.ESTALE, "the directory the symlink names is gone (removed in "
                              "between)", str(walked))
        if hold == _CHAIN:
            held = list(zip(fds, names))
            del fds[:]
            return held
        return fds.pop() if hold else True
    finally:
        for fd in reversed(fds + spare):
            os.close(fd)


def std_root(std):
    """Return (root, std_dir) for an explicit --std DIR, after probing DIR beneath root: root is the
    parent of the directory above DIR (an explicit DIR has no known repository root, so that
    directory stands for it, as load_manifests' callers judge <root>/.aiqt/standards) and std_dir is
    DIR, which the caller passes to load_manifests with search_only=True, so the loader walks DIR
    beneath the same root again and reads only through the descriptor it holds. DIR is first walked
    here by dir_present, and a DIR that is itself a symlink, or that has one at a component below
    root, is read as the directory it names when that is a real directory inside root; one that is
    dangling, or whose target is not a directory or resolves outside root (a live link to a
    standards directory elsewhere included), or a non-directory at ANY component below root, raises
    NotADirectoryError naming that component, never followed; the loader's own walk refuses such a
    component swapped in after this one the same way. An absent DIR reads as absent here, and the
    loader then finds no manifests, which its callers refuse. A relative DIR is taken beneath
    os.getcwd(), and a DIR that climbs with '..' raises OSError (EINVAL), as dir_present refuses any
    '..'. Both walks are search_only, so a real DIR beneath a traversable but unreadable directory
    still works where the platform has O_PATH, and raises EACCES (fail-closed) where it does not."""
    std = Path(std)
    named = std if std.is_absolute() else Path(os.getcwd()) / std
    if ".." in named.parts:
        raise OSError(errno.EINVAL, "not a path beneath the probe root", str(named))
    root = named.parent.parent
    dir_present(named, root=root, search_only=True)
    return root, named


def _std_root_vectors(launch, base, today, failures):
    """Self-test vectors for a tool's explicit --std, shared by check_standards_currency.py and
    check_standards_urls_live.py so both hold the same contract. launch(argv) runs the tool's main
    and returns (exit code, stderr text); base is a fresh writable directory; today dates the fixture
    manifest (a current, url-free manifest, so no vector reaches the network). A real --std exits 0,
    also beneath a traversable but unreadable directory where the platform has O_PATH; a --std that
    is a live symlink to a directory inside its root (std_root's), or sits beneath such a symlinked
    ancestor, is read and exits 0. A --std that holds no manifest and is swapped for such a symlink
    right after std_root's probe judged it (a hook on the probe primitive) is read through the
    loader's own walk and exits 0: the directory it names holds two manifests, and right after the
    loader validates the first (a hook on its manifest class) that directory is moved aside and one
    holding a malformed copy of the second put at its name, so a loader that read the second by name
    would exit 2 where one reading through the descriptor it holds does not. A --std that is a dangling
    symlink, or a live symlink to a directory outside its root, exits 2 naming the symlink, and so
    does one swapped for such a symlink right after std_root's probe (the loader refuses it naming
    it, with the walk's refusal or dir_or_absent's reason). Each failure is appended to failures as
    one line."""
    global _lstat, Manifest
    base = Path(base).resolve()   # the fixture's own ancestors must hold no symlink
    body = ('map-key = "map-cur"\nname = "Self-test cur"\npublisher = "AIQT self-test"\n'
            'edition = "2026"\nkind = "control"\nstatus = "stable"\ncitation-unit = "control"\n'
            'id-pattern = "ST[0-9]{{2}}"\nsource-artefact = "self-test fixture"\nretrieved = "{}"\n'
            'catalogue = "full"\n[[id]]\ncode = "ST01"\ntitle = "one"\n').format(today.isoformat())
    real = base / "std-real"
    opaque = base / "std-opaque"
    swapped = base / "std-swapped"
    swap_target = base / "std-swap-target"
    for directory in (real, opaque / "standards", base / "std-ancestor-real" / "standards"):
        directory.mkdir(parents=True)
        (directory / "cur.toml").write_text(body, encoding="utf-8")
    swap_target.mkdir()
    for stem in ("a", "b"):
        (swap_target / (stem + ".toml")).write_text(
            body.replace('"map-cur"', '"map-{}"'.format(stem)), encoding="utf-8")
    # A --std two levels below base, so its root (std_root's) is base / "nest" and real lies outside it.
    out_link = base / "nest" / "inner" / "std-out-link"
    swapped_out = base / "nest" / "inner" / "std-swapped-out"
    out_link.parent.mkdir(parents=True)
    for directory in (swapped, swapped_out):
        directory.mkdir()
    try:
        (base / "std-link").symlink_to(real)
        (base / "std-dangling").symlink_to(base / "no-such-target")
        (base / "std-ancestor-link").symlink_to(base / "std-ancestor-real")
        out_link.symlink_to(real)
    except OSError as exc:
        failures.append("NOT RUN: the --std symlink vectors: cannot create a symlink ({})".format(exc))
        return
    real_lstat = _lstat
    real_manifest = Manifest

    def _swapping_lstat(target, link_to):
        # Judge target as it is, then, once, move it aside and link its name to link_to.
        moved = []

        def probe(name, dir_fd=None):
            st = real_lstat(name, dir_fd=dir_fd)
            if name == target.name and not moved:
                moved.append(target.rename(target.with_name(target.name + "-judged")))
                target.symlink_to(link_to)
            return st
        return probe

    def _swapping_manifest():
        # The loader's manifest class, which, once, right after the first manifest is validated,
        # moves the swap target aside and puts at its name a directory holding a malformed b.toml.
        moved = []

        class SwappingManifest(real_manifest):
            def __init__(self, path, data):
                super().__init__(path, data)
                if not moved:
                    moved.append(swap_target.rename(swap_target.with_name(swap_target.name + "-judged")))
                    swap_target.mkdir()
                    (swap_target / "b.toml").write_text("not a manifest [\n", encoding="utf-8")
        return SwappingManifest

    def _refusal(path):
        return ("a symlink where a directory is required: '{}'".format(path),)

    opaque.chmod(0o711)
    try:
        # (vector, --std, expected exit, the refusals one of which the error must hold, the probe
        # primitive, the loader's manifest class)
        for vector, std, want, refusals, probe, manifest in (
                ("real --std directory", real, 0, None, real_lstat, real_manifest),
                ("real --std beneath a traversable but unreadable directory", opaque / "standards",
                 0 if _SEARCH else 2, None, real_lstat, real_manifest),
                ("--std that is a live symlink to a directory inside its root", base / "std-link", 0, None,
                 real_lstat, real_manifest),
                ("--std that is a live symlink to a directory outside its root", out_link, 2,
                 _refusal(out_link), real_lstat, real_manifest),
                ("--std that is a dangling symlink", base / "std-dangling", 2,
                 _refusal(base / "std-dangling"), real_lstat, real_manifest),
                ("--std beneath a symlinked ancestor inside its root", base / "std-ancestor-link" / "standards",
                 0, None, real_lstat, real_manifest),
                ("--std swapped for a symlink inside its root after its probe, its target swapped after "
                 "the loader's open", swapped, 0, None, _swapping_lstat(swapped, swap_target),
                 _swapping_manifest()),
                ("--std swapped for a symlink outside its root after its probe", swapped_out, 2,
                 _refusal(swapped_out) + ("{} {}".format(swapped_out, LINK_OUTSIDE_ROOT),),
                 _swapping_lstat(swapped_out, real), real_manifest)):
            _lstat, Manifest = probe, manifest
            try:
                code, err = launch(["--std", str(std)])
            finally:
                _lstat, Manifest = real_lstat, real_manifest
            if code != want or (refusals is not None and not any(text in err for text in refusals)):
                failures.append("{}: expected exit {}{}, got exit {} ({!r})".format(
                    vector, want, "" if refusals is None else " holding one of " + repr(refusals), code,
                    err))
    finally:
        opaque.chmod(0o755)


# The reasons dir_or_absent gives for something there that cannot be read as the directory.
NOT_A_DIRECTORY = "is not a directory"
LINK_DANGLING = "is a symbolic link whose target does not exist"
LINK_NOT_A_DIRECTORY = "is a symbolic link whose target is not a directory"
LINK_OUTSIDE_ROOT = "is a symbolic link whose target resolves outside the repository"


def _inside(real, real_root):
    """True when the resolved path `real` is the resolved root `real_root` or under it."""
    try:
        return os.path.commonpath([real, real_root]) == real_root
    except ValueError:
        return False


def dir_or_absent(path, root):
    """Three-way fail-closed probe for an input that must be a directory or be absent, under the repository
    root `root`. Return (True, None) for a directory, (False, None) when nothing is there, and (False, reason)
    when something is there that cannot be read as that directory: a regular file or other non-directory, or
    a symbolic link that is dangling, whose target is not a directory, or whose target resolves outside root.
    A symbolic link whose target is an existing directory inside root is a directory, as dir_present also
    reads it (dir_present, and so open_dir, refuses one whose target climbs above root on its way, even
    to come back in): the link is resolved once (os.path.realpath, strict), and that one resolved path is
    the path tested against the resolved root and, with os.lstat, as a directory. RAISE OSError when the
    path cannot be examined other than by absence (an unreadable ancestor, an ancestor that is not a
    directory, or a
    symbolic link loop), as dir_present does. dir_present raises NotADirectoryError on each such reason
    instead of giving it; a caller that must name why it cannot evaluate an input refuses the reason given
    here as cannot-evaluate. Only the last
    component is judged: an ancestor symlink is followed (dir_chain_or_absent judges every component). The
    caller reads the tree through path, so a link retargeted between this probe and that read (by another
    writer at the same moment) is not detected."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return (False, None)
    if not stat.S_ISLNK(st.st_mode):
        if not stat.S_ISDIR(st.st_mode):
            return (False, NOT_A_DIRECTORY)
        return (True, None)
    try:
        real = os.path.realpath(path, strict=True)
    except FileNotFoundError:
        return (False, LINK_DANGLING)
    if not _inside(real, os.path.realpath(root)):
        return (False, LINK_OUTSIDE_ROOT)
    if not stat.S_ISDIR(os.lstat(real).st_mode):
        return (False, LINK_NOT_A_DIRECTORY)
    return (True, None)


def dir_chain_or_absent(path, root):
    """dir_or_absent for every directory component of path under root, from the first one below root down to
    path itself, so a symbolic link at any depth is judged, not only the last component. A tool that writes,
    deletes or reads a generated file probes the directory holding it this way before the first such access.
    Each link met is followed only to a directory inside root, so the directory reached is inside root too.
    Return (True, None) when every component is a directory, (False, None) at the first absent one (nothing
    below it is there), and (False, reason) at the first one that is there but cannot be read as a directory;
    the reason names that component when it is above path. A path not lexically under root, or holding a ..
    component, is (False, reason) as well. RAISE OSError as dir_or_absent does. dir_or_absent's caveat about a
    link retargeted between the probe and the read applies to every component."""
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        return (False, "is not under the repository root {}".format(root))
    if ".." in parts:
        return (False, "holds a .. component")
    current = Path(root)
    for depth, part in enumerate(parts, 1):
        current = current / part
        present, problem = dir_or_absent(current, root)
        if problem is not None:
            if depth < len(parts):
                problem = "has a directory component {} that {}".format(current, problem)
            return (False, problem)
        if not present:
            return (False, None)
    return (True, None)


def ensure_listable(directory):
    """Fail-closed on a directory that EXISTS but cannot be listed. Path.glob/rglob SILENTLY yield
    nothing on an unreadable directory rather than raising, so an unlistable input dir would read as
    "empty" and pass clean. os.scandir does raise, so force it. An absent dir is fine (callers treat
    absent as an empty, transition-safe set); only an existing-but-unlistable dir raises OSError.
    Shared so every tool that scans a required dir with glob/rglob fails closed the same way."""
    if directory.is_dir():
        list(os.scandir(directory))


def _check_stem(path):
    """Reject a manifest whose filename stem ends in the reserved fit suffix. A `foo-broad.toml` would
    derive the key `map-foo-broad`, colliding with `foo.toml`'s broad fit key `map-foo-broad`; forbid
    the stem so the fit suffix stays unambiguous. Called on BOTH read paths (map_keys per path, before
    deriving keys, and load_manifests before parsing), because map_keys deliberately does not parse a
    manifest, so a guard only in Manifest.__init__ would let a colliding stem poison MAP_KEYS at
    gen_rules import before load_manifests ever runs."""
    if path.stem.endswith(("-tight", "-broad")):
        raise ManifestError(
            "{}: manifest stem may not end in -tight/-broad (reserved fit suffix)".format(path.name))


def _manifest_names(dir_fd):
    """The *.toml entry names of the standards directory held at dir_fd, sorted, listed through the
    descriptor (a listing error raises OSError, never an empty set)."""
    with os.scandir(dir_fd) as entries:
        return sorted(entry.name for entry in entries if entry.name.endswith(".toml"))


# A manifest is opened beneath the held standards directory without following a link; O_NONBLOCK
# keeps a FIFO at a manifest's name from blocking the open (the fstat check then refuses it).
_MANIFEST_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _read_manifest(dir_fd, path):
    """The bytes of the manifest path, read beneath dir_fd only when fstat on the opened descriptor
    finds a regular file; anything else, a symlink included, raises OSError naming path."""
    try:
        manifest_fd = os.open(path.name, _MANIFEST_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, str(path)) from exc
    try:
        if not stat.S_ISREG(os.fstat(manifest_fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file where a manifest is required", str(path))
        with open(manifest_fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(manifest_fd)


def _standards_present(std_dir, root):
    """dir_or_absent for the standards dir under root, raising (OSError) where something is there that
    cannot be read as the directory, so it is never read as no manifests; every caller already renders an
    OSError as exit 2."""
    present, problem = dir_or_absent(std_dir, root)
    if problem is not None:
        raise NotADirectoryError("{} {} (cannot evaluate)".format(std_dir, problem))
    return present


def load_manifests(std_dir, *, root, search_only=False):
    """Return {map_key: Manifest} for every *.toml under std_dir, fully validated. Raise ManifestError
    on any malformed manifest or duplicate map-key. An absent/empty dir returns {} (transition-safe); an
    existing dir that cannot be listed, or a std_dir that is there but cannot be read as a directory
    (dir_or_absent: a regular file, or a symlink that is dangling, whose target is not a directory or
    resolves outside root), raises OSError (fail-closed), never a false-clean empty; that refusal names
    the reason. std_dir must lie strictly beneath root (required: the repository root for every caller
    that passes <root>/.aiqt/standards; for the --std DIR of check_standards_currency and
    check_standards_urls_live, std_root's root, the parent of the directory above DIR) and is then held
    by open_dir beneath it (search_only as there; std_root's callers pass True), so a .aiqt (or
    standards) that is dangling, or a symlink whose target is not a directory or resolves outside root,
    raises rather than being followed or reading as an absent, empty manifest set, while one that is a
    symlink to a directory inside root is read as that directory. The listing and every manifest are
    then read only through the descriptor open_dir returns, each manifest opened beneath it with
    O_NOFOLLOW, so a component swapped for a symlink or another directory between open_dir's judgement
    of it and its open is refused (OSError naming it), never followed, and a manifest that is a
    symlink or not a regular file raises OSError naming it."""
    std_dir = Path(std_dir)
    out = {}
    _standards_present(std_dir, root)
    std_fd = open_dir(std_dir, root=root, search_only=search_only)
    if std_fd is None:
        return out
    try:
        for name in _manifest_names(std_fd):
            path = std_dir / name
            _check_stem(path)
            try:
                data = tomllib.loads(_read_manifest(std_fd, path).decode("utf-8"))
            # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
            # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
            # on a deeply nested array or inline table; neither is a TOMLDecodeError. Content that is not
            # UTF-8 raises a ValueError too.
            except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
                raise ManifestError("{}: invalid TOML: {}".format(path.name, exc))
            manifest = Manifest(path, data)
            if manifest.map_key in out:
                raise ManifestError("{}: map-key '{}' already defined by {}".format(
                    path.name, manifest.map_key, out[manifest.map_key].path.name))
            out[manifest.map_key] = manifest
    finally:
        os.close(std_fd)
    return out


def map_keys(root):
    """The derived set of valid `map-<key>` frontmatter keys: the two fit-qualified keys
    `map-<stem>-tight` and `map-<stem>-broad` per manifest filename under .aiqt/standards/, and nothing
    else. A bare `map-<stem>` (no fit) or a bad suffix (`map-<stem>-medium`) is therefore not a legal
    frontmatter key, rejected fail-closed by gen_rules._check_keys with no extra gate code. Cheap (no
    parse) so gen_rules can build MAP_KEYS at import; check_mappings does the full parse/validation via
    load_manifests. .aiqt and standards are both probed beneath root, so a .aiqt (or standards) that
    is dangling, or a symlink whose target is not a directory or resolves outside root, raises rather
    than reading as an absent, transition-safe key set (a .aiqt/standards that is there but cannot be
    read as a directory raises naming the reason, as load_manifests does), and the names are listed
    through the descriptor open_dir returns, as load_manifests lists them."""
    root = Path(root)
    std_dir = root / ".aiqt" / "standards"
    _standards_present(std_dir, root)
    std_fd = open_dir(std_dir, root=root)
    if std_fd is None:
        return set()
    try:
        names = _manifest_names(std_fd)
    finally:
        os.close(std_fd)
    keys = set()
    for name in names:
        path = std_dir / name
        _check_stem(path)
        keys.add("map-" + path.stem + "-tight")
        keys.add("map-" + path.stem + "-broad")
    return keys


def validate_mappings(corpus, manifests):
    """Shared C4 / check_mappings id-validation loop. Returns (findings, mapped, tight, broad).

    Each rule's `map-<fw>-tight` / `map-<fw>-broad` frontmatter list is validated against its
    fit-stripped BASE manifest (`map-<fw>`): id-pattern match, membership in the pinned id set,
    per-list duplicate, and per-list natural-sort. Two fit-notation checks sit on top of the bare-key
    loop this replaces:
      - a map key must carry a `-tight`/`-broad` fit suffix (a bare or bad-suffix key is a finding,
        defence in depth for a hand-edited tree, mirroring the no-manifest guard);
      - MUTUAL EXCLUSIVITY: per rule+framework, an id may not be asserted both tight and broad.
    tight and broad ids are tallied separately so a caller can report the fit split."""
    findings = []
    mapped = tight = broad = 0
    for src, fm, _rel in corpus:
        # base map-key -> {"tight": [...], "broad": [...]} for this rule's mutual-exclusivity check.
        per_base = {}
        for key in sorted(k for k in fm if k.startswith("map-")):
            ids = fm[key]
            if not isinstance(ids, list):
                findings.append("{}: {}: value must be a flow sequence".format(src.name, key))
                continue
            mapped += len(ids)
            if not key.endswith(("-tight", "-broad")):
                # Unreachable while MAP_KEYS emits only fit-suffixed keys (load_corpus would reject a
                # bare key first); kept as a guard against a hand-edited or out-of-sync tree.
                findings.append("{}: {}: map key must carry a -tight/-broad fit suffix".format(
                    src.name, key))
                continue
            base = key[:-6]                # strip the 6-char fit suffix ("-tight"/"-broad")
            fit = key[-5:]                 # "tight" or "broad"
            if fit == "tight":
                tight += len(ids)
            else:
                broad += len(ids)
            per_base.setdefault(base, {"tight": [], "broad": []})[fit] = ids
            manifest = manifests.get(base)
            if manifest is None:
                # Unreachable while MAP_KEYS is derived from the same manifests; kept as a guard
                # against a hand-edited or out-of-sync tree.
                findings.append("{}: {}: no manifest under .aiqt/standards/ for this key".format(
                    src.name, key))
                continue
            if len(ids) != len(set(ids)):
                dupes = sorted({c for c in ids if ids.count(c) > 1})
                findings.append("{}: {}: duplicate id(s): {}".format(src.name, key, ", ".join(dupes)))
            if ids != sorted(ids, key=natkey):
                findings.append("{}: {}: ids must be natural-sorted; got [{}]".format(
                    src.name, key, ", ".join(ids)))
            for cid in ids:
                if not manifest.id_pattern.fullmatch(cid):
                    findings.append("{}: {}: id '{}' is malformed for {} ({})".format(
                        src.name, key, cid, manifest.name, manifest.edition))
                elif cid not in manifest.id_set:
                    findings.append("{}: {}: id '{}' is not in {} {}".format(
                        src.name, key, cid, manifest.name, manifest.edition))
        for base, lists in per_base.items():
            both = set(lists["tight"]) & set(lists["broad"])
            if both:
                findings.append("{}: {}: id(s) asserted both tight and broad: {}".format(
                    src.name, base, ", ".join(sorted(both, key=natkey))))
    return findings, mapped, tight, broad
