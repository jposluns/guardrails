# OPFiles: the operational-files standard

Formal name: AIQT Development Operational Standard. Public brand: OPFiles
(opfiles.ai). Base discovery token: `opf`. Status: draft (specification only;
schemas and the reference tooling, the scaffolder `opf init`, the adopter `opf adopt`,
the post-adoption importer `opf import`, the validator `opf doctor`, the renderer `opf render`,
the relocator `opf migrate`, the
synchronizer `opf sync`, the schema-upgrader `opf upgrade`, the absorber `opf absorb`, and the
record author `opf record`,
ship in later releases).
Date: 2026-09-27 (UTC).

OPFiles is a neutral, self-contained operational-files standard published under the Apache
License 2.0 (except vendored third-party material, which remains under its own terms). AIQT and AIQT Guardrails are trademarks (registration pending); AIQT is a brand,
not a legal entity, and the standard is authored and maintained by its lead maintainer. A project
conforms to OPFiles with this specification and
its own checks; the AIQT Guardrails pack is the reference enforcement suite and a consumer
of the standard, not its definition. AIQT-specific requirements are layered as one optional
profile, `[profiles.aiqt]` (section 9), and a base adopter need not adopt AIQT.

Unless a path is written from `/`, a path under `.working/` in this document is relative to the
root of the repository that tracks the store (the "store repository"), and every other path (the
pointer, `CHANGELOG.md`, `VERSION`) is relative to the root of the product repository. The two
roots coincide under the default configuration (section 4.1).

## 1. Purpose and scope

OPF (operational files) standardizes how a project keeps its operational records: the backlog, the
completion receipts, the worklog, findings, decisions, blocks, handoffs, and references that a
records-first operational discipline requires (the discipline AIQT's records-first rule is one
implementation of). It defines one machine-readable store of versioned TOML under
`.working/toml/`, a set of generated human-readable views above it, and three release artifacts (a
version ledger, a durable worklog, and a curated public changelog) with the gates that keep all of
them honest.

The store is always a git repository, wherever it lives. Its location is a free, migratable
configuration resolved through a committed pointer, never an architectural commitment: it defaults
to `.working/` in the product repository and can be relocated at any time to any location a target
can name, with history and the durable worklog preserved (section 5).

OPF specifies formats, layout, naming, lifecycle, and enforcement posture, and names the standard
command vocabulary of the reference tooling (`opf init`, `opf adopt`, `opf import`, `opf
doctor`, `opf render`,
`opf migrate`, `opf sync`, `opf upgrade`, `opf absorb`, `opf record`). It does not specify tooling
internals; a reference implementation follows in later releases of the AIQT Guardrails reference
suite. A project can conform to this
specification with hand-maintained files and its own checks.

## 2. Conformance language

MUST, MUST NOT, SHOULD, and MAY are used as in common standards practice: MUST is an absolute
requirement of conformance, SHOULD is a strong recommendation departed from only for recorded
reason, MAY is genuinely optional. Statements without these keywords are descriptive.

## 3. Design principles

- **Records first.** The store is the source of truth. A decision, finding, or completion that is
  not recorded did not happen. Human-readable surfaces are derived, never authoritative.
- **Facts are durable; views are re-rollable.** Detailed records are never consumed, rewritten,
  or deleted once frozen. Summaries and views over them may be regenerated, re-rolled, and
  re-worded at any time, because the facts beneath them persist intact.
- **Machine writes, human reads.** Sources are machine-shaped TOML; deliverables are generated,
  prominent, human-readable documents. Nobody hand-edits a generated file.
- **Location is configuration.** The store's location is a migratable setting, not a mode or an
  architectural commitment. Wherever the store lives, the generated public deliverables
  (`CHANGELOG.md`, `VERSION`, and any future public view) are byte-for-byte identical; only the
  committed pointer (`.opf.toml`) changes with the topology. Moving the store is a first-class,
  history-preserving operation.
- **Fail closed.** A gate that cannot read, parse, or resolve an input it is meant to cover reports
  failure or cannot-evaluate, never a clean pass.
- **Determinism where claimed.** Anything called a deterministic render is byte-reproducible from
  its sources. Anything curated by a human is labelled as curated and is gated on its facts, not
  its bytes.
- **Generic by construction.** The base-required schema vocabulary (the standard's own required
  field names and structure) names no particular adopter, operator, or profile; a conforming store's
  own data may name an operator via `actor.id` or a profile via a `[profiles.<name>]` table the
  adopter chose. The standard's own ownership and its reference profile are named as such, never as a
  requirement dependency (section 15).

## 4. Store resolution: roots, pointer, discovery, and naming

### 4.1 Two roots

Two roots organize every path in this standard:

- **The product repository root.** The repository the project ships from. It carries the committed
  store pointer (section 4.3) and the public deliverables (`CHANGELOG.md` and `VERSION`,
  section 5.8). In a monorepo, each project subdirectory that carries its own pointer is its own
  product root, and tooling operates on the product root it is aimed at.
- **The store repository root.** The git repository that tracks `.working/`. Under the default
  configuration this is the product repository itself, so the two roots coincide; after a
  relocation (section 5.4) the store repository is wherever the pointer's target resolves, and
  `.working/` sits at its root.

### 4.2 Layout overview

Base spec 1.3.0 defines adoption and the separate imported series on homes 1. The 1.3.0
requirements in sections 4.2, 8, 9.2, 11, and 14 are a target contract; they do not claim that
the reference tooling has activated adoption, imported-series validation, or the import writer.
Activation MUST include the tested upgrade in section 9.2 and deterministic doctor coverage
before a writer accepts the new format. Homes 2 is a separate, later activation.

The homes-2 contract below is for `spec_version = "2.0.0"` and `[opf].homes = 2`.
The current reference tooling reserves these names and implements their homes-2 boundary checks. It still
supports `1.2.0` and initializes legacy homes (generation 1, with no `homes` key); writers retain their
legacy paths until homes 2 is activated, which requires the homes migration (`opf upgrade`) to be
available. The section 9 manifest example
describes the 1.3.0 target on legacy homes. The homes-2 requirements in sections 4.2, 9.2, 12,
14.1, 14.2, and 15 describe the target contract, not an activated runtime guarantee.

```
<product repository root>/
  .opf.toml                        # committed store pointer (section 4.3)
  CHANGELOG.md                     # curated public changelog (deliverable; section 6.3)
  VERSION                          # deterministic render from version.toml (deliverable)
  .working/                        # the store; present here under the default in-repo configuration

<store repository root>/           # the product repository itself, by default
  .working/
    README.md                      # ownership and regeneration note (deliverable)
    WORKLOG.md                     # deterministic render of worklog.toml
    VERSION.md                     # optional human view of version.toml
    TODO.md  BACKLOG.md  PIPELINE.md
    DONE.md  FINDINGS.md  DECISIONS.md
    BLOCKS.md  HANDOFF.md  REFERENCES.md  CONTRIBUTIONS.md
    DECISIONS.toml                 # machine projection (deterministic; section 10.5)
    <TYPE>-INDEX.md ...            # optional 1:1 index mirrors (section 10.1)
    IMPORT-REPORT.md               # legacy import report (pre-1.3.0 runs only; section 14.1)
    toml/
      manifest.toml                # store manifest and discovery marker (section 9)
      counters.toml                # per-namespace ID high-water marks (section 8.2)
      version.toml                 # version and release ledger (section 6.1)
      worklog.toml                 # durable operational record (section 6.2)
      <type>.imported.index.toml    # separate imported records of each enabled type (section 8.3)
      worklog.imported.toml         # imported worklog, on its own ID number line
      lease.toml                   # single-writer lease, present only while held (section 5.7)
      init.toml                    # bootstrap provenance of a coupled init (section 9.2), if present
      backlog_item.index.toml      # typed record files (section 8)
      done.index.toml
      finding.index.toml
      pending_decision.index.toml
      autonomous_decision.index.toml
      block.index.toml
      handoff.index.toml
      reference.index.toml
      contribution.index.toml
      maintainer_decision.index.toml
      preference_pattern.index.toml
      archive/
        2026/
          archive.toml             # enumerates rotated IDs and spans (section 12)
          done.index.toml
          worklog.toml
    archive/
      moved/<source-path>          # default Move destinations (section 14.2)
      adoption/<run-id>/           # adoption retire preimages (section 14.2)
    imported/<kind>/<run-id>/      # durable originals, inventories, approvals and receipts (section 14)
    staging/<kind>/<run-id>/       # short-lived staging, evidence-gated reclamation (section 14.1)
    journals/<kind>/               # reserved recovery state, never a view or ordinary op target
      journal/                    # crash-durable frames
      runs/<run-id>/transaction.toml # gate-readable projections
      allocations/<run-id>.toml    # irrevocable ID reservations (section 8.2)
```

When the store has been relocated, the `.working/` tree lives at the store repository root exactly
as drawn, and the product repository keeps only the pointer and the public deliverables. The
per-record layout (section 9) additionally places one file per record under
`.working/toml/<type>/`, with each `<type>.index.toml` acting as the registry.

The imported files are registered managed leaves beside the clean-series files, using the same
enabled-type roster; `worklog` uses `worklog.imported.toml` instead of an imported index.
Their manifest, emitter, upgrade and containment registrations MUST agree. A 1.3.0 `opf init`
and the section 9.2 upgrade create the imported leaves for every enabled type, create-only and
empty; enabling a further type or module later creates its imported leaf in the same act as its
clean index. They are machine
records, distinct from the original-source evidence under `.working/imported/`. The first
imported-series release keeps these files inline in either store layout and provides no views
over imported data; assistants read the TOML. Historical releases remain in `version.toml`
(section 14.3); there is no `version.imported.toml` or `CHANGELOG.toml`.

The reserved children `archive/`, `imported/`, `staging/`, and `journals/` are store-tree control
area, neither machine-store records nor adopter content; they relocate with the machine store.
In homes 2, OPF writes no state outside `.working/` except the operation-lock and coupled-init
substrate in the git common directory, and none under `.aiqt/`. The record archive remains
inside the discovered machine store; the example name `toml` is not hardcoded.

The closed kind vocabulary is `import`, `ingest`, `adoption`, `layout`, `preview`. Import and
ingest run IDs use `imp-<YYYYMMDD>T<HHMMSS>Z-<hash16>`; adoption uses `adopt-` with that
suffix. Layout and preview reserve `layout-` and `preview-` with the same suffix. Here
`hash16` is 16 lowercase hexadecimal characters; constructors check lexical shape, not calendar
validity or filesystem safety. File operands use canonical contained relative paths: no empty,
dot, or parent components, absolute/drive/backslash forms, control characters, or line separators.

Homes-2 init and upgrade render this managed block into `.working/.gitignore` from the topology
constants; the reference tooling provides the renderer and drift gate but installs no block:

```gitignore
# >>> opf-managed >>>
/journals/
/staging/
# <<< opf-managed <<<
```

Durable `imported/` and `archive/` evidence stays tracked. Installation must inspect effective
ignore rules and the index first; tracked staging or journals require an explicit reviewed
untracking change, never silent index mutation. The block travels with the store and joins the
indexed-ignore candidate set. Gitignore is not access control: `git add -f` can stage ignored
state. Journals are machine-local even after completion; a clone without them cannot recover those
transactions, and requested recovery fails closed on a missing journal. Containment and doctor
exclude journals and make no recovery claim; a rogue file there is outside their coverage.

Under pre-1.3.0 tooling, every store, whatever it declares, keeps its legacy grading: the
homes-2 names are ordinary store paths there, graded, detected, and dispositioned exactly as
before, and the pre-1.3.0 doctor's check roster and residuals are unchanged. Activated 1.3.0
tooling registers on homes 1 the imported managed leaves, the adoption archive
`.working/archive/adoption/<run-id>/`, the Move destination `.working/archive/moved/`, and the
evidence bundles `.working/imported/<kind>/<run-id>/`, all recognized by containment as OPF
control area, and adds the four section 8.3 imported-series checks to the doctor roster.
C-EVIDENCE-ENUM neither runs nor reads anything until homes 2 is activated; on homes 1 the
section 14.1 completion checks carry the evidence digest verification themselves. The boundary
below applies only to a store that declares both `homes = 2` and `spec_version = "2.0.0"` once
the tooling activates that generation.

Each evidence bundle `.working/imported/<kind>/<run-id>/` carries its own inventories at its
root: `inventory.toml`, plus a new `inventory-<phase>.toml` for each later phase, where `<phase>`
is a lowercase letter followed by up to 31 lowercase letters or digits. Each holds exactly
`format = "opf.evidence.inventory/v1"` and a `file` array whose rows have exactly `path` (a
canonical store-relative file path spelled from `.working/`), `size` (a nonnegative integer), and
`sha256` (64 lowercase hex digits). A row may name a member of its own bundle other than a
bundle-root inventory, a default Move destination under `.working/archive/moved/`, or, for an
adoption bundle, a retire preimage of the same run under `.working/archive/adoption/<run-id>/`.
The owning writer or migration derives each inventory from the run's transaction record or
receipt and publishes it exclusively with the retained bytes. An inventory is never rewritten, so
a bundle stays immutable and an evidence commit changes only its bundle folder. An inventory is
not a journal projection and remains available in a clone without journals.
A phase inventory may be published in the same transaction as the base inventory to claim the
promotion receipt without creating a receipt/inventory digest cycle.

