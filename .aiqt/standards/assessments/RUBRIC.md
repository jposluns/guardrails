# Mapping fit assessment rubric

Every mapping in a rule's frontmatter carries a fit: `-tight` or `-broad`. This folder holds the rubric
used to review those fits and one record file per framework with the assessed rows. The records sit beside
the framework manifests (see the [standards README](../README.md)) but are not manifests: the manifest
loader reads only the `*.toml` files at the top of `.aiqt/standards/`, never this folder.

This is the first pass. It records a label for each assessed row under two candidate definitions of
tight. It does not choose between them, and it changes no label in any rule. Every record says
`adopted = "pending-definition"` until the maintainer settles the definition and the open conventions
below.

## The two candidate definitions of tight

### What both definitions share

- A link that rests only on a shared theme, a precondition, or a downstream impact is broad.
- A link where the rule mitigates no mechanism of the entry is a candidate for removal.
- Neither definition limits how many ids one rule may map as tight. Either one, once adopted, replaces the
  "one-to-one" wording in the standards README and on the public mappings page (`site/mappings.html`).

### Definition A: direct mitigation

A row is tight under A when all three hold:

1. The rule acts on the same object or boundary as the entry.
2. The rule directly carries out the entry's core obligation, or interrupts the entry's core attack
   mechanism, for at least one main variant.
3. The match can be seen in the entry's own statement or in its listed mitigations.

Missing secondary elements (documentation, a review cadence, organisation-wide scope, other variants) do
not stop a row from being tight under A.

### Definition B: substantial coverage

A row is tight under B when A holds and also:

- for a control or guidance entry, following the rule meets every element of the normative statement
  (each lettered item or each verb);
- for a risk, technique or weakness entry, the rule covers the entry's main variants and the main lines of
  its mitigations that apply at the rule's layer.

Leaving out any essential element makes the row broad under B.

## Decision tests

Apply these in order for each row and write the outcome into the record.

1. Read the entry's text in the pinned edition, at the location the record names. Never assess from the
   title or from memory.
2. State the entry's core obligation or attack mechanism in your own words (`core`).
3. Name the object or boundary the rule acts on, and check that it is the entry's object or boundary
   (`boundary`).
4. List what the rule covers (`covers`) and what it leaves out (`omits`), element by element for a
   control, variant by variant for a risk or technique.
5. Under A: does the rule carry out the core, or break the core mechanism, for at least one main variant,
   visibly in the entry's statement or mitigations? If yes, `label-a = "tight"`.
6. Under B: does anything essential remain in `omits`? If nothing does, `label-b = "tight"`.
7. If the only link is a theme, a precondition or an impact, both labels are broad. If no mechanism is
   mitigated at all, record `remove` and give the reason.
8. If an open convention below decides the label, name it in `conventions`, label by the reading stated in
   the record, and say so in `rationale`.

## Open conventions

The maintainer must settle these before a definition is adopted. A record that depends on one names it.

1. **Informative annexes.** Can a row that maps to an informative annex (for example ISO/IEC 23894
   Annex B, which lists risk sources rather than requirements) be tight at all?
2. **Rules as policies and procedures.** Does a pack rule, followed by an assistant, count as one of the
   "policies and procedures" an entry asks an organisation to have?
3. **What "the AI system" means.** Is it the product being built, or the AI assistant and tools used to
   build it? If it means only the product, rows that act on the assistant's own work are broad.
4. **Layer and boundary.** Can a rule that acts at the assistant's or the application's layer be tight
   to an entry whose mechanism or main control sits at another layer (the service's inbound edge, the
   client that renders output, the infrastructure, the authorization server)?
