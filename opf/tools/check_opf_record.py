#!/usr/bin/env python3
"""OPF record-authoring gate (spec 8.8): `opf record` behaviour, and red-on-revert discriminators.

  check_opf_record.py --self-test                    the fixture suite (T1-T117)
  check_opf_record.py --self-test --red-on-revert    the same, plus each test's flip must turn it red
                                                     through an assertion (_discriminate)

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
  T5  a kill at each journal step of create, of an assistant transition (three operands: counters,
      index, worklog), of a handoff-superseding create (three operands: the supersession and the appended
      handoff land together or not at all once reconciled) and of a maintainer done-with-receipt (four
      operands: counters, backlog index, done index, worklog) leaves the killed run's lease. The
      guarantee is recovery atomicity, not an untorn on-disk state at the instant of the kill: a kill
      mid-apply can leave an operand's bytes torn (neither the prestate nor the poststate), so BEFORE
      reconciliation the matrix asserts the write-ahead evidence instead (whenever any operand's bytes
      differ from the prestate, the killed run's own journal transaction is present to drive recovery).
      The spec 8.5 at-most-one-current handoff rule additionally holds CONTINUOUSLY, not only once
      reconciled: at every kill point the matrix asserts that the on-disk handoff index, wherever its
      bytes parse at all, holds at most one clean current handoff, so no intermediate on-disk state of a
      post ever shows a reader a second current handoff (a torn, unparseable payload, and a parsed index
      whose record value is not an array, count none and are recovery's business; a RecursionError or
      MemoryError at the parse turns on the stack and memory left to the run, not on the bytes alone, so
      the check cannot evaluate, never a pass).
      The next run releases that confirmed-dead holder's
      leftover lease through its own reconciliation (spec 5.7, the T74 dead-run release clause) and
      reconciles, leaving the operands exactly the prestate or exactly the poststate, the poststate iff
      the transaction is COMPLETE, and no killed run reports an id (flips: write counters outside the
      journaled transaction; write the handoff index outside it, which only the handoff-superseding
      scenario can catch, so a green run proves that scenario discriminates on its own; land the handoff
      index at a parseable two-current state inside the journaled apply, which the write-ahead evidence
      cannot catch: the continuous at-most-one-current assertion turns red on the exposure itself, at the
      kill point, where without it the corruption would surface only later and indirectly, as the
      reconciliation refusing a changed operand; that flip counts only when that assertion is the one
      that turns red, by its scenario, kill point and count, and with the assertion disarmed the
      discrimination must fail)
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
  T12 an interrupted journal while another live run holds the shared lease (taken under the `upgrade`
      label, which no writer of this tooling now uses) refuses exit 2 with every byte untouched and the
      peer's lease intact (flip: recover without the lease)
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
  T20 a legacy .aiqt/record/journal on a homes-2 store is refused by name and never recovered,
      with the .aiqt subtree byte-unchanged and no typed journal home created (flip: probe the
      generation as 1, under which the legacy journal is reconciled in place)
  T21 a direct homes-2 _publish, its reservation published first under the same held capability and
      run id, lands ONE capability-bound transaction in .working/journals/record/journal under that run
      id with its projection at txn_record("record", <rid>), the operands rewritten, and nothing under
      .aiqt (flip: route homes 2 to the legacy journal root)
  T22 on a homes-2 store the verb claims through the live reservation seam: create records end to end
      with ONE irrevocable id reservation published under the COMPLETE transaction's own run id, its
      claims exactly the counters advance, and a non-regular, unreadable (an injected permission
      error), malformed, wrong-type (a string or a boolean claims value), conflicting or misnamed
      reservation refuses exit 2 with every byte untouched, never a silent fallback to the homes-1
      counters (flips: the reviewed head's refusing seam; a publication that skips the reservation;
      the retired validate_manifest patch, under which the run refuses at the manifest; and one flip
      per new case, each disabling that case's branch alone: an entry whose open fails read as holding
      no reservation, a decimal-string claims value checked as its integer (whose run then crashes at
      the scan's high-water comparison, the injection that flip declares), a boolean claims value
      checked as an integer; the refusal cases run before the create, so each flip reaches its case)
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
      continues to a plain homes-2 run that records, its reservation published under its run id
      (flip: the acquisition reports a reclaim it did not make)
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
      text; the reviewed head's rule; a doctor that reads no reservation, under which the next run is held)
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
  T74 recovery applies the spec 5.7 never-seize and dead-run release clauses: a leftover lease whose
      holder is confirmed dead on this host is released through the reconciliation itself, in the same
      run, and the outcome names it;
      a LIVE local holder, a confirmed-dead holder on another host, a dead holder under a non-closed
      payload schema, a malformed oversized payload with a valid 64 KiB prefix, an EPERM liveness probe,
      and another verb's dead holder each refuse before any recovery write with every byte intact (never
      seized); and a refusal recovery raises after a release still carries the released-lease report
      beside the scoped no-journal-no-operand sentence (flips: the plain atomic recovery claim; the
      unscoped nothing-written sentence; and one flip per never-seize guard: liveness probe dropped, host
      binding dropped, closed-payload validation dropped, the bounded prefix read restored, EPERM read as
      dead, verb binding dropped)
  T75 two concurrent REAL recoveries: with recovery A paused between its passed ownership compare and
      its unlink of the dead run's leftover lease, recovery B refuses at the exclusive examination lock
      and writes nothing, so two recoveries can never both release, re-claim, and recover (never-seize
      across the recovery path itself; flip, applied inside the children: the examination serialization
      made a no-op, restoring the reviewed head's race, under which B's deferred unlink seizes A's live
      lease and both enter journal recovery)
  T76 an examination lock failure is diagnosed as what it is: a HELD lock (EWOULDBLOCK/EAGAIN) refuses
      naming a concurrent reconciliation, and ENOLCK, ENOTSUP, EINVAL or EBADF refuses naming a lock
      failure, never a concurrent reconciliation; each refuses with every byte untouched (flip: the
      round-2 broad catch, under which every flock failure reads as a concurrent reconciliation)
  T77 every failure after the leftover lease's release carries the released-lease report, whatever its
      type: an EMFILE on the retried O_EXCL claim, a JournalError reopening the lease directory, and an
      interrupt (the report attached as a note) (flip: the round-2 retry, which appended the report to a
      WriteGuardError alone)
  T78 with homes 2 activated as the record verb will run once activation admits it (SUPPORTED_HOMES = 2,
      no spec_version gate), a committed store declaring homes = 2 at the base spec_version 1.2.0, fresh
      or holding a legacy leftover lock, is refused at the journal-home probe before any reconciliation
      or write: every byte untouched, no journal home chosen, the store graded generation 1 (flips: the
      retired HOMES2_SPEC_VERSION patch, under which the verb reads that store as generation 2 and
      chooses the typed journal home; the generation-only probe of T30, under which the run publishes
      into the legacy journal on the fresh store and reconciles the leftover in place)
  T79 a homes-2 run killed after its reservation and before its publication leaves the reservation
      standing and the operands at the prestate; after the confirmed-dead reclaim, the next create
      derives ABOVE the orphaned reservation (the reserved numbers are never issued again), its own
      reservation says so, and the run records, exit 0: the final doctor treats the orphaned numbers
      as issued, never as missing records (flips: a derivation that ignores the reservation scan,
      under which the reserved ids are issued again; a doctor that reads no reservation, under which
      the run is held at the post-publication gate)
  T80 two runs claiming the same kind never receive the same id: two runs planned before either
      reserves derive the same numbers; the first reserves through the production claim-and-reserve
      path, and the second is refused at the held revalidation with no reservation of its own, no
      transaction and no operand published; re-planned, it derives the next numbers, both
      reservations standing disjoint (flips: the derivation flip of T79; a reservation step that passes
      the seam no planned claims to compare)
  T81 the homes-2 high-water reads every journal kind's reservations, the homes enumerated from
      JOURNAL_KINDS: an import-kind reservation of BI-1 and WL-1 moves the next create to BI-2 and
      WL-2, which records, exit 0, the final doctor reading that reservation too, and a malformed
      reservation in each other kind's home, plus a non-regular, an unreadable
      (an injected permission error), a wrong-type (a string or a boolean claims value), a conflicting
      (another kind's payload) and a misnamed entry in the import home, refuse exit 2 with every byte
      untouched (flips: the reviewed head's single record home; a doctor that reads only the record
      kind reservation home, under which the run is held at the post-publication gate; and T22's three
      flips of the unreadable, string and boolean branches; the refusal cases run before the
      cross-kind create)
  T82 the homes-2 publication boundary requires the reservation of the run id it publishes under: a
      direct _publish naming no run id, one with no reservation, and one whose reservation claims other
      numbers each refuse before any transaction opens (flip: the reviewed head's minted run id with
      no reservation evidence)
  T83 the harness's journal reader reads an empty journal only for confirmed absence: a regular file,
      a symlink, a dangling symlink and an unreadable journal home are harness faults (flip: the
      reviewed head's not-a-directory shortcut)
  T84 a leftover lease whose payload tomllib refuses with RecursionError (a deeply nested value, and a
      1001-part key under the emulated 3.14.8 key-part cap) is malformed: recovery refuses it with the
      held-lease refusal, every byte untouched (flip: the pre-fix lease parse, under which the
      RecursionError escapes to the record verb's unexpected-error backstop)
  T85 a broken allocations home refuses the homes-2 reservation scan, never read as holding no
      reservations, each leg its own test, for the import kind and for the record kind: the home a
      symlink, a regular file, under a parent that is a regular file, a mode-0 directory, a directory
      whose listing fails (an injected EIO), and a kind whose home cannot be derived (_allocations_rel
      raising) each refuse exit 2 with every entry of the store unchanged (snapshot: every entry beneath
      the root except .git, by content, symlink target, type and mode; a directory it cannot list is a
      harness fault, so the mode-0 legs snapshot before the chmod and after the mode is restored, and
      check the home's mode is still 0 when the run returns). Every leg but one refuses end to end with
      the scan's own message; the record kind's file-parent leg refuses end to end at the record journal
      inspection, and its direct scan call shows the scan's message. The mode-0 guard tests the
      effective uid alone: under uid 0 the mode-0 legs are named as not evaluated, never a pass, and the
      run exits 2 only when no other check failed (1 otherwise); a process that is not uid 0 but holds a
      read-override capability (CAP_DAC_OVERRIDE or CAP_DAC_READ_SEARCH) makes those legs fail (exit 1),
      fail-closed but reported as a failure (flips, one per scan handler, each turning that handler
      alone into return {}, each leg registered against the flip of the handler it reaches: the walk to
      the home's parent, the file-parent legs; the home's own no-follow open, the symlink, regular-file
      and mode-0 legs; the listing, the unlistable legs; the derivation of a kind's home, the
      underivable legs, the record kind's leg through a flip of its own that declares the crash its run
      then meets, the leg's own error raised again at the publication boundary's reservation read)
  T86 the discriminator binds each flip to an assertion: a flipped run that goes red only through a
      cannot-evaluate backstop (a crash the CLI turned into exit 2) or a harness exception never counts
      as discriminating, while an assertion red, or a backstop caused by a recorded injection the flip
      declares, does (flip: a discriminator that refuses only a signature TypeError at the backstop and
      lets a harness exception escape)
  T87 a declared backstop is bound to the recorded injection that must cause it, and the binding is
      decided out of process: the flipped run executes in a forked child of this gate (the gate's own
      interpreter and flags, so isolated by -I when the gate itself runs with -I), the child's stderr
      is an OS pipe, and the parent decides from bytes it read from that pipe itself which recorded
      injection, if any, a backstop line belongs to. The parent mints every declared injection's
      tokens and serials and computes its repr before the child is launched (_oop_declared,
      _DECLARED_INJECTIONS); the child receives the token pools alone, as what it needs to raise the
      declared injections. A line binds only when its bytes carry exactly one recorded, unconsumed
      token whose label the flip declares and are the backstop envelope the record CLI shares with
      opf adopt around a repr EQUAL to one the parent computed for that token's
      injection; anything the child prints that the parent did not record binds nothing, and
      a token the parent never minted binds nothing however it is shaped (tokens are unguessable and
      parent-held). So an unrelated crash under a declared flip, another exception type carrying the
      declared text, a twin of the injected exception raised in its place, a crash the CLI turns into
      an unexpected-error refusal, and a SystemExit from the flipped run each fail the discrimination;
      an unrelated error formatted with the injection's message and a child process's new exception
      printed from it carry the token but not the parent-computed repr and bind nothing; the
      injection's line forwarded only into a private capture, or cleared, leaves a later unrecorded
      twin's line unbound; a line printed, cleared and printed again binds once and strays once; a
      twin's line interleaved from a second thread (paused under sys.settrace or not) and a twin's line
      after a write under a lying __len__ stay unbound (the pipe carries the true bytes, whatever
      in-process bookkeeping was told); and a nested run's unrecorded diagnostic of another exception
      copied into the run's stderr binds nothing. Each injection a discriminated child raises is an
      instance of a private fault class (_PRIVATE_INJECTIONS: one per base type it stands in for,
      subclassing that base, so every handler that catches the base catches it alike), whose name its
      printed repr carries, followed by the serial the parent minted for its token, which only the
      injection object holds: an args copy rebuilt as the plain base type (NameError(*injected.args)),
      raised in its place, alone, after an empty write, after a token-free preamble, or with no write
      from the backstop of the injection, binds nothing, and neither does a rebuild of the private class
      from its args, a copy, deep copy or pickle round trip of it (each showing a fresh built serial), a
      same-named local class, an exception wrapping it or its repr, or an exception group holding it.
      The injection's own line is accepted however its bytes reach the pipe: one print, split writes,
      or writelines (flips, each a discriminator that binds less strictly: each declared text matched
      as a substring of any backstop line, a BaseException escaping; each line matched by its text to
      an emission from one run-wide list, whichever capture held it; each line written in one call
      bound to the exception its nearest record CLI frame held; each line bound by its token alone; an
      injection counted as printed into a capture on any write its backstop frame made there, empty or
      not; each line bound by in-process attribution, frame identity, capture spans and len(), all of
      which loaded test code in the same process could forge; each injection raised as its plain base
      type, so an args copy rebuilt as that type prints the injection's line byte for byte; and a line
      bound when a parent-computed repr, shown without its serial, occurs anywhere in it).
      Residual: the child holds its own declared injections by necessity, so test code that
      deliberately reproduces its own injection's line (writing the envelope around repr(injected), or
      moving the injection's serial onto another object of its private class) is not told apart from
      the backstop's own emission of it. The parent-held tokens and parent-computed reprs guard against
      accidental and unrecorded faults, never against a test author deliberately forging its own
      injection's output.
  T88 a flip's assertion red counts only when every injection the flip expects fired in the flipped run
      and each one that fired was seen at its backstop, on every path the test took: the child's
      verdict lists each injection it raised, and the parent requires each fired injection of an
      expected label to have its token consumed by a bound line and each expected label to have fired.
      Rejected: the declared injection raised and caught at its own site (no backstop line) before an
      unrelated held-lease refusal turns the assertion red; the declared injection caught once and then
      raised to its backstop; a declared flip whose run refuses before its injection is reached; and
      T75 under a broken flip (a wrong-arity stand-in for the examination lock), whose recovery A
      crashes into the record backstop so that T75's first assertion fails before its verdict reads,
      the crash's line reaching the parent because T75 reads and forwards each child's stderr exactly
      once on every path. Accepted: the declared injection reaching its own backstop (flips: a
      discriminator that refuses only stray backstop lines; T75's earlier collection, which drops
      each child's stderr when an assertion fails before its verdict reads)
  T89 posting a new handoff supersedes the current one in the same act (spec 8.5), landing the status the
      writer's own transition grammar derives for the posting actor (spec 8.4): a first handoff supersedes
      nothing and reports no status change; an assistant posting lands the old handoff at
      superseded/proposed with proposed_from recording current (awaiting ratification, surfaced so by
      HANDOFF.md), an automation posting the same, and a maintainer posting lands it at unqualified
      superseded; each posting takes this operation's updated_at, appends the supersedes link to the new
      record after its requested links, records the supersession in its worklog entry, reports the status
      change, and is doctor VALID once committed with HANDOFF.md showing exactly one current handoff
      (flips: the planner performs no supersession, the reviewed head's append-only create, which only the
      independent oracle refuses; a supersession that demands a previous current handoff, under which the
      first posting is refused; a supersession landed unqualified whatever the actor, which the planner's
      own validate_transition refuses, spec 8.4, the oracle's per-actor derivation covered by the unit
      vectors). A maintainer rejecting the posting's supersession proposal back to current while the new
      handoff stands current refuses whole, with the named two-current-handoffs diagnostic and every byte
      untouched (spec 8.8 restores only the one named record and settles no outcome for the newer post,
      so the writer invents none and keeps the spec 8.5 at-most-one-current rule); ratifying the proposal
      to superseded still passes, and a rejection with no other clean current handoff (a
      transition-proposed supersession) still restores current (flip: drop the sole-current rule, under
      which the rejection publishes two current handoffs and fails its own final doctor only after the
      store is dirtied). A legacy importer-authored current handoff is history outside
      every current-state join (spec 8.6): it neither blocks a clean posting nor receives the
      supersession, its bytes stay untouched, and the doctor and HANDOFF.md keep it out of the
      current-handoff join once posted over (flip: count importer-authored rows back into the writer's
      join, under which the posting wrongly supersedes the history and the oracle refuses). Each prior
      state the spec forbids refuses with its named diagnostic and every byte untouched, never published
      and then failed at the final doctor, each case its own test: a hand-edited second current handoff (a
      C-HANDOFF finding at rest) refuses the next posting (two-current-handoffs; flip: supersede whichever
      current handoff comes first, which the oracle's own one-current derivation still refuses), and a
      current handoff hand-rotated into an archive bucket (a C-ROTATION finding at rest) refuses it with
      the named archived-current-handoff diagnostic, ahead of the generic archive-walk refusal (flip: drop
      the named classification back to the generic walk refusal, which the needle assertion catches). The
      atomicity of the supersession rides T5: a kill at each journal step of the superseding create, once
      the next run reconciles, leaves the store at exactly the prestate or exactly the poststate, the old
      and new handoff never split, with the pre-reconciliation write-ahead evidence asserted at each kill
      point and a handoff-scoped unjournaled-index flip proving that scenario discriminates on its own.
  T89 a store whose backlog index an earlier emitter wrote with a literal U+2028 and U+2029 inside a
      basic string (doctor-VALID, views rendered over those bytes) takes a create: the write succeeds,
      the file is re-spelled with the \\u2028 and \\u2029 escapes, the existing record reads back
      unchanged, and doctor stays VALID; the same store with a literal separator in a comment, the
      separators in a literal string, or one extra space, each reading back as the same model, refuses
      exit 2 with every byte untouched (flips: no separator escape; the strict byte comparison; an
      acceptance that admits any bytes)
  T90 the store-read cap over every file a record operation would publish: with a small cap patched
      over the shared store module, a create whose re-spelled, appended index would exceed the cap
      refuses exit 2 BEFORE publication, naming the store-read cap, every byte untouched; with room
      under the cap the same create records, re-spells the legacy index, and stays doctor-VALID
      (flip: the size guard removed, so the over-cap index is published and the byte comparison
      turns red)
  T91 five runs each reserving one BI id through the reservation seam alone (no planner), each under
      its own acquired and released operation capability, claim the counter's base + 1 to base + 5:
      the seam derives every claim from counters.toml and every earlier reservation (flip: the
      seam's reservation scan returning nothing, so every run reserves the same number)
  T92 concurrent runs in separate processes never share a number: forked processes released together
      each take the operation capability (retrying while another holds it) and reserve one BI id,
      claiming exactly base + 1 to base + their count (flip: T91's)
  T93 two threads sharing one held capability, their reservation attempts made to overlap (the first
      held inside the seam's derivation until the second's attempt ends): the second is refused as the
      capability being used and only the first's reservation stands, at base + 1 (flip: the seam's
      held scope without the capability's claim, so both derive the same number and both reserve it)
  T94 the held reservation step refuses a plan whose ordered ids its demand and planned claims do not
      imply (a wrong number, another order, an id missing or extra) with nothing reserved, and the
      plan's own ids then reserve its claims (flip: the planned-id check removed, so only the claims
      are compared and the wrong ids reserve)
  T95 a legacy importer-authored backlog item seated in the clean index of a doctor-VALID store is never a
      clean-series operand (spec 8.6, 8.8): a maintainer transition, an assistant proposal and a maintainer
      done-with-receipt each refuse exit 2 naming importer-authored-operand with every byte untouched, and
      a new clean item linking derives_from back records with the history's bytes unchanged; a clean
      decide superseding an importer-authored resolution still records, the target's bytes unchanged and
      the store doctor VALID (flips: the operand's author check removed, so the refusals publish; the
      check applied to the --supersedes target too, so the remedy refuses)
  T96 a malformed lease is present, so it is held: a create refuses exit 2 with the held-lease refusal,
      the lease not deleted and every byte untouched (flip: an acquisition that reads a lease it cannot
      parse as absent and removes it)
  T97 a store below its git repository root, with a foreign held lease, reaches the held-lease refusal
      naming the holder, never the dirty-store refusal; the lease and every byte untouched (flip: the
      lease exclusion compares porcelain paths without the store's prefix strip)
  T98 a FIFO at the lease path refuses promptly in a bounded child, naming a present non-regular lease;
      the FIFO and every byte untouched (flip: the held-lease message opens the lease without
      O_NONBLOCK, so the child blocks past its bound)
  T99 a canonical, uncommitted counters edit refuses exit 2 before mutation, naming the path and advising
      commit or move aside with no restore command; every byte, the index and HEAD untouched (flip: the
      cleanliness gate as a no-op)
  T100 an ignored, untracked file at a view's destination refuses exit 2 the same way, every byte and the
      index untouched; moved aside, the absent destination still refuses its ignore rule, and with the
      rule corrected the create records, the saved owner bytes and the ignored content outside the
      planned scope intact (flip: the dirty probe without --ignored=matching)
  T101 the whole store untracked and ignored, which git collapses to `!! .working/`, still names every
      planned destination and refuses exit 2, every byte untouched (flip: no ignored-ancestor expansion)
  T102 an ignored directory at a view's destination, which git reports with a trailing slash, refuses
      exit 2 naming it, every byte untouched (flip: the dirty probe without --ignored=matching)
  T103 ignored content at a declared unmanaged prefix sibling of a destination, which git reports under
      literal pathspecs, is outside the planned scope: the create records and the bytes survive (flip:
      every ignored record admitted as dirt)
  T104 declared unmanaged, ignored content inside .working is outside the planned scope: the create
      records and the bytes survive (flip: the store scope widened to all of .working)
  T105 a store nested under a real non-UTF-8 directory name with an ignored, untracked operand, view
      destination, or collapsed .working ancestor refuses exit 2 naming the destination; every byte, the
      index and HEAD untouched (flip: the lossy prefix round trip with its silent non-match fallback)
  T106 an absent ignored view destination, and an absent ignored product destination (the root VERSION
      view), each refuse exit 2 naming the path, every byte untouched (flip: the ignore probe for absent
      destinations as a no-op)
  T107 an ignored lease, and a directory lease (untracked, or ignored), each get the never-seize
      refusal, never the dirty-store remedy, the lease and every byte untouched (flip: a lease exclusion
      that excludes nothing)
  T108 a lease committed at HEAD and deleted in the worktree refuses exit 2 naming the TRACKED lease and
      spec 5.7, every byte untouched (flip: the old exclusion dropping every record on the lease path)
  T109 a view under an external normalizing clean filter (the git-lfs shape) reads dirty in the racy-clean
      window because the probe never runs the filter: the create refuses exit 2 naming the git-lfs-shape
      filter and settling the worktree, every byte untouched (flip: no filter neutralization)
  T110 receipt-repair (spec 8.8), the maintainer's reviewed repair of a ratified item with no receipt, over a
      committed BI-1 a reviewed hand edit put at unqualified done (the C-RECEIPTS finding at rest), with
      homes 2 activated as T79 does: the repair records exit 0 with exactly one receipt_of receipt and its
      own worklog entry, the backlog index bytes unchanged and no status change, its run's reservation
      claiming exactly the receipt's and the entry's numbers, doctor VALID, and a second repair refuses
      (flip: no reservation published, which the publication boundary refuses); at homes 1, with no
      reservation home, it refuses exit 2 before any file but the manifest is read, every byte untouched
      (flips: the homes guard dropped; the request check's homes refusal dropped, so the planner refuses
      only after the operand reads); an item whose only receipt is importer-authored takes the clean
      receipt beside it, doctor VALID (flip: the writer's receipt join counting importer-authored
      receipts); and a kill at each journal step of its three-operand publication, every hook
      kill_points(3) names but after-lock, which a homes-2 publication never reaches (that run records),
      leaves the reservation standing and reconciles to exactly the prestate or the poststate, the backlog
      index never written, the next repair deriving above the reserved numbers (flip: the done index
      written outside the journaled transaction, in the killed child)
  T111 an assistant or automation receipt-repair refuses before the store is resolved, every byte untouched
      (flip: the maintainer check dropped)
  T112 receipt-repair refuses an item at open, active, dropped or done/proposed, the last pointed to
      done-with-receipt (flip: the state guard dropped, so a later guard refuses with another message); an
      item already holding its clean receipt (flip: the receipt check dropped); a done row naming the item
      whose links the doctor's receipt join cannot interpret, which leaves that join not evaluated (flip:
      the active done rows' interpretation check dropped, so a second receipt publishes and fails the final
      doctor); a backlog row beside the item whose status the doctor's receipt join cannot interpret, which
      leaves that join not evaluated too (flip: the active backlog rows' interpretation check dropped, so
      the receipt publishes and is refused only after publication); an item whose receipt sits in the
      archive, and an archive the doctor's walk does not pass
      (flip, for both: the active-only read, under which a second receipt publishes and fails the final
      doctor), each with every byte untouched
  T113 a peer reservation of the receipt's number landing between planning and the held capability refuses
      the repair at the held revalidation with nothing reserved or written; the re-run claims above it and
      records (flip: T80's reservation step without the planned claims); and the backlog index, which the
      repair only reads, edited between planning and the held lease refuses it at the held re-read with
      nothing reserved or written (flip: the held re-read of the operands alone)
  T114 the reviewed edit uncommitted, or committed and then edited again, refuses exit 2 naming the commit,
      every byte untouched (flip: the committed-row check dropped, so the repair publishes and its final
      doctor fails after publication)
  T115 HEAD moved between planning and the held lease, to a commit not holding the item at all and to the
      one holding it at active, refuses exit 2 at the committed-row check read again under the held lease,
      every byte untouched, no reservation and no transaction (flip: the held re-check dropped, so the
      repair publishes over a prior commit that does not hold the item and records exit 0)
  T116 HEAD moved after that held read and before the final doctor, to a commit not holding the item at all
      and to the one holding it at active: the receipt is already published, and each run refuses exit 2
      after publication, nothing offered as recorded, with the scoped recovery text and its moved-HEAD
      caution (flip: the committed-row check read again after the final doctor dropped, so over the commit
      not holding the item the repair records exit 0)
  T117 receipt-repair refuses before anything is written a store its final doctor would certainly fail: a
      done record of lookalike actor kind naming another item, which leaves the receipt join not evaluated
      (flip: the provenance check dropped), and a backlog index the repair only reads carrying a comment,
      the canonical-bytes finding (flip: that index's canonical check dropped); under either flip the
      receipt publishes and the final doctor fails after publication

Exit convention: 0 every assertion passes; 1 an assertion fails; 2 the harness cannot evaluate (git absent
or unusable, temporary storage unusable, or any unexpected harness fault), or, when no assertion fails, a
check this environment cannot build (T85's mode-0 legs under uid 0) is named as not evaluated; never a
clean skip.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_record.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import copy
import datetime
import errno
import io
import json
import os
import pickle
import re
import secrets
import select
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
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
HO_INDEX = MACH + "/handoff.index.toml"
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


class CannotEvaluate(Exception):
    """One check this environment cannot build (a mode-bit vector under uid 0): the check is named as not
    evaluated and the run exits 2 (1 when another check failed), never a pass; the other checks still run."""


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


# Any crash inside a CLI run (a flip's replacement raising NameError, or a TypeError once it no longer
# matches its caller) meets that CLI's class-width backstop, which prints a cannot-evaluate line and exits
# 2, an ordinary refusal exit that a test's assertion then reads as red. Every such line, from any verb
# (record, render, doctor, upgrade, adopt) or the cli self-test, matches _BACKSTOP, as does the refusal a
# store-resolution wrapper makes of a crash ("refused: unexpected error resolving the store ... (repr)").
# _discriminate observes the flipped run out of process: the run executes in a forked child whose
# stderr is an OS pipe, cli tees each capture write onto that pipe (_EmissionStderr), child, die_holding
# and the T75 collection (_t75_collect, on every path T75 takes, an assertion that fails before its
# verdict reads included) copy the stderr of the processes they ran onto it (_oop_forward), and the
# parent scans the bytes it read itself for every backstop occurrence, so the flipped run's red is
# judged with every backstop it met in view. While one of the looser in-process discriminators that
# serve as T86 and T87 flips runs a flip, it pushes a list here instead, and the same readers add each
# backstop occurrence in the stderr they read to the innermost list (_note_backstops). A crash that
# production code swallows, or wraps in a refusal of any other wording, prints no such line and is not
# seen either way; when that crash is an injection the flip expects, _discriminate still refuses the
# red, as it requires each expected injection that fired to have its line bound (_unseen_injections).
_BACKSTOP = re.compile(r"(?:cannot evaluate|harness error|refused): unexpected error[^\n]*")
_flip_watch = []

# Under _discriminate the discriminated run is a forked child holding, in _oop_tokens, the
# per-label pools of tokens the parent minted before the fork: each injection site a declared backstop
# may come from draws the next token of its label as it raises (_injected) or as a reverted guard's
# crash passes its one expected point (_crash_point), and writes it into the exception's own message, so
# the backstop line that prints that exception carries it, whichever write pattern brought its bytes to
# the pipe. The parent binds a line only by a token it minted itself plus the repr it computed itself
# (_oop_declared, _bound_injection_oop); a token shows only that the text came from the injection's
# message (an exception raised from its args, an error formatted with its message, or a child printing
# it carries the token too), so the line must also be the shared backstop envelope (_BACKSTOP_ENVELOPE)
# around exactly the repr the parent computed (equality, never a substring, so an exception wrapping the
# injection or its repr binds nothing), and that repr names the private fault class the child raised the
# injection as (_PRIVATE_INJECTIONS) and the serial the parent minted for that token, which only the
# injection object itself carries (_OpfInjection). Each injection the child raises is also recorded,
# with the token it drew or None, in _oop_fired, which the child's verdict carries back, so the parent
# can require every injection its flip expects to have fired and each one that fired to have its own
# line bound (_unseen_injections): a declared injection that is caught, swallowed, or printed out of
# view, followed by an unrelated assertion, never counts as the flip's red.
# The in-process T87 flips _discriminate_token_only, _discriminate_any_write and
# _discriminate_in_process push a dict here instead, {token: (label, exception object)}, minting a
# fresh token per injection in-process; _discriminate_substring, _discriminate_runwide and
# _discriminate_write_call push a list and record (label, exception object) with the message untouched.
_flip_injections = []
_INJECTION_TOKEN = re.compile(r"\binjection-[0-9a-f]{16}\b")

# One discriminated child run's state: _oop_tokens[0] holds the child's per-label pools of (token,
# serial) pairs (None outside a child), _oop_fired lists the (label, token or None) of each injection the
# child raised, in order, _oop_child[0] marks the child so cli tees capture writes onto the pipe, and
# _case_namespace[0] prefixes the child's fixture case directories (its Fixtures.count restarts at the
# fork's value, so without the prefix a child's copytree would collide with the parent's next case).
# _oop_serial counts forks parent-side; a child appends its serial to the namespace it inherited, so
# nested discriminated runs stay distinct too.
_oop_tokens = [None]
_oop_fired = []
_oop_child = [False]
_case_namespace = [""]
_oop_serial = [0]
# Tokens minted per declared label for one discriminated run: enough for every injection event a
# flipped run raises before its red (fail closed: an event past the pool gets no token, so its line
# binds nothing).
_OOP_POOL = 64
_OOP_TIMEOUT = 600.0


# The private fault classes a discriminated child raises its injections as (_injected, _crash_point):
# one per base type a declared injection stands in for, each subclassing that base, so every handler that
# catches the base (the record.cli and opf._cmd_record backstops included) catches it exactly alike, and
# each named so that its printed repr differs from the base's. Each instance also holds a serial in a
# slot, shown after the base repr (_OpfInjection): every construction draws a fresh "built-N" from
# _oop_built, and _injected then gives the injection the serial the parent minted for its token
# (_serialed), so of all the objects a child can make only the injection itself prints the repr the
# parent computed (_oop_declared). A slot is outside the exception's args and __dict__, so a copy.copy,
# copy.deepcopy or pickle round trip (each rebuilt through cls(*args) and then the __dict__) and every
# cls(*injected.args) rebuild, the private class's own included, print a fresh built serial; an instance
# made without __init__ prints "unbuilt"; an args copy rebuilt as the plain base type, or as a local
# class of the same name, prints no serial at all. _oop_private[0] is False only under the T87 flip
# _discriminate_plain_type, which raises each injection as its plain base type; _oop_exact[0] is False
# under the T87 flips _discriminate_plain_type and _discriminate_repr_substring, which show no serial
# and bind a line by a substring.
_oop_built = [0]
_oop_private = [True]
_oop_exact = [True]


class _OpfInjection:
    """The serial the private fault classes share: __init__ draws the next built serial into the
    class's _opf_serial slot, and the repr is the base's followed by that serial (with no serial under
    the T87 flips _discriminate_plain_type and _discriminate_repr_substring, _oop_exact)."""
    __slots__ = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _oop_built[0] += 1
        self._opf_serial = "built-{}".format(_oop_built[0])

    def __repr__(self):
        shown = super().__repr__()
        if not _oop_exact[0]:
            return shown
        return "{} {}".format(shown, getattr(self, "_opf_serial", "unbuilt"))


class _OpfInjectedNameError(_OpfInjection, NameError):
    """A declared NameError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedKeyError(_OpfInjection, KeyError):
    """A declared KeyError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedOSError(_OpfInjection, OSError):
    """A declared OSError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedBrokenPipeError(_OpfInjection, BrokenPipeError):
    """A declared BrokenPipeError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedMemoryError(_OpfInjection, MemoryError):
    """A declared MemoryError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedRuntimeError(_OpfInjection, RuntimeError):
    """A declared RuntimeError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedRecursionError(_OpfInjection, RecursionError):
    """A declared RecursionError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedTypeError(_OpfInjection, TypeError):
    """A declared TypeError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


class _OpfInjectedValueError(_OpfInjection, ValueError):
    """A declared ValueError injection of a discriminated child run."""
    __slots__ = ("_opf_serial",)


_PRIVATE_INJECTIONS = {NameError: _OpfInjectedNameError, KeyError: _OpfInjectedKeyError,
                       OSError: _OpfInjectedOSError, BrokenPipeError: _OpfInjectedBrokenPipeError,
                       MemoryError: _OpfInjectedMemoryError, RuntimeError: _OpfInjectedRuntimeError,
                       RecursionError: _OpfInjectedRecursionError, TypeError: _OpfInjectedTypeError,
                       ValueError: _OpfInjectedValueError}
# The attributes of a base type kept outside its args, carried over to its private rebuild.
_INJECTION_ATTRIBUTES = {NameError: ("name",),
                         OSError: ("errno", "strerror", "filename", "filename2"),
                         BrokenPipeError: ("errno", "strerror", "filename", "filename2")}


def _private_injection(exc):
    """exc rebuilt from its args as the private fault class of its exact type (_PRIVATE_INJECTIONS),
    with the attributes the base keeps outside its args (_INJECTION_ATTRIBUTES: NameError.name; an
    OSError's errno, strerror, filename and filename2) and its __dict__ (notes included) carried over;
    under the T87 flip _discriminate_plain_type (_oop_private[0] False) exc unchanged. A type with no
    private class is a harness fault, never a silent plain injection."""
    if not _oop_private[0]:
        return exc
    private = _PRIVATE_INJECTIONS.get(type(exc))
    if private is None:
        raise Harness("no private injection class for {}".format(type(exc).__name__))
    rebuilt = private(*exc.args)
    for attribute in _INJECTION_ATTRIBUTES.get(type(exc), ()):
        setattr(rebuilt, attribute, getattr(exc, attribute))
    rebuilt.__dict__.update(exc.__dict__)
    return rebuilt


def _serialed(exc, serial):
    """exc, a private injection, holding serial in place of its built one, so that its repr is the one
    the parent computed for its token; under the T87 flips _discriminate_plain_type and
    _discriminate_repr_substring (no private class, or no serial shown) exc unchanged."""
    if _oop_private[0] and _oop_exact[0]:
        exc._opf_serial = serial
    return exc


def _oop_write(text):
    """text's true bytes, written whole onto this discriminated child's pipe (fd 2): the unbound
    str.encode reads the real buffer, so a str subclass lying about its length or contents changes
    nothing the parent reads, and short pipe writes are continued to completion."""
    view = memoryview(str.encode(text, "utf-8", "backslashreplace"))
    while len(view):
        view = view[os.write(2, view):]


def _oop_forward(text):
    """Under a discriminated child run with no in-process discriminator active, copy a child
    process's stderr, as this process read it, onto the parent's pipe, so a backstop line a killed or
    concurrent child printed stays in the parent's view; otherwise nothing."""
    if _oop_child[0] and not _flip_watch and not _flip_injections and text:
        _oop_write(text)


def _note_backstops(text, written=()):
    """Add each backstop occurrence in `text`, one run's final stderr, to the innermost _flip_watch list
    as (its line, the recorded injections for which one record CLI backstop frame holding the injection
    wrote that whole line into the capture it was read from, _written_whole over `written`, that
    capture's spans): none for a child's stderr or any text read without such a capture, and None for a
    capture written anywhere but its end or truncated (_EmissionStderr.occurrences), whose contents are
    no longer the run's emissions as written, so none of its lines binds an injection."""
    if _flip_watch and text:
        _flip_watch[-1].extend((found.group(0), None if written is None else
                                _written_whole(found.start(), found.end(), written))
                               for found in _BACKSTOP.finditer(text))


def _tokenize(exc, token):
    """exc with token appended to its message (its last string argument, else a new last argument; an
    OSError's strerror too): the one transformation both the child's raised injection and the parent's
    declared copy of it go through, so their reprs agree by construction."""
    args = list(exc.args)
    strings = [index for index, arg in enumerate(args) if isinstance(arg, str)]
    if strings:
        message = args[strings[-1]]
        args[strings[-1]] = "{} {}".format(message, token)
        if isinstance(exc, OSError) and exc.strerror == message:
            exc.strerror = args[strings[-1]]
    else:
        args.append(token)
    exc.args = tuple(args)
    return exc


def _injected(label, exc):
    """exc, marked as injection `label` of the innermost discriminated run: in a discriminated child
    the next parent-minted (token, serial) pair of `label`'s pool is drawn, the token appended to its
    message (_tokenize) and the serial held by the private rebuild (_serialed), so any backstop line
    printing it carries a token and a serial the parent recorded before the fork; a label the flip
    never declared, or an event past the pool, gets neither and its line binds nothing (fail closed).
    Either way the child records the event, label and token or None, in _oop_fired, which its verdict
    carries to the parent. Under an in-process T87 flip that pushes a dict a fresh token is minted and
    recorded in-process, and under one that pushes a list exc is recorded untouched; outside any
    discriminated run exc is returned unchanged. In a discriminated child the exception returned is exc
    rebuilt as its private fault class (_private_injection), not exc itself, so the caller raises what
    this returns."""
    if _flip_injections and isinstance(_flip_injections[-1], list):
        _flip_injections[-1].append((label, exc))
    elif _flip_injections:
        token = "injection-" + secrets.token_hex(8)
        _tokenize(exc, token)
        _flip_injections[-1][token] = (label, exc)
    elif _oop_tokens[0] is not None:
        exc = _private_injection(exc)
        pool = _oop_tokens[0].get(label)
        token = None
        if pool:
            token, serial = pool.pop(0)
            _serialed(_tokenize(exc, token), serial)
        _oop_fired.append((label, token))
    return exc


def _crash_point(label, owner, name, expected):
    """owner.name, recording as injection `label` an exception it raises of exactly `expected`'s type
    and args (the crash a reverted guard lets the code reach, bound to the point it must come from), and
    in a discriminated child raising its private fault class in its place (_injected), with the
    traceback, cause, context and context suppression of the exception it replaces (raised after the
    handler, so the replaced exception is not chained on as a new context, and a None cause stays a None
    cause rather than becoming "from None"); any other exception passes unrecorded."""
    real = getattr(owner, name)

    def observing(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        except BaseException as exc:
            if type(exc) is not type(expected) or exc.args != expected.args:
                raise
            marked = _injected(label, exc)
            if marked is exc:
                raise
            marked.__cause__, marked.__context__ = exc.__cause__, exc.__context__
            marked.__suppress_context__ = exc.__suppress_context__
            traceback = exc.__traceback__
        raise marked.with_traceback(traceback)
    return patch.object(owner, name, observing)


class _EmissionStderr(io.StringIO):
    """A CLI run's captured stderr, read whole once the run returns (occurrences), so a backstop line is
    judged by the final contents however it was written: one print, split writes, or writelines (which
    calls write). Each write made while the nearest record CLI backstop frame (record.cli or
    opf._cmd_record) holds a recorded injection as its `exc` records the span of the contents it wrote,
    keyed by that frame and that injection; a write from any other frame, or from a frame holding an
    exception no injection recorded (an args copy, an unrelated error carrying the token), records
    nothing, and so does a write of nothing. A backstop line's printed set (_note_backstops) holds a
    recorded injection only when one such frame holding that injection wrote every character of the
    line, across one or more of its writes (so a line split across writes or sent through writelines
    loses nothing); as _bound_injection then requires the line to carry that injection's own token and
    repr, the set shows that the backstop frame holding that very object wrote this line, token and repr
    included, into this capture, never merely that the frame wrote something here (a write of nothing,
    of a preamble, or of the injection's repr alone, followed by another frame's line, adds nothing).
    Which recorded exception a line names is read from the token its text carries. The capture also
    tracks whether its contents are the run's output as appended: a write anywhere but the end (after a
    seek, or over an initial value) and any truncate mark it mutated, and a mutated capture binds none of
    its lines (fail closed), so a capture cleared and written again (T87's clear-reprint) stays refused
    rather than trusted."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mutated = False
        self._oop = False    # set by cli in a discriminated child: tee each write onto the pipe
        self._written = []   # (id of the backstop frame, its recorded injection, start, end) per write

    def write(self, text):
        start = self.tell()
        if start != len(self.getvalue()):
            self._mutated = True
        injections = _flip_injections[-1] if _flip_injections else None
        if isinstance(injections, dict) and injections and isinstance(text, str) and text:
            codes = (record.cli.__code__, opf._cmd_record.__code__)
            frame, held = sys._getframe(1), None
            try:
                while frame is not None and frame.f_code not in codes:
                    frame = frame.f_back
                held = None if frame is None else frame.f_locals.get("exc")
                if any(held is exc for _label, exc in injections.values()):
                    self._written.append((id(frame), held, start, start + len(text)))
            finally:
                del frame, held
        if self._oop and not _flip_watch and not _flip_injections and isinstance(text, str) and text:
            _oop_write(text)   # the true bytes, at write time: a later clear cannot unsend them
        return super().write(text)

    def truncate(self, size=None):
        self._mutated = True
        return super().truncate(size)

    def occurrences(self):
        """(the contents, the spans the record CLI backstop frames holding recorded injections wrote here
        as (frame id, injection, start, end), or None for a mutated capture), read together from this one
        capture."""
        return self.getvalue(), None if self._mutated else tuple(self._written)


def _written_whole(start, end, written):
    """The recorded injections one of whose backstop frames wrote every character of [start, end): the
    spans each frame wrote under an injection are appended in order, so they cover the range when they
    run on without a gap from start past end."""
    printed = []
    for key in dict.fromkeys((frame, id(exc)) for frame, exc, _start, _end in written):
        reach, exc = start, None
        for frame, held, span_start, span_end in written:
            if (frame, id(held)) == key and span_start <= reach < span_end:
                reach, exc = span_end, held
        if exc is not None and reach >= end and not any(exc is seen for seen in printed):
            printed.append(exc)
    return tuple(printed)


def cli(env, argv):
    """Run `opf <argv>` in-process under the scrubbed environment: (rc, stdout, stderr)."""
    out, err = io.StringIO(), _EmissionStderr()
    err._oop = _oop_child[0]
    with patch.dict(os.environ, env.vars, clear=True), contextlib.redirect_stdout(out), \
            contextlib.redirect_stderr(err):
        rc = opf.main(list(argv))
    _note_backstops(*err.occurrences())
    return rc, out.getvalue(), err.getvalue()


def record_cli(env, root, args):
    return cli(env, ["record"] + list(args) + ["--root", str(root)])


def snapshot(root):
    """Every entry beneath root except .git, each with its mode bits: a regular file by content, a symlink
    by its target text (never followed), a directory and any other type by its type. A directory the walk
    cannot list (root included) is a harness fault, never an entry silently left out of the comparison."""
    def unlistable(exc):
        raise Harness("snapshot cannot list the directory {!r} ({})".format(exc.filename, exc))
    result = {}
    for directory, dirs, files in os.walk(root, onerror=unlistable):
        dirs[:] = [d for d in dirs if not (Path(directory) == Path(root) and d == ".git")]
        for name in sorted(dirs + files):
            path = Path(directory) / name
            st = path.lstat()
            rel = path.relative_to(root).as_posix()
            if stat.S_ISREG(st.st_mode):
                kind = ("file", path.read_bytes())
            elif stat.S_ISDIR(st.st_mode):
                kind = ("dir",)
            elif stat.S_ISLNK(st.st_mode):
                kind = ("symlink", os.readlink(path))
            else:
                kind = ("other", stat.S_IFMT(st.st_mode))
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
        root = self.base / "{}{:03d}-{}".format(_case_namespace[0], self.count, name)
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

# The handoff-scoped T5 flip, applied inside the killed child: the HANDOFF INDEX (the one operand that
# carries both the supersession and the appended handoff) is written directly, OUTSIDE the journaled
# transaction, and only the other operands are journaled. The flip activates on a handoff index operand
# alone, so every other T5 scenario stays green under it and a red run can come only from the
# handoff-superseding scenario: the discrimination the aggregate FLIP_T5 cannot attribute (its
# unjournaled counters write already turns the FIRST scenario red before the handoff matrix runs).
FLIP_T5_HANDOFF = """
_journaled = record._publish
def _publish(ctx, plan, subcommand, cap=None):
    operands = list(plan.operands)
    for operand in operands:
        if operand.rel.endswith("/handoff.index.toml"):
            (Path(ctx.res.store_root) / operand.rel).write_bytes(operand.new_raw)
            operands.remove(operand)
            plan.operands = operands
            break
    return _journaled(ctx, plan, subcommand, cap=cap)
record._publish = _publish
"""

# The two-current T5 flip, applied inside the killed child: at the torn-payload kill point of the HANDOFF
# INDEX, instead of the torn prefix the flip leaves a PARSEABLE index whose superseded row reads current
# again beside the appended current handoff, then dies. The journaled transaction is already present (the
# kill point is inside apply), so the write-ahead evidence assertion stays green, the kill and lease
# assertions stay green, and the first assertion to turn red is the continuous at-most-one-current check
# over the intermediate on-disk state, naming the exposure itself (without that check the corruption
# surfaces only later and indirectly, as the reconciliation refusing a changed operand). The needle bytes
# exist only in a superseding handoff index payload, so every other scenario, and every other operand,
# keeps the engine's own torn behaviour.
FLIP_T5_TWO_CURRENT = """
import _journal
_torn = _journal._maybe_torn_payload
def _two_current(fd, data, i):
    if (os.environ.get(_journal.KILL_ENV, "") == "torn-payload:{}".format(i)
            and b'"superseded/proposed"' in data):
        _journal._write_all(fd, data.replace(b'"superseded/proposed"', b'"current"', 1))
        os.fsync(fd)
        os._exit(137)
    _torn(fd, data, i)
_journal._maybe_torn_payload = _two_current
"""

# The message of the continuous at-most-one-current assertion, and the one red FLIP_T5_TWO_CURRENT must
# produce: that assertion, at the handoff-superseding scenario's torn-payload kill of its handoff index
# (operand 1 of counters, handoff index, worklog), seeing two clean current handoffs.
T5_TWO_CURRENT = "T5 an intermediate on-disk state holds two current handoffs"
T5_TWO_CURRENT_EXPOSURE = (T5_TWO_CURRENT, ("handoff-supersede", "torn-payload:1"), 2)


def child(env, root, args, kill=None, flip="", timeout=180):
    script = _CHILD.format(tools=str(TOOLS), flip=flip)
    child_env = dict(env.vars)
    if kill is not None:
        child_env[journal.KILL_ENV] = kill
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)] + list(args),
                          capture_output=True, text=True, timeout=timeout, env=child_env)
    _note_backstops(proc.stderr)
    _oop_forward(proc.stderr)
    return proc


def journal_states(root, rel=None):
    """{transaction name: state} of the journal home `rel`. The empty map means ONLY confirmed absence
    (FileNotFoundError: the home or an ancestor is absent); a regular file, a symlink or an unreadable
    entry at the home is a harness fault (Harness, exit 2), never read as an empty journal."""
    rel = record.JOURNAL_REL if rel is None else rel
    jroot = Path(root) / rel
    try:
        jr_fd = journal.open_journal_root_from_path(str(root), rel)
    except FileNotFoundError:
        return {}
    except (OSError, journal.JournalError) as exc:
        raise Harness("the journal home {} is not a readable directory ({!r})".format(rel, exc))
    try:
        return {t.name: journal.classify_state(jr_fd, t) for t in journal._journal_txn_dirs(jr_fd, jroot)}
    finally:
        os.close(jr_fd)


def _clean_current_handoffs(data):
    """The clean (non-importer) current-handoff count of one on-disk handoff index state (the spec 8.5/8.6
    join over these bytes alone). Bytes that do not parse as TOML, and a parsed index whose record value
    is not an array, count no handoff: a torn payload is neither state, shows a reader no second current
    handoff, and is recovery's business (the write-ahead evidence assertion). A RecursionError or a
    MemoryError at the parse is CannotEvaluate, never a count: it depends on the stack and memory left
    to this process, not on the index bytes alone, so it shows nothing about how many current handoffs
    the bytes hold."""
    try:
        rows = tomllib.loads(data.decode("utf-8")).get("record", [])
    except ValueError:
        # tomllib.TOMLDecodeError, a UnicodeDecodeError and an over-long integer literal.
        return 0
    except (RecursionError, MemoryError) as exc:
        raise CannotEvaluate("the handoff index count could not finish its parse ({!r}); a two-current "
                             "index is not ruled out".format(exc)[:400]) from None
    if not isinstance(rows, list):
        return 0
    return sum(1 for r in rows if isinstance(r, dict) and r.get("type") == "handoff"
               and r.get("status") == "current"
               and not (isinstance(r.get("actor"), dict) and r["actor"].get("kind") == "importer"))


def t5_handoff_count_malformed(fx):
    """The count behind the continuous at-most-one-current assertion reads a torn or malformed handoff
    index as no handoff, never as a crash of the matrix: undecodable bytes, a TOML syntax error, an
    over-long integer literal (a bare ValueError) and a record value that is not an array (an integer, a
    boolean, a string or a single [record] table) each count 0. Deep nesting (RecursionError) and a
    parse that exhausts memory (MemoryError, injected at the parser over a well-formed index) are
    cannot-evaluate, never 0: neither is a property of the bytes alone, so neither may pass the
    assertion. Well-formed indexes count exactly their clean current handoffs (one or two current, while
    a superseded or importer-authored one counts nothing), so a count that reads every index as empty is
    red too. Every vector runs and every wrong count is named, so a red lists each vector it reaches."""
    current = '[[record]]\ntype = "handoff"\nstatus = "current"\n'
    cannot = "cannot-evaluate"
    wrong = []

    def expect(label, data, want):
        try:
            got = _clean_current_handoffs(data)
        except CannotEvaluate:
            got = cannot
        except (ValueError, RecursionError, MemoryError, TypeError) as exc:
            got = "escaped {!r}".format(exc)[:160]
        if got != want:
            wrong.append((label, got, want))
    for label, data, want in (("undecodable", b"\xff" + current.encode(), 0),
                              ("unparseable", b"[[record]\n", 0),
                              ("over-long-integer", ("x = 1" + "0" * 5000 + "\n").encode(), 0),
                              ("deeply-nested", ("x = " + "[" * 5000 + "]" * 5000 + "\n").encode(), cannot),
                              ("integer-record", b"record = 5\n", 0),
                              ("boolean-record", b"record = true\n", 0),
                              ("string-record", b'record = "handoff"\n', 0),
                              ("table-record", b'[record]\ntype = "handoff"\nstatus = "current"\n', 0)):
        expect(label, data, want)

    def exhausted(text):
        raise MemoryError("synthetic parse exhaustion")
    with patch.object(tomllib, "loads", exhausted):
        expect("memory-exhausting", current.encode(), cannot)
    for label, data, want in (("one-current", current, 1),
                              ("two-current", current + "\n" + current, 2),
                              ("superseded", current.replace("current", "superseded"), 0),
                              ("importer-current", current + 'actor = { kind = "importer" }\n', 0)):
        expect(label, data.encode(), want)
    assert not wrong, ("T5 the handoff count of each (index, got, want) below is wrong", wrong)


def flip_t5_count_narrow():
    """The pre-fix count: only a UnicodeDecodeError or a TOMLDecodeError reads as no handoff, so a bare
    ValueError, a RecursionError or a MemoryError escapes the parse, and an integer or boolean record
    value raises TypeError when iterated. A string or table record value iterates (its characters, its
    keys) without error and yields no row, so it counts 0 here as in the fixed count: those two vectors
    stay green under this flip. flip_t5_count_table_row reds the table one; the string one stays green
    under every flip here and only pins the rule."""
    def narrow(data):
        try:
            rows = tomllib.loads(data.decode("utf-8")).get("record", [])
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            return 0
        return sum(1 for r in rows if isinstance(r, dict) and r.get("type") == "handoff"
                   and r.get("status") == "current"
                   and not (isinstance(r.get("actor"), dict) and r["actor"].get("kind") == "importer"))
    return patch.object(sys.modules[__name__], "_clean_current_handoffs", narrow)


def flip_t5_count_empty():
    """A count that reads every index as holding no handoff, malformed or not."""
    return patch.object(sys.modules[__name__], "_clean_current_handoffs", lambda data: 0)


def flip_t5_count_host_zero():
    """The count that read a RecursionError or a MemoryError at the parse as no handoff: an index the
    parser could not finish, two-current or not, passes the at-most-one-current assertion."""
    real = _clean_current_handoffs

    def host_zero(data):
        try:
            return real(data)
        except CannotEvaluate:
            return 0
    return patch.object(sys.modules[__name__], "_clean_current_handoffs", host_zero)


def flip_t5_count_table_row():
    """A count that reads a single [record] table as a one-row array, so a table record value holding a
    current handoff counts it, where every production reader refuses a record value that is not an
    array."""
    def table_row(data):
        try:
            rows = tomllib.loads(data.decode("utf-8")).get("record", [])
        except ValueError:
            return 0
        except (RecursionError, MemoryError) as exc:
            raise CannotEvaluate("the handoff index count could not finish its parse ({!r})".format(
                exc)[:400]) from None
        rows = [rows] if isinstance(rows, dict) else rows
        if not isinstance(rows, list):
            return 0
        return sum(1 for r in rows if isinstance(r, dict) and r.get("type") == "handoff"
                   and r.get("status") == "current"
                   and not (isinstance(r.get("actor"), dict) and r["actor"].get("kind") == "importer"))
    return patch.object(sys.modules[__name__], "_clean_current_handoffs", table_row)


def _t5_scenarios(fx):
    """(label, base store or None for the template, args, operands in journal order): create over the
    template; an assistant transition (three operands) and a maintainer done-with-receipt (four operands)
    over a committed store whose BI-1 is active; and a handoff-superseding create (three operands) over a
    committed store holding the current HO-1, so the supersession and the appended handoff land together
    or not at all (spec 8.5)."""
    active = fx.case("t5-base-active")
    with ticking():
        step(fx, active, ["create", "--type", "backlog_item", "--title", "k"] + MAINTAINER, "BI-1")
        step(fx, active, ["transition", "BI-1", "active"] + MAINTAINER, "BI-1 active")
    posted = fx.case("t5-base-handoff")
    with ticking():
        step(fx, posted, ["create", "--type", "handoff", "--title", "k"] + ASSISTANT, "HO-1")
    return (("create", None, CREATE, (COUNTERS, BI_INDEX, WORKLOG)),
            ("transition", active, ["transition", "BI-1", "done"] + ASSISTANT, (COUNTERS, BI_INDEX, WORKLOG)),
            ("handoff-supersede", posted, ["create", "--type", "handoff", "--title", "k2"] + ASSISTANT,
             (COUNTERS, HO_INDEX, WORKLOG)),
            ("done-with-receipt", active, ["done-with-receipt", "BI-1"] + MAINTAINER,
             (COUNTERS, BI_INDEX, DN_INDEX, WORKLOG)))


def t5_crash(fx):
    for label, base, args, operands in _t5_scenarios(fx):
        _t5_matrix(fx, label, base, args, operands)


class _T5OtherRed(Exception):
    """FLIP_T5_TWO_CURRENT turned t5_crash red through an assertion other than the continuous
    at-most-one-current one at the exposure: not an assertion, so the discriminator fails by name."""


def t5_two_current_exposed(fx):
    """t5_crash as the two-current discriminator runs it. Unflipped it is t5_crash, every assertion its
    own. Under FLIP_T5_TWO_CURRENT the one red that counts is the continuous at-most-one-current
    assertion itself, by identity (T5_TWO_CURRENT_EXPOSURE: its message, scenario, kill point and a count
    of two); any other assertion, such as a later recovery assertion that sees the same corruption only
    indirectly as a reconciliation refusal, is raised as _T5OtherRed instead, so removing the immediate
    assertion cannot leave the discrimination green."""
    try:
        t5_crash(fx)
    except AssertionError as exc:
        if _t5_flip[0] == FLIP_T5_TWO_CURRENT and exc.args != (T5_TWO_CURRENT_EXPOSURE,):
            raise _T5OtherRed("the two-current flip went red through another assertion: {!r}".format(
                exc.args)[:1200]) from None
        raise


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
        # The killed run leaves its lease; its holder is confirmed dead, so the next run releases that
        # leftover through its own reconciliation (the T74 dead-run release clause, spec 5.7) and
        # reconciles.
        assert (Path(root) / LEASE).exists(), ("T5 the killed run leaves its lease", point)
        # Recovery atomicity, asserted BEFORE reconciliation: a kill mid-apply may leave an operand's
        # bytes torn (neither the prestate nor the poststate), and the write-ahead journal is what makes
        # that state recoverable, so whenever any operand's bytes differ from the prestate the killed
        # run's own journal transaction must already be present to drive the recovery; an operand changed
        # with no such transaction could never be restored and is asserted red here, not after the fact.
        interim = {rel: read(root, rel) for rel in operands}
        opened = {name for name in journal_states(root) if name not in retained}
        assert all(interim[rel] == pre[rel] for rel in operands) or opened, (
            "T5 an operand changed with no journal transaction to drive recovery", point)
        # The spec 8.5 at-most-one-current rule holds CONTINUOUSLY, not only once reconciled: at every
        # kill point, wherever the on-disk handoff index parses at all, it holds at most one clean
        # current handoff, so no intermediate state of a post shows a reader a second current handoff
        # (bytes that do not parse, or whose record value is not an array, count none; a parse ended by
        # a RecursionError or a MemoryError is cannot-evaluate, never a count).
        # The write-ahead evidence above cannot catch this (a two-current intermediate can sit inside a
        # journaled apply): this assertion reds on the exposure itself, at the kill point, where the
        # later recovery assertions would see it only indirectly, as a reconciliation refusal of a
        # changed operand. FLIP_T5_TWO_CURRENT drives it red, and its discriminator credits only this
        # assertion (T5_TWO_CURRENT_EXPOSURE).
        if HO_INDEX in operands:
            seen = _clean_current_handoffs(interim[HO_INDEX])
            assert seen <= 1, (T5_TWO_CURRENT, point, seen)
        result = record_cli(env, root, CREATE)
        refused(result, "was reconciled")
        assert "leftover single-writer lease" in result[2], ("T5 the released leftover lease is named",
                                                             point, result[2][-1200:])
        assert not (Path(root) / LEASE).exists(), ("T5 the leftover lease and recovery's own are released",
                                                   point)
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
    # A live peer takes the shared lease under another verb's label (`upgrade`, which no writer of this
    # tooling now uses), so the record recovery treats it as a live peer, never its own leftover.
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
    # Merge styles of a reject-and-re-propose cycle: the recorded field rides
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
# a doctor-INVALID store into a VALID one. Each refusal is its own test, so flip_t16_trust must turn
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


# --- T17: the strict type-aware delta comparison -------------------------------------------------------------

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


@contextlib.contextmanager
def flip_t18_requires():
    """Drop the requires half of the option guard: a decide without the options is planned, and its
    bundle's read of the missing --decision crashes (the injection its declared backstop is bound to)."""
    original = record._require_decision_options

    def apply_only(req, rid, rtype, cur_state, target, cur_qual):
        if "--decision" in req.values:
            original(req, rid, rtype, cur_state, target, cur_qual)
    with patch.object(record, "_require_decision_options", apply_only), \
            _crash_point("t18-requires", record, "_resolution_bundle", KeyError("--decision")):
        yield


def t18_given_together(fx):
    """--decision without --decided-by refuses in the parser, before the store is resolved."""
    root = fx.case("t18-given-together")
    with ticking():
        step(fx, root, PD_CREATE + MAINTAINER, "PD-1")
        refused_before_store(fx.env, root, ["transition", "PD-1", "decided", "--decision", "the inline layout"]
                             + MAINTAINER, "given together")


@contextlib.contextmanager
def flip_t18_together():
    """Drop the parser's given-together check: one option alone reaches the store, and the bundle's read
    of the missing --decided-by crashes (the injection its declared backstop is bound to)."""
    with patch.object(record, "_require_decision_pair", lambda seen: None), \
            _crash_point("t18-together", record, "_resolution_bundle", KeyError("--decided-by")):
        yield


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


def _rotate(fx, root, rid, index_rel=PD_INDEX, archived_rel=ARCHIVED_PD_INDEX, rtype="pending_decision"):
    """Release the whole worklog as 0.1.0 (release_cut, its published changelog summary, and VERSION) and
    rotate it into archive/2026 with `rid`'s row of `rtype` (a pending_decision unless named), archive.toml
    enumerating the moved id and span; then render and commit: doctor VALID (spec 12)."""
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
    index = model(root, index_rel)
    moved = [r for r in index["record"] if r["id"] == rid]
    assert len(moved) == 1, (rid, index)
    index["record"] = [r for r in index["record"] if r["id"] != rid]
    span = [worklog["entry"][0]["id"], worklog["entry"][-1]["id"]]
    documents = {
        VERSION: released, index_rel: index, WORKLOG: dict(worklog, entry=[]),
        archived_rel: {"schema": 1, "record": moved},
        ARCHIVE + "/worklog.toml": {"schema": 1, "entry": worklog["entry"]},
        ARCHIVE + "/archive.toml": {"schema": 1, "moved": [{"id": rid, "type": rtype,
                                                           "destination": archived_rel[len(MACH) + 1:]}],
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
    """An archived chain head. PD-2 decided superseding PD-1, then PD-2, the chain's head, rotated to the archive:
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


# --- T89: posting a new handoff supersedes the current one in the same act ----------------------------------

HANDOFF_VIEW = ".working/HANDOFF.md"
HO_CREATE = ["create", "--type", "handoff", "--title", "resume here"]


def _handoff_section(root, heading):
    """The entry lines of one section of the rendered HANDOFF view."""
    text = read(root, HANDOFF_VIEW).decode("utf-8")
    assert "## " + heading + "\n" in text, (heading, text)
    return text.split("## " + heading + "\n", 1)[1].split("\n## ", 1)[0].strip().splitlines()


def t89_handoff_supersession(fx):
    """A first handoff supersedes nothing and reports no status change; posting the second supersedes it in
    the same act (spec 8.5), landing the status the writer's transition grammar derives for the posting
    actor (spec 8.4): an assistant posting lands HO-1 at superseded/proposed with proposed_from recording
    current, HANDOFF.md surfacing it as awaiting ratification; an automation posting lands the same; a
    maintainer posting lands unqualified superseded. Each posting takes this operation's updated_at,
    appends the supersedes link after its requested links, records the supersession in its worklog entry,
    reports the status change, and is doctor VALID once committed, HANDOFF.md showing exactly one current
    handoff."""
    env = fx.env
    root = fx.case("t89-supersede")
    with ticking():
        out = step(fx, root, HO_CREATE + ASSISTANT, "HO-1")
        assert '"change": null' in out, ("T89 a first handoff reports no status change", out[-800:])
        first = row(root, "HO-1", HO_INDEX)
        assert first["status"] == "current" and "links" not in first, first
        doctor_valid(env, root)
        out = step(fx, root, HO_CREATE + ["--link", "relates=HO-1"] + ASSISTANT, "HO-2 supersedes HO-1")
        assert '"change": "HO-1 current -> superseded/proposed"' in out, (
            "T89 the assistant supersession is reported as the proposal it lands", out[-800:])
        old, new = row(root, "HO-1", HO_INDEX), row(root, "HO-2", HO_INDEX)
        assert old["status"] == "superseded/proposed" and old["proposed_from"] == "current", (old, new)
        assert old["updated_at"] == new["created_at"], (old, new)
        assert new["status"] == "current" and new["links"] == [
            {"rel": "relates", "id": "HO-1"}, {"rel": "supersedes", "id": "HO-1"}], new
        assert lifecycle(root)[-1] == "opf-record create HO-2 current\nsupersedes: HO-1", lifecycle(root)
        entry = model(root, WORKLOG)["entry"][-1]
        assert entry["links"] == [{"rel": "relates", "id": "HO-2"}, {"rel": "relates", "id": "HO-1"}], entry
        doctor_valid(env, root)
        assert _handoff_section(root, "Current") == ["- HO-2 resume here"], read(root, HANDOFF_VIEW)
        assert _handoff_section(root, "Awaiting ratification") == [
            "- HO-1 (superseded/proposed) resume here"], read(root, HANDOFF_VIEW)
        # An automation posting lands the same proposal shape; a maintainer then posts over it, and the
        # maintainer's supersession lands the unqualified terminal with no proposed_from (spec 8.4).
        out = step(fx, root, HO_CREATE + ["--actor", "automation"], "HO-3 supersedes HO-2")
        assert '"change": "HO-2 current -> superseded/proposed"' in out, out[-800:]
        auto = row(root, "HO-2", HO_INDEX)
        assert auto["status"] == "superseded/proposed" and auto["proposed_from"] == "current", auto
        doctor_valid(env, root)
        out = step(fx, root, HO_CREATE + MAINTAINER, "HO-4 supersedes HO-3")
        assert '"change": "HO-3 current -> superseded"' in out, (
            "T89 the maintainer supersession lands unqualified", out[-800:])
        done = row(root, "HO-3", HO_INDEX)
        assert done["status"] == "superseded" and "proposed_from" not in done, done
        assert row(root, "HO-4", HO_INDEX)["status"] == "current", row(root, "HO-4", HO_INDEX)
        doctor_valid(env, root)
        assert _handoff_section(root, "Current") == ["- HO-4 resume here"], read(root, HANDOFF_VIEW)
        assert _handoff_section(root, "Superseded") == ["- HO-3 resume here"], read(root, HANDOFF_VIEW)


def flip_t89_skip():
    """The planner performs no supersession (the reviewed head's behaviour: create only appends): only the
    independent oracle, which derives the handoff posting rule on its own, refuses the second posting."""
    return patch.object(record, "_handoff_supersession", lambda ctx, operand, ts: None)


def flip_t89_required_target():
    """A supersession that demands a previous current handoff: a first handoff, with nothing to supersede,
    is refused instead of posted."""
    original = record._handoff_supersession

    def demanding(ctx, operand, ts):
        target = original(ctx, operand, ts)
        if target is None:
            raise record.RecordError("posting a handoff requires a current handoff to supersede; fail-closed")
        return target
    return patch.object(record, "_handoff_supersession", demanding)


def flip_t89_unqualified():
    """A supersession landed unqualified whatever the posting actor (the spec-8.4 bypass this gate must
    catch): the landing derivation always returns the bare state. The planner's own validate_transition
    refuses that landing (an assistant terminal transition must land '/proposed', spec 8.4), so the
    assistant posting refuses and its test goes red; the independent oracle's per-actor derivation is
    covered by the unit vectors."""
    return patch.object(record, "_derived_status", lambda kind, spec, cur_state, target: target)


def t89_two_current_refused(fx):
    """The forbidden prior state (spec 8.5: at most one current handoff) refuses with its named diagnostic
    and every byte untouched, never published and then failed at the final doctor: a second current handoff
    hand-edited in canonically (a C-HANDOFF finding at rest) refuses the next posting."""
    env = fx.env
    root = fx.case("t89-two-current")
    with ticking():
        step(fx, root, HO_CREATE + ASSISTANT, "HO-1")
        index = model(root, HO_INDEX)
        forged = dict(index["record"][0], id="HO-2", title="a forged twin")
        index["record"] = index["record"] + [forged]
        write_commit(env, root, HO_INDEX, emit.emit_checked(index).encode("utf-8"), "a second current handoff")
        counters = model(root, COUNTERS)
        counters["counters"]["HO"] += 1
        write_commit(env, root, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
        rc, out, err = cli(env, ["doctor", "--root", str(root)])
        assert rc == 1 and "C-HANDOFF" in out, ("T89 two current handoffs are a doctor finding", rc,
                                                out[-1600:])
        refused_untouched(env, root, HO_CREATE + ASSISTANT, "two-current-handoffs")


def flip_t89_first_current():
    """Supersede whichever current handoff comes first when several stand: the forbidden prior state is
    laundered instead of refused with its named diagnostic. The independent oracle derives the one-current
    rule on its own and still refuses, so the named refusal this flip removes turns its test red."""
    original = record._current_handoffs

    def first_only(ctx, rows, verb="create handoff"):
        return original(ctx, rows, verb=verb)[:1]
    return patch.object(record, "_current_handoffs", first_only)


def t89_importer_history_posts(fx):
    """A legacy importer-authored current handoff in the clean index is history outside every current-state
    join (spec 8.6: the section 8.5 at-most-one-current rule evaluates only clean records authored by
    non-importer actors, and such a record keeps its bytes, its recorded state and its place in the clean
    series): a clean posting proceeds, supersedes nothing, and leaves the importer bytes untouched; the
    committed store is doctor VALID and HANDOFF.md keeps the history out of Current. A second clean
    posting then supersedes the clean current handoff only (the mixed historical-importer/clean-current
    case), the history again untouched."""
    env = fx.env
    root = fx.case("t89-importer-history")
    legacy = {
        "id": "HO-1", "type": "handoff", "status": "current", "title": "migrated",
        "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
        "actor": {"kind": "importer"}}
    index = model(root, HO_INDEX)
    index["record"] = index["record"] + [legacy]
    write_commit(env, root, HO_INDEX, emit.emit_checked(index).encode("utf-8"), "a legacy importer handoff")
    counters = model(root, COUNTERS)
    counters["counters"]["HO"] += 1
    write_commit(env, root, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    with ticking():
        out = step(fx, root, HO_CREATE + ASSISTANT, "HO-2 over importer history")
        assert '"change": null' in out, ("T89 importer history discharges no supersession", out[-800:])
        assert row(root, "HO-1", HO_INDEX) == legacy, ("T89 the importer bytes stay untouched",
                                                       row(root, "HO-1", HO_INDEX))
        posted = row(root, "HO-2", HO_INDEX)
        assert posted["status"] == "current" and "links" not in posted, posted
        doctor_valid(env, root)
        assert _handoff_section(root, "Current") == ["- HO-2 resume here"], read(root, HANDOFF_VIEW)
        assert _handoff_section(root, "Imported history") == [
            "- HO-1 (current) migrated"], read(root, HANDOFF_VIEW)
        out = step(fx, root, HO_CREATE + ASSISTANT, "HO-3 supersedes HO-2, the history untouched")
        assert '"change": "HO-2 current -> superseded/proposed"' in out, out[-800:]
        assert row(root, "HO-1", HO_INDEX) == legacy, row(root, "HO-1", HO_INDEX)
        assert row(root, "HO-3", HO_INDEX)["links"] == [{"rel": "supersedes", "id": "HO-2"}], (
            row(root, "HO-3", HO_INDEX))
        doctor_valid(env, root)
        assert _handoff_section(root, "Current") == ["- HO-3 resume here"], read(root, HANDOFF_VIEW)


def flip_t89_importer():
    """Count importer-authored rows back into the writer's current-handoff join (the conflation this gate
    must catch): the posting then wrongly supersedes the imported history. The independent oracle keeps
    its own importer exclusion and expects no supersession, so the posting refuses and the test goes
    red."""
    def counted(ctx, rows, verb="create handoff"):
        archived, problems = opf_check.archived_records(ctx.root_fd, ctx.machine_rel, ctx.types,
                                                        ctx.vendors, ctx.import_status, homes=ctx.homes)
        if problems:
            raise record.RecordError("the archive does not pass the doctor's own archive walk ({}); "
                                     "fail-closed".format("; ".join(problems)))
        recs = [opf_check._make_rec(r, record.HANDOFF, "active")
                for r in rows if isinstance(r, dict)] + archived
        return [rec for rec in recs
                if rec.rtype == record.HANDOFF and rec.state == "current" and rec.qual is None]
    return patch.object(record, "_current_handoffs", counted)


def t89_archived_current_refused(fx):
    """A current handoff hand-rotated into an archive bucket (a C-ROTATION finding at rest: the archive
    never holds a current handoff, spec 12) refuses the next posting with the named
    archived-current-handoff diagnostic, ahead of the generic archive-walk refusal, and every byte
    untouched."""
    env = fx.env
    root = fx.case("t89-archived-current")
    with ticking():
        step(fx, root, HO_CREATE + ASSISTANT, "HO-1")
        index = model(root, HO_INDEX)
        moved = index["record"]
        index["record"] = []
        (Path(root) / ARCHIVE).mkdir(parents=True)
        documents = {
            HO_INDEX: index,
            ARCHIVE + "/handoff.index.toml": {"schema": 1, "record": moved},
            ARCHIVE + "/archive.toml": {"schema": 1, "moved": [{
                "id": "HO-1", "type": "handoff", "destination": "archive/2026/handoff.index.toml"}]}}
        for rel, document in documents.items():
            (Path(root) / rel).write_bytes(emit.emit_checked(document).encode("utf-8"))
        fx.commit_all(root, "HO-1 hand-rotated to the archive while current")
        rc, out, err = cli(env, ["doctor", "--root", str(root)])
        assert rc == 1 and "C-ROTATION" in out, ("T89 the archived current handoff is a doctor finding",
                                                 rc, out[-1600:])
        refused_untouched(env, root, HO_CREATE + ASSISTANT, "archived-current-handoff")


def flip_t89_archived():
    """Drop the named archived-current classification back to the generic archive-walk refusal (the
    unreachable-diagnostic regression this gate must catch): the posting still refuses fail-closed, but
    not with the named diagnostic, so the needle assertion goes red."""
    def generic_only(ctx, rows, verb="create handoff"):
        archived, problems = opf_check.archived_records(ctx.root_fd, ctx.machine_rel, ctx.types,
                                                        ctx.vendors, ctx.import_status, homes=ctx.homes)
        if problems:
            raise record.RecordError("the archive does not pass the doctor's own archive walk ({}); "
                                     "fail-closed".format("; ".join(problems)))
        recs = [opf_check._make_rec(r, record.HANDOFF, "active")
                for r in rows if isinstance(r, dict)] + archived
        return [rec for rec in recs
                if rec.rtype == record.HANDOFF and rec.state == "current" and rec.qual is None
                and rec.actor_kind != "importer"]
    return patch.object(record, "_current_handoffs", generic_only)


def t89_reject_supersession(fx):
    """A maintainer rejecting the supersession proposal an assistant posting created, back to current,
    while the new handoff stands current, refuses whole with the named two-current-handoffs diagnostic
    and every byte untouched, never published and then failed at the final doctor: spec 8.8 bounds a
    rejection to restoring the one named record and settles no outcome for the newer post, so the writer
    invents none and keeps the spec 8.5 at-most-one-current rule after every reachable sequence.
    Ratifying the proposal to unqualified superseded still passes and leaves exactly one current handoff;
    a rejection with no other clean current handoff (a transition-proposed supersession over the sole
    handoff) still restores current, so the sole-current rule refuses nothing the spec allows."""
    env = fx.env
    root = fx.case("t89-reject-supersession")
    with ticking():
        step(fx, root, HO_CREATE + ASSISTANT, "HO-1")
        step(fx, root, HO_CREATE + ASSISTANT, "HO-2 supersedes HO-1")
        assert row(root, "HO-1", HO_INDEX)["status"] == "superseded/proposed", row(root, "HO-1", HO_INDEX)
        reject = ["transition", "HO-1", "current", "--reason", "the old handoff still stands"] + MAINTAINER
        refused_untouched(env, root, reject, "two-current-handoffs")
        out = step(fx, root, ["transition", "HO-1", "superseded"] + MAINTAINER, "HO-1 ratified")
        assert '"change": "HO-1 superseded/proposed -> superseded"' in out, out[-800:]
        done = row(root, "HO-1", HO_INDEX)
        assert done["status"] == "superseded" and "proposed_from" not in done, done
        doctor_valid(env, root)
        assert _handoff_section(root, "Current") == ["- HO-2 resume here"], read(root, HANDOFF_VIEW)
    root = fx.case("t89-reject-sole-current")
    with ticking():
        step(fx, root, HO_CREATE + ASSISTANT, "HO-1")
        step(fx, root, ["transition", "HO-1", "superseded"] + ASSISTANT, "HO-1 proposed superseded")
        pending = row(root, "HO-1", HO_INDEX)
        assert pending["status"] == "superseded/proposed" and pending["proposed_from"] == "current", pending
        out = step(fx, root, ["transition", "HO-1", "current", "--reason", "not superseded"] + MAINTAINER,
                   "HO-1 rejection restores current")
        assert '"change": "HO-1 superseded/proposed -> current"' in out, out[-800:]
        restored = row(root, "HO-1", HO_INDEX)
        assert restored["status"] == "current" and "proposed_from" not in restored, restored
        doctor_valid(env, root)
        assert _handoff_section(root, "Current") == ["- HO-1 resume here"], read(root, HANDOFF_VIEW)


def flip_t89_reject_guard():
    """The sole-current rule dropped from the rejection path (the reviewed head's behaviour): the
    rejection publishes a second current handoff and only the final doctor, after the store is dirtied,
    finds the C-HANDOFF, so the bytes-untouched assertion goes red."""
    return patch.object(record, "_require_sole_current_handoff", lambda ctx, rows, rid, rel: None)


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


class _Homes2SpecGate:
    """The record module's view of _opf_store with ONE name moved: SUPPORTED_SPEC_VERSION reads as
    HOMES2_SPEC_VERSION, so _load_manifest's spec_version gate (it admits only SUPPORTED_SPEC_VERSION,
    1.2.0) admits the real homes-2 declaration pair. Every other name, validate_manifest,
    homes_generation, HOMES2_SPEC_VERSION and SUPPORTED_HOMES included, is read through to the real
    module at each access, so a patch on _opf_store still reaches the record module. The move also
    inverts that gate: under it the record verb refuses EVERY 1.2.0 store (the store declares
    spec_version '1.2.0', not the tooling's 2.0.0), so only a store declaring the pair is exercised
    through it, and a vector about a 1.2.0 store runs without it (T78)."""

    def __getattr__(self, name):
        if name == "SUPPORTED_SPEC_VERSION":
            return _opf_store.HOMES2_SPEC_VERSION
        return getattr(_opf_store, name)


# The dispatcher refuses every store verb whose installed declaration contradicts the build (spec 16.1), and the
# build homes 2 is activated as here, SUPPORTED_HOMES = 2, is one whose declaration says homes = 2. So every
# vector that runs a verb under that patch also points the runtime check at that build's declaration: the file
# HOMES2_DECLARATION beside the case roots (the in-process vectors and their children name the same file, written
# with the same bytes by every writer) and the shipped-file digest of those bytes. The runtime check runs
# unpatched, and the declared release, spec_version and worklog generation are this build's own.
HOMES2_DECLARATION = "homes2-declaration/opf-declaration.toml"


def _homes2_declaration_bytes():
    import _opf_declaration
    return _opf_declaration.declaration_bytes(_opf_declaration.RELEASE_IDENTITY, _opf_store.SUPPORTED_SPEC_VERSION,
                                              2, _opf_store.SUPPORTED_WORKLOG)


@contextlib.contextmanager
def _homes2_declaration(root):
    """Point the dispatcher's runtime check at the homes-2 build's declaration, written beside `root`."""
    import hashlib
    import _opf_declaration
    data = _homes2_declaration_bytes()
    path = Path(root).parent / HOMES2_DECLARATION
    path.parent.mkdir(exist_ok=True)
    staged = path.parent / "{}.{}".format(path.name, secrets.token_hex(8))
    staged.write_bytes(data)
    os.replace(staged, path)
    with patch.object(opf, "_installed_declaration", lambda: path), \
            patch.object(_opf_declaration, "SHIPPED_DECLARATION_BYTES", data), \
            patch.object(_opf_declaration, "SHIPPED_DECLARATION_SHA256", hashlib.sha256(data).hexdigest()):
        yield


def _self_test_homes2_active(root):
    """Activate homes 2 over a fixture store: the manifest on disk declares the real homes-2 pair,
    spec_version HOMES2_SPEC_VERSION (2.0.0) and homes = 2, which the shipped validate_manifest accepts
    as it stands. Only what this build still needs to exercise homes 2 is patched: SUPPORTED_HOMES = 2
    (homes_generation activates nothing while it is 1), and the record module's spec_version gate
    through _Homes2SpecGate (_load_manifest refuses any spec_version but 1.2.0, the real pair
    included). validate_manifest, homes_generation and HOMES2_SPEC_VERSION are the shipped ones. The
    manifest bytes are restored on exit."""
    import contextlib
    import tomllib
    from unittest.mock import patch
    resolution = _opf_store.resolve_store(root)
    path = Path(resolution.store_root) / resolution.machine_rel / _opf_store.MANIFEST_NAME
    original = path.read_bytes()
    model = tomllib.loads(original.decode("utf-8"))
    model["opf"]["homes"] = 2
    model["opf"]["spec_version"] = _opf_store.HOMES2_SPEC_VERSION

    @contextlib.contextmanager
    def active():
        path.write_text(emit.emit_checked(model), encoding="utf-8")
        try:
            with patch.object(_opf_store, "SUPPORTED_HOMES", 2), \
                    patch.object(record, "_opf_store", _Homes2SpecGate()), _homes2_declaration(root):
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

# Inside a killed child: activate homes 2 exactly as _self_test_homes2_active does (the manifest on
# disk already declares the real pair, spec_version 2.0.0 and homes = 2; these are the two remaining
# module patches, SUPPORTED_HOMES and the record spec_version gate of _Homes2SpecGate; both stay
# necessary until activation flips SUPPORTED_HOMES, since homes_generation activates nothing at 1 and
# _load_manifest admits only the shipped SUPPORTED_SPEC_VERSION). The claim itself runs the REAL
# homes-2 seam: the reservation scan, the spec 8.2 derivation, and the run id reservation published
# before the publication. The runtime check reads the homes-2 build's declaration that the enclosing
# _self_test_homes2_active wrote beside the case roots (HOMES2_DECLARATION, the same bytes).
HOMES2_CHILD_FLIP = """
import hashlib
import _opf_declaration
import _opf_store
_opf_store.SUPPORTED_HOMES = 2
class _Homes2SpecGate:
    def __getattr__(self, name):
        if name == "SUPPORTED_SPEC_VERSION":
            return _opf_store.HOMES2_SPEC_VERSION
        return getattr(_opf_store, name)
record._opf_store = _Homes2SpecGate()
_homes2_declared = _opf_declaration.declaration_bytes(
    _opf_declaration.RELEASE_IDENTITY, _opf_store.SUPPORTED_SPEC_VERSION, 2, _opf_store.SUPPORTED_WORKLOG)
_opf_declaration.SHIPPED_DECLARATION_BYTES = _homes2_declared
_opf_declaration.SHIPPED_DECLARATION_SHA256 = hashlib.sha256(_homes2_declared).hexdigest()
opf._installed_declaration = lambda: Path(sys.argv[1]).parent / """ + repr(HOMES2_DECLARATION) + """
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
    """A legacy .aiqt/record/journal found on a homes-2 store is refused by name and never recovered: the
    .aiqt subtree stays byte-unchanged (the T15 leftover lock included) and no typed journal home is
    created."""
    env = fx.env
    root = fx.case("t20-homes2-legacy")
    proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
    assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-800:])
    assert (Path(root) / record.JOURNAL_REL / "lock").exists(), "T20 the legacy journal holds a leftover lock"
    with _self_test_homes2_active(root):
        aiqt_before = subtree(root, ".aiqt")
        result = record_cli(env, root, CREATE)
        refused(result, "legacy record journal")
        assert "it is never recovered: homes 2 has no homes migration (spec 4.2)" in result[2] \
            and "left unread and unchanged" in result[2] and "transports" not in result[2] \
            and "in place" not in result[2], result[2][-800:]
        assert subtree(root, ".aiqt") == aiqt_before, "T20 the .aiqt subtree is byte-unchanged"
        assert not (Path(root) / ".working/journals").exists(), "T20 no typed journal home is created"


def flip_t20():
    """Probe the generation as 1 (the reviewed head's behaviour): the legacy journal is reconciled in
    place and .aiqt changes."""
    return patch.object(record, "_probe_homes", lambda ctx: 1)


def t21_homes2_publish(fx):
    """A direct homes-2 publication (the claim through the live homes-2 seam, over an empty reservation
    scan, its reservation published by _reserve_claim under the held capability the publication then
    runs under, with the same run id, as _run_operation does: the boundary requires it, T82) lands ONE
    capability-bound transaction in the typed journal home under that run id with its terminal
    projection, rewrites exactly the planned operands, and touches nothing under .aiqt."""
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
            plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            run_id = record._record_run_id(os.urandom(16).hex())
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            try:
                record._reserve_claim(ctx, plan, cap, run_id)
                record._publish(ctx, plan, "create", cap=cap, run_id=run_id)
            finally:
                record._opf_oplock.release_operation(cap)
        finally:
            os.close(root_fd)
        assert not (Path(root) / ".aiqt").exists(), "T21 nothing under .aiqt"
        states = journal_states(root, TYPED_JOURNAL)
        names = list(states)
        assert names == [run_id] and re.fullmatch(RECORD_RUN_RE, names[0]), ("T21 one grammar-named txn",
                                                                             names, run_id)
        assert states[names[0]] == "complete", states
        projection = Path(root) / record._opf_store.txn_record("record", names[0])
        assert projection.is_file(), "T21 the terminal projection is published"
        for op in plan.operands:
            assert read(root, op.rel) == op.new_raw, ("T21 the operand is rewritten", op.rel)
        assert not (Path(root) / LEASE).exists(), "T21 the capability lease is released"


def flip_t21():
    return patch.object(record, "_record_journal_rel", lambda homes: record.JOURNAL_REL)


def t22_homes2_claim_reserves(fx):
    """On a homes-2 store the verb claims through the live reservation seam: create records end to
    end, publishing ONE irrevocable id reservation at the allocation record of the COMPLETE
    transaction's own run id, byte-exact (reservation_payload) with claims equal to exactly the
    counters advance; no legacy journal home is created. Fail-closed, in seven cases: a non-regular
    entry (a directory), an unreadable one (a regular file holding a canonical reservation whose open
    fails with an injected permission error), a malformed one, a claims value of the wrong type (a
    string, and separately a boolean), a conflicting one (content not the canonical reservation its
    name declares), and a misnamed entry each refuse exit 2 with every byte untouched, never a silent
    fallback to the homes-1 counters path. The refusal cases run before the end-to-end create, so a flip
    of one case's branch reaches that case without an earlier success assertion ending the run first."""
    env = fx.env
    base = fx.case("t22-homes2-base")
    with _self_test_homes2_active(base):
        damaged_run = "record-20260927T000009Z-0000000000000009"
        damaged = damaged_run + ".toml"
        rel = "{}/{}".format(record._ALLOCATIONS_REL, damaged)
        canonical = record._opf_journal.reservation_payload("record", damaged_run, "op", {"BI": 1})
        for label, plant, needle in (
                ("non-regular", lambda d: d.mkdir(), "cannot read the id reservation {} (contained path {!r} "
                 "is not a regular file)".format(rel, rel)),
                ("unreadable", lambda d: d.write_bytes(canonical), "cannot read the id reservation {} ([Errno {}] "
                 "{}".format(rel, errno.EACCES, UNREADABLE_RESERVATION)),
                ("malformed", lambda d: d.write_bytes(b"not toml ["), "is malformed"),
                ("wrong-type-string", lambda d: d.write_bytes(_claims_retyped(canonical, b'"1"')),
                 "the id reservation {} is malformed (id reservation claims must be".format(rel)),
                ("wrong-type-boolean", lambda d: d.write_bytes(_claims_retyped(canonical, b"true")),
                 "the id reservation {} is malformed (id reservation claims must be".format(rel)),
                ("conflicting", lambda d: d.write_bytes(record._opf_journal.reservation_payload(
                    "record", "record-20260927T000008Z-0000000000000008", "op", {"BI": 1})),
                 "conflicts with the exact canonical reservation"),
                ("misnamed", lambda d: d.write_bytes(b"x"), "is not an id reservation")):
            sub = fx.case("t22-homes2-" + label, base)
            target = Path(sub) / record._ALLOCATIONS_REL / (
                "junk.txt" if label == "misnamed" else damaged)
            target.parent.mkdir(parents=True)
            plant(target)
            with _unreadable_entry(damaged) if label == "unreadable" else contextlib.nullcontext():
                refused_untouched(env, sub, CREATE, needle)
        root = fx.case("t22-homes2-reserves", base)
        pre_counters = model(root, COUNTERS)["counters"]
        result = record_cli(env, root, CREATE)
        recorded(result)
        assert not (Path(root) / record.JOURNAL_REL).exists(), "T22 no legacy journal is created"
        states = journal_states(root, TYPED_JOURNAL)
        assert list(states.values()) == ["complete"], ("T22 one COMPLETE transaction", states)
        (run_id,) = states
        reservation = Path(root) / record._opf_store.allocation_record("record", run_id)
        assert reservation.is_file(), "T22 the reservation is published under the run id"
        payload = tomllib.loads(reservation.read_bytes().decode("utf-8"))
        post_counters = model(root, COUNTERS)["counters"]
        advance = dict((ns, n) for ns, n in post_counters.items() if pre_counters.get(ns) != n)
        assert payload["kind"] == "record" and payload["run_id"] == run_id, payload
        assert payload["claims"] == advance == {"BI": pre_counters["BI"] + 1,
                                               "WL": pre_counters["WL"] + 1}, (payload, advance)
        assert reservation.read_bytes() == record._opf_journal.reservation_payload(
            "record", run_id, payload["operation_id"], payload["claims"]), "T22 canonical payload"


UNREADABLE_RESERVATION = "synthetic unreadable id reservation"


@contextlib.contextmanager
def _unreadable_entry(name):
    """os.open raising EACCES for a reservation entry `name` opened beneath a directory descriptor, every
    other open unchanged: the entry is a regular file, unreadable only through the injected error. The
    wrapper forwards dir_fd, so it is listed in os.supports_dir_fd while it stands, as the containment
    probe requires of os.open."""
    real = os.open

    def opening(path, flags, *args, **kwargs):
        if path == name and kwargs.get("dir_fd") is not None:
            raise PermissionError(errno.EACCES, UNREADABLE_RESERVATION, name)
        return real(path, flags, *args, **kwargs)
    with patch.object(os, "open", opening), \
            patch.object(os, "supports_dir_fd", set(os.supports_dir_fd) | {opening}):
        yield


def _claims_retyped(payload, literal):
    """`payload`, a canonical reservation claiming BI = 1, with that claims value re-spelled as the TOML
    `literal` (a value of the wrong type)."""
    retyped = payload.replace(b"\nBI = 1\n", b"\nBI = " + literal + b"\n")
    if retyped == payload:
        raise Harness("the canonical reservation payload carries no BI = 1 claim to retype")
    return retyped


def flip_t22_refuse():
    """The reviewed head seam: a homes-2 claim raises instead of deriving and reserving."""
    seam = record.claim_ids

    def refusing(homes, high, demand, known_complete, reserved=None):
        if homes != 1:
            raise record.RecordError("homes-{} id allocation is not active in this build; refusing "
                                     "(the reviewed head seam)".format(homes))
        return seam(homes, high, demand, known_complete)
    return patch.object(record, "claim_ids", refusing)


def flip_t22_noreserve():
    """A publication that skips the run id reservation: ids are issued with nothing reserved."""
    return patch.object(record, "_reserve_claim", lambda ctx, plan, cap, run_id: None)


def flip_t22_validator():
    """Restore the retired validate_manifest patch as it was: validate the declaration with homes
    stripped, then copy the declared homes back into the validated base. The real 2.0.0 homes-2
    manifest then reads as above the supported spec_version and the run refuses at the manifest, never
    reaching the claim seam."""
    real = _opf_store.validate_manifest

    def homes_stripped(data, *args, **kwargs):
        opf = data.get("opf") if isinstance(data, dict) else None
        if not (isinstance(opf, dict) and "homes" in opf):
            return real(data, *args, **kwargs)
        result = real(dict(data, opf=dict((k, v) for k, v in opf.items() if k != "homes")), *args, **kwargs)
        if isinstance(result.base, dict):
            result.base = dict(result.base, homes=opf["homes"])
        return result
    return patch.object(_opf_store, "validate_manifest", homes_stripped)


# One flip per reservation-entry branch the unreadable and wrong-type cases of T22 and T81 test, each
# disabling that branch alone. The reader is the reservation seam's own (_opf_journal), which both the
# planner's scan and the seam's derivation under the held capability call, so a flip there reaches both.

@contextlib.contextmanager
def flip_unreadable_reservation_skipped():
    """The reservation reader's read-failure handler turned into a skip for an OS-level error: an entry
    whose open fails (an OSError) reads as holding no reservation. A read failure the journal layer
    reports (a JournalError, such as a non-regular entry) still refuses."""
    real = record._opf_journal._read_reservation

    def skipping(afd, kind, entry):
        try:
            return real(afd, kind, entry)
        except journal.JournalError as exc:
            if str(exc).startswith("cannot read the id reservation ") and isinstance(exc.__context__, OSError):
                return {}
            raise
    with patch.object(record._opf_journal, "_read_reservation", skipping):
        yield


@contextlib.contextmanager
def flip_string_claims_admitted():
    """The reservation claims check without its type test for a string: a claims value that is a decimal
    string is checked as the integer it spells, so the reader no longer refuses it as malformed, and the
    scan's high-water comparison of that string with an integer crashes (the injection its declared
    backstop is bound to, recorded where it leaves _opf_journal._kind_reservation_high; T22 and T81
    both reach it)."""
    real = record._opf_journal._check_claims

    def lenient(kind, claims):
        if isinstance(claims, dict):
            claims = dict((ns, int(n) if isinstance(n, str) and n.isdecimal() else n)
                          for ns, n in claims.items())
        return real(kind, claims)
    with patch.object(record._opf_journal, "_check_claims", lenient), \
            _crash_point("t22-t81-string-claims", record._opf_journal, "_kind_reservation_high",
                         TypeError("'>' not supported between instances of 'str' and 'int'")):
        yield


@contextlib.contextmanager
def flip_boolean_claims_admitted():
    """The reservation claims check reading a boolean as the integer it subclasses (isinstance in place of
    the exact type test), so the reader no longer refuses a boolean claims value as malformed."""
    real = record._opf_journal._check_claims

    def lenient(kind, claims):
        if isinstance(claims, dict):
            claims = dict((ns, int(n) if type(n) is bool else n) for ns, n in claims.items())
        return real(kind, claims)
    with patch.object(record._opf_journal, "_check_claims", lenient):
        yield


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
    assert "OPF MUST NOT read OPF artefacts from former `.aiqt/` locations" in section, \
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
    nothing, so the run reports no reconciliation and continues, here to a plain homes-2 run that
    records, its id reservation published under its transaction's run id, and the capability released.
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
        recorded(result)
        now = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        assert now != pre, "T35 the run records"
        states = journal_states(root, TYPED_JOURNAL)
        assert list(states.values()) == ["complete"], ("T35 one COMPLETE transaction", states)
        (t35_run,) = states
        assert (Path(root) / record._opf_store.allocation_record("record", t35_run)).is_file(), \
            "T35 the run reserves before publishing"
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
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root)], capture_output=True,
                          text=True, timeout=180, env=dict(env.vars))
    _note_backstops(proc.stderr)
    _oop_forward(proc.stderr)
    return proc.returncode


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
        assert "was reconciled" not in result[2] and "were reclaimed" not in result[2], result[2][-800:]
        recorded(result)


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
    """A direct homes-2 publication (T21's setup, its reservation published under a capability released
    before the publication takes its own) whose own acquisition removes a lease staging leftover
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
            plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            run_id = record._record_run_id(os.urandom(16).hex())
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            try:
                record._reserve_claim(ctx, plan, cap, run_id)
                lease_bytes = (Path(root) / LEASE).read_bytes()
            finally:
                record._opf_oplock.release_operation(cap)
            (Path(root) / LEASE).write_bytes(lease_bytes)
            staging = _opf_oplock._staging_name(opf_check.LEASE_NAME)
            (Path(root) / MACH / staging).write_bytes(b"a torn lease publication")
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            try:
                record._publish(ctx, plan, "create", run_id=run_id)
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
    the next run reconciles nothing (the publication's id reservation, published first as the boundary
    requires, stands, so that run derives above it and records, exit 0, as T79).
    The reviewed head promised the next run reconciles the typed journal."""
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
            plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            run_id = record._record_run_id(os.urandom(16).hex())
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            try:
                record._reserve_claim(ctx, plan, cap, run_id)
            finally:
                record._opf_oplock.release_operation(cap)
            reserved_bi = ctx.claims["BI"]
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            try:
                with patch.object(journal, "_MAX_JOURNAL_READ_BYTES", 1):
                    record._publish(ctx, plan, "create", run_id=run_id)
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
        rc, out, err = record_cli(env, root, CREATE)
        assert "was reconciled" not in err and "were reclaimed" not in err, \
            ("T52 the next run reconciles nothing", err[-800:])
        # The refused publication's reservation stands (T79's orphan shape): the next run derives above
        # it and records, the final doctor treating the reserved numbers as issued (no commit held them).
        assert RECORDED_EVENT in out and rc == 0, \
            ("T52 the next run records above the standing reservation", rc, err[-1600:])
        rows = [r["id"] for r in model(root, BI_INDEX)["record"]]
        assert "BI-{}".format(reserved_bi + 1) in rows and "BI-{}".format(reserved_bi) not in rows, \
            ("T52 the reserved id is never issued", rows)


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
    the re-run then records with no removal left to name. The residual
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
        assert "staging leftover" not in result[2], ("T58 the re-run proceeds with no removal to name",
                                                     result[2][-800:])
        recorded(result)


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
        raise _injected("t62-emit", BrokenPipeError(32, "synthetic output failure after the lease release"))
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
            raise _injected("t64-stderr", OSError(5, T64_STDERR))
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
        raise _injected("t64-emit", BrokenPipeError(32, "synthetic emission failure"))
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
                raise _injected("t68-mark-copy", MemoryError("synthetic allocation failure after the "
                                                             "acquisition"))
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
        raise _injected("t69-release", RuntimeError("fd release witness"))
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
            raise _injected("t69-token", MemoryError("synthetic token allocation failure"))
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


