# AIQT Guardrails™

**Rules your AI follows. Controls that catch what slips.**

AIQT Guardrails holds your AI assistant to one standard: **A**ccuracy, **I**ntegrity, **Q**uality, and
**T**rust. More than guidance: these principles outrank progress, speed, and cost. In chat, your
assistant answers to the standard. In your codebase, technical controls block, flag, or refuse the
mistakes they are built to catch. Open source, and built to be read.

**132 rules. 108 gates. 26 hooks. Mapped to 17 frameworks.**

The full pack and per-assistant setup guides live at [aiqt.ai](https://aiqt.ai).

## 01. Two ways to run it.

| Where | What you get | Tag |
| --- | --- | --- |
| **In a chat assistant** | Add AIQT to Claude, ChatGPT, Gemini, or Copilot in the form each assistant supports: a skill, a Gem, an agent, or an uploaded instruction file. Every conversation answers to the standard. | Available |
| **In a coding assistant** | For Claude Code, Codex, Cursor, or Copilot in the editor, the governance core is published to inspect and use as preview instructions today. The generated adapters, a guided install, a setup doctor, and the enforcement controls that catch a violation as it happens arrive with the 1.1.0 developer release, in development. | Preview |

## 02. What is inside.

| Piece | In one breath | Tag |
| --- | --- | --- |
| **The standard** | 132 clear, single-behaviour rules across the four AIQT facets, plus a security floor covering confidentiality, integrity, availability, and privacy. | Rules |
| **Technical controls** | 108 shipped gates and 26 hooks, not just prose: they block, flag, or refuse an action that breaks the standard, so the rules hold even when they are not front of mind. | Controls |
| **Generated adapters** | One source renders the Claude, AGENTS.md, Gemini, Copilot, and Cursor surfaces, so they cannot drift. | Generated |
| **A standards crosswalk** | Mappings from the rules to 17 pinned external control catalogues, for teams that report against a framework. | Mappings |

## 03. Get started.

- **In chat**: pick your assistant at [aiqt.ai](https://aiqt.ai), download the skill or instruction
  file, and add it the way your assistant expects. Using AIQT in a chat assistant needs no Python.
- **In a repository**: clone this repository and read the governance core under `.aiqt/core/`; the
  rules are plain Markdown, and every control under `tools/` is a standalone script. The pack's
  tools and hooks require Python 3.14 or newer.

## 04. Keep what loads at session start small.

Claude Code reads some files into every session before you type anything. A shipped gate measures the
pack's share of that always loaded text and fails if it grows. Aim for one target ceiling: keep
everything Claude Code loads at the start of a session under 150,000 characters in total. On a
200k-context model, Claude Code's own total limit is 120,000 characters; that is observed Claude Code
behaviour, not a pack guarantee. The counting rules, the totals the gate prints, and what not to load
twice are in [Keep the always loaded instructions small](INSTRUCTION-BUDGET.md).

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
