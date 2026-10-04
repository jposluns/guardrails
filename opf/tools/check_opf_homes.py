#!/usr/bin/env python3
"""Homes contract, control-boundary and gitignore drift gate (writers still use legacy homes).

Checks documented topology against the constructor authority. Text checks protect the planned
contract, including the 1.3.0 adoption/import target, not runtime conformance to either target. No store is mutated or required to install a block.
The registered pins tile each registered section completely (the self-test proves the
concatenation equality per section), so deleting any sentence in a tiled section turns the gate
red. Residual: the default gate's pins are substring checks over normalized text, so added
text, a pinned sentence neutralized by appended text included, stays green under the default
entry alone; the self-test's per-section tiling equality turns red on any addition inside a
tiled section. The live residuals are additions to untiled sections (for example 8.7, 9.1, 10
and 13), a default-gate run without the self-test, and an addition landed together with a
matching registry edit, which review of registry changes catches. Section 9.1 stays untiled
over its lowercase-may profile rule, but its implementation-class paragraph is pinned sentence
by sentence (_PINNED), so deleting or rewording a pinned sentence there turns the gate red;
the rest of 9.1, and additions to it, stay unprotected. The quickstart's implementation-class
sentences and the opfiles.ai disclosure page's fresh-only item are pinned the same way over
tag-stripped text (_SURFACE), outside the keyword lint; the rest of those files, and additions
to them, stay unprotected.

The default entry also runs a section 2 keyword lint over every registered pin: a pin without
MUST, MUST NOT, SHOULD or MAY is red unless the registry marks it descriptive (_D). Residual:
the lint reads the registry marker, not the meaning, so a requirement wrongly marked descriptive
stays green; review of registry changes catches that.
"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store as store  # noqa: E402

SPEC = Path(__file__).resolve().parents[1] / "spec" / "OPF-SPEC.md"
QUICKSTART = SPEC.parent / "OPF-QUICKSTART.md"
DISCLOSURE = SPEC.parents[1] / "site" / "disclosure.html"


class _Descriptive(str):
    """Pin marker: descriptive by intent, so the section 2 keyword lint accepts it without MUST,
    MUST NOT, SHOULD or MAY. It covers definitions, format, layout and field descriptions,
    examples, rationale, status and activation notes, disclosed residuals, restatements of a
    keyword requirement stated elsewhere, and enumerated items whose obligation a keyword lead-in
    carries. A marked pin that carries a keyword is itself a finding."""
    __slots__ = ()


_D = _Descriptive
# Section 2 keywords, case-sensitive and whole-word: a lowercase "must" is not a keyword.
_KEYWORD = re.compile(r"\b(?:MUST|SHOULD|MAY)\b")
# Scope each wording check to its operative section, never a whole-document negative scan.
# Ordered whole-section tiling: each tuple, space-joined, equals exactly its section's
# normalized body (the self-test proves that equality), so deleting any sentence in a
# registered section breaks a pin and turns the gate red. Every pin is also keyword-linted
# (keyword_findings): _D marks a descriptive pin.
_CONTRACT = {
    "0": (
        _D('# OPFiles: the operational-files standard Formal name: AIQT Development Operational Standard.'),
        _D('Public brand: OPFiles (opfiles.ai).'),
        _D('Base discovery token: opf.'),
        _D('Status: draft (specification only; schemas and the reference tooling, the scaffolder opf init, the adopter opf adopt, the post-adoption importer opf import, the validator opf doctor, the renderer opf render, the relocator opf migrate, the synchronizer opf sync, the schema-upgrader opf upgrade, the absorber opf absorb, and the record author opf record, ship in later releases).'),
        _D('Date: 2026-10-03 (UTC).'),
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
        _D('The 1.3.0 requirements in sections 4.2, 4.5, 6.1, 8, 9.1, 9.2, 11, 12, 14, and 16.1 are a target contract; they do not claim that the reference tooling has activated adoption, imported-series validation, or the import writer.'),
        'Activation MUST include deterministic doctor coverage before a writer accepts the new format.',
        'For an upgrade-capable implementation (section 16.1), activation MUST also include the tested upgrade in section 9.2; a fresh-only implementation activates without it and MUST instead include the section 16.1 admission check and its refusal evidence.',
        _D('Homes 2 is a separate, later activation.'),
        _D('The homes-2 contract below is for spec_version = "2.0.0" and [opf].homes = 2.'),
        _D('The current reference tooling reserves these names and implements their homes-2 boundary checks.'),
        _D('It still supports 1.2.0 and initializes legacy homes (generation 1, with no homes key); writers retain their legacy paths until homes 2 is activated, which requires the homes migration (opf upgrade) to be available.'),
        _D('The section 9 manifest example describes the 1.3.0 target on legacy homes.'),
        _D('The homes-2 requirements in sections 4.2, 9.2, 12, 14.1, 14.2, and 15 describe the target contract, not an activated runtime guarantee.'),
        _D('<product repository root>/ .opf.toml # committed store pointer (section 4.3) CHANGELOG.md # curated public changelog (deliverable; section 6.3) VERSION # deterministic render from version.toml (deliverable) .working/ # the store; present here under the default in-repo configuration <store repository root>/ # the product repository itself, by default .working/ README.md # ownership and regeneration note (deliverable) WORKLOG.md # deterministic render of worklog.toml VERSION.md # optional human view of version.toml TODO.md BACKLOG.md PIPELINE.md DONE.md FINDINGS.md DECISIONS.md BLOCKS.md HANDOFF.md REFERENCES.md CONTRIBUTIONS.md DECISIONS.toml # machine projection (deterministic; section 10.5) <TYPE>-INDEX.md ...'),
        _D('# optional 1:1 index mirrors (section 10.1) IMPORT-REPORT.md # legacy import report (pre-1.3.0 runs only; section 14.1) toml/ manifest.toml # store manifest and discovery marker (section 9) counters.toml # per-namespace ID high-water marks (section 8.2) version.toml # version and release ledger (section 6.1) worklog.toml # durable operational record (section 6.2) <type>.imported.index.toml # separate imported records of each enabled type (section 8.3) worklog.imported.toml # imported worklog, on its own ID number line lease.toml # single-writer lease, present only while held (section 5.7) init.toml # bootstrap provenance of a coupled init (section 9.2), if present backlog_item.index.toml # typed record files (section 8) done.index.toml finding.index.toml pending_decision.index.toml autonomous_decision.index.toml block.index.toml handoff.index.toml reference.index.toml contribution.index.toml maintainer_decision.index.toml preference_pattern.index.toml archive/ 2026/ archive.toml # enumerates rotated IDs and spans (section 12) done.index.toml worklog.toml archive/ moved/<source-path> # default Move destinations (section 14.2) adoption/<run-id>/ # retire preimages and archived occupying sources (section 14.2) imported/<kind>/<run-id>/ # durable originals, inventories, approvals and receipts (section 14) staging/<kind>/<run-id>/ # short-lived staging, evidence-gated reclamation (section 14.1) journals/<kind>/ # reserved recovery state, never a view or ordinary op target journal/ # crash-durable frames runs/<run-id>/transaction.toml # gate-readable projections allocations/<run-id>.toml # irrevocable ID reservations (section 8.2) When the store has been relocated, the .working/ tree lives at the store repository root exactly as drawn, and the product repository keeps only the pointer and the public deliverables.'),
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
        _D('The closed kind vocabulary is import, ingest, adoption, layout, preview, record.'),
        _D('Import and ingest run IDs use imp-<YYYYMMDD>T<HHMMSS>Z-<hash16>; adoption uses adopt- with that suffix.'),
        _D('Layout and preview reserve layout- and preview- with the same suffix.'),
        _D('Record reserves record- with the same suffix; it is a journal-only kind that names just its journal home, journals/record/, never a staging or imported home.'),
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
        _D('A row may name a member of its own bundle other than a bundle-root inventory, a default Move destination under .working/archive/moved/, or, for an adoption bundle, a preserved file of the same run, a retire preimage or an archived occupying source, under .working/archive/adoption/<run-id>/.'),
        "The owning writer or migration MUST derive each inventory from the run's transaction record or receipt and MUST publish it exclusively with the retained bytes.",
        'An inventory MUST NOT be rewritten, so a bundle stays immutable and an evidence commit changes only its bundle folder.',
        _D('An inventory is not a journal projection and remains available in a clone without journals.'),
        'A phase inventory MAY be published in the same transaction as the base inventory to claim the promotion receipt without creating a receipt/inventory digest cycle.',
        _D('C-EVIDENCE-ENUM reconciles exact membership, directory structure, regular-file types, sizes and digests: every payload file under .working/imported/ and .working/archive/, meaning every file other than a bundle-root inventory, is claimed by exactly one row, and every inventory is itself schema-checked against the shape above rather than claimed.'),
        'Unlisted or unclaimed entries, a bundle without an inventory, and missing listed files MUST be findings; unreadable or malformed inputs, including a path claimed twice, MUST yield cannot-evaluate.',
        'The recognized legacy format opf.ingest.evidence-inventory/v1 MUST be refused as the named legacy-ingest-inventory finding under C-EVIDENCE-ENUM, without migration or rewriting; completed-ingest replay cannot evaluate that bundle.',
        'A fresh-only implementation (section 16.1) refuses such an inventory earlier, as unsupported-legacy-state at admission, and MUST NOT then grade that store under C-EVIDENCE-ENUM.',
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
    "4.5": (
        _D('Within the resolved store repository, tooling locates the machine store by finding exactly one immediate subdirectory of .working/ containing a manifest.toml that declares standard = "opf" in its [opf] base table, trying toml first.'),
        _D('Zero matches, or more than one, is a cannot-evaluate outcome: the tool reports it and stops; it never guesses, and never treats it as an empty or absent store.'),
        _D('The pointer names which repository carries the store; the manifest discovery observes where, within it, the machine store sits.'),
        _D('Because the machine subdirectory is observed at each use rather than declared and trusted, renaming it is a directory move with nothing to go stale; the pointer, which does declare a location, is validated at every resolution and fails closed rather than trusting a stale target (section 4.3).'),
        _D('Profiles declared under [profiles.<name>] are enumerated after the base is validated; a profile a tool does not support is ignored for enforcement and recorded as unevaluated, never treated as a base-validation failure (section 9).'),
        'A fresh-only implementation (section 16.1) MUST recognize a candidate manifest.toml whose base table is the retired [devprocess] only to refuse that store as unsupported-older-store; the recognition is not a discovery match.',
        'For a fresh-only implementation, a recognized legacy candidate, an ambiguous outcome, and an input that discovery cannot read or parse are a failed discovery of an existing store and MUST NOT be treated as an absent store that permits initialization or adoption; a recognized legacy candidate beside a matching store makes the outcome ambiguous, which yields cannot-evaluate, not unsupported-older-store, a refusal that applies only where no matching store accompanies the legacy candidate.',
        'A fresh-only implementation MUST NOT treat a zero-match outcome over a present .working/ as authorizing initialization or adoption by itself.',
        'In every command that resolves a store and meets that outcome, as part of its section 16.1 admission check and so before every write that check precedes, it MUST run the section 16.1 legacy-state search over that .working/ tree and MUST refuse each listed item it finds as unsupported-legacy-state, beside the zero-match cannot-evaluate result; where that .working/ holds no recognized legacy candidate and no section 16.1 legacy-state item, only a section 14 first adoption authorizes initialization there, through an investigation that distinguishes first adoption from re-adoption and records a digest-stamped inventory, and one approved plan that gives every foreign file a disposition before init-store (section 14.1).',
        'Because a zero-match outcome discovers no store manifest, that search MUST apply the section 16.1 rule for a store with no discovered manifest, exactly as section 16.1 states it, and a cannot-evaluate result of that search authorizes neither initialization nor adoption.',
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
        'A fresh-only implementation (section 16.1) provides no legacy LF validation and MUST refuse a store that declares the legacy_fragment type or holds an LF record.',
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
        'That acceptance binds an upgrade-capable implementation (section 16.1); a fresh-only implementation never reaches it, because the withdrawn legacy receipt is legacy state that it MUST refuse under section 16.1.',
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
        "A pending_decision's open > decided, landing bare or /proposed, MUST write its resolution bundle (section 8.5) in the same act: transition MUST require the decision and decided_by values there and MUST refuse them on every other transition.",
        "decided_by MUST be given explicitly, never inferred from the actor, because the recorder of an answer is often not its decider, and decided_at MUST be the operation's clock value.",
        'A ratification MUST keep the bundle; a rejection of decided/proposed MUST remove it together with proposed_from, since an open decision carries none of it.',
        "A transition that lands a pending_decision at unqualified decided, a maintainer's open > decided or a maintainer's ratification of decided/proposed, MAY supersede the current resolution of a chain in the same act: transition then appends the supersedes link (section 8.5) to the record's links.",
        'Before anything is written, the superseded record MUST be another pending_decision at unqualified decided, schema-valid, and the head of its chain (no pending_decision already links supersedes to it), and its own chain MUST NOT lead back to the superseding record.',
        'These target checks MUST read the same records as the chain rule below, the active index together with every archived record (section 12), so a decided, schema-valid chain head rotated to the archive can be superseded, and an archived pending_decision that already supersedes the target or leads back to the superseding record is counted.',
        'transition MUST refuse the link on every other transition, a /proposed landing included, because the doctor counts a supersedes link from a proposal too and would then find the superseded chain without a current resolution.',
        "Every transition that lands a pending_decision at unqualified decided, with or without a supersession, MUST then apply the doctor's chain rule (section 8.5) to the planned index together with every archived record before anything is written: the chain the record belongs to afterwards, a connected component over supersedes links in either direction, MUST have exactly one current effective resolution.",
        _D('So transition also refuses to decide a record that a pending_decision not at unqualified decided already supersedes when no other member of its chain is a current resolution, and a supersession that passes the checks above but still leaves the chain without a current resolution.'),
        'The rule MUST read the records the doctor reads for that check, the planned index together with every archived record (section 12), so a chain member rotated to the archive is judged before anything is written.',
        "With no archive tree nothing has rotated; an archive input that the doctor's archive walk finds missing, unreadable, malformed or otherwise defective MUST refuse the transition with nothing written, never be skipped.",
        "A contribution's proposed > sent, landing bare or /proposed, MUST write its delivery bundle (section 8.5) in the same act: transition MUST require the channel and ref values there and MUST refuse them on every other transition, and sent_at MUST be the operation's clock value.",
        "The send MUST refuse a record that already carries delivery, so a rejection of sent/proposed removes the bundle whole and restores the record as it was before the proposal; a ratification MUST keep the bundle.",
        _D("A record that carries a planned delivery at proposed (section 8.5) therefore cannot be sent by transition; the way out is to withdraw it and create a new contribution, whose send writes the bundle."),
        _D("transition does not read a standing authorization (section 8.4): an assistant or automation send lands sent/proposed even where the authorization would permit a bare sent, for a maintainer to ratify."),
        "A recipient receipt reference MAY be given only on sent > acknowledged, landing bare or /proposed: transition then adds receipt_ref and receipted_at, the operation's clock value, to delivery, and MUST refuse the value on every other transition.",
        'A rejection of acknowledged/proposed back to sent MUST remove those two keys.',
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
        'One invocation covers one source in one journaled transaction, followed by one render and one full doctor; a frozen migrate, move or retire source (section 14.2) MUST NOT be written, and doctor grades it under the section 11 bounded treatment, never as an obstacle to VALID.',
        'create, transition, and done-with-receipt MUST each append their own worklog entry, one entry per change (section 6.2), in the same journaled transaction as the change.',
        "Its detail MUST open with a lifecycle line naming the record and its status change (opf-record create <ID> <status>, or opf-record transition <ID> <from> -> <to>); a rejection's entry MUST also record its reason.",
        'worklog-append MUST refuse a detail that opens with that lifecycle grammar.',
        'Every subcommand runs one operation sequence, and an implementation of the verb MUST preserve its guarantees: 1.',
        "Resolve the store, then any interrupted authoring transaction MUST be reconciled first.",
        'Reconciliation writes the store, so it MUST run only under the single-writer lease that publication uses: a held lease MUST refuse before any recovery write and MUST NOT be seized.',
        'An operand changed since the interruption, to bytes that are neither its journaled prestate nor its planned poststate nor a write of either torn by the interruption, MUST be reported and refused, never overwritten.',
        'A reconciled interruption MUST refuse the new operation, so the operator inspects it before anything new is written.',
        _D('A fresh-only implementation (section 16.1) performs that reconciliation only after its section 16.1 read-only pre-scan and within the section 16.1 recovery bound, and runs its admission check after it.'),
        _D('2. Precondition: re-emitting the unchanged parsed model of every file the operation rewrites reproduces its on-disk bytes exactly (the section 9.2 rule).'),
        'A file carrying comments or non-canonical serialization MUST be refused and left untouched.',
        _D('The check proves serialization only: a hand edit or hand merge that leaves canonical bytes is not detectable by it, and the integration-base merge policy of section 5.7 remains a separate requirement.'),
        _D('3. Claim each new ID as one atomic act (section 8.2).'),
        'At homes 1 the counters.toml advance MUST be an operand of the same journaled transaction, under the held lease, and IDs MUST be reported only once that transaction has completed, so a rollback never withdraws an ID anyone has seen.',
        _D('At homes 2 the claim is an irrevocable reservation in the journal home, made before the reversible publication (section 4.2).'),
        _D("4. Postcondition: the model diff of every rewritten file equals exactly the operation's allowed delta (the new rows appended, the counters advanced by exactly the claim, and for a transition one status and updated_at change plus the proposed_from write or removal and, for a pending_decision, the resolution bundle write or removal and the supersedes link append), value for value and type for type (a boolean or float is never equal to an integer), before anything is written."),
        'The expected delta MUST be derived from the request, the prior bytes of each rewritten file, the claimed IDs, the clock value, and the schema rules, never from the planned rows themselves.',
        '5. The in-repo store contract (section 5.7): the planned destinations MUST be clean, including ignored files, and the single-writer lease MUST be held across publication, render, and the final doctor.',
        '6. Every rewritten file MUST be published in one crash-durable journaled transaction, so an interruption leaves the store exactly at its prestate or exactly at its poststate once reconciled.',
        _D('The reference tooling keeps that journal under .aiqt/record/journal at homes 1 and under .working/journals/record/journal at homes 2.'),
        _D('7. The declared views are rendered.'),
        'Then a full doctor MUST report VALID; a failure leaves the change for review with recovery advice scoped to the planned paths.',
        "One exception applies to a status change: doctor compares it with the prior committed snapshot, and doctor's history comparison sees only the prior snapshot's type and status, which identify neither the transitioning actor nor the pre-proposal state; doctor validates proposed_from but does not use it as rejection evidence, so doctor MAY grade that change cannot-evaluate until the change is committed.",
        "The verb's render and final doctor MUST accept that cannot-evaluate only for exactly the record and the from and to statuses it has just written, never a finding and never any other cannot-evaluate, and the verb MUST report it as pending until commit.",
        _D('opf doctor itself is unchanged and still reports it until then.'),
        '8. The lease MUST be released, and only then are the claimed IDs and touched files reported.',
        'The change MUST be left uncommitted in the working tree: the verb MUST NOT stage or commit it.',
        'The verb MUST exit 0 when the change is recorded and the store is doctor-VALID (or carries only the pending cannot-evaluate of item 7), and 2 on every refusal or cannot-evaluate.',
        'Import MUST preserve that operation sequence, including the one allocation seam, independent model delta check, cleanliness gate, lease and reconcile-first recovery.',
        _D('Its atomic operands are the imported counter rows first, the touched <type>.imported.index.toml and worklog.imported.toml files, and the evidence bundle: the exact original at .working/imported/import/<run-id>/originals/<source-path> and inventory.toml in the retained opf.evidence.inventory/v1 format (section 4.2).'),
        _D('The journal remains .aiqt/record/journal at homes 1 and .working/journals/record/journal at homes 2.'),
        'It MUST NOT rewrite clean records or clean counters.',
        'Import MUST refuse with exit 2 on a non-importer actor, absent or invalid provenance, a clean-series record operand, an imported-to-clean link, a historical timestamp later than the run clock, an unknown missingness reason, or a type not enabled and supported by the writer.',
        "It MUST also refuse without an adoption receipt, outside that plan's approved migrate-source scope, or when any section 14.1 bound item, the source bytes against the plan digest (archived under section 14.2 or frozen live), the tool release identity, or the prompt-pack version and digest included, no longer matches the approved plan.",
        'Bound-item drift, in this session or a later one after a tool upgrade, MUST refuse into a fresh plan with its own single approval; import MUST NOT reinterpret the old approval.',
        _D('Replay identity is (source_sha256, batch content digest).'),
        'A completed identical replay MUST re-read and verify the published records and evidence, then succeed as a no-op reporting the existing IDs.',
        'A partial overlap, including a differing batch against the same source within the run, MUST refuse, naming the overlap and directing to journal recovery.',
        'Each source admits at most one completed batch per correction chain: a differing batch against a source whose batch already completed MUST refuse and name the completed run, unless the new batch declares that run as the completed run it corrects.',
        "Such a corrected import is a fresh run; its records MUST link supersedes or corrects to the imported records they replace, the previous run's immutable evidence MUST be preserved, and source completion (section 14.1) evaluates the correcting run.",
        "Before the source's recorded retirement, the source-bytes plan check of the refusal list MUST bind a correcting run as it binds any other; after that retirement, the preserved original under .working/imported/import/<run-id>/originals/ is the source of record, the digest bound is its recorded source_sha256, and the source-bytes check does not apply to the retired path.",
        'Retry MUST NOT allocate duplicate IDs.',
    ),
    "9": (
        _D(".working/toml/manifest.toml is the store's control document and discovery marker."),
        _D('The optional [opf].worklog key selects the active worklog storage generation, independently of [opf].homes and layout.'),
        _D('Omission and integer 1 select the legacy worklog.toml ledger.'),
        _D('This build supports only generation 1: integer 2 is reserved and refused before source selection; booleans, other types, and other integers are invalid.'),
        _D('Archive buckets retain their legacy worklog.toml shape.'),
        _D('Recognizing this key does not activate generation-2 writers or migration.'),
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
        "A base-schema version bump MUST ship a tested, in-place store-schema upgrade (opf upgrade) in at least one published upgrade-capable implementation (section 16.1), the reference tooling, so that a store below the new version has an upgrade path once that upgrade activates; the path holds only within this section's preconditions and refusals and that implementation's disclosed residuals.",
        'Every requirement of this section on an upgrade, its deltas, preconditions, report, refusals, and remedies binds an upgrade-capable implementation; a fresh-only implementation implements none of them and MUST instead refuse under section 16.1.',
        "Every implementation class MUST apply the version ceiling's refusal at the end of this section, a fresh-only implementation at its section 16.1 admission check; a fresh-only implementation that supports a version below the reserved homes-2 declaration (section 4.2) MUST NOT apply the ceiling's homes-2 recognition and MUST refuse that declaration under section 16.1.",
        'The upgrade MUST be idempotent.',
        'A purely schema-level bump MUST be additive, using atomic replacement of existing files, create-only writes for new index files, and regeneration of declared views through exclusively created temporary files followed by atomic rename.',
        _D('These writes are sequential, with recovery scope held in memory, not a durable transaction journal.'),
        'A homes-generation bump MUST additionally relocate OPF control areas as a versioned, journaled, fail-closed relocation.',
        'Every destination MUST be digest-verified before its source is removed.',
        'Both kinds of upgrade MUST run under the store consistency contract and the single-writer lease (section 5.7).',
        'It MUST fail closed on an unresolvable store, a declared spec_version ABOVE the tooling, a divergence, a held lease, or any populated state that contradicts its preconditions; it MUST NOT lower the fail-closed floor.',
        'Before any write it MUST enforce two fail-closed preconditions: it claims the single-writer lease (section 5.7) and holds it across the whole mutation, and it verifies the working tree is clean, including ignored files, over the planned schema and render destinations (store and product roots) and the index collision candidates, so the committed HEAD is a verified restore path for that scope; a held lease or a dirty store refuses, and a dirty store is asked to commit its own changes, never restored by the tool.',
        "After applying the schema delta, it MUST regenerate the declared views and MUST require a full doctor VALID before offering the uncommitted change for the adopter's own branch-and-merge.",
        'It MUST NOT stage or commit the change.',
        "Both upgrade kinds, on the schema-level path and the homes-generation relocation path, the homes-2 path included, alike, MUST NOT write the adoption archive and MUST NOT overwrite, relocate or remove a plan-enumerated frozen source (section 14.2); a frozen source sits outside the planned schema and render destinations, so the clean-tree precondition does not read it, and the required doctor VALID grades it under the section 11 bounded treatment.",
        "An upgrade delta or manifest change that would declare a new view or managed store path at a registered [unmanaged] path or at a plan-enumerated frozen source MUST refuse, naming the collision; the remedy is the adopter's own fresh plan re-dispositioning that file (section 14.2).",
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
        "The upgrade MUST refuse before any write a store whose [unmanaged] entry equals or contains a discovery candidate (section 14.2), which an earlier store can carry, naming the entry and the candidate; the remedy is the adopter's own fresh plan re-dispositioning that candidate (section 14.2), recorded before the upgrade is retried.",
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
        _D('"partial" means the adoption receipt enumerates migrate-disposed sources whose completion results are not yet recorded green (section 14.1); it can persist across assistant sessions without a process running.'),
        _D('"complete" means every such source has a green completion result and recorded retirement under section 14.1 with a live full doctor VALID, or that a pre-1.3.0 legacy import finished and its preserved legacy run evidence substantiates it; the section 9.2 upgrade preserves that legacy status without fabricating an approval or receipt.'),
        'A partial or complete status that neither an adoption receipt with completion results nor preserved legacy import evidence substantiates MUST fail closed, as does a missing, unreadable or contradictory input.',
        'Elapsed time and a staging directory MUST NOT be taken as proof of status.',
        'A fresh-only implementation (section 16.1) MUST NOT use the preserved-legacy-evidence route: it substantiates partial and complete only through an adoption receipt with completion results, and it refuses a partial or complete status in a store that holds no adoption receipt as legacy state under section 16.1, which yields cannot-evaluate instead where it cannot establish whether the store holds one.',
        'The section 16.1 admission check belongs to neither layer above: it MUST run at every posture, and off and warn MUST NOT disable or soften it.',
        'From the recorded approval until its retirement is recorded, a path the approved plan enumerates as a frozen retire, move or migrate source (section 14.2) is bounded adoption state: while its live bytes still match its plan digest, containment MUST report it as migration_incomplete detail rather than failing it, at "none" during a clean start as much as at "partial".',
        'A digest mismatch (a drifted source) or an unenumerated path MUST remain a containment-gate failure at required; the bounded treatment is never a blanket exemption.',
        'A formerly occupied destination needs no bounded treatment: its occupying source was archived at apply (section 14.2), the destination is an ordinary managed path from apply onward, and a check that reads the adoption archive, the section 14.1 completion check and import included, MUST fail at required, naming the path, on a missing, unreadable or digest-mismatched archived copy.',
        _D("Adoption coverage (which types are populated, which modules are wired, how much of the project's operational surface has moved into the store) is a report, never a gate: breadth of adoption is a journey, and failing a build over it would train bypasses."),
        'It MUST stay report-only at every posture.',
        'Defaults: scaffolding and clean-start adoption MUST write posture = "required" and import_status = "none".',
        'An adoption with migrate-disposed sources keeps required and MUST hold import_status = "partial" until each source\'s completion result is recorded green (section 14.1), then "complete".',
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
        _D('The record-rotation archive under the discovered machine store is distinct from the store-tree archive/ in section 4.2, which retains relocated adopter files, adoption preimages and archived occupying sources (section 14.2), never rotated records.'),
        'Tooling MUST NOT scan either as the other.',
        'Imported originals, acceptance evidence, retirement preimages, and archived occupying sources MUST be retained indefinitely by default, except for the pre-commit abort reversal of archived occupying sources and retirement preimages written by the aborted, uncommitted apply in section 14.2.',
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
        'It MUST end with rendered views at every declared destination, a destination whose occupying source apply archived under section 14.2 included, and with an adoption receipt plus its outcome-event chain.',
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
        'The clean-start completion check MUST deterministically verify the following roster: 1. Authority and freshness: roots, destinations and live preimages still match the approved plan.',
        _D('2. Discovery accounting: every inventory entry has a disposition or recorded exclusion.'),
        _D('3. Preservation and restore: each retirement preimage, preserved at apply for a retire-disposed file and for a non-occupying migrate-disposed source alike, and each archived occupying source (section 14.2) exists, digest-matched, under .working/archive/adoption/<run-id>/, and a restore exercise reproduces its bytes.'),
        _D('4. Operational readiness: the store resolves to the planned identity, and CI asserts store presence and identity so absence cannot pass as NOT-APPLICABLE.'),
        _D('Doctor validity and declared-view byte drift are evaluated on the live tree, where apply has already rendered every declared view, a formerly occupied destination included, and any drift the plan does not account for fails the check.'),
        _D('Consumer repointings match the plan.'),
        _D('5. Wiring: the enforcement pack is installed and probed, with a direct store write denied, a write under .working/archive/adoption/<run-id>/ denied, and sanctioned writer and render paths succeeding on the live tree.'),
        _D("Server-side branch protection is adopter-attested, explicitly outside the local probe's guarantee."),
        _D('6. Retirement readiness: each retire-disposed file that occupied no managed destination is present in the live tree and its live bytes still equal its plan digest, and each archived occupying source still equals its plan digest in the archive.'),
        'Retirement MUST be recorded only on a green completion check.',
        'For an occupying source, apply has already archived and removed it under section 14.2, so the green check records its retirement without touching the live tree, and MUST re-verify its archived bytes against the plan digest before recording.',
        'For an old file at no managed destination, the physical removal or relocation MUST also wait for the green check: the file MUST stay frozen, byte-identical in place, its live bytes MUST still equal the plan digest when the removal or relocation runs, and that removal or relocation MUST run as one journaled, recoverable transaction under the section 5.7 consistency contract and lease.',
        'A completion-check failure or cannot-evaluate MUST report incomplete and MUST retire nothing; changing the approved work MUST take a fresh plan.',
        'Once apply commits, restoring an archived file to the live tree is new approved work, never recovery: it MUST take a fresh plan with its own single approval, whose apply copies the archived bytes, digest-verified, to the planned destination, and the archived copy MUST stay retained afterward.',
        "Before an apply commits, an abort reverses that apply's own uncommitted writes from journaled preimages (section 14.2); the reversal is not a restore and MUST NOT be read as one.",
        'Green proves preservation, restorability and operational coverage, never semantic fidelity or fulfilment of old obligations; the receipt MUST disclose that limit.',
        'The enforcement pack MUST freeze each plan-enumerated old file that remains in the live tree until its retirement is recorded, MUST deny writes under .working/archive/adoption/<run-id>/, and MUST protect both record series, counters, declared views and evidence.',
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
        "For each migrate-disposed source, import completion MUST verify the preserved original's digest (the archived copy under section 14.2 for a source that occupied a managed destination, the frozen live file and its apply-archived retirement preimage otherwise), at least one imported record referencing that source or a recorded skip under the approved policy, and doctor VALID over both the strict store and the imported series, evaluated on the live tree.",
        "It MUST record the source's result in the adoption receipt's outcome-event chain.",
        "A green result records the source's retirement: for a frozen live source, an imported and a skipped one alike, one journaled transaction MUST record the retirement and remove the file, whose live bytes MUST still match the plan digest, and hence its apply-archived retirement preimage under completion check 3, when that transaction runs, and an archived source is already out of the live tree, so its recorded retirement changes no live path.",
        'import_status MUST become "complete" only when every migrate source completes on those terms; import is never complete on a state that is not doctor-VALID.',
        _D('This proves source accounting and preservation, not byte-level mapping coverage or semantic fidelity.'),
        _D('No writer-side leftover accounting is required.'),
        'Clean-start adoption and import ship on homes 1 with explicit evidence coverage: their completion checks MUST re-read inventories and payload digests themselves, because C-EVIDENCE-ENUM is inactive until homes 2.',
        _D('The evidence-bundle format in section 4.2 is retained, including the import home .working/imported/import/<run-id>/.'),
        'Preservation durability and digest verification MUST precede source removal, at apply for an archived occupying source and at recorded retirement for a frozen one, and every removal MUST run in a journaled, recoverable transaction.',
        'A source outside the participating roots MUST be a plan-time cannot-evaluate naming that source.',
        'Investigation, planning and read-only status commands MUST remove nothing.',
        'Legacy runs MAY occupy .working/staging/import/<run-id>/ or .working/staging/ingest/<run-id>/.',
        'Their evidence inventories and readers remain available; old acceptance records describe those runs and MUST NOT authorize a new adoption or retirement.',
        'A legacy import completed under the pre-1.3.0 contract MUST keep its recorded status, substantiated by its preserved run evidence (section 11); a retrospective approval or receipt MUST NOT be fabricated.',
        "Preserved legacy run evidence is that run's durable archive in its recorded legacy home, for the reference tooling .aiqt/import-archive/<run-id>/, holding the run's acceptance record and its evidence inventory in the retained legacy format; substantiation MUST re-read that inventory and digest-match every file it enumerates, and a missing, unreadable or digest-mismatched item MUST leave the status unsubstantiated, failing closed under section 11.",
        'A fresh-only implementation (section 16.1) provides none of these legacy readers: it MUST refuse, under section 16.1, a store that holds a listed legacy-state item, MUST NOT alter its recorded status, and MUST NOT substantiate a status from legacy run evidence; a legacy run that leaves no listed item is a disclosed residual (section 17).',
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
        'An [unmanaged] path MUST NOT equal or contain a discovery candidate, a manifest.toml present in an immediate subdirectory of .working/ (section 4.5); such an entry is a contradictory input and MUST yield cannot-evaluate, so the keep protection never covers a file that discovery reads.',
        'A keep declaration whose path equals or contains a discovery candidate MUST refuse at plan time, naming that candidate, the same way a collision refuses below, and the section 9.2 upgrade into 1.3.0 refuses a store that already carries such an entry.',
        _D('A no-follow existence probe of manifest.toml in an immediate subdirectory of .working/, a kept one included, used only by discovery, by this rule, and by the section 16.1 admission check, is not a read of a kept path.'),
        'An unmanaged path MUST NOT collide with an OPF-managed file or view: a keep declaration naming a declared view or managed store path MUST refuse at plan time, and a later manifest change, type enablement or upgrade delta that would declare a view or managed store path at a registered [unmanaged] path MUST refuse the same way (section 9.2), so a kept path never becomes an occupied destination.',
        _D('- **Migrate.** Keep the source for post-adoption import into the separate imported series.'),
        'Its exact bytes MUST be preserved at apply, as the archived occupying copy for an occupying source and as a retirement preimage under .working/archive/adoption/<run-id>/<source-path> for a non-occupying one (completion check 3), and its retirement MUST be recorded only after its source completion check is green (section 14.1).',
        'There MUST NOT be an imported view.',
        "Import MUST read an occupying source's archived copy and a non-occupying source's frozen live file.",
        _D('- **Move.** Relocate to a named destination outside the managed store, or by default to .working/archive/moved/<source-path>, preserving substructure.'),
        'An occupied move destination MUST be a collision finding, never an overwrite.',
        'An explicit destination inside the store tree MUST lie beneath .working/archive/moved/.',
        'The default Move MUST refuse a store resolved outside the product root until a multi-root coordinator exists.',
        'The relocation MUST run only after the plan-bound green completion check, with digest-verified preservation; for an occupying source it MUST copy the archived bytes to the move destination, and the archived copy MUST stay retained.',
        '- **Retire.** The first-class clean-start choice: the exact preimage MUST be preserved under .working/archive/adoption/<run-id>/ with restore proven, and the retirement MUST be recorded only on the green completion check (section 14.1).',
        _D('No old obligation is thereby fulfilled.'),
        _D('Occupied destinations are resolved preserve-first, uniformly for a declared view path and a machine-store path alike.'),
        "Within the one approved apply transaction, apply MUST copy each occupying source byte-identically to .working/archive/adoption/<run-id>/<source-path>, MUST verify the copy's digest against the plan digest, MUST commit the copy durably, and only then MUST remove the source from the live path: verify, then remove, in that order, never reversed and never split across transactions.",
        "Before apply commits, an abort takes the ordinary per-operation preimage reversal of section 14: the reversal MUST restore each removed source to its live path from its journaled preimage, MUST verify the restored bytes against the plan digest, MUST commit the restored bytes durably, and only then MAY discard the aborted run's archive copy of that source, so an interruption leaves each source either live and byte-identical or archived with its removal journaled, never removed without a durably committed, digest-verified archive copy.",
        'The reversal MAY also discard retirement preimages written by that uncommitted apply for non-occupying sources, since each such source remains frozen, live and byte-identical in place and is never removed at apply.',
        _D("That reversal undoes the uncommitted apply's own writes; it is not the section 14.1 restore, whose fresh-plan rule governs an archived file only once apply commits."),
        'Recovery of an interrupted apply MUST resolve from the apply journal alone and MUST NOT require the live tree to resolve as a store: on restart the one transaction either completes forward from its durably committed journal or reverses fully as above, so an interruption that removed an occupying machine-store file, the manifest included, never leaves the store unresolvable or waiting on a plan it cannot form.',
        _D('A fresh-only implementation (section 16.1) performs that recovery only after its section 16.1 read-only pre-scan and within the section 16.1 recovery bound, and runs its admission check after it.'),
        _D('After the archival the destination is an ordinary managed path: apply initializes the machine file or renders the view immediately, and no writer carries an occupied-destination obligation afterward.'),
        _D("The archived copy is the disposition's preserved original: the retire preimage, the source import reads for migrate, or the bytes a move relocation copies out after the green check."),
        _D("Archival is preservation, not retirement: the green completion check of section 14.1 remains the one gate that records each disposition's retirement."),
        'An old file at no managed destination takes no archival at apply beyond its retirement preimage under completion check 3, preserved at apply for a retire-disposed and a migrate-disposed file alike: it MUST stay frozen, byte-identical in place, and MUST be removed or relocated only after the applicable green check, exactly as section 14.1 orders.',
        _D("The adoption archive is immutable in each archived file's store-relative identity and bytes."),
        'Once apply commits, a file under .working/archive/adoption/<run-id>/ MUST keep that store-relative path and those exact bytes: it MUST NOT be written, rewritten, relocated or removed within its store by any OPF operation, and the enforcement pack MUST deny writes there (section 14.1).',
        "A section 5.4 whole-store relocation MAY carry the archive, and only as part of the whole .working/ tree it relocates: every archived file keeps its store-relative path and its exact bytes, opf migrate --store MUST re-verify every carried archived copy's digest against the plan at the destination before the old tree is removed, and the enforcement pack's write denial with the completion check 5 probe (section 14.1) binds .working/archive/adoption/<run-id>/ within the resolved store, at the destination after that relocation as before it.",
        "The completion check MUST re-verify every archived copy's digest against the plan (section 14.1, check 3), import MUST re-verify an archived source's digest against the plan before reading it, and a missing, unreadable or digest-mismatched archived copy MUST fail closed at required, naming the path.",
        'Once apply commits, restoring an archived file to the live tree MUST be a fresh plan with its own single approval (section 14.1); no other operation restores it, and the pre-commit abort reversal above is not restoration.',
        'A frozen source whose live bytes no longer match its plan digest is a drifted source: it MUST NOT be archived, retired, moved or removed, doctor MUST fail it at required (section 11), and the remedy MUST be a fresh plan with its own single approval (section 14.1).',
        "opf migrate --store is whole-store relocation (section 5.4), never disposition execution: it MUST carry every frozen source, the adoption archive and every evidence bundle byte-identically into the destination at unchanged store-relative paths, MUST NOT execute, advance or end any disposition, and MUST renew the approval's bound paths to their relocated equivalents in the same recorded change, the bound digests unchanged; any byte difference remains bound-item drift refusing into a fresh plan (section 14.1).",
        'Detection MUST surface unresolved files in the plan and MUST NOT silently absorb, delete or overwrite them.',
        'Dispositions are plan data and MUST be covered by the single approval, never by separate approvals per file.',
        'An unreadable declaration or detected input MUST fail closed.',
        'Reports MUST retain migration_incomplete until the declared work is resolved.',
        _D('The reserved children archive/, imported/, staging/, and journals/ are OPF control area.'),
        'Detection MUST NOT surface them as adopter content, an adoption option MUST NOT select them, and an [unmanaged] declaration MUST NOT equal, contain, or lie within them.',
        "Adoption evidence MUST be committed and immutable under .working/imported/adoption/<run-id>/; append-only outcome events retain the receipt's history.",
        _D('In homes 2, transaction records live under .working/journals/adoption/; homes 1 retains its legacy journal paths and completion-carried evidence checks.'),
        'After adoption, containment uses the receipt-bound import_status and the bounded treatment of section 11: a plan-enumerated frozen retire, move or migrate source whose live bytes still match its plan digest MUST be reported rather than failed until its retirement is recorded, whatever the status, and a drifted source MUST fail at required exactly as section 11 defines.',
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
        _D('A no-follow existence probe of a former .aiqt/ location, used only to refuse an operation, is not a read of that material.'),
        'Experimental fields MUST ride registered x-<vendor> tables only, within the limits of section 8.7.',
        _D("The base standard's required schema vocabulary names no adopter, operator, or profile by definition; a conforming store's own data legitimately may (an operator via actor.id, a profile via a [profiles.<name>] table the adopter chose)."),
        _D("AIQT appears in the standard's title and brand as trademark and authorship attribution (the standard is authored and maintained by its lead maintainer), which is attribution rather than a requirement dependency; AIQT is also one profile, [profiles.aiqt], cited only as the reference enforcement suite and a consumer."),
        _D("A profile carries an adopter's own additional requirements without the base ever depending on them."),
    ),
    "16": (
        _D('Conformance is reported against the base and, separately, against each declared profile a tool evaluated.'),
        _D('A report speaks in conformant_for_declared_scope, nonconformant, indeterminate, or migration_incomplete, each qualified by whether it concerns the OPFiles **base** or a named **profile**.'),
        _D('Every report names its scope, its exclusions, and its cannot-evaluate results.'),
        _D('A base-conformant store may declare a profile the reporting tool did not evaluate; the report names that profile as unevaluated rather than implying whole-store coverage.'),
        _D('An unqualified claim of "OPFiles conformant" or "AIQT conformant" is never emitted, by tooling or by prose: a conformance claim is a completeness claim over a declared set, and it enumerates that set, including which profiles were and were not evaluated.'),
        'Until validation tooling ships, a conformance claim is self-asserted and MUST say so.',
    ),
    "16.1": (
        _D('An implementation is a tool or tool suite offered to create, write, or validate OPF stores.'),
        _D('A project that keeps its own store by hand, with its own checks over that store alone (section 1), is not an implementation under this section.'),
        _D('The base defines two implementation conformance classes, upgrade-capable and fresh-only.'),
        'A class changes which requirements bind an implementation only where this specification says so; a declared scope, exclusion, profile, or posture MUST NOT otherwise waive a base requirement.',
        _D('Current-format requirements, the imported series, adoption, and the section 8.6 authority firewall included, bind both classes.'),
        _D('An upgrade-capable implementation meets every section 9.2 requirement for each earlier base version and generation and grades legacy state under sections 8.1, 8.6, 11, and 14.1.'),
        _D("The reference tooling targets the upgrade-capable class: its opf upgrade, in the repository's reference code, carries a store from base 1.0.0 or 1.1.0 to base 1.2.0, within its disclosed residuals, and its upgrade into base 1.3.0 remains a target contract (section 9.2)."),
        _D('A fresh-only implementation supports exactly one base spec_version, one homes generation, and one worklog storage generation, initializes stores directly at them, and implements no section 9.2 upgrade and no legacy-state grading.'),
        'An implementation MUST declare, in the documentation of each release and in every conformance report it emits, its release identity, its class, and its supported spec_version, homes generation, and worklog storage generation.',
        'An implementation that declares no class MUST be treated as upgrade-capable, and every upgrade requirement binds it.',
        'An unreadable, malformed, or contradictory declaration MUST yield cannot-evaluate and MUST NOT authorize any store operation.',
        'A fresh-only implementation MUST run an admission check in every command that resolves a store, at every posture, before any other grading and before any write, the claim of the single-writer lease (section 5.7) included, apart from the lease reconciliation and recovery that the recovery bound below leaves to sections 5.7, 8.8, 14.1, and 14.2, which the read-only pre-scan below precedes.',
        "The check MUST run after any section 5.7 comparison against the sync target that the command performs, over the state that comparison found, and a fresh-only implementation's opf sync MUST NOT bring in, by a fast-forward, a state that the check, run first over the fetched target state, refuses or cannot evaluate, nor send, by a push of pending local commits (section 5.7), a state that the check, run first over the local state it would push, refuses or cannot evaluate.",
        'A command that writes, and does not already hold the lease from the recovery below, MUST, after admission, take the lease.toml that section 5.7 has it take in the machine store and make it observable at the sync target where the store has one (section 5.7) before any other write, the session_lease record that section 5.7 has it record for that lease where the concurrent-operation module is enabled included.',
        "Once that lease is taken and observable, and before any other write, it MUST confirm that the lease.toml carries its own claim and that every directory listing, existence-probe result, and candidate the check read is unchanged from what the check read, apart from exactly the bytes that its own claim of the lease wrote under section 5.7: the directory entries that claim created, a file that claim created, the lease.toml included, holding only what that claim wrote, and, in a file that claim rewrote, such as a <type>.index.toml or worklog ledger that gains its session_lease record, only that claim's own change, so that file MUST equal the bytes the check read with exactly that change applied, and a change to any other record or byte of it is a difference, never exempt as part of a file the claim wrote.",
        'Where anything else differs, or the lease.toml does not carry its own claim, it MUST stop as cannot-evaluate and write nothing but what section 5.7 requires to end its own claim: it MUST release its own lease (section 5.7) where the lease.toml carries its own claim, so the lease path ends as it was before the claim, and MUST leave that lease.toml untouched where it does not, since section 5.7 forbids seizing a lease from a live holder.',
        'The check MUST compare parsed versions, never strings, and MUST refuse:',
        _D('- as unsupported-older-store, a store whose manifest declares a spec_version below the supported one, or whose base table is the retired [devprocess] (section 4.5), naming the declared version or table, the supported version, the store location, and the remedy: upgrade the store with an upgrade-capable implementation, then retry;'),
        _D('- as unsupported-store-generation, a store whose homes or worklog storage generation differs from the supported one, naming each generation found and supported;'),
        _D('- as unsupported-legacy-state, a store at the supported version that holds legacy state, or a zero-match .working/ that holds it (section 4.5), naming each item and its path;'),
        _D('- with the section 9.2 version ceiling\'s finding, a store whose manifest declares a spec_version above the supported one, the reserved homes-2 declaration (section 4.2) included when the supported version is below it, naming the declared and supported versions and the tooling-upgrade remedy.'),
        _D('Legacy state is this closed list: a clean-series record, active or archived, whose actor.kind is importer (section 8.6); a declared legacy_fragment type or an LF record (section 8.1); a partial or complete import_status in a store that holds no adoption receipt (sections 11 and 14.1); an evidence inventory in the legacy format opf.ingest.evidence-inventory/v1 (section 4.2); a .working/IMPORT-REPORT.md; and a legacy .working/imports/ directory (section 4.4).'),
        _D('Extending the list is a specification change.'),
        'The check MUST search the .working/ tree of the store repository for each item, its staging and imported areas and the record archive included, within the three limits that follow, and, where discovery finds a store manifest, MUST read import_status from it; a legacy run archive outside .working/, for the reference tooling .aiqt/import-archive/, is reached only through the import_status item.',
        'The check MUST enumerate .working/ and every directory beneath the discovered machine store, .working/staging/ and .working/imported/, and a directory among these that it cannot enumerate yields cannot-evaluate.',
        'Outside those directories, a candidate lies only at a manifest.toml in an immediate subdirectory of .working/, which the check MUST reach only by a no-follow existence probe of that name (section 14.2), never by enumerating that subdirectory; a probe it cannot complete yields cannot-evaluate, and no other directory can hold a candidate or a path-defined item, so the check enumerates no other, the recovery journal below aside.',
        'First, the check MUST match the two path-defined items, a .working/IMPORT-REPORT.md and a legacy .working/imports/ directory, at those live paths only; a copy preserved under .working/archive/adoption/<run-id>/ (section 14.2) is adoption evidence, not legacy state.',
        'Second, the check MUST NOT read the contents of, or enumerate beneath, a path registered under [unmanaged] that section 14.2 permits: it matches such a path against a path-defined item by the path alone, makes no access beneath it but the existence probe above, and leaves any other listed item kept inside it unsearched, a gap section 17 discloses.',
        'That limit applies to no other [unmanaged] entry: an entry that equals, contains, or lies within a reserved control area (section 14.2) or the machine store, that equals or contains .working/ itself or a discovery candidate (section 14.2), or that names an OPF-managed file or view is a contradictory input, and the check MUST yield cannot-evaluate for it, never admission.',
        "Third, to find the items defined by file content, the check MUST parse exactly these candidates and no other file, the recovery journal below aside, a boundary section 17 discloses: each candidate manifest.toml that discovery reads (section 4.5), the discovered store manifest included; as candidate records, the record files of the discovered machine store, active and archived, meaning the worklog ledger worklog.toml and the imported worklog worklog.imported.toml (sections 6.2 and 8.3), each <type>.index.toml and <type>.imported.index.toml, each per-record file under a <type>/ directory (section 9), and each index, worklog ledger, and record file under its record archive archive/<YYYY>/ (section 12); as candidate inventories, each inventory.toml and inventory-<phase>.toml at the root of a run folder .working/staging/<kind>/<run-id>/ or .working/imported/<kind>/<run-id>/; and, in each run folder .working/imported/adoption/<run-id>/ (section 14.2), as candidate adoption receipts, each file at an adoption receipt path of the implementation's own section 14 adoption writer.",
        'Where discovery finds no store manifest, as over a zero-match .working/ (section 4.5), the import_status item and the declared legacy_fragment type item do not apply and no file is a candidate record or a candidate adoption receipt; the check still parses each candidate inventory present and matches both path-defined items, and an unreadable, malformed, or contradictory candidate that is present, a candidate manifest.toml that discovery reads (section 4.5) included, MUST yield cannot-evaluate, never admission.',
        'The check MUST classify each candidate it reads by the rules that follow, whether or not discovery finds a store manifest, and MUST yield cannot-evaluate, never admission, for each candidate those rules class as unreadable, malformed, or contradictory.',
        _D('A candidate is unreadable where the check cannot read its bytes.'),
        _D("A candidate adoption receipt is malformed where its bytes do not parse in the receipt format of the implementation's own section 14 adoption writer, since this specification fixes no adoption receipt path or format; every other candidate is malformed where its bytes do not parse as TOML."),
        _D('A candidate manifest.toml whose [opf] table declares standard = "opf" is a discovery match (section 4.5), and that match is contradictory where the file also carries a [devprocess] table, where its spec_version is absent or does not parse as a version, where a present [opf].homes or [opf].worklog value is not a TOML integer, or where a present import_status is not none, partial, or complete.'),
        _D('A candidate manifest.toml that carries an [opf] table whose standard is absent or any other value is contradictory, a mistyped or altered discovery token (section 17) included; one that carries a [devprocess] table and no [opf] table is a recognized legacy candidate (section 4.5); and one that carries neither table is foreign content, neither a match nor a contradiction, that a section 14 first adoption dispositions.'),
        _D("Any other candidate is contradictory where a value that decides a listed item, a record's actor.kind or type or an inventory's format, is absent where its envelope or format requires it, or is present and not a string."),
        'Section 14.2 places adoption evidence under .working/imported/adoption/<run-id>/, and the check MUST decide from that area alone whether a store holds an adoption receipt.',
        _D('A candidate adoption receipt that is neither unreadable nor malformed establishes that the store holds one, and a store whose .working/imported/adoption/ is absent or holds no file holds none.'),
        "Where that area holds a file but no candidate adoption receipt, as where another implementation's writer placed its receipt at another path, the check cannot establish whether the store holds an adoption receipt, and for a partial or complete import_status it MUST yield cannot-evaluate, never unsupported-legacy-state and never admission.",
        _D("A file there at no adoption receipt path of the implementation's own writer is not a candidate adoption receipt and, as a possible receipt, bears only on that decision; the third limit above alone decides whether it is a candidate inventory, so an inventory.toml or inventory-<phase>.toml at the root of a run folder there is one, parsed and classified as any other."),
        _D('An adoption receipt kept outside .working/imported/adoption/, which section 14.2 does not permit, decides nothing, a gap section 17 discloses.'),
        "A fresh-only implementation's own adoption writer MUST make its adoption receipt crash-durable before it begins to write the partial or complete import_status that receipt substantiates, whether or not both writes share a transaction, and any reversal of that status MUST make the prior import_status crash-durable before it removes that receipt, so no interruption, recovered or not, leaves its own store holding that status without that receipt.",
        "A fresh-only implementation MUST follow the lease rules of section 5.7 and the recovery rules of sections 8.8, 14.1, and 14.2 unchanged and in the order those sections set, exactly as an upgrade-capable implementation does: after resolving the store, a command reconciles a leftover lease and completes or rolls back, from its journal, an interrupted journaled transaction of the implementation's own writer, a section 8.8 authoring or import transaction, a section 14.2 apply, or a section 14.1 retirement, removal, relocation, or reclamation transaction, as those sections require, and its admission check then runs over the store those steps left, before any other write.",
        'Before either step and before any other write, every store-resolving command MUST run the admission check once as a pre-scan that writes nothing, over the store as the command found it, and where the pre-scan refuses the store or yields cannot-evaluate, the command MUST report every finding it meets and stop, the lease reconciliation and the recovery unperformed.',
        'The pre-scan MUST defer to the admission check that follows recovery only a candidate that is an operand of an interrupted transaction whose journal lies within the recovery bound below and that the pre-scan classes as unreadable, malformed, or contradictory or finds absent, together with a discovery outcome that such an operand alone decides, as where apply removed an occupying manifest.toml (section 14.2), and MUST defer nothing else, so a store whose manifest declares a version or generation that the check refuses, or that shows a listed legacy-state item, the live bytes of an operand included, is refused before any write.',
        "Within that order a recovery bound applies: a fresh-only implementation MUST recover only a journal in its own writer's format for the supported version and generations whose operands all lie at paths that the transaction's section, at that version and those generations, lets that kind of transaction write, none of them a path-defined legacy-state item.",
        _D("The bound reads the journal's format and its operands' paths, never the content of a journaled prestate or planned poststate, so a section 14.2 apply journal whose preimages are an occupying source's foreign bytes, TOML or not, lies within it, and each section's own refusal of an operand changed since the interruption, such as that of section 8.8, still applies."),
        "Every store-resolving command that finds its own writer's recovery journal naming an interrupted transaction MUST read that journal in its pre-scan, the one input beyond the candidates and directories above that the check reads, and MUST yield cannot-evaluate, never admission, before any write, the lease reconciliation included, where it cannot read that journal or parse it in that format, or where an operand lies outside those paths.",
        _D("After a section 8.8 reconciliation, that reconciliation's refusal of the new operation still applies, whatever the admission check that follows it finds."),
        _D('A command that performs no such recovery defers nothing and classes every candidate by the rules above, a candidate torn by an interruption included, and so does every command for a candidate that no such journal names as an operand.'),
        "Where a store meets more than one refusal above, the section 9.2 ceiling included, the check MUST report every finding it meets, and that store's refusal fixture asserts each of them.",
        "An input the check cannot read, parse, or enumerate MUST yield cannot-evaluate, never admission; the pre-scan's deferral of a candidate is not admission, and the check that follows recovery classes that candidate afresh.",
        'Each refusal MUST be a fail-closed INVALID finding, never VALID.',
        'Where its admission check, the pre-scan included, refuses a store or yields cannot-evaluate, a fresh-only implementation MUST NOT grade the rest of that store, write, stage, or partially upgrade any file, rewrite a version declaration, fabricate provenance, or invoke another upgrader, the section 5.7 claim and release of a lease the command takes aside; the store and product trees, ignored files and the lease path included, MUST stay byte-identical to the state the command found where the pre-scan stops it, and otherwise to the state that the lease reconciliation and recovery above left, apart from that claim and release.',
        'A fresh-only implementation that provides an upgrade command MUST refuse an older store with the same finding and MUST NOT report a successful upgrade; it MAY report a no-op on a supported store only after full doctor VALID.',
        'Admission MUST NOT substitute for any other applicable check.',
        'For a store its admission check refuses, a report MUST give the base result as indeterminate, naming the class, the supported version and generations, and the finding; it MUST NOT give conformant_for_declared_scope, nor nonconformant on that refusal alone.',
        'A claim of the fresh-only class MUST cite refusal evidence: for each finding above and each legacy-state item, a fixture that every store-resolving command refuses with that finding, asserting each seeded legacy-state item by its path, with both trees byte-identical; for the section 9.2 ceiling, whose finding carries no token in this specification, the fixture asserts an INVALID finding that names the declared and supported versions and the tooling-upgrade remedy.',
        "The importer-authored clean-series item's fixtures MUST include five separate fixtures that each seed exactly one importer-authored clean record, one an entry in the active worklog.toml ledger, one an entry in an archived worklog ledger, one an active typed record in its <type>.index.toml, one an active typed record in a per-record file under a <type>/ directory (section 9), and one an archived typed record, and the import_status item's fixtures MUST include one whose .working/imported/adoption/ holds no file, refused with that item's finding, and one whose .working/imported/adoption/<run-id>/ holds a file at no adoption receipt path of the implementation's own writer and no candidate adoption receipt, which every store-resolving command MUST report as cannot-evaluate, never as unsupported-legacy-state and never as admission, with both trees byte-identical.",
        "The recovery bound's fixtures MUST include, for each kind of recovery journal the implementation's own writers keep, the section 8.8 journal and the section 14.2 apply journal included: one whose candidate is torn under such a journal within the bound, which a command that performs that recovery recovers and then admits, the section 14.2 one interrupted after apply removed an occupying manifest.toml; one whose journal names a path-defined legacy-state item as an operand and one whose journal does not parse in its writer's format, each of which every store-resolving command MUST report as cannot-evaluate, never as admission, with both trees byte-identical; and one at the supported version that holds such a journal, whose operands all parse, and a listed legacy-state item that is no operand of it, which every store-resolving command MUST refuse with that item's finding, with both trees byte-identical.",
        "The section 14.2 apply journal's fixtures MUST also include a first adoption interrupted after its apply journal was durably committed, once apply had removed a foreign source that does not parse as TOML from a planned machine-store record path, such as worklog.toml, journaling its preimage, and had written the current-format manifest, which a command that performs that recovery completes forward and then admits.",
        "The pre-scan's fixtures MUST include, each with the concurrent-operation module enabled and with it disabled, a store whose manifest declares a spec_version below the supported one and that holds a dead run's leftover lease.toml, with the module that run's held session_lease record, and an interrupted journal of the implementation's own writer whose operands all parse, which every store-resolving command MUST refuse as unsupported-older-store before any write, with both trees byte-identical, that lease.toml and that journal included.",
        "The recheck's fixtures MUST include one, with the concurrent-operation module enabled and its session_lease records kept in their <type>.index.toml, in which another process sets actor.kind to importer in an existing record of that index after admission and before the command's claim, which then rewrites that index; the command MUST stop at the recheck as cannot-evaluate and write nothing but the section 5.7 release of its own lease.",
        _D('Missing evidence makes the claim indeterminate, never a pass.'),
        "A fresh-only claim MUST NOT imply upgrade compatibility or continuity from the implementation's own earlier releases; moving to a later base version requires a new declaration and new evidence.",
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
        _D('The retired 1.0.0 token devprocess is recognized by opf upgrade, purely to carry a legacy store forward, and by a fresh-only implementation (section 16.1), purely to refuse that store by name.'),
        _D('- A fresh-only implementation (section 16.1) proves tested admission and refusal behaviour, not authenticated history: a version declaration and the absence of listed legacy state cannot prove that a store was never upgraded or hand-rewritten, and the closed legacy-state list catches only what it lists.'),
        _D('It does not list a legacy staging run under .working/staging/import/ or .working/staging/ingest/ that carries no legacy-format inventory, a legacy .archive/ entry that section 9.2 leaves in place with a standing finding, or a legacy run archive outside .working/, which only the import_status item reaches.'),
        _D("Admission parses only the section 16.1 candidates and a recovery journal of the implementation's own writer (sections 8.8, 14.1, and 14.2), and never reads beneath a path registered under [unmanaged] that section 14.2 permits, so a listed item kept outside those candidates, such as a legacy-format inventory kept under such a path or anywhere but the root of a staging or evidence run folder, goes undetected; an [unmanaged] entry that section 14.2 forbids is a cannot-evaluate input, never a reason to leave a path unsearched."),
        _D("Admission recognizes only the adoption receipts of the implementation's own section 14 adoption writer, so where another implementation's writer placed a store's adoption receipt under .working/imported/adoption/ at another path or in another format, and that area holds no adoption receipt of the implementation's own writer, the check cannot establish whether that store holds an adoption receipt, and a partial or complete import_status there is cannot-evaluate: never refused as legacy, but not admitted either, even where a current-format adoption set that status."),
        _D("An adoption receipt kept outside .working/imported/adoption/, which section 14.2 does not permit, decides nothing, so where that area holds no file, its store's partial or complete import_status is refused as unsupported-legacy-state."),
        _D('A refusal at its pre-scan leaves a store unchanged, and no refusal offers preservation, repair, or continuity; an adopter whose store holds legacy state, an upgraded store with pre-1.3.0 import history included, needs an upgrade-capable implementation for that store.'),
        _D('The lease and recovery steps of sections 5.7, 8.8, 14.1, and 14.2 run after a read-only pre-scan and before the admission check that follows them (section 16.1).'),
        _D("The pre-scan refuses, before any write, a store whose manifest declares a version or generation that the check refuses or that shows a listed legacy-state item, but it defers an unclassifiable or absent operand of an interrupted transaction within the recovery bound, so a store that only the later check refuses, as where such an operand or the recovery's own result meets a refusal, or where something that does not take the lease changed the store between the pre-scan and the recovery, may carry the effects of a completed recovery of the implementation's own interrupted transaction, within the section 16.1 recovery bound, and of the section 5.7 reconciliation of a dead run's leftover lease."),
        _D('A first adoption interrupted while a foreign source that parses and shows a listed legacy-state item still occupies a planned machine-store record path is refused at the pre-scan once the current-format manifest exists, so no command of a fresh-only implementation recovers that apply.'),
        _D("A command that writes and takes its lease after admission rechecks admission once that lease is taken and observable and before any other write; a stop there writes nothing but what section 5.7 requires to end the command's own claim: it releases a lease.toml that carries its own claim and leaves untouched one that carries another holder's claim."),
        _D("That stop restores nothing else, so a change that another process made and the recheck detected stays in the tree; where the store has a sync target, that target's history can keep the lease's claim and release, and where the concurrent-operation module is enabled the session_lease record of that claim and its release remain."),
        _D("Within the section 16.1 recovery bound, a fresh-only implementation trusts its own writer's recovery journal, as section 8.8, 14.1, or 14.2 recovery does, to complete or roll back an interrupted transaction before admission runs; a command that performs no such recovery, a read-only command included, reports a store with a torn candidate as cannot-evaluate until a recovering command recovers it, and a clone without the journal (section 4.2) stays cannot-evaluate."),
        _D('Only the transactions section 16.1 lists are recovered that way: a candidate torn by any other write, such as a section 12 rotation or a non-green outcome event appended to an adoption receipt, is classed like any other candidate even where the implementation journals that write, so one left unparseable stays cannot-evaluate for every command of a fresh-only implementation, its own writers included, until it is repaired by hand, and one left parseable is not recognized as torn.'),
        _D('A change made after the recheck by anything that does not take the lease, such as a hand edit or a branch switch, is outside admission.'),
        _D('Until validation tooling ships, a class claim is self-asserted (section 16).'),
    ),
}
# Sentence pins in sections the registry does not tile: substring-checked and keyword-linted
# like _CONTRACT pins, each proven unique and deletion-red by the self-test, but with no
# per-section tiling equality, so additions there stay green. Section 9.1 stays untiled because
# its profile rule uses a lowercase "may", which section 2 reads as descriptive.
_PINNED = {
    "9.1": (
        _D('Implementation conformance classes (section 16.1) are defined by the base, not by profiles.'),
        'A profile MAY require an upgrade-capable implementation, because that adds a requirement.',
        "A profile, a store manifest field, or a command-line request MUST NOT declare, grant, or relax an implementation's class.",
    ),
}

# Plain-language surfaces restating the section 16.1 classes: sentence pins over tag-stripped,
# whitespace-normalized text, so deleting or rewording a pinned sentence turns the gate red.
# They are not specification sentences, so the section 2 keyword lint does not read them.
_SURFACE = {
    QUICKSTART: (
        'An implementation declares a conformance class: an upgrade-capable one carries older stores forward within its disclosed limits, and a fresh-only one supports one current format and refuses by name an older store or one in which its admission check detects an item on its closed legacy-state list (OPF-SPEC section 16.1).',
        'That check searches only the files section 16.1 permits and the list catches only what it lists, so legacy state the list omits, or that sits where the check does not search, goes undetected.',
        'The reference tooling targets the upgrade-capable class, but its upgrade into base 1.3.0 is still a target it does not yet perform.',
    ),
    DISCLOSURE: (
        'A fresh-only implementation (specification section 16.1) proves tested admission and refusal behaviour, not authenticated history: it refuses by name an older store or one in which its admission check detects an item on the closed legacy-state list.',
        "It runs that check once before any write, and a store refused there is left unchanged; only then does it recover, within a bound, its own interrupted writes and reconcile a dead run's leftover lease as the base rules require, and it runs the check again after them, so a store refused only by that second run can carry their effects.",
        'That check searches only the files section 16.1 permits and the list catches only what it lists, so legacy state the list omits, or that sits where the check does not search, goes undetected; an input it cannot read is cannot-evaluate, never admitted.',
        "Where it cannot tell whether a file in the adoption evidence area is another implementation's adoption receipt, it reports cannot-evaluate rather than refusing the store as legacy, and it does not see an adoption receipt kept outside that area, so it can refuse as legacy a store whose recorded import status rests on such a receipt.",
        'It offers no upgrade, repair, or continuity, so a store holding legacy state needs an upgrade-capable implementation.',
        'The upgrade into base 1.3.0 is still a target that the reference tooling does not yet perform, and the reference-tooling residuals above name stores its current upgrade cannot carry.',
        'A class claim is self-asserted until validation tooling ships.',
    ),
}


def _surface_text(text):
    return " ".join(re.sub(r"<[^>]*>", "", text).replace("`", "").split())


def surface_findings(texts=None):
    texts = {path: path.read_text(encoding="utf-8") for path in _SURFACE} if texts is None else texts
    findings = []
    for path, fragments in _SURFACE.items():
        body = _surface_text(texts[path])
        for fragment in fragments:
            if fragment not in body:
                findings.append("surface {} missing contract: {}".format(path.name, fragment))
    return findings


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
    for section, fragments in list(_CONTRACT.items()) + list(_PINNED.items()):
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
    for kind in store.JOURNAL_KINDS:
        if "`{}`".format(kind) not in layout:
            findings.append("spec 4.2 missing kind: " + kind)
    blocks = re.findall(r"^```gitignore\n(.*?)^```$", layout, re.MULTILINE | re.DOTALL)
    if len(blocks) != 1 or blocks[0] != store.render_homes_gitignore():
        findings.append("spec 4.2 managed gitignore block differs from renderer")
    return findings


def keyword_findings(contract=None):
    # Section 2 keyword lint over every tiled section (the sections the 1.3.0 bump touches) and
    # every untiled sentence pin.
    # Section 2 reads a sentence without MUST, MUST NOT, SHOULD or MAY as descriptive, so an
    # unmarked keywordless pin is a requirement that states no requirement. Tiling ties each pin
    # to its spec sentence, so the lint reads the registry.
    contract = dict(_CONTRACT, **_PINNED) if contract is None else contract
    findings = []
    for section, fragments in contract.items():
        for fragment in fragments:
            keyword = _KEYWORD.search(fragment) is not None
            if isinstance(fragment, _Descriptive):
                if keyword:
                    findings.append("spec {} descriptive marker on a keyword sentence: {}".format(
                        section, fragment))
            elif not keyword:
                findings.append("spec {} requirement without a section 2 keyword: {}".format(
                    section, fragment))
    return findings


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
        except (ValueError, journal.JournalError, views.ViewsError):
            return True
        return False

    def refusal(thunk):
        # The refusal message, or None when the thunk completed; any other exception propagates.
        try:
            thunk()
        except (ValueError, journal.JournalError, views.ViewsError) as exc:
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
    import stat

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
            # The journal-only record kind (spec 4.2): a record staging run is NOT admitted by the
            # homes-2 walk, exactly as any unknown kind (option ii, adding record to STAGING_KINDS,
            # turns this red).
            add(".working/staging/record/run/file")
            check("record-staging-kind-invalid", lambda: any("invalid staging kind 'record'" in s
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

        # Worklog intake reopens the manifest through the shared contained reader.
        # Give that read the SAME filesystem observation as doctor's initial read;
        # a real read through this fixture's fd 0 is a manifest fault, not a homes test.
        def worklog_toml(fd, rel, *, with_raw=False):
            data, _status = doctor_toml(fd, rel, None)
            return (b"", data) if with_raw and data is not None else data

        def worklog_stat(_fd, rel):
            if rel != machine + "/worklog":
                raise AssertionError("unexpected worklog shape probe: " + rel)
            return None                         # no conflicting generation-2 directory

        del listed[:]
        with patch.object(doctor, "_list_contained", listing), patch.object(doctor, "_read_toml", doctor_toml), \
                patch.object(store, "_read_toml_contained", side_effect=worklog_toml), \
                patch.object(journal, "_lstat_contained", side_effect=worklog_stat), \
                patch.object(doctor, "_read_bytes", read_bytes):
            report = doctor._Report()
            doctor._validate_opened_store(0, None, machine, None, {}, None, "default", True, report)
        return report.result(), list(listed)

    def contained(result, path):
        return any(repr(path) in message for message in result.by_check.get("C-CONTAINMENT", []))

    # A legacy (homes 1) report keeps the legacy roster, order and count and the pinned legacy residual
    # text, with the homes-2 names graded as ordinary paths and no evidence read.
    legacy_report, legacy_listed = dispatch(full_manifest)
    # The roster is pinned independently of the live REQUIRED_CHECKS constant: sha256 over the 28 legacy check
    # ids in report order joined by a newline, UTF-8, so an added, removed or reordered check changes it.
    legacy_roster_sha256 = "21302b2c175604ab67307443e85a85fac592eff17cc4c2bf334948801adbce5f"

    def legacy_roster(rep):
        return len(rep.checks) == 28 and hashlib.sha256("\n".join(rep.checks).encode("utf-8")).hexdigest() \
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
          and len(report.checks) == 29)
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
        # plan_views now uses validated shared manifest intake. Keep this boundary
        # fixture's deliberate empty source set, but supply the current reader and
        # the validator's full result type, including profile scope. Manifest-fault
        # coverage remains in the worklog entry-point regressions.
        def view_toml(_fd, rel):
            if rel != machine + "/manifest.toml":
                raise AssertionError("unexpected view source read: " + rel)
            return copy.deepcopy(view_manifest)

        with patch.object(store, "_read_toml_contained", side_effect=view_toml), \
                patch.object(store, "validate_manifest",
                             return_value=store.ManifestValidation(store.VALID)), \
                patch.object(views, "_resolve_view", return_value=("projection", [], lambda _src: "1.0.0\n")), \
                patch.object(views, "_spec_destination", return_value=("store", ".working/journals/file")):
            return refusal(lambda: views.plan_views(-1, machine)) or ""

    with active():
        check("view-journal-destination-refused", lambda: "journal home" in view_plan(manifest2))
        # Pair the legacy allowance with the same destination's homes-2 refusal:
        # removing the destination guard must not make this allowance vacuously pass.
        check("view-legacy-journal-destination-planned", lambda: view_plan(manifest) == ""
              and "journal home" in view_plan(manifest2))

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
                patch.object(store, "resolve_store_fd",
                             return_value=(resolved, emit.emit_checked(model).encode())), \
                patch.object(store, "validate_manifest", return_value=store.ManifestValidation(store.VALID)), \
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
                patch.object(store, "resolve_store_fd", return_value=(SimpleNamespace(
                    status=store.CANNOT_EVALUATE, detail="fixture"), None)), \
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
                patch.object(store, "resolve_store_fd",
                             return_value=(resolved, emit.emit_checked(model).encode())), \
                patch.object(store, "validate_manifest", return_value=store.ManifestValidation(store.VALID)), \
                patch.object(store._journal, "_open_parent", side_effect=FileNotFoundError), \
                patch.object(adopt, "validate_plan", wraps=adopt.validate_plan) as frozen:
            # Every planned creation needs observed absence, so the root-level ones are declared targets; the
            # enforcement members are installed by the pack row the frozen plan ties them to.
            bindings = adopt.canonical_plan_bindings()
            targets = ["notes.txt", ".opf/hooks/pre-commit"]
            observed = planning.investigate(Path("/store"), sources=[], targets=targets)
            result = planning.plan(
                Path("/store"), sources=[], targets=targets, product="opf", decisions=[],
                ops=[op, adopt.enforcement_install_op(bindings["enforcement"])],
                expected_observation_digest=tomllib.loads(observed.observation.decode())["observation_digest"],
                now=utc, run_nonce="0123456789abcdef", bindings=bindings)
        return result, [c.kwargs.get("homes") for c in frozen.call_args_list]

    homes2_plan, _ = planned(2, control_op)
    legacy_plan, legacy_frozen = planned(1, control_op)
    ordinary_plan, ordinary_frozen = planned(2, dict(create, path="notes.txt"))
    # Each op loop refuses on its own (its exact findings), not only through the frozen-plan revalidation.
    refused = tuple(adopt.validate_op(control_op, homes=2).findings)
    check("plan-homes2-control-op-refused", lambda: homes2_plan.status == store.INVALID and homes2_plan.plan is None
          and bool(refused) and homes2_plan.findings == refused)
    control_retire = dict(op="retire-file", path=".working/journals/file", preimage_digest="sha256:" + "0" * 64)
    control_source = dict(path=control_retire["path"], digest=control_retire["preimage_digest"], disposition="retire",
                          occupying=False, preservation=store.retire_preimage(
                              "adopt-20260917T120000Z-0123456789abcdef", control_retire["path"]))
    with patch.object(planning, "_decisions", return_value=([control_retire], [control_source], [])):
        retired, _ = planned(2, dict(create, path="notes.txt"))
        legacy_retired, _ = planned(1, dict(create, path="notes.txt"))
    # A move beneath the Move archive is the generation-dependent disposition: spec 14.2 permits that explicit
    # destination, so the legacy generation plans it, while the homes-2 operand refusal reaches it.
    control_move = dict(op="move-file", source="notes.md", destination=".working/archive/moved/notes.md",
                        source_digest="sha256:" + "0" * 64)
    move_source = dict(path="notes.md", digest=control_move["source_digest"], disposition="move", occupying=False,
                       preservation=control_move["destination"])
    with patch.object(planning, "_decisions", return_value=([control_move], [move_source], [])):
        moved, _ = planned(2, dict(create, path="notes.txt"))
        legacy_moved, legacy_moved_frozen = planned(1, dict(create, path="notes.txt"))
        activated_plan, activated_frozen = planned(1, dict(create, path="notes.txt"), supported=2)
    check("plan-homes2-control-disposition-refused", lambda: retired.status == store.INVALID
          and retired.findings == tuple(adopt.validate_op(control_retire, homes=2).findings) != ())
    check("plan-homes2-control-move-refused", lambda: moved.status == store.INVALID
          and moved.findings == tuple(adopt.validate_op(control_move, homes=2).findings) != ())
    # Adoption refuses the control area in the legacy generation too (spec 14.2 carries no homes qualifier):
    # the legacy op check admits these rows, and the frozen plan's revalidation at the store's own
    # generation refuses them.
    check("plan-legacy-control-disposition-refused", lambda: legacy_retired.status == store.INVALID
          and legacy_retired.plan is None and "plan sources[0] path '.working/journals/file' selects the reserved "
          "store control area as adoption content (spec 14.2)" in legacy_retired.findings)
    check("plan-legacy-control-op-refused", lambda: legacy_plan.status == store.INVALID and legacy_plan.plan is None
          and legacy_frozen == [1]
          and "plan writes '.working/journals/file' into the reserved store control area (spec 14.2)"
          in legacy_plan.findings)
    check("plan-legacy-control-move-planned", lambda: legacy_moved.status == store.VALID
          and legacy_moved_frozen == [1])
    check("plan-activated-legacy-generation", lambda: activated_plan.status == store.VALID
          and activated_frozen == [1])
    check("plan-homes2-frozen-plan-bound", lambda: ordinary_plan.status == store.VALID and ordinary_frozen == [2])
    check("plan-validate-homes2-refused", lambda: adopt.validate_plan(
        tomllib.loads(legacy_moved.plan.decode()), homes=2).status == store.INVALID)
    check("plan-validate-legacy-unchanged", lambda: adopt.validate_plan(
        tomllib.loads(legacy_moved.plan.decode())).status == store.VALID)

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
    # PR B: the projection's state derives from the SAME captured frame sequence _existing_frames
    # validated (state_of_frames), never a separate classify_state re-read.
    terminal = frames + [(journal.F_COMPLETE, dict(txn=run))]
    with patch.object(home_journal, "_existing_frames", return_value=terminal) as existing, \
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
        check("internal-projection-shared-payload", lambda: payload == home_journal.projection_payload(
            "import", run, "complete", "original-operation"))
        existing.return_value = frames
        check("internal-projection-open-refused", lambda: refuses(
            lambda: home_journal._project(100, 103, Path("/unused") / run, "import", run)))
        existing.return_value = terminal
        prior.return_value = object()
        with patch.object(journal, "_read_contained", return_value=(b"corrupt", None)):
            check("internal-projection-conflict-refused", lambda: refuses(
                lambda: home_journal._project(100, 103, Path("/unused") / run, "import", run)))
        with patch.object(journal, "_read_contained", return_value=(payload, None)):
            check("internal-projection-idempotent", lambda: home_journal._project(
                100, 103, Path("/unused") / run, "import", run) is None and publish.call_count == 1)

    # PR B (maintainer ruling): shared native-journal validation is kind-generic; adoption identities
    # validate through the same pure validators the adoption executor will bind to.
    adoption_header = dict(kind="adoption", run_id=adopt_run, operation_id="adopt-op")
    adoption_frames = [(journal.F_INTENT, dict(txn=adopt_run, header=adoption_header, ops=[])),
                       (journal.F_COMPLETE, dict(txn=adopt_run))]
    check("internal-journal-kind-generic-adoption", lambda:
          home_journal.check_run_frames(adoption_frames, "adoption", adopt_run) is not None
          and home_journal.state_of_frames(adoption_frames) == "complete"
          and tomllib.loads(home_journal.projection_payload(
              "adoption", adopt_run, "complete", "adopt-op").decode()) == {
              "format": "opf.journal.transaction/v1", "kind": "adoption", "run_id": adopt_run,
              "state": "complete", "operation_id": "adopt-op",
              "journal_rel": ".working/journals/adoption/journal"})
    check("internal-journal-adoption-identity-refused", lambda: refuses(
        lambda: home_journal.check_run_frames(adoption_frames, "import", adopt_run)))
    check("internal-journal-adoption-rolled-back", lambda: home_journal.state_of_frames(
        [(journal.F_INTENT, dict(txn=adopt_run, header=adoption_header, ops=[])),
         (journal.F_RIP, dict(txn=adopt_run)), (journal.F_RC, dict(txn=adopt_run))]) == "rolled-back")
    check("internal-attempt-adoption-spelling", lambda:
          home_journal.attempt_txn("adoption", adopt_run, 3) == adopt_run + ".a0003")
    adoption_attempt = [(journal.F_INTENT, dict(txn=adopt_run + ".a0001", header=dict(
        kind="adoption", run_id=adopt_run, attempt=1, operation_id="adopt-op"), ops=[])),
        (journal.F_COMPLETE, dict(txn=adopt_run + ".a0001"))]
    check("internal-attempt-adoption-identity", lambda:
          home_journal.check_attempt_frames(adoption_attempt, "adoption", adopt_run, 1) is not None)
    check("internal-attempt-adoption-foreign-refused", lambda: refuses(
        lambda: home_journal.check_attempt_frames(adoption_attempt, "import", adopt_run, 1)))
    check("internal-projection-adoption-nonterminal-refused", lambda: refuses(
        lambda: home_journal.projection_payload("adoption", adopt_run, "open", "adopt-op")))

    # A matching header cannot legitimize an invalid constructor identity. Require the
    # public JournalError contract, including for empty frame sequences and projections.
    def journal_refuses(thunk):
        try:
            thunk()
        except journal.JournalError:
            return True
        return False

    for label, bad_kind, bad_run in (("traversal", "adoption", "../escape"),
                                      ("kind", "bogus", adopt_run)):
        bad_header = dict(kind=bad_kind, run_id=bad_run, operation_id="op")
        bad_frames = [(journal.F_INTENT, dict(txn=bad_run, header=bad_header))]
        bad_attempt = [(journal.F_INTENT, dict(txn=bad_run + ".a0001",
                                             header=dict(bad_header, attempt=1)))]
        for validator, call in (
                ("run", lambda: home_journal.check_run_frames(bad_frames, bad_kind, bad_run)),
                ("empty-run", lambda: home_journal.check_run_frames([], bad_kind, bad_run)),
                ("attempt", lambda: home_journal.check_attempt_frames(
                    bad_attempt, bad_kind, bad_run, 1)),
                ("empty-attempt", lambda: home_journal.check_attempt_frames([], bad_kind, bad_run, 1)),
                ("attempt-name", lambda: home_journal.attempt_txn(bad_kind, bad_run, 1)),
                ("record", lambda: home_journal.projection_record(bad_kind, bad_run, "complete", "op")),
                ("payload", lambda: home_journal.projection_payload(bad_kind, bad_run, "complete", "op"))):
            check("internal-identity-{}-{}".format(label, validator), lambda: journal_refuses(call))

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


def _gitignore_reconciliation_self_test(check):
    """PR I vectors: the pure .working/.gitignore planner and the reviewed-rewrite path
    (_opf_store.plan_homes_gitignore / preview_homes_gitignore_rewrite), then the read-only
    inspector (_opf_write_guard.inspect_homes_gitignore / verify_homes_gitignore_effective) over
    real git fixture repositories built under an isolated HOME, XDG_CONFIG_HOME and disabled
    system config, so an adopter's real global ignore rules cannot leak into the vectors. When git
    is absent from PATH the fixture vectors FAIL as one named cannot-evaluate check, never a
    silent skip. The stores under test are throwaway fixtures; the inspector never mutates an
    index, and gi-tracked-staging-readonly proves it on the index content digest and the tree."""
    import hashlib
    import os
    import subprocess
    from unittest.mock import patch
    import _opf_observe as observe
    import _opf_write_guard as guard

    blk = store.render_homes_gitignore()
    block = blk.encode("utf-8")

    def drift_refuses(thunk):
        try:
            thunk()
        except ValueError as exc:
            return str(exc).startswith("homes-gitignore-block-drift")
        return False

    def value_refuses(thunk):
        try:
            thunk()
        except ValueError:
            return True
        return False

    def guard_refuses(thunk, needle):
        try:
            thunk()
        except guard.WriteGuardError as exc:
            return needle in str(exc)
        return False

    # I1..I6: the pure planner. Adopter bytes are an exact prefix of every planned result.
    check("gi-absent", lambda: store.plan_homes_gitignore(None) == block)
    adopter = b"# adopter\n/local/\n"
    check("gi-append-preserve", lambda: store.plan_homes_gitignore(adopter) == adopter + block)
    check("gi-append-no-eol", lambda: store.plan_homes_gitignore(b"/local/") == b"/local/\n" + block
          and store.homes_gitignore_matches((b"/local/\n" + block).decode("utf-8")))
    check("gi-noop", lambda: store.plan_homes_gitignore(b"# adopter\n" + block + b"/local/\n") is None)
    for index, bad in enumerate((blk + blk, blk.replace("/staging/", "/imported/"),
                                 blk.replace("/journals/\n", ""), blk.replace("\n", "\r\n"),
                                 blk.rstrip("\n"),
                                 blk.replace("# <<< opf-managed <<<", "# >>> opf-managed >>>"))):
        check("gi-drift-{}".format(index),
              lambda b=bad: drift_refuses(lambda: store.plan_homes_gitignore(b.encode("utf-8"))))
    check("gi-non-utf8", lambda: store.plan_homes_gitignore(b"\xff\n") == b"\xff\n" + block)

    # The reviewed-rewrite path: a drifted well-formed block previews the exact replacement, which
    # is applied only when the approval byte-matches a fresh preview; never silently, and never for
    # an ambiguous marker structure.
    drifted = b"# adopter\n" + blk.replace("/staging/", "/imported/").encode("utf-8") + b"/local/\n"
    check("gi-rewrite-preview", lambda: store.preview_homes_gitignore_rewrite(drifted) ==
          b"# adopter\n" + block + b"/local/\n")
    check("gi-rewrite-approved", lambda: store.plan_homes_gitignore(
        drifted, approved_rewrite=store.preview_homes_gitignore_rewrite(drifted),
        reviewed_existing=drifted) == b"# adopter\n" + block + b"/local/\n")
    check("gi-rewrite-never-silent",
          lambda: drift_refuses(lambda: store.plan_homes_gitignore(drifted)))
    check("gi-rewrite-stale", lambda: drift_refuses(lambda: store.plan_homes_gitignore(
        b"# changed\n" + blk.replace("/journals/", "/other/").encode("utf-8"),
        approved_rewrite=store.preview_homes_gitignore_rewrite(drifted), reviewed_existing=drifted)))
    # The approval binds to the reviewed prestate: an edit INSIDE the drifted block after review
    # yields the same preview, so only the reviewed bytes refuse it; an approval without them refuses.
    inblock = drifted.replace(b"/journals/\n", b"/journals/\n!/journals/unreviewed\n")
    check("gi-rewrite-inblock-edit", lambda: store.preview_homes_gitignore_rewrite(inblock) ==
          store.preview_homes_gitignore_rewrite(drifted) and drift_refuses(
              lambda: store.plan_homes_gitignore(
                  inblock, approved_rewrite=store.preview_homes_gitignore_rewrite(drifted),
                  reviewed_existing=drifted)))
    check("gi-rewrite-unbound", lambda: value_refuses(lambda: store.plan_homes_gitignore(
        drifted, approved_rewrite=store.preview_homes_gitignore_rewrite(drifted))))
    check("gi-rewrite-ambiguous", lambda: value_refuses(
        lambda: store.preview_homes_gitignore_rewrite((blk + blk).encode("utf-8"))))
    check("gi-rewrite-exact-noop", lambda: value_refuses(
        lambda: store.preview_homes_gitignore_rewrite(block)))
    # A supplied approval binds to its reviewed prestate before ANY branch: a file now absent,
    # marker-free or already exact is not the reviewed one, so the stale approval refuses rather than
    # being dropped for a create, append or no-op plan.
    for label, current in (("absent", None), ("markerless", b"# concurrent\n"), ("exact", block)):
        check("gi-rewrite-stale-" + label, lambda c=current: drift_refuses(
            lambda: store.plan_homes_gitignore(c, approved_rewrite=store.preview_homes_gitignore_rewrite(
                drifted), reviewed_existing=drifted)))

    # An absent git is a named cannot-evaluate FAIL at the inspector...
    with patch.object(observe, "_git_path", lambda: None):
        check("gi-git-missing", lambda: guard_refuses(
            lambda: guard.inspect_homes_gitignore(Path("."), "init"), "git is missing"))
    # ...and for every fixture vector below, never a silent skip.
    git = observe._git_path()
    if git is None:
        check("gi-fixtures-cannot-evaluate", lambda: False)   # git is missing from PATH
        return

    with tempfile.TemporaryDirectory(prefix="opf-homes-gi-") as tmp:
        base = Path(tmp)
        home = base / "home"
        (home / "cfg").mkdir(parents=True)
        (home / ".gitconfig").write_text("", encoding="utf-8")
        env_iso = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / "cfg"),
                   "GIT_CONFIG_NOSYSTEM": "1"}

        def fixture_env():
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(env_iso)
            env.update({"GIT_AUTHOR_NAME": "opf-selftest", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "opf-selftest",
                        "GIT_COMMITTER_EMAIL": "t@example.invalid"})
            return env

        def run_git(cwd, *args):
            # The three maintenance pins keep automatic gc/maintenance from writing into .git
            # behind a later read-only assertion.
            proc = subprocess.run([git, "-C", str(cwd), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                                   "-c", "maintenance.auto=false"] + list(args), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, env=fixture_env(), timeout=60)
            if proc.returncode != 0:
                raise RuntimeError("fixture git {} failed: {}".format(args, proc.stdout))
            return proc.stdout

        def fixture(name, subdir=""):
            repo = base / name
            repo.mkdir()
            run_git(base, "init", "-q", name)
            root = repo / subdir if subdir else repo
            (root / ".working").mkdir(parents=True, exist_ok=True)
            return repo, root

        def tracked_fixture(name, rel, subdir=""):
            repo, root = fixture(name, subdir)
            payload = root / Path(rel)
            payload.parent.mkdir(parents=True, exist_ok=True)
            payload.write_text("x\n", encoding="utf-8")
            run_git(repo, "--literal-pathspecs", "add", "--",
                    payload.relative_to(repo).as_posix())
            run_git(repo, "commit", "-qm", "seed")
            return repo, root

        def index_digest(repo):
            return hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()

        def tree_snapshot(repo):
            rows = []
            for dirpath, dirnames, filenames in os.walk(repo):
                for entry in dirnames + filenames:
                    rows.append(os.path.relpath(os.path.join(dirpath, entry), str(repo)))
            return sorted(rows)

        with patch.dict(os.environ, env_iso):
            for name in guard._homes_config_overrides():
                del os.environ[name]   # an ambient override refuses every discovery probe (restored on exit)
            # A clean fixture plans the block with no holds, through ONLY the allowlisted read-only
            # verbs (I15): rev-parse, ls-files, check-ignore, config, cat-file; never status.
            repo_c, root_c = fixture("clean")
            seen = []
            real_run, real_disc = observe._run_git, observe._run_git_config_discovery

            def spy_run(g, r, args, **kw):
                seen.append(tuple(args))
                return real_run(g, r, args, **kw)

            def spy_disc(g, r, args, **kw):
                seen.append(tuple(args))
                return real_disc(g, r, args, **kw)

            with patch.object(observe, "_run_git", spy_run), \
                 patch.object(observe, "_run_git_config_discovery", spy_disc):
                planned_c, holds_c = guard.inspect_homes_gitignore(root_c, "init")[:2]
            check("gi-clean", lambda: planned_c == block and holds_c == [])
            allowed = {"rev-parse", "ls-files", "check-ignore", "config", "cat-file"}
            check("gi-verb-allowlist", lambda: bool(seen) and all(
                next((a for a in args if not a.startswith("-")), None) in allowed
                for args in seen))

            # I7: a symlinked control file is homes-gitignore-unreadable, never followed.
            repo7, root7 = fixture("symlink")
            os.symlink("elsewhere", root7 / ".working" / ".gitignore")
            check("gi-symlink", lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(root7, "init"), "homes-gitignore-unreadable"))

            # A second hard link to the control file is likewise unreadable (contained-reader class).
            repoh, rooth = fixture("hardlink")
            (rooth / ".working" / ".gitignore").write_bytes(block)
            os.link(rooth / ".working" / ".gitignore", rooth / "alias")
            check("gi-hardlink", lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(rooth, "init"), "homes-gitignore-unreadable"))

            # I8: a tracked staging path is the held tracked-control-path refusal, and the inspection
            # leaves the index bytes and the tree untouched (read-only proof on the content digest,
            # not the mtime: the split-index mtime touch is the disclosed residual).
            repo8, root8 = tracked_fixture("tracked-staging", ".working/staging/import/f")
            before = (index_digest(repo8), tree_snapshot(repo8))
            holds8 = guard.inspect_homes_gitignore(root8, "init")[1]
            check("gi-tracked-staging", lambda: any(
                h.startswith("tracked-control-path") and ".working/staging/import/f" in h
                for h in holds8))
            after8 = (index_digest(repo8), tree_snapshot(repo8))
            if after8 != before:
                # Name what moved, so a failure on another git version is diagnosable from the log.
                print("gi-tracked-staging-readonly: index changed={} tree added={} removed={}".format(
                    after8[0] != before[0], sorted(set(after8[1]) - set(before[1])),
                    sorted(set(before[1]) - set(after8[1]))), file=sys.stderr)
            check("gi-tracked-staging-readonly", lambda: after8 == before)

            # I9: a tracked journals path holds identically.
            repo9, root9 = tracked_fixture("tracked-journals", ".working/journals/ingest/x")
            check("gi-tracked-journals", lambda: any(
                h.startswith("tracked-control-path") and ".working/journals/ingest/x" in h
                for h in guard.inspect_homes_gitignore(root9, "init")[1]))

            # I10: index flags do not hide a tracked control path: a skip-worktree entry and,
            # separately, an intent-to-add entry both hold.
            repo10, root10 = tracked_fixture("tracked-flags", ".working/staging/import/f")
            run_git(repo10, "update-index", "--skip-worktree", "--", ".working/staging/import/f")
            check("gi-tracked-flags-skip", lambda: any(
                h.startswith("tracked-control-path")
                for h in guard.inspect_homes_gitignore(root10, "init")[1]))
            repo10b, root10b = fixture("intent-to-add")
            ita = root10b / ".working" / "journals" / "ingest"
            ita.mkdir(parents=True)
            (ita / "y").write_text("x\n", encoding="utf-8")
            run_git(repo10b, "add", "-N", "--", ".working/journals/ingest/y")
            check("gi-tracked-flags-intent", lambda: any(
                h.startswith("tracked-control-path") and ".working/journals/ingest/y" in h
                for h in guard.inspect_homes_gitignore(root10b, "init")[1]))

            # A flagged .working/.gitignore itself is cannot-evaluate: its worktree bytes may not
            # be the committed ones.
            repof, rootf = tracked_fixture("gitignore-flagged", ".working/.gitignore")
            run_git(repof, "update-index", "--skip-worktree", "--", ".working/.gitignore")
            check("gi-gitignore-flagged", lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(rootf, "init"), "skip-worktree"))

            # I12: an exact block that a later adopter negation overrides is
            # homes-gitignore-ineffective (per-path effectiveness, not a whole-probe rc).
            repo12, root12 = fixture("ineffective")
            (root12 / ".working" / ".gitignore").write_bytes(block + b"!/staging/\n")
            planned12, holds12 = guard.inspect_homes_gitignore(root12, "init")[:2]
            check("gi-ineffective", lambda: planned12 is None and any(
                h.startswith("homes-gitignore-ineffective") and "staging" in h for h in holds12))

            # I13: a nested store holds on its own prefixed control path.
            repo13, root13 = tracked_fixture("nested", ".working/staging/import/f", subdir="sub")
            check("gi-nested-prefix", lambda: any(
                h.startswith("tracked-control-path") and "sub/.working/staging/import/f" in h
                for h in guard.inspect_homes_gitignore(root13, "init")[1]))

            # I14: a store prefix beginning with a pathspec-magic sigil stays literal
            # (--literal-pathspecs on ls-files; the "./" prefix on check-ignore).
            repo14, root14 = tracked_fixture("magic", ".working/staging/import/f", subdir=":x")
            check("gi-pathspec-magic", lambda: any(
                h.startswith("tracked-control-path") and ":x/.working/staging/import/f" in h
                for h in guard.inspect_homes_gitignore(root14, "init")[1]))

            # I16: no repository at all is cannot-evaluate, never a pass.
            norepo = base / "norepo"
            (norepo / ".working").mkdir(parents=True)
            check("gi-not-repo", lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(norepo, "init"),
                "not inside a git repository"))

            # The post-write re-check: effective after an install, a named finding after an
            # adopter negation lands beneath it.
            repov, rootv = fixture("verify")
            (rootv / ".working" / ".gitignore").write_bytes(block)
            check("gi-verify-effective",
                  lambda: guard.verify_homes_gitignore_effective(rootv, "init") == [])
            (rootv / ".working" / ".gitignore").write_bytes(block + b"!/journals/\n")
            check("gi-verify-ineffective", lambda: any(
                h.startswith("homes-gitignore-ineffective") and "journals" in h
                for h in guard.verify_homes_gitignore_effective(rootv, "init")))

            # The durable probe answers for NEW evidence: tracked evidence already under imported/
            # (the steady state after the first import) cannot mask an ignoring rule...
            repod, rootd = tracked_fixture("durable-tracked", ".working/imported/ev")
            (rootd / ".gitignore").write_text("/.working/imported/\n", encoding="utf-8")
            (rootd / ".working" / ".gitignore").write_bytes(block)
            check("gi-durable-tracked", lambda: any(
                h.startswith("durable-evidence-ignored") and "imported" in h
                for h in guard.inspect_homes_gitignore(rootd, "init")[1]))
            # ...and an index entry at the probe child itself, which would mask it, is cannot-evaluate.
            repom, rootm = tracked_fixture("probe-masked",
                                           ".working/imported/" + guard._HOMES_PROBE_CHILD)
            (rootm / ".gitignore").write_text("/.working/imported/\n", encoding="utf-8")
            check("gi-probe-masked", lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(rootm, "init"), "would mask"))

            # A regular file AT a control home is not matched by the block's directory-only rules:
            # held by the inspector and the post-write re-check alike.
            repot, roott = fixture("home-file")
            (roott / ".working" / ".gitignore").write_bytes(block)
            (roott / ".working" / "staging").write_text("x\n", encoding="utf-8")
            check("gi-home-file", lambda: all(any(
                h.startswith("homes-gitignore-ineffective") and "staging" in h for h in holds)
                for holds in (guard.inspect_homes_gitignore(roott, "init")[1],
                              guard.verify_homes_gitignore_effective(roott, "init"))))

            # An ignored control file is never committed, so the block would not travel: held.
            repoi, rooti = fixture("control-ignored")
            (rooti / ".gitignore").write_text("/.working/.gitignore\n", encoding="utf-8")
            (rooti / ".working" / ".gitignore").write_bytes(block)
            check("gi-control-ignored", lambda: any(
                h.startswith("homes-gitignore-ignored")
                for h in guard.inspect_homes_gitignore(rooti, "init")[1]))

            # The post-write re-check applies the inspector's unreadable-input refusal.
            repou, rootu = fixture("verify-unreadable")
            (rootu / ".gitignore").write_text("/.working/staging/\n/.working/journals/\n",
                                              encoding="utf-8")
            os.symlink("elsewhere", rootu / ".working" / ".gitignore")
            check("gi-verify-unreadable", lambda: guard_refuses(
                lambda: guard.verify_homes_gitignore_effective(rootu, "init"),
                "homes-gitignore-unreadable"))

            # A symlinked .working/ is the named unreadable refusal, never a raw JournalError.
            repow, rootw = fixture("working-symlink")
            (rootw / ".working").rmdir()
            (rootw / "real").mkdir()
            os.symlink("real", rootw / ".working")
            check("gi-working-symlink", lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(rootw, "init"), "homes-gitignore-unreadable"))

            # The shared indexed-ignore availability probe runs through the verb allowlist too, in
            # the inspector and the post-write re-check alike: narrowing the allowlist refuses its
            # config call (no other homes probe runs config).
            with patch.object(guard, "_HOMES_INSPECT_VERBS",
                              guard._HOMES_INSPECT_VERBS - frozenset(("config",))):
                check("gi-verb-allowlist-shared", lambda: all(guard_refuses(
                    lambda f=f: f(root_c, "init"), "'config'") for f in (
                        guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))

            # A control-path listing git answers with stderr (a sparse index expanding, for example)
            # refuses with git's own words, not a bare rc.
            def noisy_run(g, r, args, **kw):
                if "ls-files" in args:
                    return observe._GitOutcome(True, 0, b"", "hint: synthetic ls-files stderr\n")
                return real_run(g, r, args, **kw)

            with patch.object(observe, "_run_git", noisy_run):
                check("gi-index-stderr", lambda: guard_refuses(
                    lambda: guard.inspect_homes_gitignore(root_c, "init"), "synthetic ls-files stderr"))

            # The inspector forwards the reviewed prestate: the approval applies on the reviewed
            # bytes and holds on any other bytes, even ones with the same preview.
            repor, rootr = fixture("rewrite-bound")
            drift_r = b"# adopter\n" + blk.replace("/staging/\n", "/staging/\n/extra/\n").encode("utf-8")
            other_r = drift_r.replace(b"/extra/", b"/other/")
            (rootr / ".working" / ".gitignore").write_bytes(drift_r)
            preview_r = store.preview_homes_gitignore_rewrite(drift_r)
            check("gi-rewrite-inspect-bound", lambda: guard.inspect_homes_gitignore(
                rootr, "init", approved_rewrite=preview_r, reviewed_existing=drift_r)
                == (preview_r, [], drift_r)
                and any(h.startswith("homes-gitignore-block-drift")
                        for h in guard.inspect_homes_gitignore(
                            rootr, "init", approved_rewrite=preview_r, reviewed_existing=other_r)[1]))
            # ...and a stale approval holds when the file has since lost its markers, never an append.
            (rootr / ".working" / ".gitignore").write_bytes(b"# concurrent replacement\n")
            planned_s, holds_s = guard.inspect_homes_gitignore(
                rootr, "init", approved_rewrite=preview_r, reviewed_existing=drift_r)[:2]
            check("gi-rewrite-inspect-stale", lambda: planned_s is None and any(
                h.startswith("homes-gitignore-block-drift") for h in holds_s))

            # The inspection returns the exact bytes it planned from, so the caller's journal can bind
            # its prestate to them: a file with and without a final newline plan the same bytes.
            repoe, roote = fixture("prestate")
            (roote / ".working" / ".gitignore").write_bytes(b"/local/")
            check("gi-prestate", lambda: guard.inspect_homes_gitignore(roote, "init") == (
                b"/local/\n" + block, [], b"/local/"))

            # The allowlist reads the verb after the argument-less global flags only: a leading option
            # that takes an argument cannot pass its argument off as the verb, and config passes only in
            # a read form; each refuses before launch.
            launched = []
            with patch.object(observe, "_run_git",
                              lambda *a, **k: launched.append(a) or observe._GitOutcome(True, 0, b"", "")):
                check("gi-verb-option-arg", lambda: all(guard_refuses(
                    lambda a=a: guard._homes_run_git(git, root_c, a), "non-allowlisted") for a in (
                        ["-C", "ls-files", "update-index", "--refresh"], ["-c", "x=y", "ls-files"],
                        ["config", "core.bare", "true"])) and not launched)

            # A config read action counts only where git still parses it as an option, before `--` and
            # the first positional, beside argument-less flags: Codex's `--file F -- k --get`, a
            # positional first (`k --get-all`) and an option taking the action as its argument
            # (`--file --get`) are writes to git 2.53.0, each refused before launch; the partial-clone
            # probe's own reads still pass.
            config_file = str(base / "guard-config")
            config_launched = []
            with patch.object(observe, "_run_git_config_discovery", lambda *a, **k: config_launched.append(a)
                              or observe._GitOutcome(True, 0, b"", "")):
                check("gi-config-guard-dashdash", lambda: all(guard_refuses(
                    lambda a=a: guard._homes_run_git_discovery(git, root_c, a), "non-allowlisted") for a in (
                        ["config", "--file", config_file, "--", "qa.worker", "--get"],
                        ["config", "--", "qa.worker", "--get"],
                        ["config", "qa.worker", "--get"],
                        ["config", "-z", "--file", config_file, "qa.worker", "--get-all"],
                        ["config", "-z", "qa.worker", "--get-all"],
                        ["config", "--file", "--get", "--", "qa.worker", "v"])) and not config_launched)
            check("gi-config-guard-reads", lambda: all(guard._homes_allowlisted_verb(a) == "config" for a in (
                ["config", "--get-regexp", r"^remote\..*\.partialclonefilter$"],
                ["config", "--get", "extensions.partialClone"],
                ["config", "-z", "--name-only", "--get-regexp", r"^remote\..*\.promisor$"],
                ["config", "--type=bool", "--get-all", "remote.origin.promisor"])))

            # An ignore source git cannot read (a symlinked nested .gitignore, which git refuses to
            # follow whatever the uid) is only a warning, with check-ignore rc 1, the not-ignored
            # answer: any diagnostic is cannot-evaluate in the inspector and the re-check alike...
            repos, roots = fixture("source-diagnostic")
            (roots / ".working" / ".gitignore").write_bytes(block)
            (roots / ".working" / "imported").mkdir()
            (roots / "rules").write_text("*\n", encoding="utf-8")
            os.symlink(os.path.join("..", "..", "rules"), roots / ".working" / "imported" / ".gitignore")
            check("gi-ignore-source-diagnostic", lambda: all(guard_refuses(
                lambda f=f: f(roots, "init"), "unable to access") for f in (
                    guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))

            # ...including a diagnostic on the shared partial-clone probe's config reads.
            def noisy_disc(g, r, args, **kw):
                out = real_disc(g, r, args, **kw)
                if "config" in args:
                    return observe._GitOutcome(out.completed, out.rc, out.out, "warning: synthetic config\n")
                return out

            with patch.object(observe, "_run_git_config_discovery", noisy_disc):
                check("gi-discovery-stderr", lambda: guard_refuses(
                    lambda: guard.inspect_homes_gitignore(root_c, "init"), "synthetic config"))

            # ...and a partial-clone config probe that fails WITHOUT a diagnostic (rc 2 or 255, a
            # timeout, or an enumerated promisor key whose re-query answers rc 1) is cannot-evaluate in
            # the inspector and the re-check alike, never the partial fallback whose availability check
            # can still end clean (Codex's reproductions over the installed block); the shared default
            # keeps that fallback for opf.py's init preflight.
            repoz, rootz = fixture("config-probe-failure")
            (rootz / ".working" / ".gitignore").write_bytes(block)

            def requery_fails(args):
                if "--name-only" in args:
                    return observe._GitOutcome(True, 0, b"remote.origin.promisor\x00", "")
                return observe._GitOutcome(True, 1, b"", "")

            for label, answer in (
                    ("rc2", lambda a: observe._GitOutcome(True, 2, b"", "")),
                    ("rc255", lambda a: observe._GitOutcome(True, 255, b"", "")),
                    ("timeout", lambda a: observe._GitOutcome(False, None, b"", "synthetic timeout")),
                    ("requery", requery_fails)):
                def failing_disc(g, r, args, answer=answer, **kw):
                    return answer(args) if "config" in args else real_disc(g, r, args, **kw)

                with patch.object(observe, "_run_git_config_discovery", failing_disc):
                    check("gi-config-probe-failure-" + label, lambda: all(guard_refuses(
                        lambda f=f: f(rootz, "init"), "partial-clone config probe failed") for f in (
                            guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective))
                        and observe._is_partial_clone(git, rootz, run=failing_disc) is True)

            # ...and so is an enumerated promisor key whose non-UTF-8 name cannot be re-queried at all:
            # Codex's round-4 reproduction, `[remote "up\xffstream"] promisor = garbage` over the
            # installed block, a value git itself rejects (rc 128) through the byte-exact key; the
            # default keeps the partial fallback.
            repon, rootn = fixture("config-probe-nonutf8")
            (rootn / ".working" / ".gitignore").write_bytes(block)
            with open(repon / ".git" / "config", "ab") as config_fh:
                config_fh.write(b'\n[remote "up\xffstream"]\n\tpromisor = garbage\n')
            check("gi-config-probe-failure-nonutf8", lambda: all(guard_refuses(
                lambda f=f: f(rootn, "init"), "partial-clone config probe failed") for f in (
                    guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective))
                and observe._is_partial_clone(git, rootn) is True)

            # A runtime configuration override the config-discovery probe would drop is a named
            # cannot-evaluate, never replayed: with core.ignoreCase=true supplied through
            # GIT_CONFIG_COUNT (Codex's reproduction) or a wrapper's `git -c` (GIT_CONFIG_PARAMETERS),
            # an uppercase negation re-includes the home for the adopter's git, while the probe, which
            # drops the override, would read the block as effective (as it does, correctly, without
            # one). Every dropped name is refused; the carried GIT_CONFIG_NOSYSTEM toggle is not.
            ignore_case = (("GIT_CONFIG_COUNT", dict(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.ignoreCase",
                                                     GIT_CONFIG_VALUE_0="true")),
                           ("GIT_CONFIG_PARAMETERS", dict(GIT_CONFIG_PARAMETERS="'core.ignorecase'='true'")))
            for rel in ("staging", "journals"):
                repoo, rooto = fixture("override-" + rel)
                (rooto / ".working" / ".gitignore").write_bytes(block + b"!/" + rel.upper().encode() + b"/\n")
                (rooto / ".working" / rel).mkdir()
                (rooto / ".working" / rel / "payload").write_text("x\n", encoding="utf-8")
                check("gi-config-override-baseline-" + rel,
                      lambda r=rooto: guard.inspect_homes_gitignore(r, "init")[:2] == (None, []))
                for name, extra in ignore_case:
                    with patch.dict(os.environ, extra):
                        check("gi-config-override-" + rel + "-" + name, lambda r=rooto, n=name: all(
                            guard_refuses(lambda f=f: f(r, "init"), n) for f in (
                                guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))
            with patch.dict(os.environ, dict(GIT_CONFIG="x", GIT_CONFIG_GLOBAL="x", GIT_CONFIG_SYSTEM="x",
                                             GIT_CONFIG_KEY_3="x")):
                check("gi-config-override-names", lambda: guard._homes_config_overrides() == [
                    "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_KEY_3", "GIT_CONFIG_SYSTEM"])

            # A partial re-include beneath a home: /<home>/* ignores the probe child, not the home, so
            # a re-included subdirectory's future content would be unignored. git's own rule report
            # shows the governing rule is not a home-level exclusion: cannot-evaluate. The same
            # home-level rule repeated after the block still covers the home.
            for rel in ("staging", "journals"):
                repop, rootp = fixture("partial-" + rel)
                (rootp / ".working" / ".gitignore").write_bytes(
                    block + "!/{0}/\n/{0}/*\n!/{0}/import/\n".format(rel).encode("utf-8"))
                (rootp / ".working" / rel / "import").mkdir(parents=True)
                check("gi-partial-reinclude-" + rel, lambda r=rootp: all(guard_refuses(
                    lambda f=f: f(r, "init"), "re-include") for f in (
                        guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))
            repoq, rootq = fixture("home-rule-repeated")
            (rootq / ".working" / ".gitignore").write_bytes(block + b"/staging/\n")
            check("gi-home-rule-repeated",
                  lambda: guard.inspect_homes_gitignore(rootq, "init")[:2] == (None, []))

            # A glob character in the store prefix: check-ignore matches its probe paths against the
            # index as glob pathspecs, so a tracked sibling the glob matches would mask the durable and
            # control-file answers; the glob-mode listing refuses it. The tracked control file itself
            # is its own (tracked) answer and passes.
            for label, rel, rule in (("durable", ".working/imported/" + guard._HOMES_PROBE_CHILD,
                                      "imported/\n"), ("control", ".working/.gitignore", ".gitignore\n")):
                repog, rootg = fixture("glob-" + label, subdir="s[t]")
                (rootg / ".working" / ".gitignore").write_bytes(block)
                sibling = repog / "st" / Path(rel)
                sibling.parent.mkdir(parents=True)
                sibling.write_text("x\n", encoding="utf-8")
                run_git(repog, "--literal-pathspecs", "add", "--", sibling.relative_to(repog).as_posix())
                run_git(repog, "commit", "-qm", "seed")
                (repog / ".git" / "info").mkdir(exist_ok=True)
                (repog / ".git" / "info" / "exclude").write_text(rule, encoding="utf-8")
                check("gi-glob-prefix-masked-" + label, lambda r=rootg: guard_refuses(
                    lambda: guard.inspect_homes_gitignore(r, "init"), "would mask"))
            repok, rootk = tracked_fixture("control-tracked", ".working/.gitignore")
            (rootk / ".working" / ".gitignore").write_bytes(block)
            check("gi-control-tracked",
                  lambda: guard.inspect_homes_gitignore(rootk, "init")[:2] == (None, []))

            # The post-write re-check confirms the block itself: root rules that happen to cover the
            # homes do not stand in for an absent, adopter-only or drifted .working/.gitignore.
            for label, content in (("absent", None), ("adopter", b"/local/\n"), ("drifted", drift_r)):
                repob, rootb = fixture("verify-missing-" + label)
                (rootb / ".gitignore").write_text("/.working/staging/\n/.working/journals/\n",
                                                  encoding="utf-8")
                if content is not None:
                    (rootb / ".working" / ".gitignore").write_bytes(content)
                check("gi-verify-block-missing-" + label, lambda r=rootb: any(
                    h.startswith("homes-gitignore-block-missing")
                    for h in guard.verify_homes_gitignore_effective(r, "init")))

        # I11: a global core.excludesFile that ignores imported/ is durable-evidence-ignored under
        # the adopter's REAL configuration (config discovery), read through a second isolated HOME.
        home11 = base / "home11"
        (home11 / "cfg").mkdir(parents=True)
        (home11 / "excludes").write_text("imported/\n", encoding="utf-8")
        (home11 / ".gitconfig").write_text(
            "[core]\n\texcludesFile = {}\n".format(home11 / "excludes"), encoding="utf-8")
        env_iso11 = dict(HOME=str(home11), XDG_CONFIG_HOME=str(home11 / "cfg"),
                         GIT_CONFIG_NOSYSTEM="1")
        with patch.dict(os.environ, env_iso11):
            for name in guard._homes_config_overrides():
                del os.environ[name]
            repo11, root11 = fixture("durable-ignored")
            check("gi-durable-ignored", lambda: any(
                h.startswith("durable-evidence-ignored") and "imported" in h
                for h in guard.inspect_homes_gitignore(root11, "init")[1]))

        # A store the working directory reaches through a symlink (Claude's round-4 reproduction): an
        # includeIf "gitdir:" rule matching only that logical path gives the adopter's git there, with cwd
        # and PWD at the link, a core.excludesFile that ignores imported/, while a probe from the physical
        # path alone reads it as not ignored. The probe runs from both paths and refuses the disagreement,
        # naming both; answers that agree (a rule matching the physical path, or no rule) stand, and a
        # working directory at the physical path has no logical path, so nothing changes there.
        homel = base / "homel"
        (homel / "cfg").mkdir(parents=True)
        (homel / "excludes").write_text("/.working/imported/\n", encoding="utf-8")
        (homel / "excludes.inc").write_text(
            "[core]\n\texcludesFile = {}\n".format(homel / "excludes"), encoding="utf-8")
        (base / "lphys").mkdir()
        os.symlink("lphys", base / "llink")
        env_isol = dict(HOME=str(homel), XDG_CONFIG_HOME=str(homel / "cfg"), GIT_CONFIG_NOSYSTEM="1")

        def from_dir(path, thunk, pwd=None):
            # Run `thunk` with the working directory and PWD at `path` as given (symlinks unresolved),
            # as an adopter's shell there does, or with PWD the exact spelling `pwd` of that directory;
            # both are restored.
            previous = os.getcwd()
            os.chdir(path)
            try:
                with patch.dict(os.environ, PWD=str(path) if pwd is None else pwd):
                    return thunk()
            finally:
                os.chdir(previous)

        def include_if(gitdir):
            rule = "[includeIf \"gitdir:{}/\"]\n\tpath = {}\n".format(gitdir, homel / "excludes.inc")
            (homel / ".gitconfig").write_text("" if gitdir is None else rule, encoding="utf-8")

        with patch.dict(os.environ, env_isol):
            for name in guard._homes_config_overrides():
                del os.environ[name]
            repol, rootl = fixture("lphys/repo")
            (rootl / ".working" / ".gitignore").write_bytes(block)
            logical = base / "llink" / "repo"
            include_if(base / "llink")
            check("gi-logical-path-disagree", lambda: all(from_dir(logical, lambda f=f: guard_refuses(
                lambda: f(rootl, "init"), "answers differently from the physical path {!r} and from the "
                "logical path {!r}".format(str(rootl), str(logical)))) for f in (
                    guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))
            check("gi-logical-path-physical-cwd", lambda: from_dir(
                rootl, lambda: guard.inspect_homes_gitignore(rootl, "init")[:2] == (None, [])))
            include_if(os.path.realpath(base / "lphys"))
            check("gi-logical-path-agree-held", lambda: from_dir(logical, lambda: any(
                h.startswith("durable-evidence-ignored")
                for h in guard.inspect_homes_gitignore(rootl, "init")[1])))
            include_if(None)
            check("gi-logical-path-agree-clean", lambda: from_dir(logical, lambda: guard._homes_logical_path(
                rootl) == str(logical) and guard.inspect_homes_gitignore(rootl, "init")[:2] == (None, [])
                and guard.verify_homes_gitignore_effective(rootl, "init") == []))

            # git names the worktree top by PWD's exact spelling (Codex's and Claude's round-5
            # reproductions): a rule written against `R/.`, `R//`, `R/.working/..` or `<link>/./repo`
            # applies to the adopter's git there, so the probe passes that PWD verbatim, never
            # normalized, and refuses the disagreement naming it; with no rule the spellings agree.
            # Below the worktree top the adopter's git names the repository by its physical path, but
            # the probe runs from `<PWD>/..`: a rule matching that spelling is the disclosed
            # over-refusal.
            spellings = (("dot", rootl, str(rootl) + "/.", str(rootl) + "/."),
                         ("dslash", rootl, str(rootl) + "//", str(rootl) + "/"),
                         ("dotdot", rootl, str(rootl) + "/.working/..", str(rootl) + "/.working/.."),
                         ("link-dot", logical, str(base) + "/llink/./repo", str(base) + "/llink/./repo"))
            for label, cwd, pwd, rule in spellings:
                include_if(rule)
                check("gi-logical-spelling-verbatim-" + label, lambda c=cwd, p=pwd: from_dir(
                    c, lambda: guard._homes_logical_path(rootl) == p, pwd=p))
                check("gi-logical-spelling-disagree-" + label, lambda c=cwd, p=pwd: all(from_dir(
                    c, lambda f=f: guard_refuses(lambda: f(rootl, "init"), "answers differently from the "
                                                 "physical path {!r} and from the logical path {!r}".format(
                                                     str(rootl), p)), pwd=p) for f in (
                        guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))
            include_if(None)
            check("gi-logical-spelling-agree-clean", lambda: all(from_dir(c, lambda: guard.inspect_homes_gitignore(
                rootl, "init")[:2] == (None, []), pwd=p) for _, c, p, _ in spellings))
            below = logical / ".working"
            include_if(str(below) + "/..")
            check("gi-logical-path-subdir-overrefusal", lambda: from_dir(below, lambda: guard_refuses(
                lambda: guard.inspect_homes_gitignore(rootl, "init"), "and from the logical path {!r}".format(
                    str(below) + "/.."))))
            include_if(None)

            # A relative ambient PWD (Claude's fix-7 reproduction): git names the worktree top by it
            # verbatim, so with PWD `.` there a `gitdir:[.]/` rule applies to the adopter's git, a
            # spelling no probe reproduces; both entry points refuse it, naming it. An unset or empty
            # PWD is not refused: that rule then does not apply to the adopter's git, and the
            # inspection stays clean.
            def clean_both():
                try:
                    return (guard.inspect_homes_gitignore(rootl, "init")[:2] == (None, [])
                            and guard.verify_homes_gitignore_effective(rootl, "init") == [])
                except guard.WriteGuardError:
                    return False

            def pwd_unset(thunk):
                with patch.dict(os.environ):
                    os.environ.pop("PWD", None)
                    return thunk()

            include_if("[.]")
            check("gi-logical-path-relative-pwd", lambda: all(from_dir(rootl, lambda f=f: guard_refuses(
                lambda: f(rootl, "init"), "the ambient PWD '.' is relative"), pwd=".") for f in (
                    guard.inspect_homes_gitignore, guard.verify_homes_gitignore_effective)))
            check("gi-logical-path-pwd-unset-clean", lambda: from_dir(rootl, lambda: pwd_unset(clean_both)))
            check("gi-logical-path-pwd-empty-clean", lambda: from_dir(rootl, clean_both, pwd=""))
            include_if(None)


def self_test():
    """Run the vectors behind main()'s cannot-evaluate backstop, so the canonical `--self-test` entry maps an
    escaping read or parse error to exit 2 exactly as `main(["--self-test"])` does."""
    try:
        return _self_test_vectors()
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        print("check_opf_homes: cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2


def _self_test_vectors():
    import _opf_adopt as adopt
    import _opf_check as doctor
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
    # The journal-only record kind (spec 4.2): journal_root, txn_record and allocation_record admit it;
    # stage_run, evidence_run and evidence_inventory keep refusing it, so record never gains a staging
    # or imported home. The paired mutations are exercised in place: dropping record from JOURNAL_KINDS
    # turns the constructors red, and widening STAGING_KINDS with record (option ii) turns the
    # staging refusals red.
    from unittest.mock import patch as _patch
    record_run = "record" + suffix
    check("journal-kinds", lambda: store.JOURNAL_KINDS == store.STAGING_KINDS + ("record",))
    check("journal-record", lambda: store.journal_root("record") == ".working/journals/record/journal")
    check("transaction-record", lambda: store.txn_record("record", record_run) ==
          ".working/journals/record/runs/{}/transaction.toml".format(record_run))
    check("allocation-record", lambda: store.allocation_record("record", record_run) ==
          ".working/journals/record/allocations/{}.toml".format(record_run))
    for bad in (None, [], "", "imp" + suffix, "adopt" + suffix, record_run.upper(), record_run + "/x",
                "record" + suffix.replace("Z", "X")):
        for function in (store.txn_record, store.allocation_record):
            check("run-refusal-{}-record-{!r}".format(function.__name__, bad),
                  lambda function=function, bad=bad: refuses(lambda: function("record", bad)))
    check("kind-record-no-staging", lambda: refuses(lambda: store.stage_run("record", record_run)))
    check("kind-record-no-evidence", lambda: refuses(lambda: store.evidence_run("record", record_run)))
    check("kind-record-no-inventory", lambda: refuses(lambda: store.evidence_inventory("record", record_run)))
    with _patch.object(store, "JOURNAL_KINDS", store.STAGING_KINDS):
        check("journal-record-flip-dropped-kind", lambda: refuses(lambda: store.journal_root("record"))
              and refuses(lambda: store.txn_record("record", record_run))
              and refuses(lambda: store.allocation_record("record", record_run)))
    with _patch.object(store, "STAGING_KINDS", store.STAGING_KINDS + ("record",)):
        check("staging-record-flip-option-ii", lambda: not refuses(lambda: store.stage_run("record", record_run))
              and not refuses(lambda: store.evidence_run("record", record_run)))
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
          adopt._IMPORT_RUN_ID_RE.pattern)
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
    _gitignore_reconciliation_self_test(check)

    # OPF-D2B PR3a (decision 5): the base spec_version bump 1.1.0 -> 1.2.0 is an intended change
    # (init.toml provenance, with a tested opf upgrade route), no longer inert.
    check("inert-version", lambda: store.SUPPORTED_SPEC_VERSION == "1.2.0")
    check("inert-homes", lambda: store.SUPPORTED_HOMES == 1
          and store.homes_generation({"opf": {"homes": 2}}) == 1)
    check("inert-init", lambda: init._manifest_model()["opf"]["spec_version"] == "1.2.0"
          and "homes" not in init._manifest_model()["opf"])
    check("inert-import", lambda: doctor.IMPORTS_REL == ".working/imports"
          and doctor._is_import_run_id("imp" + suffix) and not doctor._is_import_run_id("adopt" + suffix))
    check("inert-root-exclusions", lambda: store.STORE_ROOT_CONTROL_DIRS == (".git", ".aiqt"))
    source = Path(store.__file__).read_text(encoding="utf-8")
    check("transitional-comment", lambda: "In homes 2, .aiqt is AIQT-only" in source
          and "Until homes 2 is activated, legacy import state still" in source)
    text = SPEC.read_text(encoding="utf-8")
    check("spec-contract", lambda: not contract_findings(text))
    # The journal-only record kind is spec-pinned through the kind loop: deleting `record` from the
    # section 4.2 vocabulary sentence turns the gate red with the kind's own finding.
    check("spec-kind-record", lambda: "spec 4.2 missing kind: record" in
          contract_findings(text.replace("`preview`, `record`", "`preview`", 1)))
    # Each operative section independently discriminates: removing it must fail the drift gate.
    for section, body in _sections(text).items():
        if section in _CONTRACT or section in _PINNED:
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
    # Untiled sentence pins: each section stays out of the tiling registry, its normalized
    # control is green, each pin occurs exactly once, and deleting that occurrence in place turns
    # the gate red with the pin's own finding.
    for section, fragments in _PINNED.items():
        body = _sections(text)[section]
        normalized_body = " ".join(body.replace("`", "").split())
        pin_prefix = "spec " + section + " missing contract"
        control = text.replace(body, "\n\n" + normalized_body + "\n\n", 1)
        check("spec-pinned-untiled-" + section, lambda s=section: s not in _CONTRACT)
        check("spec-pin-control-" + section, lambda c=control, p=pin_prefix: not any(
            f.startswith(p) for f in contract_findings(c)))
        for fragment in fragments:
            mutated = text.replace(body, "\n\n" + normalized_body.replace(fragment, "", 1) + "\n\n", 1)
            finding = pin_prefix + ": " + fragment
            check("spec-pin-flip-" + section + "-" + fragment,
                  lambda f=finding, m=mutated, frag=fragment, nb=normalized_body:
                  nb.count(frag) == 1 and f in contract_findings(m))
    # Surface pins: the live files are green, each pin occurs exactly once in its normalized
    # text, and deleting that occurrence turns the gate red with the pin's own finding.
    surfaces = {path: path.read_text(encoding="utf-8") for path in _SURFACE}
    check("surface-contract", lambda: not surface_findings(surfaces))
    for path, fragments in _SURFACE.items():
        normalized = _surface_text(surfaces[path])
        for fragment in fragments:
            mutated = dict(surfaces)
            mutated[path] = normalized.replace(fragment, "", 1)
            finding = "surface {} missing contract: {}".format(path.name, fragment)
            check("surface-pin-flip-" + path.name + "-" + fragment,
                  lambda f=finding, m=mutated, frag=fragment, nb=normalized:
                  nb.count(frag) == 1 and f in surface_findings(m))
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
    )
    for fragment, red in lint_cases:
        check("keyword-lint-case-{!r}".format(str(fragment)), lambda f=fragment, r=red:
              bool(keyword_findings({"14.2": (f,)})) == r)
    # Stripping any marker, or lowercasing any pin's keywords, turns the lint red on that pin.
    for registry, section, fragments in [(r, s, f) for r in (_CONTRACT, _PINNED) for s, f in r.items()]:
        for index, fragment in enumerate(fragments):
            if isinstance(fragment, _Descriptive):
                flipped = str(fragment)
            elif _KEYWORD.search(fragment):
                flipped = _KEYWORD.sub(lambda m: m.group(0).lower(), fragment)
            else:
                continue  # an unmarked keywordless pin is already red under spec-keywords
            expected = "spec {} requirement without a section 2 keyword: {}".format(section, flipped)
            with patch.dict(registry, {section: fragments[:index] + (flipped,) + fragments[index + 1:]}):
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
        findings = (contract_findings(SPEC.read_text(encoding="utf-8")) + keyword_findings()
                    + surface_findings())
        for finding in findings:
            print("check_opf_homes: " + finding)
        return 1 if findings else 0
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        print("check_opf_homes: cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
