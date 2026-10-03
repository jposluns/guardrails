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
  and its imported series stays empty.
- **From the specification.** Where this prompt and the specification differ, follow the
  specification and report the difference. Where the specification is silent or open, do not invent
  behaviour and present it as the standard's: follow the procedure in "Where the specification is
  silent or open" below.

Work through the implementation plan in order. Each step ends with acceptance checks; do not start
the next step until the current step's checks pass, and report any check you could not run as not
run, never as passed.

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
- Section 4.2 (Layout overview) says the base spec 1.3.0 requirements for adoption and the
  imported series are a target contract, and that the homes-2 contract (`spec_version = "2.0.0"`,
  `[opf].homes = 2`) is a separate, later activation. Section 9 (The manifest) says its manifest
  example describes the 1.3.0 target on legacy homes.
- The prompt pack manifest at the pinned commit lists no members. The specification uses the
  prompt pack only for post-adoption import (section 14.1, One approval and completion), which a
  fresh start does not perform, so you need nothing from it.
- Cite the specification as this prompt does: by file and section heading, for example
  "OPF-SPEC.md, section 8.2 (ID namespaces and counters)".

## What you must not do

- Do not import, vendor, copy, translate or execute this repository's code as part of your
  implementation, and do not make your implementation depend on it at run time. This covers
  everything under `opf/tools/`, `opf/enforcement/` and `tools/`. Writing your own equivalent of
  the CI recipe is required; copying it is not allowed.
- Do not import existing records. Do not write records with `actor.kind = "importer"`, do not
  write to the imported series (`<type>.imported.index.toml`, `worklog.imported.toml`), do not set
  `import_status` to anything but `"none"`, and do not create evidence under `.working/imported/`.
  Pre-existing operational files in your project stay where they are unless the maintainer decides
  otherwise (see step 7).
- Do not claim conformance unless every item of the final conformance checklist passes, and even
  then use only the scoped vocabulary of OPF-SPEC.md, section 16 (Conformance vocabulary and
  claims). Never write an unqualified "OPFiles conformant" or "OPF conformant", and say that the
  claim is self-asserted.
- Do not present a choice you made in a gap of the specification as a requirement of the standard.
- Do not author records that only a maintainer may author or ratify (see step 13), do not weaken
  the enforcement posture, and do not publish a changelog summary without the maintainer's
  curation.
- Do not name this repository, its maintainer or any adopter in your store's base schema
  vocabulary (OPF-SPEC.md, section 15, Genericization boundary).

## Choices to make before you start

These choices follow from the specification and from the fresh-start scope. Confirm them with the
maintainer before step 1, and keep the confirmation in your implementation notes.

- **Specification version.** Implement base spec 1.3.0 on legacy homes (homes 1): write
  `spec_version = "1.3.0"` and no `homes` key. Do not declare `homes = 2` or
  `spec_version = "2.0.0"`; both are reserved for a later activation (section 4.2, Layout overview;
  section 9.2, Store schema upgrades). Omit the `[opf].worklog` key or set it to `1`; `2` is
  reserved and refused (section 9, The manifest). Note that section 9.2 says the 1.2.0 reference
  validator refuses a 1.3.0 declaration as above its ceiling, so the reference tooling at the pinned
  commit will not validate your store; that is expected and is not a fault in your store.
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

## Where the specification is silent or open

The specification leaves the points below open, or states them only as examples. For each one,
choose the narrowest deterministic behaviour that satisfies every rule the specification does
state, write the choice in your implementation notes with the section it fills, and ask the
maintainer to ratify it. Once the store exists, record each open question as a `pending_decision`
record; only the maintainer records the ruling. In your conformance report, list these choices as
implementation choices, not as requirements of the standard.

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
   command vocabulary in section 1 names no release verb.
