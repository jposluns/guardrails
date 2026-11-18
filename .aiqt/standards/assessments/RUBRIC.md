# Mapping fit assessment rubric

Every mapping in a rule's frontmatter carries a fit: `-tight` or `-broad`. This folder holds the rubric
used to review those fits and one record file per framework with the assessed rows. The records sit beside
the framework manifests (see the [standards README](../README.md)) but are not manifests: the manifest
loader reads only the `*.toml` files at the top of `.aiqt/standards/`, never this folder.

This is the first pass. It records a label for each assessed row under two definitions of tight, and it
changes no label in any rule.

**The maintainer's choice.** On 2026-11-18 the maintainer chose Definition B, in a recorded decision: a
tight fit is substantial coverage of the whole control, not only of its core
mechanism. Definition A stays in the records as `label-a`, because it is the first half of B and
because the open conventions are still stated against it. Every record now applies B to the whole
control, and its `adopted` field says whether the row's B label is settled or still depends on an open
convention (see the record schema). Adopting B in the records changes no fit in any rule's frontmatter,
in the mappings or on the site; that is a later change.

## The two definitions of tight

### What both definitions share

- A link that rests only on a shared theme, a precondition, or a downstream impact is broad.
- A link where the rule mitigates no mechanism of the entry is a candidate for removal.
- Neither definition limits how many ids one rule may map as tight. Definition B, now chosen, is to
  replace the "one-to-one" wording in the standards README and the "1-to-1" wording on the public
  mappings page (`site/mappings/index.html`, generated from `site/draft-src/mappings.toml`); that
  wording is not changed in this pass.

### Terms used in both definitions

- **Main variant.** A variant the entry itself names: in its description, its numbered examples, its
  sub-techniques or its attack scenarios. A variant the assessor supplies from outside the entry does not
  count.
- **Listed mitigations.** The entry's own prevention, mitigation, remediation (immediate or temporary
  remediation included), detection or implementation guidance, item by item as the entry numbers or heads
  them, with detection guidance as defined here. They are read from the whole entry as the pinned edition
  presents it, never from one part of it: where an edition carries an entry across more than one file or
  part (the ATLAS release, for example, gives a technique's text in its techniques file and links its
  mitigations to it only through `mitigates` relations in its YAML copy), every part is read, and a record
  never narrows the control by citing only one of them. For a technique, each mitigation the edition links
  to it is a listed mitigation. Whether a sub-technique also takes in the mitigations the edition links to
  its parent technique is open (convention 10). Detection guidance is any part or item that names signs of
  an attack or failure to watch for, where the entry heads or numbers it as detection guidance or
  otherwise presents those signs as something to detect (calling them detection hooks or signals, for
  example), wherever in the entry they sit, the prose of the description included; each sign it names
  counts. Signs the prose names without presenting them as something to detect are read for the core and
  the main variants only. For a catalogue control or a management system control, the statement is what
  counts; text in a separate discussion or guidance part counts only as convention 6 below allows.
- **Exposure checklists.** A checklist of the conditions under which a system is exposed (a "how to
  detect" or "is the application vulnerable" list that names exposed conditions rather than signs of an
  attack) is part of the control under B when the entry presents its conditions as requirements: that is,
  when it says that any one of them that holds makes, or may make, the system vulnerable, or leaves it
  likely exposed (the MCP02 and MCP07 checklists use those two forms), so that each is to be absent.
  Each condition is then weighed. A condition that restates a listed mitigation is recorded with that
  mitigation; a condition that asks for something no listed mitigation states is an essential element of
  its own. A checklist that the entry presents only as examples, without tying exposure or possible
  exposure to any one condition, adds no elements.
- **Parts that add no elements.** An entry's impact list, its list of the vulnerabilities it prevents, its
  references, further reading, tool lists, pointers to other documents and links to suggest edits. They
  are read for the core, but nothing in them is a main variant or a listed mitigation, and `omits` does
  not list them.
