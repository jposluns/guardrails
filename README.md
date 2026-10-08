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

## Requirements

The pack's tools and hooks require Python 3.14 or newer. Using AIQT in a chat assistant needs no
Python.

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
under `.claude/rules/` and the AIQT block in `CLAUDE.md`, plus `.claude/CLAUDE.md` if you have one. A shipped
gate measures that text, with the header line Claude Code writes before each file, and fails if the pack's
share grows.

Keep everything Claude Code loads at the start of a session under 120,000 characters in total. Claude Code's
own limit depends on the model; 120,000 is the lowest value it uses, so treat it as a conservative floor,
not a fixed limit. The gate prints two totals: PACK, the pack's own share, and SESSION, which adds the
rest of your `CLAUDE.md` and its imports. Both count `.claude/CLAUDE.md` and its imports, and a header for
each loaded file with its resolved path relative to the repository root (not the absolute part, which
depends on where the repository sits). Compare SESSION, not PACK, plus your own files under `~/.claude/`,
such as `~/.claude/CLAUDE.md` and any rules there, against the floor when you check your setup; Claude
Code's own limit counts file contents only, so the headers make SESSION read a little high.
The gate reads only an enumerated grammar and exits 2, naming the file and line, on anything outside it: a
control, format, or Unicode whitespace character other than tab and a line ending; frontmatter that is not
plain ASCII keys and one-line string values; a plain `paths:` value with no `/`, `*`, `?`, `[`, `{` or
letter-dot-letter run (such as `src` or `Makefile`), which the gate cannot prove a YAML reader reads as a
string (an over-refusal for values such as these), so quote it; an `@` import that is not plain ASCII, holds a `..`, or
passes through a symlink; an import target it cannot read; a case variant of `CLAUDE.md`, `.claude/CLAUDE.md`,
or `.claude/rules/` (such as `.claude/claude.md`), which a case-insensitive file system loads; and an
HTML comment on the same line as an `@` that starts an import, since Claude Code removes the comment and
can join an import path across it. An `@` inside a word, such as an email address in a comment, is exempt
from that comment check, except where it forms a Windows 8.3 short name import such as `name@HOST~1`,
which the gate refuses though Claude Code does not import it (a disclosed over-refusal).
Within that grammar it counts every HTML comment except a whole line comment with blank lines around it.
It follows each `@` it reads as possibly naming a file, even one in a code span, and the imports of a rule
file scoped with `paths:`, which Claude Code loads in every session. Either total can be higher than what
Claude Code loads. The gate models the pinned Claude Code build's loader; it does not run Claude Code, and
its model is not a proof.

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
