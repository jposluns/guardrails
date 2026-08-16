"""Loader for the standards id-manifests under .aiqt/standards/ (the crosswalk's no-fabrication source
of truth). Stdlib only, offline: no network and no dependency outside the repo.

Each manifest is one external framework: a pinned edition plus the enumerated set of canonical control
ids that a rule's `map-<key>` frontmatter is permitted to cite. `gen_rules` derives its MAP_KEYS from
these files (a key exists only if its manifest does); `check_mappings` validates every mapped id against
them. An id that is not in its manifest cannot ship, so a fabricated mapping is structurally impossible.

Requires Python 3.11+ for tomllib (CI pins 3.12).
"""
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: the standards loader requires Python 3.11+ (tomllib).")

# Required top-level fields in every manifest. Attribution (licence, source-url) is intentionally NOT
# required here: it is added, per-source verified, with the public NOTICE. This file's job is to make
# an id verifiable, not to assert a licence.
REQUIRED = ("map-key", "name", "publisher", "edition", "kind", "status", "citation-unit",
            "id-pattern", "source-artefact", "retrieved")
KINDS = {"risk", "control", "guidance", "technique"}      # how the public page words the relation
STATUSES = {"stable", "beta", "snapshot"}                 # edition stability, surfaced as a badge


class ManifestError(ValueError):
    """A malformed standards manifest. check_mappings maps this to exit 2 (fail-closed)."""


def natkey(text):
    """Sort key that orders embedded numbers numerically (LLM2 before LLM10, V2 before V14)."""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r'(\d+)', text)]


class Manifest:
    __slots__ = ("path", "map_key", "name", "publisher", "edition", "kind", "status",
                 "citation_unit", "id_pattern", "source_artefact", "retrieved", "ids", "id_set",
                 "titles")

    def __init__(self, path, data):
        self.path = path
        for key in REQUIRED:
            if key not in data:
                raise ManifestError("{}: missing required field '{}'".format(path.name, key))
        self.map_key = data["map-key"]
        expected = "map-" + path.stem
        if self.map_key != expected:
            raise ManifestError("{}: map-key '{}' must equal 'map-<filename>' ('{}')".format(
                path.name, self.map_key, expected))
        self.name = data["name"]
        self.publisher = data["publisher"]
        self.edition = str(data["edition"])
        self.kind = data["kind"]
        if self.kind not in KINDS:
            raise ManifestError("{}: kind '{}' must be one of {}".format(
                path.name, self.kind, "/".join(sorted(KINDS))))
        self.status = data["status"]
        if self.status not in STATUSES:
            raise ManifestError("{}: status '{}' must be one of {}".format(
                path.name, self.status, "/".join(sorted(STATUSES))))
        self.citation_unit = data["citation-unit"]
        self.source_artefact = data["source-artefact"]
        self.retrieved = str(data["retrieved"])
        try:
            self.id_pattern = re.compile(data["id-pattern"])
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


def load_manifests(std_dir):
    """Return {map_key: Manifest} for every *.toml under std_dir, fully validated. Raise ManifestError
    on any malformed manifest or duplicate map-key. An absent/empty dir returns {} (transition-safe)."""
    std_dir = Path(std_dir)
    out = {}
    if not std_dir.is_dir():
        return out
    for path in sorted(std_dir.glob("*.toml")):
        with open(path, "rb") as handle:
            try:
                data = tomllib.load(handle)
            except tomllib.TOMLDecodeError as exc:
                raise ManifestError("{}: invalid TOML: {}".format(path.name, exc))
        manifest = Manifest(path, data)
        if manifest.map_key in out:
            raise ManifestError("{}: map-key '{}' already defined by {}".format(
                path.name, manifest.map_key, out[manifest.map_key].path.name))
        out[manifest.map_key] = manifest
    return out


def map_keys(root):
    """The derived set of valid `map-<key>` frontmatter keys: one per manifest filename under
    .aiqt/standards/. Cheap (no parse) so gen_rules can build MAP_KEYS at import; check_mappings does
    the full parse/validation via load_manifests."""
    std_dir = Path(root) / ".aiqt" / "standards"
    if not std_dir.is_dir():
        return set()
    return {"map-" + path.stem for path in std_dir.glob("*.toml")}