C-EVIDENCE-ENUM reconciles exact membership, directory structure, regular-file types, sizes and
digests: every payload file under `.working/imported/` and `.working/archive/`, meaning every file
other than a bundle-root inventory, is claimed by exactly one row, and every inventory is itself
schema-checked against the shape above rather than claimed. Unlisted or unclaimed entries, a bundle
without an inventory, and missing listed files are findings; unreadable or malformed inputs,
including a path claimed twice, cannot evaluate. The recognized legacy format
`opf.ingest.evidence-inventory/v1` is refused as the named `legacy-ingest-inventory` finding under
C-EVIDENCE-ENUM, without migration or rewriting; completed-ingest replay cannot evaluate that bundle.
A phase inventory never substitutes for a missing
`inventory.toml`: such a bundle cannot evaluate. Deleting a whole bundle, inventory and payload
together, is outside this local snapshot check; independent history is required to detect that
loss. Inventories assert membership, not authenticated actor history. The contained reader's size
ceiling still applies.

The legacy `imports/` exclusion remains registered until its writers migrate. In homes 2,
`staging/` is walked and stray-graded, including empty runs; an unknown kind is always a finding.
The existing staged-plan presence test also recognizes import and ingest runs in their typed
staging homes. For legacy stores, other kinds cannot substantiate partial import status until
their plan readers are registered. At 1.3.0, partial status is receipt-bound under section 11,
not inferred from
staging. Doctor never consults a journal to decide partial status and makes no claim that
a staged plan has a recoverable transaction. In homes 2, ordinary transaction operands, including
those of an open transaction being recovered, cannot equal, descend from, or contain `journals/`; a
legacy store's transactions and recovery keep their legacy operand handling, and no shipped writer
targets that home. Capability-bound journal APIs derive their destinations from kind and run
identity. Legacy journal transport must preserve bytes through the migration's receipt binding.
These comparisons are byte-exact: on a case-insensitive or normalizing filesystem, a differently
cased or composed spelling can alias a reserved home and is not caught. Discovery precedes the
manifest, so it cannot know the generation: it examines `manifest.toml` in every immediate
subdirectory, including `journals/`, and fails closed on a reserved-name match or ambiguity; it
reads nothing deeper there.


### 4.3 The pointer

The store is resolved through a committed pointer plus manifest discovery, with no hardcoded path.

`.opf.toml` at the product repository root is the committed store pointer: lowercase machine
source (section 4.6), tracked in the product repository. It carries a `[store]` table whose
`target` names where the store repository lives, in the typed target syntax of section 5.5:

```toml
[store]
target = "dir:."      # the default: the store rides this repository, at .working/
```

A relocated store points wherever it now lives:

```toml
[store]
target = "git@git.example.com:acme/product-ops.git"
```

Resolution rules:

- An uncommitted, machine-local override file `.opf.local.toml` (same shape, never committed, and
  ignored by version control) MAY override or complete the pointer on one system, for targets that
  only make sense there: a local-only store's absolute path, or a private store that only the
  maintainer's systems can resolve (section 5.8). The committed pointer MUST be safe to publish;
  anything machine-local or private-only belongs in the override.
- Resolution order is the local override first, then the committed pointer. Where neither file
  exists, the default location (`.working/` at the product root) is tried; a valid manifest found
  there resolves as the store. Where no pointer exists and no default store is found, there is
  nothing to operate on and `opf init` is the remedy.
- A pointer that exists but does not resolve (the target unreachable, or no valid manifest at the
  target) is a cannot-evaluate outcome: the tool reports it and stops. It never falls back
  silently to the default location, which could resolve a different store than the one intended.
- The tracked-store requirement (section 5.1) is checked against the resolved store, wherever the
  pointer says it lives, never against an assumed location.

Relative `dir:` paths in the pointer are interpreted against the product repository root, which is
the named fixed root for this file; any other path in a pointer MUST be absolute.

### 4.4 The machine store

Machine-store TOML records live in `.working/toml/`. The directory name `.working` at the store
repository root is fixed by this standard. The machine subdirectory's standard name is `toml`;
tooling MUST NOT hardcode it, and MUST locate it by discovery. The names `imports`, `imported`,
`archive`, `staging`, and `journals` are reserved at the store level for OPF control area and
MUST NOT be used as a machine subdirectory name; discovery fails closed on a machine store so named.
The legacy staging name `imports` remains reserved so legacy content cannot be re-absorbed.

### 4.5 Manifest discovery

Within the resolved store repository, tooling locates the machine store by finding exactly one
immediate subdirectory of `.working/` containing a `manifest.toml` that declares
`standard = "opf"` in its `[opf]` base table, trying `toml` first. Zero matches, or more than one,
is a cannot-evaluate outcome: the tool reports it and stops; it never guesses, and never treats it
as an empty or absent store. The pointer names which repository carries the store; the manifest
discovery observes where, within it, the machine store sits. Because the machine subdirectory is
observed at each use rather than declared and trusted, renaming it is a directory move with
nothing to go stale; the pointer, which does declare a location, is validated at every resolution
and fails closed rather than trusting a stale target (section 4.3). Profiles declared under
`[profiles.<name>]` are enumerated after the base is validated; a profile a tool does not support
is ignored for enforcement and recorded as unevaluated, never treated as a base-validation failure
(section 9).

### 4.6 Casing convention and rationale

Source files are lowercase; deliverables are uppercase.

- Every file inside `.working/toml/` is lowercase: `manifest.toml`, `counters.toml`,
  `version.toml`, `worklog.toml`, `worklog.imported.toml`, `lease.toml`, `init.toml`,
  `<type>.index.toml`, `<type>.imported.index.toml`, `archive.toml`. The
  pointer `.opf.toml` and its local override are lowercase machine source on the same terms.
- Every generated deliverable at `.working/` top level, and the public deliverables at the
  product repository root (`CHANGELOG.md`, `VERSION`), is uppercase.

The rationale: uppercase filenames are the recognized cross-industry norm for read-me-first
documents (README, LICENSE, CHANGELOG), they sort to the top of directory listings, and their
prominence signals "this is the surface a human reads". Lowercase signals machine-owned source that
humans change only through tooling or review, never casually. The casing itself is part of the
contract: a lowercase file is never a deliverable, an uppercase file is never hand-authored truth.

## 5. The store is a git repository: location, migration, and consistency

### 5.1 Always a git repository

A conforming store MUST be tracked in a git repository, wherever it lives. An untracked or ignored
`.working/` tree is a hard failure in scaffolding and validation, not a warning: an untracked
store has no history, no tamper evidence, and no durability, which defeats the purpose of keeping
records at all. There is no gitignore fallback. The check runs against the resolved store
(section 4.3): whichever repository the pointer resolves to must actually track the tree. A store
placed in a plain directory conforms only once that directory is itself a git repository (the
local-only pattern, section 5.3); "somewhere git does not reach" is not a location the standard
recognizes.

Privacy is achieved by where the tracked store lives, never by leaving it untracked. Adopters
remain free to ignore anything outside `.working/`; the tracked-store requirement covers the
store, not the rest of the tree.

### 5.2 Location is a configuration

The store's location is a free configuration, not a fixed set of modes.

The default is `.working/` in the main product repository: zero configuration, created by
`opf init`, with the development process public to everyone who can read the repository. This is
deliberate: the simplest conforming posture is also the most transparent one, and a project that
wants a private process opts into it by relocating the store, not by weakening tracking.

From the default, the store can migrate anywhere (section 5.4). The patterns in section 5.3 are
just configurations of the same machinery: nothing in the store's format, gates, IDs, or views
changes when it moves, and the product repository's generated public deliverables are identical
wherever the store lives (section 5.8).

### 5.3 Location patterns

Any location a target (section 5.5) can name conforms, provided the store is tracked
(section 5.1) and operated under the consistency contract (section 5.7). Four patterns are common
enough to name:

- **In-repo (the default).** The store rides the product repository at `.working/`. Simplest;
  suits projects whose process is already open. The store's consistency is the product
  repository's own branch-and-merge discipline.
- **Private companion repository.** The store lives at the root of a separate, private repository,
  paired with the public product repository. The development process stays private, works across
  multiple systems with full history and tamper evidence, and the product repository carries only
  the pointer and the generated public deliverables. Where the development records are not
  intended for publication, this is the pattern this standard suggests.
- **Local-only, self-backed.** The store lives in a local directory that is its own git
  repository, never pushed anywhere. Maximum locality, no egress; but there is no sync target, so
  durability is wholly the adopter's own backup discipline. Choosing this pattern is choosing to
  own that backup, and the choice SHOULD be recorded as a maintainer decision; the residual is
  disclosed in section 17.
- **Monorepo, per-project.** Each project subdirectory carries its own `<project-subdir>/.working`
  store and its own pointer, making each subdirectory an independent product root (section 4.1).
  Stores never share counters, manifests, or leases across projects.

### 5.4 Migration is first-class

`opf migrate --store <target>` relocates the tracked store to any location a target can name. The
operation MUST:

- run under the consistency contract and the single-writer lease (section 5.7), refusing to start
  from a stale, ahead, or divergent store;
- validate the target before any push, per section 5.6: the destination is confirmed to be the
  recorded, intended target, never inherited from ambient state, and a store is never pushed to an
  unexpected place;
- preserve the store's git history into the destination repository through a history-preserving
  extraction, and preserve the durable worklog and its archive byte for byte: a
  relocation that flattens history into a single import commit, or that loses or rewrites any
  worklog entry, is nonconformant;
- keep the generated public deliverables in the product repository: `CHANGELOG.md` and `VERSION`
  remain at (or are restored to) the product repository root, and the render targets are rewired
  so future renders keep writing them there (section 5.8);
- re-point the committed pointer, and the manifest's recorded sync target, in the same change, so
  the pointer, the manifest, and the actual remote never disagree across a landed state;
- verify the destination before retiring the source: the relocated store is cloned back and
  reconciled (ledgers, indexes, counters, and worklog digests confirmed identical) before the old
  location is removed or archived. The old in-repo `.working/` tree leaves the product repository
  only in the migration change itself, after that verification, and the removal is recorded. An
  unverified destination never justifies destroying the source: this is the
  verified-restore-path discipline applied to the store itself;
- record the relocation as a worklog entry naming both the old and the new location.

Migration is symmetric: a store can move in-repo to companion, companion to in-repo, anywhere to
anywhere, repeatedly, and each move is one recorded, verified, history-preserving operation.

### 5.5 Target syntax

The canonical form of a target, in the pointer and as the argument to `opf migrate --store`, is a
git URL or a filesystem path. This form is universal for clone and sync: git already speaks every
transport it needs, and the host in a remote URL already distinguishes GitHub, GitLab, Gitea, and
any self-hosted forge, so the standard defines no per-host syntax for synchronization.

Explicit typing exists only where git cannot infer what to do, which is creating a new remote and
authenticating that creation:

| Form | Example | Git infers sync? | Provider needed for |
|---|---|---|---|
| `dir:<path>` or a bare path | `dir:../product-ops-store` | yes (file transport) | creating the local repository |
| `git:<url>` or a bare git URL | `git@git.example.com:acme/ops.git` | yes | nothing; the remote must already exist |
| `github:<owner/repo>` | `github:acme/product-ops` | yes, after creation | creating the remote and authenticating creation through the host API |
| `gitlab:<owner/repo>` | `gitlab:acme/product-ops` | yes, after creation | same, for GitLab |

A `dir:` target names a directory that is, or will be initialized as, its own git repository (the
local-only pattern). A typed host form resolves, after creation, to an ordinary git URL, which is
what the pointer then records; the typed form is a creation convenience, not a parallel transport.

### 5.6 The provider registry

Target handling is extensible through a provider registry with the same shape as the
`[types.<name>]` registry (section 9): one `[providers.<name>]` table per provider in the
manifest. Each provider is a handler covering some or all of three roles: `create` (make a new
store location), `auth` (authenticate creation with the host), and `sync` (clone, fetch, push).

- `local-directory` and `generic-git-remote` ship as the universal fallbacks and cover every
  target between them: any local path and any git URL on any host.
- `github` and `gitlab` plug in only for their create and auth API conveniences; their sync is
  ordinary git. Further hosts join by registering a provider, never by extending the target
  grammar.
- Authentication uses the adopter's existing git credentials (SSH keys, tokens through git's own
  credential machinery). OPF stores no credentials of its own, ever.
- An unregistered provider is unavailable until it is reviewed and registered, on the same posture
  as any other tooling dependency.