- **At the rule's layer, or at another layer.** One test decides each case. Name the act a listed
  mitigation asks for, and ask whether the party the rule binds can carry out that act by itself, with
  what it holds and runs, for the agent, tool or system the entry is about. The party the rule binds is
  the assistant, and also the product it builds where convention 8 is read that way. If it can, the
  mitigation is at the rule's layer. If the act needs something only another party holds or runs, it is at
  another layer, and the record names that party. These acts are at another layer wherever they appear:
  issuing, expiring, rotating, revoking or binding credentials, tokens, keys or certificates (the issuer
  or the identity provider); granting an agent's permissions, auditing the permissions granted, and
  keeping the records of changes to them (the party that grants them and holds those records), except
  where the assistant is that party, for the sub-agents it starts and the tools it hands on to them, where
  granting and auditing those permissions are at the rule's layer; enforcing access, mutual
  authentication or address restrictions at a server, tool, endpoint or network, keeping tamper evident
  logs there, and watching those logs for signs of attack (whoever runs the servers, the network or the
  infrastructure); and rendering output (the client). Holding its own actions for human
  approval, limiting the tools it hands on, refusing authority it cannot verify, storing the secrets it
  holds, and watching its own actions and the messages it receives are at the rule's layer. A mitigation
  that asks for several acts is split, and each act is marked on its own. The same act gets the same
  marking wherever it appears, in every record. Under the adopted Definition B the marking does not make
  a mitigation non-essential: a requirement a project cannot meet at its own layer is still part of the
  control, and leaving it out weighs against tight. The marking records where the mitigation sits, which
  convention 4 reads under A, so records that name convention 4 mark each listed mitigation in `omits`
  that sits at another layer.
- **Essential element.** For a control or guidance entry, each lettered item, each verb and each object
  of the normative statement, for the whole of each object it names: an element the rule meets for only
  part of its object (one of several systems or configurations, or a subset of the components or tooling)
  is not met in full. For a risk, technique or weakness entry, each main variant and each listed
  mitigation, at whatever layer it sits, each sign of its detection guidance, and each condition of an
  exposure checklist that is part of the control and adds an element. An element the entry makes
  conditional is essential only as convention 5 decides; an item of a discussion or guidance part is
  essential only as convention 6 decides. Every essential element the rule does not meet in full goes
  into `omits`, with the part it does meet named in `covers`.

### Definition A: direct mitigation

A row is tight under A when all three hold:

1. The rule acts on the same object or boundary as the entry.
2. The rule directly carries out the entry's core obligation, or interrupts the entry's core attack
   mechanism, for at least one main variant.
3. The match can be seen in the entry's own statement or in its listed mitigations.

Missing secondary elements (documentation, a review cadence, organisation-wide scope, other variants) do
not stop a row from being tight under A.

### Definition B: substantial coverage of the whole control (chosen)

The maintainer chose this definition on 2026-11-18, in a recorded decision. It asks for
substantial coverage of the whole control, not only of its core mechanism. A row is tight under B when A
holds and also:

- for a control or guidance entry, following the rule meets every element of the normative statement:
  each lettered item, each verb and each object, for the whole of each object;
- for a risk, technique or weakness entry, the rule covers the entry's main variants and every line of
  its mitigations, whatever layer each sits at: each listed mitigation, each sign of its detection
  guidance, and each condition of an exposure checklist that is part of the control, as the terms above
  define them.

What the control requires is read from the whole control, never narrowed to what the rule's layer can
reach. Leaving out any essential element makes the row broad under B.

## Decision tests

Apply these in order for each row and write the outcome into the record.

1. Read the entry's text in the pinned edition, at the location the record names. Never assess from the
   title or from memory. Read the whole entry, from its first line to its last line before the next entry
   starts (leaving out page numbers and running headers or footers of the reference copy), and cite that
   whole span in `text-location`, naming where any parts that add no elements sit. For a catalogue
   control the span is the statement parts, and the discussion part as well where the record relies on
   it. The record may name narrower ranges inside that span, but `omits` is built against the whole of it.
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
9. If a label rests on a fact about how the rule is used rather than on the text of the rule or the entry
   (for example whether the assistant holds context for more than one user or tenant), assess the row for
   the case the rule's own text addresses, state that case in `assumptions` with the labels the row would
   get where it does not hold, and do not name it in `conventions`: it is a premise, not an open
   convention.

## Open conventions

Choosing Definition B did not settle these, and the maintainer has settled none of them. All ten stay
open, all three questions of convention 6 included. Conventions 6, 9 and 10 state below the reading taken
in this pass. Conventions 1 to 5, 7 and 8 state a question without a reading: where one of them decides a
label, the record states the reading it takes in `assumptions`, with the labels the other reading would
give, and the totals below say what each other reading changes. Every reading taken is the assessor's
proposal, not a decision. A record names every convention that decides one of its labels (test 8).

1. **Informative annexes.** Can a row that maps to an informative annex (for example ISO/IEC 23894
   Annex B, which lists risk sources rather than requirements) be tight at all?
2. **Rules as policies and procedures.** Does a pack rule, followed by an assistant, count as one of the
   "policies and procedures" an entry asks an organisation to have?
