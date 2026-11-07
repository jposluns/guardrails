# OPFiles (OPF): the operational-files standard

OPFiles is an open standard for keeping a project's operational records, its backlog, findings,
decisions, worklog and session handoffs, in a form both people and AI development assistants can
trust. Each record lives in a fixed, machine-written source file, and the readable version is
generated from it. A check, set as a required step in your commits or CI, fails the moment the two
disagree.

The website is [opfiles.ai](https://opfiles.ai). The standard is open source under the Apache
License 2.0, except vendored third-party material, which remains under its own terms.

Status: the specification is a working draft. 2 of its 10 commands are not yet implemented, and
adoption cannot finish yet (see [Adoption](#adoption)).

## Why it exists

AI assistants and people both lose track of operational state: what was decided, what is still
open, what changed and why. Hand-kept notes drift, and a readable summary edited by hand can quietly
disagree with the facts underneath it. OPF fixes this with 3 rules:

- **One source per fact.** Each record is written once, in a closed TOML schema that refuses
  what does not fit.
- **Views are generated, never edited.** The Markdown views (BACKLOG, FINDINGS, DECISIONS and so
  on) are regenerated from their declared sources.
- **A drift gate holds the two together.** The gate re-renders every view and compares the bytes;
  if a source and its view disagree, the build fails.

The standard also forbids reusing a record's number or deleting a record. Old records are archived
(relocated), never removed.

## How it works

1. **Edit the source.** You or your assistant change a record in the machine store under
   `.working/` (for example with `opf record`).
2. **Run the renderer.** `opf render` regenerates every declared view from its sources.
3. **Check the view.** `opf render --check` and `opf doctor` run as a required check. `doctor`
   exits 0 when clean, 1 on a finding, and 2 when it cannot evaluate.

Schema validation and drift checks catch structural defects; they do not establish that a record's
claims are true. Review recorded claims against their evidence.

## Adoption

The intended path is assistant-led. Give your AI development assistant this prompt:

```text
Read https://opfiles.ai/llms.txt and https://opfiles.ai/adopt.md, then inspect this project and propose a plan to adopt OPFiles (OPF). The plan should name every file you would create, import, or retire, and why. Do not change any files until I have reviewed and approved the plan.
```

1. Your assistant reads the standard and inspects your project.
2. You review the plan it proposes: what it would create, import, or retire.
3. You authorize it, then inspect the scaffolded store and the validation results.

Adopting by hand is the fallback when you cannot use an assistant; both paths target the same
store format. In this build, `opf adopt` can plan, approve, reconcile and report status, but its
apply step refuses every plan and `complete` refuses with exit 2, so adoption cannot finish yet.
`opf init` scaffolds a new store today.

## The files and what they do

The store lives in exactly 1 subdirectory of `.working/` whose manifest declares the OPF token.
Tools find it that way and stop, rather than guess, if there are 0 or more than 1.

### Core files

| File | What it is | Why it exists |
| --- | --- | --- |
| Store manifest | The store's control document: its schema, types, views, modules and enforcement posture | Tools read the contract instead of inferring it; an unknown key is a finding |
| ID counters | 1 monotonic high-water value per record namespace | Record numbers are never reused |
| Version ledger | Version and release numbers, dates, spans and digests | Releases are traceable without prose |
| Worklog | 1 entry per change, appended as the work happens | The detailed record; correctable until released, then frozen |
| Single-writer lease | Present only while a run holds the store | Prevents two runs writing at once; its absence is normal |

### Record indexes

| Record | What it holds |
| --- | --- |
| Backlog | 1 record per unit of planned work, with a permanent ID, a closed set of states and a ratified "done" |
| Completion receipts | A durable receipt, created in its single terminal state and immutable |
| Findings | An observation worth acting on, linking its remediation |
| Pending decisions | A question that must be answered before dependent work settles |
| Self-made decisions | A call an actor was authorized to make, with its basis and effects |
| Maintainer rulings | An immutable ruling: the decision, the answer and its rationale |
| Standing preferences | The reusable rule beneath a family of rulings |
| Blocks | Named work that cannot proceed, scoped to listed record IDs |
| Session handoff | The state a session resumes from; at most 1 current handoff |
| Sources | A captured source, recorded when a claim resting on it is made |
| Peer work | Work proposed or sent to a peer project: proposal, delivery and outcome |

Around them sit the store pointer (where the store repository lives; one that does not resolve
stops the tool), the archive (1 rotation manifest per calendar year; rotation is relocation, never
deletion) and the generated decisions projection, `.working/DECISIONS.toml`. 4 optional modules are
off by default.

The specification defines 12 baseline record types (11 typed indexes plus the worklog) and
10 command verbs; the reference scaffold declares 13 generated views.

## The command verbs

| Verb | Does | Status |
| --- | --- | --- |
| `init` | scaffolds the store | implemented |
| `render` | regenerates and checks the views | implemented |
| `doctor` | verifies the store | implemented |
| `absorb` | drafts changelog entries for human curation | implemented |
| `record` | authors 1 record change at a time | implemented |
| `adopt` | plan, approve, reconcile and status run; apply and complete refuse | partly implemented |
| `upgrade` | refuses with exit 2; no store upgrade is defined | refuses |
| `import` | ordinary modes retired; refuse with exit 2 | retired |
| `migrate` | specified, not yet implemented | planned |
| `sync` | specified, not yet implemented | planned |

The `opf` reference CLI is 1 implementation of the standard's command vocabulary. You can use your
own tooling instead, or adopt by hand.

## What is in this folder

- [`spec/`](spec/): the specification ([OPF-SPEC.md](spec/OPF-SPEC.md)) and the
  [quickstart](spec/OPF-QUICKSTART.md).
- [`tools/`](tools/): the `opf` reference CLI (`opf.py`) and its checks.
- [`prompt-pack/`](prompt-pack/): the prompts an assistant follows to adopt, import and work the flow.
- [`enforcement/`](enforcement/): the enforcement material for the drift gate and checks.
- [`site/`](site/): the opfiles.ai website.
