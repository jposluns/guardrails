# OPF init later-slice obligation crosswalk

This document accounts for each later OPF-INIT obligation that the `opf-init-p0` gate residue in
`.aiqt/core/gates/manifest.toml` lists as out of scope: target and control binding, the adoption
classifier, coordination, Keep-apply, verb wiring, git staging, publication, finalization, the CLI
verbs, and Migrate/Move. It is a reconciliation record, not an implementation claim. A symbol cited
below implements at most the part its entry names; presence in source does not establish runtime
correctness, and no entry is closed.

`opf/tools/check_opf_init_reconciliation.py` checks the structure of this file only: every residue
obligation has exactly one entry, the Migrate/Move entry keeps its three split children, and an
entry whose disposition is `transferred` names a successor. It does not judge whether a cited
symbol meets its requirement.

Generation note: `OPF-SPEC.md` section 16.1 states that the reference tooling still initializes and
validates stores at base `1.2.0` (`_opf_store.SUPPORTED_SPEC_VERSION`, with
`_opf_store.SUPPORTED_HOMES = 1`), while the specification defines only base 2.0.0 with homes
generation 2. Unless an entry says otherwise, the code it cites covers the 1.2.0, homes 1 store.

Ownership note: which tracker records own the split work is a pending maintainer decision, so every
owner below is a proposal. Where a further maintainer decision bears on an entry, its ownership is
marked open and the decision is named.

Field vocabulary: `Disposition` is one of `open`, `retained`, `transferred` or `superseded`; an
entry marked `transferred` names its successor in `Successor`.

## Obligation: target and control binding

- Requirement: the coupled init binds its explicit product root, repository and control area, and
  re-observes that binding under the operation mutex (`OPF-INIT-D2B.md`; residue of the
  `opf-init-p0` gate).
- Implementation: `opf/tools/_opf_init_operation.py` `observe_binding` (observes repository,
  toplevel, git directory, common git directory, prefix, object format and HEAD) and
  `run_init_operation` (re-observes the binding and HEAD under the mutex);
  `opf/tools/_opf_init_substrate.py` (persisted plan and phase records under the control root).
- Generation: 1.2.0, homes 1; `run_init_operation` also takes a `homes2` argument, whose coverage
  is not reconciled here.
- Tests: `opf/tools/_opf_init_operation.py --self-test`; `opf/tools/_opf_init_substrate.py
  --self-test`; `opf/tools/_opf_oplock.py --self-test`.
- Not done: binding for an adoption whose store resolves outside the product root;
  `_opf_adopt_apply._store_posture_or_refuse` refuses that case.
- Proposed owner: adoption coordination and driver inputs successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

## Obligation: adoption classifier

- Requirement: investigation distinguishes first adoption from re-adoption (`OPF-SPEC.md`
  section 14); the coupled init needs a classifier that establishes adoption qualification.
- Implementation: `opf/tools/_opf_init_observe.py` `read_seed_basis` (a seed-consumer seam whose
  docstring states it does not establish adoption qualification and that a separate classifier
  must supply that authority) and `prior_store_holder` (prior-store detector);
  `opf/tools/_opf_adopt_plan.py` (module text: ancestry marks re-adoption when a pointer or a
  resolved store manifest is present). These are different classification contracts.
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/check_opf_init_observe.py --self-test --red-on-revert`;
  `opf/tools/check_opf_init.py --self-test` (prior-store detector over shared ancestry fixtures).
- Not done: the full classifier that authorizes initialization; a decision on which contract is
  authoritative for a manifestless tree with OPF ancestry.
- Proposed owner: classifier decision and implementation successor.
- Ownership: open (maintainer decision pending: which classifier contract is authoritative).
- Disposition: open
- Successor: none

## Obligation: coordination

- Requirement: a run holds the single-writer lease before mutating a store (`OPF-SPEC.md` section
  5.7), and the init and adoption operations are sequenced under one mutex.
- Implementation: `opf/tools/_opf_init_operation.py` `run_init_operation` (mutex, persisted plan,
  lease attach and detach, source and view groups, final check); `opf/tools/_opf_oplock.py`.
  `opf/tools/_opf_adopt_apply.py` `_store_posture_or_refuse` refuses a product root that resolves a
  store because the apply shell has no lease join.
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/_opf_init_operation.py --self-test`; `opf/tools/_opf_oplock.py --self-test`;
  `opf/tools/_opf_adopt_apply.py --self-test`.
