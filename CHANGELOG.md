# Changelog: AIQT Guardrails™

The user-facing changes for each public release.

## 1.0.5: The chat-assistant skill (2026-09-18)

- AIQT 1.0.5 ships the chat assistant: the AIQT governance core packaged for a chat assistant, as a Skill you upload to Claude or a portable instruction file for other assistants (ChatGPT, Copilot, Gemini). It guides the assistant toward the AIQT standard: claims matched to their sources, checks kept at full strength, and "finished" verified before it is said. The evidence page shows what has been tested on each platform.
- Includes the portable AIQT governance core and a curated security-rule subset for chat use.

## Unreleased (toward 1.1.1)

- AGENTS.md now places the rules listed in .aiqt/core/must-deliver.toml directly after the apex rule, ahead of every other rule, so growth in an unlisted rule can no longer push a listed rule out of the first 32768 bytes. The current AGENTS.md is byte-identical (the listed rules already came first), and GEMINI.md and the Copilot instructions keep AIQT priority order. tools/gen_agents.py now stops with exit 2, writing nothing, when that list is absent, malformed or names a rule that is not in the corpus, and the adopter conformance suite judges AGENTS.md in the same order and, like the generator, reports an installed AGENTS.md with no such list as MALFORMED (exit 2). Release classification, as tools/check_release_delta.py reads it: the renderer change has no output byte change (byte-only); the new tools/_must_deliver.py is an added pack path; and the conformance change, an exit change in a tool outside every renderer declaration, touches no surface that gate diffs (clause text, the path set, ownership, the order record, renderer declarations), so it adds no delta of its own.
