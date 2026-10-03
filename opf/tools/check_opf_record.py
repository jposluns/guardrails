#!/usr/bin/env python3
"""OPF record-authoring gate (spec 8.8): `opf record` behaviour, and red-on-revert discriminators.

  check_opf_record.py --self-test                    the fixture suite (T1-T73)
  check_opf_record.py --self-test --red-on-revert    the same, plus each test's flip must turn it red

There is no live-adopter leg (this repository is not an OPFiles adopter), so the whole assurance rides the
self-test. Every fixture is a real store built beneath this gate's own mkdtemp root: `opf init`, a commit,
`opf render --write`, a commit, then per-case edits committed so the cleanliness gate sees a clean tree.
Each case runs on its own copy of that template; the root is removed in a finally.

  T1  a comment-bearing index, counters, or worklog refuses exit 2 with every byte untouched
      (flip: drop the byte-reproduction precondition)
  T2  create with links and refs round-trips and the published bytes are canonical; with a lossy emitter
      the verb's serializer (_emit_bytes) itself refuses, and the whole run refuses exit 2 with every byte
      untouched (flip: publish raw emit output instead of emit_checked; the independent postcondition
      still refuses the end-to-end run, so the flip turns red on the serializer's own refusal)
  T3  a plan that mutates one extra field of an existing record, or the initial status or requested title
      of the new record, or the requested summary of a new worklog entry, refuses exit 2, bytes untouched
      (flip: drop the allowed-delta postcondition)
  T4  two sequential creates claim BI-1 then BI-2 (and their own worklog entries WL-1, WL-2) with
      monotonic counters; a counters map missing an enabled namespace refuses (flip: drop the
      known-complete proof)
  T5  a kill at each journal step of create and of an assistant transition (three operands: counters,
      index, worklog) and of a maintainer done-with-receipt (four operands: counters, backlog index, done
      index, worklog) leaves the killed run's lease, which refuses the next run before any recovery write;
      once the operator releases it, reconciliation leaves the operands exactly the prestate or exactly the
      poststate, the poststate iff the transaction is COMPLETE, and no killed run reports an id (flip: write
      counters outside the journaled transaction)
  T6  an assistant `transition BI done` lands done/proposed with zero receipts; an assistant
      done-with-receipt refuses before the store is resolved, and a maintainer `transition BI done`
      refuses, both with every byte untouched; a maintainer done-with-receipt (ratifying done/proposed, or
      from active) lands unqualified done with exactly one receipt_of receipt and its own worklog entry,
      doctor VALID once committed (flips: derive the bare status for an assistant; drop the
      maintainer-only check; drop the done-only-through-its-receipt guard)
  T7  a maintainer rejection without --reason, of a proposal that does not record its pre-proposal
      state, to another state, or by an assistant refuses with every byte untouched; with a reason it
      returns to the recorded pre-proposal state and records the reason (flips: the check sees a
      placeholder reason; guess the pre-proposal state)
  T8  two branches that each create from the same committed counters conflict on the store paths; a
      canonical union resolution with the duplicate BI-1 is doctor INVALID with a C-ID-SPACE finding
      (flip: disable check_unique_ids)
  T9  a held lease refuses exit 2 and is never seized; success is reported only after the lease release;
      a failed release reports the final gate's recorded outcome, doctor-VALID after a create and the
      accepted pending cannot-evaluate after an actor-dependent transition (flips: emit the success report
      before releasing the lease; the unconditional doctor-VALID release-failure text)
  T10 a store the final doctor grades not VALID exits 2 with scoped recovery text, the change left for
      review and the lease released (flip: skip the final doctor)
  T11 worklog-append claims the next WL id with no status qualifier; an id inside a released span refuses
      exit 2, bytes untouched (flip: drop the released-span check)
  T12 an interrupted journal while another live run holds the shared lease (taken through the upgrade
      verb) refuses exit 2 with every byte untouched and the peer's lease intact (flip: recover without
      the lease)
  T13 an interrupted journal whose operand was edited after the interruption, on the prestate side or the
      poststate side, refuses exit 2 naming the path, every byte untouched; once the edit is undone the
      next run reconciles (flip: drop the intervening-edit check)
  T14 create with an unknown or path-like --type refuses with the enabled-baseline-types message, not an
      operand read failure (flip: skip the --type check before the operand read)
  T15 a run whose journal lock release fails after COMPLETE still renders, runs doctor, and reports, and
      says the lock may be left; the next run reconciles the leftover lock and names the COMPLETE
      transaction without claiming render and doctor never ran (flip: the old never-ran outcome text)
  T16 the proposing transition writes the pre-proposal state into the record's own proposed_from field;
      a maintainer rejection restores exactly that recorded state and removes the field; a forged
      proposing worklog line (its FROM state rewritten canonically, committed with its views, doctor
      VALID) changes nothing in either direction, refused target and restored state alike; a proposal
      carrying no proposed_from (proposed outside the verb by a canonical hand edit) refuses with every
      byte untouched; a reject-and-re-propose cycle merged --no-ff or squash-merged into the mainline
      behaves exactly the same (flip: read the worklog lifecycle line instead of the record's field);
      and a current row the schema grades invalid, a proposed_from naming an illegal predecessor or a
      stray proposed_from on an unqualified status (each a doctor finding), is never trusted or
      overwritten, in three separate tests: the rejection, the ratifying done-with-receipt, and the
      proposing transition each refuse with every byte untouched (flip, applied to each of the three:
      trust the current row without validating it)
  T17 a planner mutation that changes only a value's TYPE (an extension count to true or 1.0, the index
      schema marker to true, the receipt counter to true) refuses exit 2 before publication with every
      byte untouched (flip: compare the delta with ordinary equality instead of the strict comparator)
  T18 a pending_decision's open -> decided carries its resolution bundle: a maintainer lands decided with
      the bundle (decided_by from --decided-by, not from --actor; decided_at the clock value), doctor
      VALID once committed; an assistant, and separately automation, lands decided/proposed with the
      bundle and proposed_from open, doctor VALID once committed; a maintainer ratification keeps the
      bundle unchanged; a maintainer rejection with --reason restores open with no bundle and no
      proposed_from (flips: the planner writes no bundle; the rejection keeps the bundle; the planner
      takes decided_by from --actor, which only the independent oracle refuses). Each option refusal is
      its own test with its own flip, every byte untouched: a decide without --decision and --decided-by
      refuses before its bundle is planned (flip: drop the requires half of the option guard, where the
      planner's own lookup of the absent option still fails closed); --decision without --decided-by
      refuses before the store is resolved (flip: drop the parser's given-together check); and the
      options on open -> withdrawn, and on the ratification of decided/proposed, each refuse in its own
      test (flip, applied to each of the two: drop the apply-only half of the option guard, under which
      the options are silently ignored)

  T19 a publication's journal transaction name is the homes record- grammar
      record-<YYYYMMDD>T<HHMMSS>Z-<hash16> (flip: mint the legacy dotted name); an interrupted
      transaction under the LEGACY dotted name an earlier build minted is still reconciled, at kill
      points on both sides of COMPLETE (flip: a recovery that enumerates only grammar-named
      transactions)
  T20 a legacy .aiqt/record/journal on a homes-2 store is refused by name, never recovered in place,
      with the .aiqt subtree byte-unchanged and no typed journal home created (flip: probe the
      generation as 1, under which the legacy journal is reconciled in place)
  T21 a direct homes-2 _publish lands ONE capability-bound transaction in
      .working/journals/record/journal with its projection at txn_record("record", <rid>), the operands
      rewritten, and nothing under .aiqt (flip: route homes 2 to the legacy journal root)
  T22 on a homes-2 store the verb still refuses at the claim seam (the record reservation is a separate
      later change), with every byte untouched and neither journal home created (flip: a reconcile that
      mkdirs the typed home)
  T23 a homes-2 publication killed at each journal step leaves the typed journal reconcilable: the next
      run reconciles it under the operation capability (the confirmed-dead gate clears the dead run's
      lease and active record), the operands end exactly at the prestate or exactly at the poststate,
      the poststate iff COMPLETE, and .aiqt is never touched (its flip runs inside the killed child)
  T5-flip FLIP_T5 stays a genuine discriminator against the current _publish signature: applied in a
      killed child it performs its unjournaled counters write and dies at the kill point, never at
      an earlier signature error
  T24 a manifest the journal-home probe cannot read leaves the generation unknowable, so a store
      carrying a legacy .aiqt/record/journal refuses fail-closed with the .aiqt subtree
      byte-unchanged, and a store carrying none keeps the homes-1 broken-manifest behaviour
      (flip: probe the failure as generation 1, under which the legacy journal is reconciled in
      place)
  T25 the homes-2 recovery plan is derived under the HELD operation capability: a peer publication
      attempted right after the operand check is refused by the capability, or its completed bytes
      survive reconciliation (flip: derive the plan before the acquisition, under which the peer's
      completed publication is rolled back and erased)
  T26 a homes-2 publication killed after its durable COMPLETE but before the terminal projection is
      reconciled by the next run: the confirmed-dead leftovers are cleared, the missing projection
      is published, and the operands keep the poststate (flip: a projection never reads as missing,
      under which that state is never reconciled)
  T27 the homes-1 success report carries no journal_rel key and its commit advice names the legacy
      journal home, byte-shape-identical to the pre-D build (flip: re-add the key)
  T28 spec 15 records that a no-follow existence probe of a former .aiqt/ location, used only to
      refuse, is not a read (flip: read the spec with the sentence removed)
  T29 the gate runs inside the OPF git lifecycle: a git launch that strips every GIT_* variable, as
      the operation capability's rev-parse does, still runs with the system-config pins, and every
      fixture git call carries the three no-maintenance pins (flip: launch git past the lifecycle's
      wrapper, keeping only the GIT_CONFIG_NOSYSTEM pin so the flip itself reads no system config)
  T30 a readable manifest declaring homes 2 that this tooling does not activate (no activation
      patches) refuses BEFORE any reconciliation: the legacy leftover lock, the .aiqt subtree and
      every other byte unchanged (flip: probe the generation alone, under which the legacy journal
      is reconciled in place)
  T31 a homes-2 run killed while holding the capability with no journal work left for recovery
      (before the journal home existed, a nothing-opened transaction, or COMPLETE with its
      projection) is reconciled by the next run: the confirmed-dead lease and active record are
      cleared, that run refuses naming it, and ordinary acquisition succeeds again; a LIVE holder's
      capability is refused and never seized (flip: a trigger that sees neither capability record)
  T32 a homes-2 run killed inside its capability release, after the lease leg is removed and before
      the active record's (no journal home yet, or COMPLETE with its projection), leaves the active
      record ALONE; the next run reclaims it through the confirmed-dead gate and refuses naming it,
      and ordinary acquisition succeeds again (flip: a trigger that sees the lease alone, under which
      every later acquisition refuses the lone active record as stale)
  T33 the journal state is re-derived under the HELD capability: a capability leftover triggers
      recovery while no journal home exists, and a peer run killed mid-apply leaves an open
      transaction before the acquisition; that run rolls it back and names it, never reporting no
      journal work pending (flip: take the unheld absence as the plan)
  T34 the module residual list and the record guard disclose the unheld trigger read, with what
      recovery acts on and reports re-derived under the held capability, and the leftovers the
      substrate's recovery gate refuses, a sibling worktree's reclaimed from its owning checkout (flip:
      read the texts with the disclosure removed)
  T35 a live holder that releases between the trigger read and the recovery acquisition leaves nothing
      cleared and nothing pending under the held capability: the run reports no reconciliation and
      continues to the plain homes-2 claim-seam refusal, operands untouched (flip: the acquisition
      reports a reclaim it did not make)
  T36 the journal home is reopened under the held capability: a home renamed aside and replaced by a
      peer's with an open transaction after the trigger read is planned from its current binding and
      rolled back (flip: plan through the trigger read's descriptor)
  T37 a homes-2 capability leftover of another operation (a killed `ingest` holder) is reclaimed and
      attributed to the operation its record names, never to opf record (flip: attribute every reclaim
      to record)
  T38 a recovery acquisition that removes a staging leftover and then refuses names the removal and
      claims only that no record operand, journal or projection was written; a contended one says it
      removed nothing (flip: the staging removal goes unreported)
  T39 a recovery acquisition that deletes a dead holder's records and then fails names both deletes
      and the holder's recorded operation (flip: the deletes go unreported)
  T40 a refusal under the held recovery capability (an intervening operand edit) names the records
      the acquisition already removed beside its journal-and-operands wording (flip: as T39)
  T41 each residual list states exactly when another run is needed, marks the refused-leftover list
      not exhaustive, names a refusal's removals and the homes-2 owning checkout, and the held plan
      reopens the journal home only when present; a homes-1 record run never reclaims a dead
      capability holder, and a live holder that releases normally lets the next run continue (flip:
      read the texts with those statements removed)
  T42 a recovery acquisition whose lease publication fails after the active record's published names
      its own unlinks (that publication's retired staging name and the unwind's removal of the new
      active record), never "removed nothing" (flips: the staging retirement, or the unwind's removal,
      goes unreported)
  T43 a direct homes-2 publication whose acquisition removes a staging leftover and then refuses names
      the removal, never a blanket "nothing written" (flips: the staging removal goes unreported, or
      the refusal keeps the blanket wording)
  T44 a forked-continuation refusal of the recovery acquisition names the dead holder's records the
      acquisition removed before the fork (flip: the refusal carries no report)
  T45 an acquisition error carrying no removal report is worded as unknown, never as "removed nothing"
      (flip: a missing report reads as nothing removed)
  T46 each residual list states the rule deciding what is left for a later trigger, with its cases
      kept as examples marked NOT exhaustive, and describes staging-leftover cleanup as it is;
      acquire_operation's docstring names which errors carry the reports; a validation raise carries
      none, and a multiply-linked staging name is removed (flip: read the texts with those statements
      removed)
  T47 the no-pending reclaimed outcome names the grammar exclusion and speaks for the journal's commit,
      never for the operands' current bytes (flips: either sentence reverted)
  T48 the homes-1 journal-prepare refusal says preparation may have created the journal directories
      (flip: the unqualified "nothing written")
  T49 the record guard's introduction says capability recovery may remove a dead holder's records
      even with no journal work pending (flip: read the text with that sentence removed)
  T50 a homes-1 leftover journal lock over a COMPLETE transaction whose operand was restored to its
      prestate keeps the restored bytes and speaks for the journal's commit, never certifying the
      publication as present in the working tree (flip: the reviewed head's present-in-the-tree text)
  T51 the homes-1 journal-lock refusals (a held lock, a failed lock acquisition) after journal
      preparation created the directories say so, never "nothing written" (flip: the unqualified text)
  T52 the governing rule and a direct homes-2 publication refused before its transaction opened
      promise no reconciliation of work never left: the rule scopes the leftovers to what a run left,
      the refusal says any journal work left is for the next trigger, none when it failed before its
      transaction opened, and the next run reconciles nothing (flips: the reviewed head's refusal
      text; the reviewed head's rule)
  T53 a projection path holding an entry that is not the projection reads as existence only: the
      reclaimed outcome and the guard introduction never say the terminal transaction carries its
      projection (flips: the reviewed head's outcome; its introduction)
  T54 this header describes every test in TESTS and never claims T46 names every case (flip: the
      reviewed head's T46 line)
  T55 a homes-1 journal lock release that unlinks the lock and then fails says the lock may be left,
      never that it is left in place, and the next run reconciles nothing (flip: the reviewed head's
      left-in-place text)
  T56 a homes-1 run killed after its preimage capture, before INTENT, leaves its lock over a
      nothing-opened transaction; the leftover-lock outcome says no transaction was open, never that
      every transaction was already terminal (flip: the reviewed head's head clause)
  T57 a homes-1 publication failing after INTENT retains its lock and says the transaction is left
      for the next run's reconciliation as far as each step succeeds, never that the next run
      reconciles it; an intervening edit then refuses that reconciliation (flip: the reviewed head's
      promise)
  T58 a holder that releases between the trigger read and the recovery acquisition, with a staging
      leftover that acquisition removes, refuses once naming the removal and reclaiming no record,
      the re-run then proceeding, and the residual lists and the recovery docstring state that
      reporting (flip: the silent continue of the fix-9 head)
  T59 a homes-1 recovery whose lease acquisition fails after creating the lease says only that no
      operand, journal entry or journal lock was written, beside the write guard's left-lease report,
      never "Nothing was written" (flip: the reviewed head's unscoped sentence)
  T60 a failed lease or single-writer-claim release says the record may be left in place, scoped to
      what the release verifiably did (an absent or replaced lease is refused as not this run's own,
      and an unlinked record's directory fsync can fail after the unlink), never that it is LEFT in
      place (flip: the left-in-place text)
  T61 a journal lock acquisition failing after its O_EXCL create refuses saying the created lock may
      be left in place for the next run's reconciliation, never through the cli's cannot-evaluate
      backstop with no mention of it (flip: the refusal without the disclosure)
  T62 a success-report emission that fails after the single-writer claim's release succeeded is not
      reported as a lease-release failure: the refusal says the release succeeded and only the
      report's output failed, the transaction stays COMPLETE and the lease absent, and the next run
      records normally (flip: the fix-10 head's one except spanning the release and the emission)
  T63 T29's temporary global config is removed even when its write fails after creating the file,
      the write running inside the cleanup region (flip: the fix-10 head's write before it)
  T64 a diagnostic print that itself fails never displaces the governing failure: a lease-release
      failure (an error or an interrupt) and an emission failure each still govern the exit, and a
      recovery refusal survives a lease-release failure of either kind (flips: the fix-11 head's
      unprotected _conclude prints; its recovery-lease release handler)
  T65 a SIGINT delivered by a line trace immediately before the _conclude call still releases the
      single-writer claim exactly once, with the interrupt governing the exit: the release mark is
      set inside _conclude, never by the caller before the call (flip: a release mark that reads as
      already set, the fix-11 head's caller-side premature mark)
  T66 a homes-1 journal-lock release that raises an interrupt while the publication failed and its
      transaction reads rolled-back leaves the rolled-back refusal governing the exit, the release
      failure surfaced beside it (flip: the reviewed head's narrow release handler, which the
      interrupt passes through)
  T67 the record CLI invoked inside an embedding caller's except block, with the journal-lock
      release raising a non-ordinary error on the clean exit, fails as the run's own failure (exit
      2, no success report), exactly as outside the handler: the cleanup reads the run's own
      recorded failure, never sys.exc_info() (flip: the fix-13 head's cleanup, whose ambient
      sys.exc_info() read takes the caller's handled exception for a refusal in flight and reports
      success over the swallowed release failure)
  T68 the single-writer release mark is copied before the lease acquisition, so an ordinary
      allocation failure at that copy can never land between the acquisition and the protected
      region: a trap that fails the copy only once the lease is held never fires and the run
      records (flip: the fix-13 head's ordering, whose post-acquisition copy fails and strands the
      just-acquired lease)
  T69 the homes-1 publication's journal root descriptor is closed exactly once on every exit: a
      non-ordinary clean-exit journal-lock release failure inside an embedding caller's except block,
      and an allocation failure at the lock token, each leave no journal descriptor open, the original
      failure governing the exit (flip: the fix-14 head's _publish, whose token allocation precedes its
      cleanup region and whose release handler's re-raise skips the trailing close)
  T70 an acquisition whose lease publication fails, and whose removal-report tagging then fails with
      a MemoryError, still unwinds in full: no descriptor it adopted survives, it leaves no capability
      record, and the anchor is free, so the next acquisition succeeds; untrapped, the original
      OpLockError propagates carrying its removal reports (flip: the fix-15 head's _acquire_body,
      whose failure handler tags the error before any release)
  T71 the module residual list states once, beside the asynchronous-interrupt disclosure, that an
      interpreter allocation failure may leave a lease, a capability record, a journal lock or a
      descriptor for the next run's recovery (flip: the statement removed)
  T72 a decision that lands unqualified decided supersedes its chain's current resolution in the same act:
      a maintainer decides PD-2 with --supersedes PD-1, doctor VALID, and DECISIONS lists PD-2 effective
      and PD-1 superseded; the maintainer's ratification of an assistant's decided/proposed PD-3 with
      --supersedes PD-2 makes PD-3 the one effective resolution, doctor VALID (flip: the planner writes no
      link, which only the independent oracle refuses). Each supersession refusal is its own test with its
      own flip, every byte untouched: a --supersedes value that is not a record id refuses before the
      store is resolved (flip: drop the parser's record-id check); --supersedes on an assistant decide (a
      decided/proposed landing) refuses (flip: drop the landing check); a target still open refuses (flip:
      drop the target-decided check); a target the schema grades invalid, and separately a target seated
      in the index as another type, each refuses (flip, applied to each of the two: drop only the
      target-validation call); a target that is no record, and separately an existing backlog item in
      another namespace, each refuses (flip, applied to each of the two: append the link without locating
      or checking the target, since the lookup cannot be dropped alone); a second successor for a
      resolution already superseded refuses (flip: drop the chain-head check); and a target whose own
      chain leads back to the record refuses (flip: drop the cycle check). Under each of the landing,
      target-validation, and unchecked-target flips the link publishes and the post-publication render's
      source gate then refuses on a doctor finding: C-DECISION-CHAINS with no current resolution
      (landing), the target's own pre-existing finding (invalid or wrong-type target), or C-LINKS (a
      dangling link, or a supersession of another type); under the target-decided flip the link publishes
      doctor VALID; each exits with bytes changed, so the untouched assertion turns red. The fork and
      cycle tests assert that their own check refuses before the chain rule runs, because under their
      flips the chain rule still refuses with every byte untouched (two current resolutions for the fork,
      none for the cycle). Every landing at unqualified decided must leave its chain with exactly one
      current resolution (the doctor's own rule, recomputed over the planned index), each case its own
      test, every byte untouched: QA1 Case A, a --supersedes naming a chain head whose joined chain would
      still have none (the same decide without the link then lands doctor VALID); QA1 Case B, a record
      already superseded by an open successor, decided with --supersedes; and that record decided without
      the link (flip, applied to each of the three: drop the chain rule, under which the decide publishes
      and the source gate refuses on C-DECISION-CHAINS with no current resolution). The chain rule
      reads what the doctor reads, the archive included, each case its own test after a release whose
      worklog rotates to archive/2026 with one decision: QA3 reproduction A, a decide whose chain's one
      current resolution was rotated, lands doctor VALID; QA3 reproduction B, the decide of a record an
      archived withdrawn decision supersedes, refuses with every byte untouched; and an archived index
      that does not parse refuses a decide with every byte untouched, never skipped (flip, applied to
      each of the three: restore the active-only read, under which A refuses, B publishes and the
      source gate refuses on C-DECISION-CHAINS with no current resolution, and the third publishes and
      the final doctor cannot evaluate the archive). The supersedes target checks read the same record
      set, each case its own test: superseding a decided chain head rotated to the archive lands doctor
      VALID, and a second successor for a resolution an archived decision already supersedes refuses at
      the chain-head check, before the chain rule runs, with every byte untouched, and an archived
      withdrawn target refuses as not an unqualified decided resolution, with every byte untouched (flip,
      applied to each of the three: restore the active-only target read, under which the archived head
      is no record, the fork reaches the chain rule, and the withdrawn target refuses as no record); and
      a supersede that would close a cycle through an archived chain member refuses with the cycle
      check's own message, every byte untouched, in its own test (flip: restore the active-only read for
      the cycle check alone, under which the chain rule refuses instead with its own message). The
      decisions register's other two types are driven end to end:
      a maintainer files a maintainer_decision linking exemplifies PP-1, doctor VALID and listed with its link, and an
      assistant distils a preference_pattern to active/proposed that the maintainer ratifies and then
      retires, doctor VALID (flip: the planner drops the requested links, which only the independent
      oracle refuses); the same ruling filed by an assistant refuses with every byte untouched in its own
      test (flip: the planner's record validation replaced by a pass-through, so the ruling publishes and
      that gate reports its actor finding)
  T73 a contribution's send carries its delivery bundle: a maintainer creates CN-1; --channel on
      proposed -> withdrawn, and --receipt-ref on proposed -> sent, each refuse with every byte untouched;
      an assistant sends CN-1 to sent/proposed with delivery {channel, ref, sent_at} (sent_at the clock
      value); a maintainer rejection with --reason restores proposed with no delivery and no proposed_from;
      the assistant sends again and the maintainer's ratification keeps the bundle; the re-send is CN-2,
      created linking supersedes CN-1, and CN-1 moves to superseded keeping its bundle; a maintainer sends
      CN-2, an assistant acknowledges it with --receipt-ref to acknowledged/proposed (receipt_ref, and
      receipted_at the clock value), a maintainer rejection back to sent removes the two receipt keys, and
      the maintainer then acknowledges with --receipt-ref; doctor VALID after each commit (flips, each
      refused by the planner's own record validation, so the labelled step assertion turns red: the
      planner writes no delivery bundle, so the send is refused; the rejection of sent/proposed keeps
      the bundle; the rejection of acknowledged/proposed keeps the receipt keys)

Exit convention: 0 every assertion passes; 1 an assertion fails; 2 the harness cannot evaluate (git absent
or unusable, temporary storage unusable, or any unexpected harness fault), never a clean skip.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_record.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import copy
import datetime
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal as journal          # noqa: E402
import _opf_changelog as opf_changelog  # noqa: E402
import _opf_check as opf_check      # noqa: E402
import _opf_emit as emit            # noqa: E402
import _opf_oplock                  # noqa: E402
import _opf_record as record        # noqa: E402
import _opf_release as opf_release  # noqa: E402
import _opf_schema as schema        # noqa: E402
import _opf_write_guard as guard    # noqa: E402
import _optlevel                    # noqa: E402
import opf                          # noqa: E402

TOOLS = Path(__file__).resolve().parent
MACH = ".working/toml"
COUNTERS = MACH + "/counters.toml"
BI_INDEX = MACH + "/backlog_item.index.toml"
PD_INDEX = MACH + "/pending_decision.index.toml"
MD_INDEX = MACH + "/maintainer_decision.index.toml"
PP_INDEX = MACH + "/preference_pattern.index.toml"
DECISIONS_VIEW = ".working/DECISIONS.md"
DN_INDEX = MACH + "/done.index.toml"
WORKLOG = MACH + "/worklog.toml"
VERSION = MACH + "/version.toml"
LEASE = MACH + "/lease.toml"
RECORDED_EVENT = '"event": "recorded"'
CREATE = ["create", "--type", "backlog_item", "--title", "an item", "--actor", "assistant:gate"]
APPEND = ["worklog-append", "--kind", "added", "--summary", "a fact", "--actor", "assistant:gate"]
# No automatic gc or maintenance may detach from, or outlive, a fixture git launch.
GIT_NO_MAINTENANCE = ("-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false")


def kill_points(n):
    """The journal engine's injection hooks, in order, over one transaction of n operands."""
    points = ["after-lock"] + ["after-preimage-{}".format(i) for i in range(n)]
    points += ["after-preimages", "torn:INTENT", "after-publish-INTENT"]
    for i in range(n):
        points += ["torn-payload:{}".format(i), "after-apply-{}".format(i)]
    return tuple(points + ["torn:COMPLETE", "after-publish-COMPLETE"])


class Harness(Exception):
    """A harness fault: the gate cannot evaluate (exit 2), never an assertion verdict."""


class Env:
    """The scrubbed environment every git and opf call in this gate runs under."""

    def __init__(self, base):
        home = base / "home"
        home.mkdir()
        self.vars = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "LC_ALL": "C",
                     "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "gate",
                     "GIT_AUTHOR_EMAIL": "gate@example.invalid", "GIT_COMMITTER_NAME": "gate",
                     "GIT_COMMITTER_EMAIL": "gate@example.invalid"}

    # Spliced into EVERY fixture git command: no DETACHED auto-gc/auto-maintenance may outlive a
    # commit and keep repacking/pruning .git/objects while Fixtures.case copytrees this repository
    # (F-367: the loose objects and their fan-out directories vanish mid-copy, ENOENT). gc.auto=0
    # disables auto-gc, maintenance.auto=false keeps commit from spawning `git maintenance run
    # --auto` at all, and gc.autoDetach=false is defence in depth: a gc --auto that still runs
    # stays foreground, inside run_git's wait.
    NO_AUTO_MAINTENANCE = ("-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                           "-c", "maintenance.auto=false")

    def run_git(self, root, *args):
        return subprocess.run(["git", "-C", str(root), "-c", "init.defaultBranch=main"]
                              + list(self.NO_AUTO_MAINTENANCE) + list(args),
                              capture_output=True, text=True, timeout=120, env=self.vars)

    def git(self, root, *args):
        proc = self.run_git(root, *args)
        if proc.returncode != 0:
            raise Harness("fixture git {} failed: {}".format(args, proc.stderr.strip()))
        return proc.stdout


def assert_no_auto_maintenance(env, base):
    """DETERMINISTIC guard on the run_git maintenance pins (F-367): a traced fixture commit must
    spawn NO maintenance or gc child. Without the pins, commit spawns the DETACHED `git
    maintenance run --auto` child; what an unpinned commit demonstrates here is that child
    LAUNCHING (this single-file seed stays under the automatic-work thresholds), and once those
    thresholds are met the child can repack or prune .git/objects after run_git returned, racing
    the copytree in Fixtures.case (ENOENT mid-copy). Runs before any fixture is built; a spawned
    child is a Harness fault (exit 2, cannot-evaluate), never a verdict."""
    probe = base / "maintenance-probe"
    probe.mkdir()
    trace = base / "maintenance-probe-trace.jsonl"
    env.git(probe, "init", "-q")
    (probe / "seed.txt").write_bytes(b"seed\n")
    env.git(probe, "add", "-A")
    with patch.dict(env.vars, dict(GIT_TRACE2_EVENT=str(trace))):
        env.git(probe, "commit", "-q", "-m", "probe")
    spawned = []
    for line in trace.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)   # one trace2 event per line; unparseable output is a Harness fault
        if event.get("event") == "child_start":
            argv = event.get("argv") or []
            if set(("maintenance", "gc")) & set(argv):
                spawned.append(argv)
    if spawned:
        raise Harness("a fixture commit spawned automatic maintenance %r: the run_git pins"
                      " (gc.auto=0, gc.autoDetach=false, maintenance.auto=false) are missing, so a"
                      " detached gc can prune .git/objects while a later case copytrees this"
                      " repository" % (spawned,))