3. **The assistant as the system.** When an entry is about a system that an organisation builds, deploys,
   operates or governs (its oversight, its level of automation, its tooling, its inventory of
   components, its applications), does that system include the AI assistant and the tools it uses for the
   work, or only the product being built? If it means only the product, a rule that meets the entry only
   through the assistant or its tools does not act on the entry's object, and both labels are broad, with
   one exception. Where the entry's object takes in the tooling used to build the system, the assistant's
   tools are that tooling on the product reading too, so a rule on those tools still acts on the entry's
   object. Whether the object takes in that tooling is read from the entry's statement, or from its
   guidance as far as convention 6 allows. The exception holds for every entry, AI or not; when the system
   is an AI system, whether a rule on the tooling for any product acts on tooling for an AI system is then
   the question of convention 9.

   To decide whether this convention is engaged, ask these questions in order and stop at the first one
   that answers. (a) Is the entry's object named as a kind of software the assistant is by its nature (an
   LLM or LLM-enabled application, an agent or agentic component, an MCP client or server), as in the
   OWASP LLM, agentic and MCP lists? Then it is not engaged, because the entry reaches the assistant
   directly. A class that takes in software of every kind (a system, its components, applications,
   services, infrastructure) is not such a kind, even though the assistant falls inside it. Nor is an AI
   system, or AI named in general terms (the AI of a human and AI configuration, for example), which takes
   in AI software of every kind, even though the assistant is an AI system by its nature. For both kinds
   of class, whether the class takes in the assistant is the question this convention asks, so (a) does
   not answer and the assessor goes on to (b). (b) Does the entry act only where the assistant would not
   be even if counted as part of the system, such as the inbound edge of a service the assistant only
   calls? Then it is not engaged. (c) Is the entry's object a system, AI or not, that an organisation
   builds, deploys, operates or governs, and is the rule's boundary the assistant itself, its actions and
   decisions, or its tools? Then it is engaged. (d) Otherwise it is not engaged. Every record states in
   `assumptions` which question answers for its row. Two related questions are kept apart as conventions
   8 and 9.
