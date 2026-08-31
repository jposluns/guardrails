#!/usr/bin/env python3
"""Release-manifest generator (VER-CORE 4.1/4.2, Section 12 step 2). Offline, stdlib only, fail-closed.

Owns four deterministic outputs, all generated in one run from the anchored ownership map
(.aiqt/core/ownership.toml) and the git-tracked surface:

  .gitattributes                         one literal line per in-scope tracked path (3.3)
  .aiqt/manifest.toml                    SOURCES, ARTIFACTS, TREE digest, genesis (4.1)
  .aiqt/release/root.txt                 sha256:<hex> over the exact manifest bytes (5.4)
  .aiqt/release/announce-snippet.txt     ready-to-publish version + ROOT lines (5.4)

Concern-1 scope is COMPUTED BY INVERSION: all git-tracked repository paths minus the map's
concrete-literal exclusion set; every remaining path must carry exactly one release class, with no
default and no precedence (overlapping selectors are a schema FAIL). The tracked enumeration comes from
`git ls-files -z --cached --stage`, NEVER a filesystem walk, and an unusable repository is fail-closed
exit 2 with no fallback: a manifest generated from a surface git cannot certify would be a fabricated
completeness claim.

check_manifest.py IMPORTS this module's loader and expansion (the check_gensrc_failclose -> gen_gensrc
house pattern: reuse the validated loader, never fork a second parser) and independently recomputes
every verdict from the same inputs; it never trusts this generator's output bytes.

BOOTSTRAP: the four output paths are treated as IN SCOPE while still untracked whenever they are present
on disk (this run creates them, and a release commit git-adds them with the map). Write mode forces all
four into scope; check mode adds each output that exists on disk. A MISSING output is never assumed:
its selector then matches nothing and the NO-STRAYS leg fails LOUD (exit 2). Drift in a present output
is caught by byte reconciliation. This keeps the generator fail-safe with no filesystem-walk fallback.

  gen_manifest.py             regenerate all four outputs
  gen_manifest.py --check     exit 1 if any output differs from a fresh regeneration; write nothing
  gen_manifest.py --self-test build synthetic git fixtures and assert the fail-closed invariants
  gen_manifest.py --root DIR  operate on DIR instead of the repo root (fixtures)

Exit convention (matches the repo's gates): 0 clean; 1 drift; 2 malformed or unreadable input, an
unclassified/stray/overlapping selector outcome, an exclusion swallowing a gensrc or portability
surface, a tracked concern-2 path, an unusable git repository, or any other cannot-evaluate.
"""
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: gen_manifest.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, reconcile  # noqa: E402
import check_versions  # noqa: E402  the ONE shared ASCII SemVer validator the release gates use

# Static content-bearing inputs shared by manifest.toml/root.txt/announce-snippet.txt (VERSION ->
# release-version + snippet; releases.toml -> genesis; renderers.toml + CLAUDE.md -> artifact roster).
# .gitattributes derives from ownership.toml alone. Every output also derives from the dynamic tracked
# SOURCES set, an input that cannot be enumerated statically here. gen_gensrc ast.literal_eval's this
# table (no-execute discovery, never importing a generator), so the shared tuple is inlined literally per
# entry rather than referenced by name.
GENSRC_OUTPUTS = (
    {"target": ".gitattributes", "kind": "file",
     "sources": (".aiqt/core/ownership.toml",), "regenerate": "python3 tools/gen_manifest.py"},
    {"target": ".aiqt/manifest.toml", "kind": "file",
     "sources": (".aiqt/core/ownership.toml", "VERSION", ".aiqt/core/releases.toml",
                 ".aiqt/core/renderers.toml", "CLAUDE.md"),
     "regenerate": "python3 tools/gen_manifest.py"},
    {"target": ".aiqt/release/root.txt", "kind": "file",
     "sources": (".aiqt/core/ownership.toml", "VERSION", ".aiqt/core/releases.toml",
                 ".aiqt/core/renderers.toml", "CLAUDE.md"),
     "regenerate": "python3 tools/gen_manifest.py"},
    {"target": ".aiqt/release/announce-snippet.txt", "kind": "file",
     "sources": (".aiqt/core/ownership.toml", "VERSION", ".aiqt/core/releases.toml",
                 ".aiqt/core/renderers.toml", "CLAUDE.md"),
     "regenerate": "python3 tools/gen_manifest.py"},
    {"target": ".aiqt/frozen.json", "kind": "file",
     "sources": (".aiqt/core/ownership.toml",), "regenerate": "python3 tools/gen_manifest.py"},
)

OWNERSHIP_REL = ".aiqt/core/ownership.toml"
MANIFEST_REL = ".aiqt/manifest.toml"
ROOT_REL = ".aiqt/release/root.txt"
SNIPPET_REL = ".aiqt/release/announce-snippet.txt"
ATTRIBUTES_REL = ".gitattributes"
RELEASES_REL = ".aiqt/core/releases.toml"
RENDERERS_REL = ".aiqt/core/renderers.toml"
VERSION_REL = "VERSION"
FROZEN_REL = ".aiqt/frozen.json"       # EN-8 write-scope frozen floor; a fifth generated, drift-gated output
FROZEN_VERSION = 1
OWN_OUTPUTS_REL = (ATTRIBUTES_REL, MANIFEST_REL, ROOT_REL, SNIPPET_REL)
# The frozen floor is a FIFTH generated, drift-gated output, but it is NOT one of the four release-manifest
# self-outputs (it is not the manifest, its ROOT, or the snippet): it is a normal derived SOURCES member,
# so it is hashed into the manifest and covered by the published ROOT like any other derived file.
# GENERATED_OUTPUTS_REL is the full generated set for the classify-universe bootstrap, the drift reconcile,
# and the 100644 mode check; OWN_OUTPUTS_REL stays the four for the manifest-self carve-out and the delta gate.
GENERATED_OUTPUTS_REL = OWN_OUTPUTS_REL + (FROZEN_REL,)

# The ONE shared release-order row schema (2.4/2.6/6.5), single source of truth for the three gates that
# read releases.toml (VC-4 QA #6): read_genesis (needs only the row count, validates the two mandatory
# identity fields and ACCEPTS every documented field), check_release_build (the full per-field validator),
# and check_release_delta (the delta consumer). RELEASE_ROW_MANDATORY is the identity pair every row
# carries from birth; RELEASE_ROW_ALLOWED adds the anchor tag fields and the attestation fields a row
# gains once QA'd. A drift self-test in check_release_build and check_release_delta binds each gate's own
# key handling to these sets so they cannot diverge.
RELEASE_ROW_MANDATORY = frozenset({"version", "commit_sha"})
RELEASE_ROW_ALLOWED = frozenset({"version", "commit_sha", "tag", "tag_object_sha",
                                 "qa-sha256", "qa-store-path", "attestation-timestamps"})

