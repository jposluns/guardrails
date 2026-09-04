# AIQT OPF: the operational-files standard

Standard token: `aiqt-opf`. Status: draft (specification only; schemas and the reference tooling,
the scaffolder `opf init`, the importer `opf import`, the validator `opf doctor`, the renderer
`opf render`, the relocator `opf migrate`, and the synchronizer `opf sync`, ship in later
releases). Date: 2026-09-04 (UTC). Part of the AIQT Guardrails pack, CC BY-SA 4.0.

Unless a path is written from `/`, a path under `.working/` in this document is relative to the
root of the repository that tracks the store (the "store repository"), and every other path (the
pointer, `CHANGELOG.md`, `VERSION`) is relative to the root of the product repository. The two
roots coincide under the default configuration (section 4.1).

## 1. Purpose and scope

OPF (operational files) standardizes how a project keeps its operational records: the backlog, the
completion receipts, the worklog, findings, decisions, blocks, handoffs, and references that AIQT's
records-first discipline requires. It defines one machine-readable store of versioned TOML under
`.working/toml/`, a set of generated human-readable views above it, and three release artifacts (a
version ledger, a durable worklog, and a curated public changelog) with the gates that keep all of
them honest.

The store is always a git repository, wherever it lives. Its location is a free, migratable
configuration resolved through a committed pointer, never an architectural commitment: it defaults
to `.working/` in the product repository and can be relocated at any time to any location a target
can name, with history and the durable worklog preserved (section 5).

OPF specifies formats, layout, naming, lifecycle, and enforcement posture, and names the standard
command vocabulary of the reference tooling (`opf init`, `opf import`, `opf doctor`, `opf render`,
`opf migrate`, `opf sync`). It does not specify tooling internals; a reference implementation
follows in later releases of the pack. A project can conform to this specification with
hand-maintained files and its own checks.

## 2. Conformance language

MUST, MUST NOT, SHOULD, and MAY are used as in common standards practice: MUST is an absolute
requirement of conformance, SHOULD is a strong recommendation departed from only for recorded
reason, MAY is genuinely optional. Statements without these keywords are descriptive.

## 3. Design principles

- **Records first.** The store is the source of truth. A decision, finding, or completion that is
  not recorded did not happen. Human-readable surfaces are derived, never authoritative.
- **Facts are append-only; views are re-rollable.** Detailed records are never consumed, rewritten,
  or deleted once frozen. Summaries and views over them may be regenerated, re-rolled, and
  re-worded at any time, because the facts beneath them persist intact.
- **Machine writes, human reads.** Sources are machine-shaped TOML; deliverables are generated,
  prominent, human-readable documents. Nobody hand-edits a generated file.
- **Location is configuration.** The store's location is a migratable setting, not a mode or an
  architectural commitment. Wherever the store lives, the public face of the product repository is
  identical, and moving the store is a first-class, history-preserving operation.
- **Fail closed.** A gate that cannot read, parse, or resolve an input it is meant to cover reports
  failure or cannot-evaluate, never a clean pass.
- **Determinism where claimed.** Anything called a deterministic render is byte-reproducible from
  its sources. Anything curated by a human is labelled as curated and is gated on its facts, not
  its bytes.
- **Generic by construction.** Nothing in the standard names a particular adopter, operator, tool
  vendor, or internal system.

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
    BLOCKS.md  HANDOFF.md  REFERENCES.md
    <TYPE>-INDEX.md ...            # optional 1:1 index mirrors (section 10.1)
    IMPORT-REPORT.md               # present only while a migration is unresolved
    toml/
      manifest.toml                # store manifest and discovery marker (section 9)
      counters.toml                # per-namespace ID high-water marks (section 8.2)
      version.toml                 # version and release ledger (section 6.1)
      worklog.toml                 # durable operational record (section 6.2)
      lease.toml                   # single-writer lease, present only while held (section 5.7)
      backlog_item.index.toml      # typed record files (section 8)
      done.index.toml
      finding.index.toml
      pending_decision.index.toml
      autonomous_decision.index.toml
      block.index.toml
      handoff.index.toml
      reference.index.toml
      archive/
        2026/
          archive.toml             # enumerates rotated IDs and spans (section 12)
          done.index.toml
          worklog.toml
      imports/<import-run-id>/     # staged import plans, mappings, fragments (section 14)
