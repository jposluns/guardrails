# Keep the always loaded instructions small

Claude Code reads some files into every session before you type anything: for this pack, the rule files
under `.claude/rules/` and the AIQT block in `CLAUDE.md`, plus `.claude/CLAUDE.md` if you have one. A shipped
gate, `tools/check_instruction_budget.py`, measures that text, with the header line Claude Code writes
before each file, and fails if the pack's share grows. The enforced figure lives in
`.aiqt/core/instruction-budget.toml` (`ratchet`), lowered as the pack shrinks and raised only with a
reviewed reason; the `ceiling` in that file is a separate reported figure for the pack's own share
alone, not enforced, and PACK currently exceeds it (its value predates the header counting that
raised PACK, and was not raised with it).

Treat 150,000 characters as this project's target for everything Claude Code loads at the start of a
session. One caveat: the loader of the pinned Claude Code build the gate models (2.1.287 in
`.aiqt/core/instruction-budget.toml`) carries its own 120,000-character check on loaded file
contents, a total limit below that figure (Claude Code behaviour, which can change, not a pack
guarantee), so budget to 120,000, not 150,000.
Today the shipped pack may exceed that figure: the budget gate allows it a PACK of up to the committed ratchet, 134,475 UTF-16 code units, the unit the budget gate counts.
The gate prints two totals: PACK, the
pack's own share, and SESSION, which adds the rest of your `CLAUDE.md` and its imports. Both count
`.claude/CLAUDE.md` and its imports, and a header for each loaded file with its resolved path relative to
the repository root (not the absolute part, which depends on where the repository sits). Compare SESSION,
not PACK, plus your own files under `~/.claude/`, such as `~/.claude/CLAUDE.md` and any rules there,
against the target when you check your setup; that Claude Code limit counts file contents only, so
the headers make SESSION read a little high.
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
