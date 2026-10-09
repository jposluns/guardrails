# OPFiles at a glance

Date: 2026-10-03 (UTC). The two-minute version of the OPFiles standard; the full specification
lives in OPF-SPEC.md beside this file.

## What it is

OPFiles is a neutral operational-files standard; AIQT layers its own requirements as one optional
`[profiles.aiqt]` profile, and a project can adopt OPFiles without adopting AIQT.

OPF standardizes a project's operational files: backlog, worklog, findings, decisions, blocks,
handoffs, references. Machines write versioned TOML in one store; humans read generated views. The
store is the source of truth; nothing hand-edits a generated file.

## The three release artifacts

- **`version.toml`**: the machine ledger of versions, dates, and release boundaries. It generates
  the root `VERSION` file. Numbers and digests only, never prose.
- **`worklog.toml`**: the detailed record, one entry per change, mutable-until-release and never
  deleted. An unreleased entry may be corrected in place; a released entry is immutable; entries
  are never rolled away or deleted. It generates `.working/WORKLOG.md`, and it survives every store
  move byte for byte.
- **`CHANGELOG.md`** (product repo root): the public story. Machine-drafted, human-curated
  summaries of the worklog, each declaring the version range it covers
  (`covers = "1.0.0..1.2.3"`). Old summaries roll up into range summaries; the details always
  survive in the worklog, so re-rolling or re-wording is safe. Two gates protect it: coverage (the
  ranges tile every released version, no gap, no overlap) and freeze (a published summary changes
  only through a recorded re-publish).

## The layout

```
.opf.toml             # pointer to wherever the store lives (default: right here)
CHANGELOG.md          # public, curated
VERSION               # generated from version.toml
.working/
  BACKLOG.md  TODO.md  FINDINGS.md  WORKLOG.md  ...   # generated views (UPPERCASE)
  toml/
    manifest.toml  counters.toml
    version.toml  worklog.toml
    backlog_item.index.toml  finding.index.toml  ...  # machine records (lowercase)
```

One rule of thumb carries the whole convention: lowercase files are machine source you change
through tooling or review; UPPERCASE files are generated or published deliverables you read and
never hand-edit (the curated `CHANGELOG.md` being the one you edit through its publish flow).

## Where the store lives

By default: `.working/` in your own repository, zero configuration, and your development process is
public along with your code. The store is always a git repository, wherever it lives; an untracked
store is a hard failure, not a warning.

And it can live anywhere: `opf migrate --store <target>` moves the whole store, git history and
worklog intact, to any git location (a private companion repository if you want a private process,
a local-only directory you back up yourself, a per-project `.working/` in a monorepo, any path or
git URL) while `CHANGELOG.md` and `VERSION` stay in your product repository, identical for every
reader whatever you chose. The target is just a path or git URL; `github:owner/repo` and
`gitlab:owner/repo` exist only as conveniences for creating a new remote.

## Getting started

1. Run `opf init` (until the tooling ships: create `.working/toml/` by hand with `manifest.toml`
   declaring `standard = "opf"` in its `[opf]` table, plus `counters.toml`,
   `version.toml`, `worklog.toml`, and the eleven baseline `<type>.index.toml` files (worklog is the
   exempt ledger listed above, not an index); specification
   sections 4 and 9). A `[profiles.aiqt]` table is optional and is ignored by base-only tooling.
   Anything already sitting in `.working/` takes a per-file disposition in the adoption plan
   (`opf adopt`; a fresh `opf init` refuses an existing `.working/` directory): keep it, migrate it
   for a later post-adoption import, move it, or retire it; nothing is absorbed or overwritten
   silently. `opf init` also refuses when the git history of HEAD's first-parent line shows a
   prior store (specification section 8.2: counters never restart, so record ids are never
   reissued). That scan follows the first-parent line only: a store that never reached a tree on
   HEAD's first-parent line (for example one created and deleted on a side branch, merged or not)
   is not detected. The scan also has a path limit: the scan checks the root's current path only
   (a store committed under another directory, for example before a rename, is not detected).
   `opf adopt` is the path for re-adopting any prior ancestry. When the scan does find a prior
   store, the refusal also prints a `git checkout` command for restoring the store paths the
   named commit holds, under `--no-replace-objects` (so a replacement ref cannot substitute
   other bytes for that commit's) and `--literal-pathspecs`; when the store was deleted across
   more than one commit, that commit holds only part of the store, so the first command is
   labelled as restoring only the store paths its commit holds, and an additional command
   restores the other named store path as its own commit's tree holds it. No printed command
   is ever labelled as restoring the whole store: each restores the named paths exactly as
   the named commit's tree holds them, and when that tree provably lacks a structural store
   file (a `manifest.toml` or a `counters.toml` deleted inside `.working` by an earlier
   commit), the refusal says the restore is PARTIAL and names each missing path. When a path in a
   command would hold a non-printable character, no command is printed at all: the refusal
   instead names the repository, commit, and paths in an inert escaped form, for a restore
   performed by hand. Every other value interpolated into init's output (the refusal's own
   path mentions and the JSON event lines included) is printed in the same inert escaped
   form, so no line init prints can carry a live shell substitution or quote for a POSIX
   shell to act on. A repository whose history the scan cannot trust as-is (a shallow clone, any entry
   at the legacy `.git/info/grafts` path, or a grafts path it cannot inspect) is refused as
   cannot-evaluate rather than initialized.