RELEASE_CLASSES = ("pack-immutable", "derived", "manifest-self", "managed-block")
NAMESPACE_CLASSES = ("adopter-state", "archive")
# EN-8 write-scope frozen floor: the Architect-bound operational frozen class set (2026-08-31). A guarded
# Write/Edit/MultiEdit whose target resolves into one of these classes is hard-denied by write_scope_guard
# (.aiqt/core/hooks/scripts/aiqt_hooks.py), un-lowerable by the per-slice scope declaration. This is the ONE
# place the manifest-class -> frozen mapping is bound, recorded, and reviewed; the hook never reimplements
# or guesses it. derived + manifest-self are generated outputs (generated-artefact-source-only: edit the
# source, never the output); archive is frozen rotation data. EXCLUDED by the binding: pack-immutable
# (rule/doc SOURCES are legitimately edited), managed-block (hand-authored regions are legitimately edited),
# adopter-state (working state is legitimately edited). Changing this set is itself a guardrail-config change
# needing explicit authorization (SECI-guardrail-config-integrity).
FROZEN_CLASSES = ("derived", "manifest-self", "archive")
BLOCK_BEGIN = "<!-- RULES-INDEX:BEGIN (generated) -->"
BLOCK_END = "<!-- RULES-INDEX:END -->"
# gitattributes hazard characters: a path carrying any of these would need git-side quoting or would
# parse as a glob; fail closed and force an explicit review (none exists in the tree today).
ATTR_UNSAFE = set(' #!*?[]"')


