#!/usr/bin/env python3
"""Mistakes-register appender and AEI projection (GD-112 component 6; rules mstreg/slfgrd).

  orch_register.py append --register PATH --id MR-N --mistake T --evidence R --rule CID \
                          --guardrail T [--status proposed] [--commit SHA --check-ref PATH]
                          [--prefix MR] [--klass T] [--ref R]
      Append one chained row (seq and prev computed from the file; a wrong --id fails). --prefix
      parameterizes the id family (MR for the mistakes register, AT for an attestation register,
      GD-127 C.2); --klass writes the optional class field (for example systemic-lapse); --ref
      writes the blocker ref an attestation row covers. Before anything is written, the register as the
      append would leave it on disk (the existing bytes plus the new row) is run through the register
      gate's own parser, check_mistakes_register.verify_lines and verify_append_only (with the
      --update-anchor bootstrap's allowance for an untracked register that has no anchor yet), so an
      illegal transition, an unknown status, or a landed row without an existing --commit and
      --check-ref is refused at write time instead of reaching CI: a violation exits 1, and an
      unreadable register or an append-only authority that cannot be evaluated exits 2, each naming
      the reason on stderr and leaving the register byte-identical (an absent register stays absent).
  orch_register.py project --register PATH
      Emit an AEI v1 enumeration of the rows whose LATEST status is proposed or accepted and not yet
      landed, as granted open items, so the stop guard itself enforces mistake-to-guardrail
      follow-through. A registry can name this as (part of) its enumerator. An attestation register
      is evidence, never backlog: project the mistakes register, not the attestations file.
  orch_register.py --self-test
      Synthetic fixtures for the write-time validation: each refusal vector leaves the register
      byte-identical, and a clean append still lands.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: orch_register.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import datetime
import hashlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from check_mistakes_register import (  # noqa: E402
    CannotEvaluate, verify_append_only, verify_lines)

ZERO = "0" * 64


def _lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def cmd_append(a):
    path = Path(a.register)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, ValueError) as exc:
        print("append: refused, the register cannot be read ({}); nothing written".format(exc),
              file=sys.stderr)
        return 2
    lines = text.splitlines()
    prev = ZERO if not lines else hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
    seq = len(lines) + 1
    want_id = "{}-{}".format(a.prefix, sum(1 for l in lines if '"status": "proposed"' in l) + 1) \
        if a.status == "proposed" else a.id
    if a.status == "proposed" and a.id != want_id:
        sys.exit("append: a new proposal must take the next id {} (ids are permanent, "
                 "never reused)".format(want_id))
    row = {"seq": seq, "id": a.id, "ts": datetime.datetime.now(
        datetime.timezone.utc).isoformat(), "mistake": a.mistake, "evidence": a.evidence,
        "rule": a.rule, "guardrail": a.guardrail, "status": a.status, "prev": prev}
    if a.commit:
        row["commit"] = a.commit
    if a.check_ref:
        row["check_ref"] = a.check_ref
    if a.klass:
        row["class"] = a.klass
    if a.ref:
        row["ref"] = a.ref
    line = json.dumps(row, sort_keys=True)
    # Validate exactly what the append leaves on disk (so a register missing its final newline, which
    # would fuse the new row onto its last line, is refused too), with the register gate's own parser.
    after = (text + line + "\n").splitlines()
    root = repo_root(path.parent)
    problems = verify_lines(after, root=root, prefix=a.prefix + "-")
    try:
        problems += verify_append_only(root, path, after, allow_missing_anchor=True)
    except CannotEvaluate as exc:
        print("append: refused, cannot evaluate the register's append-only history ({}); nothing "
              "written".format(exc), file=sys.stderr)
        return 2
    if problems:
        print("append: refused, the register with this row fails the register gate "
              "({} violation(s)); nothing written:".format(len(problems)), file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print("appended {} seq {}".format(a.id, seq))
    return 0


def cmd_project(a):
    latest = {}
    for line in _lines(Path(a.register)):
        try:
            row = json.loads(line)
        except ValueError:
            sys.exit("project: malformed register line (run check_mistakes_register.py)")
        latest[row.get("id")] = row
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items = [{"id": "{}-guardrail".format(rid), "title": "land the guardrail: {}".format(
        row.get("guardrail", "")), "state": "open", "granted": row["status"] == "accepted"}
        for rid, row in sorted(latest.items())
        if row.get("status") in ("proposed", "accepted")]
    for it in items:  # a proposed row enumerates but does not compel (express authorization)
        if not it["granted"]:
            it["state"] = "proposed"
    print(json.dumps({"version": 1, "generated_at_utc": now,
                      "source": {"locator": a.register, "revision": "", "observed_at_utc": now},
                      "items": items}))
    return 0


def self_test():
    import contextlib
    import io
    import shutil
    import subprocess
    import tempfile
    # Hermetic git fixtures (test-hermeticity): an inherited git environment would redirect the
    # fixture's init/add/commit, and the gate's own git reads, into the CALLER's repository.
    from _git_fixture_env import scrub_git_environment
    from check_mistakes_register import update_anchor
    scrub_git_environment()
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-orch-register-"))
    failures = []

    def append(reg, rid, status="proposed", **extra):
        fields = {"register": str(reg), "id": rid, "mistake": "m", "evidence": "e",
                  "rule": "mstreg", "guardrail": "g", "status": status, "commit": None,
                  "check_ref": None, "prefix": "MR", "klass": None, "ref": None}
        fields.update(extra)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                rc = cmd_append(argparse.Namespace(**fields))
            except SystemExit as exc:  # the next-id refusal exits with its message
                rc = exc.code if isinstance(exc.code, int) else 1
                print(exc.code, file=sys.stderr)
        return rc, err.getvalue()

    def snapshot(reg):
        anchor = Path(str(reg) + ".anchor")
        return (reg.read_bytes() if reg.exists() else None,
                anchor.read_bytes() if anchor.exists() else None)

    def accepted(name, reg, rid, status="proposed", **extra):
        rc, err = append(reg, rid, status, **extra)
        if rc != 0:
            failures.append("{}: a clean append should land (exit {}: {})".format(
                name, rc, err.strip()))

    def refused(name, reg, want_rc, reason, rid, status="proposed", **extra):
        before = snapshot(reg)
        rc, err = append(reg, rid, status, **extra)
        if rc != want_rc or reason not in err:
            failures.append("{}: want exit {} naming {!r}, got exit {}: {}".format(
                name, want_rc, reason, rc, err.strip()))
        if snapshot(reg) != before:
            failures.append("{}: a refused append changed the register or its anchor".format(name))

    def git(repo, *args):
        return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c",
                               "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                               *args], check=True, capture_output=True, text=True, timeout=30)

    try:
        repo = tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                       timeout=30)
        (repo / "check.py").write_text("# durable check\n", encoding="utf-8")
        git(repo, "add", "check.py")
        git(repo, "commit", "-q", "-m", "seed")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        # an untracked register with no anchor yet: the gate's bootstrap allowance applies
        reg = repo / "reg.jsonl"
        accepted("first-proposal", reg, "MR-1")
        accepted("legal-accept", reg, "MR-1", "accepted")
        accepted("second-proposal", reg, "MR-2")
        accepted("legal-decline", reg, "MR-2", "declined")
        refused("illegal-transition-refused", reg, 1, "illegal status transition declined -> "
                "accepted", "MR-2", "accepted")
        refused("mistyped-status-refused", reg, 1, "unknown status 'acepted'", "MR-1", "acepted")
        refused("landed-without-commit-refused", reg, 1,
                "landed row cites a commit that does not exist", "MR-1", "landed",
                check_ref="check.py")
        refused("landed-missing-check-ref-refused", reg, 1, "which is not an existing",
                "MR-1", "landed", commit=head, check_ref="nosuch.py")
        accepted("verified-landed-appends", reg, "MR-1", "landed", commit=head,
                 check_ref="check.py")
        if len(reg.read_text(encoding="utf-8").splitlines()) != 5:
            failures.append("the clean appends should leave exactly five rows")
        # an attestation register validates under its own id family (--prefix AT)
        accepted("attestation-prefix-appends", repo / "attest.jsonl", "AT-1", prefix="AT")
        # a register whose last line lacks its newline would fuse the new row onto it on disk
        fused = repo / "fused.jsonl"
        accepted("fused-seed", fused, "MR-1")
        fused.write_bytes(fused.read_bytes().rstrip(b"\n"))
        refused("missing-final-newline-refused", fused, 1, "not a JSON object", "MR-1",
                "accepted")
        # the append-only leg: an anchored row rewritten in place (seq and chain still valid, so
        # verify_lines alone reads clean) is refused through verify_append_only
        anch = repo / "anchored.jsonl"
        accepted("anchored-seed", anch, "MR-1")
        update_anchor(anch, anch.read_text(encoding="utf-8").splitlines())
        old = json.loads(anch.read_text(encoding="utf-8"))
        old["mistake"] = "rewritten"
        anch.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="utf-8")
        refused("anchored-rewrite-refused", anch, 1, "the anchored row was rewritten", "MR-2")
        # a tracked register with no computable merge-base is cannot-evaluate (exit 2), not a pass
        tracked = repo / "tracked.jsonl"
        accepted("tracked-seed", tracked, "MR-1")
        git(repo, "add", "tracked.jsonl")
        git(repo, "commit", "-q", "-m", "track the register")
        refused("tracked-without-authority-exit-2", tracked, 2, "cannot evaluate", "MR-2")
        # an unreadable (non-UTF-8) register is refused as cannot-evaluate, never read as empty
        garbled = repo / "garbled.jsonl"
        garbled.write_bytes(b"\xff\n")
        refused("unreadable-register-exit-2", garbled, 2, "cannot be read", "MR-1")
        # an absent register refused on its first row stays absent, directory included
        refused("refused-first-row-creates-nothing", repo / "sub" / "new.jsonl", 1,
                "first row for MR-1 must be proposed", "MR-1", "accepted")
        if (repo / "sub").exists():
            failures.append("a refused first row created the register's directory")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        failures.append("a fixture step raised {}: {}".format(type(exc).__name__, exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: an illegal transition, an unknown status, a landed row without an existing "
          "commit or check_ref, a row that would fuse onto an unterminated last line, and an anchored "
          "rewrite are each refused (exit 1), an unreadable register and a tracked register with no "
          "computable merge-base are refused as cannot-evaluate (exit 2), every refusal leaves the "
          "register and its anchor byte-identical, and clean appends still land")
    return 0


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("append")
    for name, req in (("--register", True), ("--id", True), ("--mistake", True),
                      ("--evidence", True), ("--rule", True), ("--guardrail", True),
                      ("--status", False), ("--commit", False), ("--check-ref", False),
                      ("--prefix", False), ("--klass", False), ("--ref", False)):
        p1.add_argument(name, required=req, dest=name.lstrip("-").replace("-", "_"),
                        default={"--status": "proposed", "--prefix": "MR"}.get(name))
    p2 = sub.add_parser("project")
    p2.add_argument("--register", required=True)
    a = ap.parse_args()
    return cmd_append(a) if a.cmd == "append" else cmd_project(a)


if __name__ == "__main__":
    sys.exit(main())
