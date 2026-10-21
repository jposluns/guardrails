#!/usr/bin/env python3
"""Mistakes-register appender and AEI projection (GD-112 component 6; rules mstreg/slfgrd).

  orch_register.py append --register PATH --id MR-N --mistake T --evidence R --rule CID \
                          --guardrail T [--status proposed] [--commit SHA --check-ref PATH]
                          [--prefix MR] [--klass T] [--ref R] [--bootstrap]
      Append one chained row (seq and prev computed from the file; a wrong --id fails). --prefix
      parameterizes the id family (MR for the mistakes register, AT for an attestation register,
      GD-127 C.2) and must agree with the registry declaration; --klass writes the optional class
      field (for example systemic-lapse); --ref writes the blocker ref an attestation row covers.
      An ordinary append requires the register's append-only authority exactly as the register
      gate without --update-anchor does: an untracked register with no anchor is refused with
      exit 2 naming the anchor. --bootstrap is the explicit first-row operation for such a
      register: it appends the register's FIRST row (a register that already holds a line is
      refused with exit 2, its anchor being check_mistakes_register.py --update-anchor's to
      establish), validated under the gate's --update-anchor allowance, and then establishes the
      anchor that --update-anchor would (update_anchor on the register as committed), so after
      either operation the ordinary gate passes on the result.
      The appender validates in the register gate's own context: the validation root is the
      nearest enclosing git repository of this appender's own installed location, never the
      repository that merely contains the register and never a root derived from where this
      invocation runs (an appender installed outside any git repository refuses with exit 2);
      the id family comes from
      that root's registry declaration (.aiqt/orchestration.local.json or
      .aiqt/orchestration.json) that names the register path, and two declarations resolving to
      the same register path (or to one file through hard links) are refused as ambiguous. Before anything is written, the register
      text as the append would leave it
      (the existing decoded lines plus the new row; validation runs on the decoded UTF-8 text with
      the gate's own line splitting, not on raw bytes) is run through the register gate's own
      parser, check_mistakes_register.verify_lines and verify_append_only (the missing-anchor
      allowance only under --bootstrap, for a first row), so an append the gate would reject
      is refused at write time instead of reaching CI. A violation (an illegal transition, an
      unknown status, a wrong-family id, a landed row without an existing --commit and --check-ref)
      exits 1; a context or authority the appender cannot evaluate (an unreadable register or
      register descriptor, a symlinked register path, a register path that is not a regular
      file (a FIFO, a device or a socket: the register open is NON-BLOCKING, so
      no file type can make the open wait, and the opened descriptor's own fstat decides
      before any lock or read, because on Linux a read-write open of a FIFO succeeds while a
      read through that descriptor can wait forever; a directory is refused by the open
      itself (EISDIR, before any descriptor exists), and a socket, which that open cannot
      address at all (ENXIO, before any descriptor exists), is refused with its type named
      from the path's own lstat, a point-in-time read taken only for the message; the
      non-blocking flag is cleared once
      the file is proven regular), an appender installed outside any git
      repository, a gate root git itself does not confirm as a repository toplevel (an invalid
      gitfile, an empty .git directory or a dangling .git symlink is a marker, not a
      repository, selected by the shared lstat marker walk and never walked past, and a marker
      whose inspection fails with anything but ENOENT refuses by name; a valid linked worktree
      passes; the probe and every later git authority read run under a scrubbed environment
      with every GIT_-prefixed variable removed and only the exact-valued config-isolation
      pins re-applied (GIT_CONFIG_NOSYSTEM at "1", GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at
      os.devnull, the purely subtractive values the self-test scrub sets, so a scrubbed
      self-test's probe and authority reads keep reading no system or global configuration),
      so a caller-supplied GIT_DIR, GIT_WORK_TREE
      or GIT_INDEX_FILE never redirects them), a registry entry or declared register path whose inspection fails with
      anything but a CONFIRMED ENOENT (only absence confirmed at the final name under a
      parent that opens, or down a chain of such confirmations, reads as absent; a dangling
      symbolic link at a declared path is refused by name), a registry that is not
      a regular file (the registry is read through the same non-blocking open and
      regular-file proof as the register, so a FIFO, a directory, a device or a socket at
      the registry path is refused with its named file type, never read and never blocked
      on), a non-string or empty
      registry declaration, a
      path no registry declaration names, conflicting declarations resolving to one path, a git
      failure deciding whether the register is tracked, no computable merge-base for a tracked
      register, a malformed or unusable anchor entry, a dangling symlink, an anchor lstat
      failure that is not ENOENT and an anchor path whose own chain cannot confirm the
      absence included (an anchor under a register directory this first row is yet to
      create reads as absent only because every missing component is itself a confirmed
      ENOENT), an untracked register with no anchor on an ordinary
      append, a landed row's commit read that git cannot launch or that times out, a register
      directory that cannot be created, a lock that cannot be acquired, a commit step that
      fails) exits 2; each
      refusal names its reasons on stderr and leaves every row other appenders wrote
      byte-identical (a refusal taken under the acquired lock after the exclusive first-row
      creation removes the file and the directory components this call observed as its own
      (each component created with mkdir relative to an open descriptor on its parent
      directory, recorded only after a no-follow identity stat relative to that same
      descriptor, and removed with rmdir relative to a descriptor the sweep itself descends
      to, only after that identity, st_dev and st_ino, is re-confirmed by a no-follow stat
      relative to that descriptor),
      with an uninspectable ancestor refusing before any mkdir, but only
      while that file is still this call's own empty file; a lock that cannot be acquired on
      the created file leaves that empty file in place, because removal without the lock could
      race a cooperating appender; anything else is left in place and the refusal says so).
      Cooperating appenders are serialized by an exclusive flock held across the read, the
      validation and the commit of an existing register (an appender that took the lock on a
      file another appender's commit has since replaced at the path starts over on the file
      the path names, at most LOCK_WAITS times); the first row of an absent register
      is validated BEFORE its exclusive creation, then created exclusively
      (O_EXCL) and then re-checked under the acquired lock (the path still names the created
      file and it is still empty), so an appender that lands between the creation and the
      lock forces a refusal, never an interleaved row (the serialization there is the
      exclusive creation plus those under-lock re-checks, not a second validation; the
      re-checks catch the register-row interleavings their identity, link-count, size and
      byte comparisons detect, and other validation inputs, the anchor included, are not
      revalidated under the lock). The commit is ALL OR NOTHING: the register is opened once
      with O_NOFOLLOW and never written; the validated bytes plus the new row are written in
      one write to a temporary file created exclusively in the register's own directory
      under the lock (and locked itself, so the lock carries over to the file the rename puts
      at the path), given the register's permission bits and fsynced, and only after the
      under-lock re-check (the path still names the validated file's device and inode, the
      file has no other hard link, and it still holds exactly the validated bytes) and the
      check that the temporary's name still names the temporary (its lstat has the open
      temporary's device and inode and is a regular file) is the temporary renamed over the
      register, still under the lock, after which the directory is fsynced. The temporary is
      created in the register's directory, so a commit needs write access to that directory.
      Any failure before the rename (the temporary's creation, its lock, a failed or short
      write, its permission bits, its fsync, the re-check, the temporary's name check, the
      rename itself) leaves the register's bytes unchanged, since nothing was written to it,
      and removes the temporary while its name still names this call's own file (a removal
      failure is named with the temporary's path; the identity check and the unlink are two
      system calls, so an entry put at the name between them is unlinked in the temporary's
      place). OUTSIDE THE GUARANTEE: a crash after the rename and before the
      directory sync completes (the rename may then not be durable), and a directory-sync or
      --bootstrap anchor-write failure after the rename, which exits 2 saying the row is in
      the register; a crash before the rename can leave the temporary at its name beside an
      unchanged register; and the replacing file carries the register's permission bits but
      not its owner, group, ACLs or extended attributes. A writer that does not take the lock
      (a hand edit, any other tool) is outside that serialization. At the register, the
      re-check catches one that lands between the validation and the re-check, one landing
      between the re-check's last read and the rename (only the temporary's lstat runs in
      between) is overwritten by the rename, and any other is caught later by the
      mistakes-register gate, not here. At the temporary's name, the name check refuses an
      entry swapped in from the temporary's creation up to that check, but one swapped in
      between the check and the rename (two system calls, lstat then rename) is renamed over
      the register unvalidated and the append reports success; the check proves which file the
      name holds, not its bytes, so a writer that opens the temporary by name and rewrites it
      before the rename is not caught here either. A second RESIDUAL: the hard-link conflict
      check on the registry declarations is point-in-time; a hard link created after that
      check is not caught by this appender and is left to the register gate. A third
      RESIDUAL: POSIX offers no race-free proof that the directory found at a name is the one
      mkdir just made, so the entry a created component's no-follow identity read-back finds
      at the name is recorded as this call's own, and the cleanup sweep's identity re-check
      and its rmdir are two calls; the cleanup's exact bound is stated once, at
      _made_dirs_swept (abbreviated: rmdir of an EMPTY directory only, within the two
      disclosed windows, inode reuse included; never a non-empty directory and never a
      file). The sweep removes by descriptor, like creation: it re-opens the recorded
      outer parent by path (so a rename of the tree before the sweep starts is not
      followed), descends ITERATIVELY one name at a time with O_NOFOLLOW (a raced,
      unrecorded middle component is descended through by name and never removed),
      re-checks each component's recorded device and inode with a no-follow stat
      relative to the descriptor of its parent and removes it with rmdir relative to that
      same descriptor, so an ancestor swapped for a symlink during the sweep can never
      redirect a removal outside the recorded tree. Its report is three-valued: a
      component is reported absent, AT ITS OWN NAME and never as gone (it may have been
      renamed away), only when its own lookup found it absent there, a recorded
      component the sweep never looked up (behind an absent, renamed or unreadable
      parent) is NAMED unreachable, never counted absent, and one it looked up and kept
      is left in place and NAMED with its reason. The sweep's two descriptor slots hold
      at most two descriptors at once; each one's owning slot is cleared before its
      close, every slot still held is attempted in a finally, and a failed close is
      reported as a close failure, never retried and never as a directory left in place
      (Linux releases the number even then; where a failed close leaves its descriptor
      open, that descriptor is outside the slots, one per reported close failure).
  orch_register.py project --register PATH
      Emit an AEI v1 enumeration of the rows whose LATEST status is proposed or accepted and not yet
      landed, as granted open items, so the stop guard itself enforces mistake-to-guardrail
      follow-through. A registry can name this as (part of) its enumerator. An attestation register
      is evidence, never backlog: project the mistakes register, not the attestations file.
      The projection reads through the same non-blocking open and descriptor file-type check
      as the appender: a register that is not a regular file is refused with exit 2 and a
      diagnostic naming the file type (a socket, which the open cannot address at all, is
      named from a following stat, so a symlink to a socket is named as a socket), never
      read and never blocked on, and any OTHER open, descriptor, path-value or decode
      failure is refused with exit 2 naming the error, never an escaped traceback. An
      absent register still projects as no items, where absent means an open ENOENT
      CONFIRMED at the final name (re-inspected without following links, under a parent
      that opens): a dangling symbolic link at the register path, a register path under a
      missing parent directory, or any other unresolvable path is exit 2 naming the
      cause, and a descriptor failure after a successful open is exit 2, never a
      successful empty projection. A register line that is not JSON, or whose row is not
      a JSON object with a string id, is refused with exit 2 as well, and so is a row
      whose decoded value is still too deep to format into the enumeration. The path is
      still followed, so a symlink to a regular register projects as before.
  orch_register.py --self-test
      Synthetic fixtures for the write-time validation: each refusal vector leaves the register
      and its anchor byte-identical (the first-row cleanup and left-in-place vectors assert
      their named outcomes instead), a failure injected at each commit step leaves the
      register's bytes unchanged and no temporary file, a file or symlink swapped in at the
      temporary's name before the name check is refused by that name and left in place with the
      register byte-identical, every clean append is
      re-read and its landed last row and a clean verify_lines asserted, the serialization
      lock, the first-row creation re-checks (size and file identity) and the write binding
      are probed in place, an ordinary append to an untracked register with no anchor is
      refused while --bootstrap lands the first row and leaves a register the ordinary gate
      passes, and the production root resolution runs in subprocess vectors,
      unpatched for the appender installed inside a repository and, for the appender
      installed outside any, through a test-only search ceiling at the fixture's temporary
      directory (the walk is unchanged and still climbs to the filesystem root, but a .git
      entry in a directory above that one reads as absent, so a git checkout around
      TMPDIR cannot decide the verdict).
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
import secrets
import stat
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mistakes_register import (  # noqa: E402
    REGISTRY_FILES, CannotEvaluate, _absent_down_chain, _nonregular_name, _read_following,
    confirmed_root, entry_state, gate_root, update_anchor, verify_append_only, verify_lines)

ZERO = "0" * 64
# how many times an append starts over because a cooperating appender replaced the register
# while this one waited for its lock, before it refuses
LOCK_WAITS = 16


def _lines(path):
    """The register's decoded lines, read without blocking through the shared
    check_mistakes_register._read_following (a non-blocking open whose OPENED descriptor's
    own fstat must prove a regular file before anything is read; a directory, a FIFO or a
    device is never a register, and a socket, which that open cannot address at all, is
    named from a following stat taken only for the message). Raises CannotEvaluate naming
    the file type for anything that is not a regular file (or naming the descriptor
    instead, when the refusal's own close also fails: the close's OSError takes over, so
    the message changes but the result stays CannotEvaluate), and naming the error for any
    other open, descriptor, path-value or decode failure, so no OSError or ValueError
    escapes to the caller as a traceback. ONLY an open ENOENT that the shared reader
    CONFIRMS at the final name (a no-follow re-inspection under a parent that opens) reads
    as an absent register (no lines): a dangling symbolic link at the register path, a
    register path under a missing parent directory, or any other unresolvable path raises
    CannotEvaluate naming the cause, and the shared reader converts every OSError after a
    successful open (from fstat, read or close) to CannotEvaluate, so a descriptor failure
    reporting ENOENT is exit 2 for the caller, never a successful empty projection. The
    path is followed as before, so a symlink to a regular register still reads; what it
    aliases is still type-checked."""
    try:
        raw = _read_following(path, "the register path")
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise CannotEvaluate("the register cannot be read ({})".format(exc))
    try:
        return raw.decode("utf-8").splitlines()
    except ValueError as exc:
        raise CannotEvaluate("the register cannot be read ({})".format(exc))


def _gate_root():
    """The register gate's own validation-root CANDIDATE: the nearest enclosing .git entry
    above this appender's own installed location, through the SHARED
    check_mistakes_register.gate_root walk (entry_state: an explicit lstat whose only absence
    answer is a CONFIRMED ENOENT, so a dangling .git symlink is SELECTED, never walked past,
    and left for
    the git confirmation to refuse, and any other inspection failure raises CannotEvaluate,
    never reads as absence). The gate validates a DECLARED register against THIS root, not
    against the repository that happens to contain the register. This resolver has NO cwd
    fallback: an appender installed outside any git repository returns None and cmd_append
    refuses with exit 2, so a validation root is never
    derived from where the invocation runs. The returned candidate is only a MARKER until
    cmd_append requires git itself to confirm it (check_mistakes_register.confirmed_root,
    under a scrubbed git environment), so a marker that is not a repository is refused, never
    trusted and never walked past."""
    return gate_root(Path(__file__).resolve().parent)


def _declared_family(root, path):
    """The (registry key, id family) of the declaration that names this register path. Raises
    CannotEvaluate when there is no registry, the registry cannot be inspected or read
    (registry PRESENCE is an explicit lstat whose only absence answer is a confirmed ENOENT,
    so an
    inspection failure or a dangling entry never selects the lower-priority registry file;
    the read itself goes through the shared non-blocking open and regular-file proof, so a
    registry that is a FIFO, a directory, a device or a socket is refused with its named
    file type, never read and never blocked on), a
    declaration is not a non-empty string, no declaration names the path, a declared path's
    file identity cannot be inspected (only a path CONFIRMED absent, every missing component
    a plain ENOENT under a parent that opens, omits an identity from
    the hard-link conflict check: an unreadable identity is never a missing identity, and a
    dangling symbolic link at a declared path is refused by name, never read as absent), or two
    declarations resolve to the same register path (the declared id family is then ambiguous,
    so there is no single gate context to validate in): the register gate evaluates declared
    surfaces only. RESIDUAL: the identity comparison is point-in-time; a hard link created
    after this check is not caught here and is left to the register gate. RESIDUAL: the
    named too-deeply-nested refusal below covers this appender's own registry read (the
    register gate and the record-drift gate carry their own); the hook script
    (aiqt_hooks.py) reads the registry and the register anchor catching ValueError only,
    so a registry or anchor nested too deeply for the JSON decoder there (a
    RecursionError) is handled by that script's top-level dispatcher handler, which fails
    closed in blocking modes and warns in fail-open modes, never by a named per-read
    refusal."""
    registry = None
    found = False
    for rel in REGISTRY_FILES:
        candidate = Path(root) / rel
        try:
            candidate_entry = entry_state(candidate)
        except CannotEvaluate as exc:
            raise CannotEvaluate("the orchestration registry at {} cannot be inspected ({}); "
                                 "only confirmed absence (ENOENT) selects the next registry "
                                 "file".format(rel, exc))
        if candidate_entry is None:
            continue  # confirmed absence: the next registry file may still be present
        found = True
        # The registry is read like the register itself: a non-blocking open whose opened
        # descriptor must prove a regular file, so a FIFO (or any other non-regular file)
        # at the registry path is refused by name instead of blocking the appender; a
        # non-regular registry raises with its named file type, and any other read failure
        # keeps this clause's own wording.
        try:
            registry = json.loads(_read_following(
                candidate, "the orchestration registry").decode("utf-8"))
        except RecursionError:
            # the decoder raises RecursionError, not ValueError, past its nesting limit;
            # a registry that deep is the same named refusal, never an escaped traceback
            raise CannotEvaluate("the orchestration registry is nested too deeply for the "
                                 "JSON decoder")
        except (OSError, ValueError) as exc:
            raise CannotEvaluate("the orchestration registry cannot be read ({})".format(exc))
        break
    if not found:
        raise CannotEvaluate("no orchestration registry declares this register (the gate "
                             "validates declared surfaces only)")
    if not isinstance(registry, dict):
        raise CannotEvaluate("the orchestration registry is not a JSON object; a malformed "
                             "registry offers no declared surfaces to evaluate")
    want = path.resolve()
    matched = None
    resolved = []
    identities = []
    for key, family in (("mistakes_register", "MR"), ("attestations", "AT")):
        if key not in registry:
            continue
        declared = registry[key]
        if not isinstance(declared, str) or not declared:
            raise CannotEvaluate("the registry's {} declaration is not a non-empty string; a "
                                 "malformed declaration offers no register path to "
                                 "evaluate".format(key))
        declared_path = Path(declared) if os.path.isabs(declared) else Path(root) / declared
        target = declared_path.resolve()
        resolved.append(target)
        try:
            declared_stat = os.stat(str(declared_path))
            identities.append((declared_stat.st_dev, declared_stat.st_ino))
        except FileNotFoundError:
            # the stat follows the path, so its ENOENT also covers a dangling symbolic
            # link and a missing parent chain: only a declared path CONFIRMED absent
            # (every missing component a plain ENOENT under a parent that opens, the
            # state a first append into a directory it is yet to create legitimately
            # sees) has no identity to collide on; a dangling link at the declared path,
            # or any other unresolvable path, is a named refusal, never an absent
            # register
            if not _absent_down_chain(declared_path):
                raise CannotEvaluate("the declared {} path names a dangling symbolic link "
                                     "(an entry is present at its final name but it does "
                                     "not resolve to a file); an unresolvable register "
                                     "path is never read as absent".format(key))
        except OSError as exc:
            raise CannotEvaluate("the declared {} path cannot be inspected ({}); only "
                                 "confirmed absence (ENOENT) omits a file identity from the "
                                 "hard-link conflict check".format(key, exc))
        if matched is None and target == want:
            matched = (key, family)
    if len(set(resolved)) < len(resolved) or len(set(identities)) < len(identities):
        raise CannotEvaluate("conflicting registry declarations resolve to the same register "
                             "path (two names for one file, hard links included), so the "
                             "declared id family is ambiguous (fix the registry before "
                             "appending)")
    if matched is not None:
        return matched
    raise CannotEvaluate("no registry declaration names this register path (the gate validates "
                         "the declared mistakes_register and attestations surfaces only)")


def _fd_bytes(fd):
    """Every byte of the file open on FD, read with pread from offset zero (the descriptor's own
    offset is left alone)."""
    out = bytearray()
    while True:
        chunk = os.pread(fd, 1 << 16, len(out))
        if not chunk:
            return bytes(out)
        out += chunk


def _commit_failure(fd, path, raw, tmp_fd, tmp_path, content):
    """Commit CONTENT (the validated bytes RAW plus the new row) by replacing the register with
    the temporary file TMP_PATH, open on TMP_FD and created exclusively in the register's own
    directory under the register lock held on FD. The steps, in order: lock the temporary (so
    the file the rename puts at the path is already locked and the serialization carries over
    to it), write CONTENT in one write (a short write is a failure, never resumed), give the
    temporary the register's permission bits, fsync it, then re-check under the lock that the
    path still names the validated file (device and inode), that the file has no other hard
    link (the rename would leave such a name at the old content) and that it still holds
    exactly RAW (its size, then its bytes, so an in-place rewrite of equal length is caught
    too), then check that the temporary's name still names the temporary (its lstat has the
    device and inode fstat read on TMP_FD after the fsync, and is a regular file, so a file or
    symlink swapped in at the name is refused by that name), and only then rename the
    temporary over the register. Returns None once the rename has landed, else the refusal
    clause for the step that failed: every failure before the rename leaves the register's
    bytes as they were, since nothing is ever written to it, and the caller removes the
    temporary. RESIDUAL: the rename goes by name and POSIX offers no rename conditional on the
    source's identity, so the name check and the rename are two system calls; an entry a
    writer that ignores the lock puts at the temporary's name between them is renamed over the
    register unvalidated, and this returns None."""
    try:
        fcntl.flock(tmp_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return "the temporary file could not be locked ({})".format(exc)
    try:
        written = os.write(tmp_fd, content)
    except OSError as exc:
        return "the write to the temporary file failed ({})".format(exc)
    if written != len(content):
        return "short write to the temporary file ({} of {} bytes)".format(written, len(content))
    try:
        st_fd = os.fstat(fd)
        os.fchmod(tmp_fd, stat.S_IMODE(st_fd.st_mode))
    except OSError as exc:
        return ("the temporary file could not be given the register's permission bits "
                "({})".format(exc))
    try:
        os.fsync(tmp_fd)
    except OSError as exc:
        return "the temporary file could not be synced to disk ({})".format(exc)
    try:
        st_tmp = os.fstat(tmp_fd)
    except OSError as exc:
        return "the temporary file cannot be inspected through its descriptor ({})".format(exc)
    try:
        st_fd = os.fstat(fd)
        st_path = os.lstat(str(path))
    except FileNotFoundError:
        st_path = None
    except OSError as exc:
        return "the register cannot be re-checked under the lock ({})".format(exc)
    if st_path is None or (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
        return ("the register at this path is no longer the file that was validated (replaced "
                "between the validation and the commit)")
    if st_fd.st_nlink != 1:
        return ("the register has {} hard links; the commit replaces it by rename, which would "
                "leave every other name at the old content".format(st_fd.st_nlink))
    if st_fd.st_size != len(raw):
        return ("the register on disk is {} byte(s) but the validated text is {} byte(s) (a "
                "writer changed the file after the validation; for a first row, after its "
                "exclusive creation)".format(st_fd.st_size, len(raw)))
    try:
        same = _fd_bytes(fd) == bytes(raw)
    except OSError as exc:
        return "the register cannot be re-read under the lock ({})".format(exc)
    if not same:
        return ("the register's bytes changed after the validation (same size, different "
                "content: a writer rewrote it in place)")
    # the rename goes by name, so the entry at the temporary's name must still be the regular
    # file this call wrote and synced; the lstat and the rename stay two calls
    try:
        st_name = os.lstat(tmp_path)
    except FileNotFoundError:
        return "the temporary file {} is no longer at its name".format(tmp_path)
    except OSError as exc:
        return "the temporary file {} cannot be re-checked before the rename ({})".format(
            tmp_path, exc)
    if ((st_name.st_dev, st_name.st_ino) != (st_tmp.st_dev, st_tmp.st_ino)
            or not stat.S_ISREG(st_name.st_mode)):
        return ("the entry at the temporary file's name {} is no longer this call's temporary "
                "(replaced after its creation)".format(tmp_path))
    try:
        os.replace(tmp_path, str(path))
    except OSError as exc:
        return "the temporary file could not be renamed over the register ({})".format(exc)
    return None


def _temp_removed(tmp_fd, tmp_path):
    """Remove the commit's temporary file after a failure before the rename, only while its name
    still names this call's own temporary (device and inode); returns the clause naming the
    outcome, a removal failure by the temporary's path. RESIDUAL: the identity check and the
    unlink are two system calls, so an entry a writer puts at the name between them is
    unlinked in the temporary's place."""
    try:
        st_fd = os.fstat(tmp_fd)
        st_path = os.lstat(tmp_path)
        if (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
            return ("the entry now at the temporary file's name {} is not this call's temporary "
                    "and was left in place".format(tmp_path))
        os.unlink(tmp_path)
    except FileNotFoundError:
        return "the temporary file {} was no longer at its name".format(tmp_path)
    except OSError as exc:
        return ("the temporary file {} could not be removed ({}) and must be removed by "
                "hand".format(tmp_path, exc))
    return "the temporary file {} was removed".format(tmp_path)


def _dir_synced(directory):
    """fsync DIRECTORY after the rename so the rename itself reaches the disk; returns None, or
    the failure for the message (the rename has already landed either way)."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        return "the register's directory could not be opened to sync the rename ({})".format(exc)
    note = None
    try:
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            note = "the register's directory could not be synced ({})".format(exc)
    finally:
        try:
            os.close(dir_fd)
        except OSError as exc:
            note = ((note + "; ") if note else "") + (
                "the descriptor on the register's directory could not be closed "
                "({})".format(exc))
    return note


def _swept_closed(fd, where, close_failures):
    """Close one descriptor the sweep held (on the directory WHERE names), recording a
    failed close in CLOSE_FAILURES, KEPT APART from the removal outcomes: a failed close
    is a descriptor-close failure, never a directory left in place, and never raised (an
    OSError; any other exception class propagates, so every caller has already cleared
    the slot that owned FD before calling this)."""
    try:
        os.close(fd)
    except OSError as exc:
        close_failures.append("the descriptor the sweep held on {} could not be closed: "
                              "{}".format(where, exc))


def _made_dirs_swept(made_dirs):
    """Remove the directory components THIS CALL observed as its own, innermost first, BY
    DESCRIPTOR, mirroring creation: each entry is (path, st_dev, st_ino), recorded by
    _dirs_created only after its own mkdir succeeded and its identity was read back with a
    no-follow stat relative to the open descriptor on its parent. The sweep re-opens the
    recorded OUTER parent by path once (so a rename of the whole tree BEFORE the sweep
    starts is not followed; only a rename after that open is), then walks ITERATIVELY, no
    recursion and at most two descriptors held in its two slots however deep the record
    (the slots' bound, stated under DESCRIPTOR SLOTS below): it descends one
    name at a time, each opened O_RDONLY | O_DIRECTORY | O_NOFOLLOW relative to the
    descriptor above it and identity-read with os.fstat on the opened descriptor (a
    recorded component must still match its recorded device and inode before the sweep
    descends through it), removes the innermost recorded component first, and climbs back
    out step by step, re-checking each climbed-to directory with the same fstat identity
    read against the identity observed on the way down. The record can be DISCONTINUOUS
    (a raced mkdir by another actor is used for the descent, never recorded) and can
    contain ".." components: the sweep descends through an unrecorded intermediate BY
    NAME and removes nothing that is not recorded. The report is THREE-VALUED and
    conservative, per recorded entry: ABSENT (skipped) only when that entry's OWN
    no-follow lookup, relative to a reached parent, fails with ENOENT, which proves it
    absent AT ITS OWN NAME only (an entry renamed away is found absent there while it
    survives elsewhere, so absence is never reported as gone); UNREACHABLE, named
    with its reason, for every recorded entry the sweep never looked up (behind an
    absent, renamed, substituted or uninspectable parent, past a climb it could not make,
    or off the lexical chain), never folded into absence, since it may remain at its name
    or wherever its parent was moved; and LEFT in place, named with its reason, for an
    entry the sweep looked up and could not prove to be the created component or could
    not remove.
    The removal itself re-checks the name with a
    no-follow os.stat relative to the held parent descriptor (the recorded st_dev and
    st_ino and a directory mode) and removes with os.rmdir relative to that same
    descriptor, so an ancestor swapped for a symlink during the sweep can never redirect
    a removal outside the recorded tree. An entry this sweep looked up and cannot prove to
    be the created component (an inspection failure, a changed identity, a non-directory)
    is left in place and NAMED, and nothing beneath a kept or unreachable entry is ever
    removed. DESCRIPTOR SLOTS: each descriptor the sweep opens is owned by one of two
    slots (cur_fd, nxt_fd), so the slots hold at most two at once, and one rule holds at
    every close (the descent handoff, the climb handoff, the stopped-descent release and
    the finally): the owning slot is cleared, or re-bound by the handoff, BEFORE the
    close is called, so an exception of any class escaping a close never leaves the
    closed number (possibly reissued by then) in a slot a later close reaches; the
    finally attempts the close of each slot still held, the second in a nested finally,
    so an exception from one slot's close never skips the other. Each close is attempted
    once and never retried: a close that fails with OSError is recorded in the returned
    close failures, KEPT APART from the removal outcomes, never raised (on Linux the
    number is released even then, so a retry could close a reissued number); an
    attempted close is not otherwise proof of release. The slots' bound is the whole
    descriptor claim: a descriptor leaves its slot before its close is attempted, so
    where a failed close does NOT release it (Linux releases it), it stays open outside
    the slots and the sweep goes on; the live count is then at most two plus the close
    failures reported, not two. The sweep does not stop at a failed close, because its
    removals never rely on a descriptor that left the slots and stopping would leave
    recorded components behind on a platform that released the number. BOUND
    (exact, stated once here; every other site abbreviates and says so): each removal is
    rmdir of an entry that is an EMPTY directory at removal time, at most one moved-in
    empty directory per created component (the mkdir-to-stat window) plus the
    identity-check-to-rmdir window (the re-check and the rmdir are two calls, so an empty
    directory swapped into the name between them is removed; a non-empty one is left in
    place and named), ownership is not guaranteed across either window, and the
    device-and-inode re-check cannot detect inode reuse (an empty directory that reuses
    the recorded device and inode at the recorded name before the re-check is removed as
    if it were the recorded one); never a non-empty directory and never a file. Returns
    (left, skipped, unreachable, close_failures): the components left in place, each with
    its reason (an rmdir failure included), the ones found absent at their own name, the
    unreachable ones, each with its reason, and the descriptor-close failures."""
    left, skipped, unreachable, close_failures = [], [], [], []
    if not made_dirs:
        return left, skipped, unreachable, close_failures
    rev_dirs = list(reversed(made_dirs))
    outer_parent = rev_dirs[0][0].parent
    # The descent plan: every NAME between the recorded outer parent and the innermost
    # recorded component, outermost first, each as (name, path, recorded identity), the
    # identity None for a name _dirs_created did not record (a raced directory of another
    # actor, descended through, never removed). The plan is lexical; a recorded component
    # that is not nested under the chain so far cannot be reached by descriptor and is
    # named unreachable, every recorded component inside it too.
    chain = []
    cur_path = outer_parent
    for j, entry in enumerate(rev_dirs):
        comp, comp_dev, comp_ino = entry
        try:
            parts = comp.relative_to(cur_path).parts
        except ValueError:
            parts = ()
        if not parts:
            unreachable.extend("{} (not nested under the recorded chain, so the sweep "
                               "cannot reach it by descriptor)".format(inner)
                               for inner, _dev, _ino in rev_dirs[j:])
            break
        for name in parts[:-1]:
            cur_path = cur_path / name
            chain.append((name, cur_path, None))
        cur_path = cur_path / parts[-1]
        chain.append((parts[-1], cur_path, (comp_dev, comp_ino)))
    if not chain:
        return left, skipped, unreachable, close_failures
    try:
        cur_fd = os.open(str(outer_parent), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        unreachable.extend("{} (the recorded outer parent {} cannot be opened for the "
                           "sweep: {})".format(made, outer_parent, exc)
                           for _n, made, rec in reversed(chain) if rec is not None)
        return left, skipped, unreachable, close_failures
    cur_on = str(outer_parent)
    # the second slot: the one transient descriptor a descent or climb step holds, and
    # the directory it is on (both slots are cleared before any close of their number)
    nxt_fd = nxt_on = None
    observed = []  # observed[i]: the identity the descent read on chain[i]
    try:
        try:
            root_st = os.fstat(cur_fd)
        except OSError as exc:
            unreachable.extend("{} (the recorded outer parent {} cannot be inspected: "
                               "{})".format(made, outer_parent, exc)
                               for _n, made, rec in reversed(chain) if rec is not None)
            return left, skipped, unreachable, close_failures
        depth = 0          # cur_fd is on chain[depth - 1] (on the outer parent at 0)
        reach = len(chain)  # chain positions at or past reach are already reported
        while depth < len(chain) - 1:
            name, comp_path, rec = chain[depth]
            deeper = [str(p) for _n, p, r in chain[depth + 1:] if r is not None]
            kind = ("recorded parent" if rec is not None
                    else "raced unrecorded intermediate")
            # A failed step names every recorded entry beneath it UNREACHABLE (never looked
            # up, so never absent: an absent or renamed parent may have taken them with
            # it); the step's own entry is then looked up by the removal phase below.
            nxt_on = str(comp_path)
            try:
                nxt_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=cur_fd)
            except OSError as exc:
                unreachable.extend("{} (its {} {} cannot be descended into: {})".format(
                    inner, kind, name, exc) for inner in deeper)
                reach = depth + 1
                break
            try:
                nxt_st = os.fstat(nxt_fd)
            except OSError as exc:
                unreachable.extend("{} (its {} {} cannot be inspected: {})".format(
                    inner, kind, name, exc) for inner in deeper)
                reach = depth + 1
                break
            if rec is not None and (nxt_st.st_dev, nxt_st.st_ino) != rec:
                unreachable.extend("{} (its recorded parent {} is no longer the directory "
                                   "this call created)".format(inner, name)
                                   for inner in deeper)
                reach = depth + 1
                break
            observed.append((nxt_st.st_dev, nxt_st.st_ino))
            # the handoff: both slots are re-bound BEFORE the old number is closed
            prev, prev_on = cur_fd, cur_on
            cur_fd, cur_on, nxt_fd, nxt_on = nxt_fd, nxt_on, None, None
            _swept_closed(prev, prev_on, close_failures)
            depth += 1
        if nxt_fd is not None:
            # a stopped descent step still holds the descriptor it opened: released here,
            # slot cleared first, before the removal phase opens another
            prev, nxt_fd = nxt_fd, None
            _swept_closed(prev, nxt_on, close_failures)
        pos = depth  # cur_fd is on the parent of chain[pos]
        while True:
            name, comp_path, rec = chain[pos]
            if rec is not None and pos < reach:
                comp_st = None
                try:
                    comp_st = os.stat(name, dir_fd=cur_fd, follow_symlinks=False)
                except FileNotFoundError:
                    skipped.append(str(comp_path))
                except OSError as exc:
                    left.append("{} (cannot be inspected: {})".format(comp_path, exc))
                if comp_st is not None:
                    if ((comp_st.st_dev, comp_st.st_ino) != rec
                            or not stat.S_ISDIR(comp_st.st_mode)):
                        left.append("{} (no longer the directory this call "
                                    "created)".format(comp_path))
                    else:
                        try:
                            os.rmdir(name, dir_fd=cur_fd)
                        except OSError as exc:
                            left.append("{} (its rmdir failed: {})".format(comp_path, exc))
            if pos == 0:
                break
            # Climb back to the parent of chain[pos - 1]. The step that entered the
            # directory the sweep is on is chain[pos - 1]; its inverse is ".." unless
            # that step itself was "..", in which case the inverse is the name that
            # entered the level before it (two consecutive ".." steps cannot be
            # inverted by name, so the components above them are named unreachable). The
            # climbed-to directory must match the identity observed on the way down; a
            # failed climb step leaves its descriptor in the slot for the finally.
            expect = observed[pos - 2] if pos >= 2 else (root_st.st_dev, root_st.st_ino)
            above = str(chain[pos - 2][1]) if pos >= 2 else str(outer_parent)
            rest = [str(p) for _n, p, r in chain[:pos] if r is not None]
            if chain[pos - 1][0] != "..":
                step, flags = "..", os.O_RDONLY | os.O_DIRECTORY
            elif pos >= 2 and chain[pos - 2][0] != "..":
                step = chain[pos - 2][0]
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            else:
                unreachable.extend("{} (the sweep cannot climb back out of consecutive "
                                   "dot-dot steps to reach it)".format(inner)
                                   for inner in rest)
                break
            nxt_on = above
            try:
                nxt_fd = os.open(step, flags, dir_fd=cur_fd)
            except OSError as exc:
                unreachable.extend("{} (the sweep cannot climb back to the directory "
                                   "holding it: {})".format(inner, exc) for inner in rest)
                break
            try:
                up_st = os.fstat(nxt_fd)
            except OSError as exc:
                unreachable.extend("{} (the directory the sweep climbed back to cannot be "
                                   "inspected: {})".format(inner, exc) for inner in rest)
                break
            if (up_st.st_dev, up_st.st_ino) != expect:
                unreachable.extend("{} (a directory the sweep descended through was moved "
                                   "while the sweep ran, so the component can no longer "
                                   "be reached)".format(inner) for inner in rest)
                break
            # the handoff: both slots are re-bound BEFORE the old number is closed
            prev, prev_on = cur_fd, cur_on
            cur_fd, cur_on, nxt_fd, nxt_on = nxt_fd, nxt_on, None, None
            _swept_closed(prev, prev_on, close_failures)
            pos -= 1
    finally:
        # each slot still held, cleared before its close; the nested finally attempts the
        # second slot even when the first slot's close raises any exception class
        try:
            if nxt_fd is not None:
                prev, nxt_fd = nxt_fd, None
                _swept_closed(prev, nxt_on, close_failures)
        finally:
            if cur_fd is not None:
                prev, cur_fd = cur_fd, None
                _swept_closed(prev, cur_on, close_failures)
    return left, skipped, unreachable, close_failures


def _dirs_created(parent):
    """Create the register directory PARENT's missing components and return the components
    this call OBSERVED as its own, innermost first, each as (path, st_dev, st_ino). Each
    component is created with os.mkdir RELATIVE to an open descriptor on its parent directory
    and its identity is read back with a no-follow os.stat relative to that SAME descriptor,
    never by re-resolving the string path, so an outer component swapped aside after its
    creation cannot substitute another tree's entry for the identity read-back. The deepest
    EXISTING ancestor is opened O_RDONLY (a parent that is searchable but not readable
    refuses here rather than creating blind) and the walk descends O_NOFOLLOW, one component
    at a time. The absence walk inspects each ancestor through the shared entry_state (a
    CONFIRMED ENOENT is the only absence), so an UNINSPECTABLE ancestor refuses BEFORE any
    mkdir runs. A
    component that appears between the walk and its mkdir (FileExistsError) is another
    actor's directory: used for the descent, never recorded. DISCLOSED RESIDUAL: POSIX offers
    no race-free proof that the directory found at a name is the one mkdir just made, so the
    entry the no-follow stat finds at the created name is recorded as this call's own; a
    directory moved into that name between the mkdir and the stat is therefore recorded, and
    the later sweep's exact bound is stated once, at _made_dirs_swept (abbreviated: rmdir
    of an EMPTY directory only, within the two disclosed windows, inode reuse included;
    never a non-empty directory and never a file). Raises
    CannotEvaluate, with the already-recorded components swept and the sweep's outcome named,
    when the existing ancestor or a component cannot be opened, a component cannot be
    created, its identity cannot be read back, or a descriptor this walk opened cannot be
    closed (a failing close is wrapped, never a raw OSError; on the unwind path the
    in-flight refusal wins and a secondary close failure is suppressed)."""
    missing = []
    anc = parent
    while anc != anc.parent:
        if entry_state(anc) is not None:
            break
        missing.append(anc)
        anc = anc.parent
    made = []
    if not missing:
        return made
    try:
        dir_fd = os.open(str(missing[-1].parent), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise CannotEvaluate("the existing ancestor {} cannot be opened to create the "
                             "register directory under it ({})".format(
                                 missing[-1].parent, exc))
    try:
        for comp in reversed(missing):
            try:
                os.mkdir(comp.name, dir_fd=dir_fd)
            except FileExistsError:
                pass  # another actor's directory: used for the descent, never recorded
            except OSError as exc:
                raise CannotEvaluate("{}{}".format(exc, _made_dirs_note(made)))
            else:
                try:
                    comp_st = os.stat(comp.name, dir_fd=dir_fd, follow_symlinks=False)
                except OSError as exc:
                    raise CannotEvaluate("the created directory component {} cannot be read "
                                         "back ({}) and was left in place{}".format(
                                             comp, exc, _made_dirs_note(made)))
                made.insert(0, (comp, comp_st.st_dev, comp_st.st_ino))
            if comp != missing[0]:
                try:
                    nxt = os.open(comp.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=dir_fd)
                except OSError as exc:
                    raise CannotEvaluate("the directory component {} cannot be opened to "
                                         "create its children ({}){}".format(
                                             comp, exc, _made_dirs_note(made)))
                prev, dir_fd = dir_fd, nxt  # held first: a raising close cannot strand nxt
                try:
                    os.close(prev)
                except OSError as exc:
                    raise CannotEvaluate("the descriptor this walk held on {}'s parent "
                                         "could not be closed ({}){}".format(
                                             comp, exc, _made_dirs_note(made)))
    except BaseException:
        try:
            os.close(dir_fd)
        except OSError:
            pass  # the in-flight refusal names its own step; never a raw OSError over it
        raise
    try:
        os.close(dir_fd)
    except OSError as exc:
        raise CannotEvaluate("the descriptor this walk held on the created register "
                             "directory could not be closed ({}){}".format(
                                 exc, _made_dirs_note(made)))
    return made


def _swept_note(outcome, all_removed):
    """The refusal-message clauses, each led by "; ", for one sweep OUTCOME (left, skipped,
    unreachable, close_failures), following the sweep's three-valued report: every
    component left in place and every UNREACHABLE component is named, each in its own
    clause (an unreachable one was never inspected, so it is never said to be gone);
    every component found ABSENT is named as absent AT ITS OWN NAME, never as gone (its
    own lookup proves only that: it may have been renamed away and survive elsewhere), so
    "no directory component this call made remains at its own name" is said only when
    nothing was left in place or unreachable; ALL_REMOVED when every one was removed;
    and, KEPT APART from the removal outcomes, every descriptor the sweep could not close
    (never rendered as a directory left in place; claims follow the sweep, never the
    plan)."""
    left, skipped, unreachable, close_failures = outcome
    if left or unreachable:
        note = ""
        if left:
            note += ("; directory component(s) this call made could not be removed and "
                     "were left in place: {}".format(", ".join(left)))
        if unreachable:
            note += ("; directory component(s) this call made could not be reached by the "
                     "sweep, so they were never inspected and may remain, at their own "
                     "name or wherever a moved parent took them: {}".format(
                         ", ".join(unreachable)))
        if skipped:
            note += ("; directory component(s) this call made were found absent at their "
                     "own name, so possibly renamed away rather than gone: {}".format(
                         ", ".join(skipped)))
    elif skipped:
        note = ("; no directory component this call made remains at its own name (found "
                "absent there, so possibly renamed away rather than gone: {})".format(
                    ", ".join(skipped)))
    else:
        note = all_removed
    if close_failures:
        note += ("; descriptor close failure(s) in the sweep, not a directory left in "
                 "place: {}".format("; ".join(close_failures)))
    return note


def _made_dirs_note(made_dirs):
    """The refusal-message clause for the directory sweep: empty when nothing was planned,
    else the actual outcome of the sweep, rendered by _swept_note."""
    if not made_dirs:
        return ""
    return _swept_note(_made_dirs_swept(made_dirs),
                       "; the directory components this call made were removed")


def _creation_cleanup(fd, path, made_dirs):
    """After a refusal taken UNDER the acquired lock that follows the exclusive first-row
    creation (a lock that cannot be acquired never reaches this helper: removal without the
    lock could race a cooperating appender, so that path leaves the file): remove the created file,
    but only while it is still this call's own, still-empty file (the path still names the
    created device and inode, size zero), then the directory components this call itself created
    (innermost first, each only while empty; a component found absent at its own name does
    not stop the sweep). Returns the sentence for the refusal message; an entry this helper
    cannot prove safe to remove is left in place and NAMED, the made directory components
    included, a made component the sweep could not reach is named unreachable and one found
    absent is named absent at its own name (neither is ever said to be gone), a
    descriptor-close failure is reported as a close failure, never as a directory left in
    place, and the register gate stays the authority over whatever remains."""
    note = None
    try:
        st_fd = os.fstat(fd)
        st_path = os.lstat(str(path))
        if (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
            note = ("the entry now at the register path was left in place (the path no "
                    "longer names the created file)")
        elif st_fd.st_size != 0:
            note = "the created register was left in place (it is no longer empty)"
        else:
            os.unlink(str(path))
    except FileNotFoundError:
        note = ("the created register was no longer at the register path at this cleanup "
                "(found absent there, so possibly renamed away rather than gone; nothing "
                "remained to remove at that path)")
    except OSError as exc:
        note = "the created register could not be removed ({})".format(exc)
    if note is None:
        outcome = _made_dirs_swept(made_dirs)
        lead = ("the created register was removed" if any(outcome[:3]) else
                "the created register and the directory components this call made were "
                "removed")
        return lead + _swept_note(outcome, "")
    return note + _made_dirs_note(made_dirs)


def cmd_append(a, _waits=0):
    path = Path(a.register)
    try:
        root = _gate_root()
    except CannotEvaluate as exc:
        print("append: refused, cannot establish the register gate's context ({}); nothing "
              "written".format(exc), file=sys.stderr)
        return 2
    if root is None:
        print("append: refused, this appender is not installed inside a git repository, so "
              "the register gate's context cannot be established (the gate root is resolved "
              "from the appender's own location, never from the invocation directory); "
              "nothing written", file=sys.stderr)
        return 2
    if not confirmed_root(root):
        print("append: refused, the gate's validation root is not a git repository git itself "
              "confirms (rev-parse --show-toplevel must name it; an invalid gitfile or an "
              "empty .git directory is a repository marker, not a repository), so the register "
              "gate's context cannot be established and neither its registry nor any register "
              "it declares, an external register included, is trusted; nothing written",
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
    tmp_fd = -1
    created = False
    made_dirs = []
    landed_seq = None
    retried = False
    escaping = False
    try:
        try:
            # read-write though the commit never writes through it, so a register this caller
            # may not write is refused here rather than replaced by the commit's rename; the
            # open is non-blocking so no file type can make the open itself wait, and the
            # file-type check on the opened descriptor below decides what was opened
            fd = os.open(str(path), os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            fd = -1
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                print("append: refused, the register path is a symlink; the append binds the "
                      "validated text to one file identity, so an aliased path is never "
                      "followed; nothing written", file=sys.stderr)
                return 2
            if exc.errno == errno.EISDIR:
                print("append: refused, the register path names a directory, not a regular "
                      "file; nothing written", file=sys.stderr)
                return 2
            if exc.errno == errno.ENXIO:
                # on Linux this open cannot address a socket (or a device node no driver
                # backs) at all: ENXIO arrives before any descriptor exists, so the refusal
                # names the type from the path's own lstat, a point-in-time read taken only
                # for the message, never as a proof about an opened descriptor
                try:
                    mode = os.lstat(str(path)).st_mode
                except OSError:
                    mode = None
                if mode is not None and not stat.S_ISREG(mode):
                    print("append: refused, the register path names {}, not a regular file "
                          "(the non-blocking open cannot address it, so it was never "
                          "opened, locked or read); nothing written".format(
                              _nonregular_name(mode)), file=sys.stderr)
                    return 2
            print("append: refused, the register cannot be read ({}); nothing written".format(
                exc), file=sys.stderr)
            return 2
        if fd >= 0:
            # Refuse a non-regular register BEFORE locking or reading: on Linux a read-write
            # open of a FIFO succeeds and a read through that descriptor can wait forever
            # (the descriptor itself holds the write end), so only the opened descriptor's
            # own file type decides.
            try:
                st_opened = os.fstat(fd)
            except OSError as exc:
                print("append: refused, the register cannot be inspected through its open "
                      "descriptor ({}); nothing written".format(exc), file=sys.stderr)
                return 2
            if not stat.S_ISREG(st_opened.st_mode):
                print("append: refused, the register path names {}, not a regular file; "
                      "nothing written".format(_nonregular_name(st_opened.st_mode)),
                      file=sys.stderr)
                return 2
            # the descriptor was opened non-blocking only so the open and the check above
            # could not wait; the proven-regular file now reads with normal semantics
            try:
                fcntl.fcntl(fd, fcntl.F_SETFL,
                            fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            except OSError as exc:
                print("append: refused, the register descriptor's non-blocking flag cannot "
                      "be cleared ({}); nothing written".format(exc), file=sys.stderr)
                return 2
            # Serialize cooperating appenders: the exclusive lock is held from before the read
            # until the commit lands (or the refusal returns), so two appends through this tool
            # cannot interleave between the read, the validation and the commit.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                print("append: refused, cannot lock the register ({}); nothing written".format(
                    exc), file=sys.stderr)
                return 2
            # A cooperating appender that committed while this call waited for the lock put a
            # new file at the path (its commit is a rename), so the lock just acquired is on
            # the replaced file: start over on the file the path names now (this descriptor
            # stays open, and its stale lock held, until this call returns).
            try:
                st_locked = os.fstat(fd)
                st_named = os.lstat(str(path))
            except FileNotFoundError:
                st_named = None
            except OSError as exc:
                print("append: refused, the register cannot be re-checked after taking its lock "
                      "({}); nothing written".format(exc), file=sys.stderr)
                return 2
            if st_named is None or (st_locked.st_dev, st_locked.st_ino) != \
                    (st_named.st_dev, st_named.st_ino):
                if _waits < LOCK_WAITS:
                    retried = True
                    return cmd_append(a, _waits + 1)
                print("append: refused, the register was replaced {} times while this call "
                      "waited for its lock; nothing written".format(_waits + 1),
                      file=sys.stderr)
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
        if a.bootstrap and lines:
            print("append: refused, --bootstrap appends only a register's first row and this "
                  "register already holds {} line(s); establish its anchor with "
                  "tools/check_mistakes_register.py --update-anchor, then append without "
                  "--bootstrap; nothing written".format(len(lines)), file=sys.stderr)
            return 2
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
        # containing repository root resolved above. An ordinary append requires the anchor
        # exactly as the gate without --update-anchor does; only --bootstrap, a first row,
        # takes the --update-anchor allowance for an untracked register with no anchor.
        after = (text + line + "\n").splitlines()
        try:
            problems = verify_lines(after, root=root, prefix=family + "-")
        except CannotEvaluate as exc:
            print("append: refused, cannot evaluate the register's rows ({}); nothing "
                  "written".format(exc), file=sys.stderr)
            return 2
        try:
            problems += verify_append_only(root, path, after,
                                           allow_missing_anchor=a.bootstrap)
        except CannotEvaluate as exc:
            hint = ("" if a.bootstrap or lines else "; if this is the first row of an untracked "
                    "register with no anchor yet, append it with --bootstrap, which establishes "
                    "the anchor")
            print("append: refused, cannot evaluate the register's append-only history ({}); "
                  "nothing written{}".format(exc, hint), file=sys.stderr)
            return 2
        if problems:
            print("append: refused, the register with this row fails the register gate "
                  "({} violation(s)); nothing written:".format(len(problems)), file=sys.stderr)
            for p in problems:
                print("  " + p, file=sys.stderr)
            return 1
        content = bytes(raw) + (line + "\n").encode("utf-8")
        if fd < 0:
            # First row: the register was absent at the read, so create it exclusively; a file
            # that appeared since is never appended to blindly (O_EXCL refuses it, symlinks
            # included). A row refused by the validation never reaches this point, so its
            # directory stays absent; the directory components this call creates are
            # recorded by _dirs_created (identity read back relative to the parent descriptor
            # after each successful mkdir; an uninspectable ancestor refuses before any
            # mkdir) and swept BY DESCRIPTOR on a later refusal, so such a refusal removes
            # only what this call observed as its own, within the sweep's exact bound,
            # stated once at _made_dirs_swept (abbreviated: rmdir of an EMPTY directory
            # only, within the two disclosed windows, inode reuse included; never a
            # non-empty directory and never a file).
            try:
                made_dirs = _dirs_created(path.parent)
            except CannotEvaluate as exc:
                print("append: refused, the register's directory could not be created ({}); "
                      "nothing written".format(exc), file=sys.stderr)
                return 2
            try:
                fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
            except OSError as exc:
                dirs_note = _made_dirs_note(made_dirs)
                print("append: refused, the register could not be created exclusively ({}); a "
                      "file that appeared between the validation and the write is never "
                      "appended to blindly; nothing written by this call{}".format(
                          exc, dirs_note), file=sys.stderr)
                return 2
            created = True
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                print("append: refused, cannot lock the register ({}); nothing was written to "
                      "it; the created empty register was left in place (removing it without "
                      "the lock could race a cooperating appender that already opened this "
                      "path); run tools/check_mistakes_register.py before trusting this "
                      "file".format(exc), file=sys.stderr)
                return 2
        # The commit is all or nothing: the validated bytes plus the new row go to a temporary
        # file created exclusively in the register's own directory under the lock, which is
        # synced and then renamed over the register, still under the lock, only after the
        # re-check that the path still names the validated file (or, for a first row, the file
        # this call just created) with exactly the validated bytes, and that the temporary's
        # name still names the temporary (_commit_failure). The register itself is never
        # written, so any failure before the rename leaves its bytes as they were; the
        # temporary is then removed. For a freshly created register the
        # re-check is also the serialization check, since its lock is necessarily taken after
        # the creation: an appender that landed in between forces a refusal, never an
        # interleaved row. Only the interleavings the identity, link-count, size and byte
        # comparisons detect are caught; other validation inputs (the anchor included) are not
        # revalidated under the lock.
        tmp_path = "{}.tmp.{}.{}".format(path, os.getpid(), secrets.token_hex(4))
        try:
            tmp_fd = os.open(tmp_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600)
        except OSError as exc:
            msg = ("append: refused, the temporary file for the commit could not be created "
                   "exclusively in the register's directory ({}); nothing was written to the "
                   "register".format(exc))
            if created:
                msg += "; " + _creation_cleanup(fd, path, made_dirs)
            print(msg, file=sys.stderr)
            return 2
        failure = _commit_failure(fd, path, raw, tmp_fd, tmp_path, content)
        if failure is not None:
            msg = "append: refused, {}; nothing was written to the register; {}".format(
                failure, _temp_removed(tmp_fd, tmp_path))
            if created:
                msg += "; " + _creation_cleanup(fd, path, made_dirs)
            print(msg, file=sys.stderr)
            return 2
        # The rename has landed: the row is in the register. What follows cannot undo it, so
        # a failure here exits 2 saying the row is in place.
        landed_seq = seq
        notes = []
        sync_note = _dir_synced(path.parent)
        if sync_note is not None:
            notes.append(sync_note + ", so the rename may not survive a crash")
        if a.bootstrap:
            try:
                update_anchor(path, after)
            except CannotEvaluate as exc:
                notes.append("the anchor could not be established ({}); run "
                             "tools/check_mistakes_register.py --update-anchor to verify the "
                             "register and establish it".format(exc))
        if notes:
            print("append: {} seq {} was appended and is in the register, but {}".format(
                a.id, seq, "; ".join(notes)), file=sys.stderr)
            return 2
        if a.bootstrap:
            print("appended {} seq {}; anchor established at seq {}".format(a.id, seq, seq))
        else:
            print("appended {} seq {}".format(a.id, seq))
        return 0
    except BaseException:
        # whether an exception is escaping THIS append is tracked explicitly: ambient
        # exception state (sys.exc_info) would also see an exception a CALLER is already
        # handling around this call, and the close failure below would then print its
        # report but return the body's own verdict (success included) instead of exit 2
        escaping = True
        raise
    finally:
        # A close failure here is NAMED on stderr and the call exits 2, never an escaped
        # OSError: each owned descriptor's close is attempted exactly once (Linux
        # releases the number even when close reports a failure), the failure is reported
        # beside whatever verdict was in flight, and only when no exception is escaping
        # this append's own body (tracked above, never read from ambient state) does the
        # close failure replace the verdict. A row this call landed was already written
        # and fsynced before this point, so the message then says the row is in the
        # register; only an append that landed nothing is called refused.
        close_failures = []
        if tmp_fd >= 0:
            try:
                os.close(tmp_fd)
            except OSError as exc:
                close_failures.append("the commit temporary's descriptor could not be "
                                      "closed ({})".format(exc))
        if fd >= 0:
            try:
                os.close(fd)
            except OSError as exc:
                close_failures.append("the register descriptor could not be closed "
                                      "({})".format(exc))
        if close_failures:
            if landed_seq is not None:
                print("append: {} seq {} was appended and is in the register, but {}".format(
                    a.id, landed_seq, "; ".join(close_failures)), file=sys.stderr)
            elif retried:
                # the retried append's verdict is only ABOVE this line when that call
                # RETURNED; a retried call that raised (the next-id refusal included) is
                # still unwinding through this finally, so its verdict is reported after
                # this line, wherever that exception is handled
                print("append: {} (the descriptor belonged to this call's superseded "
                      "lock-wait attempt; {})".format(
                          "; ".join(close_failures),
                          "the retried append raised instead of returning, so its "
                          "verdict is carried by that still unwinding exception and "
                          "is reported after this line" if escaping else
                          "the retried append reported its own verdict above"),
                      file=sys.stderr)
            else:
                print("append: refused, {}".format("; ".join(close_failures)),
                      file=sys.stderr)
            if not escaping:
                raise SystemExit(2)


def cmd_project(a):
    latest = {}
    try:
        reg_lines = _lines(Path(a.register))
    except CannotEvaluate as exc:
        print("project: refused, {}; the projection reads a regular file only, and its open "
              "never blocks on the file type".format(exc), file=sys.stderr)
        return 2
    # Rows the projection cannot parse or shape are refused like its read failures (exit 2
    # on stderr), never an escaped traceback and never a partial enumeration: a non-object
    # row has no fields to project, a non-string id would make the sorted enumeration
    # below compare incomparable keys, and a line nested too deeply for the JSON decoder
    # (which raises RecursionError, not ValueError) is a parse failure like any other.
    for line in reg_lines:
        try:
            row = json.loads(line)
        except RecursionError:
            print("project: refused, a register line is nested too deeply for the JSON "
                  "decoder (run check_mistakes_register.py); the projection never guesses "
                  "at a row it cannot parse", file=sys.stderr)
            return 2
        except ValueError:
            print("project: refused, a register line is not JSON (run "
                  "check_mistakes_register.py); the projection never guesses at a row it "
                  "cannot parse", file=sys.stderr)
            return 2
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            print("project: refused, a register row is not a JSON object with a string id "
                  "(run check_mistakes_register.py); the projection never guesses at a row "
                  "it cannot shape", file=sys.stderr)
            return 2
        latest[row["id"]] = row
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        # formatting a row field walks its decoded value, and the decoder accepts more
        # nesting than str/repr can walk on the same stack: a value that deep is the same
        # named refusal as any other row the projection cannot shape, never an escaped
        # RecursionError and never a partial enumeration (nothing is printed on a refusal)
        items = [{"id": "{}-guardrail".format(rid), "title": "land the guardrail: {}".format(
            row.get("guardrail", "")), "state": "open", "granted": row["status"] == "accepted"}
            for rid, row in sorted(latest.items())
            if row.get("status") in ("proposed", "accepted")]
        for it in items:  # a proposed row enumerates but does not compel (express authorization)
            if not it["granted"]:
                it["state"] = "proposed"
    except RecursionError:
        print("project: refused, a register row is nested too deeply to format for the "
              "enumeration (run check_mistakes_register.py); the projection never guesses "
              "at a row it cannot shape", file=sys.stderr)
        return 2
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
    import socket
    import subprocess
    import tempfile
    # Hermetic git fixtures (test-hermeticity): an inherited git environment would redirect the
    # fixture's init/add/commit, and the gate's own git reads, into the CALLER's repository.
    from _git_fixture_env import scrub_git_environment
    from check_mistakes_register import update_anchor
    scrub_git_environment()
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-orch-register-"))
    failures = []
    # The appender resolves the register gate's own root (production: _gate_root(), the
    # nearest .git entry above the appender's own installed location, git-confirmed by
    # confirmed_root and with NO cwd fallback);
    # each fixture stands in its own repository, so the resolution is routed at the fixture
    # root under test and restored in the finally.
    gate_root = {"root": None}
    real_gate_root = _gate_root
    globals()["_gate_root"] = lambda: gate_root["root"]

    def append(reg, rid, status="proposed", **extra):
        fields = {"register": str(reg), "id": rid, "mistake": "m", "evidence": "e",
                  "rule": "mstreg", "guardrail": "g", "status": status, "commit": None,
                  "check_ref": None, "prefix": "MR", "klass": None, "ref": None,
                  "bootstrap": False}
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
            st = entry_state(p)  # a test absence read must never misread an inspection error
            if st is None:
                return None
            if stat.S_ISLNK(st.st_mode):
                return ("symlink", os.readlink(str(p)))
            if stat.S_ISDIR(st.st_mode):
                return ("dir", tuple(sorted(os.listdir(str(p)))))
            return Path(str(p)).read_bytes()
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
        if temps_beside(reg):
            failures.append("{}: a refused append left a temporary file: {}".format(
                name, temps_beside(reg)))

    def temps_beside(reg):
        # the commit's temporary files (<register>.tmp.<pid>.<hex>) in the register's directory
        try:
            names = os.listdir(str(Path(str(reg)).parent))
        except FileNotFoundError:
            return []
        return sorted(n for n in names if n.startswith(Path(str(reg)).name + ".tmp."))

    def vector_cleanup(name, step):
        # a red vector's own cleanup can fail under a mutant; record that failure and let
        # the later vectors run instead of stopping at the shared fixture handler
        try:
            step()
        except OSError as exc:
            failures.append("{}: the vector's own cleanup failed ({})".format(name, exc))

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

        # an untracked register with no anchor yet: its first row is the explicit --bootstrap,
        # which establishes the anchor every later ordinary append requires
        declare(mistakes_register="reg.jsonl")
        reg = repo / "reg.jsonl"
        accepted("first-proposal", reg, "MR-1", bootstrap=True)
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
        # an ORDINARY append requires the anchor exactly as the gate without --update-anchor
        # does: on an untracked register with no anchor it is refused with exit 2 naming the
        # anchor, as a first row (the register stays absent) and on a register that already
        # holds rows but was never anchored; --bootstrap appends only a first row, so on the
        # latter it is refused too, naming the gate's --update-anchor
        declare(mistakes_register="noanchor.jsonl")
        noanchor = repo / "noanchor.jsonl"
        refused("ordinary-first-row-without-anchor-refused", noanchor, 2, "has no anchor",
                "MR-1")
        if entry_state(noanchor) is not None:
            failures.append("ordinary-first-row-without-anchor-refused: the refusal created "
                            "the register")
        noanchor.write_text(_lines(reg)[0] + "\n", encoding="utf-8")
        refused("ordinary-append-without-anchor-refused", noanchor, 2, "has no anchor",
                "MR-1", "accepted")
        refused("bootstrap-on-existing-rows-refused", noanchor, 2, "--update-anchor", "MR-1",
                "accepted", bootstrap=True)
        # --bootstrap lands the first row AND establishes the anchor --update-anchor would
        # write, so the ORDINARY gate (no --update-anchor) passes on the result, and passes
        # again after a following ordinary append
        from check_mistakes_register import run as gate_run
        declare(mistakes_register="boot.jsonl")
        boot = repo / "boot.jsonl"
        accepted("bootstrap-first-row-lands", boot, "MR-1", bootstrap=True)
        boot_lines = _lines(boot)
        want_anchor = {"seq": 1, "digest": hashlib.sha256(
            boot_lines[0].encode("utf-8")).hexdigest()} if boot_lines else None
        try:
            got_anchor = json.loads(Path(str(boot) + ".anchor").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            got_anchor = "unreadable ({})".format(exc)
        if got_anchor != want_anchor:
            failures.append("bootstrap-establishes-anchor: want the anchor --update-anchor "
                            "would write, {!r}, got {!r}".format(want_anchor, got_anchor))

        def ordinary_gate():
            gate_out = io.StringIO()
            with contextlib.redirect_stdout(gate_out):
                gate_rc = gate_run(repo)
            return gate_rc, gate_out.getvalue().strip()

        boot_gate = ordinary_gate()
        if boot_gate[0] != 0:
            failures.append("bootstrap-then-gate-passes: the ordinary gate should pass on the "
                            "bootstrapped register, got exit {}: {}".format(*boot_gate))
        accepted("ordinary-after-bootstrap-lands", boot, "MR-1", "accepted")
        boot_gate = ordinary_gate()
        if boot_gate[0] != 0:
            failures.append("ordinary-append-then-gate-passes: the ordinary gate should pass "
                            "after an ordinary append, got exit {}: {}".format(*boot_gate))
        # an anchor write that fails AFTER the bootstrap's rename cannot undo the landed row:
        # exit 2 saying the row is in the register and naming the gate's --update-anchor
        declare(mistakes_register="bootfail.jsonl")
        bootfail = repo / "bootfail.jsonl"

        def failing_update_anchor(upath, ulines):
            raise CannotEvaluate("injected anchor failure")

        saved_update_anchor = globals().get("update_anchor")
        globals()["update_anchor"] = failing_update_anchor
        try:
            rc, err = append(bootfail, "MR-1", bootstrap=True)
        finally:
            globals()["update_anchor"] = saved_update_anchor
        if (rc != 2 or "was appended and is in the register" not in err
                or "injected anchor failure" not in err or "--update-anchor" not in err
                or len(_lines(bootfail)) != 1):
            failures.append("bootstrap-anchor-failure-named: want exit 2 saying the row landed "
                            "and naming --update-anchor, got exit {}: {}".format(
                                rc, err.strip()))
        # a failed or short write must not leave a partial row: under RLIMIT_FSIZE the kernel
        # cuts the temporary file's write short, so the commit refuses before its rename and
        # the register keeps its bytes
        declare(mistakes_register="reg.jsonl")
        limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (reg.stat().st_size + 20, limit[1]))
            refused("failed-write-leaves-no-partial-row", reg, 2,
                    "nothing was written to the register", "MR-3")
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, limit)
            signal.signal(signal.SIGXFSZ, handler)
        accepted("append-after-rollback-lands", reg, "MR-3")
        # the serialization lock is held across the validation: a second flock taken during
        # verify_append_only must be refused while the append holds the register
        lockp = repo / "lockprobe.jsonl"
        declare(mistakes_register="lockprobe.jsonl")
        accepted("lock-seed", lockp, "MR-1", bootstrap=True)
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
        accepted("swap-seed", swap, "MR-1", bootstrap=True)
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
        if rc != 2 or "replaced between the validation and the commit" not in err:
            failures.append("replaced-register-refused: want exit 2 naming the replacement, got "
                            "exit {}: {}".format(rc, err.strip()))
        if swap.read_text(encoding="utf-8") != swap_seed:
            failures.append("replaced-register-refused: the replacement file received the row")
        if displaced != swap_seed.encode("utf-8"):
            failures.append("replaced-register-refused: the displaced file received the row")
        # an attestation register validates under its own id family, which the registry declares
        declare(mistakes_register="reg.jsonl", attestations="attest.jsonl")
        accepted("attestation-prefix-appends", repo / "attest.jsonl", "AT-1", prefix="AT",
                 bootstrap=True)
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
        accepted("fused-seed", fused, "MR-1", bootstrap=True)
        fused.write_bytes(fused.read_bytes().rstrip(b"\n"))
        refused("missing-final-newline-refused", fused, 1, "not a JSON object", "MR-1",
                "accepted")
        # the append-only leg: an anchored row rewritten in place (seq and chain still valid, so
        # verify_lines alone reads clean) is refused through verify_append_only
        anch = repo / "anchored.jsonl"
        declare(mistakes_register="anchored.jsonl")
        accepted("anchored-seed", anch, "MR-1", bootstrap=True)
        update_anchor(anch, anch.read_text(encoding="utf-8").splitlines())
        old = json.loads(anch.read_text(encoding="utf-8"))
        old["mistake"] = "rewritten"
        anch.write_text(json.dumps(old, sort_keys=True) + "\n", encoding="utf-8")
        refused("anchored-rewrite-refused", anch, 1, "the anchored row was rewritten", "MR-2")
        # a malformed or unreadable anchor is cannot-evaluate (exit 2), never a bypassed digest
        anchbad = repo / "anchbad.jsonl"
        declare(mistakes_register="anchbad.jsonl")
        accepted("anchor-cases-seed", anchbad, "MR-1", bootstrap=True)
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
        accepted("symlink-target-seed", target, "MR-1", bootstrap=True)
        link = repo / "link.jsonl"
        link.symlink_to("target.jsonl")
        declare(mistakes_register="link.jsonl")
        refused("symlinked-register-refused", link, 2, "symlink", "MR-2")

        # ---- a register that is not a regular file is refused with exit 2 BEFORE locking or
        # reading: on Linux a read-write open of a FIFO succeeds and a read through that
        # descriptor can wait forever (the descriptor itself holds the write end), and a
        # read-only open of a FIFO with no writer waits in the open itself, so both the
        # appender and the projection open non-blocking and refuse on the opened
        # descriptor's file type. Every vector here runs under an alarm, so a regression
        # that blocks fails instead of hanging the suite.
        class VectorAlarm(Exception):
            # deliberately NOT an OSError: the appender's own read loop and the projection
            # catch OSError, and the alarm must escape both to fail the vector
            pass

        # The bound exists only to turn a blocking regression into a failure instead of a
        # hung suite: a step that exceeds the configured seconds (default 60) records a
        # failure, so the alarm can fail a vector only by firing, never pass one, and a
        # regression that blocks still fails within the bound. A loaded host can therefore
        # at worst cause a false failure, never a false pass.
        def alarm_bounded(name, step, seconds=60):
            def on_alarm(signum, frame):
                raise VectorAlarm("blocked for {} s".format(seconds))
            old_handler = signal.signal(signal.SIGALRM, on_alarm)
            signal.alarm(seconds)
            try:
                return step()
            except VectorAlarm as exc:
                failures.append("{}: the call blocked instead of refusing ({})".format(
                    name, exc))
                return None
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

        def project_rc(regpath):
            # stdout is kept (third element) so the regular-register vector can assert the
            # projected payload itself, not only the exit code; an escaped OSError,
            # ValueError, RecursionError or SystemExit becomes a non-integer rc, which
            # fails every rc assertion below, so an escape is always red, never a crashed
            # suite
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    rc = cmd_project(argparse.Namespace(register=str(regpath)))
                except SystemExit as exc:
                    rc = "an uncaught SystemExit: {!r}".format(exc.code)
                except (OSError, ValueError, RecursionError) as exc:
                    rc = "an uncaught {}: {}".format(type(exc).__name__, exc)
            return rc, err.getvalue(), out.getvalue()

        def want_refusal(name, got, naming):
            if got is None:
                return  # the alarm already recorded the failure
            rc, err = got[0], got[1]
            missing = [w for w in naming if w not in err]
            if rc != 2 or missing:
                failures.append("{}: want exit 2 naming {}, got exit {!r}: {}".format(
                    name, " and ".join(repr(w) for w in naming), rc, err.strip()))

        declare(mistakes_register="fifo.jsonl")
        fifo_reg = repo / "fifo.jsonl"
        os.mkfifo(str(fifo_reg))
        fifo_before = os.lstat(str(fifo_reg))
        want_refusal("fifo-register-refused",
                     alarm_bounded("fifo-register-refused", lambda: append(fifo_reg, "MR-1")),
                     ("FIFO", "not a regular file"))
        fifo_after = os.lstat(str(fifo_reg))
        if (not stat.S_ISFIFO(fifo_after.st_mode)
                or (fifo_before.st_dev, fifo_before.st_ino)
                != (fifo_after.st_dev, fifo_after.st_ino)):
            failures.append("fifo-register-refused: the refusal changed the register entry")
        if temps_beside(fifo_reg):
            failures.append("fifo-register-refused: the refusal left a temporary file: "
                            "{}".format(temps_beside(fifo_reg)))
        want_refusal("fifo-project-refused",
                     alarm_bounded("fifo-project-refused", lambda: project_rc(fifo_reg)),
                     ("FIFO", "not a regular file"))
        fifo_reg.unlink()
        # a close failure while the appender unwinds (here while it returns the FIFO
        # refusal) is a NAMED refusal (exit 2), never an escaped OSError: the injected
        # close really closes the descriptor exactly once, identified by its file
        # identity, and only then reports failure
        declare(mistakes_register="fifo-close.jsonl")
        fifo_close_reg = repo / "fifo-close.jsonl"
        os.mkfifo(str(fifo_close_reg))
        fifo_close_id = os.lstat(str(fifo_close_reg))
        close_fired = {"n": 0, "again": 0}
        app_close_failed = set()
        real_app_close, real_app_fstat = os.close, os.fstat

        def app_failing_close(close_fd):
            # a second close of a descriptor number whose close already failed is the
            # regression the "attempted exactly once" guarantee forbids (the number can
            # be reissued by then, so a retry can close an unrelated file): count it so
            # the vector can assert zero
            if close_fd in app_close_failed:
                close_fired["again"] += 1
            try:
                st_closed = real_app_fstat(close_fd)
            except OSError:
                st_closed = None
            real_app_close(close_fd)
            if st_closed is not None and (st_closed.st_dev, st_closed.st_ino) == \
                    (fifo_close_id.st_dev, fifo_close_id.st_ino):
                close_fired["n"] += 1
                app_close_failed.add(close_fd)
                raise OSError(5, "injected close failure")

        def close_fail_append():
            try:
                return append(fifo_close_reg, "MR-1")
            except OSError as exc:  # the pre-fix escape: the close's raw OSError
                return "an uncaught OSError: {}".format(exc), ""

        os.close = app_failing_close
        try:
            cf_got = alarm_bounded("append-close-failure-named", close_fail_append)
        finally:
            os.close = real_app_close
        if cf_got is not None:
            cf_rc, cf_err = cf_got
            if cf_rc != 2 or "could not be closed" not in cf_err \
                    or "not a regular file" not in cf_err:
                failures.append("append-close-failure-named: want exit 2 naming the close "
                                "failure beside the refusal, got exit {!r}: {}".format(
                                    cf_rc, cf_err.strip()))
            if close_fired["n"] == 0:
                failures.append("append-close-failure-named: the injected close failure "
                                "never fired, so the vector proved nothing")
        if close_fired["again"]:
            failures.append("append-close-failure-named: the failed close was attempted "
                            "again ({} extra attempt(s)); each owned descriptor's close "
                            "is attempted exactly once".format(close_fired["again"]))
        if not stat.S_ISFIFO(os.lstat(str(fifo_close_reg)).st_mode):
            failures.append("append-close-failure-named: the refusal changed the register "
                            "entry")
        fifo_close_reg.unlink()
        # a close failure AFTER a row landed: the row was written and fsynced before the
        # close, so the exit is 2 but the message says the row is in the register, never
        # that the append was refused; the injection fires on the descriptor that holds
        # the renamed commit temporary (identified as the file now at the register path),
        # so the temporary's own close wrapper is the one exercised
        real_lc_close, real_lc_fstat, real_lc_lstat = os.close, os.fstat, os.lstat

        def landed_close_injector(target_reg, fired):
            failed_fds = set()

            def failing_close(close_fd):
                # a second close of a descriptor number whose close already failed is
                # the regression the "attempted exactly once" guarantee forbids (the
                # number can be reissued by then, so a retry can close an unrelated
                # file): count it so each vector can assert zero
                if close_fd in failed_fds:
                    fired["again"] += 1
                try:
                    st_closed = real_lc_fstat(close_fd)
                except OSError:
                    st_closed = None
                real_lc_close(close_fd)
                if st_closed is None:
                    return
                try:
                    st_reg = real_lc_lstat(str(target_reg))
                except OSError:
                    return
                if (st_closed.st_dev, st_closed.st_ino) == \
                        (st_reg.st_dev, st_reg.st_ino):
                    fired["n"] += 1
                    failed_fds.add(close_fd)
                    raise OSError(5, "injected close failure")
            return failing_close

        declare(mistakes_register="landed-close.jsonl")
        landed_close_reg = repo / "landed-close.jsonl"
        landed_fired = {"n": 0, "again": 0}

        def landed_close_append():
            try:
                return append(landed_close_reg, "MR-1", bootstrap=True)
            except OSError as exc:
                return "an uncaught OSError: {}".format(exc), ""

        os.close = landed_close_injector(landed_close_reg, landed_fired)
        try:
            lc_got = alarm_bounded("landed-close-failure-named", landed_close_append)
        finally:
            os.close = real_lc_close
        if lc_got is not None:
            lc_rc, lc_err = lc_got
            if lc_rc != 2 or "was appended and is in the register" not in lc_err \
                    or "could not be closed" not in lc_err or "refused" in lc_err:
                failures.append("landed-close-failure-named: want exit 2 saying the landed "
                                "row is in the register beside the close failure (never "
                                "'refused'), got exit {!r}: {}".format(lc_rc, lc_err.strip()))
            if landed_fired["n"] == 0:
                failures.append("landed-close-failure-named: the injected close failure "
                                "never fired, so the vector proved nothing")
            if not _lines(landed_close_reg):
                failures.append("landed-close-failure-named: the landed row is not in the "
                                "register")
        if landed_fired["again"]:
            failures.append("landed-close-failure-named: the failed close was attempted "
                            "again ({} extra attempt(s)); each owned descriptor's close "
                            "is attempted exactly once".format(landed_fired["again"]))
        vector_cleanup("landed-close-failure-named", landed_close_reg.unlink)
        vector_cleanup("landed-close-failure-named",
                       Path(str(landed_close_reg) + ".anchor").unlink)
        # the same landed append plus close failure, invoked while the CALLER is inside
        # its own exception handler: the exit-2 decision must come from tracking this
        # append's own escaping exception, because ambient exception state sees the
        # caller's handled exception and would let the printed close failure return the
        # body's success
        declare(mistakes_register="landed-close-handler.jsonl")
        handler_reg = repo / "landed-close-handler.jsonl"
        handler_fired = {"n": 0, "again": 0}

        def handler_close_append():
            try:
                raise ValueError("a caller's handled exception")
            except ValueError:
                try:
                    return append(handler_reg, "MR-1", bootstrap=True)
                except OSError as exc:
                    return "an uncaught OSError: {}".format(exc), ""

        os.close = landed_close_injector(handler_reg, handler_fired)
        try:
            hc_got = alarm_bounded("landed-close-failure-in-handler", handler_close_append)
        finally:
            os.close = real_lc_close
        if hc_got is not None:
            hc_rc, hc_err = hc_got
            if hc_rc != 2 or "was appended and is in the register" not in hc_err \
                    or "could not be closed" not in hc_err:
                failures.append("landed-close-failure-in-handler: a close failure on a "
                                "landed append must exit 2 even inside a caller's handler, "
                                "got exit {!r}: {}".format(hc_rc, hc_err.strip()))
            if handler_fired["n"] == 0:
                failures.append("landed-close-failure-in-handler: the injected close "
                                "failure never fired, so the vector proved nothing")
        if handler_fired["again"]:
            failures.append("landed-close-failure-in-handler: the failed close was "
                            "attempted again ({} extra attempt(s)); each owned "
                            "descriptor's close is attempted exactly once".format(
                                handler_fired["again"]))
        vector_cleanup("landed-close-failure-in-handler", handler_reg.unlink)
        vector_cleanup("landed-close-failure-in-handler",
                       Path(str(handler_reg) + ".anchor").unlink)
        # a directory at the register path: the appender's read-write open refuses it with
        # the same named diagnostic, and the projection's file-type check refuses it too
        declare(mistakes_register="dirreg.jsonl")
        dir_reg = repo / "dirreg.jsonl"
        dir_reg.mkdir()
        want_refusal("directory-register-refused",
                     alarm_bounded("directory-register-refused",
                                   lambda: append(dir_reg, "MR-1")),
                     ("directory", "not a regular file"))
        if not dir_reg.is_dir() or list(dir_reg.iterdir()):
            failures.append("directory-register-refused: the refusal changed the directory")
        want_refusal("directory-project-refused",
                     alarm_bounded("directory-project-refused", lambda: project_rc(dir_reg)),
                     ("directory", "not a regular file"))
        dir_reg.rmdir()
        # a symlinked register path stays refused by name under the same bound whatever it
        # aliases (here a FIFO, so following it could block); the projection still follows
        # the path but refuses the aliased FIFO by the opened descriptor's file type
        declare(mistakes_register="fifolink.jsonl")
        fifo_target = repo / "fifolink-target"
        os.mkfifo(str(fifo_target))
        fifo_link = repo / "fifolink.jsonl"
        fifo_link.symlink_to("fifolink-target")
        want_refusal("symlink-register-refused-bounded",
                     alarm_bounded("symlink-register-refused-bounded",
                                   lambda: append(fifo_link, "MR-1")),
                     ("symlink",))
        want_refusal("symlink-to-fifo-project-refused",
                     alarm_bounded("symlink-to-fifo-project-refused",
                                   lambda: project_rc(fifo_link)),
                     ("FIFO", "not a regular file"))
        if (not fifo_link.is_symlink()
                or not stat.S_ISFIFO(os.lstat(str(fifo_target)).st_mode)):
            failures.append("symlink-to-fifo vectors: a refusal changed the register entry "
                            "or its target")
        fifo_link.unlink()
        fifo_target.unlink()
        # a Unix socket at the register path cannot be opened as a file at all (on Linux the
        # open fails with ENXIO before any descriptor exists), so both the appender and the
        # projection refuse it with the same diagnostic naming the file type, exit 2, never
        # a traceback and never the generic unreadable clause
        declare(mistakes_register="sock.jsonl")
        sock_reg = repo / "sock.jsonl"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        held_cwd = os.getcwd()
        os.chdir(str(repo))
        try:
            # bound by a relative name: a bound AF_UNIX path is limited to about 107 bytes,
            # and the fixture's temporary directory can be deeper than that
            sock.bind("sock.jsonl")
        finally:
            os.chdir(held_cwd)
        try:
            want_refusal("socket-register-refused",
                         alarm_bounded("socket-register-refused",
                                       lambda: append(sock_reg, "MR-1")),
                         ("socket", "not a regular file"))
            want_refusal("socket-project-refused",
                         alarm_bounded("socket-project-refused",
                                       lambda: project_rc(sock_reg)),
                         ("socket", "not a regular file"))
            # a symlink to the socket is followed, as projection reads always have been,
            # so the refusal names the TARGET's type (a socket), never the link's own
            # numeric mode
            sock_link = repo / "socklink.jsonl"
            sock_link.symlink_to("sock.jsonl")
            want_refusal("socket-symlink-project-named",
                         alarm_bounded("socket-symlink-project-named",
                                       lambda: project_rc(sock_link)),
                         ("socket", "not a regular file"))
            if not sock_link.is_symlink():
                failures.append("socket-symlink-project-named: the refusal changed the link")
            sock_link.unlink()
            if not stat.S_ISSOCK(os.lstat(str(sock_reg)).st_mode):
                failures.append("socket vectors: a refusal changed the register entry")
        finally:
            sock.close()
        sock_reg.unlink()
        # the appender's REGISTRY read never blocks either: a FIFO at the registry path is
        # refused (exit 2) naming the registry and the file type, before the register is
        # ever opened, and the refused append creates nothing
        saved_registry_bytes = registry.read_bytes()
        registry.unlink()
        os.mkfifo(str(registry))
        want_refusal("fifo-registry-refused",
                     alarm_bounded("fifo-registry-refused",
                                   lambda: append(repo / "regfifo-probe.jsonl", "MR-1")),
                     ("orchestration registry", "FIFO", "not a regular file"))
        if entry_state(repo / "regfifo-probe.jsonl") is not None:
            failures.append("fifo-registry-refused: the refused append created the register")
        if not stat.S_ISFIFO(os.lstat(str(registry)).st_mode):
            failures.append("fifo-registry-refused: the refusal changed the registry entry")
        registry.unlink()
        # a registry nested too deeply for the JSON decoder (which raises RecursionError,
        # not ValueError) is the same named refusal (exit 2), never an escaped traceback,
        # and the refused append creates nothing
        registry.write_bytes(b"[" * 2000000)

        def deep_registry_append():
            try:
                return append(repo / "deepreg-probe.jsonl", "MR-1")
            except RecursionError as exc:
                return "an uncaught RecursionError: {}".format(exc), ""

        want_refusal("deep-registry-append-refused",
                     alarm_bounded("deep-registry-append-refused", deep_registry_append),
                     ("orchestration registry", "nested too deeply"))
        if entry_state(repo / "deepreg-probe.jsonl") is not None:
            failures.append("deep-registry-append-refused: the refused append created the "
                            "register")
        registry.write_bytes(saved_registry_bytes)
        # the registry control: with the registry a regular file again, the same read
        # evaluates and a clean append lands under the same bound
        declare(mistakes_register="regctl.jsonl")
        alarm_bounded("regular-registry-still-reads",
                      lambda: accepted("regular-registry-still-reads",
                                       repo / "regctl.jsonl", "MR-1", bootstrap=True))
        # a regular register still appends and still projects after the file-type check,
        # under the same alarm so a clean path that started blocking fails rather than hangs
        declare(mistakes_register="plain.jsonl")
        plain_reg = repo / "plain.jsonl"
        alarm_bounded("regular-register-still-appends",
                      lambda: accepted("regular-register-still-appends", plain_reg, "MR-1",
                                       bootstrap=True))
        alarm_bounded("regular-register-still-appends-ordinary",
                      lambda: accepted("regular-register-still-appends-ordinary", plain_reg,
                                       "MR-1", "accepted"))
        plain_got = alarm_bounded("regular-register-still-projects",
                                  lambda: project_rc(plain_reg))
        # the projection's OUTPUT is asserted, not only its exit code: the register's open
        # item, the envelope and the source locator must all be the projected ones
        want_plain_items = [{"id": "MR-1-guardrail", "title": "land the guardrail: g",
                             "state": "open", "granted": True}]
        if plain_got is not None:
            if plain_got[0] != 0:
                failures.append("regular-register-still-projects: want exit 0, got exit "
                                "{!r}: {}".format(plain_got[0], plain_got[1].strip()))
            else:
                try:
                    plain_payload = json.loads(plain_got[2])
                except ValueError:
                    plain_payload = None
                if (not isinstance(plain_payload, dict)
                        or plain_payload.get("version") != 1
                        or plain_payload.get("items") != want_plain_items
                        or plain_payload.get("source", {}).get("locator") != str(plain_reg)
                        or not isinstance(plain_payload.get("generated_at_utc"), str)
                        or not plain_payload.get("generated_at_utc")):
                    failures.append("regular-register-still-projects: the projection must "
                                    "emit the register's own open items, got: {!r}".format(
                                        plain_got[2]))
        # a symlink to a REGULAR register still projects as before: the path is followed,
        # only what it aliases is type-checked, and the projected items are the target's own
        plain_link = repo / "plainlink.jsonl"
        plain_link.symlink_to("plain.jsonl")
        link_got = alarm_bounded("symlink-to-regular-register-projects",
                                 lambda: project_rc(plain_link))
        if link_got is not None:
            try:
                link_payload = json.loads(link_got[2])
            except ValueError:
                link_payload = None
            if (link_got[0] != 0 or not isinstance(link_payload, dict)
                    or link_payload.get("items") != want_plain_items):
                failures.append("symlink-to-regular-register-projects: want exit 0 with the "
                                "target's own items, got exit {!r}: {} {!r}".format(
                                    link_got[0], link_got[1].strip(), link_got[2]))
        plain_link.unlink()
        # a register whose bytes do not decode is refused by the projection as
        # cannot-evaluate (exit 2 naming the read), never an escaped traceback
        garbled_proj = repo / "garbled-proj.jsonl"
        garbled_proj.write_bytes(b"\xff\n")
        want_refusal("garbled-project-refused",
                     alarm_bounded("garbled-project-refused",
                                   lambda: project_rc(garbled_proj)),
                     ("cannot be read",))
        garbled_proj.unlink()
        # a descriptor failure AFTER the projection's open succeeded is cannot-evaluate
        # (exit 2 naming the open descriptor), never a successful empty projection and
        # never an escaped OSError: a failure injected at each descriptor step of the
        # shared reader (fstat, read, close), each reporting ENOENT, the errno that a
        # too-broad absence test would read as a missing register
        inj_reg = repo / "inject-proj.jsonl"
        inj_reg.write_text('{"id": "MR-9", "status": "accepted", "guardrail": "g"}\n',
                           encoding="utf-8")
        inj_id = os.stat(str(inj_reg))
        real_p_read, real_p_fstat, real_p_close = os.read, os.fstat, os.close
        inj_fired = {"n": 0}

        def inj_is_reg(st):
            return (st.st_dev, st.st_ino) == (inj_id.st_dev, inj_id.st_ino)

        def inj_read(read_fd, count):
            # fire only on the projection's own register read, identified by the open
            # descriptor's file identity, never on another read of the same chunk size
            if count == 1 << 16:
                try:
                    st_read = real_p_fstat(read_fd)
                except OSError:
                    st_read = None
                if st_read is not None and inj_is_reg(st_read):
                    inj_fired["n"] += 1
                    raise FileNotFoundError(2, "injected descriptor failure on read")
            return real_p_read(read_fd, count)

        def inj_fstat(fstat_fd):
            st_seen = real_p_fstat(fstat_fd)
            if inj_is_reg(st_seen):
                inj_fired["n"] += 1
                raise FileNotFoundError(2, "injected descriptor failure on fstat")
            return st_seen

        def inj_close(close_fd):
            # the close itself still happens exactly once; only its reported result fails
            try:
                st_closed = real_p_fstat(close_fd)
            except OSError:
                st_closed = None
            real_p_close(close_fd)
            if st_closed is not None and inj_is_reg(st_closed):
                inj_fired["n"] += 1
                raise FileNotFoundError(2, "injected descriptor failure on close")

        inj_reals = {"read": real_p_read, "fstat": real_p_fstat, "close": real_p_close}
        for inj_name, inj_attr, inj_fn in (
                ("project-read-failure-never-absent", "read", inj_read),
                ("project-fstat-failure-never-absent", "fstat", inj_fstat),
                ("project-close-failure-never-absent", "close", inj_close)):
            inj_fired["n"] = 0
            setattr(os, inj_attr, inj_fn)
            try:
                inj_got = alarm_bounded(inj_name, lambda: project_rc(inj_reg))
            finally:
                setattr(os, inj_attr, inj_reals[inj_attr])
            want_refusal(inj_name, inj_got,
                         ("cannot be read from its open descriptor",))
            if inj_got is not None and inj_fired["n"] == 0:
                failures.append(inj_name + ": the injected failure never fired, so the "
                                "vector proved nothing")
        inj_reg.unlink()
        # the paired FIRST-RUN control: a genuinely ABSENT register (a missing final name
        # confirmed under a present parent directory, the clean-tree state before any
        # append) still projects as no items, exit 0
        absent_got = alarm_bounded("absent-register-still-projects-empty",
                                   lambda: project_rc(repo / "no-such-register.jsonl"))
        if absent_got is not None:
            absent_payload = None
            if absent_got[0] == 0:
                try:
                    absent_payload = json.loads(absent_got[2])
                except ValueError:
                    absent_payload = None
            if (absent_got[0] != 0 or not isinstance(absent_payload, dict)
                    or absent_payload.get("items") != []):
                failures.append("absent-register-still-projects-empty: want exit 0 with no "
                                "items, got exit {!r}: {} {!r}".format(
                                    absent_got[0], absent_got[1].strip(), absent_got[2]))
        # a register path whose OPEN fails with something other than ENOENT (here a path
        # component that is a file, ENOTDIR) is cannot-evaluate, never an escaped OSError
        # and never an empty projection; this refusal predates the descriptor-failure
        # handling, so the vector is retained regression coverage (like the absent-register
        # control, it also passes without that change); the fixture reuses a regular file
        # as the bogus directory component
        enotdir_base = repo / "enotdir-proj.jsonl"
        enotdir_base.write_text("", encoding="utf-8")
        want_refusal("enotdir-project-refused",
                     alarm_bounded("enotdir-project-refused",
                                   lambda: project_rc(enotdir_base / "child.jsonl")),
                     ("cannot be read",))
        enotdir_base.unlink()
        # an invalid register path VALUE (an embedded NUL, impossible in an OS argument
        # vector but reachable in-process) is cannot-evaluate, never an escaped ValueError
        want_refusal("nul-path-project-refused",
                     alarm_bounded("nul-path-project-refused",
                                   lambda: project_rc("nul\x00path.jsonl")),
                     ("cannot be read",))
        # rows the projection cannot shape are refused with exit 2 naming the row, never
        # an escaped traceback (a non-object row has no fields; a non-string id would make
        # the sorted enumeration compare incomparable keys) and never exit 1
        for row_name, row_body, row_naming in (
                ("non-json-line-project-refused", "{\n", ("is not JSON",)),
                ("non-object-row-project-refused", "[1]\n",
                 ("not a JSON object with a string id",)),
                ("non-string-id-project-refused",
                 '{"id": 1, "status": "proposed", "guardrail": "g"}\n',
                 ("not a JSON object with a string id",))):
            rowbad = repo / "rowbad-proj.jsonl"
            rowbad.write_text(row_body, encoding="utf-8")
            want_refusal(row_name,
                         alarm_bounded(row_name, lambda: project_rc(rowbad)),
                         row_naming)
            rowbad.unlink()
        # a line nested too deeply for the JSON decoder (balanced or not) is refused with
        # exit 2 naming the nesting and printing no payload, never an escaped
        # RecursionError and never exit 1; the depth is far beyond what any plausible
        # interpreter stack lets the decoder parse, so the decoder's recursion failure,
        # not a shape check, decides both vectors
        for deep_name, deep_body in (
                ("deep-nesting-unbalanced-project-refused", "[" * 2000000 + "\n"),
                ("deep-nesting-balanced-project-refused",
                 "[" * 2000000 + "]" * 2000000 + "\n")):
            deepbad = repo / "deepbad-proj.jsonl"
            deepbad.write_text(deep_body, encoding="utf-8")
            deep_got = alarm_bounded(deep_name, lambda: project_rc(deepbad))
            want_refusal(deep_name, deep_got, ("nested too deeply",))
            if deep_got is not None and deep_got[2] != "":
                failures.append("{}: a refused projection must print nothing on stdout, "
                                "got {!r}".format(deep_name, deep_got[2][:120]))
            deepbad.unlink()
        # an UNRESOLVABLE register path is a refusal NAMING the cause (exit 2, empty
        # stdout), never an empty projection: a dangling symbolic link at the register
        # path, then a register path under a missing parent directory (the absent-register
        # first-run control above keeps a confirmed-missing final name projecting as no
        # items)
        dangle_proj = repo / "dangle-proj.jsonl"
        dangle_proj.symlink_to("missing-proj-target")
        dangle_got = alarm_bounded("dangling-register-project-refused",
                                   lambda: project_rc(dangle_proj))
        want_refusal("dangling-register-project-refused", dangle_got,
                     ("dangling symbolic link",))
        if dangle_got is not None and dangle_got[2] != "":
            failures.append("dangling-register-project-refused: a refused projection must "
                            "print nothing on stdout, got {!r}".format(dangle_got[2][:120]))
        if not dangle_proj.is_symlink():
            failures.append("dangling-register-project-refused: the refusal changed the "
                            "register entry")
        dangle_proj.unlink()
        noparent_got = alarm_bounded("missing-parent-project-refused",
                                     lambda: project_rc(repo / "no-such-dir" / "reg.jsonl"))
        want_refusal("missing-parent-project-refused", noparent_got,
                     ("parent directory", "cannot be opened"))
        if noparent_got is not None and noparent_got[2] != "":
            failures.append("missing-parent-project-refused: a refused projection must "
                            "print nothing on stdout, got {!r}".format(noparent_got[2][:120]))
        # both production rendering sites are driven UNCONDITIONALLY by fault injection
        # (host-independent, so this coverage cannot vanish on a host whose decoder does
        # not out-run its renderer): a decoded row value whose rendering raises
        # RecursionError is a named refusal at the projection's formatting handler
        # (exit 2, nothing on stdout) and at the appender's shared row validation
        # (exit 1 through the gate's placeholder), never an escaped RecursionError
        class _UnshowableValue:
            def __repr__(self):
                raise RecursionError("synthetic unrenderable value")

            def __str__(self):
                raise RecursionError("synthetic unrenderable value")

        unshow_proj = repo / "unshow-proj.jsonl"
        unshow_proj.write_text("{}\n", encoding="utf-8")
        real_proj_loads = json.loads

        def unshowable_loads(s, *largs, **lkw):
            return {"id": "MR-1", "status": "accepted", "guardrail": _UnshowableValue()}

        json.loads = unshowable_loads
        try:
            unshow_got = alarm_bounded("project-unrenderable-value-refused",
                                       lambda: project_rc(unshow_proj))
        finally:
            json.loads = real_proj_loads
        want_refusal("project-unrenderable-value-refused", unshow_got,
                     ("nested too deeply to format",))
        if unshow_got is not None and unshow_got[2] != "":
            failures.append("project-unrenderable-value-refused: a refused projection must "
                            "print nothing on stdout, got {!r}".format(unshow_got[2][:120]))
        unshow_proj.unlink()
        import check_mistakes_register as gate_module
        declare(mistakes_register="unshow-append.jsonl")
        unshow_append_reg = repo / "unshow-append.jsonl"
        real_pairs_hook = gate_module._pairs_without_duplicates
        unshow_row = {"seq": _UnshowableValue(), "id": "MR-1", "ts": "t", "mistake": "m",
                      "evidence": "e", "rule": "r", "guardrail": "g", "status": "proposed",
                      "prev": ZERO}

        def unshow_append():
            try:
                return append(unshow_append_reg, "MR-1", bootstrap=True)
            except RecursionError as exc:
                return "an uncaught RecursionError: {}".format(exc), ""

        gate_module._pairs_without_duplicates = lambda pairs: dict(unshow_row)
        try:
            ua_got = alarm_bounded("append-unrenderable-seq-named", unshow_append)
        finally:
            gate_module._pairs_without_duplicates = real_pairs_hook
        if ua_got is not None:
            ua_rc, ua_err = ua_got
            if ua_rc != 1 or "is not the line number" not in ua_err \
                    or "nested too deeply to display" not in ua_err:
                failures.append("append-unrenderable-seq-named: want exit 1 naming the seq "
                                "violation through the placeholder, got exit {!r}: "
                                "{}".format(ua_rc, ua_err.strip()))
            if entry_state(unshow_append_reg) is not None:
                failures.append("append-unrenderable-seq-named: the refused append created "
                                "the register")
        # a row the decoder ACCEPTS whose value is still too deep to format into the
        # enumeration is refused with exit 2 naming the nesting (a wording distinct from
        # the decoder refusal above, so a mis-measured depth cannot satisfy this vector
        # through the decoder path), never an escaped RecursionError; the window between
        # the decoder's and the renderer's recursion limits is MEASURED on this host

        def _deepest(ok):
            lo, hi = 1, 2
            while ok(hi):
                lo, hi = hi, hi * 2
                if hi > 1 << 24:
                    return lo
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                if ok(mid):
                    lo = mid
                else:
                    hi = mid
            return lo

        def _deep_row(depth):
            return ('{"id": "MR-1", "status": "accepted", "guardrail": '
                    + "[" * depth + "]" * depth + "}")

        def _row_decodes(depth):
            try:
                json.loads(_deep_row(depth))
            except RecursionError:
                return False
            return True

        def _value_renders(depth):
            try:
                repr(json.loads("[" * depth + "]" * depth))
            except RecursionError:
                return False
            return True

        decode_max = _deepest(_row_decodes)
        render_max = _deepest(_value_renders)
        if render_max < decode_max:
            deepval = repo / "deepval-proj.jsonl"
            deepval.write_text(_deep_row(decode_max) + "\n", encoding="utf-8")
            deepval_got = alarm_bounded("deep-value-project-refused",
                                        lambda: project_rc(deepval))
            want_refusal("deep-value-project-refused", deepval_got,
                         ("nested too deeply to format",))
            if deepval_got is not None and deepval_got[2] != "":
                failures.append("deep-value-project-refused: a refused projection must "
                                "print nothing on stdout, got {!r}".format(
                                    deepval_got[2][:120]))
            deepval.unlink()
            # the appender shares the gate's row validation: an EXISTING row that decodes
            # but whose seq value is too deep to render refuses with exit 1 naming the
            # violation through the placeholder, never an escaped RecursionError
            declare(mistakes_register="deepseq.jsonl")
            deep_seq_reg = repo / "deepseq.jsonl"
            deep_seq_line = ('{"seq": ' + "[" * decode_max + "]" * decode_max
                             + ', "id": "MR-1", "ts": "t", "mistake": "m", "evidence": "e",'
                             ' "rule": "r", "guardrail": "g", "status": "proposed",'
                             ' "prev": "' + ZERO + '"}')
            deep_seq_reg.write_text(deep_seq_line + "\n", encoding="utf-8")
            # the anchor digests the raw line without rendering its decoded values, so the
            # untracked register's append-only authority is decidable and the vector exits
            # at the row violation, not at a missing anchor
            update_anchor(deep_seq_reg, [deep_seq_line])

            def deep_seq_append():
                try:
                    return append(deep_seq_reg, "MR-2")
                except RecursionError as exc:
                    return "an uncaught RecursionError: {}".format(exc), ""

            ds_got = alarm_bounded("append-deep-seq-value-named", deep_seq_append)
            if ds_got is not None:
                ds_rc, ds_err = ds_got
                if ds_rc != 1 or "is not the line number" not in ds_err \
                        or "nested too deeply to display" not in ds_err:
                    failures.append("append-deep-seq-value-named: want exit 1 naming the "
                                    "seq violation through the placeholder, got exit {!r}: "
                                    "{}".format(ds_rc, ds_err.strip()))
            deep_seq_reg.unlink()
            Path(str(deep_seq_reg) + ".anchor").unlink()
        else:
            # when render_max >= decode_max no decodable input can out-run the renderer
            # on this host, so there is no real-value window to drive here; the
            # fault-injection vectors above drove both production rendering sites
            # unconditionally, and the omitted window cases are reported BY NAME, never
            # silently folded into the PASS text
            print("NOT RUN: deep-value-project-refused and append-deep-seq-value-named "
                  "(this host's render limit {} is not below its decode limit {}, so no "
                  "decodable input can out-run the renderer; the unconditional "
                  "fault-injection vectors still drove both rendering sites)".format(
                      render_max, decode_max))

        # a tracked register with no computable merge-base is cannot-evaluate (exit 2), not a pass
        tracked = repo / "tracked.jsonl"
        declare(mistakes_register="tracked.jsonl")
        accepted("tracked-seed", tracked, "MR-1", bootstrap=True)
        git(repo, "add", "tracked.jsonl")
        git(repo, "commit", "-q", "-m", "track the register")
        refused("tracked-without-authority-exit-2", tracked, 2, "cannot evaluate", "MR-2")
        # a git failure deciding WHETHER the register is tracked (a corrupt index: exit 128, not
        # git's confirmed "not tracked" exit 1) is cannot-evaluate, never the missing-anchor
        # allowance; the register, its anchor and the directory stay byte-identical
        def dir_state(d):
            return {e.name: (e.read_bytes() if e.is_file() and not e.is_symlink() else None)
                    for e in d.iterdir()}

        entries = dir_state(repo)
        index_path = repo / ".git" / "index"
        saved_index = index_path.read_bytes()
        index_path.write_bytes(b"broken-index")
        try:
            refused("git-failure-is-cannot-evaluate", tracked, 2,
                    "cannot decide whether the register is tracked", "MR-2")
        finally:
            index_path.write_bytes(saved_index)
        if dir_state(repo) != entries:
            failures.append("a git-failure refusal changed the register directory (an entry "
                            "name or its bytes)")
        # the root probe and the authority reads run under a SCRUBBED environment: a junk
        # GIT_INDEX_FILE and GIT_DIR in the CALLER's environment never reach them, so this
        # append lands cleanly despite both (before this change the junk index alone flipped
        # the tracked decision into a refusal)
        junk_index = tmp / "junk-index"
        junk_index.write_text("broken-index", encoding="utf-8")
        declare(mistakes_register="envclean.jsonl")
        os.environ["GIT_INDEX_FILE"] = str(junk_index)
        os.environ["GIT_DIR"] = str(junk_index)
        try:
            accepted("scrubbed-env-append-lands", repo / "envclean.jsonl", "MR-1",
                     bootstrap=True)
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
            os.environ.pop("GIT_DIR", None)
        # an unreadable (non-UTF-8) register is refused as cannot-evaluate, never read as empty
        garbled = repo / "garbled.jsonl"
        declare(mistakes_register="garbled.jsonl")
        garbled.write_bytes(b"\xff\n")
        refused("unreadable-register-exit-2", garbled, 2, "cannot be read", "MR-1")
        # the DECLARATION check's own dangling-link refusal: a dangling symbolic link at a
        # declared register path is refused by name in _declared_family (the stat's ENOENT
        # is never read as a confirmed-absent declaration), before the register open could
        # refuse the link with its own wording
        declare(mistakes_register="dangle-decl.jsonl")
        dangle_decl = repo / "dangle-decl.jsonl"
        dangle_decl.symlink_to("missing-decl-target")
        refused("declared-dangling-link-refused", dangle_decl, 2,
                "the declared mistakes_register path names a dangling symbolic link", "MR-1")
        if not dangle_decl.is_symlink():
            failures.append("declared-dangling-link-refused: the refusal changed the "
                            "register entry")
        if entry_state(repo / "missing-decl-target") is not None:
            failures.append("declared-dangling-link-refused: the refusal created the "
                            "link's target")
        dangle_decl.unlink()
        # an absent register refused on its first row stays absent, directory included
        declare(mistakes_register="sub/new.jsonl")
        refused("refused-first-row-creates-nothing", repo / "sub" / "new.jsonl", 1,
                "first row for MR-1 must be proposed", "MR-1", "accepted", bootstrap=True)
        if entry_state(repo / "sub") is not None:
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
        accepted("nested-repo-validates-in-gate-root", nested / "nested-reg.jsonl", "MR-1",
                 bootstrap=True)
        # PRODUCTION root resolution (no _gate_root patch), exercised in SUBPROCESSES:
        # (a) the appender installed in repository A, the register in nested repository B that
        # A declares: the resolver walks up from the appender's own installed file, so the
        # append validates in A's context and lands (a register-based or cwd-based resolution
        # would consult B, find no registry there, and refuse);
        # (b) the appender installed OUTSIDE any git repository, invoked with the cwd inside a
        # declaring repository: refused (exit 2), never a root adopted from the invocation
        # directory. Only (b) needs no marker above the temporary directory: the other
        # installs stop the walk at their own marker first, and the non-repository gate
        # root below is refused by the realpath comparison whatever surrounds it.
        inst_a = tmp / "inst-a"
        (inst_a / "tools").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(inst_a)], check=True, capture_output=True,
                       timeout=30)
        src_tools = Path(__file__).resolve().parent
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(src_tools / tool_name), str(inst_a / "tools" / tool_name))
        (inst_a / ".aiqt").mkdir()
        (inst_a / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": "nested-b/reg.jsonl"}), encoding="utf-8")
        nested_b = inst_a / "nested-b"
        nested_b.mkdir()
        subprocess.run(["git", "init", "-q", str(nested_b)], check=True, capture_output=True,
                       timeout=30)
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(inst_a / "tools" / "orch_register.py"), "append",
             "--register", str(nested_b / "reg.jsonl"), "--id", "MR-1", "--mistake", "m",
             "--evidence", "e", "--rule", "mstreg", "--guardrail", "g", "--bootstrap"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 0 or len(_lines(nested_b / "reg.jsonl")) != 1:
            failures.append("production-root-nested-repo: the installed appender should "
                            "validate the nested register in its own repository's context "
                            "and land, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        # TEST-ONLY HERMETICITY for (b): "outside any git repository" cannot hold for a
        # temporary directory that itself sits inside a git checkout, so (b) runs the copied
        # appender's own main() through a driver that pins a search CEILING at the fixture's
        # temporary directory: the production walk is not changed and still climbs to the
        # filesystem root, but a .git entry in a directory strictly ABOVE that directory
        # (on its resolved chain, the chain the walk climbs) reads as absent, so no
        # ancestor marker, valid, invalid or uninspectable, can decide the verdict, while
        # every marker at or below it is still read (the control below appends through the
        # same driver from inside a repository and lands)
        ceiling_driver = tmp / "ceiling-driver.py"
        ceiling_driver.write_text(
            "import os\n"
            "import sys\n"
            "tools_dir, ceiling = sys.argv[1], sys.argv[2]\n"
            "sys.path.insert(0, tools_dir)\n"
            "import check_mistakes_register\n"
            "above = set()\n"
            "chain = os.path.realpath(ceiling)\n"
            "while os.path.dirname(chain) != chain:\n"
            "    chain = os.path.dirname(chain)\n"
            "    above.add(chain)\n"
            "real_entry_state = check_mistakes_register.entry_state\n"
            "\n"
            "\n"
            "def ceiling_entry_state(entry):\n"
            "    name = os.fspath(entry)\n"
            "    if os.path.basename(name) == '.git' and os.path.dirname(name) in above:\n"
            "        return None\n"
            "    return real_entry_state(entry)\n"
            "\n"
            "\n"
            "check_mistakes_register.entry_state = ceiling_entry_state\n"
            "import orch_register\n"
            "sys.argv = [os.path.join(tools_dir, 'orch_register.py')] + sys.argv[3:]\n"
            "sys.exit(orch_register.main())\n", encoding="utf-8")
        outside_tools = tmp / "outside-tools"
        outside_tools.mkdir()
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(src_tools / tool_name), str(outside_tools / tool_name))
        declare(mistakes_register="cwd-probe.jsonl")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(ceiling_driver), str(outside_tools), str(tmp),
             "append", "--register", str(repo / "cwd-probe.jsonl"), "--id", "MR-1",
             "--mistake", "m", "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
            capture_output=True, text=True, timeout=60, cwd=str(repo))
        if proc.returncode != 2 or "not installed inside a git repository" not in proc.stderr:
            failures.append("production-root-outside-install: want exit 2 naming the "
                            "installation, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        if entry_state(repo / "cwd-probe.jsonl") is not None:
            failures.append("production-root-outside-install: the refused append created the "
                            "register")
        # the ceiling's control: the same driver, pinned at the same directory, still finds
        # the marker of an install BELOW it, so the append from inside repository A lands
        # (red if the driver hid every .git entry, which would let (b) pass under a
        # cwd-derived root)
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(ceiling_driver), str(inst_a / "tools"), str(tmp),
             "append", "--register", str(nested_b / "reg.jsonl"), "--id", "MR-2",
             "--mistake", "m", "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 0 or len(_lines(nested_b / "reg.jsonl")) != 2:
            failures.append("production-root-ceiling-keeps-markers-below: the ceiling driver "
                            "must still find an install's own marker below the ceiling and "
                            "land, got exit {}: {}".format(proc.returncode,
                                                           proc.stderr.strip()))
        # (c) a '.git' entry git itself rejects (an invalid gitfile, an empty .git directory)
        # is a repository MARKER, not a repository: the installed appender refuses BEFORE
        # trusting the marker's registry or writing the declared EXTERNAL register
        ext_regs = tmp / "ext-regs"
        ext_regs.mkdir()
        for marker_tag in ("invalid-gitfile", "empty-gitdir"):
            bad_inst = tmp / ("inst-" + marker_tag)
            (bad_inst / "tools").mkdir(parents=True)
            if marker_tag == "invalid-gitfile":
                (bad_inst / ".git").write_text("not a gitfile\n", encoding="utf-8")
            else:
                (bad_inst / ".git").mkdir()
            for tool_name in ("orch_register.py", "check_mistakes_register.py",
                              "_gen_common.py"):
                shutil.copy(str(src_tools / tool_name), str(bad_inst / "tools" / tool_name))
            (bad_inst / ".aiqt").mkdir()
            ext_reg = ext_regs / (marker_tag + "-reg.jsonl")
            (bad_inst / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
                {"version": 1, "mistakes_register": str(ext_reg)}), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-I", "-B", str(bad_inst / "tools" / "orch_register.py"),
                 "append", "--register", str(ext_reg), "--id", "MR-1", "--mistake", "m",
                 "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
                capture_output=True, text=True, timeout=60, cwd=str(tmp))
            if proc.returncode != 2 \
                    or "validation root is not a git repository" not in proc.stderr:
                failures.append("production-root-{}: want exit 2 naming the unconfirmed "
                                "root, got exit {}: {}".format(marker_tag, proc.returncode,
                                                               proc.stderr.strip()))
            if entry_state(ext_reg) is not None:
                failures.append("production-root-{}: the refused append created the external "
                                "register".format(marker_tag))
            # the same marker under a CONTAMINATED caller environment: GIT_DIR (and GIT_DIR
            # plus GIT_WORK_TREE) pointed at a valid repository used to make git confirm the
            # marker directory as a toplevel; the scrubbed probe refuses both, creating
            # nothing
            for env_tag, bad_env in (
                    ("git-dir", dict(os.environ, GIT_DIR=str(repo / ".git"))),
                    ("git-dir-worktree", dict(os.environ, GIT_DIR=str(repo / ".git"),
                                              GIT_WORK_TREE=str(bad_inst)))):
                proc = subprocess.run(
                    [sys.executable, "-I", "-B",
                     str(bad_inst / "tools" / "orch_register.py"), "append",
                     "--register", str(ext_reg), "--id", "MR-1", "--mistake", "m",
                     "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
                    capture_output=True, text=True, timeout=60, cwd=str(tmp), env=bad_env)
                if proc.returncode != 2 \
                        or "validation root is not a git repository" not in proc.stderr:
                    failures.append("production-root-{}-{}: a caller GIT_DIR must never make "
                                    "git confirm the marker root, want exit 2, got exit {}: "
                                    "{}".format(marker_tag, env_tag, proc.returncode,
                                                proc.stderr.strip()))
                if entry_state(ext_reg) is not None:
                    failures.append("production-root-{}-{}: the refused append created the "
                                    "external register".format(marker_tag, env_tag))
        # (c2) a DANGLING .git symlink is the nearest marker: it is SELECTED (never walked
        # past to the valid outer repository, whose registry even declares this same external
        # register) and the git confirmation refuses it, creating nothing
        dangle_outer = tmp / "dangle-outer"
        (dangle_outer / "w" / "tools").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(dangle_outer)], check=True,
                       capture_output=True, timeout=30)
        (dangle_outer / "w" / ".git").symlink_to("missing-git-target")
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(src_tools / tool_name),
                        str(dangle_outer / "w" / "tools" / tool_name))
        dangle_reg = ext_regs / "dangling-marker-reg.jsonl"
        (dangle_outer / ".aiqt").mkdir()
        (dangle_outer / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": str(dangle_reg)}), encoding="utf-8")
        (dangle_outer / "w" / ".aiqt").mkdir()
        (dangle_outer / "w" / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": str(dangle_reg)}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-I", "-B",
             str(dangle_outer / "w" / "tools" / "orch_register.py"), "append",
             "--register", str(dangle_reg), "--id", "MR-1", "--mistake", "m",
             "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 2 \
                or "validation root is not a git repository" not in proc.stderr:
            failures.append("production-root-dangling-marker: want exit 2 naming the "
                            "unconfirmed root, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        if entry_state(dangle_reg) is not None:
            failures.append("production-root-dangling-marker: the refused append created the "
                            "external register")
        # (c3) the REALPATH comparison: an empty .git directory nested INSIDE a valid outer
        # repository makes git discovery name the OUTER toplevel; the comparison refuses the
        # nested candidate rather than adopting the outer answer, creating nothing
        nested_marker = tmp / "nested-marker"
        (nested_marker / "x" / "tools").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(nested_marker)], check=True,
                       capture_output=True, timeout=30)
        (nested_marker / "x" / ".git").mkdir()
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(src_tools / tool_name),
                        str(nested_marker / "x" / "tools" / tool_name))
        (nested_marker / "x" / ".aiqt").mkdir()
        nested_reg = ext_regs / "nested-marker-reg.jsonl"
        (nested_marker / "x" / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": str(nested_reg)}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-I", "-B",
             str(nested_marker / "x" / "tools" / "orch_register.py"), "append",
             "--register", str(nested_reg), "--id", "MR-1", "--mistake", "m",
             "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 2 \
                or "validation root is not a git repository" not in proc.stderr:
            failures.append("production-root-nested-marker: the realpath comparison should "
                            "refuse the nested empty .git, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        if entry_state(nested_reg) is not None:
            failures.append("production-root-nested-marker: the refused append created the "
                            "external register")
        # (d) the valid linked-worktree case keeps working: a worktree's .git FILE is a
        # git-confirmed root, and the append lands
        wt_main = tmp / "wt-main"
        wt_main.mkdir()
        subprocess.run(["git", "init", "-q", str(wt_main)], check=True, capture_output=True,
                       timeout=30)
        (wt_main / "seed.txt").write_text("seed\n", encoding="utf-8")
        git(wt_main, "add", "seed.txt")
        git(wt_main, "commit", "-q", "-m", "seed")
        wt = tmp / "wt-linked"
        git(wt_main, "worktree", "add", "-q", str(wt))
        (wt / "tools").mkdir()
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(src_tools / tool_name), str(wt / "tools" / tool_name))
        (wt / ".aiqt").mkdir()
        (wt / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": "wt-reg.jsonl"}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(wt / "tools" / "orch_register.py"),
             "append", "--register", str(wt / "wt-reg.jsonl"), "--id", "MR-1", "--mistake",
             "m", "--evidence", "e", "--rule", "mstreg", "--guardrail", "g", "--bootstrap"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 0 or len(_lines(wt / "wt-reg.jsonl")) != 1:
            failures.append("production-root-linked-worktree: a valid worktree gitfile root "
                            "should append cleanly, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        # two declarations resolving to one path are an ambiguous id family: cannot-evaluate,
        # even when the register exists and the row itself would be legal under one family
        declare(mistakes_register="shared.jsonl", attestations="shared.jsonl")
        refused("conflicting-declarations-refused", repo / "shared.jsonl", 2,
                "conflicting registry declarations", "MR-1")
        declare(mistakes_register="reg.jsonl", attestations="./reg.jsonl")
        refused("aliased-declarations-refused", reg, 2,
                "conflicting registry declarations", "MR-4")
        # two declarations that are HARD LINKS to one inode are one register under two names
        # even though their resolved paths differ: ambiguous, cannot-evaluate
        hard_a = repo / "hard-a.jsonl"
        declare(mistakes_register="hard-a.jsonl")
        accepted("hardlink-seed", hard_a, "MR-1", bootstrap=True)
        os.link(str(hard_a), str(repo / "hard-b.jsonl"))
        declare(mistakes_register="hard-a.jsonl", attestations="hard-b.jsonl")
        refused("hardlinked-declarations-refused", hard_a, 2,
                "conflicting registry declarations", "MR-1", "accepted")
        # a DECLARATION whose identity inspection fails with anything but ENOENT is
        # cannot-evaluate: an unreadable identity is never a missing identity (it could hide
        # a hard link to the register, landing the append in both aliases)
        declare(mistakes_register="hid-a.jsonl", attestations="hid-b.jsonl")
        hid_a = repo / "hid-a.jsonl"
        accepted("hidden-link-seed", hid_a, "MR-1", bootstrap=True)
        os.link(str(hid_a), str(repo / "hid-b.jsonl"))
        hid_b_path = str(repo / "hid-b.jsonl")
        real_os_stat = os.stat
        for errtag, errnum, errname in (("eio", 5, "injected input-output error"),
                                        ("eacces", 13, "injected permission denied")):
            def hiding_stat(p, *sargs, **skw):
                if str(p) == hid_b_path:
                    raise OSError(errnum, errname)
                return real_os_stat(p, *sargs, **skw)
            os.stat = hiding_stat
            try:
                refused("declaration-stat-{}-exit-2".format(errtag), hid_a, 2,
                        "only confirmed absence (ENOENT) omits a file identity", "MR-1",
                        "accepted")
            finally:
                os.stat = real_os_stat
        if len(_lines(hid_a)) != 1 or len(_lines(repo / "hid-b.jsonl")) != 1:
            failures.append("declaration-stat-errors: a refused append changed a hard-linked "
                            "alias")
        # registry PRESENCE is ENOENT-only: a dangling registry symlink entry is SELECTED and
        # refused, never skipped into the lower-priority registry file, and a registry lstat
        # failure that is not ENOENT is cannot-evaluate
        declare(mistakes_register="reg.jsonl")
        saved_registry = registry.read_bytes()
        fallback_registry = repo / ".aiqt" / "orchestration.json"
        fallback_registry.write_text(json.dumps(
            {"version": 1, "mistakes_register": "reg.jsonl"}), encoding="utf-8")
        registry.unlink()
        registry.symlink_to("missing-registry-target")
        refused("dangling-registry-symlink-refused", reg, 2,
                "dangling symbolic link", "MR-4")
        registry.unlink()
        registry.write_bytes(saved_registry)
        real_reg_lstat = os.lstat

        def registry_eio_lstat(p, *largs, **lkw):
            if str(p) == str(registry):
                raise OSError(5, "injected input-output error")
            return real_reg_lstat(p, *largs, **lkw)

        os.lstat = registry_eio_lstat
        try:
            refused("registry-lstat-eio-refused", reg, 2,
                    "only confirmed absence (ENOENT) selects the next registry file", "MR-4")
        finally:
            os.lstat = real_reg_lstat
        fallback_registry.unlink()
        # a declared value that is not a non-empty string (a number, a list, an empty string,
        # null) is a named cannot-evaluate for EACH form, never a TypeError traceback; the
        # per-key refusal text is asserted so a mutant that skips one form goes red here
        for bad_value, bad_form in ((5, "number"), (["x"], "list"), ("", "empty-string"),
                                    (None, "null")):
            registry.write_text(json.dumps(
                {"version": 1, "mistakes_register": bad_value}), encoding="utf-8")
            refused("malformed-declaration-{}-refused".format(bad_form), reg, 2,
                    "the registry's mistakes_register declaration is not a non-empty string",
                    "MR-4")
        # FIRST-ROW SERIALIZATION: a cooperating appender that commits between the exclusive
        # creation and the creator's lock (its commit renames a new file over the created
        # one) forces the creator to refuse under its lock; exactly the interleaved row
        # remains, and the chain stays clean
        declare(mistakes_register="race.jsonl")
        race_reg = repo / "race.jsonl"
        race = {"fired": False, "inner": None}
        real_flock = fcntl.flock

        def interleaving(lock_fd, op):
            if not race["fired"]:
                race["fired"] = True
                race["inner"] = append(race_reg, "MR-1", bootstrap=True)
            return real_flock(lock_fd, op)

        fcntl.flock = interleaving
        try:
            rc, err = append(race_reg, "MR-1", bootstrap=True)
        finally:
            fcntl.flock = real_flock
        if race["inner"] is None or race["inner"][0] != 0:
            failures.append("first-row-race: the interleaved appender should land its row, "
                            "got {!r}".format(race["inner"]))
        if rc != 2 or "no longer the file that was validated" not in err \
                or "left in place" not in err or temps_beside(race_reg):
            failures.append("first-row-race: the creator should refuse (exit 2) naming the "
                            "replaced file and keep the row, got exit {}: {}".format(
                                rc, err.strip()))
        race_lines = _lines(race_reg)
        if len(race_lines) != 1 or verify_lines(race_lines, root=gate_root["root"],
                                                prefix="MR-"):
            failures.append("first-row-race: exactly the interleaved row should remain, with "
                            "a clean verify_lines")
        # a writer that does not take the lock and writes INTO the created file before the
        # creator's lock is caught by the size re-check: the creator refuses, its row never
        # lands, and the writer's bytes are left in place, named
        declare(mistakes_register="race2.jsonl")
        race2_reg = repo / "race2.jsonl"
        race2 = {"fired": False}

        def writing_into_created(lock_fd, op):
            if not race2["fired"]:
                race2["fired"] = True
                with open(str(race2_reg), "ab") as fh:
                    fh.write(b"interloper\n")
            return real_flock(lock_fd, op)

        fcntl.flock = writing_into_created
        try:
            rc, err = append(race2_reg, "MR-1", bootstrap=True)
        finally:
            fcntl.flock = real_flock
        if (rc != 2 or "after its exclusive creation" not in err
                or "it is no longer empty" not in err or temps_beside(race2_reg)
                or race2_reg.read_bytes() != b"interloper\n"):
            failures.append("first-row-inplace-writer: the creator should refuse (exit 2) "
                            "naming the size re-check and leave the writer's bytes, got exit "
                            "{}: {}".format(rc, err.strip()))
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
            rc, err = append(appeared, "MR-1", bootstrap=True)
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
        accepted("vanish-seed", vanish, "MR-1", bootstrap=True)

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
        if entry_state(vanish) is not None:
            failures.append("vanished-register-refused: a refusal re-created the register")
        # a descriptor read failure is cannot-evaluate with a named reason, never an
        # unhandled exception
        declare(mistakes_register="reg.jsonl")
        real_os_read = os.read
        read_fail = {"fired": False}
        read_fail_identity = os.stat(str(reg))

        def failing_read(read_fd, count):
            # fire only on the appender's own register read, identified by the open
            # descriptor's file identity, never on the registry read (same chunk size) and
            # never on a subprocess pipe read from the root-validation git probe
            if not read_fail["fired"] and count == 1 << 16:
                try:
                    st_read = os.fstat(read_fd)
                except OSError:
                    st_read = None
                if st_read is not None and (st_read.st_dev, st_read.st_ino) == \
                        (read_fail_identity.st_dev, read_fail_identity.st_ino):
                    read_fail["fired"] = True
                    raise OSError(5, "injected input-output error")
            return real_os_read(read_fd, count)

        os.read = failing_read
        try:
            refused("descriptor-read-failure-exit-2", reg, 2,
                    "cannot be read from its open descriptor", "MR-4")
        finally:
            os.read = real_os_read
        # ALL OR NOTHING: a failure injected at each commit step (the temporary's creation, its
        # lock, a failed write, a short write, its permission bits, its fsync, the under-lock
        # re-check, the rename) leaves the register's bytes exactly as they were (for a
        # re-check vector: exactly as the interfering writer left them, with no row of this
        # call) and no temporary file beside it. Every injection also fails any truncation,
        # so an append written into the register itself could not repair it: each vector is
        # red on an in-place append, which lands the row or leaves partial bytes.
        real_os_write = os.write
        real_c_open = os.open
        real_c_ftruncate = os.ftruncate
        real_c_fchmod = os.fchmod
        real_c_fsync = os.fsync
        real_c_replace = os.replace
        real_c_unlink = os.unlink
        real_c_fstat = os.fstat
        real_c_lstat = os.lstat
        declare(mistakes_register="commit.jsonl")
        commit_reg = repo / "commit.jsonl"
        accepted("commit-seed", commit_reg, "MR-1", bootstrap=True)
        commit_seed = commit_reg.read_bytes()
        commit_anchor = snapshot(commit_reg)[1]
        commit_tmp = str(commit_reg) + ".tmp."
        commit_link = repo / "commit-link.jsonl"

        def refusing_truncate(trunc_fd, length):
            raise OSError(5, "injected truncate failure")

        def step_open(p, flags, *oargs, **okw):
            if os.fspath(p).startswith(commit_tmp) and flags & os.O_EXCL:
                raise OSError(28, "injected no space")
            return real_c_open(p, flags, *oargs, **okw)

        def step_flock(lock_fd, op):
            if op & fcntl.LOCK_NB:
                raise OSError(errno.EWOULDBLOCK, "injected lock contention")
            return real_flock(lock_fd, op)

        def step_write(write_fd, payload):
            if isinstance(payload, (bytes, bytearray)) and payload.startswith(b'{"'):
                real_os_write(write_fd, payload[:7])
                raise OSError(5, "injected write failure")
            return real_os_write(write_fd, payload)

        def step_short_write(write_fd, payload):
            if isinstance(payload, (bytes, bytearray)) and payload.startswith(b'{"'):
                return real_os_write(write_fd, payload[:7])
            return real_os_write(write_fd, payload)

        def step_fchmod(chmod_fd, mode):
            raise OSError(1, "injected chmod failure")

        def step_fsync(sync_fd):
            raise OSError(5, "injected fsync failure")

        def step_replace(src, dst, *rargs, **rkw):
            if os.fspath(src).startswith(commit_tmp):
                raise OSError(16, "injected rename failure")
            return real_c_replace(src, dst, *rargs, **rkw)

        def fsync_after(action):
            # a writer that does not take the lock lands while the temporary is synced,
            # after the validation and before the re-check
            fired = {"done": False}

            def fsync_then(sync_fd):
                if not fired["done"]:
                    fired["done"] = True
                    action()
                return real_c_fsync(sync_fd)
            return fsync_then

        def replace_with_seed_copy():
            decoy = repo / "commit-decoy.jsonl"
            decoy.write_bytes(commit_seed)
            real_c_replace(str(decoy), str(commit_reg))

        def append_in_place():
            with open(str(commit_reg), "ab") as fh:
                fh.write(b"interloper\n")

        rewritten = commit_seed.replace(b'"mistake": "m"', b'"mistake": "n"', 1)

        def rewrite_in_place():
            with open(str(commit_reg), "r+b") as fh:
                fh.write(rewritten)

        def link_register():
            os.link(str(commit_reg), str(commit_link))

        def fstat_fails_after_fsync():
            # the temporary's identity is read through its descriptor once it is synced: fail
            # that one read (the descriptor fsync just synced), so the cleanup can still remove it
            seen = {"fd": None, "done": False}

            def recording_fsync(sync_fd):
                seen["fd"] = sync_fd
                return real_c_fsync(sync_fd)

            def failing_fstat(st_fd, *fargs, **fkw):
                if st_fd == seen["fd"] and not seen["done"]:
                    seen["done"] = True
                    raise OSError(5, "injected descriptor fstat failure")
                return real_c_fstat(st_fd, *fargs, **fkw)
            return [(os, "fsync", recording_fsync), (os, "fstat", failing_fstat)]

        def lstat_fails_at_temp():
            # the name check's lstat of the temporary fails with an error other than a missing
            # name, once, so the cleanup can still remove it
            fired = {"done": False}

            def failing_lstat(p, *largs, **lkw):
                if os.fspath(p).startswith(commit_tmp) and not fired["done"]:
                    fired["done"] = True
                    raise OSError(5, "injected temporary lstat failure")
                return real_c_lstat(p, *largs, **lkw)
            return [(os, "lstat", failing_lstat)]

        for tag, patches, reason, want in (
                ("temp-create", [(os, "open", step_open)],
                 "the temporary file for the commit could not be created exclusively", None),
                ("temp-lock", [(fcntl, "flock", step_flock)],
                 "the temporary file could not be locked", None),
                ("write", [(os, "write", step_write)],
                 "the write to the temporary file failed", None),
                ("short-write", [(os, "write", step_short_write)],
                 "short write to the temporary file", None),
                ("permission-bits", [(os, "fchmod", step_fchmod)],
                 "the register's permission bits", None),
                ("fsync", [(os, "fsync", step_fsync)], "could not be synced to disk", None),
                ("temp-fstat", fstat_fails_after_fsync(),
                 "the temporary file cannot be inspected through its descriptor ([Errno 5] "
                 "injected descriptor fstat failure)", None),
                ("temp-lstat", lstat_fails_at_temp(),
                 "cannot be re-checked before the rename ([Errno 5] injected temporary lstat "
                 "failure)", None),
                ("rename", [(os, "replace", step_replace)],
                 "could not be renamed over the register", None),
                ("recheck-identity", [(os, "fsync", fsync_after(replace_with_seed_copy))],
                 "no longer the file that was validated", None),
                ("recheck-size", [(os, "fsync", fsync_after(append_in_place))],
                 "a writer changed the file after the validation",
                 commit_seed + b"interloper\n"),
                ("recheck-bytes", [(os, "fsync", fsync_after(rewrite_in_place))],
                 "a writer rewrote it in place", rewritten),
                ("recheck-hard-link", [(os, "fsync", fsync_after(link_register))],
                 "hard links", None)):
            name = "commit-step-" + tag
            commit_reg.write_bytes(commit_seed)
            saved = [(obj, attr, getattr(obj, attr)) for obj, attr, _fn in patches]
            for obj, attr, fn in patches:
                setattr(obj, attr, fn)
            os.ftruncate = refusing_truncate
            try:
                rc, err = append(commit_reg, "MR-1", "accepted")
            finally:
                os.ftruncate = real_c_ftruncate
                for obj, attr, old in saved:
                    setattr(obj, attr, old)
            got_bytes = commit_reg.read_bytes() if entry_state(commit_reg) is not None else None
            want_bytes = commit_seed if want is None else want
            if rc != 2 or reason not in err:
                failures.append("{}: want exit 2 naming {!r}, got exit {}: {}".format(
                    name, reason, rc, err.strip()))
            if got_bytes != want_bytes:
                failures.append("{}: the register's bytes changed, want {!r}, got {!r}".format(
                    name, want_bytes, got_bytes))
            if tag != "temp-create" and "the temporary file {}".format(commit_tmp) not in err:
                failures.append("{}: the refusal should name the removed temporary, got: "
                                "{}".format(name, err.strip()))
            if temps_beside(commit_reg):
                failures.append("{}: a temporary file was left: {}".format(
                    name, temps_beside(commit_reg)))
            if snapshot(commit_reg)[1] != commit_anchor:
                failures.append("{}: the refusal changed the anchor".format(name))
            if entry_state(commit_link) is not None:
                vector_cleanup(name, commit_link.unlink)
        # the rename goes by name: an entry a writer that ignores the lock swaps in at the
        # temporary's name while the temporary is synced (a foreign file, or a symlink) is
        # refused by that name and left in place, named, and a temporary removed from its name
        # is refused; the register stays byte-identical, never the swapped-in entry
        swap_side = repo / "commit-swap-side.jsonl"
        swap_target = repo / "commit-swap-target.jsonl"
        swap_target.write_bytes(b"TARGET\n")

        def swap_in_file(at):
            swap_side.write_bytes(b"FOREIGN UNVALIDATED\n")
            real_c_replace(str(swap_side), at)

        def swap_in_symlink(at):
            os.symlink(str(swap_target), str(swap_side))
            real_c_replace(str(swap_side), at)

        def swap_at_temp(swap, seen):
            # the swap records the temporary's path it observed, so the refusal clause must
            # name that path
            seen.append(str(repo / temps_beside(commit_reg)[0]))
            swap(seen[0])

        for swap_tag, swap, reason, left_named in (
                ("file", swap_in_file, "{} is no longer this call's temporary",
                 "is not this call's temporary and was left in place"),
                ("symlink", swap_in_symlink, "{} is no longer this call's temporary",
                 "is not this call's temporary and was left in place"),
                ("vanished", real_c_unlink, "{} is no longer at its name", None)):
            name = "commit-temp-swapped-" + swap_tag
            commit_reg.write_bytes(commit_seed)
            before = snapshot(commit_reg)
            seen_tmp = []
            os.fsync = fsync_after(
                lambda swap=swap, seen=seen_tmp: swap_at_temp(swap, seen))
            try:
                rc, err = append(commit_reg, "MR-1", "accepted")
            finally:
                os.fsync = real_c_fsync
            left = temps_beside(commit_reg)
            reason = reason.format(seen_tmp[0] if seen_tmp else commit_tmp + "<not observed>")
            if rc != 2 or reason not in err:
                failures.append("{}: want exit 2 naming {!r}, got exit {}: {}".format(
                    name, reason, rc, err.strip()))
            if snapshot(commit_reg) != before:
                failures.append("{}: the register or its anchor changed, want {!r}, got "
                                "{!r}".format(name, before, snapshot(commit_reg)))
            if left_named is None and left:
                failures.append("{}: an entry was left at a temporary's name: {}".format(
                    name, left))
            if left_named is not None and (len(left) != 1 or "{} {}".format(
                    repo / left[0], left_named) not in err):
                failures.append("{}: the swapped-in entry should be left in place and named by "
                                "its path, got {}: {}".format(name, left, err.strip()))
            if os.path.islink(str(commit_reg)):
                vector_cleanup(name, commit_reg.unlink)
            vector_cleanup(name, lambda: [
                os.unlink(str(repo / n)) for n in temps_beside(commit_reg)])
        vector_cleanup("commit-temp-swapped", swap_target.unlink)
        # an entry at the name with the temporary's device and inode is the temporary itself,
        # a regular file, so no filesystem state fails only the regular-file test; an injected
        # lstat result pins that test
        not_regular_seen = []

        def not_regular_lstat(p, *largs, **lkw):
            st = real_c_lstat(p, *largs, **lkw)
            if os.fspath(p).startswith(commit_tmp):
                not_regular_seen.append(os.fspath(p))
                return os.stat_result((stat.S_IFLNK | 0o777,) + tuple(st)[1:])
            return st

        commit_reg.write_bytes(commit_seed)
        before = snapshot(commit_reg)
        os.lstat = not_regular_lstat
        try:
            rc, err = append(commit_reg, "MR-1", "accepted")
        finally:
            os.lstat = real_c_lstat
        if (rc != 2 or not not_regular_seen
                or "{} is no longer this call's temporary".format(not_regular_seen[0]) not in err
                or snapshot(commit_reg) != before or temps_beside(commit_reg)):
            failures.append("commit-temp-name-not-regular: want exit 2 naming the temporary, "
                            "the register unchanged and no temporary left, got exit {} with {}: "
                            "{}".format(rc, temps_beside(commit_reg), err.strip()))
        if os.path.islink(str(commit_reg)):
            vector_cleanup("commit-temp-name-not-regular", commit_reg.unlink)
        commit_reg.write_bytes(commit_seed)
        # a temporary that cannot be removed is NAMED, with its path, in the refusal, and the
        # register still keeps its bytes
        def refusing_tmp_unlink(p, *uargs, **ukw):
            if os.fspath(p).startswith(commit_tmp):
                raise OSError(1, "injected unlink failure")
            return real_c_unlink(p, *uargs, **ukw)

        os.fsync = step_fsync
        os.unlink = refusing_tmp_unlink
        try:
            rc, err = append(commit_reg, "MR-1", "accepted")
        finally:
            os.fsync = real_c_fsync
            os.unlink = real_c_unlink
        left_tmps = temps_beside(commit_reg)
        if (rc != 2 or "injected unlink failure" not in err or "removed by hand" not in err
                or len(left_tmps) != 1 or str(repo / left_tmps[0]) not in err
                or commit_reg.read_bytes() != commit_seed):
            failures.append("commit-temp-removal-failure-named: want exit 2 naming the "
                            "temporary left behind by its path, got exit {} with {}: "
                            "{}".format(rc, left_tmps, err.strip()))
        vector_cleanup("commit-temp-removal-failure-named", lambda: [
            os.unlink(str(repo / n)) for n in temps_beside(commit_reg)])
        # a directory sync that fails AFTER the rename cannot undo the landed row: exit 2
        # saying the row is in the register and naming the sync
        def failing_dir_fsync(sync_fd):
            if stat.S_ISDIR(os.fstat(sync_fd).st_mode):
                raise OSError(5, "injected directory sync failure")
            return real_c_fsync(sync_fd)

        os.fsync = failing_dir_fsync
        try:
            rc, err = append(commit_reg, "MR-1", "accepted")
        finally:
            os.fsync = real_c_fsync
        if (rc != 2 or "was appended and is in the register" not in err
                or "injected directory sync failure" not in err
                or len(_lines(commit_reg)) != 2 or temps_beside(commit_reg)):
            failures.append("commit-dir-sync-failure-named: want exit 2 saying the row is in "
                            "the register and naming the sync, got exit {}: {}".format(
                                rc, err.strip()))
        # the replacing file carries the register's permission bits
        commit_reg.write_bytes(commit_seed)
        os.chmod(str(commit_reg), 0o640)
        accepted("commit-keeps-permission-bits", commit_reg, "MR-1", "accepted")
        if stat.S_IMODE(os.lstat(str(commit_reg)).st_mode) != 0o640:
            failures.append("commit-keeps-permission-bits: want mode 0o640 after the commit, "
                            "got {:o}".format(stat.S_IMODE(os.lstat(str(commit_reg)).st_mode)))
        # a cooperating appender that commits (by rename) while this one waits for the lock
        # leaves this one holding the lock on the replaced file: it starts over on the file
        # the path names now, so both rows land, in order, with a clean chain
        commit_reg.write_bytes(commit_seed)
        accepted("lock-wait-seed", commit_reg, "MR-2")
        wait = {"fired": False, "inner": None}

        def committing_while_waiting(lock_fd, op):
            if not wait["fired"]:
                wait["fired"] = True
                wait["inner"] = append(commit_reg, "MR-1", "accepted")
            return real_flock(lock_fd, op)

        fcntl.flock = committing_while_waiting
        try:
            rc, err = append(commit_reg, "MR-2", "accepted")
        finally:
            fcntl.flock = real_flock
        wait_lines = _lines(commit_reg)
        wait_ids = [(json.loads(l).get("id"), json.loads(l).get("status"))
                    for l in wait_lines]
        if (wait["inner"] is None or wait["inner"][0] != 0 or rc != 0
                or wait_ids != [("MR-1", "proposed"), ("MR-2", "proposed"),
                                ("MR-1", "accepted"), ("MR-2", "accepted")]
                or verify_lines(wait_lines, root=gate_root["root"], prefix="MR-")):
            failures.append("lock-wait-starts-over: both rows should land in order with a "
                            "clean chain, got inner {!r}, outer exit {}: {} rows {}".format(
                                wait["inner"], rc, err.strip(), wait_ids))
        # a close failure on the stale descriptor of a SUPERSEDED lock-wait attempt names
        # the superseded attempt and is never called refused: the retried call carries the
        # verdict, so when it RETURNED the message says that verdict was reported above
        # and the exit becomes 2, and when it RAISED the message says the verdict is
        # carried by the still unwinding exception (reported after this line), which
        # keeps propagating instead of being replaced by exit 2
        declare(mistakes_register="superseded-close.jsonl")
        sup_reg = repo / "superseded-close.jsonl"
        accepted("superseded-close-seed", sup_reg, "MR-1", bootstrap=True)
        accepted("superseded-close-second-seed", sup_reg, "MR-2")
        real_sup_close, real_sup_fstat = os.close, os.fstat

        def superseded_scenario(name, inner_rid, inner_status, outer_rid, outer_status):
            st_stale = os.lstat(str(sup_reg))
            fired = {"n": 0, "again": 0, "inner": None}
            stale_failed = set()

            def stale_failing_close(close_fd):
                # a second close of a descriptor number whose close already failed is
                # the regression the "attempted exactly once" guarantee forbids: count
                # it so the scenario can assert zero
                if close_fd in stale_failed:
                    fired["again"] += 1
                try:
                    st_closed = real_sup_fstat(close_fd)
                except OSError:
                    st_closed = None
                real_sup_close(close_fd)
                if st_closed is not None and (st_closed.st_dev, st_closed.st_ino) == \
                        (st_stale.st_dev, st_stale.st_ino):
                    fired["n"] += 1
                    stale_failed.add(close_fd)
                    raise OSError(5, "injected close failure")

            committed = {"done": False}

            def commit_then_fail_closes(lock_fd, op):
                # while the outer append waits for its lock, a cooperating appender
                # commits, so the file the outer call locked is stale and it starts
                # over; only then is the close failure armed (on the stale file's
                # identity), so the cooperating append's own closes stay real
                if not committed["done"]:
                    committed["done"] = True
                    fired["inner"] = append(sup_reg, inner_rid, inner_status)
                    os.close = stale_failing_close
                return real_flock(lock_fd, op)

            fcntl.flock = commit_then_fail_closes
            try:
                got = alarm_bounded(name,
                                    lambda: append(sup_reg, outer_rid, outer_status))
            finally:
                fcntl.flock = real_flock
                os.close = real_sup_close
            if fired["inner"] is None or fired["inner"][0] != 0:
                failures.append("{}: the cooperating commit should land cleanly, got "
                                "{!r}".format(name, fired["inner"]))
            if fired["n"] == 0:
                failures.append("{}: the injected close failure never fired, so the "
                                "vector proved nothing".format(name))
            if fired["again"]:
                failures.append("{}: the failed close was attempted again ({} extra "
                                "attempt(s)); each owned descriptor's close is attempted "
                                "exactly once".format(name, fired["again"]))
            return got

        sup_got = superseded_scenario("superseded-close-failure-named", "MR-1", "accepted",
                                      "MR-2", "accepted")
        if sup_got is not None:
            sup_rc, sup_err = sup_got
            if (sup_rc != 2 or "superseded lock-wait attempt" not in sup_err
                    or "reported its own verdict above" not in sup_err
                    or "could not be closed" not in sup_err or "refused" in sup_err):
                failures.append("superseded-close-failure-named: want exit 2 naming the "
                                "superseded attempt beside the close failure (never "
                                "'refused'), got exit {!r}: {}".format(
                                    sup_rc, sup_err.strip()))
            sup_ids = [(json.loads(l).get("id"), json.loads(l).get("status"))
                       for l in _lines(sup_reg)]
            if sup_ids != [("MR-1", "proposed"), ("MR-2", "proposed"),
                           ("MR-1", "accepted"), ("MR-2", "accepted")]:
                failures.append("superseded-close-failure-named: both rows should land "
                                "in order past the stale close failure, got {}".format(
                                    sup_ids))
        # the retried call RAISES here: the cooperating commit takes the proposal id this
        # call wanted, so the retried call's next-id refusal (a sys.exit) is still
        # unwinding when the stale descriptor's close fails; the close failure must say
        # the verdict follows with that exception, never that it was reported above (the
        # refusal prints after this message), and the exception's own verdict (exit 1
        # here) stays, never replaced by exit 2
        raising_got = superseded_scenario("superseded-close-failure-while-raising",
                                          "MR-3", "proposed", "MR-3", "proposed")
        if raising_got is not None:
            r_rc, r_err = raising_got
            if (r_rc != 1 or "superseded lock-wait attempt" not in r_err
                    or "must take the next id" not in r_err
                    or "still unwinding" not in r_err
                    or "reported its own verdict above" in r_err
                    or "could not be closed" not in r_err
                    or "append: refused" in r_err):
                failures.append("superseded-close-failure-while-raising: want the retried "
                                "call's own refusal (exit 1) with the close failure "
                                "naming the superseded attempt and the still unwinding "
                                "verdict, got exit {!r}: {}".format(r_rc, r_err.strip()))
        vector_cleanup("superseded-close-failure-while-raising", sup_reg.unlink)
        vector_cleanup("superseded-close-failure-while-raising",
                       Path(str(sup_reg) + ".anchor").unlink)
        # the vectors below continue on the commit register, so restore its declaration
        declare(mistakes_register="commit.jsonl")
        # a landed row's commit read that git cannot launch, or that times out, is
        # cannot-evaluate: exit 2 naming the read, never a traceback, nothing written
        real_c_run = subprocess.run
        for cf_tag, cf_exc in (
                ("timeout", subprocess.TimeoutExpired(["git", "cat-file"], 10)),
                ("launch-error", OSError(2, "injected launch failure"))):
            def failing_catfile(argv, *rargs, **rkw):
                if "cat-file" in argv:
                    raise cf_exc
                return real_c_run(argv, *rargs, **rkw)

            commit_reg.write_bytes(commit_seed)
            accepted("landed-commit-read-{}-seed".format(cf_tag), commit_reg, "MR-1",
                     "accepted")
            before = snapshot(commit_reg)
            subprocess.run = failing_catfile
            try:
                try:
                    rc, err = append(commit_reg, "MR-1", "landed", commit=head,
                                     check_ref="check.py")
                except Exception as exc:  # the escape this vector pins: a raw exception
                    rc, err = "raised", "{}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.run = real_c_run
            if rc != 2 or "cites an existing commit" not in err:
                failures.append("landed-commit-read-{}-exit-2: want exit 2 naming the commit "
                                "read, got exit {}: {}".format(cf_tag, rc, err.strip()))
            if snapshot(commit_reg) != before or temps_beside(commit_reg):
                failures.append("landed-commit-read-{}-exit-2: the refusal changed the "
                                "register".format(cf_tag))
        # FIRST-ROW CLEANUP (under the acquired lock): a write failure on the very first row,
        # in nested directory components this call itself created, removes the created file
        # AND those components, and the refusal names the removal
        declare(mistakes_register="mk/deep/first.jsonl")
        first_reg = repo / "mk" / "deep" / "first.jsonl"
        write_fail3 = {"fired": False}

        def failing_write3(write_fd, payload):
            if not write_fail3["fired"] and isinstance(payload, bytes):
                if payload.startswith(b'{"'):
                    write_fail3["fired"] = True
                    raise OSError(5, "injected write failure")
            return real_os_write(write_fd, payload)

        os.write = failing_write3
        try:
            rc, err = append(first_reg, "MR-1", bootstrap=True)
        finally:
            os.write = real_os_write
        if rc != 2 or ("the created register and the directory components this call made "
                       "were removed") not in err:
            failures.append("first-row-write-failure-cleanup: want exit 2 naming the removal, "
                            "got exit {}: {}".format(rc, err.strip()))
        if entry_state(first_reg) is not None or entry_state(repo / "mk") is not None:
            failures.append("first-row-write-failure-cleanup: the refused first row left the "
                            "register or its made directories behind")
        # an exclusive-creation failure removes the directory components this call made and
        # names the removal, leaving the tree as it was
        declare(mistakes_register="mk2/deep/excl.jsonl")
        excl_reg = repo / "mk2" / "deep" / "excl.jsonl"
        real_os_open = os.open

        def refusing_open(p, flags, *oargs, **okw):
            if str(p) == str(excl_reg) and (flags & os.O_EXCL):
                raise OSError(13, "injected exclusive-creation failure")
            return real_os_open(p, flags, *oargs, **okw)

        os.open = refusing_open
        try:
            rc, err = append(excl_reg, "MR-1", bootstrap=True)
        finally:
            os.open = real_os_open
        if rc != 2 or "could not be created exclusively" not in err or (
                "the directory components this call made were removed") not in err:
            failures.append("excl-failure-cleanup: want exit 2 naming the removal, got exit "
                            "{}: {}".format(rc, err.strip()))
        if entry_state(repo / "mk2") is not None:
            failures.append("excl-failure-cleanup: the refused creation left its made "
                            "directories behind")
        # FIRST-ROW IDENTITY: a path replaced between the exclusive creation and the lock no
        # longer names the created file; the re-check UNDER the acquired lock refuses, and
        # the displacing file receives no row
        declare(mistakes_register="ident.jsonl")
        ident_reg = repo / "ident.jsonl"
        ident = {"fired": False}

        def displacing(lock_fd, op):
            if not ident["fired"]:
                ident["fired"] = True
                decoy = repo / "ident-decoy.jsonl"
                decoy.write_bytes(b"decoy bytes\n")
                os.replace(str(decoy), str(ident_reg))
            return real_flock(lock_fd, op)

        fcntl.flock = displacing
        try:
            rc, err = append(ident_reg, "MR-1", bootstrap=True)
        finally:
            fcntl.flock = real_flock
        if rc != 2 or "no longer the file that was validated" not in err:
            failures.append("first-row-identity: want exit 2 naming the binding, got exit "
                            "{}: {}".format(rc, err.strip()))
        if ident_reg.read_bytes() != b"decoy bytes\n":
            failures.append("first-row-identity: the displacing file received the row")
        # FIRST-ROW IDENTITY with made directory components: the refusal NAMES the components
        # this call made that were left in place (the displacing file occupies them)
        declare(mistakes_register="mk3/deep/ident2.jsonl")
        ident2_reg = repo / "mk3" / "deep" / "ident2.jsonl"
        ident2 = {"fired": False}

        def displacing2(lock_fd, op):
            if not ident2["fired"]:
                ident2["fired"] = True
                decoy2 = repo / "ident2-decoy.jsonl"
                decoy2.write_bytes(b"decoy bytes\n")
                os.replace(str(decoy2), str(ident2_reg))
            return real_flock(lock_fd, op)

        fcntl.flock = displacing2
        try:
            rc, err = append(ident2_reg, "MR-1", bootstrap=True)
        finally:
            fcntl.flock = real_flock
        if rc != 2 or "no longer the file that was validated" not in err \
                or "left in place" not in err or "mk3" not in err:
            failures.append("first-row-identity-made-dirs: the refusal should name the made "
                            "directory components left in place, got exit {}: {}".format(
                                rc, err.strip()))
        if ident2_reg.read_bytes() != b"decoy bytes\n":
            failures.append("first-row-identity-made-dirs: the displacing file received the "
                            "row")
        # a cleanup that finds the created first-row file ABSENT at the register path,
        # unlinked or RENAMED AWAY, says it is no longer at that path, never that it is
        # gone (a renamed file survives elsewhere) and never that it could not be removed
        # (red when the message says the file was already gone)
        for gone_how in ("unlinked", "renamed"):
            declare(mistakes_register="gone-{}.jsonl".format(gone_how))
            gone_reg = repo / "gone-{}.jsonl".format(gone_how)
            gone_moved = repo / "gone-{}-moved.jsonl".format(gone_how)
            gone = {"fired": False}

            def removing(lock_fd, op):
                if not gone["fired"]:
                    gone["fired"] = True
                    if gone_how == "unlinked":
                        os.unlink(str(gone_reg))
                    else:
                        os.rename(str(gone_reg), str(gone_moved))
                return real_flock(lock_fd, op)

            fcntl.flock = removing
            try:
                rc, err = append(gone_reg, "MR-1", bootstrap=True)
            finally:
                fcntl.flock = real_flock
            if (rc != 2 or "no longer the file that was validated" not in err
                    or "the created register was no longer at the register path" not in err
                    or "possibly renamed away rather than gone" not in err
                    or "already gone" in err or "could not be removed" in err
                    or (gone_how == "renamed" and (entry_state(gone_moved) is None
                                                   or gone_moved.read_bytes() != b""))):
                failures.append("first-row-absent-at-path-{}: the cleanup should say the "
                                "created file is no longer at the register path, never gone, "
                                "got exit {}: {}".format(gone_how, rc, err.strip()))
        # a lock that cannot be acquired on the created first-row file leaves that empty file
        # in place and says so: removing it without the lock could race a cooperating appender
        declare(mistakes_register="lockfail.jsonl")
        lockfail_reg = repo / "lockfail.jsonl"
        lockfail = {"fired": False}

        def refusing_flock(lock_fd, op):
            if not lockfail["fired"]:
                lockfail["fired"] = True
                raise OSError(37, "injected lock failure")
            return real_flock(lock_fd, op)

        fcntl.flock = refusing_flock
        try:
            rc, err = append(lockfail_reg, "MR-1", bootstrap=True)
        finally:
            fcntl.flock = real_flock
        if rc != 2 or "cannot lock the register" not in err or "left in place" not in err:
            failures.append("first-row-lock-failure: want exit 2 naming the kept file, got "
                            "exit {}: {}".format(rc, err.strip()))
        if entry_state(lockfail_reg) is None or lockfail_reg.read_bytes() != b"":
            failures.append("first-row-lock-failure: the created empty register should be "
                            "left in place, empty")
        # a register directory that cannot be created is a named exit 2, never a traceback
        declare(mistakes_register="mkfail/reg.jsonl")
        real_os_mkdir = os.mkdir

        def failing_mkdir(mp, *margs, **mkw):
            raise OSError(5, "injected mkdir failure")

        os.mkdir = failing_mkdir
        try:
            refused("mkdir-failure-exit-2", repo / "mkfail" / "reg.jsonl", 2,
                    "directory could not be created", "MR-1", bootstrap=True)
        finally:
            os.mkdir = real_os_mkdir
        if entry_state(repo / "mkfail") is not None:
            failures.append("mkdir-failure-exit-2: a failed mkdir left directory components "
                            "behind")
        # a PARTIAL directory creation failure removes exactly the component it did create
        # (recorded only after its own mkdir succeeded) and the refusal reports the sweep's
        # outcome, never the plan
        declare(mistakes_register="mk4/deep/part.jsonl")
        part_outer = repo / "mk4"

        def partial_mkdir(mp, *margs, **mkw):
            if str(mp).endswith("deep"):
                raise OSError(28, "injected no space")
            return real_os_mkdir(mp, *margs, **mkw)

        os.mkdir = partial_mkdir
        try:
            rc, err = append(repo / "mk4" / "deep" / "part.jsonl", "MR-1", bootstrap=True)
        finally:
            os.mkdir = real_os_mkdir
        if rc != 2 or "directory could not be created" not in err \
                or "the directory components this call made were removed" not in err:
            failures.append("partial-mkdir-cleanup: the refusal should report the completed "
                            "sweep of the created component, got exit {}: {}".format(
                                rc, err.strip()))
        if entry_state(part_outer) is not None:
            failures.append("partial-mkdir-cleanup: the created outer component was left "
                            "behind")
        # a PRE-EXISTING parent whose inspection fails (EIO, not ENOENT) refuses BEFORE any
        # mkdir and is NEVER recorded as made nor removed: an uninspectable ancestor is never
        # read as absent (before this change the walk recorded it as made and the sweep
        # removed the pre-existing, empty directory)
        declare(mistakes_register="mk5/deep/pre.jsonl")
        pre_outer = repo / "mk5"
        pre_outer.mkdir()
        real_walk_lstat = os.lstat

        def pre_eio_lstat(wp, *largs, **lkw):
            if str(wp) == str(pre_outer):
                raise OSError(5, "injected input-output error")
            return real_walk_lstat(wp, *largs, **lkw)

        def never_mkdir(mp, *margs, **mkw):
            raise OSError(28, "injected no space")

        os.lstat = pre_eio_lstat
        os.mkdir = never_mkdir
        try:
            rc, err = append(repo / "mk5" / "deep" / "pre.jsonl", "MR-1",
                             bootstrap=True)
        finally:
            os.lstat = real_walk_lstat
            os.mkdir = real_os_mkdir
        if rc != 2 or "directory could not be created" not in err \
                or "cannot be inspected" not in err:
            failures.append("uninspectable-ancestor-refuses: want exit 2 naming the "
                            "inspection before any mkdir, got exit {}: {}".format(
                                rc, err.strip()))
        pre_state = entry_state(pre_outer)
        if pre_state is None or not stat.S_ISDIR(pre_state.st_mode):
            failures.append("uninspectable-ancestor-refuses: the pre-existing parent was "
                            "removed or replaced")
        if entry_state(repo / "mk5" / "deep") is not None:
            failures.append("uninspectable-ancestor-refuses: a refused walk still created a "
                            "component")
        # the sweep removes a component only while its recorded identity still matches: an
        # entry REPLACED by ANOTHER EMPTY DIRECTORY (same name, demonstrably different
        # identity: the replacement is created BESIDE the original, so the two inodes exist
        # at once, then renamed into the name) is left in place and NAMED, never removed,
        # so the device-and-inode comparison itself, not the directory type check alone,
        # carries this vector
        swap_dir = repo / "swapdir"
        swap_dir.mkdir()
        swap_st = os.lstat(str(swap_dir))
        swap_other = repo / "swapdir-other"
        swap_other.mkdir()
        other_st = os.lstat(str(swap_other))
        os.rmdir(str(swap_dir))
        os.rename(str(swap_other), str(swap_dir))
        left, skipped, unreach, _closes = _made_dirs_swept([(swap_dir, swap_st.st_dev, swap_st.st_ino)])
        swap_after = entry_state(swap_dir)
        if (not left or "no longer the directory this call created" not in left[0]
                or str(swap_dir) not in left[0] or skipped or unreach or swap_after is None
                or not stat.S_ISDIR(swap_after.st_mode)
                or (swap_after.st_dev, swap_after.st_ino)
                != (other_st.st_dev, other_st.st_ino)):
            failures.append("swept-identity: a replacing empty directory must survive and "
                            "be named, got left={} skipped={} unreachable={}".format(
                                left, skipped, unreach))
        vector_cleanup("swept-identity", lambda: os.rmdir(str(swap_dir)))
        # a component replaced by a NON-DIRECTORY (a regular file) is the separate TYPE
        # leg of the same re-check: left in place and named, never removed
        type_dir = repo / "typedir"
        type_dir.mkdir()
        type_st = os.lstat(str(type_dir))
        os.rmdir(str(type_dir))
        type_dir.write_text("not a directory", encoding="utf-8")
        left, skipped, unreach, _closes = _made_dirs_swept([(type_dir, type_st.st_dev, type_st.st_ino)])
        if (not left or "no longer the directory this call created" not in left[0]
                or skipped or unreach or entry_state(type_dir) is None):
            failures.append("swept-type: a non-directory at a made component's name must "
                            "be left in place and named, got left={} skipped={} "
                            "unreachable={}".format(left, skipped, unreach))
        vector_cleanup("swept-type", type_dir.unlink)
        # an already-absent INNER component does not stop the sweep: the OUTER component
        # is still removed and the absent one is reported as skipped (red when the ENOENT
        # branch stops the sweep instead of continuing)
        sweep_outer = repo / "sweepout"
        sweep_outer.mkdir()
        sweep_outer_st = os.lstat(str(sweep_outer))
        sweep_inner = sweep_outer / "inner"
        sweep_inner.mkdir()
        sweep_inner_st = os.lstat(str(sweep_inner))
        os.rmdir(str(sweep_inner))
        left, skipped, unreach, _closes = _made_dirs_swept(
            [(sweep_inner, sweep_inner_st.st_dev, sweep_inner_st.st_ino),
             (sweep_outer, sweep_outer_st.st_dev, sweep_outer_st.st_ino)])
        if (left or unreach or skipped != [str(sweep_inner)]
                or entry_state(sweep_outer) is not None):
            failures.append("absent-component-sweep-continues: the outer component must "
                            "still be removed and the absent inner one reported skipped, "
                            "got left={} skipped={} unreachable={}".format(
                                left, skipped, unreach))
        # the identity read-back is RELATIVE to the created component's parent descriptor:
        # an OUTER component swapped aside after its creation (renamed away, a planted
        # tree moved into its pathname) can no longer substitute the planted tree's entry
        # for the identity, so the sweep leaves the planted entry in place and NAMED and
        # removes nothing another actor made (red under a string-path identity read-back,
        # which records the planted entry and removes it)
        planted = repo / "mk7-planted"
        (planted / "deep").mkdir(parents=True)
        planted_deep_st = os.lstat(str(planted / "deep"))
        ancswap = {"fired": False}

        def ancestor_swapping_mkdir(mp, *margs, **mkw):
            out = real_os_mkdir(mp, *margs, **mkw)
            if not ancswap["fired"] and os.fspath(mp).endswith("deep"):
                ancswap["fired"] = True
                os.rename(str(repo / "mk7"), str(repo / "mk7-aside"))
                os.rename(str(planted), str(repo / "mk7"))
            return out

        os.mkdir = ancestor_swapping_mkdir
        try:
            anc_made = _dirs_created(repo / "mk7" / "deep")
        finally:
            os.mkdir = real_os_mkdir
        left, skipped, unreach, _closes = _made_dirs_swept(anc_made)
        planted_deep = entry_state(repo / "mk7" / "deep")
        if (len(anc_made) != 2 or skipped or len(left) != 1 or len(unreach) != 1
                or "no longer the directory this call created" not in left[0]
                or "no longer the directory this call created" not in unreach[0]
                or planted_deep is None
                or (planted_deep.st_dev, planted_deep.st_ino)
                != (planted_deep_st.st_dev, planted_deep_st.st_ino)
                or entry_state(repo / "mk7-aside" / "deep") is None):
            failures.append("made-dir-ancestor-swap: the planted tree must be left in "
                            "place and named, never removed, got made={} left={} "
                            "skipped={} unreachable={}".format(anc_made, left, skipped,
                                                               unreach))
        # the DISCLOSED RESIDUAL, pinned: a directory moved into a created component's
        # NAME between the mkdir and the identity read-back (same parent, so the parent
        # descriptor cannot tell the two apart) is recorded as made, and the bound's
        # per-component term is the removal of exactly that one EMPTY directory for this
        # component; the directory this call actually created survives under its
        # displaced name
        intruder = repo / "mk8-intruder"
        intruder.mkdir()
        intruder_st = os.lstat(str(intruder))
        nameswap = {"fired": False}

        def name_swapping_mkdir(mp, *margs, **mkw):
            out = real_os_mkdir(mp, *margs, **mkw)
            if not nameswap["fired"] and os.fspath(mp).endswith("mk8"):
                nameswap["fired"] = True
                os.rename(str(repo / "mk8"), str(repo / "mk8-displaced"))
                os.rename(str(intruder), str(repo / "mk8"))
            return out

        os.mkdir = name_swapping_mkdir
        try:
            resid_made = _dirs_created(repo / "mk8")
        finally:
            os.mkdir = real_os_mkdir
        left, skipped, unreach, _closes = _made_dirs_swept(resid_made)
        displaced_st = entry_state(repo / "mk8-displaced")
        if (resid_made != [(repo / "mk8", intruder_st.st_dev, intruder_st.st_ino)]
                or left or skipped or unreach or entry_state(repo / "mk8") is not None
                or displaced_st is None or not stat.S_ISDIR(displaced_st.st_mode)):
            failures.append("name-swap-residual: the moved-in empty directory is recorded "
                            "and removed (the disclosed worst case) and the displaced "
                            "created directory survives, got made={} left={} "
                            "skipped={} unreachable={}".format(resid_made, left, skipped,
                                                               unreach))
        # a mkdir RACED by another actor (FileExistsError: the name appears between the
        # walk and this call's own mkdir) is used, never recorded: the sweep removes the
        # component this call did make and the raced directory survives untouched
        raced = {"fired": False}

        def racing_mkdir(mp, *margs, **mkw):
            if not raced["fired"] and os.fspath(mp).endswith("mk6"):
                raced["fired"] = True
                real_os_mkdir(mp, *margs, **mkw)  # another actor wins the race
            return real_os_mkdir(mp, *margs, **mkw)

        os.mkdir = racing_mkdir
        try:
            raced_made = _dirs_created(repo / "mk6" / "deep")
        finally:
            os.mkdir = real_os_mkdir
        raced_before = os.lstat(str(repo / "mk6"))
        left, skipped, unreach, _closes = _made_dirs_swept(raced_made)
        raced_after = entry_state(repo / "mk6")
        if ([m[0] for m in raced_made] != [repo / "mk6" / "deep"] or left or skipped
                or unreach
                or entry_state(repo / "mk6" / "deep") is not None or raced_after is None
                or (raced_after.st_dev, raced_after.st_ino)
                != (raced_before.st_dev, raced_before.st_ino)):
            failures.append("raced-mkdir-never-recorded: only the component this call made "
                            "may be recorded or removed, got made={} left={} "
                            "skipped={}".format(raced_made, left, skipped))
        vector_cleanup("raced-mkdir-never-recorded", lambda: os.rmdir(str(repo / "mk6")))
        # ---- the per-component term of the bound, pinned: the mkdir-to-stat window opens
        # once PER CREATED COMPONENT, so one moved-in empty directory per component (two
        # here, in one call) is recorded, each is removed by the sweep, and the directories
        # this call actually created survive under their displaced names (red with the
        # sweep's os.rmdir replaced by a no-op: both donors survive)
        donor_a = repo / "w2-donor-a"
        donor_a.mkdir()
        donor_a_st = os.lstat(str(donor_a))
        donor_b = repo / "w2-donor-b"
        donor_b.mkdir()
        donor_b_st = os.lstat(str(donor_b))
        percomp = {"count": 0}

        def per_component_mkdir(mp, *margs, **mkw):
            out = real_os_mkdir(mp, *margs, **mkw)
            if os.fspath(mp).endswith("w2a") and percomp["count"] == 0:
                percomp["count"] = 1
                os.rename(str(repo / "w2a"), str(repo / "w2a-displaced"))
                os.rename(str(donor_a), str(repo / "w2a"))
            elif os.fspath(mp).endswith("w2b") and percomp["count"] == 1:
                percomp["count"] = 2
                os.rename(str(repo / "w2a" / "w2b"), str(repo / "w2b-displaced"))
                os.rename(str(donor_b), str(repo / "w2a" / "w2b"))
            return out

        os.mkdir = per_component_mkdir
        try:
            percomp_made = _dirs_created(repo / "w2a" / "w2b")
        finally:
            os.mkdir = real_os_mkdir
        percomp_ids = [(dev, ino) for _p, dev, ino in percomp_made]
        left, skipped, unreach, _closes = _made_dirs_swept(percomp_made)
        if (percomp_ids != [(donor_b_st.st_dev, donor_b_st.st_ino),
                            (donor_a_st.st_dev, donor_a_st.st_ino)]
                or left or skipped or unreach or entry_state(repo / "w2a") is not None
                or entry_state(repo / "w2a-displaced") is None
                or entry_state(repo / "w2b-displaced") is None):
            failures.append("per-component-residual: one moved-in empty directory PER "
                            "created component must be recorded and removed, with the "
                            "created directories surviving displaced, got made={} left={} "
                            "skipped={}".format(percomp_made, left, skipped))
        vector_cleanup("per-component-residual", lambda: (
            os.rmdir(str(repo / "w2b-displaced")), os.rmdir(str(repo / "w2a-displaced"))))
        # ---- the identity-check-to-rmdir term of the bound, pinned: the sweep's re-check
        # and its rmdir are two calls, so an EMPTY directory swapped into the name between
        # them is removed (ownership is not guaranteed across the window) while the
        # recorded directory survives under its displaced name (red with the sweep's
        # os.rmdir replaced by a no-op: the swapped-in intruder survives)
        win_made = _dirs_created(repo / "win1")
        win_intruder = repo / "win1-intruder"
        win_intruder.mkdir()
        real_os_rmdir = os.rmdir
        winswap = {"fired": False}

        def window_swapping_rmdir(rp, *rargs, **rkw):
            if not winswap["fired"] and os.fspath(rp).endswith("win1"):
                winswap["fired"] = True
                os.rename(str(repo / "win1"), str(repo / "win1-original"))
                os.rename(str(win_intruder), str(repo / "win1"))
            return real_os_rmdir(rp, *rargs, **rkw)

        os.rmdir = window_swapping_rmdir
        try:
            left, skipped, unreach, _closes = _made_dirs_swept(win_made)
        finally:
            os.rmdir = real_os_rmdir
        if (left or skipped or unreach or entry_state(repo / "win1") is not None
                or entry_state(repo / "win1-original") is None):
            failures.append("sweep-window-empty-swap: the empty directory swapped in "
                            "between the identity re-check and the rmdir must be the one "
                            "removed, with the recorded directory surviving displaced, got "
                            "left={} skipped={}".format(left, skipped))
        vector_cleanup("sweep-window-empty-swap",
                       lambda: os.rmdir(str(repo / "win1-original")))
        # ---- the same window with a NON-EMPTY intruder: rmdir refuses it (each removal is
        # rmdir of an entry that is an EMPTY directory at removal time), the entry is left
        # in place and NAMED with the rmdir failure as its reason, and nothing beneath it
        # is removed (red with the sweep's os.rmdir replaced by a no-op: nothing is named;
        # red when the rmdir failure is named without its reason)
        win2_made = _dirs_created(repo / "win2")
        win2_intruder = repo / "win2-intruder"
        (win2_intruder / "payload").mkdir(parents=True)
        win2swap = {"fired": False}

        def window2_swapping_rmdir(rp, *rargs, **rkw):
            if not win2swap["fired"] and os.fspath(rp).endswith("win2"):
                win2swap["fired"] = True
                os.rename(str(repo / "win2"), str(repo / "win2-original"))
                os.rename(str(win2_intruder), str(repo / "win2"))
            return real_os_rmdir(rp, *rargs, **rkw)

        os.rmdir = window2_swapping_rmdir
        try:
            left, skipped, unreach, _closes = _made_dirs_swept(win2_made)
        finally:
            os.rmdir = real_os_rmdir
        if (len(left) != 1
                or not left[0].startswith(str(repo / "win2") + " (its rmdir failed: ")
                or "[Errno {}]".format(errno.ENOTEMPTY) not in left[0] or skipped or unreach
                or entry_state(repo / "win2" / "payload") is None
                or entry_state(repo / "win2-original") is None):
            failures.append("sweep-window-nonempty-named: a non-empty entry swapped in "
                            "between the identity re-check and the rmdir must be left in "
                            "place and named, its contents kept, got left={} "
                            "skipped={}".format(left, skipped))
        vector_cleanup("sweep-window-nonempty-named", lambda: (
            os.rmdir(str(repo / "win2" / "payload")), os.rmdir(str(repo / "win2")),
            os.rmdir(str(repo / "win2-original"))))
        # ---- an ancestor swapped for a SYMLINK while the sweep runs redirects nothing:
        # every removal goes through the held descriptor (the recorded tree, wherever a
        # rename moved it AFTER the sweep opened the recorded outer parent by path; a
        # rename before the sweep starts is not followed), never through a re-resolved
        # string path, so the victim tree the
        # symlink names survives and the swapped ancestor is left in place and named (red
        # with the sweep reverted to string paths: the victim's inner directory was removed
        # through the symlink)
        sw_made = _dirs_created(repo / "swx" / "deep")
        (repo / "victim" / "deep").mkdir(parents=True)
        sweepswap = {"fired": False}

        def sweep_swapping_rmdir(rp, *rargs, **rkw):
            if not sweepswap["fired"] and os.fspath(rp).endswith("deep"):
                sweepswap["fired"] = True
                os.rename(str(repo / "swx"), str(repo / "swx-aside"))
                os.symlink(str(repo / "victim"), str(repo / "swx"))
            return real_os_rmdir(rp, *rargs, **rkw)

        os.rmdir = sweep_swapping_rmdir
        try:
            left, skipped, unreach, _closes = _made_dirs_swept(sw_made)
        finally:
            os.rmdir = real_os_rmdir
        if (entry_state(repo / "victim" / "deep") is None or skipped or unreach
                or len(left) != 1
                or "no longer the directory this call created" not in left[0]
                or entry_state(repo / "swx-aside" / "deep") is not None
                or entry_state(repo / "swx-aside") is None):
            failures.append("sweep-ancestor-symlink-swap: the victim tree must survive, the "
                            "recorded inner component must be removed through the held "
                            "descriptor and the swapped ancestor left in place and named, "
                            "got left={} skipped={}".format(left, skipped))
        vector_cleanup("sweep-ancestor-symlink-swap", lambda: (
            os.unlink(str(repo / "swx")), os.rmdir(str(repo / "swx-aside")),
            os.rmdir(str(repo / "victim" / "deep")), os.rmdir(str(repo / "victim"))))
        # ---- O_NOFOLLOW on the creation descent carries its own vector: an intermediate
        # component swapped for a symlink between its identity read-back and the descent
        # open is REFUSED, and nothing is created through the symlink's target (red with
        # O_NOFOLLOW removed from the descent open, which follows the symlink and creates
        # the next component inside the target)
        x1_target = repo / "x1-target"
        x1_target.mkdir()
        real_os_stat = os.stat
        x1swap = {"fired": False}

        def x1_swapping_stat(sp, *sargs, **skw):
            out = real_os_stat(sp, *sargs, **skw)
            if not x1swap["fired"] and "dir_fd" in skw and os.fspath(sp) == "x1a":
                x1swap["fired"] = True
                os.rename(str(repo / "x1a"), str(repo / "x1a-real"))
                os.symlink(str(x1_target), str(repo / "x1a"))
            return out

        os.stat = x1_swapping_stat
        try:
            try:
                _dirs_created(repo / "x1a" / "x1b")
            except CannotEvaluate as exc:
                x1_refusal = str(exc)
            else:
                x1_refusal = None
        finally:
            os.stat = real_os_stat
        if (x1_refusal is None or "cannot be opened" not in x1_refusal
                or os.listdir(str(x1_target))
                or entry_state(repo / "x1a-real" / "x1b") is not None):
            failures.append("descent-nofollow-refuses-symlink: a raced intermediate symlink "
                            "must refuse the descent with nothing created through its "
                            "target, got refusal={} target-entries={}".format(
                                x1_refusal, os.listdir(str(x1_target))))
        vector_cleanup("descent-nofollow-refuses-symlink", lambda: (
            os.unlink(str(repo / "x1a")), os.rmdir(str(repo / "x1a-real")),
            os.rmdir(str(x1_target))))
        # ---- the no-follow identity read-back carries its own vector: a component swapped
        # for a symlink between the mkdir and the read-back records the ENTRY's own
        # identity (the symlink), never its target's, so the sweep refuses the
        # non-directory and the target survives (red with the read-back following
        # symlinks: the target's identity is recorded)
        x2_target = repo / "x2-target"
        x2_target.mkdir()
        x2_target_st = os.lstat(str(x2_target))
        x2swap = {"fired": False}

        def x2_swapping_mkdir(mp, *margs, **mkw):
            out = real_os_mkdir(mp, *margs, **mkw)
            if not x2swap["fired"] and os.fspath(mp) == "x2":
                x2swap["fired"] = True
                os.rename(str(repo / "x2"), str(repo / "x2-real"))
                os.symlink(str(x2_target), str(repo / "x2"))
            return out

        os.mkdir = x2_swapping_mkdir
        try:
            x2_made = _dirs_created(repo / "x2")
        finally:
            os.mkdir = real_os_mkdir
        x2_entry = os.lstat(str(repo / "x2"))
        left, skipped, unreach, _closes = _made_dirs_swept(x2_made)
        if (len(x2_made) != 1
                or (x2_made[0][1], x2_made[0][2]) != (x2_entry.st_dev, x2_entry.st_ino)
                or (x2_made[0][1], x2_made[0][2])
                == (x2_target_st.st_dev, x2_target_st.st_ino)
                or len(left) != 1
                or "no longer the directory this call created" not in left[0]
                or skipped or unreach or entry_state(x2_target) is None):
            failures.append("read-back-records-entry-not-target: the identity read-back "
                            "must record the entry at the created name, never a symlink "
                            "target, and the sweep must keep both, got made={} left={} "
                            "skipped={}".format(x2_made, left, skipped))
        vector_cleanup("read-back-records-entry-not-target", lambda: (
            os.unlink(str(repo / "x2")), os.rmdir(str(repo / "x2-real")),
            os.rmdir(str(x2_target))))
        # ---- every descriptor the creation walk opens is closed exactly once on the clean
        # path: the deepest existing ancestor plus one per descended component, plus one
        # absence-confirmation open of that ancestor per missing component (entry_state
        # confirms each ENOENT at the final name relative to an opened parent; the climbs
        # through the still-missing middle components open nothing). For three missing
        # components that is 3 walk descriptors + 3 confirmation descriptors. (Red with
        # the descent's os.close of the handed-over parent removed: one descriptor leaks
        # per descended component.)
        real_os_open = os.open
        real_os_close = os.close
        fd_opens, fd_live, fd_bad = [], set(), []

        def counting_open(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            fd_opens.append(out)
            fd_live.add(out)
            return out

        def counting_close(cfd):
            # a number this walk does not hold open (never opened, or closed already and
            # not reissued) is a double close; numbers are reused after a close, so the
            # live set, not the raw numbers, carries the exactly-once claim
            if cfd in fd_live:
                fd_live.discard(cfd)
            else:
                fd_bad.append(cfd)
            return real_os_close(cfd)

        os.open = counting_open
        os.close = counting_close
        try:
            x3_made = _dirs_created(repo / "x3a" / "x3b" / "x3c")
        finally:
            os.open = real_os_open
            os.close = real_os_close
        left, skipped, unreach, _closes = _made_dirs_swept(x3_made)
        if fd_bad or fd_live or len(fd_opens) != 6 or left or skipped or unreach:
            failures.append("creation-descriptors-closed-once: the walk must close every "
                            "descriptor it opened exactly once (the ancestor plus one per "
                            "descended component, plus one absence-confirmation open per "
                            "missing component), got opens={} still-open={} double={} "
                            "left={} skipped={}".format(fd_opens, fd_live, fd_bad, left,
                                                        skipped))
        # ---- the descent is descriptor-relative end to end: an ancestor renamed aside
        # after a component's read-back (its old pathname now naming a fresh foreign tree
        # with the same component name) cannot redirect the walk, so the next component is
        # created inside the directory this call opened and the foreign tree stays empty
        # (red with the descent open re-resolving the string path: the next component
        # lands inside the foreign tree)
        anc9 = repo / "anc9"
        anc9.mkdir()
        x4swap = {"fired": False}

        def x4_swapping_stat(sp, *sargs, **skw):
            out = real_os_stat(sp, *sargs, **skw)
            if not x4swap["fired"] and "dir_fd" in skw and os.fspath(sp) == "m1":
                x4swap["fired"] = True
                os.rename(str(anc9), str(repo / "anc9-aside"))
                real_os_mkdir(str(anc9))
                real_os_mkdir(str(anc9 / "m1"))
            return out

        os.stat = x4_swapping_stat
        try:
            x4_made = _dirs_created(anc9 / "m1" / "m2")
        finally:
            os.stat = real_os_stat
        x4_real_m2 = entry_state(repo / "anc9-aside" / "m1" / "m2")
        if (os.listdir(str(anc9 / "m1")) or x4_real_m2 is None or len(x4_made) != 2
                or (x4_made[0][1], x4_made[0][2])
                != (x4_real_m2.st_dev, x4_real_m2.st_ino)):
            failures.append("descent-by-descriptor-not-path: the walk must create and "
                            "record through its held descriptors, leaving the foreign tree "
                            "at the re-resolved path empty, got made={} "
                            "foreign-entries={}".format(
                                x4_made, os.listdir(str(anc9 / "m1"))))
        vector_cleanup("descent-by-descriptor-not-path", lambda: (
            os.rmdir(str(anc9 / "m1")), os.rmdir(str(anc9)),
            os.rmdir(str(repo / "anc9-aside" / "m1" / "m2")),
            os.rmdir(str(repo / "anc9-aside" / "m1")),
            os.rmdir(str(repo / "anc9-aside"))))
        # ---- a close failure in the creation walk is a named CannotEvaluate, raised only
        # after the sweep ran and with its outcome in the message, never a raw OSError
        # (red with the descent handoff close restored to a bare os.close: the injected
        # OSError escapes the walk unwrapped; this vector catches its own OSError, so
        # that failure is attributed here, never to the shared fixture handler)
        c3fail = {"fired": False}

        def _is_confirmation_fd(cfd):
            # entry_state's absence confirmation opens parents O_PATH; the creation walk's
            # own descriptors are plain directory opens, so the injections below target the
            # walk's first close, never the confirmation's (F_GETFL is one of the few
            # operations permitted on an O_PATH descriptor and reports that flag)
            try:
                return bool(fcntl.fcntl(cfd, fcntl.F_GETFL) & os.O_PATH)
            except OSError:
                return False

        def failing_close(cfd):
            if not c3fail["fired"] and not _is_confirmation_fd(cfd):
                c3fail["fired"] = True
                real_os_close(cfd)
                raise OSError(5, "injected close failure")
            return real_os_close(cfd)

        os.close = failing_close
        c3_refusal = c3_raw = None
        try:
            try:
                _dirs_created(repo / "c3a" / "c3b")
            except CannotEvaluate as exc:
                c3_refusal = str(exc)
            except OSError as exc:
                c3_raw = exc
        finally:
            os.close = real_os_close
        if (c3_raw is not None or c3_refusal is None
                or "could not be closed" not in c3_refusal
                or "removed" not in c3_refusal or entry_state(repo / "c3a") is not None):
            failures.append("close-failure-cannot-evaluate: a failing close in the creation "
                            "walk must raise CannotEvaluate naming the step and the sweep "
                            "outcome, never a raw OSError, with the created component "
                            "removed, got refusal={} raw={!r}".format(c3_refusal, c3_raw))
        vector_cleanup("close-failure-cannot-evaluate", lambda: (
            os.rmdir(str(repo / "c3a")) if entry_state(repo / "c3a") is not None
            else None))
        # ---- the FINAL close of the creation walk (the descriptor held on the created
        # register directory itself) is wrapped too: a SINGLE-component creation, whose
        # only close is that final one, gives a named CannotEvaluate only after the sweep
        # removed the component, never a raw OSError (red with that close restored to a
        # bare os.close; this vector catches its own OSError, so that failure is
        # attributed here, never to the shared fixture handler)
        c5fail = {"fired": False}

        def final_failing_close(cfd):
            if c5fail["fired"] or _is_confirmation_fd(cfd):
                return real_os_close(cfd)
            real_os_close(cfd)
            c5fail["fired"] = True
            raise OSError(5, "injected close failure")

        os.close = final_failing_close
        c5_refusal = c5_raw = None
        try:
            try:
                _dirs_created(repo / "c5")
            except CannotEvaluate as exc:
                c5_refusal = str(exc)
            except OSError as exc:
                c5_raw = exc
        finally:
            os.close = real_os_close
        if (c5_raw is not None or c5_refusal is None
                or "could not be closed" not in c5_refusal
                or "were removed" not in c5_refusal
                or entry_state(repo / "c5") is not None):
            failures.append("creation-final-close-wrapped: a single-component creation "
                            "whose final close fails must raise a named CannotEvaluate "
                            "after the sweep removed the component, never a raw OSError, "
                            "got refusal={} raw={!r}".format(c5_refusal, c5_raw))
        vector_cleanup("creation-final-close-wrapped", lambda: (
            os.rmdir(str(repo / "c5")) if entry_state(repo / "c5") is not None
            else None))
        # ---- a close failure inside the sweep is recorded as a CLOSE failure, KEPT
        # APART from the removal outcomes, with the removals still done and nothing
        # raised; the caller refusal clause (_made_dirs_note) renders it as a close
        # failure, never as a directory left in place (red with the sweep closes
        # unguarded, and red when a close failure is folded into the left-in-place list:
        # the note then claims a removed directory was left in place; this vector catches
        # its own OSError, so that failure is attributed here, never to the shared
        # fixture handler)
        c4_made = _dirs_created(repo / "c4a" / "c4b")
        c4fail = {"fired": False}

        def sweep_failing_close(cfd):
            real_os_close(cfd)
            if not c4fail["fired"]:
                c4fail["fired"] = True
                raise OSError(5, "injected close failure")

        os.close = sweep_failing_close
        c4_note = c4_raw = None
        try:
            try:
                c4_note = _made_dirs_note(c4_made)
            except OSError as exc:
                c4_raw = exc
        finally:
            os.close = real_os_close
        if (c4_raw is not None or c4_note is None
                or "could not be closed" not in c4_note
                or "not a directory left in place" not in c4_note
                or "could not be removed" in c4_note
                or "the directory components this call made were removed" not in c4_note
                or entry_state(repo / "c4a") is not None):
            failures.append("sweep-close-failure-recorded: a failing close in the sweep "
                            "must be rendered in the caller refusal clause as a close "
                            "failure with the removals still done, never as a directory "
                            "left in place and never raised, got note={} raw={!r}".format(
                                c4_note, c4_raw))
        vector_cleanup("sweep-close-failure-recorded", lambda: (
            os.rmdir(str(repo / "c4a" / "c4b")) if entry_state(repo / "c4a" / "c4b")
            is not None else None,
            os.rmdir(str(repo / "c4a")) if entry_state(repo / "c4a") is not None
            else None))
        # ---- a DISCONTINUOUS record: a raced mkdir by another actor wins the MIDDLE
        # component, so creation records the outer and the inner component but never the
        # middle; the sweep descends through the unrecorded middle BY NAME, removes the
        # recorded inner component, keeps the raced middle (nothing unrecorded is ever
        # removed) and NAMES the outer component it cannot remove; nothing is counted
        # already absent (red when the sweep assumes each recorded component is the
        # direct child of the previous one: the inner component is then reported absent
        # and left on disk)
        rmid = {"fired": False}

        def racing_middle_mkdir(mp, *margs, **mkw):
            if not rmid["fired"] and os.fspath(mp).endswith("rmb"):
                rmid["fired"] = True
                real_os_mkdir(mp, *margs, **mkw)  # another actor wins the race
            return real_os_mkdir(mp, *margs, **mkw)

        os.mkdir = racing_middle_mkdir
        try:
            rmid_made = _dirs_created(repo / "rma" / "rmb" / "rmc")
        finally:
            os.mkdir = real_os_mkdir
        left, skipped, unreach, closes = _made_dirs_swept(rmid_made)
        if ([m[0] for m in rmid_made] != [repo / "rma" / "rmb" / "rmc", repo / "rma"]
                or skipped or unreach or closes or len(left) != 1
                or not left[0].startswith(str(repo / "rma") + " (its rmdir failed: ")
                or entry_state(repo / "rma" / "rmb" / "rmc") is not None
                or entry_state(repo / "rma" / "rmb") is None):
            failures.append("raced-middle-component-swept: a component recorded behind a "
                            "raced unrecorded middle directory must be removed through "
                            "it, never counted absent, with the raced middle kept and the "
                            "non-empty outer component named, got made={} left={} "
                            "skipped={} closes={}".format(rmid_made, left, skipped,
                                                          closes))
        vector_cleanup("raced-middle-component-swept", lambda: (
            os.rmdir(str(repo / "rma" / "rmb")), os.rmdir(str(repo / "rma"))))
        # ---- a register path containing a dot-dot component records a discontinuous,
        # non-sibling chain too; the lexical descent reaches both recorded components
        # through the held descriptors and removes them (red when the sweep assumes
        # direct children: the component past the dot-dot is then reported absent and
        # left on disk)
        dd_made = _dirs_created(repo / "dd1" / ".." / "dd2")
        left, skipped, unreach, closes = _made_dirs_swept(dd_made)
        if (len(dd_made) != 2 or left or skipped or unreach or closes
                or entry_state(repo / "dd1") is not None
                or entry_state(repo / "dd2") is not None):
            failures.append("dotdot-register-path-swept: both recorded components of a "
                            "dot-dot register path must be removed, got made={} left={} "
                            "skipped={} closes={}".format(dd_made, left, skipped, closes))
        # ---- the sweep is ITERATIVE and its slots hold at most two descriptors at a time: a
        # 1500-component record (deeper than the default interpreter recursion limit and
        # deeper than the default descriptor limit) is created and swept completely (red
        # with a recursive sweep: RecursionError)
        deep_base = repo / "deepbase"
        deep_base.mkdir()
        deep_path = deep_base
        for _i in range(1500):
            deep_path = deep_path / "d"
        deep_made = []
        deep_err = deep_left = deep_skipped = deep_unreach = deep_closes = None
        try:
            deep_made = _dirs_created(deep_path)
            deep_left, deep_skipped, deep_unreach, deep_closes = _made_dirs_swept(deep_made)
        except RecursionError as exc:
            deep_err = exc
        if (deep_err is not None or len(deep_made) != 1500 or deep_left or deep_skipped
                or deep_unreach or deep_closes or entry_state(deep_base / "d") is not None):
            failures.append("deep-chain-iterative-sweep: a 1500-component record must be "
                            "created and swept completely, with no recursion limit and no "
                            "descriptor exhaustion, got err={!r} made={} left={} "
                            "skipped={} unreachable={} closes={}".format(
                                deep_err, len(deep_made), deep_left, deep_skipped,
                                deep_unreach, deep_closes))
        vector_cleanup("deep-chain-iterative-sweep", lambda: os.rmdir(str(deep_base)))
        # ---- every descriptor the sweep opens is closed exactly once even when a
        # non-OSError exception is injected mid-sweep: the finally unwinds the held
        # descriptors, the exception propagates, and live-set accounting (as in
        # creation-descriptors-closed-once) shows no leak and no double close (red
        # without the finally: every held descriptor leaks)
        real_os_fstat = os.fstat
        nleak_made = _dirs_created(repo / "nl1" / "nl2" / "nl3")
        fd_opens2, fd_live2, fd_bad2 = [], set(), []

        def counting_open2(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            fd_opens2.append(out)
            fd_live2.add(out)
            return out

        def counting_close2(cfd):
            if cfd in fd_live2:
                fd_live2.discard(cfd)
            else:
                fd_bad2.append(cfd)
            return real_os_close(cfd)

        nleak = {"count": 0}

        def nonos_fstat(ffd, *fargs, **fkw):
            if isinstance(ffd, int) and ffd in fd_live2:
                nleak["count"] += 1
                if nleak["count"] == 2:
                    raise ValueError("injected non-OSError mid-sweep")
            return real_os_fstat(ffd, *fargs, **fkw)

        os.open = counting_open2
        os.close = counting_close2
        os.fstat = nonos_fstat
        nleak_err = None
        try:
            try:
                _made_dirs_swept(nleak_made)
            except ValueError as exc:
                nleak_err = exc
        finally:
            os.open = real_os_open
            os.close = real_os_close
            os.fstat = real_os_fstat
        leaked = list(fd_live2)
        for lfd in leaked:
            try:
                real_os_close(lfd)
            except OSError:
                pass
        if nleak_err is None or leaked or fd_bad2 or len(fd_opens2) < 2:
            failures.append("sweep-nonoserror-no-descriptor-leak: a non-OSError raised "
                            "mid-sweep must propagate with every descriptor the sweep "
                            "opened closed exactly once, got err={!r} opens={} "
                            "still-open={} double={}".format(nleak_err, fd_opens2,
                                                             leaked, fd_bad2))
        vector_cleanup("sweep-nonoserror-no-descriptor-leak", lambda: (
            os.rmdir(str(repo / "nl1" / "nl2" / "nl3")),
            os.rmdir(str(repo / "nl1" / "nl2")), os.rmdir(str(repo / "nl1"))))
        # ---- the sweep descent identity re-check carries its own vector: a DIFFERENT
        # directory substituted at a recorded parent name (holding the directory the
        # call actually created, moved inside it) stops the descent; the components
        # beneath are left in place and named, never removed through the substitute (red
        # with the descent identity comparison dropped: the recorded inner component is
        # then removed out of the substitute)
        z1_made = _dirs_created(repo / "z1" / "z1c")
        os.rename(str(repo / "z1"), str(repo / "z1-aside"))
        (repo / "z1").mkdir()
        os.rename(str(repo / "z1-aside" / "z1c"), str(repo / "z1" / "z1c"))
        left, skipped, unreach, closes = _made_dirs_swept(z1_made)
        if (skipped or closes or len(left) != 1 or len(unreach) != 1
                or "is no longer the directory this call created" not in unreach[0]
                or "no longer the directory this call created" not in left[0]
                or entry_state(repo / "z1" / "z1c") is None
                or entry_state(repo / "z1-aside") is None):
            failures.append("sweep-descent-identity-substitution: a different directory "
                            "substituted at a recorded parent name must stop the descent, "
                            "the components beneath it named unreachable and the "
                            "substitute left in place and named, got left={} skipped={} "
                            "unreachable={} closes={}".format(left, skipped, unreach,
                                                              closes))
        vector_cleanup("sweep-descent-identity-substitution", lambda: (
            os.rmdir(str(repo / "z1" / "z1c")), os.rmdir(str(repo / "z1")),
            os.rmdir(str(repo / "z1-aside"))))
        # ---- O_NOFOLLOW on the sweep descent carries its own vector: the recorded
        # parent renamed aside and a SYMLINK to it left at its name refuses the descent,
        # and nothing is removed through the symlink target even though the target IS
        # the recorded directory (red with O_NOFOLLOW dropped from the descent open: the
        # open follows the symlink, the identity matches, and the inner component is
        # removed through it)
        z2_made = _dirs_created(repo / "z2" / "z2c")
        os.rename(str(repo / "z2"), str(repo / "z2-victim"))
        os.symlink(str(repo / "z2-victim"), str(repo / "z2"))
        left, skipped, unreach, closes = _made_dirs_swept(z2_made)
        if (skipped or closes or len(left) != 1 or len(unreach) != 1
                or "cannot be descended into" not in unreach[0]
                or "no longer the directory this call created" not in left[0]
                or entry_state(repo / "z2-victim" / "z2c") is None):
            failures.append("sweep-descent-nofollow-symlink: a symlink at a recorded "
                            "parent name must refuse the descent with nothing removed "
                            "through its target, got left={} skipped={} unreachable={} "
                            "closes={}".format(left, skipped, unreach, closes))
        vector_cleanup("sweep-descent-nofollow-symlink", lambda: (
            os.unlink(str(repo / "z2")), os.rmdir(str(repo / "z2-victim" / "z2c")),
            os.rmdir(str(repo / "z2-victim"))))
        # ---- the removal re-check runs relative to the HELD descriptor, never a
        # re-resolved string path, and that carries its own vector: after the descent,
        # the recorded parent is renamed aside with an impostor child inside it while a
        # fresh directory at the recorded string path receives the ORIGINAL recorded
        # child; the held-descriptor re-check sees the impostor and refuses, naming it
        # (red with the re-check re-resolving the string path: it sees the recorded
        # identity there, passes, and the rmdir through the held descriptor removes the
        # impostor)
        y1_made = _dirs_created(repo / "y1" / "y1c")
        (repo / "y1-imp").mkdir()
        y3swap = {"count": 0}

        def y3_swapping_fstat(ffd, *fargs, **fkw):
            out = real_os_fstat(ffd, *fargs, **fkw)
            y3swap["count"] += 1
            if y3swap["count"] == 2:
                os.rename(str(repo / "y1" / "y1c"), str(repo / "keepc"))
                os.rename(str(repo / "y1-imp"), str(repo / "y1" / "y1c"))
                os.rename(str(repo / "y1"), str(repo / "y1-aside"))
                real_os_mkdir(str(repo / "y1"))
                os.rename(str(repo / "keepc"), str(repo / "y1" / "y1c"))
            return out

        os.fstat = y3_swapping_fstat
        try:
            left, skipped, unreach, closes = _made_dirs_swept(y1_made)
        finally:
            os.fstat = real_os_fstat
        if (skipped or unreach or closes or len(left) != 2
                or "no longer the directory this call created" not in left[0]
                or "no longer the directory this call created" not in left[1]
                or entry_state(repo / "y1-aside" / "y1c") is None
                or entry_state(repo / "y1" / "y1c") is None):
            failures.append("sweep-recheck-by-descriptor-not-path: the removal re-check "
                            "must run relative to the held descriptor and keep the "
                            "impostor it sees there, got left={} skipped={} "
                            "closes={}".format(left, skipped, closes))
        vector_cleanup("sweep-recheck-by-descriptor-not-path", lambda: (
            os.rmdir(str(repo / "y1" / "y1c")), os.rmdir(str(repo / "y1")),
            os.rmdir(str(repo / "y1-aside" / "y1c")), os.rmdir(str(repo / "y1-aside"))))
        # ---- the sweep's report is THREE-VALUED: a recorded parent renamed away before
        # the sweep is reported absent by its OWN lookup, while the recorded component
        # inside it, never looked up, is named UNREACHABLE in each caller's message
        # (_made_dirs_note and _creation_cleanup), never folded into absence, the renamed
        # parent itself is named absent AT ITS OWN NAME, and neither caller says that no
        # component remains or that the components were removed (red when the entries
        # behind an absent recorded parent are counted absent: the message then says none
        # remains while the moved component survives; red when the absent parent is not
        # named as absent at its own name)
        rn1_made = _dirs_created(repo / "rn1a" / "rn1b")
        os.rename(str(repo / "rn1a"), str(repo / "rn1a-moved"))
        rn1_left, rn1_skipped, rn1_unreach, rn1_closes = _made_dirs_swept(rn1_made)
        rn1_note = _made_dirs_note(rn1_made)
        rn1_file = repo / "rn1-created.jsonl"
        with open(str(rn1_file), "xb") as rn1_fh:
            rn1_cleanup = _creation_cleanup(rn1_fh.fileno(), rn1_file, rn1_made)
        rn1_inner = str(repo / "rn1a" / "rn1b")
        rn1_absent = ("found absent at their own name, so possibly renamed away rather "
                      "than gone: {}".format(repo / "rn1a"))
        if (rn1_left or rn1_closes or rn1_skipped != [str(repo / "rn1a")]
                or len(rn1_unreach) != 1 or not rn1_unreach[0].startswith(rn1_inner + " (")
                or any("no directory component this call made remains" in m
                       or "could not be reached" not in m or rn1_inner not in m
                       or rn1_absent not in m
                       for m in (rn1_note, rn1_cleanup))
                or "were removed" in rn1_cleanup
                or entry_state(repo / "rn1a-moved" / "rn1b") is None
                or entry_state(rn1_file) is not None):
            failures.append("sweep-renamed-parent-continuous: a component behind a recorded "
                            "parent renamed away must be named unreachable in the caller "
                            "messages, never counted absent, got left={} skipped={} "
                            "unreachable={} closes={} note={!r} cleanup={!r}".format(
                                rn1_left, rn1_skipped, rn1_unreach, rn1_closes, rn1_note,
                                rn1_cleanup))
        vector_cleanup("sweep-renamed-parent-continuous", lambda: (
            os.rmdir(str(repo / "rn1a-moved" / "rn1b")),
            os.rmdir(str(repo / "rn1a-moved"))))
        # ---- the same with a DISCONTINUOUS record: a raced unrecorded middle sits between
        # the recorded outer and inner components, and the recorded outer component is
        # renamed away before the sweep; the inner component is named unreachable in BOTH
        # caller messages (_made_dirs_note and _creation_cleanup, the latter with an empty
        # created register it removes), never counted absent, and the outer component is
        # named absent at its own name (red as the continuous vector, and red when
        # _creation_cleanup alone drops the unreachable components from its message)
        rn2 = {"fired": False}

        def rn2_racing_mkdir(mp, *margs, **mkw):
            if not rn2["fired"] and os.fspath(mp).endswith("rn2b"):
                rn2["fired"] = True
                real_os_mkdir(mp, *margs, **mkw)  # another actor wins the race
            return real_os_mkdir(mp, *margs, **mkw)

        os.mkdir = rn2_racing_mkdir
        try:
            rn2_made = _dirs_created(repo / "rn2a" / "rn2b" / "rn2c")
        finally:
            os.mkdir = real_os_mkdir
        os.rename(str(repo / "rn2a"), str(repo / "rn2a-moved"))
        rn2_left, rn2_skipped, rn2_unreach, rn2_closes = _made_dirs_swept(rn2_made)
        rn2_note = _made_dirs_note(rn2_made)
        rn2_file = repo / "rn2-created.jsonl"
        with open(str(rn2_file), "xb") as rn2_fh:
            rn2_cleanup = _creation_cleanup(rn2_fh.fileno(), rn2_file, rn2_made)
        rn2_inner = str(repo / "rn2a" / "rn2b" / "rn2c")
        rn2_absent = ("found absent at their own name, so possibly renamed away rather "
                      "than gone: {}".format(repo / "rn2a"))
        if ([m[0] for m in rn2_made] != [repo / "rn2a" / "rn2b" / "rn2c", repo / "rn2a"]
                or rn2_left or rn2_closes or rn2_skipped != [str(repo / "rn2a")]
                or len(rn2_unreach) != 1 or not rn2_unreach[0].startswith(rn2_inner + " (")
                or any("no directory component this call made remains" in m
                       or "could not be reached" not in m or rn2_inner not in m
                       or rn2_absent not in m
                       for m in (rn2_note, rn2_cleanup))
                or not rn2_cleanup.startswith("the created register was removed; ")
                or "were removed" in rn2_cleanup
                or entry_state(repo / "rn2a-moved" / "rn2b" / "rn2c") is None
                or entry_state(rn2_file) is not None):
            failures.append("sweep-renamed-parent-discontinuous: a component recorded behind "
                            "a raced middle under a recorded parent renamed away must be "
                            "named unreachable in both caller messages, never counted "
                            "absent, got made={} left={} skipped={} unreachable={} closes={} "
                            "note={!r} cleanup={!r}".format(
                                rn2_made, rn2_left, rn2_skipped, rn2_unreach, rn2_closes,
                                rn2_note, rn2_cleanup))
        vector_cleanup("sweep-renamed-parent-discontinuous", lambda: (
            os.rmdir(str(repo / "rn2a-moved" / "rn2b" / "rn2c")),
            os.rmdir(str(repo / "rn2a-moved" / "rn2b")),
            os.rmdir(str(repo / "rn2a-moved"))))
        # ---- a recorded entry RENAMED AWAY ITSELF is found absent by its own lookup, which
        # proves absence at its own name only: each caller message (_made_dirs_note, and
        # _creation_cleanup with an empty created register it removes) says no component
        # remains AT ITS OWN NAME and names the absent entry, never that it is gone, for a
        # single recorded entry renamed away and for a two-entry chain whose inner entry
        # moved out (its emptied outer entry removed); each caller sweeps a fresh record,
        # and the moved entry survives (red when a found-absent entry is said to remain
        # nowhere: "no directory component this call made remains")
        for ra_case in ("single", "inner-moved-out"):
            for ra_caller in ("note", "cleanup"):
                ra_tag = "sweep-renamed-entry-{}-{}".format(ra_case, ra_caller)
                ra_base = repo / "ra-{}-{}".format(ra_case, ra_caller)
                ra_absent = ra_base if ra_case == "single" else ra_base / "inner"
                ra_moved = repo / "ra-{}-{}-moved".format(ra_case, ra_caller)
                ra_made = _dirs_created(ra_absent)
                os.rename(str(ra_absent), str(ra_moved))
                ra_want = ("; no directory component this call made remains at its own name "
                           "(found absent there, so possibly renamed away rather than gone: "
                           "{})".format(ra_absent))
                ra_file = repo / "ra-{}-{}.jsonl".format(ra_case, ra_caller)
                if ra_caller == "note":
                    ra_got = _made_dirs_note(ra_made)
                else:
                    ra_want = "the created register was removed" + ra_want
                    with open(str(ra_file), "xb") as ra_fh:
                        ra_got = _creation_cleanup(ra_fh.fileno(), ra_file, ra_made)
                if (len(ra_made) != (1 if ra_case == "single" else 2) or ra_got != ra_want
                        or entry_state(ra_base) is not None or entry_state(ra_moved) is None
                        or entry_state(ra_file) is not None):
                    failures.append("{}: an entry renamed away must be named absent at its "
                                    "own name, never gone, with the moved entry surviving, "
                                    "got made={} message={!r}".format(ra_tag, ra_made,
                                                                      ra_got))
                vector_cleanup(ra_tag, lambda: os.rmdir(str(ra_moved)))
        # ---- the slot rule at the DESCENT handoff: a close that SUCCEEDS and then raises a
        # non-OSError after its number was reissued (duplicated here onto the null device,
        # standing in for another thread's open) must leave that replacement descriptor
        # closed ZERO times, with the sweep's other descriptor closed once (red when the
        # handoff closes before re-binding its slots: the finally closes the old slot
        # again, which by then is the replacement)
        hr_made = _dirs_created(repo / "hra" / "hrb")
        hr = {"closed": None, "after": []}

        def hr_reusing_close(cfd):
            if hr["closed"] is None:
                hr["closed"] = cfd
                real_os_close(cfd)
                null_fd = real_os_open(os.devnull, os.O_RDONLY)
                if null_fd != cfd:
                    os.dup2(null_fd, cfd)
                    real_os_close(null_fd)
                raise ValueError("injected non-OSError after a completed close")
            hr["after"].append(cfd)
            return real_os_close(cfd)

        os.close = hr_reusing_close
        hr_err = None
        try:
            try:
                _made_dirs_swept(hr_made)
            except ValueError as exc:
                hr_err = exc
        finally:
            os.close = real_os_close
        hr_null = os.stat(os.devnull)
        try:
            hr_rep = os.fstat(hr["closed"]) if hr["closed"] is not None else None
        except OSError:
            hr_rep = None
        if (hr_err is None or hr_rep is None or hr["closed"] in hr["after"]
                or len(hr["after"]) != 1
                or (hr_rep.st_dev, hr_rep.st_ino) != (hr_null.st_dev, hr_null.st_ino)):
            failures.append("sweep-close-handoff-reuse-nonoserror: a handoff close that "
                            "raises a non-OSError after its number was reissued must "
                            "close the replacement zero times and the other descriptor "
                            "once, got err={!r} reissued={} later-closes={} "
                            "replacement-open={}".format(hr_err, hr["closed"], hr["after"],
                                                         hr_rep is not None))
        if hr_rep is not None:
            real_os_close(hr["closed"])
        vector_cleanup("sweep-close-handoff-reuse-nonoserror", lambda: (
            os.rmdir(str(repo / "hra" / "hrb")), os.rmdir(str(repo / "hra"))))

        # ---- the same slot rule at the CLIMB handoff and at the STOPPED-DESCENT release,
        # one vector each: the close at that site succeeds, its number is reissued (onto
        # the null device) and a non-OSError is raised; the replacement must be closed ZERO
        # times and the sweep's one other held descriptor once. Each vector proves it hit
        # its site by the identity of the descriptor being closed: the climb handoff closes
        # the descriptor on the recorded outer component after the inner one was removed,
        # and the stopped-descent release closes the descriptor the stopped step opened on
        # a SUBSTITUTED recorded parent (red when that site closes before re-binding or
        # clearing its slot: the finally then closes the replacement)
        def slot_reuse_vector(sr_tag, sr_made, sr_nth, sr_site_path):
            sr = {"count": 0, "closed": None, "site": None, "after": []}

            def sr_reusing_close(cfd):
                sr["count"] += 1
                if sr["count"] == sr_nth:
                    sr["closed"] = cfd
                    sr_st = real_os_fstat(cfd)
                    sr["site"] = (sr_st.st_dev, sr_st.st_ino)
                    real_os_close(cfd)
                    sr_null_fd = real_os_open(os.devnull, os.O_RDONLY)
                    if sr_null_fd != cfd:
                        os.dup2(sr_null_fd, cfd)
                        real_os_close(sr_null_fd)
                    raise ValueError("injected non-OSError after a completed close")
                if sr["closed"] is not None:
                    sr["after"].append(cfd)
                return real_os_close(cfd)

            sr_site_st = os.lstat(str(sr_site_path))
            os.close = sr_reusing_close
            sr_err = None
            try:
                try:
                    _made_dirs_swept(sr_made)
                except ValueError as exc:
                    sr_err = exc
            finally:
                os.close = real_os_close
            sr_null = os.stat(os.devnull)
            try:
                sr_rep = os.fstat(sr["closed"]) if sr["closed"] is not None else None
            except OSError:
                sr_rep = None
            if (sr_err is None or sr_rep is None or sr["closed"] in sr["after"]
                    or len(sr["after"]) != 1
                    or sr["site"] != (sr_site_st.st_dev, sr_site_st.st_ino)
                    or (sr_rep.st_dev, sr_rep.st_ino) != (sr_null.st_dev, sr_null.st_ino)):
                failures.append("{}: a close at this site that raises a non-OSError after "
                                "its number was reissued must close the replacement zero "
                                "times and the other descriptor once, got err={!r} "
                                "reissued={} site-matched={} later-closes={} "
                                "replacement-open={}".format(
                                    sr_tag, sr_err, sr["closed"],
                                    sr["site"] == (sr_site_st.st_dev, sr_site_st.st_ino),
                                    sr["after"], sr_rep is not None))
            if sr_rep is not None:
                real_os_close(sr["closed"])

        # the climb handoff: a two-component sweep closes the outer parent at the descent
        # handoff (first close), then, with the inner component removed, closes the
        # descriptor on the outer component at the climb handoff (second close)
        ch_made = _dirs_created(repo / "cha" / "chb")
        slot_reuse_vector("sweep-close-climb-reuse-nonoserror", ch_made, 2, repo / "cha")
        if entry_state(repo / "cha" / "chb") is not None or entry_state(repo / "cha") is None:
            failures.append("sweep-close-climb-reuse-nonoserror: the injected close must be "
                            "the climb handoff, after the inner component's removal and "
                            "before the outer component's")
        vector_cleanup("sweep-close-climb-reuse-nonoserror", lambda: (
            os.rmdir(str(repo / "cha" / "chb")) if entry_state(repo / "cha" / "chb")
            is not None else None, os.rmdir(str(repo / "cha"))))
        # the stopped-descent release: the recorded parent is substituted by a fresh
        # directory, so the descent's first step opens it, finds the identity changed and
        # stops holding that descriptor; its release is the sweep's first close
        sd_made = _dirs_created(repo / "sra" / "srb")
        os.rename(str(repo / "sra"), str(repo / "sra-aside"))
        (repo / "sra").mkdir()
        slot_reuse_vector("sweep-close-stopped-descent-reuse-nonoserror", sd_made, 1,
                          repo / "sra")
        vector_cleanup("sweep-close-stopped-descent-reuse-nonoserror", lambda: (
            os.rmdir(str(repo / "sra")), os.rmdir(str(repo / "sra-aside" / "srb")),
            os.rmdir(str(repo / "sra-aside"))))
        # ---- the finally attempts EVERY slot still held: a non-OSError injected at the
        # descent's fstat leaves both slots held, and the first slot's close raising a
        # non-OSError (after its real close) must not skip the other slot's close
        # (live-set accounting: nothing leaks and nothing is closed twice; red with the
        # two final closes in one block: the second slot's descriptor leaks)
        fsl_made = _dirs_created(repo / "fsla" / "fslb")
        fsl_opens, fsl_live, fsl_bad = [], set(), []
        fsl = {"fstat": 0, "close": 0}

        def fsl_open(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            fsl_opens.append(out)
            fsl_live.add(out)
            return out

        def fsl_close(cfd):
            if cfd in fsl_live:
                fsl_live.discard(cfd)
            else:
                fsl_bad.append(cfd)
            real_os_close(cfd)
            fsl["close"] += 1
            if fsl["close"] == 1:
                raise ValueError("injected non-OSError from a final close")

        def fsl_fstat(ffd, *fargs, **fkw):
            fsl["fstat"] += 1
            if fsl["fstat"] == 2:
                raise RuntimeError("injected non-OSError at the descent")
            return real_os_fstat(ffd, *fargs, **fkw)

        os.open = fsl_open
        os.close = fsl_close
        os.fstat = fsl_fstat
        fsl_err = None
        try:
            try:
                _made_dirs_swept(fsl_made)
            except (RuntimeError, ValueError) as exc:
                fsl_err = exc
        finally:
            os.open = real_os_open
            os.close = real_os_close
            os.fstat = real_os_fstat
        fsl_leaked = list(fsl_live)
        for fsl_lfd in fsl_leaked:
            try:
                real_os_close(fsl_lfd)
            except OSError:
                pass
        if fsl_err is None or fsl_leaked or fsl_bad or len(fsl_opens) != 2:
            failures.append("sweep-final-slot-close-exception: an exception from one final "
                            "slot's close must not skip the other slot's close, got err={!r} "
                            "opens={} still-open={} double={}".format(
                                fsl_err, fsl_opens, fsl_leaked, fsl_bad))
        vector_cleanup("sweep-final-slot-close-exception", lambda: (
            os.rmdir(str(repo / "fsla" / "fslb")), os.rmdir(str(repo / "fsla"))))
        # ---- the climb-back identity comparison carries its own vector: once the inner
        # component is removed, the recorded outer component is moved into another
        # directory while the sweep holds it, so the dot-dot climb lands in that other
        # directory; the comparison with the identity observed on the way down refuses,
        # the outer component is named unreachable and survives (red with the comparison
        # dropped, or with the climbed-to identity taken from the expectation instead of
        # an fstat: the sweep then finds the moved component by name in the wrong
        # directory and removes it)
        cbi_made = _dirs_created(repo / "cbia" / "cbib")
        (repo / "cbi-other").mkdir()
        cbi = {"fired": False}

        def cbi_moving_rmdir(rp, *rargs, **rkw):
            out = real_os_rmdir(rp, *rargs, **rkw)
            if not cbi["fired"] and os.fspath(rp) == "cbib":
                cbi["fired"] = True
                os.rename(str(repo / "cbia"), str(repo / "cbi-other" / "cbia"))
            return out

        os.rmdir = cbi_moving_rmdir
        try:
            left, skipped, unreach, closes = _made_dirs_swept(cbi_made)
        finally:
            os.rmdir = real_os_rmdir
        if (left or skipped or closes or len(unreach) != 1 or not cbi["fired"]
                or not unreach[0].startswith(str(repo / "cbia") + " (")
                or "moved while the sweep ran" not in unreach[0]
                or entry_state(repo / "cbi-other" / "cbia") is None
                or entry_state(repo / "cbi-other" / "cbia" / "cbib") is not None):
            failures.append("sweep-climb-identity-substitution: a climb that lands in a "
                            "directory other than the one observed on the way down must "
                            "stop, naming the outer component unreachable and removing "
                            "nothing there, got left={} skipped={} unreachable={} "
                            "closes={}".format(left, skipped, unreach, closes))
        vector_cleanup("sweep-climb-identity-substitution", lambda: (
            os.rmdir(str(repo / "cbi-other" / "cbia")), os.rmdir(str(repo / "cbi-other"))))
        # ---- a FAILING climb inspection carries its own vector: the fstat of the
        # directory the climb reached fails, so the sweep stops there, names the outer
        # component unreachable (never removed, never absent) and closes every descriptor
        # it opened exactly once (red when the climb proceeds without its own fstat: the
        # outer component is then removed unverified)
        cbf_made = _dirs_created(repo / "cbfa" / "cbfb")
        cbf_opens, cbf_live, cbf_bad = [], set(), []
        cbf = {"fstat": 0}

        def cbf_open(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            cbf_opens.append(out)
            cbf_live.add(out)
            return out

        def cbf_close(cfd):
            if cfd in cbf_live:
                cbf_live.discard(cfd)
            else:
                cbf_bad.append(cfd)
            return real_os_close(cfd)

        def cbf_fstat(ffd, *fargs, **fkw):
            cbf["fstat"] += 1
            if cbf["fstat"] == 3:
                raise OSError(5, "injected input-output error")
            return real_os_fstat(ffd, *fargs, **fkw)

        os.open = cbf_open
        os.close = cbf_close
        os.fstat = cbf_fstat
        try:
            left, skipped, unreach, closes = _made_dirs_swept(cbf_made)
        finally:
            os.open = real_os_open
            os.close = real_os_close
            os.fstat = real_os_fstat
        cbf_leaked = list(cbf_live)
        for cbf_lfd in cbf_leaked:
            try:
                real_os_close(cbf_lfd)
            except OSError:
                pass
        if (left or skipped or closes or len(unreach) != 1 or cbf_leaked or cbf_bad
                or not unreach[0].startswith(str(repo / "cbfa") + " (")
                or "climbed back to cannot be inspected" not in unreach[0]
                or entry_state(repo / "cbfa") is None
                or entry_state(repo / "cbfa" / "cbfb") is not None):
            failures.append("sweep-climb-inspection-failure: a climb whose inspection fails "
                            "must stop, naming the outer component unreachable, with every "
                            "descriptor closed once, got left={} skipped={} unreachable={} "
                            "closes={} still-open={} double={}".format(
                                left, skipped, unreach, closes, cbf_leaked, cbf_bad))
        vector_cleanup("sweep-climb-inspection-failure",
                       lambda: os.rmdir(str(repo / "cbfa")))
        # ---- the two-descriptor ceiling is MEASURED on the CLEAN path (no close fails, so
        # every live descriptor is one a slot holds): live-set accounting over a sweep of
        # a five-component record records the peak number of sweep descriptors open at
        # once, which must be at most two, with every one closed exactly once and the
        # whole record removed (red when the sweep holds one more descriptor at any
        # point: the measured peak is three)
        pk_made = _dirs_created(repo / "pk1" / "pk2" / "pk3" / "pk4" / "pk5")
        pk_opens, pk_live, pk_bad = [], set(), []
        pk = {"peak": 0}

        def pk_open(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            pk_opens.append(out)
            pk_live.add(out)
            pk["peak"] = max(pk["peak"], len(pk_live))
            return out

        def pk_close(cfd):
            if cfd in pk_live:
                pk_live.discard(cfd)
            else:
                pk_bad.append(cfd)
            return real_os_close(cfd)

        os.open = pk_open
        os.close = pk_close
        try:
            left, skipped, unreach, closes = _made_dirs_swept(pk_made)
        finally:
            os.open = real_os_open
            os.close = real_os_close
        pk_leaked = list(pk_live)
        for pk_lfd in pk_leaked:
            try:
                real_os_close(pk_lfd)
            except OSError:
                pass
        if (pk["peak"] > 2 or len(pk_opens) < 2 or pk_leaked or pk_bad or left or skipped
                or unreach or closes or entry_state(repo / "pk1") is not None):
            failures.append("sweep-peak-two-descriptors: the sweep must hold at most two "
                            "descriptors at once and close each exactly once, got peak={} "
                            "opens={} still-open={} double={} left={} skipped={} "
                            "unreachable={} closes={}".format(
                                pk["peak"], pk_opens, pk_leaked, pk_bad, left, skipped,
                                unreach, closes))
        vector_cleanup("sweep-peak-two-descriptors", lambda: [
            os.rmdir(str(p)) for p, _dev, _ino in pk_made if entry_state(p) is not None])
        # ---- the same peak with the descent STOPPED at depth one: the middle recorded
        # component is substituted by a fresh empty directory, so the descent stops on it
        # holding the descriptor its step opened, the stopped-descent release closes that
        # descriptor before the climb opens another, and the peak stays at most two with
        # every descriptor closed exactly once; the substitute is left in place and named,
        # the component behind it named unreachable, and the outer component, no longer
        # empty, left in place and named with its rmdir failure as its reason (red with the
        # stopped-descent release removed: the climb then opens a third descriptor and
        # the held one leaks)
        sd2_made = _dirs_created(repo / "sd1" / "sd2" / "sd3")
        os.rename(str(repo / "sd1" / "sd2"), str(repo / "sd2-aside"))
        (repo / "sd1" / "sd2").mkdir()
        sd2_opens, sd2_live, sd2_bad = [], set(), []
        sd2 = {"peak": 0}

        def sd2_open(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            sd2_opens.append(out)
            sd2_live.add(out)
            sd2["peak"] = max(sd2["peak"], len(sd2_live))
            return out

        def sd2_close(cfd):
            if cfd in sd2_live:
                sd2_live.discard(cfd)
            else:
                sd2_bad.append(cfd)
            return real_os_close(cfd)

        os.open = sd2_open
        os.close = sd2_close
        try:
            left, skipped, unreach, closes = _made_dirs_swept(sd2_made)
        finally:
            os.open = real_os_open
            os.close = real_os_close
        sd2_leaked = list(sd2_live)
        for sd2_lfd in sd2_leaked:
            try:
                real_os_close(sd2_lfd)
            except OSError:
                pass
        if (sd2["peak"] > 2 or len(sd2_opens) != 4 or sd2_leaked or sd2_bad or skipped
                or closes or len(left) != 2
                or left[0] != "{} (no longer the directory this call created)".format(
                    repo / "sd1" / "sd2")
                or not left[1].startswith(str(repo / "sd1") + " (its rmdir failed: ")
                or "[Errno {}]".format(errno.ENOTEMPTY) not in left[1]
                or len(unreach) != 1
                or not unreach[0].startswith(str(repo / "sd1" / "sd2" / "sd3") + " (")
                or entry_state(repo / "sd2-aside" / "sd3") is None):
            failures.append("sweep-peak-stopped-descent: a descent stopped at depth one must "
                            "release its held descriptor before the climb, keeping the peak "
                            "at most two with each descriptor closed once, got peak={} "
                            "opens={} still-open={} double={} left={} skipped={} "
                            "unreachable={} closes={}".format(
                                sd2["peak"], sd2_opens, sd2_leaked, sd2_bad, left, skipped,
                                unreach, closes))
        vector_cleanup("sweep-peak-stopped-descent", lambda: (
            os.rmdir(str(repo / "sd1" / "sd2")), os.rmdir(str(repo / "sd1")),
            os.rmdir(str(repo / "sd2-aside" / "sd3")), os.rmdir(str(repo / "sd2-aside"))))
        # ---- a close that reports failure WITHOUT releasing its descriptor (close(2):
        # after a close interrupted by a signal, POSIX.1-2008 leaves the descriptor's state
        # unspecified and POSIX.1-2024 keeps it open, while Linux releases the number even
        # then) is the case the slots' bound is stated for: the descent handoff's
        # close fails that way here, the sweep reports it as a close failure, never
        # attempts that number again, and finishes the removals; the slots still hold at
        # most two descriptors at once (held = opened and no close attempted yet), every
        # descriptor's close is attempted exactly once, and the failed one stays open
        # OUTSIDE the slots, so the live count reaches three, two plus the one close
        # failure reported (red when a failed close is retried: that number is then
        # attempted twice)
        ncr_made = _dirs_created(repo / "ncra" / "ncrb")
        ncr_opens, ncr_held, ncr_live, ncr_attempts = [], set(), set(), []
        ncr = {"failed": None, "held_peak": 0, "live_peak": 0}

        def ncr_open(*oargs, **okw):
            out = real_os_open(*oargs, **okw)
            ncr_opens.append(out)
            ncr_held.add(out)
            ncr_live.add(out)
            ncr["held_peak"] = max(ncr["held_peak"], len(ncr_held))
            ncr["live_peak"] = max(ncr["live_peak"], len(ncr_live))
            return out

        def ncr_close(cfd):
            ncr_attempts.append(cfd)
            ncr_held.discard(cfd)
            if ncr["failed"] is None:
                ncr["failed"] = cfd
                raise OSError(5, "injected close failure that left the descriptor open")
            ncr_live.discard(cfd)
            return real_os_close(cfd)

        os.open = ncr_open
        os.close = ncr_close
        try:
            left, skipped, unreach, closes = _made_dirs_swept(ncr_made)
        finally:
            os.open = real_os_open
            os.close = real_os_close
        ncr_left_open = sorted(ncr_live)
        for ncr_lfd in ncr_left_open:
            try:
                real_os_close(ncr_lfd)
            except OSError:
                pass
        if (ncr["failed"] is None or ncr_attempts.count(ncr["failed"]) != 1
                or sorted(ncr_attempts) != sorted(ncr_opens) or ncr_held
                or ncr["held_peak"] > 2 or ncr_left_open != [ncr["failed"]]
                or ncr["live_peak"] > 2 + len(closes)
                or len(closes) != 1 or "left the descriptor open" not in closes[0]
                or str(repo) not in closes[0]
                or left or skipped or unreach or entry_state(repo / "ncra") is not None):
            failures.append("sweep-close-failure-not-released: a close that fails without "
                            "releasing its descriptor must be reported once and never "
                            "retried, with the removals done and the slots holding at most "
                            "two, got failed={} attempts={} opens={} held-peak={} "
                            "live-peak={} left-open={} closes={} left={} skipped={} "
                            "unreachable={}".format(
                                ncr["failed"], ncr_attempts, ncr_opens, ncr["held_peak"],
                                ncr["live_peak"], ncr_left_open, closes, left, skipped,
                                unreach))
        vector_cleanup("sweep-close-failure-not-released", lambda: [
            os.rmdir(str(p)) for p, _dev, _ino in ncr_made if entry_state(p) is not None])
        # ---- a sweep close failure reached through _creation_cleanup is rendered in THAT
        # caller's returned message as a close failure, kept apart from the removal
        # outcomes, with the created register and the components removed and nothing
        # raised (red with _creation_cleanup's close-failure rendering suppressed: its
        # message then hides the failure)
        ccf_made = _dirs_created(repo / "ccfa" / "ccfb")
        ccf_file = repo / "ccfa" / "ccfb" / "ccf.jsonl"
        ccf = {"fired": False}

        def ccf_failing_close(cfd):
            real_os_close(cfd)
            if not ccf["fired"]:
                ccf["fired"] = True
                raise OSError(5, "injected close failure")

        ccf_msg = ccf_raw = None
        with open(str(ccf_file), "xb") as ccf_fh:
            os.close = ccf_failing_close
            try:
                try:
                    ccf_msg = _creation_cleanup(ccf_fh.fileno(), ccf_file, ccf_made)
                except OSError as exc:
                    ccf_raw = exc
            finally:
                os.close = real_os_close
        if (ccf_raw is not None or ccf_msg is None
                or not ccf_msg.startswith("the created register and the directory "
                                          "components this call made were removed")
                or "descriptor close failure(s) in the sweep, not a directory left in "
                   "place" not in ccf_msg
                or "injected close failure" not in ccf_msg
                or "could not be removed" in ccf_msg
                or entry_state(repo / "ccfa") is not None):
            failures.append("creation-cleanup-sweep-close-failure: a sweep close failure "
                            "must be rendered in _creation_cleanup's message as a close "
                            "failure with the removals done, got msg={!r} raw={!r}".format(
                                ccf_msg, ccf_raw))
        vector_cleanup("creation-cleanup-sweep-close-failure", lambda: [
            os.rmdir(str(p)) for p, _dev, _ino in ccf_made if entry_state(p) is not None])
        # an anchor lstat failure that is NOT absence (EIO here) is cannot-evaluate, never the
        # missing-anchor allowance and never an append past an uninspectable authority
        declare(mistakes_register="anchio.jsonl")
        anchio = repo / "anchio.jsonl"
        accepted("anchor-inspect-seed", anchio, "MR-1", bootstrap=True)
        update_anchor(anchio, anchio.read_text(encoding="utf-8").splitlines())
        anchio_anchor = str(anchio) + ".anchor"
        real_os_lstat = os.lstat

        def eio_lstat(p, *largs, **lkw):
            if str(p) == anchio_anchor:
                raise OSError(5, "injected input-output error")
            return real_os_lstat(p, *largs, **lkw)

        os.lstat = eio_lstat
        try:
            rc, err = append(anchio, "MR-1", "accepted")
        finally:
            os.lstat = real_os_lstat
        if rc != 2 or "cannot be inspected" not in err:
            failures.append("anchor-lstat-eio-exit-2: want exit 2 naming the inspection, got "
                            "exit {}: {}".format(rc, err.strip()))
        if len(_lines(anchio)) != 1:
            failures.append("anchor-lstat-eio-exit-2: the refused append changed the register")
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
          "register descriptor, a symlinked register path (probed alarm-bounded, aliasing a "
          "FIFO, so a follow would block), a register path that is not a regular file (a "
          "FIFO, a directory and a Unix socket, each refused by the appender and by the "
          "projection with a "
          "diagnostic naming the file type, each alarm-bounded so a blocking regression "
          "fails rather than hangs, the FIFO entry, the directory and the socket entry left "
          "unchanged, while "
          "a regular register still appends and still projects under the same bound, the "
          "projected payload itself asserted, envelope, open item and source locator, and a "
          "symlink to a regular register still projecting the target's own items), a FIFO "
          "at the registry path (refused naming the registry and the file type, nothing "
          "created, while a regular registry still reads and a clean append lands under the "
          "same bound), a register the projection cannot decode (refused naming the read, "
          "never a traceback), a symlink to a socket at the projected register path "
          "(refused naming the TARGET's type, a socket, never the link's own numeric "
          "mode), a descriptor failure after the projection's open succeeded (fstat, read "
          "and close each injected to report ENOENT, each refused naming the open "
          "descriptor, never a successful empty projection, while a genuinely absent "
          "register still projects as no items), a projected register path that cannot be "
          "opened for another reason (a path through a non-directory) or is not a valid "
          "path value (an embedded NUL), each refused naming the read, never an escaped "
          "error, a register line the projection cannot parse or shape (a line that is "
          "not JSON, a line nested too deeply for the JSON decoder, balanced and "
          "unbalanced, with nothing printed on stdout, a row that is not a JSON object, a "
          "row whose id is not a string, each refused "
          "with exit 2 naming the row, never a traceback and never exit 1), "
          "an undeclared register, an appender "
          "installed outside any git repository (subprocess vector, never a cwd-derived root, "
          "run through a test-only search ceiling at the fixture's temporary directory, so a "
          "git checkout around it cannot decide the verdict, the ceiling's control still "
          "finding an install's own marker below it), "
          "a gate root git itself does not confirm as a repository toplevel (an invalid "
          "gitfile, an empty .git directory, a dangling .git symlink selected by the shared "
          "marker walk and never walked past, a nested empty .git directory refused by the "
          "realpath comparison, and a caller GIT_DIR or GIT_DIR plus GIT_WORK_TREE pointed "
          "at a valid repository included, each in subprocess vectors; a valid linked "
          "worktree appends cleanly, and the scrubbed environment keeps a caller "
          "GIT_INDEX_FILE or GIT_DIR from reaching the root probe or any authority read, "
          "probed by a clean append under both), a registry entry or declared register "
          "path whose inspection fails with "
          "anything but ENOENT (EIO and EACCES included; a dangling registry symlink is "
          "selected and refused, never the lower-priority registry file), a malformed "
          "registry declaration (a number, a list, an empty string, null, each with its "
          "named per-key refusal), conflicting declarations resolving to one "
          "path (two hard links to one inode included), a malformed, unreadable or unusable "
          "anchor entry (a dangling symlink, a directory and an anchor lstat failure that is "
          "not ENOENT included), a git failure deciding whether the register is tracked, a "
          "tracked register with no computable merge-base, a register directory that cannot "
          "be created (an uninspectable ancestor refuses before any mkdir and a "
          "pre-existing parent is never removed), a register replaced or unlinked between "
          "validation and write, a first "
          "row interleaved between the exclusive creation and the lock (the interleaved row "
          "survives, the creator refuses) or displaced from its path in that window (the "
          "identity re-check refuses, the displacing file receives no row) or written into "
          "there by a writer outside the lock (the size re-check refuses, the writer's bytes "
          "stay), a lock that "
          "cannot be acquired (the created empty file is left in place and named), a file "
          "that appeared before the exclusive creation, an ordinary append to an untracked "
          "register with no anchor (as a first row and on unanchored rows; --bootstrap on "
          "a register that already holds rows is refused too), a landed row's commit read "
          "that times out or cannot be launched (no traceback), and a failure at each "
          "commit step (the temporary's exclusive creation, its lock, a failed write with "
          "truncation also failing, a short write, its permission bits, its fsync, the "
          "under-lock re-check of identity, size, bytes and hard links, the rename, and the "
          "kernel's own file-size limit) are refused as cannot-evaluate (exit 2), each "
          "leaving the register's bytes unchanged and no temporary file (a temporary that "
          "cannot be removed is named by its path); a file or symlink swapped in at the "
          "temporary's name, a temporary removed from its name and an injected non-regular "
          "entry there are refused before the rename by that name (exit 2), the register "
          "byte-identical and a swapped-in entry left in place and named; --bootstrap lands "
          "a first row and establishes the anchor --update-anchor would write, after which "
          "the ordinary gate passes, before and after a following ordinary append, and a "
          "directory sync "
          "or anchor write failing after the rename exits 2 saying the row is in the "
          "register; the replacing file keeps the register's permission bits, and an "
          "appender that took its lock on a file another appender's commit replaced starts "
          "over and lands after it; a "
          "refusal under the acquired lock after the first-row creation removes the created "
          "file and the directory components the call observed as its own and names the "
          "removal (each component created and identity-read relative to an open descriptor "
          "on its parent, and swept BY DESCRIPTOR, mirroring creation: the sweep re-opens "
          "the recorded outer parent, descends O_NOFOLLOW, re-checks each component's "
          "recorded device and inode with a no-follow stat relative to its parent's "
          "descriptor and removes it with rmdir relative to that same descriptor, so an "
          "ancestor swapped for a symlink during the sweep redirects no removal and the "
          "victim tree survives, with the descent identity re-check, the descent "
          "O_NOFOLLOW and the held-descriptor removal re-check each pinned by its own "
          "red-under-mutant vector, and the climb-back identity comparison pinned by a "
          "substituted-identity and a failing-inspection vector; the sweep is iterative "
          "and its two slots hold at most two descriptors however deep the record (probed "
          "at a 1500-component record; the peak of simultaneously live sweep descriptors "
          "measured at most two on a clean sweep and on one whose descent stops at depth "
          "one; under a close that fails without releasing its descriptor, the slots "
          "measured at most two, that descriptor reported once as a close failure, never "
          "attempted again and left open outside the slots, so the live count is at most two "
          "plus the close failures reported), clears each descriptor's owning slot before its "
          "close at the descent handoff, the climb handoff and the stopped-descent "
          "release (each probed by a close there that raises a non-OSError after its "
          "number was reissued, whose replacement is closed zero times) and attempts "
          "every slot still held in a finally (probed by live-set accounting under an "
          "injected non-OSError and by a final close that raises without skipping the "
          "other slot), descends a "
          "discontinuous record through a raced unrecorded middle component by name, "
          "removing nothing unrecorded, and sweeps a dot-dot register path completely; "
          "its report is three-valued (absent only on the entry's own ENOENT lookup, and "
          "then only at its own name, unreachable for every recorded entry it never "
          "looked up, left in place with its reason for one it looked up and kept or "
          "could not remove), so a recorded parent renamed away, with a continuous or a "
          "discontinuous record, leaves the components behind it named unreachable in "
          "both caller messages, never said to be gone, and an entry itself renamed "
          "away, alone or as the inner entry of a two-entry chain, is named absent at its "
          "own name in both caller messages, never said to be gone; an entry "
          "replaced by another empty directory, or by a non-directory, is left in place and "
          "named; a mkdir raced by another actor is used, never recorded, and survives the "
          "sweep; an outer component swapped aside cannot substitute a planted tree's entry "
          "for the identity read-back, so the planted entry is left in place and named; a "
          "raced intermediate symlink refuses the descent (O_NOFOLLOW) with nothing created "
          "through its target; the identity read-back records the entry's own identity, "
          "never a symlink's target's; the creation walk closes every descriptor it opened "
          "exactly once on the clean path, a close failure there is a named cannot-evaluate "
          "raised only after the sweep ran, and a close failure inside the sweep is named "
          "in the sweep's outcome as a close failure, kept apart from the removal "
          "outcomes (never rendered as a directory left in place, checked in both caller "
          "messages), never raised and never a raw OSError, and the final close of "
          "the creation walk is wrapped too (a single-component creation whose final "
          "close fails is a named cannot-evaluate after the sweep); the disclosed "
          "residual is pinned by vectors, with its exact bound stated once, at "
          "_made_dirs_swept (abbreviated: rmdir of an EMPTY directory only, within the "
          "two disclosed windows, inode reuse included; never a non-empty directory and "
          "never a file); the mkdir-to-stat window is probed at two components in one "
          "call (each moved-in directory recorded and removed, each displaced created "
          "directory surviving) and the identity-check-to-rmdir window with an empty and "
          "a non-empty swap (the empty one removed, the non-empty one left in place and "
          "named); the "
          "sweep does not stop at an already-absent component, the outer component is still "
          "removed and the absent one reported skipped, what cannot be removed is named with "
          "its reason and left in place, and a created file found absent at the register "
          "path at the cleanup, unlinked or renamed away, is said to be no longer at that "
          "path, never gone); every "
          "refusal leaves the rows other appenders wrote byte-identical; the register "
          "validates in the gate root's own "
          "context (a nested-repository register validates under the declaring outer root, "
          "in-process and in the production-root subprocess vector); an existing register's "
          "exclusive lock is held across the read, the validation and the commit (probed in "
          "place), while the first row of an absent register is validated before its "
          "exclusive creation and the created file is re-checked under the acquired lock, "
          "identity and size still zero, the serialization there being the exclusive "
          "creation plus those under-lock re-checks, not a second validation (they catch "
          "the register-row interleavings their identity, link-count, size and byte "
          "comparisons detect; other validation inputs, the anchor included, are not "
          "revalidated under the lock); an UNRESOLVABLE projected register path is a "
          "refusal naming the cause, never an empty projection (a dangling symbolic link "
          "at the register path and a register path under a missing parent directory are "
          "each exit 2 by name with nothing on stdout, while the first-run control, a "
          "confirmed-missing final name under a present parent, still projects as no "
          "items); a row value the decoder accepts but the formatter cannot walk is a "
          "named refusal too (the projection refuses with exit 2 naming the nesting, and "
          "the appender's shared validation names the violation through a placeholder at "
          "exit 1, both driven unconditionally by fault injection at the production "
          "rendering sites and additionally through the measured window between the "
          "decoder's and the renderer's recursion limits where this host has one (the "
          "window cases MAY NOT RUN: a host whose render limit is not below its decode "
          "limit skips them and this suite still exits 0, an omission the aggregate "
          "runner's RESULT line does not reflect, so the named NOT RUN line printed "
          "beside this PASS is the only disclosure), a "
          "skipped window case being reported as a named NOT RUN line, never a silent "
          "omission), never an escaped RecursionError; a registry nested too deeply for "
          "the JSON decoder is the same named refusal (exit 2), never a traceback; a "
          "descriptor close failure on the appender's unwind is exit 2 with the close "
          "attempted exactly once (a repeat attempt on a descriptor whose close failed "
          "is counted by the injectors and asserted zero) and each injection's firing "
          "asserted, never an escaped "
          "OSError (after a refusal it is a named refusal; after a LANDED row the message "
          "says the row is in the register, never refused, and the exit stays 2 even when "
          "the caller invokes the append inside its own exception handler, the escaping "
          "exception being tracked explicitly, never read from ambient exception state; "
          "on a SUPERSEDED lock-wait attempt's stale descriptor the message names the "
          "superseded attempt, never refused, the exit becoming 2 when the retried "
          "append returned with both rows landed in order, while a retried append that "
          "raised keeps its own verdict, probed at the next-id refusal whose exit 1 "
          "survives the close failure, the message then saying that verdict is carried "
          "by the still unwinding exception, never that it was reported above); "
          "the declaration check refuses a dangling symbolic link at a declared register "
          "path by name; and every "
          "clean append is re-read with its last row and "
          "verify_lines checked")
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
    p1.add_argument("--bootstrap", action="store_true",
                    help="append the first row of an untracked register that has no anchor yet "
                         "and establish its anchor, as check_mistakes_register.py "
                         "--update-anchor would")
    p2 = sub.add_parser("project")
    p2.add_argument("--register", required=True)
    a = ap.parse_args()
    return cmd_append(a) if a.cmd == "append" else cmd_project(a)


if __name__ == "__main__":
    sys.exit(main())
