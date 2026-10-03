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
  format". This prompt therefore requires the full section 9.2 upgrade to 1.3.0, tested on
  synthetic older-store fixtures that you build yourself, before any writer of yours accepts the
  1.3.0 format (step 8): from 1.2.0, and from 1.0.0 and 1.1.0 by composing the earlier deltas,
  including the completed-legacy-import route and the pre-upgrade repair route. Where the upgrade
  needs a legacy-format detail the pinned sources do not give, that gap blocks activation until the
  maintainer resolves it (open point 25). The same section says the
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
  claims). Never write an unqualified "OPFiles conformant" or "AIQT conformant", the two claims
  section 16 says are "never emitted", nor an unqualified "OPF conformant", which this prompt adds
  to that list; and say that the claim is self-asserted.
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
  implement and test the section 9.2 upgrade to 1.3.0 from 1.0.0, 1.1.0 and 1.2.0 (step 8), as
  section 4.2 requires.
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
  MUST refuse to enable a record type the writer cannot author. The synthetic 1.0.0 fixtures of
  step 8 enable modules as test data; that does not change this choice for your store.
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
standard. None of them makes a stated MUST optional. Open point 25 differs from the others: it
names details the upgrade needs that no choice of yours can supply, apart from one labelled interim
reading, and it blocks activation until the maintainer resolves it.

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
    for `doctor` and `render --check` (step 13). The specification does name flags for interfaces
    outside this prompt's scope: `opf migrate --store <target>` (section 5.4),
    `opf record import --batch FILE [--root DIR]` (section 8.8), and `opf import --prompt`,
    `--status` and `--verify`, with the former `--scan`, `--plan`, `--review` and `--apply` modes
    refusing (section 14.1). OPF-INIT-D2B.md, "Bootstrap Provenance", names one flag of `opf init`:
    "The `--decisions` CLI flag and resume dispatch are the PR7 activation boundary and are not
    added in PR1"; it gives that flag no argument form or behaviour. This prompt's reading:
    `--decisions` is how init receives its Keep decisions (the set K of step 9), so keep that name
    for that role and never give it another meaning; its argument form, and whether a fresh-start
    init, whose K is empty, accepts it, are yours to choose and record. Apart from those named
    flags, this prompt fixes no flags or exit codes for the commands it has you build: the other
    flags of `opf init`, the flags of `opf doctor`, `opf render`, `opf upgrade` (the `--to` of open
    point 21 included), the release cut and rotation, and their exit codes beyond those named above,
    are yours to choose and record. This prompt's choice for `opf record`: a final
    validation that fails after the change is written exits 2 (step 10), since section 8.8 names
    only 0 and 2 for the verb.
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
15. **The `.working/README.md` deliverable.** Section 4.2 lists it as "README.md # ownership and
    regeneration note (deliverable)" but does not say whether it is a rendered view or who writes it,
    and no section 9.2 delta creates it. The "(deliverable)" label sits in tension with section 10.3,
    which says every generated file opens with a header naming its source paths, schema and
    generator versions, a source-set digest and the regeneration command, and with section 3's
    "Nobody hand-edits a generated file." This prompt's choice, which treats the file as a deliverable
    only in the sense of being a human-facing top-level note, not a rendered view: init writes it as
    an init-created source (open point 17) in fixed wording that you choose and record, saying that
    the store under `.working/` changes only through the OPF tooling, which files are generated, and
    the regeneration command; init stages it with its other sources, and it carries no do-not-edit
    header, since it is not rendered, so it carries no source paths and no source-set digest either,
    which a file with no sources could not state in any case. The
    validator does not require it, since an upgraded store may lack it, but when it is present the
    tracked-store check of open point 20 covers it. If the maintainer rules it a rendered view,
    declare it in the manifest and render it in step 5 instead, and give it the full section 10.3
    header.
16. **Worklog entries written by the record verbs.** Section 8.8 fixes the opening line of their
    `detail` but not their `kind`; section 6.2 lets a manifest register more kinds but names no key.
    Section 8.8 also says `create`, `transition` and `done-with-receipt` "MUST each append their own
    worklog entry, one entry per change", and `done-with-receipt` changes two records. This prompt's
    reading: it appends two entries in its one transaction, the transition form
    `opf-record transition <ID> <from> -> done` for the backlog item and the create form
    `opf-record create <ID> recorded` for its receipt, so the gates of open point 22 find the
    transition-form entry for the backlog item.
17. **Initialization formats.** OPF-INIT-D2B.md lists the field names of its plan, outcome and
    bootstrap formats in a brace notation, and says that its first slice "freezes these formats
    only; it does not implement their producers, persistence, or resume consumers". It does not
    give value types or a file encoding for the plan and outcome; the meaning of `binding`, `sets`,
    `publication_boundaries` and `recovery_policy`; the Keep fields beyond `operation` and
    `schema`; what the outer mutex and the owned lock are; or which files belong to the source set
    S and the view set V. This prompt's choice for the last: every init-created file that is not a
    rendered view, `CHANGELOG.md` included, is a source; every declared view, `VERSION` included,
    is a view. The contract also says "Duplicate decisions, overlapping group boundaries, and
    missing coverage result in refusal" (review register row F11 says the same of closed
    decisions), without saying what the decisions must cover. This prompt's reading: coverage is
    over the pre-existing paths that init's Keep decisions are about, which a fresh start does not
    have, so an empty decision set is complete; the section 14 inventory of step 9 is not a set of
    Keep candidates. If the maintainer reads coverage as every inventory entry, init with an empty
    K refuses on any non-empty inventory, so get this ruled before step 9. Binding the section 14
    inventory into the plan's `inventory_digest` is this prompt's choice (step 9): the contract
    lists only the field name. The plan's `acceptance` field is where row F13 applies: "Plans,
    proposals, report edits, and blanket consent cannot substitute for explicit attributed
    acceptance"; its value format is open too.
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
20. **What "tracked" means while an init or an upgrade is uncommitted.** Section 5.1 makes an
    untracked or ignored `.working/` tree "a hard failure in scaffolding and validation".
    OPF-INIT-D2B.md requires init-created sources staged and initial views unstaged with "the whole
    store is VALID". Section 9.2 has the upgrade create missing managed leaves and indexes
    create-only, regenerate the declared views and "require a full doctor VALID before offering the
    uncommitted change", and says "It MUST NOT stage or commit the change", so the files the upgrade
    creates are outside the git index when that VALID is required. This prompt's reading, which
    reconciles the three: the tracked-store check fails when any path under `.working/` is ignored,
    or when any file under `.working/` is absent from the git index (staged or committed), whether
    it is a machine-store source, a declared view, or any other file, such as a stray file or a
    `.working/README.md` (open point 15) left unstaged, with exactly the three exceptions below. The
    first two are keyed on the manifest at the prior snapshot's path in `HEAD`, which open point 22
    defines; the third is keyed on the lease. No other file is exempt, and any further exemption is
    a maintainer ruling added to this list.
    - While `HEAD` holds no prior snapshot of the store (open point 22), as right after init (an
      unborn `HEAD` included), a declared view may be absent from the index. Init's sources get no
      exception: init stages them.
    - While the manifest at the prior snapshot's path in `HEAD` declares a lower base version than
      the working tree does (an `[opf]` `spec_version` below the working tree's, or the retired
      1.0.0 `[devprocess]` table), as after an upgrade that is not yet committed, a declared view
      may be absent from the index, and so may a machine-store file that the section 9.2 deltas
      between those two versions create, provided that it is absent from `HEAD`, is not ignored, and
      is byte-identical to the empty file your emitter writes for its kind. Those files are the
      empty `contribution`, `maintainer_decision` and `preference_pattern` indexes of the 1.0.0
      delta and the imported managed leaves of the 1.3.0 delta.
    - The lease file `lease.toml` may be absent from the index, since it is "present only while
      held" (section 5.7) and the writer's final validation runs while the lease is held (section
      8.8, item 5). Whether the lease file is committed at all is part of open point 12; until that
      is ruled, this exception is this prompt's choice.

    Once `HEAD` declares the working tree's version, neither of the first two exceptions applies, so
    the adopter's commit of the upgrade must carry those files. Reading the base table and
    `spec_version` of the manifest in `HEAD` only to bound the second exception is also this
    prompt's reading, and it rests on a reading of section 17 that open point 21 shares: "The
    retired 1.0.0 token `devprocess` is recognized by `opf upgrade` alone, purely to carry a legacy
    store forward" bars discovering, grading or carrying forward a store under that token anywhere
    else, but not reading a `[devprocess]` table's version as data about `HEAD` once the working
    tree's store has been discovered under `[opf]`. So the validator discovers and validates no
    store at `HEAD`, and open point 22 reads the prior snapshot at `HEAD` the same way, by path and
    file name. The opposite reading, that any reading of a `[devprocess]` table outside
    `opf upgrade` is recognition, removes the retired-table leg of the second exception, and then
    the upgrade from 1.0.0, composed or staged, could not reach the validator VALID that section 9.2
    requires without staging its files, which section 9.2 forbids. Ask the maintainer to rule on
    this reading and on the open point 21 message together. A `HEAD` manifest that the second
    exception needs and that cannot be read is cannot-evaluate. Section 5.1 fails "an untracked or
    ignored `.working/` tree" without separating managed files from others, so this reading counts
    every file under `.working/`; the narrower alternative, counting only machine-store sources and
    declared views, is the maintainer's to choose instead. Rotation (step 12) gets no exception: the
    `archive/<YYYY>/` files it creates are machine-store sources, so this prompt's choice is that
    rotation stages exactly the files it writes, created and rewritten, as exact index entries in
    the way step 9 stages init's sources, after verifying those destinations clean, so that its own
    final validation and every later gate pass before the maintainer commits. The alternatives are a
    fourth exception for archive files that `HEAD` lacks and that year's `archive.toml` enumerates,
    or rotation that stages nothing, with gates compared only after the maintainer commits; ask the
    maintainer to choose. Ask the maintainer to ratify the whole rule.
