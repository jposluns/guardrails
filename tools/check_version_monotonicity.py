#!/usr/bin/env python3
"""Cross-history version-monotonicity gate for the pack SemVer. Offline, stdlib only, fail-closed.

check_versions.py sees only the committed files at HEAD, so a rewrite that replaces the whole release
array with a lower but internally-consistent sequence passes it. This gate (GA-3) adds the cross-history
layer: the changelog as it exists now is compared against the changelog as it existed at a baseline commit,
and (once tags exist) against the shipped tags. check_versions.py is not modified; this gate imports its
SEMVER regex and _parse helper (the sibling-import idiom check_rule_placement.py already uses for gen_rules).

Layer A (changelog-history append-only, buildable now). The authoritative history is the git history of
changelog.toml itself. The baseline file is read with `git show BASE:changelog.toml`; the current file is
read from the working tree once, through check_release_cut.working_blob (see _HeadInputs). Invariants:
  M1 (prefix identity): the base release version list is a prefix of the head release version list, same
     version strings in the same order, with no removal, no insertion before an existing entry, and no
     rewrite of an existing entry's version. Title, date, and items of a shipped release MAY change (this
     matches the established post-release curation practice); only `version` is the identity key.
  M2 (no decrease): latest(head) >= latest(base), asserted separately from M1 so a tail rewrite produces a
     finding that names the decrease, not only a prefix mismatch.
  M3 (well-formedness): both sides parse as bare SemVer via the imported helpers; a malformed version on
     either side is fail-closed (exit 2), since the comparison cannot answer its question on bad input.
Pre-release genesis exemption: when the base snapshot carries no changelog release tag AND its releases.toml
is present with zero rows, Layer A reports NOT APPLICABLE and suspends M1/M2 (drafts may be relabeled),
reactivating fully once the first release is recorded (a tag key or a ledger row). The tag-created-but-
unrecorded first-release window is a disclosed residual documented at _base_is_genesis.
Soundness: the protected branch forbids force-push and every change to main passes through this gate at its
merge, so base-vs-head prefix checking composes inductively into monotonicity across all of main's history;
no full-history walk is needed.

Layer B (tag monotonicity, ships DORMANT, activated only by data). A `git tag` returning empty is ambiguous
between "no tags exist" and "tags were not fetched" (a depth-1 CI checkout fetches none), so dormancy is
NEVER decided by probing the environment; it is decided from the single-source file, per guard-input-
soundness. When a release is tagged, its [[release]] entry gains an optional `tag = "vX.Y.Z"` key (inert to
gen_changelog.render_md and check_versions, both of which read neither). Behaviour:
  No release carries `tag`: print NOT APPLICABLE and contribute exit 0 (the honest dormant state).
  Any release carries `tag`:
    T1 the tag value equals "v" + the entry's own version (a malformed tag is a finding);
    T2 the tag resolves locally (a recorded-but-unresolvable tag is exit 2: unfetched and deleted are
       indistinguishable and neither may pass);
    T3 the tagged commit's changelog has that version as its latest release;
    T4 the maximum recorded tag version is not above the head latest version.

Layers C and D (release-order and id-history append-only) are full-row prefix checks over
.aiqt/core/releases.toml and .aiqt/core/id-history.toml (see layer_c and layer_d). Layer D carries ONE
disclosed, self-expiring exception: before the first recorded release (the release-order record present
with ZERO rows at base AND head), and only against a base changelog that already records a tagged
release (so the changelog stays append-only in every later gated transition and the relabel cannot
recur), EVERY [[born]] row may change its born-release once, all moving from one shared never-released
version (recorded as no base or head changelog release or tag version) to the version being cut (the
newly appended head changelog version, strictly above the base latest), accompanied by a PRESENT
.aiqt/release-cut.toml declaration that check_release_cut's transition evaluator accepts for this exact
base-to-head changelog transition (the declaration is a required matching input and author-controlled
intent, not proof of authorization or review; review stays with the protected branch), with the id,
every other field, every other row, and the tombstone and successor sections unchanged, and with no
[[born]] row, an appended one included, carrying the never-released version at head. Every condition
is read from the single-source files, never from git
tags; a condition that cannot be read fails closed (exit 2), a condition that reads false leaves the
plain append-only findings in force, and the exception ends permanently once releases.toml records a
row (see layer_d).

Baseline resolution (Layer A), in precedence order:
  1. an explicit --base REF flag (what CI passes);
  2. with no flag, `git merge-base HEAD origin/main`;
  3. if no baseline resolves, exit 2 with a remediation message (never a silent "nothing to compare, pass").
Three distinguishable NON-error states are each printed explicitly and contribute exit 0:
  - the baseline ref resolves but changelog.toml is absent there (introduced since base);
  - HEAD is the root commit (no parent) AND no explicit --base was given. An explicit --base is always
    resolved and compared, even on a root HEAD, so an unresolvable explicit base fails closed (exit 2);
    a HEAD^ that fails for any reason other than a genuine root (a shallow clone, a broken-parent history)
    is a fail-closed error, never a silent root.
  - the base snapshot is pre-release genesis (no changelog release tag AND a releases.toml present with zero
    rows): Layer A reports NOT APPLICABLE and suspends M1/M2, per the genesis exemption above.
Everything else that prevents the comparison (an unresolvable ref, a git failure, unparseable TOML on either
side) is exit 2; every git return code is checked and a nonzero exit is never treated as an empty result.

  check_version_monotonicity.py [--base REF]   check the invariants (default base: merge-base with origin/main)
  check_version_monotonicity.py --self-test    deterministic self-test (no wall clock, no randomness)

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE
  1  a real finding (append-only violation, decrease, or a tag-layer finding)
  2  malformed input, an unresolvable ref, or a git/read error (fail-closed)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_version_monotonicity.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import io
import os
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_version_monotonicity.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from check_versions import _parse  # noqa: E402  reuse the shipped bare-SemVer parser (M3 well-formedness)
# Reuse the release-cut declaration parser and transition evaluator (one reading of the declaration,
# one schema): the relabel exception grants nothing without a declaration that evaluator accepts.
# Every working-tree input is read through that gate's own no-follow reader (see _HeadInputs).
from check_release_cut import CannotEvaluate as _ReaderRefusal  # noqa: E402
from check_release_cut import evaluate_transition, working_blob  # noqa: E402


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable or unresolvable input is never treated as an empty or clean result."""


# --- git helpers (every return code is checked; a nonzero exit is never treated as empty) -----------

def _git(root, args):
    """Run `git -C root <args>`. Returns the CompletedProcess; the caller inspects returncode. A missing
    git binary is itself a fail-closed condition (the comparison cannot run), surfaced as a GateError."""
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _is_root_commit(root):
    """True only if HEAD is a genuine root commit (its own commit object carries no `parent` header). The
    object is read directly, so this is not fooled by a HEAD^ that merely fails to resolve: a shallow clone
    or a broken-parent history names a parent whose object is absent, and that is raised as a GateError
    (fail-closed), never silently reported as a root. Raises if HEAD itself does not resolve."""
    head = _git(root, ["cat-file", "-p", "HEAD^{commit}"])
    if head.returncode != 0:
        raise GateError("cannot resolve HEAD: {}".format(head.stderr.strip()))
    # A commit object's headers precede the first blank line; a root commit has no `parent ` line.
    header = head.stdout.split("\n\n", 1)[0]
    parents = [line.split()[1] for line in header.splitlines() if line.startswith("parent ")]
    if not parents:
        return True
    for parent in parents:
        if _git(root, ["rev-parse", "--verify", "--quiet", parent + "^{commit}"]).returncode != 0:
            raise GateError("HEAD names parent {} which does not resolve (a shallow clone or a broken-parent "
                            "history); fetch the full history or pass an explicit --base".format(parent))
    return False


def _resolve_commit(root, ref):
    proc = _git(root, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"])
    if proc.returncode != 0:
        raise GateError("cannot resolve baseline ref {!r} to a commit".format(ref))
    return proc.stdout.strip()


def _default_base(root):
    proc = _git(root, ["merge-base", "HEAD", "origin/main"])
    if proc.returncode != 0:
        raise GateError("no baseline resolved: `git merge-base HEAD origin/main` failed; pass --base REF, "
                        "or fetch origin/main")
    return proc.stdout.strip()


def _path_in_commit(root, commit, path):
    proc = _git(root, ["ls-tree", "--name-only", commit, "--", path])
    if proc.returncode != 0:
        raise GateError("git ls-tree failed for {} at {}: {}".format(path, commit, proc.stderr.strip()))
    return proc.stdout.strip() != ""


def _show_file(root, ref, path):
    proc = _git(root, ["show", "{}:{}".format(ref, path)])
    if proc.returncode != 0:
        raise GateError("git show {}:{} failed: {}".format(ref, path, proc.stderr.strip()))
    return proc.stdout


def _show_file_bytes(root, ref, path):
    """As _show_file, but returns the exact blob BYTES: the release-cut declaration digest is computed
    over the complete comparison-base blob, so no text decoding or newline translation may alter it."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "show", "{}:{}".format(ref, path)],
                              capture_output=True)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))
    if proc.returncode != 0:
        raise GateError("git show {}:{} failed: {}".format(
            ref, path, proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def _resolve_tag(root, tag):
    return _git(root, ["rev-parse", "--verify", "--quiet", "refs/tags/" + tag]).returncode == 0


# --- pure logic (M1/M2 and the tag name/ceiling checks; always run in --self-test) ------------------

def check_prefix(base_versions, head_versions):
    """M1. base_versions must be an exact prefix of head_versions. Returns a list of finding strings."""
    findings = []
    if len(base_versions) > len(head_versions):
        findings.append("the base changelog records {} release(s) but head records {}; an existing "
                        "release entry was removed (append-only violation)".format(
                            len(base_versions), len(head_versions)))
    for i in range(min(len(base_versions), len(head_versions))):
        if base_versions[i] != head_versions[i]:
            findings.append("release #{}: base version {} != head version {} (an existing release's "
                            "version was rewritten, reordered, or an entry inserted before it; only "
                            "version is the identity key)".format(i + 1, base_versions[i], head_versions[i]))
    return findings


def check_no_decrease(base_versions, head_versions):
    """M2. latest(head) >= latest(base). Returns a list of finding strings. Callers guarantee both sides
    are well-formed SemVer (M3), so _parse returns a tuple here."""
    if not base_versions or not head_versions:
        return []
    base_latest, head_latest = base_versions[-1], head_versions[-1]
    if _parse(head_latest) < _parse(base_latest):
        return ["the latest head version {} is lower than the latest base version {} "
                "(no-decrease violation)".format(head_latest, base_latest)]
    return []


def _genesis_from_signals(base_release_tags, base_ledger_present, base_ledger_rows):
    """Pure genesis predicate. base_release_tags: the `tag` values (None where absent) of the base
    changelog releases. base_ledger_present: whether the base releases.toml exists. base_ledger_rows:
    its [[release]] rows (empty list for zero). Genesis iff no base release carries a tag AND the
    ledger is present with zero rows. An ABSENT ledger is NOT genesis (protection is retained: the
    source cannot confirm zero shipped)."""
    if any(t is not None for t in base_release_tags):
        return False
    if not base_ledger_present:
        return False
    return not base_ledger_rows


def check_tag_preservation(base_releases, head_releases):
    """A base changelog release that carries a `tag` must keep that exact tag at the same position
    when its version is unchanged. This makes the changelog tag key append-only across a gated
    comparison, so the tag shipment-signal cannot be stripped (which would otherwise let a later base
    re-enter genesis). When the version at the position differs or the entry is gone, check_prefix
    already reports it, so this avoids a redundant finding. Returns a list of finding strings."""
    findings = []
    for i in range(min(len(base_releases), len(head_releases))):
        b, h = base_releases[i], head_releases[i]
        if b.get("tag") is not None and h.get("version") == b.get("version") and h.get("tag") != b.get("tag"):
            findings.append("release {}: base tag {!r} != head tag {!r} (a shipped release's tag "
                            "declaration must not be removed or changed)".format(
                                b.get("version"), b.get("tag"), h.get("tag")))
    return findings


def _tag_version(tag):
    """The numeric part of a `vX.Y.Z` tag string, or None if it is not v-prefixed."""
    return tag[1:] if isinstance(tag, str) and tag.startswith("v") else None


def check_tag_names(tagged):
    """T1. Each recorded tag must equal 'v' + its entry's version. tagged is a list of (version, tag)."""
    findings = []
    for version, tag in tagged:
        if tag != "v" + version:
            findings.append("release {} records tag {!r}; expected {!r} (a tag must be v + version)".format(
                version, tag, "v" + version))
    return findings


def check_tag_ceiling(tagged, head_latest):
    """T4. No recorded tag version may exceed the head latest version. tagged is a list of (version, tag)."""
    findings = []
    head_tup = _parse(head_latest)
    for version, tag in tagged:
        num = _tag_version(tag)
        tup = _parse(num) if num is not None else None
        if tup is not None and head_tup is not None and tup > head_tup:
            findings.append("recorded tag {} is above the head latest version {} (the pack must not move "
                            "behind a shipped tag)".format(tag, head_latest))
    return findings


# --- release extraction (M3 well-formedness on each side) -------------------------------------------

def _releases_from_data(data, side):
    """Validate and return the list of {'version', 'tag'} dicts from parsed changelog data. Raises
    GateError (fail-closed) on a missing array, a non-table entry, or a missing/malformed version."""
    releases = data.get("release")
    if side == "head":
        if not isinstance(releases, list) or not releases:
            raise GateError("changelog.toml has no [[release]] tables")
    else:
        # A baseline with release = [] is a valid (empty) prefix; only an absent array is malformed.
        if not isinstance(releases, list):
            raise GateError("baseline changelog.toml has no [[release]] array")
    out = []
    for idx, rel in enumerate(releases):
        if not isinstance(rel, dict):
            raise GateError("{} release #{} is not a table ({!r})".format(side, idx + 1, rel))
        version = rel.get("version")
        if not isinstance(version, str) or _parse(version) is None:
            raise GateError("{} release #{} has a missing or malformed version {!r}".format(
                side, idx + 1, version))
        out.append({"version": version, "tag": rel.get("tag")})
    return out


class _HeadInputs:
    """The working-tree (HEAD) inputs of one run: changelog.toml, .aiqt/core/releases.toml,
    .aiqt/core/id-history.toml and .aiqt/release-cut.toml. Each is read AT MOST ONCE, at its first
    access, through check_release_cut.working_blob (the release-cut gate's own reader, imported, not
    copied), and every later check parses those captured bytes, so no check re-reads a path that
    another check already judged. That reader opens every path component relative to its parent
    directory descriptor with O_NOFOLLOW, so a symlink at ANY component (a symlinked .aiqt or
    .aiqt/core included) is refused; it opens the final component non-blocking and reads it only
    when the opened descriptor is a regular file, so a FIFO, device or other non-regular file is
    refused before any read (a socket already fails at the open, a directory when its file object
    is made). A refused or unreadable input, or a read that runs out of memory, is GateError (exit 2)
    naming it; an absent one (any component missing) reads as None, and each caller decides what
    absence means. An input no check reaches is never read, so it is never refused either (the
    declaration is read only when layer_d evaluates an otherwise eligible relabel). A hard link is a
    regular file and is read as one; the repository root itself is opened by its path, as the
    release-cut gate opens it."""

    def __init__(self, root):
        self.root = root
        self._raw = {}

    def raw(self, rel):
        """The exact bytes of the head input rel, or None when it is absent."""
        if rel not in self._raw:
            try:
                self._raw[rel] = working_blob(self.root, rel)
            except (OSError, _ReaderRefusal) as exc:
                raise GateError("cannot read {} at HEAD (a symlink at any path component, a "
                                "non-regular file, and an unreadable file are each refused): "
                                "{}".format(rel, exc))
            # A read that runs out of memory is fail-closed and names the input too, as a parse
            # that runs out of memory is in toml() below; it never escapes run() as a traceback.
            except MemoryError as exc:
                raise GateError("cannot read {} at HEAD: out of memory reading it ({})".format(
                    rel, exc))
        return self._raw[rel]

    def toml(self, rel):
        """The head input rel parsed from its captured bytes, or None when it is absent."""
        raw = self.raw(rel)
        if raw is None:
            return None
        try:
            return tomllib.loads(raw.decode("utf-8"))
        # RecursionError too: tomllib raises it (a RuntimeError, not a ValueError) on a deeply
        # nested array or inline table; a UnicodeDecodeError is a ValueError.
        except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
            raise GateError("cannot read {} at HEAD: {}".format(rel, exc))


def _load_head_releases(head):
    data = head.toml("changelog.toml")
    if data is None:
        raise GateError("cannot read changelog.toml at HEAD: it is absent")
    return _releases_from_data(data, "head")


def _parse_base_releases(text):
    try:
        data = tomllib.loads(text)
    # RecursionError too: tomllib raises it (a RuntimeError, not a ValueError) on a deeply
    # nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("baseline changelog.toml does not parse: {}".format(exc))
    return _releases_from_data(data, "base")


# --- the two layers ---------------------------------------------------------------------------------

def _validated_ledger_rows(data, where):
    """The [[release]] rows of a parsed releases.toml whose TOP-LEVEL keys and format-version are
    valid. Anything else is malformed input (GateError, fail-closed): a misspelled section key or an
    unsupported format-version must never read as a zero-row (genesis or relabel-window) ledger."""
    unknown = set(data) - {"format-version", "release"}
    if unknown:
        raise GateError("{} has unknown top-level key(s): {}; expected a subset of "
                        "'format-version', 'release'".format(where, ", ".join(sorted(unknown))))
    fmt = data.get("format-version")
    if not (type(fmt) is int and fmt == 1):
        raise GateError("{} has an unsupported format-version {!r}; expected 1".format(where, fmt))
    return _rows_of(data, "release", where)


def _base_is_genesis(root, base_commit, base_releases):
    """True when no shipment SIGNAL is recorded in the base files: no changelog release carries a tag AND
    a present releases.toml has zero rows. Reads the BASE snapshot only. An absent base releases.toml is
    NOT genesis (guard-input-soundness). A present-but-unparseable ledger, or one whose TOP-LEVEL keys or
    format-version are invalid, is fail-closed (GateError, exit 2), mirroring layer_c's base-parse handling;
    a ledger with rows (even malformed rows) is simply non-genesis (protection retained), with per-row
    validation left to layer_c and the manifest gate."""
    # DISCLOSED RESIDUAL: the gate decides shipment from single-source files (the changelog tag key and
    # releases.toml rows), not by probing git tags, whose absence is ambiguous under a shallow fetch
    # (matching Layer B's stated design). A TRANSIENT first-release window therefore exists where a git
    # tag object has been created but its changelog tag key / releases.toml row has not yet landed, and in
    # that window the base reads as genesis. This residual is bounded (first release only; once the first
    # ledger row lands, every later base has prior rows) and is closed by the release process landing the
    # tag key / ledger row promptly and not basing a relabel PR in that window.
    present = _path_in_commit(root, base_commit, RELEASES_REL)
    rows = []
    if present:
        try:
            base_rel = tomllib.loads(_show_file(root, base_commit, RELEASES_REL))
        # RecursionError too: tomllib raises it (a RuntimeError, not a ValueError) on a deeply
        # nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
        except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
            raise GateError("baseline {} does not parse: {}".format(RELEASES_REL, exc))
        rows = _validated_ledger_rows(base_rel, "baseline " + RELEASES_REL)
    return _genesis_from_signals([r.get("tag") for r in base_releases], present, rows)


def layer_a(root, base, head_releases):
    """Changelog-history append-only. Prints its own status; returns a list of finding strings. Raises
    GateError on any fail-closed condition."""
    if base is None:
        # No explicit base: a genuine root HEAD has nothing to compare against (the honest NOT APPLICABLE);
        # otherwise resolve the default merge-base. An explicit --base is always resolved below, even on a
        # root HEAD, so an unresolvable explicit base fails closed rather than passing as NOT APPLICABLE.
        if _is_root_commit(root):
            print("changelog-history: NOT APPLICABLE (HEAD is the root commit; no baseline to compare)")
            return []
        base = _default_base(root)
    base_commit = _resolve_commit(root, base)
    if not _path_in_commit(root, base_commit, "changelog.toml"):
        print("changelog-history: NOT APPLICABLE (changelog.toml is absent at base {}; introduced since "
              "base)".format(base))
        return []
    base_releases = _parse_base_releases(_show_file(root, base_commit, "changelog.toml"))
    if _base_is_genesis(root, base_commit, base_releases):
        print("changelog-history: NOT APPLICABLE (base {} is pre-release: no changelog release carries a "
              "tag and {} has zero release rows; append-only release identity is enforced from the first "
              "recorded release. See the tag-created-but-unrecorded residual at "
              "_base_is_genesis)".format(base, RELEASES_REL))
        return []
    base_versions = [r["version"] for r in base_releases]
    head_versions = [r["version"] for r in head_releases]
    findings = check_prefix(base_versions, head_versions) + check_no_decrease(base_versions, head_versions)
    findings += check_tag_preservation(base_releases, head_releases)
    if not findings:
        print("changelog-history: PASS (base {}: {} base release(s) prefix-preserved into {} head "
              "release(s), no decrease)".format(base, len(base_versions), len(head_versions)))
    return findings


def layer_b(root, head_releases):
    """Tag monotonicity, dormant until a release records a tag. Prints its own status; returns a list of
    finding strings. Raises GateError on any fail-closed condition."""
    tagged = [(r["version"], r["tag"]) for r in head_releases if r.get("tag") is not None]
    if not tagged:
        print("tag-monotonicity: NOT APPLICABLE: no release records a tag; tag monotonicity activates "
              "with the first tagged release")
        return []
    for version, tag in tagged:
        if not isinstance(tag, str):
            raise GateError("release {} has a non-string tag {!r}".format(version, tag))
    findings = check_tag_names(tagged)
    head_latest = head_releases[-1]["version"]
    for version, tag in tagged:
        if not _resolve_tag(root, tag):
            raise GateError("release {} records tag {} but no such tag object is visible (fetch tags, "
                            "or the tag was deleted)".format(version, tag))
        tagged_versions = [r["version"] for r in _parse_base_releases(_show_file(root, tag, "changelog.toml"))]
        if not tagged_versions or tagged_versions[-1] != version:
            findings.append("tag {} points at a commit whose latest release is {}, not {}".format(
                tag, tagged_versions[-1] if tagged_versions else "(none)", version))
    findings += check_tag_ceiling(tagged, head_latest)
    if not findings:
        print("tag-monotonicity: PASS ({} tagged release(s) verified)".format(len(tagged)))
    return findings


RELEASES_REL = ".aiqt/core/releases.toml"
IDHISTORY_REL = ".aiqt/core/id-history.toml"
RELEASE_CUT_REL = ".aiqt/release-cut.toml"
REGISTER_SECTIONS = ("born", "tombstone", "successor")


def _prefix_findings(base_rows, head_rows, label):
    """FULL-ROW prefix identity (2.4/7.3): base_rows must be an exact prefix of head_rows, comparing the
    WHOLE row (dict equality, so ANY field edit to a prior row breaks the prefix, not merely a key edit).
    Returns a list of finding strings. base_rows/head_rows are lists of row dicts already validated to be
    tables by the caller."""
    findings = []
    if len(base_rows) > len(head_rows):
        findings.append("{}: the base records {} row(s) but head records {}; an existing row was removed "
                        "(append-only violation)".format(label, len(base_rows), len(head_rows)))
    for i in range(min(len(base_rows), len(head_rows))):
        if base_rows[i] != head_rows[i]:
            findings.append("{} row #{}: an existing row was edited or reordered (any edit to a prior "
                            "row is a FAIL; append-only)".format(label, i + 1))
    return findings


def _rows_of(data, key, where):
    """The array-of-tables under `key`, validated. A present-but-non-array section, or a non-table row,
    is malformed input (GateError). An absent section is a valid empty list."""
    rows = data.get(key, [])
    if not isinstance(rows, list):
        raise GateError("{}: the {!r} section is not an array of tables".format(where, key))
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise GateError("{}: {!r} row #{} is not a table".format(where, key, i))
    return rows


def _resolve_base_commit(root, base):
    """Resolve the baseline commit for the append-only prefix layers, mirroring layer_a's precedence:
    None means 'no baseline to compare' (a genuine root-commit HEAD with no explicit --base), for which
    the prefix layers report NOT APPLICABLE. An explicit --base is always resolved (fail-closed if it
    does not resolve), even on a root HEAD."""
    if base is None:
        if _is_root_commit(root):
            return None
        base = _default_base(root)
    return _resolve_commit(root, base)


def layer_c(root, base_commit, head_data):
    """Release-order append-only (2.4): base releases.toml rows are an exact full-row prefix of head
    rows. NOT APPLICABLE while HEAD has no baseline, the record is absent at HEAD, or the file is absent
    at base (introduced since base); zero rows is a valid empty prefix. head_data is the parsed HEAD
    releases.toml, or None when absent at HEAD. The absent-at-HEAD case is a sound delegation, not a
    fail-open: releases.toml is a manifest-covered SOURCES member, so DELETING it makes check_manifest
    fail closed (exit 2) on the missing SOURCES member and on read_genesis (VC-4 QA #10, verified); this
    prefix layer therefore need only apply when the record exists."""
    if head_data is None:
        print("release-order-append-only: NOT APPLICABLE ({} is absent at HEAD)".format(RELEASES_REL))
        return []
    if base_commit is None:
        print("release-order-append-only: NOT APPLICABLE (no baseline to compare)")
        return []
    if not _path_in_commit(root, base_commit, RELEASES_REL):
        print("release-order-append-only: NOT APPLICABLE ({} is absent at base; introduced since "
              "base)".format(RELEASES_REL))
        return []
    try:
        base_data = tomllib.loads(_show_file(root, base_commit, RELEASES_REL))
    # RecursionError too: tomllib raises it (a RuntimeError, not a ValueError) on a deeply
    # nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("baseline {} does not parse: {}".format(RELEASES_REL, exc))
    base_rows = _rows_of(base_data, "release", "baseline " + RELEASES_REL)
    head_rows = _rows_of(head_data, "release", RELEASES_REL)
    findings = _prefix_findings(base_rows, head_rows, "releases.toml [[release]]")
    if not findings:
        print("release-order-append-only: PASS ({} base row(s) prefix-preserved into {} head "
              "row(s))".format(len(base_rows), len(head_rows)))
    return findings


def _born_relabel_shape(base_data, head_data):
    """The row-level SHAPE of the one-time born-release relabel (see layer_d): EVERY base [[born]] row
    changed ONLY its born-release value, all moving from ONE shared old version string to ONE shared
    new version string, with the same keys and values otherwise (the id included), no row removed, no
    base row left unchanged and no appended row carrying the old version (a PARTIAL relabel is never a
    relabel shape: no row, base or appended, may hold the never-released version at head), and the
    [[tombstone]] and [[successor]] sections exactly EQUAL on both sides (appending to those sections
    beside a relabel is refused; appending NEW [[born]] rows beyond the base rows at any OTHER version
    stays deliberately allowed, exactly as in an ordinary append-only transition). Id uniqueness is
    not judged here: an appended row that repeats an existing id passes this shape, as it passes an
    ordinary append; check_clauses.load_register refuses a duplicate [[born]] id (exit 2).
    Returns (old_version, new_version, relabelled_row_count) when that whole-register shape holds with
    at least one base row, else None. Pure row logic over already-parsed data; whether the relabel is
    PERMITTED is _relabel_window_reason's question."""
    for section in ("tombstone", "successor"):
        if (_rows_of(base_data, section, "baseline " + IDHISTORY_REL)
                != _rows_of(head_data, section, IDHISTORY_REL)):
            return None
    base_rows = _rows_of(base_data, "born", "baseline " + IDHISTORY_REL)
    head_rows = _rows_of(head_data, "born", IDHISTORY_REL)
    if not base_rows or len(base_rows) > len(head_rows):
        return None
    old_versions, new_versions = set(), set()
    for base_row, head_row in zip(base_rows, head_rows):
        if set(base_row) != set(head_row):
            return None
        if any(base_row[key] != head_row[key] for key in base_row if key != "born-release"):
            return None
        old, new = base_row.get("born-release"), head_row.get("born-release")
        if not isinstance(old, str) or not isinstance(new, str) or old == new:
            return None
        old_versions.add(old)
        new_versions.add(new)
    if len(old_versions) != 1 or len(new_versions) != 1:
        return None
    old_version = old_versions.pop()
    # The never-released version must survive in NO row: an appended row carrying it would keep at
    # head the very version every base row just moved away from (the hole a partial relabel leaves).
    for head_row in head_rows[len(base_rows):]:
        if head_row.get("born-release") == old_version:
            return None
    return old_version, new_versions.pop(), len(base_rows)


def _relabel_window_reason(root, base_commit, head_releases, old_version, new_version, head=None):
    """Every file-sourced condition of the one-time born-release relabel (see layer_d), checked from
    the single-source files only, never from git tags (guard-input-soundness, as _base_is_genesis).
    Returns None when every condition holds, else a short reason naming the FIRST condition that reads
    false (the caller then keeps the plain append-only findings). Raises GateError (exit 2) when a
    condition CANNOT be read: an unparseable or schema-invalid release-order record on either side, an
    unparseable base changelog, a head changelog, release-order record or declaration that the
    no-follow reader refuses or cannot read (see _HeadInputs), or a declaration or changelog that
    check_release_cut's parsers refuse; an unreadable condition is never a pass. head is the run's
    _HeadInputs, so each head input is read at most once per run; a direct call without it reads through a
    fresh one. The release-cut declaration is REQUIRED here, not optional: the relabel is granted only when
    check_release_cut.evaluate_transition accepts this exact base-to-head changelog transition under
    the present declaration (full five-field schema, operation, previous-version, next-version and the
    digest of the complete base changelog blob all matching, exactly one appended release, and the
    declaration absent or different at base, so a stale declaration counts for nothing). The
    declaration is a required matching input, not proof of authorization or review."""
    if head is None:
        head = _HeadInputs(root)
    # The pre-first-release window: the release-order record exists on BOTH sides with ZERO rows (the
    # relabel self-expires once releases.toml records a row; rows are append-only under layer_c, so
    # the window can never reopen). An ABSENT record cannot confirm zero shipped and refuses the
    # relabel (the same stance _genesis_from_signals takes); a present-but-invalid one is fail-closed.
    if not _path_in_commit(root, base_commit, RELEASES_REL):
        return "{} is absent at base, so zero shipped releases cannot be confirmed".format(RELEASES_REL)
    try:
        base_ledger = tomllib.loads(_show_file(root, base_commit, RELEASES_REL))
    # RecursionError too: tomllib raises it (a RuntimeError, not a ValueError) on a deeply
    # nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("baseline {} does not parse: {}".format(RELEASES_REL, exc))
    if _validated_ledger_rows(base_ledger, "baseline " + RELEASES_REL):
        return "the base {} already records a release row".format(RELEASES_REL)
    head_ledger = head.toml(RELEASES_REL)
    if head_ledger is None:
        return "{} is absent at HEAD, so zero shipped releases cannot be confirmed".format(RELEASES_REL)
    if _validated_ledger_rows(head_ledger, RELEASES_REL):
        return "the head {} already records a release row".format(RELEASES_REL)
    # The old version must be well-formed and NEVER RELEASED: recorded as no base changelog release
    # version and as no base changelog tag version.
    if _parse(old_version) is None:
        return "the old born-release value {!r} is not a bare SemVer version".format(old_version)
    if not _path_in_commit(root, base_commit, "changelog.toml"):
        return "changelog.toml is absent at base, so the appended target version cannot be confirmed"
    base_releases = _parse_base_releases(_show_file(root, base_commit, "changelog.toml"))
    base_versions = [r["version"] for r in base_releases]
    base_tag_versions = [_tag_version(r["tag"]) for r in base_releases if r.get("tag") is not None]
    # One-time durability: the relabel is granted only against a base changelog that already records a
    # tagged release. Such a base is never genesis, so layer_a keeps the changelog append-only (prefix
    # identity plus tag preservation) across EVERY later gated transition; the version cut here
    # therefore stays recorded, and the recorded-release exclusions below refuse any later relabel away
    # from it, making "once" an inductive guarantee rather than an assumption. At an untagged
    # pre-release base the changelog may still be rewritten (the layer_a genesis exemption), so a
    # one-time guarantee cannot be read from the files, and that base is refused.
    if not any(r.get("tag") is not None for r in base_releases):
        return ("no base changelog release records a tag, so the base changelog is not append-only "
                "protected and a one-time relabel cannot be guaranteed")
    if old_version in base_versions:
        return "the old version {} is a recorded base changelog release".format(old_version)
    if old_version in base_tag_versions:
        return "the old version {} is a recorded base changelog tag version".format(old_version)
    # The new version must be THE VERSION BEING CUT: the head changelog appends at least one release
    # to the base changelog, the new version is the appended head latest, and it is strictly above
    # the base latest (a relabel may never accompany a rewrite, a no-op cut, or a non-increase).
    head_versions = [r["version"] for r in head_releases]
    if (len(head_versions) <= len(base_versions)
            or head_versions[:len(base_versions)] != base_versions):
        return "the head changelog does not append a new release to the base changelog"
    if new_version != head_versions[-1]:
        return "the new version {} is not the newly appended head changelog version {}".format(
            new_version, head_versions[-1])
    if base_versions and _parse(new_version) <= _parse(base_versions[-1]):
        return "the new version {} is not above the latest base changelog version {}".format(
            new_version, base_versions[-1])
    # The old version must stay never-released on the HEAD side too: a transition may not both relabel
    # away from a version and record that same version as a head changelog release or tag version.
    if old_version in head_versions:
        return "the old version {} is recorded as a head changelog release".format(old_version)
    if old_version in [_tag_version(r["tag"]) for r in head_releases if r.get("tag") is not None]:
        return "the old version {} is recorded as a head changelog tag version".format(old_version)
    # The cut must be DECLARED, and the declaration must match this exact base-to-head changelog
    # transition. The declaration is read and judged ONLY by check_release_cut's parser and transition
    # evaluator (no second schema here), over the exact file bytes: the base side from the comparison-
    # base blob, the head side from the bytes _HeadInputs captured through the release-cut gate's own
    # working-file reader (check_release_cut.working_blob): a symlink at any path component, or a
    # final component that is not a regular file, is refused (exit 2) before any read, never
    # followed, and the head changelog bytes judged here are the ones run() already parsed. The
    # five-field schema, the operation, the previous and next versions, the
    # complete-base-changelog digest, the single-append shape, and the staleness rule (a
    # declaration already present unchanged at base counts for nothing) are all the release-cut gate's
    # own rules; a matching declaration is a required input, not proof of authorization or review. An
    # ABSENT declaration refuses the relabel; an unreadable or schema-invalid input on either side is
    # fail-closed.
    head_cut_bytes = head.raw(RELEASE_CUT_REL)
    if head_cut_bytes is None:
        return ("{} is absent at HEAD; the relabel requires a release-cut declaration that "
                "matches the cut".format(RELEASE_CUT_REL))
    base_cut_bytes = None
    if _path_in_commit(root, base_commit, RELEASE_CUT_REL):
        base_cut_bytes = _show_file_bytes(root, base_commit, RELEASE_CUT_REL)
    head_changelog_bytes = head.raw("changelog.toml")
    if head_changelog_bytes is None:
        raise GateError("cannot read changelog.toml at HEAD: it is absent")
    code, detail = evaluate_transition(_show_file_bytes(root, base_commit, "changelog.toml"),
                                       head_changelog_bytes, base_cut_bytes, head_cut_bytes)
    if code == 2:
        raise GateError("release-cut evaluation of the declared cut: {}".format(detail))
    if code != 0:
        return "the release-cut evaluation refuses this transition: {}".format(detail)
    return None


def layer_d(root, base_commit, head_data, head_releases, head):
    """Id-history append-only (7.3): per section (born, tombstone, successor), base register rows are an
    exact full-row prefix of head register rows. NOT APPLICABLE while HEAD has no baseline, the register
    is absent at HEAD, or the file is absent at base. head_data is the parsed HEAD id-history.toml, or
    None when absent at HEAD. As with layer_c, the absent-at-HEAD case is a sound delegation, not a
    fail-open: id-history.toml is a manifest-covered SOURCES member, so DELETING it makes check_manifest
    fail closed (exit 2) on the missing SOURCES member (VC-4 QA #10, verified).
    ONE-TIME RELABEL EXCEPTION (self-expiring). Before the first recorded release ONLY, the [[born]]
    rows may change their born-release once, so identifiers drafted under a version that will never
    ship can be relabelled to the version actually being cut. Granted only when EVERY condition holds,
    each read from its single-source file (never from git tags): the release-order record is present
    with ZERO rows at base AND at head; EVERY base [[born]] row differs ONLY in born-release, all
    moving from ONE shared old version to ONE shared new version, with no row removed, no base row left
    unchanged, no appended row carrying the old version, and the tombstone/successor sections exactly
    equal (appended [[born]] rows beyond the base rows stay allowed at any other version, as in any
    ordinary transition); the base changelog records a tagged
    release, which keeps it append-only in every later gated transition, so the version cut here stays
    recorded and a second relabel away from it is refused for good (at an untagged pre-release base the
    changelog is rewritable under the layer_a genesis exemption, so "once" could not be read from the
    files and the relabel is refused there); the old version is well-formed and recorded as no base or
    head changelog release or tag version; the new version is the newly appended head changelog latest,
    strictly above the base latest; and a PRESENT release-cut declaration matches this exact
    transition under check_release_cut.evaluate_transition (five-field schema, operation,
    previous-version, next-version and complete-base-changelog digest all checked, exactly one appended
    release, never a declaration left unchanged from base; the declaration is a required matching
    input, not proof of authorization or review). A condition that cannot be read is
    fail-closed (GateError, exit 2); one that reads false leaves the plain append-only findings
    (exit 1) exactly as without the exception. head_releases is the validated HEAD changelog release
    list run() already loaded, and head is the run's _HeadInputs, through which the relabel conditions
    reuse the head bytes already read and read the declaration. The exception ends permanently once
    releases.toml records a row: the window keys on ledger rows, which are themselves append-only
    under layer_c; a recorded changelog tag does not CLOSE the window (only a ledger row does), it is
    one of the window's preconditions."""
    if head_data is None:
        print("id-history-append-only: NOT APPLICABLE ({} is absent at HEAD)".format(IDHISTORY_REL))
        return []
    if base_commit is None:
        print("id-history-append-only: NOT APPLICABLE (no baseline to compare)")
        return []
    if not _path_in_commit(root, base_commit, IDHISTORY_REL):
        print("id-history-append-only: NOT APPLICABLE ({} is absent at base; introduced since "
              "base)".format(IDHISTORY_REL))
        return []
    try:
        base_data = tomllib.loads(_show_file(root, base_commit, IDHISTORY_REL))
    # RecursionError too: tomllib raises it (a RuntimeError, not a ValueError) on a deeply
    # nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("baseline {} does not parse: {}".format(IDHISTORY_REL, exc))
    findings = []
    for section in REGISTER_SECTIONS:
        base_rows = _rows_of(base_data, section, "baseline " + IDHISTORY_REL)
        head_rows = _rows_of(head_data, section, IDHISTORY_REL)
        findings += _prefix_findings(base_rows, head_rows, "id-history [[{}]]".format(section))
    if findings:
        relabel = _born_relabel_shape(base_data, head_data)
        if relabel is not None:
            old_version, new_version, changed = relabel
            if _relabel_window_reason(root, base_commit, head_releases,
                                      old_version, new_version, head) is None:
                print("id-history-append-only: PASS (one-time pre-release born-release relabel "
                      "{} -> {} on {} [[born]] row(s), every other row and section prefix-preserved; "
                      "this exception ends once {} records a release row)".format(
                          old_version, new_version, changed, RELEASES_REL))
                return []
    if not findings:
        print("id-history-append-only: PASS (born/tombstone/successor sections prefix-preserved)")
    return findings


def _load_head_toml(head, rel):
    """Parse a HEAD record for the prefix layers from the run's _HeadInputs. Returns None when the
    file is absent at HEAD (the prefix layer then reports NOT APPLICABLE; the file's REQUIRED presence
    is owned by check_manifest, which fails closed exit 2 on a deleted manifest-covered register, VC-4
    QA #10 verified). A present file the no-follow reader refuses, an unreadable one, or an
    unparseable one is fail-closed here too (GateError, exit 2)."""
    return head.toml(rel)


def run(root, base):
    """Run every layer against `root`, resolving the baseline from `base` (None means the default
    merge-base). Returns the exit code 0/1/2. Each head input the run accesses is read at most once,
    through one _HeadInputs."""
    try:
        head = _HeadInputs(root)
        head_releases = _load_head_releases(head)
        findings = layer_a(root, base, head_releases) + layer_b(root, head_releases)
        base_commit = _resolve_base_commit(root, base)
        findings += layer_c(root, base_commit, _load_head_toml(head, RELEASES_REL))
        findings += layer_d(root, base_commit, _load_head_toml(head, IDHISTORY_REL), head_releases,
                            head)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} version-monotonicity finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: version-monotonicity gate passed (see each layer's status line above for what was "
          "checked or exempted)")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure-function cases (prefix/no-decrease/tag logic) always run and are deterministic. The git-level cases
