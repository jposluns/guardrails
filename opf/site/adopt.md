# Adopt OPFiles

This is the machine-readable adoption guide. The human-readable page is at /adopt.

OPFiles is an operational-files standard: a `.working/` store of lowercase TOML sources, generated uppercase views, and a drift check for comparing declared views with their sources. Adopting with an AI development assistant is the recommended path; adopting by hand is the fallback for when you cannot. Both paths target the same store format; configure and verify the required checks separately.

## What an OPFiles store is

By default, the store lives under `.working/` in your product repository; the committed `.opf.toml` pointer can name a separate store repository. Paths under `.working/` are relative to the store repository. Lowercase files under `.working/toml/` are machine source you change through tooling or review. Uppercase files at the `.working/` top level are generated views you read and never hand-edit. The pointer `.opf.toml` and curated `CHANGELOG.md` sit at the product repository root. A generated product-root `VERSION` is required whenever releases exist. The initial scaffold omits its view declaration; add that declaration when recording the first release so the reference renderer can produce the file. Doctor checks `VERSION` whenever releases exist, even if its view is undeclared.

## Before you apply: verify the artifact digest

Before scaffolding or applying anything to your project, obtain the pack's artifact digest and compare it against the published hashes at posluns.dev/hashes.txt. Proceed only on a match; refuse if the digest differs or the reference evidence is unavailable. This is a required adopter action on both paths below, not an automatic step that something else performs for you.

## The recommended path: assistant-guided adoption with `opf adopt`

The standard's adoption flow (OPF-SPEC.md section 14) runs in this order: investigate, propose one plan, take one approval, apply, then a completion check before any old file is recorded as retired. The adopter approves one concrete plan, once; any change to a bound item takes a fresh plan and a fresh approval. Clean start is first-class: preserve and retire the old operational files, establish the new store and its enforcement, and import nothing.

The prompt pack ships the instructions an assistant follows: `adopt/SKILL.md`, the adoption procedure, and `adopt/clean-start.md`, the guidance for a project with no prior records, both under `opf/prompt-pack/` in the reference repository (github.com/jposluns/guardrails) and listed with their digests in the pack's `pack.toml`. Give them to your assistant with this guide.

What the reference CLI runs today:

- Investigation, through the planner's read-only library entry; no `opf adopt` subcommand prints the inventory yet. It records a digest-stamped inventory, tells first adoption from re-adoption, and lists each file in its scope that needs a disposition: keep, migrate, move, or retire.
- `opf adopt plan --inputs FILE`, which freezes and prints the plan from a planning worksheet and writes nothing. Its output is an inert, digest-bound proposal, never permission to apply. The planner checks the shape of the release, prompt-pack, enforcement and postimage digests the worksheet supplies, not the bytes they name, with one exception: when a kept file registers into a store the plan creates, the digest planned for its manifest must be the default manifest's real digest.
- `opf adopt status`, which reports adoption runs and the adoption journal and writes nothing.

What it does not run yet: `opf adopt approve`, `apply`, `complete` and `reconcile` refuse with exit 2, so no approval is recorded, nothing is applied, and nothing is retired. Installing the enforcement pack through adoption, and post-adoption import, are not available either. An assistant following the pack stops at the approval step and reports, rather than doing the missing stages by hand.

For a project with no prior records, the clean-start guidance describes one interim route: `opf init`, review and commit, `opf render --write`, commit, then `opf doctor --require-store` and `opf render --check`. That route initializes a store; it is not an adoption. It records no plan, approval, receipt or completion check, and installs no enforcement. If you are adopting AIQT, include OPFiles in the plan.

## Adopting by hand

1. Create the machine store at `.working/toml/`: `manifest.toml` (the control document: an `[opf]` table declaring `standard = "opf"`, the base `spec_version`, the storage `layout`, the enforcement `posture`, and `import_status`, plus a `[types.<name>]` registration for every enabled type, the declared `[views]`, and the other tables the standard defines; see section 9 of OPF-SPEC.md, linked from /standard), `counters.toml` (the per-namespace ID high-water marks), `version.toml` (the version and release ledger, numbers, dates, spans, and digests, never release prose), `worklog.toml` (the durable operational record: one entry per change, correctable before its span is released, frozen afterward, never deleted), and the eleven baseline typed indexes as `<type>.index.toml`, empty to start.
2. Write the committed pointer `.opf.toml` at your product repository root, so the store resolves from a stable location.
3. Work records-first: treat the store as the source of truth, append a worklog entry per change, keep the backlog, findings, and decisions in their typed files, and regenerate the views rather than editing them.
4. Configure the drift check as a required commit or CI check: it re-renders the declared views from their sources and fails on byte differences. The reference tooling reports per-record view drift as cannot-evaluate.

The reference tooling requires Python 3.14 or newer. It validates spec 1.2.0 stores in either layout, with limits: it does not render or drift-check per-record views, and doctor reports module-tier and importer record content schemas as cannot-evaluate. For a store with module-tier or importer records, or a per-record store that declares views, doctor's overall verdict is cannot-evaluate (exit 2). Ordinary import is retired and refuses with exit 2, and nothing in the reference CLI creates `legacy_fragment` records; the type remains only for stores that hold them from an import under the pre-1.3.0 contract. In such a store, as in a store with module-tier records, doctor reports cannot-evaluate, `opf render --write` refuses, and `opf upgrade` exits 2 without offering the change, because the baseline validator does not validate those records' content schemas. If an upgrade from 1.0.0 or 1.1.0 passes preflight, it applies the schema delta before failing at render, leaving the changes in place for recovery. Transition checks also report cannot-evaluate when legality depends on an unidentified last-transition actor or a rejection requires unavailable pre-proposal state. See /disclosure for the tooling's limits.

Conformance you assert by hand is self-asserted until the reference validator has checked it, and you should say so wherever you claim it.

## More

- The standard: /standard
- Reference tooling: /tooling
- The prompt pack: `opf/prompt-pack/` in the reference repository
- The disclosure of current limits: /disclosure
