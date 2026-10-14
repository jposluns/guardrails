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
      parser, check_mistakes_register.verify_lines and verify_append_only (with the --update-anchor
      bootstrap's allowance for an untracked register that has no anchor yet; the allowance covers
      any unanchored untracked register, not only an empty one), so an append the gate would reject
      is refused at write time instead of reaching CI. A violation (an illegal transition, an
      unknown status, a wrong-family id, a landed row without an existing --commit and --check-ref)
      exits 1; a context or authority the appender cannot evaluate (an unreadable register or
      register descriptor, a symlinked register path, an appender installed outside any git
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
      anything but ENOENT (only confirmed absence reads as absent), a non-string or empty
      registry declaration, a
      path no registry declaration names, conflicting declarations resolving to one path, a git
      failure deciding whether the register is tracked, no computable merge-base for a tracked
      register, a malformed or unusable anchor entry, a dangling symlink and an anchor lstat
      failure that is not ENOENT included, a register directory that cannot be created, a lock
      that cannot be acquired) exits 2; each
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
      validation and the write of an existing register; the first row of an absent register
      is validated BEFORE its exclusive creation, then created exclusively
      (O_EXCL) and then re-checked under the acquired lock (the path still names the created
      file and its size is still zero), so an appender that lands between the creation and the
      lock forces a refusal, never an interleaved row (the serialization there is the
      exclusive creation plus those under-lock re-checks, not a second validation). The
      write goes through the same
      descriptor the validation read (opened with O_NOFOLLOW, written only while its device and
      inode still match the path and the on-disk size still equals the validated text); a failed
      or short write is truncated back to the validated size, so no partial row remains, with one
      disclosed RESIDUAL: when that truncation itself fails, the partial bytes remain and the
      refusal names the un-truncated state and directs to the register gate. A writer that does
      not take the lock (a hand edit, any other tool) is outside that serialization: the size
      re-check catches one that lands between the validation and the write, any other is caught
      later by the mistakes-register gate, not here. A second RESIDUAL: the hard-link conflict
      check on the registry declarations is point-in-time; a hard link created after that
      check is not caught by this appender and is left to the register gate. A third
      RESIDUAL: POSIX offers no race-free proof that the directory found at a name is the one
      mkdir just made, so the entry a created component's no-follow identity read-back finds
      at the name is recorded as this call's own, and the cleanup sweep's identity re-check
      and its rmdir are two calls; the cleanup's exact bound is therefore: each removal is
      rmdir of an entry that is an EMPTY directory at removal time, at most one moved-in
      empty directory per created component (the mkdir-to-stat window) plus the sweep's
      identity-check-to-rmdir window, and ownership is not guaranteed across either window;
      never a non-empty directory and never a file. The sweep removes by descriptor, like
      creation: it re-opens the recorded outer parent by path, descends O_NOFOLLOW one
      recorded component at a time, re-checks each component's recorded device and inode
      with a no-follow stat relative to its parent's descriptor and removes it with rmdir
      relative to that same descriptor, so an ancestor swapped for a symlink during the
      sweep can never redirect a removal outside the recorded tree.
  orch_register.py project --register PATH
      Emit an AEI v1 enumeration of the rows whose LATEST status is proposed or accepted and not yet
      landed, as granted open items, so the stop guard itself enforces mistake-to-guardrail
      follow-through. A registry can name this as (part of) its enumerator. An attestation register
      is evidence, never backlog: project the mistakes register, not the attestations file.
  orch_register.py --self-test
      Synthetic fixtures for the write-time validation: each refusal vector leaves the register
      and its anchor byte-identical (the disclosed failed-rollback residual vector excepted,
      which asserts exactly the seed plus the injected partial bytes; the first-row cleanup
      and left-in-place vectors assert their named outcomes instead), every clean append is
      re-read and its landed last row and a clean verify_lines asserted, the serialization
      lock, the first-row creation re-checks (size and file identity) and the write binding
      are probed in place, and the production root resolution runs unpatched in subprocess
      vectors (the appender installed inside a repository, and installed outside any).
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
import stat
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mistakes_register import (  # noqa: E402
    REGISTRY_FILES, CannotEvaluate, confirmed_root, entry_state, gate_root,
    verify_append_only, verify_lines)

