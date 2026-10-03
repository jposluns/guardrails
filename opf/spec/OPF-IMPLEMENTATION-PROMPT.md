# OPF implementation prompt: build OPFiles natively from its specification

This document is a prompt. Give it to an AI coding assistant working in its own project, and that
assistant can implement OPF (OPFiles, the operational-files standard) natively in that project. It
is a guide to the specification, not a second specification: every requirement below points to the
section of the specification that states it, and the specification wins wherever the two seem to
differ. Everything from the next heading on is addressed to the assistant.

## Your task

You are going to implement OPF in the project you are working in, from the written specification,
as a fresh start:

- **Native.** Write your own implementation in your project's own language and conventions. Do not
  import, vendor, copy or translate code from the `jposluns/guardrails` repository, including its
  `opf/tools/` reference tooling, its CI recipe and its workflow template. Read them only where this
  prompt tells you to, to understand behaviour the specification states.
- **Fresh.** Start a new, empty store. Do not import, migrate or convert any existing record, note,
  backlog, changelog history or older OPF store into it. The store's `import_status` stays `"none"`
  and its imported series stays empty. The synthetic older-store fixtures that step 8 builds to test
  the store upgrade are throwaway test stores, not imports, and never enter your project's store.
- **Fresh start needs free destinations.** Initialization refuses when `.working/` exists or when
  `CHANGELOG.md` or `VERSION` already sits at the product root (step 9). Many real projects already
  have one of those files. For them the specification's only route is adoption under section 14,
  which this prompt puts out of scope, so check this with the maintainer before step 1.
- **From the specification.** Where this prompt and the specification differ, follow the
  specification and report the difference. Where the specification is silent or open, do not invent
  behaviour and present it as the standard's: follow the procedure in "Where the specification is
  silent or open" below.

Work through the implementation plan in order. Each step ends with acceptance checks, and each
check uses only what that step and the steps before it build; no check depends on a later step. Do
not start the next step until the current step's checks pass, and report any check you could not
run as not run, never as passed. Until step 9 (initialization) exists, the tests of steps 3 to 8
build their throwaway stores themselves, from sections 4 and 9 of the specification, with your own
canonical emitter (step 1) and, from step 5 on, your own renderer; the QUICKSTART notes that a
store can be built by hand from those sections.

## Read the specification first

Read these files in full before you write any code, at the pinned commit
`5d8d0fc7450c016ff40d129ad844e5547f699e83` of `jposluns/guardrails`. Read them from the pinned URLs,
not from a branch, so the text cannot change under you. After downloading each file, compute its
SHA-256 and compare it with the value given here; if any value differs, or a file cannot be fetched,
stop and report it. Do not work from memory or from a summary, this one included.

| File | Role | SHA-256 at the pinned commit |
|---|---|---|
| `opf/spec/OPF-SPEC.md` | The authoritative specification | `ec731c78ca75233de3cc0127425afc220b5c52b55110f9feb1a2e1b6d423cd02` |
| `opf/spec/OPF-QUICKSTART.md` | A short orientation; the specification governs | `88f04cc0d54a748c96e0870bd7b35cf160e52935e0400c45623e5b694021444e` |
| `opf/spec/OPF-INIT-D2B.md` | The coupled initialization contract | `4b84f78ec791853cb78451535cde86650060628bc5f48f1f55a6f0d6f87b26ec` |
| `opf/spec/OPF-INIT-D2B-REVIEW.md` | The review register for that contract | `5b29499173ed8ad47607d3d604bb77e59cd47a639f35046fb4815e241a794ffa` |
| `opf/enforcement/ci/opf-ci.sh` | The reference CI recipe (read for behaviour; do not copy) | `0036e97e68163c6c6df6f3b3260cf5c38e19a0d3d8a3bb713afef2355a2f8104` |
| `opf/enforcement/ci/github-actions.yml` | The reference CI workflow template (read; do not copy) | `f19ac5603f3b391295c64abde679843b29e20746f3d85d5b2e092ad210a52fe5` |
| `opf/prompt-pack/pack.toml` | The prompt-pack manifest | `69f62d00fa86a2d19576717df0356ddcbd1c56367586199250b28d370f90b16a` |

Each file's raw URL has this form, with the file's path from the table in place of `<path>`:

```text
https://raw.githubusercontent.com/jposluns/guardrails/5d8d0fc7450c016ff40d129ad844e5547f699e83/<path>
```

For example, the specification itself is at
`https://raw.githubusercontent.com/jposluns/guardrails/5d8d0fc7450c016ff40d129ad844e5547f699e83/opf/spec/OPF-SPEC.md`.

Facts about these sources that shape the work:

- The specification describes itself as a draft. Its opening status paragraph says the schemas and
  the reference tooling ship in later releases, and the opening of section 9 (The manifest) says
  the schema release that follows the specification is normative. Several formats in the
  specification are therefore illustrative, and some details are left open. This prompt lists the
  open points it found; you may find more.
- Section 4.2 (Layout overview) says that the 1.3.0 requirements in sections 4.2, 6.1, 8, 9.2, 11,
  12 and 14 are a target contract, which does not claim that the reference tooling has activated
  adoption, imported-series validation or the import writer, and that "Activation MUST include the
  tested upgrade in section 9.2 and deterministic doctor coverage before a writer accepts the new
  format". This prompt therefore requires the section 9.2 upgrade from 1.2.0 to 1.3.0, tested on
  synthetic older-store fixtures that you build yourself, before any writer of yours accepts the
  1.3.0 format (step 8). The same section says the
  homes-2 contract (`spec_version = "2.0.0"`, `[opf].homes = 2`) is a separate, later activation,
  and that the section 9 manifest example describes the 1.3.0 target on legacy homes.
- The prompt pack manifest at the pinned commit (`format = "opf.prompt-pack/v1"`,
  `version = "0.1.0"`) lists no members. The pack has two roles in the specification, and this
  fresh start needs neither. Its prompts drive post-adoption import (section 14.1, One approval
  and completion), which a fresh start does not perform, so there are no import prompts to run.
  Separately, section 14.1 says every `opf.adoption.plan/v2` plan MUST bind "the version and digest
  of the prompt pack". This prompt reads section 14 as not requiring an adoption plan for a scaffold
  with nothing to disposition (open point 14); if the maintainer rules otherwise, the plan binds the
  `version` and `digest` values of the pinned `pack.toml`.
- Cite the specification as this prompt does: by file and section heading, for example
  "OPF-SPEC.md, section 8.2 (ID namespaces and counters)".

## What you must not do

- Do not import, vendor, copy, translate or execute this repository's code as part of your
  implementation, and do not make your implementation depend on it at run time. This covers
  everything under `opf/tools/`, `opf/enforcement/` and `tools/`. Writing your own equivalent of
  the CI recipe is required; copying it is not allowed.
- Do not import existing records. In your project's store, do not write records with
  `actor.kind = "importer"`, do not add rows to the imported series (`<type>.imported.index.toml`,
  `worklog.imported.toml`; steps 3 and 9 create those files empty, and they stay empty), do not set
  `import_status` to anything but `"none"`, and do not create evidence under `.working/imported/`.
  Synthetic records in throwaway test stores, such as the step 8 fixtures, are test data and are
  not covered by this rule.
  Pre-existing operational files in your project stay where they are unless the maintainer decides
  otherwise (see step 9).
- Do not claim conformance unless every item of the final conformance checklist passes, and even
  then use only the scoped vocabulary of OPF-SPEC.md, section 16 (Conformance vocabulary and
  claims). Never write an unqualified "OPFiles conformant" or "OPF conformant", and say that the
  claim is self-asserted.
- Do not present a choice you made in a gap of the specification as a requirement of the standard.
  A requirement the specification states as MUST is never optional; if this prompt seems to make one
  optional, follow the specification and report the difference.
- Do not author records that only a maintainer may author or ratify (see step 14), do not weaken
  the enforcement posture, and do not publish a changelog summary without the maintainer's
  curation.
- Do not let your store's base-required schema vocabulary name a particular adopter, operator,
  profile, internal system, endpoint, tier vocabulary or command (OPF-SPEC.md, section 15,
  Genericization boundary); this repository and its maintainer are among the names that excludes.
  `actor.kind` carries only the portable categories, and identity detail lives in `actor.id` or
  extensions.

## Choices to make before you start

These choices follow from the specification and from the fresh-start scope. Confirm them with the
maintainer before step 1, and keep the confirmation in your implementation notes.

- **Specification version.** Implement base spec 1.3.0 on legacy homes (homes 1): write
  `spec_version = "1.3.0"` and no `homes` key. Do not declare `homes = 2` or
  `spec_version = "2.0.0"`; both are reserved for a later activation (section 4.2, Layout overview;
  section 9.2, Store schema upgrades). Omit the `[opf].worklog` key or set it to `1`; `2` is
  reserved and refused (section 9, The manifest). Before any writer of yours accepts this format,
  implement and test the section 9.2 upgrade from 1.2.0 to 1.3.0 (step 8), as section 4.2 requires.
  Your validator refuses a `spec_version` above 1.3.0 as a fail-closed INVALID finding naming the
  tooling-upgrade remedy, keeps the reserved homes-2 declaration recognized under legacy homes-1
  grading (section 9.2, last paragraph), and grades a declaration below 1.3.0 as open point 21 says.
  Note that section 9.2 says the 1.2.0 reference validator refuses a 1.3.0 declaration as above its
  ceiling, so the reference tooling at the pinned commit will not validate your store; that is
  expected and is not a fault in your store.
- **Location.** Use the default in-repo store: `.working/` in your project, with the pointer
  `.opf.toml` holding `[store] target = "dir:."` (section 4.3, The pointer; section 5.2, Location
  is a configuration). Relocation (`opf migrate`) is optional and out of this prompt's minimum.
- **Layout.** Use `layout = "inline"` (section 9, The manifest, storage layout). `per-record`
  needs the concurrent-operation module.
