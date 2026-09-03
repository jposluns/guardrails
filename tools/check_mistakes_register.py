#!/usr/bin/env python3
"""Mistakes-register gate (GD-112 component 6, rules mstreg/secaud): the register is APPEND-ONLY with
permanent ids and a digest chain, and a landed row's claims are verified against the repository.

Row schema (one JSON object per line):
  {"seq": N, "id": "MR-N", "ts": "<iso-utc>", "mistake": "...", "evidence": "...", "rule": "...",
   "guardrail": "...", "status": "proposed|accepted|landed|declined|superseded",
   "prev": "<sha256 of the previous raw line, 64*'0' for the first>",
   ...landed rows also: "commit": "<sha>", "check_ref": "<repo-relative path>",
   ...optional: "class": "<row classifier, e.g. systemic-lapse>", "ref": "<the blocker ref an
   attestation row covers>"}
A status change is a NEW row re-naming an earlier id at a higher seq; nothing is ever edited in place.
A registry-declared `attestations` register (GD-127 C.2) is verified by this same gate under the
AT- id prefix; the id prefix is per-register, MR- for the mistakes register.

Append-only enforcement, two modes: a git-TRACKED register is checked as an exact line-prefix of its
merge-base state with origin/HEAD (so the protected-branch reference must be resolvable: on a fresh
clone or CI checkout run `git remote set-head origin -a`); an UNTRACKED register is checked against a
persisted anchor file (<register>.anchor: the last verified seq and line digest), which only
--update-anchor moves forward on a green run (and establishes the first anchor). Fail-closed: a
DECLARED register that is absent or unreadable, a broken chain or sequence, a reused id, an illegal
status transition, or a landed row whose commit or repo-relative check_ref does not exist is a
violation (exit 1); and an append-only AUTHORITY that cannot be read (no computable merge-base, or a
declared untracked register with no anchor) is a distinct cannot-evaluate failure (exit 2), never a
silent skip nor a HEAD-against-itself self-comparison (chkfcl/grdinp). With no registry, or no declared
register path, the live leg is NOT APPLICABLE and the self-test carries the assurance in CI.
  check_mistakes_register.py                   run the live leg
  check_mistakes_register.py --update-anchor   verify, then advance the anchor on green
  check_mistakes_register.py --self-test       synthetic fixtures for every invariant
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

REGISTRY_FILES = (".aiqt/orchestration.local.json", ".aiqt/orchestration.json")
STATUSES = ("proposed", "accepted", "landed", "declined", "superseded")
ZERO = "0" * 64
TRANSITIONS = {"proposed": {"accepted", "declined", "superseded"},
               "accepted": {"landed", "declined", "superseded"},
               "landed": {"superseded"}, "declined": set(), "superseded": set()}


class CannotEvaluate(RuntimeError):
    """The append-only authority itself could not be read (a distinct cannot-evaluate case, exit 2),
    kept separate from a register-content violation (exit 1) so the guard never self-certifies against
    an authority it could not compute (chkfcl/grdinp)."""


def verify_lines(lines, root=None, prefix="MR-"):
    """Return a list of violation strings for the raw register lines (empty = clean). prefix is the
    register's id family (MR- for the mistakes register, AT- for an attestations register)."""
    problems, prev_line, ids_born, last_status = [], None, {}, {}
    for n, line in enumerate(lines, 1):
        if not line.strip():
            problems.append("line {}: blank line inside the register".format(n))
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append("line {}: not a JSON object".format(n))
            continue
        want_prev = ZERO if prev_line is None else hashlib.sha256(
            prev_line.encode("utf-8")).hexdigest()
        if row.get("prev") != want_prev:
            problems.append("line {}: digest chain broken".format(n))
        if row.get("seq") != n:
            problems.append("line {}: seq {} is not the line number".format(n, row.get("seq")))
        rid = row.get("id")
        status = row.get("status")
        if status not in STATUSES:
            problems.append("line {}: unknown status {!r}".format(n, status))
        elif not isinstance(rid, str) or not rid.startswith(prefix):
            problems.append("line {}: id {!r} is not an {} id".format(n, rid, prefix))
        elif rid in ids_born and status == "proposed":
            problems.append("line {}: id {} reused as a new proposal".format(n, rid))
        elif rid in ids_born:
            if status not in TRANSITIONS.get(last_status[rid], set()):
                problems.append("line {}: illegal status transition {} -> {} on {}".format(
                    n, last_status[rid], status, rid))
            last_status[rid] = status
        else:
            if status != "proposed":
                problems.append("line {}: first row for {} must be proposed".format(n, rid))
            ids_born[rid] = n
            last_status[rid] = status
        for key in ("ts", "mistake", "evidence", "rule", "guardrail"):
            if not isinstance(row.get(key), str) or not row.get(key):
                problems.append("line {}: missing field {}".format(n, key))
        if status == "landed" and root is not None:
            commit, check_ref, check_ok = row.get("commit"), row.get("check_ref"), False
            if not isinstance(commit, str) or subprocess.run(
                    ["git", "-C", str(root), "cat-file", "-e", str(commit) + "^{commit}"],
                    capture_output=True, timeout=10).returncode != 0:
                problems.append("line {}: landed row cites a commit that does not exist".format(n))
            # check_ref is a REPO-RELATIVE path (schema); an absolute value would discard root and let
            # any host file satisfy the check, so reject it and confirm containment under root (grdinp).
            if isinstance(check_ref, str) and check_ref and not Path(check_ref).is_absolute():
                try:
                    check_root = Path(root).resolve()
                    check_path = (check_root / check_ref).resolve()
                    check_path.relative_to(check_root)
                    check_ok = check_path.exists()
                except (OSError, ValueError):
                    check_ok = False
            if not check_ok:
                problems.append("line {}: landed row cites check_ref {!r} which is not an existing "
                                "repo-relative path".format(n, check_ref))
        prev_line = line
    return problems


