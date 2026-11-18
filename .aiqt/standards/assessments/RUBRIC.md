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
  "one-to-one" wording in the standards README and the "1-to-1" wording on the public mappings page
  (`site/mappings/index.html`, generated from `site/draft-src/mappings.toml`).

### Terms used in both definitions

- **Main variant.** A variant the entry itself names: in its description, its numbered examples, its
  sub-techniques or its attack scenarios. A variant the assessor supplies from outside the entry does not
  count.
- **Listed mitigations.** The entry's own prevention, mitigation, remediation or implementation guidance at
  the location the record cites, item by item as the entry numbers or heads them. For a catalogue control
  or a management system control, the statement is what counts; text in a separate discussion or
  guidance part counts only as convention 6 below allows.
- **Essential element.** For a control or guidance entry, each lettered item and each verb of the
  normative statement. For a risk, technique or weakness entry, each main variant and each listed
  mitigation that applies at the rule's layer. An element the entry makes conditional is essential only as
  convention 5 decides; an item of a discussion or guidance part is essential only as convention 6 decides.
  Every essential element the rule does not meet in full goes into `omits`, with the part it does meet
  named in `covers`.

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
   title or from memory. For a numbered list of examples, mitigations or guidelines, read and cite the
   whole list to its last item, and for a prose entry read to the next entry's heading; the record may name
   narrower ranges inside that span, but `omits` is built against the whole of it.
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
   the record, and say so in `rationale`. A convention decides a label when the other reading of it would
   give a different `label-a` or `label-b`. Name it even when the record's reading is the one the
   assessor finds more likely, and state in `assumptions` which label the other reading would give. A
   convention whose other reading gives the same labels does not decide the row and is not named in
   `conventions`. If a label changes only when two conventions are both read the other way, name both, and
   say in `assumptions` that the label changes only when both are.

## Open conventions

The maintainer must settle these before a definition is adopted. A record that depends on one names it.

1. **Informative annexes.** Can a row that maps to an informative annex (for example ISO/IEC 23894
   Annex B, which lists risk sources rather than requirements) be tight at all?
2. **Rules as policies and procedures.** Does a pack rule, followed by an assistant, count as one of the
   "policies and procedures" an entry asks an organisation to have?
3. **The assistant as the system.** When an entry is about a system that an organisation builds, deploys,
   operates or governs (its oversight, its level of automation, its tooling, its inventory of
   components), does that system include the AI assistant and the tools it uses for the work, or only the
   product being built? If it means only the product, a rule that meets the entry only through the
   assistant or its tools does not act on the entry's object, and both labels are broad. Where this
   result and convention 9 point different ways, convention 9 wins: if the entry's object takes in the
   tooling used to build the system (in its statement, or in guidance as far as convention 6 allows), the
   assistant's tools are that tooling on the product reading too, so the product reading alone does not
   make the labels broad, and whether a rule on those tools acts on the entry's object is decided by
   convention 9 (and by convention 6 when only guidance shows that the object takes in the tooling).
   The result above stands for every other entry. This convention is engaged whenever the entry's object is such a system, AI or not, and the rule's boundary is the
   assistant itself, its actions and decisions, or its tools. It is not engaged when the entry's object
   is a kind of software the assistant itself is (an LLM or LLM-enabled application, an agent or agentic
   component, an MCP client or server), as in the OWASP LLM, agentic and MCP lists, because the entry
   then reaches the assistant directly. It is also not engaged when the entry acts where the assistant
   would not be even if counted as part of the system, such as the inbound edge of a service the
   assistant only calls. Two related questions are kept apart as conventions 8 and 9.