def flip_t69_release():
    """flip_t69 for T69-release-failure-closes-fd, a flip of its own so that it expects the one
    injection that test raises, the release failure (_INTENDED_BACKSTOPS)."""
    return flip_t69()


def flip_t69_token():
    """flip_t69 for T69-token-failure-closes-fd, a flip of its own so that it expects the one injection
    that test raises, the token allocation failure (_INTENDED_BACKSTOPS)."""
    return flip_t69()


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

# --- T74: recovery applies the never-seize and dead-run release clauses to a leftover lease (spec 5.7) ---

T74_UNSCOPED = "Nothing was written;"
T74_SCOPED = "Nothing was written to the journal or to any operand; both are left exactly as found"
T74_HELD = "runs only under the single-writer lease"


def _t74_interrupted(fx, name):
    """A store whose create was killed mid-apply, the dead run's lease LEFT in place (unlike
    _interrupted, which models the operator's own release)."""
    root = fx.case(name)
    proc = child(fx.env, root, CREATE, kill="after-apply-0")
    assert proc.returncode == 137, ("T74 the child is killed", proc.returncode, proc.stderr[-800:])
    assert (Path(root) / LEASE).exists(), "T74 the killed run leaves its lease"
    return root


def t74_lease_bytes(pid, host=None, verb="record", lease_schema=1):
    """A canonical-shape lease payload naming holder opf-<verb>:<host>:<pid> (acquire_lease's field
    order), for the refusal legs that plant a crafted leftover."""
    host = socket.gethostname() if host is None else host
    return ('acquired_at = "2026-09-27T00:00:00Z"\nholder = "opf-{}:{}:{}"\noperation = "{}"\n'
            'schema = {}\n'.format(verb, host, pid, verb, lease_schema)).encode("utf-8")