def verify_append_only(root, path, lines, allow_missing_anchor=False):
    """Tracked register: exact full-line prefix of the merge-base state. Untracked: the anchor file.
    Returns a list of violations. Raises CannotEvaluate when the append-only AUTHORITY itself cannot be
    read (tracked: no computable merge-base with origin/HEAD, or its historical file unreadable;
    untracked: a declared register present with no anchor and allow_missing_anchor False), so the guard
    never falls back to comparing a possibly-tampered HEAD against itself. allow_missing_anchor is set
    only by the --update-anchor bootstrap that establishes the first anchor."""
    rel = None
    try:
        rel = str(path.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        pass
    if rel:
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                                 capture_output=True, timeout=10).returncode == 0
        if tracked:
            base = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD",
                                   "origin/HEAD"], capture_output=True, text=True, timeout=10)
            if base.returncode != 0 or not base.stdout.strip():
                raise CannotEvaluate("cannot compute the register authority from origin/HEAD: {}"
                                     .format(base.stderr.strip() or "no merge-base"))
            ref = base.stdout.strip()
            old = subprocess.run(["git", "-C", str(root), "show", "{}:{}".format(ref, rel)],
                                 capture_output=True, text=True, timeout=10)
            if old.returncode != 0:
                raise CannotEvaluate("cannot read the register at its merge-base authority: {}"
                                     .format(old.stderr.strip() or "git show failed"))
            old_lines = old.stdout.splitlines()
            if lines[:len(old_lines)] != old_lines:
                return ["the register is not an append-only extension of its merge-base state"]
            return []
    anchor = Path(str(path) + ".anchor")
    if not anchor.exists() and not allow_missing_anchor:
        raise CannotEvaluate("the declared untracked register has no anchor; run --update-anchor to "
                             "establish the baseline (an absent authority is not a clean result)")
    if anchor.exists():
        try:
            a = json.loads(anchor.read_text(encoding="utf-8"))
            n, digest = int(a["seq"]), str(a["digest"])
        except (OSError, ValueError, KeyError, TypeError):
            return ["the anchor file is unreadable or malformed (fail-closed)"]
        if len(lines) < n:
            return ["the register is shorter than its verified anchor (rows removed)"]
        if n > 0 and hashlib.sha256(lines[n - 1].encode("utf-8")).hexdigest() != digest:
            return ["the anchored row was rewritten (tamper or reorder before seq {})".format(n)]
    return []


def update_anchor(path, lines):
    anchor = Path(str(path) + ".anchor")
    if lines:
        anchor.write_text(json.dumps({"seq": len(lines), "digest": hashlib.sha256(
            lines[-1].encode("utf-8")).hexdigest()}), encoding="utf-8")