- Not done: adoption coordination over a resolved store (lease join and recovery of a dead apply's
  lease).
- Proposed owner: adoption coordination and driver inputs successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

## Obligation: Keep-apply

- Requirement: a Keep disposition registers the path as unmanaged and preserves its bytes
  (`OPF-SPEC.md` section 14.2); the older D2b Keep-acceptance contract is validated by
  `opf/tools/_opf_init_contract.py`.
- Implementation: `opf/tools/_opf_adopt_apply.py` `OP_HANDLERS` (`register-unmanaged` maps to
  `_execute_registration_op`), `_compose_register_unmanaged`, `_driver_contexts`;
  `opf/tools/_opf_init_contract.py` (Keep-acceptance validator, no filesystem effect).
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/_opf_adopt_apply.py --self-test`; `opf/tools/_opf_init_contract.py
  --self-test`; `opf/tools/check_opf_init_contract.py`.
- Not done: Keep through a usable end-to-end adoption path (the apply shell still refuses a
  resolved store); reconciliation of the older Keep acceptance and worklog contract with the
  one-plan approval.
- Proposed owner: Keep and publication integration successor.
- Ownership: open (maintainer decision pending: whether the one-plan approval replaces the older
  Keep acceptance and worklog contract).
- Disposition: open
- Successor: none

## Obligation: verb wiring

- Requirement: the adoption verb dispatches each stage to its implementation (`OPF-SPEC.md`
  section 14).
- Implementation: `opf/tools/opf.py` `_cmd_adopt` (subcommands `plan`, `approve`, `apply`,
  `complete`, `reconcile` and `status`; its docstring states that `complete` refuses fail-closed,
  exit 2, until the completion stage lands).
- Generation: 1.2.0, homes 1; `_cmd_adopt` also describes refusals on base 2.0.0.
- Tests: `opf/tools/opf.py --self-test` (not mapped to specific cases here).
- Not done: a working `complete`; a successful `apply` (see finalization); whether this obligation
  overlaps the CLI verbs entry is undecided, so the two stay separate.
- Proposed owner: completion and retirement successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

## Obligation: git staging

- Requirement: only exact S file entries enter the index, unrelated index state survives, and git
  configuration cannot transform payloads (`OPF-INIT-D2B-REVIEW.md` F19 to F22, assigned to PR5).
- Implementation: none on main. `opf/tools/_opf_init_operation.py` module docstring: the layer
  stages nothing in the git index (PR5; the views remain untracked).
- Generation: not applicable (no implementation).
- Tests: `opf/tools/check_opf_init_p0.py --self-test --red-on-revert` checks the F19 boundary
  contract (staging set fixed empty) in memory, not staging.
- Not done: all of it.
- Proposed owner: coupled-init staging and CLI activation successor, if retained.
- Ownership: open (maintainer decision pending: whether exact-source git staging remains a product
  promise).
- Disposition: open
- Successor: none

## Obligation: publication

- Requirement: sources and views are published through a create-only, preserving journal, and
  adoption apply plants governance, renders views and records the receipt (`OPF-SPEC.md` section
  14).
- Implementation: `opf/tools/_opf_init_operation.py` `_stage_and_publish` and
  `run_init_operation`; `opf/tools/_opf_adopt_apply.py` `OP_HANDLERS` (`plant-governance`,
  `render-views`, `record-adoption`) and `_DRIVER_UNSUPPLIED`, which names inputs the driver cannot
  supply for those handlers.
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/_opf_init_operation.py --self-test`; `opf/tools/_opf_adopt_apply.py
  --self-test`.
- Not done: publication through the complete adoption driver (the inputs `_DRIVER_UNSUPPLIED`
  names).