```

When the store has been relocated, the `.working/` tree lives at the store repository root exactly
as drawn, and the product repository keeps only the pointer and the public deliverables. The
per-record layout profile (section 9) additionally places one file per record under
`.working/toml/<type>/`, with each `<type>.index.toml` acting as the registry.

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

All machine-readable TOML lives in `.working/toml/`. The directory name `.working` at the store
repository root is fixed by this standard. The machine subdirectory's standard name is `toml`;
tooling MUST NOT hardcode it, and MUST locate it by discovery.

### 4.5 Manifest discovery

Within the resolved store repository, tooling locates the machine store by finding exactly one
immediate subdirectory of `.working/` containing a `manifest.toml` that declares
`standard = "aiqt-opf"` in its `[opf]` table, trying `toml` first. Zero matches, or more than one,
is a cannot-evaluate outcome: the tool reports it and stops; it never guesses, and never treats it
as an empty or absent store. The pointer names which repository carries the store; the manifest
discovery observes where, within it, the machine store sits. Because the machine subdirectory is
observed at each use rather than declared and trusted, renaming it is a directory move with
nothing to go stale; the pointer, which does declare a location, is validated at every resolution
and fails closed rather than trusting a stale target (section 4.3).

### 4.6 Casing convention and rationale

Source files are lowercase; deliverables are uppercase.

- Every file inside `.working/toml/` is lowercase: `manifest.toml`, `counters.toml`,
  `version.toml`, `worklog.toml`, `lease.toml`, `<type>.index.toml`, `archive.toml`. The pointer
  `.opf.toml` and its local override are lowercase machine source on the same terms.
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
changes when it moves, and the product repository's public face is identical wherever the store
lives (section 5.8).

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
  extraction, and preserve the durable append-only worklog and its archive byte for byte: a
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
  unverified destination never justifies destroying the source: this is the pack's
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

Nothing stale, nothing ahead. Before any OPF operation (`init`, `import`, `doctor`, `render`,
`migrate`), the store repository is reconciled to a known-consistent, up-to-date state against its
sync target: the target is fetched and the local store compared against it.

- **Equal:** the operation proceeds.
- **Behind the target:** the tooling refuses to operate and surfaces the state. The remedy is a
  fast-forward pull to current (`opf sync`), which the tooling MAY offer and perform as its own
  surfaced step, then re-run the operation; the pull is never folded silently into another
  operation.
- **Ahead of, or divergent from, the target** (unsynced commits on two systems): the tooling
  refuses to operate and surfaces the state. A divergence HALTS for the human, always: it is never
  auto-merged and never silently resolved by picking a side, because a textual merge of
  append-only TOML ledgers can silently mangle the very records the standard exists to protect.
- **After any operation that writes,** the store is synced back to its target in the same session,
  so the store never lingers ahead on one system.

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

This contract deliberately dogfoods two shipped AIQT rules, which are its basis: the
concurrency-lease rule (hold a lease so two runs never act on the same state at once, reconcile it
on resume or close, never seize it from a live holder) and the reconcile-record-against-reality
rule (the store is authoritative only while it matches reality, so divergence is detected by
observation at defined checkpoints and treated as a finding to resolve, never a discrepancy to
leave standing; a store can never certify itself current merely because nothing updated it).

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

### 5.8 The public face is identical across topologies

Generated public deliverables always live in the product repository: the root `CHANGELOG.md`, the
root `VERSION`, and any future public view. This holds identically whether the store is in-repo, a
private companion, local-only, or anywhere else; a reader of the product repository sees the same
files with the same bytes whatever the topology, and nothing about the product repository's
surface reveals or depends on where the store lives. Only the maintainer's systems need resolve a
private store; for everyone else the pointer is an inert file and the public deliverables are the
whole story.

Publication is stage-then-promote: `opf render` writes into the product repository only bytes that
have passed the store's gates, and those bytes land through the product repository's normal
review flow like any other change. Rendering from a private store into the public product
repository is the promotion step, and it never promotes anything the gates have not passed.

## 6. The release triad: version.toml, worklog.toml, and CHANGELOG.md

Three artifacts, deliberately separated so the version anchor, the detailed record, and the public
story cannot tangle: a machine ledger of releases, an append-only worklog of changes, and a curated
summary for the public. The ledger anchors versioning; the worklog holds every fact; the changelog
tells the story and can always be retold because the facts persist beneath it.

### 6.1 version.toml, the version and release ledger

`.working/toml/version.toml` is the machine ledger of version numbers, release dates, and release
boundaries. It is the single source for the project's version: the root `VERSION` file is
deterministically generated from it (the latest release's version, as exact bytes) and drift-gated,
and an optional human view renders to `.working/VERSION.md`. Release-delta tooling (the check that
computes the minimum required version bump for a change) anchors here. The ledger is not the
changelog: it carries numbers, dates, spans, and digests, never release prose.

Each `[[release]]` row records:

- `version`: the SemVer version string, unique in the ledger.
- `date`: the release date, RFC 3339 UTC, read from the clock at the release event.
- `worklog_span`: the inclusive, contiguous span of worklog entry IDs the release covers, as a
  two-element array `["WL-a", "WL-b"]`, or an empty array for a release with no worklog entries.
- `coverage_digest`: a digest over the canonical serialization of the covered worklog entries, in
  ID order, computed at release cut. The exact canonicalization is fixed by the schema release that
  follows this specification; it MUST be deterministic and cover the entries' full content.

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

The worklog is durable and append-only. Entries are never consumed, rolled away, or deleted; every
fact ever recorded stays in the worklog (or its archive, section 12) forever, and survives store
relocation byte for byte (section 5.4). This durability is what makes the changelog safely
re-rollable: a summary can be re-worded or re-rolled at any depth because the detail it summarizes
is never lost.

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
lives, the changelog's home is the product repository (section 5.8). It is machine-drafted and
human-curated: a summary of the worklog over declared version ranges, not a deterministic render,
and not byte-drift-gated. There is exactly one public changelog; it is conceptually single-sourced
from the worklog and the version ledger, and no separate changelog source file exists.

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
digest. It never applies to the worklog. The worklog is the frozen record; the changelog is a
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
- every such row has exactly one matching entry heading in `CHANGELOG.md`, and every entry heading
  matches exactly one such row.

The check confirms the start and end versions of each declared range against the ledger; it is
deterministic, and it fails closed on an unreadable or unparseable input.

### 7.2 Freeze and re-publish

Freeze the detail, not the summary. The worklog is the frozen append-only record; the changelog is
free to be re-worded, because editing a summary's prose can never change the facts, which persist
in the worklog.

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
| Governance module | maintainer_action | MA |
| Governance module | maintainer_decision | MD |
| Delivery-assurance module | artifact | AR |
| Delivery-assurance module | gate_run | GR |
| Delivery-assurance module | release | RL |
| Delivery-assurance module | waiver | WV |
| Operational-policy module | mode | MO |
| Operational-policy module | tier_assessment | TA |
| Concurrent-operation module | session_lease | SL |
| Decision-support module | preference_pattern | PP |
| Migration quarantine (importer-only) | legacy_fragment | LF |
| Reserved, excluded | transaction | TX |
| Reserved, unassigned | (none) | CL |

Notes on the roster:

- The word "changelog" is not a record type. It names the curated public deliverable (section 6.3).
  The detailed per-change type is `worklog`. The namespace `CL` is reserved unassigned so it can
  never half-collide with the freed word.
- `transaction` (`TX`) is excluded from the adopter standard with its name and namespace reserved;
  it may enter later as a versioned module only if portable semantics are demonstrated.
- Modules ship default-off; each is enabled by one manifest edit. `legacy_fragment` is created only
  by an importer, never scaffolded.
- `done` is a durable completion receipt linked one-to-one to a backlog item reaching ratified
  `done`; a standalone receipt is legal only for imported history with provenance.
- A `finding` records the observation and links its remediation rather than containing it.
- A `block` scopes one or more enumerated records and feeds actionability (section 8.5).
- The delivery-assurance `release` record, where enabled, references a `version.toml` release row
  by version string; the ledger row is the fact, the record is the delivery-assurance envelope
  around it.
- `version.toml` and `counters.toml` are control ledgers, not record types.

### 8.2 ID namespaces and counters

Record IDs have the form `<NS>-<n>`: the type's two-letter namespace, a hyphen, and a positive
integer. Namespaces map one-to-one to types. `counters.toml` holds one monotonic high-water value
per namespace; allocation increments it under the profile's lock as one atomic claim, so no gap
between choosing and reserving can double-allocate. Counters are never reset and IDs are never
reused, even when a record is superseded, refuted, or its work reverted. Rotation, index rewrites,
and store relocation never touch `counters.toml`.

### 8.3 The record envelope

Every record carries the envelope; types add their own fields on top. Schemas are closed: an
unknown key is a validation failure unless it sits under a registered vendor extension table.

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
recorded as unknown via the import provenance reference, never guessed.

The worklog entry uses a reduced envelope (`id`, `date`, `actor`, `kind`, `summary`, optional
detail, `links`, `refs`); its status is fixed (section 8.5).

### 8.4 The status grammar

```
status    ::= state ( "/" qualifier )?
state     ::= lowercase name from the type's declared state set
qualifier ::= "proposed"
```

- Each type declares a closed state set: one initial state, zero or more working states, and one or
  more terminal states.
- A terminal transition performed by an actor whose `kind` is `assistant` or `automation` lands
  with the `/proposed` qualifier (for example `done/proposed`). Only a maintainer transition
  removes the qualifier (ratification) or returns the record to a working state (rejection, with a
  recorded reason). A `/proposed` status is not terminal: gates and completion claims treat the
  record as unfinished, and views surface it as awaiting ratification.
- No resurrection: a record in an unqualified terminal state never re-enters a working state. A
  revived concern is a new record linking the old one.
- Supersession is a link, not a state edit: the superseding record links `supersedes`, and where
  the type records it, the superseded record's terminal state reflects it.

### 8.5 Transition rules

Baseline types:

| Type | States | Rules |
|---|---|---|
| backlog_item | `open` > `active` > `done` or `dropped`; `open` > `dropped` | Ratified `done` creates the one-to-one `done` receipt. Blocked-ness is never a stored state; it is derived from active blocks at view time. |
| done | `recorded` | Created terminal, immutable. Links `receipt_of` to its backlog item. |
| worklog | `recorded` | Append-only ledger row; mutability governed by section 6.2, not by transition. |
| finding | `open` > `fixed`, `routed`, `refuted`, or `accepted` | Severity is graded at or after the fix decision, never before. |
| pending_decision | `open` > `decided` or `withdrawn` | All-or-none resolution bundle: an open decision carries none of `decision`, `decided_at`, `decided_by`; a decided one carries all. A decided record may be superseded by a new decision linking `supersedes`; exactly one current effective resolution exists per chain. |
| autonomous_decision | `recorded` | Immutable ACT record: the classification basis, the action, links. Overturning is a new record (or maintainer decision) linking it. |
| block | `active` > `released` or `expired` | Scopes an enumerated list of record IDs. A block created by an assistant or automation actor is `active/proposed` and is a proposal, not a grant: it does not count toward blocked-ness or justify a stop until a maintainer ratifies it. |
| handoff | `current` > `superseded` | Posting a new handoff supersedes the previous in the same act; at most one `current` handoff exists. |
| reference | `recorded` | Immutable captured reference. |

Module types, in outline (full schemas ship with the module schemas release): maintainer_action
`open` > `done` or `dropped`; maintainer_decision `recorded`; artifact `staged` > `promoted` or
`rejected`; gate_run `recorded` with a three-valued verdict field (`pass`, `fail`,
`cannot_evaluate`), never folded into status; release `planned` > `published` or `abandoned`;
waiver `active` > `expired` or `revoked`, expiry required at creation; mode `active` > `retired`;
tier_assessment `recorded`; session_lease `held` > `released` or `reconciled`; preference_pattern
`active` > `retired`; legacy_fragment `quarantined` > `resolved` or `ignored`.

Actionability: a backlog item is actionable when its state is `open` or `active` and no unqualified
`active` block scopes it. This is the block join every scheduling view renders.

### 8.6 Links and reference capture

`links` relate records to records; `rel` comes from a closed vocabulary: `supersedes`, `resolves`,
`remediates`, `receipt_of`, `corrects`, `follows`, `relates`. Extending the vocabulary is a
specification version change.

`refs` capture external sources at the moment a claim or artifact is produced: each is
`{kind, locator, note}` with `kind` one of `path` (a repository path, with a line where
applicable), `url`, or `doc` (a document and section). A record whose claims rest on an external
source without a captured reference is unsourced, whatever confidence backs it.

### 8.7 Extensions

Experimental or adopter-specific fields ride only under `x-<vendor>` tables, with each vendor token
registered in the manifest; an unregistered prefix is a validation failure. An extension may add
metadata but MUST NOT override identity, state, transitions, resolution completeness, publication
inclusion, block actionability, counters, lock ordering, or actor attribution.

## 9. The manifest

`.working/toml/manifest.toml` is the store's control document and discovery marker. Illustrative
shape (the schema release that follows this specification is normative):

```toml
[opf]
standard = "aiqt-opf"          # discovery marker; exact token required
spec_version = "1.0.0"         # the pack release whose specification the store conforms to
layout_profile = "inline"      # "inline" or "per-record"
posture = "required"           # "off", "warn", or "required" (section 11)
import_status = "none"         # "none", "partial", or "complete"