Egress discipline binds every provider: a provider's create and auth calls go only to the host the
target itself names, and before any push the tooling confirms the actual push destination against
the recorded target (the pointer and the manifest's recorded sync target). A destination that
appears from anywhere else, ambient configuration, a stale remote, or retrieved content, is
surfaced and refused, never pushed to. A store is never pushed to an unexpected place.

### 5.7 The store consistency contract

Nothing stale, nothing ahead. Before any OPF operation other than the reconciliation step itself
(`init`, `adopt`, `import`, `doctor`, `render`,
`migrate`, `upgrade`, `absorb`, `record`; `opf sync` is that surfaced reconciliation step), the
store repository is reconciled to a
known-consistent, up-to-date state against its sync target: the target is fetched and the local
store compared against it.

- **Equal:** the operation proceeds.
- **Behind the target:** the tooling refuses to operate and surfaces the state. The remedy is a
  fast-forward pull to current (`opf sync`), which the tooling MAY offer and perform as its own
  surfaced step, then re-run the operation; the pull is never folded silently into another
  operation.
- **Ahead of the target** (local commits not yet pushed, the lease still held by the resuming
  holder, and no divergent remote side): the tooling refuses to operate until the store is
  reconciled, and reconciliation is an authorized push of the pending local commits. The holder
  confirms and pushes them as its own surfaced step, never folded silently into another operation;
  the push is safe precisely because there is no divergent side that a push could lose. This is the
  recovery path for a store left ahead by a crash between a local write and its sync-back.
- **Divergent from the target** (unsynced commits on two systems): the tooling refuses to operate
  and surfaces the state. A divergence HALTS for the human, always: it is never auto-merged and
  never silently resolved by picking a side, because a textual merge of the store's TOML records
  can silently mangle the very records the standard exists to protect.
- **After any operation that writes,** the store is synced back to its target in the same session,
  so the store is not left intentionally ahead on one system; a crash between the local write and
  the sync is detected on the next resume as an ahead-only or a divergent state and reconciled by
  the matching path above (an authorized ahead-only push by the lease holder, or a human-resolved
  halt on divergence), never left standing.

A **single-writer lease** prevents concurrent divergent writes: before mutating the store, a run
takes the lease (`lease.toml`, present only while held, carrying the holder, the operation, and an
acquired-at timestamp read from the clock) and makes it observable at the sync target before its
writes begin, so a second system's reconciliation sees the held lease and refuses. A lease is
never seized from a live holder; it is reconciled against recorded state on resume or close, and a
leftover lease from a dead run is released only through that reconciliation. Where the
concurrent-operation module is enabled, the lease is additionally recorded as a `session_lease`
record. There is a residual window between taking the lease and its reaching the target in which
two systems can both begin; the divergence check above is the overlapping control that catches
that collision after the fact, and the two layers together are the guarantee (disclosed in
section 17).

This contract states two generic operational requirements inline. First, a **single-writer
lease**: a run holds a lease so two runs never act on the same store state at once, reconciles it
on resume or close, and never seizes it from a live holder. Second, **reconcile the record against
reality**: the store is authoritative only while it matches what is actually in use, so divergence
is detected by observation at defined checkpoints and treated as a finding to resolve, never a
discrepancy to leave standing, and a store can never certify itself current merely because nothing
updated it. AIQT's concurrency-lease and reconcile-record-against-reality rules are the reference
implementation of these two requirements; the requirements themselves are the standard's and bind
any conforming adopter.

Scope of the contract by pattern:

- **A store with a dedicated sync target of its own** (a companion repository, or any relocated
  store with a remote) is bound by the full contract above: OPF tooling is the writer, and it
  pulls current, refuses on divergence, and syncs back after.
- **The default in-repo store** has no dedicated sync target; it rides the product repository,
  whose own version-control discipline (branch and merge on green) is the consistency mechanism
  across systems. There the contract reduces to the lease plus a clean-state check: the store
  paths in the working tree carry no conflict markers, no mid-merge state, and no concurrent OPF
  run, or the tooling refuses.
- **A local-only store** has no sync target, so the behind/ahead axis does not exist; the lease
  still guards concurrent runs on the one system, and durability is the adopter's recorded backup
  responsibility (section 5.3).

**Parallel branches allocate against the integration base.** The lease serializes writers on one
store, but two branches of a store that rides the product repository each start from the same
committed `counters.toml`, so each can claim the same record ID. Store files are therefore never
hand-merged: after a merge conflict on a store path, take the integration base's version of the
conflicted store files and redo the authoring operation on that base, which claims the next ID
afresh (section 8.8). The byte-reproduction precondition of `opf record` and `opf upgrade` refuses
only a store file whose bytes are not the canonical serialization of its content, such as one
carrying comments or non-canonical formatting. A hand edit or hand merge that leaves canonical bytes
passes it undetected, so this integration-base rule is a separate requirement that the precondition
does not enforce. A merge that resolves without a conflict yet duplicates an ID is caught by
`opf doctor`, which checks store-wide ID uniqueness and that every ID lies within its counter
(section 8.2). A collision is never resolved by decrementing a counter or reusing an ID.

### 5.8 The public deliverables are identical across topologies

Generated public deliverables always live in the product repository: the root `CHANGELOG.md`, the
root `VERSION`, and any future public view. This holds identically whether the store is in-repo, a
private companion, local-only, or anywhere else; a reader of the product repository sees the same
generated public deliverables, byte for byte, whatever the topology. The committed pointer
(`.opf.toml`) is the one part of the surface that does change with the topology: it names where the
store lives (for example `dir:.` for an in-repo store, or a companion target), so it necessarily
differs between topologies. Only the maintainer's systems need resolve a private store; for everyone
else the pointer is an inert file and the public deliverables are the whole story.

Publication is stage-then-promote: `opf render` writes into the product repository only bytes that
have passed the store's gates, and those bytes land through the product repository's normal
review flow like any other change. Rendering from a private store into the public product
repository is the promotion step, and it never promotes anything the gates have not passed.

## 6. The release triad: version.toml, worklog.toml, and CHANGELOG.md

Three artifacts, deliberately separated so the version anchor, the detailed record, and the public
story cannot tangle: a machine ledger of releases, a durable worklog of changes, and a curated
summary for the public. The ledger anchors versioning; the worklog holds every fact; the changelog
tells the story and can always be retold because the facts persist beneath it.

### 6.1 version.toml, the version and release ledger

`.working/toml/version.toml` is the machine ledger of version numbers, release dates, and release
boundaries. It is the single source for the project's version: the root `VERSION` file is
deterministically generated from it (the latest release's version, as exact bytes) and drift-gated,
and an optional human view renders to `.working/VERSION.md`. The reference suite's release-delta
tooling (the check that computes the minimum required version bump for a change) anchors here; it
is a consumer of the ledger, not part of the base standard's definition. The ledger is not the
changelog: it carries numbers, dates, spans, and digests, never release prose.

Each `[[release]]` row records:

- `version`: the SemVer version string, unique in the ledger.
- `date`: the release date, RFC 3339 UTC, read from the clock at the release event.
- `worklog_span`: the inclusive, contiguous span of worklog entry IDs the release covers, as a
  two-element array `["WL-1", "WL-88"]`, or an empty array for a release with no worklog entries.
- `coverage_digest`: a digest over the canonical serialization of the covered worklog entries, in
  ID order, computed at release cut. The exact canonicalization is fixed by the schema release that
  follows this specification; it MUST be deterministic and cover the entries' full content.
- `imported`: optional boolean, permitted only as `true` and only on a historical release recorded
  under section 14.3. On an imported-flagged row, `date` carries the source-recorded release date
  rather than a clock read at a witnessed release cut, `worklog_span` is empty, and the row rests
  on imported provenance. A witnessed release cut never sets the flag, and the flag is never
  added to or removed from an existing row.

Release rows are append-only and immutable once written. Spans MUST be contiguous and
non-overlapping across consecutive releases, in ID order, so the released worklog tiles exactly and
the unreleased tail is everything after the last span.

The ledger also carries the summary rows that back the public changelog. Each `[[summary]]` row
records:

- `covers`: a single released version (`"1.3.0"`), an inclusive range over contiguous released
  versions (`"1.0.0..1.2.3"`), or `"unreleased"` for the optional working section.
- `status`: `working`, `published`, or `superseded`.
- `digest`: required once `status` is `published` or `superseded`; the freeze digest of the
  corresponding `CHANGELOG.md` entry (section 7.2).
- `superseded_by`: present exactly when `status` is `superseded`; the `covers` token of the rollup
  summary that replaced this one.

Summary rows hold digests and ranges only, never prose. Prose lives in exactly one place: the root
`CHANGELOG.md`.

### 6.2 worklog.toml, the durable operational record

`.working/toml/worklog.toml` is the detailed operational record: one entry per change, appended as
the work happens. It generates the deterministic view `.working/WORKLOG.md`.

The worklog is durable and mutable-until-release, never deleted. An unreleased entry MAY be
corrected in place through ordinary review; once a release freezes its span the entry is immutable;
and no entry is ever consumed, rolled away, or deleted. Every fact ever recorded stays in the
worklog (or its archive, section 12), and survives store relocation byte for byte (section 5.4).
This durability is what makes the changelog safely re-rollable: a summary can be re-worded or
re-rolled at any depth because the detail it summarizes is never lost.

Each `[[entry]]` row is a worklog record (type `worklog`, namespace `WL`, section 8) carrying its
ID, timestamp, actor, a change kind (`added`, `changed`, `fixed`, `removed`, `security`, `docs`, or
`infra`; a manifest MAY register additional kinds), a one-line summary, optional detail, and links
to the records and revisions it concerns.

Entry mutability follows the release cut. Before its span is frozen by a release, an entry MAY be
corrected through ordinary review (never deleted). At release cut, the release's `coverage_digest`
freezes the covered entries; from then on any change to them is a gate failure. An entry MUST NOT
be appended into an already-released span: spans are contiguous frozen ID intervals, so a
post-release correction is a new entry in the current unreleased tail, linking the entry it
corrects. Late attribution to a published release is thereby impossible by construction rather than
forbidden by policy.

### 6.3 CHANGELOG.md, the curated public summary

The public changelog lives at the product repository root as `CHANGELOG.md`; wherever the store
lives, the changelog's home is the product repository (section 5.8). It is machine-drafted (from the
worklog entries in range, the version ledger's `[[release]]` rows, and the `done` completion receipts
those worklog entries link) and human-curated: a summary of the worklog over declared version ranges, not a
deterministic render, and not byte-drift-gated. There is exactly one public changelog; it is
conceptually single-sourced from the store (the worklog and the version ledger, with `done` receipts
enriching the draft only), and no separate changelog source file exists.

Each entry begins with a heading of the form `## <covers>`, optionally followed by parenthesized
dates, where `<covers>` is the machine-parseable token matching a `[[summary]]` row: a version, a
range `a..b`, or `unreleased`. Entries are ordered descending by the highest version covered, with
the optional unreleased section first. The prose beneath the heading is the human-curated summary
of the worklog entries in that range.

### 6.4 The rollup model

The changelog is tiered: worklog entries roll up into per-release summaries, and per-release
summaries roll up into range summaries. Each level summarizes only the level directly below it, so
every rollup is a small, reviewable act: a release summary is drafted from its span's worklog
entries; a range summary is drafted from the per-release summaries it replaces, never from the raw
worklog wholesale.

When a range summary lands, the per-release summaries inside its range are superseded: their
`[[summary]]` rows flip to `superseded` (keeping their digests as publication history) and their
entries leave `CHANGELOG.md`, replaced by the range entry. Nothing beneath changes: the worklog
keeps every entry and the ledger keeps every release row and every superseded summary digest.

### 6.5 What consume-on-rollup does and does not touch

Consumption applies only at the changelog's granularity: a range summary supersedes the finer
summaries within it in the changelog view, exactly as a weekly summary supersedes dailies in a
digest. It never applies to the worklog. The worklog is the durable record; the changelog is a
re-rollable view over it. Deleting or thinning worklog entries during rollup is nonconformant.

## 7. Changelog gates: range coverage and freeze

The changelog is gated on its facts, not its bytes: coverage proves the story spans every release,
and freeze proves a published story changed only through a recorded re-publication.

### 7.1 Range coverage

A deterministic gate confirms, from `version.toml` and `CHANGELOG.md` alone:

- every non-superseded, non-unreleased `[[summary]]` row's `covers` token parses and refers only to
  versions present in the release ledger, with a range denoting a contiguous run in ledger order;
- those rows tile the ledger exactly: every released version falls in exactly one row's coverage,
  with no gap and no overlap;
- every such row has exactly one matching release-or-range entry heading in `CHANGELOG.md`, and
  every release-or-range entry heading matches exactly one such row; the optional `## unreleased`
  heading, when present, matches instead the `covers = "unreleased"` row (the unreleased worklog
  tail), so it is neither an unmatched heading nor a second match against a released row.

The check confirms the start and end versions of each declared range against the ledger; it is
deterministic, and it fails closed on an unreadable or unparseable input.

### 7.2 Freeze and re-publish

Freeze the detail, not the summary. The worklog is the durable record of facts, frozen once its
spans are released; the changelog is free to be re-worded, because editing a summary's prose can
never change the facts, which persist in the worklog.

- A `working` summary (unpublished, including the unreleased section) is edited with no ceremony.
- Publishing a summary computes its freeze digest (the exact bytes of its `CHANGELOG.md` entry,
  from its heading line up to but not including the next entry heading or end of file, with LF line
  endings) and records it on the `[[summary]]` row with `status = "published"`.
- The freeze gate recomputes, for every published summary, both the freeze digest against the
  current `CHANGELOG.md` entry and the underlying `coverage_digest`s of the releases it covers
  against the current worklog. A mismatch on either is a failure.

Editing an already-published entry is therefore never silent: the changed bytes break the recorded
digest, and in a released pack they change the per-file manifest and root digest, so the edit is
structurally a re-publication. The re-publish flow updates the row's digest in the same reviewed
change, and a light gate confirms the edited entry still declares the same `covers` and still rests
on the same underlying worklog entries (the coverage digests unchanged): the facts held, only the
prose moved. A change to the facts themselves is not a re-wording and must land as new worklog
entries under section 6.2.

### 7.3 The curation flow

No unreviewed machine summary ever ships. The flow is fixed: the machine drafts a summary from the
level below; a human curates it; publication freezes its digest; only then does it ship. Drafting
is assistance, not authority; the published words are the curator's.

## 8. Record model

### 8.1 Type taxonomy and placement

| Tier | Type | Namespace |
|---|---|---|
| Baseline | backlog_item | BI |
| Baseline | done | DN |
| Baseline | worklog | WL |
| Baseline | finding | FN |
| Baseline | pending_decision | PD |
| Baseline | autonomous_decision | AD |
| Baseline | block | BL |
| Baseline | handoff | HO |
| Baseline | reference | RF |
| Baseline | contribution | CN |
| Baseline | maintainer_decision | MD |
| Baseline | preference_pattern | PP |
| Governance module | maintainer_action | MA |
| Delivery-assurance module | artifact | AR |
| Delivery-assurance module | gate_run | GR |
| Delivery-assurance module | release | RL |
| Delivery-assurance module | waiver | WV |
| Operational-policy module | mode | MO |
| Operational-policy module | tier_assessment | TA |
| Concurrent-operation module | session_lease | SL |
| Migration quarantine (importer-only) | legacy_fragment | LF |
| Reserved, excluded | transaction | TX |
| Reserved, unassigned | (none) | CL |

Notes on the roster:

- The word "changelog" is not a record type. It names the curated public deliverable (section 6.3).
  The detailed per-change type is `worklog`. The namespace `CL` is reserved unassigned so it can
  never half-collide with the freed word.
- `transaction` (`TX`) is excluded from the adopter standard with its name and namespace reserved;
  it may enter later as a versioned module only if portable semantics are demonstrated.
- Modules ship default-off; each is enabled by one manifest edit. `legacy_fragment` (LF) is
  deprecated for new stores as of 1.3.0, not removed: its taxonomy row and legacy validation remain
  for existing stores and evidence. New imports use the imported series and verbatim `unparsed`
  text (section 8.3), not LF quarantine. LF is never scaffolded.
- Imported history uses the same enabled types in a separate series, not additional record types.
  Reserved namespaces remain reserved. Imported states describe history and confer no current
  authority (section 8.6).
- `done` is a durable completion receipt linked one-to-one to a backlog item reaching ratified
  `done`; a standalone receipt is legal only for imported history with provenance.
- A `finding` records the observation and links its remediation rather than containing it.
- A `block` scopes one or more enumerated records and feeds actionability (section 8.5).
- `contribution` records an artifact, fix, or proposal this project proposes or sends to a peer project,
  with a delivery bundle once sent: the outward counterpart to `reference`, which records what comes in. It carries no
  fleet-specific semantics; those ride a registered `x-<vendor>` extension (section 8.7).
- `maintainer_decision` and `preference_pattern` are baseline as of spec_version 1.1.0; they were
  module-tier in 1.0.0. With that change the governance module carries `maintainer_action` alone, and
  the now-empty decision-support module is retired. A store upgrades across the change with the
  additive migration (section 9.2).
- The delivery-assurance `release` record, where enabled, references a `version.toml` release row
  by version string; the ledger row is the fact, the record is the delivery-assurance envelope
  around it.
- `version.toml` and `counters.toml` are control ledgers, not record types.

### 8.2 ID namespaces and counters

Clean record IDs have the form `<NS>-<n>`; imported IDs have the form `imported:<NS>-<n>`,
for example `imported:BI-7`. The complete lexical grammar is
`^(?:imported:)?[A-Z]{2}-[1-9][0-9]*$`, with the namespace additionally required to name the
record's enabled type in section 8.1. Namespaces map one-to-one to types within each series.
`counters.toml` holds independent monotonic high-water values per series and namespace:
`BI` for clean backlog items and the quoted TOML key `"imported:BI"` for imported backlog items.
The same rule includes `"imported:WL"`; clean release spans tile only the clean `WL` number line.
Uniqueness, counter high-water, contiguity and no-deletion checks evaluate each series independently;
allocation increments its counter under the store's lock as one atomic claim, so no gap
between choosing and reserving can double-allocate. Counters are never reset and IDs are never
reused, even when a record is superseded, refuted, or its work reverted. Rotation, index rewrites,
and store relocation never touch `counters.toml`. Re-adoption seeds both series from a pinned
ancestral snapshot and refuses a missing required namespace; it never zero-seeds prior ancestry.
Only a genuinely first adoption starts its counters at zero.

### 8.3 The record envelope

Clean records carry the envelope below; the imported envelope follows it. Types add their own
fields on top. Schemas are closed: an unknown key is a validation failure unless it sits under a
registered vendor extension table.

| Field | Requirement | Meaning |
|---|---|---|
| `id` | required | `<NS>-<n>`, matching the type's namespace |
| `type` | required | the type name; must match the file the record lives in |
| `status` | required | per the status grammar (section 8.4) |
| `title` | required | one line, human-oriented |
| `created_at` | required | RFC 3339 UTC, read from the clock at creation |
| `updated_at` | required | RFC 3339 UTC, read from the clock at the last transition |
| `actor.kind` | required | `maintainer`, `assistant`, `automation`, or `importer` |
| `actor.id` | optional | identity detail within the adopter's own vocabulary |
| `summary` | optional | short prose body |
| `links` | optional | array of `{rel, id}`; `rel` from the closed link vocabulary (section 8.6) |
| `refs` | optional | array of captured references (section 8.6) |
| `x-<vendor>` | optional | registered vendor extension tables only (section 8.7) |

An importer MAY omit `created_at` where the source genuinely does not record it; the omission is
recorded as unknown via the import provenance reference, never guessed. This legacy permission
does not replace the following 1.3.0 imported-series contract.

The worklog entry uses a reduced envelope (`id`, `date`, `actor`, `kind`, `summary`, optional
detail, `links`, `refs`); its status is fixed (section 8.5). The worklog records facts, not
proposable decisions, so its entries never take the `/proposed` qualifier whatever the actor: an
assistant-authored or automation-authored worklog entry is a conformant recorded fact needing no
ratification (section 8.4).

The imported envelope is closed and deterministic. It requires `id`, `type`, a one-line `title`,
`status` from the type's legal state set without `/proposed`, `actor.kind = "importer"`,
`actor.id` naming the importing assistant, and an `import` provenance table. Imported worklog
rows use this envelope with `type = "worklog"` and `status = "recorded"`, plus their worklog
fields. Standalone imported `done` receipts are legal history. Historical `created_at`,
`updated_at`, `date` and `decided_at` are optional; when present they MUST be valid RFC 3339 UTC
and no later than the writer's import clock instant. Import time MUST NOT stand in for event time.

Other historical type fields may be absent only with an explicit missingness row. Supplied
fields retain their declared value types and vocabularies; unknown keys still fail. Missing
historical timestamps and type fields are accounted for in `unrecorded = [{field, reason}]`,
with one row per absent field, no duplicate fields and no row claiming a supplied field absent.
`field` names a field in that type's schema. The closed reasons are `not_recorded_in_source`,
`unparsed`, `ambiguous`, `conflicting`, and `not_applicable`. The first means "never recorded
historically in the supplied source", not a claim about all history. The required imported
envelope and provenance fields cannot be waived through missingness. Strict current resolution
bundles and transition obligations do not apply to historical omissions.

The `import` table requires `source` (the canonical store-relative path of the preserved
original, spelled from `.working/`), `source_sha256` (64 lowercase hexadecimal digits),
`run` (the `imp-<YYYYMMDD>T<HHMMSS>Z-<hash16>` run ID), and `imported_at` (RFC 3339 UTC read
from the writer's clock). Optional `span` is an informational byte range in the original.
Optional `import.history` retains verbatim source-precision values that cannot be losslessly
normalized, such as a date-only string; no UTC midnight is fabricated. Optional `import.unparsed`
holds verbatim source text that cannot be mapped. The assistant MUST retain such text rather than
drop it. The writer performs no byte-tiling or leftover accounting: byte-level coverage and
semantic fidelity are not machine-proven. Preserved originals remain the restoration authority.

Imported records and their worklog entries are immutable after publication; corrections are a
fresh import run retaining the old evidence. A conforming imported series can reach doctor VALID:
it MUST NOT enter the legacy importer or module-schema deferral seam. C-IMPORTED-SCHEMA checks
this envelope and missingness; C-IMPORTED-IDS checks the series grammar, counters and no-deletion;
C-IMPORTED-PROVENANCE re-reads each preserved original and verifies `source_sha256`.
C-CONTAINMENT recognizes the imported managed leaves, C-LINKS resolves the union of both series,
and C-IMPORTED-SEGREGATION enforces section 8.6. Missing or unreadable evidence fails closed.

### 8.4 The status grammar

```
status    ::= state ( "/" qualifier )?
state     ::= lowercase name from the type's declared state set
qualifier ::= "proposed"
```

- Each type declares a closed state set: one initial state, zero or more working states, and one or
  more terminal states.
- A terminal transition performed by an actor whose `kind` is `assistant` or `automation` lands
  with the `/proposed` qualifier (for example `done/proposed`); creating a record directly in a
  terminal factual or ACT state that awaits no ratification, such as an `autonomous_decision` or a
  `reference`, is not such a transition and carries no qualifier. Only a maintainer transition
  removes the qualifier (ratification) or returns the record to a working state (rejection, with a
  recorded reason). A `/proposed` status is not terminal: gates and completion claims treat the
  record as unfinished, and views surface it as awaiting ratification. The `/proposed` qualifier
  attaches to any transition an assistant or automation makes that awaits a maintainer's
  ratification: the assistant or automation terminal transitions above (for example `done/proposed`
  or `fixed/proposed`), and the gated non-terminal states, which are proposals rather than grants even
  though they are not terminal. A type declares its gated states: `block` gates `active` (a proposed
  block, `active/proposed`), `contribution` gates `sent` (an assistant-sent contribution,
  `sent/proposed`, awaiting a maintainer's ratification that it was genuinely sent), and
  `preference_pattern` gates `active` (an assistant-distilled pattern, `active/proposed`, awaiting
  ratification). This is one mechanism, not a set of per-type special cases: entering a gated state as
  an assistant or automation takes `/proposed`, and only a maintainer ratifies it to the unqualified
  state. A recorded factual entry that proposes nothing and awaits no ratification is exempt: the
  worklog, whose entries record facts rather than propose a transition, never takes `/proposed`, so an
  assistant-authored or automation-authored worklog entry (status `recorded`) is a conformant recorded
  fact rather than an unratified proposal.
- Standing authorization for `contribution` `sent`: `sent` gating is on by default, but an adopter MAY
  declare a standing authorization for a named recipient that deactivates per-send gating for that
  recipient, letting an assistant or automation land the unqualified `sent` grant to it without a
  per-send ratification. A valid declaration for the contribution's declared recipient relieves the
  gating; an absent or malformed declaration fails closed, so gating stays on. The declaration is an
  adopter configuration surface; a store that declares none keeps every `sent` gated.
- No resurrection: a record in an unqualified terminal state never re-enters a working state. A
  revived concern is a new record linking the old one.
- Supersession is a link, not a state edit: the superseding record links `supersedes`, and where
  the type records it, the superseded record's terminal state reflects it. Where the superseded
  record is immutable, including every imported record, its recorded state stands and the link
  alone records the supersession.
- The worklog is a special case of these rules, with terminality keyed to release rather than to a
  state transition (section 6.2). It declares the single state `recorded`: while an entry sits in
  the unreleased tail its `recorded` status is pre-terminal and mutable-until-release, and once a
  release freezes its span the entry's `recorded` status is terminal and immutable. The
  released-frozen span is therefore the worklog's terminal state that satisfies the
  one-or-more-terminal-states requirement above, while correcting an unreleased entry in place is an
  ordinary pre-terminal edit, not a resurrection of a terminal record.

### 8.5 Transition rules

Baseline types:

| Type | States | Rules |
|---|---|---|
| backlog_item | `open` > `active` > `done` or `dropped`; `open` > `dropped` | Ratified `done` creates the one-to-one `done` receipt. Blocked-ness is never a stored state; it is derived from active blocks at view time. |
| done | `recorded` | Created terminal, immutable. Links `receipt_of` to its backlog item. |
| worklog | `recorded` | Durable operational record with release-keyed terminality: the unreleased tail is pre-terminal and mutable, a released-frozen span is terminal and immutable (sections 6.2 and 8.4). Takes no `/proposed` qualifier whatever the actor. Mutability governed by section 6.2, not by transition. |
| finding | `open` > `fixed`, `routed`, `refuted`, or `accepted` | Severity is graded at or after the fix decision, never before. |
| pending_decision | `open` > `decided` or `withdrawn` | All-or-none resolution bundle: an open decision carries none of `decision`, `decided_at`, `decided_by`; a decided one carries all. A decided record may be superseded by a new decision linking `supersedes`; exactly one current effective resolution exists per chain. |
| autonomous_decision | `recorded` | Immutable ACT record: the classification basis, the action, links. Overturning is a new record (or maintainer decision) linking it. |
| block | `active` > `released` or `expired` | Scopes an enumerated list of record IDs. A block created by an assistant or automation actor is `active/proposed` and is a proposal, not a grant: it does not count toward blocked-ness or justify a stop until a maintainer ratifies it. |
| handoff | `current` > `superseded` | Posting a new handoff supersedes the previous in the same act; at most one `current` handoff exists among clean records authored by non-importer actors (section 8.6). |
| reference | `recorded` | Immutable captured reference. |
| contribution | `proposed` > `sent` > `acknowledged` or `superseded`; `proposed` > `withdrawn` | Records what this project proposes or sends to a peer, with a delivery bundle `{channel, ref, sent_at, receipt_ref?, receipted_at?}`: `channel`/`ref`/`sent_at` are required once sent, `sent_at` is forbidden before, and the receipt fields are legal only at `acknowledged`. `sent` is gated (an assistant lands `sent/proposed`; a maintainer, or a valid standing authorization for the recipient, lands the bare grant). `acknowledged` is the single positive terminal (responded, adopted, reshaped, or declined); the outcome lives in `summary`/`x-<vendor>`, never as a state. A re-send is a new record linking `supersedes`; the superseded record records `superseded`. |
| maintainer_decision | `recorded` | Created-terminal, immutable maintainer ruling carrying its `decision` (answer plus rationale). `actor.kind` is `maintainer` or `importer` only (a maintainer ruling with assistant attribution is a contradiction; `importer` covers migrated history). Overturning is a new record linking the old. It MAY `exemplifies` the preference_pattern it instantiates. |
| preference_pattern | `active` > `retired` | A distilled preference pattern carrying `context` and `rationale` (with the envelope `title`). `active` is gated: an assistant-distilled pattern lands `active/proposed` awaiting maintainer ratification to the unqualified `active`. |

Module types, in outline (full schemas ship with the module schemas release): maintainer_action
`open` > `done` or `dropped`; artifact `staged` > `promoted` or
`rejected`; gate_run `recorded` with a three-valued verdict field (`pass`, `fail`,
`cannot_evaluate`), never folded into status; release `planned` > `published` or `abandoned`;
waiver `active` > `expired` or `revoked`, expiry required at creation; mode `active` > `retired`;
tier_assessment `recorded`; session_lease `held` > `released` or `reconciled`;
legacy_fragment `quarantined` > `resolved` or `ignored`.

Actionability: a clean backlog item is actionable when its state is `open` or `active` and no
clean, unqualified `active` block authored by a non-importer actor scopes it. Imported and
importer-authored blocks never enter this join.
This is the block join every scheduling view renders.

### 8.6 Links and reference capture

`links` relate records to records; `rel` comes from a closed vocabulary: `supersedes`, `resolves`,
`remediates`, `receipt_of`, `corrects`, `follows`, `relates`, `exemplifies`, `derives_from`.
Extending the vocabulary is a specification version change.

- `exemplifies`: the source record instantiates the linked pattern. Its target is constrained to a
  `preference_pattern` (PP); the source side is unconstrained. Its primary user is
  `maintainer_decision` (a ruling exemplifies the pattern it instantiates).
- `derives_from`: the source record was derived from the linked record; directional, usable by any
  type. Its primary user is `contribution` (a contribution derives from the internal finding,
  decision, or backlog item that motivated it).

`refs` capture external sources at the moment a claim or artifact is produced: each is
`{kind, locator, note}` with `kind` one of `path` (a repository path, with a line where
applicable), `url`, or `doc` (a document and section). A record whose claims rest on an external
source without a captured reference is unsourced, whatever confidence backs it.

Imported history has an authority firewall, enforced by the writer and doctor and keyed on
provenance rather than on series alone: it covers every record whose `actor.kind` is `importer`,
in the imported series or written into the clean series by the pre-1.3.0 legacy importer. An
importer-authored record MUST NOT satisfy a current approval, receipt, actionability or
supersession obligation of any record authored by a non-importer actor. A clean `done` record's
`receipt_of` MUST target a clean backlog item, and a clean backlog item reaching ratified `done`
requires a clean receipt authored by a non-importer actor; a legacy importer-authored receipt
satisfies, as recorded history, only the legacy importer-authored backlog item it was recorded
with. An importer-authored decision is never the current effective resolution of a clean
`pending_decision` chain; resolving such a chain now requires a new clean decision linking back.
Importer-authored blocks do not grant a current stop, and an importer-authored record cannot
discharge a required supersession. The firewall covers every state-bearing type: an
importer-authored record is never the current `handoff`, an active `waiver`, a held
`session_lease`, an active `mode`, or a ratified active `preference_pattern`; a state-bearing
status on an importer-authored record describes history at its source and confers nothing now,
and every current-state join, including the section 8.5 at-most-one-current handoff rule,
evaluates only clean records authored by non-importer actors. Acting on history requires a new
strict clean record at the present time, with a link back to the historical record.
C-IMPORTED-SEGREGATION enforces this firewall over importer-authored records in both series.

Clean-to-imported links allow only `relates`, `derives_from`, `follows`, and same-type `supersedes`;
the last records historical continuation without discharging a current supersession obligation,
and the immutable imported target keeps its recorded state, the link alone recording the
supersession (section 8.4). Imported-to-imported links allow every declared relation subject to
its type constraints. Imported-to-clean links are refused by both writer and doctor. Links
resolve over the union of both series; a dangling cross-series link is a finding, never silently
omitted. The imported series cannot supply current authority through a link, an extension, or an
adoption approval.

### 8.7 Extensions

Experimental or adopter-specific fields ride only under `x-<vendor>` tables, with each vendor token
registered in the manifest; an unregistered prefix is a validation failure. An extension may add
metadata but MUST NOT override identity, state, transitions, resolution completeness, publication
inclusion, block actionability, counters, lock ordering, or actor attribution.

### 8.8 Authoring operations

`opf record` is the reference tooling's record-authoring verb. Its clean-series subcommands use
the strict record model; the new `import` write mode uses the separate 1.3.0 imported model.
Import is not a flag that relaxes `create`, and the clean-series subcommands refuse
`actor.kind = "importer"`: an importer authors only through `import`. Its subcommands:

- `create`: one new record of an enabled baseline type, in the type's initial state. An assistant
  or automation author entering a gated initial state lands `/proposed`; a created-terminal factual
  or ACT type (`reference`, `autonomous_decision`, `maintainer_decision`) carries no qualifier
  (section 8.4). `done` receipts and worklog entries are not created this way.
- `transition`: a status change checked against the type's grammar (section 8.5). An assistant or
  automation author landing a terminal or gated state takes `/proposed`; only a maintainer
  ratifies, or rejects with a recorded reason back to the recorded pre-proposal state
  (section 8.4).
- `done-with-receipt`: maintainer-only. It moves a backlog item to unqualified `done`, from
  `active` or by ratifying `done/proposed`, and in the same act creates its one-to-one `done`
  receipt linked `receipt_of` (section 8.5). An assistant reaching `done` uses `transition` and
  lands `done/proposed`; no receipt exists until a maintainer ratifies.
- `worklog-append`: one entry appended to the unreleased tail of `worklog.toml`, status `recorded`,
  never `/proposed` whatever the actor (sections 6.2 and 8.4). An entry that would fall inside a
  released span is refused.

- `import --batch FILE [--root DIR]`: the primary import surface (the import write mode; there
  is no separate `--import` flag). A single-record import is a one-row batch. The canonical TOML batch,
  `opf.record.import-batch/v1`, declares one source path, record rows, worklog rows, historical
  fields, missingness, verbatim unparsed text and batch-local link keys. The writer resolves local
  keys to claimed imported IDs and recomputes source size and SHA-256 from preserved bytes,
  never trusting caller-supplied measurements. One invocation covers one source in one journaled
  transaction, followed by one render and one full doctor.

Every subcommand runs one operation sequence, and an implementation of the verb MUST preserve its
guarantees:

1. Resolve the store, then reconcile any interrupted authoring transaction first. Reconciliation
   writes the store, so it runs only under the single-writer lease that publication uses: a held
   lease refuses before any recovery write and is never seized. An operand changed since the
   interruption, to bytes that are neither its journaled prestate nor its planned poststate nor a
   write of either torn by the interruption, is reported and refused, never overwritten. A
   reconciled interruption refuses the new operation, so the operator inspects it before anything
   new is written.
2. Precondition: re-emitting the unchanged parsed model of every file the operation rewrites
   reproduces its on-disk bytes exactly (the section 9.2 rule). A file carrying comments or
   non-canonical serialization refuses and is left untouched. The check proves serialization only:
   a hand edit or hand merge that leaves canonical bytes is not detectable by it, and the
   integration-base merge policy of section 5.7 remains a separate requirement.
3. Claim each new ID as one atomic act (section 8.2). At homes 1 the `counters.toml` advance is an
   operand of the same journaled transaction, under the held lease, and IDs are reported only once
   that transaction has completed, so a rollback never withdraws an ID anyone has seen. At homes 2
   the claim is an irrevocable reservation in the journal home, made before the reversible
   publication (section 4.2).
4. Postcondition: the model diff of every rewritten file equals exactly the operation's allowed
   delta (the new rows appended, the counters advanced by exactly the claim, and for a transition
   one status and `updated_at` change), value for value, before anything is written. The expected
   delta is derived from the request, the claimed IDs, the clock value, and the schema rules, never
   from the planned rows themselves.
5. The in-repo store contract (section 5.7): the planned destinations are clean, including ignored
   files, and the single-writer lease is held across publication, render, and the final doctor.
6. Every rewritten file is published in one crash-durable journaled transaction, so an
   interruption leaves the store exactly at its prestate or exactly at its poststate once
   reconciled. The reference tooling keeps that journal under `.aiqt/record/journal` at homes 1.
7. The declared views are rendered, then a full doctor must report VALID; a failure leaves the
   change for review with recovery advice scoped to the planned paths.
8. The lease is released, and only then are the claimed IDs and touched files reported. The
   change is left uncommitted in the working tree: the verb never stages or commits it.

The verb exits 0 when the change is recorded and the store is doctor-VALID, and 2 on every refusal
or cannot-evaluate.

Import preserves that operation sequence, including the one allocation seam, independent model
delta check, cleanliness gate, lease and reconcile-first recovery. Its atomic operands are the
imported counter rows first, the touched `<type>.imported.index.toml` and
`worklog.imported.toml` files, and the evidence bundle: the exact original at
`.working/imported/import/<run-id>/originals/<source-path>` and `inventory.toml` in the retained
`opf.evidence.inventory/v1` format (section 4.2). The journal remains
`.aiqt/record/journal` at homes 1. It never rewrites clean records or clean counters.

Import refuses with exit 2 on a non-importer actor, absent or invalid provenance, a clean-series
record operand, an imported-to-clean link, a historical timestamp later than the run clock, an
unknown missingness reason, or a type not enabled and supported by the writer. It also refuses
without an adoption receipt, outside that plan's approved migrate-source scope, or when any
section 14.1 bound item, the live source bytes against the plan digest, the tool release
identity, or the prompt-pack version and digest included, no longer matches the approved plan.
Bound-item drift, in this session or a later one after a tool upgrade, requires a fresh plan
with its own single approval; import never reinterprets the old approval.

Replay identity is `(source_sha256, batch content digest)`. A completed identical replay
re-reads and verifies the published records and evidence, then succeeds as a no-op reporting
the existing IDs. A partial overlap, including a differing batch against the same source within
the run, refuses, names the overlap and directs to journal recovery. Each source admits at most
one completed batch: a differing batch against a source whose batch already completed refuses
and names the completed run. A corrected import is a fresh run whose batch declares the
completed run it corrects; its records link `supersedes` or `corrects` to the imported records
they replace, the previous run's immutable evidence is preserved, and source completion
(section 14.1) evaluates the correcting run. Retry never allocates duplicate IDs.

## 9. The manifest

`.working/toml/manifest.toml` is the store's control document and discovery marker. Illustrative
shape (the schema release that follows this specification is normative):

```toml
# .working/toml/manifest.toml
# OPFiles (AIQT Development Operational Standard) store manifest and discovery marker.

[opf]
standard = "opf"               # discovery token; exact value required
spec_version = "1.3.0"         # OPFiles base spec version this store conforms to
layout = "inline"              # storage layout: "inline" or "per-record" (was layout_profile)
posture = "required"           # "off", "warn", or "required" (section 11)
import_status = "none"         # "none", "partial", or "complete"

[store]
sync_target = ""               # the store's dedicated sync target (section 5.7); empty under the
                               # in-repo default, where the store rides the product repository

[modules]                      # base-level optional capability modules (generic, not AIQT-specific)
governance = true              # the [profiles.aiqt] profile below requires these three enabled
delivery_assurance = false
operational_policy = true      # (required by [profiles.aiqt].required_modules)
concurrent_operation = true    # (required by [profiles.aiqt].required_modules)

# --- Profiles: additive requirement bundles, namespaced, ignored by base-only tooling ---

[profiles.aiqt]
version = "1.0.0"                    # AIQT profile version, independent of spec_version above
base_compat = ">=1.0.0 <2.0.0"       # base spec_versions this profile applies to
posture_floor = "required"           # effective posture = strictest(base.posture, this)
required_modules = ["governance", "operational_policy", "concurrent_operation"]
verification_floor = "triple-family" # AIQT reference-suite policy; base tools ignore this
extension_namespace = "x-aiqt"       # record-level namespace this profile owns (section 8.7)

# A second adopter could later add, ignored by everyone who does not support it:
# [profiles.acme]
# version = "0.1.0"
# base_compat = ">=1.0.0 <2.0.0"

[types.backlog_item]
namespace = "BI"

# ... one [types.<name>] table per enabled type; namespaces per section 8.1

[providers.local-directory]
handler = "builtin"
roles = ["create", "sync"]

[providers.generic-git-remote]
handler = "builtin"
roles = ["sync"]

# ... optional host providers, for example:
# [providers.github]
# handler = "plugin"
# roles = ["create", "auth"]

[unmanaged]
paths = []                     # pre-existing files kept in place, enumerated (section 14.2)

[views."BACKLOG.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/BACKLOG.md"

[views."WORKLOG.md"]
kind = "deterministic"
sources = ["worklog"]
target = ".working/WORKLOG.md"

[views."DECISIONS.toml"]       # a machine projection (section 10.5): deterministic, byte-drift-gated
kind = "projection"
sources = ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]
target = ".working/DECISIONS.toml"

[views."VERSION"]
kind = "deterministic"
sources = ["version"]
target = "VERSION"

[deliverables."CHANGELOG.md"]
kind = "curated"
target = "CHANGELOG.md"

[archive]
period = "year"

[vendors]
registered = ["x-aiqt"]        # record-level extension namespaces; the aiqt profile owns x-aiqt,
                               # registered here so base-only record validation accepts x-aiqt fields
```

View and deliverable targets under `.working/` are relative to the store repository root; the
public targets (`VERSION`, `CHANGELOG.md`) are relative to the product repository root
(section 5.8).

Storage layout:

The `layout` field selects the storage layout only:

- **`inline`** (default): records live inline in `<type>.index.toml`; the index and the store
  coincide. One global store lock serializes writers. This is the ordinary single-writer case: a
  consumer reads one small file with one parse.
- **`per-record`** (with the concurrent-operation module): `<type>.index.toml` becomes a registry
  of `{id, state, path, digest}` rows, records live one per file under `<type>/`, and any
  multi-record operation takes `(namespace, id)` locks in ascending order. Concurrent writers pay
  the file-count cost only when they have the problem it solves.

Readers always enter at `<type>.index.toml` in either layout. The ledgers are exempt from the
per-record layout: `version.toml` and `worklog.toml` are always single files, written under the
store lock (allocation of WL IDs still goes through `counters.toml` atomically).

### 9.1 Base standard and profiles

The manifest declares one **base** standard and zero or more **profiles**.

The base is the `[opf]` table: `standard = "opf"` (the discovery token, exact value
required), `spec_version` (the base SemVer this store conforms to), and the base storage and posture
fields. Base-only tooling reads the base, validates it, and operates on it alone.

A profile is a `[profiles.<name>]` sub-table carrying its own `version` (independent SemVer) and a
`base_compat` range, plus profile-namespaced requirement fields. **A profile may only add
requirements; it may never weaken, remove, or override a base requirement.** A profile table holds
only profile-namespaced keys and cannot write a base field, so a weakening override is impossible by
construction; where a profile field combines with a base setting it combines toward the stricter
value, and a profile value that would loosen a base setting is a profile validation failure,
surfaced, never applied.

Base and profile versions evolve independently. A tool that does not recognize a declared profile,
or does not support its major version, **ignores it for enforcement** and records it as
present-but-unevaluated; it never fails base validation over an unknown profile. A tool that does
support a profile enforces the profile's additional requirements as extra gates and **fails closed**
on an unreadable, unparseable, or base-incompatible instance of that profile. Fail-safe governs a
profile outside a tool's declared coverage; fail-closed governs a profile inside it (section 17).

A profile's record-level additions ride registered `x-<vendor>` extension tables (section 8.7),
registered in `[vendors]` so base record validation accepts them as opaque; a profile's store-wide
requirements live in `[profiles.<name>]`, invisible to base validation. A profile-aware tool checks
that each supported profile's `extension_namespace` is registered in `[vendors]`; an unregistered
profile namespace is a profile-setup failure surfaced to the adopter, because base-only record
validation would otherwise reject that profile's records under the section 8.7 unregistered-prefix
rule (fail-closed, never a weakening). The shipped profile is
`[profiles.aiqt]`, dogfooded by the reference suite; it declares a posture floor, required modules,
the reference verification floor, and its `x-aiqt` extension namespace. The `[profiles.<name>]`
namespace and these contract rules are reserved and documented; the profile-authoring interface is
not yet a committed public contract for third-party authors.

### 9.2 Store schema upgrades

The homes-generation upgrade targets `spec_version = "2.0.0"` with required integer `[opf].homes = 2`.
Absent or `1` denotes legacy homes for migration; unknown future generations are refused.
The homes-generation target does not itself change the runtime supported version or init format.
The migration refuses a store resolved outside the product root until a multi-root coordinator exists.
Unproven legacy `.archive/` entries remain in place with a standing finding until dispositioned.

A base-schema version bump ships a tested, in-place store-schema upgrade (`opf upgrade`). The upgrade
is idempotent. A purely schema-level bump is additive, using atomic replacement of existing files,
create-only writes for new index files, and regeneration of declared views through exclusively
created temporary files followed by atomic rename. These writes are sequential, with recovery scope
held in memory, not a durable transaction journal. A homes-generation bump additionally relocates
OPF control areas as a versioned, journaled, fail-closed relocation.
Every destination is digest-verified before its source is removed. Both kinds of upgrade run under
the store consistency contract and the single-writer lease (section 5.7).
It fails closed on an unresolvable store, a declared `spec_version` ABOVE the tooling,
a divergence, a held lease, or any populated state that contradicts its preconditions; it never
lowers the fail-closed floor. Before any write it enforces two fail-closed preconditions: it claims
the single-writer lease (section 5.7) and holds it across the whole mutation, and it verifies the
working tree is clean, including ignored files, over the planned schema and render destinations
(store and product roots) and the index collision candidates, so the committed HEAD is a verified
restore path for that scope; a held lease or a dirty store refuses, and a dirty store is asked
to commit its own changes, never restored by the tool. After applying the schema delta, it regenerates
the declared views and requires a full doctor VALID before offering the uncommitted change for the
adopter's own branch-and-merge. It never stages or commits the change.

The manifest and counters rewrite is a model regeneration through the canonical new-document emitter,
never a textual round-trip edit, bounded by two guards: a precondition that re-emitting the UNCHANGED
parsed model reproduces the on-disk bytes exactly (proving the file is canonical and comment-free, so
nothing can be lost), failing closed otherwise; and a postcondition that the model diff equals exactly
the allowed delta, failing closed otherwise. The allowed delta is expressed as ensure-present and
ensure-absent over the whole 1.0.0 origin family, so a governance-enabled, a decision_support-enabled,
a bare, and a view-omitting 1.0.0 store all migrate under one rule and the normative text cannot diverge
from the tooling. For the 1.0.0 to 1.1.0 upgrade the allowed delta is: rename the base table
`[devprocess]` to `[opf]` and its `standard` discovery token from `devprocess` to `opf` (the OPFiles
rebrand), carrying every other base field over unchanged; bump `spec_version` to 1.1.0; remove the
retired `decision_support` module key where present; add each of the `[types]` rows for `contribution`,
`maintainer_decision`, and `preference_pattern` not already declared by an enabled 1.0.0 module (a
governance-enabled store already declares `maintainer_decision` and a decision_support-enabled store
`preference_pattern`; the row moves from module tier to baseline unchanged); add the two new view rows
(`CONTRIBUTIONS.md` and the `DECISIONS.toml` projection); widen the existing `DECISIONS.md` composed
view's `sources` from the two 1.0.0 decision sources (`pending_decision`, `autonomous_decision`) to the
four required at 1.1.0 by adding `maintainer_decision` and `preference_pattern` where that view is
declared (a 1.0.0 store that declares no `DECISIONS.md` gains none and stays valid, since no composed
view is required); extend `counters.toml` with the `CN`/`MD`/`PP` zeros while preserving every existing
high-water; and create each missing empty `*.index.toml` file for the three baseline types, skipping any
that already exist (such as a `maintainer_decision.index.toml` where governance was enabled, whose
records are preserved byte-for-byte). The upgrade weakens nothing: `preference_pattern` simply moves to
always-on, so a populated decision-support index is kept as is.

Base spec 1.2.0 admits one new managed machine-store file, `.working/toml/init.toml`: the bootstrap
provenance a coupled `opf init` records (its format is frozen in OPF-INIT-D2B). It is a managed leaf
when present and is never required, so a store without it stays valid. For the 1.1.0 to 1.2.0 upgrade
the allowed schema delta is the `spec_version` bump alone: no other manifest field, schema file, or
counter changes, and no provenance is created for an existing store (none is ever fabricated).
Declared views are then regenerated, so a stale committed view can change. A 1.0.0 store takes the
1.0.0 delta above directly to 1.2.0.

For the 1.2.0 to 1.3.0 upgrade, the allowed schema delta is the version bump, registration and
create-only initialization of missing imported managed leaves for enabled types, and addition
of missing imported counter rows at zero only where no imported ancestry exists.
Existing records, evidence, clean counters and imported high-water values MUST be preserved;
a populated collision, missing ancestral counter or unprovable prestate refuses.
The upgrade creates no historical records, adoption approval or provenance, changes no posture
or import status, and adds no imported views.
An unresolved legacy import must be reconciled under its original contract before upgrading;
legacy LF records and evidence remain readable and are never silently converted.
A completed legacy import upgrades in place: its `import_status` stays `"complete"`,
substantiated by its preserved legacy run evidence under section 11, and no adoption approval,
receipt or provenance is fabricated for it.
The bump also activates the homes-1 recognition of the adoption and import control paths and
the four imported-series checks of section 4.2; the upgrade itself creates no such folder.
Earlier stores compose their applicable deltas with this delta; repeated upgrade is a verified
no-op only after full doctor VALID.
The 1.3.0 delta remains a target contract until a tested upgrade and its required readers
activate; import writing is a separate later activation.
Declaring a spec_version above the tooling's supported version, 1.3.0 on older tooling included,
is refused at validation rather than treated as supported; only the reserved homes-2 declaration
of section 4.2 stays recognized, gated on its own activation.

## 10. Views and deliverables

### 10.1 Deterministic views

Two kinds of generated view render from the store to `.working/` top level, both declared in the
manifest's view map:

- **1:1 index mirrors**: `<TYPE>-INDEX.md` (the type name uppercased, hyphen, INDEX), one per
  enabled type where declared, mirroring the machine index for human reading.
- **Composed views**: `TODO.md`, `BACKLOG.md`, `PIPELINE.md`, `DONE.md`, `FINDINGS.md`,
  `DECISIONS.md`, `BLOCKS.md`, `HANDOFF.md`, `REFERENCES.md`, `CONTRIBUTIONS.md`, `WORKLOG.md`, and the
  optional `VERSION.md`, each composed from declared sources through the transform vocabulary.

The root `VERSION` file is a deterministic deliverable rendered from `version.toml` into the
product repository (section 5.8).

### 10.2 The transform vocabulary

Composition is bounded by a closed, versioned vocabulary: filter on declared field predicates; sort
on declared keys with ID as the final tie-breaker; group by a declared field; project declared
columns; and exactly two named joins, the block join (actionability, section 8.5) and the
decision-resolution link (current effective resolution through the supersession chain). Anything
beyond this vocabulary requires a specification version bump; ad hoc logic never enters a
generator.

### 10.3 Determinism requirements and generated headers

A deterministic render is byte-reproducible: UTF-8, LF line endings, stable ordering, no
locale-dependent sorting, no wall-clock content in compared bytes, no network access, and no model
involvement. Every generated file opens with a header stating that it is generated and must not be
hand-edited, naming its source paths, the schema and generator versions, a digest of the source
set, and the regeneration command; the header carries no timestamp.

### 10.4 The curated changelog is not a view

`CHANGELOG.md` is a deliverable, not a deterministic view: its prose is human-curated, so it is
never byte-drift-gated. Its gates are range coverage and freeze (section 7). Its entries carry no
do-not-edit header; instead the changelog opens with a short note that it is a curated summary of
the project's worklog, gated on coverage and publication freeze.

### 10.5 Machine projections

A machine projection is a third generated-output class beside deterministic views and the curated
changelog: a deterministic, byte-drift-gated TOML deliverable at `.working/` top level, its name
UPPERCASE per the casing convention (section 4.6) because it is generated, never hand-authored truth.
It is declared in the manifest view map with the view kind `projection`, so it is covered by the same
render, drift, and doctor gates as any declared view with no new gate. Every determinism requirement of
section 10.3 applies verbatim (UTF-8, LF, stable ordering, no locale-dependent sorting, no wall-clock
content, no network access, no model involvement, and a leading do-not-edit header naming its sources,
the schema and generator versions, a source-set digest, and the regeneration command, with no
timestamp), the header being a leading TOML comment block. Advisory activity over a projection, an
assistant-side prediction, dedup, or pattern-spotting pass, is a READ-TIME activity outside the
generator, so no model output can ever be baked into the generated bytes.

The shipped projection is `DECISIONS.toml`, the machine counterpart to the composed `DECISIONS.md`
view, projected from `pending_decision`, `autonomous_decision`, `maintainer_decision`, and
`preference_pattern`. Its payload (projection contract v1) carries `schema = 1` (the projection
contract's own version, bumped independently) and `projection = "decisions"`; four arrays of tables,
one per source type, each row sorted by numeric ID and projecting the full base record (the envelope
fields, the type's declared extra fields, and `links`/`refs` as nested arrays of tables), with
`x-<vendor>` extension tables excluded as profile-owned data; and one `[derived]` table carrying the
existing decision-resolution join's output (`effective` and `superseded` pending_decision IDs,
numerically sorted) and nothing beyond the closed section-10.2 vocabulary, so no new join is
introduced.

## 11. Enforcement posture

Enforcement has two layers with deliberately different ceilings:

| Layer | `off` | `warn` | `required` |
|---|---|---|---|
| Artifact and record integrity | not run | reported, non-failing | build-failing, fail-closed |
| Adoption coverage | not run | report only | report only, never build-failing |

The integrity layer is deterministic and safe to hard-fail; `opf doctor` runs it. It includes:
schema validity of what exists; ID uniqueness across active, archive, and staging; counter
monotonicity; bidirectional index reconciliation; transition legality and no-resurrection; the
all-or-none resolution bundle; view drift (byte) for every deterministic view including `VERSION`;
worklog span tiling and frozen coverage digests; changelog range coverage; changelog freeze;
archive integrity; the tracked-store requirement against the resolved store; pointer and
sync-target agreement (the committed pointer, the manifest's recorded sync target, and the store
repository's actual remote agree; section 5.6); unmanaged-path containment (section 14.2); and
path containment. At `required`, an unreadable, unparseable, or unresolvable declared input is a
failure, never an empty or clean result. Imported history joins this integrity layer through
the deterministic checks in sections 8.3 and 8.6; the authority firewall is never report-only.

`import_status = "none"` means clean start with no approved migrate-source import.
`"partial"` means the adoption receipt enumerates migrate-disposed sources whose completion
checks are not yet green; it can persist across assistant sessions without a process running.
`"complete"` means every such source has a green completion result under section 14.1, or that
a pre-1.3.0 legacy import finished and its preserved legacy run evidence substantiates it; the
section 9.2 upgrade preserves that legacy status without fabricating an approval or receipt.
A partial or complete status that neither an adoption receipt with completion results nor
preserved legacy import evidence substantiates fails closed, as does a missing, unreadable or
contradictory input. Neither elapsed time nor a staging directory proves status.
From the recorded approval until its disposition executes, a path the approved plan enumerates
as an outstanding retire, move or migrate source, and whose live bytes still match its plan
digest, is bounded adoption state: containment reports it as `migration_incomplete` detail
rather than failing it, at `"none"` during a clean start as much as at `"partial"`.
A digest mismatch or an unenumerated path remains a containment-gate failure at `required`;
the bounded treatment is never a blanket exemption.

Adoption coverage (which types are populated, which modules are wired, how much of the project's
operational surface has moved into the store) is a report, never a gate: breadth of adoption is a
journey, and failing a build over it would train bypasses. It stays report-only at every posture.

Defaults: scaffolding and clean-start adoption write `posture = "required"` and
`import_status = "none"`. An adoption with migrate-disposed sources keeps `required` and sets
`import_status = "partial"` until their completion checks are green, then `"complete"`.
Import status never weakens posture. Reports carry `migration_incomplete` while an approved
source or detected file remains unresolved. Weakening the posture (`required` toward `warn`
or `off`) is a guardrail-configuration change: it takes effect only through the maintainer's
explicit, recorded authorization, separate from adoption approval, and is never self-applied
by the assistant or by tooling.

A profile may raise, never lower, the effective posture: the effective posture is the strictest of
the base `posture` and every supported profile's `posture_floor`. Weakening the base `posture`
remains a guardrail-configuration change under the maintainer's recorded authorization; a profile
floor is additive and cannot substitute for that authorization in the loosening direction.

## 12. Rotation, archive, and retention

Rotation is relocation, never deletion, and never ID reuse. Records in unqualified terminal states,
other than worklog records, MAY rotate to `.working/toml/archive/<YYYY>/` (calendar-year buckets) on
manifest-declared age or size thresholds. Worklog records are excluded from that generic
terminal-record permission and rotate solely under the release-based rule: only released, frozen
worklog spans MAY rotate, and the unreleased `recorded` tail never rotates whatever its age or size,
because it is pre-terminal and mutable-until-release (sections 6.2 and 8.4). Open records, active
blocks, unresolved decisions, unresolved fragments, unexpired waivers, the current handoff, and the
unreleased worklog tail never rotate. The imported series never rotates at 1.3.0:
`<type>.imported.index.toml` and `worklog.imported.toml` sit outside the rotation thresholds and
their records never move to the record archive.

The record-rotation archive under the discovered machine store is distinct from the store-tree
`archive/` in section 4.2, which retains relocated adopter files and adoption preimages, never
rotated records. Neither is scanned as the other. Imported originals, acceptance evidence, and
retire preimages are retained indefinitely by default. Automatic reclamation applies only to staging
runs, after independent re-read and digest verification of their required evidence in its durable
home; age alone never authorizes deletion.

Each rotation writes the year's `archive.toml`, enumerating every moved ID (and, for the worklog,
every moved span) and its destination. Validation confirms that every ID exists in exactly one
active or archived location, and coverage gates read active and archive together, so rotation never
changes any gate's answer. `counters.toml` is untouched by rotation, preserving ID permanence.
Retention is thereby indefinite by default; an adopter bound by a retention policy applies it as a
recorded maintainer decision governing archival and rotation (aged data moved into the archive,
preserved byte for byte), never as deletion of a record.

## 13. Tamper evidence

Tamper evidence is layered:

- **History.** The tracked store (section 5) puts every record change in version-control history
  under the project's review gate, and store relocation preserves that history (section 5.4); the
  store is never the only copy of its own past.
- **Frozen digests.** Released worklog spans are frozen by `coverage_digest`; published changelog
  entries are frozen by their summary digests. A silent edit to either breaks a recorded digest and
  fails the integrity layer; the only way through is the recorded re-publish flow (section 7.2).
- **Index reconciliation.** In the per-record layout, index rows carry per-record digests, and
  reconciliation between the index and record files is bidirectional; in the inline layout, the
  index and store coincide and reconciliation runs against the views and ledgers.
- **Archive enumeration.** `archive.toml` makes every rotation enumerable, so a record cannot
  quietly vanish under the name of rotation.

Records are never deleted: an unreleased entry may be corrected in place, and a frozen or released
record is immutable or supersession-marked. Nothing ever leaves the store. A record moves only by
rotation, an archival relocation within the store that `archive.toml` enumerates (section 12), which
preserves the record byte for byte and keeps every ID resolvable in its active or archived location.
That enumeration records the movement; it never authorizes a departure from the store or a deletion.

## 14. Adoption, post-adoption import and pre-existing files

`opf adopt` guides setup and wiring; `opf import` is only the post-adoption import activity.
Relocating the store remains `opf migrate` (section 5.4). Clean start is first-class: preserve
and retire old operational files, establish the new store and enforcement, and import nothing.
This makes no claim that historical obligations were fulfilled or converted.

Adoption follows investigate, plan, one approval, apply, completion, then retirement. Investigation
distinguishes first adoption from re-adoption and records a digest-stamped inventory, including
governance surfaces for each supported assistant platform. Every foreign `.working/` file has
a disposition before `init-store`; adoption never runs blind init over populated content.
Apply composes the coupled-init substrate and the journaled adoption operations, with per-operation
preimage checks and reversal. It ends with rendered views and an adoption receipt plus its
outcome-event chain. A bootstrap `views-ready` milestone alone is not adoption success.

### 14.1 One approval and completion

Exactly one adopter approval follows each concrete plan. The `opf.adoption.plan/v2` plan binds:

- product and store identities and the observed revision;
- every source path, byte digest, disposition and preservation destination;
- exact creations, replacements, removals and consumer repointings;
- tool release identity, including `manifest_sha256` checked against an independent anchor;
- the version and digest of the prompt pack;
- enforcement-pack contents per platform and each platform's disclosed residual coverage;
- the completion-check roster and the rule that retirement requires green checks and matching bytes;
- the missingness and unparsed-content policy, the skip policy under which a migrate-disposed
  source may be recorded as skipped, and the migrate-disposed sources import may touch.

The attributed approval binds `plan_digest` and `inventory_digest`, hence that whole plan.
There is no per-fragment acceptance or later adopter checkpoint within the approved plan.
Any bound-item drift refuses into a fresh plan with its own single approval; a changed old file
is not retired. Approval never absorbs a separate posture-weakening authorization (section 11)
or changelog curation (section 7.3). Digests establish binding, not actor authenticity or semantic
correctness; self-asserted identity and same-user tampering remain disclosed residuals.

The clean-start completion check deterministically verifies the following roster:

1. Authority and freshness: roots, destinations and live preimages still match the approved plan.
2. Discovery accounting: every inventory entry has a disposition or recorded exclusion.
3. Preservation and restore: each retirement preimage exists, digest-matched, under
   `.working/archive/adoption/<run-id>/`, and a restore exercise reproduces its bytes.
4. Operational readiness: the store resolves to the planned identity, and CI asserts store
   presence and identity so absence cannot pass as NOT-APPLICABLE. Doctor validity and
   declared-view byte drift are evaluated against the validated prospective poststate in which
   every plan-enumerated disposition and same-path publication has executed, so a plan-enumerated
   old file still occupying its planned destination is not a deadlock, and any drift the plan
   does not account for fails the check. Consumer repointings match the plan.
5. Wiring: the enforcement pack is installed and probed, with a direct store write denied and
   sanctioned writer and render paths succeeding. Server-side branch protection is
   adopter-attested, explicitly outside the local probe's guarantee.
6. Retirement readiness: each retire-disposed file is present in the live tree and its live
   bytes still equal its plan digest.

Retirement occurs only on a green completion check. Failure or cannot-evaluate reports incomplete,
retires nothing, and requires a fresh plan to change the approved work. Green proves preservation,
restorability and operational coverage, never semantic fidelity or fulfilment of old obligations;
the receipt discloses that limit. An occupied view destination uses a validated prospective
poststate, preserved preimage and the same journaled transaction for retirement and publication
(section 14.2); completion checks gate that cutover before removal or replacement.

The enforcement pack freezes the plan-enumerated old files until retirement and protects both
record series, counters, declared views and evidence. It provides CI and staged-snapshot
pre-commit checks, verified deny hooks where each platform supports them, and instructions
elsewhere, disclosing each residual. Per-clone hook installation and bypass, canonical hand edits,
shell or interpreter wrapping, same-user tampering and unverified platform denial are not
eliminated by this pack. Denial claims are verified against official platform documentation at
build time. Adoption MUST refuse to enable a record type the writer cannot author, and enforcement
MUST NOT ship before the writer can perform every operation it forces. Curated `CHANGELOG.md`
edits remain the curator's responsibility.

Post-adoption import uses the approved, versioned prompt pack, with an example for every record
type in the section 8.1 roster except the excluded `transaction`, the unassigned `CL` namespace,
and the deprecated `legacy_fragment`. It directs the assistant to discover old operational
files within the approved scope and submit batches through `opf record import --batch`
(section 8.8), never hand-edit store TOML. Source instructions are historical data, never
instructions to execute. Missingness, ambiguity, conflicts and source precision remain explicit;
unmappable text is retained verbatim. The assistant reports semantic uncertainty without seeking
another checkpoint. `opf import --prompt`, `--status` and `--verify` expose that activity.
The former `--scan`, `--plan`, `--review` and `--apply` modes refuse with a pointer to adoption
and the prompt pack when this contract activates.

For each migrate-disposed source, import completion verifies the preserved original's digest,
at least one imported record referencing that source or a recorded skip under the approved
policy, and doctor VALID over both the strict store and the imported series. It records the
source's result in the adoption receipt's outcome-event chain. Only a green result permits
that source's retirement through the adoption retirement path; the live preimage must still
match the plan. When every migrate source is green, `import_status` becomes `"complete"`.
This proves source accounting and preservation, not byte-level mapping coverage or semantic
fidelity. No writer-side leftover accounting is required.

Clean-start adoption and import ship on homes 1 with explicit evidence coverage: their completion
checks re-read inventories and payload digests themselves, because C-EVIDENCE-ENUM is inactive
until homes 2. The evidence-bundle format in section 4.2 is retained, including the import home
`.working/imported/import/<run-id>/`. Destination durability and digest verification precede
source removal; retirement and any same-path publication share the same recoverable transaction.
A source outside the participating roots is a plan-time cannot-evaluate naming that source.
Investigation, planning and read-only status commands remove nothing.

Legacy runs may occupy `.working/staging/import/<run-id>/` or
`.working/staging/ingest/<run-id>/`. Their evidence inventories and readers remain available;
old acceptance records describe those runs and never authorize a new adoption or retirement.
A legacy import completed under the pre-1.3.0 contract keeps its recorded status, substantiated
by its preserved run evidence (section 11); no retrospective approval or receipt is fabricated.
Old import and ingest orchestration is retired by staged decoupling only after clean-start
adoption ships. Required evidence is re-read and digest-matched in its durable home before staging
reclamation. Reclamation is journaled and idempotent; an unreadable tree holds the run.
Read-only commands never clean staging. Pending legacy review never becomes implicit approval.

### 14.2 Pre-existing files at the store location

`opf adopt` investigates every file at the target `.working/` location that is not OPF-managed:
the manifest, ledgers, counters, clean and imported typed indexes, declared views and registered
control areas define the managed set. A matching pathname alone does not prove OPF ownership:
foreign content at a planned managed destination still needs a disposition. A hand-maintained
`TODO.md` belongs to the adopter.
`opf init` refuses undispositioned foreign content; post-adoption import uses only its approved
source scope and does not authorize an incidental discovery.

The plan records one disposition per foreign file from `keep`, `migrate`, `move`, `retire`:

- **Keep.** Leave it untouched and register it under `[unmanaged]`. Ordinary tooling never reads,
  rewrites or deletes it. An unmanaged path cannot collide with an OPF-managed file or view.
- **Migrate.** Keep the source frozen now for post-adoption import into the separate imported
  series. Preserve its exact bytes and retire it only after its source completion check is green.
  There is no imported view. Where an old file occupies a clean generated-view destination,
  validate the prospective poststate and preserve the preimage, then retire and publish in the
  same journaled transaction only after the applicable completion check is green. A pending
  migrate source cannot be overwritten to make adoption pass: the occupied destination's
  publication waits, in that same transaction, for the green check.
- **Move.** Relocate to a named destination outside the managed store, or by default to
  `.working/archive/moved/<source-path>`, preserving substructure. An occupied destination is a
  collision finding, never an overwrite. An explicit destination inside the store tree is valid
  only beneath `.working/archive/moved/`. The default Move refuses a store resolved outside the
  product root until a multi-root coordinator exists. Removal requires the plan-bound green
  completion check and digest-verified preservation.
- **Retire.** The first-class clean-start choice: preserve the exact preimage under
  `.working/archive/adoption/<run-id>/`, prove restore and completion, then remove or replace
  it without import. No old obligation is thereby fulfilled.

Detection surfaces unresolved files in the plan and never silently absorbs, deletes or overwrites
them. Dispositions are plan data covered by the single approval, not separate approvals per file.
An unreadable declaration or detected input fails closed. Reports retain `migration_incomplete`
until the declared work is resolved.

The reserved children `archive/`, `imported/`, `staging/`, and `journals/` are OPF control area.
Detection never surfaces them as adopter content, no adoption option selects them, and no
`[unmanaged]` declaration may equal, contain, or lie within them. Adoption evidence is committed
and immutable under `.working/imported/adoption/<run-id>/`; append-only outcome events retain
the receipt's history. In homes 2, transaction records live under `.working/journals/adoption/`;
homes 1 retains its legacy journal paths and completion-carried evidence checks.

After adoption, containment uses the receipt-bound `import_status` and the bounded treatment of
section 11: a plan-enumerated outstanding retire, move or migrate source, digest-matched, is
reported rather than failed until its disposition executes, whatever the status. An unregistered
path outside that enumerated scope fails containment at `required`. No source is implicitly
imported and no staging presence grants authority.

### 14.3 Migrating an existing release pipeline

An adopter with an existing single-source release pipeline (for example, a release-notes TOML
that generates a version file and a changelog) migrates by recording its releases in
`version.toml` as imported-flagged `[[release]]` rows (the optional `imported` flag of
section 6.1) and its per-release notes as published
per-release `[[summary]]` rows.
Pre-migration releases have no worklog entries: their spans are empty and their summary digests
are recorded as imported facts, flagged as resting on imported provenance rather than on a
witnessed release cut. This remains the historical-release path at 1.3.0: the ledger is not an
imported record type, its spans never refer to `imported:WL` IDs, and import does not create a
separate version or changelog TOML file. Changelog prose still requires the curator's act.

## 15. Genericization boundary

Nothing in a conforming store's base-required schema vocabulary names a particular adopter,
operator, profile, internal system, endpoint, tier vocabulary, or command. A profile schema (for
example `[profiles.aiqt]`) legitimately names its owner, and a conforming store's own data
legitimately names an operator via `actor.id`; the neutrality constraint binds the base-required
vocabulary, not profile schemas or store data. `actor.kind` carries only the
portable categories; identity detail lives in `actor.id` or extensions. `mode`,
`tier_assessment`, and `waiver` ship structure only (evidence, assessor, outcome, validity, scope,
expiry) with adopter-supplied vocabularies. The location patterns of section 5.3 are described
generically; no pattern names a real repository, host account, or internal system. Import and
adoption provenance, including originals under `imported/` and retired files under `archive/`,
stays inside the adopter's own repositories. In homes 2, `.aiqt/` is AIQT-owned material, not an OPF
state home; OPF operates without it. Only homes migration may read explicitly inventoried OPF
artefacts from former `.aiqt/` locations, without touching unrelated AIQT material.
Experimental fields ride registered
`x-<vendor>` tables only, within the limits of section 8.7.

The base standard's required schema vocabulary names no adopter, operator, or profile by
definition; a conforming store's own data legitimately may (an operator via `actor.id`, a profile
via a `[profiles.<name>]` table the adopter chose). AIQT appears in the standard's title and brand
as trademark and authorship attribution (the standard is authored and maintained by its lead
maintainer), which is attribution rather than a requirement dependency; AIQT is also one profile,
`[profiles.aiqt]`, cited only as the reference enforcement suite and a consumer. A profile
carries an adopter's own additional requirements without the base ever depending on them.

## 16. Conformance vocabulary and claims

Conformance is reported against the base and, separately, against each declared profile a tool
evaluated. A report speaks in `conformant_for_declared_scope`, `nonconformant`, `indeterminate`, or
`migration_incomplete`, each qualified by whether it concerns the OPFiles **base** or a named
**profile**. Every report names its scope, its exclusions, and its cannot-evaluate results. A
base-conformant store may declare a profile the reporting tool did not evaluate; the report names
that profile as unevaluated rather than implying whole-store coverage. An unqualified claim of
"OPFiles conformant" or "AIQT conformant" is never emitted, by tooling or by prose: a
conformance claim is a completeness claim over a declared set, and it enumerates that set, including
which profiles were and were not evaluated. Until validation tooling ships, a conformance claim is
self-asserted and MUST say so.

## 17. Residual coverage disclosures

The gates in this standard are strong where they are strong and say so where they are not:

- The freeze gate proves a published summary's bytes changed only through recorded re-publication;
  it cannot prove the prose is accurate or complete. Human curation (section 7.3) is that control.
- Range coverage proves every release is summarized exactly once; it cannot judge summary quality.
- The unreleased worklog tail is mutable until release: an entry there MAY be corrected in place, a
  guarantee that rests on review and version-control history rather than machine enforcement; machine
  freezing and immutability begin at release cut.
- Store resolution fails closed on a pointer that does not resolve and on zero or multiple
  manifests at the target; it cannot detect a second store that no pointer names, placed somewhere
  the tooling was never aimed.
- The tracked-store check verifies the resolved store is under version control, not ignored, and
  that the pointer, the manifest's recorded sync target, and the actual remote agree; it cannot
  verify the backup, access, or hosting discipline of the repository that tracks the store. The
  local-only pattern in particular places durability wholly on the adopter's own backup, which is
  why choosing it SHOULD be a recorded decision (section 5.3).
- The single-writer lease has a propagation window: between a lease being taken and its becoming
  observable at the sync target, two systems can both begin. The consistency contract's divergence
  check is the overlapping control that catches that collision after the fact; the two layers
  together, not the lease alone, are the guarantee (section 5.7).
- A host provider's create and auth conveniences call the external API of the host the target
  names. The egress bound is that named host and nothing else; the standard cannot vouch for the
  host's own behaviour beyond that bound.
- Public deliverables reach the product repository through `opf render` under the product
  repository's normal review flow (section 5.8); the quality of that review flow is the adopter's
  own discipline, which this standard requires to exist but does not itself gate.
- A base-only tool does not evaluate profiles: a store could satisfy the base and violate a profile
  it declares, and a base-only tool would not detect it. This is by design (profiles are additive
  and a base tool is out of their scope), and it is the reason a conformance report always names
  which profiles it did and did not evaluate. Fail-safe-for-unknown-profiles is scoped to a tool
  that does not cover the profile; a profile-aware tool fails closed on its own profile.
- The base discovery token `opf` is a single exact string carried in every adopter manifest.
  A mistyped or altered token makes the store undiscoverable, which resolves to cannot-evaluate
  (fail-closed), never to a silent empty store. The token is stable within a base-schema major line;
  a store-breaking rename ships only with the tested `opf upgrade` migration (section 9.2), which
  rewrites the base table and token in place so no existing adopter's manifest is stranded. The
  retired 1.0.0 token `devprocess` is recognized by `opf upgrade` alone, purely to carry a legacy
  store forward.

## Appendix A: record envelope example

Synthetic data throughout; no real project, person, or record.

```toml
[[record]]
id = "FN-7"
type = "finding"
status = "fixed/proposed"
title = "Generated view drifted from its source index"
created_at = "2026-08-12T09:14:02Z"
updated_at = "2026-08-12T11:40:55Z"
actor = { kind = "assistant" }
summary = "BACKLOG.md no longer matched backlog_item.index.toml after a hand edit."
severity = "minor"
links = [ { rel = "remediates", id = "BI-42" } ]
refs = [ { kind = "path", locator = ".working/BACKLOG.md", note = "drifted bytes" } ]
```

## Appendix B: version.toml example

```toml
schema = 1

[[release]]
version = "1.2.3"
date = "2026-06-14T00:00:00Z"
worklog_span = ["WL-1", "WL-88"]
coverage_digest = "sha256:2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae"

[[release]]
version = "1.3.0"
date = "2026-08-30T00:00:00Z"
worklog_span = ["WL-89", "WL-131"]
coverage_digest = "sha256:fcde2b2edba56bf408601fb721fe9b5c338d10ee429ea04fae5511b68fbf8fb9"

[[summary]]
covers = "unreleased"
status = "working"

[[summary]]
covers = "1.3.0"
status = "superseded"
digest = "sha256:a3f5c1de9b6a44708d622de1f9f26bbee2ccc0be9cbb1c19b599162eeb0ed4f1"
superseded_by = "1.2.3..1.3.0"

[[summary]]
covers = "1.2.3"
status = "superseded"
digest = "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
superseded_by = "1.2.3..1.3.0"

[[summary]]
covers = "1.2.3..1.3.0"
status = "published"
digest = "sha256:60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752"
```

## Appendix C: worklog.toml example

```toml
schema = 1

[[entry]]
id = "WL-131"
date = "2026-08-29T16:22:41Z"
actor = { kind = "maintainer" }
kind = "fixed"
summary = "Close the view generator's stale-output gap on renamed types"
links = [ { rel = "resolves", id = "BI-42" }, { rel = "remediates", id = "FN-7" } ]

[[entry]]
id = "WL-132"
date = "2026-09-02T10:05:19Z"
actor = { kind = "assistant" }
kind = "docs"
summary = "Correct the block-join description in the composed-view docs (corrects WL-90)"
links = [ { rel = "corrects", id = "WL-90" } ]
```

## Appendix D: CHANGELOG.md entry examples

```markdown
## unreleased

- In progress: per-record layout documentation.

## 1.2.3..1.3.0 (2026-06-14 to 2026-08-30)

The baseline store through the view-pipeline hardening: the typed store, the nine
baseline record types, deterministic views with a drift gate, and the first import
tooling; then renamed types can no longer leave a stale generated view behind, and
composed views now surface records awaiting ratification.
```

---