10. **Exit codes and flags beyond `opf record`.** Section 8.8 fixes `opf record` at exit 0 or 2,
    and OPF-INIT-D2B.md states when coupled init may exit 0. The reference CI recipe uses 0, 1 and 2
    for `doctor` and `render --check` (step 12). The specification fixes no other flags or exit
    codes.
11. **Journal location.** Section 8.8, item 6 requires a crash-durable journaled transaction and
    names only the reference tooling's homes-1 location, which is under `.aiqt/`, an AIQT-owned
    directory (section 15). Do not use `.aiqt/`. Choose a location, make it machine-local, and
    record the choice.
12. **The lease file in an in-repo store.** Section 5.7 (The store consistency contract) defines
    the lease and its contents but does not say whether `lease.toml` is committed in the in-repo
    pattern, or its field names (the QUICKSTART notes call the file illustrative).
13. **Rotation thresholds.** Section 12 (Rotation, archive, and retention) allows rotation on
    manifest-declared age or size thresholds but names no manifest keys.
14. **Adoption receipt for an empty start.** Section 14 says adoption ends with an adoption receipt
    and section 14.1 binds an `opf.adoption.plan/v2` plan, but the specification gives no receipt
    format and does not say whether `opf init` in a project with nothing to disposition still
    needs one.
15. **The `.working/README.md` deliverable.** Section 4.2 lists it as an "ownership and
    regeneration note" but does not say whether it is a rendered view.
16. **Worklog entries written by the record verbs.** Section 8.8 fixes the opening line of their
    `detail` but not their `kind`; section 6.2 lets a manifest register more kinds but names no key.
17. **Initialization formats.** OPF-INIT-D2B.md lists the field names of its plan, outcome and
    bootstrap formats but not their value types.

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
      <type>.index.toml      one per enabled baseline type except worklog (sections 4.2, 8.1)
      <type>.imported.index.toml, worklog.imported.toml   empty imported leaves (section 4.2)
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
  section 9.2, Store schema upgrades).
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
- **version.toml.** `schema = 1`, then append-only, immutable `[[release]]` rows (`version`,
  `date`, `worklog_span`, `coverage_digest`, and the optional `imported` flag, which a fresh store
  never sets) and `[[summary]]` rows (`covers`, `status`, `digest`, `superseded_by`). Summary rows
  hold digests and ranges only, never prose (section 6.1; Appendix B).
- **worklog.toml.** `schema = 1`, then `[[entry]]` rows with `id`, `date`, `actor`, `kind`,
  `summary`, optional `detail`, `links` and `refs` (section 8.3, reduced envelope; Appendix C). The
  base kinds are `added`, `changed`, `fixed`, `removed`, `security`, `docs` and `infra`
  (section 6.2). Clean entries carry no `status` key in Appendix C; their status is fixed as
  `recorded` and never takes `/proposed` (section 8.4).
- **Imported leaves.** Create `<type>.imported.index.toml` for every enabled type and
  `worklog.imported.toml`, create-only and empty, in the same act that creates the clean index
  (section 4.2). They stay empty in a fresh store.
- `version.toml` and `worklog.toml` are always single files written under the store lock, in either
  layout (section 9, storage layout).

Acceptance checks:

- The manifest validates. A wrong `standard` value leaves the store undiscovered, which is
  cannot-evaluate (section 4.5), and a `spec_version` above your implementation's ceiling is a
  fail-closed INVALID finding naming the upgrade remedy (section 9.2, last paragraph).
- Every enabled type has exactly one clean index and one imported leaf; the worklog has its
  ledger and its imported ledger.
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
  (section 8.3; section 8.7, Extensions).
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
- **Per-type rules (section 8.5).** Among them: blocked-ness is never stored, it is derived; a
  ratified `done` backlog item has its one-to-one `done` receipt linked `receipt_of`; a
  pending_decision's resolution bundle is all-or-none, with exactly one current effective
  resolution per supersession chain; at most one `current` handoff; a contribution's delivery
  bundle fields appear only at the states the table allows; a `maintainer_decision` takes
  `actor.kind` `maintainer` (or `importer`, which a fresh store never uses).
