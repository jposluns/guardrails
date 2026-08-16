# Standards id-manifests

These TOML files are the crosswalk's no-fabrication source of truth. Each one pins a single external
framework: its edition and the enumerated set of canonical control ids that a rule's `map-<key>`
frontmatter is allowed to cite. A rule may cite a framework only after its manifest lands here, so a
citation to an unsourced framework, or to an id that does not exist in the pinned edition, cannot ship.

## How it is enforced

- `tools/gen_rules.py` DERIVES its `MAP_KEYS` from this directory: one key per manifest, named
  `map-<filename>` (so `owasp-llm.toml` enables `map-owasp-llm`). A `map-*` key with no manifest is
  rejected as an unknown frontmatter key.
- `tools/check_mappings.py` (a CI gate, and part of `run_all_checks.sh`) validates every mapped id in
  the corpus against its manifest: the id must match the manifest `id-pattern` and appear verbatim in
  the manifest's id set. It also enforces per-list hygiene (no duplicates, natural-sorted order). A
  malformed manifest fails closed (exit 2). Both are stdlib-only and offline: CI never reaches the
  network or the private source catalogues.

## Manifest schema

Top-level fields (all required):

| field | meaning |
| --- | --- |
| `map-key` | the frontmatter key, must equal `map-<filename>` |
| `name` | the framework's published name |
| `publisher` | the issuing body |
| `edition` | the pinned edition/version (never inline in a rule's id) |
| `kind` | `risk` \| `control` \| `guidance` \| `technique` (how the public page words the relation) |
| `status` | `stable` \| `beta` \| `snapshot` (edition stability, shown as a badge) |
| `citation-unit` | the unit an id names (e.g. risk category, chapter, control) |
| `id-pattern` | a regex every id must fully match |
| `source-artefact` | the exact published document the ids were read from |
| `retrieved` | ISO date the ids were extracted |

Then one `[[id]]` table per id, each with a `code` (required, must match `id-pattern`) and an optional
`title`. Ids must be unique and in natural-sorted order.

Attribution (licence, source URL) is intentionally NOT carried here; it is added, per-source verified,
with the public NOTICE, because this file's job is to make an id verifiable, not to assert a licence.

## Adding or updating a manifest

1. Read the ids from the authoritative published source, in a session with access to it. Never map from
   memory or an unversioned page. Record the exact `edition` and `source-artefact`.
2. Use the bare canonical id token; keep the edition in `edition`, never in the id.
3. Run `python3 tools/check_mappings.py` and `python3 tools/gen_rules.py --check` locally.
4. On an edition bump, any rule citing a removed id fails the gate on the same change, so an orphaned
   mapping cannot pass silently: fix the rule in the same change.

Deferred frameworks (no manifest yet, so their keys are inert): NIST AI RMF / SSDF / 800-53, ISO 42001 /
23894, CSA CCM / AICM, MITRE ATLAS, Google SAIF, OWASP SCVS. Each returns atomically with its manifest.
