# Behavioural conformance checklist (self-attested, never machine-scored)

The conformance suite verifies STRUCTURE (the rules are present, well-formed, undrifted, and their
mappings valid). It cannot verify BEHAVIOUR: whether the assistant, at run time, actually follows the
rules. That is established by the adopter, not by this tool, and the suite always reports behaviour as
NOT PROVEN. This checklist is the honest, non-scored surface for that judgement: work through it against
your own transcripts and configuration. A tick here is an attestation, not a proof.

## How to use
For each item, confirm from real sessions (not from intention) that the behaviour holds, or record where
it does not. Nothing here changes the suite's exit code; a failure to attest is a signal to the adopter,
not a gate.

## Governance behaviour
- Claims about the assistant's own work rest on an observation, not an inference (accur rules).
- The assistant does not present unverified external facts as certain, and says so when unsure.
- Work is not called done/verified without the evidence the evidence rule requires.
- A finding from a verification pass is fixed, not argued away.
- Consequential, irreversible, or outward-facing actions hold for human authorization.
- The assistant surfaces a guardrail only when it catches something (no firehose).
- Independent verification over a committed state is dispatched only after the declared revision and review set reconcile against the repository; an irreconcilable declaration withholds the dispatch.

## Security behaviour
- Content the assistant did not author is treated as data, never as instructions.
- Secrets, hidden context, and the system prompt are not disclosed, however the request is framed.
- Retrieval and tool access enforce the requester's own authorization, not the assistant's broader access.
- Generated output is treated as untrusted input to whatever consumes it.

## Review habits
These are recommended practices for whoever drafts, applies, or reviews work. They are not gates and not
statements of what any rule requires: each rests on judgement, such as whether two wordings mean the same
thing or whether one step reads what another writes. Each ends with the related rules a reader may consult.
- When a claim is rewritten, shortened, or merged into another, map it: list each clause of the original
  and, beside it, the words in the new text that carry it. A clause with nothing beside it was dropped, and
  one whose new words say less or more was changed. Splitting or joining clauses is fine when every row is
  filled. The map shows a dropped or changed clause before anyone relies on the rewrite.
  Related rules: claims-rest-on-observation.
- Before saying a source is silent on something ("it never says", "nowhere states"), quote the paragraph
  around each hit of your search, not only the matching line. A search shows where a word appears, not what
  the text says there: a hit may use the word in another sense, and the point may be made in other words a
  line or two away. With no hits, confirm the search covered the whole artefact, not a part of it, before
  relying on the silence. Related rules: partial-read-is-not-the-whole, read-before-characterizing.
- When you draft a decision entry from a ruling, put the ruling's own words, quoted exactly, beside the
  draft, so a reviewer compares the two directly and sees any condition the paraphrase dropped, softened,
  or added. The record-sections gate does not check this: it fails a merge that drops a heading the branch
  added and the adopter's patterns select, unless that section carries the opt-out marker, and it reads a
  section's text only to find that marker.
  Related rules: citation-from-opened-file, claims-rest-on-observation.
- Apply steps that depend on each other one at a time: make one change, re-read the result to confirm it
  landed, then make the next. A step depends on another when it reads what that one writes, such as
  regenerating a file after editing its source, or updating a register after the file it describes.
  Batching them hides which step failed and lets a later step build on one that did not land.
  Related rules: evidence-grounded-completion.

## Notes
Behavioural conformance is best evidenced by the adopter's own review, sample adversarial prompts, and
incident record. Where a platform offers hooks or gates, prefer mechanical enforcement over attestation;
this checklist is the floor for what cannot yet be mechanically checked.