class GateError(Exception):
    """A fail-closed condition (malformed input, unusable enumeration, schema violation): exit 2."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _check_rel_path(path, where):
    """Canonical repo-relative POSIX path or GateError. No absolute, backslash, empty/'.'/'..' segment,
    control character (the FULL C0 range 0x00-0x1F and DEL 0x7F), or trailing slash."""
    if not isinstance(path, str) or not path:
        raise GateError("{}: path must be a non-empty string".format(where))
    if "\\" in path or path.startswith("/") or path.endswith("/"):
        raise GateError("{}: {!r} must be a clean POSIX repo-relative path".format(where, path))
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in path):
        raise GateError("{}: {!r} carries a control character".format(where, path))
    if any(seg in ("", ".", "..") for seg in path.split("/")):
        raise GateError("{}: {!r} has an empty, '.', or '..' segment".format(where, path))
    return path


class Selector:
    """An exact path or a literal directory-prefix pattern '<prefix>/**'. Nothing else is a selector."""

    def __init__(self, raw, where):
        if raw.endswith("/**"):
            self.prefix = _check_rel_path(raw[:-3], where) + "/"
            self.exact = None
            body = self.prefix[:-1]
        else:
            self.exact = _check_rel_path(raw, where)
            self.prefix = None
            body = self.exact
        if "*" in body:
            raise GateError("{}: {!r} is not an exact path or a literal '<prefix>/**' pattern"
                            .format(where, raw))
        self.raw = raw
        self.where = where

    def matches(self, path):
        return path == self.exact if self.exact is not None else path.startswith(self.prefix)


def _selector_of(row, where):
    """Exactly one of 'path'/'pattern' on a row; 'pattern' must end '/**'."""
    has_path, has_pattern = "path" in row, "pattern" in row
    if has_path == has_pattern:
        raise GateError("{}: exactly one of 'path' or 'pattern' is required".format(where))
    raw = row["path"] if has_path else row["pattern"]
    if not isinstance(raw, str) or not raw:
        raise GateError("{}: the selector must be a non-empty string".format(where))
    if has_pattern and not raw.endswith("/**"):
        raise GateError("{}: a pattern must be a literal directory prefix ending '/**'".format(where))
    if has_path and raw.endswith("/**"):
        raise GateError("{}: a '/**' selector must use the 'pattern' key".format(where))
    return Selector(raw, where)


def load_ownership(root):
    """Strict parse of the working-tree ownership map. Returns (exclusions, release_rows, namespace_rows,
    binary_set). Reads the file, then defers all validation to parse_ownership."""
    path = root / OWNERSHIP_REL
    try:
        data = load_toml(path)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(OWNERSHIP_REL, exc))
    return parse_ownership(data)


def parse_ownership(data):
    """Validate an already-parsed ownership map. Split out from load_ownership so a predecessor ownership
    map read via `git show <commit>:.aiqt/core/ownership.toml` (check_release_delta, VC-4 QA #11) runs the
    SAME validation as the working-tree loader, never a second parser. Returns (exclusions, release_rows,
    namespace_rows, binary_set); any schema violation raises GateError."""
    known_tables = {"format-version", "exclusion", "release-class", "namespace-class",
                    "checkout", "adopter-extent"}
    extra = set(data) - known_tables
    if extra:
        raise GateError("{}: unknown table/key(s): {}".format(OWNERSHIP_REL, ", ".join(sorted(extra))))
    if data.get("format-version") != 1:
        raise GateError("{}: format-version must be exactly 1".format(OWNERSHIP_REL))

    def rows(name, allowed_keys, class_vocab, class_required):
        out = []
        for i, row in enumerate(data.get(name, [])):
            where = "{} [[{}]] #{}".format(OWNERSHIP_REL, name, i + 1)
            if not isinstance(row, dict):
                raise GateError("{}: not a table".format(where))
            bad = set(row) - allowed_keys
            if bad:
                raise GateError("{}: unknown key(s): {}".format(where, ", ".join(sorted(bad))))
            sel = _selector_of(row, where)
            cls = row.get("class")
            if class_required:
                if cls not in class_vocab:
                    raise GateError("{}: class must be one of {}".format(where, "/".join(class_vocab)))
            elif not isinstance(row.get("reason"), str) or not row["reason"]:
                raise GateError("{}: a non-empty reason is required".format(where))
            out.append((sel, cls))
        return out

    exclusions = rows("exclusion", {"path", "pattern", "reason"}, (), class_required=False)
    release = rows("release-class", {"path", "pattern", "class", "note"}, RELEASE_CLASSES, True)
    namespace = rows("namespace-class", {"path", "pattern", "class", "note"}, NAMESPACE_CLASSES, True)
    if not release:
        raise GateError("{}: at least one [[release-class]] row is required".format(OWNERSHIP_REL))
    checkout = data.get("checkout", {})
    if set(checkout) - {"binary"}:
        raise GateError("{}: [checkout] carries unknown key(s)".format(OWNERSHIP_REL))
    binary = checkout.get("binary", [])
    if not isinstance(binary, list) or not all(isinstance(b, str) for b in binary):
        raise GateError("{}: [checkout].binary must be a list of paths".format(OWNERSHIP_REL))
    binary_set = {_check_rel_path(b, "[checkout].binary") for b in binary}
    extent = data.get("adopter-extent", {})
    if extent.get("authority") != "adopter-experience-spec" or set(extent) - {"authority"}:
        raise GateError("{}: [adopter-extent] must carry exactly authority = "
                        "\"adopter-experience-spec\"".format(OWNERSHIP_REL))
    return exclusions, release, namespace, binary_set


def git_tracked(root):
    """The tracked path set from `git ls-files -z --cached --stage`, strictly decoded and validated.
    Fail-closed (GateError) on an unusable repository, a nonzero exit, empty output, a duplicate path,
    a case-fold or NFC/NFD collision, a symlink (120000), a gitlink (160000), or an unknown mode.
    NEVER a filesystem walk and never a silent empty tree."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--stage"],
                              capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("cannot run git ls-files ({}); fail-closed".format(exc))
    if proc.returncode != 0:
        raise GateError("git ls-files failed (exit {}): {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    if not proc.stdout:
        raise GateError("git ls-files returned no entries; an empty tree is never assumed")
    paths, seen_fold, seen_nfc = set(), {}, {}
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        try:
            meta, path = record.decode("utf-8").split("\t", 1)
            mode = meta.split(" ", 1)[0]
        except (UnicodeDecodeError, ValueError) as exc:
            raise GateError("malformed git index record ({}); fail-closed".format(exc))
        if mode == "120000":
            raise GateError("tracked symlink {!r}: symlinks are rejected in pack scope (4.3)".format(path))
        if mode == "160000":
            raise GateError("tracked gitlink {!r}: submodules are unsupported in pack scope".format(path))
        if mode not in ("100644", "100755"):
            raise GateError("tracked path {!r} has unsupported mode {}".format(path, mode))
        _check_rel_path(path, "git index")
        if path in paths:
            raise GateError("duplicate tracked path {!r}".format(path))
        fold, nfc = path.casefold(), unicodedata.normalize("NFC", path)
        if seen_fold.setdefault(fold, path) != path:
            raise GateError("case-fold collision: {!r} and {!r}".format(seen_fold[fold], path))
        if seen_nfc.setdefault(nfc, path) != path:
            raise GateError("unicode-normalization collision: {!r} and {!r}".format(seen_nfc[nfc], path))
        paths.add(path)
    return paths


def check_output_modes(root):
    """F-236: the GENERATED_OUTPUTS_REL are generated TEXT and MUST carry git index mode 100644, never
    100755. gen_manifest reads the index mode at build and check_manifest re-reads it (defence in depth);
    otherwise a mode flip 100644->100755 on a generated output passes both gates unnoticed. Scoped STRICTLY
    to the generated outputs: run_all_checks.sh and other *.sh are legitimately executable and are NEVER
    touched. An output not yet tracked (genesis, before the release commit git-adds it) is not in the index
    and is skipped, never an error. GateError (exit 2) on a non-100644 tracked output or an unusable git read."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--stage", "--",
                               *GENERATED_OUTPUTS_REL], capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("cannot run git ls-files for output modes ({}); fail-closed".format(exc))
    if proc.returncode != 0:
        raise GateError("git ls-files (output modes) failed (exit {}): {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        try:
            meta, path = record.decode("utf-8").split("\t", 1)
            mode = meta.split(" ", 1)[0]
        except (UnicodeDecodeError, ValueError) as exc:
            raise GateError("malformed git index record for an output ({}); fail-closed".format(exc))
        if path in GENERATED_OUTPUTS_REL and mode != "100644":
            raise GateError("{}: a generated output must have git index mode 100644, not {} "
                            "(generated text is never executable)".format(path, mode))


def classify(tracked, exclusions, release, namespace, root, assume_outputs=False, outputs_present=None):
    """The build-time assertion (4.2). Returns ({path: class}, excluded) over the in-scope set. GateError
    on: a stale exclusion (zero matches); an unclassified in-scope path (COMPLETENESS); a selector with
    zero matches or an untracked exact path (NO-STRAYS); two selectors matching one path (overlap); a class
    selector reaching an excluded path; a concern-2 selector matching any tracked path.
    The universe is the tracked set PLUS this generator's own four outputs when present (or all four in
    write mode, assume_outputs=True): the release commit git-adds them, so a present-but-untracked output
    is in scope while a MISSING output is never assumed and trips NO-STRAYS. Presence is read from the
    working tree by default; a caller classifying a git commit (not the checkout) passes outputs_present, a
    set of the OWN_OUTPUTS_REL that exist AT that commit, so root's filesystem is never consulted for a
    predecessor tree (check_release_delta, VC-4 QA #11)."""
    universe = set(tracked)
    for rel in GENERATED_OUTPUTS_REL:
        present = (rel in outputs_present) if outputs_present is not None else (root / rel).exists()
        if assume_outputs or present:
            universe.add(rel)
    excluded = set()
    for sel, _ in exclusions:
        hits = {p for p in universe if sel.matches(p)}
        if not hits:
            raise GateError("{}: exclusion {!r} matches no tracked path (stale deny-list entry)"
                            .format(sel.where, sel.raw))
        excluded |= hits
    in_scope = universe - excluded
    classes = {}
    for sel, cls in release:
        hits = {p for p in in_scope if sel.matches(p)}
        if not hits:
            raise GateError("{}: concern-1 selector {!r} matches no tracked in-scope path (NO-STRAYS)"
                            .format(sel.where, sel.raw))
        reach_excluded = {p for p in excluded if sel.matches(p)}
        if reach_excluded:
            raise GateError("{}: selector {!r} reaches excluded path(s) {}".format(
                sel.where, sel.raw, ", ".join(sorted(reach_excluded)[:3])))
        for p in hits:
            if p in classes:
                raise GateError("overlap: {!r} is matched by more than one concern-1 selector "
                                "(selectors must be disjoint; no precedence)".format(p))
            classes[p] = cls
    unclassified = in_scope - set(classes)
    if unclassified:
        raise GateError("COMPLETENESS: {} in-scope tracked path(s) carry no class, e.g. {}; add an "
                        "explicit [[release-class]] row or a concrete [[exclusion]] line".format(
                            len(unclassified), ", ".join(sorted(unclassified)[:5])))
    for sel, _ in namespace:
        hits = {p for p in universe if sel.matches(p)}
        if hits:
            raise GateError("{}: concern-2 namespace {!r} matches tracked path(s) {}; tracked paths "
                            "belong to concern 1".format(sel.where, sel.raw, ", ".join(sorted(hits)[:3])))
    return classes, excluded


def cross_check_exclusions(root, exclusions):
    """No exclusion may swallow a gensrc-registered target or a check_portability required surface."""
    import gen_gensrc      # reuse the validated loader, never a second parser (house pattern)
    import check_portability
    decls = gen_gensrc.discover_declarations(root / "tools")
    entries = gen_gensrc.collect_entries(decls, root)
    targets = [e["target"].rstrip("/") for e in entries]
    surfaces = targets + list(check_portability.REQUIRED_DIR_ROOTS) + list(
        check_portability.REQUIRED_FILE_ROOTS)
    for sel, _ in exclusions:
        for surface in surfaces:
            if sel.matches(surface) or (sel.prefix and (surface + "/").startswith(sel.prefix)):
                raise GateError("{}: exclusion {!r} would swallow the gensrc/portability surface {!r}"
                                .format(sel.where, sel.raw, surface))


def read_version(root):
    # Validate the RAW VERSION file grammar FIRST, then parse. Read the raw BYTES (not read_text): read_text
    # applies universal-newline translation, which silently rewrites a CR-terminated "1.0.0\r\n" to "1.0.0\n"
    # and masks the CR. Do NOT .strip() either: .strip() masks surrounding Unicode whitespace, so an
    # NBSP-wrapped " 1.0.0 \n" would collapse to "1.0.0" and seed release-version at exit 0. The canonical
    # VERSION file is exactly `X.Y.Z` followed by a single trailing LF and nothing else (no leading/trailing/
    # embedded whitespace, no NBSP, no CR, exactly one final "\n"), the same standard check_versions holds the
    # on-disk VERSION to (both read raw bytes and reject anything but `latest + "\n"`); the two gates agree.
    try:
        raw = (root / VERSION_REL).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError("VERSION is not valid UTF-8 ({})".format(exc))
    if not raw.endswith("\n") or raw.count("\n") != 1:
        raise GateError("VERSION {!r} is not a bare SemVer with a single trailing newline".format(raw))
    version = raw[:-1]
    # Parse the X.Y.Z body with the SHARED ASCII SemVer validator, never a second grammar: it rejects a
    # Unicode digit, a leading zero, and any surrounding or embedded whitespace (an NBSP/CR body fails here).
    if check_versions._parse(version) is None:
        raise GateError("VERSION {!r} is not a bare SemVer".format(raw))
    return version


def read_genesis(root):
    """genesis derives from the release-order record: zero [[release]] rows -> True (2.5). A missing or
    unreadable record never bootstraps genesis.

    F-237 SCOPED, VC-4 QA #6 RECONCILED: reject an unknown TOP-LEVEL key, and apply a per-row guard when a
    [[release]] row is present (rows first appear at the Step-4 attestation commit; at Step 2 the record is
    header-only). read_genesis needs only the ROW COUNT, so it validates the two mandatory identity fields
    (version and the candidate commit SHA, spec 2.4 / the releases.toml header comment) and ACCEPTS every
    field of the documented full release-row schema (RELEASE_ROW_ALLOWED). The earlier minimal allow-set
    named only {version, commit_sha, tag_object_sha}, which rejected the tag / qa-sha256 / qa-store-path /
    attestation-timestamps keys a real attestation row carries, so the first attested row could never pass
    this guard: read_genesis now shares ONE release-row schema with check_release_build (the full per-field
    validator) and check_release_delta (the delta consumer), so the three cannot diverge. The per-field
    type/format validation and the tag-validation flow remain Step 4's (check_release_build)."""
    try:
        data = load_toml(root / RELEASES_REL)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(RELEASES_REL, exc))
    if data.get("format-version") != 1:
        raise GateError("{}: format-version must be exactly 1".format(RELEASES_REL))
    top_allowed = {"format-version", "release"}
    top_extra = set(data) - top_allowed
    if top_extra:
        raise GateError("{}: unknown top-level key(s): {}".format(
            RELEASES_REL, ", ".join(sorted(top_extra))))
    rows = data.get("release", [])
    if not isinstance(rows, list):
        raise GateError("{}: [[release]] must be an array".format(RELEASES_REL))
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise GateError("{}: [[release]] row #{} is not a table".format(RELEASES_REL, idx + 1))
        row_extra = set(row) - RELEASE_ROW_ALLOWED
        if row_extra:
            raise GateError("{}: [[release]] row #{} has unknown key(s): {} (not in the shared release-row "
                            "schema)".format(RELEASES_REL, idx + 1, ", ".join(sorted(row_extra))))
        missing = RELEASE_ROW_MANDATORY - set(row)
        if missing:
            raise GateError("{}: [[release]] row #{} is missing the mandatory field(s): {}".format(
                RELEASES_REL, idx + 1, ", ".join(sorted(missing))))
    return len(rows) == 0


