#!/usr/bin/env python3
"""OPF shared operation lock (OPF-D2B PR2 redesign).

SENSITIVE-TIER, concurrency-critical: a lock bug means data corruption or a race. Fail-closed
throughout, stdlib-only, Linux/macOS (POSIX advisory locks).

The lock identity is THREE LEGS, held together or the acquisition fails and unwinds:

  1. A persistent flock ANCHOR at <control-root>/opf-oplock/mutex.lock. The control root is the
     AUTHORITATIVE common git directory (git rev-parse --git-common-dir, asked of git itself with
     an explicit -C binding to the resolved store root over a scrubbed environment with every
     GIT_* variable removed; never inferred from a name or a worktrees-layout heuristic), or the
     store root itself when .git is GENUINELY absent. The .git entry is classified three-way on a
     no-follow basis: genuine absence roots the control tree at the store root; a real directory
     or a regular gitdir-pointer file asks git; ANYTHING else (a symlink, live or dangling, a
     FIFO, an unreadable entry, or a failed or missing git on a git store) refuses, never falls
     back to the repository root. The anchor is created once and NEVER unlinked by release, so
     this module's own paths can never swap the flock inode under a waiting peer.
  2. An exclusive ACTIVE RECORD at <control-root>/opf-oplock/active.toml (published exclusively:
     os.link of a complete staging file, refused on EEXIST; see below). A crash leaves it
     behind, and a pre-existing record under a free anchor refuses acquisition until EXPLICIT
     recovery (recover=True). The active record persists the FULL owner identity
     (pid, uid, nodename, canonical /proc start time, session, and acquisition stamp) so a later
     recovery can DECIDE the holder's liveness rather than infer staleness from age or timeout.
     Recovery is never granted by age or timeout, and even under recover=True a record is cleared
     ONLY when the recorded holder is CONFIRMED DEAD on this host by the journal's
     possibly-live-never-seized model (_journal.owner_confirmed_dead over the persisted owner
     identity); a possibly-live, cross-host, or malformed holder, or a lone lease with no paired
     active record, is REFUSED rather than seized. Both stale records are first validated against
     their COMPLETE closed schemas (top-key equality, supported schema, required non-empty fields),
     and a paired lease must name the same holder, operation, and acquisition stamp as the active
     record, so a missing field never compares equal to a missing field. The recovery delete is
     bound to the (st_dev, st_ino) identity and exact bytes the liveness gate read: a record
     replaced after that read is refused and PRESERVED, never deleted. So a live holder whose
     records are exposed under a split anchor can never be recovered into a two-holder state.
     The active record (schema 2, its own version, independent of the lease's) also persists a
     [machine_store] table: the path and the (st_dev, st_ino) identity, as canonical decimal
     strings, of the machine store holding its paired lease. The path is a RECOVERY INPUT: when the
     recorded identity differs from the recovering checkout's, the path's ancestors are opened by a
     no-follow walk and its final component is stat'ed no-follow (never opened) to decide whether
     the recorded machine store still exists. Linked
     git worktrees SHARE the control root (so the anchor and active record) but each has its OWN
     machine store (so its own lease); recovery therefore refuses, deleting nothing, when the
     record's machine store is not the recovering checkout's, so a sibling worktree can never
     delete the shared active record and strand the owning checkout's lease. That refusal holds
     only while the RECORDED machine store still exists: its recorded path is walked no-follow and
     stat'ed no-follow, and when it is absent or binds an object with a different (st_dev, st_ino)
     (a deleted worktree, or this checkout's machine store destroyed and recreated) the paired
     lease is taken as gone with it, so recovery continues to the liveness gate (a checkout MOVED
     rather than deleted is indistinguishable here: the disclosed moved-store limit below); any
     other outcome of that
     examination (a symlinked ancestor, an I/O or permission error) refuses. A schema-1 record
     (the earlier format, with no [machine_store]) is refused by recovery with a message naming
     that cause. Recovery deletes the verified LEASE FIRST (the unlink fsynced on its directory)
     and only then the active record, so a crash or a failed delete between the two leaves a lone
     ACTIVE record, which still carries the owner identity the liveness gate reads and is
     therefore recoverable by a later recover=True; it never leaves the lone owner-less lease that
     no recovery can confirm dead.
  3. The spec 5.7 LEASE at <machine-store>/lease.toml (published exclusively, as the active
     record is), mandatory: acquisition
     requires a RESOLVED machine store and every capability carries a lease. A capability without
     a lease cannot exist. The lease schema is single-sourced from the store validator and is not
     extended here; lease-holder liveness is derived from the paired active record's owner (same
     holder).

Both control records are emitted through the checked TOML encoder (_opf_emit.emit_checked) and
re-checked for top-key equality against their closed key sets, and PUBLISHED ATOMICALLY: the payload
is written in full (the shared full-write loop, _journal._write_all) to a uniquely named staging
file in the same directory (".<name>.opf-stage-<32 hex>", created O_CREAT|O_EXCL, its mode then SET
explicitly to 0o644 and verified on the open descriptor, owner read and write present and no group
or other write, before any byte is written, so a restrictive umask such as 0o444 or 0o777 can never
publish a record that release or crash recovery cannot reopen; a newly created anchor has its mode
set the same way; and initialization is RESTARTABLE (fix round 6): a control directory or anchor,
new or left by a creator killed before it corrected the mode, whose owner permissions the umask
masked is repaired on the existing-object path to its intended 0o755 or 0o644, only when it is our
own object of the expected type whose mode lies within the intended one, bound to the stat'ed object
and never following a symbolic link, and otherwise refused; that REPAIR accepts and preserves a
control directory's set-group-ID bit only as mkdir inherits it, from a set-group-ID parent whose
group it carries, fix round 7; a control directory whose owner permissions are intact is not
repaired, and the directory validation refuses only group or other write, so its special bits are
accepted as they are, whatever set them, fix round 8) and fsynced; only then is it published
at its final name by os.link, which refuses an existing name (EEXIST) exactly as O_EXCL did; the
staging name is then unlinked and the directory fsynced, all before the capability returns.
GUARANTEED against process death (SIGKILL included) at any point: the final name holds either no
record or the complete, fsynced one, never a torn prefix; at most a staging leftover remains, and
the next acquisition removes every leftover matching that exact staging pattern under the held
anchor flock, before stale classification, as garbage rather than a finding (a staging name bound to
anything but a regular file refuses). The capability retains the open descriptors, the (st_dev,
st_ino) identities, and the exact payload bytes of everything it created. A publication that FAILS
or is INTERRUPTED before returning (an OSError, or any BaseException such as a KeyboardInterrupt) is
treated as POTENTIALLY PUBLISHED: the names it bound are established by OBSERVATION (the staging
name, and the final name once its os.link was attempted, each counted only when a no-follow stat
finds it binding the created inode), never by bookkeeping an interruption could skip, and are
removed by verified identity; its descriptor is closed before raising; an OSError is normalized to
OpLockError, while any other BaseException is re-raised as itself with the cleanup outcome attached
as notes; where that cleanup itself fails, the raise carries both the failure and a reason naming
the leftover it could not remove, never a message that implies a clean unwind. The acquisition
unwind then deletes the active record only once the lease name is OBSERVED absent after a failed or
interrupted lease publication; a lease still present (or a presence check that cannot answer) KEEPS
the owner-bearing active record beside it, so a later recover=True can clear both, never a lone
owner-less lease, with ONE exception: the fork-forward composite disclosed in the residuals below
(fix round 9), under which a lone owner-less lease, should it arise, needs manual removal.

Interruptions are DEFERRED, not chased line by line (fix round 5). In CPython an asynchronous
exception reaches Python code only through a Python-level signal handler (KeyboardInterrupt is the
default SIGINT handler's raise). The whole acquisition (recovery included) and the whole release
therefore run with every signal that has a Python-level handler BLOCKED (_SignalDeferral; SIGKILL,
SIGSTOP, and the synchronous fault signals are never blocked): a signal arriving meanwhile stays
pending and is delivered when the exact previous mask is restored, after the section has ended.
GUARANTEED, on the main thread of a platform with signal.pthread_sigmask, within the residuals
below: no signal-raised exception lands inside those sections; it is delivered after them. A
capability that was fully formed when such a signal was delivered never reaches the caller, so it is
released (records removed, descriptors closed, the anchor's close giving up the lock) before the
interruption propagates as itself, through the scoped release directly, with no second /proc
identity read that could refuse before the release owns the descriptors (fix round 6), and the
interruption carries a note stating the observed outcome of that release, each clause derived from
the step the release recorded as completed (fix round 7); an acquisition that failed is fully
unwound before it; a release has ended released and closed before it. A signal handler that FORKS
splits this into two processes sharing every open file description, and a flock belongs to the
open file description: cleanup that runs in a forked child (the unreturned release, the
acquisition unwind, a publication's failure cleanup, the release legs and the release scope's
exit) therefore acts on records only in the process that began the operation, by pid (fix round
7): a forked child closes only its own descriptor copies and never removes a record or staging
name, so the acquirer keeps its lock and its records. The acquirer's identity is captured ONCE, at
the acquisition's entry, and the published active record, the capability, and every fork guard
are bound to that same value (fix round 8), so a child whose handler RETURNS and which continues
the acquisition forward builds a capability that names the acquirer, not itself; the hand-off
(_handoff) then refuses it: such a forked continuation receives no capability, closes only its
inherited descriptor copies (no unlink), and raises a refusal stating what that refusal itself did
(fix round 9). Before that hand-off, such a continuation is refused at the flock or its first
mutating step under it: the flock, the staging-leftover removals, the recovery deletes, and every
syscall of both publications run only in the acquiring process (fix round 9, _gated_step, the pid
check and the step one expression), and the publication reads its staged bytes back before
publishing them.

RELEASE BY CLOSE (fix round 9). No lock path of this module calls flock(LOCK_UN) (only the
self-test's own probes of a fresh descriptor do, to test whether the anchor is free). A flock
belongs to the
open file description, which the anchor descriptor shares with every dup and every forked copy of
it, and it is freed only when the LAST descriptor referring to that description is closed (verified
in this environment, and witnessed by T-f9-2: a dup, or a forked child's inherited copy, keeps the
lock held after the holder's own descriptor is closed, and the last close frees it). The release
and the acquisition unwind therefore give the lock up ONLY by closing the retained anchor
descriptor, and a forked child that reaches any cleanup path closes only its own copies, which can
never free a lock its acquirer still references, whether or not it passed a pid check first.
CONSEQUENCE: while a forked child still holds inherited copies, the lock stays held even after the
acquirer's release has closed its own descriptor. That is the safe direction (never two holders;
a later acquisition is refused as contention meanwhile), and it is bounded for the children this
module sees: a child in any of its cleanup paths closes its copies at once, and every retained
descriptor is O_CLOEXEC, so an exec drops them; the remaining window is disclosed below.

Beneath the deferral, as defence in depth against exceptions no mask can defer (an OSError or a
MemoryError raised by a step, or an exception injected by a non-signal mechanism), descriptor
ownership is single-sourced (_FdOwner): every descriptor the module opens is registered with one
owner as soon as the call that opened it returns, and leaves it only by an explicit transfer, an
explicit close, or the owner's final close_all. Ownership is cleared BEFORE each os.close, so a
close that fails after the kernel released the number (as Linux does) can never lead a later step
to close an unrelated descriptor that reused it. For an exception raised by any OPERATION after a
descriptor's registration, including one raised by a cleanup step itself, every remaining close
step still runs, each as its own guarded step, and an interruption propagates as itself after that
cleanup (an ordinary failure is collected into one OpLockError instead). A close an interruption
cuts short after its ownership was cleared is never retried (its number may have been reused) and
is reported as UNCONFIRMED, never as closed, separately from a close that ran and failed (fix round
9). The
store-root descriptor is closed inside the protected acquisition body, so a failing close (EIO)
runs the full unwind and never leaks the still-flocked anchor. A publication's staging descriptor
is protected from the moment it is adopted: by the private owner's context-managed exit when no
caller owner is given, and by the caller's owner, closed by that caller's unwind, when one is (the
acquisition's path). Release runs inside an enclosing context-managed cleanup armed before any
state changes. No line of the acquisition, publication, or release BODIES lies outside their
cleanup; the smaller helpers they call are protected against their operations' exceptions but are
not claimed line-complete against an exception injected at an arbitrary line boundary (the
residuals below).

Release is identity-bound. It refuses, FIRST, any caller that is not the recorded acquirer (pid plus
the /proc start-time identity _journal._pid_start provides), touching nothing (a capability the
acquisition itself releases because it was never returned skips the /proc read, but not the pid:
only the process that began that acquisition releases it, and a forked child closes only its
descriptor copies); it then, with the Python-handled signals deferred (above), enters an
enclosing cleanup (a context manager armed before any state changes) that takes sole ownership of
the retained descriptors and marks the capability released before any step that can fail, and does
so again on exit, so an interruption anywhere in the release, its own bookkeeping included, still
ends released and closed. Taking ownership is ONE synchronized claim under the capability's lock
(fix round 8): the released check, the capture of the retained descriptors, and their transfer
happen together, so a second release of the same capability, even from another thread in the same
instant, loses the claim and refuses holding no descriptor, and can never close a descriptor
number the winner released and the process reused. That lock is NEVER acquired blocking (fix
round 9, codex MED 1): each attempt re-checks the pid, tries the lock without blocking, and
re-checks the pid once it is held, for at most _CLAIM_WAIT_SECONDS in all, so a forked child, in
which a copy another thread held at the fork stays locked forever, never waits on it. The release
re-checks the retained anchor and control-directory identities (collected, not early-raised); it
then removes the LEASE and ONLY THEN the active record:
when the lease cannot be removed, the owner-bearing active record is KEPT beside it, so a later
recover=True can confirm the holder dead and clear both, never a lone owner-less lease no recovery
can clear (the one exception is the fork-forward composite disclosed below, fix round 9, whose lone
lease needs manual removal); each removal is a verified unlink (re-open no-follow, type, link count,
size, device and inode against the retained identity, byte equality against the retained payload,
and a final pre-unlink name-stat) and a mismatch refuses and PRESERVES the file rather than
removing it; an unlink whose directory fsync then fails is reported as a removal that is not
durable, never as an unlink that did not run, and the active record still waits for a durably
removed lease (fix round 8); an OS error inside a leg (an EIO read, a failed fstat) is normalized
to OpLockError and collected like any other leg failure; every retained descriptor is then closed
by that enclosing cleanup, the anchor's first (its close is what gives up the lock: release by
close, above), each its own guarded step, so no leg failure or interruption can skip a close; and
the collected failures aggregate into one raise after the closes. The acquisition unwind follows
the same shape and the same lease-first order, with the same fork-forward exception to its
no-lone-lease guarantee.

Every control path is reached only through dir_fd opens beneath no-follow walked parents
(_opf_store._open_dir_nofollow); every control open carries O_NOFOLLOW and O_NONBLOCK, so a
symlink at a control name is refused rather than followed and a FIFO cannot block the type check.
Nothing here creates a missing input root (only the single opf-oplock component is ever created,
under an already-open control root), and a link-count check applies to regular files ONLY, never
to a directory.

CALLER CONTRACT (fix rounds 8 to 10). An OpCapability is owned by the one thread that acquired it,
and that thread releases it, once. A concurrent release of the same capability (a second thread
calling release_operation while the first is in progress, or after it) is REFUSED safely: exactly
one release claims the capability, and every other refuses with OpLockError holding none of its
descriptors, removing nothing, and closing nothing, so it can never close a descriptor number the
process has reused; no release ever waits on the claim without bound (it retries without blocking
for at most _CLAIM_WAIT_SECONDS, then refuses). An acquisition is in progress from its entry
identity capture, the FIRST statement of acquire_operation (fix round 10), and this contract is
bounded there: a fork that lands before that capture (before or at the entry call, or at the
capture's own line boundary) is a separate caller, not a continuation, which captures its own
identity and acquires, or is refused, on its own account, contending for the one lock like any
other caller. Forking after that capture while an acquisition, or while a release, is in progress
(a signal handler that forks, where the deferral does not stand, or outside the deferred section)
yields NO usable capability in the child: a child that continues the acquisition forward is
refused at the flock, at its first pid-gated step under it, or at the hand-off, a child that
continues a
release forward refuses as a forked child (except one forked in the release scope's last lines,
after its own check, which returns having closed only its own copies and freed nothing), a child
that raises closes only its inherited descriptor copies, and release_operation refuses any
process other than the acquirer; the
acquirer keeps its capability, its records, and its lock, and no child can free that lock, since
the lock is released only by closing (above). A caller that forks while HOLDING a capability
(outside this module's sections) must expect the child's inherited descriptor copies to keep the
lock held, even after the parent's release, until the child execs (every retained descriptor is
O_CLOEXEC) or exits; a later acquisition is refused as contention meanwhile. The multithreaded
signal-deferral limitation stays as disclosed below: the deferral blocks signals on the calling
thread only, so a signal the kernel delivers to another thread still runs its Python handler on
the main thread inside the section, where only the structural protections and the pid guards
apply.

DISCLOSED RESIDUALS (this guard does not cover): the flock is advisory, binding only cooperating
processes; a same-uid actor who unlinks and recreates the anchor out of band is not prevented, only
bounded by the control-directory ownership and permission checks and surfaced by the stale-record
refusal; the final unlink is by name, so a window remains between the pre-unlink name-stat and the
unlink itself, narrowed by flock exclusivity, never closed; acquirer identity degrades to bare pid
equality where /proc start times are unavailable (non-Linux), where a confirmed-dead recovery still
rests on os.kill returning ProcessLookupError rather than on a start time; the machine-store
directory is validated for OWNERSHIP ONLY (not group- or other-writability), so a group-writable
machine store can weaken the lease leg where the control root and the machine store diverge in
trust: this is ownership-only BY DESIGN, to avoid over-firing on the shared-group adopter stores the
machine store legitimately lives in, and is disclosed here rather than prevented; a store or
common-dir path with a symlinked ancestor is refused by the no-follow walk rather than served; and
this is the LOCAL in-repository serialization boundary, not a remote-visible lease. Atomic
publication needs hard-link support in the control and machine-store directories (a filesystem
without it refuses acquisition, fail-closed), and its crash guarantee is against PROCESS death:
durability across power loss rests on the filesystem honouring fsync and is not independently
verified here. The restartable mode repair treats ANY same-uid control directory or anchor of the
expected type whose mode lies within the intended one but lacks an owner permission as an
interrupted creation, so a mode a same-uid actor set deliberately in that range is overwritten with
the intended one; on a platform without O_PATH the repair uses the platform's no-follow chmod by
name, so a same-uid swap of the name for another object of ours in the instant before it can reach
that object (the identity re-check then refuses), and where no no-follow chmod exists the repair
refuses, leaving a manual chmod. The interruption guarantee rests on the signal deferral and is
bounded as follows. The deferral covers this module's acquisition (recovery included) and release; a
helper another module calls directly (the resume substrate's calls to _create_control_file,
_verified_unlink, and _open_control_dir) runs without it, under the structural protections only. The
deferral is a no-op for a caller on a thread other than the main thread and on a platform without
signal.pthread_sigmask, where only the structural protections apply. A process-directed signal that
the kernel delivers to another thread which leaves it unblocked still has its Python handler run on
the main thread, inside the section. A child process started inside the section (the git rev-parse)
inherits the blocked mask, so it defers the same signals until it exits (bounded by the git
timeout). A second signal arriving in the few bytecodes between a first deferred signal's delivery
and the start of the unreturned capability's release can skip that release (its complete,
owner-bearing records then wait for a later recover=True, and its descriptors for process exit), and
the note the interruption carries then says NOT released. The forked-child guard compares pids
only (a forked child never shares its live parent's pid): a capability object carried into a
LATER process that inherits a recycled acquirer pid after the acquirer died is outside it, as it is
outside the public release's /proc start-time check where start times are unavailable. A child
whose signal handler RETURNS continues the interrupted operation FORWARD against the shared control
state until it reaches a guard (fix round 8). Since fix round 9 the flock and every mutating step
taken under it are pid-gated (the flock, the staging-leftover removals, the recovery deletes, and
each syscall of both
publications: the staging create, the mode set, the write and its fsync, the link, the staging
retire, the directory fsync), the capability hand-off refuses the child, the release legs act on
records only in the acquirer, and no path unlocks explicitly, so no child can free the lock and no
second holder arises. What is NOT prevented or undone: (a) the ONE step a fork lands inside, after
its gate's os.getpid() returned or within a Python-level step (the write loop, the mode set), which
the child completes; for the write loop that lands the payload twice through the shared file
offset, which the publication's read-back of the staged bytes refuses before the link (fix round
9), but a child's write landing after that read-back and before the link is not caught and
publishes a record that release refuses and recovery cannot parse, needing manual removal; (b)
steps outside the gates: before the flock (creating the control directory or the anchor, repairing
a mode; each idempotent or refused on a lost race, never a record), and inside the release legs
after their pid check (removing the acquirer's records, which surfaces as the acquirer's own leg
failure); (c) the FORK-FORWARD COMPOSITE: the composites examined in fix round 9 (a child winning
the staging retire, the staging create, the lease link, a recovery delete, or the write loop) no
longer strand a lone owner-less lease (T-f9-3 to T-f9-5), but that outcome is not proven
impossible for every interleaving of a truly concurrent child with its acquirer, so should it
arise, the lone lease needs manual removal, exactly as under the moved-store limit below. In every
case the acquirer's own step fails and surfaces what the child did. The hand-off's pid check and
its return are one expression (no line boundary between them), but a fork that lands inside it
after os.getpid() returned leaves the child a capability object bound to the acquirer's pid, which
release_operation refuses (not usable), and whose descriptor copies close only at the child's
exit. RELEASE BY CLOSE leaves this window (fix round 9): while any forked child still holds
inherited descriptor copies, the lock stays held after the acquirer's release. A child in one of
this module's cleanup paths closes its copies at once, but a child forked outside them (a caller's
own fork while holding; a forked continuation that returns to caller code before reaching one; or
one that forked while another thread's concurrent release was claiming, and so lost the claim and
holds its copies unreferenced) keeps the lock held until it execs or exits: the safe direction,
never two holders. A close an interruption cut short after its ownership was cleared is never
retried and is reported UNCONFIRMED; when it was the anchor descriptor's, the lock stays held until
the process exits. The release claim's lock is a threading.Lock that is never acquired blocking: a
forked child never waits on it and claims its own inherited copies without it (no other thread
exists in the child), and a release in the acquirer that finds it held past _CLAIM_WAIT_SECONDS
refuses holding nothing. An
exception injected by a NON-signal
mechanism (sys.settrace, as the self-tests do, or an asynchronous exception set on the thread from
outside) or a MemoryError can land at any point, where only the structural protections stand: on
CPython a nested try statement's own line, a with statement's exit line, and a cleanup block's first
line lie outside every enclosing exception range, and the bodies are built so none follows a
descriptor's adoption (each context-managed cleanup encloses a single call on its own line), but the
smaller helpers (the machine-store walk, the anchor open, the staging-leftover listing, the record
read and verified unlink, the recorded-store probe, the bound mode repair, a forked child's close of
its inherited descriptor copies) are not, so such an
injection there can leak one descriptor, and one at the deferral's own entry or exit can leave the
signals blocked on that thread. At the bytecode level such an injection between an open returning
and its registration, between a helper's transfer and its caller's adoption, or between ownership
being cleared and the os.close call, can leak that one descriptor (never double-close it; the close
is reported UNCONFIRMED, fix round 9); one
landing between the active record's publication returning and the acquisition storing its identity
can leave that complete, owner-bearing record for a later recover=True (a LEASE so left is caught by
the unwind's absence check, which keeps the active record beside it); and one landing inside a
cleanup handler itself, outside its individually guarded close steps, is not covered. A capability
built and handed its descriptors but interrupted before it is returned is unwound like any other
failure (its descriptors come back to the unwind, its records are removed, and its descriptors are
closed, the anchor's giving up the lock); record descriptors are adopted straight into the
acquisition's owner, with no transfer step. Stale pairing by the recorded path cannot tell a
DELETED machine store from a MOVED one: when
a checkout that crashed while holding is renamed or moved (for example by git worktree move) and
recovery then runs from ANOTHER checkout first, the recorded path is absent, recovery clears the
shared active record, and the moved checkout's lease is left as a lone lease that needs manual
removal (no second holder arises; recovering from the moved checkout itself first avoids it).

Nested journal/index lock composition is DELIBERATELY OUT OF SCOPE here (deferred to PR3); this
module makes no cross-lock ordering claim.

Run: python3 -I -B opf/tools/_opf_oplock.py --self-test
Exit: 0 self-test clean; 1 self-test failure; 2 refused precondition (missing containment
primitive or git binary), never a clean skip; 3 self-test incomplete (a restrictive-umask witness
was SKIPPED because no fixture root honours the umask), never a pass.
"""
import errno
import fcntl
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _containment        # noqa: E402
import _journal            # noqa: E402
import _opf_check          # noqa: E402
import _opf_emit           # noqa: E402
import _opf_init_contract  # noqa: E402
import _opf_store          # noqa: E402

# Fixed control names. The control directory holds exactly the anchor and, while an operation is
# active, the active record; nothing else is created there (the earlier draft's ops/<uuid>/phases
# tree is retired with the nested-lock scope-out).
CONTROL_DIRNAME = "opf-oplock"
ANCHOR_NAME = "mutex.lock"
ACTIVE_NAME = "active.toml"

# Closed top-level key sets for the two control records. The lease shape is single-sourced from
# the store validator (_opf_check.LEASE_TOP_KEYS, spec 5.7); the active record adds the op_id that
# correlates the control-side record to the capability that owns it, and (PR2 round 3) the [owner]
# sub-table that persists the acquirer's full identity so recovery can decide liveness without
# inferring staleness. PR2 OWNS the active-record schema (PR1 froze the coupled-init contracts,
# NOT this record), so extending it here is in-scope; the lease schema is NOT ours to change.
ACTIVE_TOP_KEYS = frozenset(("schema", "op_id", "holder", "operation", "acquired_at", "owner",
                             "machine_store"))
_SCHEMA = 1  # == _opf_schema.SUPPORTED_SCHEMA (the lease payload the doctor validates)
# The ACTIVE record carries its own schema version, independent of the lease's (which is not ours
# to change). Version 2 adds the [machine_store] table naming the machine store whose lease is
# paired with this record, so recovery from a sibling git worktree (which shares the control root
# but has its OWN machine store) can never delete an active record whose lease lives elsewhere.
# A version-1 record (no [machine_store]) is refused by recovery with a clear message.
_ACTIVE_SCHEMA = 2
_LEGACY_ACTIVE_SCHEMA = 1
# [machine_store]: the machine-store directory's absolute path and its (st_dev, st_ino) identity as
# canonical decimal STRINGS (an inode number can exceed the TOML signed 64-bit integer range on some
# filesystems). The (dev, ino) pair is the binding; the path is a recovery input (its ancestors are
# opened no-follow and its final component stat'ed no-follow, never opened; see
# _recorded_machine_store_present).
MACHINE_STORE_KEYS = frozenset(("path", "dev", "ino"))
_MAX_DECIMAL_DIGITS = 20  # 2**64 - 1 has 20 decimal digits

# Field bounds: single-sourced from the D2b contract's actor-id bound and control-character class
# (the class already covers C0, DEL, C1, and the BOM).
MAX_FIELD_BYTES = _opf_init_contract.MAX_ACTOR_ID_BYTES
_CONTROL_RE = _opf_init_contract._CONTROL_RE

_GIT_TIMEOUT_SECONDS = 30

# A small control record (active.toml or lease.toml) is tiny; a read for the recovery liveness gate
# is bounded so an oversize plant is a fail-closed refusal, never slurped (SECA resource-bounds).
_MAX_RECORD_BYTES = 65536

# Control opens: no-follow always; O_NONBLOCK always, so a FIFO planted at a control name cannot
# block the open that feeds the type check (c13); O_CLOEXEC so a retained fd never leaks across an
# exec boundary.
_DIR_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

# The modes this module creates its control objects with. The creating process's umask filters a
# mode passed to open or mkdir, so a restrictive umask (0o444, 0o777) would leave a record, the
# anchor, or the control directory its own later readers cannot open; each is therefore set
# explicitly after creation and verified (fix round 5), and one a creator killed before that step
# left is repaired on the next acquisition (fix round 6, _repair_restrictive_mode).
_CONTROL_FILE_MODE = 0o644
_CONTROL_DIR_MODE = 0o755


class OpLockError(Exception):
    """A fail-closed locking error."""


class _UnlinkNotDurable(OpLockError):
    """A verified unlink REMOVED its name, but the directory fsync that makes the removal durable
    failed (fix round 8, codex LOW): the removal happened and may not survive a power loss. A
    caller records the removal as performed and its durability as failed, never as an unlink that
    did not run."""


class _ForkedStep(OpLockError):
    """A mutating step refused because it was reached in a FORKED CONTINUATION (fix round 9, claude
    MED): a process whose pid differs from the one that began the operation, as a signal handler
    that forks and returns in the child produces. The refusal itself performed nothing."""


# The release claim (fix round 9, codex MED 1) is taken WITHOUT ever blocking on the capability's
# lock: each attempt is a non-blocking acquire, retried every _CLAIM_POLL_SECONDS for at most
# _CLAIM_WAIT_SECONDS. The step the lock guards is a few attribute assignments, so the bound is
# generous; it only ends a wait that a paused or wedged concurrent releaser would otherwise make
# unbounded.
_CLAIM_WAIT_SECONDS = 5.0
_CLAIM_POLL_SECONDS = 0.001

_NEVER_RETRIED = "it is never retried, since its number may have been reused"


def _unconfirmed_closes(count, what="descriptor"):
    """The fix-round-9 wording for `count` closes an interruption cut short after their ownership
    was cleared: whether they closed is UNCONFIRMED, never reported as closed."""
    return "the close of {} {}{} was interrupted, so whether {} closed is UNCONFIRMED ({})".format(
        count, what, "" if count == 1 else "s", "it" if count == 1 else "they", _NEVER_RETRIED)


def _gated_step(pid, what, fn, *args, **kwargs):
    """Run ONE mutating step, fn(*args, **kwargs), only in the process `pid` that began the
    operation (fix round 9, claude MED): a forked continuation (a signal handler that forked and
    returned in the child, which then runs the interrupted operation forward) is refused with
    _ForkedStep before it publishes, retires, or unlinks anything. The pid check and the call are
    ONE expression, so no line boundary lies between them; a fork landing inside it after
    os.getpid() returned, or inside a Python-level fn (the write loop), lets the child complete
    that one step (the disclosed residual)."""
    return fn(*args, **kwargs) if os.getpid() == pid else _refuse_forked_step(pid, what)


def _refuse_forked_step(pid, what):
    """_gated_step's refusal in a forked continuation: raise _ForkedStep, having done nothing."""
    raise _ForkedStep("refused to {} in a forked continuation (pid {}) of the process that began "
                      "this operation (pid {}): a signal handler forked and this child continued "
                      "the operation forward; this refusal performed nothing".format(
                          what, os.getpid(), pid))


class _FdOwner:
    """The single owner of the descriptors one unit of work opens (round 3, D2).

    A descriptor is registered (adopt) the moment the call that opened it returns, and leaves this
    owner in exactly one of three ways: an explicit TRANSFER to the caller on success, an explicit
    close, or close_all at the end of the unit. Every close clears ownership BEFORE os.close, so a
    descriptor number the kernel has already released (Linux releases it even when close reports an
    error) and has perhaps reused for an unrelated file is never closed a second time. close_all
    runs each remaining close as its own independently guarded step: a failing close (OSError) is
    collected and the remaining closes still run, and an interruption (any other BaseException)
    raised by a close step is held until every remaining step has run, then handed back for the
    caller to re-raise. Used as a context manager, the owner closes whatever it still owns on exit:
    on a clean exit a failed close raises OpLockError and a held interruption is re-raised; with an
    exception in flight, close failures are attached to it as notes, and a held interruption
    replaces an ordinary exception, so an interruption is never swallowed or converted.

    NOT covered (disclosed in the module contract): across the acquisition and the release a
    signal-raised interruption is deferred (_SignalDeferral) and cannot land here at all, but a
    non-signal injection (sys.settrace, an externally set asynchronous exception) or a MemoryError
    landing in the few bytecodes between an open returning and its adopt, or between a transfer and
    the caller's adopt, can leak that one descriptor; one landing between ownership being cleared
    and os.close leaks that one descriptor rather than risk a double close, and since fix round 9
    that close is reported as UNCONFIRMED, never as closed (the interruption may equally have
    landed just after the close completed, which no pure-Python check can tell apart)."""
    __slots__ = ("_fds",)

    def __init__(self):
        self._fds = []

    def adopt(self, fd):
        """Register a just-opened descriptor; returns it."""
        self._fds.append(fd)
        return fd

    def adopt_all(self, fds):
        """Register several descriptors in ONE step (a single list extend, which no Python-level
        interruption can split), closed later in reverse of the given order."""
        self._fds.extend(fds)

    def transfer(self, fd):
        """Hand `fd` over to the caller, who owns it from here; this owner will not close it."""
        self._fds.remove(fd)
        return fd

    def transfer_all(self):
        """Hand every owned descriptor over at once (the capability takes them)."""
        self._fds = []

    def holds_any(self):
        """True while this owner still owns at least one descriptor."""
        return bool(self._fds)

    def __contains__(self, fd):
        return fd in self._fds

    def close(self, fd, message):
        """Close one owned descriptor now, ownership cleared first; an OSError raises OpLockError
        built from `message` (a format string taking the error)."""
        self._fds.remove(fd)
        try:
            os.close(fd)
        except OSError as exc:
            raise OpLockError(message.format(exc))

    def close_guarded(self, fd):
        """close_all's guarded step for ONE owned descriptor, leaving the others owned. Returns
        (problems, unconfirmed, interrupt) exactly as close_all does; a descriptor whose close was
        interrupted before its ownership was cleared stays owned, so it is not unconfirmed."""
        problems = []
        unconfirmed = []
        interrupt = None
        try:
            self.close(fd, "cannot close a control descriptor ({})")
        except OpLockError as exc:
            problems.append(str(exc))
        except BaseException as exc:
            interrupt = exc
            if fd not in self._fds:
                unconfirmed.append(fd)
        return problems, unconfirmed, interrupt

    def close_all(self):
        """Close every descriptor still owned, most recently adopted first, each close its own
        guarded step. Returns (problems, unconfirmed, interrupt): the collected close failures
        (the close ran and reported an error), the descriptors whose close step an interruption
        cut short (fix round 9, codex LOW: ownership was already cleared, so whether each closed is
        UNCONFIRMED, and none is ever closed again, since its number may have been reused), and the
        first interruption a close step raised (None when there was none)."""
        problems = []
        unconfirmed = []
        interrupt = None
        while self._fds:
            fd = self._fds.pop()
            try:
                os.close(fd)
            except OSError as exc:
                problems.append("cannot close a control descriptor ({})".format(exc))
            except BaseException as exc:
                unconfirmed.append(fd)
                if interrupt is None:
                    interrupt = exc
        return problems, unconfirmed, interrupt

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        problems, unconfirmed, interrupt = self.close_all()
        if unconfirmed:
            problems.append(_unconfirmed_closes(len(unconfirmed), "control descriptor"))
        if exc is None:
            if interrupt is not None:
                if problems:
                    interrupt.add_note("opf-oplock: {}".format("; ".join(problems)))
                raise interrupt
            if problems:
                raise OpLockError("; ".join(problems))
            return False
        if problems:
            exc.add_note("opf-oplock: additionally {}".format("; ".join(problems)))
        if interrupt is not None and isinstance(exc, Exception):
            interrupt.add_note("opf-oplock: raised while cleaning up after: {}".format(exc))
            raise interrupt
        return False


# Signal deferral (fix round 5). In CPython an asynchronous exception reaches Python code only
# through a Python-level signal handler (KeyboardInterrupt is the default SIGINT handler's raise),
# and that handler runs only on the main thread, at an interpreter check point. Blocking every
# signal that has such a handler across a critical section therefore keeps any signal-raised
# exception out of it: the signal stays pending and is delivered when the previous mask is restored,
# after the section has completed or unwound. Synchronous fault signals are never blocked (the
# faulting instruction raises them itself, and a blocked synchronous fault is fatal), nor are
# SIGKILL and SIGSTOP (which cannot be).
_NEVER_DEFERRED = frozenset(getattr(signal, _name) for _name in (
    "SIGKILL", "SIGSTOP", "SIGSEGV", "SIGBUS", "SIGFPE", "SIGILL", "SIGTRAP", "SIGSYS", "SIGABRT")
    if hasattr(signal, _name))


def _deferrable_signals():
    """The signals a _SignalDeferral blocks: every signal that currently has a Python-level handler
    installed (a callable, which includes the default SIGINT handler that raises KeyboardInterrupt),
    minus _NEVER_DEFERRED. Empty, so the deferral is a no-op, on a thread other than the main thread
    (the signal mask is per thread, and a Python handler never runs there) and on a platform without
    signal.pthread_sigmask."""
    if not hasattr(signal, "pthread_sigmask") \
            or threading.current_thread() is not threading.main_thread():
        return ()
    deferred = []
    for sig in signal.valid_signals():
        if sig in _NEVER_DEFERRED:
            continue
        try:
            handler = signal.getsignal(sig)
        except (OSError, ValueError):
            continue
        if callable(handler):
            deferred.append(sig)
    return tuple(deferred)


class _SignalDeferral:
    """Defer the Python-handled signals across a critical section (fix round 5), as a context manager.

    Entry reads the current mask and then blocks _deferrable_signals(). Both calls run any handler
    whose signal arrived before the block took effect, so such a signal is delivered at the entry,
    before anything in the section has changed (and a block that took effect before its handler
    raised is undone first). Exit restores the EXACT previous mask, which delivers every signal that
    arrived during the section: its handler runs, and any exception it raises (a KeyboardInterrupt)
    propagates from the exit, after the section's own cleanup, carrying a note naming the exception
    the section itself ended with, if any. Nesting is safe: an inner exit restores the outer, still
    blocked, mask, so delivery waits for the outermost exit.

    A no-op on a thread other than the main thread and on a platform without pthread_sigmask (see
    _deferrable_signals); there, only the structural protections (the single descriptor owner and
    the context-managed cleanups) stand against an interruption. NOT covered (disclosed in the module
    contract): an exception injected by a non-signal mechanism (sys.settrace, or an asynchronous
    exception set on the thread from outside), a MemoryError at an arbitrary point, and a
    process-directed signal the kernel delivers to another thread that leaves it unblocked (CPython
    still runs its handler on the main thread). A child process started inside the section inherits
    the blocked mask, so it defers the same signals until it exits."""
    __slots__ = ("_prev",)

    def __init__(self):
        self._prev = None

    def __enter__(self):
        prev = None
        try:
            signals = _deferrable_signals()
            if signals:
                prev = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                signal.pthread_sigmask(signal.SIG_BLOCK, signals)
                self._prev = prev
            return self
        except BaseException:
            # A handler ran inside the blocking call after the block took effect (or the entry
            # failed after it): undo the block, which may itself deliver a further pending signal,
            # and let the section never start.
            if prev is not None:
                self._prev = None
                signal.pthread_sigmask(signal.SIG_SETMASK, prev)
            raise

    def __exit__(self, exc_type, exc, tb):
        prev, self._prev = self._prev, None
        if prev is None:
            return False
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, prev)
        except BaseException as delivered:
            if exc is not None:
                delivered.add_note("opf-oplock: a signal deferred across the critical section was "
                                   "delivered after it ended with: {!r}".format(exc))
            raise
        return False


# Atomic publication (round 3, D1). A control record is written in full to a uniquely named STAGING
# file in the same directory and fsynced, and only then published at its final name by os.link,
# which fails with EEXIST (the O_EXCL exclusivity the final name needs); the staging name is then
# unlinked and the directory fsynced. A process killed at any point therefore leaves either no
# final record or a complete one. A staging name is "." + the final name + _STAGING_MARKER + the 32
# lowercase hex digits of a uuid4; a leftover matching EXACTLY that pattern is what a process that
# died mid-publication leaves behind, and acquisition removes it under the anchor flock as garbage.
_STAGING_MARKER = ".opf-stage-"
_STAGING_HEX = frozenset("0123456789abcdef")


def _staging_name(name):
    """A fresh, unique staging name for the control record `name`."""
    return "." + name + _STAGING_MARKER + uuid.uuid4().hex


def _is_staging_name(entry, name):
    """True ONLY for a name this module's _staging_name could have produced for `name`."""
    prefix = "." + name + _STAGING_MARKER
    if not entry.startswith(prefix):
        return False
    tail = entry[len(prefix):]
    return len(tail) == 32 and all(ch in _STAGING_HEX for ch in tail)


class OpCapability:
    """The held operation lock: inert data plus the retained descriptors.

    Carries the three legs (the flocked anchor, the active record, the lease), the recorded
    (st_dev, st_ino) identities and exact payload bytes that release verifies against, and the
    acquirer identity (pid plus /proc start time) that release refuses any other caller on. It
    exposes NO further lock acquisition: nested journal/index composition is out of scope here
    (PR3), so this object cannot be used to widen what was acquired.

    Fix round 8: the acquirer identity is the one captured ONCE at the acquisition's entry (the same
    pid the published active record names), and the capability carries the lock (_claim) under
    which one release claims it: the released check, the capture of the retained descriptors, and
    the transfer of their ownership are one synchronized step (_ReleaseScope.take_ownership), and
    _claimant names the release scope that won it.
    """
    __slots__ = ("op_id", "holder", "operation", "store_root", "machine_rel",
                 "_ctl_fd", "_machine_fd", "_anchor_fd", "_active_fd", "_lease_fd",
                 "_anchor_ident", "_ctl_ident", "_machine_ident", "_active_ident", "_lease_ident",
                 "_active_bytes", "_lease_bytes",
                 "_acquirer_pid", "_acquirer_pid_start", "_released", "_claim", "_claimant")

    def __init__(self, op_id, holder, operation, store_root, machine_rel,
                 ctl_fd, machine_fd, anchor_fd, active_fd, lease_fd,
                 anchor_ident, ctl_ident, machine_ident, active_ident, lease_ident,
                 active_bytes, lease_bytes, acquirer_pid, acquirer_pid_start):
        self.op_id = op_id
        self.holder = holder
        self.operation = operation
        self.store_root = store_root
        self.machine_rel = machine_rel
        self._ctl_fd = ctl_fd
        self._machine_fd = machine_fd
        self._anchor_fd = anchor_fd
        self._active_fd = active_fd
        self._lease_fd = lease_fd
        self._anchor_ident = anchor_ident
        self._ctl_ident = ctl_ident
        self._machine_ident = machine_ident
        self._active_ident = active_ident
        self._lease_ident = lease_ident
        self._active_bytes = active_bytes
        self._lease_bytes = lease_bytes
        self._acquirer_pid = acquirer_pid
        self._acquirer_pid_start = acquirer_pid_start
        self._released = False
        self._claim = threading.Lock()
        self._claimant = None


# --- small fail-closed primitives ---------------------------------------------------------------


def _utc_now():
    """RFC 3339 UTC, read from the clock at the acquisition event (timestamp-from-clock)."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _lstat_at(dir_fd, name, label):
    """No-follow stat of `name` under dir_fd: the stat result, or None on GENUINE absence. Any
    other error refuses (an unreadable control name is a failure, never nothing-to-check)."""
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OpLockError("cannot stat {} ({})".format(label, exc))


def _validate_file_fd(fd, label):
    """Type/link-count/ownership/permission validation of an OPEN control file. The link-count
    check (exactly 1) applies here, to a regular FILE, and nowhere else (c4). Returns the stat."""
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise OpLockError("cannot fstat {} ({})".format(label, exc))
    if not stat.S_ISREG(st.st_mode):
        raise OpLockError("{} is not a regular file".format(label))
    if st.st_nlink != 1:
        raise OpLockError("{} link count is {}, expected exactly 1".format(label, st.st_nlink))
    if st.st_uid != os.getuid():
        raise OpLockError("{} is owned by uid {}, not the current uid {}".format(
            label, st.st_uid, os.getuid()))
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OpLockError("{} is group- or other-writable (mode {:o})".format(
            label, stat.S_IMODE(st.st_mode)))
    return st


def _validate_ctl_dir_fd(fd, label):
    """Type/ownership/permission validation of an OPEN control directory. Deliberately NO
    link-count check: a directory's link count grows with its subdirectories, so an nlink ceiling
    on a directory is a false-positive generator, not a guard (c4). Returns the stat."""
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise OpLockError("cannot fstat {} ({})".format(label, exc))
    if not stat.S_ISDIR(st.st_mode):
        raise OpLockError("{} is not a directory".format(label))
    if st.st_uid != os.getuid():
        raise OpLockError("{} is owned by uid {}, not the current uid {}".format(
            label, st.st_uid, os.getuid()))
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OpLockError("{} is group- or other-writable (mode {:o})".format(
            label, stat.S_IMODE(st.st_mode)))
    return st


def _open_dir_at(parent_fd, name, label):
    """Open a directory component beneath an already-trusted dir fd, no-follow."""
    try:
        return os.open(name, _DIR_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise OpLockError("cannot open directory {} no-follow ({})".format(label, exc))


# Fallible acquisition steps, each a helper so the acquisition body holds no nested try statement
# after its first descriptor adoption (fix round 4: a try statement's own line lies outside every
# enclosing exception range, so an interruption landing on it would skip the unwind).


def _open_path_dir_nofollow(path, what):
    """Open an absolute directory path by the shared no-follow walk (_opf_store._open_dir_nofollow);
    an OSError refuses, naming `what` and the path."""
    try:
        return _opf_store._open_dir_nofollow(path)
    except OSError as exc:
        raise OpLockError("cannot open {} {} no-follow ({})".format(what, path, exc))


def _fstat_or_refuse(fd, what):
    """fstat an open descriptor; an OSError refuses, naming `what`."""
    try:
        return os.fstat(fd)
    except OSError as exc:
        raise OpLockError("cannot fstat {} ({})".format(what, exc))


def _dup_store_root(store_fd):
    """Duplicate the store-root descriptor (the control root of a store with no .git)."""
    try:
        return os.dup(store_fd)
    except OSError as exc:
        raise OpLockError("cannot duplicate the store-root descriptor ({})".format(exc))


def _flock_exclusive(anchor_fd):
    """Take the anchor's exclusive flock WITHOUT blocking: contention refuses (a held anchor is
    never seized), and any other OSError refuses."""
    try:
        fcntl.flock(anchor_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise OpLockError("operation lock is held by another process (contention; a held anchor is "
                          "never seized)")
    except OSError as exc:
        raise OpLockError("cannot flock mutex anchor ({})".format(exc))


def _validate_field(label, value):
    """A control-record field: a non-empty, strictly-encodable, bounded string with no character
    of the single-sourced control class (C0, DEL, C1, BOM; _opf_init_contract._CONTROL_RE)."""
    if type(value) is not str or not value:
        raise OpLockError("{} must be a non-empty string".format(label))
    try:
        raw = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise OpLockError("{} is not encodable UTF-8 (lone surrogate)".format(label))
    if len(raw) > MAX_FIELD_BYTES:
        raise OpLockError("{} exceeds {} bytes".format(label, MAX_FIELD_BYTES))
    if _CONTROL_RE.search(value):
        raise OpLockError("{} carries a forbidden control character".format(label))


def _control_payload(document, closed_keys, label):
    """The exact bytes of a control record: emitted through the checked encoder (round-trip
    proven), then independently reparsed and checked for top-key EQUALITY against the closed set,
    so a record with a missing or extra top-level key can never be written."""
    try:
        text = _opf_emit.emit_checked(document)
    except _opf_emit.EmitError as exc:
        raise OpLockError("cannot emit {} ({})".format(label, exc))
    try:
        reparsed = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:  # defensive: emit_checked already reparsed
        raise OpLockError("emitted {} did not reparse ({})".format(label, exc))
    if set(reparsed) != set(closed_keys):
        raise OpLockError("{} top-level keys {} do not equal the closed set {}".format(
            label, sorted(reparsed), sorted(closed_keys)))
    return text.encode("utf-8")


def _read_control_record(dir_fd, name, label):
    """Read and TOML-parse an on-disk control record beneath dir_fd, no-follow and fail-closed. The
    opened object (not just the name) must be a plain singly-linked regular file, bounded by
    _MAX_RECORD_BYTES, decodable UTF-8, and a TOML table. ANY failure (absence, wrong type,
    oversize, unparseable, or a non-table document) raises OpLockError, so a record the recovery
    gate cannot read is a refusal, never a silent clean pass (check-fails-closed-on-unreadable).
    Returns (the parsed dict, its (st_dev, st_ino) identity, its exact raw bytes) so the recovery
    gate can bind its later delete to the very object and bytes it verified (DEF-1)."""
    with _FdOwner() as owner:
        try:
            fd = owner.adopt(os.open(name, _FILE_READ_FLAGS, dir_fd=dir_fd))
        except OSError as exc:
            raise OpLockError("cannot read {} for the recovery liveness gate ({})".format(
                label, exc))
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("{} is not a regular file; refusing recovery".format(label))
        if st.st_nlink != 1:
            raise OpLockError("{} link count is {}, expected exactly 1; refusing recovery".format(
                label, st.st_nlink))
        if st.st_size > _MAX_RECORD_BYTES:
            raise OpLockError("{} is {} bytes, over the {}-byte record cap; refusing recovery".format(
                label, st.st_size, _MAX_RECORD_BYTES))
        ident = (st.st_dev, st.st_ino)
        data = bytearray()
        while len(data) <= _MAX_RECORD_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if len(data) > _MAX_RECORD_BYTES:
            raise OpLockError("{} exceeds the {}-byte record cap; refusing recovery".format(
                label, _MAX_RECORD_BYTES))
    try:
        doc = tomllib.loads(bytes(data).decode("utf-8", errors="strict"))
    except (tomllib.TOMLDecodeError, ValueError, UnicodeDecodeError) as exc:
        raise OpLockError("{} is not decodable UTF-8 TOML; refusing recovery ({})".format(label, exc))
    if not isinstance(doc, dict):
        raise OpLockError("{} is not a TOML table; refusing recovery".format(label))
    return doc, ident, bytes(data)


# --- the control root (authoritative common git dir) --------------------------------------------


def _classify_git_entry(store_root_fd, store_root):
    """Three-way classification of <store_root>/.git on a no-follow basis: "absent" for genuine
    absence, "present" for a real directory or a regular gitdir-pointer file (confirmed on the
    OPENED object, not just the name), and a refusal for everything else. Never a fallback."""
    st = _lstat_at(store_root_fd, ".git", ".git at {}".format(store_root))
    if st is None:
        return "absent"
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError(".git at {} is a symlink; refusing (never followed, and never a "
                          "repository-root fallback)".format(store_root))
    if stat.S_ISDIR(st.st_mode):
        with _FdOwner() as owner:
            owner.adopt(_open_dir_at(store_root_fd, ".git", ".git at {}".format(store_root)))
        return "present"
    if stat.S_ISREG(st.st_mode):
        with _FdOwner() as owner:
            try:
                fd = owner.adopt(os.open(".git", _FILE_READ_FLAGS, dir_fd=store_root_fd))
            except OSError as exc:
                raise OpLockError("cannot open .git at {} no-follow ({})".format(store_root, exc))
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OpLockError(".git at {} changed type under the open; refusing".format(
                    store_root))
        return "present"
    raise OpLockError(".git at {} has an unexpected type (mode {:o}); refusing, never a "
                      "repository-root fallback".format(store_root, stat.S_IFMT(st.st_mode)))


def _git_common_dir(store_root):
    """The authoritative common git directory of `store_root`, asked of git itself: rev-parse
    --git-common-dir with an explicit -C binding and a scrubbed environment (every GIT_* variable
    removed, so an ambient GIT_DIR or GIT_COMMON_DIR cannot redirect the answer). Any failure
    (missing git, launch failure, timeout, nonzero exit, undecodable, unterminated, or
    non-single-line output) refuses; only the one record terminator is stripped, never a trailing
    space that is part of the path; the control root is never guessed and never falls back to the
    repository root."""
    git = shutil.which("git")
    if git is None:
        raise OpLockError("git binary not found on PATH while the store carries a .git entry; "
                          "refusing (the control root is never guessed)")
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    try:
        proc = subprocess.run(
            [git, "--no-replace-objects", "-C", str(store_root), "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpLockError("git rev-parse --git-common-dir could not run at {} ({}); "
                          "refusing".format(store_root, exc))
    if proc.returncode != 0:
        raise OpLockError("git rev-parse --git-common-dir failed at {} (exit {}); refusing, "
                          "never a repository-root fallback".format(store_root, proc.returncode))
    try:
        out = proc.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise OpLockError("git rev-parse --git-common-dir output at {} is not decodable; "
                          "refusing".format(store_root))
    # git prints the common-dir path followed by EXACTLY one newline record terminator. Strip ONLY
    # that one terminator, never arbitrary trailing whitespace: a directory path may legitimately end
    # in a space or a tab, and a blanket .strip() would silently redirect the control root to a
    # different (stripped) path (DEF-4).
    if not out.endswith("\n"):
        raise OpLockError("git rev-parse --git-common-dir at {} returned unterminated output; "
                          "refusing".format(store_root))
    common = out[:-1]
    if not common or "\n" in common:
        raise OpLockError("git rev-parse --git-common-dir at {} returned unexpected output; "
                          "refusing".format(store_root))
    if not os.path.isabs(common):
        common = os.path.join(str(store_root), common)
    # Lexical normalization only (abspath, never Path.resolve()): the trust decision belongs to
    # the no-follow walk that opens the result, which refuses any symlinked component.
    return os.path.abspath(common)


def _open_control_dir(control_root_fd, control_root_desc, dirname=CONTROL_DIRNAME):
    """Open (creating on genuine absence) a single-component control home beneath an already-open
    parent: by default this module's own opf-oplock control directory, and by explicit `dirname` a
    SIBLING control home (the OPF-D2B resume substrate's opf-init tree composes this rather than
    reimplementing it). A pre-existing symlink or wrong type at the name refuses up front; only
    the single component is ever created (no parents, and never a missing input root). Creation is
    RESTARTABLE (fix round 6): an existing directory whose owner permissions a restrictive umask
    masked, as a creator killed before correcting its mode leaves it, is repaired to its intended
    mode by _repair_restrictive_mode, whoever created it."""
    label = "{}/{}".format(control_root_desc, dirname)
    st = _lstat_at(control_root_fd, dirname, label)
    if st is None:
        created = False
        try:
            os.mkdir(dirname, _CONTROL_DIR_MODE, dir_fd=control_root_fd)
            created = True
        except FileExistsError:
            pass  # a concurrent creator won the race; classify what is there now
        except OSError as exc:
            raise OpLockError("cannot create control directory {} ({})".format(label, exc))
        if created:
            try:
                os.fsync(control_root_fd)
            except OSError as exc:
                raise OpLockError("created control directory {}, but the fsync of its parent "
                                  "after the creation FAILED ({})".format(label, exc))
        st = _lstat_at(control_root_fd, dirname, label)
        if st is None:
            raise OpLockError("control directory {} vanished after creation; refusing".format(
                label))
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError("control directory name {} is a symlink; refusing".format(label))
    if not stat.S_ISDIR(st.st_mode):
        raise OpLockError("control directory name {} is not a directory".format(label))
    st = _repair_restrictive_mode(control_root_fd, dirname, label, st, is_dir=True)
    with _FdOwner() as owner:
        fd = owner.adopt(_open_dir_at(control_root_fd, dirname, label))
        _validate_ctl_dir_fd(fd, label)
        return owner.transfer(fd)


def _repair_restrictive_mode(parent_fd, name, label, st, is_dir):
    """Self-heal a control object whose owner permissions a restrictive umask masked (fix round 6,
    RESTARTABLE initialization). A creator killed between creating the control directory or the
    anchor and correcting its mode leaves, under umask 0o444 or 0o777, a directory of mode 0311 or
    0000 or an anchor of mode 0200 or 0000, which every later open refuses; this repairs it on the
    next acquisition, whether this call or an earlier one created it. `st` is the no-follow stat of
    `name` beneath the trusted parent_fd. Nothing is changed unless the object is exactly the
    expected type (a directory, or a regular file with one link), owned by the current uid, and
    lacks an owner permission this module needs (read, write, and search for the directory; read and
    write for the anchor). Such an object is repaired ONLY when its mode is a subset of the intended
    one (0755 or 0644), which is all a umask can leave: a mode carrying any other bit (a group or
    other write, a special bit), or a hard-linked anchor, is not what an interrupted creation
    leaves, and refuses unchanged. One special bit is the exception (fix round 7): a directory
    created beneath a set-group-ID parent inherits that parent's S_ISGID bit and group from mkdir
    itself, so a control directory carrying S_ISGID is what an interrupted creation leaves exactly
    when the trusted parent (the parent_fd already held, fstat'ed here) is a set-group-ID directory
    and the new directory carries the parent's group; only then is the bit accepted, and it is
    preserved by the repair. The repair sets EXACTLY the intended mode (plus that inherited bit),
    the state an uninterrupted creation ends in, so it never loosens past it, and is bound to the
    stat'ed object and never follows a symbolic link (_chmod_bound). The name is then re-stat'ed
    no-follow and must still bind that same (st_dev, st_ino) object, now at the intended mode, or
    this refuses; where the kernel drops an inherited S_ISGID on the chmod (it clears the bit for a
    caller outside the directory's group), the intended mode without it is accepted. Returns the
    stat to classify."""
    if is_dir:
        needed, intended, right_type = stat.S_IRWXU, _CONTROL_DIR_MODE, stat.S_ISDIR(st.st_mode)
    else:
        needed = stat.S_IRUSR | stat.S_IWUSR
        intended, right_type = _CONTROL_FILE_MODE, stat.S_ISREG(st.st_mode)
    if not right_type or st.st_uid != os.getuid() or (st.st_mode & needed) == needed:
        return st                          # nothing to repair; the caller's checks decide the rest
    mode = stat.S_IMODE(st.st_mode)
    inherited = 0
    if is_dir and mode & stat.S_ISGID and _inherits_setgid(parent_fd, label, st):
        inherited = stat.S_ISGID
    if mode & ~(intended | inherited) or (not is_dir and st.st_nlink != 1):
        shape = "a mode within {:o}".format(intended | inherited) if is_dir \
            else "a mode within {:o}, one link".format(intended)
        raise OpLockError("{} lacks the owner permissions this module needs (mode {:o}{}) but is "
                          "not what an interrupted creation leaves ({}); refusing to repair it "
                          "(manual intervention required)".format(
                              label, mode, "" if is_dir else ", {} link(s)".format(st.st_nlink),
                              shape))
    _chmod_bound(parent_fd, name, label, st, intended | inherited)
    after = _lstat_at(parent_fd, name, label)
    if after is None or stat.S_IFMT(after.st_mode) != stat.S_IFMT(st.st_mode) \
            or (after.st_dev, after.st_ino) != (st.st_dev, st.st_ino) \
            or stat.S_IMODE(after.st_mode) not in (intended | inherited, intended):
        raise OpLockError("{} changed, or is not at its intended mode {:o}, after its mode repair; "
                          "refusing".format(label, intended | inherited))
    return after


def _inherits_setgid(parent_fd, label, st):
    """Whether a control directory's S_ISGID bit is the one mkdir inherits (fix round 7): true only
    when the trusted parent descriptor is a set-group-ID directory and the directory `st` describes
    carries the parent's group, as inheritance gives it. An fstat failure refuses."""
    try:
        pst = os.fstat(parent_fd)
    except OSError as exc:
        raise OpLockError("cannot fstat the parent of {} to classify its set-group-ID bit "
                          "({})".format(label, exc))
    return stat.S_ISDIR(pst.st_mode) and bool(pst.st_mode & stat.S_ISGID) \
        and st.st_gid == pst.st_gid


def _chmod_bound(parent_fd, name, label, st, mode):
    """chmod the object `name` beneath parent_fd to `mode` WITHOUT following a symbolic link and
    bound to the object `st` describes (fix round 6). Where O_PATH exists (Linux), the name is
    opened O_PATH|O_NOFOLLOW (which needs no permission on the object itself, so a mode-0000 object
    opens), the OPENED object is confirmed to be that same (st_dev, st_ino), type, and owner, and
    its mode is set through that descriptor's /proc/self/fd entry, which names the opened object
    itself and cannot be redirected by a later swap of the name. Elsewhere the platform's no-follow
    chmod beneath the parent descriptor is used when it is supported, and the caller's re-stat
    confirms the identity afterwards; where neither is available this refuses rather than follow a
    link."""
    if hasattr(os, "O_PATH"):
        with _FdOwner() as owner:
            try:
                fd = owner.adopt(os.open(name, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         dir_fd=parent_fd))
                ost = os.fstat(fd)
                if stat.S_IFMT(ost.st_mode) != stat.S_IFMT(st.st_mode) \
                        or (ost.st_dev, ost.st_ino) != (st.st_dev, st.st_ino) \
                        or ost.st_uid != os.getuid():
                    raise OpLockError("{} changed before its mode repair; refusing".format(label))
                os.chmod("/proc/self/fd/{}".format(fd), mode)
            except OSError as exc:
                raise OpLockError("cannot repair the mode of {} ({}); refusing (manual "
                                  "intervention required)".format(label, exc))
        return
    if os.chmod in os.supports_follow_symlinks and os.chmod in os.supports_dir_fd:
        try:
            os.chmod(name, mode, dir_fd=parent_fd, follow_symlinks=False)
        except (OSError, NotImplementedError, ValueError) as exc:
            raise OpLockError("cannot repair the mode of {} ({}); refusing (manual intervention "
                              "required)".format(label, exc))
        return
    raise OpLockError("cannot repair the mode of {} without following a symbolic link on this "
                      "platform; refusing (manual intervention required: chmod it to {:o})".format(
                          label, mode))


# --- the anchor (leg 1) ---------------------------------------------------------------------------


def _open_anchor(ctl_fd):
    """Open the persistent mutex anchor, existing-first: a pre-existing regular file is opened
    O_RDWR|O_NOFOLLOW; creation (O_CREAT|O_EXCL) happens only on genuine absence; a symlink or any
    other type at the name refuses. The opened file is validated (regular, link count exactly 1,
    owned by us, not group/other-writable) before it may carry the flock. Creation is RESTARTABLE
    (fix round 6): a pre-existing anchor whose owner read or write a restrictive umask masked, as a
    creator killed between its creation and its fchmod leaves it, is repaired to 0644 by
    _repair_restrictive_mode before it is opened."""
    st = _lstat_at(ctl_fd, ANCHOR_NAME, ANCHOR_NAME)
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            raise OpLockError("mutex anchor name is a symlink; refusing")
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("mutex anchor name is not a regular file; refusing")
        _repair_restrictive_mode(ctl_fd, ANCHOR_NAME, "mutex anchor", st, is_dir=False)
    with _FdOwner() as owner:
        if st is None:
            try:
                fd = owner.adopt(os.open(ANCHOR_NAME,
                                         os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                         | os.O_NONBLOCK | os.O_CLOEXEC, 0o644, dir_fd=ctl_fd))
            except OSError as exc:
                # EEXIST here means a lost creation race or a dangling symlink at the name: refuse
                # rather than guess which; the next attempt classifies what is there.
                raise OpLockError("cannot create mutex anchor ({})".format(exc))
            # Fix round 5: the creating process's umask filters the requested mode, so the mode is
            # set explicitly; a umask masking the owner's read or write would otherwise leave an
            # anchor every later O_RDWR open refuses. One try for both steps, so this round adds no
            # nested try statement's line to the function.
            step = "set the mode of the new mutex anchor"
            try:
                os.fchmod(fd, _CONTROL_FILE_MODE)
                step = "fsync control directory after anchor creation"
                os.fsync(ctl_fd)
            except OSError as exc:
                raise OpLockError("cannot {} ({})".format(step, exc))
        else:
            try:
                fd = owner.adopt(os.open(ANCHOR_NAME,
                                         os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                         dir_fd=ctl_fd))
            except OSError as exc:
                raise OpLockError("cannot open mutex anchor no-follow ({})".format(exc))
        _validate_file_fd(fd, "mutex anchor")
        return owner.transfer(fd)


def _post_lock_anchor_check(ctl_fd, anchor_fd):
    """After the flock: re-fstat the locked fd (still a regular file, link count still exactly 1)
    and take a FRESH no-follow name-stat, requiring the name to still bind the locked inode. An
    anchor unlinked or swapped between our open and our flock would otherwise leave two holders
    locked on two different inodes. Returns the anchor (st_dev, st_ino) identity."""
    st = _validate_file_fd(anchor_fd, "mutex anchor (post-lock)")
    name_st = _lstat_at(ctl_fd, ANCHOR_NAME, "mutex anchor (post-lock name check)")
    if name_st is None or not stat.S_ISREG(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != (st.st_dev, st.st_ino):
        raise OpLockError("mutex anchor was unlinked or replaced between open and lock; refusing "
                          "(the locked inode is no longer the inode the name binds)")
    return (st.st_dev, st.st_ino)


# --- the machine store (leg 3 parent) --------------------------------------------------------------


def _open_machine_dir(store_root_fd, machine_rel, store_root):
    """Walk the RESOLVED machine-store relative path beneath the store root, one no-follow dir_fd
    component at a time. Every failure refuses (no silent skip, and nothing is created: the
    machine store is an input root this module never creates)."""
    if type(machine_rel) is not str or not machine_rel:
        raise OpLockError("machine-store relative path is missing from the resolution")
    parts = machine_rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise OpLockError("machine-store relative path {!r} is not canonical".format(machine_rel))
    with _FdOwner() as owner:
        fd = None
        for comp in parts:
            parent = store_root_fd if fd is None else fd
            nfd = owner.adopt(_open_dir_at(parent, comp, "{}/{}".format(store_root, comp)))
            # The child is owned BEFORE the parent closes, and the parent's ownership is cleared
            # before its close: a failing close of the parent can neither leak the child (the owner
            # still closes it) nor double-close the parent (on Linux a close that reports an error
            # has still released the descriptor).
            if fd is not None:
                owner.close(fd, "cannot close a machine-store walk descriptor ({})")
            fd = nfd
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise OpLockError("cannot fstat machine store {}/{} ({})".format(
                store_root, machine_rel, exc))
        # Ownership only: the machine store is adopter content, not this module's control
        # directory, so the group/other-write hardening is not imposed on it (a DISCLOSED
        # residual, above); the lease file itself is created 0o644 by us and fully identity- and
        # byte-verified at release.
        if st.st_uid != os.getuid():
            raise OpLockError("machine store {}/{} is owned by uid {}, not the current uid "
                              "{}".format(store_root, machine_rel, st.st_uid, os.getuid()))
        return owner.transfer(fd)


# --- stale records and explicit recovery ------------------------------------------------------------


def _classify_stale(dir_fd, name, label):
    """Whether a control record already exists at `name` (a crash artefact under a free anchor).
    True for a plain regular file; False for genuine absence; anything else (a symlink, a FIFO, a
    wrong type) refuses outright, recovery included: manual intervention is required there."""
    st = _lstat_at(dir_fd, name, label)
    if st is None:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise OpLockError("{} name is a symlink; refusing (manual intervention required)".format(
            label))
    if not stat.S_ISREG(st.st_mode):
        raise OpLockError("{} name is not a regular file; refusing (manual intervention "
                          "required)".format(label))
    return True


def _remove_staging_garbage(dir_fd, name, label):
    """D1: remove the staging leftovers a process killed mid-publication left for the control record
    `name` beneath dir_fd. Called ONLY under the held anchor flock, before stale classification, so
    no cooperating publisher can be mid-publication: a leftover never became a record, is never a
    finding, and is simply removed. A leftover killed after its os.link shares its inode with the
    complete final record (link count 2); removing the staging name restores the final record's
    single link, so the recovery gate then reads it like any other stale record. The directory is
    listed through a FRESH descriptor (never a rewind of a retained one); only a plain regular file
    whose name matches the staging pattern EXACTLY is removed, and a staging name bound to anything
    else refuses (manual intervention). Returns the number of leftovers removed."""
    with _FdOwner() as owner:
        try:
            list_fd = owner.adopt(os.open(".", _DIR_OPEN_FLAGS, dir_fd=dir_fd))
            entries = os.listdir(list_fd)
        except OSError as exc:
            raise OpLockError("cannot list the {} directory for staging leftovers ({})".format(
                label, exc))
    removed = 0
    for entry in entries:
        if not _is_staging_name(entry, name):
            continue
        st = _lstat_at(dir_fd, entry, "{} staging leftover {}".format(label, entry))
        if st is None:
            continue
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("{} staging leftover {} is not a regular file; refusing (manual "
                              "intervention required)".format(label, entry))
        try:
            os.unlink(entry, dir_fd=dir_fd)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OpLockError("cannot remove {} staging leftover {} ({})".format(label, entry, exc))
        removed += 1
    if removed:
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            raise OpLockError("cannot fsync the {} directory after removing staging leftovers "
                              "({})".format(label, exc))
    return removed


def _validate_recovery_active(doc):
    """Validate a stale ACTIVE record against its COMPLETE schema before its holder or owner is
    trusted for a recovery decision (DEF-2). Without this a record with a missing holder yields
    None, and a paired lease with a missing holder also yields None, so None == None would compare
    equal and a live holder be seized; an unchecked top-key set would likewise admit a forged or
    truncated record. The full closed top-key set, the supported integer schema, a required
    non-empty holder / operation / op_id / acquired_at, an [owner] table, and owner/record
    consistency (the owner's session is the record's op_id and the owner's utc is the record's
    acquired_at) are all required; any failure refuses recovery. The [owner] identity fields
    themselves are validated by _journal.owner_confirmed_dead, which reads a malformed owner as
    possibly-live. A version-1 record (the earlier format, written before the [machine_store]
    pairing identity existed) is refused with a message naming that cause, since its paired lease
    cannot be located safely (it may live in a sibling worktree's machine store). Returns the
    validated ([owner] table, [machine_store] table)."""
    if doc.get("schema") == _LEGACY_ACTIVE_SCHEMA and type(doc.get("schema")) is int \
            and "machine_store" not in doc:
        raise OpLockError("the stale active record uses the earlier schema {} format, which carries "
                          "no [machine_store] identity, so the machine store holding its paired "
                          "lease (possibly a sibling git worktree's) cannot be confirmed; refusing "
                          "recovery (manual intervention required: remove the active record and the "
                          "paired lease together once the holder is known dead)".format(
                              _LEGACY_ACTIVE_SCHEMA))
    if set(doc) != set(ACTIVE_TOP_KEYS):
        raise OpLockError("the stale active record top-level keys {} do not equal the closed set "
                          "{}; refusing recovery (manual intervention required)".format(
                              sorted(doc), sorted(ACTIVE_TOP_KEYS)))
    if type(doc["schema"]) is not int or doc["schema"] != _ACTIVE_SCHEMA:
        raise OpLockError("the stale active record schema is not the supported version {}; refusing "
                          "recovery (manual intervention required)".format(_ACTIVE_SCHEMA))
    for key in ("holder", "operation", "op_id", "acquired_at"):
        val = doc.get(key)
        if type(val) is not str or not val:
            raise OpLockError("the stale active record {} is missing or not a non-empty string; "
                              "refusing recovery (manual intervention required)".format(key))
    owner = doc.get("owner")
    if not isinstance(owner, dict):
        raise OpLockError("the stale active record carries no [owner] identity; its holder may be "
                          "live; refusing recovery (manual intervention required)")
    if owner.get("session") != doc["op_id"] or owner.get("utc") != doc["acquired_at"]:
        raise OpLockError("the stale active record [owner] is inconsistent with the record it sits "
                          "in (owner session/utc do not match op_id/acquired_at); refusing recovery "
                          "(manual intervention required)")
    machine = doc.get("machine_store")
    if not isinstance(machine, dict) or set(machine) != set(MACHINE_STORE_KEYS):
        raise OpLockError("the stale active record [machine_store] is missing or its keys do not "
                          "equal the closed set {}; refusing recovery (manual intervention "
                          "required)".format(sorted(MACHINE_STORE_KEYS)))
    if type(machine["path"]) is not str or not machine["path"]:
        raise OpLockError("the stale active record [machine_store] path is missing or not a "
                          "non-empty string; refusing recovery (manual intervention required)")
    for key in ("dev", "ino"):
        if not _is_canonical_decimal(machine[key]):
            raise OpLockError("the stale active record [machine_store] {} is not a canonical "
                              "decimal string; refusing recovery (manual intervention "
                              "required)".format(key))
    return owner, machine


def _is_canonical_decimal(value):
    """True for a canonical unsigned decimal string (ASCII digits, no sign, no leading zero, at most
    _MAX_DECIMAL_DIGITS digits): exactly what str() of a non-negative st_dev/st_ino produces."""
    if type(value) is not str or not value or len(value) > _MAX_DECIMAL_DIGITS:
        return False
    if not all("0" <= ch <= "9" for ch in value):
        return False
    return value == "0" or value[0] != "0"


def _machine_store_table(machine_path, machine_st):
    """The [machine_store] table an active record persists: the machine-store path (a recovery
    input, examined by _recorded_machine_store_present) and its (st_dev, st_ino) identity as
    canonical decimal strings (the pairing binding)."""
    return dict(path=machine_path, dev=str(machine_st.st_dev), ino=str(machine_st.st_ino))


def _validate_recovery_lease(doc, active_doc):
    """Validate a stale LEASE against its COMPLETE schema before it is paired with the validated
    active record (DEF-2): the full closed top-key set, the supported integer schema, and a required
    non-empty holder / operation / acquired_at, so a missing lease holder can never compare equal to
    a missing active holder as None == None. The pairing then requires the holder, operation, and
    acquired_at to EQUAL the active record's, since acquisition writes both records from the same
    values; a lease from any other acquisition is not this dead holder's and is never seized."""
    if set(doc) != set(_opf_check.LEASE_TOP_KEYS):
        raise OpLockError("the stale lease top-level keys {} do not equal the closed set {}; "
                          "refusing recovery (manual intervention required)".format(
                              sorted(doc), sorted(_opf_check.LEASE_TOP_KEYS)))
    if type(doc["schema"]) is not int or doc["schema"] != _SCHEMA:
        raise OpLockError("the stale lease schema is not the supported version {}; refusing "
                          "recovery (manual intervention required)".format(_SCHEMA))
    for key in ("holder", "operation", "acquired_at"):
        val = doc.get(key)
        if type(val) is not str or not val:
            raise OpLockError("the stale lease {} is missing or not a non-empty string; refusing "
                              "recovery (manual intervention required)".format(key))
    if doc["holder"] != active_doc["holder"]:
        raise OpLockError("the stale lease holder does not match the confirmed-dead active "
                          "record holder; refusing recovery (manual intervention required)")
    if doc["operation"] != active_doc["operation"] \
            or doc["acquired_at"] != active_doc["acquired_at"]:
        raise OpLockError("the stale lease operation/acquired_at do not match the confirmed-dead "
                          "active record (not the same acquisition); refusing recovery (manual "
                          "intervention required)")


def _recorded_machine_store_present(machine):
    """D4: whether the machine store a stale active record names still exists. The recorded path is
    walked no-follow from the filesystem root to its parent (_opf_store._open_dir_nofollow) and its
    final component is stat'ed no-follow beneath that parent. Returns True when the recorded path
    binds an object with the RECORDED (st_dev, st_ino): the paired lease may live there, so recovery
    from any other checkout must refuse. Returns False when the path is absent (ENOENT at any
    component) or binds an object with a different identity: the recorded machine store, and with it
    the paired lease, is taken as gone, so no lease can be stranded, EXCEPT under the disclosed
    moved-store limit (a checkout renamed or moved elsewhere also reads as absent here, and its
    lease is then left as a lone lease needing manual removal). ANY other outcome (a symlinked or
    non-directory ancestor, a permission or I/O error, a path that is not absolute) is a refusal:
    whether the paired lease still exists cannot be confirmed, never read as gone."""
    path = machine["path"]
    parent, base = os.path.split(path)
    if not os.path.isabs(path) or "\x00" in path or not base or base in (".", ".."):
        raise OpLockError("the stale active record [machine_store] path {!r} is not a canonical "
                          "absolute path, so whether its paired lease still exists cannot be "
                          "confirmed; refusing recovery (manual intervention required)".format(path))
    try:
        parent_fd = _opf_store._open_dir_nofollow(parent)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OpLockError("cannot confirm whether the recorded machine store {!r} still exists "
                          "({}); its paired lease may live there; refusing recovery (manual "
                          "intervention required)".format(path, exc))
    with _FdOwner() as owner:
        owner.adopt(parent_fd)
        try:
            st = os.stat(base, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise OpLockError("cannot confirm whether the recorded machine store {!r} still exists "
                              "({}); its paired lease may live there; refusing recovery (manual "
                              "intervention required)".format(path, exc))
    return (str(st.st_dev), str(st.st_ino)) == (machine["dev"], machine["ino"])


def _require_holder_confirmed_dead(ctl_fd, machine_fd, stale_active, stale_lease, machine_st,
                                   machine_path):
    """The recovery LIVENESS GATE (fail-closed). Under recover=True a stale record is cleared ONLY
    when the recorded holder is CONFIRMED DEAD, reusing the journal's possibly-live-never-seized
    model (_journal.owner_confirmed_dead) over the owner identity the active record persists. A
    possibly-live, cross-host, or malformed holder, or a stale lease with no paired active record,
    RAISES rather than being seized, so a live holder whose records are exposed under a split
    anchor can never be recovered into a two-holder state. Both records are validated against their
    COMPLETE schemas first (DEF-2). Returns the (st_dev, st_ino) identity and exact bytes it read
    for the active record and, when present, the lease, so the caller's delete removes ONLY those
    same objects with those same bytes (DEF-1: a record swapped to a LIVE holder B's after this
    read is refused and preserved, never seized). Called ONLY on the explicit recover=True path,
    under the held anchor flock, before any _recover_stale delete runs.

    Cross-worktree PAIRING: the active record lives under the control root, which linked git
    worktrees SHARE, while the lease lives in the acquirer's OWN machine store. The active record's
    [machine_store] (st_dev, st_ino) must therefore equal the recovering acquirer's machine store
    (`machine_st`, the fstat of `machine_fd`); otherwise the paired lease lives in another checkout's
    machine store, this recovery cannot see or clear it, and deleting the shared active record would
    strand that lease with no owner-bearing record. Such a recovery is REFUSED (naming both machine
    stores) and nothing is deleted; recovery is run from the owning checkout instead. The refusal
    applies only while the RECORDED machine store still exists: when its recorded path is absent,
    or now binds a different object, the recorded machine store and the paired lease inside it are
    taken as gone (a deleted worktree, or this checkout's machine store destroyed and recreated),
    and recovery continues to the liveness gate (D4); a checkout MOVED rather than deleted reads the
    same way, the disclosed moved-store limit under which its lease is left as a lone lease."""
    if not stale_active:
        raise OpLockError("a stale lease exists with no paired active record; its holder carries no "
                          "owner identity and cannot be confirmed dead (possibly live); refusing "
                          "recovery (manual intervention required)")
    doc, active_ident, active_bytes = _read_control_record(ctl_fd, ACTIVE_NAME, "active record")
    owner, machine = _validate_recovery_active(doc)
    if (machine["dev"], machine["ino"]) != (str(machine_st.st_dev), str(machine_st.st_ino)) \
            and _recorded_machine_store_present(machine):
        raise OpLockError("the stale active record is paired with a different machine store: the one "
                          "recorded at {!r} (device/inode {}/{}), which still exists with that "
                          "identity, not this checkout's machine store at {!r} (device/inode {}/{}); "
                          "its lease is not visible here and deleting the shared active record would "
                          "strand it; refusing recovery (run recovery from the checkout that owns "
                          "that machine store)".format(
                              machine["path"], machine["dev"], machine["ino"], machine_path,
                              machine_st.st_dev, machine_st.st_ino))
    node = owner.get("nodename")
    if not isinstance(node, str) or not node:
        raise OpLockError("the stale active record owner has no usable nodename; its holder may be "
                          "live; refusing recovery (manual intervention required)")
    here = os.uname().nodename
    if node != here:
        raise OpLockError("the stale active record was written on host {!r}, not this host {!r}; a "
                          "cross-host holder is never seized; refusing recovery (manual "
                          "intervention required)".format(node, here))
    if not _journal.owner_confirmed_dead(owner):
        raise OpLockError("the stale active record holder is not confirmed dead (possibly live or a "
                          "malformed owner identity); a possibly-live holder is never seized; "
                          "refusing recovery (manual intervention required)")
    lease_ident = lease_bytes = None
    if stale_lease:
        lease_doc, lease_ident, lease_bytes = _read_control_record(
            machine_fd, _opf_check.LEASE_NAME, "lease")
        _validate_recovery_lease(lease_doc, doc)
    return active_ident, active_bytes, lease_ident, lease_bytes


def _recover_stale(dir_fd, name, ident, expected_bytes, label):
    """Identity- and byte-verified unlink of an OBSERVED stale control record, under the held anchor
    flock and only on the explicit recover=True path AFTER the liveness gate confirmed the holder
    dead. `ident` and `expected_bytes` are the (st_dev, st_ino) identity and exact bytes the
    liveness gate read for THIS record; the record is removed ONLY when it is STILL that same object
    carrying those same bytes, re-verified here before the unlink. A record swapped after the
    liveness read (a LIVE holder B publishing fresh records under a split anchor) fails the identity
    or byte check and is refused and PRESERVED rather than deleted into a two-holder state (DEF-1).
    The opened object (not just the name) must be a plain singly-linked regular file; the name must
    still bind that same inode at the final pre-unlink re-check; anything else refuses and
    preserves."""
    with _FdOwner() as owner:
        try:
            fd = owner.adopt(os.open(name, _FILE_READ_FLAGS, dir_fd=dir_fd))
        except FileNotFoundError:
            raise OpLockError("stale {} vanished during recovery; refusing (another actor is "
                              "interfering)".format(label))
        except OSError as exc:
            raise OpLockError("cannot open stale {} for recovery ({})".format(label, exc))
        _recover_stale_verify(fd, ident, expected_bytes, label)
    name_st = _lstat_at(dir_fd, name, "stale {} (pre-unlink re-check)".format(label))
    if name_st is None or not stat.S_ISREG(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != ident:
        raise OpLockError("stale {} changed identity during recovery; refusing".format(label))
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError as exc:
        raise OpLockError("cannot unlink stale {} ({})".format(label, exc))
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        raise _UnlinkNotDurable("stale {} was unlinked, but the directory fsync after the unlink "
                                "FAILED ({}), so the removal may not survive a power loss; "
                                "recovery stops here".format(label, exc))


def _recover_stale_verify(fd, ident, expected_bytes, label):
    """The opened-object checks of _recover_stale: a plain singly-linked regular file that is STILL
    the gate-read (st_dev, st_ino) object carrying the gate-read bytes; anything else refuses and
    preserves. A raw OS error is normalized to OpLockError."""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OpLockError("stale {} is not a plain singly-linked regular file; refusing "
                              "recovery (manual intervention required)".format(label))
        if (st.st_dev, st.st_ino) != ident:
            raise OpLockError("stale {} was replaced after the liveness gate read it (device/inode "
                              "mismatch); refusing recovery and PRESERVING it (a live holder's "
                              "record is never seized)".format(label))
        if st.st_size != len(expected_bytes):
            raise OpLockError("stale {} size {} no longer matches the {} bytes the liveness gate "
                              "read; refusing recovery and PRESERVING it (a live holder's record is "
                              "never seized)".format(label, st.st_size, len(expected_bytes)))
        data = bytearray()
        while len(data) <= len(expected_bytes):
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if bytes(data) != expected_bytes:
            raise OpLockError("stale {} no longer carries the bytes the liveness gate confirmed "
                              "dead; refusing recovery and PRESERVING it (a live holder's record is "
                              "never seized)".format(label))
    except OSError as exc:
        raise OpLockError("cannot verify stale {} for recovery ({})".format(label, exc))


# --- control-record create and verified unlink ------------------------------------------------------


def _unlink_created_on_failure(dir_fd, names, fd):
    """LOW-1: best-effort removal of the names a FAILED or INTERRUPTED publication bound to its new
    inode, so neither a staging leftover nor a final record the caller never received is stranded.
    `names` are the CANDIDATE names (the staging name first, then the final name once its os.link
    was ATTEMPTED); which of them the publication actually bound is established by OBSERVATION
    here (fix round 4, H1), never from bookkeeping an interruption could have skipped: a candidate
    is bound when a no-follow stat finds it binding the open fd's (st_dev, st_ino). A final name
    that is absent or binds another inode (an EEXIST refusal, or a link never made) is not ours and
    is left alone; a staging name binding another inode is substituted and left in place. The inode
    was created O_CREAT|O_EXCL under a fresh staging name, so it is ours; its link count must equal
    the number of bound names (any further link is not ours and pins the inode: everything is left
    in place), and each bound name is re-stat'ed against the identity before its unlink, so a
    substituted target is never removed. Returns None when every bound name was removed (or was
    already gone); returns a reason string NAMING the stranded leftover when one could not be
    removed, so the caller aggregates it into the raise rather than claim a clean unwind it did not
    achieve (DEF-5: the disclosure stays accurate, the cleanup failure is never swallowed)."""
    if not names:
        return None
    shown = names[-1]
    try:
        fst = os.fstat(fd)
    except OSError as exc:
        return "could not stat the torn {} to remove it ({}); it may be stranded".format(shown, exc)
    ident = (fst.st_dev, fst.st_ino)
    bound = []
    for name in names:
        try:
            name_st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue                       # not bound (never linked, or already gone)
        except OSError as exc:
            return ("could not stat {} to decide whether this publication bound it ({}); it may "
                    "be stranded".format(name, exc))
        if stat.S_ISREG(name_st.st_mode) and (name_st.st_dev, name_st.st_ino) == ident:
            bound.append(name)
        elif name == names[0]:
            return ("the torn {} name no longer binds the created inode (substituted); left in "
                    "place".format(name))
    if fst.st_nlink != len(bound):
        return ("the torn {} has {} links, not the {} names this publication bound (not "
                "exclusively ours); left in place and possibly stranded".format(
                    shown, fst.st_nlink, len(bound)))
    for name in reversed(bound):
        try:
            name_st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue                       # the name is already gone: nothing stranded under it
        except OSError as exc:
            return "could not re-stat the torn {} before removal ({}); it may be stranded".format(
                name, exc)
        if not stat.S_ISREG(name_st.st_mode) or (name_st.st_dev, name_st.st_ino) != ident:
            return ("the torn {} name no longer binds the created inode (substituted); left in "
                    "place".format(name))
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError as exc:
            return "could not unlink the torn {} ({}); it is stranded".format(name, exc)
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        return "could not fsync the directory after removing the torn {} ({})".format(shown, exc)
    return None


def _create_control_file(dir_fd, name, payload, label, owner=None, publisher_pid=None):
    """ATOMIC, exclusive publication of a control record (D1). The payload is written in full to a
    fresh staging file (O_CREAT|O_EXCL under a unique _staging_name) with the full-byte write loop
    and fsynced; only then is it published at `name` by os.link, which refuses an existing name
    (EEXIST) exactly as O_EXCL did; the staging name is then unlinked and the directory fsynced.
    A process killed at ANY point therefore leaves either no record at `name` or the complete,
    fsynced one, plus at most a staging leftover that the next acquisition removes as garbage under
    the anchor flock. Returns the still-open fd (of the published inode) and its (st_dev, st_ino)
    identity; the caller retains both. With `owner` (a caller's _FdOwner), the staging descriptor is
    adopted straight into that owner and stays there on success, with no transfer step between
    the publication and the caller's ownership, and a failure closes only this descriptor; without
    it, a private owner holds the descriptor and transfers it to the caller on success.

    On any FAILURE or INTERRUPTION before return (an OSError, or any BaseException such as a
    KeyboardInterrupt), the publication is treated as POTENTIALLY PUBLISHED (fix round 4, H1): the
    names it bound are established by observation (the staging name, and the final name once its
    os.link was attempted, each counted only when it binds the created inode) and removed by
    verified identity, so an interruption landing just after the link or the staging unlink
    completed can no longer leave an untracked final record behind; the descriptor is closed
    through the single descriptor owner before raising (LOW-1: nothing is stranded), UNLESS that
    cleanup itself fails, in which case the raise NAMES the leftover rather than implying a clean
    unwind (DEF-5), and the caller must treat the record as possibly present. The staging descriptor
    is protected from the moment it is adopted (fix round 4, M; wording qualified in fix round 5):
    WITHOUT `owner`, by the private owner, which encloses the whole publication as a context manager,
    so an interruption landing before the protected body is entered still closes it (its empty
    staging file is then garbage for the next acquisition); WITH `owner` (the acquisition's path),
    there is no context manager here: _publish_staged's own handler closes the descriptor on a
    failure inside its protected region, and otherwise the caller's owner holds it and the caller's
    unwind closes it. An OSError is normalized to OpLockError; a refusal inside the publication (the
    staging mode check) propagates as the OpLockError it is; any other BaseException is re-raised as
    itself with the cleanup outcome attached as notes; an interruption raised by the cleanup itself
    is re-raised after the descriptor is closed.

    Fix round 9 (claude MED): every mutating step of the publication runs only in the process
    `publisher_pid` (_gated_step), the pid the operation began with: the acquisition passes the
    pid it captured at its entry, so a forked continuation of it is refused before it creates,
    writes, links, or unlinks anything; without it, the pid at this call's entry is used."""
    if publisher_pid is None:
        publisher_pid = os.getpid()
    args = (dir_fd, name, _staging_name(name), payload, label)
    if owner is not None:
        return _publish_staged(*args, owner, False, publisher_pid)
    # The private owner encloses the whole publication as a context manager, and the body is ONE
    # call on the with statement's own line, deliberately: a line boundary between the body's end
    # and the owner's exit (as a separate return line inside the with would create) lies outside
    # every exception range, so an interruption landing there would skip the exit. Every boundary
    # of the body belongs to _publish_staged's own frame instead.
    with _FdOwner() as own: return _publish_staged(*args, own, True, publisher_pid)


def _publish_staged(dir_fd, name, staging, payload, label, owner, transfer, publisher_pid):
    """The body of _create_control_file (fix round 4, M). ONE protected region spans everything
    from the staging open to the return, so no line boundary after the staging descriptor's
    adoption lies outside the failure handler below (a nested try statement's own line is outside
    the enclosing exception ranges, so none sits between the adoption and the protection). The
    progress marker `step` is set BEFORE each syscall it names, so the cleanup's candidate names
    never lag a completed syscall. On success the descriptor is transferred out of `owner` when
    `transfer` is set and otherwise stays owned by it; on failure only this descriptor is closed.
    A failure raised in a FORKED CHILD of the publishing process (fix round 7: a signal handler
    that forked where no deferral stands) removes no name, since the names and the inode are the
    publisher's; the child closes only its own copy of the descriptor. Fix round 8 (claude LOW):
    every note and message states the descriptor close only when it ran; an interruption landing in
    the hand-over to the caller (after the owner released the descriptor, before the caller
    received it) leaves the descriptor owned by nobody, and the note then says it was not closed
    and stays open until this process exits (the disclosed transfer-to-adoption residual).

    Fix round 9 (claude MED): each mutating step (the staging create, the mode set, the write and
    its fsync, the link, the staging retire, the directory fsync) runs through _gated_step, bound
    to `publisher_pid`, the pid the operation began with (for the acquisition, the pid captured at
    its entry), so a forked continuation is refused before it performs any of them rather than
    racing the publisher for the staging and final names. Fix round 9 (codex LOW): a close that an
    interruption cut short after ownership was cleared is reported as UNCONFIRMED, never as
    closed."""
    step = "create the staging file for"
    fd = None
    what = "{} {}".format(step, label)
    try:
        fd = owner.adopt(_gated_step(publisher_pid, what, os.open, staging,
                                     os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                     | os.O_NONBLOCK | os.O_CLOEXEC, _CONTROL_FILE_MODE,
                                     dir_fd=dir_fd))
        step = "set the mode of"
        _gated_step(publisher_pid, "set the mode of " + label, _set_record_mode, fd, label)
        step = "write"
        _gated_step(publisher_pid, "write " + label, _journal._write_all, fd, payload)
        _gated_step(publisher_pid, "fsync " + label, os.fsync, fd)
        step = "verify the staged bytes of"
        _verify_staged(fd, payload, label)
        step = "publish"
        _gated_step(publisher_pid, "publish " + label, os.link, staging, name, src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd, follow_symlinks=False)
        step = "retire the staging name of"
        _gated_step(publisher_pid, "retire the staging name of " + label, os.unlink, staging,
                    dir_fd=dir_fd)
        step = "fsync the directory of"
        _gated_step(publisher_pid, "fsync the directory of " + label, os.fsync, dir_fd)
        st = os.fstat(fd)
        return (owner.transfer(fd) if transfer else fd), (st.st_dev, st.st_ino)
    except BaseException as exc:
        if fd is None:
            # The staging open itself failed, was interrupted, or was refused in a forked
            # continuation: no descriptor was bound.
            if isinstance(exc, OSError):
                raise OpLockError("cannot create the staging file for {} ({})".format(label, exc))
            raise
        owned = fd in owner
        unclosed = ("its descriptor was no longer owned by this publication (the interruption "
                    "landed in its hand-over to the caller), so it was not closed and stays open "
                    "until this process exits")
        if os.getpid() != publisher_pid:
            # Fix round 7: a forked child shares the publisher's names and inode; removing them
            # would destroy the publisher's record mid-publication. Close only the child's copy.
            problems, unconfirmed, interrupt = owner.close_guarded(fd) if owned \
                else ([], [], None)
            if not owned:
                closed = "its descriptor copy was no longer owned by this publication (the " \
                    "interruption landed in its hand-over), so it was not closed and stays open " \
                    "until this process exits"
            elif unconfirmed:
                closed = "the close of its descriptor copy was interrupted ({!r}), so whether it " \
                    "closed is UNCONFIRMED ({})".format(interrupt, _NEVER_RETRIED)
            elif interrupt is not None:
                closed = "the close of its descriptor copy was interrupted ({!r}) before its " \
                    "ownership was released, so its owner's own cleanup closes it".format(interrupt)
            elif problems:
                closed = "the close of its descriptor copy failed ({})".format("; ".join(problems))
            else:
                closed = "it closed only its own descriptor copy"
            exc.add_note("opf-oplock: this failure of the {} publication was raised in a forked "
                         "child (pid {}) of the publisher (pid {}), so this cleanup removed no "
                         "name; {}".format(label, os.getpid(), publisher_pid, closed))
            if interrupt is not None and isinstance(exc, Exception):
                raise interrupt
            raise
        # ANY failure or interruption removes what this call bound and closes the descriptor HERE,
        # because the caller never received the fd and cannot unwind it. Each cleanup step is
        # guarded on its own, so an interruption inside the cleanup still lets the descriptor close.
        # Once the link was attempted the final name is a candidate too: whether it binds our inode
        # is decided by observation, never by bookkeeping.
        candidates = [staging] if step in ("create the staging file for", "set the mode of",
                                           "write", "verify the staged bytes of") \
            else [staging, name]
        problems = []
        interrupt = None
        try:
            strand = _unlink_created_on_failure(dir_fd, candidates, fd)
        except BaseException as cexc:
            interrupt = cexc
            strand = ("the cleanup of the unpublished {} was interrupted, so a leftover may remain "
                      "(a staging leftover is removed as garbage by the next acquisition under the "
                      "anchor flock)".format(label))
        if strand is not None:
            problems.append(strand)
        close_problems, close_unconfirmed, close_interrupt = owner.close_guarded(fd) if owned \
            else ([], [], None)
        # Fix round 9 (codex LOW): the close is named as it happened; "closed" only when it ran and
        # reported success, UNCONFIRMED when an interruption cut it short after ownership cleared.
        if not owned:
            closure = unclosed
        elif close_unconfirmed:
            closure = "the close of its descriptor was interrupted ({!r}), so whether it closed " \
                "is UNCONFIRMED ({})".format(close_interrupt, _NEVER_RETRIED)
        elif close_interrupt is not None:
            closure = "the close of its descriptor was interrupted ({!r}) before its ownership " \
                "was released, so its owner's own cleanup closes it".format(close_interrupt)
        elif close_problems:
            closure = "the close of its descriptor reported a failure ({})".format(
                "; ".join(close_problems))
        else:
            closure = "its descriptor closed"
        if closure != "its descriptor closed":
            problems.append(closure)
        if interrupt is None:
            interrupt = close_interrupt
        if interrupt is not None and isinstance(exc, Exception):
            interrupt.add_note("opf-oplock: raised while cleaning up after a failed write of {} "
                               "({}); {}".format(label, exc, "; ".join(problems) or "no leftover"))
            raise interrupt
        if isinstance(exc, OSError):
            if isinstance(exc, FileExistsError) and step == "publish":
                msg = ("{} already exists under a free anchor (EXPLICIT recovery required: "
                       "recover=True)".format(label))
            else:
                msg = "cannot {} {} ({})".format(step, label, exc)
            if problems:
                raise OpLockError("{}; additionally the torn record could not be cleaned up: "
                                  "{}".format(msg, "; ".join(problems))) from exc
            raise OpLockError(msg) from exc
        if isinstance(exc, OpLockError):
            # A refusal raised inside the publication (the staging mode check): already a clean
            # OpLockError, extended with any leftover the cleanup could not remove.
            if problems:
                raise OpLockError("{}; additionally the unpublished record could not be cleaned "
                                  "up: {}".format(exc, "; ".join(problems))) from exc
            raise
        removal = "the torn record was removed" if strand is None \
            else "the torn record could not be cleaned up: {}".format(strand)
        exc.add_note("opf-oplock: interrupted while writing {}; {}, and {}".format(
            label, removal, closure))
        raise


def _verify_staged(fd, payload, label):
    """Read the staged bytes back before publication (fix round 9, a class-width finding beside
    claude MED): the staging file must hold exactly `payload`, its size and its bytes read at
    explicit offsets from 0 (os.pread, so the shared file offset is neither used nor moved). A
    forked continuation that completed the write loop it was forked inside (a Python-level step no
    pid gate can split) writes through the SAME open file description, whose offset it shares, so
    the payload lands twice; publishing that would leave a record release refuses and recovery
    cannot parse. Any mismatch refuses (an OpLockError the publication's cleanup then handles),
    and an OSError propagates to the publication's own handler."""
    size = os.fstat(fd).st_size
    data = bytearray()
    while len(data) < len(payload) + 1:
        chunk = os.pread(fd, len(payload) + 1 - len(data), len(data))
        if not chunk:
            break
        data += chunk
    if size != len(payload) or bytes(data) != payload:
        raise OpLockError("the staging file for {} holds {} bytes that are not the {} bytes "
                          "written (a forked continuation may have written through the shared "
                          "descriptor); refusing to publish it".format(label, size, len(payload)))


def _set_record_mode(fd, label):
    """Set a staging record's mode to _CONTROL_FILE_MODE explicitly, before anything is written or
    published (fix round 5): the staging open's requested mode is filtered by the process umask, and
    a umask that masks the owner's read or write (0o444, 0o777) would otherwise publish a record that
    release and crash recovery cannot reopen. The result is verified on the open descriptor: owner
    read and write present and no group or other write, or this refuses (an OSError from fchmod or
    fstat propagates to the publication's own handler)."""
    os.fchmod(fd, _CONTROL_FILE_MODE)
    mode = os.fstat(fd).st_mode
    owner_rw = stat.S_IRUSR | stat.S_IWUSR
    if (mode & owner_rw) != owner_rw or mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OpLockError("the staging file for {} has mode {:o} after being set to {:o} (owner "
                          "read and write are required, group and other write refused); refusing "
                          "to publish a record release and recovery could not read".format(
                              label, stat.S_IMODE(mode), _CONTROL_FILE_MODE))


def _verified_unlink(dir_fd, name, ident, expected_bytes, label, outcome=None):
    """Remove a control record ONLY when it is verifiably the one this capability created: re-open
    no-follow, require a plain regular file with link count exactly 1, the retained (st_dev,
    st_ino) identity, the exact retained size, and byte equality with the retained payload; then a
    final pre-unlink name-stat re-check; then unlink by dir_fd and fsync the parent. ANY mismatch
    refuses and PRESERVES the file (never-seize: a replaced or modified record is somebody's
    evidence, not ours to delete). An unlink whose directory fsync then fails raises
    _UnlinkNotDurable (fix round 8): the name WAS removed, only its durability failed.

    Fix round 10 (codex LOW): with `outcome` (a dict), the caller learns what happened even when an
    interruption (any BaseException that is not an OSError) lands anywhere from the unlink through
    the fsync: "done" is set once both completed; on an interruption "interrupted" is set and
    "unlinked" records, by OBSERVATION (a no-follow stat of the name, never bookkeeping the
    interruption could have skipped), whether the name no longer binds the verified record (True),
    still does (False), or could not be observed (None). The interruption itself propagates
    unchanged."""
    with _FdOwner() as owner:
        try:
            fd = owner.adopt(os.open(name, _FILE_READ_FLAGS, dir_fd=dir_fd))
        except FileNotFoundError:
            raise OpLockError("{} is already absent; this capability did not remove it and "
                              "refuses to certify a release leg it did not perform".format(label))
        except OSError as exc:
            raise OpLockError("cannot reopen {} for verified unlink ({})".format(label, exc))
        _verified_unlink_verify(fd, ident, expected_bytes, label)
    name_st = _lstat_at(dir_fd, name, "{} (pre-unlink re-check)".format(label))
    if name_st is None or not stat.S_ISREG(name_st.st_mode) \
            or (name_st.st_dev, name_st.st_ino) != ident:
        raise OpLockError("{} identity changed before the unlink; preserved".format(label))
    step = "unlink"
    try:
        os.unlink(name, dir_fd=dir_fd)
        step = "fsync"
        os.fsync(dir_fd)
        if outcome is not None:
            outcome["done"] = True
    except OSError as exc:
        if step == "unlink":
            raise OpLockError("cannot unlink {} ({})".format(label, exc))
        raise _UnlinkNotDurable("{} was unlinked, but the directory fsync after the unlink FAILED "
                                "({}), so the removal may not survive a power loss".format(
                                    label, exc))
    except BaseException:
        if outcome is not None:
            outcome["interrupted"] = True
            outcome["unlinked"] = _unlinked_by_observation(dir_fd, name, ident)
        raise


def _unlinked_by_observation(dir_fd, name, ident):
    """Whether `name` beneath dir_fd no longer binds the record `ident` (fix round 10): True when
    it is absent or binds another object, False when it still binds that record, None when the
    no-follow stat cannot answer (an OSError), which the caller reports as UNCONFIRMED."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return None
    return (st.st_dev, st.st_ino) != ident


def _verified_unlink_verify(fd, ident, expected_bytes, label):
    """The opened-object checks of _verified_unlink; a raw OS error is normalized to OpLockError."""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OpLockError("{} is no longer a regular file; preserved".format(label))
        if st.st_nlink != 1:
            raise OpLockError("{} link count is {}, expected exactly 1; preserved".format(
                label, st.st_nlink))
        if (st.st_dev, st.st_ino) != ident:
            raise OpLockError("{} was replaced (device/inode mismatch); preserved".format(label))
        if st.st_size != len(expected_bytes):
            raise OpLockError("{} size {} does not match the recorded payload ({} bytes); "
                              "preserved".format(label, st.st_size, len(expected_bytes)))
        data = bytearray()
        while len(data) <= len(expected_bytes):
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        if bytes(data) != expected_bytes:
            raise OpLockError("{} payload does not match the recorded bytes; preserved".format(
                label))
    except OSError as exc:
        # DEF-3: normalize a raw OS failure (an fstat or an EIO read) to OpLockError, so the
        # error-collecting callers (release_operation and the acquire unwind) that catch ONLY
        # OpLockError still run the remaining legs and the descriptor closes (the anchor's too),
        # rather than a raw OSError skipping them and leaking the fd and the records.
        raise OpLockError("cannot verify {} for the release leg ({}); preserved".format(label, exc))


# --- acquire / release ------------------------------------------------------------------------------


def acquire_operation(store_root, operation, holder=None, recover=False):
    """Acquire the shared operation lock for the RESOLVED machine store at `store_root`.

    All three legs are taken or the acquisition unwinds: the anchor flock under the authoritative
    control root, the exclusive active record, and the mandatory lease. A host whose nodename is
    empty is refused before anything else, because the owner identity the active record persists
    would carry no host and a later recovery could never confirm the holder dead (D6). Under the
    held flock, staging leftovers of a publication killed mid-way are removed as garbage (D1). A
    pre-existing active record or lease under a free anchor is a crash artefact: it refuses
    acquisition unless recover=True. recover=True is NOT a licence to seize: the recovery liveness
    gate clears a stale record only when the recorded holder is CONFIRMED DEAD on this host (via the
    journal's possibly-live-never-seized model over the owner identity the active record persists);
    a possibly-live, cross-host, or malformed holder, or a stale lease with no paired active record,
    is refused so a live holder whose records are exposed under a split anchor is never seized into
    a two-holder state. Recovery also refuses an active record paired with a different machine
    store that still exists (a sibling git worktree's; D4), and deletes the lease before the active
    record, so an interrupted recovery leaves only a lone active record that a later recovery can
    clear. Returns an OpCapability; raises OpLockError fail-closed on everything else. An
    interruption (a BaseException that is not an Exception) propagates as itself after the unwind.
    Everything after the argument validation runs with the Python-handled signals deferred (fix
    round 5): a signal arriving meanwhile is delivered only once the acquisition has either failed
    and unwound or fully formed its capability, which is then released before the interruption
    propagates, because the caller never receives it.

    Fix round 10 (codex MED): the acquirer identity is captured by this function's FIRST
    statement, before any preliminary call, and the fork contract is bounded there: a fork that
    lands before that capture (before or at the entry call, or at the capture's own line boundary)
    is a separate caller, which captures its own identity and acquires, or is refused, on its own
    account, contending for the one lock like any other caller; every fork after it is a
    continuation, which receives no capability.
    """
    # Fix round 8 (codex HIGH 1), moved first in fix round 10 (codex MED): the acquirer identity is
    # captured ONCE, here, before any other statement, and everything the acquisition binds to an
    # identity (the default holder, the active record's owner, the capability, and every fork
    # guard) uses this same value, never a later os.getpid(), which a forked continuation would
    # answer with its own pid.
    acquirer_pid = os.getpid()
    if not _containment.probe():
        raise OpLockError("race-free containment primitive absent; fail-closed")
    if type(recover) is not bool:
        raise OpLockError("recover must be a bool (a control parameter is validated, never "
                          "coerced)")
    # D6: the nodename is read ONCE and must be a non-empty string, because recovery refuses an owner
    # with no usable nodename; writing one would create a record no recovery could ever clear.
    nodename = os.uname().nodename
    if type(nodename) is not str or not nodename:
        raise OpLockError("this host reports an empty nodename (os.uname().nodename); refusing to "
                          "acquire, because the active record's owner identity would carry no host "
                          "and a later recovery could never confirm its holder dead")
    if holder is None:
        holder = "opf:{}:{}".format(nodename, acquirer_pid)
    _validate_field("holder", holder)
    _validate_field("operation", operation)

    # Fix round 5: the whole acquisition, recovery included, runs with the Python-handled signals
    # DEFERRED (_SignalDeferral), so no signal-raised exception (a KeyboardInterrupt) can land inside
    # it; a signal that arrives meanwhile is delivered at the deferral's exit, after the capability
    # is fully formed or the acquisition fully unwound. A capability that was fully formed when such
    # a signal was delivered never reaches the caller, so it is released here before the
    # interruption propagates as itself. The body is ONE call on the with statement's own line, and
    # the return sits inside the same try, so no line boundary of this frame after the acquisition
    # lies outside the release below. Fix round 8: the return goes through _handoff, which hands
    # the capability over only in the process that began the acquisition.
    args = (store_root, operation, holder, recover, nodename, acquirer_pid)
    cap = None
    try:
        with _SignalDeferral(): cap = _acquire_body(*args)
        return _handoff(cap, acquirer_pid)
    except BaseException as exc:
        if cap is not None and not cap._released:
            _release_unreturned(cap, exc)
        raise


def _release_unreturned(cap, exc):
    """Release a fully formed capability the caller never received because `exc` (a signal deferred
    across the acquisition, delivered at its end) interrupted the return (fix round 5). The release
    runs under its own deferral; whatever it raises is attached to `exc` as a note, so the original
    interruption is the one that propagates.

    Fix round 6 (codex MEDIUM 2): this process built the capability moments ago, so the acquirer
    identity is already established. The release therefore goes straight to the scoped release
    (_release_scoped, the enclosing cleanup that owns the descriptors), never through
    release_operation, whose fresh /proc identity read can refuse BEFORE that cleanup takes
    ownership (an unreadable /proc/<pid>/stat reads as a differing start time) and would leave the
    descriptors open and the anchor locked.

    Fix round 7 (codex HIGH): the identity is still bound, by pid alone and with no /proc read: the
    capability carries the acquirer's pid (_acquirer_pid, captured once at the acquisition's entry
    since fix round 8, never at the build, which a forked continuation could reach), and only that
    process releases here. A signal handler that FORKS turns this path into
    two processes: the acquirer, whose handler returned and which receives the capability, and a
    child, whose copy of the interruption arrives here. The child shares the acquirer's open file
    descriptions, and a flock belongs to the open file description, so a record removal in the
    child would destroy the acquirer's records (and, before fix round 9, its explicit unlock freed
    the acquirer's lock; no path unlocks explicitly since). A forked child (a pid that differs from
    the builder's; a live parent's pid is never a child's) therefore closes ONLY its own inherited
    descriptor copies, which cannot free the lock the acquirer still references, and never removes
    a record.

    The note states the OBSERVED outcome, each clause derived from what the release recorded as it
    completed each step (fix round 7, codex and claude LOW), never from which exception escaped:
    which records its verified unlinks removed, whether the anchor descriptor's close (which gives
    up this process's hold on the lock, fix round 9) completed, failed, was cut short (UNCONFIRMED),
    or was never confirmed, and whether the other descriptor closes completed, failed, or were cut
    short; "released" only when every step completed, "released only in part" otherwise, and NOT
    released when an interruption landed before the release took ownership (the disclosed
    second-signal window)."""
    what = "opf-oplock: the capability the interrupted acquisition never returned was"
    if os.getpid() != cap._acquirer_pid:
        _release_unreturned_forked(cap, exc, what)
        return
    sink = []
    try:
        with _SignalDeferral(): _release_scoped(cap, sink)
    except BaseException as rexc:
        exc.add_note(_unreturned_note(what, cap, sink, rexc))
    else:
        exc.add_note(_unreturned_note(what, cap, sink, None))


def _handoff(cap, acquirer_pid):
    """Hand a fully formed capability to the caller, only in the process that began the acquisition
    (fix round 8, codex HIGH 1). A signal handler that FORKS inside the acquisition and RETURNS in
    the child lets the child continue the acquisition forward; the capability it builds is bound to
    the entry identity (the acquirer's pid), never the child's own, and it reaches here in a process
    whose pid differs from that identity. Such a forked continuation receives NO capability: it
    closes only its inherited descriptor copies (ownership taken first; a flock belongs to the open
    file description it shares with the acquirer, so those closes cannot free the acquirer's lock,
    and nothing here unlinks, since the records are the acquirer's), under its own deferral, and
    raises a refusal that says what the refusal itself did (fix round 9, claude LOW). The check
    and the return are ONE expression, so no line boundary lies between them; a fork landing
    inside that expression after os.getpid() returned is the disclosed hand-off residual."""
    return cap if os.getpid() == acquirer_pid else _refuse_continuation(cap, acquirer_pid)


def _refuse_continuation(cap, acquirer_pid):
    """_handoff's refusal of a forked continuation (fix round 8): close only this process's
    inherited descriptor copies, under its own deferral, then raise the refusal. The message states
    only what this refusal did (fix round 9, claude LOW): a forward step the continuation ran
    before reaching the hand-off is not observed here, and surfaces as the acquirer's own failure.
    An interruption a close step raised propagates as itself, carrying the refusal as a note."""
    done = {}
    with _SignalDeferral(): _close_inherited(cap, done)
    text = ("acquisition refused in a forked continuation (pid {}) of the acquiring process (pid "
            "{}): a signal handler forked inside the acquisition and this child continued it; it "
            "receives no capability, this refusal removed nothing and did not unlock the anchor, "
            "and {}; any forward step this continuation ran before the hand-off is surfaced by the "
            "acquirer's own failures".format(os.getpid(), acquirer_pid, _inherited_closes(done)))
    if done["interrupt"] is not None:
        done["interrupt"].add_note("opf-oplock: " + text)
        raise done["interrupt"]
    raise OpLockError(text)


def _release_unreturned_forked(cap, exc, what):
    """The forked child's side of _release_unreturned (fix round 7): close only this process's
    inherited descriptor copies, under its own deferral, and note exactly what this release did
    (fix round 9: an interrupted close is UNCONFIRMED, never reported closed)."""
    who = "not released by this process, a forked child (pid {}) of its acquirer (pid {}): this " \
          "release removed no record and did not unlock the anchor (a flock belongs to the open " \
          "file description the child shares with its acquirer, so the child's closes cannot " \
          "free it)".format(os.getpid(), cap._acquirer_pid)
    done = {}
    try:
        with _SignalDeferral(): _close_inherited(cap, done)
    except BaseException as rexc:
        exc.add_note("{} {}, and a signal deferred across closing its inherited descriptor copies "
                     "was delivered as {!r}{}".format(what, who, rexc, "; " + _inherited_closes(
                         done) if "count" in done else ""))
    else:
        exc.add_note("{} {}; {}".format(what, who, _inherited_closes(done)))


def _close_inherited(cap, done):
    """Close a forked child's copies of the capability's descriptors, ownership taken first so no
    number is closed twice; no unlink, and never an explicit unlock (fix round 7; since fix round 9
    no path unlocks explicitly, and a child's closes cannot free a lock its acquirer still
    references). Records in `done` the count, the close failures, the descriptors whose close an
    interruption cut short ("unconfirmed"), and that interruption; nothing is raised here, so the
    caller reports every outcome and re-raises the interruption itself."""
    fds = tuple(fd for fd in (cap._machine_fd, cap._ctl_fd, cap._anchor_fd, cap._active_fd,
                              cap._lease_fd) if fd is not None)
    cap._released = True
    cap._lease_fd = cap._active_fd = cap._anchor_fd = cap._ctl_fd = cap._machine_fd = None
    owner = _FdOwner()
    owner.adopt_all(fds)
    done["problems"], done["unconfirmed"], done["interrupt"] = owner.close_all()
    done["count"] = len(fds)


def _inherited_closes(done):
    """What _close_inherited observed (fix round 9): "it closed only its N inherited descriptor
    copies" only when every close ran and succeeded; otherwise the failures and the UNCONFIRMED
    closes, never a close that was not confirmed."""
    if not done["problems"] and not done["unconfirmed"]:
        return "it closed only its {} inherited descriptor copies".format(done["count"])
    parts = []
    if done["problems"]:
        parts.append("closing some reported failures ({})".format("; ".join(done["problems"])))
    if done["unconfirmed"]:
        parts.append(_unconfirmed_closes(len(done["unconfirmed"]), "inherited descriptor"))
    return "of its {} inherited descriptor copies, {}".format(done["count"], "; ".join(parts))


def _unreturned_note(what, cap, sink, rexc):
    """The note on an unreturned capability's release (fix round 7), derived from the state the
    release scope recorded (sink[0], a _ReleaseScope) as each step completed."""
    if not sink or not sink[0].claimed:
        return ("{} NOT released: {!r} interrupted its release before the release took ownership, "
                "so its records wait for a later recover=True and its descriptors and anchor lock "
                "for this process's exit".format(what, rexc))
    scope = sink[0]
    facts = []
    # Fix round 8 (codex LOW): an unlink that ran is a removal; only its durability can fail, and a
    # removal whose directory fsync failed is named as not durable, never as not performed.
    # Fix round 10 (codex LOW): a removal whose directory fsync was interrupted is named with its
    # durability UNCONFIRMED, and a removal that could not be observed is named as UNCONFIRMED.
    def _shown(n):
        if n in scope.undurable:
            return n + (" (not durably: the directory fsync after its unlink FAILED, so the "
                        "removal may not survive a power loss)")
        if n in scope.durability_unconfirmed:
            return n + (" (its durability UNCONFIRMED: an interruption landed after its unlink and "
                        "before its directory fsync completed, so the removal may not survive a "
                        "power loss)")
        return n
    shown = [_shown(n) for n in scope.removed]
    qualified = scope.undurable or scope.durability_unconfirmed
    if scope.removal_unconfirmed:
        # Fix round 11 (codex and claude LOW): with an UNCONFIRMED removal, each record's own
        # outcome is rendered separately (removed, removal UNCONFIRMED, or not removed), so a record
        # whose removal is unconfirmed is never also reported as not removed.
        parts = []
        for n in ("lease", "active record"):
            if n in scope.removed:
                parts.append("its {} removed".format(_shown(n)))
            elif n in scope.removal_unconfirmed:
                parts.append("its {}'s removal UNCONFIRMED (its removal was interrupted between "
                             "its unlink and the end of its directory fsync, and its name could "
                             "not be observed)".format(n))
            else:
                parts.append("its {} not removed (any that remains waits for a later "
                             "recover=True)".format(n))
        records = "; ".join(parts)
    elif len(scope.removed) == 2:
        records = "its {} and its {} removed".format(*shown) if qualified \
            else "its records removed"
    elif scope.removed:
        records = "its {} removed but not its {} (any that remains waits for a later " \
            "recover=True)".format(shown[0], "active record" if scope.removed[0] == "lease"
                                   else "lease")
    else:
        records = "neither of its records removed (any that remains waits for a later " \
            "recover=True)"
    facts.append(records)
    # Fix round 9 (codex MED 2): the lock is given up only by closing the anchor descriptor, so
    # that close's recorded outcome is the lock clause; (codex LOW) a close an interruption cut
    # short after its ownership was cleared is UNCONFIRMED, never reported as closed.
    anchor = scope.retained[2] if scope.retained else None
    if scope.anchor_close == "":
        facts.append("its anchor descriptor closed (releasing its hold on the lock)")
    elif anchor is not None and anchor in scope.unconfirmed:
        facts.append("its anchor descriptor's close was interrupted, so the release of its hold on "
                     "the lock is UNCONFIRMED ({})".format(_NEVER_RETRIED))
    elif scope.anchor_close is not None:
        facts.append("its anchor descriptor's close reported a failure ({}), so the release of its "
                     "hold on the lock is NOT confirmed".format(scope.anchor_close))
    else:
        # Fix round 10 (claude LOW): only what was recorded; a close of the anchor descriptor may
        # still have run, unattributed, among the general closes.
        facts.append("the release of its hold on the lock NOT confirmed (no anchor-close step "
                     "was recorded)")
    if scope.closed is None:
        facts.append("its descriptor closes NOT completed (its descriptors and the anchor lock may "
                     "stay held until this process exits)")
    elif not scope.closed and not scope.unconfirmed:
        facts.append("its descriptors closed")
    else:
        parts = []
        if scope.closed:
            parts.append("closing its descriptors reported failures ({})".format(
                "; ".join(scope.closed)))
        if scope.unconfirmed:
            count = len(scope.unconfirmed)
            parts.append("the close of {} of its descriptors was interrupted, so whether {} closed "
                         "is UNCONFIRMED ({})".format(count, "it" if count == 1 else "they",
                                                      _NEVER_RETRIED))
        facts.append(" and ".join(parts))
    complete = len(scope.removed) == 2 and not qualified and not scope.removal_unconfirmed \
        and scope.anchor_close == "" and scope.closed == [] and not scope.unconfirmed
    text = "{} {}: {}, {}, and {}".format(what, "released" if complete else "released only in part",
                                          facts[0], facts[1], facts[2])
    if rexc is not None:
        text += "; the release raised {!r}".format(rexc)
    return text


def _acquire_body(store_root, operation, holder, recover, nodename, acquirer_pid):
    """The body of acquire_operation, run with the Python-handled signals deferred (fix round 5):
    resolve the store, take the three legs (recovering first under recover=True), and return the
    capability, or unwind everything and raise. An unwind that runs in a FORKED CHILD of the
    acquiring process (fix round 7: a signal handler that forked where no deferral stands) closes
    only the child's descriptor copies: the records and the flock's open file description are the
    acquirer's, so it neither removes a record nor unlocks the anchor. `acquirer_pid` is the
    identity acquire_operation captured at its entry (fix round 8): the active record's owner, the
    capability, and the unwind's fork guard are all bound to it, and its /proc start time is read
    once, so the record and the capability name the same process.

    Fix round 9 (claude MED): the flock and every mutating step taken under it run only in the
    acquiring process (_gated_step on `acquirer_pid`): the flock itself, the staging-leftover
    removals, the recovery deletes, and both publications (whose own syscalls are gated inside
    _publish_staged, before the lease publication's first), so a forked continuation is refused
    before it locks, removes, or publishes anything. Fix round 9 (codex MED 2): the unwind never
    calls flock(LOCK_UN); it gives the lock up only by closing the anchor descriptor, which frees
    nothing a forked copy still references."""
    res = _opf_store.resolve_store(store_root)
    if res.status != _opf_store.RESOLVED:
        raise OpLockError("no RESOLVED machine store at {} ({}: {}); the operation lock requires "
                          "a resolved store".format(store_root, res.status, res.detail))
    store_root_abs = str(res.store_root)

    # D2: ONE owner holds every descriptor this acquisition opens, from the moment each open returns
    # until the capability takes them all (transfer_all) or the unwind closes them (close_all).
    owner = _FdOwner()
    anchor_fd = None
    # A record counts as created once its identity is stored (the publication's own return); the
    # lease is ATTEMPTED from just before its publication call, after which a failure is treated as
    # potentially published (fix round 4, H1).
    lease_attempted = False
    active_ident = lease_ident = None
    active_payload = lease_payload = None
    ctl_fd = machine_fd = None
    cap = None
    try:
        # Fix round 4: no nested try statement follows the first adoption in this body. A try
        # statement's own line lies outside every enclosing exception range, so an interruption
        # landing on it would skip the unwind below; each fallible step is therefore a small
        # fail-closed helper whose own frame holds its try.
        store_fd = owner.adopt(_open_path_dir_nofollow(store_root_abs, "store root"))
        machine_fd = owner.adopt(_open_machine_dir(store_fd, res.machine_rel, store_root_abs))
        machine_st = _fstat_or_refuse(machine_fd, "machine store {}/{}".format(
            store_root_abs, res.machine_rel))
        machine_path = os.path.join(store_root_abs, res.machine_rel)

        if _classify_git_entry(store_fd, store_root_abs) == "absent":
            control_root_fd = owner.adopt(_dup_store_root(store_fd))
            control_root_desc = store_root_abs
        else:
            control_root_desc = _git_common_dir(store_root_abs)
            control_root_fd = owner.adopt(_open_path_dir_nofollow(control_root_desc,
                                                                  "common git dir"))
        ctl_fd = owner.adopt(_open_control_dir(control_root_fd, control_root_desc))
        # Ownership of the control-root descriptor is cleared BEFORE its close, so a failing close
        # (Linux has released the number even then) can never lead the unwind to close whatever
        # unrelated file has since been given the same number (codex HIGH 4).
        owner.close(control_root_fd, "cannot close the control-root descriptor ({})")

        anchor_fd = owner.adopt(_open_anchor(ctl_fd))
        # Fix round 9: the flock too is taken only in the acquiring process, so a forked
        # continuation takes no lock, on the shared open file description or on its own.
        _gated_step(acquirer_pid, "take the anchor lock", _flock_exclusive, anchor_fd)
        anchor_ident = _post_lock_anchor_check(ctl_fd, anchor_fd)

        # D1: under the held flock no cooperating publisher is mid-publication, so a staging leftover
        # is garbage from a process killed mid-publication: removed, never a finding. This runs
        # BEFORE stale classification, so a record killed between its os.link and its staging
        # unlink is back to a single link when the recovery gate reads it. Fix round 9: only in
        # the acquiring process (a forked continuation is refused here, having removed nothing).
        _gated_step(acquirer_pid, "remove the active record's staging leftovers",
                    _remove_staging_garbage, ctl_fd, ACTIVE_NAME, "active record")
        _gated_step(acquirer_pid, "remove the lease's staging leftovers",
                    _remove_staging_garbage, machine_fd, _opf_check.LEASE_NAME, "lease")

        # Stale-state classification under the held flock: a record under a FREE anchor is a
        # crash artefact; only explicit recovery clears it, and only for a CONFIRMED-DEAD holder.
        stale_active = _classify_stale(ctl_fd, ACTIVE_NAME, "active record")
        stale_lease = _classify_stale(machine_fd, _opf_check.LEASE_NAME, "lease")
        if stale_active or stale_lease:
            if not recover:
                stale = ", ".join(n for n, s in ((ACTIVE_NAME, stale_active),
                                                 (_opf_check.LEASE_NAME, stale_lease)) if s)
                raise OpLockError(
                    "stale operation record(s) under a free anchor ({}); refusing without EXPLICIT "
                    "recovery (recover=True), which itself proceeds only when the recorded holder "
                    "is confirmed dead".format(stale))
            # recover=True: CONFIRM the recorded holder is dead before any delete (never seize a
            # possibly-live, cross-host, or malformed holder, nor a lone owner-less lease). The gate
            # returns the identity and bytes it read for each record so the delete below removes ONLY
            # those same objects with those same bytes (DEF-1: a record swapped to a live holder's
            # after the liveness read is preserved, never seized). The gate also refuses an active
            # record paired with ANOTHER checkout's still-existing machine store (a sibling git
            # worktree), so the shared active record is never deleted out from under a lease this
            # recovery cannot see.
            rec_active_ident, rec_active_bytes, rec_lease_ident, rec_lease_bytes = \
                _require_holder_confirmed_dead(ctl_fd, machine_fd, stale_active, stale_lease,
                                               machine_st, machine_path)
            # Delete order: the verified LEASE first (its unlink is fsynced on the machine-store
            # directory inside _recover_stale), and ONLY THEN the active record. A crash or a failed
            # delete between the two leaves a LONE ACTIVE RECORD, which still carries the owner
            # identity the liveness gate reads, so a later recover=True re-confirms the dead holder
            # and clears it. The reverse order stranded a lone owner-less lease that no recovery
            # could ever confirm dead. Fix round 9: each delete runs only in the acquiring process.
            if stale_lease:
                _gated_step(acquirer_pid, "remove the stale lease", _recover_stale, machine_fd,
                            _opf_check.LEASE_NAME, rec_lease_ident, rec_lease_bytes, "lease")
            if stale_active:
                _gated_step(acquirer_pid, "remove the stale active record", _recover_stale, ctl_fd,
                            ACTIVE_NAME, rec_active_ident, rec_active_bytes, "active record")

        op_id = str(uuid.uuid4())
        _validate_field("op_id", op_id)
        acquired_at = _utc_now()
        # The active record persists the FULL owner identity a later recovery needs to decide
        # liveness WITHOUT inferring it: exactly the schema _journal.owner_confirmed_dead validates
        # (pid, uid, session, utc, and the canonical /proc start time), plus the nodename that lets
        # recovery refuse a cross-host holder it can never probe by pid.
        acquirer_start = _journal._pid_start(acquirer_pid)
        owner_identity = dict(pid=acquirer_pid, uid=os.getuid(), nodename=nodename,
                              session=op_id, utc=acquired_at)
        owner_identity["pid-start"] = acquirer_start
        # The active record also names the machine store its paired lease lives in, so a recovery
        # from a sibling git worktree (same control root, different machine store) refuses.
        active_payload = _control_payload(
            dict(schema=_ACTIVE_SCHEMA, op_id=op_id, holder=holder, operation=operation,
                 acquired_at=acquired_at, owner=owner_identity,
                 machine_store=_machine_store_table(machine_path, machine_st)),
            ACTIVE_TOP_KEYS, "active record")
        lease_payload = _control_payload(
            dict(schema=_SCHEMA, holder=holder, operation=operation, acquired_at=acquired_at),
            _opf_check.LEASE_TOP_KEYS, "lease")

        # Each record's descriptor is adopted straight into this acquisition's owner (owner=owner),
        # so no transfer step lies between a publication and the unwind's ownership of it. Fix
        # round 9: both publications are bound to the acquirer's entry pid (publisher_pid), so the
        # lease's publication refuses a forked continuation before its first syscall.
        active_fd, active_ident = _create_control_file(ctl_fd, ACTIVE_NAME, active_payload,
                                                       "active record", owner=owner,
                                                       publisher_pid=acquirer_pid)
        lease_attempted = True
        lease_fd, lease_ident = _create_control_file(machine_fd, _opf_check.LEASE_NAME,
                                                     lease_payload, "lease", owner=owner,
                                                     publisher_pid=acquirer_pid)

        ctl_st = os.fstat(ctl_fd)
        # Close the store-root descriptor INSIDE the protected body, before ownership of the
        # retained descriptors transfers to the capability: a failing close (EIO) then runs the
        # full unwind (records removed, descriptors closed, the anchor's giving up the lock)
        # instead of escaping after the capability was built and leaking the still-flocked
        # anchor. Ownership is cleared FIRST so the unwind never re-closes a number the kernel
        # has already released.
        owner.close(store_fd, "cannot close the store-root descriptor ({})")
        cap = OpCapability(
            op_id=op_id, holder=holder, operation=operation,
            store_root=store_root_abs, machine_rel=res.machine_rel,
            ctl_fd=ctl_fd, machine_fd=machine_fd, anchor_fd=anchor_fd,
            active_fd=active_fd, lease_fd=lease_fd,
            anchor_ident=anchor_ident,
            ctl_ident=(ctl_st.st_dev, ctl_st.st_ino),
            machine_ident=(machine_st.st_dev, machine_st.st_ino),
            active_ident=active_ident, lease_ident=lease_ident,
            active_bytes=active_payload, lease_bytes=lease_payload,
            acquirer_pid=acquirer_pid, acquirer_pid_start=acquirer_start)
        owner.transfer_all()               # the capability now owns every retained descriptor
        return cap
    except BaseException as exc:
        # A capability already built and handed every descriptor (transfer_all is one assignment,
        # so the owner is then empty) but interrupted before it was returned is unwound like any
        # other failure: its descriptors come back to the owner in one step, closed below after the
        # records are removed; the caller never receives it, and it is
        # marked released so it can never close a number twice.
        if cap is not None and not owner.holds_any():
            cap._released = True
            owner.adopt_all((cap._machine_fd, cap._ctl_fd, cap._anchor_fd, cap._active_fd,
                             cap._lease_fd))
        if os.getpid() != acquirer_pid:
            # Fix round 7: a forked child shares the acquirer's open file descriptions (the flock
            # belongs to one) and its records; it closes only its own descriptor copies, which
            # cannot free the lock the acquirer still references (fix round 9: release by close).
            problems, unconfirmed, interrupt = owner.close_all()
            if unconfirmed:
                closed = "{} ({!r})".format(_unconfirmed_closes(len(unconfirmed),
                                                                "inherited descriptor"), interrupt)
            elif problems:
                closed = "closing its descriptor copies reported failures ({})".format(
                    "; ".join(problems))
            else:
                closed = "it closed only its own descriptor copies"
            exc.add_note("opf-oplock: this acquisition unwind ran in a forked child (pid {}) of "
                         "the acquiring process (pid {}), so this unwind removed no record and did "
                         "not unlock the anchor; {}".format(os.getpid(), acquirer_pid, closed))
            if interrupt is not None and isinstance(exc, Exception):
                raise interrupt
            raise
        # Unwind in reverse leg order; every unwind failure is collected, never swallowed, and
        # every step (each leg, each close) is its own guarded step, so neither a
        # failure nor an interruption in one step skips the rest. D3: the active record is removed
        # ONLY after the lease is: if the lease cannot be removed, the owner-bearing active record
        # is KEPT, so a later recover=True can confirm the holder dead and clear both, rather than a
        # lone owner-less lease that no recovery can ever clear. A leg's raw OSError is collected
        # like an OpLockError (DEF-3, defence in depth over _verified_unlink's own normalization).
        # H1 (fix round 4): a lease publication that FAILED or was INTERRUPTED is treated as
        # potentially published, because its own cleanup can fail or be interrupted after the link.
        # The active record is then deleted only once the lease name is OBSERVED absent (under the
        # held flock, where stale classification found no lease before this publication); a lease
        # still present, or a presence check that cannot answer, KEEPS the owner-bearing active
        # record beside it, never leaving the lone owner-less lease no recovery can clear.
        unwind = []
        interrupt = None
        lease_removed = True
        lease_why = "the lease was not removed"
        if lease_ident is not None:
            lease_outcome = {}
            try:
                _verified_unlink(machine_fd, _opf_check.LEASE_NAME, lease_ident, lease_payload,
                                 "lease (unwind)", lease_outcome)
            except _UnlinkNotDurable as uexc:
                # Fix round 8: the lease name WAS removed but not durably; the conservative order
                # still keeps the active record, and the message says which step failed.
                unwind.append(str(uexc))
                lease_removed = False
                lease_why = "the lease's removal was not made durable"
            except (OpLockError, OSError) as uexc:
                unwind.append(str(uexc))
                lease_removed = False
            except BaseException as uexc:
                # Fix round 10 (codex LOW): the interruption is held (re-raised after the full
                # unwind) and the lease leg's outcome is taken from what _verified_unlink observed:
                # a leg that completed is a durable removal; a lease observed gone after an
                # interrupted fsync has its durability UNCONFIRMED (the active record is then kept,
                # the conservative order); an unobservable one is UNCONFIRMED.
                interrupt = uexc
                lease_removed = bool(lease_outcome.get("done"))
                if not lease_removed and lease_outcome.get("unlinked"):
                    lease_why = ("the lease was unlinked, but its directory fsync was interrupted, "
                                 "so its removal's durability is UNCONFIRMED")
                elif not lease_removed and lease_outcome.get("interrupted") \
                        and lease_outcome.get("unlinked") is None:
                    lease_why = ("whether the lease was removed is UNCONFIRMED (its removal was "
                                 "interrupted between its unlink and the end of its directory "
                                 "fsync, and its name could not be observed)")
        elif lease_attempted:
            try:
                if _lstat_at(machine_fd, _opf_check.LEASE_NAME, "lease (unwind)") is not None:
                    unwind.append("lease (unwind): the failed publication left the lease in place, "
                                  "so it may be this acquisition's")
                    lease_removed = False
            except OpLockError as uexc:
                unwind.append(str(uexc))
                lease_removed = False
            except BaseException as uexc:
                interrupt = uexc
                lease_removed = False
        if active_ident is not None:
            if not lease_removed:
                unwind.append("active record (unwind) KEPT: {}, so the owner-bearing active "
                              "record is retained for a later recover=True to confirm the holder "
                              "dead and clear both".format(lease_why))
            else:
                try:
                    _verified_unlink(ctl_fd, ACTIVE_NAME, active_ident, active_payload,
                                     "active record (unwind)")
                except (OpLockError, OSError) as uexc:
                    unwind.append(str(uexc))
                except BaseException as uexc:
                    if interrupt is None:
                        interrupt = uexc
        # Fix round 9 (codex MED 2): NO flock(LOCK_UN). The flock belongs to the anchor's open file
        # description and is freed when its LAST descriptor closes, so the lock is given up by
        # closing the anchor descriptor (first, so its outcome is known), and a forked copy that
        # still references the description keeps it held: never a second holder.
        if anchor_fd is not None and anchor_fd in owner:
            anchor_problems, anchor_unconfirmed, anchor_interrupt = owner.close_guarded(anchor_fd)
            unwind.extend("{}; the lock's release is not confirmed".format(p)
                          for p in anchor_problems)
            if anchor_unconfirmed:
                unwind.append("the mutex anchor descriptor's close was interrupted, so the lock's "
                              "release is UNCONFIRMED ({})".format(_NEVER_RETRIED))
            if interrupt is None:
                interrupt = anchor_interrupt
        close_problems, close_unconfirmed, close_interrupt = owner.close_all()
        unwind.extend(close_problems)
        if close_unconfirmed:
            unwind.append(_unconfirmed_closes(len(close_unconfirmed), "control descriptor"))
        if interrupt is None:
            interrupt = close_interrupt
        if interrupt is not None and isinstance(exc, Exception):
            # An interruption raised by the unwind itself is re-raised after the full cleanup.
            detail = "; additionally the unwind failed: {}".format("; ".join(unwind)) if unwind else ""
            interrupt.add_note("opf-oplock: raised while unwinding a failed acquisition "
                               "({}){}".format(exc, detail))
            raise interrupt
        if unwind and isinstance(exc, Exception):
            raise OpLockError("{}; additionally the unwind failed: {}".format(
                exc, "; ".join(unwind))) from exc
        if unwind:
            exc.add_note("opf-oplock: additionally the unwind failed: {}".format("; ".join(unwind)))
        raise


def release_operation(cap):
    """Identity-bound, verified release of an OpCapability.

    Refuses FIRST any caller that is not the recorded acquirer (pid plus /proc start time); a
    refused caller touches nothing. From then on the whole release runs inside an enclosing cleanup
    (_ReleaseScope, a context manager armed before any state changes) that OWNS the capability's
    descriptors (the capability is marked released and its descriptor fields cleared before any leg
    runs, and again by the cleanup itself, so no later call can close a number twice, and an
    interruption landing anywhere in the release's own bookkeeping still ends released and closed).
    It re-checks the retained anchor and control-directory
    identities, then removes the LEASE first (a verified, fsynced unlink) and ONLY THEN the active
    record: if the lease cannot be removed, the owner-bearing active record is KEPT (D3), so a later
    recover=True can confirm the holder dead and clear both. Each unlink is verified byte for byte
    (a mismatch refuses and PRESERVES the file). Every retained descriptor is then closed by that
    enclosing cleanup, the anchor's first, each as its own guarded step that runs whatever happened
    before it; the lock is given up by that close alone, never by flock(LOCK_UN) (fix round 9:
    release by close), so a forked copy of the anchor descriptor keeps it held until it too is
    closed. The
    collected failures raise as one OpLockError, and an interruption (a BaseException that is not
    an Exception) raised anywhere in the release propagates as itself after that cleanup. The
    anchor itself is NEVER unlinked. Everything after the acquirer check runs with the
    Python-handled signals deferred (fix round 5), so a signal arriving meanwhile is delivered
    only after the release has ended released and closed.
    """
    if not isinstance(cap, OpCapability):
        raise OpLockError("release requires an OpCapability")
    if cap._released:
        raise OpLockError("capability already released")
    pid = os.getpid()
    start = _journal._pid_start(pid)
    recorded = cap._acquirer_pid_start
    if recorded != "" and not _journal._is_canonical_pid_start(recorded):
        raise OpLockError("recorded acquirer start time is malformed; refusing release "
                          "(a malformed control input is a failure, never trusted)")
    if pid != cap._acquirer_pid or start != recorded:
        raise OpLockError("release refused: caller (pid {}) is not the recorded acquirer "
                          "(pid {})".format(pid, cap._acquirer_pid))

    # D2 plus fix round 4 (H2): the whole release lifecycle runs inside an ENCLOSING cleanup (a
    # context manager armed before any state changes), whose exit takes sole ownership of the
    # retained descriptors, marks the capability released, and closes every descriptor (the
    # anchor's close giving up the lock, fix round 9), whatever interrupted the body. An
    # interruption before the scope is entered changes nothing (the capability stays unreleased, so
    # a retry still works); one anywhere inside it still ends released and closed. The body is ONE
    # call on the with statement's own line, deliberately: a line boundary between the body's end
    # and the scope's exit lies outside every exception range, so an interruption landing there
    # would skip the exit. Every boundary of the body belongs to _release_legs's own frame instead.
    # Fix round 5: the scope, its exit (the closes) included, runs with the Python-handled signals
    # DEFERRED, so a signal-raised exception is delivered only after the release has ended released
    # and closed; the same one-call-per-with shape holds for the deferral.
    with _SignalDeferral(): _release_scoped(cap)


def _release_scoped(cap, sink=None):
    """The deferred part of release_operation (fix round 5): the enclosing _ReleaseScope around the
    release legs, as one call on the with statement's own line. With `sink` (a list), the scope
    appends itself to it, so a caller can read the per-step outcome it recorded (fix round 7)."""
    with _ReleaseScope(cap, sink) as scope: _release_legs(cap, scope)


def _release_legs(cap, scope):
    """The body of release_operation (fix round 4, H2), run inside its enclosing _ReleaseScope:
    take ownership, re-check the retained identities, then the lease leg and (only once the lease
    is gone) the active leg, each failure collected into scope.errors and each removal recorded in
    scope.removed once its verified unlink returned. In a forked child of the acquirer (fix round
    7) no leg runs: the records are the acquirer's. Fix round 8: taking ownership is the
    synchronized claim, and a release that loses it (another thread's release of the same
    capability won) refuses here, holding no descriptor and touching nothing; a removal whose
    directory fsync failed is recorded in scope.removed AND scope.undurable, and the active leg then
    still waits (the conservative order: the lease's removal is not yet durable). Fix round 9: a
    claim taken in a forked child (scope.in_child) runs no leg either, and the scope's exit then
    refuses, stating what this release did."""
    if not scope.take_ownership():
        raise OpLockError(scope.lost_claim())
    if scope.in_child or os.getpid() != scope.pid:
        return
    machine_fd, ctl_fd, anchor_fd = scope.retained[0], scope.retained[1], scope.retained[2]
    errors = scope.errors
    # Retained-identity re-checks. Collected rather than early-raised: the legs below still run,
    # so a genuine release cleans what it verifiably owns and the anomaly is surfaced with it.
    try:
        st = os.fstat(anchor_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 \
                or (st.st_dev, st.st_ino) != cap._anchor_ident:
            raise OpLockError("mutex anchor identity or link count changed while held "
                              "(nlink {}); the flock no longer excludes anyone".format(
                                  st.st_nlink))
    except (OSError, OpLockError) as exc:
        errors.append("anchor: {}".format(exc))
    try:
        st = os.fstat(ctl_fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_nlink == 0 \
                or (st.st_dev, st.st_ino) != cap._ctl_ident:
            raise OpLockError("control directory identity changed while held")
    except (OSError, OpLockError) as exc:
        errors.append("control dir: {}".format(exc))
    # D3: the lease first, and the active record ONLY once the lease is gone. A lease the
    # release could not remove keeps the owner-bearing active record beside it (recoverable by a
    # later recover=True once this holder is dead); the reverse would strand a lone owner-less
    # lease that no recovery can confirm dead. A leg's raw OSError is collected like an
    # OpLockError (DEF-3, defence in depth over _verified_unlink's own normalization).
    # Fix round 10 (codex LOW): an interruption from a leg's unlink through its fsync is recorded
    # from the leg's observed outcome (_record_interrupted_removal) before it propagates unchanged,
    # so a completed unlink is never reported as a record that was not removed.
    outcome = {}
    try:
        _verified_unlink(machine_fd, _opf_check.LEASE_NAME, cap._lease_ident,
                         cap._lease_bytes, "lease", outcome)
        scope.removed.append("lease")
    except _UnlinkNotDurable as exc:
        scope.removed.append("lease")
        scope.undurable.append("lease")
        errors.append("lease leg: {}".format(exc))
    except (OpLockError, OSError) as exc:
        errors.append("lease leg: {}".format(exc))
    except BaseException:
        _record_interrupted_removal(scope, "lease", outcome)
        raise
    if "lease" in scope.removed and "lease" not in scope.undurable:
        outcome = {}
        try:
            _verified_unlink(ctl_fd, ACTIVE_NAME, cap._active_ident, cap._active_bytes,
                             "active record", outcome)
            scope.removed.append("active record")
        except _UnlinkNotDurable as exc:
            scope.removed.append("active record")
            scope.undurable.append("active record")
            errors.append("active leg: {}".format(exc))
        except (OpLockError, OSError) as exc:
            errors.append("active leg: {}".format(exc))
        except BaseException:
            _record_interrupted_removal(scope, "active record", outcome)
            raise
    else:
        errors.append("active leg: KEPT, because the lease {} (the owner-bearing active record "
                      "is retained so a later recover=True can confirm the holder dead and clear "
                      "both)".format("removal was not made durable" if "lease" in scope.removed
                                     else "was not removed"))


def _record_interrupted_removal(scope, name, outcome):
    """Record in `scope` what an interrupted release leg's verified unlink did (fix round 10, codex
    LOW), from the outcome _verified_unlink observed: a leg that completed (the interruption landed
    after it) is a removal; a name observed gone after an interruption between the unlink and the
    end of its fsync is a removal whose durability is UNCONFIRMED; a name whose state could not be
    observed is a removal that is itself UNCONFIRMED; a name observed still bound is no removal."""
    if name in scope.removed:
        return
    if outcome.get("done"):
        scope.removed.append(name)
    elif outcome.get("unlinked"):
        scope.removed.append(name)
        scope.durability_unconfirmed.append(name)
    elif outcome.get("interrupted") and outcome.get("unlinked") is None:
        scope.removal_unconfirmed.append(name)


_LOST_CLAIM = ("capability already released: a concurrent release of the same capability claimed "
               "it first, so this release holds none of its descriptors and touched nothing (no "
               "record, no unlock, no close)")
_CLAIM_TIMEOUT = ("release refused: another release of the same capability held its claim step for "
                  "longer than {} seconds, so this release holds none of its descriptors and "
                  "touched nothing (no record, no unlock, no close)").format(_CLAIM_WAIT_SECONDS)


class _ReleaseScope:
    """The enclosing cleanup of one release (fix round 4, H2), used as a context manager.

    Construction changes nothing and captures nothing (so an interruption before the scope is
    entered leaves the capability unreleased and a retry still works). take_ownership, the body's
    first step, is ONE synchronized claim (fix round 8, codex HIGH 2): under the capability's lock
    (taken without ever blocking, fix round 9) it checks that no other release has claimed the
    capability, captures the retained descriptors, marks the capability released, and clears its
    descriptor fields, so two releases of the same capability can never both retain its descriptor
    numbers; the loser holds nothing and refuses. It is idempotent for the scope that won, and the
    exit runs it again, so an interruption landing before the body's own call still ends released
    and a later call can never close a number twice. The exit then (only when this scope holds the
    claim) adopts every retained descriptor into one owner in a single step, closes the anchor
    descriptor first (which gives up this process's hold on the lock; the anchor is never
    unlinked), then closes every other descriptor, each its own guarded step; an interruption in
    one step is held until every step has run. Outcome: an interruption (from the body or the
    cleanup) propagates as itself after the cleanup, an in-flight body exception propagates with
    the collected failures as notes, and a clean body with collected failures raises one
    OpLockError.

    The whole scope, this exit included, runs inside release_operation's _SignalDeferral (fix round
    5), so a signal-raised interruption cannot land in it. NOT covered (disclosed in the module
    contract): a non-signal injection (sys.settrace, an externally set asynchronous exception) or a
    MemoryError landing inside this exit itself, outside its individually guarded close steps (for
    example at its entry, before the first guarded step), which no pure-Python handler can close.

    Fix round 7: the scope acts only in the process that built the capability (`pid`, its
    _acquirer_pid, no /proc read). In a FORKED CHILD, which shares the acquirer's open file
    descriptions (a flock belongs to one), the legs remove no record and the exit closes only the
    child's descriptor copies. Fix round 9 (codex MED 2): the exit NEVER calls flock(LOCK_UN), in
    the acquirer or in a child: the lock is given up only by closing the anchor descriptor, which
    frees it once no other descriptor references its open file description, so a child's closes
    can never free a lock its acquirer still references. The scope also RECORDS each step as it
    completes, for a truthful outcome note: `removed` (the records whose unlink ran and removed the
    name), `undurable` (those among them whose directory fsync then failed, fix round 8),
    `anchor_close` (None until the anchor descriptor's close is confirmed or fails: "" once it
    closed, else the failure), `closed` (None until every close step has run, then the list of
    close failures), and `unconfirmed` (the descriptors whose close an interruption cut short
    after ownership cleared, fix round 9: never reported closed, never closed again); `claimed` is
    True once this scope holds the claim, `in_child` once it claimed in a forked child, and
    `refused` (None, "lost", or "timeout") once a claim attempt has concluded that it cannot win.
    Fix round 10 (codex LOW): `durability_unconfirmed` (removals whose directory fsync an
    interruption cut short) and `removal_unconfirmed` (records whose interrupted unlink could not
    be observed), both recorded by _record_interrupted_removal."""
    __slots__ = ("_cap", "retained", "claimed", "in_child", "refused", "errors", "pid", "removed",
                 "undurable", "durability_unconfirmed", "removal_unconfirmed", "anchor_close",
                 "closed", "unconfirmed")

    def __init__(self, cap, sink=None):
        self._cap = cap
        self.retained = ()
        self.claimed = False
        self.in_child = False
        self.refused = None
        self.errors = []
        self.pid = cap._acquirer_pid
        self.removed = []
        self.undurable = []
        self.durability_unconfirmed = []
        self.removal_unconfirmed = []
        self.anchor_close = None
        self.closed = None
        self.unconfirmed = []
        if sink is not None:
            sink.append(self)

    def take_ownership(self):
        """The synchronized claim (fix round 8), fork-aware and never blocking (fix round 9, codex
        MED 1): True when this scope holds the capability (now or already), False when another
        release claimed it first or held the claim step past _CLAIM_WAIT_SECONDS. Idempotent for
        the winner, and a concluded refusal is not retried.

        The capability's lock is NEVER acquired blocking. Each attempt first re-checks os.getpid()
        against the acquirer's pid, then tries the lock without blocking, and re-checks the pid once
        the lock is held; between attempts it sleeps _CLAIM_POLL_SECONDS, for at most
        _CLAIM_WAIT_SECONDS in all. A forked child (a lock copy that another thread held at the
        fork stays locked there forever, since that thread does not exist in the child) therefore
        never waits on the lock: it claims its own inherited copies without the lock (no other
        thread exists in it, so no concurrent releaser), marking the claim in_child, only so the
        exit closes those copies; its legs run nothing and its release refuses."""
        if self.claimed:
            return True
        if self.refused is not None:
            return False
        lock = self._cap._claim
        deadline = time.monotonic() + _CLAIM_WAIT_SECONDS
        while True:
            if os.getpid() != self.pid:
                return self._claim_locked(True)
            if lock.acquire(False):
                try:
                    return self._claim_locked(os.getpid() != self.pid)
                finally:
                    lock.release()
            if time.monotonic() >= deadline:
                self.refused = "timeout"
                return False
            time.sleep(_CLAIM_POLL_SECONDS)

    def _claim_locked(self, in_child):
        """take_ownership's step, with the capability's lock held (or, `in_child`, in a forked child
        where no other thread exists): claim the capability for this scope unless another release
        holds it, then capture the retained descriptors ONCE (before the fields are cleared, so an
        interruption between the two steps never loses them) and transfer their ownership here."""
        cap = self._cap
        if cap._claimant is None and not cap._released:
            cap._claimant = self
        if cap._claimant is not self:
            self.refused = "lost"
            return False
        if not self.retained:
            # Adopted in this order, so closed in reverse: lease, active, (the anchor is closed
            # first, on its own, by the exit), ctl, machine.
            self.retained = (cap._machine_fd, cap._ctl_fd, cap._anchor_fd, cap._active_fd,
                             cap._lease_fd)
        cap._released = True
        cap._lease_fd = cap._active_fd = cap._anchor_fd = cap._ctl_fd = cap._machine_fd = None
        self.in_child = self.in_child or in_child
        self.claimed = True
        return True

    def lost_claim(self):
        """The refusal of a release that did not win the claim (fix round 8; the bounded wait's
        own message since fix round 9)."""
        return _CLAIM_TIMEOUT if self.refused == "timeout" else _LOST_CLAIM

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self.take_ownership():
            # Fix round 8: another release holds the capability; this scope retained nothing, so
            # it unlocks nothing and closes nothing.
            if exc is None:
                raise OpLockError(self.lost_claim())
            return False
        owner = _FdOwner()
        owner.adopt_all(self.retained)
        errors = self.errors
        # Fix round 9 (codex MED 2): NO flock(LOCK_UN), here or anywhere. The flock belongs to the
        # anchor's open file description and is freed when its LAST descriptor closes, so the lock
        # is given up by closing the anchor descriptor, first and on its own so its outcome is
        # recorded. In a forked child the same close frees nothing the acquirer still references,
        # so this exit runs the same code in both processes and a child can never free the lock.
        anchor = self.retained[2]
        anchor_problems, anchor_unconfirmed, interrupt = owner.close_guarded(anchor)
        if anchor in owner:
            # Fix round 10 (claude LOW): the close step was interrupted BEFORE its ownership was
            # cleared, so os.close never ran on it and the number is still ours: its one close is
            # made here, attributed, rather than left to the general closes below, whose outcome
            # the anchor clause could not name. The first interruption is kept.
            anchor_problems, anchor_unconfirmed, retried = owner.close_guarded(anchor)
            if interrupt is None:
                interrupt = retried
        if anchor_problems:
            self.anchor_close = anchor_problems[0]
            errors.append("anchor close: {}; the release of the lock is not confirmed".format(
                anchor_problems[0]))
        elif not anchor_unconfirmed and anchor not in owner:
            self.anchor_close = ""
        close_problems, close_unconfirmed, close_interrupt = owner.close_all()
        self.closed = anchor_problems + close_problems
        self.unconfirmed = anchor_unconfirmed + close_unconfirmed
        errors.extend("close: {}".format(p) for p in close_problems)
        if self.unconfirmed:
            errors.append(_unconfirmed_closes(len(self.unconfirmed)))
        if interrupt is None:
            interrupt = close_interrupt
        if self.in_child or os.getpid() != self.pid:
            # Fix round 7, stated from what this release recorded (fix round 9): a forked child
            # removes no record in its legs and frees no lock with its closes.
            errors.append("forked child (pid {}) of the acquirer (pid {}): this release removed "
                          "{}, and it touched only this process's own descriptor copies, whose "
                          "closes never release the acquirer's lock".format(
                              os.getpid(), self.pid, " and ".join(self.removed) or "no record"))
        if interrupt is not None and (exc is None or isinstance(exc, Exception)):
            if exc is not None:
                interrupt.add_note("opf-oplock: raised while cleaning up after: {}".format(exc))
            if errors:
                interrupt.add_note("opf-oplock: release completed with failures: {}".format(
                    "; ".join(errors)))
            raise interrupt
        if exc is not None:
            if errors:
                exc.add_note("opf-oplock: release completed with failures: {}".format(
                    "; ".join(errors)))
            return False
        if errors:
            # Fix round 7: state which records were removed (observed), rather than a fixed claim
            # that mismatched files were preserved, which was false when every record was removed.
            # Fix round 8: a removal whose directory fsync failed is named as not durable.
            raise OpLockError("release completed with failures (records removed: {}): {}".format(
                ", ".join(n + (" (removal not durable)" if n in self.undurable else "")
                          for n in self.removed) or "none", "; ".join(errors)))
        return False


# --- self-test --------------------------------------------------------------------------------------


def _st_git_env(home):
    """A pinned, hermetic environment for FIXTURE git commands: ambient GIT_* dropped, then HOME,
    XDG_CONFIG_HOME, and the global/system config files bound into the fixture so no ambient user
    or system git config can affect a fixture command (LOW-5). The production _git_common_dir keeps
    HOME/XDG_CONFIG_HOME (scrubbing only GIT_*), so self_test() also pins those in os.environ."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, "xdg")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.path.join(home, "gitconfig-global")
    env["GIT_CONFIG_SYSTEM"] = os.path.join(home, "gitconfig-system")
    return env


def _st_git(args, cwd, env):
    proc = subprocess.run(["git"] + args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("fixture git {} failed: {}".format(
            args, proc.stdout.decode("utf-8", "replace")))


def _st_store_tree(root):
    """A minimal adopted-store tree that resolve_store resolves (RESOLVED at the default
    location)."""
    md = os.path.join(root, ".working", "toml")
    os.makedirs(md)
    manifest = ('[opf]\nstandard = "opf"\nspec_version = "1.1.0"\nlayout = "inline"\n'
                'posture = "required"\nimport_status = "none"\n\n'
                '[modules]\nconcurrent_operation = true\n')
    with open(os.path.join(md, "manifest.toml"), "w", encoding="utf-8") as fh:
        fh.write(manifest)


def _st_git_store(parent, name, env):
    """A REAL git repository carrying a committed adopted-store tree."""
    root = os.path.join(parent, name)
    os.mkdir(root)
    _st_git(["init", "-q"], root, env)
    _st_store_tree(root)
    _st_git(["add", "-A"], root, env)
    _st_git(["-c", "user.name=opf-selftest", "-c", "user.email=selftest@example.invalid",
             "commit", "-q", "-m", "fixture"], root, env)
    return root


def _st_expect_refusal(fn, *args, needle=None, **kwargs):
    """Run fn expecting an OpLockError; assert the message carries `needle` when given."""
    try:
        fn(*args, **kwargs)
    except OpLockError as exc:
        if needle is not None and needle not in str(exc):
            raise AssertionError("refusal message {!r} lacks {!r}".format(str(exc), needle))
        return str(exc)
    raise AssertionError("{} did not refuse".format(getattr(fn, "__name__", fn)))


def _st_ctl_dir(root):
    return os.path.join(root, ".git", CONTROL_DIRNAME)


def _st_lease_path(root):
    return os.path.join(root, ".working", "toml", _opf_check.LEASE_NAME)


def _st_reaped_child():
    """A pid that is CONFIRMED DEAD on this host: a forked child reports its /proc starttime and
    exits, and the parent reaps it, so os.kill(pid, 0) later raises ProcessLookupError (or, on the
    astronomically-unlikely reuse, a differing start time confirms death). Returns (pid,
    pid_start)."""
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:                          # the child: report its own start time, then die
        os.close(rfd)
        try:
            os.write(wfd, _journal._pid_start(os.getpid()).encode("utf-8"))
        except OSError:
            pass
        os._exit(0)
    os.close(wfd)
    data = b""
    while True:
        chunk = os.read(rfd, 4096)
        if not chunk:
            break
        data += chunk
    os.close(rfd)
    os.waitpid(pid, 0)                    # reap: the pid is now dead
    return pid, data.decode("utf-8")


def _st_write_active_owned(root, pid, pid_start, nodename, op_id, holder=None):
    """Write a stale active.toml carrying an [owner] identity directly (a crash artefact the
    recovery gate reads). Returns the holder string it wrote, so a paired lease can match it."""
    if holder is None:
        holder = "opf:{}:{}".format(nodename, pid)
    lines = (
        'schema = {}'.format(_ACTIVE_SCHEMA),
        'op_id = "{}"'.format(op_id),
        'holder = "{}"'.format(holder),
        'operation = "recovered-op"',
        'acquired_at = "2026-01-01T00:00:00Z"',
        '',
        '[owner]',
        'pid = {}'.format(pid),
        'uid = {}'.format(os.getuid()),
        'nodename = "{}"'.format(nodename),
        'session = "{}"'.format(op_id),
        'utc = "2026-01-01T00:00:00Z"',
        'pid-start = "{}"'.format(pid_start),
        '',
    ) + _st_machine_store_lines(root)
    with open(os.path.join(_st_ctl_dir(root), ACTIVE_NAME), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return holder


def _st_machine_store_lines(root):
    """The [machine_store] table lines pairing a hand-written active record with `root`'s machine
    store (the default .working/toml location the fixtures resolve to)."""
    machine = os.path.join(root, ".working", "toml")
    st = os.stat(machine)
    return ('[machine_store]',
            'path = "{}"'.format(machine),
            'dev = "{}"'.format(st.st_dev),
            'ino = "{}"'.format(st.st_ino),
            '')


def _st_write_lease_owned(root, holder):
    """Write a stale lease.toml naming `holder` (a crash artefact paired with the active record)."""
    with open(_st_lease_path(root), "w", encoding="utf-8") as fh:
        fh.write('schema = 1\nholder = "{}"\noperation = "recovered-op"\n'
                 'acquired_at = "2026-01-01T00:00:00Z"\n'.format(holder))


def _t_c1_common_dir_authority(d, env):
    """T-c1: the control root is the AUTHORITATIVE common git dir (git itself, scrubbed env),
    exercised over a real linked worktree; a bogus gitdir pointer refuses (the retired
    name-heuristic would have trusted it); a poisoned ambient GIT_DIR is inert."""
    main = _st_git_store(d, "main", env)
    wt = os.path.join(d, "wt")
    _st_git(["worktree", "add", "--detach", "-q", wt], main, env)
    os.environ["GIT_DIR"] = os.path.join(d, "decoy-git-dir")  # must be scrubbed, so inert
    try:
        cap = acquire_operation(wt, "op-worktree")
    finally:
        del os.environ["GIT_DIR"]
    anchor = os.path.join(_st_ctl_dir(main), ANCHOR_NAME)
    active = os.path.join(_st_ctl_dir(main), ACTIVE_NAME)
    assert os.path.isfile(anchor), "anchor not under the MAIN common git dir"
    assert os.path.isfile(active), "active record not under the MAIN common git dir"
    # Cross-checkout serialization through the one common anchor:
    _st_expect_refusal(acquire_operation, main, "op-main", needle="held")
    release_operation(cap)
    assert os.path.isfile(anchor), "release must NEVER unlink the anchor"
    assert not os.path.exists(active)
    cap2 = acquire_operation(main, "op-main")
    release_operation(cap2)
    # A bogus gitdir pointer file refuses; nothing is created and nothing falls back.
    bogus = os.path.join(d, "bogus")
    os.mkdir(bogus)
    _st_store_tree(bogus)
    with open(os.path.join(bogus, ".git"), "w", encoding="utf-8") as fh:
        fh.write("gitdir: /nonexistent-opf-oplock-decoy\n")
    _st_expect_refusal(acquire_operation, bogus, "op-bogus", needle="rev-parse")
    assert not os.path.exists(os.path.join(bogus, CONTROL_DIRNAME)), \
        "a failed git answer must never fall back to a repo-root control tree"


def _t_c2_anchor_validation(d, env):
    """T-c2: anchor persistence and the pre-lock validation roster (symlink, hard link,
    group/other write bits)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    ctl = _st_ctl_dir(root)
    anchor = os.path.join(ctl, ANCHOR_NAME)
    assert os.path.isfile(anchor), "anchor must persist across release"
    victim = os.path.join(d, "victim")
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write("victim\n")
    os.unlink(anchor)
    os.symlink(victim, anchor)
    _st_expect_refusal(acquire_operation, root, "op", needle="symlink")
    os.unlink(anchor)
    cap = acquire_operation(root, "op")   # clean recreation on genuine absence
    release_operation(cap)
    os.link(anchor, os.path.join(ctl, "hardlink-victim"))
    _st_expect_refusal(acquire_operation, root, "op", needle="link count")
    os.unlink(os.path.join(ctl, "hardlink-victim"))
    os.chmod(anchor, 0o666)
    _st_expect_refusal(acquire_operation, root, "op", needle="writable")
    os.chmod(anchor, 0o644)
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_c2_dirperms(d, env):
    """T-c2-dirperms (LOW-4): a group- or other-writable CONTROL DIRECTORY refuses acquisition (the
    directory ownership/permission vector, distinct from the anchor-file bits in T-c2)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    ctl = _st_ctl_dir(root)
    os.chmod(ctl, 0o775)                  # group-writable control dir
    _st_expect_refusal(acquire_operation, root, "op", needle="writable")
    os.chmod(ctl, 0o757)                  # other-writable control dir
    _st_expect_refusal(acquire_operation, root, "op", needle="writable")
    os.chmod(ctl, 0o755)
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_crit1_anchor_swap(d, env):
    """T-crit1: an out-of-band unlink-and-recreate of the anchor while held cannot yield a second
    holder (the active record refuses it), and the anomaly is surfaced at release while both
    records are still cleaned (identity checks are collected, not legs-blocking)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    ctl = _st_ctl_dir(root)
    anchor = os.path.join(ctl, ANCHOR_NAME)
    os.unlink(anchor)                      # out-of-model interference
    with open(anchor, "wb"):
        pass
    os.chmod(anchor, 0o644)
    # The new inode's flock is free, but the active record under the free anchor refuses:
    _st_expect_refusal(acquire_operation, root, "op2", needle="stale")
    msg = _st_expect_refusal(release_operation, cap, needle="anchor")
    assert "lease leg" not in msg and "active leg" not in msg, msg
    assert not os.path.exists(os.path.join(ctl, ACTIVE_NAME)), "active leg must still run"
    assert not os.path.exists(_st_lease_path(root)), "lease leg must still run"


def _t_c3_c11_mandatory_lease_and_bytes(d, env):
    """T-c3 plus T-c11: acquisition requires a RESOLVED store; the lease and active record exist
    while held with EXACTLY the recorded bytes (checked-encoder TOML, closed key sets); the active
    record now persists an [owner] identity; the lease is gone after release."""
    plain = os.path.join(d, "plain")
    os.mkdir(plain)
    _st_git(["init", "-q"], plain, env)
    _st_expect_refusal(acquire_operation, plain, "op", needle="RESOLVED")
    assert not os.path.exists(os.path.join(plain, ".git", CONTROL_DIRNAME))
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op-bytes")
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    with open(lease, "rb") as fh:
        on_disk = fh.read()
    assert on_disk == cap._lease_bytes, "lease bytes must equal the recorded payload"
    with open(active, "rb") as fh:
        assert fh.read() == cap._active_bytes, "active bytes must equal the recorded payload"
    lease_doc = tomllib.loads(on_disk.decode("utf-8"))
    assert set(lease_doc) == set(_opf_check.LEASE_TOP_KEYS), lease_doc
    assert lease_doc["schema"] == _SCHEMA and type(lease_doc["schema"]) is int
    assert lease_doc["operation"] == "op-bytes"
    assert lease_doc["acquired_at"].endswith("Z") and "T" in lease_doc["acquired_at"]
    active_doc = tomllib.loads(cap._active_bytes.decode("utf-8"))
    assert set(active_doc) == set(ACTIVE_TOP_KEYS), active_doc
    assert active_doc["op_id"] == cap.op_id
    owner = active_doc["owner"]
    assert isinstance(owner, dict), owner
    assert owner["pid"] == os.getpid() and type(owner["pid"]) is int
    assert owner["nodename"] == os.uname().nodename
    assert isinstance(owner["pid-start"], str)
    assert owner["session"] == cap.op_id and owner["utc"] == active_doc["acquired_at"]
    assert active_doc["schema"] == _ACTIVE_SCHEMA and type(active_doc["schema"]) is int
    machine = active_doc["machine_store"]
    mst = os.stat(os.path.join(root, ".working", "toml"))
    assert set(machine) == set(MACHINE_STORE_KEYS), machine
    assert (machine["dev"], machine["ino"]) == (str(mst.st_dev), str(mst.st_ino)), machine
    assert machine["path"] == os.path.join(os.path.realpath(root), ".working", "toml"), machine
    release_operation(cap)
    assert not os.path.exists(lease) and not os.path.exists(active)


def _t_c4_dir_nlink_free(d, env):
    """T-c4: the link-count check is FILE-only; a control directory that gained a subdirectory
    (nlink above 2) still acquires."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    os.mkdir(os.path.join(_st_ctl_dir(root), "unrelated-subdir"))
    cap = acquire_operation(root, "op")   # the retired draft refused here (dir nlink ceiling)
    release_operation(cap)


def _t_c5_stale_and_recovery(d, env):
    """T-c5: a stale record under a free anchor refuses without recover=True; and a LONE stale
    lease with NO paired active record is fail-closed even WITH recover=True (its holder carries no
    owner identity and cannot be confirmed dead, so it is possibly-live and never seized). The
    confirmed-dead recovery path is exercised by T-r3-dead."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    with open(_st_lease_path(root), "wb") as fh:
        fh.write(b'schema = 1\nholder = "opf:host:1"\noperation = "old"\n'
                 b'acquired_at = "2026-01-01T00:00:00Z"\n')
    _st_expect_refusal(acquire_operation, root, "op", needle="stale")
    _st_expect_refusal(acquire_operation, root, "op", recover=True,
                       needle="no paired active record")
    assert os.path.exists(_st_lease_path(root)), "a possibly-live lone lease is never seized"
    os.unlink(_st_lease_path(root))       # operator manual intervention (no confirmable owner)
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_r3_live_recover_refuses(d, env):
    """T-r3-live-recover-refuses (the gemini-CRITICAL regression): a LIVE holder's records exposed
    under a FRESH/free anchor are NEVER seized under recover=True. The anchor is split BOTH ways (a
    fresh anchor via unlink of mutex.lock, and via rename aside); recovery must REFUSE and delete
    nothing, so no two-holder state can arise."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)                # establish the control dir + persistent anchor
    node = os.uname().nodename
    live_pid = os.getpid()
    live_start = _journal._pid_start(live_pid)
    holder = _st_write_active_owned(root, live_pid, live_start, node, op_id="live-op-id")
    _st_write_lease_owned(root, holder=holder)
    ctl = _st_ctl_dir(root)
    anchor = os.path.join(ctl, ANCHOR_NAME)
    active = os.path.join(ctl, ACTIVE_NAME)
    lease = _st_lease_path(root)
    for split in ("unlink", "rename"):
        # Force a SECOND acquirer to mint a fresh, free anchor while the LIVE holder's records stay:
        if split == "unlink":
            if os.path.exists(anchor):
                os.unlink(anchor)
        else:
            if os.path.exists(anchor):
                os.rename(anchor, anchor + ".aside")
        _st_expect_refusal(acquire_operation, root, "op2", recover=True, needle="live")
        assert os.path.exists(active), "the LIVE holder's active record must be preserved ({})".format(split)
        assert os.path.exists(lease), "the LIVE holder's lease must be preserved ({})".format(split)
        if split == "rename" and os.path.exists(anchor + ".aside"):
            os.unlink(anchor + ".aside")
    # No two-holder state ever arose; the (fictitious) live holder's records are cleared manually.
    os.unlink(active)
    os.unlink(lease)


def _t_r3_dead_recover_proceeds(d, env):
    """T-r3-dead-recover-proceeds: a CONFIRMED-DEAD owner (same host, a reaped child's pid plus its
    recorded /proc start time) refuses without recover=True and is cleared WITH recover=True; the
    paired lease (matching holder) is cleared with it."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    dead_pid, dead_start = _st_reaped_child()
    node = os.uname().nodename
    holder = _st_write_active_owned(root, dead_pid, dead_start, node, op_id="dead-op-id")
    _st_write_lease_owned(root, holder=holder)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    _st_expect_refusal(acquire_operation, root, "op", needle="recover=True")
    cap = acquire_operation(root, "op", recover=True)   # confirmed dead: proceeds and clears
    release_operation(cap)
    assert not os.path.exists(active), "a confirmed-dead active record is cleared"
    assert not os.path.exists(lease), "the paired lease is cleared with it"


def _t_r3_crosshost_refuses(d, env):
    """T-r3-crosshost-refuses: an owner with a FOREIGN nodename is never seized (a remote pid can
    never be probed locally), so recover=True REFUSES and preserves the records."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    foreign = "some-other-host.invalid"
    assert foreign != os.uname().nodename
    holder = _st_write_active_owned(root, os.getpid(), _journal._pid_start(os.getpid()),
                                    foreign, op_id="foreign-op-id")
    _st_write_lease_owned(root, holder=holder)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    _st_expect_refusal(acquire_operation, root, "op", recover=True, needle="host")
    assert os.path.exists(active) and os.path.exists(lease), \
        "a cross-host holder's records are never seized"
    os.unlink(active)
    os.unlink(lease)


def _t_c6_c7_med6_verified_release(d, env):
    """T-c6/T-c7 plus T-med6: a tampered record is refused and PRESERVED at release, with error
    collection (not an early raise). A tampered LEASE keeps the owner-bearing active record beside
    it (round 3, D3: the active record is removed only once the lease is gone); a tampered ACTIVE
    record still lets the lease leg run. The preserved leftovers are cleared by explicit manual
    intervention (the tampered lease no longer pairs with its active record, so recover=True
    refuses it)."""
    root = _st_git_store(d, "repo", env)
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    cap = acquire_operation(root, "op")
    with open(lease, "ab") as fh:
        fh.write(b"tampered = true\n")
    msg = _st_expect_refusal(release_operation, cap, needle="lease leg")
    assert "active leg: KEPT" in msg, msg
    assert os.path.exists(lease), "a mismatched lease must be PRESERVED"
    assert os.path.exists(active), "the active record is KEPT while its lease remains (D3)"
    os.unlink(lease)                                     # manual clear of the preserved pair
    os.unlink(active)
    cap = acquire_operation(root, "op")
    with open(active, "wb") as fh:                       # replaced content, same inode
        fh.write(b"forged = true\n")
    _st_expect_refusal(release_operation, cap, needle="active leg")
    assert os.path.exists(active), "a mismatched active record must be PRESERVED"
    assert not os.path.exists(lease), "the lease leg must still have run"
    os.unlink(active)                                    # manual clear of the owner-less leftover
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_c6_diffinode(d, env):
    """T-c6-diffinode (LOW-4): a same-BYTES but DIFFERENT-INODE lease swap under a held capability
    is refused and PRESERVED at release (identity is device+inode, not bytes), and the active
    record is KEPT beside it (round 3, D3)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    lease = _st_lease_path(root)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    with open(lease, "rb") as fh:
        same_bytes = fh.read()
    os.unlink(lease)                                     # swap the inode: same bytes, new inode
    with open(lease, "wb") as fh:
        fh.write(same_bytes)
    os.chmod(lease, 0o644)
    _st_expect_refusal(release_operation, cap, needle="device/inode")
    assert os.path.exists(lease), "a byte-identical but inode-swapped lease is PRESERVED"
    assert os.path.exists(active), "the active record is KEPT while its lease remains (D3)"
    os.unlink(lease)                                     # manual clear of the preserved swap
    os.unlink(active)
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_h4_quote(d, env):
    """T-h4-quote (LOW-4): a holder carrying a double-quote (allowed: it is outside the control
    class) round-trips through the checked encoder to a schema-valid lease with the value intact,
    distinct from the control-character refusal (T-low1)."""
    root = _st_git_store(d, "repo", env)
    holder = 'opf:host:pid with a "double quote" inside'
    cap = acquire_operation(root, "op", holder=holder)
    lease = _st_lease_path(root)
    with open(lease, "rb") as fh:
        doc = tomllib.loads(fh.read().decode("utf-8"))
    assert set(doc) == set(_opf_check.LEASE_TOP_KEYS), doc
    assert doc["holder"] == holder, "the double-quoted holder must round-trip intact"
    assert cap.holder == holder
    active_doc = tomllib.loads(cap._active_bytes.decode("utf-8"))
    assert active_doc["holder"] == holder
    release_operation(cap)
    assert not os.path.exists(lease)


def _t_c9_acquirer_identity(d, env):
    """T-c9: release refuses any caller that is not the recorded acquirer, BEFORE any leg runs
    (witnessed from a forked child sharing the descriptors)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                          # the child: a different pid, same inherited fds
        try:
            try:
                release_operation(cap)
            except OpLockError:
                os._exit(0)
            os._exit(1)
        except BaseException:
            os._exit(2)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "child release must refuse with OpLockError (status {})".format(status)
    assert os.path.exists(_st_lease_path(root)), "the refused child must not have run any leg"
    assert os.path.exists(os.path.join(_st_ctl_dir(root), ACTIVE_NAME))
    release_operation(cap)                # the true acquirer still releases cleanly


def _t_c10_git_classification(d, env):
    """T-c10: the three-way .git classification: symlink and FIFO refuse; genuine absence roots
    the control tree at the store root; a symlink at the control name refuses; a missing git
    binary on a git store refuses with NO repo-root fallback."""
    real = _st_git_store(d, "real", env)
    sym = os.path.join(d, "symgit")
    os.mkdir(sym)
    _st_store_tree(sym)
    os.symlink(os.path.join(real, ".git"), os.path.join(sym, ".git"))
    _st_expect_refusal(acquire_operation, sym, "op", needle="symlink")
    fifo_root = os.path.join(d, "fifogit")
    os.mkdir(fifo_root)
    _st_store_tree(fifo_root)
    os.mkfifo(os.path.join(fifo_root, ".git"))
    _st_expect_refusal(acquire_operation, fifo_root, "op", needle="unexpected type")
    nogit = os.path.join(d, "nogit")
    os.mkdir(nogit)
    _st_store_tree(nogit)
    cap = acquire_operation(nogit, "op")  # genuine absence: the store root is the control root
    assert os.path.isfile(os.path.join(nogit, CONTROL_DIRNAME, ANCHOR_NAME))
    release_operation(cap)
    r2 = _st_git_store(d, "r2", env)
    elsewhere = os.path.join(d, "elsewhere")
    os.mkdir(elsewhere)
    os.symlink(elsewhere, os.path.join(r2, ".git", CONTROL_DIRNAME))
    _st_expect_refusal(acquire_operation, r2, "op", needle="symlink")
    r3 = _st_git_store(d, "r3", env)
    emptybin = os.path.join(d, "emptybin")
    os.mkdir(emptybin)
    old_path = os.environ["PATH"]
    os.environ["PATH"] = emptybin
    try:
        _st_expect_refusal(acquire_operation, r3, "op", needle="git binary")
    finally:
        os.environ["PATH"] = old_path
    assert not os.path.exists(os.path.join(r3, CONTROL_DIRNAME)), \
        "missing git must never fall back to a repo-root control tree"


def _t_c3_companion(d, env):
    """T-c3-companion (LOW-4): two product roots companion-pointed at ONE store contend on the
    single shared anchor; the lease is rooted at the RESOLVED store root, not at either product
    root."""
    store = _st_git_store(d, "store", env)
    lease = os.path.join(store, ".working", "toml", _opf_check.LEASE_NAME)
    prod_a = os.path.join(d, "prodA")
    prod_b = os.path.join(d, "prodB")
    for prod in (prod_a, prod_b):
        os.mkdir(prod)
        with open(os.path.join(prod, _opf_store.POINTER_REL), "w", encoding="utf-8") as fh:
            fh.write('[store]\ntarget = "dir:{}"\n'.format(store))
    cap = acquire_operation(prod_a, "op-a")
    assert os.path.isfile(lease), "the lease is rooted at the resolved store, not the product root"
    assert not os.path.exists(os.path.join(prod_a, ".working", "toml", _opf_check.LEASE_NAME))
    _st_expect_refusal(acquire_operation, prod_b, "op-b", needle="held")
    release_operation(cap)
    cap = acquire_operation(prod_b, "op-b")   # freed: the companion product now acquires
    release_operation(cap)
    assert not os.path.exists(lease)


def _t_c12_contention_and_double_release(d, env):
    """T-c12: in-process contention on a second open file description refuses without blocking,
    and a second release of the same capability refuses."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    t0 = time.monotonic()
    _st_expect_refusal(acquire_operation, root, "op2", needle="held")
    assert time.monotonic() - t0 < 10, "contention must refuse promptly, never block"
    release_operation(cap)
    _st_expect_refusal(release_operation, cap, needle="already released")


def _t_c13_fifo_control_names(d, env):
    """T-c13: a FIFO planted at a control name is refused promptly (no-follow, O_NONBLOCK, type
    check), never blocked on and never consumed as a record."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    fifo_active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    os.mkfifo(fifo_active)
    t0 = time.monotonic()
    _st_expect_refusal(acquire_operation, root, "op", needle="not a regular file")
    assert time.monotonic() - t0 < 10, "a FIFO must not block the type check"
    os.unlink(fifo_active)
    os.mkfifo(_st_lease_path(root))
    t0 = time.monotonic()
    _st_expect_refusal(acquire_operation, root, "op", needle="not a regular file")
    assert time.monotonic() - t0 < 10, "a FIFO must not block the type check"
    os.unlink(_st_lease_path(root))
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_c8_c14_scope_out(d, env):
    """T-c8/T-c14: the nested-lock surface and the phases subtree are REMOVED (PR3 scope-out),
    and no cross-lock ordering claim survives in the module's public docstrings."""
    assert not hasattr(OpCapability, "acquire_journal_lock"), "nested journal lock is PR3"
    assert not hasattr(OpCapability, "acquire_index_lock"), "nested index lock is PR3"
    for doc in (__doc__, OpCapability.__doc__, acquire_operation.__doc__,
                release_operation.__doc__):
        text = doc or ""
        assert "canonical lock order" not in text and "canonical order" not in text \
            and "repository -> journal -> index" not in text, "retired ordering claim survives"
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    ctl = _st_ctl_dir(root)
    assert not os.path.exists(os.path.join(ctl, "ops"))
    assert not os.path.exists(os.path.join(ctl, "phases"))
    assert sorted(os.listdir(ctl)) == sorted([ANCHOR_NAME, ACTIVE_NAME]), os.listdir(ctl)
    release_operation(cap)
    assert os.listdir(ctl) == [ANCHOR_NAME], os.listdir(ctl)


def _t_low1_field_validation(d, env):
    """T-low1: holder and operation are validated against the single-sourced control class (C0,
    DEL, C1, BOM), non-emptiness, exact type, and the byte bound, BEFORE any filesystem action.
    The C1 and BOM probes are built with chr() so the test source itself stays ASCII-clean."""
    root = _st_git_store(d, "repo", env)
    for bad_holder in ("bad\x00nul", "bad\x1fctl", "bad" + chr(0x85) + "c1", "bad\x7fdel",
                       "bad" + chr(0xFEFF) + "bom", "", 123, "x" * (MAX_FIELD_BYTES + 1)):
        _st_expect_refusal(acquire_operation, root, "op", holder=bad_holder)
    for bad_op in ("", "op\nnewline", "op" + chr(0x9C) + "c1", None):
        _st_expect_refusal(acquire_operation, root, bad_op)
    assert not os.path.exists(_st_ctl_dir(root)), "validation must precede any filesystem action"
    assert not os.path.exists(_st_lease_path(root))


def _t_low1_torn_write(d, env):
    """T-low1-torn (LOW-1): a forced short/failed control write leaves NO stranded file. The
    active-record write is made to write a partial prefix and then raise; the just-created record
    must be unlinked (not stranded as a crash artefact), and the lock must remain re-acquirable."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    saved_write_all = _journal._write_all

    def _boom(fd, payload):
        os.write(fd, payload[:5])         # a torn partial write, then fail
        raise OSError("simulated control-write failure (T-low1-torn)")

    _journal._write_all = _boom
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="cannot write")
    finally:
        _journal._write_all = saved_write_all
    assert not os.path.exists(active), "a failed control write must not strand a torn active record"
    assert not os.path.exists(lease), "no lease is created when the active write fails first"
    cap = acquire_operation(root, "op")   # fully unwound and re-acquirable
    release_operation(cap)


def _t_d1_swap_after_liveness_gate(d, env):
    """T-d1 (DEF-1, BLOCKER): after the liveness gate confirms holder A dead, an anchor split lets a
    LIVE holder B publish FRESH records at the same names before the recovery delete runs. The delete
    must remove ONLY the very objects+bytes the gate read, so B's swapped-in live records are refused
    and PRESERVED, never seized into a two-holder state. The interleave is injected by wrapping the
    module's own _require_holder_confirmed_dead to swap the records the instant it returns."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)                # establish the control dir + persistent anchor
    node = os.uname().nodename
    dead_pid, dead_start = _st_reaped_child()
    a_holder = _st_write_active_owned(root, dead_pid, dead_start, node, op_id="dead-A-op-id",
                                      holder="opf:holderA-dead")
    _st_write_lease_owned(root, holder=a_holder)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    mod = sys.modules[__name__]
    saved_gate = mod._require_holder_confirmed_dead

    def _wrapped_gate(*a, **k):
        result = saved_gate(*a, **k)      # confirms A dead, captures A's ident+bytes
        # A split anchor: a LIVE holder B (this process) replaces A's records RIGHT NOW, new inodes.
        os.unlink(active)
        b_holder = _st_write_active_owned(root, os.getpid(), _journal._pid_start(os.getpid()),
                                          node, op_id="live-B-op-id", holder="opf:holderB-live")
        os.unlink(lease)
        _st_write_lease_owned(root, holder=b_holder)
        return result

    mod._require_holder_confirmed_dead = _wrapped_gate
    try:
        _st_expect_refusal(acquire_operation, root, "op2", recover=True, needle="never seized")
    finally:
        mod._require_holder_confirmed_dead = saved_gate
    assert os.path.exists(active) and os.path.exists(lease), \
        "a live holder B's swapped-in records must be PRESERVED, never deleted into two holders"
    with open(active, "rb") as fh:
        assert b"holderB-live" in fh.read(), "B's record must be intact (not A's, not deleted)"
    os.unlink(active)                      # manual clear of the fictitious live-B records
    os.unlink(lease)


def _t_d2_missing_holder_fields(d, env):
    """T-d2 (DEF-2): stale records whose holder is MISSING or EMPTY must NOT be seized under
    recover=True. A missing holder yields None on both records (None == None) and an empty holder
    yields "" on both ("" == ""), so the pre-fix equality would match and seize a possibly-live
    holder. Both the completed-schema keyset check and the required-non-empty-holder check refuse,
    with a CONFIRMED-DEAD reaped-child owner so ONLY that validation stands in the way. Sub-cases E
    and F (round 3, D5) keep the ACTIVE record valid and break ONLY the lease holder, so the LEASE
    keyset and non-empty-holder checks are the ones exercised."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    dead_pid, dead_start = _st_reaped_child()
    node = os.uname().nodename
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)

    def _write_active(holder_line):
        lines = ["schema = {}".format(_ACTIVE_SCHEMA), 'op_id = "d2-op-id"']
        if holder_line is not None:
            lines.append(holder_line)
        lines += ['operation = "recovered-op"', 'acquired_at = "2026-01-01T00:00:00Z"', "",
                  "[owner]", "pid = {}".format(dead_pid), "uid = {}".format(os.getuid()),
                  'nodename = "{}"'.format(node), 'session = "d2-op-id"',
                  'utc = "2026-01-01T00:00:00Z"', 'pid-start = "{}"'.format(dead_start), ""]
        lines += list(_st_machine_store_lines(root))
        with open(active, "w", encoding="utf-8") as fh:
            fh.write(chr(10).join(lines))

    def _write_lease(holder_line):
        lines = ["schema = 1"]
        if holder_line is not None:
            lines.append(holder_line)
        lines += ['operation = "recovered-op"', 'acquired_at = "2026-01-01T00:00:00Z"', ""]
        with open(lease, "w", encoding="utf-8") as fh:
            fh.write(chr(10).join(lines))

    # Sub-case A: holder KEY MISSING on both (None == None) -> refused by the ACTIVE keyset check.
    # Every sub-case needle names its ONE check uniquely: the earlier generic "refusing recovery"
    # also matched the non-empty-field refusal (and "closed set" alone also matches the lease and
    # [machine_store] keyset refusals), so it could not tell which check stood in the way.
    _write_active(None)
    _write_lease(None)
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="stale active record top-level keys")
    assert os.path.exists(active) and os.path.exists(lease), \
        "missing-holder records are never seized"

    # Sub-case B: holder EMPTY on both ("" == "") -> refused by the required-non-empty-holder check.
    _write_active('holder = ""')
    _write_lease('holder = ""')
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="stale active record holder is missing or not a non-empty string")
    assert os.path.exists(active) and os.path.exists(lease), "empty-holder records are never seized"

    # Sub-case C: complete, equal holders, but the lease is from a DIFFERENT acquisition (its
    # operation differs) -> refused by the pairing check; the pre-fix holder-only match seized it.
    _write_active('holder = "opf:d2-holder"')
    _write_lease('holder = "opf:d2-holder"')
    with open(lease, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(lease, "w", encoding="utf-8") as fh:
        fh.write(text.replace('operation = "recovered-op"', 'operation = "another-op"'))
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="not the same acquisition")
    assert os.path.exists(active) and os.path.exists(lease), "an unpaired lease is never seized"

    # Sub-case D: complete and paired, but an unsupported schema -> refused.
    _write_lease('holder = "opf:d2-holder"')
    with open(active, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(active, "w", encoding="utf-8") as fh:
        fh.write(text.replace("schema = {}".format(_ACTIVE_SCHEMA), "schema = 99", 1))
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="stale active record schema is not the supported version")
    assert os.path.exists(active) and os.path.exists(lease), "an unsupported schema is never seized"

    # Sub-cases E and F (round 3, D5): the ACTIVE record is complete, valid, and confirmed dead, and
    # ONLY the LEASE holder is missing (E) or empty (F). Sub-cases A and B broke BOTH records, so the
    # active-record check refused first and the lease checks were never reached; here the
    # active-record validation passes and the needle names the LEASE check that refuses.
    _write_active('holder = "opf:d2-holder"')
    _write_lease(None)
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="stale lease top-level keys")
    assert os.path.exists(active) and os.path.exists(lease), \
        "a lease with a missing holder is never seized"
    _write_lease('holder = ""')
    _st_expect_refusal(acquire_operation, root, "op2", recover=True,
                       needle="stale lease holder is missing or not a non-empty string")
    assert os.path.exists(active) and os.path.exists(lease), \
        "a lease with an empty holder is never seized"
    os.unlink(active)
    os.unlink(lease)


def _t_d3_eio_release_and_unwind(d, env):
    """T-d3 (DEF-3): a raw OS failure (EIO) inside a verified-unlink leg is normalized to OpLockError
    and collected, so the release still decides the active leg (round 3, D3: KEPT beside a lease it
    could not remove) and still runs the final anchor unlock, instead of a raw OSError skipping the
    unlock and _released and leaking the fds. Exercised
    for BOTH the release path and the acquire unwind path. os.read is faulted ONLY for the control
    records (matched by the read fd's (st_dev, st_ino) against the records' current identities, a
    portable match with no /proc dependency), so git subprocess pipe reads are unaffected."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    mod = sys.modules[__name__]
    saved_read = os.read

    def _record_idents():
        idents = set()
        for path in (active, lease):
            try:
                st = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            idents.add((st.st_dev, st.st_ino))
        return idents

    def _eio_read(fd, n):
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) in _record_idents():
            raise OSError(errno.EIO, "simulated read failure (T-d3)")
        return saved_read(fd, n)

    # Phase 1: EIO in RELEASE. The lease leg fails, so the active record is KEPT (D3); the anchor is
    # unlocked and _released set.
    cap = acquire_operation(root, "op")
    os.read = _eio_read
    try:
        msg = _st_expect_refusal(release_operation, cap, needle="active leg")
    finally:
        os.read = saved_read
    assert "lease leg" in msg and "active leg" in msg, msg
    assert cap._released is True, "the unlock/close/_released must run despite the EIO legs"
    assert os.path.exists(active) and os.path.exists(lease), \
        "both records PRESERVED: the lease leg failed, so the active record was KEPT (D3)"
    os.unlink(active)
    os.unlink(lease)
    cap = acquire_operation(root, "op")    # re-acquirable: the anchor was truly unlocked
    release_operation(cap)

    # Phase 2: EIO in the acquire UNWIND. Force a post-creation failure (a raising OpCapability) so
    # both records exist when the unwind runs, with os.read still faulting each unwind leg's read.
    saved_cap = mod.OpCapability

    class _BoomCap:
        def __init__(self, *a, **k):
            raise OSError("simulated post-creation failure (T-d3 unwind)")

    os.read = _eio_read
    mod.OpCapability = _BoomCap
    try:
        msg = _st_expect_refusal(acquire_operation, root, "op", needle="unwind failed")
    finally:
        os.read = saved_read
        mod.OpCapability = saved_cap
    assert "lease (unwind)" in msg and "active record (unwind)" in msg, msg
    assert os.path.exists(active) and os.path.exists(lease), \
        "the EIO-failed lease unwind leg PRESERVED the lease and KEPT the active record (D3)"
    os.unlink(active)                      # manual clear of the preserved records
    os.unlink(lease)
    cap = acquire_operation(root, "op")    # anchor unlocked + fds closed by the unwind finally
    release_operation(cap)


def _t_d4_gitdir_trailing_space(d, env):
    """T-d4 (DEF-4): _git_common_dir strips ONLY the single newline record terminator, never
    arbitrary trailing whitespace, so a common git dir whose real name ends in a space is honoured
    and the control root is not silently redirected to a stripped decoy. Witnessed end to end over
    a REAL git store whose separate git dir is named "common " beside a decoy "common": the buggy
    .strip() created the control tree in the decoy."""
    true_dir = os.path.join(d, "common ")          # trailing space IS part of the name
    decoy_dir = os.path.join(d, "common")          # the stripped path: the decoy the bug would pick
    os.mkdir(decoy_dir)
    root = os.path.join(d, "repo")
    os.mkdir(root)
    _st_git(["init", "-q", "--separate-git-dir", true_dir], root, env)
    _st_store_tree(root)
    assert _git_common_dir(root) == os.path.abspath(true_dir), \
        "the one record terminator is stripped, never the trailing space"
    cap = acquire_operation(root, "op")
    try:
        assert os.path.isfile(os.path.join(true_dir, CONTROL_DIRNAME, ANCHOR_NAME)), \
            "the anchor must live under the real space-suffixed common git dir"
        assert not os.path.exists(os.path.join(decoy_dir, CONTROL_DIRNAME)), \
            "the control tree must never be redirected to the stripped decoy"
    finally:
        release_operation(cap)


def _t_d5_failed_cleanup(d, env):
    """T-d5 (DEF-5): when the torn-record cleanup ITSELF cannot remove the file, the write failure
    AND the stranded leftover are both surfaced (the leftover named), never swallowed into a bare
    'cannot write' that falsely implies a clean unwind. Witnessed by hardlinking the just-created
    torn active record's staging file during the failing write so cleanup finds a link count it did
    not make and leaves it in place; the buggy code returned only 'cannot write' with the strand
    unnamed. (Round 3, D1: the torn bytes live in the staging file, never at the final name.)"""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")    # create the control dir + anchor first
    release_operation(cap)
    ctl = _st_ctl_dir(root)
    active = os.path.join(ctl, ACTIVE_NAME)
    hardlink = os.path.join(ctl, "torn-hardlink-victim")
    saved_write_all = _journal._write_all

    def _boom(fd, payload):
        os.write(fd, payload[:5])          # a torn partial write
        staging = _st_staging_leftovers(ctl, ACTIVE_NAME)
        assert len(staging) == 1, staging
        os.link(os.path.join(ctl, staging[0]), hardlink)   # pin the inode: a link not ours
        raise OSError("simulated control-write failure (T-d5)")

    _journal._write_all = _boom
    try:
        msg = _st_expect_refusal(acquire_operation, root, "op", needle="could not")
    finally:
        _journal._write_all = saved_write_all
    assert "active" in msg and "cannot write" in msg, msg
    assert not os.path.exists(active), "a torn record is never published at the final name"
    assert _st_staging_leftovers(ctl, ACTIVE_NAME), \
        "a torn record the cleanup could not remove IS surfaced and left, not hidden"
    os.unlink(hardlink)                    # manual clear of the pinning link
    cap = acquire_operation(root, "op")    # the staging leftover is garbage: removed, re-acquirable
    release_operation(cap)
    assert not _st_staging_leftovers(ctl, ACTIVE_NAME), "the staging leftover must be removed"


def _st_staging_leftovers(dir_path, name):
    """The staging leftovers (D1 staging names) for the control record `name` under dir_path."""
    return sorted(e for e in os.listdir(dir_path) if _is_staging_name(e, name))


def _st_open_fds():
    """The number of descriptors this process holds open (Linux /proc/self/fd, else /dev/fd). The
    listing's own transient descriptor is present in every measurement alike, so a difference of
    two readings is exactly the descriptors leaked in between."""
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(fd_dir):
            return len(os.listdir(fd_dir))
    raise AssertionError("no descriptor listing (/proc/self/fd or /dev/fd) to count leaks with")


def _st_crash_acquire(root):
    """A REAL crashed holder: a forked child acquires the lock at `root` and exits WITHOUT
    releasing, and the parent reaps it, so both records are left behind with a confirmed-dead
    owner (the kernel drops the child's flock at exit)."""
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            acquire_operation(root, "crashed-op")
            os._exit(0)                   # no release: the crash leaves both records
        except BaseException:
            os._exit(3)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "the crashing child must acquire (status {})".format(status)


def _t_r2_1_crash_between_recovery_deletes(d, env):
    """T-r2-1 (recovery delete order): a recovering process is SIGKILLed between its two recovery
    deletes. The lease is deleted (and fsynced) FIRST, so the crash leaves a LONE ACTIVE record
    that still carries the dead owner's identity, and a later recover=True re-confirms the holder
    dead and succeeds. The earlier order (active first) left a lone owner-less lease that every
    later recovery refused forever. The kill is injected deterministically by wrapping the
    module's own _recover_stale in the recovering child so the process dies the instant its FIRST
    delete returns."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    _st_crash_acquire(root)
    assert os.path.exists(active) and os.path.exists(lease)
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:                          # the recovering process, killed between the deletes
        try:
            mod = sys.modules[__name__]
            saved = mod._recover_stale

            def _die_after_first_delete(*a, **k):
                saved(*a, **k)
                os.kill(os.getpid(), signal.SIGKILL)

            mod._recover_stale = _die_after_first_delete
            acquire_operation(root, "op", recover=True)
            os._exit(0)                   # not reached: the first delete kills the process
        except BaseException:
            os._exit(3)
    _, status = os.waitpid(pid, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, \
        "the recovering child must die by the injected SIGKILL (status {})".format(status)
    assert not os.path.exists(lease), "the lease is deleted FIRST"
    assert os.path.exists(active), "the owner-bearing active record survives the crash"
    cap = acquire_operation(root, "op", recover=True)   # the lone active record is recoverable
    release_operation(cap)
    assert not os.path.exists(active) and not os.path.exists(lease)


def _t_r2_2_cross_worktree_recovery(d, env):
    """T-r2-2 (cross-worktree pairing): a holder acquires in the MAIN checkout and crashes; a
    recover=True from a LINKED worktree (same shared control root and active record, but its own
    machine store) must REFUSE and delete nothing, because the paired lease lives in the main
    checkout's machine store; recovery from the main checkout then clears both records. Before the
    fix the worktree recovery deleted the shared active record and stranded the main lease as a
    lone, unrecoverable leftover. A schema-1 record (the earlier format, no [machine_store]) is
    refused with a message naming that cause."""
    main = _st_git_store(d, "main", env)
    wt = os.path.join(d, "wt")
    _st_git(["worktree", "add", "--detach", "-q", wt], main, env)
    active = os.path.join(_st_ctl_dir(main), ACTIVE_NAME)
    main_lease = _st_lease_path(main)
    _st_crash_acquire(main)
    assert os.path.exists(active) and os.path.exists(main_lease)
    with open(active, "rb") as fh:
        before = fh.read()
    _st_expect_refusal(acquire_operation, wt, "op", recover=True,
                       needle="paired with a different machine store")
    with open(active, "rb") as fh:
        assert fh.read() == before, "the shared active record must be PRESERVED byte for byte"
    assert os.path.exists(main_lease), "the main checkout's lease must be preserved"
    assert not os.path.exists(_st_lease_path(wt)), "the worktree acquired nothing"
    cap = acquire_operation(main, "op", recover=True)   # the owning checkout recovers both
    release_operation(cap)
    assert not os.path.exists(active) and not os.path.exists(main_lease)
    # Legacy schema-1 record (no [machine_store]): refused with a clear, specific message.
    dead_pid, dead_start = _st_reaped_child()
    holder = _st_write_active_owned(main, dead_pid, dead_start, os.uname().nodename,
                                    op_id="legacy-op-id")
    with open(active, "r", encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("schema = {}".format(_ACTIVE_SCHEMA), "schema = 1", 1)
    text = text[:text.index("[machine_store]")]
    with open(active, "w", encoding="utf-8") as fh:
        fh.write(text)
    _st_write_lease_owned(main, holder=holder)
    _st_expect_refusal(acquire_operation, main, "op", recover=True,
                       needle="earlier schema 1 format")
    assert os.path.exists(active) and os.path.exists(main_lease), "a legacy record is never seized"
    os.unlink(active)                      # manual intervention, as the refusal instructs
    os.unlink(main_lease)


def _t_r2_3_store_fd_close_eio(d, env):
    """T-r2-3 (store-root close inside the unwind): an EIO from the final store-root descriptor
    close must run the full acquisition unwind: an OpLockError, NO leaked descriptor (before the
    fix five leaked, the flocked anchor among them), no stranded records, and the lock
    re-acquirable (no phantom contention). The failing close is injected on exactly the store-root
    descriptor, which is really closed first (as Linux does) and then reported as EIO."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")    # control dir + anchor exist; git warm
    release_operation(cap)
    saved_open = _opf_store._open_dir_nofollow
    saved_close = os.close
    store_root_abs = os.path.realpath(root)
    target = {}

    def _open_recording(path):
        fd = saved_open(path)
        if str(path) == store_root_abs and "fd" not in target:
            target["fd"] = fd
        return fd

    def _close_eio(fd):
        saved_close(fd)
        if target.get("fd") == fd and not target.get("fired"):
            target["fired"] = True
            raise OSError(errno.EIO, "simulated store-root close failure (T-r2-3)")

    baseline = _st_open_fds()
    _opf_store._open_dir_nofollow = _open_recording
    os.close = _close_eio
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="store-root descriptor")
    finally:
        os.close = saved_close
        _opf_store._open_dir_nofollow = saved_open
    assert target.get("fired"), "the injected store-root close failure must have fired"
    assert _st_open_fds() == baseline, "no descriptor may leak (the flocked anchor above all)"
    assert not os.path.exists(os.path.join(_st_ctl_dir(root), ACTIVE_NAME))
    assert not os.path.exists(_st_lease_path(root))
    cap = acquire_operation(root, "op")    # not contention: the anchor was really unlocked
    release_operation(cap)


def _t_r2_4_interrupt_mid_write(d, env):
    """T-r2-4 (BaseException mid-write): a KeyboardInterrupt landing inside the control-record
    write loop propagates AS ITSELF, but only after the torn record is unlinked and its descriptor
    closed, with the cleanup outcome attached as a note. Before the fix the descriptor leaked and
    the torn active record was stranded, refusing every later acquisition as stale."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    saved_write_all = _journal._write_all

    def _interrupt(fd, payload):
        os.write(fd, payload[:5])          # a torn partial write, then the interrupt lands
        raise KeyboardInterrupt()

    baseline = _st_open_fds()
    _journal._write_all = _interrupt
    caught = None
    try:
        acquire_operation(root, "op")
    except KeyboardInterrupt as exc:
        caught = exc
    finally:
        _journal._write_all = saved_write_all
    assert caught is not None, "the KeyboardInterrupt must propagate as itself"
    notes = " ".join(getattr(caught, "__notes__", ()))
    assert "torn record was removed" in notes, notes
    assert _st_open_fds() == baseline, "the torn record's descriptor must not leak"
    assert not os.path.exists(active), "the torn active record must not be stranded"
    cap = acquire_operation(root, "op")    # no stale refusal: nothing was stranded
    release_operation(cap)


def _t_r2_5_machine_walk_close(d, env):
    """T-r2-5 (machine-store walk hand-off): an EIO from closing an intermediate machine-store
    walk descriptor must leak NEITHER descriptor and must not double-close: the refusal is an
    OpLockError naming the walk. Before the fix the newly opened child descriptor leaked and the
    already-released parent was closed a second time (EBADF masking the real failure)."""
    root = _st_git_store(d, "repo", env)
    saved_open_at = sys.modules[__name__]._open_dir_at
    saved_close = os.close
    targets = set()
    fired = []
    mod = sys.modules[__name__]

    def _open_recording(parent, name, label):
        fd = saved_open_at(parent, name, label)
        if name == ".working":
            targets.add(fd)
        return fd

    def _close_eio(fd):
        saved_close(fd)
        if fd in targets:
            targets.discard(fd)
            fired.append(fd)
            raise OSError(errno.EIO, "simulated walk close failure (T-r2-5)")

    baseline = _st_open_fds()
    mod._open_dir_at = _open_recording
    os.close = _close_eio
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="machine-store walk descriptor")
    finally:
        os.close = saved_close
        mod._open_dir_at = saved_open_at
    assert fired, "the injected walk close failure must have fired"
    assert _st_open_fds() == baseline, "neither walk descriptor may leak"
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_f3_1_sigkill_mid_publication(d, env):
    """T-f3-1 (round 3, D1: atomic publication). A RECOVERING process (a crashed holder's records
    are left behind first, as in the codex counterexample) is SIGKILLed mid-publication: after a
    partial write of the ACTIVE record's staging file, after a partial write of the LEASE's staging
    file, and after the active record's os.link but before its staging name is retired. In every
    case a later recover=True must SUCCEED: the final name holds either nothing or the complete
    record, and the staging leftover is removed as garbage under the anchor flock. Before the fix
    the partial bytes sat at the final name and every later recovery refused the torn record as
    undecodable, forever."""
    root = _st_git_store(d, "repo", env)
    ctl = _st_ctl_dir(root)
    machine = os.path.join(root, ".working", "toml")
    active = os.path.join(ctl, ACTIVE_NAME)
    lease = _st_lease_path(root)
    for case in ("active-partial", "lease-partial", "active-linked"):
        _st_crash_acquire(root)
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:                      # the recovering process, killed mid-publication
            try:
                if case == "active-linked":
                    saved_link = os.link

                    def _die_after_link(*a, **k):
                        saved_link(*a, **k)
                        os.kill(os.getpid(), signal.SIGKILL)

                    os.link = _die_after_link
                else:
                    target = 1 if case == "active-partial" else 2
                    calls = []
                    saved_write_all = _journal._write_all

                    def _die_mid_write(fd, payload):
                        calls.append(fd)
                        if len(calls) == target:
                            os.write(fd, payload[:5])
                            os.kill(os.getpid(), signal.SIGKILL)
                        saved_write_all(fd, payload)

                    _journal._write_all = _die_mid_write
                acquire_operation(root, "op", recover=True)
                os._exit(0)               # not reached: the injected kill lands first
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, \
            "the recovering child must die by the injected SIGKILL ({}, status {})".format(
                case, status)
        leftovers = _st_staging_leftovers(ctl, ACTIVE_NAME) \
            + _st_staging_leftovers(machine, _opf_check.LEASE_NAME)
        cap = acquire_operation(root, "op", recover=True)   # recovery must succeed
        release_operation(cap)
        assert leftovers, "the kill must have landed mid-publication ({})".format(case)
        assert not os.path.exists(active) and not os.path.exists(lease), case
        assert not _st_staging_leftovers(ctl, ACTIVE_NAME), case
        assert not _st_staging_leftovers(machine, _opf_check.LEASE_NAME), case


def _t_f3_2_lease_removal_failure_keeps_active(d, env):
    """T-f3-2 (round 3, D3: release order). When removing the LEASE fails (EIO injected into the
    lease unlink only), the owner-bearing ACTIVE record is KEPT, on the release path and on the
    acquisition-unwind path alike, so once the holder has exited a recover=True confirms it dead and
    clears BOTH records. Before the fix the active record was deleted anyway, leaving a lone
    owner-less lease that every later recovery refused ("no paired active record")."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    for path_kind in ("release", "unwind"):
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:                      # the holder: its lease removal fails, then it exits
            try:
                mod = sys.modules[__name__]
                saved_unlink = os.unlink

                def _eio_lease_unlink(name, *a, **k):
                    if name == _opf_check.LEASE_NAME and k.get("dir_fd") is not None:
                        raise OSError(errno.EIO, "simulated lease unlink failure (T-f3-2)")
                    return saved_unlink(name, *a, **k)

                if path_kind == "release":
                    cap = acquire_operation(root, "op")
                    os.unlink = _eio_lease_unlink
                    needle = "active leg: KEPT"
                    try:
                        release_operation(cap)
                    except OpLockError as exc:
                        os._exit(0 if needle in str(exc) else 4)
                    os._exit(1)

                class _BoomCap:
                    def __init__(self, *a, **k):
                        raise OSError("simulated post-creation failure (T-f3-2 unwind)")

                # The unwind's lease leg is failed at _verified_unlink: replacing os.unlink before
                # acquire_operation would fail its containment probe (os.supports_dir_fd) instead.
                saved_verified_unlink = mod._verified_unlink

                def _eio_lease_leg(dir_fd, name, *a, **k):
                    if name == _opf_check.LEASE_NAME:
                        raise OpLockError("cannot unlink lease (unwind) (simulated EIO, T-f3-2)")
                    return saved_verified_unlink(dir_fd, name, *a, **k)

                mod._verified_unlink = _eio_lease_leg
                mod.OpCapability = _BoomCap
                try:
                    acquire_operation(root, "op")
                except OpLockError as exc:
                    os._exit(0 if "active record (unwind) KEPT" in str(exc) else 4)
                os._exit(1)
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
            "the {} must refuse and name the KEPT active record (status {})".format(
                path_kind, status)
        assert os.path.exists(active), \
            "the owner-bearing active record is KEPT beside the lease ({})".format(path_kind)
        assert os.path.exists(lease), "the lease removal failed ({})".format(path_kind)
        cap = acquire_operation(root, "op", recover=True)   # the dead holder is recoverable
        release_operation(cap)
        assert not os.path.exists(active) and not os.path.exists(lease), path_kind


def _t_f3_3_control_root_close_fd_reuse(d, env):
    """T-f3-3 (round 3, D2, codex HIGH 4: descriptor REUSE). The control-root descriptor's close is
    made to really close and then report EIO, and its number is immediately reused for an unrelated
    /dev/null descriptor. The acquisition must refuse WITHOUT closing that unrelated descriptor:
    ownership is cleared before the close. Before the fix the unwind closed the number a second
    time, silently closing the unrelated file (a later fstat on it returned EBADF)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")    # control dir + anchor exist; git warm
    release_operation(cap)
    saved_open = _opf_store._open_dir_nofollow
    saved_close = os.close
    common = os.path.join(os.path.realpath(root), ".git")
    target = {}

    def _open_recording(path):
        fd = saved_open(path)
        if str(path) == common and "fd" not in target:
            target["fd"] = fd
        return fd

    def _close_then_reuse(fd):
        saved_close(fd)
        if target.get("fd") == fd and "fired" not in target:
            target["fired"] = True
            unrelated = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
            if unrelated != fd:           # force the reuse onto the just-released number
                os.dup2(unrelated, fd, inheritable=False)
                saved_close(unrelated)
            raise OSError(errno.EIO, "simulated control-root close failure (T-f3-3)")

    baseline = _st_open_fds()
    _opf_store._open_dir_nofollow = _open_recording
    os.close = _close_then_reuse
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="control-root descriptor")
    finally:
        os.close = saved_close
        _opf_store._open_dir_nofollow = saved_open
    assert target.get("fired"), "the injected control-root close failure must have fired"
    try:
        st = os.fstat(target["fd"])
    except OSError:
        raise AssertionError("the unwind closed an UNRELATED descriptor that reused the "
                             "control-root number")
    assert stat.S_ISCHR(st.st_mode), "the reused number must still be the unrelated /dev/null"
    os.close(target["fd"])
    assert _st_open_fds() == baseline, "no descriptor may leak"
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _st_anchor_free(root):
    """True when the mutex anchor's flock is FREE (a fresh open file description can take it)."""
    fd = os.open(os.path.join(_st_ctl_dir(root), ANCHOR_NAME), os.O_RDWR | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def _st_expect_interrupt(fn, *args, **kwargs):
    """Run fn expecting a KeyboardInterrupt to propagate AS ITSELF; returns it."""
    try:
        fn(*args, **kwargs)
    except KeyboardInterrupt as exc:
        return exc
    raise AssertionError("the KeyboardInterrupt must propagate as itself from {}".format(
        getattr(fn, "__name__", fn)))


def _t_f3_4_release_interruptions(d, env):
    """T-f3-4 (round 3, D2, codex HIGH 3: release sites). A KeyboardInterrupt lands (1) during the
    release's anchor identity re-check, (2) at the step that gives up the lock (the LOCK_UN until
    fix round 9; since then the anchor descriptor's close, interrupted right after the kernel
    closed it), and (3) right after the first retained record descriptor closes. Each time it
    propagates AS ITSELF, but only after EVERY retained descriptor is closed (no descriptor-count
    delta) and the anchor flock is free. Before the fix the first two leaked all five retained
    descriptors with the anchor still locked, and the third leaked four."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    baseline = _st_open_fds()

    # Site 1: the anchor identity re-check (before any leg runs).
    cap = acquire_operation(root, "op")
    target = cap._anchor_fd
    saved_fstat = os.fstat
    fired = []

    def _fstat_interrupt(fd, *a, **k):
        if fd == target and not fired:
            fired.append(fd)
            raise KeyboardInterrupt()
        return saved_fstat(fd, *a, **k)

    os.fstat = _fstat_interrupt
    try:
        _st_expect_interrupt(release_operation, cap)
    finally:
        os.fstat = saved_fstat
    assert fired, "the anchor re-check interruption must have fired"
    assert cap._released is True, "an interrupted release still ends released"
    assert _st_open_fds() == baseline, "no retained descriptor may leak (site 1)"
    assert _st_anchor_free(root), "the anchor must be unlocked (site 1)"
    assert os.path.exists(active) and os.path.exists(lease), "the legs never ran (site 1)"
    os.unlink(active)                      # manual clear: the interruption preceded the legs
    os.unlink(lease)

    # Site 2: the step that gives up the lock. Fix round 9 (release by close) removed the LOCK_UN
    # this site used to interrupt; the lock is given up by the anchor descriptor's close, which is
    # interrupted here right after the kernel really closed it.
    cap = acquire_operation(root, "op")
    target = cap._anchor_fd
    saved_close = os.close
    fired = []

    def _anchor_close_interrupt(fd):
        saved_close(fd)
        if fd == target and not fired:
            fired.append(fd)
            raise KeyboardInterrupt()

    os.close = _anchor_close_interrupt
    try:
        _st_expect_interrupt(release_operation, cap)
    finally:
        os.close = saved_close
    assert fired, "the anchor-close interruption must have fired"
    assert _st_open_fds() == baseline, "no retained descriptor may leak (site 2)"
    assert _st_anchor_free(root), "the anchor must be unlocked (site 2)"
    assert not os.path.exists(active) and not os.path.exists(lease), "the legs ran (site 2)"

    # Site 3: right after the FIRST retained descriptor (the lease's) is really closed.
    cap = acquire_operation(root, "op")
    target = cap._lease_fd
    saved_close = os.close
    fired = []

    def _close_interrupt(fd):
        saved_close(fd)
        if fd == target and not fired:
            fired.append(fd)
            raise KeyboardInterrupt()

    os.close = _close_interrupt
    try:
        _st_expect_interrupt(release_operation, cap)
    finally:
        os.close = saved_close
    assert fired, "the first-close interruption must have fired"
    assert _st_open_fds() == baseline, "the remaining four descriptors must still close (site 3)"
    assert _st_anchor_free(root), "the anchor must be unlocked (site 3)"
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_f3_5_acquire_interruptions(d, env):
    """T-f3-5 (round 3, D2, codex HIGH 3: acquisition sites). A KeyboardInterrupt lands during (1)
    the control-directory validation, (2) the anchor validation, (3) the control-directory fsync
    after the anchor's creation, and (4) the cleanup of a failed control-record write. Each time it
    propagates AS ITSELF with no descriptor-count delta, and the lock is re-acquirable afterwards
    (for site 4 the interrupted cleanup's staging leftover is removed as garbage). Before the fix
    sites 1 to 3 each leaked one descriptor, and site 4 leaked the record's descriptor and stranded
    the torn record as a stale artefact."""
    mod = sys.modules[__name__]

    # Site 1: the control-directory validation inside _open_control_dir.
    root = _st_git_store(d, "r1", env)
    baseline = _st_open_fds()
    saved_ctl = mod._validate_ctl_dir_fd

    def _ctl_interrupt(fd, label):
        raise KeyboardInterrupt()

    mod._validate_ctl_dir_fd = _ctl_interrupt
    try:
        _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        mod._validate_ctl_dir_fd = saved_ctl
    assert _st_open_fds() == baseline, "no descriptor may leak (site 1)"
    cap = acquire_operation(root, "op")
    release_operation(cap)

    # Site 2: the anchor validation inside _open_anchor.
    baseline = _st_open_fds()
    saved_file = mod._validate_file_fd

    def _file_interrupt(fd, label):
        if label == "mutex anchor":
            raise KeyboardInterrupt()
        return saved_file(fd, label)

    mod._validate_file_fd = _file_interrupt
    try:
        _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        mod._validate_file_fd = saved_file
    assert _st_open_fds() == baseline, "no descriptor may leak (site 2)"
    cap = acquire_operation(root, "op")
    release_operation(cap)

    # Site 3: the control-directory fsync right after the anchor's creation (a fresh store).
    root3 = _st_git_store(d, "r3", env)
    ctl3 = _st_ctl_dir(root3)
    baseline = _st_open_fds()
    saved_fsync = os.fsync
    fired = []

    def _fsync_interrupt(fd):
        if not fired and os.path.exists(os.path.join(ctl3, ANCHOR_NAME)):
            st = os.fstat(fd)
            cst = os.stat(ctl3)
            if (st.st_dev, st.st_ino) == (cst.st_dev, cst.st_ino):
                fired.append(fd)
                raise KeyboardInterrupt()
        return saved_fsync(fd)

    os.fsync = _fsync_interrupt
    try:
        _st_expect_interrupt(acquire_operation, root3, "op")
    finally:
        os.fsync = saved_fsync
    assert fired, "the anchor-creation fsync interruption must have fired"
    assert _st_open_fds() == baseline, "the new anchor's descriptor must not leak (site 3)"
    cap = acquire_operation(root3, "op")
    release_operation(cap)

    # Site 4: the cleanup of a failed control-record write is itself interrupted.
    baseline = _st_open_fds()
    saved_write_all = _journal._write_all
    saved_cleanup = mod._unlink_created_on_failure

    def _boom(fd, payload):
        os.write(fd, payload[:5])          # a torn partial write, then fail
        raise OSError("simulated control-write failure (T-f3-5)")

    def _cleanup_interrupt(*a, **k):
        raise KeyboardInterrupt()

    _journal._write_all = _boom
    mod._unlink_created_on_failure = _cleanup_interrupt
    try:
        _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        _journal._write_all = saved_write_all
        mod._unlink_created_on_failure = saved_cleanup
    assert _st_open_fds() == baseline, "the torn record's descriptor must not leak (site 4)"
    assert not os.path.exists(os.path.join(_st_ctl_dir(root), ACTIVE_NAME)), \
        "a torn record is never published at the final name (site 4)"
    cap = acquire_operation(root, "op")    # the staging leftover is garbage: removed
    release_operation(cap)
    assert not _st_staging_leftovers(_st_ctl_dir(root), ACTIVE_NAME), "site 4 leftover removed"


def _t_f3_6_shared_walker_handoff(d, env):
    """T-f3-6 (round 3, D2, codex MEDIUM 5: the shared no-follow walker). Through acquire_operation,
    the close of an intermediate parent descriptor in _opf_store._open_dir_nofollow is made to
    really close and then report EIO. The refusal must leak NO descriptor: the walker owns the
    child before it closes the parent and closes the child on failure. Before the fix the newly
    opened child descriptor leaked (a descriptor-count delta of one)."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    parent_st = os.stat(d)
    saved_walk = _opf_store._open_dir_nofollow
    saved_close = os.close
    state = {"in": False, "fired": False}

    def _walk(path):
        state["in"] = True
        try:
            return saved_walk(path)
        finally:
            state["in"] = False

    def _close_eio(fd):
        hit = False
        if state["in"] and not state["fired"]:
            try:
                st = os.fstat(fd)
                hit = (st.st_dev, st.st_ino) == (parent_st.st_dev, parent_st.st_ino)
            except OSError:
                hit = False
        saved_close(fd)
        if hit:
            state["fired"] = True
            raise OSError(errno.EIO, "simulated walker close failure (T-f3-6)")

    baseline = _st_open_fds()
    _opf_store._open_dir_nofollow = _walk
    os.close = _close_eio
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="no-follow")
    finally:
        os.close = saved_close
        _opf_store._open_dir_nofollow = saved_walk
    assert state["fired"], "the injected walker close failure must have fired"
    assert _st_open_fds() == baseline, "the walker's child descriptor must not leak"
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _t_f3_7_stale_pairing_recorded_path(d, env):
    """T-f3-7 (round 3, D4: stale pairing by the RECORDED path). A dead holder's active record whose
    [machine_store] identity differs from the recovering checkout's is recoverable when the RECORDED
    machine store is gone: (a) this checkout's machine store was destroyed and recreated (a new
    inode at the same path; the lease went with it), and (b) the sibling worktree that acquired was
    deleted outright. (c) When the recorded path cannot be examined (a symlinked ancestor, refused
    by the no-follow walk), recovery REFUSES, naming that cause, and preserves everything; the
    owning checkout then recovers normally. The sibling-still-exists refusal is T-r2-2. Before the
    fix (a) and (b) were refused forever as paired with a different machine store."""
    # (a) This checkout's machine store destroyed and recreated.
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    _st_crash_acquire(root)
    machine = os.path.join(root, ".working", "toml")
    os.rename(machine, machine + ".gone")  # the old inode stays alive until the new one exists
    os.mkdir(machine)
    shutil.copy2(os.path.join(machine + ".gone", "manifest.toml"),
                 os.path.join(machine, "manifest.toml"))
    shutil.rmtree(machine + ".gone")       # the paired lease is destroyed with the old store
    cap = acquire_operation(root, "op", recover=True)
    release_operation(cap)
    assert not os.path.exists(active) and not os.path.exists(lease), "(a) recovered"

    # (b) The acquiring sibling worktree deleted outright.
    main = _st_git_store(d, "main", env)
    main_active = os.path.join(_st_ctl_dir(main), ACTIVE_NAME)
    wt = os.path.join(d, "wt")
    _st_git(["worktree", "add", "--detach", "-q", wt], main, env)
    _st_crash_acquire(wt)
    assert os.path.exists(main_active) and os.path.exists(_st_lease_path(wt))
    shutil.rmtree(wt)
    cap = acquire_operation(main, "op", recover=True)
    release_operation(cap)
    assert not os.path.exists(main_active), "(b) recovered"

    # (c) The recorded path cannot be examined: a symlinked ancestor refuses, deleting nothing.
    wt2 = os.path.join(d, "wt2")
    _st_git(["worktree", "add", "--detach", "-q", wt2], main, env)
    _st_crash_acquire(wt2)
    os.rename(wt2, wt2 + ".real")
    os.symlink(wt2 + ".real", wt2)
    try:
        _st_expect_refusal(acquire_operation, main, "op", recover=True,
                           needle="cannot confirm whether the recorded machine store")
        assert os.path.exists(main_active), "(c) the shared active record is preserved"
        assert os.path.exists(_st_lease_path(wt2 + ".real")), "(c) the owning lease is preserved"
    finally:
        os.unlink(wt2)
        os.rename(wt2 + ".real", wt2)
    cap = acquire_operation(wt2, "op", recover=True)   # the owning checkout recovers both
    release_operation(cap)
    assert not os.path.exists(main_active) and not os.path.exists(_st_lease_path(wt2))


def _t_f3_8_empty_nodename_refuses(d, env):
    """T-f3-8 (round 3, D6): on a host whose nodename is EMPTY, acquisition refuses before any
    filesystem action (with the default and with an explicit holder), because recovery refuses an
    owner with no usable nodename and could never clear the record. Before the fix the module wrote
    nodename = "" and the record became unrecoverable."""
    root = _st_git_store(d, "repo", env)
    real = os.uname()
    fake = type(real)((real.sysname, "", real.release, real.version, real.machine))
    saved_uname = os.uname
    os.uname = lambda: fake
    try:
        _st_expect_refusal(acquire_operation, root, "op", needle="empty nodename")
        _st_expect_refusal(acquire_operation, root, "op", holder="opf:explicit-holder",
                           needle="empty nodename")
    finally:
        os.uname = saved_uname
    assert not os.path.exists(_st_ctl_dir(root)), "the refusal precedes any filesystem action"
    assert not os.path.exists(_st_lease_path(root))
    cap = acquire_operation(root, "op")
    release_operation(cap)


def _st_line_events(funcs, run):
    """The number of line events the own frames of `funcs` produce while run() executes, repeats
    counted (the sweep tests below interrupt at each one in turn)."""
    codes = set(f.__code__ for f in funcs)
    count = [0]

    def local(frame, event, arg):
        if event == "line":
            count[0] += 1
        return local

    sys.settrace(lambda frame, event, arg: local if frame.f_code in codes else None)
    try:
        run()
    finally:
        sys.settrace(None)
    return count[0]


def _st_arm_interrupt(funcs, k):
    """Arm ONE KeyboardInterrupt at the k-th line event of the own frames of `funcs` (the verifier's
    line-trace injection). Returns a state dict whose "fired" key records whether it fired; the
    caller disarms with sys.settrace(None) in a finally."""
    codes = set(f.__code__ for f in funcs)
    state = {"seen": 0, "fired": False}

    def local(frame, event, arg):
        if event == "line" and not state["fired"]:
            state["seen"] += 1
            if state["seen"] == k:
                state["fired"] = True
                sys.settrace(None)
                raise KeyboardInterrupt()
        return local

    sys.settrace(lambda frame, event, arg: local if frame.f_code in codes else None)
    return state


def _t_f4_1_failed_lease_publication(d, env):
    """T-f4-1 (fix round 4, H1: a failed publication is potentially published). In a holder child,
    the LEASE publication is (a) interrupted the instant its os.link completes, (b) interrupted the
    instant its staging unlink completes (the two syscall-completed boundaries), (c) reported as
    failed by os.link although the link was made, (d) failed at the directory fsync with its own
    cleanup unlink also failing (EIO), and (e) failed after the link with its cleanup interrupted.
    After the child is reaped, a lease must NEVER be left without its owner-bearing active record,
    and a recover=True must succeed and leave nothing behind. Before the fix every case deleted the
    active record and stranded a lone lease that every recovery refused ("no paired active
    record")."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    machine = os.path.join(root, ".working", "toml")
    lease_name = _opf_check.LEASE_NAME
    expected = {"after-link": KeyboardInterrupt, "after-staging-unlink": KeyboardInterrupt,
                "link-reported-failure": OpLockError, "fsync-and-cleanup-eio": OpLockError,
                "interrupted-cleanup": KeyboardInterrupt}
    for case in ("after-link", "after-staging-unlink", "link-reported-failure",
                 "fsync-and-cleanup-eio", "interrupted-cleanup"):
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:                      # the holder whose lease publication fails, then exits
            try:
                mod = sys.modules[__name__]
                saved_link, saved_unlink, saved_fsync = os.link, os.unlink, os.fsync

                def _unlink_then_interrupt(name, *a, **k):
                    saved_unlink(name, *a, **k)
                    if _is_staging_name(name, lease_name):
                        raise KeyboardInterrupt()

                def _link(src, dst, *a, **k):
                    saved_link(src, dst, *a, **k)
                    if dst != lease_name:
                        return
                    if case == "after-link":
                        raise KeyboardInterrupt()
                    if case == "after-staging-unlink":
                        # Installed only now: replacing os.unlink before acquire_operation would
                        # fail its containment probe (os.supports_dir_fd).
                        os.unlink = _unlink_then_interrupt
                    if case in ("link-reported-failure", "interrupted-cleanup"):
                        raise OSError(errno.EIO, "simulated link report failure (T-f4-1)")

                def _cleanup_interrupt(*a, **k):
                    raise KeyboardInterrupt()

                mst = os.stat(machine)

                def _eio_unlink(name, *a, **k):
                    if name == lease_name and k.get("dir_fd") is not None:
                        raise OSError(errno.EIO, "simulated cleanup unlink failure (T-f4-1)")
                    return saved_unlink(name, *a, **k)

                def _fsync(fd):
                    st = os.fstat(fd)
                    if (st.st_dev, st.st_ino) == (mst.st_dev, mst.st_ino) \
                            and os.path.exists(lease):
                        os.unlink = _eio_unlink
                        raise OSError(errno.EIO, "simulated lease-directory fsync failure")
                    return saved_fsync(fd)

                if case == "fsync-and-cleanup-eio":
                    os.fsync = _fsync
                else:
                    os.link = _link
                if case == "interrupted-cleanup":
                    mod._unlink_created_on_failure = _cleanup_interrupt
                try:
                    acquire_operation(root, "op")
                except expected[case]:
                    os._exit(0)
                os._exit(1)
            except BaseException:
                os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
            "the {} child must raise {} (status {})".format(case, expected[case].__name__, status)
        assert os.path.exists(active) or not os.path.exists(lease), \
            "a lease is never left without its owner-bearing active record ({})".format(case)
        cap = acquire_operation(root, "op", recover=True)   # never refused as a lone lease
        release_operation(cap)
        assert not os.path.exists(active) and not os.path.exists(lease), case
        assert not _st_staging_leftovers(machine, lease_name), case


def _t_f4_2_release_interruption_sweep(d, env):
    """T-f4-2 (fix round 4, H2: release bookkeeping). ONE KeyboardInterrupt is injected by line
    trace at EVERY line event of the release body in turn (the verifier's technique; repeats
    counted). Each time the interruption propagates as itself and then EITHER nothing changed (the
    capability is still unreleased and a retry releases it) OR the release still ended released,
    unlocked, and closed: no descriptor-count delta, the anchor free, and never a lease without its
    active record. Before the fix an interruption after the descriptor fields were cleared, or at
    the start of the sequential unlock and close code, leaked all five descriptors with the anchor
    held, and a retry refused as already released."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    mod = sys.modules[__name__]
    funcs = [release_operation] + [f for f in (getattr(mod, "_release_scoped", None),
                                                getattr(mod, "_release_legs", None)) if f]
    for func in funcs:
        cap = acquire_operation(root, "op")
        total = _st_line_events([func], lambda: release_operation(cap))
        assert total, "the sweep must see line events in {}".format(func.__name__)
        for k in range(1, total + 1):
            baseline = _st_open_fds()
            cap = acquire_operation(root, "op")
            state = _st_arm_interrupt([func], k)
            try:
                _st_expect_interrupt(release_operation, cap)
            finally:
                sys.settrace(None)
            where = "{} line event {}".format(func.__name__, k)
            assert state["fired"], where
            if not cap._released:
                release_operation(cap)     # nothing changed: a retry still releases
            assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
            assert _st_anchor_free(root), "the anchor must be unlocked ({})".format(where)
            assert os.path.exists(active) or not os.path.exists(lease), where
            for path in (lease, active):   # an interruption before the legs leaves both records
                if os.path.exists(path):
                    os.unlink(path)


def _t_f4_3_acquire_interruption_sweep(d, env):
    """T-f4-3 (fix round 4, M and H1: acquisition and publication). ONE KeyboardInterrupt is
    injected by line trace at EVERY line event of the acquisition and publication bodies in turn,
    repeats counted, including the boundary right after the staging descriptor is adopted and the
    boundaries right after the lease's os.link and staging unlink complete. Each time the
    interruption propagates as itself with no descriptor-count delta, the anchor free, and NO
    record left behind (in particular never a lone lease). Before the fix the staging descriptor
    leaked at the boundary after its adoption (M), and the link and staging-unlink boundaries of
    the lease stranded a lone lease with its active record deleted (H1).

    Fix round 5 extends the scope to the smaller helpers codex named in round 4 (the machine-store
    walk, the anchor open, the staging-leftover listing, the record read, the verified unlink, the
    recorded-store probe, and the shared no-follow walker), with a REAL signal (os.kill from the
    line trace, delivered by the interpreter through a KeyboardInterrupt handler) at every line
    event of each, in its own scenario (acquisition with the anchor absent and staging leftovers
    planted, recovery, release), in a child: zero descriptor leaks, the anchor free, nothing left
    behind, and the KeyboardInterrupt delivered after the critical section. The raise-based sweep
    stays on the bodies, where it passes; its remaining helper boundaries are the disclosed
    non-signal residual."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    mod = sys.modules[__name__]
    failure = _st_in_child(lambda: _st_f4_3_signal_helpers(d, root))
    assert failure is None, failure
    funcs = [acquire_operation, _create_control_file] \
        + [f for f in (getattr(mod, "_acquire_body", None), getattr(mod, "_publish_staged", None))
           if f]
    for func in funcs:
        caps = []
        total = _st_line_events([func], lambda: caps.append(acquire_operation(root, "op")))
        release_operation(caps[0])
        assert total, "the sweep must see line events in {}".format(func.__name__)
        for k in range(1, total + 1):
            baseline = _st_open_fds()
            state = _st_arm_interrupt([func], k)
            try:
                _st_expect_interrupt(acquire_operation, root, "op")
            finally:
                sys.settrace(None)
            where = "{} line event {}".format(func.__name__, k)
            assert state["fired"], where
            assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
            assert _st_anchor_free(root), "the anchor must be unlocked ({})".format(where)
            left = [p for p in (active, lease) if os.path.exists(p)]
            for path in left:              # cleared so the next event starts from a clean store
                os.unlink(path)
            assert not left, "no record may be left behind ({}): {}".format(where, left)


def _st_arm_signal(funcs, k):
    """Arm ONE REAL SIGINT at the k-th line event of the own frames of `funcs` (fix round 5). Unlike
    _st_arm_interrupt, the line-trace hook does not raise: it sends the signal with os.kill and
    returns normally, so the interpreter itself runs the installed handler at its next check point,
    exactly as it delivers an asynchronous Ctrl-C. Returns the state dict; the caller disarms with
    sys.settrace(None) in a finally."""
    codes = set(f.__code__ for f in funcs)
    state = {"seen": 0, "fired": False}

    def local(frame, event, arg):
        if event == "line" and not state["fired"]:
            state["seen"] += 1
            if state["seen"] == k:
                state["fired"] = True
                sys.settrace(None)
                os.kill(os.getpid(), signal.SIGINT)
        return local

    sys.settrace(lambda frame, event, arg: local if frame.f_code in codes else None)
    return state


def _st_in_child(body):
    """Run body() in a forked child, so a test's own SIGINT handler, its signal traffic, and its
    umask never touch the self-test process. Returns None when body() returned, else the failure
    the child reported (its traceback) with its wait status."""
    import traceback
    rfd, wfd = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        code = 0
        report = b""
        try:
            os.close(rfd)
            body()
        except BaseException:
            code = 1
            report = traceback.format_exc().encode("utf-8", "replace")
        try:
            _journal._write_all(wfd, report)
        except BaseException:
            pass
        os._exit(code)
    os.close(wfd)
    data = bytearray()
    while True:
        chunk = os.read(rfd, 65536)
        if not chunk:
            break
        data += chunk
    os.close(rfd)
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
        return None
    return "child wait status {}: {}".format(status, bytes(data).decode("utf-8", "replace"))


def _st_named(*names):
    """The module-level functions (or Class.method) among `names` that exist in this module, so a
    sweep list written for this round still runs against an earlier round's copy (the fail-before
    evidence), where a name introduced by this round is simply absent."""
    found = []
    g = globals()
    for name in names:
        owner, _, attr = name.partition(".")
        obj = g.get(owner)
        if obj is not None and attr:
            obj = getattr(obj, attr, None)
        if obj is not None:
            found.append(obj)
    return found


def _st_section_codes():
    """The code objects of the frames that HOLD the deferred critical sections: the acquisition body
    and the scoped release, beneath one of which every critical step runs. A signal whose handler
    runs with one of them on the stack was delivered INSIDE a critical section. On an earlier
    round's copy (no such split) the outer functions hold the sections instead."""
    held = _st_named("_acquire_body", "_release_scoped")
    if not held:
        held = [acquire_operation, release_operation]
    return set(f.__code__ for f in held)


def _st_signal_sweep(root, funcs, prepare, invoke, settle, check):
    """The real-signal sweep engine (fix round 5); run it INSIDE a child (_st_in_child). A SIGINT
    handler is installed that raises KeyboardInterrupt and records whether it ran inside a critical
    section (_st_section_codes). For each function in `funcs`, its own line events are counted on
    one clean run (repeats counted; settle() disposes of that run's result), and then, for each
    event k in turn: prepare() sets the scenario up, a REAL SIGINT is sent at event k
    (_st_arm_signal), and invoke() must raise the KeyboardInterrupt, delivered exactly once and
    OUTSIDE every critical section, with no descriptor-count delta and the anchor free; check()
    verifies the scenario's own end state. Returns the number of events swept."""
    section = _st_section_codes()
    witness = {"delivered": 0, "inside": 0}

    def handler(signum, frame):
        witness["delivered"] += 1
        f = frame
        while f is not None:
            if f.f_code in section:
                witness["inside"] += 1
                break
            f = f.f_back
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, handler)
    swept = 0
    for func in funcs:
        name = func.__qualname__
        ctx = prepare()
        results = []
        total = _st_line_events([func], lambda: results.append(invoke(ctx)))
        settle(ctx, results[0])
        assert total, "the sweep must see line events in {}".format(name)
        for k in range(1, total + 1):
            where = "{} line event {}".format(name, k)
            baseline = _st_open_fds()
            ctx = prepare()
            witness["delivered"] = witness["inside"] = 0
            state = _st_arm_signal([func], k)
            try:
                _st_expect_interrupt(invoke, ctx)
            finally:
                sys.settrace(None)
            assert state["fired"], where
            assert witness["delivered"] == 1, "the signal must be delivered exactly once ({}): " \
                "{}".format(where, witness["delivered"])
            assert witness["inside"] == 0, \
                "the signal was delivered INSIDE a critical section ({})".format(where)
            check(ctx, where)
            assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
            assert _st_anchor_free(root), "the anchor must be unlocked ({})".format(where)
            swept += 1
    return swept


def _st_no_records(root, where):
    """Assert (and then clear, so the next event starts clean) that no control record and no staging
    leftover is left at `root`."""
    ctl = _st_ctl_dir(root)
    machine = os.path.join(root, ".working", "toml")
    left = [p for p in (os.path.join(ctl, ACTIVE_NAME), _st_lease_path(root)) if os.path.exists(p)]
    left += [os.path.join(ctl, e) for e in _st_staging_leftovers(ctl, ACTIVE_NAME)]
    left += [os.path.join(machine, e) for e in _st_staging_leftovers(machine, _opf_check.LEASE_NAME)]
    for path in left:
        os.unlink(path)
    assert not left, "nothing may be left behind ({}): {}".format(where, left)


def _st_acquire_scenario(root, drop_anchor=False, plant_staging=False):
    """The acquisition scenario for _st_signal_sweep: invoke acquires (the delivered signal must
    then release the unreturned capability), optionally with the anchor absent (its creation path)
    and a staging leftover planted for each record (the leftover-removal loop)."""
    ctl = _st_ctl_dir(root)
    machine = os.path.join(root, ".working", "toml")

    def prepare():
        if drop_anchor and os.path.exists(os.path.join(ctl, ANCHOR_NAME)):
            os.unlink(os.path.join(ctl, ANCHOR_NAME))
        if plant_staging:
            for where, name in ((ctl, ACTIVE_NAME), (machine, _opf_check.LEASE_NAME)):
                with open(os.path.join(where, _staging_name(name)), "wb") as fh:
                    fh.write(b"leftover")
        return None

    def invoke(ctx):
        return acquire_operation(root, "op")

    def settle(ctx, cap):
        release_operation(cap)

    def check(ctx, where):
        _st_no_records(root, where)

    return prepare, invoke, settle, check


def _st_release_scenario(root):
    """The release scenario for _st_signal_sweep: prepare acquires, invoke releases; the release
    must end released with both records removed before the signal is delivered."""
    def prepare():
        return acquire_operation(root, "op")

    def invoke(cap):
        release_operation(cap)

    def settle(cap, result):
        pass

    def check(cap, where):
        assert cap._released, "the release must have ended released ({})".format(where)
        _st_no_records(root, where)

    return prepare, invoke, settle, check


def _st_recovery_scenario(root, gone_machine_path):
    """The recovery scenario for _st_signal_sweep: prepare leaves a confirmed-dead holder's paired
    records whose [machine_store] names a DIFFERENT identity at a path that no longer exists (so the
    recorded-store probe and the shared walker run), and invoke acquires with recover=True; the
    stale records must be recovered and the new capability released before the signal is
    delivered."""
    dead_pid, dead_start = _st_reaped_child()
    node = os.uname().nodename
    holder = "opf:stale-holder"
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)

    def prepare():
        lines = ("schema = {}".format(_ACTIVE_SCHEMA), 'op_id = "stale-op-id"',
                 'holder = "{}"'.format(holder), 'operation = "recovered-op"',
                 'acquired_at = "2026-01-01T00:00:00Z"', "", "[owner]",
                 "pid = {}".format(dead_pid), "uid = {}".format(os.getuid()),
                 'nodename = "{}"'.format(node), 'session = "stale-op-id"',
                 'utc = "2026-01-01T00:00:00Z"', 'pid-start = "{}"'.format(dead_start), "",
                 "[machine_store]", 'path = "{}"'.format(gone_machine_path), 'dev = "1"',
                 'ino = "1"', "")
        with open(active, "w", encoding="utf-8") as fh:
            fh.write(chr(10).join(lines))
        _st_write_lease_owned(root, holder=holder)
        return None

    def invoke(ctx):
        return acquire_operation(root, "op", recover=True)

    def settle(ctx, cap):
        release_operation(cap)

    def check(ctx, where):
        _st_no_records(root, where)

    return prepare, invoke, settle, check


def _st_f4_3_signal_helpers(d, root):
    """T-f4-3's real-signal extension (fix round 5), run in a child: every line event of each helper
    codex named in round 4, in the scenario that reaches it."""
    gone = os.path.join(d, "recorded-machine-store-gone")
    swept = _st_signal_sweep(root, _st_named("_open_machine_dir", "_open_anchor",
                                             "_remove_staging_garbage"),
                             *_st_acquire_scenario(root, drop_anchor=True, plant_staging=True))
    swept += _st_signal_sweep(root, [_opf_store._open_dir_nofollow],
                              *_st_acquire_scenario(root))
    swept += _st_signal_sweep(root, _st_named("_read_control_record",
                                              "_recorded_machine_store_present"),
                              *_st_recovery_scenario(root, gone))
    swept += _st_signal_sweep(root, _st_named("_verified_unlink"), *_st_release_scenario(root))
    assert swept, "the helper sweep must have swept line events"


def _t_f5_1_signal_acquisition(d, env):
    """T-f5-1 (fix round 5: signals are DEFERRED across the acquisition). In a child whose SIGINT
    handler raises KeyboardInterrupt, a REAL SIGINT is sent (os.kill from a line trace, delivered by
    the interpreter) at every line event of the acquisition body, the publication, and every helper
    they call on the clean path, repeats counted. Each time the KeyboardInterrupt must be delivered
    exactly once and only AFTER the critical section (no critical frame on the handler's stack), with
    the fully formed capability released, no descriptor leaked, the anchor free, and no record or
    staging leftover left. Before the fix the handler ran inside the section at the first event."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    funcs = _st_named("_acquire_body", "_create_control_file", "_publish_staged",
                      "_set_record_mode", "_open_path_dir_nofollow", "_fstat_or_refuse",
                      "_classify_git_entry", "_git_common_dir", "_open_control_dir", "_open_dir_at",
                      "_validate_ctl_dir_fd", "_validate_file_fd", "_flock_exclusive",
                      "_post_lock_anchor_check", "_lstat_at", "_classify_stale", "_control_payload",
                      "_machine_store_table", "_handoff",
                      "_gated_step") + [_opf_store.resolve_store]
    if not _st_named("_acquire_body"):
        funcs.insert(0, acquire_operation)  # an earlier round's copy: the body is the outer function
    failure = _st_in_child(lambda: _st_signal_sweep(root, funcs, *_st_acquire_scenario(root)))
    assert failure is None, failure


def _t_f5_2_signal_release(d, env):
    """T-f5-2 (fix round 5: signals are DEFERRED across the release). As T-f5-1, over every line
    event of the scoped release, its legs, the release scope's exit, and the verified unlink's
    checks: the KeyboardInterrupt is delivered exactly once, only after the release ended released
    with both records removed, no descriptor leaked, and the anchor free."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    funcs = _st_named("_release_scoped", "_release_legs", "_ReleaseScope.__exit__",
                      "_ReleaseScope.take_ownership", "_ReleaseScope._claim_locked",
                      "_verified_unlink_verify",
                      "_FdOwner.close_all", "_FdOwner.adopt_all", "_FdOwner.close_guarded",
                      "_FdOwner.close")
    if not _st_named("_release_scoped"):
        funcs.insert(0, release_operation)  # an earlier round's copy: the scope is in the outer one
    failure = _st_in_child(lambda: _st_signal_sweep(root, funcs, *_st_release_scenario(root)))
    assert failure is None, failure


def _t_f5_3_signal_recovery(d, env):
    """T-f5-3 (fix round 5: signals are DEFERRED across recovery). As T-f5-1, under recover=True
    over a confirmed-dead holder's paired records whose recorded machine store is gone, at every
    line event of the liveness gate, the record read, the schema validation, the recorded-store
    probe (and the shared walker it uses), and the verified recovery deletes: the KeyboardInterrupt
    is delivered exactly once, after the section, with the stale records recovered, the new
    capability released, no descriptor leaked, and the anchor free."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    gone = os.path.join(d, "recorded-machine-store-gone")
    funcs = _st_named("_require_holder_confirmed_dead", "_read_control_record",
                      "_validate_recovery_active", "_validate_recovery_lease",
                      "_recorded_machine_store_present", "_recover_stale", "_recover_stale_verify")
    funcs.append(_opf_store._open_dir_nofollow)
    if not _st_named("_acquire_body"):
        funcs.insert(0, acquire_operation)  # an earlier round's copy: recovery is in the outer one
    failure = _st_in_child(lambda: _st_signal_sweep(root, funcs,
                                                    *_st_recovery_scenario(root, gone)))
    assert failure is None, failure


class _StSkip(Exception):
    """A test that cannot establish its precondition on this host: reported as SKIP with the reason,
    never as a PASS, and counted in the suite summary."""


def _st_honours_umask(parent):
    """Whether objects created in `parent` take their mode from the process umask: under umask
    0o777 a new directory and a new file must both come out mode 0000. A default ACL on `parent`
    overrides the umask (the ACL's mask applies instead), so it answers False there."""
    probe = os.path.join(parent, "umask-probe-{}".format(uuid.uuid4().hex))
    old = os.umask(0o777)
    try:
        os.mkdir(probe, 0o777)
    finally:
        os.umask(old)
    try:
        dir_mode = stat.S_IMODE(os.lstat(probe).st_mode)
        os.chmod(probe, 0o700)
        old = os.umask(0o777)
        try:
            fd = os.open(os.path.join(probe, "f"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        finally:
            os.umask(old)
        os.close(fd)
        file_mode = stat.S_IMODE(os.lstat(os.path.join(probe, "f")).st_mode)
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    return dir_mode == 0 and file_mode == 0


def _st_umask_fixture_root(d, body):
    """Run body(root) with `root` a fixture directory whose new objects honour the process umask
    (test hermeticity: an inherited default ACL, as on a TMPDIR carrying one, overrides the umask
    and would silently change what a restrictive-umask witness observes). `d` is tried first, then
    a fresh directory under /dev/shm (removed afterwards); when neither honours the umask the test
    is SKIPPED with the reason, never passed."""
    if _st_honours_umask(d):
        return body(d)
    tried = [d]
    if os.path.isdir("/dev/shm"):
        import tempfile
        shm = os.path.realpath(tempfile.mkdtemp(prefix="opf-oplock-umask-", dir="/dev/shm"))
        try:
            if _st_honours_umask(shm):
                return body(shm)
            tried.append(shm)
        finally:
            shutil.rmtree(shm, ignore_errors=True)
    else:
        tried.append("/dev/shm (absent)")
    raise _StSkip("no fixture root honours the process umask (a default ACL overrides it); "
                  "tried: {}".format(", ".join(tried)))


def _t_f5_4_restrictive_umask(d, env):
    """T-f5-4's fixtures live under a root that honours the umask (_st_umask_fixture_root)."""
    return _st_umask_fixture_root(d, lambda root: _t_f5_4_body(root, env))


def _t_f5_4_body(d, env):
    """T-f5-4 (fix round 5, codex MEDIUM 3: publication under a restrictive umask). Under umask
    0o444 and under 0o777, in a child: (a) a holder acquires and is SIGKILLed, and a recover=True
    from a normal process must then succeed; (b) a holder acquires and releases normally, which must
    succeed; (c) on a FRESH store, the first-ever acquisition (creating the control directory and
    the anchor) and its release must succeed, and a normal process must then acquire and release.
    Before the fix the records were published mode 0200 (the umask filtered the requested 0o644):
    recovery refused them as unreadable, and release could not reopen the lease."""
    root = _st_git_store(d, "repo", env)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    for mask in (0o444, 0o777):
        label = "umask {:o}".format(mask)
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:                      # (a) the crashing holder
            try:
                os.umask(mask)
                acquire_operation(root, "crashed-op")
                os.kill(os.getpid(), signal.SIGKILL)
            except BaseException:
                pass
            os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, \
            "the holder must acquire and die by SIGKILL ({}, status {})".format(label, status)
        assert os.path.exists(active) and os.path.exists(lease), label
        for path in (active, lease):              # the mode every control record is created with
            mode = stat.S_IMODE(os.stat(path).st_mode)
            assert mode == 0o644, "{} has mode {:o}, not 644 ({})".format(path, mode, label)
        cap = acquire_operation(root, "op", recover=True)   # (a) recovery must succeed
        release_operation(cap)
        assert not os.path.exists(active) and not os.path.exists(lease), label

        def _acquire_release(target, m=mask):
            os.umask(m)
            release_operation(acquire_operation(target, "op"))

        failure = _st_in_child(lambda: _acquire_release(root))          # (b) normal release
        assert failure is None, "{} (b): {}".format(label, failure)
        assert not os.path.exists(active) and not os.path.exists(lease), label
        fresh = _st_git_store(d, "fresh-{:o}".format(mask), env)
        failure = _st_in_child(lambda: _acquire_release(fresh))         # (c) first-ever acquisition
        assert failure is None, "{} (c): {}".format(label, failure)
        cap = acquire_operation(fresh, "op")
        release_operation(cap)


def _st_kill_when_present(path):
    """Arm a line trace over EVERY frame that SIGKILLs this process at the first line event at which
    `path` exists (no-follow): the first interpreter step after the syscall that created it (fix
    round 6). Run it only in a child."""
    def local(frame, event, arg):
        if event == "line" and os.path.lexists(path):
            os.kill(os.getpid(), signal.SIGKILL)
        return local

    sys.settrace(lambda frame, event, arg: local)


def _t_f6_1_restartable_init(d, env):
    """T-f6-1's fixtures live under a root that honours the umask (_st_umask_fixture_root)."""
    return _st_umask_fixture_root(d, lambda root: _t_f6_1_body(root, env))


def _t_f6_1_body(d, env):
    """T-f6-1 (fix round 6, codex MEDIUM 1 and claude LOW: restartable initialization). Under umask
    0o444 and under 0o777, a first acquisition is SIGKILLed at the first interpreter step after the
    syscall that CREATES (a) the control directory and (b) the mutex anchor, before its mode is
    corrected, leaving an object whose owner permissions the umask masked (0311 or 0000 for the
    directory, 0200 or 0000 for the anchor). A recover=True acquisition, under the same umask in a
    child and then in a normal process, must SUCCEED, and the object must end at its intended mode
    (0755, 0644). Before the fix every later acquisition refused it forever ("Permission denied").
    The repair is bounded: a mode carrying bits beyond the intended one, or a hard-linked anchor, is
    not what an interrupted creation leaves and is refused unchanged; a symlink is never repaired;
    and a real signal at every line of the repair is delivered after the section."""
    for mask in (0o444, 0o777):
        for boundary in ("control directory", "anchor"):
            label = "{} under umask {:o}".format(boundary, mask)
            root = _st_git_store(d, "r-{}-{:o}".format(boundary.split()[-1], mask), env)
            ctl = _st_ctl_dir(root)
            target = ctl
            needed = stat.S_IRWXU
            intended = _CONTROL_DIR_MODE
            if boundary == "anchor":
                cap = acquire_operation(root, "op")    # the control directory exists, intact
                release_operation(cap)
                target = os.path.join(ctl, ANCHOR_NAME)
                os.unlink(target)
                needed = stat.S_IRUSR | stat.S_IWUSR
                intended = _CONTROL_FILE_MODE
            sys.stdout.flush()
            sys.stderr.flush()
            pid = os.fork()
            if pid == 0:                  # the first acquirer, killed inside the creation window
                try:
                    os.umask(mask)
                    _st_kill_when_present(target)
                    acquire_operation(root, "op")
                except BaseException:
                    pass
                os._exit(3)
            _, status = os.waitpid(pid, 0)
            assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, \
                "the acquirer must die by SIGKILL ({}, status {})".format(label, status)
            left = stat.S_IMODE(os.lstat(target).st_mode)
            assert (left & needed) != needed, \
                "the kill must land before the mode is corrected ({}: mode {:o})".format(
                    label, left)

            def _recover(m=mask):
                os.umask(m)
                release_operation(acquire_operation(root, "op", recover=True))

            failure = _st_in_child(_recover)
            assert failure is None, "{}: recovery under the umask: {}".format(label, failure)
            cap = acquire_operation(root, "op", recover=True)
            release_operation(cap)
            mode = stat.S_IMODE(os.lstat(target).st_mode)
            assert mode == intended, "{} left at mode {:o}, not {:o}".format(label, mode, intended)
    # The repair's bounds, on the last store: never loosen past the intended mode, never a
    # hard-linked anchor, never a symlink.
    anchor = os.path.join(ctl, ANCHOR_NAME)
    for bad in (0o020, 0o100):
        os.chmod(anchor, bad)
        _st_expect_refusal(acquire_operation, root, "op", needle="interrupted creation")
        assert stat.S_IMODE(os.lstat(anchor).st_mode) == bad, "a refused anchor is left unchanged"
    os.chmod(ctl, 0o070)
    _st_expect_refusal(acquire_operation, root, "op", needle="interrupted creation")
    assert stat.S_IMODE(os.lstat(ctl).st_mode) == 0o070, "a refused directory is left unchanged"
    os.chmod(ctl, 0o755)
    os.chmod(anchor, 0)
    os.link(anchor, os.path.join(ctl, "pinning-link"))
    _st_expect_refusal(acquire_operation, root, "op", needle="interrupted creation")
    assert stat.S_IMODE(os.lstat(anchor).st_mode) == 0, "a hard-linked anchor is never repaired"
    os.unlink(os.path.join(ctl, "pinning-link"))
    victim = os.path.join(d, "symlink-victim")
    with open(victim, "wb"):
        pass
    os.chmod(victim, 0)
    os.unlink(anchor)
    os.symlink(victim, anchor)
    _st_expect_refusal(acquire_operation, root, "op", needle="symlink")
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0, "a symlink target is never repaired"
    os.unlink(anchor)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    # A real signal at every line of the repair (anchor 0200, then the directory 0311) is delivered
    # after the acquisition, which repaired the object and released the unreturned capability.
    for path, bad, good in ((anchor, 0o200, _CONTROL_FILE_MODE), (ctl, 0o311, _CONTROL_DIR_MODE)):
        prepare, invoke, settle, check = _st_acquire_scenario(root)

        def _prepare(p=path, b=bad):
            os.chmod(p, b)
            return prepare()

        def _check(ctx, where, p=path, g=good):
            check(ctx, where)
            assert stat.S_IMODE(os.lstat(p).st_mode) == g, "not repaired ({})".format(where)

        funcs = _st_named("_repair_restrictive_mode", "_chmod_bound")
        if funcs:
            failure = _st_in_child(lambda: _st_signal_sweep(root, funcs, _prepare, invoke, settle,
                                                            _check))
            assert failure is None, failure


def _t_f6_2_unreturned_release(d, env):
    """T-f6-2 (fix round 6, codex MEDIUM 2: the unreturned capability's release). In a child, a REAL
    SIGINT sent during the publication is deferred and delivered after the acquisition built its
    capability; its handler first arms a fault, then raises KeyboardInterrupt. (A) An EIO on every
    /proc read (the acquirer-identity read release_operation makes, which reads it as a differing
    start time): the capability must still be released, with ZERO descriptor delta, the anchor free,
    both records removed, and a note that says so. Before the fix the public release refused before
    taking ownership, leaking five descriptors with the anchor held, while the note claimed the
    capability "was released". (B) The lease leg fails: the note must say released WITH failures,
    the anchor free and nothing leaked, the owner-bearing records kept. (C) The release is
    interrupted before it takes ownership: the note must say NOT released."""
    root = _st_git_store(d, "repo", env)
    cap = acquire_operation(root, "op")
    release_operation(cap)
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    failure = _st_in_child(lambda: _st_f6_2_body(root, active, lease))
    assert failure is None, failure


def _st_f6_2_body(root, active, lease):
    """T-f6-2's body, run in a child (it installs its own SIGINT handler)."""
    mod = sys.modules[__name__]
    armed = {"fault": None}
    real_open = open
    saved_unlink = mod._verified_unlink
    saved_scoped = getattr(mod, "_release_scoped", None)
    held = []

    def _proc_open(path, *a, **k):
        if armed["fault"] == "proc-eio" and str(path).startswith("/proc/"):
            raise OSError(errno.EIO, "simulated /proc read failure (T-f6-2)")
        return real_open(path, *a, **k)

    def _lease_leg_fails(dir_fd, name, *a, **k):
        if armed["fault"] == "lease-leg" and name == _opf_check.LEASE_NAME:
            raise OpLockError("cannot unlink lease (simulated, T-f6-2)")
        return saved_unlink(dir_fd, name, *a, **k)

    def _interrupted_scope(cap, *a):
        if armed["fault"] == "before-ownership":
            held.append(cap)
            raise KeyboardInterrupt()
        return saved_scoped(cap, *a)

    def handler(signum, frame):
        armed["fault"] = armed.get("next")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, handler)
    _journal.open = _proc_open
    mod._verified_unlink = _lease_leg_fails
    if saved_scoped is not None:
        mod._release_scoped = _interrupted_scope
    try:
        for case in ("proc-eio", "lease-leg", "before-ownership"):
            armed["fault"] = None
            armed["next"] = case
            baseline = _st_open_fds()
            state = _st_arm_signal(_st_named("_publish_staged"), 1)
            try:
                caught = _st_expect_interrupt(acquire_operation, root, "op")
            finally:
                sys.settrace(None)
            armed["fault"] = None
            assert state["fired"], case
            notes = " ".join(getattr(caught, "__notes__", ()))
            if case == "proc-eio":
                assert _st_open_fds() == baseline, "no descriptor may leak ({}): {}".format(
                    case, notes)
                assert _st_anchor_free(root), "the anchor must be unlocked ({})".format(case)
                assert not os.path.exists(active) and not os.path.exists(lease), case
                assert "was released: its records removed, its anchor descriptor closed " \
                    "(releasing its hold on the lock), and its descriptors closed" in notes, notes
            elif case == "lease-leg":
                assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(case)
                assert _st_anchor_free(root), "the anchor must be unlocked ({})".format(case)
                assert os.path.exists(active) and os.path.exists(lease), \
                    "a lease the release could not remove keeps its active record ({})".format(case)
                # Fix round 7: the note is derived from the recorded steps (no record removed,
                # anchor descriptor closed, descriptors closed), never a fixed "with failures" text.
                assert "released only in part: neither of its records removed" in notes \
                    and "its anchor descriptor closed (releasing its hold on the lock), and its " \
                    "descriptors closed" in notes, notes
                os.unlink(lease)          # this (live) process's records: cleared by hand
                os.unlink(active)
            else:
                assert "was NOT released" in notes, notes
                assert held and not held[0]._released, "the capability was never released"
                saved_scoped(held.pop())  # the test's own cleanup of the unreleased capability
                assert _st_open_fds() == baseline, "cleanup ({})".format(case)
                assert not os.path.exists(active) and not os.path.exists(lease), case
    finally:
        del _journal.open
        mod._verified_unlink = saved_unlink
        if saved_scoped is not None:
            mod._release_scoped = saved_scoped


def _st_state(root):
    """The acquirer-visible lock state a forked child must never change (fix round 7): whether the
    anchor's flock is free, whether each control record exists, and the staging leftovers."""
    ctl = _st_ctl_dir(root)
    machine = os.path.join(root, ".working", "toml")
    return (_st_anchor_free(root), os.path.exists(os.path.join(ctl, ACTIVE_NAME)),
            os.path.exists(_st_lease_path(root)), tuple(_st_staging_leftovers(ctl, ACTIVE_NAME)),
            tuple(_st_staging_leftovers(machine, _opf_check.LEASE_NAME)))


def _st_forking_handler(root, record):
    """Install a SIGINT handler that FORKS (fix round 7, the codex probe). In the child the handler
    raises KeyboardInterrupt, so the child runs whatever cleanup the interrupted code has, and must
    then leave through _st_forked_exit. In the parent the handler waits for the child to exit,
    appends (the lock state before the fork, the lock state after the child exited, the child's
    wait status, the note the child reported) to record["events"], and returns normally, so the
    parent's own operation continues as if no signal had come."""
    def handler(signum, frame):
        before = _st_state(root)
        rfd, wfd = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            os.close(rfd)
            record["wfd"] = wfd
            raise KeyboardInterrupt()
        os.close(wfd)
        data = bytearray()
        while True:
            chunk = os.read(rfd, 65536)
            if not chunk:
                break
            data += chunk
        os.close(rfd)
        _, status = os.waitpid(pid, 0)
        record["events"].append((before, _st_state(root), status,
                                 bytes(data).decode("utf-8", "replace")))

    signal.signal(signal.SIGINT, handler)


def _st_forked_exit(top, record, fn, *args):
    """Run fn(*args) in the process `top`; a forked child (any other pid) that the handler's
    KeyboardInterrupt reaches here reports that interruption's notes to its parent and exits 0 (any
    other exception in the child exits 5), so a child never runs the test's own code further."""
    try:
        return fn(*args)
    except KeyboardInterrupt as exc:
        if os.getpid() != top:
            try:
                _journal._write_all(record["wfd"], " ".join(getattr(exc, "__notes__", ())).encode(
                    "utf-8", "replace"))
            except BaseException:
                os._exit(4)
            os._exit(0)
        raise
    except BaseException:
        if os.getpid() != top:
            os._exit(5)
        raise


def _st_forked_event(record, where):
    """The single fork event of one attempt, asserted clean: the child exited 0 and changed none of
    the acquirer's lock state. Returns the child's note."""
    assert len(record["events"]) == 1, "exactly one fork ({}): {}".format(where, record["events"])
    before, after, status, note = record["events"][0]
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "the forked child must end through its interruption ({}, status {}): {}".format(
            where, status, note)
    assert before == after, "the forked child changed the acquirer's lock state ({}): {} -> {}; " \
        "its note: {}".format(where, before, after, note)
    return note


def _t_f7_1_forked_unreturned(d, env):
    """T-f7-1 (fix round 7, codex HIGH, a round-6 regression: the unreturned release in a forked
    child). In a child, a REAL SIGINT sent during the publication is deferred and delivered after
    the capability is built; its handler FORKS. The parent's handler returns normally, so the parent
    receives the capability; the forked child's KeyboardInterrupt reaches the unreturned-capability
    release. The child must close ONLY its inherited descriptor copies: the parent's lock state is
    unchanged across the child's life (anchor held, both records present), a second acquisition is
    refused as contention, the child's note says it removed no record and did not unlock the anchor,
    and the parent then releases cleanly with no descriptor delta. Before the fix the child deleted
    the parent's records and unlocked the shared open file description, and a second acquisition
    SUCCEEDED while the parent still held its capability. The public release's fork refusal is
    T-c9."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child(lambda: _st_f7_1_body(root))
    assert failure is None, failure


def _st_f7_1_body(root):
    """T-f7-1's body, run in a child (it installs its own SIGINT handler)."""
    top = os.getpid()
    record = {"events": []}
    _st_forking_handler(root, record)
    baseline = _st_open_fds()
    state = _st_arm_signal(_st_named("_publish_staged"), 1)
    try:
        cap = _st_forked_exit(top, record, acquire_operation, root, "op")
    finally:
        sys.settrace(None)
    assert state["fired"], "the signal must have been sent"
    note = _st_forked_event(record, "T-f7-1")
    assert "forked child" in note and "removed no record and did not unlock the anchor" in note \
        and "it closed only its 5 inherited descriptor copies" in note, note
    assert not cap._released, "the parent's capability is its own, unreleased"
    assert not _st_anchor_free(root), "the parent's anchor lock must still be held"
    assert os.path.exists(os.path.join(_st_ctl_dir(root), ACTIVE_NAME)) \
        and os.path.exists(_st_lease_path(root)), "the parent's records must be intact"
    _st_expect_refusal(acquire_operation, root, "op2", needle="held")
    release_operation(cap)
    assert _st_open_fds() == baseline, "no descriptor may leak"
    assert _st_anchor_free(root), "the parent's release frees the anchor"
    _st_no_records(root, "T-f7-1")


def _t_f7_2_forked_cleanup_sweep(d, env):
    """T-f7-2 (fix round 7, the class check behind codex HIGH: every release or unwind path a forked
    child can reach). With the deferral disabled in a child (as on a thread or platform where it is
    a no-op), a SIGINT whose handler FORKS is sent at every line event of the acquisition (the
    public entry, its body, the publication) and of the release (the public entry, the scoped
    release, its legs, the release scope's exit). The forked child runs whichever cleanup the
    interruption reaches (the acquisition unwind, the publication's failure cleanup, the unreturned
    release, the release legs and scope exit) and must change NONE of the acquirer's lock state (the
    anchor's flock, the records, the staging names); the parent's own acquisition or release must
    complete normally, with no descriptor delta. Before the fix the child's unwind and scope exit
    unlocked the shared open file description and removed the parent's records."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child(lambda: _st_f7_2_body(root))
    assert failure is None, failure


def _st_f7_2_body(root):
    """T-f7-2's body, run in a child (it disables the deferral and installs its own handler)."""
    mod = sys.modules[__name__]
    mod._deferrable_signals = lambda: ()   # this child only: no deferral stands
    top = os.getpid()
    record = {"events": []}
    _st_forking_handler(root, record)
    swept = 0
    for release, funcs in ((False, _st_named("acquire_operation", "_acquire_body",
                                             "_publish_staged")),
                           (True, _st_named("release_operation", "_release_scoped",
                                            "_release_legs", "_ReleaseScope.__exit__"))):
        for func in funcs:
            if release:
                held = acquire_operation(root, "op")
                total = _st_line_events([func], lambda: release_operation(held))
            else:
                caps = []
                total = _st_line_events([func], lambda: caps.append(acquire_operation(root, "op")))
                release_operation(caps[0])
            assert total, "the sweep must see line events in {}".format(func.__qualname__)
            for k in range(1, total + 1):
                where = "{} line event {}".format(func.__qualname__, k)
                baseline = _st_open_fds()
                cap = acquire_operation(root, "op") if release else None
                record["events"] = []
                state = _st_arm_signal([func], k)
                try:
                    if release:
                        _st_forked_exit(top, record, release_operation, cap)
                    else:
                        cap = _st_forked_exit(top, record, acquire_operation, root, "op")
                finally:
                    sys.settrace(None)
                assert state["fired"], where
                _st_forked_event(record, where)
                if not release:
                    assert not cap._released and not _st_anchor_free(root), \
                        "the parent's acquisition must hold its lock ({})".format(where)
                    release_operation(cap)
                assert cap._released, where
                assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
                assert _st_anchor_free(root), "the anchor must end free ({})".format(where)
                _st_no_records(root, where)
                swept += 1
    assert swept, "the fork sweep must have swept line events"


def _t_f7_3_setgid_restartable(d, env):
    """T-f7-3's fixtures live under a root that honours the umask (_st_umask_fixture_root)."""
    return _st_umask_fixture_root(d, lambda root: _t_f7_3_body(root, env))


def _t_f7_3_body(d, env):
    """T-f7-3 (fix round 7, codex MEDIUM: restartable initialization beneath a set-group-ID
    parent). With the repository's .git at mode 02755, mkdir of the control directory inherits the
    S_ISGID bit. Under umask 0o444 and 0o777 a first acquisition is SIGKILLed at the first step
    after creating (a) the control directory (left 02311 or 02000) and (b) the anchor; recover=True
    under the umask in a child and then normally must SUCCEED, ending the directory at 02755 (the
    inherited bit preserved) and the anchor at 0644. A fresh first acquisition under each umask (no
    crash) must also succeed. Bounds: an S_ISGID control directory whose parent is NOT set-group-ID,
    or whose group is not the parent's, is not what an interrupted creation leaves and is refused
    unchanged. Before the fix every case (a) and every fresh acquisition refused ("mode 2311 ... is
    not what an interrupted creation leaves"). Fix round 8 (claude LOW): the group-mismatch bound
    runs on EVERY host, a single-group host included: where the process belongs to another group
    the directory is really chowned to it, and in every case the repair is also handed the real
    directory's no-follow stat with only its group replaced by one that is not the parent's, so
    the bound can never pass silently unexercised."""
    dir_mode = _CONTROL_DIR_MODE | stat.S_ISGID
    for mask in (0o444, 0o777):
        for boundary in ("control directory", "anchor"):
            label = "{} under umask {:o}, set-group-ID parent".format(boundary, mask)
            root = _st_git_store(d, "g-{}-{:o}".format(boundary.split()[-1], mask), env)
            git_dir = os.path.join(root, ".git")
            os.chmod(git_dir, 0o2755)
            if not os.lstat(git_dir).st_mode & stat.S_ISGID:
                raise _StSkip("the fixture cannot set S_ISGID on its .git directory")
            ctl = _st_ctl_dir(root)
            target, needed, intended = ctl, stat.S_IRWXU, dir_mode
            if boundary == "anchor":
                release_operation(acquire_operation(root, "op"))
                target = os.path.join(ctl, ANCHOR_NAME)
                os.unlink(target)
                needed, intended = stat.S_IRUSR | stat.S_IWUSR, _CONTROL_FILE_MODE
            sys.stdout.flush()
            sys.stderr.flush()
            pid = os.fork()
            if pid == 0:                  # the first acquirer, killed inside the creation window
                try:
                    os.umask(mask)
                    _st_kill_when_present(target)
                    acquire_operation(root, "op")
                except BaseException:
                    pass
                os._exit(3)
            _, status = os.waitpid(pid, 0)
            assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL, \
                "the acquirer must die by SIGKILL ({}, status {})".format(label, status)
            left = os.lstat(target).st_mode
            assert (left & needed) != needed, \
                "the kill must land before the mode is corrected ({}: mode {:o})".format(
                    label, stat.S_IMODE(left))
            if boundary == "control directory":
                assert left & stat.S_ISGID, "mkdir must inherit S_ISGID ({})".format(label)

            def _recover(m=mask, r=root):
                os.umask(m)
                release_operation(acquire_operation(r, "op", recover=True))

            failure = _st_in_child(_recover)
            assert failure is None, "{}: recovery under the umask: {}".format(label, failure)
            release_operation(acquire_operation(root, "op", recover=True))
            mode = stat.S_IMODE(os.lstat(target).st_mode)
            assert mode == intended, "{} left at mode {:o}, not {:o}".format(label, mode, intended)
    for mask in (0o444, 0o777):
        fresh = _st_git_store(d, "gfresh-{:o}".format(mask), env)
        os.chmod(os.path.join(fresh, ".git"), 0o2755)

        def _first(m=mask, r=fresh):
            os.umask(m)
            release_operation(acquire_operation(r, "op"))

        failure = _st_in_child(_first)
        assert failure is None, "fresh acquisition under umask {:o}: {}".format(mask, failure)
        mode = stat.S_IMODE(os.lstat(_st_ctl_dir(fresh)).st_mode)
        assert mode == dir_mode, "fresh control directory at {:o}, not {:o}".format(mode, dir_mode)
    # Bounds, on the last store: S_ISGID is accepted only as mkdir's inheritance.
    git_dir = os.path.join(fresh, ".git")
    ctl = _st_ctl_dir(fresh)
    os.chmod(git_dir, 0o755)              # the parent is no longer set-group-ID
    os.chmod(ctl, 0o2311)
    _st_expect_refusal(acquire_operation, fresh, "op", needle="interrupted creation")
    assert stat.S_IMODE(os.lstat(ctl).st_mode) == 0o2311, "a refused directory is left unchanged"
    os.chmod(git_dir, 0o2755)
    own_gid = os.lstat(ctl).st_gid
    others = [g for g in os.getgroups() if g != os.lstat(git_dir).st_gid]
    if others:                            # a group that is not the parent's: not inheritance
        os.chown(ctl, -1, others[0])
        os.chmod(ctl, 0o2311)
        _st_expect_refusal(acquire_operation, fresh, "op", needle="interrupted creation")
        assert stat.S_IMODE(os.lstat(ctl).st_mode) == 0o2311, "a refused directory is unchanged"
        os.chown(ctl, -1, own_gid)
    # The same bound on every host (fix round 8): the stat of the real 02311 directory, with only
    # its group replaced by one that is not the parent's. A positive control first shows that the
    # real stat (the parent's group) IS read as inheritance, so the refusal below is the group
    # check.
    os.chmod(ctl, 0o2311)
    real = os.lstat(ctl)
    parent_gid = os.lstat(git_dir).st_gid
    fields = tuple(real)
    forged = os.stat_result(fields[:stat.ST_GID] + (parent_gid + 1,) + fields[stat.ST_GID + 1:])
    git_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        assert real.st_gid == parent_gid and _inherits_setgid(git_fd, "control directory", real), \
            "the real directory carries the parent's group and inherits its bit"
        assert not _inherits_setgid(git_fd, "control directory", forged), \
            "a directory whose group is not the parent's never inherits the bit"
        _st_expect_refusal(_repair_restrictive_mode, git_fd, CONTROL_DIRNAME, "control directory",
                           forged, True, needle="interrupted creation")
    finally:
        os.close(git_fd)
    assert stat.S_IMODE(os.lstat(ctl).st_mode) == 0o2311, "a refused directory is unchanged"
    release_operation(acquire_operation(fresh, "op"))
    assert stat.S_IMODE(os.lstat(ctl).st_mode) == dir_mode, "repaired with the inherited bit"


def _t_f7_4_unreturned_note_truth(d, env):
    """T-f7-4 (fix round 7, codex and claude LOW: the unreturned release's note derives from the
    observed state; extended in fix round 9). In a child per case, a REAL SIGINT sent during the
    publication is deferred and delivered after the capability is built; its handler arms a fault,
    then raises KeyboardInterrupt. (A) An interruption at the release scope's exit ENTRY: the
    records were removed but no close ran (five descriptors retained, the anchor held), so the note
    must say the lock's release is NOT confirmed and the closes NOT completed, never that a
    descriptor closed. (B) The anchor descriptor's close reports a failure (fix round 9: the lock is
    given up by that close, never by LOCK_UN): both records are removed, so the note must say so
    and that the lock's release is NOT confirmed, never that a record is kept. (C) The lease leg
    fails: the note must say neither record was removed, with the anchor descriptor and the
    descriptors closed. (D) Fix round 9 (codex LOW): the close of a record descriptor is
    interrupted after ownership was cleared and before os.close ran, so it stays open: the note must
    say that close is UNCONFIRMED, never that the descriptors closed. Before round 7 (A) claimed
    "its anchor unlocked and its descriptors closed" and (B) claimed a kept record that did not
    exist; before round 9 (D) claimed "its descriptors closed" with a descriptor still open."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    for case in ("exit-entry", "anchor-close-fails", "lease-leg", "close-interrupted"):
        failure = _st_in_child(lambda c=case: _st_f7_4_case(root, c))
        assert failure is None, "{}: {}".format(case, failure)
        for path in (os.path.join(_st_ctl_dir(root), ACTIVE_NAME), _st_lease_path(root)):
            if case == "lease-leg" and os.path.exists(path):
                os.unlink(path)           # the exited child's records, kept by design
        assert _st_anchor_free(root), "the child's exit frees the anchor ({})".format(case)
        _st_no_records(root, case)


def _st_f7_4_case(root, case):
    """One T-f7-4 case, run in a child (it installs its own SIGINT handler and faults)."""
    mod = sys.modules[__name__]
    saved_close = os.close
    saved_unlink = mod._verified_unlink
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    anchor = os.stat(os.path.join(_st_ctl_dir(root), ANCHOR_NAME))
    held = []

    def _anchor_close_fails(fd):
        st = os.fstat(fd)
        saved_close(fd)                   # the kernel released it; the report is a failure
        if (st.st_dev, st.st_ino) == (anchor.st_dev, anchor.st_ino):
            raise OSError(errno.EIO, "simulated anchor close failure (T-f7-4)")

    def _record_close_interrupted(fd):
        st = os.fstat(fd)
        if not held and stat.S_ISREG(st.st_mode) and st.st_nlink == 0:
            held.append(fd)               # a removed record's descriptor: interrupted BEFORE close
            raise KeyboardInterrupt()
        saved_close(fd)

    def _lease_leg_fails(dir_fd, name, *a, **k):
        if name == _opf_check.LEASE_NAME:
            raise OpLockError("cannot unlink lease (simulated, T-f7-4)")
        return saved_unlink(dir_fd, name, *a, **k)

    def handler(signum, frame):
        if case == "exit-entry":
            _st_arm_interrupt(_st_named("_ReleaseScope.__exit__"), 1)
        elif case == "anchor-close-fails":
            os.close = _anchor_close_fails
        elif case == "close-interrupted":
            os.close = _record_close_interrupted
        else:
            mod._verified_unlink = _lease_leg_fails
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, handler)
    baseline = _st_open_fds()
    state = _st_arm_signal(_st_named("_publish_staged"), 1)
    try:
        caught = _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        sys.settrace(None)
        os.close = saved_close
        mod._verified_unlink = saved_unlink
    assert state["fired"], case
    notes = " ".join(getattr(caught, "__notes__", ()))
    records = (os.path.exists(active), os.path.exists(lease))
    if case == "exit-entry":
        assert _st_open_fds() - baseline == 5 and not _st_anchor_free(root), \
            "the interrupted exit retains the five descriptors and the lock (observed)"
        assert records == (False, False), records
        assert "released only in part: its records removed, the release of its hold on the lock " \
            "NOT confirmed" in notes and "its descriptor closes NOT completed" in notes, notes
        assert "descriptors closed" not in notes and "anchor descriptor closed" not in notes, notes
    elif case == "anchor-close-fails":
        assert _st_open_fds() == baseline and records == (False, False), records
        assert _st_anchor_free(root), "the kernel released the descriptor, and with it the lock"
        assert "released only in part: its records removed, its anchor descriptor's close " \
            "reported a failure" in notes and "the release of its hold on the lock is NOT " \
            "confirmed" in notes, notes
        assert "kept" not in notes and "and its descriptors closed" not in notes, notes
    elif case == "close-interrupted":
        assert held, "the record descriptor's close must have been interrupted"
        os.fstat(held[0])                 # observed OPEN: its close never ran
        assert _st_open_fds() - baseline == 1 and records == (False, False), records
        assert _st_anchor_free(root), "the anchor descriptor itself was closed"
        assert "released only in part" in notes and "UNCONFIRMED" in notes, notes
        assert "and its descriptors closed" not in notes, notes
        saved_close(held[0])
    else:
        assert _st_open_fds() == baseline and records == (True, True), records
        assert _st_anchor_free(root), "the anchor descriptor was closed (observed)"
        assert "released only in part: neither of its records removed" in notes \
            and "its anchor descriptor closed (releasing its hold on the lock), and its " \
            "descriptors closed" in notes, notes


def _st_returning_fork_handler(root, record):
    """Install a SIGINT handler that FORKS and RETURNS in both processes (fix round 8, the codex
    forward-execution probe): the child continues whatever the parent was doing, forward, and must
    leave through _st_continuation; the parent waits for the child to exit, appends (the lock state
    before the fork, the lock state after the child exited, the child's wait status, the child's
    report) to record["events"], and carries on as if no signal had come."""
    def handler(signum, frame):
        before = _st_state(root)
        rfd, wfd = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            os.close(rfd)
            record["wfd"] = wfd
            return
        os.close(wfd)
        data = bytearray()
        while True:
            chunk = os.read(rfd, 65536)
            if not chunk:
                break
            data += chunk
        os.close(rfd)
        _, status = os.waitpid(pid, 0)
        record["events"].append((before, _st_state(root), status,
                                 bytes(data).decode("utf-8", "replace")))

    signal.signal(signal.SIGINT, handler)


def _st_continuation(top, record, fn, *args):
    """Run fn(*args) in the process `top`. A forked continuation (any other pid) reports how the
    call ended, "REFUSED: " and the exception with its notes, or "GOT A CAPABILITY", and exits 0
    without running the test's own code further; the parent's call returns or raises as usual."""
    try:
        result = fn(*args)
    except BaseException as exc:
        if os.getpid() == top:
            raise
        report = "REFUSED: {!r} {}".format(exc, " ".join(getattr(exc, "__notes__", ())))
    else:
        if os.getpid() == top:
            return result
        report = "GOT A CAPABILITY (pid {} bound in it)".format(
            getattr(result, "_acquirer_pid", None))
    try:
        _journal._write_all(record["wfd"], report.encode("utf-8", "replace"))
    except BaseException:
        os._exit(4)
    os._exit(0)


def _st_continuation_event(record, where):
    """The single fork event of one attempt: the continuation exited 0, was REFUSED at the hand-off
    having closed only its five inherited descriptor copies, and changed none of the acquirer's
    lock state. Returns the child's report."""
    assert len(record["events"]) == 1, "exactly one fork ({}): {}".format(where, record["events"])
    before, after, status, report = record["events"][0]
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "the continuation must exit through its report ({}, status {}): {}".format(
            where, status, report)
    assert report.startswith("REFUSED: OpLockError") and "forked continuation" in report \
        and "it receives no capability" in report \
        and "it closed only its 5 inherited descriptor copies" in report, \
        "a forked continuation must receive no capability ({}): {}".format(where, report)
    # Fix round 9 (claude LOW): the refusal states only what the refusal itself did.
    assert "this refusal removed nothing" in report and "it removed no record" not in report, \
        "the refusal must claim only what it did ({}): {}".format(where, report)
    assert before == after, "the continuation changed the acquirer's lock state ({}): {} -> {}; " \
        "its report: {}".format(where, before, after, report)
    return report


def _st_parent_after_continuation(root, cap, baseline, where):
    """The acquirer's side after a refused continuation: it holds its own unreleased capability, the
    anchor held and both records present, a second acquisition is refused as contention, and its
    release is clean with no descriptor delta."""
    assert cap is not None and not cap._released, "the acquirer keeps its capability ({})".format(
        where)
    assert not _st_anchor_free(root), "the acquirer's anchor lock is held ({})".format(where)
    assert os.path.exists(os.path.join(_st_ctl_dir(root), ACTIVE_NAME)) \
        and os.path.exists(_st_lease_path(root)), "the acquirer's records ({})".format(where)
    _st_expect_refusal(acquire_operation, root, "op2", needle="held")
    release_operation(cap)
    assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
    assert _st_anchor_free(root), "the acquirer's release frees the anchor ({})".format(where)
    _st_no_records(root, where)


def _t_f8_1_forked_continuation(d, env):
    """T-f8-1 (fix round 8, codex HIGH 1: a forked acquisition continuation). A SIGINT handler
    FORKS and, unlike T-f7-1, RETURNS in the child, which therefore continues the acquisition
    forward. (A) The codex route, with the production deferral standing: a secondary thread leaves
    SIGINT unblocked, and a SIGINT sent at the line that builds the capability has its handler run
    on the main thread inside the section (the disclosed multithreaded limitation). (B) With the
    deferral disabled, at every line event of the acquisition's entry, its body, the capability's
    construction, and the hand-off at which the lease already exists (after the last state change,
    where a continuation has nothing left to publish). Each time the continuation must be REFUSED
    at the hand-off, receiving no capability and closing only its five inherited descriptor copies,
    with none of the acquirer's lock state changed; the acquirer keeps its capability, anchor lock,
    and records, a second acquisition is refused, and its release is clean. Before the fix the
    capability recorded the child's pid (captured at the build), the child released the parent's
    lock and records, and a second acquisition SUCCEEDED while the parent held its capability."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child(lambda: _st_f8_1_codex_route(root))
    assert failure is None, failure
    failure = _st_in_child(lambda: _st_f8_1_sweep(root))
    assert failure is None, failure


def _st_f8_1_codex_route(root):
    """T-f8-1 (A), run in a child: the production deferral, a secondary thread with SIGINT
    unblocked, and a SIGINT sent at the capability's construction line."""
    import inspect
    import warnings
    lines, first = inspect.getsourcelines(_acquire_body)
    build = [first + i for i, text in enumerate(lines) if "cap = OpCapability(" in text]
    assert len(build) == 1, build
    top = os.getpid()
    record = {"events": []}
    _st_returning_fork_handler(root, record)
    stop = threading.Event()
    other = threading.Thread(target=stop.wait, args=(30,), daemon=True)
    other.start()                         # created before the acquisition: SIGINT unblocked there
    sent = []

    def local(frame, event, arg):
        if event == "line" and frame.f_lineno == build[0] and not sent:
            sent.append(True)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.05)              # the kernel delivers it to the unblocked thread
        return local

    baseline = _st_open_fds()
    sys.settrace(lambda frame, event, arg: local if frame.f_code is _acquire_body.__code__
                 else None)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)   # fork with a thread alive
            cap = _st_continuation(top, record, acquire_operation, root, "op")
    finally:
        sys.settrace(None)
        stop.set()
    other.join(30)
    assert sent, "the signal must have been sent"
    _st_continuation_event(record, "T-f8-1 (A)")
    _st_parent_after_continuation(root, cap, baseline, "T-f8-1 (A)")


def _st_f8_1_sweep(root):
    """T-f8-1 (B), run in a child: the deferral disabled; a returning fork at every line event, at
    which the lease exists, of the acquisition's entry, body, capability construction, and
    hand-off."""
    mod = sys.modules[__name__]
    mod._deferrable_signals = lambda: ()   # this child only: no deferral stands
    lease = _st_lease_path(root)
    funcs = _st_named("acquire_operation", "_acquire_body", "OpCapability.__init__", "_handoff")
    codes = set(f.__code__ for f in funcs)
    top = os.getpid()
    record = {"events": []}
    _st_returning_fork_handler(root, record)

    def arm(k):
        """A REAL SIGINT at the k-th counted line event (k 0: count only); returns the count."""
        state = {"seen": 0}

        def local(frame, event, arg):
            if event == "line" and os.path.exists(lease):
                state["seen"] += 1
                if state["seen"] == k:
                    sys.settrace(None)
                    os.kill(os.getpid(), signal.SIGINT)
            return local

        sys.settrace(lambda frame, event, arg: local if frame.f_code in codes else None)
        return state

    state = arm(0)
    try:
        cap = acquire_operation(root, "op")
    finally:
        sys.settrace(None)
    release_operation(cap)
    total = state["seen"]
    assert total, "the sweep must see line events after the lease's publication"
    for k in range(1, total + 1):
        where = "T-f8-1 (B) counted line event {}".format(k)
        baseline = _st_open_fds()
        record["events"] = []
        arm(k)
        try:
            cap = _st_continuation(top, record, acquire_operation, root, "op")
        finally:
            sys.settrace(None)
        _st_continuation_event(record, where)
        _st_parent_after_continuation(root, cap, baseline, where)


def _t_f8_2_concurrent_release(d, env):
    """T-f8-2 (fix round 8, codex HIGH 2: concurrent release of one capability). Thread B calls
    release_operation on a capability and is paused (by a trace hook that only schedules, never
    injects) as it enters the release scope, after the released check; the main thread releases
    the same capability to completion; the five descriptor numbers it freed are then REUSED for
    unrelated /dev/null descriptors; B resumes. B must lose the claim and refuse (OpLockError,
    "already released") holding none of the descriptors: every reused descriptor stays open, the
    capability ends released once, both records removed, and the anchor free. Before the fix B had
    already snapshotted the five numbers and closed all five unrelated descriptors (EBADF)."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child(lambda: _st_f8_2_body(root))
    assert failure is None, failure


def _st_f8_2_body(root):
    """T-f8-2's body, run in a child."""
    baseline = _st_open_fds()
    cap = acquire_operation(root, "op")
    fds = [cap._machine_fd, cap._ctl_fd, cap._anchor_fd, cap._active_fd, cap._lease_fd]
    paused = threading.Event()
    resume = threading.Event()
    outcome = {}
    enter = _ReleaseScope.__enter__.__code__

    def second():
        def hook(frame, event, arg):
            if event == "call" and frame.f_code is enter and not paused.is_set():
                paused.set()
                resume.wait(30)
            return None

        sys.settrace(hook)
        try:
            release_operation(cap)
            outcome["b"] = None
        except BaseException as exc:
            outcome["b"] = exc
        finally:
            sys.settrace(None)

    other = threading.Thread(target=second)
    other.start()
    assert paused.wait(30), "the second releaser must reach the release scope"
    release_operation(cap)                # the first release completes
    reused = []
    for fd in fds:                        # the freed numbers now name unrelated files
        unrelated = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        if unrelated != fd:
            os.dup2(unrelated, fd, inheritable=False)
            os.close(unrelated)
        reused.append(fd)
    resume.set()
    other.join(30)
    assert not other.is_alive(), "the second releaser must finish"
    lost = outcome.get("b")
    closed = []
    for fd in reused:
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):
                closed.append(fd)
        except OSError:
            closed.append(fd)
    for fd in reused:
        if fd not in closed:
            os.close(fd)
    assert not closed, "the losing release closed reused, unrelated descriptors: {}".format(closed)
    assert isinstance(lost, OpLockError) and "already released" in str(lost), repr(lost)
    assert cap._released, "the capability ends released"
    assert _st_open_fds() == baseline, "no descriptor may leak"
    assert _st_anchor_free(root), "the anchor ends free"
    _st_no_records(root, "T-f8-2")


def _t_f8_3_not_durable_removal(d, env):
    """T-f8-3 (fix round 8, codex LOW: a failed durability step is not an unperformed unlink). A
    directory fsync is failed (EIO) right after a SUCCESSFUL verified unlink: (A) in the unreturned
    capability's release (a real SIGINT during the publication, deferred, whose handler arms the
    fault), after the lease's unlink and, separately, after the active record's; (B) in a public
    release after the lease's unlink; (C) in the acquisition unwind after the lease's unlink. Each
    note or message must state the removal that happened and that it is not durable, never that the
    record was not removed; the conservative order still holds (a lease whose removal is not durable
    keeps the active record). Before the fix (A) said "neither of its records removed" with the
    lease gone, and "its lease removed but not its active record" with both gone."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    for which in ("lease", "active record"):
        failure = _st_in_child(lambda w=which: _st_f8_3_unreturned(root, w))
        assert failure is None, "{}: {}".format(which, failure)
        for path in (os.path.join(_st_ctl_dir(root), ACTIVE_NAME), _st_lease_path(root)):
            if os.path.exists(path):
                os.unlink(path)           # the exited child's kept record, cleared by hand
    failure = _st_in_child(lambda: _st_f8_3_release_and_unwind(root))
    assert failure is None, failure


def _st_fail_dir_fsync(path, armed):
    """Replace os.fsync so that, while armed["on"] is set, the next fsync of the directory at `path`
    fails with EIO (once). Returns the original os.fsync, for the caller to restore."""
    target = os.stat(path)
    saved = os.fsync

    def _fsync(fd):
        st = os.fstat(fd)
        if armed.get("on") and (st.st_dev, st.st_ino) == (target.st_dev, target.st_ino):
            armed["on"] = False
            raise OSError(errno.EIO, "simulated directory fsync failure (T-f8-3)")
        return saved(fd)

    os.fsync = _fsync
    return saved


def _st_f8_3_unreturned(root, which):
    """T-f8-3 (A), one record, run in a child."""
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    where = os.path.join(root, ".working", "toml") if which == "lease" else _st_ctl_dir(root)
    armed = {}

    def handler(signum, frame):
        armed["on"] = True
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, handler)
    saved = _st_fail_dir_fsync(where, armed)
    baseline = _st_open_fds()
    state = _st_arm_signal(_st_named("_publish_staged"), 1)
    try:
        caught = _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        sys.settrace(None)
        os.fsync = saved
    assert state["fired"], which
    notes = " ".join(getattr(caught, "__notes__", ()))
    assert _st_open_fds() == baseline and _st_anchor_free(root), notes
    not_durable = "(not durably: the directory fsync after its unlink FAILED"
    if which == "lease":
        assert not os.path.exists(lease) and os.path.exists(active), "the lease went; kept active"
        assert "released only in part: its lease {}".format(not_durable) in notes \
            and "removed but not its active record" in notes, notes
        assert "neither of its records removed" not in notes, notes
    else:
        assert not os.path.exists(lease) and not os.path.exists(active), "both records went"
        assert "released only in part: its lease and its active record {}".format(not_durable) \
            in notes, notes
        assert "but not its active record" not in notes, notes
    assert "(removal not durable)" in notes and "was unlinked, but the directory fsync" in notes, \
        notes


def _st_f8_3_release_and_unwind(root):
    """T-f8-3 (B) and (C), run in a child."""
    mod = sys.modules[__name__]
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    machine = os.path.join(root, ".working", "toml")
    armed = {}
    cap = acquire_operation(root, "op")
    saved = _st_fail_dir_fsync(machine, armed)
    armed["on"] = True
    try:
        msg = _st_expect_refusal(release_operation, cap, needle="records removed")
    finally:
        os.fsync = saved
    assert "records removed: lease (removal not durable)" in msg \
        and "lease was unlinked, but the directory fsync" in msg \
        and "active leg: KEPT, because the lease removal was not made durable" in msg, msg
    assert not os.path.exists(lease) and os.path.exists(active), "(B) conservative order"
    assert _st_anchor_free(root), "(B) the release still unlocks"
    os.unlink(active)
    saved_cap = mod.OpCapability

    class _BoomCap:
        def __init__(self, *a, **k):
            armed["on"] = True
            raise OSError(errno.EIO, "simulated post-creation failure (T-f8-3 unwind)")

    saved = _st_fail_dir_fsync(machine, armed)
    mod.OpCapability = _BoomCap
    try:
        msg = _st_expect_refusal(acquire_operation, root, "op", needle="unwind failed")
    finally:
        mod.OpCapability = saved_cap
        os.fsync = saved
    assert "lease (unwind) was unlinked, but the directory fsync" in msg \
        and "active record (unwind) KEPT: the lease's removal was not made durable" in msg, msg
    assert not os.path.exists(lease) and os.path.exists(active), "(C) conservative order"
    os.unlink(active)
    assert _st_anchor_free(root), "(C) the unwind still unlocks"


def _t_f8_4_publication_close_truth(d, env):
    """T-f8-4 (fix round 8, claude LOW: a publication note claims only a close that ran; extended in
    fix round 9). On the ownerless publication path (_create_control_file with no owner, as the
    resume substrate calls it), an interruption lands inside the descriptor's hand-over to the
    caller, after the owner has released it: (A) raised in the publishing process itself, (B)
    raised in a FORKED CHILD whose handler forked there (the publisher's hand-over completes). In
    neither case did any close run, so the note must say the descriptor was NOT closed and stays
    open, and the descriptor must be observed open; in (B) the child removes no name and the
    publisher's record is intact. (C) Fix round 9 (codex LOW): the write is interrupted, and the
    cleanup's close of the staging descriptor is itself interrupted after ownership was cleared and
    before os.close ran: the descriptor stays open (observed), so the note must say that close is
    UNCONFIRMED, never that the descriptor closed. Before round 8 (A) noted "the torn record was
    removed and its descriptor closed" with the descriptor still open, and (B) noted "it closed only
    its own descriptor copy"; before round 9 (C) noted "its descriptor closed"."""
    failure = _st_in_child(lambda: _st_f8_4_body(d))
    assert failure is None, failure


def _st_f8_4_body(d):
    """T-f8-4's body, run in a child."""
    probe = os.path.join(d, "probe-dir")
    os.mkdir(probe)
    dir_fd = os.open(probe, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    saved = _FdOwner.transfer
    seen = {}
    top = os.getpid()

    def _interrupted_transfer(self, fd):
        self._fds.remove(fd)              # the owner has released it; the caller has not got it
        seen["fd"] = fd
        raise KeyboardInterrupt()

    def _forked_transfer(self, fd):
        self._fds.remove(fd)
        seen["fd"] = fd
        rfd, wfd = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            os.close(rfd)
            seen["wfd"] = wfd
            raise KeyboardInterrupt()
        os.close(wfd)
        data = bytearray()
        while True:
            chunk = os.read(rfd, 65536)
            if not chunk:
                break
            data += chunk
        os.close(rfd)
        _, seen["status"] = os.waitpid(pid, 0)
        seen["report"] = bytes(data).decode("utf-8", "replace")
        return fd

    try:
        # (A) the publisher itself.
        baseline = _st_open_fds()
        _FdOwner.transfer = _interrupted_transfer
        try:
            caught = _st_expect_interrupt(_create_control_file, dir_fd, "a.toml", b"x = 1\n",
                                          "probe record")
        finally:
            _FdOwner.transfer = saved
        notes = " ".join(getattr(caught, "__notes__", ()))
        os.fstat(seen["fd"])              # observed open: no close ran
        assert _st_open_fds() - baseline == 1, "the hand-over descriptor is the one left open"
        assert "descriptor closed" not in notes and "was not closed and stays open" in notes, notes
        os.close(seen["fd"])
        # (B) a forked child of the publisher.
        _FdOwner.transfer = _forked_transfer
        try:
            try:
                fd, _ = _create_control_file(dir_fd, "b.toml", b"x = 2\n", "probe record")
            except KeyboardInterrupt as exc:
                if os.getpid() == top:
                    raise
                open_after = True
                try:
                    os.fstat(seen["fd"])
                except OSError:
                    open_after = False
                report = "{} OPEN={}".format(" ".join(getattr(exc, "__notes__", ())), open_after)
                _journal._write_all(seen["wfd"], report.encode("utf-8", "replace"))
                os._exit(0)
        finally:
            _FdOwner.transfer = saved
        report = seen["report"]
        assert os.WIFEXITED(seen["status"]) and os.WEXITSTATUS(seen["status"]) == 0, seen
        assert "forked child" in report and "removed no name" in report \
            and "was not closed and stays open" in report and report.endswith("OPEN=True"), report
        assert "closed only its own descriptor copy" not in report, report
        with open(os.path.join(probe, "b.toml"), "rb") as fh:
            assert fh.read() == b"x = 2\n", "the publisher's record is intact"
        os.close(fd)
        # (C) the cleanup's close interrupted before os.close ran.
        _st_f8_4_interrupted_close(dir_fd)
    finally:
        os.close(dir_fd)


def _t_f8_5_setgid_contract(d, env):
    """T-f8-5 (fix round 8, codex LOW: the set-group-ID contract matches the implementation). The
    implementation: with .git at 0755 (not set-group-ID) and an existing control directory at
    02755 (owner permissions intact), acquisition and release succeed and leave 02755 unchanged,
    because the directory is not repaired and its validation refuses only group or other write.
    The contract must describe exactly that: the inheritance condition is the REPAIR's, and an
    intact directory's special bits are accepted as they are. The behavioural half passes on round
    7 as well (it pins the behaviour the corrected text describes); the contract half fails there,
    whose text claimed the bit was accepted ONLY as mkdir's inheritance."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    os.chmod(os.path.join(root, ".git"), 0o755)
    ctl = _st_ctl_dir(root)
    os.chmod(ctl, 0o2755)
    if not os.lstat(ctl).st_mode & stat.S_ISGID:
        raise _StSkip("the fixture cannot set S_ISGID on its control directory")
    release_operation(acquire_operation(root, "op"))
    assert stat.S_IMODE(os.lstat(ctl).st_mode) == 0o2755, "an intact directory is left as it is"
    text = " ".join(__doc__.split())
    assert "is accepted and preserved only as mkdir inherits it" not in text, \
        "the contract must not claim a restriction the validation does not enforce"
    assert "that REPAIR accepts and preserves a control directory's set-group-ID bit only as " \
        "mkdir inherits it" in text and "a control directory whose owner permissions are intact " \
        "is not repaired" in text, "the contract states the repair's condition and its scope"


def _st_reap_bounded(pid, deadline):
    """Reap the forked child `pid` by `deadline` (a time.monotonic() value, fix round 9): a child
    still running then is SIGKILLed and reaped. Returns (its wait status, whether it hung)."""
    while True:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return status, False
        if time.monotonic() >= deadline:
            os.kill(pid, signal.SIGKILL)
            _, status = os.waitpid(pid, 0)
            return status, True
        time.sleep(0.01)


def _st_collect(pid, rfd, seconds):
    """Collect a forked child's report from `rfd` and its wait status within `seconds` (fix round
    9), so a hang is a failure, never a stalled self-test: a child that has not ended by then is
    SIGKILLed and reported hung. Returns (its wait status, its report, whether it hung)."""
    import select
    deadline = time.monotonic() + seconds
    data = bytearray()
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([rfd], [], [], left)
            if not ready:
                break
            chunk = os.read(rfd, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(rfd)
    status, hung = _st_reap_bounded(pid, deadline)
    return status, bytes(data).decode("utf-8", "replace"), hung


def _st_in_child_bounded(body, seconds):
    """_st_in_child with a bound (fix round 9): a child that has not ended within `seconds` is
    SIGKILLed and reported as hung, never waited on forever. Returns None when body() returned,
    else the failure the child reported."""
    import traceback
    rfd, wfd = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        code = 0
        report = b""
        try:
            os.close(rfd)
            body()
        except BaseException:
            code = 1
            report = traceback.format_exc().encode("utf-8", "replace")
        try:
            _journal._write_all(wfd, report)
        except BaseException:
            pass
        os._exit(code)
    os.close(wfd)
    status, report, hung = _st_collect(pid, rfd, seconds)
    if hung:
        return "child HUNG past {} seconds and was killed: {}".format(seconds, report)
    if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
        return None
    return "child wait status {}: {}".format(status, report)


def _st_bounded_fork_handler(record, probe, seconds, after=None):
    """Install a SIGINT handler that FORKS and RETURNS in both processes (fix round 9; the returning
    fork of _st_returning_fork_handler, bounded). The child continues whatever the parent was
    doing, forward, and must leave through _st_forward. The parent collects the child within
    `seconds` (a hung child is killed and reported, never waited on forever), appends (probe()
    before the fork, probe() after the child ended, the child's wait status, its report, whether it
    hung) to record["events"], runs after() when given, and carries on as if no signal had come."""
    def handler(signum, frame):
        before = probe()
        rfd, wfd = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        pid = os.fork()
        if pid == 0:
            os.close(rfd)
            record["wfd"] = wfd
            return
        os.close(wfd)
        status, report, hung = _st_collect(pid, rfd, seconds)
        record["events"].append((before, probe(), status, report, hung))
        if after is not None:
            after()

    signal.signal(signal.SIGINT, handler)


def _st_forward(top, record, fn, *args, **kwargs):
    """Run fn(*args, **kwargs) in the process `top`, returning or raising as usual. A forked
    continuation (any other pid) reports how the call ended, "RETURNED" and the result's type, or
    "RAISED: " and the exception with its notes, through record["wfd"], and exits 0 without running
    the test's own code further (fix round 9)."""
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:
        if os.getpid() == top:
            raise
        report = "RAISED: {!r} {}".format(exc, " ".join(getattr(exc, "__notes__", ())))
    else:
        if os.getpid() == top:
            return result
        report = "RETURNED {}".format(type(result).__name__)
    try:
        _journal._write_all(record["wfd"], report.encode("utf-8", "replace"))
    except BaseException:
        os._exit(4)
    os._exit(0)


def _st_arm_counted(funcs, k, when=None):
    """Arm ONE REAL SIGINT at the k-th line event of the own frames of `funcs`, counting only the
    events at which when() holds (every event when it is None); k 0 counts without sending (fix
    round 9). Returns the state dict ("seen", "fired"); the caller disarms with
    sys.settrace(None) in a finally."""
    codes = set(f.__code__ for f in funcs)
    state = {"seen": 0, "fired": False}

    def local(frame, event, arg):
        if event == "line" and not state["fired"] and (when is None or when()):
            state["seen"] += 1
            if state["seen"] == k:
                state["fired"] = True
                sys.settrace(None)
                os.kill(os.getpid(), signal.SIGINT)
        return local

    sys.settrace(lambda frame, event, arg: local if frame.f_code in codes else None)
    return state


def _st_fork_event(record, where):
    """The single fork event of one attempt (fix round 9), asserted to have ended by itself, in
    time, with exit 0. Returns (probe before the fork, probe after the child ended, its report)."""
    assert len(record["events"]) == 1, "exactly one fork ({}): {}".format(where, record["events"])
    before, after, status, report, hung = record["events"][0]
    assert not hung, "the forked continuation HUNG and was killed ({}): {}".format(where, report)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
        "the forked continuation must end through its report ({}, status {}): {}".format(
            where, status, report)
    return before, after, report


def _st_settle(root, baseline, where):
    """After one fork attempt (fix round 9): no descriptor leaked and the anchor free; any record or
    staging leftover a continuation's forward steps left is then cleared (the property under test
    is asserted by the caller, not here)."""
    assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
    assert _st_anchor_free(root), "the anchor must end free ({})".format(where)
    ctl = _st_ctl_dir(root)
    machine = os.path.join(root, ".working", "toml")
    left = [p for p in (os.path.join(ctl, ACTIVE_NAME), _st_lease_path(root)) if os.path.lexists(p)]
    left += [os.path.join(ctl, e) for e in _st_staging_leftovers(ctl, ACTIVE_NAME)]
    left += [os.path.join(machine, e)
             for e in _st_staging_leftovers(machine, _opf_check.LEASE_NAME)]
    for path in left:
        os.unlink(path)


def _st_flock_semantics(d):
    """The flock semantics release-by-close rests on (fix round 9), witnessed on the fixture's own
    filesystem: the lock belongs to the open file description, a dup or a forked child's inherited
    copy keeps it held after the holder's own descriptor closes, and the LAST close frees it."""
    path = os.path.join(d, "flock-semantics")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)

    def free():
        probe = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(probe, fcntl.LOCK_UN)
            return True
        finally:
            os.close(probe)

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    copy = os.dup(fd)
    os.close(fd)
    assert not free(), "a dup keeps the lock held after the original descriptor closes"
    rfd, wfd = os.pipe()
    sys.stdout.flush()
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(wfd)
            os.read(rfd, 1)               # hold the inherited copy until the parent closes wfd
        finally:
            os._exit(0)
    os.close(rfd)
    os.close(copy)
    held = not free()
    os.close(wfd)
    _, hung = _st_reap_bounded(pid, time.monotonic() + 30)
    assert not hung, "the semantics witness child must exit"
    assert held, "a forked child's inherited copy keeps the lock held after the holder's last close"
    assert free(), "the last close (the child's, at its exit) frees the lock"
    os.unlink(path)


def _t_f9_1_claim_fork(d, env):
    """T-f9-1 (fix round 9, codex MED 1: a fork during a contended release claim). Thread B releases
    a capability and is paused inside the claim step (_ReleaseScope._claim_locked) holding the
    claim lock; the main thread A releases the same capability, and a SIGINT whose handler FORKS and
    RETURNS is sent at each line of A's take_ownership in turn, each case in a bounded child with
    the deferral disabled (as on a thread or platform where it is a no-op). The forked continuation
    of A must never wait on the inherited claim lock (held by B, a thread that does not exist in
    the child): it must end within the bound, refused as a forked child (or, forked after A had
    already concluded that it lost, as having lost the claim), never by the claim's timeout, which
    a child that polled the inherited lock would reach. B's release completes, A refuses as having
    lost the claim, nothing leaks, and the anchor ends free. Before the fix the continuation forked
    after the one-time pid check blocked forever on the inherited lock."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    code = _ReleaseScope.take_ownership.__code__
    lines = sorted(set(n for _, _, n in code.co_lines()
                       if n is not None and n > code.co_firstlineno))
    marks = os.path.join(d, "marks")
    os.mkdir(marks)
    for line in lines:
        failure = _st_in_child_bounded(lambda n=line: _st_f9_1_case(root, n, marks), 120)
        assert failure is None, "take_ownership line {}: {}".format(line, failure)
    fired = os.listdir(marks)
    assert fired, "the fork must have been sent at some line of take_ownership"
    assert any(m.endswith("-held") for m in fired), \
        "a fork must land while B holds the claim lock: {}".format(sorted(fired))


def _st_f9_1_case(root, line, marks):
    """One T-f9-1 case, run in a bounded child."""
    import warnings
    sys.modules[__name__]._deferrable_signals = lambda: ()   # this child only: no deferral
    baseline = _st_open_fds()
    cap = acquire_operation(root, "op")
    paused = threading.Event()
    resume = threading.Event()
    holding = {"b": False}
    outcome = {}
    claim_code = _ReleaseScope._claim_locked.__code__

    def second():
        def hook(frame, event, arg):
            if event == "call" and frame.f_code is claim_code and not paused.is_set():
                holding["b"] = True
                paused.set()
                resume.wait(1.0)          # bounded: the fork handler resumes it sooner
                holding["b"] = False
            return None

        sys.settrace(hook)
        try:
            release_operation(cap)
            outcome["b"] = None
        except BaseException as exc:
            outcome["b"] = exc
        finally:
            sys.settrace(None)

    top = os.getpid()
    record = {"events": []}
    _st_bounded_fork_handler(record, lambda: holding["b"], 15, after=resume.set)
    own = _ReleaseScope.take_ownership.__code__
    sent = []

    def local(frame, event, arg):
        if event == "line" and frame.f_lineno == line and not sent:
            sent.append(True)
            sys.settrace(None)
            os.kill(os.getpid(), signal.SIGINT)
        return local

    other = threading.Thread(target=second, daemon=True)
    lost = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # fork with a thread alive
        other.start()
        assert paused.wait(30), "B must reach the claim step"
        sys.settrace(lambda frame, event, arg: local if frame.f_code is own else None)
        try:
            try:
                _st_forward(top, record, release_operation, cap)
            except OpLockError as exc:
                lost = exc
        finally:
            sys.settrace(None)
            resume.set()
    other.join(30)
    where = "T-f9-1 line {}".format(line)
    assert not other.is_alive(), "B must finish ({})".format(where)
    assert outcome.get("b") is None, "B's release completes ({}): {!r}".format(where, outcome)
    assert isinstance(lost, OpLockError) and "already released" in str(lost), \
        "A loses the claim ({}): {!r}".format(where, lost)
    assert _st_open_fds() == baseline, "no descriptor may leak ({})".format(where)
    assert _st_anchor_free(root), "the anchor ends free ({})".format(where)
    _st_no_records(root, where)
    if not sent:
        return                            # a line this scenario never reaches
    before, _, report = _st_fork_event(record, where)
    # Refused as a forked child, or (forked after A had already concluded it lost) as having lost
    # the claim; never by the claim's timeout (a child that polled the inherited lock) or a return.
    refused = "forked child" in report or "claimed it first" in report
    assert report.startswith("RAISED:") and refused and "longer than" not in report, \
        "the continuation must be refused as a forked child, never wait on the claim lock or " \
        "return ({}): {}".format(where, report)
    with open(os.path.join(marks, "{}-{}".format(line, "held" if before else "free")), "w"):
        pass


def _t_f9_2_release_by_close(d, env):
    """T-f9-2 (fix round 9, codex MED 2: a forked child can never free the acquirer's lock). The
    flock semantics the design rests on are witnessed first (_st_flock_semantics). Then, in a
    bounded child with the deferral disabled, a SIGINT whose handler FORKS and RETURNS is sent at
    every line event of (A) the release (the public entry, the scoped release, its legs, the release
    scope's exit, the claim, and the descriptor owner's close steps) and (B) the acquisition unwind
    after a failure injected once both records are published. The forked continuation runs the
    interrupted code forward and must end by itself within the bound, and the anchor's lock state
    must be the same after it ended as before the fork: a child's closes never free a lock its
    acquirer still references. The acquirer's own release or unwind must end with the anchor free
    and no descriptor leaked (a forward step the child ran, such as a record removal, may fail the
    acquirer's own leg: the disclosed forward-execution residual). Before the fix the continuation
    reached the explicit LOCK_UN of the release scope's exit or of the unwind and freed the lock
    while the acquirer still held it."""
    _st_flock_semantics(d)
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child_bounded(lambda: _st_f9_2_body(root), 900)
    assert failure is None, failure


def _st_f9_2_body(root):
    """T-f9-2's body, run in a bounded child."""
    mod = sys.modules[__name__]
    mod._deferrable_signals = lambda: ()   # this child only: no deferral stands
    top = os.getpid()
    record = {"events": []}
    _st_bounded_fork_handler(record, lambda: _st_anchor_free(root), 60)
    # (A) the release.
    funcs = _st_named("release_operation", "_release_scoped", "_release_legs",
                      "_ReleaseScope.__exit__", "_ReleaseScope.take_ownership",
                      "_ReleaseScope._claim_locked", "_FdOwner.close_guarded", "_FdOwner.close",
                      "_FdOwner.close_all")
    cap = acquire_operation(root, "op")
    state = _st_arm_counted(funcs, 0)
    try:
        release_operation(cap)
    finally:
        sys.settrace(None)
    total = state["seen"]
    assert total, "the sweep must see line events in the release"
    for k in range(1, total + 1):
        where = "T-f9-2 (A) release line event {}".format(k)
        baseline = _st_open_fds()
        cap = acquire_operation(root, "op")
        record["events"] = []
        state = _st_arm_counted(funcs, k)
        try:
            try:
                _st_forward(top, record, release_operation, cap)
            except OpLockError:
                pass                      # a child's forward leg can fail the acquirer's own leg
        finally:
            sys.settrace(None)
        assert state["fired"], where
        before, after, report = _st_fork_event(record, where)
        assert before == after, "the forked continuation changed the acquirer's lock ({}): " \
            "anchor free {} -> {}; its report: {}".format(where, before, after, report)
        assert cap._released, where
        _st_settle(root, baseline, where)
    # (B) the acquisition unwind, after both records are published.
    saved_cap = mod.OpCapability
    boom = {"on": False}

    class _BoomCap:
        def __init__(self, *a, **k):
            boom["on"] = True
            raise OSError(errno.EIO, "simulated failure after both publications (T-f9-2)")

    funcs = _st_named("_acquire_body", "_FdOwner.close_guarded", "_FdOwner.close_all")
    mod.OpCapability = _BoomCap
    try:
        state = _st_arm_counted(funcs, 0, when=lambda: boom["on"])
        try:
            acquire_operation(root, "op")
        except (OpLockError, OSError):
            pass
        finally:
            sys.settrace(None)
        total = state["seen"]
        assert total, "the sweep must see line events in the unwind"
        for k in range(1, total + 1):
            where = "T-f9-2 (B) unwind line event {}".format(k)
            boom["on"] = False
            baseline = _st_open_fds()
            record["events"] = []
            state = _st_arm_counted(funcs, k, when=lambda: boom["on"])
            try:
                try:
                    _st_forward(top, record, acquire_operation, root, "op")
                except (OpLockError, OSError):
                    pass
                else:
                    raise AssertionError("the injected failure must fail the acquisition")
            finally:
                sys.settrace(None)
            assert state["fired"], where
            before, after, report = _st_fork_event(record, where)
            assert before == after, "the forked continuation changed the acquirer's lock ({}): " \
                "anchor free {} -> {}; its report: {}".format(where, before, after, report)
            _st_settle(root, baseline, where)
    finally:
        mod.OpCapability = saved_cap


def _t_f9_3_publication_continuation(d, env):
    """T-f9-3 (fix round 9, claude MED: a forked publication continuation). In a bounded child with
    the deferral disabled, a SIGINT whose handler FORKS and RETURNS is sent at every line event of
    the acquisition body, the publication, and the pid-gated step, repeats counted; the parent's
    handler waits for the child, so the continuation runs forward first (the adversarial schedule).
    The continuation must never receive a capability, must change none of the lock state across its
    life (the anchor's flock, the records, the staging names: every mutating step it reaches is
    pid-gated, and at a line boundary it reaches no gated step's call), and the acquirer's outcome
    must NEVER leave a lone owner-less lease (active record absent, lease present), which every
    recover=True refuses; a capability the acquirer receives holds both records and releases them.
    Before the fix a fork
    after the active record's os.link and before its staging unlink let the child retire the staging
    name and publish the lease; the acquirer's retire then failed and its cleanup removed the active
    record, stranding the lease."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child_bounded(lambda: _st_f9_3_body(root), 900)
    assert failure is None, failure


def _st_f9_3_body(root):
    """T-f9-3's body, run in a bounded child."""
    sys.modules[__name__]._deferrable_signals = lambda: ()   # this child only: no deferral
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    top = os.getpid()
    record = {"events": []}
    _st_bounded_fork_handler(record, lambda: _st_state(root), 60)
    funcs = _st_named("_acquire_body", "_publish_staged", "_gated_step")
    state = _st_arm_counted(funcs, 0)
    try:
        cap = acquire_operation(root, "op")
    finally:
        sys.settrace(None)
    release_operation(cap)
    total = state["seen"]
    assert total, "the sweep must see line events in the acquisition"
    for k in range(1, total + 1):
        where = "T-f9-3 line event {}".format(k)
        baseline = _st_open_fds()
        record["events"] = []
        state = _st_arm_counted(funcs, k)
        try:
            try:
                cap = _st_forward(top, record, acquire_operation, root, "op")
            except OpLockError:
                cap = None
        finally:
            sys.settrace(None)
        assert state["fired"], where
        before, after, report = _st_fork_event(record, where)
        assert report.startswith("RAISED:"), \
            "a forked continuation never receives a capability ({}): {}".format(where, report)
        assert before == after, "the forked continuation changed the lock state (anchor free, " \
            "active, lease, staging leftovers) ({}): {} -> {}; its report: {}".format(
                where, before, after, report)
        held = (os.path.exists(active), os.path.exists(lease))
        assert held != (False, True), "a LONE OWNER-LESS LEASE was left (active record absent, " \
            "lease present), which every recover=True refuses ({}); the continuation's report: " \
            "{}".format(where, report)
        if cap is not None:
            assert held == (True, True), "a returned capability holds both records ({})".format(
                where)
            release_operation(cap)
        _st_settle(root, baseline, where)


def _t_f9_4_recovery_continuation(d, env):
    """T-f9-4 (fix round 9, claude LOW: a refusal claims only what it did; and the recovery deletes'
    pid gate). In a bounded child with the deferral disabled, a confirmed-dead holder's paired
    records are planted and recover=True acquires; a SIGINT whose handler FORKS and RETURNS is sent
    as the liveness gate returns, and the parent waits, so the continuation runs forward first. It
    must delete neither stale record (their identities unchanged across its life), and its refusal
    must not assert "it removed no record" (a claim about steps the refusal did not observe). The
    acquirer then recovers and releases normally. Before the fix the continuation deleted both stale
    records, published its own, and was refused with "it removed no record"; the acquirer's
    recovery then refused the replaced records."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child_bounded(lambda: _st_f9_4_body(root), 300)
    assert failure is None, failure


def _st_f9_4_body(root):
    """T-f9-4's body, run in a bounded child."""
    sys.modules[__name__]._deferrable_signals = lambda: ()   # this child only: no deferral
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    dead_pid, dead_start = _st_reaped_child()
    holder = _st_write_active_owned(root, dead_pid, dead_start, os.uname().nodename,
                                    op_id="f9-4-op-id")
    _st_write_lease_owned(root, holder=holder)

    def idents():
        out = []
        for path in (active, lease):
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                out.append(None)
            else:
                out.append((st.st_dev, st.st_ino))
        return tuple(out)

    stale = idents()
    top = os.getpid()
    record = {"events": []}
    _st_bounded_fork_handler(record, idents, 60)
    gate = _require_holder_confirmed_dead.__code__
    sent = []

    def local(frame, event, arg):
        if event == "return" and not sent:
            sent.append(True)
            sys.settrace(None)
            os.kill(os.getpid(), signal.SIGINT)
        return local

    sys.settrace(lambda frame, event, arg: local if frame.f_code is gate else None)
    cap = failed = None
    try:
        try:
            cap = _st_forward(top, record, acquire_operation, root, "op", recover=True)
        except OpLockError as exc:
            failed = exc
    finally:
        sys.settrace(None)
    assert sent, "the signal must have been sent as the liveness gate returned"
    before, after, report = _st_fork_event(record, "T-f9-4")
    assert before == stale, (before, stale)
    assert report.startswith("RAISED:"), "the continuation is refused: {}".format(report)
    assert "it removed no record" not in report, \
        "a refusal must claim only what it did: {}".format(report)
    assert after == before, "the forked recovery continuation must delete no stale record: {} -> " \
        "{}; its report: {}".format(before, after, report)
    assert cap is not None, "the acquirer recovers: {!r}".format(failed)
    release_operation(cap)
    _st_no_records(root, "T-f9-4")


def _st_f8_4_interrupted_close(dir_fd):
    """T-f8-4 (C): the write is interrupted, then the cleanup's close of the staging descriptor is
    interrupted after ownership was cleared and before os.close ran (fix round 9, codex LOW)."""
    baseline = _st_open_fds()
    armed = {}
    saved_write = _journal._write_all
    saved_close = os.close

    def _write_interrupted(fd, payload):
        armed["fd"] = fd
        raise KeyboardInterrupt()

    def _close_interrupted(fd):
        if fd == armed.get("fd") and "fired" not in armed:
            armed["fired"] = True
            raise KeyboardInterrupt()     # BEFORE the close: the descriptor stays open
        saved_close(fd)

    _journal._write_all = _write_interrupted
    os.close = _close_interrupted
    try:
        caught = _st_expect_interrupt(_create_control_file, dir_fd, "c.toml", b"x = 3\n",
                                      "probe record")
    finally:
        _journal._write_all = saved_write
        os.close = saved_close
    notes = " ".join(getattr(caught, "__notes__", ()))
    assert armed.get("fired"), "the cleanup's close must have been interrupted"
    os.fstat(armed["fd"])                 # observed OPEN: the close never ran
    assert _st_open_fds() - baseline == 1, "the interrupted close leaves that one descriptor open"
    assert "descriptor closed" not in notes and "UNCONFIRMED" in notes, notes
    os.close(armed["fd"])


def _t_f9_5_write_loop_continuation(d, env):
    """T-f9-5 (fix round 9, a class-width finding beside claude MED: a fork inside the write loop).
    In a bounded child with the deferral disabled, a SIGINT whose handler FORKS and RETURNS is sent
    at every line event of the shared full-write loop (_journal._write_all) during an acquisition;
    the parent's handler waits, so the continuation finishes the loop first. The loop is a
    Python-level step no pid gate can split, and the continuation writes through the SAME open
    file description, whose offset it shares, so the payload lands twice. The acquirer must never
    publish such a record: it either receives a capability whose two records hold exactly its
    payloads and release cleanly, or it fails and leaves no record behind. Before the fix the
    acquirer published a record holding its payload twice: release refused it and preserved it,
    and recovery could not parse it (a stuck lock needing manual removal)."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child_bounded(lambda: _st_f9_5_body(root), 600)
    assert failure is None, failure


def _st_f9_5_body(root):
    """T-f9-5's body, run in a bounded child."""
    sys.modules[__name__]._deferrable_signals = lambda: ()   # this child only: no deferral
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    top = os.getpid()
    record = {"events": []}
    _st_bounded_fork_handler(record, lambda: None, 60)
    funcs = [_journal._write_all]
    state = _st_arm_counted(funcs, 0)
    try:
        cap = acquire_operation(root, "op")
    finally:
        sys.settrace(None)
    release_operation(cap)
    total = state["seen"]
    assert total, "the sweep must see line events in the write loop"
    for k in range(1, total + 1):
        where = "T-f9-5 write-loop line event {}".format(k)
        baseline = _st_open_fds()
        record["events"] = []
        state = _st_arm_counted(funcs, k)
        try:
            try:
                cap = _st_forward(top, record, acquire_operation, root, "op")
            except OpLockError:
                cap = None
        finally:
            sys.settrace(None)
        assert state["fired"], where
        _, _, report = _st_fork_event(record, where)
        assert report.startswith("RAISED:"), \
            "a forked continuation never receives a capability ({}): {}".format(where, report)
        if cap is not None:
            for path, want in ((active, cap._active_bytes), (lease, cap._lease_bytes)):
                with open(path, "rb") as fh:
                    got = fh.read()
                assert got == want, "a published record must hold exactly its payload ({}): {} " \
                    "holds {} bytes, not {}".format(where, path, len(got), len(want))
            release_operation(cap)
        else:
            left = [p for p in (active, lease) if os.path.lexists(p)]
            assert not left, "a failed acquisition leaves no record ({}): {}".format(where, left)
        _st_settle(root, baseline, where)


def _t_f10_1_entry_continuation(d, env):
    """T-f10-1 (fix round 10, codex MED: a returning fork anywhere in the outer acquisition entry).
    In a bounded child, first with the production deferral and then with it disabled, a SIGINT
    whose handler FORKS and RETURNS is sent at every line event of acquire_operation's own frame,
    the public entry the earlier sweeps (T-f8-1, T-f9-3) started after; the parent's handler waits,
    so the child runs forward first. The entry identity is captured by the entry's FIRST statement,
    and the contract is bounded there: a fork at that statement's own line event precedes the
    capture, so that child is a separate caller, not a continuation (it may acquire and release on
    its own account, contending like any caller). At every LATER event the continuation must
    receive no capability and change none of the lock state across its life, and the acquirer then
    acquires and releases cleanly. Before the fix a fork at the containment probe or the nodename
    check, before a capture placed after them, gave the child a capability bound to its own pid,
    whose release returned normally."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    for deferral in (True, False):
        failure = _st_in_child_bounded(lambda x=deferral: _st_f10_1_body(root, x), 600)
        assert failure is None, "deferral {}: {}".format("on" if deferral else "off", failure)


def _st_f10_1_entry(top, record, root):
    """T-f10-1's call of acquire_operation(root, "op"): returned or raised as usual in `top`. A
    forked child reports how it ended through record["wfd"] and exits 0: "RAISED: ..." when
    refused, "SEPARATE CALLER released" when it received a capability bound to its OWN pid (a
    separate caller, which then releases it itself), and "GOT A CAPABILITY bound to pid N" when it
    received one bound to any other pid."""
    try:
        result = acquire_operation(root, "op")
    except BaseException as exc:
        if os.getpid() == top:
            raise
        report = "RAISED: {!r} {}".format(exc, " ".join(getattr(exc, "__notes__", ())))
    else:
        if os.getpid() == top:
            return result
        if result._acquirer_pid == os.getpid():
            try:
                release_operation(result)
                report = "SEPARATE CALLER released"
            except BaseException as exc:
                report = "SEPARATE CALLER release failed: {!r}".format(exc)
        else:
            report = "GOT A CAPABILITY bound to pid {}".format(result._acquirer_pid)
    try:
        _journal._write_all(record["wfd"], report.encode("utf-8", "replace"))
    except BaseException:
        os._exit(4)
    os._exit(0)


def _st_f10_1_body(root, deferral):
    """T-f10-1's body, run in a bounded child."""
    import inspect
    if not deferral:
        sys.modules[__name__]._deferrable_signals = lambda: ()   # this child only
    lines, first = inspect.getsourcelines(acquire_operation)
    capture = [first + i for i, text in enumerate(lines)
               if text.strip() == "acquirer_pid = os.getpid()"]
    assert len(capture) == 1, capture
    code = acquire_operation.__code__
    top = os.getpid()
    record = {"events": []}
    _st_bounded_fork_handler(record, lambda: _st_state(root), 60)

    def arm(k):
        """A REAL SIGINT at the k-th line event of acquire_operation's own frame (k 0: count)."""
        state = {"seen": 0, "fired": False, "line": None}

        def local(frame, event, arg):
            if event == "line" and not state["fired"]:
                state["seen"] += 1
                if state["seen"] == k:
                    state["fired"] = True
                    state["line"] = frame.f_lineno
                    sys.settrace(None)
                    os.kill(os.getpid(), signal.SIGINT)
            return local

        sys.settrace(lambda frame, event, arg: local if frame.f_code is code else None)
        return state

    state = arm(0)
    try:
        cap = acquire_operation(root, "op")
    finally:
        sys.settrace(None)
    release_operation(cap)
    total = state["seen"]
    assert total, "the sweep must see line events in acquire_operation"
    for k in range(1, total + 1):
        baseline = _st_open_fds()
        record["events"] = []
        state = arm(k)
        try:
            cap = _st_f10_1_entry(top, record, root)
        finally:
            sys.settrace(None)
        where = "T-f10-1 deferral {} line event {} (line {})".format(
            "on" if deferral else "off", k, state["line"])
        assert state["fired"], where
        before, after, report = _st_fork_event(record, where)
        if state["line"] == capture[0]:
            assert report.startswith("RAISED:") or report == "SEPARATE CALLER released", \
                "a fork before the identity capture is a separate caller ({}): {}".format(
                    where, report)
        else:
            assert report.startswith("RAISED:"), "a forked continuation of the entry never " \
                "receives a capability ({}): {}".format(where, report)
            assert before == after, "the continuation changed the lock state ({}): {} -> {}; " \
                "its report: {}".format(where, before, after, report)
        assert cap is not None and cap._acquirer_pid == top, where
        release_operation(cap)
        _st_settle(root, baseline, where)


def _t_f10_2_interrupted_fsync(d, env):
    """T-f10-2 (fix round 10, codex LOW: an interrupted post-unlink fsync). A KeyboardInterrupt is
    raised by the directory fsync right after a SUCCESSFUL verified unlink (the fsync never ran):
    (A) in the unreturned capability's release (a real SIGINT during the publication, deferred,
    whose handler arms the fault), after the lease's unlink and, separately, after the active
    record's; (B) in the acquisition unwind after the lease's unlink. The completed unlink must be
    recorded as a removal whose durability is UNCONFIRMED, never as a record that was not removed;
    the ORIGINAL interruption (the handler's) must be the one that propagates from (A); and the
    conservative order holds (a lease whose removal is not confirmed durable keeps the active
    record). Before the fix the interruption escaped before the removal was recorded: (A) noted
    "neither of its records removed" with the lease gone and "its lease removed but not its active
    record" with both gone, and (B) noted "the lease was not removed" with the lease gone."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    for which in ("lease", "active record"):
        failure = _st_in_child(lambda w=which: _st_f10_2_unreturned(root, w))
        assert failure is None, "{}: {}".format(which, failure)
        for path in (os.path.join(_st_ctl_dir(root), ACTIVE_NAME), _st_lease_path(root)):
            if os.path.exists(path):
                os.unlink(path)           # the exited child's kept record, cleared by hand
    failure = _st_in_child(lambda: _st_f10_2_unwind(root))
    assert failure is None, failure


def _st_interrupt_dir_fsync(path, armed):
    """Replace os.fsync so that, while armed["on"] is set, the next fsync of the directory at `path`
    raises KeyboardInterrupt WITHOUT running (once). Returns the original os.fsync."""
    target = os.stat(path)
    saved = os.fsync

    def _fsync(fd):
        st = os.fstat(fd)
        if armed.get("on") and (st.st_dev, st.st_ino) == (target.st_dev, target.st_ino):
            armed["on"] = False
            raise KeyboardInterrupt("interrupted directory fsync (T-f10-2)")
        return saved(fd)

    os.fsync = _fsync
    return saved


def _st_f10_2_unreturned(root, which, stat_fails=False):
    """T-f10-2 (A), one record, run in a child. With `stat_fails` (T-f11-1), the no-follow stat
    that observes the record's name after the interrupted fsync fails with EIO, once."""
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    where = os.path.join(root, ".working", "toml") if which == "lease" else _st_ctl_dir(root)
    name = _opf_check.LEASE_NAME if which == "lease" else ACTIVE_NAME
    armed = {}
    saved_stat = os.stat

    def _stat(path, *a, **k):
        if armed.get("stat") and path == name and k.get("dir_fd") is not None:
            armed["stat"] = False
            raise OSError(errno.EIO, "simulated no-follow stat failure (T-f11-1)")
        return saved_stat(path, *a, **k)

    def handler(signum, frame):
        armed["on"] = True
        raise KeyboardInterrupt("the deferred signal (T-f10-2)")

    signal.signal(signal.SIGINT, handler)
    saved = _st_interrupt_dir_fsync(where, armed)
    if stat_fails:
        fsync = os.fsync

        def _fsync_then_stat(fd):
            try:
                return fsync(fd)
            except KeyboardInterrupt:
                # The observation that follows the interruption fails. Installed only now:
                # replacing os.stat before acquire_operation would fail its containment probe.
                armed["stat"] = True
                os.stat = _stat
                raise

        os.fsync = _fsync_then_stat
    baseline = _st_open_fds()
    state = _st_arm_signal(_st_named("_publish_staged"), 1)
    try:
        caught = _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        sys.settrace(None)
        os.fsync = saved
        os.stat = saved_stat
    assert state["fired"], which
    notes = " ".join(getattr(caught, "__notes__", ()))
    assert caught.args == ("the deferred signal (T-f10-2)",), \
        "the original interruption propagates: {!r}".format(caught)
    assert _st_open_fds() == baseline and _st_anchor_free(root), notes
    assert "neither of its records removed" not in notes, notes
    assert "UNCONFIRMED" in notes and "released only in part" in notes, notes
    if stat_fails:
        assert not armed.get("stat"), "the failing observation must have been reached"
        if which == "lease":
            assert not os.path.exists(lease) and os.path.exists(active), "the lease went"
            assert "its lease's removal UNCONFIRMED" in notes \
                and "its active record not removed" in notes, notes
        else:
            assert not os.path.exists(lease) and not os.path.exists(active), "both records went"
            assert "its lease removed; its active record's removal UNCONFIRMED" in notes, notes
            assert "but not its active record" not in notes, notes
    elif which == "lease":
        assert not os.path.exists(lease) and os.path.exists(active), "the lease went; kept active"
        assert "its lease (" in notes and "removed but not its active record" in notes, notes
    else:
        assert not os.path.exists(lease) and not os.path.exists(active), "both records went"
        assert "its lease and its active record (" in notes, notes
        assert "but not its active record" not in notes, notes


def _st_f10_2_unwind(root):
    """T-f10-2 (B), run in a child."""
    mod = sys.modules[__name__]
    active = os.path.join(_st_ctl_dir(root), ACTIVE_NAME)
    lease = _st_lease_path(root)
    armed = {}
    saved_cap = mod.OpCapability

    class _BoomCap:
        def __init__(self, *a, **k):
            armed["on"] = True
            raise OSError(errno.EIO, "simulated post-creation failure (T-f10-2 unwind)")

    saved = _st_interrupt_dir_fsync(os.path.join(root, ".working", "toml"), armed)
    mod.OpCapability = _BoomCap
    try:
        caught = _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        mod.OpCapability = saved_cap
        os.fsync = saved
    notes = " ".join(getattr(caught, "__notes__", ()))
    assert not os.path.exists(lease) and os.path.exists(active), "(B) conservative order"
    assert "the lease was not removed" not in notes and "UNCONFIRMED" in notes, notes
    os.unlink(active)
    assert _st_anchor_free(root), "(B) the unwind still gives up the lock"


def _t_f10_3_anchor_close_attributed(d, env):
    """T-f10-3 (fix round 10, claude LOW: the anchor clause states only a recorded outcome). In a
    child, a REAL SIGINT sent during the publication is deferred and delivered after the capability
    is built; its handler arms a non-signal injection (a line trace, the self-tests' own
    mechanism) at the first line of _FdOwner.close, then raises KeyboardInterrupt. The injection
    lands in the release scope exit's close of the anchor descriptor BEFORE its ownership is
    cleared, so os.close never ran on it. The anchor's close must still be made and attributed:
    the anchor ends free, no descriptor leaks, and the note says the anchor descriptor closed,
    never that no close of it completed. Before the fix the anchor was closed, unattributed, by
    the general closes, while the note read "(no close of its anchor descriptor completed here)"."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    failure = _st_in_child(lambda: _st_f10_3_body(root))
    assert failure is None, failure


def _st_f10_3_body(root):
    """T-f10-3's body, run in a child (it installs its own SIGINT handler)."""
    armed = {}

    def handler(signum, frame):
        armed["state"] = _st_arm_interrupt(_st_named("_FdOwner.close"), 1)
        raise KeyboardInterrupt("the deferred signal (T-f10-3)")

    signal.signal(signal.SIGINT, handler)
    baseline = _st_open_fds()
    state = _st_arm_signal(_st_named("_publish_staged"), 1)
    try:
        caught = _st_expect_interrupt(acquire_operation, root, "op")
    finally:
        sys.settrace(None)
    assert state["fired"] and armed["state"]["fired"], "both the signal and the injection fired"
    notes = " ".join(getattr(caught, "__notes__", ()))
    assert caught.args == ("the deferred signal (T-f10-3)",), repr(caught)
    assert _st_open_fds() == baseline, "no descriptor may leak: {}".format(notes)
    assert _st_anchor_free(root), "the anchor's close ran (observed free)"
    assert "no close of its anchor descriptor completed" not in notes, notes
    assert "its anchor descriptor closed (releasing its hold on the lock)" in notes, notes
    _st_no_records(root, "T-f10-3")


def _t_f11_1_unobservable_removal(d, env):
    """T-f11-1 (fix round 11, codex and claude LOW: each record's outcome rendered on its own). As
    T-f10-2 (A), but the interrupted directory fsync is followed by a FAILING no-follow stat of the
    record's name (EIO), so the release cannot observe whether the unlink removed it (the
    `unlinked is None` path): for the lease, and separately for the active record. The note must
    name each record's own outcome (removed, removal UNCONFIRMED, or not removed) and never assert
    that a record whose removal is UNCONFIRMED was not removed. Before the fix the active-record
    case read "its lease removed but not its active record ... (whether its active record was
    removed is UNCONFIRMED ...)", with both records in fact gone."""
    root = _st_git_store(d, "repo", env)
    release_operation(acquire_operation(root, "op"))
    for which in ("lease", "active record"):
        failure = _st_in_child(lambda w=which: _st_f10_2_unreturned(root, w, stat_fails=True))
        assert failure is None, "{}: {}".format(which, failure)
        for path in (os.path.join(_st_ctl_dir(root), ACTIVE_NAME), _st_lease_path(root)):
            if os.path.exists(path):
                os.unlink(path)           # the exited child's kept record, cleared by hand


def self_test():
    """Regression roster (plan section (e)): the PR2 T-c/T-crit/T-med/T-low roster PLUS the PR2
    round-3 recovery-liveness witnesses (T-r3-live-recover-refuses, T-r3-dead-recover-proceeds,
    T-r3-crosshost-refuses), the LOW-4 coverage tests (T-c2-dirperms, T-c3-companion, T-h4-quote,
    T-c6-diffinode), the LOW-1 torn-write witness (T-low1-torn), and the recovery-hardening
    witnesses T-d1 to T-d5 (DEF-1 to DEF-5), and the round-2 witnesses T-r2-1 to T-r2-5 (recovery
    delete order, cross-worktree pairing, store-root close, interrupted write, walk hand-off), and
    the fix-round-3 witnesses T-f3-1 to T-f3-8 (atomic publication under SIGKILL, lease-first
    release order, descriptor reuse, release and acquisition interruptions, the shared walker
    hand-off, stale pairing by the recorded path, the empty-nodename refusal), and the fix-round-4
    witnesses T-f4-1 to T-f4-3 (failed or interrupted lease publication, and line-trace
    interruption sweeps over release and acquisition, T-f4-3 extended in round 5 to the smaller
    helpers with a real signal), and the fix-round-5 witnesses T-f5-1 to T-f5-4 (a real signal at
    every line of the acquisition, the release, and recovery is deferred until after the section,
    and records published under a restrictive umask), and the fix-round-6 witnesses T-f6-1 and
    T-f6-2 (restartable initialization after a kill inside the creation window, and the unreturned
    capability's release with no identity re-read), and the fix-round-7 witnesses T-f7-1 to T-f7-4
    (a forked child never releases, unlocks, or removes what its acquirer holds, including at every
    line of the acquisition and release with no deferral; restartable initialization beneath a
    set-group-ID parent; the unreturned release's note states only the steps that completed), and
    the fix-round-8 witnesses T-f8-1 to T-f8-5 (a forked continuation receives no capability; a
    concurrent release of one capability is refused holding nothing; a removal whose directory
    fsync failed is reported as not durable, never as not performed; a publication note claims only
    a close that ran; the set-group-ID contract matches the implementation), and the fix-round-9
    witnesses T-f9-1 to T-f9-5 (a fork during a contended release claim never waits on the
    inherited lock; a forked continuation of a release or unwind never frees the acquirer's lock,
    which is released only by closing; a forked publication continuation never strands a lone
    owner-less lease; a forked recovery continuation deletes nothing and its refusal claims only
    its own acts; a fork inside the write loop never publishes a doubled record; and, fix round 10,
    T-f10-1 to T-f10-3: a fork anywhere in the acquisition entry after its identity capture gets no
    capability, an interrupted post-unlink fsync is noted as an unconfirmed removal, and an
    interrupted anchor close is retried and attributed; T-f11-1, fix round 11: an unobservable
    interrupted removal is noted per record, never as not removed; T-f7-4 and T-f8-4
    extended to interrupted closes), each a witness against a named defect. A
    missing containment primitive or git binary is a REFUSAL (non-zero),
    never a clean skip. The git fixtures are pinned hermetically (LOW-5). The restrictive-umask
    witnesses (T-f5-4, T-f6-1, T-f7-3) run under a fixture root probed to honour the umask (a
    default ACL on TMPDIR overrides it), falling back to /dev/shm; with no such root they print
    SKIP with the reason, and the run ends SELF-TEST INCOMPLETE with exit 3, never a pass."""
    import tempfile
    import traceback

    if not _containment.probe():
        print("REFUSED: race-free containment primitive absent; the operation lock cannot be "
              "exercised safely (fail-closed, non-zero)")
        return 2
    if shutil.which("git") is None:
        print("REFUSED: git binary not found; the self-test requires real git stores "
              "(fail-closed, non-zero)")
        return 2

    tests = (
        ("T-c1 authoritative common git dir (worktree, scrubbed env, bogus gitdir)",
         _t_c1_common_dir_authority),
        ("T-c2 anchor validation and persistence", _t_c2_anchor_validation),
        ("T-c2-dirperms group/other-writable control dir refuses", _t_c2_dirperms),
        ("T-crit1 anchor unlink/recreate is caught, never a second holder",
         _t_crit1_anchor_swap),
        ("T-c3/T-c11 mandatory lease, exact checked-encoder bytes, owner identity",
         _t_c3_c11_mandatory_lease_and_bytes),
        ("T-c3-companion two product roots, one store, one shared anchor", _t_c3_companion),
        ("T-c4 link-count check is file-only", _t_c4_dir_nlink_free),
        ("T-c5 stale refuses; lone lease is fail-closed even under recovery",
         _t_c5_stale_and_recovery),
        ("T-r3-live recover REFUSES a live holder (never a second holder)",
         _t_r3_live_recover_refuses),
        ("T-r3-dead recover PROCEEDS on a confirmed-dead holder", _t_r3_dead_recover_proceeds),
        ("T-r3-crosshost recover REFUSES a foreign-host holder", _t_r3_crosshost_refuses),
        ("T-c6/T-c7/T-med6 verified release preserves mismatches, legs collected",
         _t_c6_c7_med6_verified_release),
        ("T-c6-diffinode same-bytes inode swap is preserved", _t_c6_diffinode),
        ("T-h4-quote a double-quoted holder round-trips intact", _t_h4_quote),
        ("T-c9 release is bound to the acquirer identity", _t_c9_acquirer_identity),
        ("T-c10 three-way .git classification, no fallback", _t_c10_git_classification),
        ("T-c12 contention and double release refuse", _t_c12_contention_and_double_release),
        ("T-c13 FIFO control names cannot block or pass", _t_c13_fifo_control_names),
        ("T-c8/T-c14 nested-lock scope-out (PR3)", _t_c8_c14_scope_out),
        ("T-low1 single-sourced field validation", _t_low1_field_validation),
        ("T-low1-torn a failed control write strands no file", _t_low1_torn_write),
        ("T-d1 (DEF-1) live holder B swapped in after the liveness gate is never seized",
         _t_d1_swap_after_liveness_gate),
        ("T-d2 (DEF-2) missing holder fields are validated, not compared as None == None",
         _t_d2_missing_holder_fields),
        ("T-d3 (DEF-3) EIO in a release/unwind leg is collected and the unlock still runs",
         _t_d3_eio_release_and_unwind),
        ("T-d4 (DEF-4) git-common-dir strips only the record terminator",
         _t_d4_gitdir_trailing_space),
        ("T-d5 (DEF-5) a failed torn-record cleanup names the stranded leftover",
         _t_d5_failed_cleanup),
        ("T-r2-1 a crash between the recovery deletes leaves a recoverable lone active record",
         _t_r2_1_crash_between_recovery_deletes),
        ("T-r2-2 recovery from a sibling worktree refuses; legacy schema-1 refuses",
         _t_r2_2_cross_worktree_recovery),
        ("T-r2-3 an EIO store-root close runs the full unwind (no leaked anchor)",
         _t_r2_3_store_fd_close_eio),
        ("T-r2-4 a KeyboardInterrupt mid-write strands no torn record or descriptor",
         _t_r2_4_interrupt_mid_write),
        ("T-r2-5 a failing machine-store walk close leaks and double-closes nothing",
         _t_r2_5_machine_walk_close),
        ("T-f3-1 (D1) a SIGKILL mid-publication leaves a record recovery can clear",
         _t_f3_1_sigkill_mid_publication),
        ("T-f3-2 (D3) a failed lease removal keeps the active record (release and unwind)",
         _t_f3_2_lease_removal_failure_keeps_active),
        ("T-f3-3 (D2) a failed control-root close never closes a reused descriptor number",
         _t_f3_3_control_root_close_fd_reuse),
        ("T-f3-4 (D2) an interrupted release still unlocks and closes every descriptor",
         _t_f3_4_release_interruptions),
        ("T-f3-5 (D2) an interrupted acquisition leaks no descriptor",
         _t_f3_5_acquire_interruptions),
        ("T-f3-6 (D2) the shared no-follow walker hands its descriptor off safely",
         _t_f3_6_shared_walker_handoff),
        ("T-f3-7 (D4) stale pairing is decided by the recorded machine-store path",
         _t_f3_7_stale_pairing_recorded_path),
        ("T-f3-8 (D6) an empty host nodename refuses acquisition", _t_f3_8_empty_nodename_refuses),
        ("T-f4-1 (H1) a failed or interrupted lease publication never strands a lone lease",
         _t_f4_1_failed_lease_publication),
        ("T-f4-2 (H2) an interruption at any release line leaks nothing and frees the anchor",
         _t_f4_2_release_interruption_sweep),
        ("T-f4-3 (M, H1) an interruption at any acquisition line leaks nothing and strands nothing",
         _t_f4_3_acquire_interruption_sweep),
        ("T-f5-1 a real signal anywhere in the acquisition is delivered after it, leaking nothing",
         _t_f5_1_signal_acquisition),
        ("T-f5-2 a real signal anywhere in the release is delivered after it, leaking nothing",
         _t_f5_2_signal_release),
        ("T-f5-3 a real signal anywhere in recovery is delivered after it, leaking nothing",
         _t_f5_3_signal_recovery),
        ("T-f5-4 records published under a restrictive umask stay releasable and recoverable",
         _t_f5_4_restrictive_umask),
        ("T-f6-1 a first acquisition killed before its mode fix is repaired, never stuck",
         _t_f6_1_restartable_init),
        ("T-f6-2 an unreturned capability is released without a /proc read, truthfully noted",
         _t_f6_2_unreturned_release),
        ("T-f7-1 a forked child never releases the acquirer's unreturned capability",
         _t_f7_1_forked_unreturned),
        ("T-f7-2 a forked child's cleanup at any acquisition or release line changes no lock state",
         _t_f7_2_forked_cleanup_sweep),
        ("T-f7-3 a set-group-ID parent's inherited bit is repaired with, never refused",
         _t_f7_3_setgid_restartable),
        ("T-f7-4 the unreturned release's note states only the steps that completed",
         _t_f7_4_unreturned_note_truth),
        ("T-f8-1 a forked acquisition continuation receives no capability and changes nothing",
         _t_f8_1_forked_continuation),
        ("T-f8-2 a concurrent release of one capability is refused, closing no reused descriptor",
         _t_f8_2_concurrent_release),
        ("T-f8-3 a removal whose directory fsync failed is noted as not durable, not as not done",
         _t_f8_3_not_durable_removal),
        ("T-f8-4 a publication note claims a descriptor close only when the close ran",
         _t_f8_4_publication_close_truth),
        ("T-f8-5 the set-group-ID contract matches what the validation and the repair do",
         _t_f8_5_setgid_contract),
        ("T-f9-1 a fork during a contended release claim never waits on the inherited lock",
         _t_f9_1_claim_fork),
        ("T-f9-2 a forked continuation of a release or unwind never frees the acquirer's lock",
         _t_f9_2_release_by_close),
        ("T-f9-3 a forked publication continuation never strands a lone owner-less lease",
         _t_f9_3_publication_continuation),
        ("T-f9-4 a forked recovery continuation deletes nothing; its refusal claims only its acts",
         _t_f9_4_recovery_continuation),
        ("T-f9-5 a fork inside the write loop never publishes a record holding its payload twice",
         _t_f9_5_write_loop_continuation),
        ("T-f10-1 a fork anywhere in the acquisition entry after its identity capture gets nothing",
         _t_f10_1_entry_continuation),
        ("T-f10-2 an interrupted post-unlink fsync is noted as unconfirmed, never as no removal",
         _t_f10_2_interrupted_fsync),
        ("T-f10-3 an interrupted anchor close is retried while still owned, and attributed",
         _t_f10_3_anchor_close_attributed),
        ("T-f11-1 an unobservable interrupted removal is noted per record, never as not removed",
         _t_f11_1_unobservable_removal),
    )

    base = os.path.realpath(tempfile.mkdtemp(prefix="opf-oplock-selftest-"))
    # LOW-5: pin the git fixtures AND the production rev-parse hermetically. Bind HOME and
    # XDG_CONFIG_HOME (which production _git_common_dir keeps, scrubbing only GIT_*) plus
    # GIT_CONFIG_GLOBAL/SYSTEM into the per-run temp dir, and restore them afterwards, so no ambient
    # user or system git config can affect a fixture command or the module's own rev-parse.
    _saved_env = {}
    for _k in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
               "GIT_CONFIG_NOSYSTEM"):
        _saved_env[_k] = os.environ.get(_k)
    os.environ["HOME"] = base
    os.environ["XDG_CONFIG_HOME"] = os.path.join(base, "xdg")
    os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(base, "gitconfig-global")
    os.environ["GIT_CONFIG_SYSTEM"] = os.path.join(base, "gitconfig-system")
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    os.makedirs(os.path.join(base, "xdg"), exist_ok=True)
    failures = []
    skipped = []
    try:
        env = _st_git_env(base)
        for index, (name, fn) in enumerate(tests):
            d = os.path.join(base, "t{:02d}".format(index))
            os.mkdir(d)
            try:
                fn(d, env)
                print("PASS  {}".format(name))
            except _StSkip as exc:
                skipped.append(name)
                print("SKIP  {}: {}".format(name, exc))
            except BaseException:
                failures.append(name)
                print("FAIL  {}".format(name))
                traceback.print_exc()
    finally:
        for _k, _v in _saved_env.items():
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL: {} of {} tests failed: {}".format(
            len(failures), len(tests), "; ".join(failures)))
        return 1
    if skipped:
        # a skipped check never executed, so the run is not a pass: exit non-zero (fail closed)
        print("SELF-TEST INCOMPLETE: {} of {} tests SKIPPED, not passed: {}".format(
            len(skipped), len(tests), "; ".join(skipped)))
        return 3
    print("SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    sys.exit("usage: _opf_oplock.py --self-test")