- Proposed owner: Keep and publication integration successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

## Obligation: finalization

- Requirement: adoption ends with rendered views, an adoption receipt and its outcome-event chain,
  and a `views-ready` milestone alone does not count as success (`OPF-SPEC.md` section 14);
  retirement waits on the section 14.1 completion checks.
- Implementation: `opf/tools/_opf_init_operation.py` `run_init_operation` (records `views-ready`
  after lease detach and the final check). `opf/tools/_opf_adopt_apply.py` `DRIVER_STAGES` maps the
  receipt stage to `_receipt_not_yet_composable`, which refuses; `run_apply` refuses before writing.
  `opf/tools/_opf_adopt_complete.py` `PR6_DENY_PROBES_SHIPPED` is `False`.
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/_opf_init_operation.py --self-test`; `opf/tools/_opf_adopt_apply.py
  --self-test`; `opf/tools/_opf_adopt_complete.py --self-test`.
- Not done: the adoption receipt stage, completion and retirement.
- Proposed owner: completion and retirement successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

## Obligation: CLI verbs

- Requirement: the init CLI exposes coupled-init milestones only once they are complete
  (`opf/tools/_opf_init_operation.py` module docstring defers this to PR7).
- Implementation: `opf/tools/opf.py` `_init_main` (source-only store scaffolding under the init
  mutex and lease, without rendering).
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/check_opf_init.py --self-test`.
- Not done: wiring the CLI to `run_init_operation` and any coupled-init success report.
- Proposed owner: coupled-init staging and CLI activation successor, if retained.
- Ownership: open (maintainer decision pending: whether the adoption re-scope supersedes or
  transfers the standalone coupled-init activation).
- Disposition: open
- Successor: none

## Obligation: Migrate/Move

- Requirement: the parent name covers three separate meanings that `OPF-SPEC.md` sections 14.2 and
  5.4 keep apart; each is accounted for below.
- Implementation: see the split children.
- Generation: see the split children.
- Tests: see the split children.
- Not done: see the split children.
- Proposed owner: see the split children.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

### Split: import disposition

- Requirement: a migrate-disposed source keeps its exact bytes at apply for post-adoption import,
  and its retirement is recorded only after its completion check is green (`OPF-SPEC.md` section
  14.2, Migrate).
- Implementation: not reconciled to a symbol here; `opf/tools/_opf_import_complete.py` and
  `opf/tools/_opf_import_batch.py` exist but are not verified against this requirement.
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/_opf_import_complete.py --self-test`; `opf/tools/_opf_import_batch.py
  --self-test` (coverage of this requirement not established).
- Not done: source-accounted import completion through adoption.
- Proposed owner: import disposition, Move retirement and whole-store relocation successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

### Split: move disposition

- Requirement: Move relocates to a named destination or by default to
  `.working/archive/moved/<source-path>`, and an occupied destination is a collision, never an
  overwrite (`OPF-SPEC.md` section 14.2, Move).
- Implementation: `opf/tools/_opf_adopt_plan.py` `_default_move_destination` (default destination
  and collision refusal at plan time); `opf/tools/_opf_adopt_apply.py` `OP_HANDLERS` (`move-file`
  maps to `_execute_file_op`).
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/_opf_adopt_plan.py` test cases `test_default_move_destination` and
  `test_default_move_collision` (which gate runs them is not reconciled here);
  `opf/tools/_opf_adopt_apply.py --self-test`.
- Not done: Move retirement after a green completion through the real driver.
- Proposed owner: import disposition, Move retirement and whole-store relocation successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none

### Split: whole-store relocation

- Requirement: `opf migrate --store` relocates the whole store, carries every frozen source,
  archive and evidence bundle byte-identically, and never executes a disposition (`OPF-SPEC.md`
  sections 5.4 and 14.2).
- Implementation: none on main reconciled here.
- Generation: not applicable (no implementation).
- Tests: none.
- Not done: all of it.
- Proposed owner: import disposition, Move retirement and whole-store relocation successor.
- Ownership: open (tracker assignment pending).
- Disposition: open
- Successor: none