def _t74_dead_pid(root):
    """The killed fixture child's own pid, read from the leftover lease it left: a pid this host has
    positively seen exit, so the liveness probe reads it confirmed-dead."""
    data = tomllib.loads(read(root, LEASE).decode("utf-8"))
    pid = int(data["holder"].rsplit(":", 1)[1])
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid
    raise Harness("T74 the killed child's pid is still live")


def _t74_refused_leg(fx, name, lease_bytes, needle=T74_HELD):
    """Plant `lease_bytes` over the interrupted fixture's leftover lease and assert recovery refuses
    before any write, every byte untouched (never seized)."""
    root = _t74_interrupted(fx, name)
    (Path(root) / LEASE).write_bytes(lease_bytes)
    before = snapshot(root)
    refused(record_cli(fx.env, root, CREATE), needle)
    assert snapshot(root) == before, ("T74 refuses with every byte untouched", name)
    return root


def t74_never_seize_dead_run_release(fx):
    """Recovery applies the spec 5.7 never-seize and dead-run release clauses, each never-seize guard
    carrying its own discriminating leg: a leftover lease whose holder is confirmed dead on this host is
    released through the reconciliation itself, in the same run, with the outcome naming the released
    lease and its holder; a LIVE local holder refuses before any recovery write with every byte intact
    (the never-seize core: never seized, however stale the journal behind it); a confirmed-dead holder on
    ANOTHER host
    refuses (liveness is observable only here, and this leg's dead pid turns red if the host binding is
    dropped); a dead holder under a non-closed payload schema refuses (full validation precedes any
    holder comparison); a MALFORMED OVERSIZED payload whose first 64 KiB parse as exactly the dead
    holder's valid lease refuses (the payload is read through EOF, never judged by a valid-looking
    prefix); an EPERM liveness probe reads possibly-live and refuses (a live foreign-uid holder is never
    seized); a dead holder of ANOTHER verb's lease refuses (this verb's reconciliation reconciles nothing
    of that verb's writes); and a refusal recovery raises after a release (an intervening edit) still
    carries the released-lease report beside the scoped no-journal-no-operand sentence, never the
    unscoped nothing-written reading."""
    env = fx.env
    root = _t74_interrupted(fx, "t74-dead-holder")
    dead_pid = _t74_dead_pid(root)
    result = record_cli(env, root, CREATE)
    refused(result, "was reconciled")
    err = result[2]
    assert "leftover single-writer lease" in err and "confirmed-dead holder" in err, (
        "T74 the outcome names the released lease and its holder", err[-1200:])
    assert not (Path(root) / LEASE).exists(), "T74 the leftover lease and recovery's own are released"
    assert not any(s == "open" for s in journal_states(root).values()), "T74 the journal is reconciled"
    # The live local holder: the never-seize core. The crafted lease names THIS live process.
    _t74_refused_leg(fx, "t74-live-holder", t74_lease_bytes(os.getpid()))
    # A confirmed-dead holder on another host still reads possibly-live here (the host binding).
    _t74_refused_leg(fx, "t74-cross-host", t74_lease_bytes(dead_pid, host="no-such-host.invalid"))
    # A dead holder under a non-closed payload schema (full validation precedes the holder comparison).
    _t74_refused_leg(fx, "t74-bad-schema", t74_lease_bytes(dead_pid, lease_schema=999))
    # A malformed oversized payload whose first 64 KiB are exactly the dead holder's valid lease.
    valid = t74_lease_bytes(dead_pid)
    oversized = valid + b"#" * (65536 - len(valid) - 1) + b"\n" + b"schema = 1\n"
    assert len(oversized) > 65536 and tomllib.loads(oversized[:65536].decode("utf-8")), \
        "T74 the oversized fixture's 64 KiB prefix parses clean"
    _t74_refused_leg(fx, "t74-oversized-prefix", oversized)
    # An EPERM liveness probe reads possibly-live, never dead (a live foreign-uid holder).
    real_kill = os.kill

    def eperm_kill(pid, sig):
        if pid == dead_pid and sig == 0:
            raise PermissionError(1, "Operation not permitted")
        return real_kill(pid, sig)
    root = _t74_interrupted(fx, "t74-eperm-holder")
    (Path(root) / LEASE).write_bytes(t74_lease_bytes(dead_pid))
    before = snapshot(root)
    with patch.object(os, "kill", eperm_kill):
        refused(record_cli(env, root, CREATE), T74_HELD)
    assert snapshot(root) == before, "T74 an EPERM probe refuses with every byte untouched"
    # Another verb's dead holder: record recovery reconciles nothing of an upgrade's writes.
    _t74_refused_leg(fx, "t74-foreign-verb", t74_lease_bytes(dead_pid, verb="upgrade"))
    root = _t74_interrupted(fx, "t74-released-then-refused")
    original = read(root, BI_INDEX)
    edited = original + b"# an edit after the interruption\n"
    (Path(root) / BI_INDEX).write_bytes(edited)
    result = record_cli(env, root, CREATE)
    refused(result, "an intervening edit")
    err = result[2]
    assert "Before that refusal," in err and "leftover single-writer lease" in err, (
        "T74 the refusal carries the released-lease report", err[-1600:])
    assert T74_SCOPED in err and T74_UNSCOPED not in err, (
        "T74 the no-write sentence is scoped to the journal and operands", err[-1600:])
    assert not (Path(root) / LEASE).exists(), "T74 recovery's own lease is released on the refusal path"
    assert read(root, BI_INDEX) == edited, "T74 the intervening edit is never overwritten"


def flip_t74_claim():
    """The pre-fix recovery claim: the plain atomic acquisition, under which ANY present lease refuses
    (the pre-qualification reading of spec 8.8 item 1, which now grants the dead-run release, grounded in
    the section 5.7 dead-run release clause) and a dead run's leftover waits for the operator."""
    return patch.object(guard, "acquire_lease_for_recovery",
                        lambda root_fd, machine_rel, verb: (guard.acquire_lease(root_fd, machine_rel, verb),
                                                            None))


def flip_t74_unscoped():
    """Refuse the intervening edit with the reviewed head's unscoped nothing-written sentence."""
    fixed = record.RecordError

    class Unscoped(fixed):
        def __init__(self, *args):
            super().__init__(*(a.replace(T74_SCOPED, "Nothing was written; the journal and every operand "
                                         "are left exactly as found") if isinstance(a, str) else a
                               for a in args))
    return patch.object(record, "RecordError", Unscoped)


def flip_t74_live():
    """The liveness probe dropped: every examined holder reads confirmed-dead, live ones included."""
    return patch.object(guard, "_lease_holder_confirmed_dead", lambda pid: True)


def flip_t74_host():
    """The host binding dropped: a cross-host holder's recorded pid is probed as if it were local."""
    return patch.object(guard, "_holder_names_this_host", lambda host: True)


def flip_t74_schema():
    """The closed-payload validation dropped: any parseable payload carrying holder and operation
    strings qualifies, whatever its schema or extra keys."""
    def loose(raw):
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        ok = isinstance(data, dict) and all(type(data.get(k)) is str for k in ("holder", "operation"))
        return data if ok else None
    return patch.object(guard, "_closed_lease_model", loose)


def flip_t74_prefix():
    """The pre-fix single bounded lease read: an oversized payload is judged by its first 64 KiB. It takes
    the cause list its callers now pass and records no cause (the pre-fix read had none)."""
    def bounded(pfd, name, why=None):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            return os.read(fd, 65536)
        except OSError:
            return None
        finally:
            journal._close_fd_quietly(fd)
    return patch.object(guard, "read_lease_payload", bounded)


