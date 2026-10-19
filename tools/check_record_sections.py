#!/usr/bin/env python3
"""Post-merge record-section preservation gate. Offline, stdlib only, fail-closed.

An adopter declares Markdown record files in `.aiqt/record-sections.toml`. For each declared file, the
configured regular expression selects record headings from exact column-zero `## ` lines. The gate finds
the unique merge-base of the pre-merge target and the branch tip, collects every selected heading that is
present on the branch tip but absent at that merge-base, and requires the same heading in the post-merge
commit. A branch-owned section may instead carry the configured opt-out marker as an exact line; that is an
explicit declaration that the section was superseded.

The default post-merge ref is HEAD. With no --branch-ref, HEAD must be an ordinary two-parent merge commit:
its first parent is the pre-merge target and its second parent is the branch tip. For a squash/rebase merge,
or any other integration whose post-merge commit does not retain the branch parent, the caller must keep the
branch ref available and pass --branch-ref. When the post-merge commit does retain two parents, its second
parent is the observable branch tip, so an explicit --branch-ref must name that second parent; a --branch-ref
that disagrees with it is fail-closed, because pointing it at the first parent would compare against the wrong
tip and mask a drop. A post-merge commit with THREE OR MORE parents (an octopus merge) is not evaluable and
fails closed, with or without --branch-ref: this gate validates one branch integration per merge, so checking
a single named branch of an octopus would leave a section dropped from another parent unchecked, and requiring
--branch-ref to name any non-first parent is not enough because a section-free second parent is itself such a
parent. Integrate through two-parent merges, or run the gate on the two-parent merge that landed each branch.
--base-ref is optional; absent it, the gate requires exactly one
merge-base between the post-merge commit's first parent and the branch tip. An explicit --base-ref is accepted
only when the target and branch have a unique merge-base and the supplied ref names it; if they have more than
one merge-base, or the ref is only a common ancestor, it is rejected (two merge-bases can disagree on a
section, so accepting one would let a real drop read as pre-existing).

Config schema (version 1, unchanged):

  schema-version = 1
  opt-out-marker = "<!-- aiqt-record-section: superseded -->"

  [[record]]
  path = "docs/decision-log.md"
  heading-pattern = '^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'

The marker is recognized only as a whole line inside the branch-owned section it excuses. A global marker,
a marker in another section, or a marker added only to the post-merge file does not excuse a missing section.

Config schema (version 2) adds a [[table]] record kind for append-only tables whose rows are keyed on
declared columns. A version-1 config keeps exactly the version-1 behaviour above. In a version-2 config the
[[record]] kind (with its opt-out-marker) is optional, [[table]] is optional, and at least one of the two
kinds must be declared; [[record]] and opt-out-marker come together:

  schema-version = 2

  [[table]]
  path = "docs/done-log.md"
  format = "markdown"                    # or "toml" with array = "<top-level array name>"
  header = "| id | pr | closed | note |" # exact header line; table = header, delimiter row, |-lines
  key = ["id", "pr"]                     # row identity; must be unique per snapshot
  edit = "frozen"                        # or "key-stable": non-key cells may be corrected in place

Row identity. For a Markdown table, exactly one line of the file must match `header` cell for cell after
trimming surrounding cell whitespace (so a re-padded header still locates the table) and be followed by a
GFM delimiter row; a file carries at most one declared table (a duplicate table path in the config is
refused); the table's rows are the consecutive following lines that start with `|`, and each row line must
also end with an unescaped `|` (a GFM-style row without the trailing pipe is refused); cells are split
on unescaped `|` and trimmed of surrounding whitespace; the cell count must equal the header's cell count;
lines are split on LF only, and a CR inside a table line or in a line matching the declared header in ANY
compared snapshot (post-merge, first parent, branch tip, or merge-base) is exit 2, so a CRLF table fails
closed rather than reading as absent. For a TOML table, rows are the declared
top-level array, which must be an array of tables whose key fields are present strings or integers, compared
by parsed value so key order and layout do not matter. A row's identity is the tuple of its `key` columns; a
duplicate key in any snapshot is exit 2, matching the duplicate-heading rule.

Table invariants. R1 (base-row preservation): every key present at the POST-MERGE COMMIT'S FIRST PARENT (the
pre-merge target, never the merge-base, which usually predates a row the target gained after the fork) is
present in the post-merge commit; the table declarations for R1 come from that first parent's config at the
same repo-relative path TOGETHER WITH the working config, WHATEVER schema version the working config
declares, so one merge cannot remove a table's declaration and drop its rows at the same time, and a
schema downgrade to version 1 in the same merge keeps the first parent's table obligations in force (a
version-1 working config whose first parent declared no tables behaves exactly as before). A first-parent
config that is present but is neither a valid schema-version-1 nor schema-version-2 shape fails closed,
because the declarations it may carry cannot be known. R1 needs only the post-merge commit and its first
parent, so a version-2 config declaring only tables is evaluable on a single-parent (squash) commit with
no --branch-ref and no --base-ref (an explicit --base-ref cannot be validated without a branch graph, so
it fails closed there); [[record]] entries keep the version-1 graph requirements. R2 (the row analogue of
the heading leg): every key on the branch tip that is absent at the merge-base is present in the post-merge
commit. R3 (edit = "frozen" only): a key present at both the first parent and the post-merge commit with
any cell changed is a finding, and a branch-owned key surviving into the post-merge commit with any cell
changed from its branch-tip content is a finding too. Cells and TOML values compare by parsed value with
type-aware equality: an integer never equals a boolean or a float (so such a substitution is an edit), and
an unchanged NaN is not an edit. Reordering, appending or inserting rows anywhere, re-padding or alignment
changes (header line included), TOML
reformatting, and (in key-stable mode) non-key cell edits are all allowed. Rows have no deletion opt-out: a
supersession is a new row or, in key-stable mode, a non-key cell edit. A whole declared file deleted while
it existed at the compared base counts as every row it held disappearing (exit 1 when it held any); a
declared table absent from a
file that still exists post-merge is ambiguous and fails closed (exit 2).

Exit convention:
  0  every in-scope branch-owned heading is present after the merge, or explicitly opted out, and every
     in-scope table row obligation (R1, R2, R3) holds; also NOT
     APPLICABLE when the default config is absent AND was not present at the pre-merge target, because this
     checkout never adopted the gate
  1  at least one in-scope branch-owned heading was dropped from the post-merge commit, a declared table
     row key disappeared (R1 or R2), a frozen row was edited (R3), or a declared table file that existed
     at the compared base was deleted
  2  malformed or unreadable config/input, a missing, duplicate or misshapen declared table, a duplicate
     row key in any snapshot, a missing key column, a CR in a table line or in a header-matching line of
     any compared snapshot, an explicit --base-ref on a single-parent commit with no branch ref, a
     first-parent config that is neither a valid version-1 nor version-2 shape, a declared TOML array that
     is not an array of tables or carries a non-scalar key value, a version-2 config with unknown keys,
     a config path whose canonicalization net-redirects through a
     symlink (its real target differs from the literal name) or that resolves outside the repository root,
     an invalid config path (for example one carrying an embedded NUL byte), a default config the merge
     itself removed, an
     unresolvable ref (including an unresolvable post-merge ref while the removal check runs), a missing
     declared branch record, duplicate selected headings, a non-unique merge-base on either the default or the
     explicit-base path, an explicit --base-ref that is not that unique merge-base, a branch ref that cannot be
     inferred, an explicit --branch-ref that disagrees with the observable second parent of a two-parent
     post-merge commit, or an octopus (three-or-more-parent) post-merge commit

RESIDUAL, table leg. Key-stable mode lets a row's content be gutted while its key survives. Only declared
tables and the stated grammar are covered; HTML tables and lists are not, and the grammar is textual, so a
table that still parses inside an HTML comment or other non-rendering wrapper is counted even though it is
not rendered. Archive rotation of rows to
another file reads as disappearance, an over-deny, since no archive path list exists. Changing a declared
table's header line (beyond re-padding) is refused while the first parent's declaration is in force, an
over-deny: a column rename or addition cannot land through a gated merge that keeps the old declaration in
scope. A carriage return in a header-matching line of any compared snapshot fails closed, an over-deny when
the loss it blocks would have been benign line-ending normalization. A file carries at most one declared
table. The config is trusted
adopter input: reading the first parent's config closes declaration narrowing within the same merge
(including a schema downgrade), but not
narrowing landed in an earlier, separate commit. A single-parent post-merge commit is compared only against
its first parent: that checks a squash endpoint to endpoint, but a multi-commit rebase or fast-forward
integration is checked only across its final commit, so a drop in an earlier commit of such an integration
passes unless the gate runs per commit or on the two-parent merge. A pull-request
check evaluates the test merge, so if the target moves afterwards the live run on the target is still
needed. The gate detects drops; it does not prevent a resolver from running. And it does not prove that a
row's claim is true; that semantic burden stays with the rules the records serve.

RESIDUAL. This gate proves heading preservation only for newly introduced headings selected by the adopter's
patterns. It does not compare section bodies, cover edits to a heading already present at merge-base, detect a
renamed heading as preserved, or prove that an opt-out marker represents a valid supersession. A record path
or heading outside the config is outside its surface. An explicit --base-ref is accepted only when the target
and branch have a unique merge-base and the supplied ref names it; a non-unique merge-base fails closed on both
the default and the explicit-base paths, so an explicit base cannot mask a drop by naming one of several
merge-bases or a mere common ancestor. A config path is judged on its honestly-canonicalized (realpath) form,
so a path whose canonicalization NET-REDIRECTS through a symlink (its real target differs from the literal
name) is fail-closed rather than followed, whatever `..`, ENOENT, or lexical-prefix form it takes; a symlink
crossed but cancelled back to the same literal path (real == lexical) is allowed and loads exactly the
literally-named, reviewed file, since it masks nothing and any masking would additionally need config
narrowing, which is out of this gate's threat model; an octopus (three-or-more-parent) merge is not evaluable
and fails closed;
and an unresolvable post-merge ref fails closed rather than reading as never-adopted.

The decision-critical parent list, the target and branch tip that fix which record sections the branch
owns, is read from the raw commit object (`git cat-file commit`), which reads the object's own bytes rather
than a graph view. It is therefore immune to all three local parent-rewrite or graph-cache mechanisms:
refs/replace/* (also disabled by --no-replace-objects), the legacy .git/info/grafts file (which
--no-replace-objects does NOT cover), and the commit-graph performance cache (which cat-file does not
consult). Neither a replacement ref, a grafts line, nor a crafted commit-graph can rewrite the parents the
gate derives the owned-section set from. The gate's ancestry sanity checks (merge-base --all,
merge-base --is-ancestor, which confirm the base is a common ancestor of target and branch) do traverse the
graph; they run with --no-replace-objects and GIT_GRAFT_FILE pinned to an empty file, so they are
replace- and graft-immune, and a mismatched commit-graph makes such a query error out and the gate fail
closed (exit 2) rather than read a masked result. This closes the parent-rewrite class the owned-section
computation depends on.

The config is trusted adopter input to this preservation check, so the gate does not defend the config's own
integrity: rewriting the adopter's config so its heading-pattern selects fewer or no headings (semantic
narrowing that makes real sections stop matching) is a change to that trusted config, not a dropped section
this gate detects. Guarding the config against adversarial narrowing is out of this gate's threat model; it is
the config-integrity surface's concern, not this one's.

Those boundaries are deliberate; the PASS result prints only the scope note that it compares selected new
headings and not bodies or pre-existing headings, so the other boundaries above live here rather than in that
line. Missing or ambiguous inputs inside the declared surface fail closed.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_record_sections.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import io
import math
import os
import re
import subprocess
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_record_sections.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

CONFIG_REL = ".aiqt/record-sections.toml"
OID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
TOP_KEYS = {"schema-version", "opt-out-marker", "record"}
ROW_KEYS = {"path", "heading-pattern"}
V2_TOP_KEYS = {"schema-version", "opt-out-marker", "record", "table"}
TABLE_KEYS_MARKDOWN = {"path", "format", "header", "key", "edit"}
TABLE_KEYS_TOML = {"path", "format", "array", "key", "edit"}
DELIMITER_CELL_RE = re.compile(r":?-+:?")


class GateError(Exception):
    """An input the gate needs cannot be read or interpreted. The caller reports exit 2."""


@dataclass(frozen=True)
class RecordSpec:
    path: str
    heading_pattern: re.Pattern


@dataclass(frozen=True)
class TableSpec:
    """One declared append-only table. header/header_cells/key_positions are markdown-only; array is
    toml-only; the unused fields hold their empty defaults so the spec stays hashable for deduplication
    when the first parent's config and the working config declare the same table."""
    path: str
    table_format: str
    header: str
    header_cells: tuple
    key: tuple
    key_positions: tuple
    edit: str
    array: str


@dataclass(frozen=True)
class Config:
    """A loaded adopter config: schema version, the heading leg (marker plus record specs, version-1
    semantics), and the version-2 table leg. A version-1 config always has tables == ()."""
    version: int
    marker: object
    records: tuple
    tables: tuple


