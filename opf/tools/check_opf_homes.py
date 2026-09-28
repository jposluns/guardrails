#!/usr/bin/env python3
"""Homes contract, control-boundary and gitignore drift gate (writers still use legacy homes).

Checks documented topology against the constructor authority. Text checks protect the planned
contract, including the 1.3.0 adoption/import target, not runtime conformance to either target. No store is mutated or required to install a block.
The registered pins tile each registered section completely (the self-test proves the
concatenation equality per section), so deleting any sentence in a tiled section turns the gate
red. Residual: the default gate's pins are substring checks over normalized text, so added
text, a pinned sentence neutralized by appended text included, stays green under the default
entry alone; the self-test's per-section tiling equality turns red on any addition inside a
tiled section. The live residuals are additions to untiled sections (for example 8.7, 10, 13
and 16), a default-gate run without the self-test, and an addition landed together with a
matching registry edit, which review of registry changes catches.

The default entry also runs a section 2 keyword lint over every registered pin: a pin without
MUST, MUST NOT, SHOULD or MAY is red unless the registry marks it descriptive (_D) or carries it
on the explicit, temporary exemption list of pending decision PD-OPF-OCCUPIED-VIEW-PRESERVE-FIRST
(_P). Residual: the lint reads the registry marker, not the meaning, so a requirement wrongly
marked descriptive stays green; review of registry changes catches that.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store as store  # noqa: E402

SPEC = Path(__file__).resolve().parents[1] / "spec" / "OPF-SPEC.md"


class _Descriptive(str):
    """Pin marker: descriptive by intent, so the section 2 keyword lint accepts it without MUST,
    MUST NOT, SHOULD or MAY. It covers definitions, format, layout and field descriptions,
    examples, rationale, status and activation notes, disclosed residuals, restatements of a
    keyword requirement stated elsewhere, and enumerated items whose obligation a keyword lead-in
    carries. A marked pin that carries a keyword is itself a finding."""
    __slots__ = ()


class _PendingOccupiedView(str):
    """Pin marker: TEMPORARY keyword-lint exemption for pending decision
    PD-OPF-OCCUPIED-VIEW-PRESERVE-FIRST. It covers only keywordless sentences inside the on-hold
    occupied-destination machinery (the section 14.2 matrix, deferral, cutover, bootstrap
    relocation, the section 11 re-check exception and its section 8.8 exit interplay), whose
    wording waits for that decision. A marked pin that carries a keyword or sits outside the
    pending sections is a finding, so each exemption leaves with the rewrite that settles it."""
    __slots__ = ()


_D = _Descriptive
_P = _PendingOccupiedView
_PENDING_DECISION = "PD-OPF-OCCUPIED-VIEW-PRESERVE-FIRST"
_PENDING_SECTIONS = ("8.8", "9.2", "11", "14.1", "14.2")
# Section 2 keywords, case-sensitive and whole-word: a lowercase "must" is not a keyword.
_KEYWORD = re.compile(r"\b(?:MUST|SHOULD|MAY)\b")
# Scope each wording check to its operative section, never a whole-document negative scan.
# Ordered whole-section tiling: each tuple, space-joined, equals exactly its section's
# normalized body (the self-test proves that equality), so deleting any sentence in a
# registered section breaks a pin and turns the gate red. Every pin is also keyword-linted
# (keyword_findings): _D marks a descriptive pin, _P a temporary pending-decision exemption.
_CONTRACT = {
    "0": (
        _D('# OPFiles: the operational-files standard Formal name: AIQT Development Operational Standard.'),
        _D('Public brand: OPFiles (opfiles.ai).'),
        _D('Base discovery token: opf.'),
        _D('Status: draft (specification only; schemas and the reference tooling, the scaffolder opf init, the adopter opf adopt, the post-adoption importer opf import, the validator opf doctor, the renderer opf render, the relocator opf migrate, the synchronizer opf sync, the schema-upgrader opf upgrade, the absorber opf absorb, and the record author opf record, ship in later releases).'),
        _D('Date: 2026-09-27 (UTC).'),
        _D('OPFiles is a neutral, self-contained operational-files standard published under the Apache License 2.0 (except vendored third-party material, which remains under its own terms).'),
        _D('AIQT and AIQT Guardrails are trademarks (registration pending); AIQT is a brand, not a legal entity, and the standard is authored and maintained by its lead maintainer.'),
        _D('A project conforms to OPFiles with this specification and its own checks; the AIQT Guardrails pack is the reference enforcement suite and a consumer of the standard, not its definition.'),
        _D('AIQT-specific requirements are layered as one optional profile, [profiles.aiqt] (section 9), and a base adopter need not adopt AIQT.'),
        _D('Unless a path is written from /, a path under .working/ in this document is relative to the root of the repository that tracks the store (the "store repository"), and every other path (the pointer, CHANGELOG.md, VERSION) is relative to the root of the product repository.'),
        _D('The two roots coincide under the default configuration (section 4.1).'),
    ),
    "1": (
        _D("OPF (operational files) standardizes how a project keeps its operational records: the backlog, the completion receipts, the worklog, findings, decisions, blocks, handoffs, and references that a records-first operational discipline requires (the discipline AIQT's records-first rule is one implementation of)."),
        _D('It defines one machine-readable store of versioned TOML under .working/toml/, a set of generated human-readable views above it, and three release artifacts (a version ledger, a durable worklog, and a curated public changelog) with the gates that keep all of them honest.'),
        _D('The store is always a git repository, wherever it lives.'),
        _D('Its location is a free, migratable configuration resolved through a committed pointer, never an architectural commitment: it defaults to .working/ in the product repository and can be relocated at any time to any location a target can name, with history and the durable worklog preserved (section 5).'),
        _D('OPF specifies formats, layout, naming, lifecycle, and enforcement posture, and names the standard command vocabulary of the reference tooling (opf init, opf adopt, opf import, opf doctor, opf render, opf migrate, opf sync, opf upgrade, opf absorb, opf record).'),
        _D('It does not specify tooling internals; a reference implementation follows in later releases of the AIQT Guardrails reference suite.'),
        _D('A project can conform to this specification with hand-maintained files and its own checks.'),
    ),
    "2": (
        'MUST, MUST NOT, SHOULD, and MAY are used as in common standards practice: MUST is an absolute requirement of conformance, SHOULD is a strong recommendation departed from only for recorded reason, MAY is genuinely optional.',
        _D('Statements without these keywords are descriptive.'),
    ),
    "4.2": (
        _D('Base spec 1.3.0 defines adoption and the separate imported series on homes 1.'),
        _D('The 1.3.0 requirements in sections 4.2, 6.1, 8, 9.2, 11, 12, and 14 are a target contract; they do not claim that the reference tooling has activated adoption, imported-series validation, or the import writer.'),
        'Activation MUST include the tested upgrade in section 9.2 and deterministic doctor coverage before a writer accepts the new format.',
        _D('Homes 2 is a separate, later activation.'),
        _D('The homes-2 contract below is for spec_version = "2.0.0" and [opf].homes = 2.'),
        _D('The current reference tooling reserves these names and implements their homes-2 boundary checks.'),
        _D('It still supports 1.2.0 and initializes legacy homes (generation 1, with no homes key); writers retain their legacy paths until homes 2 is activated, which requires the homes migration (opf upgrade) to be available.'),
        _D('The section 9 manifest example describes the 1.3.0 target on legacy homes.'),
        _D('The homes-2 requirements in sections 4.2, 9.2, 12, 14.1, 14.2, and 15 describe the target contract, not an activated runtime guarantee.'),
        _D('<product repository root>/ .opf.toml # committed store pointer (section 4.3) CHANGELOG.md # curated public changelog (deliverable; section 6.3) VERSION # deterministic render from version.toml (deliverable) .working/ # the store; present here under the default in-repo configuration <store repository root>/ # the product repository itself, by default .working/ README.md # ownership and regeneration note (deliverable) WORKLOG.md # deterministic render of worklog.toml VERSION.md # optional human view of version.toml TODO.md BACKLOG.md PIPELINE.md DONE.md FINDINGS.md DECISIONS.md BLOCKS.md HANDOFF.md REFERENCES.md CONTRIBUTIONS.md DECISIONS.toml # machine projection (deterministic; section 10.5) <TYPE>-INDEX.md ...'),
        _D('# optional 1:1 index mirrors (section 10.1) IMPORT-REPORT.md # legacy import report (pre-1.3.0 runs only; section 14.1) toml/ manifest.toml # store manifest and discovery marker (section 9) counters.toml # per-namespace ID high-water marks (section 8.2) version.toml # version and release ledger (section 6.1) worklog.toml # durable operational record (section 6.2) <type>.imported.index.toml # separate imported records of each enabled type (section 8.3) worklog.imported.toml # imported worklog, on its own ID number line lease.toml # single-writer lease, present only while held (section 5.7) init.toml # bootstrap provenance of a coupled init (section 9.2), if present backlog_item.index.toml # typed record files (section 8) done.index.toml finding.index.toml pending_decision.index.toml autonomous_decision.index.toml block.index.toml handoff.index.toml reference.index.toml contribution.index.toml maintainer_decision.index.toml preference_pattern.index.toml archive/ 2026/ archive.toml # enumerates rotated IDs and spans (section 12) done.index.toml worklog.toml archive/ moved/<source-path> # default Move destinations (section 14.2) adoption/<run-id>/ # adoption retire preimages (section 14.2) imported/<kind>/<run-id>/ # durable originals, inventories, approvals and receipts (section 14) staging/<kind>/<run-id>/ # short-lived staging, evidence-gated reclamation (section 14.1) journals/<kind>/ # reserved recovery state, never a view or ordinary op target journal/ # crash-durable frames runs/<run-id>/transaction.toml # gate-readable projections allocations/<run-id>.toml # irrevocable ID reservations (section 8.2) When the store has been relocated, the .working/ tree lives at the store repository root exactly as drawn, and the product repository keeps only the pointer and the public deliverables.'),
        _D('The per-record layout (section 9) additionally places one file per record under .working/toml/<type>/, with each <type>.index.toml acting as the registry.'),
        _D('The imported files are registered managed leaves beside the clean-series files, using the same enabled-type roster; worklog uses worklog.imported.toml instead of an imported index.'),
        'Their manifest, emitter, upgrade and containment registrations MUST agree.',
        'A 1.3.0 opf init and the section 9.2 upgrade MUST create the imported leaves for every enabled type, create-only and empty; enabling a further type or module later MUST create its imported leaf in the same act as its clean index.',
        _D('They are machine records, distinct from the original-source evidence under .working/imported/.'),
        'The first imported-series release MUST keep these files inline in either store layout and MUST NOT provide views over imported data; assistants read the TOML.',
        _D('Historical releases remain in version.toml (section 14.3); there is no version.imported.toml or CHANGELOG.toml.'),
        _D('The reserved children archive/, imported/, staging/, and journals/ are store-tree control area, neither machine-store records nor adopter content; they relocate with the machine store.'),
        'In homes 2, OPF MUST NOT write state outside .working/ except the operation-lock and coupled-init substrate in the git common directory, and MUST NOT write any under .aiqt/.',
        'The record archive MUST remain inside the discovered machine store, and the example name toml MUST NOT be hardcoded.',
        _D('The closed kind vocabulary is import, ingest, adoption, layout, preview.'),
        _D('Import and ingest run IDs use imp-<YYYYMMDD>T<HHMMSS>Z-<hash16>; adoption uses adopt- with that suffix.'),
        _D('Layout and preview reserve layout- and preview- with the same suffix.'),
        _D('Here hash16 is 16 lowercase hexadecimal characters; constructors check lexical shape, not calendar validity or filesystem safety.'),
        'File operands MUST use canonical contained relative paths: no empty, dot, or parent components, absolute/drive/backslash forms, control characters, or line separators.',
        'Homes-2 init and upgrade MUST render this managed block into .working/.gitignore from the topology constants; the reference tooling provides the renderer and drift gate but installs no block: gitignore # >>> opf-managed >>> /journals/ /staging/ # <<< opf-managed <<< Durable imported/ and archive/ evidence MUST stay tracked.',
        'Installation MUST inspect effective ignore rules and the index first; tracked staging or journals MUST require an explicit reviewed untracking change, never silent index mutation.',
        _D('The block travels with the store and joins the indexed-ignore candidate set.'),
        _D('Gitignore is not access control: git add -f can stage ignored state.'),
        'Journals are machine-local even after completion; a clone without them cannot recover those transactions, and requested recovery MUST fail closed on a missing journal.',
        _D('Containment and doctor exclude journals and make no recovery claim; a rogue file there is outside their coverage.'),
        _D("Under pre-1.3.0 tooling, every store within that tooling's own version ceiling keeps its legacy grading: the homes-2 names are ordinary store paths there, graded, detected, and dispositioned exactly as before, and the pre-1.3.0 doctor's check roster and residuals are unchanged."),
        _D('Tooling that carries the section 9.2 ceiling refuses an above-ceiling declaration as a fail-closed INVALID finding; tooling released before that ceiling grades such a store as legacy instead, a disclosed residual of section 9.2.'),
        'Activated 1.3.0 tooling MUST register on homes 1 the imported managed leaves, the adoption archive .working/archive/adoption/<run-id>/, the Move destination .working/archive/moved/, and the evidence bundles .working/imported/<kind>/<run-id>/, all recognized by containment as OPF control area, and MUST add the four section 8.3 imported-series checks to the doctor roster.',
        'C-EVIDENCE-ENUM MUST NOT run or read anything until homes 2 is activated; on homes 1 the section 14.1 completion checks MUST carry the evidence digest verification themselves.',
        _D('The boundary below applies only to a store that declares both homes = 2 and spec_version = "2.0.0" once the tooling activates that generation.'),
        _D('Each evidence bundle .working/imported/<kind>/<run-id>/ carries its own inventories at its root: inventory.toml, plus a new inventory-<phase>.toml for each later phase, where <phase> is a lowercase letter followed by up to 31 lowercase letters or digits.'),
        _D('Each holds exactly format = "opf.evidence.inventory/v1" and a file array whose rows have exactly path (a canonical store-relative file path spelled from .working/), size (a nonnegative integer), and sha256 (64 lowercase hex digits).'),
        _D('A row may name a member of its own bundle other than a bundle-root inventory, a default Move destination under .working/archive/moved/, or, for an adoption bundle, a retire preimage of the same run under .working/archive/adoption/<run-id>/.'),
        "The owning writer or migration MUST derive each inventory from the run's transaction record or receipt and MUST publish it exclusively with the retained bytes.",
        'An inventory MUST NOT be rewritten, so a bundle stays immutable and an evidence commit changes only its bundle folder.',
        _D('An inventory is not a journal projection and remains available in a clone without journals.'),
        'A phase inventory MAY be published in the same transaction as the base inventory to claim the promotion receipt without creating a receipt/inventory digest cycle.',
        _D('C-EVIDENCE-ENUM reconciles exact membership, directory structure, regular-file types, sizes and digests: every payload file under .working/imported/ and .working/archive/, meaning every file other than a bundle-root inventory, is claimed by exactly one row, and every inventory is itself schema-checked against the shape above rather than claimed.'),
        'Unlisted or unclaimed entries, a bundle without an inventory, and missing listed files MUST be findings; unreadable or malformed inputs, including a path claimed twice, MUST yield cannot-evaluate.',
        'The recognized legacy format opf.ingest.evidence-inventory/v1 MUST be refused as the named legacy-ingest-inventory finding under C-EVIDENCE-ENUM, without migration or rewriting; completed-ingest replay cannot evaluate that bundle.',
        'A phase inventory MUST NOT substitute for a missing inventory.toml: such a bundle cannot evaluate.',
        _D('Deleting a whole bundle, inventory and payload together, is outside this local snapshot check; independent history is required to detect that loss.'),
        _D('Inventories assert membership, not authenticated actor history.'),
        _D("The contained reader's size ceiling still applies."),
        _D('The legacy imports/ exclusion remains registered until its writers migrate.'),
        'In homes 2, staging/ MUST be walked and stray-graded, including empty runs; an unknown kind MUST always be a finding.',
        _D('The existing staged-plan presence test also recognizes import and ingest runs in their typed staging homes.'),
        _D('For legacy stores, other kinds cannot substantiate partial import status until their plan readers are registered.'),
        _D('At 1.3.0, partial status is receipt-bound under section 11, not inferred from staging.'),
        'Doctor MUST NOT consult a journal to decide partial status and makes no claim that a staged plan has a recoverable transaction.',
        "In homes 2, ordinary transaction operands, including those of an open transaction being recovered, MUST NOT equal, descend from, or contain journals/; a legacy store's transactions and recovery keep their legacy operand handling, and no shipped writer targets that home.",
        'Capability-bound journal APIs MUST derive their destinations from kind and run identity.',
        "Legacy journal transport MUST preserve bytes through the migration's receipt binding.",
        _D('These comparisons are byte-exact: on a case-insensitive or normalizing filesystem, a differently cased or composed spelling can alias a reserved home and is not caught.'),
        'Discovery precedes the manifest, so it cannot know the generation: it MUST examine manifest.toml in every immediate subdirectory, including journals/, MUST fail closed on a reserved-name match or ambiguity, and MUST NOT read deeper there.',
    ),
    "4.4": (
        _D('Machine-store TOML records live in .working/toml/.'),
        _D('The directory name .working at the store repository root is fixed by this standard.'),
        "The machine subdirectory's standard name is toml; tooling MUST NOT hardcode it, and MUST locate it by discovery.",
        'The names imports, imported, archive, staging, and journals are reserved at the store level for OPF control area and MUST NOT be used as a machine subdirectory name; discovery fails closed on a machine store so named.',
        _D('The legacy staging name imports remains reserved so legacy content cannot be re-absorbed.'),
    ),
    "4.6": (
        _D('Source files are lowercase; deliverables are uppercase.'),
        _D('- Every file inside .working/toml/ is lowercase: manifest.toml, counters.toml, version.toml, worklog.toml, worklog.imported.toml, lease.toml, init.toml, <type>.index.toml, <type>.imported.index.toml, archive.toml.'),
        _D('The pointer .opf.toml and its local override are lowercase machine source on the same terms.'),
        _D('- Every generated deliverable at .working/ top level, and the public deliverables at the product repository root (CHANGELOG.md, VERSION), is uppercase.'),
        _D('The rationale: uppercase filenames are the recognized cross-industry norm for read-me-first documents (README, LICENSE, CHANGELOG), they sort to the top of directory listings, and their prominence signals "this is the surface a human reads".'),
        _D('Lowercase signals machine-owned source that humans change only through tooling or review, never casually.'),
        'The casing itself is part of the contract: a lowercase file MUST NOT be a deliverable, and an uppercase file MUST NOT be hand-authored truth.',
    ),
    "5.7": (
        _D('Nothing stale, nothing ahead.'),
        'Before any OPF operation other than the reconciliation step itself (init, adopt, import, doctor, render, migrate, upgrade, absorb, record; opf sync is that surfaced reconciliation step), the store repository MUST be reconciled to a known-consistent, up-to-date state against its sync target: the target is fetched and the local store compared against it.',
        _D('- **Equal:** the operation proceeds.'),
        '- **Behind the target:** the tooling MUST refuse to operate and MUST surface the state.',
        'The remedy is a fast-forward pull to current (opf sync), which the tooling MAY offer and perform as its own surfaced step, then re-run the operation; the pull is never folded silently into another operation.',
        '- **Ahead of the target** (local commits not yet pushed, the lease still held by the resuming holder, and no divergent remote side): the tooling MUST refuse to operate until the store is reconciled, and reconciliation is an authorized push of the pending local commits.',
        'The holder MUST confirm and push them as its own surfaced step, never folded silently into another operation; the push is safe precisely because there is no divergent side that a push could lose.',
        _D('This is the recovery path for a store left ahead by a crash between a local write and its sync-back.'),
        '- **Divergent from the target** (unsynced commits on two systems): the tooling MUST refuse to operate and MUST surface the state.',
        "A divergence MUST halt for the human, always: it MUST NOT be auto-merged or silently resolved by picking a side, because a textual merge of the store's TOML records can silently mangle the very records the standard exists to protect.",
        '- **After any operation that writes,** the store MUST be synced back to its target in the same session, so the store is not left intentionally ahead on one system; a crash between the local write and the sync is detected on the next resume as an ahead-only or a divergent state and MUST be reconciled by the matching path above (an authorized ahead-only push by the lease holder, or a human-resolved halt on divergence), never left standing.',
        "A **single-writer lease** prevents concurrent divergent writes: before mutating the store, a run MUST take the lease (lease.toml, present only while held, carrying the holder, the operation, and an acquired-at timestamp read from the clock) and MUST make it observable at the sync target before its writes begin, so a second system's reconciliation sees the held lease and refuses.",
        'A lease MUST NOT be seized from a live holder; it MUST be reconciled against recorded state on resume or close, and a leftover lease from a dead run MUST be released only through that reconciliation.',
        'Where the concurrent-operation module is enabled, the lease MUST additionally be recorded as a session_lease record.',
        _D('There is a residual window between taking the lease and its reaching the target in which two systems can both begin; the divergence check above is the overlapping control that catches that collision after the fact, and the two layers together are the guarantee (disclosed in section 17).'),
        _D('This contract states two generic operational requirements inline.'),
        'First, a **single-writer lease**: a run MUST hold a lease so two runs never act on the same store state at once, MUST reconcile it on resume or close, and MUST NOT seize it from a live holder.',
        'Second, **reconcile the record against reality**: the store is authoritative only while it matches what is actually in use, so divergence MUST be detected by observation at defined checkpoints and treated as a finding to resolve, never a discrepancy to leave standing, and a store MUST NOT certify itself current merely because nothing updated it.',
        _D("AIQT's concurrency-lease and reconcile-record-against-reality rules are the reference implementation of these two requirements; the requirements themselves are the standard's and bind any conforming adopter."),
        _D('Scope of the contract by pattern: - **A store with a dedicated sync target of its own** (a companion repository, or any relocated store with a remote) is bound by the full contract above: OPF tooling is the writer, and it pulls current, refuses on divergence, and syncs back after.'),
        _D('- **The default in-repo store** has no dedicated sync target; it rides the product repository, whose own version-control discipline (branch and merge on green) is the consistency mechanism across systems.'),
        'There the contract reduces to the lease plus a clean-state check: the store paths in the working tree carry no conflict markers, no mid-merge state, and no concurrent OPF run, or the tooling MUST refuse.',
        _D("- **A local-only store** has no sync target, so the behind/ahead axis does not exist; the lease still guards concurrent runs on the one system, and durability is the adopter's recorded backup responsibility (section 5.3)."),
        _D('**Parallel branches allocate against the integration base.** The lease serializes writers on one store, but two branches of a store that rides the product repository each start from the same committed counters.toml, so each can claim the same record ID.'),
        "Store files therefore MUST NOT be hand-merged: after a merge conflict on a store path, take the integration base's version of the conflicted store files and redo the authoring operation on that base, which claims the next ID afresh (section 8.8).",
        _D('The byte-reproduction precondition of opf record and opf upgrade refuses only a store file whose bytes are not the canonical serialization of its content, such as one carrying comments or non-canonical formatting.'),
        _D('A hand edit or hand merge that leaves canonical bytes passes it undetected, so this integration-base rule is a separate requirement that the precondition does not enforce.'),
        _D('A merge that resolves without a conflict yet duplicates an ID is caught by opf doctor, which checks store-wide ID uniqueness and that every ID lies within its counter (section 8.2).'),
        'A collision MUST NOT be resolved by decrementing a counter or reusing an ID.',
    ),
    "6.1": (
        _D('.working/toml/version.toml is the machine ledger of version numbers, release dates, and release boundaries.'),
        _D("It is the single source for the project's version: the root VERSION file is deterministically generated from it (the latest release's version, as exact bytes) and drift-gated, and an optional human view renders to .working/VERSION.md."),
        _D("The reference suite's release-delta tooling (the check that computes the minimum required version bump for a change) anchors here; it is a consumer of the ledger, not part of the base standard's definition."),
        'The ledger is not the changelog: it carries numbers, dates, spans, and digests, and MUST NOT carry release prose.',
        _D('Each [[release]] row records: - version: the SemVer version string, unique in the ledger.'),
        _D('- date: the release date, RFC 3339 UTC, read from the clock at the release event.'),
        _D('- worklog_span: the inclusive, contiguous span of worklog entry IDs the release covers, as a two-element array ["WL-1", "WL-88"], or an empty array for a release with no worklog entries.'),
        _D('- coverage_digest: a digest over the canonical serialization of the covered worklog entries, in ID order, computed at release cut.'),
        "The exact canonicalization is fixed by the schema release that follows this specification; it MUST be deterministic and cover the entries' full content.",
        _D('- imported: optional boolean, permitted only as true and only on a historical release recorded under section 14.3.'),
        'On an imported-flagged row, date carries the source-recorded release date rather than a clock read at a witnessed release cut, worklog_span MUST be empty, and the row rests on imported provenance.',
        'A witnessed release cut MUST NOT set the flag, and the flag MUST NOT be added to or removed from an existing row.',
        'Release rows MUST be append-only and immutable once written.',
        'Spans MUST be contiguous and non-overlapping across consecutive releases, in ID order, so the released worklog tiles exactly and the unreleased tail is everything after the last span.',
        _D('The ledger also carries the summary rows that back the public changelog.'),
        _D('Each [[summary]] row records: - covers: a single released version ("1.3.0"), an inclusive range over contiguous released versions ("1.0.0..1.2.3"), or "unreleased" for the optional working section.'),
        _D('- status: working, published, or superseded.'),
        _D('- digest: required once status is published or superseded; the freeze digest of the corresponding CHANGELOG.md entry (section 7.2).'),
        _D('- superseded_by: present exactly when status is superseded; the covers token of the rollup summary that replaced this one.'),
        'Summary rows MUST hold digests and ranges only, never prose.',
        'Prose MUST live in exactly one place: the root CHANGELOG.md.',
    ),
    "8.1": (
        _D('| Tier | Type | Namespace | |---|---|---| | Baseline | backlog_item | BI | | Baseline | done | DN | | Baseline | worklog | WL | | Baseline | finding | FN | | Baseline | pending_decision | PD | | Baseline | autonomous_decision | AD | | Baseline | block | BL | | Baseline | handoff | HO | | Baseline | reference | RF | | Baseline | contribution | CN | | Baseline | maintainer_decision | MD | | Baseline | preference_pattern | PP | | Governance module | maintainer_action | MA | | Delivery-assurance module | artifact | AR | | Delivery-assurance module | gate_run | GR | | Delivery-assurance module | release | RL | | Delivery-assurance module | waiver | WV | | Operational-policy module | mode | MO | | Operational-policy module | tier_assessment | TA | | Concurrent-operation module | session_lease | SL | | Migration quarantine (importer-only) | legacy_fragment | LF | | Reserved, excluded | transaction | TX | | Reserved, unassigned | (none) | CL | Notes on the roster: - The word "changelog" is not a record type.'),
        _D('It names the curated public deliverable (section 6.3).'),
        _D('The detailed per-change type is worklog.'),
        _D('The namespace CL is reserved unassigned so it can never half-collide with the freed word.'),
        _D('- transaction (TX) is excluded from the adopter standard with its name and namespace reserved; it may enter later as a versioned module only if portable semantics are demonstrated.'),
        _D('- Modules ship default-off; each is enabled by one manifest edit.'),
        _D('legacy_fragment (LF) is deprecated for new stores as of 1.3.0, not removed: its taxonomy row and legacy validation remain for existing stores and evidence.'),
        'New imports MUST use the imported series and verbatim unparsed text (section 8.3), not LF quarantine.',
        'LF MUST NOT be scaffolded.',
        _D('- Imported history uses the same enabled types in a separate series, not additional record types.'),
        _D('Reserved namespaces remain reserved.'),
        _D('Imported states describe history and confer no current authority (section 8.6).'),
        '- done is a durable completion receipt linked one-to-one to a backlog item reaching ratified done; a standalone receipt MUST be imported history with provenance.',
        _D('- A finding records the observation and links its remediation rather than containing it.'),
        _D('- A block scopes one or more enumerated records and feeds actionability (section 8.5).'),
        _D('- contribution records an artifact, fix, or proposal this project proposes or sends to a peer project, with a delivery bundle once sent: the outward counterpart to reference, which records what comes in.'),
        _D('It carries no fleet-specific semantics; those ride a registered x-<vendor> extension (section 8.7).'),
        _D('- maintainer_decision and preference_pattern are baseline as of spec_version 1.1.0; they were module-tier in 1.0.0.'),
        _D('With that change the governance module carries maintainer_action alone, and the now-empty decision-support module is retired.'),
        _D('A store upgrades across the change with the additive migration (section 9.2).'),
        _D('- The delivery-assurance release record, where enabled, references a version.toml release row by version string; the ledger row is the fact, the record is the delivery-assurance envelope around it.'),
        _D('- version.toml and counters.toml are control ledgers, not record types.'),
    ),
    "8.2": (
        _D('Clean record IDs have the form <NS>-<n>; imported IDs have the form imported:<NS>-<n>, for example imported:BI-7.'),
        "The complete lexical grammar is ^(?:imported:)?[A-Z]{2}-[1-9][0-9]*$, and the namespace MUST additionally name the record's enabled type in section 8.1.",
        _D('Namespaces map one-to-one to types within each series.'),
        'counters.toml MUST hold independent monotonic high-water values per series and namespace: BI for clean backlog items and the quoted TOML key "imported:BI" for imported backlog items.',
        'The same rule includes "imported:WL"; clean release spans MUST tile only the clean WL number line.',
        "Uniqueness, counter high-water, contiguity and no-deletion checks MUST evaluate each series independently; allocation MUST increment its counter under the store's lock as one atomic claim, so no gap between choosing and reserving can double-allocate.",
        'Counters MUST NOT be reset and IDs MUST NOT be reused, even when a record is superseded, refuted, or its work reverted.',
        'Rotation, index rewrites, and store relocation MUST NOT touch counters.toml.',
        'Re-adoption MUST seed both series from a pinned ancestral snapshot and MUST refuse a missing required namespace; it MUST NOT zero-seed prior ancestry.',
        'Only a genuinely first adoption MAY start its counters at zero.',
    ),
    "8.3": (
        _D('Clean records carry the envelope below; the imported envelope follows it.'),
        _D('Types add their own fields on top.'),
        'Schemas are closed: an unknown key MUST fail validation unless it sits under a registered vendor extension table.',
        "| Field | Requirement | Meaning | |---|---|---| | id | required | <NS>-<n>, matching the type's namespace | | type | required | the type name; must match the file the record lives in | | status | required | per the status grammar (section 8.4) | | proposed_from | optional | the unqualified state held before the current /proposed status, written by the authoring verb (section 8.8); legal only on a /proposed status, and only naming a legal predecessor state for the type | | title | required | one line, human-oriented | | created_at | required | RFC 3339 UTC, read from the clock at creation | | updated_at | required | RFC 3339 UTC, read from the clock at the last transition | | actor.kind | required | maintainer, assistant, automation, or importer | | actor.id | optional | identity detail within the adopter's own vocabulary | | summary | optional | short prose body | | links | optional | array of {rel, id}; rel from the closed link vocabulary (section 8.6) | | refs | optional | array of captured references (section 8.6) | | x-<vendor> | optional | registered vendor extension tables only (section 8.7) | An importer MAY omit created_at where the source genuinely does not record it; the omission is recorded as unknown via the import provenance reference, never guessed.",
        _D('This legacy permission does not replace the following 1.3.0 imported-series contract.'),
        _D('The worklog entry uses a reduced envelope (id, date, actor, kind, summary, optional detail, links, refs); its status is fixed (section 8.5).'),
        'The worklog records facts, not proposable decisions, so its entries MUST NOT take the /proposed qualifier whatever the actor: an assistant-authored or automation-authored worklog entry is a conformant recorded fact needing no ratification (section 8.4).',
        _D('The imported envelope is closed and deterministic.'),
        'It MUST carry id, type, a one-line title, status from the type\'s legal state set without /proposed, actor.kind = "importer", actor.id naming the importing assistant, and an import provenance table.',
        'Imported worklog rows MUST use this envelope with type = "worklog" and status = "recorded", plus their worklog fields.',
        _D('Standalone imported done receipts are legal history.'),
        "Historical created_at, updated_at, date and decided_at are optional; when present they MUST be valid RFC 3339 UTC and no later than the writer's import clock instant.",
        'Import time MUST NOT stand in for event time.',
        'Other historical type fields MUST NOT be absent without an explicit missingness row.',
        'Supplied fields MUST retain their declared value types and vocabularies; unknown keys still fail.',
        'Missing historical timestamps and type fields MUST be accounted for in unrecorded = [{field, reason}], with one row per absent field, no duplicate fields and no row claiming a supplied field absent.',
        "field MUST name a field in that type's schema.",
        _D('The closed reasons are not_recorded_in_source, unparsed, ambiguous, conflicting, and not_applicable.'),
        _D('The first means "never recorded historically in the supplied source", not a claim about all history.'),
        'The required imported envelope and provenance fields MUST NOT be waived through missingness.',
        _D('Strict current resolution bundles and transition obligations do not apply to historical omissions.'),
        "The import table MUST carry source (the canonical store-relative path of the preserved original, spelled from .working/), source_sha256 (64 lowercase hexadecimal digits), run (the imp-<YYYYMMDD>T<HHMMSS>Z-<hash16> run ID), and imported_at (RFC 3339 UTC read from the writer's clock).",
        _D('Optional span is an informational byte range in the original.'),
        'Optional import.history retains verbatim source-precision values that cannot be losslessly normalized, such as a date-only string; a UTC midnight MUST NOT be fabricated.',
        _D('Optional import.unparsed holds verbatim source text that cannot be mapped.'),
        'The assistant MUST retain such text rather than drop it.',
        _D('The writer performs no byte-tiling or leftover accounting: byte-level coverage and semantic fidelity are not machine-proven.'),
        _D('Preserved originals remain the restoration authority.'),
        'Imported records and their worklog entries MUST be immutable after publication; corrections MUST be a fresh import run retaining the old evidence.',
        'A conforming imported series can reach doctor VALID: it MUST NOT enter the legacy importer or module-schema deferral seam.',
        _D('C-IMPORTED-SCHEMA checks this envelope and missingness; C-IMPORTED-IDS checks the series grammar, counters and no-deletion; C-IMPORTED-PROVENANCE re-reads each preserved original and verifies source_sha256.'),
        _D('C-CONTAINMENT recognizes the imported managed leaves, C-LINKS resolves the union of both series, and C-IMPORTED-SEGREGATION enforces section 8.6.'),
        'Missing or unreadable evidence MUST fail closed.',
    ),
    "8.4": (
        _D('status ::= state ( "/" qualifier )? state ::= lowercase name from the type\'s declared state set qualifier ::= "proposed" - Each type declares a closed state set: one initial state, zero or more working states, and one or more terminal states.'),
        '- A terminal transition performed by an actor whose kind is assistant or automation MUST land with the /proposed qualifier (for example done/proposed); creating a record directly in a terminal factual or ACT state that awaits no ratification, such as an autonomous_decision or a reference, is not such a transition and carries no qualifier.',
        'Only a maintainer transition MAY remove the qualifier (ratification) or return the record to a working state (rejection, with a recorded reason).',
        'A /proposed status is not terminal: gates and completion claims MUST treat the record as unfinished, and views MUST surface it as awaiting ratification.',
        _D("The /proposed qualifier attaches to any transition an assistant or automation makes that awaits a maintainer's ratification: the assistant or automation terminal transitions above (for example done/proposed or fixed/proposed), and the gated non-terminal states, which are proposals rather than grants even though they are not terminal."),
        _D("A type declares its gated states: block gates active (a proposed block, active/proposed), contribution gates sent (an assistant-sent contribution, sent/proposed, awaiting a maintainer's ratification that it was genuinely sent), and preference_pattern gates active (an assistant-distilled pattern, active/proposed, awaiting ratification)."),
        'This is one mechanism, not a set of per-type special cases: entering a gated state as an assistant or automation MUST take /proposed, and only a maintainer MAY ratify it to the unqualified state.',
        'A recorded factual entry that proposes nothing and awaits no ratification is exempt: the worklog, whose entries record facts rather than propose a transition, MUST NOT take /proposed, so an assistant-authored or automation-authored worklog entry (status recorded) is a conformant recorded fact rather than an unratified proposal.',
        '- Standing authorization for contribution sent: sent gating is on by default, but an adopter MAY declare a standing authorization for a named recipient that deactivates per-send gating for that recipient, letting an assistant or automation land the unqualified sent grant to it without a per-send ratification.',
        "A valid declaration for the contribution's declared recipient relieves the gating; an absent or malformed declaration MUST fail closed, so gating stays on.",
        _D('The declaration is an adopter configuration surface; a store that declares none keeps every sent gated.'),
        '- No resurrection: a record in an unqualified terminal state MUST NOT re-enter a working state.',
        'A revived concern MUST be a new record linking the old one.',
        "- Supersession is a link, not a state edit: the superseding record MUST link supersedes, and where the type records it, the superseded record's terminal state MUST reflect it.",
        'Where the superseded record is immutable, including every imported record, its recorded state MUST stand and the link alone records the supersession.',
        _D('- The worklog is a special case of these rules, with terminality keyed to release rather than to a state transition (section 6.2).'),
        _D("It declares the single state recorded: while an entry sits in the unreleased tail its recorded status is pre-terminal and mutable-until-release, and once a release freezes its span the entry's recorded status is terminal and immutable."),
        _D("The released-frozen span is therefore the worklog's terminal state that satisfies the one-or-more-terminal-states requirement above, while correcting an unreleased entry in place is an ordinary pre-terminal edit, not a resurrection of a terminal record."),
    ),
    "8.5": (
        'Baseline types: | Type | States | Rules | |---|---|---| | backlog_item | open > active > done or dropped; open > dropped | Ratified done MUST create the one-to-one done receipt.',
        'Blocked-ness MUST NOT be a stored state; it is derived from active blocks at view time.',
        _D('| | done | recorded | Created terminal, immutable.'),
        _D('Links receipt_of to its backlog item.'),
        _D('| | worklog | recorded | Durable operational record with release-keyed terminality: the unreleased tail is pre-terminal and mutable, a released-frozen span is terminal and immutable (sections 6.2 and 8.4).'),
        _D('Takes no /proposed qualifier whatever the actor.'),
        _D('Mutability governed by section 6.2, not by transition.'),
        '| | finding | open > fixed, routed, refuted, or accepted | Severity MUST be graded at or after the fix decision, never before.',
        '| | pending_decision | open > decided or withdrawn | All-or-none resolution bundle: an open decision MUST carry none of decision, decided_at, decided_by; a decided one MUST carry all.',
        'A decided record MAY be superseded by a new decision linking supersedes; exactly one current effective resolution MUST exist per chain.',
        _D('| | autonomous_decision | recorded | Immutable ACT record: the classification basis, the action, links.'),
        'Overturning MUST be a new record (or maintainer decision) linking it.',
        _D('| | block | active > released or expired | Scopes an enumerated list of record IDs.'),
        'A block created by an assistant or automation actor is active/proposed and is a proposal, not a grant: it MUST NOT count toward blocked-ness or justify a stop until a maintainer ratifies it.',
        '| | handoff | current > superseded | Posting a new handoff MUST supersede the previous in the same act; at most one current handoff MAY exist among clean records authored by non-importer actors (section 8.6).',
        _D('| | reference | recorded | Immutable captured reference.'),
        '| | contribution | proposed > sent > acknowledged or superseded; proposed > withdrawn | Records what this project proposes or sends to a peer, with a delivery bundle {channel, ref, sent_at, receipt_ref?, receipted_at?}: channel/ref/sent_at MUST be present once sent, sent_at MUST NOT be present before, and the receipt fields MAY appear only at acknowledged.',
        _D('sent is gated (an assistant lands sent/proposed; a maintainer, or a valid standing authorization for the recipient, lands the bare grant).'),
        'acknowledged is the single positive terminal (responded, adopted, reshaped, or declined); the outcome MUST live in summary/x-<vendor>, never as a state.',
        'A re-send MUST be a new record linking supersedes; the superseded record records superseded.',
        _D('| | maintainer_decision | recorded | Created-terminal, immutable maintainer ruling carrying its decision (answer plus rationale).'),
        'actor.kind MUST be maintainer or importer only (a maintainer ruling with assistant attribution is a contradiction; importer covers migrated history).',
        'Overturning MUST be a new record linking the old.',
        'It MAY exemplifies the preference_pattern it instantiates.',
        _D('| | preference_pattern | active > retired | A distilled preference pattern carrying context and rationale (with the envelope title).'),
        _D('active is gated: an assistant-distilled pattern lands active/proposed awaiting maintainer ratification to the unqualified active.'),
        _D('| Module types, in outline (full schemas ship with the module schemas release): maintainer_action open > done or dropped; artifact staged > promoted or rejected; gate_run recorded with a three-valued verdict field (pass, fail, cannot_evaluate), never folded into status; release planned > published or abandoned; waiver active > expired or revoked, expiry required at creation; mode active > retired; tier_assessment recorded; session_lease held > released or reconciled; legacy_fragment quarantined > resolved or ignored.'),
        _D('Actionability: a clean backlog item authored by a non-importer actor is actionable when its state is open or active and no clean, unqualified active block authored by a non-importer actor scopes it.'),
        'Imported and importer-authored records MUST NOT enter this join on either side: an importer-authored backlog item is history, never actionable work, whatever its recorded state, and an importer-authored block MUST NOT block (section 8.6).',
        'This is the block join every scheduling view renders; a scheduling view MUST surface an importer-authored item only as history, never as actionable.',
    ),
    "8.6": (
        _D('links relate records to records; rel comes from a closed vocabulary: supersedes, resolves, remediates, receipt_of, corrects, follows, relates, exemplifies, derives_from.'),
        _D('Extending the vocabulary is a specification version change.'),
        _D('- exemplifies: the source record instantiates the linked pattern.'),
        'Its target MUST be a preference_pattern (PP); the source side is unconstrained.',
        _D('Its primary user is maintainer_decision (a ruling exemplifies the pattern it instantiates).'),
        _D('- derives_from: the source record was derived from the linked record; directional, usable by any type.'),
        _D('Its primary user is contribution (a contribution derives from the internal finding, decision, or backlog item that motivated it).'),
        _D('refs capture external sources at the moment a claim or artifact is produced: each is {kind, locator, note} with kind one of path (a repository path, with a line where applicable), url, or doc (a document and section).'),
        _D('A record whose claims rest on an external source without a captured reference is unsourced, whatever confidence backs it.'),
        _D('Imported history has an authority firewall, enforced by the writer and doctor and keyed on provenance rather than on series alone: it covers every record whose actor.kind is importer, in the imported series or written into the clean series by the pre-1.3.0 legacy importer.'),
        'An importer-authored record MUST NOT satisfy a current approval, receipt, actionability or supersession obligation of any record authored by a non-importer actor.',
        _D("The same bar covers every current obligation, record-level or store-level, including field-borne authority: a gate_run verdict, a tier_assessment outcome, an artifact's promoted state, a release's published state and a maintainer_action's done on an importer-authored record describe history and discharge nothing now."),
        "A clean done record's receipt_of MUST target a clean backlog item, and a clean backlog item reaching ratified done MUST hold a clean receipt authored by a non-importer actor; a legacy importer-authored receipt satisfies, as recorded history, only the legacy importer-authored backlog item it was recorded with.",
        "One exception is defined so that the section 9.2 refusal always has a writer-performable remedy: where that upgrade withdraws a ratified done item's only receipt as legacy importer-authored, a maintainer-authored clean maintainer_decision that links corrects to that item and records that its completion stands MUST satisfy the item's receipt obligation in place of the withdrawn receipt; opf record create authors such a decision, doctor and the upgrade MUST accept it, and a maintainer who instead judges the work unfinished MUST record a new clean backlog item linking derives_from back, never reopen the terminal done.",
        'An importer-authored decision MUST NOT be treated as the current effective resolution of a clean pending_decision chain; resolving such a chain now MUST take a new clean decision linking back.',
        'Importer-authored blocks MUST NOT grant a current stop, and an importer-authored record MUST NOT discharge a required supersession.',
        'The firewall covers every state-bearing type: an importer-authored record MUST NOT be treated as the current handoff, an active waiver, a held session_lease, an active mode, or a ratified active preference_pattern; a state-bearing status on an importer-authored record describes history at its source and MUST confer nothing now, and every current-state join, including the section 8.5 at-most-one-current handoff rule, MUST evaluate only clean records authored by non-importer actors.',
        'After the section 9.2 upgrade an existing legacy importer-authored clean record MUST keep its bytes, its recorded state and its place in the clean series, and remains readable history there; what the upgrade changes, and its report enumerates (section 9.2), is authority alone: such a record leaves every current-state join, its open or active backlog items leave actionability, and its blocks stop granting a stop.',
        'The clean-series writer MUST refuse a transition on an importer-authored record in either series (section 8.8).',
        'Acting on history MUST take a new strict clean record at the present time, with a link back to the historical record.',
        _D('C-IMPORTED-SEGREGATION enforces this firewall over importer-authored records in both series.'),
        'Clean-to-imported links MUST be limited to relates, derives_from, follows, and same-type supersedes; the last records historical continuation without discharging a current supersession obligation, and the immutable imported target keeps its recorded state, the link alone recording the supersession (section 8.4).',
        'Imported-to-imported links MAY use every declared relation subject to its type constraints.',
        'Imported-to-clean links MUST be refused by both writer and doctor.',
        'Links MUST resolve over the union of both series; a dangling cross-series link MUST be a finding, never silently omitted.',
        'The imported series MUST NOT supply current authority through a link, an extension, or an adoption approval.',
    ),
    "8.8": (
        _D("opf record is the reference tooling's record-authoring verb."),
        _D('It writes the record and worklog shapes this specification defines, and it adds one optional envelope key of its own: proposed_from (section 8.3), which appears only on /proposed records written by opf record transition.'),
        _D('That key was added while base 1.2.0 was still unreleased, so no released 1.2.0 store or tooling predates it, and it carries no spec_version bump.'),
        _D('Its clean-series subcommands use the strict record model; the new import write mode uses the separate 1.3.0 imported model.'),
        'Import is not a flag that relaxes create, and the clean-series subcommands MUST refuse actor.kind = "importer": an importer authors only through import.',
        'The clean-series subcommands MUST also refuse an importer-authored record as their operand: acting on imported or legacy importer-authored history is a new clean record linking back (section 8.6).',
        _D("Its subcommands: - create: one new record of an enabled baseline type, in the type's initial state."),
        _D('An assistant or automation author entering a gated initial state lands /proposed; a created-terminal factual or ACT type (reference, autonomous_decision, maintainer_decision) carries no qualifier (section 8.4).'),
        _D('done receipts and worklog entries are not created this way.'),
        _D("- transition: a status change checked against the type's grammar (section 8.5)."),
        "An assistant or automation author landing a terminal or gated state takes /proposed, and the verb MUST record the state the record held at that moment in the record's own proposed_from field (section 8.3) in the same act; only a maintainer ratifies, or rejects with a recorded reason back to the recorded pre-proposal state (section 8.4).",
        'A rejection MUST restore exactly the recorded proposed_from state, and leaving the /proposed status, by rejection or ratification, MUST remove the field.',
        _D('A proposed record that carries no proposed_from was proposed outside transition; that includes a record create lands directly at a /proposed initial state, so the absence establishes no provenance.'),
        'Such a record cannot be rejected by transition, which never infers or invents a predecessor; the worklog entry of the proposing transition is informational, never evidence. transition MUST refuse to act on, or overwrite, a record whose current row is not schema-valid, proposed_from included, so a schema-detectable forgery must be repaired by the operator before transition acts; it is never laundered.',
        _D('The field is an ordinary record field, so a canonical hand edit of it that keeps the row schema-valid is not detected, the same as any other field (the section 5.7 integration-base rule remains the control).'),
        'A backlog item MUST reach unqualified done only through done-with-receipt.',
        _D('- done-with-receipt: maintainer-only.'),
        _D('It moves a backlog item to unqualified done, from active or by ratifying done/proposed, and in the same act creates its one-to-one done receipt linked receipt_of (section 8.5).'),
        _D('An assistant reaching done uses transition and lands done/proposed; no receipt exists until a maintainer ratifies.'),
        _D('- worklog-append: one entry appended to the unreleased tail of worklog.toml, status recorded, never /proposed whatever the actor (sections 6.2 and 8.4).'),
        'An entry that would fall inside a released span MUST be refused.',
        _D('- import --batch FILE [--root DIR]: the primary import surface (the import write mode; there is no separate --import flag).'),
        _D('A single-record import is a one-row batch.'),
        _D('The canonical TOML batch, opf.record.import-batch/v1, declares one source path, record rows, worklog rows, historical fields, missingness, verbatim unparsed text and batch-local link keys.'),
        'The writer MUST resolve local keys to claimed imported IDs and MUST recompute source size and SHA-256 from preserved bytes, never trusting caller-supplied measurements.',
        _P('One invocation covers one source in one journaled transaction, followed by one render and one full doctor, both treating a deferred view destination under sections 14.2 and 11, so a pending migrate, move or retire source at a declared view path is neither overwritten nor an obstacle to VALID.'),
        'create, transition, and done-with-receipt MUST each append their own worklog entry, one entry per change (section 6.2), in the same journaled transaction as the change.',
        "Its detail MUST open with a lifecycle line naming the record and its status change (opf-record create <ID> <status>, or opf-record transition <ID> <from> -> <to>); a rejection's entry MUST also record its reason.",
        'worklog-append MUST refuse a detail that opens with that lifecycle grammar.',
        'Every subcommand runs one operation sequence, and an implementation of the verb MUST preserve its guarantees: 1.',
        "Resolve the store, then reconcile any interrupted authoring transaction first, and settle any owed adoption or import cutover re-check the same way: while a committed cutover's re-check outcome is unrecorded, every roster command of the section 14.2 matrix MUST, before any store write, either run and record that owed outcome through the section 14.1 journal-led recovery or refuse.",
        'Reconciliation writes the store, so it MUST run only under the single-writer lease that publication uses: a held lease MUST refuse before any recovery write and MUST NOT be seized.',
        'An operand changed since the interruption, to bytes that are neither its journaled prestate nor its planned poststate nor a write of either torn by the interruption, MUST be reported and refused, never overwritten.',
        'A reconciled interruption MUST refuse the new operation, so the operator inspects it before anything new is written.',
        _D('2. Precondition: re-emitting the unchanged parsed model of every file the operation rewrites reproduces its on-disk bytes exactly (the section 9.2 rule).'),
        'A file carrying comments or non-canonical serialization MUST be refused and left untouched.',
        _D('The check proves serialization only: a hand edit or hand merge that leaves canonical bytes is not detectable by it, and the integration-base merge policy of section 5.7 remains a separate requirement.'),
        _D('3. Claim each new ID as one atomic act (section 8.2).'),
        'At homes 1 the counters.toml advance MUST be an operand of the same journaled transaction, under the held lease, and IDs MUST be reported only once that transaction has completed, so a rollback never withdraws an ID anyone has seen.',
        _D('At homes 2 the claim is an irrevocable reservation in the journal home, made before the reversible publication (section 4.2).'),
        _D("4. Postcondition: the model diff of every rewritten file equals exactly the operation's allowed delta (the new rows appended, the counters advanced by exactly the claim, and for a transition one status and updated_at change plus the proposed_from write or removal), value for value and type for type (a boolean or float is never equal to an integer), before anything is written."),
        'The expected delta MUST be derived from the request, the prior bytes of each rewritten file, the claimed IDs, the clock value, and the schema rules, never from the planned rows themselves.',
        '5. The in-repo store contract (section 5.7): the planned destinations MUST be clean, including ignored files, and the single-writer lease MUST be held across publication, render, and the final doctor.',
        '6. Every rewritten file MUST be published in one crash-durable journaled transaction, so an interruption leaves the store exactly at its prestate or exactly at its poststate once reconciled.',
        _D('The reference tooling keeps that journal under .aiqt/record/journal at homes 1.'),
        _D('7.'),
        _P('The declared views are rendered, except that an occupied destination in a protective lifecycle state of the section 14.2 matrix, a deferred view destination included, is never written and leaves the planned destination set, so the step 5 cleanliness gate does not read it: its generated bytes are validated prospectively and the occupying source, or the landed postimage of a committed cutover, is left in place.'),
        _P('Then a full doctor must report VALID, evaluating a deferred destination under the section 11 bounded treatment; a failure leaves the change for review with recovery advice scoped to the planned paths.'),
        _P("One exception applies to a status change: doctor compares it with the prior committed snapshot, and doctor's history comparison sees only the prior snapshot's type and status, which identify neither the transitioning actor nor the pre-proposal state; doctor validates proposed_from but does not use it as rejection evidence, so doctor can grade that change cannot-evaluate until the change is committed."),
        _P("The verb's render and final doctor accept that cannot-evaluate only for exactly the record and the from and to statuses it has just written, never a finding and never any other cannot-evaluate, and the verb reports it as pending until commit."),
        _P('opf doctor itself is unchanged and still reports it until then.'),
        '8. The lease MUST be released, and only then are the claimed IDs and touched files reported.',
        'The change MUST be left uncommitted in the working tree: the verb MUST NOT stage or commit it.',
        _P('The verb exits 0 when the change is recorded and the store is doctor-VALID (or carries only the pending cannot-evaluate of item 7), and 2 on every refusal or cannot-evaluate.'),
        'Import MUST preserve that operation sequence, including the one allocation seam, independent model delta check, cleanliness gate, lease and reconcile-first recovery.',
        _D('Its atomic operands are the imported counter rows first, the touched <type>.imported.index.toml and worklog.imported.toml files, and the evidence bundle: the exact original at .working/imported/import/<run-id>/originals/<source-path> and inventory.toml in the retained opf.evidence.inventory/v1 format (section 4.2).'),
        _D('The journal remains .aiqt/record/journal at homes 1.'),
        'It MUST NOT rewrite clean records or clean counters.',
        'Import MUST refuse with exit 2 on a non-importer actor, absent or invalid provenance, a clean-series record operand, an imported-to-clean link, a historical timestamp later than the run clock, an unknown missingness reason, or a type not enabled and supported by the writer.',
        "It MUST also refuse without an adoption receipt, outside that plan's approved migrate-source scope, or when any section 14.1 bound item, the live source bytes against the plan digest, the tool release identity, or the prompt-pack version and digest included, no longer matches the approved plan.",
        'Bound-item drift, in this session or a later one after a tool upgrade, MUST refuse into a fresh plan with its own single approval; import MUST NOT reinterpret the old approval.',
        _D('Replay identity is (source_sha256, batch content digest).'),
        'A completed identical replay MUST re-read and verify the published records and evidence, then succeed as a no-op reporting the existing IDs.',
        'A partial overlap, including a differing batch against the same source within the run, MUST refuse, naming the overlap and directing to journal recovery.',
        'Each source admits at most one completed batch per correction chain: a differing batch against a source whose batch already completed MUST refuse and name the completed run, unless the new batch declares that run as the completed run it corrects.',
        "Such a corrected import is a fresh run; its records MUST link supersedes or corrects to the imported records they replace, the previous run's immutable evidence MUST be preserved, and source completion (section 14.1) evaluates the correcting run.",
        "Before the source's retirement, the live-bytes plan check of the refusal list MUST bind a correcting run as it binds any other; after its retirement, the preserved original under .working/imported/import/<run-id>/originals/ is the source of record, the digest bound is its recorded source_sha256, and the live-bytes check does not apply to the retired path.",
        'Retry MUST NOT allocate duplicate IDs.',
    ),
    "9": (
        _D(".working/toml/manifest.toml is the store's control document and discovery marker."),
        _D('Illustrative shape (the schema release that follows this specification is normative): toml # .working/toml/manifest.toml # OPFiles (AIQT Development Operational Standard) store manifest and discovery marker.'),
        _D('[opf] standard = "opf" # discovery token; exact value required spec_version = "1.3.0" # OPFiles base spec version this store conforms to layout = "inline" # storage layout: "inline" or "per-record" (was layout_profile) posture = "required" # "off", "warn", or "required" (section 11) import_status = "none" # "none", "partial", or "complete" [store] sync_target = "" # the store\'s dedicated sync target (section 5.7); empty under the # in-repo default, where the store rides the product repository [modules] # base-level optional capability modules (generic, not AIQT-specific) governance = true # the [profiles.aiqt] profile below requires these three enabled delivery_assurance = false operational_policy = true # (required by [profiles.aiqt].required_modules) concurrent_operation = true # (required by [profiles.aiqt].required_modules) # --- Profiles: additive requirement bundles, namespaced, ignored by base-only tooling --- [profiles.aiqt] version = "1.0.0" # AIQT profile version, independent of spec_version above base_compat = ">=1.0.0 <2.0.0" # base spec_versions this profile applies to posture_floor = "required" # effective posture = strictest(base.posture, this) required_modules = ["governance", "operational_policy", "concurrent_operation"] verification_floor = "triple-family" # AIQT reference-suite policy; base tools ignore this extension_namespace = "x-aiqt" # record-level namespace this profile owns (section 8.7) # A second adopter could later add, ignored by everyone who does not support it: # [profiles.acme] # version = "0.1.0" # base_compat = ">=1.0.0 <2.0.0" [types.backlog_item] namespace = "BI" # ...'),
        _D('one [types.<name>] table per enabled type; namespaces per section 8.1 [providers.local-directory] handler = "builtin" roles = ["create", "sync"] [providers.generic-git-remote] handler = "builtin" roles = ["sync"] # ...'),
        _D('optional host providers, for example: # [providers.github] # handler = "plugin" # roles = ["create", "auth"] [unmanaged] paths = [] # pre-existing files kept in place, enumerated (section 14.2) [views."BACKLOG.md"] kind = "composed" sources = ["backlog_item", "block"] target = ".working/BACKLOG.md" [views."WORKLOG.md"] kind = "deterministic" sources = ["worklog"] target = ".working/WORKLOG.md" [views."DECISIONS.toml"] # a machine projection (section 10.5): deterministic, byte-drift-gated kind = "projection" sources = ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"] target = ".working/DECISIONS.toml" [views."VERSION"] kind = "deterministic" sources = ["version"] target = "VERSION" [deliverables."CHANGELOG.md"] kind = "curated" target = "CHANGELOG.md" [archive] period = "year" [vendors] registered = ["x-aiqt"] # record-level extension namespaces; the aiqt profile owns x-aiqt, # registered here so base-only record validation accepts x-aiqt fields View and deliverable targets under .working/ are relative to the store repository root; the public targets (VERSION, CHANGELOG.md) are relative to the product repository root (section 5.8).'),
        _D('Storage layout: The layout field selects the storage layout only: - **inline** (default): records live inline in <type>.index.toml; the index and the store coincide.'),
        'One global store lock MUST serialize writers.',
        _D('This is the ordinary single-writer case: a consumer reads one small file with one parse.'),
        '- **per-record** (with the concurrent-operation module): <type>.index.toml becomes a registry of {id, state, path, digest} rows, records live one per file under <type>/, and any multi-record operation MUST take (namespace, id) locks in ascending order.',
        _D('Concurrent writers pay the file-count cost only when they have the problem it solves.'),
        _D('Readers always enter at <type>.index.toml in either layout.'),
        'The ledgers are exempt from the per-record layout: version.toml and worklog.toml MUST always be single files, written under the store lock (allocation of WL IDs still goes through counters.toml atomically).',
    ),
    "9.2": (
        _D('The homes-generation upgrade targets spec_version = "2.0.0" with required integer [opf].homes = 2.'),
        'Absent or 1 denotes legacy homes for migration; unknown future generations MUST be refused.',
        _D('The homes-generation target does not itself change the runtime supported version or init format.'),
        'The migration MUST refuse a store resolved outside the product root until a multi-root coordinator exists.',
        'Unproven legacy .archive/ entries MUST remain in place with a standing finding until dispositioned.',
        'A base-schema version bump MUST ship a tested, in-place store-schema upgrade (opf upgrade).',
        'The upgrade MUST be idempotent.',
        'A purely schema-level bump MUST be additive, using atomic replacement of existing files, create-only writes for new index files, and regeneration of declared views through exclusively created temporary files followed by atomic rename.',
        _D('These writes are sequential, with recovery scope held in memory, not a durable transaction journal.'),
        'A homes-generation bump MUST additionally relocate OPF control areas as a versioned, journaled, fail-closed relocation.',
        'Every destination MUST be digest-verified before its source is removed.',
        'Both kinds of upgrade MUST run under the store consistency contract and the single-writer lease (section 5.7).',
        'It MUST fail closed on an unresolvable store, a declared spec_version ABOVE the tooling, a divergence, a held lease, or any populated state that contradicts its preconditions; it MUST NOT lower the fail-closed floor.',
        'Before any write it MUST enforce two fail-closed preconditions: it claims the single-writer lease (section 5.7) and holds it across the whole mutation, and it verifies the working tree is clean, including ignored files, over the planned schema and render destinations (store and product roots) and the index collision candidates, so the committed HEAD is a verified restore path for that scope; a held lease or a dirty store refuses, and a dirty store is asked to commit its own changes, never restored by the tool.',
        _P("After applying the schema delta, it regenerates the declared views and requires a full doctor VALID before offering the uncommitted change for the adopter's own branch-and-merge."),
        'It MUST NOT stage or commit the change.',
        "Both upgrade kinds are roster commands under the section 14.2 matrix, keyed on the destination's lifecycle state, on the schema-level path and the homes-generation relocation path, the homes-2 path included, alike: an upgrade MUST validate an occupied destination's view bytes prospectively, MUST withhold the same-path publication, MUST NOT overwrite or relocate the occupying source, and MUST NOT write a destination whose committed cutover awaits its recorded re-check outcome or whose re-check failed; the protected path sits outside its planned schema and render destinations, so the clean-tree precondition does not read it, and its required doctor VALID grades that destination under the section 11 bounded treatment.",
        "An upgrade delta or manifest change that would declare a new view or managed store path at a registered [unmanaged] path MUST refuse, naming the collision; the remedy is the adopter's own fresh plan re-dispositioning the kept file (section 14.2).",
        'The manifest and counters rewrite MUST be a model regeneration through the canonical new-document emitter, never a textual round-trip edit, bounded by two guards: a precondition that re-emitting the UNCHANGED parsed model reproduces the on-disk bytes exactly (proving the file is canonical and comment-free, so nothing can be lost), failing closed otherwise; and a postcondition that the model diff equals exactly the allowed delta, failing closed otherwise.',
        _D('The allowed delta is expressed as ensure-present and ensure-absent over the whole 1.0.0 origin family, so a governance-enabled, a decision_support-enabled, a bare, and a view-omitting 1.0.0 store all migrate under one rule and the normative text cannot diverge from the tooling.'),
        _D("For the 1.0.0 to 1.1.0 upgrade the allowed delta is: rename the base table [devprocess] to [opf] and its standard discovery token from devprocess to opf (the OPFiles rebrand), carrying every other base field over unchanged; bump spec_version to 1.1.0; remove the retired decision_support module key where present; add each of the [types] rows for contribution, maintainer_decision, and preference_pattern not already declared by an enabled 1.0.0 module (a governance-enabled store already declares maintainer_decision and a decision_support-enabled store preference_pattern; the row moves from module tier to baseline unchanged); add the two new view rows (CONTRIBUTIONS.md and the DECISIONS.toml projection); widen the existing DECISIONS.md composed view's sources from the two 1.0.0 decision sources (pending_decision, autonomous_decision) to the four required at 1.1.0 by adding maintainer_decision and preference_pattern where that view is declared (a 1.0.0 store that declares no DECISIONS.md gains none and stays valid, since no composed view is required); extend counters.toml with the CN/MD/PP zeros while preserving every existing high-water; and create each missing empty *.index.toml file for the three baseline types, skipping any that already exist (such as a maintainer_decision.index.toml where governance was enabled, whose records are preserved byte-for-byte)."),
        _D('The upgrade weakens nothing: preference_pattern simply moves to always-on, so a populated decision-support index is kept as is.'),
        _D('Base spec 1.2.0 admits one new managed machine-store file, .working/toml/init.toml: the bootstrap provenance a coupled opf init records (its format is frozen in OPF-INIT-D2B).'),
        _D('It is a managed leaf when present and is never required, so a store without it stays valid.'),
        'For the 1.1.0 to 1.2.0 upgrade the allowed schema delta is the spec_version bump alone: no other manifest field, schema file, or counter changes, and the upgrade MUST NOT create provenance for an existing store (none is ever fabricated).',
        _D('Declared views are then regenerated, so a stale committed view can change.'),
        _D('A 1.0.0 store takes the 1.0.0 delta above directly to 1.2.0.'),
        _D('For the 1.2.0 to 1.3.0 upgrade, the allowed schema delta is the version bump, registration and create-only initialization of missing imported managed leaves for enabled types, and addition of missing imported counter rows at zero only where no imported ancestry exists.'),
        'Existing records, evidence, clean counters and imported high-water values MUST be preserved; a populated collision, missing ancestral counter or unprovable prestate refuses.',
        'The upgrade MUST NOT create historical records, adoption approval or provenance, MUST NOT change posture or import status, and MUST NOT add imported views.',
        "The bump changes no record's bytes, but it does change what a legacy importer-authored clean record confers, because the section 8.6 firewall keys on provenance: the upgrade report MUST enumerate every legacy importer-authored clean record whose current authority the firewall withdraws, naming each backlog item that leaves the actionability join, each block that stops granting a stop, each ratified done item whose only receipt is legacy importer-authored and so stops holding a valid receipt, and each record that ceases to be a current state under section 8.6, so that authority change is reported, never silent.",
        'Where a withdrawn authority would leave the post-upgrade doctor below VALID, a receipt-stripped done item included, the upgrade MUST refuse before any write, naming each such record and the remedy: a maintainer-authored clean record that restores or supersedes the withdrawn authority under section 8.6, for a receipt-stripped done item the section 8.6 maintainer_decision route, recorded before the upgrade is retried; every named remedy MUST be performable through the sanctioned writer without hand-editing canonical files.',
        'An unresolved legacy import MUST be reconciled under its original contract before upgrading; legacy LF records and evidence remain readable and MUST NOT be silently converted.',
        'A completed legacy import upgrades in place: its import_status MUST stay "complete", substantiated by its preserved legacy run evidence under section 11, and adoption approval, receipt or provenance MUST NOT be fabricated for it.',
        'The bump also activates the homes-1 recognition of the adoption and import control paths and the four imported-series checks of section 4.2; the upgrade itself MUST NOT create such a folder.',
        _D('Earlier stores compose their applicable deltas with this delta; repeated upgrade is a verified no-op only after full doctor VALID.'),
        _D('The 1.3.0 delta remains a target contract until a tested upgrade and its required readers activate; import writing is a separate later activation.'),
        "Declaring a spec_version above the tooling's supported version MUST be refused at validation as a fail-closed INVALID finding naming the tooling-upgrade remedy, never treated as supported; the 1.2.0 reference validator that ships this ceiling already refuses a 1.3.0 declaration with exactly that finding.",
        _D('Only the reserved homes-2 declaration of section 4.2 stays recognized, and that recognition keeps legacy homes-1 grading until its own activation, never homes-2 validation.'),
        _D('Two residuals are disclosed rather than silent: tooling released before this ceiling treats an above-ceiling declaration as legacy and can report it VALID, and the recognized homes-2 pair is graded under legacy rules, not refused.'),
        _D('Both residuals end when pre-ceiling tooling leaves use and homes 2 activates.'),
    ),
    "11": (
        _D('Enforcement has two layers with deliberately different ceilings:'),
        _D('| Layer | off | warn | required |'),
        _D('|---|---|---|---|'),
        _D('| Artifact and record integrity | not run | reported, non-failing | build-failing, fail-closed |'),
        _D('| Adoption coverage | not run | report only | report only, never build-failing |'),
        _D('The integrity layer is deterministic and safe to hard-fail; opf doctor runs it.'),
        _D("It includes: schema validity of what exists; ID uniqueness across active, archive, and staging; counter monotonicity; bidirectional index reconciliation; transition legality and no-resurrection; the all-or-none resolution bundle; view drift (byte) for every deterministic view including VERSION; worklog span tiling and frozen coverage digests; changelog range coverage; changelog freeze; archive integrity; the tracked-store requirement against the resolved store; pointer and sync-target agreement (the committed pointer, the manifest's recorded sync target, and the store repository's actual remote agree; section 5.6); unmanaged-path containment (section 14.2); and path containment."),
        'At required, an unreadable, unparseable, or unresolvable declared input MUST be a failure, never an empty or clean result.',
        'Imported history joins this integrity layer through the deterministic checks in sections 8.3 and 8.6; the authority firewall MUST NOT be report-only.',
        _D('import_status = "none" means clean start with no approved migrate-source import.'),
        _P('"partial" means the adoption receipt enumerates migrate-disposed sources whose cutover transactions have not yet committed with a matched post-commit re-check (section 14.1), green eligibility included; it can persist across assistant sessions without a process running.'),
        _P('"complete" means every such source has a green completion result under section 14.1 whose cutover transaction has committed with its landed poststate re-checked and a live full doctor VALID recorded, or that a pre-1.3.0 legacy import finished and its preserved legacy run evidence substantiates it; the section 9.2 upgrade preserves that legacy status without fabricating an approval or receipt.'),
        'A partial or complete status that neither an adoption receipt with completion results nor preserved legacy import evidence substantiates MUST fail closed, as does a missing, unreadable or contradictory input.',
        'Elapsed time and a staging directory MUST NOT be taken as proof of status.',
        _P('From the recorded approval until the section 14.2 matrix records its re-check matched, a path the approved plan enumerates as an outstanding retire, move or migrate source is bounded adoption state keyed on its section 14.2 lifecycle state, never on bytes alone.'),
        'In the planned and eligible-green states, where its live bytes still match its plan digest, containment MUST report it as migration_incomplete detail rather than failing it, at "none" during a clean start as much as at "partial".',
        'A digest mismatch in those states (a drifted source) or an unenumerated path MUST remain a containment-gate failure at required; the bounded treatment is never a blanket exemption.',
        "A deferred view destination (section 14.2), a plan-enumerated migrate, move or retire source whose digest-matched live bytes occupy a declared generated-view destination, receives the same bounded treatment for view drift: doctor and the render drift gate MUST validate that view's generated bytes prospectively, from the store, without reading the occupied path as the view, and MUST report the pending cutover as migration_incomplete detail rather than a drift failure, so a store awaiting its authorized cutover, before or after green eligibility, reaches VALID with the source bytes still in place.",
        'A digest mismatch at an occupied path in those states MUST remain a failure at required, exactly as for containment.',
        'In the cutover committed state, doctor MUST compare the destination against the poststate the cutover journal recorded at commit, MUST report the owed re-check as migration_incomplete detail, and MUST NOT fail the destination for no longer holding the source bytes.',
        "In the re-check failed state, doctor MUST fail at required naming each mismatched path; that recorded standing failure MUST NOT deadlock recovery under required posture: the sanctioned writer's own final doctor MUST accept exactly those recorded failed-re-check findings, and no other finding, as standing rather than caused by its change when the change touches no affected destination, and investigation, planning and the recording of the fresh plan's approval MUST remain available, so the section 14.1 fresh plan that discharges the failure can always be produced and executed without hand-editing canonical files.",
        _D("Adoption coverage (which types are populated, which modules are wired, how much of the project's operational surface has moved into the store) is a report, never a gate: breadth of adoption is a journey, and failing a build over it would train bypasses."),
        'It MUST stay report-only at every posture.',
        'Defaults: scaffolding and clean-start adoption MUST write posture = "required" and import_status = "none".',
        'An adoption with migrate-disposed sources keeps required and MUST hold import_status = "partial" until each source\'s completion check is green and its cutover has committed with a matched re-check (section 14.1), then "complete".',
        'Import status MUST NOT weaken posture.',
        'Reports MUST carry migration_incomplete while an approved source or detected file remains unresolved.',
        "Weakening the posture (required toward warn or off) is a guardrail-configuration change: it MUST take effect only through the maintainer's explicit, recorded authorization, separate from adoption approval, and MUST NOT be self-applied by the assistant or by tooling.",
        "A profile MAY raise, and MUST NOT lower, the effective posture: the effective posture is the strictest of the base posture and every supported profile's posture_floor.",
        "Weakening the base posture remains a guardrail-configuration change under the maintainer's recorded authorization; a profile floor is additive and MUST NOT substitute for that authorization in the loosening direction.",
    ),
    "12": (
        'Rotation MUST be relocation, never deletion, and never ID reuse.',
        'Records in unqualified terminal states, other than worklog records, MAY rotate to .working/toml/archive/<YYYY>/ (calendar-year buckets) on manifest-declared age or size thresholds.',
        'Worklog records are excluded from that generic terminal-record permission and rotate solely under the release-based rule: only released, frozen worklog spans MAY rotate, and the unreleased recorded tail never rotates whatever its age or size, because it is pre-terminal and mutable-until-release (sections 6.2 and 8.4).',
        'Open records, active blocks, unresolved decisions, unresolved fragments, unexpired waivers, the current handoff, and the unreleased worklog tail MUST NOT rotate.',
        'The imported series MUST NOT rotate at 1.3.0: <type>.imported.index.toml and worklog.imported.toml sit outside the rotation thresholds and their records MUST NOT move to the record archive.',
        _D('The record-rotation archive under the discovered machine store is distinct from the store-tree archive/ in section 4.2, which retains relocated adopter files and adoption preimages, never rotated records.'),
        'Tooling MUST NOT scan either as the other.',
        'Imported originals, acceptance evidence, and retire preimages MUST be retained indefinitely by default.',
        'Automatic reclamation MUST apply only to staging runs, after independent re-read and digest verification of their required evidence in its durable home; age alone MUST NOT authorize deletion.',
        "Each rotation MUST write the year's archive.toml, enumerating every moved ID (and, for the worklog, every moved span) and its destination.",
        "Validation MUST confirm that every ID exists in exactly one active or archived location, and coverage gates MUST read active and archive together, so rotation never changes any gate's answer.",
        'counters.toml MUST remain untouched by rotation, preserving ID permanence.',
        'Retention is thereby indefinite by default; an adopter bound by a retention policy MUST apply it as a recorded maintainer decision governing archival and rotation (aged data moved into the archive, preserved byte for byte), never as deletion of a record.',
    ),
    "14": (
        _D('opf adopt guides setup and wiring; opf import is only the post-adoption import activity.'),
        _D('Relocating the store remains opf migrate (section 5.4).'),
        _D('Clean start is first-class: preserve and retire old operational files, establish the new store and enforcement, and import nothing.'),
        _D('This makes no claim that historical obligations were fulfilled or converted.'),
        _D('Adoption follows investigate, plan, one approval, apply, completion, then retirement.'),
        'Investigation MUST distinguish first adoption from re-adoption and MUST record a digest-stamped inventory, including governance surfaces for each supported assistant platform.',
        'Every foreign .working/ file, and every pre-existing file at a declared view or deliverable destination outside .working/, the product-root VERSION included, MUST have a disposition before init-store; adoption MUST NOT run blind init over populated content.',
        'Apply MUST compose the coupled-init substrate and the journaled adoption operations, with per-operation preimage checks and reversal.',
        'It MUST end with rendered views at every declared destination except the deferred view destinations of section 14.2, whose occupying sources it MUST leave byte-identical in place, and with an adoption receipt plus its outcome-event chain.',
        'A bootstrap views-ready milestone alone MUST NOT count as adoption success.',
    ),
    "14.1": (
        'Exactly one adopter approval MUST follow each concrete plan.',
        "The opf.adoption.plan/v2 plan MUST bind: - product and store identities and the observed revision; - every source path, byte digest, disposition and preservation destination; - exact creations, replacements, removals and consumer repointings; - tool release identity, including manifest_sha256 checked against an independent anchor; - the version and digest of the prompt pack; - enforcement-pack contents per platform and each platform's disclosed residual coverage; - the completion-check roster and the rule that retirement requires green checks and matching bytes; - the missingness and unparsed-content policy, the skip policy under which a migrate-disposed source may be recorded as skipped, and the migrate-disposed sources import may touch.",
        'The attributed approval MUST bind plan_digest and inventory_digest, hence that whole plan.',
        'A per-fragment acceptance or later adopter checkpoint MUST NOT occur within the approved plan.',
        'Any bound-item drift MUST refuse into a fresh plan with its own single approval; a changed old file MUST NOT be retired.',
        "One renewal is not drift: a section 5.4 whole-store relocation MUST rebind the plan's bound paths to their relocated equivalents with the bound digests unchanged (section 14.2).",
        'Approval MUST NOT absorb a separate posture-weakening authorization (section 11) or changelog curation (section 7.3).',
        _D('Digests establish binding, not actor authenticity or semantic correctness; self-asserted identity and same-user tampering remain disclosed residuals.'),
        'The clean-start completion check MUST deterministically verify the following roster: 1.',
        _D('Authority and freshness: roots, destinations and live preimages still match the approved plan.'),
        _D('2. Discovery accounting: every inventory entry has a disposition or recorded exclusion.'),
        _D('3. Preservation and restore: each retirement preimage exists, digest-matched, under .working/archive/adoption/<run-id>/, and a restore exercise reproduces its bytes.'),
        _D('4. Operational readiness: the store resolves to the planned identity, and CI asserts store presence and identity so absence cannot pass as NOT-APPLICABLE.'),
        _P('Doctor validity and declared-view byte drift are evaluated against the validated prospective poststate in which every disposition and same-path publication this adoption itself executes has run, while a migrate-disposed source, whose cutover belongs to import completion, stays in place at its original or bootstrap path (section 14.2) and any view destination it occupies stays deferred, so a plan-enumerated old file still occupying its planned destination is not a deadlock, and any drift the plan does not account for fails the check.'),
        _D('Consumer repointings match the plan.'),
        '5. Wiring: the enforcement pack is installed and probed, with a direct store write denied and sanctioned writer and render paths succeeding under the same deferral as check 4: a probe MUST NOT write a deferred view destination, its render output for such a destination is validated against the prospective poststate, and the probe verifies that the sanctioned paths withhold publication there until the authorized cutover transaction has replaced the source and its post-commit re-check has matched (the section 14.2 matrix), never merely until eligibility.',
        _D("Server-side branch protection is adopter-attested, explicitly outside the local probe's guarantee."),
        _D('6.'),
        _D('Retirement readiness: each retire-disposed file is present in the live tree and its live bytes still equal its plan digest.'),
        'Retirement MUST occur only on a green completion check, and a green result evaluated against a prospective poststate is retirement eligibility, never completed adoption by itself: eligibility ends no deferral and authorizes nothing outside the cutover transaction.',
        'Sanctioned record writes between eligibility and cutover change rendered views without ending eligibility, so the cutover transaction MUST re-derive its poststate, every published view byte included, from the store under its own held lease, and its journal MUST record the digests of that re-derived poststate at commit: that journal-recorded poststate is the one postimage every later recovery and re-check compares against.',
        "The cutover's lease MUST span from before its commit until its re-check outcome is recorded; lease reconciliation (section 5.7) MUST NOT release a dead cutover holder's lease without either recording the owed re-check outcome through the journal-led recovery below or leaving the destination under its cutover committed protection, and every roster command MUST settle an owed re-check outcome, by running and recording it or by refusing, before any store write (section 8.8).",
        "Adoption MUST NOT be recorded complete until the journaled cutover transaction that executes the dispositions and same-path publications this adoption itself performs has committed, and a post-commit re-check has found the landed live poststate equal to that re-derived poststate, digest for digest, with a live full doctor VALID, recorded as its own outcome event in the receipt's chain.",
        'An interrupted or rolled-back cutover leaves adoption incomplete whatever green result preceded it; the eligibility stands, the cutover MUST be retried or recovered under the same approved plan, and neither the re-check nor the retry needs a new adopter approval.',
        "Recovery is journal-led: while the journal shows no commit, recovery MUST confirm or restore the prestate, and the live preimage MUST still match the plan digest before a retry commits; once the journal shows the commit, recovery MUST recognize the landed postimage, run and record the pending re-check against the journal-recorded poststate, and MUST NOT demand the replaced preimage, so a crash between the commit and the re-check's recording is resumable, never a deadlock.",
        'A completion-check failure or cannot-evaluate MUST report incomplete and MUST retire nothing; changing the approved work MUST take a fresh plan.',
        "A post-commit re-check that does not match is a recorded failed outcome event with a fail-closed result: the landed bytes and every preserved preimage MUST be retained, the section 14.2 roster commands MUST NOT write the affected destination, doctor MUST fail at required naming each mismatched path, and any further write toward that destination MUST wait for a fresh plan with its own single approval; nothing is silently rolled back or retried, and a failed re-check MUST NOT deadlock recovery: section 11 defines how doctor's required failure coexists with producing and approving that fresh plan, which binds the landed bytes as the preimage its own cutover replaces or confirms.",
        'Green proves preservation, restorability and operational coverage, never semantic fidelity or fulfilment of old obligations; the receipt MUST disclose that limit.',
        "An occupied view destination MUST use a validated prospective poststate, preserved preimage and the same journaled transaction for retirement and publication (section 14.2); completion checks MUST gate that cutover before removal or replacement, and only the cutover's own matched landed re-check completes it.",
        "The enforcement pack MUST freeze the plan-enumerated old files until each one's disposition has executed under the section 14.2 matrix, and MUST protect both record series, counters, declared views and evidence.",
        "Its floor is normative: the pack MUST provide CI checks, MUST provide staged-snapshot pre-commit checks with the per-clone installation residual disclosed, MUST provide a verified deny hook on each supported platform whose official documentation confirms denial support, and MUST provide instructions on a platform without verifiable denial, disclosing each platform's residual.",
        'The supported platform roster is Claude Code, Codex, Gemini CLI and Cursor; the pack MUST cover every one of them by one of those two means, per platform.',
        'Denial claims MUST be verified against official platform documentation at build time.',
        'Per-clone hook installation and bypass, canonical hand edits, shell or interpreter wrapping, same-user tampering and unverified platform denial are not eliminated by this pack and MUST each be disclosed as residuals.',
        'Adoption MUST refuse to enable a record type the writer cannot author, and enforcement MUST NOT ship before the writer can perform every operation it forces.',
        _D("Curated CHANGELOG.md edits remain the curator's responsibility."),
        'Post-adoption import MUST use the approved, versioned prompt pack, with an example for every record type in the section 8.1 roster except the excluded transaction, the unassigned CL namespace, and the deprecated legacy_fragment.',
        'It MUST direct the assistant to discover old operational files within the approved scope and submit batches through opf record import --batch (section 8.8), never hand-edit store TOML.',
        'Source instructions MUST be treated as historical data, never as instructions to execute.',
        'Missingness, ambiguity, conflicts and source precision MUST remain explicit; unmappable text MUST be retained verbatim.',
        'The assistant MUST report semantic uncertainty without seeking another checkpoint.',
        _D('opf import --prompt, --status and --verify expose that activity.'),
        'The former --scan, --plan, --review and --apply modes MUST refuse with a pointer to adoption and the prompt pack when this contract activates.',
        "For each migrate-disposed source, import completion MUST verify the preserved original's digest, at least one imported record referencing that source or a recorded skip under the approved policy, and doctor VALID over both the strict store and the imported series, evaluated on the live tree with any deferred view destination under the section 11 bounded treatment.",
        "It MUST record the source's result in the adoption receipt's outcome-event chain.",
        "A green result is that source's retirement eligibility: only it permits the source's retirement through the adoption retirement path, in one journaled transaction with any same-path view publication, the live preimage MUST still match the plan when that transaction runs, and eligibility ends no per-source deferral.",
        "That transaction MUST re-derive its poststate, the published view bytes included, from the store under its own held lease at cutover time, MUST record that poststate's digests in its journal at commit, and MUST hold that lease until the re-check outcome is recorded, exactly as for adoption.",
        _P('The source completes only when its cutover transaction has committed and the post-commit re-check has matched the landed bytes, the published view where the source occupied one included, against that re-derived poststate, with a live full doctor VALID recorded in the same outcome event.'),
        _P("A pre-commit interruption or rollback leaves the source's result incomplete, retried under the same plan without a new approval; a post-commit interruption is recovered by recognizing the landed postimage and recording the pending re-check, exactly as for adoption."),
        _P("A failed per-source re-check takes the same fail-closed outcome as adoption's, recorded against that source."),
        'import_status MUST become "complete" only when every migrate source completes on those terms; import is never complete on a state that is not doctor-VALID.',
        _D('This proves source accounting and preservation, not byte-level mapping coverage or semantic fidelity.'),
        _D('No writer-side leftover accounting is required.'),
        'Clean-start adoption and import ship on homes 1 with explicit evidence coverage: their completion checks MUST re-read inventories and payload digests themselves, because C-EVIDENCE-ENUM is inactive until homes 2.',
        _D('The evidence-bundle format in section 4.2 is retained, including the import home .working/imported/import/<run-id>/.'),
        'Destination durability and digest verification MUST precede source removal; retirement and any same-path publication MUST share the same recoverable transaction.',
        'A source outside the participating roots MUST be a plan-time cannot-evaluate naming that source.',
        'Investigation, planning and read-only status commands MUST remove nothing.',
        'Legacy runs MAY occupy .working/staging/import/<run-id>/ or .working/staging/ingest/<run-id>/.',
        'Their evidence inventories and readers remain available; old acceptance records describe those runs and MUST NOT authorize a new adoption or retirement.',
        'A legacy import completed under the pre-1.3.0 contract MUST keep its recorded status, substantiated by its preserved run evidence (section 11); a retrospective approval or receipt MUST NOT be fabricated.',
        "Preserved legacy run evidence is that run's durable archive in its recorded legacy home, for the reference tooling .aiqt/import-archive/<run-id>/, holding the run's acceptance record and its evidence inventory in the retained legacy format; substantiation MUST re-read that inventory and digest-match every file it enumerates, and a missing, unreadable or digest-mismatched item MUST leave the status unsubstantiated, failing closed under section 11.",
        'Old import and ingest orchestration MUST be retired by staged decoupling only after clean-start adoption ships.',
        'Required evidence MUST be re-read and digest-matched in its durable home before staging reclamation.',
        'Reclamation MUST be journaled and idempotent; an unreadable tree MUST hold the run.',
        'Read-only commands MUST NOT clean staging.',
        'Pending legacy review MUST NOT become implicit approval.',
    ),
    "14.2": (
        'opf adopt MUST investigate every file at the target .working/ location that is not OPF-managed, and every pre-existing file at a declared view or deliverable destination outside .working/, the product-root VERSION included: the manifest, ledgers, counters, clean and imported typed indexes, declared views and registered control areas define the managed set.',
        'A matching pathname alone does not prove OPF ownership: foreign content at a planned managed destination, a machine-store path as much as a view path, MUST still take a disposition.',
        _D('A hand-maintained TODO.md belongs to the adopter.'),
        'opf init MUST refuse undispositioned foreign content; post-adoption import MUST use only its approved source scope and MUST NOT authorize an incidental discovery.',
        'The plan MUST record one disposition per foreign file from keep, migrate, move, retire: - **Keep.** Leave it untouched and register it under [unmanaged].',
        'Ordinary tooling MUST NOT read, rewrite or delete it.',
        'An unmanaged path MUST NOT collide with an OPF-managed file or view: a keep declaration naming a declared view or managed store path MUST refuse at plan time, and a later manifest change, type enablement or upgrade delta that would declare a view or managed store path at a registered [unmanaged] path MUST refuse the same way (section 9.2), so a kept path never becomes an occupied destination.',
        _D('- **Migrate.** Keep the source frozen now for post-adoption import into the separate imported series.'),
        'Its exact bytes MUST be preserved, and it MUST be retired only after its source completion check is green, through the cutover transaction the matrix below governs.',
        'There MUST NOT be an imported view.',
        "Where an old file occupies a clean generated-view destination, that path is a deferred view destination whose protection is keyed on the matrix's lifecycle states below, never on the occupying bytes alone: deferral MUST last until the authorized cutover transaction has actually replaced the source and its post-commit re-check has matched (section 14.1), never merely until the applicable completion check is green.",
        "One deferral rule MUST bind every roster command of the matrix below: each validates the view's generated bytes prospectively and MUST withhold the same-path publication, the deferred path sits outside a writer's planned destinations so no cleanliness gate reads it, doctor MUST grade it under the section 11 bounded treatment, and no operation overwrites the source or requires the live tree to hold the generated bytes before the cutover.",
        _P('Validate the prospective poststate and preserve the preimage, then retire and publish in the same journaled transaction only after the applicable completion check is green, re-checking the landed bytes under section 14.1.'),
        "A pending migrate source MUST NOT be overwritten to make adoption pass: the occupied destination's publication waits, in that same transaction, for the green check.",
        _P('A retire-disposed source at a declared view destination defers identically, cut over by the adoption completion path.'),
        _D('- **Move.** Relocate to a named destination outside the managed store, or by default to .working/archive/moved/<source-path>, preserving substructure.'),
        'An occupied destination MUST be a collision finding, never an overwrite.',
        'An explicit destination inside the store tree MUST lie beneath .working/archive/moved/.',
        'The default Move MUST refuse a store resolved outside the product root until a multi-root coordinator exists.',
        'Removal MUST require the plan-bound green completion check and digest-verified preservation.',
        'A move-disposed source at a declared view destination defers identically to a migrate source: its relocation and the same-path publication share one journaled cutover transaction after the green check, and roster commands MUST NOT render over or relocate it beforehand.',
        '- **Retire.** The first-class clean-start choice: the exact preimage MUST be preserved under .working/archive/adoption/<run-id>/ with restore and completion proven before the removal or replacement, which runs without import through the adoption cutover transaction.',
        _D('No old obligation is thereby fulfilled.'),
        _P('A foreign file at a planned machine-store destination (the manifest, a ledger, counters.toml, a typed index or another required machine file) cannot defer like a view: the store cannot exist while foreign bytes occupy a file it must parse.'),
        'The plan MUST resolve such a collision at plan time and MUST NOT leave it to apply: either the adopter re-dispositions the file to move or retire under the ordinary rules, or the plan binds a bootstrap relocation for it.',
        _P('A bound bootstrap relocation means apply relocates the source byte-identically, digest-verified, to .working/archive/adoption/<run-id>/bootstrap/<source-path> in the same journaled apply transaction that creates the machine file, and every protection of this section, the freeze, the plan digests, the disposition and its lifecycle state, follows the relocated source: it remains the frozen source import reads for migrate, or the preserved file whose removal or move still awaits its green check.'),
        "A bootstrap relocation is not retirement and completes no disposition; the approval binds both paths, and it is the one authorized exception to the planned row's relocation prohibition: only apply's journaled transaction MAY perform it, exactly as bound, and no roster command MAY relocate the source otherwise.",
        'A plan that neither re-dispositions nor binds a bootstrap relocation for a machine-store collision MUST refuse at plan time, naming the path and those two remedies; a keep there already refuses.',
        _P('A view destination never takes a bootstrap relocation: it defers under the matrix below, and after a bootstrap relocation the machine-store destination is an ordinary managed file while the matrix binds the relocated source at its bootstrap path exactly as it binds an occupying source.'),
        _P('Occupied destinations follow one normative matrix.'),
        _P('The occupied-destination writer roster is closed and enumerates every writer this specification names: adoption apply and its journaled adoption operations, the section 14.1 adoption and per-source cutover transactions and their journal-led recovery, the section 8.8 authoring-transaction reconciliation, opf render, every opf record subcommand (create, transition, done-with-receipt, worklog-append, import), opf import, opf upgrade in both its schema-level and homes-generation forms, the homes-2 relocation path included, opf migrate, opf sync, opf init, opf absorb, rotation (section 12), the release cut (section 6.1), staging reclamation (section 14.1), the check 3 restore exercise, and every check 5 probe.'),
        "Every command or recovery path that can publish or regenerate a view or write a store path is a roster command and MUST obey the matrix; such a command MUST NOT ship outside the roster, and any operation outside the roster MUST NOT publish or regenerate a view or write a store path. opf init MUST keep refusing undispositioned foreign content rather than write over it, and opf sync MUST NOT itself publish or regenerate anything at a protected destination: a fast-forward whose incoming commits change a protected occupied destination MUST refuse and surface the state unless the incoming history carries that destination's own authorized cutover and its recorded outcome, and a divergence always halts (section 5.7). opf migrate --store is whole-store relocation (section 5.4), never disposition execution: it MUST carry every protected source, preserved preimage, bootstrap relocation and evidence bundle byte-identically into the destination with its lifecycle state and deferral intact, MUST NOT execute, advance or end any disposition, deferral or owed re-check, and MUST renew the approval's bound paths to their relocated equivalents in the same recorded change, the bound digests unchanged; any byte difference remains bound-item drift refusing into a fresh plan (section 14.1).",
        _P("The matrix's relocation prohibitions bind disposition-scoped and same-path writes, not that whole-store carry, and after the carry every cell binds at the relocated paths."),
        _P('An occupied destination is in exactly one lifecycle state at any observation point: planned (disposition approved, its completion check not yet green); eligible-green (check green, cutover transaction not yet committed); cutover committed (transaction committed, its post-commit re-check outcome not yet recorded); re-check matched (the recorded re-check matched the landed bytes); re-check failed (the recorded re-check did not match); and drifted (live bytes at a planned or eligible-green destination no longer matching the plan digest).'),
        'Interrupted-recovered is not a further concurrent state but the journal-led reconciliation transition of section 14.1: before any other roster write it MUST resolve to eligible-green when the journal shows no commit, or to cutover committed, the re-check still owed, when it does, and its row below binds that transition.',
        'Protection MUST last until the authorized cutover transaction has actually replaced the source and its re-check has matched, never merely until eligibility.',
        _D('Every cell below binds every roster command of the closed roster above, identically for each command.'),
        _D('| State | Keep | Migrate | Move | Retire |'),
        _D('|---|---|---|---|---|'),
        '| planned | unreachable: the colliding declaration MUST have refused at plan time, and roster commands MUST NOT touch a kept path | roster commands MUST withhold same-path publication, MUST validate the view prospectively, and MUST NOT overwrite, relocate or remove the source | roster commands MUST withhold same-path publication, MUST validate the view prospectively, and MUST NOT overwrite, relocate or remove the source | roster commands MUST withhold same-path publication, MUST validate the view prospectively, and MUST NOT overwrite, relocate or remove the source |',
        '| eligible-green | unreachable: the plan-time refusal stands, and roster commands MUST NOT touch a kept path | protection MUST persist unchanged, and only the section 14.1 per-source cutover transaction MAY remove the completed source, its preserved original retained, and publish the view | protection MUST persist unchanged, and only its journaled cutover transaction MAY relocate the source and publish the view | protection MUST persist unchanged, and only the adoption cutover transaction MAY remove or replace the source and publish the view |',
        '| cutover committed | unreachable: a kept path never enters a cutover, and roster commands MUST NOT touch it | the destination holds the landed postimage, roster commands MUST NOT write it until the re-check outcome is recorded, and every roster command MUST settle the owed outcome, by recording it or refusing, before any store write (section 14.1) | the destination holds the landed postimage, roster commands MUST NOT write it until the re-check outcome is recorded, the relocated source MUST stay digest-verified at its move destination, and every roster command MUST settle the owed outcome before any store write | the destination holds the landed postimage, roster commands MUST NOT write it until the re-check outcome is recorded, the preimage MUST stay preserved under .working/archive/adoption/<run-id>/, and every roster command MUST settle the owed outcome before any store write |',
        '| re-check matched | unreachable: a kept path never enters a cutover, and roster commands MUST NOT touch it | deferral ends: the destination is an ordinary declared view, ordinary roster behaviour resumes, and the preserved original MUST stay retained | deferral ends: the destination is an ordinary declared view, ordinary roster behaviour resumes, and the relocated file MUST stay at its digest-verified destination | deferral ends: the destination is an ordinary declared view, ordinary roster behaviour resumes, and the preserved preimage MUST stay retained |',
        '| re-check failed | unreachable: a kept path never enters a cutover, and roster commands MUST NOT touch it | fail-closed: the failed outcome event is recorded, the landed bytes and the preserved original MUST be retained, roster commands MUST NOT write the destination, doctor MUST fail at required, any further write there MUST wait for a fresh plan with its own single approval, and that fresh plan remains producible under the section 11 standing-finding treatment | fail-closed: the failed outcome event is recorded, the landed bytes and the relocated file MUST be retained, roster commands MUST NOT write the destination, doctor MUST fail at required, any further write there MUST wait for a fresh plan with its own single approval, and that fresh plan remains producible under the section 11 standing-finding treatment | fail-closed: the failed outcome event is recorded, the landed bytes and the preserved preimage MUST be retained, roster commands MUST NOT write the destination, doctor MUST fail at required, any further write there MUST wait for a fresh plan with its own single approval, and that fresh plan remains producible under the section 11 standing-finding treatment |',
        '| drifted | unreachable: a kept path carries no plan digest to drift from, and roster commands MUST NOT touch it | fail-closed: roster commands MUST NOT render over, write or relocate the path, the disposition MUST NOT execute, the changed old file MUST NOT be retired, doctor MUST fail at required (section 11), and the remedy MUST be a fresh plan with its own single approval | fail-closed: roster commands MUST NOT render over, write or relocate the path, the disposition MUST NOT execute, the changed old file MUST NOT be moved or removed, doctor MUST fail at required (section 11), and the remedy MUST be a fresh plan with its own single approval | fail-closed: roster commands MUST NOT render over, write or relocate the path, the disposition MUST NOT execute, the changed old file MUST NOT be retired or replaced, doctor MUST fail at required (section 11), and the remedy MUST be a fresh plan with its own single approval |',
        '| interrupted-recovered | unreachable: a kept path never enters a cutover, and roster commands MUST NOT touch it | journal-led: with no recorded commit the prestate is confirmed or restored and the state returns to eligible-green, and with a recorded commit the pending re-check MUST compare the landed postimage against the journal-recorded poststate, never demand the replaced preimage | journal-led: with no recorded commit the prestate is confirmed or restored and the state returns to eligible-green, and with a recorded commit the pending re-check MUST compare the landed postimage of the view and the relocated source against the journal-recorded poststate, never demand the replaced preimage | journal-led: with no recorded commit the prestate is confirmed or restored and the state returns to eligible-green, and with a recorded commit the pending re-check MUST compare the landed postimage against the journal-recorded poststate, never demand the replaced preimage |',
        'Detection MUST surface unresolved files in the plan and MUST NOT silently absorb, delete or overwrite them.',
        'Dispositions are plan data and MUST be covered by the single approval, never by separate approvals per file.',
        'An unreadable declaration or detected input MUST fail closed.',
        'Reports MUST retain migration_incomplete until the declared work is resolved.',
        _D('The reserved children archive/, imported/, staging/, and journals/ are OPF control area.'),
        'Detection MUST NOT surface them as adopter content, an adoption option MUST NOT select them, and an [unmanaged] declaration MUST NOT equal, contain, or lie within them.',
        "Adoption evidence MUST be committed and immutable under .working/imported/adoption/<run-id>/; append-only outcome events retain the receipt's history.",
        _D('In homes 2, transaction records live under .working/journals/adoption/; homes 1 retains its legacy journal paths and completion-carried evidence checks.'),
        "After adoption, containment uses the receipt-bound import_status and the bounded treatment of section 11, keyed on the matrix's lifecycle states: a plan-enumerated outstanding retire, move or migrate source, digest-matched in the planned or eligible-green state, MUST be reported rather than failed until its disposition executes, whatever the status, and a drifted or re-check failed state MUST fail at required exactly as section 11 defines.",
        'An unregistered path outside that enumerated scope MUST fail containment at required.',
        'No source is implicitly imported, and staging presence MUST NOT grant authority.',
    ),
    "14.3": (
        _D('An adopter with an existing single-source release pipeline (for example, a release-notes TOML that generates a version file and a changelog) migrates by recording its releases in version.toml as imported-flagged [[release]] rows (the optional imported flag of section 6.1) and its per-release notes as published per-release [[summary]] rows.'),
        'Pre-migration releases have no worklog entries: their spans MUST be empty and their summary digests MUST be recorded as imported facts, flagged as resting on imported provenance rather than on a witnessed release cut.',
        'This remains the historical-release path at 1.3.0: the ledger is not an imported record type, its spans MUST NOT refer to imported:WL IDs, and import MUST NOT create a separate version or changelog TOML file.',
        "Changelog prose MUST still come from the curator's act.",
    ),
    "15": (
        "A conforming store's base-required schema vocabulary MUST NOT name a particular adopter, operator, profile, internal system, endpoint, tier vocabulary, or command.",
        _D("A profile schema (for example [profiles.aiqt]) legitimately names its owner, and a conforming store's own data legitimately names an operator via actor.id; the neutrality constraint binds the base-required vocabulary, not profile schemas or store data."),
        'actor.kind MUST carry only the portable categories; identity detail lives in actor.id or extensions.',
        'mode, tier_assessment, and waiver MUST ship structure only (evidence, assessor, outcome, validity, scope, expiry) with adopter-supplied vocabularies.',
        _D('The location patterns of section 5.3 are described generically; no pattern names a real repository, host account, or internal system.'),
        "Import and adoption provenance, including originals under imported/ and retired files under archive/, MUST stay inside the adopter's own repositories.",
        _D('In homes 2, .aiqt/ is AIQT-owned material, not an OPF state home; OPF operates without it.'),
        'Only homes migration MAY read explicitly inventoried OPF artefacts from former .aiqt/ locations, without touching unrelated AIQT material.',
        'Experimental fields MUST ride registered x-<vendor> tables only, within the limits of section 8.7.',
        _D("The base standard's required schema vocabulary names no adopter, operator, or profile by definition; a conforming store's own data legitimately may (an operator via actor.id, a profile via a [profiles.<name>] table the adopter chose)."),
        _D("AIQT appears in the standard's title and brand as trademark and authorship attribution (the standard is authored and maintained by its lead maintainer), which is attribution rather than a requirement dependency; AIQT is also one profile, [profiles.aiqt], cited only as the reference enforcement suite and a consumer."),
        _D("A profile carries an adopter's own additional requirements without the base ever depending on them."),
    ),
    "17": (
        _D("The gates in this standard are strong where they are strong and say so where they are not: - The freeze gate proves a published summary's bytes changed only through recorded re-publication; it cannot prove the prose is accurate or complete."),
        _D('Human curation (section 7.3) is that control.'),
        _D('- Range coverage proves every release is summarized exactly once; it cannot judge summary quality.'),
        '- The unreleased worklog tail is mutable until release: an entry there MAY be corrected in place, a guarantee that rests on review and version-control history rather than machine enforcement; machine freezing and immutability begin at release cut.',
        _D("- On homes 1, under the section 4.2 target contract and only once activated 1.3.0 tooling ships, the adoption and import control areas (.working/imported/<kind>/<run-id>/, .working/archive/adoption/<run-id>/ and .working/archive/moved/) are recognized by containment but not re-enumerated by doctor: the completion checks verify their evidence digests when they run, and C-EVIDENCE-ENUM is inactive until homes 2 (section 4.2), so a file added or altered there after completion, a tampered retire preimage no record references included, is outside doctor's coverage until homes 2 activates."),
        _D("This bullet discloses that target contract's residual and claims no shipped tooling behaviour."),
        _D("Version-control history over the tracked store is the control, and activated 1.3.0 tooling's C-IMPORTED-PROVENANCE still re-verifies every preserved original an imported record references."),
        _D('- Store resolution fails closed on a pointer that does not resolve and on zero or multiple manifests at the target; it cannot detect a second store that no pointer names, placed somewhere the tooling was never aimed.'),
        _D("- The tracked-store check verifies the resolved store is under version control, not ignored, and that the pointer, the manifest's recorded sync target, and the actual remote agree; it cannot verify the backup, access, or hosting discipline of the repository that tracks the store."),
        "The local-only pattern in particular places durability wholly on the adopter's own backup, which is why choosing it SHOULD be a recorded decision (section 5.3).",
        _D('- The single-writer lease has a propagation window: between a lease being taken and its becoming observable at the sync target, two systems can both begin.'),
        _D("The consistency contract's divergence check is the overlapping control that catches that collision after the fact; the two layers together, not the lease alone, are the guarantee (section 5.7)."),
        _D("- A host provider's create and auth conveniences call the external API of the host the target names."),
        _D("The egress bound is that named host and nothing else; the standard cannot vouch for the host's own behaviour beyond that bound."),
        _D("- Public deliverables reach the product repository through opf render under the product repository's normal review flow (section 5.8); the quality of that review flow is the adopter's own discipline, which this standard requires to exist but does not itself gate."),
        _D('- A base-only tool does not evaluate profiles: a store could satisfy the base and violate a profile it declares, and a base-only tool would not detect it.'),
        _D('This is by design (profiles are additive and a base tool is out of their scope), and it is the reason a conformance report always names which profiles it did and did not evaluate.'),
        'Fail-safe-for-unknown-profiles is scoped to a tool that does not cover the profile; a profile-aware tool MUST fail closed on its own profile.',
        _D('- The base discovery token opf is a single exact string carried in every adopter manifest.'),
        _D('A mistyped or altered token makes the store undiscoverable, which resolves to cannot-evaluate (fail-closed), never to a silent empty store.'),
        "The token is stable within a base-schema major line; a store-breaking rename MUST ship only with the tested opf upgrade migration (section 9.2), which rewrites the base table and token in place so no existing adopter's manifest is stranded.",
        _D('The retired 1.0.0 token devprocess is recognized by opf upgrade alone, purely to carry a legacy store forward.'),
    ),
}


def _sections(text):
    # Every heading bounds a section, so a numbered section never swallows an appendix; the
    # preamble before the first heading is registered as section "0".
    bounds = list(re.finditer(r"^#{2,3} .*$", text, re.MULTILINE))
    sections = dict()
    if bounds:
        sections["0"] = text[:bounds[0].start()]
    for i, m in enumerate(bounds):
        number = re.match(r"^#{2,3} ([0-9]+(?:\.[0-9]+)?)\.? ", m.group(0))
        if number:
            end = bounds[i + 1].start() if i + 1 < len(bounds) else len(text)
            sections[number.group(1)] = text[m.end():end]
    return sections


def contract_findings(text):
    sections = _sections(text)
    findings = []
    for section, fragments in _CONTRACT.items():
        body = " ".join(sections.get(section, "").replace("`", "").split())
        for fragment in fragments:
            if fragment not in body:
                findings.append("spec {} missing contract: {}".format(section, fragment))
    layout = sections.get("4.2", "")
    reservation = sections.get("4.4", "")
    for name in store.RESERVED_MACHINE_SUBDIRS:
        if "`{}`".format(name) not in reservation:
            findings.append("spec 4.4 missing reserved name: " + name)
    for name in store.STORE_TREE_CONTROL_DIRS:
        if name + "/" not in layout:
            findings.append("spec 4.2 missing control home: " + name)
    for kind in store.STAGING_KINDS:
        if "`{}`".format(kind) not in layout:
            findings.append("spec 4.2 missing kind: " + kind)
    blocks = re.findall(r"^```gitignore\n(.*?)^```$", layout, re.MULTILINE | re.DOTALL)
    if len(blocks) != 1 or blocks[0] != store.render_homes_gitignore():
        findings.append("spec 4.2 managed gitignore block differs from renderer")
    return findings


def keyword_findings(contract=None):
    # Section 2 keyword lint over every tiled section (the sections the 1.3.0 bump touches).
    # Section 2 reads a sentence without MUST, MUST NOT, SHOULD or MAY as descriptive, so an
    # unmarked keywordless pin is a requirement that states no requirement. Tiling ties each pin
    # to its spec sentence, so the lint reads the registry.
    contract = _CONTRACT if contract is None else contract
    findings = []
    for section, fragments in contract.items():
        for fragment in fragments:
            keyword = _KEYWORD.search(fragment) is not None
            if isinstance(fragment, _PendingOccupiedView):
                if keyword or section not in _PENDING_SECTIONS:
                    findings.append("spec {} stale {} exemption: {}".format(
                        section, _PENDING_DECISION, fragment))
            elif isinstance(fragment, _Descriptive):
                if keyword:
                    findings.append("spec {} descriptive marker on a keyword sentence: {}".format(
                        section, fragment))
            elif not keyword:
                findings.append("spec {} requirement without a section 2 keyword: {}".format(
                    section, fragment))
    return findings


def _staged_generation_self_test(check):
    """Public-boundary refusals, including empty inventories and both run kinds."""
    from unittest.mock import patch
    import check_opf_import as gate
    import _opf_import as imp
    import _opf_ingest as ingest

    cases = (
        ("ordinary-unsupplied-generation-cannot", ((2, None),)),
        ("ordinary-invalid-generation-cannot", tuple(
            (ceiling, bad) for ceiling in (1, 2)
            for bad in (True, False, 1.0, 2.0, 1.5, float("nan"), "1", "2", 0, -1, 3))),
        ("ordinary-unsupported-generation-cannot", ((1, 2),)),
    )
    outcomes = {name: [] for name, _ in cases}
    unprobed, unopened, legacy, ordered = [], [], [], []
    for is_ingest in (False, True):
        for empty in (False, True):
            rd, files = imp._memory_ingest_run()
            rd.close = lambda: None
            if not is_ingest:
                for name in imp._INGEST_RUN_MARKERS:
                    files.pop(name, None)
                    rd.tree.pop(name, None)
                inv = rd.load_toml(imp.INVENTORY_NAME)
                proposals = [dict(p, _origin=p["origin"])
                             for p in rd.load_toml(imp.PROPOSALS_NAME)["proposal"]]
                files[imp.REPORT_MD_NAME] = imp._render_report_md(
                    inv["inventory_digest"], inv["fragment"], proposals, rd.path.name).encode()
            if empty:
                # Refusal must not depend on inventory rows or their consistency with other artefacts.
                files[imp.INVENTORY_NAME] = imp._emit_bytes(imp._build_inventory([])[0], imp.INVENTORY_NAME)
            expected_failures = {"transaction-schema", "transaction-consistency"}
            if empty:
                expected_failures.add("report-binding-digests")
                expected_failures.update(
                    ("ingest-source-binding", "ingest-report-reproducibility")
                    if is_ingest else ("proposals-artifact",))
            with patch.object(gate, "_RunDir", return_value=rd) as open_run, \
                    patch.object(gate, "_ingest_store_fd", return_value=None) as locate, \
                    patch.object(gate, "_staged_run_store_fd",
                                 side_effect=gate._GateError("synthetic store unavailable")) as transaction:
                for name, values in cases:
                    for ceiling, bad in values:
                        with patch.object(store, "SUPPORTED_HOMES", ceiling):
                            open_run.reset_mock()
                            locate.reset_mock()
                            transaction.reset_mock()
                            result = (gate.check_staged_run(rd.path) if bad is None
                                      else gate.check_staged_run(rd.path, homes=bad))
                            reason = ("was not supplied" if bad is None else "supplied homes generation")
                            outcomes[name].append(
                                tuple(result) == gate.EXPECTED_CHECKS and all(
                                    not ok and detail.startswith("cannot evaluate:")
                                    and str(rd.path) in detail and reason in detail
                                    for ok, detail in result.values()))
                            unopened.append(not open_run.called)
                            unprobed.append(not locate.called and not transaction.called)
                for ceiling in (1, 2):
                    with patch.object(store, "SUPPORTED_HOMES", ceiling):
                        baseline = list(gate._check_staged_run(rd, homes=1).items())
                        result = gate.check_staged_run(rd.path, homes=1)
                        # Registry-complete: every expected id exactly once (order is pinned by the
                        # baseline comparison below, since results are not emitted in registry order).
                        ordered.append(len(result) == len(gate.EXPECTED_CHECKS) and
                                       set(result) == set(gate.EXPECTED_CHECKS))
                        legacy.append(list(result.items()) == baseline and
                                      {cid: ok for cid, (ok, _detail) in result.items()} ==
                                      {cid: cid not in expected_failures for cid in gate.EXPECTED_CHECKS})
                        if ceiling == 1:
                            legacy.append(list(gate.check_staged_run(rd.path).items()) == baseline)
    for name, values in outcomes.items():
        check(name, lambda v=values: all(v))
    check("staged-generation-no-store-probe", lambda: all(unprobed))
    check("staged-generation-no-run-open", lambda: all(unopened))
    check("staged-generation-registry-complete", lambda: all(ordered))
    check("staged-generation-legacy-values-and-order", lambda: all(legacy))
    with patch.object(gate, "_gate_homes", side_effect=gate._GateError("generation policy sentinel")):
        check("staged-generation-shared-row-policy", lambda: gate._row_scope_error(
            ingest, [], None, 1) == "cannot evaluate: generation policy sentinel")


def _staged_root_self_test(check):
    """Exercise physical root binding independently of transaction support.

    Root depth, custom-machine, pointer and decoy cases are controls: they already pass on
    the predecessor. The detached homes-2 binding case discriminates the new refusal.
    """
    import errno
    import os
    import shutil
    from unittest.mock import patch
    import check_opf_import as gate
    import _opf_import as imp

    def grade(run, generation=2):
        with patch.object(store, "SUPPORTED_HOMES", 2):
            return gate.check_staged_run(run, homes=generation)

    def bound(run, root):
        with patch.object(store, "SUPPORTED_HOMES", 2):
            rd = gate._RunDir(run)
            try:
                fd = gate._staged_run_store_fd(rd, 2)
                try:
                    actual, expected = os.fstat(fd), os.stat(root)
                    return (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
                finally:
                    os.close(fd)
            finally:
                rd.close()

    def binding_refused(run):
        with patch.object(store, "SUPPORTED_HOMES", 2):
            rd = gate._RunDir(run)
            try:
                try:
                    fd = gate._staged_run_store_fd(rd, 2)
                except gate._GateError as exc:
                    return str(exc) == "no registered store binding for homes generation 2"
                os.close(fd)
                return False
            finally:
                rd.close()

    def refused(result, needle):
        return all(not result[cid][0] and needle in result[cid][1]
                   for cid in gate._TRANSACTION_CHECKS)

    with tempfile.TemporaryDirectory(prefix="opf-staged-root-") as tmp:
        base = Path(tmp).resolve()
        for kind in ("import", "ingest"):
            root = base / kind
            machine = root / ".working" / "custom"
            machine.mkdir(parents=True)
            (machine / "manifest.toml").write_text('[opf]\nstandard = "opf"\n', encoding="utf-8")
            run = gate._self_test_gate_generation_disk(
                root, "accepted" if kind == "import" else None, location=kind)
            check("staged-root-homes2-" + kind, lambda: bound(run, root))
            resolution = store.resolve_store(root)
            check("staged-root-custom-machine-" + kind, lambda:
                  resolution.status == store.RESOLVED and resolution.machine_dir == "custom"
                  and bound(run, resolution.store_root))
            product = base / (kind + "-product")
            product.mkdir()
            (product / store.POINTER_REL).write_text(
                '[store]\ntarget = "dir:{}"\n'.format(root), encoding="utf-8")
            resolution = store.resolve_store(product)
            check("staged-root-pointer-" + kind, lambda:
                  resolution.status == store.RESOLVED and resolution.store_root == root
                  and bound(run, resolution.store_root))
            check("staged-root-generation-mismatch-" + kind, lambda:
                  refused(grade(run, 1), "registered outside homes generation 1"))
            record = root / imp._txn_record_rel(run.name)
            record.parent.mkdir(parents=True)
            record.write_bytes(b"state =\n")
            # The depth-three decoy is absent; diagnose the actual root's corruption.
            check("staged-root-wrong-level-decoy-" + kind, lambda:
                  not grade(run)["transaction-schema"][0]
                  and "unreadable/unparseable" in grade(run)["transaction-schema"][1])
            record.unlink()
            clean = grade(run)
            check("staged-root-no-transaction-" + kind, lambda:
                  clean["staged-run-structure"] == (True, "")
                  and all(clean[cid] == (
                      True, gate._HOMES2_NO_LEGACY_TRANSACTION_DETAIL)
                      for cid in gate._TRANSACTION_CHECKS))
            # Inject only after binding: the real constructor has already classified both homes.
            real_bind, real_stage = gate._staged_run_store_fd, store.stage_run
            for error in (gate._BindingRefusal, RuntimeError):
                opened = []
                def bind_then_arm(rd, generation):
                    fd = real_bind(rd, generation)
                    opened.append(fd)
                    return fd
                def fail_kind(*args):
                    if opened:
                        raise error("kind-check sentinel")
                    return real_stage(*args)
                with patch.object(gate, "_staged_run_store_fd", side_effect=bind_then_arm), \
                        patch.object(store, "stage_run", side_effect=fail_kind):
                    observed = grade(run)
                closed = False
                if opened:
                    try:
                        os.fstat(opened[0])
                    except OSError as exc:
                        closed = exc.errno == errno.EBADF
                check("staged-root-kind-exception-{}-{}".format(error.__name__, kind), lambda:
                      bool(opened) and closed and refused(observed, "kind-check sentinel"))

            # Attempts belong to the coordinator, including open and rolled-back journals.
            # These real frames pin the standalone gate's deliberately narrower transaction scope.
            if kind == "ingest":
                import _journal
                import _opf_journal
                journal = root / store.journal_root(kind)
                journal.mkdir(parents=True)
                attempt = journal / _opf_journal.attempt_txn(kind, run.name, 1)
                attempt.mkdir()
                jfd = store._open_root_fd(journal)
                try:
                    _journal.publish(jfd, attempt, _journal.F_INTENT, dict(
                        txn=attempt.name, header=dict(kind=kind, run_id=run.name, attempt=1,
                                                     operation_id="synthetic-operation"), ops=[]))
                    for state in ("open", "rolled-back"):
                        if state == "rolled-back":
                            for frame in (_journal.F_RIP, _journal.F_RC):
                                _journal.publish(jfd, attempt, frame, {"txn": attempt.name})
                        check("staged-root-attempt-" + state, lambda:
                              _journal.classify_state(jfd, attempt) == state
                              and grade(run) == clean)
                    _journal.publish(jfd, attempt, _journal.F_INTENT, {"txn": attempt.name})
                    malformed = False
                    try:
                        _journal.classify_state(jfd, attempt)
                    except _journal.JournalError:
                        malformed = True
                    check("staged-root-attempt-malformed", lambda:
                          malformed and grade(run) == clean)
                finally:
                    os.close(jfd)
                shutil.rmtree(attempt)
            # A typed projection/journal is not interchangeable with durable review acceptance.
            # Probe both namespaces even when the staging kind differs; empty bytes still count.
            for txn_kind in ("import", "ingest"):
                typed = root / store.txn_record(txn_kind, run.name)
                typed.parent.mkdir(parents=True)
                typed_decoy = root / ".working" / store.txn_record(txn_kind, run.name)
                typed_decoy.parent.mkdir(parents=True)
                typed_decoy.write_bytes(b"state =\n")
                check("staged-root-typed-ignore-decoy-{}-{}".format(txn_kind, kind), lambda:
                      grade(run) == clean)
                projection = imp._emit_bytes(dict(
                    format="opf.journal.transaction/v1", kind=txn_kind, run_id=run.name,
                    state="complete", operation_id="synthetic-operation",
                    journal_rel=store.journal_root(txn_kind)), "typed projection")
                for label, payload in (("empty", b""), ("malformed", b"state =\n"),
                                       ("projection", projection)):
                    typed.write_bytes(payload)
                    observed = grade(run)
                    check("staged-root-typed-{}-{}-{}".format(label, txn_kind, kind), lambda:
                          refused(observed, "typed transaction evidence is not supported")
                          and all(observed[cid] == clean[cid] for cid in gate.EXPECTED_CHECKS
                                  if cid not in gate._TRANSACTION_CHECKS))
                    typed.unlink()
                typed.symlink_to(root / "absent-typed-target")
                check("staged-root-typed-symlink-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence cannot be classified"))
                typed.unlink()
                os.mkfifo(typed)
                check("staged-root-typed-fifo-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence cannot be classified"))
                typed.unlink()
                parent = typed.parent
                moved_typed = parent.with_name(parent.name + "-saved")
                parent.rename(moved_typed)
                parent.symlink_to(moved_typed, target_is_directory=True)
                check("staged-root-typed-parent-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence cannot be classified"))
                parent.unlink()
                moved_typed.rename(parent)
                journal = root / store.journal_root(txn_kind)
                journal.mkdir(parents=True, exist_ok=True)
                (journal / "lock").write_bytes(b"writer lock")
                check("staged-root-typed-empty-journal-{}-{}".format(txn_kind, kind), lambda:
                      grade(run) == clean)
                single = journal / run.name
                single.mkdir()
                check("staged-root-typed-journal-{}-{}".format(txn_kind, kind), lambda:
                      refused(grade(run), "typed transaction evidence is not supported"))
                single.rmdir()
                # Unreadable typed controls must not collapse into the absent positive above.
                real_read = gate._read_store_control
                def denied_typed(fd, rel):
                    if rel == store.txn_record(txn_kind, run.name):
                        raise gate._GateError("typed control denied")
                    return real_read(fd, rel)
                with patch.object(gate, "_read_store_control", side_effect=denied_typed):
                    check("staged-root-typed-unreadable-{}-{}".format(txn_kind, kind), lambda:
                          refused(grade(run), "typed control denied"))
            typed.write_bytes(b"state =\n")
            record.write_bytes(b"state =\n")
            check("staged-root-typed-retains-legacy-corruption-" + kind, lambda:
                  refused(grade(run), "typed transaction evidence is not supported")
                  and "unreadable/unparseable" in grade(run)["transaction-schema"][1])
            typed.unlink()
            record.unlink()
            decoy = root / ".working" / imp._txn_record_rel(run.name)
            decoy.parent.mkdir(parents=True)
            decoy.write_bytes(b"state =\n")
            check("staged-root-ignore-decoy-" + kind, lambda:
                  grade(run) == clean)
            os.mkfifo(record)
            check("staged-root-fifo-" + kind, lambda:
                  "not a regular file" in grade(run)["transaction-schema"][1])
            record.unlink()
            original = record.parent
            moved = root / "moved-control"
            original.rename(moved)
            original.symlink_to(moved, target_is_directory=True)
            check("staged-root-symlink-control-" + kind, lambda:
                  not grade(run)["transaction-schema"][0]
                  and "no-follow" in grade(run)["transaction-schema"][1])
            original.unlink()
            moved.rename(original)
            # Deny only the physical store ascent, after _bind_run_name has succeeded.
            # The run remains readable and its staged-data checks must still grade.
            real_open, real_home = os.open, gate._physical_home
            denied_steps = []
            def denied(path, flags, *args, **kwargs):
                if path == "..":
                    denied_steps.append(path)
                    raise PermissionError("ancestor denied")
                return real_open(path, flags, *args, **kwargs)
            def denied_home(rd, rel):
                with patch.object(gate.os, "open", side_effect=denied):
                    return real_home(rd, rel)
            with patch.object(gate, "_physical_home", side_effect=denied_home):
                observed = grade(run)
            check("staged-root-unreadable-ancestor-" + kind, lambda:
                  bool(denied_steps) and refused(observed, "ancestor denied")
                  and observed["staged-run-structure"] == clean["staged-run-structure"]
                  and observed["report-schema"] == clean["report-schema"]
                  and observed["artifact-digest-integrity"] == clean["artifact-digest-integrity"])
            check("staged-root-readable-ancestor-" + kind, lambda: grade(run) == clean)
            detached = base / (kind + "-detached") / "a" / "b" / run.name
            shutil.copytree(run, detached)
            check("staged-root-detached-legacy-" + kind, lambda:
                  all(grade(detached, 1)[cid] == (True, "no transaction record (run not yet applied)")
                      for cid in gate._TRANSACTION_CHECKS))
            check("staged-root-detached-binding-discriminator-" + kind, lambda:
                  binding_refused(detached))
            check("staged-root-detached-homes2-" + kind, lambda:
                  refused(grade(detached), "no registered store binding for homes generation 2"))
            other_kind = "ingest" if kind == "import" else "import"
            crossed = root / store.stage_run(other_kind, run.name)
            crossed.parent.mkdir(parents=True)
            run.rename(crossed)
            try:
                observed = grade(crossed)
                check("staged-root-cross-kind-" + kind, lambda:
                      observed["staged-run-structure"] == (
                          False, "staging kind does not match {} run content".format(kind))
                      and observed["report-schema"] == clean["report-schema"])
            finally:
                crossed.rename(run)
            check("staged-root-matching-kind-" + kind, lambda: grade(run) == clean)
            misplaced = root / ".working" / "staging" / "preview" / run.name
            misplaced.parent.mkdir(parents=True)
            run.rename(misplaced)
            try:
                check("staged-root-kind-mismatch-" + kind, lambda:
                      refused(grade(misplaced), "no registered store binding for homes generation 2"))
            finally:
                misplaced.rename(run)
            claimant = base / (kind + "-claimant")
            claimed = claimant / store.stage_run(kind, run.name)
            claimed.parent.mkdir(parents=True)
            claimed.symlink_to(run, target_is_directory=True)
            route = claimant / "route"
            route.symlink_to(root, target_is_directory=True)
            check("staged-root-ambiguous-" + kind, lambda:
                  refused(grade(route / run.relative_to(root)), "ambiguous second store claim"))


def boundary_self_test():
    """Exercise the read-only boundaries with explicit in-memory filesystem observations."""
    import contextlib
    import copy
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import patch
    import _journal as journal
    import _opf_adopt as adopt
    import _opf_check as doctor
    import _opf_import as importer
    import _opf_ingest as ingest
    import _opf_journal as home_journal
    import _opf_views as views

    failures = []
    checked = []

    def check(name, thunk):
        checked.append(name)
        try:
            if not thunk():
                failures.append(name)
        except Exception as exc:
            failures.append("{} ({})".format(name, exc))

    def refuses(thunk):
        try:
            thunk()
        except (ValueError, journal.JournalError, ingest._DetectError, views.ViewsError):
            return True
        return False

    def refusal(thunk):
        # The refusal message, or None when the thunk completed; any other exception propagates.
        try:
            thunk()
        except (ValueError, journal.JournalError, ingest._DetectError, views.ViewsError) as exc:
            return str(exc)
        return None

    class _Reached(Exception):
        """A mocked first I/O was reached: nothing refused the operation before it."""

    run = "imp-20260917T120000Z-0123456789abcdef"
    adopt_run = "adopt-20260917T120000Z-0123456789abcdef"
    machine = ".working/toml"
    manifest = {"opf": {"layout": "inline"}, "types": {}, "views": {}}
    manifest2 = {"opf": {"layout": "inline", "homes": 2, "spec_version": "2.0.0"}, "types": {}, "views": {}}
    homes = tuple(".working/" + name for name in ("imports", "imported", "archive", "staging", "journals"))
    legacy_root = homes[0]

    def active():
        # Homes 2 is activated only inside the fixture; shipped tooling grades every store as legacy.
        return patch.object(store, "SUPPORTED_HOMES", 2)

    # Discovery precedes the manifest: a candidate under journals/ still fails closed as ambiguous.
    with patch.object(store, "_immediate_subdirs", return_value=["journals", "toml"]), \
            patch.object(store, "_read_toml_contained", return_value={"opf": {"standard": store.STANDARD_TOKEN}}):
        check("discovery-journals-candidate-ambiguous",
              lambda: store.discover_machine_store(-1, "/store")[0] == "multiple")
    cls = doctor.classify_containment(manifest, machine)
    check("classifier-legacy-roster", lambda: cls.homes == 1 and cls.control_roots == (legacy_root,)
          and cls.evidence_roots == ())
    check("classifier-declaration-inert", lambda: doctor.classify_containment(manifest2, machine).homes == 1)
    # Latent activation is gated: raising SUPPORTED_HOMES activates only a store declaring both homes 2
    # and spec_version 2.0.0, never a current-version store or a non-integer declaration.
    with active():
        check("activation-homes2-declared", lambda: store.homes_generation(manifest2) == 2)
        for label, opf in (("current-version", {"homes": 2, "spec_version": "1.1.0"}),
                           ("no-version", {"homes": 2}), ("boolean", {"homes": True, "spec_version": "2.0.0"}),
                           ("string", {"homes": "2", "spec_version": "2.0.0"})):
            check("activation-gated-" + label, lambda o=opf: store.homes_generation({"opf": o}) == 1)
    with active():
        check("classifier-activated-legacy", lambda: doctor.classify_containment(manifest, machine).homes == 1)
        cls2 = doctor.classify_containment(manifest2, machine)
        check("classifier-roster", lambda: cls2.homes == 2 and cls2.control_roots == homes)
        check("evidence-roots-disjoint", lambda: cls2.evidence_roots ==
              (".working/imported", ".working/archive") and cls2.archive_root not in cls2.evidence_roots)
        for home in homes:
            for operand in (home, home + "/child", ".working", "./" + home + "/"):
                altered = dict(manifest2, unmanaged={"paths": [operand]})
                check("unmanaged-collision-" + operand,
                      lambda m=altered: bool(doctor.classify_containment(m, machine).colliding)
                      and not doctor.classify_containment(m, machine).valid_unmanaged)
    for home in homes[1:]:
        for operand in (home, home + "/child"):
            check("unmanaged-legacy-valid-" + operand, lambda o=operand: doctor.classify_containment(
                dict(manifest, unmanaged={"paths": [o]}), machine).valid_unmanaged == [o])
    check("unmanaged-legacy-imports-collision", lambda: bool(doctor.classify_containment(
        dict(manifest, unmanaged={"paths": [legacy_root]}), machine).colliding))
    ledger = machine + "/evidence.toml"
    check("unmanaged-legacy-evidence-ledger", lambda: doctor.classify_containment(
        dict(manifest, unmanaged={"paths": [ledger]}), machine).valid_unmanaged == [ledger])
    resolution = SimpleNamespace(machine_rel=machine, store_root=Path("/store"), product_root=Path("/store"))
    check("detect-legacy-set-equality",
          lambda: ingest._managed_paths(resolution, manifest)[0] == {machine, legacy_root})
    with active():
        check("detect-checker-set-equality",
              lambda: ingest._managed_paths(resolution, manifest2)[0] == {machine} | set(homes))
    import stat

    def detect_open(name, *_args, **_kwargs):
        if name != ".working":
            raise AssertionError("detect entered control home: " + name)
        return 99

    for mode in (stat.S_IFDIR, stat.S_IFREG):
        with active(), patch.object(ingest.os, "open", side_effect=detect_open), \
                patch.object(ingest.os, "close"), \
                patch.object(ingest.os, "listdir", return_value=[h.split("/")[-1] for h in homes]), \
                patch.object(ingest.os, "stat", return_value=SimpleNamespace(st_mode=mode)), \
                patch.object(ingest, "_digest_of", side_effect=AssertionError("read control bytes")):
            check("detect-never-reads-controls-" + str(mode), lambda: ingest._detect_store_scope(
                -1, ingest._managed_paths(resolution, manifest2)[0], set(), set(), homes=2) == [])
    with patch.object(ingest.os, "open", side_effect=detect_open), patch.object(ingest.os, "close"), \
            patch.object(ingest.os, "listdir", return_value=[h.split("/")[-1] for h in homes]), \
            patch.object(ingest.os, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
            patch.object(ingest, "_digest_of", return_value=("sha256:" + "0" * 64, 0)):
        # The legacy (homes 1) walk prunes directories only, so a regular FILE named like a home is a row.
        check("detect-legacy-control-file-rows", lambda: len(ingest._detect_store_scope(
            -1, ingest._managed_paths(resolution, manifest)[0], set(), set())) == len(homes))
    for home in homes:
        check("frozen-row-refusal-" + home,
              lambda h=home: refuses(lambda: ingest.admit_row_scope("store", h, "", homes=2)))
    check("frozen-row-legacy-imports", lambda: refusal(lambda: ingest.admit_row_scope("store", legacy_root, ""))
          == "store-scope row {0!r} lies in the reserved imports tree {0!r}, which detection prunes "
          "wholesale".format(legacy_root))
    for home in homes[1:]:
        check("frozen-row-legacy-" + home, lambda h=home: ingest.admit_row_scope("store", h + "/file", "") is None)
    # The manifest-free review gate accepts a supplied generation only as the integer 1 or 2. Any other
    # value (a bool, a float, NaN) cannot evaluate, whatever the tooling supports and whatever the row, and so
    # does a supplied 2 on tooling that does not support homes 2.
    import check_opf_import as gate
    journal_row = dict(scope="store", source_path=".working/journals/x")
    imports_row = dict(scope="store", source_path=legacy_root + "/x")

    def gated(row, generation):
        return gate._row_scope_error(ingest, [row], None, generation)

    for supported in (1, 2):
        with patch.object(store, "SUPPORTED_HOMES", supported):
            for bad in (0, False, True, 1.5, float("nan"), "2", 3):
                for label, gated_row in (("journals", journal_row), ("imports", imports_row)):
                    check("gate-generation-cannot-{}-{}-{!r}".format(supported, label, bad),
                          lambda r=gated_row, b=bad: gated(r, b).startswith("cannot evaluate"))
    with patch.object(store, "SUPPORTED_HOMES", 1):
        check("gate-generation-unsupported-2-cannot",
              lambda: gated(journal_row, 2).startswith("cannot evaluate"))
    with active():
        check("gate-generation-homes2-journal-refused",
              lambda: "reserved store control area" in gated(journal_row, 2))
        check("gate-generation-legacy-journal-admitted", lambda: gated(journal_row, 1) == "")
        check("gate-generation-legacy-imports-refused", lambda: "reserved imports tree" in gated(imports_row, 1))
    check("move-outside-preserved", lambda: ingest.admit_move_boundary("outside/file", "ops/.working") is None)
    check("move-store-refused", lambda: refuses(lambda: ingest.admit_move_boundary("ops/.working/x", "ops/.working")))

    files = {}
    directories = {".working"}
    listed = []
    unreadable = set()
    vanished = set()
    inventories = {}

    def listing(_fd, rel):
        listed.append(rel)
        if rel == ".working/journals" or rel.startswith(".working/journals/"):
            raise AssertionError("doctor read journals")
        if rel in unreadable or rel in files:
            raise store.StoreError("unreadable or wrong-type input: " + rel)
        if rel not in directories or rel in vanished:
            return None, None
        prefix = rel + "/"
        dirs = sorted(d[len(prefix):] for d in directories if d.startswith(prefix) and "/" not in d[len(prefix):])
        leaves = sorted(d[len(prefix):] for d in files if d.startswith(prefix) and "/" not in d[len(prefix):])
        return dirs, leaves

    def add(path, raw=b"x"):
        files[path] = raw
        parts = path.split("/")
        directories.update("/".join(parts[:n]) for n in range(1, len(parts)))

    def reset():
        files.clear()
        directories.clear()
        directories.add(".working")
        inventories.clear()
        unreadable.clear()
        vanished.clear()

    def read_toml(_fd, rel, rep):
        if rel.startswith(".working/journals/") or not store.is_evidence_inventory_name(rel.rsplit("/", 1)[-1]):
            raise AssertionError("unexpected inventory authority: " + rel)
        if rel in unreadable:
            rep.cant("unreadable " + rel)
            return None, "error"
        return (copy.deepcopy(inventories[rel]), "ok") if rel in inventories else (None, "absent")

    def read_bytes(_fd, rel, rep):
        if rel in unreadable:
            rep.cant("unreadable " + rel)
            return None, "error"
        return (files[rel], "ok") if rel in files else (None, "absent")

    def containment(partial=False, model=None):
        report = doctor._Report()
        report.ran("C-CONTAINMENT")
        doctor._check_containment(0, machine, model or manifest, "partial" if partial else "complete", report)
        return report

    def evidence(generation=2):
        report = doctor._Report()
        report.ran("C-EVIDENCE-ENUM")
        doctor._check_evidence(0, generation, report)
        return report

    def doc(*rows):
        return {"format": "opf.evidence.inventory/v1", "file": list(rows)}

    def row(path, raw=b"retained"):
        return {"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def graded(report, path):
        return any(repr(path) in message for message in report.findings)

    with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", read_toml), \
            patch.object(doctor, "_read_bytes", read_bytes):
        stage = ".working/staging/import/" + run + "/plan.toml"
        add(stage)
        add(".working/journals/import/journal/inflight/frames.log", b"torn")
        # A legacy store keeps its legacy grading: each homes-2 name is one unregistered path.
        check("legacy-staging-graded", lambda: graded(containment(), ".working/staging"))
        check("legacy-journals-graded", lambda: graded(containment(), ".working/journals"))
        check("legacy-staging-not-partial", lambda: not containment(True).triage
              and any("no active" in message for message in containment(True).findings))
        with active():
            check("staging-steady-finding", lambda: any(stage in s for s in containment(model=manifest2).findings))
            check("staging-substantiated-triage", lambda: bool(containment(True, manifest2).triage)
                  and not containment(True, manifest2).findings)
            del files[stage]
            check("empty-run-grades", lambda: any(run in s for s in containment(model=manifest2).findings))
            check("empty-run-not-partial", lambda: bool(containment(True, manifest2).findings)
                  and not containment(True, manifest2).triage)
            add(stage)
            add(".working/staging/unknown/run/file")
            check("unknown-kind-always-finding", lambda: any("invalid staging kind" in s
                                                           for s in containment(True, manifest2).findings))
            reset()
            directories.add(".working/journals")
            del listed[:]
            check("journals-pruned", lambda: not containment(model=manifest2).findings
                  and not containment(model=manifest2).cannot)
            check("journals-never-listed", lambda: ".working/journals" not in listed)
            check("evidence-absent-clean", lambda: not evidence().findings and not evidence().cannot)
            bundle = ".working/imported/import/" + run
            adoption = ".working/imported/adoption/" + adopt_run
            source = bundle + "/sources/notes.txt"
            moved = ".working/archive/moved/old/notes.txt"
            receipt = adoption + "/receipt.toml"
            preimage = ".working/archive/adoption/" + adopt_run + "/AGENTS.md"
            members = (source, moved, receipt, preimage)
            for path in members:
                add(path, b"retained")
            for inventory, rows in ((bundle + "/inventory.toml", (row(source), row(moved))),
                                    (adoption + "/inventory.toml", (row(receipt), row(preimage)))):
                add(inventory, b"")
                inventories[inventory] = doc(*rows)
            check("evidence-members-match", lambda: not evidence().findings and not evidence().cannot)
            check("evidence-not-containment-graded", lambda: not containment(model=manifest2).findings)
        # Nothing reads a homes-1 store's evidence homes; its containment walk grades them as before.
        def legacy_reads_nothing():
            count = len(listed)
            with patch.object(doctor, "_read_toml", side_effect=AssertionError("legacy inventory read")), \
                    patch.object(doctor, "_read_bytes", side_effect=AssertionError("legacy evidence read")):
                legacy = evidence(1)
            return not legacy.findings and not legacy.cannot and len(listed) == count

        check("evidence-legacy-reads-nothing", legacy_reads_nothing)
        check("evidence-legacy-contained", lambda: graded(containment(), ".working/imported")
              and graded(containment(), ".working/archive"))
        with active():
            for path in members:
                files[path + ".stray"] = b"rogue"
                check("evidence-off-inventory-" + path, lambda p=path: graded(evidence(), p + ".stray"))
                del files[path + ".stray"]
                del files[path]
                check("evidence-missing-" + path, lambda p=path: graded(evidence(), p))
                files[path] = b"tampered"
                check("evidence-digest-" + path, lambda: any("mismatch" in s for s in evidence().findings))
                files[path] = b"retained"
                unreadable.add(path)
                check("evidence-unreadable-" + path, lambda: bool(evidence().cannot))
                unreadable.clear()
            for extra in (".working/archive/empty-stray", bundle + "/empty", ".working/imported/other",
                          ".working/imported/import/not-a-run", ".working/archive/adoption/" + run):
                directories.add(extra)
                check("evidence-stray-directory-" + extra, lambda: bool(evidence().findings)
                      and not evidence().cannot)
                directories.remove(extra)
            orphan = ".working/imported/import/" + run.replace("0123", "4567")
            add(orphan + "/file")
            check("evidence-bundle-without-inventory", lambda: any("has no inventory" in s
                                                                 for s in evidence().findings))
            del files[orphan + "/file"]
            directories.discard(orphan)
            # A bundle its parent listed but that is gone when it is listed itself is a race, not an
            # empty bundle: its claims are unknown, so the walk cannot evaluate rather than pass.
            raced = ".working/imported/import/" + run.replace("0123", "89ab")
            directories.add(raced)
            vanished.add(raced)
            check("evidence-bundle-vanished-cannot", lambda: any(
                "vanished" in s for s in evidence().cannot) and not evidence().findings)
            vanished.clear()
            directories.discard(raced)
            probe = adoption + "/probe.toml"
            add(probe, b"retained")
            check("evidence-unclaimed-phase-member", lambda: graded(evidence(), probe))
            add(adoption + "/inventory-evidence.toml", b"")
            inventories[adoption + "/inventory-evidence.toml"] = doc(row(probe))
            check("evidence-phase-inventory", lambda: not evidence().findings and not evidence().cannot)
            # A phase inventory never substitutes for a missing inventory.toml: the founding claims are
            # unknown, so the bundle cannot evaluate, even when the phase inventory lists every member.
            first = adoption + "/inventory.toml"
            founding = inventories.pop(first)
            del files[first]
            inventories[adoption + "/inventory-evidence.toml"] = doc(row(probe), *founding["file"])
            check("evidence-phase-without-first-cannot", lambda: any(
                "no inventory.toml" in s for s in evidence().cannot) and not evidence().findings)
            add(first, b"")
            inventories[first] = founding
            inventories[adoption + "/inventory-evidence.toml"] = doc(row(probe))
            check("evidence-phase-restored", lambda: not evidence().findings and not evidence().cannot)
            inventory = bundle + "/inventory.toml"
            good = copy.deepcopy(inventories)
            # A claim on another bundle's member or another run's preimage is refused on its own, so the
            # rightful owner's inventory drops that row here rather than masking it as a duplicate claim.
            # An import bundle claiming a preimage path under its OWN run id is refused by its kind alone.
            sole = {"cross-bundle": doc(row(preimage)), "foreign-preimage": doc(row(receipt))}
            own_preimage = ".working/archive/adoption/" + run + "/file"
            # Every malformed or unreadable inventory is cannot-evaluate and grades nothing partially.
            for label, broken in (
                    ("format", dict(doc(), format="unsupported")),
                    ("extra-key", dict(doc(row(source), row(moved)), note="x")),
                    ("no-file-array", {"format": "opf.evidence.inventory/v1"}),
                    ("row-not-table", doc("x")),
                    ("row-extra-key", doc(dict(row(source), note="x"), row(moved))),
                    ("foreign-kind", doc(dict(row(source), path=".working/imported/other/" + run + "/x"))),
                    ("wrong-prefix", doc(dict(row(source), path=source[len(".working/"):]), row(moved))),
                    ("cross-bundle", doc(row(source), row(moved), row(receipt))),
                    ("foreign-preimage", doc(row(source), row(moved), row(preimage))),
                    ("import-own-run-preimage", doc(row(source), row(moved), row(own_preimage))),
                    ("lists-inventory", doc(row(source), row(moved), row(inventory))),
                    ("boolean-size", doc(dict(row(source), size=True), row(moved))),
                    ("negative-size", doc(dict(row(source), size=-1), row(moved))),
                    ("uppercase-digest", doc(dict(row(source), sha256=row(source)["sha256"].upper()), row(moved))),
                    ("duplicate", doc(row(source), row(source), row(moved)))):
                inventories[inventory] = broken
                inventories[adoption + "/inventory.toml"] = sole.get(label, good[adoption + "/inventory.toml"])
                check("evidence-malformed-" + label, lambda: bool(evidence().cannot) and not evidence().findings)
            inventories.update(copy.deepcopy(good))
            inventories[adoption + "/inventory.toml"] = doc(row(receipt), row(preimage), row(moved))
            check("evidence-cross-duplicate-refused", lambda: bool(evidence().cannot))
            inventories.update(copy.deepcopy(good))
            # An unreadable or vanished inventory leaves its bundle's claims unknown: cannot-evaluate
            # only, never a partial reconciliation grading the rest of the homes without those claims.
            unreadable.add(inventory)
            check("evidence-unreadable-inventory", lambda: bool(evidence().cannot) and not evidence().findings)
            unreadable.clear()
            del inventories[inventory]
            check("evidence-vanished-inventory", lambda: bool(evidence().cannot) and not evidence().findings)
            inventories.update(copy.deepcopy(good))
            unreadable.add(".working/imported")
            check("evidence-unreadable-root", lambda: bool(evidence().cannot))
            unreadable.clear()

    import _opf_init as init
    full_manifest = init._manifest_model()
    full_manifest["views"] = {}
    full_manifest2 = copy.deepcopy(full_manifest)
    full_manifest2["opf"]["homes"] = 2
    full_manifest2["opf"]["spec_version"] = "2.0.0"
    reset()
    add(".working/imported/import/" + run + "/sources/notes.txt")
    # A journal on disk: legacy grades it as an ordinary path, homes 2 must skip it unread.
    add(".working/journals/import/journal/" + run + "/frames.log", b"torn")

    def dispatch(model):
        def doctor_toml(_fd, rel, _rep):
            if rel == machine + "/manifest.toml":
                return copy.deepcopy(model), "ok"
            if rel.startswith(".working/journals/"):
                raise AssertionError("doctor read a journal record")
            return None, "absent"

        del listed[:]
        with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", doctor_toml), \
                patch.object(doctor, "_read_bytes", read_bytes), \
                patch.object(importer, "_sibling_ids", return_value=[]):
            report = doctor._Report()
            doctor._validate_opened_store(0, None, machine, None, {}, None, "default", True, report)
        return report.result(), list(listed)

    def contained(result, path):
        return any(repr(path) in message for message in result.by_check.get("C-CONTAINMENT", []))

    # A legacy (homes 1) report keeps the legacy roster, order and count and the pinned legacy residual
    # text, with the homes-2 names graded as ordinary paths and no evidence read.
    legacy_report, legacy_listed = dispatch(full_manifest)
    # The roster is pinned independently of the live REQUIRED_CHECKS constant: sha256 over the 29 legacy check
    # ids in report order joined by a newline, UTF-8, so an added, removed or reordered check changes it.
    legacy_roster_sha256 = "38de4ed345c62237a4f01e194a72b3d7af13d552717ba3db3363abbbacd30eee"

    def legacy_roster(rep):
        return len(rep.checks) == 29 and hashlib.sha256("\n".join(rep.checks).encode("utf-8")).hexdigest() \
            == legacy_roster_sha256
    check("doctor-legacy-roster-exact", lambda: legacy_roster(legacy_report)
          and tuple(legacy_report.checks) == doctor.REQUIRED_CHECKS and "C-EVIDENCE-ENUM" not in doctor.REQUIRED_CHECKS)
    # The pin is independent of the live _RESIDUALS constant: sha256 over the 12 legacy residuals joined by
    # a newline, UTF-8. Any added, removed, reordered or reworded legacy residual changes it.
    legacy_residuals_sha256 = "6901f8734d2293e714c521e9b74836a188627839d7d8a08a3888b4a9cc7a8a0b"
    check("doctor-legacy-residuals-unchanged", lambda: len(legacy_report.residuals) == 12
          and hashlib.sha256("\n".join(legacy_report.residuals).encode("utf-8")).hexdigest()
          == legacy_residuals_sha256
          and not any(r in legacy_report.residuals for r in doctor._HOMES2_RESIDUALS))
    check("doctor-legacy-evidence-inert", lambda: not any(
        path.startswith(".working/imported/") for path in legacy_listed))
    check("doctor-legacy-contains-homes", lambda: contained(legacy_report, ".working/imported")
          and contained(legacy_report, ".working/journals"))
    with active():
        report, homes2_listed = dispatch(full_manifest2)
    check("doctor-homes2-roster", lambda: tuple(report.checks) == doctor.required_checks(2)
          and len(report.checks) == 30)
    check("doctor-dispatches-evidence", lambda: report.checks.get("C-EVIDENCE-ENUM") == "FINDING")
    check("doctor-homes2-residual", lambda: all(r in report.residuals for r in doctor._HOMES2_RESIDUALS))
    check("doctor-skips-journals", lambda: not contained(report, ".working/journals")
          and not any(p == ".working/journals" or p.startswith(".working/journals/") for p in homes2_listed))
    # Activated tooling alone widens nothing: a legacy manifest keeps the legacy roster and residuals.
    with active():
        activated_legacy, _ = dispatch(full_manifest)
    check("doctor-activated-legacy-roster", lambda: legacy_roster(activated_legacy)
          and activated_legacy.residuals == legacy_report.residuals)
    # The render source gate follows the report's roster: a homes-2 evidence finding refuses it, and a
    # legacy report is gated on exactly SOURCE_INTEGRITY_CHECKS.
    flagged = dict.fromkeys(doctor.required_checks(2), "PASS")
    flagged["C-EVIDENCE-ENUM"] = "FINDING"
    check("source-gate-homes2-evidence", lambda: not doctor.source_integrity_ok(
        doctor.StoreValidation(doctor.INVALID, checks=flagged)))
    check("source-gate-legacy-roster", lambda: set(doctor.source_checks(doctor.StoreValidation(
        doctor.VALID, checks=dict.fromkeys(doctor.REQUIRED_CHECKS, "PASS")))) == doctor.SOURCE_INTEGRITY_CHECKS)

    def roster(generation, ran):
        rep = doctor._Report()
        rep.require_homes(generation)
        for cid in ran:
            rep.ran(cid)
        return rep.result()

    check("roster-homes2-skipped-evidence", lambda: roster(2, doctor.REQUIRED_CHECKS).checks.get(
        "C-EVIDENCE-ENUM") == "CANNOT-EVALUATE")
    check("roster-legacy-evidence-unknown", lambda: any("C-EVIDENCE-ENUM" in m for m in roster(
        1, doctor.required_checks(2)).unattributed))

    # Homes 1 keeps the legacy operand handling: the legacy engine applies no journal-operand refusal,
    # so each legacy entry point reaches its first I/O whatever the operand.
    targets = (".working", ".working/journals", ".working/journals/import/journal/frame")

    def reached(thunk):
        try:
            thunk()
        except _Reached:
            return True
        return False

    for kind in journal.OP_KINDS:
        for target in targets:
            ops = [dict(op=kind, path=target)]
            with patch.object(journal, "_open_parent", side_effect=_Reached):
                check("legacy-apply-{}-{}".format(kind, target), lambda o=ops: reached(
                    lambda: journal.apply_ops(-1, o, lambda _name: b"")))
            with patch.object(journal, "_open_txn_beneath", side_effect=_Reached):
                check("legacy-preimage-{}-{}".format(kind, target), lambda o=ops: reached(
                    lambda: journal.capture_preimages(-1, Path("/unused"), -1, o)))
            with patch.object(journal.os, "mkdir", side_effect=_Reached):
                check("legacy-transaction-{}-{}".format(kind, target), lambda o=ops: reached(
                    lambda: journal.run_transaction(-1, -1, "/unused", run, dict(), o, lambda _name: b"",
                                                    "test")))

    # Homes 2: the capability-bound API refuses every kind before it opens anything.
    with patch.object(home_journal, "_opened", side_effect=AssertionError("opened the store journal")):
        for kind in journal.OP_KINDS:
            for target in targets:
                ops = [dict(op=kind, path=target)]
                check("homes2-transaction-{}-{}".format(kind, target), lambda o=ops: "journal home" in (
                    refusal(lambda: home_journal.run_transaction(object(), "import", run, o,
                                                                 lambda _name: b"")) or ""))
    check("ordinary-sibling-allowed", lambda: home_journal._check_ordinary_ops(
        [dict(op="create", path=".working/journals-old/file")]) is None)

    def legacy_recovery(frame_types):
        truncated = []
        intent = dict(txn=run, ops=[dict(op="remove", path=".working/journals")])
        frames = [(t, intent if t == journal.F_INTENT else dict(txn=run)) for t in frame_types]
        with patch.object(journal, "read_frames", return_value=(frames, True, 1)), \
                patch.object(journal, "_truncate_log", side_effect=lambda *_args: truncated.append(True)), \
                patch.object(journal, "_poststate_verifies", return_value=True), \
                patch.object(journal, "_restore_preimage"), patch.object(journal, "publish"):
            return journal.recover(-1, run, -1), len(truncated)

    # Legacy (homes 1) recovery truncates the torn tail and acts on an open transaction's operands.
    check("legacy-recovery-open-rolls-forward", lambda: legacy_recovery([journal.F_INTENT]) == ("rolled-forward", 1))
    check("legacy-recovery-rollback-open-rolls-back",
          lambda: legacy_recovery([journal.F_INTENT, journal.F_RIP]) == ("rolled-back", 1))
    check("legacy-recovery-terminal-truncated",
          lambda: legacy_recovery([journal.F_INTENT, journal.F_COMPLETE]) == ("terminal", 1))

    @contextlib.contextmanager
    def opened(*_args, **_kwargs):
        yield -1, -1, Path("/unused") / run

    def homes2_recovery(frame_types):
        intent = dict(txn=run, ops=[dict(op="remove", path=".working/journals")])
        frames = [(t, intent if t == journal.F_INTENT else dict(txn=run)) for t in frame_types]
        with patch.object(home_journal, "_opened", opened), \
                patch.object(home_journal, "_existing_frames", return_value=frames), \
                patch.object(home_journal, "_project"), \
                patch.object(journal, "recover", return_value="terminal") as recovered:
            outcome = refusal(lambda: home_journal.recover_transaction(object(), "import", run))
            if outcome is not None:
                outcome = "refused" if "journal home" in outcome else outcome
            return outcome or "recovered", recovered.call_count

    # Only an open homes-2 transaction is acted on, so it is refused before recover can truncate or
    # mutate anything. A terminal journal is inert history and reaches recover unchanged.
    check("homes2-recovery-open-refused", lambda: homes2_recovery([journal.F_INTENT]) == ("refused", 0))
    check("homes2-recovery-rollback-open-refused",
          lambda: homes2_recovery([journal.F_INTENT, journal.F_RIP]) == ("refused", 0))
    check("homes2-recovery-complete-terminal",
          lambda: homes2_recovery([journal.F_INTENT, journal.F_COMPLETE]) == ("recovered", 1))
    check("homes2-recovery-rolled-back-terminal",
          lambda: homes2_recovery([journal.F_INTENT, journal.F_RIP, journal.F_RC]) == ("recovered", 1))
    create = {"op": "create-file", "content_digest": "sha256:" + "0" * 64}
    for home in homes:
        check("adoption-target-" + home, lambda h=home: adopt.validate_op(
            dict(create, path=h + "/file"), homes=2).status != store.VALID)
        check("adoption-legacy-target-" + home, lambda h=home: adopt.validate_op(
            dict(create, path=h + "/file")).status == store.VALID)
    check("adoption-composed-member", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".working", "members": [
            {"path": "journals/file", "digest": "sha256:" + "0" * 64}]}, homes=2).status != store.VALID)

    check("adoption-root-pack-preserved", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".", "members": [
            {"path": "notes.txt", "digest": "sha256:" + "0" * 64}]}, homes=2).status == store.VALID)
    check("adoption-root-pack-journal-refused", lambda: adopt.validate_op(
        {"op": "install-pack", "target": ".", "members": [
            {"path": ".working/journals/file", "digest": "sha256:" + "0" * 64}]}, homes=2).status != store.VALID)
    def view_plan(model):
        view_manifest = dict(model, views=dict(VERSION=dict(kind="projection", sources=[],
                                                            target=".working/journals/file")))
        with patch.object(views, "_read_raw_and_parsed", return_value=(b"", view_manifest)), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(views, "_resolve_view", return_value=("projection", [], lambda _src: "1.0.0\n")), \
                patch.object(views, "_spec_destination", return_value=("store", ".working/journals/file")):
            return refusal(lambda: views.plan_views(-1, machine)) or ""

    with active():
        check("view-journal-destination-refused", lambda: "journal home" in view_plan(manifest2))
        check("view-legacy-journal-destination-planned", lambda: "journal home" not in view_plan(manifest))

    import _opf_adopt_plan as planning
    import _opf_emit as emit

    class _Enumerated(Exception):
        pass

    def investigation(path, unmanaged=False, generation=1):
        model = manifest2 if generation == 2 else manifest
        model = dict(model, unmanaged={"paths": [path]}) if unmanaged else model
        resolved = SimpleNamespace(status=store.RESOLVED, machine_rel=machine)
        with patch.object(store, "SUPPORTED_HOMES", generation), \
                patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                patch.object(planning.os, "close"), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=resolved), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(planning, "_read_rel", return_value=emit.emit_checked(model).encode()), \
                patch.object(store._journal, "_open_parent", side_effect=_Enumerated), \
                patch.object(planning.os, "stat", side_effect=_Enumerated):
            try:
                planning._inventory(Path("/store"), ["notes" if unmanaged else path + "/child"], [])
            except planning.PlanError:
                return "refused"
            except _Enumerated:
                return "enumerated"
        return "completed"

    # Homes 2 excludes and reserves every control root; a legacy store reserves only imports.
    for home in homes:
        check("investigation-control-" + home, lambda h=home: investigation(h, generation=2) == "refused")
        check("investigation-collision-" + home, lambda h=home: investigation(h, True, 2) == "refused")
        check("investigation-legacy-source-" + home, lambda h=home: investigation(h) == "enumerated")
    check("investigation-legacy-imports-collision", lambda: investigation(legacy_root, True) == "refused")
    for home in homes[1:]:
        check("investigation-legacy-unmanaged-" + home, lambda h=home: investigation(h, True) == "enumerated")

    def unresolved_manifest(name):
        directory = SimpleNamespace(st_mode=stat.S_IFDIR)
        with patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=SimpleNamespace(st_dev=1, st_ino=1)), \
                patch.object(planning.os, "close"), \
                patch.object(planning.os, "open", return_value=-1), \
                patch.object(planning.os, "scandir",
                             return_value=contextlib.nullcontext([SimpleNamespace(name=name)])), \
                patch.object(planning.os, "stat", return_value=directory), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=SimpleNamespace(
                    status=store.CANNOT_EVALUATE, detail="fixture")), \
                patch.object(store._journal, "_lstat_at", return_value=directory), \
                patch.object(store._journal, "_open_parent", side_effect=_Enumerated):
            try:
                planning._inventory(Path("/store"), ["notes"], [])
            except planning.PlanError as exc:
                return "requires repair" in str(exc)
        return False

    # An unresolved store with a candidate manifest under a reserved name still requires repair.
    for name in store.RESERVED_MACHINE_SUBDIRS:
        check("investigation-unresolved-reserved-" + name, lambda n=name: unresolved_manifest(n))

    # Every generation-dependent call site derives the generation from the store's own manifest read, so a
    # homes-2 store reaches the control-area refusal there and a legacy store is graded exactly as before.
    import datetime
    import tomllib
    same = SimpleNamespace(st_dev=1, st_ino=1, st_mode=stat.S_IFDIR, st_nlink=1, st_size=0,
                           st_mtime_ns=0, st_ctime_ns=0)
    control_op = dict(create, path=".working/journals/file")
    utc = datetime.datetime(2026, 9, 17, 12, tzinfo=datetime.timezone.utc)

    def planned(generation, op, supported=None):
        # `generation` selects the manifest; `supported` (default: the same) is the activated tooling.
        model = manifest2 if generation == 2 else manifest
        resolved = SimpleNamespace(status=store.RESOLVED, machine_rel=machine, detail="", pointer_source="default")
        with patch.object(store, "SUPPORTED_HOMES", generation if supported is None else supported), \
                patch.object(store._journal, "require_containment"), \
                patch.object(store, "_open_dir_nofollow", return_value=-1), \
                patch.object(planning.os, "fstat", return_value=same), \
                patch.object(planning.os, "close"), \
                patch.object(store, "_read_pointer_target", return_value=None), \
                patch.object(store, "resolve_store", return_value=resolved), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(planning, "_read_rel", return_value=emit.emit_checked(model).encode()), \
                patch.object(store._journal, "_open_parent", side_effect=FileNotFoundError), \
                patch.object(adopt, "validate_plan", wraps=adopt.validate_plan) as frozen:
            observed = planning.investigate(Path("/store"), sources=[])
            result = planning.plan(
                Path("/store"), sources=[], product="opf", decisions=[], ops=[op],
                expected_observation_digest=tomllib.loads(observed.observation.decode())["observation_digest"],
                now=utc, run_nonce="0123456789abcdef")
        return result, [c.kwargs.get("homes") for c in frozen.call_args_list]

    homes2_plan, _ = planned(2, control_op)
    legacy_plan, legacy_frozen = planned(1, control_op)
    ordinary_plan, ordinary_frozen = planned(2, dict(create, path="notes.txt"))
    # Each op loop refuses on its own (its exact findings), not only through the frozen-plan revalidation.
    refused = tuple(adopt.validate_op(control_op, homes=2).findings)
    check("plan-homes2-control-op-refused", lambda: homes2_plan.status == store.INVALID and homes2_plan.plan is None
          and bool(refused) and homes2_plan.findings == refused)
    control_retire = dict(op="retire-file", path=".working/journals/file", preimage_digest="sha256:" + "0" * 64)
    with patch.object(planning, "_decisions", return_value=([control_retire], [])):
        retired, _ = planned(2, dict(create, path="notes.txt"))
        legacy_retired, _ = planned(1, dict(create, path="notes.txt"))
    check("plan-homes2-control-disposition-refused", lambda: retired.status == store.INVALID
          and retired.findings == tuple(adopt.validate_op(control_retire, homes=2).findings) != ())
    check("plan-legacy-control-disposition-planned", lambda: legacy_retired.status == store.VALID)
    check("plan-legacy-control-op-planned", lambda: legacy_plan.status == store.VALID and legacy_frozen == [1])
    activated_plan, activated_frozen = planned(1, control_op, supported=2)
    check("plan-activated-legacy-generation", lambda: activated_plan.status == store.VALID
          and activated_frozen == [1])
    check("plan-homes2-frozen-plan-bound", lambda: ordinary_plan.status == store.VALID and ordinary_frozen == [2])
    check("plan-validate-homes2-refused", lambda: adopt.validate_plan(
        tomllib.loads(legacy_plan.plan.decode()), homes=2).status == store.INVALID)
    check("plan-validate-legacy-unchanged", lambda: adopt.validate_plan(
        tomllib.loads(legacy_plan.plan.decode())).status == store.VALID)

    same_root = SimpleNamespace(status=store.RESOLVED, machine_rel=machine, store_root=Path("/store"),
                                product_root=Path("/store"), pointer_source="default", detail="")

    def detected_rows(generation, model=manifest2):
        with patch.object(store, "SUPPORTED_HOMES", generation), \
                patch.object(store, "_open_store_root_fd", return_value=-1), patch.object(ingest.os, "close"), \
                patch.object(store, "_read_toml_contained", return_value=copy.deepcopy(model)), \
                patch.object(store, "validate_manifest", return_value=SimpleNamespace(status=store.VALID)), \
                patch.object(ingest, "_managed_paths", return_value=(set(), set(), set(), set(), set())), \
                patch.object(ingest, "_detect_store_scope", return_value=[]) as walked:
            return ingest._detect_rows("/store", same_root, None), walked.call_args.kwargs.get("homes")

    check("detect-rows-homes2-generation", lambda: detected_rows(2) == (([], 2), 2))
    check("detect-rows-legacy-generation", lambda: detected_rows(1) == (([], 1), 1))
    check("detect-rows-activated-legacy-generation", lambda: detected_rows(2, manifest) == (([], 1), 1))
    with patch.object(journal, "require_containment"), patch.object(store, "resolve_store", return_value=same_root), \
            patch.object(store, "load_manifest", return_value=SimpleNamespace(status=store.VALID, findings=[])), \
            patch.object(ingest, "_detect_rows", return_value=([], 2)), \
            patch.object(ingest, "validate_worksheet", return_value=[]):
        check("detect-result-carries-generation", lambda: ingest.detect("/store").homes == 2)

    def ingest_planned(generation):
        journal_row = dict(scope="store", source_path=".working/journals/notes.md", disposition="keep", note="")
        with patch.object(journal, "require_containment"), \
                patch.object(ingest, "validate_worksheet", return_value=[]), \
                patch.object(ingest, "validate_options", return_value=[]), \
                patch.object(ingest, "detect", return_value=ingest.DetectResult(ingest.CLEAN, homes=generation)), \
                patch.object(ingest, "_reconcile_worksheet_against_detect"), \
                patch.object(store, "resolve_store", return_value=same_root), \
                patch.object(store, "_open_root_fd", return_value=-1), patch.object(ingest.os, "close"), \
                patch.object(ingest, "_digest_of", side_effect=_Reached):
            try:
                result = ingest.plan_ingest("/store", dict(row=[journal_row]), dict(option=[]), now=utc,
                                            run_nonce="0123456789abcdef")
            except _Reached:
                return "admitted"
        return result.verdict, " ".join(result.findings)

    check("ingest-plan-homes2-control-row-refused", lambda: ingest_planned(2)[0] == ingest.FINDING
          and "reserved store control area" in ingest_planned(2)[1])
    check("ingest-plan-legacy-row-admitted", lambda: ingest_planned(1) == "admitted")

    check("internal-api-capability-required", lambda: refuses(
        lambda: home_journal.run_transaction(object(), "import", run, [], lambda _name: b"")))
    with patch.object(home_journal._opf_oplock, "OpCapability", object):
        check("internal-api-identity-required", lambda: refuses(
            lambda: home_journal.recover_transaction(object(), "import", "../elsewhere")))
    with patch.object(journal, "_lstat_contained", return_value=None), \
            patch.object(journal, "read_frames", side_effect=AssertionError("missing journal was read as empty")):
        check("internal-recovery-missing-refused",
              lambda: refuses(lambda: home_journal._existing_frames(-1, run, "import", run)))

    import threading
    import _opf_oplock as oplock
    cap = oplock.OpCapability.__new__(oplock.OpCapability)
    cap._claim = threading.Lock()
    cap._claimant = None
    cap._released = True
    check("internal-api-released-refused", lambda: refuses(
        lambda: home_journal.recover_transaction(cap, "import", run)))
    cap._released = False
    cap._acquirer_pid = -1
    # OPF-D2B PR3a: the acquirer identity now spans (pid, pid-start), so this foreign-acquirer
    # capability must carry a pid-start too; -1 keeps the identity foreign, yielding a clean refusal
    # rather than an unset-slot AttributeError.
    cap._acquirer_pid_start = -1
    check("internal-api-foreign-acquirer-refused", lambda: refuses(
        lambda: home_journal.recover_transaction(cap, "import", run)))
    cap._claim.acquire()
    try:
        check("internal-api-busy-refused", lambda: refuses(
            lambda: home_journal.recover_transaction(cap, "import", run)))
    finally:
        cap._claim.release()
    header = {"kind": "import", "run_id": run, "operation_id": "test"}
    frames = [(journal.F_INTENT, {"txn": run, "header": header, "ops": []})]
    import stat
    with patch.object(journal, "_lstat_contained", return_value=SimpleNamespace(st_mode=stat.S_IFREG)), \
            patch.object(journal, "read_frames", return_value=(frames, False, 0)):
        check("internal-api-record-identity", lambda: home_journal._existing_frames(-1, run, "import", run) == frames)
        header["kind"] = "layout"
        check("internal-api-foreign-record-refused",
              lambda: refuses(lambda: home_journal._existing_frames(-1, run, "import", run)))

    # Observe the internal API's derived arguments and payloads with filesystem writes denied
    # by mocks. Real crash/durability and capability integration remain the runtime suite's job.
    import os
    import tomllib
    cap._acquirer_pid = os.getpid()
    cap._acquirer_pid_start = journal._pid_start(os.getpid())
    cap._anchor_fd = 101
    cap._anchor_ident = (1, 101)
    cap._machine_ident = (1, 102)
    cap.store_root = Path("/store")
    cap.machine_rel = machine
    cap.op_id = "held-operation"
    cap.holder = "fixture"

    def fstat(fd):
        return SimpleNamespace(st_dev=1, st_ino=fd, st_nlink=1,
                               st_mode=stat.S_IFREG if fd == 101 else stat.S_IFDIR)

    with patch.object(home_journal.os, "fstat", side_effect=fstat), \
            patch.object(home_journal.os, "close"), \
            patch.object(store, "_open_root_fd", return_value=100), \
            patch.object(journal, "_open_dir_contained", return_value=102), \
            patch.object(journal, "open_journal_root_fd", return_value=103), \
            patch.object(journal, "ensure_journal_dirs") as mkdirs, \
            patch.object(journal, "run_transaction", return_value="complete") as transaction, \
            patch.object(home_journal, "_project") as projection:
        check("internal-api-held-run", lambda: home_journal.run_transaction(
            cap, "import", run, [], lambda _name: b"") == "complete")
        check("internal-api-derived-frame-home", lambda: transaction.call_args.args[2] ==
              Path("/store/.working/journals/import/journal"))
        check("internal-api-bound-operation", lambda: transaction.call_args.args[4] ==
              {"kind": "import", "run_id": run, "operation_id": cap.op_id})
        check("internal-api-projects-terminal", lambda: projection.call_args.args[3:] == ("import", run))

        def attributed(thunk):
            try:
                thunk()
            except journal.JournalError as exc:
                return "operation failed" in str(exc) and "cannot open" not in str(exc)
            return False

        # Recovery with a matching identity reaches the open phase and must create nothing there.
        mkdirs.reset_mock()
        with patch.object(home_journal, "_existing_frames", return_value=frames), \
                patch.object(journal, "recover", return_value="terminal") as recovered:
            check("internal-recovery-held", lambda: home_journal.recover_transaction(
                cap, "import", run) == "terminal" and recovered.call_count == 1)
            check("internal-recovery-creates-nothing", lambda: mkdirs.call_count == 0)
        # A failure inside the operation is attributed to the operation, not to opening the journal.
        transaction.side_effect = OSError("disk full")
        check("internal-api-operation-error-attributed", lambda: attributed(
            lambda: home_journal.run_transaction(cap, "import", run, [], lambda _name: b"")))
        transaction.side_effect = None
        cap._machine_ident = (9, 999)
        check("internal-api-store-swap-refused", lambda: refuses(
            lambda: home_journal.recover_transaction(cap, "import", run)))

    header["kind"] = "import"
    header["operation_id"] = "original-operation"
    record_home = ".working/journals/import/runs/" + run
    with patch.object(home_journal, "_existing_frames", return_value=frames), \
            patch.object(journal, "classify_state", return_value="complete") as classify, \
            patch.object(journal, "_lstat_contained", return_value=None) as prior, \
            patch.object(journal, "ensure_journal_dirs") as mkdirs, \
            patch.object(journal, "_open_parent", return_value=(104, "transaction.toml")), \
            patch.object(oplock, "_remove_staging_garbage"), \
            patch.object(oplock, "_create_control_file", return_value=(105, (1, 105))) as publish, \
            patch.object(home_journal.os, "close"):
        home_journal._project(100, 103, Path("/unused") / run, "import", run)
        payload = publish.call_args.args[2]
        record = tomllib.loads(payload.decode())
        check("internal-projection-derived-home", lambda: mkdirs.call_args.args == (100, record_home))
        check("internal-projection-typed-record", lambda: record == {
            "format": "opf.journal.transaction/v1", "kind": "import", "run_id": run, "state": "complete",
            "operation_id": "original-operation", "journal_rel": ".working/journals/import/journal"})
        classify.return_value = "open"
        check("internal-projection-open-refused", lambda: refuses(
            lambda: home_journal._project(100, 103, Path("/unused") / run, "import", run)))
        classify.return_value = "complete"
        prior.return_value = object()
        with patch.object(journal, "_read_contained", return_value=(b"corrupt", None)):
            check("internal-projection-conflict-refused", lambda: refuses(
                lambda: home_journal._project(100, 103, Path("/unused") / run, "import", run)))
        with patch.object(journal, "_read_contained", return_value=(payload, None)):
            check("internal-projection-idempotent", lambda: home_journal._project(
                100, 103, Path("/unused") / run, "import", run) is None and publish.call_count == 1)

    preview = "/store/.working/staging/preview/preview-run"

    def preview_ignored(generation):
        ignored = {}

        def copytree(_root, _preview, **kwargs):
            hook = kwargs["ignore"]
            for root, names in (
                    ("/store/.working", ["journals", "staging", "imported", "archive"]),
                    ("/store/.working/staging/preview", ["preview-run", "sibling"]),
                    ("/store/nested/.working", ["journals"]),
                    ("/store/nested/.working/staging/preview", ["preview-run"]),
                    ("/store/.working/imports", [run, "sibling"])):
                ignored[root] = hook(root, names)

        importer._assemble_preview(resolution, machine, {}, preview, SimpleNamespace(copytree=copytree), run,
                                   homes=generation)
        return ignored

    legacy_ignored = preview_ignored(1)
    ignored = preview_ignored(2)
    check("preview-legacy-journals-retained", lambda: legacy_ignored["/store/.working"] == set())
    check("preview-legacy-self-retained", lambda: legacy_ignored["/store/.working/staging/preview"] == set())
    check("preview-journals-only", lambda: ignored["/store/.working"] == {"journals"})
    check("preview-self-only", lambda: ignored["/store/.working/staging/preview"] == {"preview-run"})
    check("preview-nested-retained", lambda: ignored["/store/nested/.working"] == set()
          and ignored["/store/nested/.working/staging/preview"] == set())
    check("preview-promoted-only", lambda: ignored["/store/.working/imports"] == {run})
    check("evidence-homes2-roster", lambda: "C-EVIDENCE-ENUM" not in doctor.REQUIRED_CHECKS
          and doctor.required_checks(2).index("C-EVIDENCE-ENUM") == doctor.REQUIRED_CHECKS.index("C-ARCHIVE-ENUM") + 1
          and "C-EVIDENCE-ENUM" in doctor.source_checks(
              SimpleNamespace(checks=dict.fromkeys(doctor.required_checks(2)))))

    for failure in failures:
        print("FAIL: " + failure)
    print("OPF-HOMES BOUNDARY SELF-TEST: {} ({} checks)".format("FAILED" if failures else "OK", len(checked)))
    return 1 if failures else 0


# Named QA5 findings (fix 5a): (section, current keyword wording, pre-fix wording). The self-test
# reverts each and requires the keyword lint, or the pin check, to turn red.
_KEYWORD_REVERTS = (
    ('4.2', 'The first imported-series release MUST keep these files inline in either store layout and MUST NOT provide views over imported data;',
     'The first imported-series release keeps these files inline in either store layout and provides no views over imported data;'),
    ('6.1', 'Release rows MUST be append-only and immutable once written.',
     'Release rows are append-only and immutable once written.'),
    ('6.1', 'A witnessed release cut MUST NOT set the flag, and the flag MUST NOT be added to or removed from an existing row.',
     'A witnessed release cut never sets the flag, and the flag is never added to or removed from an existing row.'),
    ('8.2', 'counters.toml MUST hold independent monotonic high-water values',
     'counters.toml holds independent monotonic high-water values'),
    ('8.2', 'Counters MUST NOT be reset and IDs MUST NOT be reused,',
     'Counters are never reset and IDs are never reused,'),
    ('8.2', 'Re-adoption MUST seed both series from a pinned ancestral snapshot and MUST refuse a missing required namespace; it MUST NOT zero-seed prior ancestry.',
     'Re-adoption seeds both series from a pinned ancestral snapshot and refuses a missing required namespace; it never zero-seeds prior ancestry.'),
    ('8.3', 'It MUST carry id, type, a one-line title,',
     'It requires id, type, a one-line title,'),
    ('8.3', 'The import table MUST carry source (',
     'The import table requires source ('),
    ('8.3', 'The required imported envelope and provenance fields MUST NOT be waived through missingness.',
     'The required imported envelope and provenance fields cannot be waived through missingness.'),
    ('8.6', 'Clean-to-imported links MUST be limited to relates, derives_from, follows, and same-type supersedes;',
     'Clean-to-imported links allow only relates, derives_from, follows, and same-type supersedes;'),
    ('8.6', 'Links MUST resolve over the union of both series; a dangling cross-series link MUST be a finding, never silently omitted.',
     'Links resolve over the union of both series; a dangling cross-series link is a finding, never silently omitted.'),
    ('14.1', 'The opf.adoption.plan/v2 plan MUST bind:',
     'The opf.adoption.plan/v2 plan binds:'),
    ('14.1', 'The attributed approval MUST bind plan_digest and inventory_digest,',
     'The attributed approval binds plan_digest and inventory_digest,'),
    ('14.1', 'Source instructions MUST be treated as historical data, never as instructions to execute.',
     'Source instructions are historical data, never instructions to execute.'),
)


def self_test():
    import _opf_adopt as adopt
    import _opf_import as importer
    import _opf_init as init
    failures = []
    checked = 0

    def check(name, thunk):
        nonlocal checked
        checked += 1
        try:
            if not thunk():
                failures.append(name)
        except Exception as exc:  # a reversal must be a named failed check, not a silent skip
            failures.append("{} ({})".format(name, exc))

    def refuses(thunk):
        try:
            thunk()
        except ValueError:
            return True
        return False

    suffix = "-20260917T120000Z-0123456789abcdef"
    prefixes = {"import": "imp", "ingest": "imp", "adoption": "adopt", "layout": "layout", "preview": "preview"}
    _staged_generation_self_test(check)
    _staged_root_self_test(check)
    check("control-boundaries", lambda: boundary_self_test() == 0)
    check("kinds", lambda: store.STAGING_KINDS == tuple(prefixes))
    check("homes", lambda: store.STORE_TREE_CONTROL_DIRS == ("imported", "archive", "staging", "journals"))
    check("reservations", lambda: store.RESERVED_MACHINE_SUBDIRS ==
          ("imports", "imported", "archive", "staging", "journals"))
    for constant, expected in (("IMPORTED_REL", ".working/imported"), ("ARCHIVE_REL", ".working/archive"),
                               ("STAGING_REL", ".working/staging"), ("JOURNALS_REL", ".working/journals")):
        check(constant, lambda c=constant, e=expected: getattr(store, c) == e)
    for kind, prefix in prefixes.items():
        run = prefix + suffix
        check("stage-" + kind, lambda: store.stage_run(kind, run) == ".working/staging/{}/{}".format(kind, run))
        check("evidence-" + kind, lambda: store.evidence_run(kind, run) ==
              ".working/imported/{}/{}".format(kind, run))
        check("journal-" + kind, lambda: store.journal_root(kind) == ".working/journals/{}/journal".format(kind))
        check("transaction-" + kind, lambda: store.txn_record(kind, run) ==
              ".working/journals/{}/runs/{}/transaction.toml".format(kind, run))
        for bad in (None, [], "", ".", "..", "journal", run + "\n", run + "/x", "x/" + run,
                    run.upper(), "wrong" + suffix):
            for function in (store.stage_run, store.evidence_run, store.txn_record):
                check("run-refusal-{}-{}-{!r}".format(function.__name__, kind, bad),
                      lambda: refuses(lambda: function(kind, bad)))
    for bad in (None, [], "", "adopt", "migration", "../import", "import/preview"):
        check("kind-refusal-{!r}".format(bad), lambda: refuses(lambda: store.journal_root(bad)))
        for function in (store.stage_run, store.evidence_run, store.txn_record):
            check("kind-refusal-{}-{!r}".format(function.__name__, bad),
                  lambda: refuses(lambda: function(bad, "imp" + suffix)))
    run = "adopt" + suffix
    for path in ("notes/a.md", "adoption/a.md", "space name.txt", "\u00e9.txt"):
        check("move-" + path, lambda: store.moved_dest(path) == ".working/archive/moved/" + path)
        check("preimage-" + path, lambda: store.retire_preimage(run, path) ==
              ".working/archive/adoption/{}/{}".format(run, path))
        check("file-owner-" + path, lambda: adopt._is_contained_filepath(path))
    for bad in (None, [], "", "/", ".", "..", "./a", "a/../b", "a//b", "a/", "/a", "C:a",
                "a\\b", "a\x00b", "a\nb", "a\x7fb", "a\x85b", "a\u2028b", "a\u2029b"):
        check("move-refusal-{!r}".format(bad), lambda: refuses(lambda: store.moved_dest(bad)))
        check("preimage-refusal-{!r}".format(bad), lambda: refuses(lambda: store.retire_preimage(run, bad)))
        check("file-owner-refusal-{!r}".format(bad), lambda: not adopt._is_contained_filepath(bad))
    check("preimage-run-refusal", lambda: refuses(lambda: store.retire_preimage("imp" + suffix, "a")))
    check("import-grammar-owner", lambda: re.compile("^imp" + store._HOME_RUN_SUFFIX + r"\Z").pattern ==
          importer._RUN_ID_RE.pattern)
    check("adoption-grammar-owner", lambda: re.compile("^adopt" + store._HOME_RUN_SUFFIX + r"\Z").pattern ==
          adopt._RUN_ID_RE.pattern)
    import_run = "imp" + suffix
    check("inventory-first", lambda: store.evidence_inventory("import", import_run) ==
          ".working/imported/import/{}/inventory.toml".format(import_run))
    check("inventory-phase", lambda: store.evidence_inventory("import", import_run, "evidence") ==
          ".working/imported/import/{}/inventory-evidence.toml".format(import_run))
    for bad in ("", "Evidence", "1st", "a/b", "a" * 33, 5, []):
        check("inventory-phase-refusal-{!r}".format(bad),
              lambda: refuses(lambda: store.evidence_inventory("import", import_run, bad)))
    check("inventory-run-refusal", lambda: refuses(lambda: store.evidence_inventory("import", "adopt" + suffix)))

    # Real discovery, including legacy-token upgrade discovery and an arbitrary custom machine name.
    with tempfile.TemporaryDirectory(prefix="opf-homes-") as tmp:
        for token in (store.STANDARD_TOKEN, store.PRIOR_STANDARD_TOKEN):
            for name in ("imports", "imported", "archive", "staging", "journals", "toml", "custom"):
                root = Path(tmp) / token / name
                machine = root / ".working" / name
                machine.mkdir(parents=True)
                (machine / "manifest.toml").write_text(
                    '[{}]\nstandard = "{}"\n'.format(token, token), encoding="utf-8")
                fd = store._open_root_fd(root)
                try:
                    def discovery():
                        try:
                            status, found, _ = store.discover_machine_store(fd, root, accept_tokens=(token,))
                        except store.StoreError:
                            return name in ("imports", "imported", "archive", "staging", "journals")
                        return name in ("toml", "custom") and status == "one" and found == name
                    check("discovery-{}-{}".format(token, name), discovery)
                finally:
                    store.os.close(fd)

    block = "# >>> opf-managed >>>\n/journals/\n/staging/\n# <<< opf-managed <<<\n"
    check("gitignore-render", lambda: store.render_homes_gitignore() == block)
    for text in (block, "# adopter\n" + block + "/local/\n"):
        check("gitignore-match", lambda: store.homes_gitignore_matches(text))
    for bad in (None, "", block + block, block.replace("/staging/", "/imported/"),
                block.replace("/journals/\n", ""), block.replace("\n", "\r\n"), block.rstrip("\n"),
                block.replace("# <<< opf-managed <<<", "# >>> opf-managed >>>")):
        check("gitignore-drift-{!r}".format(bad), lambda: not store.homes_gitignore_matches(bad))

    # OPF-D2B PR3a (decision 5): the base spec_version bump 1.1.0 -> 1.2.0 is an intended change
    # (init.toml provenance, with a tested opf upgrade route), no longer inert.
    check("inert-version", lambda: store.SUPPORTED_SPEC_VERSION == "1.2.0")
    check("inert-homes", lambda: store.SUPPORTED_HOMES == 1
          and store.homes_generation({"opf": {"homes": 2}}) == 1)
    check("inert-init", lambda: init._manifest_model()["opf"]["spec_version"] == "1.2.0"
          and "homes" not in init._manifest_model()["opf"])
    check("inert-import", lambda: (importer.IMPORTS_REL, importer.IMPORT_OPS_REL, importer.IMPORT_ARCHIVE_REL) ==
          (".working/imports", ".aiqt/import", ".aiqt/import-archive"))
    check("inert-root-exclusions", lambda: store.STORE_ROOT_CONTROL_DIRS == (".git", ".aiqt"))
    source = Path(store.__file__).read_text(encoding="utf-8")
    check("transitional-comment", lambda: "In homes 2, .aiqt is AIQT-only" in source
          and "Until homes 2 is activated, legacy import state still" in source)
    text = SPEC.read_text(encoding="utf-8")
    check("spec-contract", lambda: not contract_findings(text))
    # Each operative section independently discriminates: removing it must fail the drift gate.
    for section, body in _sections(text).items():
        if section in _CONTRACT:
            check("spec-flip-" + section, lambda: bool(contract_findings(text.replace(body, "\n", 1))))
    # Whole-section pin tiling, the former 9.2 mechanism extended to every registered section:
    # the ordered pins of each section must concatenate to exactly its normalized body, so
    # deleting ANY sentence there, prose, list row, table row or fenced block included, breaks
    # a pin and turns the gate red. Each pin occurs exactly once in its section body, and
    # deleting that single operative occurrence in place (never a replace-all) turns the gate
    # red with the pin's own finding.
    from unittest.mock import patch
    for section, fragments in _CONTRACT.items():
        body = _sections(text)[section]
        normalized_body = " ".join(body.replace("`", "").split())
        pin_prefix = "spec " + section + " missing contract"
        control = text.replace(body, "\n\n" + normalized_body + "\n\n", 1)
        # Scoped to pin findings: normalizing 4.2 inherently breaks its own structural checks
        # (the rendered gitignore block), which stay covered by the unmutated spec-contract check.
        check("spec-pin-control-" + section, lambda c=control, p=pin_prefix: not any(
            f.startswith(p) for f in contract_findings(c)))
        check("spec-tile-" + section, lambda s=section, n=normalized_body:
              " ".join(_CONTRACT[s]) == n)
        # Mutate the registry itself: per-pin deletion tests cannot notice a dropped pin, but
        # the tiling equality does.
        with patch.dict(_CONTRACT, {section: _CONTRACT[section][1:]}):
            check("spec-tile-dropped-pin-" + section, lambda s=section, n=normalized_body:
                  " ".join(_CONTRACT[s]) != n)
        for fragment in fragments:
            mutated_body = normalized_body.replace(fragment, "", 1)
            mutated = text.replace(body, "\n\n" + mutated_body + "\n\n", 1)
            finding = pin_prefix + ": " + fragment
            check("spec-pin-flip-" + section + "-" + fragment,
                  lambda f=finding, m=mutated, frag=fragment, nb=normalized_body:
                  nb.count(frag) == 1 and f in contract_findings(m))
    # Delete the wrapped sentence in place: normalization must not hide a lost requirement.
    body = _sections(text)["9.2"]
    mutated, removed = re.subn(r"The\s+upgrade\s+MUST\s+be\s+idempotent\.", "", body)
    check("spec-flip-9.2-idempotence", lambda: removed == 1 and
          "spec 9.2 missing contract: The upgrade MUST be idempotent." in
          contract_findings(text.replace(body, mutated, 1)))
    # Section 2 keyword lint: synthetic cases, then every registry marker and keyword flipped,
    # then named fix-5a rewrites reverted.
    check("spec-keywords", lambda: not keyword_findings())
    lint_cases = (
        ("A lease is never seized from a live holder.", True),
        ("Installation must inspect the index first.", True),
        ("MUSTERED rows and MAYBE rows.", True),
        ("A lease MUST NOT be seized from a live holder.", False),
        ("Choosing it SHOULD be a recorded decision.", False),
        ("A phase inventory MAY be published.", False),
        (_D("The closed kind vocabulary is import."), False),
        (_D("A lease MUST NOT be seized."), True),
        (_P("An occupied destination is in exactly one lifecycle state."), False),
        (_P("An occupied destination MUST be in one lifecycle state."), True),
    )
    for fragment, red in lint_cases:
        check("keyword-lint-case-{!r}".format(str(fragment)), lambda f=fragment, r=red:
              bool(keyword_findings({"14.2": (f,)})) == r)
    check("keyword-lint-pending-scope", lambda: bool(keyword_findings(
        {"8.2": (_P("Counters are never reset."),)})))
    # Stripping any marker, or lowercasing any pin's keywords, turns the lint red on that pin.
    for section, fragments in _CONTRACT.items():
        for index, fragment in enumerate(fragments):
            if isinstance(fragment, (_Descriptive, _PendingOccupiedView)):
                flipped = str(fragment)
            elif _KEYWORD.search(fragment):
                flipped = _KEYWORD.sub(lambda m: m.group(0).lower(), fragment)
            else:
                continue  # an unmarked keywordless pin is already red under spec-keywords
            expected = "spec {} requirement without a section 2 keyword: {}".format(section, flipped)
            with patch.dict(_CONTRACT, {section: fragments[:index] + (flipped,) + fragments[index + 1:]}):
                check("keyword-flip-{}-{}".format(section, index), lambda e=expected: e in keyword_findings())
    # Red on revert: each named fix-5a rewrite (QA5), reverted in the spec together with its pin
    # (the matching registry edit tiling cannot see), turns the keyword lint red; reverted in the
    # spec alone, it turns the pin check red.
    for section, new, old in _KEYWORD_REVERTS:
        body = _sections(text)[section]
        normalized_body = " ".join(body.replace("`", "").split())
        pin = [f for f in _CONTRACT[section] if new in f]
        reverted = pin[0].replace(new, old) if len(pin) == 1 else None
        mutated = text.replace(body, "\n\n" + normalized_body.replace(new, old, 1) + "\n\n", 1)
        registry = tuple(reverted if f is pin[0] else f for f in _CONTRACT[section]) if reverted else ()
        with patch.dict(_CONTRACT, {section: registry}):
            check("keyword-revert-registry-" + section + "-" + old, lambda r=reverted, s=section:
                  r is not None and not _KEYWORD.search(r) and
                  "spec {} requirement without a section 2 keyword: {}".format(s, r) in keyword_findings())
            check("keyword-revert-unseen-by-pins-" + section + "-" + old, lambda m=mutated, s=section:
                  not any(f.startswith("spec " + s + " missing contract") for f in contract_findings(m)))
        check("keyword-revert-spec-" + section + "-" + old, lambda p=pin, m=mutated, s=section:
              len(p) == 1 and "spec {} missing contract: {}".format(s, p[0]) in contract_findings(m))
    for failure in failures:
        print("FAIL: " + failure)
    print("OPF-HOMES SELF-TEST: {} ({} checks)".format("FAILED" if failures else "OK", checked))
    return 1 if failures else 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["--self-test"]:
            return self_test()
        if args:
            print("check_opf_homes: unexpected arguments", file=sys.stderr)
            return 2
        findings = contract_findings(SPEC.read_text(encoding="utf-8")) + keyword_findings()
        for finding in findings:
            print("check_opf_homes: " + finding)
        return 1 if findings else 0
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        print("check_opf_homes: cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