21. **Older declarations and the repair route.** Section 4.2 says "Activation MUST include the
    tested upgrade in section 9.2 and deterministic doctor coverage before a writer accepts the new
    format", and section 9.2 says "Earlier stores compose their applicable deltas with this delta".
    That is not open: step 8 implements and tests the upgrade from 1.2.0, and from 1.0.0 and 1.1.0
    by composing the deltas section 9.2 gives, with the completed-legacy-import route and the
    pre-upgrade repair route, before initialization (step 9) and the record writer (step 10) accept
    the format. What is open is how a 1.3.0 validator and writer treat a store that declares an
    older version. Section 9.2 says how a validator treats a `spec_version` above its own (a
    fail-closed INVALID finding naming the tooling-upgrade remedy) and that "Only the reserved
    homes-2 declaration of section 4.2 stays recognized, and that recognition keeps legacy homes-1
    grading"; it does not say how a 1.3.0 validator grades a lower declaration. It does say that the
    upgrade's refusal names a remedy "recorded before the upgrade is retried", that "every named
    remedy MUST be performable through the sanctioned writer without hand-editing canonical files",
    and section 8.6 says "`opf record create` authors such a decision". A validator that failed
    every older declaration would make that remedy impossible on the older store: the writer's final
    validation (section 8.8, item 7) and the render gate (step 6) would refuse on the version
    finding alone, and a failed operation's leftover writes are not a sanctioned repair. This
    prompt's reading, which keeps the remedy performable:
    - A store declaring `spec_version = "1.1.0"` or `"1.2.0"` under the `[opf]` base table is graded
      under its declared version: the step 6 roster without what section 9.2 and section 4.2 tie to
      1.3.0, namely the imported managed leaves and imported counter rows, the four imported-series
      checks, the homes-1 recognition of the adoption and import control paths, and the section 8.6
      firewall's withdrawal of authority from legacy importer-authored clean records (section 9.2:
      "The bump changes no record's bytes, but it does change what a legacy importer-authored clean
      record confers"). A 1.1.0 store also does not admit `init.toml` (section 9.2: "Base spec 1.2.0
      admits one new managed machine-store file"). The validator reports that `opf upgrade` is
      available as a notice, not as a finding.
    - On such a store your writer's `create` and `transition`, which step 8 builds, run under that
      grading and the full step 10 operation sequence, and write only that version's
      format, never an imported leaf or an imported counter row, so a maintainer can record the
      remedy there and commit it (open point 28 lists the remedies).
    - A 1.0.0 store carries the retired token `devprocess`, which section 17 says "is recognized by
      `opf upgrade` alone, purely to carry a legacy store forward", so neither your validator nor
      your writer discovers it. Discovery then finds `.working/` but no machine store in it, which
      section 4.5 makes a cannot-evaluate outcome that "never treats it as an empty or absent
      store". This prompt's choice is one generic message for every such zero-match result, chosen
      from the presence of `.working/` alone and without reading any base table (step 2): it says
      that `.working/` holds no discoverable machine store, names the subdirectories it examined,
      and names the remedies that can apply there, `opf upgrade` for a legacy store and a correction
      of the manifest for a damaged or mistyped one (section 17 says a mistyped token "resolves to
      cannot-evaluate (fail-closed), never to a silent empty store"). It never names `opf init`,
      which refuses an existing `.working/` (step 9). Because this message reads no base table, it
      does not depend on the section 17 reading of open point 20. The alternative, naming
      `opf upgrade` alone when a `[devprocess]` table is seen, is consistent with that reading,
      which treats reading a table as data and not as recognition, and is the maintainer's to choose
      instead; ask for the rulings on open points 20 and 21 together. Where the composed upgrade
      from 1.0.0 must refuse for a withdrawn authority, its remedy names a
      staged route, which is this prompt's choice: `opf upgrade --to 1.2.0` applies only the deltas
      section 9.2 gives up to 1.2.0 ("A 1.0.0 store takes the 1.0.0 delta above directly to
      1.2.0"), under the same preconditions, guards and final validation, and leaves its change
      uncommitted like any upgrade. The maintainer then reviews and commits that intermediate
      upgrade before running the writer, since the writer's planned destinations "MUST be clean"
      (section 8.8, item 5) and the intermediate upgrade has just changed `counters.toml` and
      created indexes the writer writes. Only then does the maintainer record the remedy through the
      writer on the committed 1.2.0 store, commit it, and retry the upgrade to 1.3.0.
    - Any other declaration below 1.3.0 is a fail-closed INVALID finding naming the store-upgrade
      remedy (`opf upgrade`, step 8).
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
    tree, or the staged snapshot in the pre-commit check. The validator reads that prior snapshot at
    the prior snapshot's path in `HEAD`: each index file and archive file there by its file name,
    matching records by ID across active and archived files, without reading the manifest there and
    without running discovery at `HEAD`, so a `HEAD` that still holds a `[devprocess]` or older
    `[opf]` store after a composed or staged upgrade is read all the same (open point 20). The prior
    snapshot's path is found from file names alone. It is the resolved machine store's path when
    `HEAD` holds a `manifest.toml` there. Otherwise, when `HEAD` holds a `manifest.toml` in exactly
    one other immediate subdirectory of `.working/` and the working tree no longer has that
    subdirectory, the machine subdirectory has been renamed (section 4.5: "renaming it is a
    directory move with nothing to go stale"), and the prior snapshot is read at the old path; with
    more than one such candidate, or with a candidate that the working tree still has, the
    comparison is cannot-evaluate, never all-new. Only when `HEAD` holds no `manifest.toml` in any
    immediate subdirectory of `.working/` is there no prior snapshot of the store, and then every
    record is new. At the prior snapshot's path, an index file absent from `HEAD` means its records
    are new; a file present there that cannot be read or parsed is cannot-evaluate. The same
    comparison grades no-deletion: every ID that the prior snapshot holds, in each series, worklog
    entries included, must still exist in an active or archived location (section 8.2: "Uniqueness,
    counter high-water, contiguity and no-deletion checks MUST evaluate each series independently";
    section 13: "Records are never deleted"), and an ID that does not is a finding. It also grades
    counter monotonicity: every counter value that `counters.toml` holds at the prior snapshot's
    path must be met by an equal or higher value for the same series and namespace in the snapshot
    under test (section 8.2), as step 6 describes. When `HEAD` is unborn, established as review
    register row F07 requires ("Failed resolution is not unborn proof; valid symbolic HEAD and
    absent target are independently established"), there is no prior snapshot: every record is new
    and there is no transition to grade, so a valid store right after init reports valid. A `HEAD`
    that fails to resolve is cannot-evaluate. Two uncommitted transitions of one record would defeat
    the match below: after an assistant lands `done/proposed` and a maintainer then runs
    `done-with-receipt` before any commit, the validator sees `active -> done` against `HEAD` while
    the verb wrote `done/proposed -> done`. An uncommitted transition of another record would also
    fail a later operation's final validation, which accepts only the record and statuses that
    operation has just written, and section 8.8 says "Every subcommand runs one operation sequence".
    Section 8.8, item 5 already refuses most of these cases: "the planned destinations MUST be
    clean, including ignored files", and `create`, `transition`, `done-with-receipt`,
    `worklog-append`, the open point 23 handoff operation and the step 8 remedy writer each rewrite
    `worklog.toml` with their worklog entry, so each refuses on that dirty destination while an
    earlier record operation is uncommitted. This prompt's choice extends that refusal only to what
    item 5 does not cover. Every operation of yours that writes the store refuses while any record
    present at `HEAD` has a working-tree status that differs from its status there, even when none
    of its own planned destinations is dirty (as after every file but the changed record's index was
    committed), naming the remedy, which is to commit the earlier change first. That is the full
    list: the operations above, the release cut of open point 9, which writes `version.toml` and
    `CHANGELOG.md` but not `worklog.toml`, and rotation (step 12). The step 8 upgrade's own
    precondition (section 9.2) verifies the working tree clean, ignored files included, but only
    "over the planned schema and render destinations (store and product roots) and the index
    collision candidates", not every file under `.working/`, so this clause still applies to the
    upgrade: it also refuses while any record present at `HEAD` has a working-tree status that
    differs from its status there, even one outside the upgrade's own planned destinations. Init
    writes no existing store, so this clause never applies to it; a store-writing operation you add
    later joins the list. The standalone render write mode is explicitly excluded from this
    additional refusal: it writes only declared views, never a record's index or `worklog.toml`, and
    step 6 requires it to proceed over the one pending cannot-evaluate that the rule for the other
    gates below accepts (one named record and its from and to statuses, matched by the writer's
    lifecycle entry in the snapshot under test, since a standalone render cannot know what the verb
    wrote), so extending this clause to render would make that step 6 acceptance test impossible to
    pass. Render still takes the step 7 lease like any operation that writes the store (section 5.7
    names `render` in its reconciliation list); when render runs nested inside another operation's
    own sequence, it runs under that operation's already-held lease rather than taking a second one.
    The refusal comes after section 8.8, item 1 ("Resolve the store, then any interrupted authoring
    transaction MUST be reconciled first") and before any other write, so recovery from a crash runs
    first, and a reconciled change is then the earlier change to commit. This prompt's choice for
    the other gates: a gate accepts a cannot-evaluate only when the validator reports it as this
    pending kind, naming one record and its from and to statuses, and the snapshot under test
    carries, in the unreleased worklog tail and absent from `HEAD`, the writer's lifecycle entry
    `opf-record transition <ID> <from> -> <to>` for exactly that record and those statuses; never a
    finding and never any other cannot-evaluate. The render write mode alone also proceeds over view
    drift, which rendering itself remedies (step 6); that is not an acceptance of the drift, and the
    pre-commit check and CI accept no view drift. Section 8.8 calls that worklog entry
    "informational, never evidence" for rejection provenance; here it only selects which pending
    cannot-evaluate a gate tolerates, it proves nothing about the actor, and a canonical hand edit
    that adds a matching entry passes, which is the canonical hand edit residual of section 14.1.
    Under this reading CI, which checks a committed revision, compares it with itself, so the
    pending cannot-evaluate cannot arise there and CI does not re-evaluate the transitions,
    deletions or counter decrements inside the commits it checks (step 13). A merge commit has two
    parents, and step 14 says how the pre-commit check compares one.
23. **Same-act supersession of a handoff, and the contribution re-send.** Section 8.5 says "Posting
    a new handoff MUST supersede the previous in the same act". Section 8.4 says "where the type
    records it, the superseded record's terminal state MUST reflect it". Section 8.4 also says "A
    terminal transition performed by an actor whose `kind` is `assistant` or `automation` MUST land
    with the `/proposed` qualifier" and "A `/proposed` status is not terminal: gates and completion
    claims MUST treat the record as unfinished". So a `superseded/proposed` predecessor does not
    reflect the supersession, and a replacement that leaves its predecessor there has not superseded
    it in the same act; section 8.4 settles that. Section 8.8 gives no operation for the handoff:
    `create` writes "one new record" with an allowed delta of "the new rows appended, the counters
    advanced", and `transition` changes one record's status. This prompt's reading, the smallest it
    found consistent with sections 8.4, 8.5 and 8.8, is the maintainer-performed replacement: a
    single writer operation, performed by a maintainer actor, that creates the new handoff linking
    `supersedes` to its predecessor and moves the predecessor to unqualified `superseded`, in one
    journaled transaction under the full section 8.8 operation sequence. Its allowed delta is the
    `create` delta plus exactly one `transition` delta for the predecessor, with no `proposed_from`,
    and it appends one lifecycle worklog entry per changed record (section 6.2, "one entry per
    change") in the forms of section 8.8: `opf-record create <ID> current` for the new handoff and
    `opf-record transition <ID> current -> superseded` for the predecessor, the entry the open point
    22 gates match when the validator grades the predecessor's move as the pending cannot-evaluate.
    The predecessor then holds an unqualified terminal state, not a `/proposed` one, so no
    ratification or rejection follows. An assistant or automation actor's move of the predecessor
    would have to land `superseded/proposed`, so that actor cannot complete the supersession in the
    act. The writer refuses an assistant or automation replacement before anything is written,
    naming the maintainer-performed replacement as the path. That refusal stands while no operation
    lets such an actor complete the supersession in the same act; the specification defines none,
    and its standing authorization covers only a contribution's `sent` (section 8.4). For the same
    reason, this prompt's reading is that no handoff leaves `current` outside that operation:
    `transition` refuses a handoff's `current` > `superseded` move for every actor, naming the
    maintainer-performed replacement, and a plain `create` refuses a handoff while a clean handoff
    authored by a non-importer actor is `current`. That leaves no way to retire a handoff without a
    successor, though section 8.5's table lists `current` > `superseded` for the handoff. This
    prompt's reading is that a move to `superseded` with no superseding record is not an operation
    the specification defines, since section 8.4 says "the superseding record MUST link
    `supersedes`" and that move has no superseding record; disclose that a handoff leaves `current`
    only by being replaced (step 14), and ask the maintainer to rule on it with the rest of this
    point. When no such handoff is `current`, any actor may
    `create` one, since entering `current`, the initial state, is not a terminal transition. Ask the
    maintainer to rule on this before you enable the operation. The same-act rule is the handoff's
    alone. Of a contribution, section 8.5 says only "A re-send MUST be a new record linking
    `supersedes`; the superseded record records `superseded`", and the contribution table allows
    `sent` > `superseded`, so the existing `transition` and `create` perform a re-send (step 10).
    Section 8.4 settles whether a `superseded/proposed` contribution records the supersession, for
    the reason given above for the handoff: it does not, and only an unqualified `superseded`
    records it. That state change is owed only by a predecessor that can still make it. Section 8.4
    says "Where the superseded record is immutable, including every imported record, its recorded
    state MUST stand and the link alone records the supersession". Section 8.6 says that a
    clean-to-imported same-type `supersedes` "records historical continuation without discharging a
    current supersession obligation, and the immutable imported target keeps its recorded state, the
    link alone recording the supersession", that after the section 9.2 upgrade "an existing legacy
    importer-authored clean record MUST keep its bytes, its recorded state and its place in the
    clean series", and that "The clean-series writer MUST refuse a transition on an
    importer-authored record in either series". So this prompt's reading is that the
    ratification-first guard below applies only to a predecessor whose current supersession
    obligation requires a state change: a clean contribution authored by a non-importer actor.
    `create` refuses a contribution that links `supersedes` to such a predecessor whose status is
    not unqualified `superseded`, naming the remedy, which is a maintainer's ratification of the
    predecessor, committed (open point 22), before the re-send is created. A predecessor that is
    importer-authored, in the imported series or a legacy importer-authored clean record, is
    immutable history: `create` accepts a same-type `supersedes` link to it whatever state it
    records, checks no state on it and changes nothing on it, and the link discharges no current
    obligation. A fresh store holds no such record, so this case is tested only on synthetic
    fixtures (step 10). Ratifying first leaves an intermediate state of its own, which this prompt
    discloses: from the predecessor's transition until the re-send is created, a committed
    contribution sits at `superseded`, or at `superseded/proposed` until the ratification, with no
    record linking `supersedes` to it, across two commits and, for an assistant's re-send, for as
    long as the maintainer takes to ratify. Section 8.5 states no same-act rule for a re-send, so
    this prompt's reading is that the validator reports no finding for that state; if the maintainer
    rules otherwise, a re-send needs a same-act operation like the handoff's maintainer-performed
    replacement, and step 10 follows that ruling.
24. **Pointer and sync-target agreement for the in-repo default.** Section 11 lists "pointer and
    sync-target agreement (the committed pointer, the manifest's recorded sync target, and the store
    repository's actual remote agree; section 5.6)". In the in-repo default the pointer is
    `dir:.`, the manifest's `sync_target` is empty, and the store repository is the product
    repository, which usually has its own `origin` remote. The specification does not say what
    "agree" means there. This prompt's reading: with `target = "dir:."` and an empty `sync_target`,
    the check passes when the pointer resolves to the product root itself and does not compare the
    product repository's remotes, which OPF does not push; any other combination, such as a
    non-empty `sync_target` with an in-repo pointer, is a finding.
25. **Legacy-format details the upgrade needs that the pinned sources do not give. This open point
    blocks activation.** The step 8 upgrade needs each of these, and the specification does not
    define them:
    - The preserved legacy run evidence of a completed legacy import. Section 9.2 says "A completed
      legacy import upgrades in place: its `import_status` MUST stay `"complete"`, substantiated by
      its preserved legacy run evidence under section 11". Section 14.1 says that evidence "is that
      run's durable archive in its recorded legacy home, for the reference tooling
      `.aiqt/import-archive/<run-id>/`, holding the run's acceptance record and its evidence
      inventory in the retained legacy format; substantiation MUST re-read that inventory and
      digest-match every file it enumerates". The pinned sources do not define the acceptance
      record, the inventory format, or how a store records its legacy home.
    - The record schemas of the module types, the deprecated `legacy_fragment` included. Section
      8.5 gives module types only "in outline (full schemas ship with the module schemas release)",
      section 8.1 says the LF taxonomy row "and legacy validation remain for existing stores and
      evidence", and section 9.2 says "legacy LF records and evidence remain readable and MUST NOT
      be silently converted".
    - The deferral seam. Section 8.3 says "A conforming imported series can reach doctor VALID: it
      MUST NOT enter the legacy importer or module-schema deferral seam", which implies that the
      validator has a seam deferring the grading of legacy importer-authored records and of
      module-schema records. No pinned source defines it: which records enter it, how the validator
      grades a record inside it, and whether a store holding such records reaches the full validator
      VALID the upgrade requires through it. Whether an origin store holding module, LF or legacy
      importer-authored records can reach that VALID is therefore open, not settled either way. This
      prompt's interim reading, labelled as such and yours to confirm with the maintainer: until the
      seam is defined, a legacy importer-authored clean record that carries every field the ordinary
      clean schema requires, `created_at` included, is graded under that ordinary schema and the
      section 8.6 firewall, outside the seam; only module records, LF records and legacy
      importer-authored clean records without `created_at` are cannot-evaluate pending this point.
      These checks depend on the interim reading: in step 4, the check that such a record is graded
      under the ordinary schema; in step 6, the receipt-withdrawal check and the decision-chain
      checks; in step 8, the `"none"` fixture holding an importer-authored backlog item, the repair
      route, the decision-chain checks and the receipt route from a 1.0.0 fixture (which also waits
      on the 1.0.0 base field names below); and in step 10, the decision-chain checks and the
      re-send check on an upgraded legacy importer-authored clean contribution. If the maintainer
      rules that the seam takes these records too, those checks join the step 8 group that waits on
      this point and are graded as the ruling says.
    - The state of a run in a legacy import or ingest location. Step 8, "An unresolved legacy
      import", lists the locations the specification names: the retained legacy staging home
      `.working/imports/` (sections 4.2 and 4.4), the typed staging homes under `.working/staging/`
      (sections 4.2 and 14.1), evidence under `.working/imported/` (sections 4.2, 9.2 and 14.1), the
      preserved legacy run evidence home and how a store records it (section 14.1), the legacy
      import report `.working/IMPORT-REPORT.md` (section 4.2), the journal homes under
      `.working/journals/` (section 4.2) and `legacy_fragment` records (sections 8.1 and 9.2). No
      pinned source gives the layout or format of a run in any of them,
      how to tell a resolved run from an unresolved one, or whether a `quarantined` fragment is an
      unresolved import. Reserving a name against discovery, or recognizing that a run is present
      (section 4.2, "The existing staged-plan presence test"), does not answer that, and section 11
      says "a staging directory MUST NOT be taken as proof of status". Until this is resolved, the
      step 8 upgrade reports cannot-evaluate before any write on any entry it finds in any of those
      locations, whatever the `import_status`.
    - The legacy `created_at` omission. Section 8.3 says "An importer MAY omit `created_at` where
      the source genuinely does not record it; the omission is recorded as unknown via the import
      provenance reference, never guessed. This legacy permission does not replace the following
      1.3.0 imported-series contract." The permission itself is settled, and step 4 applies it to
      legacy importer-authored clean records. The pinned sources do not define the import provenance
      reference of a legacy clean record: its field, its form, or how the validator confirms that it
      records the omission.
    - The base field names of a 1.0.0 manifest. The section 9 example marks `layout` as "(was
      `layout_profile`)", and OPF-QUICKSTART.md lists that rename beside the retirement of
      `devprocess`, but the section 9.2 delta from 1.0.0 carries "every other base field over
      unchanged" and renames no field. The pinned sources do not settle whether a 1.0.0
      `[devprocess]` table carries `layout_profile` or `layout`, so the 1.0.0 fixtures cannot be
      built from them alone.

    Do not invent these formats, and do not read them out of the reference tooling's code. Ask the
    maintainer to resolve each one, by naming a source pinned by commit and SHA-256 or by a recorded
    ruling, and keep the request in your implementation notes (the store does not exist yet). Until
    a detail is resolved, your validator and upgrade report cannot-evaluate on every input that
    depends on it, so the upgrade refuses such a store before any write. Under the interim reading
    above, a legacy importer-authored clean record that carries every required field does not depend
    on the seam. A legacy importer-authored clean record without `created_at` is an input that does
    depend on a detail: it is cannot-evaluate, never a schema finding, since the specification
    permits the omission, and never valid, since the provenance check cannot run. The step 8 checks
    that depend on a detail are reported as not run; step 8 is not passed; and the activation
    section 4.2 requires has not happened, so do not start step 9. Report this as a blocker, never
    as an exclusion from your conformance scope.
26. **The section 8.6 receipt exception.** Section 8.6 says that where the section 9.2 upgrade
    "withdraws a ratified `done` item's only receipt as legacy importer-authored, a
    maintainer-authored clean `maintainer_decision` that links `corrects` to that item and records
    that its completion stands MUST satisfy the item's receipt obligation in place of the withdrawn
    receipt; `opf record create` authors such a decision, doctor and the upgrade MUST accept it". It
    names no field or wording for "records that its completion stands". This prompt's choice: the
    validator and the upgrade accept, for such an item, a clean `maintainer_decision` with
    `actor.kind = "maintainer"` and a `corrects` link to that item whose `decision` field opens with
    a fixed statement that you choose and record; they cannot check the statement's meaning.
    Section 8.6 adds that "a maintainer who instead judges the work unfinished MUST record a new
    clean backlog item linking `derives_from` back, never reopen the terminal `done`", and does not
    say whether that also satisfies the receipt obligation. This prompt's reading: it does not, so
    the upgrade keeps refusing on that item; ask the maintainer to rule.
27. **Manifest registration of the imported leaves.** Section 4.2 says the imported files are
    "registered managed leaves beside the clean-series files, using the same enabled-type roster"
    and that "Their manifest, emitter, upgrade and containment registrations MUST agree", and the
    section 9.2 delta includes "registration". Neither the section 9 example nor any other section
    shows a manifest key for that registration. This prompt's reading: the manifest registration is
    the enabled type's `[types.<name>]` row itself, so neither step 3 nor the upgrade writes a
    further key for it. If the maintainer rules that a key is needed, add it to the step 3 manifest,
    to init, and to the step 8 allowed delta.
28. **Which withdrawals leave the post-upgrade validator below VALID, and their remedies.** Section
    9.2 says the upgrade MUST refuse "Where a withdrawn authority would leave the post-upgrade
    doctor below VALID", and names one case, the receipt-stripped `done` item, and one general
    remedy, "a maintainer-authored clean record that restores or supersedes the withdrawn authority
    under section 8.6". It does not list the other cases. This prompt's readings follow, from
    sections 8.5 and 8.6, except for the decision-chain bullet, which sets out two readings for the
    maintainer to choose between:
    - A ratified `done` backlog item whose only receipt is legacy importer-authored fails the clean
      receipt obligation. The remedy is the open point 26 `maintainer_decision`, through `create`.
    - Pending_decision supersession chains. The specification supports two readings of a chain in
      which a legacy importer-authored decision supersedes a maintainer-authored one, and this
      prompt does not choose between them. The choice is the maintainer's: put it to your own
      maintainer before step 8, record the ruling in your implementation notes, and build the step
      6, step 8 and step 10 decision-chain checks for the reading ruled. Until the ruling those
      checks are not run, and step 8 is not passed. Two chain shapes show the difference, each
      record a decided pending_decision: shape A, a maintainer-authored PD-1 and a legacy
      importer-authored PD-2 that links `supersedes` to PD-1 and is the chain head; and shape B,
      shape A plus a maintainer-authored PD-3 that links `supersedes` to PD-2 and is the chain head.
      On a store declaring 1.1.0 or 1.2.0, open point 21's grading leaves out the section 8.6
      firewall, so in each shape the head is the chain's one current resolution before the upgrade.
      - Reading 1: an importer-authored `supersedes` link takes nothing out of current authority.
        Section 8.6 says "An importer-authored record MUST NOT satisfy a current approval, receipt,
        actionability or supersession obligation of any record authored by a non-importer actor",
        "an importer-authored record MUST NOT discharge a required supersession", "An
        importer-authored decision MUST NOT be treated as the current effective resolution of a
        clean `pending_decision` chain; resolving such a chain now MUST take a new clean decision
        linking back", and "a state-bearing status on an importer-authored record describes history
        at its source and MUST confer nothing now, and every current-state join, including the
        section 8.5 at-most-one-current handoff rule, MUST evaluate only clean records authored by
        non-importer actors". Its clean-to-imported wording points the same way, a same-type
        `supersedes` that "records historical continuation without discharging a current
        supersession obligation", though it governs links into the imported series. Under this
        reading, the chain is still the connected component section 8.8 defines, importer-authored
        links included, but at 1.3.0 only a link from a non-importer record takes a decided record
        out of current authority, and "resolving such a chain now" governs how a maintainer resolves
        a chain that no non-importer decision resolves, such as one whose members are all
        importer-authored. Shape A then has one current resolution, PD-1, and is valid with no
        repair; PD-2 ceases to be current and PD-1 becomes current, and the upgrade report names
        both. Shape B has two current resolutions, PD-1 and PD-3, and fails the section 8.5 rule
        ("exactly one current effective resolution MUST exist per chain"), so the upgrade refuses
        before any write, naming PD-1, PD-2 and PD-3, this open point and the reading 1 remedy
        below.
      - Reading 2: an importer-authored `supersedes` link still records the supersession as history,
        and an importer-authored decision cannot be the chain's resolution. Section 8.6 says "An
        importer-authored decision MUST NOT be treated as the current effective resolution of a
        clean `pending_decision` chain; resolving such a chain now MUST take a new clean decision
        linking back." Section 8.4 says "Supersession is a link, not a state edit" and "Where the
        superseded record is immutable, including every imported record, its recorded state MUST
        stand and the link alone records the supersession". Section 8.8 makes the chain "a connected
        component over `supersedes` links in either direction" and says "the doctor counts a
        `supersedes` link from a proposal too". Under this reading, "MUST NOT discharge a required
        supersession" binds the obligations a superseding record owes, such as the handoff's, and
        does not undo a decided record's history. At 1.3.0, PD-1 stays superseded by PD-2's link,
        and PD-2 cannot be the resolution, so shape A has no current resolution and fails the
        section 8.5 rule; the upgrade refuses before any write, naming PD-1 and PD-2, this open
        point and the reading 2 remedy below. Shape B has one current resolution, PD-3, and is valid
        with no repair; no record of the chain becomes or ceases to be current.
      - The reading 1 remedy, for shape B, through the writer on the store at its declared 1.1.0 or
        1.2.0 under open point 21's grading: the maintainer `create`s a new pending_decision PD-4 at
        `open` whose `links` already hold `supersedes` to PD-1, and the change is committed (open
        point 22). While PD-4 is open the doctor counts its link, since section 8.8 makes the chain
        "a connected component over `supersedes` links in either direction" and names no status a
        linking record must hold, and counts a link from a `/proposed` landing too ("the doctor
        counts a `supersedes` link from a proposal too"); that this covers an `open` record is this
        prompt's reading. So PD-3 stays the chain's one current resolution. The maintainer then
        `transition`s PD-4 to unqualified `decided` with its resolution bundle, appending
        `supersedes` to PD-3, and the change is committed. PD-3 is a decided, schema-valid chain
        head, and following `supersedes` links from PD-3 reaches PD-2 and PD-1, never PD-4, so the
        section 8.8 target checks pass ("its own chain MUST NOT lead back to the superseding
        record", which this prompt reads as following the target's `supersedes` links); the chain
        rule then finds one current resolution, PD-4. The retried upgrade finds PD-1 and PD-3 each
        superseded by PD-4, a non-importer record, so PD-4 is the one current resolution and the
        store reaches VALID. This remedy rests on two readings, the one of "its own chain" above and
        the one of `create` below. Section 8.8 says `create` writes "one new record of an enabled
        baseline type, in the type's initial state" and states no restriction on the links it
        carries; its `supersedes` target checks bind the link a `transition` appends. Reading that
        as permitting a `supersedes` link at `create` is this prompt's reading; ask the maintainer
        to confirm it with the ruling. Section 8.8 points the same way when it says "So `transition`
        also refuses to decide a record that a pending_decision not at unqualified `decided` already
        supersedes", which assumes that a pending_decision not yet decided, such as an `open` one,
        can carry a `supersedes` link. `create` is one way to write it there; the legacy importer
        or a canonical hand edit could also leave such a link, and that sentence may address those
        cases instead, so it supports this reading without settling it. If the maintainer
        chooses reading 1 but reads "its own chain" as the connected component, which already holds
        PD-4 through its link to PD-1, or rules that `create` may not write a `supersedes` link,
        under which nothing takes PD-1 out of current authority, since PD-1 is not a chain head
        while PD-2 links to it, shape B has no writer-performable remedy, which section 9.2 does not
        allow ("every named remedy MUST be performable through the sanctioned writer"); report that
        as a blocker of step 8. Neither link targets an importer-authored record. This remedy does
        not apply under reading 2, where shape B needs none.
      - The reading 2 remedy, for shape A, is the one section 8.6 names, "a new clean decision
        linking back", through the writer on the store at its declared 1.1.0 or 1.2.0 under open
        point 21's grading: the maintainer `create`s a new pending_decision PD-3 at `open`, the
        change is committed, the maintainer `transition`s PD-3 to unqualified `decided` with its
        resolution bundle, appending `supersedes` to PD-2, the chain head, and the change is
        committed. The retried upgrade finds shape B with PD-3 its one current resolution, valid
        under reading 2. This remedy does not apply under reading 1: it produces shape B, which
        reading 1 refuses. Section 8.8 says the clean-series subcommands "MUST also refuse an
        importer-authored record as their operand: acting on imported or legacy importer-authored
        history is a new clean record linking back (section 8.6)". This prompt reads a `supersedes`
        target as an input that `transition` reads, not as its operand, since `transition` rewrites
        only the record it decides and the same sentence names a new clean record linking back as
        the way to act on history. If the maintainer rules that the target is an operand, reading 2
        leaves shape A with no writer-performable remedy, which section 9.2 does not allow ("every
        named remedy MUST be performable through the sanctioned writer"); report that as a blocker
        of step 8. The reading 1 remedy links to no importer-authored record, so the operand
        question does not touch it.
      - Under either reading, this prompt's reading is that a chain whose members are all
        importer-authored is history and imposes no current obligation.
    - Records that become current. Section 9.2 requires the upgrade report to name "each record that
      ceases to be a current state under section 8.6, so that authority change is reported, never
      silent". A record can also become current through the upgrade, as PD-1 does in shape A under
      reading 1, and that is an authority change too. So your upgrade report names every record that
      becomes a current state at 1.3.0, with the record whose place it takes, as well as every
      record that ceases to be one. Requiring the first list is this prompt's reading of that
      purpose clause; ask the maintainer to ratify it.
    - A backlog item that leaves the actionability join, a block that stops granting a stop, and a
      record that stops being the current handoff or another current state leave no finding by
      this reading: those joins are derived when they are read, and section 8.5 allows "at most one"
      current handoff, so none is also valid. The upgrade report still names each of them.

    If your validator grades any other withdrawal below VALID, name it, its remedy and the writer
    operation that performs it, and ask the maintainer to rule before step 8 passes. Ask the
    maintainer to ratify this list and the readings above, and to choose one of the two
    decision-chain readings.

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
    README.md                ownership and regeneration note (section 4.2; open point 15)
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

- Read `.opf.local.toml` first, then `.opf.toml`. With neither, try `.working/` at the product root.
  With no pointer and no `.working/`, report that there is nothing to operate on, naming `opf init`
  for a fresh start (this prompt's reading of section 4.3, below). Where `.working/` exists, at the
  product root or at a pointer's target, discovery below decides.
- A pointer that exists but does not resolve (unreachable target, or no valid manifest there) is
  cannot-evaluate. Never fall back silently to the default location.
- Relative `dir:` paths resolve against the product root; any other path in a pointer must be
  absolute. Targets follow section 5.5 (Target syntax).
- Find the machine store as exactly one immediate subdirectory of `.working/` whose
  `manifest.toml` declares `standard = "opf"` in its `[opf]` table, trying `toml` first. Do not
  hardcode the name `toml`. Zero or more than one match is cannot-evaluate. Zero matches never reads
  as an empty or absent store (section 4.5): it gives the zero-match message of open point 21, which
  names `opf upgrade` for a legacy store and a manifest correction for a damaged one, never
  `opf init`, since init refuses an existing `.working/` (step 9). Section 4.3 says: "Where no
  pointer exists and no default store is found, there is nothing to operate on and `opf init` is the
  remedy." Its words "no default store is found" can also cover a `.working/` that holds no
  discoverable machine store, which is the zero-match case above. This prompt's reading narrows the
  `opf init` remedy to the case where no pointer exists and no `.working/` exists at all, since init
  refuses an existing `.working/` and so cannot be the remedy there, and the two messages never
  overlap; the specification does not itself state that narrowing, and it is yours to confirm with
  the maintainer.
- The names `imports`, `imported`, `archive`, `staging` and `journals` are reserved; a machine
  store with one of those names fails closed (section 4.4). Discovery examines `manifest.toml` in
  every immediate subdirectory, including `journals/`, fails closed on a reserved-name match or
  ambiguity, and reads no deeper there (end of section 4.2).
- `.opf.local.toml` is never committed and is ignored by version control; the committed pointer
  must be safe to publish (section 4.3). Section 4.3 states that but names no check and no writer
  for it, so this prompt's choice is this: the step 6 validator reports a finding when the
  snapshot it evaluates holds `.opf.local.toml` at the product root. That snapshot is the product
  repository's git index for a run on the working tree and for the step 14 pre-commit check, which
  evaluates the staged snapshot, and the checked-out revision for the step 13 CI gate; the
  pre-commit check and the CI gate therefore block it. The validator never reports it because an
  earlier commit, `HEAD` included, holds the file, so the commit that removes a committed override
  is not blocked by the defect it removes. The remedy is `git rm --cached .opf.local.toml`, which
  keeps the file on disk, and an ignore rule for it, committed together; your report asks the
  maintainer for both. No OPF operation writes that rule. Section 4.2 has init and upgrade render
  one ignore file, `.working/.gitignore`, and only at homes 2 ("Homes-2 init and upgrade MUST render
  this managed block into `.working/.gitignore`"); at homes 1, this prompt's target, they render
  none, and that file would not cover the product root in any case.

Acceptance checks:

- A pointer naming a missing directory gives cannot-evaluate, even when a valid `.working/`
  exists at the product root.
- Two subdirectories with valid manifests give cannot-evaluate; renaming the machine
  subdirectory still resolves.
- A manifest with a mistyped `standard` value is not discovered, and the result is cannot-evaluate,
  not an empty store (section 17, Residual coverage disclosures); its message does not name
  `opf init`. A repository with no pointer and no `.working/` gives the message that names
  `opf init`.

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
- **Providers.** Section 5.6 (The provider registry) says `local-directory` and
  `generic-git-remote` "ship as the universal fallbacks", which is a statement about tooling, and
  only the illustrative section 9 example declares them in a manifest. Declaring both, with the
  handler and roles of that example, is this prompt's choice, not a requirement of the standard.
  An in-repo store needs no host provider such as `github`.
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
- **The legacy `created_at` permission.** Section 8.3 adds: "An importer MAY omit `created_at` where
  the source genuinely does not record it; the omission is recorded as unknown via the import
  provenance reference, never guessed. This legacy permission does not replace the following 1.3.0
  imported-series contract." So a legacy importer-authored clean record, which exists only in an
  older store or one upgraded from it and never in your store, may lack `created_at`, and your
  validator applies that permission before and after the upgrade. The form of the import
  provenance reference is not defined, so how the validator confirms the omission is recorded is
  open point 25, which says how to grade such a record meanwhile. Its labelled interim reading
  grades a legacy importer-authored clean record that carries every required field, `created_at`
  included, under this ordinary schema. Every other clean record requires `created_at`.
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
  ratified `done` backlog item has its one-to-one `done` receipt linked `receipt_of`; a finding's
  severity is graded at or after the fix decision, never before; a pending_decision's resolution
  bundle is all-or-none, with exactly one current effective resolution per supersession chain;
  posting a new handoff supersedes the previous one in the same act, and at most one `current`
  handoff exists; a contribution's delivery bundle fields appear only at the states the table
  allows, and a re-send is a new record linking `supersedes` while the superseded record records
  `superseded`; a `maintainer_decision` takes `actor.kind` `maintainer` (or `importer`, which a
  fresh store never uses). Section 8.8 gives the writer no same-act operation for the handoff
  supersession; that gap is open point 23, whose reading is a maintainer-performed replacement and a
  refusal of an assistant or automation replacement. A contribution re-send has no same-act rule and
  is an ordinary `transition` and `create` (step 10), with a non-importer predecessor at unqualified
  `superseded` first, since a `/proposed` status is not terminal (section 8.4; open point 23); an
  importer-authored predecessor keeps its recorded state and the link alone records the supersession
  (sections 8.4 and 8.6).
- **No resurrection and supersession.** A record in an unqualified terminal state never re-enters
  a working state; a revived concern is a new record linking the old one; supersession is a
  `supersedes` link, and where the type records it, the superseded record's terminal state
  reflects it (section 8.4).
- **Links and refs.** `rel` comes from the closed vocabulary `supersedes`, `resolves`,
  `remediates`, `receipt_of`, `corrects`, `follows`, `relates`, `exemplifies` and `derives_from`;
  `exemplifies` targets a preference_pattern. `refs` are `{kind, locator, note}` with `kind` one of
  `path`, `url` or `doc` (section 8.6, Links and reference capture).
- **Actionability.** Section 8.5, "Actionability": "a clean backlog item authored by a non-importer
  actor is actionable when its state is `open` or `active` and no clean, unqualified `active` block
  authored by a non-importer actor scopes it", "Imported and importer-authored records MUST NOT
  enter this join on either side: an importer-authored backlog item is history, never actionable
  work, whatever its recorded state", "an importer-authored block MUST NOT block", and "This is the
  block join every scheduling view renders; a scheduling view MUST surface an importer-authored item
  only as history, never as actionable." The last sentence is step 5's rule, not only the
  validator's: the composed `BACKLOG.md` view must apply it too, which the step 8 upgrade exercises
  on fixtures holding importer-authored backlog items. In
  your fresh store no record is importer-authored, so this reduces to an `open` or `active` item
  that no unqualified `active` block scopes; implement the full rule all the same, since step 6
  grades it on the step 8 fixtures and open point 21 relaxes it for a store declaring 1.1.0 or
  1.2.0.

A stored record does not show which actor made its last transition: section 8.8, item 7 says the
history comparison "sees only the prior snapshot's type and status, which identify neither the
transitioning actor nor the pre-proposal state". The `/proposed` landing rule is therefore tested
at the writer, in step 10, not by record validation.

Acceptance checks, over records and snapshots the test builds:

- Record validation refuses: an ID in the wrong namespace, an unknown key, a `/proposed` worklog
  entry, a `proposed_from` on a status without `/proposed` or naming an illegal predecessor, a
  maintainer_decision with `actor.kind = "assistant"`, an open pending_decision carrying any part
  of its resolution bundle, a link with an unknown `rel`, a clean record without `created_at` whose
  `actor.kind` is not `importer`, and, given a prior and a current snapshot, a terminal record moved
  back to a working state.
- A legacy importer-authored clean record without `created_at` is graded cannot-evaluate while open
  point 25 is unresolved: never refused as schema-invalid and never accepted as valid.
- Under the interim reading of open point 25, a legacy importer-authored clean record carrying
  every required field, `created_at` included, passes record validation, and the same record with
  an unknown key is refused.
- An assistant-proposed `active/proposed` block does not make its scoped item non-actionable.

### Step 5: views and deliverables (`opf render`)

Implement section 10 (Views and deliverables). The validator in step 6 uses this renderer for its
drift checks, so it comes first.

- Render every view the manifest declares. Composed views use only the closed transform
  vocabulary: filter on declared field predicates, sort on declared keys with ID as the final
  tie-breaker, group by a declared field, project declared columns, and exactly two joins, the
  block join and the decision-resolution link (section 10.2). The block join is also the
  actionability join of section 8.5: "a scheduling view MUST surface an importer-authored item only
  as history, never as actionable", so the composed `BACKLOG.md` view MUST NOT render an
  importer-authored backlog item as actionable work, whatever its recorded state (step 4,
  Actionability). That holds for a store graded at 1.3.0. On a store declaring 1.1.0 or 1.2.0, which
  open point 21 grades without the section 8.6 firewall's withdrawal of authority, the view renders
  the join as that grading derives it, as step 4 says: section 9.2 has the upgrade report name "each
  backlog item that leaves the actionability join", so such an item is inside the join before the
  upgrade.
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
  the `DECISIONS.toml` projection and the composed `BACKLOG.md`, `CONTRIBUTIONS.md` and
  `DECISIONS.md`. The section 9 example declares `BACKLOG.md` and the projection, and the 1.1.0
  upgrade delta in section 9.2 adds `CONTRIBUTIONS.md` and the projection. Neither declares
  `DECISIONS.md`: it appears in the section 4.2 layout, and the 1.1.0 delta widens its `sources` to
  `pending_decision`, `autonomous_decision`, `maintainer_decision` and `preference_pattern` "where
  that view is declared", so declare it with those four sources.

Acceptance checks, over stores the test builds:

- Rendering twice gives identical bytes, under two different locales and time zones.
- Editing one byte of a rendered view makes the check report drift (exit 1), and re-rendering
  clears it.
- A proposed (`/proposed`) record appears in views as awaiting ratification (section 8.4).

### Step 6: the validator (`opf doctor`)

Implement the integrity layer of section 11 (Enforcement posture). Its roster: schema validity of
what exists; ID uniqueness across active, archive and staging; counter monotonicity (two checks,
below); bidirectional index reconciliation; transition legality and no
resurrection; the all-or-none resolution bundle; byte drift for every deterministic view, `VERSION`
included; worklog span tiling and frozen coverage digests; changelog range coverage; changelog
freeze; archive integrity; the tracked-store requirement against the resolved store; pointer and
sync-target agreement (open point 24 for the in-repo default); unmanaged-path containment; and path
containment.

- At `posture = "required"`, an unreadable, unparseable or unresolvable declared input is a
  failure, never an empty or clean result (section 11). `CHANGELOG.md` is a declared input.
- The tracked-store check fails hard on an untracked or ignored `.working/` tree; there is no
  gitignore fallback (section 5.1, Always a git repository). How it treats the views that init
  leaves unstaged, and the files and views that the step 8 upgrade creates and must not stage, is
  open point 20, whose reading names the only exceptions.
- An `.opf.local.toml` that the snapshot under test holds is a finding, judged on that snapshot
  alone and never on `HEAD` or an earlier commit (step 2; this prompt's choice, not a check the
  specification names).
- Transition legality compares the store with the prior committed snapshot (section 8.8, item 7);
  which commit that is, is open point 22, whose reading is the store repository's `HEAD`, read at
  the prior snapshot's path by file name alone, without discovery at `HEAD`: that path is the
  resolved machine store's current path, or, after a rename that open point 22 recognizes, its old
  path. A prior commit that is needed and cannot be read is cannot-evaluate, never "no change". An unborn `HEAD`,
  established as open point 22 says, means there is no prior snapshot and no transition to grade.
  Section 8.8, item 7 lets the validator "grade that change cannot-evaluate until the change is
  committed" and says "`opf doctor` itself is unchanged and still reports it until then"; report
  that pending kind distinctly, naming the record and its from and to statuses, so the gates below
  can recognize it. Where a later acceptance check says that a writer operation's final validation
  is valid, it also passes when that validation reports nothing but this pending kind, for exactly
  the record and the from and to statuses the operation has just written, since section
  8.8 says the verb exits 0 "when the change is recorded and the store is doctor-VALID (or carries
  only the pending cannot-evaluate of item 7)".
- Section 8.2 says "Uniqueness, counter high-water, contiguity and no-deletion checks MUST evaluate
  each series independently", so run each of them per series, the clean and imported records and the
  clean and imported worklog apart. No deletion compares with the prior committed snapshot as
  transition legality does (open point 22): every ID it holds must still exist in an active or
  archived location (section 13: "Records are never deleted"), and a missing one is a finding. The
  specification does not define contiguity beyond the release spans of section 6.1 ("Spans MUST be
  contiguous"); this prompt's reading is that the span tiling check carries it, and it is yours to
  confirm with the maintainer.
- Counter monotonicity is two checks, each run per series and per namespace (section 8.2). The
  bounds check: every ID lies within its counter (section 5.7 says doctor "checks store-wide ID
  uniqueness and that every ID lies within its counter"). The monotonicity check: section 8.2 says
  `counters.toml` "MUST hold independent monotonic high-water values per series and namespace" and
  "Counters MUST NOT be reset", which no single snapshot can show, since a lowered counter can still
  lie above every ID. So the validator also compares each counter with its value in the prior
  committed snapshot, read as transition legality reads it (open point 22): a value lower than
  there, or a counter row that the prior snapshot holds and the snapshot under test lacks, is a
  finding naming its series and namespace. A row absent from the prior snapshot, such as an imported
  counter row that the step 8 upgrade adds at zero, has no prior value to compare, and neither has
  any row when `counters.toml` is absent at the prior snapshot's path, as an absent index file holds
  no prior records (open point 22); with no prior snapshot there is nothing to compare; and a prior
  `counters.toml` that cannot be read or parsed is cannot-evaluate.
- View drift uses the step 5 renderer for every declared deterministic view and projection,
  `VERSION` included (sections 10.5 and 11).
- The four imported-series checks named in section 8.3 (C-IMPORTED-SCHEMA, C-IMPORTED-IDS,
  C-IMPORTED-PROVENANCE, C-IMPORTED-SEGREGATION) join the roster at 1.3.0 (section 4.2), with
  C-CONTAINMENT recognizing the imported managed leaves and C-LINKS resolving links over both series
  (section 8.3). In your project's fresh store the imported series is empty and no record is
  importer-authored, so they evaluate empty inputs there; your tests still exercise them on
  throwaway stores, the step 8 fixtures included. A throwaway store at 1.3.0 whose imported series
  must grade valid uses `import_status = "none"` and a preserved original for each imported record
  whose `source_sha256` matches, under the reading step 10 labels for its check (a) of re-sends that
  supersede immutable history. Treat a missing imported leaf as a finding: section 4.2 requires init
  to create every one (this is a reading of that rule, not a check the specification names).
- The section 8.6 authority firewall covers "every record whose `actor.kind` is `importer`, in the
  imported series or written into the clean series by the pre-1.3.0 legacy importer", and
  C-IMPORTED-SEGREGATION enforces it over importer-authored records in both series (section 8.6);
  section 11 says it "MUST NOT be report-only". At 1.3.0, a clean backlog item reaching ratified
  `done` "MUST hold a clean receipt authored by a non-importer actor", importer-authored items and
  blocks leave the actionability join, and every current-state join "MUST evaluate only clean
  records authored by non-importer actors" (sections 8.5 and 8.6). The one exception is the
  `maintainer_decision` that section 8.6 lets stand in for a receipt the upgrade withdrew; doctor
  "MUST accept it" (its form is open point 26). Which withdrawals leave a finding, the
  decision-chain case included, is open point 28. A store declaring an older version is graded as
  open point 21 says.
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
  after its release, a machine-store source absent from the git index, a stray untracked file
  under `.working/`, an ignored path under `.working/`, a store missing an index, and a store
  missing an imported leaf each produce a finding or cannot-evaluate, and a valid store built from
  sections 4 and 9, with its views rendered by step 5, produces valid.
- A truncated TOML file produces cannot-evaluate (exit 2), never valid.
- With `.opf.local.toml` staged in the product repository, the validator reports a finding naming
  it; with the file present, untracked and ignored, it reports none for it. With a commit at `HEAD`
  that holds the file, `git rm --cached .opf.local.toml` and an ignore rule for it, not yet
  committed, leave the validator reporting no finding for it, though `HEAD` still holds it.
- The no-store mode exits 2 on a repository with no store.
- A valid store in a repository whose `HEAD` is unborn produces valid; a `HEAD` reference that
  does not resolve produces cannot-evaluate.
- This check and the next rest on open point 25's interim reading. In a 1.3.0 store, a
  maintainer-authored backlog item at ratified `done` whose only receipt is importer-authored is a
  finding, and an importer-authored `active` block does not make its scoped
  item non-actionable; adding a maintainer-authored clean `maintainer_decision` linking `corrects`
  to that item, in the form of open point 26, makes the validator report valid.
- The decision chains of open point 28, each in a 1.3.0 store and graded under the reading the
  maintainer ruled; until the ruling, report these checks as not run. Under reading 1, shape A makes
  the validator report valid, never a finding, with PD-1 the chain's current effective resolution;
  shape B is a finding; and shape B with the reading 1 remedy applied (PD-4 linking `supersedes` to
  PD-1 and to PD-3, at unqualified `decided`) is valid. Under reading 2, shape A is a finding, since
  the chain has no current effective resolution, and shape B is valid, never a finding, with PD-3
  the current resolution. Report the ruling with the checks.
- Given a committed store declaring 1.2.0, and a working tree that declares 1.3.0 and adds, unstaged
  and not ignored, exactly the empty imported leaves of the section 9.2 delta, with the imported
  counter rows at zero and the views rendered, the tracked-store check passes (open point 20). It
  reports a finding when one of those leaves holds a row, when one is matched by an ignore rule,
  when an index of a 1.2.0 type is absent from both `HEAD` and the index, and when `HEAD` already
  declares 1.3.0. With an unborn `HEAD`, an unstaged machine-store source is a finding while an
  unstaged declared view is not.
- With `HEAD` holding a committed 1.2.0 store and the working tree holding that store upgraded to
  1.3.0, transition legality reads the prior snapshot by path and file name (open point 22) and
  grades no record as changed; changing one record's status in the working tree is then graded
  against its status in `HEAD`.
- With a committed store whose `BI` counter is 100 while its highest `BI` ID is `BI-90`, lowering
  that counter to 99 in the working tree, with the views re-rendered, is a counter-monotonicity
  finding naming the clean `BI` counter, though every ID still lies within it; lowering instead
  only the `"imported:BI"` row, from a committed value the test sets, is a finding naming that
  imported row alone; removing a counter row that `HEAD` holds is a finding; and raising a counter
  is not a counter-monotonicity finding.
- With a committed store, removing a record's row from its index, or an entry from `worklog.toml`,
  in the working tree, with the views re-rendered, is a finding; the same terminal record's row
  moved by the test into an archive file that `archive.toml` enumerates is not (no deletion).
- With a committed store, a working tree that renames the machine subdirectory and moves a terminal
  record back to a working state reads the prior snapshot at the old path (open point 22) and
  reports the resurrection as a finding; when `HEAD` holds a `manifest.toml` in two other immediate
  subdirectories of `.working/`, the result is cannot-evaluate.
- The render write mode refuses on a store with a duplicated ID and writes nothing.
- Given a committed store and a working tree that changes one record's status and appends the
  matching `opf-record transition <ID> <from> -> <to>` worklog entry, with the views not yet
  re-rendered and no other defect, a validator that grades the change cannot-evaluate reports it as
  the pending kind beside the view drift, and the standalone render write mode proceeds and renders,
  after which the validator reports no view drift. The same change without the matching entry, or
  with any other cannot-evaluate or any finding other than view drift present, makes the write mode
  refuse and write nothing.

### Step 7: consistency, lease and location

Implement section 5.7 (The store consistency contract) for the in-repo default. Every operation that
writes the store takes its lease first (section 5.7: "before mutating the store, a run MUST take the
lease"): the upgrade (step 8), initialization (step 9), the writer (step 10), the release cut (step
11), rotation (step 12) and the standalone render write mode (step 5), which section 5.7's
reconciliation list names explicitly alongside `doctor` and `record`. When render runs nested inside
another operation's own sequence, such as the writer's render before its final validation, it runs
under that operation's already-held lease and does not take a second one.

- In the in-repo pattern the contract reduces to the lease plus a clean-state check: "the store
  paths in the working tree carry no conflict markers, no mid-merge state, and no concurrent OPF
  run, or the tooling MUST refuse" (section 5.7). The specification does not define "mid-merge
  state". This prompt's reading makes it a property of each store path, not of the repository: a
  store path is in mid-merge state while the git index holds an unmerged entry (a conflict stage)
  for it. A merge in progress whose store paths carry no conflict markers and no unmerged entries
  therefore passes the check, so a merge that conflicted only outside the store does not by itself
  make an operation refuse. A merge that conflicted on a store path is not resolved in place: it
  takes the integration-base route below. Section 5.7's reconciliation list names `doctor` and
  `render` beside the writing operations, so this prompt's reading is that the clean-state check (no
  conflict markers, no mid-merge state) applies to them too, even though neither takes the lease
  that a writing operation does except for render's own write mode, covered above. Both readings,
  the per-path meaning of mid-merge state and this scope, are this prompt's, not settled by a quoted
  rule beyond the list itself, and are yours to confirm with the maintainer.
- The lease (`lease.toml`, present only while held, carrying the holder, the operation and a
  clock-read acquisition time) is never seized from a live holder and is reconciled on resume or
  close. Whether it is committed, and its field names, is open point 12.
- Store files are never hand-merged: after a merge conflict on a store path, "take the integration
  base's version of the conflicted store files and redo the authoring operation on that base, which
  claims the next ID afresh" (section 5.7). Never resolve an ID collision by lowering a counter or
  reusing an ID. This prompt's reading of "on that base": the redo runs on a commit whose store is
  the integration base's, never inside the conflicted merge. Abandon the merge
  (`git merge --abort`), then start a branch from the integration base, or rebase the branch onto it
  with the branch's store changes left out, and redo each of the branch's authoring operations there
  through the writer, committing each as open point 22 requires. Concluding the conflicted merge
  with the base's version of the conflicted store files and redoing afterwards is not this route.
  The redo cannot run inside the merge: its planned destinations then differ from `HEAD`, so section
  8.8, item 5 ("the planned destinations MUST be clean") and open point 22 refuse. And the step 14
  merge check compares a merge commit with each parent, so a record that the branch holds and the
  taken files drop is reported as deleted. Whether that route also satisfies section 5.7 is the
  maintainer's to rule; until then your tooling does not offer it, and the clean-state refusal on an
  unmerged store path names the route above.
- If you later relocate the store, `opf migrate`, `opf sync` and the provider registry
  (sections 5.4, 5.5 and 5.6) apply in full, including the behind, ahead and divergent rules. They
  are outside this prompt's minimum; do not relocate without implementing them.

Acceptance checks:

- Taking the lease while it is held refuses and changes nothing; a leftover lease from a dead run
  is released only through reconciliation, never seized.
- A conflict marker in a store file, or an unmerged index entry for a store path, makes the
  clean-state check refuse, naming the integration-base route; during a merge in progress whose
  store paths carry neither, the check passes.
- The standalone render write mode refuses and writes nothing while the lease is held by another
  run. Render nested inside another operation's sequence is checked with the first operations that
  run it: the step 8 upgrade and remedy writer, and the step 10 writer.

### Step 8: the store upgrade to 1.3.0 (`opf upgrade`)

Section 4.2 (Layout overview) says "Activation MUST include the tested upgrade in section 9.2 and
deterministic doctor coverage before a writer accepts the new format", and section 9.2 says "The
1.3.0 delta remains a target contract until a tested upgrade and its required readers activate".
Initialization (step 9) and the record writer (step 10) both write the 1.3.0 format, so implement
and test the section 9.2 upgrade here, before either: from 1.2.0, and from 1.0.0 and 1.1.0, since
section 9.2 says "Earlier stores compose their applicable deltas with this delta". The step 6
validator is the required reader.

Test the upgrade only on synthetic older-store fixtures that your tests build themselves, from
sections 4, 9 and 9.2, in throwaway repositories:

- 1.0.0 fixtures, with the base table `[devprocess]` and `standard = "devprocess"`, covering the
  whole origin family section 9.2 names: "a governance-enabled, a decision_support-enabled, a bare,
  and a view-omitting 1.0.0 store" (the 1.0.0 base field names are open point 25);
- a 1.1.0 fixture, with the `[opf]` base table and `spec_version = "1.1.0"`;
- 1.2.0 fixtures, with `spec_version = "1.2.0"`, no imported leaves and no imported counter rows;
- fixtures holding synthetic legacy importer-authored clean records, one of them without
  `created_at` (step 4), fixtures holding the two pending_decision chain shapes, A and B, of open
  point 28, and fixtures at `import_status = "partial"` and `"complete"`;
- for the pre-write unresolved-import check, fixtures that follow its rule location by location (the
  bullet "An unresolved legacy import" below): for each location it lists, a 1.2.0 fixture holding
  one synthetic entry there (a run directory holding one file; for `.working/IMPORT-REPORT.md`, the
  file itself; for `legacy_fragment`, one record at `quarantined` under its type row) at each
  `import_status` value, `"none"`, `"partial"` and `"complete"`, with the entry committed; at
  `"none"`, for each path location, the same entry untracked and not ignored, and untracked and
  matched by an ignore rule; an empty run directory under `.working/staging/import/`; fixtures with
  every location empty, one at an `import_status` outside those three values and one that omits it;
  and the `.working/imports/` entry in a 1.1.0 fixture and, once the 1.0.0 base field names of open
  point 25 are resolved, in a 1.0.0 fixture, so that the composed and staged upgrades run the check
  too.

Commit each fixture in its throwaway repository before the upgrade runs: the upgrade verifies the
working tree clean, ignored files included, over its planned schema and render destinations and the
index collision candidates (section 9.2), with the committed `HEAD` as its restore path over that
scope, and the tracked-store
reading of open point 20 is keyed on `HEAD`. Where a fixture needs a pre-1.3.0 detail the
specification does not give, that is open point 25; do not invent it. None of this imports
anything into your project's store, which is born at 1.3.0 and needs no upgrade.

The governance-enabled and decision_support-enabled 1.0.0 fixtures enable modules, which your own
store never does, so your validator and upgrade must accept them in these fixtures. Section 8.1
says that from 1.1.0 "the governance module carries `maintainer_action` alone"; this prompt reads
that as a governance-enabled 1.0.0 store declaring `governance = true` under `[modules]`, a
`maintainer_action` type row with namespace `MA`, its index and an `MA` counter, beside the
`maintainer_decision` row section 9.2 names. The upgrade keeps all of them, and, since
`maintainer_action` stays an enabled type, the 1.3.0 delta creates
`maintainer_action.imported.index.toml` and the `"imported:MA"` counter row at zero, which your
validator accepts. The `maintainer_action` record schema is open point 25, so keep that index empty
in your fixtures until it is resolved; the remedy writer below refuses to author a module type.

- **The deltas.** Section 9.2 gives three. The 1.0.0 to 1.1.0 delta renames `[devprocess]` to
  `[opf]` and its token to `opf`, bumps the version, removes the retired `decision_support` module
  key, adds the `contribution`, `maintainer_decision` and `preference_pattern` type rows not
  already declared, adds the `CONTRIBUTIONS.md` and `DECISIONS.toml` view rows, widens a declared
  `DECISIONS.md` view's `sources`, extends `counters.toml` with the `CN`, `MD` and `PP` zeros and
  creates each missing empty index, as section 9.2 states in full. The 1.1.0 to 1.2.0 delta is "the
  `spec_version` bump alone", and "the upgrade MUST NOT create provenance for an existing store".
  The 1.2.0 to 1.3.0 delta is "the version bump, registration and create-only initialization of
  missing imported managed leaves for enabled types, and addition of missing imported counter rows
  at zero only where no imported ancestry exists" (registration is open point 27). Preserve
  existing records, evidence, clean counters and imported high-water values; refuse on "a
  populated collision, missing ancestral counter or unprovable prestate". Never create historical
  records, adoption approval or provenance (no `init.toml` for an upgraded store), never change
  posture or import status, never add imported views, and never create an adoption or import
  evidence folder. This prompt's reading of composition: a composed upgrade checks every
  precondition and refusal of every delta it composes before its first write, and its postcondition
  is the composition of their allowed deltas, expressed as ensure-present and ensure-absent "over
  the whole 1.0.0 origin family" (section 9.2).
- **How it writes.** The manifest and counters rewrite is a model regeneration through the step 1
  canonical emitter, never a textual edit, with the byte-reproduction precondition and a
  postcondition that the model diff equals exactly the allowed delta. Other writes use atomic
  replacement of existing files, create-only writes for new files, and views regenerated through
  exclusively created temporary files followed by atomic rename. The upgrade is idempotent, and a
  repeated upgrade is a verified no-op only after a full validator VALID.
- **Preconditions.** Claim the step 7 lease and hold it across the whole mutation, and verify that
  the working tree is clean, ignored files included, over the planned schema and render
  destinations and the index collision candidates. Fail closed on an unresolvable store, a declared
  `spec_version` above the tooling's, a divergence, a held lease, or populated state that
  contradicts the preconditions. Refuse a delta that would put a managed path or view at a
  registered `[unmanaged]` path, naming the collision. Run the unresolved-import check below
  before any write. Also refuse before any write, naming the remedy, which is to commit the earlier
  change first, while any record present at `HEAD` has a working-tree status that differs from its
  status there, even one outside the planned destinations and collision candidates above (open point
  22, this prompt's choice). The retired token `devprocess` is recognized here and nowhere else
  (section 17).
- **Finish.** Regenerate the declared views, require a full validator VALID (step 6), and leave the
  change uncommitted for the adopter's own branch and merge: never stage or commit it, and never
  write the adoption archive. The files and views the upgrade creates therefore stay outside the
  git index, and the step 6 tracked-store check accepts them only on the terms of open point 20
  (this prompt's reading), which is what lets the required VALID be reached without staging.
- **Authority report and the repair route.** The upgrade report MUST enumerate every legacy
  importer-authored clean record whose current authority the section 8.6 firewall withdraws, "naming
  each backlog item that leaves the actionability join, each block that stops granting a stop, each
  ratified `done` item whose only receipt is legacy importer-authored and so stops holding a valid
  receipt, and each record that ceases to be a current state under section 8.6" (section 9.2). It
  also names every record that becomes a current state at 1.3.0, with the record whose place it
  takes (open point 28, this prompt's reading of section 9.2's "so that authority change is
  reported, never silent"). Where a withdrawal would leave the post-upgrade validator below VALID, a
  receipt-stripped `done` item included, the upgrade MUST refuse before any write, naming each such
  record and the remedy section 9.2 gives: "a maintainer-authored clean record that restores or
  supersedes the withdrawn authority under section 8.6, for a receipt-stripped `done` item the
  section 8.6 `maintainer_decision` route, recorded before the upgrade is retried; every named
  remedy MUST be performable through the sanctioned writer without hand-editing canonical files."
  Which withdrawals leave the validator below VALID, and the remedy for each, is open point 28. Make
  every remedy performable: on a 1.1.0 or 1.2.0 store the maintainer records it through your writer
  under the grading of open point 21; from a 1.0.0 store the refusal names the staged route of open
  point 21, whose intermediate upgrade the maintainer commits before the writer runs. The step 6
  validator and the upgrade accept the `maintainer_decision` in the form of open point 26.
- **The remedy writer.** The upgrade is not tested until its remedies have been performed and the
  upgrade retried, and those remedies go through the sanctioned writer, so build part of the step 10
  writer now: `opf record create` and `opf record transition`, for a store declaring 1.1.0 or 1.2.0
  only, graded as open point 21 says. `create` authors the open point 26 `maintainer_decision`. The
  decision-chain remedy of open point 28 needs both, whichever reading the maintainer rules: a
  `create` of a new pending_decision at `open`, under reading 1 carrying a `supersedes` link at
  creation, then a `transition` to unqualified `decided` that appends a `supersedes` link. Building
  both here also lets step 10 extend one writer, and lets any further remedy that a maintainer's
  ruling on open point 28 adds go through it. Give both every rule section 8.8 states for them, as
  step 10 describes: the `/proposed` landing and the `proposed_from` write, restore and removal; the
  pending_decision resolution bundle with an explicit `decided_by` and a clock-read `decided_at`;
  the `supersedes` append and its target checks over the active index together with every archived
  record, and the chain rule over the planned index together with every archived record (section
  8.8); the contribution delivery bundle; and the refusal of a record whose row is not schema-valid.
  Give both the full section 8.8 operation sequence that step 10 describes (lease, byte-reproduction
  precondition, one atomic ID claim, the allowed-delta postcondition, clean destinations, one
  journaled publication, render, final validation, the lifecycle worklog entry), and the open point
  22 refusal while an earlier status change is uncommitted. The remedy writer refuses
  `actor.kind = "importer"`, refuses an importer-authored record as its operand (section 8.8; a
  `supersedes` target is not its operand, by open point 28's reading), refuses a
  `maintainer_decision` by any actor but a maintainer (section 8.5), refuses a module type, whose
  schema is open point 25, and writes only the store's declared format. It does not accept the 1.3.0
  format until step 8 has passed; step 10 then extends the same writer. Writing a 1.1.0 or 1.2.0
  store is not the "new format" that section 4.2 gates.
- **A completed legacy import.** Section 9.2: "A completed legacy import upgrades in place: its
  `import_status` MUST stay `"complete"`, substantiated by its preserved legacy run evidence under
  section 11, and adoption approval, receipt or provenance MUST NOT be fabricated for it." Your
  upgrade keeps the status, writes no adoption approval, receipt, provenance or `init.toml`, and
  requires the full validator VALID, whose substantiation re-reads the preserved legacy run
  evidence and digest-matches every file it enumerates, failing closed on a missing, unreadable or
  digest-mismatched item (sections 11 and 14.1). The evidence format is open point 25: until it is
  resolved, substantiation is cannot-evaluate and the upgrade refuses such a store before any
  write.
- **An unresolved legacy import.** Section 9.2: "An unresolved legacy import MUST be reconciled
  under its original contract before upgrading". Section 11 adds that a partial or complete status
  that neither an adoption receipt with completion results nor preserved legacy import evidence
  substantiates "MUST fail closed, as does a missing, unreadable or contradictory input", and that
  "a staging directory MUST NOT be taken as proof of status". This prompt's choice of detection is
  one rule, which every case below follows. Before any write, for every origin version and every
  `import_status` value, the upgrade inspects every location in the list below, reading the working
  tree directly, so a tracked, untracked or ignored entry counts, and it evaluates each run it finds
  there on its own: evidence that substantiates one run never establishes another's resolution. A
  location that is absent or empty passes: "empty" means the location itself has no run directory in
  it at all. An existing run directory that is itself empty of files is still a run the check finds
  at that location, not an absent or empty location; it is therefore an entry under the
  cannot-evaluate rule below, the same as a populated run. That treatment is this prompt's
  fail-closed choice for these homes-1 origin stores, not a rule the specification states for them:
  section 4.2 says "In homes 2, `staging/` MUST be walked and stray-graded, including empty runs",
  which binds homes 2 only, and section 11's "a staging directory MUST NOT be taken as proof of
  status" bars reading an empty run as resolved without saying how to grade it. A run that the check
  establishes as unresolved makes the upgrade refuse before any write, naming the run's path and the
  reconciliation remedy; a run whose resolution the check cannot establish makes it report
  cannot-evaluate before any write, naming the path. The pinned sources define no format that
  establishes a run's state in any of these locations
  (open point 25), so until that point is resolved every entry in any location gives that
  cannot-evaluate, naming open point 25 as well. Your tooling does not perform the reconciliation.
  The consequences by status:
  - `"partial"` records an import that has not completed, so the upgrade refuses before any write,
    naming the reconciliation remedy, whatever the locations hold, and still names every run it
    finds.
  - `"complete"` needs the substantiation that "A completed legacy import" above describes, and
    every run in every location is still evaluated on its own, so substantiating one completed run
    never passes a second, unresolved one.
  - `"none"` passes this check only when every location is absent or empty, or every run found is
    established as resolved.
  - A value outside those three, or an `import_status` that is missing or unreadable, is
    cannot-evaluate (section 11).

  The locations, each named by the specification for legacy import or ingest state:
  - `.working/imports/`, the retained legacy staging home: section 4.4 says "The legacy staging name
    `imports` remains reserved so legacy content cannot be re-absorbed", and section 4.2 says "The
    legacy `imports/` exclusion remains registered until its writers migrate".
  - `.working/staging/import/<run-id>/` and `.working/staging/ingest/<run-id>/`, the typed staging
    homes: section 14.1 says "Legacy runs MAY occupy" them, and section 4.2 says the staged-plan
    presence test "recognizes import and ingest runs in their typed staging homes". The check also
    counts every other entry under `.working/staging/`, which is this prompt's choice: section 4.2
    says that for legacy stores other kinds "cannot substantiate partial import status until their
    plan readers are registered", so the check cannot show that such an entry holds no import state.
  - `.working/imported/`: section 14.1 retains the import home `.working/imported/import/<run-id>/`,
    section 4.2 names the legacy ingest inventory format `opf.ingest.evidence-inventory/v1`, and
    section 9.2 says the upgrade itself "MUST NOT create such a folder", so the upgrade never put
    anything there, and the check treats any entry it finds there in an origin store as import or
    ingest evidence whose run it must evaluate.
  - The preserved legacy run evidence home of section 14.1, "for the reference tooling
    `.aiqt/import-archive/<run-id>/`", which the check reads at the root of the store repository
    (this prompt's reading of where that home lies). How a store records another legacy home is open
    point 25.
  - `.working/IMPORT-REPORT.md`, which section 4.2 lists as the "legacy import report (pre-1.3.0
    runs only; section 14.1)".
  - Records of the deprecated `legacy_fragment` type, the "Migration quarantine (importer-only)" row
    of section 8.1, which section 9.2 keeps readable. Whether a `quarantined` fragment is an
    unresolved import is part of open point 25, and the LF schema already makes such a record
    cannot-evaluate until then.
  - `.working/journals/`, whose `journals/<kind>/` section 4.2 calls "reserved recovery state" for
    the closed kind vocabulary `import`, `ingest`, `adoption`, `layout`, `preview`, `record`. The
    check counts every entry under `.working/journals/`, which is this prompt's choice, made for the
    reason it counts every other entry under `.working/staging/`: the check cannot show that such an
    entry holds no import state. Section 4.2's "Journals are machine-local even after completion"
    sits in the paragraph after the managed ignore block that lists `/journals/` and that "Homes-2
    init and upgrade MUST render"; this prompt reads it as a homes-2 rule, though section 4.2 does
    not limit that paragraph to homes 2, and the same paragraph opens with "Durable `imported/` and
    `archive/` evidence MUST stay tracked", which is not tied to homes 2. Whichever generation it
    binds, section 4.2 says of a store under pre-1.3.0 tooling, as every origin store of this
    upgrade was, that "the homes-2 names are ordinary store paths there, graded, detected, and
    dispositioned exactly as before", and section 5.1 makes "An untracked or ignored `.working/`
    tree" a hard failure, so in a homes-1 origin store a journal entry is a store path, not
    machine-local data. Section 4.2 also says doctor "MUST NOT consult a journal to decide partial
    status" and "Containment and doctor exclude journals and make no recovery claim", without saying
    which homes generation the second sentence binds; this prompt's reading is that those rules bind
    doctor and containment, not this pre-write check of the upgrade, so the check treats a journal
    entry like any other entry under the rule above. Your own tooling writes no journal there (open
    point 11). Whether journal entries, or those of some kinds, should instead be left out of the
    check is the maintainer's to rule; until ruled, this prompt's choice is that every entry counts.

  If you find another location that the specification names for legacy import or ingest state, add
  it to this list, give it fixtures under the same rule, and ask the maintainer to ratify the
  addition.
- **Legacy records.** "legacy LF records and evidence remain readable and MUST NOT be silently
  converted" (section 9.2): keep their bytes and never convert them. Their schemas, and those of
  the other module types, are open point 25.

Acceptance checks, over synthetic fixtures the test builds:

- A committed 1.2.0 fixture with no records upgrades to 1.3.0, and the only model changes are the
  version bump, the imported leaves and the imported counter rows at zero (open point 27); views
  are regenerated, nothing is staged or committed, the new leaves are absent from the git index,
  and the validator reports valid, its tracked-store check accepting those leaves under open point
  20. After the test commits the change, the validator still reports valid.
- A 1.2.0 fixture with synthetic clean records keeps every record byte and every clean counter.
- Each 1.0.0 origin-family fixture and the 1.1.0 fixture upgrade to 1.3.0 with exactly the
  composed allowed delta: the 1.0.0 fixtures carry `[opf]` and `standard = "opf"` afterwards, the
  retired `decision_support` key is gone, a governance-enabled fixture keeps its
  `maintainer_decision` index byte for byte and its `maintainer_action` row, index and counter, and
  gains `maintainer_action.imported.index.toml` and the `"imported:MA"` row at zero, a
  view-omitting fixture gains no `DECISIONS.md`, every existing record and high-water value is
  kept, no `init.toml` is created, and the validator reports valid.
- Running the upgrade a second time on each result is a verified no-op.
- An upgrade that reaches 1.3.0, and a remedy writer operation that exits 0, each render under the
  lease that operation holds without taking a second one (step 7).
- A held lease, a dirty planned destination, a manifest carrying a comment, a `spec_version`
  above 1.3.0, a populated file at an imported leaf path, and a fixture holding a legacy
  importer-authored clean record without `created_at` (cannot-evaluate until open point 25 is
  resolved) each refuse with nothing written.
  Your validator and your step 8 `create` and `transition` do not discover a `devprocess`
  fixture, and report the zero-match cannot-evaluate of open point 21, whose message names
  `opf upgrade` and not `opf init`.
- A committed 1.2.0 fixture in which one record's working-tree status differs from `HEAD`, with
  every other file committed, the rendered views included, so that only that record's index differs
  from `HEAD` and no planned destination or index collision candidate of the upgrade is dirty,
  refuses before any write and writes nothing (open point 22).
- A 1.2.0 fixture at `import_status = "none"` holding a synthetic legacy importer-authored clean
  backlog item upgrades with that item named in the report. Using `"none"` here is this prompt's
  choice, so that these tests do not depend on substantiating a completed import, whose evidence
  format is open point 25; the specification does not say which status a pre-1.3.0 store with
  importer-authored records carries. This check, the repair route, the decision-chain checks and the
  1.0.0 receipt route below still rest on open point 25's interim reading of the deferral seam; if
  the maintainer rules otherwise, they wait on that point.
- The repair route, end to end: a 1.2.0 fixture whose maintainer-authored ratified `done` backlog
  item has as its only receipt a synthetic importer-authored `done` record refuses before any
  write, naming the record and the remedy. On that unchanged fixture, your step 8 `create`, run
  as a maintainer, records a clean `maintainer_decision` linking `corrects` to the item in the form
  of open point 26, and exits 0 with its final validation valid under open point 21's grading; the
  same `create` run as an assistant refuses. The test commits the decision, retries the upgrade,
  and the upgrade reaches 1.3.0 with the validator reporting valid and the item named in the
  report.
- The decision chains of open point 28, on committed 1.2.0 fixtures of shapes A and B (PD-2
  synthetic and importer-authored), each graded under the reading the maintainer ruled; until the
  ruling, report these checks as not run and step 8 as not passed. Each remedy runs as a maintainer,
  through your step 8 writer under open point 21's grading, and each of its operations exits 0 with
  its final validation valid (or carrying only the pending cannot-evaluate of step 6 for that
  operation's own transition); the test commits after each operation. Under reading 1: shape A
  upgrades to 1.3.0 with no repair offered, the validator reporting valid, and the report naming
  PD-2 as ceasing to be current and PD-1 as becoming current in its place. Shape B refuses before
  any write, naming PD-1, PD-2, PD-3, open point 28 and the remedy, and writes nothing; on that
  unchanged fixture, `create` of PD-4 at `open` with a `supersedes` link to PD-1, a commit,
  `transition` of PD-4 to unqualified `decided` with its resolution bundle appending `supersedes` to
  PD-3, and a commit; the retried upgrade reaches 1.3.0 with the validator reporting valid, PD-4 the
  chain's one current resolution, and no record of the chain named as becoming or ceasing to be
  current. Under reading 2: shape B upgrades to 1.3.0 with no repair offered, the validator
  reporting valid, PD-3 the current resolution, and no record of the chain named as becoming or
  ceasing to be current. Shape A refuses before any write, naming PD-1, PD-2, open point 28 and the
  remedy, and writes nothing; on that unchanged fixture, `create` of PD-3 at `open`, a commit,
  `transition` of PD-3 to unqualified `decided` with its resolution bundle appending `supersedes` to
  PD-2, and a commit; the retried upgrade reaches 1.3.0 with the validator reporting valid, PD-3 the
  chain's one current resolution, and no record of the chain named as becoming or ceasing to be
  current. If the maintainer rules under reading 2 that a `supersedes` target is an operand (open
  point 28), the `transition` refuses, shape A has no remedy, and step 8 is blocked; report that.
- The receipt route from a 1.0.0 fixture: the composed upgrade refuses before any write and names
  the staged route; `opf upgrade --to 1.2.0` reaches 1.2.0 with the validator reporting valid and
  nothing staged; a `create` of the decision run now refuses on its dirty planned destinations and
  writes nothing; the test commits the intermediate upgrade; the maintainer's decision is then
  recorded through the writer (exit 0) and committed; and the retried upgrade reaches 1.3.0 with
  the validator reporting valid.
- Every unresolved-import fixture refuses before any write and writes nothing. The `"partial"`
  fixtures name the reconciliation remedy, and every fixture names the path of each entry it holds,
  with a cannot-evaluate naming open point 25 for each entry, whatever its git state. This holds
  under `"complete"` too, where the report names the entry's path beside the substantiation gap,
  which shows that the check runs under every status. The fixtures at an unknown or missing
  `import_status` report cannot-evaluate.
- A fixture at `import_status = "complete"` with no preserved legacy run evidence refuses before
  any write and leaves the status unchanged.
- Once open point 25 is resolved: a fixture at `import_status = "complete"` with intact preserved
  evidence upgrades with the status still `"complete"`, no approval, receipt, provenance or
  `init.toml` created, and the validator reporting valid; one digest-mismatched evidence file
  makes it refuse before any write; a fixture holding synthetic LF or module records keeps their
  bytes and is graded as the resolved deferral seam says; and a fixture holding a legacy
  importer-authored clean record without `created_at`, its omission recorded through the resolved
  provenance reference, keeps its bytes and validates before and after the upgrade, while the same
  record without that recorded omission is a finding; and, for every location of the
  unresolved-import check, a fixture holding a run that the resolved format shows unresolved refuses
  before any write, naming that run, one whose state cannot be established reports cannot-evaluate,
  and one shown resolved passes that check, including a `"complete"` fixture whose intact evidence
  substantiates one run while a second, unresolved run occupies a location, which refuses, naming
  the second run. Until then, report these checks as not run;
  step 8 is then not passed and step 9 does not start (open point 25).

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
  section 14 requires of investigation. This prompt's choice is to bind it through the init plan's
  `inventory_digest` (OPF-INIT-D2B.md, "Plan Format", which lists only the field name); whether
  the contract's "missing coverage" refusal then reaches every inventory entry is part of open
  point 17. In a fresh start no entry lies at one of the store's
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
  `worklog.toml` with no rows, every clean index and imported leaf, `init.toml`,
  `.working/README.md` in the fixed wording of open point 15, and `CHANGELOG.md` holding only the
  section 10.4 opening note, in fixed wording that you choose and record. The changelog is required,
  not optional: section 6.3 says the public changelog "lives at the product repository root as
  `CHANGELOG.md`", the manifest declares it as a curated deliverable, and at `required` an
  unreadable or unresolvable declared input is a failure (section 11). With no releases and no
  summary rows, range coverage and freeze pass on it. Section 11, "Defaults", requires the posture
  and import status values. Then render the declared views, `VERSION` included (open point 8). Which
  files are sources and which are views is part of open point 17.
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
    never allocates twice (review register row F28); an existing source counts only when it is
    "verified byte-identical to its recorded plan payload on resume" (OPF-INIT-D2B.md, "Success
    wording").
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
  with `.working/` matched by an ignore rule, init refuses and writes nothing. So it does when a
  `VERSION` or `CHANGELOG.md` sits only in the index or only in `HEAD` (review register row F17,
  "worktree/index/HEAD"), and when a store committed in `HEAD` is deleted only in the worktree or
  the index (row F04: "A deletion present only in the worktree or index refuses fresh init").
- A plan without the maintainer's explicit attributed acceptance is not applied (row F13).
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
  landing at `/proposed`; a rejection restores exactly the recorded `proposed_from` state, and
  leaving the `/proposed` status, by rejection or ratification, removes the field; writes or
  removes the pending_decision resolution bundle and the contribution delivery bundle exactly as
  section 8.8 states; runs the `supersedes` target checks over the active index together with every
  archived record, and the chain rule over the planned index together with every archived record,
  before anything is written (section 8.8). It
  refuses to act on, or overwrite, a record whose current row is not schema-valid, `proposed_from`
  included (section 8.8).
- `done-with-receipt`: maintainer-only; moves a backlog item to unqualified `done` and creates its
  `done` receipt in the same act. A backlog item reaches unqualified `done` only this way
  (section 8.8: "A backlog item MUST reach unqualified `done` only through `done-with-receipt`").
- `worklog-append`: appends one `recorded` entry to the unreleased tail; refuses an entry inside a
  released span and a `detail` that opens with the lifecycle grammar.
- Handoff supersession: section 8.5 says "Posting a new handoff MUST supersede the previous in the
  same act", and section 8.8 gives no operation for it. Implement it only as open point 23
  describes, as the maintainer-performed replacement, once the maintainer has ruled. Until then,
  this prompt's reading is that `create` refuses a second `current` handoff rather than posting it
  in two acts; report that gap, since section 14.1 says enforcement "MUST NOT ship before the writer
  can perform every operation it forces" (step 14, Ordering). Once it is implemented, the operation
  still refuses an assistant or automation actor before anything is written, naming the
  maintainer-performed replacement as the path, since that actor's move of the predecessor would
  land `superseded/proposed`, which is not terminal (section 8.4), and the supersession would not
  complete in the same act. `transition` refuses a handoff's `current` > `superseded` move for every
  actor, so no handoff leaves `current` outside that operation (open point 23).
- Contribution re-send: section 8.5 says "A re-send MUST be a new record linking `supersedes`; the
  superseded record records `superseded`", and states no same-act rule for it, so the existing verbs
  perform it: a `transition` of the old contribution from `sent` to `superseded` (an assistant or
  automation lands `superseded/proposed`), a commit, then a `create` of the new contribution linking
  `supersedes` to it. Doing the transition first is this prompt's choice, so that no intermediate
  state links `supersedes` to a record that is not yet `superseded`; the commit between them follows
  from open point 22. The opposite intermediate state remains, and open point 23 discloses it: until
  the re-send is created, a committed contribution sits at `superseded`, or `superseded/proposed`,
  with no record linking `supersedes` to it, and the validator reports no finding for that (open
  point 23's reading). A `superseded/proposed` record does not record the supersession, since a
  `/proposed` status is not terminal (section 8.4; open point 23). So `create` refuses a
  contribution linking `supersedes` to a clean predecessor authored by a non-importer actor that is
  not at unqualified `superseded`, naming the remedy, and an assistant's re-send takes one more step
  before the `create`: a maintainer ratifies the predecessor's `superseded/proposed`, and that
  ratification is committed. A maintainer's own re-send lands unqualified `superseded` and needs no
  ratification. The guard is for a predecessor that owes the state change. An importer-authored
  predecessor, in the imported series or a legacy importer-authored clean record, is immutable
  history whose recorded state stands (sections 8.4 and 8.6): `create` accepts a same-type
  `supersedes` link to it whatever state it records, writes nothing to it, and never asks for a
  ratification that the writer could not perform, since it refuses a transition on such a record
  (open point 23).
- Older stores: step 8 built `create` and `transition` for a store declaring 1.1.0 or 1.2.0, under
  the grading of open point 21, so every remedy of open point 28 was performable before activation.
  Extend the same two subcommands to the 1.3.0 format without changing their older-store
  behaviour; `done-with-receipt` and `worklog-append` need no older-store mode, since no remedy
  uses them.
- The `import --batch` mode exists in the specification, but this fresh start does not implement
  or use it. The clean-series subcommands refuse `actor.kind = "importer"`.

`create`, `transition` and `done-with-receipt` each append their own worklog entry in the same
transaction, whose `detail` opens with `opf-record create <ID> <status>` or
`opf-record transition <ID> <from> -> <to>`, and a rejection's entry records its reason.
`done-with-receipt` appends two, one per changed record (open point 16).

Every subcommand, `worklog-append` and the open point 23 handoff operation included, refuses while
any record present at `HEAD` has a working-tree status that differs from its status there, naming
the remedy, which is to commit the earlier change first (open point 22, this prompt's choice). The
refusal comes after the first item of the operation sequence below, which resolves the store and
reconciles any interrupted transaction, and before any other write.

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
  reports the change as pending until commit; with any other cannot-evaluate in the store, or any
  finding other than a view drift that the verb's own render remedies, the same transition exits 2.
- With no handoff `current`, an assistant's `create` of a handoff lands it at `current` and exits 0.
  While a `current` handoff exists, a plain `create` of another handoff refuses and writes nothing
  for every actor, and so does any actor's `transition` of that handoff to `superseded`. Once the
  open point 23 operation is implemented: a maintainer's replacement of a committed `current`
  handoff exits 0, and the one transaction leaves the new handoff at `current` linking `supersedes`
  to the old one, the old one at unqualified `superseded` with no `proposed_from`, exactly one
  `current` handoff, one lifecycle worklog entry per changed record in the forms open point 23
  names, and a final validation that reports valid or only the pending cannot-evaluate (step 6) for
  the old handoff's move from `current` to `superseded`. Killing the process between journal write
  and publication leaves the store, after reconciliation, at its prestate or at that poststate,
  never with only one of the two handoffs changed. An assistant's or automation's replacement of the
  same handoff refuses, names the maintainer-performed replacement, and writes nothing, the old
  handoff still `current`.
- An assistant re-send of a `sent` contribution: the assistant's `transition` lands the old record
  at `superseded/proposed` with `proposed_from = "sent"` and exits 0; after the test commits it,
  the assistant's `create` of the new contribution linking `supersedes` to it refuses, names the
  ratification remedy and writes nothing (open point 23's reading); a maintainer's ratification of
  the old record to unqualified `superseded` exits 0; after the test commits it, the same `create`
  lands the new record at `proposed` linking `supersedes` and exits 0; and each operation that exits
  0 appends its own lifecycle worklog entry. A maintainer's re-send, a `transition` to unqualified
  `superseded`, a commit, then a `create`, exits 0 at each operation.
- Re-sends that supersede immutable history, on synthetic fixtures only, never in your project's
  store: (a) a store declaring 1.3.0 at `import_status = "none"` whose imported series holds a
  contribution at `acknowledged`, with its preserved original at the store-relative path its
  `import.source` names and a `source_sha256` that matches it (section 8.3); and (b) a committed
  1.2.0 fixture holding a synthetic legacy importer-authored clean contribution at `sent`, upgraded
  by your step 8 upgrade to 1.3.0 and committed. In each, a maintainer's `create` of a new
  contribution linking `supersedes` to that record lands the new record at `proposed` and exits 0
  with its final validation valid; the target's bytes and recorded state are unchanged, and the
  allowed delta holds no transition delta for it; an assistant's `create` of the same link also
  exits 0, asking for no ratification; and a maintainer's `transition` of the target to `superseded`
  refuses as a transition on an importer-authored record and writes nothing (sections 8.4, 8.6 and
  8.8; open point 23). Check (b) rests on open point 25's interim reading. Using `"none"` in check
  (a) is this prompt's reading, so that the check does not depend on an adoption receipt, whose
  format the specification does not give (open point 14): section 11 says "`import_status = "none"`
  means clean start with no approved migrate-source import" and that a partial or complete status
  that no adoption receipt or preserved legacy import evidence substantiates "MUST fail closed, as
  does a missing, unreadable or contradictory input". If the maintainer rules that an imported
  series under `"none"` is a contradictory input, check (a) waits on open point 14; report the
  ruling with the check.
- `done-with-receipt` appends a transition-form entry for the backlog item and a create-form entry
  for the receipt; and after an assistant lands `done/proposed`, `done-with-receipt` before any
  commit refuses and writes nothing, as do a `create` and a `worklog-append` while that transition
  is uncommitted.
- After step 10's extensions, every step 8 check that uses the remedy writer still passes.
- The decision chains of open point 28 on stores declaring 1.3.0, graded under the reading the
  maintainer ruled; until the ruling, report these checks as not run. These checks rest on open
  point 25's interim reading. On a shape A store with a new maintainer-authored pending_decision at
  `open` committed, a maintainer's `transition` of that new record to unqualified `decided` with a
  `supersedes` link to PD-2: under reading 1 it refuses and writes nothing, since the chain would
  then hold two current resolutions; under reading 2 it exits 0 with its final validation valid (or
  carrying only the pending cannot-evaluate of step 6 for that transition), the new record the
  chain's one current resolution, unless the maintainer has ruled that a `supersedes` target is an
  operand, in which case it refuses and writes nothing and step 8 is already blocked. On a shape B
  store, under either reading, a maintainer's `create` of PD-4 at `open` with a `supersedes` link to
  PD-1 exits 0 with its final validation valid and PD-3 the one current resolution; after the test
  commits it, a maintainer's `transition` of PD-4 to unqualified `decided` with a `supersedes` link
  to PD-3 exits 0 with its final validation valid (or carrying only the pending cannot-evaluate of
  step 6 for that transition) and PD-4 the one current resolution. Report the ruling with the
  checks.
- With an assistant's `done/proposed` transition of a backlog item left uncommitted and every other
  file, `worklog.toml` and the views included, committed, a `create` of a finding refuses and writes
  nothing, though none of its planned destinations is dirty (open point 22).
- Each writer operation that exits 0 renders under the lease it holds without taking a second one
  (step 7).
- A final validation failure exits 2 and leaves the planned change in the working tree with
  recovery advice naming the planned paths.
- Killing the process between journal write and publication leaves the store, after reconciliation,
  exactly at its prestate or exactly at its poststate, and the next operation refuses until that
  reconciliation is inspected; the open point 22 refusal is not reached before that reconciliation
  runs.
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
  operation sequence, including the open point 22 refusal while an earlier status change is
  uncommitted, which comes after any interrupted transaction is reconciled and before any other
  write.
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
- A release cut run while a record's working-tree status differs from `HEAD`, with every other file
  committed, `worklog.toml` and the rendered views included, so that only that record's index
  differs from `HEAD` and no planned destination of the cut is dirty, refuses and writes nothing
  (open point 22).
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
  records, active blocks, unresolved decisions, unresolved fragments, unexpired waivers, the current
  handoff and the unreleased worklog tail never rotate (section 12), and at 1.3.0 the imported
  series never rotates.
- Each rotation writes that year's `archive.toml`, enumerating every moved ID (and, for the
  worklog, every moved span) and its destination. Counters are untouched. Validation confirms that
  every ID exists in exactly one active or archived location, and coverage gates read active and
  archive together, and the step 6 no-deletion check confirms that no ID the prior committed
  snapshot holds has left the store. Rotation thresholds are open point 13.
- Rotation stages exactly the files it writes, created and rewritten, and leaves the commit to the
  maintainer; this is this prompt's choice in open point 20, which names the alternatives.
- Rotation writes the store, so it takes the step 7 lease before any write (section 5.7: "before
  mutating the store, a run MUST take the lease") and follows the parts of the step 10 operation
  sequence that apply to it: reconcile any interrupted transaction first, the byte-reproduction
  precondition, an allowed-delta postcondition, clean destinations, one journaled publication,
  render and a final validation, with the lease held throughout, and the open point 22 refusal while
  an earlier status change is uncommitted. Unlike the writer, it stages its files as the bullet
  above says.
- Section 13 (Tamper evidence) explains how history, frozen digests, index reconciliation and
  archive enumeration combine.

Acceptance checks:

- After a rotation every gate gives the same answer as before it, both before and after the
  maintainer commits it; unstaging one of its new archive files afterwards makes the tracked-store
  check report a finding.
- A `/proposed` record and an unreleased worklog entry are refused for rotation.
- Rotation refuses and writes nothing while the lease is held, and while a record's working-tree
  status differs from `HEAD` with every other file committed (open point 22).
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
  arise in CI, and CI does not re-evaluate the transitions, deletions or counter decrements inside
  the commits it checks; disclose that as a residual (step 14). If you choose to compare with an
  earlier commit instead, such as the merge base, label that your choice and accept the pending
  cannot-evaluate only on the terms of open point 22.
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
- The job fails on a branch whose checked-out revision holds `.opf.local.toml`, and passes once a
  later commit on that branch removes it, though an earlier commit still holds it.

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
- **Merge commits.** git runs the `pre-commit` hook from `git commit` only; a merge that `git merge`
  concludes without a conflict runs the `pre-merge-commit` hook instead (githooks(5)), and that is
  the case section 5.7 leaves to doctor: "A merge that resolves without a conflict yet duplicates an
  ID is caught by `opf doctor`". So install the same check as the `pre-merge-commit` hook too. Open
  point 22 compares with `HEAD`, which in a merge commit is the first parent alone, so it would
  grade the merged branch's committed changes as this commit's. This prompt's choice for a merge
  commit: run the no-deletion check and the counter comparison against each parent in turn, so every
  ID that either parent holds must survive and no counter may fall below its value in either parent;
  skip transition legality, so the pending cannot-evaluate does not arise; and run every other check
  as for any other commit. Never compare a merge commit with `HEAD` alone: a merge such as
  `git merge -s ours` concludes without a conflict and drops every record the merged branch added.
  The per-parent comparison needs every parent. While `git commit` concludes a merge, the
  `pre-commit` hook finds them as `HEAD` and the commits that `MERGE_HEAD` names (gitrevisions(7):
  "records the commit(s) which you are merging into your branch when you run git merge"). The
  `pre-merge-commit` hook has no documented way to name the merged commit: githooks(5) says only
  that it "takes no parameters, and is invoked after the merge has been carried out successfully",
  and git need not have written `MERGE_HEAD` by then. So, as this prompt's choice, the check running
  as `pre-merge-commit` runs no comparison and exits non-zero with a message telling the operator to
  conclude the merge with `git commit`; githooks(5) says a non-zero exit "causes the git merge
  command to abort before creating a commit". githooks(5) does not say what state that abort leaves,
  so confirm it on each git version you support, with the acceptance test below: the merge result
  stays in the index and `MERGE_HEAD` names the merged commit, so `git commit` concludes the merge
  through the `pre-commit` hook. If a git version you support leaves no `MERGE_HEAD` after the
  abort, the message tells the operator instead to rerun the merge as `git merge --no-commit`, which
  git-merge(1) says stops "just before creating a merge commit", and to conclude it with
  `git commit`. Disclose that a merge commit's status changes are not graded, and that
  `git merge --no-verify`, like `git commit --no-verify`, skips the hooks, which is part of the
  per-clone hook residual of section 14.1.
- **Instructions, on every platform (this prompt's choice).** Section 14.1 requires instructions
  only "on a platform without verifiable denial"; adding them on every platform, beside any deny
  hook, is this prompt's choice. Add OPF working rules to each platform's instructions file
  (for example `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, `GEMINI.md` for Gemini CLI, and a
  rule file under `.cursor/rules/` for Cursor; confirm each file against that platform's official
  documentation, since the specification names platforms, not files). Editing an inventoried
  instructions file follows the step 9 rule for planned instruction-surface edits. The rules: the
  store is the source of truth and you record before you claim (section 3, "Records first"); write
  only through the record writer, never hand-edit store TOML or a generated view (sections 3 and
  4.6); read every timestamp from the clock; author as `actor.kind = "assistant"`; land every
  terminal transition, and every entry into a gated state (`block` `active`, `contribution` `sent`,
  `preference_pattern` `active`), as `/proposed` for the maintainer to ratify (section 8.4); but a
  record created directly in a terminal factual or ACT state that awaits no ratification, such as a
  `reference` or an `autonomous_decision`, carries no qualifier, and a worklog entry never takes
  `/proposed` (sections 8.3 and 8.4); never author a
  maintainer_decision, never run `done-with-receipt`, never weaken the posture (section 11), and
  never publish a changelog summary (section 7.3); append a worklog entry for each change; and
  commit each writer change before the next writer operation, which otherwise refuses (open point
  22).
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
  forces (section 14.1). Until the maintainer has ruled on open point 23 and you have implemented
  the handoff operation, the writer cannot post a second handoff, so no deny rule may cover the
  handoff index. The deny hook then does not cover the whole record series, the own-platform
  denial check below and checklist item 17 do not pass, and you claim no conformance until the
  ruling. Once it is implemented, a maintainer posts a replacement handoff through the writer, and
  the writer refuses an assistant's or automation's replacement under section 8.4 (open point 23).
  This prompt's reading is that a deny rule on the handoff index then forces no operation the writer
  cannot perform, since the `/proposed` rule of section 8.4, not the deny rule, keeps that actor
  from completing the supersession. The deny rule also covers a handoff's move from `current` to
  `superseded` with no successor, which section 8.5's table lists; under open point 23's reading
  the specification defines no such operation, so the rule forces no operation there either.
  Disclose that an assistant or automation cannot replace a handoff, and that a handoff leaves
  `current` only by a maintainer's replacement.
- **Residuals to disclose.** Per-clone hook installation and bypass, canonical hand edits, shell or
  interpreter wrapping, same-user tampering, and unverified platform denial (section 14.1), plus
  each platform's own residual, and, under open point 22, that CI does not re-evaluate the
  transitions, deletions or counter decrements inside the commits it checks, and that the merge
  check above does not grade a merge commit's status changes. Copy the relevant residuals of section
  17 into your project's documentation as well.

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
- The pre-commit check blocks a commit that adds `.opf.local.toml`. With `HEAD` holding the file,
  it allows the commit that removes it from the index with `git rm --cached` and adds an ignore
  rule for it.
- Installed as both hooks, the check stops every `git merge` that would create a merge commit
  without a conflict before it creates that commit, naming `git commit` as the way to conclude it;
  after that stop the merge result is in the index and `MERGE_HEAD` names the merged commit (or, on
  a git version that leaves no `MERGE_HEAD`, the message names `git merge --no-commit`). With the
  merge concluded by `git commit`, the check blocks a merge whose merged branch, committed with the
  hooks bypassed, carries a hand-edited view; it allows a merge of a branch whose committed writer
  transitions leave no other defect; and it blocks a merge commit whose result drops a record that
  either parent holds or lowers a counter below its value in either parent, a `git merge -s ours` of
  a branch that added a record included.
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
   declared view drifts. Its last sentence, "Consumer repointings match the plan.", is not
   applicable when a fresh start repoints no consumer; say so.
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
test name). Any failed or not-run item means you do not claim conformance. Where a check follows a
maintainer's ruling on an open point, report the ruling with it.

1. The pinned sources were fetched and each SHA-256 matched this prompt's table.
2. No code from `jposluns/guardrails` is imported, vendored, copied or called at run time.
3. The store is new: `import_status = "none"`, the imported series is empty, no record has
   `actor.kind = "importer"`, and nothing exists under `.working/imported/`.
4. `.opf.toml` resolves the store; resolution and discovery fail closed in the step 2 cases.
5. `manifest.toml` declares `standard = "opf"`, `spec_version = "1.3.0"`, no `homes` key,
   `layout = "inline"`, `posture = "required"`, the providers as ratified under step 3 (this
   prompt's choice: `local-directory` and `generic-git-remote`), and no module enabled.
6. Every enabled baseline type other than `worklog` has a clean index and an imported index; the
   worklog has `worklog.toml` and `worklog.imported.toml`; `counters.toml` covers every clean and
   imported namespace; `version.toml` and `worklog.toml` exist with `schema = 1`; `init.toml`,
   `CHANGELOG.md` and the `.working/README.md` of open point 15 exist.
7. Every file under the machine store is lowercase; every generated deliverable is uppercase and
   carries the section 10.3 header (the projection as a TOML comment block; `CHANGELOG.md` and the
   init-written `.working/README.md` excepted, and `VERSION` as ratified under open point 8).
8. The validator runs the full section 11 roster, with counter monotonicity as both step 6 checks,
   plus the section 8.2 no-deletion check per series, the imported-series checks and the homes-1
   control-area registration, exits 0, 1 or 2 as step 6 describes, and fails closed on unreadable
   input; its tracked-store check admits unstaged files only on the terms of open point 20.
9. Renders are byte-reproducible and the drift check catches a one-byte edit; the render write
   mode, the pre-commit check and the writer accept the pending cannot-evaluate of section 8.8,
   item 7 only on the terms of step 6, step 10 and open point 22, and accept no other
   cannot-evaluate and no finding, apart from the view drift that the render write mode and the
   verb's own render remedy by rendering.
10. The upgrade to 1.3.0 of step 8, from 1.0.0, 1.1.0 and 1.2.0, with the completed-legacy-import
    route, the receipt repair route (from 1.2.0, and from 1.0.0 with the intermediate upgrade
    committed before the writer runs) performed end to end, the decision-chain checks of open point
    28 under the reading the maintainer ruled, with that reading's remedy performed end to end and
    the report naming every record that becomes or ceases to be current, and the pre-write
    unresolved-import check over every location and `import_status` value of step 8, is implemented
    and passed every step 8 check on synthetic fixtures before initialization and the record writer
    accepted the 1.3.0 format (section 4.2); every detail of open point 25 is resolved, and no step
    8 check was left not run.
11. Initialization implements `opf.init.coupled/v1` (this prompt's scope choice; see "Choices to
    make before you start"): exit 0 only on fresh observations, with the exact success sentence;
    init-created sources staged as exact entries; views unstaged; no commit created; final
    validation after the lease removal; a finalization or lock-release failure prevents exit 0.
12. The record writer enforces the `/proposed` rule, the per-type transition table and rules of
    section 8.5 (with the handoff supersession as the maintainer-performed replacement of open point
    23, as ratified, completing the supersession in the same act and refusing an assistant or
    automation replacement and a handoff's move to `superseded` with no successor, and the
    contribution re-send as a `transition` and a `create`, a non-importer predecessor at unqualified
    `superseded` first, and an importer-authored predecessor left at its recorded state), the
    resolution and delivery bundles, unqualified `done` only through `done-with-receipt`, the
    lifecycle worklog entry, the eight-item operation sequence, and exits 0 or 2.
13. Releases freeze worklog spans; the changelog passes range coverage and freeze; no summary was
    published without the maintainer's curation.
14. The lease, the clean-state check and the integration-base merge rule are enforced.
15. Rotation, if implemented, preserves every gate's answer; if not implemented, nothing rotates.
16. The CI job checks out full history, runs the validator in no-store-fails mode, the store
    identity check against the pinned identity, and the render check, with the 0, 1, 2 status
    rule, and no step can pass by failing.
17. Each of Claude Code, Codex, Gemini CLI and Cursor is covered, with per-platform evidence of a
    verified deny hook or a disclosed instructional fallback; the pre-commit check is installed as
    both the `pre-commit` and the `pre-merge-commit` hook;
    the section 14.1 residuals are disclosed; and the maintainer's attestation of server-side
    branch protection is recorded apart from the local probe results.
18. Every open point you filled is listed with your choice and its ratification status.
19. The conformance statement uses only the section 16 vocabulary, for example
    "`conformant_for_declared_scope` (OPFiles base, spec 1.3.0 on homes 1; self-asserted; profiles:
    none declared)", or `migration_incomplete` while an inventory entry is unresolved, and names
    its scope, its exclusions (relocation, import, adoption of pre-existing files, modules, and any
    open point you could not settle other than open point 25, which blocks the claim rather than
    narrowing it) and every cannot-evaluate result.

## Out of scope for this prompt

Import and the imported-series writer (section 8.8, `import --batch`; section 14.1, post-adoption
import), adoption of pre-existing files (section 14), migrating an existing release pipeline
(section 14.3), store relocation and sync (sections 5.4 to 5.6), schema upgrades other than the
upgrades to 1.3.0 of step 8 (section 9.2), the homes-2 generation and its homes migration
(sections 4.2 and 9.2), the modules (section 8.5) beyond what the step 8 upgrade fixtures need, and
profiles (section 9.1). If your project needs any of these, read those sections at the same pinned
commit and extend the plan, under the same rules.
