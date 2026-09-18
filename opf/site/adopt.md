# Adopt OPFiles

This is the machine-readable adoption guide. The human-readable page is at /adopt.

OPFiles is an operational-files standard: a `.working/` store of lowercase TOML sources, generated uppercase views, and a drift check that rejects any view that no longer matches its source. Adopting with an AI development assistant is the recommended path; adopting by hand is the fallback for when you cannot. Both paths reach the same gated store.

## What an OPFiles store is

The store lives in your product repository. Lowercase files under `.working/toml/` are machine source you change through tooling or review. Uppercase files at the `.working/` top level are generated views you read and never hand-edit. A committed pointer `.opf.toml` at the repository root names where the store lives, and the curated `CHANGELOG.md` and the generated `VERSION` sit at the repository root.

## Before you apply: verify the artifact digest

Before scaffolding or applying anything to your project, obtain the pack's artifact digest and compare it against the published hashes at posluns.dev/hashes.txt. Proceed only on a match; refuse if the digest differs or the reference evidence is unavailable. This is a required adopter action on both paths below, not an automatic step that something else performs for you.

## The recommended path: assistant-guided adoption

Point your AI development assistant at this site or the repository and tell it to adopt OPFiles, or to adopt AIQT, which brings OPFiles with it. It reads the standard, inspects your project, and proposes a short plan: what it will create, import, or retire, and how the pieces wire together. On your approval it scaffolds and validates the store, wires the records-first process into your project's assistant instructions, and leaves the `.working/` store for your review.

## Adopting by hand

1. Create the machine store at `.working/toml/`: `manifest.toml` (the control document, declaring `standard = "opf"`, the base `spec_version`, the storage `layout`, and the enforcement `posture`), `counters.toml` (the per-namespace ID high-water marks), `version.toml` (the version and release ledger, numbers and digests only), `worklog.toml` (the append-only operational record), and the eleven baseline typed indexes as `<type>.index.toml`, empty to start.
2. Write the committed pointer `.opf.toml` at your product repository root, so the store resolves from a stable location.
3. Work records-first: treat the store as the source of truth, append a worklog entry per change, keep the backlog, findings, and decisions in their typed files, and regenerate the views rather than editing them.
4. Wire the drift check as a required commit or CI check: it re-renders the views from their sources and rejects any that disagree, so the sources and the views cannot drift apart.

Conformance you assert by hand is self-asserted until the reference validator has checked it, and you should say so wherever you claim it.

## More

- The standard: /standard
- Reference tooling: /tooling
- The disclosure of current limits: /disclosure