def flip_t74_eperm():
    """EPERM read as dead: a live holder under another uid would be seized."""
    def eperm_is_dead(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        except Exception:  # noqa: BLE001  anything else stays possibly-live, as the fixed code reads it
            return False
        return False
    return patch.object(guard, "_lease_holder_confirmed_dead", eperm_is_dead)


def flip_t74_verb():
    """The verb binding dropped: a dead holder of ANY opf verb's lease qualifies for this verb's
    reconciliation."""
    original = guard._examined_leftover_holder

    def unbound(raw, verb):
        found = original(raw, verb)
        if found is not None:
            return found
        try:
            op = tomllib.loads(raw.decode("utf-8")).get("operation")
        except (UnicodeDecodeError, ValueError, AttributeError):
            return None
        return original(raw, op) if isinstance(op, str) and op and op != verb else None
    return patch.object(guard, "_examined_leftover_holder", unbound)


# --- T75: two concurrent recoveries never both hold the lease (spec 5.7) ---------------------------------

# The child-side schedule gate: pause at the leftover-lease unlink (after the ownership compare has
# passed) and at recovery entry (the lease held), each announced to and released by the parent through
# marker files. Only the schedule is forced; no decision is changed.
T75_GATE = """
import os as _t75_os, time as _t75_time
from pathlib import Path as _t75_path
_T75_SYNC = _t75_path({sync!r})
_T75_TAG = {tag!r}
def _t75_wait(name):
    deadline = _t75_time.monotonic() + 120
    while not (_T75_SYNC / name).exists():
        if _t75_time.monotonic() > deadline:
            raise SystemExit(99)
        _t75_time.sleep(0.02)
_t75_armed = [True]
_t75_unlink = _t75_os.unlink
def _t75_gated_unlink(path, *args, **kwargs):
    if _t75_armed and _t75_path(path).name == {lease_name!r} and kwargs.get("dir_fd") is not None:
        del _t75_armed[:]
        (_T75_SYNC / (_T75_TAG + "-at-unlink")).write_bytes(b"")
        _t75_wait(_T75_TAG + "-go-unlink")
    return _t75_unlink(path, *args, **kwargs)
_t75_os.unlink = _t75_gated_unlink
_t75_os.supports_dir_fd.add(_t75_gated_unlink)
_t75_recover = record._recover_journal
def _t75_gated_recover(ctx, jr_fd, journal_root):
    (_T75_SYNC / (_T75_TAG + "-in-recovery")).write_bytes(b"")
    _t75_wait(_T75_TAG + "-go-recovery")
    return _t75_recover(ctx, jr_fd, journal_root)
record._recover_journal = _t75_gated_recover
{neutralize}
"""

# The T75 flip, applied inside both children: the examination serialization made a no-op, restoring the
# reviewed head's unserialized release. _t75_flip holds the prelude the children run with.
_t75_flip = [""]
FLIP_T75 = """
import _opf_write_guard as _t75_guard
_t75_guard._exclusive_examination = lambda pfd, lease_rel, verb: None
"""

T75_GO_MARKERS = ("a-go-unlink", "b-go-unlink", "a-go-recovery", "b-go-recovery")


def _t75_start(env, root, sync, tag):
    """A real `opf record` child over the interrupted store, instrumented with the T75 schedule gate."""
    flip = T75_GATE.format(sync=str(sync), tag=tag, lease_name=opf_check.LEASE_NAME,
                           neutralize=_t75_flip[0])
    script = _CHILD.format(tools=str(TOOLS), flip=flip)
    return subprocess.Popen([sys.executable, "-I", "-B", "-c", script, str(root)] + list(CREATE),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=dict(env.vars))


def _t75_wait_for(sync, names, proc):
    """The first marker of `names` to appear, or "exited" when the child finishes first."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for name in names:
            if (sync / name).exists():
                return name
        if proc.poll() is not None:
            return "exited"
        time.sleep(0.02)
    raise Harness("T75 timed out waiting for " + "/".join(names))


def _t75_collect(proc, collected, unwinding=False):
    """proc's (stdout, stderr), read once and kept in `collected`: a child already collected is not read
    again, and unwinding (T75's finally) kills a child still running before reading it. The stderr is
    noted and forwarded (_note_backstops, _oop_forward) as it is read, so each child's backstop lines
    reach the discriminating parent exactly once on every path T75 takes, an assertion or harness fault
    before its verdict reads included."""
    for seen, output in collected:
        if seen is proc:
            return output
    if unwinding and proc.poll() is None:
        proc.kill()
    output = proc.communicate(timeout=60 if unwinding else 180)
    collected.append((proc, output))
    _note_backstops(output[1])
    _oop_forward(output[1])
    return output


def t75_concurrent_recovery(fx):
    """At most ONE of two concurrent REAL recoveries ever examines, releases, and re-claims a dead run's
    leftover lease (spec 5.7 never-seize over the recovery path itself). Recovery A is paused between its
    passed ownership compare and its unlink, the examination lock held; recovery B, started there, must
    refuse at that lock rather than read the same dead-holder bytes. Under an unserialized release this
    exact schedule lets B pass the same compare, A release and re-claim, and B's deferred unlink then
    seize A's freshly created LIVE lease, both runs entering journal recovery as writers; the test drives
    that schedule with real `opf record` children whose only instrumentation forces the interleaving (a
    pause at the lease unlink and at recovery entry), changing no decision, and turns red whenever both
    children reach recovery together or the loser writes anything."""
    env = fx.env
    root = _t74_interrupted(fx, "t75-concurrent")
    sync = Path(str(root) + "-sync")
    sync.mkdir()
    procs, collected = [], []
    try:
        a = _t75_start(env, root, sync, "a")
        procs.append(a)
        a_state = _t75_wait_for(sync, ["a-at-unlink"], a)
        assert a_state == "a-at-unlink", ("T75 recovery A reaches the leftover unlink", a_state)
        b = _t75_start(env, root, sync, "b")
        procs.append(b)
        b_state = _t75_wait_for(sync, ["b-at-unlink"], b)
        both_in_recovery = False
        if b_state == "b-at-unlink":
            # B passed the ownership compare over the same dead-holder bytes while A still held them
            # examined: the unserialized race. Drive it to its double-writer outcome, then fail below.
            (sync / "a-go-unlink").write_bytes(b"")
            _t75_wait_for(sync, ["a-in-recovery"], a)
            (sync / "b-go-unlink").write_bytes(b"")
            both_in_recovery = _t75_wait_for(sync, ["b-in-recovery"], b) == "b-in-recovery"
        # Release every gate so both children always exit before any verdict is read.
        for name in T75_GO_MARKERS:
            (sync / name).write_bytes(b"")
        out_a, err_a = _t75_collect(a, collected)
        out_b, err_b = _t75_collect(b, collected)
        assert not both_in_recovery, (
            "T75 two concurrent recoveries both entered journal recovery (the slower examination seized "
            "the faster recoverer's live lease)", err_a[-800:], err_b[-800:])
        assert a.returncode == 2 and "was reconciled" in err_a, (
            "T75 the first recoverer reconciles", a.returncode, err_a[-1200:])
        assert b.returncode == 2 and "concurrently examining" in err_b, (
            "T75 the second recovery refuses at the examination lock", b.returncode, err_b[-1200:])
        assert "was reconciled" not in err_b and RECORDED_EVENT not in out_b, (
            "T75 the second recovery writes nothing", err_b[-1200:])
        assert not (Path(root) / LEASE).exists(), "T75 the winning recovery released its own lease"
        assert not any(st == "open" for st in journal_states(root).values()), "T75 the journal is reconciled"
    finally:
        for proc in procs:
            _t75_collect(proc, collected, unwinding=True)


# --- T76: an examination lock failure is diagnosed as what it is (spec section 17) -----------------------

T76_HELD_LOCK = "concurrently examining"
T76_LOCK_FAILURE = "could not be taken"


def _t76_flock_failing(code):
    """fcntl.flock raising OSError(code) for the examination lock alone (an exclusive non-blocking lock
    on a DIRECTORY descriptor); every other flock (the journal and capability locks, on regular files)
    runs unchanged."""
    import fcntl
    real = fcntl.flock

    def failing(fd, operation):
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(code, os.strerror(code))
        return real(fd, operation)
    return patch.object(fcntl, "flock", failing)


def t76_lock_failure_diagnosed(fx):
    """The examination lock's failure is diagnosed as what it is: a HELD lock (EWOULDBLOCK/EAGAIN)
    refuses naming a concurrent reconciliation, and a lock the store's filesystem does not support or
    refuses (ENOLCK, ENOTSUP, EINVAL, EBADF) refuses naming the lock failure, never a concurrent
    reconciliation that need not exist; each refuses before any recovery write with every byte untouched."""
    legs = ((errno.EWOULDBLOCK, T76_HELD_LOCK, T76_LOCK_FAILURE),
            (errno.ENOLCK, T76_LOCK_FAILURE, T76_HELD_LOCK),
            (errno.ENOTSUP, T76_LOCK_FAILURE, T76_HELD_LOCK),
            (errno.EINVAL, T76_LOCK_FAILURE, T76_HELD_LOCK),
            (errno.EBADF, T76_LOCK_FAILURE, T76_HELD_LOCK))
    for code, needle, absent in legs:
        root = _t74_interrupted(fx, "t76-flock-" + errno.errorcode[code].lower())
        before = snapshot(root)
        with _t76_flock_failing(code):
            result = record_cli(fx.env, root, CREATE)
        refused(result, needle)
        assert absent not in result[2], ("T76 the refusal names only its own cause", code, result[2][-1200:])
        assert snapshot(root) == before, ("T76 a lock failure refuses with every byte untouched", code)


def flip_t76():
    """The round-2 broad catch: every flock failure reads as a concurrent reconciliation."""
    def broad(pfd, lease_rel, verb):
        import fcntl
        try:
            fcntl.flock(pfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise guard.WriteGuardError(
                "another reconciliation is concurrently examining the {} lease {} ({}); this run refuses "
                "rather than race it".format(verb, lease_rel, exc)) from exc
    return patch.object(guard, "_exclusive_examination", broad)


# --- T77: every failure after the release carries the released-lease report (spec 5.7) -------------------

T77_REPORT = "leftover single-writer lease"


def _t77_released(root):
    """True once the leftover lease has been released (unlinked): the retried claim's window."""
    return not os.path.lexists(os.path.join(str(root), LEASE))


@contextlib.contextmanager
def _t77_claim_failing(root, make_exc):
    """os.open raising make_exc() for the RETRIED exclusive lease create alone (the one after the
    leftover's release; the first create meets the leftover and refuses EEXIST, unchanged). The stand-in
    joins every capability set holding the real call (os.supports_dir_fd and its kin), so the store's
    containment probe still finds it (journal._st_supports)."""
    real = os.open
    armed = [True]

    def failing(path, flags, *args, **kwargs):
        if (armed and flags & os.O_EXCL and os.path.basename(str(path)) == Path(LEASE).name
                and kwargs.get("dir_fd") is not None and _t77_released(root)):
            del armed[:]
            raise make_exc()
        return real(path, flags, *args, **kwargs)
    tables = [table for table in journal._st_supports(os) if real in table]
    for table in tables:
        table.add(failing)
    try:
        with patch.object(os, "open", failing):
            yield
    finally:
        for table in tables:
            table.discard(failing)


def _t77_reopen_failing(root):
    """journal._open_parent raising JournalError for the retried claim's reopen of the lease directory
    alone (the first reopen of the lease's parent after the leftover's release)."""
    real = journal._open_parent
    armed = [True]

    def failing(root_fd, relpath):
        if armed and relpath == LEASE and _t77_released(root):
            del armed[:]
            raise journal.JournalError("T77 injected: the lease directory cannot be reopened")
        return real(root_fd, relpath)
    return patch.object(journal, "_open_parent", failing)


def t77_report_every_failure(fx):
    """Every failure AFTER the leftover lease's release carries the released-lease report, whatever its
    type: an EMFILE on the retried O_EXCL claim and a JournalError reopening the lease directory each
    refuse with the report appended (the lease left released, the journal left for the next run), and an
    interrupt propagates as itself with the report attached as a note."""
    for name, injected in (("t77-emfile", lambda root: _t77_claim_failing(root, lambda: _injected(
                                "t77-emfile", OSError(errno.EMFILE, "T77 injected: EMFILE on the retried "
                                                                    "claim")))),
                           ("t77-journal-error", _t77_reopen_failing)):
        root = _t74_interrupted(fx, name)
        with injected(root):
            result = record_cli(fx.env, root, CREATE)
        refused(result, "Before that claim,")
        assert T77_REPORT in result[2], ("T77 the refusal carries the released-lease report", name,
                                         result[2][-1600:])
        assert _t77_released(root), ("T77 the leftover lease is released and nothing re-claimed", name)
        assert any(st == "open" for st in journal_states(root).values()), (
            "T77 the journal is left for the next run", name)
    root = _t74_interrupted(fx, "t77-interrupt")
    notes = None
    root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with _t77_claim_failing(root, KeyboardInterrupt):
            try:
                guard.acquire_lease_for_recovery(root_fd, MACH, record.VERB)
            except KeyboardInterrupt as exc:
                notes = list(getattr(exc, "__notes__", ()))
    finally:
        os.close(root_fd)
    assert notes is not None, "T77 the interrupt propagates as itself"
    assert any(T77_REPORT in note for note in notes), (
        "T77 the interrupt carries the report as a note", notes)
    assert _t77_released(root), "T77 the interrupted claim left the leftover released"


def flip_t77():
    """The round-2 retry: only a WriteGuardError from the retried claim carries the release report."""
    def narrow(pfd, root_fd, machine_rel, verb, lease_rel, released):
        os.fsync(pfd)
        try:
            return guard.acquire_lease(root_fd, machine_rel, verb)
        except guard.WriteGuardError as exc:
            raise guard.WriteGuardError("{} Before that claim, {}.".format(exc, released)) from exc
    return patch.object(guard, "_claim_after_release", narrow)


# --- T84: a leftover lease tomllib refuses with RecursionError is malformed, refused as held (spec 5.7) ------

def t84_recursion_lease_refused_as_held(fx):
    """A leftover lease whose payload tomllib refuses with RecursionError (a deeply nested value, and a
    1001-part key under the emulated 3.14.8 key-part cap) is a malformed lease like any other: recovery
    refuses it with the held-lease refusal and its remedy, every byte untouched, never the verb backstop's
    unexpected error."""
    _t74_refused_leg(fx, "t84-deep-lease", _opf_store._ST_DEEP_TOML.encode("utf-8"))
    with _opf_store._StKeyPartCap():
        _t74_refused_leg(fx, "t84-long-key-lease", _opf_store._ST_LONG_KEY_TOML.encode("utf-8"))


@contextlib.contextmanager
def flip_t84():
    """The pre-fix lease parse, catching UnicodeDecodeError and ValueError only: a RecursionError escapes
    _closed_lease_model to the record verb's backstop (the injection its declared backstop is bound to:
    the deeply nested payload's RecursionError, which turns the first leg red)."""
    original = guard._closed_lease_model

    def narrow(raw):
        try:
            tomllib.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            pass
        return original(raw)
    with patch.object(guard, "_closed_lease_model", narrow), \
            _crash_point("t84-recursion", guard, "_closed_lease_model",
                         RecursionError("maximum recursion depth exceeded")):
        yield


# --- T78: homes 2 is the declaration pair, never homes = 2 alone (spec 4.2) ---------------------------------

def t78_homes2_needs_the_pair(fx):
    """With homes 2 activated as the record verb will run once activation admits it (SUPPORTED_HOMES = 2
    alone, without _Homes2SpecGate, so _load_manifest still admits 1.2.0; manifest validation also
    refuses this declaration, but only after reconciliation, so the journal-home probe is the guard
    before any write), a store declaring homes = 2 at the base spec_version (1.2.0, not
    HOMES2_SPEC_VERSION) is not a homes-2 store, and the record verb refuses it at that probe before any
    reconciliation or write. Two stores, each with the declaration committed: a fresh one, where a run
    past the probe publishes into the legacy journal and rewrites the operands, and one holding a
    reconcilable legacy leftover (the T15 journal lock), where a run past the probe reconciles it in
    place. In each, every byte stays untouched (asserted first), the verb chooses no journal home, the
    refusal names the declaration pair, homes_generation grades the store generation 1, and no journal
    home is created. The retired HOMES2_SPEC_VERSION patch made 1.2.0 the homes-2 version, so it read
    this very store as homes 2."""
    env = fx.env
    manifest_rel = MACH + "/" + _opf_store.MANIFEST_NAME
    needle = "homes 2 only with spec_version " + _opf_store.HOMES2_SPEC_VERSION
    for label in ("fresh", "legacy"):
        root = fx.case("t78-base-version-" + label)
        if label == "legacy":
            proc = child(env, root, CREATE, flip=FAILING_LOCK_RELEASE)
            assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (label, proc.returncode,
                                                                            proc.stderr[-800:])
            assert (Path(root) / record.JOURNAL_REL / "lock").exists(), ("T78 a reconcilable legacy leftover",
                                                                        label)
        declared = model(root, manifest_rel)
        declared["opf"]["homes"] = 2
        (Path(root) / manifest_rel).write_text(emit.emit_checked(declared), encoding="utf-8")
        fx.commit_all(root, "declare homes = 2 at the base spec_version")
        assert declared["opf"]["spec_version"] == _opf_store.SUPPORTED_SPEC_VERSION, (
            "T78 the store declares homes = 2 at 1.2.0", label, declared["opf"])
        chosen = []
        journal_rel = record._record_journal_rel

        def choosing(homes, chosen=chosen, journal_rel=journal_rel):
            chosen.append(homes)
            return journal_rel(homes)
        with patch.object(_opf_store, "SUPPORTED_HOMES", 2), _homes2_declaration(root):
            before = snapshot(root)
            with patch.object(record, "_record_journal_rel", choosing):
                result = record_cli(env, root, CREATE)
            assert snapshot(root) == before, ("T78 bytes untouched on refusal", label, result[0], result[2][-800:])
            assert chosen == [], ("T78 the verb chooses no journal home", label, chosen, result[2][-800:])
            refused(result, needle)
            assert _opf_store.homes_generation(declared) == 1, ("T78 homes = 2 at 1.2.0 is graded generation 1",
                                                               label)
        assert not (Path(root) / ".working/journals").exists(), ("T78 no typed journal home is created", label)
        if label == "fresh":
            assert not (Path(root) / record.JOURNAL_REL).exists(), "T78 no legacy journal is created"


def flip_t78():
    """Restore the retired HOMES2_SPEC_VERSION patch: the base spec_version becomes the homes-2 one, so
    the 1.2.0 store declaring homes = 2 reads as generation 2. The record verb's probe then passes and
    chooses the typed journal home; the fresh store then records through the live homes-2 seam into
    the typed journal home, and the legacy one refuses at the legacy-journal refusal, so T78 turns red
    on the written bytes or the journal home the verb chose, before its refusal text and before the
    direct homes_generation assertion."""
    return patch.object(_opf_store, "HOMES2_SPEC_VERSION", _opf_store.SUPPORTED_SPEC_VERSION)


# --- T79, T80: the homes-2 reservation vectors (spec 8.2; homes plan PR-4) -----------------------------

# Inside the killed child: the run dies immediately after its irrevocable id reservation is
# published, before its journaled publication begins.
DIE_AFTER_RESERVE = """
import os
_real_reserve = record._reserve_claim
def _die_after_reserve(ctx, plan, cap, run_id):
    _real_reserve(ctx, plan, cap, run_id)
    os._exit(137)
record._reserve_claim = _die_after_reserve
"""


def t79_kill_after_reserve(fx):
    """A homes-2 run killed after reserving and before publishing leaves its reservation standing
    and every operand at the prestate, and the reserved numbers are never issued again: after the
    confirmed-dead reclaim refusal, the next create derives ABOVE the orphan (its own reservation
    claims the next numbers), publishes them and records, exit 0: the prior commit (HEAD) held no
    record with an orphaned number, so the final doctor treats those numbers as issued and the store
    carrying the orphan is VALID."""
    env = fx.env
    base = fx.case("t79-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t79-homes2-killed", base)
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        counters0 = model(root, COUNTERS)["counters"]
        proc = child(env, root, CREATE, flip=HOMES2_CHILD_FLIP + DIE_AFTER_RESERVE)
        assert proc.returncode == 137, ("T79 the child is killed after reserving", proc.returncode,
                                        proc.stderr[-800:])
        assert RECORDED_EVENT not in proc.stdout, "T79 a killed run reports no id"
        alloc_dir = Path(root) / record._ALLOCATIONS_REL
        names = sorted(os.listdir(alloc_dir))
        assert len(names) == 1, ("T79 the reservation is standing", names)
        first = (alloc_dir / names[0]).read_bytes()
        first_claims = tomllib.loads(first.decode("utf-8"))["claims"]
        assert first_claims == {"BI": counters0["BI"] + 1, "WL": counters0["WL"] + 1}, first_claims
        assert journal_states(root, TYPED_JOURNAL) == {}, "T79 nothing was journaled"
        assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, \
            "T79 the operands end at the prestate"
        refused(record_cli(env, root, CREATE), "were reclaimed before this operation")
        assert (alloc_dir / names[0]).read_bytes() == first, "T79 the reclaim never touches the reservation"
        rc, out, err = record_cli(env, root, CREATE)
        assert rc == 0 and RECORDED_EVENT in out, ("T79 the run above the orphan records, doctor VALID", rc,
                                                   err[-1600:])
        names2 = sorted(os.listdir(alloc_dir))
        assert len(names2) == 2 and names[0] in names2, ("T79 both reservations standing", names2)
        (second_name,) = [n for n in names2 if n != names[0]]
        second_claims = tomllib.loads((alloc_dir / second_name).read_bytes().decode("utf-8"))["claims"]
        assert second_claims == {"BI": first_claims["BI"] + 1,
                                 "WL": first_claims["WL"] + 1}, \
            ("T79 the re-run derives above the orphaned reservation", first_claims, second_claims)
        assert (alloc_dir / names[0]).read_bytes() == first, "T79 the orphan is never rewritten"
        counters2 = model(root, COUNTERS)["counters"]
        assert counters2["BI"] == second_claims["BI"] and counters2["WL"] == second_claims["WL"], counters2
        rows = [r["id"] for r in model(root, BI_INDEX)["record"]]
        reserved_id = "BI-{}".format(first_claims["BI"])
        issued_id = "BI-{}".format(second_claims["BI"])
        assert issued_id in rows and reserved_id not in rows, \
            ("T79 the reserved id is never issued again", rows)


def flip_t79():
    """A derivation that ignores the reservation scan, in the planner and in the reservation seam
    alike (both read _opf_journal.reservation_high): an orphaned reservation gets its numbers issued
    again."""
    return patch.object(record._opf_journal, "reservation_high", lambda root_fd, exclude=None: {})


def flip_t79_doctor():
    """A doctor that reads no reservation: the orphaned numbers grade as missing records, so the run
    above them is held at the post-publication gate, exit 2."""
    return patch.object(opf_check, "_reserved_numbers", lambda root_fd, rep: ({}, True))


def t80_two_runs_never_same_id(fx):
    """Two runs claiming the same kind never receive the same id, whatever their interleaving: two
    contexts planned BEFORE either reserves derive the same numbers; the first reserves through the
    production claim-and-reserve path (_reserve_claim under its own held capability, nothing
    published to the store), and the second, reserving after it, is refused by the held
    revalidation with no reservation file of its own, no transaction and every operand at the
    prestate; re-planned, the second run derives the next numbers, both reservations standing
    disjoint and the first never rewritten."""
    base = fx.case("t80-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t80-homes2-two-runs", base)
        res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
        assert res.status == record._opf_store.RESOLVED, res
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        stamps = ("record-20260927T000101Z-" + "a" * 16,
                  "record-20260927T000102Z-" + "b" * 16)
        reservations = [Path(root) / record._opf_store.allocation_record("record", s) for s in stamps]
        fds = []

        def planned():
            root_fd = record._opf_store._open_dir_nofollow(res.store_root)
            fds.append(root_fd)
            ctx = record.Context(res, str(root), root_fd)
            ctx.journal_rel = record._record_journal_rel(record._probe_homes(ctx))
            record._load_manifest(ctx)
            assert ctx.homes == 2, ("T80 the validated generation", ctx.homes)
            ctx.counters = record._read_operand(root_fd, ctx.rel(opf_check.COUNTERS_NAME))
            return ctx, record._claim(ctx, ["BI", "WL"])

        def reserve(ctx, ids, stamp):
            cap = record._opf_oplock.acquire_operation(str(res.store_root), record.VERB)
            try:
                record._reserve_claim(ctx, record.Plan([], ids), cap, stamp)
            finally:
                record._opf_oplock.release_operation(cap)
        try:
            (ctx1, ids1), (ctx2, ids2) = planned(), planned()
            assert ids1 == ids2 == ["BI-1", "WL-1"], ("T80 both plans derive the same numbers", ids1, ids2)
            reserve(ctx1, ids1, stamps[0])
            first = reservations[0].read_bytes()
            try:
                reserve(ctx2, ids2, stamps[1])
            except record.RecordError as exc:
                message = str(exc)
            else:
                raise AssertionError("T80 the second, stale plan must refuse at the held revalidation")
            assert "changed between planning and the held capability" in message, message
            assert not reservations[1].exists(), "T80 the refused run publishes no reservation"
            assert journal_states(root, TYPED_JOURNAL) == {}, "T80 no transaction is opened"
            assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, \
                "T80 no operand is published"
            ctx3, ids3 = planned()
            assert ids3 == ["BI-2", "WL-2"], ("T80 the re-planned run derives the next numbers", ids3)
            reserve(ctx3, ids3, stamps[1])
        finally:
            for fd in fds:
                os.close(fd)
        assert reservations[0].read_bytes() == first, "T80 the first reservation is never rewritten"
        for reservation, claimed in zip(reservations, ({"BI": 1, "WL": 1}, {"BI": 2, "WL": 2})):
            payload = tomllib.loads(reservation.read_bytes().decode("utf-8"))
            assert payload["claims"] == claimed, ("T80 the reservations stand disjoint", reservation, payload)


def flip_t80_no_recheck():
    """A held reservation step with no stale-plan comparison: the seam is asked for the run's demand
    without the planned claims as `expected`, so a reservation landing after planning is never noticed
    here and the stale run reserves the next numbers, which its plan does not carry. T80 calls this step
    directly, so it sees no refusal; on the full record path the publication boundary's claims
    comparison (_require_reservation) would still refuse the run, leaving that reservation orphaned."""
    def no_recheck(ctx, plan, cap, run_id):
        record._opf_journal.reserve_allocation(cap, "record", run_id,
                                               {ns: ctx.demand.count(ns) for ns in ctx.demand})
    return patch.object(record, "_reserve_claim", no_recheck)


def t94_planned_ids_bound_to_claims(fx):
    """The held reservation step refuses a plan whose ordered ids are not the ones its demand and
    planned claims imply, before anything is reserved: with the claims unchanged, a wrong number, the
    right ids in another order, a missing id and an extra id each refuse with no reservation, no
    transaction and every operand at the prestate; the plan's own ids then reserve exactly its claims."""
    base = fx.case("t94-homes2-base")
    with _self_test_homes2_active(base):
        root = fx.case("t94-homes2-planned-ids", base)
        res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
        assert res.status == record._opf_store.RESOLVED, res
        pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
        stamp = "record-20260927T000201Z-" + "c" * 16
        reservation = Path(root) / record._opf_store.allocation_record("record", stamp)
        root_fd = record._opf_store._open_dir_nofollow(res.store_root)
        try:
            ctx = record.Context(res, str(root), root_fd)
            ctx.journal_rel = record._record_journal_rel(record._probe_homes(ctx))
            record._load_manifest(ctx)
            assert ctx.homes == 2, ("T94 the validated generation", ctx.homes)
            ctx.counters = record._read_operand(root_fd, ctx.rel(opf_check.COUNTERS_NAME))
            ids = record._claim(ctx, ["BI", "WL"])
            assert ids == ["BI-1", "WL-1"] and ctx.claims == {"BI": 1, "WL": 1}, ("T94 the plan", ids, ctx.claims)

            def reserve(planned_ids):
                cap = record._opf_oplock.acquire_operation(str(res.store_root), record.VERB)
                try:
                    record._reserve_claim(ctx, record.Plan([], planned_ids), cap, stamp)
                finally:
                    record._opf_oplock.release_operation(cap)
            for label, wrong in (("foreign-number", ["BI-999", "WL-1"]), ("reordered", ["WL-1", "BI-1"]),
                                 ("missing", ["BI-1"]), ("extra", ["BI-1", "WL-1", "BI-2"])):
                try:
                    reserve(wrong)
                except record.RecordError as exc:
                    message = str(exc)
                else:
                    raise AssertionError(("T94 a plan whose ids its claims do not imply must refuse", label))
                assert "are not the ids its demand and planned claims imply" in message, (label, message)
                assert not reservation.exists(), ("T94 the refused plan publishes no reservation", label)
                assert journal_states(root, TYPED_JOURNAL) == {}, ("T94 no transaction is opened", label)
                assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, \
                    ("T94 no operand is published", label)
            reserve(ids)
        finally:
            os.close(root_fd)
        payload = model(root, record._opf_store.allocation_record("record", stamp))
        assert payload["claims"] == {"BI": 1, "WL": 1}, ("T94 the plan's own ids reserve its claims", payload)


def flip_t94_ids_unchecked():
    """The held reservation step without its planned-id check: only the claims are compared, so a plan
    whose ids its claims do not imply reserves. T94 calls this step directly, so nothing else refuses
    that plan; on the full record path the postcondition's id comparison (_postcondition) would
    still refuse the run before the lease, so the planned-id check is a second guard behind it."""
    return patch.object(record, "_check_planned_ids", lambda ctx, plan: None)


# --- T81-T83: the cross-kind high-water, the publication boundary, the journal-home reader ---------------

T81_IMPORT_RUN = "imp-20260927T000101Z-" + "a" * 16


def t81_cross_kind_reservations(fx):
    """The homes-2 high-water reads EVERY journal kind's reservations (spec 8.2: every reservation's
    [claims] value for the counter key), the homes enumerated from the JOURNAL_KINDS registry: an
    import-kind reservation of {BI: 1, WL: 1} moves the next create to BI-2 and WL-2 (its own
    reservation says so, the import one is never touched), and that run records, exit 0,
    as in T79. Fail-closed in every other kind's home: a malformed
    reservation in each non-record kind's home, and in the import home a non-regular entry (a
    directory), an unreadable one (a regular file holding a canonical import reservation whose open
    fails with an injected permission error), a claims value of the wrong type (a string, and
    separately a boolean), an ingest payload under an import name, and a record-grammar name, each
    refuse exit 2 with every byte untouched. A broken import home itself is T85's. The refusal cases run
    before the cross-kind create, so a flip of one case's branch reaches that case without an earlier
    success assertion ending the run first."""
    env = fx.env
    base = fx.case("t81-homes2-base")
    with _self_test_homes2_active(base):
        stamp = "-20260927T000009Z-" + "9" * 16
        for kind in record._opf_store.JOURNAL_KINDS:
            if kind == "record":
                continue
            sub = fx.case("t81-homes2-malformed-" + kind, base)
            target = Path(sub) / record._opf_store.allocation_record(
                kind, record._opf_store._HOME_RUN_PREFIXES[kind] + stamp)
            target.parent.mkdir(parents=True)
            target.write_bytes(b"not toml [")
            refused_untouched(env, sub, CREATE, "is malformed")
        damaged = "imp" + stamp + ".toml"
        rel = "{}/{}".format(record._allocations_rel("import"), damaged)
        canonical = record._opf_journal.reservation_payload("import", "imp" + stamp, "op", {"BI": 1})
        for label, name, plant, needle in (
                ("non-regular", damaged, lambda d: d.mkdir(), "cannot read the id reservation {} (contained "
                 "path {!r} is not a regular file)".format(rel, rel)),
                ("unreadable", damaged, lambda d: d.write_bytes(canonical), "cannot read the id reservation "
                 "{} ([Errno {}] {}".format(rel, errno.EACCES, UNREADABLE_RESERVATION)),
                ("wrong-type-string", damaged, lambda d: d.write_bytes(_claims_retyped(canonical, b'"1"')),
                 "the id reservation {} is malformed (id reservation claims must be".format(rel)),
                ("wrong-type-boolean", damaged, lambda d: d.write_bytes(_claims_retyped(canonical, b"true")),
                 "the id reservation {} is malformed (id reservation claims must be".format(rel)),
                ("conflicting", damaged, lambda d: d.write_bytes(record._opf_journal.reservation_payload(
                    "ingest", "imp" + stamp, "op", {"BI": 1})),
                 "conflicts with the exact canonical reservation"),
                ("misnamed", "record" + stamp + ".toml", lambda d: d.write_bytes(
                    record._opf_journal.reservation_payload("record", "record" + stamp, "op", {"BI": 1})),
                 "is not an id reservation")):
            sub = fx.case("t81-homes2-import-" + label, base)
            target = Path(sub) / record._allocations_rel("import") / name
            target.parent.mkdir(parents=True)
            plant(target)
            with _unreadable_entry(damaged) if label == "unreadable" else contextlib.nullcontext():
                refused_untouched(env, sub, CREATE, needle)
        root = fx.case("t81-homes2-import-reserved", base)
        cap = record._opf_oplock.acquire_operation(str(root), "import")
        try:
            record._opf_journal.reserve_allocation(cap, "import", T81_IMPORT_RUN, {"BI": 1, "WL": 1})
        finally:
            record._opf_oplock.release_operation(cap)
        imported = Path(root) / record._opf_store.allocation_record("import", T81_IMPORT_RUN)
        planted = imported.read_bytes()
        rc, out, err = record_cli(env, root, CREATE)
        rows = [r["id"] for r in model(root, BI_INDEX)["record"]]
        assert rows == ["BI-2"], ("T81 the import-kind reservation's numbers are never issued", rows, rc,
                                  err[-1200:])
        own = sorted(os.listdir(Path(root) / record._ALLOCATIONS_REL))
        assert len(own) == 1, ("T81 one record reservation", own)
        claims = tomllib.loads((Path(root) / record._ALLOCATIONS_REL / own[0]).read_bytes()
                               .decode("utf-8"))["claims"]
        assert claims == {"BI": 2, "WL": 2}, ("T81 the record reservation claims above the import one", claims)
        assert imported.read_bytes() == planted, "T81 the import reservation is never touched"
        assert RECORDED_EVENT in out and rc == 0, ("T81 the run above the import reservation records, as T79",
                                                  rc, err[-1600:])


def flip_t81():
    """The reviewed head's single hard-coded home: only the record kind's reservations are read, by the
    planner and by the reservation seam alike."""
    def record_home_only(root_fd, exclude=None):
        skip = exclude[1] + ".toml" if exclude is not None and exclude[0] == "record" else None
        return record._opf_journal._kind_reservation_high(root_fd, "record", record._ALLOCATIONS_REL, skip)
    return patch.object(record._opf_journal, "reservation_high", record_home_only)


def flip_t81_doctor():
    """A doctor that reads only the record kind reservation home: the import-kind reservation is
    unread, so its numbers grade as missing records and the run above them is held."""
    real = opf_check._reserved_numbers

    def record_only(root_fd, rep):
        with patch.object(opf_check._opf_store, "JOURNAL_KINDS", ("record",)):
            return real(root_fd, rep)
    return patch.object(opf_check, "_reserved_numbers", record_only)


def t82_publication_requires_reservation(fx):
    """The homes-2 publication boundary requires reservation evidence for the run id it publishes
    under: a direct _publish naming no run id, one whose run id has no reservation (under a held
    capability), and one whose reservation claims other numbers each refuse before any transaction
    opens, with no journal transaction, every operand at the prestate and the capability released."""
    root = fx.case("t82-homes2-boundary")
    with _self_test_homes2_active(root):
        req = record.parse_request(CREATE + ["--root", str(root)])
        res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
        assert res.status == record._opf_store.RESOLVED, res
        root_fd = record._opf_store._open_dir_nofollow(res.store_root)
        try:
            ctx = record.Context(res, str(root), root_fd)
            ctx.journal_rel = record._record_journal_rel(record._probe_homes(ctx))
            assert ctx.journal_rel == TYPED_JOURNAL, ("T82 the probed journal home", ctx.journal_rel)
            record._load_manifest(ctx)
            ctx.counters = record._read_operand(root_fd, ctx.rel(opf_check.COUNTERS_NAME))
            ctx.version = record._read_operand(root_fd, ctx.rel(opf_check.VERSION_NAME)).model
            ctx.worklog = record._read_operand(root_fd, ctx.rel(opf_check.WORKLOG_NAME))
            operand = record._read_operand(root_fd, record._operand_rel(req, ctx))
            plan = record._PLANNERS["create"](req, ctx, operand, record._clock_now())
            for op in plan.operands:
                op.new_raw = record._emit_bytes(op.new_model)
            pre = dict((rel, read(root, rel)) for rel in RECORD_OPERANDS)
            unreserved = "record-20260927T000201Z-" + "c" * 16
            other = "record-20260927T000202Z-" + "d" * 16

            def held_publish(run_id, reserve=None):
                cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
                try:
                    if reserve is not None:
                        record._opf_journal.reserve_allocation(cap, "record", run_id, reserve)
                    record._publish(ctx, plan, "create", cap=cap, run_id=run_id)
                finally:
                    record._opf_oplock.release_operation(cap)
            for label, call, needle in (
                    ("no run id", lambda: record._publish(ctx, plan, "create"),
                     "requires the run id its irrevocable id reservation"),
                    ("no reservation", lambda: held_publish(unreserved), "requires its id reservation"),
                    ("other claims", lambda: held_publish(other, {"BI": 7, "WL": 7}),
                     "to claim exactly this plan's numbers")):
                try:
                    call()
                except record.RecordError as exc:
                    assert needle in str(exc) and "no transaction is opened" in str(exc), (label, str(exc))
                else:
                    raise AssertionError("T82 a publication with {} must refuse".format(label))
                assert journal_states(root, TYPED_JOURNAL) == {}, ("T82 no transaction", label)
                assert dict((rel, read(root, rel)) for rel in RECORD_OPERANDS) == pre, \
                    ("T82 the operands are untouched", label)
                assert capability_records(root) == (False, False), ("T82 the capability is released", label)
        finally:
            os.close(root_fd)
        assert not (Path(root) / record._opf_store.allocation_record("record", unreserved)).exists(), \
            "T82 the boundary never reserves on its own"


@contextlib.contextmanager
def flip_t82():
    """The reviewed head's boundary: a direct call naming no run id gets a freshly minted one, and no
    reservation evidence is required."""
    original = record._publish_homes2

    def minting(ctx, plan, subcommand, cap=None, run_id=None):
        if run_id is None:
            run_id = record._record_run_id(os.urandom(16).hex())
        return original(ctx, plan, subcommand, cap, run_id)
    with patch.object(record, "_publish_homes2", minting), \
            patch.object(record, "_require_reservation", lambda ctx, run_id: None):
        yield


def t83_journal_states_confirmed_absence(fx):
    """The harness's journal reader reads an empty journal ONLY for confirmed absence: an absent home
    reads {}, while a regular file, a symlink to a directory, a dangling symlink, and an unreadable
    home (the opener's PermissionError) are each a harness fault, never apparent emptiness; a real
    empty home reads {}."""
    root = fx.case("t83-journal-home")
    home = Path(root) / TYPED_JOURNAL
    assert journal_states(root, TYPED_JOURNAL) == {}, "T83 an absent home reads empty"
    home.parent.mkdir(parents=True)
    (home.parent / "elsewhere").mkdir()

    def faults(label):
        try:
            got = journal_states(root, TYPED_JOURNAL)
        except Harness:
            return
        raise AssertionError("T83 a {} journal home must be a harness fault, not {!r}".format(label, got))
    home.write_bytes(b"")
    faults("regular-file")
    home.unlink()
    home.symlink_to("elsewhere")
    faults("symlinked")
    home.unlink()
    home.symlink_to("absent")
    faults("dangling-symlink")
    home.unlink()
    home.mkdir()

    def unreadable(root_path, rel):
        raise PermissionError(errno.EACCES, "synthetic unreadable journal home", rel)
    with patch.object(journal, "open_journal_root_from_path", unreadable):
        faults("unreadable")
    assert journal_states(root, TYPED_JOURNAL) == {}, "T83 an empty home reads empty"


def flip_t83():
    """The reviewed head's shortcut: anything that is not a directory reads as an empty journal."""
    original = journal_states

    def shortcut(root, rel=None):
        if not (Path(root) / (record.JOURNAL_REL if rel is None else rel)).is_dir():
            return {}
        return original(root, rel)
    return patch.object(sys.modules[__name__], "journal_states", shortcut)


# --- T85: a broken allocations home refuses the reservation scan (spec 8.2) ------------------------------

@contextlib.contextmanager
def _t85_unlistable(home):
    """os.listdir raising EIO for a descriptor of `home` alone (matched by device and inode on the
    descriptor), every other listing unchanged: the scan's open succeeds and its listing fails. The
    wrapper takes a descriptor as os.listdir does, so it is listed in os.supports_fd while it stands."""
    st = home.stat()
    ident = (st.st_dev, st.st_ino)
    real = os.listdir

    def listdir(*args, **kwargs):
        target = args[0] if args else kwargs.get("path")
        if isinstance(target, int):
            fst = os.fstat(target)
            if (fst.st_dev, fst.st_ino) == ident:
                raise OSError(errno.EIO, "synthetic unlistable allocations home")
        return real(*args, **kwargs)
    with patch.object(os, "listdir", listdir), \
            patch.object(os, "supports_fd", set(os.supports_fd) | {listdir}):
        yield


def _t85_scan_refuses(root, needle):
    """The reservation scan itself, called over the resolved store as the planner calls it: it must
    refuse with `needle`, every byte of the store unchanged."""
    before = snapshot(root)
    res = record._opf_store.resolve_store(Path(os.path.abspath(str(root))))
    assert res.status == record._opf_store.RESOLVED, res
    root_fd = record._opf_store._open_dir_nofollow(res.store_root)
    try:
        try:
            high = record._reservation_high(record.Context(res, str(root), root_fd))
        except record.RecordError as exc:
            message = str(exc)
        else:
            raise AssertionError(("the reservation scan refuses", needle, high))
    finally:
        os.close(root_fd)
    assert snapshot(root) == before, ("bytes untouched by the scan", needle)
    assert needle in message, ("the scan's refusal text", needle, message)


def _t85_leg(fx, kind, shape):
    """One T85 leg: the `kind` allocations home is broken as `shape` on a homes-2 store, and a create
    refuses exit 2 with every entry of the store unchanged (snapshot), never reading the broken home as
    holding no reservations. Every leg but one refuses end to end with the scan's own message; the
    record kind's file-parent leg refuses end to end at the record journal inspection, and its direct
    scan call (_t85_scan_refuses) shows the scan's message. symlink, regular-file and mode-0 refuse at
    the home's own no-follow directory open (a mode-0 directory cannot be opened for reading, so it
    refuses there, never at the listing); file-parent refuses at the contained walk to the home's parent
    (the record kind's home shares that parent with the record journal home, whose inspection refuses
    first); unlistable refuses at the listing (an injected EIO after a successful open); underivable
    refuses before any home is opened (_allocations_rel patched to raise, KeyError for the import kind
    and ValueError for the record kind, so both caught classes are exercised). snapshot refuses a
    directory it cannot list, so the mode-0 leg snapshots the store before the chmod and after the mode
    is restored, and checks the home's mode is still 0 when the run returns. The mode-0 guard tests the
    effective uid alone: under uid 0 the leg is reported as cannot-evaluate, never a pass, while a
    process that is not uid 0 but holds a read-override capability (CAP_DAC_OVERRIDE or
    CAP_DAC_READ_SEARCH) can open the home, so the leg fails (exit 1): fail-closed, but reported as a
    failure rather than as not evaluated."""
    if shape == "mode-0" and os.geteuid() == 0:
        raise CannotEvaluate("uid 0 is not bound by mode bits, so a mode-0 {} allocations home cannot be "
                             "made unreadable".format(kind))
    env = fx.env
    rel = record._allocations_rel(kind)
    root = fx.case("t85-{}-{}".format(kind, shape))
    home = Path(root) / rel
    opened = "cannot open the {} allocations home {} (".format(kind, rel)
    with _self_test_homes2_active(root):
        if shape == "symlink":
            home.parent.mkdir(parents=True, exist_ok=True)
            (home.parent / "elsewhere").mkdir()
            home.symlink_to("elsewhere")
            refused_untouched(env, root, CREATE, opened + "[Errno ")
        elif shape == "regular-file":
            home.parent.mkdir(parents=True, exist_ok=True)
            home.write_bytes(b"")
            refused_untouched(env, root, CREATE, opened + "[Errno ")
        elif shape == "file-parent":
            home.parent.parent.mkdir(parents=True, exist_ok=True)
            home.parent.write_bytes(b"")
            walk = opened + "cannot open contained directory component"
            if kind == "record":
                # The record kind's home shares its parent with the record journal home, whose
                # inspection refuses first end to end; the scan's own refusal is pinned directly.
                refused_untouched(env, root, CREATE, "cannot inspect the record journal")
                _t85_scan_refuses(root, walk)
            else:
                refused_untouched(env, root, CREATE, walk)
        elif shape == "mode-0":
            home.mkdir(parents=True)
            before = snapshot(root)
            readable = stat.S_IMODE(home.lstat().st_mode)
            os.chmod(home, 0)
            try:
                result = record_cli(env, root, CREATE)
                mode = stat.S_IMODE(home.lstat().st_mode)
            finally:
                os.chmod(home, readable)
            assert snapshot(root) == before and mode == 0, ("bytes untouched on refusal", CREATE, mode,
                                                            result[0], result[2][-800:])
            refused(result, opened + "[Errno {}]".format(errno.EACCES))
        elif shape == "unlistable":
            home.mkdir(parents=True)
            with _t85_unlistable(home):
                refused_untouched(env, root, CREATE, "cannot list the {} allocations home {} ([Errno {}] "
                                  "synthetic unlistable allocations home".format(kind, rel, errno.EIO))
        elif shape == "underivable":
            # The derivation the scan reads is the reservation seam's own (_opf_journal), which the
            # planner's scan and the seam's derivation under the held capability both call.
            real = record._opf_journal._allocations_rel
            error = KeyError if kind == "import" else ValueError

            def underivable(k):
                if k == kind:
                    raise error("synthetic underivable {} home".format(kind))
                return real(k)
            with patch.object(record._opf_journal, "_allocations_rel", underivable):
                refused_untouched(env, root, CREATE, "the journal kind {!r} has no reservation home this "
                                  "build can derive (".format(kind))
        else:
            raise Harness("T85 has no leg {!r}".format(shape))


def _t85(kind, shape):
    def test(fx):
        _t85_leg(fx, kind, shape)
    test.__name__ = "t85_{}_{}".format(kind, shape.replace("-", "_"))
    return test


# The reservation scan's four broken-home handlers, each with its own flip that turns that handler alone
# into `return {}`, in the reservation seam's own scan (_opf_journal), which the planner and the seam's
# derivation under the held capability both call: in _kind_reservation_high, the walk to the home's parent
# (_journal._open_parent), the home's own no-follow open, and its listing; in reservation_high, the
# derivation of a kind's home (two
# flips, one per underivable leg, because only the record kind's leg then crashes past the scan, so only
# its flip expects that crash, _INTENDED_BACKSTOPS). A
# wrapper returns {} exactly where its handler raises, which is where the handler would have returned,
# and leaves every other outcome as it is. The refusals are told apart by message (no entry refusal
# shares one), and the two open handlers, which share theirs, by where the error they handled was raised.
T85_DERIVE_REFUSAL = "has no reservation home this build can derive"


def _t85_home_handler(exc):
    """The _kind_reservation_high handler that raised the JournalError `exc`: "open-parent" when the error it
    handled was raised inside _journal._open_parent, "open" for the home's own open, "listing", or None
    for any other refusal."""
    message = str(exc)
    if message.startswith("cannot list the "):
        return "listing"
    if not message.startswith("cannot open the "):
        return None
    tb = exc.__context__.__traceback__ if exc.__context__ is not None else None
    while tb is not None:
        if tb.tb_frame.f_code is record._journal._open_parent.__code__:
            return "open-parent"
        tb = tb.tb_next
    return "open"


@contextlib.contextmanager
def _t85_home_handler_returns_empty(handler):
    """_kind_reservation_high with its `handler` (as _t85_home_handler names it) turned into return {}."""
    kind_high = record._opf_journal._kind_reservation_high

    def returning_empty(root_fd, kind, home, skip=None):
        try:
            return kind_high(root_fd, kind, home, skip)
        except journal.JournalError as exc:
            if _t85_home_handler(exc) == handler:
                return {}
            raise
    with patch.object(record._opf_journal, "_kind_reservation_high", returning_empty):
        yield


def flip_t85_open_parent():
    """The handler of the walk to the allocations home's parent turned into return {}: a home whose
    parent cannot be opened reads as holding no reservations (the file-parent legs)."""
    return _t85_home_handler_returns_empty("open-parent")


def flip_t85_open():
    """The handler of the allocations home's own no-follow open turned into return {}: a home that is a
    symlink, a regular file or unreadable reads as holding no reservations (the symlink, regular-file
    and mode-0 legs)."""
    return _t85_home_handler_returns_empty("open")


def flip_t85_listing():
    """The handler of the allocations home's listing turned into return {}: a home that opens but cannot
    be listed reads as holding no reservations (the unlistable legs)."""
    return _t85_home_handler_returns_empty("listing")


@contextlib.contextmanager
def flip_t85_derive():
    """The derivation handler of reservation_high turned into return {}: a kind whose home cannot be
    derived ends the scan with no reservations, every kind's included (the import kind's underivable
    leg, which then records)."""
    high = record._opf_journal.reservation_high

    def returning_empty(root_fd, exclude=None):
        try:
            return high(root_fd, exclude)
        except journal.JournalError as exc:
            if T85_DERIVE_REFUSAL in str(exc):
                return {}
            raise
    with patch.object(record._opf_journal, "reservation_high", returning_empty):
        yield


@contextlib.contextmanager
def flip_t85_derive_record():
    """flip_t85_derive for the record kind's underivable leg, a flip of its own so that it expects the one
    injection that leg raises: with the scan ended empty, the run reaches the publication boundary, whose
    read of its own reservation derives the record kind's home again (_read_reservation), and the leg's
    ValueError escapes there to the record verb's backstop (the injection its declared backstop is bound
    to, recorded where it leaves _read_reservation)."""
    with flip_t85_derive(), \
            _crash_point("t85-record-underivable", record, "_read_reservation",
                         ValueError("synthetic underivable record home")):
        yield


# --- T91, T92: the seam's own next-ID derivation (spec 8.2) ---------------------------------------------

def _seam_reservation_bi(root, run_id):
    """The BI claim of the record reservation `run_id` on the store `root`, whose bytes must be the
    canonical payload its own content declares."""
    rel = record._opf_store.allocation_record("record", run_id)
    payload = model(root, rel)
    assert read(root, rel) == record._opf_journal.reservation_payload(
        "record", run_id, payload["operation_id"], payload["claims"]), ("canonical reservation", rel)
    return payload["claims"]["BI"]


def t91_seam_five_sequential_distinct(fx):
    """Five runs each reserving one BI id through the reservation seam alone (no planner), each under
    an operation capability of its own acquired and released in turn, never share a number: the seam
    derives every claim under the held capability from counters.toml and every earlier reservation,
    so the runs claim the counter's base + 1 to base + 5 in order, five reservations standing."""
    root = fx.case("t91-homes2-five-runs")
    with _self_test_homes2_active(root):
        base = model(root, COUNTERS)["counters"]["BI"]
        runs = ["record-20260928T00000{}Z-".format(n) + "e" * 16 for n in range(1, 6)]
        for run_id in runs:
            cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
            try:
                record._opf_journal.reserve_allocation(cap, "record", run_id, {"BI": 1})
            finally:
                record._opf_oplock.release_operation(cap)
        claims = [_seam_reservation_bi(root, run_id) for run_id in runs]
        assert claims == [base + n for n in range(1, 6)], ("T91 five runs claim five distinct numbers", base,
                                                            claims)
        assert sorted(os.listdir(Path(root) / record._ALLOCATIONS_REL)) == sorted(r + ".toml" for r in runs), \
            "T91 five reservations stand"


def flip_t91_scan_ignored():
    """The seam deriving from counters.toml alone (the reservation scan returns nothing): every run
    reserves the same next number."""
    return patch.object(record._opf_journal, "reservation_high", lambda root_fd, exclude=None: {})


T92_CHILDREN = 6
# Each child's own bound on retrying a contended acquisition, and the parent's bound on reaping them.
T92_CHILD_DEADLINE = 60.0
T92_REAP_DEADLINE = 120.0


def _t92_child(root, run_id, go_fd, parent_end):
    """A forked T92 child: close its inherited copy of the parent's write end of the start pipe, wait
    for the parent's start byte, take the operation capability (retrying while another child holds it,
    up to T92_CHILD_DEADLINE), reserve one BI id, release, and exit 0; any failure exits 1. It never
    returns."""
    code = 1
    try:
        os.close(parent_end)
        os.read(go_fd, 1)
        deadline = time.monotonic() + T92_CHILD_DEADLINE
        while True:
            try:
                cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
                break
            except record._opf_oplock.OpLockError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.01)
        try:
            record._opf_journal.reserve_allocation(cap, "record", run_id, {"BI": 1})
        finally:
            record._opf_oplock.release_operation(cap)
        code = 0
    except BaseException:  # noqa: BLE001  the exit status is the child's only verdict
        code = 1
    finally:
        os._exit(code)


def _t92_reap(pids):
    """The wait status of each of this test's own children, in order, None for one that outlived
    T92_REAP_DEADLINE; such a child, and only such a child of this test, is killed and reaped."""
    statuses = {}
    deadline = time.monotonic() + T92_REAP_DEADLINE
    while len(statuses) < len(pids) and time.monotonic() < deadline:
        for pid in pids:
            if pid not in statuses:
                waited, status = os.waitpid(pid, os.WNOHANG)
                if waited == pid:
                    statuses[pid] = status
        time.sleep(0.05)
    for pid in pids:
        if pid not in statuses:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    return [statuses.get(pid) for pid in pids]


def t92_seam_concurrent_processes_distinct(fx):
    """Concurrent runs in separate processes never share a number. T92_CHILDREN forked processes,
    released together through a pipe, each take the operation capability (retrying while another holds
    it: the anchor flock is non-blocking, so a contender is refused, never queued) and reserve one BI id
    through the seam: every child exits 0 and the claims are exactly base + 1 to base + T92_CHILDREN."""
    root = fx.case("t92-homes2-concurrent")
    with _self_test_homes2_active(root):
        base = model(root, COUNTERS)["counters"]["BI"]
        runs = ["record-20260928T0001{:02d}Z-".format(n) + "f" * 16 for n in range(T92_CHILDREN)]
        go_read, go_write = os.pipe()
        pids = []
        try:
            for run_id in runs:
                pid = os.fork()
                if pid == 0:
                    _t92_child(root, run_id, go_read, go_write)
                pids.append(pid)
            os.write(go_write, b"g" * len(pids))
        finally:
            os.close(go_read)
            os.close(go_write)
            statuses = _t92_reap(pids)
        assert all(s is not None and os.WIFEXITED(s) and os.WEXITSTATUS(s) == 0 for s in statuses), \
            ("T92 every child reserved and exited 0", statuses)
        claims = sorted(_seam_reservation_bi(root, run_id) for run_id in runs)
        assert claims == list(range(base + 1, base + 1 + T92_CHILDREN)), \
            ("T92 concurrent runs claim distinct consecutive numbers", base, claims)


# How long T93's first thread is held inside the derivation waiting for the second thread's attempt to end,
# and the parent's bound on joining both threads.
T93_OVERLAP_DEADLINE = 30.0
T93_JOIN_DEADLINE = 60.0


def t93_seam_threads_serialised(fx):
    """Two threads sharing ONE held operation capability never reserve the same number, with the two
    reservation attempts made to overlap: the first thread is held inside the seam's derivation (under
    the capability's claim, after it has derived its claims and before it publishes) until the second
    thread's attempt has ended, and the second thread starts only once the first is held there. The
    second attempt is refused at once as the capability being used, the first then reserves base + 1,
    and exactly that one reservation stands. A second attempt that blocks instead of ending releases the
    first at T93_OVERLAP_DEADLINE and turns this red on the outcomes."""
    root = fx.case("t93-homes2-threads")
    with _self_test_homes2_active(root):
        base = model(root, COUNTERS)["counters"]["BI"]
        runs = ["record-20260928T0002{:02d}Z-".format(n) + "f" * 16 for n in range(2)]
        outcomes = [None, None]
        held_inside, second_ended = threading.Event(), threading.Event()
        real_derive = record._opf_journal._derive_claims

        def holding_derive(root_fd, machine_rel, kind, run_id, demand):
            claims = real_derive(root_fd, machine_rel, kind, run_id, demand)
            if run_id == runs[0]:
                held_inside.set()
                second_ended.wait(T93_OVERLAP_DEADLINE)
            return claims

        def worker(index):
            try:
                record._opf_journal.reserve_allocation(cap, "record", runs[index], {"BI": 1})
                outcomes[index] = "reserved"
            except journal.JournalError as exc:
                outcomes[index] = str(exc)
            finally:
                if index == 1:
                    second_ended.set()
        cap = record._opf_oplock.acquire_operation(str(root), record.VERB)
        try:
            with patch.object(record._opf_journal, "_derive_claims", holding_derive):
                first = threading.Thread(target=worker, args=(0,))
                first.start()
                reached = held_inside.wait(T93_OVERLAP_DEADLINE)
                second = threading.Thread(target=worker, args=(1,))
                second.start()
                for thread in (first, second):
                    thread.join(timeout=T93_JOIN_DEADLINE)
        finally:
            record._opf_oplock.release_operation(cap)
        assert reached, ("T93 the first thread is held inside the derivation", outcomes)
        assert not first.is_alive() and not second.is_alive(), "T93 both threads finished"
        assert outcomes[0] == "reserved" and "being released or used" in (outcomes[1] or ""), \
            ("T93 the overlapping second attempt is refused as the capability being used", outcomes)
        standing = sorted(n[:-len(".toml")] for n in os.listdir(Path(root) / record._ALLOCATIONS_REL))
        numbers = [_seam_reservation_bi(root, run_id) for run_id in standing]
        assert standing == [runs[0]] and numbers == [base + 1], \
            ("T93 exactly the first thread's reservation stands, at base + 1", base, standing, numbers)


@contextlib.contextmanager
def flip_t93_unserialised():
    """The seam's held-capability scope without its claim: each entry claims a private twin of the
    capability (the same store, identity and descriptors, its own unused lock), so a second thread using
    the same capability is never refused and derives while the first is still inside."""
    real = record._opf_journal._held_root

    @contextlib.contextmanager
    def unserialised(cap):
        twin = copy.copy(cap)
        twin._claim = threading.Lock()
        with real(twin) as root_fd:
            yield root_fd
    with patch.object(record._opf_journal, "_held_root", unserialised):
        yield


# --- T86: the discriminator binds each flip to an assertion, never a backstop or harness fault ---------


def _bound_injection(line, printed, injections, consumed, allowed):
    """Consume and return the recorded injection a backstop line printed: the line carries exactly one
    token a recorded injection holds (_injected), that token not yet consumed and its label one the flip
    declares, the line holds that object's repr, and one record CLI backstop frame holding that same object
    (identity) wrote every character of this line into the intact capture it was read from (`printed`,
    _note_backstops).
    A line in a mutated capture, one with no recorded token (another verb's, a twin of the injection, a
    copied diagnostic of an unrecorded exception), one with two, one whose token's injection the
    backstop never printed into its capture (an exception raised from the injection's args, an unrelated
    error formatted with its message, any child's line, a nested run's line in a capture the injection's
    backstop wrote nothing, a preamble or its repr alone into), or a second line carrying an already
    consumed token binds none: None. Each injection accounts for one line only."""
    tokens = set(_INJECTION_TOKEN.findall(line)) & injections.keys()
    if printed is None or len(tokens) != 1:
        return None
    token, = tokens
    label, exc = injections[token]
    if label not in allowed or token in consumed or repr(exc) not in line \
            or not any(exc is seen for seen in printed):
        return None
    consumed.add(token)
    return exc


def _oop_declared(allowed):
    """(records, pools): _OOP_POOL fresh (token, serial) pairs per declared label, recorded parent-side
    as a mapping from each token to (label, the reprs of the declared injection variants of that label,
    each rebuilt as its private fault class, tokenized with that token and holding that serial), and a
    mapping from each label to its pairs in draw order, the pools handed to the child. Built BEFORE
    the child is launched, from _DECLARED_INJECTIONS alone: the parent computes every repr it will
    accept itself and never reads one back from the child."""
    records, pools = dict(), dict()
    for label in allowed:
        factories = _DECLARED_INJECTIONS.get(label)
        if not factories:
            raise Harness("injection label {!r} has no declared construction".format(label))
        pools[label] = []
        for _ in range(_OOP_POOL):
            token = "injection-" + secrets.token_hex(8)
            serial = "serial-" + secrets.token_hex(8)
            records[token] = (label, tuple(
                repr(_serialed(_tokenize(_private_injection(factory()), token), serial))
                for factory in factories))
            pools[label].append((token, serial))
    return records, pools


# The diagnostic envelope of the record CLI backstops (record.cli, opf._cmd_record) in a line _BACKSTOP
# found: the printed repr of the exception is everything between the fixed head and the fixed tail. The
# envelope is shared: the backstops of opf adopt plan and status print the same one,
# and the verb prefix that tells them apart lies before the _BACKSTOP match, so the envelope alone never
# shows which verb printed a line; only the recorded token and parent-computed repr, which the record
# tests' injections alone carry, make a line bind.
_BACKSTOP_ENVELOPE = re.compile(r"cannot evaluate: unexpected error \((?P<shown>.*)\); "
                                r"failing closed to exit 2")


def _bound_injection_oop(line, records, consumed, allowed):
    """Consume and return the token of the recorded injection a backstop line binds to, decided from
    bytes the parent read from the pipe itself: the line carries exactly one recorded token (every
    recorded token is parent-minted and unguessable), that token unconsumed and its label one the flip
    declares, and the line is the shared backstop envelope (_BACKSTOP_ENVELOPE) around an exception
    repr EQUAL to one the parent computed for the injection of that token before the child was
    launched, private class and minted serial included. A line with no recorded token (the line of
    another verb, a twin of the injection, a copied diagnostic of an unrecorded exception, a token the
    child made up), one with two, one in any other envelope, one whose repr differs from every
    parent-computed one (an exception wrapping the injection, its repr or its message; an exception
    group holding it; an unrelated error formatted with its message; a new exception a child process
    printed from it; an args copy of it rebuilt as its plain base type or as a same-named local class;
    a copy, deep copy, pickle round trip or cls(*args) rebuild of it as its own private class, each
    showing a fresh built serial), or a second line carrying an already consumed token (a cleared and
    reprinted capture) binds none: None. Each token accounts for one line only. Under the T87 flips
    _discriminate_plain_type and _discriminate_repr_substring (_oop_exact[0] False) a line binds when a
    computed repr occurs anywhere in it."""
    tokens = set(_INJECTION_TOKEN.findall(line)) & records.keys()
    if len(tokens) != 1:
        return None
    token, = tokens
    label, reprs = records[token]
    if label not in allowed or token in consumed:
        return None
    if _oop_exact[0]:
        envelope = _BACKSTOP_ENVELOPE.fullmatch(line)
        if envelope is None or envelope.group("shown") not in reprs:
            return None
    elif not any(shown in line for shown in reprs):
        return None
    consumed.add(token)
    return token


def _oop_reap(pid, err_fd, verdict_fd):
    """Read the two pipes of the child to EOF and reap it, every read and wait bounded by
    _OOP_TIMEOUT: (stderr bytes, verdict bytes, wait status, timed_out). On a timeout the child is
    killed; the caller fails closed on timed_out, never trusting a partial run."""
    deadline = time.monotonic() + _OOP_TIMEOUT
    buffers = {err_fd: bytearray(), verdict_fd: bytearray()}
    open_fds = {err_fd, verdict_fd}
    timed_out = False
    while open_fds:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        ready = select.select(sorted(open_fds), [], [], min(remaining, 5.0))[0]
        for fd in ready:
            chunk = os.read(fd, 65536)
            if chunk:
                buffers[fd].extend(chunk)
            else:
                open_fds.discard(fd)
    if timed_out:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    status = None
    reap_deadline = time.monotonic() + 60.0
    while status is None:
        waited, code = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            status = code
        elif time.monotonic() >= reap_deadline:
            timed_out = True
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
            status = os.waitpid(pid, 0)[1]   # prompt: the child was just killed
        else:
            time.sleep(0.05)
    return bytes(buffers[err_fd]), bytes(buffers[verdict_fd]), status, timed_out


def _discriminate(name, test, flip):
    """The flip must turn the test red through an assertion, and the unflipped test must stay green
    around it. The flipped run executes OUT OF PROCESS, in a forked child of this gate (the gate's own
    interpreter with the flags it was launched with, so isolated by -I only when the gate itself runs
    with -I; the in-process patches of the flip apply there and nowhere else): the stderr of the child
    is an OS pipe the parent reads, a second pipe carries the one structured verdict of the child, and
    the parent decides from bytes it read itself. Before the fork the parent mints the tokens of each
    declared injection and computes the reprs it will accept (_oop_declared); the child receives the
    token pools alone (_oop_tokens, drawn by _injected). A backstop line (_BACKSTOP) in the bytes of the
    pipe fails the discrimination unless it carries exactly one recorded, unconsumed token whose label
    the flip declares and a parent-computed repr of that injection (_INTENDED_BACKSTOPS,
    _bound_injection_oop); then every injection the flip expects must have fired in the child (its
    verdict lists each injection it raised, _oop_fired) and each one that fired must have had its token
    consumed by such a line, so a declared injection caught, swallowed or printed out of view, or never
    reached, before an unrelated assertion fails it too (_unseen_injections); any child exception other
    than an assertion (the own construction of the flip included, a SystemExit too; an interrupt
    propagates) fails it by name; and, fail closed, so does a child that crashes, reports no complete
    verdict, exits non-zero or outlives _OOP_TIMEOUT (every read and wait is bounded, _oop_reap).
    Anything the child prints that the parent did not record binds nothing, and a line binds only as
    the shared backstop envelope (_BACKSTOP_ENVELOPE) around a repr EQUAL to one the parent computed,
    which names the private fault class of the injection (_private_injection) and the serial the
    parent minted for its token, held only by the injection object itself (_OpfInjection): an args
    copy, a copy, deep copy, pickle round trip or class rebuild of the injection, and any exception
    wrapping it or its repr, bind nothing. What the child can still forge is the line of one of its
    own declared injections by deliberately reproducing that object's repr: writing the envelope
    around repr(injected) itself (the child holds the object by necessity), or moving the injection's
    own _opf_serial onto another object of its private class; nothing else. A crash production code
    swallows prints no backstop line and is outside this check unless it is an injection the flip
    expects, which must be seen."""
    test()
    allowed = _INTENDED_BACKSTOPS.get(flip, ())
    records, pools = _oop_declared(allowed)
    consumed = set()
    _oop_serial[0] += 1
    serial = _oop_serial[0]
    sys.stdout.flush()
    sys.stderr.flush()
    err_read, err_write = os.pipe()
    verdict_read, verdict_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        exit_code = 1
        try:
            os.close(err_read)
            os.close(verdict_read)
            os.dup2(err_write, 2)
            os.close(err_write)
            _oop_tokens[0] = pools
            del _oop_fired[:]   # a nested child starts with its own record, not its parent's
            _oop_child[0] = True
            _case_namespace[0] = "{}oop{:04d}-".format(_case_namespace[0], serial)
            try:
                with flip():
                    test()
            except AssertionError:
                verdict = dict(outcome="assertion")
            except KeyboardInterrupt:
                verdict = dict(outcome="interrupt")
            except BaseException as exc:  # noqa: BLE001  reported whole; the parent fails the run by name
                verdict = dict(outcome="exception", shown=repr(exc))
            else:
                verdict = dict(outcome="survived")
            verdict["fired"] = [list(event) for event in _oop_fired]
            sys.stderr.flush()
            sys.stdout.flush()
            payload = memoryview(json.dumps(verdict).encode("utf-8"))
            while len(payload):
                payload = payload[os.write(verdict_write, payload):]
            os.close(verdict_write)
            exit_code = 0
        finally:
            os._exit(exit_code)
    os.close(err_write)
    os.close(verdict_write)
    try:
        stderr_bytes, verdict_bytes, status, timed_out = _oop_reap(pid, err_read, verdict_read)
    finally:
        os.close(err_read)
        os.close(verdict_read)
    verdict = None
    with contextlib.suppress(ValueError):
        verdict = json.loads(verdict_bytes.decode("utf-8")) if verdict_bytes else None
    outcome = verdict.get("outcome") if isinstance(verdict, dict) else None
    if outcome == "interrupt":
        raise KeyboardInterrupt
    fired = verdict.get("fired") if isinstance(verdict, dict) else None
    if not isinstance(fired, list) or not all(
            isinstance(event, list) and len(event) == 2 and isinstance(event[0], str)
            and (event[1] is None or isinstance(event[1], str)) for event in fired):
        fired = None
    if timed_out or not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0 or outcome not in (
            "assertion", "survived", "exception") or fired is None:
        raise AssertionError("{} failed closed: the flipped child run ended without a complete "
                             "structured verdict (timed out {}, wait status {}, verdict {!r})".format(
                                 name, timed_out, status, verdict))
    if outcome == "exception":
        raise AssertionError("{} went red through a harness exception ({}), not an "
                             "assertion".format(name, verdict.get("shown")))
    if outcome == "survived":
        raise AssertionError(name + " survived its flip")
    stray = [found.group(0) for found in
             _BACKSTOP.finditer(stderr_bytes.decode("utf-8", "replace"))
             if _bound_injection_oop(found.group(0), records, consumed, allowed) is None]
    if stray:
        raise AssertionError("{} went red through the unexpected-error backstop (exit 2, cannot "
                             "evaluate), not an assertion: {}".format(name, stray[0]))
    unseen = _unseen_injections([tuple(event) for event in fired], consumed, allowed)
    if unseen:
        raise AssertionError("{} went red through an assertion, but {}".format(name, unseen[0]))
    test()


def _unseen_injections(fired, consumed, expected):
    """What a flipped run that went red through an assertion left unproven of the injections its flip
    expects (_INTENDED_BACKSTOPS), from the (label, token or None) of each injection the child reports
    it raised (`fired`, _oop_fired) and the tokens the parent bound to backstop lines it read itself
    (`consumed`): each injection of an expected label that fired with no token, or whose token no line
    consumed (caught, swallowed, printed out of view, or drawn past the pool), then each expected label
    that never fired. Empty only when every expected injection fired and each one that fired was seen
    at its backstop; an injection of a label the flip does not expect is the test's own and is not
    read here."""
    unseen = []
    for label, token in fired:
        if label in expected and token not in consumed:
            unseen.append("its expected injection {!r} fired without its backstop line in view "
                          "({})".format(label, token or "no token drawn"))
    unseen.extend("its expected injection {!r} never fired".format(label) for label in expected
                  if all(label != fired_label for fired_label, _token in fired))
    return unseen


def _discriminate_plain_type(name, test, flip, current=_discriminate):
    """A T87 flip: the _discriminate_repr_substring binding, with each injection raised and declared as
    its plain base type rather than its private fault class (_oop_private), so an args copy of the
    injection rebuilt as that type (NameError(*injected.args)) prints the injection's line byte for byte
    and binds."""
    me = sys.modules[__name__]
    with patch.object(me, "_oop_private", [False]), patch.object(me, "_oop_exact", [False]):
        current(name, test, flip)


def _discriminate_repr_substring(name, test, flip, current=_discriminate):
    """A T87 flip: the out-of-process run of _discriminate with private fault classes, but no serial in
    their reprs and a line bound when a parent-computed repr occurs anywhere in it (_oop_exact), so an
    exception wrapping the injection or its repr, an exception group holding it, and a copy, pickle
    round trip or class rebuild of it bind."""
    with patch.object(sys.modules[__name__], "_oop_exact", [False]):
        current(name, test, flip)


def _discriminate_in_process(name, test, flip):
    """A T87 flip: the flipped run stays in this process, and a backstop line binds when, read from the
    final contents of an intact capture, it carries the token of exactly one recorded injection the
    flip declares and one record CLI backstop frame holding that same object wrote that whole line into
    that capture, each token binding one line only
    (_INTENDED_BACKSTOPS, _bound_injection): in-process attribution (frame identity, capture spans,
    len() and tell()), which loaded test code in the same process could forge; any exception other
    than an assertion (a SystemExit too; an interrupt propagates) fails it by name."""
    test()
    faults, injections, consumed = [], {}, set()
    _flip_watch.append(faults)
    _flip_injections.append(injections)
    try:
        with flip():
            test()
    except AssertionError:
        allowed = _INTENDED_BACKSTOPS.get(flip, ())
        stray = [line for line, printed in faults
                 if _bound_injection(line, printed, injections, consumed, allowed) is None]
        if stray:
            raise AssertionError("{} went red through the unexpected-error backstop (exit 2, cannot "
                                 "evaluate), not an assertion: {}".format(name, stray[0])) from None
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001  a harness exception in the flipped run never discriminates
        raise AssertionError("{} went red through a harness exception ({!r}), not an "
                             "assertion".format(name, exc)) from None
    else:
        raise AssertionError(name + " survived its flip")
    finally:
        _flip_watch.pop()
        _flip_injections.pop()
        del faults[:]
        injections.clear()   # the recorded exceptions' tracebacks hold the flipped run's frames
    test()


# The fault pattern of _discriminate_arity_only: only a signature TypeError at the backstop.
_FLIP_ARITY_ONLY = re.compile(r"cannot evaluate: unexpected error[^\n]*TypeError\([^\n]*"
                              r"(?:positional argument|keyword argument)")


def _discriminate_arity_only(name, test, flip):
    """The T86 flip: a flipped run's red is refused only for a signature TypeError at the backstop, and
    a harness exception escapes as itself."""
    test()
    faults = []
    _flip_watch.append(faults)
    try:
        with flip():
            test()
    except AssertionError:
        if any(_FLIP_ARITY_ONLY.search(line) for line, _printed in faults):
            raise AssertionError("{} went red through a harness fault, its flip's replacement no longer "
                                 "matching its caller: {}".format(name, faults[0][0])) from None
    else:
        raise AssertionError(name + " survived its flip")
    finally:
        _flip_watch.pop()
    test()


def _t86_crash(*_args):
    raise NameError("t86 broken flip fixture")


def _t86_backstop_flip():
    """A flip whose replacement crashes inside the CLI: the record backstop exits 2."""
    return patch.object(guard, "acquire_lease", _t86_crash)


def _t86_declared_crash(*_args):
    raise _injected("t86-declared", NameError("t86 broken flip fixture"))


def _t86_declared_flip():
    """The same crash, recorded as an injection this flip declares in _INTENDED_BACKSTOPS."""
    return patch.object(guard, "acquire_lease", _t86_declared_crash)


def _t86_harness_flip():
    """A flip whose replacement crashes the test itself, outside any CLI run."""
    return patch.object(sys.modules[__name__], "recorded", _t86_crash)


def _t86_held(*_args):
    raise guard.LeaseHeldError("t86 a held lease refuses")


def _t86_assertion_flip():
    """A flip the CLI meets as an ordinary refusal: the test's own assertion turns red."""
    return patch.object(guard, "acquire_lease", _t86_held)


def t86_discrimination_bound(fx):
    """The discriminator counts a flipped run as discriminating only when it goes red through an
    assertion: a red met only through the CLI's cannot-evaluate backstop, or through a harness exception,
    fails the discrimination naming which, while an assertion red, and a backstop caused by a recorded
    injection the flip declares, discriminate."""
    def test():
        recorded(record_cli(fx.env, fx.case("t86-create"), CREATE))
    for label, flip, needle in (("backstop", _t86_backstop_flip, "unexpected-error backstop"),
                                ("harness", _t86_harness_flip, "harness exception"),
                                ("assertion", _t86_assertion_flip, None),
                                ("declared", _t86_declared_flip, None)):
        outcome = None
        try:
            sys.modules[__name__]._discriminate("t86-" + label, test, flip)
        except Exception as exc:  # noqa: BLE001  the verdict below reads which outcome was reached
            outcome = exc
        if needle is None:
            assert outcome is None, ("T86 an intended red discriminates", label, repr(outcome))
        else:
            assert isinstance(outcome, AssertionError) and needle in str(outcome), (
                "T86 a red through no assertion never discriminates", label, repr(outcome))


def flip_t86():
    """A discriminator that refuses only a signature TypeError at the backstop."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_arity_only)


# --- T87: a declared backstop is bound to the recorded injection that must cause it ---------------------


# The declarations of _discriminate_substring: backstop texts each matched as a substring of any
# backstop line.
_SUBSTRING_BACKSTOPS = {
    flip_t18_requires: ("KeyError('--decision')",),
    flip_t18_together: ("KeyError('--decided-by')",),
    flip_string_claims_admitted: ("TypeError(\"'>' not supported between instances of 'str' and 'int'\")",),
    flip_t62: ("BrokenPipeError(32, 'synthetic output failure after the lease release')",),
    flip_t64_conclude: ("OSError(5, 'synthetic stderr failure')",),
    flip_t64_recovery: ("BrokenPipeError(32, 'synthetic emission failure')",),
    flip_t68: ("MemoryError('synthetic allocation failure after the acquisition')",),
    flip_t69_release: ("RuntimeError('fd release witness')",),
    flip_t69_token: ("MemoryError('synthetic token allocation failure')",),
    flip_t77: ("OSError({}, ".format(errno.EMFILE),),
    flip_t84: ("RecursionError('maximum recursion depth exceeded')",),
    flip_t85_derive_record: ("ValueError('synthetic underivable record home')",),
    _t86_declared_flip: ("NameError('t86 broken flip fixture')",),
}
_BACKSTOP_SUBSTRING = re.compile(r"(?:cannot evaluate|harness error): unexpected error")


def _discriminate_substring(name, test, flip):
    """A T87 flip: a declared text accepts any backstop line containing it, a store-resolution refusal
    is not a backstop, and a BaseException escapes as itself. Its flipped run's injections are recorded
    untokenised, in a list it never reads."""
    test()
    faults = []
    _flip_watch.append(faults)
    _flip_injections.append([])
    try:
        with flip():
            test()
    except AssertionError:
        allowed = _SUBSTRING_BACKSTOPS.get(flip, ())
        stray = [line for line, _printed in faults if _BACKSTOP_SUBSTRING.match(line)
                 and not any(needle in line for needle in allowed)]
        if stray:
            raise AssertionError("{} went red through the unexpected-error backstop (exit 2, cannot "
                                 "evaluate), not an assertion: {}".format(name, stray[0])) from None
    except Exception as exc:  # noqa: BLE001  the width of this flip, which lets a BaseException escape
        raise AssertionError("{} went red through a harness exception ({!r}), not an "
                             "assertion".format(name, exc)) from None
    else:
        raise AssertionError(name + " survived its flip")
    finally:
        _flip_watch.pop()
        _flip_injections.pop()
    test()


def _t87_unrelated_eio(*_args):
    raise OSError(errno.EIO, "UNRELATED crash in a broken flip; intended injection never reached")


def _t87_unrelated_flip():
    """A broken flip whose replacement crashes before its test's injection is reached."""
    return patch.object(guard, "_claim_after_release", _t87_unrelated_eio)


def _t87_mimic_crash(*_args):
    raise RuntimeError("NameError('t86 broken flip fixture')")


def _t87_twin_crash(*_args):
    _injected("t86-declared", NameError("t86 broken flip fixture"))
    raise NameError("t86 broken flip fixture")


def _t87_print_twin(*_args, **_kwargs):
    raise NameError("t86 broken flip fixture")


@contextlib.contextmanager
def _t87_print_twin_flip():
    """The declared crash, whose diagnostic print in record.cli's backstop raises a twin of it (same type
    and message, a distinct object) that opf._cmd_record's backstop then prints: the declared line, printed
    from an object no injection recorded."""
    with _t86_declared_flip(), patch.object(record, "print", _t87_print_twin, create=True):
        yield


def _t87_resolver_crash(*_args):
    raise NameError("t87 crash in the store resolver")


def _t87_resolver_flip():
    """A crash the record CLI turns into an unexpected-error refusal, never its cannot-evaluate line."""
    return patch.object(record._opf_store, "resolve_store", _t87_resolver_crash)


def _t87_exit(*_args):
    raise SystemExit(0)


def _t87_exit_flip():
    """A flip whose replacement raises SystemExit(0) in the test itself."""
    return patch.object(sys.modules[__name__], "recorded", _t87_exit)


def _t87_args_copy_crash(*_args):
    """A recorded injection, never raised: an unrecorded plain NameError built from its args (token
    included, the private class's name not) is raised in its place."""
    injected = _injected("t86-declared", NameError("t86 broken flip fixture"))
    raise NameError(*injected.args)


def _t87_replaced(rebuild):
    """A crash recording the declared injection, never raised: rebuild(injected) is raised in its
    place."""
    def crash(*_args):
        raise rebuild(_injected("t86-declared", NameError("t86 broken flip fixture")))
    return crash


def _t87_subclass_rebuild(injected):
    """The private class found among NameError's subclasses by its name, rebuilt from the args."""
    found = next(cls for cls in NameError.__subclasses__() if cls.__name__ == "_OpfInjectedNameError")
    return found(*injected.args)


def _t87_caught_rebuild(injected):
    """The injection raised and caught, then the caught exception's class rebuilt from its args."""
    try:
        raise injected
    except NameError as caught:
        return caught.__class__(*caught.args)


def _t87_same_name_rebuild(injected):
    """A local class of the private class's name over the plain base, rebuilt from the args."""
    class _OpfInjectedNameError(NameError):
        """A same-named stand-in for the private class, local to this rebuild."""
    return _OpfInjectedNameError(*injected.args)


# The objects raised in place of a recorded, never raised injection: each repeats or contains the
# injection's repr, class name and token, but is not the injection object (a copy or rebuild shows a
# fresh built serial; a wrapper's repr only contains the injection's), so none binds.
_T87_REBUILDS = (
    ("deliberate-type-copy", lambda injected: type(injected)(*injected.args)),
    ("wrapped-repr", lambda injected: RuntimeError(repr(injected))),
    ("wrapped-object", RuntimeError),
    ("wrapped-format", lambda injected: RuntimeError("wrapped failure ({!r})".format(injected))),
    ("exception-group", lambda injected: ExceptionGroup("grouped", [injected])),
    ("copy", copy.copy),
    ("deepcopy", copy.deepcopy),
    ("pickle-round-trip", lambda injected: pickle.loads(pickle.dumps(injected))),
    ("subclass-lookup", _t87_subclass_rebuild),
    ("caught-class", _t87_caught_rebuild),
    ("same-name-class", _t87_same_name_rebuild),
)


def _t87_formatted_crash(*_args):
    """A recorded injection, never raised: an unrelated AttributeError whose message is the injection's
    (token included) is raised in its place."""
    injected = _injected("t86-declared", NameError("t86 broken flip fixture"))
    getattr(object(), str(injected))


# A child process that prints, in the record backstop's words, a new RuntimeError made from its argument.
_T87_CHILD = ("import sys\n"
              "print('opf record: cannot evaluate: unexpected error ({!r}); failing closed to exit 2'"
              ".format(RuntimeError(sys.argv[1])), file=sys.stderr)\n")


def _t87_child_parse(_argv):
    """A recorded injection, never raised: its message (token included) is passed to a child process
    whose stderr, a backstop line printing a new RuntimeError, is read as a child's is; the run then
    refuses as an ordinary refusal."""
    injected = _injected("t86-declared", NameError("t86 broken flip fixture"))
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", _T87_CHILD, str(injected)],
                          capture_output=True, text=True, timeout=180, env={})
    _note_backstops(proc.stderr)
    _oop_forward(proc.stderr)
    raise record.RecordError("t87 the run refuses after its child")