- **No resurrection and supersession.** A record in an unqualified terminal state never re-enters
  a working state; a revived concern is a new record linking the old one; supersession is a
  `supersedes` link (section 8.4).
- **Links and refs.** `rel` comes from the closed vocabulary `supersedes`, `resolves`,
  `remediates`, `receipt_of`, `corrects`, `follows`, `relates`, `exemplifies` and `derives_from`;
  `exemplifies` targets a preference_pattern. `refs` are `{kind, locator, note}` with `kind` one of
  `path`, `url` or `doc` (section 8.6, Links and reference capture).
- **Actionability.** A backlog item is actionable when it is `open` or `active` and no
  unqualified `active` block scopes it (section 8.5, "Actionability").

Acceptance checks:

- Validation refuses: an ID in the wrong namespace, an unknown key, a `/proposed` worklog entry, an
  assistant transition that lands an unqualified terminal or gated state, a maintainer_decision with
  `actor.kind = "assistant"`, a link with an unknown `rel`, and a terminal record moved back to a
  working state.
- An assistant-proposed `active/proposed` block does not make its scoped item non-actionable.

### Step 5: the validator (`opf doctor`)

Implement the integrity layer of section 11 (Enforcement posture). Its roster: schema validity of
what exists; ID uniqueness across active, archive and staging; counter monotonicity (every ID
within its counter); bidirectional index reconciliation; transition legality and no resurrection;
the all-or-none resolution bundle; byte drift for every deterministic view, `VERSION` included;
worklog span tiling and frozen coverage digests; changelog range coverage; changelog freeze; archive
integrity; the tracked-store requirement against the resolved store; pointer and sync-target
agreement; unmanaged-path containment; and path containment.

- At `posture = "required"`, an unreadable, unparseable or unresolvable declared input is a
  failure, never an empty or clean result (section 11).
- The tracked-store check fails hard on an untracked or ignored `.working/` tree; there is no
  gitignore fallback (section 5.1, Always a git repository).
- Transition legality compares the store with the prior committed snapshot (section 8.8, item 7).
- The imported-series checks named in section 8.3 (C-IMPORTED-SCHEMA, C-IMPORTED-IDS,
  C-IMPORTED-PROVENANCE, C-IMPORTED-SEGREGATION) and the related C-CONTAINMENT and C-LINKS also run
  at 1.3.0 (section 4.2). In a fresh store the imported series is empty, so they evaluate empty
  inputs. Treat a missing imported leaf as a finding: section 4.2 requires init to create every one
  (this is a reading of that rule, not a check the specification names).
- Adoption coverage (how much of the project has moved into the store) is a report only, never a
  gate, at every posture (section 11).
- Use the outcome vocabulary of the reference CI recipe for this command's exit status: 0 valid,
  1 finding, 2 cannot-evaluate (`opf-ci.sh` header). Provide a mode, as the recipe's
  `doctor --require-store` does, in which a repository with no store exits 2, so a removed store
  cannot pass as not applicable (section 14.1, completion check 4).

Acceptance checks, each as an automated test over a throwaway store:

- A duplicated ID, an ID above its counter, a hand-edited view, a released worklog entry edited
  after its release, an untracked `.working/`, and a store missing an index each produce a finding
  or cannot-evaluate, and the fresh valid store produces valid.
- A truncated TOML file produces cannot-evaluate (exit 2), never valid.
- The no-store mode exits 2 on a repository with no store.

### Step 6: views and deliverables (`opf render`)

Implement section 10 (Views and deliverables).

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
  calls the check `render --check` and reads exit 1 as drift (`opf-ci.sh` header). Write only bytes
  that have passed the store's gates (section 5.8).
