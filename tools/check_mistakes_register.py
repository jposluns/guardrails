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
violation (exit 1); and an append-only AUTHORITY that cannot be read (no computable merge-base, a git
failure deciding whether the register is tracked, a malformed or unreadable anchor, an anchor
entry that exists but is unusable (a dangling symlink or a non-regular file is never read as a
missing anchor), or a declared untracked register with no anchor) is a distinct cannot-evaluate
failure (exit 2), never a
silent skip nor a HEAD-against-itself self-comparison (chkfcl/grdinp). With no registry, or no declared
register path, the live leg is NOT APPLICABLE and the self-test carries the assurance in CI.
  check_mistakes_register.py                   run the live leg
  check_mistakes_register.py --update-anchor   verify, then advance the anchor on green
  check_mistakes_register.py --self-test       synthetic fixtures for every invariant
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_mistakes_register.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import json
import os
import stat
import subprocess
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


class _DuplicateKey(ValueError):
    """A register row carried the same key twice: json.loads would silently keep the last value, so
    a reader and this gate could disagree on the row (for example two status fields)."""


def _pairs_without_duplicates(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen.add(key)
    return dict(pairs)


def verify_lines(lines, root=None, prefix="MR-"):
    """Return a list of violation strings for the raw register lines (empty = clean). prefix is the
    register's id family (MR- for the mistakes register, AT- for an attestations register)."""
    problems, prev_line, ids_born, last_status = [], None, {}, {}
    for n, line in enumerate(lines, 1):
        if not line.strip():
            problems.append("line {}: blank line inside the register".format(n))
            continue
        try:
            row = json.loads(line, object_pairs_hook=_pairs_without_duplicates)
        except _DuplicateKey as exc:
            problems.append("line {}: duplicated key {!r} in the row (the parser would silently "
                            "keep the last value)".format(n, exc.args[0]))
            continue
        except ValueError:
            problems.append("line {}: not a JSON object".format(n))
            continue
        if not isinstance(row, dict):
            problems.append("line {}: not a JSON object".format(n))
            continue
        want_prev = ZERO if prev_line is None else hashlib.sha256(
            prev_line.encode("utf-8")).hexdigest()
        if row.get("prev") != want_prev:
            problems.append("line {}: digest chain broken".format(n))
        seq = row.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != n:
            problems.append("line {}: seq {} is not the line number".format(n, seq))
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
    read: a git failure deciding whether the register is tracked (any outcome other than git's
    confirmed tracked exit 0 or not-tracked exit 1); tracked: no computable merge-base with
    origin/HEAD, or its historical file unreadable; untracked: a malformed or unreadable anchor, or a
    declared register present with no anchor and allow_missing_anchor False. So the guard never falls
    back to comparing a possibly-tampered HEAD against itself, and a broken git never routes a tracked
    register into the anchor leg. allow_missing_anchor is set only by the --update-anchor bootstrap
    that establishes the first anchor (it covers any unanchored untracked register, not only an empty
    one, because a second append must still land before the first anchor is set)."""
    rel = None
    try:
        rel = str(path.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        pass
    if rel:
        # The tracked/untracked DECISION is itself an authority read: git confirms "not tracked"
        # only with exit 1; any other failure (a corrupt index: exit 128, an unrunnable git, a
        # timeout) leaves the question open and is cannot-evaluate, never a fall-through into the
        # untracked anchor leg and its missing-anchor allowance (chkfcl).
        try:
            probe = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                                   capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CannotEvaluate("cannot decide whether the register is tracked ({})".format(exc))
        if probe.returncode not in (0, 1):
            raise CannotEvaluate("cannot decide whether the register is tracked (git ls-files "
                                 "exited {}: {})".format(
                                     probe.returncode,
                                     probe.stderr.decode("utf-8", "replace").strip()))
        if probe.returncode == 0:
            try:
                base = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD",
                                       "origin/HEAD"], capture_output=True, text=True,
                                      timeout=10)
            except (OSError, subprocess.SubprocessError) as exc:
                raise CannotEvaluate("cannot compute the register authority from origin/HEAD: "
                                     "{}".format(exc))
            if base.returncode != 0 or not base.stdout.strip():
                raise CannotEvaluate("cannot compute the register authority from origin/HEAD: {}"
                                     .format(base.stderr.strip() or "no merge-base"))
            ref = base.stdout.strip()
            try:
                old = subprocess.run(["git", "-C", str(root), "show", "{}:{}".format(ref, rel)],
                                     capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError) as exc:
                raise CannotEvaluate("cannot read the register at its merge-base authority: "
                                     "{}".format(exc))
            if old.returncode != 0:
                raise CannotEvaluate("cannot read the register at its merge-base authority: {}"
                                     .format(old.stderr.strip() or "git show failed"))
            old_lines = old.stdout.splitlines()
            if lines[:len(old_lines)] != old_lines:
                return ["the register is not an append-only extension of its merge-base state"]
            return []
    anchor = Path(str(path) + ".anchor")
    # Anchor PRESENCE is decided with lstat semantics (os.path.lexists): the missing-anchor
    # allowance covers only an anchor path with NO entry at all. An entry that exists but cannot
    # be used (a dangling symlink, a directory, a FIFO or any other non-regular file) is an
    # authority that cannot be read, so it is cannot-evaluate, never the allowance, and the
    # --update-anchor bootstrap never writes through a dangling link.
    if not os.path.lexists(str(anchor)):
        if not allow_missing_anchor:
            raise CannotEvaluate("the declared untracked register has no anchor; run "
                                 "--update-anchor to establish the baseline (an absent authority "
                                 "is not a clean result)")
        return []
    try:
        anchor_stat = os.stat(str(anchor))
    except OSError as exc:
        raise CannotEvaluate("the anchor entry exists but cannot be used ({}); a dangling symlink "
                             "or an uninspectable entry is never the missing-anchor "
                             "allowance".format(exc))
    if not stat.S_ISREG(anchor_stat.st_mode):
        raise CannotEvaluate("the anchor entry exists but is not a regular file; a non-regular "
                             "entry is never the missing-anchor allowance")
    # The anchor is the append-only AUTHORITY: one that cannot be read, or that does not hold
    # the declared shape (an object with an integer seq >= 1, bools excluded, and a 64-hex
    # digest), is cannot-evaluate, never a violation list and never a silently skipped digest
    # comparison (a zero, negative or false seq used to bypass the anchored-row check; a true
    # seq was coerced to 1 and still compared).
    try:
        a = json.loads(anchor.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotEvaluate("the anchor file cannot be read as JSON ({})".format(exc))
    n = a.get("seq") if isinstance(a, dict) else None
    digest = a.get("digest") if isinstance(a, dict) else None
    if (not isinstance(n, int) or isinstance(n, bool) or n < 1
            or not isinstance(digest, str) or len(digest) != 64
            or set(digest) - set("0123456789abcdef")):
        raise CannotEvaluate("the anchor file is malformed (an anchor is an object with an "
                             "integer seq >= 1 and a 64-hex digest)")
    if len(lines) < n:
        return ["the register is shorter than its verified anchor (rows removed)"]
    if hashlib.sha256(lines[n - 1].encode("utf-8")).hexdigest() != digest:
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
    # Hermetic git fixtures (test-hermeticity): every fixture git call below inherits
    # os.environ, where an inherited GIT_INDEX_FILE / GIT_DIR (git exports these to hook
    # children) would redirect the fixture's init/add/commit into the CALLER's repository.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import scrub_git_environment
    scrub_git_environment()
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
        # a non-object line, a bool or float seq, and a duplicated key are named findings,
        # never a crash or a silent pass
        _case("non-object-line-fails", ["17"], False)
        _case("bool-seq-fails", chain(lambda p: row(True, "MR-1", "proposed", p)), False)
        _case("float-seq-fails", chain(lambda p: row(1.0, "MR-1", "proposed", p)), False)
        dup_fields = (("seq", 1), ("id", "MR-1"), ("ts", "t"), ("mistake", "m"),
                      ("evidence", "e"), ("rule", "r"), ("guardrail", "g"),
                      ("status", "landed"), ("status", "proposed"), ("prev", ZERO))
        dup_row = "{" + ", ".join(
            json.dumps(k) + ": " + json.dumps(v) for k, v in dup_fields) + "}"
        _case("duplicate-key-fails", [dup_row], False)
        # edit/delete/reorder of an anchored row fails via the anchor
        reg_dir = tmp / "r"
        reg_dir.mkdir()
        # the anchor leg needs a DECIDABLE tracked question (the fail-closed tracked decision
        # refuses a non-repository root), so the untracked fixture lives in its own repository
        subprocess.run(["git", "init", "-q", str(reg_dir)], check=True,
                       capture_output=True, timeout=30)
        rp = reg_dir / "register.jsonl"
        rp.write_text("\n".join(good) + "\n", encoding="utf-8")
        update_anchor(rp, good)
        tampered = [good[0], good[2], good[1]]
        if not verify_append_only(reg_dir, rp, tampered):
            failures.append("anchored reorder should fail")
        if verify_append_only(reg_dir, rp, good + [row(4, "MR-3", "proposed", "x")]):
            failures.append("anchored append should pass the append-only leg")
        # malformed anchor CONTROLS are cannot-evaluate, never a violation list or a silently
        # bypassed digest comparison
        anchor_path = Path(str(rp) + ".anchor")
        good_anchor = anchor_path.read_text(encoding="utf-8")
        for tag, seq_val, digest_val in (("seq -1", -1, "0" * 64),
                                         ("seq 0", 0, "0" * 64),
                                         ("seq true", True, "0" * 64),
                                         ("float seq", 1.0, "0" * 64),
                                         ("bad digest", 1, "Z" * 64),
                                         ("short hex digest", 1, "a" * 63)):
            anchor_path.write_text(json.dumps({"seq": seq_val, "digest": digest_val}),
                                   encoding="utf-8")
            try:
                verify_append_only(reg_dir, rp, good)
                failures.append("a {} anchor should be cannot-evaluate".format(tag))
            except CannotEvaluate:
                pass
        anchor_path.write_text("not json", encoding="utf-8")
        try:
            verify_append_only(reg_dir, rp, good)
            failures.append("a not-json anchor should be cannot-evaluate")
        except CannotEvaluate:
            pass
        anchor_path.write_text("[1, 2]", encoding="utf-8")
        try:
            verify_append_only(reg_dir, rp, good)
            failures.append("a non-object anchor should be cannot-evaluate")
        except CannotEvaluate as exc:
            if "malformed" not in str(exc):
                failures.append("the non-object anchor refusal should say malformed, got: "
                                "{}".format(exc))
        # anchor presence is lstat-based: an entry that EXISTS at the anchor path but cannot be
        # used (a dangling symlink, a directory) is cannot-evaluate, never the missing-anchor
        # allowance, with allow_missing_anchor True (the --update-anchor bootstrap) included
        anchor_path.unlink()
        anchor_path.symlink_to("missing-anchor-target")
        for allow in (False, True):
            try:
                verify_append_only(reg_dir, rp, good, allow_missing_anchor=allow)
                failures.append("a dangling-symlink anchor should be cannot-evaluate "
                                "(allow_missing_anchor={})".format(allow))
            except CannotEvaluate as exc:
                if "never the missing-anchor allowance" not in str(exc):
                    failures.append("the dangling-symlink anchor refusal should name the "
                                    "allowance, got: {}".format(exc))
        anchor_path.unlink()
        anchor_path.mkdir()
        try:
            verify_append_only(reg_dir, rp, good, allow_missing_anchor=True)
            failures.append("a directory at the anchor path should be cannot-evaluate")
        except CannotEvaluate as exc:
            if "not a regular file" not in str(exc):
                failures.append("the directory-anchor refusal should name the shape, got: "
                                "{}".format(exc))
        anchor_path.rmdir()
        anchor_path.write_text(good_anchor, encoding="utf-8")
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
        # a git failure deciding WHETHER the register is tracked (a corrupt index: exit 128,
        # not git's confirmed not-tracked exit 1) is cannot-evaluate and names that decision,
        # never a fall-through into the untracked anchor leg
        junk_index = tmp / "junk-index"
        junk_index.write_text("broken-index", encoding="utf-8")
        os.environ["GIT_INDEX_FILE"] = str(junk_index)
        try:
            try:
                verify_append_only(repo, tracked, rewritten)
                failures.append("a git failure deciding tracked status should be "
                                "cannot-evaluate")
            except CannotEvaluate as exc:
                if "cannot decide whether the register is tracked" not in str(exc):
                    failures.append("the tracked-decision failure should name itself, got: "
                                    "{}".format(exc))
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
        # each of the three git AUTHORITY reads (ls-files, merge-base, show) fails closed on a
        # launch error or a timeout: cannot-evaluate with the decision named, never an unhandled
        # exception and never a fall-through into the untracked anchor leg
        real_sp_run = subprocess.run

        def _raising_for(target, raised):
            def runner(cmd, *args, **kwargs):
                if isinstance(cmd, list) and target in cmd:
                    raise raised
                return real_sp_run(cmd, *args, **kwargs)
            return runner

        for tag, target, raised, named in (
                ("ls-files launch error", "ls-files",
                 OSError(5, "injected launch failure"),
                 "cannot decide whether the register is tracked"),
                ("ls-files timeout", "ls-files",
                 subprocess.TimeoutExpired(["git"], 10),
                 "cannot decide whether the register is tracked"),
                ("merge-base launch error", "merge-base",
                 OSError(5, "injected launch failure"),
                 "cannot compute the register authority")):
            subprocess.run = _raising_for(target, raised)
            try:
                try:
                    verify_append_only(repo, tracked, rewritten)
                    failures.append("a {} should be cannot-evaluate".format(tag))
                except CannotEvaluate as exc:
                    if named not in str(exc):
                        failures.append("the {} refusal should name its decision, got: "
                                        "{}".format(tag, exc))
            finally:
                subprocess.run = real_sp_run

        def _show_raises(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "merge-base" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="1234abcd\n", stderr="")
            if isinstance(cmd, list) and "show" in cmd:
                raise OSError(5, "injected launch failure")
            return real_sp_run(cmd, *args, **kwargs)

        subprocess.run = _show_raises
        try:
            try:
                verify_append_only(repo, tracked, rewritten)
                failures.append("a git-show launch error should be cannot-evaluate")
            except CannotEvaluate as exc:
                if "cannot read the register at its merge-base authority" not in str(exc):
                    failures.append("the git-show refusal should name its read, got: "
                                    "{}".format(exc))
        finally:
            subprocess.run = real_sp_run
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
        # end to end: a malformed anchor is exit 2 in the gate, then green once restored
        at_anchor_path = Path(str(at_reg_path) + ".anchor")
        saved_anchor = at_anchor_path.read_text(encoding="utf-8")
        at_anchor_path.write_text(json.dumps({"seq": True, "digest": "beef"}),
                                  encoding="utf-8")
        if run(repo) != 2:
            failures.append("a malformed anchor should be cannot-evaluate (exit 2) in the gate")
        at_anchor_path.write_text(saved_anchor, encoding="utf-8")
        if run(repo) != 0:
            failures.append("gate should pass again once the anchor is restored")
        # a dangling-symlink anchor entry is exit 2 even for the --update-anchor bootstrap, and
        # the bootstrap never creates the link's target
        at_anchor_path.unlink()
        at_anchor_path.symlink_to("missing-at-anchor-target")
        if run(repo, update=True) != 2:
            failures.append("a dangling-symlink anchor should be cannot-evaluate (exit 2) even "
                            "with --update-anchor")
        if (repo / "missing-at-anchor-target").exists():
            failures.append("the --update-anchor bootstrap wrote through a dangling anchor "
                            "symlink")
        at_anchor_path.unlink()
        at_anchor_path.write_text(saved_anchor, encoding="utf-8")
        if run(repo) != 0:
            failures.append("gate should pass again once the dangling anchor entry is replaced")
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
          "transition, a non-object line, a bool or float seq, a duplicated key, an anchored "
          "rewrite or reorder, an unverifiable or absolute-path landed check_ref, a tracked "
          "rewrite with no computable merge-base, and a declared untracked register with no "
          "anchor all fail; a malformed, non-object or wrongly shaped anchor (a zero, negative, "
          "boolean or float seq, a short or non-hex digest), an anchor entry that exists but is "
          "not a usable regular file (a dangling symlink or a directory, the --update-anchor "
          "bootstrap included, which never writes through a dangling link), and a git launch "
          "failure, timeout or undecidable exit on any of the three authority reads are "
          "cannot-evaluate (exit 2); the --update-anchor bootstrap "
          "establishes the baseline; and a declared attestations register is verified under the "
          "AT- prefix with the optional class and ref fields accepted")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    return run(repo_root(), update="--update-anchor" in args)


if __name__ == "__main__":
    sys.exit(main())