def cli(env, argv):
    """Run `opf <argv>` in-process under the scrubbed environment: (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env.vars, clear=True), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = opf.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


def record_cli(env, root, args):
    return cli(env, ["record"] + list(args) + ["--root", str(root)])


def snapshot(root):
    """Every file and directory beneath root except .git, by content and mode."""
    result = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not (Path(directory) == Path(root) and d == ".git")]
        for name in sorted(dirs + files):
            path = Path(directory) / name
            st = path.lstat()
            rel = path.relative_to(root).as_posix()
            kind = ("file", path.read_bytes()) if stat.S_ISREG(st.st_mode) else (
                "dir",) if stat.S_ISDIR(st.st_mode) else ("other", stat.S_IFMT(st.st_mode))
            result[rel] = (stat.S_IMODE(st.st_mode), kind)
    return result


def read(root, rel):
    return (Path(root) / rel).read_bytes()


def model(root, rel):
    return tomllib.loads(read(root, rel).decode("utf-8"))


def write_commit(env, root, rel, data, message):
    (Path(root) / rel).write_bytes(data)
    env.git(root, "add", "--", rel)
    env.git(root, "commit", "-q", "-m", message)


def refused(result, needle):
    rc, out, err = result
    assert rc == 2, ("expected exit 2", rc, out[-800:], err[-800:])
    assert needle in err, ("refusal text", needle, err[-1200:])
    assert '"event": "recorded"' not in out, ("no success report on a refusal", out[-800:])


def recorded(result):
    rc, out, err = result
    assert rc == 0, ("expected exit 0", rc, out[-800:], err[-1600:])
    assert "left UNCOMMITTED in the working tree" in out, out[-800:]
    return out


class Fixtures:
    """A doctor-VALID template store, copied fresh for every case."""

    def __init__(self, base, env):
        self.base = base
        self.env = env
        self.count = 0
        self.template = base / "template"
        self.template.mkdir()
        env.git(self.template, "init", "-q")
        rc, out, err = cli(env, ["init", "--root", str(self.template)])
        if rc != 0:
            raise Harness("opf init on the template failed: " + err[-800:])
        env.git(self.template, "add", "-A")
        env.git(self.template, "commit", "-q", "-m", "init")
        rc, out, err = cli(env, ["render", "--root", str(self.template), "--write"])
        if rc != 0:
            raise Harness("opf render --write on the template failed: " + err[-800:])
        env.git(self.template, "add", "-A")
        env.git(self.template, "commit", "-q", "-m", "views")
        rc, out, err = cli(env, ["doctor", "--root", str(self.template)])
        if rc != 0:
            raise Harness("the template store is not doctor-VALID: " + out[-800:] + err[-800:])

    def case(self, name, source=None):
        """A fresh copy of the template, or of `source` (a case built from it)."""
        self.count += 1
        root = self.base / "{:03d}-{}".format(self.count, name)
        shutil.copytree(self.template if source is None else source, root, symlinks=True)
        return root

    def commit_all(self, root, message):
        self.env.git(root, "add", "-A", "--", ".working")
        self.env.git(root, "commit", "-q", "-m", message)


# --- T1: the byte-reproduction precondition ---------------------------------------------------------------

def t1_precondition(fx):
    env = fx.env
    for rel, args in ((BI_INDEX, CREATE), (COUNTERS, CREATE), (WORKLOG, APPEND)):
        root = fx.case("t1-" + Path(rel).stem)
        write_commit(env, root, rel, b"# a hand-written note\n" + read(root, rel), "hand edit")
        before = snapshot(root)
        refused(record_cli(env, root, args), "not in canonical new-document form")
        assert snapshot(root) == before, ("T1 bytes untouched", rel)


# --- T2: round-trip through emit_checked ------------------------------------------------------------------

# Link targets must resolve (a dangling link is a C-LINKS finding the final doctor grades), so the new
# record, BI-1 in a fresh store, links itself.
LINKS = (("relates", "BI-1"), ("follows", "BI-1"))
REFS = (("url", "https://example.invalid/a?b=c", "why"), ("path", "docs/x.md:12", "where"))


def _t2_args():
    args = list(CREATE)
    for rel, rid in LINKS:
        args += ["--link", "{}={}".format(rel, rid)]
    for kind, locator, note in REFS:
        args += ["--ref", kind, locator, note]
    return args


def _lossy_emit(original):
    def lossy(document):
        doc = copy.deepcopy(document)
        for row in doc.get("record", []) if isinstance(doc, dict) else []:
            if isinstance(row, dict):
                row.pop("refs", None)
        return original(doc)
    return lossy


def t2_round_trip(fx):
    env = fx.env
    root = fx.case("t2-round-trip")
    recorded(record_cli(env, root, _t2_args()))
    raw = read(root, BI_INDEX)
    parsed = tomllib.loads(raw.decode("utf-8"))
    rec = parsed["record"][-1]
    assert rec["links"] == [{"rel": r, "id": i} for r, i in LINKS], rec
    assert rec["refs"] == [{"kind": k, "locator": l, "note": n} for k, l, n in REFS], rec
    assert emit.emit_checked(parsed).encode("utf-8") == raw, "T2 published bytes are canonical"
    # A lossy emitter (it drops refs): the verb's one serializer must itself refuse it. This isolated
    # check is the emit_checked discriminator, because end to end the independent postcondition also
    # refuses a lossy emission, so the run below cannot tell which guard caught it.
    sample = dict(schema=1, record=[dict(id="BI-1", refs=[dict(kind=k, locator=l, note=n) for k, l, n in REFS])])
    with patch.object(emit, "emit", _lossy_emit(emit.emit)):
        try:
            record._emit_bytes(sample)
            serializer_refused = False
        except record.RecordError:
            serializer_refused = True
    assert serializer_refused, "T2 the serializer refuses a lossy emission"
    # End to end, a lossy emitter publishes nothing: exit 2 and every byte untouched, asserted on the
    # outcome, not on which guard's message names it.
    root = fx.case("t2-lossy")
    before = snapshot(root)
    with patch.object(emit, "emit", _lossy_emit(emit.emit)):
        rc, out, err = record_cli(env, root, _t2_args())
    assert snapshot(root) == before, "T2 a lossy emission writes nothing"
    assert rc == 2 and RECORDED_EVENT not in out, ("T2 a lossy emission is refused", rc, err[-800:])


def flip_t2():
    return patch.object(record, "_emit_bytes", lambda document: emit.emit(document).encode("utf-8"))


# --- T3: the allowed-delta postcondition -------------------------------------------------------------------

def t3_postcondition(fx):
    env = fx.env
    root = fx.case("t3-extra-field")
    recorded(record_cli(env, root, CREATE))
    fx.commit_all(root, "first record")
    original = record._PLANNERS["create"]

    def tampering(req, ctx, operand, now):
        plan = original(req, ctx, operand, now)
        operand.new_model["record"][0]["title"] = "silently rewritten"
        return plan

    before = snapshot(root)
    with patch.dict(record._PLANNERS, {"create": tampering}):
        result = record_cli(env, root, CREATE)
    refused(result, "postcondition failed")
    assert snapshot(root) == before, "T3 bytes untouched"
    # The newly appended row is checked against a delta derived from the request, never against the
    # planner's own row: tampering with it after planning must refuse too, in both planners.
    for label, sub, args, key, field, value in (
            ("initial-status", "create", CREATE, "record", "status", "active"),
            ("requested-title", "create", CREATE, "record", "title", "not the requested title"),
            ("worklog-summary", "worklog-append", APPEND, "entry", "summary", "not the requested summary")):
        root = fx.case("t3-appended-" + label)

        def appended_tampering(req, ctx, operand, now, planner=record._PLANNERS[sub], key=key, field=field,
                               value=value):
            plan = planner(req, ctx, operand, now)
            operand.new_model[key][-1][field] = value
            return plan

        before = snapshot(root)
        with patch.dict(record._PLANNERS, {sub: appended_tampering}):
            result = record_cli(env, root, args)
        refused(result, "postcondition failed")
        assert snapshot(root) == before, ("T3 bytes untouched", label)


# --- T4: allocation (sequential claims, the known-complete proof) -------------------------------------------

def t4_allocation(fx):
    env = fx.env
    root = fx.case("t4-sequential")
    prior = model(root, COUNTERS)["counters"]
    recorded(record_cli(env, root, CREATE))
    fx.commit_all(root, "BI-1")
    recorded(record_cli(env, root, CREATE))
    ids = [r["id"] for r in model(root, BI_INDEX)["record"]]
    after = model(root, COUNTERS)["counters"]
    assert ids == ["BI-1", "BI-2"], ids
    assert after["BI"] == prior["BI"] + 2 and after["WL"] == prior["WL"] + 2, (prior, after)
    assert schema.check_monotonic(prior, after) == [], (prior, after)
    assert {k: v for k, v in after.items() if k not in ("BI", "WL")} == {
        k: v for k, v in prior.items() if k not in ("BI", "WL")}
    entries = model(root, WORKLOG)["entry"]
    assert [(e["id"], e["detail"]) for e in entries] == [
        ("WL-1", "opf-record create BI-1 open"), ("WL-2", "opf-record create BI-2 open")], entries
    # A counters map missing an enabled namespace can never read as high-water 0.
    root = fx.case("t4-missing-namespace")
    counters = model(root, COUNTERS)
    del counters["counters"]["BI"]
    write_commit(env, root, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "drop BI counter")
    before = snapshot(root)
    refused(record_cli(env, root, CREATE), "cannot license an allocation")
    assert snapshot(root) == before, "T4 bytes untouched"


def flip_t4():
    def unproven(ctx, counters_model):
        high, _findings = schema.validate_counters(counters_model)
        return high, []
    return patch.object(record, "_counter_state", unproven)


# --- T5: crash durability (kill-injection at each journal step, then reconciliation) --------------------

_CHILD = """
import datetime, os, sys
from pathlib import Path
sys.path.insert(0, {tools!r})
import opf
assert opf._bootstrap() == 0
import _opf_record as record
record._clock_now = lambda: datetime.datetime(2026, 9, 27, 12, 0, 0, tzinfo=datetime.timezone.utc)
{flip}
sys.exit(opf.main(["record"] + sys.argv[2:] + ["--root", sys.argv[1]]))
"""

# The T5 flip, applied inside the killed child: counters.toml is written directly, OUTSIDE the journaled
# transaction, and only the record file is journaled. _t5_flip holds the prelude the children run with.
_t5_flip = [""]
FLIP_T5 = """
_journaled = record._publish
def _publish(ctx, plan, subcommand, cap=None):
    counters = plan.operands[0]
    (Path(ctx.res.store_root) / counters.rel).write_bytes(counters.new_raw)
    plan.operands = plan.operands[1:]
    return _journaled(ctx, plan, subcommand, cap=cap)
record._publish = _publish
"""


def child(env, root, args, kill=None, flip=""):
    script = _CHILD.format(tools=str(TOOLS), flip=flip)
    child_env = dict(env.vars)
    if kill is not None:
        child_env[journal.KILL_ENV] = kill
    return subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)] + list(args),
                          capture_output=True, text=True, timeout=180, env=child_env)


def journal_states(root, rel=None):
    rel = record.JOURNAL_REL if rel is None else rel
    jroot = Path(root) / rel
    if not jroot.is_dir():
        return {}
    jr_fd = journal.open_journal_root_from_path(str(root), rel)
    try:
        return {t.name: journal.classify_state(jr_fd, t) for t in journal._journal_txn_dirs(jr_fd, jroot)}
    finally:
        os.close(jr_fd)


def _t5_scenarios(fx):
    """(label, base store or None for the template, args, operands in journal order): create over the
    template; an assistant transition (three operands) and a maintainer done-with-receipt (four operands)
    over a committed store whose BI-1 is active."""
    active = fx.case("t5-base-active")
    with ticking():
        step(fx, active, ["create", "--type", "backlog_item", "--title", "k"] + MAINTAINER, "BI-1")
        step(fx, active, ["transition", "BI-1", "active"] + MAINTAINER, "BI-1 active")
    return (("create", None, CREATE, (COUNTERS, BI_INDEX, WORKLOG)),
            ("transition", active, ["transition", "BI-1", "done"] + ASSISTANT, (COUNTERS, BI_INDEX, WORKLOG)),
            ("done-with-receipt", active, ["done-with-receipt", "BI-1"] + MAINTAINER,
             (COUNTERS, BI_INDEX, DN_INDEX, WORKLOG)))


def t5_crash(fx):
    for label, base, args, operands in _t5_scenarios(fx):
        _t5_matrix(fx, label, base, args, operands)


def _t5_matrix(fx, label, base, args, operands):
    env = fx.env
    flip = _t5_flip[0]
    reference = fx.case("t5-{}-reference".format(label), base)
    proc = child(env, reference, args)
    assert proc.returncode == 0 and '"event": "recorded"' in proc.stdout, (label, proc.returncode,
                                                                           proc.stderr[-800:])
    post = {rel: read(reference, rel) for rel in operands}
    for hook in kill_points(len(operands)):
        point = (label, hook)     # named in every assertion below
        root = fx.case("t5-{}-{}".format(label, hook.replace(":", "-")), base)
        pre = {rel: read(root, rel) for rel in operands}
        assert all(pre[rel] != post[rel] for rel in operands), ("T5 the operation rewrites every operand", point)
        retained = set(journal_states(root))     # the base's own completed transactions, kept as evidence
        proc = child(env, root, args, kill=hook, flip=flip)
        assert proc.returncode == 137, ("T5 the child is killed at", point, proc.returncode, proc.stderr[-800:])
        assert '"event": "recorded"' not in proc.stdout, ("T5 a killed run reports no id", point)
        # The killed run leaves its lease, and a held lease refuses the next run before any recovery write.
        assert (Path(root) / LEASE).exists(), ("T5 the killed run leaves its lease", point)
        held = snapshot(root)
        refused(record_cli(env, root, CREATE), "runs only under the single-writer lease")
        assert snapshot(root) == held, ("T5 a held lease refuses before any recovery write", point)
        # The operator's explicit reconciliation step (no opf run is live): release the leftover lease.
        (Path(root) / LEASE).unlink()
        refused(record_cli(env, root, CREATE), "was reconciled")
        assert not (Path(root) / LEASE).exists(), ("T5 recovery releases the lease it took", point)
        states = journal_states(root)
        assert not any(s == "open" for s in states.values()), ("T5 every transaction terminal", point, states)
        assert not (Path(root) / record.JOURNAL_REL / "lock").exists(), ("T5 the journal lock released", point)
        now = {rel: read(root, rel) for rel in operands}
        complete = any(s == "complete" for name, s in states.items() if name not in retained)
        assert now == (post if complete else pre), (
            "T5 exactly the prestate or the poststate, the poststate iff COMPLETE", point, states)


# --- T9: the single-writer lease and release-before-success -------------------------------------------------

PEER_LEASE = (b'acquired_at = "2026-09-27T00:00:00Z"\nholder = "peer-runner"\noperation = "record"\n'
              b'schema = 1\n')


def t9_lease(fx):
    env = fx.env
    # A held lease (untracked, as a live peer leaves it) refuses and is never seized.
    root = fx.case("t9-held")
    (Path(root) / LEASE).write_bytes(PEER_LEASE)
    before = snapshot(root)
    refused(record_cli(env, root, CREATE), "never seized")
    assert snapshot(root) == before and read(root, LEASE) == PEER_LEASE, "T9 the peer lease survives"
    # On success the lease is released BEFORE the report: observe stdout at the moment of release.
    root = fx.case("t9-order")
    seen = []
    original = guard.release_lease

    def observing(root_fd, machine_rel, payload, verb):
        seen.append(sys.stdout.getvalue())
        return original(root_fd, machine_rel, payload, verb)

    with patch.object(guard, "release_lease", observing):
        out = recorded(record_cli(env, root, CREATE))
    assert len(seen) == 1 and "recorded" not in seen[0], ("T9 nothing reported before release", seen)
    assert "BI-1" in out and not (Path(root) / LEASE).exists(), "T9 reported after the lease is gone"
    # A failed release reports no success.
    root = fx.case("t9-release-fails")

    def failing(*_args):
        raise guard.WriteGuardError("synthetic release failure")

    with patch.object(guard, "release_lease", failing):
        result = record_cli(env, root, CREATE)
    refused(result, "synthetic release failure")
    assert "reached doctor-VALID, but the lease release failed" in result[2], result[2][-800:]
    # After a transition whose final gate accepted only the pending cannot-evaluate, a failed release reports
    # that recorded outcome, never doctor-VALID.
    root = fx.case("t9-release-fails-pending")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        with patch.object(guard, "release_lease", failing):
            result = record_cli(env, root, ["transition", "BI-1", "done"] + ASSISTANT)
    refused(result, "synthetic release failure")
    err = result[2]
    assert "carrying only the accepted pending cannot-evaluate of BI-1 active -> done/proposed" in err, err[-800:]
    assert "lease release failed" in err and "doctor-VALID" not in err, err[-800:]


def flip_t9():
    def report_then_release(ctx, lease, report, released):
        released[0] = True
        record._emit_success(report)
        record._release(ctx, lease)
    return patch.object(record, "_conclude", report_then_release)


def flip_t9_outcome():
    return patch.object(record, "_release_failure_text", lambda report: (
        "opf record: the store reached doctor-VALID, but the lease release failed; nothing is offered as "
        "recorded. Confirm no opf run is live (spec 5.7) and reconcile the lease before any further action."))


# --- T10: the final doctor -----------------------------------------------------------------------------------

def t10_final_doctor(fx):
    env = fx.env
    root = fx.case("t10-doctor-invalid")
    env.git(root, "rm", "-q", "--", "CHANGELOG.md")
    env.git(root, "commit", "-q", "-m", "drop the changelog")
    counters_before = read(root, COUNTERS)
    result = record_cli(env, root, CREATE)
    refused(result, "NOT doctor-VALID")
    assert "C-CHANGELOG-GATES" in result[2] and "restore --staged --worktree" in result[2], result[2][-1600:]
    assert "whole-tree restore" in result[2], "T10 the recovery text is scoped"
    assert read(root, COUNTERS) != counters_before, "T10 the published change is left for review"
    assert not (Path(root) / LEASE).exists(), "T10 the lease is released on the failure path"


def flip_t10():
    return patch.object(record, "_final_gate", lambda root, transition=None: [])


# --- T11: worklog-append and the released span -----------------------------------------------------------------

def t11_worklog(fx):
    env = fx.env
    root = fx.case("t11-tail")
    recorded(record_cli(env, root, APPEND))
    entries = model(root, WORKLOG)["entry"]
    assert [e["id"] for e in entries] == ["WL-1"] and "status" not in entries[0], entries
    assert model(root, COUNTERS)["counters"]["WL"] == 1
    fx.commit_all(root, "WL-1")
    # A release whose frozen span reaches past the counter: the next claim, WL-2, lies inside it.
    version = model(root, VERSION)
    version["release"] = [{"version": "0.1.0", "date": "2026-09-01T00:00:00Z",
                           "worklog_span": ["WL-1", "WL-2"], "coverage_digest": "sha256:" + "0" * 64}]
    write_commit(env, root, VERSION, emit.emit_checked(version).encode("utf-8"), "a frozen span")
    refused_untouched(env, root, APPEND, "already-released span")


def flip_t11():
    return patch.object(record, "_check_released_span", lambda version_model, wl_number: None)


# --- T12, T13: recovery runs only under the lease and never overwrites an intervening edit ---------------

def _interrupted(fx, name, point="after-apply-0"):
    """A store whose create was killed at `point` (at after-apply-0 counters.toml holds its poststate and
    the index its prestate), with the dead run's leftover lease released as the operator's own step."""
    root = fx.case(name)
    proc = child(fx.env, root, CREATE, kill=point)
    assert proc.returncode == 137, ("the child is killed at", point, proc.returncode, proc.stderr[-800:])
    (Path(root) / LEASE).unlink()
    return root


def t12_recovery_lease(fx):
    env = fx.env
    root = _interrupted(fx, "t12-live-peer")
    # A live peer takes the shared lease through the upgrade verb, as `opf upgrade` does.
    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        peer = guard.acquire_lease(root_fd, MACH, "upgrade")
        try:
            refused_untouched(env, root, CREATE, "runs only under the single-writer lease")
            assert read(root, LEASE) == peer, "T12 the peer's lease is intact, never seized"
        finally:
            guard.release_lease(root_fd, MACH, peer, "upgrade")
    finally:
        os.close(root_fd)
    refused(record_cli(env, root, CREATE), "was reconciled")
    assert not any(s == "open" for s in journal_states(root).values()), "T12 reconciled once the peer is gone"


def flip_t12():
    return patch.object(record, "_with_recovery_lease", lambda ctx, pending, recover: recover())


def t13_intervening_edit(fx):
    env = fx.env
    for rel, side in ((BI_INDEX, "prestate"), (COUNTERS, "poststate")):
        root = _interrupted(fx, "t13-" + side)
        original = read(root, rel)
        (Path(root) / rel).write_bytes(original + b"# an owner's note written after the interruption\n")
        before = snapshot(root)
        result = record_cli(env, root, CREATE)
        assert snapshot(root) == before, ("T13 the intervening edit is never overwritten", rel)
        refused(result, "an intervening edit")
        assert rel in result[2], ("T13 the edited path is named", rel, result[2][-800:])
        # Once the owner undoes the edit, the next run reconciles.
        (Path(root) / rel).write_bytes(original)
        refused(record_cli(env, root, CREATE), "was reconciled")
        assert not any(s == "open" for s in journal_states(root).values()), ("T13 reconciled", rel)


def flip_t13():
    return patch.object(record, "_unexplained_operands", lambda root_fd, jr_fd, txns: [])


# --- T14: --type is checked before any operand read ---------------------------------------------------------

def t14_type_before_read(fx):
    env = fx.env
    root = fx.case("t14-type")
    before = snapshot(root)
    for rtype in ("bogus", "../counters"):
        args = ["create", "--type", rtype, "--title", "t", "--actor", "maintainer"]
        refused(record_cli(env, root, args), "enabled baseline record types only")
        assert snapshot(root) == before, ("T14 bytes untouched", rtype)


def flip_t14():
    return patch.object(record, "_check_request", lambda req, ctx: None)


# --- T6-T8: transitions, the done receipt, and the parallel-branch collision --------------------------------

ASSISTANT = ["--actor", "assistant:gate"]
MAINTAINER = ["--actor", "maintainer:owner"]


class Ticker:
    """A strictly advancing clock for one case's in-process runs: a transition's updated_at must follow the
    record's recorded timestamps, and two real runs can fall in the same second."""

    def __init__(self):
        self.now = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)

    def __call__(self):
        self.now += datetime.timedelta(minutes=1)
        return self.now


def ticking():
    return patch.object(record, "_clock_now", Ticker())


def step(fx, root, args, message):
    """One recorded operation, then a commit, so the next operation's cleanliness gate passes."""
    out = recorded(record_cli(fx.env, root, args))
    fx.commit_all(root, message)
    return out


def row(root, rid, index=BI_INDEX):
    rows = [r for r in model(root, index)["record"] if r["id"] == rid]
    assert len(rows) == 1, (rid, rows)
    return rows[0]


def receipts_of(root, rid):
    link = {"rel": "receipt_of", "id": rid}
    return [r for r in model(root, DN_INDEX).get("record", []) if link in r.get("links", [])]


def lifecycle(root):
    return [e["detail"] for e in model(root, WORKLOG)["entry"]]


def doctor_valid(env, root):
    rc, out, err = cli(env, ["doctor", "--root", str(root)])
    assert rc == 0, ("doctor VALID at rest", rc, out[-1600:], err[-800:])


def refused_untouched(env, root, args, needle):
    """A refusal that writes nothing. The byte comparison is asserted BEFORE the refusal text, so a guard
    removed under a flip turns this red on what was written, never on a changed message."""
    before = snapshot(root)
    result = record_cli(env, root, args)
    assert snapshot(root) == before, ("bytes untouched on refusal", args, result[0], result[2][-800:])
    refused(result, needle)


def refused_before_store(env, root, args, needle=None):
    """A usage refusal that happens before the store is resolved at all: the resolver is never called and
    no byte changes (asserted before any message), and the refusal names needle when one is given."""
    before = snapshot(root)
    calls = []
    original = record._opf_store.resolve_store

    def observing(*a, **k):
        calls.append(a)
        return original(*a, **k)

    with patch.object(record._opf_store, "resolve_store", observing):
        rc, out, err = record_cli(env, root, args)
    assert calls == [], ("refused before the store is resolved", args, rc, err[-800:])
    assert snapshot(root) == before and rc == 2, ("refused with nothing written", args, rc)
    assert '"event": "recorded"' not in out, out[-800:]
    if needle is not None:
        assert needle in err, ("refusal text", needle, err[-1200:])


def t6_done_with_receipt(fx):
    env = fx.env
    root = fx.case("t6-ratify")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        out = step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        assert "CANNOT-EVALUATE" not in out, "an actor-independent transition leaves doctor VALID"
        out = step(fx, root, ["transition", "BI-1", "done"] + ASSISTANT, "BI-1 done/proposed")
        assert row(root, "BI-1")["status"] == "done/proposed" and receipts_of(root, "BI-1") == [], "T6 proposed"
        assert model(root, DN_INDEX).get("record", []) == [] and model(root, COUNTERS)["counters"]["DN"] == 0
        assert "until this change is committed" in out and "'active' -> 'done/proposed'" in out, out[-1200:]
        doctor_valid(env, root)
        refused_before_store(env, root, ["done-with-receipt", "BI-1"] + ASSISTANT)
        refused_untouched(env, root, ["transition", "BI-1", "done"] + MAINTAINER, "done-with-receipt")
        step(fx, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "BI-1 done")
        receipts = receipts_of(root, "BI-1")
        assert row(root, "BI-1")["status"] == "done" and [r["id"] for r in receipts] == ["DN-1"], receipts
        assert receipts[0]["status"] == "recorded" and receipts[0]["actor"] == {"kind": "maintainer", "id": "owner"}
        assert len(model(root, DN_INDEX)["record"]) == 1 and model(root, COUNTERS)["counters"]["DN"] == 1
        assert lifecycle(root) == ["opf-record create BI-1 open", "opf-record transition BI-1 open -> active",
                                   "opf-record transition BI-1 active -> done/proposed",
                                   "opf-record transition BI-1 done/proposed -> done\nreceipt: DN-1"], lifecycle(root)
        doctor_valid(env, root)
        refused_untouched(env, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "active or done/proposed")
    root = fx.case("t6-from-active")
    with ticking():
        step(fx, root, ["create", "--type", "backlog_item", "--title", "m"] + MAINTAINER, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + MAINTAINER, "BI-1 active")
        step(fx, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "BI-1 done")
        assert row(root, "BI-1")["status"] == "done" and [r["id"] for r in receipts_of(root, "BI-1")] == ["DN-1"]
        doctor_valid(env, root)


def flip_t6_bare():
    return patch.object(record, "_derived_status", lambda kind, spec, cur_state, target: target)


def flip_t6_maintainer():
    return patch.object(record, "_require_maintainer", lambda actor: None)


def flip_t6_receipt():
    return patch.object(record, "_require_receipt_path", lambda rtype, to_status: None)


def t7_rejection(fx):
    env = fx.env
    root = fx.case("t7-reject")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        step(fx, root, ["transition", "BI-1", "done"] + ASSISTANT, "BI-1 done/proposed")
        refused_untouched(env, root, ["transition", "BI-1", "active"] + MAINTAINER, "recorded reason")
        refused_untouched(env, root, ["transition", "BI-1", "open", "--reason", "x"] + MAINTAINER,
                          "actual pre-proposal state")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + ASSISTANT,
                          "only a maintainer")
        step(fx, root, ["transition", "BI-1", "active", "--reason", "the fix did not hold"] + MAINTAINER, "rejected")
        entry = model(root, WORKLOG)["entry"][-1]
        assert row(root, "BI-1")["status"] == "active" and receipts_of(root, "BI-1") == []
        assert entry["detail"] == "opf-record transition BI-1 done/proposed -> active\nreason: the fix did not hold"
        assert entry["actor"] == {"kind": "maintainer", "id": "owner"}, entry
        doctor_valid(env, root)
    # A proposal made outside this verb: the record carries no proposed_from, so it cannot be rejected.
    root = fx.case("t7-unrecorded")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        index = model(root, BI_INDEX)
        index["record"][0].update(status="done/proposed", updated_at="2026-09-01T00:02:30Z")
        write_commit(env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), "proposed by hand")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "no pre-proposal state")


def flip_t7_reason():
    return patch.object(record, "_checked_reason", lambda req: req.values.get("--reason") or "placeholder")


def flip_t7_pre():
    return patch.object(record, "_recorded_pre_proposal", lambda ctx, row, rid, status: "active")


def _proposed(fx, root, via):
    """BI-1 created and moved to active by an assistant, then proposed at `via` (done or dropped), each
    step committed: the proposing lifecycle line is the verb's own."""
    step(fx, root, CREATE, "BI-1")
    step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
    step(fx, root, ["transition", "BI-1", via] + ASSISTANT, "BI-1 {}/proposed".format(via))


def _set_status(fx, root, status, message):
    """A canonical hand edit of BI-1's status, committed (history the verb did not write)."""
    index = model(root, BI_INDEX)
    index["record"][0]["status"] = status
    write_commit(fx.env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), message)


def t16_recorded_predecessor(fx):
    env = fx.env
    # The proposing transition records the pre-proposal state in the record itself; the rejection restores
    # exactly it and removes the field.
    root = fx.case("t16-restore")
    with ticking():
        _proposed(fx, root, "dropped")
        assert row(root, "BI-1").get("proposed_from") == "active", row(root, "BI-1")
        doctor_valid(env, root)
        refused_untouched(env, root, ["transition", "BI-1", "open", "--reason", "x"] + MAINTAINER,
                          "actual pre-proposal state")
        step(fx, root, ["transition", "BI-1", "active", "--reason", "stale"] + MAINTAINER, "rejected")
        assert row(root, "BI-1")["status"] == "active" and "proposed_from" not in row(root, "BI-1")
        doctor_valid(env, root)
    # A forged proposing line (its FROM state rewritten canonically, committed with its views, doctor
    # VALID) changes nothing: the attack target still refuses, the genuine rejection still lands.
    root = fx.case("t16-forged-line")
    with ticking():
        _proposed(fx, root, "dropped")
        worklog = model(root, WORKLOG)
        genuine = "opf-record transition BI-1 active -> dropped/proposed"
        assert worklog["entry"][-1]["detail"] == genuine, worklog["entry"][-1]
        worklog["entry"][-1]["detail"] = "opf-record transition BI-1 open -> dropped/proposed"
        write_commit(env, root, WORKLOG, emit.emit_checked(worklog).encode("utf-8"), "a forged proposing line")
        rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
        assert rc == 0, ("T16 the views re-render", rc, err[-800:])
        env.git(root, "add", "-A")
        env.git(root, "commit", "-q", "-m", "views")
        doctor_valid(env, root)
        refused_untouched(env, root, ["transition", "BI-1", "open", "--reason", "reject"] + MAINTAINER,
                          "actual pre-proposal state")
        step(fx, root, ["transition", "BI-1", "active", "--reason", "stale"] + MAINTAINER,
             "rejected despite the forged line")
        assert row(root, "BI-1")["status"] == "active" and "proposed_from" not in row(root, "BI-1")
        doctor_valid(env, root)
    # A proposal made outside the verb carries no proposed_from and cannot be rejected here.
    root = fx.case("t16-unrecorded")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        _set_status(fx, root, "done/proposed", "proposed by hand")
        refused_untouched(env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "no pre-proposal state")
    # Merge styles of a reject-and-re-propose cycle (the QA3 F1 reproduction): the recorded field rides
    # the merge, so the rejection still restores the true predecessor and the forged line still fails.
    for style in ("no-ff", "squash"):
        root = fx.case("t16-merge-" + style)
        with ticking():
            step(fx, root, CREATE, "BI-1")
            step(fx, root, ["transition", "BI-1", "dropped"] + ASSISTANT, "BI-1 dropped/proposed")
            env.git(root, "checkout", "-q", "-b", "side")
            step(fx, root, ["transition", "BI-1", "open", "--reason", "not yet"] + MAINTAINER, "rejected")
            step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
            step(fx, root, ["transition", "BI-1", "dropped"] + ASSISTANT, "BI-1 dropped/proposed again")
            env.git(root, "checkout", "-q", "main")
            if style == "no-ff":
                env.git(root, "merge", "-q", "--no-ff", "--no-edit", "side")
            else:
                env.git(root, "merge", "--squash", "-q", "side")
                env.git(root, "commit", "-q", "-m", "squash side")
            doctor_valid(env, root)
            assert row(root, "BI-1").get("proposed_from") == "active", (style, row(root, "BI-1"))
            worklog = model(root, WORKLOG)
            worklog["entry"][-1]["detail"] = "opf-record transition BI-1 open -> dropped/proposed"
            write_commit(env, root, WORKLOG, emit.emit_checked(worklog).encode("utf-8"),
                         "a forged proposing line")
            rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
            assert rc == 0, ("T16 the views re-render", style, rc, err[-800:])
            env.git(root, "add", "-A")
            env.git(root, "commit", "-q", "-m", "views")
            doctor_valid(env, root)
            refused_untouched(env, root, ["transition", "BI-1", "open", "--reason", "reject"] + MAINTAINER,
                              "actual pre-proposal state")
            step(fx, root, ["transition", "BI-1", "active", "--reason", "ok"] + MAINTAINER,
                 "rejected genuinely after the merge")
            assert row(root, "BI-1")["status"] == "active" and "proposed_from" not in row(root, "BI-1")
            doctor_valid(env, root)


# A schema-invalid current row (a doctor finding) is never trusted or overwritten, so the verb never launders
# a doctor-INVALID store into a VALID one (QA4 F1). Each refusal is its own test, so flip_t16_trust must turn
# EACH one red, not only the first.

def _forged_predecessor(fx, name):
    """BI-1 proposed at done by an assistant, then its proposed_from forged canonically to open (not a
    legal predecessor of done) and committed: a doctor finding. Run under ticking()."""
    root = fx.case(name)
    _proposed(fx, root, "done")
    index = model(root, BI_INDEX)
    assert index["record"][0]["proposed_from"] == "active", index["record"][0]
    index["record"][0]["proposed_from"] = "open"    # open is not a legal predecessor of done
    write_commit(fx.env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), "a forged predecessor")
    rc, out, err = cli(fx.env, ["doctor", "--root", str(root)])
    assert rc != 0 and "not a legal predecessor" in out, ("T16 the forged predecessor is a doctor finding",
                                                          rc, out[-1600:])
    return root


def t16_invalid_predecessor_rejection(fx):
    """The rejection of a row with an illegal proposed_from, to the forged target and to the truthful one
    alike, refuses with every byte untouched."""
    with ticking():
        root = _forged_predecessor(fx, "t16-invalid-predecessor-rejection")
        refused_untouched(fx.env, root, ["transition", "BI-1", "open", "--reason", "x"] + MAINTAINER,
                          "not schema-valid")
        refused_untouched(fx.env, root, ["transition", "BI-1", "active", "--reason", "x"] + MAINTAINER,
                          "not schema-valid")


def t16_invalid_predecessor_receipt(fx):
    """The ratifying done-with-receipt of a row with an illegal proposed_from refuses with every byte
    untouched."""
    with ticking():
        root = _forged_predecessor(fx, "t16-invalid-predecessor-receipt")
        refused_untouched(fx.env, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "not schema-valid")


def t16_stray_field(fx):
    """A stray proposed_from on an unqualified record (a doctor finding) is never silently overwritten: the
    proposing transition refuses with every byte untouched."""
    env = fx.env
    root = fx.case("t16-stray-field")
    with ticking():
        step(fx, root, CREATE, "BI-1")
        step(fx, root, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
        index = model(root, BI_INDEX)
        index["record"][0]["proposed_from"] = "open"
        write_commit(env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), "a stray field")
        rc, out, err = cli(env, ["doctor", "--root", str(root)])
        assert rc != 0 and "legal only on a '/proposed' status" in out, ("T16 the stray field is a "
                                                                        "doctor finding", rc, out[-1600:])
        refused_untouched(env, root, ["transition", "BI-1", "done"] + ASSISTANT, "not schema-valid")


def flip_t16():
    """Read the pre-proposal state from the worklog lifecycle line (the retired corroboration's input,
    hand-editable text) instead of the record's own field: the forged-line cases must turn red."""
    def from_worklog(ctx, row, rid, status):
        for entry in reversed(record._worklog_entries(ctx)):
            detail = entry.get("detail") if isinstance(entry, dict) else None
            if not isinstance(detail, str):
                continue
            m = record._LIFECYCLE_RE.match(detail.split("\n", 1)[0])
            if m is None or m.group(2) != rid:
                continue
            if m.group(1) == "transition" and m.group(4) == status and m.group(3) and "/" not in m.group(3):
                return m.group(3)
            return None
        return None
    return patch.object(record, "_recorded_pre_proposal", from_worklog)


def flip_t16_trust():
    """Trust the current row without validating it (the reviewed head's behaviour): the invalid-predecessor
    rejection, the invalid-predecessor done-with-receipt, and the stray-field proposal must each turn red."""
    return patch.object(record, "_require_valid_current", lambda row, rtype, ctx, rid, rel: None)


# --- T17: the strict type-aware delta comparison (codex QA3 F2) ----------------------------------------------

