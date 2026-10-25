"""Loader for the standards id-manifests under .aiqt/standards/ (the crosswalk's no-fabrication source
of truth). Stdlib only, offline: no network and no dependency outside the repo.

Each manifest is one external framework: a pinned edition plus the enumerated set of canonical control
ids that a rule's `map-<key>` frontmatter is permitted to cite. `gen_rules` derives its MAP_KEYS from
these files (a key exists only if its manifest does); `check_mappings` validates every mapped id against
them. An id that is not in its manifest cannot ship, so a fabricated mapping is structurally impossible.

Requires Python 3.14 or newer, the floor in .aiqt/core/python-floor.toml.
"""
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


def dir_present(path):
    """Fail-closed directory-absence probe. Return True if path is a directory, False if it does not
    exist. RAISE OSError if it exists but cannot be reached (an unreadable path, or an unreadable
    ancestor such as a `chmod 0` .aiqt/ parent): the caller must fail closed rather than mistake
    'cannot tell' for 'absent'. Path.is_dir()/exists() SWALLOW EACCES and return False, so an
    unreadable input dir would read as an absent (transition-safe) input and pass clean; os.stat raises
    on EACCES, so we use it. A non-directory returns False (callers treat it as absent, as is_dir did).
    Pairs with ensure_listable: dir_present catches an unreachable dir, ensure_listable catches a
    reachable-but-unlistable one."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(st.st_mode)


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
    A symbolic link whose target is an existing directory inside root is a directory, as dir_present reads
    it: the link is resolved once (os.path.realpath, strict), and that one resolved path is the path tested
    against the resolved root and, with os.lstat, as a directory. RAISE OSError when the path cannot be
    examined other than by absence (an unreadable ancestor, an ancestor that is not a directory, or a
    symbolic link loop), as dir_present does. dir_present reads every non-directory as absent; a caller that
    must not pass over an input it never read refuses the reason as cannot-evaluate instead. Only the last
    component is judged: an ancestor symlink is followed. The caller reads the tree through path, so a link
    retargeted between this probe and that read (by another writer at the same moment) is not detected."""
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


def _standards_present(std_dir, root):
    """dir_or_absent for the standards dir under root, raising (OSError) where something is there that
    cannot be read as the directory, so it is never read as no manifests; every caller already renders an
    OSError as exit 2."""
    present, problem = dir_or_absent(std_dir, root)
    if problem is not None:
        raise NotADirectoryError("{} {} (cannot evaluate)".format(std_dir, problem))
    return present


def load_manifests(std_dir, root=None):
    """Return {map_key: Manifest} for every *.toml under std_dir, fully validated. Raise ManifestError
    on any malformed manifest or duplicate map-key. An absent/empty dir returns {} (transition-safe); an
    existing dir that cannot be listed, or a std_dir that is there but cannot be read as a directory
    (dir_or_absent: a regular file, or a symlink that is dangling, whose target is not a directory or
    resolves outside root), raises OSError (fail-closed), never a false-clean empty. root is the
    repository root; by default the directory two levels up, whose .aiqt/standards std_dir is."""
    std_dir = Path(std_dir)
    out = {}
    if not _standards_present(std_dir, std_dir.parent.parent if root is None else root):
        return out
    ensure_listable(std_dir)
    for path in sorted(std_dir.glob("*.toml")):
        _check_stem(path)
        with open(path, "rb") as handle:
            try:
                data = tomllib.load(handle)
            # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
            # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
            # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
            except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
                raise ManifestError("{}: invalid TOML: {}".format(path.name, exc))
        manifest = Manifest(path, data)
        if manifest.map_key in out:
            raise ManifestError("{}: map-key '{}' already defined by {}".format(
                path.name, manifest.map_key, out[manifest.map_key].path.name))
        out[manifest.map_key] = manifest
    return out


def map_keys(root):
    """The derived set of valid `map-<key>` frontmatter keys: the two fit-qualified keys
    `map-<stem>-tight` and `map-<stem>-broad` per manifest filename under .aiqt/standards/, and nothing
    else. A bare `map-<stem>` (no fit) or a bad suffix (`map-<stem>-medium`) is therefore not a legal
    frontmatter key, rejected fail-closed by gen_rules._check_keys with no extra gate code. Cheap (no
    parse) so gen_rules can build MAP_KEYS at import; check_mappings does the full parse/validation via
    load_manifests. A .aiqt/standards that is there but cannot be read as a directory raises, as
    load_manifests does."""
    std_dir = Path(root) / ".aiqt" / "standards"
    if not _standards_present(std_dir, root):
        return set()
    ensure_listable(std_dir)
    keys = set()
    for path in std_dir.glob("*.toml"):
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