- **Modules.** Leave every module off (`governance`, `delivery_assurance`, `operational_policy`,
  `concurrent_operation`). Section 8.1 (Type taxonomy and placement) says modules ship default-off,
  section 8.5 (Transition rules) gives module types only in outline, and section 14.1 says adoption
  MUST refuse to enable a record type the writer cannot author.
- **Profiles.** Declare no profile. The `[profiles.aiqt]` table in the section 9 example is
  optional and ignored by base-only tooling (section 9.1, Base standard and profiles).
- **Posture.** `posture = "required"` and `import_status = "none"` (section 11, Enforcement
  posture, "Defaults").
- **Initialization.** Implement coupled initialization, `opf.init.coupled/v1`, in full (step 9;
  OPF-INIT-D2B.md). OPF-INIT-D2B.md identifies the older source-only behaviour as
  `opf.init.source-only/v1` and defines it only as the shipped tooling's existing behaviour, so it
  cannot be implemented from the specification; do not implement it as a substitute and do not
  claim that identifier. Requiring coupled initialization is this prompt's scope choice, not a
  requirement of the standard: section 9.2 says `init.toml` "is a managed leaf when present and is
  never required, so a store without it stays valid", and section 1 says "A project can conform to
  this specification with hand-maintained files and its own checks." This prompt makes it part of
  its own checklist (items 6 and 11) because the store identity of open point 19 rests on it.
- **Platforms.** Wire all four supported platforms, Claude Code, Codex, Gemini CLI and Cursor,
  not only the one you run on (step 14; section 14.1).

## Where the specification is silent or open

The specification leaves the points below open, or states them only as examples. For each one,
choose the narrowest deterministic behaviour that satisfies every rule the specification does
state, write the choice in your implementation notes with the section it fills, and ask the
maintainer to ratify it. Where this prompt proposes a choice, it is labelled as this prompt's
choice or reading, and it is still yours to confirm. Once the store exists, record each open
question as a `pending_decision` record; only the maintainer records the ruling. In your
conformance report, list these choices as implementation choices, not as requirements of the
standard. None of them makes a stated MUST optional.

1. **Closed schemas per type.** Section 8.3 (The record envelope) says schemas are closed and types
   add their own fields, but the specification names only some type fields: `severity` on a
   finding (Appendix A), the resolution bundle `decision`, `decided_at` and `decided_by` on a
   pending_decision, the `delivery` bundle on a contribution, `decision` on a maintainer_decision,
   `context` and `rationale` on a preference_pattern (section 8.5). It does not name the field that
   holds a block's scoped record IDs, the fields of an autonomous_decision (it describes "the
   classification basis, the action, links"), or the severity vocabulary.
2. **Index file shape.** Appendix A shows records as `[[record]]` tables. The specification does
   not say whether an index file carries other top-level keys (version.toml and worklog.toml carry
   `schema = 1` in Appendices B and C), or what an empty index contains.
3. **counters.toml shape.** Section 8.2 names the keys (`BI`, and the quoted key `"imported:BI"`
   for the imported series) but shows no complete file.
4. **coverage_digest canonicalization.** Section 6.1 says the exact canonicalization is fixed by a
   later schema release and MUST be deterministic and cover the entries' full content. Appendix B
   shows the `sha256:<64 hex>` form.
5. **Freeze digest algorithm.** Section 7.2 defines exactly which bytes are digested; the
   algorithm is shown only by the `sha256:` values in Appendix B.
6. **Generated header text.** Section 10.3 lists what the header must state (generated, do not
   edit, source paths, schema and generator versions, a source-set digest, the regeneration
   command, no timestamp) but not its exact wording or the source-set digest method.
7. **Composed view contents.** Section 10.1 names the composed views and section 10.2 bounds the
   transforms, but the specification does not say which records and columns each view shows,
   beyond the block join and the decision-resolution join.
8. **VERSION with no release, and its header.** Section 6.1 says `VERSION` holds the latest
   release's version as exact bytes; it does not say what a ledger with no release renders. Section
   10.3 says every generated file opens with a do-not-edit header, and the specification does not
   say how that applies to `VERSION`.
9. **Release cut.** Sections 6.1, 6.2 and 7.2 define the release row and the freeze, but the
   command vocabulary in section 1 names no release verb. Section 7.1 also makes every released
   version fall in exactly one non-superseded summary row with exactly one matching heading in
   `CHANGELOG.md`, so a release row alone fails range coverage until its summary row and heading
   exist, and the specification does not say how they land together. This prompt's choice: the cut
   appends the `[[release]]` row, a `working` `[[summary]]` row for that version, and the bare
   `## <version>` heading with no prose, in one operation, and the maintainer curates the prose
   before publication (section 7.3).
10. **Exit codes and flags beyond `opf record`.** Section 8.8 fixes `opf record` at exit 0 or 2,
    and OPF-INIT-D2B.md states when coupled init may exit 0. The reference CI recipe uses 0, 1 and 2
    for `doctor` and `render --check` (step 13). The specification fixes no other flags or exit
    codes. This prompt's choice for `opf record`: a final validation that fails after the change is
    written exits 2 (step 10), since section 8.8 names only 0 and 2 for the verb.
11. **Journal and lock locations.** Section 8.8, item 6 requires a crash-durable journaled
    transaction and says where the reference tooling keeps it: `.aiqt/record/journal` at homes 1
    and `.working/journals/record/journal` at homes 2. It does not require either location of
    another implementation. OPF-INIT-D2B.md requires an outer mutex and an owned lock but does not
    say where they live. Section 15 says that in homes 2 `.aiqt/` is AIQT-owned material, not an OPF
    state home, section 4.2 places the homes-2 operation lock and coupled-init substrate in the git
    common directory, and section 14.2 says "homes 1 retains its legacy journal paths". Section 4.2
    calls `journals/` a reserved store-tree control area, but the homes-1 registration it requires
    of activated 1.3.0 tooling lists only the imported managed leaves,
    `.working/archive/adoption/<run-id>/`, `.working/archive/moved/` and
    `.working/imported/<kind>/<run-id>/`. This prompt's inference, which the specification does not
    state: a homes-1 journal under `.working/journals/` might therefore fail path containment. This
    prompt's choice: keep the journal, the outer mutex and the owned lock in one machine-local
    directory inside the git common directory, not under `.working/` and not under `.aiqt/`.
12. **The lease file in an in-repo store.** Section 5.7 (The store consistency contract) defines
    the lease and its contents but does not say whether `lease.toml` is committed in the in-repo
    pattern, or its field names (the QUICKSTART notes call the file illustrative).
13. **Rotation thresholds.** Section 12 (Rotation, archive, and retention) allows rotation on
    manifest-declared age or size thresholds but names no manifest keys.
14. **Adoption receipt and plan for an empty start.** Section 11, "Defaults", names scaffolding and
    clean-start adoption separately; section 14 attaches the adoption receipt and its outcome-event
    chain to adoption; section 14.2 says `opf init` MUST refuse undispositioned foreign content.
    This prompt's reading: a scaffold with nothing to disposition is not an adoption, so it writes
    no adoption receipt and no `opf.adoption.plan/v2` plan. The specification gives no receipt
    format in any case; do not invent one.
15. **The `.working/README.md` deliverable.** Section 4.2 lists it as an "ownership and
    regeneration note" but does not say whether it is a rendered view.
16. **Worklog entries written by the record verbs.** Section 8.8 fixes the opening line of their
    `detail` but not their `kind`; section 6.2 lets a manifest register more kinds but names no key.
17. **Initialization formats.** OPF-INIT-D2B.md lists the field names of its plan, outcome and
    bootstrap formats in a brace notation, and says that its first slice "freezes these formats
    only; it does not implement their producers, persistence, or resume consumers". It does not
    give value types or a file encoding for the plan and outcome; the meaning of `binding`, `sets`,
    `publication_boundaries` and `recovery_policy`; the Keep fields beyond `operation` and
    `schema`; what the outer mutex and the owned lock are; or which files belong to the source set
    S and the view set V. This prompt's choice for the last: every init-created file that is not a
    rendered view, `CHANGELOG.md` included, is a source; every declared view, `VERSION` included,
    is a view.
18. **Canonical TOML serialization.** Section 8.8 (item 2) and section 9.2 require a canonical
    new-document emitter whose output reproduces a canonical file's bytes, but the specification
    defines no serialization: key order, quoting, inline versus standard tables, array layout and
    spacing are all unstated. Open point 4 covers only the `coverage_digest` canonicalization.
19. **Store identity for CI.** Section 14.1, completion check 4, requires that "the store resolves
    to the planned identity, and CI asserts store presence and identity so absence cannot pass as
    NOT-APPLICABLE", but the specification defines no identity value, and the reference CI recipe
    says "the CI receipt-identity comparison is a later release". This prompt's choice: the
    identity is the committed pointer's `target` together with the SHA-256 of the discovered
    machine store's `init.toml`, which coupled init writes once and nothing rewrites, and the
    expected values are pinned in the CI job definition, outside the store (step 13).
20. **What "tracked" means right after init.** Section 5.1 makes an untracked or ignored `.working/`
    tree a hard failure, while OPF-INIT-D2B.md requires init-created sources staged and initial
    views unstaged with "the whole store is VALID". This prompt's reading: the tracked-store check
    fails when any path under `.working/` is ignored or any machine-store source file is absent from
    the git index (staged or committed); a generated view may be absent from the index until the
    first commit after init.