def _t87_child_flip():
    """The parse records the declared injection, hands its message to a child, and refuses."""
    return patch.object(record, "parse_request", _t87_child_parse)


def _t87_forward_print(*args, **kwargs):
    """The backstop's diagnostic, forwarded unchanged into a private capture no CLI run reads."""
    print(*args, **dict(kwargs, file=_EmissionStderr()))


def _t87_clear_print(*args, **kwargs):
    """The backstop's diagnostic, printed to its stream, which is then cleared."""
    print(*args, **kwargs)
    stream = kwargs.get("file") or sys.stderr
    stream.seek(0)
    stream.truncate(0)


def _t87_reprint_print(*args, **kwargs):
    """The backstop's diagnostic, printed, cleared, and printed again to the same stream."""
    _t87_clear_print(*args, **kwargs)
    print(*args, **kwargs)


def _t87_nested_print(*_args, **kwargs):
    """In place of the backstop's diagnostic, a nested record CLI run (whose parse meets an unrecorded
    twin) with its stderr captured privately; its diagnostic is then appended to the outer stream."""
    private = io.StringIO()
    with contextlib.redirect_stderr(private):
        record.cli(["create"])
    (kwargs.get("file") or sys.stderr).write(private.getvalue())


def _t87_nested_copy_print(preamble):
    """A print that sends the backstop's diagnostic to a private capture, writes preamble (None: nothing)
    to the backstop's own stream, then runs a nested record CLI whose parse raises an unrecorded plain
    NameError from the injection's args; the nested backstop prints that copy, the injection's token
    included but not its private class's name, into the same stream."""
    def nested_print(*args, **kwargs):
        print(*args, **dict(kwargs, file=io.StringIO()))
        if preamble is not None:
            (kwargs.get("file") or sys.stderr).write(preamble)
        record.cli(["create"])
    return nested_print