# build throwaway repositories in a private tempdir; where git or a writable tempdir is unavailable they
# are a NAMED NOT RUN failure (never a skip beside a PASS); CI always has both. No wall clock, no
# randomness.

def _changelog_text(versions, tag_on=None):
    tag_on = tag_on or {}
    lines = ['title = "Changelog: self-test"', 'note = "self-test"', ""]
    for v in versions:
        lines += ["[[release]]", 'title = "r"', 'version = "{}"'.format(v), 'date = "2026-01-01"']
        if v in tag_on:
            lines.append('tag = "{}"'.format(tag_on[v]))
        lines += ['items = ["x"]', ""]
    return "\n".join(lines)


def _run_quiet(root, base):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root, base)


def _run_capture(root, base):
    """As _run_quiet, but returns (exit_code, stdout_text) so a case can assert a status line."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = run(root, base)
    return rc, out.getvalue()


def _run_streams(root, base):
    """As _run_capture, but returns (exit_code, stdout_text, stderr_text) so a case can assert which
    input a fail-closed refusal names."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = run(root, base)
    return rc, out.getvalue(), err.getvalue()


def _not_run(failures, case, cause):
    """Record a self-test case that could not run as a NAMED failure (a counted cannot-evaluate that
    fails the run), never a printed note beside a PASS: coverage that did not run is not coverage."""
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


_GIT_CASES = ("the git-level history, tag, release-order/id-history append-only, genesis-exemption, "
              "tag-preservation, and one-time born-release relabel cases over real repositories")
_EVERY_CASE = "every self-test case"
_SUPPRESS_VECTORS_ENV = "AIQT_SELFTEST_SUPPRESS_VECTORS"
# ONE constant ties the git probe's exact argv to the git-less arm's launch match; the
# probe-argv vector asserts the default probe launches exactly this argv, the arm-refusal
# vector asserts the simulated git-less arm refuses that same launch, and the
# probe-default-entry vector asserts self_test_main reaches _default_git_probe, so neither
# side can silently drift off the other and turn trivially green on hosts where git exists.
_GIT_PROBE_ARGV = ("git", "--version")
_NO_VECTORS_PASS_NOTE = "SELF-TEST PASS (vectors not run in this nested call)"

# Every fresh Python VECTOR-ENTRY child (the self-test children the bounded launcher starts)
# carries this depth marker: a nested entry that reaches the vectors with it PRESENT, with
# any value, the empty string included, refuses to run them (a child never launches
# self-test vector children of its own; the launcher-timeout child's plain descendant and
# the parent's own git launches are deliberate and are not vector children), so a reverted
# marked-child gate or no-vectors call ends at depth 1 with a named failure, never an
# unbounded chain; an inherited value, even an empty one, trips the same refusal (fail
# closed: presence, never truthiness, is the trigger), and the refusal text names the
# inheritance as a possible cause.
_CHILD_DEPTH_ENV = "AIQT_SELFTEST_CHILD_DEPTH"
_CHILD_TIMEOUT = 120  # seconds: the wait bound the launcher applies to every vector-entry child
_CHILD_DRAIN_TIMEOUT = 10  # seconds: each bounded post-deadline step (drain, exit wait, reap)

# Reentrancy latch for the depth-refusal DRIVE in _not_run_vectors: a nested vectors call
# that reaches the drive with the refusal reverted must not run the three-value loop again
# (three more drives per level: an exponential chain no recursion limit ends), so the loop
# runs only at the outermost level and a nested drive falls through to its first spied
# launch attempt, which the outer drive reports as the named failure, bounded in process.
_DEPTH_DRIVE_ACTIVE = False