4. **Layer and boundary.** Can a rule that acts at the assistant's or the application's layer be tight
   to an entry whose mechanism or main control sits at another layer (the service's inbound edge, the
   client that renders output, the infrastructure, the authorization server)? This convention is engaged
   when the entry's object and the rule's object are the same kind of thing but the entry's main
   mitigations act at a different layer. A difference of scope alone (one change against the whole
   architecture, one application against an organisation's whole estate) is not a difference of layer:
   Definition A treats scope as secondary, so it does not engage this convention (under B the whole of
   each object counts, as the term essential element says). For a control or
   guidance entry, the layer is where its statement places the obligation; when the statement names the
   rule's layer among others (applications as well as infrastructure, for example), the other layers are a
   difference of scope, not of layer, and this convention is not engaged. When the objects differ, the
   question is convention 3, 8 or 9, or no fit.
5. **Conditional elements.** Does an element the entry makes conditional ("as necessary", "where
   appropriate") count as essential under B?
6. **Discussion and guidance text.** Can a mitigation named only in a control's discussion part or in
   separate implementation guidance (for example the SP 800-53 discussion, or ISO/IEC 42001 Annex B)
   satisfy test 5 under A? Can such guidance show what the entry's object takes in (test 3), as the
   tooling exception of convention 3 asks? Do the items such guidance lists count as essential elements
   under B? All three questions stay open. In this pass the first two are read as: guidance may show the
   match under A and what the object takes in. The third is not settled by the choice of B: B reads the
   whole control, but whether a guidance part is part of the control is the question itself. The
   assessor's proposed reading is that an item of a discussion or guidance part is an essential element
   under B when, and only when, the source presents it as a requirement of the control, and that a heading
   that calls the guidance normative is weighed with what the source says the guidance is for. On that
   reading, ISO/IEC 42001 heads Annex B "(normative)", but its B.1 says the guidance supports
   implementation, need not be documented or justified in the statement of applicability, is not always
   suitable or sufficient, and can be extended, modified or replaced by the organisation's own
   implementation, and clause 6.1.3 asks the organisation to consider it; B.4.4 also presents its list as
   examples of what tooling can include. So the heading makes considering the guidance a requirement, not
   each item, and the B.4.4 items are not essential elements under B. No label depends on this third
   question today: row 9 is broad under B on its statement's object, row 10 leaves lettered items of the
   CM-8 statement out, and row 20 leaves both items of the SA-3(2) statement out, so each is broad under
   B whether guidance items are essential elements or not (see their records).
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
   boundary is the work product or the tooling used to build it, including the assistant's tools where
   convention 3 is read as meaning only the product and its tooling exception applies.
10. **Sub-techniques and their parent's mitigations.** Does a sub-technique take in the mitigations an
    edition links to its parent technique, as well as those linked to it directly? In this pass it is
    read as yes: the edition records the sub-technique as a specialization of the parent, and a
    mitigation of the parent's behaviour can apply to each way of carrying that behaviour out. On either
    reading a sub-technique takes in nothing linked only to its sibling sub-techniques. The ATLAS 2026.06
    release's own linking pattern is evidence against the reading taken. Where the release means a
    parent's mitigation to apply to the sub-techniques, it repeats the link on each one: AML.M0004 and
    AML.M0024 are linked to AML.T0024 and to each of its three sub-techniques (lines 13548 to 13571 and
    14296 to 14315 of its YAML copy), while AML.M0019 is linked to AML.T0024 (lines 14096 to 14100) and
    to none of them. Counted over the `mitigates` and `specializes` relations of that YAML copy, 45
    links run from a mitigation to a parent technique that has sub-techniques; 15 are repeated on every
    one of its sub-techniques and 30 are missing from at least one, and 40 of the 70 sub-techniques have
    no mitigation linked to them directly. So a parent's link that a sub-technique does not repeat can be
    read as deliberate, and on that reading a sub-technique takes in only the mitigations linked to it
    directly. This convention is engaged when a row maps to a sub-technique whose parent has a linked
    mitigation that the sub-technique does not repeat.

## Record schema

One file per framework, named after the framework's manifest, holding one `[[row]]` table per
(rule, framework, id). The records never quote control text. Exact wording stays in the source, reached
through `source`, `text-location` and `source-sha256`; the records use own words only. Where a record
also reads a second reference copy of the same edition (the ATLAS YAML copy, for the `mitigates` and
`specializes` relations its techniques file does not carry), `text-location` names that copy, its SHA-256
and the lines read.

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
| `adopted` | `B` when the row's B label holds on every reading of every open convention; `B pending convention N` (each such convention by number, as in `B pending conventions 3 and 8`) when the other reading of an open convention, or of several read the other way together, would give another B label; where only several together would, `assumptions` says so |
| `proposed` | the B label (`tight`, `broad` or `remove`) when `adopted` is `B`; `pending-convention` when `adopted` names a dependency, with `label-b` giving the label on the reading the record states |
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

| row | rule | framework | id | label-a | label-b | adopted | conventions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | human-oversight-and-autonomy-threshold | iso-23894 | B.4 | tight | broad | B | 1, 3 |
| 2 | human-authorization | iso-23894 | B.4 | tight | broad | B | 1, 3 |
| 3 | decision-classification-before-enacting | nist-airmf | MAP 3.5 | tight | broad | B | 2, 3 |
| 4 | decision-classification-before-enacting | nist-airmf | GOVERN 3.2 | tight | broad | B | 2, 3 |
| 5 | human-oversight-and-autonomy-threshold | nist-airmf | MAP 3.5 | tight | broad | B | 2, 3 |
| 6 | human-oversight-and-autonomy-threshold | nist-airmf | GOVERN 3.2 | tight | broad | B | 2, 3 |
| 7 | human-authorization | nist-airmf | MAP 3.5 | tight | broad | B | 2, 3 |
| 8 | high-assurance-verification | nist-airmf | MEASURE 1.3 | tight | broad | B | 7, 9 |
| 9 | ai-toolchain-register | iso-42001 | A.4.4 | tight | broad | B | 3, 6, 9 |
| 10 | ai-toolchain-register | nist-80053 | CM-8 | tight | broad | B | 3 |
| 11 | corroborate-external-claims | owasp-llm | LLM07 | tight | broad | B | none |
| 12 | output-encoding | atlas | AML.T0077 | tight | broad | B | 4 |
| 13 | resource-bounds | atlas | AML.T0029 | broad | broad | B | 8 |
| 14 | resource-bounds | atlas | AML.T0034.000 | broad | broad | B pending conventions 8 and 10 | 8, 10 |
| 15 | resource-bounds | owasp-asi | ASI08 | tight | broad | B | none |
| 16 | threat-model-boundaries | owasp-proactive | C4 | tight | broad | B | none |
| 17 | guardrail-config-integrity | owasp-mcp | MCP02 | tight | broad | B | 4 |
| 18 | inter-agent-trust | owasp-mcp | MCP07 | tight | broad | B | 4 |
| 19 | no-cross-context-bleed | csa-ccm | I&S-06 | tight | broad | B | 3 |
| 20 | synthetic-fixture-data | nist-80053 | SA-3(2) | tight | broad | B | 6 |

Totals for these 20 rows, under the readings the records state:

- Definition A: 18 tight, 2 broad.
- Definition B, as chosen: 0 tight, 20 broad. Rows 4, 6 and 9, tight under B in the earlier rounds,
  are broad once B is read over the whole control: rows 4 and 6 count the objects of GOVERN 3.2 as well as
  its verbs, and row 9 counts the whole of the tooling the A.4.4 statement names (see their records).
- The B label holds on every reading of every open convention for 19 of the 20 rows. Rows 13 and 14,
  which earlier rounds left waiting on convention 8, are broad under B on either reading of it once their
  listed mitigations are read from the whole technique: the ATLAS release links access control on the
  model's API (AML.M0019) to the Denial of AI Service technique and to the parent of the Excessive
  Queries sub-technique, and the resource bounds rule does not meet it on either reading (see their
  records). For row 14 this holds only while a sub-technique is read as taking in its parent's
  mitigations (convention 10): if it takes in only those linked to it directly, row 14 has no listed
  mitigations, and where convention 8 is read the other way as well, its record gives tight under B. Row
  14's B label is therefore pending conventions 8 and 10 together.

The A totals depend on conventions 1, 2, 3, 4, 6, 7, 8 and 9. The B totals depend only on conventions 8
and 10 read the other way together, which would make row 14 tight under B (1 tight, 19 broad); in every
other row the omissions that make the row broad under B stand on every reading. Each item below says
what changes when that convention is read the other way and every other convention is read as the
records state:

- Convention 1: if an informative annex entry cannot be tight, rows 1 and 2 are broad under A as well.
- Convention 2: if a pack rule is not one of an organisation's policies, procedures or processes, rows 3
  to 7 are broad under A as well.
- Convention 3: if the system an entry is about means only the product being built, rows 1 to 7, row
  10 and row 19 are broad under A as well. Row 19 is broad on that reading only while the no cross
  context bleed rule is read, as recorded, as binding only the assistant (convention 8); read as also
  binding the product, it stays tight under A.
- Convention 4: if a rule cannot be tight to an entry whose mechanism or main control sits at another
  layer, rows 12, 17 and 18 are broad under A as well.
- Convention 6, first question: if only a control's statement can show the match, row 20 is broad under
  A as well.
- Convention 6, third question: no label changes. The assessor's proposed reading, that a guidance item
  is an essential element under B only where the source presents it as a requirement, is the reading
  rows 9 and 10 state; if every item of a discussion or guidance part were an essential element under
  B, rows 9, 10 and 20 would be broad under B as they are.
- Convention 7: if the entry's role needs a person, row 8 is broad under A as well.
- Convention 8: if the resource bounds rule is read as also binding the services of the product the
  assistant builds, rows 13 and 14 are tight under A; row 13 stays broad under B, and row 14 does too
  while convention 10 is read as recorded.
- Convention 9: if an entry about an AI system is met only by a rule aimed at what makes a system AI,
  row 8 is broad under A as well.
- Convention 10: no label changes on its own. If a sub-technique takes in only the mitigations linked to
  it directly, row 14 has no listed mitigations, but while convention 8 is read as recorded the rule does
  not act on the service the sub-technique targets, so both labels stay broad.
- Convention 8 together with convention 10: if both are read the other way, row 14 is tight under A and
  under B.
- Convention 3 together with convention 9 or convention 6: row 9 is broad under A as well only if
  convention 3 is read the other way and convention 9 or convention 6 is too. On the product reading of
  convention 3 the assistant's tools are tooling for the AI system only through that convention's
  tooling exception, which rests on the Annex B guidance showing that the control's tooling takes in
  tools used to build the system (convention 6) and on a register kept for any product counting for an AI
  product (convention 9).

Convention 5 decides no row: the one record that names a conditional element (row 8) gives the same
labels on either reading.

The records are in [atlas.toml](atlas.toml), [csa-ccm.toml](csa-ccm.toml),
[iso-23894.toml](iso-23894.toml), [iso-42001.toml](iso-42001.toml), [nist-80053.toml](nist-80053.toml),
[nist-airmf.toml](nist-airmf.toml), [owasp-asi.toml](owasp-asi.toml), [owasp-llm.toml](owasp-llm.toml),
[owasp-mcp.toml](owasp-mcp.toml) and [owasp-proactive.toml](owasp-proactive.toml).

Some rows that are broad today may be tight under A (for example the decision classification rule against
ISO/IEC 23894 B.4, which is broad while rows 1 and 2 are tight). A later pass must review broad rows for
upgrades as well as tight rows for downgrades.