def run(root, update=False):
    reg = None
    for rel in REGISTRY_FILES:
        p = Path(root) / rel
        if p.exists():
            try:
                reg = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print("error: cannot read the orchestration registry: {}".format(exc))
                return 2
            break
    surfaces = []
    if isinstance(reg, dict):
        for key, prefix in (("mistakes_register", "MR-"), ("attestations", "AT-")):
            if reg.get(key):
                surfaces.append((key, prefix, reg[key]))
    if not surfaces:
        print("NOT APPLICABLE: no orchestration registry (or no declared mistakes or attestations "
              "register); the self-test carries the assurance")
        return 0
    worst, green = 0, []
    for key, prefix, declared in surfaces:
        path = Path(declared) if os.path.isabs(declared) else Path(root) / declared
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print("error: the DECLARED {} is unreadable ({}); fail-closed".format(key, exc))
            return 2
        line_problems = verify_lines(lines, root=root, prefix=prefix)
        try:
            append_problems = verify_append_only(root, path, lines, allow_missing_anchor=update)
        except CannotEvaluate as exc:
            print("error: cannot evaluate the {}'s append-only history ({}); fail-closed"
                  .format(key, exc))
            return 2
        problems = line_problems + append_problems
        if problems:
            print("FAIL: {} {} violation(s):".format(len(problems), key))
            for p in problems:
                print("  " + p)
            worst = 1
            continue
        green.append((key, path, lines))
    if worst:
        return 1
    for key, path, lines in green:
        if update:
            update_anchor(path, lines)
            print("PASS: {} verified; anchor advanced to seq {}".format(key, len(lines)))
        else:
            print("PASS: the {} is a verified append-only chain ({} row(s))".format(
                key, len(lines)))
    return 0