MANIFEST = MACH + "/manifest.toml"


def t17_strict_delta(fx):
    """A planner mutation that changes only a value's TYPE (True for 1, 1.0 for 1) must refuse before
    publication with every byte untouched: ordinary equality (True == 1, 1.0 == 1) would publish it."""
    env = fx.env
    base = fx.case("t17-base")
    with ticking():
        step(fx, base, CREATE, "BI-1")
        step(fx, base, ["transition", "BI-1", "active"] + ASSISTANT, "BI-1 active")
    manifest = model(base, MANIFEST)
    manifest["vendors"] = dict(registered=["x-qa"])
    write_commit(env, base, MANIFEST, emit.emit_checked(manifest).encode("utf-8"), "register x-qa")
    index = model(base, BI_INDEX)
    index["record"][0]["x-qa"] = dict(count=1)
    write_commit(env, base, BI_INDEX, emit.emit_checked(index).encode("utf-8"), "an x-qa extension")
    rc, out, err = cli(env, ["render", "--root", str(base), "--write"])
    assert rc == 0, ("T17 the views re-render", rc, err[-800:])
    env.git(base, "add", "-A")
    env.git(base, "commit", "-q", "-m", "views")
    doctor_valid(env, base)
    for label, sub, args, mutate in (
            ("bool-extension", "transition", ["transition", "BI-1", "done"] + ASSISTANT,
             lambda ctx, operand: operand.new_model["record"][0]["x-qa"].__setitem__("count", True)),
            ("float-extension", "done-with-receipt", ["done-with-receipt", "BI-1"] + MAINTAINER,
             lambda ctx, operand: operand.new_model["record"][0]["x-qa"].__setitem__("count", 1.0)),
            ("bool-index-schema", "transition", ["transition", "BI-1", "done"] + ASSISTANT,
             lambda ctx, operand: operand.new_model.__setitem__("schema", True)),
            ("bool-receipt-counter", "done-with-receipt", ["done-with-receipt", "BI-1"] + MAINTAINER,
             lambda ctx, operand: ctx.counters.new_model["counters"].__setitem__("DN", True))):
        root = fx.case("t17-" + label, base)

        def tampering(req, ctx, operand, now, planner=record._PLANNERS[sub], mutate=mutate):
            plan = planner(req, ctx, operand, now)
            mutate(ctx, operand)
            return plan

        clock = Ticker()
        clock.now += datetime.timedelta(days=1)   # past the base fixture's recorded timestamps
        with patch.object(record, "_clock_now", clock), patch.dict(record._PLANNERS, {sub: tampering}):
            refused_untouched(env, root, args, "postcondition failed")


def flip_t17():
    """Compare the delta with ordinary equality (the reviewed head's behaviour): True == 1 and 1.0 == 1
    then read as the allowed delta and the mutation publishes."""
    return patch.object(record, "_strict_equal", lambda a, b: a == b)


# --- T18: the resolution bundle on a decision transition ----------------------------------------------------

PD_CREATE = ["create", "--type", "pending_decision", "--title", "which layout"]
# The decider is named apart from every --actor below, so a planner that takes decided_by from --actor
# writes a value the request never gave.
DECIDE = ["--decision", "the inline layout", "--decided-by", "the architecture board"]
BUNDLE_KEYS = ("decision", "decided_at", "decided_by")
AUTOMATION = ["--actor", "automation:gate"]


def _bundle(rec):
    return tuple(rec.get(key) for key in BUNDLE_KEYS)


def _filed(fx, root, actor=ASSISTANT):
    """PD-1 created by an assistant, then its answer filed by `actor` (an assistant or automation) at
    decided/proposed, each step committed. Run under ticking()."""
    step(fx, root, PD_CREATE + ASSISTANT, "PD-1")
    step(fx, root, ["transition", "PD-1", "decided"] + DECIDE + actor, "PD-1 decided/proposed")
    rec = row(root, "PD-1", PD_INDEX)
    assert rec["status"] == "decided/proposed" and rec.get("proposed_from") == "open", rec
    assert _bundle(rec) == ("the inline layout", rec["updated_at"], "the architecture board"), rec
    return rec


def t18_decision_bundle(fx):
    env = fx.env
    root = fx.case("t18-maintainer-decides")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        step(fx, root, ["transition", "PD-1", "decided"] + DECIDE + MAINTAINER, "PD-1 decided")
        rec = row(root, "PD-1", PD_INDEX)
        assert rec["status"] == "decided" and "proposed_from" not in rec, rec
        assert _bundle(rec) == ("the inline layout", rec["updated_at"], "the architecture board"), rec
        assert lifecycle(root)[-1] == "opf-record transition PD-1 open -> decided", lifecycle(root)
        doctor_valid(env, root)
    root = fx.case("t18-assistant-ratified")
    with ticking():
        proposed = _filed(fx, root)
        doctor_valid(env, root)
        step(fx, root, ["transition", "PD-1", "decided"] + MAINTAINER, "PD-1 decided")
        rec = row(root, "PD-1", PD_INDEX)
        assert rec["status"] == "decided" and "proposed_from" not in rec, rec
        assert _bundle(rec) == _bundle(proposed), ("T18 the ratification keeps the bundle", rec, proposed)
        doctor_valid(env, root)
    root = fx.case("t18-rejected")
    with ticking():
        _filed(fx, root)
        step(fx, root, ["transition", "PD-1", "open", "--reason", "not the board's answer"] + MAINTAINER,
             "rejected")
        rec = row(root, "PD-1", PD_INDEX)
        assert rec["status"] == "open" and not any(k in rec for k in BUNDLE_KEYS + ("proposed_from",)), rec
        assert model(root, WORKLOG)["entry"][-1]["detail"] == (
            "opf-record transition PD-1 decided/proposed -> open\nreason: not the board's answer")
        doctor_valid(env, root)
    root = fx.case("t18-automation-filed")
    with ticking():
        _filed(fx, root, AUTOMATION)
        entry = model(root, WORKLOG)["entry"][-1]
        assert entry["actor"] == {"kind": "automation", "id": "gate"}, entry
        assert entry["detail"] == "opf-record transition PD-1 open -> decided/proposed", entry
        doctor_valid(env, root)


# Each option refusal is its own test, so its own flip must turn it red (the PR2 fix 5 rule).

def refused_unplanned(env, root, args, needle):
    """A decide refused before its bundle is planned: _resolution_bundle is never called, asserted before
    the bytes and the message, so the requires half removed under a flip turns this red on the isolated
    guard even though the planner's own lookup of the absent option still fails closed."""
    calls = []
    original = record._resolution_bundle

    def observing(*a):
        calls.append(a)
        return original(*a)

    before = snapshot(root)
    with patch.object(record, "_resolution_bundle", observing):
        result = record_cli(env, root, args)
    assert calls == [], ("refused before the bundle is planned", args, result[0], result[2][-800:])
    assert snapshot(root) == before, ("bytes untouched on refusal", args, result[0], result[2][-800:])
    refused(result, needle)


def flip_t18_bundle():
    """The planner writes no bundle (the reviewed head's behaviour): every decide is refused."""
    return patch.object(record, "_resolution_bundle", lambda req, ts: {})


def flip_t18_rejection():
    """The rejection removes proposed_from only and keeps the bundle, which an open decision may not carry."""
    return patch.object(record, "_proposal_keys", lambda rtype, cur_state, rejection: (record.PROPOSED_FROM,))


def flip_t18_decider():
    """The planner takes decided_by from --actor: the row stays schema-valid, so only the independent
    oracle, which reads --decided-by, refuses it."""
    def from_actor(req, ts):
        actor = req.actor["kind"] + (":" + req.actor["id"] if "id" in req.actor else "")
        return {"decision": req.values["--decision"], "decided_at": ts, "decided_by": actor}
    return patch.object(record, "_resolution_bundle", from_actor)


def t18_requires_options(fx):
    """A maintainer decide without --decision and --decided-by refuses before its bundle is planned."""
    root = fx.case("t18-requires-options")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        refused_unplanned(fx.env, root, ["transition", "PD-1", "decided"] + MAINTAINER, "requires --decision")


def flip_t18_requires():
    """Drop the requires half of the option guard: a decide without the options is planned."""
    original = record._require_decision_options

    def apply_only(req, rid, rtype, cur_state, target, cur_qual):
        if "--decision" in req.values:
            original(req, rid, rtype, cur_state, target, cur_qual)
    return patch.object(record, "_require_decision_options", apply_only)


def t18_given_together(fx):
    """--decision without --decided-by refuses in the parser, before the store is resolved."""
    root = fx.case("t18-given-together")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        refused_before_store(fx.env, root, ["transition", "PD-1", "decided", "--decision", "the inline layout"]
                             + MAINTAINER, "given together")


def flip_t18_together():
    """Drop the parser's given-together check: one option alone reaches the store."""
    return patch.object(record, "_require_decision_pair", lambda seen: None)


def t18_apply_only_withdrawn(fx):
    """The bundle options on open -> withdrawn refuse with every byte untouched."""
    root = fx.case("t18-apply-only-withdrawn")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        refused_untouched(fx.env, root, ["transition", "PD-1", "withdrawn"] + DECIDE + MAINTAINER, "apply only")


def t18_apply_only_ratification(fx):
    """The bundle options on the ratification of decided/proposed refuse with every byte untouched (a
    ratification keeps the bundle its proposal wrote)."""
    root = fx.case("t18-apply-only-ratification")
    with ticking():
        _filed(fx, root)
        refused_untouched(fx.env, root, ["transition", "PD-1", "decided"] + DECIDE + MAINTAINER, "apply only")


def flip_t18_apply_only():
    """Drop the apply-only half of the option guard: the options on any other transition are ignored."""
    original = record._require_decision_options

    def requires_only(req, rid, rtype, cur_state, target, cur_qual):
        if record._decides(rtype, cur_state, target):
            original(req, rid, rtype, cur_state, target, cur_qual)
    return patch.object(record, "_require_decision_options", requires_only)


# --- T72: supersession when a decision is decided, and the rest of the decisions register ---------------

def _section(root, heading):
    """The entry lines of one section of the rendered DECISIONS view."""
    text = read(root, DECISIONS_VIEW).decode("utf-8")
    assert "## " + heading + "\n" in text, (heading, text)
    return text.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0].strip().splitlines()


def _pd_create(title):
    return ["create", "--type", "pending_decision", "--title", title]


def _decided(fx, root, title, n):
    """PD-n created and decided by a maintainer, each step committed. Run under ticking()."""
    rid = "PD-{}".format(n)
    step(fx, root, _pd_create(title) + MAINTAINER, rid)
    step(fx, root, ["transition", rid, "decided"] + DECIDE + MAINTAINER, rid + " decided")


def _supersede(rid, target, actor=MAINTAINER):
    return ["transition", rid, "decided"] + DECIDE + ["--supersedes", target] + actor


def t72_supersession(fx):
    env = fx.env
    root = fx.case("t72-supersedes")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        step(fx, root, ["transition", "PD-2", "decided", "--decision", "the split layout", "--decided-by",
                        "the architecture board", "--supersedes", "PD-1"] + MAINTAINER, "PD-2 supersedes PD-1")
        rec = row(root, "PD-2", PD_INDEX)
        assert rec["status"] == "decided" and rec["links"] == [{"rel": "supersedes", "id": "PD-1"}], rec
        assert "links" not in row(root, "PD-1", PD_INDEX), row(root, "PD-1", PD_INDEX)
        entry = model(root, WORKLOG)["entry"][-1]
        assert entry["detail"] == "opf-record transition PD-2 open -> decided\nsupersedes: PD-1", entry
        assert entry["links"] == [{"rel": "relates", "id": "PD-2"}, {"rel": "relates", "id": "PD-1"}], entry
        doctor_valid(env, root)
        assert _section(root, "Effective resolutions") == ["- PD-2 which layout, again (supersedes PD-1)"], (
            read(root, DECISIONS_VIEW))
        assert _section(root, "Superseded resolutions") == ["- PD-1 which layout"], read(root, DECISIONS_VIEW)
        # An assistant files PD-3's answer at decided/proposed; the maintainer's ratification carries the link.
        step(fx, root, _pd_create("which layout, third") + ASSISTANT, "PD-3")
        step(fx, root, ["transition", "PD-3", "decided"] + DECIDE + ASSISTANT, "PD-3 decided/proposed")
        step(fx, root, ["transition", "PD-3", "decided", "--supersedes", "PD-2"] + MAINTAINER, "PD-3 ratified")
        rec = row(root, "PD-3", PD_INDEX)
        assert rec["status"] == "decided" and "proposed_from" not in rec, rec
        assert rec["links"] == [{"rel": "supersedes", "id": "PD-2"}] and _bundle(rec)[0] == "the inline layout", rec
        assert lifecycle(root)[-1] == ("opf-record transition PD-3 decided/proposed -> decided\n"
                                       "supersedes: PD-2"), lifecycle(root)
        doctor_valid(env, root)
        assert _section(root, "Effective resolutions") == ["- PD-3 which layout, third (supersedes PD-2)"], (
            read(root, DECISIONS_VIEW))
        assert _section(root, "Superseded resolutions") == ["- PD-1 which layout", "- PD-2 which layout, again"]


def flip_t72_link():
    """The planner writes no link: only the independent oracle, which reads --supersedes, refuses it."""
    return patch.object(record, "_supersession_link", lambda req, ctx, operand, rid: None)


# Each supersession refusal is its own test, so its own flip must turn it red (the PR2 fix 5 rule).

def t72_record_id(fx):
    """A --supersedes value that is not a record id refuses in the parser, before the store is resolved."""
    root = fx.case("t72-record-id")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        refused_before_store(fx.env, root, _supersede("PD-2", "layout"), "is not a record id")


def flip_t72_record_id():
    """Drop the parser's record-id check: the malformed value reaches the store."""
    return patch.object(record, "_require_supersedes_id", lambda values: None)


def t72_proposed_landing(fx):
    """--supersedes on an assistant decide (a decided/proposed landing) refuses with every byte untouched:
    the doctor counts the link from a proposal, which would leave PD-1's chain with no current resolution."""
    root = fx.case("t72-proposed-landing")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + ASSISTANT, "PD-2")
        refused_untouched(fx.env, root, _supersede("PD-2", "PD-1", ASSISTANT),
                          "applies only to a transition that lands")


def flip_t72_landing():
    """Drop the landing check: the proposal's link publishes before the doctor finds the chain with no
    current resolution."""
    return patch.object(record, "_require_supersedes_landing", lambda req, rid, rtype, current, to_status: None)


def t72_undecided_target(fx):
    """A target that is still open is not a current resolution: the supersede refuses with every byte
    untouched."""
    root = fx.case("t72-undecided-target")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        refused_untouched(fx.env, root, _supersede("PD-2", "PD-1"), "not an unqualified decided")


def flip_t72_decided():
    """Drop the target-decided check: the link to an open decision publishes (doctor VALID)."""
    return patch.object(record, "_require_superseded_decided", lambda trow, target: None)


def t72_invalid_target(fx):
    """A target the schema grades invalid (a whitespace decision, committed by a canonical hand edit: a
    doctor finding) is never trusted: the supersede refuses with every byte untouched."""
    env = fx.env
    root = fx.case("t72-invalid-target")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        index = model(root, PD_INDEX)
        assert index["record"][0]["id"] == "PD-1", index["record"][0]
        index["record"][0]["decision"] = "   "
        write_commit(env, root, PD_INDEX, emit.emit_checked(index).encode("utf-8"), "a blank decision")
        rc, out, err = cli(env, ["doctor", "--root", str(root)])
        assert rc != 0 and "PD-1" in out, ("T72 the blank decision is a doctor finding", rc, out[-1600:])
        refused_untouched(env, root, _supersede("PD-2", "PD-1"), "not schema-valid")


def flip_t72_target():
    """Drop only the target's validation call: the invalid target is trusted, the link publishes, and the
    final doctor then reports the target's own pre-existing finding."""
    return patch.object(record, "_require_valid_target", lambda trow, ctx, target, rel: None)


def t72_wrong_type_target(fx):
    """A target seated in the pending_decision index as another type (its `type` rewritten to backlog_item
    by a canonical hand edit: a doctor finding) is never trusted: the supersede refuses with every byte
    untouched."""
    env = fx.env
    root = fx.case("t72-wrong-type-target")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        index = model(root, PD_INDEX)
        assert index["record"][0]["id"] == "PD-1", index["record"][0]
        index["record"][0]["type"] = "backlog_item"
        write_commit(env, root, PD_INDEX, emit.emit_checked(index).encode("utf-8"), "a wrong-type row")
        rc, out, err = cli(env, ["doctor", "--root", str(root)])
        assert rc != 0, ("T72 the wrong-type row is a doctor finding", rc, out[-1600:])
        refused_untouched(env, root, _supersede("PD-2", "PD-1"), "not schema-valid")


def t72_missing_target(fx):
    """A target that is no record at all (PD-9 in a store holding PD-1 and PD-2) refuses with every byte
    untouched."""
    root = fx.case("t72-missing-target")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        refused_untouched(fx.env, root, _supersede("PD-2", "PD-9"), "PD-9 is not a record in")


def t72_wrong_namespace_target(fx):
    """A target in another namespace (the backlog item BI-1, which exists) is not in the pending_decision
    index, so the supersede refuses with every byte untouched."""
    root = fx.case("t72-wrong-namespace-target")
    with ticking():
        step(fx, root, ["create", "--type", "backlog_item", "--title", "an item"] + MAINTAINER, "BI-1")
        step(fx, root, _pd_create("which layout") + MAINTAINER, "PD-1")
        refused_untouched(fx.env, root, _supersede("PD-1", "BI-1"), "BI-1 is not a record in")


def flip_t72_unchecked_target():
    """Append the link to whatever --supersedes names, never locating or checking the target: the link to a
    missing record or a backlog item publishes before the doctor finds it dangling or of the wrong type
    (C-LINKS). The lookup cannot be dropped alone: every later target check reads the row it returns, and
    a missing row still fails the schema check closed."""
    def unchecked(req, ctx, operand, rid):
        target = req.values.get("--supersedes")
        return None if target is None else {"rel": record.SUPERSEDES, "id": target}
    return patch.object(record, "_supersession_link", unchecked)


def refused_before_chain_rule(env, root, args, needle):
    """A supersede refused by its own target check, before the planned chain is judged:
    _require_one_current_resolution is never called, asserted before the bytes and the message, so the
    check removed under a flip turns this red on the isolated guard even though the chain rule behind it
    still refuses with every byte untouched."""
    calls = []
    original = record._require_one_current_resolution

    def observing(*a):
        calls.append(a)
        return original(*a)

    before = snapshot(root)
    with patch.object(record, "_require_one_current_resolution", observing):
        result = record_cli(env, root, args)
    assert calls == [], ("refused before the chain rule runs", args, result[0], result[2][-800:])
    assert snapshot(root) == before, ("bytes untouched on refusal", args, result[0], result[2][-800:])
    refused(result, needle)


def t72_fork(fx):
    """A second successor for a resolution its chain has already superseded would fork the chain into two
    current resolutions: the supersede refuses with every byte untouched."""
    root = fx.case("t72-fork")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        step(fx, root, _supersede("PD-2", "PD-1"), "PD-2 supersedes PD-1")
        step(fx, root, _pd_create("which layout, third") + MAINTAINER, "PD-3")
        refused_before_chain_rule(fx.env, root, _supersede("PD-3", "PD-1"), "already superseded by PD-2")


def flip_t72_fork():
    """Drop the chain-head check: the second successor reaches the chain rule, which then refuses the fork."""
    return patch.object(record, "_require_chain_head", lambda rows, target, rel: None)


def t72_cycle(fx):
    """PD-2 was created linking supersedes PD-1 while both were open, then decided: PD-1 superseding PD-2
    would close a cycle and leave the chain with no current resolution, so it refuses with every byte
    untouched."""
    root = fx.case("t72-cycle")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        step(fx, root, _pd_create("which layout, again") + ["--link", "supersedes=PD-1"] + MAINTAINER, "PD-2")
        step(fx, root, ["transition", "PD-2", "decided"] + DECIDE + MAINTAINER, "PD-2 decided")
        doctor_valid(fx.env, root)
        refused_before_chain_rule(fx.env, root, _supersede("PD-1", "PD-2"), "close a cycle")


def flip_t72_cycle():
    """Drop the cycle check: the closing link reaches the chain rule, which then refuses the chain with no
    current resolution."""
    return patch.object(record, "_require_acyclic", lambda rows, rid, target, rel: None)


def t72_chain_join(fx):
    """QA1 Case A. PD-3 (open) supersedes PD-2 and PD-1 and PD-4 supersedes PD-1, then PD-4 is decided:
    the chain's one current resolution, doctor VALID. PD-2 decided with --supersedes PD-4 passes every
    target check (PD-4 is decided, schema-valid, and the head of its chain, and its own chain never reaches
    PD-2), but it would leave the chain with none (PD-2 superseded by PD-3, PD-4 by PD-2), so it refuses
    with every byte untouched; the same decide without the link keeps PD-4 current and lands doctor
    VALID."""
    root = fx.case("t72-chain-join")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        step(fx, root, _pd_create("which layout, third") + ["--link", "supersedes=PD-2", "--link",
                                                            "supersedes=PD-1"] + MAINTAINER, "PD-3")
        step(fx, root, _pd_create("which layout, fourth") + ["--link", "supersedes=PD-1"] + MAINTAINER, "PD-4")
        step(fx, root, ["transition", "PD-4", "decided"] + DECIDE + MAINTAINER, "PD-4 decided")
        doctor_valid(fx.env, root)
        refused_untouched(fx.env, root, _supersede("PD-2", "PD-4"),
                          "(PD-1, PD-2, PD-3, PD-4) with 0 current effective resolutions")
        step(fx, root, ["transition", "PD-2", "decided"] + DECIDE + MAINTAINER, "PD-2 decided")
        doctor_valid(fx.env, root)


def _shadowed(fx, root):
    """PD-1 decided, PD-2 open, and PD-3 created open linking supersedes PD-2: doctor VALID (PD-1's chain
    has its one resolution, and the PD-2 and PD-3 chain is wholly undecided). Run under ticking()."""
    _decided(fx, root, "which layout", 1)
    step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
    step(fx, root, _pd_create("which layout, third") + ["--link", "supersedes=PD-2"] + MAINTAINER, "PD-3")
    doctor_valid(fx.env, root)


def t72_superseded_record(fx):
    """QA1 Case B. PD-2 is already superseded by the open PD-3, so PD-2 decided with --supersedes PD-1
    would leave the joined chain with no current resolution (PD-1 superseded by PD-2, PD-2 by PD-3): it
    refuses with every byte untouched."""
    root = fx.case("t72-superseded-record")
    with ticking():
        _shadowed(fx, root)
        refused_untouched(fx.env, root, _supersede("PD-2", "PD-1"), "PD-2 is itself already superseded by PD-3")


def t72_superseded_record_plain(fx):
    """QA1 Case B without the link: PD-2's own decide would leave its chain with PD-3 with no current
    resolution, so it refuses with every byte untouched too."""
    root = fx.case("t72-superseded-record-plain")
    with ticking():
        _shadowed(fx, root)
        refused_untouched(fx.env, root, ["transition", "PD-2", "decided"] + DECIDE + MAINTAINER,
                          "PD-2 is itself already superseded by PD-3")


def flip_t72_chain():
    """Drop the pre-publication chain rule: the decide publishes before the doctor finds its chain with no
    current resolution."""
    return patch.object(record, "_require_one_current_resolution", lambda ctx, rows, rid, rel: None)


ARCHIVE = MACH + "/archive/2026"
ARCHIVED_PD_INDEX = ARCHIVE + "/pending_decision.index.toml"


