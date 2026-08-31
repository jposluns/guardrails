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

Config schema:

  schema-version = 1
  opt-out-marker = "<!-- aiqt-record-section: superseded -->"

  [[record]]
  path = "docs/decision-log.md"
  heading-pattern = '^## [0-9]{4}-[0-9]{2}-[0-9]{2}(?: .+)?$'

The marker is recognized only as a whole line inside the branch-owned section it excuses. A global marker,
a marker in another section, or a marker added only to the post-merge file does not excuse a missing section.

Exit convention:
  0  every in-scope branch-owned heading is present after the merge, or explicitly opted out; also NOT
     APPLICABLE when the default config is absent AND was not present at the pre-merge target, because this
     checkout never adopted the gate
  1  at least one in-scope branch-owned heading was dropped from the post-merge commit
  2  malformed or unreadable config/input, a config path whose canonicalization net-redirects through a
     symlink (its real target differs from the literal name) or that resolves outside the repository root,
     an invalid config path (for example one carrying an embedded NUL byte), a default config the merge
     itself removed, an
     unresolvable ref (including an unresolvable post-merge ref while the removal check runs), a missing
     declared branch record, duplicate selected headings, a non-unique merge-base on either the default or the
     explicit-base path, an explicit --base-ref that is not that unique merge-base, a branch ref that cannot be
     inferred, an explicit --branch-ref that disagrees with the observable second parent of a two-parent
     post-merge commit, or an octopus (three-or-more-parent) post-merge commit

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

The config is trusted adopter input to this preservation check, so the gate does not defend the config's own
integrity: rewriting the adopter's config so its heading-pattern selects fewer or no headings (semantic
narrowing that makes real sections stop matching) is a change to that trusted config, not a dropped section
this gate detects. Guarding the config against adversarial narrowing is out of this gate's threat model; it is
the config-integrity surface's concern, not this one's.

Those boundaries are deliberate; the PASS result prints only the scope note that it compares selected new
headings and not bodies or pre-existing headings, so the other boundaries above live here rather than in that
line. Missing or ambiguous inputs inside the declared surface fail closed.
"""
import argparse
import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_record_sections.py requires Python 3.11+ (tomllib).")

CONFIG_REL = ".aiqt/record-sections.toml"
OID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
TOP_KEYS = {"schema-version", "opt-out-marker", "record"}
ROW_KEYS = {"path", "heading-pattern"}


class GateError(Exception):
    """An input the gate needs cannot be read or interpreted. The caller reports exit 2."""


@dataclass(frozen=True)
class RecordSpec:
    path: str
    heading_pattern: re.Pattern


def _clean_env():
    """Return os.environ with the entire GIT_ family removed (allowlist stance).

    Ambient GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_COMMON_DIR, and every other
    GIT_-prefixed variable can redirect git's whole view, including -C and --show-toplevel, to an
    attacker-controlled decoy repository, so the gate strips the family wholesale before every git call
    and re-applies only what it sets itself, which is nothing.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git(root, args):
    """Run git without a shell and return stdout bytes. Any invocation failure is fail-closed.

    Every invocation carries --no-replace-objects, so a local git replacement ref (refs/replace/*)
    cannot substitute a crafted object for a commit the gate reads. Without it, `git replace <post>
    <obj-with-benign-parents>` rewrites the parent list `show -s --format=%P` reports, letting a real
    merge that dropped a section masquerade as one whose branch tip owns no new section (exit 0).
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
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot parse config {} ({})".format(path, exc))
    if not isinstance(data, dict) or set(data) != TOP_KEYS:
        raise GateError("{}: top-level keys must be exactly {}".format(path, sorted(TOP_KEYS)))
    version = data.get("schema-version")
    if type(version) is not int or version != 1:
        raise GateError("{}: schema-version must be integer 1".format(path))
    marker = data.get("opt-out-marker")
    if (not isinstance(marker, str) or not marker or marker != marker.strip()
            or "\n" in marker or "\r" in marker or marker.startswith("## ")):
        raise GateError("{}: opt-out-marker must be one non-heading line with no surrounding whitespace"
                        .format(path))
    rows = data.get("record")
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
    return marker, tuple(specs)


def _resolve_commit(root, ref, label):
    if not isinstance(ref, str) or not ref or any(ord(ch) < 32 for ch in ref):
        raise GateError("{} must be a non-empty ref with no control characters".format(label))
    out = _text(_git(root, ["rev-parse", "--verify", "--end-of-options",
                            "{}^{{commit}}".format(ref)]), label).splitlines()
    if len(out) != 1 or not OID_RE.fullmatch(out[0]):
        raise GateError("{} did not resolve to exactly one commit object".format(label))
    return out[0]


def _parents(root, commit):
    out = _text(_git(root, ["show", "-s", "--format=%P", commit]),
                "post-merge parents").strip()
    parents = out.split() if out else []
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
        marker, specs = config
        base, branch, post_merge = resolve_graph(
            root, post_merge_ref, branch_ref, base_ref)
        total = 0
        opted_out = 0
        findings = []
        for spec in specs:
            owned, opted, record_findings = compare_record(
                spec, marker,
                _tree_text(root, base, spec.path),
                _tree_text(root, branch, spec.path),
                _tree_text(root, post_merge, spec.path))
            total += owned
            opted_out += opted
            findings.extend(record_findings)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} of {} branch-owned record section(s) were dropped post-merge"
              .format(len(findings), total))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: {} branch-owned record section(s) were preserved; {} carried the "
          "explicit opt-out. SCOPE: selected new headings only; bodies and "
          "pre-existing headings are not compared."
          .format(total - opted_out, opted_out))
    return 0


def _selftest_git(root, args, input_text=None):
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env=_clean_env())
    if proc.returncode != 0:
        raise RuntimeError("self-test git {} failed ({})"
                           .format(args[0], proc.returncode))
    return proc.stdout.strip()


def _quiet_run(*args):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(*args)


def self_test():
    import tempfile

    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append("{}: got {}, want {}".format(name, got, want))

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
          "replacement ref rewriting a merge's parents, a legitimate '//abs' "
          "config path, an embedded NUL in the config path, and a symlink "
          "cancelled back to the same literal config path all "
          "produce the required 0/1/2 decisions")
    return 0


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