def _t87_chunks(args, kwargs):
    """The text print(*args, **kwargs) would write, cut inside its word 'unexpected'."""
    text = kwargs.get("sep", " ").join(map(str, args)) + kwargs.get("end", "\n")
    cut = text.index("unexpected") + len("unexp")
    return text[:cut], text[cut:]


def _t87_split_print(*args, **kwargs):
    """The backstop's diagnostic, written to its stream in two calls split inside 'unexpected'."""
    stream = kwargs.get("file") or sys.stderr
    for chunk in _t87_chunks(args, kwargs):
        stream.write(chunk)


def _t87_writelines_print(*args, **kwargs):
    """The backstop's diagnostic, written to its stream through writelines in the same two chunks."""
    (kwargs.get("file") or sys.stderr).writelines(_t87_chunks(args, kwargs))


@contextlib.contextmanager
def _t87_capture_flip(first_print, twin):
    """The first record CLI run's parse_request raises a recorded t86-declared injection whose backstop
    diagnostic goes through first_print; a later run's raises an unrecorded twin of it (same type and
    message, a distinct object) when twin, an unrecorded plain NameError raised from the injection's own
    args (token included, the private class's name not) when twin is "args-copy", else runs
    unflipped."""
    real_parse, calls, prints, first = record.parse_request, [], [], []

    def parse(argv):
        calls.append(None)
        if len(calls) == 1:
            first.append(_injected("t86-declared", NameError("t86 broken flip fixture")))
            raise first[0]
        if twin == "args-copy":
            raise NameError(*first[0].args)
        if twin:
            raise NameError("t86 broken flip fixture")
        return real_parse(argv)

    def first_then_plain(*args, **kwargs):
        prints.append(None)
        return (first_print if len(prints) == 1 else print)(*args, **kwargs)
    with patch.object(record, "parse_request", parse), \
            patch.object(record, "print", first_then_plain, create=True):
        yield


def _t87_forward_flip():
    """The injection's line reaches only a private capture; the twin's reaches the inspected one."""
    return _t87_capture_flip(_t87_forward_print, True)


def _t87_clear_flip():
    """The injection's line is cleared from its own capture; the twin's reaches a later one."""
    return _t87_capture_flip(_t87_clear_print, True)


def _t87_reprint_flip():
    """The injection's line is cleared and printed again into its own capture, a mutated capture."""
    return _t87_capture_flip(_t87_reprint_print, False)


def _t87_own_flip():
    """The injection's line reaches its own capture, which is inspected (the accepted control)."""
    return _t87_capture_flip(print, False)


def _t87_truncate_flip():
    """The injection's line is cleared from its own capture and nothing else is printed (the accepted
    control: no occurrence is inspected, and the red is the assertion's)."""
    return _t87_capture_flip(_t87_clear_print, False)


def _t87_nested_flip():
    """The injection's backstop runs a nested record CLI whose unrecorded twin's diagnostic, captured
    privately, is copied into the injection's own capture in place of the injection's line."""
    return _t87_capture_flip(_t87_nested_print, True)


def _t87_empty_nested_flip():
    """The injection's backstop writes nothing ("") into its own capture, then a nested run's backstop
    prints an args copy of the injection there."""
    return _t87_capture_flip(_t87_nested_copy_print(""), "args-copy")


def _t87_preamble_nested_flip():
    """The injection's backstop writes a preamble without its token into its own capture, then a nested
    run's backstop prints an args copy of the injection there."""
    return _t87_capture_flip(_t87_nested_copy_print("t87 a preamble that carries no token\n"), "args-copy")


def _t87_unwritten_nested_flip():
    """The injection's backstop makes no write to its own capture, then a nested run's backstop prints an
    args copy of the injection there."""
    return _t87_capture_flip(_t87_nested_copy_print(None), "args-copy")


def _t87_split_flip():
    """The injection's line reaches its own capture in two writes (accepted)."""
    return _t87_capture_flip(_t87_split_print, False)


def _t87_writelines_flip():
    """The injection's line reaches its own capture through writelines (accepted)."""
    return _t87_capture_flip(_t87_writelines_print, False)


class _T87LyingLen(str):
    """A str that lies in __len__ (one way to forge in-process attribution): in-process span arithmetic
    reads a length the buffer does not have, while the bytes on the pipe stay the truth."""

    def __len__(self):
        return 0


def _t87_lying_len_print(*args, **kwargs):
    """The diagnostic of the backstop, written as one str subclass lying in __len__."""
    text = kwargs.get("sep", " ").join(map(str, args)) + kwargs.get("end", "\n")
    (kwargs.get("file") or sys.stderr).write(_T87LyingLen(text))


_T87_TWIN_LINE = ("opf record: cannot evaluate: unexpected error (NameError('t86 broken flip "
                  "fixture')); failing closed to exit 2\n")


def _t87_thread_twin_print(*args, **kwargs):
    """The diagnostic of the backstop, written in two chunks with a second thread writing a twin
    backstop line into the same stream between them (an interleaving that forges in-process
    attribution, run here against the pipe): the token-bearing bytes never form a complete backstop
    line of their own, and the twin text carries no recorded token."""
    stream = kwargs.get("file") or sys.stderr
    first, second = _t87_chunks(args, kwargs)
    stream.write(first)
    twin = threading.Thread(target=stream.write, args=(_T87_TWIN_LINE,))
    twin.start()
    twin.join(timeout=60)
    assert not twin.is_alive(), "T87 the twin thread never finished its write"
    stream.write(second)


def _t87_settrace_twin_print(*args, **kwargs):
    """The diagnostic of the backstop, written around a twin line from a second thread that a trace
    function holds paused until the first chunk is written (a pausing that forges in-process
    attribution, run here against the pipe)."""
    stream = kwargs.get("file") or sys.stderr
    first, second = _t87_chunks(args, kwargs)
    paused, release = threading.Event(), threading.Event()

    def pausing(frame, event, arg):
        if not paused.is_set():
            paused.set()
            release.wait(timeout=60)
        return None

    def twin():
        sys.settrace(pausing)
        try:
            stream.write(_T87_TWIN_LINE)
        finally:
            sys.settrace(None)
    worker = threading.Thread(target=twin)
    worker.start()
    assert paused.wait(timeout=60), "T87 the traced twin thread never paused"
    stream.write(first)
    release.set()
    worker.join(timeout=60)
    assert not worker.is_alive(), "T87 the traced twin thread never finished its write"
    stream.write(second)


def _t87_wrapped_print(*args, **kwargs):
    """The diagnostic of the backstop, written by the backstop frame holding the injection, with the
    exception repr in it wrapped as a RuntimeError's: in-process frame attribution, and every binding
    by substring, accept the line; it is not the backstop's own diagnostic of the injection."""
    text = kwargs.get("sep", " ").join(map(str, args))
    found = _BACKSTOP_ENVELOPE.search(text)
    if found is not None:
        text = text.replace(found.group("shown"), repr(RuntimeError(found.group("shown"))), 1)
    print(text, **kwargs)


def _t87_wrapped_flip():
    """The line of the injection, rewritten by its own backstop frame to wrap the repr."""
    return _t87_capture_flip(_t87_wrapped_print, False)


def _t87_lying_len_flip():
    """The line of the injection written under a lying __len__ in its first run; the line of an
    unrecorded twin in the capture of a later run."""
    return _t87_capture_flip(_t87_lying_len_print, True)


def _t87_thread_twin_flip():
    """The line of the injection interleaved with a twin line a second thread wrote."""
    return _t87_capture_flip(_t87_thread_twin_print, False)


def _t87_settrace_twin_flip():
    """The line of the injection interleaved with a twin line from a thread paused under settrace."""
    return _t87_capture_flip(_t87_settrace_twin_print, False)


@contextlib.contextmanager
def _t87_declared_as(probe, model):
    """probe carries model's declarations, in _INTENDED_BACKSTOPS and in _SUBSTRING_BACKSTOPS."""
    with patch.dict(_INTENDED_BACKSTOPS, {probe: _INTENDED_BACKSTOPS[model]}), \
            patch.dict(_SUBSTRING_BACKSTOPS, {probe: _SUBSTRING_BACKSTOPS[model]}):
        yield


def _t87_legs(fx):
    """T87's legs, each (label, test, flip, setup, needle): needle names how a required-reject leg must
    fail, and None marks a required-accept control."""
    me = sys.modules[__name__]

    def create():
        recorded(record_cli(fx.env, fx.case("t87-create"), CREATE))

    def create_twice():
        first = record_cli(fx.env, fx.case("t87-first"), CREATE)
        second = record_cli(fx.env, fx.case("t87-second"), CREATE)
        recorded(first)
        recorded(second)

    def declared(probe):
        return lambda: _t87_declared_as(probe, _t86_declared_flip)
    backstop, harness = "unexpected-error backstop", "harness exception"
    legs = (("unrelated-eio", lambda: t77_report_every_failure(fx), _t87_unrelated_flip,
             lambda: _t87_declared_as(_t87_unrelated_flip, flip_t77), backstop),
            ("mimic-type", create, _t86_declared_flip,
             lambda: patch.object(me, "_t86_declared_crash", _t87_mimic_crash), backstop),
            ("twin-object", create, _t86_declared_flip,
             lambda: patch.object(me, "_t86_declared_crash", _t87_twin_crash), backstop),
            ("print-twin", create, _t87_print_twin_flip,
             lambda: _t87_declared_as(_t87_print_twin_flip, _t86_declared_flip), backstop),
            ("resolver-refusal", create, _t87_resolver_flip, contextlib.nullcontext, backstop),
            ("system-exit", create, _t87_exit_flip, contextlib.nullcontext, harness),
            ("private-capture", create_twice, _t87_forward_flip, declared(_t87_forward_flip), backstop),
            ("clear-then-twin", create_twice, _t87_clear_flip, declared(_t87_clear_flip), backstop),
            ("clear-reprint", create_twice, _t87_reprint_flip, declared(_t87_reprint_flip), backstop),
            ("copied-nested", create, _t87_nested_flip, declared(_t87_nested_flip), backstop),
            ("args-copy", create, _t86_declared_flip,
             lambda: patch.object(me, "_t86_declared_crash", _t87_args_copy_crash), backstop),
            ("formatted-unrelated", create, _t86_declared_flip,
             lambda: patch.object(me, "_t86_declared_crash", _t87_formatted_crash), backstop),
            ("child-message", create, _t87_child_flip, declared(_t87_child_flip), backstop),
            ("empty-write-nested-args-copy", create, _t87_empty_nested_flip,
             declared(_t87_empty_nested_flip), backstop),
            ("preamble-write-nested-args-copy", create, _t87_preamble_nested_flip,
             declared(_t87_preamble_nested_flip), backstop),
            ("no-write-nested-args-copy", create, _t87_unwritten_nested_flip,
             declared(_t87_unwritten_nested_flip), backstop),
            ("len-override", create_twice, _t87_lying_len_flip, declared(_t87_lying_len_flip), backstop),
            ("thread-interleaving", create, _t87_thread_twin_flip, declared(_t87_thread_twin_flip),
             backstop),
            ("settrace-pause", create, _t87_settrace_twin_flip, declared(_t87_settrace_twin_flip),
             backstop),
            ("print-wrapped", create_twice, _t87_wrapped_flip, declared(_t87_wrapped_flip), backstop),
            ("own-capture", create_twice, _t87_own_flip, declared(_t87_own_flip), None),
            ("truncate-alone", create_twice, _t87_truncate_flip, declared(_t87_truncate_flip), None),
            ("split-write", create, _t87_split_flip, declared(_t87_split_flip), None),
            ("writelines", create, _t87_writelines_flip, declared(_t87_writelines_flip), None))
    return legs + tuple((label, create, _t86_declared_flip, lambda rebuild=rebuild: patch.object(
        me, "_t86_declared_crash", _t87_replaced(rebuild)), backstop) for label, rebuild in _T87_REBUILDS)


def t87_backstop_bound_to_injection(fx):
    """A declared backstop is accepted only when, in the bytes the discriminating parent read from the
    pipe of the flipped child run, its line carries exactly one parent-minted, unconsumed token of a
    label the flip declares and is the shared backstop envelope around a repr EQUAL to one the parent
    computed for that injection before the child ran (private class and minted serial included).
    Rejected as a backstop: an unrelated EIO under the declarations of flip_t77, a RuntimeError
    carrying the text of the declared NameError, a twin of the recorded NameError raised in its place,
    and a twin raised by the print of the recorded NameError in the backstop (which the outer backstop
    then prints), each of whose lines carries no recorded token; a crash the record CLI reports as an
    unexpected-error refusal; an unrelated AttributeError formatted with the message of the injection
    and a new RuntimeError a child process printed from that message, whose lines carry the token but
    no parent-computed repr; the line of the injection forwarded only into a private capture, or
    cleared, followed by the line of an unrecorded twin (the twin stays unbound); the line of the
    injection printed, cleared and printed again (the second occurrence strays, its token consumed);
    the line of the injection written under a lying __len__ ahead of a twin in a later run, and the
    line of the injection interleaved with a twin line from a second thread, paused under sys.settrace
    or not (the pipe carries the true bytes, so the token-bearing bytes never form a complete bound
    line and the twin text binds nothing); a nested run whose unrecorded twin diagnostic is copied
    into the stderr of the run; an args copy of the injection rebuilt as the plain base type
    (NameError(*injected.args)) raised or printed in its place, alone, after an empty write, after a
    token-free preamble, or with no write from the backstop of the injection; and each object of
    _T87_REBUILDS raised in its place: a rebuild of its own private class from its args (deliberately,
    found among NameError's subclasses, or from a caught frame), a copy, deep copy or pickle round trip
    of it (each showing a fresh built serial, not the minted one), a same-named local class, a
    RuntimeError wrapping it, its repr, or a message formatted with its repr, and an ExceptionGroup
    holding it (each repr containing the injection's but not equal to it), and the injection's own
    backstop frame printing its diagnostic with the repr wrapped as a RuntimeError's (the frame and
    token are right, the repr is not the injection's). A SystemExit(0) from the
    flipped run fails as a harness exception, never escaping. Accepted, each being the own line of the
    injection object, token, private class and minted serial included, however its bytes reach the
    pipe: the line printed whole, written in two chunks, or through writelines; and a cleared capture
    with no twin (the red then being the assertion, nothing straying)."""
    me = sys.modules[__name__]
    for label, test, flip, setup, needle in _t87_legs(fx):
        outcome = None
        try:
            with setup():
                me._discriminate("t87-" + label, test, flip)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001  the verdict below reads which outcome was reached
            outcome = exc
        if needle is None:
            assert outcome is None, (
                "T87 a backstop carrying the declared injection's own exact repr discriminates",
                label, repr(outcome))
            continue
        assert isinstance(outcome, AssertionError) and needle in str(outcome), (
            "T87 a backstop or exception no declared injection caused never discriminates", label,
            repr(outcome))


def flip_t87_substring():
    """A discriminator matching each declared text as a substring of any backstop line."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_substring)


def _bound_injection_token_only(line, intact, injections, consumed, allowed):
    """The binding of _discriminate_token_only: the line, from an intact capture, carries exactly one
    recorded, unconsumed token whose label the flip declares; which object the backstop printed is
    never read."""
    tokens = set(_INJECTION_TOKEN.findall(line)) & injections.keys()
    if not intact or len(tokens) != 1:
        return None
    token, = tokens
    label, exc = injections[token]
    if label not in allowed or token in consumed:
        return None
    consumed.add(token)
    return exc


def _discriminate_token_only(name, test, flip):
    """A T87 flip: each backstop line binds by the token its text carries alone, so an unrecorded
    exception carrying the token (raised from the injection's args, an unrelated error formatted with
    its message, a child's new exception) is credited to the injection."""
    test()
    faults, injections, consumed = [], {}, set()
    _flip_watch.append(faults)
    _flip_injections.append(injections)
    try:
        with flip():
            test()
    except AssertionError:
        allowed = _INTENDED_BACKSTOPS.get(flip, ())
        stray = [line for line, printed in faults
                 if _bound_injection_token_only(line, printed is not None, injections, consumed,
                                                allowed) is None]
        if stray:
            raise AssertionError("{} went red through the unexpected-error backstop (exit 2, cannot "
                                 "evaluate), not an assertion: {}".format(name, stray[0])) from None
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001  a harness exception in the flipped run never discriminates
        raise AssertionError("{} went red through a harness exception ({!r}), not an "
                             "assertion".format(name, exc)) from None
    else:
        raise AssertionError(name + " survived its flip")
    finally:
        _flip_watch.pop()
        _flip_injections.pop()
        del faults[:]
        injections.clear()
    test()


def flip_t87_token_only():
    """A discriminator binding each backstop line by its token alone."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_token_only)


class _AnyWriteStderr(_EmissionStderr):
    """The capture of _discriminate_any_write: each write made while the nearest record CLI backstop
    frame holds a recorded injection as its `exc` adds that object to the printed set, by identity and
    once, whatever the write's text (a write of nothing or of a preamble included)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._printed = []

    def write(self, text):
        injections = _flip_injections[-1] if _flip_injections else None
        if isinstance(injections, dict) and injections:
            codes = (record.cli.__code__, opf._cmd_record.__code__)
            frame, printed = sys._getframe(1), None
            try:
                while frame is not None and frame.f_code not in codes:
                    frame = frame.f_back
                printed = None if frame is None else frame.f_locals.get("exc")
                if any(printed is exc for _label, exc in injections.values()) and \
                        not any(printed is seen for seen in self._printed):
                    self._printed.append(printed)
            finally:
                del frame, printed
        return super().write(text)

    def occurrences(self):
        return self.getvalue(), None if self._mutated else tuple(self._printed)


def _note_backstops_any_write(text, printed=()):
    """The note of _discriminate_any_write: each occurrence as (its line, its capture's whole printed
    set)."""
    if _flip_watch and text:
        _flip_watch[-1].extend((found.group(0), printed) for found in _BACKSTOP.finditer(text))


def _discriminate_any_write(name, test, flip, current=_discriminate_in_process):
    """A T87 flip: the _discriminate_in_process binding over _AnyWriteStderr, whose printed set takes
    in the injection a backstop frame holds on any write from that frame, read for every line of the
    capture, so a nested run's args copy of the injection printed after that frame wrote nothing, or a
    preamble, binds."""
    with patch.object(sys.modules[__name__], "_EmissionStderr", _AnyWriteStderr), \
            patch.object(sys.modules[__name__], "_note_backstops", _note_backstops_any_write):
        current(name, test, flip)


def flip_t87_any_write():
    """A discriminator counting an injection printed on any write its backstop frame made."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_any_write)


def flip_t87_in_process():
    """A discriminator binding each line by in-process attribution."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_in_process)


def flip_t87_plain_type():
    """A discriminator raising each injection as its plain base type."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_plain_type)


def flip_t87_repr_substring():
    """A discriminator binding a line when a serial-free computed repr occurs anywhere in it."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_repr_substring)


class _WriteCallStderr(_EmissionStderr):
    """The capture of _discriminate_write_call: each backstop line found within one write call is
    recorded on this instance as (its offset in the contents, the line, `exc` in the nearest calling
    frame that is record.cli or opf._cmd_record, else None); a mutated capture holds no emissions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._emissions = []

    def write(self, text):
        start = self.tell()
        written = super().write(text)
        if _flip_watch:
            codes = (record.cli.__code__, opf._cmd_record.__code__)
            frame = sys._getframe(1)
            try:
                while frame is not None and frame.f_code not in codes:
                    frame = frame.f_back
                printed = None if frame is None else frame.f_locals.get("exc")
                self._emissions.extend((start + found.start(), found.group(0), printed)
                                       for found in _BACKSTOP.finditer(text))
            finally:
                del frame
        return written

    def occurrences(self):
        emitted = {} if self._mutated else {(offset, line): printed
                                            for offset, line, printed in self._emissions}
        return self.getvalue(), emitted


def _note_backstops_write_call(text, emitted=None):
    """The note of _discriminate_write_call: each occurrence as (its line, the object its own capture
    recorded at its own offset for that line, else None)."""
    if _flip_watch and text:
        emitted = {} if emitted is None else emitted
        _flip_watch[-1].extend((found.group(0), emitted.get((found.start(), found.group(0))))
                               for found in _BACKSTOP.finditer(text))


def _bound_injection_write_call(printed, injections, allowed):
    """The binding of _discriminate_write_call: the printed object IS a recorded injection the flip
    declares; each once."""
    if printed is None:
        return None
    for index, (label, exc) in enumerate(injections):
        if label in allowed and printed is exc:
            del injections[index]
            return exc
    return None


def _discriminate_write_call(name, test, flip):
    """A T87 flip: each backstop line is bound to the exception the nearest record CLI frame held when
    the one write call containing the whole line ran, so a nested run's copied diagnostic binds to the
    outer injection and a line split across writes binds nothing."""
    test()
    faults, injections = [], []
    _flip_watch.append(faults)
    _flip_injections.append(injections)
    try:
        with patch.object(sys.modules[__name__], "_EmissionStderr", _WriteCallStderr), \
                patch.object(sys.modules[__name__], "_note_backstops", _note_backstops_write_call), flip():
            test()
    except AssertionError:
        allowed = _INTENDED_BACKSTOPS.get(flip, ())
        stray = [line for line, printed in faults
                 if _bound_injection_write_call(printed, injections, allowed) is None]
        if stray:
            raise AssertionError("{} went red through the unexpected-error backstop (exit 2, cannot "
                                 "evaluate), not an assertion: {}".format(name, stray[0])) from None
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001  a harness exception in the flipped run never discriminates
        raise AssertionError("{} went red through a harness exception ({!r}), not an "
                             "assertion".format(name, exc)) from None
    else:
        raise AssertionError(name + " survived its flip")
    finally:
        _flip_watch.pop()
        _flip_injections.pop()
        del faults[:], injections[:]
    test()


def flip_t87_write_call():
    """A discriminator binding each line to the frame of the one write call that held it."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_write_call)


# The emissions of _discriminate_runwide: every backstop emission any capture recorded in the flipped
# run, in one list per flip, whichever capture held it and whatever became of its line.
_runwide_emissions = []


class _RunwideStderr(_WriteCallStderr):
    """A capture that also adds each emission it records to the innermost _runwide_emissions list."""

    def write(self, text):
        recorded = len(self._emissions)
        written = super().write(text)
        if _runwide_emissions:
            _runwide_emissions[-1].extend((line, printed) for _offset, line, printed
                                         in self._emissions[recorded:])
        return written


def _bound_injection_runwide(line, emissions, injections, allowed):
    """The binding of _discriminate_runwide: the first emission of the same text anywhere in the
    flipped run, printed from a recorded injection the flip declares; each emission and injection
    consumed once."""
    for e_index, (emitted, printed) in enumerate(emissions):
        if emitted != line:
            continue
        for i_index, (label, exc) in enumerate(injections):
            if label in allowed and printed is exc:
                del emissions[e_index]
                del injections[i_index]
                return exc
    return None


def _discriminate_runwide(name, test, flip):
    """A T87 flip: each backstop line is matched by its text alone to an emission from the run-wide
    list, never to the capture or the occurrence it was read from."""
    test()
    faults, injections, emissions = [], [], []
    _flip_watch.append(faults)
    _flip_injections.append(injections)
    _runwide_emissions.append(emissions)
    try:
        with patch.object(sys.modules[__name__], "_EmissionStderr", _RunwideStderr), \
                patch.object(sys.modules[__name__], "_note_backstops", _note_backstops_write_call), flip():
            test()
    except AssertionError:
        allowed = _INTENDED_BACKSTOPS.get(flip, ())
        stray = [line for line, _printed in faults
                 if _bound_injection_runwide(line, emissions, injections, allowed) is None]
        if stray:
            raise AssertionError("{} went red through the unexpected-error backstop (exit 2, cannot "
                                 "evaluate), not an assertion: {}".format(name, stray[0])) from None
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001  a harness exception in the flipped run never discriminates
        raise AssertionError("{} went red through a harness exception ({!r}), not an "
                             "assertion".format(name, exc)) from None
    else:
        raise AssertionError(name + " survived its flip")
    finally:
        _flip_watch.pop()
        _flip_injections.pop()
        _runwide_emissions.pop()
        del faults[:], injections[:], emissions[:]
    test()


def flip_t87_runwide():
    """A discriminator matching each line by text to an emission from one run-wide list."""
    return patch.object(sys.modules[__name__], "_discriminate", _discriminate_runwide)


# --- T88: each injection a flip expects fired and was seen at its backstop, on every path --------------


def _t88_caught_crash(*_args):
    """The declared injection raised and caught at its own site, so no backstop prints it; the run then
    refuses as an ordinary held lease, and the test's own assertion turns red."""
    try:
        raise _injected("t86-declared", NameError("t86 broken flip fixture"))
    except NameError:
        pass
    raise guard.LeaseHeldError("t88 a held lease refuses after the caught injection")


def _t88_caught_then_raised(*_args):
    """The declared injection raised and caught once, then raised again: the second reaches the
    backstop and its line binds, the first is never seen."""
    try:
        raise _injected("t86-declared", NameError("t86 broken flip fixture"))
    except NameError:
        pass
    raise _injected("t86-declared", NameError("t86 broken flip fixture"))


# A broken T75 flip, applied inside both children: the examination lock replaced by a stand-in of the
# wrong arity, so recovery A crashes into the record backstop (exit 2) before it reaches the leftover
# unlink, and T75's first assertion turns red before its verdict reads either child's stderr.
FLIP_T88_WRONG_ARITY = """
import _opf_write_guard as _t75_guard
_t75_guard._exclusive_examination = lambda pfd: None
"""


@contextlib.contextmanager
def _t88_wrong_arity_flip():
    """The broken T75 flip as a context: while active, the T75 recovery children run with
    FLIP_T88_WRONG_ARITY."""
    _t75_flip[0] = FLIP_T88_WRONG_ARITY
    try:
        yield
    finally:
        _t75_flip[0] = ""


def _t88_legs(fx):
    """T88's legs, each (label, test, flip, setup, needle): needle names how a required-reject leg must
    fail, and None marks the required-accept control."""
    me = sys.modules[__name__]

    def create():
        recorded(record_cli(fx.env, fx.case("t88-create"), CREATE))

    def crash(stand_in):
        return lambda: patch.object(me, "_t86_declared_crash", stand_in)
    unseen, unfired = "fired without its backstop line in view", "never fired"
    return (("caught-injection", create, _t86_declared_flip, crash(_t88_caught_crash), unseen),
            ("caught-then-raised", create, _t86_declared_flip, crash(_t88_caught_then_raised), unseen),
            ("never-fired", create, _t86_declared_flip, crash(_t86_held), unfired),
            ("declared-seen", create, _t86_declared_flip, contextlib.nullcontext, None),
            ("t75-early-assertion", lambda: t75_concurrent_recovery(fx), _t88_wrong_arity_flip,
             contextlib.nullcontext, "unexpected-error backstop"))


def t88_expected_injection_seen(fx):
    """A flipped run that goes red through an assertion discriminates only when every injection its flip
    expects fired and each one that fired was seen at its backstop, whatever path the test took to its
    red. Rejected: the declared injection raised and caught at its own site, so no backstop prints it,
    before the run refuses as a held lease and an unrelated assertion turns red; the declared injection
    caught once and then raised to its backstop (the first one never seen); a declared flip whose run
    refuses before its injection is reached; and T75 under a broken flip whose stand-in examination lock
    has the wrong arity, so recovery A crashes into the record backstop and T75's first assertion turns
    red before its verdict reads, where A's backstop line must still reach the parent. Accepted: the
    declared injection reaching its own backstop."""
    me = sys.modules[__name__]
    for label, test, flip, setup, needle in _t88_legs(fx):
        outcome = None
        try:
            with setup():
                me._discriminate("t88-" + label, test, flip)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001  the verdict below reads which outcome was reached
            outcome = exc
        if needle is None:
            assert outcome is None, (
                "T88 an expected injection seen at its backstop discriminates", label, repr(outcome))
            continue
        assert isinstance(outcome, AssertionError) and needle in str(outcome), (
            "T88 an expected injection unseen, or never fired, never discriminates", label, repr(outcome))


def flip_t88_unchecked():
    """A discriminator that refuses only stray backstop lines: an expected injection that fired unseen, or
    never fired, before an unrelated assertion still counts as the flip's red."""
    return patch.object(sys.modules[__name__], "_unseen_injections", lambda fired, consumed, expected: [])


def _t75_collect_head(proc, collected, unwinding=False):
    """The earlier T75 collection: only the path that reaches T75's verdict reads, notes and forwards a
    child's stderr; unwinding kills a child still running and discards its output, and leaves a child
    that already exited unread."""
    if unwinding:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=60)
        return None
    output = proc.communicate(timeout=180)
    _note_backstops(output[1])
    _oop_forward(output[1])
    return output


def flip_t88_t75_unwinding():
    """T75's earlier collection, which drops each child's stderr when an assertion fails before the
    verdict reads it."""
    return patch.object(sys.modules[__name__], "_t75_collect", _t75_collect_head)


# The exact construction of each declared injection, one factory per declared variant of the label: the
# parent builds these itself, before the flipped child is launched, to compute every repr it will accept
# (_oop_declared); the child is never asked, and never believed, about what its injections looked like.
# Each factory mirrors its injection site byte for byte, and both sides go through _tokenize, so a
# divergence shows up as a stray backstop line, never as a silent accept.
_DECLARED_INJECTIONS = {
    "t18-requires": (lambda: KeyError("--decision"),),
    "t18-together": (lambda: KeyError("--decided-by"),),
    "t22-t81-string-claims": (lambda: TypeError("'>' not supported between instances of 'str' and 'int'"),),
    "t62-emit": (lambda: BrokenPipeError(32, "synthetic output failure after the lease release"),),
    "t64-stderr": (lambda: OSError(5, T64_STDERR),),
    "t64-emit": (lambda: BrokenPipeError(32, "synthetic emission failure"),),
    "t68-mark-copy": (lambda: MemoryError("synthetic allocation failure after the acquisition"),),
    "t69-release": (lambda: RuntimeError("fd release witness"),),
    "t69-token": (lambda: MemoryError("synthetic token allocation failure"),),
    "t77-emfile": (lambda: OSError(errno.EMFILE, "T77 injected: EMFILE on the retried claim"),),
    "t84-recursion": (lambda: RecursionError("maximum recursion depth exceeded"),),
    "t85-record-underivable": (lambda: ValueError("synthetic underivable record home"),),
    "t86-declared": (lambda: NameError("t86 broken flip fixture"),),
}

# The flips whose intended red is reached through a CLI backstop, each with the labels of the injections it
# expects there: under _discriminate every expected label must fire in the flipped run, and each injection of
# an expected label that fires must be the cause of a backstop line the parent read (_unseen_injections). A
# backstop line is accepted only when a recorded injection of an expected label is its cause: the line, in the
# bytes the discriminating parent read from the pipe itself, carries exactly one recorded, unconsumed token of
# an expected label and is the shared backstop envelope around exactly a repr the parent computed for that
# injection before the child ran (_oop_declared, _bound_injection_oop; under the T87 flip
# _discriminate_in_process: the in-process _injected, _EmissionStderr, _note_backstops and _bound_injection);
# any other backstop or exception in the flipped run fails the discrimination. The labels of T18 to T85 are
# each recorded by one test's injection site alone, so each of those declarations binds the test, the flip
# and the injection together (T69's two tests share one reverted _publish, so each has a flip of its own that
# expects its own test's injection, and so has the record kind's underivable leg of T85), except
# t22-t81-string-claims, which T22 and T81 record at the one site their shared string-claims flip lets a
# string claims value reach (both tests reach it, so that one declaration fits both). t86-declared is
# recorded at several sites: the T86 declared crash, the
# T87 and T88 stand-ins patched over that crash, and the record parse that T87's child and capture flips
# replace (_t87_child_parse, _t87_capture_flip); each leg declares it through _t86_declared_flip's entry (used
# as it is, or copied to the leg's own flip by _t87_declared_as). Most label the synthetic failure their test
# injects, which the reverted handling lets reach the backstop; the T18 flips drop an option guard and record
# the crash the planner then meets reading the missing option; T64's emission leg reaches the backstop in every
# run, so its recovery flip meets it too; the T84 flip restores a lease parse that lets the RecursionError of a
# deeply nested payload reach the backstop, recorded where it leaves _closed_lease_model; the string-claims
# flip of T22 and T81 admits a string claims value, and the TypeError the scan's high-water comparison then
# raises is recorded where it leaves _opf_journal._kind_reservation_high; and the T85 record-kind
# derivation flip ends the scan empty, so the leg's own ValueError is raised again at the publication
# boundary's reservation read, recorded where it leaves _read_reservation.
_INTENDED_BACKSTOPS = {
    flip_t18_requires: ("t18-requires",),
    flip_t18_together: ("t18-together",),
    flip_string_claims_admitted: ("t22-t81-string-claims",),
    flip_t62: ("t62-emit",),
    flip_t64_conclude: ("t64-stderr",),
    flip_t64_recovery: ("t64-emit",),
    flip_t68: ("t68-mark-copy",),
    flip_t69_release: ("t69-release",),
    flip_t69_token: ("t69-token",),
    flip_t77: ("t77-emfile",),
    flip_t84: ("t84-recursion",),
    flip_t85_derive_record: ("t85-record-underivable",),
    _t86_declared_flip: ("t86-declared",),
}


# --- T89: a store an earlier emitter wrote with a literal U+2028 and U+2029 ---------------------------------

T89_SUMMARY = "one" + chr(0x2028) + "two" + chr(0x2029) + "three"
T89_LITERALS = (chr(0x2028).encode("utf-8"), chr(0x2029).encode("utf-8"))


def _t89_prior_spelling(data):
    """The bytes an emitter before the U+2028/U+2029 escape wrote for the same content: each escape as the
    literal character (exact here, since the fixture holds no other backslash-u text)."""
    return data.replace(b"\\u2028", T89_LITERALS[0]).replace(b"\\u2029", T89_LITERALS[1])


def _t89_store(fx, name):
    """A doctor-VALID store whose backlog index holds BI-1 with a separator-bearing summary in the earlier
    spelling, its views rendered over those bytes, everything committed."""
    env = fx.env
    root = fx.case(name)
    step(fx, root, CREATE + ["--summary", T89_SUMMARY], "a separator-bearing record")
    written = read(root, BI_INDEX)
    if _t89_prior_spelling(written) != written:     # an emitter that escapes nothing already wrote them literally
        write_commit(env, root, BI_INDEX, _t89_prior_spelling(written), "the earlier spelling")
    rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
    assert rc == 0, ("T89 render over the earlier spelling", rc, err[-800:])
    if env.git(root, "status", "--porcelain", "--", ".working").strip():
        fx.commit_all(root, "views over the earlier spelling")
    held = read(root, BI_INDEX)
    assert all(lit in held for lit in T89_LITERALS) and b"\\u2028" not in held, (
        "T89 the fixture index holds the literal characters", held[-400:])
    doctor_valid(env, root)
    return root


def t89_prior_separator_spelling(fx):
    env = fx.env
    root = _t89_store(fx, "t89-respelled")
    kept = row(root, "BI-1")
    assert kept["summary"] == T89_SUMMARY, kept
    recorded(record_cli(env, root, CREATE))
    after = read(root, BI_INDEX)
    assert not any(lit in after for lit in T89_LITERALS), ("T89 the write re-spells the file", after[-400:])
    assert b'summary = "one\\u2028two\\u2029three"' in after, ("T89 the escapes are written", after[-400:])
    assert row(root, "BI-1") == kept, "T89 the existing record reads back unchanged"
    assert [r["id"] for r in model(root, BI_INDEX)["record"]] == ["BI-1", "BI-2"], (
        "T89 the new record is appended")
    doctor_valid(env, root)


def t89_bounded_acceptance(fx):
    env = fx.env
    base = _t89_store(fx, "t89-base")
    prior = read(base, BI_INDEX)
    kept = model(base, BI_INDEX)
    for name, data in (
            ("comment", b"# a note" + T89_LITERALS[0] + b"\n" + prior),
            ("literal-string",
             prior.replace(b'summary = "one', b"summary = 'one").replace(b'three"', b"three'")),
            ("extra-space", prior.replace(b"summary = ", b"summary  = "))):
        root = fx.case("t89-" + name, source=base)
        write_commit(env, root, BI_INDEX, data, "hand edit: " + name)
        assert read(root, BI_INDEX) != prior and model(root, BI_INDEX) == kept, (
            "T89 the edited index reads back as the same model", name)
        refused_untouched(env, root, CREATE, "not in canonical new-document form")


def flip_t89_no_escape():
    return patch.object(emit, "_LINE_SEPARATORS", frozenset())


def flip_t89_strict():
    return patch.object(emit, "matches_store_bytes", lambda raw, fresh: raw == fresh)


def flip_t89_any_bytes():
    return patch.object(emit, "matches_store_bytes", lambda raw, fresh: True)


# --- T90: the store-read cap over every file a record operation would publish -------------------------------
# Re-spelling grows each literal separator from three bytes to six and the appended row adds its whole
# body, so a legacy index near the cap could otherwise be rewritten into one the resolver and doctor then
# refuse. The operation sizes every emitted operand against the cap and refuses BEFORE publication.

T90_SUMMARY = "s" * 16000 + chr(0x2028) + "t" + chr(0x2029) + "u"