def _rotate(fx, root, rid):
    """Release the whole worklog as 0.1.0 (release_cut, its published changelog summary, and VERSION) and
    rotate it into archive/2026 with `rid`'s pending_decision row, archive.toml enumerating the moved id
    and span; then render and commit: doctor VALID (spec 12)."""
    env = fx.env
    worklog, version = model(root, WORKLOG), model(root, VERSION)
    cut = opf_release.release_cut(version, worklog, "0.1.0", "2026-09-01T00:00:00Z")
    assert cut.status == opf_check.VALID, ("the release cut", cut.status, cut.findings)
    changelog = "# Changelog\n\n## unreleased\n\n## 0.1.0\n\n- 0.1.0 notes\n"
    entries, _findings = opf_changelog._changelog_entries(changelog)
    released = cut.version_data
    released["summary"] = [{"covers": "0.1.0", "status": "published",
                            "digest": opf_changelog.freeze_digest(dict(entries)["0.1.0"])},
                           {"covers": "unreleased", "status": "working"}]
    index = model(root, PD_INDEX)
    moved = [r for r in index["record"] if r["id"] == rid]
    assert len(moved) == 1, (rid, index)
    index["record"] = [r for r in index["record"] if r["id"] != rid]
    span = [worklog["entry"][0]["id"], worklog["entry"][-1]["id"]]
    documents = {
        VERSION: released, PD_INDEX: index, WORKLOG: dict(worklog, entry=[]),
        ARCHIVED_PD_INDEX: {"schema": 1, "record": moved},
        ARCHIVE + "/worklog.toml": {"schema": 1, "entry": worklog["entry"]},
        ARCHIVE + "/archive.toml": {"schema": 1, "moved": [{"id": rid, "type": "pending_decision",
                                                           "destination": "archive/2026/pending_decision.index.toml"}],
                                   "worklog_moved": [{"span": span, "destination": "archive/2026/worklog.toml"}]}}
    (Path(root) / ARCHIVE).mkdir(parents=True)
    for rel, document in documents.items():
        (Path(root) / rel).write_bytes(emit.emit_checked(document).encode("utf-8"))
    (Path(root) / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (Path(root) / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
    assert rc == 0, ("render after the rotation", rc, out[-800:], err[-800:])
    env.git(root, "add", "-A")
    env.git(root, "commit", "-q", "-m", "release 0.1.0 and rotate " + rid)
    doctor_valid(env, root)


def _withdrawn_successor(fx, root):
    """PD-1 open and PD-2 created linking supersedes PD-1, then withdrawn. Run under ticking()."""
    step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
    step(fx, root, _pd_create("which layout, again") + ["--link", "supersedes=PD-1"] + MAINTAINER, "PD-2")
    step(fx, root, ["transition", "PD-2", "withdrawn"] + MAINTAINER, "PD-2 withdrawn")


def t72_archived_resolution(fx):
    """QA3 reproduction A. PD-2 (withdrawn) supersedes PD-1 and PD-3 (decided) supersedes PD-2, and PD-3 is
    rotated to the archive: doctor VALID, PD-3 the chain's one current resolution. Deciding PD-1 keeps the
    archived PD-3 current, so it lands and the doctor stays VALID."""
    root = fx.case("t72-archived-resolution")
    with ticking():
        _withdrawn_successor(fx, root)
        step(fx, root, _pd_create("which layout, third") + ["--link", "supersedes=PD-2"] + MAINTAINER, "PD-3")
        step(fx, root, ["transition", "PD-3", "decided"] + DECIDE + MAINTAINER, "PD-3 decided")
        _rotate(fx, root, "PD-3")
        step(fx, root, ["transition", "PD-1", "decided"] + DECIDE + MAINTAINER, "PD-1 decided")
        assert row(root, "PD-1", PD_INDEX)["status"] == "decided", row(root, "PD-1", PD_INDEX)
        doctor_valid(fx.env, root)


def t72_archived_successor(fx):
    """QA3 reproduction B. PD-2 (withdrawn) supersedes PD-1 and is rotated to the archive: doctor VALID, the
    chain wholly undecided. Deciding PD-1 would leave the chain with no current resolution (PD-1
    superseded by the archived PD-2), so it refuses with every byte untouched."""
    root = fx.case("t72-archived-successor")
    with ticking():
        _withdrawn_successor(fx, root)
        _rotate(fx, root, "PD-2")
        refused_untouched(fx.env, root, ["transition", "PD-1", "decided"] + DECIDE + MAINTAINER,
                          "PD-1 is itself already superseded by PD-2")


def t72_unreadable_archive(fx):
    """PD-1 decided and rotated to the archive, then its archived index replaced by bytes that do not parse
    (committed): the doctor's archive walk cannot read it, so deciding PD-2 refuses with every byte
    untouched rather than judging the chain without the archive."""
    root = fx.case("t72-unreadable-archive")
    with ticking():
        _decided(fx, root, "which layout", 1)
        _rotate(fx, root, "PD-1")
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        write_commit(fx.env, root, ARCHIVED_PD_INDEX, b"record = [\n", "a torn archive index")
        refused_untouched(fx.env, root, ["transition", "PD-2", "decided"] + DECIDE + MAINTAINER,
                          "does not pass the doctor's own archive walk")


def flip_t72_archive():
    """Restore the active-only read: the chain rule judges the planned index alone, never the archive."""
    return patch.object(record, "_chain_records", lambda ctx, rows: [
        opf_check._make_rec(r, record.PENDING_DECISION, "active") for r in rows if isinstance(r, dict)])


def t72_archived_head(fx):
    """QA4 MINOR-3. PD-2 decided superseding PD-1, then PD-2, the chain's head, rotated to the archive:
    doctor VALID. PD-3 decided with --supersedes PD-2 finds the archived head through the chain rule's
    record set, so it lands and the doctor stays VALID, PD-3 the chain's one current resolution."""
    root = fx.case("t72-archived-head")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        step(fx, root, _supersede("PD-2", "PD-1"), "PD-2 supersedes PD-1")
        _rotate(fx, root, "PD-2")
        step(fx, root, _pd_create("which layout, third") + MAINTAINER, "PD-3")
        step(fx, root, _supersede("PD-3", "PD-2"), "PD-3 supersedes the archived PD-2")
        rec = row(root, "PD-3", PD_INDEX)
        assert rec["status"] == "decided" and rec["links"] == [{"rel": "supersedes", "id": "PD-2"}], rec
        doctor_valid(fx.env, root)


def t72_archived_fork(fx):
    """PD-2 decided superseding PD-1 and rotated to the archive, PD-1 still active: a second successor for
    PD-1 would fork the chain, and the chain-head check, reading the archive, refuses it before the chain
    rule runs, with every byte untouched."""
    root = fx.case("t72-archived-fork")
    with ticking():
        _decided(fx, root, "which layout", 1)
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        step(fx, root, _supersede("PD-2", "PD-1"), "PD-2 supersedes PD-1")
        _rotate(fx, root, "PD-2")
        step(fx, root, _pd_create("which layout, third") + MAINTAINER, "PD-3")
        refused_before_chain_rule(fx.env, root, _supersede("PD-3", "PD-1"), "already superseded by PD-2")


def t72_archived_withdrawn_target(fx):
    """PD-1 withdrawn and rotated to the archive: doctor VALID. PD-2 decided with --supersedes PD-1 finds
    the archived PD-1, which is not a current resolution, so the target-decided check refuses it with
    every byte untouched."""
    root = fx.case("t72-archived-withdrawn-target")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        step(fx, root, ["transition", "PD-1", "withdrawn"] + MAINTAINER, "PD-1 withdrawn")
        _rotate(fx, root, "PD-1")
        step(fx, root, _pd_create("which layout, again") + MAINTAINER, "PD-2")
        refused_untouched(fx.env, root, _supersede("PD-2", "PD-1"),
                          "PD-1 is 'withdrawn', not an unqualified decided")


def flip_t72_archived_target():
    """Restore the active-only target read: the target checks see the active index alone, so the archived
    head is no record, an archived superseder is missed, and an archived withdrawn target is no record."""
    return patch.object(record, "_target_rows", lambda ctx, operand: record._index_rows(operand))


def t72_archived_cycle(fx):
    """PD-2 (withdrawn) supersedes PD-1 and PD-3 (decided) supersedes PD-2, then PD-2 is rotated to the
    archive: doctor VALID, PD-3 the chain's one current resolution. PD-1 decided with --supersedes PD-3
    would close a cycle through the archived PD-2, so the cycle check, reading the archive, refuses it
    with its own message and every byte untouched."""
    root = fx.case("t72-archived-cycle")
    with ticking():
        _withdrawn_successor(fx, root)
        step(fx, root, _pd_create("which layout, third") + ["--link", "supersedes=PD-2"] + MAINTAINER, "PD-3")
        step(fx, root, ["transition", "PD-3", "decided"] + DECIDE + MAINTAINER, "PD-3 decided")
        _rotate(fx, root, "PD-2")
        refused_untouched(fx.env, root, _supersede("PD-1", "PD-3"),
                          "PD-3 in {} already leads back to PD-1".format(PD_INDEX))


def flip_t72_archived_cycle():
    """Restore the active-only read for the cycle check alone: it walks the active index, so the chain
    through the archived PD-2 is missed and the chain rule refuses instead, with its own message."""
    link, acyclic = record._supersession_link, record._require_acyclic

    def active_only(req, ctx, operand, rid):
        with patch.object(record, "_require_acyclic", lambda rows, rid_, target, rel: acyclic(
                record._index_rows(operand), rid_, target, rel)):
            return link(req, ctx, operand, rid)
    return patch.object(record, "_supersession_link", active_only)


PATTERN = ["create", "--type", "preference_pattern", "--field", "context=layout choices", "--field",
           "rationale=fewer files to keep in sync"]
RULING = ["create", "--type", "maintainer_decision", "--title", "inline for the site", "--field",
          "decision=use the inline layout for the site", "--link", "exemplifies=PP-1"]


def t72_register(fx):
    env = fx.env
    root = fx.case("t72-register")
    with ticking():
        step(fx, root, PATTERN + ["--title", "prefer inline layouts"] + MAINTAINER, "PP-1")
        assert row(root, "PP-1", PP_INDEX)["status"] == "active", row(root, "PP-1", PP_INDEX)
        step(fx, root, RULING + MAINTAINER, "MD-1")
        rec = row(root, "MD-1", MD_INDEX)
        assert rec["status"] == "recorded" and rec["links"] == [{"rel": "exemplifies", "id": "PP-1"}], rec
        assert rec["decision"] == "use the inline layout for the site", rec
        doctor_valid(env, root)
        assert _section(root, "Maintainer decisions") == ["- MD-1 inline for the site (exemplifies PP-1)"], (
            read(root, DECISIONS_VIEW))
        step(fx, root, PATTERN + ["--title", "prefer short titles"] + ASSISTANT, "PP-2")
        assert row(root, "PP-2", PP_INDEX)["status"] == "active/proposed", row(root, "PP-2", PP_INDEX)
        doctor_valid(env, root)
        step(fx, root, ["transition", "PP-2", "active"] + MAINTAINER, "PP-2 ratified")
        assert row(root, "PP-2", PP_INDEX)["status"] == "active", row(root, "PP-2", PP_INDEX)
        step(fx, root, ["transition", "PP-2", "retired"] + MAINTAINER, "PP-2 retired")
        rec = row(root, "PP-2", PP_INDEX)
        assert rec["status"] == "retired" and "proposed_from" not in rec, rec
        doctor_valid(env, root)


def flip_t72_links():
    """The planner drops the requested links: only the independent oracle, which reads --link, refuses the
    ruling's missing exemplifies link."""
    original = record._envelope_extras

    def no_links(req, rec):
        original(req, rec)
        rec.pop("links", None)
    return patch.object(record, "_envelope_extras", no_links)


def t72_assistant_ruling(fx):
    """A maintainer_decision is a maintainer act: the same ruling filed by an assistant refuses with every
    byte untouched."""
    root = fx.case("t72-assistant-ruling")
    with ticking():
        step(fx, root, PATTERN + ["--title", "prefer inline layouts"] + MAINTAINER, "PP-1")
        refused_untouched(fx.env, root, RULING + ASSISTANT, "not valid")


def flip_t72_trust():
    """The planner's record validation replaced by a pass-through: the assistant ruling publishes."""
    return patch.object(record, "_validated", lambda rec, expected_type, ctx: rec)


# --- T73: the delivery bundle on a contribution send --------------------------------------------------------

CN_INDEX = MACH + "/contribution.index.toml"
CN_FIELDS = ["--field", "recipient=peer-project", "--field", "dedup_class=drift-gate", "--field",
             "content_digest=sha256:00"]
SEND = ["--channel", "pr", "--delivery-ref", "peer/repo#128"]
RECEIPT = ["--receipt-ref", "peer/repo#128 merged"]
SEND_BUNDLE = {"channel": "pr", "ref": "peer/repo#128"}


def _cn_create(title, links=()):
    return (["create", "--type", "contribution", "--title", title] + CN_FIELDS
            + [arg for link in links for arg in ("--link", link)] + MAINTAINER)


def _cn_step(fx, root, args, label):
    """One contribution operation that must record, then a commit and doctor VALID. A refusal turns T73 red
    on this labelled assertion, which carries the refusal text."""
    rc, out, err = record_cli(fx.env, root, args)
    assert rc == 0, ("T73 " + label + " records", rc, err[-1200:])
    recorded((rc, out, err))
    fx.commit_all(root, label)
    doctor_valid(fx.env, root)
    return row(root, args[1], CN_INDEX) if args[0] == "transition" else None


def t73_contribution_delivery(fx):
    env = fx.env
    root = fx.case("t73-delivery")
    with ticking():
        _cn_step(fx, root, _cn_create("drift-gate fix"), "CN-1 created")
        # Each option refusal leaves every byte untouched.
        refused_untouched(env, root, ["transition", "CN-1", "withdrawn", "--channel", "pr"] + MAINTAINER,
                          "--channel and --delivery-ref apply only")
        refused_untouched(env, root, ["transition", "CN-1", "sent"] + SEND + RECEIPT + MAINTAINER,
                          "--receipt-ref applies only")
        rec = _cn_step(fx, root, ["transition", "CN-1", "sent"] + SEND + ASSISTANT, "CN-1 sent/proposed")
        sent = {"channel": "pr", "ref": "peer/repo#128", "sent_at": rec["updated_at"]}
        assert rec["status"] == "sent/proposed" and rec.get("proposed_from") == "proposed", rec
        assert rec.get("delivery") == sent, ("T73 the send writes the delivery bundle", rec)
        rec = _cn_step(fx, root, ["transition", "CN-1", "proposed", "--reason", "not sent yet"] + MAINTAINER,
                       "CN-1 rejected")
        assert rec["status"] == "proposed" and "delivery" not in rec and "proposed_from" not in rec, (
            "T73 the rejection removes the delivery bundle", rec)
        rec = _cn_step(fx, root, ["transition", "CN-1", "sent"] + SEND + ASSISTANT, "CN-1 sent/proposed again")
        proposed = rec["delivery"]
        rec = _cn_step(fx, root, ["transition", "CN-1", "sent"] + MAINTAINER, "CN-1 ratified")
        assert rec["status"] == "sent" and "proposed_from" not in rec, rec
        assert rec["delivery"] == proposed, ("T73 the ratification keeps the delivery bundle", rec, proposed)
        # A re-send is a new record linking supersedes; the superseded record records superseded.
        _cn_step(fx, root, _cn_create("drift-gate fix, again", ["supersedes=CN-1"]), "CN-2 created")
        rec = _cn_step(fx, root, ["transition", "CN-1", "superseded"] + MAINTAINER, "CN-1 superseded")
        assert rec["status"] == "superseded" and rec["delivery"] == proposed, rec
        assert row(root, "CN-2", CN_INDEX)["links"] == [{"rel": "supersedes", "id": "CN-1"}]
        rec = _cn_step(fx, root, ["transition", "CN-2", "sent"] + SEND + MAINTAINER, "CN-2 sent")
        sent = rec["delivery"]
        assert rec["status"] == "sent" and sent == dict(SEND_BUNDLE, sent_at=rec["updated_at"]), rec
        rec = _cn_step(fx, root, ["transition", "CN-2", "acknowledged"] + RECEIPT + ASSISTANT,
                       "CN-2 acknowledged/proposed")
        assert rec["status"] == "acknowledged/proposed" and rec["delivery"] == dict(
            sent, receipt_ref="peer/repo#128 merged", receipted_at=rec["updated_at"]), rec
        rec = _cn_step(fx, root, ["transition", "CN-2", "sent", "--reason", "not merged yet"] + MAINTAINER,
                       "CN-2 rejected")
        assert rec["status"] == "sent" and rec["delivery"] == sent, (
            "T73 the rejection removes the receipt keys", rec, sent)
        rec = _cn_step(fx, root, ["transition", "CN-2", "acknowledged"] + RECEIPT + MAINTAINER, "CN-2 acknowledged")
        assert rec["status"] == "acknowledged" and rec["delivery"] == dict(
            sent, receipt_ref="peer/repo#128 merged", receipted_at=rec["updated_at"]), rec
        assert lifecycle(root)[-1] == "opf-record transition CN-2 sent -> acknowledged", lifecycle(root)


def flip_t73_delivery():
    """The planner writes no delivery bundle (the reviewed head's behaviour): every send is refused."""
    return patch.object(record, "_delivery_fields", lambda req, ts: {})


def flip_t73_rejection():
    """The rejection of sent/proposed removes proposed_from only and keeps the bundle, whose sent_at a
    proposed contribution may not carry."""
    return patch.object(record, "_proposal_keys", lambda rtype, cur_state, rejection: (record.PROPOSED_FROM,))


def flip_t73_receipt():
    """The rejection of acknowledged/proposed keeps the receipt keys, which a sent contribution may not
    carry."""
    return patch.object(record, "_rejected_delivery", lambda rtype, cur_state, rejection, row: None)


def t8_collision(fx):
    env = fx.env
    root = fx.case("t8-collision")
    env.git(root, "branch", "peer")
    with ticking():
        step(fx, root, ["create", "--type", "backlog_item", "--title", "alpha"] + ASSISTANT, "BI-1 alpha")
        doctor_valid(env, root)
        env.git(root, "checkout", "-q", "peer")
        step(fx, root, ["create", "--type", "backlog_item", "--title", "beta"] + ASSISTANT, "BI-1 beta")
        doctor_valid(env, root)
    env.git(root, "checkout", "-q", "main")
    assert env.run_git(root, "merge", "--no-edit", "peer").returncode != 0, "T8 the store paths conflict"
    conflicted = env.git(root, "diff", "--name-only", "--diff-filter=U").split()
    assert BI_INDEX in conflicted, conflicted
    # The hand merge spec 5.7 forbids: the index keeps both sides' rows, re-emitted canonically so byte
    # reproduction cannot catch it; every other conflicted path takes this side.
    for rel in conflicted:
        if rel != BI_INDEX:
            env.git(root, "checkout", "--ours", "--", rel)
            continue
        ours, theirs = (tomllib.loads(env.git(root, "show", ":{}:{}".format(n, rel))) for n in (2, 3))
        ours["record"] = ours["record"] + [r for r in theirs["record"] if r not in ours["record"]]
        (Path(root) / rel).write_bytes(emit.emit_checked(ours).encode("utf-8"))
    env.git(root, "add", "-A")
    env.git(root, "commit", "-q", "--no-edit")
    ids = [r["id"] for r in model(root, BI_INDEX)["record"]]
    assert ids == ["BI-1", "BI-1"], ids
    rc, out, err = cli(env, ["doctor", "--root", str(root)])
    assert rc == 1 and "store integrity: INVALID" in out, ("T8 doctor INVALID", rc, out[-2400:], err[-800:])
    assert "  C-ID-SPACE: FINDING" in out, out[-2400:]
    assert "FINDING: C-ID-SPACE: duplicate id 'BI-1'" in out, out[-2400:]


def flip_t8():
    return patch.object(opf_check, "check_unique_ids", lambda ids: [])


# --- T15: a journal lock left after COMPLETE, then its reconciliation ----------------------------------------

# Inside the child: the journal lock release fails after the transaction reached COMPLETE, so the run goes
# on to render, run doctor, and report, leaving its journal lock behind.
FAILING_LOCK_RELEASE = """
import _journal
def _failing_release(journal_root):
    raise _journal.JournalError("synthetic journal lock release failure")
_journal.release_lock = _failing_release
"""


def t15_leftover_lock(fx):
    env = fx.env
    root = fx.case("t15-leftover-lock")
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and '"event": "recorded"' in proc.stdout, (proc.returncode, proc.stderr[-800:])
    assert "could not be released" in proc.stderr, ("T15 the retained lock is surfaced", proc.stderr[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T15 the journal lock is left"
    result = record_cli(env, root, CREATE)
    refused(result, "was reconciled")
    err = result[2]
    assert "is COMPLETE" in err and "not recorded by the journal" in err, err[-1200:]
    assert "never run" not in err and "never ran" not in err, ("T15 no claim that render and doctor did not "
                                                             "run", err[-1200:])
    assert not (Path(root) / record.JOURNAL_REL / "lock").exists(), "T15 the leftover lock is reconciled"


def flip_t15():
    original = record._leftover_lock_outcome

    def claims_never_ran(owner, states):
        line = original(owner, states)
        head, sep, _rest = line.partition("so its publication was applied when it committed")
        return head + sep + ", with its render and final doctor never run" if sep else line
    return patch.object(record, "_leftover_lock_outcome", claims_never_ran)


def flip_t1():
    return patch.object(record, "_require_canonical", lambda operand: None)


def flip_t3():
    return patch.object(record, "_postcondition", lambda plan, req, ctx, now: None)


# --- T19-T23: the record run-id grammar and the homes-2 journal home (spec 4.2/8.8) -----------------------

import re
import _opf_store


def _self_test_homes2_active(root):
    """Activate homes 2 over a fixture store the way apply's A11 test does: declare `homes = 2` in the
    manifest and patch SUPPORTED_HOMES, HOMES2_SPEC_VERSION and validate_manifest (homes is not yet a
    manifest schema key). The manifest bytes are restored on exit."""
    import contextlib
    import tomllib
    from unittest.mock import patch
    resolution = _opf_store.resolve_store(root)
    path = Path(resolution.store_root) / resolution.machine_rel / _opf_store.MANIFEST_NAME
    original = path.read_bytes()
    model = tomllib.loads(original.decode("utf-8"))
    model["opf"]["homes"] = 2
    real_validate = _opf_store.validate_manifest

    def validate_declared(data, *args, **kwargs):
        # Validate the rest as shipped, then keep the declaration in the validated base: _store_homes
        # derives the generation from that base.
        opf = data.get("opf") if isinstance(data, dict) else None
        if not (isinstance(opf, dict) and "homes" in opf):
            return real_validate(data, *args, **kwargs)
        result = real_validate(dict(data, opf=dict((k, v) for k, v in opf.items() if k != "homes")),
                               *args, **kwargs)
        if isinstance(result.base, dict):
            result.base = dict(result.base, homes=opf["homes"])
        return result

    @contextlib.contextmanager
    def active():
        path.write_text(emit.emit_checked(model), encoding="utf-8")
        try:
            with patch.object(_opf_store, "SUPPORTED_HOMES", 2), \
                    patch.object(_opf_store, "HOMES2_SPEC_VERSION", _opf_store.SUPPORTED_SPEC_VERSION), \
                    patch.object(_opf_store, "validate_manifest", validate_declared):
                yield
        finally:
            path.write_bytes(original)
    return active()

RECORD_RUN_RE = r"record-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}"
TYPED_JOURNAL = ".working/journals/record/journal"
RECORD_OPERANDS = (COUNTERS, BI_INDEX, WORKLOG)

# Inside a killed child: the transaction is minted under the LEGACY dotted name an earlier build used,
# so recovery of that shape stays exercised end to end.
LEGACY_NAME_FLIP = """
import os, time
record._record_run_id = lambda lock_id: "record-create." + str(os.getpid()) + "." + str(time.time_ns()) + "." + lock_id
"""

# Inside a killed child: activate homes 2 exactly as _self_test_homes2_active does (the
# manifest on disk already declares homes = 2; these are the module patches), and route the claim seam
# through the homes-1 counters path, emulating the later id-reservation change, so the run reaches the
# homes-2 publication.
HOMES2_CHILD_FLIP = """
import _opf_store
_opf_store.SUPPORTED_HOMES = 2
_opf_store.HOMES2_SPEC_VERSION = _opf_store.SUPPORTED_SPEC_VERSION
_real_validate = _opf_store.validate_manifest
def _declared_validate(data, *a, **k):
    table = data.get("opf") if isinstance(data, dict) else None
    if not (isinstance(table, dict) and "homes" in table):
        return _real_validate(data, *a, **k)
    result = _real_validate(dict(data, opf=dict((key, v) for key, v in table.items() if key != "homes")),
                            *a, **k)
    if isinstance(result.base, dict):
        result.base = dict(result.base, homes=table["homes"])
    return result
_opf_store.validate_manifest = _declared_validate
_claim_seam = record.claim_ids
record.claim_ids = lambda homes, high, demand, known_complete: _claim_seam(1, high, demand, known_complete)
"""


def subtree(root, prefix):
    return dict((k, v) for k, v in snapshot(root).items() if k == prefix or k.startswith(prefix + "/"))


def t19_run_id_grammar(fx):
    """A publication's journal transaction name is the record run id in the homes grammar, and the
    shared constructor accepts it verbatim."""
    root = fx.case("t19-run-id")
    recorded(record_cli(fx.env, root, CREATE))
    names = list(journal_states(root))
    assert len(names) == 1 and re.fullmatch(RECORD_RUN_RE, names[0]), ("T19 the homes grammar", names)
    assert record._opf_store.txn_record("record", names[0]).endswith("/transaction.toml")


def flip_t19():
    return patch.object(record, "_record_run_id", lambda lock_id: "record-create.{}.{}.{}".format(
        os.getpid(), datetime.datetime.now().microsecond, lock_id))


def t19_legacy_name_recovery(fx):
    """An interrupted transaction under the LEGACY dotted name is still reconciled: kill points before
    apply, mid-apply, and at a torn COMPLETE, with the operands ending exactly at the prestate or
    exactly at the poststate, the poststate iff COMPLETE."""
    env = fx.env
    reference = fx.case("t19-legacy-reference")
    proc = child(env, reference, CREATE)
    assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-800:])
    post = dict((rel, read(reference, rel)) for rel in RECORD_OPERANDS)
    for hook in ("after-publish-INTENT", "after-apply-0", "torn:COMPLETE"):
        root = fx.case("t19-legacy-" + hook.replace(":", "-"))
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        proc = child(env, root, CREATE, kill=hook, flip=LEGACY_NAME_FLIP)
        assert proc.returncode == 137, ("T19 the child is killed at", hook, proc.stderr[-800:])
        (Path(root) / LEASE).unlink()
        refused(record_cli(env, root, CREATE), "was reconciled")
        states = journal_states(root)
        assert states and all(s != "open" for s in states.values()), ("T19 terminal", hook, states)
        assert all(name.startswith("record-create.") for name in states), ("T19 the legacy name", states)
        now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        complete = any(s == "complete" for s in states.values())
        assert now == (post if complete else pre), ("T19 prestate or poststate, poststate iff COMPLETE",
                                                    hook, states)


def flip_t19_legacy():
    """A recovery that enumerates only grammar-named transactions: the legacy-named interruption is
    never reconciled."""
    original = journal._journal_txn_dirs

    def grammar_only(jr_fd, journal_root):
        return [t for t in original(jr_fd, journal_root) if re.fullmatch(RECORD_RUN_RE, t.name)]
    return patch.object(journal, "_journal_txn_dirs", grammar_only)


def t20_homes2_legacy_refused(fx):
    """A legacy .aiqt/record/journal found on a homes-2 store is refused by name and never recovered in
    place: the .aiqt subtree stays byte-unchanged (the T15 leftover lock included) and no typed journal
    home is created."""
    env = fx.env
    root = fx.case("t20-homes2-legacy")
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T20 the legacy journal holds a leftover lock"
    with _self_test_homes2_active(root):
        aiqt_before = subtree(root, ".aiqt")
        result = record_cli(env, root, CREATE)
        refused(result, "legacy record journal")
        assert "never recovered" in result[2] and "homes migration transports it" in result[2], result[2][-800:]
        assert subtree(root, ".aiqt") == aiqt_before, "T20 the .aiqt subtree is byte-unchanged"
        assert not (Path(root) / ".working/journals").exists(), "T20 no typed journal home is created"


def flip_t20():
    """Probe the generation as 1 (the reviewed head's behaviour): the legacy journal is reconciled in
    place and .aiqt changes."""
    return patch.object(record, "_probe_homes", lambda ctx: 1)


def t21_homes2_publish(fx):
    """A direct homes-2 publication (claim_ids bypassed to the homes-1 counters path, emulating the
    later id-reservation change) lands ONE capability-bound transaction in the typed journal home with
    its terminal projection, rewrites exactly the planned operands, and touches nothing under .aiqt."""
    env = fx.env
    root = fx.case("t21-homes2-publish")
    with _self_test_homes2_active(root):
        req = record.parse_request(CREATE + ["--root", str(root)])
        res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
        assert res.status == record._opf_store.RESOLVED, res
        root_fd = record._opf_store._open_dir_nofollow(res.store_root)
        try:
            ctx = record.Context(res, str(root), root_fd)
            ctx.journal_rel = record._record_journal_rel(record._probe_homes(ctx))
            assert ctx.journal_rel == TYPED_JOURNAL, ("T21 the probed journal home", ctx.journal_rel)
            record._load_manifest(ctx)
            assert ctx.homes == 2, ("T21 the validated generation", ctx.homes)
            ctx.counters = record._read_operand(root_fd, ctx.rel(opf_check.COUNTERS_NAME))
            ctx.version = record._read_operand(root_fd, ctx.rel(opf_check.VERSION_NAME)).model
            ctx.worklog = record._read_operand(root_fd, ctx.rel(opf_check.WORKLOG_NAME))
            operand = record._read_operand(root_fd, record._operand_rel(req, ctx))
            seam = record.claim_ids
            with patch.object(record, "claim_ids", lambda homes, high, demand, known_complete:
                              seam(1, high, demand, known_complete)):
                plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            record._publish(ctx, plan, "create")
        finally:
            os.close(root_fd)
        assert not (Path(root) / ".aiqt").exists(), "T21 nothing under .aiqt"
        states = journal_states(root, TYPED_JOURNAL)
        names = list(states)
        assert len(names) == 1 and re.fullmatch(RECORD_RUN_RE, names[0]), ("T21 one grammar-named txn", names)
        assert states[names[0]] == "complete", states
        projection = Path(root) / record._opf_store.txn_record("record", names[0])
        assert projection.is_file(), "T21 the terminal projection is published"
        for op in plan.operands:
            assert read(root, op.rel) == op.new_raw, ("T21 the operand is rewritten", op.rel)
        assert not (Path(root) / LEASE).exists(), "T21 the capability lease is released"


def flip_t21():
    return patch.object(record, "_record_journal_rel", lambda homes: record.JOURNAL_REL)


def t22_homes2_claim_refused(fx):
    """On a homes-2 store the verb still refuses at the claim seam (the record id reservation is a
    separate later change): every byte untouched, and neither the legacy nor the typed journal home is
    created."""
    env = fx.env
    root = fx.case("t22-homes2-claim")
    with _self_test_homes2_active(root):
        refused_untouched(env, root, CREATE, "is not active in this build")
        assert not (Path(root) / record.JOURNAL_REL).exists(), "T22 no legacy journal is created"
        assert not (Path(root) / ".working/journals").exists(), "T22 no typed journal home is created"


def flip_t22():
    def creating(ctx):
        (Path(ctx.res.store_root) / ctx.journal_rel).mkdir(parents=True, exist_ok=True)
    return patch.object(record, "_reconcile_capability_journal", creating)


def t23_homes2_crash(fx):
    """A homes-2 publication killed at each journal step: the typed journal is reconciled by the next
    run under the operation capability (whose confirmed-dead gate clears the dead run's lease and active
    record), the operands end exactly at the prestate or exactly at the poststate, the poststate iff
    COMPLETE, and .aiqt is never touched."""
    env = fx.env
    base = fx.case("t23-homes2-base")
    with _self_test_homes2_active(base):
        reference = fx.case("t23-homes2-reference", base)
        proc = child(env, reference, CREATE, flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-1600:])
        assert not (Path(reference) / ".aiqt").exists(), "T23 the reference run touches nothing under .aiqt"
        ref_states = journal_states(reference, TYPED_JOURNAL)
        assert list(ref_states.values()) == ["complete"], ref_states
        post = dict((rel, read(reference, rel)) for rel in RECORD_OPERANDS)
        for hook in ("after-publish-INTENT", "torn-payload:1", "after-apply-2", "torn:COMPLETE"):
            root = fx.case("t23-homes2-" + hook.replace(":", "-"), base)
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            proc = child(env, root, CREATE, kill=hook, flip=HOMES2_CHILD_FLIP)
            assert proc.returncode == 137, ("T23 the child is killed at", hook, proc.stderr[-800:])
            assert RECORDED_EVENT not in proc.stdout, ("T23 a killed run reports no id", hook)
            assert (Path(root) / LEASE).exists(), ("T23 the dead run's capability lease is left", hook)
            result = record_cli(env, root, CREATE)
            refused(result, "was reconciled")
            assert not (Path(root) / ".aiqt").exists(), ("T23 recovery touches nothing under .aiqt", hook)
            states = journal_states(root, TYPED_JOURNAL)
            assert states and all(s != "open" for s in states.values()), ("T23 terminal", hook, states)
            assert all(re.fullmatch(RECORD_RUN_RE, name) for name in states), ("T23 grammar names", states)
            now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            complete = any(s == "complete" for s in states.values())
            assert now == (post if complete else pre), ("T23 prestate or poststate, poststate iff COMPLETE",
                                                        hook, states)
            assert not (Path(root) / LEASE).exists(), ("T23 the confirmed-dead lease is cleared", hook)


def flip_t23():
    """Recover the homes-2 store from the legacy journal root (the reviewed head's routing): the typed
    interruption is never reconciled."""
    return patch.object(record, "_record_journal_rel", lambda homes: record.JOURNAL_REL)


# --- T5-flip, T24-T28: the PR D fix-1 vectors ------------------------------------------------------------

def t5_flip_write(fx):
    """FLIP_T5 must stay a genuine discriminator against the current _publish signature: applied
    inside a killed child it performs its unjournaled counters write and the child dies at the kill
    point (137), never at an earlier signature error (a TypeError exits 2 before the flip's write
    could ever run, so nothing would discriminate the journal boundary)."""
    env = fx.env
    root = fx.case("t5-flip-write")
    pre = read(root, COUNTERS)
    proc = child(env, root, CREATE, kill="after-publish-INTENT", flip=FLIP_T5)
    assert "TypeError" not in proc.stderr, ("T5-flip a signature error is not a discrimination",
                                            proc.stderr[-800:])
    assert proc.returncode == 137, ("T5-flip the child dies at the kill point", proc.returncode,
                                    proc.stderr[-800:])
    assert read(root, COUNTERS) != pre, "T5-flip the unjournaled counters write executed before the kill"
    states = journal_states(root)
    assert any(s == "open" for s in states.values()), ("T5-flip the journaled remainder is open", states)


def t24_broken_manifest(fx):
    """A manifest the journal-home probe cannot read leaves the homes generation UNKNOWABLE (the two
    generations share one layout), so a store carrying a legacy .aiqt/record/journal refuses
    fail-closed BEFORE any legacy reconciliation write -- the .aiqt subtree stays byte-unchanged even
    on a homes-2 store with a reconcilable leftover -- while a store carrying no legacy journal keeps
    the homes-1 broken-manifest behaviour: the run proceeds to the manifest refusal itself, nothing
    reconciled and nothing written."""
    env = fx.env
    root = fx.case("t24-broken-manifest")
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T24 a reconcilable legacy leftover exists"
    manifest_rel = MACH + "/" + record._opf_store.MANIFEST_NAME
    original_read = record._read_operand

    def failing_manifest_read(root_fd, rel):
        if rel == manifest_rel:
            raise record.RecordError("synthetic manifest read failure")
        return original_read(root_fd, rel)

    with _self_test_homes2_active(root):
        before = snapshot(root)
        with patch.object(record, "_read_operand", failing_manifest_read):
            result = record_cli(env, root, CREATE)
        assert snapshot(root) == before, "T24 nothing is written on an unknowable generation"
        refused(result, "homes generation cannot be told")
    # With no legacy journal, the broken-manifest run keeps the homes-1 behaviour: it reaches the
    # manifest read refusal itself.
    root = fx.case("t24-no-legacy-journal")
    before = snapshot(root)
    with patch.object(record, "_read_operand", failing_manifest_read):
        result = record_cli(env, root, CREATE)
    assert snapshot(root) == before, "T24 bytes untouched without a legacy journal"
    refused(result, "synthetic manifest read failure")


def flip_t24():
    """Probe a failed manifest read as generation 1 (the reviewed head's behaviour): the legacy
    journal is reconciled in place and .aiqt changes."""
    original = record._probe_homes

    def probe_as_1(ctx):
        try:
            return original(ctx)
        except record.RecordError:
            return 1
    return patch.object(record, "_probe_homes", probe_as_1)


def t25_plan_under_capability(fx):
    """The homes-2 recovery plan (which transactions are open, and the clean-state rule over their
    operands) is derived under the HELD operation capability. A peer publication is attempted at the
    exact moment an unheld check would leave unprotected -- immediately after the operand check
    returns clean -- and either the held capability refuses the peer (the check ran under the
    claim), or the peer's completed publication must survive reconciliation untouched; the reviewed
    head instead rolled it back and erased it."""
    env = fx.env
    base = fx.case("t25-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t25-homes2-race", base)
        proc = child(env, root, CREATE, kill="after-apply-0", flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 137, ("T25 the child is killed", proc.returncode, proc.stderr[-800:])
        assert any(s == "open" for s in journal_states(root, TYPED_JOURNAL).values()), \
            "T25 the interruption is open"
        peer = {"attempted": False, "published": False, "raw": None}
        original_check = record._unexplained_operands

        def racing_check(root_fd, jr_fd, txns):
            problems = original_check(root_fd, jr_fd, txns)
            if problems or peer["attempted"]:
                return problems
            peer["attempted"] = True
            current = read(root, COUNTERS)
            payload = current + b"# a peer publication landed between the check and the recovery\n"
            op = {"op": "write", "path": COUNTERS,
                  "poststate": {"kind": "file", "content-sha256": record._sha256(payload)},
                  "source-poststate": {"kind": "file",
                                       "mode": stat.S_IMODE((Path(root) / COUNTERS).lstat().st_mode),
                                       "sha256": record._sha256(current)}}
            try:
                cap = record._opf_oplock.acquire_operation(str(root), record.VERB, recover=True)
            except record._opf_oplock.OpLockError:
                return problems     # the held capability excluded the peer: the check ran under it
            try:
                record._opf_journal.run_transaction(cap, "record", "record-20260927T000001Z-" + "0" * 16,
                                                    [op], lambda o: payload)
                peer["published"] = True
                peer["raw"] = payload
            finally:
                record._opf_oplock.release_operation(cap)
            return problems

        with patch.object(record, "_unexplained_operands", racing_check):
            result = record_cli(env, root, CREATE)
        assert peer["attempted"], "T25 the peer attempted its publication in the unheld window"
        if peer["published"]:
            assert read(root, COUNTERS) == peer["raw"], \
                "T25 a peer publication that completed is never rolled back by reconciliation"
        refused(result, "was reconciled")


def flip_t25():
    """Derive the recovery plan BEFORE the capability is acquired (the reviewed head's order): a
    peer publication landing in the gap is rolled back and erased."""
    fixed = record._recover_capability_journal

    def plan_outside(ctx, opened, unprojected):
        plan = record._capability_recovery_plan(ctx)
        with patch.object(record, "_capability_recovery_plan", lambda ctx: plan):
            return fixed(ctx, opened, unprojected)
    return patch.object(record, "_recover_capability_journal", plan_outside)


def t26_complete_before_projection(fx):
    """A homes-2 publication killed after its durable COMPLETE but before the terminal projection:
    the transaction is terminal, its projection absent, and the dead holder's lease left. The next
    run reconciles exactly that state -- the confirmed-dead leftovers are cleared, the missing
    projection is published, the operands keep the poststate, and .aiqt is never touched -- instead
    of leaving a state every later acquisition refuses."""
    env = fx.env
    base = fx.case("t26-homes2-base")
    with _self_test_homes2_active(base):
        reference = fx.case("t26-homes2-reference", base)
        proc = child(env, reference, CREATE, flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode,
                                                                        proc.stderr[-1600:])
        post = dict((rel, read(reference, rel)) for rel in RECORD_OPERANDS)
        root = fx.case("t26-homes2-complete", base)
        proc = child(env, root, CREATE, kill="after-publish-COMPLETE", flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 137, ("T26 the child is killed", proc.returncode, proc.stderr[-800:])
        states = journal_states(root, TYPED_JOURNAL)
        assert list(states.values()) == ["complete"], ("T26 the durable COMPLETE", states)
        (name,) = states
        projection = Path(root) / record._opf_store.txn_record("record", name)
        assert not projection.exists(), "T26 the crash landed before the projection"
        assert (Path(root) / LEASE).exists(), "T26 the dead holder's lease is left"
        result = record_cli(env, root, CREATE)
        refused(result, "was reconciled")
        assert "already terminal" in result[2], result[2][-800:]
        assert projection.is_file(), "T26 the missing terminal projection is published"
        now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        assert now == post, "T26 the completed publication keeps its poststate"
        assert not (Path(root) / LEASE).exists(), "T26 the confirmed-dead lease is cleared"
        assert not (Path(root) / ".aiqt").exists(), "T26 nothing under .aiqt"


def flip_t26():
    """A projection can never read as missing (the reviewed head's open-only trigger): the
    COMPLETE-before-projection state is never reconciled."""
    return patch.object(record, "_projection_missing", lambda ctx, name: False)


def t27_homes1_report(fx):
    """The homes-1 success report is byte-shape-identical to the pre-D build: it carries no
    journal_rel key (the D-a2 rename is the only homes-1 change), and the commit advice still
    names the legacy journal home."""
    env = fx.env
    root = fx.case("t27-homes1-report")
    out = recorded(record_cli(env, root, CREATE))
    line = next(l for l in out.splitlines() if RECORDED_EVENT in l)
    report = json.loads(line)
    assert "journal_rel" not in report, ("T27 no journal_rel on homes 1", sorted(report))
    assert "the journal under {} is local recovery evidence".format(record.JOURNAL_REL) in out, out[-800:]


def flip_t27():
    """Re-add the journal_rel key to the homes-1 report (the reviewed head's shape)."""
    original = record._emit_success

    def with_journal_rel(report):
        return original(dict(report, journal_rel=record.JOURNAL_REL))
    return patch.object(record, "_emit_success", with_journal_rel)


SPEC15_SENTENCE = ("A no-follow existence probe of a former `.aiqt/` location, used only to refuse "
                   "an operation, is not a read of that material.")


def spec15_text():
    return (TOOLS.parent / "spec" / "OPF-SPEC.md").read_text(encoding="utf-8")


def t28_spec15_sentence(fx):
    """Spec 15 records the probe-to-refuse rule the D-a5 refusal and the unknowable-generation
    refusal rely on (maintainer decision, 2026-09-29). Compared whitespace-normalized, so the
    spec's own line wrapping never decides the verdict."""
    flat = " ".join(spec15_text().split())
    assert "## 15. Genericization boundary" in flat, "T28 the spec 15 heading is present"
    section = flat.partition("## 15. Genericization boundary")[2]
    assert "Only homes migration MAY read explicitly inventoried OPF artefacts" in section, \
        "T28 the spec 15 anchor is present"
    assert SPEC15_SENTENCE in section, "T28 the spec 15 probe sentence is present"


def flip_t28():
    """Read the spec with the sentence removed."""
    original = spec15_text
    return patch.object(sys.modules[__name__], "spec15_text",
                        lambda: " ".join(original().split()).replace(SPEC15_SENTENCE, ""))


# --- T29: the gate's git runs inside the OPF git lifecycle -----------------------------------------------

def stripped_launch_env(env):
    """The environment the operation capability's rev-parse (_opf_oplock._git_rev_parse_output) gives
    git during an in-process run: the run's own environment with every GIT_* variable removed,
    GIT_CONFIG_NOSYSTEM included."""
    return dict((k, v) for k, v in env.vars.items() if not k.startswith("GIT_"))


def t29_write_launch_config(gitconfig, trace):
    """The one-launch trace2 global config T29 writes into the scrubbed HOME. A module seam: T63
    injects a write that fails after creating the file."""
    gitconfig.write_text("[trace2]\n\tenvVars = GIT_CONFIG_NOSYSTEM,GIT_CONFIG_SYSTEM\n"
                         "\teventTarget = {}\n".format(trace), encoding="utf-8")


@contextlib.contextmanager
def t29_launch_config(gitconfig, trace):
    """T29's temporary global config, created INSIDE the cleanup region (PR D fix 11): its removal is
    attempted on every exit of this context, a write that failed after creating the file included
    (the fix-10 head wrote it before its try/finally, so a failed write left it in the scrubbed HOME
    for every later fixture git launch). A write that failed before creating the file leaves nothing
    to remove; a removal failure raises."""
    try:
        t29_write_launch_config(gitconfig, trace)
        yield
    finally:
        if gitconfig.exists():
            gitconfig.unlink()


def t29_git_lifecycle(fx):
    """A git launch that strips every GIT_* variable, as the operation capability's rev-parse does, still
    runs with the system-config pins (GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_SYSTEM the null device): the gate
    runs inside the OPF git lifecycle, whose PATH wrapper reasserts them, so no fixture or production git
    call reads the host's system configuration. Observed through git's own trace2 (PR D fix 10): the
    scrubbed HOME's global config, written for exactly this one launch, names the two pin variables in
    trace2.envVars, and git logs each one's value from its OWN environment as a def_param event (an
    unset variable yields no event), so the launch argv defines no alias, which the F-367
    maintenance-pin scan refuses (an alias expansion is a command line that scan cannot read). Every
    fixture git call carries the no-maintenance pins."""
    env = fx.env
    root = fx.case("t29-git-lifecycle")
    trace = Path(root) / "t29-trace2-events.jsonl"
    gitconfig = Path(env.vars["HOME"]) / ".gitconfig"
    assert not gitconfig.exists(), "T29 the scrubbed HOME carries no global git config"
    with t29_launch_config(gitconfig, trace):
        proc = subprocess.run(["git", "-C", str(root), *GIT_NO_MAINTENANCE, "rev-parse", "--git-dir"],
                              capture_output=True, text=True, timeout=120, env=stripped_launch_env(env))
    assert proc.returncode == 0, ("T29 the stripped launch runs", proc.returncode, proc.stderr[-800:])
    seen = dict()
    for line in trace.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)   # one trace2 event per line; unparseable output is a Harness fault
        if event.get("event") == "def_param":
            seen[event.get("param")] = event.get("value")
    pins = (seen.get("GIT_CONFIG_NOSYSTEM"), seen.get("GIT_CONFIG_SYSTEM"))
    assert pins == ("1", os.devnull), ("T29 a GIT_*-stripped git launch keeps the system-config pins", pins)
    maintenance = [env.git(root, "config", "--get", key).strip()
                   for key in ("gc.auto", "gc.autoDetach", "maintenance.auto")]
    assert maintenance == ["0", "false", "false"], ("T29 the no-maintenance pins", maintenance)


def flip_t29():
    """Launch git past the lifecycle's wrapper (the reviewed head ran outside the lifecycle), keeping
    only the gate's own GIT_CONFIG_NOSYSTEM pin so the flip itself never reads system configuration."""
    original = stripped_launch_env

    def past_wrapper(env):
        launch = original(env)
        wrapper = os.path.dirname(shutil.which("git", path=launch["PATH"]))
        launch["PATH"] = os.pathsep.join(p for p in launch["PATH"].split(os.pathsep) if p != wrapper)
        launch["GIT_CONFIG_NOSYSTEM"] = "1"
        return launch
    return patch.object(sys.modules[__name__], "stripped_launch_env", past_wrapper)


# --- T30, T31: the PR D fix-3 vectors --------------------------------------------------------------------

def t30_unactivated_homes2(fx):
    """A readable manifest declaring homes 2 while this tooling does not activate it (no activation
    patches: SUPPORTED_HOMES is 1, so homes_generation reads it as 1) is refused BEFORE any
    reconciliation or write, with the homes-2 spec_version and without it. The reviewed head probed
    it as generation 1 and reconciled the legacy journal in place, removing its leftover lock."""
    env = fx.env
    manifest_rel = MACH + "/" + record._opf_store.MANIFEST_NAME
    for label, spec_version in (("pair", record._opf_store.HOMES2_SPEC_VERSION), ("current", None)):
        root = fx.case("t30-unactivated-" + label)
        proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
        assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (label, proc.returncode,
                                                                        proc.stderr[-800:])
        assert (Path(root) / record.JOURNAL_REL / "lock").exists(), ("T30 a reconcilable legacy leftover",
                                                                    label)
        manifest = model(root, manifest_rel)
        manifest["opf"]["homes"] = 2
        if spec_version is not None:
            manifest["opf"]["spec_version"] = spec_version
        (Path(root) / manifest_rel).write_text(emit.emit_checked(manifest), encoding="utf-8")
        assert record._opf_store.homes_generation(manifest) == 1, ("T30 homes 2 is not activated", label)
        before = snapshot(root)
        result = record_cli(env, root, CREATE)
        assert snapshot(root) == before, ("T30 nothing is reconciled or written", label)
        refused(result, "which this tooling does not activate")


def flip_t30():
    """Probe the generation alone (the reviewed head's behaviour): the unactivated homes-2
    declaration reads as generation 1 and the legacy journal is reconciled in place."""
    def generation_only(ctx):
        manifest = record._read_operand(ctx.root_fd, ctx.rel(record._opf_store.MANIFEST_NAME)).model
        return record._opf_store.homes_generation(manifest)
    return patch.object(record, "_probe_homes", generation_only)


# Inside the killed child: the run holds the capability (it is past _acquire_guard) and dies before its
# transaction begins, so on a store with no earlier homes-2 publication no journal home exists.
DIE_BEFORE_PUBLISH = """
import os
record._publish = lambda *a, **k: os._exit(137)
"""
# Inside the killed child: the run dies at the render, after its COMPLETE transaction and projection.
DIE_AT_RENDER = """
import os
record._render = lambda *a, **k: os._exit(137)
"""


def t31_dead_capability(fx):
    """A homes-2 run killed while holding the operation capability, in each state that leaves
    recovery no journal work (no journal home yet, a nothing-opened transaction, a COMPLETE one with
    its projection), leaves the dead holder's lease and active record. The next run reclaims them
    through the confirmed-dead gate and refuses naming it; the operands end at the prestate, or at
    the poststate iff COMPLETE; .aiqt is never touched; and an ordinary acquisition succeeds again.
    The reviewed head never reconciled these states, so every later acquisition refused on the stale
    records. A LIVE holder's capability is refused, never seized, and it releases cleanly."""
    env = fx.env
    base = fx.case("t31-homes2-base")
    with _self_test_homes2_active(base):
        reference = fx.case("t31-homes2-reference", base)
        proc = child(env, reference, CREATE, flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-1600:])
        post = dict((rel, read(reference, rel)) for rel in RECORD_OPERANDS)
        for label, kill, flip, want in (("no-journal", None, DIE_BEFORE_PUBLISH, []),
                                        ("nothing-opened", "after-preimage-0", "", ["nothing-opened"]),
                                        ("complete-projected", None, DIE_AT_RENDER, ["complete"])):
            root = fx.case("t31-homes2-" + label, base)
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            proc = child(env, root, CREATE, kill=kill, flip=HOMES2_CHILD_FLIP + flip)
            assert proc.returncode == 137, ("T31 the child is killed", label, proc.returncode,
                                            proc.stderr[-800:])
            assert (Path(root) / LEASE).exists(), ("T31 the dead holder's lease is left", label)
            states = journal_states(root, TYPED_JOURNAL)
            assert sorted(states.values()) == want, ("T31 no journal work for recovery", label, states)
            assert all((Path(root) / record._opf_store.txn_record("record", name)).is_file()
                       for name, s in states.items() if s == "complete"), ("T31 the projection", label)
            result = record_cli(env, root, CREATE)
            refused(result, "were reclaimed before this operation")
            assert "operation capability's lease and active record, left by an interrupted 'record' run " \
                   "whose holder" in result[2], (label, result[2][-800:])
            assert not (Path(root) / LEASE).exists(), ("T31 the confirmed-dead lease is cleared", label)
            now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            assert now == (post if want == ["complete"] else pre), ("T31 prestate, or poststate iff "
                                                                    "COMPLETE", label)
            assert not (Path(root) / ".aiqt").exists(), ("T31 nothing under .aiqt", label)
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            record._opf_oplock.release_operation(cap)
        root = fx.case("t31-homes2-live", reference)
        cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
        try:
            before = snapshot(root)
            result = record_cli(env, root, CREATE)
            assert snapshot(root) == before, "T31 a live holder's capability is never seized"
            refused(result, "never seized")
        finally:
            record._opf_oplock.release_operation(cap)
        assert not (Path(root) / LEASE).exists(), "T31 the live holder releases cleanly"


def flip_t31():
    """A trigger that sees neither capability record (the fix-2 head's journal-only trigger): the
    dead holder's leftovers are never reclaimed."""
    return patch.object(record, "_capability_leftover_present", lambda ctx: False)


# --- T32-T34: the PR D fix-4 vectors --------------------------------------------------------------------

# Inside the killed child: the run dies inside its capability release, after the verified lease unlink and
# before the active record's (the substrate's release removes the lease first), so the active record is
# left ALONE, with no lease.
DIE_BEFORE_ACTIVE_UNLINK = """
import os
import _opf_oplock
_real_verified_unlink = _opf_oplock._verified_unlink
def _die_before_active(dir_fd, name, *a, **k):
    if name == _opf_oplock.ACTIVE_NAME:
        os._exit(137)
    return _real_verified_unlink(dir_fd, name, *a, **k)
_opf_oplock._verified_unlink = _die_before_active
"""
# Inside the killed child: the publication refuses before its transaction begins, so the run releases
# the capability on its refusal path with no journal home created.
REFUSE_PUBLISH = """
def _refusing_publish(*a, **k):
    raise record.RecordError("synthetic refusal before the transaction begins")
record._publish = _refusing_publish
"""


def active_record(root):
    return Path(_opf_oplock._st_ctl_dir(str(root))) / _opf_oplock.ACTIVE_NAME


def t32_lone_active(fx):
    """A homes-2 run killed inside its capability release, after the lease is removed and before the
    active record is, leaves a LONE active record: with no journal home yet, and with COMPLETE and its
    projection. The next run reclaims it through the confirmed-dead gate and refuses naming it, the
    operands end at the prestate, or at the poststate iff COMPLETE, .aiqt is never touched, and an
    ordinary acquisition succeeds again. The reviewed head's lease-only trigger missed it, so every
    later acquisition refused the stale active record with no recovery."""
    env = fx.env
    base = fx.case("t32-homes2-base")
    with _self_test_homes2_active(base):
        reference = fx.case("t32-homes2-reference", base)
        proc = child(env, reference, CREATE, flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-1600:])
        post = dict((rel, read(reference, rel)) for rel in RECORD_OPERANDS)
        for label, flip, want in (("no-journal", REFUSE_PUBLISH, []),
                                  ("complete-projected", "", ["complete"])):
            root = fx.case("t32-homes2-" + label, base)
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            proc = child(env, root, CREATE, flip=HOMES2_CHILD_FLIP + flip + DIE_BEFORE_ACTIVE_UNLINK)
            assert proc.returncode == 137, ("T32 the child is killed in its release", label, proc.returncode,
                                            proc.stderr[-800:])
            assert not (Path(root) / LEASE).exists(), ("T32 the lease leg was removed", label)
            assert active_record(root).is_file(), ("T32 the active record is left alone", label)
            states = journal_states(root, TYPED_JOURNAL)
            assert sorted(states.values()) == want, ("T32 no journal work for recovery", label, states)
            assert all((Path(root) / record._opf_store.txn_record("record", name)).is_file()
                       for name, s in states.items() if s == "complete"), ("T32 the projection", label)
            result = record_cli(env, root, CREATE)
            refused(result, "were reclaimed before this operation")
            assert "operation capability's active record, left by an interrupted 'record' run whose " \
                   "holder" in result[2], (label, result[2][-800:])
            assert not active_record(root).exists(), ("T32 the confirmed-dead active record is cleared", label)
            now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            assert now == (post if want == ["complete"] else pre), ("T32 prestate, or poststate iff "
                                                                    "COMPLETE", label)
            assert not (Path(root) / ".aiqt").exists(), ("T32 nothing under .aiqt", label)
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            record._opf_oplock.release_operation(cap)