2. Commit the tree; confirm nothing under `.working/` is ignored.
3. Work records-first: append a worklog entry per change; keep the backlog, findings, and decisions
   in their typed files; regenerate views rather than editing them.
4. At release: record the release and its worklog span in `version.toml`, draft the summary from
   that span, curate it by hand, publish it into `CHANGELOG.md`, and record its freeze digest.
5. If you later want the store elsewhere: `opf migrate --store <target>`. The generated public
   deliverables (`CHANGELOG.md` and `VERSION`) are unchanged; migration re-points the committed
   pointer, updates the manifest's recorded sync target, and records the move as a worklog entry.

Scaffolding, validation, rendering, and migration tooling ships in later releases of the pack; until
then the files are simple enough to keep by hand, and a conformance claim is self-asserted and says
so. An implementation declares a conformance class: an upgrade-capable one carries older stores
forward within its disclosed limits, and a fresh-only one supports one current format and refuses by
name an older store or one in which its admission check detects an item on its closed legacy-state
list (OPF-SPEC section 16.1). That check searches only the files section 16.1 permits and the list
catches only what it lists, so legacy state the list omits, or that sits where the check does not
search, goes undetected. The reference tooling targets the upgrade-capable class, but its upgrade
into base 1.3.0 is still a target it does not yet perform.

The reference tooling requires Python 3.14 or newer.

---

# Flags: contradictions found and interpretive additions

Contradictions between the base spec and the final decisions, resolved in the revision above:

1. **Fixed topologies versus free location.** Base section 5 declared exactly two conforming
   topologies; the final decisions make location a free configuration with named patterns as mere
   examples. Revised section 5 supersedes the two-topology framing entirely.
2. **"No pointer to go stale."** Base section 4.3 argued the design's virtue was having no pointer
   at all; the final model introduces a committed pointer for the store repository while keeping
   pointerless observation for the machine subdirectory. The rationale is reworded (section 4.5)
   to scope the no-stale-pointer argument to the subdirectory and to state that the pointer, which
   does declare a location, is validated at every resolution and fails closed.
3. **Promotion out of scope versus first-class.** Base section 17 declared companion-store
   promotion mechanics unstandardized; the final decisions make render-into-product-repo and
   migration first-class. Sections 5.4, 5.8, and the revised residual in 17 replace that
   disclosure with a narrower one (the product repository's own review discipline).
4. **Path-root preamble.** The base resolved every path against the store repository root, which
   contradicts public deliverables living in the product repository under a relocated store. The
   preamble now names both roots.
5. **Consistency contract versus the in-repo default.** Applying the behind/ahead refusal
   literally to the in-repo default would refuse ordinary feature-branch work in the product
   repository. Section 5.7 scopes the pull-current/refuse axis to a store with a dedicated sync
   target and has the in-repo default inherit the product repository's branch-and-merge
   discipline (lease plus clean-state check). This is an interpretation of the decision text, not
   a literal transcription; flagged for architect ratification.

Additions the decisions imply but do not literally specify, flagged for ratification:

- The pointer filename `.opf.toml` and the uncommitted machine-local override `.opf.local.toml`
  (needed so a committed pointer stays publishable while local-only and private targets resolve
  per system).
- `opf sync` named as the explicit surfaced pull/push step the contract's "pulled current" and
  "sync back after" require.
- Manifest additions: `[store] sync_target` (so the actual remote can be validated against a
  recorded target before any push), `[providers.<name>]`, and `[unmanaged]`.
- A lease mechanism sketch (`lease.toml`, synced to the target) plus its propagation-window
  residual disclosure; the requirement is stated abstractly, the file is illustrative.
- The verified-restore-path requirement on `opf migrate` (destination verified by clone-back and
  digest reconciliation before the old location is retired).
- Integrity-layer roster grew three checks: pointer and sync-target agreement, unmanaged-path
  containment, and the tracked-store check now running against the resolved store.

Superseded by the OPFiles identity fold (this revision):

- The base discovery token is `opf`, carried in the `[opf]` base manifest table. This supersedes the
  earlier `aiqt-opf` token; the interim `devprocess` token and `[devprocess]` table are retired by the
  OPFiles rebrand, and a legacy store still carrying them is migrated forward by `opf upgrade`.
- The base storage field `layout_profile` is renamed to `layout`, reserving the word "profile" for
  the base/profile mechanism (`[profiles.<name>]`).

House-style conformance of both deliverables: no en or em dashes anywhere, Oxford -ize spellings,
sentence-case headings, paths absolute or resolved against a named fixed root, and no
adopter-internal repository, host, or system named (all examples synthetic).