def t90_reader_cap(fx):
    env = fx.env
    root = fx.case("t90-cap")
    step(fx, root, CREATE + ["--summary", T90_SUMMARY], "a large separator-bearing record")
    written = read(root, BI_INDEX)
    if _t89_prior_spelling(written) != written:
        write_commit(env, root, BI_INDEX, _t89_prior_spelling(written), "the earlier spelling")
    rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
    assert rc == 0, ("T90 render over the earlier spelling", rc, err[-800:])
    if env.git(root, "status", "--porcelain", "--", ".working").strip():
        fx.commit_all(root, "views over the earlier spelling")
    doctor_valid(env, root)
    held = read(root, BI_INDEX)
    assert all(lit in held for lit in T89_LITERALS), ("T90 the fixture holds the literals", held[-200:])
    # The patched cap must admit every capped pre-publication read (the resolver reads the manifest
    # through it) while the re-spelled index plus the appended record exceeds it.
    manifest_size = len(read(root, MACH + "/manifest.toml"))
    assert manifest_size <= len(held), ("T90 the capped resolver read must fit", manifest_size, len(held))
    with patch.object(record._opf_store, "MAX_STORE_READ_BYTES", len(held) + 8):
        refused_untouched(env, root, CREATE, "store-read cap")
    # The boundary matches the reader's own comparison (only a LARGER file is refused): just inside
    # and exactly at the cap are admitted, one byte over refuses.
    with patch.object(record._opf_store, "MAX_STORE_READ_BYTES", 16):
        record._require_within_read_cap("x", b"a" * 15)
        record._require_within_read_cap("x", b"a" * 16)
        try:
            record._require_within_read_cap("x", b"a" * 17)
            raise AssertionError("T90 one byte over the cap must refuse")
        except record.RecordError as exc:
            assert "store-read cap" in str(exc) and "17 bytes" in str(exc), exc
    # Boundary control: with room for the growth under the same patch seam, the create records, the
    # legacy index is re-spelled, and the store stays doctor-VALID.
    with patch.object(record._opf_store, "MAX_STORE_READ_BYTES", len(held) + (1 << 16)):
        recorded(record_cli(env, root, CREATE))
    after = read(root, BI_INDEX)
    assert not any(lit in after for lit in T89_LITERALS), ("T90 the admitted write re-spells", after[-200:])
    assert [r["id"] for r in model(root, BI_INDEX)["record"]] == ["BI-1", "BI-2"], (
        "T90 the new record is appended")
    doctor_valid(env, root)


def flip_t90_no_cap():
    return patch.object(record, "_require_within_read_cap", lambda rel, data: None)


# --- T95: importer-authored history is never a clean-series operand (spec 8.6, 8.8) -------------------------

def _importer_item(fx, name, status):
    """A legacy importer-authored backlog item seated in the clean index with its counter (the T89 seeding),
    then the views rendered and committed, so the store is doctor VALID before the operation runs."""
    env = fx.env
    root = fx.case(name)
    legacy = {"id": "BI-1", "type": "backlog_item", "status": status, "title": "migrated",
              "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
              "actor": {"kind": "importer"}}
    index = model(root, BI_INDEX)
    index["record"] = index["record"] + [legacy]
    write_commit(env, root, BI_INDEX, emit.emit_checked(index).encode("utf-8"), "a legacy importer item")
    counters = model(root, COUNTERS)
    counters["counters"]["BI"] += 1
    write_commit(env, root, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    rc, out, err = cli(env, ["render", "--root", str(root), "--write"])
    assert rc == 0, ("T95 the views render over the history", rc, err[-800:])
    fx.commit_all(root, "views")
    doctor_valid(env, root)
    return root, legacy


def t95_importer_operand_transition(fx):
    """A transition on a legacy importer-authored item refuses whoever acts, with every byte untouched; acting
    on that history is a new clean record linking back, which records and leaves the history's bytes alone."""
    env = fx.env
    with ticking():
        root, legacy = _importer_item(fx, "t95-importer-operand-transition", "open")
        refused_untouched(env, root, ["transition", "BI-1", "active"] + MAINTAINER, "importer-authored-operand")
        refused_untouched(env, root, ["transition", "BI-1", "dropped"] + ASSISTANT, "importer-authored-operand")
        step(fx, root, ["create", "--type", "backlog_item", "--title", "act on it", "--link", "derives_from=BI-1"]
             + MAINTAINER, "BI-2 acting on history")
        assert row(root, "BI-1") == legacy, ("T95 the history keeps its bytes", row(root, "BI-1"))
        assert row(root, "BI-2")["links"] == [{"rel": "derives_from", "id": "BI-1"}], row(root, "BI-2")
        doctor_valid(env, root)


def t95_importer_operand_receipt(fx):
    """done-with-receipt on a legacy importer-authored active item refuses with every byte untouched."""
    with ticking():
        root, _legacy = _importer_item(fx, "t95-importer-operand-receipt", "active")
        refused_untouched(fx.env, root, ["done-with-receipt", "BI-1"] + MAINTAINER, "importer-authored-operand")


def t95_importer_resolution_superseded(fx):
    """A --supersedes target is read and linked, never the operand: a clean decide superseding a legacy
    importer-authored resolution (a doctor finding at rest, spec 8.6) records with the target's bytes
    unchanged and leaves the store doctor VALID."""
    env = fx.env
    root = fx.case("t95-importer-resolution-superseded")
    ts = "2026-08-01T00:00:00Z"
    held = {"id": "PD-1", "type": "pending_decision", "status": "decided", "title": "migrated",
            "created_at": ts, "updated_at": ts, "actor": {"kind": "importer"}, "decision": "use X",
            "decided_at": ts, "decided_by": "the board"}
    fresh = {"id": "PD-2", "type": "pending_decision", "status": "open", "title": "resolve it now",
             "created_at": ts, "updated_at": ts, "actor": {"kind": "maintainer"}}
    index = model(root, PD_INDEX)
    index["record"] = index["record"] + [held, fresh]
    write_commit(env, root, PD_INDEX, emit.emit_checked(index).encode("utf-8"), "an importer resolution")
    counters = model(root, COUNTERS)
    counters["counters"]["PD"] += 2
    write_commit(env, root, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    rc, out, err = cli(env, ["doctor", "--root", str(root)])
    assert rc != 0 and "importer-authored pending_decision 'PD-1'" in out, (
        "T95 the importer resolution is a doctor finding at rest", rc, out[-1600:])
    with ticking():
        step(fx, root, ["transition", "PD-2", "decided", "--decision", "use Y", "--decided-by", "the board",
                        "--supersedes", "PD-1"] + MAINTAINER, "PD-2 supersedes the importer resolution")
    assert row(root, "PD-1", PD_INDEX) == held, ("T95 the target keeps its bytes", row(root, "PD-1", PD_INDEX))
    assert row(root, "PD-2", PD_INDEX)["links"] == [{"rel": "supersedes", "id": "PD-1"}], (
        row(root, "PD-2", PD_INDEX))
    doctor_valid(env, root)


def flip_t95_author():
    """Drop the operand's author check (the reviewed head's behaviour): each transition and the receipt on
    importer-authored history publish, so the byte comparison turns each refusal red."""
    return patch.object(record, "_require_clean_author", lambda row, rid, rel, verb: None)


def flip_t95_target():
    """Apply the author check to the --supersedes target too (the check folded into _require_valid_current
    or _require_valid_target): the clean decide that resolves an importer-authored chain then refuses, so
    the remedy's recorded step turns red."""
    original = record._require_valid_target

    def also_author(trow, ctx, target, rel):
        original(trow, ctx, target, rel)
        record._require_clean_author(trow, target, rel, "transition")
    return patch.object(record, "_require_valid_target", also_author)


# --- T96-T98: the held-lease refusal over a malformed, a nested and a non-regular lease (spec 5.7) ----------
# The record verb runs the check_clean lease exclusion and the acquire_lease claim that the removed
# store-upgrade gate used to exercise, so each is driven here.

T96_MALFORMED = b"not valid = = = toml\n"
T98_BOUND = 30


def t96_malformed_lease(fx):
    """A malformed lease is present, so it is held, never absent: the create refuses exit 2 with the
    held-lease refusal, the lease is not deleted and every byte is untouched."""
    root = fx.case("t96-malformed-lease")
    (Path(root) / LEASE).write_bytes(T96_MALFORMED)
    before = snapshot(root)
    result = record_cli(fx.env, root, CREATE)
    refused(result, "never seized")
    assert "a present lease with an unreadable payload" in result[2], result[2][-800:]
    assert read(root, LEASE) == T96_MALFORMED, "T96 the malformed lease is not deleted"
    assert snapshot(root) == before, "T96 every byte untouched"


def flip_t96():
    """An acquisition that reads a lease it cannot parse as absent: it removes that lease, then claims."""
    original = guard.acquire_lease

    def absent_if_malformed(root_fd, machine_rel, verb):
        rel = "{}/{}".format(machine_rel, opf_check.LEASE_NAME)
        with contextlib.suppress(FileNotFoundError):
            with open(os.open(rel, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root_fd), "rb") as handle:
                raw = handle.read()
            try:
                tomllib.loads(raw.decode("utf-8"))
            except ValueError:
                os.unlink(rel, dir_fd=root_fd)
        return original(root_fd, machine_rel, verb)
    return patch.object(guard, "acquire_lease", absent_if_malformed)


def t97_nested_store_held_lease(fx):
    """A store root BELOW its git repository root, with a foreign held lease, reaches the held-lease
    refusal naming the holder, never the dirty-store refusal: the porcelain paths are repository-root-
    relative, so the lease is excluded only once the store's prefix is stripped. The lease survives and
    every byte is untouched."""
    env = fx.env
    outer = fx.case("t97-nested")
    store = outer / "product" / "store"
    store.mkdir(parents=True)
    for entry in sorted(outer.iterdir()):
        if entry.name not in (".git", "product"):
            entry.rename(store / entry.name)
    env.git(outer, "add", "-A")
    env.git(outer, "commit", "-q", "-m", "nest the store below the repository root")
    assert env.git(store, "rev-parse", "--show-prefix") == "product/store/\n", "T97 the store is nested"
    (store / LEASE).write_bytes(PEER_LEASE)   # untracked, as a live peer leaves it
    before = snapshot(outer)
    result = record_cli(env, store, CREATE)
    refused(result, "never seized")
    assert "peer-runner" in result[2] and "the working tree is not clean" not in result[2], result[2][-800:]
    assert read(store, LEASE) == PEER_LEASE and snapshot(outer) == before, "T97 the peer lease survives"


def flip_t97():
    """The un-normalized lease exclusion: each porcelain path is compared without the store's prefix strip."""
    original = guard.parse_porcelain
    return patch.object(guard, "parse_porcelain",
                        lambda raw, prefix, lease, specs, verb: original(raw, b"", lease, specs, verb))


def t98_fifo_lease(fx):
    """A FIFO at the lease path refuses promptly: the held-lease refusal names a present non-regular lease
    without reading it. The create runs in a child bounded by T98_BOUND, so a blocking open turns this
    test red rather than hanging the gate. The FIFO is untouched and every byte is unchanged."""
    if not hasattr(os, "mkfifo"):
        raise CannotEvaluate("T98 needs os.mkfifo to plant a FIFO lease")
    root = fx.case("t98-fifo-lease")
    os.mkfifo(str(Path(root) / LEASE))
    before = snapshot(root)
    try:
        proc = child(fx.env, root, CREATE, flip=_t98_flip[0], timeout=T98_BOUND)
    except subprocess.TimeoutExpired:
        raise AssertionError("T98 the FIFO lease held the run past {} s, never refused".format(T98_BOUND))
    assert proc.returncode == 2, ("T98 exit 2", proc.returncode, proc.stderr[-800:])
    assert "a present non-regular lease (held)" in proc.stderr and "never seized" in proc.stderr, (
        proc.stderr[-800:])
    assert stat.S_ISFIFO((Path(root) / LEASE).lstat().st_mode) and snapshot(root) == before, (
        "T98 the FIFO is untouched")


# The T98 flip, applied inside the bounded child: the held-lease message opens the lease without
# O_NONBLOCK, so its open of a writer-less FIFO blocks. The wrapper forwards dir_fd, so it is listed in
# os.supports_dir_fd, as the containment probe requires of os.open. _t98_flip holds the child's prelude.
_t98_flip = [""]
FLIP_T98 = """
_open = os.open
def _blocking_open(path, flags, *args, **kwargs):
    if path == "lease.toml" and not flags & os.O_CREAT:
        flags &= ~os.O_NONBLOCK
    return _open(path, flags, *args, **kwargs)
os.open = _blocking_open
os.supports_dir_fd.add(_blocking_open)
"""


@contextlib.contextmanager
def flip_t98():
    _t98_flip[0] = FLIP_T98
    try:
        yield
    finally:
        _t98_flip[0] = ""


# --- T99-T109: the planned-destination cleanliness, ignore and lease guards before mutation ----------------
# The record verb runs check_clean and check_ignored before its lease, the cleanliness, ignore and lease
# guards (and the normalizing-filter refusal note) that the removed store-upgrade gate used to exercise,
# so each is driven here through `opf record create`.

CONTRIBUTIONS_VIEW = ".working/CONTRIBUTIONS.md"
VERSION_VIEW = ".working/VERSION.md"
OWNER_BYTES = b"the owner's own notes\n"
T105_PREFIX = b"nested-\xff/"


def _exclude(root, *rules):
    """Replace the repository's .git/info/exclude with `rules` (bytes), one per line."""
    info = Path(root) / ".git" / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_bytes(b"".join(rule + b"\n" for rule in rules))


def _untrack(env, root, *rels):
    """Remove `rels` from the index, keeping the files, and commit, so HEAD no longer holds them."""
    env.git(root, "rm", "-q", "-r", "--cached", "--", *rels)
    env.git(root, "commit", "-q", "-m", "untrack " + " ".join(rels))


def _with_manifest(fx, root, **tables):
    """Commit the manifest with each named top-level table replaced, emitted canonically."""
    document = model(root, MANIFEST)
    document.update(tables)
    write_commit(fx.env, root, MANIFEST, emit.emit_checked(document).encode("utf-8"), "manifest tables")


def _git_bytes(root, args, run_git=guard._opf_observe._run_git):
    """A git observation's raw stdout, through the production bytes boundary (paths need not be UTF-8),
    bound at import so a flip that patches the probe's git leaves the fixture's own checks alone."""
    out = run_git(guard._opf_observe._git_path(), str(root), args)
    if not out.completed or out.rc != 0:
        raise Harness("fixture git {} failed: {}".format(args, out.err))
    return out.out


def _index_and_head(root):
    return _git_bytes(root, ["ls-files", "--stage", "-z"]), _git_bytes(root, ["rev-parse", "HEAD"])


def _status_ignored(root, rel):
    return _git_bytes(root, ["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                             "--ignored=matching", "--no-renames", "--", rel]).split(b"\x00")


def _create_refused(env, root, needle):
    """refused_untouched over CREATE, returning the refusal's stderr: the byte comparison is asserted
    first, so a guard removed under a flip turns the test red on what was written."""
    before = snapshot(root)
    result = record_cli(env, root, CREATE)
    assert snapshot(root) == before, ("bytes untouched on refusal", result[0], result[2][-800:])
    refused(result, needle)
    return result[2]


def t99_dirty_operand(fx):
    """A canonical, uncommitted counters edit at a planned rewrite destination refuses exit 2 BEFORE any
    mutation: the refusal names the path and advises committing or moving the work aside, offering no
    restore command (the dirt is the owner's), and every byte is untouched."""
    env = fx.env
    root = fx.case("t99-dirty-operand")
    (Path(root) / COUNTERS).write_bytes(read(root, COUNTERS).replace(b"BI = 0", b"BI = 1"))
    state = _index_and_head(root)
    err = _create_refused(env, root, "Commit your changes (or move them aside)")
    assert COUNTERS in err and "the working tree is not clean" in err, err[-800:]
    assert "restore --staged" not in err, err[-800:]
    assert _index_and_head(root) == state, "T99 index and HEAD unchanged"


def flip_t99():
    """The cleanliness gate as a no-op."""
    return patch.object(guard, "check_clean", lambda *args: None)


def t100_ignored_render_target(fx):
    """An ignored, untracked file at a declared view's destination is the owner's work, not a clean
    destination: the create refuses exit 2 before mutation with the dirty-store remedy naming it, every
    byte and the index untouched. Moved aside, the absent destination still refuses its ignore rule; with
    the rule corrected the create records, and the saved owner bytes and the ignored content outside the
    planned scope survive."""
    env = fx.env
    root = fx.case("t100-ignored-render-target")
    _untrack(env, root, CONTRIBUTIONS_VIEW)
    (Path(root) / CONTRIBUTIONS_VIEW).write_bytes(OWNER_BYTES)
    (Path(root) / "outside-build").mkdir()
    (Path(root) / "outside-build" / "cache").write_bytes(b"unrelated ignored content\n")
    _exclude(root, b"/" + CONTRIBUTIONS_VIEW.encode("utf-8"), b"/outside-build/")
    assert env.git(root, "check-ignore", "--", CONTRIBUTIONS_VIEW).strip() == CONTRIBUTIONS_VIEW, (
        "T100 the fixture target is ignored")
    state = _index_and_head(root)
    err = _create_refused(env, root, "Commit your changes (or move them aside)")
    assert CONTRIBUTIONS_VIEW in err and "restore --staged" not in err, err[-800:]
    assert _index_and_head(root) == state, "T100 index and HEAD unchanged"
    saved = Path(root).parent / (Path(root).name + "-owner-notes")
    (Path(root) / CONTRIBUTIONS_VIEW).rename(saved)
    _create_refused(env, root, "ignored planned destinations")
    _exclude(root, b"/outside-build/")
    recorded(record_cli(env, root, CREATE))
    assert saved.read_bytes() == OWNER_BYTES, "T100 the saved owner bytes survive"
    assert read(root, "outside-build/cache") == b"unrelated ignored content\n", (
        "T100 ignored content outside the planned scope is untouched")


def flip_unignored_probe():
    """The dirty probe without --ignored=matching (and its expansion): status then reports no ignored entry."""
    original = guard._opf_observe._run_git

    def run_git(git, root, args, **kwargs):
        return original(git, root, [a for a in args if a not in ("--ignored=matching", "--ignored=traditional")],
                        **kwargs)
    return patch.object(guard._opf_observe, "_run_git", run_git)


def t101_collapsed_ignored_ancestor(fx):
    """A collapsed ignored ancestor (`!! .working/`, the whole store untracked and ignored) does not hide
    the planned destinations beneath it: the expansion over the same destinations names them, and the
    create refuses exit 2 before mutation with the dirty-store remedy, every byte untouched."""
    env = fx.env
    root = fx.case("t101-collapsed-ancestor")
    _untrack(env, root, ".working")
    _exclude(root, b"/.working/")
    assert b"!! .working/" in _status_ignored(root, CONTRIBUTIONS_VIEW), "T101 git collapses the ancestor"
    err = _create_refused(env, root, "Commit your changes (or move them aside)")
    assert "16 dirty path(s)" in err and CONTRIBUTIONS_VIEW in err, (
        "T101 the refusal counts every planned destination and names them", err[-800:])


def flip_t101():
    """No ignored-ancestor expansion: the traditional-mode status reports nothing."""
    original = guard._opf_observe._run_git

    def run_git(git, root, args, **kwargs):
        if "--ignored=traditional" in args:
            return guard._opf_observe._GitOutcome(True, 0, b"", "")
        return original(git, root, args, **kwargs)
    return patch.object(guard._opf_observe, "_run_git", run_git)


def t102_directory_at_destination(fx):
    """An ignored directory at a declared view's destination is a collision, though git reports it with a
    trailing slash: the create refuses exit 2 before mutation naming it, every byte untouched."""
    env = fx.env
    root = fx.case("t102-directory-at-destination")
    _untrack(env, root, CONTRIBUTIONS_VIEW)
    (Path(root) / CONTRIBUTIONS_VIEW).unlink()
    (Path(root) / CONTRIBUTIONS_VIEW).mkdir()
    (Path(root) / CONTRIBUTIONS_VIEW / "owner").write_bytes(OWNER_BYTES)
    _exclude(root, b"/" + CONTRIBUTIONS_VIEW.encode("utf-8") + b"/")
    assert b"!! " + CONTRIBUTIONS_VIEW.encode("utf-8") + b"/" in _status_ignored(root, CONTRIBUTIONS_VIEW), (
        "T102 git reports the directory with a trailing slash")
    err = _create_refused(env, root, "Commit your changes (or move them aside)")
    assert CONTRIBUTIONS_VIEW in err, ("T102 the refusal names the destination", err[-800:])


def t103_ignored_prefix_sibling(fx):
    """Ignored content at a declared unmanaged prefix sibling of a destination (.working/CONTRIBUTIONS.md.bak/,
    which --ignored=matching reports even under literal pathspecs) is outside the planned scope: the
    create records and the sibling's bytes survive."""
    env = fx.env
    root = fx.case("t103-ignored-prefix-sibling")
    sibling = CONTRIBUTIONS_VIEW + ".bak"
    _with_manifest(fx, root, unmanaged={"paths": [sibling]})
    _exclude(root, b"/" + sibling.encode("utf-8") + b"/")
    (Path(root) / sibling).mkdir()
    (Path(root) / sibling / "owner").write_bytes(OWNER_BYTES)
    assert b"!! " + sibling.encode("utf-8") + b"/" in _status_ignored(root, CONTRIBUTIONS_VIEW), (
        "T103 git reports the prefix sibling")
    recorded(record_cli(env, root, CREATE))
    assert read(root, sibling + "/owner") == OWNER_BYTES, "T103 the sibling's bytes survive"


def flip_t103():
    """No ignored-record scope filter: every ignored record git reports is admitted as dirt."""
    original = guard.parse_porcelain

    def parse(raw, prefix, lease, specs, verb):
        extra = [os.fsdecode(r[3:]).removeprefix(os.fsdecode(prefix)).rstrip("/")
                 for r in raw.split(b"\x00") if r.startswith(b"!! ")]
        return original(raw, prefix, lease, list(specs) + extra, verb)
    return patch.object(guard, "parse_porcelain", parse)


def t104_unmanaged_ignored_content(fx):
    """Declared unmanaged, ignored content inside .working (.working/cache/) is outside the planned scope:
    the create records and its bytes survive."""
    env = fx.env
    root = fx.case("t104-unmanaged-ignored")
    _with_manifest(fx, root, unmanaged={"paths": [".working/cache"]})
    _exclude(root, b"/.working/cache/")
    (Path(root) / ".working" / "cache").mkdir()
    (Path(root) / ".working" / "cache" / "output.bin").write_bytes(b"unmanaged output\n")
    recorded(record_cli(env, root, CREATE))
    assert read(root, ".working/cache/output.bin") == b"unmanaged output\n", "T104 the unmanaged bytes survive"


def flip_t104():
    """The whole-store scope: the store's planned destinations widened to all of .working."""
    original = guard.plan_write_scope

    def scope(*args):
        return dict(original(*args), store=(".working",))
    return patch.object(guard, "plan_write_scope", scope)


def t105_non_utf8_prefix(fx):
    """A store nested below its repository root under a real non-UTF-8 directory name: git's -z paths and
    --show-prefix carry those bytes. An ignored, untracked operand (counters), view destination, or
    collapsed .working ancestor refuses exit 2 before mutation with the dirty-store remedy naming the
    destination; every byte, the index and HEAD untouched."""
    env = fx.env
    for case, rel, untrack in (("counters", COUNTERS, COUNTERS), ("render", CONTRIBUTIONS_VIEW, CONTRIBUTIONS_VIEW),
                               ("ancestor", CONTRIBUTIONS_VIEW, ".working")):
        outer = fx.case("t105-" + case)
        nested = outer / os.fsdecode(T105_PREFIX.rstrip(b"/"))
        nested.mkdir()
        for entry in sorted(outer.iterdir()):
            if entry.name not in (".git", nested.name):
                entry.rename(nested / entry.name)
        env.git(outer, "add", "-A")
        env.git(outer, "commit", "-q", "-m", "nest the store below a non-UTF-8 directory")
        _untrack(env, nested, untrack)
        if case == "render":
            (nested / rel).write_bytes(OWNER_BYTES)
        ignored = b".working/" if case == "ancestor" else rel.encode("utf-8")
        _exclude(outer, b"/" + T105_PREFIX + ignored)
        assert _git_bytes(nested, ["rev-parse", "--show-prefix"]) == T105_PREFIX + b"\n", (
            "T105 a real non-UTF-8 prefix", case)
        assert b"!! " + T105_PREFIX + ignored in _status_ignored(nested, rel), ("T105 a real ignored record", case)
        state = _index_and_head(outer)
        before = snapshot(outer)
        result = record_cli(env, nested, CREATE)
        assert snapshot(outer) == before, ("T105 bytes untouched on refusal", case, result[0], result[2][-800:])
        refused(result, "Commit your changes (or move them aside)")
        assert rel in result[2], (case, result[2][-800:])
        assert _index_and_head(outer) == state, ("T105 index and HEAD unchanged", case)


def flip_t105():
    """The lossy prefix round trip and its silent non-match fallback."""
    def old_status_path(pbytes, prefix):
        prefix_b = os.fsencode(prefix).decode("utf-8", "replace").encode("utf-8")
        return pbytes[len(prefix_b):] if prefix_b and pbytes.startswith(prefix_b) else pbytes
    return patch.object(guard, "status_path", old_status_path)


def t106_absent_ignored_destination(fx):
    """An absent planned destination has no status entry, so its ignore rule is probed: an absent ignored
    view destination, and an absent ignored product destination (the root VERSION view), each refuse exit
    2 before mutation naming the path, every byte untouched."""
    env = fx.env
    root = fx.case("t106-absent-view")
    _untrack(env, root, CONTRIBUTIONS_VIEW)
    (Path(root) / CONTRIBUTIONS_VIEW).unlink()
    _exclude(root, b"/" + CONTRIBUTIONS_VIEW.encode("utf-8"))
    err = _create_refused(env, root, "ignored planned destinations")
    assert repr(CONTRIBUTIONS_VIEW) in err, ("T106 the view destination is named", err[-800:])
    root = fx.case("t106-absent-product")
    views = dict(model(root, MANIFEST)["views"],
                 VERSION={"kind": "deterministic", "sources": ["version"], "target": "VERSION"})
    _with_manifest(fx, root, views=views)
    _exclude(root, b"/VERSION")
    err = _create_refused(env, root, "ignored planned destinations")
    assert "'VERSION'" in err, ("T106 the product destination is named", err[-800:])


def flip_t106():
    """The ignore probe for absent destinations as a no-op."""
    return patch.object(guard, "check_ignored", lambda *args: None)


def t107_ignored_and_directory_lease(fx):
    """An ignored lease, and a directory lease (untracked, or ignored and so collapsed), are held: each
    create refuses exit 2 with the never-seize refusal, never the dirty-store remedy, the lease and every
    byte untouched."""
    env = fx.env
    root = fx.case("t107-ignored-lease")
    (Path(root) / LEASE).write_bytes(PEER_LEASE)
    _exclude(root, b"/" + LEASE.encode("utf-8"))
    assert env.git(root, "check-ignore", "--", LEASE).strip() == LEASE, "T107 the fixture lease is ignored"
    err = _create_refused(env, root, "The lease is never seized (spec 5.7)")
    assert "peer-runner" in err and "confirmed NO opf run is live" in err, err[-800:]
    assert "Commit your changes" not in err, err[-800:]
    assert read(root, LEASE) == PEER_LEASE, "T107 the ignored lease survives"
    for ignored in (False, True):
        root = fx.case("t107-directory-lease-" + ("ignored" if ignored else "untracked"))
        (Path(root) / LEASE).mkdir()
        (Path(root) / LEASE / "owner").write_bytes(b"held by another run\n")
        if ignored:
            _exclude(root, b"/" + LEASE.encode("utf-8") + b"/")
        err = _create_refused(env, root, "The lease is never seized (spec 5.7)")
        assert "Commit your changes" not in err, ("T107 no dirt advice", ignored, err[-800:])
        assert (Path(root) / LEASE).is_dir(), ("T107 the directory lease survives", ignored)


def flip_t107():
    """A lease exclusion that excludes nothing: the lease record reads as ordinary dirt."""
    original = guard.parse_porcelain
    return patch.object(guard, "parse_porcelain",
                        lambda raw, prefix, lease, specs, verb: original(raw, prefix, None, specs, verb))


def t108_committed_deleted_lease(fx):
    """A lease committed at HEAD (a spec 5.7 violation) and deleted in the worktree emits a tracked " D"
    record on the lease path: the create refuses exit 2 naming the TRACKED lease and spec 5.7, rather than
    claiming the now-absent lease and sweeping its deletion into the change; every byte untouched."""
    env = fx.env
    root = fx.case("t108-committed-deleted-lease")
    write_commit(env, root, LEASE, PEER_LEASE, "a committed lease")
    (Path(root) / LEASE).unlink()
    err = _create_refused(env, root, "is TRACKED in git")
    assert "spec 5.7" in err, ("T108 the refusal names spec 5.7", err[-800:])


def flip_t108():
    """The old lease exclusion: every record on the lease path is dropped, whatever its status."""
    original = guard.parse_porcelain

    def parse(raw, prefix, lease, specs, verb):
        if lease is not None:
            path = os.fsencode(prefix) + os.fsencode(lease)
            raw = b"".join(r + b"\x00" for r in raw.split(b"\x00")[:-1] if r[3:] != path)
        return original(raw, prefix, lease, specs, verb)
    return patch.object(guard, "parse_porcelain", parse)


def t109_normalizing_filter_note(fx):
    """A store whose view carries an external normalizing clean filter (the git-lfs shape: the index holds
    the cleaned blob, the worktree the uncleaned body) reads dirty in the racy-clean window, because the
    probe never runs the filter: the create refuses exit 2 before mutation, and the refusal names the
    git-lfs-shape filter and settling the worktree; every byte untouched."""
    env = fx.env
    root = fx.case("t109-normalizing-filter")
    env.git(root, "config", "filter.norm.clean", "sed 's/[0-9]//g'")
    env.git(root, "config", "filter.norm.required", "true")
    (Path(root) / ".gitattributes").write_text(VERSION_VIEW + " filter=norm\n", encoding="utf-8")
    env.git(root, "add", "--renormalize", "--", VERSION_VIEW)
    env.git(root, "add", "--", ".gitattributes")
    env.git(root, "commit", "-q", "-m", "a normalizing filter")
    assert not re.search("[0-9]", env.git(root, "show", ":" + VERSION_VIEW)) and re.search(
        b"[0-9]", read(root, VERSION_VIEW)), "T109 the index holds the cleaned blob"
    os.utime(str(Path(root) / VERSION_VIEW), (4102444800, 4102444800))   # 2100-01-01: status must re-hash
    err = _create_refused(env, root, "Commit your changes (or move them aside)")
    assert "git-lfs-shape" in err and "Settle the worktree" in err, err[-800:]


def flip_t109():
    """No filter neutralization: the probe runs the filter, so the normalizing store reads clean."""
    return patch.object(guard._opf_observe, "_filter_neutralizing_config", lambda *args, **kwargs: [])


# --- T110-T117: receipt-repair, the reviewed repair of a ratified item with no receipt (spec 8.8) -----------

REPAIR_REASON = "ratified in review without its receipt"
REPAIR = ["receipt-repair", "BI-1", "--reason", REPAIR_REASON] + MAINTAINER
REPAIR_OPERANDS = (COUNTERS, DN_INDEX, WORKLOG)
ARCHIVED_DN_INDEX = ARCHIVE + "/done.index.toml"
NO_RECEIPT = "C-RECEIPTS: ratified backlog_item 'BI-1' (at 'done') has no done receipt"
T113_PEER_RUN = "imp-20260927T000102Z-" + "b" * 16


def _drop_legacy_journal(root):
    """The homes-1 runs that built a fixture leave their completed transactions in the legacy record
    journal, local recovery evidence that a homes-2 run refuses on sight (T20); a fixture about to run at
    homes 2 removes it, once every transaction there is terminal and nothing else sits under .aiqt."""
    states = journal_states(root)
    assert states and all(s != "open" for s in states.values()), ("the setup runs left no open transaction",
                                                                  states)
    aiqt = Path(root) / ".aiqt"
    assert sorted(os.listdir(aiqt)) == ["record"], ("only the legacy record journal sits under .aiqt",
                                                    sorted(os.listdir(aiqt)))
    shutil.rmtree(aiqt)


def _repair_store(fx, name, moves=(["transition", "BI-1", "active"] + MAINTAINER,), edit="commit"):
    """A store whose maintainer-created BI-1 ("repair me") the verb moved through `moves` at homes 1, each
    step committed. With `edit` "commit" the reviewed hand edit then puts BI-1 at unqualified done with no
    receipt, canonical and committed (doctor reports the C-RECEIPTS finding); with "worktree" the same
    edit is left uncommitted; with None no edit is made. The legacy journal is then removed
    (_drop_legacy_journal), and the store is ready for homes-2 activation."""
    root = fx.case(name)
    with ticking():
        step(fx, root, ["create", "--type", "backlog_item", "--title", "repair me"] + MAINTAINER, "BI-1")
        for move in moves:
            step(fx, root, move, " ".join(move[:3]))
    if edit is not None:
        index = model(root, BI_INDEX)
        index["record"][0].update(status="done", updated_at="2026-09-01T00:30:00Z")
        data = emit.emit_checked(index).encode("utf-8")
        if edit == "commit":
            write_commit(fx.env, root, BI_INDEX, data, "the reviewed edit: BI-1 done")
        else:
            (Path(root) / BI_INDEX).write_bytes(data)
    _drop_legacy_journal(root)
    return root


def _reservations(root, kind="record"):
    home = Path(root) / record._opf_journal._allocations_rel(kind)
    return sorted(os.listdir(home)) if home.exists() else []


def t110_receipt_repair(fx):
    """A committed BI-1 a reviewed hand edit put at unqualified done with no receipt (the C-RECEIPTS finding
    at rest): at homes 2 the maintainer's receipt-repair records exit 0, landing exactly one receipt_of
    receipt and its own worklog entry, the backlog index bytes unchanged, no status change reported, its
    run's reservation under journals/record/allocations claiming exactly the receipt's and the entry's
    numbers, one COMPLETE transaction in the typed journal, and doctor VALID; a second repair then refuses,
    every byte untouched (flip: no reservation published, so the publication boundary refuses)."""
    env = fx.env
    base = _repair_store(fx, "t110-base")
    rc, out, err = cli(env, ["doctor", "--root", str(base)])
    assert rc == 1 and NO_RECEIPT in out + err, ("T110 the reviewed edit leaves the C-RECEIPTS finding", rc,
                                                 out[-1600:], err[-800:])
    with _self_test_homes2_active(base):
        root = fx.case("t110-repair", base)
        bi_before = read(root, BI_INDEX)
        counters0 = model(root, COUNTERS)["counters"]
        did, wid = "DN-{}".format(counters0["DN"] + 1), "WL-{}".format(counters0["WL"] + 1)
        with ticking():
            out = recorded(record_cli(env, root, REPAIR + ["--summary", "the missing receipt"]))
        assert '"change": null' in out and '"ids": ["{}", "{}"]'.format(did, wid) in out, out[-1600:]
        assert read(root, BI_INDEX) == bi_before, "T110 the backlog index bytes are unchanged"
        receipts = receipts_of(root, "BI-1")
        assert [r["id"] for r in receipts] == [did], receipts
        assert (receipts[0]["actor"] == {"kind": "maintainer", "id": "owner"} and receipts[0]["title"] == "repair me"
                and receipts[0]["status"] == "recorded" and receipts[0]["summary"] == "the missing receipt"), receipts
        entry = model(root, WORKLOG)["entry"][-1]
        assert entry["id"] == wid and entry["detail"] == "opf-record create {} recorded\nreceipt_of: BI-1\nreason: {}".format(
            did, REPAIR_REASON), entry
        states = journal_states(root, TYPED_JOURNAL)
        assert list(states.values()) == ["complete"], ("T110 one COMPLETE typed transaction", states)
        (run_id,) = states
        assert _reservations(root) == [Path(record._opf_store.allocation_record("record", run_id)).name], (
            "T110 the run's own reservation stands", _reservations(root))
        payload = model(root, record._opf_store.allocation_record("record", run_id))
        assert payload["claims"] == {"DN": counters0["DN"] + 1, "WL": counters0["WL"] + 1}, payload
        assert not (Path(root) / record.JOURNAL_REL).exists(), "T110 no legacy journal is created"
        doctor_valid(env, root)
        refused_untouched(env, root, REPAIR, "already holds the clean done receipt '{}'".format(did))


def t110_homes1_refused(fx):
    """At homes 1 there is no reservation home: the repair refuses exit 2 naming it, before any file but the
    manifest is read, every byte untouched and no journal home created (flips: the homes guard dropped, so
    the repair claims through the homes-1 counters path and publishes; the request check's homes refusal
    dropped, so only the planner's own refusal holds, after every operand is read)."""
    root = _repair_store(fx, "t110-homes1")
    manifest_rel = MACH + "/" + record._opf_store.MANIFEST_NAME
    reads = []
    original_read = record._read_operand

    def observing(root_fd, rel):
        reads.append(rel)
        return original_read(root_fd, rel)
    with patch.object(record, "_read_operand", observing):
        refused_untouched(fx.env, root, REPAIR, "only a homes-2 store has")
    assert reads and set(reads) == {manifest_rel}, ("T110 refused before any operand is read", reads)
    assert not (Path(root) / ".working/journals").exists(), "T110 no reservation home is created"


def flip_t110_homes1():
    return patch.object(record, "_require_reservation_home", lambda ctx: None)


def flip_t110_homes1_late():
    """The request check without its receipt-repair homes refusal: the planner's own call still refuses."""
    check_request = record._check_request

    def without_repair(req, ctx):
        if req.subcommand != "receipt-repair":
            check_request(req, ctx)
    return patch.object(record, "_check_request", without_repair)


def t110_importer_arm(fx):
    """A clean BI-1 at done whose only receipt is a legacy importer-authored DN-1 (the spec 8.6 receipt arm's
    finding at rest) takes the clean receipt beside it: DN-2 records exit 0, the importer's row keeps its
    bytes, and the store is doctor VALID (flip: the writer's receipt join counts importer-authored
    receipts, so the item is refused, never repaired)."""
    env = fx.env
    base = _repair_store(fx, "t110-importer-base")
    legacy = {"id": "DN-1", "type": "done", "status": "recorded", "title": "migrated receipt",
              "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
              "actor": {"kind": "importer"}, "links": [{"rel": "receipt_of", "id": "BI-1"}]}
    dones = model(base, DN_INDEX)
    dones["record"] = dones.get("record", []) + [legacy]
    write_commit(env, base, DN_INDEX, emit.emit_checked(dones).encode("utf-8"), "a legacy importer receipt")
    counters = model(base, COUNTERS)
    counters["counters"]["DN"] += 1
    write_commit(env, base, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    rc, out, err = cli(env, ["doctor", "--root", str(base)])
    assert rc == 1 and "its only receipt(s) 'DN-1' are importer-authored" in out + err, (
        "T110 the importer-only receipt is the receipt arm's finding at rest", rc, out[-1600:], err[-800:])
    with _self_test_homes2_active(base):
        root = fx.case("t110-importer-arm", base)
        with ticking():
            recorded(record_cli(env, root, REPAIR))
        assert [r["id"] for r in receipts_of(root, "BI-1")] == ["DN-1", "DN-2"], receipts_of(root, "BI-1")
        assert model(root, DN_INDEX)["record"][0] == legacy, "T110 the importer's receipt keeps its bytes"
        doctor_valid(env, root)


def flip_t110_importer():
    """The writer's receipt join without the spec 8.6 firewall: every receipt naming the item counts,
    importer-authored ones included."""
    def counting(ctx, operand, rid):
        held = [rec.id for rec in record._receipt_records(ctx, record._index_rows(operand))
                if rec.rtype == "done" and ("receipt_of", rid) in rec.links]
        if held:
            raise record.RecordError("receipt-repair refused: {} already holds {}".format(rid, held))
    return patch.object(record, "_require_unreceipted", counting)


# Inside the killed child: the done index, the one operand the repair adds a row to, is written directly,
# OUTSIDE the journaled transaction, and only the counters and the worklog are journaled.
FLIP_REPAIR_UNJOURNALED = """
_journaled = record._publish
def _publish(ctx, plan, subcommand, cap=None, run_id=None):
    operands = [op for op in plan.operands if not op.rel.endswith("/done.index.toml")]
    for op in plan.operands:
        if op.rel.endswith("/done.index.toml"):
            (Path(ctx.res.store_root) / op.rel).write_bytes(op.new_raw)
    plan.operands = operands
    return _journaled(ctx, plan, subcommand, cap=cap, run_id=run_id)
record._publish = _publish
"""
_repair_crash_flip = [""]


def t110_repair_crash(fx):
    """A homes-2 receipt-repair (three operands: counters, done index, worklog) run at each of the journal
    engine's injection hooks kill_points(3) names. At after-lock, the homes-1 journal lock's hook, the child
    records: the homes-2 publication holds the operation capability and takes no journal lock, so that hook
    is never reached. Killed at every other hook, the run leaves its reservation standing and no reported
    id; the next run refuses after reclaiming the dead holder's capability records (a kill before INTENT is
    durable, which opens no transaction) or reconciling the typed journal, leaving the operands exactly at
    the prestate or exactly at the poststate (the poststate iff COMPLETE) and the backlog index untouched;
    after a rolled-back kill the next repair derives above the reserved numbers and records, doctor VALID
    (flip: the done index written outside the journaled transaction, applied in the killed child)."""
    env = fx.env
    base = _repair_store(fx, "t110-crash-base")
    with _self_test_homes2_active(base):
        counters0 = model(base, COUNTERS)["counters"]
        reference = fx.case("t110-crash-reference", base)
        proc = child(env, reference, REPAIR, flip=HOMES2_CHILD_FLIP)
        assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (proc.returncode, proc.stderr[-1600:])
        post = dict((rel, read(reference, rel)) for rel in REPAIR_OPERANDS)
        rolled_back = None
        points = kill_points(len(REPAIR_OPERANDS))
        for hook in points:
            point = ("T110 repair", hook)
            root = fx.case("t110-crash-" + hook.replace(":", "-"), base)
            pre = dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS)
            bi_before = read(root, BI_INDEX)
            proc = child(env, root, REPAIR, kill=hook, flip=HOMES2_CHILD_FLIP + _repair_crash_flip[0])
            if hook == "after-lock":
                # The homes-1 journal lock's hook: the homes-2 publication runs under the held operation
                # capability and takes no journal lock, so the hook is never reached and the run records.
                assert proc.returncode == 0 and RECORDED_EVENT in proc.stdout, (
                    "T110 no journal lock is taken at homes 2", point, proc.returncode, proc.stderr[-800:])
                assert dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS) == post, ("T110 the poststate", point)
                continue
            assert proc.returncode == 137, ("T110 the child is killed at", point, proc.stderr[-800:])
            assert RECORDED_EVENT not in proc.stdout, ("T110 a killed run reports no id", point)
            (reserved,) = _reservations(root)
            claims = model(root, record._ALLOCATIONS_REL + "/" + reserved)["claims"]
            assert claims == {"DN": counters0["DN"] + 1, "WL": counters0["WL"] + 1}, (point, claims)
            # Before INTENT is durable the kill leaves no opened transaction, only the dead holder's
            # capability records, which the next run reclaims; from INTENT on it reconciles the transaction.
            opened = points.index(hook) >= points.index("after-publish-INTENT")
            refused(record_cli(env, root, REPAIR), "was reconciled" if opened else "were reclaimed")
            states = journal_states(root, TYPED_JOURNAL)
            assert states and all(s != "open" for s in states.values()), ("T110 terminal", point, states)
            assert opened or set(states.values()) == {"nothing-opened"}, ("T110 nothing opened", point, states)
            now = dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS)
            complete = any(s == "complete" for s in states.values())
            assert now == (post if complete else pre), ("T110 prestate or poststate, poststate iff COMPLETE",
                                                        point, states)
            assert read(root, BI_INDEX) == bi_before, ("T110 the backlog index is never written", point)
            assert _reservations(root) == [reserved], ("T110 the reservation stands", point)
            if "rolled-back" in states.values() and rolled_back is None:
                rolled_back = root
        assert rolled_back is not None, "T110 some kill point rolls the repair back"
        recorded(record_cli(env, rolled_back, REPAIR))
        assert [r["id"] for r in receipts_of(rolled_back, "BI-1")] == ["DN-{}".format(counters0["DN"] + 2)], (
            "T110 the next repair derives above the reserved numbers", receipts_of(rolled_back, "BI-1"))
        doctor_valid(env, rolled_back)


@contextlib.contextmanager
def flip_t110_crash():
    """The crash matrix's children run with the done index written outside the journaled transaction."""
    _repair_crash_flip[0] = FLIP_REPAIR_UNJOURNALED
    try:
        yield
    finally:
        _repair_crash_flip[0] = ""


def t111_repair_maintainer_only(fx):
    """An assistant or automation receipt-repair refuses before the store is resolved, every byte untouched,
    naming the maintainer-only rule (flip: the maintainer check dropped)."""
    env = fx.env
    base = _repair_store(fx, "t111-base")
    with _self_test_homes2_active(base):
        root = fx.case("t111-maintainer-only", base)
        for actor in (ASSISTANT, ["--actor", "automation"]):
            refused_before_store(env, root, ["receipt-repair", "BI-1", "--reason", REPAIR_REASON] + actor,
                                 "maintainer-only")


def flip_t111():
    return patch.object(record, "_require_repair_maintainer", lambda actor: None)


def t112_repair_state_guard(fx):
    """An item at open, active, dropped or done/proposed is refused with every byte untouched, the
    done/proposed one pointed to done-with-receipt (flip: the state guard dropped, under which the item is
    refused only by a later guard, naming another rule)."""
    env = fx.env
    for label, moves, needle in (
            ("open", (), "at unqualified done"),
            ("active", (["transition", "BI-1", "active"] + MAINTAINER,), "at unqualified done"),
            ("dropped", (["transition", "BI-1", "dropped"] + MAINTAINER,), "at unqualified done"),
            ("proposed", (["transition", "BI-1", "active"] + MAINTAINER, ["transition", "BI-1", "done"] + ASSISTANT),
             "ratified with its receipt by opf record done-with-receipt")):
        base = _repair_store(fx, "t112-" + label, moves=moves, edit=None)
        with _self_test_homes2_active(base):
            refused_untouched(env, fx.case("t112-" + label + "-repair", base), REPAIR, needle)


def flip_t112_state():
    return patch.object(record, "_require_done_item", lambda row, rid: None)


def t112_repair_existing_receipt(fx):
    """An item that already holds its clean receipt (done-with-receipt) is refused, every byte untouched
    (flip: the receipt check dropped, so a second receipt publishes and fails the final doctor)."""
    base = _repair_store(fx, "t112-held", moves=(["transition", "BI-1", "active"] + MAINTAINER,
                                                 ["done-with-receipt", "BI-1"] + MAINTAINER), edit=None)
    with _self_test_homes2_active(base):
        refused_untouched(fx.env, fx.case("t112-held-repair", base), REPAIR,
                          "already holds the clean done receipt 'DN-1'")


def flip_t112_receipt():
    return patch.object(record, "_require_unreceipted", lambda ctx, operand, rid: None)


def t112_repair_uninterpreted_receipt(fx):
    """A committed DN-1 naming BI-1 whose links value is one table rather than an array of tables, which the
    doctor's receipt join reads as no link at all and so does not evaluate (C-RECEIPTS CANNOT-EVALUATE), is
    refused naming the row and the cause, every byte untouched (flip: the active done rows' interpretation
    check dropped, so the receipt join reads DN-1 as no receipt, a second receipt publishes and the final
    doctor fails after publication)."""
    env = fx.env
    base = _repair_store(fx, "t112-uninterpreted")
    malformed = {"id": "DN-1", "type": "done", "status": "recorded", "title": "repair me",
                 "created_at": "2026-09-01T00:30:00Z", "updated_at": "2026-09-01T00:30:00Z",
                 "actor": {"kind": "maintainer"}, "links": {"rel": "receipt_of", "id": "BI-1"}}
    dones = model(base, DN_INDEX)
    dones["record"] = dones.get("record", []) + [malformed]
    write_commit(env, base, DN_INDEX, emit.emit_checked(dones).encode("utf-8"), "a receipt with malformed links")
    counters = model(base, COUNTERS)
    counters["counters"]["DN"] += 1
    write_commit(env, base, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    rc, out, err = cli(env, ["doctor", "--root", str(base)])
    assert rc != 0 and "the receipt join is not evaluated over a partial set" in out + err, (
        "T112 the malformed receipt leaves the receipt join not evaluated at rest", rc, out[-1600:], err[-800:])
    with _self_test_homes2_active(base):
        refused_untouched(env, fx.case("t112-uninterpreted-repair", base), REPAIR,
                          "done.index.toml#{} has links that cannot be interpreted".format(len(dones["record"])))


def flip_t112_uninterpreted():
    real = record._require_interpreted_rows

    def backlog_rows_only(rows, rtype, rel, rid):
        if rtype != "done":
            real(rows, rtype, rel, rid)
    return patch.object(record, "_require_interpreted_rows", backlog_rows_only)


def t112_repair_uninterpreted_backlog(fx):
    """A committed BI-2 beside BI-1 whose status the doctor cannot interpret, which leaves the receipt join
    not evaluated (C-RECEIPTS CANNOT-EVALUATE) just as an uninterpreted done row does, is refused naming
    the backlog row and the cause, every byte untouched (flip: the active backlog rows' interpretation
    check dropped, so the repair publishes and is refused only after publication)."""
    env = fx.env
    base = _repair_store(fx, "t112-uninterpreted-backlog")
    items = model(base, BI_INDEX)
    items["record"].append(dict(items["record"][0], id="BI-2", title="unread", status="bogus"))
    write_commit(env, base, BI_INDEX, emit.emit_checked(items).encode("utf-8"), "a backlog row with a bad status")
    counters = model(base, COUNTERS)
    counters["counters"]["BI"] += 1
    write_commit(env, base, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    rc, out, err = cli(env, ["doctor", "--root", str(base)])
    assert rc != 0 and "the receipt join is not evaluated over a partial set" in out + err, (
        "T112 the uninterpreted backlog row leaves the receipt join not evaluated at rest", rc, out[-1600:],
        err[-800:])
    with _self_test_homes2_active(base):
        refused_untouched(env, fx.case("t112-uninterpreted-backlog-repair", base), REPAIR,
                          "backlog_item record row {}#2 has status that cannot be interpreted".format(BI_INDEX))


def flip_t112_backlog():
    real = record._require_interpreted_rows

    def done_rows_only(rows, rtype, rel, rid):
        if rtype != "backlog_item":
            real(rows, rtype, rel, rid)
    return patch.object(record, "_require_interpreted_rows", done_rows_only)


def _archived_receipt(fx, name):
    """BI-1 ratified with its receipt DN-1 (done-with-receipt), then DN-1 rotated to archive/2026: doctor
    VALID, the receipt held only in the archive."""
    root = _repair_store(fx, name, moves=(["transition", "BI-1", "active"] + MAINTAINER,
                                          ["done-with-receipt", "BI-1"] + MAINTAINER), edit=None)
    _rotate(fx, root, "DN-1", DN_INDEX, ARCHIVED_DN_INDEX, "done")
    assert receipts_of(root, "BI-1") == [], "the receipt sits in the archive alone"
    return root


def t112_repair_archived_receipt(fx):
    """An item whose clean receipt sits in the archive is refused, every byte untouched: the receipt join
    reads every archived record (flip: the active-only read, under which a second receipt publishes and
    fails the final doctor)."""
    base = _archived_receipt(fx, "t112-archived")
    with _self_test_homes2_active(base):
        refused_untouched(fx.env, fx.case("t112-archived-repair", base), REPAIR,
                          "already holds the clean done receipt 'DN-1'")


def t112_repair_unreadable_archive(fx):
    """An archived done index that does not parse (committed) refuses the repair with the archive walk's
    problems, every byte untouched, never skipped (flip: the active-only read)."""
    base = _archived_receipt(fx, "t112-unreadable-archive")
    write_commit(fx.env, base, ARCHIVED_DN_INDEX, b"record = [\n", "a torn archive index")
    with _self_test_homes2_active(base):
        refused_untouched(fx.env, fx.case("t112-unreadable-archive-repair", base), REPAIR,
                          "does not pass the doctor's own archive walk")


def flip_t112_archive():
    return patch.object(record, "_receipt_records", lambda ctx, rows: [
        opf_check._make_rec(r, "done", "active") for r in rows if isinstance(r, dict)])


def t113_repair_peer_reservation(fx):
    """A peer's reservation of the receipt's number landing between planning and the held capability (an
    import-kind reservation planted just before this run reserves) refuses the run at the held
    revalidation: every operand at the prestate, no record reservation and no transaction; the re-run
    claims above the peer's number and records, doctor VALID (flip: the reservation step without the
    planned claims, which reserves the next numbers and is refused only at the publication boundary)."""
    env = fx.env
    base = _repair_store(fx, "t113-base")
    with _self_test_homes2_active(base):
        root = fx.case("t113-peer-reservation", base)
        counters0 = model(root, COUNTERS)["counters"]
        peer = Path(root) / record._opf_store.allocation_record("import", T113_PEER_RUN)
        payload = record._opf_journal.reservation_payload("import", T113_PEER_RUN, "op",
                                                          {"DN": counters0["DN"] + 1})
        reserve = record._reserve_claim

        def peer_first(ctx, plan, cap, run_id):
            peer.parent.mkdir(parents=True, exist_ok=True)
            peer.write_bytes(payload)
            return reserve(ctx, plan, cap, run_id)
        pre = dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS + (BI_INDEX,))
        with patch.object(record, "_reserve_claim", peer_first), ticking():
            result = record_cli(env, root, REPAIR)
        assert _reservations(root) == [], ("T113 the refused run publishes no reservation", _reservations(root))
        refused(result, "changed between planning and the held capability")
        assert dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS + (BI_INDEX,)) == pre, \
            "T113 every operand at the prestate"
        assert journal_states(root, TYPED_JOURNAL) == {}, "T113 no transaction is opened"
        assert peer.read_bytes() == payload, "T113 the peer's reservation is never touched"
        with ticking():
            recorded(record_cli(env, root, REPAIR))
        assert [r["id"] for r in receipts_of(root, "BI-1")] == ["DN-{}".format(counters0["DN"] + 2)], (
            "T113 the re-run claims above the peer's reservation", receipts_of(root, "BI-1"))
        doctor_valid(env, root)


def t113_repair_backlog_changed(fx):
    """The backlog index the repair reads and never rewrites, edited between planning and the held lease
    (BI-1 put back at active just before the lease is taken), refuses the run at the held re-read naming
    that index: every operand at the prestate, no reservation and no transaction (flip: the held re-read
    of the operands alone, under which the receipt publishes for an item no longer at done and the final
    doctor fails after publication)."""
    env = fx.env
    base = _repair_store(fx, "t113-backlog-base")
    with _self_test_homes2_active(base):
        root = fx.case("t113-backlog-changed", base)
        index = model(root, BI_INDEX)
        index["record"][0]["status"] = "active"
        edited = emit.emit_checked(index).encode("utf-8")
        acquire = record._acquire_guard

        def edit_first(ctx):
            (Path(root) / BI_INDEX).write_bytes(edited)
            return acquire(ctx)
        pre = dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS)
        with patch.object(record, "_acquire_guard", edit_first), ticking():
            result = record_cli(env, root, REPAIR)
        assert dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS) == pre, "T113 every operand at the prestate"
        assert _reservations(root) == [], ("T113 the refused run publishes no reservation", _reservations(root))
        assert journal_states(root, TYPED_JOURNAL) == {}, "T113 no transaction is opened"
        refused(result, "{} changed after it was read".format(BI_INDEX))