def flip_t32():
    """A trigger that sees the capability lease alone (the reviewed head's lease-only trigger): the lone
    active record is never reclaimed."""
    return patch.object(record, "_capability_active_present", lambda ctx: False)


def t33_journal_rederived_under_capability(fx):
    """The journal state recovery acts on and reports is re-derived under the HELD capability, never
    taken from the unheld trigger read. A live holder's lease triggers recovery while no journal home
    exists; between that read and the recovery acquisition the holder releases and a peer run killed at
    its first apply leaves an OPEN transaction. The recovering run rolls that transaction back and names
    it, the operands end at the prestate, and it never reports no journal work pending. The reviewed
    head planned from the stale absence, reported nothing open, and left the transaction open."""
    env = fx.env
    base = fx.case("t33-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t33-homes2-race", base)
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        held = [record._opf_oplock.acquire_operation(str(root), record.VERB)]
        peer = {"rc": None}
        original_acquire = record._opf_oplock.acquire_operation

        def acquire_after_peer(store_root, operation, holder=None, recover=False):
            if held:
                record._opf_oplock.release_operation(held.pop())
                peer["rc"] = child(env, root, CREATE, kill="after-apply-0", flip=HOMES2_CHILD_FLIP).returncode
            return original_acquire(store_root, operation, holder=holder, recover=recover)

        try:
            assert not (Path(root) / TYPED_JOURNAL).exists(), "T33 no journal home at the trigger read"
            with patch.object(record._opf_oplock, "acquire_operation", acquire_after_peer):
                result = record_cli(env, root, CREATE)
        finally:
            if held:
                record._opf_oplock.release_operation(held.pop())
        assert peer["rc"] == 137, ("T33 the peer is killed mid-apply before the acquisition", peer)
        refused(result, "was reconciled")
        states = journal_states(root, TYPED_JOURNAL)
        assert states and all(s != "open" for s in states.values()), \
            ("T33 the peer's open transaction is reconciled by this run", states)
        assert "rolled BACK" in result[2], ("T33 the outcome names the rollback", result[2][-800:])
        assert "no record journal work pending" not in result[2], \
            ("T33 no outcome from the stale absence", result[2][-800:])
        now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        assert now == pre, "T33 the operands end at the prestate"
        assert not (Path(root) / LEASE).exists() and not active_record(root).exists(), \
            "T33 the capability is released"


def flip_t33():
    """Take the trigger read's journal absence as the plan (the fix-3 head's behaviour): the peer's
    open transaction is left open and nothing pending is reported."""
    fixed = record._reconcile_capability_journal

    def absent_as_empty(ctx):
        if record._journal._lstat_contained(ctx.root_fd, ctx.journal_rel) is not None:
            return fixed(ctx)
        with patch.object(record, "_capability_recovery_plan", lambda ctx: ([], [])):
            return fixed(ctx)
    return patch.object(record, "_reconcile_capability_journal", absent_as_empty)


RESIDUAL_MODULE = ("On a homes-2 store the recovery trigger reads the operation capability's lease and "
                   "active record UNHELD")
RESIDUAL_GUARD = "RESIDUAL: those trigger reads are unheld"
# Fix 5: each list states that what recovery acts on and reports is re-derived under the held capability,
# and that a sibling worktree's leftover is reclaimed from the checkout owning its machine store (fix 6:
# a homes-2 record run there; the journal home reopened only when present).
RESIDUAL_HELD_MODULE = ("the journal home, the plan and what recovery actually cleared are re-derived under "
                        "the held capability")
RESIDUAL_HELD_GUARD = ("the journal home re-inspected (and REOPENED when present), the plan re-read, and what "
                       "the acquisition actually cleared taken from the capability itself")
RESIDUAL_SIBLING = "a homes-2 record run in the checkout owning that store reclaims it"
RESIDUAL_BY_HAND_MODULE = "by hand for a record the gate cannot accept"
RESIDUAL_BY_HAND_GUARD = "manual intervention for a record the gate cannot accept"


def residual_texts():
    """(the module docstring, the record guard's docstring), whitespace-normalized."""
    return (" ".join((_optlevel.source_docstring(record.__file__) or "").split()),
            " ".join((_optlevel.source_docstring(record.__file__, "_reconcile_capability_journal") or "").split()))


def t34_residual_disclosed(fx):
    """The module residual list and the record guard each disclose what the trigger leaves: the unheld
    trigger read, with what recovery acts on and reports re-derived under the held capability, and the
    leftovers the substrate's recovery gate refuses, a sibling worktree's being reclaimed from the
    checkout owning its machine store rather than by hand. The reviewed head claimed every refused
    leftover needs clearing by hand."""
    module, guard = residual_texts()
    assert RESIDUAL_MODULE in module, "T34 the module residual list discloses the unheld trigger read"
    assert RESIDUAL_HELD_MODULE in module, "T34 the module residual list states the held re-derivation"
    assert RESIDUAL_SIBLING in module, "T34 the module residual list names the owning-checkout reclaim"
    assert RESIDUAL_BY_HAND_MODULE in module, "T34 the module residual list names the manual clearance"
    assert RESIDUAL_GUARD in guard, "T34 the record guard discloses the unheld trigger read"
    assert RESIDUAL_HELD_GUARD in guard, "T34 the record guard states the held re-derivation"
    assert RESIDUAL_SIBLING in guard, "T34 the record guard names the owning-checkout reclaim"
    assert RESIDUAL_BY_HAND_GUARD in guard, "T34 the record guard names the refused leftovers"


def flip_t34():
    """Read the texts with the disclosure removed."""
    original = residual_texts
    pins = (RESIDUAL_MODULE, RESIDUAL_GUARD, RESIDUAL_HELD_MODULE, RESIDUAL_HELD_GUARD, RESIDUAL_SIBLING,
            RESIDUAL_BY_HAND_MODULE, RESIDUAL_BY_HAND_GUARD)

    def stripped():
        texts = []
        for text in original():
            for pin in pins:
                text = text.replace(pin, "")
            texts.append(text)
        return tuple(texts)
    return patch.object(sys.modules[__name__], "residual_texts", stripped)


# --- T35, T36: the PR D fix-5 vectors --------------------------------------------------------------------

def t35_released_holder_not_reconciled(fx):
    """A LIVE holder's capability triggers recovery and the holder releases between the trigger read and
    the recovery acquisition, with no peer: the held acquisition clears nothing and the held plan finds
    nothing, so the run reports no reconciliation and continues, here to the claim-seam refusal a plain
    homes-2 run gives, with the operands untouched, no journal home created, and the capability released.
    The reviewed head worded the outcome from the unheld trigger read and refused claiming an
    interrupted run was reconciled."""
    env = fx.env
    base = fx.case("t35-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t35-homes2-released", base)
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        held = [record._opf_oplock.acquire_operation(str(root), record.VERB)]
        original_acquire = record._opf_oplock.acquire_operation

        def acquire_after_release(store_root, operation, holder=None, recover=False):
            if held:
                record._opf_oplock.release_operation(held.pop())
            return original_acquire(store_root, operation, holder=holder, recover=recover)

        try:
            with patch.object(record._opf_oplock, "acquire_operation", acquire_after_release):
                result = record_cli(env, root, CREATE)
        finally:
            if held:
                record._opf_oplock.release_operation(held.pop())
        assert "was reconciled" not in result[2] and "were reclaimed" not in result[2], \
            ("T35 no reconciliation is reported", result[2][-800:])
        refused(result, "is not active in this build")
        now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        assert now == pre, "T35 the operands are untouched"
        assert not (Path(root) / ".working/journals").exists(), "T35 no journal home is created"
        assert not (Path(root) / LEASE).exists() and not active_record(root).exists(), \
            "T35 the capability is released"


def flip_t35():
    """The recovery acquisition reports a reclaim it did not make (the reviewed head's outcome, taken
    from the unheld trigger read): the released holder reads as a reconciled interruption."""
    original = record._opf_oplock.acquire_operation

    def reporting(store_root, operation, holder=None, recover=False):
        cap = original(store_root, operation, holder=holder, recover=recover)
        if recover:
            cap.recovered = ("lease", "active record")
        return cap
    return patch.object(record._opf_oplock, "acquire_operation", reporting)


def t36_journal_home_reopened(fx):
    """The journal home is REOPENED under the held capability, never read through the trigger read's
    descriptor. An empty typed journal home and a live holder's lease trigger recovery; between that read
    and the recovery acquisition the holder releases, the home is renamed aside, and a peer run killed at
    its first apply leaves an OPEN transaction in a replacement home. The recovering run rolls that
    transaction back and names it, and the operands end at the prestate. The reviewed head planned
    through the stale descriptor, reported no transaction open, and left the transaction open."""
    env = fx.env
    base = fx.case("t36-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t36-homes2-replaced", base)
        (Path(root) / TYPED_JOURNAL).mkdir(parents=True)
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        held = [record._opf_oplock.acquire_operation(str(root), record.VERB)]
        peer = {"rc": None}
        original_acquire = record._opf_oplock.acquire_operation

        def acquire_after_replacement(store_root, operation, holder=None, recover=False):
            if held:
                record._opf_oplock.release_operation(held.pop())
                os.rename(Path(root) / TYPED_JOURNAL, Path(root) / ".git" / "t36-journal-aside")
                peer["rc"] = child(env, root, CREATE, kill="after-apply-0", flip=HOMES2_CHILD_FLIP).returncode
            return original_acquire(store_root, operation, holder=holder, recover=recover)

        try:
            with patch.object(record._opf_oplock, "acquire_operation", acquire_after_replacement):
                result = record_cli(env, root, CREATE)
        finally:
            if held:
                record._opf_oplock.release_operation(held.pop())
        assert peer["rc"] == 137, ("T36 the peer is killed mid-apply before the acquisition", peer)
        refused(result, "was reconciled")
        states = journal_states(root, TYPED_JOURNAL)
        assert states and all(s != "open" for s in states.values()), \
            ("T36 the replacement home's open transaction is reconciled by this run", states)
        assert "rolled BACK" in result[2], ("T36 the outcome names the rollback", result[2][-800:])
        now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        assert now == pre, "T36 the operands end at the prestate"
        assert not (Path(root) / LEASE).exists() and not active_record(root).exists(), \
            "T36 the capability is released"


def flip_t36():
    """Plan through the descriptor the trigger read opened (the reviewed head's behaviour): the replaced
    home's open transaction is never seen."""
    fixed = record._reconcile_capability_journal

    def stale_descriptor(ctx):
        try:
            jr_fd = record._journal.open_journal_root_fd(ctx.root_fd, ctx.journal_rel)
        except (record._journal.JournalError, OSError):
            return fixed(ctx)
        try:
            with patch.object(record, "_capability_recovery_plan",
                              lambda ctx: record._capability_journal_plan(ctx, jr_fd)):
                return fixed(ctx)
        finally:
            os.close(jr_fd)
    return patch.object(record, "_reconcile_capability_journal", stale_descriptor)


# --- T37-T41: the PR D fix-6 vectors --------------------------------------------------------------------

# A child that takes the operation capability for {operation} and dies holding it, leaving its lease and
# active record with no journal work.
DIE_HOLDING = """
import os, sys
sys.path.insert(0, {tools!r})
import opf
assert opf._bootstrap() == 0
import _opf_oplock
_opf_oplock.acquire_operation(sys.argv[1], {operation!r})
os._exit(137)
"""


def die_holding(env, root, operation):
    script = DIE_HOLDING.format(tools=str(TOOLS), operation=operation)
    return subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)], capture_output=True,
                          text=True, timeout=180, env=dict(env.vars)).returncode


def capability_records(root):
    return (Path(root) / LEASE).exists(), active_record(root).exists()


def t37_reclaim_names_operation(fx):
    """A homes-2 capability leftover of ANOTHER operation (an `ingest` holder killed holding it, no record
    journal present) is reclaimed and attributed to the operation its record names, never to opf record:
    the lead describes a capability reclamation, the outcome names 'ingest' and says the record run does
    not examine that operation's journal work. The reviewed head reported "an interrupted opf record run
    was reconciled"."""
    env = fx.env
    base = fx.case("t37-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t37-homes2-ingest", base)
        assert die_holding(env, root, "ingest") == 137, "T37 the ingest holder is killed holding it"
        assert capability_records(root) == (True, True), "T37 its lease and active record are left"
        result = record_cli(env, root, CREATE)
        refused(result, "the operation capability records an interrupted run left were reclaimed before "
                        "this operation")
        err = result[2]
        assert "interrupted opf record run" not in err, ("T37 never attributed to opf record", err[-800:])
        assert "left by an interrupted 'ingest' run whose holder" in err, ("T37 the operation named", err[-800:])
        assert "any journal work of the 'ingest' operation is not examined here" in err, err[-800:]
        assert capability_records(root) == (False, False), "T37 the confirmed-dead records are cleared"
        assert not (Path(root) / TYPED_JOURNAL).exists(), "T37 no journal home is created"


def flip_t37():
    """Attribute every reclaim to opf record (the reviewed head's wording)."""
    original = record._opf_oplock.acquire_operation

    def as_record(store_root, operation, holder=None, recover=False):
        cap = original(store_root, operation, holder=holder, recover=recover)
        if cap.recovered:
            cap.recovered_operation = record.VERB
        return cap
    return patch.object(record._opf_oplock, "acquire_operation", as_record)


def t38_staging_removal_disclosed(fx):
    """A recovery acquisition that removes a staging leftover and THEN refuses (a lone lease with no
    paired active record) names the removed leftover and says only that no record operand, journal or
    projection was written; the lone lease is kept. A contended acquisition, which removes nothing, says
    so. The reviewed head said "Nothing was written" after deleting the staging file."""
    env = fx.env
    base = fx.case("t38-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t38-homes2-lone-lease", base)
        cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
        lease_bytes = (Path(root) / LEASE).read_bytes()
        record._opf_oplock.release_operation(cap)
        (Path(root) / LEASE).write_bytes(lease_bytes)
        staging = _opf_oplock._staging_name(opf_check.LEASE_NAME)
        (Path(root) / MACH / staging).write_bytes(b"a torn lease publication")
        result = record_cli(env, root, CREATE)
        refused(result, "a stale lease exists with no paired active record")
        err = result[2]
        assert "Nothing was written" not in err, ("T38 no nothing-written claim", err[-800:])
        assert ("No record operand, record journal or projection was written, but before it failed the "
                "acquisition removed lease staging leftover " + staging) in err, ("T38 the removal named",
                                                                                 err[-800:])
        assert (Path(root) / LEASE).exists() and not (Path(root) / MACH / staging).exists(), \
            "T38 the lone lease is kept and the staging leftover removed"
        root = fx.case("t38-homes2-contended", base)
        held = record._opf_oplock.acquire_operation(str(root), record.VERB)
        try:
            before = snapshot(root)
            result = record_cli(env, root, CREATE)
            assert snapshot(root) == before, "T38 a contended acquisition removes nothing"
        finally:
            record._opf_oplock.release_operation(held)
        refused(result, "and the acquisition removed nothing (fail-closed)")


def flip_t38():
    """The staging removal goes unreported (the reviewed head's acquisition reported no removal)."""
    original = _opf_oplock._remove_staging_garbage
    return patch.object(_opf_oplock, "_remove_staging_garbage",
                        lambda dir_fd, name, label, removed=None: original(dir_fd, name, label))


def flip_unreported_deletes():
    """The recovery deletes go unreported (the reviewed head's acquisition reported none on a failure)."""
    original = _opf_oplock._recover_stale
    return patch.object(_opf_oplock, "_recover_stale",
                        lambda dir_fd, name, ident, expected_bytes, label, removed=None:
                        original(dir_fd, name, ident, expected_bytes, label))


def t39_deletes_then_failure_disclosed(fx):
    """A recovery acquisition that deletes a confirmed-dead holder's lease and active record and THEN
    fails (its own publication refuses) names both deleted records and the holder's recorded operation,
    never claiming nothing was written. The reviewed head said "Nothing was written" with both gone."""
    env = fx.env
    base = fx.case("t39-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t39-homes2-dead", base)
        proc = child(env, root, CREATE, flip=HOMES2_CHILD_FLIP + DIE_BEFORE_PUBLISH)
        assert proc.returncode == 137, ("T39 the holder is killed", proc.returncode, proc.stderr[-800:])
        assert capability_records(root) == (True, True), "T39 its lease and active record are left"

        def failing_publication(*a, **k):
            raise _opf_oplock.OpLockError("synthetic publication failure after the recovery deletes")
        with patch.object(_opf_oplock, "_create_control_file", failing_publication):
            result = record_cli(env, root, CREATE)
        refused(result, "synthetic publication failure after the recovery deletes")
        err = result[2]
        assert "Nothing was written" not in err, ("T39 no nothing-written claim", err[-800:])
        assert ("before it failed the acquisition removed the stale operation capability lease and active "
                "record of a holder its recovery gate confirmed dead (its recorded operation 'record')") \
            in err, ("T39 the deletes named", err[-800:])
        assert capability_records(root) == (False, False), "T39 both records were deleted"


