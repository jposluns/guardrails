# Standards id-manifests

These TOML files are the crosswalk's no-fabrication source of truth. Each one pins a single external
framework: its edition and the enumerated set of canonical control ids that a rule's `map-<key>`
frontmatter is allowed to cite. A rule's `map-<key>` frontmatter may cite a framework only after its
manifest lands here, so a mapping to an unsourced framework, or to an id that does not exist in the
pinned edition, cannot ship. Rule-body prose is checked more narrowly: a numbered designator in one of
the spellings the gate's grammar lists must resolve to a manifest or a recorded exclusion. The grammar
reads ISO joined by a slash, a dash or whitespace to any of IEC, IEEE, a deliverable type (TR, TS, PAS)
or a project-stage abbreviation (AWI, WD, CD, DIS, FDIS, DTR, DTS); NIST SP or NIST Special Publication
with a number, or SP or Special Publication with an n-n number; NIST 800-n; NIST AI; NIST IR or NISTIR;
FIPS, NIST FIPS or FIPS PUB; and RFC; each followed by its number. NIST is joined to its series, and
FIPS to PUB, by whitespace, a dash or a dot (as in NIST.SP.800-53), a body to its number by whitespace,
a dash, a dot or nothing, and a joining dash or dot may end a line. A designator run on into letters or
digits the grammar does not read, or into a dash that, after any further dashes, is followed neither by
a letter nor by same-line whitespace and then a character that is neither whitespace nor a digit (a dash
before an LF counts, directly or after same-line whitespace, whatever opens the next line), is reported
as a whole token, never resolved to a shorter identity, and only an exclusion naming the whole token as
the finding prints it waives it. A token that ends at a dash keeps nothing after the dash, so one
exclusion naming it waives each citation in its listed paths printed as that token, whatever followed
the dash. Not detected: a framework named in
prose without such a number; the omissions the gate names, among them the ISO prefixes DPAS, PWI, CDV,
PDTR, PDTS, TTA, NP and PRF and the IWA and ISO Guide series, a list that is not complete; any other
body, series, prefix or spelling outside the grammar; and a designator broken by markup or continued
onto a line opened by anything but whitespace and blockquote markers, such as a comment marker.
Read short, so such a spelling can resolve to a registered shorter identity (named cases, not a complete
list): whitespace before a dash; an entity, markup or any other character outside the dash list, such
as a soft hyphen, before or in place of a dash; a dash followed by same-line whitespace and then a
character that is neither whitespace nor a digit, such as markup, or, since only LF ends a line, a lone
CR, VT, FF, U+0085, U+2028 or U+2029 and then such a character; and a dot, colon or slash followed by
whitespace, on the same line or at a line end, other than the ISO year colon. A hyphenated year
(ISO 42001-2023) is its own identity: it does not resolve to ISO 42001. The grammar and its full
residual are in the docstring of `tools/check_cited_standards.py`.

## How it is enforced

- `tools/gen_rules.py` DERIVES its `MAP_KEYS` from this directory: the two fit-qualified keys
  `map-<filename>-tight` and `map-<filename>-broad` per manifest (so `owasp-llm.toml` enables
  `map-owasp-llm-tight` and `map-owasp-llm-broad`). Every mapping carries its fit: `-tight` for a direct,
  one-to-one correspondence, `-broad` for a looser thematic relation. A bare `map-<filename>` with no fit
  suffix, a bad suffix, or a `map-*` key with no manifest is rejected as an unknown frontmatter key.
- `tools/check_mappings.py` (a CI gate, and part of `run_all_checks.sh`) validates every mapped id in
  the corpus against its manifest: the id must match the manifest `id-pattern` and appear verbatim in
  the manifest's id set. It also enforces per-list hygiene (no duplicates, natural-sorted order) and
  mutual exclusivity (an id is not asserted both tight and broad for one rule+framework). A malformed
  manifest fails closed (exit 2). Both are stdlib-only and offline: CI never reaches the network or the
  private source catalogues.
