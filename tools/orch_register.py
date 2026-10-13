#!/usr/bin/env python3
"""Mistakes-register appender and AEI projection (GD-112 component 6; rules mstreg/slfgrd).

  orch_register.py append --register PATH --id MR-N --mistake T --evidence R --rule CID \
                          --guardrail T [--status proposed] [--commit SHA --check-ref PATH]
                          [--prefix MR] [--klass T] [--ref R]
      Append one chained row (seq and prev computed from the file; a wrong --id fails). --prefix
      parameterizes the id family (MR for the mistakes register, AT for an attestation register,
      GD-127 C.2) and must agree with the registry declaration; --klass writes the optional class
      field (for example systemic-lapse); --ref writes the blocker ref an attestation row covers.
      The appender validates in the register gate's own context: the validation root is the
      gate's own root (tools/_gen_common.repo_root resolved from the gate's location, exactly as
      check_mistakes_register.main resolves it), never the repository that merely contains the
      register and never a root derived from where this invocation runs; the id family comes from
      that root's registry declaration (.aiqt/orchestration.local.json or
      .aiqt/orchestration.json) that names the register path, and two declarations resolving to
      the same register path are refused as ambiguous. Before anything is written, the register
      text as the append would leave it
      (the existing decoded lines plus the new row; validation runs on the decoded UTF-8 text with
      the gate's own line splitting, not on raw bytes) is run through the register gate's own
      parser, check_mistakes_register.verify_lines and verify_append_only (with the --update-anchor
      bootstrap's allowance for an untracked register that has no anchor yet; the allowance covers
      any unanchored untracked register, not only an empty one), so an append the gate would reject
      is refused at write time instead of reaching CI. A violation (an illegal transition, an
      unknown status, a wrong-family id, a landed row without an existing --commit and --check-ref)
      exits 1; a context or authority the appender cannot evaluate (an unreadable register or
      register descriptor, a symlinked register path, a gate root that is not a git repository, a
      path no registry declaration names, conflicting declarations resolving to one path, a git
      failure deciding whether the register is tracked, no computable merge-base for a tracked
      register, a malformed or unusable anchor entry, a dangling symlink included) exits 2; each
      refusal names its reasons on stderr and leaves every row other appenders wrote
      byte-identical (a refusal after the exclusive first-row creation removes the file and the
      directory components this call itself created, but only while that file is still this
      call's own empty file; anything else is left in place and the refusal says so).
      Cooperating appenders are serialized by an exclusive flock held across the read, the
      validation and the write of an existing register; a first row is created exclusively
      (O_EXCL) and then re-checked under the acquired lock (the path still names the created
      file and its size is still zero), so an appender that lands between the creation and the
      lock forces a refusal, never an interleaved row. The write goes through the same
      descriptor the validation read (opened with O_NOFOLLOW, written only while its device and
      inode still match the path and the on-disk size still equals the validated text); a failed
      or short write is truncated back to the validated size, so no partial row remains, with one
      disclosed RESIDUAL: when that truncation itself fails, the partial bytes remain and the
      refusal names the un-truncated state and directs to the register gate. A writer that does
      not take the lock (a hand edit, any other tool) is outside that serialization: the size
      re-check catches one that lands between the validation and the write, any other is caught
      later by the mistakes-register gate, not here.
  orch_register.py project --register PATH
      Emit an AEI v1 enumeration of the rows whose LATEST status is proposed or accepted and not yet
      landed, as granted open items, so the stop guard itself enforces mistake-to-guardrail
      follow-through. A registry can name this as (part of) its enumerator. An attestation register
      is evidence, never backlog: project the mistakes register, not the attestations file.
  orch_register.py --self-test
      Synthetic fixtures for the write-time validation: each refusal vector leaves the register
      and its anchor byte-identical (the disclosed failed-rollback residual vector excepted,
      which asserts the named partial bytes instead), every clean append is re-read and its
      landed last row and a clean verify_lines asserted, and the serialization lock, the
      first-row creation re-check and the write binding are probed in place.
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
from _gen_common import repo_root  # noqa: E402
from check_mistakes_register import (  # noqa: E402
    REGISTRY_FILES, CannotEvaluate, verify_append_only, verify_lines)

ZERO = "0" * 64


def _lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _gate_root():
    """The register gate's own validation root: tools/_gen_common.repo_root resolved from the
    gate's location (the repository that contains the installed gate and this appender), exactly
    as check_mistakes_register.main resolves it. The gate validates a DECLARED register against
    THIS root, not against the repository that happens to contain the register (a register inside
    a nested repository that a declaring outer root names is validated in the outer root's
    context), so the appender resolves the same root; its previous register-based resolution
    could disagree with the gate on nested repositories, and a root derived from the cwd is never
    used while the gate script sits inside a repository."""
    return repo_root()


def _declared_family(root, path):
    """The (registry key, id family) of the declaration that names this register path. Raises
    CannotEvaluate when there is no registry, the registry cannot be read, no declaration names
    the path, or two declarations resolve to the same register path (the declared id family is
    then ambiguous, so there is no single gate context to validate in): the register gate
    evaluates declared surfaces only."""
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
    matched = None
    resolved = []
    for key, family in (("mistakes_register", "MR"), ("attestations", "AT")):
        declared = registry.get(key)
        if isinstance(declared, str) and declared:
            declared_path = Path(declared) if os.path.isabs(declared) else Path(root) / declared
            target = declared_path.resolve()
            resolved.append(target)
            if matched is None and target == want:
                matched = (key, family)
    if len(set(resolved)) < len(resolved):
        raise CannotEvaluate("conflicting registry declarations resolve to the same register "
                             "path, so the declared id family is ambiguous (fix the registry "
                             "before appending)")
    if matched is not None:
        return matched
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


def _creation_cleanup(fd, path, made_dirs):
    """After a refusal that follows the exclusive first-row creation: remove the created file,
    but only while it is still this call's own, still-empty file (the path still names the
    created device and inode, size zero), then the directory components this call itself created
    (innermost first, each only while empty). Returns the sentence for the refusal message; an
    entry this helper cannot prove safe to remove is left in place and named, and the register
    gate stays the authority over whatever remains."""
    try:
        st_fd = os.fstat(fd)
        st_path = os.lstat(str(path))
        if (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
            return "the created register was left in place (the path no longer names it)"
        if st_fd.st_size != 0:
            return "the created register was left in place (it is no longer empty)"
        os.unlink(str(path))
    except OSError as exc:
        return "the created register could not be removed ({})".format(exc)
    for made in made_dirs:
        try:
            os.rmdir(str(made))
        except OSError as exc:
            return ("the created register was removed; its directory was not fully removed "
                    "({})".format(exc))
    return "the created register and the directory components this call made were removed"


def cmd_append(a):
    path = Path(a.register)
    root = _gate_root()
    if not (Path(root) / ".git").exists():
        print("append: refused, the gate's validation root is not a git repository, so the "
              "register gate's context cannot be established (the appender resolves the same "
              "root the gate's live leg validates, from the gate's own location); nothing "
              "written", file=sys.stderr)
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
    created = False
    made_dirs = []
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
            try:
                while True:
                    chunk = os.read(fd, 1 << 16)
                    if not chunk:
                        break
                    raw += chunk
            except OSError as exc:
                print("append: refused, the register cannot be read from its open descriptor "
                      "({}); nothing written".format(exc), file=sys.stderr)
                return 2
            try:
                text = bytes(raw).decode("utf-8")
            except ValueError as exc:
                print("append: refused, the register cannot be read ({}); nothing "
                      "written".format(exc), file=sys.stderr)
                return 2
        else:
            raw = bytearray()
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
            # included). A row refused by the validation never reaches this point, so its
            # directory stays absent; the directory components this call itself creates are
            # recorded (innermost first) so a refusal past this point can remove them again.
            anc = path.parent
            while not anc.exists() and anc != anc.parent:
                made_dirs.append(anc)
                anc = anc.parent
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(str(path),
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o644)
            except OSError as exc:
                for made in made_dirs:  # best-effort: only empty components this call made
                    try:
                        os.rmdir(str(made))
                    except OSError:
                        break
                print("append: refused, the register could not be created exclusively ({}); a "
                      "file that appeared between the validation and the write is never "
                      "appended to blindly; nothing written by this call".format(exc),
                      file=sys.stderr)
                return 2
            created = True
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                print("append: refused, cannot lock the register ({}); nothing written; "
                      "{}".format(exc, _creation_cleanup(fd, path, made_dirs)), file=sys.stderr)
                return 2
        # Bind the write to the file the validation read (or, for a first row, to the file this
        # call just created, re-checked UNDER the acquired lock): if the path no longer names
        # that file, refuse rather than write through either name.
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
        # The on-disk size must still be the validated length (for a first row: still zero).
        # This is the serialization re-check for a freshly created register, where the lock is
        # necessarily taken only after the creation: a cooperating appender that landed between
        # the creation and this lock forces a refusal here, never an interleaved row. For an
        # existing register it narrows the non-cooperating-writer window to after this check.
        size = st_fd.st_size
        if size != len(raw):
            msg = ("append: refused, the register on disk is {} byte(s) but the validated text "
                   "is {} byte(s) (a writer changed the file between the validation and the "
                   "write; for a first row, between its exclusive creation and the lock); "
                   "nothing further written".format(size, len(raw)))
            if created:
                msg += "; " + _creation_cleanup(fd, path, made_dirs)
            print(msg, file=sys.stderr)
            return 2
        try:
            written = os.write(fd, data)
        except OSError as exc:
            msg = "append: the write failed ({}); {}".format(exc, _rolled_back(fd, size))
            if created:
                msg += "; " + _creation_cleanup(fd, path, made_dirs)
            print(msg, file=sys.stderr)
            return 2
        if written != len(data):
            msg = "append: short write ({} of {} bytes); {}".format(
                written, len(data), _rolled_back(fd, size))
            if created:
                msg += "; " + _creation_cleanup(fd, path, made_dirs)
            print(msg, file=sys.stderr)
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
    # The appender resolves the register gate's own root (production: repo_root(), the same
    # function the gate's main() calls); each fixture stands in its own repository, so the
    # resolution is routed at the fixture root under test and restored in the finally.
    gate_root = {"root": None}
    real_gate_root = _gate_root
    globals()["_gate_root"] = lambda: gate_root["root"]

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
        def state(p):
            sp = str(p)
            if os.path.islink(sp):
                return ("symlink", os.readlink(sp))
            if os.path.isdir(sp):
                return ("dir", tuple(sorted(os.listdir(sp))))
            return Path(sp).read_bytes() if os.path.lexists(sp) else None
        return (state(reg), state(Path(str(reg) + ".anchor")))

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
        problems = verify_lines(after, root=gate_root["root"], prefix=prefix + "-")
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
        gate_root["root"] = repo

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
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                        raise  # an unrelated probe error is not lock contention
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

        # hold a handle on the inode the append validates, so the DISPLACED file can be proven
        # row-free after the refusal (the replacement check alone reads only the decoy)
        displaced_fd = os.open(str(swap), os.O_RDONLY)
        try:
            globals()["verify_append_only"] = swapping
            try:
                rc, err = append(swap, "MR-1", "accepted")
            finally:
                globals()["verify_append_only"] = real_vao
            displaced = os.pread(displaced_fd, 1 << 16, 0)
        finally:
            os.close(displaced_fd)
        if rc != 2 or "replaced between validation and write" not in err:
            failures.append("replaced-register-refused: want exit 2 naming the replacement, got "
                            "exit {}: {}".format(rc, err.strip()))
        if swap.read_text(encoding="utf-8") != swap_seed:
            failures.append("replaced-register-refused: the replacement file received the row")
        if displaced != swap_seed.encode("utf-8"):
            failures.append("replaced-register-refused: the displaced file received the row")
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
        gate_root["root"] = bare
        refused("no-registry-refused", bare / "reg.jsonl", 2, "no orchestration registry",
                "MR-1")
        gate_root["root"] = repo
        # a gate root that is not a git repository offers no gate context to validate in
        outside = tmp / "outside"
        outside.mkdir()
        gate_root["root"] = outside
        refused("gate-root-not-a-repo-refused", outside / "reg.jsonl", 2,
                "validation root is not a git repository", "MR-1")
        gate_root["root"] = repo
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
                ("digest-short-hex", json.dumps({"seq": 1, "digest": "a" * 63}),
                 "the anchor file is malformed"),
                ("seq-zero", json.dumps({"seq": 0, "digest": "0" * 64}),
                 "the anchor file is malformed"),
                ("seq-float", json.dumps({"seq": 1.0, "digest": "0" * 64}),
                 "the anchor file is malformed"),
                ("non-object", json.dumps([1, 2]),
                 "the anchor file is malformed"),
                ("not-json", "not json", "cannot be read as JSON")):
            Path(str(anchbad) + ".anchor").write_text(body, encoding="utf-8")
            refused("anchor-{}-exit-2".format(tag), anchbad, 2, reason, "MR-2")
        # an anchor ENTRY that exists but cannot be used is cannot-evaluate, never the
        # missing-anchor bootstrap allowance: a dangling symlink, then a directory
        anchbad_anchor = Path(str(anchbad) + ".anchor")
        anchbad_anchor.unlink()
        anchbad_anchor.symlink_to("missing-anchor-target")
        refused("anchor-dangling-symlink-exit-2", anchbad, 2,
                "never the missing-anchor allowance", "MR-2")
        anchbad_anchor.unlink()
        anchbad_anchor.mkdir()
        refused("anchor-directory-exit-2", anchbad, 2, "not a regular file", "MR-2")
        anchbad_anchor.rmdir()
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
        def dir_state(d):
            return {e.name: (e.read_bytes() if e.is_file() and not e.is_symlink() else None)
                    for e in d.iterdir()}

        entries = dir_state(repo)
        os.environ["GIT_INDEX_FILE"] = str(junk_index)
        try:
            refused("git-failure-is-cannot-evaluate", tracked, 2,
                    "cannot decide whether the register is tracked", "MR-2")
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
        if dir_state(repo) != entries:
            failures.append("a git-failure refusal changed the register directory (an entry "
                            "name or its bytes)")
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
        # the validation root is the GATE's root, never the repository that merely contains
        # the register: a register inside a nested repository, declared by the outer root's
        # registry, validates in the outer root's context (the earlier register-based
        # resolution consulted the nested repository, found no registry there, and refused)
        nested = repo / "nestedrepo"
        nested.mkdir()
        subprocess.run(["git", "init", "-q", str(nested)], check=True, capture_output=True,
                       timeout=30)
        declare(mistakes_register="nestedrepo/nested-reg.jsonl")
        accepted("nested-repo-validates-in-gate-root", nested / "nested-reg.jsonl", "MR-1")
        # two declarations resolving to one path are an ambiguous id family: cannot-evaluate,
        # even when the register exists and the row itself would be legal under one family
        declare(mistakes_register="shared.jsonl", attestations="shared.jsonl")
        refused("conflicting-declarations-refused", repo / "shared.jsonl", 2,
                "conflicting registry declarations", "MR-1")
        declare(mistakes_register="reg.jsonl", attestations="./reg.jsonl")
        refused("aliased-declarations-refused", reg, 2,
                "conflicting registry declarations", "MR-4")
        # FIRST-ROW SERIALIZATION (both reviews, MAJOR 1): an appender that lands between the
        # exclusive creation and the creator's lock forces the creator to refuse under its
        # lock; exactly the interleaved row remains, and the chain stays clean
        declare(mistakes_register="race.jsonl")
        race_reg = repo / "race.jsonl"
        race = {"fired": False, "inner": None}
        real_flock = fcntl.flock

        def interleaving(lock_fd, op):
            if not race["fired"]:
                race["fired"] = True
                race["inner"] = append(race_reg, "MR-1")
            return real_flock(lock_fd, op)

        fcntl.flock = interleaving
        try:
            rc, err = append(race_reg, "MR-1")
        finally:
            fcntl.flock = real_flock
        if race["inner"] is None or race["inner"][0] != 0:
            failures.append("first-row-race: the interleaved appender should land its row, "
                            "got {!r}".format(race["inner"]))
        if rc != 2 or "between its exclusive creation and the lock" not in err \
                or "left in place" not in err:
            failures.append("first-row-race: the creator should refuse (exit 2) naming the "
                            "window and keep the row, got exit {}: {}".format(rc, err.strip()))
        race_lines = _lines(race_reg)
        if len(race_lines) != 1 or verify_lines(race_lines, root=gate_root["root"],
                                                prefix="MR-"):
            failures.append("first-row-race: exactly the interleaved row should remain, with "
                            "a clean verify_lines")
        # a file that APPEARS between the validation and the exclusive creation is refused
        # (O_CREAT with O_EXCL) and the appeared file's bytes stay intact
        declare(mistakes_register="appeared.jsonl")
        appeared = repo / "appeared.jsonl"

        def planting(vroot, vpath, vlines, allow_missing_anchor=False):
            out = real_vao(vroot, vpath, vlines, allow_missing_anchor=allow_missing_anchor)
            appeared.write_bytes(b"interloper bytes\n")
            return out

        globals()["verify_append_only"] = planting
        try:
            rc, err = append(appeared, "MR-1")
        finally:
            globals()["verify_append_only"] = real_vao
        if rc != 2 or "could not be created exclusively" not in err:
            failures.append("appeared-register-refused: want exit 2 naming the exclusive "
                            "creation, got exit {}: {}".format(rc, err.strip()))
        if appeared.read_bytes() != b"interloper bytes\n":
            failures.append("appeared-register-refused: the appeared file was modified")
        # a register UNLINKED between the validation and the write is refused and never
        # re-created (the vanished-path leg of the write binding)
        vanish = repo / "vanish.jsonl"
        declare(mistakes_register="vanish.jsonl")
        accepted("vanish-seed", vanish, "MR-1")

        def vanishing(vroot, vpath, vlines, allow_missing_anchor=False):
            out = real_vao(vroot, vpath, vlines, allow_missing_anchor=allow_missing_anchor)
            os.unlink(str(vanish))
            return out

        globals()["verify_append_only"] = vanishing
        try:
            rc, err = append(vanish, "MR-1", "accepted")
        finally:
            globals()["verify_append_only"] = real_vao
        if rc != 2 or "no longer the file that was validated" not in err:
            failures.append("vanished-register-refused: want exit 2 naming the binding, got "
                            "exit {}: {}".format(rc, err.strip()))
        if os.path.lexists(str(vanish)):
            failures.append("vanished-register-refused: a refusal re-created the register")
        # a descriptor read failure is cannot-evaluate with a named reason, never an
        # unhandled exception
        declare(mistakes_register="reg.jsonl")
        real_os_read = os.read
        read_fail = {"fired": False}

        def failing_read(read_fd, count):
            if not read_fail["fired"]:
                read_fail["fired"] = True
                raise OSError(5, "injected input-output error")
            return real_os_read(read_fd, count)

        os.read = failing_read
        try:
            refused("descriptor-read-failure-exit-2", reg, 2,
                    "cannot be read from its open descriptor", "MR-4")
        finally:
            os.read = real_os_read
        # an OSError DURING the write rolls back to the validated size exactly like a short
        # write (the failed-write branch, distinct from the RLIMIT short-write leg above)
        real_os_write = os.write
        write_fail = {"fired": False}

        def failing_write(write_fd, payload):
            if not write_fail["fired"] and isinstance(payload, bytes) \
                    and payload.startswith(b'{"'):
                write_fail["fired"] = True
                real_os_write(write_fd, payload[:7])
                raise OSError(5, "injected write failure")
            return real_os_write(write_fd, payload)

        os.write = failing_write
        try:
            refused("oserror-write-rolls-back", reg, 2, "truncated back", "MR-4")
        finally:
            os.write = real_os_write
        # the disclosed rollback RESIDUAL: when the truncation itself fails, the refusal
        # names the un-truncated state and the partial bytes remain for the register gate
        declare(mistakes_register="rollfail.jsonl")
        rollfail = repo / "rollfail.jsonl"
        accepted("rollfail-seed", rollfail, "MR-1")
        rollfail_seed = rollfail.read_bytes()
        real_ftruncate = os.ftruncate
        write_fail2 = {"fired": False}

        def failing_write2(write_fd, payload):
            if not write_fail2["fired"] and isinstance(payload, bytes) \
                    and payload.startswith(b'{"'):
                write_fail2["fired"] = True
                real_os_write(write_fd, payload[:7])
                raise OSError(5, "injected write failure")
            return real_os_write(write_fd, payload)

        def failing_truncate(trunc_fd, length):
            raise OSError(5, "injected truncate failure")

        os.write = failing_write2
        os.ftruncate = failing_truncate
        try:
            rc, err = append(rollfail, "MR-1", "accepted")
        finally:
            os.write = real_os_write
            os.ftruncate = real_ftruncate
        if rc != 2 or "could NOT be truncated back" not in err:
            failures.append("failed-rollback-residual: the refusal must name the failed "
                            "truncation, got exit {}: {}".format(rc, err.strip()))
        left = rollfail.read_bytes()
        if not left.startswith(rollfail_seed) or len(left) != len(rollfail_seed) + 7:
            failures.append("failed-rollback-residual: exactly the named partial bytes "
                            "should remain (a disclosed residual, repaired by hand)")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        failures.append("a fixture step raised {}: {}".format(type(exc).__name__, exc))
    finally:
        globals()["_gate_root"] = real_gate_root
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: an illegal transition, an unknown status, a landed row without an "
          "existing commit or check_ref, a row that would fuse onto an unterminated last line, an "
          "anchored rewrite, a wrong-family id in either direction, and a --prefix that disagrees "
          "with the registry declaration are each refused (exit 1); an unreadable register or "
          "register descriptor, a symlinked register path, an undeclared register, a gate root "
          "that is not a git repository, conflicting declarations resolving to one path, a "
          "malformed, unreadable or unusable anchor entry (a dangling symlink and a directory "
          "included), a git failure deciding whether the register is tracked, a tracked "
          "register with no computable merge-base, a register replaced or unlinked between "
          "validation and write, a first row interleaved between the exclusive creation and "
          "the lock (the interleaved row survives, the creator refuses), a file that appeared "
          "before the exclusive creation, and a failed or short write (rolled back to the "
          "validated size, a failed rollback disclosed with its partial bytes named) are "
          "refused as cannot-evaluate (exit 2); every refusal leaves the rows other appenders "
          "wrote byte-identical (the disclosed failed-rollback residual excepted); the "
          "register validates in the gate root's own context (a nested-repository register "
          "validates under the declaring outer root); the lock is held across the validation; "
          "and every clean append is re-read with its last row and verify_lines checked")
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
