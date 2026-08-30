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
branch ref available and pass --branch-ref. --base-ref is optional; absent it, the gate requires exactly one
merge-base between the post-merge commit's first parent and the branch tip. An explicit --base-ref must itself
name a real merge-base of the target and branch (the unique merge-base, or one of them when several exist); a
base that is only a common ancestor is rejected.

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
  2  malformed or unreadable config/input, a default config the merge itself removed, an unresolvable ref, a
     missing declared branch record, duplicate selected headings, a non-unique merge-base when the gate
     computes the base itself, an explicit --base-ref that is not a merge-base, or a branch ref that cannot
     be inferred

RESIDUAL. This gate proves heading preservation only for newly introduced headings selected by the adopter's
patterns. It does not compare section bodies, cover edits to a heading already present at merge-base, detect a
renamed heading as preserved, or prove that an opt-out marker represents a valid supersession. A record path
or heading outside the config is outside its surface. An explicit --base-ref is constrained to a real
merge-base of the target and branch, not merely a common ancestor, so it cannot suppress a branch-owned
section by naming an ancestor that already contains it; a non-unique merge-base fails closed only on the
default path, where the gate computes the base itself. Those boundaries are deliberate and printed in the
PASS result; missing or ambiguous inputs inside the declared surface fail closed.
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
    """Run git without a shell and return stdout bytes. Any invocation failure is fail-closed."""
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE,
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
    candidate = Path(value) if Path(value).is_absolute() else root / value
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise GateError("config path must resolve inside the repository root")
    return resolved


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
    """Return (marker, record specs), or None only for an absent default adopter config."""
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
        proc = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor",
                               ancestor, descendant], stdout=subprocess.PIPE,
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
    if branch_ref is None:
        if len(parents) != 2:
            raise GateError(
                "cannot infer the branch tip: post-merge commit must have exactly two parents; "
                "pass --branch-ref for squash, rebase, or octopus integration")
        branch = parents[1]
    else:
        branch = _resolve_commit(root, branch_ref, "branch ref")
    if base_ref is not None:
        base = _resolve_commit(root, base_ref, "base ref")
        bases = _merge_base_all(root, target, branch)
        if bases and base not in bases:
            raise GateError(
                "explicit base ref is not a merge-base of target and branch; a supplied --base-ref "
                "must name a real merge-base (the unique one, or one of several), not merely a "
                "common ancestor")
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
    is read from that commit's tree at config_rel, the repo-relative path that was looked for. When no
    target parent can be established (a parentless post-merge commit, or an unresolvable ref or tree),
    the question cannot be answered, so this reports False and the caller keeps NOT APPLICABLE rather
    than crashing.
    """
    if config_rel is None:
        return False
    try:
        post_merge = _resolve_commit(root, post_merge_ref, "post-merge ref")
        parents = _parents(root, post_merge)
        if not parents:
            return False
        return _tree_text(root, parents[0], config_rel) is not None
    except GateError:
        return False


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
          "config removed by the merge, and a non-merge-base explicit base "
          "all produce the required 0/1/2 decisions")
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
        help="merge-base ref; computed from target and branch when omitted. An explicit value must be a "
             "real merge-base, not merely a common ancestor")
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
