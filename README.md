# AIQT Guardrails™

**Rules your AI follows. Controls that catch what slips.**

AIQT Guardrails holds your AI assistant to one standard: **A**ccuracy, **I**ntegrity, **Q**uality, and
**T**rust. More than guidance: these principles outrank progress, speed, and cost. In chat, the standard
guides your assistant. In this repository, CI gates check each pull request targeting main and each
push to main against the rules the gates cite, a subset of the standard. Open source, and built to
be read.

<!-- COUNTS-PROOF:BEGIN (generated) -->
**132 rules. 116 CI gates that check this repository. 28 Claude Code hooks shipped as an opt-in plugin. Mapped to 17 frameworks.**
<!-- COUNTS-PROOF:END -->

The full pack and per-assistant setup guides live at [aiqt.ai](https://aiqt.ai).

## 01. Two ways to run it.

| Where | What you get | Tag |
| --- | --- | --- |
| **In a chat assistant** | Add AIQT to Claude, ChatGPT, Gemini, or Copilot in the form each assistant supports: a skill, a Gem, an agent, or an uploaded instruction file. The standard guides every conversation: instructions are context the assistant follows, not an enforcement mechanism. | Available |
| **In a coding assistant** | For Claude Code, Codex, Cursor, or Copilot in the editor, the governance core is published to inspect and use as preview instructions today, and the repository ships its Claude Code enforcement hooks as a generated plugin. The generated adapters, a guided install, and a setup doctor arrive with the 1.1.0 developer release, in development. | Preview |

## 02. What is inside.

<!-- COUNTS-TABLE:BEGIN (generated) -->
| Piece | In one breath | Tag |
| --- | --- | --- |
| **The standard** | 132 clear, single-behaviour rules across the four AIQT facets, plus a security floor covering confidentiality, integrity, availability, and privacy. | Rules |
| **Technical controls** | 116 CI gates that check this repository and 28 Claude Code hooks shipped as an opt-in plugin (21 blocking, 7 warning). Today 38 of the 132 rules are linked to at least one shipped gate or hook; [ENFORCEMENT.md](ENFORCEMENT.md) lists each rule's status. | Controls |
| **Generated adapters** | The Claude, AGENTS.md, Gemini, Copilot, and Cursor surfaces are generated from one source and drift-checked in CI. | Generated |
| **A standards crosswalk** | Mappings from the rules to 17 pinned external control catalogues, for teams that report against a framework. | Mappings |
<!-- COUNTS-TABLE:END -->

## 03. Get started.

- **In chat**: pick your assistant at [aiqt.ai](https://aiqt.ai), download the skill or instruction
  file, and add it the way your assistant expects. Using AIQT in a chat assistant needs no Python.
- **In a repository**: clone this repository and read the governance core under `.aiqt/core/`; the
  rules are plain Markdown, and the controls are Python scripts under `tools/` that you can read and
  run. The pack's tools and hooks require Python 3.14 or newer.

## 04. Keep what loads at session start small.

Claude Code reads some files into every session before you type anything. A shipped gate measures the
pack's share of that always loaded text and fails if it grows. This project's target is to keep
everything Claude Code loads at the start of a session under 150,000 characters in total. One caveat:
the loader of the pinned Claude Code build the gate models (2.1.287, pinned with its binary hash in
`.aiqt/core/instruction-budget.toml`) carries its own 120,000-character check on loaded file contents,
a total limit below that figure (Claude Code behaviour, which can change, not a pack guarantee), so
budget to 120,000. Today the shipped pack may exceed that figure: the budget gate allows it a PACK of up to the committed ratchet, 134,489 UTF-16 code units, the unit the budget gate counts.
The counting rules, the totals the gate prints, and what not to load twice are
in [Keep the always loaded instructions small](INSTRUCTION-BUDGET.md).

## 05. Verify your download.

Each release ships a per-file manifest and a published root digest, and those same hashes are published
independently on posluns.dev. By validating them you can confirm that the files you downloaded are the
ones we intended you to have.

## 06. Better with every release.

AIQT catches the mistakes that align to the rules and process we have built so far, and improves with
every release. The pack is open source, so you can contribute your own guardrails today. Opt-in tools
coming in an upcoming release will let you automatically share the comments, improvements, and
guardrails you have added, so the community's contributions feed future versions.

## 07. Licence and trademarks.

AIQT Guardrails is authored and maintained by Jeff Posluns.
See [LICENSE](LICENSE) and [NOTICE](NOTICE) for terms and third-party attribution. AIQT™ and AIQT
Guardrails™ are trademarks of Jeff Posluns (registration pending), and AIQT is a brand, not a legal
entity.

## More.

[Roadmap](ROADMAP.md) | [Changelog](CHANGELOG.md) | [Scope](SCOPE.md) | [Disclosure](DISCLOSURE.md) |
[System hardening](SYSTEM-HARDENING.md) | [Instruction budget](INSTRUCTION-BUDGET.md) |
[aiqt.ai](https://aiqt.ai)

AIQT Guardrails by Jeff Posluns.