- Declare at least `WORKLOG.md` (deterministic), `VERSION` (deterministic) and `DECISIONS.toml`
  (projection). Section 9.2 notes that no composed view is required, but its 1.1.0 upgrade delta
  adds the `CONTRIBUTIONS.md` and `DECISIONS.toml` view rows, so declaring `CONTRIBUTIONS.md`,
  `DECISIONS.md` and `BACKLOG.md` as well is the safer reading.

Acceptance checks:

- Rendering twice gives identical bytes, under two different locales and time zones.
- Editing one byte of a rendered view makes the check report drift (exit 1), and re-rendering
  clears it.
- A proposed (`/proposed`) record appears in views as awaiting ratification (section 8.4).

### Step 7: fresh initialization (`opf init`)

Initialization creates the store sources and pointer for a fresh start.

- **Preconditions.** The project is a git repository (section 5.1). There is no `.working/`
  directory (OPF-QUICKSTART.md, "Getting started", says a fresh `opf init` refuses an existing
  one). Every pre-existing file at a declared view or deliverable destination outside `.working/`,
  including `VERSION` and `CHANGELOG.md`, needs a disposition first, and init refuses
  undispositioned foreign content (section 14, Adoption, post-adoption import and pre-existing
  files; section 14.2, Pre-existing files at the store location).
- **If a destination is occupied, or `.working/` exists,** stop and report it to the maintainer.
  The specification's route is adoption under section 14 with one approved plan and a disposition
  per file (`keep`, `move` or `retire`; `migrate` feeds an import, which this fresh start excludes).
  Do not move, overwrite, absorb or delete those files yourself.
- **If the project's history shows an earlier OPF store,** stop: that is re-adoption, which seeds
  counters from a pinned ancestral snapshot and never from zero (section 8.2).
- **What init writes.** The pointer, `manifest.toml` (step 3 values, `posture = "required"`,
  `import_status = "none"`), `counters.toml` at zero, `version.toml` and `worklog.toml` with no
  rows, every clean index and imported leaf, and, if you choose, a `CHANGELOG.md` holding only the
  section 10.4 opening note (section 11, "Defaults", requires the posture and import status
  values). Then render the declared views.
- **Provenance.** `.working/toml/init.toml` is optional and never required (section 9.2). Write it
  only if you implement the coupled contract below; never fabricate it.
- **Coupled initialization (optional).** OPF-INIT-D2B.md defines the coupled contract
  `opf.init.coupled/v1`: an exact success sentence, exit 0 only with fresh observations supporting
  each assertion, the plan, outcome and bootstrap formats, the Keep schema with its canonical JSON
  rules and fixed limits, and init-created sources staged while views stay unstaged, with no commit
  created. Its review register, OPF-INIT-D2B-REVIEW.md, records most of those invariants as
  "specified - not runtime-verified" at the pinned commit. If you implement it, implement all of
  it and claim it by its format name; otherwise implement a source-only initialization, write no
  `init.toml`, and do not use the coupled success sentence. The QUICKSTART notes that a fresh store
  can also be created by hand from sections 4 and 9.

Acceptance checks:

- In an empty repository, init succeeds, the validator reports valid, and the render check reports
  no drift.
- With a pre-existing `.working/`, `VERSION` or `CHANGELOG.md`, init refuses and writes nothing.
- Running init a second time refuses rather than overwriting.
- After you commit, `git check-ignore` reports nothing ignored under `.working/` and `git ls-files`
  lists every store file.

### Step 8: the record writer (`opf record`)

Implement section 8.8 (Authoring operations). Subcommands:

- `create`: a new record of an enabled baseline type in its initial state (not `done` receipts or
  worklog entries).
- `transition`: a checked status change; writes `proposed_from` on an assistant or automation
  landing at `/proposed`, restores it exactly on rejection, removes it on ratification; writes or
  removes the pending_decision resolution bundle and the contribution delivery bundle exactly as
  section 8.8 states; applies the chain rule over the active index and every archived record.