def _toml_str(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def managed_block_digest(root):
    """SHA-256 over CLAUDE.md's delimited block: from the start of the BEGIN marker through the last
    byte of the END marker, markers included, the following LF excluded. Exactly one pair, BEGIN before
    END, no nesting."""
    raw = (root / "CLAUDE.md").read_bytes()
    begin, end = BLOCK_BEGIN.encode(), BLOCK_END.encode()
    if raw.count(begin) != 1 or raw.count(end) != 1:
        raise GateError("CLAUDE.md: expected exactly one {} / {} marker pair".format(
            BLOCK_BEGIN, BLOCK_END))
    i, j = raw.index(begin), raw.index(end)
    if j < i:
        raise GateError("CLAUDE.md: END marker precedes BEGIN marker")
    return _sha256(raw[i:j + len(end)])


def load_renderers(root):
    """The committed renderer declaration rows (freshness proven by gen_renderers.py's own drift gate,
    which runs earlier in the roster). Minimal shape validation here; check_manifest recomputes fully."""
    try:
        data = load_toml(root / RENDERERS_REL)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(RENDERERS_REL, exc))
    if data.get("format-version") != 1:
        raise GateError("{}: format-version must be exactly 1".format(RENDERERS_REL))
    rows = data.get("renderer", [])
    if not rows:
        raise GateError("{}: at least one [[renderer]] row is required".format(RENDERERS_REL))
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("renderer-id"), str) \
                or not isinstance(r.get("targets"), list):
            raise GateError("{}: a renderer row is malformed".format(RENDERERS_REL))
    return rows