def t40_held_refusal_discloses(fx):
    """A refusal raised under the held recovery capability (an intervening operand edit over the open
    transaction a killed run left) names what the acquisition had already removed -- the dead holder's
    lease and active record -- beside its own journal-and-operands wording; the transaction stays open
    and the edit is kept. The reviewed head said "Nothing was written" with both records gone."""
    env = fx.env
    base = fx.case("t40-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t40-homes2-edit", base)
        proc = child(env, root, CREATE, kill="after-apply-0", flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 137, ("T40 the holder is killed mid-apply", proc.returncode,
                                        proc.stderr[-800:])
        edited = read(root, COUNTERS) + b"# an intervening edit\n"
        (Path(root) / COUNTERS).write_bytes(edited)
        result = record_cli(env, root, CREATE)
        refused(result, "Nothing was written to the journal or to any operand; both are left exactly as found")
        err = result[2]
        assert ("Before this refusal, under the held capability, the recovery acquisition removed the stale "
                "operation capability lease and active record of a holder its recovery gate confirmed dead") \
            in err, ("T40 the removal named", err[-800:])
        assert capability_records(root) == (False, False), "T40 the recovery acquisition cleared both records"
        assert any(s == "open" for s in journal_states(root, TYPED_JOURNAL).values()), \
            "T40 the transaction is left open"
        assert read(root, COUNTERS) == edited, "T40 the intervening edit is kept"


# Fix 6: what each residual list states, exactly (the reviewed head's lists overstated when another run
# or the holder's death is needed, promised nothing written, presented the refused leftovers as complete,
# and named a sibling's owning checkout regardless of its generation).
# Fix 7 retired the fix-6 "reclaimed and reconciled by this run" and "Only a run ..." sentences (T46 pins
# the cases that leave work for a later trigger instead).
FIX6_BOTH = ("a homes-1 record run there never reclaims a capability record",
             "the refusal naming whatever the acquisition removed before it refused",
             "cannot be unlinked or fsynced during the recovery deletes")
FIX6_MODULE = ("until it releases normally, or exits and a later run confirms it dead",
               "NOT an exhaustive list of the gate's and the deletes' refusals")
FIX6_GUARD = ("the holder releases normally, or exits and a later run confirms it dead",
              "That last group is NOT exhaustive")
FIX6_PLAN = "REOPENED whenever it is present (an absent home is re-read as absent under the held capability"
FIX6_RETIRED = ("neither reclaims nor reconciles", "refuses this run with nothing written",
                "refuses record runs, nothing written", "the journal home is ALWAYS re-inspected and REOPENED")


def fix6_texts():
    """(the module docstring, the record guard's, the held plan's), whitespace-normalized."""
    module, guard = residual_texts()
    return module, guard, " ".join((_optlevel.source_docstring(record.__file__, "_capability_recovery_plan") or "").split())


def t41_residuals_exact(fx):
    """Each residual list states exactly when another run is needed (fix 7: T46 pins the cases that leave
    work for a later trigger): a possibly-live holder refuses until it releases normally or exits; the
    refused-leftover list is marked not exhaustive; a refusal names what the acquisition removed; a
    sibling's leftover is reclaimed only by a homes-2 run in its owning checkout; the held plan reopens
    the journal home only when present. Two of those statements are exercised: a homes-1 record run
    never reclaims a dead capability holder's records, and a live holder that releases normally lets
    the next run continue."""
    module, guard, plan = fix6_texts()
    for pin in FIX6_BOTH + FIX6_MODULE:
        assert pin in module, ("T41 the module residual list states", pin)
    for pin in FIX6_BOTH + FIX6_GUARD:
        assert pin in guard, ("T41 the record guard states", pin)
    assert FIX6_PLAN in plan, "T41 the held plan reopens the journal home only when present"
    for retired in FIX6_RETIRED:
        assert retired not in module and retired not in guard and retired not in plan, \
            ("T41 the overstated wording is gone", retired)
    env = fx.env
    root = fx.case("t41-homes1-owner")
    assert die_holding(env, root, record.VERB) == 137, "T41 the capability holder is killed holding it"
    result = record_cli(env, root, CREATE)
    refused(result, "holds the single-writer lease")
    assert capability_records(root) == (True, True), "T41 a homes-1 record run never reclaims the records"
    base = fx.case("t41-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t41-homes2-live", base)
        held = record._opf_oplock.acquire_operation(str(root), record.VERB)
        try:
            refused(record_cli(env, root, CREATE), "never seized")
        finally:
            record._opf_oplock.release_operation(held)
        result = record_cli(env, root, CREATE)
        refused(result, "is not active in this build")
        assert "was reconciled" not in result[2] and "were reclaimed" not in result[2], result[2][-800:]


def flip_t41():
    """Read the texts with the fix-6 statements removed."""
    original = fix6_texts
    pins = FIX6_BOTH + FIX6_MODULE + FIX6_GUARD + (FIX6_PLAN,)

    def stripped():
        texts = []
        for text in original():
            for pin in pins:
                text = text.replace(pin, "")
            texts.append(text)
        return tuple(texts)
    return patch.object(sys.modules[__name__], "fix6_texts", stripped)


# --- T42-T46: the PR D fix-7 vectors --------------------------------------------------------------------

# The four reports an acquisition's OpLockError carries of what it removed (PR D fix 6; fix 7).
REPORTS = ("recovered", "recovered_operation", "staging_removed", "created_removed")


def own_staging_pattern(name, label):
    """A regular expression for the report of a publication's own retired staging name (PR D fix 7)."""
    return (re.escape("the staging name ." + name + _opf_oplock._STAGING_MARKER) + "[0-9a-f]{32}"
            + re.escape(" of its own {} publication".format(label)))


def t42_own_unlinks_disclosed(fx):
    """A recovery acquisition whose lease publication fails after its active record's published names the
    unlinks it performed itself -- the retired staging name of that publication and the unwind's removal
    of the new active record -- never "the acquisition removed nothing". The live holder whose lease
    triggered recovery releases before the acquisition (as T35), so nothing stale is removed. The reviewed
    head said "the acquisition removed nothing" after both unlinks."""
    env = fx.env
    base = fx.case("t42-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t42-homes2-unwind", base)
        held = [record._opf_oplock.acquire_operation(str(root), record.VERB)]
        original_acquire = record._opf_oplock.acquire_operation
        original_publish = _opf_oplock._create_control_file

        def acquire_after_release(store_root, operation, holder=None, recover=False):
            if held:
                record._opf_oplock.release_operation(held.pop())
            return original_acquire(store_root, operation, holder=holder, recover=recover)

        def lease_publication_fails(dir_fd, name, *a, **k):
            if name == opf_check.LEASE_NAME:
                raise _opf_oplock.OpLockError("synthetic lease publication failure")
            return original_publish(dir_fd, name, *a, **k)
        try:
            with patch.object(record._opf_oplock, "acquire_operation", acquire_after_release), \
                    patch.object(_opf_oplock, "_create_control_file", lease_publication_fails):
                result = record_cli(env, root, CREATE)
        finally:
            if held:
                record._opf_oplock.release_operation(held.pop())
        refused(result, "synthetic lease publication failure")
        err = result[2]
        assert "the acquisition removed nothing" not in err, ("T42 no removed-nothing claim", err[-800:])
        assert re.search("but before it failed the acquisition removed " + own_staging_pattern(
            _opf_oplock.ACTIVE_NAME, "active record") + re.escape("; its own new active record (unwind)"),
            err), ("T42 both unlinks named", err[-800:])
        assert capability_records(root) == (False, False), "T42 the unwind left no capability record"


def flip_t42_publication():
    """A publication's staging retirement goes unreported (the reviewed head's publication kept no list)."""
    original = _opf_oplock._create_control_file
    return patch.object(_opf_oplock, "_create_control_file",
                        lambda dir_fd, name, payload, label, owner=None, publisher_pid=None, removed=None:
                        original(dir_fd, name, payload, label, owner=owner, publisher_pid=publisher_pid))


def flip_t42_unwind():
    """The unwind's own removals go unreported (the reviewed head's verified unlink kept no list)."""
    original = _opf_oplock._verified_unlink
    return patch.object(_opf_oplock, "_verified_unlink",
                        lambda dir_fd, name, ident, expected_bytes, label, outcome=None, removed=None:
                        original(dir_fd, name, ident, expected_bytes, label, outcome))


def t43_publish_names_removals(fx):
    """A direct homes-2 publication (T21's setup) whose own acquisition removes a lease staging leftover
    and then refuses the stale lone lease names that removal, never a blanket "nothing written"; the lone
    lease is kept, the staging leftover is gone, and no journal home or operand is written. The reviewed
    head said "nothing written" after deleting the staging file."""
    root = fx.case("t43-homes2-publish")
    with _self_test_homes2_active(root):
        req = record.parse_request(CREATE + ["--root", str(root)])
        res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
        assert res.status == record._opf_store.RESOLVED, res
        root_fd = record._opf_store._open_dir_nofollow(res.store_root)
        try:
            ctx = record.Context(res, str(root), root_fd)
            ctx.journal_rel = record._record_journal_rel(record._probe_homes(ctx))
            assert ctx.journal_rel == TYPED_JOURNAL, ("T43 the probed journal home", ctx.journal_rel)
            record._load_manifest(ctx)
            ctx.counters = record._read_operand(root_fd, ctx.rel(opf_check.COUNTERS_NAME))
            ctx.version = record._read_operand(root_fd, ctx.rel(opf_check.VERSION_NAME)).model
            ctx.worklog = record._read_operand(root_fd, ctx.rel(opf_check.WORKLOG_NAME))
            operand = record._read_operand(root_fd, record._operand_rel(req, ctx))
            seam = record.claim_ids
            with patch.object(record, "claim_ids", lambda homes, high, demand, known_complete:
                              seam(1, high, demand, known_complete)):
                plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            lease_bytes = (Path(root) / LEASE).read_bytes()
            record._opf_oplock.release_operation(cap)
            (Path(root) / LEASE).write_bytes(lease_bytes)
            staging = _opf_oplock._staging_name(opf_check.LEASE_NAME)
            (Path(root) / MACH / staging).write_bytes(b"a torn lease publication")
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            try:
                record._publish(ctx, plan, "create")
            except record.RecordError as exc:
                message = str(exc)
            else:
                raise AssertionError("T43 the publication must refuse the stale lone lease")
        finally:
            os.close(root_fd)
    assert "stale operation record(s) under a free anchor (lease.toml)" in message, message
    assert "nothing written" not in message, ("T43 no blanket nothing-written claim", message)
    assert ("No record operand, record journal or projection was written, but before it failed the "
            "acquisition removed lease staging leftover " + staging) in message, ("T43 the removal named",
                                                                                 message)
    assert (Path(root) / LEASE).exists() and not (Path(root) / MACH / staging).exists(), \
        "T43 the lone lease is kept and the staging leftover removed"
    assert not (Path(root) / TYPED_JOURNAL).exists(), "T43 no journal home is created"
    assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, "T43 the operands are untouched"


def flip_t43():
    """The direct publication's refusal keeps the reviewed head's blanket wording."""
    return patch.object(record, "_acquisition_removals", lambda exc: "nothing written")


def t44_continuation_refusal_discloses(fx):
    """A forked-continuation refusal of the recovery acquisition names what the acquisition removed before
    the fork -- the confirmed-dead holder's lease and active record, and its own publications' retired
    staging names -- never "the acquisition removed nothing". The hand-off is taken down its continuation
    branch in this process (_refuse_continuation), as a signal handler that forks and returns in the child
    takes it there. The reviewed head's refusal carried no report."""
    env = fx.env
    base = fx.case("t44-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t44-homes2-continuation", base)
        assert die_holding(env, root, record.VERB) == 137, "T44 the capability holder is killed holding it"
        assert capability_records(root) == (True, True), "T44 its lease and active record are left"
        with patch.object(_opf_oplock, "_handoff",
                          lambda cap, acquirer_pid: _opf_oplock._refuse_continuation(cap, acquirer_pid)):
            result = record_cli(env, root, CREATE)
        refused(result, "acquisition refused in a forked continuation")
        err = result[2]
        assert "and the acquisition removed nothing" not in err, ("T44 no removed-nothing claim", err[-800:])
        assert re.search(re.escape(
            "but before it failed the acquisition removed the stale operation capability lease and active "
            "record of a holder its recovery gate confirmed dead (its recorded operation 'record'); ")
            + own_staging_pattern(_opf_oplock.ACTIVE_NAME, "active record") + "; "
            + own_staging_pattern(opf_check.LEASE_NAME, "lease"), err), ("T44 the removals named", err[-800:])


def flip_t44():
    """The continuation refusal carries no report (the reviewed head's _refuse_continuation)."""
    original = _opf_oplock._refuse_continuation

    def untagged(cap, acquirer_pid):
        try:
            original(cap, acquirer_pid)
        except _opf_oplock.OpLockError as exc:
            raise _opf_oplock.OpLockError(str(exc)) from None
    return patch.object(_opf_oplock, "_refuse_continuation", untagged)


def t45_unreported_removals_unknown(fx):
    """A recovery acquisition error that carries no report of what the acquisition removed (as
    acquire_operation's validation raises carry none) is never worded as an acquisition that removed
    nothing: the refusal says what it removed is not known. The reviewed head read a missing report as
    nothing removed."""
    env = fx.env
    base = fx.case("t45-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t45-homes2-unreported", base)
        assert die_holding(env, root, record.VERB) == 137, "T45 the capability holder is killed holding it"
        original = record._opf_oplock.acquire_operation

        def unreported(store_root, operation, holder=None, recover=False):
            if recover:
                raise _opf_oplock.OpLockError("synthetic refusal carrying no removal report")
            return original(store_root, operation, holder=holder, recover=recover)
        with patch.object(record._opf_oplock, "acquire_operation", unreported):
            result = record_cli(env, root, CREATE)
        refused(result, "synthetic refusal carrying no removal report")
        err = result[2]
        assert "removed nothing" not in err, ("T45 no removed-nothing claim", err[-800:])
        assert ("No record operand, record journal or projection was written; this error carries no report "
                "of what the acquisition removed before it failed, so whether it removed anything is not "
                "known here") in err, ("T45 the removals are said to be unknown", err[-800:])
        assert capability_records(root) == (True, True), "T45 the records are left"


def flip_t45():
    """A missing report reads as nothing removed (the reviewed head's _acquisition_removals)."""
    original = record._acquisition_removals

    def as_empty(exc):
        for name, empty in zip(REPORTS, ((), None, (), ())):
            if not hasattr(exc, name):
                setattr(exc, name, empty)
        return original(exc)
    return patch.object(record, "_acquisition_removals", as_empty)


# Fix 7: what each residual list, acquire_operation's docstring and the refusal helper state, exactly (the
# reviewed head claimed a dead run's leftover is always reclaimed and reconciled, that "Only" two cases
# leave work for a later trigger, that a multiply-linked staging leftover is refused, that every
# acquisition error carries the reports, and that only a pre-body error lacks one). Fix 8: the closed
# "in each of these cases" list is replaced by the rule the code enforces, the cases kept as examples
# marked NOT exhaustive, a live run's own failed publication among them. Fix 9: the rule scopes the
# leftovers to what a run actually left (none from a publication refused before its transaction opened)
# and says the next trigger reconciles them only as far as each step succeeds (T52).
RULE_FIX8 = ("The rule the code enforces: any run that fails or dies before its publication and release "
             "complete leaves its records and journal work for the next run's trigger, which reconciles "
             "under the held capability")
RULE_FIX9 = ("The rule the code enforces: whatever a run that fails or dies before its publication and "
             "release complete leaves -- the capability records it did not remove, and any record-journal "
             "work it left (an open transaction, or a terminal one missing its projection; a publication "
             "refused before its transaction opened leaves none) -- is left for the next run's trigger, "
             "which reconciles it under the held capability as far as each step succeeds")
FIX7_BOTH = ("left is handled by this run only as far as each step succeeds: its lease and active record "
             "are reclaimed when the recovery acquisition succeeds, and its record-journal work (an open "
             "transaction, or a terminal one missing its projection) is reconciled when the held plan and "
             "each reconciliation succeed",
             RULE_FIX9,
             "Cases that leave such work include (examples, NOT exhaustive)",
             "and a live run's own publication that fails after its transaction opened and before it "
             "completes, which leaves an open transaction, or a terminal one missing its projection, for "
             "that trigger even when the run's own release succeeds",
             "or fails, after its deletes included, which leaves every pending transaction and any record it "
             "did not remove",
             "which leaves every pending transaction while the records the acquisition removed stay removed",
             "a transaction that cannot be reconciled, which leaves it and each one planned after it",
             "a failed release of the recovery capability, which leaves whichever of this run's own records it "
             "did not remove",
             "the journal work of an operation other than record (an 'ingest' holder's), which this verb never "
             "examines",
             "or a name the acquisition had itself created: a publication's staging name, or a new record its "
             "unwind removed), or saying that is not known when the error carries no report of it",
             "a record that is not a plain singly-linked regular file, a staging leftover that is not a regular "
             "file (a multiply-linked one IS removed")
FIX7_MODULE = ("a trigger read that fails (the run refuses before any acquisition)",)
FIX7_GUARD = ("a trigger read that fails (this run refuses before any acquisition)",)
FIX7_ACQUIRE = ("and the forked-continuation refusal (_refuse_continuation). The validation raises carry "
                "NONE: the containment probe, the `recover` type check, the empty-nodename refusal, the holder "
                "and operation field checks, and _acquire_body's store-resolution refusal",)
FIX7_REMOVALS = ("An error that carries NO report",
                 "cannot say what was removed, so the sentence says that is not known instead")
FIX7_RETIRED = ("is reclaimed and reconciled by this run", "Only a run that takes the capability",
                "a record or its staging leftover that is not a plain singly-linked regular file",
                "carrying the same three reports", "carries no report; no removal runs there",
                "left for a later run's trigger in each of these cases", RULE_FIX8,
                "own publication that fails before it completes, which leaves an open transaction")


def fix7_texts():
    """(the module docstring, the record guard's, acquire_operation's, _acquisition_removals'),
    whitespace-normalized."""
    module, guard = residual_texts()
    return (module, guard, " ".join((_optlevel.source_docstring(_opf_oplock.__file__, "acquire_operation") or "").split()),
            " ".join((_optlevel.source_docstring(record.__file__, "_acquisition_removals") or "").split()))


def t46_disclosures_exact(fx):
    """Each residual list states the rule deciding what is left for a later trigger, with its cases
    kept as examples marked NOT exhaustive (fix 8: no closed "in each of these cases" list; a live
    run's own failed publication among the examples), a refusal's own-name removals and its unknown
    case, and staging-leftover cleanup as it is (a
    multiply-linked staging name IS removed); acquire_operation's docstring names exactly which errors
    carry the removal reports (its validation raises do not); the refusal helper says an unreported error
    is unknown. Two of those statements are exercised: a validation raise carries no report, and a
    staging name hard-linked to a dead holder's active record is removed and named."""
    module, guard, acquire, removals = fix7_texts()
    for pin in FIX7_BOTH + FIX7_MODULE:
        assert pin in module, ("T46 the module residual list states", pin)
    for pin in FIX7_BOTH + FIX7_GUARD:
        assert pin in guard, ("T46 the record guard states", pin)
    for pin in FIX7_ACQUIRE:
        assert pin in acquire, ("T46 acquire_operation's docstring states", pin)
    for pin in FIX7_REMOVALS:
        assert pin in removals, ("T46 _acquisition_removals' docstring states", pin)
    for retired in FIX7_RETIRED:
        assert all(retired not in text for text in (module, guard, acquire, removals)), \
            ("T46 the overstated wording is gone", retired)
    env = fx.env
    base = fx.case("t46-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t46-homes2-linked-staging", base)
        try:
            _opf_oplock.acquire_operation(str(root), record.VERB, recover="yes")
        except _opf_oplock.OpLockError as exc:
            assert not any(hasattr(exc, name) for name in REPORTS), "T46 a validation raise carries no report"
        else:
            raise AssertionError("T46 a non-bool recover must refuse")
        assert die_holding(env, root, "ingest") == 137, "T46 the ingest holder is killed holding it"
        staging = active_record(root).parent / _opf_oplock._staging_name(_opf_oplock.ACTIVE_NAME)
        os.link(active_record(root), staging)
        assert os.lstat(staging).st_nlink == 2, "T46 the staging name shares the active record's inode"
        result = record_cli(env, root, CREATE)
        refused(result, "the acquisition also removed active record staging leftover " + staging.name)
        assert not os.path.lexists(staging) and capability_records(root) == (False, False), \
            "T46 the multiply-linked staging name is removed and both records reclaimed"


def flip_t46():
    """Read the texts with the fix-7 statements removed."""
    original = fix7_texts
    pins = FIX7_BOTH + FIX7_MODULE + FIX7_GUARD + FIX7_ACQUIRE + FIX7_REMOVALS

    def stripped():
        texts = []
        for text in original():
            for pin in pins:
                text = text.replace(pin, "")
            texts.append(text)
        return tuple(texts)
    return patch.object(sys.modules[__name__], "fix7_texts", stripped)


# --- T47-T49: the PR D fix-8 vectors --------------------------------------------------------------------

# Fix 8: each outcome and refusal message states only what the code checked (the reviewed head certified
# any COMPLETE publication as present in the working tree, read "every terminal one carrying its
# projection" without _projection_missing's grammar exclusion, called a preparation that had already
# created the journal directories "nothing written", and introduced the record guard with a home-absent
# sentence its own capability recovery contradicts).
T47_GRAMMAR = ("every terminal one in the homes record grammar having an entry at its projection path, "
               "which is not validated as its projection here; a name outside that grammar has no "
               "projection path and is not checked")
# The fix-8 wording T53 retires: a projection path's entry read as the projection itself.
T47_GRAMMAR_FIX8 = ("every terminal one in the homes record grammar carrying its projection; a name outside "
                    "that grammar has no projection path and is not checked")
T47_JOURNAL = ("A COMPLETE transaction's publication was applied when it committed; its operands' "
               "current bytes are not re-checked by this recovery (unexplained operands are checked "
               "only for OPEN transactions)")
T47_RETIRED = ("Any publication a COMPLETE transaction made is present in the working tree",
               "every terminal one carrying its projection)", "grammar carrying its projection")


def t47_reclaimed_outcome_scoped(fx):
    """The no-pending reclaimed outcome describes journal state only: its terminal-projection reading
    names the grammar exclusion (_projection_missing checks a projection only for a homes-grammar
    name), and its COMPLETE sentence speaks for the journal's commit, never for the operands' current
    bytes, keeping the run-opf-doctor advice. The reviewed head certified working-tree poststate it
    had not read."""
    env = fx.env
    base = fx.case("t47-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t47-homes2-complete", base)
        proc = child(env, root, CREATE, flip=HOMES2_CHILD_FLIP + DIE_AT_RENDER)
        assert proc.returncode == 137, ("T47 the child is killed at the render", proc.returncode,
                                        proc.stderr[-800:])
        result = record_cli(env, root, CREATE)
        refused(result, "were reclaimed before this operation")
        assert T47_GRAMMAR in result[2], ("T47 the grammar exclusion is named", result[2][-1200:])
        assert T47_JOURNAL in result[2], ("T47 the outcome speaks for the journal, not current bytes",
                                          result[2][-1200:])
        assert "run opf doctor before relying on it" in result[2], \
            ("T47 the doctor advice is kept", result[2][-800:])
        for retired in T47_RETIRED:
            assert retired not in result[2], ("T47 the overstated wording is gone", retired)


def _flip_reclaimed(new, old):
    """Read _reclaimed_outcome's line with a fix-8 sentence reverted to the reviewed head's wording."""
    fixed = record._reclaimed_outcome

    def reverted(cap, pending):
        return fixed(cap, pending).replace(new, old)
    return patch.object(record, "_reclaimed_outcome", reverted)


def flip_t47_grammar():
    return _flip_reclaimed(T47_GRAMMAR, "every terminal one carrying its projection")


def flip_t47_poststate():
    return _flip_reclaimed(T47_JOURNAL,
                           "Any publication a COMPLETE transaction made is present in the working tree")


T48_QUALIFIED = ("preparation may already have created its directories, and no operand, journal entry "
                 "or lock was written (fail-closed)")


def t48_prepare_names_created(fx):
    """The homes-1 journal-prepare refusal names what preparation may have done: ensure_journal_dirs
    runs before open_journal_root_fd, so a failure there can follow the journal directories' creation,
    and the refusal says so instead of the reviewed head's unqualified "nothing written". The operands
    are untouched."""
    env = fx.env
    root = fx.case("t48-homes1-prepare")
    pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
    journal_dir = Path(root) / record.JOURNAL_REL
    assert not journal_dir.exists(), "T48 no journal directory before the run"

    def failing_open(root_fd, journal_rel):
        raise journal.JournalError("synthetic journal open failure")
    with patch.object(record._journal, "open_journal_root_fd", failing_open):
        result = record_cli(env, root, CREATE)
    refused(result, "cannot prepare the record journal " + record.JOURNAL_REL)
    refused(result, T48_QUALIFIED)
    assert "; nothing written (fail-closed)" not in result[2], \
        ("T48 the unqualified wording is gone", result[2][-800:])
    assert journal_dir.is_dir(), "T48 preparation created the journal directories before the refusal"
    assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, "T48 the operands are untouched"


def flip_t48():
    """Refuse with the reviewed head's unqualified nothing-written wording."""
    fixed = record.RecordError

    class Unqualified(fixed):
        def __init__(self, *args):
            super().__init__(*(a.replace(T48_QUALIFIED, "nothing written (fail-closed)")
                               if isinstance(a, str) else a for a in args))
    return patch.object(record, "RecordError", Unqualified)


# Fix 8: the record guard's introduction no longer claims an absent home means nothing written.
FIX8_GUARD = ("no record-journal work to do, and no journal write made, when the home is absent (no "
              "mkdir), or when no transaction is open AND every terminal one has an entry at its terminal "
              "projection path (existence only: _projection_missing does not validate that entry as the "
              "projection); even with no journal work pending, capability recovery may still remove a "
              "confirmed-dead holder's lease and active record (_capability_leftover_present, below)",)
FIX8_RETIRED = ("nothing to do, and nothing written, when the home is absent",
                "every transaction is terminal AND carries its terminal projection")


def t49_home_absent_recovery_disclosed(fx):
    """The record guard's introduction no longer says an absent journal home means nothing to do and
    nothing written: even with no journal work pending, capability recovery may still remove a
    confirmed-dead holder's lease and active record (T31's no-journal case exercises that reclaim)."""
    _module, guard = residual_texts()
    for pin in FIX8_GUARD:
        assert pin in guard, ("T49 the record guard states", pin)
    for retired in FIX8_RETIRED:
        assert retired not in guard, ("T49 the overstated wording is gone", retired)


def flip_t49():
    """Read the texts with the fix-8 introduction removed."""
    original = residual_texts

    def stripped():
        texts = []
        for text in original():
            for pin in FIX8_GUARD:
                text = text.replace(pin, "")
            texts.append(text)
        return tuple(texts)
    return patch.object(sys.modules[__name__], "residual_texts", stripped)


# --- T50-T59: the PR D fix-9 vectors --------------------------------------------------------------------

# Fix 9: each message, docstring and header sentence states only what the code checked on its path (the
# reviewed head certified a leftover lock's COMPLETE publication as present in the working tree, called
# the lock refusals after journal preparation "nothing written", promised reconciliation of work a run
# never left, read a projection path's entry as the projection, claimed T46 named every case, said a lock
# whose unlink had succeeded was left in place, said every transaction was terminal beside a
# nothing-opened one, promised the next run reconciles a retained transaction, said a released holder's
# acquisition cleared nothing after it removed a staging leftover, and said "Nothing was written" beside
# a recovery lease the failed acquisition had created).
T50_JOURNAL = ("so its publication was applied when it committed; its operands' current bytes are not "
               "re-checked by this recovery (unexplained operands are checked only for OPEN transactions)")
T50_FIX8 = "so its publication is present in the working tree"


def t50_leftover_lock_complete_scoped(fx):
    """A homes-1 run whose journal lock release fails after COMPLETE (T15's child) leaves its lock; the
    operator then restores the index to its pre-publication bytes. Recovery checks operands only for
    OPEN transactions, so it keeps the restored bytes, and its outcome speaks for the journal's commit
    instead of certifying the publication as present in the working tree."""
    env = fx.env
    root = fx.case("t50-leftover-lock-restored")
    pre = read(root, BI_INDEX)
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T50 the journal lock is left"
    assert read(root, BI_INDEX) != pre, "T50 the publication rewrote the index"
    (Path(root) / BI_INDEX).write_bytes(pre)
    result = record_cli(env, root, CREATE)
    refused(result, "was reconciled")
    err = result[2]
    assert read(root, BI_INDEX) == pre, "T50 recovery keeps the restored bytes (not re-checked)"
    assert "is COMPLETE, " + T50_JOURNAL in err, ("T50 the outcome speaks for the journal", err[-1200:])
    assert T50_FIX8 not in err, ("T50 no present-in-the-tree certification", err[-1200:])
    assert "run opf doctor before relying on it" in err, ("T50 the doctor advice is kept", err[-800:])


def flip_t50():
    """The leftover-lock outcome keeps the reviewed head's present-in-the-tree certification."""
    fixed = record._leftover_lock_outcome

    def reverted(owner, states):
        return fixed(owner, states).replace(
            T50_JOURNAL + ", and whether", T50_FIX8 + "; whether")
    return patch.object(record, "_leftover_lock_outcome", reverted)


T51_QUALIFIED = ("the journal preparation may already have created its directories, and no operand, "
                 "journal entry or lock was written by this run (fail-closed)")


def t51_lock_refusals_name_created(fx):
    """The homes-1 journal-lock refusals follow ensure_journal_dirs, so on a store with no journal home
    they follow the directories' creation: a lock read as held (a peer that took it after this run's
    preparation) and a failed lock acquisition each say preparation may have created the directories,
    never the reviewed head's unqualified "nothing written". The operands are untouched."""
    env = fx.env
    owner = dict(pid=os.getpid(), uid=os.getuid(), session="opf-record.peer", utc="2026-09-30T00:00:00Z")
    owner["pid-start"] = ""

    def failing_acquire(journal_root, session_id):
        raise journal.JournalError("synthetic journal lock failure")
    for label, target, replacement, needle in (
            ("held", "read_lock_owner", lambda journal_root: dict(owner), "the record journal lock is held; "),
            ("acquire", "acquire_lock", failing_acquire,
             "cannot take the record journal lock (synthetic journal lock failure); ")):
        root = fx.case("t51-homes1-lock-" + label)
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        journal_dir = Path(root) / record.JOURNAL_REL
        assert not journal_dir.exists(), ("T51 no journal directory before the run", label)
        with patch.object(record._journal, target, replacement):
            result = record_cli(env, root, CREATE)
        refused(result, needle + T51_QUALIFIED)
        assert "nothing written" not in result[2], ("T51 the unqualified wording is gone", label,
                                                    result[2][-800:])
        assert journal_dir.is_dir(), ("T51 preparation created the journal directories", label)
        assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, ("T51 operands untouched", label)


def flip_t51():
    """Refuse with the reviewed head's unqualified nothing-written wording."""
    fixed = record.RecordError

    class Unqualified(fixed):
        def __init__(self, *args):
            super().__init__(*(a.replace(T51_QUALIFIED, "nothing written (fail-closed)")
                               if isinstance(a, str) else a for a in args))
    return patch.object(record, "RecordError", Unqualified)


T52_TAIL = ("(an open transaction, or a terminal one missing its projection; none when it failed before its "
            "transaction opened) is left for the next opf record run's recovery trigger (fail-closed)")
T52_FIX8 = "and the next opf record run reconciles the typed journal"


def t52_publication_refused_before_intent(fx):
    """The governing rule scopes the leftovers to what a run actually left, and a direct homes-2
    publication (T43's setup) refused by the engine's real pre-INTENT budget check (the journal-read cap
    lowered to one byte) leaves no transaction and releases its own capability: its refusal says any
    journal work left is for the next trigger, none when it failed before its transaction opened, and
    the next run reconciles nothing. The reviewed head promised the next run reconciles the typed
    journal."""
    module, guard = residual_texts()
    for text, where in ((module, "module"), (guard, "guard")):
        assert RULE_FIX9 in text, ("T52 the rule scopes the leftovers", where)
        assert RULE_FIX8 not in text, ("T52 the unscoped rule is gone", where)
    env = fx.env
    root = fx.case("t52-homes2-pre-intent")
    with _self_test_homes2_active(root):
        req = record.parse_request(CREATE + ["--root", str(root)])
        res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
        assert res.status == record._opf_store.RESOLVED, res
        root_fd = record._opf_store._open_dir_nofollow(res.store_root)
        try:
            ctx = record.Context(res, str(root), root_fd)
            ctx.journal_rel = record._record_journal_rel(record._probe_homes(ctx))
            assert ctx.journal_rel == TYPED_JOURNAL, ("T52 the probed journal home", ctx.journal_rel)
            record._load_manifest(ctx)
            ctx.counters = record._read_operand(root_fd, ctx.rel(opf_check.COUNTERS_NAME))
            ctx.version = record._read_operand(root_fd, ctx.rel(opf_check.VERSION_NAME)).model
            ctx.worklog = record._read_operand(root_fd, ctx.rel(opf_check.WORKLOG_NAME))
            operand = record._read_operand(root_fd, record._operand_rel(req, ctx))
            seam = record.claim_ids
            with patch.object(record, "claim_ids", lambda homes, high, demand, known_complete:
                              seam(1, high, demand, known_complete)):
                plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            try:
                with patch.object(journal, "_MAX_JOURNAL_READ_BYTES", 1):
                    record._publish(ctx, plan, "create")
            except record.RecordError as exc:
                message = str(exc)
            else:
                raise AssertionError("T52 the over-budget publication must refuse")
        finally:
            os.close(root_fd)
        assert "journal-read cap" in message, ("T52 the engine's pre-INTENT budget refusal", message)
        assert T52_TAIL in message, ("T52 the refusal scopes what it left", message)
        assert T52_FIX8 not in message, ("T52 no promise of reconciliation", message)
        assert capability_records(root) == (False, False), "T52 the publication released its capability"
        states = journal_states(root, TYPED_JOURNAL)
        assert all(s == "nothing-opened" for s in states.values()), ("T52 no transaction opened", states)
        assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, "T52 the operands are untouched"
        result = record_cli(env, root, CREATE)
        refused(result, "is not active in this build")
        assert "was reconciled" not in result[2] and "were reclaimed" not in result[2], \
            ("T52 the next run reconciles nothing", result[2][-800:])


def flip_t52_message():
    """The publication refusal keeps the reviewed head's promise of reconciliation."""
    fixed = record.RecordError
    pattern = re.compile(r"and any journal work it left in the typed journal (\S+) " + re.escape(T52_TAIL))

    class Promising(fixed):
        def __init__(self, *args):
            super().__init__(*(pattern.sub(lambda m: T52_FIX8 + " " + m.group(1) + " (fail-closed)", a)
                               if isinstance(a, str) else a for a in args))
    return patch.object(record, "RecordError", Promising)


def flip_t52_rule():
    """Read the residual lists with the reviewed head's unscoped rule."""
    original = residual_texts

    def reverted():
        return tuple(text.replace(RULE_FIX9, RULE_FIX8) for text in original())
    return patch.object(sys.modules[__name__], "residual_texts", reverted)


T53_GUARD = FIX8_GUARD[0]
T53_GUARD_FIX8 = ("no record-journal work to do, and no journal write made, when the home is absent (no "
                  "mkdir), or when every transaction is terminal AND carries its terminal projection; even "
                  "with no journal work pending, capability recovery may still remove a confirmed-dead "
                  "holder's lease and active record (_capability_leftover_present, below)")


def t53_projection_existence_only(fx):
    """A COMPLETE homes-2 transaction with a dead holder (T47's child, killed at the render) whose
    projection file is replaced by an empty directory: _projection_missing checks existence only, so
    recovery reclaims the capability records and leaves the directory, and neither the outcome nor the
    guard introduction says the terminal transaction carries its projection."""
    _module, guard = residual_texts()
    assert T53_GUARD in guard, "T53 the guard introduction states existence only"
    assert T53_GUARD_FIX8 not in guard, "T53 the carries-its-projection introduction is gone"
    env = fx.env
    base = fx.case("t53-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t53-homes2-not-a-projection", base)
        proc = child(env, root, CREATE, flip=HOMES2_CHILD_FLIP + DIE_AT_RENDER)
        assert proc.returncode == 137, ("T53 the child is killed at the render", proc.returncode,
                                        proc.stderr[-800:])
        states = journal_states(root, TYPED_JOURNAL)
        assert list(states.values()) == ["complete"], ("T53 the COMPLETE transaction", states)
        (name,) = states
        projection = Path(root) / record._opf_store.txn_record("record", name)
        assert projection.is_file(), "T53 the projection was published"
        projection.unlink()
        projection.mkdir()
        result = record_cli(env, root, CREATE)
        refused(result, "were reclaimed before this operation")
        err = result[2]
        assert T47_GRAMMAR in err, ("T53 the outcome states existence only", err[-1200:])
        assert "grammar carrying its projection" not in err, ("T53 no carrying-its-projection claim",
                                                              err[-1200:])
        assert projection.is_dir(), "T53 the non-projection entry is kept as found"
        assert capability_records(root) == (False, False), "T53 the dead holder's records are reclaimed"


def flip_t53_outcome():
    return _flip_reclaimed(T47_GRAMMAR, T47_GRAMMAR_FIX8)


def flip_t53_guard():
    """Read the guard introduction with the reviewed head's carries-its-projection wording."""
    original = residual_texts

    def reverted():
        module, guard = original()
        return module, guard.replace(T53_GUARD, T53_GUARD_FIX8)
    return patch.object(sys.modules[__name__], "residual_texts", reverted)


T54_T46 = ("T46 each residual list states the rule deciding what is left for a later trigger, with its "
           "cases kept as examples marked NOT exhaustive")
T54_FIX8 = "T46 each residual list names every case that leaves work for a later trigger"


def header_text():
    """This gate's header (the module docstring), whitespace-normalized."""
    return " ".join((_optlevel.source_docstring(__file__) or "").split())


def t54_header_exact(fx):
    """The header describes every test the runner registers, and its T46 line states what T46 pins (the
    rule with its cases kept as NOT-exhaustive examples), never that each list names every case. The
    reviewed head's header stopped at T46 and kept the exhaustive-list claim."""
    text = header_text()
    assert T54_T46 in text, "T54 the T46 line states the rule"
    assert T54_FIX8 not in text, "T54 the every-case claim is gone"
    numbers = sorted(set(int(re.match(r"T([0-9]+)", name).group(1)) for name, _t, _f in TESTS))
    last = numbers[-1]
    assert "the fixture suite (T1-T{})".format(last) in text, ("T54 the usage line names the suite", last)
    for n in numbers:
        assert re.search(r"(^|\s)T{}\s".format(n), text), ("T54 the header describes", n)


def flip_t54():
    """Read the header with the reviewed head's T46 line."""
    original = header_text
    return patch.object(sys.modules[__name__], "header_text",
                        lambda: original().replace(T54_T46, T54_FIX8))


T55_MAY_BE_LEFT = ("it may be left in place (a failure after its unlink leaves it removed, not durably), and "
                   "a lock left there is for the next opf record run's reconciliation")
T55_FIX8 = "it is left in place, and the next opf record run reconciles it"


def t55_lock_release_after_unlink(fx):
    """A homes-1 journal lock release that unlinks the lock and then fails (its directory fsync) still
    records, and says the lock MAY be left, never that it is left in place: the lock is gone, and the
    next run reconciles nothing. The reviewed head said the lock was left in place and would be
    reconciled."""
    env = fx.env
    root = fx.case("t55-lock-unlinked")
    lock = Path(root) / record.JOURNAL_REL / "lock"

    def unlinked_then_failing(journal_root):
        os.unlink(os.path.join(str(journal_root), "lock"))
        raise OSError(5, "synthetic journal directory fsync failure after the unlink")
    with patch.object(record._journal, "release_lock", unlinked_then_failing):
        result = record_cli(env, root, CREATE)
    recorded(result)
    err = result[2]
    assert "could not be released (" in err, ("T55 the failed release is surfaced", err[-800:])
    assert T55_MAY_BE_LEFT in err, ("T55 the lock may be left", err[-800:])
    assert T55_FIX8 not in err, ("T55 no left-in-place claim", err[-800:])
    assert not lock.exists(), "T55 the lock was unlinked"
    fx.commit_all(root, "t55 record")
    result = record_cli(env, root, CREATE)
    assert "was reconciled" not in result[2], ("T55 the next run reconciles nothing", result[2][-800:])
    recorded(result)


def flip_t55():
    """Print the reviewed head's left-in-place text."""
    import builtins

    def reverted(*args, **kwargs):
        args = tuple(a.replace(T55_MAY_BE_LEFT, T55_FIX8) if isinstance(a, str) else a for a in args)
        return builtins.print(*args, **kwargs)
    return patch.object(record, "print", reverted, create=True)


T56_HEAD = "a leftover journal lock of a dead run was released; no transaction was open"
T56_FIX8 = "every transaction was already terminal"


def t56_nothing_opened_not_terminal(fx):
    """A homes-1 run killed after its preimage capture and before INTENT leaves its journal lock over a
    nothing-opened transaction (not terminal); once its lease is released the next run reconciles the
    lock and says no transaction was open and the dead run's transaction never opened, never that every
    transaction was already terminal. The operands keep the prestate."""
    env = fx.env
    root = fx.case("t56-nothing-opened")
    pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
    proc = child(env, root, CREATE, kill="after-preimages")
    assert proc.returncode == 137, ("T56 the child is killed before INTENT", proc.returncode, proc.stderr[-800:])
    states = journal_states(root)
    assert list(states.values()) == ["nothing-opened"], ("T56 a nothing-opened transaction", states)
    (name,) = states
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T56 the dead run's lock is left"
    (Path(root) / LEASE).unlink()
    result = record_cli(env, root, CREATE)
    refused(result, "was reconciled")
    err = result[2]
    assert T56_HEAD + ": the dead run's transaction " + name + " never opened, so it published nothing" in err, \
        ("T56 no transaction was open", err[-1200:])
    assert T56_FIX8 not in err, ("T56 no every-terminal claim", err[-1200:])
    assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, "T56 the operands keep the prestate"


def flip_t56():
    """The leftover-lock outcome keeps the reviewed head's every-terminal clause."""
    fixed = record._leftover_lock_outcome
    return patch.object(record, "_leftover_lock_outcome", lambda owner, states: fixed(owner, states).replace(
        "released; no transaction was open", "released; " + T56_FIX8))


# Inside the child: the publication fails after INTENT (an apply OSError, which the engine does not roll
# back), so the transaction stays open and the journal lock is retained; the child then exits normally.
FAILING_APPLY = """
import _journal
def _failing_apply(root_fd, ops, staged_reader):
    raise OSError(5, "synthetic apply failure after INTENT")
_journal.apply_ops = _failing_apply
"""
T57_RETAINED = ("the journal lock is retained, leaving it for the next opf record run's reconciliation, which "
                "acts on it only as far as each step succeeds (fail-closed)")
T57_FIX8 = "the journal lock is retained so the next opf record run reconciles it (fail-closed)"


def t57_retained_not_promised(fx):
    """A homes-1 publication failing after INTENT keeps its transaction open and its journal lock, and
    says the transaction is left for the next run's reconciliation as far as each step succeeds, never
    that the next run reconciles it: once the failed run has exited, an intervening operand edit makes
    that next run refuse instead, the transaction still open and the edit kept."""
    env = fx.env
    root = fx.case("t57-apply-fails-live")

    def failing_apply(root_fd, ops, staged_reader):
        raise OSError(5, "synthetic apply failure after INTENT")
    with patch.object(record._journal, "apply_ops", failing_apply):
        result = record_cli(env, root, CREATE)
    refused(result, "the publication FAILED and its transaction ")
    assert T57_RETAINED in result[2], ("T57 the transaction is left, not promised", result[2][-800:])
    assert T57_FIX8 not in result[2], ("T57 no promise of reconciliation", result[2][-800:])
    root = fx.case("t57-apply-fails-exited")
    proc = child(env, root, CREATE, flip=FAILING_APPLY)
    assert proc.returncode == 2 and T57_RETAINED in proc.stderr, ("T57 the child's refusal", proc.returncode,
                                                                  proc.stderr[-800:])
    assert list(journal_states(root).values()) == ["open"], "T57 the transaction is left open"
    edited = read(root, COUNTERS) + b"# an intervening edit\n"
    (Path(root) / COUNTERS).write_bytes(edited)
    result = record_cli(env, root, CREATE)
    refused(result, "cannot be reconciled without overwriting a change made since it was interrupted")
    assert list(journal_states(root).values()) == ["open"], "T57 the next run did not reconcile it"
    assert read(root, COUNTERS) == edited, "T57 the intervening edit is kept"


def flip_t57():
    """Refuse with the reviewed head's promise of reconciliation."""
    fixed = record.RecordError

    class Promising(fixed):
        def __init__(self, *args):
            super().__init__(*(a.replace(T57_RETAINED, T57_FIX8) if isinstance(a, str) else a for a in args))
    return patch.object(record, "RecordError", Promising)


T58_BOTH = ("(a staging leftover that acquisition removed is then reported by a refusal that names it: "
            "this run refuses once and a re-run proceeds, PR D fix 10)")
T58_FIX9 = "is then named by no outcome"
T58_RECOVER = ("or None when the acquisition removed no pre-existing leftover (names it created and retired "
               "itself in the same acquisition, created_removed, are its own bookkeeping, never leftovers), "
               "reclaimed no capability record and the plan under the capability found nothing (a holder "
               "that released after the trigger read, say), so the run continues; a staging leftover the "
               "acquisition removed while reclaiming no record is named by its own outcome "
               "(_staging_removed_outcome, PR D fix 10), so no removal is left unreported")
T58_RETIRED = "or None when the acquisition cleared nothing"


def t58_texts():
    """(the module docstring, the record guard's, _recover_capability_journal's), whitespace-normalized."""
    module, guard = residual_texts()
    return module, guard, " ".join((_optlevel.source_docstring(record.__file__, "_recover_capability_journal") or "").split())


def t58_released_holder_staging_reported(fx):
    """A live holder releases between the trigger read and the recovery acquisition (T35's setup) while
    an active-record staging leftover sits in the control directory: the acquisition removes it and
    reclaims no record, and the run now refuses ONCE, its outcome naming the removal and saying no
    record was reclaimed, instead of continuing with no line naming it (the fix-9 head, PR D fix 10);
    the re-run then proceeds to the claim-seam refusal with no removal left to name. The residual
    lists and the recovery docstring state that reporting."""
    module, guard, recover = t58_texts()
    assert T58_BOTH in module and T58_BOTH in guard, "T58 the residual lists state the reporting"
    assert T58_FIX9 not in module and T58_FIX9 not in guard, "T58 the named-by-no-outcome wording is gone"
    assert T58_RECOVER in recover, "T58 the recovery docstring states the reporting"
    assert T58_RETIRED not in recover, "T58 the cleared-nothing wording is gone"
    env = fx.env
    base = fx.case("t58-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t58-homes2-released-staging", base)
        held = [record._opf_oplock.acquire_operation(str(root), record.VERB)]
        staging = active_record(root).parent / _opf_oplock._staging_name(_opf_oplock.ACTIVE_NAME)
        staging.write_bytes(b"a torn active record publication")
        original_acquire = record._opf_oplock.acquire_operation

        def acquire_after_release(store_root, operation, holder=None, recover=False):
            if held:
                record._opf_oplock.release_operation(held.pop())
            return original_acquire(store_root, operation, holder=holder, recover=recover)
        try:
            with patch.object(record._opf_oplock, "acquire_operation", acquire_after_release):
                result = record_cli(env, root, CREATE)
        finally:
            if held:
                record._opf_oplock.release_operation(held.pop())
        refused(result, "staging leftovers an interrupted publication left were removed before this "
                        "operation: the recovery acquisition removed active record staging leftover ")
        err = result[2]
        assert not os.path.lexists(staging), "T58 the recovery acquisition removed the staging leftover"
        assert staging.name in err, ("T58 the refusal names the removed leftover", err[-1200:])
        assert "reclaiming no operation capability record" in err, ("T58 no reclaim is claimed", err[-1200:])
        assert "nothing written" not in err, ("T58 no nothing-written wording beside the removal", err[-1200:])
        assert capability_records(root) == (False, False), "T58 the capability is released"
        result = record_cli(env, root, CREATE)
        refused(result, "is not active in this build")
        assert "staging leftover" not in result[2], ("T58 the re-run proceeds with no removal to name",
                                                     result[2][-800:])


def flip_t58():
    """The fix-9 head's behaviour: a staging-only removal yields no outcome and the run continues
    unrefused, the removal named by no line."""
    fixed = record._recover_capability_journal

    def reverted(ctx, opened, unprojected):
        result = fixed(ctx, opened, unprojected)
        if result is not None and not result[0] and not result[1]:
            return None
        return result
    return patch.object(record, "_recover_capability_journal", reverted)


T59_SCOPED = "No operand, journal entry or journal lock was written by this recovery (fail-closed)"


def t59_recovery_lease_failure_scoped(fx):
    """A homes-1 recovery whose lease acquisition fails AFTER the lease file was created (its payload
    write fails) refuses with the write guard's own report that the lease is left in place, and the
    recovery's sentence speaks only for operands, journal entries and the journal lock, never the
    reviewed head's "Nothing was written" beside a created lease. The dead run's lock and the operands
    are kept."""
    env = fx.env
    root = fx.case("t59-recovery-lease-created")
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-800:])
    lock = Path(root) / record.JOURNAL_REL / "lock"
    assert lock.exists() and not (Path(root) / LEASE).exists(), "T59 a leftover lock and no lease"
    post = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)

    def failing_write(fd, data):
        raise OSError(5, "synthetic lease payload write failure")
    with patch.object(journal, "_write_all", failing_write):
        result = record_cli(env, root, CREATE)
    refused(result, "needs reconciliation (a dead run's journal lock)")
    err = result[2]
    assert "lease is LEFT in place" in err, ("T59 the write guard reports the created lease", err[-800:])
    assert T59_SCOPED in err, ("T59 the recovery's sentence is scoped", err[-800:])
    assert "Nothing was written" not in err, ("T59 no nothing-written claim", err[-800:])
    assert (Path(root) / LEASE).exists(), "T59 the failed acquisition created the lease"
    assert lock.exists(), "T59 the leftover journal lock is kept"
    assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == post, "T59 the operands are untouched"


