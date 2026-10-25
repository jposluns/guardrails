# AIQT disclosure: what we claim, and what we do not

For each thing AIQT does, the claim and its limitation stand side by side, each pointing to the evidence. Where a fact is not yet published, the matrix points to where it will appear rather than implying it.

## What exists today

Claim: AIQT 1.0.5, the chat assistant, is released and downloadable today: a Skill for Claude, and a portable instruction file intended for any assistant that accepts standing instructions. The development assistant, version 1.1.0, is in development. Teams and Enterprise are ideas under consideration, with no dates and no commitments.

Limitation: No platform test result is published yet: the evidence page marks every platform pending, and ChatGPT, Gemini, and Copilot as not yet tested, so compatibility with a given assistant is intended, not verified. A 1.1.0 feature described on this site is a design, not a shipped product; it ships when it does what these pages say it does. Nothing labelled an idea is promised at all.

Evidence: [The 1.0.5 release](https://aiqt.ai/evidence#release) [Where 1.1.0 stands](https://aiqt.ai/development#availability)

## What the product is made of

Claim: The 1.0.5 chat artefacts, the Claude Skill and the portable instruction files, are prose instructions and reference material only, with no executable code and no network calls. The repository that ships them also carries code, each file listed in the 1.0.5 release manifest: the Python and shell tooling under tools/ and opf/tools/; the opf enforcement hooks (the pre-commit hook and the Claude Code deny hook), the pre-commit installer, and the CI template and script under opf/enforcement/; the site HTML pages (inline scripts included) and JavaScript under site/ and opf/site/; docs/_shell.html, an HTML shell page with an inline script; the preview hook scripts under .preview/; and the generated Claude Code hooks plugin under plugin/ and its source under .aiqt/core/hooks/. This repository's own CI code under .github/, outside that manifest, runs the quality gates on GitHub once on each batch of pull requests before it merges into main and on each push to main, and a message-leak scan on every pull request targeting main; a separate scheduled workflow runs the offline standards-currency audit monthly and on manual dispatch. On your machine none of it runs on its own: each piece runs only when you run a tool or check, install the plugin or an enforcement hook, or open the site in a browser.

Limitation: Instructions cannot do anything your assistant platform cannot already do. The 1.1.0 design adds a guided install and a setup doctor around that tooling, each documented before it ships.

Evidence: [What the files are](https://aiqt.ai/install#pick) [The 1.1.0 directory](https://aiqt.ai/tech-details#directory)

## What it can enforce

Claim: AIQT is a behavioural standard your assistant is required to follow: it announces what its guardrails catch, surfaces issues instead of hiding them, and backs its claims with evidence.

Limitation: It does not technically prevent a model from erring. Results still depend on the model, the platform, competing instructions, and the tools in the conversation. AIQT does not make an AI infallible, and the chat skill does not sandbox, monitor, or block anything at runtime; the repository's CI gates and the opt-in Claude Code hooks plugin block or warn only on the specific actions they are built to catch, within each control's stated residuals.

Evidence: [What AIQT does not do](https://aiqt.ai/evidence#limits)

## Security coverage

Claim: AIQT's source corpus carries a universal, language-neutral security baseline: behavioural rules covering secrets, authentication, authorization, input validation, untrusted content, logging, and data minimization. The 1.0.5 chat skill ships the subset a chat assistant can act on directly: keeping secrets out of the transcript, treating pasted or fetched content as data, resisting social pressure, and sending only the data a task needs. The fuller per-language and development-time delivery is a 1.1.0 design, not yet shipped.

Limitation: It is not a static analyzer, a vulnerability scanner, a penetration test, or an audit, and it does not guarantee that generated code is secure. Deeper per-language depth is a 1.1.0 design, composed at install from third-party sources you choose, which AIQT lists and credits but does not author or vouch for.

Evidence: [Per-language depth, composed at install](https://aiqt.ai/tech-details#composed) [The frameworks the rules map to](https://aiqt.ai/mappings#by-framework)

## Standards mappings

Claim: AIQT publishes a navigational crosswalk from the pack's rules to identifiers in security and AI-governance frameworks (OWASP, NIST, MITRE ATLAS, ISO/IEC, CSA), each pinned to a stated edition, with every mapped identifier validated against that edition before it can ship.

Limitation: A mapping asserts a relationship of ideas, not certification, attestation, audit evidence, or proof that you meet a standard. No framework publisher endorses, sponsors, or is affiliated with the pack. The absence of a mapping means none has been asserted yet, and a pinned edition may lag the newest release.

Evidence: [How the crosswalk is built](https://aiqt.ai/mappings#methodology) [The framework registry](https://aiqt.ai/mappings#registry)

## How AIQT itself is verified, and its change record

Claim: Substantive changes to the pack are reviewed before they land by independent verifiers from different model families, each briefed to refute rather than confirm. This project holds its own pack to a standing floor of three families; the pack's portable rule for adopters is two families for substantive work, and a third for critical changes. Every substantive change is recorded; the public change log is a curated release-level view.

Limitation: This describes how the AIQT project verifies its own pack; it says nothing about whether changes you make with AIQT installed receive the same review. Routine bookkeeping changes run the mechanical gates only.

Evidence: [One QA standard, whichever model checks](https://aiqt.ai/tech-details#qa-standard) [The change log](https://github.com/jposluns/guardrails/blob/main/CHANGELOG.md)

## Data handling

Claim: The 1.0.5 chat artefacts send nothing anywhere: they are instructions your assistant reads, with no code and no network calls. The 1.0.5 release is wider than those artefacts: its repository ships code, each file listed in the release manifest and named under What the product is made of, and on your machine none of it runs on its own. What you type still goes to your assistant's vendor under that vendor's terms, exactly as it would without AIQT. In the 1.1.0 design, routine checks run locally and send nothing to a model.

Limitation: The shipped repository code is not network-free when run: some of the checks and tools make network requests when run. Among them: the link-liveness check that verifies published framework URLs, the CWE importer (tools/import_cwe.py), which fetches the catalogue it vendors, and tools/ci-status.sh, which reads CI results through the GitHub CLI. The 1.1.0 substantive and delicate review tiers will send content to model providers. The providers, credentials, retention, and cost stay marked pending until they are documented before 1.1.0 ships. AIQT cannot intercept, encrypt, or block data that your assistant platform itself sends.

Evidence: [What the 1.0.5 files are](https://aiqt.ai/install#pick) [The data flow, by tier](https://aiqt.ai/tech-details#qa-where)

## Platform support and currency

Claim: The evidence page carries the single platform test table: which platforms have a documented install method, when each was last tested, and the result. An untested platform says so plainly.

Limitation: As of today no platform test result is published; every last-tested entry is pending. Platform behaviour changes outside AIQT's control, so a test date states what was true that day, not a promise that it stays true. A per-file date on a rule says when the file last changed, not that it was re-verified on every platform that day.

Evidence: [Platform test status](https://aiqt.ai/evidence#platform-tests)

## Licence, warranty, and endorsement

Claim: AIQT is open source under the Apache License 2.0 (except vendored third-party material, which remains under its own terms; see the NOTICE): you may use, copy, modify, distribute, and prepare derivative works of it freely, including commercially, and you keep your changes. If you redistribute it you keep the licence and copyright notices, carry prominent notices in any files you modify stating that you changed them (Apache License 2.0, section 4(b)), and retain the applicable attribution notices from the NOTICE (Apache License 2.0, section 4(d)).

Limitation: It is provided as-is, with no warranty or condition of any kind. No trademark rights are licensed (Apache License 2.0, section 6), and it carries the patent grant the Apache License 2.0 provides (Apache License 2.0, section 3). Using AIQT does not mean the project or its maintainer endorses you, and nothing permits you to imply sponsorship or official status. Equally, no framework publisher or platform vendor endorses AIQT.

Evidence: [The licence](https://github.com/jposluns/guardrails/blob/main/LICENSE) [No endorsement or compliance claim](https://aiqt.ai/mappings#methodology)

## Not legal or compliance advice

Claim: AIQT can sit alongside your compliance framework and help you demand more checkable behaviour from your assistant.

Limitation: It is not legal advice, not a compliance certification, and no substitute for your own counsel, policy, regulator, or production approval gates. Responsibility for what you ship stays with you.

Evidence: [What AIQT does not do](https://aiqt.ai/evidence#limits)
