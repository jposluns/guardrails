#!/usr/bin/env python3
"""OPF adoption apply engine: the apply SHELL (slice 1), the three file ops (slice 2), init-store (slice 3), the two registration ops (slice 4), the three finish ops (slice 5) and the enable-hook op (OPF-SPEC 1.3.0).

Slice 1 (the clean-start adoption track's first apply unit) supplies the engine SKELETON, shaped by spec
1.3.0 sections 4.2, 5.7 and 14: run identity; the evidence-bundle, archive and Move homes, all
derived from the `_opf_store` homes constructors (never re-spelled here); the `opf.evidence.inventory/v1`
inventory DERIVED from the run's own transaction op list and published in that SAME transaction as the
retained bytes it lists (spec 4.2), a base `inventory.toml` then one `inventory-<phase>.toml` per later
phase, never rewritten; the homes-1 bundle verification the completion checks carry themselves while
C-EVIDENCE-ENUM is inactive (spec 14.1), which RE-READS the inventories and payload digests from disk;
the preserve-first composition of spec 14.2; live re-observation of every operand; one journaled
transaction per (run, phase), reconcile-first; and a dispatch table keyed by the closed eleven-op
ADOPT_OPS vocabulary. Slice 2 makes the three pure file ops executable (EXECUTABLE_OPS: create-file,
move-file, retire-file), staged as spec 14.1 and 14.2 order them: the run's base transaction is the APPLY
stage, which creates a planned file, takes a non-occupying retire source's retirement preimage while the
source stays frozen in place, and archives an occupying retire or move source preserve-first; the run's
RETIREMENT_PHASE transaction, which the stage driver runs only after the green completion check, is the
RETIREMENT stage, which removes a frozen retire source once the retirement preimage the committed base
published re-verifies, relocates a frozen non-occupying move source (live bytes re-verified against the
plan digest; the base publishes nothing for it), and relocates an occupying move source from the archive
copy the committed base published, re-verified. Every preserved copy and relocated file keeps its
source's mode bits exactly. Slice 3 lands init-store, composed over the coupled-init substrate
(_init_store). Slice 5 makes the three finish ops executable: plant-governance (create-only
planting of a pack member that passed the b.5 trust gate, verify_pack_member), render-views (create-only view
publication of the render engine's planned bytes, composed into the journaled transaction) and record-adoption (the immutable receipt core and its genesis outcome event in the
run's own evidence bundle, adoption_record). PR3 slice 2 makes `enable-hook` executable, in the run's
base (apply-stage) transaction only, as does slice 4 with the two structured-edit ops (_REGISTRATION_OPS:
register-unmanaged, repoint-consumer). The stage driver
adds the plan-v2 apply-input gate, the one approval and the apply stage (spec 14.1): `opf adopt approve`
re-proves a frozen plan from its own bytes, re-derives it over the live tree and prints the approval
binding its plan_digest and inventory_digest, writing nothing; `opf adopt apply` admits only that approved
pair, persists both in the run's evidence bundle within the run's one base transaction, and dispatches
every plan op through the table and then the driver's mandatory receipt stage (which mints the
record-adoption row and assembles the receipt core), so while any plan op or the driver's mandatory
receipt stage is unlanded, apply refuses before anything is written. Every other op returns a
refusing not-yet-executable verdict: pack installation (install-pack), the completion checks, the stage
driver's retirement stage (it opens the RETIREMENT_PHASE transaction the file ops compose into only after
a green completion check, and no adoption interface opens that transaction in this build), and the
`complete` and `reconcile` CLI subcommands remain later slices, as does the driver's receipt stage; the
read-only `opf adopt` subcommands plan and status shipped with K9a. Live outside the self-test fixtures
today: `opf adopt status` opens and lists the evidence home in opf.py through the _journal containment
primitives, then grades each listed bundle through this module's _verify_bundle_at (beneath the HELD home
descriptor it is passed) and the journal through journal_state, with _open_product_root anchoring both
reads to one product-root descriptor; plan, approve and apply refuse over a non-clean adoption journal
(require_clean_journal); the transaction shell and the dispatch table are reachable from `opf adopt
apply` only past the unlanded-op gate and the driver's mandatory receipt stage (unlanded in this build),
which no plan passes, and reconcile() and compose_rows stay reachable only from the self-test and the
crash children it starts in a fresh interpreter through the `--selftest-child` flag (the
`_opf_init_operation` crash-child precedent), a self-test harness entry, never an adoption
interface, which runs whatever spec it is handed without an approval.

Preserve-first (spec 14.2), enforced over the composed op list BEFORE any transaction opens: a live file
is removed, OR OVERWRITTEN BY A `write` (which destroys the live bytes exactly as a removal does), ONLY
as the second half of a pair whose first half, earlier in the SAME transaction, creates its byte-identical
archive copy at `.working/archive/adoption/<run-id>/<source-path>`. The copy's create op carries the plan
digest as its content digest, which the journal verifies against the staged bytes, RE-READS from the
written destination and digest-verifies again (a spec 14.2 verification checkpoint), and fsyncs (file and
parent) before the next op runs, and the removal or write carries the same digest and mode as a
`source-poststate` pin, which the journal verifies at preimage capture under its lock. So the copy is
verified on disk and durably committed before the source is destroyed, never reversed and never split
across transactions, and a pre-commit abort (the journal's reverse-order rollback) restores the source
from its digest-checked preimage, RE-READS and digest-verifies the restored live bytes, and only then
discards the copy or reports a prestate (the rollback-side checkpoint). A source whose live bytes no
longer match its plan digest is drifted and is never archived or removed. A non-occupying source takes
only its retirement preimage at apply and stays frozen in place; its removal waits for the green
completion check and runs in the RETIREMENT_PHASE transaction, where a removal pairs instead with the
archive copy the run's COMMITTED base transaction published (its INTENT digest, re-read live before
anything is composed) or, for a move, with the create of its destination earlier in that same
transaction. An `rmdir` may target ONLY a directory this same transaction created; a
pre-existing live directory is never removed by this shell. No protected destination
(`_opf_adopt.protected_destination`, the ONE predicate the planner shares, its names compared
case-insensitively: a path that is not normalized and contained; a `.git` or `.aiqt` component at any
depth, the adoption journal's own tree included; the product-root pointers; the store control area; the
store tree's `.gitignore`) is an apply operand of ANY kind, save the mkdirs leading to this run's own
homes. The adoption archive and every evidence bundle are immutable: an op may only create beneath this
run's own archive, this run's own bundle, or the Move root, never write, remove, or rmdir anything under
`.working/archive/` or `.working/imported/`, and never create in another run's home.

The adoption journal root is `.aiqt/adopt/journal` at the PRODUCT root: the homes-1 legacy journal family
of `.aiqt/record/journal` and `.aiqt/import/journal`. It is anchored there, never under the store, because
recovery of an interrupted apply MUST resolve from the apply journal alone and MUST NOT require the live
tree to resolve as a store (spec 14.2): an apply that archived and removed an occupying manifest leaves no
store to resolve. reconcile() therefore never resolves the store. Homes 2 relocates transaction records
under `.working/journals/adoption/` and forbids OPF state under `.aiqt/` (spec 4.2, 14.2); homes 2 is not
activated in this build (SUPPORTED_HOMES = 1).

Journal discipline (reconcile-first, the `opf record` step-1 posture): every transaction inspects the
adoption journal FIRST and REFUSES on a held lock (never seized) or any open (INTENT-without-terminal)
transaction. Recovery is the EXPLICIT reconcile() entry: it rolls an open transaction forward when every
poststate already verifies, else back from its durable preimages (`_journal.recover`), under the journal's
own lock, breaking a confirmed-dead owner's lock only through `_journal.reconcile_and_claim_stale`; the
interrupted run stays refused. A transaction is named by its run (base) or run.phase, so one run has at
most one base transaction and COMMITS at most one transaction per phase: a second base attempt, and any
phase attempt once one has committed, refuses before it opens, and changing approved work takes a fresh
plan with its own run id (spec 14.1). A phase attempt that ended terminal WITHOUT committing (rolled back,
by its own pre-commit abort or by reconcile() after an interruption, or never opened) is retained in the
journal as it stands, and a retry of that phase opens as the next attempt, `<run>.<phase>.attempt-<n>`,
bound to the same committed base and its preservation exactly as the first attempt was
(_next_attempt_or_refuse). The base is never retried: nothing binds to a base that has not committed, so a
base that did not commit takes a fresh plan with its own run id.

Stage driver (spec 14, 14.1): a plan is admitted only as its own canonical bytes whose plan_digest
re-seals and which the schema grades VALID; the approval is the receipt's APPROVAL_REQUIRED shape, its
plan_digest and inventory_digest equal to the plan's and its approved_at no earlier than the instant the
plan froze. Freshness is two observations: the live product revision (observe_revision, one read-only
git query) must equal the plan's bound revision, and re-derivation, the planner re-run over the live tree
with the worksheet that froze the plan and the plan's own run instant and nonce, must reproduce the plan
byte for byte; a moved revision, a changed observed item (sources, targets, store identity, ancestry) or
a changed worksheet refuses into a fresh plan with its own single approval. Replay admission precedes
freshness: a run whose base transaction exists refuses on the one-apply rule itself. Apply dispatches
every plan op in plan order in the apply stage through one context table (`ops`, the transaction's
ApplyOps; `plan`; `approval`; `product_root`; `stage`), the seam the op slices compose through, then
composes the driver's mandatory receipt stage (DRIVER_STAGES; spec 14 ends apply with the receipt and its
outcome-event chain, which no plan row can carry), and refuses any composed remove or write of a plan
source that occupies no managed destination: such a source stays frozen in place until the retirement
stage after a green completion check, and apply takes only its preimage. The driver, not handler
convention, enforces the composition rules: handlers run under a per-thread composition guard that
refuses any direct filesystem or process effect and any transaction of their own (so a slice composes
into `ops`, init-store's substrate included), and composition first runs as a write-free preflight
before the journal is prepared, so a preflight refusal writes nothing at all; a refusal of the second
composition, under the journal lock, releases the lock and removes the journal directories the run
created, so it too leaves the tree as it found it, unless a release or a removal fails or may not be
durable (a directory a concurrent run has populated meanwhile stays): the refusal then names each such
leftover in place of "nothing written". Disclosed:
digests bind the approval to one plan, never the actor's authenticity (self-asserted identity and
same-user tampering stay spec 14.1 residuals); the release, prompt_pack, enforcement and skip_policy
bindings are worksheet-asserted, so re-derivation proves the worksheet still freezes the plan, not that
the tool release or packs in use match it (the trust-verification slice observes them); the composition
guard is an audit hook, not a sandbox (an already-open writable descriptor, ctypes, a thread already
running before composition, since starting one is refused, or an unaudited entry point stays outside
it); the re-derivation and the transaction are not one snapshot, so each op re-observes its own operands
under the journal lock; one run takes one base transaction, so an approved
plan applies at most once.

Single-writer lease (spec 5.7): this slice carries NO lease join, so a transaction REFUSES, before writing
anything, when the product root resolves a store (RESOLVED) or when a pointer names a store outside the
product root, and EVERY other store posture that cannot be evaluated (a malformed or unreadable pointer,
multiple machine stores, an undiscoverable root) refuses too (spec 14.2: an unreadable declaration or detected
input fails closed). Only the two first-adoption states adoption exists for are admitted: NOT-ADOPTED, and a
present `.working/` at the DEFAULT location carrying no valid manifest, re-proved by a fresh discovery. A
first adoption has no store and so no lease home; the pre-store single-writer control is THIS journal's
lock, which init-store holds across the whole substrate run (one lock shared by both writers; the
substrate's operation mutex is taken inside it), inside its own durable-intent transaction whose
journal-only recovery is the op's declared reversal (see _init_store). The coupled-init substrate admits
the plan-enumerated control-area homes and frozen non-occupying sources beneath `.working/` through its
admitted-inventory seam, re-verified under its mutex and recorded in its immutable plan; plain `opf init`
and the bare substrate stay unweakened (an existing `.working/` still refuses a plain fresh init exactly
as before, pinned by the substrate's own self-test). Disclosed residuals of these slices, none of them a
relaxation: init.toml's member digest is never plan-bindable (it embeds the operation id, time and HEAD),
so the row binds its PATH with the explicit all-zero unbound marker and the live bytes are verified
against the operation's validated recorded plan instead; the context plan's approval binding (plan_digest
against the captured approval) and full plan-schema validation are the stage driver's, not init-store's,
which validates exactly the fields it consumes; bundle MEMBERSHIP (an off-inventory file inside a bundle)
is not reconciled by the shell's verifier, only listed payloads — init-store's admitted-inventory
derivation DOES refuse an unlisted file beneath the bundle home; containment registration of the archive,
Move and evidence homes on homes 1 is part of the 1.3.0 activation, not this slice; investigation does
not read this journal (the planner's ancestry disclosure lists durable OPF history outside .working, and
`.aiqt/adopt/journal` is one more such home), so the stage driver refuses plan, approve and apply over a
non-clean adoption journal (require_clean_journal); the shipped `retire-file` vocabulary row declares a
single `remove`, while spec 1.3.0 preserves the retirement preimage at apply and removes only after the green check, so the file ops
split each retire and move row across the two stages, and the base transaction's create of the archive
copy is the declared row's unlisted first half; the RETIREMENT_PHASE transaction refuses on a resolved
store exactly as every transaction here does (no lease join yet), so once init-store executes, a real
adoption's retirement stage refuses until the lease join lands; the retirement stage is bound to the plan
rows it is handed, not to the rows the base ran (the base INTENT records ops, not plan rows), so binding
both stages to the one approved plan is the stage driver's, through the approval it captures; while a
retirement attempt is rolled back and not yet retried, the committed base's frozen sources stay frozen in
place, but an OCCUPYING move source, which the base archived and removed, exists only in this run's archive
(neither at its source nor at its destination) until a retry relocates it (when to retry is the stage
driver's; the engine admits the retry); a frozen retire source whose MODE alone changed after apply refuses
its retirement (its preserved copy keeps the apply-time mode and a removal pairs only at that mode, which
is stricter than spec 14.1's bytes-only drift rule; restoring the mode, or a fresh plan, clears it), while
a frozen move source relocates with its live mode; and occupancy is the plan row's fact, never re-derived
from the tree, so a planner
misclassification of a non-occupying source as occupying archives and removes it at apply (its bytes
preserved byte-exact) rather than leaving it frozen until the green check.
The shell's interruption is exercised in-process through the journal's kill-point seam AND by real
process death: the file-ops kill-injection matrix (the migrate.py crash-harness model) runs each
stage's transaction in a child interpreter that os._exit()s at every kill point the transaction
reaches and mid-write in every payload it creates, then reconciles in a fresh process, breaking the
dead owner's stale lock, and lands the tree on exactly its prestate or its verified poststate, the
rollback leg killing the recovery itself at every restore point; init-store's own kill-injection
SIGKILLs the whole engine in a child dispatch mid-publication, under the held lock and open
transaction (it is not exercised for enable-hook or the registration ops of slice 4).
Named residual (an exception in a descriptor
handoff or close-out): the transaction's descriptors are owned by a list from their binding on and
closed pop-before-close, so no number is ever closed twice, but no signal mask is used, so an
asynchronous interrupt (SIGINT or SIGTERM, delivered through any thread), or any other exception raised
at the same point (an injected one included), can leave a descriptor open in the process until it
exits: one landing between a C call's return and the binding of the descriptor it returned; between a
helper's return and the handoff of the descriptors it returned to the owning list (journal_state's
transaction directory descriptors, bound to txns before held.extend has taken them all); between a
descriptor's pop and its close inside a close-out (every close-out loop in this module runs through
_close_held_into, which still closes the rest of its list and records every exception it meets;
_close_held re-raises the first, each later one noted on it, and the transaction's final close-out runs
it over each of its three lists in turn and names every exception beside the run's own outcome, never in
its place, so only a descriptor popped and then interrupted before its close stays open); or an
asynchronous interrupt landing at _close_held_into's own loop boundary or in its handler, outside the
per-descriptor try (what that loop has not reached stays open), or between the final close-out's calls
or in the outcome report after them (what was not reached stays open, and that interrupt then
propagates in place of the run's own outcome, which is then not named beside it); or inside a
_journal helper (acquire_lock's lock descriptor among them, and _open_dir_contained's duplicate, which
it never returns once one of its closes raises anything but OSError; the cleanup loops of _open_parent,
ensure_journal_dirs, _open_dir_contained and _journal_txn_dirs close every descriptor they opened even
then, so only the one an interrupt lands on between its pop and its close stays open); or inside the
store's _open_working_dir_fd, where an interrupt (or any exception but OSError) raised by its parent
close leaves the `.working` descriptor it had just opened open, that exception resolved as below.
Leak-freedom under interrupt, or under an exception raised in those windows, is NOT claimed. The
finish ops add their own: all three finish ops
compose into this shell's transactions, which refuse a resolved store (no lease join), so once init-store
has run, a real adoption's plant, render and receipt transactions refuse until the lease join lands;
render-views composes create-only view publications through this journal (an occupied view destination
refuses fail-closed in this slice; the spec 14.2 preserve-then-render write for a plan-enumerated
occupying source composes with the file-ops slice), delegating only read-only planning and the U6 source
gate to the render and check engines; the receipt core's content (files, approval,
transaction ids, checks, probes) is the stage driver's to assemble, held here only to the shipped
validator and the plan bindings adoption_record names; and the trust gate proves member bytes against the
agreed release inventory, never the publisher's authenticity beyond it.

Exceptions on the closing paths: the run's FIRST interrupt always propagates as itself (a
close-out or a _journal helper close that meets an earlier error and then an interrupt raises the
interrupt, never notes it on the error), and every close exception is named where a reader sees it: in
the propagating exception, or in the run's report, which renders each exception with the notes recorded
on it (_journal._exc_said), never only in a note on an exception a caller then drops. Every close site
the transaction reaches resolves through ONE first-interrupt selection, _journal._first_interrupt: the
store probe (_default_store_present_without_manifest), the store's resolution, discovery and no-follow
walk, _journal's helper closes and cleanup loops, a phase's committed-base read
(_committed_base_or_refuse), every held list closed through _close_held (the first listing's walk and
journal_state's among them), the check engine's listing and teardown and the render-views planning
close reached from a composer (each through _journal._yield_close_exceptions, an
interrupt in flight there kept as itself with the close exception noted on it), and the transaction's
close-outs, sweep and listings (recorded, then selected in the order RAISED: the lock read's close
before the release, the release's own exceptions, the closes after it, the closing sweep and listing,
then the final close-out), so a later interrupt never propagates in place of an earlier one. An
exception a close raised leaves the close helpers marked (_journal._mark_fd_release_raised: _journal's
_close_fd_yielding and _yield_close_exceptions, the store's _close_fd_exc_safe and _close_fd_on_exit and
its no-follow walk's hand-off close), and EVERY handler in the transaction's reach that reads an
exception's class as a signal applies ONE record-and-report rule first, _journal._fd_release_fault: a marked
exception is raised as itself (or, in the two lock-state reads, recorded and named beside the outcome),
never read as absent, unreadable, cannot-evaluate, cannot-inspect, does-not-verify or not reached. Those
handlers: _journal's _lstat_contained, _read_contained (its parent walk), release_lock and
_poststate_verifies; the store's _read_store_bytes_contained (both reads), _open_working_dir_fd, every
StoreError handler of resolve_store, resolve_store_fd, _resolve_at and load_manifest (so the resolver, its
discovery and the probe, _default_store_present_without_manifest, both of its handlers, never fold one
into a posture or a False); and here _open_product_root, _read_live, ApplyOps._mkdirs, journal_state,
_committed_base_or_refuse, _absent_journal_dirs, the sweep (_remove_journal_dirs, both _open_parent
handlers), _interrupted_lock_state, _failed_lock_state and run_adopt_transaction's journal reads, lock
acquire and transaction handler (a close exception after the COMPLETE frame is durable leaves the commit
standing, named beside it as AdoptCommittedLockError; an interrupt there still propagates as itself, the
transaction named NOT confirmed committed). run_transaction rolls back on any Exception a close raised (a KeyError
from _poststate_verifies' close among them), never only on a JournalError. A lock clause names each
exception with its notes (_journal._msg_said), and a transaction whose state cannot be classified names
why. A self-test vector injects a raise after a real close at EVERY close event of seven runs (commit,
refusal, body interrupt, body fault, failed acquire, lock stays, failed transaction with rollback), EVERY
probe class at every event, the classes read from the code (each exception class named at a raise,
except or isinstance site of every module on a close event's stack, plus an ordinary fault and an
interrupt no site names), so a handler added later that drops one, or a class added later, fails the
suite. Not named (disclosed): a close OSError that yields
to an exception already in flight (#378), as before; and a close OSError on a quiet teardown close
(_journal._close_fd_quietly: the lock identity's and the first listing's held descriptors, the
cleanup loops of _open_parent, _open_dir_contained and ensure_journal_dirs, and _committed_base_or_refuse's
journal descriptor), whose descriptor close(2) released anyway; and a close OSError on _journal._pid_start's
/proc stat FILE OBJECT (a file-object close, outside every os.close probe), which reads as an unknown start
time, so release_lock treats the run's own lock as not provably its own and the commit reports the lock
STAYING, fail-closed, without naming that close error.
Residual (disclosed, not chased): when a SECOND ORDINARY fault (an Exception) arrives during a cleanup
that is already failing, which of the two propagates and which is named beside it (in a note, or as the
other's context) is not specified, for instance a _journal close exception with an error in flight
propagates in its place, that error noted on it and kept as its context; a second interrupt never
propagates in place of the first. Likewise an interrupt a later close raises while an earlier close's
exception is being HANDLED by a caller (a rollback publish after _poststate_verifies' close raised, for
instance) propagates as the first interrupt with that exception kept only as its context (__context__),
not noted on it. An asynchronous interrupt landing outside these protected calls (the
named residual above) is not covered.

The enable-hook op (PR3 slice 2) writes executable-on-load configuration under the threat model carried in
_opf_adopt_hook's docstring, whose pure merge core computes every byte it publishes. The live registration
file must hash to the row's old_digest (drift refuses into a fresh plan); the merged bytes must hash to the
row's new_digest, the exact post-merge registration the one approval bound; the live bytes are archived at
this run's adoption archive and then overwritten through a `write` pinned to them, so the reversal (the
journal restoring the recorded prior bytes) and the archived copy agree byte-exact; and an already-merged
file is a verified no-op that composes nothing. The op writes configuration only and never runs, loads or
activates the hook. Disclosed residuals of slice 2: the shell's store-posture gate admits only a first
adoption (no lease join yet), so a re-adoption's enable-hook refuses with it; the registration target is
bound to the merge core's closed v1 path allowlist (_opf_adopt_hook.REGISTRATION_PATHS) and plugin_entry
to the plan-row pack-member grammar (_opf_adopt._is_hook_entry, one shell word, never arbitrary command
text), while checking the entry against a digest-verified installed pack is the trust gate's
(a later slice); binding the rows to the one approved plan is the stage driver's; and a platform that
already loaded a merged registration is not un-executed by restoring the file.

Registration ops (slice 4): each rewrites ONE existing file whole, never by text substitution, under the
record writer's byte-reproduction discipline: the bytes it rewrites (live, or the previous link of a
register-unmanaged chain in this same transaction) must hash to the row's old_digest (drift refuses into a
fresh plan) and re-emit unchanged byte-exact through emit_checked (a comment or non-canonical serialization
refuses, untouched); the postimage must hash to the row's new_digest, the bytes the one approval bound.
register-unmanaged rewrites the frozen store manifest that the plan's store identity names, reproducing the
planner's own rendering (the kept path appended to [unmanaged].paths), and its reparse must equal the prior
manifest plus exactly that entry and validate; an entry in the spec 14.2 store control area (every homes
generation), at a protected destination, overlapping the machine store, a declared view or an entry already
registered (manifest paths compared under one lexical, casefolded spelling), written by this transaction, not
a regular file at apply, or whose bytes no longer hash to its approved source digest refuses. repoint-consumer
re-emits the caller's planned postimage model of a canonical TOML consumer, and refuses without the store
identity's valid manifest, at any of those reserved store paths, or at a file kept under [unmanaged]. The
first rewrite of a path is preserve-first: its live bytes are archived at this run's adoption archive, then
overwritten through a `write` pinned to them, so the reversal (the journal restoring the recorded old bytes)
and the archived copy agree byte-exact; a chain's later links only replace that one write's planned bytes.
Every refusal is raised while composing, before the transaction opens, with nothing written; the journal's own
staged-digest pin re-checks the bytes inside the transaction. Disclosed residuals of slice 4: the
store-posture gate below refuses every resolved store (no lease join yet), and both ops need a store manifest
(live, or created earlier in this transaction by init-store), so until the lease
join lands they execute only in the self-test's fixtures, behind a stand-in for that join which swallows only
the in-root resolved-store refusal, while the unpatched shell refuses them; the planned consumer model is the
caller's (the stage driver's), bound only through new_digest; a consumer in any format but TOML refuses, since
TOML's is this slice's one canonical emitter; the preserve-first archive copy of each rewritten file
(.working/archive/adoption/<run-id>/<path>) is a creation the plan's derived effects list
(_opf_adopt.rewrite_preservations), so apply creates no file the plan does not bind; repoint-consumer reads
the live manifest's [unmanaged] and [views] without a plan digest for it; and binding the rows, the store
table, the kept files' source digests and the consumer models to the one approved plan is the stage driver's.

Outcome model: single-sourced from `_opf_store` exactly as the sibling `_opf_adopt` does; the inventory
grading is the doctor's own shared validator (`_opf_check._evidence_rows`), so a malformed inventory or a
path claimed twice is CANNOT-EVALUATE and the legacy ingest format is the named `legacy-ingest-inventory`
finding (spec 4.2). Filesystem entry points raise `AdoptApplyError` (a fail-closed refusal, exit 2).

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo's gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_adopt_apply.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import tomllib
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal              # noqa: E402
import _opf_adopt as schema  # noqa: E402
import _opf_adopt_hook as hook  # noqa: E402
import _opf_init_operation as init_op  # noqa: E402
import _opf_store as store   # noqa: E402
import _optlevel             # noqa: E402
from _opf_emit import EmitError, _model_equal, emit_checked  # noqa: E402

KIND = "adoption"
SESSION_ID = "opf-adopt"
OPERATION = "adopt-apply"
# The adoption journal root, at the PRODUCT root and outside `.working/` (see the module docstring).
JOURNAL_REL = ".aiqt/adopt/journal"
# The only plan format apply may take (spec 14.1), the schema's own marker.
PLAN_V2_FORMAT = schema.PLAN_FORMAT
# The run's evidence-bundle members apply persists: the approved plan and its captured approval (spec 14.1).
PLAN_NAME = "plan.toml"
APPROVAL_NAME = "approval.toml"
BASE_PHASE = "base"    # the [adoption] identity phase of inventory.toml (spec 4.2)
# The stage every plan op composes in during apply; the retirement stage follows a green completion check.
APPLY_STAGE = "apply"
# The driver's mandatory receipt stage (spec 14), composed after every plan row whatever the plan carries.
RECEIPT_STAGE = "receipt"
# The bound on the one read-only git query that observes the live product revision (observe_revision).
_GIT_TIMEOUT_SECONDS = 30
_GIT_DIAGNOSIS_CHARS = 300
# The phase of the run's retirement-stage transaction, which the stage driver runs only after the green
# completion check (spec 14.1); the base transaction is the apply stage.
RETIREMENT_PHASE = "retirement"
DIR_MODE = 0o755
FILE_MODE = 0o644
ARCHIVE_MODE_MASK = 0o755    # an archive copy keeps its source's mode bits under this mask (never widened)
_NONCE_RE = re.compile(r"^[0-9a-f]{16}\Z")
# The store-tree control homes this engine may create beneath and never rewrite (spec 4.2, 14.2); which of
# their paths take a create is the shared protected-destination predicate's.
_CONTROL_HOMES = (store.ARCHIVE_REL, store.IMPORTED_REL)
# The Move root, derived from the public Move-destination constructor (spec 14.2), never re-spelled.
_MOVED_ROOT = store.moved_dest("x").rsplit("/", 1)[0]


class AdoptApplyError(Exception):
    """A fail-closed refusal or cannot-evaluate carrying the operator-facing reason (mapped to exit 2)."""


class AdoptCommittedLockError(AdoptApplyError):
    """NOT a refusal: the transaction `txn` COMMITTED, so its product-tree changes LANDED and are not rolled
    back, but its journal lock release left the lock or may not be durable, or its final descriptor
    close-out raised (each such exception named in the message and the first chained as the cause, never
    in place of the commit). A subclass of AdoptApplyError, so the CLI's exit mapping is unchanged."""

    def __init__(self, txn, note):
        super().__init__("the adoption transaction {} COMMITTED: its product-tree changes LANDED and are not "
                         "rolled back; {}".format(txn, note))
        self.txn = txn


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _within(path, home):
    return path == home or path.startswith(home + "/")


def _is_mode(value):
    return type(value) is int and 0 <= value <= 0o7777


def _mode_text(value):
    return "{:04o}".format(value) if _is_mode(value) else repr(value)


def _manifest_spelling(path):
    """One comparable spelling of a root-relative path: the manifest validators (_opf_store's
    _is_contained_relpath) admit empty and `.` components and contained `..` steps in [unmanaged] paths and
    view targets, so `ci/./x`, `ci//x` and `ci/a/../x` all name `ci/x`. Each is reduced lexically and
    casefolded, as protected_destination compares its names, so no equivalent spelling slips an overlap
    guard (a case-only difference names one file on a case-insensitive filesystem; refusing it on a
    case-sensitive one is the fail-closed side)."""
    parts = []
    for comp in path.split("/"):
        if comp in ("", "."):
            continue
        if comp == ".." and parts:
            parts.pop()
        else:
            parts.append(comp)
    return "/".join(parts).casefold()


def _overlaps(path, other):
    """Whether two root-relative paths name one file, or one contains the other, under _manifest_spelling."""
    a, b = _manifest_spelling(path), _manifest_spelling(other)
    return _within(a, b) or _within(b, a)


# --- run identity (the homes grammar, _opf_store; `adopt-<YYYYMMDD>T<HHMMSS>Z-<hash16>`, spec 4.2) ----

def is_run_id(value):
    """True only for a string the homes constructors accept as an adoption run id (spec 4.2: lexical
    shape, not calendar validity). An import-family id, a traversing spelling, or any other malformed
    value is refused, never accepted."""
    try:
        store.evidence_run(KIND, value)
    except ValueError:
        return False
    return True


def mint_run_id(now, run_nonce):
    """Mint `adopt-<UTCSTAMP>Z-<hash16>` from an injected clock instant and nonce, then validate the
    result against the homes grammar so the mint can never drift from what the constructors accept.
    Identical inputs give a byte-identical id. Raises AdoptApplyError on a naive/non-UTC datetime or a
    nonce outside 16 lowercase hex characters (fail-closed, never a fabricated identity)."""
    if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
            or now.utcoffset() != datetime.timedelta(0)):
        raise AdoptApplyError("now must be a clock-derived aware UTC datetime")
    if not isinstance(run_nonce, str) or not _NONCE_RE.match(run_nonce):
        raise AdoptApplyError("run_nonce must be 16 lowercase hex characters")
    run_id = "adopt-" + now.strftime("%Y%m%dT%H%M%SZ") + "-" + run_nonce
    if not is_run_id(run_id):
        raise AdoptApplyError("minted run id {!r} does not match the adoption run-id grammar "
                              "(fail-closed)".format(run_id))
    return run_id


# --- homes paths, derived from the _opf_store constructors (spec 4.2) ---------------------------------

def evidence_home_rel(run_id):
    """`.working/imported/adoption/<run-id>`, the adoption evidence bundle (spec 4.2, 14.2)."""
    try:
        return store.evidence_run(KIND, run_id)
    except ValueError as exc:
        raise AdoptApplyError("evidence home: {}".format(exc))


def inventory_rel(run_id, phase=None):
    """The bundle-root inventory: `inventory.toml`, or `inventory-<phase>.toml` for a later phase. The
    base phase name is reserved for `inventory.toml` itself (spec 4.2), so no later phase is ever named
    `base` and one phase spelling never answers to two file names."""
    if phase == BASE_PHASE:
        raise AdoptApplyError("evidence inventory: invalid evidence inventory phase {!r}: the base phase "
                              "name is reserved for inventory.toml (spec 4.2)".format(phase))
    try:
        return store.evidence_inventory(KIND, run_id, phase)
    except ValueError as exc:
        raise AdoptApplyError("evidence inventory: {}".format(exc))


def archive_rel(run_id, source_path):
    """`.working/archive/adoption/<run-id>/<source-path>`: the retirement preimage of a non-occupying
    source, or the archived copy of an occupying one (spec 14.2)."""
    try:
        return store.retire_preimage(run_id, source_path)
    except ValueError as exc:
        raise AdoptApplyError("adoption archive: {}".format(exc))


def _txn_name(run_id, phase):
    inventory_rel(run_id, phase)   # validates both the run id and the phase spelling
    return run_id if phase is None else "{}.{}".format(run_id, phase)


def _plan_hex(plan_digest):
    if not schema._is_digest(plan_digest):
        raise AdoptApplyError("plan digest {!r} is not a sha256:<64 hex> digest".format(plan_digest))
    return plan_digest[len("sha256:"):]


def _plan_digest_of(plan_bytes):
    """The `plan_digest` the plan bytes carry, shape-checked only: the doctor proves the identity digest
    against the PROVEN plan (_opf_adopt_state), so this read stays light and a plan frozen by the real
    planner always yields its own sealed digest. Raises AdoptApplyError on unreadable bytes or a
    missing or malformed digest."""
    try:
        doc = tomllib.loads(plan_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AdoptApplyError("the plan carries no readable plan_digest ({}); fail-closed".format(exc))
    digest = doc.get("plan_digest") if isinstance(doc, dict) else None
    _plan_hex(digest)
    return digest


# --- live re-observation (investigation is not a snapshot; "Re-observe at apply") ---------------------

def _read_live(root_fd, relpath):
    """(fstat, bytes) of one contained, regular, single-linked live file, or (None, None) when absent. A
    symlink, special file, multiply-linked file, or non-contained spelling refuses."""
    if not schema._is_contained_filepath(relpath):
        raise AdoptApplyError("operand {!r} is not a contained relative file path".format(relpath))
    try:
        st = _journal._lstat_contained(root_fd, relpath)
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
        raise AdoptApplyError("cannot observe {!r} ({}); fail-closed".format(relpath, exc))
    if st is None:
        return None, None
    if not stat.S_ISREG(st.st_mode):
        raise AdoptApplyError("operand {!r} is not a regular file (a symlink or special entry is "
                              "refused)".format(relpath))
    try:
        data, fst = _journal._read_contained(root_fd, relpath, require_single_link=True)
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
        raise AdoptApplyError("cannot read {!r} contained ({}); fail-closed".format(relpath, exc))
    return fst, data


def observe_live(root_fd, relpath):
    """Re-observe ONE operand's live state at apply time: absent, or a regular file's mode, size and
    bare-hex sha256. The op slices call this immediately before composing each op, so a preimage or
    absence precondition is checked against the LIVE tree, never the investigation inventory; drift
    grading is each op's own precondition, not this observer's."""
    fst, data = _read_live(root_fd, relpath)
    if fst is None:
        return dict(kind="absent")
    return dict(kind="file", mode=stat.S_IMODE(fst.st_mode), size=len(data), sha256=_sha256(data))


# --- the evidence inventory (spec 4.2): graded by the doctor's own shared validator -------------------

def validate_inventory(doc, run_id):
    """Grade ONE parsed inventory of the adoption bundle of `run_id` exactly as C-EVIDENCE-ENUM does,
    through `_opf_check._evidence_rows`: VALID; the legacy ingest format is the named
    `legacy-ingest-inventory` finding (INVALID); every malformed input, a path claimed twice included,
    is CANNOT-EVALUATE (spec 4.2). Both non-VALID verdicts are refusing."""
    import _opf_check
    if not is_run_id(run_id):
        return schema._cannot("inventory scope run id {!r} does not match the adoption "
                              "grammar".format(run_id))
    try:
        rows = _opf_check._evidence_rows(evidence_home_rel(run_id), KIND, run_id, doc)
        seen = set()
        for row in rows:
            if row["path"] in seen:
                raise ValueError("{!r} is claimed more than once".format(row["path"]))
            seen.add(row["path"])
    except _opf_check._LegacyIngestInventory as exc:
        return schema._invalid([str(exc)])
    except (TypeError, ValueError, KeyError) as exc:
        return schema._cannot("malformed inventory: {}".format(exc))
    return schema._ok()


def inventory_identity(run_id, doc):
    """The validated (phase, plan_digest) [adoption] identity of one parsed adoption inventory
    (spec 4.2), through the doctor's own validator; raises AdoptApplyError on a missing, malformed or
    foreign-run identity."""
    import _opf_check
    try:
        return _opf_check._adoption_identity(run_id, doc)
    except (TypeError, ValueError, KeyError) as exc:
        raise AdoptApplyError("inventory identity: {}".format(exc))


def inventory_row(path, data):
    """One inventory row for retained bytes at a store-relative path (spec 4.2 row shape)."""
    return dict(path=path, size=len(data), sha256=_sha256(data))


def emit_inventory(run_id, rows, phase=None, plan_digest=None):
    """Canonical inventory bytes for the adoption bundle of `run_id`: rows sorted by path, carrying the
    [adoption] identity table (the run id, the phase, `base` when None, and the plan digest, spec 4.2),
    validated fail-closed FIRST so a malformed row or identity can never reach bytes. Raises
    AdoptApplyError on any refusal, a missing or malformed plan digest included."""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise AdoptApplyError("inventory rows must be a list of tables")
    doc = dict(format=store.EVIDENCE_INVENTORY_FORMAT,
               adoption=dict(run_id=run_id, phase=BASE_PHASE if phase is None else phase,
                             plan_digest=plan_digest),
               file=sorted((dict(r) for r in rows), key=lambda r: str(r.get("path"))))
    checked = validate_inventory(doc, run_id)
    if checked.status != store.VALID:
        raise AdoptApplyError("inventory refused: {}".format("; ".join(checked.findings)))
    try:
        return emit_checked(doc).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("inventory cannot be emitted canonically ({}); fail-closed".format(exc))


def _open_product_root(product_root):
    if not isinstance(product_root, (str, os.PathLike)):
        raise AdoptApplyError("product_root must be an absolute path")
    root = Path(product_root)
    if not root.is_absolute() or ".." in root.parts:
        raise AdoptApplyError("product_root must be absolute and contain no '..'")
    try:
        return store._open_dir_nofollow(root)
    except OSError as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
        raise AdoptApplyError("cannot open product root {!r} contained ({}); "
                              "fail-closed".format(str(root), exc))


def verify_bundle(product_root, run_id):
    """Homes-1 evidence verification carried by the completion checks (spec 14.1, 4.2): RE-READ every
    bundle-root inventory of this adoption run from disk, then every payload those inventories list,
    contained and single-linked. Read-only. A missing bundle, a bundle with no inventory, a missing listed
    file, a listed entry that is not a regular file, and a size or digest mismatch are findings (INVALID),
    as is the legacy ingest format; a phase inventory without inventory.toml, an unreadable or malformed
    inventory, a path claimed twice across the bundle's inventories, and an unreadable payload are
    CANNOT-EVALUATE. Bundle membership (an unlisted file) is not reconciled here. CAPACITY LIMIT
    (disclosed): every inventory and payload read is bounded by the journal's contained-read cap
    (_journal._MAX_PRODUCT_READ_BYTES, 16 MiB), so a listed file over the cap is CANNOT-EVALUATE naming
    the cap, never truncated or slurped unbounded; the same ceiling bounds compose (_read_live), preimage
    capture and poststate verification, so no bundle this shell writes can carry an over-cap payload.
    Directory identities are RETAINED from the bundle listing through every inventory and payload read
    (_verify_bundle_at), so a directory concurrently swapped onto a listed pathname is never re-resolved
    mid-verification (round 3). Each inventory's [adoption] identity is held to the doctor's bar (spec
    4.2): a phase other than its file name's, or a plan digest other than the one the bundle's own
    plan.toml proves (read once, and its row checked against those same bytes), is CANNOT-EVALUATE, as
    is a bundle whose plan.toml is absent, unreadable or unproven."""
    if not is_run_id(run_id):
        return schema._cannot("bundle run id {!r} does not match the adoption grammar".format(run_id))
    root_fd = _open_product_root(product_root)
    try:
        return _verify_bundle_at(root_fd, run_id, evidence_home_rel(run_id))
    finally:
        store._close_fd_exc_safe(root_fd)


def _verify_bundle_at(root_fd, run_id, bundle, home_fd=None):
    # Round-3 identity retention (the read_lock_owner_at posture applied to the bundle): ONE descriptor
    # per directory, opened contained/no-follow beneath its retained parent and HELD from the bundle
    # listing through every inventory and payload read, so no read re-resolves a pathname from root_fd
    # after the listing. The bundle descriptor the listing used is the SAME descriptor every
    # bundle-relative read goes through, and a listed payload outside the bundle walks its own chain the
    # same way, sharing every already-opened ancestor, so all reads of one verification bind to one
    # directory identity per path: a directory swapped onto a pathname between the listing and a read is
    # never followed, and two directories neither of which verifies alone can never combine into one
    # false success. A component that cannot be opened contained (symlinked, wrong-type, unreadable) is
    # CANNOT-EVALUATE, never approximated. home_fd (round 4): the evidence-home descriptor a caller that
    # LISTED the home still holds; when given, it SEEDS the retained chain (a dup, so the caller's
    # descriptor stays open and the cleanup below owns only the dup) and the bundle is stat'ed and opened
    # beneath THAT held identity, never re-walked from root_fd, so an evidence home swapped onto its
    # pathname between the caller's listing and this verification can never contribute a bundle.
    import _opf_check
    dir_fds = dict()
    missing = set()     # each directory found absent once: a later path beneath it never reopens it
    if home_fd is not None:
        # Round 7 MINOR: the subscript KEY is computed BEFORE the dup (Python evaluates an
        # assignment right-hand side first), so a _check_rel raise on a malformed bundle path can
        # never strand the just-duplicated home descriptor outside dir_fds, where the cleanup
        # below cannot close it.
        home_key = tuple(_journal._check_rel(bundle))[:-1]
        dir_fds[home_key] = os.dup(home_fd)

    def dir_at(parts):
        """The RETAINED dir fd for the relative directory `parts` (a tuple of components; () is the
        product root itself): each component is opened O_DIRECTORY|O_NOFOLLOW beneath its retained
        parent exactly once and reused for every later read of this verification. One found absent is
        remembered and raises FileNotFoundError again unopened, as does every path beneath it."""
        if not parts:
            return root_fd
        if parts in missing:
            raise FileNotFoundError("contained directory {!r} is absent".format("/".join(parts)))
        fd = dir_fds.get(parts)
        if fd is None:
            try:
                pfd = dir_at(parts[:-1])
            except FileNotFoundError:
                missing.add(parts)
                raise
            try:
                fd = os.open(parts[-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
            except FileNotFoundError:
                missing.add(parts)
                raise
            except OSError as exc:
                raise _journal.JournalError("cannot open contained directory component {!r} of "
                                            "{!r} ({})".format(parts[-1], "/".join(parts), exc))
            dir_fds[parts] = fd
        return fd

    def read_retained(relpath):
        """_read_contained's sibling over the RETAINED parent chain (contained, no-follow, single-link,
        capped at _journal._MAX_PRODUCT_READ_BYTES), never a fresh path walk from root_fd."""
        parts = _journal._check_rel(relpath)
        # O_NONBLOCK so a non-regular final component (e.g. a FIFO swapped in for the regular file)
        # returns at once instead of blocking forever; the fstat below then refuses it.
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=dir_at(tuple(parts[:-1])))
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode):
                raise _journal.JournalError("contained path {!r} is not a regular file".format(relpath))
            if fst.st_nlink != 1:
                raise _journal.JournalError("contained control file {!r} has {} hard links; "
                                            "refusing to read a multiply-linked control file (a hardlink "
                                            "to an out-of-tree victim, never our singly-linked control "
                                            "file)".format(relpath, fst.st_nlink))
            return _journal._read_fd(fd, cap=_journal._MAX_PRODUCT_READ_BYTES), fst
        finally:
            store._close_fd_exc_safe(fd)

    try:
        try:
            if home_fd is not None:
                # presence and type read through the HELD home descriptor the caller's listing used,
                # never by re-walking `bundle` from root_fd (round 4).
                st = _journal._lstat_at(home_fd, _journal._check_rel(bundle)[-1])
            else:
                st = _journal._lstat_contained(root_fd, bundle)
            if st is None:
                return schema._invalid(["evidence bundle {!r} is missing".format(bundle)])
            if not stat.S_ISDIR(st.st_mode):
                return schema._cannot("evidence bundle {!r} is not a directory".format(bundle))
            dfd = dir_at(tuple(_journal._check_rel(bundle)))
            names = sorted(os.listdir(dfd))
        except (_journal.JournalError, OSError) as exc:
            return schema._cannot("cannot list evidence bundle {!r} ({})".format(bundle, exc))
        inventories = [name for name in names if store.is_evidence_inventory_name(name)]
        if not inventories:
            return schema._invalid(["evidence bundle {!r} has no inventory".format(bundle)])
        if "inventory.toml" not in inventories:
            return schema._cannot("evidence bundle {!r} has a phase inventory but no inventory.toml; a "
                                  "phase inventory never stands in for it (spec 4.2)".format(bundle))
        expected = dict()
        digests = dict()
        for name in inventories:
            rel = bundle + "/" + name
            try:
                raw, _fst = read_retained(rel)
                doc = tomllib.loads(raw.decode("utf-8"))
            except (_journal.JournalError, OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                return schema._cannot("inventory {!r} is unreadable or unparseable ({})".format(rel, exc))
            checked = validate_inventory(doc, run_id)
            if checked.status != store.VALID:
                return schema.AdoptValidation(checked.status, [
                    "inventory {!r}: {}".format(rel, f) for f in checked.findings])
            # The doctor's identity bar (spec 4.2): the phase is the one this file name carries, and the
            # plan digest (compared below, once the plan is proven) is the bundle's own sealed plan's.
            try:
                phase, digests[rel] = _opf_check._adoption_identity(run_id, doc)
                named = _opf_check._inventory_phase(name)
            except ValueError as exc:
                return schema._cannot("inventory {!r}: {}".format(rel, exc))
            if phase != named:
                return schema._cannot("inventory {!r} names phase {!r}, not the {!r} phase its file name "
                                      "carries (an inventory copied from another phase, spec "
                                      "4.2)".format(rel, phase, named))
            for row in doc["file"]:
                if row["path"] in expected:
                    return schema._cannot("{!r} is claimed by more than one inventory of bundle "
                                          "{!r}".format(row["path"], bundle))
                expected[row["path"]] = row
        # The bundle's own plan.toml, read ONCE: its seal proof and its inventory row (below) are checked
        # against the same bytes, so a plan swapped between two reads never combines into a clean result.
        # An absent, unreadable or unproven plan proves no digest, so no inventory of the bundle (an empty
        # base inventory included) verifies as this run's record (spec 4.2).
        plan_path = bundle + "/" + PLAN_NAME
        try:
            plan_raw, _fst = read_retained(plan_path)
        except FileNotFoundError:
            return schema._cannot("evidence bundle {!r} has no {} to prove its inventories' plan digest "
                                  "against (spec 4.2)".format(bundle, PLAN_NAME))
        except (_journal.JournalError, OSError) as exc:
            return schema._cannot("the bundle's {!r} is unreadable, so no plan digest is proven "
                                  "({})".format(plan_path, exc))
        try:
            sealed = _opf_check._prove_adoption_plan(plan_raw, run_id)
        except ValueError as exc:
            return schema._cannot("{!r}: {}".format(plan_path, exc))
        for rel in sorted(digests):
            if digests[rel] != sealed:
                return schema._cannot("inventory {!r} names plan digest {!r}, not its bundle's own sealed "
                                      "plan's {!r} (an inventory of another run or plan, spec "
                                      "4.2)".format(rel, digests[rel], sealed))
        findings = []
        for path in sorted(expected):
            row = expected[path]
            if path == plan_path:
                data = plan_raw    # the bytes the seal proof read: never a second read
            else:
                try:
                    parts = _journal._check_rel(path)
                    try:
                        pfd = dir_at(tuple(parts[:-1]))
                    except FileNotFoundError:
                        findings.append("listed file {!r} is missing".format(path))
                        continue
                    pst = _journal._lstat_at(pfd, parts[-1])
                    if pst is None:
                        findings.append("listed file {!r} is missing".format(path))
                        continue
                    if not stat.S_ISREG(pst.st_mode):
                        findings.append("listed entry {!r} is not a regular file".format(path))
                        continue
                    # bounded by _MAX_PRODUCT_READ_BYTES (16 MiB): an over-cap listed payload cannot be
                    # hashed here and is CANNOT-EVALUATE below, naming the cap (a disclosed capacity
                    # limit; the same ceiling bounds compose/capture/poststate, so the shell never writes
                    # such a bundle).
                    data, _fst = read_retained(path)
                except (_journal.JournalError, OSError) as exc:
                    return schema._cannot("cannot read listed file {!r} ({})".format(path, exc))
            if len(data) != row["size"] or _sha256(data) != row["sha256"]:
                findings.append("listed file {!r} does not match its recorded size and sha256 (payload "
                                "drift)".format(path))
        return schema._ok() if not findings else schema._invalid(findings)
    finally:
        _close_held(list(dir_fds.values()))     # one close that raises never abandons the rest


# --- composition: preserve-first, derived inventories, immutable homes (spec 4.2, 14.2) ---------------

def _archive_root(run_id):
    """This run's whole adoption-archive home, derived from the public retire-preimage constructor
    (spec 4.2), never composed from private helpers."""
    return archive_rel(run_id, "x").rsplit("/", 1)[0]


def _bundle_root_inventory(run_id, path):
    bundle = evidence_home_rel(run_id)
    member = path[len(bundle) + 1:] if path.startswith(bundle + "/") else None
    return member is not None and "/" not in member and store.is_evidence_inventory_name(member)


def _evidence_eligible(run_id, path):
    """Whether a created path is retained evidence an inventory row claims (spec 4.2): a member of this
    run's bundle other than a bundle-root inventory, this run's archive, or a Move destination (any path
    beneath the Move root, default or explicit)."""
    if _within(path, evidence_home_rel(run_id)):
        return not _bundle_root_inventory(run_id, path)
    return _within(path, _archive_root(run_id)) or _within(path, _MOVED_ROOT)


def derive_rows(run_id, ops, staged):
    """The inventory rows of one transaction, DERIVED from its own op list and staged bytes, never
    supplied by a caller: one row per retained-evidence create."""
    rows = []
    for op in ops:
        if op.get("op") == "create" and _evidence_eligible(run_id, op["path"]):
            data = staged.get(op["path"])
            if not isinstance(data, bytes):
                raise AdoptApplyError("no staged bytes for evidence {!r}".format(op["path"]))
            rows.append(inventory_row(op["path"], data))
    return rows


def _pinned_remove(rel, data, mode):
    """Remove a file only while it still holds the verified bytes and mode; the journal verifies the pin
    at capture, under its lock, before it takes the preimage (the pinned-remove idiom of the retired
    ingest execution coordinator)."""
    return dict(op="remove", path=rel, poststate=dict(kind="absent"),
                **{"source-poststate": dict(kind="file", mode=mode, sha256=_sha256(data))})


class ApplyOps:
    """ONE adoption transaction's ordered op list and staged bytes, composed only through these methods
    against the live tree beneath `root_fd`: create-only publication, preserve-first archival, and a
    final inventory derived from the list itself. check_apply_ops re-proves every invariant over the
    finished list, so a hand-built list is held to the same rules."""

    def __init__(self, root_fd, run_id, phase=None, plan_digest=None):
        self.root_fd = root_fd
        self.run_id = run_id
        self.phase = phase
        self.plan_digest = plan_digest
        self.inventory = inventory_rel(run_id, phase)
        self.ops = []
        self.staged = {}
        self._dirs = set()
        self.sealed = False
        # A phase transaction's view of the run's COMMITTED base: the archive copies its INTENT published
        # ({path: content sha256}) and the mode each was created at ({path: mode}, the source's own mode),
        # set by run_adopt_transaction. And the source -> destination pairs this transaction relocates, each
        # removal paired with its destination's create (check_apply_ops).
        self.committed = {}
        self.committed_modes = {}
        self.moves = {}

    def _mkdirs(self, rel):
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d in self._dirs:
                continue
            try:
                st = _journal._lstat_contained(self.root_fd, d)
            except (_journal.JournalError, OSError) as exc:
                _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
                raise AdoptApplyError("cannot inspect {!r} ({}); fail-closed".format(d, exc))
            if st is None:
                self.ops.append(dict(op="mkdir", path=d, poststate=dict(kind="dir", mode=DIR_MODE)))
            elif not stat.S_ISDIR(st.st_mode):
                raise AdoptApplyError("path component {!r} exists and is not a directory".format(d))
            self._dirs.add(d)

    def create(self, rel, data, mode=FILE_MODE):
        """Exclusive create of planned bytes at an absent path; an occupied path refuses (a collision
        routes to a disposition, never an overwrite)."""
        if self.sealed:
            raise AdoptApplyError("the transaction is sealed by its inventory; nothing may follow it")
        if not schema._is_contained_filepath(rel) or not isinstance(data, bytes) or rel in self.staged:
            raise AdoptApplyError("create needs a fresh contained path and bytes, not {!r}".format(rel))
        fst, _data = _read_live(self.root_fd, rel)
        if fst is not None:
            raise AdoptApplyError("{!r} is occupied; a collision routes to a disposition, never an "
                                  "overwrite (an inventory is never rewritten)".format(rel))
        self._mkdirs(rel)
        self.ops.append(dict(op="create", path=rel,
                             poststate=dict(kind="file", mode=mode, **{"content-sha256": _sha256(data)})))
        self.staged[rel] = data

    def frozen(self, source_path, plan_digest):
        """Re-observe one plan-enumerated source and require its live bytes to equal the plan digest (a
        drifted source MUST NOT be archived, retired, moved or removed, spec 14.2). Composes nothing.
        Returns (bytes, mode)."""
        expected = _plan_hex(plan_digest)
        fst, data = _read_live(self.root_fd, source_path)
        if fst is None:
            raise AdoptApplyError("plan-enumerated source {!r} is absent from the live tree; a fresh plan "
                                  "with its own approval is the remedy (spec 14.1)".format(source_path))
        if _sha256(data) != expected:
            raise AdoptApplyError("source {!r} is drifted: its live bytes no longer match its plan digest, "
                                  "so it MUST NOT be archived, retired, moved or removed; a fresh plan "
                                  "with its own approval is the remedy (spec 14.2)".format(source_path))
        return data, stat.S_IMODE(fst.st_mode)

    def require_absent(self, rel):
        """Re-observe one destination and refuse it occupied: a collision routes to a disposition, never
        an overwrite (spec 14.2). Composes nothing."""
        fst, _data = _read_live(self.root_fd, rel)
        if fst is not None:
            raise AdoptApplyError("{!r} is occupied; a collision routes to a disposition, never an "
                                  "overwrite (spec 14.2)".format(rel))

    def preserve(self, source_path, plan_digest):
        """Preserve one plan-enumerated source at apply: re-observe its live bytes, require them to equal
        the plan digest (frozen), and create the byte-identical copy at
        `.working/archive/adoption/<run-id>/<source-path>` with the source's own mode bits, so a private
        source is never widened and an executable one keeps its bits. The source itself stays live and
        frozen: a non-occupying source's retirement preimage. Returns (bytes, mode)."""
        data, mode = self.frozen(source_path, plan_digest)
        self.create(archive_rel(self.run_id, source_path), data, mode)
        return data, mode

    def preserve_masked(self, source_path, plan_digest):
        """Preserve one plan-enumerated source at apply: re-observe its live bytes, require them to equal
        the plan digest (a drifted source MUST NOT be archived, spec 14.2), and create the byte-identical
        copy, at the source's mode masked by ARCHIVE_MODE_MASK, at
        `.working/archive/adoption/<run-id>/<source-path>`. The source itself stays live and frozen: a
        non-occupying source's retirement preimage. Returns (bytes, the source's unmasked mode)."""
        expected = _plan_hex(plan_digest)
        fst, data = _read_live(self.root_fd, source_path)
        if fst is None:
            raise AdoptApplyError("plan-enumerated source {!r} is absent at apply; a fresh plan with its "
                                  "own approval is the remedy (spec 14.1)".format(source_path))
        if _sha256(data) != expected:
            raise AdoptApplyError("source {!r} is drifted: its live bytes no longer match its plan digest, "
                                  "so it MUST NOT be archived, retired, moved or removed; a fresh plan "
                                  "with its own approval is the remedy (spec 14.2)".format(source_path))
        # the copy carries the SOURCE's own mode, not the default create mode: an adopter file
        # narrowed to owner-only (a registration overlay can carry secrets) must not widen to
        # world-readable in the tracked archive home (spec 4.2 keeps that home tracked). The mask
        # only narrows: a group- or world-writable or setuid/setgid/sticky source never yields such
        # a copy. Mode protects local readers only: git records 644 or 755, so once the archive is
        # committed the copy's mode is not preserved, and a source kept untracked only by an anchored
        # ignore rule (such as /.claude/) is copied into the tracked archive home, a disclosed residual.
        self.create(archive_rel(self.run_id, source_path), data,
                    mode=stat.S_IMODE(fst.st_mode) & ARCHIVE_MODE_MASK)
        return data, stat.S_IMODE(fst.st_mode)

    def archive_occupying(self, source_path, plan_digest):
        """Resolve one occupied destination preserve-first (spec 14.2): the verified archive copy's create
        op, then the pinned removal of the live source, in THIS transaction and in that order."""
        data, mode = self.preserve(source_path, plan_digest)
        self.ops.append(_pinned_remove(source_path, data, mode))

    def _sealing_plan_digest(self):
        """The plan digest the sealed inventory's [adoption] identity carries (spec 4.2): the explicit
        one this transaction was opened with, else the staged plan's own (the base transaction stages the
        plan), else the committed bundle plan's own (a later phase extends the committed base); with none
        of the three the transaction cannot seal (fail-closed)."""
        if self.plan_digest is not None:
            return self.plan_digest
        staged = self.staged.get(plan_rel(self.run_id))
        if isinstance(staged, bytes):
            return _plan_digest_of(staged)
        fst, data = _read_live(self.root_fd, plan_rel(self.run_id))
        if fst is not None:
            return _plan_digest_of(data)
        raise AdoptApplyError("the transaction stages no plan and the bundle holds none, so the sealed "
                              "inventory's [adoption] identity cannot name a plan digest (spec 4.2); "
                              "pass one explicitly (fail-closed)")

    def committed_copy(self, source_path, plan_digest):
        """The retirement stage's preservation check (spec 14.1 check 3, 14.2: digest verification precedes
        removal): the archive copy of one source must be one the run's COMMITTED base transaction published
        with the plan digest, and its live bytes must still equal it. Composes nothing; returns the
        archived bytes and the mode that INTENT created the copy at (the source's own mode)."""
        rel = archive_rel(self.run_id, source_path)
        expected = _plan_hex(plan_digest)
        mode = self.committed_modes.get(rel)
        if self.committed.get(rel) != expected or not _is_mode(mode):
            raise AdoptApplyError("{!r} has no archive copy the run's committed base transaction published "
                                  "with its plan digest; preservation precedes removal (spec 14.2), so "
                                  "nothing is retired or moved (fail-closed)".format(source_path))
        fst, data = _read_live(self.root_fd, rel)
        if fst is None or _sha256(data) != expected:
            raise AdoptApplyError("the archive copy {!r} is missing or no longer matches its plan digest; "
                                  "nothing is retired or moved (spec 14.2, fail-closed)".format(rel))
        return data, mode

    def retire(self, source_path, plan_digest):
        """Retirement stage (spec 14.1): remove one frozen non-occupying source, only after its committed
        retirement preimage re-verifies, through a removal pinned to the plan digest and the live mode."""
        if self.sealed:
            raise AdoptApplyError("the transaction is sealed by its inventory; nothing may follow it")
        self.committed_copy(source_path, plan_digest)
        data, mode = self.frozen(source_path, plan_digest)
        self.ops.append(_pinned_remove(source_path, data, mode))

    def relocate(self, source_path, destination, plan_digest, occupying):
        """Retirement stage (spec 14.2 Move): an occupying source, archived and removed at apply, has its
        committed archive copy re-verified and copied to the destination, the copy staying retained; a
        frozen non-occupying source has its verified live bytes created at the destination, then is
        removed through a pinned removal paired with that create. The destination takes the source's own
        mode bits in both branches (the live mode, or the mode the base archived the source at)."""
        if occupying:
            self.create(destination, *self.committed_copy(source_path, plan_digest))
            return
        data, mode = self.frozen(source_path, plan_digest)
        self.create(destination, data, mode)
        self.ops.append(_pinned_remove(source_path, data, mode))
        self.moves[source_path] = destination

    def seal(self):
        """Create this transaction's inventory, derived from its own op list and carrying the run, phase
        and plan identity (spec 4.2), as its final op."""
        data = emit_inventory(self.run_id, derive_rows(self.run_id, self.ops, self.staged),
                              self.phase, self._sealing_plan_digest())
        self.create(self.inventory, data)
        self.sealed = True


def check_apply_ops(run_id, phase, ops, staged, committed=None, moves=None, committed_modes=None):
    """Re-prove the shell's invariants over ONE finished op list, pure and before any transaction
    opens; returns the findings (empty means admissible). Preserve-first: every removal AND every write
    (which destroys the live bytes exactly as a removal does) carries a pinned digest and mode and
    follows, in this same list, the create of its archive copy with that same digest and, for a removal,
    at that same mode (spec 14.2; a removed source's preserved copy keeps the source's mode bits exactly,
    while a rewrite's archive copy is narrowed under ARCHIVE_MODE_MASK and pairs by digest alone). In the
    RETIREMENT_PHASE transaction alone, a removal (never a write) may instead pair with the create of its
    move destination earlier in this list (`moves`, source -> destination) or, failing that, with the
    archive copy the run's COMMITTED base transaction published (`committed`, path -> content sha256 from
    that transaction's INTENT, and `committed_modes`, path -> the mode that INTENT created it at, compared
    when given), at the same digest (spec 14.1: retirement waits for the green check); this check is pure,
    so the live bytes of that committed copy are re-read by run_adopt_transaction (_committed_pairs_live); a write
    additionally needs staged bytes matching its own content digest, like a create. An rmdir may target
    only a directory an earlier mkdir in this same list creates (so, under one-op-per-path, no live
    directory is ever removed by this shell). A protected destination (_opf_adopt.protected_destination,
    the predicate the planner applies to every move destination and archive copy, its names compared
    case-insensitively: `.git` and `.aiqt` at any depth, the adoption journal's own tree included, the
    product-root pointers, the store control area and the store tree's `.gitignore`) is never an operand of
    any kind, save a mkdir of this run's own homes or
    their ancestors. Immutable homes: under `.working/archive/` and `.working/imported/` only a create
    beneath this run's own archive, own bundle, or the Move root (and the mkdirs leading there) is allowed,
    never a write, remove, or rmdir, and never another run's home (spec 14.2, 4.2). One op per path. The
    final op, and the only bundle-root inventory, is this transaction's own inventory, byte-equal to the inventory
    derived from the list's retained bytes (spec 4.2). Spec 4.2 MAY lets a phase inventory publish in the
    same transaction as the base to claim a promotion receipt without a digest cycle; this shell has no
    promotion receipt to claim (the adoption receipt is a bundle member its own transaction's inventory
    claims), so it deliberately takes the STRICTER exactly-one-inventory rule, and a later slice may relax
    it with its own vectors."""
    target = inventory_rel(run_id, phase)
    roots = (evidence_home_rel(run_id), _archive_root(run_id), _MOVED_ROOT)
    committed = committed if isinstance(committed, dict) else {}
    committed_modes = committed_modes if isinstance(committed_modes, dict) else {}
    moves = moves if isinstance(moves, dict) else {}
    if not isinstance(ops, list) or not ops or not isinstance(staged, dict):
        return ["the transaction carries no op list"]
    findings = []
    seen = set()
    created = {}
    made_dirs = set()
    for i, op in enumerate(ops):
        where = "op[{}]".format(i)
        if (not isinstance(op, dict) or op.get("op") not in _journal.OP_KINDS
                or not schema._is_contained_filepath(op.get("path"))):
            findings.append("{} is not a contained journal op".format(where))
            continue
        kind, path = op["op"], op["path"]
        protected = schema.protected_destination(path, run_id)
        if protected is not None and not (kind == "mkdir" and any(_within(r, path) for r in roots)):
            findings.append("{} would {} {!r}, a protected destination: {} (fail-closed)".format(
                where, kind, path, protected))
            continue
        if path in seen:
            findings.append("{} touches {!r} a second time (one op per path)".format(where, path))
        seen.add(path)
        if kind not in ("create", "mkdir") and any(_within(path, home) for home in _CONTROL_HOMES):
            findings.append("{} would {} {!r}: the adoption archive and evidence bundles are immutable "
                            "(spec 14.2, 4.2); only a create beneath this run's own homes is "
                            "allowed".format(where, kind, path))
        if kind == "create":
            digest = (op.get("poststate") or {}).get("content-sha256")
            data = staged.get(path)
            if not isinstance(data, bytes) or _sha256(data) != digest:
                findings.append("{} create {!r} has no staged bytes matching its content digest".format(
                    where, path))
            created[path] = (digest, (op.get("poststate") or {}).get("mode"))
        elif kind == "mkdir":
            made_dirs.add(path)
        elif kind == "rmdir":
            if path not in made_dirs:
                findings.append("{} rmdirs {!r}, which this transaction did not create; a live directory "
                                "is never removed by the apply shell (fail-closed)".format(where, path))
        elif kind in ("remove", "write"):
            verb = "removes" if kind == "remove" else "overwrites"
            if kind == "write":
                digest = (op.get("poststate") or {}).get("content-sha256")
                data = staged.get(path)
                if not isinstance(data, bytes) or _sha256(data) != digest:
                    findings.append("{} write {!r} has no staged bytes matching its content digest".format(
                        where, path))
            pin = op.get("source-poststate")
            if not (isinstance(pin, dict) and pin.get("kind") == "file" and isinstance(pin.get("sha256"), str)
                    and isinstance(pin.get("mode"), int)):
                findings.append("{} {} {!r} without a pinned file digest and mode "
                                "(source-poststate)".format(where, verb, path))
            else:
                rel = archive_rel(run_id, path)
                paired = created.get(rel)
                if paired is None and kind == "remove" and phase == RETIREMENT_PHASE:
                    paired = (created.get(moves[path]) if path in moves
                              else (committed[rel], committed_modes.get(rel, pin["mode"])) if rel in committed
                              else None)
                if paired is None:
                    findings.append("{} {} {!r} before, or without, its archive copy earlier in this "
                                    "transaction: preserve-first, verify then destroy, never reversed or "
                                    "split (spec 14.2)".format(where, verb, path))
                elif paired[0] != pin["sha256"]:
                    findings.append("{} {} {!r} whose pinned digest differs from its archive copy's "
                                    "content digest".format(where, verb, path))
                elif kind == "remove" and paired[1] != pin["mode"]:
                    findings.append("{} {} {!r} whose pinned mode {} differs from the mode {} its "
                                    "preserved copy is created at: a preservation keeps the source's mode "
                                    "bits exactly, so a source whose mode changed after its copy was made "
                                    "is not removed (restore the mode, or take a fresh plan; "
                                    "fail-closed)".format(
                                        where, verb, path, _mode_text(pin["mode"]), _mode_text(paired[1])))
    inventories = [i for i, op in enumerate(ops)
                   if isinstance(op, dict) and op.get("op") == "create" and isinstance(op.get("path"), str)
                   and _bundle_root_inventory(run_id, op["path"])]
    if [ops[i]["path"] for i in inventories] != [target]:
        findings.append("the transaction must publish exactly its own inventory {!r} and no other "
                        "bundle-root inventory".format(target))
    elif inventories[0] != len(ops) - 1:
        findings.append("the inventory {!r} must be the final op, after every retained byte it "
                        "lists".format(target))
    else:
        try:
            sealed_doc = tomllib.loads(staged.get(target, b"").decode("utf-8"))
            identity = sealed_doc.get("adoption") if isinstance(sealed_doc, dict) else None
            claimed = identity.get("plan_digest") if isinstance(identity, dict) else None
            plan_staged = staged.get(plan_rel(run_id))
            if isinstance(plan_staged, bytes) and claimed != _plan_digest_of(plan_staged):
                findings.append("inventory {!r} does not carry the staged plan's own plan_digest in its "
                                "[adoption] identity (spec 4.2)".format(target))
            derived = emit_inventory(run_id, derive_rows(run_id, ops[:-1], staged), phase, claimed)
        except (AdoptApplyError, KeyError, TypeError, AttributeError, UnicodeDecodeError,
                tomllib.TOMLDecodeError) as exc:
            findings.append("no inventory can be derived from this transaction ({})".format(exc))
        else:
            if staged.get(target) != derived:
                findings.append("inventory {!r} is not the one derived from this transaction's own "
                                "retained bytes (spec 4.2: derived from the run's transaction record and "
                                "published with the bytes it lists)".format(target))
    return findings


def _committed_pairs_live(root_fd, run_id, phase, ops, committed, moves):
    """The live half of the committed pairing check_apply_ops proves pure (spec 14.1 check 3, 14.2: digest
    verification precedes removal): RE-READ, contained and single-linked, every committed archive copy a
    RETIREMENT_PHASE removal in this finished list pairs with (one neither created earlier in the list nor
    relocated to a move destination), and refuse a copy that is missing or no longer holds its INTENT
    digest. A hand-built list that appends a pinned removal without ApplyOps.retire is held to this rule
    too. Returns the findings; a copy that cannot be read refuses (AdoptApplyError)."""
    if phase != RETIREMENT_PHASE:
        return []
    findings = []
    created = set()
    for op in ops:
        if op["op"] == "create":
            created.add(op["path"])
        elif op["op"] == "remove" and op["path"] not in moves:
            rel = archive_rel(run_id, op["path"])
            if rel in created:
                continue
            fst, data = _read_live(root_fd, rel)
            if fst is None or _sha256(data) != committed.get(rel):
                findings.append("the committed archive copy {!r} a removal of {!r} pairs with is missing or "
                                "no longer matches its INTENT digest; nothing is retired (spec 14.2, "
                                "fail-closed)".format(rel, op["path"]))
    return findings


# --- the journaled transaction shell (reconcile-first; `.aiqt/adopt/journal`) --------------------------

def _journal_root(product_root):
    return Path(product_root) / JOURNAL_REL


def journal_state(root_fd, journal_root):
    """The adoption journal's state READ-ONLY, through the engine's own classification: (owner, opened),
    the recorded lock owner (None when no lock is held) and the sorted names of the transactions
    `_journal.classify_state` reads as open (INTENT without a terminal frame). A nothing-opened,
    complete, or rolled-back transaction is clean. An absent journal root is (None, []); a symlinked,
    dangling, non-directory, unreadable, or corrupt journal, or a symlinked or wrong-type entry in it (any
    entry but a transaction directory or a regular, singly-linked `lock` / `lock.break`), raises
    AdoptApplyError (fail-closed, never followed, skipped or read as absent). The lock and every entry are
    read through the ONE contained journal-root descriptor, never by re-resolving `journal_root` (which
    only names the returned entries), and each transaction is classified through its own directory
    descriptor HELD from the enumeration itself (round 4), so a transaction directory swapped onto its
    name after the enumeration can never read as clean. Nothing is written here."""
    try:
        st = _journal._lstat_contained(root_fd, JOURNAL_REL)
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
        raise AdoptApplyError("cannot inspect the adoption journal {} ({}); "
                              "fail-closed".format(JOURNAL_REL, exc))
    if st is None:
        return None, []
    if not stat.S_ISDIR(st.st_mode):
        raise AdoptApplyError("the adoption journal {} is not a directory; fail-closed".format(JOURNAL_REL))
    held = []   # jr_fd, then every held transaction directory descriptor: ONE list, ONE close-out
    try:
        try:
            held.append(_journal.open_journal_root_fd(root_fd, JOURNAL_REL))
        except (_journal.JournalError, OSError) as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
            raise AdoptApplyError("cannot open the adoption journal {} ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
        jr_fd = held[0]
        try:
            owner = _journal.read_lock_owner_at(jr_fd)
            # hold=True (round 4): every transaction directory descriptor is opened AT enumeration and
            # HELD through classification, and classify_state reads the frames through that same held
            # identity, so a transaction directory swapped onto its name after the enumeration (an
            # interrupted transaction renamed aside and replaced by an empty decoy) is still classified
            # from the enumerated directory's own frames, never reopened by name and read as clean.
            txns = _journal._journal_txn_dirs(jr_fd, journal_root, strict=True, hold=True)
            # the handoff: an exception landing here, before extend has taken every descriptor in txns
            # (an interrupt at the generator's start or a resume), leaves the ones not yet taken open
            # until the process exits (the disclosed residual, module docstring)
            held.extend(tfd for _t, tfd in txns)
            opened = sorted(t.name for t, tfd in txns
                            if _journal.classify_state(jr_fd, t, txn_fd=tfd) == "open")
        except (_journal.JournalError, OSError) as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
            raise AdoptApplyError("the adoption journal {} cannot be read ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
    finally:
        _close_held(held)   # the held transaction directories (deepest appended last), then jr_fd
    return owner, opened


def journal_clean_or_refuse(root_fd, journal_root):
    """The reconcile-first discipline (`opf record` step 1): inspect the adoption journal READ-ONLY
    (journal_state) and REFUSE on a held lock (a possibly-live owner is never seized) or any open
    transaction, directing the operator to the explicit reconcile() entry. An absent journal root is
    clean; an unreadable or corrupt journal refuses. Nothing is written here, recovery included."""
    owner, opened = journal_state(root_fd, journal_root)
    if owner is not None:
        raise AdoptApplyError("the adoption journal lock is held (pid {}); it is never seized. "
                              "Reconcile once no adoption run is live (fail-closed)".format(owner.get("pid")))
    if opened:
        raise AdoptApplyError("interrupted adoption transaction(s) {} must be reconciled before this run "
                              "(run reconcile()); nothing was written (fail-closed)".format(", ".join(opened)))


def reconcile(product_root):
    """The EXPLICIT recovery step, never an implicit side effect of a run: reconcile every adoption
    journal transaction to a terminal state from the journal ALONE (spec 14.2: the live tree is never
    required to resolve as a store, and this function never resolves it), rolling an open one FORWARD
    when every poststate already verifies, else BACK from its durable preimages, under the journal's own
    lock. An open init-store transaction is preverified first (_init_store_reversal_preverify): its
    rollback removes only the operation's own publication, so foreign content at a member path
    refuses the whole reconciliation fail-closed, preserved. A lock held by a possibly-live owner
    refuses (never seized); a confirmed-dead owner's lock is
    broken only through `_journal.reconcile_and_claim_stale`. Returns the (transaction, outcome) pairs;
    on the stale-lock path each outcome names what the break's own recovery DID (rolled-forward or
    rolled-back), never 'terminal' for work this call performed. The interrupted run itself stays
    refused, so the operator inspects the reconciled tree first."""
    root_fd = _open_product_root(product_root)
    journal_root = _journal_root(product_root)
    outcomes = []
    try:
        try:
            _journal.require_containment()
            st = _journal._lstat_contained(root_fd, JOURNAL_REL)
            if st is None:
                return outcomes
            if not stat.S_ISDIR(st.st_mode):
                raise AdoptApplyError("the adoption journal {} is not a directory; "
                                      "fail-closed".format(JOURNAL_REL))
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            try:
                # beneath the HELD jr_fd (round 3, the journal_state posture): the journal path is
                # never re-resolved after the contained open, so a concurrently swapped journal
                # cannot hide the held lock or substitute a decoy's.
                owner = _journal.read_lock_owner_at(jr_fd)
                txns = _journal._journal_txn_dirs(jr_fd, journal_root)
                opened = sorted(t.name for t in txns if _journal.classify_state(jr_fd, t) == "open")
                if owner is None and not opened:
                    return outcomes
                if owner is not None and not _journal.owner_confirmed_dead(owner):
                    raise AdoptApplyError("the adoption journal lock is held by a possibly-live owner "
                                          "(pid {}); it is never seized (fail-closed)".format(owner.get("pid")))
                # Before ANY rollback can run (the stale-lock break below reconciles every
                # transaction itself), prove each open init-store transaction's reversal removes
                # only the operation's own publication: a foreign file at a member path is
                # preserved and the whole reconciliation refuses fail-closed
                # (_init_store_reversal_preverify).
                for t in txns:
                    if t.name.endswith("." + INIT_STORE_PHASE) and t.name in opened:
                        _init_store_reversal_preverify(root_fd, product_root, jr_fd, t)
                stale = None
                if owner is not None:
                    # The stale-lock break reconciles every transaction ITSELF (under its arbitration
                    # lock), so the idempotent recover() re-run below would read each one 'terminal'.
                    # Record the pre-break states so the reported outcome names what that recovery DID.
                    stale = {t.name: _journal.classify_state(jr_fd, t) for t in txns}
                    if _journal.reconcile_and_claim_stale(journal_root, jr_fd, root_fd,
                                                          SESSION_ID) != "acquired":
                        raise AdoptApplyError("the adoption journal lock became live during "
                                              "reconciliation; it is never seized (fail-closed)")
                else:
                    _journal.acquire_lock(journal_root, SESSION_ID)
                try:
                    for txn in txns:
                        outcome = _journal.recover(jr_fd, txn, root_fd)
                        if stale is not None and stale.get(txn.name) == "open" and outcome == "terminal":
                            outcome = ("rolled-forward"
                                       if _journal.classify_state(jr_fd, txn) == "complete"
                                       else "rolled-back")
                        outcomes.append((txn.name, outcome))
                finally:
                    _journal.release_lock(journal_root)
            finally:
                _journal._close_fd_quietly(jr_fd)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the adoption journal {} cannot be reconciled ({}); "
                                  "fail-closed".format(JOURNAL_REL, exc))
    finally:
        store._close_fd_exc_safe(root_fd)
    return outcomes


def _default_store_present_without_manifest(product_root):
    """True ONLY for the one CANNOT-EVALUATE posture adoption exists for (spec 14.2): neither pointer file
    exists (the resolver reached the DEFAULT location) and a FRESH discovery at the product root reports
    `.working/` present with no machine store ("present": a foreign store-shaped tree awaiting
    dispositions). "multiple" (ambiguous), "one" (a store raced in), "absent" (the resolver's
    cannot-evaluate came from something else), and every discovery error read False (fail-closed)."""
    held = []
    try:
        try:
            held.append(_open_product_root(product_root))
        except AdoptApplyError as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never read as False
            return False
        try:
            status, _machine, _detail = store.discover_machine_store(held[0], Path(product_root))
        except (store.StoreError, OSError) as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never read as False
            return False
    finally:
        if held:
            # popped before its one close (never closed twice), closed in THIS frame so the #377 frame test
            # sees the discovery's exception in flight and keeps it; with none in flight the close error is
            # raised (fail-closed), never swallowed. Any other exception the close raises goes through the
            # one first-interrupt selection: an interrupt in flight (the discovery's) keeps propagating as
            # itself, that exception noted on it, never replaced by it (_journal._yield_close_exceptions)
            inflight = _journal._in_flight_in(sys._getframe())
            try:
                store._close_fd_exc_safe(held.pop())
            except OSError:
                raise                       # #377: raised only with nothing in flight here
            except BaseException as exc:    # noqa: BLE001  the first interrupt propagates as itself
                _journal._yield_close_exceptions(inflight, [exc])
    return status == "present"


def _store_posture_or_refuse(product_root):
    """Spec 5.7: a run MUST hold the single-writer lease before mutating a store, and this slice has no
    lease join, so a product root that resolves a store refuses, and a pointer that resolves one OUTSIDE
    the product root is refused by name (adoption evidence and archives belong at that store root, which
    this slice does not support). Spec 14.2: every OTHER posture that cannot be evaluated (a malformed or
    unreadable pointer, multiple machine stores, an undiscoverable root) fails closed too. ONLY the two
    first-adoption states adoption exists for are admitted, neither of which has a store or a lease home:
    NOT-ADOPTED, and the DEFAULT location's `.working/` present WITHOUT a valid manifest, re-proved by a
    fresh discovery (never inferred from the resolver's detail text)."""
    res = store.resolve_store(product_root)
    if res.status == store.RESOLVED:
        if res.pointer_source != "default" and (
                res.store_root is None
                or Path(os.path.abspath(res.store_root)) != Path(os.path.abspath(product_root))):
            raise AdoptApplyError("a pointer ({}) names a store outside the product root; adoption "
                                  "evidence and archives belong at that store root, which this slice "
                                  "does not support (fail-closed)".format(res.pointer_source))
        raise AdoptApplyError("the product root resolves a store ({}); this apply shell carries no "
                              "single-writer lease join yet (spec 5.7), so it refuses "
                              "(fail-closed)".format(res.machine_rel))
    if res.status == store.NOT_ADOPTED:
        return
    if res.pointer_source == "default" and _default_store_present_without_manifest(product_root):
        return
    raise AdoptApplyError("the store posture cannot be evaluated ({}); an ambiguous, malformed or "
                          "unreadable store input refuses (spec 14.2, "
                          "fail-closed)".format(res.detail))


def _committed_base_or_refuse(root_fd, journal_root, run_id, phase):
    """Spec 4.2, 14.1: a phase transaction extends the run's COMMITTED base, so it requires the base
    journal transaction to classify complete AND the live inventory.toml to hold the exact bytes that
    transaction's INTENT published (bytes check_apply_ops proved derived and valid when the base
    composed). An inventory.toml on disk alone, hand-planted or swapped since the commit, never admits a
    phase (fail-closed). The classification and the INTENT digest come from ONE captured frame set (QA
    round 3): the journal is read once, so a journal swapped between a classify read and an extract read
    can never split the decision across two observations. Returns the creates that INTENT published,
    ({path: content sha256}, {path: mode}), the committed preservation a retirement-stage removal
    pairs with and the mode it keeps."""
    base_rel = inventory_rel(run_id)
    try:
        if _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + run_id) is None:
            raise AdoptApplyError("phase {!r} needs the run's COMMITTED base transaction, and none "
                                  "exists; an inventory.toml on disk never stands in for it (spec 4.2); "
                                  "nothing written (fail-closed)".format(phase))
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
        try:
            # ONE captured frame set: classify_state's own state machine (_validate_terminal_agreement,
            # then the frame types) is applied to the same frames the INTENT digest is extracted from.
            frames, _torn, _good = _journal.read_frames(jr_fd, journal_root / run_id)
            _journal._validate_terminal_agreement(frames)
            types = [t for t, _ in frames]
            if _journal.F_INTENT not in types or _journal.F_COMPLETE not in types:
                raise AdoptApplyError("phase {!r} needs the run's COMMITTED base transaction, and {!r} "
                                      "is not complete; nothing written (fail-closed)".format(
                                          phase, run_id))
            intent = _journal._first(frames, _journal.F_INTENT)
        finally:
            inflight = _journal._in_flight_in(sys._getframe())
            try:
                _journal._close_fd_quietly(jr_fd)
            except BaseException as cexc:   # noqa: BLE001  the first interrupt propagates as itself
                _journal._yield_close_exceptions(inflight, [cexc])
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
        raise AdoptApplyError("cannot inspect the run's base transaction ({}); fail-closed".format(exc))
    ops = intent.get("ops", []) if isinstance(intent, dict) else []
    published = next(((op.get("poststate") or {}).get("content-sha256") for op in ops
                      if isinstance(op, dict) and op.get("op") == "create"
                      and op.get("path") == base_rel), None)
    fst, data = _read_live(root_fd, base_rel)
    if published is None or fst is None or _sha256(data) != published:
        raise AdoptApplyError("phase {!r} needs the base inventory.toml the committed base transaction "
                              "published, and the live bytes are missing or do not match that "
                              "transaction's INTENT digest (spec 4.2); nothing written "
                              "(fail-closed)".format(phase))
    creates = [op for op in ops
               if isinstance(op, dict) and op.get("op") == "create" and isinstance(op.get("path"), str)]
    return ({op["path"]: (op.get("poststate") or {}).get("content-sha256") for op in creates},
            {op["path"]: (op.get("poststate") or {}).get("mode") for op in creates})


def _attempt_name(txn, n):
    return txn if n == 1 else "{}.attempt-{}".format(txn, n)


def _next_attempt_or_refuse(root_fd, journal_root, run_id, phase):
    """The journal name this transaction opens under, decided from the adoption journal READ-ONLY before
    anything is written. With no prior attempt of this (run, phase) it is the transaction name itself. A
    base attempt of any state refuses: the base is the run's one binding, and nothing binds to a base that
    has not committed. For a phase, every prior attempt (`<run>.<phase>`, then `.attempt-<n>`) is
    classified: a COMMITTED one refuses (one run commits one transaction per phase), an open one cannot
    reach here (journal_clean_or_refuse refuses it first) and any state but rolled-back or nothing-opened
    refuses; otherwise the retry takes the next attempt number, every prior attempt's record retained as it
    stands (spec 14.1: a stage is bound to the run's approved plan and committed base, never to one
    attempt). An unreadable or corrupt prior attempt refuses (fail-closed)."""
    txn = _txn_name(run_id, phase)
    pattern = re.compile(re.escape(txn) + r"(?:\.attempt-([1-9][0-9]*))?\Z")
    prior = []
    try:
        if _journal._lstat_contained(root_fd, JOURNAL_REL) is None:
            return txn
        jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
        try:
            for entry in _journal._journal_txn_dirs(jr_fd, journal_root):
                match = pattern.match(entry.name)
                if match:
                    prior.append((int(match.group(1) or 1), entry.name, _journal.classify_state(jr_fd, entry)))
        finally:
            inflight = _journal._in_flight_in(sys._getframe())
            try:
                _journal._close_fd_quietly(jr_fd)
            except BaseException as cexc:   # noqa: BLE001  the first interrupt propagates as itself
                _journal._yield_close_exceptions(inflight, [cexc])
    except (_journal.JournalError, OSError) as exc:
        _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
        raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
    if not prior:
        return txn
    committed = [name for _n, name, state in prior if state == "complete"]
    if phase is None or committed:
        raise AdoptApplyError("run {} already has its transaction {!r}: one run takes one base transaction and "
                              "commits one transaction per phase, and changing approved work takes a fresh plan "
                              "with its own run id (spec 14.1); nothing written (fail-closed)".format(
                                  run_id, (committed or [txn])[0]))
    unsettled = sorted(name for _n, name, state in prior if state not in ("rolled-back", "nothing-opened"))
    if unsettled:
        raise AdoptApplyError("prior attempt(s) {} of {!r} are neither rolled back nor unopened; nothing "
                              "written (fail-closed)".format(", ".join(unsettled), txn))
    return _attempt_name(txn, max(n for n, _name, _state in prior) + 1)


def _compose_checked(root_fd, run_id, phase, compose, plan_digest=None, committed=None, committed_modes=None):
    """Compose ONE transaction against the live tree beneath `root_fd`, read-only: compose(ops) fills a fresh
    ApplyOps (carrying the committed base's archive copies and modes for a phase), the derived inventory
    seals it, check_apply_ops re-proves every invariant, and every committed archive copy a retirement
    removal pairs with is re-read live (_committed_pairs_live), so a refusal here has written nothing.
    Returns the sealed ApplyOps."""
    committed = dict(committed or {})
    committed_modes = dict(committed_modes or {})
    ops = ApplyOps(root_fd, run_id, phase, plan_digest)
    ops.committed = dict(committed)
    ops.committed_modes = dict(committed_modes)
    compose(ops)
    ops.seal()
    findings = check_apply_ops(run_id, phase, ops.ops, ops.staged, committed, ops.moves, committed_modes)
    findings = findings or _committed_pairs_live(root_fd, run_id, phase, ops.ops, committed, ops.moves)
    if findings:
        raise AdoptApplyError("the composed transaction is refused before it opens: {}".format(
            "; ".join(findings)))
    return ops


def _absent_journal_dirs(root_fd):
    """The JOURNAL_REL directories, shallowest first, absent beneath `root_fd` before this run prepares the
    journal: exactly those ensure_journal_dirs creates, so a refusal before the transaction opens can
    remove them again (_remove_journal_dirs)."""
    parts = JOURNAL_REL.split("/")
    for i in range(len(parts)):
        try:
            present = _journal._lstat_contained(root_fd, "/".join(parts[:i + 1]))
        except (_journal.JournalError, OSError) as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
            raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
        if present is None:
            return ["/".join(parts[:j + 1]) for j in range(i, len(parts))]
    return []


def _remove_journal_dirs(root_fd, created, raised=None):
    """Deepest first, remove the journal directories this run created, once its refusal has released the
    lock, so a refusal whose sweep returns ([], []) leaves the tree as it found it (the caller names
    anything else). rmdir only, never forced: a directory that is no
    longer empty (a transaction record, a retained or concurrent run's lock) stays in place. A directory
    the walk cannot reach (an ancestor it cannot open, such as one created with mode 000 under a 0777
    umask) or cannot remove never ends the sweep: every shallower one is still attempted, since an empty
    ancestor is removable through its own parent. Returns (stays, unconfirmed): what stays, each with its
    reason, omitting anything beneath a directory this sweep removed or found absent (an rmdir succeeds
    only on an empty directory); and what it removed whose parent fsync then failed, so the removal may
    not be durable. ([], []) when the tree is as found. When `raised` is a list, each parent close RECORDS
    its exception there, never raising it past an exception already in flight (an interrupt in an rmdir
    among them), and with none in flight raises the one it recorded, which stops the sweep. An exception a
    close inside _open_parent raised is never read as a missing or unreachable directory: it stops the
    sweep as itself (_journal._fd_release_fault)."""
    left = []
    unconfirmed = []
    gone = []
    for rel in reversed(created):
        parent = []     # owns the parent descriptor from its binding on; the finally empties it
        unwinding = False
        try:
            try:
                try:
                    pfd, name = _journal._open_parent(root_fd, rel)
                except FileNotFoundError as exc:
                    _journal._fd_release_fault(exc)  # a close's exception stops the sweep, never read as absent
                    continue    # never created: a preparation that failed part-way
                except (_journal.JournalError, OSError) as exc:
                    # a close's exception stops the sweep as itself, every fault recorded on it kept: the
                    # caller names it beside the outcome, never only as a "not reached" reason it may drop
                    _journal._fd_release_fault(exc)
                    left.append((rel, "not reached: {}".format(exc)))
                    continue
                parent.append(pfd)
                try:
                    os.rmdir(name, dir_fd=pfd)
                except FileNotFoundError:
                    gone.append(rel)
                    continue
                except OSError as exc:
                    left.append((rel, "not removed: {}".format(exc)))
                    continue
                gone.append(rel)
                try:
                    os.fsync(pfd)
                except OSError as exc:
                    unconfirmed.append("{} (parent fsync failed: {})".format(rel, exc))
            except BaseException:   # noqa: BLE001  re-raised: only marks an exception in flight
                unwinding = True
                raise
        finally:
            if raised is None:
                _close_held(parent)
            else:
                mark = len(raised)
                _close_held_into(parent, raised)
                if len(raised) > mark and not unwinding:
                    raise raised[mark]  # nothing in flight: this close's own exception stops the sweep
    return (["{} ({})".format(rel, why) for rel, why in left
             if not any(rel.startswith(g + "/") for g in gone)], unconfirmed)


_NOTHING_WRITTEN = "nothing written (fail-closed)"
# The trailing claims a refusal reason may carry from a shared helper. _refusal_text strips them, so a
# refusal of run_adopt_transaction carries only the claim derived from its observation.
_CLAIM_TAILS = ("; " + _NOTHING_WRITTEN, "; nothing was written (fail-closed)", " (fail-closed)", "; fail-closed")


def _entry_kind(st):
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "symlink" if stat.S_ISLNK(st.st_mode) else "special entry"


def _journal_listing(root_fd, keep=None, raised=None):
    """A no-follow listing of the adoption journal tree beneath the held product-root descriptor: each
    JOURNAL_REL component, the direct entries of each ancestor, and every entry beneath the journal root,
    keyed by relative path to (type, st_dev, st_ino). A directory the walk traverses is keyed by the fstat
    of the descriptor it opened, never by the stat by name before the open: one whose identity differs
    between the two (swapped in between) is keyed to ("unlisted", reason) and not traversed. A directory
    the walk cannot open or list is keyed `<dir>/` to ("unlisted", reason), a key no entry name can take
    (a name is never empty), so a listing never stands for what it did not see. When `keep` is a list, the
    descriptors this listing opened for the JOURNAL_REL components are handed to it as (rel, fd) pairs and
    stay open: while they are HELD no filesystem can hand a removed component's freed inode number to a
    recreation, so a later listing compared against this one can never read a recreated component as
    unchanged; the caller re-reads each held identity with fstat at that comparison and closes every kept
    descriptor. Each is appended to `keep` the moment it is bound (one list operation, no later transfer
    step), so from then on the caller alone closes it, on every path an exception reaches; one bound but
    interrupted before that append (a synchronous raise) is adopted by the close-out: with a `keep` it
    joins the kept descriptors (keyed ""), which the caller closes, and with no `keep` every descriptor
    is closed here through _close_held, each popped before its one close, the rest still closed when one
    close raises (the first exception re-raised, each later one noted on it); when `raised` is a list,
    that close-out and every child close of the recursive walk instead RECORD every exception they meet
    there, in order, so no close raises past an exception already in flight and the caller holds each
    exception object itself (an interrupt among them) and reports every one: a walk child close that
    records one with nothing in flight raises it, stopping the walk, and the final close-out raises none.
    An asynchronous interrupt
    (SIGINT, SIGTERM) that lands between a C call's return and the binding of the descriptor it returned,
    between a pop and its close in the close-out (that one descriptor), or at _close_held_into's own loop
    boundary, can leave a descriptor
    open until the process exits: a disclosed residual (module docstring), never a second close."""
    found = {}

    def opened_as(rel, st, fd):
        """True when the descriptor `fd` opened is the directory `st` stated, keying `rel` by that
        descriptor's fstat; else `rel` is keyed unlisted, naming the change."""
        try:
            fst = os.fstat(fd)
        except OSError as exc:
            found[rel] = ("unlisted", str(exc))
            return False
        if not stat.S_ISDIR(fst.st_mode) or (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
            found[rel] = ("unlisted", "it changed between this listing's stat and its open (st_dev {}, st_ino "
                          "{}, then st_dev {}, st_ino {})".format(st.st_dev, st.st_ino, fst.st_dev, fst.st_ino))
            return False
        found[rel] = (_entry_kind(fst), fst.st_dev, fst.st_ino)
        return True

    def walk(dfd, rel, deep):
        try:
            names = sorted(os.listdir(dfd))
        except OSError as exc:
            found[rel + "/"] = ("unlisted", str(exc))
            return
        for name in names:
            sub = rel + "/" + name
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except OSError as exc:
                found[sub] = ("unlisted", str(exc))
                continue
            found[sub] = (_entry_kind(st), st.st_dev, st.st_ino)
            if deep and stat.S_ISDIR(st.st_mode):
                child = []      # owns the child descriptor from its binding on; the finally empties it
                mark = len(raised) if raised is not None else 0
                try:
                    try:
                        child.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                             dir_fd=dfd))
                    except OSError as exc:
                        found[sub + "/"] = ("unlisted", str(exc))
                    if child and opened_as(sub, st, child[0]):
                        walk(child[0], sub, True)
                finally:
                    # with `raised`, recorded there, never raised past an exception in flight (a deeper
                    # frame's, an interrupt among them, which this frame's close would otherwise replace)
                    if raised is None:
                        _close_held(child)
                    else:
                        _close_held_into(child, raised)
                if raised is not None and len(raised) > mark:
                    raise raised[mark]  # nothing in flight: this close's own exception stops the walk

    parts = JOURNAL_REL.split("/")
    opened = keep if keep is not None else []   # handed over as each is bound: the caller's list owns it
    cur = root_fd
    try:
        for i, comp in enumerate(parts):
            rel = "/".join(parts[:i + 1])
            try:
                st = os.stat(comp, dir_fd=cur, follow_symlinks=False)
            except FileNotFoundError:
                break
            except OSError as exc:
                found[rel] = ("unlisted", str(exc))
                break
            found[rel] = (_entry_kind(st), st.st_dev, st.st_ino)
            if not stat.S_ISDIR(st.st_mode):
                break
            try:
                cur = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cur)
            except OSError as exc:
                found[rel + "/"] = ("unlisted", str(exc))
                break
            opened.append((rel, cur))
            if not opened_as(rel, st, cur):
                break
            walk(cur, rel, i == len(parts) - 1)
    finally:
        if cur != root_fd and cur not in [fd for _rel, fd in opened]:
            opened.append(("", cur))    # bound, but interrupted before its append: adopted, so one party closes it
        if keep is None:
            # deepest first, each popped before its one close; one that raises never abandons the rest,
            # and the first exception is re-raised, or every one is recorded in the caller's `raised`
            if raised is None:
                _close_held(opened)
            else:
                _close_held_into(opened, raised)
    return found


def _journal_components():
    """The JOURNAL_REL path components, shallowest first: .aiqt, .aiqt/adopt, .aiqt/adopt/journal."""
    parts = JOURNAL_REL.split("/")
    return ["/".join(parts[:i + 1]) for i in range(len(parts))]


def _entry_named(rel, seen, created, txn, mine, maybe_mine, lock_gone=False):
    """One entry present now that the run's first listing did not hold, named with what is true of it:
    (text, True when it is this run's or may be). The lock is this run's ONLY when it is the inode this
    run's acquire wrote (`mine`); with no such identity it may be this run's only while `maybe_mine` (an
    acquire of this run may have created it), else it is another run's; when `lock_gone` (this run's
    release read back no lock of its own while it still held the lock's descriptor) any lock present is
    another run's, whatever its identity, since that descriptor no longer pins the inode. A changed
    component of the journal
    path itself may be this run's when it is a directory: its preparation may have recreated it after
    `created` was taken; it creates nothing else there."""
    named = "{} ({})".format(rel, seen[0])
    journal = JOURNAL_REL + "/"
    if rel in created:
        if seen[0] == "directory":
            return named + ", a journal directory this run created", True
        return named + ", now a {} where this run had created a journal directory".format(seen[0]), True
    if rel == journal + txn or rel.startswith(journal + txn + "/"):
        return named + ", in this run's transaction record {!r}, which the journal keeps".format(txn), True
    if rel == journal + "lock":
        if mine is not None and seen[1:] == mine[:2] and not lock_gone:
            return named + ", this run's journal lock", True
        if mine is not None or not maybe_mine or lock_gone:
            return named + ", another run's journal lock, not this run's", False
        return named + ", a journal lock", True
    if rel in _journal_components():
        if seen[0] == "directory":
            return named + ", a component of the journal path that changed since this run began, possibly " \
                           "recreated by this run", True
        return named + ", a component of the journal path that changed since this run began, now a {}, " \
                       "which this run never creates there".format(seen[0]), False
    if (rel.startswith(journal) and "/" not in rel[len(journal):] and seen[0] != "directory"
            and rel != journal + "lock.break"):
        return named + (", a stray entry: the next run refuses on it as unreadable and reconcile() does not "
                        "touch it, so no sanctioned path clears it and it stays"), False
    return named, False


def _observed(before, after, created, txn, mine=None, maybe_mine=False, lock_gone=False):
    """What the run's closing listing shows against its first one: ([], False) when the two are equal and
    each saw everything, else (clauses, ours). A directory EITHER listing could not open or list makes the
    journal not fully observed, which is named first and always stands in place of the claim, even when
    both listings failed alike. Then each entry present now that was not (or not as the same type and
    inode), under a lead-in that attributes them to this run only when one of them is (or may be) its
    own, then each entry gone. An entry either listing could not state hides itself and everything below
    it; a directory either listing stated but could not list (keyed `<dir>/`) hides only what is below it,
    so an entry the closing listing observed absent is always named gone. `ours` is True when any clause
    concerns what this run wrote or cannot rule out. `lock_gone` is _entry_named's."""
    blind = {}
    for listing in (before, after):
        for rel, seen in listing.items():
            if seen[0] == "unlisted":
                blind.setdefault(rel, seen[1])
    said = []
    if blind:
        said.append("the adoption journal could not be fully observed, so what this run left there is not "
                    "known: {}".format("; ".join("{} could not be listed ({})".format(rel, why)
                                                 for rel, why in sorted(blind.items()))))
    named = [_entry_named(rel, after[rel], created, txn, mine, maybe_mine, lock_gone)
             for rel in sorted(rel for rel in after if rel not in blind and after[rel] != before.get(rel))]
    ours = bool(blind) or any(own for _text, own in named)
    if named:
        said.append(("the adoption journal now holds entries it did not hold when this run began: " if ours
                     else "the adoption journal now holds entries it did not hold when this run began, not "
                     "attributed to this run: ") + ", ".join(text for text, _own in named))
    gone = sorted(rel for rel in before if rel not in after and not any(
        rel.startswith(key) if key.endswith("/") else rel == key or rel.startswith(key + "/")
        for key in blind))
    if gone:
        said.append("entries present when this run began are gone: {}".format(", ".join(gone)))
    return said, ours


def _journal_unbound(jr_fd, held, after):
    """None when the closing listing observed the journal directory this run wrote to: the journal path
    still resolves to the identity (st_dev, st_ino) of the descriptor the run held, or it is absent and
    that held directory is unlinked (this run's cleanup removed it, empty). Else the clause saying the
    binding changed, or could not be observed (the listing could not see the journal path or one of its
    ancestors), in place of the claim: what the directory this run wrote to now holds is unobserved.
    Called while the descriptor is still held, so its inode cannot be reused by a later directory."""
    if jr_fd is None or held is None:
        return None
    now = after.get(JOURNAL_REL)
    if now is not None and now[0] == "directory" and now[1:] == held:
        return None
    if now is None:
        try:
            if os.fstat(jr_fd).st_nlink == 0:
                return None
        except OSError:
            pass
    if now is not None and now[0] == "unlisted" or now is None and any(
            after.get(r, ("",))[0] == "unlisted" or after.get(r + "/", ("",))[0] == "unlisted"
            for r in _journal_components()[:-1]):
        return ("the adoption journal path {} could not be observed, so whether it still resolves to the "
                "journal directory this run wrote to (st_dev {}, st_ino {}), and what that directory holds, "
                "is not known".format(JOURNAL_REL, held[0], held[1]))
    return ("the adoption journal path {} no longer resolves to the journal directory this run wrote to "
            "(st_dev {}, st_ino {}), so what that directory holds was not observed".format(
                JOURNAL_REL, held[0], held[1]))


def _refusal_text(head, observed=None, ours=True):
    """THE composer of every refusal of run_adopt_transaction: the reason, stripped of any trailing claim a
    shared helper wrote, then "nothing written" ONLY when `observed` is [] (the run's two journal listings
    are equal and complete, bound to the journal directory it wrote to, its cleanup durable, and its lock
    released durably or never taken), else the observed leftovers in place of it, said to be this run's
    only when `ours` (something observed is, or may be, its own). No observation (None) makes no claim."""
    for tail in _CLAIM_TAILS:
        if head.endswith(tail):
            head = head[:-len(tail)]
            break
    if observed is None:
        return head + " (fail-closed)"
    if not observed:
        return head + "; " + _NOTHING_WRITTEN
    if not ours:
        return "{}; {} (fail-closed)".format(head, "; ".join(observed))
    return "{}; NOT everything this run wrote is confirmed undone: {} (fail-closed)".format(
        head, "; ".join(observed))


def _unreadable_lock(exc, at_acquire=False):
    """The clause for a journal lock that cannot be read: never blind-removed, refused by the next run and
    by reconcile() alike, so no sanctioned path clears it, and the clause says so and names it. Only on
    the acquire path can it be this run's own unfinished write. An exception is named with every note
    recorded on it (_journal._msg_said)."""
    said = _journal._msg_said(exc) if isinstance(exc, BaseException) else exc
    return ("a journal lock is present but unreadable ({}){}; it is never blind-removed, the next run and "
            "reconcile() both refuse on it, and no sanctioned path clears it, so {}/lock STAYS".format(
                said, ", possibly this run's own unfinished write" if at_acquire else "", JOURNAL_REL))


def _lock_identity(jr_fd, keep=None, raised=None):
    """The journal lock beneath the held journal descriptor, no-follow: (st_dev, st_ino, its bytes), or
    None when absent. JournalError or OSError when what is present cannot be read. When `keep` is a list,
    the O_RDONLY|O_NOFOLLOW descriptor the identity was read from is handed to it and stays open: while it
    is HELD no filesystem can hand this lock's freed inode number to a later lock, so a read-back compared
    against this identity can never read a peer's lock as this run's; the caller closes it, on every path,
    only after that comparison. With no `keep`, and whenever what is present cannot be read, it is closed
    here. Ownership passes with the append itself (one list operation): the finally closes the descriptor
    ONLY while it is not in `keep`, so at every point exactly one party closes it, once. The open sits
    inside the protected block, so no instruction between its binding and that block goes uncovered by an
    exception raised in Python code; an asynchronous interrupt (SIGINT, SIGTERM) that lands between the
    open's C return and that binding, or inside the finally's close, can leave the descriptor open until
    the process exits, a disclosed residual (module docstring). When `raised` is a list, the close here
    RECORDS its exception there (_close_held_into), never raising it past an exception already in flight
    (an interrupt in the read among them) nor in place of an identity already read: the caller names
    every one beside its outcome."""
    lfd = None
    try:
        try:
            lfd = os.open("lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=jr_fd)
        except FileNotFoundError:
            return None
        st = os.fstat(lfd)
        if not stat.S_ISREG(st.st_mode):
            raise _journal.JournalError("journal lock is not a regular file (fail-closed)")
        identity = st.st_dev, st.st_ino, _journal._read_fd(lfd, cap=_journal._MAX_JOURNAL_READ_BYTES)
        if keep is not None:
            keep.append(lfd)
        return identity
    finally:
        if lfd is not None and (keep is None or lfd not in keep):
            if raised is None:
                _journal._close_fd_quietly(lfd)
            else:
                _close_held_into([lfd], raised)


def _close_held_into(fds, raised, close=None):
    """Close every descriptor a caller HOLDS in the list `fds` (each a bare fd or a (rel, fd) pair),
    emptying it, and append EVERY exception raised on the way (an interrupt or an injected exception) to
    the list `raised`, in order, raising none itself. Each close is `close(fd)` when given, else the quiet
    _journal._close_fd_quietly; a `close` that propagates its close error records that error too:
    store._close_fd_exc_safe, called from this frame, keeps a close quiet only for an exception whose
    traceback head is THIS frame, and an exception still unwinding the caller's frame (a finally of
    run_adopt_transaction) never has this frame as its head, so its close error is raised here and
    recorded, never swallowed. Each descriptor is taken out of the list BEFORE its
    one close, so a later pass over the same list (an outer finally) never closes a number again: an
    exception raised between the two can at worst leave that one descriptor open until the process exits
    (a disclosed residual, module docstring), never closed twice (a second close can shut another
    thread's reused number, man 2 close). Such an exception never abandons the rest: every descriptor
    still in the list is closed."""
    while fds:
        try:
            item = fds.pop()
            fd = item[1] if isinstance(item, tuple) else item
            if close is None:
                _journal._close_fd_quietly(fd)
            else:
                close(fd)
        except BaseException as exc:    # noqa: BLE001  keep closing; the caller reports every one
            raised.append(exc)


def _close_raised(raised):
    """Re-raise the FIRST interrupt of a close-out (`raised`, in the order they were raised), so a later
    interrupt is never demoted to a note on an earlier error, else its first exception; every other one
    is recorded on it as a note naming it, so none is discarded without trace; nothing when it is
    empty."""
    if raised:
        first = _journal._first_interrupt(raised)
        if first is None:
            first = raised[0]
        for later in raised:
            if later is not first:
                first.add_note("another close-out exception, recorded here and not re-raised: {}".format(
                    _journal._exc_said(later)))
        raise first


def _close_held(fds):
    """Close every descriptor held in the list `fds` through _close_held_into, every one even when a close
    raises, then re-raise the FIRST interrupt it met, else its first exception, each other one noted on it
    (_close_raised). With an exception in flight in the CALLER's frame (its `finally`), what the closes
    raised goes through the one first-interrupt selection instead (_journal._yield_close_exceptions), so
    an interrupt in flight there keeps propagating as itself, each close exception noted on it."""
    inflight = _journal._in_flight_in(sys._getframe(1))
    raised = []
    _close_held_into(fds, raised)
    if inflight is None:
        _close_raised(raised)
    else:
        _journal._yield_close_exceptions(inflight, raised)


def _close_out_said(raised):
    """The clause naming EVERY exception the transaction's close-outs recorded (`raised`, in order: the
    lock reads' own closes (_lock_identity's, the release read-back's among them), the early lock
    close's, then the final close-out's), or None when they recorded none. It is reported
    BESIDE the run's own outcome, never in its place. Each exception is named with what it says of its
    descriptor: an OSError is a close's own error, and that close has still released the descriptor
    (man 2 close); any other exception was raised between a descriptor's pop and the end of its close,
    so that one descriptor may stay open until the process exits."""
    if not raised:
        return None
    return "the run's descriptor close-out RAISED {} beside that outcome, never in its place: {}".format(
        "an exception" if len(raised) == 1 else "{} exceptions, in order".format(len(raised)),
        "; then ".join("{} ({})".format(_journal._exc_said(exc),
                                        "a close error: that close has still released its descriptor"
                                        if isinstance(exc, OSError) else "the descriptor it had popped "
                                        "may stay open until the process exits") for exc in raised))


def _observation_raised_said(raised):
    """The clause naming EVERY exception the transaction's closing cleanup or observation raised or
    recorded (`raised`, in order: the one that stopped it, then each the closing journal listing's own
    close-out recorded after it), reported BESIDE the run's own outcome, never in its place, and in place
    of any claim about what the run left there: that observation did not finish."""
    return ("the run's closing cleanup and observation of the adoption journal RAISED {} before it "
            "finished, so what this run left there is not known (one raised inside a close-out there may "
            "leave the one descriptor that close-out had popped open until the process exits): {}".format(
                "an exception" if len(raised) == 1 else "{} exceptions, in order".format(len(raised)),
                "; then ".join(_journal._exc_said(exc) for exc in raised)))


def _release_raised_said(raised):
    """The clause naming EVERY exception the run's lock release, its read-back or the failed acquire's lock
    read raised while the run's own outcome (a refusal, an exception, an interrupt) was already in flight
    (`raised`, in order): recorded there, reported BESIDE that outcome, never in its place; None when they
    raised none."""
    if not raised:
        return None
    return "the run's journal lock release or read RAISED {} beside that outcome, never in its place: {}".format(
        "an exception" if len(raised) == 1 else "{} exceptions, in order".format(len(raised)),
        "; then ".join(_journal._exc_said(exc) for exc in raised))


def _release_outcome(jr_fd, journal_root, mine, raised=None):
    """Release this run's own journal lock (ownership-checked), then read back, beneath the held journal
    descriptor, what that left, judged by the lock's identity, never by process identity: (state, detail,
    stop), each row as the OUTCOME TABLE below fixes it; the code below is that table.

    Inputs: R, what the release raised, and B, what its read-back raised, each one of: nothing; "own" (a
    JournalError or OSError, the lock code's own error); "ordinary" (any other Exception); "interrupt"
    (a BaseException that is not an Exception). The read-back runs after every R. And `mine`, the
    identity this run's acquire wrote (_lock_identity), known or None (that read found no lock).

    stop, the third element, is the exception the caller raises or records: the FIRST interrupt of R and
    B in the order raised (_journal._first_interrupt, the one first-interrupt selection), else the first
    of them that is not "own", else None. An "own" exception is never stop; the detail names it.

        R          B          stop  state
        nothing    nothing    None  from the read-back (below)
        own        nothing    None  from the read-back
        ordinary   nothing    R     from the read-back
        interrupt  nothing    R     from the read-back
        nothing    own        None  "unreadable"
        own        own        None  "unreadable"
        ordinary   own        R     "unreadable"
        interrupt  own        R     "unreadable"
        nothing    ordinary   B     "unreadable"
        own        ordinary   B     "unreadable"
        ordinary   ordinary   R     "unreadable"
        interrupt  ordinary   R     "unreadable"
        nothing    interrupt  B     "unreadable"
        own        interrupt  B     "unreadable"
        ordinary   interrupt  B     "unreadable" (B is the first interrupt, though raised second)
        interrupt  interrupt  R     "unreadable"

    The state from a read-back that raised nothing, for every R: a lock present that is `mine` (its inode
    and bytes) "stays"; one at `mine`'s inode with other bytes "altered" (altered in place, never presumed
    released); one present with `mine` None "unidentified"; else (no lock, or one at a fresh inode, a
    peer's) "unconfirmed" when R raised (the release may not be durable), "released" when it did not.

    The detail names EVERY exception R and B raised, in every row and every state, whether or not it is
    also stop, so none is dropped: with B nothing, R's clause (the exception itself when "own"), else "its
    release left it" on "stays" and None on "unidentified" and "released"; on "unreadable", B itself when
    R raised nothing and B is "own", else R's clause, then B's. Each exception is named with every note
    recorded on it (_journal._msg_said, _journal._exc_said), so a fault a close recorded on R is named too.

    The caller (_release_note, then run_adopt_transaction) gives every row the same result whether
    `mine` is known or None. Committed (no outcome in flight): stop None, the commit returns on
    "released", else raises AdoptCommittedLockError naming the state's clause; stop set, stop propagates
    as itself, COMMITTED and the state's clause in its note. Refused (an AdoptApplyError in flight): stop
    None or ordinary, the refusal stands, the state's clause and stop named in it; stop an interrupt,
    stop propagates as itself, the refusal and the state's clause in its note. Any other outcome in
    flight keeps stop beside it, through the same first-interrupt selection.

    The bytes acquire_lock writes carry no value unique to one acquire (uid, pid, pid-start, session, utc
    to the second), so EVERY caller holds an O_RDONLY descriptor on the lock from the moment `mine` is
    read (_lock_identity's keep) until after this read-back, on every path: while it is held, a filesystem
    that reuses freed inode numbers (ext4) can never hand `mine`'s inode to a peer's lock, a read-back at
    `mine`'s inode IS the file this run's acquire wrote (equal bytes its lock left in place, other bytes a
    genuine in-place alteration), and a peer's lock lands at a fresh inode, read as released, never as this
    run's. A per-acquire token would change the journal lock format other readers validate, so none is
    added here. When `raised` is a list, the read-back's close records its exception there
    (_lock_identity), so it never raises past one the release raised, nor conceals the outcome."""
    own = (_journal.JournalError, OSError)
    released = read = now = None
    try:
        _journal.release_lock(journal_root)
    except BaseException as exc:    # noqa: BLE001  every class: resolved by the table, never dropped
        released = exc
    try:
        now = _lock_identity(jr_fd, raised=raised)
    except BaseException as exc:    # noqa: BLE001  every class: resolved by the table, never dropped
        read = exc
    excs = [exc for exc in (released, read) if exc is not None]     # in the order raised
    stop = _journal._first_interrupt(excs)
    if stop is None:
        stop = next((exc for exc in excs if not isinstance(exc, own)), None)
    failed = (None if released is None else released if isinstance(released, own)
              else "its release raised {}".format(_journal._exc_said(released)) if isinstance(released, Exception)
              else "its release was interrupted ({})".format(_journal._exc_said(released)))
    if read is not None:
        if failed is None and isinstance(read, own):
            return "unreadable", read, stop
        return "unreadable", "; then ".join(s for s in (
            None if failed is None else failed if isinstance(failed, str)
            else "its release raised {}".format(_journal._exc_said(failed)),
            "its read-back raised {}".format(_journal._exc_said(read))) if s), stop
    if isinstance(failed, BaseException):
        failed = _journal._msg_said(failed)     # its message, then every note recorded on it
    if now is not None and mine is not None and now == mine:
        return "stays", failed or "its release left it", stop
    if now is not None and mine is not None and now[:2] == mine[:2]:
        return "altered", "the inode its acquire wrote now holds other content{}".format(
            "; {}".format(failed) if failed is not None else ""), stop
    if now is not None and mine is None:
        return "unidentified", failed, stop
    if failed is not None:
        return "unconfirmed", failed, stop
    return "released", None, stop


def _release_note(jr_fd, journal_root, mine, raised=None):
    """The run's normal lock release, at the end of a transaction or a refusal under the lock: (state,
    clause, stop), the clause None when the lock is released, else naming what stays or what may not be
    durable and every exception the detail names, reported as _failed_lock_state reports it on the acquire
    path, never swallowed. `raised` and stop as _release_outcome's (its outcome table)."""
    state, detail, stop = _release_outcome(jr_fd, journal_root, mine, raised)
    if state == "released":
        return state, None, stop
    if state == "unreadable":
        return state, _unreadable_lock(detail), stop
    if state == "unidentified":
        return state, ("a journal lock {}/lock is present after this run's release{}, and this run could not "
                       "read back the lock it wrote to tell whether it is its own: the next run refuses on "
                       "it".format(JOURNAL_REL, "" if detail is None else " ({})".format(detail))), stop
    if state == "unconfirmed":
        return state, ("this run's journal lock was released, but the release may not be durable "
                       "({})".format(detail)), stop
    if state == "altered":
        return state, _altered_lock(detail), stop
    return state, ("this run's journal lock STAYS ({}): the next run refuses on it, and reconcile() breaks "
                   "it once this process has exited".format(detail)), stop


def _altered_lock(detail):
    """The clause for this run's lock altered in place: an unresolved outcome, never a release."""
    return ("this run's journal lock was altered and stays ({}/lock: {}); it is not released, and the next "
            "run refuses on it".format(JOURNAL_REL, detail))


def _interrupted_lock_state(jr_fd, exc, raised=None):
    """What an interrupt, or another exception `exc`, inside acquire_lock left, OBSERVED beneath the held
    journal descriptor, never presumed: (state, clause). No lock present is "untaken"; a present lock this
    run cannot tell from another's (`exc` may have come before or after its create) is named and stays. An
    Exception is described as an error, only any other BaseException as an interrupt. When `raised` is a
    list, the read's close records its exception there, never raising it past `exc` (_lock_identity). Any
    other exception the read raises after `exc` resolves with it through the one first-interrupt selection:
    when `exc` is the first interrupt, or neither is an interrupt, the caller re-raises `exc` as itself and
    the read's is named in the "unreadable" clause; when only the read's is an interrupt, it propagates as
    itself with `exc` noted on it, never left only as its context."""
    try:
        now = _lock_identity(jr_fd, raised=raised)
    except (_journal.JournalError, OSError) as unread:
        if _journal._fd_release_fault(unread, raised) is not None:
            # a close's exception is never read as an unreadable lock: recorded in `raised`, named beside
            # the run's outcome, and what the acquire left is not known
            return "unidentified", ("whether this run's lock acquire left a journal lock {}/lock is not known: "
                                    "reading it raised a close exception, named beside this outcome; a lock "
                                    "there stays, and the next run refuses on it".format(JOURNAL_REL))
        return "unreadable", _unreadable_lock(unread, at_acquire=True)
    except BaseException as later:  # noqa: BLE001  raised after `exc`: never in place of the first interrupt
        if _journal._first_interrupt([exc, later]) is not later:
            return "unreadable", _unreadable_lock("its read raised {}".format(_journal._exc_said(later)),
                                                  at_acquire=True)
        later.add_note("raised by the lock read after the lock acquire raised {}, which is recorded "
                       "here".format(_journal._exc_said(exc)))
        raise
    if now is None:
        return "untaken", None
    return "unidentified", ("a journal lock {}/lock is present after this run's lock acquire {}, and this run "
                            "cannot tell whether it is its own: it stays, and the next run refuses on "
                            "it".format(JOURNAL_REL, "failed with an error ({})".format(_journal._exc_said(exc))
                                        if isinstance(exc, Exception) else "was interrupted"))


def _failed_lock_state(jr_fd, journal_root, error, raised=None):
    """What an acquire_lock that failed with an OSError left, read beneath the held journal descriptor, so
    the refusal reports it precisely: (state, clause, interrupt) as _release_note's. acquire_lock creates
    the lock BEFORE it writes and synchronizes it, so such a failure can leave this run's own lock, and it
    says so (error.lock_created). Only a lock this call created is released again (ownership-checked, as
    every refusal under the lock releases it), its identity taken first, and what stays, or a release
    whose durability is unconfirmed, is named. A lock this call did not create (another run's, another
    thread's of this same process, or any lock when the create itself failed) is never touched, nor is
    an unreadable one. The identity's own descriptor is HELD from the moment it is read until after the
    release's read-back (as every _release_outcome caller holds it), so a filesystem that reuses freed
    inode numbers can never hand this lock's inode to a peer's lock inside that window. When `raised` is
    a list, every close here (the identity's own, the read-back's and the held descriptor's) records its
    exception there, never raising it past one already in flight nor in place of the state returned. An
    exception the owner read or check raises that is not a JournalError or OSError (an interrupt, or one a
    close inside _journal raised) is returned as the interrupt, the lock named as staying, so the caller
    names it beside the acquire's own error, never in its place."""
    if not getattr(error, "lock_created", False):
        return "untaken", "this run left no journal lock (its create failed), and no lock present is touched", None
    held = []   # the identity's own descriptor, HELD across the release and its read-back (inode reuse)
    try:
        try:
            owner = _journal.read_lock_owner_at(jr_fd)
            mine = _lock_identity(jr_fd, keep=held, raised=raised)
            current = owner is not None and _journal._owner_is_current(owner)
        except (_journal.JournalError, OSError) as exc:
            if _journal._fd_release_fault(exc, raised if raised is not None else None) is None:
                return "unreadable", _unreadable_lock(exc, at_acquire=True), None
            # a close's exception is never read as an unreadable lock (this run's own, readable lock):
            # recorded in `raised` and named beside the refusal, the lock named as staying
            return "stays", ("this run's own journal lock WAS created, and reading it back raised a close "
                             "exception ({}), named beside this outcome: it stays, and the next run refuses "
                             "on it".format(_journal._exc_said(exc))), None
        except BaseException as exc:    # noqa: BLE001  returned: named beside the acquire's error
            return "stays", ("this run's own journal lock WAS created, and reading it back raised {}: it "
                             "stays, and the next run refuses on it".format(_journal._exc_said(exc))), exc
        if owner is None:
            return "untaken", "this run left no journal lock", None
        if not current:
            return "untaken", ("the journal lock present is another run's (pid {}), not this "
                               "run's".format(owner.get("pid"))), None
        state, detail, interrupt = _release_outcome(jr_fd, journal_root, mine, raised)
        if state == "released":
            return state, "this run's own journal lock WAS created, then released again", interrupt
        if state == "unreadable":
            return state, _unreadable_lock(detail), interrupt
        if state == "unconfirmed":
            return state, ("this run's own journal lock WAS created, then released again, but the release may "
                           "not be durable ({})".format(detail)), interrupt
        if state == "altered":
            return state, _altered_lock(detail), interrupt
        if state == "unidentified":
            return state, ("this run's own journal lock WAS created, and a journal lock is present after its "
                           "release{} that this run cannot tell from its own: it stays, and the next run "
                           "refuses on it".format("" if detail is None else " ({})".format(detail))), interrupt
        return state, ("this run's own journal lock WAS created and STAYS ({}): the next run refuses on it, and "
                       "reconcile() breaks it once this process has exited".format(detail)), interrupt
    finally:
        if raised is None:
            _close_held(held)
        else:
            _close_held_into(held, raised)


def _lock_said(lock_state, lock_note):
    """The lock outcome as one clause: the release's own clause when it named one, else the state's."""
    return lock_note or {"released": "this run's journal lock was released",
                         "untaken": "this run holds no journal lock",
                         "retained": "the journal lock is retained"}.get(
                             lock_state, "this run's journal lock outcome was not observed")


def run_adopt_transaction(product_root, run_id, compose, phase=None, plan_digest=None):
    """ONE journaled adoption transaction, the run's base transaction or one later phase's, through the
    shared 9.3 engine. Refusals BEFORE anything is written, in order: containment, a non-clean journal
    (reconcile-first: the adoption journal is inspected FIRST, per the module docstring), the store
    posture (no lease join, spec 5.7), an existing base transaction of this run or a COMMITTED transaction
    of this run and phase (_next_attempt_or_refuse: a phase attempt that rolled back or never opened is
    retried as the next attempt, its record retained), and for a phase anything but a committed,
    INTENT-digest-matched base inventory.toml (spec 4.2). Then, under the journal
    lock so observation and the journal's own capture are contiguous, compose(ops) fills a fresh ApplyOps
    against the live tree, the derived inventory seals it, check_apply_ops re-proves every invariant, and
    every committed archive copy a retirement removal pairs with is re-read live (_committed_pairs_live);
    a refusal there releases the lock and removes the journal directories this run created
    (_remove_journal_dirs). EVERY refusal here is worded by ONE composer (_refusal_text), and the claim
    "nothing written" is DERIVED, never hand-written: the run takes a no-follow listing of the adoption
    journal tree (_journal_listing) before it creates anything and again at the refusal, after its cleanup,
    and the claim stands ONLY when the two are equal and each saw the whole tree, the journal path still
    resolves to the directory this run held (_journal_unbound, checked before that descriptor is closed),
    the cleanup durable, and the lock released durably (or never taken); otherwise the refusal names what
    could not be observed and every entry present now that was not before, attributed to this run only
    when one is (or may be) its own (a
    pre-INTENT transaction directory, its frames.log and preimages included), what its cleanup left or may
    not have made durable, and what its lock release left, in place of the claim. The first listing's
    descriptors on the journal path components are HELD until that closing comparison (as jr_fd is), the
    components' before-identities re-read from the held descriptors with fstat, and the descriptor on the
    lock this run's acquire wrote is HELD through the release's read-back and, on every outcome but a
    confirmed-gone lock, through that closing comparison too, so a filesystem that reuses freed
    inode numbers (ext4) can never hand a removed component's inode to a recreation, or the released lock's
    inode to a peer's lock, and make the comparison read it as unchanged or as this run's own. The lock's
    descriptor is closed right after that read-back ONLY when the read-back, taken while the descriptor
    still pinned the inode, saw this run's lock gone (released, or released but maybe not durably),
    BEFORE the cleanup and the closing listing (an NFS
    client keeps a file unlinked while open as a .nfsXXXX entry until its last close), and the closing
    listing then attributes any lock present from that recorded outcome; on every other outcome, an
    interrupted read-back included, the descriptor stays held, so the closing comparison never reads a
    peer's lock on a reused inode as this run's own. Every descriptor this run holds is owned by a list
    from its binding on (the append is the handoff) and closed pop-before-close, so no number is closed
    twice; an asynchronous interrupt (SIGINT, SIGTERM, delivered through any thread), or any exception
    raised between a descriptor's pop and its close, can still leave a descriptor open until the process
    exits, here or inside a _journal helper, a disclosed residual (module docstring), so no leak-freedom
    under interrupt is claimed; the final close-out closes each list in turn and records every exception
    it meets, as the early lock close does, so one such exception never abandons the descriptors after
    it, and none replaces the run's own outcome: a commit still raises AdoptCommittedLockError
    (COMMITTED, its changes LANDED), a refusal still refuses, and an exception still propagates as
    itself, each naming every close-out exception beside it. An exception the closing cleanup or
    observation raises (the closing listing's own close-out included) is named the same way, beside the
    outcome, and the refusal then claims nothing about what the run left. An interrupt (a BaseException
    that is not an Exception) a close-out or that observation meets propagates as ITSELF, never wrapped
    in an Exception, with the run's own outcome (its commit, refusal or exception) named in its note.
    A leftover only the
    reconcile-first discipline clears is left to it rather than to hand removal, and one no sanctioned
    path clears is named as such. The lock release is read back by identity (the inode and content this
    run's acquire wrote, _lock_identity), never by process identity; at the end of a committed transaction
    a release that leaves the lock or may not be durable, or a close-out that raised, raises
    AdoptCommittedLockError, whose changes LANDED. An interrupt (any BaseException but a refusal), one
    raised inside the release included, propagates as itself, with the transaction state and the
    observed outcome attached as a note. One the release, its read-back or a failed acquire's lock read
    raises (a close inside a _journal helper among them) while the run's own outcome (a refusal, an
    exception, an interrupt) is already in flight is RECORDED, named beside that outcome, never in its
    place; an interrupt among them still propagates as itself, that outcome, a refusal's reason included,
    named in its note.
    Disclosed too: a concurrent run that
    prepared the journal but has not yet taken its lock can find the directory removed by that cleanup,
    and then refuses at the lock. A failure that may have left the transaction open RETAINS the lock (and
    the journal) so every later run refuses into reconcile().
    A transaction opened from inside the stage driver's composition (a plan op handler opening its own)
    refuses before anything else: one run takes one base transaction. Returns the transaction
    name (the attempt's, for a retried phase)."""
    denied = getattr(_COMPOSITION, "denied", None)
    if denied is not None:
        denied.append("a nested adoption transaction")
        raise AdoptApplyError(_refusal_text("a plan op handler may not open its own adoption transaction: every "
                                            "op composes into the driver's one base transaction (spec 14.1, "
                                            "14.2)"))
    txn = _txn_name(run_id, phase)
    if not callable(compose):
        raise AdoptApplyError(_refusal_text("compose must be a callable that fills the transaction's ApplyOps"))
    journal_root = _journal_root(product_root)
    root_fd = jr_fd = jr_id = mine = before = interrupted = None
    maybe_mine = False
    created = []
    held_components = []    # the first listing's component descriptors, HELD until the closing comparison
    mine_held = []          # the lock identity's descriptor, HELD until after the closing comparison
    anchors = []            # the product-root, then the journal descriptor: closed LAST, by the teardown
    failure = None
    closeout = []           # every exception this run's close-outs and lock read closes record: named beside its outcome
    release_raised = []     # every exception the lock release or read raised with the outcome in flight: named beside it
    pre = fin = 0           # closeout's length as the lock release, then the final close-out, began (time order)
    pending = None          # the run's own outcome in flight through the lock's release, if any
    lock_state, lock_note = "untaken", None
    held = retain = done = entered = False
    try:
        anchors.append(_open_product_root(product_root))    # owned by `anchors` from its binding on
        root_fd = anchors[-1]
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise AdoptApplyError("{} (fail-closed)".format(exc))
        # BEFORE this run creates anything. Not the closing path: its walk's child closes RAISE (no
        # `raised` list) through _close_held, which resolves them against an exception already in flight
        # through the one first-interrupt selection, so an interrupt there propagates as itself; it fails
        # the run before it writes anything.
        before = _journal_listing(root_fd, keep=held_components)
        journal_clean_or_refuse(root_fd, journal_root)
        _store_posture_or_refuse(product_root)
        txn = _next_attempt_or_refuse(root_fd, journal_root, run_id, phase)
        committed, committed_modes = {}, {}
        if phase is not None:
            committed, committed_modes = _committed_base_or_refuse(root_fd, journal_root, run_id, phase)
        created = _absent_journal_dirs(root_fd)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            anchors.append(_journal.open_journal_root_fd(root_fd, JOURNAL_REL))     # owned from its binding on
            jr_fd = anchors[-1]
            jr_st = os.fstat(jr_fd)
            jr_id = (jr_st.st_dev, jr_st.st_ino)    # the journal directory this run writes to
        except (_journal.JournalError, OSError) as exc:
            _journal._fd_release_fault(exc)    # a close's exception is raised as itself, never this refusal
            raise AdoptApplyError("cannot prepare the adoption journal {} ({})".format(JOURNAL_REL, exc))
        try:
            maybe_mine = True
            try:
                _journal.acquire_lock(journal_root, SESSION_ID)
            except _journal.JournalError as exc:   # the O_EXCL create refused: the lock was never this run's
                if not _journal._fd_release_raised(exc):
                    maybe_mine = False
                    raise AdoptApplyError("cannot take the adoption journal lock ({})".format(exc))
                # a close's exception AFTER the create (the lock's own close): observed, never presumed
                # untaken, and it propagates as itself (_journal._fd_release_fault), what it left named on it
                lock_state = "unobserved"
                lock_state, lock_note = _interrupted_lock_state(jr_fd, exc, closeout)
                _journal._fd_release_fault(exc)
            except OSError as exc:
                maybe_mine = getattr(exc, "lock_created", False)
                lock_state = "unobserved"   # until the read below returns: never presumed untaken
                lock_state, clause, interrupted = _failed_lock_state(jr_fd, journal_root, exc, closeout)
                if interrupted is not None or lock_state not in ("untaken", "released"):
                    lock_note = clause      # named once, by the composer, in place of the claim
                    if interrupted is not None:
                        # beside this refusal, never in its place: an interrupt still propagates as
                        # itself, with this acquire's error, the refusal's reason, named in its note
                        release_raised.append(interrupted)
                    raise AdoptApplyError("cannot take the adoption journal lock ({})".format(exc))
                raise AdoptApplyError("cannot take the adoption journal lock ({}); {}".format(exc, clause))
            except BaseException as exc:   # an interrupt or other error inside acquire: observed, never presumed
                # "unobserved" until that read returns: an exception the read raises propagates past the
                # assignment, and acquire may have created the lock, so the run never claims it holds none
                lock_state = "unobserved"
                lock_state, lock_note = _interrupted_lock_state(jr_fd, exc, closeout)
                raise
            held = True
            lock_state = "held"
            try:
                # what THIS acquire wrote, its descriptor HELD, so the release reads back by identity
                # and a freed-inode reuse can never read a peer's lock as this run's
                mine = _lock_identity(jr_fd, keep=mine_held, raised=closeout)
                maybe_mine = mine is None
            except (_journal.JournalError, OSError) as exc:
                raise AdoptApplyError("cannot read back the adoption journal lock this run took ({})".format(exc))
            # THE STALE-PREFLIGHT GUARD (QA round 2): the refusals above ran BEFORE the lock, and
            # the other writer (an init-store dispatch) takes THIS SAME lock, so its whole
            # transaction may have opened, published a store and completed between those checks
            # and this acquisition (or an interrupted one may have appeared). Re-prove every
            # consequential prerequisite HERE, under the held lock, before anything composes: no
            # open transaction appeared, the store posture is still a first-adoption state, this
            # run and phase still have no transaction, and a phase's committed base still
            # verifies. The pre-lock copies stay as cheap early refusals only; nothing composed
            # before the lock authorizes anything after it.
            _owner, opened = journal_state(root_fd, journal_root)
            if opened:
                raise AdoptApplyError("interrupted adoption transaction(s) {} appeared before the "
                                      "lock was acquired and must be reconciled first (run "
                                      "reconcile()); nothing written (fail-closed)".format(
                                          ", ".join(opened)))
            _store_posture_or_refuse(product_root)
            try:
                prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
            except (_journal.JournalError, OSError) as exc:
                raise AdoptApplyError("cannot inspect the adoption journal ({}); "
                                      "fail-closed".format(exc))
            if prior is not None:
                raise AdoptApplyError("run {} grew its transaction {!r} before the lock was "
                                      "acquired: one run takes one transaction per phase, and "
                                      "changing approved work takes a fresh plan with its own "
                                      "run id (spec 14.1); nothing written "
                                      "(fail-closed)".format(run_id, txn))
            if phase is not None:
                committed, committed_modes = _committed_base_or_refuse(root_fd, journal_root, run_id, phase)
            ops = _compose_checked(root_fd, run_id, phase, compose, plan_digest, committed, committed_modes)
            staged = dict(ops.staged)

            def staged_reader(op):
                data = staged.get(op["path"])
                if not isinstance(data, bytes):
                    # raised INSIDE apply, so the engine rolls back rather than leaving an open transaction.
                    raise _journal.JournalError("no staged bytes for {!r} (fail-closed)".format(op["path"]))
                return data

            header = dict(kind=KIND, run_id=run_id, phase=phase or "base", operation=OPERATION)
            entered = True      # from here the journal may hold this transaction's records by design
            try:
                _journal.run_transaction(root_fd, jr_fd, journal_root, txn, header, ops.ops,
                                         staged_reader, SESSION_ID)
            except Exception as exc:
                # the engine's own error, or any Exception a close raised (_fd_release_raised: the engine
                # rolled back on it), classified and named with every note recorded on it
                if not isinstance(exc, (_journal.JournalError, OSError)) and not _journal._fd_release_raised(exc):
                    raise
                # An absent transaction directory reads as nothing-opened (read_frames), so a failure
                # before INTENT is told apart from one after it (the record-publication precedent).
                try:
                    state = _journal.classify_state(jr_fd, journal_root / txn)
                except _journal.JournalError as unread:     # named below, never dropped
                    state = "in an unreadable state (its classification raised {})".format(
                        _journal._exc_said(unread))
                if state == "nothing-opened":
                    entered = False
                    raise AdoptApplyError("the adoption transaction was refused before it opened "
                                          "({})".format(_journal._msg_said(exc)))
                if state == "rolled-back":
                    raise AdoptApplyError("the adoption transaction was refused and rolled back to the "
                                          "prestate ({}): the product tree is as it was, and the journal "
                                          "keeps this transaction's terminal record".format(_journal._msg_said(exc)))
                if state == "complete" and _journal._fd_release_raised(exc):
                    # a close after the COMPLETE frame was made durable (publish's frames-log or directory
                    # close): the transaction COMMITTED, so that result stands, the lock released as on any
                    # commit, and the close's exception, every fault recorded on it kept, is named beside it
                    # (AdoptCommittedLockError), never a FAILED refusal for a change that landed
                    closeout.append(exc)
                    done = True
                    return txn
                retain = True
                lock_state = "retained"
                raise AdoptApplyError("the adoption transaction {} FAILED and is {} ({}); the journal lock "
                                      "is retained so the next run refuses into reconcile() "
                                      "(fail-closed)".format(txn, state, _journal._msg_said(exc)))
            done = True
            return txn
        except BaseException as exc:    # noqa: BLE001  re-raised: only marks the outcome in flight
            pending = exc
            raise
        finally:
            if held and not retain:
                pre = len(closeout)     # what closeout recorded before the release (the lock read's close)
                try:
                    try:
                        lock_state, lock_note, interrupted = _release_note(jr_fd, journal_root, mine, closeout)
                    except BaseException as exc:    # noqa: BLE001  the release note raised outside its read-back
                        if pending is None:
                            raise
                        release_raised.append(exc)  # beside the outcome in flight, never in its place
                finally:
                    # closed right after the read-back ONLY when that read-back, taken while this
                    # descriptor still pinned the lock's inode, saw this run's lock gone (released, or
                    # released but maybe not durably), BEFORE the cleanup and the closing listing: an NFS
                    # client renames a file unlinked while still open to .nfsXXXX until its last close, an
                    # entry the closing listing would misreport and one that keeps rmdir from the journal.
                    # On EVERY other outcome (stays, altered, unidentified, unreadable, or a read-back an
                    # interrupt escaped, which left lock_state "held") the lock may still exist, so this
                    # descriptor stays HELD through the closing comparison: while it is held no filesystem
                    # can hand the lock's freed inode to a peer's lock and make that comparison read the
                    # peer's lock as this run's own. An interrupted read-back behind a release that did
                    # unlink can then hold a .nfsXXXX entry alive into the closing listing, which the
                    # refusal names as a leftover: disclosed, never a misattribution. This close-out
                    # RECORDS every exception it meets in `closeout` and raises none, so one never
                    # replaces the run's own outcome (a commit, a refusal, an interrupt): the reporting
                    # below names it beside that outcome, as it does the final close-out's.
                    if lock_state in ("released", "unconfirmed"):
                        _close_held_into(mine_held, closeout)
                if interrupted is not None:
                    if pending is None:
                        raise interrupted
                    release_raised.append(interrupted)  # beside the outcome in flight, never in its place
    except BaseException as exc:
        failure = exc
        raise
    finally:
        teardown, listed = None, []     # listed: every exception the closing sweep's and listing's closes record
        try:
            observed = said = None      # inside the teardown-protected try from its first statement on
            ours = True
            left, unconfirmed = [], []
            if before is not None and not done:
                # bound before the sweep, the state's clause too: a sweep that raises keeps the lock clause
                said = ([lock_note] if lock_note else
                        [_lock_said(lock_state, None)] if lock_state not in ("untaken", "released") else [])
            if created and not (done or retain):
                left, unconfirmed = _remove_journal_dirs(root_fd, created, raised=listed)
            if before is not None and not done:
                if (left or unconfirmed) and not entered:
                    if left:
                        said.append("the refusal's cleanup is INCOMPLETE: journal directories this run created "
                                    "STAY: {}".format("; ".join(left)))
                    if unconfirmed:
                        said.append("the refusal's cleanup is UNCONFIRMED: journal directories this run created "
                                    "were removed, but the removal may not be durable: {}".format(
                                        "; ".join(unconfirmed)))
                    said[-1] += (". They are left to the reconcile-first discipline, never to hand removal: a "
                                 "later run inspects the adoption journal before it writes, reuses these "
                                 "directories, and refuses into reconcile() on a held lock or an open "
                                 "transaction there")
                after = _journal_listing(root_fd, raised=listed)
                if listed:
                    raise listed[0]     # its close-out raised: this observation stops, every one named below
                for rel, cfd in held_components:
                    # the before identity, re-read from the descriptor HELD since the first listing: while
                    # it is held no filesystem reuses its inode, so a recreation never compares equal
                    if before.get(rel, ("unlisted",))[0] == "unlisted":
                        continue
                    try:
                        cst = os.fstat(cfd)
                    except OSError as exc:
                        before[rel] = ("unlisted", str(exc))
                    else:
                        before[rel] = (_entry_kind(cst), cst.st_dev, cst.st_ino)
                # the lock is attributed from the recorded release outcome: on a confirmed-gone lock
                # (lock_gone) its descriptor is closed by now, and the read-back taken while it was held
                # rules this run's own lock out; on EVERY other outcome that descriptor is still HELD
                # here, so no freed-inode reuse can make this comparison read a peer's lock as this run's
                delta, ours = _observed(before, after, created, txn, mine, maybe_mine,
                                        lock_state in ("released", "unconfirmed"))
                unbound = _journal_unbound(jr_fd, jr_id, after)
                ours = ours or bool(said) or unbound is not None
                observed = said + ([unbound] if unbound else []) + delta
        except BaseException as exc:    # noqa: BLE001  the observation failed: named beside the outcome below
            teardown = exc
        finally:
            # every descriptor the run still holds is owned by one of these three LIVE lists:
            # held_components and (on any outcome but a confirmed-gone lock) mine_held, both HELD through
            # the closing comparison, and anchors, whose jr_fd is held through the closing observation so
            # its identity stays this run's, then root_fd. Each list is emptied pop-before-close, so no
            # number is closed twice, and each close-out RECORDS every exception it meets in `closeout`
            # and raises none: an exception raised between a pop and its close (an interrupt or an
            # injected exception) leaves that one descriptor open until the process exits, never the rest
            # of its list or the lists after it, and it never replaces the run's own outcome: below, a
            # commit still reports COMMITTED, a refusal still refuses and a propagating exception still
            # propagates, each with every close-out exception named beside it. An interrupt at a
            # close-out's own loop boundary, or between these calls, can still leave what was not reached
            # open and propagate in place of that outcome (the disclosed residual, module docstring).
            # mine_held: any still held, a stay, a retained lock, or no release reached. anchors close
            # through store._close_fd_exc_safe (#377), so a close error there is RECORDED, never
            # swallowed: a run with no exception in flight still raises it (a commit as the cause of its
            # AdoptCommittedLockError), and an exception or refusal in flight stays the outcome, the
            # close error named beside it.
            fin = len(closeout)     # every later closeout entry comes after the closing sweep and listing
            _close_held_into(held_components, closeout)
            _close_held_into(mine_held, closeout)
            _close_held_into(anchors, closeout, store._close_fd_exc_safe)
        closed_said = "; ".join(s for s in (_release_raised_said(release_raised), _close_out_said(closeout))
                                if s) or None
        if done:
            phase_said = "the adoption transaction {} COMMITTED: its product-tree changes LANDED".format(txn)
        elif entered:
            phase_said = ("the adoption transaction {} was entered and is NOT confirmed committed or "
                          "undone".format(txn))
        else:
            phase_said = "the adoption transaction {} did not open".format(txn)
        seen_said = None
        # every exception the closing cleanup or observation raised, then each later one a close of the
        # closing sweep or listing recorded: none is lost, an interrupt among them included
        torn = ([teardown] if teardown is not None else []) + [exc for exc in listed if exc is not teardown]
        if torn:
            # the closing cleanup or observation raised (the closing listing's own close-out included):
            # never in place of the run's own outcome, which stands below with each named beside it, and
            # what that observation did not finish is not claimed (no "nothing written")
            seen_said = _observation_raised_said(torn)
            observed, ours = (said or []) + [seen_said], True
        # an interrupt (a BaseException that is not an Exception) a close-out or the closing observation
        # met propagates as ITSELF, never wrapped in an Exception nor dropped into a refusal's text; the
        # run's own outcome (its commit, its refusal or its exception) is named beside it in its note
        # every recorded exception in the order it was RAISED: the lock read's close before the release
        # (closeout[:pre]); the release's own exceptions (release_raised: the release's interrupt, or the
        # failed acquire's, came before the closes its read-back then made), then those closes and the
        # early lock close (closeout[pre:fin]); the closing sweep and listing (torn); the final
        # close-out (closeout[fin:]). The one first-interrupt selection reads this order, so a later
        # close-out interrupt never propagates in place of an earlier one
        ordered = closeout[:pre] + release_raised + closeout[pre:fin] + torn + closeout[fin:]
        stop = None
        if failure is None or isinstance(failure, Exception):
            stop = _journal._first_interrupt(ordered)
        if stop is not None:
            if failure is None:
                stood = [phase_said] + [n for n in (lock_note, seen_said) if n]
            elif isinstance(failure, AdoptApplyError):
                stood = [phase_said, "the run's refusal stands beside this interrupt: "
                         + _refusal_text(str(failure), observed, ours)]
            else:
                stood = [phase_said, "the run's own exception stands beside this interrupt: {}".format(
                    _journal._exc_said(failure))] + (observed or [])
            stood = "; ".join(stood + ([closed_said] if closed_said else []))
            stop.add_note(stood if stood.endswith(" (fail-closed)") else stood + " (fail-closed)")
            raise stop
        if failure is not None and (not isinstance(failure, Exception) or failure is interrupted):
            # an interrupt still propagates as itself, with the transaction state and the outcome named
            if failure is interrupted or observed or closed_said:
                lock_said = _lock_said(lock_state, lock_note)
                failure.add_note("; ".join([phase_said] + ([] if lock_said in (observed or []) else [lock_said])
                                           + (observed or []) + ([closed_said] if closed_said else []))
                                 + " (fail-closed)")
        elif failure is None:
            # the transaction COMMITTED: that result stands, with a close-out exception named beside it
            noted = [n for n in (lock_note, closed_said, seen_said) if n]
            if noted:
                raise AdoptCommittedLockError(txn, "; ".join(noted)) \
                    from (ordered[0] if ordered else None)
        elif isinstance(failure, AdoptApplyError):
            # the refusal stands, with every close-out exception added to it
            text = _refusal_text(str(failure), observed, ours) + ("; " + closed_said if closed_said else "")
            if text != str(failure):
                raise AdoptApplyError(text) from failure
        elif said:
            # an exception once the transaction was entered names that phase, never only what it observed
            raise AdoptApplyError(_refusal_text(str(failure), ([phase_said] if entered else []) + observed, ours)
                                  + ("; " + closed_said if closed_said else "")) from failure
        elif observed or closed_said or entered:
            failure.add_note("; ".join(([phase_said] if entered else []) + (observed or [])
                                       + ([closed_said] if closed_said else []))
                             + " (fail-closed)")   # not a refusal: it propagates as itself


# --- init-store: the coupled-init substrate inside the adoption transaction (slice 3) ---------------------

# The phase suffix of the one init-store transaction an adoption run may take in this journal.
INIT_STORE_PHASE = "initstore"
# The EXPLICIT unbound-digest marker: a member whose bytes cannot be bound at plan time (init.toml,
# which embeds the operation id, time and HEAD) must carry exactly this value, so a digest that only
# LOOKS bound is refused; any plan-bindable member carrying it is refused the other way.
UNBOUND_DIGEST = "sha256:" + "0" * 64


def _init_store_scaffold(first_adoption, changelog_created):
    """(manifest path, {path: digest or None}) of the COMPLETE creation set of the coupled-init
    substrate's scaffold: a sha256:<hex> digest for every payload the D1 builders fix at plan time
    (the machine ledgers, with a zero-seeded counters only on a first adoption; the per-type
    indexes; the root pointer; and CHANGELOG.md when the plan creates it), and None where the bytes
    are not knowable HERE before the operation runs: init.toml, which embeds the operation id, time
    and HEAD and so is never plan-bindable (the row carries the explicit UNBOUND_DIGEST marker and
    the live bytes are verified against the operation's validated recorded plan instead); and each
    declared initial view and a re-adoption's seeded counters, which ARE plan-bindable (the planner
    derives them from the pinned sources and snapshot), carried in the row and verified after
    publication. The view destinations come from the default manifest's own declared views, and the
    machine manifest digest pins the default manifest (baseline record types only, every module
    tier off)."""
    import _opf_check
    import _opf_init
    manifest = _opf_init.build_manifest()
    documents = {
        store.MANIFEST_NAME: manifest,
        "version.toml": _opf_init.build_version(),
        "worklog.toml": _opf_init.build_worklog(),
    }
    if first_adoption:
        documents["counters.toml"] = _opf_init.build_counters()
    for tname in _opf_init.INDEX_TYPES:
        documents[tname + _opf_check.INDEX_SUFFIX] = _opf_init.build_index(tname)
    machine = "{}/{}".format(store.WORKING_DIRNAME, store.DEFAULT_MACHINE_SUBDIR)
    scaffold = {"{}/{}".format(machine, n): "sha256:" + _sha256(t.encode("utf-8"))
                for n, t in documents.items()}
    if not first_adoption:
        scaffold[init_op.COUNTERS_RELPATH] = None
    scaffold[store.POINTER_REL] = "sha256:" + _sha256(
        emit_checked({"store": {"target": "dir:."}}).encode("utf-8"))
    if changelog_created:
        scaffold[init_op.CHANGELOG_RELPATH] = "sha256:" + _sha256(init_op._CHANGELOG_PAYLOAD)
    scaffold[init_op.PROVENANCE_RELPATH] = None
    for view in tomllib.loads(manifest)["views"].values():
        scaffold[view["target"]] = None
    return "{}/{}".format(machine, store.MANIFEST_NAME), scaffold


def _init_store_members_or_refuse(op_row, scaffold, manifest_rel):
    """{path: digest} of the row's members, refused unless they are EXACTLY the scaffold's complete
    creation set ("the scaffold writes exactly its members": a missing member is refused like a
    stray one) with the manifest digest-bound to the default manifest, every plan-bindable digest
    bound (the builders' exact bytes for the fixed payloads, checked HERE before anything is
    written), and the one plan-unbindable member (init.toml) carrying the explicit UNBOUND_DIGEST
    marker, never a value that only looks bound."""
    members = {m["path"]: m["digest"] for m in op_row["members"]}
    missing = sorted(set(scaffold) - set(members))
    stray = sorted(set(members) - set(scaffold))
    if missing or stray:
        raise AdoptApplyError("the row's members are not exactly the scaffold's creations (missing: "
                              "{}; outside it: {}); the scaffold writes exactly its members, the "
                              "frozen machine store's manifest ({}) among them (nothing "
                              "written)".format(missing, stray, manifest_rel))
    for path in sorted(scaffold):
        digest = scaffold[path]
        if digest is None:
            if path == init_op.PROVENANCE_RELPATH:
                if members[path] != UNBOUND_DIGEST:
                    raise AdoptApplyError("member {} embeds the operation id, time and HEAD, so its "
                                          "digest cannot be bound at plan time: the row carries the "
                                          "explicit all-zero unbound marker, never a value that only "
                                          "looks bound (nothing written)".format(path))
            elif members[path] == UNBOUND_DIGEST:
                raise AdoptApplyError("member {} carries the unbound marker and its digest is "
                                      "plan-bindable (the planner derives it from the pinned sources); "
                                      "bind it (nothing written)".format(path))
        elif members[path] != digest:
            raise AdoptApplyError("member {} binds digest {} where the substrate's builders fix {}; "
                                  "nothing written".format(path, members[path], digest))
    return members


def _init_store_external_destinations():
    """The default manifest's declared view and deliverable destinations OUTSIDE `.working/` (today
    the CHANGELOG.md deliverable; every declared view is store-scope). Derived from the manifest
    itself so the disposition preflight can never drift from what init publishes around."""
    import _opf_init
    doc = tomllib.loads(_opf_init.build_manifest())
    targets = {d.get("target") for d in doc.get("deliverables", {}).values() if isinstance(d, dict)}
    targets |= {v.get("target") for v in doc.get("views", {}).values() if isinstance(v, dict)}
    return sorted(t for t in targets
                  if isinstance(t, str) and not _within(t, store.WORKING_DIRNAME))


def _init_store_sources_or_refuse(plan):
    """Every consumed sources-row field (path, digest, disposition, occupying, preservation)
    validated fail-closed through the planner's own _validate_plan_sources BEFORE either the
    disposition preflight or the admission consumes a row -- independent of whether `.working`
    exists (codex round 3, finding 3): a malformed row refuses with nothing written, never
    reads as non-occupying, never freezes a source, never authorizes a copy."""
    findings = []
    short = schema._validate_plan_sources(plan["sources"], plan["run_id"], 1, findings)
    if short is not None or findings:
        raise AdoptApplyError("the context plan's sources rows do not validate ({}); every "
                              "consumed sources-row field (path, digest, disposition, occupying, "
                              "preservation) is validated fail-closed before it justifies any "
                              "disposition or admission (nothing written)".format(
                                  "; ".join(findings) if findings else "; ".join(short.findings)))


def _init_store_dispositions_or_refuse(root_fd, plan):
    """Spec 14: every pre-existing file at a declared view or deliverable destination outside
    `.working/` MUST carry a plan disposition, its live bytes digest-bound, before init-store; an
    undispositioned or drifted one refuses with nothing written. (The substrate itself preserves a
    pre-existing CHANGELOG.md byte-exact in its plan's E set and refuses existing store
    pointers.) The rows are validated fail-closed FIRST (_init_store_sources_or_refuse), so a
    malformed row refuses here even when `.working` is absent and the admission never runs."""
    _init_store_sources_or_refuse(plan)
    rows = {row.get("path"): row for row in plan["sources"] if isinstance(row, dict)}
    for target in _init_store_external_destinations():
        live = observe_live(root_fd, target)
        if live["kind"] == "absent":
            continue
        row = rows.get(target)
        if not isinstance(row, dict) or row.get("disposition") not in schema.DISPOSITIONS:
            raise AdoptApplyError("undispositioned declared destination {}: every pre-existing file "
                                  "at a declared deliverable or view destination outside .working "
                                  "carries a plan disposition before init-store (spec 14; nothing "
                                  "written)".format(target))
        if row.get("digest") != "sha256:" + live["sha256"]:
            raise AdoptApplyError("declared destination {} drifted from its plan disposition's "
                                  "digest; nothing written".format(target))


def _init_store_bundle_paths(root_fd, product_root, run_id, model):
    """{path: digest} of this run's evidence-bundle files admissible beneath `.working/`: the
    bundle-root inventories plus EXACTLY the payloads they list, and nothing else — and only after
    verify_bundle has re-proved every listed payload on disk. Empty when nothing lies beneath the
    bundle home; an invalid or unevaluable bundle refuses (never partially admitted)."""
    import _opf_check
    home = evidence_home_rel(run_id)
    if not any(e["kind"] == "file" and _within(e["path"], home) for e in model["entries"]):
        return {}
    checked = verify_bundle(product_root, run_id)
    if checked.status != store.VALID:
        raise AdoptApplyError("the evidence bundle of {} does not verify ({}); fail-closed".format(
            run_id, "; ".join(checked.findings)))
    return _init_store_bundle_listed(root_fd, model, run_id)


def _init_store_bundle_listed(root_fd, model, run_id):
    """{path: digest} of `run_id`'s bundle files among the observed `.working` entries: the
    bundle-root inventories plus the payloads they list. The caller has ALREADY verified the
    bundle (verify_bundle / _verify_bundle_at); an unreadable or malformed inventory still
    refuses here, never reads as an empty listing."""
    import _opf_check
    home = evidence_home_rel(run_id)
    out = {}
    for entry in model["entries"]:
        if entry["kind"] != "file" or not _within(entry["path"], home):
            continue
        name = entry["path"][len(home) + 1:]
        if "/" in name or not store.is_evidence_inventory_name(name):
            continue
        out[entry["path"]] = entry["digest"]
        _fst, data = _read_live(root_fd, entry["path"])
        try:
            rows = _opf_check._evidence_rows(home, KIND, run_id,
                                             tomllib.loads(data.decode("utf-8")))
        except (ValueError, TypeError, KeyError, UnicodeDecodeError,
                tomllib.TOMLDecodeError) as exc:
            raise AdoptApplyError("the evidence inventory {} cannot be read ({}); "
                                  "fail-closed".format(entry["path"], exc))
        for row in rows:
            # Listed paths are product-root-relative (a bundle may list payloads outside its home,
            # e.g. archive copies); only those beneath .working can appear in the inventory at all.
            out[row["path"]] = "sha256:" + row["sha256"]
    return out


def _init_store_prior_committed_ops(jr_fd, journal_root, prior):
    """(creates, archivals) a PRIOR adoption run committed in this journal: creates maps path to
    the bare-hex content sha256 of every `create` op across the prior run's COMPLETE
    transactions (its init-store transaction, which publishes no adoption artefact, excluded);
    archivals lists (source_path, hex_sha256, preservation) for every pinned `remove` whose
    preservation copy the SAME complete transaction created with the same digest -- the
    committed preserve-first archival shape of spec 14.2. Raises on an unreadable journal
    (fail-closed, never an empty answer)."""
    creates, archivals = {}, []
    for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
        name = txn_dir.name
        if name != prior and not name.startswith(prior + "."):
            continue
        if name == _txn_name(prior, INIT_STORE_PHASE):
            continue
        if _journal.classify_state(jr_fd, txn_dir) != "complete":
            continue
        frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
        intent = _journal._first(frames, _journal.F_INTENT)
        ops = intent.get("ops") if isinstance(intent, dict) else None
        if not isinstance(ops, list):
            continue
        txn_creates = {}
        for op in ops:
            if isinstance(op, dict) and op.get("op") == "create" \
                    and isinstance(op.get("path"), str):
                hexd = (op.get("poststate") or {}).get("content-sha256")
                if isinstance(hexd, str):
                    txn_creates[op["path"]] = hexd
        for op in ops:
            if not (isinstance(op, dict) and op.get("op") == "remove"
                    and isinstance(op.get("path"), str)):
                continue
            pin = (op.get("source-poststate") or {}).get("sha256")
            try:
                pres = archive_rel(prior, op["path"])
            except AdoptApplyError:
                continue
            if isinstance(pin, str) and txn_creates.get(pres) == pin:
                archivals.append((op["path"], pin, pres))
        creates.update(txn_creates)
    return creates, archivals


def _init_store_prior_run_paths(root_fd, run_id, model, machine, jr_fd, journal_root):
    """{path: digest} of PRIOR REVERSED runs' committed artefacts admissible beneath the control
    homes, plus whether a proven prior run's COMMITTED archival emptied the machine directory
    (claude round 4, F1). Spec 14.1 still burns a reversed run id -- a reversed run never
    reruns -- but a FRESH run may continue over the prior run's committed work, admitted ONLY
    when each artefact is PROVEN, per prior run id a control-home file names: (1) that run's
    init-store transaction is terminal ROLLED-BACK in this adoption journal (the one sanctioned
    way its id was burnt), (2) its evidence bundle verifies (_verify_bundle_at for that run id,
    re-reading every listed payload), and (3) EVERY listed artefact lies within that run's
    RETAINED-EVIDENCE domains -- the SAME _evidence_eligible predicate the composer derives
    inventory rows from (its own bundle, its own adoption archive, or a default Move
    destination), plus its own bundle-root inventories -- and was created by a transaction
    COMPLETE in this journal, its live
    bytes digest-matched (so a hand-planted or tampered artefact is never admitted). The whole
    prior bundle is admitted or none of it. The machine directory is admitted only on such a
    prior run's COMMITTED archival: a complete transaction that created the preservation copy
    and removed a machine-store source pinned to the same digest, the copy live and admitted.
    Anything not so proven stays foreign and refuses; no journal descriptor proves nothing
    (fail-closed)."""
    out, machine_archived = {}, False
    if jr_fd is None or journal_root is None:
        return out, machine_archived
    priors = set()
    for entry in model["entries"]:
        if entry["kind"] != "file":
            continue
        for home in _CONTROL_HOMES:
            prefix = home + "/" + KIND + "/"
            if entry["path"].startswith(prefix):
                rid = entry["path"][len(prefix):].split("/", 1)[0]
                if rid != run_id and is_run_id(rid):
                    priors.add(rid)
    for prior in sorted(priors):
        txn = _txn_name(prior, INIT_STORE_PHASE)
        try:
            if _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn) is None:
                continue
            if _journal.classify_state(jr_fd, journal_root / txn) != "rolled-back":
                continue
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot classify prior run {}'s init-store transaction ({}); "
                                  "fail-closed".format(prior, exc))
        if _verify_bundle_at(root_fd, prior, evidence_home_rel(prior)).status != store.VALID:
            continue
        try:
            creates, archivals = _init_store_prior_committed_ops(jr_fd, journal_root, prior)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot search the adoption journal for prior run {}'s "
                                  "committed transactions ({}); fail-closed".format(prior, exc))
        listed = _init_store_bundle_listed(root_fd, model, prior)
        # The admissible domains are EXACTLY the retained-evidence domains this module already
        # defines (_evidence_eligible: own bundle, own adoption archive, default Move
        # destinations -- never re-spelled here) plus the prior run's own bundle-root
        # inventories, which _evidence_eligible deliberately excludes from derived rows but
        # which the committed transaction created all the same (codex round 5, finding 1: a
        # committed Move payload is retained evidence and must not strand the bundle).
        admitted = {path: digest for path, digest in listed.items()
                    if (_evidence_eligible(prior, path) or _bundle_root_inventory(prior, path))
                    and creates.get(path) == _plan_hex(digest)}
        if not admitted or set(admitted) != set(listed):
            continue   # an artefact not proven committed keeps the whole bundle foreign
        out.update(admitted)
        for source_path, hexd, pres in archivals:
            if _within(source_path, machine) and admitted.get(pres) == "sha256:" + hexd:
                machine_archived = True
    return out, machine_archived


def _init_store_archival_committed(jr_fd, journal_root, source_path, digest, preservation):
    """Whether a COMMITTED adoption transaction of this journal archived the occupying file at
    `source_path` preserve-first (spec 14.2): its durable intent creates the preservation copy
    at `preservation` with this digest AND removes the source pinned to the same digest. A
    sources row alone proves PLANNED work; only this committed archival evidence (plus the
    live, digest-matched preservation copy the caller requires) can admit the emptied machine
    directory. No journal descriptor reads as no evidence, the fail-closed direction."""
    if jr_fd is None or journal_root is None:
        return False
    hexd = _plan_hex(digest)
    try:
        for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
            if _journal.classify_state(jr_fd, txn_dir) != "complete":
                continue
            frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
            intent = _journal._first(frames, _journal.F_INTENT)
            ops = intent.get("ops") if isinstance(intent, dict) else None
            if not isinstance(ops, list):
                continue
            preserved = any(isinstance(o, dict) and o.get("op") == "create"
                            and o.get("path") == preservation
                            and (o.get("poststate") or {}).get("content-sha256") == hexd
                            for o in ops)
            removed = any(isinstance(o, dict) and o.get("op") == "remove"
                          and o.get("path") == source_path
                          and (o.get("source-poststate") or {}).get("sha256") == hexd
                          for o in ops)
            if preserved and removed:
                return True
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot search the adoption journal for the occupying file's "
                              "archival transaction ({}); fail-closed".format(exc))
    return False


def _init_store_admitted(root_fd, product_root, plan, jr_fd=None, journal_root=None):
    """The blind-init guard and the substrate seam's admitted inventory (spec 14, 14.2): observe the
    live `.working` tree and justify EVERY file in it from the approved plan, else refuse with
    nothing written. Justified, and so admitted — each entry's path, mode, size and digest handed to
    the substrate, which RE-VERIFIES them under its own operation mutex and records them in its
    immutable plan: a non-occupying plan source frozen in place whose live bytes match the row's
    digest; a preservation copy at a row's recorded control-area preservation destination with that
    row's digest; and this run's evidence bundle (its inventories and exactly the payloads they
    list), only after verify_bundle re-proves every listed payload. An undispositioned foreign file,
    a drifted one, an unlisted control-area file, or a directory no admitted file lies beneath
    refuses -- EXCEPT the machine home itself when COMPLETED ARCHIVAL EVIDENCE proves the shell
    archived an occupying machine-store file out of it (spec 14.2: "After the archival the
    destination is an ordinary managed path"): the occupying row's preservation copy must lie
    LIVE at its recorded destination, digest-matched, and the archival transaction must be
    COMMITTED in this adoption journal (_init_store_archival_committed) -- a validated row
    alone proves planned work, not completed archival. A PRIOR run's committed artefacts (its
    verified evidence bundle and archive copies) are admitted, and the machine home with them,
    only through _init_store_prior_run_paths: that run's init-store transaction terminal
    ROLLED-BACK, its bundle verified, every artefact created by a COMPLETE transaction of this
    journal, and the machine home only on its committed archival -- so a fresh run continues a
    reversed run's committed work without re-running its burnt id (claude round 4, F1;
    spec 14.1). Only then is the emptied, pre-existing
    directory admitted as a pre-existing planned directory, its mode pre-checked HERE (before
    the run's intent opens, so a wrong-mode tree never burns the run id) at the FULL
    stat.S_IMODE the substrate compares: the machine directory against the planned mode
    (codex round 4), and `.working` with admitted files against the substrate's own two
    facts -- it records `.working`'s observed mode MASKED to 0o777 and re-compares the full
    S_IMODE against that record, so a special bit (a setgid 02755) can never pass, and it
    refuses a group- or world-writable mode outright (claude round 5, N2). The substrate
    re-verifies and preserves what it admits.
    Every consumed
    sources-row field (path, digest, disposition, occupying, preservation) is validated
    fail-closed through the planner's own _validate_plan_sources BEFORE it justifies anything: a
    malformed row refuses, never reads as non-occupying or authorizes a copy. Returns
    (admitted file entries, admitted pre-existing planned directories); an absent `.working`
    returns ((), ())."""
    try:
        model, present = init_op.observe_inventory(root_fd)
    except init_op.InitOperationError as exc:
        raise AdoptApplyError("the .working tree cannot be observed ({}); fail-closed".format(exc))
    if not present:
        return (), ()
    _init_store_sources_or_refuse(plan)
    machine = "{}/{}".format(store.WORKING_DIRNAME, store.DEFAULT_MACHINE_SUBDIR)
    frozen, copies, occ_rows = {}, {}, []
    for row in plan["sources"]:
        path, digest = row["path"], row["digest"]
        if row["occupying"] is True and _within(path, machine):
            occ_rows.append(row)
        if row["occupying"] is False and row["disposition"] in schema.DISPOSITIONS \
                and _within(path, store.WORKING_DIRNAME) and not _within(path, machine):
            frozen[path] = digest
        pres = row.get("preservation")
        if row["disposition"] != "keep" and isinstance(pres, str) \
                and any(_within(pres, home) for home in _CONTROL_HOMES):
            copies[pres] = digest
    bundle = _init_store_bundle_paths(root_fd, product_root, plan["run_id"], model)
    prior, prior_machine = _init_store_prior_run_paths(root_fd, plan["run_id"], model, machine,
                                                       jr_fd, journal_root)
    admitted, foreign = [], []
    for entry in model["entries"]:
        if entry["kind"] != "file":
            continue
        path = entry["path"]
        want = frozen.get(path) or copies.get(path) or bundle.get(path) or prior.get(path)
        if want is None or want != entry["digest"]:
            foreign.append(path)
            continue
        admitted.append({"path": path, "mode": entry["mode"], "size": entry["size"],
                         "digest": entry["digest"]})
    if foreign:
        raise AdoptApplyError("undispositioned foreign .working content {}: every foreign .working "
                              "file carries a plan disposition first (a source frozen in place, a "
                              "recorded preservation copy, or this run's verified evidence bundle, "
                              "live bytes digest-matched), never a blind init over populated content "
                              "(spec 14; nothing written)".format(", ".join(sorted(foreign))))
    dirs = {e["path"] for e in model["entries"] if e["kind"] == "directory"}
    stray = sorted(dirs - init_op._admitted_dirs([e["path"] for e in admitted]))
    admitted_dirs = ()
    if machine in stray and (occ_rows or prior_machine):
        # Spec 14.2: "After the archival the destination is an ordinary managed path: apply
        # initializes the machine file or renders the view immediately." The archival removed the
        # occupying FILE; its pre-existing parent directory survives (the shell never removes a
        # pre-existing directory) and holds no file (one would have refused as foreign above).
        # The admission is derived from VERIFIED ARCHIVAL EVIDENCE, never the planned row alone:
        # every occupying machine row's preservation copy must lie LIVE at its recorded
        # destination, digest-matched (it was admitted above), and a COMMITTED adoption
        # transaction of this journal must record that archival preserve-first -- or, with no
        # occupying row, a PRIOR reversed run's PROVEN committed archival emptied the directory
        # (_init_store_prior_run_paths, claude round 4 F1). Only then is the
        # directory admitted as a pre-existing planned directory: the substrate re-verifies it
        # under its mutex (a plain directory, owned, at exactly its planned mode) and PRESERVES
        # it through the run and its reversal.
        admitted_paths = dict((e["path"], e["digest"]) for e in admitted)
        for orow in occ_rows:
            pres = orow.get("preservation")
            if not (isinstance(pres, str) and admitted_paths.get(pres) == orow["digest"]
                    and _init_store_archival_committed(jr_fd, journal_root, orow["path"],
                                                       orow["digest"], pres)):
                raise AdoptApplyError("the pre-existing machine directory {} is admitted only "
                                      "on completed archival evidence: the occupying row's "
                                      "preservation copy live and digest-matched at its "
                                      "recorded destination AND the archival transaction "
                                      "COMMITTED in this adoption journal; a sources row alone "
                                      "proves planned work, not completed archival (spec 14.2; "
                                      "nothing written)".format(machine))
        stray.remove(machine)
        admitted_dirs = (store.WORKING_DIRNAME, machine)
        # The admitted-directory MODE is pre-checked HERE, before the run's intent opens, so a
        # wrong-mode pre-existing tree (a umask-002 adopter) refuses with nothing written and
        # the run id never burnt; the substrate re-proves the same fact under its mutex. The
        # comparison reads the SAME full stat.S_IMODE value the substrate compares (codex
        # round 4, finding 2): observe_inventory's entries mask to 0o777, so a special bit
        # (a setgid 02755 directory) would otherwise pass here and burn the run id on the
        # substrate's own post-intent refusal.
        for dpath in admitted_dirs:
            try:
                dst = os.stat(dpath, dir_fd=root_fd, follow_symlinks=False)
            except OSError as exc:
                raise AdoptApplyError("cannot stat {} ({}); fail-closed".format(dpath, exc))
            if not stat.S_ISDIR(dst.st_mode):
                raise AdoptApplyError("admitted pre-existing path {} is no longer a directory; "
                                      "fail-closed (nothing written)".format(dpath))
            if dpath == store.WORKING_DIRNAME and admitted:
                # With admitted files the substrate records `.working`'s observed mode MASKED
                # to 0o777 and re-compares the FULL stat.S_IMODE against that record under its
                # mutex, and refuses a group- or world-writable mode; the SAME two facts are
                # pre-checked HERE so a special-bit or writable `.working` refuses BEFORE the
                # run's intent opens and never burns the run id (claude round 5, N2).
                wmode = stat.S_IMODE(dst.st_mode)
                if wmode != (wmode & 0o777):
                    raise AdoptApplyError("admitted pre-existing directory {} holds mode {}, "
                                          "whose special bit the substrate's 0o777-masked "
                                          "recorded mode can never match; it is preserved as "
                                          "found, never rewritten, and refused BEFORE the "
                                          "run's intent opens (nothing written)".format(
                                              dpath, "%o" % wmode))
                if wmode & 0o022:
                    raise AdoptApplyError("the admitted {} directory mode {} is group- or "
                                          "world-writable; a store is never published beneath "
                                          "a home writable by others, and it is refused "
                                          "BEFORE the run's intent opens (nothing "
                                          "written)".format(dpath, "%o" % wmode))
                continue
            if stat.S_IMODE(dst.st_mode) != init_op.DIR_MODE:
                raise AdoptApplyError("admitted pre-existing directory {} holds mode {}, not "
                                      "the planned {}; it is preserved as found, never "
                                      "rewritten, and refused BEFORE the run's intent opens "
                                      "(nothing written)".format(
                                          dpath, "%o" % stat.S_IMODE(dst.st_mode),
                                          "%o" % init_op.DIR_MODE))
    if stray:
        raise AdoptApplyError("foreign .working directory(ies) {} hold no admitted file; a blind "
                              "init over them is refused (spec 14; nothing written)".format(stray))
    if not admitted and not admitted_dirs:
        raise AdoptApplyError("a .working directory exists holding nothing the plan admits; a blind "
                              "init over it is refused (spec 14; nothing written)")
    return tuple(admitted), admitted_dirs


def _init_store_record(product_root):
    """The substrate's recorded history: None when it records nothing, the ONE recorded
    operation's report otherwise, WHATEVER its status -- intact (partial or completed), or the
    unevaluable debris an interrupted record discard left (its directory still names the
    operation id, the binding the caller uses: _init_store_orphan_or_refuse either binds it to
    this journal's own rolled-back init-store intent and finishes the discard, or refuses).
    More than one operation refuses: init-store never picks among histories."""
    import _opf_init_substrate
    try:
        survey = _opf_init_substrate.classify_init_operations(str(product_root))
    except (_opf_init_substrate.InitSubstrateError, OSError) as exc:
        raise AdoptApplyError("the coupled-init substrate cannot be classified ({}); "
                              "fail-closed".format(exc))
    ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
    if not ops:
        return None
    if len(ops) != 1:
        raise AdoptApplyError("the coupled-init substrate already records init history ({} "
                              "operation(s), first {}); init-store never adopts over recorded "
                              "history or picks among operations (fail-closed)".format(
                                  len(ops), ops[0].status))
    return ops[0]


def _init_store_orphan_or_refuse(product_root, root_fd, jr_fd, journal_root, record, recover):
    """ONE recorded substrate operation found before a fresh init-store: admissible ONLY as the
    reversed remnant of THIS journal's own init-store work — a ROLLED-BACK init-store transaction
    must name the operation id in its durable intent (the binding the intent exists for; the
    record's DIRECTORY name carries the id even when an interrupted record discard left it
    unevaluable) and every creation that intent recorded must be absent live (its rollback
    restored them from the journal's preimages). Then, under the EXPLICIT context recover flag,
    its leftovers are scrubbed and the record discarded under the substrate's own mutex (a record
    whose effects were reversed authorizes nothing), so a fresh plan can run; the scrub is
    restartable at every point (_init_store_scrub discards the record LAST, the recovery
    discriminator preserved until the filesystem cleanup is durably complete). Anything else — an
    initialized store's completed operation, a partial or unevaluable record no intent of this
    journal names — refuses fail-closed: recorded init history is never adopted, resumed across
    runs, or silently chosen."""
    intent = None
    try:
        for txn_dir in _journal._journal_txn_dirs(jr_fd, journal_root):
            if not txn_dir.name.endswith("." + INIT_STORE_PHASE):
                continue
            if _journal.classify_state(jr_fd, txn_dir) != "rolled-back":
                continue
            frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
            doc = _journal._first(frames, _journal.F_INTENT)
            header = doc.get("header") if isinstance(doc, dict) else None
            if isinstance(header, dict) and header.get("init_operation_id") == record.op_id:
                intent = doc
                break
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot search the adoption journal for the recorded operation's "
                              "init-store intent ({}); fail-closed".format(exc))
    if intent is None:
        raise AdoptApplyError("the coupled-init substrate already records init history (operation "
                              "{}) that no rolled-back init-store transaction of this adoption "
                              "journal names; it is never adopted, resumed across runs, or silently "
                              "chosen (fail-closed)".format(record.op_id))
    for op in intent.get("ops", []):
        try:
            present = _journal._lstat_contained(root_fd, op.get("path")) is not None
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot observe {} ({}); fail-closed".format(op.get("path"), exc))
        if present:
            raise AdoptApplyError("the rolled-back init-store transaction's effect {} is still "
                                  "present; its reversal did not restore the prestate "
                                  "(fail-closed)".format(op.get("path")))
    if not recover:
        raise AdoptApplyError("discarding the reversed operation {} record needs the EXPLICIT "
                              "context recover flag, never an implicit side effect".format(
                                  record.op_id))
    header = intent["header"]
    _init_store_scrub(product_root, root_fd, record.op_id,
                      members=[op.get("path") for op in intent.get("ops", [])
                               if isinstance(op, dict) and isinstance(op.get("path"), str)],
                      working_created=header.get("working_created") is True,
                      machine_created=header.get("machine_created") is not False,
                      recover=recover)


def _fsync_surviving_parent(root_fd, rel):
    """Make the ABSENCE of the cleanup target `rel` durable: fsync its nearest SURVIVING
    ancestor directory -- the directory whose entry set records the removal -- walking up past
    ancestors that were themselves removed, to the product root if need be. A restarted scrub
    cannot tell a target that never existed from one whose removal a crash separated from its
    parent fsync, so the barrier is (re-)established for EVERY absent cleanup target before the
    operation record may be discarded (codex round 4, finding 1). Fail-closed: an ancestor that
    cannot be classified or fsynced refuses, never reads as durable."""
    parts = rel.split("/")
    keep = 0
    for i in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:i])
        try:
            st = _journal._lstat_contained(root_fd, prefix)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot observe {} to make {}'s absence durable ({}); "
                                  "fail-closed".format(prefix, rel, exc))
        if st is None:
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise AdoptApplyError("the surviving ancestor {} of the absent cleanup target {} is "
                                  "not a directory; fail-closed".format(prefix, rel))
        keep = i
        break
    try:
        # _open_parent opens the components BEFORE the last, so handing it the surviving
        # ancestor plus its (removed) child component opens exactly that ancestor, contained.
        pfd, _name = _journal._open_parent(root_fd, "/".join(parts[:keep + 1]))
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot open the surviving parent of the absent cleanup target {} "
                              "({}); fail-closed".format(rel, exc))
    try:
        os.fsync(pfd)
    finally:
        _journal._close_fd_quietly(pfd)


def _init_store_scrub(product_root, root_fd, operation_id, members, working_created,
                      machine_created, recover):
    """Clear a REVERSED init-store operation's remnants under the substrate's own mutex, so the
    tree returns to its prestate (NOT-ADOPTED, or the admitted content alone), in an order
    RESTARTABLE AT EVERY POINT from the durable adoption journal plus the still-recorded
    operation: FIRST the operation's payload staging leftovers beside each member destination (a
    kill between a member's staging write and its link, or between the link and the staging
    unlink, leaves one, its name embedding the operation id -- root-level destinations such as
    CHANGELOG.md and the store pointer included, so no leftover publication byte outlives the
    reversal), then the lease control file when the interruption left one, then the then-empty
    CREATED planned directories (`.working` and the machine home only when this journal's intent
    created them: an admitted pre-existing directory is preserved as found), and ONLY THEN the
    operation record (its journals and outcomes stay, preserved attempt evidence). Each removal's
    parent directory is fsynced right after its unlink or rmdir, and a cleanup target already
    ABSENT on entry -- a restarted scrub cannot tell one that never existed from one whose
    removal a crash separated from its parent fsync -- has its nearest surviving ancestor
    fsynced (_fsync_surviving_parent), so EVERY target's absence is durable BEFORE the record
    discard is issued: the record deletion cannot become durable while a cleanup removal is
    not, because each removal's durability barrier has already completed when the discard
    begins (the self-test pins this syscall order). The record is the recovery
    discriminator that lets the next dispatch find the reversed operation again, so it is
    discarded LAST, once every filesystem effect is durably gone; a kill inside the discard itself
    leaves a partial record whose directory still names the operation id, which the next dispatch
    re-binds to the rolled-back intent and finishes discarding. The journal's preimage rollback
    has already removed the member files; this clears only what that rollback cannot name."""
    import _opf_init_substrate
    import _opf_oplock
    machine = "{}/{}".format(store.WORKING_DIRNAME, store.DEFAULT_MACHINE_SUBDIR)
    try:
        holder = _opf_oplock.acquire_init_operation(str(product_root), init_op.OPERATION,
                                                    recover=recover)
    except _opf_oplock.OpLockError as exc:
        raise AdoptApplyError("cannot take the substrate operation mutex to scrub the reversed "
                              "operation ({}); fail-closed".format(exc))
    try:
        try:
            for path in sorted(members):
                parent, _sep, _name = path.rpartition("/")
                stage_rel = (parent + "/" if parent else "") + init_op.staging_name(
                    path, operation_id)
                st = _journal._lstat_contained(root_fd, stage_rel)
                if st is None:
                    _fsync_surviving_parent(root_fd, stage_rel)
                    continue
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise AdoptApplyError("the reversed operation's staging name {} is not a "
                                          "plain singly-linked regular file; it is preserved for "
                                          "inspection (fail-closed)".format(stage_rel))
                sfd, sname = _journal._open_parent(root_fd, stage_rel)
                try:
                    os.unlink(sname, dir_fd=sfd)
                    os.fsync(sfd)
                finally:
                    _journal._close_fd_quietly(sfd)
            st = _journal._lstat_contained(root_fd, init_op.LEASE_RELPATH)
            if st is not None and stat.S_ISREG(st.st_mode):
                lfd, lname = _journal._open_parent(root_fd, init_op.LEASE_RELPATH)
                try:
                    os.unlink(lname, dir_fd=lfd)
                    os.fsync(lfd)
                finally:
                    _journal._close_fd_quietly(lfd)
            elif st is None:
                _fsync_surviving_parent(root_fd, init_op.LEASE_RELPATH)
            created = ([machine] if machine_created else []) \
                + ([store.WORKING_DIRNAME] if working_created else [])
            for rel in created:
                st = _journal._lstat_contained(root_fd, rel)
                if st is None:
                    _fsync_surviving_parent(root_fd, rel)
                    continue
                if not stat.S_ISDIR(st.st_mode):
                    raise AdoptApplyError("{} is not a directory at reversal; "
                                          "fail-closed".format(rel))
                dfd, dname = _journal._open_parent(root_fd, rel)
                try:
                    try:
                        os.rmdir(dname, dir_fd=dfd)
                    except OSError as exc:
                        raise AdoptApplyError("cannot remove the reversed directory {} ({}); it "
                                              "is preserved for inspection "
                                              "(fail-closed)".format(rel, exc))
                    os.fsync(dfd)
                finally:
                    _journal._close_fd_quietly(dfd)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot scrub the reversed operation's control files ({}); "
                                  "fail-closed".format(exc))
        try:
            survey = _opf_init_substrate.classify_init_operations(str(product_root))
            ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
            if any(r.op_id == operation_id for r in ops):
                _swept, discarded = _opf_init_substrate.settle_operation(holder, operation_id)
                if not discarded:
                    _opf_init_substrate.discard_reversed_operation(holder, operation_id)
        except (_opf_init_substrate.InitSubstrateError, OSError) as exc:
            raise AdoptApplyError("cannot discard the reversed operation record {} ({}); "
                                  "fail-closed".format(operation_id, exc))
    finally:
        if not holder._released and not holder._spent:
            try:
                _opf_oplock.release_init_holder(holder)
            except _opf_oplock.OpLockError:
                pass   # a leftover mutex refuses the next run into explicit recovery, never seized


def _init_store_intent_ops(members):
    """The durable-intent ops of the one init-store transaction: one `create` per member, each
    carrying its poststate digest where the plan binds one. init.toml is never plan-bindable, so the
    journal's forward election can NEVER fire on an open init-store transaction: recovery from the
    journal alone always rolls BACK to the preserved prestate (every created member removed, after
    _init_store_reversal_preverify proves each live member is the operation's own publication),
    which is the op's declared reversal — "remove the scaffolded store, restoring NOT-ADOPTED"
    (spec 14.2). The planned directories, the payload staging names and the transient lease are
    not ops (a preserving rollback cannot name them); _init_store_scrub clears them under the
    substrate mutex, restartably, and only then discards the operation record."""
    ops = []
    for path in sorted(members):
        op = {"op": "create", "path": path, "mode": FILE_MODE}
        if members[path] != UNBOUND_DIGEST:
            op["poststate"] = {"kind": "file", "mode": FILE_MODE,
                               "content-sha256": members[path][len("sha256:"):]}
        ops.append(op)
    return ops


def _init_store_txn_clear(jr_fd, txn):
    """Clear the frame-less debris a crash left in this run's OWN init-store transaction
    directory between its mkdir and its INTENT (the caller classified it nothing-opened, so no
    INTENT was ever durable and nothing beyond the journal directories was written): remove
    frames.log (empty or torn) and any captured preimage payloads, so the run's retry opens the
    transaction as if fresh. Anything else in the directory refuses fail-closed (preserved for
    inspection), and a directory holding an INTENT never reaches here (the caller refuses it
    first)."""
    try:
        tfd = os.open(txn, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=jr_fd)
    except OSError as exc:
        raise AdoptApplyError("cannot reopen this run's frame-less init-store transaction "
                              "directory ({}); fail-closed".format(exc))
    try:
        for entry in sorted(os.listdir(tfd)):
            st = os.stat(entry, dir_fd=tfd, follow_symlinks=False)
            if entry == "frames.log" and stat.S_ISREG(st.st_mode):
                os.unlink(entry, dir_fd=tfd)
            elif entry == "preimages" and not stat.S_ISLNK(st.st_mode) \
                    and stat.S_ISDIR(st.st_mode):
                pfd = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=tfd)
                try:
                    for name in sorted(os.listdir(pfd)):
                        pst = os.stat(name, dir_fd=pfd, follow_symlinks=False)
                        if not stat.S_ISREG(pst.st_mode):
                            raise AdoptApplyError("this run's frame-less init-store transaction "
                                                  "holds a non-regular preimage entry {!r}; it "
                                                  "is preserved for inspection "
                                                  "(fail-closed)".format(name))
                        os.unlink(name, dir_fd=pfd)
                    os.fsync(pfd)
                finally:
                    _journal._close_fd_quietly(pfd)
            else:
                raise AdoptApplyError("this run's frame-less init-store transaction directory "
                                      "holds {!r}; it is preserved for inspection "
                                      "(fail-closed)".format(entry))
        os.fsync(tfd)
    except OSError as exc:
        raise AdoptApplyError("cannot clear this run's frame-less init-store transaction "
                              "directory ({}); fail-closed".format(exc))
    finally:
        _journal._close_fd_quietly(tfd)


def _init_store_txn_begin(root_fd, jr_fd, journal_root, txn, header, ops):
    """Open the init-store transaction and make its reversal durable BEFORE the substrate writes
    (the 9.3 framing run_transaction uses): the transaction directory and its frames.log, the
    captured preimages — every planned creation proved ABSENT here, before anything is written —
    then the INTENT frame binding the run, the adoption kind, the ancestral seed, the minted
    operation id, the admitted inventory and every member digest. The APPLY is the coupled-init
    substrate's own journaled publication, never apply_ops. The frame budget is bounded by
    construction: the member roster and the admitted inventory are both capped far below the
    journal's read cap."""
    txn_dir = journal_root / txn
    try:
        try:
            os.mkdir(txn, 0o777, dir_fd=jr_fd)
        except FileExistsError:
            # A crash between a prior attempt's mkdir HERE and its INTENT left a frame-less
            # transaction directory (the caller classified it nothing-opened: no INTENT was ever
            # durable, so nothing beyond the journal directories was written). The run id is not
            # burnt by its own pre-INTENT crash: clear the crash's partial frames.log and captured
            # preimage payloads so the exclusive creates below cannot trip on debris, then open
            # the transaction as if fresh.
            _init_store_txn_clear(jr_fd, txn)
        os.fsync(jr_fd)
        _journal._create_frames_excl(jr_fd, txn_dir)
        _journal.capture_preimages(jr_fd, txn_dir, root_fd, ops)
        _journal.publish(jr_fd, txn_dir, _journal.F_INTENT,
                         {"txn": txn, "header": header, "ops": ops})
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("the init-store transaction was refused before it opened ({}); "
                              "nothing written beyond the journal directories "
                              "(fail-closed)".format(exc))


def _init_store_view_digests(product_root, operation_id):
    """Best-effort READ-ONLY load of the operation's own views-group journal INTENT: {path:
    bare-hex sha256} of the views the operation itself planned to publish, durable BEFORE the
    first view write, so the reversal can recognize the operation's own view bytes (a view's
    plan-time digest lives in the row, and the row's binding may be exactly what the
    postcondition refuted). Returns {} when the substrate, journal or intent is absent or
    unreadable: then only the operation's own plan-bound bytes are recognized, the fail-closed
    direction (a live view the reversal cannot attribute refuses, preserved)."""
    import _opf_init_substrate
    out = {}
    control_fd = home_fd = jr_fd = None
    try:
        try:
            control_fd, _desc = _opf_init_substrate._open_init_control_root(str(product_root))
        except _opf_init_substrate.InitSubstrateError:
            return out
        if control_fd is None:
            return out
        home_fd = os.open(_opf_init_substrate.SUBSTRATE_DIRNAME,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=control_fd)
        jr_fd = os.open(_opf_init_substrate.JOURNALS_DIRNAME,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
        frames, _torn, _good = _journal.read_frames(
            jr_fd, Path(init_op._txn_name(operation_id, "views")))
        intent = _journal._first(frames, _journal.F_INTENT)
        if isinstance(intent, dict) and intent.get("operation_id") == operation_id:
            for eff in intent.get("effects", []):
                if isinstance(eff, dict) and isinstance(eff.get("path"), str) \
                        and isinstance(eff.get("digest"), str) \
                        and eff["digest"].startswith("sha256:"):
                    out[eff["path"]] = eff["digest"][len("sha256:"):]
    except (_journal.JournalError, OSError):
        return {}
    finally:
        for fd in (jr_fd, home_fd, control_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)
    return out


def _init_store_recorded_plan(product_root, op_id):
    """The operation's OWN recorded plan: ops/<op_id>/plan.json read no-follow beneath the
    substrate's control root and validated through the substrate's own plan validator for that
    operation id, REGARDLESS of the record's phase-record status (a kill inside a phase-record
    publication leaves an exact staging-name leftover that classifies the whole record
    CANNOT-EVALUATE without touching the plan record's integrity). None when the substrate, the
    record or the plan is absent or invalid: the fail-closed direction (a live member with no
    attributable plan evidence is preserved, never removed)."""
    import _opf_init_substrate
    if not isinstance(op_id, str) or not _opf_init_substrate._OP_ID_RE.match(op_id):
        return None
    control_fd = home_fd = ops_fd = op_fd = None
    try:
        try:
            control_fd, _desc = _opf_init_substrate._open_init_control_root(str(product_root))
        except _opf_init_substrate.InitSubstrateError:
            return None
        if control_fd is None:
            return None
        home_fd = os.open(_opf_init_substrate.SUBSTRATE_DIRNAME,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=control_fd)
        ops_fd = os.open(_opf_init_substrate.OPS_DIRNAME,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
        op_fd, _ident = _opf_init_substrate._open_existing_op_dir(ops_fd, op_id)
        _doc, raw = _opf_init_substrate._read_json_record(
            op_fd, _opf_init_substrate.PLAN_NAME, "plan record",
            _opf_init_substrate.MAX_PLAN_BYTES)
        return _opf_init_substrate._validate_plan(raw, op_id)
    except (_opf_init_substrate.InitSubstrateError, OSError):
        return None
    finally:
        for fd in (op_fd, ops_fd, home_fd, control_fd):
            if fd is not None:
                _journal._close_fd_quietly(fd)


def _init_store_reversal_preverify(root_fd, product_root, jr_fd, txn_dir):
    """The reversal removes EXACTLY what the operation created and nothing else (spec 14.2):
    every recorded creation still PRESENT live must be proven the operation's own publication by
    ATTRIBUTABLE SUBSTRATE EVIDENCE -- the digest the operation's own recorded plan binds to the
    path (read through the substrate's own plan validator, WHATEVER the record's phase-record
    status: _init_store_recorded_plan, so a kill inside a phase-record publication never strands
    the reversal), or the digest the operation's own views-group journal INTENT bound before the
    first view write -- BEFORE the journal rollback unlinks it. The adoption row's digest NEVER
    authorizes a removal by itself: a row bound to foreign bytes would otherwise delete the very
    foreign file it happens to match. Conflicting or absent evidence PRESERVES the file: live
    content that matches neither evidence source, a non-regular entry, or a member with no
    readable recorded plan is a FOREIGN write that landed after the preimages proved absence --
    the reversal refuses fail-closed, the transaction left open for inspection (remove or move
    the foreign content aside, then reconcile()). RESIDUAL (disclosed): the verification and
    the rollback's unlink are two observations, so a same-user adversarial swap between them
    stays outside the journal's quiescence guarantee, and a foreign file whose bytes EQUAL the
    substrate's own published payload is indistinguishable from it and is removed."""
    try:
        frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
        intent = _journal._first(frames, _journal.F_INTENT)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot read the init-store intent before its reversal ({}); "
                              "fail-closed".format(exc))
    if not isinstance(intent, dict):
        return
    header = intent.get("header") if isinstance(intent.get("header"), dict) else {}
    op_id = header.get("init_operation_id")

    def live_member_sha(path):
        try:
            st = _journal._lstat_contained(root_fd, path)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot observe {} before its reversal ({}); "
                                  "fail-closed".format(path, exc))
        if st is None:
            return None
        foreign = AdoptApplyError("the reversal of {} found a non-regular, multiply-linked or "
                                  "unreadable entry at {}; foreign content is preserved, never "
                                  "removed by the reversal (fail-closed)".format(
                                      Path(txn_dir).name, path))
        if not stat.S_ISREG(st.st_mode):
            raise foreign
        if st.st_nlink != 1:
            # The ONE legitimate two-link state: a crash between the member's link(2) and its
            # staging unlink leaves the destination sharing its inode with the operation's OWN
            # plan-recorded staging name (the substrate's completed-publication classification);
            # any other extra link is foreign.
            parent, _sep, _name = path.rpartition("/")
            stage_rel = (parent + "/" if parent else "") \
                + init_op.staging_name(path, op_id if isinstance(op_id, str) else "")
            try:
                sst = _journal._lstat_contained(root_fd, stage_rel)
            except (_journal.JournalError, OSError):
                sst = None
            if st.st_nlink != 2 or sst is None \
                    or (sst.st_dev, sst.st_ino) != (st.st_dev, st.st_ino):
                raise foreign
        try:
            data, _fst = _journal._read_contained(root_fd, path)
        except (_journal.JournalError, OSError):
            raise foreign
        return _sha256(data)

    live_sha = {}
    for op in intent.get("ops", []):
        if not isinstance(op, dict) or op.get("op") != "create":
            continue
        path = op.get("path")
        got = live_member_sha(path)
        if got is None:
            continue
        live_sha[path] = got
    if not live_sha:
        return
    plan_sha = {}
    recorded = _init_store_recorded_plan(product_root, op_id)
    if isinstance(recorded, dict):
        try:
            plan_sha = {e["path"]: e["digest"][len("sha256:"):]
                        for e in recorded["sets"]["S"]
                        if isinstance(e, dict) and isinstance(e.get("path"), str)
                        and isinstance(e.get("digest"), str)
                        and e["digest"].startswith("sha256:")}
        except (KeyError, TypeError):
            plan_sha = {}
    view_sha = _init_store_view_digests(product_root, op_id)
    for path in sorted(live_sha):
        got = live_sha[path]
        accept = {d for d in (plan_sha.get(path), view_sha.get(path)) if d is not None}
        if got not in accept:
            raise AdoptApplyError("the reversal of {} found live content at {} that is not the "
                                  "operation's own publication; a foreign write is preserved, "
                                  "never removed by the reversal, which removes exactly what the "
                                  "operation created (spec 14.2); the transaction stays open for "
                                  "inspection (fail-closed)".format(Path(txn_dir).name, path))


def _init_store_reverse(product_root, root_fd, jr_fd, journal_root, txn, operation_id,
                        working_created, machine_created, members):
    """Execute the op's declared reversal NOW, from the transaction's own durable intent: prove
    every live member is the operation's own publication (_init_store_reversal_preverify: the
    reversal removes exactly what the operation created and nothing else), roll the open
    transaction back from its preimages (the forward election cannot fire, see
    _init_store_intent_ops), then scrub the substrate operation's remnants -- payload staging
    leftovers, lease, created directories -- and discard its record under the substrate mutex
    (_init_store_scrub, record LAST). A failure BEFORE the rollback completes leaves the
    transaction OPEN or ROLLBACK-IN-PROGRESS, so every later run refuses into reconcile(); a
    failure DURING the scrub leaves it terminal ROLLED-BACK beside the still-recorded operation,
    which the next init-store dispatch re-binds to this intent and re-scrubs under the EXPLICIT
    recover flag (fail-closed either way, never a silent half-reversal). Admitted .working
    content, a preserved CHANGELOG.md and admitted pre-existing directories were never ops, so
    the rollback cannot touch them."""
    import _opf_init_substrate
    _init_store_reversal_preverify(root_fd, product_root, jr_fd, journal_root / txn)
    try:
        outcome = _journal.recover(jr_fd, journal_root / txn, root_fd)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("the init-store reversal could not roll back ({}); the transaction "
                              "stays open for reconcile() (fail-closed)".format(exc))
    if outcome != "rolled-back":
        raise AdoptApplyError("the init-store transaction {} reconciled {} rather than rolling "
                              "back; fail-closed".format(txn, outcome))
    try:
        survey = _opf_init_substrate.classify_init_operations(str(product_root))
        ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
        recorded = any(r.op_id == operation_id for r in ops)
    except (_opf_init_substrate.InitSubstrateError, OSError) as exc:
        raise AdoptApplyError("the reversed substrate record cannot be classified ({}); "
                              "fail-closed".format(exc))
    if recorded:
        _init_store_scrub(product_root, root_fd, operation_id, members, working_created,
                          machine_created, recover=False)


def _init_store_postcondition(root_fd, product_root, members, operation_id):
    """Why the published scaffold does not verify, or None: the operation's validated recorded plan
    must create EXACTLY the member paths; every member's LIVE bytes, re-read contained, must match
    the row's digest; and init.toml, the one plan-unbindable member, must equal that recorded plan's
    own payload byte-exact (the residual the row cannot carry; see the module residuals)."""
    import _opf_init_substrate
    recorded = None
    try:
        survey = _opf_init_substrate.classify_init_operations(str(product_root))
        ops = survey.operations if survey.status == _opf_init_substrate.OPERATIONS else ()
        rep = next((r for r in ops if r.op_id == operation_id), None)
        if rep is not None and rep.status == _opf_init_substrate.INTACT:
            recorded = rep.plan
    except (_opf_init_substrate.InitSubstrateError, OSError):
        recorded = None
    if not isinstance(recorded, dict):
        return "operation {} left no intact recorded plan".format(operation_id)
    created = {e["path"] for key in ("S", "V") for e in recorded["sets"][key]}
    if created != set(members):
        return "operation {} planned creations {} where the row binds {}".format(
            operation_id, sorted(created), sorted(members))
    plan_payload = next((e["payload"] for e in recorded["sets"]["S"]
                         if e["path"] == init_op.PROVENANCE_RELPATH), None)
    for path in sorted(members):
        live = observe_live(root_fd, path)
        if live["kind"] != "file":
            return "operation {} did not publish {}".format(operation_id, path)
        if path == init_op.PROVENANCE_RELPATH:
            import base64
            try:
                want = _sha256(base64.b64decode(plan_payload, validate=True))
            except (TypeError, ValueError):
                want = None
            if want is None or want != live["sha256"]:
                return "operation {} published {} whose bytes do not equal its validated recorded " \
                       "plan's payload".format(operation_id, path)
        elif "sha256:" + live["sha256"] != members[path]:
            return "operation {} published {} whose live bytes do not match the row's member " \
                   "digest".format(operation_id, path)
    return None


def _init_store(op_row, context=None):
    """init-store (spec 14, 14.2): scaffold the default machine store by COMPOSING the coupled-init
    substrate's journaled, create-only operation (`_opf_init_operation.run_init_operation`) INSIDE
    this engine's own adoption transaction — never the thin `opf init` CLI path and never
    re-implemented here. `context` carries `product_root` (absolute), `plan` (its `run_id`, `store`
    identity and `sources` dispositions), `ancestral` (the pinned evidence commit of a re-adoption's
    counters seed, else None) and `recover` (default False: EXPLICIT recovery, never implicit).

    One lock shared by both writers: the handler takes THIS journal's lock (the same one
    run_adopt_transaction takes) before observing anything it acts on and holds it across the
    substrate's whole run, so no adoption transaction can interleave between the reconcile-first
    check, the preflights and the publication; the substrate's own operation mutex is taken inside
    it (lock order: adoption journal, then substrate mutex).

    Durable intent, recovery and reversal: before the substrate runs, the run's init-store
    transaction captures preimages (every creation proved absent) and publishes an INTENT binding
    the run id, adoption kind, ancestral seed, minted substrate operation id, admitted .working
    inventory and every member digest. A crash leaves that transaction open, so every later run
    refuses into reconcile(), whose journal-only recovery always rolls init-store BACK (init.toml is
    never plan-bindable, so the forward election cannot fire): the declared reversal, "remove the
    scaffolded store, restoring NOT-ADOPTED". The reversal removes EXACTLY what the operation
    created and nothing else: every live member is proven the operation's own publication before
    the rollback unlinks it (_init_store_reversal_preverify; foreign content at a member path is
    preserved and the reversal refuses), and the scrub clears the payload staging leftovers, the
    lease and the created directories BEFORE discarding the record LAST, so the reversed
    substrate record (the recovery discriminator) survives until the filesystem cleanup is
    complete and is discarded, under the EXPLICIT recover flag, by the next init-store dispatch
    that finds its rolled-back intent (an interrupted scrub is simply finished). A
    post-publication verification failure executes the same reversal immediately. A reversed or
    completed run never reruns under the same run id: changing approved work takes a fresh plan with
    its own run id (spec 14.1) -- and that FRESH run admits the reversed run's committed artefacts
    (its verified evidence bundle, its archive copies, and the machine directory its committed
    archival emptied) once each is proven (_init_store_prior_run_paths), so an earlier phase's
    committed work is continued, never re-done, refused as foreign, or stranded (claude round 4,
    F1).

    Preconditions, before anything is written: the row targets the frozen default machine store at
    the product root; its members are EXACTLY the scaffold's complete creation set, every
    plan-bindable digest bound (fixed payloads checked against the builders here) and init.toml
    carrying the explicit unbound marker; a re-adoption names an ancestral seed (counters are seeded
    from the pinned high-water, never from zero) and a first adoption names none; the adoption
    journal is clean (reconcile-first); the store posture is one of the two first-adoption states;
    every pre-existing file at a declared external destination carries a digest-matched disposition;
    and every live `.working` file is justified by the plan (_init_store_admitted) — the substrate
    re-verifies the admitted inventory under its own mutex and records it in its immutable plan.

    VALID only when the substrate reached VIEWS-READY under the intent's operation id, its recorded
    plan creates exactly the members, and every member's live bytes verify. That is THIS OP's
    verdict; a `views-ready` milestone alone is never adoption success (spec 14), which still needs
    render-views, record-adoption and the completion checks. Every other outcome refuses
    CANNOT-EVALUATE after the reversal above. RESIDUAL (disclosed): init.toml's digest is bound by
    path and verified against the operation's validated recorded plan, never by a plan-time digest;
    and the context plan's own approval binding (plan_digest against the captured approval) is the
    stage driver's, not this op's."""
    try:
        return _init_store_run(op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot("init-store refused: {}".format(exc))


def _init_store_run(op_row, context):
    import uuid
    ctx = context if isinstance(context, dict) else {}
    product_root, plan, ancestral = ctx.get("product_root"), ctx.get("plan"), ctx.get("ancestral")
    recover = ctx.get("recover", False)
    if not isinstance(recover, bool):
        raise AdoptApplyError("its context recover flag {!r} is not a bool (fail-closed)".format(recover))
    if not (isinstance(plan, dict) and isinstance(plan.get("store"), dict)
            and isinstance(plan.get("sources"), list)):
        raise AdoptApplyError("its context carries no approved plan store identity and sources (fail-closed)")
    run_id = plan.get("run_id")
    if not is_run_id(run_id):
        raise AdoptApplyError("its context plan carries no adoption run id (the run's init-store "
                              "transaction is named by it); fail-closed")
    frozen = plan["store"]
    kind = frozen.get("adoption")
    if kind not in schema.ADOPTION_KINDS:
        raise AdoptApplyError("plan store adoption {!r} is outside {}".format(kind, schema.ADOPTION_KINDS))
    if kind == "re-adoption" and not isinstance(ancestral, str):
        raise AdoptApplyError("a re-adoption seeds its counters from a pinned ancestral snapshot, never from "
                              "zero (decision 6), and no evidence commit was supplied")
    if kind == "first-adoption" and ancestral is not None:
        raise AdoptApplyError("a first adoption has no ancestry, so an ancestral counters seed contradicts it")
    variant = init_op.CHANGELOG_RELPATH in {m.get("path") for m in op_row["members"]
                                            if isinstance(m, dict)}
    manifest_rel, scaffold = _init_store_scaffold(kind == "first-adoption", variant)
    machine = manifest_rel.rsplit("/", 1)[0]
    if op_row["store_root"] != "." or frozen.get("store_root") != "." or frozen.get("machine_rel") != machine:
        raise AdoptApplyError("the coupled-init substrate scaffolds only the default machine store {} at the "
                              "product root (row store_root {!r}, plan store {!r})".format(
                                  machine, op_row["store_root"], frozen))
    members = _init_store_members_or_refuse(op_row, scaffold, manifest_rel)
    txn = _txn_name(run_id, INIT_STORE_PHASE)
    root_fd = _open_product_root(product_root)
    journal_root = _journal_root(product_root)
    jr_fd = None
    held = False
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise AdoptApplyError("{} (fail-closed)".format(exc))
        journal_clean_or_refuse(root_fd, journal_root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot prepare the adoption journal {} ({}); nothing "
                                  "written (fail-closed)".format(JOURNAL_REL, exc))
        try:
            _journal.acquire_lock(journal_root, SESSION_ID)
        except _journal.JournalError as exc:
            raise AdoptApplyError("cannot take the adoption journal lock ({}); nothing "
                                  "written (fail-closed)".format(exc))
        held = True
        # Under the ONE lock both writers share (the shell's transactions take this same lock), so
        # nothing can interleave between these observations and the substrate's publication.
        _owner, opened = journal_state(root_fd, journal_root)
        if opened:
            raise AdoptApplyError("interrupted adoption transaction(s) {} must be reconciled before "
                                  "this run (run reconcile()); nothing written (fail-closed)".format(
                                      ", ".join(opened)))
        state = "nothing-opened"
        try:
            if _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn) is not None:
                state = _journal.classify_state(jr_fd, journal_root / txn)
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("cannot classify this run's init-store transaction ({}); "
                                  "fail-closed".format(exc))
        if state == "complete":
            raise AdoptApplyError("run {} already completed its init-store transaction: one run takes "
                                  "one transaction per phase, and changing approved work takes a fresh "
                                  "plan with its own run id (spec 14.1); nothing written "
                                  "(fail-closed)".format(run_id))
        if state != "nothing-opened":
            raise AdoptApplyError("run {}'s init-store transaction was reversed ({}); a retried or "
                                  "changed run takes a fresh plan with its own run id (spec 14.1); "
                                  "nothing written (fail-closed)".format(run_id, state))
        record = _init_store_record(product_root)
        if record is not None:
            _init_store_orphan_or_refuse(product_root, root_fd, jr_fd, journal_root, record, recover)
        _store_posture_or_refuse(product_root)
        _init_store_dispositions_or_refuse(root_fd, plan)
        # The row decided whether CHANGELOG.md is a creation; the live tree must agree: a
        # pre-existing (dispositioned) changelog is preserved, never a member, and an absent one is
        # created, so it is one.
        if (observe_live(root_fd, init_op.CHANGELOG_RELPATH)["kind"] == "file") == variant:
            raise AdoptApplyError("the row's members are not exactly the scaffold's creations: "
                                  "CHANGELOG.md {} (nothing written beyond the journal "
                                  "directories)".format(
                                      "pre-exists dispositioned and is preserved, never a member"
                                      if variant else
                                      "is absent, so the scaffold creates it as a member"))
        admitted, admitted_dirs = _init_store_admitted(root_fd, product_root, plan,
                                                        jr_fd, journal_root)
        operation_id = str(uuid.uuid4())
        working_created = not admitted and store.WORKING_DIRNAME not in admitted_dirs
        machine_created = machine not in admitted_dirs
        header = dict(kind=KIND, run_id=run_id, phase=INIT_STORE_PHASE, operation=OPERATION,
                      op="init-store", adoption=kind, ancestral=ancestral,
                      init_operation_id=operation_id, working_created=working_created,
                      machine_created=machine_created, admitted=[dict(e) for e in admitted],
                      admitted_dirs=list(admitted_dirs))
        _init_store_txn_begin(root_fd, jr_fd, journal_root, txn, header,
                              _init_store_intent_ops(members))
        result = init_op.run_init_operation(str(product_root), ancestral=ancestral, recover=recover,
                                            admitted=admitted, operation_id=operation_id,
                                            admitted_dirs=admitted_dirs)
        if result.status != init_op.VIEWS_READY:
            failure = "the coupled-init substrate ended {} ({})".format(
                result.status, (result.primary_failure or {}).get("detail"))
        elif result.operation_id != operation_id:
            failure = "the substrate ran operation {} where the intent authorized {}".format(
                result.operation_id, operation_id)
        else:
            failure = _init_store_postcondition(root_fd, product_root, members, operation_id)
        if failure is not None:
            _init_store_reverse(product_root, root_fd, jr_fd, journal_root, txn, operation_id,
                                working_created=working_created,
                                machine_created=machine_created, members=sorted(members))
            raise AdoptApplyError("{}; the scaffold was reversed from the transaction's durable "
                                  "intent, restoring NOT-ADOPTED, and a retried or changed run takes "
                                  "a fresh plan with its own run id (spec 14.1, 14.2)".format(failure))
        try:
            _journal.publish(jr_fd, journal_root / txn, _journal.F_COMPLETE, {"txn": txn})
        except (_journal.JournalError, OSError) as exc:
            raise AdoptApplyError("the scaffold is published but its transaction cannot complete "
                                  "({}); reconcile() reverses it (fail-closed)".format(exc))
        return schema._ok()
    finally:
        if held:
            try:
                _journal.release_lock(journal_root)
            except (_journal.JournalError, OSError):
                pass   # a leftover lock refuses the next run into reconcile(), never a silent seize
        if jr_fd is not None:
            _journal._close_fd_quietly(jr_fd)
        store._close_fd_exc_safe(root_fd)


# --- the registration ops (slice 4): register-unmanaged and repoint-consumer --------------------------

class RegistrationContext:
    """What the two registration handlers compose against: the transaction's ApplyOps, the plan's frozen
    store identity (its `store` table; the manifest every register-unmanaged row rewrites is
    _opf_adopt.store_manifest of it, never a caller-supplied path), and the planned postimage MODEL of each
    repoint-consumer path, which the handler re-emits and digest-checks against the row before staging it.
    `kept` maps each kept path to its approved source digest (the plan's keep source rows), against which
    register-unmanaged re-checks the kept file's live bytes. `rewrites` holds the one write op each
    rewritten path has in this transaction, so a register-unmanaged chain extends that one write link by link
    (one op per path) and a second repointing of one consumer refuses."""

    def __init__(self, ops, store_table=None, consumers=None, kept=None):
        self.ops = ops
        self.store = store_table
        self.consumers = dict(consumers or {})
        self.kept = dict(kept or {})
        self.rewrites = {}


def _canonical_model(rel, data):
    """PRECONDITION (the record writer's byte-reproduction discipline, _opf_record._require_canonical):
    parse one rewritten file as TOML and require that re-emitting the UNCHANGED model reproduces its bytes
    exactly, so a whole-file regeneration loses nothing. A file that is not UTF-8 TOML, or that carries
    comments or non-canonical serialization, refuses with its bytes untouched. It proves serialization
    only: a hand edit that leaves canonical bytes is caught by the row's old_digest, not here."""
    try:
        model = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise AdoptApplyError("{!r} does not parse as TOML ({}); this slice re-emits a whole file only "
                              "through the canonical TOML emitter, never a text substitution "
                              "(fail-closed)".format(rel, exc))
    try:
        canonical = emit_checked(model).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("{!r} does not round-trip through the canonical emitter ({}); "
                              "fail-closed".format(rel, exc))
    if canonical != data:
        raise AdoptApplyError("{!r} is not in canonical new-document form (it carries comments or "
                              "non-canonical serialization); a whole-file re-emit could lose content, so "
                              "nothing is rewritten (fail-closed)".format(rel))
    return model


def _current_bytes(context, rel, old_digest):
    """The bytes a row rewrites: this transaction's pending postimage when an earlier row already rewrote
    `rel` (a registration chain's previous link), else the LIVE bytes, re-observed contained. Either must
    hash to the row's old_digest; drift refuses into a fresh plan (spec 14.1)."""
    pending = context.rewrites.get(rel)
    if pending is not None:
        data = context.ops.staged.get(rel)
    else:
        fst, data = _read_live(context.ops.root_fd, rel)
        if fst is None:
            raise AdoptApplyError("{!r} is absent at apply; a fresh plan with its own approval is the remedy "
                                  "(spec 14.1)".format(rel))
    if not isinstance(data, bytes) or "sha256:" + _sha256(data) != old_digest:
        raise AdoptApplyError("{!r} is drifted: its bytes no longer match the row's old_digest, so it is "
                              "never rewritten; a fresh plan with its own approval is the remedy "
                              "(spec 14.1)".format(rel))
    return data


def _stage_rewrite(context, rel, old_digest, new_bytes):
    """Compose one whole-file rewrite preserve-first (spec 14.2): the first rewrite of `rel` archives its
    live bytes, re-verified against old_digest, at this run's adoption archive and then overwrites it
    through a `write` pinned to those bytes and mode, which is also its reversal (the journal restores the
    recorded old bytes); a later link of a chain only replaces that write's planned bytes."""
    ops = context.ops
    if ops.sealed:
        raise AdoptApplyError("the transaction is sealed by its inventory; nothing may follow it")
    pending = context.rewrites.get(rel)
    if pending is not None:
        pending["poststate"]["content-sha256"] = _sha256(new_bytes)
    else:
        data, mode = ops.preserve_masked(rel, old_digest)
        pending = dict(op="write", path=rel, poststate=dict(kind="file"),
                       **{"source-poststate": dict(kind="file", mode=mode, sha256=_sha256(data))})
        pending["poststate"]["content-sha256"] = _sha256(new_bytes)
        ops.ops.append(pending)
        context.rewrites[rel] = pending
    ops.staged[rel] = new_bytes


def _adopt_created(context, rel):
    """Fold a file this transaction CREATES into the rewrite chain: the manifest init-store scaffolds when no
    store resolves is where the planner's registration chain starts (_opf_adopt_plan._bind_registrations),
    so its create op becomes the one op the chain extends link by link, with no live bytes to preserve and
    the journal's reversal of a create (removal) undoing every link."""
    if rel not in context.rewrites:
        created = [op for op in context.ops.ops if op.get("op") == "create" and op.get("path") == rel]
        if created:
            context.rewrites[rel] = created[-1]


def _store_model(context, manifest):
    """The frozen store manifest as this transaction leaves it so far (its pending chain link, a manifest
    created earlier in this transaction, or the live bytes), parsed and VALID, else refuse: both
    registration ops need it to tell the store's own paths from adopter content (fail-closed)."""
    _adopt_created(context, manifest)
    if manifest in context.rewrites:
        data = context.ops.staged.get(manifest)
    else:
        fst, data = _read_live(context.ops.root_fd, manifest)
        if fst is None:
            raise AdoptApplyError("the store manifest {!r} the plan's store identity names is absent at "
                                  "apply; a fresh plan with its own approval is the remedy "
                                  "(spec 14.1)".format(manifest))
    # For a LIVE manifest this parse guard is redundant with the posture gate, which refuses an unparseable
    # manifest first ("the store posture cannot be evaluated"); it is reachable only for a manifest created
    # earlier in this transaction (a redundant pair, disclosed).
    try:
        model = tomllib.loads(data.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise AdoptApplyError("the store manifest {!r} does not parse as TOML ({}); "
                              "fail-closed".format(manifest, exc))
    if store.validate_manifest(model).status != store.VALID:
        raise AdoptApplyError("the store manifest {!r} is not a valid manifest; nothing is registered or "
                              "repointed (fail-closed)".format(manifest))
    return model


def _reserved_store_path(path, context, model):
    """Why `path` is a store path neither registration op may name, or None. The spec 14.2 store control
    area (_opf_adopt._in_control_area: archive/, imported/, staging/, journals/ and imports/ in EVERY homes
    generation, this run's own adoption archive included; an [unmanaged] declaration MUST NOT equal, contain
    or lie within them), a protected destination (_opf_adopt.protected_destination: .git and .aiqt at any
    depth, the store pointers, the store tree's .gitignore), the machine store subtree, or a declared view
    target of `model`, each in either containment direction; the control area and protected names are
    composed under the product root and the frozen store root alike, and the machine store and view
    targets are compared under _manifest_spelling. This covers every area the planner's keep path refuses
    (_opf_adopt_plan._decisions: the control area, a declared view, the machine store). protected_destination
    also refuses every control-area path but this run's own archive, so for those paths the two checks are a
    redundant pair (disclosed); this run's archive is the control-area check's alone."""
    run_id = context.ops.run_id
    for root in sorted({".", context.store["store_root"]}):
        if schema._in_control_area(path, root):
            return "{!r} lies in the reserved store control area (spec 14.2)".format(path)
        why = schema.protected_destination(path, run_id, root)
        if why is not None:
            return "{!r} is a protected destination: {}".format(path, why)
    machine = schema.store_manifest(context.store).rsplit("/", 1)[0]
    if _overlaps(path, machine):
        return "{!r} overlaps the machine store {!r}".format(path, machine)
    views = [v["target"] for v in model.get("views", {}).values()]
    hit = [v for v in views if _overlaps(path, v)]
    if hit:
        return "{!r} overlaps the declared view {!r}".format(path, hit[0])
    return None


def _without_last_entry(model, entry, prior):
    """The reparsed postimage with its one appended [unmanaged] entry taken back out, shaped as `prior` had
    it (an [unmanaged] table or paths list the registration introduced is dropped), or None when the last
    path is not `entry`. The delta is derived from the prior model, never from the planner's rows."""
    out = copy.deepcopy(model)
    paths = out.get("unmanaged", {}).get("paths")
    if not isinstance(paths, list) or not paths or paths[-1] != entry:
        return None
    paths.pop()
    if not paths and "paths" not in prior.get("unmanaged", {}):
        del out["unmanaged"]["paths"]
    if not out["unmanaged"] and "unmanaged" not in prior:
        del out["unmanaged"]
    return out


def _compose_register_unmanaged(op_row, context):
    """register-unmanaged, apply stage only (spec 14.2 Keep: the file stays exactly where it is,
    untouched; only the frozen store manifest changes). The manifest is the plan's store identity's, read
    from this transaction's pending chain link, a manifest this transaction creates (the chain then
    extends that create), or the live tree, and required to equal old_digest; its bytes must be canonical
    (the byte-reproduction precondition) and VALID; the entry must not be a reserved store path
    (_reserved_store_path), overlap an entry already registered, or be written by this transaction, and the
    kept file must be a regular file at apply whose bytes hash to its approved source digest (context.kept);
    the postimage is the planner's own rendering (_opf_adopt_plan's registration
    binding: the kept path appended to [unmanaged].paths, emitted through emit_checked), and its reparse
    must equal the prior model plus exactly that one entry (the allowed-delta postcondition, type-aware),
    validate as a manifest, and hash to new_digest, else nothing is composed. Reversal is the journaled
    write's: the recorded manifest preimage restored, which drops the entry (or, for a manifest this
    transaction creates, the create's removal)."""
    entry = op_row["entry"]
    manifest = schema.store_manifest(context.store)
    if manifest is None:
        raise AdoptApplyError("register-unmanaged needs the plan's frozen store identity to name the "
                              "manifest it rewrites, and none was supplied (fail-closed)")
    _adopt_created(context, manifest)
    current = _current_bytes(context, manifest, op_row["old_digest"])
    model = _canonical_model(manifest, current)
    if store.validate_manifest(model).status != store.VALID:
        raise AdoptApplyError("the store manifest {!r} is not a valid manifest; nothing is registered "
                              "(fail-closed)".format(manifest))
    collision = _reserved_store_path(entry, context, model)
    registered = [k for k in model.get("unmanaged", {}).get("paths", []) if _overlaps(entry, k)]
    if collision is None and registered:
        collision = "{!r} is already registered [unmanaged] (as {!r})".format(entry, registered[0])
    # compared under _manifest_spelling like every other overlap guard: a case alias of a consumer an
    # earlier row repoints names that one file on a case-insensitive filesystem
    if collision is None and any(op.get("op") in ("create", "write", "remove")
                                 and _overlaps(op.get("path", ""), entry) for op in context.ops.ops):
        collision = ("{!r} is written by this transaction, and a kept file stays exactly where it is, "
                     "untouched (spec 14.2 Keep)".format(entry))
    if collision is not None:
        raise AdoptApplyError("register-unmanaged refused: {} (fail-closed)".format(collision))
    try:
        kept = _journal._lstat_contained(context.ops.root_fd, entry)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot observe the kept file {!r} ({}); fail-closed".format(entry, exc))
    # An absent entry binds no approved digest and _read_live refuses any non-regular entry, so the two
    # checks below refuse whatever this guard does (redundant guards, disclosed); it names the cause first.
    if kept is None or not stat.S_ISREG(kept.st_mode):
        raise AdoptApplyError("the kept file {!r} is not a regular file at apply (absent, a directory, a "
                              "symlink or a special entry); Keep registers a file that stays where it is, so "
                              "a fresh plan with its own approval is the remedy (spec 14.1, "
                              "14.2)".format(entry))
    approved = context.kept.get(entry)
    # with no digest the drift comparison below refuses too (a redundant pair, disclosed); this guard
    # names the cause
    if not schema._is_digest(approved):
        raise AdoptApplyError("no approved source digest binds the kept file {!r}; Keep registers only the "
                              "bytes the plan approved (fail-closed)".format(entry))
    _fst, kept_bytes = _read_live(context.ops.root_fd, entry)
    if not isinstance(kept_bytes, bytes) or "sha256:" + _sha256(kept_bytes) != approved:
        raise AdoptApplyError("the kept file {!r} is drifted: its live bytes no longer match its approved "
                              "source digest, so it is never registered; a fresh plan with its own approval "
                              "is the remedy (spec 14.1)".format(entry))
    planned = copy.deepcopy(model)
    planned.setdefault("unmanaged", {}).setdefault("paths", []).append(entry)
    try:
        new_bytes = emit_checked(planned).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("the registered manifest cannot be emitted canonically ({}); "
                              "fail-closed".format(exc))
    reparsed = tomllib.loads(new_bytes.decode("utf-8"))
    back = _without_last_entry(reparsed, entry, model)
    if back is None or not _model_equal(back, model):
        raise AdoptApplyError("the registered manifest is not the prior manifest plus exactly the one "
                              "[unmanaged] entry {!r}; nothing is rewritten (fail-closed)".format(entry))
    if store.validate_manifest(reparsed).status != store.VALID:
        raise AdoptApplyError("registering {!r} yields an invalid manifest; nothing is rewritten "
                              "(fail-closed)".format(entry))
    if "sha256:" + _sha256(new_bytes) != op_row["new_digest"]:
        raise AdoptApplyError("the registered manifest does not hash to the row's new_digest, the postimage "
                              "the approval bound; nothing is rewritten (fail-closed)")
    _stage_rewrite(context, manifest, op_row["old_digest"], new_bytes)


def _compose_repoint_consumer(op_row, context):
    """repoint-consumer, apply stage only: a whole-file parse-and-re-emit, never a regex substitution over
    repository text. The live consumer must equal old_digest and be canonical TOML (the byte-reproduction
    precondition, so the re-emit drops nothing the model does not carry; any other format refuses, since
    this slice's one canonical emitter is TOML's); the planned postimage model is re-emitted through
    emit_checked and must hash to new_digest and differ from the old bytes. Without the plan's store
    identity, or with its manifest absent, unparseable or invalid, it refuses, since it cannot then tell the
    store's own files from a consumer. A reserved store path (_reserved_store_path: the machine store and
    its manifest, a declared view, the store control area, a protected destination) and a file kept under
    [unmanaged] (live, or registered by an earlier row of this transaction) are never a consumer. Reversal
    is the journaled write's: the recorded old bytes restored."""
    path = op_row["path"]
    manifest = schema.store_manifest(context.store)
    if manifest is None:
        raise AdoptApplyError("repoint-consumer needs the plan's frozen store identity to tell the store's "
                              "own files from a consumer, and none was supplied (fail-closed)")
    model = _store_model(context, manifest)
    reserved = _reserved_store_path(path, context, model)
    if reserved is not None:
        raise AdoptApplyError("repoint-consumer refused: {}, never a consumer (fail-closed)".format(reserved))
    kept = [k for k in model.get("unmanaged", {}).get("paths", []) if _overlaps(path, k)]
    if kept:
        raise AdoptApplyError("repoint-consumer {!r} overlaps the [unmanaged] entry {!r}: ordinary tooling "
                              "never rewrites a kept file (spec 14.2 Keep; fail-closed)".format(path, kept[0]))
    if any(_overlaps(path, rewritten) for rewritten in context.rewrites):
        raise AdoptApplyError("{!r} is repointed by two plan rows; one row rewrites one consumer "
                              "(fail-closed)".format(path))
    _canonical_model(path, _current_bytes(context, path, op_row["old_digest"]))
    planned = context.consumers.get(path)
    if not isinstance(planned, dict):
        raise AdoptApplyError("no planned postimage model for repoint-consumer {!r} "
                              "(fail-closed)".format(path))
    try:
        new_bytes = emit_checked(planned).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("the repointed consumer {!r} cannot be emitted canonically ({}); "
                              "fail-closed".format(path, exc))
    if "sha256:" + _sha256(new_bytes) != op_row["new_digest"]:
        raise AdoptApplyError("the re-emitted consumer {!r} does not hash to the row's new_digest, the "
                              "postimage the approval bound; nothing is rewritten (fail-closed)".format(path))
    if op_row["new_digest"] == op_row["old_digest"]:
        raise AdoptApplyError("repoint-consumer {!r} repoints nothing (its new_digest is its old_digest); "
                              "nothing is rewritten (fail-closed)".format(path))
    _stage_rewrite(context, path, op_row["old_digest"], new_bytes)


_REGISTRATION_OPS = {"register-unmanaged": _compose_register_unmanaged,
                     "repoint-consumer": _compose_repoint_consumer}


def _execute_registration_op(op_row, context=None):
    """The one slice-4 handler behind the two registration ops: compose this row's journal ops into the
    base (apply-stage) transaction its RegistrationContext carries and report VALID; any refusal (drift, a
    non-canonical or invalid file, a reserved-path collision, a postimage off new_digest, a wrong phase)
    is CANNOT-EVALUATE naming the reason, raised before the row appends an op. Nothing is written here:
    the composed ops run only inside run_adopt_transaction, after check_apply_ops re-proves the list."""
    name = op_row.get("op")
    if not isinstance(context, RegistrationContext) or not isinstance(context.ops, ApplyOps):
        return schema._cannot("op {!r} composes only inside an adoption transaction, through its "
                              "RegistrationContext; nothing composed (fail-closed)".format(name))
    if context.ops.phase is not None:
        return schema._cannot("op {!r} runs only in the apply stage (the run's base transaction), not phase "
                              "{!r}; nothing composed (fail-closed)".format(name, context.ops.phase))
    try:
        _REGISTRATION_OPS[name](op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot(str(exc))
    return schema._ok()


# --- the enable-hook op (PR3 slice 2): the threat-modelled registration merge, published preserve-first -

class HookContext:
    """What the enable-hook handler composes against: the transaction's ApplyOps. The row carries all the
    approval bound (registration_path, plugin_entry, old_digest, new_digest), so nothing supplied here can
    widen what the row registers."""

    def __init__(self, ops):
        self.ops = ops


def _compose_enable_hook(op_row, ops):
    """enable-hook, apply stage only, over the pure merge core of _opf_adopt_hook (whose docstring carries
    the threat model). The registration path must name a supported v1 registration target (the merge
    core's closed REGISTRATION_PATHS allowlist: PATH identity selects the platform, decided BEFORE any
    read, so an unsupported platform or a non-settings file refuses with nothing read and nothing
    written, however settings-shaped its content). The plugin_entry must match the pack-member grammar
    dispatch already validated, re-proved here because OP_HANDLERS is a public table and a direct
    caller skips dispatch; for the same reason a row that is not a table carrying string
    registration_path, plugin_entry, old_digest and new_digest refuses first, never an uncaught
    KeyError or AttributeError. The registration file must then be present, regular,
    contained and hash to the row's
    old_digest: drift refuses into a fresh plan, never a merge over unknown content (threat 3).
    merge_registration computes the merged bytes and refuses every unrecognized format or shape, conflicting
    entry and control-character vector (threats 2 and 5); the merged bytes must hash to the row's
    new_digest, so the one approval bound the exact post-merge executable registration byte for byte
    (threat 1). The live bytes are archived at this run's adoption archive, then overwritten through a
    `write` pinned to them, so the reversal (the journal restoring the recorded prior bytes) and the
    archived copy agree byte-exact. A verified no-op (the canonical entry already registered, so new_digest
    equals old_digest) composes nothing. Configuration only: nothing here or in the journal runs, loads or
    activates the registered hook (threat 6). Every refusal precedes the first appended op."""
    fields = ("registration_path", "plugin_entry", "old_digest", "new_digest")
    if not isinstance(op_row, dict) or not all(isinstance(op_row.get(k), str) for k in fields):
        raise AdoptApplyError("enable-hook row is not a table carrying string registration_path, "
                              "plugin_entry, old_digest and new_digest, re-proved here so a caller of the "
                              "handler table that skips dispatch gets an attributed refusal; nothing read "
                              "or written (fail-closed)")
    path = op_row["registration_path"]
    if schema._in_control_area(path) or schema.protected_destination(path, ops.run_id) is not None:
        raise AdoptApplyError("enable-hook {!r} names the store control area or a protected destination, "
                              "never a registration file (fail-closed)".format(path))
    if not hook.supported_registration_path(path):
        raise AdoptApplyError("enable-hook {!r} is not a supported v1 registration target (the closed "
                              "{} allowlist: exactly {}); an unsupported platform or a non-settings "
                              "file gets no enable-hook write at all, and content shape never admits "
                              "one (fail-closed)".format(path, hook.REGISTRATION_FAMILY,
                                                         ", ".join(hook.REGISTRATION_PATHS)))
    if not schema._is_hook_entry(op_row["plugin_entry"]):
        raise AdoptApplyError("enable-hook plugin_entry is not one word of the pack-member grammar "
                              "(_opf_adopt._is_hook_entry), re-proved here so a caller of the handler "
                              "table that skips dispatch still cannot register it; nothing read or "
                              "written (fail-closed)")
    if path in ops.staged:
        raise AdoptApplyError("{!r} is merged by two plan rows; one row merges one registration file "
                              "(fail-closed)".format(path))
    fst, data = _read_live(ops.root_fd, path)
    if fst is None:
        raise AdoptApplyError("registration file {!r} is absent at apply; a fresh plan with its own approval "
                              "is the remedy (spec 14.1)".format(path))
    if "sha256:" + _sha256(data) != op_row["old_digest"]:
        raise AdoptApplyError("registration file {!r} is drifted: its live bytes no longer match the row's "
                              "old_digest, so nothing is merged over unknown content; a fresh plan with its "
                              "own approval is the remedy (spec 14.1)".format(path))
    merged = hook.merge_registration(data, op_row["plugin_entry"])
    if merged.status != store.VALID:
        raise AdoptApplyError("the registration merge of {!r} refused ({}): {}".format(
            path, merged.status, "; ".join(merged.findings)))
    if merged.new_digest != op_row["new_digest"]:
        raise AdoptApplyError("the merged registration {!r} does not hash to the row's new_digest, the exact "
                              "executable registration the approval bound; nothing is written "
                              "(fail-closed)".format(path))
    if not merged.changed:
        return
    prior, mode = ops.preserve_masked(path, op_row["old_digest"])
    ops.ops.append(dict(op="write", path=path,
                        poststate=dict(kind="file", **{"content-sha256": _sha256(merged.new_bytes)}),
                        **{"source-poststate": dict(kind="file", mode=mode, sha256=_sha256(prior))}))
    ops.staged[path] = merged.new_bytes


def _execute_enable_hook(op_row, context=None):
    """The slice-2 handler behind enable-hook: compose the row's journal ops into the base (apply-stage)
    transaction its HookContext carries and report VALID; any refusal (drift, an absent or protected
    registration file, a merge refusal, merged bytes off new_digest, a wrong phase) is CANNOT-EVALUATE
    naming the reason, before the row appends an op. Nothing is written here: the composed ops run only
    inside run_adopt_transaction, after check_apply_ops re-proves the list."""
    if not isinstance(context, HookContext) or not isinstance(context.ops, ApplyOps):
        return schema._cannot("op 'enable-hook' composes only inside an adoption transaction, through its "
                              "HookContext; nothing composed (fail-closed)")
    if context.ops.phase is not None:
        return schema._cannot("op 'enable-hook' runs only in the apply stage (the run's base transaction), "
                              "not phase {!r}; nothing composed (fail-closed)".format(context.ops.phase))
    try:
        _compose_enable_hook(op_row, context.ops)
    except AdoptApplyError as exc:
        return schema._cannot(str(exc))
    return schema._ok()


# --- the finish ops: plant-governance, render-views, record-adoption (slice 5) --------------------------

# The run's receipt core and its genesis outcome event are members of the run's OWN evidence bundle (spec
# 4.2: `imported/<kind>/<run-id>/` holds the run's approvals and receipts; spec 14.2: adoption evidence is
# committed and immutable there, and append-only outcome events retain the receipt's history). So both are
# claimed by the transaction's derived inventory and never rewritten; each later event is a further bundle
# member that a later phase publishes.
RECEIPT_NAME = "receipt.toml"
GENESIS_EVENT_NAME = "event-0001.toml"


def receipt_rel(run_id):
    """`.working/imported/adoption/<run-id>/receipt.toml`, the run's immutable receipt core."""
    return evidence_home_rel(run_id) + "/" + RECEIPT_NAME


def genesis_event_rel(run_id):
    """`.working/imported/adoption/<run-id>/event-0001.toml`, the run's genesis `applied` outcome event."""
    return evidence_home_rel(run_id) + "/" + GENESIS_EVENT_NAME


def _composing_ops(op_row, context):
    """The ApplyOps a composing finish op appends to, carried as context["ops"] by the compose callable of
    one adoption transaction; anything else refuses, so such an op never writes outside the shell."""
    ops = context.get("ops") if isinstance(context, dict) else None
    if not isinstance(ops, ApplyOps):
        raise AdoptApplyError("op {!r} composes only inside an adoption transaction, and its context carries "
                              "no ApplyOps; nothing composed".format(op_row["op"]))
    return ops


def _context_plan(context):
    plan = context.get("plan") if isinstance(context, dict) else None
    if not isinstance(plan, dict):
        raise AdoptApplyError("its context carries no approved plan (fail-closed)")
    return plan


def verify_pack_member(plan, pack, source_member):
    """The b.5 trust gate for ONE pack member (spec 14.1: the release identity, bound by digest). `pack`
    carries the release manifest's exact bytes (`manifest`), the caller-observed `root.txt` bytes (`root`)
    and member bytes keyed by source path (`members`). The agreed ROOT is the plan's release.manifest_sha256,
    which must equal the plan's own anchor_sha256 (two fields of the ONE approved plan, frozen at approval;
    observing the anchor at its independent source is the planner's b.5 capture, never re-done here); the
    observed root.txt and the sha256 of the exact manifest bytes must both equal that ROOT; the manifest
    must parse VALID through the shipped _opf_pack_manifest grammar, its tree-sha256 must recompute from
    its own source rows (the consumer obligation that grammar leaves), and its release-version must BE the
    approved release's version, so the approved identity can never name one version while the digest-agreed
    pack carries another; and the member's bytes must match the size and sha256 its one source row records
    in that frozen inventory. Returns (bytes, bare-hex sha256); any failure raises AdoptApplyError. Passing
    proves the bytes are the ones the agreed release lists, never that its publisher is authentic beyond
    the anchor (the self-asserted-identity residual, spec 14.1)."""
    import _opf_pack_manifest as pack_manifest
    release = plan.get("release")
    agreed = release.get("manifest_sha256") if isinstance(release, dict) else None
    if not (schema._is_digest(agreed) and release.get("anchor_sha256") == agreed):
        raise AdoptApplyError("the plan's release manifest_sha256 does not agree with its independent anchor, "
                              "so there is no agreed ROOT (fail-closed)")
    if not isinstance(pack, dict):
        raise AdoptApplyError("its context carries no pack manifest, root.txt and member bytes (fail-closed)")
    root = pack_manifest.parse_root_txt(pack.get("root"))
    if not isinstance(root, str):
        raise AdoptApplyError("the observed root.txt is refused: {}".format("; ".join(root.findings)))
    if "sha256:" + root != agreed:
        raise AdoptApplyError("the observed root.txt names a ROOT other than the plan's agreed release ROOT")
    manifest = pack.get("manifest")
    if type(manifest) is not bytes or "sha256:" + pack_manifest.compute_root(manifest) != agreed:
        raise AdoptApplyError("the pack manifest bytes do not hash to the agreed ROOT (a substituted or "
                              "altered manifest)")
    document, checked = pack_manifest.parse_manifest(manifest)
    if checked.status != store.VALID:
        raise AdoptApplyError("the pack manifest is refused ({}): {}".format(
            checked.status, "; ".join(checked.findings)))
    if pack_manifest.compute_tree(document["sources"]) != document["tree-sha256"]:
        raise AdoptApplyError("the pack manifest's tree-sha256 does not recompute from its own source rows")
    if document["release-version"] != release.get("version"):
        raise AdoptApplyError("the pack manifest's release-version {!r} is not the approved release version "
                              "{!r}; the approved release identity is contradictory (fail-closed)".format(
                                  document["release-version"], release.get("version")))
    rows = [row for row in document["sources"] if row["path"] == source_member]
    if len(rows) != 1:
        raise AdoptApplyError("source_member {!r} is not a source row of the frozen pack "
                              "inventory".format(source_member))
    members = pack.get("members")
    data = members.get(source_member) if isinstance(members, dict) else None
    if type(data) is not bytes or len(data) != rows[0]["bytes"] or _sha256(data) != rows[0]["sha256"]:
        raise AdoptApplyError("pack member {!r} does not match the size and sha256 the frozen pack "
                              "inventory records (a forged or altered member)".format(source_member))
    return data, rows[0]["sha256"]


def _plant_governance(op_row, context=None):
    """plant-governance (spec 14.1): create-only planting of ONE pack member whose bytes passed the trust
    gate (verify_pack_member, against context["plan"] and context["pack"]) and whose verified digest is the
    row's content_digest, composed into the transaction context["ops"] carries. ApplyOps.create re-observes
    the path absent (a pre-existing different file routes to retire-file plus create-file under explicit
    plan rows, never an overwrite). The store tree is the engine's, never a governance adapter's, so a path
    there refuses, as do every protected destination and the product-root VERSION deliverable (spec 14.2
    names it render-managed; declared views live inside the store tree, refused here, and a planned
    collision with one is the plan validator's). Reversal before commit is the journal's rollback,
    which removes the planted file. Every refusal is CANNOT-EVALUATE and precedes any filesystem
    publication; one raised inside ApplyOps.create (a path component that is not a directory) can leave
    context["ops"] partly composed, so the compose callable MUST raise on any refusing verdict, discarding
    the whole transaction before it opens (run_adopt_transaction then writes nothing)."""
    try:
        ops = _composing_ops(op_row, context)
        path = op_row["path"]
        if (_within(path.casefold(), store.WORKING_DIRNAME)
                or schema.protected_destination(path, ops.run_id) is not None):
            raise AdoptApplyError("{!r} lies in the store tree or is a protected destination, where no "
                                  "governance adapter is planted (fail-closed)".format(path))
        if path.casefold() == "version":
            raise AdoptApplyError("{!r} is the product-root VERSION deliverable, a render-managed "
                                  "destination (spec 14.2), where no governance adapter is planted "
                                  "(fail-closed)".format(path))
        data, digest = verify_pack_member(_context_plan(context), context.get("pack"), op_row["source_member"])
        if "sha256:" + digest != op_row["content_digest"]:
            raise AdoptApplyError("the verified member {!r} is not the content_digest the row "
                                  "binds".format(op_row["source_member"]))
        ops.create(path, data)
    except AdoptApplyError as exc:
        return schema._cannot("plant-governance refused: {}".format(exc))
    return schema._ok()


def _planned_views(res):
    """{product-relative destination: bytes} of every declared view the render engine's own read-only
    planner (_opf_views.plan_views) renders for the resolved store `res`; writes nothing."""
    import _opf_views
    try:
        fd = store._open_store_root_fd(res.store_root, False)
    except OSError as exc:
        raise AdoptApplyError("cannot open the store root ({}); fail-closed".format(exc))
    try:
        planned = _opf_views.plan_views(fd, res.machine_rel)
    except (_opf_views.ViewsError, RecursionError, ValueError, OSError) as exc:
        raise AdoptApplyError("the render engine cannot plan the declared views ({})".format(exc))
    finally:
        inflight = _journal._in_flight_in(sys._getframe())
        try:
            store._close_fd_exc_safe(fd)    # in THIS frame: the #377 frame test sees what is in flight
        except OSError:
            raise                           # #377: raised only with nothing in flight here
        except BaseException as exc:        # noqa: BLE001  the first interrupt propagates as itself
            _journal._yield_close_exceptions(inflight, [exc])
    return {dest: text.encode("utf-8") for _name, _scope, dest, text in planned}


def _render_views(op_row, context=None):
    """render-views (spec 14, 14.2): compose, into the transaction context["ops"] carries, the CREATE of
    every declared view with the exact approved bytes, so the views land through the same journaled
    adoption transaction, per-op preimage checks, reversal (the journal's rollback removes them from their
    captured absent preimages) and adoption-journal lock as every other composing op. `context` carries
    `product_root` (the transaction's own product root, bound to ops.root_fd by directory identity), `plan`
    (its frozen `store` identity) and `observations` (the inert git-derived facts the git-aware caller
    gathers, exactly as `opf render --write` does through _opf_observe.gather).

    Preconditions, all before anything is composed: the plan's frozen DEFAULT store resolves at the product
    root, which is the transaction's own root; the row's members are EXACTLY the declared view destinations
    the render engine's own read-only planner renders, and each member digest is the digest of the bytes it
    renders, so apply reproduces the approved bytes or refuses, never writes others; the U6 source gate
    (_opf_check.validate_store with the caller's observations, judged by source_integrity_ok) passes, so no
    view is rendered over a store whose source integrity is not sound; every authored deliverable this op
    does not own (C-CHANGELOG-GATES always, C-VERSION-FILE unless a VERSION view is planned) already
    passes, so an authored residual refuses with NOTHING written, never after a write; and every
    destination is observed ABSENT, contained and no-follow. An occupied view destination refuses
    fail-closed in this slice (spec 14.2: adopter content is never absorbed, deleted or overwritten; the
    preserve-then-render write for a plan-enumerated occupying source composes with the file-ops slice).
    The journal then captures each create's absent prestate under its lock and digest-verifies the written
    poststate: a destination raced in between compose and that capture is refused there as a prestate
    violation before the transaction's INTENT publishes (nothing opened), and the create itself is
    exclusive and no-follow; an arbitrary concurrent writer is outside the journal's model. Every handler
    refusal is CANNOT-EVALUATE and precedes any filesystem publication; the preconditions above refuse
    before anything is composed, while one raised inside ApplyOps.create can leave context["ops"] partly
    composed, so the compose callable MUST raise on any refusing verdict, discarding the whole transaction
    before it opens (run_adopt_transaction then writes nothing)."""
    try:
        _render_views_compose(op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot("render-views refused: {}".format(exc))
    return schema._ok()


def _render_views_compose(op_row, context):
    import _opf_check
    ops = _composing_ops(op_row, context)
    plan = _context_plan(context)
    product_root = context.get("product_root")
    if not isinstance(product_root, (str, os.PathLike)):
        raise AdoptApplyError("its context carries no product_root (fail-closed)")
    frozen = plan.get("store") if isinstance(plan.get("store"), dict) else {}
    res = store.resolve_store(product_root)
    if not (res.status == store.RESOLVED and res.pointer_source == "default"
            and op_row["store_root"] == "." == frozen.get("store_root")
            and res.machine_rel == frozen.get("machine_rel")
            and Path(os.path.abspath(res.store_root)) == Path(os.path.abspath(product_root))):
        raise AdoptApplyError("render-views renders only the plan's frozen default store at the product "
                              "root, and it must resolve (status {}, plan store {!r}); nothing "
                              "composed".format(res.status, frozen))
    try:
        fd_st = os.fstat(ops.root_fd)
        ctx_st = os.stat(os.path.abspath(product_root))
    except OSError as exc:
        raise AdoptApplyError("cannot bind the context product root to the transaction root ({}); "
                              "fail-closed".format(exc))
    if (fd_st.st_dev, fd_st.st_ino) != (ctx_st.st_dev, ctx_st.st_ino):
        raise AdoptApplyError("the context product_root is not the transaction's own product root, so the "
                              "composed views would land in a tree the store checks never saw; nothing "
                              "composed (fail-closed)")
    planned = _planned_views(res)
    members = {m["path"]: m["digest"] for m in op_row["members"]}
    if set(members) != set(planned):
        raise AdoptApplyError("the row's members are not exactly the declared view destinations {}; "
                              "nothing composed".format(sorted(planned)))
    stale = sorted(p for p in planned if "sha256:" + _sha256(planned[p]) != members[p])
    if stale:
        raise AdoptApplyError("the render engine renders bytes other than the row binds at {}; apply "
                              "reproduces the approved bytes or refuses, never writes others (nothing "
                              "composed)".format(", ".join(stale)))
    report = _opf_check.validate_store(res, observations=context.get("observations"))
    if not _opf_check.source_integrity_ok(report):
        raise AdoptApplyError("the store's SOURCE integrity is not sound (U6 validate_store), so no view is "
                              "rendered over it; nothing composed")
    owned = {"C-VIEW-DRIFT"} | ({"C-VERSION-FILE"} if "VERSION" in planned else set())
    residual = sorted(cid for cid in _opf_check.DELIVERABLE_DRIFT_CHECKS
                      if cid not in owned and report.checks.get(cid) != "PASS")
    if residual:
        raise AdoptApplyError("authored deliverable(s) {} must already pass; render-views renders only its "
                              "own declared views and repairs nothing authored; nothing composed".format(
                                  ", ".join(residual)))
    occupied = sorted(p for p in planned if observe_live(ops.root_fd, p)["kind"] != "absent")
    if occupied:
        raise AdoptApplyError("view destination(s) {} are occupied; adopter content is never absorbed or "
                              "overwritten (spec 14.2), and an occupying source routes to its preserve-first "
                              "disposition with the file ops; nothing composed".format(", ".join(occupied)))
    for path in sorted(planned):
        ops.create(path, planned[path])


def event_digest(event):
    """The event_digest of ONE outcome event (the spec 14.2 append-only events): the sha256 of the event's
    canonical bytes with the `event_digest` key omitted, so the digest can never cover itself. The ONE
    definition both the mint and the recomputation check use: adoption_record re-parses the bytes it
    emitted and requires the recorded digest to recompute through this helper, so a recorded digest that
    is not the canonical-bytes digest can never publish. Raises AdoptApplyError fail-closed."""
    if not isinstance(event, dict):
        raise AdoptApplyError("an outcome event must be a table")
    body = {key: event[key] for key in event if key != "event_digest"}
    try:
        return "sha256:" + _sha256(emit_checked(body).encode("utf-8"))
    except EmitError as exc:
        raise AdoptApplyError("the outcome event cannot be emitted canonically ({}); fail-closed".format(exc))


def adoption_record(run_id, plan, receipt, now):
    """(receipt core bytes, genesis event bytes) of one adoption run (spec 14, 14.1), pure. The receipt core
    the stage driver assembles must validate through the shipped validate_receipt_core, which also enforces
    that the embedded approval attests the receipt's own plan_digest and inventory_digest; it must bind this
    run and the approved plan's product, plan_digest, inventory_digest, store root and release manifest, and
    record an observed, agreeing independent anchor. The genesis `applied` event chains from the sha256 of
    the canonical core bytes; its event_digest is the named event_digest helper's (the sha256 of its own
    canonical bytes without that key), required to RECOMPUTE from the emitted bytes before anything returns;
    the one-event chain must validate through the shipped validate_event_chain. `now` is the injected,
    aware-UTC recorded_at instant. Raises AdoptApplyError on any refusal."""
    checked = schema.validate_receipt_core(receipt)
    if checked.status != store.VALID:
        raise AdoptApplyError("the receipt core is refused ({}): {}".format(
            checked.status, "; ".join(checked.findings)))
    if receipt["run_id"] != run_id or plan.get("run_id") != run_id:
        raise AdoptApplyError("the receipt core, the plan and the transaction name different adoption runs")
    unbound = [key for key in ("product", "plan_digest", "inventory_digest") if receipt[key] != plan.get(key)]
    frozen, release = plan.get("store"), plan.get("release")
    if not (isinstance(frozen, dict) and receipt["store_root"] == frozen.get("store_root")):
        unbound.append("store_root")
    if not (isinstance(release, dict)
            and receipt["release"]["manifest_sha256"] == release.get("manifest_sha256")):
        unbound.append("release.manifest_sha256")
    if unbound:
        raise AdoptApplyError("the receipt core does not bind the approved plan's {}".format(
            ", ".join(unbound)))
    anchor = receipt["release"]
    if anchor["independent_anchor_observed"] is not True or anchor["anchor_agreement"] is not True:
        raise AdoptApplyError("the receipt records no observed, agreeing independent release anchor")
    if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
            or now.utcoffset() != datetime.timedelta(0)):
        raise AdoptApplyError("now must be a clock-derived aware UTC datetime")
    try:
        core = emit_checked(receipt).encode("utf-8")
        event = dict(format=schema.EVENT_FORMAT, schema=schema.SCHEMA_VERSION, kind="applied", run_id=run_id,
                     revision=plan.get("revision"), recorded_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     previous_event_digest="sha256:" + _sha256(core))
        event["event_digest"] = event_digest(event)
        event_bytes = emit_checked(event).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("the adoption record cannot be emitted canonically ({}); "
                              "fail-closed".format(exc))
    # Defence in depth: emit_checked already refuses bytes that do not round-trip, so by construction no
    # vector reaches this refusal; the self-test pins the contract with an independent recompute of the
    # canonical event body instead.
    reparsed = tomllib.loads(event_bytes.decode("utf-8"))
    if reparsed.get("event_digest") != event_digest(reparsed):
        raise AdoptApplyError("the genesis event_digest does not recompute from the emitted event's own "
                              "canonical bytes (fail-closed)")
    chain = schema.validate_event_chain([tomllib.loads(event_bytes.decode("utf-8"))], "sha256:" + _sha256(core))
    if chain.status != store.VALID:
        raise AdoptApplyError("the genesis outcome event is refused ({}): {}".format(
            chain.status, "; ".join(chain.findings)))
    return core, event_bytes


def minted_record_row(run_id, core):
    """The stage driver's record-adoption row MINT (spec 14, 14.1): the ONE executable row shape, minted
    only once the approval exists and the receipt core's canonical bytes are assembled, binding this run's
    bundle receipt path and the core's digest. The EXPLICIT stage-driver rule, pinned in the self-test: the
    executable row is never a plan operand -- validate_plan itself refuses a plan whose record-adoption row
    names this bundle receipt path (the receipt creation is a reserved-control-area effect, spec 14.2) --
    and the plan binds the receipt through the approval digests the core must embed (validate_receipt_core
    plus the bindings _record_adoption enforces), while the genesis event is bound by chaining from the
    core digest this row carries and by its recomputed event_digest."""
    if not isinstance(core, bytes):
        raise AdoptApplyError("minted_record_row needs the receipt core's canonical bytes (fail-closed)")
    return dict(op="record-adoption", receipt_path=receipt_rel(run_id),
                receipt_core_digest="sha256:" + _sha256(core))



def _record_adoption(op_row, context=None):
    """record-adoption (spec 14, 14.1): compose, into the transaction context["ops"] carries, the create of
    the run's immutable receipt core at its bundle receipt_rel and of its genesis `applied` outcome event
    beside it (adoption_record, from context["plan"], context["receipt"] and context["now"]). The row must
    name exactly that receipt path and bind the emitted core's digest: as the planner records, no frozen
    plan can carry that digest (the core binds the approval captured after the plan freezes), so the stage
    driver MINTS this row once the approval exists (minted_record_row, the one mint; validate_plan
    refuses the bundle receipt path as a reserved-control-area creation, so the executable row is never a
    plan operand, the explicit stage-driver rule the self-test pins). Both files are create-only, so a run
    records exactly one genesis: both destinations are observed absent before either create is composed,
    so a second record-adoption of the same run, in this transaction or a later one, refuses on the
    occupied path with neither file composed. Reversal before commit is the journal's rollback, which
    removes both files. Every refusal is CANNOT-EVALUATE and precedes any filesystem publication; one
    raised inside ApplyOps.create (a path component that is not a directory) can leave context["ops"]
    partly composed, so the compose callable MUST raise on any refusing verdict, discarding the whole
    transaction before it opens (run_adopt_transaction then writes nothing)."""
    try:
        ops = _composing_ops(op_row, context)
        if op_row["receipt_path"] != receipt_rel(ops.run_id):
            raise AdoptApplyError("receipt_path {!r} is not this run's bundle receipt {!r}".format(
                op_row["receipt_path"], receipt_rel(ops.run_id)))
        core, event = adoption_record(ops.run_id, _context_plan(context), context.get("receipt"),
                                      context.get("now"))
        if "sha256:" + _sha256(core) != op_row["receipt_core_digest"]:
            raise AdoptApplyError("the row's receipt_core_digest is not the digest of the emitted core")
        dests = (receipt_rel(ops.run_id), genesis_event_rel(ops.run_id))
        for rel in dests:
            if rel in ops.staged:
                raise AdoptApplyError("{!r} is already composed in this transaction: one run records "
                                      "exactly one genesis".format(rel))
        occupied = [rel for rel in dests if observe_live(ops.root_fd, rel)["kind"] != "absent"]
        if occupied:
            raise AdoptApplyError("{} already occupied: one run records exactly one genesis, so neither "
                                  "the receipt nor its genesis event is composed".format(
                                      ", ".join(repr(rel) for rel in occupied)))
        ops.create(dests[0], core)
        ops.create(dests[1], event)
    except AdoptApplyError as exc:
        return schema._cannot("record-adoption refused: {}".format(exc))
    return schema._ok()


# --- the dispatch table: every op but the landed ops (the three file ops, init-store, the registration ops, enable-hook and the three finish ops) refuses not-yet-executable --

def _not_yet_executable(op_row, context=None):
    """The refusing not-yet-executable verdict behind every dispatch entry no slice has landed yet. Each
    landing slice replaces its OP_HANDLERS entry with a real executor; the self-test pins every OTHER entry
    to THIS handler and every such canonical row to a refusing status, so a silently-enabled op is a red."""
    name = op_row.get("op") if isinstance(op_row, dict) else None
    return schema.AdoptValidation(store.CANNOT_EVALUATE, [
        "op {!r} is not yet executable in this build; a later adoption slice lands it "
        "(fail-closed)".format(name)])


class OpContext:
    """What an executable handler composes against: the transaction's ApplyOps (its run, its phase and the
    live tree beneath its root descriptor), the plan's source rows keyed by path (a retire or move row's
    disposition and occupancy are plan facts, spec 14.1, never inferred from the tree), and the planned
    bytes of each create-file path, which the handler digest-checks against the row before staging them. A
    path two source rows claim is ambiguous and binds nothing, and a row whose path is not a string binds
    nothing either. `claimed` holds every create-file path and move destination the rows have named
    (canonically case-folded, _claim_key, to the path as named), so two rows can never land on one path, on
    case or Unicode-normalization variants of one path, or on a file and its own ancestor or descendant
    (the live tree alone cannot see a path a row composed earlier in this transaction, or one a later stage
    creates). The fold is Unicode canonical caseless matching, not every filesystem's case table (as
    disclosed for _opf_adopt.protected_destination), and it refuses, fail-closed, two names a
    case-sensitive filesystem would keep apart (`stra\u00dfe` and `strasse`)."""

    def __init__(self, ops, sources=(), content=None):
        self.ops = ops
        self.sources = {}
        for row in sources:
            path = row.get("path") if isinstance(row, dict) else None
            if isinstance(path, str):
                self.sources[path] = None if path in self.sources else row
        self.content = dict(content or {})
        self.claimed = {}

    def claim(self, path):
        """Claim one created path for this transaction's rows. A path equal to one already claimed, a case
        or normalization variant of it (one name on a case-insensitive or normalization-insensitive
        filesystem), or its ancestor or descendant (one row needs as a file a name the other needs as a
        directory) refuses before the base commits work that no retirement stage could finish."""
        folded = _claim_key(path)
        for prior_folded, prior in self.claimed.items():
            if _within(folded, prior_folded) or _within(prior_folded, folded):
                raise AdoptApplyError("{!r} and {!r} are named by two plan rows (create-file paths or move "
                                      "destinations) as one path, case or normalization variants, or a "
                                      "file and its own ancestor or descendant; a collision routes to a "
                                      "disposition, never an overwrite (fail-closed)".format(prior, path))
        self.claimed[folded] = path


def _claim_key(path):
    """Unicode canonical caseless form (D145): NFD, casefold, NFD again, so an NFC and an NFD spelling of
    one name, and its case variants, fold to one key."""
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", path).casefold())


def _stage(ops):
    """The stage a transaction executes file ops in: its base transaction is the apply stage and its
    RETIREMENT_PHASE transaction the retirement stage (spec 14.1); any other phase runs no file op."""
    if ops.phase is None:
        return "apply"
    if ops.phase == RETIREMENT_PHASE:
        return "retirement"
    raise AdoptApplyError("phase {!r} runs no file op: a file op composes only in the run's base "
                          "transaction (apply) or its {!r} transaction (fail-closed)".format(
                              ops.phase, RETIREMENT_PHASE))


def _bound_occupying(context, path, disposition, digest, destination=None):
    """Whether the ONE plan source row binding `path` with this disposition and digest occupies a managed
    destination; no such row (absent, ambiguous, or disagreeing with the op row) refuses, as does a row
    whose preservation is not the one this op lands (a non-occupying move's destination, else the run's
    archive copy; the planner's own pairing, spec 14.1). A source that is protected or lies in the store
    control area is never removable by this shell (check_apply_ops), so it refuses here, in the apply
    stage, before a base commits that a retirement stage could never finish."""
    row = context.sources.get(path)
    if not (isinstance(row, dict) and row.get("disposition") == disposition and row.get("digest") == digest
            and schema._is_bool(row.get("occupying"))):
        raise AdoptApplyError("no single plan source row binds {!r} as a {!r} source at this digest; its "
                              "occupancy is a plan fact, never inferred (fail-closed)".format(path, disposition))
    run_id = context.ops.run_id
    kept_at = destination if disposition == "move" and not row["occupying"] else archive_rel(run_id, path)
    if row.get("preservation") != kept_at:
        raise AdoptApplyError("the plan source row for {!r} records preservation {!r}, not {!r}, where this "
                              "op preserves it (fail-closed)".format(path, row.get("preservation"), kept_at))
    if schema._in_control_area(path) or schema.protected_destination(path, run_id) is not None or any(
            _within(path, home) for home in _CONTROL_HOMES):
        raise AdoptApplyError("{} source {!r} names the store control area or a protected path, which "
                              "this shell never removes (fail-closed)".format(disposition, path))
    return row["occupying"]


def _compose_create_file(op_row, context):
    """create-file, apply stage only: the planned bytes, digest-checked against content_digest, created at
    an absent path (ApplyOps.create re-observes it; a collision is never an overwrite). The store control
    area, this run's own archive and bundle included, is the engine's to write, never a plan row's: a row
    there could plant a forged preimage or an unlisted evidence payload. Reversal is the journal's
    pre-commit rollback, which removes the created file."""
    ops, path = context.ops, op_row["path"]
    if _stage(ops) != "apply":
        raise AdoptApplyError("create-file {!r} runs only in the apply stage (the run's base "
                              "transaction); nothing composed (fail-closed)".format(path))
    if schema._in_control_area(path) or schema.protected_destination(path, ops.run_id) is not None:
        raise AdoptApplyError("create-file {!r} names the store control area or a protected destination, "
                              "which only the engine's own preservation and inventory writes may use "
                              "(fail-closed)".format(path))
    data = context.content.get(path)
    if not isinstance(data, bytes) or "sha256:" + _sha256(data) != op_row["content_digest"]:
        raise AdoptApplyError("the planned bytes for create-file {!r} are missing or do not match its "
                              "content_digest (fail-closed)".format(path))
    context.claim(path)
    ops.create(path, data)


def _compose_retire_file(op_row, context):
    """retire-file. Apply stage: an occupying source is archived and removed preserve-first, a
    non-occupying one takes its retirement preimage and stays frozen in place. Retirement stage: a frozen
    source is removed once its committed preimage re-verifies; an occupying source, already out of the
    live tree, only has its archived bytes re-verified (recording its retirement is the receipt's, a later
    slice). Drift refuses in both stages. Reversal before commit is the journal's rollback, which restores
    a removed source from its digest-checked preimage."""
    ops, path, digest = context.ops, op_row["path"], op_row["preimage_digest"]
    occupying = _bound_occupying(context, path, "retire", digest)
    if _stage(ops) == "apply":
        if occupying:
            ops.archive_occupying(path, digest)
        else:
            ops.preserve(path, digest)
    elif occupying:
        ops.committed_copy(path, digest)
    else:
        ops.retire(path, digest)


def _compose_move_file(op_row, context):
    """move-file. In both stages the destination must be unprotected and, inside the store tree, strictly
    beneath the Move root (the planner's rule, spec 14.2; protected_destination alone exempts this run's
    own archive and bundle, where a move would plant a forged preimage or evidence payload). Apply stage:
    the destination must be absent; an occupying source is archived and removed preserve-first, a
    non-occupying one is only drift-checked and stays frozen. Retirement stage (spec 14.2: the relocation
    runs only after the green check): ApplyOps.relocate. The default destination
    `.working/archive/moved/<source-path>` is the planner's (store.moved_dest). Reversal before commit is
    the journal's rollback: the destination create is undone and a removed source restored."""
    ops = context.ops
    source, destination, digest = op_row["source"], op_row["destination"], op_row["source_digest"]
    occupying = _bound_occupying(context, source, "move", digest, destination)
    protected = schema.protected_destination(destination, ops.run_id)
    # the store-tree name compares case-insensitively, as protected_destination's names do, so a case
    # variant (`.Working/x`) that a case-insensitive filesystem lands inside the store is refused too.
    if (protected is None and _within(destination.casefold(), store.WORKING_DIRNAME)
            and not destination.startswith(_MOVED_ROOT + "/")):
        protected = "{!r} lies inside the store tree but not beneath {}/ (spec 14.2)".format(
            destination, _MOVED_ROOT)
    if protected is not None:
        raise AdoptApplyError("move-file destination is protected: {} (fail-closed)".format(protected))
    context.claim(destination)
    if _stage(ops) == "apply":
        ops.require_absent(destination)
        if occupying:
            ops.archive_occupying(source, digest)
        else:
            ops.frozen(source, digest)
    else:
        ops.relocate(source, destination, digest, occupying)


_FILE_OPS = {"create-file": _compose_create_file, "move-file": _compose_move_file,
             "retire-file": _compose_retire_file}
# The ops this build executes; the self-test pins the table to exactly this set.
EXECUTABLE_OPS = frozenset(_FILE_OPS)


def _execute_file_op(op_row, context=None):
    """The one slice-2 handler behind the three file ops: compose this row's journal ops into the
    transaction its OpContext carries and report VALID; any refusal (drift, a collision, a wrong stage, an
    unbound source, unplanned bytes) is CANNOT-EVALUATE naming the reason. Every refusal is raised before
    the row appends an op. Nothing is written here: the composed ops run only inside
    run_adopt_transaction, after check_apply_ops re-proves the whole list."""
    if not isinstance(context, OpContext) or not isinstance(context.ops, ApplyOps):
        return schema._cannot("op {!r} composes only inside an adoption transaction, through its "
                              "OpContext; nothing composed (fail-closed)".format(op_row.get("op")))
    try:
        _FILE_OPS[op_row["op"]](op_row, context)
    except AdoptApplyError as exc:
        return schema._cannot(str(exc))
    return schema._ok()


# Keyed by the closed ADOPT_OPS vocabulary; the self-test reconciles this table against
# ADOPT_OPS_BY_NAME in BOTH directions so it can neither drop nor invent an op.
OP_HANDLERS = {
    "install-pack": _not_yet_executable,
    "init-store": _init_store,
    "create-file": _execute_file_op,
    "plant-governance": _plant_governance,
    "register-unmanaged": _execute_registration_op,
    "move-file": _execute_file_op,
    "repoint-consumer": _execute_registration_op,
    "retire-file": _execute_file_op,
    "enable-hook": _execute_enable_hook,
    "render-views": _render_views,
    "record-adoption": _record_adoption,
}


def compose_rows(rows, sources=(), content=None):
    """The compose callable run_adopt_transaction takes for a list of plan op rows: dispatch each row, in
    order, into the transaction through one OpContext, and refuse the whole transaction (AdoptApplyError,
    nothing written) on the first refusing verdict, an op whose slice has not landed included."""
    rows = list(rows)

    def compose(ops):
        context = OpContext(ops, sources, content)
        for i, row in enumerate(rows):
            verdict = dispatch(row, context)
            if verdict.status != store.VALID:
                raise AdoptApplyError("plan op[{}] ({!r}) refused: {}".format(
                    i, row.get("op") if isinstance(row, dict) else None, "; ".join(verdict.findings)))
    return compose


def dispatch(op_row, context=None):
    """Validate, then dispatch ONE plan op row. A malformed row propagates the validator's refusing
    verdict; an op with no registered handler (dispatch-roster drift) is CANNOT-EVALUATE, never a skip.
    Only init-store executes (given its context); the file-op handlers, the registration handlers, the
    enable-hook handler and the three finish ops compose into the transaction their context carries,
    and every other handler refuses with no side effect."""
    checked = schema.validate_op(op_row)
    if checked.status != store.VALID:
        return checked
    handler = OP_HANDLERS.get(op_row["op"])
    if handler is None:
        return schema.AdoptValidation(store.CANNOT_EVALUATE, [
            "op {!r} has no registered handler (dispatch roster drift; fail-closed)".format(op_row["op"])])
    return handler(op_row, context)


# --- the stage driver: plan, the one approval, apply (spec 14, 14.1) -----------------------------------

def plan_rel(run_id):
    """`<bundle>/plan.toml`: the approved frozen plan's exact bytes, which apply persists (spec 14.1)."""
    return evidence_home_rel(run_id) + "/" + PLAN_NAME


def approval_rel(run_id):
    """`<bundle>/approval.toml`: the captured approval's exact bytes, which apply persists beside its plan."""
    return evidence_home_rel(run_id) + "/" + APPROVAL_NAME


def require_clean_journal(product_root):
    """The stage driver's reconcile-first gate for plan, approve and apply alike, read-only: investigation
    does not read the adoption journal, so a stage over an interrupted transaction or a held lock refuses
    here (journal_clean_or_refuse) and directs the operator to reconcile()."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        raise AdoptApplyError("{} (fail-closed)".format(exc))
    root_fd = _open_product_root(product_root)
    try:
        journal_clean_or_refuse(root_fd, _journal_root(product_root))
    finally:
        store._close_fd_exc_safe(root_fd)


def _require_unapplied(product_root, run_id):
    """Replay admission (spec 14.1: one approval, one apply), read-only and BEFORE freshness, so a second
    apply of a run refuses on this rule itself, never on whatever drift its own first apply left behind: a
    run whose base transaction the adoption journal already holds, in any state, refuses.
    run_adopt_transaction re-proves the rule under the journal lock."""
    txn = _txn_name(run_id, None)
    root_fd = _open_product_root(product_root)
    try:
        prior = _journal._lstat_contained(root_fd, JOURNAL_REL + "/" + txn)
    except (_journal.JournalError, OSError) as exc:
        raise AdoptApplyError("cannot inspect the adoption journal ({}); fail-closed".format(exc))
    finally:
        store._close_fd_exc_safe(root_fd)
    if prior is not None:
        raise AdoptApplyError("run {} already has its transaction {!r}: an approved plan applies at most once, "
                              "and changing approved work takes a fresh plan with its own run id (spec 14.1); "
                              "nothing written (fail-closed)".format(run_id, txn))


def observe_revision(product_root):
    """The live product revision, OBSERVED (spec 14.1: the plan binds the observed revision, and any
    bound-item drift refuses): the commit HEAD names at `product_root`, from ONE read-only
    `git rev-parse --verify` with an explicit -C binding, replacement objects off, every ambient GIT_*
    variable scrubbed, and the global and system git configuration neutralized (GIT_CONFIG_NOSYSTEM=1,
    GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM pinned to the null device: the CONFIG PINS of the
    _opf_observe._scrubbed_env convention, and ONLY those pins, since this call keeps the rest of the
    ambient environment and sets none of that convention's other variables), so an ambient GIT_DIR or
    GIT_WORK_TREE cannot redirect
    the answer and host configuration (a system core.hooksPath, a core.fsmonitor program, or a malformed
    global config) can neither run code during the observation nor change or break it: the answer is the
    repository's alone. Disclosed, as _opf_oplock discloses for its own walk: git honors safe.directory
    only from the command line or the global and system configuration neutralized here, so a repository
    owned by another uid that the operator trusts ONLY through a global or system safe.directory is now
    REFUSED (git's own dubious-ownership refusal, surfaced in the refusal text), common where CI writes a
    global safe.directory; adopting such a product tree takes ownership of the product root, not ambient
    configuration. Investigation never enters
    .git and takes the revision from the worksheet; approve and apply observe it here. Missing git, a root
    in no repository, an unborn HEAD, a failed or timed-out query, or an answer that is not one 40- or
    64-digit object id refuses: an unreadable or unverifiable revision is never assumed fresh."""
    git = shutil.which("git")
    if git is None:
        raise AdoptApplyError("git is not on PATH, so the live product revision cannot be observed; an "
                              "unverifiable revision is never assumed fresh (spec 14.1, fail-closed)")
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    try:
        proc = subprocess.run([git, "--no-replace-objects", "-C", str(product_root), "rev-parse", "--verify",
                               "--quiet", "HEAD^{commit}"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdoptApplyError("the live product revision cannot be observed ({}); an unverifiable revision is "
                              "never assumed fresh (spec 14.1, fail-closed)".format(exc))
    answer = proc.stdout.decode("ascii", errors="replace").strip() if proc.returncode == 0 else None
    if answer is None or not schema._is_revision(answer):
        said = "".join(c if c.isprintable() else " " for c in (proc.stderr or b"").decode("utf-8", "replace"))
        said = " ".join(said.split())[:_GIT_DIAGNOSIS_CHARS]
        raise AdoptApplyError("the live product revision at {} cannot be observed (git rev-parse exit {}: no "
                              "repository, an unborn HEAD, a repository git refuses, or an unreadable answer{}); "
                              "this observation ignores the global and system git configuration, so a "
                              "safe.directory set there (as git's own hint suggests) does not apply here; an "
                              "unverifiable revision is never assumed fresh (spec 14.1, fail-closed)".format(
                                  product_root, proc.returncode, "; git said: " + said if said else ""))
    return answer


# The composition guard (spec 14.1, 14.2): while the stage driver composes, a plan op handler may only stage
# into the transaction's ApplyOps; a direct filesystem or process effect, or a transaction of its own, would
# land before the driver's checks and outside the run's one base transaction. One process-wide audit hook,
# installed on the first composition and armed per thread only inside one, refuses each such effect and
# records it, so a handler that swallows the refusal still refuses the whole composition.
_COMPOSITION = threading.local()
_COMPOSITION_HOOK = []
_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_EFFECT_EVENTS = frozenset((
    "os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.link", "os.symlink", "os.truncate", "os.chmod",
    "os.chown", "os.chflags", "os.lchflags", "os.utime", "os.mkfifo", "os.mknod", "os.setxattr",
    "os.removexattr", "os.fork", "os.forkpty", "os.exec", "os.posix_spawn", "os.spawn", "os.system",
    "os.startfile", "os.kill", "os.killpg", "subprocess.Popen", "pty.spawn", "shutil.copyfile",
    "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.chown", "shutil.move", "shutil.rmtree",
    "shutil.make_archive", "shutil.unpack_archive", "tempfile.mkstemp", "tempfile.mkdtemp",
    "_thread.start_new_thread", "_thread.start_joinable_thread"))


def _composition_audit(event, args):
    denied = getattr(_COMPOSITION, "denied", None)
    if denied is None:
        return
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if not ((isinstance(flags, int) and flags & _WRITE_OPEN_FLAGS)
                or (isinstance(mode, str) and any(c in mode for c in "wax+"))):
            return
    elif event not in _EFFECT_EVENTS:
        return
    denied.append(event)
    raise AdoptApplyError("a plan op handler attempted a direct effect ({}) while the stage driver composes; "
                          "a handler only stages into the transaction's ApplyOps, so nothing lands before the "
                          "driver's checks or outside the run's one base transaction (fail-closed)".format(event))


@contextlib.contextmanager
def _composing():
    """Arm the composition guard on this thread for one composition; a composition already armed here (a
    handler re-entering the driver) refuses. After the body, any recorded effect refuses the composition even
    where a handler caught the guard's own refusal. Starting a thread is an effect (the guard arms per
    thread). Disclosed: an audit hook is not a sandbox; a write through an already-open writable descriptor,
    ctypes, a thread already running before composition, or an entry point CPython does not audit stays
    outside it."""
    if getattr(_COMPOSITION, "denied", None) is not None:
        raise AdoptApplyError("a composition is already in progress on this thread; a plan op handler may not "
                              "re-enter the stage driver (fail-closed)")
    if not _COMPOSITION_HOOK:
        sys.addaudithook(_composition_audit)
        _COMPOSITION_HOOK.append(_composition_audit)
    denied = []
    _COMPOSITION.denied = denied
    try:
        yield
    finally:
        _COMPOSITION.denied = None
    if denied:
        raise AdoptApplyError("a plan op handler attempted direct effect(s) {} while the stage driver composed "
                              "and continued past the refusal; the composition refuses (fail-closed)".format(
                                  ", ".join(denied)))


def _receipt_not_yet_composable(context=None):
    """The mandatory receipt stage's slice-1 composer: a refusing not-yet-executable verdict, so apply
    refuses before anything is written until the record-adoption slice replaces DRIVER_STAGES[RECEIPT_STAGE]."""
    return schema.AdoptValidation(store.CANNOT_EVALUATE, [
        "the adoption receipt and its outcome-event chain are not yet composable in this build; a later "
        "adoption slice lands the receipt stage (fail-closed)"])


# The driver's mandatory stages (spec 14: apply ends with an adoption receipt plus its outcome-event chain).
# No plan can carry that receipt, since its core binds the approval recorded after the plan freezes
# (_opf_adopt_plan), so the obligation is the driver's: composed after every plan row, inside the run's one
# base transaction, whatever rows the plan carries. A record-adoption row a plan does carry dispatches like
# any other row; the receipt slice reconciles that row with this stage.
DRIVER_STAGES = {RECEIPT_STAGE: _receipt_not_yet_composable}


def _canonical_toml(data, label):
    """Parse `data` as TOML that is byte-identical to its own emit_checked rendering, or refuse: a frozen
    artefact admits no second spelling (a comment, a reordering or a whitespace change is a hand edit)."""
    if not isinstance(data, bytes):
        raise AdoptApplyError("{} must be bytes".format(label))
    try:
        doc = tomllib.loads(data.decode("utf-8"))
        canonical = emit_checked(doc).encode("utf-8")
    except (ValueError, RecursionError, EmitError) as exc:   # UnicodeDecodeError, TOMLDecodeError included
        raise AdoptApplyError("{} is unreadable or malformed TOML ({}); fail-closed".format(label, exc))
    if canonical != data:
        raise AdoptApplyError("{} is not in its canonical emitted form (a hand edit, comment or reordering); "
                              "only the exact frozen bytes are admitted (fail-closed)".format(label))
    return doc


def frozen_plan(plan_bytes):
    """Re-prove one frozen plan from its own bytes (spec 14.1): canonical TOML, the `opf.adoption.plan/v2`
    format (any other is never apply input), VALID under the schema validator, and a plan_digest that
    re-seals, the planner's own seal: the digest of the canonical emission of the plan without its
    plan_digest. Returns the parsed plan; raises AdoptApplyError."""
    doc = _canonical_toml(plan_bytes, "the adoption plan")
    if doc.get("format") != PLAN_V2_FORMAT:
        raise AdoptApplyError("apply takes only an approved {} plan (spec 14.1); {!r} binds none of the "
                              "v2 roster, so it is never apply input (fail-closed)".format(
                                  PLAN_V2_FORMAT, doc.get("format")))
    checked = schema.validate_plan(doc)
    if checked.status != store.VALID:
        raise AdoptApplyError("the adoption plan is not a VALID {} plan: {} (fail-closed)".format(
            PLAN_V2_FORMAT, "; ".join(checked.findings)))
    body = dict(doc)
    claimed = body.pop("plan_digest")
    try:
        sealed = "sha256:" + _sha256(emit_checked(body).encode("utf-8"))
    except EmitError as exc:
        raise AdoptApplyError("the adoption plan cannot be re-sealed ({}); fail-closed".format(exc))
    if sealed != claimed:
        raise AdoptApplyError("the adoption plan's plan_digest does not seal its own bytes (an edited plan); a "
                              "fresh plan with its own approval is the remedy (spec 14.1, fail-closed)")
    return doc


def _run_instant(run_id):
    """The aware UTC instant an adoption run id's stamp names (the plan's own freezing instant)."""
    if not isinstance(run_id, str):
        raise AdoptApplyError("plan run id {!r} is not a string; fail-closed".format(run_id))
    stamp = run_id[len("adopt-"):len("adopt-") + len("YYYYMMDDTHHMMSSZ")]
    try:
        return datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError as exc:
        raise AdoptApplyError("plan run id {!r} names no calendar instant ({}); fail-closed".format(run_id, exc))


def approval_findings(approval, plan_doc):
    """The findings against one captured approval for one plan, empty when it binds (spec 14.1): the closed
    APPROVAL_REQUIRED keyset (the receipt's own approval shape), a token actor, an RFC 3339 UTC approved_at
    no earlier than the instant the plan froze (its run id's stamp; the approval follows its plan), and a plan_digest AND an
    inventory_digest equal to the plan's own, hence that whole plan."""
    findings = []
    if schema._validate_subtable(approval, schema.APPROVAL_REQUIRED, "approval", findings) is None or findings:
        return findings
    if not schema._is_token(approval["actor"]):
        findings.append("approval actor is not a non-empty single-line token")
    if not schema._is_timestamp(approval["approved_at"]):
        findings.append("approval approved_at is not an RFC 3339 UTC instant")
    else:
        try:
            approved = datetime.datetime.fromisoformat(approval["approved_at"])
            planned = _run_instant(plan_doc.get("run_id"))
            follows = approved >= planned
        except (AdoptApplyError, TypeError, ValueError) as exc:
            findings.append("approval approved_at cannot be ordered after the plan's run instant ({})".format(exc))
        else:
            if not follows:
                findings.append("approval approved_at {!r} precedes the instant its plan froze ({}, its run id's "
                                "stamp): the one approval MUST follow its concrete plan (spec 14.1)".format(
                                    approval["approved_at"], planned.strftime("%Y-%m-%dT%H:%M:%SZ")))
    for key in ("plan_digest", "inventory_digest"):
        if approval[key] != plan_doc.get(key):
            findings.append("approval {0} {1!r} does not bind this plan's {0} {2!r}; an approval binds exactly "
                            "one plan, and a changed plan takes its own single approval (spec 14.1)".format(
                                key, approval[key], plan_doc.get(key)))
    return findings


def apply_plan(plan_doc, approval=None):
    """The apply-input gate, pure and write-free (spec 14.1): only an `opf.adoption.plan/v2` plan the schema
    validator grades VALID, with a captured approval binding its plan_digest and inventory_digest, is apply
    input. Any other format, the shipped v1 schema included, is refused on the marker before any other field
    is read; an invalid v2 plan propagates the validator's verdict; a v2 plan with no approval, or whose
    approval binds another plan, is refused. VALID admits the pair to the stage driver (run_apply) and is
    never by itself a write."""
    if not isinstance(plan_doc, dict):
        return schema._cannot("adoption plan is not a table")
    if plan_doc.get("format") != PLAN_V2_FORMAT:
        return schema._cannot("apply takes only an approved {} plan (spec 14.1); {!r} binds none of the "
                              "v2 roster, so it is never apply input (fail-closed)".format(
                                  PLAN_V2_FORMAT, plan_doc.get("format")))
    checked = schema.validate_plan(plan_doc)
    if checked.status != store.VALID:
        return checked
    if approval is None:
        return schema._cannot("apply takes an APPROVED plan, and no captured approval binds this one (spec "
                              "14.1); apply refuses (fail-closed)")
    findings = approval_findings(approval, plan_doc)
    if findings:
        return schema.AdoptValidation(store.CANNOT_EVALUATE, findings)
    return schema._ok()


def rederive_or_refuse(product_root, plan_doc, plan_bytes, worksheet):
    """Bound-item freshness (spec 14.1: any bound-item drift refuses into a fresh plan with its own single
    approval), read-only as the planner is: re-run the planner over the LIVE tree with the worksheet that
    froze the plan and the plan's own run instant and nonce, and require the byte-identical plan; but first
    the live product revision is OBSERVED (observe_revision) and must equal the plan's bound revision, so a
    revision-only change (an empty commit advancing HEAD) refuses. A changed observation (the sources,
    targets, store identity and ancestry the planner observes) refuses on the planner's own stale-inventory
    binding, and a change to a worksheet input (a decision, an op, a binding) freezes a different plan and
    refuses. Disclosed: the release, prompt_pack, enforcement and skip_policy bindings are worksheet-asserted,
    not observed here, so re-derivation proves only that the worksheet still freezes this plan, never that
    the tool release, prompt pack or enforcement pack in use match it; observing them is the trust-verification
    slice's. `worksheet` is the parsed planning worksheet: sources, targets, expected_observation_digest,
    product, decisions, ops, bindings."""
    import _opf_adopt_plan as planner
    observed = observe_revision(product_root)
    if observed != plan_doc["revision"]:
        raise AdoptApplyError("the product revision moved: the plan binds {} and the live HEAD is {}; any "
                              "bound-item drift refuses into a fresh plan with its own single approval (spec "
                              "14.1, fail-closed)".format(plan_doc["revision"], observed))
    run_id = plan_doc["run_id"]
    now = _run_instant(run_id)
    try:
        sheet = copy.deepcopy(worksheet)
        res = planner.plan(product_root, sources=sheet["sources"], targets=sheet["targets"],
                           expected_observation_digest=sheet["expected_observation_digest"],
                           product=sheet["product"], decisions=sheet["decisions"], ops=sheet["ops"],
                           now=now, run_nonce=run_id[-16:], bindings=sheet["bindings"])
    except (KeyError, TypeError) as exc:
        raise AdoptApplyError("the planning worksheet cannot re-derive the plan ({!r}); fail-closed".format(exc))
    if res.status != store.VALID:
        reasons = list(res.findings) + ["unresolved source disposition: " + u for u in res.unresolved]
        raise AdoptApplyError("the live tree no longer freezes this plan ({}); any bound-item drift refuses "
                              "into a fresh plan with its own single approval (spec 14.1, fail-closed)".format(
                                  "; ".join(reasons)))
    if res.plan != plan_bytes:
        raise AdoptApplyError("re-planning from the worksheet over the live tree freezes a different plan than "
                              "{}: a bound item drifted, so a fresh plan with its own single approval is the "
                              "remedy (spec 14.1, fail-closed)".format(plan_doc["plan_digest"]))


def capture_approval(product_root, plan_bytes, worksheet, actor, now):
    """The approve stage, the one approval (spec 14.1), write-free: re-prove the frozen plan from its bytes,
    refuse over a non-clean adoption journal or a run already applied, re-derive the plan over the live
    tree and its observed revision (rederive_or_refuse, so a moved revision, a stale observation or a changed
    worksheet refuses into a fresh plan), refuse an approval instant before the plan froze, then
    return the canonical
    approval bytes, in the receipt's APPROVAL_REQUIRED shape, binding the plan's plan_digest and
    inventory_digest. `now` is the clock instant of the approval. The adopter holds those bytes until apply,
    which persists them with the plan in the run's evidence bundle; nothing is written here."""
    plan_doc = frozen_plan(plan_bytes)
    require_clean_journal(product_root)
    _require_unapplied(product_root, plan_doc["run_id"])
    rederive_or_refuse(product_root, plan_doc, plan_bytes, worksheet)
    if (type(now) is not datetime.datetime or type(now.tzinfo) is not datetime.timezone
            or now.utcoffset() != datetime.timedelta(0)):
        raise AdoptApplyError("now must be a clock-derived aware UTC datetime")
    approval = dict(actor=actor, approved_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    plan_digest=plan_doc["plan_digest"], inventory_digest=plan_doc["inventory_digest"])
    findings = approval_findings(approval, plan_doc)
    if findings:
        raise AdoptApplyError("the approval cannot be captured: {} (fail-closed)".format("; ".join(findings)))
    try:
        return emit_checked(approval).encode("utf-8")
    except EmitError as exc:
        raise AdoptApplyError("the approval cannot be emitted canonically ({}); fail-closed".format(exc))


def run_apply(product_root, plan_bytes, approval_bytes, worksheet):
    """The apply stage (spec 14, 14.1, 14.2). Before anything is written, in order: re-prove the frozen
    plan (frozen_plan) and the captured approval from their own canonical bytes; admit the pair through
    apply_plan (the approval binds this plan's plan_digest and inventory_digest and follows its plan);
    refuse over a non-clean adoption journal; refuse a run already applied (_require_unapplied, replay
    admission ahead of freshness); re-derive the plan over the live tree and its observed revision
    (rederive_or_refuse); and refuse while any plan op's slice, or the driver's mandatory receipt stage
    (DRIVER_STAGES), has not landed, so an unlanded step refuses the whole apply fail-closed by
    construction, never half of it.

    Composition is the driver's, and so are its rules. One compose function stages the plan and approval
    bytes in the run's evidence bundle, dispatches EVERY plan op in plan order in the apply stage, each with
    one context table (`ops`, the transaction's ApplyOps; `plan`; `approval`; `product_root`; `stage`), then
    composes the mandatory receipt stage whatever rows the plan carries; the first refusing verdict refuses
    the whole composition. Handlers run under the composition guard (_composing): a handler only stages into
    `ops`, so a direct filesystem or process effect, or a transaction of its own, refuses the composition (a
    slice that would delegate to its own journaled operation, init-store's substrate included, composes into
    `ops` instead). Retirement partition (spec 14.1, 14.2): a plan source that occupies no managed
    destination stays frozen, byte-identical in place, until the retirement stage after a green completion
    check, so the apply stage may take only its preimage: a composed remove or write of any non-occupying
    plan source refuses. The compose function first runs as a write-free preflight against the live tree,
    BEFORE the journal is prepared, so a handler, receipt, partition or invariant refusal writes nothing at
    all; then ONE base transaction (run_adopt_transaction) composes it again under the journal lock, every
    check re-proved there, and commits it. A refusal of that second composition (a stateful handler, or the
    tree drifting between the passes) releases the lock and removes the journal directories the run
    created, so it too leaves the tree as it found it, unless a release or a removal fails or may not be
    durable (a directory a concurrent run has populated meanwhile stays, disclosed): run_adopt_transaction
    then names each such leftover in place of "nothing written".
    Returns the transaction name."""
    plan_doc = frozen_plan(plan_bytes)
    approval = _canonical_toml(approval_bytes, "the adoption approval")
    gate = apply_plan(plan_doc, approval)
    if gate.status != store.VALID:
        raise AdoptApplyError("the approval does not admit this plan: {} (fail-closed)".format(
            "; ".join(gate.findings)))
    run_id = plan_doc["run_id"]
    require_clean_journal(product_root)
    _require_unapplied(product_root, run_id)
    rederive_or_refuse(product_root, plan_doc, plan_bytes, worksheet)
    rows = list(plan_doc["ops"])
    unlanded = []
    ops_unlanded = sorted(set(row["op"] for row in rows
                              if OP_HANDLERS.get(row["op"], _not_yet_executable) is _not_yet_executable))
    if ops_unlanded:
        unlanded.append("plan op(s) " + ", ".join(ops_unlanded))
    if DRIVER_STAGES.get(RECEIPT_STAGE, _receipt_not_yet_composable) is _receipt_not_yet_composable:
        unlanded.append("the mandatory {} stage".format(RECEIPT_STAGE))
    if unlanded:
        raise AdoptApplyError("{} not yet executable in this build; a later adoption slice lands each, and apply "
                              "refuses before anything is written (fail-closed)".format(" and ".join(unlanded)))
    frozen = set(row["path"] for row in plan_doc["sources"] if not row["occupying"])

    def compose(ops):
        with _composing():
            ops.create(plan_rel(run_id), plan_bytes)
            ops.create(approval_rel(run_id), approval_bytes)
            # KNOWN GAP, unreachable in this build: the finish ops read context keys this driver does not
            # supply yet: plant-governance reads `pack` (the release manifest, root.txt and members it
            # verifies), render-views reads `observations` (the inert git observations it renders from),
            # and record-adoption reads `receipt` and `now`. The receipt-stage gate above refuses every plan
            # before this compose runs, so none of them is reached. The slice that lands the receipt stage
            # must also gather and pass those four keys here; until then (read from the code, not run) an
            # absent pack or receipt refuses, and absent observations leave the store checks that need them
            # cannot-evaluate. Likewise the file-op handlers compose only through an OpContext (the plan's
            # source rows and each create-file path's planned bytes), which this table is not, so a file-op
            # row dispatched here refuses (fail-closed) until that slice binds the two.
            context = dict(ops=ops, plan=plan_doc, approval=approval, product_root=product_root,
                           stage=APPLY_STAGE)
            for i, row in enumerate(rows):
                verdict = dispatch(row, context)
                if verdict.status != store.VALID:
                    raise AdoptApplyError("plan op[{}] ({!r}) refused: {}".format(
                        i, row["op"], "; ".join(verdict.findings)))
            verdict = DRIVER_STAGES[RECEIPT_STAGE](context)
            if verdict.status != store.VALID:
                raise AdoptApplyError("the mandatory {} stage refused: {}".format(
                    RECEIPT_STAGE, "; ".join(verdict.findings)))
        touched = sorted(set(op["path"] for op in ops.ops
                             if op.get("op") in ("remove", "write") and op.get("path") in frozen))
        if touched:
            raise AdoptApplyError("the apply stage would remove or rewrite non-occupying plan source(s) {}; "
                                  "each stays frozen, byte-identical in place, until the retirement stage after "
                                  "a green completion check (spec 14.1, 14.2), and apply takes only its "
                                  "preimage (fail-closed)".format(", ".join(touched)))

    root_fd = _open_product_root(product_root)
    try:
        _compose_checked(root_fd, run_id, None, compose)
    finally:
        store._close_fd_exc_safe(root_fd)
    return run_adopt_transaction(product_root, run_id, compose)


def _selftest_git_commit(root):
    """Self-test fixtures only: make `root` a git repository when it is not one, then advance its HEAD by
    one EMPTY commit through plumbing (mktree, commit-tree, update-ref; no template or signing, a pinned
    identity and date), so only the revision moves. The global and system git configuration is neutralized
    (GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM pinned to the null device, matching
    _opf_observe._scrubbed_env): only so pinned is "no hook" true -- a system core.hooksPath would
    otherwise run a reference-transaction hook at init and update-ref. Returns the new HEAD, or None when
    git is unavailable or any step fails (the caller records that as a failed check)."""
    git = shutil.which("git")
    if git is None:
        return None
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    env.update(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
    where = str(root)
    try:
        if not os.path.isdir(os.path.join(where, ".git")):
            subprocess.run([git, "-C", where, "-c", "init.templateDir=", "init", "-q"],
                           stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60, check=True)
        tree = subprocess.run([git, "-C", where, "mktree"], input=b"", capture_output=True, env=env,
                              timeout=60, check=True).stdout.decode("ascii").strip()
        head = subprocess.run([git, "-C", where, "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                              stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60)
        if head.returncode == 0:
            made = subprocess.run([git, "-C", where, "commit-tree", "--no-gpg-sign", "-p",
                                   head.stdout.decode("ascii").strip(), "-m", "fixture", tree],
                                  stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60,
                                  check=True)
        else:
            made = subprocess.run([git, "-C", where, "commit-tree", "--no-gpg-sign", "-m", "fixture", tree],
                                  stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60,
                                  check=True)
        commit = made.stdout.decode("ascii").strip()
        subprocess.run([git, "-C", where, "update-ref", "HEAD", commit], stdin=subprocess.DEVNULL,
                       capture_output=True, env=env, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    return commit


def _selftest_git_set_head(root, commit):
    """Self-test fixtures only: point `root`'s HEAD back at `commit` (update-ref), with the global and
    system git configuration neutralized exactly as _selftest_git_commit pins it (update-ref too runs a
    reference-transaction hook under a system core.hooksPath); True when it did."""
    git = shutil.which("git")
    if git is None or not isinstance(commit, str):
        return False
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    try:
        proc = subprocess.run([git, "-C", str(root), "update-ref", "HEAD", commit], stdin=subprocess.DEVNULL,
                              capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# --- self-test -----------------------------------------------------------------------------------------

def self_test():
    """Fail-closed invariants over synthetic vectors and throwaway temporary fixtures, judged on returned
    statuses, byte comparisons and journal states; each check asserting a refusal of the executable shell
    or of apply input also matches one reason keyword so the refusal is attributed to the rule under test
    (validator and dispatch gradings are asserted on their returned status, with a named finding matched
    where that finding is itself the contract). No network; every write lands under its own
    TemporaryDirectory. The driver vectors DO run git fixture subprocesses (_selftest_git_commit,
    _selftest_git_set_head) and the production observation (observe_revision): each pins
    GIT_CONFIG_NOSYSTEM/GIT_CONFIG_GLOBAL/GIT_CONFIG_SYSTEM itself, and the whole run executes under a
    throwaway HOME and XDG_CONFIG_HOME (test-hermeticity, the _opf_observe.self_test convention), so no
    host git configuration -- hooks, fsmonitor, ignore or attributes files -- is ever read or run. The
    init-store vectors use git too (real fixture repositories, which the substrate binds) and a subprocess
    (a child dispatch of THIS engine, SIGKILLed inside the substrate's publication), under that same
    throwaway HOME, as are the file-ops crash matrix's --selftest-child interpreters of this module,
    which inherit that environment."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-ADOPT-APPLY SELF-TEST: containment unavailable ({}); cannot evaluate".format(exc),
              file=sys.stderr)
        return 2
    import tempfile
    from unittest import mock
    try:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-home-") as home:
            with mock.patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1"):
                return _self_test_checks()
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-ADOPT-APPLY SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2


def _self_test_checks():
    import signal
    import tempfile
    from unittest import mock

    failures = []
    failure_details = {}
    checked = [0]
    skipped = []    # (check, why): a check this platform cannot evaluate, named, never silently dropped
    # The descriptor census the inode-reuse, handoff and NFS vectors use is /proc/self/fd; a platform
    # without it (macOS) skips exactly those vectors, each named with this note, never a partial run.
    census = Path("/proc/self/fd").is_dir()
    no_census = "no /proc/self/fd descriptor census on this platform"

    def descriptors():
        """The descriptors open now, by the census: compared at the end so this run leaves none behind for
        the modules a self-test run loads after it."""
        found = set()
        for fd_name in os.listdir("/proc/self/fd"):
            try:
                os.fstat(int(fd_name))
            except OSError:
                continue
            found.add(int(fd_name))
        return found
    entry_descriptors = descriptors() if census else None

    def check(name, cond, observed=None):
        checked[0] += 1
        if not cond:
            failures.append(name)
            if observed is not None:
                failure_details[name] = observed

    def refusal(fn, *args, **kwargs):
        """The refusal text when fn refuses with AdoptApplyError, else None."""
        return attempt(fn, *args, **kwargs)[1]

    def attempt(fn, *args, **kwargs):
        """(result, None) on success, (None, refusal text) on AdoptApplyError, so a refused step is a
        recorded check failure rather than an escape that would mask the checks after it."""
        try:
            return fn(*args, **kwargs), None
        except AdoptApplyError as exc:
            return None, str(exc)

    _emit_inventory = globals()["emit_inventory"]
    _run_adopt_transaction = globals()["run_adopt_transaction"]

    def fixture_plan(run_id_):
        """(bytes, digest): the run's fixture plan, sealed as the planner seals it (its plan_digest is the
        digest of its canonical emission without that field), so the bundle verifier proves it exactly as
        it proves a real plan (spec 4.2)."""
        body = dict(format="opf.adoption.plan/v2", run_id=run_id_)
        digest = "sha256:" + _sha256(emit_checked(body).encode("utf-8"))
        return emit_checked(dict(body, plan_digest=digest)).encode("utf-8"), digest

    def emit_inventory(run_id_, rows, phase=None, plan_digest=None):
        """Self-test shadow of the module emitter: every fixture inventory carries its run's fixture plan
        digest in its [adoption] identity unless a vector passes its own."""
        return _emit_inventory(run_id_, rows, phase,
                               fixture_plan(run_id_)[1] if plan_digest is None else plan_digest)

    def run_adopt_transaction(product_root, run_id_, compose, phase=None, plan_digest=None):
        """Self-test shadow of the module transaction: a base transaction that names no plan digest
        stages its run's fixture plan first, as apply stages the approved plan, and every transaction
        that names none carries that plan's digest."""
        plan_bytes_, digest = fixture_plan(run_id_)
        staged = compose
        if phase is None and plan_digest is None:
            def staged(ops):
                ops.create(plan_rel(run_id_), plan_bytes_)
                return compose(ops)
        return _run_adopt_transaction(product_root, run_id_, staged, phase,
                                      digest if plan_digest is None else plan_digest)

    def stage_fixture_plan(product_root, run_id_):
        """Write the run's fixture plan into a hand-built bundle."""
        path = Path(product_root) / plan_rel(run_id_)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fixture_plan(run_id_)[0])

    def dead_pid():
        """A pid with POSITIVE evidence of death (ProcessLookupError on signal 0), for the stale-lock
        vector. Nothing is spawned or signalled; EPERM or any ambiguity keeps searching."""
        pid = (1 << 22) - 1
        for _ in range(4096):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return pid
            except OSError:
                pass
            pid -= 1
        return None

    ZERO = "0" * 64
    _D_ZERO = "sha256:" + ZERO
    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    VALID, INVALID, CANNOT = store.VALID, store.INVALID, store.CANNOT_EVALUATE

    # landed ops (the three file ops, slice 2; init-store, slice 3; the registration ops, slice 4; the
    # three finish ops, slice 5; enable-hook, PR3 slice 2) are pinned to their handlers, every other entry
    # to the refusing handler, and every canonical row refuses (each landed op's for want of its context,
    # before anything is observed). A silently-enabled op is a red; the slice that legitimately lands an
    # op updates these pins in the same change.
    landed = dict([("init-store", _init_store), ("plant-governance", _plant_governance),
                   ("render-views", _render_views), ("record-adoption", _record_adoption),
                   ("enable-hook", _execute_enable_hook),
                   ("register-unmanaged", _execute_registration_op),
                   ("repoint-consumer", _execute_registration_op)]
                  + [(n, _execute_file_op) for n in sorted(EXECUTABLE_OPS)])
    check("handlers-cover-vocabulary", set(OP_HANDLERS) == set(schema.ADOPT_OPS_BY_NAME))
    check("handlers-all-refusing-but-the-landed-ops",
          all(h is _not_yet_executable for n, h in OP_HANDLERS.items() if n not in landed))
    check("handlers-landed-ops-pinned", all(OP_HANDLERS[n] is h for n, h in landed.items()))
    check("handler-init-store-landed", OP_HANDLERS["init-store"] is _init_store)
    check("handlers-only-landed-ops-execute",
          {n for n, h in OP_HANDLERS.items() if h is not _not_yet_executable} == set(landed)
          and OP_HANDLERS["enable-hook"] is _execute_enable_hook)
    check("handlers-only-registration-ops-execute",
          set(_REGISTRATION_OPS) == {"register-unmanaged", "repoint-consumer"}
          and all(OP_HANDLERS[n] is _execute_registration_op for n in _REGISTRATION_OPS))
    check("handlers-executable-exactly-the-file-ops",
          EXECUTABLE_OPS == {"create-file", "move-file", "retire-file"}
          and all(OP_HANDLERS[n] is _execute_file_op for n in EXECUTABLE_OPS))
    res = dispatch(schema.canonical_op("init-store"))
    check("op-init-store-refuses-without-context",
          res.status == CANNOT and any("no approved plan" in f for f in res.findings))
    for name, needle in (("plant-governance", "no ApplyOps"), ("record-adoption", "no ApplyOps"),
                         ("render-views", "no ApplyOps")):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-without-context".format(name),
              res.status == CANNOT and any(needle in f for f in res.findings))
    for name in sorted(schema.ADOPT_OP_NAMES - set(landed)):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-not-yet-executable".format(name),
              res.status == CANNOT and any("not yet executable" in f for f in res.findings))
    bare = dispatch(schema.canonical_op("enable-hook"))
    check("op-enable-hook-refuses-without-transaction-context",
          bare.status == CANNOT and any("HookContext" in f for f in bare.findings))
    for name in sorted(_REGISTRATION_OPS):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-without-transaction-context".format(name),
              res.status == CANNOT and any("RegistrationContext" in f for f in res.findings))
    for name in sorted(EXECUTABLE_OPS):
        res = dispatch(schema.canonical_op(name))
        check("op-{}-refuses-without-op-context".format(name),
              res.status == CANNOT and any("OpContext" in f for f in res.findings))
    check("dispatch-out-of-vocab-cannot-eval", dispatch(dict(op="delete-everything")).status == CANNOT)
    check("dispatch-malformed-row-invalid", dispatch(dict(op="create-file", path="a/b")).status == INVALID)

    # round 3: the module introduction must name the LIVE status surface (`opf adopt status` reads both
    # adoption homes through this module) instead of calling the module dead code. The introduction is read
    # from the source, so the check does not depend on the interpreter level.
    intro = _optlevel.source_docstring(__file__) or ""
    check("module-intro-names-the-live-status-surface",
          "dead code" not in intro and "opf adopt status" in intro
          and "the CLI verb remain" not in intro)

    # 1: run identity. The homes grammar and the schema's shipped grammar agree on every vector; the mint
    # validates its own output; the import family and traversal spellings are refused.
    rid = mint_run_id(now, "0123456789abcdef")
    _t_plan_digest = fixture_plan(rid)[1]
    other_run = mint_run_id(now, "fedcba9876543210")
    check("mint-run-id-grammar", is_run_id(rid) and rid == "adopt-20260917T120000Z-0123456789abcdef")
    check("mint-run-id-deterministic", mint_run_id(now, "0123456789abcdef") == rid)
    check("mint-naive-now-refused",
          "aware UTC" in (refusal(mint_run_id, now.replace(tzinfo=None), "0123456789abcdef") or ""))
    check("mint-bad-nonce-refused", "16 lowercase hex" in (refusal(mint_run_id, now, "XYZ") or ""))
    vectors = (rid, "imp-20260917T120000Z-0123456789abcdef", "adopt-20260917T120000Z-../escapes/xx",
               "adopt-20260917T120000Z-0123456789ABCDEF", "adopt-2026091T120000Z-0123456789abcdef", 7, None)
    check("run-id-store-and-schema-grammars-agree",
          all(is_run_id(v) == (isinstance(v, str) and bool(schema._RUN_ID_RE.match(v))) for v in vectors))
    check("run-id-import-family-rejected", not is_run_id("imp-20260917T120000Z-0123456789abcdef"))
    check("run-id-traversal-rejected", not is_run_id("adopt-20260917T120000Z-../escapes/xx"))

    # 2: every home comes from the _opf_store constructors, pinned to the spec 4.2 spellings.
    home = evidence_home_rel(rid)
    check("homes-evidence-from-store",
          home == store.evidence_run("adoption", rid) == ".working/imported/adoption/" + rid)
    check("homes-inventory-from-store",
          inventory_rel(rid) == store.evidence_inventory("adoption", rid) == home + "/inventory.toml"
          and inventory_rel(rid, "completion") == home + "/inventory-completion.toml")
    check("homes-archive-from-store",
          archive_rel(rid, "a/b.md") == store.retire_preimage(rid, "a/b.md")
          == ".working/archive/adoption/" + rid + "/a/b.md")
    check("homes-bad-phase-refused",
          "invalid evidence inventory phase" in (refusal(inventory_rel, rid, "Bad") or ""))
    check("homes-bad-run-refused", "invalid adoption run-id" in
          (refusal(evidence_home_rel, "imp-20260917T120000Z-0123456789abcdef") or ""))
    # item 11: the Move root and this run's archive root DERIVE from the public store constructors.
    check("homes-moved-root-from-store",
          _MOVED_ROOT == ".working/archive/moved" and store.moved_dest("a/b.md") == _MOVED_ROOT + "/a/b.md")
    check("homes-archive-root-from-store",
          _archive_root(rid) == ".working/archive/adoption/" + rid
          and archive_rel(rid, "a/b.md") == _archive_root(rid) + "/a/b.md")

    # 3: inventory grading is the doctor's (spec 4.2): emit -> reparse -> re-emit is a byte fixed point; a
    # path claimed twice and every malformed row are CANNOT-EVALUATE; the legacy format is the named finding.
    rows = [inventory_row(home + "/payload/a.md", b"alpha\n"),
            inventory_row(archive_rel(rid, "legacy/OLD.md"), b"old\n")]
    data = emit_inventory(rid, rows)
    doc = tomllib.loads(data.decode("utf-8"))
    check("inventory-roundtrip-valid", validate_inventory(doc, rid).status == VALID)
    check("inventory-reemit-fixed-point", emit_inventory(rid, doc["file"]) == data)

    def variant(**changes):
        d = dict(format=store.EVIDENCE_INVENTORY_FORMAT, adoption=dict(doc["adoption"]),
                 file=[dict(r) for r in doc["file"]])
        d.update(changes)
        return d

    def identity_variant(**changes):
        d = variant()
        d["adoption"] = dict(d["adoption"], **changes)
        for key, value in list(changes.items()):
            if value is None:
                del d["adoption"][key]
        return d

    # The [adoption] identity (spec 4.2, the run-binding ruling): emitted bytes carry exactly the run,
    # the phase and the plan digest; a missing or malformed identity, a foreign run and a foreign or
    # reserved phase are each CANNOT-EVALUATE, and an inventory of another kind never carries the table.
    check("inventory-identity-emitted", doc["adoption"] == dict(
        run_id=rid, phase=BASE_PHASE, plan_digest=_t_plan_digest))
    check("inventory-identity-phase-emitted", tomllib.loads(emit_inventory(
        rid, [], "retirement").decode("utf-8"))["adoption"]["phase"] == "retirement")
    check("inventory-identity-missing-cannot-eval", validate_inventory(dict(
        format=store.EVIDENCE_INVENTORY_FORMAT, file=[dict(r) for r in doc["file"]]), rid).status == CANNOT)
    check("inventory-identity-foreign-run-cannot-eval",
          validate_inventory(identity_variant(run_id=other_run), rid).status == CANNOT)
    for label, broken in (("extra-key", identity_variant(note=1)),
                          ("missing-digest", identity_variant(plan_digest=None)),
                          ("short-digest", identity_variant(plan_digest="sha256:" + "a" * 63)),
                          ("unprefixed-digest", identity_variant(plan_digest="ab" * 32)),
                          ("bad-phase", identity_variant(phase="Bad/../phase")),
                          ("uppercase-phase", identity_variant(phase="Retirement"))):
        check("inventory-identity-{}-cannot-eval".format(label),
              validate_inventory(broken, rid).status == CANNOT)
    check("emit-missing-digest-refused", "identity" in
          (refusal(_emit_inventory, rid, []) or ""))
    check("base-phase-name-reserved", "reserved" in (refusal(inventory_rel, rid, BASE_PHASE) or ""))

    dup = variant()
    dup["file"].append(dict(dup["file"][0]))
    check("inventory-duplicate-claim-cannot-eval", validate_inventory(dup, rid).status == CANNOT)
    legacy = validate_inventory(dict(format="opf.ingest.evidence-inventory/v1", file=[]), rid)
    check("inventory-legacy-format-named-finding",
          legacy.status == INVALID and any("legacy-ingest-inventory" in f for f in legacy.findings))
    check("inventory-unknown-key-cannot-eval", validate_inventory(variant(extra=1), rid).status == CANNOT)
    check("inventory-not-a-table-cannot-eval", validate_inventory([], rid).status == CANNOT)
    for label, key, value in (("negative-size", "size", -1), ("bool-size", "size", True),
                              ("prefixed-sha", "sha256", "sha256:" + ZERO), ("uppercase-sha", "sha256", "A" * 64)):
        bad = variant()
        bad["file"][0][key] = value
        check("inventory-{}-cannot-eval".format(label), validate_inventory(bad, rid).status == CANNOT)
    for label, path in (("bundle-root-inventory", home + "/inventory.toml"),
                        ("bundle-root-phase-inventory", home + "/inventory-a.toml"),
                        ("outside-families", ".working/somewhere/else.md"),
                        ("other-run-archive", archive_rel(other_run, "x.md")),
                        ("traversal", ".working/../escape")):
        bad = variant()
        bad["file"][0]["path"] = path
        check("inventory-{}-path-cannot-eval".format(label), validate_inventory(bad, rid).status == CANNOT)
    for label, path in (("nested-inventory-member", home + "/payload/inventory.toml"),
                        ("moved-destination", store.moved_dest("legacy/OLD.md")),
                        ("same-run-archive", archive_rel(rid, "OLD.md"))):
        ok = variant()
        ok["file"][0]["path"] = path
        check("inventory-{}-path-valid".format(label), validate_inventory(ok, rid).status == VALID)
    check("emit-malformed-refused", "inventory refused" in
          (refusal(emit_inventory, rid, [dict(path="docs/x.md", size=1, sha256=ZERO)]) or ""))

    # 3b: a path claimed by BOTH the base and a phase inventory of one bundle cannot evaluate (the
    # verifier's cross-inventory duplicate-claim guard, over a hand-built bundle).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        _root3 = Path(temp).resolve()
        _payload_rel = home + "/payload/a.md"
        (_root3 / _payload_rel).parent.mkdir(parents=True)
        (_root3 / _payload_rel).write_bytes(b"alpha\n")
        (_root3 / inventory_rel(rid)).write_bytes(
            emit_inventory(rid, [inventory_row(_payload_rel, b"alpha\n")]))
        (_root3 / inventory_rel(rid, "completion")).write_bytes(
            emit_inventory(rid, [inventory_row(_payload_rel, b"alpha\n")], "completion"))
        crossed = verify_bundle(_root3, rid)
        check("verify-cross-inventory-duplicate-cannot-eval",
              crossed.status == CANNOT and any("more than one inventory" in f for f in crossed.findings))

    # 3c (round 3): bundle verification RETAINS its directory identities from the listing through every
    # inventory and payload read, so a directory swapped onto the bundle pathname mid-verification is
    # never re-resolved and two directories neither of which verifies alone can never combine into one
    # false success. Vector A injects the swap immediately before the payload read (the round-3 QA
    # reproduction: reading the original's inventory, then the replacement's payload bytes, reported
    # VALID); vector B injects it immediately after the listing (the original's listing combined with
    # the replacement's inventory and payload). Both must instead report the ORIGINAL bundle's payload
    # drift, read through the retained descriptors.
    _swap_payload_rel = home + "/payload.txt"

    def _swap_fixture(base, tag, replacement_files):
        broot = base / tag
        (broot / _swap_payload_rel).parent.mkdir(parents=True)
        (broot / _swap_payload_rel).write_bytes(b"BAD!!")
        (broot / inventory_rel(rid)).write_bytes(
            emit_inventory(rid, [inventory_row(_swap_payload_rel, b"GOOD!")]))
        stage_fixture_plan(broot, rid)
        repl = broot / "replacement"
        repl.mkdir()
        for name, payload in replacement_files:
            (repl / name).write_bytes(payload)

        def swap():
            os.rename(broot / home, str(broot / home) + ".aside")
            os.rename(repl, broot / home)
        return broot, swap

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        _swap_base = Path(temp).resolve()
        # vector A: the replacement alone is CANNOT-EVALUATE (malformed inventory), the original alone
        # is INVALID (payload drift); the swap fires immediately before the payload read.
        broot_a, swap_a = _swap_fixture(_swap_base, "read", (
            ("inventory.toml", b"invalid"), ("payload.txt", b"GOOD!")))
        check("verify-swap-fixture-original-invalid", verify_bundle(broot_a, rid).status == INVALID)
        _read_fired = []
        _real_lstat_at = _journal._lstat_at

        def _reading_swap(pfd, name):
            # the LIVE read path (round 4): _verify_bundle_at stats each listed payload through
            # _journal._lstat_at on its RETAINED parent immediately before read_retained reads it, so
            # hooking here injects the swap between the listing and the payload read. The round-3 hook
            # patched _journal._read_contained, which the retained-descriptor rewrite no longer calls for
            # bundle reads, so its swap never fired and the vector passed vacuously; the fired check below
            # keeps this vector honest.
            if name == "payload.txt" and not _read_fired:
                _read_fired.append(name)
                swap_a()
            return _real_lstat_at(pfd, name)

        with mock.patch.object(_journal, "_lstat_at", _reading_swap):
            swapped = verify_bundle(broot_a, rid)
        check("verify-swap-before-payload-read-injection-fired", _read_fired == ["payload.txt"])
        check("verify-swap-before-payload-read-still-original-drift",
              swapped.status == INVALID and any("payload drift" in f for f in swapped.findings))
        # vector B: the replacement alone is CANNOT-EVALUATE (a malformed phase inventory its own
        # listing would surface); the swap fires immediately after the bundle listing.
        broot_b, swap_b = _swap_fixture(_swap_base, "list", (
            ("inventory.toml", emit_inventory(rid, [inventory_row(_swap_payload_rel, b"GOOD!")])),
            ("payload.txt", b"GOOD!"), ("inventory-completion.toml", b"invalid")))
        _list_fired = []
        _real_listdir = os.listdir

        def _listing_swap(target):
            names = _real_listdir(target)
            if isinstance(target, int) and not _list_fired:
                _list_fired.append(target)
                swap_b()
            return names

        with mock.patch.object(os, "listdir", _listing_swap):
            swapped = verify_bundle(broot_b, rid)
        check("verify-swap-after-listing-injection-fired", _list_fired != [])
        check("verify-swap-after-listing-still-original-drift",
              swapped.status == INVALID and any("payload drift" in f for f in swapped.findings))

    # 3d (round 9): the verifier reads the bundle's plan.toml ONCE and checks its inventory row against
    # the bytes its seal proof read (spec 4.2): plan A is swapped for B (A plus a newline) right after
    # the proof, under an inventory whose plan row names B, so a second read would combine the proof of
    # A with B's row into a false VALID; the one read instead reports the plan's drift.
    import _opf_check
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        _root4 = Path(temp).resolve()
        _plan_a = fixture_plan(rid)[0]
        _plan_b = _plan_a + b"\n"
        stage_fixture_plan(_root4, rid)
        (_root4 / inventory_rel(rid)).write_bytes(
            emit_inventory(rid, [inventory_row(plan_rel(rid), _plan_b)]))
        _proofs = []
        _real_prove = _opf_check._prove_adoption_plan

        def _proving_swap(raw, run_id_):
            _proofs.append(raw)
            sealed_ = _real_prove(raw, run_id_)
            (_root4 / plan_rel(rid)).write_bytes(_plan_b)
            return sealed_

        with mock.patch.object(_opf_check, "_prove_adoption_plan", _proving_swap):
            swapped = verify_bundle(_root4, rid)
        check("verify-plan-swapped-after-proof-one-read",
              _proofs == [_plan_a] and swapped.status == INVALID
              and any(repr(plan_rel(rid)) in f and "payload drift" in f for f in swapped.findings))

    # 4: the op-list invariants, over hand-built lists (check_apply_ops is pure).
    src, body = ".working/TODO.md", b"todo\n"
    copy = archive_rel(rid, src)

    def c(path, payload):
        return dict(op="create", path=path,
                    poststate=dict(kind="file", mode=FILE_MODE, **{"content-sha256": _sha256(payload)}))

    def sealed(ops, staged, phase=None):
        inv = emit_inventory(rid, derive_rows(rid, ops, staged), phase)
        staged = dict(staged)
        staged[inventory_rel(rid, phase)] = inv
        return ops + [c(inventory_rel(rid, phase), inv)], staged

    def findings_of(ops, staged, phase=None):
        return check_apply_ops(rid, phase, ops, staged)

    good = sealed([c(copy, body), _pinned_remove(src, body, FILE_MODE)], {copy: body})
    check("compose-preserve-first-admitted", findings_of(*good) == [])
    check("compose-remove-before-copy-refused", any("preserve-first" in f for f in findings_of(
        *sealed([_pinned_remove(src, body, FILE_MODE), c(copy, body)], {copy: body}))))
    check("compose-remove-without-copy-refused", any("preserve-first" in f for f in findings_of(
        *sealed([_pinned_remove(src, body, FILE_MODE)], {}))))
    check("compose-unpinned-remove-refused", any("source-poststate" in f for f in findings_of(
        *sealed([c(copy, body), dict(op="remove", path=src, poststate=dict(kind="absent"))], {copy: body}))))
    check("compose-pin-copy-mismatch-refused", any("differs" in f for f in findings_of(
        *sealed([c(copy, b"other\n"), _pinned_remove(src, body, FILE_MODE)], {copy: b"other\n"}))))
    elsewhere = (("write-archive", dict(op="write", path=copy,
                                        poststate=dict(kind="file", **{"content-sha256": _sha256(body)})), {}),
                 ("remove-archive", _pinned_remove(copy, body, FILE_MODE), {}),
                 ("rmdir-evidence", dict(op="rmdir", path=home, poststate=dict(kind="absent")), {}),
                 ("create-other-run-archive", c(archive_rel(other_run, src), body),
                  {archive_rel(other_run, src): body}),
                 ("create-other-run-bundle", c(evidence_home_rel(other_run) + "/x.md", body),
                  {evidence_home_rel(other_run) + "/x.md": body}),
                 ("create-import-bundle", c(store.evidence_run("import", "imp-20260917T120000Z-0123456789abcdef")
                                            + "/x.md", body),
                  {store.evidence_run("import", "imp-20260917T120000Z-0123456789abcdef") + "/x.md": body}))
    for label, op, staged in elsewhere:
        check("compose-immutable-{}-refused".format(label),
              any("immutable" in f for f in findings_of(*sealed([op], staged))))
    hand = emit_inventory(rid, [inventory_row(copy, body), inventory_row(home + "/never-written.md", b"x")])
    check("compose-hand-inventory-refused", any("derived" in f for f in findings_of(
        [c(copy, body), _pinned_remove(src, body, FILE_MODE), c(inventory_rel(rid), hand)],
        {copy: body, inventory_rel(rid): hand})))
    check("compose-inventory-not-final-refused", any("final op" in f for f in findings_of(
        [good[0][-1]] + good[0][:-1], good[1])))
    check("compose-no-inventory-refused", any("exactly its own inventory" in f for f in findings_of(
        good[0][:-1], good[1])))
    check("compose-base-inventory-in-phase-refused", any("exactly its own inventory" in f for f in findings_of(
        good[0], good[1], "completion")))
    check("compose-path-twice-refused", any("second time" in f for f in findings_of(*sealed(
        [c(copy, body), _pinned_remove(src, body, FILE_MODE),
         dict(op="write", path=src, poststate=dict(kind="file", **{"content-sha256": _sha256(body)}))],
        {copy: body, src: body}))))
    # a write over a live path destroys its bytes exactly as a removal does, so it takes the SAME pinned,
    # same-transaction, digest-verified archive-copy pairing (spec 14.2); an rmdir may target only a
    # directory this transaction created; and the store control roots (_opf_store.STORE_ROOT_CONTROL_DIRS,
    # the adoption journal's own tree included) are never operands of any kind.
    new_body = b"# rendered view\n"

    def w(path, payload, pin=None):
        post = dict(kind="file")
        post["content-sha256"] = _sha256(payload)
        op = dict(op="write", path=path, poststate=post)
        if pin is not None:
            op["source-poststate"] = dict(kind="file", mode=FILE_MODE, sha256=_sha256(pin))
        return op

    check("compose-unpinned-write-refused", any("source-poststate" in f for f in findings_of(
        *sealed([w(src, new_body)], {src: new_body}))))
    check("compose-write-without-copy-refused", any("preserve-first" in f for f in findings_of(
        *sealed([w(src, new_body, pin=body)], {src: new_body}))))
    check("compose-write-pin-copy-mismatch-refused", any("differs" in f for f in findings_of(
        *sealed([c(copy, b"other\n"), w(src, new_body, pin=body)],
                {copy: b"other\n", src: new_body}))))
    check("compose-paired-write-admitted", findings_of(
        *sealed([c(copy, body), w(src, new_body, pin=body)], {copy: body, src: new_body})) == [])
    check("compose-live-rmdir-refused", any("did not create" in f for f in findings_of(
        *sealed([dict(op="rmdir", path="legacy/empty", poststate=dict(kind="absent"))], {}))))
    check("compose-control-root-create-refused", any("control root" in f for f in findings_of(
        *sealed([c(".git/hooks/post-checkout", body)], {".git/hooks/post-checkout": body}))))
    journal_file = JOURNAL_REL + "/x/frames.log"
    check("compose-journal-remove-refused", any("control root" in f for f in findings_of(
        *sealed([c(archive_rel(rid, journal_file), body), _pinned_remove(journal_file, body, FILE_MODE)],
                {archive_rel(rid, journal_file): body}))))
    mv = store.moved_dest("legacy/OLD.md")
    check("derive-rows-claims-move-destination",
          derive_rows(rid, [c(mv, body)], {mv: body}) == [inventory_row(mv, body)])
    # K1: no protected destination (_opf_adopt.protected_destination, the ONE predicate the planner shares) is
    # an operand: .git and .aiqt at any depth, every store control root (staging, journals and the imports
    # tree too, not only the archive and evidence homes), the product-root pointers and the store .gitignore.
    for label, path in (("nested-aiqt", "docs/.aiqt/x.md"), ("nested-git", "docs/.git/x"),
                        ("journals", ".working/journals/x"), ("staging", ".working/staging/x"),
                        ("imports", ".working/imports/x"), ("pointer", ".opf.toml"),
                        ("local-pointer", ".opf.local.toml"), ("store-gitignore", ".working/.gitignore"),
                        ("casefold-git", ".GIT/x"), ("casefold-pointer", ".OPF.toml"),
                        ("casefold-staging", ".Working/staging/x"), ("casefold-archive", ".WORKING/archive/x"),
                        ("casefold-gitignore", ".working/.GITIGNORE")):
        refused = findings_of(*sealed([c(path, body)], dict([(path, body)])))
        check("compose-protected-{}-create-refused".format(label),
              any("protected destination" in f for f in refused))

    def mkdir(path):
        return dict(op="mkdir", path=path, poststate=dict(kind="dir", mode=DIR_MODE))
    check("compose-protected-mkdir-refused", any("protected destination" in f for f in findings_of(
        *sealed([mkdir(".working/staging")], dict()))))
    check("compose-own-home-mkdirs-admitted", findings_of(*sealed(
        [mkdir(d) for d in (".working", ".working/archive", ".working/archive/moved",
                            ".working/archive/moved/legacy")] + [c(mv, body)], dict([(mv, body)]))) == [])

    # K1 parity: for each destination, a plan moving a source there validates exactly when apply admits the
    # create of it, both through the one shared predicate (its protected names compared case-insensitively).
    def move_plan(destination):
        p = schema.canonical_plan()
        digest = "sha256:" + _sha256(body)
        p["ops"].insert(0, dict(op="move-file", source="legacy/MOVE.md", destination=destination,
                                source_digest=digest))
        p["sources"] = sorted(p["sources"] + [dict(path="legacy/MOVE.md", digest=digest, disposition="move",
                                                   occupying=False, preservation=destination)],
                              key=lambda r: r["path"])
        p["effects"] = schema.derive_effects(p["ops"], p["sources"], schema.store_manifest(p["store"]))
        return p
    check("parity-canonical-plan-run", schema.canonical_plan()["run_id"] == rid)
    for path, admitted in (("adopter/moved.md", True), (store.moved_dest("legacy/MOVE.md"), True),
                           (store.moved_dest(".working/TODO.md"), True), (".aiqt/x.md", False),
                           ("docs/.aiqt/x.md", False), ("docs/.git/x", False), (".opf.toml", False),
                           (".opf.local.toml", False), (".working/.gitignore", False),
                           (".working/journals/x", False), (".working/staging/x", False),
                           (".working/imports/x", False), (".working/imported/x", False),
                           (archive_rel(other_run, "x.md"), False), (".GIT/x", False),
                           ("docs/.Aiqt/x.md", False), (".OPF.toml", False), (".Opf.Local.toml", False),
                           (".Working/staging/x", False), (".WORKING/.GITIGNORE", False)):
        planned = schema.validate_plan(move_plan(path)).status == VALID
        applied = findings_of(*sealed([c(path, body)], dict([(path, body)]))) == []
        check("parity-" + path, planned == applied == admitted)

    # 5: the journaled shell over throwaway fixtures. An occupied view destination and an occupying
    # machine-store file (a foreign manifest-shaped file, so no store resolves) are archived preserve-first
    # and removed; a non-occupying source takes only its preimage and stays frozen in place.
    def fixture(temp):
        root = Path(temp).resolve()
        files = {".working/TODO.md": b"hand-kept todo\n",
                 ".working/toml/manifest.toml": b"# a foreign manifest-shaped file\n",
                 "legacy/RULES.md": b"old rules\n"}
        for rel, payload in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(payload)
        return root, files

    def snapshot(root):
        # the regular files outside `.aiqt/`, read through the same fail-closed walk as tree_state
        return {rel: data for rel, (kind, _mode, data) in tree_state(root).items()
                if kind == "file" and not rel.startswith(".aiqt/")}

    # The whole-tree state the file-op checks (section 11 on) compare: EVERY entry beneath the root, `.aiqt/`
    # included, as (kind, mode, bytes or link target), never followed; directories, symlinks and special
    # entries count, as do modes. journal=False leaves out exactly the adoption journal root's subtree and
    # its ancestor directories (the journal's own bookkeeping), for comparisons across a transaction.
    # A directory the walk cannot list (the root or any subtree) RAISES (os.walk alone skips it silently), so
    # an unreadable subtree fails the comparison instead of dropping out of both states (section 11k).
    journal_dirs = tuple("/".join(JOURNAL_REL.split("/")[:i]) for i in range(1, JOURNAL_REL.count("/") + 2))

    def walk_error(exc):
        raise exc

    def tree_state(root, journal=True):
        out = {}
        for dirpath, dirnames, filenames in os.walk(root, onerror=walk_error):
            base = os.path.relpath(dirpath, root)
            for name in dirnames + filenames:
                rel = name if base == "." else base + "/" + name
                if not journal and (rel in journal_dirs or _within(rel, JOURNAL_REL)):
                    continue
                full = os.path.join(dirpath, name)
                st = os.lstat(full)
                mode = stat.S_IMODE(st.st_mode)
                if stat.S_ISLNK(st.st_mode):
                    out[rel] = ("link", mode, os.readlink(full))
                elif stat.S_ISDIR(st.st_mode):
                    out[rel] = ("dir", mode, None)
                elif stat.S_ISREG(st.st_mode):
                    out[rel] = ("file", mode, Path(full).read_bytes())
                else:
                    out[rel] = ("special", mode, None)
            if not journal:
                dirnames[:] = [d for d in dirnames if (d if base == "." else base + "/" + d) != JOURNAL_REL]
        return out

    def untouched_save_journal_dirs(before, root):
        """A refusal's whole-tree predicate: every entry of `before` is unchanged in kind, mode and bytes or
        target, and the ONLY new entries are the adoption journal root and its ancestors, as directories
        (the journal bookkeeping run_adopt_transaction creates before it composes); nothing else is added
        or removed, the journal root's own contents included."""
        after = tree_state(root)
        new = set(after) - set(before)
        return (all(k in journal_dirs and after[k][0] == "dir" for k in new)
                and dict((k, v) for k, v in after.items() if k not in new) == before)

    def plan_digest(payload):
        return "sha256:" + _sha256(payload)

    def compose_full(files, drift=False):
        def compose(ops):
            todo = files[".working/TODO.md"] + (b"drift" if drift else b"")
            ops.archive_occupying(".working/TODO.md", plan_digest(todo))
            ops.archive_occupying(".working/toml/manifest.toml", plan_digest(files[".working/toml/manifest.toml"]))
            ops.preserve("legacy/RULES.md", plan_digest(files["legacy/RULES.md"]))
        return compose

    def lock_free(root):
        return _journal.read_lock_owner(_journal_root(root)) is None

    def txn_state(root, txn):
        jr_fd = _journal.open_journal_root_from_path(root, JOURNAL_REL)
        try:
            return _journal.classify_state(jr_fd, _journal_root(root) / txn)
        finally:
            _journal._close_fd_quietly(jr_fd)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        check("fixture-store-not-resolved", store.resolve_store(root).status != store.RESOLVED)
        txn, _why = attempt(run_adopt_transaction, root, rid, compose_full(files))
        after = snapshot(root)
        check("apply-txn-named-by-run", txn == rid and txn_state(root, rid) == "complete" and lock_free(root))
        check("preserve-first-archive-copies-byte-identical",
              all(after.get(archive_rel(rid, p)) == files[p] for p in files))
        check("preserve-first-occupying-sources-removed",
              ".working/TODO.md" not in after and ".working/toml/manifest.toml" not in after)
        check("non-occupying-source-frozen-in-place", after.get("legacy/RULES.md") == files["legacy/RULES.md"])
        inv = tomllib.loads(after.get(inventory_rel(rid), b"file = []").decode("utf-8"))
        check("inventory-derived-from-transaction",
              sorted(r["path"] for r in inv["file"])
              == sorted([plan_rel(rid)] + [archive_rel(rid, p) for p in files]))
        check("apply-bundle-verifies", verify_bundle(root, rid).status == VALID)
        # THE single-byte payload flip (size preserved): the re-read verification goes red, then the
        # missing and non-regular listed-entry findings.
        archived = root / archive_rel(rid, ".working/TODO.md")
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(b"hand-kept todO\n")
        flipped = verify_bundle(root, rid)
        check("verify-single-byte-flip-invalid",
              flipped.status == INVALID and any("payload drift" in f for f in flipped.findings))
        archived.unlink()
        check("verify-missing-listed-invalid", verify_bundle(root, rid).status == INVALID)
        archived.symlink_to("elsewhere")
        typed = verify_bundle(root, rid)
        check("verify-non-regular-invalid",
              typed.status == INVALID and any("not a regular file" in f for f in typed.findings))
        archived.unlink()
        archived.write_bytes(files[".working/TODO.md"])
        check("verify-restored-valid", verify_bundle(root, rid).status == VALID)
        # a listed payload over the journal's contained-read cap cannot be hashed: CANNOT-EVALUATE naming
        # the cap (the disclosed capacity limit; compose, capture and poststate reads share the ceiling,
        # so the shell itself never writes such a bundle), never a truncated or unbounded read.
        archived.write_bytes(b"x" * (_journal._MAX_PRODUCT_READ_BYTES + 1))
        overcap = verify_bundle(root, rid)
        check("verify-over-cap-payload-cannot-eval",
              overcap.status == CANNOT and any("read cap" in f for f in overcap.findings))
        archived.unlink()
        archived.write_bytes(files[".working/TODO.md"])
        check("verify-restored-after-over-cap-valid", verify_bundle(root, rid).status == VALID)
        # one transaction per run and phase: a second base transaction refuses before it opens.
        before = snapshot(root)
        late = refusal(run_adopt_transaction, root, rid,
                       lambda ops: ops.create(evidence_home_rel(rid) + "/late.md", b"late\n"))
        check("second-base-txn-refused", late is not None and "fresh plan" in late)
        check("second-base-txn-writes-nothing", snapshot(root) == before and lock_free(root))
        # a later phase publishes its own inventory beside the committed base inventory.
        ptxn, _why = attempt(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/completion/result.toml", b"green = true\n"), phase="completion")
        check("phase-txn-published",
              ptxn == rid + ".completion" and (root / inventory_rel(rid, "completion")).is_file()
              and verify_bundle(root, rid).status == VALID)
        base = root / inventory_rel(rid)
        saved = base.read_bytes() if base.is_file() else None
        if saved is not None:
            base.unlink()
        check("verify-phase-without-base-cannot-eval",
              saved is not None and verify_bundle(root, rid).status == CANNOT)
        if saved is not None:
            base.write_bytes(saved)
        # an op against the adoption journal's own tree (a store control root) is refused, so a committed
        # record can never be archived away by a later run.
        frames_rel = JOURNAL_REL + "/" + rid + "/frames.log"
        frames_bytes = (root / frames_rel).read_bytes()
        journal_hit = refusal(run_adopt_transaction, root, other_run, lambda ops: ops.archive_occupying(
            frames_rel, plan_digest(frames_bytes)))
        check("apply-journal-operand-refused",
              journal_hit is not None and "control root" in journal_hit
              and (root / frames_rel).read_bytes() == frames_bytes and txn_state(root, rid) == "complete")
        # the phase gate binds the LIVE inventory.toml to the committed base transaction's INTENT digest,
        # so bytes swapped after the commit refuse a later phase.
        swapped = emit_inventory(rid, [inventory_row(home + "/decoy.md", b"decoy\n")])
        base.write_bytes(swapped)
        drifted_base = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/audit/result.toml", b"green = true\n"), phase="audit")
        check("phase-swapped-inventory-refused",
              saved is not None and swapped != saved and drifted_base is not None
              and "INTENT" in drifted_base)
        if saved is not None:
            base.write_bytes(saved)
        # QA round 3: the gate reads the journal ONCE, so a stale classification is never combined with a
        # later frame read. classify_state is patched to answer complete while every frame read returns
        # the INTENT-only (open) frames carrying the live inventory's own digest: a two-read gate accepts
        # that split observation; the one-read gate refuses it as not complete.
        live_digest = _sha256(base.read_bytes())
        open_frames = [(_journal.F_INTENT, dict(txn=rid, ops=[dict(
            op="create", path=inventory_rel(rid), poststate={"content-sha256": live_digest})]))]
        root_fd2 = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(_journal, "classify_state", lambda *a, **k: "complete"), \
                    mock.patch.object(_journal, "read_frames", lambda *a, **k: (list(open_frames), False, 0)):
                try:
                    _committed_base_or_refuse(root_fd2, _journal_root(root), rid, "audit")
                    split = None
                except AdoptApplyError as exc:
                    split = str(exc)
        finally:
            os.close(root_fd2)
        check("phase-gate-single-frame-read", split is not None and "not complete" in split)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        orphan = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/completion/result.toml", b"x\n"), phase="completion")
        check("phase-without-base-refused", orphan is not None and "never stands in" in orphan)
        drifted = refusal(run_adopt_transaction, root, rid, compose_full(files, drift=True))
        check("drifted-source-refused", drifted is not None and "drifted" in drifted)
        check("drifted-source-writes-nothing", snapshot(root) == before and lock_free(root)
              and not (root / JOURNAL_REL / rid).exists())
        # a hand-planted inventory.toml with NO base journal transaction never admits a phase (spec 4.2).
        planted = root / inventory_rel(rid)
        planted.parent.mkdir(parents=True, exist_ok=True)
        planted.write_bytes(b"not toml")
        forged = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/completion/result.toml", b"x\n"), phase="completion")
        check("phase-planted-inventory-refused", forged is not None and "never stands in" in forged)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        root = Path(temp).resolve()
        (root / ".working/toml").mkdir(parents=True)
        (root / ".working/toml/manifest.toml").write_text(_opf_init.build_manifest(), encoding="utf-8")
        before = snapshot(root)
        leased = refusal(run_adopt_transaction, root, rid, lambda ops: None)
        check("resolved-store-refused-without-lease",
              store.resolve_store(root).status == store.RESOLVED and leased is not None and "lease" in leased)
        check("resolved-store-writes-nothing", snapshot(root) == before and not (root / ".aiqt").exists())

    # THE STALE-PREFLIGHT GUARD (QA round 2): the pre-lock journal and posture checks never
    # authorize composition. A cooperating writer (init-store takes this same lock) that opens a
    # transaction, or publishes a store, between the preflight and the acquisition is caught by
    # the re-proof UNDER the held lock, before anything composes.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        real_acquire = _journal.acquire_lock

        def plant_open_txn(journal_root, session_id):
            real_acquire(journal_root, session_id)
            rfd = _open_product_root(root)
            try:
                jfd = _journal.open_journal_root_fd(rfd, JOURNAL_REL)
                try:
                    os.mkdir("foreignrun", dir_fd=jfd)
                    _journal._create_frames_excl(jfd, _journal_root(root) / "foreignrun")
                    _journal.publish(jfd, _journal_root(root) / "foreignrun", _journal.F_INTENT,
                                     {"txn": "foreignrun", "header": {}, "ops": []})
                finally:
                    _journal._close_fd_quietly(jfd)
            finally:
                store._close_fd_exc_safe(rfd)

        with mock.patch.object(_journal, "acquire_lock", plant_open_txn):
            raced = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
                evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("stale-preflight-open-txn-rechecked-under-lock",
              raced is not None and "appeared before the lock" in raced
              and not (root / JOURNAL_REL / rid).exists() and lock_free(root))

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        real_acquire = _journal.acquire_lock

        def plant_pointer(journal_root, session_id):
            real_acquire(journal_root, session_id)
            (root / store.POINTER_REL).write_bytes(b"store = 1\n")

        with mock.patch.object(_journal, "acquire_lock", plant_pointer):
            raced = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
                evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("stale-preflight-posture-rechecked-under-lock",
              raced is not None and "store posture" in raced
              and not (root / JOURNAL_REL / rid).exists() and lock_free(root))

    # 5b: hand-built op lists through the EXECUTABLE shell: a write over a live path, an rmdir of a live
    # directory, and a create under a store control root are each refused before the transaction opens,
    # the tree untouched.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        (root / "legacy/empty").mkdir(parents=True)
        before = snapshot(root)

        def hand_write(ops):
            new = b"# rendered view\n"
            post = dict(kind="file")
            post["content-sha256"] = _sha256(new)
            ops.ops.append(dict(op="write", path=".working/TODO.md", poststate=post))
            ops.staged[".working/TODO.md"] = new

        overwrote = refusal(run_adopt_transaction, root, rid, hand_write)
        check("apply-hand-built-write-refused",
              overwrote is not None and "source-poststate" in overwrote
              and (root / ".working/TODO.md").read_bytes() == files[".working/TODO.md"]
              and not (root / archive_rel(rid, ".working/TODO.md")).exists())

        def hand_rmdir(ops):
            ops.ops.append(dict(op="rmdir", path="legacy/empty", poststate=dict(kind="absent")))

        deleted = refusal(run_adopt_transaction, root, rid, hand_rmdir)
        check("apply-hand-built-rmdir-refused",
              deleted is not None and "did not create" in deleted and (root / "legacy/empty").is_dir())
        hooked = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            ".git/hooks/post-checkout", b"#!/bin/sh\necho hi\n", mode=0o755))
        check("apply-control-root-create-refused",
              hooked is not None and "control root" in hooked and not (root / ".git").exists()
              and snapshot(root) == before and lock_free(root))

    # 5c: the store-posture gate fails closed on EVERY cannot-evaluate posture except the one
    # first-adoption state the fixtures above already exercise (a present default .working/ without a
    # valid manifest): two machine stores (ambiguous), a malformed pointer, and a pointer resolving a
    # store OUTSIDE the product root each refuse with nothing written; NOT-ADOPTED is admitted.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        root = Path(temp).resolve()
        for sub in ("toml", "other"):
            (root / ".working" / sub).mkdir(parents=True)
            (root / ".working" / sub / "manifest.toml").write_text(_opf_init.build_manifest(),
                                                                   encoding="utf-8")
        before = snapshot(root)
        twin = refusal(run_adopt_transaction, root, rid, lambda ops: ops.archive_occupying(
            ".working/toml/manifest.toml",
            plan_digest((root / ".working/toml/manifest.toml").read_bytes())))
        check("posture-multiple-stores-refused",
              store.resolve_store(root).status == CANNOT and twin is not None
              and "cannot be evaluated" in twin and snapshot(root) == before
              and not (root / ".aiqt").exists())
        # the posture refusals carry no mid-message write claim of their own, so the composed text never
        # contradicts itself (red against "refuses before anything is written" / "before writing").
        check("posture-refusal-no-own-claim", twin is not None and "before anything" not in twin
              and twin.count("nothing written") <= 1)
        os.unlink(root / ".working/other/manifest.toml")
        os.rmdir(root / ".working/other")
        leased = refusal(run_adopt_transaction, root, rid, lambda ops: ops.create(
            evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("posture-resolved-refusal-no-own-claim", store.resolve_store(root).status == store.RESOLVED
              and leased is not None and "lease join" in leased and "before writing" not in leased)
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / ".opf.toml").write_bytes(b"this is [not toml")
        pointed = refusal(run_adopt_transaction, root, rid,
                          lambda ops: ops.create(evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("posture-malformed-pointer-refused",
              store.resolve_store(root).status == CANNOT and pointed is not None
              and "cannot be evaluated" in pointed and not (root / ".aiqt").exists())
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        pair = Path(temp).resolve()
        root, outside = pair / "product", pair / "elsewhere"
        (outside / ".working/toml").mkdir(parents=True)
        (outside / ".working/toml/manifest.toml").write_text(_opf_init.build_manifest(), encoding="utf-8")
        root.mkdir()
        (root / ".opf.toml").write_text('[store]\ntarget = "dir:' + str(outside) + '"\n', encoding="utf-8")
        outward = refusal(run_adopt_transaction, root, rid,
                          lambda ops: ops.create(evidence_home_rel(rid) + "/x.md", b"x\n"))
        check("posture-outside-pointer-refused",
              store.resolve_store(root).status == store.RESOLVED and outward is not None
              and "outside the product root" in outward
              and not (root / ".aiqt").exists() and not (root / ".working").exists())
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "legacy").mkdir()
        (root / "legacy/RULES.md").write_bytes(b"old rules\n")
        was_unadopted = store.resolve_store(root).status == store.NOT_ADOPTED
        fresh, _why = attempt(run_adopt_transaction, root, rid,
                              lambda ops: ops.preserve("legacy/RULES.md", plan_digest(b"old rules\n")))
        check("not-adopted-root-admitted",
              was_unadopted and fresh == rid and verify_bundle(root, rid).status == VALID)

    # 6: the pre-commit abort. A failure at the final op (after both removals) rolls the one transaction
    # back in reverse order: each source is restored byte-identical before its archive copy is discarded.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory):
            aborted = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("abort-rolls-back", aborted is not None and "rolled back" in aborted
              and "nothing written" not in aborted and "terminal record" in aborted
              and txn_state(root, rid) == "rolled-back" and lock_free(root))
        check("abort-restores-prestate", snapshot(root) == before)

    # 6a: a committed transaction whose lock release fails names the lock that stays, never a silent
    # success over it (red against a release that swallows its failure).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)

        def release_refused(journal_root):
            raise OSError("an injected lock release fault")
        with mock.patch.object(_journal, "release_lock", release_refused):
            kept = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("commit-lock-release-failure-named", kept is not None and "COMMITTED" in kept
              and "lock STAYS" in kept and txn_state(root, rid) == "complete" and not lock_free(root))
    # 6a': the post-COMMIT release failure is its own type, carrying the transaction, and says the changes
    # LANDED (red against the plain refusal type); the exit mapping is AdoptApplyError's, unchanged.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        with mock.patch.object(_journal, "release_lock", release_refused):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
                landed_error = None
            except AdoptApplyError as exc:
                landed_error = exc
        check("commit-lock-release-typed-landed", isinstance(landed_error, AdoptCommittedLockError)
              and landed_error.txn == rid and "LANDED" in str(landed_error)
              and "lock STAYS" in str(landed_error) and txn_state(root, rid) == "complete")
    # 6a'': an interrupt raised inside the release after a committed transaction propagates as itself, with
    # the COMMITTED state and the observed lock outcome attached (red against a release that drops both).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)

        def release_interrupted(journal_root):
            raise KeyboardInterrupt("injected release interrupt")
        notes = None
        with mock.patch.object(_journal, "release_lock", release_interrupted):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except KeyboardInterrupt as exc:
                notes = " ".join(getattr(exc, "__notes__", []))
        check("commit-release-interrupt-outcome-noted", notes is not None and "COMMITTED" in notes
              and "LANDED" in notes and "lock STAYS" in notes and txn_state(root, rid) == "complete"
              and not lock_free(root))
    # 6a''': the release read-back judges the lock by what THIS acquire wrote, never by process identity: a
    # lock another thread of this same process takes after this run's release is not "this run's lock
    # STAYS" (red against a read-back decided by process identity). Test-hermeticity: the released lock's
    # inode is HELD OPEN here too (an O_RDONLY descriptor taken before the release, closed only after the
    # verdict) across the peer acquire, so this check's verdict never rests on filesystem inode-number
    # behavior (ext4 reuses a freed inode number immediately, tmpfs and btrfs never do); production holds
    # its own descriptor on the lock across the release and read-back, and the reuse vector below (the
    # peer-reused-inode check) forces the collision and proves that hold.
    # The process umask is pinned for the vector (restored in the finally): an inherited owner-bit umask
    # would make the 0o600 lock unreadable to its own read-back, an ambient cause outside this check.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        saved_umask = os.umask(0o022)
        pinned_locks = []
        seen = {}
        try:
            root, files = fixture(temp)
            real_release = _journal.release_lock

            def peer_thread_after(journal_root):
                lock = str(Path(journal_root) / "lock")
                pinned_locks.append(os.open(lock, os.O_RDONLY | os.O_NOFOLLOW))
                st = os.fstat(pinned_locks[0])
                seen["own"] = (st.st_dev, st.st_ino)
                real_release(journal_root)
                _journal.acquire_lock(journal_root, "opf-adopt-selftest-thread")
                st = os.lstat(lock)
                seen["peer"] = (st.st_dev, st.st_ino)
            with mock.patch.object(_journal, "release_lock", peer_thread_after):
                done_txn, why = attempt(run_adopt_transaction, root, rid, compose_full(files))
            try:
                owner_now = _journal.read_lock_owner(_journal_root(root))
            except _journal.JournalError as exc:
                owner_now = "unreadable ({})".format(exc)
            check("commit-release-peer-lock-not-own",
                  done_txn == rid and why is None and not lock_free(root),
                  observed="txn={!r} why={!r} own lock (st_dev, st_ino)={!r} peer lock (st_dev, st_ino)="
                           "{!r} lock owner now={!r}".format(done_txn, why, seen.get("own"),
                                                             seen.get("peer"), owner_now))
        finally:
            os.umask(saved_umask)
            _close_held(pinned_locks)
    # Shared by the two inode-reuse vectors below (6a'''b and 6a11d): the ext4 reuse rule, modelled with
    # no ext4 mount. A freed inode number may be handed to the very next create; one still held by any open
    # descriptor of this process never is (the census is /proc/self/fd, fstat'ed with the REAL os.fstat).
    # _reuse_remapped wraps a stat-family call so a result whose identity a vector recorded as reused
    # reports the predecessor's (st_dev, st_ino) instead: production then sees exactly the identities a
    # reusing filesystem would show it, on any filesystem the self-test actually runs on.
    _real_stat, _real_lstat, _real_fstat = os.stat, os.lstat, os.fstat

    def _inode_free(identity):
        for fd_name in os.listdir("/proc/self/fd"):
            try:
                fd_st = _real_fstat(int(fd_name))
            except OSError:
                continue
            if (fd_st.st_dev, fd_st.st_ino) == identity:
                return False
        return True

    def _reuse_remapped(real, reuse):
        def wrapper(*args, **kwargs):
            got = real(*args, **kwargs)
            old = reuse.get((got.st_dev, got.st_ino))
            if old is None:
                return got
            return os.stat_result((got.st_mode, old[1], old[0], got.st_nlink, got.st_uid, got.st_gid,
                                   got.st_size, got.st_atime, got.st_mtime, got.st_ctime))
        return wrapper
    # 6a'''b: the inode-reuse vector for the release read-back, no test-side pin: a peer acquires after
    # this run's release, and the stat family is remapped so the peer's lock reports the released lock's
    # (st_dev, st_ino), but ONLY when no descriptor of this process still holds that inode (the ext4 reuse
    # rule above). Production holds the lock's descriptor from the moment its identity is taken until after
    # the read-back, so the remap never arms and the peer's live lock is never read as this run's (red
    # against an identity descriptor closed before the release: the remap arms, the read-back sees this
    # run's inode with the peer's bytes, and the committed transaction falsely raises
    # AdoptCommittedLockError over a peer's lock, saying this run's lock was altered and stays).
    if not census:
        skipped.append(("commit-release-peer-reused-inode-not-own", no_census))
    else:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            reuse = {}
            seen = {}
            try:
                root, files = fixture(temp)
                real_release = _journal.release_lock

                def peer_reused_inode(journal_root):
                    lock = str(Path(journal_root) / "lock")
                    own_st = _real_lstat(lock)
                    own = (own_st.st_dev, own_st.st_ino)
                    real_release(journal_root)
                    _journal.acquire_lock(journal_root, "opf-adopt-selftest-peer")
                    peer_st = _real_lstat(lock)
                    seen["own"] = own
                    seen["peer"] = (peer_st.st_dev, peer_st.st_ino)
                    seen["own freed"] = _inode_free(own)
                    if seen["own freed"]:
                        reuse[(peer_st.st_dev, peer_st.st_ino)] = own
                stat_w, lstat_w, fstat_w = (_reuse_remapped(_real_stat, reuse),
                                            _reuse_remapped(_real_lstat, reuse),
                                            _reuse_remapped(_real_fstat, reuse))
                with mock.patch.object(_journal, "release_lock", peer_reused_inode), \
                        mock.patch.object(os, "stat", stat_w), \
                        mock.patch.object(os, "lstat", lstat_w), \
                        mock.patch.object(os, "fstat", fstat_w), \
                        mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {stat_w}), \
                        mock.patch.object(os, "supports_follow_symlinks",
                                          os.supports_follow_symlinks | {stat_w, lstat_w}):
                    done_txn, why = attempt(run_adopt_transaction, root, rid, compose_full(files))
                try:
                    owner_now = _journal.read_lock_owner(_journal_root(root))
                except _journal.JournalError as exc:
                    owner_now = "unreadable ({})".format(exc)
                check("commit-release-peer-reused-inode-not-own",
                      done_txn == rid and why is None and not lock_free(root)
                      and seen.get("own freed") is False,
                      observed="txn={!r} why={!r} seen={!r} lock owner now={!r}".format(
                          done_txn, why, seen, owner_now))
            finally:
                os.umask(saved_umask)
    # 6a'''b2: the inode-reuse vector for the acquire-failure path (_failed_lock_state): acquire_lock creates
    # this run's lock, then fails with an OSError (lock_created), so the refusal releases that lock and
    # reads it back; a peer acquires inside that release and the stat family is remapped under the ext4
    # reuse rule above. _failed_lock_state HOLDS the identity's descriptor across its release and
    # read-back, so the remap never arms and the refusal says this run's lock was created, then released
    # again, the peer's lock named as another run's (red against an identity descriptor closed before the
    # release: the read-back sees this run's inode with the peer's bytes and falsely says this run's lock
    # was altered and stays).
    if not census:
        skipped.append(("failed-lock-state-peer-reused-inode", no_census))
    else:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            reuse = {}
            seen = {}
            try:
                root, files = fixture(temp)
                os.makedirs(root / JOURNAL_REL)
                real_release = _journal.release_lock
                real_acquire_now = _journal.acquire_lock

                def created_then_os_failed(journal_root, session_id):
                    real_acquire_now(journal_root, session_id)
                    fault = OSError("an injected acquire fault after the create")
                    fault.lock_created = True
                    raise fault

                def peer_reused_inode_at_failure(journal_root):
                    lock = str(Path(journal_root) / "lock")
                    own_st = _real_lstat(lock)
                    own = (own_st.st_dev, own_st.st_ino)
                    real_release(journal_root)
                    real_acquire_now(journal_root, "opf-adopt-selftest-peer")
                    peer_st = _real_lstat(lock)
                    seen["own"] = own
                    seen["peer"] = (peer_st.st_dev, peer_st.st_ino)
                    seen["own freed"] = _inode_free(own)
                    if seen["own freed"]:
                        reuse[(peer_st.st_dev, peer_st.st_ino)] = own
                stat_w, lstat_w, fstat_w = (_reuse_remapped(_real_stat, reuse),
                                            _reuse_remapped(_real_lstat, reuse),
                                            _reuse_remapped(_real_fstat, reuse))
                with mock.patch.object(_journal, "acquire_lock", created_then_os_failed), \
                        mock.patch.object(_journal, "release_lock", peer_reused_inode_at_failure), \
                        mock.patch.object(os, "stat", stat_w), \
                        mock.patch.object(os, "lstat", lstat_w), \
                        mock.patch.object(os, "fstat", fstat_w), \
                        mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {stat_w}), \
                        mock.patch.object(os, "supports_follow_symlinks",
                                          os.supports_follow_symlinks | {stat_w, lstat_w}):
                    err = refusal(run_adopt_transaction, root, rid, compose_full(files))
                check("failed-lock-state-peer-reused-inode",
                      "WAS created, then released again" in (err or "") and "altered" not in (err or "")
                      and "another run's journal lock, not this run's" in (err or "")
                      and seen.get("own freed") is False and not reuse,
                      observed="refusal={!r} seen={!r}".format(err, seen))
            finally:
                os.umask(saved_umask)
    # 6a'''b3: the two descriptor handoffs (_lock_identity's and _journal_listing's `keep`) are interrupt
    # safe: a KeyboardInterrupt raised at EVERY bytecode boundary of the keeping call's own frame
    # (sys.settrace with opcode events), one boundary per run, propagates with each descriptor closed
    # exactly once by exactly one party and none leaked. The close ledger closes each number for real,
    # then parks a guard pipe on it, so a second close of that number is counted rather than landing on a
    # reused descriptor; the leak census is /proc/self/fd. The lock leg runs the identity read of a held
    # lock, the failure leg the one inside _failed_lock_state (its created lock stays, as the interrupt
    # note says, and is removed between runs), the listing leg the run's first listing over a journal of
    # three components. Scope: the injection here is a SYNCHRONOUS raise, so these legs prove the
    # pop-before-close and append-is-handoff ownership discipline, nothing more. NAMED RESIDUAL, not
    # tested and not claimed: an ASYNCHRONOUS interrupt (SIGINT or SIGTERM, delivered through any thread),
    # or any other exception raised at the same point, can leave a descriptor open in the process until it
    # exits, at the boundary between a C call's return and the binding of the descriptor it returned
    # (skipped here), between a helper's return and the handoff of its descriptors to the owning list
    # (journal_state's transaction directories before held.extend has taken them all), between a
    # descriptor's pop and its close inside a close-out (that one descriptor, 6a'''b3c and 6a'''b3d), at
    # a close-out's own loop boundary, or inside a _journal helper (acquire_lock's own lock descriptor
    # among them); no signal mask is used, so these legs establish no leak-freedom under interrupt
    # (red against an ownership flag set after the append: two closes; and against a transfer inside the
    # finally: a leak).
    handoff_legs = (("descriptor-handoff-interrupt-lock-identity", _lock_identity, False),
                    ("descriptor-handoff-interrupt-failed-lock-state", _lock_identity, True),
                    ("descriptor-handoff-interrupt-journal-listing", _journal_listing, False))
    final_close_legs = (("final-close-out-injected-fault-closes-the-rest", ("held_components",), "refused"),
                        ("final-close-out-two-faults-every-fault-named", ("held_components", "anchors"),
                         "refused"),
                        ("final-close-out-fault-commit-stands-committed", ("anchors",), "committed"),
                        ("final-close-out-fault-interrupt-propagates-as-itself", ("held_components",),
                         "interrupted"),
                        ("early-lock-close-fault-commit-stands-committed", ("mine_held",), "committed"),
                        ("early-lock-close-fault-refusal-stands", ("mine_held",), "refused"),
                        ("early-lock-close-fault-interrupt-propagates-as-itself", ("mine_held",), "interrupted"),
                        ("closing-listing-close-fault-refusal-stands", ("_journal_listing",), "refused"),
                        ("closing-listing-close-fault-interrupt-propagates-as-itself", ("_journal_listing",),
                         "interrupted"),
                        ("final-close-out-interrupt-commit-propagates-as-itself", ("anchors",),
                         "committed-stopped"),
                        ("final-close-out-interrupt-refusal-propagates-as-itself", ("held_components",),
                         "refused-stopped"),
                        ("closing-listing-two-faults-every-fault-named", ("_journal_listing",) * 2, "refused"),
                        ("closing-listing-fault-then-interrupt-propagates-as-itself", ("_journal_listing",) * 2,
                         "refused-stopped"))
    if not census:
        for name, _target, _failing in handoff_legs:
            skipped.append((name, no_census))
        for name, _targets, _kind in final_close_legs:
            skipped.append((name, no_census))
        skipped.append(("journal-listing-close-out-injected-fault-closes-the-rest", no_census))
        for name in ("closing-walk-interrupt-then-fault-propagates-as-itself",
                     "closing-walk-fault-then-interrupt-propagates-as-itself",
                     "closing-walk-two-faults-every-fault-named",
                     "closing-sweep-interrupt-then-close-fault-propagates-as-itself",
                     "closing-sweep-fault-then-close-interrupt-propagates-as-itself",
                     "closing-sweep-close-fault-keeps-the-lock-clause",
                     "closing-listing-close-fault-stops-the-observation",
                     "closing-listing-interrupt-names-the-own-exception",
                     "release-readback-close-fault-keeps-the-release-interrupt-refused",
                     "release-readback-close-fault-keeps-the-release-interrupt-committed",
                     "acquire-interrupt-lock-read-close-fault-keeps-the-interrupt",
                     "failed-acquire-readback-close-fault-keeps-the-release-interrupt",
                     "failed-acquire-held-identity-close-fault-keeps-the-owner-interrupt",
                     "failed-acquire-identity-read-close-fault-keeps-the-read-interrupt",
                     "own-identity-read-close-fault-keeps-the-read-interrupt",
                     "closing-sweep-close-fault-keeps-the-state-lock-clause",
                     "closing-walk-close-fault-stops-the-walk",
                     "closing-sweep-close-fault-stops-the-sweep",
                     "journal-helper-close-fault-keeps-the-release-interrupt-refused",
                     "journal-helper-close-fault-keeps-the-release-interrupt-committed",
                     "journal-helper-close-fault-keeps-the-body-interrupt",
                     "release-interrupt-keeps-the-refusal-reason",
                     "failed-acquire-owner-read-close-fault-keeps-the-acquire-error",
                     "journal-open-parent-close-fault-keeps-the-missing-component",
                     "journal-pid-start-close-fault-keeps-the-read-interrupt",
                     "verify-missing-ancestor-opened-once"):
            skipped.append((name, no_census))
    else:
        import dis

        def _fds_open():
            out = set()
            for fd_name in os.listdir("/proc/self/fd"):
                try:
                    _real_fstat(int(fd_name))
                except OSError:
                    continue
                out.add(int(fd_name))
            return out

        def _unbound_returns(code):
            """The offsets of each store binding a call's result: the one boundary no Python code covers."""
            got, prior = set(), None
            for ins in dis.get_instructions(code):
                if (ins.opname.startswith(("STORE_FAST", "STORE_DEREF")) and prior is not None
                        and prior.opname.startswith("CALL")
                        and any(name in str(ins.argval) for name in ("lfd", "cur"))):
                    got.add(ins.offset)
                prior = ins
            return got

        def _handoff_run(root, target, boundary, compose, real_close):
            """One transaction interrupted at opcode `boundary` of the keeping call of `target`: (fired,
            outcome, numbers closed twice, numbers leaked)."""
            code = target.__code__
            skip = _unbound_returns(code)
            hits, fired = [0], []
            guard_r, guard_w = os.pipe()
            guard_st = _real_fstat(guard_r)
            guard_id = (guard_st.st_dev, guard_st.st_ino)
            guarded, doubles = set(), []

            def ledger_close(fd):
                try:
                    fst = _real_fstat(fd)
                    fid = (fst.st_dev, fst.st_ino)
                except OSError:
                    fid = None
                if fid is None or (fd in guarded and fid == guard_id):
                    doubles.append(fd)      # already closed once and never reopened: a second close
                    guarded.discard(fd)
                    return real_close(fd)
                real_close(fd)
                os.dup2(guard_r, fd, inheritable=False)     # a second close of this number lands here
                guarded.add(fd)
                return None

            def local(frame, event, arg):
                if fired:
                    return None
                frame.f_trace_opcodes = True    # set again here: some interpreters arm it only from a local
                if event == "opcode" and frame.f_lasti not in skip:
                    hits[0] += 1
                    if hits[0] == boundary:
                        fired.append(frame.f_lineno)
                        raise KeyboardInterrupt("an injected interrupt at boundary {}".format(boundary))
                return local

            def tracer(frame, event, arg):
                if fired or frame.f_code is not code or frame.f_locals.get("keep") is None:
                    return None
                frame.f_trace_opcodes = True
                return local
            outcome = None
            leaked = set()
            prior_trace = sys.gettrace()
            try:
                baseline = _fds_open()
                with mock.patch.object(os, "close", ledger_close):
                    sys.settrace(tracer)
                    try:
                        run_adopt_transaction(root, rid, compose)
                    except KeyboardInterrupt:
                        outcome = "interrupted"
                    except AdoptApplyError:
                        outcome = "refused"
                    finally:
                        sys.settrace(prior_trace)
                leaked = _fds_open() - baseline - guarded
            finally:
                _close_held([guard_w, guard_r] + sorted(guarded, reverse=True))
            return fired, outcome, sorted(doubles), sorted(leaked)

        def compose_refused_here(ops):
            raise AdoptApplyError("an injected compose refusal")
        for name, target, failing in handoff_legs:
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                saved_umask = os.umask(0o022)
                bad = []
                boundary = 0
                control = None
                try:
                    root, files = fixture(temp)
                    os.makedirs(root / JOURNAL_REL)
                    real_acquire_now = _journal.acquire_lock
                    real_close = os.close

                    def created_then_os_failed_here(journal_root, session_id):
                        real_acquire_now(journal_root, session_id)
                        fault = OSError("an injected acquire fault after the create")
                        fault.lock_created = True
                        raise fault
                    acquire = created_then_os_failed_here if failing else real_acquire_now
                    with mock.patch.object(_journal, "acquire_lock", acquire):
                        while boundary < 4000:
                            boundary += 1
                            fired, outcome, doubles, leaked = _handoff_run(root, target, boundary,
                                                                           compose_refused_here, real_close)
                            lock_path = _journal_root(root) / "lock"
                            stays = os.path.lexists(lock_path)
                            if failing and stays:
                                os.unlink(lock_path)    # this run's created lock, which the interrupt left
                            if not fired:
                                control = (outcome, doubles, leaked, stays)
                                break
                            if outcome != "interrupted" or doubles or leaked or (stays and not failing):
                                bad.append((boundary, fired[0], outcome, doubles, leaked, stays))
                finally:
                    os.umask(saved_umask)
                check(name, 1 < boundary < 4000 and not bad and control is not None
                      and control[0] == "refused" and not control[1] and not control[2],
                      observed="boundaries={} control={!r} failed (boundary, line, outcome, closed twice, "
                               "leaked, lock stays)={!r}".format(boundary - 1, control, bad[:6]))
        # 6a'''b3c: the run's final close-out closes each list in turn and never replaces the run's own
        # outcome. An exception injected between a descriptor's pop and its close (the patched close-out
        # raises instead of closing) at the FIRST close of held_components, the journal path components
        # the first listing holds, leaves exactly that popped descriptor open: every OTHER descriptor the
        # run holds (the rest of held_components, mine_held, and anchors' jr_fd and root_fd) is still
        # closed, and the run's own refusal still propagates, naming the injected exception. With a
        # second injection at the first close of anchors, exactly the two popped descriptors stay open
        # and the refusal names BOTH, in the order they were raised. A committed run whose close-out is
        # injected at anchors (the last list, so only root_fd closes after it) still reports COMMITTED
        # (AdoptCommittedLockError, the injected exception named and chained as its cause, the
        # transaction complete), and an interrupt raised by compose still propagates as itself, the
        # injected exception named in its note. The same three outcomes stand when the injection is at
        # the EARLY lock close (mine_held, closed right after the release's read-back) and, for a refusal
        # and an interrupt, at the first close of the closing journal listing's own close-out, whose
        # exception is named beside the refusal in place of any "nothing written" claim. An injected
        # KeyboardInterrupt in the close-out of a commit or a refusal propagates as ITSELF, never wrapped
        # in AdoptCommittedLockError or dropped into the refusal text, the commit or the refusal named in
        # its note. Two injections inside the closing listing's own close-out (its first two closes)
        # keep both exceptions: two faults are both named beside the refusal in the order raised, and a
        # fault then an interrupt propagates that interrupt as ITSELF, the refusal and the fault named in
        # its note (red against the listing's first exception standing for the rest, its later ones only
        # as text notes the report drops). A refusal and a commit with no injection are the controls
        # (red against sequential
        # close-outs or a close-out that stops at its first failure: every descriptor after the
        # injection leaks; against a close-out or observation exception replacing the run's own outcome,
        # which is what the pre-fix chain, early lock close and observation re-raise did; against an
        # interrupt wrapped as an Exception's cause; and against a later exception discarded without
        # trace).
        class _InjectedCloseFault(RuntimeError):
            pass

        class _InjectedCloseInterrupt(KeyboardInterrupt):
            pass
        held_code, txn_code = _close_held_into.__code__, _run_adopt_transaction.__code__
        listing_code = _journal_listing.__code__

        def compose_interrupted_here(ops):
            raise KeyboardInterrupt("an injected compose interrupt")

        def _injected_names(targets):
            """The argument each injection in `targets` raises with: its target, and for a target named
            again its ordinal, so every injected exception has a repr of its own."""
            return [target + ("" if targets[:i + 1].count(target) == 1 else " #{}".format(
                targets[:i + 1].count(target))) for i, target in enumerate(targets)]

        def _final_close_run(root, targets, compose, faults=None):
            """One transaction over `compose` whose close-out raises, at the first close of each list
            named in `targets` (a list of the transaction's, or "_journal_listing": the closing listing's
            own close-out, its next close for each repeat), the matching class of `faults` (default
            _InjectedCloseFault each), with the matching _injected_names argument: (exception, popped
            descriptors, descriptors closed after the first injection, descriptors leaked)."""
            real_quiet, real_exc_safe = _journal._close_fd_quietly, store._close_fd_exc_safe
            armed, popped, after = list(targets), [], []
            faults = faults or (_InjectedCloseFault,) * len(targets)
            names = _injected_names(targets)

            def fault(list_name):
                popped.append(None)
                return faults[len(popped) - 1](names[len(popped) - 1])

            def faulting(real, fd):
                caller = sys._getframe(2)
                if caller.f_code is held_code:
                    fds, up = caller.f_locals.get("fds"), caller.f_back
                    while up is not None and up.f_code is not txn_code and up.f_code.co_name.startswith(
                            "_close_held"):
                        up = up.f_back
                    if (up is not None and up.f_code is listing_code and "_journal_listing" in armed
                            and up.f_back is not None and up.f_back.f_code is txn_code
                            and up.f_locals.get("keep") is None and fds is up.f_locals.get("opened")):
                        armed.remove("_journal_listing")
                        exc = fault("_journal_listing")
                        popped[-1] = fd
                        raise exc
                    if up is not None and up.f_code is txn_code:
                        for list_name in armed:
                            if fds is up.f_locals.get(list_name):
                                armed.remove(list_name)
                                exc = fault(list_name)
                                popped[-1] = fd
                                raise exc
                        if popped:
                            after.append(fd)
                return real(fd)
            raised = None
            baseline = _fds_open()
            try:
                # every close the close-out makes: the quiet close for the held lists, the #377 close for
                # anchors
                with mock.patch.object(_journal, "_close_fd_quietly", lambda fd: faulting(real_quiet, fd)), \
                        mock.patch.object(store, "_close_fd_exc_safe", lambda fd: faulting(real_exc_safe, fd)):
                    try:
                        run_adopt_transaction(root, rid, compose)
                    except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:   # an injected fault among them
                        raised = exc
                leaked = _fds_open() - baseline
            finally:
                _close_held(list(popped))   # a copy: the caller reads popped
            return raised, popped, after, sorted(leaked)
        for name, targets, kind in final_close_legs:
            control = got = None
            committed = None
            # a "-stopped" leg injects an interrupt at its LAST injection, an ordinary fault at each before
            faults = tuple(_InjectedCloseInterrupt if kind.endswith("-stopped") and i == len(targets) - 1
                           else _InjectedCloseFault for i in range(len(targets)))
            fault = faults[-1]
            for injected in ((), targets):
                with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                    saved_umask = os.umask(0o022)
                    try:
                        root, files = fixture(temp)
                        if kind.startswith("committed"):
                            compose = compose_full(files)
                        else:
                            os.makedirs(root / JOURNAL_REL)
                            compose = compose_refused_here
                            if kind == "interrupted" and injected:
                                compose = compose_interrupted_here
                        outcome = _final_close_run(root, injected, compose, faults[:len(injected)])
                        if injected:
                            got = outcome
                            committed = txn_state(root, rid) if kind.startswith("committed") else None
                        else:
                            control = outcome
                    finally:
                        os.umask(saved_umask)
            raised, popped, after, leaked = got if got is not None else (None, [], [], [])
            named = [repr(cls(each)) for cls, each in zip(faults, _injected_names(targets))]
            text = (" ".join(getattr(raised, "__notes__", [])) if isinstance(raised, KeyboardInterrupt)
                    else str(raised))
            at = [text.find(each) for each in named]
            marker = ("observation of the adoption journal RAISED" if set(targets) == {"_journal_listing"}
                      else "descriptor close-out RAISED")
            if kind == "committed":
                stands = (control is not None and control[0] is None
                          and isinstance(raised, AdoptCommittedLockError) and raised.txn == rid
                          and "COMMITTED" in text and "LANDED" in text and committed == "complete"
                          and isinstance(raised.__cause__, _InjectedCloseFault))
            elif kind == "committed-stopped":
                stands = (control is not None and control[0] is None and type(raised) is fault
                          and "COMMITTED" in text and "LANDED" in text and committed == "complete")
            elif kind == "interrupted":
                stands = (type(raised) is KeyboardInterrupt
                          and str(raised) == "an injected compose interrupt" and "did not open" in text)
            elif kind == "refused-stopped":
                stands = (type(raised) is fault and "the run's refusal stands beside this interrupt" in text
                          and "an injected compose refusal" in text)
            else:
                stands = (type(raised) is AdoptApplyError and "an injected compose refusal" in text
                          and (marker == "descriptor close-out RAISED" or _NOTHING_WRITTEN not in text))
            check(name, (kind.startswith("committed") or (control is not None
                                                           and isinstance(control[0], AdoptApplyError)))
                  and control is not None and not control[1] and not control[3] and stands
                  and marker in text and -1 not in at and at == sorted(at)
                  and len(popped) == len(targets) and leaked == sorted(popped)
                  and len(after) >= (1 if kind.startswith("committed") else 2 + len(targets) - 1),
                  observed="control (exception, leaked)={!r} exception={!r} text={!r} popped={!r} closed "
                           "after the first injection={!r} leaked={!r} state={!r}".format(
                               None if control is None else (control[0], control[3]), raised, text[-400:],
                               popped, after, leaked, committed))
        # 6a'''b3h: every close on the closing path RECORDS its exception beside one already in flight,
        # never raising past it. The closing listing's recursive walk closes each child descriptor in a
        # finally, so with <journal>/outer/inner present the inner close and then the outer close run as
        # the frames unwind: each injection makes the real close, then raises. An interrupt at the inner
        # close then a fault at the outer one, and the reverse, each propagate the interrupt as ITSELF, the
        # refusal and both exceptions named in order in its note; two faults are both named beside the
        # refusal (red against a walk child close that raises past the deeper frame's exception: the
        # outer close's exception replaces it and the inner one is named nowhere). The closing sweep's
        # parent close does the same beside an exception raised by that sweep's rmdir, in both orders. A
        # sweep close fault with the lock left in place keeps the lock's clause in the refusal (red
        # against that clause bound only after the sweep). A fault recorded by the closing listing's own
        # close-out stops the observation there: the step after it never runs and is named nowhere. A
        # run whose compose raised an ordinary exception, its closing listing's close-out interrupted,
        # propagates that interrupt with the run's own exception named in its note. No note ends with a
        # doubled "(fail-closed)". No injection leaks a descriptor: each makes its real close first.
        me = sys.modules[__name__]
        walk_code = next(c for c in listing_code.co_consts if getattr(c, "co_name", None) == "walk")
        sweep_code = _remove_journal_dirs.__code__

        def _path_fault_run(root, where, faults, compose, release_note=None):
            """One transaction over `compose` whose calls at `where` each make their real call, then raise
            in turn an instance of each class of `faults`, named for its ordinal: "walk", each child close
            of the closing listing's recursive walk (deepest first); "sweep", the closing sweep's first
            rmdir, then its parent close; "sweep-close", the sweep's first parent close. `release_note`
            stands in for _release_note when given: (exception, the injected exceptions, leaked)."""
            real_quiet, real_rmdir = _journal._close_fd_quietly, os.rmdir
            injected = []

            def inject():
                exc = faults[len(injected)]("{} #{}".format(where, len(injected) + 1))
                injected.append(exc)
                raise exc

            def faulting_quiet(fd):
                real_quiet(fd)
                up = sys._getframe(1)
                while up is not None and up.f_code.co_name.startswith("_close_held"):
                    up = up.f_back
                if len(injected) >= len(faults) or up is None:
                    return
                if where == "walk" and up.f_code is walk_code:
                    while up is not None and up.f_code is walk_code:
                        up = up.f_back
                    if (up is not None and up.f_code is listing_code and up.f_locals.get("keep") is None
                            and up.f_back is not None and up.f_back.f_code is txn_code):
                        inject()
                elif up.f_code is sweep_code and (where == "sweep-close" or (where == "sweep" and injected)):
                    inject()

            def faulting_rmdir(path, *args, **kwargs):
                real_rmdir(path, *args, **kwargs)
                if where == "sweep" and faults and not injected and sys._getframe(1).f_code is sweep_code:
                    inject()
            raised = None
            baseline = _fds_open()
            with mock.patch.object(_journal, "_close_fd_quietly", faulting_quiet), \
                    mock.patch.object(os, "rmdir", faulting_rmdir), \
                    mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {faulting_rmdir}), \
                    mock.patch.object(me, "_release_note", release_note or _release_note):
                try:
                    run_adopt_transaction(root, rid, compose)
                except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:
                    raised = exc
            return raised, injected, sorted(_fds_open() - baseline)

        def lock_stays_note(jr_fd, journal_root, mine, raised=None):
            return "stays", "an injected lock STAYS clause", None
        closing_path_legs = (
            ("closing-walk-interrupt-then-fault-propagates-as-itself", "walk",
             (_InjectedCloseInterrupt, _InjectedCloseFault), None),
            ("closing-walk-fault-then-interrupt-propagates-as-itself", "walk",
             (_InjectedCloseFault, _InjectedCloseInterrupt), None),
            ("closing-walk-two-faults-every-fault-named", "walk", (_InjectedCloseFault, _InjectedCloseFault), None),
            ("closing-sweep-interrupt-then-close-fault-propagates-as-itself", "sweep",
             (_InjectedCloseInterrupt, _InjectedCloseFault), None),
            ("closing-sweep-fault-then-close-interrupt-propagates-as-itself", "sweep",
             (_InjectedCloseFault, _InjectedCloseInterrupt), None),
            ("closing-sweep-close-fault-keeps-the-lock-clause", "sweep-close", (_InjectedCloseFault,),
             lock_stays_note))
        for name, where, faults, release_note in closing_path_legs:
            control = got = None
            for injected in ((), faults):
                with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                    saved_umask = os.umask(0o022)
                    try:
                        root, files = fixture(temp)
                        if where == "walk":
                            os.makedirs(root / JOURNAL_REL / "outer" / "inner")
                        outcome = _path_fault_run(root, where, injected, compose_refused_here, release_note)
                        if injected:
                            got = outcome
                        else:
                            control = outcome
                    finally:
                        os.umask(saved_umask)
            raised, injected, leaked = got if got is not None else (None, [], [])
            stop = next((exc for exc in injected if not isinstance(exc, Exception)), None)
            text = (" ".join(getattr(raised, "__notes__", [])) if isinstance(raised, KeyboardInterrupt)
                    else str(raised))
            at = [text.find(repr(exc)) for exc in injected]
            if stop is not None:
                stands = raised is stop and "the run's refusal stands beside this interrupt" in text
            else:
                stands = type(raised) is AdoptApplyError and _NOTHING_WRITTEN not in text
            check(name, control is not None and type(control[0]) is AdoptApplyError and not control[2]
                  and (release_note is None or "an injected lock STAYS clause" in str(control[0]))
                  and stands and len(injected) == len(faults) and "an injected compose refusal" in text
                  and "observation of the adoption journal RAISED" in text
                  and -1 not in at and at == sorted(at) and "(fail-closed) (fail-closed)" not in text
                  and (release_note is None or "an injected lock STAYS clause" in text) and not leaked,
                  observed="control (exception, leaked)={!r} exception={!r} injected={!r} text={!r} "
                           "leaked={!r}".format(None if control is None else (control[0], control[2]),
                                                raised, injected, text[-600:], leaked))
        reached = []

        def unbound_after_listing(jr_fd, held, after):
            reached.append("the observation step after the closing listing")
            raise _InjectedCloseFault(reached[-1])

        def compose_errored_here(ops):
            raise RuntimeError("an injected compose error")

        def compose_errored_noted(ops):
            error = RuntimeError("an injected compose error")
            error.add_note("an injected note on the compose error")
            raise error
        stops_got = own_got = noted_got = None
        for leg in ("stops", "own", "noted"):
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                saved_umask = os.umask(0o022)
                try:
                    root, files = fixture(temp)
                    os.makedirs(root / JOURNAL_REL)
                    if leg == "stops":
                        with mock.patch.object(me, "_journal_unbound", unbound_after_listing):
                            stops_got = _final_close_run(root, ("_journal_listing",), compose_refused_here)
                    elif leg == "own":
                        own_got = _final_close_run(root, ("_journal_listing",), compose_errored_here,
                                                   (_InjectedCloseInterrupt,))
                    else:
                        noted_got = _final_close_run(root, ("_journal_listing",), compose_errored_noted,
                                                     (_InjectedCloseInterrupt,))
                finally:
                    os.umask(saved_umask)
        raised, popped, _after, leaked = stops_got if stops_got is not None else (None, [], [], [])
        text = str(raised)
        check("closing-listing-close-fault-stops-the-observation",
              type(raised) is AdoptApplyError and not reached
              and repr(_InjectedCloseFault("_journal_listing")) in text
              and "observation of the adoption journal RAISED an exception" in text
              and len(popped) == 1 and leaked == sorted(popped),
              observed="exception={!r} later step reached={!r} popped={!r} leaked={!r}".format(
                  raised, reached, popped, leaked))
        raised, popped, _after, leaked = own_got if own_got is not None else (None, [], [], [])
        text = " ".join(getattr(raised, "__notes__", []))
        check("closing-listing-interrupt-names-the-own-exception",
              type(raised) is _InjectedCloseInterrupt
              and "the run's own exception stands beside this interrupt: {!r}".format(
                  RuntimeError("an injected compose error")) in text
              and repr(_InjectedCloseInterrupt("_journal_listing")) in text
              and not text.endswith("(fail-closed) (fail-closed)")
              and len(popped) == 1 and leaked == sorted(popped),
              observed="exception={!r} note={!r} popped={!r} leaked={!r}".format(raised, text[-600:], popped,
                                                                                  leaked))
        # the run's own exception named beside that interrupt with what is recorded on it (red against it
        # named by its repr alone, its note then dropped from the report)
        raised, popped, _after, leaked = noted_got if noted_got is not None else (None, [], [], [])
        text = " ".join(getattr(raised, "__notes__", []))
        check("closing-listing-interrupt-names-the-own-exception-notes",
              type(raised) is _InjectedCloseInterrupt
              and "[recorded on it: an injected note on the compose error]" in text
              and len(popped) == 1 and leaked == sorted(popped),
              observed="exception={!r} note={!r} popped={!r} leaked={!r}".format(raised, text[-600:], popped,
                                                                                  leaked))
        # 6a'''b3i: every close on a lock read and release path RECORDS its exception in the run's close-out
        # list, never raising past one already in flight nor in place of an outcome already read. Each
        # injection makes the real call first, then raises: an interrupt inside the release, then a fault
        # at the read-back's close, propagates the interrupt as ITSELF with the fault named in its note, on
        # a refusal and on a commit (which still says COMMITTED); the same beside an interrupt inside
        # acquire (the observed lock state's read close), beside an interrupt inside the release of a lock
        # a failed acquire created (that release's read-back close), beside an interrupt at the failed
        # acquire's owner check (the held identity descriptor's close), and beside an interrupt inside the
        # read of the identity this run's acquire wrote, on the transaction's own read and on the failed
        # acquire's (red against each close raising past the interrupt: the fault replaces it). A release
        # note that raised leaves its lock clause in the refusal through a sweep close fault (red against
        # the state's clause bound only after the sweep). A walk or sweep close fault with nothing in
        # flight stops that walk or sweep at once: no later child is opened, no later rmdir runs (red
        # against those own-raise lines removed). No injection leaks a descriptor.
        identity_code, outcome_code = _lock_identity.__code__, _release_outcome.__code__
        failed_code, interrupted_code = _failed_lock_state.__code__, _interrupted_lock_state.__code__

        def _called_from(frame, codes):
            while frame is not None and frame.f_code.co_name.startswith("_close_held"):
                frame = frame.f_back
            for code in codes:
                if frame is None or frame.f_code is not code:
                    return False
                frame = frame.f_back
            return True

        def _lock_read_run(root, compose, at, read_at=None, acquire=None, release=False, owner=False):
            """One transaction over `compose` whose quiet close called from the frames `at` (innermost first)
            makes its real close, then raises an injected fault, once; `read_at` likewise interrupts the
            lock read there after the real read; `acquire` (an exception factory) raises after the real
            acquire; `release` interrupts the release before it unlinks; `owner` interrupts the failed
            acquire's owner check. (exception, the injected exceptions in order, leaked)."""
            real_quiet, real_read = _journal._close_fd_quietly, _journal._read_fd
            real_acquire, real_owner = _journal.acquire_lock, _journal._owner_is_current
            fired = []

            def fire(exc):
                fired.append(exc)
                raise exc

            def quiet(fd):
                real_quiet(fd)
                if not any(isinstance(e, _InjectedCloseFault) for e in fired) and _called_from(
                        sys._getframe(1), at):
                    fire(_InjectedCloseFault("lock read close #{}".format(len(fired) + 1)))

            def reading(fd, *args, **kwargs):
                data = real_read(fd, *args, **kwargs)
                if read_at is not None and all(isinstance(e, Exception) for e in fired) and _called_from(sys._getframe(1), read_at):
                    fire(_InjectedCloseInterrupt("lock read #{}".format(len(fired) + 1)))
                return data

            def acquiring(journal_root, session):
                real_acquire(journal_root, session)
                if acquire is not None:
                    fire(acquire())

            def releasing(journal_root):
                fire(_InjectedCloseInterrupt("release #{}".format(len(fired) + 1)))

            def owning(owner_row):
                if owner:
                    fire(_InjectedCloseInterrupt("owner check #{}".format(len(fired) + 1)))
                return real_owner(owner_row)
            raised = None
            baseline = _fds_open()
            with mock.patch.object(_journal, "_close_fd_quietly", quiet), \
                    mock.patch.object(_journal, "_read_fd", reading), \
                    mock.patch.object(_journal, "acquire_lock", acquiring), \
                    mock.patch.object(_journal, "_owner_is_current", owning), \
                    mock.patch.object(_journal, "release_lock", releasing if release else _journal.release_lock):
                try:
                    run_adopt_transaction(root, rid, compose)
                except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:
                    raised = exc
            return raised, fired, sorted(_fds_open() - baseline)

        def acquire_failed():
            exc = OSError(5, "an injected acquire EIO after the lock was created")
            exc.lock_created = True
            return exc

        def acquire_interrupted():
            return _InjectedCloseInterrupt("acquire #1")

        def compose_committed(ops):
            return None
        lock_read_legs = (
            ("release-readback-close-fault-keeps-the-release-interrupt-refused", compose_refused_here,
             dict(at=(identity_code, outcome_code), release=True), False),
            ("release-readback-close-fault-keeps-the-release-interrupt-committed", compose_committed,
             dict(at=(identity_code, outcome_code), release=True), True),
            ("acquire-interrupt-lock-read-close-fault-keeps-the-interrupt", compose_refused_here,
             dict(at=(identity_code, interrupted_code), acquire=acquire_interrupted), False),
            ("failed-acquire-readback-close-fault-keeps-the-release-interrupt", compose_refused_here,
             dict(at=(identity_code, outcome_code, failed_code), acquire=acquire_failed, release=True), False),
            ("failed-acquire-held-identity-close-fault-keeps-the-owner-interrupt", compose_refused_here,
             dict(at=(failed_code,), acquire=acquire_failed, owner=True), False),
            ("failed-acquire-identity-read-close-fault-keeps-the-read-interrupt", compose_refused_here,
             dict(at=(identity_code, failed_code), read_at=(identity_code, failed_code), acquire=acquire_failed),
             False),
            ("own-identity-read-close-fault-keeps-the-read-interrupt", compose_refused_here,
             dict(at=(identity_code, txn_code), read_at=(identity_code, txn_code)), False))
        for name, compose_here, how, committed in lock_read_legs:
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                saved_umask = os.umask(0o022)
                try:
                    root, files = fixture(temp)
                    raised, fired, leaked = _lock_read_run(root, compose_here, **how)
                finally:
                    os.umask(saved_umask)
            stop = next((exc for exc in fired if not isinstance(exc, Exception)), None)
            fault = next((exc for exc in fired if isinstance(exc, _InjectedCloseFault)), None)
            text = " ".join(getattr(raised, "__notes__", []) or [])
            check(name, stop is not None and fault is not None and raised is stop and repr(fault) in text
                  and (not committed or "COMMITTED" in text) and not leaked,
                  observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                      raised, fired, text[-600:], leaked))

        def release_note_raised(jr_fd, journal_root, mine, raised=None):
            raise RuntimeError("an injected release error")
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            try:
                root, files = fixture(temp)
                raised, injected, leaked = _path_fault_run(root, "sweep-close", (_InjectedCloseFault,),
                                                           compose_refused_here, release_note_raised)
            finally:
                os.umask(saved_umask)
        check("closing-sweep-close-fault-keeps-the-state-lock-clause",
              type(raised) is AdoptApplyError and len(injected) == 1 and repr(injected[0]) in str(raised)
              and "this run's journal lock outcome was not observed" in str(raised) and not leaked,
              observed="exception={!r} injected={!r} leaked={!r}".format(raised, injected, leaked))
        for name, where in (("closing-walk-close-fault-stops-the-walk", "walk"),
                            ("closing-sweep-close-fault-stops-the-sweep", "sweep")):
            counted = []
            real_quiet_here, real_rmdir_here = _journal._close_fd_quietly, os.rmdir

            def counting_quiet(fd):
                real_quiet_here(fd)
                up = sys._getframe(1)
                while up is not None and up.f_code.co_name.startswith("_close_held"):
                    up = up.f_back
                if where == "walk" and up is not None and up.f_code is walk_code:
                    while up is not None and up.f_code is walk_code:
                        up = up.f_back
                    if (up is not None and up.f_code is listing_code and up.f_locals.get("keep") is None
                            and up.f_back is not None and up.f_back.f_code is txn_code):
                        counted.append(fd)
                        if len(counted) == 1:
                            raise _InjectedCloseFault("{} close #1".format(where))
                elif where == "sweep" and up is not None and up.f_code is sweep_code and len(counted) == 1:
                    counted.append(fd)
                    raise _InjectedCloseFault("{} close #1".format(where))

            def counting_rmdir(path, *args, **kwargs):
                real_rmdir_here(path, *args, **kwargs)
                if sys._getframe(1).f_code is sweep_code:
                    counted.append(path)
            raised = None
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                saved_umask = os.umask(0o022)
                try:
                    root, files = fixture(temp)
                    if where == "walk":
                        os.makedirs(root / JOURNAL_REL / "a")
                        os.makedirs(root / JOURNAL_REL / "b")
                    baseline = _fds_open()
                    with mock.patch.object(_journal, "_close_fd_quietly", counting_quiet), \
                            mock.patch.object(os, "rmdir", counting_rmdir), \
                            mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {counting_rmdir}):
                        try:
                            run_adopt_transaction(root, rid, compose_refused_here)
                        except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:
                            raised = exc
                    leaked = sorted(_fds_open() - baseline)
                finally:
                    os.umask(saved_umask)
            # the walk: one child close only (the other child is never opened); the sweep: its first rmdir,
            # then its parent close, and no later rmdir
            check(name, type(raised) is AdoptApplyError and len(counted) == (1 if where == "walk" else 2)
                  and repr(_InjectedCloseFault("{} close #1".format(where))) in str(raised) and not leaked,
                  observed="exception={!r} counted={!r} leaked={!r}".format(raised, counted, leaked))
        # 6a'''b3j: the closes INSIDE the _journal helpers the release paths call (read_lock_owner_at's,
        # read_lock_owner's, _open_parent's, _pid_start's) never raise past an exception in flight, and the
        # run records what its release or a failed acquire's lock read raises beside an outcome already in
        # flight. Each injection makes the real close first, then raises. An interrupt at read_lock_owner_at's
        # close, then a fault at read_lock_owner's, propagates the interrupt as ITSELF with the fault named, on
        # a refusal (its reason named) and on a commit (COMMITTED named) (red against _close_fd_yielding
        # raising a non-OSError past the interrupt). A fault at read_lock_owner_at's close beside a body
        # interrupt leaves the body interrupt propagating (red against the run raising its release's
        # exception in place of the outcome in flight). A release interrupt beside a refusal keeps the
        # refusal's reason in its note (the same red). A fault at the failed acquire's owner read keeps the
        # acquire's error in the refusal (red against _failed_lock_state letting it escape). _open_parent's
        # cleanup close fault propagates past the FileNotFoundError in flight (that error noted on it and
        # kept as its context, never left only as a note on it) and closes the rest; _pid_start's close
        # fault keeps the interrupt in its read. A missing ancestor of several listed files is opened ONCE
        # (red against the missing-ancestor cache removed). No injection leaks a descriptor.
        rlo_at_code, rlo_code = _journal.read_lock_owner_at.__code__, _journal.read_lock_owner.__code__

        def _helper_close_run(root, compose, plan, under, release=None, acquire=None):
            """One transaction whose _journal propagating close called (through _close_fd_yielding) from the
            frame whose code is a key of `plan`, beneath the frame `under`, makes its real close, then raises
            plan[code]() once. (exception, the injected exceptions in order, leaked)."""
            real_prop, real_acquire = _journal._close_fd_propagating, _journal.acquire_lock
            fired = []

            def prop(fd):
                real_prop(fd)
                up = sys._getframe(2)
                make = plan.get(up.f_code)
                frame = up
                while frame is not None and frame.f_code is not under:
                    frame = frame.f_back
                if make is not None and frame is not None and up.f_code not in [c for c, _ in fired]:
                    exc = make()
                    fired.append((up.f_code, exc))
                    raise exc

            def acquiring(journal_root, session):
                real_acquire(journal_root, session)
                if acquire is not None:
                    raise acquire()
            raised = None
            baseline = _fds_open()
            with mock.patch.object(_journal, "_close_fd_propagating", prop), \
                    mock.patch.object(_journal, "acquire_lock", acquiring), \
                    mock.patch.object(_journal, "release_lock", release or _journal.release_lock):
                try:
                    run_adopt_transaction(root, rid, compose)
                except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:
                    raised = exc
            return raised, [e for _, e in fired], sorted(_fds_open() - baseline)

        def compose_interrupted(ops):
            raise _InjectedCloseInterrupt("body")

        def release_interrupted(journal_root):
            raise _InjectedCloseInterrupt("release")

        def helper_fault():
            return _InjectedCloseFault("helper journal close")

        def helper_interrupt():
            return _InjectedCloseInterrupt("helper lock close")
        helper_legs = (
            ("journal-helper-close-fault-keeps-the-release-interrupt-refused", compose_refused_here,
             dict(plan={rlo_at_code: helper_interrupt, rlo_code: helper_fault}, under=outcome_code),
             lambda raised, fired, text: raised is fired[0] and repr(fired[1]) in text
             and "an injected compose refusal" in text),
            ("journal-helper-close-fault-keeps-the-release-interrupt-committed", compose_committed,
             dict(plan={rlo_at_code: helper_interrupt, rlo_code: helper_fault}, under=outcome_code),
             lambda raised, fired, text: raised is fired[0] and repr(fired[1]) in text and "COMMITTED" in text),
            ("journal-helper-close-fault-keeps-the-body-interrupt", compose_interrupted,
             dict(plan={rlo_at_code: helper_fault}, under=outcome_code),
             lambda raised, fired, text: type(raised) is _InjectedCloseInterrupt and str(raised) == "body"
             and repr(fired[0]) in text),
            ("journal-helper-close-faults-beside-the-body-interrupt-all-named", compose_interrupted,
             dict(plan={rlo_at_code: helper_interrupt, rlo_code: helper_fault}, under=outcome_code),
             lambda raised, fired, text: type(raised) is _InjectedCloseInterrupt and str(raised) == "body"
             and repr(fired[0]) in text and repr(fired[1]) in text),
            ("release-interrupt-keeps-the-refusal-reason", compose_refused_here,
             dict(plan={}, under=outcome_code, release=release_interrupted),
             lambda raised, fired, text: type(raised) is _InjectedCloseInterrupt and str(raised) == "release"
             and "an injected compose refusal" in text),
            ("failed-acquire-owner-read-close-fault-keeps-the-acquire-error", compose_refused_here,
             dict(plan={rlo_at_code: helper_fault}, under=failed_code, acquire=acquire_failed),
             lambda raised, fired, text: type(raised) is AdoptApplyError and len(fired) == 1
             and "an injected acquire EIO" in str(raised) and repr(fired[0]) in str(raised)))
        for name, compose_here, how, holds in helper_legs:
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                saved_umask = os.umask(0o022)
                try:
                    root, files = fixture(temp)
                    raised, fired, leaked = _helper_close_run(root, compose_here, **how)
                finally:
                    os.umask(saved_umask)
            text = " ".join(getattr(raised, "__notes__", []) or [])
            check(name, len(fired) == len(how["plan"]) and holds(raised, fired, text) and not leaked,
                  observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                      raised, fired, text[-600:], leaked))
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            os.makedirs(os.path.join(temp, "a", "b"))
            real_quiet_here, fired = _journal._close_fd_quietly, []
            raised = None

            def faulting_quiet(fd):
                real_quiet_here(fd)
                if not fired:
                    fired.append(_InjectedCloseFault("open-parent close"))
                    raise fired[0]
            tfd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                baseline = _fds_open()
                with mock.patch.object(_journal, "_close_fd_quietly", faulting_quiet):
                    try:
                        _journal._open_parent(tfd, "a/b/absent/x")
                    except (FileNotFoundError, RuntimeError) as exc:
                        raised = exc
                leaked = sorted(_fds_open() - baseline)
            finally:
                os.close(tfd)
        check("journal-open-parent-close-fault-propagates-past-the-missing-component",
              bool(fired) and raised is fired[0] and isinstance(raised.__context__, FileNotFoundError)
              and "FileNotFoundError" in " ".join(getattr(raised, "__notes__", []) or []) and not leaked,
              observed="exception={!r} injected={!r} leaked={!r}".format(raised, fired, leaked))
        # 6a'''b3k (D-U10-SECOND-FAULT): the run's FIRST interrupt propagates as itself, and every close
        # exception is named where a reader sees it, never only in a note on an exception a caller drops.
        # Each injection makes the real close first, then raises. A fault then an interrupt at the closes of
        # one close-out (_close_held, _open_parent's cleanup directly and under the refused run's sweep)
        # raise the INTERRUPT, the fault noted on it (red against the first exception raised, the interrupt
        # demoted to a note). A cleanup close fault beneath a missing parent propagates out of the sweep and
        # out of _lstat_contained (red against it noted on the FileNotFoundError they read as absent). A
        # report names what is recorded on each exception it names (red against repr alone), and an
        # interrupt then a fault at the release's two lock reads beside a body interrupt both stand named.
        open_parent_code, sweep_code = _journal._open_parent.__code__, _remove_journal_dirs.__code__

        def _quiet_plan(plan, under):
            """A _journal._close_fd_quietly that makes its real close, then, called beneath _open_parent's
            frame and (unless None) the frame whose code is `under`, raises the next plan entry once.
            (the patched close, the injected exceptions in order)."""
            real_quiet_k, fired_k = _journal._close_fd_quietly, []

            def quiet(fd):
                real_quiet_k(fd)
                codes, frame = set(), sys._getframe(1)
                while frame is not None:
                    codes.add(frame.f_code)
                    frame = frame.f_back
                if len(fired_k) < len(plan) and open_parent_code in codes and (under is None or under in codes):
                    fired_k.append(plan[len(fired_k)]())
                    raise fired_k[-1]
            return quiet, fired_k
        fault_then_interrupt = (lambda: _InjectedCloseFault("cleanup close #1"),
                                lambda: _InjectedCloseInterrupt("cleanup close #2"))
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            os.makedirs(os.path.join(temp, "a", "b", "c"))
            quiet, fired = _quiet_plan(fault_then_interrupt, None)
            raised = None
            walk_fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                baseline = _fds_open()
                with mock.patch.object(_journal, "_close_fd_quietly", quiet):
                    try:
                        _journal._open_parent(walk_fd, "a/b/c/x")
                    except (RuntimeError, KeyboardInterrupt) as exc:
                        raised = exc
                leaked = sorted(_fds_open() - baseline)
            finally:
                os.close(walk_fd)
        check("journal-open-parent-first-interrupt-propagates-as-itself",
              len(fired) == 2 and raised is fired[1] and repr(fired[0]) in " ".join(
                  getattr(raised, "__notes__", []) or []) and not leaked,
              observed="exception={!r} injected={!r} leaked={!r}".format(raised, fired, leaked))
        pair, closing, raised = [os.open(os.devnull, os.O_RDONLY) for _ in range(2)], [], None
        real_quiet_c = _journal._close_fd_quietly

        def quiet_pair(fd):
            real_quiet_c(fd)
            if len(closing) < 2:
                closing.append(fault_then_interrupt[len(closing)]())
                raise closing[-1]
        baseline = _fds_open()
        with mock.patch.object(_journal, "_close_fd_quietly", quiet_pair):
            try:
                _close_held(pair)
            except (RuntimeError, KeyboardInterrupt) as exc:
                raised = exc
        leaked = sorted(_fds_open() - baseline)
        check("close-out-first-interrupt-propagates-as-itself",
              len(closing) == 2 and raised is closing[1] and repr(closing[0]) in " ".join(
                  getattr(raised, "__notes__", []) or []) and not pair and not leaked,
              observed="exception={!r} injected={!r} leaked={!r}".format(raised, closing, leaked))
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            raised = None
            try:
                root, files = fixture(temp)
                quiet, fired = _quiet_plan(fault_then_interrupt, sweep_code)
                baseline = _fds_open()
                with mock.patch.object(_journal, "_close_fd_quietly", quiet):
                    try:
                        run_adopt_transaction(root, rid, compose_refused_here)
                    except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:
                        raised = exc
                leaked = sorted(_fds_open() - baseline)
            finally:
                os.umask(saved_umask)
        text = " ".join(getattr(raised, "__notes__", []) or [])
        check("sweep-close-first-interrupt-propagates-as-itself",
              len(fired) == 2 and raised is fired[1] and repr(fired[0]) in text
              and "an injected compose refusal" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired, text[-600:], leaked))
        for name, under, call in (
                ("sweep-missing-parent-close-fault-is-not-discarded", sweep_code,
                 lambda base_fd: _remove_journal_dirs(base_fd, ["x/missing/z"], raised=[])),
                ("lstat-missing-parent-close-fault-is-not-discarded", _journal._lstat_contained.__code__,
                 lambda base_fd: _journal._lstat_contained(base_fd, "x/missing/z"))):
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                os.makedirs(os.path.join(temp, "x"))
                quiet, fired = _quiet_plan(fault_then_interrupt[:1], under)
                raised = returned = None
                miss_fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    baseline = _fds_open()
                    with mock.patch.object(_journal, "_close_fd_quietly", quiet):
                        try:
                            returned = call(miss_fd)
                        except RuntimeError as exc:
                            raised = exc
                    leaked = sorted(_fds_open() - baseline)
                finally:
                    os.close(miss_fd)
            check(name, len(fired) == 1 and raised is fired[0]
                  and isinstance(raised.__context__, FileNotFoundError) and not leaked,
                  observed="exception={!r} returned={!r} injected={!r} leaked={!r}".format(
                      raised, returned, fired, leaked))
        carrier = _InjectedCloseInterrupt("carrier")
        carrier.add_note("a close raised {!r} while this interrupt was in flight".format(
            _InjectedCloseFault("noted close fault")))
        for name, said in (("close-out-report-names-what-is-noted", _close_out_said([carrier])),
                           ("observation-report-names-what-is-noted", _observation_raised_said([carrier])),
                           ("release-report-names-what-is-noted", _release_raised_said([carrier]))):
            check(name, repr(_InjectedCloseFault("noted close fault")) in (said or ""), observed=said)
        # 6a'''b3l (D-U10-SECOND-FAULT, round 14): every close the transaction reaches resolves through the
        # one first-interrupt selection (_journal._first_interrupt), so the FIRST interrupt propagates as
        # itself. Each injection makes the real close first, then raises. The store probe's close with the
        # discovery's interrupt in flight keeps that interrupt, the close fault noted on it (red against
        # the fault replacing it); with nothing in flight its close error is the one raised (#377).
        import errno
        probe_code, held_into_code = _default_store_present_without_manifest.__code__, _close_held_into.__code__
        identity_code, outcome_code = _lock_identity.__code__, _release_outcome.__code__
        real_exc_safe_p = store._close_fd_exc_safe
        for name, inject in (("store-probe-close-fault-keeps-the-first-interrupt", "fault"),
                             ("store-probe-close-error-raised-with-nothing-in-flight", "eio")):
            discovery_intr = _InjectedCloseInterrupt("discovery interrupt")
            injected, raised = [], None

            def discover(*args, inject=inject, discovery_intr=discovery_intr, **kwargs):
                if inject == "fault":
                    raise discovery_intr
                return "absent", None, ""

            def probe_close(fd, inject=inject, injected=injected):
                real_exc_safe_p(fd)
                if sys._getframe(1).f_code is probe_code and not injected:
                    injected.append(_InjectedCloseFault("probe close fault") if inject == "fault"
                                    else OSError(errno.EIO, "probe close EIO"))
                    raise injected[-1]
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                baseline = _fds_open()
                with mock.patch.object(store, "discover_machine_store", discover), \
                        mock.patch.object(store, "_close_fd_exc_safe", probe_close):
                    try:
                        _default_store_present_without_manifest(temp)
                    except (RuntimeError, OSError, KeyboardInterrupt) as exc:
                        raised = exc
                leaked = sorted(_fds_open() - baseline)
            want = discovery_intr if inject == "fault" else (injected[0] if injected else None)
            check(name, len(injected) == 1 and raised is want and not leaked and (
                inject != "fault" or repr(injected[0]) in " ".join(getattr(raised, "__notes__", []) or [])),
                  observed="exception={!r} injected={!r} leaked={!r}".format(raised, injected, leaked))

        def ordered_run(sweep_plan, anchor_plan=(), read_plan=(), release_plan=(), release_real=True,
                        acquire_plan=(), compose=compose_refused_here, mine_none=False):
            """One run over `compose` (a refusal by default) with exceptions injected, each once and in order:
            at the sweep's _open_parent cleanup close (`sweep_plan`), at the final close-out's anchor
            closes (`anchor_plan`, once the sweep's has fired), as a lock read's _read_fd under
            _lock_identity (`read_plan`, each entry (the code _lock_identity must be called from, factory)),
            and right after the lock release (`release_plan`) or the lock acquire (`acquire_plan`); with
            `release_real` False the release leaves the lock in place, so its read-back reads it; with
            `mine_none` the run's own lock identity read finds no lock, so `mine` is None. (the
            propagating exception, its notes' text, every injected exception, the descriptors leaked)."""
            fired_all = []
            quiet_o, fired_o = _quiet_plan(sweep_plan, sweep_code)
            real_read_o, real_release_o = _journal._read_fd, _journal.release_lock
            real_acquire_o = _journal.acquire_lock
            reads, releases, anchored = list(read_plan), list(release_plan), list(anchor_plan)
            acquires = list(acquire_plan)

            def anchor_close(fd):
                real_exc_safe_p(fd)
                if anchored and (fired_o or not sweep_plan) and sys._getframe(1).f_code is held_into_code:
                    fired_all.append(anchored.pop(0)())
                    raise fired_all[-1]

            def read_fd(fd, *args, **kwargs):
                frame = sys._getframe(1)
                if reads and frame.f_code is identity_code and frame.f_back.f_code is reads[0][0]:
                    fired_all.append(reads.pop(0)[1]())
                    raise fired_all[-1]
                return real_read_o(fd, *args, **kwargs)

            def release_lock(*args, **kwargs):
                if release_real:
                    real_release_o(*args, **kwargs)
                if releases:
                    fired_all.append(releases.pop(0)())
                    raise fired_all[-1]

            def acquire_lock(*args, **kwargs):
                real_acquire_o(*args, **kwargs)
                if acquires:
                    fired_all.append(acquires.pop(0)())
                    raise fired_all[-1]
            real_identity_o = _lock_identity

            def identity_unavailable(jr_fd, keep=None, raised=None):
                # the run's own identity read (the one with `keep`) finds no lock, as when the lock is
                # briefly absent there; it then restores the real one, so every later read calls it directly
                # and its frames stay what read_plan matches
                setattr(me, "_lock_identity", real_identity_o)
                return None if keep is not None else real_identity_o(jr_fd, keep, raised)
            raised_o = None
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp_o:
                saved_umask = os.umask(0o022)
                try:
                    root_o, _files_o = fixture(temp_o)
                    baseline_o = _fds_open()
                    with mock.patch.object(_journal, "_close_fd_quietly", quiet_o), \
                            mock.patch.object(store, "_close_fd_exc_safe", anchor_close), \
                            mock.patch.object(_journal, "_read_fd", read_fd), \
                            mock.patch.object(_journal, "release_lock", release_lock), \
                            mock.patch.object(_journal, "acquire_lock", acquire_lock), \
                            mock.patch.object(me, "_lock_identity",
                                              identity_unavailable if mine_none else real_identity_o):
                        try:
                            run_adopt_transaction(root_o, rid, compose)
                        except (Exception, KeyboardInterrupt) as exc:     # noqa: BLE001  checked below
                            raised_o = exc
                    leaked_o = sorted(_fds_open() - baseline_o)
                finally:
                    os.umask(saved_umask)
            return raised_o, " ".join(getattr(raised_o, "__notes__", []) or []), fired_o + fired_all, leaked_o
        # a sweep interrupt then an anchor close-out interrupt: the sweep's, the first, propagates as
        # itself, the later one and the refusal named in its note (red against the final close-out's
        # recorded exceptions read ahead of the sweep's, the later interrupt propagating)
        raised, text, fired, leaked = ordered_run(
            (lambda: _InjectedCloseInterrupt("sweep interrupt"),),
            anchor_plan=(lambda: _InjectedCloseInterrupt("anchor close-out interrupt"),))
        check("sweep-interrupt-then-close-out-interrupt-propagates-the-first",
              len(fired) == 2 and raised is fired[0] and repr(fired[1]) in text
              and "an injected compose refusal" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired, text[-600:], leaked))
        # an interrupt the lock read's close records BEFORE the release (that read failed, so the run
        # refuses), then one right after the release: the first propagates as itself (red against the
        # release's exceptions read ahead of what the close-outs recorded before the release)
        real_quiet_r, fired_r = _journal._close_fd_quietly, []

        def quiet_r(fd):
            real_quiet_r(fd)
            frame = sys._getframe(1)
            while frame is not None and frame.f_code is not held_into_code:
                frame = frame.f_back
            if not fired_r and frame is not None and frame.f_back.f_code is identity_code \
                    and frame.f_back.f_back.f_code is _run_adopt_transaction.__code__:
                fired_r.append(_InjectedCloseInterrupt("lock read close interrupt"))
                raise fired_r[-1]
        with mock.patch.object(_journal, "_close_fd_quietly", quiet_r):
            raised, text, fired, leaked = ordered_run(
                (), read_plan=((_run_adopt_transaction.__code__, lambda: OSError(errno.EIO, "lock read EIO")),),
                release_plan=(lambda: _InjectedCloseInterrupt("release interrupt"),))
        check("lock-read-close-interrupt-before-the-release-propagates-first",
              len(fired_r) == 1 and len(fired) == 2 and raised is fired_r[0] and repr(fired[1]) in text
              and "cannot read back the adoption journal lock" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired_r + fired, text[-600:], leaked))
        # the release's read-back interrupted with the refusal in flight: that interrupt propagates as
        # itself, the refusal and the release's record named in its note (red against it raised in place
        # of that record, the refusal then unnamed: the round-12 carried-over A2)
        raised, text, fired, leaked = ordered_run(
            (), read_plan=((outcome_code, lambda: _InjectedCloseInterrupt("read-back interrupt")),),
            release_real=False)
        check("release-read-back-interrupt-beside-the-refusal-is-recorded",
              len(fired) == 1 and raised is fired[0] and "an injected compose refusal" in text
              and "lock release or read RAISED" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired, text[-600:], leaked))
        # an interrupt in the release, then another in its read-back: the release's, the first,
        # propagates as itself, the read-back's named beside it (red against the read-back's escaping
        # _release_outcome, the release's then dropped without trace)
        raised, text, fired, leaked = ordered_run(
            (), read_plan=((outcome_code, lambda: _InjectedCloseInterrupt("read-back interrupt")),),
            release_plan=(lambda: _InjectedCloseInterrupt("release interrupt"),), release_real=False)
        check("release-interrupt-then-read-back-interrupt-propagates-the-first",
              len(fired) == 2 and raised is not None and raised.args == ("release interrupt",)
              and any(exc is raised for exc in fired) and "read-back interrupt" in text
              and "an injected compose refusal" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired, text[-600:], leaked))
        # an ORDINARY fault in the release (a close inside it), then an interrupt in its read-back: the
        # read-back's interrupt is the first interrupt and propagates as itself, the release's fault named
        # beside it, on a refusal and on a commit (which still says COMMITTED) (red against the release's
        # fault held as the interrupt, the read-back's then demoted to the unreadable clause: round 14)
        for name, compose_m, said in (
                ("release-fault-then-read-back-interrupt-propagates-the-interrupt-refused",
                 compose_refused_here, "an injected compose refusal"),
                ("release-fault-then-read-back-interrupt-propagates-the-interrupt-committed",
                 compose_committed, "COMMITTED")):
            raised, text, fired, leaked = ordered_run(
                (), read_plan=((outcome_code, lambda: _InjectedCloseInterrupt("read-back interrupt")),),
                release_plan=(lambda: _InjectedCloseFault("release close fault"),), release_real=False,
                compose=compose_m)
            check(name, len(fired) == 2 and isinstance(fired[0], _InjectedCloseFault) and raised is fired[1]
                  and repr(fired[0]) in text and said in text and not leaked,
                  observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                      raised, fired, text[-600:], leaked))
        # an interrupt in the release, then an ordinary fault in its read-back: the release's interrupt,
        # the first, still propagates as itself, the read-back's fault named beside it
        raised, text, fired, leaked = ordered_run(
            (), read_plan=((outcome_code, lambda: _InjectedCloseFault("read-back fault")),),
            release_plan=(lambda: _InjectedCloseInterrupt("release interrupt"),), release_real=False)
        check("release-interrupt-then-read-back-fault-propagates-the-interrupt",
              len(fired) == 2 and raised is fired[0] and repr(fired[1]) in text
              and "an injected compose refusal" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired, text[-600:], leaked))
        # 6a'''b3m (D-U10-SECOND-FAULT, round 16): _release_outcome's OUTCOME TABLE, one vector per row.
        # The release raises R and its read-back B, each nothing, "own" (OSError, JournalError), "ordinary"
        # or "interrupt"; this run's lock identity is known or unavailable (`mine` None); the run commits
        # or refuses. The release leaves the lock in place, so the read-back reads it. Each row requires
        # the table's stop (written out here, not computed by the code under test): on a commit stop
        # propagates as itself, else AdoptCommittedLockError; on a refusal an interrupt stop propagates
        # as itself, else the refusal stands. In every row EVERY injected exception is named in the full
        # rendering (message, notes, chain), with COMMITTED or the refusal and the state's clause, and no
        # descriptor leaks (red against an ordinary or own release exception dropped on "unidentified",
        # and an ordinary release exception never returned on a read-back that raised nothing).
        table_stop = {("nothing", "nothing"): None, ("own", "nothing"): None, ("ordinary", "nothing"): "R",
                      ("interrupt", "nothing"): "R", ("nothing", "own"): None, ("own", "own"): None,
                      ("ordinary", "own"): "R", ("interrupt", "own"): "R", ("nothing", "ordinary"): "B",
                      ("own", "ordinary"): "B", ("ordinary", "ordinary"): "R", ("interrupt", "ordinary"): "R",
                      ("nothing", "interrupt"): "B", ("own", "interrupt"): "B", ("ordinary", "interrupt"): "B",
                      ("interrupt", "interrupt"): "R"}
        table_make = {("R", "own"): lambda: OSError(errno.EIO, "R-own-release-EIO"),
                      ("R", "ordinary"): lambda: _InjectedCloseFault("R-ordinary-release-fault"),
                      ("R", "interrupt"): lambda: _InjectedCloseInterrupt("R-interrupt-release-interrupt"),
                      ("B", "own"): lambda: _journal.JournalError("B-own-read-back-error"),
                      ("B", "ordinary"): lambda: _InjectedCloseFault("B-ordinary-read-back-fault"),
                      ("B", "interrupt"): lambda: _InjectedCloseInterrupt("B-interrupt-read-back-interrupt")}

        def rendering(exc):
            """Every exception of `exc`'s chain (cause and context), each as str and with its notes."""
            seen, todo, parts = [], [exc], []
            while todo:
                cur = todo.pop(0)
                if cur is None or any(cur is was for was in seen):
                    continue
                seen.append(cur)
                parts.append("{} {}".format(cur, _journal._exc_said(cur)))
                todo += [cur.__cause__, cur.__context__]
            return " ".join(parts)
        for (r_kind, b_kind), want in table_stop.items():
            for mine_none in (False, True):
                for committed in (True, False):
                    raised, text, fired, leaked = ordered_run(
                        (), read_plan=(((outcome_code, table_make["B", b_kind]),) if b_kind != "nothing" else ()),
                        release_plan=((table_make["R", r_kind],) if r_kind != "nothing" else ()),
                        release_real=False, compose=compose_committed if committed else compose_refused_here,
                        mine_none=mine_none)
                    tags = ["{}-{}-".format(tag, kind) for tag, kind in (("R", r_kind), ("B", b_kind))
                            if kind != "nothing"]
                    stop = None if want is None else next(
                        (exc for exc in fired if "{}-".format(want) in str(exc)), None)
                    if committed:
                        stood = raised is stop if want is not None else type(raised) is AdoptCommittedLockError
                    else:
                        stood = (raised is stop if want is not None and not isinstance(stop, Exception)
                                 else type(raised) is AdoptApplyError)
                    full = rendering(raised)
                    clause = ("present but unreadable" if b_kind != "nothing" else
                              "could not read back the lock it wrote" if mine_none else "STAYS")
                    check("release-outcome-table-R-{}-B-{}-mine-{}-{}".format(
                              r_kind, b_kind, "unavailable" if mine_none else "known",
                              "committed" if committed else "refused"),
                          len(fired) == len(tags) and (want is None or stop is not None) and stood
                          and all(tag in full for tag in tags)
                          and ("COMMITTED" if committed else "an injected compose refusal") in full
                          and clause in full and not leaked,
                          observed="exception={!r} injected={!r} rendering={!r} leaked={!r}".format(
                              raised, fired, full[-900:], leaked))
        # the same table where the real release ran before R was raised (the lock gone, so "unconfirmed"):
        # stop is R unless R is "own", and R is named (red against an ordinary R returned as no stop)
        for r_kind in ("own", "ordinary", "interrupt"):
            for committed in (True, False):
                raised, text, fired, leaked = ordered_run(
                    (), release_plan=(table_make["R", r_kind],), release_real=True,
                    compose=compose_committed if committed else compose_refused_here)
                stop = fired[0] if fired and r_kind != "own" else None
                if committed:
                    stood = raised is stop if stop is not None else type(raised) is AdoptCommittedLockError
                else:
                    stood = (raised is stop if stop is not None and not isinstance(stop, Exception)
                             else type(raised) is AdoptApplyError)
                full = rendering(raised)
                check("release-outcome-table-R-{}-B-nothing-lock-gone-{}".format(
                          r_kind, "committed" if committed else "refused"),
                      len(fired) == 1 and stood and "R-{}-".format(r_kind) in full
                      and ("COMMITTED" if committed else "an injected compose refusal") in full
                      and "may not be durable" in full and not leaked,
                      observed="exception={!r} injected={!r} rendering={!r} leaked={!r}".format(
                          raised, fired, full[-900:], leaked))
        # 6a'''b3n (D-U10-SECOND-FAULT, round 17): a JournalError a CLOSE inside the release's owner read
        # raises is never read as release_lock's unreadable-lock signal and dropped. An EIO at
        # read_lock_owner_at's close, then a JournalError at read_lock_owner's (the EIO recorded on it), and
        # that JournalError alone: on a commit and on a refusal every injected exception is named in the
        # full rendering beside COMMITTED or the refusal (red against release_lock returning on any
        # JournalError, and against a lock clause naming its exception without the notes recorded on it).
        for name, plan in (
                ("release-owner-read-close-eio-then-journal-error-both-named",
                 dict(((rlo_at_code, lambda: OSError(errno.EIO, "FIRST-CLOSE-EIO")),
                       (rlo_code, lambda: _journal.JournalError("SECOND-CLOSE"))))),
                ("release-owner-read-close-journal-error-named",
                 dict(((rlo_at_code, lambda: _journal.JournalError("SINGLE-CLOSE-SENTINEL")),)))):
            for committed in (True, False):
                with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                    saved_umask = os.umask(0o022)
                    try:
                        root, files = fixture(temp)
                        raised, fired, leaked = _helper_close_run(
                            root, compose_committed if committed else compose_refused_here, plan, outcome_code)
                    finally:
                        os.umask(saved_umask)
                full = rendering(raised)
                check("{}-{}".format(name, "committed" if committed else "refused"),
                      len(fired) == len(plan) and all(exc.args[-1] in full for exc in fired)
                      and (type(raised) is AdoptCommittedLockError and "COMMITTED" in full if committed
                           else type(raised) is AdoptApplyError and "an injected compose refusal" in full)
                      and not leaked,
                      observed="exception={!r} injected={!r} rendering={!r} leaked={!r}".format(
                          raised, fired, full[-900:], leaked))
        # the "altered" state names what the release raised (red against its clause dropping R)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            altered_error = None

            def malformed_then_eio(journal_root):
                (Path(journal_root) / "lock").write_bytes(b"[")
                raise OSError(errno.EIO, "ALTERED-RELEASE-EIO")
            with mock.patch.object(_journal, "release_lock", malformed_then_eio):
                try:
                    run_adopt_transaction(root, rid, compose_full(files))
                except AdoptApplyError as exc:
                    altered_error = exc
        check("commit-release-altered-lock-names-the-release-error",
              isinstance(altered_error, AdoptCommittedLockError) and "altered and stays" in str(altered_error)
              and "ALTERED-RELEASE-EIO" in str(altered_error), observed="exception={!r}".format(altered_error))
        # a transaction whose state classification raises names that exception in the refusal (red against
        # the handler dropping it as an unreadable state)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            unclassified = None

            def transaction_fails(*args, **kwargs):
                raise _journal.JournalError("TRANSACTION-FAULT")

            def classify_raises(*args, **kwargs):
                raise _journal.JournalError("CLASSIFY-CLOSE-SENTINEL")
            with mock.patch.object(_journal, "run_transaction", transaction_fails), \
                    mock.patch.object(_journal, "classify_state", classify_raises):
                try:
                    run_adopt_transaction(root, rid, compose_full(files))
                except AdoptApplyError as exc:
                    unclassified = exc
        check("failed-transaction-classification-error-named",
              type(unclassified) is AdoptApplyError and "CLASSIFY-CLOSE-SENTINEL" in str(unclassified)
              and "TRANSACTION-FAULT" in str(unclassified) and "retained" in str(unclassified),
              observed="exception={!r}".format(unclassified))
        # a JournalError a close inside _poststate_verifies raises propagates as itself, never read as
        # does-not-verify (red against the handler returning False on it); with no injection it verifies
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            with open(os.path.join(temp, "f"), "wb") as fh:
                fh.write(b"poststate bytes")
            os.chmod(os.path.join(temp, "f"), 0o644)
            post_op = dict(op="create", path="f", poststate=dict(kind="file", mode=0o644))
            post_op["poststate"]["content-sha256"] = hashlib.sha256(b"poststate bytes").hexdigest()
            real_prop_p, fired_p, raised_p = _journal._close_fd_propagating, [], None

            def prop_p(fd):
                real_prop_p(fd)
                if not fired_p:
                    fired_p.append(_journal.JournalError("POSTSTATE-CLOSE-SENTINEL"))
                    raise fired_p[0]
            post_fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                control_p = _journal._poststate_verifies(post_fd, post_op)
                baseline = _fds_open()
                with mock.patch.object(_journal, "_close_fd_propagating", prop_p):
                    try:
                        verdict_p = _journal._poststate_verifies(post_fd, post_op)
                    except _journal.JournalError as exc:
                        verdict_p, raised_p = None, exc
                leaked = sorted(_fds_open() - baseline)
            finally:
                os.close(post_fd)
        check("poststate-close-journal-error-propagates", control_p is True and bool(fired_p)
              and raised_p is fired_p[0] and verdict_p is None and not leaked,
              observed="control={!r} verdict={!r} raised={!r} injected={!r} leaked={!r}".format(
                  control_p, verdict_p, raised_p, fired_p, leaked))
        # 6a'''b3o (rounds 18 and 19, the close-exception CLASS): a raise after a REAL close at EVERY close
        # event of seven runs (commit, refusal, body interrupt, body fault, failed acquire, lock stays,
        # failed transaction with rollback), one injection per run, EVERY probe class at EVERY event. The
        # classes are read from the code, never a hand list (close_site_classes): every exception class
        # named at a raise, except or isinstance site of every module whose code is on the stack at any
        # close event of those runs (StoreError and FileNotFoundError among them), plus an ordinary fault and
        # an interrupt no site names. The injected exception is named in the outcome (raised itself, or in
        # the message, notes, cause or context chain of what was raised) on every run, except the disclosed
        # unnamed close OSErrors (a quiet teardown close, or one yielding to an exception in flight). Red
        # against any handler in the run's reach that reads a close exception as a clean signal (absent,
        # unreadable, cannot-evaluate, not reached). The probe runs make fsync a no-op (no close event
        # depends on it, re-proved below by equal event counts) so the full set fits a self-test run.
        import ast
        import builtins
        import inspect
        import warnings
        real_acquire_c, real_apply_c = _journal.acquire_lock, _journal.apply_ops

        def class_failed_acquire(journal_root, session_id):
            real_acquire_c(journal_root, session_id)
            fault = OSError(errno.EIO, "CLASS-ACQUIRE-EIO")
            fault.lock_created = True
            raise fault

        def class_apply_then_fail(root_fd, ops, reader):
            real_apply_c(root_fd, ops, reader)
            raise _journal.JournalError("CLASS-ROLLBACK-TRIGGER")

        def class_body_interrupt(ops):
            raise _InjectedCloseInterrupt("CLASS-BODY-INTERRUPT")

        def class_body_fault(ops):
            raise _InjectedCloseFault("CLASS-BODY-FAULT")
        class_scenarios = (("commit", None, ()), ("refusal", compose_refused_here, ()),
                           ("body-interrupt", class_body_interrupt, ()), ("body-fault", class_body_fault, ()),
                           ("failed-acquire", None, (("acquire_lock", class_failed_acquire),)),
                           ("lock-stays", None, (("release_lock", lambda journal_root: None),)),
                           ("rollback", None, (("apply_ops", class_apply_then_fail),)))
        quiet_code = _journal._close_fd_quietly.__code__

        def class_run(scenario, plan, sites=None):
            """One run of `scenario` with os.close replaced by the real close, then a raise of plan[n]() at
            close event n (counted from 1, or only beneath a frame running `plan["under"]` when given; with
            plan["fast"], fsync a no-op): (events counted, [(exception, disclosed-unnamed)], what the run
            raised). `sites`, a set, collects the module of every frame on the stack at each close event, from
            the close up to run_adopt_transaction (the close-site path, never the harness above it)."""
            name, compose_c, patches = scenario
            under = plan.get("under")
            held = _fds_open()
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                root, files = fixture(temp)
                real_close_c, seen, fired = os.close, [0], []

                def close_then_raise(fd):
                    real_close_c(fd)
                    if under is not None:
                        frame = sys._getframe(1)
                        while frame is not None and frame.f_code is not under:
                            frame = frame.f_back
                        if frame is None:
                            return
                    seen[0] += 1
                    if sites is not None:
                        frame = sys._getframe(1)
                        while frame is not None:
                            sites.add(frame.f_globals.get("__name__"))
                            frame = None if frame.f_code is _run_adopt_transaction.__code__ else frame.f_back
                    if seen[0] in plan:
                        exc = plan[seen[0]]()
                        fired.append((exc, sys._getframe(1).f_code is quiet_code or sys.exc_info()[1] is not None))
                        raise exc
                raised = None
                with contextlib.ExitStack() as stack:
                    for attr, value in patches:
                        stack.enter_context(mock.patch.object(_journal, attr, value))
                    stack.enter_context(mock.patch.object(os, "close", close_then_raise))
                    if plan.get("fast"):
                        stack.enter_context(mock.patch.object(os, "fsync", lambda fd: None))
                    try:
                        run_adopt_transaction(root, rid, compose_c or compose_full(files))
                    except BaseException as exc:    # noqa: BLE001  every outcome is inspected below
                        raised = exc
            # An injection at a disclosed close window (the store's _open_working_dir_fd parent close among
            # them; leak-freedom under interrupt is not claimed) leaves a descriptor open: reclaim each one
            # this run left behind, so the sweep's runs never leave them to the modules tested after this one.
            for fd in sorted(_fds_open() - held):
                os.close(fd)
            return seen[0], fired, raised

        def class_named(exc, raised):
            return raised is exc or (raised is not None and str(exc.args[-1]) in rendering(raised))

        def close_site_classes(module_names):
            """Every exception class named at a raise, an except or an isinstance site of each module in
            `module_names` that lives beside this one (its self-test functions excluded), resolved in that
            module's namespace: the classes a close-site path can raise or a handler on it can read."""
            found = set()
            here = os.path.dirname(os.path.abspath(__file__))
            for module_name in sorted(n for n in module_names if n in sys.modules):
                module = sys.modules[module_name]
                if os.path.dirname(os.path.abspath(getattr(module, "__file__", None) or "/")) != here:
                    continue
                todo = [ast.parse(inspect.getsource(module))]
                while todo:
                    node = todo.pop()
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "self_test" in node.name:
                        continue
                    named = []
                    if isinstance(node, ast.ExceptHandler) and node.type is not None:
                        named = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
                    elif isinstance(node, ast.Raise) and node.exc is not None:
                        named = [node.exc.func if isinstance(node.exc, ast.Call) else node.exc]
                    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 2 \
                            and node.func.id in ("isinstance", "issubclass"):
                        named = node.args[1].elts if isinstance(node.args[1], ast.Tuple) else [node.args[1]]
                    for expr in named:
                        chain = []
                        while isinstance(expr, ast.Attribute):
                            chain.append(expr.attr)
                            expr = expr.value
                        if isinstance(expr, ast.Name):
                            obj = vars(module).get(expr.id, getattr(builtins, expr.id, None))
                            for attr in reversed(chain):
                                obj = getattr(obj, attr, None)
                            if isinstance(obj, type) and issubclass(obj, BaseException):
                                found.add(obj)
                    todo.extend(ast.iter_child_nodes(node))
            return found

        def class_maker(cls, said):
            """An instance of `cls` whose last argument carries `said`, or None when no form constructs one."""
            forms = (((errno.EIO, said),) if issubclass(cls, OSError) else ()) \
                + ((("utf-8", b"", 0, 1, said),) if issubclass(cls, UnicodeDecodeError) else ()) \
                + ((said,), (said, "", 0), (said, said))
            for args in forms:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("error")
                        made = cls(*args)
                except Exception:   # noqa: BLE001  the next constructor form is tried
                    continue
                if made.args and said in str(made.args[-1]):
                    return made
            return None
        class_sites, class_counts = set(), []
        for scenario in class_scenarios:
            class_counts.append((class_run(scenario, dict(), class_sites)[0],
                                 class_run(scenario, dict(fast=True))[0]))
        class_set = sorted(close_site_classes(class_sites) | {_InjectedCloseFault, _InjectedCloseInterrupt},
                           key=lambda cls: (cls.__module__, cls.__qualname__))
        class_unmade = [cls.__qualname__ for cls in class_set if class_maker(cls, "probe") is None]
        class_bad, class_runs = [], 0
        for scenario, (total, _fast_total) in zip(class_scenarios, class_counts):
            for at in range(1, total + 1):
                for cls in class_set:
                    said = "CLASS-CLOSE-%s-%d-%s" % (scenario[0], at, cls.__qualname__)
                    if class_maker(cls, said) is None:
                        continue
                    _n, fired, raised = class_run(scenario, dict(((at, lambda: class_maker(cls, said)),
                                                                  ("fast", True))))
                    class_runs += 1
                    if fired and not (isinstance(fired[0][0], OSError) and fired[0][1]) \
                            and not class_named(fired[0][0], raised):
                        class_bad.append((scenario[0], at, repr(fired[0][0]), repr(raised)[:300]))
        class_names = {cls.__qualname__ for cls in class_set}
        check("close-class-every-close-event-named-or-raised",
              all(total == fast_total for total, fast_total in class_counts) and not class_unmade
              and {"StoreError", "FileNotFoundError", "JournalError", "OSError", "KeyError"} <= class_names
              and class_runs == sum(total for total, _fast in class_counts) * len(class_set) and not class_bad,
              observed="counts={} classes={} unmade={} runs={} unnamed (scenario, class): events={!r}; first "
              "(scenario, event, injected, raised)={!r}".format(
                  class_counts, sorted(class_names), class_unmade, class_runs,
                  sorted({(sc, inj.split("(")[0]): [ev for sc2, ev, inj2, _r in class_bad
                                                    if sc2 == sc and inj2.split("(")[0] == inj.split("(")[0]]
                          for sc, _ev, inj, _r in class_bad}.items()), class_bad[:8]))
        # round 19: a close after the COMPLETE frame is durable (publish's frames-log close, then its
        # transaction-directory close) that raises any probe class but an interrupt leaves the commit
        # standing: AdoptCommittedLockError naming COMMITTED and the injected exception, the transaction
        # complete and the lock released (red against the handler refusing it as FAILED with the lock kept)
        publish_code = _journal.publish.__code__
        complete_bad, complete_runs = [], 0
        for nth in (1, 2):
            for cls in (cls for cls in class_set if issubclass(cls, Exception)):
                said = "COMPLETE-CLOSE-%d-%s" % (nth, cls.__qualname__)
                real_close_q, seen_q, fired_q, outcome_q = os.close, [0], [], None

                def close_q(fd):
                    real_close_q(fd)
                    frame = sys._getframe(1)
                    while frame is not None and not (frame.f_code is publish_code
                                                     and frame.f_locals.get("ftype") == _journal.F_COMPLETE):
                        frame = frame.f_back
                    if frame is not None:
                        seen_q[0] += 1
                        if seen_q[0] == nth:
                            fired_q.append(class_maker(cls, said))
                            raise fired_q[0]
                with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                    root, files = fixture(temp)
                    with mock.patch.object(os, "close", close_q), mock.patch.object(os, "fsync", lambda fd: None):
                        try:
                            outcome_q = run_adopt_transaction(root, rid, compose_full(files))
                        except BaseException as exc:    # noqa: BLE001  inspected below
                            outcome_q = exc
                    state_q, free_q = txn_state(root, rid), lock_free(root)
                complete_runs += 1
                if not (fired_q and type(outcome_q) is AdoptCommittedLockError and "COMMITTED" in str(outcome_q)
                        and said in rendering(outcome_q) and state_q == "complete" and free_q):
                    complete_bad.append((nth, cls.__qualname__, repr(outcome_q)[:240], state_q, free_q))
        check("complete-publish-close-commit-stands-and-names-it", complete_runs > 20 and not complete_bad,
              observed="runs={} bad (close, class, outcome, state, lock free)={!r}".format(
                  complete_runs, complete_bad[:6]))
        # round 19: a StoreError a close beneath _resolve_at's discovery raises (marked as it left the close
        # helper) is raised as itself, never read as a cannot-evaluate posture that _store_posture_or_refuse
        # then admits (red against _resolve_at's handler returning CANNOT-EVALUATE on it): at
        # _lstat_contained's close; and an ordinary fault at _read_contained's file close, then a StoreError
        # at its parent close, the StoreError raised with the first recorded on it
        resolve_code, classify_code = store._resolve_at.__code__, store._classify_working_names.__code__
        lstat_code, read_code = _journal._lstat_contained.__code__, _journal._read_contained.__code__

        def resolve_close_run(where, makers):
            """One run with an empty composer, os.close making the real close, then raising the next of
            `makers` at each close made directly by `where` beneath _resolve_at and _classify_working_names:
            (the exceptions raised there, what the run raised or returned)."""
            real_close_r, fired_r, outcome_r = os.close, [], None

            def close_r(fd):
                real_close_r(fd)
                frame, codes, direct = sys._getframe(1), set(), None
                while frame is not None:
                    codes.add(frame.f_code)
                    if direct is None and frame.f_code not in (_journal._close_fd_propagating.__code__,
                                                               _journal._close_fd_yielding.__code__):
                        direct = frame.f_code
                    frame = frame.f_back
                if direct is where and resolve_code in codes and classify_code in codes \
                        and len(fired_r) < len(makers):
                    fired_r.append(makers[len(fired_r)]())
                    raise fired_r[-1]
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                root, files = fixture(temp)
                with mock.patch.object(os, "close", close_r):
                    try:
                        outcome_r = run_adopt_transaction(root, rid, lambda ops: None)
                    except BaseException as exc:    # noqa: BLE001  inspected below
                        outcome_r = exc
            return fired_r, outcome_r
        fired_r, outcome_r = resolve_close_run(lstat_code, (lambda: store.StoreError("STORE-CLOSE-SENTINEL"),))
        check("resolve-at-close-store-error-raised-as-itself", len(fired_r) == 1 and outcome_r is fired_r[0],
              observed="injected={!r} outcome={!r}".format(fired_r, outcome_r))
        fired_r, outcome_r = resolve_close_run(read_code, (lambda: RuntimeError("INNER-CLOSE-SENTINEL"),
                                                           lambda: store.StoreError("OUTER-CLOSE-SENTINEL")))
        check("resolve-at-read-close-fault-then-store-error-both-named",
              len(fired_r) == 2 and outcome_r is fired_r[1] and "INNER-CLOSE-SENTINEL" in rendering(outcome_r),
              observed="injected={!r} outcome={!r}".format(fired_r, outcome_r))
        # the sweep (_remove_journal_dirs): an ordinary fault at one of its closes, then a JournalError at
        # the next (the second raised with the first recorded on it), at every consecutive pair: both are
        # named beside the refusal (red against the sweep keeping only the JournalError's message)
        sweep_bad, sweep_runs = [], 0
        sweep_total = class_run(class_scenarios[1], dict(under=_remove_journal_dirs.__code__))[0]
        for at in range(1, sweep_total):
            _n, fired, raised = class_run(class_scenarios[1], dict((
                ("under", _remove_journal_dirs.__code__),
                (at, lambda: _InjectedCloseFault("SWEEP-FIRST-%d" % at)),
                (at + 1, lambda: _journal.JournalError("SWEEP-SECOND-%d" % at)))))
            sweep_runs += 1
            if not isinstance(raised, AdoptApplyError) or "an injected compose refusal" not in rendering(raised) \
                    or not all(class_named(exc, raised) for exc, _quiet in fired):
                sweep_bad.append((at, [repr(exc) for exc, _quiet in fired], repr(raised)[:300]))
        check("close-class-sweep-close-pairs-both-named", sweep_runs > 3 and not sweep_bad,
              observed="pairs={} unnamed={!r}".format(sweep_runs, sweep_bad[:6]))
        # a KeyError a close inside _poststate_verifies raises rolls the transaction back (run_transaction
        # rolls back on any Exception a close raised) and is named in the refusal; the lock is released
        # (red against the transaction left open with its lock released)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            real_prop_k, fired_k, raised_k = _journal._close_fd_propagating, [], None
            post_code = _journal._poststate_verifies.__code__

            def prop_k(fd):
                real_prop_k(fd)
                frame = sys._getframe(1)
                while frame is not None and frame.f_code is not post_code:
                    frame = frame.f_back
                if frame is not None and not fired_k:
                    fired_k.append(KeyError("POSTSTATE-CLOSE-K"))
                    raise fired_k[0]
            with mock.patch.object(_journal, "_close_fd_propagating", prop_k):
                try:
                    run_adopt_transaction(root, rid, compose_full(files))
                except BaseException as exc:    # noqa: BLE001  inspected below
                    raised_k = exc
            state_k, free_k = txn_state(root, rid), lock_free(root)
        check("poststate-close-key-error-rolls-back-and-is-named",
              bool(fired_k) and type(raised_k) is AdoptApplyError and "POSTSTATE-CLOSE-K" in rendering(raised_k)
              and state_k == "rolled-back" and free_k,
              observed="raised={!r} state={!r} injected={!r}".format(raised_k, state_k, fired_k))
        # an interrupt inside the lock acquire, then another in the read of what it left: the acquire's,
        # the first, propagates as itself, the read's named beside it; an ordinary fault in the acquire,
        # then an interrupt in that read: the read's interrupt propagates, the acquire's fault noted on it
        # (red against the read's exception escaping _interrupted_lock_state in place of the acquire's)
        for name, first_m, want in (
                ("acquire-interrupt-then-lock-read-interrupt-propagates-the-first",
                 lambda: _InjectedCloseInterrupt("acquire interrupt"), 0),
                ("acquire-fault-then-lock-read-interrupt-propagates-the-interrupt",
                 lambda: _InjectedCloseFault("acquire close fault"), 1)):
            raised, text, fired, leaked = ordered_run(
                (), read_plan=((interrupted_code, lambda: _InjectedCloseInterrupt("lock read interrupt")),),
                acquire_plan=(first_m,))
            check(name, len(fired) == 2 and raised is fired[want] and repr(fired[1 - want]) in text
                  and "holds no journal lock" not in text and not leaked,
                  observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                      raised, fired, text[-600:], leaked))
        # an interrupt at the early lock close (after the release saw the lock gone), then one at the
        # closing sweep's close: the early one, the first, propagates as itself, the sweep's named beside
        # it (red against the close-out entries recorded before the closing sweep read after it: the
        # final close-out's split `fin` dropped)
        real_quiet_e, fired_e = _journal._close_fd_quietly, []

        def quiet_e(fd):
            real_quiet_e(fd)
            frame = sys._getframe(1)
            while frame is not None and frame.f_code is not held_into_code:
                frame = frame.f_back
            if not fired_e and frame is not None and frame.f_back.f_code is txn_code:
                fired_e.append(_InjectedCloseInterrupt("early lock close interrupt"))
                raise fired_e[-1]
        with mock.patch.object(_journal, "_close_fd_quietly", quiet_e):
            raised, text, fired, leaked = ordered_run((lambda: _InjectedCloseInterrupt("sweep interrupt"),))
        check("early-lock-close-interrupt-then-sweep-interrupt-propagates-the-first",
              len(fired_e) == 1 and len(fired) == 1 and raised is fired_e[0] and repr(fired[0]) in text
              and "an injected compose refusal" in text and not leaked,
              observed="exception={!r} injected={!r} note={!r} leaked={!r}".format(
                  raised, fired_e + fired, text[-600:], leaked))

        # an ordinary exception out of the engine once the transaction was entered (here after it
        # completed) propagates as itself naming that phase, never only the journal entries it observed
        # (red against the transaction state left out of its note)
        def run_transaction_then_fault(*args, **kwargs):
            real_run_transaction_m(*args, **kwargs)
            raise _InjectedCloseFault("engine fault after the transaction completed")
        real_run_transaction_m, raised = _journal.run_transaction, None
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            try:
                root, files = fixture(temp)
                baseline = _fds_open()
                with mock.patch.object(_journal, "run_transaction", run_transaction_then_fault):
                    try:
                        run_adopt_transaction(root, rid, compose_committed)
                    except (AdoptApplyError, RuntimeError, KeyboardInterrupt) as exc:
                        raised = exc
                leaked = sorted(_fds_open() - baseline)
            finally:
                os.umask(saved_umask)
        text = " ".join(getattr(raised, "__notes__", []) or [])
        check("entered-transaction-engine-fault-names-the-phase",
              type(raised) is _InjectedCloseFault and "was entered and is NOT confirmed committed or undone" in text
              and not leaked,
              observed="exception={!r} note={!r} leaked={!r}".format(raised, text[-600:], leaked))
        # Every other routed close site the transaction reaches (the store's resolution and discovery, the
        # no-follow walk, _journal's cleanup loops, the check engine's listing, the render-views planning
        # close): an interrupt in flight in the site's body, then a fault at its close (the real close made
        # first), propagates the INTERRUPT as itself, the fault noted on it, and leaks nothing (red against
        # the close's fault replacing the interrupt, which then survives only as its __context__).
        import types
        import _opf_check
        import _opf_views

        def site_vector(name, site, close_at, body_at, skip, call):
            site_code, intr = site.__code__, _InjectedCloseInterrupt("{} body interrupt".format(name))
            real_close_s, real_body_s = getattr(*close_at), getattr(*body_at)
            fired_s, injected_s, seen = [], [], []

            def under_site(frame):
                for _ in range(3):
                    if frame is None:
                        return False
                    if frame.f_code is site_code:
                        return True
                    frame = frame.f_back
                return False

            def close_s(fd):
                real_close_s(fd)
                if fired_s and not injected_s and under_site(sys._getframe(1)):
                    injected_s.append(_InjectedCloseFault("{} close fault".format(name)))
                    raise injected_s[-1]

            def body_s(*args, **kwargs):
                if not fired_s and sys._getframe(1).f_code is site_code:
                    seen.append(None)
                    if len(seen) > skip:
                        fired_s.append(intr)
                        raise intr
                return real_body_s(*args, **kwargs)
            raised_s = None
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp_s:
                for sub in (".working/x", "t1", "t2"):
                    os.makedirs(os.path.join(temp_s, sub))
                site_fd = os.open(temp_s, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    baseline_s = _fds_open()
                    with mock.patch.object(close_at[0], close_at[1], close_s), \
                            mock.patch.object(body_at[0], body_at[1], body_s):
                        try:
                            call(temp_s, site_fd)
                        except (RuntimeError, KeyboardInterrupt) as exc:
                            raised_s = exc
                    leaked_s = sorted(_fds_open() - baseline_s)
                finally:
                    os.close(site_fd)
            check("close-site-first-interrupt-" + name,
                  len(fired_s) == 1 and len(injected_s) == 1 and raised_s is intr and repr(injected_s[0]) in
                  " ".join(getattr(raised_s, "__notes__", []) or []) and not leaked_s,
                  observed="exception={!r} injected={!r} leaked={!r}".format(raised_s, injected_s, leaked_s))
        exc_safe_at, quiet_at = (store, "_close_fd_exc_safe"), (_journal, "_close_fd_quietly")

        def _close_held_caller(base_fd):
            """A finally closing a held list through _close_held (journal_state's and the first
            listing's walk's posture) while its body's exception is in flight."""
            held_pair = [os.dup(base_fd), os.dup(base_fd)]
            try:
                os.fstat(base_fd)
            finally:
                _close_held(held_pair)
        for row in (
                ("store-immediate-subdirs", store._immediate_subdirs, exc_safe_at, (store, "_list_real_subdirs"),
                 0, lambda temp_s, fd: store._immediate_subdirs(fd, ".working")),
                ("store-open-working-dir", store._open_working_dir_fd, exc_safe_at, (os, "open"), 0,
                 lambda temp_s, fd: store._open_working_dir_fd(fd, ".working")),
                ("store-discover-fd", store.discover_machine_store_fd, exc_safe_at,
                 (store, "_classify_working_names"), 0, lambda temp_s, fd: store.discover_machine_store_fd(fd)),
                ("store-resolve", store.resolve_store, exc_safe_at, (store, "_read_pointer_target"), 0,
                 lambda temp_s, fd: store.resolve_store(temp_s)),
                ("store-resolve-at", store._resolve_at, exc_safe_at, (store, "discover_machine_store"), 0,
                 lambda temp_s, fd: store.resolve_store(temp_s)),
                ("store-load-manifest", store.load_manifest, exc_safe_at, (store, "_read_toml_contained"), 0,
                 lambda temp_s, fd: store.load_manifest(types.SimpleNamespace(
                     status=store.RESOLVED, machine_rel=".working/x", store_root=Path(temp_s),
                     pointer_source="default"))),
                ("store-no-follow-walk", store._open_dir_nofollow, (_journal, "_close_fd_propagating"),
                 (os, "open"), 1, lambda temp_s, fd: store._open_dir_nofollow(os.path.join(temp_s, ".working"))),
                ("journal-ensure-dirs", _journal.ensure_journal_dirs, quiet_at, (os, "fsync"), 0,
                 lambda temp_s, fd: _journal.ensure_journal_dirs(fd, ".working/x/y")),
                ("journal-open-dir-contained", _journal._open_dir_contained, quiet_at, (os, "dup"), 0,
                 lambda temp_s, fd: _journal._open_dir_contained(fd, ".working/x")),
                ("journal-txn-dirs", _journal._journal_txn_dirs, quiet_at, (os, "open"), 1,
                 lambda temp_s, fd: _journal._journal_txn_dirs(fd, Path(temp_s), hold=True)),
                ("check-list-contained", _opf_check._list_contained, (_opf_check, "_close_fd_quietly"),
                 (os, "listdir"), 0, lambda temp_s, fd: _opf_check._list_contained(fd, ".working")),
                ("check-validate-store", _opf_check.validate_store, (_opf_check, "_close_fd_quietly"),
                 (_opf_check, "_validate_opened_store"), 0,
                 lambda temp_s, fd: _opf_check.validate_store(types.SimpleNamespace(
                     status=store.RESOLVED, machine_rel=".working/x", store_root=Path(temp_s),
                     pointer_source="default", product_root=Path(temp_s)))),
                ("committed-base", _committed_base_or_refuse, quiet_at, (_journal, "read_frames"), 0,
                 lambda temp_s, fd: (os.makedirs(os.path.join(temp_s, JOURNAL_REL, rid)),
                                     _committed_base_or_refuse(fd, Path(temp_s) / JOURNAL_REL, rid, "p1"))),
                ("close-held", _close_held_caller, quiet_at, (os, "fstat"), 0,
                 lambda temp_s, fd: _close_held_caller(fd)),
                ("planned-views", _planned_views, exc_safe_at, (_opf_views, "plan_views"), 0,
                 lambda temp_s, fd: _planned_views(types.SimpleNamespace(store_root=Path(temp_s),
                                                                         machine_rel=".working/x")))):
            site_vector(*row)

        class _StatFile:
            """A /proc stat file whose read is interrupted and whose close then raises a fault."""
            def __init__(self):
                self.faults = []

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                self.close()
                return False

            def read(self, *args):
                raise _InjectedCloseInterrupt("stat read")

            def close(self):
                self.faults.append(_InjectedCloseFault("stat close"))
                raise self.faults[-1]
        opened_stat, raised = [], None

        def stat_open(*args, **kwargs):
            opened_stat.append(_StatFile())
            return opened_stat[-1]
        with mock.patch.object(_journal, "open", stat_open, create=True):
            try:
                _journal._pid_start(os.getpid())
            except (RuntimeError, KeyboardInterrupt) as exc:
                raised = exc
        check("journal-pid-start-close-fault-keeps-the-read-interrupt",
              type(raised) is _InjectedCloseInterrupt and len(opened_stat) == 1 and opened_stat[0].faults
              and repr(opened_stat[0].faults[0]) in " ".join(getattr(raised, "__notes__", []) or []),
              observed="exception={!r} opened={!r}".format(raised, opened_stat))
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            mroot = Path(temp) / "product"
            os.makedirs(mroot / evidence_home_rel(rid))
            # The bundle carries its sealed fixture plan and the [adoption] identity naming it, so the
            # identity bar admits the inventory and only the missing listed files remain to be found.
            stage_fixture_plan(mroot, rid)
            with open(mroot / evidence_home_rel(rid) / "inventory.toml", "w", encoding="utf-8") as fh:
                fh.write("[adoption]\nrun_id = \"" + rid + "\"\nphase = \"base\"\nplan_digest = \""
                         + fixture_plan(rid)[1] + "\"\n")
                fh.write("".join("[[file]]\npath = \"{}\"\nsize = 0\nsha256 = \"{}\"\n".format(
                    listed, "0" * 64) for listed in ("missing/a", "missing/b", "missing/deeper/c")))
            real_open_here, absent_opens = os.open, []

            def counting_open(path, *args, **kwargs):
                if path == "missing":
                    absent_opens.append(path)
                return real_open_here(path, *args, **kwargs)
            with mock.patch.dict(globals(), {"validate_inventory": lambda doc, run_id: schema._ok()}), \
                    mock.patch.object(os, "open", counting_open), \
                    mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {counting_open}):
                verified = verify_bundle(mroot, rid)
        check("verify-missing-ancestor-opened-once",
              verified.status == INVALID and len(verified.findings) == 3 and len(absent_opens) == 1,
              observed="status={!r} findings={!r} opens={!r}".format(
                  verified.status, verified.findings, absent_opens))
        # 6a'''b3d: _journal_listing's own close-out (no `keep`: the closing listing of a refused run) runs
        # through _close_held too. An exception injected between a journal path component descriptor's
        # pop and its close (the patched close-out raises instead of closing) at the FIRST such close
        # leaves exactly that popped descriptor open: its sibling component descriptors are still closed
        # and the injected exception propagates. A listing with no injection is the control (red against
        # a close-out loop that stops at its first failure: the unreached siblings leak).
        comp_rels = ["/".join(JOURNAL_REL.split("/")[:i + 1]) for i in range(len(JOURNAL_REL.split("/")))]

        def _listing_close_run(root, inject):
            """One listing with no `keep` over `root`, its close-out injected at its first close of a
            journal path component when `inject`: (exception, popped descriptors, component descriptors
            closed after the injection, descriptors leaked)."""
            real_quiet = _journal._close_fd_quietly
            comp_ids = set()
            for rel in comp_rels:
                cst = _real_lstat(root / rel)
                comp_ids.add((cst.st_dev, cst.st_ino))
            popped, after = [], []

            def faulting_quiet(fd):
                try:
                    fst = _real_fstat(fd)
                except OSError:
                    return real_quiet(fd)
                if (fst.st_dev, fst.st_ino) in comp_ids:
                    if inject and not popped:
                        popped.append(fd)
                        raise _InjectedCloseFault("journal listing")
                    after.append(fd)
                return real_quiet(fd)
            raised, leaked = None, set()
            root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                baseline = _fds_open()
                try:
                    with mock.patch.object(_journal, "_close_fd_quietly", faulting_quiet):
                        try:
                            _journal_listing(root_fd)
                        except _InjectedCloseFault as exc:
                            raised = exc
                    leaked = _fds_open() - baseline
                finally:
                    _close_held(list(popped))   # a copy: the caller reads popped
            finally:
                os.close(root_fd)
            return raised, popped, after, sorted(leaked)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            control = got = None
            try:
                root, files = fixture(temp)
                os.makedirs(root / JOURNAL_REL)
                control = _listing_close_run(root, False)
                got = _listing_close_run(root, True)
            finally:
                os.umask(saved_umask)
            raised, popped, after, leaked = got if got is not None else (None, [], [], [])
            check("journal-listing-close-out-injected-fault-closes-the-rest",
                  control is not None and control[0] is None and not control[1]
                  and len(control[2]) == len(comp_rels) and not control[3]
                  and isinstance(raised, _InjectedCloseFault) and len(popped) == 1
                  and leaked == sorted(popped) and len(after) == len(comp_rels) - 1,
                  observed="control (exception, component closes, leaked)={!r} exception={!r} popped={!r} "
                           "component closes after the injection={!r} leaked={!r}".format(
                               None if control is None else (control[0], control[2], control[3]), raised,
                               popped, after, leaked))
    # 6a'''b3e: within ONE list, _close_held re-raises the FIRST exception its close-out met, each later
    # one recorded on it as a note, and still closes every other descriptor. Three closes of a five
    # descriptor list are injected (the patched close-out raises instead of closing): the first is the
    # one raised, the second and third are each named in a note in that order, and the two others are
    # closed (red against a close-out keeping the last exception, and against one that drops a later
    # exception without trace).
    class _ListCloseFault(RuntimeError):
        pass
    list_fds, list_popped, list_raised = [], [], None
    real_quiet = _journal._close_fd_quietly

    def list_faulting_quiet(fd):
        if len(list_popped) < 3:
            list_popped.append(fd)
            raise _ListCloseFault(len(list_popped))
        return real_quiet(fd)
    try:
        for _ in range(5):
            list_fds.append(os.open(os.devnull, os.O_RDONLY))
        held_list = list(list_fds)
        with mock.patch.object(_journal, "_close_fd_quietly", list_faulting_quiet):
            try:
                _close_held(held_list)
            except _ListCloseFault as exc:
                list_raised = exc
        list_notes = getattr(list_raised, "__notes__", [])
        list_open = []
        for fd in list_fds:
            try:
                _real_fstat(fd)
            except OSError:
                continue
            list_open.append(fd)
    finally:
        _close_held(list(list_popped))
    check("close-held-first-exception-raised-later-noted",
          list_raised is not None and list_raised.args == (1,) and not held_list
          and len(list_notes) == 2 and repr(_ListCloseFault(2)) in list_notes[0]
          and repr(_ListCloseFault(3)) in list_notes[1] and sorted(list_open) == sorted(list_popped),
          observed="raised={!r} notes={!r} still open={!r} popped={!r}".format(
              list_raised, list_notes, list_open, list_popped))
    # 6a'''b3f: _verify_bundle_at's close-out of its retained directory descriptors runs through
    # _close_held too. An exception injected at its FIRST close (the patched close-out raises instead of
    # closing) over a bundle whose verification retains several directories leaves exactly that popped
    # descriptor open: every other retained directory is still closed, and the injected exception is
    # the one raised (red against a close-out loop that stops at its first failure: the unreached
    # directories leak).
    class _VerifyCloseFault(RuntimeError):
        pass
    verify_code = _verify_bundle_at.__code__
    verify_seen, verify_popped, verify_raised = [], [], None

    def verify_faulting_quiet(fd):
        up = sys._getframe(1)
        while (up is not None and up.f_code is not verify_code
               and up.f_code.co_name.startswith("_close_held")):
            up = up.f_back
        if (up is not None and up.f_code is verify_code
                and fd in (up.f_locals.get("dir_fds") or {}).values()):
            verify_seen.append(fd)
            if not verify_popped:
                verify_popped.append(fd)
                raise _VerifyCloseFault("the first retained directory close")
        return real_quiet(fd)
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        try:
            _root3f = Path(temp).resolve()
            _payload3f = home + "/payload/a.md"
            (_root3f / _payload3f).parent.mkdir(parents=True)
            (_root3f / _payload3f).write_bytes(b"alpha\n")
            (_root3f / inventory_rel(rid)).write_bytes(
                emit_inventory(rid, [inventory_row(_payload3f, b"alpha\n")]))
            stage_fixture_plan(_root3f, rid)
            verify_control = verify_bundle(_root3f, rid)
            with mock.patch.object(_journal, "_close_fd_quietly", verify_faulting_quiet):
                try:
                    verify_bundle(_root3f, rid)
                except _VerifyCloseFault as exc:
                    verify_raised = exc
            verify_open = []
            for fd in verify_seen:
                try:
                    _real_fstat(fd)
                except OSError:
                    continue
                verify_open.append(fd)
        finally:
            _close_held(list(verify_popped))
    check("verify-bundle-close-out-injected-fault-closes-the-rest",
          verify_control.status == store.VALID and isinstance(verify_raised, _VerifyCloseFault)
          and len(verify_seen) >= 3 and verify_open == verify_popped,
          observed="control={!r} raised={!r} closes seen={!r} still open={!r} popped={!r}".format(
              verify_control.status, verify_raised, verify_seen, verify_open, verify_popped))
    # 6a'''b3g: the close-out clause names each recorded exception with what it says of its descriptor:
    # an OSError is a close's own error, whose close has still released the descriptor (man 2 close),
    # and any other exception may leave the descriptor it had popped open; both stand beside the run's
    # outcome, in order (red against the earlier clause that said of every exception that a popped
    # descriptor may stay open, a close error included).
    label_raised = [OSError(5, "a close error"), RuntimeError("a popped close")]
    label_said = _close_out_said(label_raised)
    check("close-out-clause-labels-each-exception",
          label_said is not None and "2 exceptions, in order" in label_said
          and "beside that outcome, never in its place" in label_said
          and ("{!r} (a close error: that close has still released its descriptor); then {!r} (the "
               "descriptor it had popped may stay open until the process exits)".format(*label_raised)
               in label_said)
          and _close_out_said([]) is None,
          observed="clause={!r}".format(label_said))
    # 6a'''b4: an NFS product root, modelled: a file unlinked while this process still holds it open is
    # renamed to .nfsXXXX (the client's silly-rename) and removed at its last close. The run closes the
    # lock descriptor it holds right after the release's read-back, before the cleanup and the closing
    # listing, so a refusal under the lock still says "nothing written" and removes the journal it
    # created (red against holding that descriptor through the cleanup: the .nfs entry is named a stray
    # that stays, and the rmdir of the journal directory fails as not empty).
    nfs_legs = ((True, "nfs-silly-rename-lock-closed-before-listing"),
                (False, "nfs-silly-rename-lock-closed-before-cleanup"))
    if not census:
        for _prebuilt, name in nfs_legs:
            skipped.append((name, no_census))
    else:
        for prebuilt, name in nfs_legs:
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                saved_umask = os.umask(0o022)
                silly, renamed = {}, []
                err = left = None
                try:
                    root, files = fixture(temp)
                    if prebuilt:
                        os.makedirs(root / JOURNAL_REL)
                    lock_path = str(_journal_root(root) / "lock")
                    nfs_path = str(_journal_root(root) / ".nfs000000000000abc1")
                    real_unlink, real_close = os.unlink, os.close

                    def nfs_unlink(path, *args, **kwargs):
                        if not args and not kwargs and str(path) == lock_path:
                            st = _real_lstat(lock_path)
                            if not _inode_free((st.st_dev, st.st_ino)):
                                os.rename(lock_path, nfs_path)
                                silly[nfs_path] = (st.st_dev, st.st_ino)
                                renamed.append(nfs_path)
                                return None
                        return real_unlink(path, *args, **kwargs)

                    def nfs_close(fd):
                        try:
                            return real_close(fd)
                        finally:
                            for path, identity in list(silly.items()):
                                if _inode_free(identity):
                                    del silly[path]
                                    real_unlink(path)

                    def compose_refused_nfs(ops):
                        raise AdoptApplyError("an injected compose refusal")
                    with mock.patch.object(os, "unlink", nfs_unlink), mock.patch.object(os, "close", nfs_close), \
                            mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {nfs_unlink}):
                        err = refusal(run_adopt_transaction, root, rid, compose_refused_nfs)
                    left = sorted(os.listdir(root / JOURNAL_REL)) if prebuilt else os.path.lexists(root / ".aiqt")
                finally:
                    os.umask(saved_umask)
                check(name, bool(renamed) and not silly and left in ([], False)
                      and "injected compose refusal; " + _NOTHING_WRITTEN in (err or "") and ".nfs" not in (err or ""),
                      observed="refusal={!r} silly-renamed={!r} still renamed={!r} left={!r}".format(
                          err, renamed, silly, left))
    # 6a'''b5: the inode-reuse vector for a release that did NOT release (the "stays" outcome): the
    # release leaves this run's lock in place, a peer then removes that lock and acquires its own between
    # the read-back and the closing listing, and the stat family is remapped under the ext4 reuse rule
    # above. On a non-released outcome the lock identity's descriptor stays HELD through the closing
    # comparison, so the reuse never arms and the closing listing names the peer's lock as another run's
    # (red against a descriptor closed right after the read-back on every outcome: the peer's lock lands
    # on the freed inode and the closing listing reads it as this run's own journal lock).
    if not census:
        skipped.append(("release-stays-peer-reused-inode", no_census))
    else:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            reuse = {}
            seen = {}
            try:
                root, files = fixture(temp)
                os.makedirs(root / JOURNAL_REL)
                me = sys.modules[__name__]
                journal_root_here = _journal_root(root)
                real_listing = _journal_listing
                real_acquire_now = _journal.acquire_lock

                def release_leaves_lock(journal_root):
                    seen["release ran"] = True      # left in place: the read-back records "stays"

                def peer_before_closing_listing(root_fd, keep=None, raised=None):
                    if keep is None and seen.pop("release ran", None):
                        lock = str(journal_root_here / "lock")
                        own_st = _real_lstat(lock)
                        own = (own_st.st_dev, own_st.st_ino)
                        os.unlink(lock)
                        real_acquire_now(journal_root_here, "opf-adopt-selftest-peer")
                        peer_st = _real_lstat(lock)
                        seen["own"] = own
                        seen["peer"] = (peer_st.st_dev, peer_st.st_ino)
                        seen["own freed"] = _inode_free(own)
                        if seen["own freed"]:
                            reuse[(peer_st.st_dev, peer_st.st_ino)] = own
                    return real_listing(root_fd, keep=keep, raised=raised)

                def compose_refused_stays(ops):
                    raise AdoptApplyError("an injected compose refusal")
                stat_w, lstat_w, fstat_w = (_reuse_remapped(_real_stat, reuse),
                                            _reuse_remapped(_real_lstat, reuse),
                                            _reuse_remapped(_real_fstat, reuse))
                with mock.patch.object(_journal, "release_lock", release_leaves_lock), \
                        mock.patch.object(me, "_journal_listing", peer_before_closing_listing), \
                        mock.patch.object(os, "stat", stat_w), \
                        mock.patch.object(os, "lstat", lstat_w), \
                        mock.patch.object(os, "fstat", fstat_w), \
                        mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {stat_w}), \
                        mock.patch.object(os, "supports_follow_symlinks",
                                          os.supports_follow_symlinks | {stat_w, lstat_w}):
                    err = refusal(run_adopt_transaction, root, rid, compose_refused_stays)
                check("release-stays-peer-reused-inode",
                      "another run's journal lock, not this run's" in (err or "")
                      and ", this run's journal lock" not in (err or "")
                      and "STAYS" in (err or "")
                      and seen.get("own freed") is False and not reuse,
                      observed="refusal={!r} seen={!r}".format(err, seen))
            finally:
                os.umask(saved_umask)
    # 6a'''b6: the inode-reuse vector for an INTERRUPTED release read-back: the release really releases,
    # the read-back (_lock_identity with no keep) is interrupted before it records an outcome, a peer
    # acquires before the closing listing, and the stat family is remapped under the ext4 reuse rule
    # above. With no recorded outcome the lock identity's descriptor stays HELD through the closing
    # comparison, so the reuse never arms and the interrupt's note names the peer's lock as another run's
    # (red against a descriptor closed whatever the release left: the peer's lock lands on the freed inode
    # and the note reads it as this run's own journal lock).
    if not census:
        skipped.append(("release-readback-interrupted-peer-reused-inode", no_census))
    else:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            reuse = {}
            seen = {}
            note = None
            try:
                root, files = fixture(temp)
                os.makedirs(root / JOURNAL_REL)
                me = sys.modules[__name__]
                journal_root_here = _journal_root(root)
                real_listing = _journal_listing
                real_identity = _lock_identity
                real_release = _journal.release_lock
                real_acquire_now = _journal.acquire_lock

                def release_for_real_then_arm(journal_root):
                    lock = str(journal_root_here / "lock")
                    own_st = _real_lstat(lock)
                    seen["own"] = (own_st.st_dev, own_st.st_ino)
                    real_release(journal_root)
                    seen["release ran"] = True

                def interrupted_readback(jr_fd, keep=None, raised=None):
                    if keep is None and seen.get("release ran"):
                        raise KeyboardInterrupt("an injected interrupt inside the release read-back")
                    return real_identity(jr_fd, keep=keep, raised=raised)

                def peer_before_closing_listing2(root_fd, keep=None, raised=None):
                    if keep is None and seen.pop("release ran", None):
                        real_acquire_now(journal_root_here, "opf-adopt-selftest-peer")
                        peer_st = _real_lstat(str(journal_root_here / "lock"))
                        seen["peer"] = (peer_st.st_dev, peer_st.st_ino)
                        seen["own freed"] = _inode_free(seen["own"])
                        if seen["own freed"]:
                            reuse[(peer_st.st_dev, peer_st.st_ino)] = seen["own"]
                    return real_listing(root_fd, keep=keep, raised=raised)

                def compose_refused_readback(ops):
                    raise AdoptApplyError("an injected compose refusal")
                stat_w, lstat_w, fstat_w = (_reuse_remapped(_real_stat, reuse),
                                            _reuse_remapped(_real_lstat, reuse),
                                            _reuse_remapped(_real_fstat, reuse))
                with mock.patch.object(_journal, "release_lock", release_for_real_then_arm), \
                        mock.patch.object(me, "_lock_identity", interrupted_readback), \
                        mock.patch.object(me, "_journal_listing", peer_before_closing_listing2), \
                        mock.patch.object(os, "stat", stat_w), \
                        mock.patch.object(os, "lstat", lstat_w), \
                        mock.patch.object(os, "fstat", fstat_w), \
                        mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {stat_w}), \
                        mock.patch.object(os, "supports_follow_symlinks",
                                          os.supports_follow_symlinks | {stat_w, lstat_w}):
                    try:
                        run_adopt_transaction(root, rid, compose_refused_readback)
                        note = "the injected interrupt did not propagate"
                    except KeyboardInterrupt as exc:
                        note = "; ".join(getattr(exc, "__notes__", []) or [])
                    except AdoptApplyError as exc:
                        note = "refused instead: {}".format(exc)
                check("release-readback-interrupted-peer-reused-inode",
                      "another run's journal lock, not this run's" in (note or "")
                      and ", this run's journal lock" not in (note or "")
                      and seen.get("own freed") is False and not reuse,
                      observed="note={!r} seen={!r}".format(note, seen))
            finally:
                os.umask(saved_umask)
    # 6a'''': a refusal past the journal's own pre-INTENT capture (its transaction directory, frames.log and
    # preimages written) never says "nothing written": the composer names each entry the run's two journal
    # listings differ by, over a journal that predates the run and over one the run created (red against
    # the hand-written claim of a "refused before it opened" branch).
    for prebuilt in (True, False):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            if prebuilt:
                os.makedirs(root / JOURNAL_REL)
            real_capture = _journal.capture_preimages

            def capture_then_refuse(*args):
                real_capture(*args)
                raise _journal.JournalError("injected refusal after capture, before INTENT")
            with mock.patch.object(_journal, "capture_preimages", capture_then_refuse):
                err = refusal(run_adopt_transaction, root, rid, compose_full(files))
            txn_rel = JOURNAL_REL + "/" + rid
            check("pre-intent-capture-leftover-named-" + ("prebuilt" if prebuilt else "created"),
                  "refused before it opened" in (err or "") and "nothing written" not in (err or "")
                  and "{} (directory)".format(txn_rel) in (err or "")
                  and "{}/frames.log (file)".format(txn_rel) in (err or "")
                  and "{}/preimages".format(txn_rel) in (err or "")
                  and (root / txn_rel / "frames.log").is_file() and lock_free(root))
    # 6a5: a journal listing that could not read part of the tree never authorizes "nothing written", even
    # when both listings failed alike; it says the journal could not be fully observed (red against a
    # comparison in which two equal "unlisted" markers vanish). The fault hits the listing only.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        jst = os.stat(root / JOURNAL_REL)
        real_capture, real_listdir = _journal.capture_preimages, os.listdir

        def capture_then_refuse(*args):
            real_capture(*args)
            raise _journal.JournalError("injected refusal after capture, before INTENT")

        def journal_unlistable(target="."):
            if isinstance(target, int) and (os.fstat(target).st_dev, os.fstat(target).st_ino) == (
                    jst.st_dev, jst.st_ino):
                raise PermissionError(13, "an injected listing fault")
            return real_listdir(target)
        with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                mock.patch.object(os, "listdir", journal_unlistable):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("pre-intent-unlisted-journal-no-claim", "refused before it opened" in (err or "")
              and "nothing written" not in (err or "") and "could not be fully observed" in (err or "")
              and (root / JOURNAL_REL / rid / "frames.log").is_file())
    # 6a6: the closing observation is bound to the journal directory this run wrote to: one swapped in
    # during preparation, then moved away with the original restored, never reads as "nothing written"
    # (red against a closing listing compared by path alone).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        jpath = root / JOURNAL_REL
        os.makedirs(jpath)
        real_capture, real_ensure, real_release = (_journal.capture_preimages, _journal.ensure_journal_dirs,
                                                   _journal.release_lock)

        def capture_then_refuse(*args):
            real_capture(*args)
            raise _journal.JournalError("injected refusal after capture, before INTENT")

        def swap_in(*args):
            os.rename(jpath, root / "journal-aside")
            return real_ensure(*args)

        def release_then_restore(journal_root):
            real_release(journal_root)
            os.rename(jpath, root / "retained-journal")
            os.rename(root / "journal-aside", jpath)
        with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                mock.patch.object(_journal, "ensure_journal_dirs", swap_in), \
                mock.patch.object(_journal, "release_lock", release_then_restore):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("pre-intent-journal-swap-restore-named", "refused before it opened" in (err or "")
              and "nothing written" not in (err or "") and "no longer resolves" in (err or "")
              and (root / "retained-journal" / rid / "frames.log").is_file())
    # 6a7: content changed in place on the inode this run's acquire wrote (here malformed) is an unresolved
    # outcome, never a release: a committed transaction raises AdoptCommittedLockError naming the altered
    # lock (red against a read-back that presumes release on any other content).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        real_release = _journal.release_lock

        def malformed_then_release(journal_root):
            (Path(journal_root) / "lock").write_bytes(b"{")
            real_release(journal_root)
        with mock.patch.object(_journal, "release_lock", malformed_then_release):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
                altered_error = None
            except AdoptApplyError as exc:
                altered_error = exc
        check("commit-release-altered-lock-unresolved", isinstance(altered_error, AdoptCommittedLockError)
              and "altered and stays" in str(altered_error) and "lock was released" not in str(altered_error)
              and (root / JOURNAL_REL / "lock").read_bytes() == b"{")
    # 6a8: another run's lock, planted between the clean check and this run's acquire, is never this run's:
    # the refusal attributes nothing to this run and names the lock as another run's (red against the
    # "NOT everything this run wrote" lead-in, or an unattributed lock label).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        real_acquire = _journal.acquire_lock

        def foreign_first(journal_root, session_id):
            (Path(journal_root) / "lock").write_bytes(json.dumps(dict(
                uid=os.getuid(), pid=os.getppid(), session="another-run", **{"pid-start": ""}, utc="2026-01-01T00:00:00Z")).encode())
            return real_acquire(journal_root, session_id)
        with mock.patch.object(_journal, "acquire_lock", foreign_first):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("acquire-foreign-lock-not-attributed", "cannot take the adoption journal lock" in (err or "")
              and "not attributed to this run" in (err or "") and "another run's journal lock" in (err or "")
              and "this run wrote" not in (err or "") and "nothing written" not in (err or "")
              and not lock_free(root))
    # 6a9: an interrupt inside acquire after the lock file exists records the lock as observed, never as
    # "this run holds no journal lock" (red against a lock state presumed untaken).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)

        def created_then_interrupted(journal_root, session_id):
            real_acquire(journal_root, session_id)
            raise KeyboardInterrupt("injected acquire interrupt")
        notes = None
        with mock.patch.object(_journal, "acquire_lock", created_then_interrupted):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except KeyboardInterrupt as exc:
                notes = " ".join(getattr(exc, "__notes__", []))
        check("acquire-interrupt-lock-observed", notes is not None and "holds no journal lock" not in notes
              and "lock acquire was interrupted" in notes and not lock_free(root))
    # 6a10: the closing listing keys the journal by the descriptor it opened, never by its stat by name: a
    # swap timed between that stat and that open, over the journal this run wrote to (swap) or with the
    # original restored there (swap and restore), never reads as "nothing written" and names the change
    # (red against a listing keyed by the stat before its open). The same swap at `adopt` never states as
    # gone the journal beneath it, which that listing did not see (red against "gone" for unseen entries).
    for comp, restore in (("journal", False), ("journal", True), ("adopt", False), ("adopt", True)):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root, files = fixture(temp)
            os.makedirs(root / JOURNAL_REL)
            jpath = root / JOURNAL_REL if comp == "journal" else root / JOURNAL_REL.rsplit("/", 1)[0]
            armed, started = [], []
            real_capture, real_ensure, real_release, real_open = (
                _journal.capture_preimages, _journal.ensure_journal_dirs, _journal.release_lock, os.open)

            def capture_then_refuse(*args):
                real_capture(*args)
                raise _journal.JournalError("injected refusal after capture, before INTENT")

            def swap_in(*args):
                if restore:
                    os.rename(jpath, root / "journal-aside")
                return real_ensure(*args)

            def swap_before_open(path, flags, *args, **kwargs):
                if armed and path == comp and flags & os.O_DIRECTORY:
                    armed.clear()
                    os.rename(jpath, root / "retained-journal")
                    if restore:
                        os.rename(root / "journal-aside", jpath)
                    else:
                        os.mkdir(jpath)
                return real_open(path, flags, *args, **kwargs)
            opener = mock.patch.object(os, "open", swap_before_open)

            def release_then_arm(journal_root):
                # os.open is wrapped only from here, past require_containment, so the next open of the
                # journal by name is the closing listing's, right after its stat
                real_release(journal_root)
                armed.append(True)
                started.append(opener.start())
            try:
                with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                        mock.patch.object(_journal, "ensure_journal_dirs", swap_in), \
                        mock.patch.object(_journal, "release_lock", release_then_arm):
                    err = refusal(run_adopt_transaction, root, rid, compose_full(files))
            finally:
                if started:
                    opener.stop()
            held = root / "retained-journal" / ("" if comp == "journal" else "journal")
            check("pre-intent-{}-stat-open-swap-named".format(comp) + ("-restore" if restore else ""),
                  "refused before it opened" in (err or "") and "nothing written" not in (err or "")
                  and "{} could not be listed (it changed between this listing's stat and its open".format(
                      jpath.relative_to(root).as_posix()) in (err or "") and "this run wrote" in (err or "")
                  and " are gone" not in (err or "") and (held / rid / "frames.log").is_file())
    # 6a11: a journal path component that changed since the run began (a concurrent cleanup removed the
    # journal tree after this run took what it creates, so its preparation recreated it) may be this run's:
    # the refusal never hides it under the neutral lead-in (red against attributing it to no one).
    # Test-hermeticity: each removed component's inode is HELD OPEN (an O_DIRECTORY descriptor taken
    # before its rmdir, closed only after the verdict), so no filesystem can hand the recreated component
    # the SAME inode number back (ext4 reuses a freed inode number immediately, tmpfs and btrfs never do)
    # and hide the recreation from the closing listing's (kind, st_dev, st_ino) comparison. The process
    # umask is pinned for the vector (restored in the finally) so an inherited owner-bit umask cannot make
    # a recreated component unlistable, an ambient cause outside this check.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        saved_umask = os.umask(0o022)
        pinned_components = []
        seen = {}
        try:
            root, files = fixture(temp)
            os.makedirs(root / JOURNAL_REL)
            real_ensure = _journal.ensure_journal_dirs

            def cleaned_then_ensure(*args):
                for rel in reversed(_journal_components()):
                    pinned_components.append(os.open(str(root / rel), os.O_RDONLY | os.O_DIRECTORY))
                    st = os.fstat(pinned_components[-1])
                    seen[rel + " removed"] = (st.st_dev, st.st_ino)
                    os.rmdir(root / rel)
                made = real_ensure(*args)
                for rel in _journal_components():
                    try:
                        st = os.lstat(root / rel)
                        seen[rel + " recreated"] = (st.st_dev, st.st_ino)
                    except OSError as exc:
                        seen[rel + " recreated"] = str(exc)
                return made

            def compose_refused(ops):
                raise AdoptApplyError("an injected compose refusal")
            with mock.patch.object(_journal, "ensure_journal_dirs", cleaned_then_ensure):
                err = refusal(run_adopt_transaction, root, rid, compose_refused)
            check("journal-component-recreated-attributed", "injected compose refusal" in (err or "")
                  and "not attributed to this run" not in (err or "") and "this run wrote" in (err or "")
                  and "{} (directory), a component of the journal path".format(JOURNAL_REL) in (err or "")
                  and "nothing written" not in (err or ""),
                  observed="refusal={!r} component (st_dev, st_ino) before/after={!r}".format(err, seen))
        finally:
            os.umask(saved_umask)
            _close_held(pinned_components)
    # 6a11b: only a directory at a journal path component may be this run's recreation; anything else there
    # is named by its type, never as possibly recreated by this run (red against the label for any type).
    comp_dir = _entry_named(JOURNAL_REL, ("directory", 1, 2), [], rid, None, False)
    comp_file = _entry_named(JOURNAL_REL, ("file", 1, 2), [], rid, None, False)
    check("journal-component-type-named", comp_dir[1] and "possibly recreated by this run" in comp_dir[0]
          and not comp_file[1] and "possibly recreated" not in comp_file[0]
          and "{} (file), a component of the journal path that changed since this run began, now a file".format(
              JOURNAL_REL) in comp_file[0])
    # 6a11c: an entry at a journal directory this run created is named by its observed type: only a
    # directory there is "a journal directory this run created" (red against that label for any type).
    made_dir = _entry_named(JOURNAL_REL, ("directory", 1, 2), [JOURNAL_REL], rid, None, False)
    made_file = _entry_named(JOURNAL_REL, ("file", 1, 2), [JOURNAL_REL], rid, None, False)
    check("journal-created-type-named", made_dir == ("{} (directory), a journal directory this run "
                                                     "created".format(JOURNAL_REL), True)
          and made_file == ("{} (file), now a file where this run had created a journal directory".format(
              JOURNAL_REL), True))
    # 6a11d: the inode-reuse vector for the journal-component listing, no test-side pin: a concurrent
    # cleanup removes the journal path components after the run's first listing, its preparation recreates
    # them, and the stat family is remapped so each recreated component reports its predecessor's
    # (st_dev, st_ino), but ONLY while no descriptor of this process still holds that inode (the ext4 reuse
    # rule above). The first listing's component descriptors are HELD by production until the closing
    # comparison, so the remap never arms and the refusal names the recreated components (red against a
    # first listing that closes them: every remap arms, the recreated components compare as unchanged, and
    # the refusal falsely says "nothing written" over three directories this run recreated and left).
    if not census:
        skipped.append(("journal-component-reused-inode-named", no_census))
    else:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            saved_umask = os.umask(0o022)
            reuse = {}
            seen = {}
            try:
                root, files = fixture(temp)
                os.makedirs(root / JOURNAL_REL)
                real_ensure = _journal.ensure_journal_dirs

                def cleaned_then_ensure(*args):
                    removed = {}
                    for rel in reversed(_journal_components()):
                        rm_st = _real_lstat(root / rel)
                        removed[rel] = (rm_st.st_dev, rm_st.st_ino)
                        os.rmdir(root / rel)
                    made = real_ensure(*args)
                    for rel in _journal_components():
                        new_st = _real_lstat(root / rel)
                        freed = _inode_free(removed[rel])
                        seen[rel] = dict(removed=removed[rel], recreated=(new_st.st_dev, new_st.st_ino),
                                         freed=freed)
                        if freed:
                            reuse[(new_st.st_dev, new_st.st_ino)] = removed[rel]
                    return made

                def compose_refused(ops):
                    raise AdoptApplyError("an injected compose refusal")
                stat_w, lstat_w, fstat_w = (_reuse_remapped(_real_stat, reuse),
                                            _reuse_remapped(_real_lstat, reuse),
                                            _reuse_remapped(_real_fstat, reuse))
                with mock.patch.object(_journal, "ensure_journal_dirs", cleaned_then_ensure), \
                        mock.patch.object(os, "stat", stat_w), \
                        mock.patch.object(os, "lstat", lstat_w), \
                        mock.patch.object(os, "fstat", fstat_w), \
                        mock.patch.object(os, "supports_dir_fd", os.supports_dir_fd | {stat_w}), \
                        mock.patch.object(os, "supports_follow_symlinks",
                                          os.supports_follow_symlinks | {stat_w, lstat_w}):
                    err = refusal(run_adopt_transaction, root, rid, compose_refused)
                check("journal-component-reused-inode-named", "injected compose refusal" in (err or "")
                      and "nothing written" not in (err or "") and "this run wrote" in (err or "")
                      and "{} (directory), a component of the journal path".format(JOURNAL_REL) in (err or "")
                      and not reuse,
                      observed="refusal={!r} components={!r}".format(err, seen))
            finally:
                os.umask(saved_umask)
    # 6a12: a journal path the closing listing could not reach is said to be unobserved, never to "no
    # longer resolve" (red against the binding clause that treats an unreached path as absent), and the
    # journal beneath the unlisted `adopt` is never said to be gone (red against "gone" for unseen entries).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        armed = []
        real_capture, real_release, real_open = _journal.capture_preimages, _journal.release_lock, os.open

        def capture_then_refuse(*args):
            real_capture(*args)
            raise _journal.JournalError("injected refusal after capture, before INTENT")

        def adopt_unopenable(path, flags, *args, **kwargs):
            if path == "adopt" and flags & os.O_DIRECTORY:
                raise PermissionError(13, "an injected open fault")
            return real_open(path, flags, *args, **kwargs)
        opener = mock.patch.object(os, "open", adopt_unopenable)

        def release_then_arm(journal_root):
            real_release(journal_root)      # os.open is wrapped from here: the closing listing's opens
            armed.append(True)
            opener.start()
        try:
            with mock.patch.object(_journal, "capture_preimages", capture_then_refuse), \
                    mock.patch.object(_journal, "release_lock", release_then_arm):
                err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        finally:
            if armed:
                opener.stop()
        check("pre-intent-unreached-journal-unobserved", "refused before it opened" in (err or "")
              and "nothing written" not in (err or "") and "no longer resolves" not in (err or "")
              and "{} could not be observed".format(JOURNAL_REL) in (err or "")
              and " are gone" not in (err or ""))
    # 6a12b: a directory the opening listing stated but could not list hides only what is below it: the
    # journal, removed before the closing listing observed it absent, is named gone (red against a
    # "contents not listed" key that hides the directory itself).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)
        jst = os.stat(root / JOURNAL_REL)
        real_listdir, faults = os.listdir, []

        def journal_unlistable_once(target="."):
            if not faults and isinstance(target, int) and (os.fstat(target).st_dev, os.fstat(target).st_ino) == (
                    jst.st_dev, jst.st_ino):
                faults.append(True)     # the opening listing's only: the closing one lists what is there
                raise PermissionError(13, "an injected listing fault")
            return real_listdir(target)

        def removed_then_refused(*args):
            os.rmdir(root / JOURNAL_REL)
            raise OSError("an injected preparation fault")
        with mock.patch.object(os, "listdir", journal_unlistable_once), \
                mock.patch.object(_journal, "ensure_journal_dirs", removed_then_refused):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("pre-intent-unlisted-journal-gone-named", "cannot prepare the adoption journal" in (err or "")
              and "{}/ could not be listed".format(JOURNAL_REL) in (err or "")
              and "entries present when this run began are gone: {}".format(JOURNAL_REL) in (err or "")
              and "nothing written" not in (err or "") and not (root / JOURNAL_REL).exists())
    # 6a12c: the same rule read directly, one level up (red against hiding `adopt` under its own key); and
    # an entry literally named "*" whose stat fails is that entry alone, never its directory's contents
    # (red against a "contents not listed" key an entry name can take).
    adopt = JOURNAL_REL.rsplit("/", 1)[0]
    said, _ours = _observed({".aiqt": ("directory", 1, 1), adopt: ("directory", 1, 2),
                             adopt + "/": ("unlisted", "an injected fault")}, {".aiqt": ("directory", 1, 1)}, [], rid)
    check("observed-unlisted-contents-dir-gone", ("entries present when this run began are gone: " + adopt) in said
          and "{}/ could not be listed (an injected fault)".format(adopt) in said[0])
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        jdir = Path(temp) / JOURNAL_REL
        os.makedirs(jdir)
        (jdir / "x").write_bytes(b"")
        (jdir / "*").write_bytes(b"")
        real_stat = os.stat

        def star_unstatable(path, *args, **kwargs):
            if path == "*":
                raise PermissionError(13, "an injected stat fault")
            return real_stat(path, *args, **kwargs)
        listing_fd = store._open_dir_nofollow(Path(temp).resolve())
        try:
            first = _journal_listing(listing_fd)
            os.unlink(jdir / "x")
            with mock.patch.object(os, "stat", star_unstatable):
                second = _journal_listing(listing_fd)
        finally:
            os.close(listing_fd)
        said, _ours = _observed(first, second, [], rid)
        check("observed-star-entry-not-contents", second.get(JOURNAL_REL + "/*", ("",))[0] == "unlisted"
              and JOURNAL_REL + "/" not in second
              and ("entries present when this run began are gone: {}/x".format(JOURNAL_REL)) in said)
    # 6a13: an ordinary exception inside acquire, after the lock file exists, is described as an error,
    # never as an interrupt (red against the interrupt wording for every exception).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        os.makedirs(root / JOURNAL_REL)

        def created_then_failed(journal_root, session_id):
            real_acquire(journal_root, session_id)
            raise ValueError("an injected acquire error")
        with mock.patch.object(_journal, "acquire_lock", created_then_failed):
            err = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("acquire-error-lock-not-interrupt", "lock acquire failed with an error" in (err or "")
              and "interrupted" not in (err or "") and "nothing written" not in (err or "")
              and not lock_free(root))

    # 6b: the spec 14.2 apply-side verification checkpoint. A same-length fault injected into the archive
    # copy's own destination write (the staged bytes verify; the DISK bytes differ) is caught by the
    # journal's read-back BEFORE the paired source removal runs: at every after-apply seam each source is
    # either live and byte-identical or archived byte-identically, and the transaction rolls back.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        target = archive_rel(rid, ".working/TODO.md")
        armed = []
        real_verify = _journal._verify_staged_digest

        def arming_verify(op, payload):
            real_verify(op, payload)
            if op["path"] == target:
                armed.append(True)

        real_write = _journal._write_all

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)      # same length, wrong bytes: only a read-back can catch it
            return real_write(fd, data)

        def preserved_now():
            for pth, payload in files.items():
                live, cp = root / pth, root / archive_rel(rid, pth)
                if live.exists() and live.read_bytes() == payload:
                    continue
                if cp.exists() and cp.read_bytes() == payload:
                    continue
                return False
            return True

        seams = []

        def observing_kill(name):
            if name.startswith("after-apply-"):
                seams.append(preserved_now())

        with mock.patch.object(_journal, "_verify_staged_digest", arming_verify), \
                mock.patch.object(_journal, "_write_all", faulty_write), \
                mock.patch.object(_journal, "_kill_point", observing_kill):
            corrupt = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("archive-write-fault-never-leaves-source-unpreserved", bool(seams) and all(seams))
        check("archive-write-fault-rolls-back",
              corrupt is not None and "rolled back" in corrupt and snapshot(root) == before
              and lock_free(root))

    # 6c: the spec 14.2 rollback-side checkpoint. A fault injected into the SOURCE-restoring write of a
    # genuine rollback is caught by the restored-bytes read-back: the reversal STOPS before the aborted
    # run's archive copy is discarded and before any prestate is reported, the journal lock is RETAINED
    # so the next run refuses into reconcile(), and the explicit reconcile then completes the rollback.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        src2 = ".working/TODO.md"
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        armed = []
        real_restore = _journal._restore_preimage

        def arming_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "remove" and op.get("path") == src2:
                armed.append(True)
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        real_write = _journal._write_all

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)
            return real_write(fd, data)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                mock.patch.object(_journal, "_restore_preimage", arming_restore), \
                mock.patch.object(_journal, "_write_all", faulty_write):
            faulted = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("restore-fault-never-reports-prestate",
              faulted is not None and "rolled back" not in faulted and "reconcile" in faulted)
        check("restore-fault-retains-archive",
              (root / archive_rel(rid, src2)).exists()
              and (root / archive_rel(rid, src2)).read_bytes() == files[src2])
        check("restore-fault-retains-lock", not lock_free(root))
        _journal.release_lock(_journal_root(root))   # the crashed owner, in this in-process simulation
        check("restore-fault-reconciles-to-prestate",
              (rid, "rolled-back") in reconcile(root) and snapshot(root) == before and lock_free(root))

    # 6d: rollback ORDER is pinned, not only final equality: at the moment each archive copy's create-undo
    # discards it, its source has ALREADY been restored byte-identical (spec 14.2: restore, verify, then
    # discard, never reversed), so a reversal that unlinks the copies first goes red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        real_restore = _journal._restore_preimage
        arch = _archive_root(rid) + "/"
        misordered = []

        def watching_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "create" and str(op.get("path", "")).startswith(arch):
                source = op["path"][len(arch):]
                live = root / source
                if source in files and not (live.exists() and live.read_bytes() == files[source]):
                    misordered.append(op["path"])
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                mock.patch.object(_journal, "_restore_preimage", watching_restore):
            aborted2 = refusal(run_adopt_transaction, root, rid, compose_full(files))
        check("abort-restores-sources-before-archive-discard",
              aborted2 is not None and "rolled back" in aborted2 and not misordered
              and snapshot(root) == before and lock_free(root))

    # 6e: RESTARTABLE restoration of a read-only source (spec 14.2). The same fault as 6c, but the source
    # is READ-ONLY (0400) or writable (0644, the control leg): the restore checkpoint verifies the bytes
    # BEFORE the recorded prestate mode is installed, so the faulted attempt fails closed exactly as 6c
    # and the LATER reconcile still finishes to the exact prestate bytes AND mode once the fault is gone,
    # never wedging on an EACCES reopen of the half-restored file.
    for ro_mode in (0o644, 0o400):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = Path(temp).resolve()
            keep = "legacy/KEEP.md"
            prior = b"prior read-only bytes\n"
            (root / keep).parent.mkdir(parents=True)
            (root / keep).write_bytes(prior)
            (root / keep).chmod(ro_mode)

            def compose_keep(ops):
                ops.archive_occupying(keep, plan_digest(prior))

            real_verify = _journal._verify_staged_digest

            def failing_inventory(op, payload):
                if op["path"] == inventory_rel(rid):
                    raise _journal.JournalError("injected failure at the inventory publication")
                return real_verify(op, payload)

            armed = []
            real_restore = _journal._restore_preimage

            def arming_restore(jr_fd, txn_dir, rfd, op, op_index=0):
                if op.get("op") == "remove" and op.get("path") == keep:
                    armed.append(True)
                return real_restore(jr_fd, txn_dir, rfd, op, op_index)

            real_write = _journal._write_all

            def faulty_write(fd, data):
                if armed:
                    armed.clear()
                    data = b"X" * len(data)
                return real_write(fd, data)

            with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                    mock.patch.object(_journal, "_restore_preimage", arming_restore), \
                    mock.patch.object(_journal, "_write_all", faulty_write):
                faulted = refusal(run_adopt_transaction, root, rid, compose_keep)
            tag = "restore-fault-mode-{:04o}".format(ro_mode)
            check(tag + "-fails-closed",
                  faulted is not None and "rolled back" not in faulted and "reconcile" in faulted
                  and txn_state(root, rid) == "open" and not lock_free(root))
            _journal.release_lock(_journal_root(root))   # the crashed owner, in this in-process simulation
            outcomes, _why = attempt(reconcile, root)
            check(tag + "-reconciles-to-exact-prestate",
                  outcomes is not None and (rid, "rolled-back") in outcomes
                  and (root / keep).read_bytes() == prior
                  and stat.S_IMODE((root / keep).lstat().st_mode) == ro_mode and lock_free(root))

    # 6f: the verification READ itself failing (an injected EIO) while a read-only source restores must
    # not wedge recovery either: the checkpoint runs before the prestate mode is installed, so the later
    # reconcile reopens the still-owner-writable file and lands the exact prestate bytes and mode.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        keep = "legacy/KEEP.md"
        prior = b"prior read-only bytes\n"
        (root / keep).parent.mkdir(parents=True)
        (root / keep).write_bytes(prior)
        (root / keep).chmod(0o400)

        def compose_keep(ops):
            ops.archive_occupying(keep, plan_digest(prior))

        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        real_read_back = _journal._read_back_verify
        eio = [True]

        def eio_read_back(fd, expected_sha, path, what):
            if eio and what == "restored":
                eio.clear()
                raise OSError(5, "injected fault at the verification read")
            return real_read_back(fd, expected_sha, path, what)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_inventory), \
                mock.patch.object(_journal, "_read_back_verify", eio_read_back):
            faulted = refusal(run_adopt_transaction, root, rid, compose_keep)
        check("verify-read-fault-fails-closed",
              faulted is not None and "rolled back" not in faulted and "reconcile" in faulted
              and txn_state(root, rid) == "open" and not lock_free(root))
        _journal.release_lock(_journal_root(root))   # the crashed owner, in this in-process simulation
        outcomes, _why = attempt(reconcile, root)
        check("verify-read-fault-reconciles-to-exact-prestate",
              outcomes is not None and (rid, "rolled-back") in outcomes
              and (root / keep).read_bytes() == prior
              and stat.S_IMODE((root / keep).lstat().st_mode) == 0o400 and lock_free(root))

    # 6g: the restore PRIMITIVE stays restartable at prestate modes the shell cannot even compose (its
    # bounded contained read cannot read a 0000 source, but the SHARED engine must finish any restore its
    # journal records): a faulted attempt fails closed AND reverts the temporary owner-write grant, and
    # the retry lands the exact prestate bytes and mode, on both the in-place (live file present) and the
    # recreate (live file absent) paths.
    for ro_mode in (0o400, 0o000):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = Path(temp).resolve()
            prior = b"the recorded preimage bytes\n"
            (root / "legacy").mkdir()
            live = root / "legacy/LOCKED.md"
            live.write_bytes(b"?" * len(prior))          # a faulted earlier restore's debris
            live.chmod(ro_mode)
            root_fd = store._open_dir_nofollow(root)
            try:
                _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
                txn_dir = _journal_root(root) / rid
                (txn_dir / "preimages").mkdir(parents=True)
                (txn_dir / "preimages/0").write_bytes(prior)
                op = dict(op="remove", path="legacy/LOCKED.md",
                          prestate=dict(kind="file", mode=ro_mode, size=len(prior), payload="0",
                                        sha256=_sha256(prior)))
                jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
                try:
                    def try_restore():
                        try:
                            _journal._restore_preimage(jr_fd, txn_dir, root_fd, op)
                            return None
                        except _journal.JournalError as exc:
                            return str(exc)

                    real_write = _journal._write_all
                    armed = [True]

                    def faulty_write(fd, data):
                        if armed:
                            armed.clear()
                            data = b"X" * len(data)
                        return real_write(fd, data)

                    tag = "restore-primitive-mode-{:04o}".format(ro_mode)
                    with mock.patch.object(_journal, "_write_all", faulty_write):
                        faulted = try_restore()
                    check(tag + "-fault-fails-closed-and-reverts-the-grant",
                          faulted is not None and stat.S_IMODE(live.lstat().st_mode) == ro_mode)
                    retried = try_restore()              # the fault is gone: the restore must finish
                    got_mode = stat.S_IMODE(live.lstat().st_mode)
                    live.chmod(0o600)                    # the fixture's own grant, for the byte assert
                    check(tag + "-retry-restores-exact-prestate",
                          retried is None and got_mode == ro_mode and live.read_bytes() == prior)
                    live.unlink()                        # the RECREATE path: absent live file
                    armed.append(True)
                    with mock.patch.object(_journal, "_write_all", faulty_write):
                        refaulted = try_restore()
                    reretried = try_restore()            # the fault is gone: the restore must finish
                    got_mode = stat.S_IMODE(live.lstat().st_mode)
                    live.chmod(0o600)                    # the fixture's own grant, for the byte assert
                    check(tag + "-recreate-fault-then-retry-exact",
                          refaulted is not None and reretried is None and got_mode == ro_mode
                          and live.read_bytes() == prior)
                finally:
                    _journal._close_fd_quietly(jr_fd)
            finally:
                os.close(root_fd)

    # 6h: the apply-side checkpoint guards a PAIRED WRITE's own destination too (spec 14.2): a same-length
    # fault in the write's kernel-visible bytes (the staged bytes verify) raises AT the checkpoint and
    # HALTS the engine's op sequence, so the op after the faulted write never runs. This is the engine
    # seam the shell's write-as-removal pairing relies on; a checkpoint skipped on the write branch would
    # let the sequence continue and go red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        old_doc, witness = b"old doc bytes\n", b"witness\n"
        new_doc = b"NEW DOC BYTES\n"
        (root / "doc.md").write_bytes(old_doc)
        (root / "doc.md").chmod(0o644)
        (root / "witness.md").write_bytes(witness)
        (root / "witness.md").chmod(0o644)
        wop = dict(op="write", path="doc.md",
                   prestate=dict(kind="file", mode=0o644, size=len(old_doc), payload="0",
                                 sha256=_sha256(old_doc)),
                   poststate=dict(kind="file", mode=0o644))
        wop["poststate"]["content-sha256"] = _sha256(new_doc)
        rop = dict(op="remove", path="witness.md", poststate=dict(kind="absent"),
                   prestate=dict(kind="file", mode=0o644, size=len(witness), payload="1",
                                 sha256=_sha256(witness)))
        real_write = _journal._write_all
        armed = [True]

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)
            return real_write(fd, data)

        root_fd = store._open_dir_nofollow(root)
        try:
            with mock.patch.object(_journal, "_write_all", faulty_write):
                try:
                    _journal.apply_ops(root_fd, [wop, rop], lambda op: new_doc)
                    halted = None
                except _journal.JournalError as exc:
                    halted = str(exc)
        finally:
            os.close(root_fd)
        check("paired-write-destination-fault-fails-closed",
              halted is not None and "recorded digest" in halted)
        check("paired-write-destination-fault-halts-the-sequence",
              (root / "witness.md").exists() and (root / "witness.md").read_bytes() == witness)

    # 6i: the rollback-side checkpoint on the IN-PLACE restore path (a write op's undo, the live file
    # still present): a same-length fault in the restoring write REFUSES to report a rollback (the journal
    # stays open, fail-closed into recovery), and the recovery then lands the exact prestate; a reversal
    # that accepted the faulty in-place restore and published its terminal frame would go red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        old_doc, new_doc = b"old doc bytes\n", b"NEW DOC BYTES\n"
        (root / "doc.md").write_bytes(old_doc)
        (root / "doc.md").chmod(0o644)
        wop = dict(op="write", path="doc.md", poststate=dict(kind="file", mode=0o644))
        wop["poststate"]["content-sha256"] = _sha256(new_doc)
        never = dict(op="create", path="never.md", poststate=dict(kind="file", mode=0o644))
        never["poststate"]["content-sha256"] = _sha256(b"never staged\n")
        staged = dict()
        staged["doc.md"] = new_doc                       # never.md unstaged: raises INSIDE apply

        def reader(op):
            data = staged.get(op["path"])
            if not isinstance(data, bytes):
                raise _journal.JournalError("no staged bytes for {!r} (fail-closed)".format(op["path"]))
            return data

        armed = []
        real_restore = _journal._restore_preimage

        def arming_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "write":
                armed.append(True)
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        real_write = _journal._write_all

        def faulty_write(fd, data):
            if armed:
                armed.clear()
                data = b"X" * len(data)
            return real_write(fd, data)

        root_fd = store._open_dir_nofollow(root)
        jr_fd = None
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            header = dict(kind=KIND, run_id=rid, phase="base", operation=OPERATION)
            with mock.patch.object(_journal, "_restore_preimage", arming_restore), \
                    mock.patch.object(_journal, "_write_all", faulty_write):
                try:
                    _journal.run_transaction(root_fd, jr_fd, _journal_root(root), rid, header,
                                             [wop, never], reader, SESSION_ID)
                    outcome = "complete"
                except _journal.JournalError:
                    outcome = _journal.classify_state(jr_fd, _journal_root(root) / rid)
            check("restore-in-place-fault-never-reports-rollback", outcome == "open")
            check("restore-in-place-fault-then-recover-exact",
                  _journal.recover(jr_fd, _journal_root(root) / rid, root_fd) == "rolled-back"
                  and (root / "doc.md").read_bytes() == old_doc
                  and stat.S_IMODE((root / "doc.md").lstat().st_mode) == 0o644)
        finally:
            if jr_fd is not None:
                _journal._close_fd_quietly(jr_fd)
            os.close(root_fd)

    # 6j: the temporary owner-write grant itself (fix #3). The grant is HARD-LINK GATED: a multiply-
    # linked unwritable target is refused BEFORE any chmod (codex round-3: a second name, here OUTSIDE
    # the product root, must stay exactly as found, under an injected fstat fault, under an uninjected
    # retry, and under an interruption raced into the reopen). On a singly-linked target the grant is
    # reverted on each exercised failed exit its handler observes (an fstat fault, an identity
    # refusal, an interruption at the reopen; the journal's own comments disclose the exits the
    # handler never sees), a raced-in symlink at the grant chmod fails closed as a JournalError (never a raw
    # ValueError), the grant requires OWNERSHIP (a non-owned unwritable file fails closed with a named
    # JournalError, simulated by an EPERM chmod), and the recreate path's verify-before-mode ordering
    # is pinned (at the restore checkpoint the recreated file still holds the temporary 0600, the
    # exact prestate mode installed only after). Round-4 additions: an interruption delivered
    # IMMEDIATELY after the grant chmod returns (codex's settrace SIGINT, which used to escape
    # between the chmod and the revert-protected reopen) now reaches the revert, and a fault AT
    # the post-checkpoint prestate-mode install, BEFORE its fchmod takes effect, DELIBERATELY
    # leaves the grant behind a NAMED JournalError, the next reconcile finishing directly through
    # the still-writable file. Round-5 addition: a fault at the durability fsync AFTER that fchmod
    # took effect leaves the exact prestate mode with NO grant, behind a JournalError naming that
    # state, the uninjected retry then landing the exact prestate bytes and mode.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        product = Path(temp).resolve() / "product"       # the product root under restore ...
        product.mkdir()
        outside = Path(temp).resolve() / "outside-link.md"   # ... and a second name OUTSIDE it
        prior = b"the recorded preimage bytes\n"
        (product / "legacy").mkdir()
        live = product / "legacy/LOCKED.md"

        def mode_of(p):
            return stat.S_IMODE(p.lstat().st_mode)

        def reset(linked=False):
            for f in (live, outside):
                if f.exists():
                    f.unlink()
            live.write_bytes(b"?" * len(prior))          # a faulted earlier restore's debris
            if linked:
                os.link(live, outside)
            live.chmod(0o400)

        root_fd = store._open_dir_nofollow(product)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
            txn_dir = _journal_root(product) / rid
            (txn_dir / "preimages").mkdir(parents=True)
            (txn_dir / "preimages/0").write_bytes(prior)
            op = dict(op="remove", path="legacy/LOCKED.md",
                      prestate=dict(kind="file", mode=0o400, size=len(prior), payload="0",
                                    sha256=_sha256(prior)))
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            try:
                def try_restore():
                    try:
                        _journal._restore_preimage(jr_fd, txn_dir, root_fd, op)
                        return None
                    except _journal.JournalError as exc:
                        return str(exc)

                # the multiply-linked target is refused BEFORE any chmod: the injected identity-fstat
                # fault (codex's reproduction) is never even reached, and BOTH names keep 0400 exactly
                reset(linked=True)

                def eio_fstat(fd, _real=os.fstat, _ino=live.lstat().st_ino):
                    fst = _real(fd)
                    if fst.st_ino == _ino:
                        raise OSError(5, "injected fault at the identity fstat")
                    return fst

                with mock.patch.object(os, "fstat", eio_fstat):
                    refused = try_restore()
                check("grant-hardlink-refused-before-any-chmod",
                      refused is not None and "hard links" in refused
                      and mode_of(live) == 0o400 and mode_of(outside) == 0o400
                      and outside.lstat().st_nlink == 2)
                retried = try_restore()                  # uninjected retry: still refused, still 0400
                check("grant-hardlink-uninjected-retry-still-as-found",
                      retried is not None and "hard links" in retried
                      and mode_of(live) == 0o400 and mode_of(outside) == 0o400)

                # an interruption raced into the reopen window: for a multiply-linked inode there IS
                # no grant window (no chmod ever runs), so the outside name stays exactly as found
                reset(linked=True)
                opens = []

                def interrupting_open(p, flags, *a, _real=os.open, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None:
                        opens.append(p)
                        if len(opens) == 2:
                            raise KeyboardInterrupt()
                    return _real(p, flags, *a, **kw)

                with mock.patch.object(os, "open", interrupting_open):
                    try:
                        interrupted = try_restore()
                    except BaseException as exc:
                        interrupted = "escaped: " + type(exc).__name__
                check("grant-hardlink-interrupt-leaves-modes-as-found",
                      interrupted is not None and "hard links" in interrupted and len(opens) == 1
                      and mode_of(live) == 0o400 and mode_of(outside) == 0o400)

                # fault-and-retry on the singly-linked target: an fstat fault AFTER the grant reverts
                # the grant on that exit (fail-closed), and the uninjected retry lands the exact
                # prestate bytes and mode
                reset()
                armed = [True]

                def eio_fstat_once(fd, _real=os.fstat, _ino=live.lstat().st_ino):
                    fst = _real(fd)
                    if armed and fst.st_ino == _ino:
                        armed.clear()
                        raise OSError(5, "injected fault at the identity fstat")
                    return fst

                with mock.patch.object(os, "fstat", eio_fstat_once):
                    faulted = try_restore()
                check("grant-fstat-fault-fails-closed-and-reverts-the-grant",
                      faulted is not None and mode_of(live) == 0o400)
                refinished = try_restore()               # the fault is gone: the restore must finish
                check("grant-fstat-fault-retry-restores-exact-prestate",
                      refinished is None and mode_of(live) == 0o400 and live.read_bytes() == prior)

                # an identity refusal (a swapped inode) also reverts the grant before failing closed
                reset()

                def swapped_fstat(fd, _real=os.fstat, _ino=live.lstat().st_ino):
                    fst = _real(fd)
                    if fst.st_ino == _ino:
                        fake = list(fst)
                        fake[1] = fst.st_ino + 1         # a different inode: the identity check refuses
                        return os.stat_result(fake)
                    return fst

                with mock.patch.object(os, "fstat", swapped_fstat):
                    swapped = try_restore()
                check("grant-identity-refusal-reverts-the-grant",
                      swapped is not None and "swapped" in swapped and mode_of(live) == 0o400)

                # an interruption AT the reopen of a singly-linked target reverts the grant too
                reset()
                opens2 = []

                def interrupting_open2(p, flags, *a, _real=os.open, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None:
                        opens2.append(p)
                        if len(opens2) == 2:
                            raise KeyboardInterrupt()
                    return _real(p, flags, *a, **kw)

                escaped = []
                with mock.patch.object(os, "open", interrupting_open2):
                    try:
                        try_restore()
                    except KeyboardInterrupt:
                        escaped.append(True)
                check("grant-interrupt-at-reopen-reverts-the-grant",
                      escaped == [True] and mode_of(live) == 0o400)

                # a symlink raced in at the grant chmod is a JournalError, never a raw ValueError
                reset()

                def valueerror_chmod(p, m, *a, _real=os.chmod, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None and m == 0o600:
                        raise ValueError("chmod: cannot use dir_fd and follow_symlinks together")
                    return _real(p, m, *a, **kw)

                with mock.patch.object(os, "chmod", valueerror_chmod):
                    try:
                        raced = try_restore()
                    except ValueError:
                        raced = None                     # a raw ValueError escaped: red
                check("grant-raced-symlink-valueerror-is-journalerror",
                      raced is not None and "symlink" in raced and mode_of(live) == 0o400)

                # the grant requires OWNERSHIP: a non-owned unwritable file (the kernel refuses the
                # chmod with EPERM, simulated here) fails closed with the NAMED JournalError
                reset()

                def eperm_chmod(p, m, *a, _real=os.chmod, **kw):
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None and m == 0o600:
                        raise PermissionError(1, "Operation not permitted")
                    return _real(p, m, *a, **kw)

                with mock.patch.object(os, "chmod", eperm_chmod):
                    denied = try_restore()
                check("grant-requires-ownership-fails-closed-named",
                      denied is not None and "ownership" in denied and mode_of(live) == 0o400)

                # codex round-4: an interruption delivered IMMEDIATELY after the grant chmod
                # returns (settrace fires SIGINT on the first traced line in _restore_preimage
                # after the real chmod applied the grant) used to escape between the chmod and
                # the revert-protected reopen with the 0600 grant still installed; the revert
                # protection now spans the grant chmod itself, so the KeyboardInterrupt escapes
                # with the mode already reverted to exactly as found
                reset()
                sigint_fired = []
                grant_applied = []

                def flagging_chmod(p, m, *a, _real=os.chmod, **kw):
                    result = _real(p, m, *a, **kw)
                    if p == "LOCKED.md" and kw.get("dir_fd") is not None and m == 0o600:
                        grant_applied.append(True)
                    return result

                restore_code = _journal._restore_preimage.__code__

                def sigint_tracer(frame, event, arg):
                    if frame.f_code is not restore_code:
                        return None
                    if event == "line" and grant_applied and not sigint_fired:
                        sigint_fired.append(True)
                        os.kill(os.getpid(), signal.SIGINT)
                    return sigint_tracer

                escaped_interrupt = []
                prior_trace = sys.gettrace()
                # The REAL SIGINT below must surface as a KeyboardInterrupt regardless of
                # the inherited disposition (a harness may launch this process with SIGINT
                # ignored, and Python then installs no default_int_handler): pin the default
                # handler for exactly this window and restore the inherited one after.
                prior_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
                try:
                    with mock.patch.object(os, "chmod", flagging_chmod):
                        sys.settrace(sigint_tracer)
                        try:
                            try_restore()
                        except KeyboardInterrupt:
                            escaped_interrupt.append(True)
                finally:
                    sys.settrace(prior_trace)
                    signal.signal(signal.SIGINT, prior_handler)
                check("grant-interrupt-after-grant-chmod-reverts-the-grant",
                      escaped_interrupt == [True] and sigint_fired == [True]
                      and mode_of(live) == 0o400)
                check("grant-interrupt-after-grant-chmod-retry-restores-exact",
                      try_restore() is None and mode_of(live) == 0o400
                      and live.read_bytes() == prior)

                # claude round-4: a fault AT the post-checkpoint prestate-mode install is not an
                # anonymous escape silently retaining the grant: the checkpoint has already
                # verified the restored bytes, so this exit DELIBERATELY leaves the owner-write
                # grant (the recreate path's restartability posture) and says so in a NAMED
                # JournalError; the next reconcile then reopens the still-writable file and
                # finishes installing the exact prestate mode without another grant cycle
                reset()
                fchmod_armed = [True]

                def eio_prestate_fchmod(fd_, m, _real=os.fchmod):
                    if fchmod_armed and m == 0o400:
                        fchmod_armed.clear()
                        raise OSError(5, "injected fault at the prestate-mode fchmod")
                    return _real(fd_, m)

                with mock.patch.object(os, "fchmod", eio_prestate_fchmod):
                    retained = try_restore()
                check("grant-post-checkpoint-mode-fault-names-the-retained-grant",
                      retained is not None and "deliberately left" in retained
                      and mode_of(live) == 0o600 and live.read_bytes() == prior)
                check("grant-post-checkpoint-mode-fault-reconcile-finishes-directly",
                      try_restore() is None and mode_of(live) == 0o400
                      and live.read_bytes() == prior)

                # codex round-5 (= claude F1): the OTHER post-checkpoint state. A fault at the
                # durability fsync AFTER the prestate fchmod took effect leaves the live mode
                # ALREADY the exact prestate mode -- NO grant remains -- and the NAMED
                # JournalError must say so instead of claiming a retained grant; the uninjected
                # retry re-runs the whole grant cycle (the prestate mode is read-only) and lands
                # the exact prestate bytes and mode
                reset()
                fsync_fault_armed = []

                def arming_prestate_fchmod(fd_, m, _real=os.fchmod):
                    result = _real(fd_, m)
                    if m == 0o400:
                        fsync_fault_armed.append(True)
                    return result

                def eio_post_mode_fsync(fd_, _real=os.fsync):
                    if fsync_fault_armed:
                        fsync_fault_armed.clear()
                        raise OSError(5, "injected fault at the post-mode durability fsync")
                    return _real(fd_)

                with mock.patch.object(os, "fchmod", arming_prestate_fchmod), \
                        mock.patch.object(os, "fsync", eio_post_mode_fsync):
                    unsynced = try_restore()
                check("grant-post-mode-fsync-fault-names-no-grant-remains",
                      unsynced is not None and "no grant remains" in unsynced
                      and "deliberately left" not in unsynced
                      and mode_of(live) == 0o400 and live.read_bytes() == prior)
                check("grant-post-mode-fsync-fault-retry-restores-exact",
                      try_restore() is None and mode_of(live) == 0o400
                      and live.read_bytes() == prior)

                # the RECREATE path's verify-before-mode ordering is pinned: AT the restore checkpoint
                # the recreated file still holds the temporary owner-rw 0600 (so a faulted checkpoint
                # leaves a reopenable file), and the exact 0400 prestate mode is installed only after
                live.unlink()                            # absent live file: the recreate path
                interim = []
                real_read_back = _journal._read_back_verify

                def observing_read_back(fd, expected_sha, path_, what):
                    if what == "restored":
                        interim.append(stat.S_IMODE(os.fstat(fd).st_mode))
                    return real_read_back(fd, expected_sha, path_, what)

                with mock.patch.object(_journal, "_read_back_verify", observing_read_back):
                    recreated = try_restore()
                check("recreate-verifies-before-prestate-mode-installed",
                      recreated is None and interim == [0o600] and mode_of(live) == 0o400
                      and live.read_bytes() == prior)
            finally:
                _journal._close_fd_quietly(jr_fd)
        finally:
            os.close(root_fd)

    # 7: an interrupted apply (INTENT without a terminal frame, through the journal's kill-point seam)
    # refuses every later run until the EXPLICIT reconcile, which works from the journal alone and never
    # resolves the store; it reverses fully mid-archive, and completes forward once every op landed.
    class _Interrupt(Exception):
        pass

    def interrupt_when(pred):
        def fake(name):
            if name.startswith("after-apply-") and pred():
                raise _Interrupt(name)
        return fake

    def reconcile_without_store(root):
        with mock.patch.object(store, "resolve_store", side_effect=AssertionError("reconcile resolved")):
            try:
                return reconcile(root)
            except (AssertionError, AdoptApplyError):
                return []

    # 7 order: the EXPLICIT recovery's rollback order is pinned exactly as the in-run reversal's (6d): at
    # the moment recover()'s create-undo discards an archive copy, its source has ALREADY been restored
    # byte-identical, so an explicit reconcile that unlinked the copies first would go red here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        man = ".working/toml/manifest.toml"
        gone = interrupt_when(lambda: (root / archive_rel(rid, man)).exists() and not (root / man).exists())
        with mock.patch.object(_journal, "_kill_point", gone):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        check("reconcile-order-fixture-left-open", txn_state(root, rid) == "open" and lock_free(root))
        arch = _archive_root(rid) + "/"
        misordered = []
        real_restore = _journal._restore_preimage

        def watching_restore(jr_fd, txn_dir, rfd, op, op_index=0):
            if op.get("op") == "create" and str(op.get("path", "")).startswith(arch):
                source = op["path"][len(arch):]
                live = root / source
                if source in files and not (live.exists() and live.read_bytes() == files[source]):
                    misordered.append(op["path"])
            return real_restore(jr_fd, txn_dir, rfd, op, op_index)

        with mock.patch.object(_journal, "_restore_preimage", watching_restore):
            outcomes, _why = attempt(reconcile, root)
        check("reconcile-restores-sources-before-archive-discard",
              outcomes is not None and (rid, "rolled-back") in outcomes and not misordered
              and snapshot(root) == before and lock_free(root))

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        manifest = ".working/toml/manifest.toml"
        mid = interrupt_when(lambda: (root / archive_rel(rid, manifest)).exists() and (root / manifest).exists())
        with mock.patch.object(_journal, "_kill_point", mid):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
                interrupted = False
            except (_Interrupt, AdoptApplyError) as exc:
                interrupted = isinstance(exc, _Interrupt)
        check("interrupt-leaves-open-transaction", interrupted and txn_state(root, rid) == "open")
        partial = snapshot(root)
        blocked = refusal(run_adopt_transaction, root, other_run,
                          lambda ops: ops.create(evidence_home_rel(other_run) + "/x.md", b"x\n"))
        check("interrupted-journal-refuses-next-run", blocked is not None and "reconcile" in blocked)
        check("interrupted-journal-refusal-writes-nothing", snapshot(root) == partial)
        check("reconcile-back-from-journal-alone", (rid, "rolled-back") in reconcile_without_store(root))
        check("reconcile-back-restores-prestate", snapshot(root) == before and lock_free(root))
        check("reconciled-journal-admits-next-run",
              attempt(run_adopt_transaction, root, other_run, compose_full(files))[0] == other_run)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        landed = interrupt_when(lambda: (root / inventory_rel(rid)).exists())
        with mock.patch.object(_journal, "_kill_point", landed):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        check("reconcile-forward-from-journal-alone", (rid, "rolled-forward") in reconcile_without_store(root))
        check("reconcile-forward-verified-poststate",
              txn_state(root, rid) == "complete" and verify_bundle(root, rid).status == VALID
              and not (root / ".working/TODO.md").exists())

    # 7b: the reconcile-first ORDER: with BOTH an open transaction and a resolvable store present, the
    # refusal is the journal's (the operator is pointed at reconcile()), never the lease refusal the
    # posture gate would raise, because the journal is inspected FIRST (the module-docstring discipline).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        import _opf_init
        root, files = fixture(temp)
        man = ".working/toml/manifest.toml"
        mid = interrupt_when(lambda: (root / archive_rel(rid, man)).exists())
        with mock.patch.object(_journal, "_kill_point", mid):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        (root / man).write_text(_opf_init.build_manifest(), encoding="utf-8")
        ordered = refusal(run_adopt_transaction, root, other_run, compose_full(files))
        check("journal-checked-before-store-posture",
              store.resolve_store(root).status == store.RESOLVED and txn_state(root, rid) == "open"
              and ordered is not None and "reconcile" in ordered and "lease" not in ordered)

    # 7c: a confirmed-dead owner's stale lock is broken only through _journal.reconcile_and_claim_stale,
    # and reconcile() reports the TRUE outcome of the recovery that break performed, never 'terminal' for
    # its own work.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        before = snapshot(root)
        man = ".working/toml/manifest.toml"
        mid = interrupt_when(lambda: (root / archive_rel(rid, man)).exists() and (root / man).exists())
        with mock.patch.object(_journal, "_kill_point", mid):
            try:
                run_adopt_transaction(root, rid, compose_full(files))
            except (_Interrupt, AdoptApplyError):
                pass
        dead = dead_pid()
        owner_row = dict(uid=os.getuid(), pid=dead, session="opf-adopt-selftest-dead",
                         utc="2026-09-17T12:00:00Z")
        owner_row["pid-start"] = ""
        (_journal_root(root) / "lock").write_text(json.dumps(owner_row), encoding="utf-8")
        outcomes = reconcile(root) if dead is not None else []
        check("stale-lock-reconcile-reports-true-outcomes",
              dead is not None and txn_state(root, rid) == "rolled-back"
              and (rid, "rolled-back") in outcomes and snapshot(root) == before and lock_free(root))

    # 8: a held journal lock refuses a transaction and a reconcile (never seized).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        journal_root = _journal_root(root)
        root_fd = store._open_dir_nofollow(root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        finally:
            os.close(root_fd)
        _journal.acquire_lock(journal_root, "opf-adopt-selftest-peer")
        check("held-lock-refuses-transaction", "it is never seized" in
              (refusal(run_adopt_transaction, root, rid, compose_full(files)) or ""))
        check("held-lock-refuses-reconcile",
              "possibly-live owner" in (refusal(reconcile, root) or ""))
        _journal.release_lock(journal_root)

    # 8b (round 3): reconcile reads the journal lock beneath the HELD contained journal-root fd (the
    # round-2 journal_state posture), never by re-resolving the journal PATH: the journal path is
    # swapped for a symlink to an empty decoy right after the contained open (the deterministic
    # stand-in for a concurrent writer), and the product's own held lock must STILL refuse -- a
    # path-based owner read saw the decoy's absent lock and reconciled straight through to a clean
    # empty outcome list here.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = fixture(temp)
        journal_root = _journal_root(root)
        root_fd = store._open_dir_nofollow(root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        finally:
            os.close(root_fd)
        _journal.acquire_lock(journal_root, "opf-adopt-selftest-live-peer")
        decoy = Path(temp).resolve() / "decoy"
        decoy.mkdir()
        _real_open_jr = _journal.open_journal_root_fd
        _jr_swap_fired = []

        def _racing_open_jr(rfd, rel):
            fd = _real_open_jr(rfd, rel)
            _jr_swap_fired.append(rel)   # the injection provably ran (round 4)
            os.rename(journal_root, str(journal_root) + ".moved")
            os.symlink(decoy, journal_root)
            return fd

        with mock.patch.object(_journal, "open_journal_root_fd", _racing_open_jr):
            _res, why = attempt(reconcile, root)
        check("reconcile-lock-read-beneath-held-jr-fd",
              _jr_swap_fired != [] and why is not None and "possibly-live owner" in why)

    # 9: live re-observation over a throwaway fixture.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "legacy.md").write_bytes(b"legacy\n")
        (root / "link.md").symlink_to("legacy.md")
        root_fd = store._open_dir_nofollow(root)
        try:
            check("observe-absent", observe_live(root_fd, "missing.md") == dict(kind="absent"))
            seen = observe_live(root_fd, "legacy.md")
            check("observe-file-digest", seen.get("size") == 7 and seen.get("sha256") == _sha256(b"legacy\n"))
            check("observe-symlink-refused",
                  "not a regular file" in (refusal(observe_live, root_fd, "link.md") or ""))
            check("observe-traversal-refused",
                  "not a contained relative file path" in (refusal(observe_live, root_fd, "../escape") or ""))
        finally:
            os.close(root_fd)

    # round 7 MINOR 1: _verify_bundle_at computes the retained-chain KEY before duplicating the held
    # home descriptor, so a bundle path _check_rel refuses can never strand the dup outside dir_fds
    # (where the verification cleanup cannot close it). The malformed-bundle raise still propagates
    # and no duplicated descriptor survives it.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "home").mkdir()
        root_fd = store._open_dir_nofollow(root)
        home_fd = os.open("home", os.O_RDONLY | os.O_DIRECTORY, dir_fd=root_fd)
        _m1_seen = dict()
        _m1_real_dup = os.dup

        def _m1_dup(fd):
            nfd = _m1_real_dup(fd)
            _m1_seen["dup"] = nfd
            return nfd

        os.dup = _m1_dup
        try:
            try:
                _verify_bundle_at(root_fd, "x", "a/../b", home_fd=home_fd)
                _m1_out = "returned"
            except _journal.JournalError:
                _m1_out = "raised"
        finally:
            os.dup = _m1_real_dup
        check("r7-verify-bundle-malformed-rel-raises", _m1_out == "raised")
        _m1_leaked = False
        if "dup" in _m1_seen:
            try:
                os.fstat(_m1_seen["dup"])
                _m1_leaked = True
            except OSError:
                pass
        check("r7-verify-bundle-home-dup-never-stranded", not _m1_leaked)
        if _m1_leaked:                    # a pre-fix run leaks it; close so the failing suite stays clean
            try:
                os.close(_m1_seen["dup"])
            except OSError:
                pass
        os.close(home_fd)
        os.close(root_fd)

    # round 7 MINOR 2: _journal_txn_dirs(hold=True) builds the returned entry path BEFORE opening the
    # transaction descriptor, so a path construction that raises (a malformed journal_root) can never
    # strand a just-opened txn descriptor outside `out`, where the except-cleanup cannot close it. The
    # construction error still propagates and no held txn descriptor survives it.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "txn-1").mkdir()
        jr_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        _m2_opened = []
        _m2_real_open = os.open

        def _m2_open(*args, **kwargs):
            fd = _m2_real_open(*args, **kwargs)
            if kwargs.get("dir_fd") == jr_fd:
                _m2_opened.append(fd)
            return fd

        os.open = _m2_open
        try:
            try:
                _journal._journal_txn_dirs(jr_fd, None, strict=True, hold=True)
                _m2_out = "returned"
            except TypeError:
                _m2_out = "raised"
        finally:
            os.open = _m2_real_open
        check("r7-txn-dirs-held-path-error-raises", _m2_out == "raised")
        _m2_left = []
        for _fd in _m2_opened:
            try:
                os.fstat(_fd)
            except OSError:
                continue
            _m2_left.append(_fd)
        check("r7-txn-dirs-no-held-descriptor-survives-path-error", not _m2_left)
        _close_held(_m2_left)             # a pre-fix run leaks it; close so the failing suite stays clean
        os.close(jr_fd)

    # 10: apply takes only a plan/v2 (spec 14.1): a v1-marked plan is never apply input. The v1 schema no
    # longer ships (the plan-v2 schema replaced it, U8), and apply refuses on the format marker before it
    # reads any other field, so the v1 input is the bare marker.
    v1 = apply_plan(dict(format="opf.adoption.plan/v1"))
    check("apply-v1-plan-refused", v1.status == CANNOT and any("never apply input" in f for f in v1.findings))
    # the schema's own canonical v2 plan is apply input ONLY with a captured approval binding both of its
    # digests (spec 14.1): no approval, an approval naming another plan_digest or inventory_digest (each
    # flip red against a gate that ignores the binding), an unknown approval key and a multi-line actor
    # are each refused; the bare v2 marker propagates the validator's verdict.
    canon_plan = schema.canonical_plan()
    check("apply-plan-format-is-the-schema-marker", PLAN_V2_FORMAT == schema.PLAN_FORMAT
          and canon_plan.get("format") == PLAN_V2_FORMAT)
    canon = apply_plan(canon_plan)
    check("apply-canonical-v2-plan-without-approval-refused",
          canon.status == CANNOT and any("no captured approval" in f for f in canon.findings))
    bound = dict(actor="adopter", approved_at="2026-09-17T12:00:00Z", plan_digest=canon_plan["plan_digest"],
                 inventory_digest=canon_plan["inventory_digest"])
    check("apply-canonical-v2-plan-with-binding-approval-valid", apply_plan(canon_plan, bound).status == VALID)
    for key in ("plan_digest", "inventory_digest"):
        other = dict(bound)
        other[key] = "sha256:" + "f" * 64
        res = apply_plan(canon_plan, other)
        check("apply-approval-{}-flip-refused".format(key),
              res.status == CANNOT and any("does not bind" in f for f in res.findings))
    res = apply_plan(canon_plan, dict(bound, note="also approves something else"))
    check("apply-approval-unknown-key-refused",
          res.status == CANNOT and any("unknown key" in f for f in res.findings))
    res = apply_plan(canon_plan, dict(bound, actor="adopter\nsecond line"))
    check("apply-approval-multiline-actor-refused",
          res.status == CANNOT and any("actor" in f for f in res.findings))
    # the one approval follows its plan (spec 14.1): an approved_at before the plan's created_at is refused (red
    # against a gate that checks the timestamp's shape alone).
    res = apply_plan(canon_plan, dict(bound, approved_at="1999-01-01T00:00:00Z"))
    check("apply-approval-before-plan-refused",
          res.status == CANNOT and any("precedes" in f for f in res.findings))
    v2 = apply_plan(dict(format=PLAN_V2_FORMAT))
    check("apply-v2-marked-plan-refused",
          v2.status == INVALID and any("missing required key" in f for f in v2.findings))
    nontable = apply_plan([])
    check("apply-non-table-refused",
          nontable.status == CANNOT and any("not a table" in f for f in nontable.findings))

    # 11: the stage driver over a fixture the real planner froze (a non-occupying retire source and a kept
    # file at a NOT-ADOPTED root that is a git repository, its HEAD the plan's bound revision). Approve is
    # write-free and binds the plan's two digests; a moved revision, an unobservable one, a stale tree or a
    # changed worksheet refuses into a fresh plan; a hand-edited or unsealed plan refuses; an approval for
    # another plan, or one before its plan, refuses; a non-clean adoption journal refuses the stage; with
    # unlanded ops in its plan, apply refuses before ANY write. With composing handlers patched in for the
    # landed-op case, apply still refuses while the driver's mandatory receipt stage is unlanded; with it
    # patched in too, apply persists the plan and approval in the run's bundle, dispatches every row in plan
    # order in the apply stage and then the receipt stage, and leaves the frozen retire source in place. The
    # retirement-partition flip, a handler writing the live tree directly, one swallowing that refusal, one
    # opening its own transaction and one starting a thread each refuse with the WHOLE tree unchanged, as
    # does a receipt stage refusing only on the second, locked composition; a second apply, and an approve
    # of the applied run, refuse on the one-apply rule itself.
    import copy as _copy   # this function binds `copy` as a local name (section 4), shadowing the module
    import shutil
    import _opf_adopt_plan as planner
    import _opf_init

    def _digest(data):
        return "sha256:" + _sha256(data)

    def _bytes(path):
        """A file's bytes, or None when absent, so a missing file is a recorded check failure."""
        return path.read_bytes() if path.is_file() else None

    def _snapshot(root):
        """Every path beneath root but the fixture repository's own .git, which only the revision vectors
        move (through git itself), with each file's bytes."""
        return dict((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None)
                    for p in sorted(root.rglob("*")) if p.relative_to(root).parts[0] != ".git")

    def _sheet(root, revision):
        bindings = dict(schema.canonical_plan_bindings(), revision=revision or "0" * 40)
        manifest = _opf_init.build_manifest()
        views = sorted(v["target"] for v in tomllib.loads(manifest)["views"].values())
        rows = [dict(op="init-store", store_root=".", members=[dict(
                    path=".working/toml/manifest.toml", digest=_digest(manifest.encode("utf-8")))]),
                dict(op="render-views", store_root=".",
                     members=[dict(path=v, digest=_digest(v.encode("utf-8"))) for v in views]),
                schema.enforcement_install_op(bindings["enforcement"])]
        sheet = dict(sources=["keep.md", "legacy.md"], targets=[".opf/hooks/pre-commit"], product="opf",
                     decisions=[dict(path="keep.md", disposition="keep", actor="fixture"),
                                dict(path="legacy.md", disposition="retire", actor="fixture")],
                     ops=rows, bindings=bindings)
        obs = planner.investigate(root, sources=sheet["sources"], targets=sheet["targets"])
        sheet["expected_observation_digest"] = (
            tomllib.loads(obs.observation.decode("utf-8"))["observation_digest"] if obs.observation else "")
        return sheet

    def _freeze(root, sheet, nonce="0123456789abcdef"):
        res = planner.plan(root, now=now, run_nonce=nonce, **_copy.deepcopy(sheet))
        return res.plan if res.status == VALID else None

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = Path(temp).resolve()
        (root / "keep.md").write_bytes(b"kept\n")
        (root / "legacy.md").write_bytes(b"legacy rules\n")
        head = _selftest_git_commit(root)
        check("driver-fixture-git-revision", head is not None and attempt(observe_revision, root)[0] == head)
        sheet = _sheet(root, head)
        plan_bytes = _freeze(root, sheet)
        check("driver-fixture-plan-frozen", plan_bytes is not None)
        plan_bytes = plan_bytes or b""
        plan_doc, _err = attempt(frozen_plan, plan_bytes)
        check("driver-frozen-plan-reproved", isinstance(plan_doc, dict)
              and plan_doc == tomllib.loads(plan_bytes.decode("utf-8")))
        plan_doc = plan_doc or dict(run_id=rid, sources=[], ops=[])
        frid = plan_doc["run_id"]
        check("driver-frozen-plan-non-canonical-refused", "canonical emitted form" in
              (refusal(frozen_plan, plan_bytes + b"# an adopter note\n") or ""))
        edited = dict(plan_doc, created_at="2026-01-01T00:00:00Z")
        check("driver-frozen-plan-unsealed-edit-refused", "does not seal" in
              (refusal(frozen_plan, emit_checked(edited).encode("utf-8")) or ""))
        v1_bytes = emit_checked(dict(plan_doc, format="opf.adoption.plan/v1")).encode("utf-8")
        check("driver-frozen-plan-v1-refused", "never apply input" in (refusal(frozen_plan, v1_bytes) or ""))

        # approve: write-free, the receipt's approval shape, binding both plan digests.
        before = _snapshot(root)
        approval_bytes, err = attempt(capture_approval, root, plan_bytes, sheet, "adopter", now)
        check("driver-approve-captures", isinstance(approval_bytes, bytes) and err is None)
        approval_bytes = approval_bytes or b""
        approval_doc = tomllib.loads(approval_bytes.decode("utf-8")) if approval_bytes else dict()
        check("driver-approval-binds-plan", set(approval_doc) == set(schema.APPROVAL_REQUIRED)
              and approval_doc.get("plan_digest") == plan_doc.get("plan_digest")
              and approval_doc.get("inventory_digest") == plan_doc.get("inventory_digest")
              and approval_doc.get("approved_at") == "2026-09-17T12:00:00Z")
        check("driver-approve-writes-nothing", _snapshot(root) == before)
        check("driver-approve-multiline-actor-refused", "actor" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter\nsecond", now) or ""))
        # the approval follows its plan: an approval instant before the plan's created_at is refused.
        check("driver-approve-before-plan-refused", "precedes" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter",
                       now - datetime.timedelta(days=1)) or ""))
        # revision-only drift: an EMPTY commit advances HEAD with every inventoried byte unchanged, so only
        # the observed revision can see it; approve and apply both refuse into a fresh plan.
        moved = _selftest_git_commit(root)
        check("driver-fixture-revision-moved", moved is not None and moved != head and _snapshot(root) == before)
        check("driver-approve-revision-drift-refused", "revision moved" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        check("driver-apply-revision-drift-refused", "revision moved" in
              (refusal(run_apply, root, plan_bytes, approval_bytes, sheet) or ""))
        check("driver-fixture-revision-restored", _selftest_git_set_head(root, head)
              and attempt(observe_revision, root)[0] == head)
        # an unobservable revision (no repository at the root) refuses, never assumed fresh.
        os.rename(root / ".git", root / "git-aside")
        check("driver-approve-unobservable-revision-refused", "revision" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        os.rename(root / "git-aside", root / ".git")
        # git's own diagnosis (here a repository another user owns) reaches the refusal, printable and bounded.
        said = b"fatal: detected dubious ownership in repository at '/x'\n\x1b[31m" + b"y" * 2000
        with mock.patch.object(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, b"", said)):
            err = refusal(observe_revision, root) or ""
        check("driver-unobservable-revision-names-git-diagnosis", "dubious ownership" in err
              and "\x1b" not in err and len(err) < 1000
              and "ignores the global and system git configuration, so a safe.directory set there" in err)
        # stale observation: a source's bytes change after the plan froze -> refuse into a fresh plan.
        (root / "legacy.md").write_bytes(b"edited after planning\n")
        check("driver-approve-stale-observation-refused", "fresh plan" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        (root / "legacy.md").write_bytes(b"legacy rules\n")
        # a changed worksheet (an attributed decision revised) freezes a different plan -> refused.
        revised = _copy.deepcopy(sheet)
        revised["decisions"][1]["actor"] = "another"
        check("driver-approve-revised-worksheet-refused", "different plan" in
              (refusal(capture_approval, root, plan_bytes, revised, "adopter", now) or ""))
        # reconcile-first: an interrupted adoption transaction refuses the stage (the planner never reads
        # the adoption journal, so without this gate approve would capture over it).
        (root / JOURNAL_REL / "txn").mkdir(parents=True)
        jfd = os.open(str(root / JOURNAL_REL), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _journal.publish(jfd, root / JOURNAL_REL / "txn", _journal.F_INTENT, dict(txn="txn", ops=[]))
        finally:
            os.close(jfd)
        check("driver-approve-open-journal-refused", "must be reconciled" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or ""))
        check("driver-plan-stage-open-journal-refused", "must be reconciled" in
              (refusal(require_clean_journal, root) or ""))
        shutil.rmtree(root / ".aiqt")
        check("driver-fixture-restored", _snapshot(root) == before)

        # apply in THIS build: the retire-file row is landed, so apply refuses before any write (no journal,
        # no bundle) on what is still unlanded (the driver's mandatory receipt stage, and any other plan op
        # whose slice has not landed), never on the retire-file row, and the frozen retire source stays
        # byte-identical in place: the
        # retire row's gating is the retirement partition (the driver-retire-partition vectors below) and the
        # retirement stage after the green completion check, which no adoption interface opens in this
        # build. An approval for ANOTHER plan of the same tree refuses on the binding first.
        err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-retire-gated-receipt-unlanded-refused", "not yet executable" in (err or "")
              and "mandatory receipt stage" in (err or "") and "retire-file" not in (err or "")
              and _bytes(root / "legacy.md") == b"legacy rules\n", observed=err)
        check("driver-apply-unlanded-writes-nothing", _snapshot(root) == before)
        other_plan = _freeze(root, sheet, nonce="fedcba9876543210") or b""
        check("driver-apply-approval-binding-flip-refused", "does not bind" in
              (refusal(run_apply, root, other_plan, approval_bytes, sheet) or ""))
        check("driver-apply-edited-approval-refused", "canonical emitted form" in
              (refusal(run_apply, root, plan_bytes, approval_bytes + b"# also\n", sheet) or ""))
        early = emit_checked(dict(approval_doc, approved_at="1999-01-01T00:00:00Z")).encode("utf-8")
        check("driver-apply-approval-before-plan-refused", "precedes" in
              (refusal(run_apply, root, plan_bytes, early, sheet) or ""))
        check("driver-apply-refusals-write-nothing", _snapshot(root) == before)

        # landed ops (patched handlers that compose through the context table).
        seen = []

        def composing(op_row, context):
            seen.append((op_row["op"], context.get("stage"), context["plan"]["plan_digest"],
                         context["approval"]["actor"], context.get("product_root")))
            if op_row["op"] == "retire-file":
                context["ops"].preserve(op_row["path"], op_row["preimage_digest"])
            return schema._ok()

        def receipt(context):
            seen.append((RECEIPT_STAGE, context.get("stage"), context["plan"]["plan_digest"],
                         context["approval"]["actor"], context.get("product_root")))
            return schema._ok()

        def over_eager(op_row, context):
            # a retire handler that removes a non-occupying source at apply (archive-then-remove, which
            # check_apply_ops alone admits as a valid preserve-first pair).
            if op_row["op"] == "retire-file":
                context["ops"].archive_occupying(op_row["path"], op_row["preimage_digest"])
            return schema._ok()

        def rogue_write(op_row, context):
            # a handler writing the live tree directly instead of staging into context["ops"].
            with open(os.path.join(str(context["product_root"]), "rogue.txt"), "wb") as fh:
                fh.write(b"written outside the transaction\n")
            return schema._ok()

        def swallowing(op_row, context):
            # a handler that catches the guard's refusal of its direct mkdir and reports success anyway.
            try:
                os.mkdir(os.path.join(str(context["product_root"]), "rogue-dir"))
            except AdoptApplyError:
                pass
            return schema._ok()

        def own_transaction(op_row, context):
            # a handler opening its own journaled transaction (an init-store delegating to its substrate).
            try:
                run_adopt_transaction(context["product_root"], context["plan"]["run_id"], lambda ops: None)
            except AdoptApplyError:
                pass
            return schema._ok()

        def threaded(op_row, context):
            # a handler handing its direct write to a thread of its own, outside the per-thread guard.
            worker = threading.Thread(target=(Path(str(context["product_root"])) / "via-thread").write_bytes,
                                      args=(b"written from another thread\n",))
            worker.start()
            worker.join()
            return schema._ok()

        passes = []

        def second_pass_refusing(context):
            # VALID in the write-free preflight, refusing when the transaction composes again.
            passes.append(context.get("stage"))
            return schema._ok() if len(passes) == 1 else schema._cannot("a second-pass refusal")

        def landed(handler, stage=receipt):
            stack = contextlib.ExitStack()
            stack.enter_context(mock.patch.dict(OP_HANDLERS, dict.fromkeys(OP_HANDLERS, handler)))
            if stage is not None:
                stack.enter_context(mock.patch.dict(DRIVER_STAGES, {RECEIPT_STAGE: stage}))
            return stack
        # the mandatory receipt stage (spec 14): the planner-produced plan carries no record-adoption row, and
        # with every row's handler landed apply still refuses before any write while the receipt stage is
        # unlanded (red against a driver that enumerates plan rows only).
        rows_named = [row["op"] for row in plan_doc["ops"]]
        with landed(composing, stage=None):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-receipt-stage-unlanded-refused", "record-adoption" not in rows_named
              and "mandatory receipt stage" in (err or "") and "plan op(s)" not in (err or "") and not seen
              and _snapshot(root) == before)
        with landed(over_eager):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-retire-partition-flip-refused", "stays frozen" in (err or "") and "legacy.md" in (err or ""))
        # the partition refusal lands in the write-free preflight: the WHOLE tree is unchanged, no journal
        # directory included (red against a composition that first runs under the prepared journal).
        check("driver-retire-partition-source-untouched",
              _bytes(root / "legacy.md") == b"legacy rules\n" and _snapshot(root) == before)
        # composition rules the driver enforces, not handler convention: a direct write, a direct mkdir
        # whose refusal the handler swallows, and a transaction of the handler's own each refuse the apply
        # with the whole tree unchanged (each red against a driver that trusts its handlers).
        with landed(rogue_write):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-direct-write-refused", "direct effect" in (err or "")
              and not (root / "rogue.txt").exists() and _snapshot(root) == before)
        with landed(swallowing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-swallowed-refusal-refused", "continued past the refusal" in (err or "")
              and not (root / "rogue-dir").exists() and _snapshot(root) == before)
        with landed(own_transaction):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-own-transaction-refused", "nested adoption transaction" in (err or "")
              and _snapshot(root) == before)
        # a handler handing its write to a thread it starts (the guard arms per thread) refuses at the start.
        with landed(threaded):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-handler-thread-start-refused", "direct effect" in (err or "")
              and not (root / "via-thread").exists() and _snapshot(root) == before)
        # a refusal of the SECOND composition, under the journal lock after the preflight passed (a stateful
        # composer, or the tree drifting between the passes), also leaves the WHOLE tree unchanged: the
        # journal directories the run created are removed again (red against a driver that leaves them).
        del passes[:]
        with landed(composing, stage=second_pass_refusing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-second-pass-refusal-tree-untouched", "second-pass refusal" in (err or "")
              and len(passes) == 2 and _snapshot(root) == before)
        # only the directories the run created: a journal ancestor that predates the run stays in place.
        shutil.rmtree(root / ".aiqt", ignore_errors=True)   # isolate from a left-behind journal above
        (root / JOURNAL_REL).parent.mkdir(parents=True)
        ancestor = _snapshot(root)
        del passes[:]
        with landed(composing, stage=second_pass_refusing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-second-pass-refusal-keeps-prior-dirs", "second-pass refusal" in (err or "")
              and len(passes) == 2 and _snapshot(root) == ancestor)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        check("driver-fixture-restored-after-second-pass", _snapshot(root) == before)
        # a journal preparation that fails part-way (its first directory made) leaves the tree unchanged too.

        def partial_journal(root_fd, journal_rel):
            os.mkdir(journal_rel.split("/")[0], dir_fd=root_fd)
            raise OSError("an injected journal preparation fault")
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", partial_journal):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-partial-journal-refusal-tree-untouched", "cannot prepare" in (err or "")
              and _snapshot(root) == before)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a first directory the cleanup walk cannot open (mode 000, as a 0777 umask makes it; the walk is
        # also refused by patch, so the vector holds under any uid) is still removed through its parent: the
        # unreachable deeper directories never end the sweep (red against a cleanup that stops at them).
        real_open_parent = _journal._open_parent
        sealed_dirs = []

        def sealed_journal(root_fd, journal_rel):
            os.mkdir(journal_rel.split("/")[0], 0o000, dir_fd=root_fd)
            sealed_dirs.append(journal_rel)
            raise PermissionError("an injected journal preparation fault")

        def sealed_walk(root_fd, relpath):
            if sealed_dirs and "/" in relpath and relpath.split("/")[0] == JOURNAL_REL.split("/")[0]:
                raise _journal.JournalError("cannot open contained directory component (injected EACCES)")
            return real_open_parent(root_fd, relpath)
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", sealed_journal), \
                mock.patch.object(_journal, "_open_parent", sealed_walk):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-sealed-journal-ancestor-removed", sealed_dirs and "cannot prepare" in (err or "")
              and "INCOMPLETE" not in (err or "") and _snapshot(root) == before)
        if os.path.lexists(root / ".aiqt"):
            os.chmod(root / ".aiqt", 0o700)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a directory the cleanup cannot remove (populated meanwhile) is named in the refusal, with every
        # ancestor it keeps, never a bare "nothing written" (red against a cleanup that reports nothing),
        # and left to the reconcile-first discipline, never to hand removal (red against a refusal that
        # directs the operator to remove what may hold a concurrent run's lock).

        def populated_journal(root_fd, journal_rel):
            os.makedirs(root / journal_rel)
            (root / journal_rel / "stray").write_bytes(b"populated meanwhile\n")
            raise OSError("an injected journal preparation fault")
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", populated_journal):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        rels = ["/".join(JOURNAL_REL.split("/")[:i + 1]) for i in range(len(JOURNAL_REL.split("/")))]
        check("driver-apply-incomplete-cleanup-named", "cannot prepare" in (err or "")
              and "cleanup is INCOMPLETE" in (err or "") and "nothing written" not in (err or "")
              and "no sanctioned path clears" in (err or "")
              and all("{} (not removed".format(r) in (err or "") for r in rels)
              and "remove them" not in (err or "") and "never to hand removal" in (err or "")
              and "reconcile()" in (err or "") and (root / JOURNAL_REL / "stray").is_file())
        # the stray entry itself is named, and only it carries "no sanctioned path clears" (red against a
        # note that names only its parent and attaches that text to every leftover).
        check("driver-apply-stray-entry-named", "{}/stray (file), a stray entry".format(JOURNAL_REL) in (err or "")
              and (err or "").count("no sanctioned path clears") == 1)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a removal whose parent fsync fails is reported as possibly not durable, never as clean, and never
        # as a directory that stays (red against a refusal that calls it a leftover).
        real_fsync = os.fsync

        def fsync_failing(fd):
            if (root / ".aiqt").exists():
                return real_fsync(fd)
            raise OSError("an injected fsync fault")
        with landed(composing), mock.patch.object(_journal, "ensure_journal_dirs", partial_journal), \
                mock.patch.object(os, "fsync", fsync_failing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-cleanup-fsync-failure-named", "cannot prepare" in (err or "")
              and "may not be durable" in (err or "") and "cleanup is UNCONFIRMED" in (err or "")
              and "nothing written" not in (err or "") and "INCOMPLETE" not in (err or "")
              and "STAY" not in (err or "") and _snapshot(root) == before)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a lock-file failure other than a held lock (here the journal directory vanishing under a peer's
        # cleanup) is the named lock refusal, not a raw OSError past the driver.

        def vanishing_lock(journal_root, session_id):
            raise FileNotFoundError(2, "No such file or directory", str(journal_root / "lock"))
        with landed(composing), mock.patch.object(_journal, "acquire_lock", vanishing_lock):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-oserror-named-refusal", "cannot take the adoption journal lock" in (err or "")
              and "left no journal lock" in (err or "") and _snapshot(root) == before)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        # a lock failure PAST the lock's creation, with the journal directories predating the run (so no
        # directory cleanup runs), names what it left: this run's own lock released again, a release whose
        # durability is unconfirmed, a lock that stays, or an unreadable lock left in place; never "nothing
        # written" over a lock left behind (each red against a refusal that neither releases nor names it).
        os.makedirs(root / JOURNAL_REL)
        prior = _snapshot(root)
        lock_path = root / JOURNAL_REL / "lock"
        real_fsync_dir = _journal._fsync_path_dir
        real_write_all = _journal._write_all
        faults = []

        def failing(real, times):
            def faulty(*args):
                if len(faults) < times:
                    faults.append(args)
                    raise OSError("an injected lock fault")
                return real(*args)
            return faulty
        with landed(composing), mock.patch.object(_journal, "_fsync_path_dir", failing(real_fsync_dir, 1)):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        # its lock released durably and the journal listing as found, "nothing written" is the DERIVED claim
        check("driver-apply-lock-sync-failure-released", "cannot take the adoption journal lock" in (err or "")
              and "WAS created, then released again" in (err or "") and "may not be durable" not in (err or "")
              and (err or "").endswith("; nothing written (fail-closed)") and _snapshot(root) == prior)
        del faults[:]
        with landed(composing), mock.patch.object(_journal, "_fsync_path_dir", failing(real_fsync_dir, 2)):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-release-unconfirmed-named", "released again, but the release may not be "
              "durable" in (err or "") and "nothing written" not in (err or "") and _snapshot(root) == prior)
        del faults[:]
        with landed(composing), mock.patch.object(_journal, "_fsync_path_dir", failing(real_fsync_dir, 1)), \
                mock.patch.object(_journal, "release_lock", lambda journal_root: None):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        stayed = lock_path.is_file()
        with landed(composing):
            later = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-left-named", "WAS created and STAYS" in (err or "")
              and "nothing written" not in (err or "") and stayed and "lock is held" in (later or ""))
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        del faults[:]
        with landed(composing), mock.patch.object(_journal, "_write_all", failing(real_write_all, 1)):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-unreadable-named", "present but unreadable" in (err or "")
              and "no sanctioned path clears it" in (err or "") and "lock STAYS" in (err or "")
              and "nothing written" not in (err or "") and lock_path.is_file())
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        # a lock this call did NOT create (here another thread of this same process takes it before this
        # call's create fails with EMFILE) is never released, though process identity matches (red against
        # a release decided by process identity).
        real_acquire = _journal.acquire_lock

        def peer_thread_first(journal_root, session_id):
            real_acquire(journal_root, "opf-adopt-selftest-thread")
            raise OSError(24, "Too many open files")
        with landed(composing), mock.patch.object(_journal, "acquire_lock", peer_thread_first):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-lock-not-created-untouched", "left no journal lock" in (err or "")
              and lock_path.is_file())
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        # the normal release, on a refusal taken under the lock (here before the transaction opens),
        # names a lock that stays or a release that may not be durable, never "nothing written" over it
        # (each red against a release that swallows its failure).

        def unopened(*args):
            raise _journal.JournalError("injected refusal before transaction creation")

        def release_failing(journal_root):
            raise OSError("an injected lock release fault")
        with landed(composing), mock.patch.object(_journal, "run_transaction", unopened), \
                mock.patch.object(_journal, "release_lock", release_failing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-release-failure-named", "refused before it opened" in (err or "")
              and "lock STAYS" in (err or "") and "nothing written" not in (err or "") and lock_path.is_file())
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        # an unreadable lock on the RELEASE path is never called "possibly this run's own unfinished write"
        # (its write finished at acquire; red against the acquire-path wording reused here).

        def release_to_symlink(journal_root):
            os.unlink(str(journal_root / "lock"))
            os.symlink("elsewhere", str(journal_root / "lock"))
        with landed(composing), mock.patch.object(_journal, "run_transaction", unopened), \
                mock.patch.object(_journal, "release_lock", release_to_symlink):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-release-unreadable-named", "present but unreadable" in (err or "")
              and "unfinished write" not in (err or "") and "nothing written" not in (err or "")
              and "{}/lock (symlink)".format(JOURNAL_REL) in (err or ""))
        if os.path.lexists(lock_path):
            os.unlink(lock_path)
        syncs = []

        def second_sync_failing(path):
            syncs.append(path)
            if len(syncs) == 2:
                raise OSError("an injected release fsync fault")
            return real_fsync_dir(path)
        with landed(composing), mock.patch.object(_journal, "run_transaction", unopened), \
                mock.patch.object(_journal, "_fsync_path_dir", second_sync_failing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-release-unconfirmed-named", "refused before it opened" in (err or "")
              and "released, but the release may not be durable" in (err or "")
              and "nothing written" not in (err or "") and _snapshot(root) == prior)
        shutil.rmtree(root / ".aiqt", ignore_errors=True)
        del seen[:]
        (root / "keep.md").write_bytes(b"kept, then edited\n")
        with landed(composing):
            err = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-drift-after-approval-refused", "fresh plan" in (err or "") and not seen)
        (root / "keep.md").write_bytes(b"kept\n")
        with landed(composing):
            txn, err = attempt(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-commits-with-landed-ops", txn == frid and err is None)
        # every row in plan order, then the receipt stage, in the write-free preflight and again in the
        # transaction, each in the apply stage with the plan, approval and product root.
        check("driver-apply-dispatches-every-row-in-order",
              [s[0] for s in seen] == (rows_named + [RECEIPT_STAGE]) * 2
              and all(s[1:] == (APPLY_STAGE, plan_doc["plan_digest"], "adopter", root) for s in seen))
        check("driver-apply-frozen-source-in-place-with-preimage",
              _bytes(root / "legacy.md") == b"legacy rules\n"
              and _bytes(root / archive_rel(frid, "legacy.md")) == b"legacy rules\n")
        check("driver-apply-persists-plan-and-approval",
              _bytes(root / plan_rel(frid)) == plan_bytes and _bytes(root / approval_rel(frid)) == approval_bytes)
        bundle_rows = tomllib.loads((_bytes(root / inventory_rel(frid)) or b"").decode("utf-8")).get("file", [])
        check("driver-apply-bundle-verifies", verify_bundle(root, frid).status == VALID
              and sorted(r["path"] for r in bundle_rows)
              == sorted([plan_rel(frid), approval_rel(frid), archive_rel(frid, "legacy.md")]))
        # one approval, one apply: the second apply refuses on that rule itself, ahead of the freshness
        # refusal its own first apply would otherwise trigger, with the evidence unchanged.
        applied = _snapshot(root)
        del seen[:]
        with landed(composing):
            again = refusal(run_apply, root, plan_bytes, approval_bytes, sheet)
        check("driver-apply-one-approval-one-apply", "already has its transaction" in (again or "")
              and not seen and _snapshot(root) == applied)
        # and approve refuses an applied run on the same rule, ahead of freshness.
        check("driver-approve-after-apply-refused", "already has its transaction" in
              (refusal(capture_approval, root, plan_bytes, sheet, "adopter", now) or "")
              and _snapshot(root) == applied)

    # 12: the finish ops (slice 5), see _finish_ops_self_test.
    _finish_ops_self_test(check)

    # 13: init-store over the coupled-init substrate (slice 3; real git fixtures, see _init_store_self_test).
    _init_store_self_test(check)

    # 14 (PR3 slice 2): the enable-hook op through the EXECUTABLE shell, over a first-adoption fixture whose
    # one operand is an adopter registration of the v1 settings.json family (no store resolves). The row's
    # new_digest is the merge core's own output over the planned bytes, so apply is held to publishing
    # exactly the post-merge registration the approval bound. Each refusal vector names its rule's keyword
    # and leaves the tree byte-identical with no transaction opened.
    reg_rel, entry = ".claude/settings.json", "opf-governance"
    reg_old = hook._emit(hook.canonical_registration())
    reg_new = hook.merge_registration(reg_old, entry).new_bytes

    def hook_fixture(temp, payload=reg_old):
        root = Path(temp).resolve()
        if payload is not None:
            (root / reg_rel).parent.mkdir(parents=True)
            (root / reg_rel).write_bytes(payload)
        return root

    def hook_row(old=reg_old, new=reg_new, path=reg_rel):
        return dict(op="enable-hook", registration_path=path, plugin_entry=entry,
                    old_digest=plan_digest(old), new_digest=plan_digest(new))

    def compose_hook(rows, handler=dispatch):
        def compose(ops):
            context = HookContext(ops)
            for i, row in enumerate(rows):
                verdict = handler(row, context)
                if verdict.status != VALID:
                    raise AdoptApplyError("plan op[{}] ({!r}) refused: {}".format(
                        i, row.get("op"), "; ".join(verdict.findings)))
        return compose

    # the round trip, under a spawn denial (threat 6: the op writes configuration only and never runs,
    # loads or activates the hook it registers): the live registration is archived byte-exact first, then
    # carries exactly the approved postimage, the adopter's own entries preserved through the model merge.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp)
        # an owner-only registration (an adopter overlay can carry secrets): the write must keep
        # the live file's mode AND the archive copy must carry that same mode, never the default
        # create mode (the archive home stays tracked, spec 4.2, so a widened copy would publish).
        (root / reg_rel).chmod(0o600)
        first_adoption = store.resolve_store(root).status == store.NOT_ADOPTED
        spawned = AssertionError("enable-hook spawned a process")
        with mock.patch.object(subprocess, "Popen", side_effect=spawned), \
                mock.patch.object(os, "fork", side_effect=spawned), \
                mock.patch.object(os, "system", side_effect=spawned):
            txn, why = attempt(run_adopt_transaction, root, rid, compose_hook([hook_row()]))
        after = snapshot(root)
        landed = json.loads(after.get(reg_rel, b"{}").decode("utf-8"))
        prior = json.loads(reg_old.decode("utf-8"))
        check("hook-merge-committed",
              first_adoption and txn == rid and why is None
              and txn_state(root, rid) == "complete" and lock_free(root))
        check("hook-registration-is-approved-postimage",
              reg_new != reg_old and after.get(reg_rel) == reg_new
              and plan_digest(after.get(reg_rel, b"")) == hook_row()["new_digest"])
        check("hook-adopter-entries-preserved",
              after.get(reg_rel) == reg_new    # non-vacuous: preservation is asserted ON the landed write
              and landed.get("permissions") == prior["permissions"]
              and landed.get("hooks", {}).get("PostToolUse") == prior["hooks"]["PostToolUse"])
        check("hook-preimage-archived-byte-exact", after.get(archive_rel(rid, reg_rel)) == reg_old)
        copy_path = root / archive_rel(rid, reg_rel)
        check("hook-archive-copy-keeps-source-mode",
              copy_path.is_file() and stat.S_IMODE(copy_path.lstat().st_mode) == 0o600
              and stat.S_IMODE((root / reg_rel).lstat().st_mode) == 0o600)
        check("hook-bundle-verifies", verify_bundle(root, rid).status == VALID)
        late = refusal(run_adopt_transaction, root, rid, compose_hook([hook_row(reg_new, reg_new)]),
                       phase="completion")
        check("hook-outside-apply-stage-refused", late is not None and "apply stage" in late
              and (root / reg_rel).read_bytes() == reg_new)

    # a widened source never yields a widened copy: the archive copy takes the source's mode under
    # ARCHIVE_MODE_MASK, so a group- and world-writable registration archives at 0o644 while the live
    # file keeps its own mode (reverting the mask turns this red).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp)
        (root / reg_rel).chmod(0o666)
        txn, why = attempt(run_adopt_transaction, root, rid, compose_hook([hook_row()]))
        copy_path = root / archive_rel(rid, reg_rel)
        check("hook-archive-copy-never-widens",
              txn == rid and why is None and copy_path.is_file()
              and stat.S_IMODE(copy_path.lstat().st_mode) == 0o644
              and stat.S_IMODE((root / reg_rel).lstat().st_mode) == 0o666
              and (root / reg_rel).read_bytes() == reg_new)

    # the verified no-op: a registration already carrying the canonical entry binds new_digest equal to
    # old_digest, and the op composes nothing, neither an archive copy nor a write.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp, reg_new)
        txn, why = attempt(run_adopt_transaction, root, rid, compose_hook([hook_row(reg_new, reg_new)]))
        after = snapshot(root)
        check("hook-already-merged-is-verified-noop",
              txn == rid and why is None and after.get(reg_rel) == reg_new
              and archive_rel(rid, reg_rel) not in after and txn_state(root, rid) == "complete")

    stale = hook.merge_registration(reg_old, "opf-other").new_bytes
    drifted = hook._emit(dict(hook.canonical_registration(), model="drifted"))
    foreign = hook._emit(dict(hook.canonical_registration(), unknownKey="x"))
    # why unrecognized-shape is redundantly guarded: a refused merge carries no postimage digest, so
    # no row's new_digest can ever match it and the handler's new_digest check also refuses.
    foreign_merge = hook.merge_registration(foreign, entry)
    check("hook-unrecognized-shape-has-no-postimage",
          foreign_merge.status != VALID and foreign_merge.new_digest is None
          and foreign_merge.new_bytes is None)
    empty = b"{}"
    empty_new = hook.merge_registration(empty, entry).new_bytes
    # A candidate that is NOT one shell word: the pure merge core accepts any token (its disclosed
    # trust boundary), so this row's digests are built from the core's own output over it, and
    # ONLY the plan-row pack-member grammar (_opf_adopt._is_hook_entry) refuses the row: through
    # dispatch (validate_op), and again inside the handler, which a direct OP_HANDLERS caller
    # reaches without dispatch. Handed straight to the handler with its re-proof removed, the
    # merge agrees with the row's digests and the write LANDS, so BOTH checks of that vector go
    # red: a behavioural flip, not a diagnostic one.
    multiword = "pkg-tool a.cfg;b"
    multiword_new = hook.merge_registration(reg_old, multiword).new_bytes

    def multiword_row():
        return dict(op="enable-hook", registration_path=reg_rel, plugin_entry=multiword,
                    old_digest=plan_digest(reg_old), new_digest=plan_digest(multiword_new))

    # Guard accounting (QA rounds 1 and 2): each vector names the rule that refuses it and what
    # removing that rule ALONE actually does, as observed under single-guard mutants of this
    # source, so no mutation claim is stronger than the source. SINGLY guarded (removing the one
    # guard admits the write or the drift, so both of the vector's checks go red): stale-new-digest
    # (new_digest), already-merged-drift (old_digest: the merge no-ops and the drifted transaction
    # commits), the three allowlist vectors, and multiword-entry-direct-handler (the handler's
    # grammar re-proof). REDUNDANTLY guarded (removing the named check alone re-attributes the
    # refusal, so only its -refused check goes red, and no write is admitted because the next rule
    # in source order still refuses): old-digest-drift (then new_digest, since the drifted file
    # merges to another postimage, then preserve_masked()'s digest re-check); unrecognized-shape (then
    # new_digest: a refused merge has no postimage digest, pinned above); protected-registration-
    # path (then the allowlist; with both removed, check_apply_ops refuses the archive mkdir under
    # .aiqt); multiword-plugin-entry (dispatch's grammar, then the handler's re-proof); and
    # two-rows-one-registration (then ApplyOps.create refuses the second archive copy's path).
    # registration-absent is neither: removing its guard admits no write, but the next statement
    # hashes the absent bytes and raises TypeError, which escapes the named refusal (this self-test
    # then fails closed with exit 2), so that guard turns an uncaught crash into an attributed refusal.
    hook_flips = (
        # THE new_digest flip: live bytes and old_digest agree, but the plan's new_digest is stale (another
        # merge's postimage), so the merged bytes are not the registration the approval bound
        ("stale-new-digest", [hook_row(new=stale)], reg_old, "new_digest", None),
        # THE old_digest flip: the live registration drifted after planning (still a mergeable file)
        ("old-digest-drift", [hook_row()], drifted, "drifted", None),
        # the one behaviour the handler's own old_digest check adds beyond preserve_masked(): a live file
        # ALREADY carrying the merged bytes under a row whose old differs is drift, but without the
        # check the merge no-ops (live == new), composes nothing, and the drifted transaction is
        # ADMITTED as a silent no-op; removing the check flips BOTH checks of this vector red.
        ("already-merged-drift", [hook_row()], reg_new, "drifted", None),
        ("registration-absent", [hook_row()], None, "absent", None),
        ("unrecognized-shape", [hook_row(foreign, foreign)], foreign, "registration merge", None),
        # the protected operand EXISTS (planted below), so the refusal is the path rule, never the
        # absent-file refusal a missing operand would reach first.
        ("protected-registration-path", [hook_row(path=".aiqt/settings.json")], reg_old,
         "protected destination", {".aiqt/settings.json": reg_old}),
        # the closed v1 registration-path allowlist (QA round 1 blocker): another platform's
        # settings file, an unrelated JSON file, and the adopter's personal settings.local.json
        # each carry an OTHERWISE-VALID row (correct digests over mergeable live content), so only
        # the path identity refuses; with the allowlist removed each write LANDS (both checks red).
        ("unsupported-platform-path", [hook_row(path=".gemini/settings.json")], None,
         "supported v1 registration", {".gemini/settings.json": reg_old}),
        ("non-settings-json-path",
         [dict(op="enable-hook", registration_path="config/application.json", plugin_entry=entry,
               old_digest=plan_digest(empty), new_digest=plan_digest(empty_new))], None,
         "supported v1 registration", {"config/application.json": empty}),
        ("local-settings-path", [hook_row(path=".claude/settings.local.json")], None,
         "supported v1 registration", {".claude/settings.local.json": reg_old}),
        ("multiword-plugin-entry", [multiword_row()], reg_old, "field 'plugin_entry'", None),
        ("two-rows-one-registration", [hook_row(), hook_row()], reg_old, "two plan rows", None),
    )
    for label, rows, payload, keyword, extra in hook_flips:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = hook_fixture(temp, payload)
            for rel, data in (extra or dict()).items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
            before = snapshot(root)
            why = refusal(run_adopt_transaction, root, rid, compose_hook(rows))
            check("hook-{}-refused".format(label), why is not None and keyword in why)
            # snapshot() skips .aiqt/, so a planted operand there is re-read directly.
            check("hook-{}-writes-nothing".format(label),
                  snapshot(root) == before and lock_free(root)
                  and not (root / JOURNAL_REL / rid).exists()
                  and all((root / rel).read_bytes() == data
                          for rel, data in (extra or dict()).items()))

    # the grammar re-proof IN the handler: the multiword row handed straight to OP_HANDLERS, skipping
    # dispatch, refuses with the tree unchanged and no transaction opened.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp)
        before = snapshot(root)
        why = refusal(run_adopt_transaction, root, rid,
                      compose_hook([multiword_row()], handler=OP_HANDLERS["enable-hook"]))
        check("hook-multiword-entry-direct-handler-refused",
              why is not None and "pack-member grammar" in why)
        check("hook-multiword-entry-direct-handler-writes-nothing",
              snapshot(root) == before and lock_free(root) and not (root / JOURNAL_REL / rid).exists())

    # the row-shape re-proof IN the handler: a malformed row handed straight to OP_HANDLERS (a field
    # missing, or a path that is not a string) refuses attributed, never an uncaught KeyError or
    # AttributeError, with the tree unchanged and no transaction opened. Removing the shape guard
    # admits no write, but the uncaught exception then escapes to the harness, which fails closed
    # to exit 2 in place of the attributed refusal (as for the absent-registration guard).
    def hook_row_without(field):
        row = hook_row()
        del row[field]
        return row

    malformed = [("no-plugin-entry", hook_row_without("plugin_entry"), "string registration_path"),
                 ("non-string-path", dict(hook_row(), registration_path=None), "string registration_path"),
                 ("no-new-digest", hook_row_without("new_digest"), "string registration_path")]
    for label, row, keyword in malformed:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = hook_fixture(temp)
            before = snapshot(root)
            why = refusal(run_adopt_transaction, root, rid,
                          compose_hook([row], handler=OP_HANDLERS["enable-hook"]))
            check("hook-malformed-row-{}-direct-handler-refused".format(label),
                  why is not None and keyword in why)
            check("hook-malformed-row-{}-direct-handler-writes-nothing".format(label),
                  snapshot(root) == before and lock_free(root) and not (root / JOURNAL_REL / rid).exists())

    # the reversal: an injected failure at the final op (the inventory publication, AFTER the registration
    # write landed) rolls the transaction back, restoring the prior registration byte-exact.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp)
        before = snapshot(root)
        seen = []
        real_staged_verify = _journal._verify_staged_digest

        def hook_failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                seen.append((root / reg_rel).read_bytes())
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_staged_verify(op, payload)

        with mock.patch.object(_journal, "_verify_staged_digest", hook_failing_inventory):
            aborted = refusal(run_adopt_transaction, root, rid, compose_hook([hook_row()]))
        check("hook-abort-rolls-back-after-write",
              seen == [reg_new] and aborted is not None and "rolled back" in aborted
              and txn_state(root, rid) == "rolled-back" and lock_free(root))
        check("hook-abort-restores-prior-registration",
              snapshot(root) == before and (root / reg_rel).read_bytes() == reg_old)

    # and the EXPLICIT recovery: an interruption after the registration write leaves an open transaction,
    # which reconcile() rolls back from the journal alone to the prior registration bytes.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp)
        before = snapshot(root)
        written = interrupt_when(lambda: (root / reg_rel).read_bytes() == reg_new)
        with mock.patch.object(_journal, "_kill_point", written):
            try:
                run_adopt_transaction(root, rid, compose_hook([hook_row()]))
                interrupted = False
            except (_Interrupt, AdoptApplyError) as exc:
                interrupted = isinstance(exc, _Interrupt)
        check("hook-interrupt-after-write-leaves-open-transaction",
              interrupted and txn_state(root, rid) == "open" and (root / reg_rel).read_bytes() == reg_new)
        check("hook-reconcile-restores-prior-registration",
              (rid, "rolled-back") in reconcile_without_store(root) and snapshot(root) == before
              and lock_free(root))

    # the phase guard proven on a WRITING row (QA round 1, claude minor 5): the base transaction of
    # this run composes nothing, so the completion-phase row WOULD write (live bytes match old, the
    # merged bytes match new, the archive copy is absent). With the apply-stage guard the refusal
    # names the stage and the tree keeps the prior bytes; with the guard removed the write lands in
    # the later phase, so the vector is behavioural, not only a keyword re-attribution.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = hook_fixture(temp)
        base_txn, base_why = attempt(run_adopt_transaction, root, rid, lambda ops: None)
        late_writing = refusal(run_adopt_transaction, root, rid, compose_hook([hook_row()]),
                               phase="completion")
        check("hook-writing-row-outside-apply-stage-refused",
              base_txn == rid and base_why is None
              and late_writing is not None and "apply stage" in late_writing
              and (root / reg_rel).read_bytes() == reg_old
              and not (root / archive_rel(rid, reg_rel)).exists())


    # 15 (slice 4): the registration ops, over throwaway fixtures through the EXECUTABLE shell. A valid
    # manifest makes the store resolve, which the posture gate refuses (no lease join yet), so the
    # unpatched shell refuses a registration first; every later vector runs behind `lease_join`, a stand-in
    # for the not-yet-landed join that swallows ONLY the real gate's refusal of a store resolving inside the
    # product root and hands every other posture to the real gate. Each refusal vector names its rule's
    # keyword and leaves the tree byte-identical (`.aiqt/` aside, the journal's home). A vector is OTHERWISE
    # VALID where the rule allows it (planted files, planner-bound digests, a planned model), so removing
    # its guard turns the refusal into a commit, never into another guard's refusal; the redundant guards
    # are named where they stand.
    import _opf_init
    import _opf_adopt_plan
    reg_store = schema.canonical_plan()["store"]
    man = schema.store_manifest(reg_store)
    keep, keep2, consumer, notes = "adopter/KEEP.md", "adopter/KEEP2.md", "ci/consumer.toml", "ci/notes.md"
    old_consumer = dict(paths=dict(rules="legacy/RULES.md"), name="ci")
    new_consumer = dict(paths=dict(rules=store.moved_dest("legacy/RULES.md")), name="ci")

    planted = {}

    def reg_fixture(temp, manifest_text=None, extra=None):
        root = Path(temp).resolve()
        files = {keep: b"kept\n", keep2: b"kept too\n", notes: b"Rules: see legacy/RULES.md\n",
                 consumer: emit_checked(old_consumer).encode("utf-8")}
        if manifest_text is not False:
            files[man] = (manifest_text or _opf_init.build_manifest()).encode("utf-8")
        files.update(extra or {})
        for rel, payload in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(payload)
        planted.clear()
        planted.update(files)
        return root, files

    def bound(raw, entries, init=None):
        """The planner's OWN registration binding (_opf_adopt_plan._bind_registrations) over the manifest
        bytes `raw` (or, given an init-store row `init`, over the manifest it scaffolds), so apply is held
        to reproducing exactly the postimages the plan binds."""
        rows = [dict(op="register-unmanaged", entry=e) for e in entries]
        if init is not None:
            _opf_adopt_plan._bind_registrations([init] + rows, dict(resolution=dict(status=store.NOT_ADOPTED)),
                                                None)
        else:
            _opf_adopt_plan._bind_registrations(
                rows, dict(resolution=dict(status=store.RESOLVED, manifest_digest=plan_digest(raw))),
                tomllib.loads(raw.decode("utf-8")))
        return rows

    def chain(files, entries):
        return bound(files[man], entries)

    def unbound(raw, entry):
        """One link whose new_digest is the planner's rendering over `raw` WITHOUT the planner's validity
        check (which refuses to bind over an invalid manifest), so only apply's own rule can refuse it."""
        model = tomllib.loads(raw.decode("utf-8"))
        model.setdefault("unmanaged", {}).setdefault("paths", []).append(entry)
        return [dict(op="register-unmanaged", entry=entry, old_digest=plan_digest(raw),
                     new_digest=plan_digest(emit_checked(model).encode("utf-8")))]

    def repoint_row(files, planned=None, path=None):
        path = path or consumer
        body = emit_checked(planned or new_consumer).encode("utf-8")
        return dict(op="repoint-consumer", path=path, old_digest=plan_digest(files.get(path, b"")),
                    new_digest=plan_digest(body))

    def compose_reg(rows, consumers=None, store_table=reg_store, before_row=None):
        # the kept files' approved source digests: the fixture's planted bytes, as the plan's keep sources
        # bind them (the stage driver's), so a kept file drifts only where a vector makes it
        def compose(ops):
            context = RegistrationContext(ops, store_table, consumers,
                                          dict((p, plan_digest(b)) for p, b in planted.items()))
            for i, row in enumerate(rows):
                if before_row is not None:
                    before_row(i, ops, context)
                verdict = dispatch(row, context)
                if verdict.status != VALID:
                    raise AdoptApplyError("plan op[{}] ({!r}) refused: {}".format(
                        i, row.get("op"), "; ".join(verdict.findings)))
        return compose

    real_gate = _store_posture_or_refuse

    def leased_gate(product_root):
        res = store.resolve_store(product_root)
        if (res.status == store.RESOLVED and res.store_root is not None
                and Path(os.path.abspath(res.store_root)) == Path(os.path.abspath(product_root))):
            return None
        return real_gate(product_root)

    def lease_join():
        return mock.patch.dict(globals(), dict(_store_posture_or_refuse=leased_gate))

    def reg_refusal(temp, rows, keyword, label, consumers=None, store_table=reg_store, manifest_text=None,
                    extra=None, before_row=None):
        root, files = reg_fixture(temp, manifest_text, extra)
        before = snapshot(root)
        with lease_join():
            why = refusal(run_adopt_transaction, root, rid, compose_reg(
                rows(files) if callable(rows) else rows, consumers, store_table, before_row))
        check("reg-{}-refused".format(label), why is not None and keyword in why)
        check("reg-{}-writes-nothing".format(label),
              snapshot(root) == before and lock_free(root) and not (root / JOURNAL_REL / rid).exists())

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp)
        before = snapshot(root)
        leased = refusal(run_adopt_transaction, root, rid, compose_reg(chain(files, [keep])))
        check("reg-resolved-store-refused-without-lease-join",
              store.resolve_store(root).status == store.RESOLVED and leased is not None and "lease" in leased
              and snapshot(root) == before)
    # the stand-in is no blanket bypass: every posture but the in-root resolved store still meets the real
    # gate (here a malformed pointer beside an otherwise registrable store).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp, extra={".opf.toml": b"not toml ["})
        before = snapshot(root)
        with lease_join():
            gated = refusal(run_adopt_transaction, root, rid, compose_reg(chain(files, [keep])))
        check("reg-lease-stand-in-keeps-posture-gate",
              gated is not None and "cannot be evaluated" in gated and snapshot(root) == before)

    # the round trip: a two-link registration chain and one consumer repointing in ONE transaction. Each
    # rewritten file is archived byte-exact first and then carries exactly the bound postimage; the kept
    # files themselves stay untouched; the manifest's reparse is the prior model plus exactly the two
    # entries, and it still validates.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp, extra={"adopter/LATE.md": b"late\n"})
        rows = chain(files, [keep, keep2])
        with lease_join():
            txn, why = attempt(run_adopt_transaction, root, rid, compose_reg(
                rows + [repoint_row(files)], dict([(consumer, new_consumer)])))
        after = snapshot(root)
        prior = tomllib.loads(files[man].decode("utf-8"))
        prior["unmanaged"]["paths"] = prior["unmanaged"]["paths"] + [keep, keep2]
        landed = tomllib.loads(after.get(man, b"").decode("utf-8"))
        check("reg-chain-and-repoint-committed",
              txn == rid and why is None and txn_state(root, rid) == "complete" and lock_free(root))
        check("reg-manifest-is-planner-postimage", plan_digest(after.get(man, b"")) == rows[-1]["new_digest"])
        check("reg-manifest-prior-plus-exactly-the-entries",
              _model_equal(landed, prior) and store.validate_manifest(landed).status == VALID)
        check("reg-kept-files-untouched", after.get(keep) == files[keep] and after.get(keep2) == files[keep2])
        check("repoint-consumer-is-reemitted-postimage",
              after.get(consumer) == emit_checked(new_consumer).encode("utf-8"))
        check("reg-preimages-archived-byte-exact",
              after.get(archive_rel(rid, man)) == files[man]
              and after.get(archive_rel(rid, consumer)) == files[consumer])
        check("reg-bundle-verifies", verify_bundle(root, rid).status == VALID)
        # every file apply created is a bound effect (spec 14.1): the archive copies are exactly the
        # creations the plan's own derivation lists for these rows (_opf_adopt.rewrite_preservations).
        effects = schema.derive_effects(rows + [repoint_row(files)], [], man, rid)
        archived = dict((p, plan_digest(b)) for p, b in after.items() if _within(p, _archive_root(rid)))
        check("reg-archive-copies-are-bound-effects", len(archived) == 2 and archived == dict(
            (row["path"], row["digest"]) for row in effects["creations"]))

    # the stage rule: a committed base transaction that archived no manifest (one consumer repointing), then
    # an otherwise-valid link (a planted kept file, the planner-bound digests over the live manifest) in a
    # later phase. Without the rule it commits, rewriting the manifest; with it the tree stays unchanged.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp, extra={"adopter/LATE.md": b"late\n"})
        with lease_join():
            base_txn, base_why = attempt(run_adopt_transaction, root, rid, compose_reg(
                [repoint_row(files)], dict([(consumer, new_consumer)])))
        before = snapshot(root)
        with lease_join():
            late = refusal(run_adopt_transaction, root, rid, compose_reg(
                chain(files, ["adopter/LATE.md"])), phase="completion")
        check("reg-outside-apply-stage-refused", base_txn == rid and base_why is None and late is not None
              and "apply stage" in late)
        check("reg-outside-apply-stage-writes-nothing", snapshot(root) == before
              and not (root / JOURNAL_REL / (rid + ".completion")).exists())

    # the first-adoption chain: a manifest CREATED earlier in this transaction (the init-store scaffold the
    # planner's binding starts from when no store resolves) is extended by the bound links, the chain folded
    # into that one create, under the REAL posture gate (no store resolves yet).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp, manifest_text=False)
        scaffold = _opf_init.build_manifest().encode("utf-8")
        init = dict(op="init-store", store_root=".", members=[dict(path=man, digest=plan_digest(scaffold))])
        rows = bound(None, [keep, keep2], init)
        txn, why = attempt(run_adopt_transaction, root, rid, compose_reg(
            rows, before_row=lambda i, ops, context: ops.create(man, scaffold) if i == 0 else None))
        check("reg-chain-extends-manifest-created-in-transaction",
              txn == rid and why is None and txn_state(root, rid) == "complete"
              and plan_digest((root / man).read_bytes()) == rows[-1]["new_digest"]
              and (root / keep).read_bytes() == files[keep] and not (root / archive_rel(rid, man)).exists())
        check("reg-created-manifest-chain-binds-no-archive-copy", not any(
            _within(row["path"], _archive_root(rid))
            for row in schema.derive_effects([init] + rows, [], man, rid)["creations"]))

    commented = "# a hand comment\n" + _opf_init.build_manifest()
    this_archive = archive_rel(rid, "x.md")
    control = [this_archive, ".working/archive/x.md", ".working/staging/x.md", ".working/journals/x.md",
               ".working/imported/x.md", ".working/imports/x.md"]
    protected = [".working/.gitignore", ".git/config", ".aiqt/x.md"]
    tracked = dict([(consumer, new_consumer)])
    planted_consumer = emit_checked(old_consumer).encode("utf-8")
    kept_consumer = emit_checked(dict(tomllib.loads(_opf_init.build_manifest()),
                                      unmanaged=dict(paths=[consumer])))

    def swap_model(i, ops, context):
        if i == 1:
            context.consumers[consumer] = dict(new_consumer, name="ci2")

    def with_manifest(**tables):
        return emit_checked(dict(tomllib.loads(_opf_init.build_manifest()), **tables))

    def aliased_view(target, alias):
        views = tomllib.loads(_opf_init.build_manifest())["views"]
        return with_manifest(views=dict((k, dict(v, target=alias) if v["target"] == target else v)
                                        for k, v in views.items()))

    def drift_kept(i, ops, context):
        context.kept[keep] = plan_digest(b"approved, then edited\n")

    def unbind_kept(i, ops, context):
        context.kept.pop(keep, None)

    hooks = {"consumer-twice": swap_model, "entry-kept-drifted": drift_kept, "entry-kept-unbound": unbind_kept}
    decisions_toml = ".working/DECISIONS.toml"

    flips = [
        # the byte-reproduction precondition: a comment makes the manifest non-canonical; the row carries
        # the planner-bound postimage of the parsed model, so only the canonicality rule refuses
        ("non-canonical-manifest", lambda f: chain(f, [keep]), "canonical", None, commented, None),
        # the postimage flip: the emitted manifest does not hash to the row's new_digest
        ("manifest-postimage-off-new-digest",
         lambda f: [dict(chain(f, [keep])[0], new_digest=_D_ZERO)], "new_digest", None, None, None),
        # drift: the live manifest no longer matches the row's old_digest
        ("manifest-drift", lambda f: [dict(chain(f, [keep])[0], old_digest=_D_ZERO)], "drifted", None, None,
         None),
        # a broken chain: the second link names the manifest the FIRST link rewrote, not its postimage
        ("chain-link-broken", lambda f: [chain(f, [keep])[0], dict(chain(f, [keep2])[0])], "drifted", None,
         None, None),
        ("entry-in-machine-store", lambda f: chain(f, [".working/toml/x.md"]), "machine store", None, None,
         {".working/toml/x.md": b"x\n"}),
        ("entry-is-view-target", lambda f: chain(f, [".working/TODO.md"]), "declared view", None, None,
         {".working/TODO.md": b"x\n"}),
        # the contains direction: an entry containing a reserved root is a directory, which the kept-file
        # re-observation refuses too (a redundant pair, disclosed)
        ("entry-contains-store", lambda f: chain(f, [".working"]), "control area", None, None, None),
        ("entry-already-registered", lambda f: chain(f, [keep, keep]), "already registered", None, None, None),
        ("entry-written-by-transaction", lambda f: [repoint_row(f)] + chain(f, [consumer]),
         "written by this transaction", tracked, None, None),
        # a case alias of the consumer an earlier row repoints (one file on a case-insensitive filesystem)
        ("entry-written-by-transaction-case", lambda f: [repoint_row(f)] + chain(f, ["CI/consumer.toml"]),
         "written by this transaction", tracked, None, {"CI/consumer.toml": planted_consumer}),
        # an absent entry binds no approved digest, so the unbound-digest check refuses it too (a redundant
        # pair, disclosed at _compose_register_unmanaged)
        ("entry-not-a-file", lambda f: chain(f, ["adopter/NEVER.md"]), "kept file", None, None, None),
        # an invalid prior manifest stays invalid with one more entry, so the postimage validity check
        # refuses it too (a redundant pair, disclosed); the vector below pins the verdict over a crash
        ("invalid-manifest", lambda f: unbound(f[man], keep), "not a valid manifest", None,
         emit_checked(dict(tomllib.loads(_opf_init.build_manifest()), stray=dict(key=1))), None),
        # with no store identity there is no manifest path to read, which the contained-path read refuses
        # too (a redundant pair, disclosed)
        ("no-store-identity", lambda f: chain(f, [keep]), "frozen store identity", None, None, None),
        # repoint-consumer: drift, the postimage flip, a non-canonical or non-TOML consumer, a missing or
        # unchanged planned model, a reserved store path or a kept file as a consumer, and a consumer twice
        ("consumer-drift", lambda f: [dict(repoint_row(f), old_digest=plan_digest(f[consumer] + b"x"))],
         "drifted", tracked, None, None),
        ("consumer-postimage-off-new-digest", lambda f: [dict(repoint_row(f), new_digest=_D_ZERO)],
         "new_digest", tracked, None, None),
        ("consumer-not-toml", lambda f: [repoint_row(f, path=notes)], "does not parse as TOML",
         dict([(notes, new_consumer)]), None, None),
        # a non-table model also fails emit_checked (a redundant pair, disclosed)
        ("consumer-no-planned-model", lambda f: [repoint_row(f)], "no planned postimage", None, None, None),
        ("consumer-repoints-nothing", lambda f: [repoint_row(f, planned=old_consumer)], "repoints nothing",
         dict([(consumer, old_consumer)]), None, None),
        ("consumer-is-store-manifest", lambda f: [repoint_row(f, path=man)], "machine store",
         dict([(man, new_consumer)]), None, None),
        ("consumer-in-machine-store", lambda f: [repoint_row(f, path=".working/toml/counters.toml")],
         "machine store", dict([(".working/toml/counters.toml", new_consumer)]), None,
         {".working/toml/counters.toml": planted_consumer}),
        ("consumer-is-view-target", lambda f: [repoint_row(f, path=".working/DECISIONS.toml")],
         "declared view", dict([(".working/DECISIONS.toml", new_consumer)]), None,
         {".working/DECISIONS.toml": planted_consumer}),
        # this run's own archive is the one control-area path protected_destination exempts; a write there
        # also meets check_apply_ops's archive immutability (a redundant pair, disclosed)
        ("consumer-in-control-area", lambda f: [repoint_row(f, path=this_archive)], "control area",
         dict([(this_archive, new_consumer)]), None, {this_archive: planted_consumer}),
        ("consumer-kept-unmanaged", lambda f: [repoint_row(f)], "[unmanaged] entry", tracked, kept_consumer,
         None),
        ("consumer-registered-in-transaction", lambda f: chain(f, [consumer]) + [repoint_row(f)],
         "[unmanaged] entry", tracked, None, None),
        # a second row chained on the first row's postimage, its planned model swapped between the rows
        # (swap_model), so only the one-row-per-consumer rule refuses it
        ("consumer-twice", lambda f: [repoint_row(f), dict(
            repoint_row(f, planned=dict(new_consumer, name="ci2")), old_digest=repoint_row(f)["new_digest"])],
         "two plan rows", dict(tracked), None, None),
        # the same consumer under a case alias (one file on a case-insensitive filesystem)
        ("consumer-twice-case", lambda f: [repoint_row(f), repoint_row(f, path="CI/consumer.toml")],
         "two plan rows", dict(tracked, **{"CI/consumer.toml": new_consumer}), None,
         {"CI/consumer.toml": planted_consumer}),
        # repoint-consumer over an invalid store manifest (a stray table) that the posture gate admits
        ("consumer-store-manifest-invalid", lambda f: [repoint_row(f)], "not a valid manifest", tracked,
         with_manifest(stray=dict(key=1)), None),
        # the kept file's live bytes against its approved source digest: drifted, or bound by none
        ("entry-kept-drifted", lambda f: chain(f, [keep]), "approved source digest", None, None, None),
        # unbound: the drift comparison refuses it too (a redundant pair, disclosed), so this vector pins
        # the named cause, not a commit
        ("entry-kept-unbound", lambda f: chain(f, [keep]), "no approved source digest", None, None, None),
        # an entry overlapping one already registered, in the contains direction and under an alias
        ("entry-within-registered", lambda f: chain(f, [keep]), "already registered", None,
         with_manifest(unmanaged=dict(paths=["adopter"])), None),
        ("entry-registered-alias", lambda f: chain(f, [keep]), "already registered", None,
         with_manifest(unmanaged=dict(paths=["adopter/./KEEP.md"])), None),
        # a case-only spelling of the machine store (one directory on a case-insensitive filesystem)
        ("entry-in-machine-store-case", lambda f: chain(f, [".WORKING/toml/x.md"]), "machine store", None,
         None, {".WORKING/toml/x.md": b"x\n"}),
        # a declared view target spelled with a `.` component still reserves its path, for both ops
        ("entry-is-aliased-view-target", lambda f: chain(f, [decisions_toml]), "declared view", None,
         aliased_view(decisions_toml, ".working/./DECISIONS.toml"), {decisions_toml: b"x\n"}),
        ("consumer-is-aliased-view-target", lambda f: [repoint_row(f, path=decisions_toml)], "declared view",
         dict([(decisions_toml, new_consumer)]), aliased_view(decisions_toml, ".working//DECISIONS.toml"),
         {decisions_toml: planted_consumer}),
    ]
    # an [unmanaged] entry naming the consumer under each equivalent spelling the manifest validator admits
    flips += [("consumer-kept-unmanaged-alias-{}".format(i), lambda f: [repoint_row(f)], "[unmanaged] entry",
               tracked, with_manifest(unmanaged=dict(paths=[alias])), None)
              for i, alias in enumerate(("ci/./consumer.toml", "ci//consumer.toml", "ci/x/../consumer.toml",
                                         "./ci/consumer.toml", "CI/consumer.toml"))]
    flips += [("entry-in-control-{}".format(i), (lambda e: lambda f: chain(f, [e]))(e), "control area", None,
               None, {e: b"x\n"}) for i, e in enumerate(control)]
    flips += [("entry-protected-{}".format(i), (lambda e: lambda f: chain(f, [e]))(e), "protected destination",
               None, None, {e: b"x\n"}) for i, e in enumerate(protected)]
    # entry-in-control-1..5 also meet protected_destination, whose message names the control area too (a
    # redundant pair, disclosed at _reserved_store_path); entry-in-control-0, this run's own archive, is the
    # control-area check's alone.
    for label, rows, keyword, consumers, manifest_text, extra in flips:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            reg_refusal(temp, rows, keyword, label, consumers,
                        None if label == "no-store-identity" else reg_store, manifest_text, extra,
                        hooks.get(label))
    # the committed store pointer as an entry: a planted pointer naming the product root itself, so the store
    # still resolves in-root and only the protected-destination rule refuses the registration.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        pointer = '[store]\ntarget = "dir:{}"\n'.format(Path(temp).resolve()).encode("utf-8")
        check("reg-entry-store-pointer-resolves-in-root", store.resolve_store(
            reg_fixture(temp, extra={".opf.toml": pointer})[0]).status == store.RESOLVED)
        reg_refusal(temp, lambda f: chain(f, [".opf.toml"]), "protected destination", "entry-store-pointer",
                    extra={".opf.toml": pointer})
    # repoint-consumer without a usable store identity: none at all, or one whose manifest is absent.
    for label, table in (("consumer-no-store-identity", None),
                         ("consumer-store-manifest-absent", dict(reg_store, machine_rel=".working/other"))):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            reg_refusal(temp, lambda f: [repoint_row(f)],
                        "frozen store identity" if table is None else "absent", label, tracked, table)
    # a malformed manifest refuses as a verdict, never a raw exception (the manifest-validity precondition).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        bad = emit_checked(dict(tomllib.loads(_opf_init.build_manifest()), unmanaged=dict(paths="x")))
        root, files = reg_fixture(temp, bad)
        verdicts = []

        def compose_bad(ops):
            context = RegistrationContext(ops, reg_store)
            try:
                verdicts.append(dispatch(dict(op="register-unmanaged", entry=keep, old_digest=plan_digest(
                    files[man]), new_digest=_D_ZERO), context))
            except (AttributeError, TypeError) as exc:
                verdicts.append(exc)
            raise AdoptApplyError("vector done")

        with lease_join():
            refusal(run_adopt_transaction, root, rid, compose_bad)
        check("reg-malformed-manifest-is-a-verdict",
              len(verdicts) == 1 and isinstance(verdicts[0], schema.AdoptValidation)
              and verdicts[0].status == CANNOT
              and any("not a valid manifest" in f for f in verdicts[0].findings))

    # a non-canonical CONSUMER (a comment) refuses on the byte-reproduction precondition too.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp)
        noisy = b"# hand comment\n" + files[consumer]
        (root / consumer).write_bytes(noisy)
        files[consumer] = noisy
        before = snapshot(root)
        with lease_join():
            why = refusal(run_adopt_transaction, root, rid, compose_reg(
                [repoint_row(files)], dict([(consumer, new_consumer)])))
        check("reg-non-canonical-consumer-refused",
              why is not None and "canonical" in why and snapshot(root) == before)

    # the allowed-delta postcondition: an emitter that smuggles a second [unmanaged] path into the planned
    # manifest is refused even when the row's new_digest names the smuggled bytes, so the digest alone can
    # never stand in for the one-entry delta.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp)
        real_emit = emit_checked

        def smuggling_emit(model):
            paths = model.get("unmanaged", {}).get("paths", [])
            if keep in paths:
                model = tomllib.loads(real_emit(model))      # a private copy (`copy` is a local here)
                model["unmanaged"]["paths"].append("adopter/SMUGGLED.md")
            return real_emit(model)

        smuggled = tomllib.loads(files[man].decode("utf-8"))
        smuggled["unmanaged"]["paths"] += [keep, "adopter/SMUGGLED.md"]
        row = dict(op="register-unmanaged", entry=keep, old_digest=plan_digest(files[man]),
                   new_digest=plan_digest(real_emit(smuggled).encode("utf-8")))
        before = snapshot(root)
        with lease_join(), mock.patch.dict(globals(), dict(emit_checked=smuggling_emit)):
            why = refusal(run_adopt_transaction, root, rid, compose_reg([row]))
        check("reg-smuggled-delta-refused", why is not None and "exactly the one" in why
              and snapshot(root) == before)

    # reversal: a pre-commit abort rolls both rewrites back to their recorded old bytes, and an
    # interruption after the manifest write landed is rolled back by the explicit reconcile, byte-exact.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        with lease_join(), mock.patch.object(_journal, "_verify_staged_digest", failing_inventory):
            aborted = refusal(run_adopt_transaction, root, rid, compose_reg(
                chain(files, [keep]) + [repoint_row(files)], dict([(consumer, new_consumer)])))
        check("reg-abort-restores-recorded-old-bytes",
              aborted is not None and "rolled back" in aborted and snapshot(root) == before
              and txn_state(root, rid) == "rolled-back" and lock_free(root))
    # the journal's own staged-digest pin on the registration write itself: a refusal there, inside the
    # transaction after the archive copy landed, rolls the whole transaction back byte-exact.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_manifest_write(op, payload):
            if op["path"] == man and op["op"] == "write":
                raise _journal.JournalError("injected staged-digest refusal at the manifest write")
            return real_verify(op, payload)

        with lease_join(), mock.patch.object(_journal, "_verify_staged_digest", failing_manifest_write):
            pinned = refusal(run_adopt_transaction, root, rid, compose_reg(chain(files, [keep])))
        check("reg-in-transaction-write-refusal-rolls-back",
              pinned is not None and "rolled back" in pinned and snapshot(root) == before
              and txn_state(root, rid) == "rolled-back" and lock_free(root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root, files = reg_fixture(temp)
        before = snapshot(root)
        rows = chain(files, [keep])
        rewritten = interrupt_when(lambda: plan_digest((root / man).read_bytes()) == rows[0]["new_digest"])
        with lease_join(), mock.patch.object(_journal, "_kill_point", rewritten):
            try:
                run_adopt_transaction(root, rid, compose_reg(rows))
                cut = False
            except (_Interrupt, AdoptApplyError) as exc:
                cut = isinstance(exc, _Interrupt)
        check("reg-interrupt-leaves-open-transaction", cut and txn_state(root, rid) == "open"
              and plan_digest((root / man).read_bytes()) == rows[0]["new_digest"])
        check("reg-reconcile-restores-manifest",
              (rid, "rolled-back") in reconcile_without_store(root) and snapshot(root) == before
              and lock_free(root))


    # 16: the three file ops (slice 2), staged as spec 14.1 and 14.2 order them. The apply stage (the run's
    # base transaction) creates a planned file, takes a non-occupying retire source's preimage while it
    # stays frozen, archives an occupying retire or move source preserve-first, and leaves a non-occupying
    # move source frozen; the retirement stage (RETIREMENT_PHASE, after the green check) removes the
    # frozen retire source, relocates both move sources, and touches nothing for the occupying retire.
    # Each round trip below goes red if its op still refuses.
    fx_files = {"legacy/RULES.md": b"old rules\n", ".working/TODO.md": b"hand-kept todo\n",
                "legacy/MOVE.md": b"move me\n", "docs/VIEW.md": b"old view\n"}
    fx_new, fx_new_bytes = "adopter/NEW.md", b"planned\n"
    fx_moved = store.moved_dest("legacy/MOVE.md")
    fx_view_dest = "adopter/view-old.md"
    fx_sources = [dict(path="legacy/RULES.md", disposition="retire", occupying=False,
                       preservation=archive_rel(rid, "legacy/RULES.md")),
                  dict(path=".working/TODO.md", disposition="retire", occupying=True,
                       preservation=archive_rel(rid, ".working/TODO.md")),
                  dict(path="legacy/MOVE.md", disposition="move", occupying=False, preservation=fx_moved),
                  dict(path="docs/VIEW.md", disposition="move", occupying=True,
                       preservation=archive_rel(rid, "docs/VIEW.md"))]
    for row in fx_sources:
        row["digest"] = plan_digest(fx_files[row["path"]])
    fx_create = dict(op="create-file", path=fx_new, content_digest=plan_digest(fx_new_bytes))
    fx_disposed = [dict(op="retire-file", path="legacy/RULES.md",
                        preimage_digest=plan_digest(fx_files["legacy/RULES.md"])),
                   dict(op="retire-file", path=".working/TODO.md",
                        preimage_digest=plan_digest(fx_files[".working/TODO.md"])),
                   dict(op="move-file", source="legacy/MOVE.md", destination=fx_moved,
                        source_digest=plan_digest(fx_files["legacy/MOVE.md"])),
                   dict(op="move-file", source="docs/VIEW.md", destination=fx_view_dest,
                        source_digest=plan_digest(fx_files["docs/VIEW.md"]))]
    check("file-ops-fixture-rows-valid",
          all(schema.validate_op(r).status == VALID for r in [fx_create] + fx_disposed))

    def file_fixture(temp):
        root = Path(temp).resolve()
        for rel, payload in fx_files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(payload)
        return root

    def apply_stage(root, rows=None, sources=None, content=None):
        return attempt(run_adopt_transaction, root, rid, compose_rows(
            [fx_create] + fx_disposed if rows is None else rows, fx_sources if sources is None else sources,
            {fx_new: fx_new_bytes} if content is None else content))

    def retirement_stage(root, rows=None, sources=None):
        return attempt(run_adopt_transaction, root, rid, compose_rows(
            fx_disposed if rows is None else rows, fx_sources if sources is None else sources),
            phase=RETIREMENT_PHASE)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        txn, why = apply_stage(root)
        after = snapshot(root)
        check("file-ops-apply-stage-commits", txn == rid and why is None and txn_state(root, rid) == "complete"
              and lock_free(root))
        check("create-file-creates-planned-bytes", after.get(fx_new) == fx_new_bytes)
        check("retire-file-apply-preimage-taken-source-frozen",
              after.get(archive_rel(rid, "legacy/RULES.md")) == fx_files["legacy/RULES.md"]
              and after.get("legacy/RULES.md") == fx_files["legacy/RULES.md"])
        check("retire-file-apply-occupying-archived-then-removed",
              after.get(archive_rel(rid, ".working/TODO.md")) == fx_files[".working/TODO.md"]
              and ".working/TODO.md" not in after)
        check("move-file-apply-non-occupying-frozen-not-relocated",
              after.get("legacy/MOVE.md") == fx_files["legacy/MOVE.md"] and fx_moved not in after
              and archive_rel(rid, "legacy/MOVE.md") not in after)
        check("move-file-apply-occupying-archived-not-relocated",
              after.get(archive_rel(rid, "docs/VIEW.md")) == fx_files["docs/VIEW.md"]
              and "docs/VIEW.md" not in after and fx_view_dest not in after)
        check("file-ops-apply-bundle-verifies", verify_bundle(root, rid).status == VALID)
        staged_tree = tree_state(root)
        late_create = refusal(run_adopt_transaction, root, rid, compose_rows(
            [fx_create], (), {fx_new: fx_new_bytes}), phase=RETIREMENT_PHASE)
        check("create-file-retirement-stage-refused-untouched-save-journal-dirs",
              late_create is not None and "apply stage" in late_create
              and untouched_save_journal_dirs(staged_tree, root))
        rtxn, why = retirement_stage(root)
        done = snapshot(root)
        check("file-ops-retirement-stage-commits",
              rtxn == rid + "." + RETIREMENT_PHASE and why is None
              and txn_state(root, rtxn) == "complete" and lock_free(root))
        check("retire-file-retirement-removes-frozen-source-preimage-retained",
              "legacy/RULES.md" not in done
              and done.get(archive_rel(rid, "legacy/RULES.md")) == fx_files["legacy/RULES.md"])
        check("retire-file-retirement-occupying-touches-nothing",
              ".working/TODO.md" not in done
              and done.get(archive_rel(rid, ".working/TODO.md")) == fx_files[".working/TODO.md"])
        check("move-file-retirement-relocates-frozen-source",
              done.get(fx_moved) == fx_files["legacy/MOVE.md"] and "legacy/MOVE.md" not in done)
        check("move-file-retirement-copies-archive-and-retains-it",
              done.get(fx_view_dest) == fx_files["docs/VIEW.md"]
              and done.get(archive_rel(rid, "docs/VIEW.md")) == fx_files["docs/VIEW.md"])
        rinv = tomllib.loads(done.get(inventory_rel(rid, RETIREMENT_PHASE), b"file = []").decode("utf-8"))
        check("file-ops-retirement-inventory-claims-move-root",
              [r["path"] for r in rinv["file"]] == [fx_moved] and verify_bundle(root, rid).status == VALID)

    # 16b: the per-op preimage flips. One byte of one bound source changes between plan and apply: the
    # op refuses as drifted (cannot-evaluate), and the tree is untouched, no journal transaction opened.
    for label, source in (("retire-frozen", "legacy/RULES.md"), ("retire-occupying", ".working/TODO.md"),
                          ("move-frozen", "legacy/MOVE.md"), ("move-occupying", "docs/VIEW.md")):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = file_fixture(temp)
            flipped = bytearray(fx_files[source])
            flipped[0] ^= 0x01
            (root / source).write_bytes(bytes(flipped))
            before = tree_state(root)
            _txn, why = apply_stage(root)
            check("preimage-flip-{}-apply-refused-untouched-save-journal-dirs".format(label),
                  why is not None and "drifted" in why and untouched_save_journal_dirs(before, root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        for source in ("legacy/RULES.md", "legacy/MOVE.md"):
            flipped = bytearray(fx_files[source])
            flipped[-1] ^= 0x01
            (root / source).write_bytes(bytes(flipped))
            before = tree_state(root)
            _txn, why = retirement_stage(root)
            check("preimage-flip-{}-retirement-refused-untouched-save-journal-dirs".format(source),
                  why is not None and "drifted" in why and untouched_save_journal_dirs(before, root))
            (root / source).write_bytes(fx_files[source])
        # a frozen retire source whose MODE alone changed since apply (bytes intact) refuses its retirement:
        # its preserved copy keeps the apply-time mode, and the refusal names both modes in octal.
        rules = root / "legacy/RULES.md"
        rules_mode = stat.S_IMODE(rules.lstat().st_mode)
        rules.chmod(0o755)
        before = tree_state(root)
        _txn, why = retirement_stage(root)
        check("retirement-mode-only-change-refused-octal-untouched-save-journal-dirs",
              why is not None and "pinned mode 0755 differs from the mode {:04o}".format(rules_mode) in why
              and untouched_save_journal_dirs(before, root))
        rules.chmod(rules_mode)
        # the committed preservation re-verifies at retirement: a tampered archive copy refuses the whole
        # stage before anything is removed or relocated.
        copy_path = root / archive_rel(rid, "legacy/RULES.md")
        archived = copy_path.is_file()      # False when the apply stage itself refused: a recorded red
        if archived:
            copy_path.write_bytes(b"old rulez\n")
        before = tree_state(root)
        _txn, why = retirement_stage(root)
        check("retirement-tampered-preimage-refused-untouched-save-journal-dirs",
              archived and why is not None and "archive copy" in why
              and untouched_save_journal_dirs(before, root))
        if archived:
            copy_path.write_bytes(fx_files["legacy/RULES.md"])

    # 16c: preservation precedes removal. A retirement stage whose base never archived the source (the
    # base created only the planned file) refuses: nothing is retired that the committed base did not
    # preserve with the plan digest.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root, rows=[fx_create])
        before = tree_state(root)
        _txn, why = retirement_stage(root, rows=fx_disposed[:1])
        check("retirement-without-committed-preimage-refused-untouched-save-journal-dirs",
              why is not None and "committed base" in why and untouched_save_journal_dirs(before, root))

    # 16d: create-file's own preconditions and the plan bindings the handlers require, each refused with
    # the tree untouched: an occupied path, bytes that do not match content_digest, a path in the store
    # control area (this run's own archive, which check_apply_ops alone would admit), a source no plan row
    # binds or binds at another digest or twice, a move whose destination is already occupied, and a move
    # into this run's own archive (which protected_destination alone exempts).
    forged = archive_rel(rid, "legacy/FORGED.md")
    forged_row = dict(op="create-file", path=forged, content_digest=plan_digest(fx_new_bytes))
    forged_ops = [c(forged, fx_new_bytes)]
    check("create-file-control-area-admitted-by-shell-alone",
          findings_of(*sealed(forged_ops, {forged: fx_new_bytes})) == [])
    other_sources = [dict(r, digest=plan_digest(b"elsewhere\n")) if r["path"] == "legacy/RULES.md" else r
                     for r in fx_sources]
    twice = fx_sources + [dict(fx_sources[0])]

    def moved_to(destination):
        """fx_sources with the frozen move source's preservation at `destination` (the planner's pairing)."""
        return [dict(r, preservation=destination) if r["path"] == "legacy/MOVE.md" else r for r in fx_sources]

    def move_row(destination):
        return dict(fx_disposed[2], destination=destination)

    # two frozen move sources sent to ONE destination, and a retire source inside the Move root (a store
    # control-area path no retirement stage may remove).
    pair_dest = "adopter/D.md"
    pair_rows = [move_row(pair_dest), dict(op="move-file", source="legacy/RULES.md", destination=pair_dest,
                                           source_digest=plan_digest(fx_files["legacy/RULES.md"]))]
    pair_sources = [dict(r, preservation=pair_dest) for r in fx_sources if r["path"] == "legacy/MOVE.md"] + [
        dict(path="legacy/RULES.md", disposition="move", occupying=False, preservation=pair_dest,
             digest=plan_digest(fx_files["legacy/RULES.md"]))]

    def two_moves(dest_move, dest_rules, reverse=False):
        """Two frozen move sources, legacy/MOVE.md to dest_move and legacy/RULES.md to dest_rules."""
        rows = [move_row(dest_move), dict(op="move-file", source="legacy/RULES.md", destination=dest_rules,
                                          source_digest=plan_digest(fx_files["legacy/RULES.md"]))]
        sources = [dict(r, preservation=dest_move) for r in fx_sources if r["path"] == "legacy/MOVE.md"] + [
            dict(path="legacy/RULES.md", disposition="move", occupying=False, preservation=dest_rules,
                 digest=plan_digest(fx_files["legacy/RULES.md"]))]
        return dict(rows=rows[::-1] if reverse else rows, sources=sources)

    beneath_new = fx_new + "/y.md"
    in_root, in_root_bytes = store.moved_dest("old/x.md"), b"moved earlier\n"
    in_root_row = dict(op="retire-file", path=in_root, preimage_digest=plan_digest(in_root_bytes))
    in_root_sources = [dict(path=in_root, disposition="retire", occupying=False,
                            digest=plan_digest(in_root_bytes), preservation=archive_rel(rid, in_root))]
    for label, kwargs, reason in (
            ("create-occupied", dict(rows=[fx_create]), "occupied"),
            ("create-digest-mismatch", dict(rows=[fx_create], content={fx_new: b"unplanned\n"}),
             "content_digest"),
            ("create-control-area", dict(rows=[forged_row], content={forged: fx_new_bytes}), "control area"),
            ("retire-unbound", dict(rows=fx_disposed[:1], sources=fx_sources[1:]), "plan source row"),
            ("retire-other-digest", dict(rows=fx_disposed[:1], sources=other_sources), "plan source row"),
            ("retire-ambiguous", dict(rows=fx_disposed[:1], sources=twice), "plan source row"),
            ("move-destination-occupied", dict(rows=fx_disposed[2:3]), "occupied"),
            ("move-into-own-archive", dict(rows=[move_row(archive_rel(rid, "legacy/MOVE.md"))],
                                           sources=moved_to(archive_rel(rid, "legacy/MOVE.md"))), "beneath"),
            # a protected move destination refuses at APPLY, through protected_destination itself (a
            # non-occupying move composes nothing there, so without this rule the base would commit)
            ("move-protected-aiqt", dict(rows=[move_row(".aiqt/x.md")], sources=moved_to(".aiqt/x.md")),
             "destination is protected"),
            ("move-protected-nested-aiqt", dict(rows=[move_row("docs/.aiqt/x.md")],
                                                sources=moved_to("docs/.aiqt/x.md")), "destination is protected"),
            ("move-protected-git", dict(rows=[move_row(".git/x")], sources=moved_to(".git/x")),
             "destination is protected"),
            ("move-protected-pointer", dict(rows=[move_row(".opf.toml")], sources=moved_to(".opf.toml")),
             "destination is protected"),
            ("move-protected-local-pointer", dict(rows=[move_row(".opf.local.toml")],
                                                  sources=moved_to(".opf.local.toml")),
             "destination is protected"),
            # rows the retirement stage could never finish refuse at apply, before the base commits
            ("move-onto-create-file-path", dict(rows=[fx_create, move_row(fx_new)], sources=moved_to(fx_new)),
             "two plan rows"),
            ("two-moves-one-destination", dict(rows=pair_rows, sources=pair_sources), "two plan rows"),
            # a file and its own descendant, in both row orders, and two case variants of one destination
            ("create-file-then-move-beneath-it", dict(rows=[fx_create, move_row(beneath_new)],
                                                      sources=moved_to(beneath_new)), "two plan rows"),
            ("move-beneath-then-create-file", dict(rows=[move_row(beneath_new), fx_create],
                                                   sources=moved_to(beneath_new)), "two plan rows"),
            ("two-moves-file-then-descendant", two_moves(pair_dest, pair_dest + "/child.md"), "two plan rows"),
            ("two-moves-descendant-then-file", two_moves(pair_dest, pair_dest + "/child.md", reverse=True),
             "two plan rows"),
            ("two-moves-case-variants", two_moves(pair_dest, pair_dest.lower()), "two plan rows"),
            # NFC and NFD spellings of one name (one name on a normalization-insensitive filesystem)
            ("two-moves-normalization-variants", two_moves("adopter/caf\u00e9.md", "adopter/cafe\u0301.md"),
             "two plan rows"),
            ("move-store-tree-case-variant", dict(rows=[move_row(".Working/stuff.md")],
                                                  sources=moved_to(".Working/stuff.md")),
             "inside the store tree"),
            ("retire-source-in-move-root", dict(rows=[in_root_row], sources=in_root_sources), "never removes"),
            ("move-preservation-mismatch", dict(rows=fx_disposed[2:3], sources=moved_to("adopter/elsewhere.md")),
             "records preservation")):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = file_fixture(temp)
            if label == "create-occupied":
                (root / fx_new).parent.mkdir(parents=True)
                (root / fx_new).write_bytes(b"adopter's own\n")
            if label == "move-destination-occupied":
                (root / fx_moved).parent.mkdir(parents=True)
                (root / fx_moved).write_bytes(b"already here\n")
            if label == "retire-source-in-move-root":
                (root / in_root).parent.mkdir(parents=True)
                (root / in_root).write_bytes(in_root_bytes)
            before = tree_state(root)
            _txn, why = apply_stage(root, **kwargs)
            check("file-op-{}-refused-untouched-save-journal-dirs".format(label),
                  why is not None and reason in why and untouched_save_journal_dirs(before, root)
                  and lock_free(root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        wrong = refusal(run_adopt_transaction, root, rid, lambda ops: None)
        before = tree_state(root)
        late = refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed[:1], fx_sources),
                       phase="completion")
        check("file-op-other-phase-refused-untouched-save-journal-dirs",
              wrong is None and late is not None and "runs no file op" in late
              and untouched_save_journal_dirs(before, root) and lock_free(root))
        before = tree_state(root)
        unlanded = refusal(run_adopt_transaction, root, other_run,
                           compose_rows([schema.canonical_op("install-pack")]))
        check("compose-rows-unlanded-op-refuses-whole-transaction-untouched-save-journal-dirs",
              unlanded is not None and "not yet executable" in unlanded
              and not (root / JOURNAL_REL / other_run).exists()
              and untouched_save_journal_dirs(before, root) and lock_free(root))

    # 16e: the retirement-stage pairing rule over hand-built lists (check_apply_ops is pure). A frozen
    # source's removal pairs with its committed base archive copy, or with its move destination's create
    # earlier in the same list, ONLY in RETIREMENT_PHASE, and only at the pinned digest.
    gone = _pinned_remove(src, body, FILE_MODE)
    committed_copy = {copy: _sha256(body)}
    check("compose-retirement-committed-pair-admitted", check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([gone], {}, RETIREMENT_PHASE), committed=committed_copy) == [])
    check("compose-committed-pair-outside-retirement-refused", any(
        "preserve-first" in f for f in check_apply_ops(
            rid, "completion", *sealed([gone], {}, "completion"), committed=committed_copy)))
    check("compose-retirement-committed-pair-digest-refused", any("differs" in f for f in check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([gone], {}, RETIREMENT_PHASE), committed={copy: ZERO})))
    check("compose-retirement-write-never-committed-paired", any("preserve-first" in f for f in check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([w(src, new_body, pin=body)], {src: new_body}, RETIREMENT_PHASE),
        committed=committed_copy)))
    moved_to = store.moved_dest(src)
    check("compose-retirement-move-pair-admitted", check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([c(moved_to, body), gone], {moved_to: body}, RETIREMENT_PHASE),
        moves={src: moved_to}) == [])
    check("compose-move-pair-at-apply-refused", any("preserve-first" in f for f in check_apply_ops(
        rid, None, *sealed([c(moved_to, body), gone], {moved_to: body}), moves={src: moved_to})))
    # the preserved copy keeps the source's mode bits exactly: a removal pinned at one mode pairs only with
    # a copy created at that mode, in the same list or as the committed base's INTENT created it.
    check("compose-pin-copy-mode-mismatch-refused", any("pinned mode" in f for f in findings_of(
        *sealed([c(copy, body), _pinned_remove(src, body, 0o600)], {copy: body}))))
    check("compose-retirement-committed-pair-mode-refused", any("pinned mode" in f for f in check_apply_ops(
        rid, RETIREMENT_PHASE, *sealed([gone], {}, RETIREMENT_PHASE), committed=committed_copy,
        committed_modes={copy: 0o600})))

    # 16f: reversal. The retirement stage aborts at its final op (its inventory publication) and the
    # journal's reverse-order rollback restores the removed frozen sources byte-exact and removes the
    # relocated destinations; then an interruption through the kill-point seam, after a removal landed,
    # leaves an open transaction the explicit reconcile rolls back from the journal alone, byte-exact.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_retirement_inventory(op, payload):
            if op["path"] == inventory_rel(rid, RETIREMENT_PHASE):
                raise _journal.JournalError("injected failure at the retirement inventory publication")
            return real_verify(op, payload)

        with mock.patch.object(_journal, "_verify_staged_digest", failing_retirement_inventory):
            aborted = refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed, fx_sources),
                              phase=RETIREMENT_PHASE)
        rtxn = rid + "." + RETIREMENT_PHASE
        check("retirement-abort-reverses-byte-exact",
              aborted is not None and "rolled back" in aborted and snapshot(root) == before
              and txn_state(root, rtxn) == "rolled-back" and lock_free(root))
        # until it is retried, the occupying move source exists only in this run's archive (disclosed)
        check("retirement-rolled-back-occupying-move-source-only-archived",
              "docs/VIEW.md" not in before and fx_view_dest not in before
              and before.get(archive_rel(rid, "docs/VIEW.md")) == fx_files["docs/VIEW.md"])
        # the rolled-back attempt does not block the stage for good: with the fault removed, a retry opens as
        # the next attempt (the rolled-back record retained as it stands), binds the same committed base and
        # finishes every row, the occupying move relocated from the committed archive copy included.
        retry, why = retirement_stage(root)
        done = snapshot(root)
        check("retirement-retry-after-rollback-commits-as-next-attempt",
              retry == rtxn + ".attempt-2" and why is None and txn_state(root, rtxn + ".attempt-2") == "complete"
              and txn_state(root, rtxn) == "rolled-back" and lock_free(root))
        check("retirement-retry-after-rollback-finishes-every-row",
              "legacy/RULES.md" not in done and "legacy/MOVE.md" not in done
              and done.get(fx_moved) == fx_files["legacy/MOVE.md"]
              and done.get(fx_view_dest) == fx_files["docs/VIEW.md"]
              and done.get(archive_rel(rid, "docs/VIEW.md")) == fx_files["docs/VIEW.md"]
              and verify_bundle(root, rid).status == VALID)
        settled = tree_state(root)
        again = refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed, fx_sources),
                        phase=RETIREMENT_PHASE)
        check("retirement-after-committed-retry-refused-untouched-save-journal-dirs",
              again is not None and "fresh plan" in again and untouched_save_journal_dirs(settled, root)
              and lock_free(root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        before = snapshot(root)
        removed = interrupt_when(lambda: not (root / "legacy/RULES.md").exists())
        with mock.patch.object(_journal, "_kill_point", removed):
            try:
                run_adopt_transaction(root, rid, compose_rows(fx_disposed, fx_sources), phase=RETIREMENT_PHASE)
                interrupted = False
            except (_Interrupt, AdoptApplyError) as exc:
                interrupted = isinstance(exc, _Interrupt)
        rtxn = rid + "." + RETIREMENT_PHASE
        check("retirement-interrupt-leaves-open-transaction",
              interrupted and txn_state(root, rtxn) == "open" and not (root / "legacy/RULES.md").exists())
        check("retirement-interrupt-reconciles-back-byte-exact",
              (rtxn, "rolled-back") in reconcile_without_store(root) and snapshot(root) == before
              and lock_free(root))
        # recovery clears the stage too: after reconcile rolled the interrupted attempt back, a retry commits.
        retry, why = retirement_stage(root)
        done = snapshot(root)
        check("retirement-retry-after-reconciled-interrupt-commits",
              retry == rtxn + ".attempt-2" and why is None and txn_state(root, retry) == "complete"
              and "legacy/RULES.md" not in done and done.get(fx_view_dest) == fx_files["docs/VIEW.md"])
    # a phase attempt that never opened (a failure before its INTENT) is retained and retried as well, the
    # attempt number following the highest retained one; the base is never retried, whatever its state.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)

        def capture_fails(*_args, **_kwargs):
            raise _journal.JournalError("injected failure before INTENT")

        with mock.patch.object(_journal, "capture_preimages", capture_fails):
            unopened_base = refusal(run_adopt_transaction, root, rid, compose_rows(
                [fx_create] + fx_disposed, fx_sources, {fx_new: fx_new_bytes}))
        before = tree_state(root)
        base_retry = refusal(run_adopt_transaction, root, rid, compose_rows(
            [fx_create] + fx_disposed, fx_sources, {fx_new: fx_new_bytes}))
        check("unopened-base-never-retried",
              unopened_base is not None and "before it opened" in unopened_base
              and txn_state(root, rid) == "nothing-opened" and base_retry is not None
              and "fresh plan" in base_retry and untouched_save_journal_dirs(before, root) and lock_free(root))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        before = tree_state(root, journal=False)
        rtxn = rid + "." + RETIREMENT_PHASE
        with mock.patch.object(_journal, "capture_preimages", capture_fails):
            unopened = [refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed, fx_sources),
                                phase=RETIREMENT_PHASE) for _ in range(2)]
        check("retirement-unopened-attempts-retained-tree-untouched",
              all(u is not None and "before it opened" in u for u in unopened)
              and tree_state(root, journal=False) == before
              and txn_state(root, rtxn) == txn_state(root, rtxn + ".attempt-2") == "nothing-opened"
              and lock_free(root))
        retry, why = retirement_stage(root)
        check("retirement-retry-after-unopened-attempts-commits",
              retry == rtxn + ".attempt-3" and why is None and txn_state(root, retry) == "complete"
              and "legacy/RULES.md" not in snapshot(root))
    # a gapped history (`<txn>` and `<txn>.attempt-3`, the record of attempt 2 deleted) retries as attempt 4,
    # after the highest retained number, never into the deleted slot.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        rtxn = rid + "." + RETIREMENT_PHASE
        with mock.patch.object(_journal, "capture_preimages", capture_fails):
            for _ in range(3):
                refusal(run_adopt_transaction, root, rid, compose_rows(fx_disposed, fx_sources),
                        phase=RETIREMENT_PHASE)
        gap = _journal_root(root) / (rtxn + ".attempt-2")
        for dirpath, dirnames, filenames in os.walk(gap, topdown=False):
            for name in filenames:
                os.unlink(os.path.join(dirpath, name))
            for name in dirnames:
                os.rmdir(os.path.join(dirpath, name))
        os.rmdir(gap)
        retry, why = retirement_stage(root)
        check("retirement-retry-after-gapped-attempts-takes-next-after-highest",
              retry == rtxn + ".attempt-4" and why is None and txn_state(root, retry) == "complete"
              and txn_state(root, rtxn) == txn_state(root, rtxn + ".attempt-3") == "nothing-opened"
              and not gap.exists() and lock_free(root))

    # 16g: a HAND-BUILT retirement removal (a compose callback appending a pinned removal, never through
    # ApplyOps.retire) is held to the live preservation check too: the committed archive copy it pairs with
    # is re-read before the transaction opens, so a missing or modified copy refuses with the tree untouched,
    # while the intact copy admits the same removal (the control leg).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        rules_copy = root / archive_rel(rid, "legacy/RULES.md")
        saved = rules_copy.read_bytes() if rules_copy.is_file() else None

        def hand_retire(ops):
            mode = stat.S_IMODE((root / "legacy/RULES.md").lstat().st_mode)
            ops.ops.append(_pinned_remove("legacy/RULES.md", fx_files["legacy/RULES.md"], mode))

        for label, tamper in (("missing", rules_copy.unlink),
                              ("modified", lambda: rules_copy.write_bytes(b"old rulez\n"))):
            if saved is not None:
                tamper()
            before = tree_state(root)
            why = refusal(run_adopt_transaction, root, rid, hand_retire, phase=RETIREMENT_PHASE)
            check("retirement-hand-built-removal-{}-archive-refused-untouched-save-journal-dirs".format(label),
                  saved is not None and why is not None and "committed archive copy" in why
                  and untouched_save_journal_dirs(before, root))
            if saved is not None:
                rules_copy.write_bytes(saved)
        htxn, _why = attempt(run_adopt_transaction, root, rid, hand_retire, phase=RETIREMENT_PHASE)
        check("retirement-hand-built-removal-intact-archive-admitted",
              htxn == rid + "." + RETIREMENT_PHASE and not (root / "legacy/RULES.md").exists())

    # 16h: every preserved copy and relocated file keeps its source's mode bits EXACTLY (the journal pins a
    # create's mode with fchmod, so a dropped mode would install FILE_MODE): a private 0600 source is never
    # widened and an executable 0755 one keeps its bits, through the apply stage's archive copies and the
    # retirement stage's relocations, live and from the committed archive.
    for mode in (0o600, 0o755):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = file_fixture(temp)
            for rel in fx_files:
                (root / rel).chmod(mode)

            def mode_of(rel):
                return stat.S_IMODE((root / rel).lstat().st_mode) if (root / rel).is_file() else None

            txn, _why = apply_stage(root)
            copies = [archive_rel(rid, p) for p in ("legacy/RULES.md", ".working/TODO.md", "docs/VIEW.md")]
            check("mode-{:04o}-apply-archive-copies-keep-source-mode".format(mode),
                  txn == rid and all(mode_of(p) == mode for p in copies))
            rtxn, _why = retirement_stage(root)
            check("mode-{:04o}-retirement-relocations-keep-source-mode".format(mode),
                  rtxn == rid + "." + RETIREMENT_PHASE and mode_of(fx_moved) == mode
                  and mode_of(fx_view_dest) == mode and all(mode_of(p) == mode for p in copies)
                  and verify_bundle(root, rid).status == VALID)

    # 16i: the retirement stage re-verifies an OCCUPYING retire source's archive copy too (spec 14.1 check
    # 3: it re-verifies the archived bytes against the plan digest before recording), although it composes
    # no removal for it: a modified or missing copy refuses the stage with the tree untouched.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        apply_stage(root)
        todo_copy = root / archive_rel(rid, ".working/TODO.md")
        saved = todo_copy.read_bytes() if todo_copy.is_file() else None
        for label, tamper in (("modified", lambda: todo_copy.write_bytes(b"hand-kept todO\n")),
                              ("missing", todo_copy.unlink)):
            if saved is not None:
                tamper()
            before = tree_state(root)
            _txn, why = retirement_stage(root, rows=fx_disposed[1:2])
            check("retirement-occupying-retire-{}-archive-refused-untouched-save-journal-dirs".format(label),
                  saved is not None and why is not None and "archive copy" in why
                  and untouched_save_journal_dirs(before, root))
            if saved is not None:
                todo_copy.write_bytes(saved)

    # 16j: a source row whose path is not a string (unhashable included) binds nothing: the op refuses as
    # unbound (AdoptApplyError), never a raw TypeError out of the transaction shell.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        try:
            _txn, why = apply_stage(root, rows=fx_disposed[:1], sources=[dict(fx_sources[0], path=["x"])])
            raw = None
        except TypeError as exc:
            why, raw = None, exc
        check("op-context-non-string-source-path-binds-nothing",
              raw is None and why is not None and "plan source row" in why and lock_free(root))

    # 16k: the whole-tree comparison fails CLOSED. A directory the walk cannot list, the root itself or a
    # child, raises out of tree_state and out of the refusal predicate built on it, never drops out of both
    # states as an empty or partial snapshot (os.walk alone skips it).
    with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
        root = file_fixture(temp)
        real_scandir = os.scandir
        intact = tree_state(root)
        for label, victim in (("root", root), ("child", root / "legacy")):
            def unlistable(path=".", _victim=os.fspath(victim)):
                if os.fspath(path) == _victim:
                    raise PermissionError("injected: directory {} cannot be listed".format(_victim))
                return real_scandir(path)

            for name, probe in (("tree-state", lambda: tree_state(root)),
                                ("refusal-predicate", lambda: untouched_save_journal_dirs(intact, root))):
                with mock.patch.object(os, "scandir", unlistable):
                    try:
                        probe()
                        raised = False
                    except PermissionError:
                        raised = True
                check("{}-unlistable-{}-fails-closed".format(name, label), raised)
        check("tree-state-control-leg-lists-every-entry",
              "legacy" in intact and "legacy/RULES.md" in intact and tree_state(root) == intact)

    # 17: the kill-injection matrix (the journal's crash harness, the migrate.py model): each stage's
    # transaction runs in a CHILD interpreter that os._exit()s (137) at one kill point, leaving only what
    # was already fsync'd; a FRESH child then reconciles, breaking the dead owner's stale lock, and an
    # in-process reconcile after it is a no-op (idempotent). The tree outside the journal must then be
    # EXACTLY its prestate (the transaction not complete) or the verified poststate a clean child commits
    # (the transaction complete, the bundle verifying), with no lock and no open transaction. The kill
    # points are every one the stage's transaction reaches, RECORDED from an in-process run, plus a torn
    # payload (a strict prefix written and fsync'd, then death) in every create the transaction's INTENT
    # lists, plus the two torn frame publications. The apply stage covers preservation (archive copies) and
    # preserve-first removal; the retirement stage covers frozen-source removal and destination publication
    # (live and from the committed archive). The rollback leg kills the transaction one op short of its end, then
    # kills the recovery itself at every restore point and torn rollback frame; a final fresh recovery
    # lands the exact prestate.

    def crash_child(temp, spec, kill=None):
        spec_file = Path(temp) / "kill-spec.json"
        spec_file.write_text(json.dumps(spec), encoding="utf-8")
        env = dict(os.environ)
        env.pop(_journal.KILL_ENV, None)
        if kill is not None:
            env[_journal.KILL_ENV] = kill
        return subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__), "--selftest-child",
                               str(spec_file)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=300).returncode

    def stage_spec(root, phase):
        rows = [fx_create] + fx_disposed if phase is None else fx_disposed
        plan_bytes_, digest = fixture_plan(rid)
        return dict(action="transaction", root=str(root), run_id=rid, phase=phase, rows=rows,
                    sources=fx_sources, content={fx_new: fx_new_bytes.hex()},
                    plan=plan_bytes_.hex(), plan_digest=digest)

    def staged_fixture(temp, phase):
        root = file_fixture(Path(temp) / "root")
        if phase is not None:
            apply_stage(root)          # the committed base, in process: only the stage under test is killed
        return root

    def journal_quiet(root):
        root_fd = store._open_dir_nofollow(root)
        try:
            return journal_state(root_fd, _journal_root(root)) == (None, [])
        finally:
            store._close_fd_exc_safe(root_fd)

    for phase in (None, RETIREMENT_PHASE):
        stage = "apply" if phase is None else "retirement"
        txn_of = _txn_name(rid, phase)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = staged_fixture(temp, phase)
            points, kinds = [], []
            real_run = _journal.run_transaction

            def recording_run(*args):
                kinds.extend(op["op"] for op in args[5])   # the op list the journal applies, in order
                return real_run(*args)

            with mock.patch.object(_journal, "_kill_point", points.append), \
                    mock.patch.object(_journal, "run_transaction", recording_run):
                (apply_stage if phase is None else retirement_stage)(root)
            tears = ["torn-payload:{}".format(i) for i, kind in enumerate(kinds) if kind == "create"]
        n = len([p for p in points if p.startswith("after-apply-")])
        check("kill-matrix-{}-points-recorded".format(stage),
              "after-lock" in points and "after-publish-COMPLETE" in points and n >= 4 and len(tears) >= 3)
        with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
            root = staged_fixture(temp, phase)
            pre = tree_state(root, journal=False)
            clean = crash_child(temp, stage_spec(root, phase))
            post = tree_state(root, journal=False)
        check("kill-matrix-{}-clean-child-commits".format(stage), clean == 0 and post != pre)
        for point in points + tears + ["torn:INTENT", "torn:COMPLETE"]:
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                root = staged_fixture(temp, phase)
                pre = tree_state(root, journal=False)
                died = crash_child(temp, stage_spec(root, phase), kill=point)
                recovered = crash_child(temp, dict(action="reconcile", root=str(root)))
                again, _why = attempt(reconcile, root)
                state, txn_now = tree_state(root, journal=False), txn_state(root, txn_of)
                check("kill-{}-{}-recovers-to-prestate-or-verified-poststate".format(stage, point),
                      died == 137 and recovered == 0 and again == [] and journal_quiet(root)
                      and ((state == pre and txn_now != "complete")
                           or (state == post and txn_now == "complete"
                               and verify_bundle(root, rid).status == VALID)))
        for rpoint in (["torn:ROLLBACK-IN-PROGRESS"] + ["after-restore-{}".format(i) for i in range(n)]
                       + ["torn:ROLLBACK-COMPLETE"]):
            with tempfile.TemporaryDirectory(prefix="opf-adopt-apply-") as temp:
                root = staged_fixture(temp, phase)
                pre = tree_state(root, journal=False)
                died = crash_child(temp, stage_spec(root, phase), kill="after-apply-{}".format(n - 2))
                rdied = crash_child(temp, dict(action="reconcile", root=str(root)), kill=rpoint)
                recovered = crash_child(temp, dict(action="reconcile", root=str(root)))
                again, _why = attempt(reconcile, root)
                check("kill-rollback-{}-{}-lands-exact-prestate".format(stage, rpoint),
                      died == 137 and rdied == 137 and recovered == 0 and again == [] and journal_quiet(root)
                      and tree_state(root, journal=False) == pre and txn_state(root, txn_of) == "rolled-back")

    if census:
        left_open = sorted(descriptors() - entry_descriptors)
        check("self-test-leaves-no-descriptor-open", not left_open,
              observed="{} descriptor(s) left open, first {}".format(
                  len(left_open), [(fd, os.readlink("/proc/self/fd/{}".format(fd))) for fd in left_open[:4]]))
    else:
        skipped.append(("self-test-leaves-no-descriptor-open", no_census))
    for name, why in skipped:
        print("  SKIPPED: {} ({})".format(name, why))
    if failures:
        print("OPF-ADOPT-APPLY SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
            if f in failure_details:
                print("    observed: {}".format(failure_details[f]))
        return 1
    print("OPF-ADOPT-APPLY SELF-TEST: PASS ({} apply-shell checks; ten executable ops: the file ops, "
          "init-store, the registration ops, enable-hook and the finish ops)".format(checked[0]))
    return 0


def _kill_injection_child(spec_path):
    """The self-test's kill-injection child, `--selftest-child SPEC` (the migrate.py crash-harness model,
    entered as `_opf_init_operation --selftest-child` is): in THIS fresh interpreter, run ONE adoption
    transaction of the plan rows SPEC names, or reconcile(), against SPEC's fixture root. The transaction
    carries SPEC's sealed fixture plan digest in its inventory's [adoption] identity, and a base
    transaction stages that plan's bytes in the run's bundle first, as apply stages the approved plan
    (spec 4.2). The parent sets _journal.KILL_ENV, so the journal os._exit()s (137) at that named point
    exactly as a power loss would; otherwise 0 done, 2 refused."""
    try:
        spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
        if spec["action"] == "reconcile":
            reconcile(spec["root"])
        else:
            content = {path: bytes.fromhex(data) for path, data in spec["content"].items()}
            run_id, phase = spec["run_id"], spec["phase"]
            plan_bytes = bytes.fromhex(spec["plan"])
            rows = compose_rows(spec["rows"], spec["sources"], content)

            def compose(ops):
                if phase is None:
                    ops.create(plan_rel(run_id), plan_bytes)
                return rows(ops)
            run_adopt_transaction(spec["root"], run_id, compose, phase=phase, plan_digest=spec["plan_digest"])
    except (AdoptApplyError, OSError, ValueError, KeyError) as exc:
        print("kill-injection child refused: {}".format(exc), file=sys.stderr)
        return 2
    return 0


def _init_store_self_test(check):
    """Slice 3: init-store composed over the coupled-init substrate inside the adoption transaction.
    The precondition vectors are pure; the executing vectors run over REAL git fixtures (the
    substrate binds the repository and HEAD) under the substrate's own pinned git lifecycle, and the
    interrupted run is a child process dispatching THROUGH THIS ENGINE, SIGKILLed inside the
    substrate's publication (--selftest-init-store-child), so recovery is proved over the adoption
    journal, not the substrate alone. Each refusal is attributed by a reason keyword and judged on
    BEHAVIOUR: trees compared, operations counted, transactions classified."""
    import shutil
    import tempfile
    import _opf_oplock
    cannot = store.CANNOT_EVALUATE
    manifest_rel, scaffold = _init_store_scaffold(True, True)

    def members_for(sc, fake="sha256:" + "a" * 64):
        out = []
        for path in sorted(sc):
            digest = sc[path]
            if path == init_op.PROVENANCE_RELPATH:
                digest = UNBOUND_DIGEST
            elif digest is None:
                digest = fake
            out.append(dict(path=path, digest=digest))
        return out

    def ctx(root, adoption="first-adoption", sources=(), ancestral=None, run_id=None,
            recover=False):
        plan = dict(run_id=run_id or "adopt-20260917T120000Z-0123456789abcdef",
                    store=dict(store_root=".", machine_rel=manifest_rel.rsplit("/", 1)[0],
                               adoption=adoption),
                    sources=[dict(r) for r in sources])
        out = dict(product_root=root, plan=plan, ancestral=ancestral)
        if recover:
            out["recover"] = True
        return out

    def refused(res, needle):
        return res.status == cannot and any(needle in f for f in res.findings)

    # The handler's own derivations pin the substrate's: the manifest path and the default manifest's
    # exact digest (baseline record types only, every module tier off), and a scaffold that is the
    # COMPLETE creation set: machine sources, init.toml, the root pointer, CHANGELOG.md when created,
    # and every declared view.
    import _opf_init
    default = tomllib.loads(_opf_init.build_manifest())
    row = dict(op="init-store", store_root=".", members=members_for(scaffold))
    bound = {m["path"]: m["digest"] for m in row["members"]}
    check("init-store-manifest-is-the-substrate-manifest", manifest_rel == init_op._MANIFEST_RELPATH
          and bound[manifest_rel] == "sha256:" + _sha256(_opf_init.build_manifest().encode("utf-8")))
    check("init-store-default-manifest-baseline-only", not any(default["modules"].values())
          and set(default["types"]) == set(store.BASELINE_TYPES))
    check("init-store-scaffold-is-the-complete-creation-set",
          init_op.PROVENANCE_RELPATH in scaffold and store.POINTER_REL in scaffold
          and init_op.CHANGELOG_RELPATH in scaffold
          and {v["target"] for v in default["views"].values()} <= set(scaffold)
          and set(init_op.BOOTSTRAP_SOURCE_ROSTER) <= set(scaffold))
    check("init-store-external-destinations-derived",
          _init_store_external_destinations() == [init_op.CHANGELOG_RELPATH])
    check("init-store-row-validates", schema.validate_op(row).status == store.VALID)

    with tempfile.TemporaryDirectory() as tmp:
        bare = os.path.join(tmp, "bare")
        os.mkdir(bare)
        missing = dict(row, members=[m for m in row["members"] if m["path"] != store.POINTER_REL])
        stray = dict(row, members=row["members"] + [dict(path=".working/notes.md",
                                                         digest="sha256:" + "0" * 64)])
        bound_init = dict(row, members=[dict(m, digest="sha256:" + "d" * 64)
                                        if m["path"] == init_op.PROVENANCE_RELPATH else m
                                        for m in row["members"]])
        unbound_view = dict(row, members=[dict(m, digest=UNBOUND_DIGEST)
                                          if m["path"] == ".working/TODO.md" else m
                                          for m in row["members"]])
        wrong_fixed = dict(row, members=[dict(m, digest="sha256:" + "c" * 64)
                                         if m["path"].endswith("/version.toml") else m
                                         for m in row["members"]])
        other = ctx(bare)
        other["plan"]["store"]["machine_rel"] = store.WORKING_DIRNAME + "/other"
        check("init-store-no-context-refused", refused(dispatch(row), "no approved plan"))
        check("init-store-no-run-id-refused",
              refused(dispatch(row, ctx(bare, run_id="imp-20260917T120000Z-0123456789abcdef")),
                      "no adoption run id"))
        check("init-store-missing-member-refused",
              refused(dispatch(missing, ctx(bare)), "not exactly the scaffold"))
        check("init-store-stray-member-refused",
              refused(dispatch(stray, ctx(bare)), "not exactly the scaffold"))
        check("init-store-bound-looking-init-toml-refused",
              refused(dispatch(bound_init, ctx(bare)), "cannot be bound at plan time"))
        check("init-store-unbound-marker-on-bindable-member-refused",
              refused(dispatch(unbound_view, ctx(bare)), "bind it"))
        check("init-store-fixed-payload-digest-refused",
              refused(dispatch(wrong_fixed, ctx(bare)), "builders fix"))
        check("init-store-other-machine-store-refused", refused(dispatch(row, other),
                                                                "default machine store"))
        check("init-store-readoption-without-seed-refused",
              refused(dispatch(row, ctx(bare, "re-adoption")), "never from"))
        check("init-store-first-adoption-with-seed-refused",
              refused(dispatch(row, ctx(bare, ancestral="0" * 40)), "no ancestry"))
        check("init-store-preconditions-wrote-nothing", os.listdir(bare) == [])
    # Fail-closed sources-row validation (round 2): a malformed consumed field never reads as
    # non-occupying, never freezes a source, and never authorizes a control-area copy; the
    # planner's own _validate_plan_sources is the ONE validator for these fields.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        rel = store.WORKING_DIRNAME + "/notes.md"
        (root / store.WORKING_DIRNAME).mkdir()
        (root / rel).write_bytes(b"adopter notes\n")
        digest = "sha256:" + _sha256(b"adopter notes\n")
        run = "adopt-20260917T120000Z-0123456789abcdef"
        rfd = _open_product_root(root)
        try:
            def admitted_err(sources):
                try:
                    _init_store_admitted(rfd, root, dict(run_id=run, sources=sources))
                except AdoptApplyError as exc:
                    return str(exc)
                return None

            stringy = dict(path=rel, digest=digest, disposition="keep", occupying="true")
            check("init-store-nonbool-occupying-refused-fail-closed",
                  "validated fail-closed" in (admitted_err([stringy]) or ""))
            shred = dict(path=rel, digest=digest, disposition="shred", occupying=False,
                         preservation=store.IMPORTED_REL + "/x.md")
            check("init-store-invalid-disposition-copy-refused-fail-closed",
                  "validated fail-closed" in (admitted_err([shred]) or ""))
            wrong_home = dict(path=rel, digest=digest, disposition="retire", occupying=False,
                              preservation=store.ARCHIVE_REL + "/adoption/other/x.md")
            check("init-store-wrong-preservation-home-refused-fail-closed",
                  "validated fail-closed" in (admitted_err([wrong_home]) or ""))
            good = dict(path=rel, digest=digest, disposition="keep", occupying=False)
            adm, adirs = _init_store_admitted(rfd, root, dict(run_id=run, sources=[good]))
            check("init-store-valid-row-still-admits",
                  [e["path"] for e in adm] == [rel] and adirs == ())
        finally:
            store._close_fd_exc_safe(rfd)

    if shutil.which("git") is None:
        check("init-store-git-available", False)
        return
    _opf_oplock._st_with_git_lifecycle(lambda: _init_store_git_checks(check, ctx, refused))


def _init_store_child(root, env, kill, row, context):
    """Run ONE dispatch in a fresh interpreter with the named substrate kill hook armed; returns the
    child's exit status (negative on the expected SIGKILL)."""
    import base64
    import json
    import subprocess
    doc = dict(row=row, ctx=dict(context, product_root=None))
    blob = base64.b64encode(json.dumps(doc).encode("utf-8")).decode("ascii")
    argv = [sys.executable, "-I", "-B", os.path.abspath(__file__),
            "--selftest-init-store-child", root, kill, blob]
    proc = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=300)
    return proc.returncode


def _init_store_child_main(argv):
    """The --selftest-init-store-child entry: install the substrate's source-publication kill hook
    (production code carries no kill hooks), dispatch ONE init-store row in this interpreter, print
    the status. The self-test SIGKILLs the ENGINE mid-publication this way — under its held adoption
    journal lock and open transaction — exactly as a crash would."""
    import base64
    import json
    import signal
    root, kill, blob = argv
    doc = json.loads(base64.b64decode(blob))
    name, _sep, arg = kill.partition(":")

    def die():
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGKILL)

    def plant_stage(pfd, entry, data):
        fd = os.open(entry["staging"], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=pfd)
        try:
            os.fchmod(fd, entry["mode"])
            _journal._write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

    if name == "source":
        counter = {"n": 0}
        real_stage = init_op._stage_and_publish

        def stage(pfd, pname, entry, data):
            out = real_stage(pfd, pname, entry, data)
            counter["n"] += 1
            if counter["n"] == int(arg):
                die()
            return out
        init_op._stage_and_publish = stage
    elif name == "stage":
        # crash AFTER the named member's staging file exists, BEFORE its link: the payload
        # staging leftover the reversal's scrub must remove (codex round 2, finding 2)
        real_stage = init_op._stage_and_publish

        def stage(pfd, pname, entry, data):
            if entry["path"] == arg:
                plant_stage(pfd, entry, data)
                die()
            return real_stage(pfd, pname, entry, data)
        init_op._stage_and_publish = stage
    elif name == "linked":
        # crash AFTER the named member's destination link, BEFORE the staging unlink
        real_stage = init_op._stage_and_publish

        def stage(pfd, pname, entry, data):
            if entry["path"] == arg:
                plant_stage(pfd, entry, data)
                os.link(entry["staging"], pname, src_dir_fd=pfd, dst_dir_fd=pfd,
                        follow_symlinks=False)
                die()
            return real_stage(pfd, pname, entry, data)
        init_op._stage_and_publish = stage
    elif name == "txnmkdir":
        # crash between the init-store transaction's mkdir and its frames.log
        real_frames = _journal._create_frames_excl

        def frames(jr_fd, txn_dir):
            if str(txn_dir).endswith("." + INIT_STORE_PHASE):
                die()
            return real_frames(jr_fd, txn_dir)
        _journal._create_frames_excl = frames
    elif name == "intent":
        # crash after the INTENT is durable, before the substrate writes anything
        def run(*_a, **_k):
            die()
        init_op.run_init_operation = run
    elif name == "phasestage":
        # crash INSIDE the substrate's publication of the named phase record: the record's
        # staging file exists in the operation directory (an exact staging-name leftover that
        # classifies the record CANNOT-EVALUATE), the record itself does not (claude round 3)
        import _opf_init_substrate
        import _opf_oplock
        real_record = _opf_init_substrate.record_phase

        def record(sub, cap, phase):
            if phase == arg:
                pname = "%04d-%s.json" % (sub._next_seq, phase)
                fd = os.open(_opf_oplock._staging_name(pname),
                             os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=sub._op_fd)
                _journal._write_all(fd, b"torn")
                os.fsync(fd)
                os.close(fd)
                die()
            return real_record(sub, cap, phase)
        _opf_init_substrate.record_phase = record
    elif name == "scrubdir":
        # crash immediately AFTER the recovery scrub removes the named reversed directory,
        # BEFORE the record discard: the still-recorded operation is the recovery
        # discriminator, so the record-LAST order keeps the next dispatch recoverable
        real_rmdir = os.rmdir

        def rmdir(dname, *a, **k):
            real_rmdir(dname, *a, **k)
            if dname == arg:
                die()
        os.rmdir = rmdir
        # the containment probe checks these functions by IDENTITY against supports_dir_fd
        os.supports_dir_fd.add(rmdir)
    elif name == "cleanup":
        # crash INSIDE the reversed-record discard of a RECOVERY dispatch; "partial" first
        # removes one record file, simulating a kill between the discard's own unlinks
        import _opf_init_substrate

        def discard(_writer, op_id):
            if arg == "partial":
                os.unlink(os.path.join(init_op._ops_dir(root), op_id,
                                       _opf_init_substrate.PLAN_NAME))
            die()
        _opf_init_substrate.discard_reversed_operation = discard
    elif name != "none":
        return 2
    res = dispatch(doc["row"], dict(doc["ctx"], product_root=root))
    print(json.dumps({"status": res.status, "findings": res.findings}))
    return 0


def _init_store_git_checks(check, ctx, refused):
    """The executing init-store vectors over real git fixtures (see _init_store_self_test)."""
    import tempfile
    import _opf_init
    from _opf_schema import high_water
    valid = store.VALID
    manifest_rel, scaffold = _init_store_scaffold(True, True)
    # Every adoption inventory names its run's sealed plan in its [adoption] identity (spec 4.2), so
    # these vectors run over a fixture plan sealed as the planner seals it, as the shell's own self-test
    # does: a base transaction stages it, and a hand-built base inventory lists it.
    _emit_inventory = globals()["emit_inventory"]
    _run_adopt_transaction = globals()["run_adopt_transaction"]

    def fixture_plan(run_id_):
        body = dict(format="opf.adoption.plan/v2", run_id=run_id_)
        digest = "sha256:" + _sha256(emit_checked(body).encode("utf-8"))
        return emit_checked(dict(body, plan_digest=digest)).encode("utf-8"), digest

    def emit_inventory(run_id_, rows, phase=None, plan_digest=None):
        plan_bytes_, digest = fixture_plan(run_id_)
        listed = [dict(r) for r in rows]
        if phase is None and plan_rel(run_id_) not in set(r.get("path") for r in listed):
            listed.append(inventory_row(plan_rel(run_id_), plan_bytes_))
        return _emit_inventory(run_id_, listed, phase, digest if plan_digest is None else plan_digest)

    def run_adopt_transaction(product_root, run_id_, compose, phase=None, plan_digest=None):
        plan_bytes_, digest = fixture_plan(run_id_)
        staged = compose
        if phase is None and plan_digest is None:
            def staged(ops):
                ops.create(plan_rel(run_id_), plan_bytes_)
                return compose(ops)
        return _run_adopt_transaction(product_root, run_id_, staged, phase,
                                      digest if plan_digest is None else plan_digest)

    def op_ids(root):
        ops = init_op._ops_dir(root)
        return sorted(os.listdir(ops)) if os.path.isdir(ops) else []

    def snap(root):
        """The worktree snapshot MINUS this engine's own journal home (which legitimately persists
        transactions and their preimages across a reversal)."""
        return {rel: v for rel, v in init_op._tree_snapshot(root).items()
                if rel.split(os.sep)[0] != ".aiqt"}

    def live_digest(root, rel):
        with open(os.path.join(root, rel), "rb") as fh:
            return "sha256:" + _sha256(fh.read())

    def txn_state(root, run):
        root_fd = _open_product_root(root)
        try:
            jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
            try:
                return _journal.classify_state(
                    jr_fd, _journal_root(root) / _txn_name(run, INIT_STORE_PHASE))
            finally:
                _journal._close_fd_quietly(jr_fd)
        finally:
            store._close_fd_exc_safe(root_fd)

    with tempfile.TemporaryDirectory(prefix="opf-adopt-init-") as base:
        base = os.path.realpath(base)
        env = init_op._st_git_env(base)
        nonce = ("%016x" % n for n in range(1, 128))

        def rid():
            return mint_run_id(datetime.datetime(2026, 9, 17, 12, 0, 0,
                                                 tzinfo=datetime.timezone.utc), next(nonce))

        def plant(root, rel, data):
            """Write a fixture file, setting every directory the write creates to EXACTLY 0755,
            the mode the substrate plans and admits. A planted directory otherwise takes the
            ambient umask (0775 under umask 002) or a default ACL on the TMPDIR (0750), and each
            vector would then refuse on that directory's mode instead of the fact it pins."""
            made, parent = [], os.path.dirname(os.path.join(root, rel))
            while not os.path.isdir(parent):
                made.append(parent)
                parent = os.path.dirname(parent)
            init_op._write(os.path.join(root, rel), data)
            for path in reversed(made):
                os.chmod(path, 0o755)

        # The probe: the SUBSTRATE itself publishes one store in a scratch repo, and the
        # planner-bindable view digests are harvested from it (each view is a pure function of the
        # planned sources, so the harvest is repository-independent; the probe never meets the
        # engine under test).
        probe = init_op._plain_repo(os.path.join(base, "probe"), env)
        pres = init_op.run_init_operation(probe)
        check("init-store-probe-views-ready", pres.status == init_op.VIEWS_READY)
        members = []
        for path in sorted(scaffold):
            digest = scaffold[path]
            if path == init_op.PROVENANCE_RELPATH:
                digest = UNBOUND_DIGEST
            elif digest is None:
                digest = live_digest(probe, path)
            members.append(dict(path=path, digest=digest))
        row = dict(op="init-store", store_root=".", members=members)
        check("init-store-full-row-validates", schema.validate_op(row).status == valid)

        # A first adoption over NOT-ADOPTED: the substrate publishes the default store inside the
        # run's COMPLETE init-store transaction; every member's live bytes match the row. The same
        # run never reruns, and a fresh run over the initialized store refuses on its history.
        root = init_op._plain_repo(os.path.join(base, "fresh"), env)
        run = rid()
        res = dispatch(row, ctx(root, run_id=run))
        check("init-store-first-adoption-views-ready", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED and len(op_ids(root)) == 1
              and live_digest(root, manifest_rel) == scaffold[manifest_rel]
              and txn_state(root, run) == "complete")
        check("init-store-same-run-rerun-refused",
              refused(dispatch(row, ctx(root, run_id=run)), "already completed its init-store"))
        before = snap(root)
        check("init-store-initialized-store-refused",
              refused(dispatch(row, ctx(root, run_id=rid())), "already records init history")
              and snap(root) == before)

        # THE REVERSAL (claude 4 / codex 3): a member digest the published bytes cannot match (a
        # deferred view digest, so every pre-write check passes) rolls the WHOLE scaffold back from
        # the transaction's durable intent: tree byte-identical, no store, no operation record, the
        # transaction terminal ROLLED-BACK. The reversed run id is burnt; a fresh run then succeeds.
        root = init_op._plain_repo(os.path.join(base, "reverse"), env)
        wrongv = [dict(m, digest="sha256:" + "b" * 64) if m["path"] == ".working/TODO.md"
                  else dict(m) for m in members]
        before = snap(root)
        run = rid()
        res = dispatch(dict(row, members=wrongv), ctx(root, run_id=run))
        check("init-store-member-mismatch-reversed", refused(res, "restoring NOT-ADOPTED")
              and snap(root) == before and op_ids(root) == []
              and store.resolve_store(root).status != store.RESOLVED
              and txn_state(root, run) == "rolled-back")
        check("init-store-reversed-run-never-reruns",
              refused(dispatch(row, ctx(root, run_id=run)), "was reversed"))
        res = dispatch(row, ctx(root, run_id=rid()))
        check("init-store-fresh-run-after-reversal", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # A wrong PLAN-FIXED digest refuses before anything at all is written (no journal home).
        root = init_op._plain_repo(os.path.join(base, "fixed"), env)
        wrongf = [dict(m, digest="sha256:" + "c" * 64) if m["path"].endswith("/version.toml")
                  else dict(m) for m in members]
        before = init_op._tree_snapshot(root)
        check("init-store-fixed-digest-refused-pre-write",
              refused(dispatch(dict(row, members=wrongf), ctx(root, run_id=rid())), "builders fix")
              and init_op._tree_snapshot(root) == before)

        # Reconcile-first: an unreadable adoption journal lock refuses before the substrate.
        root = init_op._plain_repo(os.path.join(base, "journal"), env)
        os.makedirs(os.path.join(root, JOURNAL_REL))
        with open(os.path.join(root, JOURNAL_REL, "lock"), "wb") as fh:
            fh.write(b"not a lock\n")
        before = init_op._tree_snapshot(root)
        check("init-store-journal-first", refused(dispatch(row, ctx(root, run_id=rid())),
                                                  "adoption journal")
              and init_op._tree_snapshot(root) == before and op_ids(root) == [])

        # THE BLIND-INIT DISCRIMINATOR (codex 7 / claude 5): the SAME foreign .working fixture is
        # refused undispositioned and ADMITTED once the plan dispositions it — the substrate then
        # verifies and preserves the frozen source byte-exact through the whole publication.
        root = init_op._plain_repo(os.path.join(base, "blind"), env)
        payload = b"adopter notes\n"
        plant(root, os.path.join(store.WORKING_DIRNAME, "notes.md"), payload)
        before = snap(root)
        check("init-store-undispositioned-working-refused",
              refused(dispatch(row, ctx(root, run_id=rid())), "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])
        frozen_row = dict(path=".working/notes.md", digest="sha256:" + _sha256(payload),
                          disposition="keep", occupying=False)
        res = dispatch(row, ctx(root, run_id=rid(), sources=[frozen_row]))
        with open(os.path.join(root, store.WORKING_DIRNAME, "notes.md"), "rb") as fh:
            intact = fh.read() == payload
        check("init-store-dispositioned-working-admitted", res.status == valid and intact
              and store.resolve_store(root).status == store.RESOLVED)
        root = init_op._plain_repo(os.path.join(base, "driftsrc"), env)
        plant(root, os.path.join(store.WORKING_DIRNAME, "notes.md"), b"drifted\n")
        check("init-store-drifted-frozen-source-refused",
              refused(dispatch(row, ctx(root, run_id=rid(), sources=[frozen_row])),
                      "undispositioned foreign") and op_ids(root) == [])

        # This run's evidence bundle is admitted only VERIFIED: the inventories plus exactly the
        # payloads they list; one tampered byte refuses the whole bundle.
        root = init_op._plain_repo(os.path.join(base, "bundle"), env)
        run = rid()
        home = evidence_home_rel(run)
        data = b"evidence payload\n"
        plant(root, os.path.join(home, "observe.json"), data)
        inv = emit_inventory(run, [inventory_row(home + "/observe.json", data)])
        plant(root, os.path.join(home, "inventory.toml"), inv)
        plant(root, plan_rel(run), fixture_plan(run)[0])
        res = dispatch(row, ctx(root, run_id=run))
        check("init-store-verified-bundle-admitted", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and live_digest(root, home + "/observe.json") == "sha256:" + _sha256(data))
        root = init_op._plain_repo(os.path.join(base, "badbundle"), env)
        run = rid()
        home = evidence_home_rel(run)
        plant(root, os.path.join(home, "observe.json"), b"tampered\n")
        plant(root, os.path.join(home, "inventory.toml"),
              emit_inventory(run, [inventory_row(home + "/observe.json", data)]))
        plant(root, plan_rel(run), fixture_plan(run)[0])
        check("init-store-tampered-bundle-refused",
              refused(dispatch(row, ctx(root, run_id=run)), "does not verify")
              and op_ids(root) == [])

        # CHANGELOG.md disposition (codex 6 / claude 3): a pre-existing changelog with no plan row
        # refuses; dispositioned, it is preserved byte-exact and never a member; a members list
        # still naming it is a set mismatch.
        root = init_op._plain_repo(os.path.join(base, "changelog"), env)
        body = b"# My changes\n"
        init_op._write(os.path.join(root, init_op.CHANGELOG_RELPATH), body)
        mem_nolog = [m for m in members if m["path"] != init_op.CHANGELOG_RELPATH]
        check("init-store-existing-changelog-undispositioned-refused",
              refused(dispatch(dict(row, members=mem_nolog), ctx(root, run_id=rid())),
                      "undispositioned declared destination"))
        logrow = dict(path=init_op.CHANGELOG_RELPATH, digest="sha256:" + _sha256(body),
                      disposition="keep", occupying=False)
        res = dispatch(dict(row, members=mem_nolog), ctx(root, run_id=rid(), sources=[logrow]))
        with open(os.path.join(root, init_op.CHANGELOG_RELPATH), "rb") as fh:
            preserved = fh.read() == body
        check("init-store-dispositioned-changelog-preserved", res.status == valid and preserved)
        root = init_op._plain_repo(os.path.join(base, "changelog2"), env)
        init_op._write(os.path.join(root, init_op.CHANGELOG_RELPATH), body)
        check("init-store-preserved-changelog-never-a-member",
              refused(dispatch(row, ctx(root, run_id=rid(), sources=[logrow])),
                      "not exactly the scaffold"))

        # SOURCE VALIDATION BEFORE CONSUMPTION, .working ABSENT (codex round 3, finding 3): a
        # malformed external CHANGELOG.md row (a string occupying) refuses fail-closed BEFORE
        # the disposition preflight consumes it, with nothing written -- the planner's
        # _validate_plan_sources runs independent of whether .working exists.
        root = init_op._plain_repo(os.path.join(base, "extmalformed"), env)
        init_op._write(os.path.join(root, init_op.CHANGELOG_RELPATH), body)
        badlog = dict(logrow, occupying="true")
        before = snap(root)
        check("init-store-malformed-external-row-refused-fail-closed",
              refused(dispatch(dict(row, members=mem_nolog),
                               ctx(root, run_id=rid(), sources=[badlog])),
                      "validated fail-closed")
              and snap(root) == before and op_ids(root) == [])

        # The counters flip: a re-adoption whose ancestral store allocated WL-7 seeds from that
        # pinned high-water (next WL 8, never a reused 1), its counters member digest-bound to the
        # seeded builder bytes; a snapshot missing a baseline namespace refuses inside the substrate
        # (UNKNOWN, never zero) and the scaffold is REVERSED, tree unchanged.
        values = dict.fromkeys(sorted(set(store.BASELINE_TYPES.values())), 0)
        values.update(WL=7, BI=3)
        seeded = _opf_init.build_counters(seed=dict(values)).encode("utf-8")
        missing_ns = {k: v for k, v in values.items() if k != "HO"}
        for name, counters in (("readopt", values), ("unknown", missing_ns)):
            root = init_op._plain_repo(os.path.join(base, name), env)
            init_op._write(os.path.join(root, init_op.COUNTERS_RELPATH),
                           init_op._counters_toml(counters))
            init_op._git(["add", "-A"], root, env)
            init_op._git(["commit", "-q", "-m", "adopted"], root, env)
            evidence = init_op._head(root, env)
            init_op._git(["rm", "-q", "-r", store.WORKING_DIRNAME], root, env)
            init_op._git(["commit", "-q", "-m", "deleted"], root, env)
            mem = [dict(m, digest="sha256:" + _sha256(seeded))
                   if m["path"] == init_op.COUNTERS_RELPATH else dict(m) for m in members]
            before = snap(root)
            run = rid()
            res = dispatch(dict(row, members=mem), ctx(root, "re-adoption", ancestral=evidence,
                                                       run_id=run))
            if name == "readopt":
                got = {}
                if res.status == valid:
                    with open(os.path.join(root, init_op.COUNTERS_RELPATH), "rb") as fh:
                        got = tomllib.loads(fh.read().decode("utf-8"))["counters"]
                check("init-store-readoption-seeds-ancestral-high-water", res.status == valid
                      and high_water(got, "WL") + 1 == 8 and got.get("BI") == 3)
            else:
                check("init-store-missing-namespace-reversed", refused(res, "UNKNOWN")
                      and snap(root) == before and op_ids(root) == []
                      and txn_state(root, run) == "rolled-back")

        # RECOVERY (claude 1 / codex 2, 4): the ENGINE is SIGKILLed inside the substrate's
        # publication, under its held adoption lock and open transaction. Every later run refuses
        # into reconcile(); reconcile reverses the scaffold from the journal ALONE; the orphaned
        # substrate record is discarded only under the EXPLICIT recover flag; a fresh run then
        # succeeds. A partial substrate operation is never resumed across adoption runs.
        root = init_op._plain_repo(os.path.join(base, "kill"), env)
        before = snap(root)
        rc = _init_store_child(root, env, "source:2", row, ctx(root, run_id=rid()))
        partial = op_ids(root)
        check("init-store-killed-engine-left-partial", rc != 0 and len(partial) == 1
              and store.resolve_store(root).status != store.RESOLVED)
        # THE PLAIN-SUBSTRATE COUPLING (round 2): a plain `opf init` resume can never finish or
        # publish the adoption-started partial operation; its plan records the coupled recovery
        # policy and refuses any resume, so the store stays unpublished and the adoption
        # transaction stays the only recovery path.
        plain = init_op.run_init_operation(root, recover=True)
        check("init-store-plain-resume-of-coupled-partial-refused",
              plain.status != init_op.VIEWS_READY
              and "never resumed or published outside"
              in (plain.primary_failure or {}).get("detail", "")
              and store.resolve_store(root).status != store.RESOLVED
              and op_ids(root) == partial)
        check("init-store-interrupted-run-refuses-into-reconcile",
              refused(dispatch(row, ctx(root, run_id=rid())), "never seized")
              and op_ids(root) == partial)
        outcomes = reconcile(root)
        gone = all(not os.path.lexists(os.path.join(root, m["path"])) for m in members)
        check("init-store-reconcile-reverses-from-journal-alone",
              "rolled-back" in [outcome for _t, outcome in outcomes] and gone
              and op_ids(root) == partial)
        check("init-store-orphan-discard-needs-explicit-recover",
              refused(dispatch(row, ctx(root, run_id=rid())), "EXPLICIT context recover")
              and op_ids(root) == partial)
        res = dispatch(row, ctx(root, run_id=rid(), recover=True))
        check("init-store-fresh-run-after-recovery", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and len(op_ids(root)) == 1 and partial[0] not in op_ids(root)
              and {rel: v for rel, v in snap(root).items()
                   if rel.split(os.sep)[0] != store.WORKING_DIRNAME
                   and rel != init_op.CHANGELOG_RELPATH
                   and rel != store.POINTER_REL} == before)

        # THE REVERSAL REMOVES ONLY ITS OWN PUBLICATION (round 2): a foreign write that lands at
        # a member path between the durable intent and the substrate is PRESERVED -- the reversal
        # refuses fail-closed, the transaction stays open, and reconcile() refuses the same way.
        # First a poststate-bound member (CHANGELOG.md), then the one unbindable member
        # (init.toml, whose only binding is the operation's recorded plan, absent here).
        real_run = init_op.run_init_operation
        for fixture, target, payload in (
                ("foreignlog", init_op.CHANGELOG_RELPATH, b"the user's own changelog\n"),
                ("foreigninit", init_op.PROVENANCE_RELPATH, b"not the operation's provenance\n")):
            root = init_op._plain_repo(os.path.join(base, fixture), env)
            run = rid()

            def write_then_run(*a, **k):
                init_op._write(os.path.join(root, target), payload)
                return real_run(*a, **k)

            init_op.run_init_operation = write_then_run
            try:
                res = dispatch(row, ctx(root, run_id=run))
            finally:
                init_op.run_init_operation = real_run

            def foreign_kept():
                return init_op._read_or_none(os.path.join(root, target)) == payload

            check("init-store-foreign-{}-preserved-not-reversed".format(target),
                  refused(res, "foreign") and foreign_kept()
                  and txn_state(root, run) == "open")
            rec_err = None
            try:
                reconcile(root)
            except AdoptApplyError as exc:
                rec_err = str(exc)
            check("init-store-reconcile-preserves-foreign-{}".format(target),
                  rec_err is not None and "preserved" in rec_err and foreign_kept()
                  and txn_state(root, run) == "open")

        # THE FOREIGN-VIEW BINDING (codex round 3, finding 1): a row that binds the deferred
        # TODO.md view digest to FOREIGN bytes never authorizes their removal -- the reversal
        # accepts only attributable substrate publication evidence (the operation's own
        # recorded plan and its views-group intent), never the adoption row alone, so the
        # injected file is preserved and the reversal refuses with the transaction open.
        root = init_op._plain_repo(os.path.join(base, "foreignview"), env)
        run = rid()
        foreign_view = b"user data that the renderer never publishes\n"
        memf = [dict(m, digest="sha256:" + _sha256(foreign_view))
                if m["path"] == ".working/TODO.md" else dict(m) for m in members]

        def write_view_then_run(*a, **k):
            init_op._write(os.path.join(root, ".working", "TODO.md"), foreign_view)
            return real_run(*a, **k)

        init_op.run_init_operation = write_view_then_run
        try:
            res = dispatch(dict(row, members=memf), ctx(root, run_id=run))
        finally:
            init_op.run_init_operation = real_run
        check("init-store-foreign-view-binding-preserved",
              refused(res, "foreign")
              and init_op._read_or_none(os.path.join(root, ".working", "TODO.md"))
              == foreign_view and txn_state(root, run) == "open")

        # THE OCCUPIED MACHINE PATH COMPOSITION (codex round 2, spec 14.2: "After the archival
        # the destination is an ordinary managed path"): the shell archives the occupying
        # machine-store file preserve-first; the emptied PRE-EXISTING machine directory is then
        # admitted through the plan's own occupying row and init-store publishes with no manual
        # directory removal. On a reversal the pre-existing directory is preserved as found.
        occ_bytes = b"# a foreign manifest-shaped file\n"
        for fixture, mem in (("occupied", members), ("occupied2", wrongv)):
            root = init_op._plain_repo(os.path.join(base, fixture), env)
            plant(root, manifest_rel, occ_bytes)
            run = rid()
            run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
                manifest_rel, "sha256:" + _sha256(occ_bytes)))
            occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                           disposition="retire", occupying=True,
                           preservation=archive_rel(run, manifest_rel))
            before = snap(root)
            res = dispatch(dict(row, members=mem), ctx(root, run_id=run, sources=[occ_row]))
            if fixture == "occupied":
                check("init-store-after-occupying-archival-admitted", res.status == valid
                      and store.resolve_store(root).status == store.RESOLVED
                      and live_digest(root, manifest_rel) == scaffold[manifest_rel]
                      and live_digest(root, archive_rel(run, manifest_rel))
                      == "sha256:" + _sha256(occ_bytes))
            else:
                check("init-store-reversal-preserves-preexisting-machine-dir",
                      refused(res, "restoring NOT-ADOPTED") and snap(root) == before
                      and txn_state(root, run) == "rolled-back" and op_ids(root) == [])

        # ARCHIVAL EVIDENCE, NOT PLANNING (codex round 3, finding 2): an occupying row whose
        # archival never ran admits nothing -- the emptied machine directory is admitted only
        # with the preservation copy live, digest-matched, AND the archival transaction
        # COMMITTED in this adoption journal; here neither holds and nothing is written.
        root = init_op._plain_repo(os.path.join(base, "noarchive"), env)
        machine_dir = manifest_rel.rsplit("/", 1)[0]
        os.makedirs(os.path.join(root, machine_dir))
        os.chmod(os.path.join(root, store.WORKING_DIRNAME), 0o755)
        os.chmod(os.path.join(root, machine_dir), 0o755)
        run = rid()
        occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                       disposition="retire", occupying=True,
                       preservation=archive_rel(run, manifest_rel))
        before = snap(root)
        check("init-store-unarchived-occupying-row-refused",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                      "completed archival")
              and snap(root) == before and op_ids(root) == [])

        # THE COMMITTED-TRANSACTION HALF OF THE ARCHIVAL PROOF (claude round 4, F2): a LIVE
        # digest-matched preservation copy admits the emptied machine directory ONLY beside a
        # COMPLETE preserve-AND-remove transaction of this journal. P1: a hand-placed copy with
        # no archival transaction at all; P3: a REAL archival that ROLLED BACK, the copy then
        # hand-placed; P4: a COMMITTED preserve-only transaction that never removed the source.
        # Each refuses with nothing written; each goes green if one conjunct of
        # _init_store_archival_committed (or the whole call) is dropped.
        occ_digest = "sha256:" + _sha256(occ_bytes)
        root = init_op._plain_repo(os.path.join(base, "handcopy"), env)
        run = rid()
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(run, manifest_rel))
        os.makedirs(os.path.join(root, machine_dir))
        plant(root, archive_rel(run, manifest_rel), occ_bytes)
        os.chmod(os.path.join(root, store.WORKING_DIRNAME), 0o755)
        os.chmod(os.path.join(root, machine_dir), 0o755)
        before = snap(root)
        check("init-store-live-copy-without-archival-refused",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                      "completed archival")
              and snap(root) == before and op_ids(root) == [])

        root = init_op._plain_repo(os.path.join(base, "rbarchival"), env)
        plant(root, manifest_rel, occ_bytes)
        run = rid()
        real_staged_verify = _journal._verify_staged_digest

        def failing_inventory_for(txn_run):
            def failing(op, payload):
                if op["path"] == inventory_rel(txn_run):
                    raise _journal.JournalError("injected failure at the inventory publication")
                return real_staged_verify(op, payload)
            return failing

        rolled = None
        _journal._verify_staged_digest = failing_inventory_for(run)
        try:
            try:
                run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
                    manifest_rel, occ_digest))
            except AdoptApplyError as exc:
                rolled = str(exc)
        finally:
            _journal._verify_staged_digest = real_staged_verify
        os.unlink(os.path.join(root, manifest_rel))
        plant(root, archive_rel(run, manifest_rel), occ_bytes)
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(run, manifest_rel))
        before = snap(root)
        check("init-store-rolled-back-archival-refused",
              rolled is not None and "rolled back to the prestate" in rolled
              and refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                          "completed archival")
              and snap(root) == before and op_ids(root) == [])

        root = init_op._plain_repo(os.path.join(base, "presonly"), env)
        plant(root, manifest_rel, occ_bytes)
        run = rid()
        run_adopt_transaction(root, run, lambda ops: ops.preserve(manifest_rel, occ_digest))
        os.unlink(os.path.join(root, manifest_rel))
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(run, manifest_rel))
        before = snap(root)
        check("init-store-preserve-only-transaction-refused",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])),
                      "completed archival")
              and snap(root) == before and op_ids(root) == [])

        # THE ADMITTED-DIRECTORY MODE PRE-CHECK (claude round 3, R3-5): a wrong-mode machine
        # directory (a umask-002 adopter) refuses BEFORE the run's intent opens -- nothing
        # recorded -- and the SAME run id then succeeds once the mode is corrected, proving
        # the id was never burnt by the refusal.
        root = init_op._plain_repo(os.path.join(base, "mode775"), env)
        plant(root, manifest_rel, occ_bytes)
        run = rid()
        run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
            manifest_rel, "sha256:" + _sha256(occ_bytes)))
        occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                       disposition="retire", occupying=True,
                       preservation=archive_rel(run, manifest_rel))
        os.chmod(os.path.join(root, machine_dir), 0o775)
        check("init-store-wrong-mode-admitted-dir-refused-pre-intent",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])), "holds mode")
              and op_ids(root) == [])
        # THE FULL-S_IMODE PRE-CHECK (codex round 4, finding 2): a setgid 02755 machine
        # directory is NOT the planned 0755 under the same full stat.S_IMODE comparison the
        # substrate makes, so it refuses BEFORE the intent too (observe_inventory's 0o777 mask
        # would have read it as 755); the run id is still unburnt, proven by the 0755 rerun.
        os.chmod(os.path.join(root, machine_dir), 0o2755)
        check("init-store-setgid-admitted-dir-refused-pre-intent",
              refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])), "holds mode 2755")
              and op_ids(root) == [])
        os.chmod(os.path.join(root, machine_dir), 0o755)
        res = dispatch(row, ctx(root, run_id=run, sources=[occ_row]))
        check("init-store-unburnt-run-id-reruns-after-mode-fix", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # THE `.working` FULL-MODE PRE-CHECK (claude round 5, N2): with admitted files the
        # substrate records `.working`'s observed mode MASKED to 0o777 and re-compares the
        # FULL stat.S_IMODE against that record under its mutex (so a special bit can never
        # pass), and refuses a group- or world-writable mode; the SAME two facts are
        # pre-checked before the run's intent opens, so each wrong `.working` mode refuses
        # with no operation record and the SAME run id reruns once the mode is corrected --
        # never a burnt id.
        root = init_op._plain_repo(os.path.join(base, "wmode"), env)
        plant(root, manifest_rel, occ_bytes)
        run = rid()
        run_adopt_transaction(root, run, lambda ops: ops.archive_occupying(
            manifest_rel, "sha256:" + _sha256(occ_bytes)))
        occ_row = dict(path=manifest_rel, digest="sha256:" + _sha256(occ_bytes),
                       disposition="retire", occupying=True,
                       preservation=archive_rel(run, manifest_rel))
        os.chmod(os.path.join(root, machine_dir), 0o755)
        for wmode, why in ((0o2755, "holds mode 2755"), (0o1755, "holds mode 1755"),
                           (0o775, "group- or world-writable")):
            os.chmod(os.path.join(root, store.WORKING_DIRNAME), wmode)
            check("init-store-working-mode-{:o}-refused-pre-intent".format(wmode),
                  refused(dispatch(row, ctx(root, run_id=run, sources=[occ_row])), why)
                  and op_ids(root) == [])
        os.chmod(os.path.join(root, store.WORKING_DIRNAME), 0o755)
        res = dispatch(row, ctx(root, run_id=run, sources=[occ_row]))
        check("init-store-working-unburnt-run-id-reruns-after-mode-fix", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        # THE PRIOR REVERSED RUN'S COMMITTED ARTEFACTS (claude round 4, F1; the orchestrator
        # ruling): spec 14.1 keeps the reversed run id burnt, and a FRESH run continues the
        # occupied-machine composition by admitting the PRIOR run's committed artefacts -- the
        # prior bundle VERIFIES, every artefact was created by a COMPLETE transaction of this
        # journal, the prior init-store transaction is terminal ROLLED-BACK, and the machine
        # directory is admitted on the prior run's COMMITTED archival. Both reversal shapes:
        # the postcondition refusal, and SIGKILL inside the publication plus reconcile().
        for fixture, kill in (("prior", None), ("prior2", "source:2")):
            root = init_op._plain_repo(os.path.join(base, fixture), env)
            plant(root, manifest_rel, occ_bytes)
            r1 = rid()
            run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
                manifest_rel, occ_digest))
            occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                           occupying=True, preservation=archive_rel(r1, manifest_rel))
            if kill is None:
                reversed_r1 = refused(dispatch(dict(row, members=wrongv),
                                               ctx(root, run_id=r1, sources=[occ_row])),
                                      "restoring NOT-ADOPTED")
                need_recover = False
            else:
                reversed_r1 = _init_store_child(root, env, kill, row,
                                                ctx(root, run_id=r1, sources=[occ_row])) != 0
                reconcile(root)
                need_recover = True
            burnt = refused(dispatch(row, ctx(root, run_id=r1, sources=[occ_row],
                                              recover=need_recover)), "was reversed")
            r2 = rid()
            res = dispatch(row, ctx(root, run_id=r2, recover=need_recover))
            check("init-store-prior-committed-artefacts-admitted-{}".format(
                      "reversal" if kill is None else "kill"),
                  reversed_r1 and burnt and res.status == valid
                  and store.resolve_store(root).status == store.RESOLVED
                  and txn_state(root, r2) == "complete"
                  and live_digest(root, archive_rel(r1, manifest_rel)) == occ_digest
                  and live_digest(root, manifest_rel) == scaffold[manifest_rel])

        # THE MOVE-PAYLOAD CONTINUATION (codex round 5, finding 1): a prior reversed run whose
        # COMMITTED transaction also created a default Move destination -- retained evidence
        # under the SAME _evidence_eligible domains the composer derives inventory rows from --
        # is admitted whole by the fresh run; an admission re-spelled to the bundle and archive
        # homes alone strands this bundle and goes red here.
        root = init_op._plain_repo(os.path.join(base, "priormove"), env)
        plant(root, manifest_rel, occ_bytes)
        moved_rel = store.moved_dest("prior-note.md")
        moved_bytes = b"a prior run's committed Move payload" + b"\x0a"
        r1 = rid()

        def compose_move(ops):
            ops.archive_occupying(manifest_rel, occ_digest)
            ops.create(moved_rel, moved_bytes)

        run_adopt_transaction(root, r1, compose_move)
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(r1, manifest_rel))
        reversed_r1 = refused(dispatch(dict(row, members=wrongv),
                                       ctx(root, run_id=r1, sources=[occ_row])),
                              "restoring NOT-ADOPTED")
        burnt = refused(dispatch(row, ctx(root, run_id=r1, sources=[occ_row])), "was reversed")
        res = dispatch(row, ctx(root, run_id=rid()))
        check("init-store-prior-move-payload-admitted-after-reversal",
              reversed_r1 and burnt and res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and live_digest(root, moved_rel) == "sha256:" + _sha256(moved_bytes)
              and live_digest(root, archive_rel(r1, manifest_rel)) == occ_digest
              and live_digest(root, manifest_rel) == scaffold[manifest_rel])

        # The negatives stay foreign and refuse with nothing written: a prior bundle that no
        # longer verifies (a tampered archive copy), and a prior archival that ROLLED BACK with
        # the copy and a verifying inventory hand-placed afterwards (neither a committed
        # transaction nor a rolled-back prior init-store transaction proves them).
        root = init_op._plain_repo(os.path.join(base, "priorbad"), env)
        plant(root, manifest_rel, occ_bytes)
        r1 = rid()
        run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
            manifest_rel, occ_digest))
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(r1, manifest_rel))
        tampered = refused(dispatch(dict(row, members=wrongv),
                                    ctx(root, run_id=r1, sources=[occ_row])),
                           "restoring NOT-ADOPTED")
        with open(os.path.join(root, archive_rel(r1, manifest_rel)), "wb") as fh:
            fh.write(b"tampered copy" + b"\x0a")
        before = snap(root)
        check("init-store-prior-unverified-bundle-refused",
              tampered and refused(dispatch(row, ctx(root, run_id=rid())),
                                   "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        root = init_op._plain_repo(os.path.join(base, "priorrb"), env)
        plant(root, manifest_rel, occ_bytes)
        r1 = rid()
        rolled = None
        _journal._verify_staged_digest = failing_inventory_for(r1)
        try:
            try:
                run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
                    manifest_rel, occ_digest))
            except AdoptApplyError as exc:
                rolled = str(exc)
        finally:
            _journal._verify_staged_digest = real_staged_verify
        os.unlink(os.path.join(root, manifest_rel))
        plant(root, archive_rel(r1, manifest_rel), occ_bytes)
        plant(root, inventory_rel(r1), emit_inventory(
            r1, [inventory_row(archive_rel(r1, manifest_rel), occ_bytes)]))
        before = snap(root)
        check("init-store-prior-rolled-back-archival-refused",
              rolled is not None and "rolled back to the prestate" in rolled
              and refused(dispatch(row, ctx(root, run_id=rid())),
                          "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        # THE TERMINAL-ROLLED-BACK CONJUNCT PINNED (claude round 5, N1a): a prior run's REAL
        # committed archival whose init-store transaction NEVER RAN proves nothing -- its id
        # was never burnt the one sanctioned way -- so the fresh run refuses with the
        # snapshot unchanged and no operation record; dropping the terminal-ROLLED-BACK gate
        # (and its absent-transaction sibling) admits it and goes red here.
        root = init_op._plain_repo(os.path.join(base, "priornoinit"), env)
        plant(root, manifest_rel, occ_bytes)
        r1 = rid()
        run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
            manifest_rel, occ_digest))
        before = snap(root)
        check("init-store-prior-without-init-store-txn-refused",
              refused(dispatch(row, ctx(root, run_id=rid())), "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        # THE COMMITTED-CREATION CONJUNCT PINNED (claude round 5, N1b): an extra artefact
        # hand-planted in the reversed prior run's archive home and LISTED by a rewritten,
        # self-consistent inventory still refuses -- no COMPLETE transaction of this journal
        # created it (nor the rewritten inventory's own bytes) -- with the snapshot unchanged
        # and no operation record; dropping the committed-creation equality admits it and
        # goes red here.
        root = init_op._plain_repo(os.path.join(base, "priorextra"), env)
        plant(root, manifest_rel, occ_bytes)
        r1 = rid()
        run_adopt_transaction(root, r1, lambda ops: ops.archive_occupying(
            manifest_rel, occ_digest))
        occ_row = dict(path=manifest_rel, digest=occ_digest, disposition="retire",
                       occupying=True, preservation=archive_rel(r1, manifest_rel))
        reversed_r1 = refused(dispatch(dict(row, members=wrongv),
                                       ctx(root, run_id=r1, sources=[occ_row])),
                              "restoring NOT-ADOPTED")
        planted = b"hand-planted, never committed" + b"\x0a"
        plant(root, archive_rel(r1, "planted.txt"), planted)
        plant(root, inventory_rel(r1), emit_inventory(r1, [
            inventory_row(archive_rel(r1, manifest_rel), occ_bytes),
            inventory_row(archive_rel(r1, "planted.txt"), planted)]))
        before = snap(root)
        check("init-store-prior-extralisted-artefact-refused",
              reversed_r1
              and refused(dispatch(row, ctx(root, run_id=rid())), "undispositioned foreign")
              and snap(root) == before and op_ids(root) == [])

        # CRASH AT EVERY POINT (round 2): the transaction mkdir, the INTENT, a member's staging
        # write (machine-dir and root-level), between a member's link and its staging unlink,
        # after a full member publication, and inside the recovery cleanup (at the record discard
        # and mid-discard). Every point recovers to a fresh publication over a tree judged WHOLE:
        # byte-identical outside the published store, no staging leftover anywhere, exactly one
        # (fresh) operation record.
        def stage_leftovers(r):
            found = []
            for dirpath, _dirs, files in os.walk(r):
                if os.path.relpath(dirpath, r).split(os.sep)[0] == ".git":
                    continue
                found += [n for n in files if init_op._STAGE_MARKER in n]
            return found

        def outside_store(r):
            return {rel: v for rel, v in snap(r).items()
                    if rel.split(os.sep)[0] != store.WORKING_DIRNAME
                    and rel != init_op.CHANGELOG_RELPATH and rel != store.POINTER_REL}

        # the pre-INTENT mkdir crash: nothing was durable beyond the directory, so the SAME run
        # id simply reruns (its frame-less debris is cleared), never a burnt run id.
        root = init_op._plain_repo(os.path.join(base, "killmkdir"), env)
        before = snap(root)
        run = rid()
        rc = _init_store_child(root, env, "txnmkdir", row, ctx(root, run_id=run))
        reconcile(root)
        res = dispatch(row, ctx(root, run_id=run))
        check("init-store-crash-at-txn-mkdir-reruns-same-run", rc != 0 and res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and txn_state(root, run) == "complete"
              and stage_leftovers(root) == [] and outside_store(root) == before)

        version_rel = "{}/version.toml".format(manifest_rel.rsplit("/", 1)[0])
        # phasestage:* SIGKILLs INSIDE a phase-record publication (claude round 3, R3-1: the
        # staging leftover classifies the record CANNOT-EVALUATE, and the reversal must still
        # bind init.toml through the operation's own plan record); scrubdir:* SIGKILLs the
        # RECOVERY dispatch right after a reversed directory's removal, BEFORE the record
        # discard (claude round 3, R3-2: under the pinned record-LAST order the still-recorded
        # operation lets the next dispatch finish the scrub; a record-FIRST scrub strands the
        # tree with no recovery discriminator).
        two_stage = ("cleanup", "scrubdir")
        machine_base = manifest_rel.rsplit("/", 1)[0].rsplit("/", 1)[1]
        for i, kill in enumerate(("intent", "stage:" + version_rel,
                                  "linked:" + init_op.CHANGELOG_RELPATH,
                                  "stage:" + init_op.CHANGELOG_RELPATH, "source:2",
                                  "phasestage:sources-ready", "phasestage:views-ready",
                                  "cleanup:entry", "cleanup:partial",
                                  "scrubdir:" + machine_base)):
            root = init_op._plain_repo(os.path.join(base, "kill{}".format(i)), env)
            before = snap(root)
            rc = _init_store_child(root, env,
                                   "source:2" if kill.partition(":")[0] in two_stage else kill,
                                   row, ctx(root, run_id=rid()))
            reconcile(root)
            killed = rc != 0
            if kill.partition(":")[0] in two_stage:
                killed = killed and _init_store_child(root, env, kill, row,
                                                      ctx(root, run_id=rid(),
                                                          recover=True)) != 0
                reconcile(root)
            res = dispatch(row, ctx(root, run_id=rid(), recover=True))
            check("init-store-crash-{}-recovers-whole-tree".format(
                      kill.replace(":", "-").replace("/", "-").replace(".", "")),
                  killed and res.status == valid
                  and store.resolve_store(root).status == store.RESOLVED
                  and stage_leftovers(root) == [] and len(op_ids(root)) == 1
                  and outside_store(root) == before)

        # THE SCRUB'S DURABILITY ORDER (codex round 4, finding 1; claude round 4, F3), pinned
        # by a MOCKED SYSCALL-ORDER TRACE over one real scrub: EVERY cleanup unlink and rmdir
        # in the pre-discard trace -- each one, not just some (claude round 5, N3: an
        # any-form pairing stayed green when the lease-unlink fsync alone was dropped) -- is
        # immediately followed by an fsync of the SAME parent directory (matched by the
        # descriptor's device and inode, so a dropped parent fsync goes red), a cleanup target
        # already ABSENT on entry to a RESTARTED scrub has its nearest surviving ancestor
        # fsynced, and every one of these barriers precedes the record discard.
        import _opf_init_substrate

        def dir_ident(fd):
            if not isinstance(fd, int):
                return None
            try:
                fst = os.fstat(fd)
            except OSError:
                return None
            return (fst.st_dev, fst.st_ino)

        def path_ident(dpath):
            dfd = os.open(dpath, os.O_RDONLY | os.O_DIRECTORY)
            try:
                return dir_ident(dfd)
            finally:
                os.close(dfd)

        def traced_scrub(r):
            root_fd = _open_product_root(r)
            intent = None
            try:
                jr_fd = _journal.open_journal_root_fd(root_fd, JOURNAL_REL)
                try:
                    for txn_dir in _journal._journal_txn_dirs(jr_fd, _journal_root(r)):
                        if not txn_dir.name.endswith("." + INIT_STORE_PHASE):
                            continue
                        if _journal.classify_state(jr_fd, txn_dir) != "rolled-back":
                            continue
                        frames, _torn, _good = _journal.read_frames(jr_fd, txn_dir)
                        intent = _journal._first(frames, _journal.F_INTENT)
                        break
                finally:
                    _journal._close_fd_quietly(jr_fd)
                header = intent["header"]
                trace = []
                real_unlink, real_rmdir, real_fsync = os.unlink, os.rmdir, os.fsync
                real_settle = _opf_init_substrate.settle_operation
                real_discard = _opf_init_substrate.discard_reversed_operation

                def t_unlink(name, *a, **k):
                    trace.append(("unlink", dir_ident(k.get("dir_fd"))))
                    return real_unlink(name, *a, **k)

                def t_rmdir(name, *a, **k):
                    trace.append(("rmdir", dir_ident(k.get("dir_fd"))))
                    return real_rmdir(name, *a, **k)

                def t_fsync(fd):
                    trace.append(("fsync", dir_ident(fd)))
                    return real_fsync(fd)

                def t_settle(holder, op_id):
                    trace.append(("discard", None))
                    return real_settle(holder, op_id)

                def t_discard(holder, op_id):
                    trace.append(("discard", None))
                    return real_discard(holder, op_id)

                os.unlink, os.rmdir, os.fsync = t_unlink, t_rmdir, t_fsync
                os.supports_dir_fd.add(t_unlink)
                os.supports_dir_fd.add(t_rmdir)
                _opf_init_substrate.settle_operation = t_settle
                _opf_init_substrate.discard_reversed_operation = t_discard
                try:
                    _init_store_scrub(r, root_fd, header["init_operation_id"],
                                      members=[op["path"] for op in intent.get("ops", [])
                                               if isinstance(op, dict)
                                               and isinstance(op.get("path"), str)],
                                      working_created=header.get("working_created") is True,
                                      machine_created=header.get("machine_created")
                                      is not False,
                                      recover=True)
                finally:
                    os.unlink, os.rmdir, os.fsync = real_unlink, real_rmdir, real_fsync
                    os.supports_dir_fd.discard(t_unlink)
                    os.supports_dir_fd.discard(t_rmdir)
                    _opf_init_substrate.settle_operation = real_settle
                    _opf_init_substrate.discard_reversed_operation = real_discard
            finally:
                store._close_fd_exc_safe(root_fd)
            return trace

        def before_discard(trace):
            cut = trace.index(("discard", None)) if ("discard", None) in trace else -1
            return trace[:cut] if cut >= 0 else None

        def labeled(head):
            return [e for e in head if e[1] is not None]

        def paired(seq, kind, ident):
            return any(e == (kind, ident) and i + 1 < len(seq)
                       and seq[i + 1] == ("fsync", ident)
                       for i, e in enumerate(seq))

        def every_paired(head):
            # Over the RAW pre-discard trace: each unlink/rmdir is IMMEDIATELY followed by
            # the fsync of its own parent, so no removal -- the lease's included -- can ride
            # on a sibling pair in the same directory (claude round 5, N3).
            return all(e[0] not in ("unlink", "rmdir")
                       or (i + 1 < len(head) and head[i + 1] == ("fsync", e[1]))
                       for i, e in enumerate(head))

        root = init_op._plain_repo(os.path.join(base, "scrubtrace"), env)
        rc1 = _init_store_child(root, env, "stage:" + init_op.CHANGELOG_RELPATH, row,
                                ctx(root, run_id=rid()))
        reconcile(root)
        root_id = path_ident(root)
        working_id = path_ident(os.path.join(root, store.WORKING_DIRNAME))
        machine_id = path_ident(os.path.join(root, machine_dir))
        # The scrub's OWN lease branch is exercised by planting a leftover lease AFTER the
        # mutex acquisition: the oplock's recovery deletes a verified stale lease itself
        # (fsynced) during acquire, so the organic fixture never reaches the scrub's lease
        # unlink and its fsync would otherwise go unpinned (claude round 5, N3).
        import _opf_oplock
        real_acquire = _opf_oplock.acquire_init_operation

        def plant_lease_after_acquire(*a, **k):
            holder = real_acquire(*a, **k)
            init_op._write(os.path.join(root, init_op.LEASE_RELPATH), b"leftover lease\x0a")
            return holder

        _opf_oplock.acquire_init_operation = plant_lease_after_acquire
        try:
            head = before_discard(traced_scrub(root))
        finally:
            _opf_oplock.acquire_init_operation = real_acquire
        seq = labeled(head) if head is not None else []
        check("init-store-scrub-order-removal-then-parent-fsync-then-discard",
              rc1 != 0 and head is not None
              and every_paired(head)                        # EVERY removal, lease included
              and paired(seq, "unlink", root_id)            # the staging leftover
              and paired(seq, "unlink", machine_id)         # the planted leftover lease
              and paired(seq, "rmdir", working_id)          # the created machine directory
              and paired(seq, "rmdir", root_id))            # the created .working
        res = dispatch(row, ctx(root, run_id=rid(), recover=True))
        check("init-store-scrub-order-tree-recovers", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED)

        root = init_op._plain_repo(os.path.join(base, "scrubtrace2"), env)
        rc1 = _init_store_child(root, env, "source:2", row, ctx(root, run_id=rid()))
        reconcile(root)
        rc2 = _init_store_child(root, env, "scrubdir:" + machine_base, row,
                                ctx(root, run_id=rid(), recover=True))
        reconcile(root)
        working_id = path_ident(os.path.join(root, store.WORKING_DIRNAME))
        gone = not os.path.lexists(os.path.join(root, machine_dir))
        head = before_discard(traced_scrub(root))
        seq = labeled(head) if head is not None else []
        check("init-store-scrub-restart-absent-target-fsync-before-discard",
              rc1 != 0 and rc2 != 0 and gone and head is not None and every_paired(head)
              and ("fsync", working_id) in seq)             # the removed machine directory's
        res = dispatch(row, ctx(root, run_id=rid(), recover=True))   # surviving parent
        check("init-store-scrub-restart-recovers", res.status == valid
              and store.resolve_store(root).status == store.RESOLVED
              and stage_leftovers(root) == [] and len(op_ids(root)) == 1)


def _finish_ops_self_test(check):
    """Slice 5: plant-governance, record-adoption and render-views. The two composing ops run through the
    journaled shell over throwaway NOT-ADOPTED fixtures; render-views runs over a resolvable store built from
    the init builders' own bytes, with the inert observations a git-aware caller would gather injected, and
    its expected bytes come from an independent twin store the render engine writes directly. Each refusal
    is attributed by a reason keyword and judged with the tree unchanged."""
    import contextlib
    import io
    import tempfile
    from unittest import mock
    import _opf_check
    import _opf_init
    import _opf_observe
    import _opf_pack_manifest as pack_manifest
    import _opf_views
    valid, invalid, cannot = store.VALID, store.INVALID, store.CANNOT_EVALUATE
    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    rid = mint_run_id(now, "0123456789abcdef")

    def refused(res, needle):
        return res.status == cannot and any(needle in f for f in res.findings)

    def snapshot(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob("*"))
                if p.is_file() and not p.is_symlink() and not str(p.relative_to(root)).startswith(".aiqt/")}

    def composer(rows, **context):
        def compose(ops):
            for row in rows:
                verdict = dispatch(row, dict(context, ops=ops))
                if verdict.status != valid:
                    raise AdoptApplyError("; ".join(verdict.findings))
        return compose

    def seal(plan_doc):
        """`plan_doc` sealed as the planner seals it: its plan_digest is the digest of the canonical emission
        of the plan without that key, so frozen_plan re-proves it from its own bytes."""
        body = dict((k, plan_doc[k]) for k in plan_doc if k != "plan_digest")
        return dict(body, plan_digest="sha256:" + _sha256(emit_checked(body).encode("utf-8")))

    def run(root, rows, phase=None, staged=None, **context):
        """(transaction name, None) or (None, refusal text): one adoption transaction over `rows`. A base
        transaction stages the context's own plan first, as apply stages the approved plan the handlers
        read, and every transaction carries that plan's digest; `staged` (bytes, digest) stages another
        plan instead, for the mismatched-plan vector only."""
        plan_bytes, digest = staged or (emit_checked(context["plan"]).encode("utf-8"),
                                        context["plan"]["plan_digest"])
        compose = composer(rows, **context)
        if phase is None:
            rows_compose = compose

            def compose(ops):
                ops.create(plan_rel(rid), plan_bytes)
                rows_compose(ops)
        try:
            return run_adopt_transaction(root, rid, compose, phase, digest), None
        except AdoptApplyError as exc:
            return None, str(exc)

    # The release: one governance member, its frozen pack inventory (a manifest in the shipped grammar whose
    # TREE recomputes from its source rows) and the agreed ROOT the plan binds and root.txt reports.
    member = b"# Governance adapter\nRecords are written through opf record, never by hand.\n"
    source = "governance/AGENTS.md"

    def manifest_bytes(data, tree=None):
        rows = [dict(path=source, bytes=len(data), sha256=_sha256(data))]
        q = chr(34)
        lines = ["format-version = 1", "release-version = " + q + "1.3.0" + q, "genesis = true",
                 "tree-sha256 = " + q + (tree or pack_manifest.compute_tree(rows)) + q,
                 "", "[[sources]]", "path = " + q + source + q, "bytes = " + str(len(data)),
                 "sha256 = " + q + _sha256(data) + q,
                 "", "[[artifacts]]", "artifact-id = " + q + "file:" + source + q, "path = " + q + source + q,
                 "kind = " + q + "file" + q, "sha256 = " + q + _sha256(data) + q]
        return ("\n".join(lines) + "\n").encode("utf-8")

    def release(manifest, plan=None, anchor=None):
        """(sealed plan, pack) agreeing on the ROOT of `manifest` unless `anchor` names another one."""
        agreed = "sha256:" + pack_manifest.compute_root(manifest)
        plan = dict(plan or base_plan)
        plan["release"] = dict(plan["release"], manifest_sha256=agreed, anchor_sha256=anchor or agreed)
        pack = dict(manifest=manifest, root=(agreed + "\n").encode("ascii"), members=dict([(source, member)]))
        return seal(plan), pack

    base_plan = schema.canonical_plan()
    manifest = manifest_bytes(member)
    plan, pack = release(manifest)
    plant = dict(op="plant-governance", path="AGENTS.md", content_digest="sha256:" + _sha256(member),
                 source_member=source)
    # ONE approved plan throughout: the sealed plan the handlers read is the plan the base transaction
    # stages in the bundle and whose digest the inventory's [adoption] identity names, and the receipt core
    # and its embedded approval bind that same digest.
    receipt = schema.canonical_receipt_core()
    receipt["release"] = dict(receipt["release"], manifest_sha256=plan["release"]["manifest_sha256"])
    receipt["plan_digest"] = plan["plan_digest"]
    receipt["approval"] = dict(receipt["approval"], plan_digest=plan["plan_digest"])
    core, event = adoption_record(rid, plan, receipt, now)
    record = minted_record_row(rid, core)
    ctx = dict(plan=plan, pack=pack, receipt=receipt, now=now)
    check("finish-fixture-pack-manifest-valid", pack_manifest.parse_manifest(manifest)[1].status == valid)
    check("finish-fixture-plan-sealed", frozen_plan(emit_checked(plan).encode("utf-8")) == plan
          and plan["plan_digest"] != base_plan["plan_digest"])

    def bound_digests(tree):
        """The plan digests a committed bundle names: the staged plan.toml's own, the inventory's [adoption]
        identity, the receipt core's and its embedded approval's (None where a piece is absent)."""
        def doc(rel):
            return tomllib.loads(tree[rel].decode("utf-8")) if rel in tree else dict()
        rc = doc(receipt_rel(rid))
        return (doc(plan_rel(rid)).get("plan_digest"), doc(inventory_rel(rid)).get("adoption", {}).get("plan_digest"),
                rc.get("plan_digest"), rc.get("approval", {}).get("plan_digest"))

    def one_plan(tree, plan_doc):
        """True when the bundle stages exactly `plan_doc` and plan.toml, inventory, receipt and approval all
        name its digest: the identity the bundle verifier alone does not compare (it proves the inventory
        against plan.toml, never the receipt; the doctor owns the approval binding)."""
        return (tree.get(plan_rel(rid)) == emit_checked(plan_doc).encode("utf-8")
                and bound_digests(tree) == (plan_doc["plan_digest"],) * 4)
    check("finish-fixture-rows-validate", plan["run_id"] == rid and schema.validate_op(plant).status == valid
          and schema.validate_op(record).status == valid)
    check("finish-receipt-in-own-bundle", receipt_rel(rid) == evidence_home_rel(rid) + "/receipt.toml"
          and genesis_event_rel(rid) == evidence_home_rel(rid) + "/event-0001.toml")
    check("record-row-mint-is-the-executable-row",
          record == dict(op="record-adoption", receipt_path=receipt_rel(rid),
                         receipt_core_digest="sha256:" + _sha256(core))
          and schema.validate_op(record).status == valid)
    # M3: the genesis event's digest recomputes from its own canonical bytes without the event_digest key,
    # through the named helper AND an independent inline recomputation, so a minted digest that is not the
    # canonical-bytes digest is a red here and a refusal inside adoption_record itself.
    genesis_doc = tomllib.loads(event.decode("utf-8"))
    genesis_body = dict((k, genesis_doc[k]) for k in genesis_doc if k != "event_digest")
    check("genesis-event-digest-recomputes",
          genesis_doc["event_digest"] == "sha256:" + _sha256(emit_checked(genesis_body).encode("utf-8"))
          and genesis_doc["event_digest"] == event_digest(genesis_doc))
    # The explicit stage-driver rule behind the mint: the executable record-adoption row is never a plan
    # operand. validate_plan itself refuses a plan whose record-adoption row names the run's bundle receipt
    # (the receipt creation is a reserved-control-area effect, spec 14.2), so the minted row can never be
    # smuggled through an approval; the plan binds the receipt through the approval digests the core must
    # embed (validate_receipt_core plus _record_adoption's bindings).
    minted_ops = [dict(record) if r.get("op") == "record-adoption" else r for r in base_plan["ops"]]
    minted_plan = dict(base_plan, ops=minted_ops,
                       effects=schema.derive_effects(minted_ops, base_plan["sources"],
                                                     schema.store_manifest(base_plan["store"])))
    minted_graded = schema.validate_plan(minted_plan)
    check("record-minted-row-never-plannable",
          schema.validate_plan(base_plan).status == valid and minted_graded.status == invalid
          and any("control area" in f and receipt_rel(rid) in f for f in minted_graded.findings))
    # and on homes 2 even validate_op refuses the minted row's receipt operand, so the homes-2 activation
    # slice must route the receipt through the capability-bound journal API (or land its own explicit rule)
    # before this build's SUPPORTED_HOMES rises -- the fact is pinned so it cannot drift silently.
    check("record-minted-row-homes2-operand-refused",
          schema.validate_op(record, homes=1).status == valid
          and schema.validate_op(record, homes=2).status == invalid)

    # The two composing ops over a NOT-ADOPTED root: the verified member is planted create-only, and the
    # receipt core and its genesis event land in the run's own bundle, claimed by the derived inventory.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        txn, why = run(root, [plant, record], **ctx)
        after = snapshot(root)
        check("finish-plant-and-record-commit", txn == rid and why is None)
        check("plant-governance-planted-verified-bytes", after.get("AGENTS.md") == member)
        check("record-adoption-receipt-and-genesis-written",
              after.get(receipt_rel(rid)) == core and after.get(genesis_event_rel(rid)) == event)
        listed = []
        if inventory_rel(rid) in after:
            listed = [r["path"] for r in tomllib.loads(after[inventory_rel(rid)].decode("utf-8"))["file"]]
        check("record-adoption-bundle-verifies", verify_bundle(root, rid).status == valid
              and receipt_rel(rid) in listed and genesis_event_rel(rid) in listed)
        check("record-adoption-one-approved-plan", one_plan(after, plan))
        disk_core, disk_event = after.get(receipt_rel(rid), b""), after.get(genesis_event_rel(rid), b"x=")
        check("record-adoption-reparsed-core-and-chain-valid",
              schema.validate_receipt_core(tomllib.loads(disk_core.decode("utf-8"))).status == valid
              and schema.validate_event_chain([tomllib.loads(disk_event.decode("utf-8"))],
                                              "sha256:" + _sha256(disk_core)).status == valid)
        # one genesis per run: a later phase of the same run recording again refuses on the occupied receipt.
        before = snapshot(root)
        txn, why = run(root, [record], phase="completion", **ctx)
        check("record-adoption-second-genesis-later-phase-refused",
              txn is None and "occupied" in (why or "") and snapshot(root) == before)
    # the mismatched-plan vector: a bundle staging ANOTHER sealed plan (its inventory naming that plan) while
    # the receipt and approval bind the context plan still verifies, so only the one-plan equality catches it
    # (red if that equality is dropped or weakened to the verifier's verdict).
    stray_plan = seal(dict(format=PLAN_V2_FORMAT, run_id=rid))
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        txn, why = run(root, [plant, record], staged=(emit_checked(stray_plan).encode("utf-8"),
                                                      stray_plan["plan_digest"]), **ctx)
        after = snapshot(root)
        digests = bound_digests(after)
        check("record-adoption-mismatched-plan-detected",
              txn == rid and why is None and verify_bundle(root, rid).status == valid
              and digests[:2] == (stray_plan["plan_digest"],) * 2 and digests[2:] == (plan["plan_digest"],) * 2
              and not one_plan(after, plan) and not one_plan(after, stray_plan))
    # both destinations are observed before either is composed: an occupied genesis event beside an absent
    # receipt refuses with NOTHING composed, not with the receipt create already appended.
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        planted = root / genesis_event_rel(rid)
        planted.parent.mkdir(parents=True)
        planted.write_bytes(b"the adopter's own bytes\n")
        before, seen = snapshot(root), []

        def probe(ops):
            verdict = dispatch(dict(record), dict(ctx, ops=ops))
            seen.append((verdict, list(ops.ops), dict(ops.staged)))
            raise AdoptApplyError("probe")
        try:
            run_adopt_transaction(root, rid, probe)
        except AdoptApplyError:
            pass
        check("record-adoption-occupied-event-composes-nothing",
              len(seen) == 1 and refused(seen[0][0], "neither the receipt nor its genesis event is composed")
              and seen[0][1] == [] and seen[0][2] == {} and snapshot(root) == before)

    def refuses_untouched(label, rows, needle, **changes):
        with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
            root = Path(temp).resolve()
            txn, why = run(root, rows, **dict(ctx, **changes))
            check(label, txn is None and needle in (why or "") and snapshot(root) == dict())

    # The trust gate (b.5): each single mutation refuses with nothing written.
    forged = bytearray(member)
    forged[0] ^= 1
    refuses_untouched("plant-forged-member-refused", [plant], "forged",
                      pack=dict(pack, members=dict([(source, bytes(forged))])))
    altered = manifest.replace(b"1.3.0", b"1.3.1")
    refuses_untouched("plant-substituted-manifest-refused", [plant], "do not hash to the agreed ROOT",
                      pack=dict(pack, manifest=altered))
    other_root = ("sha256:" + "7" * 64 + "\n").encode("ascii")
    refuses_untouched("plant-other-root-txt-refused", [plant], "names a ROOT other",
                      pack=dict(pack, root=other_root))
    refuses_untouched("plant-disagreeing-anchor-refused", [plant], "no agreed ROOT",
                      plan=release(manifest, anchor="sha256:" + "7" * 64)[0])
    untree = manifest_bytes(member, tree="0" * 64)
    plan_t, pack_t = release(untree)
    refuses_untouched("plant-tree-not-recomputing-refused", [plant], "does not recompute",
                      plan=plan_t, pack=pack_t)
    refuses_untouched("plant-unbound-content-digest-refused",
                      [dict(plant, content_digest="sha256:" + "7" * 64)], "not the content_digest")
    refuses_untouched("plant-unknown-source-member-refused",
                      [dict(plant, source_member="governance/OTHER.md")], "not a source row")
    refuses_untouched("plant-store-tree-path-refused", [dict(plant, path=".working/AGENTS.md")], "store tree")
    # the approved release identity reconciles with the digest-agreed pack manifest: a plan naming
    # another version over the same agreed bytes is contradictory and refuses, nothing written.
    version_plan = dict(plan)
    version_plan["release"] = dict(plan["release"], version="9.9.9")
    refuses_untouched("plant-release-version-mismatch-refused", [plant],
                      "not the approved release version", plan=version_plan)
    # the product-root VERSION deliverable is a render-managed destination (spec 14.2), never a plant target.
    refuses_untouched("plant-version-deliverable-refused", [dict(plant, path="VERSION")],
                      "VERSION deliverable")
    with tempfile.TemporaryDirectory(prefix="opf-adopt-finish-") as temp:
        root = Path(temp).resolve()
        (root / "AGENTS.md").write_bytes(b"the adopter's own file\n")
        txn, why = run(root, [plant], **ctx)
        check("plant-occupied-destination-refused", txn is None and "occupied" in (why or "")
              and snapshot(root) == dict([("AGENTS.md", b"the adopter's own file\n")]))

    # The receipt (b.4): the shipped validator discriminates an approval attesting a different plan, the
    # receipt must bind THIS plan, and the shipped chain validator refuses a second genesis.
    stray = "sha256:" + "7" * 64
    wrong_approval = dict(receipt, approval=dict(receipt["approval"], plan_digest=stray))
    graded = schema.validate_receipt_core(wrong_approval)
    check("receipt-approval-digest-flip-invalid", graded.status == invalid
          and any("approval attests" in f for f in graded.findings))
    refuses_untouched("record-approval-digest-flip-refused", [record], "approval attests",
                      receipt=wrong_approval)
    other_plan = dict(receipt, plan_digest=stray, approval=dict(receipt["approval"], plan_digest=stray))
    check("receipt-other-plan-validates-alone", schema.validate_receipt_core(other_plan).status == valid)
    refuses_untouched("record-unbound-plan-digest-refused", [record], "plan_digest", receipt=other_plan)
    # each receipt binding beyond plan_digest is pinned by its own refusal: the run identities (both
    # legs), product, inventory digest, store root and release manifest.
    other_rid = mint_run_id(now, "fedcba9876543210")
    refuses_untouched("record-other-run-receipt-refused", [record], "different adoption runs",
                      receipt=dict(receipt, run_id=other_rid))
    refuses_untouched("record-other-run-plan-refused", [record], "different adoption runs",
                      plan=dict(plan, run_id=other_rid))
    refuses_untouched("record-unbound-product-refused", [record], "plan's product",
                      receipt=dict(receipt, product="opf"))
    refuses_untouched("record-unbound-inventory-digest-refused", [record], "inventory_digest",
                      receipt=dict(receipt, inventory_digest=stray,
                                   approval=dict(receipt["approval"], inventory_digest=stray)))
    refuses_untouched("record-unbound-store-root-refused", [record], "store_root",
                      receipt=dict(receipt, store_root="elsewhere"))
    refuses_untouched("record-unbound-release-manifest-refused", [record], "release.manifest_sha256",
                      receipt=dict(receipt, release=dict(receipt["release"], manifest_sha256=stray)))
    refuses_untouched("record-anchor-disagreement-refused", [record], "agreeing independent",
                      receipt=dict(receipt, release=dict(receipt["release"], anchor_agreement=False)))
    refuses_untouched("record-anchor-unobserved-refused", [record], "agreeing independent",
                      receipt=dict(receipt, release=dict(receipt["release"], independent_anchor_observed=False,
                                                         anchor_agreement=True)))
    refuses_untouched("record-unbound-core-digest-refused",
                      [dict(record, receipt_core_digest=stray)], "receipt_core_digest")
    refuses_untouched("record-receipt-outside-bundle-refused",
                      [dict(record, receipt_path=".working/toml/adoption.toml")], "bundle receipt")
    refuses_untouched("record-naive-now-refused", [record], "aware UTC", now=now.replace(tzinfo=None))
    refuses_untouched("record-second-genesis-same-transaction-refused", [record, record], "exactly one genesis")
    genesis = tomllib.loads(event.decode("utf-8"))
    second = dict(genesis, previous_event_digest=genesis["event_digest"], event_digest=stray)
    chained = schema.validate_event_chain([genesis, second], "sha256:" + _sha256(core))
    check("event-chain-second-genesis-invalid", chained.status == invalid
          and any("genesis" in f for f in chained.findings))
    with mock.patch.object(schema, "validate_event_chain", return_value=schema._invalid(["stub"])):
        refuses_untouched("record-consults-the-chain-validator", [record], "genesis outcome event is refused")
    for name in ("plant-governance", "record-adoption"):
        res = dispatch(dict(plant if name == "plant-governance" else record), dict(ctx))
        check("{}-without-transaction-refused".format(name), refused(res, "no ApplyOps"))

    # render-views composes into the journaled shell: the approved bytes land through the same adoption
    # transaction, preimage checks, reversal and journal lock as every other composing op (spec 14, 14.2).
    # The expected bytes come from a twin store the render engine writes directly, so the composed creates
    # are pinned byte-for-byte to the engine's own output.
    views = sorted(v["target"] for v in tomllib.loads(_opf_init.build_manifest())["views"].values())
    obs = dict(tracked="tracked", prior=_opf_observe._empty_prior())
    _this = sys.modules[__name__]

    def lease_stand_in():
        # The shell refuses a resolved store (no single-writer lease join yet, spec 5.7). This stand-in
        # admits the fixture's resolved store so the composed render transaction is exercised end to end:
        # exactly the seam the lease-join slice will occupy. The UNPATCHED refusal is pinned below
        # (render-views-resolved-store-refused-without-lease).
        return mock.patch.object(_this, "_store_posture_or_refuse", lambda product_root: None)

    def built_store(parent, name, changelog=True):
        root = Path(parent).resolve() / name
        machine = root / store.WORKING_DIRNAME / store.DEFAULT_MACHINE_SUBDIR
        machine.mkdir(parents=True)
        docs = dict([(store.MANIFEST_NAME, _opf_init.build_manifest()), ("counters.toml", _opf_init.build_counters()),
                     ("version.toml", _opf_init.build_version()), ("worklog.toml", _opf_init.build_worklog())])
        for type_name in _opf_init.INDEX_TYPES:
            docs[type_name + _opf_check.INDEX_SUFFIX] = _opf_init.build_index(type_name)
        for name_, text in docs.items():
            (machine / name_).write_text(text, encoding="utf-8")
        if changelog:
            (root / "CHANGELOG.md").write_bytes(b"# Changelog\n")
        return root

    with tempfile.TemporaryDirectory(prefix="opf-adopt-render-") as temp:
        oracle = built_store(temp, "oracle")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = _opf_views.render(["--root", str(oracle), "--write"], observations=obs)
        rendered = dict((v, (oracle / v).read_bytes()) for v in views if (oracle / v).is_file())
        check("render-oracle-twin-rendered", rc == _opf_views.EXIT_OK and sorted(rendered) == views)
        row = dict(op="render-views", store_root=".",
                   members=[dict(path=v, digest="sha256:" + _sha256(rendered.get(v, b""))) for v in views])
        check("render-row-validates", schema.validate_op(row).status == valid)

        def rctx(root, **changes):
            return dict(dict(product_root=str(root), plan=plan, observations=obs), **changes)

        # the real posture first: WITHOUT the lease join, the shell refuses the resolved store before the
        # handler composes anything, the same spec 5.7 posture plant-governance and record-adoption take.
        root = built_store(temp, "posture")
        before = snapshot(root)
        txn, why = run(root, [row], **rctx(root))
        check("render-views-resolved-store-refused-without-lease",
              txn is None and "lease" in (why or "") and snapshot(root) == before)

        # the committed render: ONE journaled adoption transaction composes the creates; the written views
        # are byte-identical to the engine twin's; the journal holds the completed transaction under a
        # freed lock; nothing else changed but the run's staged bundle plan and the transaction's own
        # derived inventory.
        root = built_store(temp, "apply")
        before = snapshot(root)
        with lease_stand_in():
            txn, why = run(root, [row], **rctx(root))
        after = snapshot(root)
        jr_fd = _journal.open_journal_root_from_path(root, JOURNAL_REL)
        try:
            committed = _journal.classify_state(jr_fd, _journal_root(root) / rid)
        finally:
            _journal._close_fd_quietly(jr_fd)
        check("render-views-writes-exactly-the-approved-bytes", txn == rid and why is None
              and all(after.get(v) == rendered.get(v) for v in views)
              and set(after) - set(before) == set(views) | set([plan_rel(rid), inventory_rel(rid)]))
        check("render-views-journaled-transaction-complete",
              committed == "complete" and _journal.read_lock_owner(_journal_root(root)) is None)

        # the reversal (spec 14): an injected failure at the transaction's final op rolls every composed
        # view back from its journaled (absent) preimage; the tree is the exact prestate.
        root = built_store(temp, "abort")
        before = snapshot(root)
        real_verify = _journal._verify_staged_digest

        def failing_inventory(op, payload):
            if op["path"] == inventory_rel(rid):
                raise _journal.JournalError("injected failure at the inventory publication")
            return real_verify(op, payload)

        with lease_stand_in(), mock.patch.object(_journal, "_verify_staged_digest", failing_inventory):
            txn, why = run(root, [row], **rctx(root))
        check("render-views-abort-restores-prestate",
              txn is None and "rolled back" in (why or "") and snapshot(root) == before)

        # preservation (spec 14.2): an adopter's live file at a declared view destination refuses the
        # whole transaction with the live bytes untouched -- never absorbed, deleted or overwritten.
        adopter = b"ADOPTER HAND-MAINTAINED CONTENT\n"
        root = built_store(temp, "occupied")
        (root / views[0]).write_bytes(adopter)
        before = snapshot(root)
        with lease_stand_in():
            txn, why = run(root, [row], **rctx(root))
        check("render-views-occupied-destination-preserved",
              txn is None and "are occupied; adopter content is never absorbed" in (why or "")
              and (root / views[0]).read_bytes() == adopter and snapshot(root) == before)

        def refuses_uncomposed(label, name, needle, rrow=row, changelog=True, **changes):
            root = built_store(temp, name, changelog)
            before = snapshot(root)
            with lease_stand_in():
                txn, why = run(root, [rrow], **dict(rctx(root), **changes))
            check(label, txn is None and needle in (why or "") and snapshot(root) == before)

        # the U6 source gate, now BEFORE anything is composed (an untracked observation, then none at all).
        refuses_uncomposed("render-views-source-gate-refuses-untracked", "untracked", "SOURCE integrity",
                           observations=dict(obs, tracked="untracked"))
        refuses_uncomposed("render-views-source-gate-refuses-without-observations", "noobs",
                           "SOURCE integrity", observations=None)
        bent = [dict(m, digest="sha256:" + "7" * 64) if m["path"] == views[0] else m for m in row["members"]]
        refuses_uncomposed("render-views-unbound-bytes-refused", "bent", "other than the row binds",
                           rrow=dict(row, members=bent))
        refuses_uncomposed("render-views-missing-member-refused", "short", "not exactly the declared view",
                           rrow=dict(row, members=row["members"][1:]))
        other_store = dict(plan, store=dict(plan["store"], machine_rel=".working/data"))
        refuses_uncomposed("render-views-other-store-refused", "other", "frozen default store",
                           plan=other_store)
        # an authored residual (an absent CHANGELOG.md) now refuses with NOTHING written, never after a
        # write the delegated engine would have left in place.
        refuses_uncomposed("render-views-authored-residual-refused", "residual", "C-CHANGELOG-GATES",
                           changelog=False)
        # a drifted root VERSION is an authored residual too: no declared view targets VERSION, so
        # C-VERSION-FILE is not render-views' own and refuses with nothing written.
        root = built_store(temp, "version")
        (root / "VERSION").write_bytes(b"9.9.9\n")
        before = snapshot(root)
        with lease_stand_in():
            txn, why = run(root, [row], **rctx(root))
        check("render-views-drifted-version-residual-refused",
              txn is None and "C-VERSION-FILE" in (why or "") and snapshot(root) == before)
        # a context product root other than the transaction's own root refuses by directory identity, so
        # composed views can never land in a tree the store checks never saw.
        refuses_uncomposed("render-views-foreign-product-root-refused", "foreign",
                           "transaction's own product root", product_root=str(oracle))
        # the frozen-default-store identity is pinned leg by leg: a non-default pointer source and a store
        # root other than the product root each refuse even when every other leg matches (crafted
        # resolutions; the default fixture cannot produce either shape).
        root = built_store(temp, "identity")
        genuine = store.resolve_store(root)

        def res_variant(status=genuine.status, **changes):
            fields = dict(store_root=genuine.store_root, machine_dir=genuine.machine_dir,
                          machine_rel=genuine.machine_rel, pointer_source=genuine.pointer_source,
                          target=genuine.target, product_root=genuine.product_root)
            fields.update(changes)
            return store.Resolution(status, genuine.detail, **fields)

        for label, fake in (("render-views-pointer-source-refused", res_variant(pointer_source="committed")),
                            ("render-views-foreign-store-root-refused", res_variant(store_root=oracle)),
                            ("render-views-unresolved-status-refused",
                             res_variant(status=store.CANNOT_EVALUATE))):
            before = snapshot(root)
            with lease_stand_in(), mock.patch.object(store, "resolve_store", return_value=fake):
                txn, why = run(root, [row], **rctx(root))
            check(label, txn is None and "frozen default store" in (why or "") and snapshot(root) == before)

        bare = Path(temp).resolve() / "bare"
        bare.mkdir()
        txn, why = run(bare, [row], **rctx(bare))
        check("render-views-not-adopted-refused", txn is None and "must resolve" in (why or "")
              and snapshot(bare) == dict())
        # reconcile-first: a held adoption journal lock refuses the transaction before the handler runs
        # (never seized) -- the shell's own gate now guards render-views exactly as the other finish ops.
        root = built_store(temp, "locked")
        before = snapshot(root)
        root_fd = _open_product_root(root)
        try:
            _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        finally:
            store._close_fd_exc_safe(root_fd)
        _journal.acquire_lock(_journal_root(root), "opf-adopt-selftest-peer")
        try:
            with lease_stand_in():
                txn, why = run(root, [row], **rctx(root))
            check("render-views-held-journal-lock-refused",
                  txn is None and "never seized" in (why or "") and snapshot(root) == before)
        finally:
            _journal.release_lock(_journal_root(root))
    check("render-views-without-transaction-refused", refused(dispatch(row), "no ApplyOps"))


def main():
    args = sys.argv[1:]
    # the crash matrix's child entry, the house crash-child pattern (`_opf_init_operation --selftest-child`):
    # a self-test harness entry the self-test alone starts, never an adoption interface.
    if len(args) == 2 and args[0] == "--selftest-child":
        return _kill_injection_child(args[1])
    if args[:1] == ["--selftest-init-store-child"]:
        return _init_store_child_main(args[1:])
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_apply.py --self-test (a library module; the adoption verb is `opf adopt`)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