- `done-with-receipt`: maintainer-only; moves a backlog item to unqualified `done` and creates its
  `done` receipt in the same act.
- `worklog-append`: appends one `recorded` entry to the unreleased tail; refuses an entry inside a
  released span and a `detail` that opens with the lifecycle grammar.
- The `import --batch` mode exists in the specification, but this fresh start does not implement
  or use it. The clean-series subcommands refuse `actor.kind = "importer"`.

`create`, `transition` and `done-with-receipt` each append their own worklog entry in the same
transaction, whose `detail` opens with `opf-record create <ID> <status>` or
`opf-record transition <ID> <from> -> <to>`, and a rejection's entry records its reason.

Every subcommand follows the eight-item operation sequence of section 8.8: reconcile any
interrupted transaction first, under the lease; the byte-reproduction precondition; one atomic ID
claim with the counter advance inside the same transaction; a postcondition that the model diff
equals exactly the allowed delta, derived from the request and never from the planned rows; clean
destinations, ignored files included, with the lease held across publication, render and the final
validation; one crash-durable journaled publication; render, then a full validation that must
report valid (with the one pending cannot-evaluate for the record and statuses just written); then
release the lease and report. Leave the change uncommitted: never stage or commit it.

Exit status: 0 when the change is recorded and the store validates (or carries only that pending
cannot-evaluate), 2 on every refusal or cannot-evaluate (section 8.8). The specification names no
other status for this verb, so treat a post-write validation failure as 2.

Acceptance checks:

- An assistant `transition` of a backlog item to `done` lands `done/proposed` with
  `proposed_from = "active"`, creates no receipt, and appends one worklog entry.
- Killing the process between journal write and publication leaves the store, after
  reconciliation, exactly at its prestate or exactly at its poststate, and the next operation
  refuses until that reconciliation is inspected.
- A file with a comment is refused and left untouched; a held lease refuses; no ID is ever reported
  for a rolled-back transaction.

### Step 9: releases and the changelog

Implement sections 6 (The release triad) and 7 (Changelog gates: range coverage and freeze).

- **Release cut.** Append a `[[release]]` row with a clock-read `date`, the contiguous,
  non-overlapping `worklog_span` of the entries it covers (`[]` for none), and their
  `coverage_digest`. From then on those entries are frozen; a correction is a new entry in the
  unreleased tail linking `corrects` (section 6.2). Re-render `VERSION`. The specification names no
  verb for this (open point 9); whatever performs it runs under the lease and the step 8 guarantees.
- **Drafting.** The machine drafts a summary from the level directly below (release summaries
  from their worklog span; range summaries from the release summaries they replace), using the
  worklog, the ledger and the linked `done` receipts (sections 6.3 and 6.4). The specification
  names `opf absorb` as the absorber; it fixes no interface for it.
- **Curation and publication.** No unreviewed machine summary ships (section 7.3): you draft, the
  maintainer curates and publishes. Publishing records the freeze digest of the entry's exact
  bytes, from its heading line up to the next entry heading or end of file, with LF endings, on the
  `[[summary]]` row with `status = "published"` (section 7.2).
- **Headings.** Each entry starts `## <covers>`, optionally with parenthesized dates, where
  `<covers>` is a version, a range `a..b`, or `unreleased`; entries run in descending order with
  the optional unreleased section first (section 6.3; Appendix D).
- **Gates.** Range coverage: every non-superseded released summary row parses, refers to ledger
  versions, tiles the ledger with no gap or overlap, and matches exactly one heading (section 7.1).
  Freeze: every published summary's digest and its releases' coverage digests recompute equal
  (section 7.2). Rollup supersedes summaries, never worklog entries (sections 6.4 and 6.5).

Acceptance checks:

