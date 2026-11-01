# OPF init later-slice obligation crosswalk

This document accounts for each later OPF-INIT obligation that the `opf-init-p0` gate residue in
`.aiqt/core/gates/manifest.toml` lists as out of scope: target and control binding, the adoption
classifier, coordination, Keep-apply, verb wiring, git staging, publication, finalization, the CLI
verbs, and Migrate/Move. It is a reconciliation record, not an implementation claim. A symbol cited
below implements at most the part its entry names; presence in source does not establish runtime
correctness. No entry is closed by evidence of implementation; an entry marked `transferred` is
closed only in the sense the closing rule below allows, by an accepted successor.

`opf/tools/check_opf_init_reconciliation.py` checks the structure of this file only: every residue
obligation has exactly one entry, the Migrate/Move entry keeps its three split children, every
entry carries an Owner and an Ownership, and an entry whose disposition is `transferred` names as
its successor a backlog id listed in `TODO.md` at the repository root and an Owner other than
`none yet`. The check reads this file through a closed list of line shapes, each starting at the
line start: a blank line, an entry heading, any other ATX heading, a field line (`- Name: value`),
an ordered item (`1. text`), a line indented with spaces exactly to the text of the item line or
continuation line above it, and prose. Inline text is read by the CommonMark 0.31.2 rules: a
backslash escapes ASCII punctuation, a code span closes only on a backtick run of exactly its
opening length, and an inline comment is `<!-->`, `<!--->` or `<!--` through the next `-->` on its
line. A backtick run that nothing on its line closes, on a line other than a heading, carries a
code span onto the next line when that line is indented, prose, or an ordered item other than
`1. ` with text (the one item a reader starts a list on below a paragraph), and is literal text
when the next line ends the paragraph. Heading, ordered item, continuation and prose text must open
with a letter, a digit that starts no list marker, `(`, a quote, a backtick or an option name; must
not begin with an entry or field name, any whitespace, and a colon, either once emphasis, code and
escape marks are removed or as a reader renders it (a character reference decoded as the vendored
marko decodes it: a decimal number of up to seven digits, a hexadecimal one of up to six or a name
of up to 32 characters, ended by `;`, read by Python's `html.unescape`; an escaped character and a
closed code span's content kept literal); and must not open with letters that could begin such a
name (a prefix of one, or a whole one and whitespace) followed, once rendered, by a character a
reader shows with no glyph: a format character (Unicode category Cf) or a default-ignorable code
point (the Unicode `Default_Ignorable_Code_Point` property, such as U+034F or U+3164). Whitespace
here is any character Python's `str.isspace` accepts: the tab, line feed, form feed, carriage return
and space that HTML shows as one space (so `Owner&#10;:` reads as `Owner :`), and the no-break and
other Unicode spaces, which HTML does not collapse but a reader shows blank. A vertical tab is not
HTML whitespace; its reference decodes to nothing and a literal one is a control character, so it
is refused either way. A character reference is so refused only where its decoded character
completes such a name: `S&amp;P` and `Split&nbsp;decisions` are read, and two spellings a reader
renders alike are judged alike. A name spelled with a look-alike a reader does show (a visible
combining mark such as U+0301, a full-width colon) is read as that text, not as the name. That
text, and a field line, must hold no raw HTML outside a code span; a field value
is checked for nothing else, apart from the refusals below that apply to every line. Every other
line cannot evaluate: a blockquote, a bullet item other than a field line, an ordered item marked
other than `<digits>. `, a table row, a setext underline or thematic break, an HTML line, an
indented line that continues no item, a fence or comment opener indented by spaces or tabs at any
width (a backtick run with another backtick later on its line is inline code, not an opener), text
after the end of a comment that opens its line, a `<!--` that does not close on its line (refused
as raw HTML, although a reader shows it as literal text unless a later line of the paragraph closes
it), a code span carried into a line that does not close it, two backtick runs on one line that
nothing on it closes above a line that continues the paragraph (refused although a reader may
render them, as the check carries one run), a backtick run left open above an ordered item that
continues the paragraph (also inside a list, where a reader may start a sibling item instead), an
entry heading whose name holds a character reference `html.unescape` decodes or a character a reader
shows with no glyph, and a carriage return or other control character. Two refusals reach text a
reader shows differently: an entry heading name holding a reference that a backslash escapes or a
code span holds (`coordi\&#110;ation`, which the vendored marko shows literally) cannot evaluate
rather than reading as an unknown obligation, and a name matched without regard to case also
matches U+017F (long s) for `s` and U+0130 and U+0131 (dotted capital and dotless i) for `i`, so
a Split name spelled with a long s is refused. An entry ends at the next heading that is not an
entry heading; prose and ordered items end no entry. An Owner, Ownership, Disposition or
Successor field line after that end and before the next entry, or before the first entry, belongs
to no entry and is refused. Lines and `TODO.md`
table rows inside a fenced code block, or inside an HTML comment that opens its line, are not read;
such a comment ends on the first line holding `-->`, so `<!-->` and `<!--->` end it on their own
line. An inline comment hides no later line. A field's value is read from its first line only.
Where `TODO.md` cannot be read, as in a copy of `opf/` alone, the check cannot evaluate. It does
not judge whether a cited symbol meets its requirement or whether a named successor accepted the
work.

Generation note: `OPF-SPEC.md` section 16.1 states that the reference tooling still initializes and
validates stores at base `1.2.0` (`_opf_store.SUPPORTED_SPEC_VERSION`, with
`_opf_store.SUPPORTED_HOMES = 1`), while the specification defines only base 2.0.0 with homes
generation 2. Unless an entry says otherwise, the code it cites covers the 1.2.0, homes 1 store.

Ownership note: the maintainer answered the crosswalk's ownership questions on 2026-10-31. The
answers, in plain words:

1. Each of the old coupled-init later-slice duties moves to the adoption-orchestration work
   (backlog id `OPF-ADOPT-ORCH`); a duty with no equivalent acceptance check there stays open.
2. Adoption's present-tree classification and init's history and counter policy stay two distinct
   classifiers.
3. Automatic exact-source git staging stays a product promise until a contract change withdraws it.
4. One adoption approval replaces the older Keep acceptance and worklog contract; the replacement
   documents which attribution and preservation requirements survive.
5. An entry closes only when every obligation in it has evidence or an accepted successor.

Acceptance reading: the `OPF-ADOPT-ORCH` backlog row in `TODO.md` lists no acceptance checks of its
own, so this record reads that work's acceptance as the adoption contract it implements:
`OPF-SPEC.md` section 14 (the end state of apply) and the clean-start completion-check roster in
section 14.1. That reading is this record's inference, not part of the answers; an entry below is
marked `transferred` only where that contract carries a check equivalent to the entry's remaining
work, and is otherwise left open with the reason.

Field vocabulary: `Disposition` is one of `open`, `retained`, `transferred` or `superseded`; an
entry marked `transferred` names its successor's `TODO.md` backlog id in `Successor`. Every
transfer below rests on the acceptance reading above, which is this record's inference.

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
- Owner: adoption-orchestration work (`OPF-ADOPT-ORCH`).
- Ownership: transferred under answer 1, by this record's inferred acceptance reading. The
  equivalent acceptance check is the section 14.1 plan binding of product and store identities and
  the observed revision, with completion checks 1 (authority and freshness) and 4 (the store
  resolves to the planned identity); these check the binding wherever the store resolves. The
  remaining work sits behind the same function as coordination's refusal
  (`_opf_adopt_apply._store_posture_or_refuse`), which refuses a pointer to an out-of-root store
  first, with its own message (adoption evidence and archives belong at that store root, which the
  apply shell does not support), before its lease-join refusal is reached. Binding over such a
  store therefore needs that support as well as coordination's lease join. That dependency does not
  remove the equivalent check; coordination stays open because no section 14.1 check covers a lease
  join, not because of the shared function.
- Disposition: transferred
- Successor: OPF-ADOPT-ORCH

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
- Owner: none yet.
- Ownership: open. The answers settle that adoption's present-tree classification and init's
  history and counter policy stay two distinct classifiers, so neither contract replaces the other.
  The transfer answer keeps a duty open when the adoption work has no equivalent acceptance check,
  and neither section 14 nor the section 14.1 completion roster checks either classification.
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
- Owner: none yet.
- Ownership: open. The answer moves this duty to the adoption-orchestration work only with an
  equivalent acceptance check there, and the section 14.1 completion roster has none for a lease
  join over a resolved store or the recovery of a dead apply's lease.
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
- Owner: none yet.
- Ownership: open. The answers settle that one adoption approval replaces the older Keep
  acceptance and worklog contract, and that the replacement documents which attribution and
  preservation requirements survive; that record of surviving requirements does not exist yet, and
  no section 14.1 completion check verifies a Keep path's preserved bytes, so the entry stays open.
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
- Owner: adoption-orchestration work (`OPF-ADOPT-ORCH`).
- Ownership: transferred under answer 1, by this record's inferred acceptance reading. The
  equivalent acceptance check is the section 14.1 completion roster that a working `complete`
  runs, with retirement recorded only on a green check.
- Disposition: transferred
- Successor: OPF-ADOPT-ORCH

## Obligation: git staging

- Requirement: only exact S file entries enter the index, unrelated index state survives, and git
  configuration cannot transform payloads (`OPF-INIT-D2B-REVIEW.md` F19 to F22, assigned to PR5).
- Implementation: none on main. `opf/tools/_opf_init_operation.py` module docstring: the layer
  stages nothing in the git index (PR5; the views remain untracked).
- Generation: not applicable (no implementation).
- Tests: `opf/tools/check_opf_init_p0.py --self-test --red-on-revert` checks the F19 boundary
  contract (staging set fixed empty) in memory, not staging.
- Not done: all of it.
- Owner: none yet.
- Ownership: open. The answers keep automatic exact-source git staging as a product promise until a
  contract change withdraws it, so it is not superseded; the transfer answer keeps it open because
  neither section 14 nor the section 14.1 completion roster checks what enters the git index.
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
- Owner: adoption-orchestration work (`OPF-ADOPT-ORCH`).
- Ownership: transferred under answer 1, by this record's inferred acceptance reading. The
  equivalent acceptance checks are section 14's requirement that apply end with rendered views and
  an adoption receipt plus its outcome-event chain, and completion checks 4 (declared views
  rendered, no unaccounted drift) and 5 (the enforcement pack installed and probed).
- Disposition: transferred
- Successor: OPF-ADOPT-ORCH

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
- Owner: adoption-orchestration work (`OPF-ADOPT-ORCH`).
- Ownership: transferred under answer 1, by this record's inferred acceptance reading. The
  equivalent acceptance checks are section 14's rule that a `views-ready` milestone alone is not
  adoption success and section 14.1's rule that retirement is recorded only on a green completion
  check.
- Disposition: transferred
- Successor: OPF-ADOPT-ORCH

## Obligation: CLI verbs

- Requirement: the init CLI exposes coupled-init milestones only once they are complete
  (`opf/tools/_opf_init_operation.py` module docstring defers this to PR7).
- Implementation: `opf/tools/opf.py` `_init_main` (source-only store scaffolding under the init
  mutex and lease, without rendering).
- Generation: 1.2.0, homes 1.
- Tests: `opf/tools/check_opf_init.py --self-test`.
- Not done: wiring the CLI to `run_init_operation` and any coupled-init success report.
- Owner: none yet.
- Ownership: open. The answer moves this duty to the adoption-orchestration work only with an
  equivalent acceptance check there, and section 14 has no check for the standalone `opf init` CLI
  wiring or its coupled-init success report.
- Disposition: open
- Successor: none

## Obligation: Migrate/Move

- Requirement: the parent name covers three separate meanings that `OPF-SPEC.md` sections 14.2 and
  5.4 keep apart; each is accounted for below.
- Implementation: see the split children.
- Generation: see the split children.
- Tests: see the split children.
- Not done: see the split children.
- Owner: see the split children.
- Ownership: open. By the answer that an entry closes only when every obligation has evidence or an
  accepted successor, this entry stays open while its three children are open.
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
- Owner: none yet.
- Ownership: open. Section 14.1 places the import completion check in post-adoption import, which
  the backlog tracks as separate work (`OPF-IMPORT-COMPLETION`); the adoption-orchestration work has
  no equivalent acceptance check beyond the completion check 3 preservation of a migrate-disposed
  source, so the transfer answer keeps this duty open.
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
- Owner: none yet.
- Ownership: open. The transfer answer keeps a duty open when the adoption work has no equivalent
  acceptance check, and section 14.1 has none for a Move's relocated bytes or its collision rule.
  Completion check 6 (retirement readiness) covers only retire-disposed files at no managed
  destination and archived occupying sources, and check 3 covers preserved preimages under
  `.working/archive/adoption/<run-id>/`, not the bytes at a Move destination. Section 14.2 runs the
  relocation only after the green completion check, so no completion check can verify the
  relocated bytes, and none checks that an occupied move destination is a collision, never an
  overwrite.
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
- Owner: none yet.
- Ownership: open. Whole-store relocation is `opf migrate --store` (section 5.4), not an adoption
  step; section 14.1 only rebinds an approved plan's paths after a relocation, so the
  adoption-orchestration work has no equivalent acceptance check and the transfer answer keeps this
  duty open.
- Disposition: open
- Successor: none
