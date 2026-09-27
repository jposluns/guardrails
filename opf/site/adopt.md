# Adopt OPFiles

This is the machine-readable adoption guide. The human-readable page is at /adopt.

OPFiles is an operational-files standard: a `.working/` store of lowercase TOML sources, generated uppercase views, and a drift check for comparing declared views with their sources. Adopting with an AI development assistant is the recommended path; adopting by hand is the fallback for when you cannot. Both paths target the same store format; configure and verify the required checks separately.

## What an OPFiles store is

The store lives in your product repository. Lowercase files under `.working/toml/` are machine source you change through tooling or review. Uppercase files at the `.working/` top level are generated views you read and never hand-edit. A committed pointer `.opf.toml` at the repository root names where the store lives, and the curated `CHANGELOG.md` sits at the repository root. A generated root `VERSION` is required whenever releases exist. The initial scaffold omits its view declaration; add that declaration when recording the first release so the reference renderer can produce the file. Doctor checks `VERSION` whenever releases exist, even if its view is undeclared.

## Before you apply: verify the artifact digest

Before scaffolding or applying anything to your project, obtain the pack's artifact digest and compare it against the published hashes at posluns.dev/hashes.txt. Proceed only on a match; refuse if the digest differs or the reference evidence is unavailable. This is a required adopter action on both paths below, not an automatic step that something else performs for you.

## The recommended path: assistant-guided adoption

Ask your AI development assistant to read the standard, inspect your project, and propose an adoption plan naming what it would create, import, or retire. Review the plan before authorizing changes, then inspect the scaffolded store, the validation results, and any records-first instructions added to your project. If adopting AIQT, include OPFiles in that plan.

## Adopting by hand

1. Create the machine store at `.working/toml/`: `manifest.toml` (the control document, declaring `standard = "opf"`, the base `spec_version`, the storage `layout`, and the enforcement `posture`), `counters.toml` (the per-namespace ID high-water marks), `version.toml` (the version and release ledger, numbers and digests only), `worklog.toml` (the durable operational record: one entry per change, correctable before its span is released, frozen afterward, never deleted), and the eleven baseline typed indexes as `<type>.index.toml`, empty to start.
2. Write the committed pointer `.opf.toml` at your product repository root, so the store resolves from a stable location.
3. Work records-first: treat the store as the source of truth, append a worklog entry per change, keep the backlog, findings, and decisions in their typed files, and regenerate the views rather than editing them.
4. Configure the drift check as a required commit or CI check: it re-renders the declared views from their sources and fails on byte differences. The reference tooling reports per-record view drift as cannot-evaluate.

Conformance you assert by hand is self-asserted until the reference validator has checked it, and you should say so wherever you claim it.

## More

- The standard: /standard
- Reference tooling: /tooling
- The disclosure of current limits: /disclosure