def flip_t59():
    """Refuse with the reviewed head's unscoped sentence."""
    fixed = record.RecordError

    class Unscoped(fixed):
        def __init__(self, *args):
            super().__init__(*(a.replace(T59_SCOPED, "Nothing was written (fail-closed)")
                               if isinstance(a, str) else a for a in args))
    return patch.object(record, "RecordError", Unscoped)


# --- T60, T61: the PR D fix-10 vectors ------------------------------------------------------------------

# Fix 10: the release-failure messages state only what release_lease / release_operation verifiably
# did (the fix-9 head certified "it is LEFT in place" over a release that refuses an absent or
# replaced record as not this run's own, and over an unlink whose directory fsync failed after it),
# and a journal lock acquisition failing after its O_EXCL create discloses the possibly-left lock
# instead of reaching the cli's cannot-evaluate backstop, which said nothing about it.
T60_LEASE_MAY = ("it may be left in place (the release refuses a lease that is absent or was replaced "
                 "as not this run's own, and a failure after its unlink leaves it removed, not durably), "
                 "it is never seized (spec 5.7), and the failure above still governs.")
T60_CLAIM_MAY = ("whichever of its records the release did not remove may be left in place (never seized, "
                 "spec 5.7; the release removes each record only as far as its verified steps succeed, "
                 "and a removal's directory fsync can fail after the unlink), and the failure above "
                 "still governs.")
T60_FIX9 = "it is LEFT in place (never seized, spec 5.7) and the failure above still governs."


def t60_release_failure_scoped(fx):
    """A failed release beside a governing refusal says the record may be left in place, scoped to what
    the release verifiably did, never the fix-9 head's it-is-LEFT-in-place certainty. Two legs: the
    homes-1 recovery lease released after a recovery refusal (an intervening edit under a dead run's
    open transaction), and the single-writer claim released after a failed publication. Each synthetic
    release failure removes nothing, so the lease is in fact still present."""
    env = fx.env

    def failing_release(root_fd, machine_rel, expected_payload, verb):
        raise guard.WriteGuardError("synthetic lease release failure")
    root = fx.case("t60-recovery-release-fails")
    proc = child(env, root, CREATE, flip=FAILING_APPLY)
    assert proc.returncode == 2, ("T60 the child's publication fails after INTENT", proc.returncode,
                                  proc.stderr[-800:])
    assert list(journal_states(root).values()) == ["open"], "T60 the dead run's transaction is open"
    edited = read(root, COUNTERS) + b"# an intervening edit\n"
    (Path(root) / COUNTERS).write_bytes(edited)
    with patch.object(record._opf_write_guard, "release_lease", failing_release):
        result = record_cli(env, root, CREATE)
    refused(result, "cannot be reconciled without overwriting a change made since it was interrupted")
    err = result[2]
    assert "additionally, releasing the lease failed (" in err, ("T60 the failure is surfaced", err[-1200:])
    assert T60_LEASE_MAY in err, ("T60 the lease may be left", err[-1200:])
    assert T60_FIX9 not in err, ("T60 no left-in-place certainty", err[-1200:])
    assert (Path(root) / LEASE).exists(), "T60 the recovery lease is in fact left"
    assert read(root, COUNTERS) == edited, "T60 the intervening edit is kept"
    root = fx.case("t60-publication-release-fails")

    def failing_apply(root_fd, ops, staged_reader):
        raise OSError(5, "synthetic apply failure after INTENT")
    with patch.object(record._journal, "apply_ops", failing_apply), \
            patch.object(record._opf_write_guard, "release_lease", failing_release):
        result = record_cli(env, root, CREATE)
    refused(result, "the publication FAILED and its transaction ")
    err = result[2]
    assert "additionally, releasing the single-writer claim failed (" in err, \
        ("T60 the claim-release failure is surfaced", err[-1200:])
    assert T60_CLAIM_MAY in err, ("T60 the records may be left", err[-1200:])
    assert T60_FIX9 not in err, ("T60 no left-in-place certainty", err[-1200:])
    assert (Path(root) / LEASE).exists(), "T60 the single-writer lease is in fact left"


def flip_t60():
    """Print the fix-9 head's left-in-place release-failure text."""
    import builtins

    def reverted(*args, **kwargs):
        def back(a):
            a = a.replace("releasing the single-writer claim failed", "releasing the lease failed")
            a = a.replace(T60_CLAIM_MAY, T60_FIX9)
            return a.replace(T60_LEASE_MAY, T60_FIX9)
        args = tuple(back(a) if isinstance(a, str) else a for a in args)
        return builtins.print(*args, **kwargs)
    return patch.object(record, "print", reverted, create=True)


T61_LOCK_MAY = ("the lock itself may be left in place (a failure after its O_EXCL create leaves the "
                "created lock, which is for the next opf record run's reconciliation), and no operand "
                "or journal entry was written by this run (fail-closed)")


