#!/usr/bin/env python3
"""Mistakes-register appender and AEI projection (GD-112 component 6; rules mstreg/slfgrd).

  orch_register.py append --register PATH --id MR-N --mistake T --evidence R --rule CID \
                          --guardrail T [--status proposed] [--commit SHA --check-ref PATH]
                          [--prefix MR] [--klass T] [--ref R]
      Append one chained row (seq and prev computed from the file; a wrong --id fails). --prefix
      parameterizes the id family (MR for the mistakes register, AT for an attestation register,
      GD-127 C.2) and must agree with the registry declaration; --klass writes the optional class
      field (for example systemic-lapse); --ref writes the blocker ref an attestation row covers.
      The appender validates in the register gate's own context: the id family comes from the
      registry declaration (.aiqt/orchestration.local.json or .aiqt/orchestration.json) that names
      the register path, and the validation root is the git repository that contains the register,
      never the cwd. Before anything is written, the register text as the append would leave it
      (the existing decoded lines plus the new row; validation runs on the decoded UTF-8 text with
      the gate's own line splitting, not on raw bytes) is run through the register gate's own
      parser, check_mistakes_register.verify_lines and verify_append_only (with the --update-anchor
      bootstrap's allowance for an untracked register that has no anchor yet; the allowance covers
      any unanchored untracked register, not only an empty one), so an append the gate would reject
      is refused at write time instead of reaching CI. A violation (an illegal transition, an
      unknown status, a wrong-family id, a landed row without an existing --commit and --check-ref)
      exits 1; a context or authority the appender cannot evaluate (an unreadable register, a
      symlinked register path, a register outside any git repository, a path no registry
      declaration names, a git failure deciding whether the register is tracked, no computable
      merge-base for a tracked register, a malformed or unreadable anchor) exits 2; each refusal
      names its reasons on stderr and leaves the register byte-identical (an absent register and
      its directory stay absent). Cooperating appenders are serialized by an exclusive flock held
      across the read, the validation and the write, and the write goes through the same descriptor
      the validation read (opened with O_NOFOLLOW, written only while its device and inode still
      match the path); a failed or short write is truncated back to the validated size, so no
      partial row remains. A writer that does not take the lock (a hand edit, any other tool) is
      outside that serialization and is caught later by the mistakes-register gate, not here.
  orch_register.py project --register PATH
      Emit an AEI v1 enumeration of the rows whose LATEST status is proposed or accepted and not yet
      landed, as granted open items, so the stop guard itself enforces mistake-to-guardrail
      follow-through. A registry can name this as (part of) its enumerator. An attestation register
      is evidence, never backlog: project the mistakes register, not the attestations file.
  orch_register.py --self-test
      Synthetic fixtures for the write-time validation: each refusal vector leaves the register and
      its anchor byte-identical, every clean append is re-read and its landed last row and a clean
      verify_lines asserted, and the serialization lock and the write binding are probed in place.
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
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mistakes_register import (  # noqa: E402
    REGISTRY_FILES, CannotEvaluate, verify_append_only, verify_lines)

ZERO = "0" * 64


def _lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _register_root(path):
    """The git repository that contains the register, or None. The register gate validates a
    declared register against the repository that contains it, so the appender resolves that same
    root instead of falling back to the cwd (a cwd fallback made acceptance depend on where the
    appender happened to run, which the gate never does)."""
    parent = path.resolve().parent
    for anc in [parent, *parent.parents]:
        if (anc / ".git").exists():
            return anc
    return None


def _declared_family(root, path):
    """The (registry key, id family) of the declaration that names this register path. Raises
    CannotEvaluate when there is no registry, the registry cannot be read, or no declaration names
    the path: the register gate evaluates declared surfaces only, so an undeclared append has no
    gate context to validate in."""
    registry = None
    for rel in REGISTRY_FILES:
        candidate = Path(root) / rel
        if candidate.exists():
            try:
                registry = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CannotEvaluate("the orchestration registry cannot be read ({})".format(exc))
            break
    if not isinstance(registry, dict):
        raise CannotEvaluate("no orchestration registry declares this register (the gate "
                             "validates declared surfaces only)")
    want = path.resolve()
    for key, family in (("mistakes_register", "MR"), ("attestations", "AT")):
        declared = registry.get(key)
        if isinstance(declared, str) and declared:
            declared_path = Path(declared) if os.path.isabs(declared) else Path(root) / declared
            if declared_path.resolve() == want:
                return key, family
    raise CannotEvaluate("no registry declaration names this register path (the gate validates "
                         "the declared mistakes_register and attestations surfaces only)")


def _rolled_back(fd, size):
    """Truncate the register back to its validated size after a failed or short write, under the
    still-held lock, so no partial row remains; returns the sentence for the refusal message."""
    try:
        os.ftruncate(fd, size)
    except OSError as exc:
        return ("the register could NOT be truncated back to its validated {} bytes ({}); run "
                "tools/check_mistakes_register.py before trusting this file".format(size, exc))
    return ("the register was truncated back to its validated {} bytes, so no partial row "
            "remains".format(size))


def cmd_append(a):
    path = Path(a.register)
    root = _register_root(path)
    if root is None:
        print("append: refused, the register lies outside any git repository, so the gate's "
              "validation context cannot be established (the gate validates a declared register "
              "against the repository that contains it, never the cwd); nothing written",
              file=sys.stderr)
        return 2
    try:
        key, family = _declared_family(root, path)
    except CannotEvaluate as exc:
        print("append: refused, cannot establish the register gate's context ({}); nothing "
              "written".format(exc), file=sys.stderr)
        return 2
    if a.prefix != family:
        print("append: refused, --prefix {} disagrees with the registry declaration ({} names "
              "this register, id family {}-); nothing written".format(a.prefix, key, family),
              file=sys.stderr)
        return 1
    fd = -1
    try:
        try:
            fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW)
        except FileNotFoundError:
            fd = -1
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                print("append: refused, the register path is a symlink; the append binds the "
                      "validated text to one file identity, so an aliased path is never "
                      "followed; nothing written", file=sys.stderr)
                return 2
            print("append: refused, the register cannot be read ({}); nothing written".format(
                exc), file=sys.stderr)
            return 2
        if fd >= 0:
            # Serialize cooperating appenders: the exclusive lock is held from before the read
            # until the append lands (or the refusal returns), so two appends through this tool
            # cannot interleave between the read, the validation and the write.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                print("append: refused, cannot lock the register ({}); nothing written".format(
                    exc), file=sys.stderr)
                return 2
            raw = bytearray()
            while True:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                raw += chunk
            try:
                text = bytes(raw).decode("utf-8")
            except ValueError as exc:
                print("append: refused, the register cannot be read ({}); nothing "
                      "written".format(exc), file=sys.stderr)
                return 2
        else:
            text = ""
        lines = text.splitlines()
        prev = ZERO if not lines else hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
        seq = len(lines) + 1
        want_id = "{}-{}".format(a.prefix, sum(
            1 for l in lines if '"status": "proposed"' in l) + 1) \
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
        # Validate the register text as this append leaves it (the decoded lines plus the new
        # row, the same decoded-text parse the register gate runs, so a register missing its
        # final newline, which would fuse the new row onto its last line, is refused too) with
        # the gate's own functions, in the gate's own context: the declared id family and the
        # containing repository root resolved above.
        after = (text + line + "\n").splitlines()
        problems = verify_lines(after, root=root, prefix=family + "-")
        try:
            problems += verify_append_only(root, path, after, allow_missing_anchor=True)
        except CannotEvaluate as exc:
            print("append: refused, cannot evaluate the register's append-only history ({}); "
                  "nothing written".format(exc), file=sys.stderr)
            return 2
        if problems:
            print("append: refused, the register with this row fails the register gate "
                  "({} violation(s)); nothing written:".format(len(problems)), file=sys.stderr)
            for p in problems:
                print("  " + p, file=sys.stderr)
            return 1
        data = (line + "\n").encode("utf-8")
        if fd < 0:
            # First row: the register was absent at the read, so create it exclusively; a file
            # that appeared since is never appended to blindly (O_EXCL refuses it, symlinks
            # included), and a refused row never reaches this point, so the directory of a
            # refused first row stays absent.
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path),
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o644)
            except OSError as exc:
                print("append: refused, the register could not be created exclusively ({}); a "
                      "file that appeared between the validation and the write is never "
                      "appended to blindly; nothing written by this call".format(exc),
                      file=sys.stderr)
                return 2
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                print("append: refused, cannot lock the register ({}); nothing written".format(
                    exc), file=sys.stderr)
                return 2
        else:
            # Bind the write to the file the validation read: if the path no longer names that
            # file, refuse rather than write through either name.
            st_fd = os.fstat(fd)
            try:
                st_path = os.lstat(str(path))
            except OSError:
                st_path = None
            if st_path is None or (st_fd.st_dev, st_fd.st_ino) != \
                    (st_path.st_dev, st_path.st_ino):
                print("append: refused, the register at this path is no longer the file that "
                      "was validated (replaced between validation and write); nothing written "
                      "to either file", file=sys.stderr)
                return 2
        size = os.fstat(fd).st_size
        try:
            written = os.write(fd, data)
        except OSError as exc:
            print("append: the write failed ({}); {}".format(exc, _rolled_back(fd, size)),
                  file=sys.stderr)
            return 2
        if written != len(data):
            print("append: short write ({} of {} bytes); {}".format(
                written, len(data), _rolled_back(fd, size)), file=sys.stderr)
            return 2
        print("appended {} seq {}".format(a.id, seq))
        return 0
    finally:
        if fd >= 0:
            os.close(fd)


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
    import resource
    import shutil
    import signal
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
        before = _lines(reg)
        rc, err = append(reg, rid, status, **extra)
        if rc != 0:
            failures.append("{}: a clean append should land (exit {}: {})".format(
                name, rc, err.strip()))
            return
        after = _lines(reg)
        if len(after) != len(before) + 1 or after[:len(before)] != before:
            failures.append("{}: the append should add exactly one row ({} -> {} line(s))".format(
                name, len(before), len(after)))
            return
        prefix = extra.get("prefix", "MR")
        want = {"seq": len(before) + 1, "id": rid, "status": status, "mistake": "m",
                "evidence": "e", "rule": "mstreg", "guardrail": "g",
                "prev": ZERO if not before else hashlib.sha256(
                    before[-1].encode("utf-8")).hexdigest()}
        for field, as_key in (("commit", "commit"), ("check_ref", "check_ref"),
                              ("klass", "class"), ("ref", "ref")):
            if extra.get(field):
                want[as_key] = extra[field]
        row = json.loads(after[-1])
        got = {k: v for k, v in row.items() if k != "ts"}
        if got != want or not isinstance(row.get("ts"), str) or not row["ts"]:
            failures.append("{}: the landed last row {!r} is not the appended row {!r}".format(
                name, got, want))
        problems = verify_lines(after, root=_register_root(reg), prefix=prefix + "-")
        if problems:
            failures.append("{}: the register after the append fails verify_lines: {}".format(
                name, problems))

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
        (repo / ".aiqt").mkdir()
        registry = repo / ".aiqt" / "orchestration.local.json"

        def declare(**surfaces):
            registry.write_text(json.dumps({"version": 1, **surfaces}), encoding="utf-8")

        # an untracked register with no anchor yet: the gate's bootstrap allowance applies
        declare(mistakes_register="reg.jsonl")
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
        # a failed or short write must not leave a partial row: under RLIMIT_FSIZE the kernel
        # cuts the append short, and the appender must truncate back to the validated size
        limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (reg.stat().st_size + 20, limit[1]))
            refused("failed-write-leaves-no-partial-row", reg, 2, "truncated back", "MR-3")
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, limit)
            signal.signal(signal.SIGXFSZ, handler)
        accepted("append-after-rollback-lands", reg, "MR-3")
        # the serialization lock is held across the validation: a second flock taken during
        # verify_append_only must be refused while the append holds the register
        lockp = repo / "lockprobe.jsonl"
        declare(mistakes_register="lockprobe.jsonl")
        accepted("lock-seed", lockp, "MR-1")
        seen = {}
        real_vao = verify_append_only

        def probing(vroot, vpath, vlines, allow_missing_anchor=False):
            probe_fd = os.open(str(lockp), os.O_RDONLY)
            try:
                try:
                    fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(probe_fd, fcntl.LOCK_UN)
                    seen["held"] = False
                except OSError:
                    seen["held"] = True
            finally:
                os.close(probe_fd)
            return real_vao(vroot, vpath, vlines, allow_missing_anchor=allow_missing_anchor)

        globals()["verify_append_only"] = probing
        try:
            accepted("lock-held-append-lands", lockp, "MR-1", "accepted")
        finally:
            globals()["verify_append_only"] = real_vao
        if seen.get("held") is not True:
            failures.append("the append lock must be held across the validation (a concurrent "
                            "flock during verify_append_only must be refused)")
        # the write is bound to the validated file: a register replaced between the validation
        # and the write is refused, and neither the old nor the new file receives the row
        swap = repo / "swap.jsonl"
        declare(mistakes_register="swap.jsonl")
        accepted("swap-seed", swap, "MR-1")
        swap_seed = swap.read_text(encoding="utf-8")

        def swapping(vroot, vpath, vlines, allow_missing_anchor=False):
            out = real_vao(vroot, vpath, vlines, allow_missing_anchor=allow_missing_anchor)
            decoy = repo / "swap-decoy.jsonl"
            decoy.write_text(swap_seed, encoding="utf-8")
            os.replace(str(decoy), str(swap))
            return out

        globals()["verify_append_only"] = swapping
        try:
            rc, err = append(swap, "MR-1", "accepted")
        finally:
            globals()["verify_append_only"] = real_vao
        if rc != 2 or "replaced between validation and write" not in err:
            failures.append("replaced-register-refused: want exit 2 naming the replacement, got "
                            "exit {}: {}".format(rc, err.strip()))
        if swap.read_text(encoding="utf-8") != swap_seed:
            failures.append("replaced-register-refused: the replacement file received the row")
        # an attestation register validates under its own id family, which the registry declares
        declare(mistakes_register="reg.jsonl", attestations="attest.jsonl")
        accepted("attestation-prefix-appends", repo / "attest.jsonl", "AT-1", prefix="AT")
        # a wrong-family id is refused in both directions, under the declared family
        refused("mr-id-into-attestations-refused", repo / "attest.jsonl", 1,
                "is not an AT- id", "MR-1", "accepted", prefix="AT")
        refused("at-id-into-mistakes-refused", reg, 1, "is not an MR- id", "AT-1", "accepted")
        # a --prefix that disagrees with the registry declaration is refused before any read
        refused("prefix-disagrees-with-declaration", repo / "attest.jsonl", 1,
                "disagrees with the registry declaration", "MR-2")
        # a path no declaration names, and a root with no registry, are cannot-evaluate
        refused("undeclared-register-refused", repo / "undeclared.jsonl", 2,
                "no registry declaration names", "MR-1")
        bare = tmp / "bare"
        bare.mkdir()
        subprocess.run(["git", "init", "-q", str(bare)], check=True, capture_output=True,
                       timeout=30)
        refused("no-registry-refused", bare / "reg.jsonl", 2, "no orchestration registry",
                "MR-1")
        # a register outside any git repository has no gate context (never a cwd fallback)
        outside = tmp / "outside"
        outside.mkdir()
        refused("outside-any-repo-refused", outside / "reg.jsonl", 2,
                "outside any git repository", "MR-1")
        # a register whose last line lacks its newline would fuse the new row onto it on disk
        fused = repo / "fused.jsonl"
        declare(mistakes_register="fused.jsonl")
        accepted("fused-seed", fused, "MR-1")
        fused.write_bytes(fused.read_bytes().rstrip(b"\n"))
        refused("missing-final-newline-refused", fused, 1, "not a JSON object", "MR-1",
                "accepted")
        # the append-only leg: an anchored row rewritten in place (seq and chain still valid, so
        # verify_lines alone reads clean) is refused through verify_append_only
        anch = repo / "anchored.jsonl"
        declare(mistakes_register="anchored.jsonl")
        accepted("anchored-seed", anch, "MR-1")
        update_anchor(anch, anch.read_text(encoding="utf-8").splitlines())
        old = json.loads(anch.read_text(encoding="utf-8"))
        old["mistake"] = "rewritten"
        anch.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="utf-8")
        refused("anchored-rewrite-refused", anch, 1, "the anchored row was rewritten", "MR-2")
        # a malformed or unreadable anchor is cannot-evaluate (exit 2), never a bypassed digest
        anchbad = repo / "anchbad.jsonl"
        declare(mistakes_register="anchbad.jsonl")
        accepted("anchor-cases-seed", anchbad, "MR-1")
        for tag, body, reason in (
                ("seq-negative", json.dumps({"seq": -1, "digest": "0" * 64}),
                 "the anchor file is malformed"),
                ("seq-bool", json.dumps({"seq": True, "digest": "0" * 64}),
                 "the anchor file is malformed"),
                ("digest-not-hex", json.dumps({"seq": 1, "digest": "Z" * 64}),
                 "the anchor file is malformed"),
                ("not-json", "not json", "cannot be read as JSON")):
            Path(str(anchbad) + ".anchor").write_text(body, encoding="utf-8")
            refused("anchor-{}-exit-2".format(tag), anchbad, 2, reason, "MR-2")
        Path(str(anchbad) + ".anchor").unlink()
        # a symlinked register path is refused: the append binds to one file identity
        target = repo / "target.jsonl"
        declare(mistakes_register="target.jsonl")
        accepted("symlink-target-seed", target, "MR-1")
        link = repo / "link.jsonl"
        link.symlink_to("target.jsonl")
        declare(mistakes_register="link.jsonl")
        refused("symlinked-register-refused", link, 2, "symlink", "MR-2")
        # a tracked register with no computable merge-base is cannot-evaluate (exit 2), not a pass
        tracked = repo / "tracked.jsonl"
        declare(mistakes_register="tracked.jsonl")
        accepted("tracked-seed", tracked, "MR-1")
        git(repo, "add", "tracked.jsonl")
        git(repo, "commit", "-q", "-m", "track the register")
        refused("tracked-without-authority-exit-2", tracked, 2, "cannot evaluate", "MR-2")
        # a git failure deciding WHETHER the register is tracked (a corrupt index: exit 128, not
        # git's confirmed "not tracked" exit 1) is cannot-evaluate, never the missing-anchor
        # allowance; the register, its anchor and the directory stay byte-identical
        junk_index = tmp / "junk-index"
        junk_index.write_text("broken-index", encoding="utf-8")
        entries = sorted(entry.name for entry in repo.iterdir())
        os.environ["GIT_INDEX_FILE"] = str(junk_index)
        try:
            refused("git-failure-is-cannot-evaluate", tracked, 2,
                    "cannot decide whether the register is tracked", "MR-2")
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
        if sorted(entry.name for entry in repo.iterdir()) != entries:
            failures.append("a git-failure refusal changed the register directory")
        # an unreadable (non-UTF-8) register is refused as cannot-evaluate, never read as empty
        garbled = repo / "garbled.jsonl"
        declare(mistakes_register="garbled.jsonl")
        garbled.write_bytes(b"\xff\n")
        refused("unreadable-register-exit-2", garbled, 2, "cannot be read", "MR-1")
        # an absent register refused on its first row stays absent, directory included
        declare(mistakes_register="sub/new.jsonl")
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
    print("SELF-TEST PASS: an illegal transition, an unknown status, a landed row without an "
          "existing commit or check_ref, a row that would fuse onto an unterminated last line, an "
          "anchored rewrite, a wrong-family id in either direction, and a --prefix that disagrees "
          "with the registry declaration are each refused (exit 1); an unreadable register, a "
          "symlinked register path, an undeclared or out-of-repository register, a malformed or "
          "unreadable anchor, a git failure deciding whether the register is tracked, a tracked "
          "register with no computable merge-base, a register replaced between validation and "
          "write, and a failed write (rolled back to the validated size) are refused as "
          "cannot-evaluate (exit 2); every refusal leaves the register and its anchor "
          "byte-identical; the lock is held across the validation; and every clean append is "
          "re-read with its last row and verify_lines checked")
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