def build_artifacts(root, classes, renderers):
    """ARTIFACTS rows from the renderer declaration: file targets one row, tree targets one row per
    sorted tracked leaf, plus the CLAUDE.md managed-block row. artifact-id is '<renderer-id>:<path>'."""
    rows = []
    for r in renderers:
        rid = r["renderer-id"]
        for target in r["targets"]:
            if target == "CLAUDE.md":
                rows.append({"artifact-id": "{}:CLAUDE.md#RULES-INDEX".format(rid), "path": "CLAUDE.md",
                             "kind": "managed-block", "block-id": "RULES-INDEX",
                             "sha256": managed_block_digest(root)})
                continue
            if target.endswith("/"):
                leaves = sorted(p for p in classes if p.startswith(target))
                if not leaves:
                    raise GateError("renderer {}: tree target {!r} has no tracked leaves".format(
                        rid, target))
                for leaf in leaves:
                    rows.append({"artifact-id": "{}:{}".format(rid, leaf), "path": leaf,
                                 "kind": "file", "sha256": _sha256((root / leaf).read_bytes())})
            else:
                if target not in classes:
                    raise GateError("renderer {}: target {!r} is not a tracked in-scope path".format(
                        rid, target))
                rows.append({"artifact-id": "{}:{}".format(rid, target), "path": target,
                             "kind": "file", "sha256": _sha256((root / target).read_bytes())})
    rows.sort(key=lambda r: (r["path"], r["artifact-id"]))
    ids = [r["artifact-id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise GateError("duplicate artifact-id in the computed roster")
    return rows


def _frozen_text(release, namespace):
    """The EN-8 frozen-floor artifact bytes (.aiqt/frozen.json): a version-pinned JSON object listing, in
    the shared write-scope entry grammar (a repo-root-relative POSIX path; a trailing '/' marks a tree,
    otherwise an exact file), every ownership selector whose class is in FROZEN_CLASSES ({derived,
    manifest-self, archive}). ONE classifier, TWO consumers: the same ownership rows classify() reads for the
    manifest are read here for the floor, so the runtime hook's floor cannot drift from the gate's
    classification (the drift gate makes any divergence a CI failure). An exact-path selector yields a file
    entry; a '<prefix>/**' pattern yields a '<prefix>/' tree entry. Entries are sorted and de-duplicated for
    determinism. classify() has already proven every release selector live (NO-STRAYS) and every namespace
    selector zero-tracked, so a frozen release selector (derived, manifest-self) maps to at least one tracked
    generated output and the archive namespace maps to a reserved (untracked) rotation tree, both correctly
    frozen against a guarded-tool write. The floor lists .aiqt/frozen.json itself (it is class derived), so
    the floor's own committed copy is deny-protected from the guarded tools."""
    entries = set()
    for sel, cls in list(release) + list(namespace):
        if cls in FROZEN_CLASSES:
            entries.add(sel.exact if sel.exact is not None else sel.prefix)
    return json.dumps({"version": FROZEN_VERSION, "frozen": sorted(entries)},
                      indent=2, sort_keys=True) + "\n"


def compute_all(root, write_mode):
    """Compute the five generated output texts (the four release-manifest self-outputs plus the EN-8 frozen
    floor). Returns {rel_path: text}. GateError on any fail-closed condition. In write mode the attributes
    and frozen-floor texts are computed first and their own SOURCES hashes use the COMPUTED text (each file
    is about to carry it, and the floor may not yet exist on first generation); in check mode disk bytes are
    hashed everywhere."""
    exclusions, release, namespace, binary_set = load_ownership(root)
    tracked = git_tracked(root)
    classes, _ = classify(tracked, exclusions, release, namespace, root, assume_outputs=write_mode)
    cross_check_exclusions(root, exclusions)
    import check_manifest  # the verifier-carried minimum-class table (single home, 4.2)
    check_manifest.apply_minimums(classes)

    for b in binary_set:
        if classes.get(b) is None:
            raise GateError("[checkout].binary {!r} is not a tracked in-scope path".format(b))

    # 1. .gitattributes: one literal line per in-scope path, sorted; binary overrides in place.
    attr_lines = ["# generated by tools/gen_manifest.py from {}; do not hand-edit".format(OWNERSHIP_REL)]
    for path in sorted(classes):
        if any(ch in ATTR_UNSAFE for ch in path):
            raise GateError(".gitattributes: path {!r} carries a gitattributes-unsafe character; "
                            "review and extend the renderer explicitly".format(path))
        attr_lines.append("{} {}".format(path, "binary" if path in binary_set else "text eol=lf"))
    attributes_text = "\n".join(attr_lines) + "\n"

    # 1b. The EN-8 frozen floor, from the same ownership classification (one classifier, two consumers).
    frozen_text = _frozen_text(release, namespace)

    # 2. The manifest.
    version = read_version(root)
    genesis = read_genesis(root)
    source_paths = sorted(p for p, c in classes.items()
                          if p not in (MANIFEST_REL, ROOT_REL, SNIPPET_REL) and c != "managed-block")
    src_rows = []
    for p in source_paths:
        if write_mode and p == ATTRIBUTES_REL:
            data = attributes_text.encode("utf-8")
        elif write_mode and p == FROZEN_REL:
            # The frozen floor is a SOURCES member (class derived); in write mode use the COMPUTED text for
            # its hash, because the file is about to be (re)written and may not exist yet on first generation
            # (mirrors the .gitattributes self-reference above, so regeneration converges in one pass).
            data = frozen_text.encode("utf-8")
        else:
            try:
                data = (root / p).read_bytes()
            except OSError as exc:
                raise GateError("cannot read tracked source {} ({}); fail-closed".format(p, exc))
        if p not in binary_set:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise GateError("{} is not valid UTF-8 and is not declared [checkout].binary; "
                                "classify it explicitly".format(p))
        src_rows.append((p, len(data), _sha256(data)))
    artifacts = build_artifacts(root, classes, load_renderers(root))
    tree = _sha256("".join("{}\t{}\n".format(p, d) for p, _, d in src_rows).encode("utf-8"))

    lines = ["# generated by tools/gen_manifest.py; do not hand-edit (VER-CORE 4.1)",
             "format-version = 1",
             "release-version = {}".format(_toml_str(version)),
             "genesis = {}".format("true" if genesis else "false"),
             "tree-sha256 = {}".format(_toml_str(tree)), ""]
    for p, size, digest in src_rows:
        lines += ["[[sources]]", "path = {}".format(_toml_str(p)), "bytes = {}".format(size),
                  "sha256 = {}".format(_toml_str(digest)), ""]
    for row in artifacts:
        lines += ["[[artifacts]]", "artifact-id = {}".format(_toml_str(row["artifact-id"])),
                  "path = {}".format(_toml_str(row["path"])), "kind = {}".format(_toml_str(row["kind"]))]
        if row["kind"] == "managed-block":
            lines.append("block-id = {}".format(_toml_str(row["block-id"])))
        lines += ["sha256 = {}".format(_toml_str(row["sha256"])), ""]
    manifest_text = "\n".join(lines)

    # 3 + 4. ROOT over the exact manifest bytes; the publishable snippet.
    root_hex = _sha256(manifest_text.encode("utf-8"))
    root_text = "sha256:{}\n".format(root_hex)
    snippet_text = "aiqt-guardrails release {}\nroot sha256:{}\n".format(version, root_hex)
    return {ATTRIBUTES_REL: attributes_text, MANIFEST_REL: manifest_text,
            ROOT_REL: root_text, SNIPPET_REL: snippet_text, FROZEN_REL: frozen_text}


def run(root, check):
    try:
        texts = compute_all(root, write_mode=not check)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, ValueError, KeyError, TypeError) as exc:
        # An unreadable/undecodable input (VERSION, CLAUDE.md, a source file) is cannot-evaluate, exit 2,
        # not drift: mirror check_manifest.run() so the two sides share one exit convention (4.2 docstring).
        print("error: cannot evaluate ({}: {}); fail-closed".format(type(exc).__name__, exc),
              file=sys.stderr)
        return 2
    if check:
        try:
            check_output_modes(root)  # F-236: the four outputs are generated text, index mode 100644 only.
        except GateError as exc:
            print("error: {}; fail-closed".format(exc), file=sys.stderr)
            return 2
    if not check:
        (root / ".aiqt" / "release").mkdir(parents=True, exist_ok=True)
    drifted = []
    for rel in GENERATED_OUTPUTS_REL:  # attributes first: the manifest's SOURCES row covers its new bytes
        if reconcile(root / rel, texts[rel], check):
            drifted.append(rel)
    if drifted:
        print("drift: {} out of date; run tools/gen_manifest.py".format(", ".join(drifted)),
              file=sys.stderr)
        return 1
    if not check:
        print("wrote {} ({} sources, root sha256:{}...)".format(
            ", ".join(GENERATED_OUTPUTS_REL), texts[MANIFEST_REL].count("[[sources]]"),
            _sha256(texts[MANIFEST_REL].encode("utf-8"))[:12]))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    root = repo_root()
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            print("usage: gen_manifest.py [--check] [--root DIR] | --self-test", file=sys.stderr)
            return 2
        root = Path(args[i + 1]).resolve()
    return run(root, "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic git fixtures (pinned identity, sanitized env, empty template dir so no user hooks load) assert
# the fail-closed invariants end to end:
#   (a) a conformant fixture generates, then re-checks drift-clean, and two runs are byte-identical;
#   (b) carve-outs: the manifest, ROOT, and snippet are absent from SOURCES; CLAUDE.md is absent from
#       SOURCES while its managed-block row is present in ARTIFACTS; the map itself IS in SOURCES;
#   (c) corrupting each of the four outputs fails --check (exit 1);
#   (d) an unclassified new tracked path (COMPLETENESS), a stray/untracked exact selector (NO-STRAYS), an
#       overlapping selector pair, a tracked concern-2 path, and a stale exclusion each exit 2;
#   (e) a non-UTF-8 tracked file absent from [checkout].binary exits 2; declared binary passes;
#   (f) a git-less root exits 2 (never a filesystem-walk fallback);
#   (g) raw-byte hashing: a CRLF file's recorded sha256 equals the sha256 of its exact raw bytes;
#   (h) a missing CLAUDE.md marker pair exits 2.

_OWN_BASE = '''format-version = 1

[[release-class]]
path = ".aiqt/manifest.toml"
class = "manifest-self"

[[release-class]]
path = ".aiqt/release/root.txt"
class = "derived"

[[release-class]]
path = ".aiqt/release/announce-snippet.txt"
class = "derived"

[[release-class]]
path = ".aiqt/frozen.json"
class = "derived"

[[release-class]]
path = ".gitattributes"
class = "pack-immutable"

[[release-class]]
path = "CLAUDE.md"
class = "managed-block"

[[release-class]]
path = "VERSION"
class = "pack-immutable"

[[release-class]]
path = "src.txt"
class = "pack-immutable"

[[release-class]]
path = "out.txt"
class = "pack-immutable"

[[release-class]]
pattern = ".aiqt/core/**"
class = "pack-immutable"

[[release-class]]
pattern = "tools/**"
class = "pack-immutable"
'''

_OWN_TAIL = '''
[[namespace-class]]
pattern = ".aiqt/archive/**"
class = "archive"

[adopter-extent]
authority = "adopter-experience-spec"
'''

_GEN_THING = ('GENSRC_OUTPUTS = (\n'
              '    {"target": "out.txt", "kind": "file",\n'
              '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_thing.py"},\n'
              ')\n')

_RENDERERS = ('format-version = 1\n\n'
              '[[renderer]]\n'
              'renderer-id = "thing"\n'
              'entrypoint = "tools/gen_thing.py"\n'
              'semantics-revision = 1\n'
              'targets = ["out.txt", "CLAUDE.md"]\n'
              'closure = ["tools/gen_thing.py"]\n'
              'code-digest = "0000000000000000000000000000000000000000000000000000000000000000"\n')

_CLAUDE = ("# Fixture\n\n<!-- RULES-INDEX:BEGIN (generated) -->\nindex\n<!-- RULES-INDEX:END -->\n\ntail\n")

# The order/references/dispositions records the checker reads. _ORDER matches the operative gen_rules
# TIER_FACETS/CIA_FACETS so a clean fixture passes leg 11.
_ORDER = ('format-version = 1\napex-corpus-id = "prjint1"\n\n'
          '[[precedence-tier]]\nrank = 10\nmembers = ["ACCUR", "INTEG", "QUALI", "TRUST"]\n'
          'members-are-equal = true\n\n'
          '[[precedence-tier]]\nrank = 20\nmembers = ["PROGR"]\nmembers-are-equal = true\n\n'
          '[[precedence-tier]]\nrank = 30\nmembers = ["SPEED"]\nmembers-are-equal = true\n\n'
          '[[precedence-tier]]\nrank = 40\nmembers = ["COST"]\nmembers-are-equal = true\n\n'
          '[presentation-order]\nsecurity-facets = ["SECC", "SECI", "SECA", "SECP"]\n')
_REFERENCES = ('format-version = 1\nquorum = 1\n\n'
               '[[reference]]\nlocation = "x"\nretrieval-method = "git-tag"\nderivation = "independent"\n'
               'pipeline-id = "P"\nindependence-basis = "the fixture pipeline"\n')
_DISPOSITIONS = "format-version = 1\n"


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    base = ["git", "-C", str(root), "-c", "user.name=aiqt-selftest",
            "-c", "user.email=selftest@invalid", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]
    return subprocess.run(base + list(args), capture_output=True, env=env, timeout=60)


def _build_fixture(base, own_extra="", extra_files=None, do_commit=True):
    """A synthetic git repo: tools/gen_thing.py, src.txt, out.txt, VERSION, CLAUDE.md, and the .aiqt/core
    records, all git-committed under a pinned identity and empty template. Returns base."""
    (base / "tools").mkdir(parents=True)
    (base / ".aiqt" / "core").mkdir(parents=True)
    (base / "tools" / "gen_thing.py").write_text(_GEN_THING, encoding="utf-8")
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (base / "out.txt").write_text("GENERATED\nsource\n", encoding="utf-8")
    (base / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (base / "CLAUDE.md").write_text(_CLAUDE, encoding="utf-8")
    (base / ".aiqt" / "core" / "ownership.toml").write_text(_OWN_BASE + own_extra + _OWN_TAIL,
                                                            encoding="utf-8")
    (base / ".aiqt" / "core" / "releases.toml").write_text("format-version = 1\n", encoding="utf-8")
    (base / ".aiqt" / "core" / "renderers.toml").write_text(_RENDERERS, encoding="utf-8")
    (base / ".aiqt" / "core" / "order.toml").write_text(_ORDER, encoding="utf-8")
    (base / ".aiqt" / "core" / "references.toml").write_text(_REFERENCES, encoding="utf-8")
    (base / ".aiqt" / "core" / "dispositions.toml").write_text(_DISPOSITIONS, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    if do_commit:
        if _git(base, "init", "-q", "--template=").returncode != 0:
            return None
        _git(base, "add", "-A")
        _git(base, "commit", "-q", "-m", "fixture", "--no-verify")
    return base


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        print("SELF-TEST ERROR: git is unavailable; fail-closed", file=sys.stderr)
        return 2

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-manifest-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # (a) conformant: generate, re-check drift-clean, determinism.
        good = _build_fixture(tmp / "good")
        if good is None:
            print("SELF-TEST ERROR: cannot init a git fixture; fail-closed", file=sys.stderr)
            return 2
        if run_quiet(good, check=False) != 0:
            failures.append("conformant: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant: regeneration expected drift-clean exit 0")
        manifest_text = (good / MANIFEST_REL).read_text(encoding="utf-8")
        run_quiet(good, check=False)
        if (good / MANIFEST_REL).read_text(encoding="utf-8") != manifest_text:
            failures.append("determinism: two runs are not byte-identical")

        # (b) SOURCES carve-outs and the managed-block artifact.
        if '[[sources]]\npath = ".aiqt/manifest.toml"' in manifest_text:
            failures.append("carve-out: manifest-self must not appear in SOURCES")
        if '"CLAUDE.md"' in manifest_text.split("[[artifacts]]", 1)[0]:
            failures.append("carve-out: CLAUDE.md must not appear in SOURCES")
        if 'path = ".aiqt/core/ownership.toml"' not in manifest_text:
            failures.append("carve-out: the ownership map must be a SOURCES member")
        if "managed-block" not in manifest_text or "RULES-INDEX" not in manifest_text:
            failures.append("artifacts: the CLAUDE.md managed-block row is missing")

        # (c) corrupt each generated output (the four self-outputs plus the frozen floor) -> exit 1.
        for rel in GENERATED_OUTPUTS_REL:
            corr = _build_fixture(tmp / ("corrupt-" + rel.replace("/", "_")))
            run_quiet(corr, check=False)
            tp = corr / rel
            tp.write_text(tp.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
            if run_quiet(corr, check=True) != 1:
                failures.append("corruption of {} expected exit 1 (drift)".format(rel))

        # (c2) EN-8: the generated frozen floor lists exactly the {derived, manifest-self, archive}
        # selectors in the write-scope grammar (trailing '/' marks a tree), and lists itself (class
        # derived). A conformant fixture's floor is drift-clean and self-protecting.
        floorf = _build_fixture(tmp / "frozen-floor")
        run_quiet(floorf, check=False)
        floor_obj = json.loads((floorf / FROZEN_REL).read_text(encoding="utf-8"))
        want_floor = {".aiqt/archive/", ".aiqt/frozen.json", ".aiqt/manifest.toml",
                      ".aiqt/release/announce-snippet.txt", ".aiqt/release/root.txt"}
        if floor_obj.get("version") != FROZEN_VERSION or set(floor_obj.get("frozen", [])) != want_floor:
            failures.append("EN-8: the frozen floor is not the expected {{derived, manifest-self, archive}} "
                            "selector set (got {!r})".format(floor_obj))

        # (d) COMPLETENESS / NO-STRAYS / overlap / concern-2 / stale exclusion.
        comp = _build_fixture(tmp / "completeness", extra_files={"stray-new.txt": "x\n"})
        if run_quiet(comp, check=False) != 2:
            failures.append("unclassified tracked path expected exit 2 (COMPLETENESS)")

        stray = _build_fixture(tmp / "strays",
                               own_extra='\n[[release-class]]\npath = "ghost.txt"\nclass = "pack-immutable"\n')
        if run_quiet(stray, check=False) != 2:
            failures.append("classed-but-untracked exact selector expected exit 2 (NO-STRAYS)")

        overlap2 = _build_fixture(tmp / "overlap2",
                                  own_extra='\n[[release-class]]\npattern = "tools/**"\nclass = "derived"\n')
        if run_quiet(overlap2, check=False) != 2:
            failures.append("overlapping selectors expected exit 2 (overlap)")

        c2 = _build_fixture(tmp / "concern2", extra_files={".aiqt/archive/x.txt": "a\n"})
        if run_quiet(c2, check=False) != 2:
            failures.append("a tracked concern-2 namespace path expected exit 2")

        stale = _build_fixture(
            tmp / "stale",
            own_extra='')
        # add a stale exclusion by rewriting the map after build (a concrete exclusion matching nothing)
        (stale / ".aiqt" / "core" / "ownership.toml").write_text(
            _OWN_BASE + '\n[[exclusion]]\npath = "nonexistent-file.txt"\nreason = "stale"\n' + _OWN_TAIL,
            encoding="utf-8")
        _git(stale, "add", "-A")
        _git(stale, "commit", "-q", "-m", "stale", "--no-verify")
        if run_quiet(stale, check=False) != 2:
            failures.append("a stale exclusion (zero matches) expected exit 2")

        # (e) non-UTF-8 not declared binary -> 2; declared binary passes.
        nonutf = _build_fixture(tmp / "nonutf", own_extra='\n[[release-class]]\npath = "blob.bin"\nclass = "pack-immutable"\n',
                                extra_files={"blob.bin": b"\xff\xfe\x00 not-utf8"})
        if run_quiet(nonutf, check=False) != 2:
            failures.append("a non-UTF-8 tracked file not declared binary expected exit 2")
        binok = tmp / "binok"
        _build_fixture(binok, own_extra='\n[[release-class]]\npath = "blob.bin"\nclass = "pack-immutable"\n',
                       extra_files={"blob.bin": b"\xff\xfe\x00 not-utf8"}, do_commit=False)
        # declare it binary and commit
        (binok / ".aiqt" / "core" / "ownership.toml").write_text(
            _OWN_BASE + '\n[[release-class]]\npath = "blob.bin"\nclass = "pack-immutable"\n'
            + _OWN_TAIL.replace("[adopter-extent]",
                                '[checkout]\nbinary = ["blob.bin"]\n\n[adopter-extent]'),
            encoding="utf-8")
        _git(binok, "init", "-q", "--template=")
        _git(binok, "add", "-A")
        _git(binok, "commit", "-q", "-m", "binok", "--no-verify")
        if run_quiet(binok, check=False) != 0:
            failures.append("a declared-binary non-UTF-8 file expected exit 0")

        # (f) git-less root -> 2.
        gitless = tmp / "gitless"
        _build_fixture(gitless, do_commit=False)
        if run_quiet(gitless, check=False) != 2:
            failures.append("a git-less root expected exit 2 (no filesystem-walk fallback)")

        # (g) raw-byte hashing: a CRLF source's recorded sha256 equals sha256 of its exact raw bytes.
        crlf = _build_fixture(tmp / "crlf",
                              own_extra='\n[[release-class]]\npath = "crlf.txt"\nclass = "pack-immutable"\n',
                              extra_files={"crlf.txt": b"line1\r\nline2\r\n"})
        run_quiet(crlf, check=False)
        want = hashlib.sha256(b"line1\r\nline2\r\n").hexdigest()
        if want not in (crlf / MANIFEST_REL).read_text(encoding="utf-8"):
            failures.append("raw-byte hashing: the CRLF file's raw sha256 is not recorded verbatim")

        # (h) a broken CLAUDE.md marker pair -> 2.
        marker = _build_fixture(tmp / "marker", do_commit=False)
        (marker / "CLAUDE.md").write_text("# no markers here\n", encoding="utf-8")
        _git(marker, "init", "-q", "--template=")
        _git(marker, "add", "-A")
        _git(marker, "commit", "-q", "-m", "marker", "--no-verify")
        if run_quiet(marker, check=False) != 2:
            failures.append("a missing CLAUDE.md marker pair expected exit 2")

        # (i) F-235: workflow exclusions are EXACT path= literals, never a glob. With exact literals a
        #     newly tracked .github/workflows/deploy.yml carries no reviewed exclusion and no class, so it
        #     trips COMPLETENESS (exit 2) and cannot silently escape NO-STRAYS. The contrasting glob case
        #     shows exactly what the fix removes: a pattern=".github/workflows/**" would swallow the new
        #     workflow silently (exit 0), which is the escape F-235 closes.
        wf_files = {".github/workflows/currency.yml": "on: push\n",
                    ".github/workflows/quality.yml": "on: push\n",
                    ".github/workflows/deploy.yml": "on: push\n"}
        wf_exact = ('\n[[exclusion]]\npath = ".github/workflows/currency.yml"\nreason = "CI"\n'
                    '\n[[exclusion]]\npath = ".github/workflows/quality.yml"\nreason = "CI"\n')
        wf = _build_fixture(tmp / "workflow-exact", own_extra=wf_exact, extra_files=wf_files)
        if run_quiet(wf, check=False) != 2:
            failures.append("F-235: a new tracked .github/workflows/deploy.yml outside the exact-literal "
                            "exclusion set expected exit 2 (COMPLETENESS: no silent escape)")
        wf_glob = _build_fixture(
            tmp / "workflow-glob",
            own_extra='\n[[exclusion]]\npattern = ".github/workflows/**"\nreason = "CI"\n',
            extra_files=wf_files)
        if run_quiet(wf_glob, check=False) != 0:
            failures.append("F-235: a glob workflow exclusion is expected to (unsafely) swallow a new "
                            "workflow (exit 0); this documents the escape the exact-literal fix removes")

        # (j) F-236: an output with git index mode 100755 fails closed. Generate, git-add the outputs at
        #     their 100644 mode (drift-clean), then chmod one executable and re-stage so the index mode is
        #     100755 -> exit 2. Scoped to the four outputs; *.sh stay legitimately executable.
        modef = _build_fixture(tmp / "outmode")
        run_quiet(modef, check=False)
        _git(modef, "add", "-A")
        if run_quiet(modef, check=True) != 0:
            failures.append("F-236: a freshly generated 100644 output set expected drift-clean exit 0")
        os.chmod(modef / MANIFEST_REL, 0o755)
        _git(modef, "add", MANIFEST_REL)
        if run_quiet(modef, check=True) != 2:
            failures.append("F-236: an output at git index mode 100755 expected exit 2 (generated text is "
                            "never executable)")

        # (k) F-237: the releases.toml minimal row guard. A row with an unknown key and a row missing a
        #     mandatory field each fail closed (exit 2); a header-only record (zero rows) is genesis-clean.
        relbad = _build_fixture(tmp / "releases-badkey", do_commit=False)
        (relbad / ".aiqt" / "core" / "releases.toml").write_text(
            'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ncommit_sha = "deadbeef"\n'
            'bogus = "x"\n', encoding="utf-8")
        _git(relbad, "init", "-q", "--template=")
        _git(relbad, "add", "-A")
        _git(relbad, "commit", "-q", "-m", "relbad", "--no-verify")
        if run_quiet(relbad, check=False) != 2:
            failures.append("F-237: a releases row with an unknown key expected exit 2")
        relmiss = _build_fixture(tmp / "releases-missing", do_commit=False)
        (relmiss / ".aiqt" / "core" / "releases.toml").write_text(
            'format-version = 1\n\n[[release]]\nversion = "1.0.0"\n', encoding="utf-8")
        _git(relmiss, "init", "-q", "--template=")
        _git(relmiss, "add", "-A")
        _git(relmiss, "commit", "-q", "-m", "relmiss", "--no-verify")
        if run_quiet(relmiss, check=False) != 2:
            failures.append("F-237: a releases row missing the mandatory commit_sha expected exit 2")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant git fixture generates and regenerates drift-clean and is "
          "deterministic; SOURCES carve out the manifest, ROOT, snippet, and CLAUDE.md while the map is a "
          "member and the managed block is an artifact; corrupting each of the four outputs fails --check "
          "(exit 1); and an unclassified path, a stray selector, an overlapping selector pair, a tracked "
          "concern-2 path, a stale exclusion, a non-UTF-8 file not declared binary, a git-less root, and a "
          "broken CLAUDE.md marker pair all fail closed (exit 2), while raw-byte hashing records a CRLF "
          "file's exact bytes and a declared-binary file passes; a new tracked .github/workflows file "
          "outside the EXACT-literal exclusion set trips COMPLETENESS (exit 2) while a glob would swallow "
          "it silently (F-235); an output at git index mode 100755 fails closed (exit 2) while a clean "
          "100644 set passes (F-236); and a releases row with an unknown key or a missing mandatory field "
          "each fail closed (exit 2) under the minimal Step-2 row guard (F-237)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