21. **Upgrade scope and older declarations.** Section 4.2 says "Activation MUST include the tested
    upgrade in section 9.2 and deterministic doctor coverage before a writer accepts the new
    format." That is not open: step 6 gives the doctor coverage, and step 8 implements and tests the
    1.2.0 to 1.3.0 upgrade on synthetic fixtures before initialization (step 9) and the record
    writer (step 10) accept the format. Three details around it are open. First, section 9.2 says
    "Earlier stores compose their applicable deltas with this delta" and gives the 1.0.0 and 1.1.0
    deltas; this prompt's reading is that the activation prerequisite is the 1.2.0 to 1.3.0 upgrade,
    and that your upgrade refuses a 1.0.0 or 1.1.0 store, naming the deltas it does not implement,
    unless you implement and test those deltas too. Second, section 9.2 requires an unresolved
    legacy import to be reconciled under its original contract and a completed one to keep
    `import_status = "complete"`, substantiated by preserved legacy run evidence (sections 11 and
    14.1), but the pinned sources do not define that evidence's "retained legacy format"; this
    prompt's reading is that your upgrade refuses, before any write, a store whose `import_status`
    is not `"none"`, and your conformance report lists that as an exclusion. Third, section 9.2
    says how a validator treats a `spec_version` above its own (a fail-closed INVALID finding naming
    the tooling-upgrade remedy) and that "Only the reserved homes-2 declaration of section 4.2 stays
    recognized, and that recognition keeps legacy homes-1 grading"; it does not say how a 1.3.0
    validator grades a store that declares a lower version. This prompt's reading: a fail-closed
    INVALID finding naming the store-upgrade remedy (`opf upgrade`, step 8).
22. **The writer's pending cannot-evaluate outside the verb, and the comparison snapshot.** Section
    8.8, item 7 says doctor compares a status change "with the prior committed snapshot", "MAY
    grade that change cannot-evaluate until the change is committed", and that "The verb's render
    and final doctor MUST accept that cannot-evaluate only for exactly the record and the from and
    to statuses it has just written, never a finding and never any other cannot-evaluate, and the
    verb MUST report it as pending until commit. `opf doctor` itself is unchanged and still reports
    it until then." It does not say which commit is the prior committed snapshot, or how a gate
    other than the verb (the standalone render write mode, the pre-commit check, CI) learns which
    record and statuses the verb wrote. This prompt's reading of the snapshot: the prior committed
    snapshot is the store repository's `HEAD` commit, and the snapshot under test is the working
    tree, or the staged snapshot in the pre-commit check. This prompt's choice for the other gates:
    a gate accepts a cannot-evaluate only when the validator reports it as this pending kind, naming
    one record and its from and to statuses, and the snapshot under test carries, in the unreleased
    worklog tail and absent from `HEAD`, the writer's lifecycle entry
    `opf-record transition <ID> <from> -> <to>` for exactly that record and those statuses; never a
    finding and never any other cannot-evaluate. Section 8.8 calls that worklog entry
    "informational, never evidence" for rejection provenance; here it only selects which pending
    cannot-evaluate a gate tolerates, it proves nothing about the actor, and a canonical hand edit
    that adds a matching entry passes, which is the canonical hand edit residual of section 14.1.
    Under this reading CI, which checks a committed revision, compares it with itself, so the
    pending cannot-evaluate cannot arise there and CI does not re-evaluate the transitions inside
    the commits it checks (step 13).
23. **Same-act supersession of a handoff or a contribution.** Section 8.5 says "Posting a new
    handoff MUST supersede the previous in the same act", and of a contribution "A re-send MUST be a
    new record linking `supersedes`; the superseded record records `superseded`." Section 8.4 says
    "where the type records it, the superseded record's terminal state MUST reflect it". Section 8.8
    gives no operation for this: `create` writes "one new record" with an allowed delta of "the new
    rows appended, the counters advanced", and `transition` changes one record's status. This
    prompt's reading, the smallest it found consistent with sections 8.4, 8.5 and 8.8: a single
    writer operation that creates the new record linking `supersedes` to its predecessor and moves
    the predecessor to `superseded`, in one journaled transaction under the full section 8.8
    operation sequence; its allowed delta is the `create` delta plus exactly one `transition` delta
    for the predecessor; the `/proposed` rule applies to the predecessor's move as to any other
    terminal transition, so an assistant or automation lands `superseded/proposed` with
    `proposed_from`; and it appends one lifecycle worklog entry per changed record (section 6.2,
    "one entry per change"). The specification does not say whether a `superseded/proposed`
    predecessor satisfies "reflect it", or what happens when a maintainer rejects it back to
    `current` while another handoff is `current`; this prompt's reading is that such a rejection
    refuses before anything is written, since the result would break the one-`current` rule. Ask the
    maintainer to rule on all of this before you enable the operation.