def flip_t113_backlog():
    return patch.object(record, "_planned_from", lambda ctx, plan: list(plan.operands))


def t114_repair_uncommitted_edit(fx):
    """The reviewed edit must be committed first: BI-1 at done in the working tree only, and then committed at
    done with a further uncommitted edit of its row, each refuse exit 2 naming it, every byte untouched
    (flip: the committed-row check dropped, so the repair publishes and the final doctor, which accepts no
    pending status change for it, fails after publication)."""
    env = fx.env
    base = _repair_store(fx, "t114-base", edit="worktree")
    with _self_test_homes2_active(base):
        root = fx.case("t114-uncommitted", base)
        refused_untouched(env, root, REPAIR, "commit the reviewed edit first")
        write_commit(env, root, BI_INDEX, read(root, BI_INDEX), "the reviewed edit: BI-1 done")
        index = model(root, BI_INDEX)
        index["record"][0]["title"] = "repair me, retitled"
        (Path(root) / BI_INDEX).write_bytes(emit.emit_checked(index).encode("utf-8"))
        refused_untouched(env, root, REPAIR, "commit the reviewed edit first")


def flip_t114():
    return patch.object(record, "_require_committed_item", lambda ctx, row, rid: None)


def t115_repair_head_moved(fx):
    """HEAD moved between planning and the held lease (git reset --soft, as another shell would run it,
    made just before the lease is taken, after planning read the prior commit) to the first commit, which
    does not hold BI-1 at all, and to the commit holding BI-1 at active, each refuse at the committed-row
    check read again under the held lease: exit 2 naming it, every byte untouched, no reservation and no
    transaction (flip: the held re-check dropped, so the repair publishes over a prior commit that does
    not hold the item, and, the doctor's history check never reaching an item the prior commit does not
    hold, records exit 0)."""
    env = fx.env
    base = _repair_store(fx, "t115-base")
    with _self_test_homes2_active(base):
        for label, target in (("absent", None), ("active", "HEAD~1")):
            root = fx.case("t115-head-moved-" + label, base)
            if target is None:
                (target,) = env.git(root, "rev-list", "--max-parents=0", "HEAD").split()
            target = env.git(root, "rev-parse", "--verify", target).strip()
            acquire = record._acquire_guard

            def moved_first(ctx, root=root, target=target, acquire=acquire):
                env.git(root, "reset", "-q", "--soft", target)
                return acquire(ctx)
            before = snapshot(root)
            with patch.object(record, "_acquire_guard", moved_first), ticking():
                result = record_cli(env, root, REPAIR)
            assert snapshot(root) == before, ("T115 bytes untouched", label, result[0], result[2][-1600:])
            assert _reservations(root) == [], ("T115 no reservation", label, _reservations(root))
            assert journal_states(root, TYPED_JOURNAL) == {}, ("T115 no transaction is opened", label)
            assert env.git(root, "rev-parse", "HEAD").strip() == target, ("T115 HEAD was moved", label)
            refused(result, "under the held lease, immediately before publication, the prior commit (HEAD) "
                            "was read again: receipt-repair refused (uncommitted-item): BI-1")


def flip_t115():
    return patch.object(record, "_require_committed_held", lambda ctx, req: None)


def t116_repair_head_moved_published(fx):
    """HEAD moved after the committed-row check read under the held lease and before the final doctor
    (git reset --soft, as another shell would run it, made right after that held read), to the first
    commit, which does not hold BI-1 at all, and to the commit holding BI-1 at active: the receipt is
    already published, and each run refuses exit 2 after publication, never exit 0, nothing offered as
    recorded, with the scoped recovery text and its moved-HEAD caution, the lease released (flip: the
    post-publication re-check dropped, so over the commit not holding the item the final doctor passes
    and the repair records exit 0)."""
    env = fx.env
    base = _repair_store(fx, "t116-base")
    with _self_test_homes2_active(base):
        for label, target, needle in (
                ("absent", None, "after the final doctor, the prior commit (HEAD) was read again and the "
                                 "receipt is not offered as recorded: receipt-repair refused (uncommitted-item): "
                                 "BI-1"),
                ("active", "HEAD~1", "the uncommitted change is left for review")):
            root = fx.case("t116-head-moved-" + label, base)
            if target is None:
                (target,) = env.git(root, "rev-list", "--max-parents=0", "HEAD").split()
            target = env.git(root, "rev-parse", "--verify", target).strip()
            held = record._require_committed_held

            def moved_after(ctx, req, root=root, target=target, held=held):
                held(ctx, req)
                env.git(root, "reset", "-q", "--soft", target)
            pre = dict((rel, read(root, rel)) for rel in REPAIR_OPERANDS)
            with patch.object(record, "_require_committed_held", moved_after), ticking():
                result = record_cli(env, root, REPAIR)
            assert env.git(root, "rev-parse", "HEAD").strip() == target, ("T116 HEAD was moved", label)
            refused(result, needle)
            assert "restore --staged --worktree" in result[2] and "if HEAD has moved since" in result[2], (
                "T116 the scoped recovery text with its moved-HEAD caution", label, result[2][-1600:])
            assert read(root, DN_INDEX) != pre[DN_INDEX], ("T116 the publication is left for review", label)
            assert not (Path(root) / LEASE).exists(), ("T116 the lease is released on the failure path", label)


def flip_t116():
    return patch.object(record, "_require_committed_published", lambda ctx, req: None)


def t117_repair_unreadable_provenance(fx):
    """A committed DN-1 whose actor kind is the lookalike 'Maintainer', naming another item receipt_of,
    leaves the doctor's receipt join CANNOT-EVALUATE at rest whichever item it names: the repair of BI-1
    refuses before anything is written, naming the record (flip: the provenance check dropped, so the
    receipt publishes and the final doctor fails after publication)."""
    env = fx.env
    base = _repair_store(fx, "t117-provenance")
    lookalike = {"id": "DN-1", "type": "done", "status": "recorded", "title": "other",
                 "created_at": "2026-09-01T00:30:00Z", "updated_at": "2026-09-01T00:30:00Z",
                 "actor": {"kind": "Maintainer"}, "links": [{"rel": "receipt_of", "id": "BI-2"}]}
    dones = model(base, DN_INDEX)
    dones["record"] = dones.get("record", []) + [lookalike]
    write_commit(env, base, DN_INDEX, emit.emit_checked(dones).encode("utf-8"), "a receipt of lookalike provenance")
    counters = model(base, COUNTERS)
    counters["counters"]["DN"] += 1
    write_commit(env, base, COUNTERS, emit.emit_checked(counters).encode("utf-8"), "its counter")
    rc, out, err = cli(env, ["doctor", "--root", str(base)])
    assert rc != 0 and "whether each is importer-authored (spec 8.6) is not known" in out + err, (
        "T117 the lookalike provenance leaves the receipt join not evaluated at rest", rc, out[-1600:], err[-800:])
    with _self_test_homes2_active(base):
        refused_untouched(env, fx.case("t117-provenance-repair", base), REPAIR,
                          "the done record 'DN-1' ({}) has an actor kind that cannot be read".format(DN_INDEX))


def flip_t117_provenance():
    return patch.object(record, "_require_known_provenance", lambda ctx, records, rid: None)


def t117_repair_noncanonical_backlog(fx):
    """A committed backlog index carrying a comment, which the repair only reads and the doctor's
    canonical-bytes leg reports at rest, refuses the repair before anything is written, naming the index
    (flip: the canonical check of the read-only index dropped, so the receipt publishes and the final
    doctor fails after publication)."""
    env = fx.env
    base = _repair_store(fx, "t117-noncanonical")
    write_commit(env, base, BI_INDEX, read(base, BI_INDEX) + b"# a hand note\n", "a commented backlog index")
    rc, out, err = cli(env, ["doctor", "--root", str(base)])
    assert rc != 0 and BI_INDEX in out + err and "C-CONTAINMENT" in out + err, (
        "T117 the commented index is the canonical-bytes finding at rest", rc, out[-1600:], err[-800:])
    with _self_test_homes2_active(base):
        refused_untouched(env, fx.case("t117-noncanonical-repair", base), REPAIR,
                          "{} is not in canonical new-document form".format(BI_INDEX))


def flip_t117_canonical():
    return patch.object(record, "_require_canonical_backlog", lambda ctx: None)


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
    ("T22-homes2-claim-reserves", t22_homes2_claim_reserves, (flip_t22_refuse, flip_t22_noreserve,
                                                              flip_t22_validator,
                                                              flip_unreadable_reservation_skipped,
                                                              flip_string_claims_admitted,
                                                              flip_boolean_claims_admitted)),
    ("T23-homes2-crash-typed", t23_homes2_crash, flip_t23),
    ("T5-flip-unjournaled-write", t5_flip_write, None),     # FLIP_T5 itself is T5's discriminator
    ("T5-handoff-count-malformed-index", t5_handoff_count_malformed, (flip_t5_count_narrow,
                                                                      flip_t5_count_empty,
                                                                      flip_t5_count_host_zero,
                                                                      flip_t5_count_table_row)),
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
     (flip_t52_message, flip_t52_rule, flip_t79_doctor)),
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
    ("T69-release-failure-closes-fd", t69_release_failure_closes_fd, flip_t69_release),
    ("T69-token-failure-closes-fd", t69_token_failure_closes_fd, flip_t69_token),
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
    ("T74-recovery-never-seize-dead-run-release", t74_never_seize_dead_run_release,
     (flip_t74_claim, flip_t74_unscoped, flip_t74_live, flip_t74_host, flip_t74_schema, flip_t74_prefix,
      flip_t74_eperm, flip_t74_verb)),
    ("T75-concurrent-recovery-single-writer", t75_concurrent_recovery, None),  # its flip runs inside the children
    ("T76-examination-lock-failure-diagnosed", t76_lock_failure_diagnosed, flip_t76),
    ("T77-release-report-every-failure", t77_report_every_failure, flip_t77),
    ("T78-homes2-declaration-pair-required", t78_homes2_needs_the_pair, (flip_t78, flip_t30)),
    ("T79-kill-after-reserve", t79_kill_after_reserve, (flip_t79, flip_t79_doctor)),
    ("T80-two-runs-distinct-ids", t80_two_runs_never_same_id, (flip_t79, flip_t80_no_recheck)),
    ("T81-homes2-cross-kind-high-water", t81_cross_kind_reservations, (flip_t81,
                                                                       flip_t81_doctor,
                                                                       flip_unreadable_reservation_skipped,
                                                                       flip_string_claims_admitted,
                                                                       flip_boolean_claims_admitted)),
    ("T82-homes2-publication-requires-reservation", t82_publication_requires_reservation, flip_t82),
    ("T83-journal-states-confirmed-absence", t83_journal_states_confirmed_absence, flip_t83),
    ("T84-recovery-recursion-lease-refused-as-held", t84_recursion_lease_refused_as_held, flip_t84),
    ("T85-homes2-allocations-home-import-symlink", _t85("import", "symlink"), flip_t85_open),
    ("T85-homes2-allocations-home-import-regular-file", _t85("import", "regular-file"), flip_t85_open),
    ("T85-homes2-allocations-home-import-file-parent", _t85("import", "file-parent"), flip_t85_open_parent),
    ("T85-homes2-allocations-home-import-mode-0", _t85("import", "mode-0"), flip_t85_open),
    ("T85-homes2-allocations-home-import-unlistable", _t85("import", "unlistable"), flip_t85_listing),
    ("T85-homes2-allocations-home-import-underivable", _t85("import", "underivable"), flip_t85_derive),
    ("T85-homes2-allocations-home-record-symlink", _t85("record", "symlink"), flip_t85_open),
    ("T85-homes2-allocations-home-record-regular-file", _t85("record", "regular-file"), flip_t85_open),
    ("T85-homes2-allocations-home-record-file-parent", _t85("record", "file-parent"), flip_t85_open_parent),
    ("T85-homes2-allocations-home-record-mode-0", _t85("record", "mode-0"), flip_t85_open),
    ("T85-homes2-allocations-home-record-unlistable", _t85("record", "unlistable"), flip_t85_listing),
    ("T85-homes2-allocations-home-record-underivable", _t85("record", "underivable"), flip_t85_derive_record),
    ("T86-discrimination-bound-to-assertion", t86_discrimination_bound, flip_t86),
    ("T87-backstop-bound-to-injection", t87_backstop_bound_to_injection, (flip_t87_substring, flip_t87_runwide,
                                                                          flip_t87_write_call,
                                                                          flip_t87_token_only,
                                                                          flip_t87_any_write,
                                                                          flip_t87_in_process,
                                                                          flip_t87_plain_type,
                                                                          flip_t87_repr_substring)),
    ("T88-expected-injection-seen", t88_expected_injection_seen, (flip_t88_unchecked,
                                                                  flip_t88_t75_unwinding)),
    ("T89-handoff-posting-supersedes", t89_handoff_supersession, (flip_t89_skip, flip_t89_required_target,
                                                                  flip_t89_unqualified)),
    ("T89-two-current-handoffs-refused", t89_two_current_refused, flip_t89_first_current),
    ("T89-importer-history-clean-posting", t89_importer_history_posts, flip_t89_importer),
    ("T89-archived-current-handoff-refused", t89_archived_current_refused, flip_t89_archived),
    ("T89-reject-supersession-sole-current", t89_reject_supersession, flip_t89_reject_guard),
    ("T89-prior-separator-spelling-respelled", t89_prior_separator_spelling, (flip_t89_no_escape,
                                                                            flip_t89_strict)),
    ("T89-prior-separator-spelling-bounded", t89_bounded_acceptance, flip_t89_any_bytes),
    ("T90-store-read-cap-before-publication", t90_reader_cap, flip_t90_no_cap),
    ("T91-seam-five-sequential-distinct", t91_seam_five_sequential_distinct, flip_t91_scan_ignored),
    ("T92-seam-concurrent-processes-distinct", t92_seam_concurrent_processes_distinct, flip_t91_scan_ignored),
    ("T93-seam-threads-serialised", t93_seam_threads_serialised, flip_t93_unserialised),
    ("T94-planned-ids-bound-to-claims", t94_planned_ids_bound_to_claims, flip_t94_ids_unchecked),
    ("T95-importer-operand-transition-refused", t95_importer_operand_transition, flip_t95_author),
    ("T95-importer-operand-receipt-refused", t95_importer_operand_receipt, flip_t95_author),
    ("T95-importer-resolution-superseded", t95_importer_resolution_superseded, flip_t95_target),
    ("T96-malformed-lease-refused-as-held", t96_malformed_lease, flip_t96),
    ("T97-nested-store-held-lease", t97_nested_store_held_lease, flip_t97),
    ("T98-fifo-lease-refused-promptly", t98_fifo_lease, flip_t98),
    ("T99-dirty-operand-refused", t99_dirty_operand, flip_t99),
    ("T100-ignored-render-target-refused", t100_ignored_render_target, flip_unignored_probe),
    ("T101-collapsed-ignored-ancestor-refused", t101_collapsed_ignored_ancestor, flip_t101),
    ("T102-directory-at-destination-refused", t102_directory_at_destination, flip_unignored_probe),
    ("T103-ignored-prefix-sibling-outside-scope", t103_ignored_prefix_sibling, flip_t103),
    ("T104-unmanaged-ignored-outside-scope", t104_unmanaged_ignored_content, flip_t104),
    ("T105-non-utf8-prefix-refused", t105_non_utf8_prefix, flip_t105),
    ("T106-absent-ignored-destination-refused", t106_absent_ignored_destination, flip_t106),
    ("T107-ignored-and-directory-lease-held", t107_ignored_and_directory_lease, flip_t107),
    ("T108-committed-deleted-lease-refused", t108_committed_deleted_lease, flip_t108),
    ("T109-normalizing-filter-note", t109_normalizing_filter_note, flip_t109),
    ("T110-receipt-repair-records", t110_receipt_repair, flip_t22_noreserve),
    ("T110-receipt-repair-homes1-refused", t110_homes1_refused, (flip_t110_homes1, flip_t110_homes1_late)),
    ("T110-receipt-repair-importer-arm", t110_importer_arm, flip_t110_importer),
    ("T110-receipt-repair-crash", t110_repair_crash, flip_t110_crash),
    ("T111-receipt-repair-maintainer-only", t111_repair_maintainer_only, flip_t111),
    ("T112-receipt-repair-state-guard", t112_repair_state_guard, flip_t112_state),
    ("T112-receipt-repair-existing-receipt", t112_repair_existing_receipt, flip_t112_receipt),
    ("T112-receipt-repair-uninterpreted-receipt", t112_repair_uninterpreted_receipt, flip_t112_uninterpreted),
    ("T112-receipt-repair-uninterpreted-backlog", t112_repair_uninterpreted_backlog, flip_t112_backlog),
    ("T112-receipt-repair-archived-receipt", t112_repair_archived_receipt, flip_t112_archive),
    ("T112-receipt-repair-unreadable-archive", t112_repair_unreadable_archive, flip_t112_archive),
    ("T113-receipt-repair-peer-reservation", t113_repair_peer_reservation, flip_t80_no_recheck),
    ("T113-receipt-repair-backlog-changed", t113_repair_backlog_changed, flip_t113_backlog),
    ("T114-receipt-repair-uncommitted-edit", t114_repair_uncommitted_edit, flip_t114),
    ("T115-receipt-repair-head-moved", t115_repair_head_moved, flip_t115),
    ("T116-receipt-repair-head-moved-after-held-read", t116_repair_head_moved_published, flip_t116),
    ("T117-receipt-repair-unreadable-provenance", t117_repair_unreadable_provenance, flip_t117_provenance),
    ("T117-receipt-repair-noncanonical-backlog", t117_repair_noncanonical_backlog, flip_t117_canonical),
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
    ran, failures, unevaluated = [], [], []

    def check(name, test):
        ran.append(name)
        try:
            test()
        except AssertionError as exc:
            failures.append("{}: {}".format(name, exc))
        except CannotEvaluate as exc:
            ran.remove(name)
            unevaluated.append("{}: {}".format(name, exc))

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
                          lambda name=label, test=test, flip=flip: _discriminate(name, lambda: test(fx), flip))
            check("red-on-revert-T5-crash-prestate-or-poststate",
                  lambda: _discriminate("T5", lambda: t5_crash(fx),
                                        lambda: _child_flip(fx)))
            check("red-on-revert-T5-handoff-unjournaled-index",
                  lambda: _discriminate("T5-handoff", lambda: t5_crash(fx),
                                        lambda: _child_flip_handoff(fx)))
            check("red-on-revert-T5-handoff-two-current-interim", lambda: _discriminate_two_current(fx))
            check("red-on-revert-T5-handoff-two-current-interim-disarmed",
                  lambda: t5_two_current_counter(fx))
            check("red-on-revert-T75-concurrent-recovery-single-writer",
                  lambda: _discriminate("T75", lambda: t75_concurrent_recovery(fx),
                                        lambda: _t75_child_flip(fx)))
    except Exception as exc:  # noqa: BLE001  a harness fault is cannot-evaluate, never a verdict
        print("OPF-RECORD SELF-TEST ERROR: {!r}".format(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("OPF-RECORD SELF-TEST: exercised " + ", ".join(ran))
    for reason in unevaluated:
        print("CANNOT EVALUATE: " + reason, file=sys.stderr)
    if failures:
        for failure in failures:
            print("FAIL: " + failure, file=sys.stderr)
        return 1
    if unevaluated:
        print("OPF-RECORD SELF-TEST CANNOT EVALUATE: {} check(s) not evaluated, never a pass".format(
            len(unevaluated)), file=sys.stderr)
        return 2
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


@contextlib.contextmanager
def _child_flip_handoff(fx):
    """The handoff-scoped T5 flip as a context: while active, t5_crash's killed children run with
    FLIP_T5_HANDOFF, under which every scenario but the handoff-superseding one stays green, so the
    red it must produce can come only from that scenario's own matrix."""
    _t5_flip[0] = FLIP_T5_HANDOFF
    try:
        yield
    finally:
        _t5_flip[0] = ""


@contextlib.contextmanager
def _child_flip_two_current(fx):
    """The two-current T5 flip as a context: while active, t5_crash's killed children run with
    FLIP_T5_TWO_CURRENT, under which the handoff-superseding scenario's torn-payload kill leaves a
    PARSEABLE two-current handoff index inside a journaled apply. Every assertion before the continuous
    at-most-one-current check stays green there (the child is killed, its lease stands, and the
    write-ahead evidence is present), so that check is the first to red, on the exposure itself."""
    _t5_flip[0] = FLIP_T5_TWO_CURRENT
    try:
        yield
    finally:
        _t5_flip[0] = ""


def _discriminate_two_current(fx):
    """FLIP_T5_TWO_CURRENT must turn T5 red through the continuous at-most-one-current assertion at the
    exposure itself (t5_two_current_exposed), and the unflipped T5 must stay green around it."""
    _discriminate("T5-two-current", lambda: t5_two_current_exposed(fx), lambda: _child_flip_two_current(fx))


def t5_two_current_counter(fx):
    """With the continuous at-most-one-current assertion disarmed (its count reads every handoff index
    as holding none, flip_t5_count_empty), FLIP_T5_TWO_CURRENT still turns T5 red, later, through a
    recovery assertion; the two-current discriminator must refuse that red and fail, naming the other
    assertion (_T5OtherRed), not on an unrelated fault."""
    with flip_t5_count_empty():
        try:
            _discriminate_two_current(fx)
        except AssertionError as exc:
            assert "_T5OtherRed(" in str(exc), ("T5 the disarmed two-current discriminator failed, but not "
                                                "on another assertion", str(exc)[:1200])
            return
    raise AssertionError("T5 the two-current discriminator stayed green with its assertion disarmed")


@contextlib.contextmanager
def _t75_child_flip(fx):
    """The T75 flip as a context: while active, the recovery children run with the examination
    serialization made a no-op (the unserialized release)."""
    _t75_flip[0] = FLIP_T75
    try:
        yield
    finally:
        _t75_flip[0] = ""


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
