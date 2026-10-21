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
GFM delimiter row; one config declares at most one table per file (a duplicate table path in a config is
refused; the working config and the first parent's combined can still put two declared tables, under
different headers or arrays, in one file); the table's rows are the consecutive following lines that
start with `|`, and each row line must
also end with an unescaped `|` (a GFM-style row without the trailing pipe is refused); the first line that
does not start with `|` ends the rows, and it is exit 2 when it still carries an unescaped `|` (an indented
row, or a row without its leading pipe, which GFM renders as a further row); cells are split
on unescaped `|` and trimmed of surrounding ASCII spaces and tabs ONLY, matching the GFM cell grammar, so
a cell edited only in other Unicode whitespace (a no-break space) is a real edit, not a re-padding; the
cell count must equal the header's cell count;
lines are split on LF only, and a CR inside a table line or in a line matching the declared header in ANY
compared snapshot (post-merge, first parent, branch tip, or merge-base) is exit 2 (each candidate line is
CR-stripped BEFORE the leading-pipe test, so a CR-prefixed header line is caught too), so a CRLF table
fails closed rather than reading as absent. A declared table the header search cannot locate at the first
parent or at the branch tip, while the file exists there and that snapshot's own config declared the table
(CR-only line endings, an indented table, or any other unlocatable form), is exit 2 naming the file and
snapshot, never read as the table not existing yet; at the branch tip the merge-base's config counts as
declaring too, so a branch that renamed or removed a table both sides had declared (its own config and
file no longer carry the declared form) fails closed rather than shedding the branch's own row
obligations, while a branch forked before the table was declared, where neither the branch tip's config
nor the merge-base's declares it, still reads as not-yet. A declared table's file ABSENT at the branch
tip while it existed at the merge-base and the merge-base's config declared the table fails closed the
same way (exit 2): the branch itself moved or deleted the declared file, and rows it re-added under
another path are invisible to this declaration, so reading the table as absent would shed the branch's
own row obligations. Only a file genuinely absent at a snapshot (at the branch tip, only when it was
also absent at the merge-base or the merge-base's config did not declare the table), or an existing
file whose applicable configs had not yet declared the table, is skippable. A snapshot's
config declared the table when it declares the same path, format, and header cells (Markdown) or array
name (TOML); a declaration of another table at the same path (another array, another header) does not
count, so a new table added beside a declared one reads as not-yet at the snapshots that did not declare
it, while the earlier declaration stays in force for its own table. For a TOML table, rows
are the declared
top-level array, which must be an array of tables whose key fields are present strings or integers, compared
by parsed value so key order and layout do not matter. A row's identity is the tuple of its `key` columns; a
duplicate key in any snapshot is exit 2, matching the duplicate-heading rule.

Table invariants. R1 (base-row preservation): every key present at the POST-MERGE COMMIT'S FIRST PARENT (the
pre-merge target, never the merge-base, which usually predates a row the target gained after the fork) is
present in the post-merge commit; the table declarations for R1 come from that first parent's config at the
same repo-relative path TOGETHER WITH the working config, WHATEVER schema version the working config
declares, so one merge cannot remove a table's declaration and drop its rows at the same time, and a
schema downgrade to version 1 in the same merge keeps the first parent's table obligations in force. A
version-1 working config behaves exactly as before only while the first parent's config is absent, or is
valid and declares no tables: the first parent's config is read and FULLY validated on every run through
the same parser and shape checks as the working config (shared code, not a second looser copy), so a
first-parent config that is present but is not a valid schema-version-1 or schema-version-2 config fails
closed, whatever the working config's version, because the declarations it may carry cannot be known.
Declarations come from the working config and the first parent's config only; the branch tip's config and
the merge-base's are read solely to decide whether an unlocatable branch-tip table, or a declared file
absent at the branch tip while present at the merge-base, fails closed, never to add obligations. R1 needs only the post-merge commit and its first
parent, so a version-2 config declaring only tables is evaluable on a single-parent (squash) commit with
no --branch-ref and no --base-ref (an explicit --base-ref cannot be validated without a branch graph, so
it fails closed there); [[record]] entries keep the version-1 graph requirements. R2 (the row analogue of
the heading leg): every key on the branch tip that is absent at the merge-base is present in the post-merge
commit. R3 (edit = "frozen" only): a key present at both the first parent and the post-merge commit with
any cell changed is a finding, and a branch-owned key surviving into the post-merge commit with any cell
changed from its branch-tip content is a finding too. Cells and TOML values compare by parsed value with
type-aware equality: an integer never equals a boolean or a float (so such a substitution is an edit), and
floats and datetimes additionally compare by type plus exact repr, floats by their sign bit too, so a
signed-zero substitution (0.0 to -0.0), a NaN sign flip (nan to -nan), and a timezone-aware instant
rewritten in another UTC offset are edits, while an unchanged NaN is not an edit and a spelling change
that parses to the identical value (Z versus +00:00, nan versus +nan) is not one either. Reordering,
appending or inserting rows anywhere, re-padding or alignment
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
  2  malformed or unreadable config/input (a heading-pattern that cannot be compiled included, whatever
     the compiler raises), a missing, duplicate or misshapen declared table, a declared
     table that cannot be located at the first parent or the branch tip while the file exists there and
     that snapshot's config declared it (at the branch tip a declaration in the merge-base's config
     counts too), a declared table's file absent at the branch tip while it existed at the merge-base
     and the merge-base's config declared the table, a duplicate
     row key in any snapshot, a missing key column, a CR in a table line or in a header-matching line of
     any compared snapshot, a line directly after a table's rows that carries a pipe without starting
     with one, an explicit --base-ref on a single-parent commit with no branch ref, a
     first-parent config that is not a valid version-1 or version-2 config, a branch-tip or merge-base
     config that is not a valid version-1 or version-2 config when a declared table cannot be located at
     the branch tip, a merge-base config that is not one when a declared table's file is absent at the
     branch tip while present at the merge-base (the only cases that read either of those snapshots'
     configs), a declared TOML array
     that
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
not rendered. A non-blank line with no unescaped pipe directly after the rows ends the table although
GFM would render it as a further row, so a row placed after such a line is not tracked; a heading or
block quote that carries a pipe directly after the rows is refused like an unstarted row, an over-deny
whose in-band fix is a blank line after the table. Archive rotation of rows to
another file reads as disappearance, an over-deny, since no archive path list exists. Changing a declared
table's header line (beyond re-padding) is refused while the first parent's declaration is in force, an
over-deny: a column rename or addition cannot land through a gated merge that keeps the old declaration in
scope. A carriage return in a header-matching line of any compared snapshot fails closed, an over-deny when
the loss it blocks would have been benign line-ending normalization, and an unlocatable declared table at
the first parent or the branch tip fails closed the same way, an over-deny when the unlocatable form was
benign; at the branch tip that includes a table only the merge-base's config declared (the branch renamed
or removed it), an over-deny when the branch had added no rows to it. A declared table's file absent at
the branch tip while it existed at the merge-base fails closed too when the merge-base's or the
pre-merge target's config declares a table at that PATH; that decision keys on the path alone, whatever
header cells, TOML array name or format the declarations carry, so a withdrawal and a later
re-declaration under a changed locator, however each step landed (directly on the target or through
gated merges), never makes the absence read as the table not yet existing, and a branch forked before
those steps that moved the declared file cannot shed the rows it added under the new path. The path key
over-refuses exactly these sequences, each exit 2: a branch that moved or deleted the declared file and
merges in after the declaration at that path was withdrawn and re-declared under a changed header, TOML
array name or format; a branch that moved or deleted the file BEFORE any table was declared at its path,
merging in after the pre-merge target declared one there; and a merge whose working config re-declares a
table at a path the merge-base's config declared while integrating a branch that moved or deleted the
file (its exit: merge that branch while the declaration at the path is withdrawn, then re-declare in a
later commit). A branch-side deletion of
the declared file cannot be told from such a move and is refused the same way, an over-deny for a
deletion with no replacement (or a move the branch added no rows to). The in-band exit is to withdraw or
narrow the declaration at that path in an earlier, separate commit, land that withdrawal on the target
(directly or
through its own gated merge) before the commit or merge that moves or removes the file, and then move or
remove the file, directly or through a gated merge; a branch can instead restore the file at the
declared path on its own tip (merging the target in or rebasing) before the gated merge (reading the
first parent's config closes narrowing
only within the same merge, and a branch that carries both the withdrawal and the removal into one gated
merge is still refused, because the pre-merge target's config still declares the table, which keeps the
absent-file check running for that path, and the merge-base's config declares a table there); a
single commit on the target that removes the file and its declaration together is NOT a route around the
refusal, because the first parent's declaration is still in force there and the removal fails R1; and
while the declaration is withdrawn, rows that in-flight branches add to the old path are outside the
gate. With a --config outside the repository root there is no in-repo config at any commit, so
the first-parent declaration read and both branch-tip refusals (the unlocatable table and the absent
file) never apply there: declarations come from the working config alone, and a branch-side rename, move
or removal of a declared table's form or file reads as not-yet or absent; only a direct run() caller can
reach that state, since the command line refuses an out-of-root --config. Each config declares at most one
table per file, and header uniqueness is judged on trimmed cells: a SECOND table in the same file whose
header carries the
same cells as the declared header (padding aside) makes the declared header ambiguous and is refused, so
while such a file sits on the target every gated merge is refused, an over-deny. When a key absent at the
merge-base is added by BOTH sides of a frozen table with different content, every resolution is refused
(keeping the target's row trips branch-side R3, keeping the branch's row trips R3 against the first
parent), an over-deny whose in-band exit is a fresh key. A table declaration added only on the branch is
not read for obligations (declarations come from the working config and the first parent's config), so a
resolver can drop a branch-added declaration together with that table's rows in the same merge. The config
is trusted
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
import datetime
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
    return _parse_config_data(data, path)


def _parse_config_data(data, where):
    """The one shape validator for a parsed config mapping, shared by the working-config load and every
    historical snapshot read, so no second, looser copy of the schema can drift from load_config."""
    if isinstance(data, dict) and type(data.get("schema-version")) is int and data["schema-version"] == 2:
        return _load_config_v2(data, where)
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        raise GateError("{}: top-level keys must be exactly {}".format(where, sorted(TOP_KEYS)))
    version = data.get("schema-version")
    if type(version) is not int or version != 1:
        raise GateError("{}: schema-version must be integer 1".format(where))
    marker = _load_marker(data, where)
    specs = _load_record_specs(data.get("record"), where)
    return Config(1, marker, specs, ())


def _load_marker(data, path):
    marker = data.get("opt-out-marker")
    if (not isinstance(marker, str) or not marker or marker != marker.strip()
            or "\n" in marker or "\r" in marker or marker.startswith("## ")):
        raise GateError("{}: opt-out-marker must be one non-heading line with no surrounding whitespace"
                        .format(path))
    return marker


def _compile_heading_pattern(pattern, where):
    """Compile one heading-pattern, turning EVERY compilation failure into a contextual refusal.

    re.compile raises re.error for a malformed pattern, but also OverflowError for a repetition count
    past the engine's limit (a{4294967296}), a BARE ValueError for a repetition count past CPython's
    integer string conversion limit (re's parser converts the count with int(), so a{<4400 digits>}
    raises before the engine's own count check can), and RecursionError for nesting deeper than the
    interpreter's recursion limit; MemoryError is caught too, should compilation exhaust memory.
    Catching only re.error let the others escape as a traceback (exit 1, the finding code) that names
    neither the config nor the snapshot, for the working config and for every historical config alike."""
    try:
        return re.compile(pattern)
    except (re.error, OverflowError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("{}: heading-pattern is invalid ({}: {})".format(where, type(exc).__name__, exc))


def _load_record_specs(rows, path):
    if not isinstance(rows, list):
        raise GateError("{}: record must be a non-empty array of [[record]] tables, got {}"
                        .format(path, type(rows).__name__))
    if not rows:
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
        compiled = _compile_heading_pattern(pattern, where)
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
    """Cell identity trims surrounding ASCII spaces and tabs ONLY, so re-padding or alignment changes
    compare equal while a cell edited in any other Unicode whitespace (a no-break space) stays an edit:
    the GFM grammar strips spaces and tabs around cell content, nothing wider, so str.strip() with no
    argument would silently forgive edits the rendered table shows."""
    return tuple(part.strip(" \t") for part in parts)


def _has_unescaped_pipe(line):
    """True when line carries a `|` that no backslash escapes, using _split_cells's escape rule."""
    index = 0
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if line[index] == "|":
            return True
        index += 1
    return False


def _check_row_run_end(line, where):
    """The first line after a table's rows that does not start with `|` ends the table; it fails closed
    when it still carries an unescaped pipe. Such a line is usually a row written without its leading
    pipe or indented before it, which GFM renders as a further table row; ending the table there silently
    would leave that row, and every row after it, outside the obligations, so a merge could drop them
    unseen. A line that opens another block yet carries a pipe (a heading or a block quote placed directly
    after the rows) is refused too, an over-deny whose in-band fix is a blank line after the table. A
    blank line, or a non-blank line with no unescaped pipe, still ends the table."""
    if line.strip() and _has_unescaped_pipe(line):
        raise GateError(
            "{}: a line directly after the table's rows carries a pipe but does not start with one (an "
            "indented row or a row without its leading pipe); every row must start with a pipe, so the "
            "line fails closed rather than silently ending the table".format(where))


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
    silently empty every row obligation for that snapshot. The CR-stripped candidate is formed BEFORE the
    leading-pipe test: a CR-PREFIXED header line does not itself start with a pipe, so testing the raw
    line first would skip it and read the table as absent instead of refusing the carriage return.
    """
    header_cells = _normalize_cells(_split_cells(header, "{} declared header".format(where)))
    matches = []
    for index, line in enumerate(lines):
        stripped = line.replace("\r", "")
        if not stripped.startswith("|"):
            continue
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

    Returns None when the table cannot be located in the snapshot (no declared header line, or no declared
    TOML array); the caller decides whether that is skippable (an existing file whose snapshot config had
    not yet declared the table) or fail-closed (post-merge: see _require_present; the first parent and the
    branch tip when that snapshot's config declared the table: see _require_locatable, since reading an
    unlocatable declared table as absent would silently empty the snapshot's row obligations). Rows are
    compared by parsed value: a Markdown row is its tuple of trimmed cells, a TOML row is its parsed
    table, so layout changes compare equal.
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
            _check_row_run_end(line, where)
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


def _require_locatable(rows, text, declared_by, decl, where):
    """Fail closed on a declared table that cannot be located in an EXISTING file at a compared snapshot.

    rows is None when _read_table_rows could not locate the declared table. With the file itself absent
    (text is None) the table genuinely did not exist at that snapshot, so None passes through and the
    caller skips that snapshot's obligations (at the branch tip the caller first fails closed, through
    _require_tip_file, on a file the branch itself removed while it existed at the merge-base under the
    merge-base's declaration, so this pass-through covers only a genuinely absent file). With the file present, the decision follows the configs
    that apply to that snapshot: declared_by is None when none of them declared the table (a declaration
    with the same _table_locator, not merely one at the same path), or a short phrase naming the config
    that did ("that snapshot's config"; at the branch tip "the merge-base's config" counts too, so a
    branch that renamed or removed a table both sides had declared cannot shed its own row obligations).
    When the table was declared, an unlocatable form (CR-only line endings, an indented table, or
    anything else the header search cannot find) is ambiguous input, and reading it as "the table did
    not exist yet" would silently empty the snapshot's row obligations and let a simultaneous drop pass,
    so it is exit 2 naming the file, the snapshot, and the declaring config; when no applicable config
    declared the table (it was declared only later), absence from the existing file is genuinely not-yet
    and None passes through.
    """
    if rows is None and text is not None and declared_by:
        raise GateError(
            "{}: the file exists and {} declares the table, but the declared table "
            "({}) cannot be located; an unlocatable declared table fails closed rather than reading as "
            "absent".format(where, declared_by, decl.header or decl.array))
    return rows


def _require_tip_file(branch_text, mergebase_text, declared, decl, where):
    """Fail closed on a declared table's file ABSENT at the branch tip while it existed at the merge-base
    and the merge-base's or the pre-merge target's config declares a table at that PATH
    (_absent_file_declared: the path alone, whatever header cells, array name or format).

    Such a branch itself moved or deleted the declared file. Rows it re-added under another path are
    invisible to this declaration, so reading the table as genuinely absent would shed the branch's own
    row obligations: R2 would see no branch rows while a resolver that keeps the target's tree drops
    them; keying on the path alone keeps a withdrawal re-declared under a changed locator from making
    the absence read as the table not yet existing. A move and a deletion cannot be told apart here, so
    both fail closed; for a deletion with no replacement (or a move that added no rows) that is an
    over-deny, whose in-band exit is to withdraw the declaration in an earlier, separate commit, LAND
    that withdrawal on the target before the commit or merge that removes the file, and then remove the
    file (a single commit removing the file and its declaration together still fails R1 under the first
    parent's declaration, so landing on the target directly is not by itself a route around this
    refusal; a branch that carries the withdrawal and the removal into one gated merge is refused here,
    because the pre-merge target's config still declares the table, which keeps this check running for
    its path, and the merge-base's config declares a table there). A file also absent at the merge-base
    (the branch forked before the file existed), or a path neither the merge-base's nor the pre-merge
    target's config declares a table at (as when the declaration is adopted by this very merge's working
    config), passes through: there is no in-force declaration whose branch rows the absence could shed.
    """
    if branch_text is None and mergebase_text is not None and declared:
        raise GateError(
            "{}: the declared file is absent while it existed at the merge-base and {} a table at "
            "this path; a branch that moved or deleted a declared table's file fails closed rather "
            "than reading as absent".format(where, declared))


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

    Plain Python equality is wrong for frozen rows three times over: bool is an int subclass and int
    equals float (True == 1 == 1.0), so a boolean/integer or integer/float cell substitution would read
    as unchanged; NaN never equals itself, so an untouched NaN cell would read as an edit; and == treats
    0.0 == -0.0 and compares timezone-aware datetimes by instant, so a signed-zero substitution or an
    instant rewritten in another UTC offset would read as unchanged. Values of different concrete types
    are therefore never equal; floats compare by exact repr plus sign bit and datetime values by type plus
    exact repr. repr distinguishes every non-NaN float value (so -0.0 and a changed value are edits) but
    spells every NaN "nan" whatever its sign, so the sign bit is compared too and a TOML nan rewritten as
    -nan is an edit, while two NaNs of the same sign are equal (TOML spells no NaN payload); repr also
    spells a datetime's tzinfo offset, so a re-offset instant is an edit while a pure spelling change that
    parses to the identical value, Z versus +00:00, is not. Lists, tuples, and tables compare element by
    element under the same rules.
    """
    if type(before) is not type(after):
        return False
    if isinstance(before, float):
        return (repr(before) == repr(after)
                and math.copysign(1.0, before) == math.copysign(1.0, after))
    if isinstance(before, (datetime.datetime, datetime.date, datetime.time)):
        return repr(before) == repr(after)
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


def _snapshot_config_tables(root, config_rel, commit, label):
    """The validated table declarations of the config as it stood at one snapshot, () when absent there.

    The snapshot's text goes through _parse_config_data, the SAME parser and shape checks the working
    config is loaded with (shared code, never a second looser copy), so a historical config that is
    present but not a valid schema-version-1 or schema-version-2 config (unparseable TOML, a version-1
    config carrying [[table]], a bare schema-version 2, a non-list record, a heading-pattern that cannot
    be compiled, any other invalid shape) fails closed with exit 2 naming the config and the snapshot: the
    declarations such a config may carry cannot be known. A valid version-1 config validates
    whole and contributes no tables, because version 1 cannot declare them.
    """
    text = _tree_text(root, commit, config_rel)
    if text is None:
        return ()
    where = "{} at {}".format(config_rel, label)
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise GateError("{}: cannot parse ({})".format(where, exc))
    return _parse_config_data(data, where).tables


def _base_config_tables(root, config_path, target):
    """Table declarations read from the pre-merge target's own config, so the same merge cannot remove a
    table's declaration and drop that table's rows at once.

    Reads the first parent's tree at the working config's repo-relative path through the shared validated
    snapshot reader (_snapshot_config_tables). A config absent there, or a working config outside the
    repository root, contributes no tables; so does a valid schema-version-1 config, because version 1
    cannot declare tables; any invalid config fails closed. Declaration narrowing landed in an EARLIER,
    separate commit is out of scope (trusted config), and a declaration added only on the BRANCH is not
    read here (the branch tip's config never adds obligations; see the module RESIDUAL).
    """
    config_rel = _repo_relative(root, config_path)
    if config_rel is None:
        return ()
    return _snapshot_config_tables(root, config_rel, target, "the pre-merge target")


def _table_locator(spec):
    """What locates a declared table in its file: the path, the format, and the trimmed header cells
    (Markdown) or the array name (TOML). Two declarations with the same locator name the same table
    whatever their key columns, edit mode, or header padding, so a snapshot "declared the table" when its
    config carries a declaration with this locator; a declaration of a DIFFERENT table at the same path
    (another TOML array, another header) is not a declaration of this one. The absent-file decision does
    NOT use this locator: it keys on the path alone (_absent_file_declared)."""
    return (spec.path, spec.table_format, spec.header_cells, spec.array)


def _absent_file_declared(decl, mergebase_locators, base_locators):
    """Which snapshot configs declare a table at an absent declared file's PATH, as a phrase for the
    refusal message, or None when none does.

    The decision keys on the path alone, never the full locator: a declaration whose header cells, TOML
    array name or format changed since the fork still marks the path as declared, so a withdrawal and a
    re-declaration under a changed locator cannot make a branch-side move or deletion of the file read
    as the table not yet existing (which would shed the rows the branch re-added under the new path).
    The pre-merge target's declarations are read alongside the merge-base's, so a table declared at the
    path only after the branch forked still fails the branch's move or deletion closed.
    """
    mergebase = any(locator[0] == decl.path for locator in mergebase_locators)
    base = any(locator[0] == decl.path for locator in base_locators)
    if mergebase and base:
        return "the merge-base's and the pre-merge target's configs declare"
    if mergebase:
        return "the merge-base's config declares"
    if base:
        return "the pre-merge target's config declares"
    return None


def _snapshot_decl_locators(root, config_rel, commit, label):
    """Locators (_table_locator) of the tables one snapshot's own config declares, read ONLY to decide
    whether an unlocatable branch-tip table (_require_locatable) or a declared file absent at the branch
    tip while present at the merge-base (_require_tip_file) fails closed, never to add obligations.
    The branch tip's config and the merge-base's are both read for the unlocatable decision, and the
    merge-base's (beside the first parent's declarations, by path alone: _absent_file_declared) for the
    absent-file one: a table only the merge-base's config declared is one the
    branch itself renamed, moved or removed, and reading its branch-tip copy as not yet there or absent
    would shed the branch's own row obligations. An absent or out-of-root config declares none; an
    invalid config at that snapshot fails closed here (exit 2), since whether the table was declared
    there then cannot be known. The reads are lazy: they happen only once a branch-tip table was found
    unlocatable in an existing file, or its declared file absent at the branch tip while present at the
    merge-base, so an ordinary merge never pays them, and an invalid config at either snapshot is
    refused only on those paths."""
    if config_rel is None:
        return frozenset()
    return frozenset(_table_locator(spec) for spec in
                     _snapshot_config_tables(root, config_rel, commit, label))


def _compare_tables(root, decls, base_decl_locators, config_rel, target, graph, post_merge):
    """Evaluate R1 (and R3 for frozen tables) against the first parent, plus R2 (and branch-side R3 for
    frozen tables) when the branch graph is resolvable, for every declared table. Returns (checked key
    count, findings). An unlocatable declared table in an existing file fails closed at the first parent
    (when the first parent's config declared that same table: base_decl_locators) and at the branch tip
    (when the branch tip's own config did, or the merge-base's config did, both read lazily: a branch
    that renamed or removed a table the merge-base's config declared must fail closed rather than shed
    its own branch rows, while a branch forked before the table was declared, where neither config
    declares it, still reads as not-yet). A declared file ABSENT at the branch tip while it existed at
    the merge-base fails closed the same way when the merge-base's or the first parent's config declares
    a table at that path, by the path alone (_require_tip_file, _absent_file_declared), so a branch that
    moved or deleted the declared file cannot shed its own rows
    either; for the merge-base's ROWS an unlocatable table reads as absent,
    which can only ADD branch-owned obligations, never shed one. Every first-parent declaration is itself
    in decls, so each table the first parent declared is checked under its own declaration; a
    working-config table the first parent did not declare (a new TOML array or header at an
    already-declared path) reads as not-yet there, like any adoption."""
    findings = []
    checked = 0
    branch_locators = None
    mergebase_locators = None
    r1_base = _r1_base_commit(target, graph[0] if graph else None)
    for decl in decls:
        base_where = "{} at the pre-merge target".format(decl.path)
        base_text = _tree_text(root, r1_base, decl.path)
        base_rows = _require_locatable(
            None if base_text is None else _read_table_rows(base_text, decl, base_where),
            base_text,
            "that snapshot's config" if _table_locator(decl) in base_decl_locators else None,
            decl, base_where)
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
            branch_where = "{} at branch tip".format(decl.path)
            branch_text = _tree_text(root, branch_commit, decl.path)
            branch_parsed = (None if branch_text is None else
                             _read_table_rows(branch_text, decl, branch_where))
            if branch_text is None and mergebase_text is not None:
                if mergebase_locators is None:
                    mergebase_locators = _snapshot_decl_locators(
                        root, config_rel, mergebase_commit, "the merge-base")
                _require_tip_file(branch_text, mergebase_text,
                                  _absent_file_declared(decl, mergebase_locators,
                                                        base_decl_locators),
                                  decl, branch_where)
            if branch_parsed is None and branch_text is not None:
                if branch_locators is None:
                    branch_locators = _snapshot_decl_locators(
                        root, config_rel, branch_commit, "the branch tip")
                if mergebase_locators is None:
                    mergebase_locators = _snapshot_decl_locators(
                        root, config_rel, mergebase_commit, "the merge-base")
                locator = _table_locator(decl)
                declared_by = ("that snapshot's config" if locator in branch_locators
                               else "the merge-base's config" if locator in mergebase_locators
                               else None)
                branch_parsed = _require_locatable(
                    branch_parsed, branch_text, declared_by, decl, branch_where)
            branch_rows = branch_parsed or {}
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
        base_tables = _base_config_tables(root, config_path, target)
        decls = tuple(dict.fromkeys(config.tables + base_tables))
        tables_declared = len(decls)
        if decls:
            table_keys_checked, table_findings = _compare_tables(
                root, decls, frozenset(_table_locator(spec) for spec in base_tables),
                _repo_relative(root, config_path), target, graph, post_merge)
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


def _quiet_run_refusal(*args):
    """run()'s exit code paired with its captured stderr text, so a refusal case can assert the SPECIFIC
    message rather than only exit 2, which any earlier, unrelated refusal would satisfy."""
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        code = run(*args)
    return code, err.getvalue()


@contextmanager
def _swapped(name, value):
    """Swap one module-level helper for a mutant or naive variant during a self-test case and restore it.

    A self-test case proves a refusal load-bearing by showing the same fixture PASS (or fall through to
    its underlying finding) once the specific check is removed, and proves an allowed change deliberate
    by showing it FAIL under the naive variant it guards against; a few allow controls are instead
    discriminated by a paired refusing fixture on the same surface, where no swapped variant can reach
    the check they bound. Mutation is attribute patching only, never generated code.
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

    def expect_refusal(name, result, needle):
        code, text = result
        if code != 2 or needle not in text:
            failures.append("{}: got (exit {}, {!r}), want exit 2 naming {!r}"
                            .format(name, code, text.strip(), needle))

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
                ("an over-long integer literal", "9" * 4400, "cannot parse config"),
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
            expect_refusal(
                "duplicate selected heading",
                _quiet_run_refusal(
                    root, config_path, True, dupmerge, None, None),
                "duplicate selected heading")

            bad_config = aiqt / "bad-record-sections.toml"
            bad_config.write_text(
                'schema-version = true\nrecord = "wrong"\n',
                encoding="utf-8")
            expect_refusal(
                "malformed config",
                _quiet_run_refusal(root, bad_config, True, bad, None, None),
                "bad-record-sections.toml: top-level keys must be exactly")
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
            failures.extend(_heading_pattern_self_test_cases(Path(directory)))
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
          "TOML comparison, floats and datetimes compared by exact repr and "
          "floats by sign too, so a NaN sign flip is an edit, "
          "a CR-bearing header, a CR-prefixed one included, in any compared "
          "snapshot, an unlocatable declared table in an existing file at "
          "the first parent or the branch tip under that snapshot's own "
          "declaration of the same table, at the branch tip under the "
          "merge-base's declaration too (so a branch-side rename of a "
          "declared table cannot shed the branch's own rows), a declared "
          "table's file absent at the branch tip while it existed at the "
          "merge-base when the merge-base's or the pre-merge target's config "
          "declares a table at that path, keyed on the path alone (so a "
          "branch-side move or deletion of the declared file cannot shed "
          "them either, after a withdraw-then-re-declare under a changed "
          "header, TOML array name or format included, with a branch forked "
          "before the file existed and a path no applicable snapshot config "
          "declared both still "
          "allowed), an invalid "
          "branch-tip or merge-base config read "
          "for those decisions, a schema downgrade or any invalid first-parent "
          "config (validated by the working loader's own shape checks) that "
          "would shed declarations, a heading-pattern the compiler rejects "
          "with OverflowError, ValueError, RecursionError or MemoryError in "
          "the working or the "
          "first-parent config, a row-shaped line directly after a table's "
          "rows that does not start with a pipe, a pipe after a double "
          "backslash on such a line, an explicit --base-ref on a "
          "single-parent commit, a no-break-space cell edit, and the "
          "exercised grammar, shape and graph refusals, each table-leg "
          "refusal and each config-shape refusal asserted by its specific "
          "message), shows "
          "each row-loss, row-edit, graph and "
          "declaration-scope refusal load-bearing against a mutant with that "
          "check swapped out, and allows reorder, insertion, re-padding of "
          "rows and of the header line (tabs included), TOML reformatting, "
          "an unchanged NaN "
          "value, a NaN respelled as +nan, a datetime respelled to the "
          "identical parsed value, a heading directly after a table's rows, "
          "escaped pipes (directly after the rows included), key-stable "
          "corrections, new tables, a table "
          "adopted into an existing file, a branch forked before adoption, "
          "a new table declared beside an already-declared one at the same "
          "path, at the first parent and at the branch tip alike, a valid "
          "version-1 first-parent config, a declared file removed after its "
          "declaration was withdrawn in an earlier, separate commit that "
          "landed on the target first (directly or through gated merges, "
          "while one commit removing the file and its declaration together "
          "still fails R1 and a branch carrying the withdrawal and the "
          "removal into one gated merge is still refused), a clean "
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
    Every refusing fixture here is asserted with its SPECIFIC refusal message (so an earlier, unrelated
    refusal cannot satisfy it); only the mutant and naive-variant re-runs are judged by exit code alone,
    since what they show is the change of outcome. Refusals whose check is a single swappable helper also
    carry a mutant. Returns failure strings.
    """
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("v2 {}: got {}, want {}".format(name, got, want))

    def expect_refusal(name, result, needle):
        code, text = result
        if code != 2 or needle not in text:
            failures.append("v2 {}: got (exit {}, {!r}), want exit 2 naming {!r}"
                            .format(name, code, text.strip(), needle))

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
    cfg_typed = cfg("typed.toml",
                    'schema-version = 2\n\n[[table]]\npath = "typedlog.toml"\nformat = "toml"\n'
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
    expect_refusal("duplicate key under a declared id-only key",
                   _quiet_run_refusal(root, cfg_idonly, True, m3, None, None),
                   "duplicate row key ('X')")
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
    expect_refusal("an explicit --base-ref on a single-parent squash fails closed (real ancestor)",
                   _quiet_run_refusal(root, cfg_frozen, True, squash_clean, None, target),
                   "cannot infer the branch tip")
    expect_refusal("an explicit --base-ref on a single-parent squash fails closed (unresolvable ref)",
                   _quiet_run_refusal(root, cfg_frozen, True, squash_clean, None, "0" * 40),
                   "cannot infer the branch tip")
    with _swapped("_tables_only_squash",
                  lambda parents, b_ref, ba_ref, record_specs:
                  len(parents) == 1 and b_ref is None and not record_specs):
        expect("the explicit base ref is ignored once the squash-path guard drops it (mutant)",
               _quiet_run(root, cfg_frozen, True, squash_clean, None, "0" * 40), 0)
        expect("the real-ancestor explicit base ref is ignored once the squash-path guard drops it "
               "(mutant)",
               _quiet_run(root, cfg_frozen, True, squash_clean, None, target), 0)

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

    # A line directly after the rows that carries a pipe without starting with one is a row GFM still
    # renders; ending the table there would leave it untracked, so dropping it later would pass. A
    # pipe-less line (a heading) still ends the table, and fails only under a variant that refuses any
    # non-blank line after the rows.
    for case_name, branch_name, stray in (
            ("a row without its leading pipe directly after the rows", "v2-no-lead-pipe",
             "X | PR1 | 2026-02-01 | target row |"),
            ("a row indented three spaces directly after the rows", "v2-indented-row",
             "   " + row_x1)):
        stray_base = _v2_branch_commit(root, branch_name, target, {"done.md": md(row_y, stray)},
                                       "a row written outside the pipe grammar")
        stray_drop = _v2_branch_commit(root, branch_name + "-drop", stray_base,
                                       {"done.md": md(row_y)}, "that row dropped")
        expect_refusal(case_name,
                       _quiet_run_refusal(root, cfg_frozen, True, stray_drop, None, None),
                       "done.md at the pre-merge target: a line directly after the table's rows "
                       "carries a pipe but does not start with one")
        with _swapped("_check_row_run_end", lambda line, where: None):
            expect(case_name + "; the drop passes unseen once the row-run end check is removed "
                   "(mutant)",
                   _quiet_run(root, cfg_frozen, True, stray_drop, None, None), 0)
    heading_after = _v2_branch_commit(root, "v2-heading-after", target,
                                      {"done.md": md(row_y, row_x1) + "## Notes\nprose\n"},
                                      "a heading directly after the rows")

    def any_nonblank_end(line, where):
        if line.strip():
            raise GateError("{}: a non-blank line follows the rows".format(where))

    expect("control: a pipe-less heading directly after the rows ends the table",
           _quiet_run(root, cfg_frozen, True, heading_after, None, None), 0)
    with _swapped("_check_row_run_end", any_nonblank_end):
        expect("the heading after the rows fails when any non-blank line is refused (naive variant)",
               _quiet_run(root, cfg_frozen, True, heading_after, None, None), 2)

    # The table-ending test uses _split_cells's escape rule: a backslash escapes exactly the next
    # character, so a line whose every pipe is `\|` carries no unescaped pipe and ends the table like
    # prose, while `\\|` (the first backslash escapes the second) still carries an unescaped pipe and is
    # refused. A naive any-pipe test refuses the escaped-pipe prose; a pair-blind test that erases every
    # `\|` sequence silently ends the table at the double-backslash line.
    escaped_after = _v2_branch_commit(root, "v2-escaped-pipe-after", target,
                                      {"done.md": md(row_y, row_x1)
                                                  + "prose with an escaped \\| pipe\n"},
                                      "escaped pipe directly after the rows")
    expect("control: a line with only an escaped pipe directly after the rows ends the table",
           _quiet_run(root, cfg_frozen, True, escaped_after, None, None), 0)
    with _swapped("_has_unescaped_pipe", lambda line: "|" in line):
        expect("the escaped-pipe prose fails when any pipe after the rows is refused (naive variant)",
               _quiet_run(root, cfg_frozen, True, escaped_after, None, None), 2)
    paired_base = _v2_branch_commit(root, "v2-paired-backslash", target,
                                    {"done.md": md(row_y, "a stray row with \\\\| a pipe after a "
                                                          "double backslash")},
                                    "double backslash does not escape the pipe")
    paired_drop = _v2_branch_commit(root, "v2-paired-backslash-drop", paired_base,
                                    {"done.md": md(row_y)}, "that line dropped")
    expect_refusal("a pipe after a double backslash directly after the rows",
                   _quiet_run_refusal(root, cfg_frozen, True, paired_drop, None, None),
                   "done.md at the pre-merge target: a line directly after the table's rows "
                   "carries a pipe but does not start with one")

    def pair_blind_pipe(line):
        return "|" in line.replace("\\|", "")

    with _swapped("_has_unescaped_pipe", pair_blind_pipe):
        expect("the double-backslash pipe line ends the table silently once backslash pairs are "
               "ignored (mutant)",
               _quiet_run(root, cfg_frozen, True, paired_drop, None, None), 0)

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
    cr_needle = "a line matching the declared header contains a carriage return"
    expect_refusal("a CR-bearing header at the first parent fails closed",
                   _quiet_run_refusal(root, cfg_frozen, True, cr_clean, None, None),
                   "done.md at the pre-merge target: " + cr_needle)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the clean normalization passes once the header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, cr_clean, None, None), 0)
    cr_drop = _v2_branch_commit(root, "v2-cr-header-drop", cr_base,
                                {"done.md": md(row_y)},
                                "normalized to LF, a row dropped")
    expect_refusal("a CR-bearing header at the first parent fails closed before a drop can hide",
                   _quiet_run_refusal(root, cfg_frozen, True, cr_drop, None, None),
                   "done.md at the pre-merge target: " + cr_needle)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the hidden drop surfaces as a finding once the header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, cr_drop, None, None), 1)
    cr_feat = _v2_branch_commit(root, "v2-cr-branch", fork,
                                {"done.md": "# Done log\n\n" + header + "\r\n" + delimiter + "\n"
                                            + row_y + "\n" + row_x2 + "\n"},
                                "CR-terminated header on the branch")
    m_crb = _v2_synthetic_merge(root, target, [target, cr_feat],
                                "merge keeps the target file, branch row gone")
    expect_refusal("a CR-bearing header at the branch tip fails closed",
                   _quiet_run_refusal(root, cfg_frozen, True, m_crb, None, None),
                   "done.md at branch tip: " + cr_needle)
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
    expect_refusal("a CR-bearing header at the merge-base fails closed",
                   _quiet_run_refusal(root, cfg_frozen, True, m_crmb, None, None),
                   "done.md at merge-base: " + cr_needle)
    with _swapped("_locate_header", cr_tolerant_locate):
        expect("the clean merge passes once the merge-base header CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m_crmb, None, None), 0)
    cr_post = _v2_branch_commit(root, "v2-cr-post", target,
                                {"done.md": cr_header_md},
                                "CR-terminated header post-merge")
    expect_refusal("a CR-bearing header post-merge stays fail-closed",
                   _quiet_run_refusal(root, cfg_frozen, True, cr_post, None, None),
                   "done.md at post-merge: " + cr_needle)
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

    # repr spells every NaN "nan", so the sign bit is compared too: nan rewritten as -nan is an edit,
    # while +nan, which parses to the same positive NaN, is a respelling and stays allowed.
    def repr_only_equal(before, after):
        if type(before) is not type(after):
            return False
        if isinstance(before, (float, datetime.datetime, datetime.date, datetime.time)):
            return repr(before) == repr(after)
        if isinstance(before, dict):
            return (before.keys() == after.keys()
                    and all(repr_only_equal(before[key], after[key]) for key in before))
        if isinstance(before, (list, tuple)):
            return (len(before) == len(after)
                    and all(repr_only_equal(b, a) for b, a in zip(before, after)))
        return before == after

    nan_flip = _v2_branch_commit(root, "v2-nan-flip", nan_target,
                                 {"nanlog.toml": '[[done]]\nid = "N1"\nscore = -nan\n'},
                                 "frozen NaN sign flipped")
    expect("a frozen TOML nan rewritten as -nan",
           _quiet_run(root, cfg_tomlnan, True, nan_flip, None, None), 1)
    with _swapped("_row_values_equal", repr_only_equal):
        expect("the NaN sign flip passes once floats compare by repr alone (mutant)",
               _quiet_run(root, cfg_tomlnan, True, nan_flip, None, None), 0)
    nan_plus = _v2_branch_commit(root, "v2-nan-plus", nan_target,
                                 {"nanlog.toml": '[[done]]\nid = "N1"\nscore = +nan\n'},
                                 "frozen NaN respelled as +nan")
    expect("control: a frozen TOML nan respelled as +nan is not an edit",
           _quiet_run(root, cfg_tomlnan, True, nan_plus, None, None), 0)

    # Floats and datetimes compare by type plus exact repr: plain equality reads 0.0 == -0.0 and compares
    # timezone-aware datetimes by instant, so a signed-zero substitution or a re-offset instant would land
    # unnoticed, while an identical value respelled (Z versus +00:00) parses to the same value and stays
    # allowed.
    typed_base_text = '[[done]]\nid = "D1"\nat = 2026-01-01T12:00:00Z\nscore = 0.0\n'
    typed_target = _v2_branch_commit(root, "v2-typed", target,
                                     {"typedlog.toml": typed_base_text}, "typed rows adopted")
    for case_name, branch_name, edited_text in (
            ("a frozen TOML datetime rewritten to the same instant in another offset",
             "v2-typed-reoffset",
             '[[done]]\nid = "D1"\nat = 2026-01-01T07:00:00-05:00\nscore = 0.0\n'),
            ("a frozen TOML 0.0 edited to -0.0",
             "v2-typed-negzero",
             '[[done]]\nid = "D1"\nat = 2026-01-01T12:00:00Z\nscore = -0.0\n')):
        typed_edit = _v2_branch_commit(root, branch_name, typed_target,
                                       {"typedlog.toml": edited_text}, branch_name)
        m_typed_value = _v2_synthetic_merge(root, typed_edit, [typed_target, typed_edit],
                                            "typed value edit lands")
        expect(case_name,
               _quiet_run(root, cfg_typed, True, m_typed_value, None, None), 1)
        with _swapped("_row_values_equal", lambda before, after: before == after):
            expect(case_name + " passes under plain equality (mutant)",
                   _quiet_run(root, cfg_typed, True, m_typed_value, None, None), 0)
    respelled = _v2_branch_commit(
        root, "v2-typed-respelled", typed_target,
        {"typedlog.toml": '[[done]]\nid = "D1"\nat = 2026-01-01T12:00:00+00:00\nscore = 0.0\n'
                          '\n[[done]]\nid = "D2"\n'},
        "Z respelled as +00:00, a row appended")
    m_respelled = _v2_synthetic_merge(root, respelled, [typed_target, respelled],
                                      "respelling lands")
    expect("control: a frozen datetime respelled to the identical parsed value is not an edit",
           _quiet_run(root, cfg_typed, True, m_respelled, None, None), 0)

    # Markdown cells are trimmed of ASCII spaces and tabs only (the GFM cell grammar): an edit made of
    # other Unicode whitespace is a real edit, while tab re-padding stays as legitimate as space
    # re-padding and fails only under a space-only trim.
    nbsp_edit = _v2_branch_commit(
        root, "v2-nbsp", target,
        {"done.md": md("| Y | PR0 | 2026-01-01 | base row\u00a0 |", row_x1)},
        "no-break space appended inside a frozen cell")
    m_nbsp = _v2_synthetic_merge(root, nbsp_edit, [target, nbsp_edit], "nbsp edit lands")
    expect("a frozen cell edited only by a no-break space",
           _quiet_run(root, cfg_frozen, True, m_nbsp, None, None), 1)
    with _swapped("_normalize_cells", lambda parts: tuple(part.strip() for part in parts)):
        expect("the no-break-space edit passes under full Unicode trimming (mutant)",
               _quiet_run(root, cfg_frozen, True, m_nbsp, None, None), 0)
    tab_padded = _v2_branch_commit(
        root, "v2-tab-pad", target,
        {"done.md": "# Done log\n\n" + header + "\n" + delimiter + "\n"
                    + "|\tY\t| PR0 | 2026-01-01 | base row\t|\n" + row_x1 + "\n"},
        "cells re-padded with tabs")
    m_tab = _v2_synthetic_merge(root, tab_padded, [target, tab_padded], "tab re-padding lands")
    expect("control: tab re-padding of cells is not an edit",
           _quiet_run(root, cfg_frozen, True, m_tab, None, None), 0)
    with _swapped("_normalize_cells", lambda parts: tuple(part.strip(" ") for part in parts)):
        expect("tab re-padding fails under a space-only trim (naive variant)",
               _quiet_run(root, cfg_frozen, True, m_tab, None, None), 1)

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
    expect_refusal("the declared header absent from a present file",
                   _quiet_run_refusal(root, cfg_badheader, True, m3, None, None),
                   "done.md at post-merge: the declared table (| id | status |) is absent post-merge")
    with _swapped("_require_present",
                  lambda rows, decl, where: {} if rows is None else rows):
        expect("the absent table passes once the presence check is removed (mutant)",
               _quiet_run(root, cfg_badheader, True, m3, None, None), 0)

    doubled = _v2_branch_commit(
        root, "v2-doubled-header", target,
        {"done.md": md(row_y, row_x1) + "\n```\n" + header + "\n```\n"},
        "header duplicated inside a code fence")
    m11 = _v2_synthetic_merge(root, doubled, [target, doubled], "doubled header lands")
    expect_refusal("the header present twice (once inside a code fence)",
                   _quiet_run_refusal(root, cfg_frozen, True, m11, None, None),
                   "done.md at post-merge: the declared header line appears 2 times")
    with _swapped("_locate_header",
                  lambda lines, table_header, where:
                  lines.index(table_header) if table_header in lines else None):
        expect("the doubled header passes once the uniqueness check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, m11, None, None), 0)

    expect_refusal("a key column missing from the header",
                   _quiet_run_refusal(root, cfg_typokey, True, mrs, None, None),
                   "typokey.toml [[table]] #1: key column 'typo' must appear exactly once in the header")
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
    expect_refusal("a TOML row missing a key column",
                   _quiet_run_refusal(root, cfg_toml, True, mtm, None, None),
                   "notes.toml at post-merge row #2: key column 'pr' is missing")
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
    expect_refusal("a row with one cell too many",
                   _quiet_run_refusal(root, cfg_frozen, True, mwc, None, None),
                   "done.md at post-merge: a table row has 5 cell(s), the header has 4")
    with _swapped("_check_cell_count", lambda count, expected, where: None):
        expect("the wrong cell count passes once the count check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, mwc, None, None), 0)
    few_cells = _v2_branch_commit(
        root, "v2-few-cells", target,
        {"done.md": md(row_y, row_x1, "| V | PR8 | 2026-03-03 |")},
        "row with one cell too few")
    mwf = _v2_synthetic_merge(root, few_cells, [target, few_cells],
                              "missing cell lands")
    expect_refusal("a row with one cell too few",
                   _quiet_run_refusal(root, cfg_frozen, True, mwf, None, None),
                   "done.md at post-merge: a table row has 3 cell(s), the header has 4")
    with _swapped("_check_cell_count", lambda count, expected, where: None):
        expect("the missing cell passes once the count check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, mwf, None, None), 0)

    cr_row = _v2_branch_commit(
        root, "v2-cr", target,
        {"done.md": md(row_y, row_x1, "| C | PR9 | 2026-03-02 | a\rb |")},
        "CR inside a table line")
    mcr = _v2_synthetic_merge(root, cr_row, [target, cr_row], "CR lands")
    expect_refusal("a CR inside a table line",
                   _quiet_run_refusal(root, cfg_frozen, True, mcr, None, None),
                   "done.md at post-merge: a table line contains a carriage return")
    with _swapped("_check_no_cr", lambda line, where: None):
        expect("the CR passes once the CR check is removed (mutant)",
               _quiet_run(root, cfg_frozen, True, mcr, None, None), 0)

    bad_array = _v2_branch_commit(root, "v2-toml-badarray", target,
                                  {"notes2.toml": "bad = [1, 2]\n"},
                                  "declared TOML array of non-tables")
    mtb = _v2_synthetic_merge(root, bad_array, [target, bad_array],
                              "bad array lands")
    expect_refusal("a declared TOML array that is not an array of tables",
                   _quiet_run_refusal(root, cfg_tomlbad, True, mtb, None, None),
                   "notes2.toml at post-merge: bad must be an array of tables")
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
    expect_refusal("a non-scalar TOML key value",
                   _quiet_run_refusal(root, cfg_toml, True, mts, None, None),
                   "notes.toml at post-merge row #2 key column 'pr': a key field must be a string or an "
                   "integer, got list")
    with _swapped("_toml_row_value",
                  lambda value, where:
                  value if type(value) in (str, int) else repr(value)):
        expect("the non-scalar key value passes once the scalar check is removed (mutant)",
               _quiet_run(root, cfg_toml, True, mts, None, None), 0)

    expect_refusal("a version-2 config with unknown top-level keys",
                   _quiet_run_refusal(root, cfg_unknown, True, m3, None, None),
                   "unknown.toml: unknown top-level keys ['surprise'] in a schema-version 2 config")
    with _swapped("_check_v2_top_keys", lambda data, path: None):
        expect("the unknown config key passes once the key check is removed (mutant)",
               _quiet_run(root, cfg_unknown, True, m3, None, None), 0)

    # Grammar refusals on declared files, asserted on single-parent fixtures (the first parent's copy is
    # well-formed, the post-merge copy is not), plus a parentless commit.
    toml_garbled = _v2_branch_commit(root, "v2-toml-garbled", target,
                                     {"notes.toml": "= not toml\n"},
                                     "declared TOML file unparseable")
    expect_refusal("an unparseable declared TOML file fails closed",
                   _quiet_run_refusal(root, cfg_toml, True, toml_garbled, None, None),
                   "cannot parse TOML")
    headless = _v2_branch_commit(root, "v2-no-delimiter", target,
                                 {"done.md": "# Done log\n\n" + header},
                                 "header with no delimiter row")
    expect_refusal("a header with no delimiter row fails closed",
                   _quiet_run_refusal(root, cfg_frozen, True, headless, None, None),
                   "not followed by a delimiter row")
    bad_delim = _v2_branch_commit(root, "v2-bad-delimiter", target,
                                  {"done.md": "# Done log\n\n" + header
                                              + "\n| x | y | z | w |\n" + row_y + "\n"},
                                  "line after the header is not a delimiter row")
    expect_refusal("a non-delimiter line after the header fails closed",
                   _quiet_run_refusal(root, cfg_frozen, True, bad_delim, None, None),
                   "not a matching delimiter row")
    dup_toml_key = _v2_branch_commit(root, "v2-toml-dup", target,
                                     {"notes.toml": notes_base + "\n" + notes_base},
                                     "duplicate TOML row key")
    expect_refusal("a duplicate TOML row key fails closed",
                   _quiet_run_refusal(root, cfg_toml, True, dup_toml_key, None, None),
                   "duplicate row key")
    with _swapped("_register_key",
                  lambda rows, key, row, where: rows.__setitem__(key, row)):
        expect("the duplicate TOML row key passes once the duplicate guard is removed (mutant)",
               _quiet_run(root, cfg_toml, True, dup_toml_key, None, None), 0)
    expect_refusal("a root (parentless) post-merge commit fails closed",
                   _quiet_run_refusal(root, cfg_frozen, True, fork, None, None),
                   "post-merge commit has no first parent")
    root_parents = _parents
    with _swapped("_parents",
                  lambda root_path, commit: root_parents(root_path, commit) or [target]):
        expect("the root commit is evaluated once the parent guard invents a parent (mutant)",
               _quiet_run(root, cfg_frozen, True, fork, None, None), 1)

    # Config-shape refusals asserted directly against load_config, each with its specific message.
    load_refusals = []

    def expect_load_refusal(label, text, needle):
        load_refusals.append(label)
        path = cfg("refuse-{}.toml".format(len(load_refusals)), text)
        try:
            load_config(path)
            outcome = "accepted"
        except GateError as exc:
            outcome = "refused" if needle in str(exc) else "refused without naming it ({})".format(exc)
        expect(label, outcome, "refused")

    expect_load_refusal("a version-2 config with neither record kind", "schema-version = 2\n",
                        "at least one [[record]] or [[table]] table is required")
    expect_load_refusal("[[record]] entries without opt-out-marker",
                        'schema-version = 2\n\n[[record]]\npath = "records.md"\n'
                        "heading-pattern = '^## .+$'\n",
                        "declared together or not at all")
    expect_load_refusal("an unknown [[table]] format",
                        'schema-version = 2\n\n[[table]]\npath = "d.md"\nformat = "csv"\n'
                        'header = "| id |"\nkey = ["id"]\nedit = "frozen"\n',
                        "format must be 'markdown' or 'toml'")
    expect_load_refusal("an unknown [[table]] edit mode",
                        "schema-version = 2\n\n" + md_table + 'edit = "sometimes"\n',
                        "edit must be 'frozen' or 'key-stable'")
    expect_load_refusal("an empty [[table]] key list",
                        'schema-version = 2\n\n[[table]]\npath = "d.md"\nformat = "markdown"\n'
                        'header = "| id |"\nkey = []\nedit = "frozen"\n',
                        "key must be a non-empty list")
    expect_load_refusal("a header that is not a |-prefixed line",
                        'schema-version = 2\n\n[[table]]\npath = "d.md"\nformat = "markdown"\n'
                        'header = "id |"\nkey = ["id"]\nedit = "frozen"\n',
                        "header must be one |-prefixed line")
    expect_load_refusal("a TOML array name carrying a newline",
                        'schema-version = 2\n\n[[table]]\npath = "d.toml"\nformat = "toml"\n'
                        'array = "a\\nb"\nkey = ["id"]\nedit = "frozen"\n',
                        "array must name one top-level TOML array")
    expect_load_refusal("an unknown key inside [[table]]",
                        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\nextra = 1\n',
                        "keys must be exactly")
    expect_load_refusal("a duplicate declared table path",
                        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n\n'
                        + md_table + 'edit = "frozen"\n',
                        "duplicate table path")

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
    expect_refusal("[[record]] entries keep the branch-graph requirement on a single parent",
                   _quiet_run_refusal(root, cfg_mixed, True, mix_squash, None, None),
                   "cannot infer the branch tip")
    with _swapped("resolve_graph",
                  lambda root_path, post_ref, b_ref=None, ba_ref=None:
                  (mix_base, mix_squash, mix_squash)):
        expect("the single-parent mixed config passes once the graph requirement is removed (mutant)",
               _quiet_run(root, cfg_mixed, True, mix_squash, None, None), 0)
    octopus = _v2_synthetic_merge(root, m3_tree, [target, feat, deleter],
                                  "octopus merge, no row dropped")
    expect_refusal("an octopus merge fails closed for the table leg",
                   _quiet_run_refusal(root, cfg_frozen, True, octopus, None, None),
                   "post-merge commit has 3 parents (an octopus merge)")
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
    expect_refusal("an uninterpretable first-parent config version fails closed",
                   _quiet_run_refusal(vthree, vthree_config, True, vthree_drop, None, None),
                   ".aiqt/record-sections.toml at the pre-merge target: top-level keys must be exactly")
    with _swapped("_base_config_tables",
                  lambda root_path, config_path, target_commit: ()):
        expect("the uninterpretable first-parent config is skipped once the read is removed (mutant)",
               _quiet_run(vthree, vthree_config, True, vthree_drop, None, None), 0)

    # The first-parent config is validated by the SAME parser and shape checks as the working config, so
    # every invalid shape fails closed, not only an unknown version: a version-1 config carrying
    # [[table]], a bare schema-version 2, and a non-list record value each previously contributed no
    # declarations, letting the same merge replace the config and drop the rows it governed. A valid
    # version-1 first-parent config still contributes no tables and passes.
    bshape = directory / "v2baseshape"
    bshape.mkdir()
    _selftest_git(bshape, ["init", "-q", "-b", "main"])
    _selftest_git(bshape, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(bshape, ["config", "user.email", "selftest@example.invalid"])
    (bshape / ".aiqt").mkdir()
    (bshape / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n', encoding="utf-8")
    (bshape / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    (bshape / "records.md").write_text("# R\n\n## 2026-08-01 Existing\nbase\n", encoding="utf-8")
    _selftest_git(bshape, ["add", "-A"])
    _selftest_git(bshape, ["commit", "-q", "-m", "valid config adopted"])
    bshape_seed = _selftest_git(bshape, ["rev-parse", "HEAD"])
    bshape_config = bshape / ".aiqt" / "record-sections.toml"
    other_table_cfg = ('schema-version = 2\n\n[[table]]\npath = "newlog.md"\nformat = "markdown"\n'
                       'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    # The non-list record value sits beside a valid marker and a valid table declaration, so only the
    # record-type check can refuse it (no pairing or empty-config rule fires first).
    record_not_list_cfg = ('schema-version = 2\n'
                           'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n'
                           'record = 42\n\n' + md_table + 'edit = "frozen"\n')
    shaped_drops = {}
    for shape_index, (refusal_name, needle, bad_text) in enumerate((
            ("a first-parent version-1 config carrying [[table]] fails closed",
             "top-level keys must be exactly",
             v1_config + "\n" + md_table + 'edit = "frozen"\n'),
            ("a bare schema-version-2 first-parent config fails closed",
             "at least one [[record]] or [[table]] table is required",
             "schema-version = 2\n"),
            ("a first-parent config whose record value is not a list fails closed",
             ".aiqt/record-sections.toml at the pre-merge target: record must be a non-empty array of "
             "[[record]] tables, got int",
             record_not_list_cfg)), 1):
        shaped = _v2_branch_commit(bshape, "v2-shape-{}".format(shape_index), bshape_seed,
                                   {".aiqt/record-sections.toml": bad_text},
                                   "malformed config lands ungated")
        shaped_drop = _v2_branch_commit(bshape, "v2-shape-{}-drop".format(shape_index), shaped,
                                        {".aiqt/record-sections.toml": other_table_cfg,
                                         "done.md": md(row_y)},
                                        "config replaced, a row dropped")
        expect_refusal(refusal_name,
                       _quiet_run_refusal(bshape, bshape_config, True, shaped_drop, None, None),
                       needle)
        with _swapped("_base_config_tables",
                      lambda root_path, config_path, target_commit: ()):
            expect(refusal_name + "; the drop hides once the base-config read is removed (mutant)",
                   _quiet_run(bshape, bshape_config, True, shaped_drop, None, None), 0)
        shaped_drops[shape_index] = shaped_drop
    true_record_specs = _load_record_specs
    with _swapped("_load_record_specs",
                  lambda rows, path:
                  () if not isinstance(rows, list) else true_record_specs(rows, path)):
        expect("the non-list record value is accepted once only the record-type check is removed, so "
               "its table declaration reports the dropped row instead (mutant)",
               _quiet_run(bshape, bshape_config, True, shaped_drops[3], None, None), 1)
    valid_v1_parent = _v2_branch_commit(bshape, "v2-shape-valid-v1", bshape_seed,
                                        {".aiqt/record-sections.toml": v1_config},
                                        "valid version-1 config lands")
    valid_v1_child = _v2_branch_commit(bshape, "v2-shape-valid-v1-next", valid_v1_parent,
                                       {".aiqt/record-sections.toml": other_table_cfg},
                                       "tables-only config adopted, rows kept")
    expect("control: a valid version-1 first-parent config contributes no tables and passes",
           _quiet_run(bshape, bshape_config, True, valid_v1_child, None, None), 0)

    # An unlocatable declared table in an EXISTING file fails closed at the first parent and at the
    # branch tip when that snapshot's own config declared the table; only a genuinely absent file (the
    # cfg_two control above), or an existing file whose snapshot config had not yet declared the table,
    # is skippable. In-repo configs make each snapshot's own declarations readable.
    uloc = directory / "v2unlocatable"
    uloc.mkdir()
    _selftest_git(uloc, ["init", "-q", "-b", "main"])
    _selftest_git(uloc, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(uloc, ["config", "user.email", "selftest@example.invalid"])
    (uloc / ".aiqt").mkdir()
    (uloc / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n', encoding="utf-8")
    (uloc / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    _selftest_git(uloc, ["add", "-A"])
    _selftest_git(uloc, ["commit", "-q", "-m", "clean table adopted"])
    uloc_clean = _selftest_git(uloc, ["rev-parse", "HEAD"])
    uloc_config = uloc / ".aiqt" / "record-sections.toml"
    cr_only_md = ("# Done log\r\r" + header + "\r" + delimiter + "\r"
                  + row_y + "\r" + row_x1 + "\r")
    uloc_crbase = _v2_branch_commit(uloc, "v2-uloc-crbase", uloc_clean,
                                    {"done.md": cr_only_md}, "table rewritten with CR-only endings")
    uloc_drop = _v2_branch_commit(uloc, "v2-uloc-drop", uloc_crbase,
                                  {"done.md": md(row_y)}, "normalized to LF, a row dropped")
    expect_refusal("a CR-only first-parent table under that snapshot's declaration fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, uloc_drop, None, None),
                   "done.md at the pre-merge target: the file exists and that snapshot's config "
                   "declares the table, but the declared table (| id | pr | closed | note |) cannot "
                   "be located")
    with _swapped("_require_locatable",
                  lambda rows, text, declared, decl, where: rows):
        expect("the CR-only drop hides once the unlocatable-table check is removed (mutant)",
               _quiet_run(uloc, uloc_config, True, uloc_drop, None, None), 0)
    indented_md = ("# Done log\n\n " + header + "\n " + delimiter + "\n "
                   + row_y + "\n " + row_x1 + "\n")
    uloc_indented = _v2_branch_commit(uloc, "v2-uloc-indented", uloc_clean,
                                      {"done.md": indented_md}, "table indented by one space")
    uloc_ind_drop = _v2_branch_commit(uloc, "v2-uloc-ind-drop", uloc_indented,
                                      {"done.md": md(row_y)}, "dedented, a row dropped")
    expect_refusal("an indented first-parent table under that snapshot's declaration fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, uloc_ind_drop, None, None),
                   "done.md at the pre-merge target: the file exists and that snapshot's config "
                   "declares the table, but the declared table (| id | pr | closed | note |) cannot "
                   "be located")
    with _swapped("_require_locatable",
                  lambda rows, text, declared, decl, where: rows):
        expect("the indented-table drop hides once the unlocatable-table check is removed (mutant)",
               _quiet_run(uloc, uloc_config, True, uloc_ind_drop, None, None), 0)

    # A CR-PREFIXED header line must be caught by the carriage-return refusal: the CR-stripped candidate
    # is formed BEFORE the leading-pipe test, so the line is recognized as the header and refused for its
    # CR rather than skipped as a non-pipe line (which would read the table as absent). The ordering
    # mutant reverts to pipe-first matching; the unlocatable-table rule still catches the first-parent
    # case behind it, so hiding the drop needs BOTH checks removed.
    def pipe_first_locate(lines, table_header, where):
        header_cells = _normalize_cells(_split_cells(table_header,
                                                     "{} declared header".format(where)))
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
                raise GateError("{}: a line matching the declared header contains a carriage "
                                "return".format(where))
            matches.append(index)
        if len(matches) > 1:
            raise GateError("{}: the declared header line appears {} times".format(where, len(matches)))
        return matches[0] if matches else None

    cr_prefixed_md = "\r" + header + "\n" + delimiter + "\n" + row_y + "\n" + row_x1 + "\n"
    uloc_crpre = _v2_branch_commit(uloc, "v2-uloc-crpre", uloc_clean,
                                   {"done.md": cr_prefixed_md}, "CR-prefixed header line")
    uloc_crpre_drop = _v2_branch_commit(uloc, "v2-uloc-crpre-drop", uloc_crpre,
                                        {"done.md": md(row_y)}, "normalized, a row dropped")
    expect_refusal("a CR-prefixed first-parent header line fails closed as a carriage return",
                   _quiet_run_refusal(uloc, uloc_config, True, uloc_crpre_drop, None, None),
                   "carriage return")
    with _swapped("_locate_header", pipe_first_locate):
        expect_refusal("the CR-prefixed header still fails closed as unlocatable under pipe-first "
                       "matching (mutant)",
                       _quiet_run_refusal(uloc, uloc_config, True, uloc_crpre_drop, None, None),
                       "cannot be located")
        with _swapped("_require_locatable",
                      lambda rows, text, declared, decl, where: rows):
            expect("the CR-prefixed drop hides once both the strip order and the unlocatable-table "
                   "check are removed (mutants)",
                   _quiet_run(uloc, uloc_config, True, uloc_crpre_drop, None, None), 0)
    crpre_mb_t = _v2_branch_commit(uloc, "v2-uloc-crpre-mb-t", uloc_crpre,
                                   {"done.md": md(row_y, row_x1)}, "target normalizes")
    crpre_mb_b = _v2_branch_commit(uloc, "v2-uloc-crpre-mb-b", uloc_crpre,
                                   {"done.md": md(row_y, row_x1, row_x2)}, "branch adds a row")
    m_crpre_mb = _v2_synthetic_merge(uloc, crpre_mb_b, [crpre_mb_t, crpre_mb_b],
                                     "merge keeps every row")
    expect_refusal("a CR-prefixed header at the merge-base fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, m_crpre_mb, None, None),
                   "carriage return")
    with _swapped("_locate_header", pipe_first_locate):
        expect("the merge-base CR-prefixed header reads as absent once the strip order is "
               "reverted (mutant)",
               _quiet_run(uloc, uloc_config, True, m_crpre_mb, None, None), 0)
    tip_cr_md = ("# Done log\r\r" + header + "\r" + delimiter + "\r"
                 + row_y + "\r" + row_x1 + "\r" + row_x2 + "\r")
    uloc_tip_b = _v2_branch_commit(uloc, "v2-uloc-tip-b", uloc_clean,
                                   {"done.md": tip_cr_md}, "branch adds a row, CR-only endings")
    uloc_tip_t = _v2_branch_commit(uloc, "v2-uloc-tip-t", uloc_clean,
                                   {"t.txt": "t\n"}, "target change")
    m_tip = _v2_synthetic_merge(uloc, uloc_tip_t, [uloc_tip_t, uloc_tip_b],
                                "merge keeps the target file, branch row gone")
    expect_refusal("a CR-only branch-tip table under that snapshot's declaration fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, m_tip, None, None),
                   "done.md at branch tip: the file exists and that snapshot's config declares the "
                   "table, but the declared table (| id | pr | closed | note |) cannot be located")
    with _swapped("_require_locatable",
                  lambda rows, text, declared, decl, where: rows):
        expect("the dropped branch row stays hidden once the unlocatable-table check is removed "
               "(mutant)",
               _quiet_run(uloc, uloc_config, True, m_tip, None, None), 0)

    # The branch tip's config is read only once a branch-tip table is unlocatable, and an invalid one
    # then fails closed: whether that snapshot declared the table cannot be known. Reading it as
    # declaring nothing would hide the dropped branch row. Each branch below changes the config, so the
    # working tree is returned to the target (whose config is the valid one under test) before each run.
    uloc_badtip_b = _v2_branch_commit(uloc, "v2-uloc-badtip-b", uloc_clean,
                                      {"done.md": tip_cr_md,
                                       ".aiqt/record-sections.toml": "schema-version = 2\n"},
                                      "branch adds a row, CR-only endings, invalid config")
    m_badtip = _v2_synthetic_merge(uloc, uloc_tip_t, [uloc_tip_t, uloc_badtip_b],
                                   "merge keeps the target file, branch row gone")
    _selftest_git(uloc, ["checkout", "-q", uloc_tip_t])
    expect_refusal("an invalid branch-tip config read for an unlocatable branch-tip table fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, m_badtip, None, None),
                   ".aiqt/record-sections.toml at the branch tip: at least one [[record]] or [[table]] "
                   "table is required")
    true_decl_locators = _snapshot_decl_locators

    def lenient_decl_locators(root_path, config_rel, commit, label):
        try:
            return true_decl_locators(root_path, config_rel, commit, label)
        except GateError:
            return frozenset()

    def mergebase_blind_locators(root_path, config_rel, commit, label):
        if label == "the merge-base":
            return frozenset()
        return lenient_decl_locators(root_path, config_rel, commit, label)

    # With only the invalid branch-tip config read as declaring nothing, the merge-base's own
    # declaration still fails the unlocatable table closed (defence in depth); hiding the dropped
    # branch row needs the merge-base read removed as well.
    with _swapped("_snapshot_decl_locators", lenient_decl_locators):
        expect_refusal("an invalid branch-tip config read as declaring nothing is still caught by the "
                       "merge-base's declaration (mutant)",
                       _quiet_run_refusal(uloc, uloc_config, True, m_badtip, None, None),
                       "done.md at branch tip: the file exists and the merge-base's config declares "
                       "the table, but the declared table (| id | pr | closed | note |) cannot be "
                       "located")
    with _swapped("_snapshot_decl_locators", mergebase_blind_locators):
        expect("the dropped branch row hides once an invalid branch-tip config reads as declaring "
               "nothing and the merge-base declaration is not read (mutants)",
               _quiet_run(uloc, uloc_config, True, m_badtip, None, None), 0)

    # A snapshot declared the table when its config declares the same table (path, format, header cells
    # or array), whatever its key or edit mode: a branch that switched the table to key-stable still
    # declared it, so its unlocatable copy fails closed, which matching the whole declaration would miss.
    uloc_keystable_b = _v2_branch_commit(uloc, "v2-uloc-ks-b", uloc_clean,
                                         {"done.md": tip_cr_md,
                                          ".aiqt/record-sections.toml":
                                              "schema-version = 2\n\n" + md_table
                                              + 'edit = "key-stable"\n'},
                                         "branch adds a row, CR-only endings, key-stable config")
    m_kstip = _v2_synthetic_merge(uloc, uloc_tip_t, [uloc_tip_t, uloc_keystable_b],
                                  "merge keeps the target file, branch row gone")
    _selftest_git(uloc, ["checkout", "-q", uloc_tip_t])
    expect_refusal("an unlocatable branch-tip table its config declares under another edit mode "
                   "fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, m_kstip, None, None),
                   "done.md at branch tip: the file exists and that snapshot's config declares the "
                   "table")
    with _swapped("_table_locator", lambda spec: spec):
        expect_refusal("the key-stable branch declaration is still caught by the merge-base's "
                       "declaration once the whole declaration must match (mutant)",
                       _quiet_run_refusal(uloc, uloc_config, True, m_kstip, None, None),
                       "done.md at branch tip: the file exists and the merge-base's config declares "
                       "the table")
        with _swapped("_snapshot_decl_locators", mergebase_blind_locators):
            expect("the dropped branch row hides once the whole declaration must match and the "
                   "merge-base declaration is not read (mutants)",
                   _quiet_run(uloc, uloc_config, True, m_kstip, None, None), 0)

    # A branch that RENAMES a declared table in its own config and file (adding a row under the new
    # form) must not read as the table not yet existing at the branch tip: the merge-base's config
    # declared the table, so the unlocatable branch-tip copy fails closed, and a resolver who keeps the
    # target's side cannot silently drop the branch's own rows into a PASS. A branch forked before the
    # table was declared stays allowed (the adoption fixtures below), because there neither the branch
    # tip's config nor the merge-base's declares it.
    renamed_header = "| id | pr | closed | notes |"
    renamed_cfg = ('schema-version = 2\n\n[[table]]\npath = "done.md"\nformat = "markdown"\n'
                   'header = "| id | pr | closed | notes |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    renamed_md = ("# Done log\n\n" + renamed_header + "\n" + delimiter + "\n"
                  + row_y + "\n" + row_x1 + "\n" + row_x2 + "\n")
    uloc_ren_b = _v2_branch_commit(uloc, "v2-uloc-rename-b", uloc_clean,
                                   {"done.md": renamed_md,
                                    ".aiqt/record-sections.toml": renamed_cfg},
                                   "branch renames the declared header and adds a row")
    m_ren = _v2_synthetic_merge(uloc, uloc_tip_t, [uloc_tip_t, uloc_ren_b],
                                "resolver keeps the target side, branch row gone")
    _selftest_git(uloc, ["checkout", "-q", uloc_tip_t])
    expect_refusal("a branch-side rename of a declared table fails closed through the merge-base's "
                   "declaration",
                   _quiet_run_refusal(uloc, uloc_config, True, m_ren, None, None),
                   "done.md at branch tip: the file exists and the merge-base's config declares the "
                   "table, but the declared table (| id | pr | closed | note |) cannot be located")
    with _swapped("_snapshot_decl_locators", mergebase_blind_locators):
        expect("the renamed table's dropped branch row hides once the merge-base declaration is not "
               "read (mutant)",
               _quiet_run(uloc, uloc_config, True, m_ren, None, None), 0)

    # The merge-base's config is read for that decision too, and an invalid one then fails closed:
    # whether the merge-base declared the table cannot be known, and reading it as declaring nothing
    # would hide the dropped branch row (the branch tip's own config here declares only the renamed
    # form). The target commit repairs the config, so the working and first-parent reads stay valid.
    uloc_badmb = _v2_branch_commit(uloc, "v2-uloc-badmb", uloc_clean,
                                   {".aiqt/record-sections.toml": "schema-version = 2\n"},
                                   "invalid config lands ungated")
    uloc_badmb_t = _v2_branch_commit(uloc, "v2-uloc-badmb-t", uloc_badmb,
                                     {".aiqt/record-sections.toml":
                                          "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n'},
                                     "target repairs the config")
    uloc_badmb_b = _v2_branch_commit(uloc, "v2-uloc-badmb-b", uloc_badmb,
                                     {"done.md": renamed_md,
                                      ".aiqt/record-sections.toml": renamed_cfg},
                                     "branch renames the declared header and adds a row")
    m_badmb = _v2_synthetic_merge(uloc, uloc_badmb_t, [uloc_badmb_t, uloc_badmb_b],
                                  "resolver keeps the target side, branch row gone")
    _selftest_git(uloc, ["checkout", "-q", uloc_badmb_t])
    expect_refusal("an invalid merge-base config read for an unlocatable branch-tip table fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, m_badmb, None, None),
                   ".aiqt/record-sections.toml at the merge-base: at least one [[record]] or "
                   "[[table]] table is required")
    with _swapped("_snapshot_decl_locators", lenient_decl_locators):
        expect("the renamed table's dropped branch row hides once an invalid merge-base config reads "
               "as declaring nothing (mutant)",
               _quiet_run(uloc, uloc_config, True, m_badmb, None, None), 0)

    # The merge-base's config is read on the ABSENT-FILE path too (the branch deleted or moved the
    # declared file), and an invalid one fails closed there the same way: whether the merge-base
    # declared a table at the path cannot be known. Here the pre-merge target declares one, so even
    # read as declaring nothing the merge-base cannot hide the removal (defence in depth); the
    # merge-base read is shown load-bearing by the re-declaring-merge fixture further down, where
    # the pre-merge target does not declare the path.
    uloc_badmb_del = _v2_branch_commit(uloc, "v2-uloc-badmb-del", uloc_badmb,
                                       {"done.md": None},
                                       "branch deletes the declared file under the invalid config")
    m_badmb_del = _v2_synthetic_merge(uloc, uloc_badmb_t, [uloc_badmb_t, uloc_badmb_del],
                                      "merge keeps the target file")
    _selftest_git(uloc, ["checkout", "-q", uloc_badmb_t])
    expect_refusal("an invalid merge-base config read for a declared file absent at the branch tip "
                   "fails closed",
                   _quiet_run_refusal(uloc, uloc_config, True, m_badmb_del, None, None),
                   ".aiqt/record-sections.toml at the merge-base: at least one [[record]] or "
                   "[[table]] table is required")
    with _swapped("_snapshot_decl_locators", lenient_decl_locators):
        expect_refusal("with the invalid merge-base config read as declaring nothing, the deleted "
                       "file is still refused through the pre-merge target's declaration at the "
                       "path (mutant)",
                       _quiet_run_refusal(uloc, uloc_config, True, m_badmb_del, None, None),
                       "done.md at branch tip: the declared file is absent while it existed at the "
                       "merge-base and the pre-merge target's config declares a table at this path")

    # A branch that MOVES the declared table's file (deleting the declared path and re-adding the
    # table under a new path its own config declares) must not read as the table genuinely absent at
    # the branch tip: the file existed at the merge-base and the merge-base's and the pre-merge
    # target's configs declare a table at that path, so the absence fails closed (_require_tip_file,
    # keyed on the path alone) and a resolver who keeps the target's side cannot silently drop the
    # branch's own rows into a PASS. A plain branch-side deletion of
    # the declared file cannot be told from such a move and is refused the same way, the disclosed
    # over-deny.
    moved_cfg = ('schema-version = 2\n\n[[table]]\npath = "done2.md"\nformat = "markdown"\n'
                 'header = "| id | pr | closed | note |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    moved_md = ("# Done log\n\n" + header + "\n" + delimiter + "\n"
                + row_y + "\n" + row_x1 + "\n" + row_x2 + "\n")
    uloc_move_b = _v2_branch_commit(uloc, "v2-uloc-move-b", uloc_clean,
                                    {"done.md": None, "done2.md": moved_md,
                                     ".aiqt/record-sections.toml": moved_cfg},
                                    "branch moves the declared file and adds a row")
    m_move = _v2_synthetic_merge(uloc, uloc_tip_t, [uloc_tip_t, uloc_move_b],
                                 "resolver keeps the target side, branch row gone")
    _selftest_git(uloc, ["checkout", "-q", uloc_tip_t])
    expect_refusal("a branch-side move of a declared table's file fails closed through the "
                   "declarations at its path",
                   _quiet_run_refusal(uloc, uloc_config, True, m_move, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's and the pre-merge target's configs declare a "
                   "table at this path")
    with _swapped("_require_tip_file",
                  lambda branch_text, mergebase_text, declared, decl, where: None):
        expect("the moved table's dropped branch row hides once the absent-file check is removed "
               "(mutant)",
               _quiet_run(uloc, uloc_config, True, m_move, None, None), 0)
    with _swapped("_snapshot_decl_locators", mergebase_blind_locators):
        expect_refusal("with the merge-base read blinded, the moved file is still refused through "
                       "the pre-merge target's declaration at the path (mutant)",
                       _quiet_run_refusal(uloc, uloc_config, True, m_move, None, None),
                       "done.md at branch tip: the declared file is absent while it existed at the "
                       "merge-base and the pre-merge target's config declares a table at this path")
    uloc_del_b = _v2_branch_commit(uloc, "v2-uloc-del-b", uloc_clean,
                                   {"done.md": None}, "branch deletes the declared file")
    m_del = _v2_synthetic_merge(uloc, uloc_tip_t, [uloc_tip_t, uloc_del_b],
                                "merge keeps the target file")
    _selftest_git(uloc, ["checkout", "-q", uloc_tip_t])
    expect_refusal("a branch-side deletion of the declared file fails closed the same way (the "
                   "disclosed over-deny)",
                   _quiet_run_refusal(uloc, uloc_config, True, m_del, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base")
    with _swapped("_require_tip_file",
                  lambda branch_text, mergebase_text, declared, decl, where: None):
        expect("the deletion reads as a PASS once the absent-file check is removed (mutant)",
               _quiet_run(uloc, uloc_config, True, m_del, None, None), 0)

    # A declaration of ANOTHER table at an already-declared path (a new TOML array, a new Markdown
    # header) is not a declaration of the new table at the first parent, so the new table reads as
    # not-yet there while the old declaration keeps guarding its own table; matching on the path alone
    # refuses both.
    locr = directory / "v2locator"
    locr.mkdir()
    _selftest_git(locr, ["init", "-q", "-b", "main"])
    _selftest_git(locr, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(locr, ["config", "user.email", "selftest@example.invalid"])
    toml_decl = ('[[table]]\npath = "notes.toml"\nformat = "toml"\narray = "{}"\nkey = ["id"]\n'
                 'edit = "key-stable"\n')
    new_header = "| id | pr | closed | note | owner |"
    new_md_decl = ('[[table]]\npath = "done.md"\nformat = "markdown"\nheader = "' + new_header
                   + '"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    (locr / ".aiqt").mkdir()
    (locr / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + toml_decl.format("done") + "\n" + md_table + 'edit = "frozen"\n',
        encoding="utf-8")
    (locr / "notes.toml").write_text('[[done]]\nid = "N1"\n', encoding="utf-8")
    (locr / "done.md").write_text(md(row_y), encoding="utf-8")
    _selftest_git(locr, ["add", "-A"])
    _selftest_git(locr, ["commit", "-q", "-m", "two tables declared"])
    locr_base = _selftest_git(locr, ["rev-parse", "HEAD"])
    locr_config = locr / ".aiqt" / "record-sections.toml"
    for case_name, branch_name, writes in (
            ("a new TOML array declared at an already-declared path", "v2-loc-array",
             {".aiqt/record-sections.toml": "schema-version = 2\n\n" + toml_decl.format("archive")
                                            + "\n" + md_table + 'edit = "frozen"\n',
              "notes.toml": '[[done]]\nid = "N1"\n\n[[archive]]\nid = "A1"\n'}),
            ("a new Markdown header declared at an already-declared path", "v2-loc-header",
             {".aiqt/record-sections.toml": "schema-version = 2\n\n" + toml_decl.format("done")
                                            + "\n" + new_md_decl,
              "done.md": md(row_y) + "\n" + new_header + "\n| --- | --- | --- | --- | --- |\n"
                         "| Z | PR9 | 2026-03-01 | new | owner |\n"})):
        locr_next = _v2_branch_commit(locr, branch_name, locr_base, writes,
                                      "another table declared beside the first")
        expect("control: " + case_name + " reads as not-yet at the first parent",
               _quiet_run(locr, locr_config, True, locr_next, None, None), 0)
        with _swapped("_table_locator", lambda spec: spec.path):
            expect(case_name + " fails when a declaration matches on the path alone (naive variant)",
                   _quiet_run(locr, locr_config, True, locr_next, None, None), 2)

    # The branch-tip half of the same rule: a branch forked before the new header was declared leaves
    # it unlocatable in its existing done.md, and neither the branch tip's config nor the merge-base's
    # declares it, so it reads as not-yet there; matching on the path alone would read the old
    # declaration at the same path as declaring it and refuse the merge.
    locr_tip_new = _v2_branch_commit(locr, "v2-loc-tip-new", locr_base,
                                     {".aiqt/record-sections.toml":
                                          "schema-version = 2\n\n" + toml_decl.format("done")
                                          + "\n" + new_md_decl,
                                      "done.md": md(row_y) + "\n" + new_header
                                                 + "\n| --- | --- | --- | --- | --- |\n"
                                                   "| Z | PR9 | 2026-03-01 | new | owner |\n"},
                                     "the new header declared and added on the target")
    locr_tip_old = _v2_branch_commit(locr, "v2-loc-tip-old", locr_base,
                                     {"b.txt": "b\n"}, "branch forked before the new header")
    m_loc_tip = _v2_synthetic_merge(locr, locr_tip_new, [locr_tip_new, locr_tip_old],
                                    "merge keeps both tables")
    _selftest_git(locr, ["checkout", "-q", locr_tip_new])
    expect("control: a new Markdown header declared beside an already-declared path reads as not-yet "
           "at the branch tip",
           _quiet_run(locr, locr_config, True, m_loc_tip, None, None), 0)
    with _swapped("_table_locator", lambda spec: spec.path):
        expect("the pre-declaration branch tip fails when a declaration matches on the path alone "
               "(naive variant)",
               _quiet_run(locr, locr_config, True, m_loc_tip, None, None), 2)

    # The rule is bounded by the snapshot's OWN declarations: a table (and its declaration) adopted in
    # the very commit under gate, and an old branch that forked before adoption, both leave the earlier
    # snapshot's existing file legitimately table-free, so neither fails; an unbounded variant that
    # refuses every unlocatable table in an existing file refuses both.
    def unbounded_require(rows, text, declared, decl, where):
        if rows is None and text is not None:
            raise GateError("{}: unlocatable".format(where))
        return rows

    adop = directory / "v2adoption"
    adop.mkdir()
    _selftest_git(adop, ["init", "-q", "-b", "main"])
    _selftest_git(adop, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(adop, ["config", "user.email", "selftest@example.invalid"])
    (adop / "done.md").write_text("# Done log\n\nprose only, no table yet\n", encoding="utf-8")
    _selftest_git(adop, ["add", "-A"])
    _selftest_git(adop, ["commit", "-q", "-m", "file exists with no table or config"])
    adop_base = _selftest_git(adop, ["rev-parse", "HEAD"])
    adop_feat = _v2_branch_commit(adop, "v2-adop-feat", adop_base,
                                  {"b.txt": "b\n"}, "old branch from before adoption")
    adop_adopt = _v2_branch_commit(adop, "v2-adop-adopt", adop_base,
                                   {".aiqt/record-sections.toml":
                                        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n',
                                    "done.md": md(row_y)},
                                   "table and declaration adopted together")
    adop_config = adop / ".aiqt" / "record-sections.toml"
    expect("control: adopting a table into an existing file passes (the first parent never declared it)",
           _quiet_run(adop, adop_config, True, adop_adopt, None, None), 0)
    with _swapped("_require_locatable", unbounded_require):
        expect("the adoption commit fails under an unbounded unlocatable rule (naive variant)",
               _quiet_run(adop, adop_config, True, adop_adopt, None, None), 2)
    m_adop = _v2_synthetic_merge(adop, adop_adopt, [adop_adopt, adop_feat],
                                 "pre-adoption branch merges in")
    expect("control: a branch forked before adoption passes (its config never declared the table)",
           _quiet_run(adop, adop_config, True, m_adop, None, None), 0)
    with _swapped("_require_locatable", unbounded_require):
        expect("the pre-adoption branch fails under an unbounded unlocatable rule (naive variant)",
               _quiet_run(adop, adop_config, True, m_adop, None, None), 2)

    # The absent-file rule is bounded by the merge-base's FILE: a branch forked before the declared
    # file existed leaves it absent at the merge-base and the branch tip alike, and there are no branch
    # rows the absence could shed, so the merge passes. This allow control is discriminated by the
    # paired refusing fixtures on the same surface (the move and deletion fixtures above, which differ
    # only in the file existing at the merge-base); the call that would refuse it is made only while
    # the merge-base holds the file, so no swapped variant can reach it.
    prefile = directory / "v2prefile"
    prefile.mkdir()
    _selftest_git(prefile, ["init", "-q", "-b", "main"])
    _selftest_git(prefile, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(prefile, ["config", "user.email", "selftest@example.invalid"])
    (prefile / ".aiqt").mkdir()
    (prefile / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n', encoding="utf-8")
    _selftest_git(prefile, ["add", "-A"])
    _selftest_git(prefile, ["commit", "-q", "-m", "declaration adopted before the file"])
    prefile_base = _selftest_git(prefile, ["rev-parse", "HEAD"])
    prefile_feat = _v2_branch_commit(prefile, "v2-prefile-feat", prefile_base,
                                     {"b.txt": "b\n"}, "old branch from before the file")
    prefile_tgt = _v2_branch_commit(prefile, "v2-prefile-target", prefile_base,
                                    {"done.md": md(row_y)}, "target adds the declared file")
    m_prefile = _v2_synthetic_merge(prefile, prefile_tgt, [prefile_tgt, prefile_feat],
                                    "merge keeps the target file")
    prefile_config = prefile / ".aiqt" / "record-sections.toml"
    _selftest_git(prefile, ["checkout", "-q", prefile_tgt])
    expect("control: a branch forked before the declared file existed passes (the file is absent at "
           "the merge-base too)",
           _quiet_run(prefile, prefile_config, True, m_prefile, None, None), 0)

    # And by the SNAPSHOT DECLARATIONS AT THE PATH: a file the branch deleted before any table was
    # declared at its path is refused once the pre-merge target declares one there (the absent-file
    # decision reads the first parent's declarations beside the merge-base's, by path alone), while
    # a declaration adopted by the very merge under gate, where neither the merge-base's nor the
    # first parent's config declares a table at the path, leaves the deletion allowed; a variant
    # that refuses every absent branch-tip file the merge-base held refuses the allowed one too,
    # and a mutant that reverts the decision to the merge-base's full locator passes the refused one.
    def absent_file_always(branch_text, mergebase_text, declared, decl, where):
        if branch_text is None and mergebase_text is not None:
            raise GateError("{}: the declared file is absent".format(where))

    def locator_keyed_declared(decl, mergebase_locators, base_locators):
        if _table_locator(decl) in mergebase_locators:
            return "the merge-base's config declares"
        return None

    declb = directory / "v2declbound"
    declb.mkdir()
    _selftest_git(declb, ["init", "-q", "-b", "main"])
    _selftest_git(declb, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(declb, ["config", "user.email", "selftest@example.invalid"])
    (declb / ".aiqt").mkdir()
    (declb / ".aiqt" / "record-sections.toml").write_text(
        'schema-version = 2\n\n[[table]]\npath = "newlog.md"\nformat = "markdown"\n'
        'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n', encoding="utf-8")
    (declb / "done.md").write_text(md(row_y, row_x1), encoding="utf-8")
    _selftest_git(declb, ["add", "-A"])
    _selftest_git(declb, ["commit", "-q", "-m", "another table declared, this file not yet"])
    declb_base = _selftest_git(declb, ["rev-parse", "HEAD"])
    declb_tgt = _v2_branch_commit(declb, "v2-declb-target", declb_base,
                                  {".aiqt/record-sections.toml":
                                       "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n'},
                                  "target declares the table")
    declb_del = _v2_branch_commit(declb, "v2-declb-del", declb_base,
                                  {"done.md": None}, "branch deletes the not-yet-declared file")
    m_declb = _v2_synthetic_merge(declb, declb_tgt, [declb_tgt, declb_del],
                                  "merge keeps the target file")
    declb_config = declb / ".aiqt" / "record-sections.toml"
    _selftest_git(declb, ["checkout", "-q", declb_tgt])
    expect_refusal("a branch-deleted file is refused once the pre-merge target declares a table at "
                   "its path, though the merge-base's config did not",
                   _quiet_run_refusal(declb, declb_config, True, m_declb, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the pre-merge target's config declares a table at this path")
    with _swapped("_absent_file_declared", locator_keyed_declared):
        expect("the pre-declaration deletion passes once the decision reverts to the merge-base's "
               "full locator (mutant)",
               _quiet_run(declb, declb_config, True, m_declb, None, None), 0)
    declb_adopt = _v2_branch_commit(declb, "v2-declb-adopt", declb_base,
                                    {".aiqt/record-sections.toml":
                                         "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n'},
                                    "the merge itself adopts the declaration")
    m_declb_adopt = _v2_synthetic_merge(declb, declb_adopt, [declb_base, declb_del],
                                        "the adopting merge keeps the target file")
    _selftest_git(declb, ["checkout", "-q", declb_adopt])
    expect("control: the deletion merges once no applicable snapshot config declares a table at the "
           "path (the declaration is adopted by this very merge)",
           _quiet_run(declb, declb_config, True, m_declb_adopt, None, None), 0)
    with _swapped("_require_tip_file", absent_file_always):
        expect("the adopting merge fails under an absent-file rule unbounded by the snapshot "
               "declarations (naive variant)",
               _quiet_run(declb, declb_config, True, m_declb_adopt, None, None), 2)

    # The deletion/move over-deny's in-band exit, end to end: withdrawing the declaration in an
    # EARLIER, separate commit that lands on the target before the removal, and then removing the file,
    # passes, directly and through gated merges alike, while a single commit on the target that removes
    # the file and its declaration together still fails R1 under the first parent's declaration (landing
    # on the target directly is not by itself a route around the refusal). While the declaration is
    # withdrawn, a row an in-flight branch added to the old path is outside the gate, the disclosed cost
    # of that route; the paired control shows the same row loss caught while the declaration is in force.
    route = directory / "v2route"
    route.mkdir()
    _selftest_git(route, ["init", "-q", "-b", "main"])
    _selftest_git(route, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(route, ["config", "user.email", "selftest@example.invalid"])
    other_decl = ('[[table]]\npath = "newlog.md"\nformat = "markdown"\n'
                  'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    (route / ".aiqt").mkdir()
    (route / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + md_table + 'edit = "frozen"\n\n' + other_decl,
        encoding="utf-8")
    (route / "done.md").write_text(md(row_y), encoding="utf-8")
    (route / "newlog.md").write_text("# New log\n\n| id | pr |\n| --- | --- |\n| N | PR0 |\n",
                                     encoding="utf-8")
    _selftest_git(route, ["add", "-A"])
    _selftest_git(route, ["commit", "-q", "-m", "two tables declared"])
    route_base = _selftest_git(route, ["rev-parse", "HEAD"])
    route_config = route / ".aiqt" / "record-sections.toml"
    withdrawn_cfg = "schema-version = 2\n\n" + other_decl
    route_direct = _v2_branch_commit(route, "v2-route-direct", route_base,
                                     {"done.md": None,
                                      ".aiqt/record-sections.toml": withdrawn_cfg},
                                     "one commit removes the file and its declaration")
    expect("a single target commit removing a declared file and its declaration together fails R1",
           _quiet_run(route, route_config, True, route_direct, None, None), 1)
    route_w = _v2_branch_commit(route, "v2-route-withdraw", route_base,
                                {".aiqt/record-sections.toml": withdrawn_cfg},
                                "withdraw the declaration first")
    expect("control: a direct commit withdrawing the declaration alone passes",
           _quiet_run(route, route_config, True, route_w, None, None), 0)
    route_rm = _v2_branch_commit(route, "v2-route-remove", route_w,
                                 {"done.md": None}, "then remove the file")
    expect("control: a direct removal after the earlier withdrawal passes",
           _quiet_run(route, route_config, True, route_rm, None, None), 0)
    route_mw = _v2_synthetic_merge(route, route_w, [route_base, route_w],
                                   "gated merge of the withdrawal")
    _selftest_git(route, ["checkout", "-q", route_w])
    expect("control: the withdrawal lands through a gated merge too",
           _quiet_run(route, route_config, True, route_mw, None, None), 0)
    route_r2 = _v2_branch_commit(route, "v2-route-remove2", route_mw,
                                 {"done.md": None}, "remove the file on a branch")
    route_mr = _v2_synthetic_merge(route, route_r2, [route_mw, route_r2],
                                   "gated merge of the removal")
    _selftest_git(route, ["checkout", "-q", route_r2])
    expect("control: the removal lands through a gated merge once the declaration was withdrawn",
           _quiet_run(route, route_config, True, route_mr, None, None), 0)
    route_f = _v2_branch_commit(route, "v2-route-inflight", route_base,
                                {"done.md": md(row_y, row_x2)}, "in-flight branch adds a row")
    route_keep = _v2_branch_commit(route, "v2-route-keep", route_base,
                                   {"t.txt": "t\n"}, "target keeps the declaration")
    m_route_caught = _v2_synthetic_merge(route, route_keep, [route_keep, route_f],
                                         "resolver keeps the target file")
    _selftest_git(route, ["checkout", "-q", route_keep])
    expect("control: an in-flight branch row dropped while the declaration is in force is caught",
           _quiet_run(route, route_config, True, m_route_caught, None, None), 1)
    m_route_window = _v2_synthetic_merge(route, route_mr, [route_mr, route_f],
                                         "resolver keeps the removed state")
    _selftest_git(route, ["checkout", "-q", route_r2])
    expect("an in-flight branch row added to the old path is outside the gate while the declaration "
           "is withdrawn (the disclosed cost of the in-band exit)",
           _quiet_run(route, route_config, True, m_route_window, None, None), 0)

    # The header-change over-deny holds only while the first parent's declaration is in force, so a
    # gated merge that withdraws the declaration and a later gated merge that re-declares it under a
    # changed header both pass. The absent-file decision keys on the PATH alone, so for a branch
    # forked before them that moved the declared file and added a row under the new path, the old
    # declaration at the merge-base and the re-declared one at the pre-merge target both mark the
    # path as declared and the merge is refused even when the resolver keeps the target's side. A
    # mutant that reverts the decision to the merge-base's full locator reads the changed-locator
    # absence as the table not yet existing and passes the same drop silently: the gap this key
    # closes. The paired control refuses the same branch against a target that kept the declaration.
    route_rd = _v2_branch_commit(route, "v2-route-redeclare", route_mw,
                                 {".aiqt/record-sections.toml": renamed_cfg + "\n" + other_decl,
                                  "done.md": ("# Done log\n\n" + renamed_header + "\n" + delimiter
                                              + "\n" + row_y + "\n")},
                                 "re-declare the table under a changed header")
    route_mrd = _v2_synthetic_merge(route, route_rd, [route_mw, route_rd],
                                    "gated merge of the re-declaration")
    _selftest_git(route, ["checkout", "-q", route_rd])
    expect("control: the re-declaration under a changed header lands through a gated merge once the "
           "declaration was withdrawn",
           _quiet_run(route, route_config, True, route_mrd, None, None), 0)
    route_mv = _v2_branch_commit(route, "v2-route-move", route_base,
                                 {"done.md": None, "done2.md": md(row_y, row_x2),
                                  ".aiqt/record-sections.toml": moved_cfg + "\n" + other_decl},
                                 "branch forked before both moves the file and adds a row")
    m_route_shed = _v2_synthetic_merge(route, route_rd, [route_mrd, route_mv],
                                       "resolver keeps the target side, branch row gone")
    _selftest_git(route, ["checkout", "-q", route_rd])
    expect_refusal("a moved branch merging after a withdraw-then-redeclare header change through "
                   "gated merges is refused on the path alone",
                   _quiet_run_refusal(route, route_config, True, m_route_shed, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's and the pre-merge target's configs declare a "
                   "table at this path")
    with _swapped("_absent_file_declared", locator_keyed_declared):
        expect("the same dropped branch row passes once the decision reverts to the merge-base's "
               "full locator (mutant: the gap the path key closes)",
               _quiet_run(route, route_config, True, m_route_shed, None, None), 0)
    m_route_move_caught = _v2_synthetic_merge(route, route_keep, [route_keep, route_mv],
                                              "resolver keeps the target side, branch row gone")
    _selftest_git(route, ["checkout", "-q", route_keep])
    expect_refusal("control: the same moved branch is refused against a target that kept the declaration",
                   _quiet_run_refusal(route, route_config, True, m_route_move_caught, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's and the pre-merge target's configs declare a "
                   "table at this path")

    # A branch that carries the withdrawal and the removal into one gated merge is still refused:
    # the pre-merge target's config still declares the table, which keeps the absent-file check
    # running for its path, and the merge-base's config declares a table there.
    route_bw = _v2_branch_commit(route, "v2-route-both-w", route_base,
                                 {".aiqt/record-sections.toml": withdrawn_cfg},
                                 "the branch withdraws the declaration")
    route_bwr = _v2_branch_commit(route, "v2-route-both-rm", route_bw,
                                  {"done.md": None}, "then removes the file on the same branch")
    m_route_both = _v2_synthetic_merge(route, route_keep, [route_keep, route_bwr],
                                       "one gated merge carries both")
    _selftest_git(route, ["checkout", "-q", route_keep])
    expect_refusal("a branch carrying the withdrawal and the removal into one gated merge is still "
                   "refused",
                   _quiet_run_refusal(route, route_config, True, m_route_both, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's and the pre-merge target's configs declare a "
                   "table at this path")

    # Only the merge-base declares at the path here: the pre-merge target carries the withdrawal, and
    # the merge's own working config re-declares the table while integrating the moved branch, so the
    # refusal rests on the merge-base read alone and blinding that read passes the merge, showing it
    # load-bearing on the absent-file path. The in-band exit is to integrate the moved branch while
    # the declaration at the path is withdrawn and re-declare in a later commit.
    m_route_mbonly = _v2_synthetic_merge(route, route_rd, [route_mw, route_mv],
                                         "the re-declaring merge integrates the moved branch")
    _selftest_git(route, ["checkout", "-q", route_rd])
    expect_refusal("a merge whose working config re-declares a table at a path the merge-base "
                   "declared refuses the moved branch through the merge-base alone",
                   _quiet_run_refusal(route, route_config, True, m_route_mbonly, None, None),
                   "done.md at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's config declares a table at this path")
    with _swapped("_snapshot_decl_locators", mergebase_blind_locators):
        expect("the moved branch merges once the merge-base read is blinded and the pre-merge "
               "target's config does not declare the path (mutant)",
               _quiet_run(route, route_config, True, m_route_mbonly, None, None), 0)

    # The path key covers every locator change, not Markdown headers alone: a TOML table withdrawn
    # and re-declared under another ARRAY NAME at the same path, each step through a gated merge,
    # still refuses a branch forked before both that moved the declared file and added a row, where
    # the merge-base's-locator decision would pass the drop silently (the mutant shows that gap).
    toml_route_decl = ('[[table]]\npath = "notes.toml"\nformat = "toml"\narray = "{}"\n'
                       'key = ["id"]\nedit = "key-stable"\n')
    moved_toml_decl = ('[[table]]\npath = "notes2.toml"\nformat = "toml"\narray = "done"\n'
                       'key = ["id"]\nedit = "key-stable"\n')
    pathk = directory / "v2pathkey"
    pathk.mkdir()
    _selftest_git(pathk, ["init", "-q", "-b", "main"])
    _selftest_git(pathk, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(pathk, ["config", "user.email", "selftest@example.invalid"])
    (pathk / ".aiqt").mkdir()
    (pathk / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + toml_route_decl.format("done") + "\n" + other_decl,
        encoding="utf-8")
    (pathk / "notes.toml").write_text('[[done]]\nid = "N1"\n', encoding="utf-8")
    _selftest_git(pathk, ["add", "-A"])
    _selftest_git(pathk, ["commit", "-q", "-m", "a TOML table declared"])
    pathk_base = _selftest_git(pathk, ["rev-parse", "HEAD"])
    pathk_config = pathk / ".aiqt" / "record-sections.toml"
    pathk_w = _v2_branch_commit(pathk, "v2-pathk-withdraw", pathk_base,
                                {".aiqt/record-sections.toml":
                                     "schema-version = 2\n\n" + other_decl},
                                "withdraw the declaration first")
    pathk_mw = _v2_synthetic_merge(pathk, pathk_w, [pathk_base, pathk_w],
                                   "gated merge of the withdrawal")
    _selftest_git(pathk, ["checkout", "-q", pathk_w])
    expect("control: the TOML withdrawal lands through a gated merge",
           _quiet_run(pathk, pathk_config, True, pathk_mw, None, None), 0)
    pathk_rd = _v2_branch_commit(pathk, "v2-pathk-redeclare", pathk_mw,
                                 {".aiqt/record-sections.toml":
                                      "schema-version = 2\n\n" + toml_route_decl.format("archive")
                                      + "\n" + other_decl,
                                  "notes.toml": '[[archive]]\nid = "N1"\n'},
                                 "re-declare the table under another array name")
    pathk_mrd = _v2_synthetic_merge(pathk, pathk_rd, [pathk_mw, pathk_rd],
                                    "gated merge of the re-declaration")
    _selftest_git(pathk, ["checkout", "-q", pathk_rd])
    expect("control: the re-declaration under another array name lands through a gated merge once "
           "the declaration was withdrawn",
           _quiet_run(pathk, pathk_config, True, pathk_mrd, None, None), 0)
    pathk_mv = _v2_branch_commit(pathk, "v2-pathk-move", pathk_base,
                                 {"notes.toml": None,
                                  "notes2.toml": '[[done]]\nid = "N1"\n\n[[done]]\nid = "N2"\n',
                                  ".aiqt/record-sections.toml":
                                      "schema-version = 2\n\n" + moved_toml_decl + "\n" + other_decl},
                                 "branch forked before both moves the file and adds a row")
    m_pathk_shed = _v2_synthetic_merge(pathk, pathk_rd, [pathk_mrd, pathk_mv],
                                       "resolver keeps the target side, branch row gone")
    _selftest_git(pathk, ["checkout", "-q", pathk_rd])
    expect_refusal("a moved branch merging after a withdraw-then-redeclare ARRAY RENAME through "
                   "gated merges is refused on the path alone",
                   _quiet_run_refusal(pathk, pathk_config, True, m_pathk_shed, None, None),
                   "notes.toml at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's and the pre-merge target's configs declare a "
                   "table at this path")
    with _swapped("_absent_file_declared", locator_keyed_declared):
        expect("the array-renamed drop passes once the decision reverts to the merge-base's full "
               "locator (mutant: the gap the path key closes)",
               _quiet_run(pathk, pathk_config, True, m_pathk_shed, None, None), 0)

    # And FORMAT changes: a table declared as TOML at the fork, withdrawn and re-declared as Markdown
    # at the same path by direct target commits, still refuses the moved branch; the direction is
    # TOML at the fork because a Markdown merge-base snapshot read under a TOML declaration is a
    # fail-closed parse of its own, while an unlocatable Markdown read returns no rows.
    rec_toml_decl = ('[[table]]\npath = "records"\nformat = "toml"\narray = "done"\nkey = ["id"]\n'
                     'edit = "key-stable"\n')
    rec_md_decl = ('[[table]]\npath = "records"\nformat = "markdown"\nheader = "| id | pr |"\n'
                   'key = ["id", "pr"]\nedit = "frozen"\n')
    rec2_toml_decl = ('[[table]]\npath = "records2"\nformat = "toml"\narray = "done"\nkey = ["id"]\n'
                      'edit = "key-stable"\n')
    fmtc = directory / "v2formatchange"
    fmtc.mkdir()
    _selftest_git(fmtc, ["init", "-q", "-b", "main"])
    _selftest_git(fmtc, ["config", "user.name", "AIQT Self-Test"])
    _selftest_git(fmtc, ["config", "user.email", "selftest@example.invalid"])
    (fmtc / ".aiqt").mkdir()
    (fmtc / ".aiqt" / "record-sections.toml").write_text(
        "schema-version = 2\n\n" + rec_toml_decl + "\n" + other_decl, encoding="utf-8")
    (fmtc / "records").write_text('[[done]]\nid = "N1"\n', encoding="utf-8")
    _selftest_git(fmtc, ["add", "-A"])
    _selftest_git(fmtc, ["commit", "-q", "-m", "a table declared as TOML"])
    fmtc_base = _selftest_git(fmtc, ["rev-parse", "HEAD"])
    fmtc_config = fmtc / ".aiqt" / "record-sections.toml"
    fmtc_w = _v2_branch_commit(fmtc, "v2-fmtc-withdraw", fmtc_base,
                               {".aiqt/record-sections.toml":
                                    "schema-version = 2\n\n" + other_decl},
                               "withdraw the declaration directly")
    expect("control: the direct withdrawal before the format change passes",
           _quiet_run(fmtc, fmtc_config, True, fmtc_w, None, None), 0)
    fmtc_rd = _v2_branch_commit(fmtc, "v2-fmtc-redeclare", fmtc_w,
                                {".aiqt/record-sections.toml":
                                     "schema-version = 2\n\n" + rec_md_decl + "\n" + other_decl,
                                 "records": ("# Records\n\n| id | pr |\n| --- | --- |\n"
                                             "| Y | PR0 |\n")},
                                "re-declare the same path as Markdown")
    expect("control: the direct re-declaration under a changed format passes once the declaration "
           "was withdrawn",
           _quiet_run(fmtc, fmtc_config, True, fmtc_rd, None, None), 0)
    fmtc_mv = _v2_branch_commit(fmtc, "v2-fmtc-move", fmtc_base,
                                {"records": None,
                                 "records2": '[[done]]\nid = "N1"\n\n[[done]]\nid = "N2"\n',
                                 ".aiqt/record-sections.toml":
                                     "schema-version = 2\n\n" + rec2_toml_decl + "\n" + other_decl},
                                "branch forked before both moves the file and adds a row")
    m_fmtc_shed = _v2_synthetic_merge(fmtc, fmtc_rd, [fmtc_rd, fmtc_mv],
                                      "resolver keeps the target side, branch row gone")
    _selftest_git(fmtc, ["checkout", "-q", fmtc_rd])
    expect_refusal("a moved branch merging after a withdraw-then-redeclare FORMAT change is refused "
                   "on the path alone",
                   _quiet_run_refusal(fmtc, fmtc_config, True, m_fmtc_shed, None, None),
                   "records at branch tip: the declared file is absent while it existed at the "
                   "merge-base and the merge-base's and the pre-merge target's configs declare a "
                   "table at this path")
    with _swapped("_absent_file_declared", locator_keyed_declared):
        expect("the format-changed drop passes once the decision reverts to the merge-base's full "
               "locator (mutant: the gap the path key closes)",
               _quiet_run(fmtc, fmtc_config, True, m_fmtc_shed, None, None), 0)

    return failures


def _heading_pattern_self_test_cases(directory):
    """A heading-pattern the compiler rejects with something other than re.error must still be a
    contextual exit-2 refusal naming the config, in the working config and in the first parent's config.

    A repetition count past the engine's limit raises OverflowError from a real pattern, and one past
    CPython's integer string conversion limit raises a bare ValueError from a real pattern (re's parser
    converts the count with int()); the conversion limit is pinned to the default 4300 digits for the
    ValueError case, so the 4400-digit count overflows it whatever widening a caller applied
    (test hermeticity). A recursion overflow and a memory exhaustion are INJECTED (re.compile raises
    RecursionError or MemoryError on a marked pattern) rather than provoked by a deeply nested or
    memory-exhausting one, because the depth that overflows depends on the interpreter's recursion limit
    and a real allocation failure would destabilize the suite. Each refusal is paired with a mutant whose
    compile step catches re.error only, under which the same fixture lets the exception escape run().
    Returns failure strings.
    """
    failures = []

    def outcome(repo_root, config, post):
        try:
            return _quiet_run_refusal(repo_root, config, True, post, None, None)
        except (OverflowError, ValueError, RecursionError, MemoryError) as exc:
            return "escaped", type(exc).__name__

    def expect_refusal(name, result, needle):
        code, text = result
        if code != 2 or needle not in text:
            failures.append("pattern {}: got ({}, {!r}), want exit 2 naming {!r}"
                            .format(name, code, text.strip(), needle))

    def expect_escape(name, result, exc_name):
        if result != ("escaped", exc_name):
            failures.append("pattern {}: got {!r}, want a bare {} escaping run()"
                            .format(name, result, exc_name))

    def re_error_only(pattern, where):
        try:
            return re.compile(pattern)
        except re.error as exc:
            raise GateError("{}: heading-pattern is invalid ({})".format(where, exc))

    def v1_config(pattern):
        return ('schema-version = 1\n'
                'opt-out-marker = "<!-- aiqt-record-section: superseded -->"\n\n'
                '[[record]]\npath = "records.md"\nheading-pattern = \'' + pattern + "'\n")

    tables_only = ('schema-version = 2\n\n[[table]]\npath = "done.md"\nformat = "markdown"\n'
                   'header = "| id | pr |"\nkey = ["id", "pr"]\nedit = "frozen"\n')
    real_compile = re.compile

    def injected_compile(pattern, flags=0):
        if isinstance(pattern, str) and "injected-recursion" in pattern:
            raise RecursionError("injected compiler recursion overflow")
        if isinstance(pattern, str) and "injected-memory" in pattern:
            raise MemoryError("injected compiler memory exhaustion")
        return real_compile(pattern, flags)

    prev_digits = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        _heading_pattern_cases(directory, outcome, expect_refusal, expect_escape,
                               re_error_only, v1_config, tables_only,
                               injected_compile, real_compile)
    finally:
        sys.set_int_max_str_digits(prev_digits)
    return failures


def _heading_pattern_cases(directory, outcome, expect_refusal, expect_escape, re_error_only,
                           v1_config, tables_only, injected_compile, real_compile):
    """One fixture repo per exception class the compile boundary must catch beyond re.error; every
    outcome is recorded through the expect callbacks, which append to the caller's failure list."""
    for label, pattern, exc_name, needle_detail in (
            ("a repetition count past the engine's limit", "^## a{4294967296}$", "OverflowError",
             "OverflowError: the repetition number is too large"),
            ("a repetition count past the integer string conversion limit",
             "^## a{" + "9" * 4400 + "}$", "ValueError",
             "ValueError: Exceeds the limit"),
            ("an injected compiler recursion overflow", "^## injected-recursion$", "RecursionError",
             "RecursionError: injected compiler recursion overflow"),
            ("an injected compiler memory exhaustion", "^## injected-memory$", "MemoryError",
             "MemoryError: injected compiler memory exhaustion")):
        repo = directory / "pattern-{}".format(exc_name.lower())
        repo.mkdir()
        _selftest_git(repo, ["init", "-q", "-b", "main"])
        _selftest_git(repo, ["config", "user.name", "AIQT Self-Test"])
        _selftest_git(repo, ["config", "user.email", "selftest@example.invalid"])
        (repo / ".aiqt").mkdir()
        (repo / ".aiqt" / "record-sections.toml").write_text(v1_config(pattern), encoding="utf-8")
        (repo / "records.md").write_text("# R\n\n## 2026-08-01 Existing\n", encoding="utf-8")
        (repo / "done.md").write_text("| id | pr |\n| --- | --- |\n| A1 | PR0 |\n", encoding="utf-8")
        _selftest_git(repo, ["add", "-A"])
        _selftest_git(repo, ["commit", "-q", "-m", "first parent carries the pattern"])
        parent = _selftest_git(repo, ["rev-parse", "HEAD"])
        child = _v2_branch_commit(repo, "pattern-next", parent,
                                  {".aiqt/record-sections.toml": tables_only},
                                  "valid tables-only config replaces it")
        working = directory / "pattern-working-{}.toml".format(exc_name.lower())
        working.write_text(v1_config(pattern), encoding="utf-8")
        historical = repo / ".aiqt" / "record-sections.toml"
        re.compile = injected_compile
        try:
            expect_refusal(label + " in the working config",
                           outcome(repo, working, child),
                           "{} [[record]] #1: heading-pattern is invalid ({}".format(
                               working.name, needle_detail))
            expect_refusal(label + " in the first parent's config",
                           outcome(repo, historical, child),
                           ".aiqt/record-sections.toml at the pre-merge target [[record]] #1: "
                           "heading-pattern is invalid ({}".format(needle_detail))
            with _swapped("_compile_heading_pattern", re_error_only):
                expect_escape(label + " in the working config escapes once only re.error is "
                              "caught (mutant)", outcome(repo, working, child), exc_name)
                expect_escape(label + " in the first parent's config escapes once only re.error is "
                              "caught (mutant)", outcome(repo, historical, child), exc_name)
        finally:
            re.compile = real_compile


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
