# Keep the always loaded instructions small

Claude Code reads some files into every session before you type anything: for this pack, the rule files
under `.claude/rules/` and the AIQT block in `CLAUDE.md`, plus `.claude/CLAUDE.md` if you have one. A shipped
gate, `tools/check_instruction_budget.py`, measures that text, with the header line Claude Code writes
before each file, and fails if the pack's share grows. The enforced figure lives in
`.aiqt/core/instruction-budget.toml` (`ratchet`), lowered as the pack shrinks except for a deliberate
margin recorded there, and raised only with a reviewed reason; the `ceiling` in that file is a
separate reported figure for the pack's own share alone, not enforced, and PACK currently exceeds it
(its value predates the header counting that raised PACK, and was not raised with it).

The comment above `ratchet` in that file keeps a dated history of the ratchet, and the gate's
`--change-shape` mode holds it to an append-only rule: every existing history line stays exactly as
written, and new lines go only directly above the `#   ceiling` comment line, as one new entry per
raise, each opening with a line reading `Raised OLD -> NEW on YYYY-MM-DD`. A push or merge train
carrying several raises adds one entry for each, compared entry by entry: the first OLD is the base
value, each later OLD is the NEW of the entry before it, and the last NEW is the new ratchet. A change
that leaves the ratchet where it is (held, not raised) or lowers it may add no history lines at all, so
it cannot add a history line of its own. Write a held, not raised, record, and a late record of an
earlier change that landed without one, into the next raise entry instead: first that entry's
`Raised OLD -> NEW on YYYY-MM-DD` line, then the held or late record as more lines of the same entry,
each written like the other entry lines (`#`, exactly 24 spaces, then printable ASCII text with no
trailing blank). For example, a line opening `Held at N on YYYY-MM-DD, not raised, when` or
`Late record of the YYYY-MM-DD change, written on YYYY-MM-DD: held at N` continues the entry above it.
No line of an entry after its first may begin with the word "raised" in any letter case, since the gate
reads such a line as the start of a new entry; rewrap the text so that word falls inside a line. An
entry's date may be no earlier than the latest date on a `Raised OLD -> NEW on YYYY-MM-DD` line already
in the history, and no later than the day after the checking day (UTC); a date in any other line, such
as one in a held or late record, sets no bound for later entries. The held and late records written on 2026-11-18 above the `Raised 136499 -> 137546` line predate this rule
and stay as they are; do not copy that layout. `tools/check_instruction_budget.py` states the full
entry grammar.

Treat 150,000 characters as this project's target for everything Claude Code loads at the start of a
session. One caveat: the loader of the pinned Claude Code build the gate models (2.1.287 in
`.aiqt/core/instruction-budget.toml`) carries its own 120,000-character check on loaded file
contents, a total limit below that figure (Claude Code behaviour, which can change, not a pack
guarantee), so budget to 120,000, not 150,000.
Today the shipped pack may exceed that figure: the budget gate allows it a PACK of up to the committed ratchet, 137,546 UTF-16 code units, the unit the budget gate counts.
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