class _ChildOutcome:
    """What _run_bounded_child observed. returncode is None when the leader could not be
    reaped within the reap bound, and ALSO whenever anchor_lost is True: a status this
    launcher did not collect itself is never reported (CPython's Popen.wait turns the ECHILD
    of an already reaped child into 0; this launcher reaps with its own waitid instead, so a
    lost anchor can never read as an exit of 0); stdout and stderr are BYTES (text-mode
    newline translation would split a CR-separated prefix into a line of its own and defeat
    an exact line match); timed_out means the wait bound elapsed first, so the group kill
    was attempted (sent only when the anchor check before it found the leader UNREAPED);
    ready is False when a requested readiness handshake never produced a complete line;
    survivors means the group still had a member when the bounded post-reap OBSERVATION
    ended (nothing is ever signalled after the reap); anchor_lost means an anchor check or
    the reap found the leader ALREADY REAPED by another path (SIGCHLD ignored in a way the
    launch check cannot see, or an outside reap), or the cleanup's anchor check could not
    be made, so every destructive group signal still to come was withheld. Ordinary
    vector-entry callers treat timed_out,
    a None returncode, ready False and survivors True each as a failure, so a lost anchor
    reaches every one of them as a failure through its None returncode; the launcher's own
    NEGATIVE vectors are the deliberate exceptions (launcher-timeout and
    launcher-readiness-window REQUIRE timed_out True, launcher-group-replacement REQUIRES
    survivors True, and the launcher-anchor vectors REQUIRE anchor_lost True)."""

    def __init__(self, returncode, stdout, stderr, timed_out, survivors, ready=True,
                 anchor_lost=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.survivors = survivors
        self.ready = ready
        self.anchor_lost = anchor_lost


def _child_depth_marker_present():
    """True when _CHILD_DEPTH_ENV is set AT ALL, the empty string included: presence is what
    the bounded launcher guarantees and what an inheriting caller may hand down, so the
    refusal keyed on this helper triggers on membership, never on truthiness (an inherited
    empty value refuses the same way: fail closed)."""
    return _CHILD_DEPTH_ENV in os.environ


def _ready_line_complete(ready_path):
    """True once the readiness file exists and holds a complete (LF-terminated) line."""
    try:
        with open(ready_path, "rb") as fh:
            return fh.read().endswith(b"\n")
    except OSError:
        return False


def _await_readiness(proc, ready_path):
    """Wait, bounded by _CHILD_TIMEOUT, for the child's readiness handshake: a complete
    LF-terminated line at ready_path, which the child writes BEFORE entering the phase the
    caller's timeout window is meant to cover. Returns False when the bound elapses or the
    leader exits without completing it; the leader is NOT reaped here either way."""
    import time
    deadline = time.monotonic() + _CHILD_TIMEOUT
    while True:
        if _ready_line_complete(ready_path):
            return True
        exited = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        if exited is not None or time.monotonic() >= deadline:
            # one last look: the write may have landed between the read and the exit
            return _ready_line_complete(ready_path)
        time.sleep(0.05)


def _drain_pipes_unreaped(proc, deadline, out, err):
    """Read the child's pipes toward EOF until DEADLINE WITHOUT reaping the leader (no
    communicate, no wait): the unreaped leader keeps anchoring the group id so a later group
    signal can never land on a reused id. Each pipe is closed exactly once, at its own EOF;
    a pipe still open when the deadline passes is left for the launcher's own close. Returns
    True when every pipe reached EOF within the bound."""
    import selectors
    import time
    sel = selectors.DefaultSelector()
    try:
        for pipe, buf in ((proc.stdout, out), (proc.stderr, err)):
            if pipe is not None and not pipe.closed:
                os.set_blocking(pipe.fileno(), False)
                sel.register(pipe, selectors.EVENT_READ, buf)
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            for key, _events in sel.select(min(remaining, 0.5)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if chunk:
                    key.data.extend(chunk)
                else:
                    sel.unregister(key.fileobj)
                    key.fileobj.close()
        return True
    finally:
        sel.close()


def _await_exit_unreaped(pid, deadline):
    """Wait, bounded by DEADLINE, for PID to exit WITHOUT reaping it (WNOWAIT): the exited
    leader stays an unreaped zombie that keeps anchoring its process-group id until the
    launcher has sent the final group signal. Returns True once the exit was observed."""
    import time
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _leader_unreaped(proc):
    """The ANCHOR CHECK made before EVERY destructive group signal: True while the leader is
    still this process's own UNREAPED child (live, or a zombie), OBSERVED with WNOWAIT and
    never reaped here; False when the anchor is LOST (Popen already holds a returncode, or
    the observation raises ChildProcessError because another path reaped the leader). Any
    other failure propagates, so an anchor that cannot be verified is never treated as
    held. It relies on two PRECONDITIONS: SIGCHLD is not ignored (_run_bounded_child
    refuses to launch under SIG_IGN; ignored anyway, the kernel reaps the leader itself and
    this check reports the anchor lost), and this process is the leader's ONLY reaper.
    What it cannot exclude: an outside reap landing
    AFTER this observation and BEFORE the signal it guards (no unsynchronized observation
    can); the launcher's reap then finds the leader gone and reports the anchor lost, but
    that signal has already been sent."""
    if proc.returncode is not None:
        return False
    try:
        os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
    except ChildProcessError:
        return False
    return True


def _reap_leader(pid, deadline):
    """The launcher's ONE reap, bounded by DEADLINE: waitid WITHOUT WNOWAIT, so the status
    comes from this very call. Returns (returncode, lost): the collected returncode (the
    exit status, or minus the signal number for a killed leader) with lost False; (None,
    True) when the leader is no longer this process's child (ChildProcessError: another path
    reaped it, so no status is collected and none is made up); (None, False) when the bound
    elapsed first."""
    import time
    while True:
        try:
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG)
        except ChildProcessError:
            return None, True
        if info is not None:
            if info.si_code == os.CLD_EXITED:
                return info.si_status, False
            return -info.si_status, False  # CLD_KILLED or CLD_DUMPED: the signal number
        if time.monotonic() >= deadline:
            return None, False
        time.sleep(0.05)


class _SigchldIgnoredError(RuntimeError):
    """Raised by _run_bounded_child BEFORE anything is launched when SIGCHLD is ignored
    (SIG_IGN) in this process. The kernel would then reap each leader itself at its exit,
    so no anchor check could hold the leader's process-group id and the launcher's own
    reap could collect no status. A parent's ignore survives exec, so a self-test started
    by such a parent inherits it and is refused under this name, not by a bare
    ChildProcessError from a later wait."""


def _run_bounded_child(args, env, timeout=_CHILD_TIMEOUT, ready_path=None):
    """The one bounded launcher every fresh Python VECTOR-ENTRY self-test child goes
    through; the git-level FIFO case (R35) also runs its gate-run children through it, for the
    same bounds. That scope is deliberately narrower than every child process of this tool: the
    parent self-test's direct git launches (_default_git_probe; the fixture repositories'
    git init/config/add/commit/tag/rev-parse calls; and the gate's own _git launches,
    rev-parse, cat-file, merge-base, ls-tree and show, which the live run and every
    in-process _run_quiet self-test drive perform, all run without this launcher) and the
    launcher-timeout child's own plain descendant are OUTSIDE this launcher and its
    bounds.
    args is the argv tail after the fixed [sys.executable, "-I", "-B"] head, so every
    vector-entry child runs this interpreter. Bounds: (1) the child's environment carries
    _CHILD_DEPTH_ENV, whose presence-refusal at the top of _not_run_vectors stops any child
    of this tool from launching vector children of its own, so a reverted guard ends at
    depth 1; (2) the child leads its OWN session and so its own process group, and EVERY
    destructive group signal, the first timeout kill and the final one alike, is preceded
    by the ANCHOR CHECK (_leader_unreaped: the leader OBSERVED with WNOWAIT, never reaped
    first; the pipes are drained and the exit observed the same way) and is sent only when
    that check finds the leader still unreaped (live, or a zombie); a leader another path
    already reaped means the anchor is LOST, and an observation failing any other way leaves
    it UNVERIFIABLE (that failure also raises: during the lifecycle it is the primary
    exception, in the cleanup a kept stage failure); either way the signal is withheld,
    anchor_lost is True and the outcome's returncode is None. After the single reap at the
    end the group is only ever OBSERVED (signal 0), never signalled destructively, and a
    member seen then surfaces only as survivors True; (3) every wait, drain and probe
    carries its own bound, so a pipe held open outside the killed group only delays the
    bounded drain and is reported through timed_out (returncode None is reserved for a
    leader that could not be reaped within the reap bound, or a lost anchor), never a stall
    of the whole self-test. When ready_path is given, the launcher first waits, bounded by
    _CHILD_TIMEOUT, for the child to publish a complete LF-terminated line there, and the
    TIMEOUT window starts only at that readiness; a handshake that never completes comes
    back as ready False and the group is killed the same anchored way. What the group kill
    bounds is the group's MEMBERS: group membership, not ancestry, is the boundary, so a
    descendant that moved itself into a new session, or into another process group with
    setpgid, is outside the group and outside this bound.
    Exception safety: the output buffers and every flag the cleanup reads exist BEFORE the
    launch (an allocation failure there launches nothing), and the try/finally starts the
    moment Popen returns. The finally runs each cleanup stage as its OWN guarded step, in
    order: the stdout close, the stderr close, the anchor check, the final destructive group
    signal and the one bounded reap; a stage that raises (any exception class) never skips
    a later stage. An exception from the lifecycle itself (the PRIMARY) is re-raised
    unchanged; the first cleanup-stage failure is kept and raised only when nothing else
    raised. Attempting a cleanup step cannot guarantee it succeeds: a close, signal or reap
    that fails is reported (raised, or set aside for the primary), never retried, so a
    failed signal can leave the group running and a failed reap leaves returncode None.
    PRECONDITIONS, relied on by every anchor check: SIGCHLD NOT IGNORED (ignored, the kernel
    reaps the leader itself and the check reports the anchor lost) and this process as the
    leader's ONLY reaper. The first is CHECKED once, before anything is created or
    launched: when signal.getsignal reports SIGCHLD as SIG_IGN (a parent's ignore survives
    exec), the launcher raises _SigchldIgnoredError and launches nothing. That check sees
    only what Python recorded: a disposition changed after it, or set outside Python (a
    C-level ignore or SA_NOCLDWAIT, which getsignal does not report), is not refused, and a
    leader the kernel reaps under it surfaces as a ChildProcessError from a wait or as a
    lost anchor. The second precondition is stated, not checked. The RESIDUAL they leave open:
    an outside reap landing after an anchor check and before the signal it guards is not
    excluded (no unsynchronized observation can exclude it); the reap that follows then
    finds the leader gone, so the outcome still reports anchor_lost True and returncode
    None, but that one signal was already sent."""
    import signal
    import time
    # the SIGCHLD precondition, checked before anything exists: ignored, the kernel would
    # reap each leader itself, so the launch is refused by name and nothing is launched
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        raise _SigchldIgnoredError(
            "refused before launch: SIGCHLD is ignored (SIG_IGN) in this process, possibly "
            "inherited across exec from a parent that ignores it; the kernel would reap each "
            "launched leader itself, so no anchor check could hold its process-group id and "
            "no exit status could be collected (run with SIGCHLD at SIG_DFL)")
    child_env = dict(env)
    child_env[_CHILD_DEPTH_ENV] = "1"
    # everything the cleanup reads exists BEFORE the launch: an allocation failure here
    # launches nothing, so the protected region can start the moment Popen returns
    out = bytearray()
    err = bytearray()
    ready = True
    timed_out = False
    anchor_lost = False
    returncode = None
    primary_raised = False
    cleanup_error = None
    proc = subprocess.Popen([sys.executable, "-I", "-B", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=child_env, start_new_session=True)
    try:
        if ready_path is not None:
            ready = _await_readiness(proc, ready_path)
        # the execution window starts only HERE, at readiness (or at launch when no
        # handshake was requested): the deadline is taken AFTER the readiness wait returns
        deadline = time.monotonic() + timeout
        timed_out = not _drain_pipes_unreaped(proc, deadline, out, err)
        if ready and not timed_out:
            # the pipes are at EOF; wait, bounded by the same deadline, for the leader's
            # exit, observed with WNOWAIT and never reaped here
            timed_out = not _await_exit_unreaped(proc.pid, deadline)
        if not ready or timed_out:
            # the FIRST destructive signal: the whole group, sent BEFORE the bounded
            # post-timeout drain below and only when the anchor check just before it finds
            # the leader still unreaped; a lost anchor withholds it, and the exit wait too
            # (that leader is no longer this process's child)
            if _leader_unreaped(proc):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                anchor_lost = True
            drain_deadline = time.monotonic() + _CHILD_DRAIN_TIMEOUT
            _drain_pipes_unreaped(proc, drain_deadline, out, err)
            if not anchor_lost:
                _await_exit_unreaped(proc.pid, drain_deadline)
    except BaseException:
        primary_raised = True  # the finally below still cleans up; the raise is UNCHANGED
        raise
    finally:
        # each cleanup stage is its OWN guarded step: a stage that raises (any exception
        # class) never skips a later one, and the first stage failure is kept until every
        # stage has run
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None and not pipe.closed:
                    pipe.close()  # EOF never came in bound, or the lifecycle above raised
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        anchored = False
        if not anchor_lost:
            try:
                anchored = _leader_unreaped(proc)  # the anchor check before the final signal
            except BaseException as exc:  # UNVERIFIABLE: never treated as held
                if cleanup_error is None:
                    cleanup_error = exc
        if anchored:
            try:
                # the FINAL destructive signal, sent while the unreaped (zombie, or
                # unkillable and live) leader still anchors the group id; harmless when
                # the group is already empty apart from that leader
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        else:
            anchor_lost = True  # lost or unverifiable: the final signal is withheld
        try:
            reaped, lost = _reap_leader(proc.pid, time.monotonic() + _CHILD_DRAIN_TIMEOUT)
            if lost:
                anchor_lost = True
            if proc.returncode is None and (reaped is not None or lost):
                # Popen's own bookkeeping, so it never waits on this pid again (its later
                # cleanup could otherwise reap a reused pid): after a lost anchor it is
                # closed the way CPython closes it on ECHILD, while the OUTCOME below
                # carries None, never that 0
                proc.returncode = reaped if reaped is not None else 0
            returncode = None if anchor_lost else reaped
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if cleanup_error is not None and not primary_raised:
            raise cleanup_error  # a cleanup failure surfaces only when nothing else raised
    # bounded post-reap OBSERVATION: after the reap the group id may be reused by an
    # unrelated group, so the probe uses signal 0 only and nothing is ever signalled
    # destructively here; a member still seen at the end surfaces as survivors True
    survivors = True
    for _ in range(100):
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            survivors = False
            break
        time.sleep(0.1)
    return _ChildOutcome(returncode, bytes(out), bytes(err), timed_out, survivors, ready,
                         anchor_lost)


def _exact_line_count(stdout_bytes, line):
    """Count LINE occurring as an exact LF-delimited line of STDOUT_BYTES. The split is on
    LF over captured BYTES: text-mode newline translation would turn a CR-separated prefix
    into a line of its own and let it count as exact, so a prefixed, suffixed, CR-prefixed
    or CR-suffixed spelling all count 0 here."""
    return stdout_bytes.split(b"\n").count(line.encode("utf-8"))


def _default_git_probe():
    """The real git probe: launches exactly list(_GIT_PROBE_ARGV), the same constant the
    simulated git-less arm matches on; the probe-argv vector asserts that identity."""
    return subprocess.run(list(_GIT_PROBE_ARGV), capture_output=True, text=True).returncode


class _SetupAllocationError(OSError):
    """An OSError raised by the entry scrub's temp-directory ALLOCATION, re-raised (chained) at
    the allocation call itself by _mark_setup_allocations. The public entry's handler catches
    ONLY this type, so the catch is bound to the allocation: any other OSError out of the setup
    propagates unchanged."""


def _mark_setup_allocations():
    """Type the setup's temp-directory allocation at the call: until the returned restore
    callable runs, an OSError raised by tempfile.mkdtemp is re-raised as _SetupAllocationError.
    The entry scrub's scratch HOME is the only temp-directory allocation in that setup, so this
    binds the public entry's handler to exactly the allocation call. The restore is idempotent
    and runs before any case, so an in-run allocation stays a plain OSError (guarded in-run, or
    propagating)."""
    real_mkdtemp = tempfile.mkdtemp

    def marked_mkdtemp(*args, **kwargs):
        try:
            return real_mkdtemp(*args, **kwargs)
        except OSError as exc:
            raise _SetupAllocationError(str(exc)) from exc

    def restore():
        if tempfile.mkdtemp is marked_mkdtemp:
            tempfile.mkdtemp = real_mkdtemp

    tempfile.mkdtemp = marked_mkdtemp
    return restore


def _not_run_vectors(failures):
    """Red-on-revert vectors for the skips that printed SELF-TEST PASS (PARTIAL) and exited 0,
    plus the narrowness of the public entry's setup handler.

    Scoped layer: a nested self-test run with git unavailable (the git probe raises OSError),
    one whose git probe returns NONZERO (its own arm, independent of the OSError arm, so
    either handler's removal is red on its own), and one with an unavailable temp directory
    (mkdtemp raises OSError; the git probe is ALSO injected to succeed, so a host where git
    is genuinely absent still reaches the mkdtemp branch and the nested run records ONLY the
    no-tempdir line), must each exit nonzero AND name the git-level cases on a NOT RUN line.
    A further arm re-runs the no-tempdir entry on a SIMULATED git-less host (the real probe
    launch is patched to raise), so the only-the-no-tempdir-line property is asserted on
    every host, with or without git. All enter self_test_main AFTER its entry scrub already
    succeeded.

    Entry-narrowness layer (in-process): the public entry catches ONLY the scrub's
    temp-directory ALLOCATION failure (typed at the allocation call by _mark_setup_allocations);
    an injected PermissionError out of the scrub that is NOT the allocation must PROPAGATE,
    never be reported as the setup NOT RUN. The probe vectors assert the default git probe
    launches exactly list(_GIT_PROBE_ARGV), that the simulated git-less arm refuses that same
    launch, and that self_test_main reaches _default_git_probe when no _git_probe is injected
    (argv-VALUE agreement and helper reachability; an EQUIVALENT literal on either side still
    agrees and is not distinguished); the no-vectors-pass-text vector asserts a clean nested
    no-vectors entry exits 0 printing only the distinct no-vectors PASS note.

    Fresh-process public-entry layer: the scoped injections cannot reach a failure of the entry
    scrub itself (scrub_git_environment allocates its scratch HOME with tempfile.mkdtemp before
    any case runs), so a child runs this tool as __main__ --self-test with tempfile.mkdtemp
    raising OSError process-wide, before the scrub, and must exit 1 with the entry's whole-run
    NOT RUN line on stdout, never a bare traceback. EVERY fresh Python vector-entry child
    runs through _run_bounded_child, the one bounded launcher (its own process group, the
    _CHILD_DEPTH_ENV marker whose presence-refusal at the top of this body bounds any child
    at depth 1, bytes capture, and a bound on every wait, drain and probe); the parent
    self-test's direct git launches (the probe, the gate's own _git calls driven
    in-process, and the fixture repositories' calls) and the launcher-timeout child's own
    plain descendant are outside that launcher. Every PUBLIC-ENTRY child environment
    carries _SUPPRESS_VECTORS_ENV (the launcher-group, launcher-timeout and
    launcher-cleanup probe children carry only the depth marker, and the
    launcher-group-replacement, launcher-readiness-window, launcher-anchor and
    launcher-sigchld-refusal vectors launch nothing: modelled doubles, or a spied Popen),
    so a vector-entry
    child never launches vector children of its own and never prints the
    full PASS (the marked gate records a named NOT RUN failure). The spy child proves ONLY
    that a marker-carrying child reaches its own named SELF-TEST FAIL without launching any
    subprocess (subprocess.run and subprocess.Popen are both replaced); its injections leave
    the git-level cases NOT RUN inside it, so it does not prove those cases complete."""
    if _child_depth_marker_present():
        # This entry already runs INSIDE a child the bounded launcher started (every
        # vector-entry child carries the marker), or the variable was inherited from the
        # caller's environment, possibly empty: PRESENCE, never truthiness, triggers this
        # refusal. Reaching the vectors under the marker means the marked-child gate or a
        # no-vectors nested call did not hold, or the inherited variable is the cause:
        # refuse with ONE named failure instead of launching children (a child never
        # launches vector children of its own), so any such chain ends at depth 1.
        _not_run(failures, "the red-on-revert NOT RUN and entry vectors",
                 "refused at child depth 1 ({} is present, with any value, even empty, "
                 "either set by the bounded launcher or inherited from the caller's "
                 "environment): a nested entry reached the vectors, so the marked-child "
                 "gate or a no-vectors nested call did not hold, or the variable was "
                 "inherited".format(_CHILD_DEPTH_ENV))
        return

    # Launcher depth-refusal vector (in-process): with _CHILD_DEPTH_ENV present (the empty
    # string, "0" and "1" alike: presence, never truthiness, triggers the refusal) and both
    # subprocess.run and subprocess.Popen replaced by spies, a nested _not_run_vectors call
    # must record exactly ONE failure, the named depth refusal, and launch NOTHING: red when
    # the depth refusal is removed, and red for the empty value when the refusal reverts to
    # truthiness (the spies turn any launch attempt, or anything else the unrefused vectors
    # do, into this vector's named failure, bounded in process; the _DEPTH_DRIVE_ACTIVE
    # latch keeps a reverted refusal LINEAR: a nested drive skips this loop, so each value
    # ends at its nested call's first spied launch, never an exponential chain).
    def _spy_launch(*_args, **_kwargs):
        raise RuntimeError("spy: a depth-marked nested vectors call tried to launch")

    prior_depth = os.environ.get(_CHILD_DEPTH_ENV)
    real_spied_run, real_spied_popen = subprocess.run, subprocess.Popen
    global _DEPTH_DRIVE_ACTIVE
    prior_drive = _DEPTH_DRIVE_ACTIVE
    _DEPTH_DRIVE_ACTIVE = True
    for depth_value in (() if prior_drive else ("", "0", "1")):
        os.environ[_CHILD_DEPTH_ENV] = depth_value
        subprocess.run, subprocess.Popen = _spy_launch, _spy_launch
        depth_failures = []
        depth_got = None
        try:
            _not_run_vectors(depth_failures)
        except Exception as exc:  # the spies raise on any launch attempt
            depth_got = "an escaped {}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.run, subprocess.Popen = real_spied_run, real_spied_popen
            if prior_depth is None:
                os.environ.pop(_CHILD_DEPTH_ENV, None)
            else:
                os.environ[_CHILD_DEPTH_ENV] = prior_depth
        if depth_got is None and (len(depth_failures) != 1
                                  or "refused at child depth 1" not in depth_failures[0]):
            depth_got = "{} failure(s): {!r}".format(len(depth_failures), depth_failures[:2])
        if depth_got is not None:
            failures.append("(vector child-depth-refusal value {!r}) a nested vectors call "
                            "with {} present, whatever its value, must record exactly one "
                            "named depth refusal and launch nothing (got {})".format(
                                depth_value, _CHILD_DEPTH_ENV, depth_got))
    _DEPTH_DRIVE_ACTIVE = prior_drive
    # The ABSENT side of the same boundary: this very call reached the vectors only under
    # an absent marker, and the predicate must agree now that the loop restored absence, so
    # a predicate that refused on absence too could never turn the full run green.
    if _child_depth_marker_present():
        failures.append("(vector child-depth-absent) with {} absent the depth predicate "
                        "must be False so a full run reaches these vectors (got "
                        "True)".format(_CHILD_DEPTH_ENV))

    # Launcher group-and-marker vector (fresh process): a trivial child run through the ONE
    # bounded launcher reports whether it leads its own process group and the depth-marker
    # value it inherited: red when start_new_session is dropped (the child then joins THIS
    # group and reports False) or when the launcher stops setting _CHILD_DEPTH_ENV.
    group_probe = ("import os, sys\n"
                   "print(os.getpgid(0) == os.getpid(), os.environ.get(sys.argv[1]))\n")
    outcome = _run_bounded_child(["-c", group_probe, _CHILD_DEPTH_ENV], os.environ)
    if (outcome.returncode != 0 or outcome.stdout != b"True 1\n"
            or outcome.timed_out or outcome.survivors):
        failures.append("(vector launcher-group) a child run through the bounded launcher "
                        "must lead its OWN process group and carry {}=1 (want stdout "
                        "b'True 1\\n'; got exit {}, stdout {!r}, timed out {}, survivors "
                        "{})".format(_CHILD_DEPTH_ENV, outcome.returncode, outcome.stdout[:80],
                                     outcome.timed_out, outcome.survivors))

    # Launcher group-replacement vector (in-process, modelled doubles): once the leader has
    # been REAPED the kernel may hand its process-group id to an UNRELATED new group, so a
    # post-reap probe that still "sees a member" may be seeing that replacement. The doubles
    # model exactly that schedule: the leader exits at once, readiness completes at the
    # first waitid observation, and every group probe afterwards keeps reporting a member
    # (the replacement). The waitid double HONOURS its flags (WNOWAIT observes without
    # reaping; a call WITHOUT WNOWAIT is a reap and is recorded exactly like a wait or a
    # communicate) and validates the target and the required flags, so the launcher must
    # perform EXACTLY ONE reap (its own final waitid without WNOWAIT), send its destructive
    # group signal only while the unreaped leader anchors the id, and NEVER signal
    # destructively after that reap (the replacement sighting may surface only as survivors
    # True): red when a post-reap cleanup kill is reintroduced, red when the final kill
    # moves after the reap, red when the reap reverts to Popen.wait, and red when os.WNOWAIT
    # is removed from _await_readiness, _await_exit_unreaped or the anchor check (the
    # un-flagged waitid then counts as a SECOND reap and the final kill lands post-reap).
    import signal
    import time
    gmod = globals()
    killpg_events = []
    reap_events = []
    model_errors = []
    ready_seen = {"complete": False}

    class _ModelSiginfo:
        """A modelled waitid result: only the two fields the launcher reads."""

        def __init__(self, si_code, si_status):
            self.si_code = si_code
            self.si_status = si_status

    class _ReplacedGroupProc:
        """A Popen double whose leader is reapable at once and whose numeric group id is
        then reused: the patched killpg below always sees a member. No pipes (stdout and
        stderr None): the drain has nothing to read and the schedule stays modelled."""
        pid = 4194305  # modelled only: killpg and waitid are both patched below
        stdout = None
        stderr = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def communicate(self, timeout=None):  # a reap, however the launcher drains
            self.returncode = 0
            reap_events.append("communicate")
            return b"", b""

        def wait(self, timeout=None):  # a reap through Popen
            self.returncode = 0
            reap_events.append("wait")
            return 0

    def _replaced_killpg(pgid, sig):
        if pgid != _ReplacedGroupProc.pid:
            model_errors.append("killpg target {!r} (want {!r})".format(
                pgid, _ReplacedGroupProc.pid))
        killpg_events.append((sig, bool(reap_events)))

    def _replaced_waitid(idtype, wpid, options):
        # the flags are honoured and validated: WNOWAIT observes without reaping; its
        # ABSENCE is a reap and is recorded; the first call also completes the modelled
        # readiness handshake, so _await_readiness exercises its own waitid here
        if idtype != os.P_PID or wpid != _ReplacedGroupProc.pid:
            model_errors.append("waitid target ({!r}, {!r}) (want (P_PID, {!r}))".format(
                idtype, wpid, _ReplacedGroupProc.pid))
        if not options & os.WEXITED:
            model_errors.append("waitid without WEXITED (options {!r})".format(options))
        if not options & os.WNOWAIT:
            reap_events.append("waitid")  # a reap: the flags say so
        ready_seen["complete"] = True
        return _ModelSiginfo(os.CLD_EXITED, 0)  # the modelled leader is already waitable

    def _replaced_ready_line(_path):
        # the modelled readiness file: complete only after the first waitid observation
        return ready_seen["complete"]

    def _no_sleep(*_args, **_kwargs):
        return None

    real_rg_popen, real_rg_killpg = subprocess.Popen, os.killpg
    real_rg_waitid, real_rg_sleep = os.waitid, time.sleep
    real_rg_ready_line = _ready_line_complete
    subprocess.Popen = _ReplacedGroupProc
    os.killpg = _replaced_killpg
    os.waitid = _replaced_waitid
    time.sleep = _no_sleep
    gmod["_ready_line_complete"] = _replaced_ready_line
    try:
        outcome = _run_bounded_child(["-c", "pass"], os.environ, timeout=5,
                                     ready_path="modelled: never opened")
    finally:
        subprocess.Popen, os.killpg = real_rg_popen, real_rg_killpg
        os.waitid, time.sleep = real_rg_waitid, real_rg_sleep
        gmod["_ready_line_complete"] = real_rg_ready_line
    post_reap_kills = [sig for sig, after_reap in killpg_events if sig != 0 and after_reap]
    anchored_kill = any(sig != 0 and not after_reap for sig, after_reap in killpg_events)
    if (post_reap_kills or not anchored_kill or model_errors or reap_events != ["waitid"]
            or outcome.returncode != 0 or not outcome.ready or not outcome.survivors):
        failures.append("(vector launcher-group-replacement) under a modelled reaped-leader"
                        "/replaced-group schedule the launcher must perform EXACTLY ONE "
                        "reap (its own final waitid), send its destructive group signal "
                        "while the unreaped leader anchors the id and never after the "
                        "reap, and aim every kill and waitid at the leader with the "
                        "required flags, where the replacement surfaces only as survivors "
                        "True (got post-reap kills {!r}, anchored kill {}, reaps {!r}, "
                        "model errors {!r}, exit {!r}, ready {}, survivors {})".format(
                            post_reap_kills, anchored_kill, reap_events, model_errors,
                            outcome.returncode, outcome.ready, outcome.survivors))

    # Launcher readiness-window vector (in-process, modelled doubles, virtual clock): the
    # execution window must start AT readiness, never at launch, last exactly the requested
    # timeout, and on timeout the FIRST destructive group kill must come BEFORE the bounded
    # post-timeout drain. The virtual clock advances only inside time.sleep, so the
    # schedule is deterministic: readiness completes at about 100.0 on that clock, far past
    # a 5-second window measured from launch, and the modelled leader exits only once the
    # group kill lands. The vector records the ACTUAL readiness completion (the first
    # complete-line observation) and the deadline the launcher hands its first drain, and
    # requires that deadline to be readiness plus the requested timeout and the first kill
    # to land at or after it and before it plus one polling increment of the model (the
    # 0.05-second poll of _await_exit_unreaped, the loop that observes the deadline here).
    # Red when the readiness gate is routed around (ready = True in its place), when the
    # deadline is taken before the readiness wait, for a zero-length or an overlong window,
    # and when the first timeout kill ALONE is removed (the first recorded kill then follows
    # the post-timeout drain). The window predicate itself is checked at each end, just
    # inside, exactly at and just outside, so neither bound can drift unseen.
    window_events = []
    window_clock = {"now": 0.0}
    window_killed = {"done": False}
    window_ready = {"at": None}
    window_ready_from = 100.0
    window_timeout = 5
    window_poll = 0.05

    def _window_bounds_ok(ready_at, drain_deadline, kill_at):
        # the requested window, from the ACTUAL readiness: the drain's deadline is
        # readiness plus the timeout, and the first kill lands at or after that deadline
        # and strictly before it plus one polling increment
        if ready_at is None or drain_deadline is None or kill_at is None:
            return False
        want = ready_at + window_timeout
        return abs(drain_deadline - want) <= 1e-9 and want <= kill_at < want + window_poll

    window_bound_misses = []
    for bound_label, bound_deadline, bound_kill, bound_want in (
            ("deadline just below", 104.999999, 105.0, False),
            ("deadline exactly at", 105.0, 105.0, True),
            ("deadline just above", 105.000001, 105.0, False),
            ("kill just below the deadline", 105.0, 104.999999, False),
            ("kill exactly at the deadline", 105.0, 105.0, True),
            ("kill just above the deadline", 105.0, 105.000001, True),
            ("kill just below one increment", 105.0, 105.049999, True),
            ("kill exactly one increment", 105.0, 105.0 + window_poll, False),
            ("kill just above one increment", 105.0, 105.050001, False)):
        if _window_bounds_ok(100.0, bound_deadline, bound_kill) is not bound_want:
            window_bound_misses.append(bound_label)
    if window_bound_misses:
        failures.append("(vector launcher-readiness-window-bounds) the window predicate "
                        "must accept a kill from readiness plus the timeout up to, not "
                        "including, one polling increment later, and only the deadline "
                        "readiness plus the timeout (wrong at: {})".format(
                            ", ".join(window_bound_misses)))

    def _window_monotonic():
        return window_clock["now"]

    def _window_sleep(duration):
        window_clock["now"] += duration

    class _WindowProc:
        """A Popen double that never exits on its own: unwaitable until the group kill
        lands, an unreaped zombie afterwards. No pipes: each drain returns at once and
        still marks its place in the recorded event order."""
        pid = 4194306  # modelled only: killpg and waitid are both patched below
        stdout = None
        stderr = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def communicate(self, timeout=None):
            self.returncode = -9
            return b"", b""

        def wait(self, timeout=None):
            self.returncode = -9
            return -9

    def _window_killpg(_pgid, sig):
        if sig == 0:
            if window_killed["done"]:
                raise ProcessLookupError  # the killed, reaped group is empty
            return None
        window_events.append(("kill", window_clock["now"], None))
        window_killed["done"] = True

    def _window_waitid(_idtype, _wpid, _options):
        if window_killed["done"]:
            return _ModelSiginfo(os.CLD_KILLED, signal.SIGKILL)
        return None

    def _window_ready_line(_path):
        complete = window_clock["now"] >= window_ready_from
        if complete and window_ready["at"] is None:
            window_ready["at"] = window_clock["now"]  # the ACTUAL readiness completion
        return complete

    real_w_drain = _drain_pipes_unreaped

    def _window_drain(proc, deadline, out, err):
        window_events.append(("drain", window_clock["now"], deadline))
        return real_w_drain(proc, deadline, out, err)

    real_w_popen, real_w_killpg = subprocess.Popen, os.killpg
    real_w_waitid, real_w_sleep = os.waitid, time.sleep
    real_w_monotonic, real_w_ready_line = time.monotonic, _ready_line_complete
    subprocess.Popen = _WindowProc
    os.killpg = _window_killpg
    os.waitid = _window_waitid
    time.sleep = _window_sleep
    time.monotonic = _window_monotonic
    gmod["_ready_line_complete"] = _window_ready_line
    gmod["_drain_pipes_unreaped"] = _window_drain
    try:
        outcome = _run_bounded_child(["-c", "pass"], os.environ, timeout=window_timeout,
                                     ready_path="modelled: never opened")
    finally:
        subprocess.Popen, os.killpg = real_w_popen, real_w_killpg
        os.waitid, time.sleep = real_w_waitid, real_w_sleep
        time.monotonic = real_w_monotonic
        gmod["_ready_line_complete"] = real_w_ready_line
        gmod["_drain_pipes_unreaped"] = real_w_drain
    kill_idx = [i for i, event in enumerate(window_events) if event[0] == "kill"]
    drain_idx = [i for i, event in enumerate(window_events) if event[0] == "drain"]
    first_kill_at = window_events[kill_idx[0]][1] if kill_idx else None
    first_drain_deadline = window_events[drain_idx[0]][2] if drain_idx else None
    if (not outcome.ready or not outcome.timed_out or outcome.returncode != -9
            or outcome.survivors or len(drain_idx) < 2 or not kill_idx
            or not _window_bounds_ok(window_ready["at"], first_drain_deadline, first_kill_at)
            or kill_idx[0] > drain_idx[1]):
        failures.append("(vector launcher-readiness-window) on the modelled virtual-clock "
                        "schedule the {}-second window must start AT the actual readiness "
                        "(recorded at {!r}): the first drain's deadline must be readiness "
                        "plus the timeout, the first destructive group kill must land at "
                        "or after it and before it plus {} and precede the post-timeout "
                        "drain, with the leader then reaped (got ready {}, timed out {}, "
                        "returncode {!r}, survivors {}, first drain deadline {!r}, first "
                        "kill at {!r}, events {!r})".format(
                            window_timeout, window_ready["at"], window_poll, outcome.ready,
                            outcome.timed_out, outcome.returncode, outcome.survivors,
                            first_drain_deadline, first_kill_at, window_events))

    # Launcher anchor vectors (in-process, modelled doubles): EVERY destructive group
    # signal waits on the anchor check, and a lost anchor never reads as an exit of 0. The
    # Popen double's wait models CPython's Popen.wait on a leader another path already
    # reaped (its ECHILD reads as 0), so a launcher that reaps through Popen.wait reports
    # 0 here. first-kill: the leader was ALREADY REAPED before the first timeout kill
    # (every waitid raises ChildProcessError, as it does once SIGCHLD is ignored and the
    # kernel reaped the leader, and the modelled drain reports a pipe still held at the
    # deadline): no destructive group signal may be sent at all. final-signal: the leader
    # is reaped by another path after its exit was observed and before the final anchor
    # check: the final signal must be withheld. outside-reap: the outside reap lands
    # BETWEEN the final anchor observation and the final signal (modelled: inside the
    # killpg double, before the signal is recorded); no unsynchronized observation can
    # exclude that window and this vector does not claim it closed, so the signal is still
    # sent (the disclosed residual, required here only to prove the schedule was reached),
    # and what it pins is that the reap which follows reports the loss. Each must come back
    # with anchor_lost True and returncode None, without raising. Red when the anchor check
    # before the first kill is removed, red when the final anchor check is replaced by
    # anchored = True, and red when the reap reverts to Popen.wait.
    anchor_state = {}
    anchor_kills = []
    anchor_reaps = []

    class _AnchorProc:
        """A Popen double with no pipes whose wait and communicate model CPython's ECHILD
        handling on an already reaped leader (both read as an exit of 0)."""
        pid = 4194307  # modelled only: killpg and waitid are both patched below
        stdout = None
        stderr = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def communicate(self, timeout=None):
            anchor_reaps.append("communicate")
            self.returncode = 0
            return b"", b""

        def wait(self, timeout=None):
            anchor_reaps.append("wait")
            self.returncode = 0
            return 0

    def _anchor_waitid(_idtype, _wpid, _options):
        reap_after = anchor_state["reap_after"]
        if reap_after is not None and anchor_state["observations"] >= reap_after:
            anchor_state["reaped"] = True  # another path reaps the leader now
        if anchor_state["reaped"]:
            raise ChildProcessError("modelled: the leader was reaped by another path")
        anchor_state["observations"] += 1
        return _ModelSiginfo(os.CLD_EXITED, 0)

    def _anchor_killpg(_pgid, sig):
        if sig == 0:
            raise ProcessLookupError  # the post-reap probe: the modelled group is empty
        if anchor_state["reap_at_signal"]:
            anchor_state["reaped"] = True  # the outside reap lands just before the signal
        anchor_kills.append((sig, anchor_state["observations"]))

    def _anchor_drain_held(_proc, _deadline, _out, _err):
        return False  # modelled: a pipe is still held open when the deadline passes

    real_a_drain = _drain_pipes_unreaped
    for anchor_label, reap_after, reap_at_signal, anchor_drain, want_timed_out, want_kills in (
            ("first-kill", 0, False, _anchor_drain_held, True, 0),
            ("final-signal", 1, False, real_a_drain, False, 0),
            ("outside-reap", None, True, real_a_drain, False, 1)):
        anchor_state.update(reap_after=reap_after, reap_at_signal=reap_at_signal,
                            reaped=False, observations=0)
        del anchor_kills[:]
        del anchor_reaps[:]
        anchor_got = None
        real_a_popen, real_a_killpg = subprocess.Popen, os.killpg
        real_a_waitid, real_a_sleep = os.waitid, time.sleep
        subprocess.Popen = _AnchorProc
        os.killpg = _anchor_killpg
        os.waitid = _anchor_waitid
        time.sleep = _no_sleep
        gmod["_drain_pipes_unreaped"] = anchor_drain
        try:
            outcome = _run_bounded_child(["-c", "pass"], os.environ, timeout=5)
        except Exception as exc:  # the vector reports it below as its own named failure
            anchor_got = "an escaped {}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen, os.killpg = real_a_popen, real_a_killpg
            os.waitid, time.sleep = real_a_waitid, real_a_sleep
            gmod["_drain_pipes_unreaped"] = real_a_drain
        if anchor_got is None and (
                len(anchor_kills) != want_kills or anchor_reaps or outcome.returncode is not None
                or not outcome.anchor_lost or outcome.timed_out is not want_timed_out):
            anchor_got = ("destructive kills {!r}, Popen reaps {!r}, returncode {!r}, "
                          "anchor lost {}, timed out {}".format(
                              anchor_kills, anchor_reaps, outcome.returncode,
                              outcome.anchor_lost, outcome.timed_out))
        if anchor_got is not None:
            failures.append("(vector launcher-anchor-{}) a leader another path reaped must "
                            "withhold every destructive group signal an anchor check can "
                            "still guard ({} expected on this schedule) and come back with "
                            "anchor_lost True and returncode None, never a collected 0, "
                            "without raising (got {})".format(anchor_label, want_kills,
                                                             anchor_got))

    # Launcher SIGCHLD-refusal vectors (in-process, a spied Popen): with SIGCHLD ignored the
    # kernel reaps every leader itself, so no anchor check could hold its group id; the
    # launcher must refuse with the named _SigchldIgnoredError BEFORE anything is launched.
    # subprocess.Popen is replaced by a spy that records every launch attempt and raises
    # its own error, so nothing real starts under any disposition here. ignored (SIG_IGN)
    # must be refused with no launch attempt; default (SIG_DFL) and python-handler (a
    # no-op handler installed through signal.signal, under which the kernel does not reap)
    # must reach exactly one launch attempt, the spy's own error propagating unchanged. The
    # caller's disposition is restored after each, whatever happened, and the vector
    # requires it restored. A handler installed outside Python (getsignal None) cannot be
    # put back through signal.signal, so these vectors then leave it alone and record a
    # NAMED NOT RUN. Red when the refusal is removed or moves after the launch, and red for
    # python-handler when the refusal widens to every disposition other than SIG_DFL.
    sigchld_prior = signal.getsignal(signal.SIGCHLD)
    if sigchld_prior is None:
        _not_run(failures, "the launcher-sigchld-refusal vectors",
                 "SIGCHLD has a handler installed outside Python, which signal.signal "
                 "cannot restore")
    else:
        sigchld_launches = []
        sigchld_spy_error = RuntimeError("spy: the launcher attempted a launch")

        def _sigchld_spy_popen(*_args, **_kwargs):
            sigchld_launches.append(signal.getsignal(signal.SIGCHLD))
            raise sigchld_spy_error

        def _sigchld_noop_handler(_signum, _frame):
            return None

        for sc_label, sc_disposition, sc_refused in (
                ("ignored", signal.SIG_IGN, True),
                ("default", signal.SIG_DFL, False),
                ("python-handler", _sigchld_noop_handler, False)):
            del sigchld_launches[:]
            sc_got = None
            real_sc_popen = subprocess.Popen
            subprocess.Popen = _sigchld_spy_popen
            try:
                signal.signal(signal.SIGCHLD, sc_disposition)
                _run_bounded_child(["-c", "pass"], os.environ, timeout=5)
                sc_got = "no exception"
            except _SigchldIgnoredError as exc:
                if not sc_refused:
                    sc_got = "refused: {}".format(exc)
            except Exception as exc:  # reported below as this vector's own named failure
                if sc_refused or exc is not sigchld_spy_error:
                    sc_got = "an escaped {}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.Popen = real_sc_popen
                signal.signal(signal.SIGCHLD, sigchld_prior)
            want_launches = 0 if sc_refused else 1
            if sc_got is None and len(sigchld_launches) != want_launches:
                sc_got = "{} launch attempt(s), want {}".format(len(sigchld_launches),
                                                              want_launches)
            if sc_got is None and signal.getsignal(signal.SIGCHLD) != sigchld_prior:
                sc_got = "the caller's SIGCHLD disposition was not restored"
            if sc_got is not None:
                failures.append("(vector launcher-sigchld-refusal {}) the launcher must "
                                "refuse with _SigchldIgnoredError and launch nothing exactly "
                                "when SIGCHLD is SIG_IGN, and reach its launch otherwise, "
                                "with the caller's disposition restored (got {})".format(
                                    sc_label, sc_got))

    # Launcher exception-cleanup vectors (fresh processes, injected failures): whatever
    # raises after Popen returns, the launcher's try/finally must run EVERY cleanup stage:
    # close both owned pipes with EXACTLY ONE close call each, attempt the final group
    # signal only after the anchor check, perform the one bounded reap, re-raise a PRIMARY
    # (lifecycle) exception unchanged, and raise a cleanup-stage failure only when nothing
    # else raised. Each vector launches a REAL child (except allocation, whose failure
    # must come BEFORE any launch) and asserts the expected exception OBJECT itself
    # propagated, the per-pipe close count, a returncode collected, the leader reaped and
    # the group empty. readiness, selector, pipe-read, pipe-close (the drain's EOF close)
    # and exit-observation inject the primary at one lifecycle step. anchor-check (a
    # cleanup waitid OSError: the anchor is unverifiable, so the final signal must be
    # withheld), final-signal (a PermissionError from the final killpg) and close-runtime
    # (a RuntimeError from the FINAL cleanup's stdout close while stderr is still open)
    # each fail one cleanup stage with a readiness failure pending, which must propagate
    # unchanged while every later stage still runs. close-alone fails the final cleanup's
    # stdout close with NOTHING else raised (the drain is modelled as finishing without
    # reaching EOF, over a real child that exits on its own): that close error must be
    # raised, but only after stderr is closed and the leader reaped. anchor-echild is a
    # REAL outside reap (the exit observation is replaced by a bounded waitpid that collects
    # the status 3) before the cleanup: no destructive signal, no exception, and the
    # outcome must carry anchor_lost True and returncode None, never the 0 CPython's
    # Popen.wait makes of ECHILD. Its window is _CHILD_TIMEOUT, the bound every vector-entry
    # child gets, not 5 seconds: the drain and the outside reap both run against the
    # launcher's one window, so a child slow to start under load would otherwise run it
    # out, and the first timeout kill that follows (recorded, never sent) would turn the
    # vector red although the launcher behaved. These vectors, like the anchor check they
    # exercise, rely on SIGCHLD not being ignored (the launcher's precondition; under
    # SIG_IGN it refuses by name, as the vectors above require). A child a red vector
    # leaves unreaped is killed and reaped by its pid afterwards. Red
    # when the cleanup try/finally is removed, when the buffers are allocated after the
    # launch, when a cleanup stage is no longer isolated (or catches only OSError), when a
    # final close is duplicated, when the kept cleanup failure is dropped or re-raised at
    # once, and when the reap reverts to Popen.wait.
    import selectors
    real_cl_selector = selectors.DefaultSelector
    real_cl_read = os.read
    real_cl_waitid = os.waitid
    real_cl_killpg = os.killpg
    real_cl_await_readiness = gmod["_await_readiness"]
    real_cl_await_exit = gmod["_await_exit_unreaped"]
    real_cl_drain = gmod["_drain_pipes_unreaped"]
    cl_hang_args = ["-c", "import time; time.sleep(60)"]

    def _cleanup_raiser(injected):
        def _raise(*_args, **_kwargs):
            raise injected
        return _raise

    def _drive_cleanup_vector(child_args, ready_path, injected, install, restore,
                              close_raises=None, launches=True, extra=None, window=5):
        """Launch a REAL child with INSTALL's injected failure in place and a WINDOW-second
        timeout, and return what the launcher got wrong (None when every guarantee held):
        INJECTED itself re-raised
        unchanged (None: a normal return is required), both pipes closed by EXACTLY ONE
        close call each, a returncode collected, the leader reaped and the group empty,
        plus EXTRA's own check of what SEEN recorded. With LAUNCHES False the failure must
        come before any launch. CLOSE_RAISES, when given, is raised by stdout's FIRST close
        call, after the raw pipe is closed. A child this vector launched that is still this
        process's UNREAPED child afterwards (a regressed launcher) is killed and reaped BY
        ITS PID here (unreaped, so the pid cannot have been reused): a red vector leaks no
        sleeping child."""
        seen = {"closes": {}}
        real_cl_popen = subprocess.Popen

        class _CountingPipe:
            """Delegates to the real pipe and counts EVERY close call; with RAISES set, the
            first close call closes the raw pipe, then raises RAISES."""

            def __init__(self, raw, name, raises):
                self.raw = raw
                self._name = name
                self._raises = raises
                seen["closes"][name] = 0

            @property
            def closed(self):
                return self.raw.closed

            def fileno(self):
                return self.raw.fileno()

            def close(self):
                seen["closes"][self._name] += 1
                self.raw.close()
                if self._raises is not None and seen["closes"][self._name] == 1:
                    raise self._raises

        class _RecordingPopen(real_cl_popen):
            def __init__(self, *p_args, **p_kwargs):
                super().__init__(*p_args, **p_kwargs)
                self.stdout = _CountingPipe(self.stdout, "stdout", close_raises)
                self.stderr = _CountingPipe(self.stderr, "stderr", None)
                seen["proc"] = self

        subprocess.Popen = _RecordingPopen
        install(seen)
        got = None
        try:
            seen["outcome"] = _run_bounded_child(child_args, os.environ, timeout=window,
                                                 ready_path=ready_path)
            if injected is not None:
                got = "no exception propagated"
        except BaseException as exc:
            if injected is None or exc is not injected:
                got = "a different exception {}: {}".format(type(exc).__name__, exc)
        finally:
            restore()
            subprocess.Popen = real_cl_popen
        misses = [] if got is None else [got]
        proc = seen.get("proc")
        if proc is None:
            if launches:
                misses.append("no child was launched")
            return "; ".join(misses) if misses else None
        if not launches:
            misses.append("a child was launched before the failing allocation")
        for pipe_name in ("stdout", "stderr"):
            if seen["closes"][pipe_name] != 1:
                misses.append("{} closed by {} close call(s), not exactly one".format(
                    pipe_name, seen["closes"][pipe_name]))
        if proc.returncode is None:
            misses.append("no returncode collected")
        for pipe_name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            if not pipe.closed:
                misses.append(pipe_name + " left open")
        leader_unreaped = False
        try:
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            leader_unreaped = True
            misses.append("leader still waitable: never reaped")
        except ChildProcessError:
            pass
        if leader_unreaped:
            # a regressed launcher left its child unreaped: kill it by its pid (still this
            # process's unreaped child, so the pid is not reused) and reap it
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=_CHILD_DRAIN_TIMEOUT)
            except subprocess.TimeoutExpired:
                misses.append("the leaked child could not be reaped")
        for pipe in (proc.stdout, proc.stderr):
            if not pipe.closed:
                pipe.raw.close()  # no descriptor outlives a red vector
        group_gone = False
        for _ in range(100):
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                group_gone = True
                break
            time.sleep(0.1)
        if not group_gone:
            misses.append("the group still has a member")
        if extra is not None:
            extra_miss = extra(seen)
            if extra_miss is not None:
                misses.append(extra_miss)
        return "; ".join(misses) if misses else None

    inj_ready = RuntimeError("injected: readiness failure")
    inj_selector = RuntimeError("injected: selector-creation failure")
    inj_read = OSError("injected: pipe-read failure")
    inj_close = OSError("injected: pipe-close failure")
    inj_exit = RuntimeError("injected: exit-observation failure")
    inj_alloc = MemoryError("injected: buffer-allocation failure")
    inj_anchor = OSError("injected: cleanup anchor-check failure")
    inj_signal = PermissionError("injected: final-signal failure")
    inj_close_rt = RuntimeError("injected: final-cleanup close failure")
    inj_close_alone = OSError("injected: final-cleanup close failure, nothing else raised")
    cl_kills = []

    def _install_ready(_seen):
        gmod["_await_readiness"] = _cleanup_raiser(inj_ready)

    def _restore_all():
        # every seam any cleanup vector patches, restored whichever one it installed
        gmod["_await_readiness"] = real_cl_await_readiness
        gmod["_await_exit_unreaped"] = real_cl_await_exit
        gmod["_drain_pipes_unreaped"] = real_cl_drain
        gmod.pop("bytearray", None)
        selectors.DefaultSelector = real_cl_selector
        os.read = real_cl_read
        os.waitid = real_cl_waitid
        os.killpg = real_cl_killpg

    def _install_selector(_seen):
        selectors.DefaultSelector = _cleanup_raiser(inj_selector)

    def _install_read(seen):
        def _read_child_pipe(fd, length):
            proc = seen.get("proc")
            open_fds = []
            for pipe in (proc.stdout, proc.stderr) if proc is not None else ():
                if pipe is not None and not pipe.closed:
                    open_fds.append(pipe.fileno())
            if fd in open_fds:
                raise inj_read
            return real_cl_read(fd, length)
        os.read = _read_child_pipe

    def _install_exit(_seen):
        gmod["_await_exit_unreaped"] = _cleanup_raiser(inj_exit)

    def _install_nothing(_seen):
        return None

    def _install_alloc(_seen):
        gmod["bytearray"] = _cleanup_raiser(inj_alloc)  # shadows the builtin in this module

    def _recording_killpg(pgid, sig):
        if sig != 0:
            cl_kills.append(sig)
        return real_cl_killpg(pgid, sig)

    def _install_anchor(seen):
        _install_ready(seen)
        del cl_kills[:]

        def _anchor_failing_waitid(idtype, wpid, options):
            if options & os.WNOWAIT:
                raise inj_anchor  # the cleanup's anchor observation cannot be made
            return real_cl_waitid(idtype, wpid, options)
        os.waitid = _anchor_failing_waitid
        os.killpg = _recording_killpg

    def _no_destructive_signal(_seen):
        if cl_kills:
            return "a destructive group signal {!r} was sent on an unverifiable anchor".format(
                cl_kills)
        return None

    def _install_signal(seen):
        _install_ready(seen)

        def _signal_failing_killpg(pgid, sig):
            if sig != 0:
                raise inj_signal
            return real_cl_killpg(pgid, sig)
        os.killpg = _signal_failing_killpg

    def _install_drain_short(_seen):
        def _drain_without_eof(_proc, _deadline, _out, _err):
            return True  # modelled: the drain finished with both pipes still open
        gmod["_drain_pipes_unreaped"] = _drain_without_eof

    def _install_echild(_seen):
        del cl_kills[:]

        def _outside_reap(pid, deadline):
            # the OUTSIDE reap: collect the leader's status before the launcher can
            while time.monotonic() < deadline:
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    return True
                time.sleep(0.05)
            return False

        def _dropping_killpg(pgid, sig):
            if sig != 0:
                cl_kills.append(sig)  # recorded, never sent: the anchor is lost here
                return None
            return real_cl_killpg(pgid, sig)
        gmod["_await_exit_unreaped"] = _outside_reap
        os.killpg = _dropping_killpg

    def _echild_reported(seen):
        outcome = seen.get("outcome")
        if outcome is None:
            return "no outcome returned"
        if outcome.returncode is not None or not outcome.anchor_lost or cl_kills:
            return ("returncode {!r} (want None), anchor lost {}, destructive signals "
                    "{!r}".format(outcome.returncode, outcome.anchor_lost, cl_kills))
        return None

    cl_pass_args = ["-c", "pass"]
    for cl_label, cl_args, cl_ready_path, cl_injected, cl_install, cl_close, cl_extra in (
            ("readiness", cl_hang_args, "injected: never opened", inj_ready,
             _install_ready, None, None),
            ("selector", cl_hang_args, None, inj_selector, _install_selector, None, None),
            ("pipe-read", ["-c", "print('x', flush=True)\nimport time\ntime.sleep(60)"],
             None, inj_read, _install_read, None, None),
            ("pipe-close", ["-c", "print('x')"], None, inj_close,
             _install_nothing, inj_close, None),
            ("exit-observation", cl_pass_args, None, inj_exit, _install_exit, None, None),
            ("allocation", cl_hang_args, None, inj_alloc, _install_alloc, None, None),
            ("anchor-check", cl_pass_args, "injected: never opened", inj_ready,
             _install_anchor, None, _no_destructive_signal),
            ("final-signal", cl_pass_args, "injected: never opened", inj_ready,
             _install_signal, None, None),
            ("close-runtime", cl_hang_args, "injected: never opened", inj_ready,
             _install_ready, inj_close_rt, None),
            ("close-alone", cl_pass_args, None, inj_close_alone,
             _install_drain_short, inj_close_alone, None),
            ("anchor-echild", ["-c", "import sys; sys.exit(3)"], None, None,
             _install_echild, None, _echild_reported)):
        cl_got = _drive_cleanup_vector(cl_args, cl_ready_path, cl_injected, cl_install,
                                       _restore_all, close_raises=cl_close,
                                       launches=cl_label != "allocation", extra=cl_extra,
                                       window=(_CHILD_TIMEOUT if cl_label == "anchor-echild"
                                               else 5))
        if cl_got is not None:
            failures.append("(vector launcher-cleanup-{}) the {} injection must leave the "
                            "launcher's cleanup guarantees intact: the expected exception "
                            "(or none) propagated unchanged, every cleanup stage run, each "
                            "pipe closed by exactly one close call, a returncode collected, "
                            "the leader reaped and the group empty (got {})".format(
                                cl_label, cl_label, cl_got))

    # Launcher timeout vector (fresh process): the child creates a live same-group
    # descendant, publishes a readiness line (descendant pid and group, its own pid and
    # group, and the descendant's liveness) and only then enters its hang; the launcher
    # starts the 5-second window only AT that readiness, so the window can never expire
    # before the descendant exists. The outcome must come back timed out with the leader
    # reaped (a returncode, never None) and the group empty, because the launcher SIGKILLs
    # the whole group while the unreaped leader still anchors it; the parsed readiness
    # fields must put the live descendant in the group the child leads. Red when
    # start_new_session is dropped, red when the descendant is no longer created, and red
    # when readiness never completes (missing readiness FAILS: ready False, never a
    # silently narrower exercise); the kill-before-drain order and the readiness-anchored
    # window are pinned by the launcher-readiness-window vector above, not here (dropping
    # only the first timeout kill leaves THIS vector green: the final anchored kill still
    # empties the group within the bounded drain).
    try:
        hang_dir = tempfile.mkdtemp(prefix="aiqt-monotonicity-selftest-hang-")
    except OSError as exc:
        _not_run(failures, "the launcher-timeout vector",
                 "no writable temp directory ({})".format(exc))
    else:
        try:
            import shutil
            hang_ready = os.path.join(hang_dir, "ready")
            hang = ("import os, subprocess, sys, time\n"
                    "desc = subprocess.Popen([sys.executable, '-I', '-c', "
                    "'import time; time.sleep(60)'])\n"
                    "line = '{} {} {} {} {}\\n'.format(desc.pid, os.getpgid(desc.pid), "
                    "os.getpid(), os.getpgid(0), desc.poll() is None)\n"
                    "with open(sys.argv[1] + '.tmp', 'w') as fh:\n"
                    "    fh.write(line)\n"
                    "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
                    "time.sleep(60)\n")
            outcome = _run_bounded_child(["-c", hang, hang_ready], os.environ, timeout=5,
                                         ready_path=hang_ready)
            fields = []
            try:
                with open(hang_ready, "r", encoding="utf-8") as fh:
                    fields = fh.read().split()
            except OSError:
                pass
            handshake_ok = (outcome.ready and len(fields) == 5 and fields[4] == "True"
                            and fields[1] == fields[3] and fields[2] == fields[3])
            if (not handshake_ok or not outcome.timed_out or outcome.returncode is None
                    or outcome.survivors):
                failures.append("(vector launcher-timeout) a child whose readiness line "
                                "puts a LIVE descendant in the group the child leads, and "
                                "which then outlives the wait bound, must be killed AS A "
                                "GROUP while the unreaped leader anchors it, leaving the "
                                "leader reaped and the group empty; missing or wrong "
                                "readiness fails (got ready {}, readiness fields {!r}, "
                                "timed out {}, returncode {!r}, survivors {}, stderr "
                                "{!r})".format(outcome.ready, fields, outcome.timed_out,
                                               outcome.returncode, outcome.survivors,
                                               outcome.stderr[:120]))
        finally:
            shutil.rmtree(hang_dir, ignore_errors=True)

    def _unavailable(*_args, **_kwargs):
        raise OSError("injected: unavailable")

    scoped = (
        ("git-absent", {"_git_probe": _unavailable},
         "NOT RUN: {}: git is unavailable (injected: unavailable)".format(_GIT_CASES)),
        # The probe's nonzero-RETURN-CODE handler is its own arm, independent of the OSError
        # arm above: each needle names its own cause, so neither NOT RUN line can hide the
        # other handler's removal.
        ("git-probe-nonzero", {"_git_probe": lambda: 1},
         "NOT RUN: {}: git is unavailable (the git probe exited nonzero)".format(_GIT_CASES)),
        ("no-tempdir", {"_git_probe": lambda: 0, "_mkdtemp": _unavailable},
         "NOT RUN: {}: no writable temp directory".format(_GIT_CASES)),
    )
    for label, kwargs, want in scoped:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = self_test_main(_vectors=False, **kwargs)
        if rc == 0 or want not in out.getvalue():
            failures.append("(NOT RUN vector {}) must exit nonzero AND record {!r}, never a PASS "
                            "(PARTIAL) exit 0 (got exit {})".format(label, want, rc))

    # The SAME no-tempdir entry under a simulated git-less host: were its successful-probe
    # injection ever dropped, the nested run would probe for real, fail, and record the
    # misleading extra git-unavailable line; this asserts the single-line outcome on every host.
    label, kwargs, want = scoped[2]  # the no-tempdir arm
    real_run = subprocess.run

    def _gitless_run(args, *more, **kw):
        if tuple(args[:len(_GIT_PROBE_ARGV)]) == _GIT_PROBE_ARGV:
            raise OSError("simulated: git absent")
        return real_run(args, *more, **kw)

    subprocess.run = _gitless_run
    out = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = self_test_main(_vectors=False, **kwargs)
    finally:
        subprocess.run = real_run
    text = out.getvalue()
    if rc == 0 or want not in text or "git is unavailable" in text:
        failures.append("(NOT RUN vector {}, git-less host) must record ONLY {!r} and never an "
                        "extra git-unavailable line, with or without a real git (got exit "
                        "{})".format(label, want, rc))

    # Arm-refusal: the simulated git-less arm against the REAL default probe. With the arm
    # installed, the probe's own launch is exactly what the arm must refuse, so the arm's
    # match cannot drift off _GIT_PROBE_ARGV (a drifted literal would fall through to a real
    # launch: a live return code where git exists, or an OSError without the simulated
    # message where it does not).
    subprocess.run = _gitless_run
    refused = False
    try:
        _default_git_probe()
    except OSError as exc:
        refused = "simulated: git absent" in str(exc)
    finally:
        subprocess.run = real_run
    if not refused:
        failures.append("(vector probe-argv arm) the simulated git-less arm must refuse the "
                        "default probe's own launch (its match is _GIT_PROBE_ARGV, the argv "
                        "the probe launches), never fall through to a real launch")

    # Entry narrowness: an OSError out of the scrub that is NOT the temp-directory allocation
    # must PROPAGATE out of the public entry (red when the entry catches plain OSError instead
    # of the typed allocation error).
    import _git_fixture_env as _fixture_env
    real_scrub = _fixture_env.scrub_git_environment

    def _denying_scrub():
        raise PermissionError("injected: non-allocation scrub failure")

    _fixture_env.scrub_git_environment = _denying_scrub
    propagated = False
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self_test_main(_vectors=False)
    except PermissionError as exc:
        propagated = "injected: non-allocation scrub failure" in str(exc)
    finally:
        _fixture_env.scrub_git_environment = real_scrub
    if not propagated:
        failures.append("(entry vector non-allocation scrub OSError) an OSError out of the "
                        "scrub that is not the temp-directory allocation must PROPAGATE out of "
                        "the public entry, never be reported as the setup NOT RUN")

    # Probe-argv agreement: the default probe must launch exactly list(_GIT_PROBE_ARGV), once.
    # With the arm-refusal vector above (the arm refuses the probe's own real launch) and the
    # probe-default-entry vector below (the entry reaches this helper), what is proven is
    # argv-VALUE agreement between probe, arm and entry plus helper reachability, NOT
    # dependence on the constant object: a side moved to an EQUIVALENT literal still agrees
    # and is not detected. The recording stub launches nothing, so this holds on hosts with
    # or without git.
    launched = []

    class _ProbeResult:
        returncode = 0

    def _recording_run(args, *_more, **_kw):
        launched.append(tuple(args))
        return _ProbeResult()

    real_probe_run = subprocess.run
    subprocess.run = _recording_run
    try:
        _default_git_probe()
    finally:
        subprocess.run = real_probe_run
    if launched != [tuple(_GIT_PROBE_ARGV)]:
        failures.append("(vector probe-argv) the default git probe must launch exactly "
                        "list(_GIT_PROBE_ARGV) once, the argv the simulated git-less arm "
                        "matches on (got {!r})".format(launched))

    # Probe-default-entry: without an injected _git_probe, self_test_main must reach
    # _default_git_probe itself, exactly once (red when the entry launches a literal probe of
    # its own, leaving the tested helper disconnected); the recorder raises OSError, so the
    # nested run records the git-unavailable NOT RUN and launches nothing.
    _mod = sys.modules[__name__]
    real_default_probe = _mod._default_git_probe
    probe_reached = []

    def _recording_probe():
        probe_reached.append(True)
        raise OSError("injected: default probe reached")

    _mod._default_git_probe = _recording_probe
    out = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = self_test_main(_vectors=False)
    finally:
        _mod._default_git_probe = real_default_probe
    reach_want = "NOT RUN: {}: git is unavailable (injected: default probe reached)".format(
        _GIT_CASES)
    if probe_reached != [True] or rc == 0 or reach_want not in out.getvalue():
        failures.append("(vector probe-default-entry) self_test_main without an injected "
                        "_git_probe must reach _default_git_probe exactly once and record "
                        "the git-unavailable NOT RUN (got {} probe call(s), exit {})".format(
                            len(probe_reached), rc))

    # No-vectors PASS text: a clean nested no-vectors entry must complete with exit 0 and
    # print the DISTINCT no-vectors PASS line, never the full-run "SELF-TEST PASS:" text, so
    # the full PASS line is only ever printed by a run whose vectors ran.
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = self_test_main(_vectors=False)
    text = out.getvalue()
    if rc != 0 or _NO_VECTORS_PASS_NOTE + ":" not in text or "SELF-TEST PASS:" in text:
        failures.append("(vector no-vectors-pass-text) a clean nested no-vectors entry must "
                        "exit 0 and print the distinct no-vectors PASS line {!r}, never the "
                        "full-run SELF-TEST PASS line (got exit {})".format(
                            _NO_VECTORS_PASS_NOTE + ":", rc))

    tool = str(Path(__file__).resolve())
    entry_env = dict(os.environ)
    entry_env[_SUPPRESS_VECTORS_ENV] = "1"  # a child never runs vectors of its own
    inject = ("import runpy, sys, tempfile\n"
              "def _unavailable(*_args, **_kwargs):\n"
              "    raise OSError('injected: unavailable')\n"
              "tempfile.mkdtemp = _unavailable\n"
              "tool = sys.argv[1]\n"
              "sys.argv[:] = [tool, '--self-test']\n"
              "runpy.run_path(tool, run_name='__main__')\n")
    want = "NOT RUN: {}: fixture setup failed: no writable temp directory".format(_EVERY_CASE)
    outcome = _run_bounded_child(["-c", inject, tool], entry_env)
    if (outcome.returncode != 1 or want.encode("utf-8") not in outcome.stdout
            or outcome.timed_out or outcome.survivors):
        failures.append("(NOT RUN vector public-entry no-tempdir) must exit 1 AND record {!r} "
                        "through the real --self-test entry, never a bare traceback, a hang "
                        "or a surviving group member (got exit {}, timed out {}, survivors "
                        "{})".format(want, outcome.returncode, outcome.timed_out,
                                     outcome.survivors))

    # Inherited-marker guard: a FULL public-entry run that inherits _SUPPRESS_VECTORS_ENV (the
    # scrub keeps it: it is not GIT_-prefixed) must exit 1 and print the named suppression
    # NOT RUN line as an exact LF-delimited BYTES line exactly once (a prefixed, suffixed or
    # CR-separated-prefix line fails: the launcher captures bytes, so newline translation can
    # never fold a CR-separated prefix away), with no PASS text of either form, never the
    # unchanged PASS over silently skipped vectors; the bounded launcher turns a hang into
    # this vector's named failure within the launcher's group-and-timeout bounds, never a
    # stall of the whole self-test past those bounds.
    marked_expect = []
    _not_run(marked_expect, "the red-on-revert NOT RUN and entry vectors",
             "suppressed by {} (a vector child, never a full run)".format(_SUPPRESS_VECTORS_ENV))
    suppressed_line = "  - " + marked_expect[0]
    outcome = _run_bounded_child([tool, "--self-test"], entry_env)
    exact_lines = _exact_line_count(outcome.stdout, suppressed_line)
    marked_ok = (not outcome.timed_out and not outcome.survivors and outcome.returncode == 1
                 and exact_lines == 1 and b"SELF-TEST PASS" not in outcome.stdout)
    if outcome.timed_out:
        got = ("a timeout: the marked child hung instead of failing named (the group was "
               "killed before the leader was reaped; survivors {})".format(outcome.survivors))
    else:
        got = "exit {} (exact-line count {}, PASS text printed: {}, survivors {})".format(
            outcome.returncode, exact_lines, b"SELF-TEST PASS" in outcome.stdout,
            outcome.survivors)
    if not marked_ok:
        failures.append("(vector inherited-marker) a full public-entry run inheriting {} must "
                        "exit 1 AND print the exact LF-delimited line {!r} exactly once with "
                        "no PASS text, never the unchanged PASS over silently skipped vectors "
                        "(got {})".format(_SUPPRESS_VECTORS_ENV, suppressed_line, got))

    # Exact-line predicate vector: the count is over captured BYTES split on LF, so a
    # CR-separated prefix (which text-mode newline translation would split into a line of
    # its own) never counts as the exact line; red when the count reverts to text-mode
    # splitting or translates newlines before splitting.
    line_bytes = suppressed_line.encode("utf-8")
    for case_label, data, want_count in (
            ("exact", line_bytes + b"\n", 1),
            ("prefixed", b"prefix " + line_bytes + b"\n", 0),
            ("CR-prefixed", b"prefix\r" + line_bytes + b"\n", 0),
            ("suffixed", line_bytes + b" suffix\n", 0),
            ("CR-suffixed", line_bytes + b"\r\n", 0),
            ("duplicated", line_bytes + b"\n" + line_bytes + b"\n", 2)):
        got_count = _exact_line_count(data, suppressed_line)
        if got_count != want_count:
            failures.append("(vector marker-line-bytes {}) _exact_line_count must count the "
                            "suppression line as an exact LF-delimited BYTES line (want {}, "
                            "got {})".format(case_label, want_count, got_count))

    # _SUPPRESS_VECTORS_ENV guard: the spy child proves ONLY that a marker-carrying child
    # reaches its own named SELF-TEST FAIL without launching any subprocess (subprocess.run
    # AND subprocess.Popen are replaced); its denied probe and mkdtemp leave the git-level
    # cases NOT RUN inside it, so it does not prove those cases complete.
    spy = ("import importlib.util, subprocess, sys\n"
           "spec = importlib.util.spec_from_file_location("
           "'aiqt_monotonicity_vector_child', sys.argv[1])\n"
           "mod = importlib.util.module_from_spec(spec)\n"
           "sys.modules[spec.name] = mod\n"
           "spec.loader.exec_module(mod)\n"
           "def _spy(*_args, **_kwargs):\n"
           "    print('VECTOR-CHILD-SPAWNED', file=sys.stderr)\n"
           "    raise RuntimeError('vector child spawned despite the suppress marker')\n"
           "subprocess.run = _spy\n"
           "subprocess.Popen = _spy\n"
           "def _deny(*_args, **_kwargs):\n"
           "    raise OSError('injected: unavailable')\n"
           "sys.exit(mod.self_test_main(_git_probe=_deny, _mkdtemp=_deny))\n")
    outcome = _run_bounded_child(["-c", spy, tool], entry_env)
    spawned = (b"vector child spawned" in outcome.stderr
               or b"VECTOR-CHILD-SPAWNED" in outcome.stderr)
    if (outcome.returncode != 1 or b"SELF-TEST FAIL" not in outcome.stdout or spawned
            or outcome.timed_out or outcome.survivors):
        failures.append("(vector suppress-marker) a child carrying {} must run its cases to its "
                        "own named SELF-TEST FAIL without spawning vector children (got exit {}, "
                        "spawned: {}, timed out {}, survivors {})".format(
                            _SUPPRESS_VECTORS_ENV, outcome.returncode, spawned,
                            outcome.timed_out, outcome.survivors))


def self_test_main(_git_probe=None, _mkdtemp=None, _vectors=True):
    # Hermetic git fixtures (test-hermeticity): every fixture git call below inherits
    # os.environ, where an inherited GIT_INDEX_FILE / GIT_DIR (git exports these to hook
    # children) would redirect the fixture's init/add/commit into the CALLER's repository.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import scrub_git_environment
    failures = []
    restore_mkdtemp = _mark_setup_allocations()
    try:
        # The scrub allocates its scratch HOME with tempfile.mkdtemp BEFORE any case runs, so a
        # host with no writable temp directory fails here first: report ONE named NOT RUN
        # failure covering the whole run (exit 1; nothing ran), never a bare traceback. Only the
        # ALLOCATION failure is caught, typed at the allocation call itself
        # (_mark_setup_allocations); any other exception, including any other OSError out of the
        # scrub, still propagates.
        scrub_git_environment()
    except _SetupAllocationError as exc:
        _not_run(failures, _EVERY_CASE,
                 "fixture setup failed: no writable temp directory ({})".format(exc))
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    finally:
        restore_mkdtemp()

    # F-TOML-BARE-VALUEERROR-CLASS: a parser overflow makes tomllib raise RecursionError (a RuntimeError, not
    # a ValueError); the baseline parse must still fail closed as GateError carrying the overflow.
    # The overflow is INJECTED (tomllib.loads and tomllib.load raise RecursionError on a marked, otherwise valid
    # input) rather than provoked by a deeply nested body: the depth at which tomllib overflows is an
    # interpreter limit, so a fixed body overflows under one recursion limit and parses (or trips an unrelated
    # refusal) under another.
    real_loads, real_load = tomllib.loads, tomllib.load

    def overflowing_loads(text, **kwargs):
        if "injected-overflow" in text:
            raise RecursionError("injected parser overflow")
        return real_loads(text, **kwargs)

    tomllib.loads = overflowing_loads
    tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
    try:
        _parse_base_releases("deep = 1  # injected-overflow\n")
        failures.append("a baseline changelog.toml parser overflow must fail closed (GateError)")
    except GateError as exc:
        if "injected parser overflow" not in str(exc):
            failures.append("a baseline parser overflow was refused without its finding ({})".format(exc))
    except RecursionError:
        failures.append("a baseline changelog.toml parser overflow let a bare RecursionError escape")
    finally:
        tomllib.loads, tomllib.load = real_loads, real_load

    # M1 (prefix identity): (base, head, expect_a_finding).
    prefix_cases = [
        (["1.0.0"], ["1.0.0"], False),                          # identical
        (["1.0.0"], ["1.0.0", "1.1.0"], False),                 # append one
        (["1.0.0"], ["1.0.0", "1.1.0", "1.2.0"], False),        # append two
        (["1.0.0", "1.1.0"], ["1.0.0", "0.9.0"], True),         # tail rewrite to a lower version
        (["1.0.0", "1.1.0"], ["1.0.0", "1.2.0"], True),         # tail rewrite to a higher version
        (["1.0.0", "1.1.0"], ["1.0.0"], True),                  # removal
        (["1.0.0", "1.1.0"], ["1.0.0", "1.0.5", "1.1.0"], True),  # insertion before an existing entry
        (["1.0.0", "1.1.0"], ["1.1.0", "1.0.0"], True),         # reorder
        ([], ["1.0.0"], False),                                 # empty base
    ]
    for base, head, expect in prefix_cases:
        got = bool(check_prefix(base, head))
        if got != expect:
            failures.append("check_prefix({}, {}) finding={}; expected {}".format(base, head, got, expect))

    # M2 (no decrease): (base, head, expect_a_finding).
    nodecrease_cases = [
        (["1.0.0", "1.1.0"], ["1.0.0", "0.9.0"], True),   # decrease
        (["1.0.0"], ["1.0.0", "1.1.0"], False),           # increase
        (["1.0.0"], ["1.0.0"], False),                    # equal
        ([], ["1.0.0"], False),                           # empty base
    ]
    for base, head, expect in nodecrease_cases:
        got = bool(check_no_decrease(base, head))
        if got != expect:
            failures.append("check_no_decrease({}, {}) finding={}; expected {}".format(
                base, head, got, expect))

    # Tag logic (pure): name match/mismatch, ceiling equal/above.
    if check_tag_names([("1.0.0", "v1.0.0")]):
        failures.append("check_tag_names expected no finding for a matching tag")
    if not check_tag_names([("1.0.0", "v1.0.1")]):
        failures.append("check_tag_names expected a finding for a tag/version mismatch")
    if check_tag_ceiling([("1.0.0", "v1.0.0")], "1.0.0"):
        failures.append("check_tag_ceiling expected no finding when the tag equals the head latest")
    if not check_tag_ceiling([("1.0.0", "v2.0.0")], "1.0.0"):
        failures.append("check_tag_ceiling expected a finding when a tag is above the head latest")

    # _parse fullmatch regression (Step 4e): the shipped SemVer parser this gate imports must reject a
    # trailing newline (the `$`-anchored match() accepted "1.0.0\n"; fullmatch closes it). A clean
    # version still parses.
    if _parse("1.0.0\n") is not None:
        failures.append("_parse should reject a trailing-newline version (fullmatch hardening, 4e)")
    if _parse("1.2.3") != (1, 2, 3):
        failures.append("_parse should still accept a clean bare SemVer")

    # Full-row prefix identity (_prefix_findings), the shared logic of layer_c/layer_d (append-only):
    # (append passes, edit-a-prior-field fails, delete fails, empty base passes, identical passes).
    r1, r2 = {"version": "1.0.0", "commit_sha": "a"}, {"version": "1.1.0", "commit_sha": "b"}
    if _prefix_findings([r1], [r1, r2], "x"):
        failures.append("_prefix_findings: appending a row expected no finding")
    if not _prefix_findings([r1], [{"version": "1.0.0", "commit_sha": "EDITED"}], "x"):
        failures.append("_prefix_findings: editing a prior row FIELD expected a finding")
    if not _prefix_findings([r1, r2], [r1], "x"):
        failures.append("_prefix_findings: deleting a row expected a finding")
    if _prefix_findings([], [r1], "x"):
        failures.append("_prefix_findings: an empty base is a valid prefix")
    if _prefix_findings([r1, r2], [r1, r2], "x"):
        failures.append("_prefix_findings: an identical row list expected no finding")

    # Genesis predicate (_genesis_from_signals): file-level, binary, read at BASE only. (tags, present,
    # rows, expect). An absent ledger is NOT genesis; a tag or a ledger row means shipped.
    genesis_cases = [
        ([None], True, [], True),                        # one draft, header-only ledger
        ([None, None], True, [], True),                  # multiple drafts
        (["v1.0.0"], True, [], False),                   # tagged => shipped
        ([None], True, [{"version": "1.0.0"}], False),   # a ledger row => shipped
        ([None], False, [], False),                      # absent ledger => NOT genesis
        ([], True, [], True),                            # no releases, header-only ledger
    ]
    for tags, present, rows, expect in genesis_cases:
        got = _genesis_from_signals(tags, present, rows)
        if got is not expect:
            failures.append("_genesis_from_signals({}, {}, {}) = {}; expected {}".format(
                tags, present, rows, got, expect))

    # Tag-preservation (check_tag_preservation): a shipped release's tag must not be dropped or changed
    # while its version is unchanged; a changed version is check_prefix's finding, not this one.
    if check_tag_preservation([{"version": "1.0.0", "tag": "v1.0.0"}],
                              [{"version": "1.0.0", "tag": "v1.0.0"}]):
        failures.append("check_tag_preservation: an unchanged tagged release expected no finding")
    if not check_tag_preservation([{"version": "1.0.0", "tag": "v1.0.0"}], [{"version": "1.0.0"}]):
        failures.append("check_tag_preservation: dropping a base tag expected a finding")
    if check_tag_preservation([{"version": "1.0.0"}], [{"version": "1.0.0"}]):
        failures.append("check_tag_preservation: no base tag expected no finding")
    if check_tag_preservation([{"version": "1.0.0", "tag": "v1.0.0"}], [{"version": "1.0.5"}]):
        failures.append("check_tag_preservation: a changed version is check_prefix's finding, not this one")

    # Git-level cases: real repositories in a private tempdir. A NAMED NOT RUN failure where unavailable.
    import builtins
    import shutil
    import tempfile

    git_ok = True
    git_why = "the git probe exited nonzero"
    try:
        probe_rc = _git_probe() if _git_probe is not None else _default_git_probe()
        if probe_rc != 0:
            git_ok = False
    except OSError as exc:
        git_ok = False
        git_why = str(exc)

    base_tmp = None
    if not git_ok:
        _not_run(failures, _GIT_CASES, "git is unavailable ({})".format(git_why))
    else:
        try:
            base_tmp = Path((_mkdtemp or tempfile.mkdtemp)(prefix="aiqt-monotonicity-selftest-"))
        except OSError as exc:
            _not_run(failures, _GIT_CASES, "no writable temp directory ({})".format(exc))

    if base_tmp is not None:
        def _init(path):
            path.mkdir(parents=True, exist_ok=True)
            for args in (["init", "-q"], ["config", "user.name", "AIQT Self-Test"],
                         ["config", "user.email", "selftest@example.invalid"]):
                subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)

        def _write(path, text):
            (path / "changelog.toml").write_text(text, encoding="utf-8")

        def _commit(path, msg):
            subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", msg],
                           check=True, capture_output=True, text=True)

        def _root(path):
            # A parent commit, so HEAD is not itself the root commit; the root-commit N/A path is exercised
            # separately below rather than masking the real comparison in every fixture.
            (path / "README.md").write_text("seed\n", encoding="utf-8")
            _commit(path, "initial")

        def _write_ledger(path, body=""):
            # A header-only .aiqt/core/releases.toml (zero [[release]] rows) is the genesis ledger signal;
            # `body` appends [[release]] rows for the non-genesis (shipped) fixtures.
            core = path / ".aiqt" / "core"
            core.mkdir(parents=True, exist_ok=True)
            (core / "releases.toml").write_text("format-version = 1\n" + body, encoding="utf-8")

        try:
            # (1) append-in-working-tree passes; (2) a tail rewrite fails; (4) a garbage base is exit 2.
            r1 = base_tmp / "append"
            _init(r1)
            _root(r1)
            _write(r1, _changelog_text(["1.0.0"]))
            _commit(r1, "seed 1.0.0")
            _write(r1, _changelog_text(["1.0.0", "1.1.0"]))
            if _run_quiet(r1, "HEAD") != 0:
                failures.append("git case: appending 1.1.0 with --base HEAD expected exit 0")
            _write(r1, _changelog_text(["0.9.0"]))
            if _run_quiet(r1, "HEAD") != 1:
                failures.append("git case: rewriting 1.0.0 to 0.9.0 with --base HEAD expected exit 1")
            if _run_quiet(r1, "no-such-ref-xyz") != 2:
                failures.append("git case: a garbage --base expected fail-closed exit 2")

            # (3) a base commit lacking changelog.toml yields the printed NOT APPLICABLE (exit 0). The
            # root commit here has no changelog; changelog arrives at HEAD, so --base HEAD~1 is "absent".
            r2 = base_tmp / "introduced"
            _init(r2)
            _root(r2)
            _write(r2, _changelog_text(["1.0.0"]))
            _commit(r2, "introduce changelog")
            if _run_quiet(r2, "HEAD~1") != 0:
                failures.append("git case: changelog absent at base expected NOT APPLICABLE exit 0")

            # (3b) a true root-commit HEAD with NO base yields the printed NOT APPLICABLE (exit 0); the
            # same root HEAD with an explicit but unresolvable --base fails closed (exit 2), because an
            # explicit base is resolved even on a root and NOT APPLICABLE is reserved for the no-base case.
            r2b = base_tmp / "rootcommit"
            _init(r2b)
            _write(r2b, _changelog_text(["1.0.0"]))
            _commit(r2b, "sole commit")
            if _run_quiet(r2b, None) != 0:
                failures.append("git case: a root-commit HEAD with no base expected NOT APPLICABLE exit 0")
            if _run_quiet(r2b, "no-such-ref-xyz") != 2:
                failures.append("git case: a root-commit HEAD with an unresolvable --base expected exit 2")

            # (5) a recorded tag with no tag object is exit 2; (6) creating the tag makes it pass.
            r3 = base_tmp / "tagged"
            _init(r3)
            _root(r3)
            _write(r3, _changelog_text(["1.0.0"]))
            _commit(r3, "seed 1.0.0 untagged")
            _write(r3, _changelog_text(["1.0.0"], tag_on={"1.0.0": "v1.0.0"}))
            if _run_quiet(r3, "HEAD") != 2:
                failures.append("git case: a recorded tag with no tag object expected fail-closed exit 2")
            subprocess.run(["git", "-C", str(r3), "tag", "v1.0.0", "HEAD"],
                           check=True, capture_output=True, text=True)
            if _run_quiet(r3, "HEAD") != 0:
                failures.append("git case: creating the recorded tag expected exit 0")

            # (7) a broken-parent history: HEAD names a parent whose object is absent (as a shallow clone
            # leaves it). The old check read any failing HEAD^ as a root and passed NOT APPLICABLE; the fix
            # reads HEAD's own object, sees the parent, and fails closed (exit 2) when it cannot resolve.
            r4 = base_tmp / "brokenparent"
            _init(r4)
            _write(r4, _changelog_text(["1.0.0"]))
            _commit(r4, "seed 1.0.0")
            _write(r4, _changelog_text(["1.0.0", "1.1.0"]))
            _commit(r4, "append 1.1.0")
            parent_sha = subprocess.run(["git", "-C", str(r4), "rev-parse", "HEAD^"],
                                        check=True, capture_output=True, text=True).stdout.strip()
            (r4 / ".git" / "objects" / parent_sha[:2] / parent_sha[2:]).unlink()
            if _run_quiet(r4, None) != 2:
                failures.append("git case: a broken-parent history (unresolvable HEAD^) expected "
                                "fail-closed exit 2")

            # (8) layer_c / layer_d append-only over the release-order and id-history registers. The base
            # commit carries a one-row releases.toml and a one-row id-history.toml; the working tree
            # appends a row to each (exit 0), then edits a prior releases row field (exit 1), then deletes
            # the releases record (NOT APPLICABLE, so exit 0 again).
            r5 = base_tmp / "registers"
            _init(r5)
            _root(r5)
            _write(r5, _changelog_text(["1.0.0", "1.1.0"]))
            core = r5 / ".aiqt" / "core"
            core.mkdir(parents=True, exist_ok=True)
            rel_base = 'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ncommit_sha = "aaa"\n'
            reg_base = '[[born]]\nid = "calpha"\nborn-release = "1.0.0"\n'
            (core / "releases.toml").write_text(rel_base, encoding="utf-8")
            (core / "id-history.toml").write_text(reg_base, encoding="utf-8")
            _commit(r5, "base registers: one release row, one born row")
            # (8a) append a release row and a born row -> a valid prefix extension, exit 0.
            (core / "releases.toml").write_text(
                rel_base + '\n[[release]]\nversion = "1.1.0"\ncommit_sha = "bbb"\n', encoding="utf-8")
            (core / "id-history.toml").write_text(
                reg_base + '\n[[born]]\nid = "cbeta1"\nborn-release = "1.1.0"\n', encoding="utf-8")
            if _run_quiet(r5, "HEAD") != 0:
                failures.append("git case: appending a release row and a born row expected exit 0")
            # (8b) edit a PRIOR release row's field -> append-only violation, exit 1.
            (core / "releases.toml").write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ncommit_sha = "EDITED"\n',
                encoding="utf-8")
            (core / "id-history.toml").write_text(reg_base, encoding="utf-8")
            if _run_quiet(r5, "HEAD") != 1:
                failures.append("git case: editing a prior release row field expected exit 1")
            # (8c) delete the releases record at HEAD -> layer_c NOT APPLICABLE; with id-history restored
            # and changelog intact the run is otherwise clean, exit 0.
            (core / "releases.toml").unlink()
            (core / "id-history.toml").write_text(reg_base, encoding="utf-8")
            if _run_quiet(r5, "HEAD") != 0:
                failures.append("git case: a releases record absent at HEAD expected NOT APPLICABLE "
                                "(exit 0)")

            # G1 genesis relabel passes: base changelog [1.0.0] untagged + header-only ledger; the
            # working tree relabels it to [1.0.5]. Without the genesis exemption check_prefix -> exit 1.
            g1 = base_tmp / "genesis-relabel"
            _init(g1)
            _root(g1)
            _write(g1, _changelog_text(["1.0.0"]))
            _write_ledger(g1)
            _commit(g1, "genesis: 1.0.0 draft, header-only ledger")
            _write(g1, _changelog_text(["1.0.5"]))
            if _run_quiet(g1, "HEAD") != 0:
                failures.append("git case G1: genesis relabel 1.0.0 -> 1.0.5 expected exit 0")

            # G2 genesis draft consolidation that lowers the latest version passes (M2 suspended in
            # genesis): base [1.0.0, 1.1.0] untagged + header-only ledger; head consolidates to [1.0.5].
            g2 = base_tmp / "genesis-consolidate"
            _init(g2)
            _root(g2)
            _write(g2, _changelog_text(["1.0.0", "1.1.0"]))
            _write_ledger(g2)
            _commit(g2, "genesis: two drafts, header-only ledger")
            _write(g2, _changelog_text(["1.0.5"]))
            if _run_quiet(g2, "HEAD") != 0:
                failures.append("git case G2: genesis consolidation to a lower latest expected exit 0")

            # G3 a ledger ROW makes the base non-genesis, so relabel fails: base [1.0.0] untagged with a
            # one-row ledger; head relabels the changelog to [1.0.5] (ledger row untouched) -> exit 1.
            g3 = base_tmp / "ledger-row-relabel"
            _init(g3)
            _root(g3)
            _write(g3, _changelog_text(["1.0.0"]))
            _write_ledger(g3, '\n[[release]]\nversion = "1.0.0"\ncommit_sha = "aaa"\n')
            _commit(g3, "shipped: one ledger row")
            _write(g3, _changelog_text(["1.0.5"]))
            if _run_quiet(g3, "HEAD") != 1:
                failures.append("git case G3: relabel once a ledger row exists expected exit 1")

            # G4 a tagged base is non-genesis, so relabel fails: base changelog [1.0.0] tag="v1.0.0" +
            # header-only ledger + a real git tag; head relabels to [1.0.5] -> exit 1.
            g4 = base_tmp / "tagged-relabel"
            _init(g4)
            _root(g4)
            _write(g4, _changelog_text(["1.0.0"], tag_on={"1.0.0": "v1.0.0"}))
            _write_ledger(g4)
            _commit(g4, "shipped: tagged 1.0.0")
            subprocess.run(["git", "-C", str(g4), "tag", "v1.0.0", "HEAD"],
                           check=True, capture_output=True, text=True)
            _write(g4, _changelog_text(["1.0.5"]))
            if _run_quiet(g4, "HEAD") != 1:
                failures.append("git case G4: relabel once tagged expected exit 1")

            # G5 the boundary commit passes: commit P is genesis (changelog [1.0.0] untagged + header-only
            # ledger); commit S ships 1.0.5 (changelog [1.0.5] tag="v1.0.5" + a ledger row) with a real git
            # tag. With base P (genesis) layer_a is NOT APPLICABLE, layer_b validates v1.0.5, layer_c the
            # appended ledger row -> exit 0.
            g5b = base_tmp / "boundary"
            _init(g5b)
            _root(g5b)
            _write(g5b, _changelog_text(["1.0.0"]))
            _write_ledger(g5b)
            _commit(g5b, "P: genesis 1.0.0 draft")
            p_sha = subprocess.run(["git", "-C", str(g5b), "rev-parse", "HEAD"],
                                   check=True, capture_output=True, text=True).stdout.strip()
            _write(g5b, _changelog_text(["1.0.5"], tag_on={"1.0.5": "v1.0.5"}))
            _write_ledger(g5b, '\n[[release]]\nversion = "1.0.5"\ncommit_sha = "ccc"\n')
            _commit(g5b, "S: ship 1.0.5")
            subprocess.run(["git", "-C", str(g5b), "tag", "v1.0.5", "HEAD"],
                           check=True, capture_output=True, text=True)
            s_sha = subprocess.run(["git", "-C", str(g5b), "rev-parse", "HEAD"],
                                   check=True, capture_output=True, text=True).stdout.strip()
            if _run_quiet(g5b, p_sha) != 0:
                failures.append("git case G5: the boundary commit (base P genesis) expected exit 0")

            # G6 a commit after the boundary is non-genesis, so relabeling the shipped 1.0.5 -> 1.0.6 fails.
            _write(g5b, _changelog_text(["1.0.6"]))
            if _run_quiet(g5b, s_sha) != 1:
                failures.append("git case G6: relabel 1.0.5 -> 1.0.6 from a shipped base expected exit 1")

            # G7 ISOLATES the base-ledger fail-closed read: the base commit carries changelog [1.0.0]
            # untagged plus a MALFORMED (unparseable) releases.toml; the working tree DELETES releases.toml
            # (so layer_c is NOT APPLICABLE at head) and relabels the changelog to [1.0.5]. The base-read
            # GateError in _base_is_genesis is then the ONLY source of exit 2, so G7 detects a fail-open
            # regression of that read rather than an exit 2 that any head parse could also produce.
            g7 = base_tmp / "malformed-ledger"
            _init(g7)
            _root(g7)
            _write(g7, _changelog_text(["1.0.0"]))
            core7 = g7 / ".aiqt" / "core"
            core7.mkdir(parents=True, exist_ok=True)
            (core7 / "releases.toml").write_text("format-version = 1\n[[release]\nbroken\n", encoding="utf-8")
            _commit(g7, "base with unparseable ledger")
            (core7 / "releases.toml").unlink()
            _write(g7, _changelog_text(["1.0.5"]))
            if _run_quiet(g7, "HEAD") != 2:
                failures.append("git case G7: a malformed base ledger (base read isolated) expected "
                                "fail-closed exit 2")

            # G8 tag-preservation isolates the new check: base changelog [1.0.0] tag="v1.0.0" + header-only
            # ledger + a real git tag; head keeps version 1.0.0 but DROPS the tag key -> exit 1 from
            # check_tag_preservation alone (layer_b NA with no head tag, layer_c header-only both sides).
            g8 = base_tmp / "tag-preservation"
            _init(g8)
            _root(g8)
            _write(g8, _changelog_text(["1.0.0"], tag_on={"1.0.0": "v1.0.0"}))
            _write_ledger(g8)
            _commit(g8, "shipped: tagged 1.0.0, header-only ledger")
            subprocess.run(["git", "-C", str(g8), "tag", "v1.0.0", "HEAD"],
                           check=True, capture_output=True, text=True)
            _write(g8, _changelog_text(["1.0.0"]))
            if _run_quiet(g8, "HEAD") != 1:
                failures.append("git case G8: dropping a shipped release's tag key expected exit 1")

            # G9 (FIX 1) a base ledger that is valid TOML but records its rows under a MISSPELLED top-level
            # key ([[releases]] not [[release]]) fails closed (exit 2): the base-read schema check rejects
            # the unknown key rather than reading zero [[release]] rows and passing. Base changelog [1.0.0]
            # untagged; the working tree carries a valid header-only ledger and relabels to [1.0.5], so the
            # base read is the only source of exit 2. Without FIX 1 this read zero rows and exited 0.
            g9 = base_tmp / "misspelled-ledger-key"
            _init(g9)
            _root(g9)
            _write(g9, _changelog_text(["1.0.0"]))
            core9 = g9 / ".aiqt" / "core"
            core9.mkdir(parents=True, exist_ok=True)
            (core9 / "releases.toml").write_text(
                'format-version = 1\n\n[[releases]]\nversion = "1.0.0"\ncommit_sha = "aaa"\n',
                encoding="utf-8")
            _commit(g9, "base ledger with a misspelled top-level key")
            _write_ledger(g9)
            _write(g9, _changelog_text(["1.0.5"]))
            if _run_quiet(g9, "HEAD") != 2:
                failures.append("git case G9: a base ledger with a misspelled top-level key expected "
                                "fail-closed exit 2 (FIX 1)")

            # G10 (FIX 1) a base ledger with an unsupported format-version fails closed (exit 2): the
            # base-read schema check rejects format-version = 999. Base changelog [1.0.0] untagged; the
            # working tree carries a valid header-only ledger and relabels to [1.0.5], so the base read is
            # the only source of exit 2. Without FIX 1 this read zero rows and exited 0.
            g10 = base_tmp / "bad-format-version"
            _init(g10)
            _root(g10)
            _write(g10, _changelog_text(["1.0.0"]))
            core10 = g10 / ".aiqt" / "core"
            core10.mkdir(parents=True, exist_ok=True)
            (core10 / "releases.toml").write_text("format-version = 999\n", encoding="utf-8")
            _commit(g10, "base ledger with an unsupported format-version")
            _write_ledger(g10)
            _write(g10, _changelog_text(["1.0.5"]))
            if _run_quiet(g10, "HEAD") != 2:
                failures.append("git case G10: a base ledger with format-version = 999 expected "
                                "fail-closed exit 2 (FIX 1)")

            # R cases: the ONE-TIME pre-release born-release relabel exception (layer_d). Shared
            # shape: the base commit carries changelog [1.0.5] with a recorded and real tag v1.0.5
            # (the append-only protected history the exception requires), a header-only ledger, and
            # an id-history of two [[born]] rows at born-release 1.1.0 plus one [[tombstone]] row;
            # the head working tree appends changelog 1.1.1, relabels BOTH born rows to 1.1.1, and
            # declares the cut in a complete release-cut declaration (operation, previous and next
            # version, and the digest of the complete base changelog blob all matching). Each
            # negative case then breaks one condition at the level that can isolate it: run() where
            # the broken condition is the only refusal, a direct helper call where an earlier
            # condition or another layer would mask it (each such case says which; a case that
            # cannot reach its named condition without touching other fields, such as R12f, says
            # which rule actually refuses it). The findings outside the window stay exactly the
            # plain append-only ones.
            import hashlib

            reg_rows = ('[[born]]\nid = "aaa1"\nborn-release = "1.1.0"\n\n'
                        '[[born]]\nid = "aaa2"\nborn-release = "1.1.0"\n\n'
                        '[[tombstone]]\nid = "zzz9"\nretired-release = "1.0.0"\n')
            reg_relabelled = reg_rows.replace('born-release = "1.1.0"', 'born-release = "1.1.1"')
            base_log = _changelog_text(["1.0.5"], tag_on={"1.0.5": "v1.0.5"})
            head_log = _changelog_text(["1.0.5", "1.1.1"], tag_on={"1.0.5": "v1.0.5"})

            def _cut_text(next_version="1.1.1", previous="1.0.5", base_text=base_log,
                          operation="append", digest=None):
                if digest is None:
                    digest = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
                return ('schema = 1\noperation = "{}"\nprevious-version = "{}"\n'
                        'next-version = "{}"\nbase-changelog-sha256 = "{}"\n').format(
                            operation, previous, next_version, digest)

            def _write_register(path, text):
                core_dir = path / ".aiqt" / "core"
                core_dir.mkdir(parents=True, exist_ok=True)
                (core_dir / "id-history.toml").write_text(text, encoding="utf-8")

            def _write_cut(path, text):
                (path / ".aiqt" / "release-cut.toml").write_text(text, encoding="utf-8")

            def _relabel_repo(name, base_register=reg_rows, ledger_body="", base_text=base_log,
                              tag="v1.0.5", base_cut=None):
                repo = base_tmp / name
                _init(repo)
                _root(repo)
                _write(repo, base_text)
                _write_ledger(repo, ledger_body)
                _write_register(repo, base_register)
                if base_cut is not None:
                    _write_cut(repo, base_cut)
                _commit(repo, "base: pre-release register")
                if tag is not None:
                    subprocess.run(["git", "-C", str(repo), "tag", tag, "HEAD"],
                                   check=True, capture_output=True, text=True)
                return repo

            # R1 inside the window the declared whole-register relabel passes, with the DISTINCT
            # status line naming the old and new version and the row count.
            rr1 = _relabel_repo("relabel-pass")
            rr1_base = subprocess.run(["git", "-C", str(rr1), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            _write(rr1, head_log)
            _write_register(rr1, reg_relabelled)
            _write_cut(rr1, _cut_text())
            rc, out = _run_capture(rr1, "HEAD")
            if rc != 0:
                failures.append("git case R1: the in-window born-release relabel expected exit 0")
            elif "relabel 1.1.0 -> 1.1.1 on 2 [[born]] row(s)" not in out:
                failures.append("git case R1: the relabel PASS line must name the old version, the "
                                "new version and the row count (got {!r})".format(out))

            # R2 the same transition through the two CI baselines: push mode (--base HEAD^) and PR
            # mode (--base origin/BRANCH, a remote-tracking ref), after committing the head state.
            _commit(rr1, "relabel the born rows and append 1.1.1")
            if _run_quiet(rr1, "HEAD^") != 0:
                failures.append("git case R2a: the relabel in push mode (--base HEAD^) expected exit 0")
            subprocess.run(["git", "-C", str(rr1), "update-ref", "refs/remotes/origin/main", rr1_base],
                           check=True, capture_output=True, text=True)
            if _run_quiet(rr1, "origin/main") != 0:
                failures.append("git case R2b: the relabel in PR mode (--base origin/main) expected "
                                "exit 0")

            # R3 the window has ENDED at base: the base ledger records a row, so the identical relabel
            # fails (exit 1, the plain append-only findings).
            rr3 = _relabel_repo("relabel-base-ledger-row",
                                ledger_body='\n[[release]]\nversion = "1.0.0"\ncommit_sha = "aaa"\n')
            _write(rr3, head_log)
            _write_register(rr3, reg_relabelled)
            _write_cut(rr3, _cut_text())
            if _run_quiet(rr3, "HEAD") != 1:
                failures.append("git case R3: relabel once the base ledger records a row expected "
                                "exit 1")

            # R4 the window ENDS at head too: the head ledger gains a row (a valid layer_c append) in
            # the same change as the relabel, so the relabel fails.
            rr4 = _relabel_repo("relabel-head-ledger-row")
            _write(rr4, head_log)
            _write_ledger(rr4, '\n[[release]]\nversion = "1.1.1"\ncommit_sha = "bbb"\n')
            _write_register(rr4, reg_relabelled)
            _write_cut(rr4, _cut_text())
            if _run_quiet(rr4, "HEAD") != 1:
                failures.append("git case R4: relabel while the head ledger records a row expected "
                                "exit 1")

            # R5 the new version is NOT the appended head changelog version (rows move to 1.2.0 while
            # the changelog appends 1.1.1; the declaration matches the changelog, so only this
            # condition refuses).
            rr5 = _relabel_repo("relabel-wrong-target")
            _write(rr5, head_log)
            _write_register(rr5, reg_rows.replace('born-release = "1.1.0"',
                                                  'born-release = "1.2.0"'))
            _write_cut(rr5, _cut_text())
            if _run_quiet(rr5, "HEAD") != 1:
                failures.append("git case R5: relabel to a version other than the appended head "
                                "changelog version expected exit 1")

            # R6 the changelog is UNCHANGED (no version is being cut), so the relabel fails, and the
            # findings are exactly the plain full-row prefix findings (one per edited row).
            rr6 = _relabel_repo("relabel-no-cut")
            _write_register(rr6, reg_relabelled)
            _write_cut(rr6, _cut_text())
            rc, out = _run_capture(rr6, "HEAD")
            if rc != 1:
                failures.append("git case R6: relabel with the changelog unchanged expected exit 1")
            elif ("FAIL: 2 version-monotonicity finding(s)" not in out
                  or "an existing row was edited or reordered" not in out):
                failures.append("git case R6: outside the window the relabel must keep the plain "
                                "append-only findings, one per edited row (got {!r})".format(out))

            # R7 the old version was RELEASED (a recorded base changelog release version that carries
            # no tag, so the release exclusion is the only condition that refuses).
            base_log7 = _changelog_text(["1.0.4", "1.0.5"], tag_on={"1.0.5": "v1.0.5"})
            rr7 = _relabel_repo("relabel-from-released", base_text=base_log7,
                                base_register=reg_rows.replace('born-release = "1.1.0"',
                                                               'born-release = "1.0.4"'))
            _write(rr7, _changelog_text(["1.0.4", "1.0.5", "1.1.1"], tag_on={"1.0.5": "v1.0.5"}))
            _write_register(rr7, reg_relabelled)
            _write_cut(rr7, _cut_text(base_text=base_log7))
            if _run_quiet(rr7, "HEAD") != 1:
                failures.append("git case R7: relabel from a released (base changelog) version "
                                "expected exit 1")
            # R7 (unit level) the base-release exclusion in isolation, with the reason asserted: when
            # the head changelog really appends, it contains every base release, so the head-side
            # exclusion would mask a removed base-side one at run() level; this head does not extend
            # the base, so the base-side exclusion is the one condition that can name the refusal.
            rr7_sha = subprocess.run(["git", "-C", str(rr7), "rev-parse", "HEAD"],
                                     check=True, capture_output=True, text=True).stdout.strip()
            head7 = [{"version": "1.1.1", "tag": None}]
            reason7 = _relabel_window_reason(rr7, rr7_sha, head7, "1.0.4", "1.1.1")
            if reason7 is None or "recorded base changelog release" not in reason7:
                failures.append("unit case R7: an old version recorded as a base changelog release "
                                "must refuse the relabel with its own reason (got "
                                "{!r})".format(reason7))

            # R8 a row changes its id BESIDE born-release: not a relabel shape.
            rr8 = _relabel_repo("relabel-id-edit")
            _write(rr8, head_log)
            _write_register(rr8, reg_relabelled.replace('id = "aaa2"', 'id = "aaa2x"'))
            _write_cut(rr8, _cut_text())
            if _run_quiet(rr8, "HEAD") != 1:
                failures.append("git case R8: relabel with an id edit expected exit 1")

            # R9 a [[tombstone]] row changes: the relabel never covers the other sections.
            rr9 = _relabel_repo("relabel-tombstone-edit")
            _write(rr9, head_log)
            _write_register(rr9, reg_relabelled.replace('retired-release = "1.0.0"',
                                                        'retired-release = "1.0.1"'))
            _write_cut(rr9, _cut_text())
            if _run_quiet(rr9, "HEAD") != 1:
                failures.append("git case R9: relabel with a tombstone edit expected exit 1")

            # R10 TWO distinct old versions move in one transition: refused (one shared old version
            # per transition).
            reg_two_old = ('[[born]]\nid = "aaa1"\nborn-release = "1.1.0"\n\n'
                           '[[born]]\nid = "aaa2"\nborn-release = "1.0.9"\n')
            rr10 = _relabel_repo("relabel-two-old-versions", base_register=reg_two_old)
            _write(rr10, head_log)
            _write_register(rr10, reg_two_old.replace('born-release = "1.1.0"',
                                                      'born-release = "1.1.1"')
                            .replace('born-release = "1.0.9"', 'born-release = "1.1.1"'))
            _write_cut(rr10, _cut_text())
            if _run_quiet(rr10, "HEAD") != 1:
                failures.append("git case R10: relabel from two distinct old versions expected exit 1")

            # R11 the old version is not a bare SemVer version.
            rr11 = _relabel_repo("relabel-malformed-old",
                                 base_register='[[born]]\nid = "aaa1"\nborn-release = "garbage"\n')
            _write(rr11, head_log)
            _write_register(rr11, '[[born]]\nid = "aaa1"\nborn-release = "1.1.1"\n')
            _write_cut(rr11, _cut_text())
            if _run_quiet(rr11, "HEAD") != 1:
                failures.append("git case R11: relabel from a malformed old version expected exit 1")

            # R12 the REQUIRED release-cut declaration, judged by check_release_cut's own parser and
            # transition evaluator: a complete matching declaration passes (b); a mismatched
            # next-version (a), an ABSENT declaration (d), a wrong complete-base digest (e), an
            # initialize-shaped declaration (f: the wrong operation with the empty previous-version
            # and absent digest that shape requires, refused by the evaluator's digest comparison
            # against a present base, because flipping ONLY the operation is a schema error the
            # evaluator fails closed on, not a reachable policy refusal), and a declaration already
            # present unchanged at base (h, stale) each refuse with the plain findings (exit 1); a
            # schema-invalid declaration on either side (c, g, i, j) is fail-closed (exit 2), never
            # read as absent.
            rr12 = _relabel_repo("relabel-cut-declaration")
            _write(rr12, head_log)
            _write_register(rr12, reg_relabelled)
            cut12 = rr12 / ".aiqt" / "release-cut.toml"
            cut12.write_text(_cut_text(next_version="1.2.0"), encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 1:
                failures.append("git case R12a: relabel against a mismatched declared next-version "
                                "expected exit 1")
            cut12.write_text(_cut_text(), encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 0:
                failures.append("git case R12b: relabel under a complete matching declaration "
                                "expected exit 0")
            cut12.write_text("next-version = 7\n", encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 2:
                failures.append("git case R12c: relabel with a non-string declared next-version "
                                "expected fail-closed exit 2")
            cut12.unlink()
            if _run_quiet(rr12, "HEAD") != 1:
                failures.append("git case R12d: relabel WITHOUT a release-cut declaration expected "
                                "exit 1 (the declaration is required)")
            cut12.write_text(_cut_text(digest="0" * 64), encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 1:
                failures.append("git case R12e: relabel under a declaration whose digest is not the "
                                "complete base changelog's expected exit 1")
            cut12.write_text(_cut_text(operation="initialize", previous="", digest="absent"),
                             encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 1:
                failures.append("git case R12f: relabel under an initialize-shaped declaration "
                                "(wrong operation) expected exit 1")
            # R12f (unit level) the rule that refuses the initialize-shaped declaration is the
            # evaluator's digest comparison (the absent digest that shape requires can never match a
            # present base), asserted here so the R12 comment stays honest about which rule is
            # reached; run() keeps the plain findings, so only the direct call can name the reason.
            rr12_sha = subprocess.run(["git", "-C", str(rr12), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            head12 = [{"version": "1.0.5", "tag": "v1.0.5"}, {"version": "1.1.1", "tag": None}]
            reason12f = _relabel_window_reason(rr12, rr12_sha, head12, "1.1.0", "1.1.1")
            if reason12f is None or "declaration digest does not match" not in reason12f:
                failures.append("unit case R12f: the initialize-shaped declaration must be refused "
                                "by the digest comparison (got {!r})".format(reason12f))
            cut12.write_text('next-version = "1.1.1"\n', encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 2:
                failures.append("git case R12g: relabel under an incomplete declaration (only "
                                "next-version) expected fail-closed exit 2")
            cut12.write_text("not = = toml\n", encoding="utf-8")
            if _run_quiet(rr12, "HEAD") != 2:
                failures.append("git case R12j: relabel under an unparseable declaration expected "
                                "fail-closed exit 2, never a read-as-absent pass")

            # R12h a STALE declaration (the same declaration already recorded at base) counts for
            # nothing; R12i a schema-invalid declaration AT BASE is fail-closed, never skipped.
            rr12s = _relabel_repo("relabel-cut-stale", base_cut=_cut_text())
            _write(rr12s, head_log)
            _write_register(rr12s, reg_relabelled)
            if _run_quiet(rr12s, "HEAD") != 1:
                failures.append("git case R12h: relabel under a declaration unchanged from base "
                                "(stale) expected exit 1")
            rr12m = _relabel_repo("relabel-cut-malformed-base", base_cut="not = = toml\n")
            _write(rr12m, head_log)
            _write_register(rr12m, reg_relabelled)
            _write_cut(rr12m, _cut_text())
            if _run_quiet(rr12m, "HEAD") != 2:
                failures.append("git case R12i: relabel over an unparseable BASE declaration "
                                "expected fail-closed exit 2")

            # R13 the strictly-above bound on the new version against the base latest (1.0.5), each
            # end derived from the refusal `new <= base latest`: just below (1.0.4) and exactly at
            # (1.0.5) refuse, just above (1.0.6) passes end to end. Below and at are judged at the
            # helper level with the reason asserted: at run() level layer_a's no-decrease or the head
            # changelog's own ordering would refuse those heads too, so only the direct call isolates
            # this condition.
            rr13 = _relabel_repo("relabel-target-bounds")
            rr13_sha = subprocess.run(["git", "-C", str(rr13), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            for bound_version in ("1.0.4", "1.0.5"):
                head13 = [{"version": "1.0.5", "tag": "v1.0.5"},
                          {"version": bound_version, "tag": None}]
                reason13 = _relabel_window_reason(rr13, rr13_sha, head13, "1.1.0", bound_version)
                if reason13 is None or "is not above" not in reason13:
                    failures.append("unit case R13: relabel to {} against base latest 1.0.5 must "
                                    "refuse as not strictly above (got {!r})".format(
                                        bound_version, reason13))
            _write(rr13, _changelog_text(["1.0.5", "1.0.6"], tag_on={"1.0.5": "v1.0.5"}))
            _write_register(rr13, reg_rows.replace('born-release = "1.1.0"',
                                                   'born-release = "1.0.6"'))
            _write_cut(rr13, _cut_text(next_version="1.0.6"))
            if _run_quiet(rr13, "HEAD") != 0:
                failures.append("git case R13: relabel to 1.0.6, just above the base latest 1.0.5, "
                                "expected exit 0")

            # R14 (unit level, over a real base commit) the old version recorded only as a base
            # changelog TAG version still reads as released. The run()-level route cannot isolate this
            # (a base tag whose version mismatches its entry trips layer_b/tag-preservation first).
            rr14 = base_tmp / "relabel-from-tagged"
            _init(rr14)
            _root(rr14)
            _write(rr14, _changelog_text(["1.0.0"], tag_on={"1.0.0": "v1.0.2"}))
            _write_ledger(rr14)
            _commit(rr14, "base with a mismatched recorded tag")
            rr14_sha = subprocess.run(["git", "-C", str(rr14), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            head14 = [{"version": "1.0.0", "tag": "v1.0.2"}, {"version": "1.1.1", "tag": None}]
            reason14 = _relabel_window_reason(rr14, rr14_sha, head14, "1.0.2", "1.1.1")
            if reason14 is None or "base changelog tag version" not in reason14:
                failures.append("unit case R14: an old version recorded as a base changelog tag "
                                "version must refuse the relabel with the base-side reason (got "
                                "{!r})".format(reason14))

            # R15 (unit level) the head changelog must APPEND to the base: an unchanged release list
            # (the growth half) and a longer list that REWRITES the base prefix (the prefix half) are
            # each refused by the append condition alone, with the reason asserted. At run() level
            # layer_a's own prefix finding refuses the rewrite against this tagged base too, so only
            # the direct call isolates this condition.
            rr15 = _relabel_repo("relabel-changelog-rewrite")
            rr15_sha = subprocess.run(["git", "-C", str(rr15), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            for label15, head15 in (
                    ("an unchanged head changelog",
                     [{"version": "1.0.5", "tag": "v1.0.5"}]),
                    ("a longer head changelog that rewrites the base prefix",
                     [{"version": "1.0.9", "tag": None}, {"version": "1.1.1", "tag": None}])):
                reason15 = _relabel_window_reason(rr15, rr15_sha, head15, "1.1.0",
                                                  head15[-1]["version"])
                if reason15 is None or "does not append" not in reason15:
                    failures.append("unit case R15: {} must refuse the relabel as not appending "
                                    "(got {!r})".format(label15, reason15))

            # R16 a base row is REMOVED beside the relabel, with the tombstone section UNCHANGED, so
            # the born-row length guard is the one condition that refuses.
            rr16 = _relabel_repo("relabel-row-removed")
            _write(rr16, head_log)
            _write_register(rr16, '[[born]]\nid = "aaa1"\nborn-release = "1.1.1"\n\n'
                                  '[[tombstone]]\nid = "zzz9"\nretired-release = "1.0.0"\n')
            _write_cut(rr16, _cut_text())
            if _run_quiet(rr16, "HEAD") != 1:
                failures.append("git case R16: relabel beside a removed born row expected exit 1")

            # R17 the relabel beside APPENDED born rows passes: appended [[born]] rows were never
            # covered by the prefix and stay deliberately allowed in the same transition.
            rr17 = _relabel_repo("relabel-with-append")
            _write(rr17, head_log)
            _write_register(rr17, reg_relabelled + '\n[[born]]\nid = "aaa3"\n'
                                                   'born-release = "1.1.1"\n')
            _write_cut(rr17, _cut_text())
            if _run_quiet(rr17, "HEAD") != 0:
                failures.append("git case R17: relabel beside appended born rows expected exit 0")

            # R17b an appended [[born]] row at the OLD version is refused: the never-released version
            # must survive in no row, so an append cannot reintroduce what the relabel just moved
            # every base row away from (R17, the same append at the NEW version, is the passing
            # control; the appended row is the only difference between the two).
            rr17b = _relabel_repo("relabel-append-at-old")
            _write(rr17b, head_log)
            _write_register(rr17b, reg_relabelled + '\n[[born]]\nid = "aaa3"\n'
                                                    'born-release = "1.1.0"\n')
            _write_cut(rr17b, _cut_text())
            if _run_quiet(rr17b, "HEAD") != 1:
                failures.append("git case R17b: relabel beside an appended born row at the old "
                                "(never-released) version expected exit 1")

            # R18 an UNTAGGED pre-release base is refused: with no recorded changelog tag the base
            # changelog stays rewritable under the layer_a genesis exemption, so the one-time
            # guarantee cannot be read from the files. Everything else here is the valid R1 shape,
            # declaration included, so the missing tag is the one condition that refuses.
            base_log18 = _changelog_text(["1.0.0"])
            rr18 = _relabel_repo("relabel-untagged-base", base_text=base_log18, tag=None)
            _write(rr18, _changelog_text(["1.0.0", "1.1.1"]))
            _write_register(rr18, reg_relabelled)
            _write_cut(rr18, _cut_text(previous="1.0.0", base_text=base_log18))
            if _run_quiet(rr18, "HEAD") != 1:
                failures.append("git case R18: relabel against an untagged (rewritable) base "
                                "changelog expected exit 1")

            # R19 (unit level, over a real base commit) the BASE-side zero-row condition in isolation.
            # At run() level a base ledger row always implies a head ledger row too (rows are
            # append-only under layer_c), so the head-side condition masks this one there; the direct
            # call reads a header-only working-tree ledger against a base that records a row.
            rr19 = base_tmp / "relabel-base-row-isolated"
            _init(rr19)
            _root(rr19)
            _write(rr19, _changelog_text(["1.0.0"]))
            _write_ledger(rr19, '\n[[release]]\nversion = "1.0.0"\ncommit_sha = "aaa"\n')
            _commit(rr19, "base ledger with one row")
            rr19_sha = subprocess.run(["git", "-C", str(rr19), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            _write_ledger(rr19)
            head19 = [{"version": "1.0.0", "tag": None}, {"version": "1.1.1", "tag": None}]
            reason19 = _relabel_window_reason(rr19, rr19_sha, head19, "1.1.0", "1.1.1")
            if reason19 is None or "base" not in reason19 or "row" not in reason19:
                failures.append("unit case R19: a base ledger that records a row must refuse the "
                                "relabel on the base side alone (got {!r})".format(reason19))

            # R20 a PARTIAL relabel (one of two rows moves) is never a relabel shape: every base
            # [[born]] row must participate, so no row can be left at the never-released version.
            rr20 = _relabel_repo("relabel-partial")
            _write(rr20, head_log)
            _write_register(rr20, reg_rows.replace('id = "aaa1"\nborn-release = "1.1.0"',
                                                   'id = "aaa1"\nborn-release = "1.1.1"'))
            _write_cut(rr20, _cut_text())
            if _run_quiet(rr20, "HEAD") != 1:
                failures.append("git case R20: a partial relabel (one row left at the old version) "
                                "expected exit 1")

            # R21 a [[tombstone]] (a) or [[successor]] (b) row is APPENDED beside the relabel, each a
            # prefix-preserving extension of a base that already holds one row of that section: the
            # other sections must be exactly EQUAL across a granted relabel, never merely
            # prefix-preserved, and each section's comparison is exercised on its own so weakening
            # either one independently to prefix preservation fails its paired case.
            reg21 = reg_rows + '\n[[successor]]\nid = "sss1"\nsuperseded-by = "aaa1"\n'
            reg21_new = reg21.replace('born-release = "1.1.0"', 'born-release = "1.1.1"')
            for label21, section21, appended21 in (
                    ("a", "tombstone", '[[tombstone]]\nid = "zzz8"\nretired-release = "1.0.0"\n'),
                    ("b", "successor", '[[successor]]\nid = "sss2"\nsuperseded-by = "aaa2"\n')):
                rr21 = _relabel_repo("relabel-section-append-" + label21, base_register=reg21)
                _write(rr21, head_log)
                _write_register(rr21, reg21_new + "\n" + appended21)
                _write_cut(rr21, _cut_text())
                if _run_quiet(rr21, "HEAD") != 1:
                    failures.append("git case R21{}: relabel beside an appended {} row expected "
                                    "exit 1".format(label21, section21))

            # R22 a changed row GAINS A KEY beside the relabel: never a relabel shape (the key-set
            # comparison is the one shape condition that refuses).
            rr22 = _relabel_repo("relabel-extra-key")
            _write(rr22, head_log)
            _write_register(rr22, reg_relabelled.replace(
                'id = "aaa2"\nborn-release = "1.1.1"',
                'id = "aaa2"\nborn-release = "1.1.1"\nnote = "x"'))
            _write_cut(rr22, _cut_text())
            if _run_quiet(rr22, "HEAD") != 1:
                failures.append("git case R22: relabel with an extra key on a changed row expected "
                                "exit 1")

            # R23 (unit level) shape predicates that other conditions mask at run() level: a
            # non-string born-release on either side and two distinct new versions are each refused
            # by the shape helper itself (at run() level the window or target conditions would also
            # refuse such heads, so only the direct call isolates these predicates).
            born23 = [{"id": "a", "born-release": "1.1.0"}, {"id": "b", "born-release": "1.1.0"}]
            born23_int = [{"id": "a", "born-release": 7}, {"id": "b", "born-release": 7}]
            born23_new = [{"id": "a", "born-release": "1.1.1"}, {"id": "b", "born-release": "1.1.1"}]
            born23_two = [{"id": "a", "born-release": "1.1.1"}, {"id": "b", "born-release": "1.1.2"}]
            for label23, b23, h23 in (
                    ("a non-string new born-release", born23, born23_int),
                    ("a non-string old born-release", born23_int, born23_new),
                    ("two distinct new versions", born23, born23_two)):
                if _born_relabel_shape({"born": b23}, {"born": h23}) is not None:
                    failures.append("unit case R23: {} must never be a relabel shape".format(label23))

            # R24 a [[successor]] row changes beside the relabel: the successor comparison is the one
            # condition that refuses.
            reg_succ = reg_rows + '\n[[successor]]\nid = "sss1"\nsuperseded-by = "aaa1"\n'
            rr24 = _relabel_repo("relabel-successor-edit", base_register=reg_succ)
            _write(rr24, head_log)
            _write_register(rr24, reg_succ.replace('born-release = "1.1.0"',
                                                   'born-release = "1.1.1"')
                            .replace('superseded-by = "aaa1"', 'superseded-by = "aaa2"'))
            _write_cut(rr24, _cut_text())
            if _run_quiet(rr24, "HEAD") != 1:
                failures.append("git case R24: relabel with a successor edit expected exit 1")

            # R25 two distinct NEW versions at run() level (integration for the R23 predicate).
            rr25 = _relabel_repo("relabel-two-new-versions")
            _write(rr25, head_log)
            _write_register(rr25, reg_rows.replace('id = "aaa1"\nborn-release = "1.1.0"',
                                                   'id = "aaa1"\nborn-release = "1.1.1"')
                            .replace('id = "aaa2"\nborn-release = "1.1.0"',
                                     'id = "aaa2"\nborn-release = "1.1.2"'))
            _write_cut(rr25, _cut_text())
            if _run_quiet(rr25, "HEAD") != 1:
                failures.append("git case R25: relabel to two distinct new versions expected exit 1")

            # R26 the release-order record ABSENT at base refuses the relabel (zero shipped releases
            # cannot be confirmed) with the plain findings (exit 1): never an error, never a pass.
            rr26 = base_tmp / "relabel-base-ledger-absent"
            _init(rr26)
            _root(rr26)
            _write(rr26, base_log)
            _write_register(rr26, reg_rows)
            _commit(rr26, "base without a release-order record")
            subprocess.run(["git", "-C", str(rr26), "tag", "v1.0.5", "HEAD"],
                           check=True, capture_output=True, text=True)
            _write(rr26, head_log)
            _write_ledger(rr26)
            _write_register(rr26, reg_relabelled)
            _write_cut(rr26, _cut_text())
            if _run_quiet(rr26, "HEAD") != 1:
                failures.append("git case R26: relabel with the base release-order record absent "
                                "expected exit 1")

            # R27 the release-order record ABSENT at head refuses the relabel the same way (an open
            # window is never inferred from a missing record).
            rr27 = _relabel_repo("relabel-head-ledger-absent")
            _write(rr27, head_log)
            (rr27 / ".aiqt" / "core" / "releases.toml").unlink()
            _write_register(rr27, reg_relabelled)
            _write_cut(rr27, _cut_text())
            if _run_quiet(rr27, "HEAD") != 1:
                failures.append("git case R27: relabel with the head release-order record absent "
                                "expected exit 1")

            # R28 a head release-order record with a MISSPELLED section key is fail-closed (exit 2),
            # never read as a zero-row window.
            rr28 = _relabel_repo("relabel-head-ledger-misspelled")
            _write(rr28, head_log)
            (rr28 / ".aiqt" / "core" / "releases.toml").write_text(
                'format-version = 1\n\n[[releases]]\nversion = "1.1.1"\n', encoding="utf-8")
            _write_register(rr28, reg_relabelled)
            _write_cut(rr28, _cut_text())
            if _run_quiet(rr28, "HEAD") != 2:
                failures.append("git case R28: relabel over a misspelled head ledger key expected "
                                "fail-closed exit 2")

            # R29 (unit level, over a real base commit) changelog.toml ABSENT at base refuses with
            # its own reason, never read as an empty base changelog. At run() level layer_a reports
            # NOT APPLICABLE for that base, so only the direct call pins this condition's reason.
            rr29 = base_tmp / "relabel-base-changelog-absent"
            _init(rr29)
            _root(rr29)
            _write_ledger(rr29)
            _write_register(rr29, reg_rows)
            _commit(rr29, "base without a changelog")
            rr29_sha = subprocess.run(["git", "-C", str(rr29), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            head29 = [{"version": "1.1.1", "tag": None}]
            reason29 = _relabel_window_reason(rr29, rr29_sha, head29, "1.1.0", "1.1.1")
            if reason29 is None or "changelog.toml is absent" not in reason29:
                failures.append("unit case R29: an absent base changelog must refuse the relabel "
                                "with its own reason (got {!r})".format(reason29))

            # R30 a SECOND relabel transition is refused: after the first relabel lands (the rr1
            # commit from R2), the version it cut is a recorded base changelog release, kept
            # append-only by layer_a against this tagged history, so relabelling away from it reads
            # as released. This is the multi-commit regression for the one-time guarantee.
            _write(rr1, _changelog_text(["1.0.5", "1.1.1", "1.1.2"], tag_on={"1.0.5": "v1.0.5"}))
            _write_register(rr1, reg_rows.replace('born-release = "1.1.0"',
                                                  'born-release = "1.1.2"'))
            _write_cut(rr1, _cut_text(next_version="1.1.2", previous="1.1.1", base_text=head_log))
            if _run_quiet(rr1, "HEAD") != 1:
                failures.append("git case R30: a second relabel transition after the first landed "
                                "expected exit 1")

            # R31 the old version may not become a recorded release in the SAME transition: two
            # appended versions with the rows moved from the intermediate one are refused at run()
            # level, and the head-side recorded-release and recorded-tag-version exclusions are each
            # isolated at the helper level with the reason asserted (at run() level the evaluator's
            # single-append rule would also refuse the double append).
            rr31 = _relabel_repo("relabel-old-appended")
            rr31_sha = subprocess.run(["git", "-C", str(rr31), "rev-parse", "HEAD"],
                                      check=True, capture_output=True, text=True).stdout.strip()
            _write(rr31, _changelog_text(["1.0.5", "1.1.0", "1.1.1"], tag_on={"1.0.5": "v1.0.5"}))
            _write_register(rr31, reg_relabelled)
            _write_cut(rr31, _cut_text())
            if _run_quiet(rr31, "HEAD") != 1:
                failures.append("git case R31: relabel from a version appended as a head release in "
                                "the same transition expected exit 1")
            head31 = [{"version": "1.0.5", "tag": "v1.0.5"}, {"version": "1.1.0", "tag": None},
                      {"version": "1.1.1", "tag": None}]
            reason31 = _relabel_window_reason(rr31, rr31_sha, head31, "1.1.0", "1.1.1")
            if reason31 is None or "head changelog release" not in reason31:
                failures.append("unit case R31: an old version recorded as a head changelog release "
                                "must refuse the relabel (got {!r})".format(reason31))
            head31b = [{"version": "1.0.5", "tag": "v1.0.5"}, {"version": "1.1.1", "tag": "v1.1.0"}]
            reason31b = _relabel_window_reason(rr31, rr31_sha, head31b, "1.1.0", "1.1.1")
            if reason31b is None or "head changelog tag version" not in reason31b:
                failures.append("unit case R31: an old version recorded as a head changelog tag "
                                "version must refuse the relabel (got {!r})".format(reason31b))

            # R32 the declaration digest is judged over the EXACT comparison-base blob bytes: against
            # a committed CRLF base changelog blob, a declaration hashing those exact bytes passes (a)
            # and one hashing the LF-normalized text is refused (b), so a base read that decodes text
            # and translates newlines flips BOTH outcomes and can satisfy neither. The fixture pins
            # core.autocrlf off so the committed blob really holds CRLF (asserted before the cases).
            crlf_base = base_log.replace("\n", "\r\n").encode("utf-8")
            crlf_head = head_log.replace("\n", "\r\n").encode("utf-8")
            rr32 = base_tmp / "relabel-crlf-base"
            _init(rr32)
            subprocess.run(["git", "-C", str(rr32), "config", "core.autocrlf", "false"],
                           check=True, capture_output=True, text=True)
            _root(rr32)
            (rr32 / "changelog.toml").write_bytes(crlf_base)
            _write_ledger(rr32)
            _write_register(rr32, reg_rows)
            _commit(rr32, "base: CRLF changelog blob")
            subprocess.run(["git", "-C", str(rr32), "tag", "v1.0.5", "HEAD"],
                           check=True, capture_output=True, text=True)
            blob32 = subprocess.run(["git", "-C", str(rr32), "show", "HEAD:changelog.toml"],
                                    check=True, capture_output=True).stdout
            if b"\r\n" not in blob32:
                failures.append("git case R32: the committed base changelog blob must hold CRLF "
                                "line endings for this case pair to discriminate")
            (rr32 / "changelog.toml").write_bytes(crlf_head)
            _write_register(rr32, reg_relabelled)
            _write_cut(rr32, _cut_text(digest=hashlib.sha256(crlf_base).hexdigest()))
            if _run_quiet(rr32, "HEAD") != 0:
                failures.append("git case R32a: relabel declaring the digest of the exact CRLF base "
                                "blob bytes expected exit 0")
            _write_cut(rr32, _cut_text(
                digest=hashlib.sha256(crlf_base.replace(b"\r\n", b"\n")).hexdigest()))
            if _run_quiet(rr32, "HEAD") != 1:
                failures.append("git case R32b: relabel declaring the digest of the LF-normalized "
                                "base changelog text expected exit 1")

            # R33 a head-side input is read from the working tree, so it must be a REGULAR file: a
            # symlinked declaration (a) or changelog (b), each pointing at bytes that would otherwise
            # be accepted, is fail-closed (exit 2), never followed. The base side is read from the
            # blob; a host where symlinks cannot be created records a NAMED NOT RUN failure.
            for label33, rel33 in (("a", ".aiqt/release-cut.toml"), ("b", "changelog.toml")):
                rr33 = _relabel_repo("relabel-symlink-" + label33)
                _write(rr33, head_log)
                _write_register(rr33, reg_relabelled)
                _write_cut(rr33, _cut_text())
                target33 = base_tmp / ("relabel-symlink-target-" + label33)
                victim33 = rr33 / rel33
                target33.write_bytes(victim33.read_bytes())
                victim33.unlink()
                try:
                    victim33.symlink_to(target33)
                except OSError as exc:
                    _not_run(failures, "git case R33{}".format(label33),
                             "cannot create a symlink here ({})".format(exc))
                    continue
                if _run_quiet(rr33, "HEAD") != 2:
                    failures.append("git case R33{}: a symlinked head {} expected fail-closed exit 2 "
                                    "(a non-regular head file is never followed)".format(
                                        label33, rel33))

            # R34 a symlink at ANY path component of a head input is refused, not only at the final
            # one: a symlinked .aiqt parent directory (a), release-order record (b) or id-history
            # register (c) is fail-closed (exit 2) naming the input whose read was refused (for the
            # parent, the first input read under it, the release-order record). Each case first runs
            # its regular-file control in the same repository (exit 0 with the relabel PASS line),
            # then moves the path outside the repository and links it back, so the link points at
            # exactly the bytes the control accepted. A host where symlinks cannot be created records
            # a NAMED NOT RUN failure.
            for label34, rel34, named34 in (("a", ".aiqt", RELEASES_REL),
                                            ("b", RELEASES_REL, RELEASES_REL),
                                            ("c", IDHISTORY_REL, IDHISTORY_REL)):
                rr34 = _relabel_repo("relabel-component-symlink-" + label34)
                _write(rr34, head_log)
                _write_register(rr34, reg_relabelled)
                _write_cut(rr34, _cut_text())
                rc34, out34, _err34 = _run_streams(rr34, "HEAD")
                if rc34 != 0 or "relabel 1.1.0 -> 1.1.1 on 2 [[born]] row(s)" not in out34:
                    failures.append("git case R34{} control: the relabel with a regular {} expected "
                                    "exit 0 and the relabel PASS line (got exit {})".format(
                                        label34, rel34, rc34))
                victim34 = rr34 / rel34
                target34 = base_tmp / ("relabel-component-target-" + label34)
                if victim34.is_dir():
                    shutil.copytree(victim34, target34, symlinks=True)
                    shutil.rmtree(victim34)
                else:
                    target34.write_bytes(victim34.read_bytes())
                    victim34.unlink()
                try:
                    victim34.symlink_to(target34, target_is_directory=target34.is_dir())
                except OSError as exc:
                    _not_run(failures, "git case R34{}".format(label34),
                             "cannot create a symlink here ({})".format(exc))
                    continue
                rc34, _out34, err34 = _run_streams(rr34, "HEAD")
                if rc34 != 2 or "cannot read {} at HEAD".format(named34) not in err34:
                    failures.append("git case R34{}: a symlinked head {} expected fail-closed exit 2 "
                                    "naming {} (a symlink at any path component is never followed; "
                                    "got exit {}, stderr {!r})".format(
                                        label34, rel34, named34, rc34, err34))

            # R35 a FIFO head changelog is refused before any read: the reader opens it non-blocking
            # and refuses it by the opened descriptor's type, so the run neither blocks on the open
            # nor consumes the pipe. Each variant runs the gate in a child through the bounded
            # launcher with a 30-second deadline (a reader that opens the FIFO blocking, or reads it,
            # hangs there, and the deadline turns that into this case's named failure): (a) no
            # writer, and (b) the child holds the FIFO open for writing with the bytes of the head
            # changelog the control accepts, which must still be unread in the pipe when the run
            # returns. The regular-file control runs first, in process; a host where a FIFO cannot
            # be created records a NAMED NOT RUN failure.
            rr35 = _relabel_repo("relabel-fifo")
            _write(rr35, head_log)
            _write_register(rr35, reg_relabelled)
            _write_cut(rr35, _cut_text())
            if _run_quiet(rr35, "HEAD") != 0:
                failures.append("git case R35 control: the relabel with a regular head changelog "
                                "expected exit 0")
            (rr35 / "changelog.toml").unlink()
            try:
                os.mkfifo(rr35 / "changelog.toml")
            except (OSError, AttributeError) as exc:
                _not_run(failures, "git case R35", "cannot create a FIFO here ({})".format(exc))
            else:
                fifo_child = (
                    "import io, os, sys\n"
                    "from contextlib import redirect_stderr, redirect_stdout\n"
                    "sys.path.insert(0, os.path.dirname(sys.argv[1]))\n"
                    "import check_version_monotonicity as gate\n"
                    "payload = sys.argv[3].encode('utf-8')\n"
                    "held = None\n"
                    "if payload:\n"
                    "    held = os.open(os.path.join(sys.argv[2], 'changelog.toml'),\n"
                    "                   os.O_RDWR | os.O_NONBLOCK)\n"
                    "try:\n"
                    "    if held is not None:\n"
                    "        os.write(held, payload)\n"
                    "    err = io.StringIO()\n"
                    "    with redirect_stdout(io.StringIO()), redirect_stderr(err):\n"
                    "        rc = gate.run(gate.Path(sys.argv[2]), 'HEAD')\n"
                    "    left = payload\n"
                    "    if held is not None:\n"
                    "        try:\n"
                    "            left = os.read(held, len(payload) + 1)\n"
                    "        except BlockingIOError:\n"
                    "            left = b''\n"
                    "finally:\n"
                    "    if held is not None:\n"
                    "        os.close(held)\n"
                    "text = err.getvalue()\n"
                    "named = ('cannot read changelog.toml at HEAD' in text\n"
                    "         and 'not a regular file' in text)\n"
                    "print('FIFO-CASE rc={} named={} unread={}'.format(rc, named, left == payload))\n")
                want35 = "FIFO-CASE rc=2 named=True unread=True"
                for label35, payload35 in (("a", ""), ("b", head_log)):
                    outcome = _run_bounded_child(["-c", fifo_child, str(Path(__file__).resolve()),
                                                  str(rr35), payload35], os.environ, timeout=30)
                    if (outcome.timed_out or outcome.survivors or outcome.returncode != 0
                            or _exact_line_count(outcome.stdout, want35) != 1):
                        failures.append("git case R35{}: a FIFO head changelog expected a prompt "
                                        "fail-closed exit 2 naming changelog.toml as not a regular "
                                        "file, with any written bytes left unread (want {!r}; got "
                                        "exit {}, timed out {}, survivors {}, stdout {!r})".format(
                                            label35, want35, outcome.returncode, outcome.timed_out,
                                            outcome.survivors, outcome.stdout))

            # R36 each head input a run accesses is read AT MOST ONCE, at its first access, through
            # the shared no-follow reader, and every later check uses the captured bytes. An
            # unwatched in-window relabel run (which reaches all four inputs) first records the
            # expected outcome (exit 0 with the relabel PASS line). A watched run of the same
            # repository then goes through a spy on this module's working_blob binding that records
            # each read and, as soon as an input's bytes are captured, overwrites that file in place
            # with bytes no parser accepts (an invalid UTF-8 byte, then an unclosed TOML table
            # header). The four inputs' device and inode identities are taken before the watched
            # run starts, and for its whole length, except while the shared reader itself runs,
            # six calls are wrapped: builtins.open, io.open, os.open, os.stat, os.lstat and
            # os.access. Each call made through one of those six names whose target resolves (by
            # device and inode) to an input is recorded, with whether that input was captured
            # yet, before the call runs, whatever it then returns or raises. Path.open,
            # read_bytes and read_text reach io.open, and Path.stat, exists and is_file,
            # os.path.exists and os.path.isfile reach os.stat, so those are recorded too. The
            # reads must be exactly the four inputs, once each, in first-access order, no access
            # outside the reader may be recorded, and the run must print exactly what the
            # unwatched run printed. A loader that bypasses the reader changes the reads. An
            # access through a wrapped call before the capture (an existence check ahead of the
            # reader, or a read ahead of it, which still sees the original bytes) is recorded,
            # and so is one after it, even when an existence check or a FileNotFoundError
            # fallback hides its result; a re-read after the capture whose bytes reach a check
            # gets the overwritten bytes, so the outcome changes as well. The watch records only
            # calls made through the six wrapped names on the inputs' identities as taken before
            # the run; any other route to an input is outside this check. Examples, not a full
            # list: an access from another process, through io.FileIO, _io.open, os.scandir,
            # os.listdir or the posix module's functions directly, through a function bound
            # before the wrap (from os import stat, or a default argument), through a descriptor
            # opened before the watched run started, or to a file put in an input's place after
            # its capture. Each watched run restores the inputs' original bytes when it ends.
            rr36 = _relabel_repo("relabel-read-once")
            _write(rr36, head_log)
            _write_register(rr36, reg_relabelled)
            _write_cut(rr36, _cut_text())
            control36 = _run_streams(rr36, "HEAD")
            if control36[0] != 0 or "relabel 1.1.0 -> 1.1.1 on 2 [[born]] row(s)" not in control36[1]:
                failures.append("git case R36 control: the unwatched relabel run expected exit 0 and "
                                "the relabel PASS line (got exit {})".format(control36[0]))
            want36 = ["changelog.toml", RELEASES_REL, IDHISTORY_REL, RELEASE_CUT_REL]
            originals36 = {rel: (rr36 / rel).read_bytes() for rel in want36}
            unparseable36 = b"\xff[[unclosed\n"
            real_reader36 = globals().get("working_blob")
            real_stat36, real_fstat36 = os.stat, os.fstat

            def _watched_run36(replaced=None):
                """One watched run of rr36: (reads, accesses outside the reader, outcome).

                replaced names an input whose capture writes the unparseable bytes to a new file
                moved into its place (a new inode) instead of overwriting it in place."""
                reads, accesses, captured, inputs = [], [], set(), {}
                for rel in want36:
                    found = real_stat36(rr36 / rel)
                    inputs[(found.st_dev, found.st_ino)] = rel

                def _identity(target, dir_fd):
                    try:
                        if isinstance(target, int):
                            found = real_fstat36(target)
                        else:
                            found = real_stat36(target, dir_fd=dir_fd)
                    except (OSError, TypeError, ValueError):
                        return None
                    return found.st_dev, found.st_ino

                def _watch(name, call, first):
                    def watched(*args, **kwargs):
                        rel = inputs.get(_identity(args[0] if args else kwargs.get(first),
                                                   kwargs.get("dir_fd")))
                        if rel is not None:
                            accesses.append((name, rel, "after capture" if rel in captured
                                             else "before capture"))
                        return call(*args, **kwargs)
                    return watched

                spied = ((builtins, "open", "file"), (io, "open", "file"), (os, "open", "path"),
                         (os, "stat", "path"), (os, "lstat", "path"), (os, "access", "path"))
                saved = [(owner, attr, getattr(owner, attr)) for owner, attr, _first in spied]
                watchers = [(owner, attr, _watch("{}.{}".format(owner.__name__, attr), real, first))
                            for (owner, attr, real), (_owner, _attr, first) in zip(saved, spied)]

                def _swap(bindings):
                    for owner, attr, value in bindings:
                        setattr(owner, attr, value)

                # The shared reader runs with the real functions bound: it is the one sanctioned
                # read (the reads record covers it), and it refuses to run when os.open is not
                # the function os.supports_dir_fd lists. The in-place overwrite keeps the
                # input's identity; were it ever to change, the watch would no longer see that
                # file, so a changed identity at capture is recorded as an access too.
                def _recording_reader(root, rel):
                    reads.append(rel)
                    _swap(saved)
                    try:
                        got = real_reader36(root, rel)
                        if got is not None:
                            if rel == replaced:
                                fresh = root / (rel + ".replacement")
                                fresh.write_bytes(unparseable36)
                                os.replace(fresh, root / rel)
                            else:
                                (root / rel).write_bytes(unparseable36)
                            found = real_stat36(root / rel)
                            if inputs.get((found.st_dev, found.st_ino)) != rel:
                                accesses.append(("identity changed", rel, "at capture"))
                            captured.add(rel)
                    finally:
                        _swap(watchers)
                    return got

                globals()["working_blob"] = _recording_reader
                try:
                    _swap(watchers)
                    outcome = _run_streams(rr36, "HEAD")
                except OSError as exc:
                    outcome = "raised {}: {}".format(type(exc).__name__, exc)
                finally:
                    _swap(saved)
                    globals()["working_blob"] = real_reader36
                    for rel, data in originals36.items():
                        (rr36 / rel).write_bytes(data)
                return reads, accesses, outcome

            reads36, accesses36, watched36 = _watched_run36()
            if watched36 != control36 or reads36 != want36 or accesses36:
                failures.append("git case R36: an in-window relabel run expected each head input read "
                                "once through the shared reader, in the order {!r}, no wrapped open, "
                                "stat or access call on an input outside that reader before or after "
                                "its capture, and the same outcome as the unwatched run once each "
                                "input was overwritten with unparseable bytes after its capture (got "
                                "reads {!r}, accesses {!r}, outcome {!r}, unwatched {!r})".format(
                                    want36, reads36, accesses36, watched36, control36))

            # R36 identity guard: for each input in turn, the capture writes the unparseable bytes
            # to a new file moved into that input's place instead of overwriting it in place, so
            # the watch no longer sees that input's file. The run must record exactly one changed
            # identity at capture, naming that input, with the same reads and outcome as the
            # unwatched run: the guard is the only thing that tells this run from a clean one.
            for rel36 in want36:
                reads36r, accesses36r, watched36r = _watched_run36(replaced=rel36)
                if (accesses36r != [("identity changed", rel36, "at capture")]
                        or reads36r != want36 or watched36r != control36):
                    failures.append("git case R36 identity guard: a capture that moved a new file "
                                    "into {}'s place expected exactly one changed identity recorded "
                                    "for it, the reads {!r} and the unwatched run's outcome (got "
                                    "accesses {!r}, reads {!r}, outcome {!r})".format(
                                        rel36, want36, accesses36r, reads36r, watched36r))

            # R36 discrimination: the watch must flag an access by path outside the reader, before
            # or after the capture, through each wrapped call. For each input in turn,
            # _HeadInputs.raw is replaced by a mutant that, for that input only, reads the path
            # before the reader runs and returns those bytes, checks Path.exists or os.access
            # before the reader runs, re-reads the path after the capture when Path.exists is
            # true, re-reads it unless that raises FileNotFoundError, re-reads it through the
            # built-in open or through os.open, or calls os.lstat on it after the capture. Each
            # mutant run must record accesses naming only that input, on the mutant's side of the
            # capture, through every call the mutant makes (so each of the six wrappers is
            # required by some mutant), and must change the outcome exactly when the mutant's
            # re-read bytes reach a check: an access before the capture sees the original bytes,
            # and an lstat or os.access reads none, so only the watch catches those.
            real_raw36 = _HeadInputs.raw

            def _rereading_raw(kind, target):
                def raw(self, rel):
                    if rel != target:
                        return real_raw36(self, rel)
                    path = self.root / rel
                    if kind == "pre-capture read":
                        if rel in self._raw:
                            return real_raw36(self, rel)
                        early = path.read_bytes()
                        real_raw36(self, rel)
                        return early
                    if kind == "pre-capture exists":
                        if rel not in self._raw and not path.exists():
                            return None
                        return real_raw36(self, rel)
                    if kind == "pre-capture os.access":
                        if rel not in self._raw and not os.access(path, os.F_OK):
                            return None
                        return real_raw36(self, rel)
                    got = real_raw36(self, rel)
                    if kind == "exists-guarded":
                        return path.read_bytes() if path.exists() else got
                    if kind == "FileNotFoundError-fallback":
                        try:
                            return path.read_bytes()
                        except FileNotFoundError:
                            return got
                    if kind == "builtins.open re-read":
                        with builtins.open(path, "rb") as fh:
                            return fh.read()
                    if kind == "os.open re-read":
                        fd = os.open(path, os.O_RDONLY)
                        try:
                            return os.read(fd, len(got) + 1)
                        finally:
                            os.close(fd)
                    os.lstat(path)
                    return got
                return raw

            for kind36, side36, calls36, changes36 in (
                    ("pre-capture read", "before capture", ("io.open",), False),
                    ("pre-capture exists", "before capture", ("os.stat",), False),
                    ("pre-capture os.access", "before capture", ("os.access",), False),
                    ("exists-guarded", "after capture", ("os.stat", "io.open"), True),
                    ("FileNotFoundError-fallback", "after capture", ("io.open",), True),
                    ("builtins.open re-read", "after capture", ("builtins.open",), True),
                    ("os.open re-read", "after capture", ("os.open",), True),
                    ("os.lstat probe", "after capture", ("os.lstat",), False)):
                for rel36 in want36:
                    _HeadInputs.raw = _rereading_raw(kind36, rel36)
                    try:
                        _reads36, accesses36m, watched36m = _watched_run36()
                    finally:
                        _HeadInputs.raw = real_raw36
                    if (not accesses36m
                            or any(entry[1:] != (rel36, side36) for entry in accesses36m)
                            or not set(calls36) <= {entry[0] for entry in accesses36m}
                            or (watched36m != control36) != changes36):
                        failures.append("git case R36 discrimination: the {} mutant for {} expected "
                                        "accesses naming only it {}, through {}, and {} outcome (got "
                                        "accesses {!r}, outcome {!r})".format(
                                            kind36, rel36, side36, " and ".join(calls36),
                                            "a changed" if changes36 else "the unwatched run's",
                                            accesses36m, watched36m))

            # R37 a read that runs out of memory is fail-closed (exit 2) naming the input, as a parse
            # that runs out of memory is, and never escapes run(): a spy on this module's
            # working_blob binding raises MemoryError for one input of an in-window relabel run
            # (which reaches all four inputs, as its control shows), each input in turn.
            rr37 = _relabel_repo("relabel-read-memoryerror")
            _write(rr37, head_log)
            _write_register(rr37, reg_relabelled)
            _write_cut(rr37, _cut_text())
            if _run_quiet(rr37, "HEAD") != 0:
                failures.append("git case R37 control: the relabel with every head input readable "
                                "expected exit 0")
            real_reader37 = globals().get("working_blob")
            for rel37 in ("changelog.toml", RELEASES_REL, IDHISTORY_REL, RELEASE_CUT_REL):

                def _exhausted_reader(root, rel, _target=rel37):
                    if rel == _target:
                        raise MemoryError("injected while reading " + rel)
                    return real_reader37(root, rel)

                globals()["working_blob"] = _exhausted_reader
                try:
                    rc37, _out37, err37 = _run_streams(rr37, "HEAD")
                except MemoryError as exc:
                    rc37, err37 = "escaped run()", "MemoryError: {}".format(exc)
                finally:
                    globals()["working_blob"] = real_reader37
                if rc37 != 2 or "cannot read {} at HEAD: out of memory".format(rel37) not in err37:
                    failures.append("git case R37: a MemoryError reading the head {} expected "
                                    "fail-closed exit 2 naming it (got exit {}, stderr {!r})".format(
                                        rel37, rc37, err37))
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if _vectors and os.environ.get(_SUPPRESS_VECTORS_ENV) == "1":
        # An inherited marker must never skip the vectors silently: record ONE named NOT RUN
        # failure so a marked run exits 1 (a vector child carries the marker and expects its own
        # injected failure); the full "SELF-TEST PASS:" line below is printed only by an
        # unmarked run whose vectors RAN, and a no-vectors nested run prints the distinct
        # no-vectors note instead.
        _not_run(failures, "the red-on-revert NOT RUN and entry vectors",
                 "suppressed by {} (a vector child, never a full run)".format(
                     _SUPPRESS_VECTORS_ENV))
    elif _vectors:
        # A fresh-process vector child carries _SUPPRESS_VECTORS_ENV, so a child whose injected
        # setup failure ever stopped failing still could not spawn children of its own (the
        # marked gate above fails it first, and the launcher's _CHILD_DEPTH_ENV refusal at the
        # top of _not_run_vectors bounds even a route around this gate at depth 1); every
        # fresh Python VECTOR-ENTRY child runs through the ONE bounded launcher: its own
        # process group, killed BEFORE reaping on timeout, with every wait and drain
        # bounded (the parent's direct git launches and the launcher-timeout child's plain
        # descendant stay outside that launcher, as its docstring states).
        _not_run_vectors(failures)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    pass_head = "SELF-TEST PASS:" if _vectors else _NO_VECTORS_PASS_NOTE + ":"
    print("{} prefix identity (M1), no-decrease (M2), tag name/ceiling logic, the "
          "genesis predicate and tag-preservation logic, the _parse fullmatch regression (4e), the "
          "full-row register prefix logic (append/edit/delete/empty/identical), and the git-level "
          "history, tag, release-order/id-history append-only, genesis-exemption, "
          "tag-preservation, and one-time born-release relabel cases all hold; an unavailable git or "
          "temp directory is a NOT RUN failure "
          "(nonzero exit), never a PASS (PARTIAL)".format(pass_head))
    return 0


def _parse_args(argv):
    base = None
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--base" and i + 1 < len(argv):
            base = argv[i + 1]
            i += 2
        elif argv[i] == "--self-test":
            self_test = True
            i += 1
        else:
            print("usage: check_version_monotonicity.py [--base REF] | --self-test", file=sys.stderr)
            return None
    return (base, self_test)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    base, self_test = parsed
    if self_test:
        return self_test_main()
    return run(repo_root(), base)


if __name__ == "__main__":
    sys.exit(main())