def self_test():
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-register-"))
    failures = []

    def row(seq, rid, status, prev, extra=None):
        r = {"seq": seq, "id": rid, "ts": "2026-08-29T00:00:00+00:00", "mistake": "m",
             "evidence": "e", "rule": "setcmp", "guardrail": "g", "status": status, "prev": prev}
        r.update(extra or {})
        return json.dumps(r, sort_keys=True)

    def chain(*rows_wo_prev):
        out, prev = [], ZERO
        for build in rows_wo_prev:
            line = build(prev)
            out.append(line)
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
        return out

    def _case(name, lines, want_clean, root=None, prefix="MR-"):
        got = not verify_lines(lines, root=root, prefix=prefix)
        if got != want_clean:
            failures.append("{}: clean={}, want {}".format(name, got, want_clean))

    def _git_ci(repo, *paths, msg):
        subprocess.run(["git", "-C", str(repo), "add", *paths], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c",
                        "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", msg], check=True, capture_output=True, timeout=30)

    try:
        good = chain(lambda p: row(1, "MR-1", "proposed", p),
                     lambda p: row(2, "MR-2", "proposed", p),
                     lambda p: row(3, "MR-1", "accepted", p))
        _case("pure-append-passes", good, True)
        _case("broken-chain-fails", good[:2] + [row(3, "MR-1", "accepted", ZERO)], False)
        _case("reused-id-fails", chain(lambda p: row(1, "MR-1", "proposed", p),
                                       lambda p: row(2, "MR-1", "proposed", p)), False)
        _case("bad-transition-fails", chain(lambda p: row(1, "MR-1", "proposed", p),
                                            lambda p: row(2, "MR-1", "declined", p),
                                            lambda p: row(3, "MR-1", "landed", p)), False)
        _case("bad-seq-fails", chain(lambda p: row(1, "MR-1", "proposed", p),
                                     lambda p: row(9, "MR-2", "proposed", p)), False)
        # edit/delete/reorder of an anchored row fails via the anchor
        reg_dir = tmp / "r"
        reg_dir.mkdir()
        rp = reg_dir / "register.jsonl"
        rp.write_text("\n".join(good) + "\n", encoding="utf-8")
        update_anchor(rp, good)
        tampered = [good[0], good[2], good[1]]
        if not verify_append_only(tmp, rp, tampered):
            failures.append("anchored reorder should fail")
        if verify_append_only(tmp, rp, good + [row(4, "MR-3", "proposed", "x")]):
            failures.append("anchored append should pass the append-only leg")
        # a landed row citing a missing commit or check fails
        repo = tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                       timeout=30)
        check_path = repo / "durable-check.py"
        check_path.write_text("# durable check\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "durable-check.py"], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T",
                        "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", "seed"], check=True, capture_output=True, timeout=30)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True, timeout=30).stdout.strip()
        landed = chain(lambda p: row(1, "MR-1", "proposed", p),
                       lambda p: row(2, "MR-1", "accepted", p),
                       lambda p: row(3, "MR-1", "landed", p,
                                     {"commit": "0" * 40, "check_ref": "tools/nosuch.py"}))
        _case("landed-unverifiable-fails", landed, False, root=repo)
        # P3: an absolute check_ref is invalid even when it names an existing fixture file.
        landed_absolute = chain(
            lambda p: row(1, "MR-2", "proposed", p),
            lambda p: row(2, "MR-2", "accepted", p),
            lambda p: row(3, "MR-2", "landed", p,
                          {"commit": head, "check_ref": str(check_path.resolve())}))
        _case("landed-absolute-check-ref-fails", landed_absolute, False, root=repo)
        # P1: a tracked register cannot be checked against itself when origin/HEAD is absent -> exit 2.
        (repo / ".aiqt").mkdir()
        tracked = repo / "mistakes.jsonl"
        tracked_good = chain(
            lambda p: row(1, "MR-1", "proposed", p),
            lambda p: row(2, "MR-1", "accepted", p),
            lambda p: row(3, "MR-1", "landed", p,
                          {"commit": head, "check_ref": "durable-check.py"}))
        tracked.write_text("\n".join(tracked_good) + "\n", encoding="utf-8")
        registry = repo / ".aiqt" / "orchestration.local.json"
        registry.write_text(json.dumps(
            {"version": 1, "mistakes_register": "mistakes.jsonl"}), encoding="utf-8")
        _git_ci(repo, ".aiqt/orchestration.local.json", "mistakes.jsonl", msg="add register")
        rewritten = chain(lambda p: row(1, "MR-1", "proposed", p),
                          lambda p: row(2, "MR-1", "declined", p))
        tracked.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        _git_ci(repo, "mistakes.jsonl", msg="rewrite register")
        if run(repo) != 2:
            failures.append("tracked rewrite without origin/HEAD should exit 2")
        # P2: a declared UNTRACKED register present with no anchor is cannot-evaluate -> exit 2.
        untracked = repo / "untracked-register.jsonl"
        untracked.write_text("\n".join(tracked_good) + "\n", encoding="utf-8")
        registry.write_text(json.dumps(
            {"version": 1, "mistakes_register": "untracked-register.jsonl"}), encoding="utf-8")
        if run(repo) != 2:
            failures.append("declared untracked register with no anchor should exit 2")
        # bootstrap: --update-anchor establishes the first anchor (exit 0), then the gate passes.
        if run(repo, update=True) != 0:
            failures.append("--update-anchor bootstrap should establish the anchor and pass")
        if run(repo) != 0:
            failures.append("gate should pass once the anchor is established")
        # GD-127 C.2: an attestations register (AT- prefix, optional class/ref fields) is covered by
        # the same machinery; before the change a declared-attestations-only registry was NOT
        # APPLICABLE (exit 0), so these legs discriminate.
        at_good = chain(lambda p: row(1, "AT-1", "proposed", p,
                                      {"ref": "ci", "class": "attestation"}))
        _case("at-prefix-passes-under-at", at_good, True, prefix="AT-")
        _case("at-rows-fail-default-prefix", at_good, False)
        at_reg_path = repo / "attest.jsonl"
        at_reg_path.write_text("\n".join(at_good) + "\n", encoding="utf-8")
        registry.write_text(json.dumps(
            {"version": 1, "attestations": "attest.jsonl"}), encoding="utf-8")
        if run(repo) != 2:
            failures.append("declared untracked attestations register with no anchor should exit 2")
        if run(repo, update=True) != 0:
            failures.append("--update-anchor should bootstrap the attestations anchor and pass")
        if run(repo) != 0:
            failures.append("gate should pass the attestations register once anchored")
        at_wrong = chain(lambda p: row(1, "MR-1", "proposed", p))
        at_reg_path.write_text("\n".join(at_wrong) + "\n", encoding="utf-8")
        if run(repo) != 1:
            failures.append("an MR- row in the attestations register should fail the AT- prefix")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: pure appends pass; a broken chain or sequence, a reused id, an illegal "
          "transition, an anchored rewrite or reorder, an unverifiable or absolute-path landed "
          "check_ref, a tracked rewrite with no computable merge-base, and a declared untracked "
          "register with no anchor all fail; the --update-anchor bootstrap establishes the baseline; "
          "and a declared attestations register is verified under the AT- prefix with the optional "
          "class and ref fields accepted")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    return run(repo_root(), update="--update-anchor" in args)


if __name__ == "__main__":
    sys.exit(main())