- `tools/check_cited_standards.py` (a CI gate, and part of `run_all_checks.sh`) scans the rule corpus for
  numbered standard designators in its grammar's spellings and fails on one that no manifest's `name` or
  `edition` carries and no recorded exclusion covers. This repository's CI and `run_all_checks.sh` pass
  `--config .aiqt/cited-standards.toml` (tracked, not shipped), which adds the OPF prompt-pack Markdown
  members (`opf/prompt-pack/**/*.md`) and import examples (`opf/prompt-pack/import/examples/*.batch.toml`), and the repository-only `.github/check_cited_standards_contract.py`
  fails if that corpus stops covering the rule files or a member `opf/prompt-pack/pack.toml` lists (the
  pack manifest is validated by its owning validator, `opf/tools/check_opf_prompt_pack.py`), or if either
  runner's gate line is not exactly `--config .aiqt/cited-standards.toml` with nothing added; without
  `--config` the gate scans the rule files alone. The config and the two repository-only `.github`
  contract scripts (`check_cited_standards_contract.py` and `check_newtab_contract.py`) are tracked, not
  shipped: in a tree without them the cited-standards gate line and the three contract gate lines (one
  newtab, plus the cited-standards contract and its self-test) exit 2 (cannot evaluate) and the shipped
  `tools/run_all_checks.sh` then ends `RESULT: FAIL`. The gate prints
  its residual on every run, the self-test and a usage error included; a clean result is not a complete
  enumeration of the standards the prose names.

## Manifest schema

Top-level fields (all required):

| field | meaning |
| --- | --- |
| `map-key` | the frontmatter key, must equal `map-<filename>` |
| `name` | the framework's published name |
| `publisher` | the issuing body |
| `edition` | the pinned edition/version (never inline in a rule's id) |
| `kind` | `risk` \| `control` \| `guidance` \| `technique` \| `weakness` (how the public page words the relation) |
| `status` | `stable` \| `beta` \| `snapshot` (edition stability, shown as a badge) |
| `catalogue` | `full` \| `subset` (is the vendored id set the complete pinned edition, or a curated selection) |
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

The framework roster below is generated from the manifests by `tools/gen_reference_facts.py` and
drift-gated in CI; do not hand-edit between the markers.

<!-- ROSTER:BEGIN (generated) -->
Frameworks with a manifest and enabled key (17 in total):

- CSA AI Controls Matrix (edition 1.1.0)
- CSA Cloud Controls Matrix (edition 4.1.0)
- ISO/IEC 23894:2023 AI guidance on risk management (edition 2023)
- ISO/IEC 42001:2023 AI management system (edition 2023)
- MITRE ATLAS (edition 2026.06)
- MITRE CWE (edition 4.20)
- NIST AI Risk Management Framework (edition 1.0 (NIST AI 100-1))
- NIST SP 800-53 Security and Privacy Controls (edition Rev 5 (catalog 5.2.0))
- NIST Secure Software Development Framework (edition 1.1 (SP 800-218))
- OWASP API Security Top 10 (edition 2023)
- OWASP Application Security Verification Standard (edition 5.0.0)
- OWASP Cheat Sheet Series (edition commit f54d8ded2764010ecacd0deb87e1586da32d41dd (2026-07-04))
- OWASP MCP Top 10 (edition 2025)
- OWASP Top 10 (Web Application Security Risks) (edition 2025)
- OWASP Top 10 Proactive Controls (edition 4.0.0)
- OWASP Top 10 for Agentic Applications (edition 2026)
- OWASP Top 10 for LLM Applications (edition 2026)
<!-- ROSTER:END -->

The corpus is mapped against these frameworks, and the public /mappings crosswalk (site/mappings.html,
generated by tools/gen_mappings.py) is derived from those mappings and drift-gated. The machine-readable
sources (NIST OSCAL catalogs, the ATLAS techniques CSV, the version-pinned CWE XML archive) are extracted
deterministically; the AI RMF and ISO id sets are read from the published documents. The two ISO manifests carry the id plus the short
clause/Annex TITLE only (a navigational pointer to a licensed copy), never the clause body text.

## Deferred frameworks

Not yet vendored (their keys stay inert until a manifest lands): Google SAIF (deferred pending a licence
determination) and OWASP SCVS (no authoritative source). Each returns atomically with its manifest.