ZERO = "0" * 64


def _lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _gate_root():
    """The register gate's own validation-root CANDIDATE: the nearest enclosing .git entry
    above this appender's own installed location, through the SHARED
    check_mistakes_register.gate_root walk (entry_state: an explicit lstat whose only absence
    answer is ENOENT, so a dangling .git symlink is SELECTED, never walked past, and left for
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
    (registry PRESENCE is an explicit lstat whose only absence answer is ENOENT, so an
    inspection failure or a dangling entry never selects the lower-priority registry file), a
    declaration is not a non-empty string, no declaration names the path, a declared path's
    file identity cannot be inspected (only confirmed absence, ENOENT, omits an identity from
    the hard-link conflict check: an unreadable identity is never a missing identity), or two
    declarations resolve to the same register path (the declared id family is then ambiguous,
    so there is no single gate context to validate in): the register gate evaluates declared
    surfaces only. RESIDUAL: the identity comparison is point-in-time; a hard link created
    after this check is not caught here and is left to the register gate."""
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
        try:
            registry = json.loads(candidate.read_text(encoding="utf-8"))
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
            pass  # confirmed absence: an absent declared path has no identity to collide on
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


def _made_dirs_swept(made_dirs):
    """Remove the directory components THIS CALL observed as its own, innermost first, BY
    DESCRIPTOR, mirroring creation: each entry is (path, st_dev, st_ino), recorded by
    _dirs_created only after its own mkdir succeeded and its identity was read back with a
    no-follow stat relative to its parent's open descriptor. The sweep re-opens the recorded
    OUTER parent by path once (exactly as creation opened the deepest existing ancestor) and
    hands _swept_under that descriptor, so every re-check and every removal below runs
    relative to an open descriptor, never by re-resolving a string path, and an ancestor
    swapped for a symlink during the sweep can never redirect a removal outside the recorded
    tree. A component that is already absent (ENOENT) does not stop the sweep, because an
    OUTER component may still have been created. An entry this sweep cannot prove to be the
    created component (an inspection failure, a changed identity, a non-directory, an
    undescendable parent) is left in place and NAMED, and nothing beneath a kept entry is
    ever removed; a descriptor this sweep cannot close is named in the outcome, never
    raised. BOUND (exact, pinned by vectors): each removal is rmdir of an entry that is an
    EMPTY directory at removal time, at most one moved-in empty directory per created
    component (the mkdir-to-stat window) plus the identity-check-to-rmdir window (the
    re-check and the rmdir are two calls, so an empty directory swapped into the name
    between them is removed; a non-empty one is left in place and named), and ownership is
    not guaranteed across either window; never a non-empty directory and never a file.
    Returns (left, skipped): the components left in place (each with its reason) and the
    already-absent ones."""
    left, skipped = [], []
    if not made_dirs:
        return left, skipped
    rev_dirs = list(reversed(made_dirs))
    try:
        dir_fd = os.open(str(rev_dirs[0][0].parent), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        left.extend("{} (the recorded outer parent {} cannot be opened for the sweep: "
                    "{})".format(made, rev_dirs[0][0].parent, exc)
                    for made, _dev, _ino in made_dirs)
        return left, skipped
    _swept_under(dir_fd, rev_dirs, left, skipped)
    try:
        os.close(dir_fd)
    except OSError as exc:
        left.append("{} (the sweep descriptor on the recorded outer parent could not be "
                    "closed: {})".format(rev_dirs[0][0].parent, exc))
    return left, skipped


def _swept_under(dir_fd, rev_dirs, left, skipped):
    """One _made_dirs_swept step: REV_DIRS is the outermost-first tail of the recorded
    components and DIR_FD is an open descriptor on the first entry's recorded parent. The
    first entry's own removal runs last (children first): while inner components remain,
    the entry is opened O_RDONLY | O_DIRECTORY | O_NOFOLLOW relative to DIR_FD, the opened
    descriptor's identity (os.fstat, race-free on the descriptor) must still match the
    recorded one before the sweep descends through it, and that descent descriptor is
    closed here (a failed close is recorded in LEFT, never raised; inner components behind
    an absent entry are reported skipped, behind an unopenable or substituted one left in
    place and named). The removal itself re-checks the name with a no-follow os.stat
    relative to DIR_FD (the recorded st_dev and st_ino and a directory mode) and removes
    with os.rmdir relative to DIR_FD: the re-check and the rmdir are the two calls of the
    disclosed identity-check-to-rmdir window."""
    comp, comp_dev, comp_ino = rev_dirs[0]
    if len(rev_dirs) > 1:
        nxt = None
        try:
            nxt = os.open(comp.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=dir_fd)
        except FileNotFoundError:
            skipped.extend(str(inner) for inner, _dev, _ino in rev_dirs[1:])
        except OSError as exc:
            left.extend("{} (its recorded parent {} cannot be descended into: {})".format(
                inner, comp.name, exc) for inner, _dev, _ino in rev_dirs[1:])
        if nxt is not None:
            try:
                nxt_st = os.fstat(nxt)
            except OSError as exc:
                nxt_st = None
                left.extend("{} (its recorded parent {} cannot be inspected: {})".format(
                    inner, comp.name, exc) for inner, _dev, _ino in rev_dirs[1:])
            if nxt_st is not None and (nxt_st.st_dev, nxt_st.st_ino) == (comp_dev, comp_ino):
                _swept_under(nxt, rev_dirs[1:], left, skipped)
            elif nxt_st is not None:
                left.extend("{} (its recorded parent {} is no longer the directory this "
                            "call created)".format(inner, comp.name)
                            for inner, _dev, _ino in rev_dirs[1:])
            try:
                os.close(nxt)
            except OSError as exc:
                left.append("{} (the descent descriptor the sweep opened on it could not "
                            "be closed: {})".format(comp, exc))
    try:
        comp_st = os.stat(comp.name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        skipped.append(str(comp))
        return
    except OSError as exc:
        left.append("{} (cannot be inspected: {})".format(comp, exc))
        return
    if ((comp_st.st_dev, comp_st.st_ino) != (comp_dev, comp_ino)
            or not stat.S_ISDIR(comp_st.st_mode)):
        left.append("{} (no longer the directory this call created)".format(comp))
        return
    try:
        os.rmdir(comp.name, dir_fd=dir_fd)
    except OSError:
        left.append(str(comp))


def _dirs_created(parent):
    """Create the register directory PARENT's missing components and return the components
    this call OBSERVED as its own, innermost first, each as (path, st_dev, st_ino). Each
    component is created with os.mkdir RELATIVE to an open descriptor on its parent directory
    and its identity is read back with a no-follow os.stat relative to that SAME descriptor,
    never by re-resolving the string path, so an outer component swapped aside after its
    creation cannot substitute another tree's entry for the identity read-back. The deepest
    EXISTING ancestor is opened O_RDONLY (a parent that is searchable but not readable
    refuses here rather than creating blind) and the walk descends O_NOFOLLOW, one component
    at a time. The absence walk inspects each ancestor through the shared entry_state (ENOENT
    is the only absence), so an UNINSPECTABLE ancestor refuses BEFORE any mkdir runs. A
    component that appears between the walk and its mkdir (FileExistsError) is another
    actor's directory: used for the descent, never recorded. DISCLOSED RESIDUAL: POSIX offers
    no race-free proof that the directory found at a name is the one mkdir just made, so the
    entry the no-follow stat finds at the created name is recorded as this call's own; a
    directory moved into that name between the mkdir and the stat is therefore recorded, and
    the later sweep's exact bound is: each removal is rmdir of an entry that is an EMPTY
    directory at removal time, at most one moved-in empty directory per created component
    (this mkdir-to-stat window) plus the sweep's identity-check-to-rmdir window, with
    ownership not guaranteed across either window; never a non-empty directory and never a
    file. Raises
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


def _made_dirs_note(made_dirs):
    """The refusal-message clause for the directory sweep: empty when nothing was planned,
    else it reports the sweep's actual outcome, naming every component left in place (claims
    follow the sweep, never the plan)."""
    if not made_dirs:
        return ""
    left, skipped = _made_dirs_swept(made_dirs)
    if left:
        return ("; directory component(s) this call made could not be removed and were left "
                "in place: {}".format(", ".join(left)))
    if skipped:
        return "; no directory component this call made remains"
    return "; the directory components this call made were removed"


def _creation_cleanup(fd, path, made_dirs):
    """After a refusal taken UNDER the acquired lock that follows the exclusive first-row
    creation (a lock that cannot be acquired never reaches this helper: removal without the
    lock could race a cooperating appender, so that path leaves the file): remove the created file,
    but only while it is still this call's own, still-empty file (the path still names the
    created device and inode, size zero), then the directory components this call itself created
    (innermost first, each only while empty; an already-absent component does not stop the
    sweep). Returns the sentence for the refusal message; an entry this helper cannot prove
    safe to remove is left in place and NAMED, the made directory components included, and the
    register gate stays the authority over whatever remains."""
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
        note = ("the created register was already gone before this cleanup (nothing remained "
                "to remove at the register path)")
    except OSError as exc:
        note = "the created register could not be removed ({})".format(exc)
    if note is None:
        left, _skipped = _made_dirs_swept(made_dirs)
        if left:
            return ("the created register was removed; directory component(s) this call made "
                    "could not be removed and were left in place: {}".format(", ".join(left)))
        return "the created register and the directory components this call made were removed"
    return note + _made_dirs_note(made_dirs)


def cmd_append(a):
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
        try:
            problems = verify_lines(after, root=root, prefix=family + "-")
        except CannotEvaluate as exc:
            print("append: refused, cannot evaluate the register's rows ({}); nothing "
                  "written".format(exc), file=sys.stderr)
            return 2
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
            # directory stays absent; the directory components this call creates are
            # recorded by _dirs_created (identity read back relative to the parent descriptor
            # after each successful mkdir; an uninspectable ancestor refuses before any
            # mkdir) and swept BY DESCRIPTOR on a later refusal, so such a refusal removes
            # only what this call observed as its own, within the sweep's exact bound: each
            # removal is rmdir of an entry that is an EMPTY directory at removal time, at
            # most one moved-in empty directory per created component (the mkdir-to-stat
            # window) plus the sweep's identity-check-to-rmdir window, ownership not
            # guaranteed across either window.
            try:
                made_dirs = _dirs_created(path.parent)
            except CannotEvaluate as exc:
                print("append: refused, the register's directory could not be created ({}); "
                      "nothing written".format(exc), file=sys.stderr)
                return 2
            try:
                fd = os.open(str(path),
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o644)
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
            msg = ("append: refused, the register at this path is no longer the file that "
                   "was validated (replaced between validation and write); nothing written "
                   "to either file")
            if created:
                msg += "; " + _creation_cleanup(fd, path, made_dirs)
            print(msg, file=sys.stderr)
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
    # The appender resolves the register gate's own root (production: _gate_root(), the
    # nearest .git entry above the appender's own installed location, git-confirmed by
    # _root_validated and with NO cwd fallback);
    # each fixture stands in its own repository, so the resolution is routed at the fixture
    # root under test and restored in the finally.
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
            accepted("scrubbed-env-append-lands", repo / "envclean.jsonl", "MR-1")
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
            os.environ.pop("GIT_DIR", None)
        # an unreadable (non-UTF-8) register is refused as cannot-evaluate, never read as empty
        garbled = repo / "garbled.jsonl"
        declare(mistakes_register="garbled.jsonl")
        garbled.write_bytes(b"\xff\n")
        refused("unreadable-register-exit-2", garbled, 2, "cannot be read", "MR-1")
        # an absent register refused on its first row stays absent, directory included
        declare(mistakes_register="sub/new.jsonl")
        refused("refused-first-row-creates-nothing", repo / "sub" / "new.jsonl", 1,
                "first row for MR-1 must be proposed", "MR-1", "accepted")
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
        accepted("nested-repo-validates-in-gate-root", nested / "nested-reg.jsonl", "MR-1")
        # PRODUCTION root resolution (no _gate_root patch), exercised in SUBPROCESSES:
        # (a) the appender installed in repository A, the register in nested repository B that
        # A declares: the resolver walks up from the appender's own installed file, so the
        # append validates in A's context and lands (a register-based or cwd-based resolution
        # would consult B, find no registry there, and refuse);
        # (b) the appender installed OUTSIDE any git repository, invoked with the cwd inside a
        # declaring repository: refused (exit 2), never a root adopted from the invocation
        # directory. Like this suite's other non-repository fixtures, these assume the
        # temporary directory itself sits outside any git work tree.
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
             "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 0 or len(_lines(nested_b / "reg.jsonl")) != 1:
            failures.append("production-root-nested-repo: the installed appender should "
                            "validate the nested register in its own repository's context "
                            "and land, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        outside_tools = tmp / "outside-tools"
        outside_tools.mkdir()
        for tool_name in ("orch_register.py", "check_mistakes_register.py", "_gen_common.py"):
            shutil.copy(str(src_tools / tool_name), str(outside_tools / tool_name))
        declare(mistakes_register="cwd-probe.jsonl")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(outside_tools / "orch_register.py"), "append",
             "--register", str(repo / "cwd-probe.jsonl"), "--id", "MR-1", "--mistake", "m",
             "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
            capture_output=True, text=True, timeout=60, cwd=str(repo))
        if proc.returncode != 2 or "not installed inside a git repository" not in proc.stderr:
            failures.append("production-root-outside-install: want exit 2 naming the "
                            "installation, got exit {}: {}".format(
                                proc.returncode, proc.stderr.strip()))
        if entry_state(repo / "cwd-probe.jsonl") is not None:
            failures.append("production-root-outside-install: the refused append created the "
                            "register")
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
             "m", "--evidence", "e", "--rule", "mstreg", "--guardrail", "g"],
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
        accepted("hardlink-seed", hard_a, "MR-1")
        os.link(str(hard_a), str(repo / "hard-b.jsonl"))
        declare(mistakes_register="hard-a.jsonl", attestations="hard-b.jsonl")
        refused("hardlinked-declarations-refused", hard_a, 2,
                "conflicting registry declarations", "MR-1", "accepted")
        # a DECLARATION whose identity inspection fails with anything but ENOENT is
        # cannot-evaluate: an unreadable identity is never a missing identity (it could hide
        # a hard link to the register, landing the append in both aliases)
        declare(mistakes_register="hid-a.jsonl", attestations="hid-b.jsonl")
        hid_a = repo / "hid-a.jsonl"
        accepted("hidden-link-seed", hid_a, "MR-1")
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
                "the orchestration registry cannot be read", "MR-4")
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
        if entry_state(vanish) is not None:
            failures.append("vanished-register-refused: a refusal re-created the register")
        # a descriptor read failure is cannot-evaluate with a named reason, never an
        # unhandled exception
        declare(mistakes_register="reg.jsonl")
        real_os_read = os.read
        read_fail = {"fired": False}

        def failing_read(read_fd, count):
            # fire only on the appender's own register read (its 64 KiB chunk size), never
            # on a subprocess pipe read from the root-validation git probe
            if not read_fail["fired"] and count == 1 << 16:
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
        write_fail2 = {"fired": False, "partial": b""}

        def failing_write2(write_fd, payload):
            if not write_fail2["fired"] and isinstance(payload, bytes) \
                    and payload.startswith(b'{"'):
                write_fail2["fired"] = True
                write_fail2["partial"] = bytes(payload[:7])
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
        if not write_fail2["partial"] or left != rollfail_seed + write_fail2["partial"]:
            failures.append("failed-rollback-residual: exactly the seed plus the injected "
                            "partial bytes should remain (a disclosed residual, repaired by "
                            "hand), got {!r}".format(left))
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
            rc, err = append(first_reg, "MR-1")
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
            rc, err = append(excl_reg, "MR-1")
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
            rc, err = append(ident_reg, "MR-1")
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
            rc, err = append(ident2_reg, "MR-1")
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
        # a cleanup that finds the created first-row file ALREADY GONE says it was already
        # gone, never that it could not be removed
        declare(mistakes_register="gone.jsonl")
        gone_reg = repo / "gone.jsonl"
        gone = {"fired": False}

        def unlinking(lock_fd, op):
            if not gone["fired"]:
                gone["fired"] = True
                os.unlink(str(gone_reg))
            return real_flock(lock_fd, op)

        fcntl.flock = unlinking
        try:
            rc, err = append(gone_reg, "MR-1")
        finally:
            fcntl.flock = real_flock
        if rc != 2 or "no longer the file that was validated" not in err \
                or "already gone" not in err or "could not be removed" in err:
            failures.append("first-row-already-gone: the cleanup should say the created file "
                            "was already gone, got exit {}: {}".format(rc, err.strip()))
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
            rc, err = append(lockfail_reg, "MR-1")
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
                    "directory could not be created", "MR-1")
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
            rc, err = append(repo / "mk4" / "deep" / "part.jsonl", "MR-1")
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
            rc, err = append(repo / "mk5" / "deep" / "pre.jsonl", "MR-1")
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
        left, skipped = _made_dirs_swept([(swap_dir, swap_st.st_dev, swap_st.st_ino)])
        swap_after = entry_state(swap_dir)
        if (not left or "no longer the directory this call created" not in left[0]
                or str(swap_dir) not in left[0] or skipped or swap_after is None
                or not stat.S_ISDIR(swap_after.st_mode)
                or (swap_after.st_dev, swap_after.st_ino)
                != (other_st.st_dev, other_st.st_ino)):
            failures.append("swept-identity: a replacing empty directory must survive and "
                            "be named, got left={} skipped={}".format(left, skipped))
        vector_cleanup("swept-identity", lambda: os.rmdir(str(swap_dir)))
        # a component replaced by a NON-DIRECTORY (a regular file) is the separate TYPE
        # leg of the same re-check: left in place and named, never removed
        type_dir = repo / "typedir"
        type_dir.mkdir()
        type_st = os.lstat(str(type_dir))
        os.rmdir(str(type_dir))
        type_dir.write_text("not a directory", encoding="utf-8")
        left, skipped = _made_dirs_swept([(type_dir, type_st.st_dev, type_st.st_ino)])
        if (not left or "no longer the directory this call created" not in left[0]
                or skipped or entry_state(type_dir) is None):
            failures.append("swept-type: a non-directory at a made component's name must "
                            "be left in place and named, got left={} skipped={}".format(
                                left, skipped))
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
        left, skipped = _made_dirs_swept(
            [(sweep_inner, sweep_inner_st.st_dev, sweep_inner_st.st_ino),
             (sweep_outer, sweep_outer_st.st_dev, sweep_outer_st.st_ino)])
        if left or skipped != [str(sweep_inner)] or entry_state(sweep_outer) is not None:
            failures.append("absent-component-sweep-continues: the outer component must "
                            "still be removed and the absent inner one reported skipped, "
                            "got left={} skipped={}".format(left, skipped))
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
        left, skipped = _made_dirs_swept(anc_made)
        planted_deep = entry_state(repo / "mk7" / "deep")
        if (len(anc_made) != 2 or skipped or len(left) != 2
                or "no longer the directory this call created" not in left[0]
                or planted_deep is None
                or (planted_deep.st_dev, planted_deep.st_ino)
                != (planted_deep_st.st_dev, planted_deep_st.st_ino)
                or entry_state(repo / "mk7-aside" / "deep") is None):
            failures.append("made-dir-ancestor-swap: the planted tree must be left in "
                            "place and named, never removed, got made={} left={} "
                            "skipped={}".format(anc_made, left, skipped))
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
        left, skipped = _made_dirs_swept(resid_made)
        displaced_st = entry_state(repo / "mk8-displaced")
        if (resid_made != [(repo / "mk8", intruder_st.st_dev, intruder_st.st_ino)]
                or left or skipped or entry_state(repo / "mk8") is not None
                or displaced_st is None or not stat.S_ISDIR(displaced_st.st_mode)):
            failures.append("name-swap-residual: the moved-in empty directory is recorded "
                            "and removed (the disclosed worst case) and the displaced "
                            "created directory survives, got made={} left={} "
                            "skipped={}".format(resid_made, left, skipped))
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
        left, skipped = _made_dirs_swept(raced_made)
        raced_after = entry_state(repo / "mk6")
        if ([m[0] for m in raced_made] != [repo / "mk6" / "deep"] or left or skipped
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
        left, skipped = _made_dirs_swept(percomp_made)
        if (percomp_ids != [(donor_b_st.st_dev, donor_b_st.st_ino),
                            (donor_a_st.st_dev, donor_a_st.st_ino)]
                or left or skipped or entry_state(repo / "w2a") is not None
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
            left, skipped = _made_dirs_swept(win_made)
        finally:
            os.rmdir = real_os_rmdir
        if (left or skipped or entry_state(repo / "win1") is not None
                or entry_state(repo / "win1-original") is None):
            failures.append("sweep-window-empty-swap: the empty directory swapped in "
                            "between the identity re-check and the rmdir must be the one "
                            "removed, with the recorded directory surviving displaced, got "
                            "left={} skipped={}".format(left, skipped))
        vector_cleanup("sweep-window-empty-swap",
                       lambda: os.rmdir(str(repo / "win1-original")))
        # ---- the same window with a NON-EMPTY intruder: rmdir refuses it (each removal is
        # rmdir of an entry that is an EMPTY directory at removal time), the entry is left
        # in place and NAMED, and nothing beneath it is removed (red with the sweep's
        # os.rmdir replaced by a no-op: nothing is named)
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
            left, skipped = _made_dirs_swept(win2_made)
        finally:
            os.rmdir = real_os_rmdir
        if (left != [str(repo / "win2")] or skipped
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
        # rename moved it), never through a re-resolved string path, so the victim tree the
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
            left, skipped = _made_dirs_swept(sw_made)
        finally:
            os.rmdir = real_os_rmdir
        if (entry_state(repo / "victim" / "deep") is None or skipped or len(left) != 1
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
        left, skipped = _made_dirs_swept(x2_made)
        if (len(x2_made) != 1
                or (x2_made[0][1], x2_made[0][2]) != (x2_entry.st_dev, x2_entry.st_ino)
                or (x2_made[0][1], x2_made[0][2])
                == (x2_target_st.st_dev, x2_target_st.st_ino)
                or len(left) != 1
                or "no longer the directory this call created" not in left[0]
                or skipped or entry_state(x2_target) is None):
            failures.append("read-back-records-entry-not-target: the identity read-back "
                            "must record the entry at the created name, never a symlink "
                            "target, and the sweep must keep both, got made={} left={} "
                            "skipped={}".format(x2_made, left, skipped))
        vector_cleanup("read-back-records-entry-not-target", lambda: (
            os.unlink(str(repo / "x2")), os.rmdir(str(repo / "x2-real")),
            os.rmdir(str(x2_target))))
        # ---- every descriptor the creation walk opens is closed exactly once on the clean
        # path: the deepest existing ancestor plus one per descended component (red with
        # the descent's os.close of the handed-over parent removed: one descriptor leaks
        # per descended component)
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
        left, skipped = _made_dirs_swept(x3_made)
        if fd_bad or fd_live or len(fd_opens) != 3 or left or skipped:
            failures.append("creation-descriptors-closed-once: the walk must close every "
                            "descriptor it opened exactly once (the ancestor plus one per "
                            "descended component), got opens={} still-open={} double={} "
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
        # (red with the descent's close restored to a bare os.close: the injected OSError
        # escapes the walk unwrapped)
        c3fail = {"fired": False}

        def failing_close(cfd):
            if not c3fail["fired"]:
                c3fail["fired"] = True
                real_os_close(cfd)
                raise OSError(5, "injected close failure")
            return real_os_close(cfd)

        os.close = failing_close
        try:
            try:
                _dirs_created(repo / "c3a" / "c3b")
            except CannotEvaluate as exc:
                c3_refusal = str(exc)
            else:
                c3_refusal = None
        finally:
            os.close = real_os_close
        if (c3_refusal is None or "could not be closed" not in c3_refusal
                or "removed" not in c3_refusal or entry_state(repo / "c3a") is not None):
            failures.append("close-failure-cannot-evaluate: a failing close in the creation "
                            "walk must raise CannotEvaluate naming the step and the sweep's "
                            "outcome, with the created component removed, got "
                            "refusal={}".format(c3_refusal))
        # ---- a close failure inside the sweep is recorded in the sweep's own outcome and
        # NAMED, with the removals still done, and is never raised (red with the sweep's
        # close of its descent descriptor unguarded: the injected OSError escapes the
        # sweep)
        c4_made = _dirs_created(repo / "c4a" / "c4b")
        c4fail = {"fired": False}

        def sweep_failing_close(cfd):
            real_os_close(cfd)
            if not c4fail["fired"]:
                c4fail["fired"] = True
                raise OSError(5, "injected close failure")

        os.close = sweep_failing_close
        try:
            left, skipped = _made_dirs_swept(c4_made)
        finally:
            os.close = real_os_close
        if (skipped or len(left) != 1 or "could not be closed" not in left[0]
                or entry_state(repo / "c4a") is not None
                or entry_state(repo / "c4a" / "c4b") is not None):
            failures.append("sweep-close-failure-recorded: a failing close in the sweep "
                            "must be named in its outcome with the removals still done, "
                            "never raised, got left={} skipped={}".format(left, skipped))
        # an anchor lstat failure that is NOT absence (EIO here) is cannot-evaluate, never the
        # missing-anchor allowance and never an append past an uninspectable authority
        declare(mistakes_register="anchio.jsonl")
        anchio = repo / "anchio.jsonl"
        accepted("anchor-inspect-seed", anchio, "MR-1")
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
          "register descriptor, a symlinked register path, an undeclared register, an appender "
          "installed outside any git repository (subprocess vector, never a cwd-derived root), "
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
          "identity re-check refuses, the displacing file receives no row), a lock that "
          "cannot be acquired (the created empty file is left in place and named), a file "
          "that appeared before the exclusive creation, and a failed or short write (rolled "
          "back to the validated size, a failed rollback disclosed with exactly the seed plus "
          "the injected partial bytes remaining) are refused as cannot-evaluate (exit 2); a "
          "refusal under the acquired lock after the first-row creation removes the created "
          "file and the directory components the call observed as its own and names the "
          "removal (each component created and identity-read relative to an open descriptor "
          "on its parent, and swept BY DESCRIPTOR, mirroring creation: the sweep re-opens "
          "the recorded outer parent, descends O_NOFOLLOW, re-checks each component's "
          "recorded device and inode with a no-follow stat relative to its parent's "
          "descriptor and removes it with rmdir relative to that same descriptor, so an "
          "ancestor swapped for a symlink during the sweep redirects no removal and the "
          "victim tree survives; an entry "
          "replaced by another empty directory, or by a non-directory, is left in place and "
          "named; a mkdir raced by another actor is used, never recorded, and survives the "
          "sweep; an outer component swapped aside cannot substitute a planted tree's entry "
          "for the identity read-back, so the planted entry is left in place and named; a "
          "raced intermediate symlink refuses the descent (O_NOFOLLOW) with nothing created "
          "through its target; the identity read-back records the entry's own identity, "
          "never a symlink's target's; the creation walk closes every descriptor it opened "
          "exactly once on the clean path, a close failure there is a named cannot-evaluate "
          "raised only after the sweep ran, and a close failure inside the sweep is named "
          "in the sweep's outcome, never raised and never a raw OSError; the disclosed "
          "residual is pinned by vectors, with its exact bound: each removal is rmdir of an "
          "entry that is an EMPTY directory at removal time, at most one moved-in empty "
          "directory per created component (the mkdir-to-stat window, probed at two "
          "components in one call, each moved-in directory recorded and removed and each "
          "displaced created directory surviving) plus the identity-check-to-rmdir window "
          "(an empty directory swapped in between the sweep's re-check and its rmdir is "
          "removed, a non-empty one is left in place and named), and ownership is not "
          "guaranteed across either window; the "
          "sweep does not stop at an already-absent component, the outer component is still "
          "removed and the absent one reported skipped, what cannot be removed is named and "
          "left in place, and a created file already gone at the cleanup is said to be "
          "already gone); every "
          "refusal leaves the rows other appenders wrote byte-identical (the disclosed "
          "failed-rollback residual excepted); the register validates in the gate root's own "
          "context (a nested-repository register validates under the declaring outer root, "
          "in-process and in the production-root subprocess vector); an existing register's "
          "exclusive lock is held across the read, the validation and the write (probed in "
          "place), while the first row of an absent register is validated before its "
          "exclusive creation and the created file is re-checked under the acquired lock, "
          "identity and size still zero, the serialization there being the exclusive "
          "creation plus those under-lock re-checks, not a second validation; and every "
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
    p2 = sub.add_parser("project")
    p2.add_argument("--register", required=True)
    a = ap.parse_args()
    return cmd_append(a) if a.cmd == "append" else cmd_project(a)


if __name__ == "__main__":
    sys.exit(main())
