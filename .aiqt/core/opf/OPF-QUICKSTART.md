# OPF at a glance

Date: 2026-09-04 (UTC). The two-minute version of the OPF standard; the full specification lives in
OPF-SPEC.md beside this file.

## What it is

OPF standardizes a project's operational files: backlog, worklog, findings, decisions, blocks,
handoffs, references. Machines write versioned TOML in one store; humans read generated views. The
store is the source of truth; nothing hand-edits a generated file.

## The three release artifacts

- **`version.toml`**: the machine ledger of versions, dates, and release boundaries. It generates
  the root `VERSION` file. Numbers and digests only, never prose.
- **`worklog.toml`**: the detailed record, one entry per change, append-only forever. Entries are
  never rolled away or deleted; it generates `.working/WORKLOG.md`, and it survives every store
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
   declaring `standard = "aiqt-opf"`, plus `counters.toml`, `version.toml`, `worklog.toml`, and
   the nine baseline `<type>.index.toml` files; specification sections 4 and 9). Anything already
   sitting in `.working/` is detected and you choose, per file: keep it, import it into the store,
   or move it; nothing is absorbed or overwritten silently.
2. Commit the tree; confirm nothing under `.working/` is ignored.
3. Work records-first: append a worklog entry per change; keep the backlog, findings, and decisions
   in their typed files; regenerate views rather than editing them.
4. At release: record the release and its worklog span in `version.toml`, draft the summary from
   that span, curate it by hand, publish it into `CHANGELOG.md`, and record its freeze digest.
5. If you later want the store elsewhere: `opf migrate --store <target>`. Nothing else changes.

Scaffolding, validation, rendering, and migration tooling ships in later releases of the pack;
until then the files are simple enough to keep by hand, and a conformance claim is self-asserted
and says so.

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

House-style conformance of both deliverables: no en or em dashes anywhere, Oxford -ize spellings,
sentence-case headings, paths absolute or resolved against a named fixed root, and no
adopter-internal repository, host, or system named (all examples synthetic).
