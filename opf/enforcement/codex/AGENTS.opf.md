# OPF working rules for Codex

Enforcement tier: instructions (advisory, not enforced).

This is the Codex member of the OPF enforcement pack (opf/enforcement/platforms.toml). Its destination
is the product root's AGENTS.md. Codex reads these rules as instructions only: no Codex hook or
setting in this pack stops a write to a listed path, because this repository documents and tests no
Codex mechanism that would. A Codex session can still edit every path below; following these rules is
up to the assistant.

Coexistence is an unresolved adoption prerequisite: the AIQT pack's tools/gen_agents.py regenerates AGENTS.md whole, so these contents cannot coexist with the AIQT instructions there today.

That generator writes AGENTS.md from the AIQT rule sources (.aiqt/core/rules/) alone: its output opens
with a GENERATED, do-not-hand-edit header, its --check mode reports a hand edit as drift, and the next
regeneration discards the edit. Replacing AGENTS.md with these contents alone would discard the AIQT
instructions instead. This pack provides no way to compose the two, and
tools/check_opf_enforce_platforms.py verifies none. In a product that runs that generator, installing
this member waits until the adopter settles that composition; only in a product that does not generate
AGENTS.md can these contents be added to it directly, as a planned instruction-surface edit.

## Working rules

- Treat the OPF store as the source of truth. Change records only through `opf record`, and the
  declared views only through `opf render`; never hand-edit store TOML or a generated view.
- Leave every path on the protected-path list untouched, through file edits and shell commands alike.
- If a change needs a listed path and no opf operation makes it, stop and ask the maintainer.

## Protected paths

This is the list the OPF Claude Code hook (opf/enforcement/claude/pretooluse_deny.py) guards on Claude
Code, rendered from that hook. On Codex it is advisory.

<!-- opf-protected-paths begin: rendered from opf/enforcement/claude/pretooluse_deny.py by tools/check_opf_enforce_platforms.py; change the hook, never this list -->

- `**/.working/**` (R1): every path with a `.working` component: OPF records, counters, ledgers, indexes, journals, staging and evidence. Change them only through `opf record` and `opf render`.
- `.working/imported/**` (R1): adoption and import evidence, written only by the opf writers.
- `.working/archive/adoption/<run-id>/**` (R2): the adoption archive of preserved originals. Write nothing here.
- Frozen old files (R3): every `[[sources]]` path whose disposition is `migrate` or `retire` in `.working/imported/adoption/<run-id>/plan.toml` (format `opf.adoption.plan/v2`), relative to the product root. Leave it byte-identical until its retirement is recorded.
- Declared views (R4): every `[views.<name>]` `target` in the machine-store manifest `.working/<machine>/manifest.toml` whose `[opf]` table carries `standard = "opf"`, relative to the product root. Change them only through `opf render`.
- `opf/enforcement/**` (R8): the OPF enforcement pack, at its installed location.
- `opf/tools/**` (R8): the opf writer and its tools, at their installed location.
- `.claude/settings.json` (R8): the Claude Code hook registration of the product root.
- `.claude/settings.local.json` (R8): the Claude Code hook registration of the product root.

On Claude Code the registered deny hook refuses a direct write to each path on this list. tools/check_opf_enforce_platforms.py calls only that hook's file-tool rule, for a Write at one throwaway fixture layout, never the other file tools, the hook's entry point or its deny output; opf/tools/check_opf_doctor.py launches the hook against its own denial vectors. On every other platform nothing refuses such a write, and this list is advisory.

Not on the list until the import writer ships: a leaf directly inside the machine store directory `.working/<machine>/` whose name matches `\A(worklog\.imported\.toml|[A-Za-z0-9_-]+\.imported\.index\.toml)\Z`.

<!-- opf-protected-paths end -->

## Residuals on this platform

- No verified platform guard (`unverified-platform-denial`): nothing on Codex stops a write to a
  listed path.
- Shell or interpreter wrapping (`shell-or-interpreter-wrapping`): a command can reach a listed path
  without naming it.
- Same-user tampering (`same-user-tampering`): anyone with the user's access can edit a listed path or
  this file.
- The pack's pre-commit and CI members run `opf doctor` and `opf render --check` over what is
  committed. They report some direct edits (an altered record body, a drifted view), not every one,
  and only where they are installed.