def _clean_env():
    """Return os.environ with the entire GIT_ family removed, then GIT_GRAFT_FILE pinned empty.

    Ambient GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_COMMON_DIR, and every other
    GIT_-prefixed variable can redirect git's whole view, including -C and --show-toplevel, to an
    attacker-controlled decoy repository, so the gate strips the family wholesale before every git call
    (allowlist stance). It then re-applies exactly one variable it sets itself: GIT_GRAFT_FILE points at
    os.devnull (a guaranteed-empty file), so no graft is read from any location. The on-disk
    .git/info/grafts parent-rewrite mechanism is NOT disabled by --no-replace-objects (that flag covers
    only refs/replace/*); git still honours info/grafts for the graph views merge-base --all and
    merge-base --is-ancestor, which could otherwise let a graft that rewrites a merge's second parent to
    a section-free tip make a real dropped section read as pre-existing. GIT_GRAFT_FILE overriding the
    graft location to an empty file neutralizes grafts repository-wide for every git call the gate makes;
    the parent read additionally uses the raw commit object (see _parents), which is graft-immune on its
    own, so the two layers close the whole parent-rewrite class.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_GRAFT_FILE"] = os.devnull
    return env


def _git(root, args):
    """Run git without a shell and return stdout bytes. Any invocation failure is fail-closed.

    Every invocation carries --no-replace-objects, so a local git replacement ref (refs/replace/*)
    cannot substitute a crafted object for a commit the gate reads, and runs under _clean_env with
    GIT_GRAFT_FILE pinned to an empty file, so the legacy .git/info/grafts parent-rewrite mechanism is
    neutralized too (--no-replace-objects does NOT cover grafts). Without both, `git replace <post>
    <obj-with-benign-parents>` or a `.git/info/grafts` line rewrites the parent list the graph views
    (`show -s --format=%P`, `merge-base`) report, letting a real merge that dropped a section
    masquerade as one whose branch tip owns no new section (exit 0). The parent read itself uses the
    raw commit object (_parents), which is graft- and replace-immune independently of these flags.
    """
    try:
        proc = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30, env=_clean_env())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise GateError("git invocation failed ({})".format(type(exc).__name__))
    if proc.returncode != 0:
        command = args[0] if args else "?"
        raise GateError("git {} exited non-zero ({})".format(command, proc.returncode))
    return proc.stdout


def _text(data, where):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise GateError("{} is not UTF-8".format(where))


def _repository_root(root):
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise GateError("cannot resolve repository root {} ({})".format(root, exc))
    top = _text(_git(resolved, ["rev-parse", "--show-toplevel"]), "git repository root").strip()
    try:
        observed = Path(top).resolve(strict=True)
    except OSError as exc:
        raise GateError("cannot resolve git's repository root {} ({})".format(top, exc))
    if observed != resolved:
        raise GateError("--root must name the repository top level, observed {}".format(observed))
    return resolved


def _contained_config_path(root, value):
    """Return the honestly-canonicalized config path, fail-closed on a net-redirecting symlink or escape.

    The config path is judged on its REAL, fully-resolved form, never on a lexical guess. The round-2/3
    guard walked the raw path components with lstat and asked relative_to(root) lexically; both are
    bypassable, because a nonexistent leading component plus `..` (`x/../ln/...`) makes each incremental
    lstat hit ENOENT (so is_symlink returns False) while resolve() still follows the symlink, and a
    lexically non-contained absolute form (one that escapes the root's textual prefix through `..` or a
    process-relative alias such as the current-directory link, then resolves back inside) trips the
    ValueError early-return yet still resolves into the root. Per the project's symlink-resolution rule
    we reject on the resolved path instead: canonicalize with realpath (which follows every symlink and
    collapses `.`/`..`), require the REAL target to sit under the (already canonical) root, AND reject a
    NET-REDIRECTING canonicalization. The path net-redirected exactly when the honest realpath differs
    from the purely lexical normalization of the same absolute candidate (both collapse
    `.`/`..`/separators and a leading `//` identically, and only realpath additionally follows links),
    so any symlink crossing that changes the resolved target is rejected regardless of `..`, ENOENT, or
    lexical-prefix form. A symlink crossed but cancelled back to the same literal path (real == lexical,
    for example `link/../real` where resolving `link` and then the following `..` lands on the same
    place) is NOT rejected: it loads exactly the literally-named, reviewed file, so it masks nothing.
    Masking a drop through such a path would additionally require narrowing the config it lands on, which
    is the config-integrity surface's concern and out of this gate's threat model.
    """
    # candidate is already absolute (root is canonical), so neither call depends on the process cwd.
    # Pass the RAW candidate string, never a pre-collapsed one: os.path.abspath/normpath would fold
    # `..` lexically BEFORE realpath runs, which would neutralize a `symlink/..` ordering (a crossed
    # symlink whose target then climbs back under root) and hide the crossing. realpath honours POSIX
    # order (resolve each symlink, THEN apply the following `..`), so it and the purely lexical normpath
    # diverge whenever, and only whenever, a symlink NET-REDIRECTED the path (a crossing whose real
    # target differs from the literal name); a symlink crossed but cancelled back to the same literal
    # path leaves real == lexical and is allowed, loading exactly the literally-named file. An invalid
    # path (for example an embedded NUL byte that reaches realpath's lstat) is fail-closed here rather
    # than raising, since this is a validation surface.
    try:
        candidate = Path(value) if Path(value).is_absolute() else root / value
        text = str(candidate)
        real_str = os.path.realpath(text)
        # os.path.normpath preserves a POSIX-defined leading `//` that realpath collapses to `/`; that
        # difference is not a symlink crossing, so fold a leading run of two or more slashes to one on
        # the lexical form, keeping real != lexical a pure net-redirection signal (without it, a
        # legitimate `//abs/.../record-sections.toml` would be wrongly rejected).
        lexical_str = re.sub(r"^/{2,}", "/", os.path.normpath(text))
    except ValueError as exc:
        raise GateError("config path is not a valid filesystem path ({})".format(type(exc).__name__))
    real = Path(real_str)
    try:
        real.relative_to(root)
    except ValueError:
        raise GateError("config path must resolve inside the repository root")
    if real_str != lexical_str:
        raise GateError(
            "config path {} crosses a symlink (resolves to {}); a symlinked config path is not "
            "followed".format(value, real))
    return real


def _config_is_symlink(path):
    """True when path itself is a symbolic link. An I/O error resolving it is fail-closed as a symlink."""
    try:
        return path.is_symlink()
    except OSError:
        return True


def _record_path(value, where):
    if not isinstance(value, str) or not value:
        raise GateError("{}: path must be a non-empty string".format(where))
    if any(ord(ch) < 32 or ch == "\\" or ch == ":" for ch in value):
        raise GateError("{}: path contains a control character, backslash, or colon".format(where))
    path = PurePosixPath(value)
    if path.is_absolute() or path == PurePosixPath(".") or any(
            part in ("", ".", "..") for part in path.parts):
        raise GateError("{}: path must be a normalized repo-relative POSIX path".format(where))
    if path.parts[0] == ".git":
        raise GateError("{}: .git is not a record surface".format(where))
    return str(path)


def load_config(path, allow_absent=False):
    """Return (marker, record specs), or None only for an absent default adopter config.

    A config path that is a symlink is fail-closed here too, so a caller that reaches load_config with an
    unresolved path (the self-test, or any direct caller) cannot bypass the symlink guard: only a
    genuinely absent, not-a-symlink default config yields None (NOT APPLICABLE).
    """
    if _config_is_symlink(path):
        raise GateError("config path {} is a symlink; a symlinked config is not followed".format(path))
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if allow_absent:
            return None
        raise GateError("required config {} is absent".format(path))
    except OSError as exc:
        raise GateError("cannot read config {} ({})".format(path, exc))
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("cannot parse config {} ({})".format(path, exc))
    if isinstance(data, dict) and type(data.get("schema-version")) is int and data["schema-version"] == 2:
        return _load_config_v2(data, path)
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        raise GateError("{}: top-level keys must be exactly {}".format(path, sorted(TOP_KEYS)))
    version = data.get("schema-version")
    if type(version) is not int or version != 1:
        raise GateError("{}: schema-version must be integer 1".format(path))
    marker = _load_marker(data, path)
    specs = _load_record_specs(data.get("record"), path)
    return Config(1, marker, specs, ())


def _load_marker(data, path):
    marker = data.get("opt-out-marker")
    if (not isinstance(marker, str) or not marker or marker != marker.strip()
            or "\n" in marker or "\r" in marker or marker.startswith("## ")):
        raise GateError("{}: opt-out-marker must be one non-heading line with no surrounding whitespace"
                        .format(path))
    return marker


def _load_record_specs(rows, path):
    if not isinstance(rows, list) or not rows:
        raise GateError("{}: at least one [[record]] table is required".format(path))
    specs = []
    seen = set()
    for number, row in enumerate(rows, 1):
        where = "{} [[record]] #{}".format(path, number)
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise GateError("{}: keys must be exactly {}".format(where, sorted(ROW_KEYS)))
        record_path = _record_path(row.get("path"), where)
        if record_path in seen:
            raise GateError("{}: duplicate record path {}".format(where, record_path))
        seen.add(record_path)
        pattern = row.get("heading-pattern")
        if not isinstance(pattern, str) or not pattern:
            raise GateError("{}: heading-pattern must be a non-empty string".format(where))
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise GateError("{}: heading-pattern is invalid ({})".format(where, exc))
        if compiled.fullmatch(""):
            raise GateError("{}: heading-pattern must not match the empty string".format(where))
        specs.append(RecordSpec(record_path, compiled))
    return tuple(specs)


def _check_v2_top_keys(data, path):
    """Refuse unknown top-level keys in a version-2 config: a misspelt or extra key could silently carry
    an intended declaration the gate then never enforces, so it is ambiguous input, not a pass."""
    unknown = set(data) - V2_TOP_KEYS
    if unknown:
        raise GateError("{}: unknown top-level keys {} in a schema-version 2 config"
                        .format(path, sorted(unknown)))


def _load_config_v2(data, path):
    _check_v2_top_keys(data, path)
    if ("record" in data) != ("opt-out-marker" in data):
        raise GateError("{}: [[record]] entries and opt-out-marker are declared together or not at all"
                        .format(path))
    marker = _load_marker(data, path) if "record" in data else None
    records = _load_record_specs(data.get("record"), path) if "record" in data else ()
    tables = _load_table_specs(data.get("table"), path) if "table" in data else ()
    if not records and not tables:
        raise GateError("{}: at least one [[record]] or [[table]] table is required".format(path))
    return Config(2, marker, records, tables)


def _load_table_specs(rows, path):
    if not isinstance(rows, list) or not rows:
        raise GateError("{}: table must be a non-empty array of [[table]] tables".format(path))
    specs = []
    seen = set()
    for number, row in enumerate(rows, 1):
        where = "{} [[table]] #{}".format(path, number)
        if not isinstance(row, dict):
            raise GateError("{}: each [[table]] entry must be a table".format(where))
        table_format = row.get("format")
        if table_format not in ("markdown", "toml"):
            raise GateError("{}: format must be 'markdown' or 'toml'".format(where))
        wanted = TABLE_KEYS_MARKDOWN if table_format == "markdown" else TABLE_KEYS_TOML
        if set(row) != wanted:
            raise GateError("{}: keys must be exactly {} for format {}"
                            .format(where, sorted(wanted), table_format))
        table_path = _record_path(row.get("path"), where)
        if table_path in seen:
            raise GateError("{}: duplicate table path {}".format(where, table_path))
        seen.add(table_path)
        key = row.get("key")
        if (not isinstance(key, list) or not key
                or any(not isinstance(name, str) or not name or name != name.strip()
                       or "\n" in name or "\r" in name or "|" in name for name in key)
                or len(set(key)) != len(key)):
            raise GateError("{}: key must be a non-empty list of unique, non-empty, trimmed column names"
                            .format(where))
        edit = row.get("edit")
        if edit not in ("frozen", "key-stable"):
            raise GateError("{}: edit must be 'frozen' or 'key-stable'".format(where))
        if table_format == "markdown":
            header = row.get("header")
            if (not isinstance(header, str) or "\n" in header or "\r" in header
                    or not header.startswith("|")):
                raise GateError("{}: header must be one |-prefixed line".format(where))
            header_cells = _normalize_cells(_split_cells(header, where + " header"))
            positions = _key_positions(header_cells, tuple(key), where)
            specs.append(TableSpec(table_path, "markdown", header, header_cells,
                                   tuple(key), positions, edit, ""))
        else:
            array = row.get("array")
            if not isinstance(array, str) or not array or "\n" in array or "\r" in array:
                raise GateError("{}: array must name one top-level TOML array".format(where))
            specs.append(TableSpec(table_path, "toml", "", (), tuple(key), (), edit, array))
    return tuple(specs)


def _resolve_commit(root, ref, label):
    if not isinstance(ref, str) or not ref or any(ord(ch) < 32 for ch in ref):
        raise GateError("{} must be a non-empty ref with no control characters".format(label))
    out = _text(_git(root, ["rev-parse", "--verify", "--end-of-options",
                            "{}^{{commit}}".format(ref)]), label).splitlines()
    if len(out) != 1 or not OID_RE.fullmatch(out[0]):
        raise GateError("{} did not resolve to exactly one commit object".format(label))
    return out[0]


def _parents(root, commit):
    """Return a commit's TRUE parent oids, parsed from the raw commit object.

    Reads `git cat-file commit <oid>` and takes the `parent ` header lines the object was actually
    written with. The raw object is immune to BOTH parent-rewrite mechanisms: refs/replace/* (already
    disabled by --no-replace-objects) and the legacy .git/info/grafts file, which git still honours for
    the graph views `show -s --format=%P` and `log --format=%P`. A grafts file rewriting a merge's
    second parent to a section-free tip would make those views report the rewritten parents, so a real
    dropped section could read as a clean integration (exit 0); the raw object carries the parents the
    commit was signed with, so no graft or replacement ref can mask the drop here. Header parsing stops
    at the first blank line (the header/message boundary), so a message body line beginning "parent " is
    never mistaken for a header.
    """
    raw = _text(_git(root, ["cat-file", "commit", commit]), "post-merge commit object")
    parents = []
    for line in raw.split("\n"):
        if line == "":
            break
        key, _, value = line.partition(" ")
        if key == "parent":
            parents.append(value)
    if not all(OID_RE.fullmatch(parent) for parent in parents):
        raise GateError("post-merge parent list is malformed")
    return parents


def _merge_base_all(root, target, branch):
    lines = _text(_git(root, ["merge-base", "--all", target, branch]),
                  "merge-base").splitlines()
    bases = [line for line in lines if line]
    if not all(OID_RE.fullmatch(base) for base in bases):
        raise GateError("merge-base returned a malformed object id")
    return bases


def _merge_base(root, target, branch):
    bases = _merge_base_all(root, target, branch)
    if len(bases) != 1:
        raise GateError("target and branch must have exactly one merge-base, observed {}"
                        .format(len(bases)))
    return bases[0]


def _require_ancestor(root, ancestor, descendant, label):
    try:
        proc = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "merge-base",
                               "--is-ancestor", ancestor, descendant], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30, env=_clean_env())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise GateError("cannot validate {} ancestry ({})"
                        .format(label, type(exc).__name__))
    if proc.returncode == 1:
        raise GateError("{} is not an ancestor of the declared commit".format(label))
    if proc.returncode != 0:
        raise GateError("cannot validate {} ancestry (git exit {})"
                        .format(label, proc.returncode))


def resolve_graph(root, post_merge_ref, branch_ref=None, base_ref=None):
    post_merge = _resolve_commit(root, post_merge_ref, "post-merge ref")
    parents = _parents(root, post_merge)
    if not parents:
        raise GateError("post-merge commit has no first parent")
    target = parents[0]
    if len(parents) > 2:
        # An octopus merge integrates several branches at once, so a single --branch-ref cannot
        # represent all of them: validating one named branch would leave a section dropped from
        # another non-first parent completely unchecked (round-3 FIX A guarded only len==2, so a
        # bogus --branch-ref on an octopus masked a drop). This gate's model is one branch per
        # merge, so a 3+-parent merge is not evaluable and fails closed rather than checking a
        # subset and reading clean. Requiring branch in parents[1:] is not enough: a section-free
        # second parent is itself in parents[1:], yet checking it alone still hides another
        # parent's dropped section.
        raise GateError(
            "post-merge commit has {} parents (an octopus merge); this gate validates a single "
            "branch integration and cannot faithfully check a simultaneous multi-branch merge, "
            "so an octopus merge is not evaluable and fails closed. Integrate branches through "
            "two-parent merges, or run the gate per branch on the two-parent merge that landed it"
            .format(len(parents)))
    if branch_ref is None:
        if len(parents) != 2:
            raise GateError(
                "cannot infer the branch tip: post-merge commit must have exactly two parents; "
                "pass --branch-ref for a squash or rebase integration whose branch tip is not "
                "retained as a parent")
        branch = parents[1]
    else:
        branch = _resolve_commit(root, branch_ref, "branch ref")
        if len(parents) == 2 and branch != parents[1]:
            raise GateError(
                "post-merge commit has two parents, so its second parent is the observable branch "
                "tip; an explicit --branch-ref must name that second parent, not a different commit "
                "(a --branch-ref pointing at the first parent or elsewhere would compare against the "
                "wrong tip and could mask a dropped section). Omit --branch-ref to use the observed "
                "second parent, or pass the second parent's own ref")
    if base_ref is not None:
        base = _resolve_commit(root, base_ref, "base ref")
        bases = _merge_base_all(root, target, branch)
        if len(bases) > 1:
            raise GateError(
                "target and branch have {} merge-bases; an explicit --base-ref is accepted only when "
                "the merge-base is unique, matching the default path (two merge-bases can disagree on "
                "a section, so accepting one would let a real drop read as pre-existing)"
                .format(len(bases)))
        if bases and base != bases[0]:
            raise GateError(
                "explicit base ref is not the unique merge-base of target and branch; a supplied "
                "--base-ref must name that merge-base, not merely a common ancestor")
    else:
        base = _merge_base(root, target, branch)
    _require_ancestor(root, base, target, "base-to-target")
    _require_ancestor(root, base, branch, "base-to-branch")
    return base, branch, post_merge


def _tree_text(root, commit, path):
    """Read a regular file from a commit. Return None only when the path is observably absent."""
    listing = _git(root, ["ls-tree", "-z", "--full-tree", commit, "--", path])
    entries = [entry for entry in listing.split(b"\0") if entry]
    if not entries:
        return None
    if len(entries) != 1 or b"\t" not in entries[0]:
        raise GateError("{} has an ambiguous tree entry at {}".format(commit, path))
    meta, raw_name = entries[0].split(b"\t", 1)
    if raw_name != path.encode("utf-8"):
        raise GateError("tree lookup for {} returned a different path".format(path))
    fields = meta.split()
    if len(fields) != 3:
        raise GateError("{} has a malformed tree entry at {}".format(commit, path))
    mode, kind, oid = fields
    if (kind != b"blob" or mode not in (b"100644", b"100755")
            or not OID_RE.fullmatch(oid.decode("ascii", errors="ignore"))):
        raise GateError("{} at {} is not a regular file blob".format(path, commit))
    payload = _git(root, ["cat-file", "blob", oid.decode("ascii")])
    return _text(payload, "{} at {}".format(path, commit))


def parse_sections(text, pattern, marker, where):
    """Return {exact heading: marker-present}. Duplicate selected headings fail closed."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    sections = {}
    for position, start in enumerate(starts):
        heading = lines[start]
        if pattern.fullmatch(heading) is None:
            continue
        if heading in sections:
            raise GateError("{}: duplicate selected heading {!r}".format(where, heading))
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        sections[heading] = marker in lines[start:end]
    return sections


def compare_record(spec, marker, base_text, branch_text, post_text):
    if branch_text is None:
        raise GateError("declared record {} is absent at the branch tip".format(spec.path))
    base_sections = {} if base_text is None else parse_sections(
        base_text, spec.heading_pattern, marker,
        "{} at merge-base".format(spec.path))
    branch_sections = parse_sections(
        branch_text, spec.heading_pattern, marker,
        "{} at branch tip".format(spec.path))
    post_sections = {} if post_text is None else parse_sections(
        post_text, spec.heading_pattern, marker,
        "{} at post-merge".format(spec.path))
    owned = [heading for heading in branch_sections if heading not in base_sections]
    findings = []
    opted_out = 0
    for heading in owned:
        if heading in post_sections:
            continue
        if branch_sections[heading]:
            opted_out += 1
            continue
        findings.append(
            "{}: branch-owned section {!r} is absent post-merge"
            .format(spec.path, heading))
    return len(owned), opted_out, findings


def _check_no_cr(line, where):
    """A CR inside a table line is ambiguous input (the grammar splits on LF only), so it fails closed."""
    if "\r" in line:
        raise GateError("{}: a table line contains a carriage return".format(where))


def _split_cells(line, where):
    """Split one |-delimited table line into raw cell strings, honouring backslash escapes.

    The line must start with `|` and end with an unescaped `|` (trailing spaces or tabs allowed); an
    escaped `\\|` stays inside its cell, so a naive split on every pipe cannot change the cell count.
    """
    trimmed = line.rstrip(" \t")
    if not trimmed.startswith("|"):
        raise GateError("{}: a table line must start with a pipe".format(where))
    parts = []
    current = []
    index = 0
    while index < len(trimmed):
        char = trimmed[index]
        if char == "\\" and index + 1 < len(trimmed):
            current.append(char + trimmed[index + 1])
            index += 2
            continue
        if char == "|":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    if len(parts) < 3 or parts[-1] != "":
        raise GateError("{}: a table line must end with an unescaped pipe and carry at least one cell"
                        .format(where))
    return parts[1:-1]


def _normalize_cells(parts):
    """Cell identity trims surrounding whitespace only, so re-padding or alignment changes compare equal."""
    return tuple(part.strip() for part in parts)


def _check_cell_count(count, expected, where):
    if count != expected:
        raise GateError("{}: a table row has {} cell(s), the header has {}".format(where, count, expected))


def _locate_header(lines, header, where):
    """Index of the unique line matching the declared header, None when absent, fail-closed on duplicates.

    A line matches when its trimmed cells equal the declared header's trimmed cells, so a re-padded or
    realigned header still locates the table, consistent with the row-cell identity. Uniqueness over
    matching lines is deliberate: a second copy of the header anywhere in the file (for example inside a
    code fence) makes the table's location ambiguous, so it fails closed rather than guessing. A line that
    would match the header once its carriage returns are removed fails closed: the grammar splits on LF
    only, so a CR-bearing header (a CRLF table) must never read as the table being absent, which would
    silently empty every row obligation for that snapshot.
    """
    header_cells = _normalize_cells(_split_cells(header, "{} declared header".format(where)))
    matches = []
    for index, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        stripped = line.replace("\r", "")
        try:
            cells = _normalize_cells(_split_cells(stripped, where))
        except GateError:
            continue
        if cells != header_cells:
            continue
        if stripped != line:
            raise GateError(
                "{}: a line matching the declared header contains a carriage return; a CRLF table "
                "fails closed rather than reading as absent".format(where))
        matches.append(index)
    if len(matches) > 1:
        raise GateError("{}: the declared header line appears {} times".format(where, len(matches)))
    if not matches:
        return None
    return matches[0]


def _key_positions(header_cells, key, where):
    """Positions of the declared key columns in the header, each present exactly once, else fail closed."""
    positions = []
    for name in key:
        matches = [index for index, cell in enumerate(header_cells) if cell == name]
        if len(matches) != 1:
            raise GateError("{}: key column {!r} must appear exactly once in the header, found {}"
                            .format(where, name, len(matches)))
        positions.append(matches[0])
    return tuple(positions)


def _register_key(rows, key, row, where):
    """Record one row under its key tuple; a duplicate key in a snapshot is ambiguous identity (exit 2)."""
    if key in rows:
        raise GateError("{}: duplicate row key {}".format(where, _key_label(key)))
    rows[key] = row


def _key_label(key):
    return "(" + ", ".join(repr(part) for part in key) + ")"


def _toml_rows_array(data, array, where):
    """The declared top-level array, validated as an array of tables; None when the key is absent."""
    if not isinstance(data, dict) or array not in data:
        return None
    rows = data[array]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise GateError("{}: {} must be an array of tables".format(where, array))
    return rows


def _toml_row_value(value, where):
    """A key field must be a string or an integer; anything else (a bool included) is ambiguous identity."""
    if type(value) not in (str, int):
        raise GateError("{}: a key field must be a string or an integer, got {}"
                        .format(where, type(value).__name__))
    return value


def _toml_row_key(row, key, where):
    values = []
    for name in key:
        if name not in row:
            raise GateError("{}: key column {!r} is missing".format(where, name))
        values.append(_toml_row_value(row[name], "{} key column {!r}".format(where, name)))
    return tuple(values)


def _read_table_rows(text, decl, where):
    """Parse one snapshot of a declared table into an ordered {key tuple: row value} mapping.

    Returns None when the table is absent from the snapshot (no declared header line, or no declared TOML
    array); the caller decides whether absence is skippable (the compared base: the table did not exist
    yet) or fail-closed (post-merge: see _require_present). Rows are compared by parsed value: a Markdown
    row is its tuple of trimmed cells, a TOML row is its parsed table, so layout changes compare equal.
    """
    if decl.table_format == "toml":
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
            raise GateError("{}: cannot parse TOML ({})".format(where, exc))
        raw_rows = _toml_rows_array(data, decl.array, where)
        if raw_rows is None:
            return None
        rows = {}
        for number, row in enumerate(raw_rows, 1):
            key = _toml_row_key(row, decl.key, "{} row #{}".format(where, number))
            _register_key(rows, key, row, where)
        return rows
    lines = text.split("\n")
    start = _locate_header(lines, decl.header, where)
    if start is None:
        return None
    if start + 1 >= len(lines):
        raise GateError("{}: the header line is not followed by a delimiter row".format(where))
    delimiter = lines[start + 1]
    _check_no_cr(delimiter, where)
    delimiter_cells = _normalize_cells(_split_cells(delimiter, where))
    if (len(delimiter_cells) != len(decl.header_cells)
            or not all(DELIMITER_CELL_RE.fullmatch(cell) for cell in delimiter_cells)):
        raise GateError("{}: the line after the header is not a matching delimiter row".format(where))
    rows = {}
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        _check_no_cr(line, where)
        cells = _normalize_cells(_split_cells(line, where))
        _check_cell_count(len(cells), len(decl.header_cells), where)
        key = tuple(cells[position] for position in decl.key_positions)
        _register_key(rows, key, cells, where)
    return rows


def _require_present(rows, decl, where):
    """A declared table absent from a file that still exists post-merge is ambiguous and fails closed.

    Whole-file deletion is handled by the caller (every row disappeared, a finding, exit 1); a surviving
    file whose declared header or array vanished cannot be told apart from a reformatting that silently
    orphaned the rows, so it is exit 2 rather than a guess in either direction.
    """
    if rows is None:
        raise GateError("{}: the declared table ({}) is absent post-merge while the file exists"
                        .format(where, decl.header or decl.array))
    return rows


def _r1_base_commit(target, base):
    """R1 compares against the post-merge commit's FIRST PARENT (the pre-merge target), never the
    merge-base: the lost row usually landed on the target after the branch forked, so the merge-base
    would not hold it and a merge-base comparison would read the drop as pre-existing."""
    del base
    return target


def _r1_findings(decl, base_rows, post_rows):
    """R1, base-row preservation: every key at the pre-merge target survives into the post-merge commit."""
    return ["{}: row {} present at the pre-merge target is absent post-merge"
            .format(decl.path, _key_label(key))
            for key in base_rows if key not in post_rows]


def _r2_findings(decl, mergebase_rows, branch_rows, post_rows):
    """R2, the row analogue of the heading leg: every branch-owned key (at the branch tip, absent at the
    merge-base) survives into the post-merge commit. Rows have no opt-out marker."""
    return ["{}: branch-owned row {} is absent post-merge".format(decl.path, _key_label(key))
            for key in branch_rows if key not in mergebase_rows and key not in post_rows]


def _row_values_equal(before, after):
    """Type-aware parsed-value equality for row content, recursive over TOML containers.

    Plain Python equality is wrong for frozen rows twice over: bool is an int subclass and int equals
    float (True == 1 == 1.0), so a boolean/integer or integer/float cell substitution would read as
    unchanged, and NaN never equals itself, so an untouched NaN cell would read as an edit. Values of
    different concrete types are therefore never equal, two NaN floats are equal (an unchanged NaN is not
    an edit), and lists, tuples, and tables compare element by element under the same rules.
    """
    if type(before) is not type(after):
        return False
    if isinstance(before, float):
        if math.isnan(before) or math.isnan(after):
            return math.isnan(before) and math.isnan(after)
        return before == after
    if isinstance(before, dict):
        return (before.keys() == after.keys()
                and all(_row_values_equal(before[key], after[key]) for key in before))
    if isinstance(before, (list, tuple)):
        return (len(before) == len(after)
                and all(_row_values_equal(b, a) for b, a in zip(before, after)))
    return before == after


def _r3_findings(decl, base_rows, post_rows):
    """R3, frozen rows: a key present at both the pre-merge target and post-merge with any cell changed."""
    return ["{}: frozen row {} was edited post-merge".format(decl.path, _key_label(key))
            for key in base_rows
            if key in post_rows and not _row_values_equal(base_rows[key], post_rows[key])]


def _r3_branch_findings(decl, mergebase_rows, branch_rows, post_rows):
    """R3 for branch-owned rows in a frozen table: a key introduced on the branch (absent at the
    merge-base) that survives into the post-merge commit must keep its branch-tip content, so a resolver
    cannot rewrite a frozen row the branch added while R2 sees its key as preserved."""
    return ["{}: branch-owned frozen row {} was edited post-merge"
            .format(decl.path, _key_label(key))
            for key in branch_rows
            if key not in mergebase_rows and key in post_rows
            and not _row_values_equal(branch_rows[key], post_rows[key])]


def _base_config_tables(root, config_path, target):
    """Table declarations read from the pre-merge target's own config, so the same merge cannot remove a
    table's declaration and drop that table's rows at once.

    Reads the first parent's tree at the working config's repo-relative path. A config absent there, or a
    working config outside the repository root, contributes no tables; a schema-version-1 config there
    contributes none either, because version 1 cannot declare tables. Any other shape (unparseable TOML, a
    missing or non-integer schema-version, or a version other than 1 or 2) fails closed, because the
    declarations such a config may carry cannot be known. Declaration narrowing landed in an EARLIER,
    separate commit is out of scope (trusted config).
    """
    config_rel = _repo_relative(root, config_path)
    if config_rel is None:
        return ()
    text = _tree_text(root, target, config_rel)
    if text is None:
        return ()
    where = "{} at the pre-merge target".format(config_rel)
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("{}: cannot parse ({})".format(where, exc))
    version = data.get("schema-version") if isinstance(data, dict) else None
    if type(version) is not int or version not in (1, 2):
        raise GateError(
            "{}: schema-version must be integer 1 or 2; a first-parent config the gate cannot "
            "interpret may carry declarations it cannot enforce".format(where))
    if version == 1:
        return ()
    _check_v2_top_keys(data, where)
    if "table" not in data:
        return ()
    return _load_table_specs(data.get("table"), where)


def _compare_tables(root, decls, target, graph, post_merge):
    """Evaluate R1 (and R3 for frozen tables) against the first parent, plus R2 (and branch-side R3 for
    frozen tables) when the branch graph is resolvable, for every declared table. Returns (checked key
    count, findings)."""
    findings = []
    checked = 0
    r1_base = _r1_base_commit(target, graph[0] if graph else None)
    for decl in decls:
        base_text = _tree_text(root, r1_base, decl.path)
        base_rows = (None if base_text is None else
                     _read_table_rows(base_text, decl,
                                      "{} at the pre-merge target".format(decl.path)))
        post_where = "{} at post-merge".format(decl.path)
        post_text = _tree_text(root, post_merge, decl.path)
        if post_text is None:
            # The whole declared file was deleted: every row it held disappeared (a finding per base or
            # branch-owned key below), never a skip.
            post_rows = {}
        else:
            post_rows = _require_present(
                _read_table_rows(post_text, decl, post_where), decl, post_where)
        if base_rows is not None:
            checked += len(base_rows)
            findings.extend(_r1_findings(decl, base_rows, post_rows))
            if decl.edit == "frozen":
                findings.extend(_r3_findings(decl, base_rows, post_rows))
        if graph is not None:
            mergebase_commit, branch_commit = graph
            mergebase_text = _tree_text(root, mergebase_commit, decl.path)
            mergebase_rows = ({} if mergebase_text is None else
                              _read_table_rows(mergebase_text, decl,
                                               "{} at merge-base".format(decl.path)) or {})
            branch_text = _tree_text(root, branch_commit, decl.path)
            branch_rows = ({} if branch_text is None else
                           _read_table_rows(branch_text, decl,
                                            "{} at branch tip".format(decl.path)) or {})
            checked += sum(1 for key in branch_rows if key not in mergebase_rows)
            findings.extend(_r2_findings(decl, mergebase_rows, branch_rows, post_rows))
            if decl.edit == "frozen":
                findings.extend(
                    _r3_branch_findings(decl, mergebase_rows, branch_rows, post_rows))
    return checked, findings


def _config_removed_by_merge(root, config_rel, post_merge_ref):
    """True only when the config is provably present at the pre-merge target but absent now.

    Distinguishes an adopter that DELETED the config in this merge (silently disabling the gate) from
    one that never adopted it. The pre-merge target is the post-merge commit's first parent; the config
    is read from that commit's tree at config_rel, the repo-relative path that was looked for.

    A genuinely UNRESOLVABLE input is not swallowed: an unresolvable post-merge ref, a malformed parent
    list, or an unreadable target tree raises GateError and the caller fails closed (exit 2), matching
    the convention for unresolvable inputs. The one case that legitimately reports False is a resolvable
    but PARENTLESS post-merge commit (a root commit): it has no pre-merge target to have removed the
    config from, so absence there is genuinely never-adopted, not a masked removal.
    """
    if config_rel is None:
        return False
    post_merge = _resolve_commit(root, post_merge_ref, "post-merge ref")
    parents = _parents(root, post_merge)
    if not parents:
        return False
    return _tree_text(root, parents[0], config_rel) is not None


def _repo_relative(root, config_path):
    """The config path as a repo-relative POSIX string, or None if it is not inside the root."""
    try:
        return str(PurePosixPath(config_path.relative_to(root)))
    except ValueError:
        return None


def _tables_only_squash(parents, branch_ref, base_ref, specs):
    """True when the single-parent tables-only path applies: no [[record]] specs (they keep the graph
    rules), exactly one parent, and NO explicit graph input. An explicit --branch-ref falls through to the
    shared graph resolution, and so does an explicit --base-ref: with no branch there is no merge-base to
    validate it against, so silently ignoring it would accept an unresolvable or wrong base, and it fails
    closed instead."""
    return (len(parents) == 1 and branch_ref is None and base_ref is None
            and not specs)


def run(root, config_path, config_required, post_merge_ref="HEAD",
        branch_ref=None, base_ref=None):
    try:
        root = _repository_root(root)
        config = load_config(config_path, allow_absent=not config_required)
        if config is None:
            config_rel = _repo_relative(root, config_path)
            if _config_removed_by_merge(root, config_rel, post_merge_ref):
                raise GateError(
                    "the adopted record-section config {} was removed by this merge; a merge that "
                    "deletes the config silently disables the gate".format(config_rel))
            print("NOT APPLICABLE: {} is absent; this checkout has not adopted "
                  "the record-section gate".format(CONFIG_REL))
            return 0
        version = config.version
        marker, specs = config.marker, config.records
        tables_declared = 0
        table_keys_checked = 0
        post_merge_commit = _resolve_commit(root, post_merge_ref, "post-merge ref")
        parents = _parents(root, post_merge_commit)
        if not parents:
            raise GateError("post-merge commit has no first parent")
        target = parents[0]
        if _tables_only_squash(parents, branch_ref, base_ref, specs):
            # R1 needs only the post-merge commit and its first parent, so a tables-only config is
            # evaluable on a single-parent (squash) commit with no --branch-ref and no --base-ref; R2 has
            # no observable branch tip there and is skipped. [[record]] entries keep the version-1 graph
            # rules, and an octopus or rootless commit still fails closed through the shared paths.
            graph = None
            post_merge = post_merge_commit
        else:
            base, branch, post_merge = resolve_graph(
                root, post_merge_ref, branch_ref, base_ref)
            graph = (base, branch)
        total = 0
        opted_out = 0
        findings = []
        for spec in specs:
            heading_base, heading_branch = graph
            owned, opted, record_findings = compare_record(
                spec, marker,
                _tree_text(root, heading_base, spec.path),
                _tree_text(root, heading_branch, spec.path),
                _tree_text(root, post_merge, spec.path))
            total += owned
            opted_out += opted
            findings.extend(record_findings)
        # The first parent's declarations stay in force whatever schema version the working config
        # declares, so a merge cannot drop a table's rows by downgrading the config to version 1 (which
        # cannot declare tables) in the same merge.
        decls = tuple(dict.fromkeys(
            config.tables + _base_config_tables(root, config_path, target)))
        tables_declared = len(decls)
        if decls:
            table_keys_checked, table_findings = _compare_tables(
                root, decls, target, graph, post_merge)
            findings.extend(table_findings)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        if version == 1 and not tables_declared:
            print("FAIL: {} of {} branch-owned record section(s) were dropped post-merge"
                  .format(len(findings), total))
        else:
            print("FAIL: {} record preservation finding(s) post-merge (branch-owned "
                  "sections dropped, table row keys gone under R1 or R2, or frozen "
                  "rows edited under R3)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        return 1
    if version == 1 and not tables_declared:
        print("PASS: {} branch-owned record section(s) were preserved; {} carried the "
              "explicit opt-out. SCOPE: selected new headings only; bodies and "
              "pre-existing headings are not compared."
              .format(total - opted_out, opted_out))
    else:
        print("PASS: {} branch-owned record section(s) were preserved ({} opted out); "
              "{} declared table(s) with {} checked row key(s) intact under R1/R2/R3. "
              "SCOPE: selected new headings and declared table rows only; bodies, "
              "pre-existing headings, and undeclared surfaces are not compared."
              .format(total - opted_out, opted_out, tables_declared, table_keys_checked))
    return 0


def _selftest_git(root, args, input_text=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_GRAFT_FILE=os.devnull)
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env=env)
    if proc.returncode != 0:
        raise RuntimeError("self-test git {} failed ({})"
                           .format(args[0], proc.returncode))
    return proc.stdout.strip()


def _quiet_run(*args):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(*args)


@contextmanager
def _swapped(name, value):
    """Swap one module-level helper for a mutant or naive variant during a self-test case and restore it.

    The self-test proves each refusal is load-bearing by showing the same fixture PASS once the specific
    check is removed, and proves each allowed change is deliberate by showing it FAIL under the naive
    variant it guards against. Mutation is attribute patching only, never generated code.
    """
    module = sys.modules[__name__]
    original = getattr(module, name)
    setattr(module, name, value)
    try:
        yield
    finally:
        setattr(module, name, original)


def self_test():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle():
        return _self_test_isolated()


def _self_test_isolated():
    import tempfile

    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("{}: got {}, want {}".format(name, got, want))

    # F-TOML-BARE-VALUEERROR-CLASS: an integer literal past CPython's
    # 4300-digit int-string limit makes tomllib raise a BARE ValueError
    # (not TOMLDecodeError); load_config must still refuse with GateError.
    # The digit limit is pinned to the default 4300 (test-hermeticity).
    # A parser overflow (RecursionError, not a ValueError) must be refused
    # the same way, with the overflow in the refusal.
    # The overflow is INJECTED (tomllib.loads and tomllib.load raise
    # RecursionError on a marked, otherwise valid input) rather than provoked
    # by a deeply nested body: the depth at which tomllib overflows is an
    # interpreter limit, so a fixed body overflows under one recursion limit
    # and parses (or trips an unrelated refusal) under another.
    real_loads, real_load = tomllib.loads, tomllib.load

    def overflowing_loads(text, **kwargs):
        if "injected-overflow" in text:
            raise RecursionError("injected parser overflow")
        return real_loads(text, **kwargs)

    tomllib.loads = overflowing_loads
    tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
    prev_digits = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        for big_label, big_value, big_needle in (
                ("an over-long integer literal", "9" * 4400, ""),
                ("a parser overflow", "1  # injected-overflow",
                 "injected parser overflow")):
            with tempfile.TemporaryDirectory(
                    prefix="aiqt-record-sections-bigint-") as big_dir:
                big_cfg = Path(big_dir) / "record-sections.toml"
                big_cfg.write_text(
                    "schema-version = " + big_value + "\n", encoding="utf-8")
                try:
                    load_config(big_cfg)
                    big_outcome = "accepted"
                except GateError as exc:
                    big_outcome = ("refused" if big_needle in str(exc)
                                   else "refused without the finding")
                except (ValueError, RecursionError) as exc:
                    big_outcome = "a bare {} escaped".format(
                        type(exc).__name__)
                expect(big_label + " in the config fails closed",
                       big_outcome, "refused")
    finally:
        sys.set_int_max_str_digits(prev_digits)
        tomllib.loads, tomllib.load = real_loads, real_load

    try:
        with tempfile.TemporaryDirectory(
                prefix="aiqt-record-sections-") as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            _selftest_git(root, ["init", "-q", "-b", "main"])
            _selftest_git(root, ["config", "user.name", "AIQT Self-Test"])
            _selftest_git(
                root,
                ["config", "user.email", "selftest@example.invalid"])
            aiqt = root / ".aiqt"
            aiqt.mkdir()
            config_path = aiqt / "record-sections.toml"
            config_path.write_text(
                'schema-version = 1\n'
                'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n\n'
                '[[record]]\n'
                'path = "records.md"\n'
                "heading-pattern = "
                "'^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'\n",
                encoding="utf-8")
            base_text = (
                "# Record\n\n"
                "## 2026-08-01 Existing\n"
                "base\n")
            branch_text = (
                base_text
                + "\n## 2026-08-30 Branch item\n"
                + "branch\n")
            (root / "records.md").write_text(base_text, encoding="utf-8")
            _selftest_git(root, ["add", "-A"])
            _selftest_git(root, ["commit", "-q", "-m", "base"])
            base = _selftest_git(root, ["rev-parse", "HEAD"])

            _selftest_git(root, ["checkout", "-q", "-b", "topic"])
            (root / "records.md").write_text(branch_text, encoding="utf-8")
            _selftest_git(root, ["add", "records.md"])
            _selftest_git(
                root, ["commit", "-q", "-m", "add record section"])
            topic = _selftest_git(root, ["rev-parse", "HEAD"])

            _selftest_git(
                root, ["checkout", "-q", "-b", "target", base])
            (root / "target.txt").write_text("target\n", encoding="utf-8")
            _selftest_git(root, ["add", "target.txt"])
            _selftest_git(
                root, ["commit", "-q", "-m", "target change"])
            target = _selftest_git(root, ["rev-parse", "HEAD"])
            target_tree = _selftest_git(
                root, ["rev-parse", "{}^{{tree}}".format(target)])

            _selftest_git(
                root,
                ["merge", "-q", "--no-ff", "-m", "good merge", topic])
            good = _selftest_git(root, ["rev-parse", "HEAD"])
            expect(
                "preserved standard merge",
                _quiet_run(root, config_path, True, good, None, None),
                0)

            bad = _selftest_git(
                root,
                ["commit-tree", target_tree, "-p", target, "-p", topic],
                input_text="dropped merge\n")
            expect(
                "dropped section",
                _quiet_run(root, config_path, True, bad, None, None),
                1)
            expect(
                "explicit refs on squash-like commit",
                _quiet_run(root, config_path, True, target, topic, base),
                1)
            expect(
                "one-parent branch inference ambiguity",
                _quiet_run(root, config_path, True, target, None, None),
                2)

            # Round-3 FIX A (branch-ref must match the observable second parent): `bad` is a two-parent
            # merge that drops the section. Passing its FIRST parent (target) as --branch-ref made the
            # gate compare target against itself (empty owned set) and read clean. When the post-merge
            # commit has two parents, its second parent is the observable branch tip, so a --branch-ref
            # that disagrees with it fails closed rather than masking the drop.
            expect(
                "explicit --branch-ref disagreeing with the observable second parent fails closed",
                _quiet_run(root, config_path, True, bad, target, None),
                2)

            # Round-4 FIX A (octopus / 3+ parent merge is not evaluable and fails closed): round-3's
            # disagreement guard fired only on len(parents)==2, so a 3+-parent (octopus) merge accepted
            # a bogus --branch-ref and masked a drop. Build an octopus post-merge commit whose tree is
            # target_tree (so `topic`'s section is dropped) with parents [target, topic, octo_extra];
            # `topic` introduced the section, `octo_extra` is section-free. With ANY --branch-ref (the
            # first parent, the section-owning parent, or a section-free parent) an octopus cannot be
            # faithfully validated by this single-branch gate, so it fails closed. Pre-fix, the first
            # parent and the section-free parent both read exit 0 PASS, masking the drop.
            _selftest_git(root, ["checkout", "-q", "-b", "octo-extra", base])
            (root / "octo-extra.txt").write_text("x\n", encoding="utf-8")
            _selftest_git(root, ["add", "-A"])
            _selftest_git(
                root, ["commit", "-q", "-m", "octopus third parent, no section"])
            octo_extra = _selftest_git(root, ["rev-parse", "HEAD"])
            octopus = _selftest_git(
                root,
                ["commit-tree", target_tree, "-p", target, "-p", topic,
                 "-p", octo_extra],
                input_text="octopus drops the section\n")
            expect(
                "octopus merge fails closed with --branch-ref at the first parent",
                _quiet_run(root, config_path, True, octopus, target, None),
                2)
            expect(
                "octopus merge fails closed with --branch-ref at a section-free parent",
                _quiet_run(root, config_path, True, octopus, octo_extra, None),
                2)
            expect(
                "octopus merge fails closed with --branch-ref at the section-owning parent",
                _quiet_run(root, config_path, True, octopus, topic, None),
                2)

            # Round-6 FIX (git replacement ref must not mask a drop): the gate reads a commit's parents
            # with `show -s --format=%P`, which honours refs/replace/*. `bad` is a real two-parent merge
            # [target, topic] that drops topic's section (exit 1). Replacing bad's commit object with a
            # substitute whose parents are [target, base] (a section-free branch tip) rewrites the parent
            # list the gate sees, so pre-fix the SAME invocation read exit 0 and masked the drop. Every
            # git call now carries --no-replace-objects, so the gate reads the real parents and still
            # returns exit 1. The replace ref is removed afterwards so later fixtures read `bad` honestly.
            replace_sub = _selftest_git(
                root,
                ["commit-tree", target_tree, "-p", target, "-p", base],
                input_text="masking substitute with a section-free second parent\n")
            _selftest_git(root, ["replace", bad, replace_sub])
            try:
                expect(
                    "a git replacement ref does not mask a dropped section",
                    _quiet_run(root, config_path, True, bad, None, None),
                    1)
            finally:
                _selftest_git(root, ["replace", "-d", bad])

            # Round-8 FIX (an on-disk .git/info/grafts file must not mask a dropped section): the
            # round-6 --no-replace-objects flag disables refs/replace/* only, NOT the legacy grafts
            # mechanism, which git still honours for the graph views (`show -s --format=%P`,
            # `merge-base`). `bad` is a real two-parent merge [target, topic] that drops topic's
            # section (exit 1). Writing `.git/info/grafts` = `<bad> <target> <base>` rewrites bad's
            # observed parents to [target, base] (base is a section-free tip), so pre-fix the SAME
            # invocation read exit 0 and masked the drop. The gate now reads TRUE parents from the raw
            # commit object via `cat-file commit` (graft- and replace-immune) and pins GIT_GRAFT_FILE
            # to an empty file for every git call, so it reads the real second parent `topic` and still
            # returns exit 1. The grafts file is removed afterwards so later fixtures read `bad`
            # honestly; it also verifies the gate never depends on the stray graft persisting.
            grafts_file = root / ".git" / "info" / "grafts"
            grafts_file.parent.mkdir(parents=True, exist_ok=True)
            grafts_file.write_text(
                "{} {} {}\n".format(bad, target, base), encoding="utf-8")
            try:
                expect(
                    "an on-disk .git/info/grafts file does not mask a dropped section",
                    _quiet_run(root, config_path, True, bad, None, None),
                    1)
            finally:
                grafts_file.unlink()

            # Round-6 FIX (legitimate `//abs` config path must not be wrongly rejected): os.path.normpath
            # keeps a POSIX-defined leading `//` that realpath collapses to `/`, so a genuine absolute
            # config path written with a doubled leading slash made real != lexical falsely fire (exit 2).
            # The leading-slash run is now folded on the lexical form, so the real config loads and the
            # dropped-section merge `bad` is detected (exit 1). Driven through main() so the containment
            # path is exercised.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                double_slash_rc = main(
                    ["--root", str(root), "--config", "/" + str(config_path),
                     "--post-merge-ref", bad])
            expect(
                "a legitimate `//abs` config path is accepted, not rejected as a symlink",
                double_slash_rc, 1)

            # Round-6 FIX (an invalid config path fails closed, not crash): an embedded NUL byte in
            # --config reaches realpath's lstat and raised an uncaught ValueError pre-fix; it is now a
            # validation failure (exit 2). Reachable through the in-process interface; OS argv cannot
            # carry a NUL.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                nul_rc = main(
                    ["--root", str(root), "--config", "bad\x00path",
                     "--post-merge-ref", bad])
            expect("an embedded NUL in the config path fails closed", nul_rc, 2)

            # Round-6 accuracy pin (a symlink cancelled back to the same literal path is allowed): the
            # guard rejects a NET-REDIRECTING crossing (real != lexical), not every symlink crossing.
            # `cancel-link -> .aiqt`, so `cancel-link/../.aiqt/record-sections.toml` crosses the symlink
            # yet the following `..` climbs back so realpath == normpath == the real config. It loads the
            # literally-named reviewed config and detects the drop on `bad` (exit 1), which is the
            # behaviour the corrected disclosure documents; this pins it against a future change that
            # would wrongly fail it closed and contradict the disclosure.
            cancel_link = root / "cancel-link"
            if cancel_link.is_symlink() or cancel_link.exists():
                cancel_link.unlink()
            os.symlink(".aiqt", str(cancel_link))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                cancel_rc = main(
                    ["--root", str(root), "--config",
                     "cancel-link/../.aiqt/record-sections.toml", "--post-merge-ref", bad])
            cancel_link.unlink()
            expect(
                "a symlink cancelled back to the same literal config path is allowed and detects the drop",
                cancel_rc, 1)

            # Round-3 FIX B (symlinked PARENT component): the round-2 guard checked only the final path
            # component, so a symlinked parent directory still redirected the config the load and the
            # removal check read. Point --config through a symlinked parent (linkdir -> .aiqt); a symlink
            # at ANY component of the config path fails closed. Driven through main() so the containment
            # path is exercised; `good` is a clean merge that would otherwise read PASS.
            link_parent = root / "linkdir"
            if link_parent.is_symlink() or link_parent.exists():
                link_parent.unlink()
            os.symlink(".aiqt", str(link_parent))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                symlink_parent_rc = main(
                    ["--root", str(root), "--config",
                     "linkdir/record-sections.toml", "--post-merge-ref", good])
            link_parent.unlink()
            expect(
                "symlinked parent component in the config path fails closed",
                symlink_parent_rc, 2)

            # Round-4 FIX B (a crossed symlink hidden by ENOENT-masked '..' or lexical non-containment):
            # the round-3 guard walked the RAW components with lstat and asked relative_to(root)
            # lexically, so a nonexistent-component + '..' (x/../ln/...) made each incremental lstat hit
            # ENOENT (is_symlink -> False) while resolve() still followed the symlink, and a lexically
            # non-contained absolute form (../ back into root) tripped the ValueError early-return that
            # checked only the final component. Both followed the symlink and read the redirected config.
            # The fix judges the honest realpath. Point an in-repo symlink `ln` at a decoy dir holding a
            # never-matching (narrowed) config; on the dropped-section merge `bad`, following it would
            # read exit 0 PASS, so each bypass masks the drop. Both must fail closed on the realpath.
            decoy_dir = root / "decoy-cfg"
            decoy_dir.mkdir()
            (decoy_dir / "record-sections.toml").write_text(
                'schema-version = 1\n'
                'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n\n'
                '[[record]]\n'
                'path = "records.md"\n'
                "heading-pattern = '^## NEVER-MATCHES-ANY-REAL-HEADING$'\n",
                encoding="utf-8")
            link = root / "ln"
            if link.is_symlink() or link.exists():
                link.unlink()
            os.symlink("decoy-cfg", str(link))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                enoent_dotdot_rc = main(
                    ["--root", str(root), "--config",
                     "x/../ln/record-sections.toml", "--post-merge-ref", bad])
            escape_value = "{}/../{}/{}/ln/record-sections.toml".format(
                root.parent, root.parent.name, root.name)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                lexical_escape_rc = main(
                    ["--root", str(root), "--config", escape_value,
                     "--post-merge-ref", bad])
            link.unlink()
            expect(
                "ENOENT-masked '..' prefix crossing a symlink fails closed",
                enoent_dotdot_rc, 2)
            expect(
                "lexically non-contained form crossing a symlink fails closed",
                lexical_escape_rc, 2)

            # Round-4 FIX B, symlink-BEFORE-'..' ordering: the config path must be judged on the RAW
            # candidate, never a pre-collapsed one. A `symlink/../name` crosses the symlink and then
            # climbs, so the honest realpath resolves the link FIRST (POSIX order) and lands off the
            # lexical guess, while a lexical pre-collapse would fold `symlink/..` away and hide the
            # crossing. Symlink `lnn` -> a nested real dir, and a narrowed decoy at root/shadow-cfg:
            # `lnn/../shadow-cfg/record-sections.toml` resolves (realpath) to root/<nested>/shadow-cfg
            # (absent) but a pre-collapse would read the root/shadow-cfg decoy and mask the drop, so the
            # crossing must fail closed on the realpath rather than read the decoy.
            (root / "nest-dir" / "inner").mkdir(parents=True)
            shadow_dir = root / "shadow-cfg"
            shadow_dir.mkdir()
            (shadow_dir / "record-sections.toml").write_text(
                'schema-version = 1\n'
                'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n\n'
                '[[record]]\n'
                'path = "records.md"\n'
                "heading-pattern = '^## NEVER-MATCHES-ANY-REAL-HEADING$'\n",
                encoding="utf-8")
            nested_link = root / "lnn"
            if nested_link.is_symlink() or nested_link.exists():
                nested_link.unlink()
            os.symlink("nest-dir/inner", str(nested_link))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                symlink_then_dotdot_rc = main(
                    ["--root", str(root), "--config",
                     "lnn/../shadow-cfg/record-sections.toml", "--post-merge-ref", bad])
            nested_link.unlink()
            expect(
                "a symlink crossed before '..' fails closed, not folded away",
                symlink_then_dotdot_rc, 2)

            _selftest_git(
                root, ["checkout", "-q", "-b", "optout", base])
            opt_text = (
                base_text
                + "\n## 2026-08-30 Superseded item\n"
                + "<!-- aiqt-record-section: superseded -->\n"
                + "branch\n")
            (root / "records.md").write_text(opt_text, encoding="utf-8")
            _selftest_git(root, ["add", "records.md"])
            _selftest_git(
                root,
                ["commit", "-q", "-m", "opt out record section"])
            optout = _selftest_git(root, ["rev-parse", "HEAD"])
            optmerge = _selftest_git(
                root,
                ["commit-tree", target_tree, "-p", target, "-p", optout],
                input_text="explicit supersession\n")
            expect(
                "section-local opt-out",
                _quiet_run(
                    root, config_path, True, optmerge, None, None),
                0)

            _selftest_git(
                root, ["checkout", "-q", "-b", "duplicate", base])
            duplicate_text = (
                base_text
                + "\n## 2026-08-30 Duplicate\none\n"
                + "\n## 2026-08-30 Duplicate\ntwo\n")
            (root / "records.md").write_text(
                duplicate_text, encoding="utf-8")
            _selftest_git(root, ["add", "records.md"])
            _selftest_git(
                root, ["commit", "-q", "-m", "duplicate headings"])
            duplicate = _selftest_git(root, ["rev-parse", "HEAD"])
            dupmerge = _selftest_git(
                root,
                ["commit-tree", target_tree, "-p", target,
                 "-p", duplicate],
                input_text="ambiguous merge\n")
            expect(
                "duplicate selected heading",
                _quiet_run(
                    root, config_path, True, dupmerge, None, None),
                2)

            bad_config = aiqt / "bad-record-sections.toml"
            bad_config.write_text(
                'schema-version = true\nrecord = "wrong"\n',
                encoding="utf-8")
            expect(
                "malformed config",
                _quiet_run(root, bad_config, True, bad, None, None),
                2)
            expect(
                "absent default config is not applicable",
                _quiet_run(
                    root, aiqt / "absent.toml", False, bad, None, None),
                0)

            # Finding 1 (env-injection): ambient GIT_ variables must not redirect the gate's git
            # view. Build a decoy repo whose HEAD is a clean merge that introduces no branch-owned
            # section, point the real checkout's HEAD at the dropped merge, and confirm the gate reads
            # the REAL repo (exit 1), not the decoy (which alone would read as a clean exit 0).
            _selftest_git(root, ["checkout", "-q", bad])
            decoy = Path(directory) / "decoy"
            decoy.mkdir()
            _selftest_git(decoy, ["init", "-q", "-b", "main"])
            _selftest_git(decoy, ["config", "user.name", "AIQT Self-Test"])
            _selftest_git(
                decoy,
                ["config", "user.email", "selftest@example.invalid"])
            (decoy / "records.md").write_text(base_text, encoding="utf-8")
            _selftest_git(decoy, ["add", "-A"])
            _selftest_git(decoy, ["commit", "-q", "-m", "decoy base"])
            _selftest_git(decoy, ["checkout", "-q", "-b", "decoy-topic"])
            (decoy / "unrelated.txt").write_text("x\n", encoding="utf-8")
            _selftest_git(decoy, ["add", "-A"])
            _selftest_git(
                decoy,
                ["commit", "-q", "-m", "decoy topic, no new section"])
            _selftest_git(decoy, ["checkout", "-q", "main"])
            _selftest_git(
                decoy,
                ["merge", "-q", "--no-ff", "-m", "decoy clean merge",
                 "decoy-topic"])
            injected = {name: os.environ.get(name)
                        for name in ("GIT_DIR", "GIT_WORK_TREE")}
            os.environ["GIT_DIR"] = str(decoy / ".git")
            os.environ["GIT_WORK_TREE"] = str(root)
            try:
                expect(
                    "ambient GIT_ env does not redirect the gate",
                    _quiet_run(root, config_path, True, "HEAD", None, None),
                    1)
            finally:
                for name, value in injected.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

            # Finding 2 (config-removal evasion): a merge that DELETES the adopted default config
            # fails closed, never NOT APPLICABLE. The config is present at the target parent and
            # absent in the merge, so the gate must not read the deletion as never-adopted.
            _selftest_git(
                root, ["checkout", "-q", "-b", "config-removed", target])
            (root / ".aiqt" / "record-sections.toml").unlink()
            _selftest_git(root, ["add", "-A"])
            _selftest_git(
                root, ["commit", "-q", "-m", "remove adopted config"])
            removed_tree = _selftest_git(
                root, ["rev-parse", "HEAD^{tree}"])
            config_drop_merge = _selftest_git(
                root,
                ["commit-tree", removed_tree, "-p", target, "-p", topic],
                input_text="merge deleting the config\n")
            expect(
                "config removed by the merge fails closed",
                _quiet_run(
                    root, config_path, False, config_drop_merge, None, None),
                2)

            # Finding 3 (--base-ref bypass): an explicit --base-ref must be a real merge-base, not
            # merely a common ancestor. Build a criss-cross where target and branch have TWO
            # merge-bases (so the default path already fails closed), plus an earlier ancestor C that
            # still carries the section. Supplying C as --base-ref hid the dropped section pre-fix.
            cx = Path(directory) / "crisscross"
            cx.mkdir()
            _selftest_git(cx, ["init", "-q", "-b", "main"])
            _selftest_git(cx, ["config", "user.name", "AIQT Self-Test"])
            _selftest_git(
                cx, ["config", "user.email", "selftest@example.invalid"])
            (cx / ".aiqt").mkdir()
            (cx / ".aiqt" / "record-sections.toml").write_text(
                'schema-version = 1\n'
                'opt-out-marker = "<!-- aiqt-record-section: superseded -->"'
                '\n\n'
                '[[record]]\n'
                'path = "records.md"\n'
                "heading-pattern = "
                "'^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'\n",
                encoding="utf-8")
            no_section = "# Record\n\n## 2026-08-01 Existing\nbase\n"
            with_section = (
                no_section + "\n## 2026-08-30 Branch item\nbranch\n")
            (cx / "records.md").write_text(with_section, encoding="utf-8")
            _selftest_git(cx, ["add", "-A"])
            _selftest_git(cx, ["commit", "-q", "-m", "C carries the section"])
            cx_c = _selftest_git(cx, ["rev-parse", "HEAD"])
            (cx / "records.md").write_text(no_section, encoding="utf-8")
            _selftest_git(cx, ["add", "-A"])
            _selftest_git(cx, ["commit", "-q", "-m", "M removes the section"])
            cx_m = _selftest_git(cx, ["rev-parse", "HEAD"])
            _selftest_git(cx, ["checkout", "-q", "-b", "left", cx_m])
            (cx / "left.txt").write_text("l\n", encoding="utf-8")
            _selftest_git(cx, ["add", "-A"])
            _selftest_git(cx, ["commit", "-q", "-m", "left"])
            cx_left = _selftest_git(cx, ["rev-parse", "HEAD"])
            _selftest_git(cx, ["checkout", "-q", "-b", "right", cx_m])
            (cx / "right.txt").write_text("r\n", encoding="utf-8")
            _selftest_git(cx, ["add", "-A"])
            _selftest_git(cx, ["commit", "-q", "-m", "right"])
            cx_right = _selftest_git(cx, ["rev-parse", "HEAD"])
            left_tree = _selftest_git(cx, ["rev-parse", "left^{tree}"])
            cx_target = _selftest_git(
                cx,
                ["commit-tree", left_tree, "-p", cx_left, "-p", cx_right],
                input_text="target merge, two bases\n")
            branch_merge = _selftest_git(
                cx,
                ["commit-tree", left_tree, "-p", cx_right, "-p", cx_left],
                input_text="branch merge, two bases\n")
            _selftest_git(cx, ["checkout", "-q", branch_merge])
            (cx / "records.md").write_text(with_section, encoding="utf-8")
            (cx / "left.txt").write_text("l\n", encoding="utf-8")
            (cx / "right.txt").write_text("r\n", encoding="utf-8")
            _selftest_git(cx, ["add", "-A"])
            _selftest_git(
                cx, ["commit", "-q", "-m", "branch re-adds the section"])
            cx_branch = _selftest_git(cx, ["rev-parse", "HEAD"])
            cx_target_tree = _selftest_git(
                cx, ["rev-parse", "{}^{{tree}}".format(cx_target)])
            cx_post = _selftest_git(
                cx,
                ["commit-tree", cx_target_tree,
                 "-p", cx_target, "-p", cx_branch],
                input_text="post-merge drops the section\n")
            cx_config = cx / ".aiqt" / "record-sections.toml"
            expect(
                "two merge-bases fail closed with no explicit base",
                _quiet_run(cx, cx_config, True, cx_post, None, None),
                2)
            expect(
                "non-merge-base explicit base is rejected",
                _quiet_run(cx, cx_config, True, cx_post, None, cx_c),
                2)

            # MAJOR A (cannot-evaluate must fail closed, not open): with the default config absent AND
            # an unresolvable post-merge ref, the removal check cannot answer, so the gate fails closed
            # (exit 2) rather than reporting NOT APPLICABLE. config_path was deleted by the finding-2
            # vector above, so it is genuinely absent (and not a symlink) here.
            expect(
                "unresolvable post-merge ref fails closed when config is absent",
                _quiet_run(
                    root, config_path, False, "no-such-ref-xyz", None, None),
                2)

            # MAJOR B (symlinked config must fail closed): replacing the default config with an in-repo
            # dangling symlink must not let path resolution point the removal check at the wrong target.
            # A symlinked config (dangling or resolving) is fail-closed; only a genuinely absent,
            # not-a-symlink default config is NOT APPLICABLE. Driven through main() so the containment
            # path (which resolves the symlink) is exercised, with the config present at the target
            # parent so a bypass would otherwise read NOT APPLICABLE.
            symlink_config = root / ".aiqt" / "record-sections.toml"
            if symlink_config.is_symlink() or symlink_config.exists():
                symlink_config.unlink()
            os.symlink("missing.toml", str(symlink_config))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                symlink_rc = main(
                    ["--root", str(root), "--post-merge-ref", bad])
            symlink_config.unlink()
            expect("symlinked default config fails closed", symlink_rc, 2)

            # MAJOR C (explicit base with MULTIPLE merge-bases): when target and branch have two real
            # merge-bases that DISAGREE on the section (one contains it, one does not), accepting the
            # section-containing base would let a genuine drop read as pre-existing. An explicit
            # --base-ref is accepted only when the merge-base is unique, matching the default path.
            cxc = Path(directory) / "disagree"
            cxc.mkdir()
            _selftest_git(cxc, ["init", "-q", "-b", "main"])
            _selftest_git(cxc, ["config", "user.name", "AIQT Self-Test"])
            _selftest_git(
                cxc, ["config", "user.email", "selftest@example.invalid"])
            (cxc / ".aiqt").mkdir()
            (cxc / ".aiqt" / "record-sections.toml").write_text(
                'schema-version = 1\n'
                'opt-out-marker = "<!-- aiqt-record-section: superseded -->"'
                '\n\n'
                '[[record]]\n'
                'path = "records.md"\n'
                "heading-pattern = "
                "'^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'\n",
                encoding="utf-8")
            (cxc / "records.md").write_text(no_section, encoding="utf-8")
            _selftest_git(cxc, ["add", "-A"])
            _selftest_git(cxc, ["commit", "-q", "-m", "R, no section"])
            cxc_r = _selftest_git(cxc, ["rev-parse", "HEAD"])
            cxc_r_tree = _selftest_git(cxc, ["rev-parse", "HEAD^{tree}"])
            _selftest_git(cxc, ["checkout", "-q", "-b", "with-section", cxc_r])
            (cxc / "records.md").write_text(with_section, encoding="utf-8")
            _selftest_git(cxc, ["add", "-A"])
            _selftest_git(
                cxc, ["commit", "-q", "-m", "L carries the section"])
            cxc_l = _selftest_git(cxc, ["rev-parse", "HEAD"])
            cxc_l_tree = _selftest_git(cxc, ["rev-parse", "HEAD^{tree}"])
            _selftest_git(cxc, ["checkout", "-q", "-b", "no-section", cxc_r])
            (cxc / "other.txt").write_text("o\n", encoding="utf-8")
            _selftest_git(cxc, ["add", "-A"])
            _selftest_git(cxc, ["commit", "-q", "-m", "Rt, no section"])
            cxc_rt = _selftest_git(cxc, ["rev-parse", "HEAD"])
            cxc_target = _selftest_git(
                cxc,
                ["commit-tree", cxc_r_tree, "-p", cxc_l, "-p", cxc_rt],
                input_text="target merge, no section\n")
            cxc_branch = _selftest_git(
                cxc,
                ["commit-tree", cxc_l_tree, "-p", cxc_rt, "-p", cxc_l],
                input_text="branch merge, with section\n")
            cxc_post = _selftest_git(
                cxc,
                ["commit-tree", cxc_r_tree,
                 "-p", cxc_target, "-p", cxc_branch],
                input_text="post-merge drops the section\n")
            cxc_config = cxc / ".aiqt" / "record-sections.toml"
            expect(
                "disagreeing merge-bases fail closed with no explicit base",
                _quiet_run(cxc, cxc_config, True, cxc_post, None, None),
                2)
            expect(
                "explicit base with multiple merge-bases is rejected",
                _quiet_run(cxc, cxc_config, True, cxc_post, None, cxc_l),
                2)

            # Companion to MAJOR C (unique-merge-base membership): with a UNIQUE merge-base M, an
            # explicit --base-ref that is only a common ancestor (an ancestor of M that still carries
            # the section) is rejected, so it cannot mask a drop where the merge-base is unique.
            cxu = Path(directory) / "unique-base"
            cxu.mkdir()
            _selftest_git(cxu, ["init", "-q", "-b", "main"])
            _selftest_git(cxu, ["config", "user.name", "AIQT Self-Test"])
            _selftest_git(
                cxu, ["config", "user.email", "selftest@example.invalid"])
            (cxu / ".aiqt").mkdir()
            (cxu / ".aiqt" / "record-sections.toml").write_text(
                cxc_config.read_text(encoding="utf-8"), encoding="utf-8")
            (cxu / "records.md").write_text(with_section, encoding="utf-8")
            _selftest_git(cxu, ["add", "-A"])
            _selftest_git(cxu, ["commit", "-q", "-m", "C carries the section"])
            cxu_c = _selftest_git(cxu, ["rev-parse", "HEAD"])
            (cxu / "records.md").write_text(no_section, encoding="utf-8")
            _selftest_git(cxu, ["add", "-A"])
            _selftest_git(cxu, ["commit", "-q", "-m", "M removes the section"])
            cxu_m = _selftest_git(cxu, ["rev-parse", "HEAD"])
            cxu_m_tree = _selftest_git(cxu, ["rev-parse", "HEAD^{tree}"])
            _selftest_git(cxu, ["checkout", "-q", "-b", "u-target", cxu_m])
            (cxu / "u.txt").write_text("u\n", encoding="utf-8")
            _selftest_git(cxu, ["add", "-A"])
            _selftest_git(cxu, ["commit", "-q", "-m", "target change"])
            cxu_target = _selftest_git(cxu, ["rev-parse", "HEAD"])
            _selftest_git(cxu, ["checkout", "-q", "-b", "u-branch", cxu_m])
            (cxu / "records.md").write_text(with_section, encoding="utf-8")
            _selftest_git(cxu, ["add", "-A"])
            _selftest_git(
                cxu, ["commit", "-q", "-m", "branch re-adds the section"])
            cxu_branch = _selftest_git(cxu, ["rev-parse", "HEAD"])
            cxu_post = _selftest_git(
                cxu,
                ["commit-tree", cxu_m_tree,
                 "-p", cxu_target, "-p", cxu_branch],
                input_text="post-merge drops the section\n")
            cxu_config = cxu / ".aiqt" / "record-sections.toml"
            expect(
                "unique merge-base, non-merge-base explicit ancestor rejected",
                _quiet_run(cxu, cxu_config, True, cxu_post, None, cxu_c),
                2)

            failures.extend(_v2_self_test_cases(Path(directory)))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("error: self-test could not build its synthetic git fixture "
              "({}); fail-closed".format(exc), file=sys.stderr)
        return 2

    if failures:
        print("SELF-TEST FAIL: {} case(s)".format(len(failures)),
              file=sys.stderr)
        for failure in failures:
            print("  " + failure, file=sys.stderr)
        return 1
    print("SELF-TEST PASS: preserved and dropped post-merge sections, "
          "squash-like explicit refs, section-local opt-out, "
          "duplicate-heading ambiguity, malformed config, uninferable "
          "branch, absent default adopter config, ambient GIT_ redirection, "
          "config removed by the merge, an unresolvable post-merge ref, a "
          "symlinked config, a symlinked parent component of the config path, "
          "a config path crossing a symlink via an ENOENT-masked '..' prefix or "
          "a lexically non-contained form, an octopus (3+ parent) merge, an "
          "explicit base that is not the unique merge-base, an explicit "
          "--branch-ref disagreeing with the observable second parent, a git "
          "replacement ref rewriting a merge's parents, an on-disk "
          ".git/info/grafts file rewriting a merge's parents, a legitimate "
          "'//abs' config path, an embedded NUL in the config path, and a "
          "symlink cancelled back to the same literal config path all "
          "produce the required 0/1/2 decisions; the schema-version-2 table "
          "leg refuses the exercised dropped and edited row classes (R1 "
          "against the first parent, R2 for branch-owned rows, R3 for frozen "
          "rows against the first parent and the branch tip with type-aware "
          "TOML comparison, a CR-bearing header in any compared snapshot, a "
          "schema downgrade or uninterpretable first-parent config that "
          "would shed declarations, an explicit --base-ref on a "
          "single-parent commit, and the exercised grammar, shape and graph "
          "refusals), shows each row-loss, row-edit, graph and "
          "declaration-scope refusal load-bearing against a mutant with that "
          "check swapped out, and allows reorder, insertion, re-padding of "
          "rows and of the header line, TOML reformatting, an unchanged NaN "
          "value, escaped pipes, key-stable corrections, new tables, a clean "
          "squash, a rows-preserved schema downgrade, and a mixed "
          "heading-and-table config with both legs live, each allowed change "
          "shown failing under a naive variant or discriminated by a paired "
          "refusing fixture")
    return 0


def _v2_branch_commit(root, name, base, writes, message):
    """One fixture branch commit: check out a new branch at base, apply the writes (None deletes), commit."""
    _selftest_git(root, ["checkout", "-q", "-b", name, base])
    for rel, content in writes.items():
        path = root / rel
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    _selftest_git(root, ["add", "-A"])
    _selftest_git(root, ["commit", "-q", "-m", message])
    return _selftest_git(root, ["rev-parse", "HEAD"])


def _v2_synthetic_merge(root, tree_source, parents, message):
    """A post-merge commit with an exact resolved tree, so a fixture controls what the resolver kept."""
    tree = _selftest_git(root, ["rev-parse", "{}^{{tree}}".format(tree_source)])
    args = ["commit-tree", tree]
    for parent in parents:
        args.extend(["-p", parent])
    return _selftest_git(root, args, input_text=message + "\n")


def _v2_self_test_cases(directory):
    """Schema-version-2 [[table]] fixtures: refusal and allowed-change cases for the table leg.

    Refusals guarding a row-loss, row-edit, graph, or declaration-scope class are each shown load-bearing
    by re-running the fixture with the specific check swapped out (_swapped): the refusal vanishes into a
    PASS or, where the fixture also carries a real drop, into the underlying exit-1 finding. Allowed
    changes are each shown deliberate by re-running the fixture under the naive comparison they guard
    against and expecting a failure, or discriminated by a paired refusing fixture on the same surface.
    Grammar and config-shape refusals are asserted as refusals; those whose check is a single swappable
    helper also carry a mutant. Returns failure strings.
    """
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("v2 {}: got {}, want {}".format(name, got, want))

    header = "| id | pr | closed | note |"
    delimiter = "| --- | --- | --- | --- |"
    row_y = "| Y | PR0 | 2026-01-01 | base row |"
    row_x1 = "| X | PR1 | 2026-02-01 | target row |"
    row_x2 = "| X | PR2 | 2026-02-02 | branch row |"

    def md(*rows):
        return ("# Done log\n\n" + header + "\n" + delimiter + "\n"
                + "".join(row + "\n" for row in rows))

    notes_base = '[[done]]\nid = "N1"\npr = "PR0"\nnote = "base note"\n'

    cfg_dir = directory / "v2cfg"
    cfg_dir.mkdir()

    def cfg(name, text):
        path = cfg_dir / name
        path.write_text(text, encoding="utf-8")
        return path

    md_table = ('[[table]]\npath = "done.md"\nformat = "markdown"\n'
                'header = "| id | pr | closed | note |"\nkey = ["id", "pr"]\n')
    cfg_frozen = cfg("frozen.toml",
                     "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n')
    cfg_keystable = cfg("keystable.toml",
                        "schema-version = 2\n\n" + md_table + 'edit = "key-stable"\n')
    cfg_toml = cfg("toml.toml",
                   'schema-version = 2\n\n[[table]]\npath = "notes.toml"\nformat = "toml"\n'
                   'array = "done"\nkey = ["id", "pr"]\nedit = "key-stable"\n')
    cfg_idonly = cfg("idonly.toml",
                     'schema-version = 2\n\n[[table]]\npath = "done.md"\nformat = "markdown"\n'
                     'header = "| id | pr | closed | note |"\nkey = ["id"]\nedit = "key-stable"\n')
    cfg_badheader = cfg("badheader.toml",
                        'schema-version = 2\n\n[[table]]\npath = "done.md"\nformat = "markdown"\n'
                        'header = "| id | status |"\nkey = ["id"]\nedit = "frozen"\n')
    cfg_typokey = cfg("typokey.toml",
                      'schema-version = 2\n\n[[table]]\npath = "done.md"\nformat = "markdown"\n'
                      'header = "| id | pr | closed | note |"\nkey = ["id", "typo"]\nedit = "frozen"\n')
    cfg_two = cfg("two.toml",
                  "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n\n'
                  '[[table]]\npath = "newlog.md"\nformat = "markdown"\n'
                  'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    cfg_tomlbad = cfg("tomlbad.toml",
                      'schema-version = 2\n\n[[table]]\npath = "notes2.toml"\nformat = "toml"\n'
                      'array = "bad"\nkey = ["id"]\nedit = "key-stable"\n')
    cfg_unknown = cfg("unknown.toml",
                      "schema-version = 2\nsurprise = 1\n\n" + md_table + 'edit = "frozen"\n')
    cfg_mixed = cfg("mixed.toml",
                    'schema-version = 2\n'
                    'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n\n'
                    '[[record]]\npath = "mixedrec.md"\n'
                    "heading-pattern = '^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'\n\n"
                    + md_table + 'edit = "frozen"\n')
    cfg_tomlfrozen = cfg("tomlfrozen.toml",
                         'schema-version = 2\n\n[[table]]\npath = "flags.toml"\nformat = "toml"\n'
                         'array = "done"\nkey = ["id"]\nedit = "frozen"\n')
    cfg_tomlnan = cfg("tomlnan.toml",
                      'schema-version = 2\n\n[[table]]\npath = "nanlog.toml"\nformat = "toml"\n'
                      'array = "done"\nkey = ["id"]\nedit = "frozen"\n')

    root = directory / "v2repo"
    root.mkdir()
    _selftest_git(root, ["init", "-q", "-b", "main"])
    _selftest_git(root, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(root, ["config", "user.email", "selftest@example.invalid"])
    (root / "done.md").write_text(md(row_y), encoding="utf-8")
    (root / "notes.toml").write_text(notes_base, encoding="utf-8")
    _selftest_git(root, ["add", "-A"])
    _selftest_git(root, ["commit", "-q", "-m", "fork point"])
    fork = _selftest_git(root, ["rev-parse", "HEAD"])
    (root / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    _selftest_git(root, ["add", "-A"])
    _selftest_git(root, ["commit", "-q", "-m", "target gains X PR1 after the fork"])
    target = _selftest_git(root, ["rev-parse", "HEAD"])

    feat = _v2_branch_commit(root, "v2-feat", fork,
                             {"done.md": md(row_y, row_x2)}, "branch adds X PR2")

    # The seed: the resolver kept the branch's (X, PR2) and replaced the target's (X, PR1).
    m1 = _v2_synthetic_merge(root, feat, [target, feat], "resolver replaced X PR1")
    expect("seed: the target's (X, PR1) row replaced by the branch's (X, PR2)",
           _quiet_run(root, cfg_frozen, True, m1, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("seed passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m1, None, None), 0)
    with _swapped("_r1_base_commit",
                  lambda target_commit, base_commit:
                  base_commit if base_commit is not None else target_commit):
        expect("seed passes when R1 compares the merge-base instead of the first parent (mutant)",
               _quiet_run(root, cfg_frozen, True, m1, None, None), 0)

    # The mirror: the resolver kept (X, PR1) and dropped the branch's (X, PR2).
    m2 = _v2_synthetic_merge(root, target, [target, feat], "resolver dropped X PR2")
    expect("mirror: the branch's (X, PR2) row dropped",
           _quiet_run(root, cfg_frozen, True, m2, None, None), 1)
    with _swapped("_r2_findings",
                  lambda decl, mergebase_rows, branch_rows, post_rows: []):
        expect("mirror passes once the R2 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m2, None, None), 0)

    # Side-by-side control: both rows preserved; id-only keying would refuse this legitimate state.
    m3_tree = _v2_branch_commit(root, "v2-m3tree", target,
                                {"done.md": md(row_y, row_x1, row_x2)}, "both rows kept")
    m3 = _v2_synthetic_merge(root, m3_tree, [target, feat], "both rows preserved")
    expect("control: (X, PR1) and (X, PR2) side by side are both preserved",
           _quiet_run(root, cfg_frozen, True, m3, None, None), 0)
    with _swapped("_key_positions", lambda header_cells, key, where: (0,)):
        expect("side-by-side rows fail under id-only keying (naive variant)",
               _quiet_run(root, cfg_frozen, True, m3, None, None), 2)
    expect("duplicate key under a declared id-only key",
           _quiet_run(root, cfg_idonly, True, m3, None, None), 2)
    with _swapped("_register_key",
                  lambda rows, key, row, where: rows.__setitem__(key, row)):
        expect("duplicate key accepted once the duplicate guard is removed (mutant)",
               _quiet_run(root, cfg_idonly, True, m3, None, None), 0)

    # A plain branch deletion of a base row survives a clean merge only if the gate refuses it.
    deleter = _v2_branch_commit(root, "v2-del", fork, {"done.md": md()},
                                "branch deletes the base row")
    del_tree = _v2_branch_commit(root, "v2-del-tree", target, {"done.md": md(row_x1)},
                                 "clean-merge resolution without Y")
    m_del = _v2_synthetic_merge(root, del_tree, [target, deleter], "clean merge, Y gone")
    expect("a plain branch deletion of a base row",
           _quiet_run(root, cfg_frozen, True, m_del, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("base-row deletion passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m_del, None, None), 0)

    # The resolver ran on the branch (target merged into the branch) before the PR merge.
    resolver_on_branch = _v2_synthetic_merge(root, feat, [feat, target],
                                             "branch merges target, resolver drops X PR1")
    m4 = _v2_synthetic_merge(root, feat, [target, resolver_on_branch], "PR merge")
    expect("resolver ran on the branch before the merge",
           _quiet_run(root, cfg_frozen, True, m4, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("resolver-on-branch passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m4, None, None), 0)

    # A single-parent squash commit, no --branch-ref: R1 alone must still run (and only R1).
    squash_drop = _v2_branch_commit(root, "v2-squash-drop", target,
                                    {"done.md": md(row_y)}, "squash drops X PR1")
    squash_clean = _v2_branch_commit(
        root, "v2-squash-clean", target,
        {"done.md": md(row_y, row_x1, "| Z | PR3 | 2026-02-20 | squash row |")},
        "squash appends Z PR3")
    expect("a single-parent squash dropping a row, no --branch-ref",
           _quiet_run(root, cfg_frozen, True, squash_drop, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("the squash drop passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, squash_drop, None, None), 0)
    expect("a clean single-parent squash, no --branch-ref",
           _quiet_run(root, cfg_frozen, True, squash_clean, None, None), 0)

    def exact_set_r1(decl, base_rows, post_rows):
        if set(post_rows) != set(base_rows):
            return ["{}: the post-merge key set differs from the base key set".format(decl.path)]
        return []

    with _swapped("_r1_findings", exact_set_r1):
        expect("the clean squash fails under exact key-set equality (naive variant)",
               _quiet_run(root, cfg_frozen, True, squash_clean, None, None), 1)

    # An explicit --base-ref on a single-parent commit has no branch graph to validate it against, so it
    # is never silently ignored: it falls through to the graph resolution and fails closed, whether the
    # supplied ref is a genuine ancestor or unresolvable.
    expect("an explicit --base-ref on a single-parent squash fails closed (real ancestor)",
           _quiet_run(root, cfg_frozen, True, squash_clean, None, target), 2)
    expect("an explicit --base-ref on a single-parent squash fails closed (unresolvable ref)",
           _quiet_run(root, cfg_frozen, True, squash_clean, None, "0" * 40), 2)
    with _swapped("_tables_only_squash",
                  lambda parents, b_ref, ba_ref, record_specs:
                  len(parents) == 1 and b_ref is None and not record_specs):
        expect("the explicit base ref is ignored once the squash-path guard drops it (mutant)",
               _quiet_run(root, cfg_frozen, True, squash_clean, None, "0" * 40), 0)

    # Relabelling a key cell is a disappearance of the old key, even in key-stable mode.
    relabel = _v2_branch_commit(
        root, "v2-relabel", target,
        {"done.md": md(row_y, "| X | PR9 | 2026-02-01 | target row |")},
        "key cell PR1 relabelled to PR9")
    m6 = _v2_synthetic_merge(root, relabel, [target, relabel], "relabel lands")
    expect("a relabelled key cell reads as a disappearance in key-stable mode",
           _quiet_run(root, cfg_keystable, True, m6, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("the relabel passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_keystable, True, m6, None, None), 0)

    # A non-key cell edit: refused in frozen mode (R3), allowed in key-stable mode, and the key-stable
    # allowance fails under exact-row identity (the naive variant that cannot tell edits from drops).
    edited = _v2_branch_commit(
        root, "v2-edit", target,
        {"done.md": md("| Y | PR0 | 2026-01-01 | corrected row |", row_x1)},
        "note cell corrected in place")
    m7 = _v2_synthetic_merge(root, edited, [target, edited], "correction lands")
    expect("a frozen row edited in place",
           _quiet_run(root, cfg_frozen, True, m7, None, None), 1)
    with _swapped("_r3_findings", lambda decl, base_rows, post_rows: []):
        expect("the frozen edit passes once the R3 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m7, None, None), 0)
    expect("control: a key-stable row corrected in place",
           _quiet_run(root, cfg_keystable, True, m7, None, None), 0)
    with _swapped("_key_positions",
                  lambda header_cells, key, where: tuple(range(len(header_cells)))):
        expect("the in-place correction fails under exact-row identity (naive variant)",
               _quiet_run(root, cfg_keystable, True, m7, None, None), 1)

    # A blank line inserted mid-table orphans the tail rows out of the table.
    blanked = _v2_branch_commit(
        root, "v2-blank", target,
        {"done.md": "# Done log\n\n" + header + "\n" + delimiter + "\n"
                    + row_y + "\n\n" + row_x1 + "\n"},
        "blank line splits the table")
    m8 = _v2_synthetic_merge(root, blanked, [target, blanked], "split table lands")
    expect("a blank line mid-table orphans the tail rows",
           _quiet_run(root, cfg_frozen, True, m8, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("the orphaned tail passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m8, None, None), 0)

    # The whole declared file deleted: every row disappeared.
    file_deleter = _v2_branch_commit(root, "v2-delete-file", target,
                                     {"done.md": None}, "declared file deleted")
    m10 = _v2_synthetic_merge(root, file_deleter, [target, file_deleter],
                              "deletion lands")
    expect("the declared file deleted",
           _quiet_run(root, cfg_frozen, True, m10, None, None), 1)
    with _swapped("_r1_findings", lambda decl, base_rows, post_rows: []):
        expect("the file deletion passes once the R1 check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m10, None, None), 0)

    # Reordering, mid-table insertion, re-padding, TOML reformatting, and escaped pipes are allowed, and
    # each fails under the naive comparison it was designed against.
    resorted = _v2_branch_commit(root, "v2-resort", target,
                                 {"done.md": md(row_x1, row_y)}, "rows re-sorted")
    mrs = _v2_synthetic_merge(root, resorted, [target, resorted], "re-sort lands")

    def prefix_r1(decl, base_rows, post_rows):
        base_list = list(base_rows.values())
        post_list = list(post_rows.values())
        if post_list[:len(base_list)] != base_list:
            return ["{}: prefix mismatch".format(decl.path)]
        return []

    expect("control: the table re-sorted",
           _quiet_run(root, cfg_frozen, True, mrs, None, None), 0)
    with _swapped("_r1_findings", prefix_r1):
        expect("re-sorting fails under a prefix comparison (naive variant)",
               _quiet_run(root, cfg_frozen, True, mrs, None, None), 1)

    inserted = _v2_branch_commit(
        root, "v2-insert", target,
        {"done.md": md(row_y, "| M | PR5 | 2026-02-10 | inserted |", row_x1)},
        "row inserted mid-table")
    mmi = _v2_synthetic_merge(root, inserted, [target, inserted], "insertion lands")
    expect("control: a row inserted mid-table",
           _quiet_run(root, cfg_frozen, True, mmi, None, None), 0)
    with _swapped("_r1_findings", prefix_r1):
        expect("mid-table insertion fails under a prefix comparison (naive variant)",
               _quiet_run(root, cfg_frozen, True, mmi, None, None), 1)

    repadded = _v2_branch_commit(
        root, "v2-repad", target,
        {"done.md": "# Done log\n\n" + header + "\n| :--- | ---: | :-: | --- |\n"
                    "|  Y   | PR0 | 2026-01-01 |   base row |\n"
                    "|   X | PR1  | 2026-02-01 | target row  |\n"},
        "cells re-padded, alignment row changed")
    mrp = _v2_synthetic_merge(root, repadded, [target, repadded], "re-padding lands")

    def byte_rows(text, decl, where):
        lines = text.split("\n")
        start = _locate_header(lines, decl.header, where)
        if start is None:
            return None
        rows = {}
        for line in lines[start + 2:]:
            if not line.startswith("|"):
                break
            rows[(line,)] = (line,)
        return rows

    expect("control: re-padding and an alignment-row change",
           _quiet_run(root, cfg_frozen, True, mrp, None, None), 0)
    with _swapped("_read_table_rows", byte_rows):
        expect("re-padding fails under a byte comparison (naive variant)",
               _quiet_run(root, cfg_frozen, True, mrp, None, None), 1)

    reformatted = _v2_branch_commit(
        root, "v2-toml-reformat", target,
        {"notes.toml": 'done = [ { note = "base note", pr = "PR0", id = "N1" } ]\n'},
        "TOML reformatted, same parsed rows")
    mtr = _v2_synthetic_merge(root, reformatted, [target, reformatted],
                              "reformatting lands")

    def text_rows(text, decl, where):
        return {(line,): (line,) for line in text.split("\n") if line.strip()}

    expect("control: TOML key order and layout reformatted",
           _quiet_run(root, cfg_toml, True, mtr, None, None), 0)
    with _swapped("_read_table_rows", text_rows):
        expect("TOML reformatting fails under a text comparison (naive variant)",
               _quiet_run(root, cfg_toml, True, mtr, None, None), 1)

    escaped = _v2_branch_commit(
        root, "v2-escaped", target,
        {"done.md": md(row_y, row_x1, "| E1 | PR6 | 2026-03-05 | a \\| b |")},
        "escaped pipe inside a cell")
    mep = _v2_synthetic_merge(root, escaped, [target, escaped], "escaped pipe lands")

    def naive_split(line, where):
        return line.rstrip(" \t").split("|")[1:-1]

    expect("control: an escaped pipe inside a cell",
           _quiet_run(root, cfg_frozen, True, mep, None, None), 0)
    with _swapped("_split_cells", naive_split):
        expect("an escaped pipe fails under a naive split (naive variant)",
               _quiet_run(root, cfg_frozen, True, mep, None, None), 2)

    # The header is matched cell for cell, so re-padding it is as legitimate as re-padding a row, and an
    # exact textual matcher is the naive comparison that would wrongly read the table as absent.
    repadded_header = _v2_branch_commit(
        root, "v2-repad-header", target,
        {"done.md": "# Done log\n\n| id  | pr  | closed     | note       |\n"
                    + delimiter + "\n" + row_y + "\n" + row_x1 + "\n"},
        "header re-padded, rows intact")
    expect("control: a re-padded header still locates the declared table",
           _quiet_run(root, cfg_frozen, True, repadded_header, None, None), 0)

    def exact_locate(lines, table_header, where):
        matches = [index for index, line in enumerate(lines) if line == table_header]
        if len(matches) > 1:
            raise GateError("{}: the declared header line appears {} times"
                            .format(where, len(matches)))
        if not matches:
            return None
        return matches[0]

    with _swapped("_locate_header", exact_locate):
        expect("the re-padded header fails under exact textual matching (naive variant)",
               _quiet_run(root, cfg_frozen, True, repadded_header, None, None), 2)

    # A CR-bearing header (a CRLF table) must fail closed at EVERY compared snapshot: reading it as "table
    # absent" would silently empty the row obligations for that snapshot and let a simultaneous drop pass.
    # The mutant locates the header with its CRs ignored, so the refusal vanishes into a PASS where no row
    # was lost and into the underlying exit-1 finding where one was.
    def cr_tolerant_locate(lines, table_header, where):
        matches = [index for index, line in enumerate(lines)
                   if line.replace("\r", "") == table_header]
        if len(matches) > 1:
            raise GateError("{}: the declared header line appears {} times"
                            .format(where, len(matches)))
        if not matches:
            return None
        return matches[0]

    cr_header_md = ("# Done log\n\n" + header + "\r\n" + delimiter + "\n"
                    + row_y + "\n" + row_x1 + "\n")
    cr_base = _v2_branch_commit(root, "v2-cr-header-base", target,
                                {"done.md": cr_header_md},
                                "CR-terminated header at the first parent")
    cr_clean = _v2_branch_commit(root, "v2-cr-header-clean", cr_base,
                                 {"done.md": md(row_y, row_x1)},
                                 "normalized to LF, no row lost")
    expect("a CR-bearing header at the first parent fails closed",
           _quiet_run(root, cfg_frozen, True, cr_clean, None, None), 2)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the clean normalization passes once the header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, cr_clean, None, None), 0)
    cr_drop = _v2_branch_commit(root, "v2-cr-header-drop", cr_base,
                                {"done.md": md(row_y)},
                                "normalized to LF, a row dropped")
    expect("a CR-bearing header at the first parent fails closed before a drop can hide",
           _quiet_run(root, cfg_frozen, True, cr_drop, None, None), 2)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the hidden drop surfaces as a finding once the header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, cr_drop, None, None), 1)
    cr_feat = _v2_branch_commit(root, "v2-cr-branch", fork,
                                {"done.md": "# Done log\n\n" + header + "\r\n" + delimiter + "\n"
                                            + row_y + "\n" + row_x2 + "\n"},
                                "CR-terminated header on the branch")
    m_crb = _v2_synthetic_merge(root, target, [target, cr_feat],
                                "merge keeps the target file, branch row gone")
    expect("a CR-bearing header at the branch tip fails closed",
           _quiet_run(root, cfg_frozen, True, m_crb, None, None), 2)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the dropped branch row surfaces once the header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m_crb, None, None), 1)
    cr_mb_fork = _v2_branch_commit(root, "v2-cr-mb-fork", target,
                                   {"done.md": cr_header_md},
                                   "CR-terminated header at the fork")
    cr_mb_t = _v2_branch_commit(root, "v2-cr-mb-target", cr_mb_fork,
                                {"done.md": md(row_y, row_x1)}, "target normalizes to LF")
    cr_mb_b = _v2_branch_commit(root, "v2-cr-mb-branch", cr_mb_fork,
                                {"done.md": md(row_y, row_x1, row_x2)}, "branch adds a row")
    m_crmb = _v2_synthetic_merge(root, cr_mb_b, [cr_mb_t, cr_mb_b],
                                 "merge keeps every row")
    expect("a CR-bearing header at the merge-base fails closed",
           _quiet_run(root, cfg_frozen, True, m_crmb, None, None), 2)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the clean merge passes once the merge-base header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m_crmb, None, None), 0)
    cr_post = _v2_branch_commit(root, "v2-cr-post", target,
                                {"done.md": cr_header_md},
                                "CR-terminated header post-merge")
    expect("a CR-bearing header post-merge stays fail-closed",
           _quiet_run(root, cfg_frozen, True, cr_post, None, None), 2)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the post-merge CR header parses once the CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, cr_post, None, None), 0)

    # Frozen rows compare by parsed value with type-aware equality: plain Python equality would let a
    # boolean/integer or integer/float substitution (including a nested one) land unnoticed, and would
    # read an untouched NaN as an edit.
    flags_base_text = ('[[done]]\nid = "F1"\nflag = true\nscore = 1\n'
                       'meta = { deep = true }\n')
    flags_target = _v2_branch_commit(root, "v2-flags", target,
                                     {"flags.toml": flags_base_text},
                                     "typed TOML rows adopted")
    for case_name, branch_name, edited_text in (
            ("a frozen TOML boolean cell edited to the equal-comparing integer",
             "v2-flags-boolint",
             '[[done]]\nid = "F1"\nflag = 1\nscore = 1\nmeta = { deep = true }\n'),
            ("a frozen TOML integer cell edited to the equal-comparing float",
             "v2-flags-intfloat",
             '[[done]]\nid = "F1"\nflag = true\nscore = 1.0\nmeta = { deep = true }\n'),
            ("a nested frozen TOML value edited to the equal-comparing type",
             "v2-flags-nested",
             '[[done]]\nid = "F1"\nflag = true\nscore = 1\nmeta = { deep = 1 }\n')):
        edited_commit = _v2_branch_commit(root, branch_name, flags_target,
                                          {"flags.toml": edited_text}, branch_name)
        m_typed = _v2_synthetic_merge(root, edited_commit, [flags_target, edited_commit],
                                      "typed edit lands")
        expect(case_name,
               _quiet_run(root, cfg_tomlfrozen, True, m_typed, None, None), 1)
        with _swapped("_row_values_equal", lambda before, after: before == after):
            expect(case_name + " passes under plain equality (mutant)",
                   _quiet_run(root, cfg_tomlfrozen, True, m_typed, None, None), 0)
    nan_base_text = '[[done]]\nid = "N1"\nscore = nan\n'
    nan_target = _v2_branch_commit(root, "v2-nan", target,
                                   {"nanlog.toml": nan_base_text}, "NaN row adopted")
    nan_append = _v2_branch_commit(root, "v2-nan-append", nan_target,
                                   {"nanlog.toml": nan_base_text + '\n[[done]]\nid = "N2"\n'},
                                   "row appended, NaN row untouched")
    m_nan = _v2_synthetic_merge(root, nan_append, [nan_target, nan_append], "append lands")
    expect("control: an unchanged frozen TOML NaN value is not an edit",
           _quiet_run(root, cfg_tomlnan, True, m_nan, None, None), 0)
    with _swapped("_row_values_equal", lambda before, after: before == after):
        expect("the unchanged NaN fails under plain equality (naive variant)",
               _quiet_run(root, cfg_tomlnan, True, m_nan, None, None), 1)

    # A frozen table protects branch-owned rows too: a resolver that keeps the branch row's key but
    # rewrites its content must be refused, while key-stable mode allows the same correction.
    branch_rewrite_tree = _v2_branch_commit(
        root, "v2-branch-rewrite", target,
        {"done.md": md(row_y, row_x1, "| X | PR2 | 2026-02-02 | REWRITTEN |")},
        "tree with the branch row rewritten")
    m_br = _v2_synthetic_merge(root, branch_rewrite_tree, [target, feat],
                               "resolver rewrites the branch-owned frozen row")
    expect("a branch-owned frozen row edited by the resolver",
           _quiet_run(root, cfg_frozen, True, m_br, None, None), 1)
    with _swapped("_r3_branch_findings",
                  lambda decl, mergebase_rows, branch_rows, post_rows: []):
        expect("the branch-row edit passes once the branch-side frozen check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m_br, None, None), 0)
    expect("control: a branch-owned key-stable row corrected by the resolver",
           _quiet_run(root, cfg_keystable, True, m_br, None, None), 0)

    # A table introduced since the base has nothing for R1; R2 still covers its branch-owned rows.
    new_table = _v2_branch_commit(
        root, "v2-newtable", target,
        {"newlog.md": "| id | pr |\n| --- | --- |\n| L1 | PR4 |\n"},
        "new declared table introduced on the branch")
    mnt = _v2_synthetic_merge(root, new_table, [target, new_table], "new table lands")
    expect("control: a table introduced since the base",
           _quiet_run(root, cfg_two, True, mnt, None, None), 0)
    true_tree_text = _tree_text

    def strict_tree_text(root_path, commit, path):
        text = true_tree_text(root_path, commit, path)
        if text is None:
            raise GateError("{} is absent at {}".format(path, commit))
        return text

    with _swapped("_tree_text", strict_tree_text):
        expect("the introduced table fails when absence at the base is an error (naive variant)",
               _quiet_run(root, cfg_two, True, mnt, None, None), 2)

    # No config now and none at the pre-merge target stays NOT APPLICABLE.
    expect("control: no config now or at the target is not applicable",
           _quiet_run(root, root / ".aiqt" / "absent.toml", False, m3, None, None), 0)
    with _swapped("_config_removed_by_merge",
                  lambda root_path, config_rel, post_ref: True):
        expect("the not-applicable path fails when absence reads as a removal (naive variant)",
               _quiet_run(root, root / ".aiqt" / "absent.toml", False, m3, None, None), 2)

    # Ambiguous or malformed inputs fail closed, and each refusal is load-bearing.
    expect("the declared header absent from a present file",
           _quiet_run(root, cfg_badheader, True, m3, None, None), 2)
    with _swapped("_require_present",
                  lambda rows, decl, where: {} if rows is None else rows):
        expect("the absent table passes once the presence check is removed (mutant)",
               _quiet_run(root, cfg_badheader, True, m3, None, None), 0)

    doubled = _v2_branch_commit(
        root, "v2-doubled-header", target,
        {"done.md": md(row_y, row_x1) + "\n```\n" + header + "\n```\n"},
        "header duplicated inside a code fence")
    m11 = _v2_synthetic_merge(root, doubled, [target, doubled], "doubled header lands")
    expect("the header present twice (once inside a code fence)",
           _quiet_run(root, cfg_frozen, True, m11, None, None), 2)
    with _swapped("_locate_header",
                  lambda lines, table_header, where:
                  lines.index(table_header) if table_header in lines else None):
        expect("the doubled header passes once the uniqueness check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m11, None, None), 0)

    expect("a key column missing from the header",
           _quiet_run(root, cfg_typokey, True, mrs, None, None), 2)
    with _swapped("_key_positions",
                  lambda header_cells, key, where:
                  tuple(header_cells.index(name) for name in key if name in header_cells)):
        expect("the missing key column passes once the column check is removed (mutant)",
               _quiet_run(root, cfg_typokey, True, mrs, None, None), 0)

    toml_missing = _v2_branch_commit(
        root, "v2-toml-missing", target,
        {"notes.toml": notes_base + '\n[[done]]\nid = "N2"\n'},
        "TOML row missing a key field")
    mtm = _v2_synthetic_merge(root, toml_missing, [target, toml_missing],
                              "missing key field lands")
    expect("a TOML row missing a key column",
           _quiet_run(root, cfg_toml, True, mtm, None, None), 2)
    with _swapped("_toml_row_key",
                  lambda row, key, where:
                  tuple(str(row.get(name, "")) for name in key)):
        expect("the missing TOML key field passes once the per-row check is removed (mutant)",
               _quiet_run(root, cfg_toml, True, mtm, None, None), 0)

    extra_cell = _v2_branch_commit(
        root, "v2-extra-cell", target,
        {"done.md": md(row_y, row_x1, "| W | PR7 | 2026-03-01 | extra | spill |")},
        "row with one cell too many")
    mwc = _v2_synthetic_merge(root, extra_cell, [target, extra_cell],
                              "extra cell lands")
    expect("a row with one cell too many",
           _quiet_run(root, cfg_frozen, True, mwc, None, None), 2)
    with _swapped("_check_cell_count", lambda count, expected, where: None):
        expect("the wrong cell count passes once the count check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, mwc, None, None), 0)
    few_cells = _v2_branch_commit(
        root, "v2-few-cells", target,
        {"done.md": md(row_y, row_x1, "| V | PR8 | 2026-03-03 |")},
        "row with one cell too few")
    mwf = _v2_synthetic_merge(root, few_cells, [target, few_cells],
                              "missing cell lands")
    expect("a row with one cell too few",
           _quiet_run(root, cfg_frozen, True, mwf, None, None), 2)
    with _swapped("_check_cell_count", lambda count, expected, where: None):
        expect("the missing cell passes once the count check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, mwf, None, None), 0)

    cr_row = _v2_branch_commit(
        root, "v2-cr", target,
        {"done.md": md(row_y, row_x1, "| C | PR9 | 2026-03-02 | a\rb |")},
        "CR inside a table line")
    mcr = _v2_synthetic_merge(root, cr_row, [target, cr_row], "CR lands")
    expect("a CR inside a table line",
           _quiet_run(root, cfg_frozen, True, mcr, None, None), 2)
    with _swapped("_check_no_cr", lambda line, where: None):
        expect("the CR passes once the CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, mcr, None, None), 0)

    bad_array = _v2_branch_commit(root, "v2-toml-badarray", target,
                                  {"notes2.toml": "bad = [1, 2]\n"},
                                  "declared TOML array of non-tables")
    mtb = _v2_synthetic_merge(root, bad_array, [target, bad_array],
                              "bad array lands")
    expect("a declared TOML array that is not an array of tables",
           _quiet_run(root, cfg_tomlbad, True, mtb, None, None), 2)
    with _swapped("_toml_rows_array",
                  lambda data, array, where:
                  ([row for row in data[array] if isinstance(row, dict)]
                   if isinstance(data, dict) and array in data
                   and isinstance(data[array], list) else None)):
        expect("the non-table array passes once the shape check is removed (mutant)",
               _quiet_run(root, cfg_tomlbad, True, mtb, None, None), 0)

    non_scalar = _v2_branch_commit(
        root, "v2-toml-nonscalar", target,
        {"notes.toml": notes_base + '\n[[done]]\nid = "N3"\npr = ["PR1"]\nnote = "x"\n'},
        "non-scalar key value")
    mts = _v2_synthetic_merge(root, non_scalar, [target, non_scalar],
                              "non-scalar key value lands")
    expect("a non-scalar TOML key value",
           _quiet_run(root, cfg_toml, True, mts, None, None), 2)
    with _swapped("_toml_row_value",
                  lambda value, where:
                  value if type(value) in (str, int) else repr(value)):
        expect("the non-scalar key value passes once the scalar check is removed (mutant)",
               _quiet_run(root, cfg_toml, True, mts, None, None), 0)

    expect("a version-2 config with unknown top-level keys",
           _quiet_run(root, cfg_unknown, True, m3, None, None), 2)
    with _swapped("_check_v2_top_keys", lambda data, path: None):
        expect("the unknown config key passes once the key check is removed (mutant)",
               _quiet_run(root, cfg_unknown, True, m3, None, None), 0)

    # Grammar refusals on declared files, asserted on single-parent fixtures (the first parent's copy is
    # well-formed, the post-merge copy is not), plus a parentless commit.
    toml_garbled = _v2_branch_commit(root, "v2-toml-garbled", target,
                                     {"notes.toml": "= not toml\n"},
                                     "declared TOML file unparseable")
    expect("an unparseable declared TOML file fails closed",
           _quiet_run(root, cfg_toml, True, toml_garbled, None, None), 2)
    headless = _v2_branch_commit(root, "v2-no-delimiter", target,
                                 {"done.md": "# Done log\n\n" + header},
                                 "header with no delimiter row")
    expect("a header with no delimiter row fails closed",
           _quiet_run(root, cfg_frozen, True, headless, None, None), 2)
    bad_delim = _v2_branch_commit(root, "v2-bad-delimiter", target,
                                  {"done.md": "# Done log\n\n" + header
                                              + "\n| x | y | z | w |\n" + row_y + "\n"},
                                  "line after the header is not a delimiter row")
    expect("a non-delimiter line after the header fails closed",
           _quiet_run(root, cfg_frozen, True, bad_delim, None, None), 2)
    dup_toml_key = _v2_branch_commit(root, "v2-toml-dup", target,
                                     {"notes.toml": notes_base + "\n" + notes_base},
                                     "duplicate TOML row key")
    expect("a duplicate TOML row key fails closed",
           _quiet_run(root, cfg_toml, True, dup_toml_key, None, None), 2)
    expect("a root (parentless) post-merge commit fails closed",
           _quiet_run(root, cfg_frozen, True, fork, None, None), 2)
    root_parents = _parents
    with _swapped("_parents",
                  lambda root_path, commit: root_parents(root_path, commit) or [target]):
        expect("the root commit is evaluated once the parent guard invents a parent (mutant)",
               _quiet_run(root, cfg_frozen, True, fork, None, None), 1)

    # Config-shape refusals asserted directly against load_config.
    load_refusals = []

    def expect_load_refusal(label, text):
        load_refusals.append(label)
        path = cfg("refuse-{}.toml".format(len(load_refusals)), text)
        try:
            load_config(path)
            outcome = "accepted"
        except GateError:
            outcome = "refused"
        expect(label, outcome, "refused")

    expect_load_refusal("a version-2 config with neither record kind", "schema-version = 2\n")
    expect_load_refusal("[[record]] entries without opt-out-marker",
                        'schema-version = 2\n\n[[record]]\npath = "records.md"\n'
                        "heading-pattern = '^## .+$'\n")
    expect_load_refusal("an unknown [[table]] format",
                        'schema-version = 2\n\n[[table]]\npath = "d.md"\nformat = "csv"\n'
                        'header = "| id |"\nkey = ["id"]\nedit = "frozen"\n')
    expect_load_refusal("an unknown [[table]] edit mode",
                        "schema-version = 2\n\n" + md_table + 'edit = "sometimes"\n')
    expect_load_refusal("an empty [[table]] key list",
                        'schema-version = 2\n\n[[table]]\npath = "d.md"\nformat = "markdown"\n'
                        'header = "| id |"\nkey = []\nedit = "frozen"\n')
    expect_load_refusal("a header that is not a |-prefixed line",
                        'schema-version = 2\n\n[[table]]\npath = "d.md"\nformat = "markdown"\n'
                        'header = "id |"\nkey = ["id"]\nedit = "frozen"\n')
    expect_load_refusal("a TOML array name carrying a newline",
                        'schema-version = 2\n\n[[table]]\npath = "d.toml"\nformat = "toml"\n'
                        'array = "a\\nb"\nkey = ["id"]\nedit = "frozen"\n')
    expect_load_refusal("an unknown key inside [[table]]",
                        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\nextra = 1\n')
    expect_load_refusal("a duplicate declared table path",
                        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n\n'
                        + md_table + 'edit = "frozen"\n')

    # The graph handling is shared with the heading leg: a version-2 config mixing both kinds works on a
    # two-parent merge with BOTH legs live (a dropped heading and a dropped row each fail), keeps the
    # version-1 graph rules when [[record]] entries are present, and an octopus merge stays fail-closed
    # for the table leg too. The mixed config selects a real heading so these fixtures would catch a
    # defect that skips heading preservation whenever tables are declared.
    mixed_rec_base = "# Mixed record\n\n## 2026-08-01 Existing\nbase\n"
    mixed_rec_branch = mixed_rec_base + "\n## 2026-09-01 Mixed item\nbranch\n"
    mix_base = _v2_branch_commit(root, "v2-mix-base", target,
                                 {"mixedrec.md": mixed_rec_base}, "mixed record adopted")
    mix_feat = _v2_branch_commit(root, "v2-mix-feat", mix_base,
                                 {"mixedrec.md": mixed_rec_branch,
                                  "done.md": md(row_y, row_x1, row_x2)},
                                 "branch adds a heading and a row")
    m_mix_good = _v2_synthetic_merge(root, mix_feat, [mix_base, mix_feat],
                                     "mixed merge keeps both")
    expect("a version-2 config mixing [[record]] and [[table]] with both preserved",
           _quiet_run(root, cfg_mixed, True, m_mix_good, None, None), 0)
    mix_drop_heading = _v2_branch_commit(root, "v2-mix-drop-heading", mix_feat,
                                         {"mixedrec.md": mixed_rec_base},
                                         "tree without the branch heading")
    m_mix_dh = _v2_synthetic_merge(root, mix_drop_heading, [mix_base, mix_feat],
                                   "mixed merge drops the heading")
    expect("the heading leg stays live under a mixed config (dropped heading fails)",
           _quiet_run(root, cfg_mixed, True, m_mix_dh, None, None), 1)
    mix_drop_row = _v2_branch_commit(root, "v2-mix-drop-row", mix_feat,
                                     {"done.md": md(row_y, row_x1)},
                                     "tree without the branch row")
    m_mix_dr = _v2_synthetic_merge(root, mix_drop_row, [mix_base, mix_feat],
                                   "mixed merge drops the row")
    expect("the table leg stays live under a mixed config (dropped row fails)",
           _quiet_run(root, cfg_mixed, True, m_mix_dr, None, None), 1)
    mix_squash = _v2_branch_commit(root, "v2-mix-squash", mix_base,
                                   {"done.md": md(row_y, row_x1,
                                                  "| Q | PR10 | 2026-03-04 | squash row |")},
                                   "single-parent commit under the mixed config")
    expect("[[record]] entries keep the branch-graph requirement on a single parent",
           _quiet_run(root, cfg_mixed, True, mix_squash, None, None), 2)
    with _swapped("resolve_graph",
                  lambda root_path, post_ref, b_ref=None, ba_ref=None:
                  (mix_base, mix_squash, mix_squash)):
        expect("the single-parent mixed config passes once the graph requirement is removed (mutant)",
               _quiet_run(root, cfg_mixed, True, mix_squash, None, None), 0)
    octopus = _v2_synthetic_merge(root, m3_tree, [target, feat, deleter],
                                  "octopus merge, no row dropped")
    expect("an octopus merge fails closed for the table leg",
           _quiet_run(root, cfg_frozen, True, octopus, None, None), 2)
    true_parents = _parents
    with _swapped("_parents",
                  lambda root_path, commit: true_parents(root_path, commit)[:2]):
        expect("the octopus passes once the parent-count guard sees two parents (mutant)",
               _quiet_run(root, cfg_frozen, True, octopus, None, None), 0)

    # A merge that deletes a [[table]] declaration and that table's rows together: the first parent's
    # config still governs R1, so the drop is refused unless the base-config read is removed.
    nine = directory / "v2nine"
    nine.mkdir()
    _selftest_git(nine, ["init", "-q", "-b", "main"])
    _selftest_git(nine, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(nine, ["config", "user.email", "selftest@example.invalid"])
    (nine / ".aiqt").mkdir()
    extra_decl = ('[[table]]\npath = "extra.md"\nformat = "markdown"\n'
                  'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    (nine / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n\n' + extra_decl,
        encoding="utf-8")
    (nine / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    (nine / "extra.md").write_text("| id | pr |\n| --- | --- |\n| A1 | PR0 |\n",
                                   encoding="utf-8")
    _selftest_git(nine, ["add", "-A"])
    _selftest_git(nine, ["commit", "-q", "-m", "adopted config and tables"])
    nine_base = _selftest_git(nine, ["rev-parse", "HEAD"])
    nine_narrow = _v2_branch_commit(
        nine, "v2-narrow", nine_base,
        {".aiqt/record-sections.toml": "schema-version = 2\n\n" + extra_decl,
         "done.md": md(row_y)},
        "declaration and row removed together")
    m9 = _v2_synthetic_merge(nine, nine_narrow, [nine_base, nine_narrow],
                             "narrowing merge lands")
    nine_config = nine / ".aiqt" / "record-sections.toml"
    expect("a merge removing a [[table]] declaration and its rows together",
           _quiet_run(nine, nine_config, True, m9, None, None), 1)
    with _swapped("_base_config_tables",
                  lambda root_path, config_path, target_commit: ()):
        expect("the declaration removal passes once the base-config read is removed (mutant)",
               _quiet_run(nine, nine_config, True, m9, None, None), 0)

    # A merge that downgrades the config to schema-version 1 while dropping rows: version 1 cannot
    # declare tables, so without the first parent's declarations the drop would read as a plain
    # version-1 run and pass. A downgrade that keeps every row stays allowed.
    down = directory / "v2down"
    down.mkdir()
    _selftest_git(down, ["init", "-q", "-b", "main"])
    _selftest_git(down, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(down, ["config", "user.email", "selftest@example.invalid"])
    (down / ".aiqt").mkdir()
    (down / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n', encoding="utf-8")
    (down / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    (down / "records.md").write_text("# R\n\n## 2026-08-01 Existing\nbase\n",
                                     encoding="utf-8")
    _selftest_git(down, ["add", "-A"])
    _selftest_git(down, ["commit", "-q", "-m", "version-2 table config adopted"])
    down_base = _selftest_git(down, ["rev-parse", "HEAD"])
    v1_config = ('schema-version = 1\n'
                 'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n\n'
                 '[[record]]\npath = "records.md"\n'
                 "heading-pattern = '^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'\n")
    down_drop = _v2_branch_commit(
        down, "v2-downgrade-drop", down_base,
        {".aiqt/record-sections.toml": v1_config, "done.md": md(row_y)},
        "config downgraded to version 1, a row dropped")
    m_down = _v2_synthetic_merge(down, down_drop, [down_base, down_drop],
                                 "downgrade merge drops a row")
    down_config = down / ".aiqt" / "record-sections.toml"
    expect("a merge downgrading the config to version 1 while dropping a row",
           _quiet_run(down, down_config, True, m_down, None, None), 1)
    with _swapped("_base_config_tables",
                  lambda root_path, config_path, target_commit: ()):
        expect("the downgrade drop passes once the base-config read is removed (mutant)",
               _quiet_run(down, down_config, True, m_down, None, None), 0)
    down_keep = _v2_branch_commit(
        down, "v2-downgrade-keep", down_base,
        {".aiqt/record-sections.toml": v1_config},
        "config downgraded to version 1, rows kept")
    m_down_keep = _v2_synthetic_merge(down, down_keep, [down_base, down_keep],
                                      "downgrade merge keeps every row")
    expect("control: a downgrade that keeps every row passes",
           _quiet_run(down, down_config, True, m_down_keep, None, None), 0)

    # A first-parent config the gate cannot interpret (here schema-version 3) may carry declarations it
    # cannot enforce, so it fails closed rather than contributing no tables, which would let the same
    # merge narrow the declarations and drop the rows.
    vthree = directory / "v2vthree"
    vthree.mkdir()
    _selftest_git(vthree, ["init", "-q", "-b", "main"])
    _selftest_git(vthree, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(vthree, ["config", "user.email", "selftest@example.invalid"])
    (vthree / ".aiqt").mkdir()
    (vthree / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 3\n\n" + md_table + 'edit = "frozen"\n', encoding="utf-8")
    (vthree / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    _selftest_git(vthree, ["add", "-A"])
    _selftest_git(vthree, ["commit", "-q", "-m", "unknown schema version at the first parent"])
    vthree_base = _selftest_git(vthree, ["rev-parse", "HEAD"])
    vthree_drop = _v2_branch_commit(
        vthree, "v2-vthree-drop", vthree_base,
        {".aiqt/record-sections.toml":
             'schema-version = 2\n\n[[table]]\npath = "newlog.md"\nformat = "markdown"\n'
             'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n',
         "done.md": md(row_y)},
        "declarations narrowed under an uninterpretable first-parent config, a row dropped")
    vthree_config = vthree / ".aiqt" / "record-sections.toml"
    expect("an uninterpretable first-parent config version fails closed",
           _quiet_run(vthree, vthree_config, True, vthree_drop, None, None), 2)
    with _swapped("_base_config_tables",
                  lambda root_path, config_path, target_commit: ()):
        expect("the uninterpretable first-parent config is skipped once the read is removed (mutant)",
               _quiet_run(vthree, vthree_config, True, vthree_drop, None, None), 0)

    return failures


def _parser():
    parser = argparse.ArgumentParser(
        description="check post-merge preservation of configured record sections")
    parser.add_argument(
        "--root",
        help="repository root (default: discovered from this tool)")
    parser.add_argument(
        "--config",
        help="repo-relative config path (default: {})".format(CONFIG_REL))
    parser.add_argument(
        "--post-merge-ref",
        default="HEAD",
        help="post-merge commit ref (default: HEAD)")
    parser.add_argument(
        "--branch-ref",
        help="branch tip ref; inferred from the second parent when omitted")
    parser.add_argument(
        "--base-ref",
        help="merge-base ref; computed from target and branch when omitted. An explicit value is accepted "
             "only when the merge-base is unique and the value names it; a non-unique merge-base or a mere "
             "common ancestor is rejected")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run deterministic synthetic fixtures")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.self_test:
        return self_test()
    try:
        root = (_repository_root(Path(args.root))
                if args.root else
                _repository_root(Path(__file__).resolve().parents[1]))
        config_path = _contained_config_path(
            root, args.config or CONFIG_REL)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return run(
        root,
        config_path,
        args.config is not None,
        args.post_merge_ref,
        args.branch_ref,
        args.base_ref)


if __name__ == "__main__":
    sys.exit(main())