- Editing a published entry's prose without updating its digest fails the freeze gate.
- Editing a released worklog entry fails the coverage digest check.
- Two summary rows covering the same release, or a release with no summary, fail range coverage.

### Step 10: consistency, lease and location

Implement section 5.7 (The store consistency contract) for the in-repo default.

- In the in-repo pattern the contract reduces to the lease plus a clean-state check: no conflict
  markers, no mid-merge state and no concurrent OPF run on the store paths, or refuse.
- The lease (`lease.toml`, present only while held, carrying the holder, the operation and a
  clock-read acquisition time) is never seized from a live holder and is reconciled on resume or
  close.
- Store files are never hand-merged: after a merge conflict on a store path, take the integration
  base's version of the conflicted files and redo the authoring operation, which claims the next ID
  afresh. Never resolve an ID collision by lowering a counter or reusing an ID.
- If you later relocate the store, `opf migrate`, `opf sync` and the provider registry
  (sections 5.4, 5.5 and 5.6) apply in full, including the behind, ahead and divergent rules. They
  are outside this prompt's minimum; do not relocate without implementing them.

Acceptance checks:

- A second writer started while the lease is held refuses and changes nothing.
- A conflict marker in a store file makes every writer refuse.

### Step 11: rotation and archive

Implement section 12 (Rotation, archive, and retention) before the store needs it, or make the
validator refuse an archive it cannot read. Rotation is relocation to
`.working/toml/archive/<YYYY>/`, never deletion; each rotation writes that year's `archive.toml`
enumerating moved IDs and spans; open records, active blocks, unresolved decisions, the current
handoff and the unreleased worklog tail never rotate; counters are untouched; validation reads
active and archive together. Section 13 (Tamper evidence) explains how history, frozen digests,
index reconciliation and archive enumeration combine.

Acceptance check: after a rotation every gate gives the same answer as before it.

### Step 12: the CI gate

Write your own CI job, in your project's CI system, with the behaviour of the reference recipe
(`opf/enforcement/ci/opf-ci.sh`, read only):

- Run the validator in its no-store-fails mode, then the render drift check, over the checked-out
  revision, and stop at the first failure.
- Exit with 0 only when both pass; pass a step's own 1 or 2 through unchanged, and turn any other
  status (a launch failure, a signal) into 2, so an out-of-vocabulary status is never read as a
  verdict.
- Write nothing, and mark no step as allowed to fail; the reference workflow template notes that a
  check that cannot fail is decorative.

Acceptance checks:

- The job fails on a branch that deletes `.working/`, on a branch with a hand-edited view, and on a
  branch with a duplicated ID, and passes on a clean branch.
- A broken interpreter path makes the job exit 2, not 0 or 1.

### Step 13: wiring your own assistant session

Section 14.1 (One approval and completion) sets the enforcement floor that a clean start must meet
on each supported platform. The supported roster is Claude Code, Codex, Gemini CLI and Cursor.

