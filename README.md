# AIQT Guardrails™

**Rules your AI follows. Controls that catch what slips.**

AIQT Guardrails holds your assistant to one standard: **A**ccuracy, **I**ntegrity, **Q**uality, and
**T**rust. More than guidance: these principles outrank progress, speed, and cost. In chat, your assistant
answers to that standard. In your codebase, technical controls block, flag, or refuse the mistakes they are built to
catch. Open source.

The full pack and per-assistant setup guides live at [aiqt.ai](https://aiqt.ai).

## Two ways to run it

- **In a chat assistant** (Claude, ChatGPT, Gemini, or Copilot): add AIQT in the form each assistant supports (a skill, a Gem, an agent, or an uploaded instruction file), and every
  conversation answers to the standard. Available now.
- **In a coding assistant** (Claude Code, Codex, Cursor, or Copilot in the editor): the governance core is
  published to inspect and use as preview instructions today; the generated adapters, a guided install, a
  setup doctor, and the enforcement controls that catch a violation as it happens arrive with the 1.1.0
  developer release - in development.

## What is inside

- **The standard**: a full set of clear, single-behaviour rules across the four AIQT facets, plus a security
  floor covering confidentiality, integrity, availability, and privacy.
- **Technical controls, not just prose**: shipped gates and hooks that block, flag, or refuse an action that
  breaks the standard, so the rules hold even when they are not front of mind.
- **Generated adapters**: one source renders the Claude, AGENTS.md, Gemini, Copilot, and Cursor surfaces, so
  they cannot drift.
- **A standards crosswalk**: mappings from the rules to published control catalogues, for teams that report
  against a framework.

## Keep the always loaded instructions small

Claude Code reads some files into every session before you type anything: for this pack, the rule files
under `.claude/rules/` and the AIQT block in `CLAUDE.md`. A shipped gate measures that text and fails if
the pack's share grows.

Keep everything Claude Code loads at the start of a session under 120,000 characters in total. Claude Code's
own limit depends on the model; 120,000 is the lowest value it uses, so treat it as a conservative floor,
not a fixed limit. The gate prints two totals: PACK, the pack's own share, and SESSION, which adds the
rest of your `CLAUDE.md` and its imports. Compare SESSION, not PACK, plus your own files under
`~/.claude/`, such as `~/.claude/CLAUDE.md` and any rules there, against the floor when you check your setup.
The gate over-counts by design and never under-counts. It counts every HTML comment except a whole line
comment with blank lines around it. It follows every `@` that could name a file, even one in a code span
or a comment, so either total can be higher than what Claude Code loads, but never lower.

Do not load `AGENTS.md` into Claude Code as well, whether through the instructionFiles setting or an
import in `CLAUDE.md`. It carries the same rules as `.claude/rules/`, so every rule would load twice.

## Better with every release

AIQT catches the mistakes that align to the rules and process we have built so far, and improves with every
release. The pack is open source, so you can contribute your own guardrails today. Opt-in tools coming in an upcoming
release will let you automatically share the comments, improvements, and guardrails you have added, so the
community's contributions feed future versions.

## Verify your download

Each release ships a per-file manifest and a published root digest, and those same hashes are published
independently on posluns.dev. By validating them you can confirm that the files you downloaded are the ones
we intended you to have.

## Licence and trademarks

AIQT Guardrails is authored and maintained by Jeff Posluns.
See [LICENSE](LICENSE) and [NOTICE](NOTICE) for terms and third-party attribution. AIQT™ and AIQT
Guardrails™ are trademarks of Jeff Posluns (registration pending), and AIQT is a brand, not a legal entity.

## More

[Roadmap](ROADMAP.md) | [Changelog](CHANGELOG.md) | [Scope](SCOPE.md) | [Disclosure](DISCLOSURE.md) |
[System hardening](SYSTEM-HARDENING.md) | [aiqt.ai](https://aiqt.ai)

AIQT Guardrails by Jeff Posluns.