4. **Layer and boundary.** Can a rule that acts at the assistant's or the application's layer be tight
   to an entry whose mechanism or main control sits at another layer (the service's inbound edge, the
   client that renders output, the infrastructure, the authorization server)? This convention is engaged
   when the entry's object and the rule's object are the same kind of thing but the entry's main
   mitigations act at a different layer. A difference of scope alone (one change against the whole
   architecture, one application against an organisation's whole estate) is not a difference of layer:
   Definition A treats scope as secondary, so it does not engage this convention. When the objects
   differ, the question is convention 3, 8 or 9, or no fit.
5. **Conditional elements.** Does an element the entry makes conditional ("as necessary", "where
   appropriate") count as essential under B?
6. **Discussion and guidance text.** Can a mitigation named only in a control's discussion part or in
   separate implementation guidance (for example the SP 800-53 discussion, or ISO/IEC 42001 Annex B)
   satisfy test 5 under A? Do the items such guidance lists count as essential elements under B? In this
   pass both are read as: guidance may show the match under A, and its items are not essential under B.
7. **Human roles filled by an AI pass.** When an entry asks for people in a role (an independent
   assessor, an expert who did not build the system, a reviewer), can a check that a rule requires, and
   that may be run by another AI model, fill that role?
8. **The reach of a rule about the assistant.** Does a rule worded about the assistant's own conduct,
   consumption or limits also bind the product the assistant builds (its services, its endpoints and the
   requests they accept)? If it binds only the assistant, it does not act on an entry whose object is a
   service of the product, such as that service's inbound edge. This convention is engaged when the
   rule's text can be read either way and only the product reading puts the rule on the entry's object.
9. **Products that are not AI.** When an entry's object is an AI system and the rule acts on the product
   being built, or on the tools used to build it, whatever kind of product that is, does the rule act on
   the entry's object? In this pass it is read as yes: AI products are among the products the rule acts
   on, and the difference is one of scope, which Definition A treats as secondary. On the other reading,
   an entry about an AI system is met only by a rule aimed at what makes a system AI (its models, its
   data or its AI behaviour); a rule that applies to any product alike shares only the theme, and both
   labels are broad. This convention is engaged when the entry's object is an AI system and the rule's
   boundary is the work product or the tooling used to build it. Where it and convention 3 point
   different ways, this convention wins, as convention 3 states.

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
| 1 | human-oversight-and-autonomy-threshold | iso-23894 | B.4 | tight | broad | 1, 3 |
| 2 | human-authorization | iso-23894 | B.4 | tight | broad | 1, 3 |
| 3 | decision-classification-before-enacting | nist-airmf | MAP 3.5 | tight | broad | 2, 3 |
| 4 | decision-classification-before-enacting | nist-airmf | GOVERN 3.2 | tight | tight | 2, 3 |
| 5 | human-oversight-and-autonomy-threshold | nist-airmf | MAP 3.5 | tight | broad | 2, 3 |
| 6 | human-oversight-and-autonomy-threshold | nist-airmf | GOVERN 3.2 | tight | tight | 2, 3 |
| 7 | human-authorization | nist-airmf | MAP 3.5 | tight | broad | 2, 3 |
| 8 | high-assurance-verification | nist-airmf | MEASURE 1.3 | tight | broad | 7, 9 |
| 9 | ai-toolchain-register | iso-42001 | A.4.4 | tight | tight | 3, 6, 9 |
| 10 | ai-toolchain-register | nist-80053 | CM-8 | tight | broad | 3 |
| 11 | corroborate-external-claims | owasp-llm | LLM07 | tight | broad | none |
| 12 | output-encoding | atlas | AML.T0077 | tight | broad | 4 |
| 13 | resource-bounds | atlas | AML.T0029 | broad | broad | 8 |
| 14 | resource-bounds | atlas | AML.T0034.000 | broad | broad | 8 |
| 15 | resource-bounds | owasp-asi | ASI08 | tight | broad | none |
| 16 | threat-model-boundaries | owasp-proactive | C4 | tight | broad | none |
| 17 | guardrail-config-integrity | owasp-mcp | MCP02 | tight | broad | 4 |
| 18 | inter-agent-trust | owasp-mcp | MCP07 | tight | broad | 4 |
| 19 | no-cross-context-bleed | csa-ccm | I&S-06 | tight | broad | 3, 4 |
| 20 | synthetic-fixture-data | nist-80053 | SA-3(2) | tight | broad | 6 |

Totals for these 20 rows, under the readings the records state:

- Definition A: 18 tight, 2 broad.
- Definition B: 3 tight (rows 4, 6 and 9), 17 broad.

These totals depend on conventions 3, 8 and 9, each in its own way:

- Convention 3: if the system an entry is about means only the product being built, rows 1 to 7, row
  10 and row 19 are broad under both definitions. Row 19 is broad on that reading only while the no
  cross context bleed rule is read, as recorded, as binding only the assistant (convention 8); read as
  also binding the product, it stays tight under A.
- Convention 9: if an entry about an AI system is met only by a rule aimed at what makes a system AI,
  row 8 is broad under both definitions.
- Conventions 3 and 9 together, with 6: row 9 is broad under both definitions only if convention 3 is
  read the other way and convention 9 or convention 6 is too. On the product reading of convention 3
  the assistant's tools are tooling for an AI system only as tooling used to build it, which convention
  9 counts and which only the Annex B guidance shows (convention 6), so convention 9 decides as
  convention 3 states.
- Convention 8: if the resource bounds rule is read as also binding the services of the product the
  assistant builds, rows 13 and 14 are tight under A.

The records are in [atlas.toml](atlas.toml), [csa-ccm.toml](csa-ccm.toml),
[iso-23894.toml](iso-23894.toml), [iso-42001.toml](iso-42001.toml), [nist-80053.toml](nist-80053.toml),
[nist-airmf.toml](nist-airmf.toml), [owasp-asi.toml](owasp-asi.toml), [owasp-llm.toml](owasp-llm.toml),
[owasp-mcp.toml](owasp-mcp.toml) and [owasp-proactive.toml](owasp-proactive.toml).

Some rows that are broad today may be tight under A (for example the decision classification rule against
ISO/IEC 23894 B.4, which is broad while rows 1 and 2 are tight). A later pass must review broad rows for
upgrades as well as tight rows for downgrades.