24. **Pointer and sync-target agreement for the in-repo default.** Section 11 lists "pointer and
    sync-target agreement (the committed pointer, the manifest's recorded sync target, and the store
    repository's actual remote agree; section 5.6)". In the in-repo default the pointer is
    `dir:.`, the manifest's `sync_target` is empty, and the store repository is the product
    repository, which usually has its own `origin` remote. The specification does not say what
    "agree" means there. This prompt's reading: with `target = "dir:."` and an empty `sync_target`,
    the check passes when the pointer resolves to the product root itself and does not compare the
    product repository's remotes, which OPF does not push; any other combination, such as a
    non-empty `sync_target` with an in-repo pointer, is a finding.

## The store you are building

This summary orients you; the cited sections are the authority.

Two roots (section 4.1, Two roots): the product repository root holds the pointer `.opf.toml` and
the public deliverables `CHANGELOG.md` and `VERSION`; the store repository root holds `.working/`.
In the default in-repo pattern they are the same repository.

```text
<product root>/
  .opf.toml                  committed pointer (section 4.3)
  .opf.local.toml            optional machine-local override, never committed (section 4.3)
  CHANGELOG.md               curated public changelog (sections 6.3, 7, 10.4)
  VERSION                    deterministic render of version.toml (sections 6.1, 10.1)
  .working/
    README.md                ownership and regeneration note (section 4.2)
    WORKLOG.md, BACKLOG.md, DECISIONS.md, ...   generated views (section 10.1)
    DECISIONS.toml           machine projection (section 10.5)
    toml/                    the machine store, found by discovery (sections 4.4, 4.5)
      manifest.toml          control document and discovery marker (section 9)
      counters.toml          per-series, per-namespace high-water marks (section 8.2)
      version.toml           release ledger (section 6.1)
      worklog.toml           durable operational record (section 6.2)
      init.toml              bootstrap provenance of the coupled init (section 9.2)
      <type>.index.toml      one per enabled baseline type except worklog (sections 4.2, 8.1)
      <type>.imported.index.toml   empty imported leaf, one per enabled type except worklog
      worklog.imported.toml  the worklog's empty imported leaf (section 4.2)
```

Casing is part of the contract (section 4.6, Casing convention and rationale): every file under
the machine store is lowercase machine source; every generated deliverable is uppercase and is
never hand-authored truth.

## Implementation plan

### Step 1: foundations

Build the primitives every later step relies on.

- A TOML reader, and a canonical TOML emitter whose output for an unchanged parsed model
  reproduces the file's bytes exactly. The writers refuse any file that fails this
  byte-reproduction test, such as one carrying comments (section 8.8, Authoring operations, item 2;
  section 9.2, Store schema upgrades). The serialization itself is yours to fix (open point 18).
- A clock reader. Every `created_at`, `updated_at`, `date`, `decided_at`, `sent_at`,
  `receipted_at`, lease acquisition time and release date is read from the clock at the event, in
  RFC 3339 UTC (sections 5.7, 6.1, 8.3, 8.8). Never compose or guess a timestamp.
- SHA-256 with the `sha256:<64 lowercase hex>` text form (Appendix B).
- Path containment: canonical contained relative paths only; no empty, `.` or `..` components, no
  absolute, drive or backslash forms, no control characters or line separators (section 4.2).
- Outcome reporting with three outcomes: valid, finding, and cannot-evaluate. Fail closed: an input
  a check is meant to cover that cannot be read, parsed or resolved yields failure or
  cannot-evaluate, never a clean pass (section 3, Design principles, "Fail closed").

Acceptance checks:

- Re-emitting a parsed canonical file gives identical bytes; a file with a comment or a
  non-canonical layout is detected as non-canonical.
- An unreadable or malformed input produces cannot-evaluate in a unit test, never a pass.
- A path with a `..` component, a backslash or an absolute form is refused.

### Step 2: store resolution

Implement resolution exactly as sections 4.3 (The pointer), 4.4 (The machine store) and 4.5
(Manifest discovery) state:

- Read `.opf.local.toml` first, then `.opf.toml`. With neither, try `.working/` at the product
  root; with no store there either, report that there is nothing to operate on and that `opf init`
  is the remedy.
- A pointer that exists but does not resolve (unreachable target, or no valid manifest there) is
  cannot-evaluate. Never fall back silently to the default location.
- Relative `dir:` paths resolve against the product root; any other path in a pointer must be
  absolute. Targets follow section 5.5 (Target syntax).
- Find the machine store as exactly one immediate subdirectory of `.working/` whose
  `manifest.toml` declares `standard = "opf"` in its `[opf]` table, trying `toml` first. Do not
  hardcode the name `toml`. Zero or more than one match is cannot-evaluate.
- The names `imports`, `imported`, `archive`, `staging` and `journals` are reserved; a machine
  store with one of those names fails closed (section 4.4). Discovery examines `manifest.toml` in
  every immediate subdirectory, including `journals/`, fails closed on a reserved-name match or
  ambiguity, and reads no deeper there (end of section 4.2).
- `.opf.local.toml` is never committed and is ignored by version control; the committed pointer
  must be safe to publish (section 4.3).

Acceptance checks:

- A pointer naming a missing directory gives cannot-evaluate, even when a valid `.working/`
  exists at the product root.
- Two subdirectories with valid manifests give cannot-evaluate; renaming the machine
  subdirectory still resolves.
- A manifest with a mistyped `standard` value is not discovered, and the result is cannot-evaluate,
  not an empty store (section 17, Residual coverage disclosures).

### Step 3: manifest and control ledgers

Implement the files in sections 9 (The manifest), 8.2 (ID namespaces and counters), 6.1
(version.toml, the version and release ledger) and 6.2 (worklog.toml, the durable operational
record).

- **manifest.toml.** The `[opf]` base table holds `standard = "opf"`, `spec_version`, `layout`,
  `posture` and `import_status` (section 9 example; section 9.1). Also `[store] sync_target`
  (empty for the in-repo default), `[modules]`, one `[types.<name>]` table per enabled type with
  its `namespace`, `[providers.<name>]` tables, `[unmanaged] paths`, the view map
  `[views."<NAME>"]` with `kind` (`composed`, `deterministic` or `projection`), `sources` and
  `target`, `[deliverables."CHANGELOG.md"]` with `kind = "curated"`, `[archive] period`, and
  `[vendors] registered`. Treat the section 9 example as a guide to the shape: the specification
  says the later schema release is normative.
- **Providers.** Declare `local-directory` and `generic-git-remote`, which section 5.6 (The
  provider registry) says "ship as the universal fallbacks", with the handler and roles of the
  section 9 example. An in-repo store needs no host provider such as `github`.
- **Enabled types.** The twelve baseline types and their namespaces are in the section 8.1 table:
  backlog_item BI, done DN, worklog WL, finding FN, pending_decision PD, autonomous_decision AD,
  block BL, handoff HO, reference RF, contribution CN, maintainer_decision MD and
  preference_pattern PP. `transaction` (TX) is excluded and reserved, `CL` is reserved and
  unassigned, and `legacy_fragment` (LF) MUST NOT be scaffolded.
- **counters.toml.** Independent monotonic high-water values per series and namespace: a key per
  clean namespace (for example `BI`) and a quoted key per imported namespace (for example
  `"imported:BI"`). A genuinely first adoption MAY start at zero; counters are never reset, IDs are
  never reused, and rotation, index rewrites and relocation never touch the file (section 8.2).
  Section 9.2 shows imported counter rows added at zero during the 1.3.0 upgrade; write them at
  zero at initialization so each series has its own value.
- **version.toml.** `schema = 1`, then append-only, immutable `[[release]]` rows (`version`, the
  SemVer version string, unique in the ledger; `date`; `worklog_span`; `coverage_digest`; and the
  optional `imported` flag, which a fresh store never sets) and `[[summary]]` rows (`covers`,
  `status`, `digest`, `superseded_by`). Summary rows hold digests and ranges only, never prose
  (section 6.1; Appendix B).
- **worklog.toml.** `schema = 1`, then `[[entry]]` rows with `id`, `date`, `actor`, `kind`,
  `summary`, optional `detail`, `links` and `refs` (section 8.3, reduced envelope; Appendix C). The
  base kinds are `added`, `changed`, `fixed`, `removed`, `security`, `docs` and `infra`
  (section 6.2). Clean entries carry no `status` key in Appendix C; their status is fixed as
  `recorded` and never takes `/proposed` (section 8.4).
- **Imported leaves.** Create `<type>.imported.index.toml` for every enabled type except
  `worklog`, and `worklog.imported.toml` for the worklog; section 4.2 says "`worklog` uses
  `worklog.imported.toml` instead of an imported index". Each is create-only and empty, created in
  the same act as its clean index or ledger (section 4.2). They stay empty in a fresh store.
- `version.toml` and `worklog.toml` are always single files written under the store lock, in either
  layout (section 9, storage layout).

Acceptance checks:

- The manifest reader accepts this step's manifest. A wrong `standard` value leaves the store
  undiscovered, which is cannot-evaluate (section 4.5). A `spec_version` above 1.3.0 is a
  fail-closed INVALID finding naming the tooling-upgrade remedy, while the reserved homes-2 pair
  (`spec_version = "2.0.0"` with `[opf].homes = 2`) stays recognized and is graded under legacy
  homes-1 rules, not refused (section 9.2, last paragraph). A declaration below 1.3.0 is graded as
  open point 21 says.
- Every enabled type other than `worklog` has exactly one clean index and one imported index; the
  worklog has `worklog.toml` and `worklog.imported.toml`, and no `worklog.index.toml` or
  `worklog.imported.index.toml` exists.
- counters.toml has a value for every clean and imported namespace of every enabled type.

### Step 4: record model

Implement section 8 (Record model) in full for the baseline types.

- **IDs.** Clean IDs are `<NS>-<n>`, imported IDs are `imported:<NS>-<n>`, and the whole grammar is
  `^(?:imported:)?[A-Z]{2}-[1-9][0-9]*$`; the namespace must name the record's enabled type
  (section 8.2).
- **Envelope.** Clean records carry `id`, `type`, `status`, `title`, `created_at`, `updated_at`
  and `actor.kind` (`maintainer`, `assistant`, `automation` or `importer`), with optional
  `proposed_from`, `actor.id`, `summary`, `links`, `refs` and registered `x-<vendor>` tables. The
  schema is closed: an unknown key fails validation unless it sits under a registered vendor table
  (section 8.3; section 8.7, Extensions). `proposed_from` is legal only on a `/proposed` status and
  only naming a legal predecessor state for the type (section 8.3).
- **Status grammar.** `state` or `state/proposed`. Each type declares a closed state set with one
  initial state and one or more terminal states (section 8.4, The status grammar). Transitions per
  type are in the section 8.5 table.
- **The `/proposed` rule.** A terminal transition by an `assistant` or `automation` actor lands
  with `/proposed`; so does entering a gated state (`block` `active`, `contribution` `sent`,
  `preference_pattern` `active`). Only a maintainer ratifies (removes the qualifier) or rejects
  (returns to the recorded `proposed_from` state, with a reason). A `/proposed` record is not
  terminal: gates and views treat it as unfinished and awaiting ratification. Created-terminal
  factual or ACT types (`reference`, `autonomous_decision`, `maintainer_decision`) carry no
  qualifier (sections 8.4 and 8.8).
- **Standing authorization.** Section 8.4 lets an adopter declare a standing authorization for a
  named contribution recipient, and "an absent or malformed declaration MUST fail closed, so gating
  stays on". This prompt declares none, so every `sent` by an assistant or automation stays gated.
  The writer never reads such a declaration in any case (section 8.8).
- **Per-type rules (section 8.5).** Among them: blocked-ness is never stored, it is derived; a
  ratified `done` backlog item has its one-to-one `done` receipt linked `receipt_of`; a
  finding's severity is graded at or after the fix decision, never before; a pending_decision's
  resolution bundle is all-or-none, with exactly one current effective resolution per
  supersession chain; posting a new handoff supersedes the previous one in the same act, and at
  most one `current` handoff exists; a contribution's delivery bundle fields appear only at the
  states the table allows, and a re-send is a new record linking `supersedes` while the superseded
  record records `superseded`; a `maintainer_decision` takes `actor.kind` `maintainer` (or
  `importer`, which a fresh store never uses). Section 8.8 gives the writer no operation for the
  handoff and contribution supersessions; that gap is open point 23.
- **No resurrection and supersession.** A record in an unqualified terminal state never re-enters
  a working state; a revived concern is a new record linking the old one; supersession is a
  `supersedes` link, and where the type records it, the superseded record's terminal state
  reflects it (section 8.4).
- **Links and refs.** `rel` comes from the closed vocabulary `supersedes`, `resolves`,
  `remediates`, `receipt_of`, `corrects`, `follows`, `relates`, `exemplifies` and `derives_from`;
  `exemplifies` targets a preference_pattern. `refs` are `{kind, locator, note}` with `kind` one of
  `path`, `url` or `doc` (section 8.6, Links and reference capture).
- **Actionability.** A backlog item is actionable when it is `open` or `active` and no
  unqualified `active` block scopes it (section 8.5, "Actionability").

A stored record does not show which actor made its last transition: section 8.8, item 7 says the
history comparison "sees only the prior snapshot's type and status, which identify neither the
transitioning actor nor the pre-proposal state". The `/proposed` landing rule is therefore tested
at the writer, in step 10, not by record validation.

Acceptance checks, over records and snapshots the test builds:

- Record validation refuses: an ID in the wrong namespace, an unknown key, a `/proposed` worklog
  entry, a `proposed_from` on a status without `/proposed` or naming an illegal predecessor, a
  maintainer_decision with `actor.kind = "assistant"`, an open pending_decision carrying any part
  of its resolution bundle, a link with an unknown `rel`, and, given a prior and a current
  snapshot, a terminal record moved back to a working state.
- An assistant-proposed `active/proposed` block does not make its scoped item non-actionable.

### Step 5: views and deliverables (`opf render`)

Implement section 10 (Views and deliverables). The validator in step 6 uses this renderer for its
drift checks, so it comes first.

- Render every view the manifest declares. Composed views use only the closed transform
  vocabulary: filter on declared field predicates, sort on declared keys with ID as the final
  tie-breaker, group by a declared field, project declared columns, and exactly two joins, the
  block join and the decision-resolution link (section 10.2).
- Renders are byte-reproducible: UTF-8, LF line endings, stable ordering, no locale-dependent
  sorting, no wall-clock content, no network access and no model involvement. Every generated file
  opens with the header section 10.3 describes, and the header carries no timestamp.
- `DECISIONS.toml` is a projection (section 10.5): a leading TOML comment header, `schema = 1`,
  `projection = "decisions"`, four arrays of tables (one per source type, rows sorted by numeric ID,
  full base record with `links` and `refs`, `x-<vendor>` tables excluded), and a `[derived]` table
  with the `effective` and `superseded` pending_decision IDs.
- `VERSION` renders from `version.toml` into the product root (section 6.1; section 5.8, The
  public deliverables are identical across topologies).
- `CHANGELOG.md` is never rendered over: it is curated, carries no do-not-edit header, and opens
  with a short note that it is a curated summary of the worklog, gated on coverage and publication
  freeze (section 10.4).
- Provide a read-only check that reports drift and a write mode that renders. The reference recipe
  calls the check `render --check` and reads exit 1 as drift (`opf-ci.sh` header). The write mode
  gains its gate in step 6.
- **Declared views.** Declare `WORKLOG.md` and `VERSION`, both deterministic: section 6.2 says the
  worklog "generates the deterministic view `.working/WORKLOG.md`", and section 6.1 says `VERSION`
  is generated from the ledger and drift-gated. Section 9.2 notes that no composed view is
  required, and the specification names no further minimum. This prompt's choice: also declare
  the `DECISIONS.toml` projection and the composed `BACKLOG.md`, `DECISIONS.md` and
  `CONTRIBUTIONS.md`, which the section 9 example and the 1.1.0 upgrade delta in section 9.2
  declare.

Acceptance checks, over stores the test builds:

- Rendering twice gives identical bytes, under two different locales and time zones.
- Editing one byte of a rendered view makes the check report drift (exit 1), and re-rendering
  clears it.
- A proposed (`/proposed`) record appears in views as awaiting ratification (section 8.4).

### Step 6: the validator (`opf doctor`)

Implement the integrity layer of section 11 (Enforcement posture). Its roster: schema validity of
what exists; ID uniqueness across active, archive and staging; counter monotonicity (every ID
within its counter); bidirectional index reconciliation; transition legality and no resurrection;
the all-or-none resolution bundle; byte drift for every deterministic view, `VERSION` included;
worklog span tiling and frozen coverage digests; changelog range coverage; changelog freeze; archive
integrity; the tracked-store requirement against the resolved store; pointer and sync-target
agreement (open point 24 for the in-repo default); unmanaged-path containment; and path
containment.

- At `posture = "required"`, an unreadable, unparseable or unresolvable declared input is a
  failure, never an empty or clean result (section 11). `CHANGELOG.md` is a declared input.
- The tracked-store check fails hard on an untracked or ignored `.working/` tree; there is no
  gitignore fallback (section 5.1, Always a git repository). How it treats views that init leaves
  unstaged is open point 20.
- Transition legality compares the store with the prior committed snapshot (section 8.8, item 7);
  which commit that is, is open point 22, whose reading is the store repository's `HEAD`. A prior
  commit that is needed and cannot be read is cannot-evaluate, never "no change". Section 8.8,
  item 7 lets the validator "grade that change cannot-evaluate until the change is committed" and
  says "`opf doctor` itself is unchanged and still reports it until then"; report that pending kind
  distinctly, naming the record and its from and to statuses, so the gates below can recognize it.
- View drift uses the step 5 renderer for every declared deterministic view and projection,
  `VERSION` included (sections 10.5 and 11).
- The four imported-series checks named in section 8.3 (C-IMPORTED-SCHEMA, C-IMPORTED-IDS,
  C-IMPORTED-PROVENANCE, C-IMPORTED-SEGREGATION) join the roster at 1.3.0 (section 4.2), with
  C-CONTAINMENT recognizing the imported managed leaves and C-LINKS resolving links over both
  series (section 8.3). In a fresh store the imported series is empty, so they evaluate empty
  inputs. Treat a missing imported leaf as a finding: section 4.2 requires init to create every one
  (this is a reading of that rule, not a check the specification names).
- On homes 1, path containment recognizes as OPF control area the imported managed leaves,
  `.working/archive/adoption/<run-id>/`, `.working/archive/moved/` and
  `.working/imported/<kind>/<run-id>/` (section 4.2, "Activated 1.3.0 tooling MUST register on
  homes 1 ..."). C-EVIDENCE-ENUM does not run or read anything on homes 1 (section 4.2).
- The validator reads the record archive under the discovered machine store (section 12): ID
  uniqueness, the chain rule and archive integrity cover active and archived records together.
  With no archive tree nothing has rotated; an archive that is present and unreadable or malformed
  is cannot-evaluate.
- Adoption coverage (how much of the project has moved into the store) is a report only, never a
  gate, at every posture (section 11).
- Use the outcome vocabulary of the reference CI recipe for this command's exit status: 0 valid,
  1 finding, 2 cannot-evaluate (`opf-ci.sh` header). Provide a mode, as the recipe's
  `doctor --require-store` does, in which a repository with no store exits 2, so a removed store
  cannot pass as not applicable (section 14.1, completion check 4).
- Gate the render write mode: section 5.8 says `opf render` writes "only bytes that have passed the
  store's gates". This prompt's reading: the write mode refuses while the validator reports a
  finding or cannot-evaluate on anything other than view drift, which rendering itself remedies,
  and the writer's pending cannot-evaluate, which section 8.8, item 7 requires the verb's render to
  accept: "The verb's render and final doctor MUST accept that cannot-evaluate only for exactly the
  record and the from and to statuses it has just written, never a finding and never any other
  cannot-evaluate". Inside the writer (step 10) the render accepts it for exactly the record and
  statuses the verb has just written. The standalone write mode accepts it only on the terms of
  open point 22 (this prompt's choice), so a render between a writer transition and its commit
  still works.

Acceptance checks, each as an automated test over a throwaway store the test builds:

- A duplicated ID, an ID above its counter, a hand-edited view, a released worklog entry edited
  after its release, a machine-store source absent from the git index, an ignored path under
  `.working/`, a store missing an index, and a store missing an imported leaf each produce a
  finding or cannot-evaluate, and a valid store built from sections 4 and 9, with its views
  rendered by step 5, produces valid.
- A truncated TOML file produces cannot-evaluate (exit 2), never valid.
- The no-store mode exits 2 on a repository with no store.
- The render write mode refuses on a store with a duplicated ID and writes nothing.
- Given a committed store and a working tree that changes one record's status and appends the
  matching `opf-record transition <ID> <from> -> <to>` worklog entry, a validator that grades the
  change cannot-evaluate reports it as the pending kind, and the standalone render write mode
  proceeds. The same change without the matching entry, or with any other cannot-evaluate or any
  finding present, makes the write mode refuse and write nothing.

### Step 7: consistency, lease and location

Implement section 5.7 (The store consistency contract) for the in-repo default. Initialization
(step 9) and the writer (step 10) both depend on it.

- In the in-repo pattern the contract reduces to the lease plus a clean-state check: no conflict
  markers, no mid-merge state and no concurrent OPF run on the store paths, or refuse.
- The lease (`lease.toml`, present only while held, carrying the holder, the operation and a
  clock-read acquisition time) is never seized from a live holder and is reconciled on resume or
  close. Whether it is committed, and its field names, is open point 12.
- Store files are never hand-merged: after a merge conflict on a store path, take the integration
  base's version of the conflicted files and redo the authoring operation, which claims the next ID
  afresh. Never resolve an ID collision by lowering a counter or reusing an ID.
- If you later relocate the store, `opf migrate`, `opf sync` and the provider registry
  (sections 5.4, 5.5 and 5.6) apply in full, including the behind, ahead and divergent rules. They
  are outside this prompt's minimum; do not relocate without implementing them.

Acceptance checks:

- Taking the lease while it is held refuses and changes nothing; a leftover lease from a dead run
  is released only through reconciliation, never seized.
- A conflict marker or a mid-merge state on a store path makes the clean-state check refuse.

### Step 8: the 1.2.0 to 1.3.0 store upgrade (`opf upgrade`)

Section 4.2 (Layout overview) says "Activation MUST include the tested upgrade in section 9.2 and
deterministic doctor coverage before a writer accepts the new format", and section 9.2 says "The
1.3.0 delta remains a target contract until a tested upgrade and its required readers activate".
Initialization (step 9) and the record writer (step 10) both write the 1.3.0 format, so implement
and test the section 9.2 upgrade from 1.2.0 to 1.3.0 here, before either. The step 6 validator is
the required reader.

Test the upgrade only on synthetic older-store fixtures that your tests build themselves, from
sections 4, 9 and 9.2, in throwaway repositories. A 1.2.0 fixture has `spec_version = "1.2.0"`, no
imported leaves and no imported counter rows. This imports nothing into your project's store,
which is born at 1.3.0 and needs no upgrade.

- **The delta.** Section 9.2: "the version bump, registration and create-only initialization of
  missing imported managed leaves for enabled types, and addition of missing imported counter rows
  at zero only where no imported ancestry exists." Preserve existing records, evidence, clean
  counters and imported high-water values; refuse on "a populated collision, missing ancestral
  counter or unprovable prestate". Never create historical records, adoption approval or
  provenance (no `init.toml` for an upgraded store), never change posture or import status, never
  add imported views, and never create an adoption or import evidence folder.
- **How it writes.** The manifest and counters rewrite is a model regeneration through the step 1
  canonical emitter, never a textual edit, with the byte-reproduction precondition and a
  postcondition that the model diff equals exactly the allowed delta, expressed as ensure-present
  and ensure-absent. Other writes use atomic replacement of existing files, create-only writes for
  new files, and views regenerated through exclusively created temporary files followed by atomic
  rename. The upgrade is idempotent, and a repeated upgrade is a verified no-op only after a full
  validator VALID.
- **Preconditions.** Claim the step 7 lease and hold it across the whole mutation, and verify that
  the working tree is clean, ignored files included, over the planned schema and render
  destinations and the index collision candidates. Fail closed on an unresolvable store, a declared
  `spec_version` above the tooling's, a divergence, a held lease, or populated state that
  contradicts the preconditions. Refuse a delta that would put a managed path or view at a
  registered `[unmanaged]` path, naming the collision.
- **Finish.** Regenerate the declared views, require a full validator VALID (step 6), and leave the
  change uncommitted for the adopter's own branch and merge: never stage or commit it, and never
  write the adoption archive.
- **Authority report.** The upgrade report MUST enumerate every legacy importer-authored clean
  record whose current authority the section 8.6 firewall withdraws, and MUST refuse before any
  write, naming each such record and its remedy, where that withdrawal would leave the validator
  below VALID (section 9.2).
- **Older origins and legacy imports.** How the upgrade treats a 1.0.0 or 1.1.0 store, and a store
  whose `import_status` is not `"none"`, is open point 21.

Acceptance checks, over synthetic fixtures the test builds:

- A 1.2.0 fixture with no records upgrades to 1.3.0, and the only model changes are the version
  bump, the imported leaves and the imported counter rows at zero; views are regenerated, the
  validator reports valid, and nothing is staged or committed.
- A 1.2.0 fixture with synthetic clean records keeps every record byte and every clean counter.
- Running the upgrade a second time on the result is a verified no-op.
- A held lease, a dirty planned destination, a manifest carrying a comment, a `spec_version`
  above 1.3.0, and a populated file at an imported leaf path each refuse with nothing written.
- At `import_status = "none"` (open point 21), a fixture holding a synthetic legacy
  importer-authored clean backlog item upgrades with that item named in the report, and a fixture
  whose ratified `done` backlog item has as its only receipt a synthetic importer-authored `done`
  record refuses before any write, naming the record and the remedy.

### Step 9: fresh initialization (`opf init`)

Initialization creates the store sources and pointer for a fresh start, under the coupled contract
`opf.init.coupled/v1` of OPF-INIT-D2B.md, which you implement in full.

- **Preconditions.** The project is a git repository and not a bare one (section 5.1;
  OPF-INIT-D2B-REVIEW.md, row F02), and init refuses an "unsupported
  topology/platform/filesystem/index format, or unconfirmed target" (row F02). An unsupported
  operating system is CANNOT-EVALUATE (OPF-INIT-D2B.md, "Residuals"). Init refuses when both
  pointers exist, or when an alternate store or a retired token is present (row F03: "Both pointers,
  alternate stores, retired tokens, malformed/ambiguous/partial adoption refuse"). This prompt's
  reading extends that to either pointer alone, since in a fresh start a pointer names a store that
  already exists. There is no `.working/` directory (OPF-QUICKSTART.md, "Getting started", says a
  fresh `opf init` refuses an existing one). Neither `.working/` nor any file init would create is
  ignored by an effective ignore rule: init refuses rather than force it, since section 5.1 makes an
  ignored `.working/` tree "a hard failure in scaffolding" (review register row F22). Every
  pre-existing file at a declared view or deliverable destination outside `.working/`, including
  `VERSION` and `CHANGELOG.md`, needs a disposition first, and init refuses undispositioned foreign
  content (section 14, Adoption, post-adoption import and pre-existing files; section 14.2,
  Pre-existing files at the store location; review register row F17).
- **If a destination is occupied, or `.working/` exists,** stop and report it to the maintainer.
  The specification's route is adoption under section 14 with one approved plan and a disposition
  per file (`keep`, `move` or `retire`; `migrate` feeds an import, which this fresh start excludes).
  Do not move, overwrite, absorb or delete those files yourself.
- **If the project's history shows an earlier OPF store,** stop: that is re-adoption, which seeds
  counters from a pinned ancestral snapshot and never from zero (section 8.2; review register row
  F04). History that is "Shallow/missing/unreadable/malformed/bounded-out" is cannot-evaluate, with
  "no lazy fetch" (row F05), so init refuses rather than assume a first adoption.
- **Inventory.** Record a digest-stamped inventory of the project's pre-existing operational files,
  including the instruction and governance surfaces of each supported assistant platform, as
  section 14 requires of investigation, and bind it through the init plan's `inventory_digest`
  (OPF-INIT-D2B.md, "Plan Format"). In a fresh start no entry lies at one of the store's
  destinations, and init itself leaves every entry where it is, untouched. Give each entry a
  recorded exclusion from the store (section 14.1, completion check 2: "every inventory entry has a
  disposition or recorded exclusion"); asking the maintainer to ratify the list is this prompt's
  choice, not a check the specification names. An entry with neither a disposition nor a recorded
  exclusion keeps the report at `migration_incomplete` (section 11, "Defaults": "Reports MUST carry
  `migration_incomplete` while an approved source or detected file remains unresolved").
- **Planned instruction-surface edits.** Step 14 later adds OPF working rules to each platform's
  instructions file, editing an inventoried file or creating a missing one. Those are planned
  changes to instruction surfaces, not dispositions of preserved operational content, and the
  exclusion still stands for the file's other content. Before step 14 edits an inventoried file,
  record the planned edit and the file's inventoried digest as its preimage in your implementation
  notes and get the maintainer's agreement. This is this prompt's choice: section 14.1 has an
  adoption plan bind "exact creations, replacements, removals and consumer repointings", and open
  point 14 reads a scaffold as needing no such plan.
- **What init writes.** The init-created sources are the pointer, `manifest.toml` (step 3 values,
  `posture = "required"`, `import_status = "none"`), `counters.toml` at zero, `version.toml` and
  `worklog.toml` with no rows, every clean index and imported leaf, `init.toml`, and
  `CHANGELOG.md` holding only the section 10.4 opening note, in fixed wording that you choose and
  record. The changelog is required, not optional: section 6.3 says the public changelog "lives at
  the product repository root as `CHANGELOG.md`", the manifest declares it as a curated
  deliverable, and at `required` an unreadable or unresolvable declared input is a failure
  (section 11). With no releases and no summary rows, range coverage and freeze pass on it. Section
  11, "Defaults", requires the posture and import status values. Then render the declared views,
  `VERSION` included (open point 8). Which files are sources and which are views is part of open
  point 17.
- **Provenance.** Write `.working/toml/init.toml` in the `opf.init.bootstrap/v1` shape. Its
  `source_digest` covers the enumerated bootstrap source set and excludes `init.toml` itself, and
  the outer plan digest is computed afterward (OPF-INIT-D2B.md, "Bootstrap Provenance"). Never
  write provenance for a store this init did not create (section 9.2).
- **The coupled contract.** Implement all of OPF-INIT-D2B.md:
  - the plan (`opf.init.plan/v1`) and outcome (`opf.init.outcome/v1`) formats with their listed
    fields, and the discrete sets S, V, K, E and C with no overlaps; this prompt's reading is that
    in a fresh start the Keep set K and the existing-file set E are empty;
  - the Keep schema (operation identifier `opf-init-keep`, `schema = 1`), its canonical JSON
    representation and `sha256:` digests, and its fixed limits; this prompt's reading is that a
    malformed or over-limit decision input refuses even when there are no Keep decisions;
  - staging: exactly the init-created source files enter the git index, as exact entries with
    their validated bytes and modes; no view, no other path, no conflict stage and no
    intent-to-add entry enters it; unrelated index entries survive; and staging runs no git
    filter, hook or helper that could transform a payload (review register rows F19, F20 and F21);
  - views: the initial views are rendered from those sources, match them, and stay unstaged
    (review register row F30);
  - no commit is created;
  - ordering: the final store validation follows the last store write, including the lease
    removal, while the outer mutex still excludes participating writers (OPF-INIT-D2B.md, "Success
    wording");
  - success: exit 0 only with fresh observations supporting each assertion, including the plan's
    required checks actually executing, and then print exactly the success sentence quoted in
    OPF-INIT-D2B.md, "Success wording". A missing or failed required check, a disagreement between
    sources and views, inventory drift, malformed provenance, or a failure in finalization or lock
    release prevents exit 0 and keeps the primary error (review register rows F26 and F27);
  - resume: a retry after a crash resumes its original operation and plan, never overwrites, and
    never allocates twice; an existing source counts only when it is byte-identical to its
    recorded plan payload (review register row F28).
- **The review register.** OPF-INIT-D2B-REVIEW.md records most of these invariants as "specified -
  not runtime-verified" at the pinned commit. Treat every row that applies to a fresh start as a
  requirement your tests must cover, never as evidence that it holds anywhere. Fill the formats'
  gaps under open point 17.

Acceptance checks:

- In an empty git repository, init exits 0 and prints exactly the success sentence. Afterwards,
  `git diff --cached --name-only` lists exactly the init-created sources, and `git ls-files -s`
  shows each with the mode and blob of its written bytes; every declared view exists, the render
  check reports no drift, and no view is in the index; HEAD is unchanged and no commit exists that
  init created; the validator reports valid; and `CHANGELOG.md` holds only the opening note.
- A test that records the order of operations shows the lease removal as the last store write,
  followed by the final validation, with the outer mutex held until that validation ends.
- An injected failure in finalization or in owned-lock release makes init exit non-zero, report
  the primary error and withhold the success sentence; so does a required check that fails or does
  not execute.
- With a pre-existing `.working/`, `VERSION`, `CHANGELOG.md`, `.opf.toml` or `.opf.local.toml`, or
  with `.working/` matched by an ignore rule, init refuses and writes nothing.
- In a shallow clone, init does not fetch more history and does not exit 0.
- Inventory drift between planning and the final observation, or a malformed `init.toml`, prevents
  exit 0 and withholds the success sentence.
- Running init a second time refuses rather than overwriting.
- A process killed at each publication boundary and then retried resumes its original operation
  and plan, overwrites nothing, and allocates nothing twice.
- After you commit, with the maintainer's agreement, `git check-ignore` reports nothing ignored
  under `.working/`, `git ls-files` lists every store file and view, and the validator reports
  valid.

### Step 10: the record writer (`opf record`)

Implement section 8.8 (Authoring operations). Subcommands:

- `create`: a new record of an enabled baseline type in its initial state (not `done` receipts or
  worklog entries).
- `transition`: a checked status change; writes `proposed_from` on an assistant or automation
  landing at `/proposed`, restores it exactly on rejection, removes it on ratification; writes or
  removes the pending_decision resolution bundle and the contribution delivery bundle exactly as
  section 8.8 states; applies the chain rule over the active index and every archived record. It
  refuses to act on, or overwrite, a record whose current row is not schema-valid, `proposed_from`
  included (section 8.8).
- `done-with-receipt`: maintainer-only; moves a backlog item to unqualified `done` and creates its
  `done` receipt in the same act. A backlog item reaches unqualified `done` only this way
  (section 8.8: "A backlog item MUST reach unqualified `done` only through `done-with-receipt`").
- `worklog-append`: appends one `recorded` entry to the unreleased tail; refuses an entry inside a
  released span and a `detail` that opens with the lifecycle grammar.
- Supersession of a handoff or a contribution: section 8.5 requires it in the same act, and
  section 8.8 gives no operation for it. Implement it only as open point 23 describes, once the
  maintainer has ruled. Until then, this prompt's reading is that `create` refuses a second
  `current` handoff and the writer offers no contribution re-send, rather than doing either in two
  acts; report that gap, since section 14.1 says enforcement "MUST NOT ship before the writer can
  perform every operation it forces" (step 14, Ordering).
- The `import --batch` mode exists in the specification, but this fresh start does not implement
  or use it. The clean-series subcommands refuse `actor.kind = "importer"`.

`create`, `transition` and `done-with-receipt` each append their own worklog entry in the same
transaction, whose `detail` opens with `opf-record create <ID> <status>` or
`opf-record transition <ID> <from> -> <to>`, and a rejection's entry records its reason.

Every subcommand follows the eight-item operation sequence of section 8.8: reconcile any
interrupted transaction first, under the lease; the byte-reproduction precondition; one atomic ID
claim with the counter advance inside the same transaction; a postcondition that the model diff
equals exactly the allowed delta, derived from the request and never from the planned rows; clean
destinations, ignored files included, with the step 7 lease held across publication, render and the
final validation; one crash-durable journaled publication (journal location: open point 11);
render, then a full validation that must report valid; the verb's render and that validation
accept the one pending cannot-evaluate of section 8.8, item 7 "only for exactly the record and the
from and to statuses it has just written, never a finding and never any other cannot-evaluate",
and the verb reports it as pending until commit; then release the lease and report. Leave the change
uncommitted: never stage or commit it. When the final validation fails, leave the change in the
working tree for review with recovery advice scoped to the planned paths, as section 8.8, item 7
says; do not roll it back.

Exit status: 0 when the change is recorded and the store validates (or carries only that pending
cannot-evaluate), 2 on every refusal or cannot-evaluate (section 8.8). Section 8.8 names no status
for a final validation that fails after the change is written; this prompt's choice is 2 (open
point 10).

Acceptance checks:

- An assistant `transition` of a backlog item to `done` lands `done/proposed` with
  `proposed_from = "active"`, creates no receipt, and appends one worklog entry. An assistant
  creating a block lands `active/proposed`, and an assistant send of a contribution lands
  `sent/proposed`.
- A `transition` to unqualified `done` refuses for every actor, and `done-with-receipt` refuses an
  assistant actor.
- `transition` refuses a record whose row is not schema-valid and leaves it untouched.
- A transition that the validator grades as the pending cannot-evaluate renders, exits 0 and
  reports the change as pending until commit; with any other cannot-evaluate or any finding in the
  store, the same transition exits 2.
- While a `current` handoff exists, `create` of another handoff refuses unless the open point 23
  operation is implemented, and then the old handoff leaves `current` in the same transaction.
- A final validation failure exits 2 and leaves the planned change in the working tree with
  recovery advice naming the planned paths.
- Killing the process between journal write and publication leaves the store, after
  reconciliation, exactly at its prestate or exactly at its poststate, and the next operation
  refuses until that reconciliation is inspected.
- A file with a comment is refused and left untouched; a held lease refuses; no ID is ever reported
  for a rolled-back transaction.

### Step 11: releases and the changelog

Implement sections 6 (The release triad) and 7 (Changelog gates: range coverage and freeze).

- **Release cut.** Append a `[[release]]` row with a SemVer `version` not already in the ledger, a
  clock-read `date`, the contiguous, non-overlapping `worklog_span` of the entries it covers (`[]`
  for none), and their `coverage_digest`. From then on those entries are frozen; a correction is a
  new entry in the unreleased tail linking `corrects` (section 6.2). Re-render `VERSION`. The
  specification names no verb for this, and range coverage needs a summary row and heading for the
  new version (open point 9); whatever performs the cut runs under the lease and follows the step 10
  operation sequence.
- **Drafting.** The machine drafts a summary from the level directly below (release summaries
  from their worklog span; range summaries from the release summaries they replace), using the
  worklog, the ledger and the linked `done` receipts (sections 6.3 and 6.4). The specification
  names `opf absorb` as the absorber; it fixes no interface for it.
- **Curation and publication.** No unreviewed machine summary ships (section 7.3): you draft, the
  maintainer curates and publishes. Publishing records the freeze digest of the entry's exact
  bytes, from its heading line up to the next entry heading or end of file, with LF endings, on the
  `[[summary]]` row with `status = "published"` (section 7.2).
- **Re-publication.** Editing a published entry is a re-publication: the row's digest is updated
  in the same reviewed change, and a light gate confirms the edited entry still declares the same
  `covers` and still rests on the same worklog entries, with the coverage digests unchanged
  (section 7.2). A change to the facts lands as new worklog entries instead.
- **Headings.** Each entry starts `## <covers>`, optionally with parenthesized dates, where
  `<covers>` is a version, a range `a..b`, or `unreleased`; entries run in descending order with
  the optional unreleased section first (section 6.3; Appendix D).
- **Gates.** Range coverage (section 7.1): every non-superseded, non-unreleased summary row parses
  and refers only to ledger versions; those rows tile the ledger with no gap or overlap; every such
  row has exactly one matching heading; every release-or-range heading matches exactly one such
  row; and an `## unreleased` heading matches the `covers = "unreleased"` row instead. Freeze: every
  published summary's digest and its releases' coverage digests recompute equal (section 7.2).
  Rollup supersedes summaries, never worklog entries (sections 6.4 and 6.5).

Acceptance checks:

- A release whose `version` already exists in the ledger is refused.
- After a release cut, the validator reports valid (under your open point 9 choice), and the new
  release's worklog span is frozen.
- Editing a published entry's prose without updating its digest fails the freeze gate; a
  re-publication that changes the entry's `covers` fails.
- Editing a released worklog entry fails the coverage digest check.
- Two summary rows covering the same release, a release with no summary, and a heading with no
  matching row each fail range coverage.

### Step 12: rotation and archive

Implement section 12 (Rotation, archive, and retention) before the store needs it; until then
nothing rotates, and the step 6 validator already refuses an archive it cannot read.

- Rotation is relocation, never deletion and never ID reuse, to calendar-year buckets
  `archive/<YYYY>/` inside the discovered machine store (`.working/toml/archive/<YYYY>/` when it is
  named `toml`; section 4.2 says "the example name `toml` MUST NOT be hardcoded").
- Only records in unqualified terminal states, other than worklog records, may rotate, so a
  `/proposed` record stays active. Worklog records rotate only as released, frozen spans. Open
  records, active blocks, unresolved decisions, the current handoff and the unreleased worklog tail
  never rotate, and at 1.3.0 the imported series never rotates.
- Each rotation writes that year's `archive.toml`, enumerating every moved ID (and, for the
  worklog, every moved span) and its destination. Counters are untouched. Validation confirms that
  every ID exists in exactly one active or archived location, and coverage gates read active and
  archive together. Rotation thresholds are open point 13.
- Section 13 (Tamper evidence) explains how history, frozen digests, index reconciliation and
  archive enumeration combine.

Acceptance checks:

- After a rotation every gate gives the same answer as before it.
- A `/proposed` record and an unreleased worklog entry are refused for rotation.
- An ID present in both the active index and the archive is a finding.

### Step 13: the CI gate

Write your own CI job, in your project's CI system, with the behaviour of the reference recipe
(`opf/enforcement/ci/opf-ci.sh`, read only) plus the store identity check that the recipe leaves
out. Section 14.1, completion check 4, requires that "CI asserts store presence and identity so
absence cannot pass as NOT-APPLICABLE"; the recipe says "the CI receipt-identity comparison is a
later release", so reproducing the recipe alone does not meet that check.

- Check out full history, as the reference workflow template does with `fetch-depth: 0`. The
  template says its doctor observations read no history ("none reads history, so a shallow clone
  would already satisfy them") and keeps full history "so a later history-reading check need not
  change the template"; keeping it is this prompt's choice for the same reason. A check of yours
  that needs history and cannot read it is cannot-evaluate (exit 2), never a pass.
- Transition legality in CI: under open point 22's reading the prior committed snapshot of a
  checked-out revision is that revision itself, so the writer's pending cannot-evaluate does not
  arise in CI, and CI does not re-evaluate the transitions inside the commits it checks; disclose
  that as a residual (step 14). If you choose to compare with an earlier commit instead, such as
  the merge base, label that your choice and accept the pending cannot-evaluate only on the terms
  of open point 22.
- Run, over the checked-out revision, and stop at the first failure: the validator in its
  no-store-fails mode; the identity check; then the render drift check.
- The identity check compares the resolved store's identity with expected values pinned in the CI
  job definition, outside the store (open point 19). This prompt's choice of statuses: a mismatch
  exits 1, and an identity that cannot be read exits 2.
- Exit with 0 only when every step passes; pass a step's own 1 or 2 through unchanged, and turn any
  other status (a launch failure, a signal) into 2, so an out-of-vocabulary status is never read as
  a verdict.
- Write nothing, and mark no step as allowed to fail; the reference workflow template notes that a
  check that cannot fail is decorative.

Acceptance checks:

- The job fails on a branch that deletes `.working/`, on a branch with a hand-edited view, and on a
  branch with a duplicated ID, and passes on a clean branch.
- The job fails on a branch that replaces the store with a different valid store, made by a
  separate `opf init`.
- A broken interpreter path makes the job exit 2, not 0 or 1.

### Step 14: wiring every supported platform

Section 14.1 (One approval and completion) sets the enforcement floor: CI checks, staged-snapshot
pre-commit checks, "a verified deny hook on each supported platform whose official documentation
confirms denial support", and instructions on a platform without verifiable denial, disclosing
each platform's residual. It continues: "The supported platform roster is Claude Code, Codex,
Gemini CLI and Cursor; the pack MUST cover every one of them by one of those two means, per
platform." Wire all four, not only the platform you run on.

- **Pre-commit check.** Run the validator and the render check on the staged snapshot, and
  disclose that the hook is installed per clone (section 14.1). This git hook is not tied to any
  one assistant platform. Every commit of a writer transition carries the change that section 8.8,
  item 7 lets the validator grade cannot-evaluate "until the change is committed", so the hook
  accepts that pending cannot-evaluate on the terms of open point 22 (this prompt's choice) and
  blocks on every finding and every other cannot-evaluate.
- **Instructions, on every platform.** Add OPF working rules to each platform's instructions file
  (for example `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, `GEMINI.md` for Gemini CLI, and a
  rule file under `.cursor/rules/` for Cursor; confirm each file against that platform's official
  documentation, since the specification names platforms, not files). Editing an inventoried
  instructions file follows the step 9 rule for planned instruction-surface edits. The rules: the
  store is the source of truth and you record before you claim (section 3, "Records first"); write
  only through the record writer, never hand-edit store TOML or a generated view (sections 3 and
  4.6); read every timestamp from the clock; author as `actor.kind = "assistant"` and land terminal
  and gated states as `/proposed` for the maintainer to ratify (section 8.4); never author a
  maintainer_decision, never run `done-with-receipt`, never weaken the posture (section 11), and
  never publish a changelog summary (section 7.3); append a worklog entry for each change.
- **Deny hook, per platform.** For each of the four platforms, read its official hook
  documentation. Where it confirms that a hook can deny a tool call, install a hook that denies
  direct writes to the store's record series, counters, declared views and evidence, and writes
  under `.working/archive/adoption/`, while the sanctioned writer and render paths still work
  (section 14.1, the floor and completion check 5). Denial claims must be verified against the
  official documentation at build time. Where the documentation does not confirm denial, the
  instructions file is that platform's means of coverage, and you disclose the residual.
- **Evidence, per platform.** For each platform, record: the documentation page and section you
  read and the date you read it; the verdict (verified denial, or instructions only); the paths of
  its instructions file and hook; its disclosed residual; and its probe results.
- **Server-side branch protection.** Section 14.1, completion check 5, says "Server-side branch
  protection is adopter-attested, explicitly outside the local probe's guarantee." Ask the
  maintainer to attest whether the integration branch requires the step 13 CI job to pass before a
  change lands, and record that attestation, with who gave it and when, apart from the probe
  results. Neither CI nor the pre-commit hook establishes it, and you do not verify it yourself.
- **Probes.** On the platform you run on, probe live with your own tool calls. For each other
  platform with a hook, run the hook program directly with the input format its documentation
  specifies and show that it returns a denial; that shows the hook's decision, not that the
  platform enforces it, so report it as a hook-level probe and ask the maintainer to run the live
  probe on that platform. Report a live probe you could not run as not run.
- **Ordering.** Do not enable a deny rule before the writer can perform every operation the rule
  forces (section 14.1).
- **Residuals to disclose.** Per-clone hook installation and bypass, canonical hand edits, shell or
  interpreter wrapping, same-user tampering, and unverified platform denial (section 14.1), plus
  each platform's own residual, and, under open point 22, that CI does not re-evaluate the
  transitions inside the commits it checks. Copy the relevant residuals of section 17 into your
  project's documentation as well.

Acceptance checks:

- Each of the four platforms has an evidence record showing a verified deny hook or the
  instructional fallback with its residual disclosed.
- On your own platform, a direct edit of an index file and a write under
  `.working/archive/adoption/` by your own tool call are denied (or, without verifiable denial, the
  residual is written down), and `opf record create` and the render write still succeed with the
  hook active.
- For each other platform with a hook, the hook-level probe denies the same two writes and allows
  the sanctioned writer and render paths, and the live probe is reported as run or as not run.
- The pre-commit check blocks a commit that contains a hand-edited view.
- The pre-commit check allows a commit of a writer transition whose only remaining result is the
  pending cannot-evaluate with its matching lifecycle entry, and blocks the same commit when any
  other cannot-evaluate or any finding is present.
- The maintainer's branch-protection attestation is recorded, or its absence is reported.

### Step 15: clean-start completion

Section 14.1 defines the clean-start completion check for adoption. Run its roster against your
fresh start as the acceptance test, adapted only where an item names work that a fresh start does
not do:

1. Authority and freshness: section 14.1 checks that "roots, destinations and live preimages still
   match the approved plan". Here the approved plan is the init plan (open point 14): the roots and
   destinations still match it, and every inventoried file still has its inventoried digest,
   except the instructions files step 14 edited, which the recorded preimage digests and planned
   edits of step 9 account for.
2. Discovery accounting: every entry of the step 9 inventory has a disposition or recorded
   exclusion (section 14.1); otherwise the report carries `migration_incomplete` and you do not
   claim conformance. Report separately whether the maintainer ratified the exclusions, which is
   this prompt's choice (step 9), not a check the specification names.
3. Preservation and restore: not applicable when nothing was retired or archived; say so.
4. Operational readiness: the store resolves to the identity recorded at init, CI asserts store
   presence and identity (step 13), and on the live tree the validator reports valid and no
   declared view drifts.
5. Wiring: the step 14 evidence for all four platforms, with each probe reported as run or not run,
   and, kept apart from those locally observed results, the maintainer's attestation of
   server-side branch protection, which section 14.1 places "explicitly outside the local probe's
   guarantee". Without that attestation, report this item as not passed.
6. Retirement readiness: not applicable when nothing was retired; say so.

Whether a scaffold needs an adoption receipt is open point 14: report your ratified reading, and do
not invent a receipt format.

Then write the conformance report described in the checklist below.

## Final conformance checklist

Report each item as passed, failed or not run, with the evidence (a command and its output, or a
test name). Any failed or not-run item means you do not claim conformance.

1. The pinned sources were fetched and each SHA-256 matched this prompt's table.
2. No code from `jposluns/guardrails` is imported, vendored, copied or called at run time.
3. The store is new: `import_status = "none"`, the imported series is empty, no record has
   `actor.kind = "importer"`, and nothing exists under `.working/imported/`.
4. `.opf.toml` resolves the store; resolution and discovery fail closed in the step 2 cases.
5. `manifest.toml` declares `standard = "opf"`, `spec_version = "1.3.0"`, no `homes` key,
   `layout = "inline"`, `posture = "required"`, the `local-directory` and `generic-git-remote`
   providers, and no module enabled.
6. Every enabled baseline type other than `worklog` has a clean index and an imported index; the
   worklog has `worklog.toml` and `worklog.imported.toml`; `counters.toml` covers every clean and
   imported namespace; `version.toml` and `worklog.toml` exist with `schema = 1`; `init.toml` and
   `CHANGELOG.md` exist.
7. Every file under the machine store is lowercase; every generated deliverable is uppercase and
   carries the section 10.3 header (the projection as a TOML comment block; `CHANGELOG.md` excepted,
   and `VERSION` as ratified under open point 8).
8. The validator runs the full section 11 roster plus the imported-series checks and the homes-1
   control-area registration, exits 0, 1 or 2 as step 6 describes, and fails closed on unreadable
   input.
9. Renders are byte-reproducible and the drift check catches a one-byte edit; the render write
   mode, the pre-commit check and the writer accept the pending cannot-evaluate of section 8.8,
   item 7 only on the terms of step 6, step 10 and open point 22, and nothing else.
10. The 1.2.0 to 1.3.0 upgrade of step 8 is implemented and passed its tests on synthetic fixtures
    before initialization and the record writer accepted the 1.3.0 format (section 4.2).
11. Initialization implements `opf.init.coupled/v1` (this prompt's scope choice; see "Choices to
    make before you start"): exit 0 only on fresh observations, with the exact success sentence;
    init-created sources staged as exact entries; views unstaged; no commit created; final
    validation after the lease removal; a finalization or lock-release failure prevents exit 0.
12. The record writer enforces the `/proposed` rule, the per-type transition table and rules of
    section 8.5 (with the handoff and contribution supersession as ratified under open point 23),
    the resolution and delivery bundles, unqualified `done` only through `done-with-receipt`, the
    lifecycle worklog entry, the eight-item operation sequence, and exits 0 or 2.
13. Releases freeze worklog spans; the changelog passes range coverage and freeze; no summary was
    published without the maintainer's curation.
14. The lease, the clean-state check and the integration-base merge rule are enforced.
15. Rotation, if implemented, preserves every gate's answer; if not implemented, nothing rotates.
16. The CI job checks out full history, runs the validator in no-store-fails mode, the store
    identity check against the pinned identity, and the render check, with the 0, 1, 2 status
    rule, and no step can pass by failing.
17. Each of Claude Code, Codex, Gemini CLI and Cursor is covered, with per-platform evidence of a
    verified deny hook or a disclosed instructional fallback; the pre-commit check is installed;
    the section 14.1 residuals are disclosed; and the maintainer's attestation of server-side
    branch protection is recorded apart from the local probe results.
18. Every open point you filled is listed with your choice and its ratification status.
19. The conformance statement uses only the section 16 vocabulary, for example
    "`conformant_for_declared_scope` (OPFiles base, spec 1.3.0 on homes 1; self-asserted; profiles:
    none declared)", or `migration_incomplete` while an inventory entry is unresolved, and names
    its scope, its exclusions (relocation, import, adoption of pre-existing files, modules, the
    upgrade of stores with a legacy import or an origin before 1.2.0 under open point 21, and any
    open point you could not settle) and every cannot-evaluate result.

## Out of scope for this prompt

Import and the imported-series writer (section 8.8, `import --batch`; section 14.1, post-adoption
import), adoption of pre-existing files (section 14), migrating an existing release pipeline
(section 14.3), store relocation and sync (sections 5.4 to 5.6), schema upgrades other than the
1.2.0 to 1.3.0 upgrade of step 8 (section 9.2; see open point 21), the homes-2 generation and its
homes migration (sections 4.2 and 9.2), the modules (section 8.5), and profiles (section 9.1). If
your project needs any of these, read those sections at the same pinned commit and extend the plan,
under the same rules.