5. **Conditional elements.** Does an element the entry makes conditional ("as necessary", "where
   appropriate") count as essential under B?

## Record schema

One file per framework, named after the framework's manifest, holding one `[[row]]` table per
(rule, framework, id). The records never quote control text. Exact wording stays in the source, reached
through `source`, `text-location` and `source-sha256`; the records use own words only.

| field | meaning |
| --- | --- |
| `rule-path` | the rule file, repository-relative |
| `rule-slug` | the rule's `slug` frontmatter value |
| `rule-blob` | the git blob id of the rule file when it was assessed |
| `framework` | the manifest file name without `.toml` |
| `id` | the mapped id, exactly as in the frontmatter |
| `current-fit` | the fit the frontmatter carries today |
| `edition` | the edition, copied from the manifest |
| `source` | the reference copy that was read, by file name |
| `source-sha256` | the SHA-256 of that copy |
| `locator` | a public locator a reader with the published document can follow |
| `text-location` | where the text sits in the reference copy (lines, or the catalogue part ids) |
| `edition-check` | `match`, `mismatch` or `unchecked`: does the copy read match the pinned edition |
| `boundary` | the object or boundary the rule acts on |
| `core` | the entry's core obligation or attack mechanism, in own words |
| `covers` | what the rule covers |
| `omits` | what the rule leaves out |
| `label-a` | `tight`, `broad` or `remove` under definition A |
| `label-b` | `tight`, `broad` or `remove` under definition B |
| `adopted` | `A`, `B` or `pending-definition` |
| `proposed` | `tight`, `broad`, `remove` or `pending-definition` |
| `review-state` | `assessed`, `evidence-missing`, `edition-mismatch` or `disputed` |
| `conventions` | the open conventions (by number above) that decide a label in this row |
| `rationale` | one or two sentences |
| `assumptions` | readings the labels depend on |
| `assessor` | who assessed the row |
| `date` | the assessment date |

The review state is kept apart from the fit and is never written into rule frontmatter.

## First pass summary

These 20 rows were chosen because they looked the most likely to be over-labelled. They are not a
representative sample. All 20 are tight in frontmatter today.

| row | rule | framework | id | label-a | label-b | conventions |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | human-oversight-and-autonomy-threshold | iso-23894 | B.4 | tight | broad | 1 |
| 2 | human-authorization | iso-23894 | B.4 | tight | broad | 1 |
| 3 | decision-classification-before-enacting | nist-airmf | MAP 3.5 | tight | broad | 2 |
| 4 | decision-classification-before-enacting | nist-airmf | GOVERN 3.2 | tight | tight | 2 |
| 5 | human-oversight-and-autonomy-threshold | nist-airmf | MAP 3.5 | tight | broad | 2 |
| 6 | human-oversight-and-autonomy-threshold | nist-airmf | GOVERN 3.2 | tight | tight | 2 |
| 7 | human-authorization | nist-airmf | MAP 3.5 | tight | broad | 2 |
| 8 | high-assurance-verification | nist-airmf | MEASURE 1.3 | tight | broad | 3, 5 |
| 9 | ai-toolchain-register | iso-42001 | A.4.4 | tight | tight | 3 |
| 10 | ai-toolchain-register | nist-80053 | CM-8 | tight | broad | none |
| 11 | corroborate-external-claims | owasp-llm | LLM07 | tight | broad | none |
| 12 | output-encoding | atlas | AML.T0077 | tight | broad | 4 |
| 13 | resource-bounds | atlas | AML.T0029 | broad | broad | 4 |
| 14 | resource-bounds | atlas | AML.T0034.000 | broad | broad | 4 |
| 15 | resource-bounds | owasp-asi | ASI08 | tight | broad | none |
| 16 | threat-model-boundaries | owasp-proactive | C4 | tight | broad | 4 |
| 17 | guardrail-config-integrity | owasp-mcp | MCP02 | tight | broad | 4 |
| 18 | inter-agent-trust | owasp-mcp | MCP07 | tight | broad | none |
| 19 | no-cross-context-bleed | csa-ccm | I&S-06 | tight | broad | 4 |
| 20 | synthetic-fixture-data | nist-80053 | SA-3(2) | tight | broad | none |

Totals for these 20 rows:

- Definition A: 18 tight, 2 broad.
- Definition B: 3 tight (rows 4, 6 and 9), 17 broad.

The records are in [atlas.toml](atlas.toml), [csa-ccm.toml](csa-ccm.toml),
[iso-23894.toml](iso-23894.toml), [iso-42001.toml](iso-42001.toml), [nist-80053.toml](nist-80053.toml),
[nist-airmf.toml](nist-airmf.toml), [owasp-asi.toml](owasp-asi.toml), [owasp-llm.toml](owasp-llm.toml),
[owasp-mcp.toml](owasp-mcp.toml) and [owasp-proactive.toml](owasp-proactive.toml).

Some rows that are broad today may be tight under A (for example the decision classification rule against
ISO/IEC 23894 B.4, which is broad while rows 1 and 2 are tight). A later pass must review broad rows for
upgrades as well as tight rows for downgrades.