def t61_lock_create_failure_disclosed(fx):
    """A homes-1 journal lock acquisition that fails AFTER its O_EXCL create (here the journal-root
    fsync) leaves the created lock; the run refuses saying the lock itself may be left in place, never
    through the cli's cannot-evaluate backstop, which said nothing about it (the fix-9 head). The
    created lock is in fact present and the operands are untouched."""
    env = fx.env
    root = fx.case("t61-lock-created-then-fails")
    pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
    real = journal.acquire_lock

    def created_then_failing(journal_root, session_id):
        real(journal_root, session_id)
        raise OSError(5, "synthetic journal-root fsync failure after the lock's create")
    with patch.object(record._journal, "acquire_lock", created_then_failing):
        result = record_cli(env, root, CREATE)
    refused(result, "cannot take the record journal lock (")
    err = result[2]
    assert T61_LOCK_MAY in err, ("T61 the possibly-left lock is disclosed", err[-1200:])
    assert "cannot evaluate: unexpected error" not in err, ("T61 never the backstop", err[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T61 the failed acquisition left the lock"
    assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, "T61 the operands are untouched"


def flip_t61():
    """Refuse without the possibly-left-lock disclosure (the fix-9 head routed this failure to the cli
    backstop, which said nothing about the lock)."""
    fixed = record.RecordError

    class Undisclosed(fixed):
        def __init__(self, *args):
            super().__init__(*(a.replace(T61_LOCK_MAY, "nothing written (fail-closed)")
                               if isinstance(a, str) else a for a in args))
    return patch.object(record, "RecordError", Undisclosed)


# --- T62, T63: the PR D fix-11 vectors ------------------------------------------------------------------

# Fix 11: a failure is attributed to the step that raised it. The fix-10 head's one except spanned
# _conclude's claim release AND the success emission, so an output failure after a successful release
# printed the lease-release-failure text; and T29 wrote its temporary global config before its
# try/finally, so a write that failed after creating the file left it in the scrubbed HOME.
T62_RELEASE_FAILED = "but the lease release failed"
T62_EMIT_FAILED = ("and the lease release succeeded, but emitting the success report failed; the report "
                   "may be partial or absent, so nothing is offered as recorded here")


def t62_output_failure_not_release(fx):
    """A success-report emission that fails AFTER the single-writer claim's release succeeded is not
    reported as a lease-release failure (the fix-10 head's one except spanned the release and the
    emission): the run exits 2 saying the release succeeded and only the report's output failed, the
    transaction is COMPLETE, the lease is in fact absent, and the next run records normally."""
    env = fx.env
    root = fx.case("t62-emit-fails-after-release")
    pre = read(root, BI_INDEX)

    def failing_emit(report):
        raise BrokenPipeError(32, "synthetic output failure after the lease release")
    with patch.object(record, "_emit_success", failing_emit):
        result = record_cli(env, root, CREATE)
    refused(result, T62_EMIT_FAILED)
    err = result[2]
    assert T62_RELEASE_FAILED not in err, ("T62 the release is not blamed", err[-1200:])
    assert "synthetic output failure after the lease release" in err, ("T62 the output failure is "
                                                                       "surfaced", err[-800:])
    assert not (Path(root) / LEASE).exists(), "T62 the lease is in fact released"
    assert list(journal_states(root).values()) == ["complete"], "T62 the transaction is COMPLETE"
    assert read(root, BI_INDEX) != pre, "T62 the publication rewrote the index"
    fx.commit_all(root, "the T62 change, recorded but unreported")
    recorded(record_cli(env, root, CREATE))


def flip_t62():
    """One except spanning the release and the emission (the fix-10 head): an output failure after a
    successful release prints the lease-release-failure text."""
    def head_conclude(ctx, lease, report, released):
        released[0] = True
        try:
            record._release_guard(ctx, lease)
            record._emit_success(report)
        except BaseException:
            print(record._release_failure_text(report), file=sys.stderr)
            raise
    return patch.object(record, "_conclude", head_conclude)


T63_TORN = "synthetic no-space failure after the config file's create"


def t63_t29_cleanup_covers_the_write(fx):
    """T29's temporary global config is removed even when its write fails AFTER creating the file: the
    write runs inside the cleanup region (t29_launch_config, PR D fix 11). The fix-10 head wrote it
    before the try/finally, so a torn write left the config in the scrubbed HOME for every later
    fixture git launch. The torn write's own error still surfaces."""
    gitconfig = Path(fx.env.vars["HOME"]) / ".gitconfig"

    def torn_write(path, trace):
        path.write_text("[trace2]\n", encoding="utf-8")
        raise OSError(28, T63_TORN)
    failed = None
    try:
        with patch.object(sys.modules[__name__], "t29_write_launch_config", torn_write):
            try:
                t29_git_lifecycle(fx)
            except OSError as exc:
                failed = exc
        assert failed is not None and T63_TORN in str(failed), ("T63 the torn write surfaces", failed)
        assert not gitconfig.exists(), "T63 the torn config is removed by T29's own cleanup"
    finally:
        if gitconfig.exists():
            gitconfig.unlink()


def flip_t63():
    """Create the config before the cleanup region (the fix-10 head's ordering), so a write that fails
    after creating the file leaves it."""
    @contextlib.contextmanager
    def head_ordering(gitconfig, trace):
        t29_write_launch_config(gitconfig, trace)
        try:
            yield
        finally:
            gitconfig.unlink()
    return patch.object(sys.modules[__name__], "t29_launch_config", head_ordering)


# --- T64, T65: the PR D fix-12 vectors ------------------------------------------------------------------

# Fix 12: the diagnostic prints around the success path's release and emission, and the recovery
# lease-release surfacing, are protected, so a failing stderr write (or a release interrupt) never
# displaces the failure that governs the exit; and the success path's release mark is set inside
# _conclude, never by the caller before the call, so an interrupt landing just before _conclude
# still reaches the caller's cleanup release exactly once.
T64_STDERR = "synthetic stderr failure"


def _t64_failing_print(marker):
    """A record-module print that fails with OSError exactly once, on the first text carrying marker."""
    import builtins
    fired = []

    def failing(*args, **kwargs):
        if not fired and any(isinstance(a, str) and marker in a for a in args):
            fired.append(True)
            raise OSError(5, T64_STDERR)
        return builtins.print(*args, **kwargs)
    return failing


def _t64_refusing_recover():
    raise record.RecordError("the original t64 recovery refusal")


def t64_diagnostic_failure_never_governs(fx):
    """A diagnostic print that itself fails never displaces the governing failure (PR D fix 12): a
    lease-release failure still governs when the release-failure text cannot be written; an emission
    failure still governs when the emission-failure text cannot be written; a release interrupt
    propagates over the failing diagnostic; and a recovery refusal survives a lease-release
    KeyboardInterrupt, and an OSError whose surfacing print fails, unchanged. The fix-11 head
    propagated the stderr failure (or the release interrupt) instead, leaving the refusal or the
    original failure only in __context__."""
    env = fx.env
    root = fx.case("t64-release-then-stderr")

    def failing_release(*_args):
        raise guard.WriteGuardError("synthetic release failure")
    with patch.object(guard, "release_lease", failing_release), \
            patch.object(record, "print", _t64_failing_print("the lease release failed"), create=True):
        result = record_cli(env, root, CREATE)
    refused(result, "synthetic release failure")
    err = result[2]
    assert T64_STDERR not in err, ("T64 the stderr failure never governs the release path", err[-800:])
    assert "cannot evaluate: unexpected error" not in err, ("T64 never the backstop", err[-800:])
    root = fx.case("t64-emit-then-stderr")

    def failing_emit(report):
        raise BrokenPipeError(32, "synthetic emission failure")
    with patch.object(record, "_emit_success", failing_emit), \
            patch.object(record, "print", _t64_failing_print("emitting the success report failed"),
                         create=True):
        rc, _out, err = record_cli(env, root, CREATE)
    assert rc == 2, ("T64 the emission failure fails closed", rc, err[-800:])
    assert "synthetic emission failure" in err, ("T64 the emission failure governs", err[-800:])
    assert T64_STDERR not in err, ("T64 the stderr failure never governs the emission path", err[-800:])
    assert not (Path(root) / LEASE).exists(), "T64 the lease was released before the emission"
    root = fx.case("t64-release-interrupt")

    def interrupted_release(*_args):
        raise KeyboardInterrupt
    caught = None
    try:
        with patch.object(guard, "release_lease", interrupted_release), \
                patch.object(record, "print", _t64_failing_print("the lease release failed"),
                             create=True):
            record_cli(env, root, CREATE)
    except KeyboardInterrupt as exc:
        caught = exc
    assert caught is not None, "T64 the release interrupt governs, never the stderr failure"
    from types import SimpleNamespace
    ctx = SimpleNamespace(root_fd=None, machine_rel=MACH)
    for rel_exc in (KeyboardInterrupt(), OSError(5, "synthetic recovery release failure")):
        def failing_rel(_ctx, _lease, exc=rel_exc):
            raise exc
        outcome = None
        with patch.object(guard, "acquire_lease", lambda *_a: object()), \
                patch.object(record, "_release", failing_rel), \
                patch.object(record, "print", _t64_failing_print("releasing the lease failed"),
                             create=True):
            try:
                record._with_recovery_lease(ctx, "open transaction(s) t64", _t64_refusing_recover)
            except BaseException as exc:  # a displacing failure is reported by the assertion below
                outcome = exc
        assert isinstance(outcome, record.RecordError) and \
            "the original t64 recovery refusal" in str(outcome), (
                "T64 the recovery refusal governs", type(rel_exc).__name__, repr(outcome))


def flip_t64_conclude():
    """The fix-11 head's _conclude: unprotected diagnostic prints, so a failing stderr write displaces
    the release or emission failure (the release mark is kept, as fix 12 sets it, isolating the
    unprotected prints)."""
    def head_conclude(ctx, lease, report, released):
        released[0] = True
        prn = getattr(record, "print", print)
        try:
            record._release_guard(ctx, lease)
        except BaseException:
            prn(record._release_failure_text(report), file=sys.stderr)
            raise
        try:
            record._emit_success(report)
        except BaseException:
            prn(record._emit_failure_text(report), file=sys.stderr)
            raise
    return patch.object(record, "_conclude", head_conclude)


def flip_t64_recovery():
    """The fix-11 head's _with_recovery_lease release handler: except Exception, so a release
    interrupt displaces the refusal, and an unprotected surfacing print."""
    def head_with_recovery_lease(ctx, pending, recover):
        try:
            lease = guard.acquire_lease(ctx.root_fd, ctx.machine_rel, record.VERB)
        except guard.WriteGuardError as exc:
            raise record.RecordError("an interrupted opf record publication needs reconciliation "
                                     "({}): {} (fail-closed)".format(pending, exc))
        try:
            result = recover()
        except BaseException:
            try:
                record._release(ctx, lease)
            except Exception as rel_exc:  # the head's width: an interrupt passes through
                prn = getattr(record, "print", print)
                prn("opf record: additionally, releasing the lease failed ({}); it may be left in "
                    "place, and the failure above still governs.".format(rel_exc), file=sys.stderr)
            raise
        record._release(ctx, lease)
        return result
    return patch.object(record, "_with_recovery_lease", head_with_recovery_lease)


def t65_interrupt_before_conclude(fx):
    """A real SIGINT delivered by a line trace immediately before the _conclude call (where the
    fix-11 head had already marked the release done on the previous line) still releases the
    single-writer claim exactly once: the mark is set inside _conclude, so the caller's cleanup
    releases, the interrupt governs the exit, and no success is reported. On the fix-11 head the
    cleanup read the premature mark and skipped the release entirely, leaving the lease beside a
    COMPLETE transaction (QA round 11, F2)."""
    import inspect
    import signal
    env = fx.env
    root = fx.case("t65-sigint-before-conclude")
    releases = []
    real = guard.release_lease

    def counting(root_fd, machine_rel, payload, verb):
        releases.append(True)
        return real(root_fd, machine_rel, payload, verb)
    lines, start = inspect.getsourcelines(record._run_operation)
    offsets = [i for i, line in enumerate(lines) if "_conclude(" in line]
    assert len(offsets) == 1, ("T65 the one _conclude call in _run_operation", offsets)
    target = start + offsets[0]
    fired = []

    def local_trace(frame, event, arg):
        if event == "line" and frame.f_lineno == target and not fired:
            fired.append(True)
            os.kill(os.getpid(), signal.SIGINT)
        return local_trace

    def global_trace(frame, event, arg):
        return local_trace if frame.f_code is record._run_operation.__code__ else None
    prior_trace = sys.gettrace()
    prior_handler = signal.signal(signal.SIGINT, signal.default_int_handler)
    caught = result = None
    try:
        sys.settrace(global_trace)
        try:
            with patch.object(guard, "release_lease", counting):
                result = record_cli(env, root, CREATE)
        except KeyboardInterrupt as exc:
            caught = exc
        finally:
            sys.settrace(prior_trace)
    finally:
        signal.signal(signal.SIGINT, prior_handler)
    assert fired, "T65 the trace delivered the SIGINT"
    assert caught is not None, ("T65 the interrupt governs the exit", result)
    assert len(releases) == 1, ("T65 the release is attempted exactly once", len(releases))
    assert not (Path(root) / LEASE).exists(), "T65 the lease is in fact released"
    states = journal_states(root)
    assert list(states.values()) == ["complete"], ("T65 the publication committed", states)


def flip_t65():
    """The fix-11 head's premature mark: the release cell reads already-set before _conclude's release
    attempt begins (the head's caller set it on the line before the call), so the interrupt landing
    just before _conclude skips the release entirely."""
    return patch.object(record, "_RELEASE_PENDING", (True,))


# --- T66: the PR D fix-13 vector ------------------------------------------------------------------------

def t66_release_interrupt_never_displaces(fx):
    """A homes-1 journal-lock release that raises KeyboardInterrupt while the publication failed and
    its transaction reads rolled-back: the rolled-back refusal still governs the exit, the release
    failure surfaced beside it and the unreleased lock left for reconciliation (PR D fix 13). On the
    reviewed head the release handler caught only (JournalError, OSError), so the interrupt displaced
    the refusal, which survived only in __context__."""
    env = fx.env
    root = fx.case("t66-release-interrupt")

    def failing_run(*_args, **_kwargs):
        raise journal.JournalError("synthetic rolled-back publication failure")

    def interrupted_release(_journal_root):
        raise KeyboardInterrupt
    caught = result = None
    try:
        with patch.object(journal, "run_transaction", failing_run), \
                patch.object(journal, "classify_state", lambda *_a: "rolled-back"), \
                patch.object(journal, "release_lock", interrupted_release):
            result = record_cli(env, root, CREATE)
    except KeyboardInterrupt as exc:
        caught = exc
    assert caught is None, "T66 the rolled-back refusal governs, never the release interrupt"
    refused(result, "the publication was refused and rolled back to the prestate")
    err = result[2]
    assert "could not be released (" in err, ("T66 the failed release is surfaced", err[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T66 the unreleased lock is left"
    assert not (Path(root) / LEASE).exists(), "T66 the single-writer claim is released"


def flip_t66():
    """The reviewed head's _publish release handler: (JournalError, OSError) only, so the release
    interrupt passes through the finally and displaces the rolled-back refusal."""
    def head_publish(ctx, plan, subcommand, cap=None):
        if ctx.journal_rel != record.JOURNAL_REL:
            return record._publish_homes2(ctx, plan, subcommand, cap)
        root_fd = ctx.root_fd
        journal_root = record._journal_root(ctx)
        journal.require_containment()
        journal.ensure_journal_dirs(root_fd, record.JOURNAL_REL)
        jr_fd = journal.open_journal_root_fd(root_fd, record.JOURNAL_REL)
        held = retain = False
        token = os.urandom(16).hex()
        try:
            if journal.read_lock_owner(journal_root) is not None:
                raise record.RecordError("the record journal lock is held (fail-closed)")
            journal.acquire_lock(journal_root, "{}.{}".format(record.SESSION_ID, token))
            held = True
            ops, content = [], {}
            for operand in plan.operands:
                ops.append({"op": "write", "path": operand.rel,
                            "poststate": {"kind": "file",
                                          "content-sha256": record._sha256(operand.new_raw)},
                            "source-poststate": {"kind": "file", "mode": operand.mode,
                                                 "sha256": record._sha256(operand.raw)}})
                content[operand.rel] = operand.new_raw
            txn_id = record._record_run_id(token)
            header = {"unit": record.SESSION_ID, "kind": "record-" + subcommand}
            try:
                journal.run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops,
                                        lambda op: content[op["path"]], record.SESSION_ID)
            except (journal.JournalError, OSError) as exc:
                state = journal.classify_state(jr_fd, journal_root / txn_id)
                if state == "rolled-back":
                    raise record.RecordError("the publication was refused and rolled back to the "
                                             "prestate ({}); nothing recorded (fail-closed)".format(exc))
                retain = True
                raise record.RecordError("the publication FAILED ({})".format(exc))
        finally:
            if held and not retain:
                try:
                    journal.release_lock(journal_root)
                except (journal.JournalError, OSError):  # the head's width: an interrupt passes through
                    pass
            journal._close_fd_quietly(jr_fd)
    return patch.object(record, "_publish", head_publish)


# --- T67, T68: the PR D fix-14 vectors ------------------------------------------------------------------

def t67_ambient_exception_not_pending(fx):
    """The record CLI invoked inside an embedding caller's except block, with the journal-lock
    release raising a non-ordinary RuntimeError on the clean exit: the release failure is the run's
    own failure (exit 2, no success report), exactly as it is outside the handler. On the fix-13
    head the cleanup read sys.exc_info() in its finally, so the caller's handled ValueError read as
    a refusal in flight and the run reported success exit 0 over the swallowed release failure (QA
    round 13, F1)."""
    env = fx.env
    root = fx.case("t67-ambient-handler")

    def raising_release(_journal_root):
        raise RuntimeError("ambient release witness")
    with patch.object(record._journal, "release_lock", raising_release):
        try:
            raise ValueError("the embedding caller's handled exception")
        except ValueError:
            result = record_cli(env, root, CREATE)
    rc, out, err = result
    assert rc == 2, ("T67 a non-ordinary clean-exit release failure is the run's own failure, an "
                     "embedding caller's except block included", rc, out[-800:], err[-800:])
    assert RECORDED_EVENT not in out, ("T67 no success report", out[-800:])
    assert "ambient release witness" in err, ("T67 the release failure governs the exit", err[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T67 the unreleased lock is left"
    outside = fx.case("t67-no-handler")
    with patch.object(record._journal, "release_lock", raising_release):
        result = record_cli(env, outside, CREATE)
    assert result[0] == 2, ("T67 the same failure outside any handler", result[0], result[2][-800:])


def flip_t67():
    """The fix-13 head's _publish cleanup: `pending = sys.exc_info()[1]` read in the finally, under
    which an embedding caller's handled exception reads as a refusal in flight, so a clean-exit
    non-ordinary release failure is surfaced as a warning and the run still reports success."""
    def head_publish(ctx, plan, subcommand, cap=None):
        if ctx.journal_rel != record.JOURNAL_REL:
            return record._publish_homes2(ctx, plan, subcommand, cap)
        root_fd = ctx.root_fd
        journal_root = record._journal_root(ctx)
        journal.require_containment()
        journal.ensure_journal_dirs(root_fd, record.JOURNAL_REL)
        jr_fd = journal.open_journal_root_fd(root_fd, record.JOURNAL_REL)
        held = retain = False
        token = os.urandom(16).hex()
        try:
            if journal.read_lock_owner(journal_root) is not None:
                raise record.RecordError("the record journal lock is held (fail-closed)")
            journal.acquire_lock(journal_root, "{}.{}".format(record.SESSION_ID, token))
            held = True
            ops, content, staged = [], dict(), []
            for operand in plan.operands:
                post = dict((("kind", "file"), ("content-sha256", record._sha256(operand.new_raw))))
                source = dict((("kind", "file"), ("mode", operand.mode),
                               ("sha256", record._sha256(operand.raw))))
                ops.append(dict((("op", "write"), ("path", operand.rel), ("poststate", post),
                                 ("source-poststate", source))))
                content.update(((operand.rel, operand.new_raw),))
                staged.append(record.base64.b64encode(operand.new_raw).decode("ascii"))
            txn_id = record._record_run_id(token)
            header = dict((("unit", record.SESSION_ID), ("kind", "record-" + subcommand),
                           ("staged", staged)))
            try:
                journal.run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops,
                                        lambda op: content.get(op.get("path")), record.SESSION_ID)
            except (journal.JournalError, OSError) as exc:
                retain = True
                raise record.RecordError("the publication FAILED ({})".format(exc))
        finally:
            if held and not retain:
                pending = sys.exc_info()[1]   # the head's ambient read
                try:
                    journal.release_lock(journal_root)
                except BaseException as exc:  # noqa: BLE001  the head's cleanup handler
                    if pending is None and not isinstance(exc, (journal.JournalError, OSError)):
                        raise
                    try:
                        print("opf record: the record journal lock under {} could not be released "
                              "({}); it may be left in place.".format(record.JOURNAL_REL, exc),
                              file=sys.stderr)
                    except BaseException:
                        pass
            journal._close_fd_quietly(jr_fd)
    return patch.object(record, "_publish", head_publish)


def t68_release_mark_before_acquisition(fx):
    """The release mark's copy precedes the lease acquisition, so an ordinary allocation failure at
    `list(_RELEASE_PENDING)` can never land between _acquire_guard returning and the protected
    region: the trap object fails the copy only once the lease has been acquired, so on the fixed
    ordering it never fires and the run records with the lease released. On the fix-13 head the
    copy ran after the real acquisition, so the trap fired there: exit 2 with zero release attempts
    and the lease left in place (QA round 13, F2)."""
    env = fx.env
    root = fx.case("t68-release-mark")
    acquired = []
    real_acquire = guard.acquire_lease

    def arming_acquire(root_fd, machine_rel, verb):
        lease = real_acquire(root_fd, machine_rel, verb)
        acquired.append(True)
        return lease

    class MarkCopyTrap:
        """Iterable release mark whose copy fails once the lease is held."""

        def __iter__(self):
            if acquired:
                raise MemoryError("synthetic allocation failure after the acquisition")
            return iter((False,))
    with patch.object(record, "_RELEASE_PENDING", MarkCopyTrap()), \
            patch.object(guard, "acquire_lease", arming_acquire):
        result = record_cli(env, root, CREATE)
    assert acquired, "T68 the run acquired the lease for real"
    recorded(result)
    assert not (Path(root) / LEASE).exists(), "T68 the lease is released, never stranded"


def flip_t68():
    """The fix-13 head's ordering: the release mark's copy is taken only after _acquire_guard has
    returned, so the allocation failure lands between the acquisition and the protected region and
    the just-acquired lease is stranded."""
    real = record._acquire_guard

    def head_order(ctx):
        lease = real(ctx)
        list(record._RELEASE_PENDING)   # the head's post-acquisition copy of the release mark
        return lease
    return patch.object(record, "_acquire_guard", head_order)


# --- T69: the PR D fix-15 vector ------------------------------------------------------------------------

def _fd_identity(fd):
    """The (device, inode) a descriptor number currently names, or None when it names nothing."""
    try:
        st = os.fstat(fd)
    except OSError:
        return None
    return st.st_dev, st.st_ino


@contextlib.contextmanager
def journal_fd_ledger():
    """Every journal root descriptor the run opens, as [fd, identity, close attempts]. A close is
    counted on the latest open of its number, and only while that number still names the opened
    directory or nothing (a second close); a number since reused by another object is not counted."""
    ledger = []
    real_open, real_close = journal.open_journal_root_fd, journal._close_fd_quietly

    def opening(root_fd, journal_rel):
        fd = real_open(root_fd, journal_rel)
        ledger.append([fd, _fd_identity(fd), 0])
        return fd

    def closing(fd):
        for entry in reversed(ledger):
            if entry[0] == fd:
                if _fd_identity(fd) in (entry[1], None):
                    entry[2] += 1
                break
        return real_close(fd)
    with patch.object(journal, "open_journal_root_fd", opening), \
            patch.object(journal, "_close_fd_quietly", closing):
        yield ledger


def _settle(ledger):
    """The ledger's descriptors still open, each closed here so a red run leaks nothing into later cases."""
    latest = {}
    for entry in ledger:
        latest[entry[0]] = entry
    alive = [entry for entry in latest.values() if _fd_identity(entry[0]) == entry[1]]
    for entry in alive:
        os.close(entry[0])
    return alive


def t69_release_failure_closes_fd(fx):
    """The homes-1 publication's journal root descriptor is closed exactly once when the journal-lock
    release raises a non-ordinary RuntimeError on the clean exit inside an embedding caller's except
    block: the release failure governs (exit 2, no success report, the lock left), no journal
    descriptor survives, and the single-writer lease is released. On the fix-14 head the release
    handler's re-raise skipped the trailing close, leaking the descriptor (QA round 14, M1)."""
    env = fx.env
    root = fx.case("t69-release-in-handler")

    def raising_release(_journal_root):
        raise RuntimeError("fd release witness")
    with journal_fd_ledger() as ledger, patch.object(journal, "release_lock", raising_release):
        try:
            raise ValueError("the embedding caller's handled exception")
        except ValueError:
            result = record_cli(env, root, CREATE)
    alive = _settle(ledger)
    rc, out, err = result
    assert rc == 2, ("T69 the clean-exit release failure governs", rc, out[-800:], err[-800:])
    assert RECORDED_EVENT not in out, ("T69 no success report", out[-800:])
    assert "fd release witness" in err, ("T69 the release failure governs the exit", err[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T69 the unreleased lock is left"
    assert not (Path(root) / LEASE).exists(), "T69 the single-writer lease is released"
    assert ledger, "T69 the publication opened its journal root for real"
    assert not alive, ("T69 a raising clean-exit release leaves no journal descriptor open",
                       [entry[0] for entry in alive])
    assert [entry[2] for entry in ledger] == [1] * len(ledger), (
        "T69 each journal descriptor is closed exactly once", [entry[2] for entry in ledger])


def t69_token_failure_closes_fd(fx):
    """The homes-1 publication's journal root descriptor is closed exactly once when os.urandom fails
    with a MemoryError as _publish allocates the lock token: that failure governs (exit 2, no success
    report, no journal lock taken), no journal descriptor survives, and the single-writer lease is
    released. On the fix-14 head the token was allocated after the open but outside the cleanup
    region, leaking the descriptor (QA round 14, M1)."""
    env = fx.env
    root = fx.case("t69-token-allocation")
    real_urandom = os.urandom
    fired = []

    def urandom_trap(n):
        if sys._getframe(1).f_code is record._publish.__code__:
            fired.append(n)
            raise MemoryError("synthetic token allocation failure")
        return real_urandom(n)
    with journal_fd_ledger() as ledger, patch.object(os, "urandom", urandom_trap):
        result = record_cli(env, root, CREATE)
    alive = _settle(ledger)
    rc, out, err = result
    assert fired, "T69 the token allocation failed for real"
    assert rc == 2, ("T69 the allocation failure governs", rc, out[-800:], err[-800:])
    assert RECORDED_EVENT not in out, ("T69 no success report", out[-800:])
    assert "synthetic token allocation failure" in err, ("T69 the allocation failure governs the exit",
                                                         err[-800:])
    assert not (Path(root) / record.JOURNAL_REL / "lock").exists(), "T69 no journal lock is taken"
    assert not (Path(root) / LEASE).exists(), "T69 the single-writer lease is released"
    assert ledger, "T69 the publication opened its journal root for real"
    assert not alive, ("T69 a failed token allocation leaves no journal descriptor open",
                       [entry[0] for entry in alive])
    assert [entry[2] for entry in ledger] == [1] * len(ledger), (
        "T69 each journal descriptor is closed exactly once", [entry[2] for entry in ledger])


def flip_t69():
    """The fix-14 head's _publish: the lock token is allocated after the journal root's open but before
    the cleanup region, and the descriptor's close trails the release handler, whose re-raise of a
    clean-exit non-ordinary release failure skips it."""
    def head_publish(ctx, plan, subcommand, cap=None):
        if ctx.journal_rel != record.JOURNAL_REL:
            return record._publish_homes2(ctx, plan, subcommand, cap)
        root_fd = ctx.root_fd
        journal_root = record._journal_root(ctx)
        journal.require_containment()
        journal.ensure_journal_dirs(root_fd, record.JOURNAL_REL)
        jr_fd = journal.open_journal_root_fd(root_fd, record.JOURNAL_REL)
        held = retain = False
        pending = None
        token = os.urandom(16).hex()   # the head's allocation outside the cleanup region
        try:
            if journal.read_lock_owner(journal_root) is not None:
                raise record.RecordError("the record journal lock is held (fail-closed)")
            journal.acquire_lock(journal_root, "{}.{}".format(record.SESSION_ID, token))
            held = True
            ops, content, staged = [], dict(), []
            for operand in plan.operands:
                post = dict((("kind", "file"), ("content-sha256", record._sha256(operand.new_raw))))
                source = dict((("kind", "file"), ("mode", operand.mode),
                               ("sha256", record._sha256(operand.raw))))
                ops.append(dict((("op", "write"), ("path", operand.rel), ("poststate", post),
                                 ("source-poststate", source))))
                content.update(((operand.rel, operand.new_raw),))
                staged.append(record.base64.b64encode(operand.new_raw).decode("ascii"))
            txn_id = record._record_run_id(token)
            header = dict((("unit", record.SESSION_ID), ("kind", "record-" + subcommand),
                           ("staged", staged)))
            try:
                journal.run_transaction(root_fd, jr_fd, journal_root, txn_id, header, ops,
                                        lambda op: content.get(op.get("path")), record.SESSION_ID)
            except (journal.JournalError, OSError) as exc:
                retain = True
                raise record.RecordError("the publication FAILED ({})".format(exc))
        except BaseException as exc:  # noqa: BLE001  the head's own record of the failure in flight
            pending = exc
            raise
        finally:
            if held and not retain:
                try:
                    journal.release_lock(journal_root)
                except BaseException as exc:  # noqa: BLE001  the head's cleanup handler
                    if pending is None and not isinstance(exc, (journal.JournalError, OSError)):
                        raise   # the head's re-raise, skipping the close below
                    try:
                        print("opf record: the record journal lock under {} could not be released "
                              "({}); it may be left in place.".format(record.JOURNAL_REL, exc),
                              file=sys.stderr)
                    except BaseException:
                        pass
            journal._close_fd_quietly(jr_fd)
    return patch.object(record, "_publish", head_publish)


# --- T70-T71: the PR D fix-16 vectors -------------------------------------------------------------------

import inspect

ALLOCATION_RESIDUAL = ("clean exits only); an interpreter allocation failure (a MemoryError) may likewise "
                       "leave a lease, an operation capability record, a journal lock or an open descriptor "
                       "for the next run's recovery (PR D fix 16: no step makes a narrower promise under it);")


def t70_tag_after_unwind(fx):
    """An acquisition whose lease publication fails after its active record's published, and whose
    removal-report tagging then fails with a MemoryError (a module-global `tuple` in _opf_oplock that
    fails only when called from _tag_removals), still unwinds in full: the allocation failure escapes
    with the original refusal as its context, no descriptor the acquisition adopted survives, neither
    capability record is left, and the anchor is free, so the next acquisition succeeds. Untrapped, the
    same failure propagates as the original OpLockError carrying its removal reports. On the fix-15 head
    the handler tagged the error first, so the failure skipped the whole unwind, leaking every
    descriptor with the anchor still locked (QA round 15, codex F1 and claude M1)."""
    root = fx.case("t70-tag-after-unwind")
    real_adopt, real_publish = _opf_oplock._FdOwner.adopt, _opf_oplock._create_control_file
    adopted, fired = [], []

    def recording_adopt(owner, fd):
        real_adopt(owner, fd)
        adopted.append([fd, _fd_identity(fd)])
        return fd

    def lease_publication_fails(dir_fd, name, *a, **k):
        if name == opf_check.LEASE_NAME:
            raise _opf_oplock.OpLockError("synthetic lease publication failure")
        return real_publish(dir_fd, name, *a, **k)

    def tuple_trap(*args):
        if sys._getframe(1).f_code is _opf_oplock._tag_removals.__code__:
            fired.append(True)
            raise MemoryError("synthetic removal-report allocation failure")
        return tuple(*args)
    with _self_test_homes2_active(root):
        with patch.object(_opf_oplock, "_create_control_file", lease_publication_fails):
            try:
                _opf_oplock.acquire_operation(str(root), record.VERB)
            except _opf_oplock.OpLockError as exc:
                control = exc
            else:
                raise AssertionError("T70 the synthetic lease publication failure must refuse")
        assert "synthetic lease publication failure" in str(control), ("T70 the original failure", control)
        assert (control.recovered, control.recovered_operation, control.staging_removed) == ((), None, ()), \
            ("T70 nothing stale was removed", [getattr(control, name, None) for name in REPORTS])
        got = getattr(control, "created_removed", ())
        assert len(got) == 2 and re.fullmatch(own_staging_pattern(_opf_oplock.ACTIVE_NAME, "active record"),
                                              got[0]) and got[1] == "its own new active record (unwind)", \
            ("T70 the original OpLockError carries its removal reports", got)
        with patch.object(_opf_oplock, "_create_control_file", lease_publication_fails), \
                patch.object(_opf_oplock._FdOwner, "adopt", recording_adopt), \
                patch.object(_opf_oplock, "tuple", tuple_trap, create=True):
            try:
                _opf_oplock.acquire_operation(str(root), record.VERB)
            except (MemoryError, _opf_oplock.OpLockError) as exc:
                failure = exc
            else:
                raise AssertionError("T70 the trapped acquisition must fail")
        records = capability_records(root)
        latest = dict((entry[0], entry) for entry in adopted)
        alive = [fd for fd, identity in latest.values() if _fd_identity(fd) == identity]
        try:
            try:
                cap = _opf_oplock.acquire_operation(str(root), record.VERB)
            except _opf_oplock.OpLockError as exc:
                probe = str(exc)
            else:
                _opf_oplock.release_operation(cap)
                probe = None
        finally:
            for fd in alive:
                os.close(fd)     # a red run leaks nothing, and frees the anchor, for later cases
    assert fired, "T70 the removal-report allocation failed for real"
    assert isinstance(failure, MemoryError), ("T70 the allocation failure escapes", failure)
    assert isinstance(failure.__context__, _opf_oplock.OpLockError) \
        and "synthetic lease publication failure" in str(failure.__context__), \
        ("T70 the original refusal is the allocation failure's context", failure.__context__)
    assert adopted, "T70 the acquisition adopted its descriptors for real"
    assert not alive, ("T70 no descriptor the failed acquisition adopted survives", alive)
    assert records == (False, False), ("T70 the unwind left no capability record", records)
    assert probe is None, ("T70 the anchor is free: the next acquisition succeeds", probe)


def flip_t70():
    """The fix-15 head's _acquire_body: its failure handler tags an OpLockError with the removal reports
    FIRST, ahead of every release in the unwind (the current body rebuilt with that tagging restored at
    the handler's head)."""
    source = inspect.getsource(_opf_oplock._acquire_body)
    handler = "\n    except BaseException as exc:\n"
    if source.count(handler) != 1:
        raise Harness("flip_t70: the acquisition body's failure handler was not found once")
    source = source.replace(handler, handler + "        if isinstance(exc, OpLockError):\n"
                            "            _tag_removals(exc, removed_stale, recovered_operation, "
                            "staging_removed, created_removed)\n")
    namespace = {}
    exec(compile(source, _opf_oplock.__file__, "exec"), vars(_opf_oplock), namespace)
    return patch.object(_opf_oplock, "_acquire_body", namespace["_acquire_body"])


def t71_allocation_residual_disclosed(fx):
    """The module residual list states ONCE, directly after the asynchronous-interrupt disclosure, that
    an interpreter allocation failure may leave a lease, a capability record, a journal lock or a
    descriptor for the next run's recovery (PR D fix 16), with no per-site allocation promise."""
    module, _guard = residual_texts()
    assert module.count(ALLOCATION_RESIDUAL) == 1, ("T71 the allocation-failure residual is stated once",
                                                    ALLOCATION_RESIDUAL)
    assert module.count("interpreter allocation failure") == 1, "T71 it is stated exactly once"


def flip_t71():
    """Read the module residual list with the fix-16 statement removed."""
    original = residual_texts
    cut = ALLOCATION_RESIDUAL[len("clean exits only);"):-1]

    def stripped():
        module, guard_text = original()
        return module.replace(cut, ""), guard_text
    return patch.object(sys.modules[__name__], "residual_texts", stripped)


# --- the runner ------------------------------------------------------------------------------------------------

TESTS = (
    ("T1-precondition-byte-reproduction", t1_precondition, flip_t1),
    ("T2-round-trip-emit-checked", t2_round_trip, flip_t2),
    ("T3-allowed-delta-postcondition", t3_postcondition, flip_t3),
    ("T4-allocation-known-complete", t4_allocation, flip_t4),
    ("T5-crash-prestate-or-poststate", t5_crash, None),     # its flip runs inside the killed child
    ("T6-proposed-then-ratified-receipt", t6_done_with_receipt, (flip_t6_bare, flip_t6_maintainer,
                                                                flip_t6_receipt)),
    ("T7-rejection-reason-and-pre-proposal", t7_rejection, (flip_t7_reason, flip_t7_pre)),
    ("T8-parallel-branch-collision", t8_collision, flip_t8),
    ("T9-lease-release-before-success", t9_lease, (flip_t9, flip_t9_outcome)),
    ("T10-final-doctor", t10_final_doctor, flip_t10),
    ("T11-worklog-released-span", t11_worklog, flip_t11),
    ("T12-recovery-under-the-lease", t12_recovery_lease, flip_t12),
    ("T13-recovery-intervening-edit", t13_intervening_edit, flip_t13),
    ("T14-create-type-before-read", t14_type_before_read, flip_t14),
    ("T15-leftover-journal-lock", t15_leftover_lock, flip_t15),
    ("T16-rejection-recorded-predecessor", t16_recorded_predecessor, flip_t16),
    ("T16-invalid-predecessor-rejection", t16_invalid_predecessor_rejection, flip_t16_trust),
    ("T16-invalid-predecessor-receipt", t16_invalid_predecessor_receipt, flip_t16_trust),
    ("T16-stray-field-proposal", t16_stray_field, flip_t16_trust),
    ("T17-strict-type-aware-delta", t17_strict_delta, flip_t17),
    ("T18-decision-resolution-bundle", t18_decision_bundle, (flip_t18_bundle, flip_t18_rejection,
                                                             flip_t18_decider)),
    ("T18-decide-requires-options", t18_requires_options, flip_t18_requires),
    ("T18-options-given-together", t18_given_together, flip_t18_together),
    ("T18-options-apply-only-withdrawn", t18_apply_only_withdrawn, flip_t18_apply_only),
    ("T18-options-apply-only-ratification", t18_apply_only_ratification, flip_t18_apply_only),
    ("T19-record-run-id-grammar", t19_run_id_grammar, flip_t19),
    ("T19-legacy-name-recovery", t19_legacy_name_recovery, flip_t19_legacy),
    ("T20-homes2-legacy-journal-refused", t20_homes2_legacy_refused, flip_t20),
    ("T21-homes2-publish-typed", t21_homes2_publish, flip_t21),
    ("T22-homes2-claim-still-refused", t22_homes2_claim_refused, flip_t22),
    ("T23-homes2-crash-typed", t23_homes2_crash, flip_t23),
    ("T5-flip-unjournaled-write", t5_flip_write, None),     # FLIP_T5 itself is T5's discriminator
    ("T24-broken-manifest-fail-closed", t24_broken_manifest, flip_t24),
    ("T25-recovery-plan-under-capability", t25_plan_under_capability, flip_t25),
    ("T26-complete-before-projection", t26_complete_before_projection, flip_t26),
    ("T27-homes1-report-shape", t27_homes1_report, flip_t27),
    ("T28-spec15-probe-sentence", t28_spec15_sentence, flip_t28),
    ("T29-git-lifecycle-pins", t29_git_lifecycle, flip_t29),
    ("T30-unactivated-homes2-refused", t30_unactivated_homes2, flip_t30),
    ("T31-dead-capability-reclaimed", t31_dead_capability, flip_t31),
    ("T32-lone-active-record-reclaimed", t32_lone_active, flip_t32),
    ("T33-journal-rederived-under-capability", t33_journal_rederived_under_capability, flip_t33),
    ("T34-trigger-residual-disclosed", t34_residual_disclosed, flip_t34),
    ("T35-released-holder-not-reconciled", t35_released_holder_not_reconciled, flip_t35),
    ("T36-journal-home-reopened", t36_journal_home_reopened, flip_t36),
    ("T37-reclaim-names-operation", t37_reclaim_names_operation, flip_t37),
    ("T38-staging-removal-disclosed", t38_staging_removal_disclosed, flip_t38),
    ("T39-deletes-then-failure-disclosed", t39_deletes_then_failure_disclosed, flip_unreported_deletes),
    ("T40-held-refusal-discloses", t40_held_refusal_discloses, flip_unreported_deletes),
    ("T41-residuals-exact", t41_residuals_exact, flip_t41),
    ("T42-own-unlinks-disclosed", t42_own_unlinks_disclosed, (flip_t42_publication, flip_t42_unwind)),
    ("T43-publish-names-removals", t43_publish_names_removals, (flip_t38, flip_t43)),
    ("T44-continuation-refusal-discloses", t44_continuation_refusal_discloses, flip_t44),
    ("T45-unreported-removals-unknown", t45_unreported_removals_unknown, flip_t45),
    ("T46-disclosures-exact", t46_disclosures_exact, flip_t46),
    ("T47-reclaimed-outcome-scoped", t47_reclaimed_outcome_scoped, (flip_t47_grammar, flip_t47_poststate)),
    ("T48-prepare-names-created", t48_prepare_names_created, flip_t48),
    ("T49-home-absent-recovery-disclosed", t49_home_absent_recovery_disclosed, flip_t49),
    ("T50-leftover-lock-complete-scoped", t50_leftover_lock_complete_scoped, flip_t50),
    ("T51-lock-refusals-name-created", t51_lock_refusals_name_created, flip_t51),
    ("T52-publication-refused-before-intent", t52_publication_refused_before_intent,
     (flip_t52_message, flip_t52_rule)),
    ("T53-projection-existence-only", t53_projection_existence_only, (flip_t53_outcome, flip_t53_guard)),
    ("T54-header-exact", t54_header_exact, flip_t54),
    ("T55-lock-release-after-unlink", t55_lock_release_after_unlink, flip_t55),
    ("T56-nothing-opened-not-terminal", t56_nothing_opened_not_terminal, flip_t56),
    ("T57-retained-not-promised", t57_retained_not_promised, flip_t57),
    ("T58-released-holder-staging-reported", t58_released_holder_staging_reported, flip_t58),
    ("T59-recovery-lease-failure-scoped", t59_recovery_lease_failure_scoped, flip_t59),
    ("T60-release-failure-scoped", t60_release_failure_scoped, flip_t60),
    ("T61-lock-create-failure-disclosed", t61_lock_create_failure_disclosed, flip_t61),
    ("T62-output-failure-not-release", t62_output_failure_not_release, flip_t62),
    ("T63-t29-cleanup-covers-the-write", t63_t29_cleanup_covers_the_write, flip_t63),
    ("T64-diagnostic-failure-never-governs", t64_diagnostic_failure_never_governs,
     (flip_t64_conclude, flip_t64_recovery)),
    ("T65-interrupt-before-conclude", t65_interrupt_before_conclude, flip_t65),
    ("T66-release-interrupt-never-displaces", t66_release_interrupt_never_displaces, flip_t66),
    ("T67-ambient-exception-not-pending", t67_ambient_exception_not_pending, flip_t67),
    ("T68-release-mark-before-acquisition", t68_release_mark_before_acquisition, flip_t68),
    ("T69-release-failure-closes-fd", t69_release_failure_closes_fd, flip_t69),
    ("T69-token-failure-closes-fd", t69_token_failure_closes_fd, flip_t69),
    ("T70-tag-after-unwind", t70_tag_after_unwind, flip_t70),
    ("T71-allocation-residual-disclosed", t71_allocation_residual_disclosed, flip_t71),
    ("T72-decision-supersession", t72_supersession, flip_t72_link),
    ("T72-supersedes-record-id", t72_record_id, flip_t72_record_id),
    ("T72-supersedes-proposed-landing", t72_proposed_landing, flip_t72_landing),
    ("T72-supersedes-undecided-target", t72_undecided_target, flip_t72_decided),
    ("T72-supersedes-invalid-target", t72_invalid_target, flip_t72_target),
    ("T72-supersedes-wrong-type-target", t72_wrong_type_target, flip_t72_target),
    ("T72-supersedes-missing-target", t72_missing_target, flip_t72_unchecked_target),
    ("T72-supersedes-wrong-namespace-target", t72_wrong_namespace_target, flip_t72_unchecked_target),
    ("T72-supersedes-fork", t72_fork, flip_t72_fork),
    ("T72-supersedes-cycle", t72_cycle, flip_t72_cycle),
    ("T72-decide-chain-join", t72_chain_join, flip_t72_chain),
    ("T72-decide-superseded-record", t72_superseded_record, flip_t72_chain),
    ("T72-decide-superseded-record-plain", t72_superseded_record_plain, flip_t72_chain),
    ("T72-decide-archived-resolution", t72_archived_resolution, flip_t72_archive),
    ("T72-decide-archived-successor", t72_archived_successor, flip_t72_archive),
    ("T72-decide-unreadable-archive", t72_unreadable_archive, flip_t72_archive),
    ("T72-supersedes-archived-head", t72_archived_head, flip_t72_archived_target),
    ("T72-supersedes-archived-fork", t72_archived_fork, flip_t72_archived_target),
    ("T72-supersedes-archived-withdrawn-target", t72_archived_withdrawn_target, flip_t72_archived_target),
    ("T72-supersedes-archived-cycle", t72_archived_cycle, flip_t72_archived_cycle),
    ("T72-register-ruling-and-pattern", t72_register, flip_t72_links),
    ("T72-register-assistant-ruling", t72_assistant_ruling, flip_t72_trust),
    ("T73-contribution-delivery-bundle", t73_contribution_delivery, (flip_t73_delivery, flip_t73_rejection,
                                                                    flip_t73_receipt)),
)


# Whether the run in progress also drives each test's flip: set by self_test, read inside the lifecycle,
# whose callback takes no arguments.
_red_on_revert = [False]


def self_test(red_on_revert=False):
    """Run the whole gate inside the OPF git lifecycle (_opf_oplock._st_with_git_lifecycle): caller
    HOME/XDG stay out of fixture reads, and its PATH git wrapper reasserts the system-config pins after a
    production helper strips every GIT_* variable (the operation capability's rev-parse does), so no git
    call, fixture or production, in-process or in a killed child, reads the host's system configuration.
    Its verdicts are assert statements (some carry fixture steps), so under -O or -OO it refuses with exit 2."""
    refusal = _optlevel.assert_verdict_refusal("check_opf_record.py --self-test")
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 2
    if shutil.which("git") is None:
        print("OPF-RECORD SELF-TEST ERROR: git is not on PATH (the fixtures need real commits); exit 2",
              file=sys.stderr)
        return 2
    _red_on_revert[0] = red_on_revert
    try:
        return _opf_oplock._st_with_git_lifecycle(_self_test_isolated)
    finally:
        _red_on_revert[0] = False


def _self_test_isolated():
    red_on_revert = _red_on_revert[0]
    ran, failures = [], []

    def check(name, test):
        ran.append(name)
        try:
            test()
        except AssertionError as exc:
            failures.append("{}: {}".format(name, exc))

    def discriminate(name, test, flip):
        """The flip must turn the test red with an assertion (never an unrelated harness fault), and the
        unflipped test must stay green around it."""
        test()
        try:
            with flip():
                test()
        except AssertionError:
            pass
        else:
            raise AssertionError(name + " survived its flip")
        test()

    base = Path(tempfile.mkdtemp(prefix="opf-record-gate-")).resolve()
    try:
        if opf._bootstrap() != 0:
            raise Harness("opf bootstrap failed")
        env = Env(base)
        assert_no_auto_maintenance(env, base)
        fx = Fixtures(base, env)
        for name, test, _flip in TESTS:
            check(name, lambda test=test: test(fx))
        if red_on_revert:
            for name, test, flips in TESTS:
                flips = () if flips is None else flips if isinstance(flips, tuple) else (flips,)
                for flip in flips:
                    label = name if len(flips) == 1 else "{}:{}".format(name, flip.__name__)
                    check("red-on-revert-" + label,
                          lambda name=label, test=test, flip=flip: discriminate(name, lambda: test(fx), flip))
            check("red-on-revert-T5-crash-prestate-or-poststate",
                  lambda: discriminate("T5", lambda: t5_crash(fx),
                                       lambda: _child_flip(fx)))
    except Exception as exc:  # noqa: BLE001  a harness fault is cannot-evaluate, never a verdict
        print("OPF-RECORD SELF-TEST ERROR: {!r}".format(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("OPF-RECORD SELF-TEST: exercised " + ", ".join(ran))
    if failures:
        for failure in failures:
            print("FAIL: " + failure, file=sys.stderr)
        return 1
    print("OPF-RECORD SELF-TEST PASSED")
    return 0


@contextlib.contextmanager
def _child_flip(fx):
    """The T5 flip as a context: while active, t5_crash's killed children run with FLIP_T5."""
    _t5_flip[0] = FLIP_T5
    try:
        yield
    finally:
        _t5_flip[0] = ""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in (["--self-test"], ["--self-test", "--red-on-revert"]):
        print("check_opf_record: expected --self-test [--red-on-revert]", file=sys.stderr)
        return 2
    return self_test(red_on_revert="--red-on-revert" in args)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