- **Instructions.** Add OPF working rules to your platform's own instructions file (for example
  `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex, `GEMINI.md` for Gemini CLI, or a rule file
  under `.cursor/rules/` for Cursor; confirm the file against the platform's official
  documentation, since the specification names platforms, not files). The rules: the store is the
  source of truth and you record before you claim (section 3, "Records first"); write only through
  the record writer, never hand-edit store TOML or a generated view (sections 3 and 4.6); read every
  timestamp from the clock; author as `actor.kind = "assistant"` and land terminal and gated states
  as `/proposed` for the maintainer to ratify (section 8.4); never author a maintainer_decision,
  never run `done-with-receipt`, never weaken the posture (section 11), and never publish a
  changelog summary (section 7.3); append a worklog entry for each change.
- **Pre-commit check.** Run the validator and the render check on the staged snapshot, and
  disclose that the hook is installed per clone (section 14.1).
- **Deny hook.** Where your platform's official documentation confirms that a hook can deny a tool
  call, install one that denies direct writes to the store's record series, counters, declared
  views and evidence, and writes under `.working/archive/adoption/`, while the sanctioned writer
  and render paths still work (section 14.1, the floor and completion check 5). Verify the denial
  claim against that documentation. Where the platform has no verifiable denial, rely on the
  instructions and say so.
- **Ordering.** Do not enable a deny rule before the writer can perform every operation the rule
  forces (section 14.1).
- **Residuals to disclose.** Per-clone hook installation and bypass, canonical hand edits, shell or
  interpreter wrapping, same-user tampering, and unverified platform denial (section 14.1). Copy the
  relevant residuals of section 17 into your project's documentation as well.

Acceptance checks:

- A direct edit of an index file by your own tool call is denied (or, without verifiable denial,
  the residual is written down).
- `opf record create` and the render write still succeed with the hook active.
- The pre-commit check blocks a commit that contains a hand-edited view.

### Step 14: clean-start completion

Run the clean-start completion roster of section 14.1 as your acceptance test, adapted to a fresh
start with nothing to disposition: authority and freshness; discovery accounting (every
pre-existing operational file in the project is either outside the store's destinations or
reported to the maintainer); preservation (not applicable when nothing was retired, and say so);
operational readiness (the store resolves, CI asserts store presence, the validator reports valid
and no view drifts on the live tree); wiring (step 13 probes); and retirement readiness (not
applicable when nothing was retired). Whether an adoption receipt is still required for an empty
start is open point 14: report it, do not invent a receipt format.

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
   `layout = "inline"`, `posture = "required"`, and no module enabled.
6. Every enabled baseline type has a clean index and an imported leaf; `counters.toml` covers every
   clean and imported namespace; `version.toml` and `worklog.toml` exist with `schema = 1`.
7. Every file under the machine store is lowercase; every generated deliverable is uppercase and
   carries the section 10.3 header (the projection as a TOML comment block; `CHANGELOG.md` excepted,
   and `VERSION` as ratified under open point 8).
8. The validator runs the full section 11 roster plus the imported-series checks, exits 0, 1 or 2
   as step 5 describes, and fails closed on unreadable input.
9. Renders are byte-reproducible and the drift check catches a one-byte edit.
10. The record writer enforces the `/proposed` rule, the per-type transition table, the resolution
    and delivery bundles, the lifecycle worklog entry, the eight-item operation sequence, and exits
    0 or 2.
11. Releases freeze worklog spans; the changelog passes range coverage and freeze; no summary was
    published without the maintainer's curation.
12. The lease, the clean-state check and the integration-base merge rule are enforced.
13. Rotation, if implemented, preserves every gate's answer; if not implemented, nothing rotates.
14. The CI job runs the validator in no-store-fails mode and the render check, with the 0, 1, 2
    status rule, and no step can pass by failing.
15. Session wiring is in place on your platform (instructions, pre-commit check, deny hook or a
    disclosed lack of one), and the section 14.1 residuals are disclosed.
16. Every open point you filled is listed with your choice and its ratification status.
17. The conformance statement uses only the section 16 vocabulary, for example
    "`conformant_for_declared_scope` (OPFiles base, spec 1.3.0 on homes 1; self-asserted; profiles:
    none declared)", and names its scope, its exclusions (relocation, import, adoption of
    pre-existing files, modules, and any open point you could not settle) and every cannot-evaluate
    result.

## Out of scope for this prompt

Import and the imported-series writer (section 8.8, `import --batch`; section 14.1, post-adoption
import), adoption of pre-existing files (section 14), migrating an existing release pipeline
(section 14.3), store relocation and sync (sections 5.4 to 5.6), schema upgrades (section 9.2,
needed only when your own implementation later bumps its base schema), the homes-2 generation
(section 4.2), the modules (section 8.5), and profiles (section 9.1). If your project needs any of
these, read those sections at the same pinned commit and extend the plan, under the same rules.
