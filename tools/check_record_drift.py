#!/usr/bin/env python3
"""Record-drift gate (GD-112 component 2): an operational record row that says merge_pending while
the truth sources already carry the merge is drift, and drift FAILS.

Truth sources: the optional registry-declared `truth.changelog` file's per-release `refs` arrays (a
declared-but-absent or malformed changelog is a fail-closed exit 2, never a silent skip; omit the field
to disable this probe), and first-parent merge evidence over the FULL git history (this repo
squash-merges, so a merged PR leaves one first-parent commit whose subject carries `(#NN)`; scanned
with no cap, so a PR merged far back is never missed; verified against the live history 2026-08-29).
Matching is TYPED refs only (`pr:17`, `decision:GD-110`): a narrative number in prose never matches
(D9). Fail-closed: a merge_pending row that cannot be parsed, a duplicate ref, an unreadable declared
register, an unreadable or malformed declared changelog, or unreadable history is exit 2, never a
silent pass (chkfcl). Registry PRESENCE uses the register gate's own chain-confirmed entry
inspection and its non-blocking type-checked reader: only a registry path CONFIRMED absent (every
missing component a plain ENOENT under a parent that opens, inspected without following links)
reads as no registry; a dangling symbolic link at the registry path or on its parent chain, any
other unresolvable registry path, and a registry that is not a regular file (a FIFO included,
refused by the opened descriptor's type, never a blocked read) are each exit 2, never NOT
APPLICABLE.

Row grammar the gate reads (only rows carrying `merge_pending` are in scope):
  - <ID> :: OPEN :: merge_pending :: refs=pr:17[,decision:GD-110] :: <title>

Scope honesty: with NO orchestration registry, or a registry that declares no findings surface,
the live leg reports NOT APPLICABLE (the record is genuinely outside this checkout's declared work,
the check_crosswalk precedent); the self-test carries the assurance in CI.
  check_record_drift.py              run the live leg
  check_record_drift.py --self-test  synthetic fixtures: detection and every fail-closed branch
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_record_drift.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import os
import re
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from check_mistakes_register import (  # noqa: E402
    CannotEvaluate, _read_following, entry_state)

REGISTRY_FILES = (".aiqt/orchestration.local.json", ".aiqt/orchestration.json")
ROW_RE = re.compile(r"^-\s+(?P<id>\S+)\s*::\s*(?P<state>\w+)\s*::\s*merge_pending\s*::\s*"
                    r"refs=(?P<refs>\S+)\s*::")
REF_RE = re.compile(r"^(pr:\d+|decision:[A-Za-z0-9_.-]+)$")


def load_registry(root):
    """The first declared registry, or None only when every registry path is CONFIRMED absent.
    Presence is the register gate's chain-confirming entry_state (only a missing final name
    under a parent that opens, or a parent chain itself confirmed absent component by
    component, reads as no registry; a dangling symbolic link at the registry path or on its
    parent chain, or any other unresolvable path, raises CannotEvaluate, never NOT
    APPLICABLE), and the read is the gate's non-blocking type-checked reader, so a registry
    that is not a regular file (a FIFO included) is refused by name, never a blocked read."""
    for rel in REGISTRY_FILES:
        p = root / rel
        if entry_state(p) is None:
            continue  # confirmed absence: the next registry file may still be present
        data = json.loads(_read_following(p, "the orchestration registry").decode("utf-8"))
        # CONV4-CL3 (D13): require the version to be the int 1 by TYPE, so a bool True (1 == True in
        # Python) or a float 1.0 cannot satisfy the version-1 gate and slip a malformed registry through.
        if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1:
            raise ValueError("{}: not a version-1 registry".format(rel))
        return data
    return None


def parse_rows(text):
    """(rows, errors): rows are (id, refs list); a line naming merge_pending that fails the grammar
    or carries a malformed or duplicate ref is an ERROR, never silently dropped."""
    rows, errors, seen = [], [], set()
    for line in text.splitlines():
        if "merge_pending" not in line:
            continue
        m = ROW_RE.match(line.strip())
        if not m:
            errors.append("unparseable merge_pending row: {!r}".format(line.strip()[:120]))
            continue
        refs = m.group("refs").split(",")
        bad = [r for r in refs if not REF_RE.match(r)]
        if bad:
            errors.append("row {}: malformed ref(s) {}".format(m.group("id"), ", ".join(bad)))
            continue
        dup = sorted({r for r in refs if r in seen or refs.count(r) > 1})
        if dup:
            errors.append("row {}: duplicate ref(s) {}".format(m.group("id"), ", ".join(dup)))
            continue
        seen.update(refs)
        rows.append((m.group("id"), refs))
    return rows, errors


def merged_pr_numbers(repo):
    """First-parent squash-merge evidence: the set of NN with a subject carrying (#NN), over the FULL
    first-parent history (no cap, so a PR merged far back is never invisibly missed). Raises on an
    unreadable history (fail-closed)."""
    result = subprocess.run(["git", "-C", str(repo), "log", "--first-parent", "--format=%s"],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError("git log failed: {}".format(result.stderr.strip()))
    return set(re.findall(r"\(#(\d+)\)", result.stdout))


def changelog_refs(path):
    """Every typed ref recorded on any release's optional `refs` array. Raises on a malformed file."""
    data = load_toml(path)
    out = set()
    for rel in data.get("release", []):
        refs = rel.get("refs", [])
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise ValueError("a release carries a malformed refs array")
        out.update(refs)
    return out


def run(root):
    try:
        reg = load_registry(root)
    except (OSError, ValueError, CannotEvaluate) as exc:
        print("error: cannot read the orchestration registry: {}".format(exc))
        return 2
    if reg is None:
        print("NOT APPLICABLE: no orchestration registry in this checkout; the self-test carries "
              "the assurance")
        return 0
    truth = reg.get("truth") if isinstance(reg.get("truth"), dict) else {}
    declared_changelog = truth.get("changelog")
    if "changelog" in truth and (not isinstance(declared_changelog, str) or not declared_changelog):
        print("error: registry truth.changelog must be a non-empty path; fail-closed")
        return 2
    changelog = None
    if declared_changelog:
        changelog = Path(declared_changelog) if os.path.isabs(declared_changelog) \
            else root / declared_changelog
    rec = reg.get("record") if isinstance(reg.get("record"), dict) else {}
    declared = rec.get("findings")
    if not declared:
        print("NOT APPLICABLE: the registry declares no findings surface")
        return 0
    path = Path(declared) if os.path.isabs(declared) else root / declared
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print("error: the DECLARED findings register is unreadable ({}); a declared input "
              "never reads as nothing to check".format(exc))
        return 2
    rows, errors = parse_rows(text)
    if errors:
        print("error: {} malformed merge_pending row(s):".format(len(errors)))
        for e in errors:
            print("  " + e)
        return 2
    try:
        recorded = changelog_refs(changelog) if changelog is not None else set()
    except (OSError, ValueError) as exc:
        print("error: the declared changelog is unreadable ({}); fail-closed".format(exc))
        return 2
    if not rows:
        print("PASS: no merge_pending rows to reconcile")
        return 0
    try:
        merged = merged_pr_numbers(root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("error: first-parent history is unreadable ({}); fail-closed".format(exc))
        return 2
    findings = []
    for rid, refs in rows:
        for ref in refs:
            if ref.startswith("pr:") and ref[3:] in merged:
                findings.append("{}: {} already merged on the first-parent line, but the row is "
                                "still merge_pending".format(rid, ref))
            if ref in recorded:
                findings.append("{}: {} already recorded in the declared changelog refs, but the row "
                                "is still merge_pending".format(rid, ref))
    if findings:
        print("FAIL: {} record-drift finding(s):".format(len(findings)))
        for f in findings:
            print("  " + f)
        return 1
    print("PASS: every merge_pending row is unmerged against the full first-parent history and any "
          "declared changelog")
    return 0


def self_test():
    import shutil
    import tempfile
    # Hermetic git fixtures (test-hermeticity): every fixture git call below inherits
    # os.environ, where an inherited GIT_INDEX_FILE / GIT_DIR (git exports these to hook
    # children) would redirect the fixture's init/add/commit into the CALLER's repository.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import scrub_git_environment
    scrub_git_environment()
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-record-drift-"))
    failures = []

    def _case(name, got, want):
        if got != want:
            failures.append("{}: got {}, want {}".format(name, got, want))

    try:
        repo = tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                       capture_output=True, timeout=30)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        for msg in ("seed", "fix the gate (#17)"):
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                           capture_output=True, timeout=30)
            (repo / "f.txt").write_text(msg + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=T",
                            "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                            "commit", "-aq", "--allow-empty", "-m", msg],
                           check=True, capture_output=True, timeout=30)
        (repo / ".aiqt").mkdir()
        truth_dir = repo / "truth"
        truth_dir.mkdir()
        changelog = truth_dir / "releases.toml"
        register = repo / "findings.md"
        (repo / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "record": {"findings": str(register)},
             "truth": {"changelog": "truth/releases.toml"}}), encoding="utf-8")
        changelog.write_text(
            'title = "t"\nnote = "n"\n[[release]]\ntitle = "r"\nversion = "1.0.0"\n'
            'date = "2026-01-01"\nitems = ["x"]\nrefs = ["decision:GD-110"]\n', encoding="utf-8")

        # a merged pr ref still merge_pending -> FAIL naming the row
        register.write_text("- F-1 :: OPEN :: merge_pending :: refs=pr:17 :: title\n",
                            encoding="utf-8")
        _case("merged-pr-detected", run(repo), 1)
        # a PR merged with >1000 newer first-parent commits is beyond the former -n 1000 cap: the
        # durable proof that the cap is gone (this vector passed clean before the fix).
        for i in range(1000):
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=T",
                            "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                            "commit", "-q", "--allow-empty", "-m", "padding {}".format(i)],
                           check=True, capture_output=True, timeout=30)
        _case("merged-pr-beyond-former-cap-detected", run(repo), 1)
        # a recorded decision ref still merge_pending -> FAIL
        register.write_text("- F-2 :: OPEN :: merge_pending :: refs=decision:GD-110 :: title\n",
                            encoding="utf-8")
        _case("recorded-decision-detected", run(repo), 1)
        # a truly unmerged ref passes
        register.write_text("- F-3 :: OPEN :: merge_pending :: refs=pr:9999 :: title\n",
                            encoding="utf-8")
        _case("unmerged-passes", run(repo), 0)
        # a narrative mention with no structured ref is ignored
        register.write_text("finding 17 is waiting on the merge of 17\n", encoding="utf-8")
        _case("narrative-ignored", run(repo), 0)
        # malformed row, malformed ref, duplicate refs -> exit 2 (fail-closed)
        for bad in ("- F-4 :: OPEN :: merge_pending :: title with no refs\n",
                    "- F-5 :: OPEN :: merge_pending :: refs=seventeen :: t\n",
                    "- F-6 :: OPEN :: merge_pending :: refs=pr:17,pr:17 :: t\n"):
            register.write_text(bad, encoding="utf-8")
            _case("fail-closed {!r}".format(bad[:30]), run(repo), 2)
        # unreadable declared register -> exit 2
        register.unlink()
        _case("declared-but-absent-fails", run(repo), 2)
        # a declared-but-absent changelog -> exit 2 (a declared truth input is never a silent skip)
        register.write_text("- F-7 :: OPEN :: merge_pending :: refs=decision:GD-404 :: t\n",
                            encoding="utf-8")
        changelog.unlink()
        _case("declared-changelog-absent-fails", run(repo), 2)
        # a malformed declared changelog -> exit 2
        changelog.write_text("not toml [[", encoding="utf-8")
        _case("malformed-changelog-fails", run(repo), 2)
        # CONV4-CL3: a version that is not the int 1 (a bool True, or a float 1.0) fails closed; without
        # the type check both would satisfy '!= 1' (True == 1, 1.0 == 1) and slip a malformed registry.
        changelog.write_text(
            'title = "t"\nnote = "n"\n[[release]]\ntitle = "r"\nversion = "1.0.0"\n'
            'date = "2026-01-01"\nitems = ["x"]\nrefs = ["decision:GD-110"]\n', encoding="utf-8")
        register.write_text("- F-8 :: OPEN :: merge_pending :: refs=pr:9999 :: t\n", encoding="utf-8")
        reg_path = repo / ".aiqt" / "orchestration.local.json"
        for bad_ver in (1.0, True):
            reg_path.write_text(json.dumps(
                {"version": bad_ver, "record": {"findings": str(register)},
                 "truth": {"changelog": "truth/releases.toml"}}), encoding="utf-8")
            _case("bad-version-type {!r} fails".format(bad_ver), run(repo), 2)
        # no registry -> NOT APPLICABLE pass; the same answer with and without the .aiqt
        # directory, since each missing component is a confirmed ENOENT (the first-run
        # control for the chain-confirmed presence read below)
        bare = tmp / "bare"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", str(bare)], check=True, capture_output=True,
                       timeout=30)
        _case("no-registry-not-applicable", run(bare), 0)
        (bare / ".aiqt").mkdir()
        _case("no-registry-with-aiqt-not-applicable", run(bare), 0)
        # an UNRESOLVABLE registry path is exit 2, never NOT APPLICABLE and never a blocked
        # read: a dangling symlink at the registry path; a dangling entry on the registry's
        # parent chain; a FIFO registry (refused by the opened descriptor's type, without
        # blocking, by the shared non-blocking reader)
        (bare / ".aiqt" / "orchestration.local.json").symlink_to("missing-registry-target")
        _case("dangling-registry-fails", run(bare), 2)
        (bare / ".aiqt" / "orchestration.local.json").unlink()
        chainroot = tmp / "chainroot"
        chainroot.mkdir()
        (chainroot / ".aiqt").symlink_to("missing-aiqt-target")
        _case("unresolvable-registry-parent-fails", run(chainroot), 2)
        fiforoot = tmp / "fiforoot"
        (fiforoot / ".aiqt").mkdir(parents=True)
        os.mkfifo(str(fiforoot / ".aiqt" / "orchestration.local.json"))
        _case("fifo-registry-fails", run(fiforoot), 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: merged-pr drift is detected across the full first-parent history, the "
          "registry-declared changelog path is honoured, unmerged and narrative rows pass, every "
          "malformed, absent, or unreadable declared input fails closed, and an unresolvable "
          "registry path (a dangling symlink at the registry path or on its parent chain) or a "
          "non-regular registry (a FIFO, refused by type without blocking) is exit 2, never NOT "
          "APPLICABLE, while a registry path confirmed absent down its own chain still reads NOT "
          "APPLICABLE")
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test()
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