[store]
sync_target = ""               # the store's dedicated sync target (section 5.7); empty under the
                               # in-repo default, where the store rides the product repository

[modules]
governance = false
delivery_assurance = false
operational_policy = false
concurrent_operation = false
decision_support = false

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
registered = []
```

View and deliverable targets under `.working/` are relative to the store repository root; the
public targets (`VERSION`, `CHANGELOG.md`) are relative to the product repository root
(section 5.8).

Layout profiles:

- **`inline`** (default): records live inline in `<type>.index.toml`; the index and the store
  coincide. One global store lock serializes writers. This is the ordinary single-writer case: a
  consumer reads one small file with one parse.
- **`per-record`** (with the concurrent-operation module): `<type>.index.toml` becomes a registry
  of `{id, state, path, digest}` rows, records live one per file under `<type>/`, and any
  multi-record operation takes `(namespace, id)` locks in ascending order. Concurrent writers pay
  the file-count cost only when they have the problem it solves.

Readers always enter at `<type>.index.toml` in either profile. The ledgers are exempt from the
per-record profile: `version.toml` and `worklog.toml` are always single files, written under the
store lock (allocation of WL IDs still goes through `counters.toml` atomically).

## 10. Views and deliverables

### 10.1 Deterministic views

Two kinds of generated view render from the store to `.working/` top level, both declared in the
manifest's view map:

- **1:1 index mirrors**: `<TYPE>-INDEX.md` (the type name uppercased, hyphen, INDEX), one per
  enabled type where declared, mirroring the machine index for human reading.
- **Composed views**: `TODO.md`, `BACKLOG.md`, `PIPELINE.md`, `DONE.md`, `FINDINGS.md`,
  `DECISIONS.md`, `BLOCKS.md`, `HANDOFF.md`, `REFERENCES.md`, `WORKLOG.md`, and the optional
  `VERSION.md`, each composed from declared sources through the transform vocabulary.

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
failure, never an empty or clean result.

Adoption coverage (which types are populated, which modules are wired, how much of the project's
operational surface has moved into the store) is a report, never a gate: breadth of adoption is a
journey, and failing a build over it would train bypasses. It stays report-only at every posture.

Defaults: scaffolding writes `posture = "required"` (a clean store has no legacy excuse for drift);
import writes `warn` with `import_status = "partial"`, and every report carries
`migration_incomplete` until fragments and detected pre-existing files are resolved, at which
point the adopter flips to `required`. Weakening the posture (`required` toward `warn` or `off`)
is a guardrail-configuration change: it takes effect only through the maintainer's explicit,
recorded authorization, and is never self-applied by the assistant or by tooling.

## 12. Rotation, archive, and retention

Rotation is relocation, never deletion, and never ID reuse. Records in unqualified terminal states,
and worklog entries in released spans, MAY rotate to `.working/toml/archive/<YYYY>/` (calendar-year
buckets) on manifest-declared age or size thresholds. Open records, active blocks, unresolved
decisions, unresolved fragments, unexpired waivers, the current handoff, and the unreleased worklog
tail never rotate.

Each rotation writes the year's `archive.toml`, enumerating every moved ID (and, for the worklog,
every moved span) and its destination. Validation confirms that every ID exists in exactly one
active or archived location, and coverage gates read active and archive together, so rotation never
changes any gate's answer. `counters.toml` is untouched by rotation, preserving ID permanence.
Retention is thereby indefinite by default; an adopter bound by a retention policy applies it as a
recorded maintainer decision, never as silent deletion.

## 13. Tamper evidence

Tamper evidence is layered:

- **History.** The tracked store (section 5) puts every record change in version-control history
  under the project's review gate, and store relocation preserves that history (section 5.4); the
  store is never the only copy of its own past.
- **Frozen digests.** Released worklog spans are frozen by `coverage_digest`; published changelog
  entries are frozen by their summary digests. A silent edit to either breaks a recorded digest and
  fails the integrity layer; the only way through is the recorded re-publish flow (section 7.2).
- **Index reconciliation.** In the per-record profile, index rows carry per-record digests, and
  reconciliation between the index and record files is bidirectional; in the inline profile, the
  index and store coincide and reconciliation runs against the views and ledgers.
- **Archive enumeration.** `archive.toml` makes every rotation enumerable, so a record cannot
  quietly vanish under the name of rotation.

Records are append-only or supersession-marked; nothing leaves the store except by enumeration.

## 14. Import, pre-existing files, and legacy migration (outline)

Two different things are called migration; this standard keeps them apart. Relocating the store
itself is `opf migrate` (section 5.4). Bringing an existing project's operational content into the
store is import, covered here. Import tooling is tooling-heavy and lands in later releases; this
section fixes the posture the tooling must honour.

### 14.1 Import posture

- The import set is enumerated in full; an unreadable declared input is a failure, not an empty
  input. Extraction is deterministic; model-proposed mappings are untrusted plan data requiring the
  same validation and human acceptance as any other candidate.
- Every fragment lands in exactly one mapping state: `mapped`, `split`, `duplicate`, `ambiguous`,
  `incomplete`, `unmapped`, `ignored`, or `cannot_evaluate`. Everything not confidently mapped
  becomes quarantined `legacy_fragment` data with source path, digest, span, and run ID; nothing is
  dropped.
- Import runs stage under `.working/toml/imports/<run-id>/` and promote only a fully validated
  candidate, leaving originals in place.

### 14.2 Pre-existing files at the store location

On `opf init` and on `opf import`, the tooling detects every file already present at the target
`.working/` location that is not OPF-managed: not the manifest, the ledgers, the counters, the
typed indexes, a declared view target, or a path already enumerated as unmanaged. A project that
adopts OPF often already keeps a hand-maintained `TODO.md` or similar there; those files are the
adopter's, and the tooling treats them that way.

For each detected file, or coherent group of files, the tooling offers the adopter (or the AI
assistant driving the adoption on the adopter's behalf) three options:

- **Keep.** Leave the file exactly where it is, untouched, and record it under the manifest's
  `[unmanaged]` table. Tooling never reads, rewrites, or deletes an unmanaged path, and validation
  confirms no unmanaged path collides with the name of any OPF-managed file or declared view
  target.
- **Migrate.** Import the file's content into the appropriate OPF type through the same import
  machinery as any other source (staged under `imports/<run-id>/`, validated, promoted only on a
  full pass) and generate its view. Where the generated view lands at the same path as the
  original file, the replacement happens only as part of the promoted, validated, reviewed import,
  with the original's full content preserved in the import run (digest and fragments), never as a
  silent overwrite.
- **Move.** Relocate the file out of the managed store to a destination the adopter names, with
  the move recorded.

Detection is fail-safe: the tooling surfaces what it found and asks; it never silently absorbs,
deletes, or overwrites a pre-existing file, and it takes no default action on one. A detected file
the adopter has not yet decided on is recorded as unresolved, and posture reports carry
`migration_incomplete` until every detected file is resolved, exactly as they do for quarantined
fragments. The flow is assistant-drivable by construction: the options are presented as inert plan
data, the assistant or adopter picks per file, and each pick is recorded with its actor
attribution like any other decision.

After adoption, the same detection keeps running: a file that appears in `.working/` that is
neither OPF-managed nor enumerated as unmanaged is surfaced as a finding, never absorbed.

### 14.3 Migrating an existing release pipeline

An adopter with an existing single-source release pipeline (for example, a release-notes TOML
that generates a version file and a changelog) migrates by recording its releases as
`[[release]]` rows and its per-release notes as published per-release `[[summary]]` rows.
Pre-migration releases have no worklog entries: their spans are empty and their summary digests
are recorded as imported facts, flagged as resting on imported provenance rather than on a
witnessed release cut.

## 15. Genericization boundary

Nothing in a conforming store's shipped schemas, or in this standard, names a particular adopter,
operator, internal system, endpoint, tier vocabulary, or command. `actor.kind` carries only the
portable categories; identity detail lives in `actor.id` or extensions. `mode`,
`tier_assessment`, and `waiver` ship structure only (evidence, assessor, outcome, validity, scope,
expiry) with adopter-supplied vocabularies. The location patterns of section 5.3 are described
generically; no pattern names a real repository, host account, or internal system. Import
provenance stays inside the adopter's own repositories. Experimental fields ride registered
`x-<vendor>` tables only, within the limits of section 8.7.

## 16. Conformance vocabulary and claims

A conformance report speaks in a qualified vocabulary: `conformant_for_declared_scope`,
`nonconformant`, `indeterminate`, or `migration_incomplete`. Every report names its scope, its
exclusions, and its cannot-evaluate results. An unqualified claim of "OPF conformant" is never
emitted, by tooling or by prose: a conformance claim is a completeness claim over a declared set,
and it enumerates that set. Until validation tooling ships, a conformance claim is self-asserted
and MUST say so.

## 17. Residual coverage disclosures

The gates in this standard are strong where they are strong and say so where they are not:

- The freeze gate proves a published summary's bytes changed only through recorded re-publication;
  it cannot prove the prose is accurate or complete. Human curation (section 7.3) is that control.
- Range coverage proves every release is summarized exactly once; it cannot judge summary quality.
- Append-only enforcement on the unreleased worklog tail rests on review and version-control
  history; machine freezing begins at release cut.
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

## Appendix A: record envelope example

Synthetic data throughout; no real project, person, or record.

```toml
[[record]]
id = "FN-7"
type = "finding"
status = "fixed"
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
status = "published"
digest = "sha256:a3f5c1de9b6a44708d622de1f9f26bbee2ccc0be9cbb1c19b599162eeb0ed4f1"

[[summary]]
covers = "1.2.3"
status = "superseded"
digest = "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
superseded_by = "1.0.0..1.2.3"

[[summary]]
covers = "1.0.0..1.2.3"
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
links = ["BI-42", "FN-7"]

[[entry]]
id = "WL-132"
date = "2026-09-02T10:05:19Z"
actor = { kind = "assistant" }
kind = "docs"
summary = "Correct the block-join description in the composed-view docs (corrects WL-90)"
links = ["WL-90"]
```

## Appendix D: CHANGELOG.md entry examples

```markdown
## unreleased

- In progress: per-record layout profile documentation.

## 1.3.0 (2026-08-30)

Hardened the view pipeline: renamed types can no longer leave a stale generated
view behind, and composed views now surface records awaiting ratification.

## 1.0.0..1.2.3 (2026-01-10 to 2026-06-14)

Initial public line: the typed store, the nine baseline record types, deterministic
views with a drift gate, and the first import tooling.
```

---
