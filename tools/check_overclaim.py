#!/usr/bin/env python3
"""Overclaim lint across the site, the hand-authored repo prose, and every generated textual output.

AIQT's public copy is deliberately guidance-not-guarantee: it says what your assistant is asked and
required to do, never what AIQT itself guarantees the model will do. This gate catches a regression of
the guarantee-flavoured class the F-59/F-67 pass softened, so a reintroduced overclaim fails CI rather
than shipping. On the SITE pages it scans the VISIBLE TEXT of each page (tags, <script>, and <style>
stripped; entities unescaped; whitespace collapsed), so a phrase that wraps across source lines is still
one string and an overclaim hidden in an attribute is not falsely flagged. Text from two SEPARATE block
elements is kept apart by a space (see BLOCK_TAGS): "<p>guarantees</p><p>secure</p>" reads as two
phrases, not the phantom token "guaranteessecure", so an overclaim cannot hide by straddling a block
boundary; inline markup ("<b>guar</b>antee") still joins into one word.

TWO SURFACE CLASSES, TWO PATTERN SETS (VER-CORE 4.4). The guarantee-flavoured MARKETING patterns
(SITE_PATTERNS) are calibrated for the public site copy and run on the SITE PAGES ONLY: the rule corpus,
the generated adapters, and the hooks legitimately discuss "guarantee", "ensure", and "every" as
governance language ("that inert guarantee is BOUNDED, not categorical"), so running the marketing
deny-list over them would false-positive on honest text. The RELEASE-INTEGRITY patterns (RELEASE_PATTERNS:
tamper evidence/detection/resistance, an independent integrity channel/anchor, a stale signing claim) run
on EVERY scanned surface, because a release-integrity overclaim ships just as surely from a generated
adapter or a shipped doc as from a site page (4.4: no shipped or adopter-facing surface may claim tamper
detection, tamper evidence, tamper resistance, an independent integrity channel/anchor, or cryptographic
signing. The whole tamper-* family and any FORWARD promise about it are banned outright, D2/D3, so there
is no future-tense exception; integrity is described only in plain validation language).

THREE SURFACE COLLECTORS (VER-CORE 4.4c; replaces the former site-only scan):
  1. site/*.html: visible-text + meta scanning, SITE_PATTERNS + RELEASE_PATTERNS. site/ is a REQUIRED
     surface: an absent, unwalkable, or non-directory site/ is a fail-closed exit 2, never a silent PASS
     (the 4.4c correction of the old "no site/ directory -> PASS" shape). The enforcement register page
     (site/enforcement.html) is scanned like any other site page, with NO exemption for its verbatim
     ledger-residual blocks: the residues it renders are kept marketing-clean AT THEIR SOURCE (see the
     source-side residue-cleanliness leg below), so no per-page carve-out is needed. This is robust by
     construction: a plain scan over the whole page cannot be fooled by an exemption heuristic.
  2. The hand-authored repo prose roster (README.md, SCOPE.md, SYSTEM-HARDENING.md, aiqt-barebones.md):
     RELEASE_PATTERNS. The spec's other named prose surfaces (DISCLOSURE.md, CHANGELOG.md, ROADMAP.md,
     CLAUDE.md) are gensrc-REGISTERED generated outputs and so arrive through collector 3; the roster
     carries the hand-authored remainder, including the shipped starter file aiqt-barebones.md. Every rostered path is REQUIRED; an absent one is exit 2. Nothing is dormant
     today (every rostered surface exists), so the roster is a single declared constant with no idle
     dormant/armed machinery, per the spec's dormant/armed single-source discipline.
  3. Every TEXTUAL generated output, enumerated FROM THE GENSRC REGISTRY by recomputing it in memory
     (gen_gensrc.build_registry): a `file` target is scanned whole; a `tree` target is walked fail-closed
     (walk_files, os.walk not rglob) and every member scanned; a `block` target is scanned as the FULL
     rendered file (generated text is not contiguous in any source, so the whole rendered surface is
     scanned, including the hand-authored bytes around a managed block such as CLAUDE.md's, per 4.4c);
     a `binary` target (the [checkout].binary roster from gen_manifest.load_ownership) is skipped.
     Overlapping collectors dedupe by resolved path WITHOUT dropping errors (the block-registered site
     pages are already scanned as visible text by collector 1 and are not re-scanned as raw HTML here).
     A registered surface that is absent, unwalkable, or of the wrong type is exit 2, never a silent skip;
     a UTF-8 decode failure on a scanned file is a finding.

BEST-EFFORT, NOT COMPLETE. This lint is a compensating control, a hand-maintained DENY-LIST of the
overclaim phrasings seen so far, now over the site HTML, the repo prose roster, and every
registry-enumerated textual generated output. It CANNOT catch every paraphrase: a novel wording that
dodges the vocabulary below will pass. The third-party title allowlist and the tight-adjacency denial guard
are maintainer-calibrated against the live surfaces. It is not a complete overclaim detector, and it does not
replace human review of public copy for honesty; it polices wording, not whether the underlying integrity
mechanism actually works. Grow the vocabulary when a new class is found; do not read a PASS as proof the
copy is free of overclaims.

Negation binds to the GUARANTEE PHRASE, not the whole clause (see NEGATOR / CLAUSE_BOUNDARY): a negator
marks a match honest only when it sits in the same window as the match, where the window begins after
the last sentence/clause punctuation OR contrastive conjunction before it. A fixed char window let a
negator in a prior sentence launder a fresh overclaim; a whole-clause window let a contrastive "but"
launder one too ("does not merely help but guarantees secure output"), because the "not" there negates
"help", not "guarantees". Cutting the window at "but"/"yet"/... binds the negation to the phrase it
actually modifies.

The vocabulary, and why each pattern is shaped the way it is (calibrated so the current softened site is
clean; a pattern that flagged a legitimate line would be too broad):

  - "ensures" / "guarantees" (incl. gerunds "ensuring"/"guaranteeing"): the bare guarantee verbs.
    NEGATION-AWARE: an honest in-clause negation ("does not guarantee that generated code is secure",
    "not a guarantee that the model is perfect") is not an overclaim and is skipped.
  - "always <verb>": flagged only in an efficacy collocation ("always catches/prevents/blocks/..."),
    never bare, because the site legitimately says "always apply", "always yours", "come first".
  - "cannot/never fails": the efficacy overclaim ("cannot fail", "never fails", "won't fail"); the
    site's many honest "never" uses ("never change anything quietly", "never friction") are untouched.
  - "makes ... impossible" / bare "impossible": claiming a class of error is made impossible. Bare
    "impossible" is negation-aware.
  - "so claims match their sources": the CAUSAL framing that promises the outcome. The site's honest
    "claims match their sources" (a definition of Accuracy) and "claims matched to their sources"
    ("guides toward") lack the "so", so only the promise form trips.
  - unconditional "works [adverb] with/across/... [adverb] all/every/any": universal-compatibility
    claims, tolerating an adverb before the preposition ("works seamlessly with every assistant") and
    after it ("works with absolutely every assistant").
  - "serves/supports/applies ... with/across/for ... all/every/any": the same universal-compatibility
    claim via serve/support/apply. Its preposition set omits "on"/"in" and its quantifier set omits
    "both"/"each", so honest "applies on both sides" and "apply to it. For each source" stay clean.
  - universal-RESULT paraphrase: an efficacy/coverage verb immediately governing "all"/"every"/"any"
    ("catches all mistakes", "catches any mistake", "eliminates all errors", "serves every assistant").
    Adjacency is required, so honest prose ("you stop trusting every suggestion") is clean.
  - CATEGORICAL claim (F-318b): an efficacy/coverage verb reject/deny/catch/block/confirm/cover
    immediately governing "all"/"each"/"every" ("Rejects all edits anywhere", "denies each schedule call",
    "confirms every target"). This closes the genericized categorical-overclaim class the universal-RESULT
    pattern above missed: that set omits the verbs reject/deny/confirm and the quantifier "each", so those
    genericized shapes passed. It rides the SITE surface set, so it polices the register page's own hand
    prose (the generator's EXPLAINER, page lead copy, and class legend) and any roadmap `pending`
    description rendered there, while the rule corpus and adapters legitimately carry all/every/each
    governance language. It is a PLAIN categorical scan: there is no bound-allowance heuristic (the earlier
    natural-language clause-parser that tried to clear an "honestly bounded" categorical claim was removed,
    F-330/round-7, because it kept yielding false clears codex could exploit). A categorical shape is kept
    OUT of the register's own hand prose by construction; a residue that legitimately uses categorical
    governance vocabulary is reworded at its manifest source to be marketing-clean, so nothing depends on a
    per-claim exemption. Adjacency is required, so honest prose with a word between the verb and the
    quantifier stays clean.
  - universal-SUBJECT "one standard serves every": a single-standard subject claiming it serves/covers/
    works all|every|any target. Requires the subject and a coverage verb, so "one standard your whole
    team can work to" and "one standard, three paths" stay clean.
  - "one standard across every|all assistant|team": the one-standard-across compat claim where the
    reach is carried by the preposition "across"/"for", not a verb, so the serves/applies patterns miss
    it ("share one standard across every assistant"). Requires "across"/"for" IMMEDIATELY after
    "standard", so the softened intent form ("one standard, intended to reach across the assistants it
    uses") stays clean because a comma, not "across", follows "standard".
  - "applied/used/enforced/run by every|all assistant|team": universal compatibility carried by a
    past-participle coverage verb plus "by" ("one definition applied by every assistant"), which the
    appl(y|ies) shape of the serves pattern does not reach. The softened "intended for every assistant"
    carries no such verb and stays clean.
  - subject-first "every|all assistant works to the same": the assistants themselves asserted to work
    to one standard ("every assistant on the team works to the same ordering"). Requires the FINITE
    "works" (present-tense assertion), so the softened intent form ("every assistant ... is meant to
    work to the same ordering") stays clean because it reads bare "work", not "works".
  - "portable across ...": the pack asserted portable across tools/assistants/toolchains as a verified
    property ("the guardrails are portable across tools"). Cross-assistant reach is not yet verified (the
    evidence page marks every platform pending), so the flat assertion overstates it. INTENT-guarded: the
    softened "designed/intended to be portable across ..." stays clean.
  - "across every|all assistant|team|tool": universal cross-target reach carried by the preposition
    "across ... every|all assistant(s)|team(s)|tool(s)|toolchain(s)", not tied to the "one standard"
    subject. INTENT-guarded, so "intended to reach across every assistant" stays clean; the required
    quantifier+noun keeps honest "across every surface a change touches" clean.
  - "working|works to the same ...": parties asserted to be working to one standard as a present fact
    ("a colleague on one assistant is working to the same rules as a colleague on another"), the
    present-continuous/finite form the subject-first pattern (which needs an all|every-assistant subject)
    misses. INTENT-guarded, and the softened intent form reads bare "work" ("is meant to work to the same
    rules"), so it is not "works|working" and stays clean; "works under the same rules as the session that
    spawned it" is "under", not "to", so the subagent mechanism claim stays clean.
  - "the same ... licen[cs]e": the ShareAlike imprecision. CC BY-SA's ShareAlike is same-or-later-or-BY-SA-
    compatible (LICENSE clause 3(b)(1): a CC license with the same License Elements, this version or later,
    or a BY-SA Compatible License), so a "the same ... licence" claim that omits any of those alternatives
    overstates it. This catches the bare absolute ("the same ShareAlike licence") AND the two-alternative
    form ("the same or a compatible ShareAlike licence", which drops "or later"), with "ShareAlike"
    optionally interposed and the verbs under/carry/come-back/reused. SHAREALIKE-guarded: clean only when
    the surrounding sentence names BOTH the later-version AND the BY-SA-compatible alternatives, so the
    decided softened form ("CC BY-SA 4.0 or later, or a BY-SA Compatible License", which drops "the same")
    does not even match, and the LICENSE's own full wording matches but is cleared.
  - "foolproof": a bare guarantee-of-perfection adjective.

The RELEASE-INTEGRITY vocabulary (VER-CORE 4.4a, runs on every surface) is, like the guarantee-flavoured
set above, a MAINTAINER-CALIBRATED BEST-EFFORT DENY-LIST: it enumerates the release-integrity overclaim
phrasings seen so far and CANNOT catch every novel paraphrase, so a wording that dodges the vocabulary
below passes and the list is grown when a new class is found. Each pattern, and why it is shaped the way it
is:

  - "tamper evidence/resistance/proof", "tamper detection", and "detects/is-detected/tampering-detection
    tampering": achieved-tense claims that the interim keyless manifest-plus-ROOT layer resists or detects
    tampering, which it does not (5.2: it detects accident, not tamper). RELEASE-guarded, so an honest
    tight-adjacency denial ("This layer does not provide tamper evidence") stays clean while a present-tense
    OR forward-promise assertion trips (there is no future-tense clearance). The CLAIM forms are evident/evidence/
    resistant/resistance/proof, the hyphen-or-space "tamper-detection"/"tamper detection", the noun
    "tampering detection", and a detect verb governing the attack-noun ("detects tampering", "tampering is
    detected"). A BARE attack-noun with no detect verb ("Dependency Tampering", "a hand-tampered registry")
    is deliberately NOT matched, so an honest control TITLE or attack description does not false-positive;
    the third-party title allowlist below is the forward-safety layer for a shipped title that carries a
    CLAIM-form phrase.
  - tamper PREVENTION/immunity: "cannot|can't be tampered (with)", "impossible to tamper", "tampering is|
    remains impossible", "resists|prevents tampering", "immune to tamper(ing)". The interim keyless layer
    detects accidental corruption, not deliberate modification, so a claim that tampering is prevented or
    impossible overstates it. Each form carries a prevention verb/modal ADJACENT to the tamper token, so the
    bare attack-noun ("Dependency Tampering", "a hand-tampered registry") carries none and stays clean.
    RELEASE-guarded, so an honest tight-adjacency denial ("does not prevent tampering") still clears. (The
    marketing "guaranteed"/"unbreakable" flavour stays WEB-PAGES-ONLY, handled by the SITE_PATTERNS
    "guarantees" entry, so governance prose on a non-site surface does not false-positive.)
  - "independent ... channel/anchor/reference", the reverse "verified ... channel ... independent of", AND
    the copula reverse "<channel|anchor|reference>(s) is/are/remains/stays independent of": an achieved claim
    of the independent integrity channel or anchor, in any word order. RELEASE-guarded. The
    verified-reverse order is keyed on an ACHIEVED-verification verb (verify/verified/...) and the copula
    reverse on a linking copula before "independent of", so the obligation prose that only DESCRIBES the
    standard as an adjectival post-modifier with no verb and no copula ("a digest published through an
    authenticated channel independent of artefact delivery" (SECI-release-integrity), "integrity rests on a
    ... digest published through a channel independent of the download" (RELEASING)) stays clean by
    construction, while the achieved copula assertions ("The integrity channel is independent of artefact
    delivery", "The integrity anchor is independent of release publication", "The channels are independent of
    the download") trip (honest forms pinned as NEGATIVEs so a future edit cannot regress them).
  - "releases are/were signed" / "releases are cryptographically signed" (an adverb may sit between the
    copula and "signed") / active "we|aiqt sign(s) releases ... minisign|cryptographic" / "signed with
    minisign" / "Minisign-signed" / "carry Minisign signatures": the stale keyed-signing claim D1's keyless
    decision retired (the corrected CLAUDE.md wording lands in the same change, 4.4d). The active form is
    minisign/cryptographic-scoped so honest prose that merely says "sign" stays clean. RELEASE-guarded.

RELEASE-INTEGRITY clearing is a TIGHT two-family ALLOWLIST bound to the matched banned term (VER-CORE 4.4).
There is NO future-tense clearance: a forward promise about tamper/signing/an independent anchor is itself
banned (D2), so it flags like a bald claim. The deny-list clears ONLY two constructions and FLAGS everything
else, so it is launder-free by construction and its false positives fall in the safe direction. A novel-but-
honest phrasing that misses the allowlist may flag by design. The two families:
  - a shipped third-party control TITLE (THIRD_PARTY_TITLES, enumerated from the live site/mappings.html)
    that WHOLLY CONTAINS the matched span clears it, so a framework/control title that itself carries the
    vocabulary is not read as an AIQT claim; a match that merely OVERLAPS or abuts the title ("Software
    Supply Chain Attacks & Dependency Tampering detection feature ...", where "detection" falls outside the
    title) or only co-occurs in the sentence does not clear (F-VC5-D);
  - a TIGHT-ADJACENCY negation that DIRECTLY negates the banned term (see _adjacent_denial_clears): within
    the pre-match clause (bounded by the last CLAUSE_BOUNDARY), the negator CLOSEST to the match is separated
    from the term by NO adverb, only an optional article/quantifier (a|an|any) OR an integrity verb
    (provide/offer/deliver/...) governing it, with NO be-copula between them. Any adverb there risks affirming
    a partial property ("not fully tamper-resistant" admits some, "not only tamper-resistant but also X"
    affirms it), so none is allowed. "This layer does not provide tamper evidence" and "is not tamper-resistant"
    clear; a negator on a DIFFERENT word or across an intervening subject and copula does NOT ("The
    not-expensive release is tamper-resistant", "No customers doubt that releases are tamper-resistant"), a
    negator in a prior clause is cut off by the boundary ("Deployment is not automatic; releases are
    tamper-resistant"), and a TRAILING negator never clears ("..., not merely checksum-protected" still
    asserts the tamper claim). A paired PREDICATE-FINAL rule closes the POST-match surface: the banned term
    must END the predicate (next non-space is end-of-string, a clause boundary, or a contrastive), so a
    scope-reversing qualifier after it FLAGS ("not tamper-resistant IN NAME ONLY; they resist real attacks"
    affirms substantive tamper resistance). This two-sided binding is what makes the deny-list launder-free:
    nothing clears a banned term unless a negator is right next to it, not itself negated, and the term ends
    the predicate.

CALIBRATION: the gate catches OUTCOME/RESULT guarantees, not accurate MECHANISM claims. A claim about
the instruction loading each turn ("AIQT is on for every turn", "applied to every response") is a
mechanism claim and is deliberately NOT matched (no "works", no efficacy verb governing all/every).

  gen: python3 tools/check_overclaim.py             scan the site, the repo prose roster, and every
                                                    registry-enumerated textual generated output
       python3 tools/check_overclaim.py --self-test  run the adversarial positive/negative/collector corpus

Exit 0 clean, 1 on any finding, 2 on a read error (unreadable/absent required surface, fail-closed).
"""
import html
import json
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
import gen_gensrc  # noqa: E402  build_registry: the in-memory gensrc recomputation (collector 3)
import gen_manifest  # noqa: E402  load_ownership: the [checkout].binary roster (collector 3 skip set)
import gen_enforceability  # noqa: E402  build_ledger: recompute the residual map for the source-side residue-cleanliness leg
import gen_enforcement_register  # noqa: E402  reuse ledger_index + load_roadmap to enumerate page-bound source strings
HTML_REL = gen_enforcement_register.HTML_REL  # single-sourced register page path (site/enforcement.html)

# Negation is CLAUSE-aware, not a fixed char window: a negator only marks a match honest when it sits
# in the SAME clause as the match. A fixed window let a negator in a PRIOR sentence launder a fresh
# overclaim (e.g. "AIQT does not sandbox anything. It guarantees secure output." would wrongly pass).
# The negator alternation drives NEGATOR (the general in-clause/pre-match negation guard for the site
# guarantee patterns) and the tight-adjacency denial rule (_adjacent_denial_clears), so both read the
# same negator set.
_NEGATOR_ALT = (
    r"not|no|never|cannot|can't|without|nor|neither|hardly|rarely|"
    r"n't|doesn't|don't|isn't|aren't|won't|wouldn't")
NEGATOR = re.compile(r"\b(?:" + _NEGATOR_ALT + r")\b", re.IGNORECASE)
# A be-form COPULA between the governing negator and the match means the negator governs a DIFFERENT
# predicate and the banned claim is a fresh copular assertion, so the negator does NOT launder it (the
# tight-adjacency denial rule _adjacent_denial_clears checks this): "The not-expensive release IS
# tamper-resistant" ("is" intervenes -> flags), "No customers doubt that releases ARE tamper-resistant"
# ("are" intervenes -> flags), while the honest disclaimer "does not provide tamper evidence" (no copula
# between "not" and the match) clears. "is not tamper-resistant" also clears: the copula sits BEFORE the
# negator, not between it and the match, so the negation binds directly to the property.
BE_COPULA = re.compile(r"\b(?:is|are|was|were|be|been|being)\b", re.IGNORECASE)
# TIGHT-ADJACENCY denial tail: the text BETWEEN the governing negator and the match, with no be-copula in
# it. NO ADVERB is permitted between the negator and the banned term, by construction: ANY adverb there
# risks affirming a PARTIAL property ("not fully tamper-resistant" admits some; "not only|merely|exclusively|
# principally tamper-resistant but also X" affirms it), and the set of exclusive-focus/degree adverbs is
# open-ended, so none is allowed rather than blocklisting them. Only two forms clear: (1) an optional
# article/quantifier (a|an|any) then the term directly, the negator negating the property itself ("is not
# tamper-resistant", "not a tamper-evident release"); or (2) an INTEGRITY VERB governing the banned term,
# optionally with a SINGLE article/quantifier (a|an|any) between the verb and the term ("does not provide
# tamper evidence", "does not offer any tamper evidence", "did not deliver an independent anchor"). The
# object slot is a CLOSED allowance, not an arbitrary word run: an adverb or focus word there ("does not
# provide ONLY tamper evidence; it also provides X") affirms the property IS provided, so it is not an
# article and keeps FLAGGING. It is anchored end to end, so a NON-integrity verb or a new subject breaks it
# ("We do not doubt that releases provide tamper evidence"; "The not-expensive release is tamper-resistant"),
# and any adverbial correlative FLAGS. "not cryptographically signed" clears WITHOUT a filler: the
# bare-signing match span STARTS at "cryptographically", so the tail is empty (and a trailing "but
# checksum-protected" is after the match, so a genuine contrastive denial still clears).
INTEGRITY_NEG_TAIL = re.compile(
    r"^\s*(?:"
    r"(?:a|an|any)\s+"
    r"|(?:provide|provides|provided|offer|offers|offered|deliver|delivers|delivered|give|gives|given|"
    r"make|makes|made|guarantee|guarantees|guaranteed|ensure|ensures|ensured|have|has|had)\s+"
    r"(?:(?:a|an|any)\s+)?"
    r")?$", re.IGNORECASE)
# Sentence and clause punctuation ends the clause a match belongs to. A CONTRASTIVE conjunction
# (but/yet/however/...) also ends the negation window: it flips polarity, so a negator before it does
# NOT scope over a guarantee after it. Binding negation to the guarantee-phrase segment this way makes
# "does not merely help but guarantees secure output" flag, where a whole-clause negation window let
# the earlier "not" (which negates "help", not "guarantees") launder the overclaim.
CLAUSE_BOUNDARY = re.compile(
    r"[.!?;:,]|\b(?:but|yet|however|nonetheless|nevertheless|rather|though|although|whereas)\b",
    re.IGNORECASE)
# An INTENT HEDGE marks a COMPATIBILITY claim honest: the decided softening frames cross-assistant reach
# as an aim, not a verified result ("intended/designed to be portable across ...", "is meant to work to
# the same rules", "the intent, not a verified result yet"). A compat match is skipped when an intent
# hedge (or a negator) sits in its clause window, so the softened forms stay clean while a bare present-
# tense assertion of reach still trips. The word "intent" is included so "the intent, not a verified
# result" reads as a hedge. It is clause-scoped exactly like NEGATOR: a hedge in a PRIOR clause does not
# launder a fresh assertion (teams.html says the standard is "intended to reach across ..." and then, in a
# separate clause after the comma+"so", asserts a colleague "is working to the same rules" as a verified
# fact; the earlier hedge does not scope over that later clause, so the assertion is what must soften).
INTENT_HEDGE = re.compile(
    r"\b(?:intend(?:s|ed|ing)?|design(?:s|ed|ing)?|meant|aim(?:s|ed|ing)?|aspir(?:e|es|ed|ing)?|intent)\b",
    re.IGNORECASE)
# The SHAREALIKE clean form names the full permitted set from LICENSE clause 3(b)(1): a CC license with
# the same License Elements, THIS VERSION OR LATER, OR a BY-SA Compatible License. A "the same ... licence"
# claim is honest only when BOTH the later-version alternative AND the BY-SA-compatible alternative are
# stated in the same sentence; a two-alternative form ("the same or a compatible ShareAlike licence",
# missing "or later") and a bare absolute ("the same ShareAlike licence") both understate the permitted
# set and trip. These are searched over the whole SENTENCE around the match, not the pre-match clause
# window, because the alternatives follow the licence noun ("... licence, this version or later, or a
# BY-SA Compatible License").
LATER_ALT = re.compile(r"\blater\b", re.IGNORECASE)
COMPAT_ALT = re.compile(r"\bcompatible\b", re.IGNORECASE)

# PREDICATE-FINAL boundary for the adjacent-denial (paired with the pre-match surface): a denial clears only
# when the banned term ENDS the predicate, i.e. the first non-space AFTER the matched span is end-of-string,
# a HARD sentence/clause boundary (. ; ! ?), or a contrastive conjunction (but|yet|however|though|although|
# whereas|nor). A COMMA or COLON is deliberately NOT a terminator: either can introduce a CONTINUING
# appositive that reverses the denial ("not tamper-resistant, in name only; they resist deliberate
# modification"; "not tamper-resistant: in marketing terms, ..."), so a trailing comma/colon means NOT
# predicate-final -> FLAG. Any other trailing content is a scope-reversing qualifier ("in name only", "on
# paper only", "nominally", "in theory") or a continuing phrase that reverses the denial, so it too does NOT
# clear. This closes the POST-match surface structurally, without enumerating the qualifiers.
POSTMATCH_PREDICATE_FINAL = re.compile(
    r"\s*(?:[.;!?]|(?:but|yet|however|though|although|whereas|nor)\b|$)", re.IGNORECASE)

# Shipped third-party control TITLES that legitimately carry release-integrity vocabulary, enumerated
# from the live site/mappings.html at build (never guessed). RECONCILED 2026-08-25: mappings.html carries
# framework/control titles with the vocabulary stems, but only ONE carries a tamper stem: the OWASP MCP
# Top 10 control "MCP04: Software Supply Chain Attacks & Dependency Tampering" (the visible-text form, with
# the HTML "&amp;" entity unescaped to "&"). Under the calibrated narrow CLAIM-form tamper patterns it does
# NOT itself trip (the patterns match tamper-evident/evidence/resistance/proof/detection, not the
# attack-noun "Tampering"), so the live surfaces already pass without needing this clear; the allowlist is
# the forward-safety layer required by 4.4a, clearing a CLAIM-form hit whose span is WHOLLY CONTAINED within
# this exact shipped title (a hit that merely overlaps or abuts the title, or only co-occurs in the sentence,
# does not clear, F-VC5-D). (The SA-11(3) title "Independent Verification of Assessment Plans and Evidence"
# carries "Independent" but no channel/anchor/reference noun, so it does not match the independent-channel
# pattern and needs no entry.) An exact case-sensitive occurrence in the match's sentence that fully contains
# the matched span clears (_title_allowlisted).
THIRD_PARTY_TITLES = (
    "Software Supply Chain Attacks & Dependency Tampering",
)

# The hand-authored repo prose roster (collector 2): the spec's named prose surfaces that are NOT
# gensrc-registered generated outputs. DISCLOSURE.md/CHANGELOG.md/ROADMAP.md/CLAUDE.md are registered and
# arrive through collector 3; these are the hand-authored remainder, including the shipped starter file
# aiqt-barebones.md (in .aiqt/manifest.toml and in-scope for check_portability.py, but not gensrc-generated,
# so the overclaim gate reaches it only through this roster) and the gates-manifest SOURCE
# .aiqt/core/gates/manifest.toml (scanned as raw text so a banned overclaim in a gate RESIDUE string cannot
# escape; it is a source, not a gensrc target, so it too is reachable only here). Every path is REQUIRED
# (absent = exit 2). Nothing is dormant today, so this is a single declared constant, not idle dormant/armed
# machinery. NOTE: the gates manifest now carries THIS gate's own residue AND is a scanned surface, so that
# residue must itself stay clean under the gate (verified: it uses no banned release-integrity vocabulary).
REPO_PROSE_ROSTER = ("README.md", "SCOPE.md", "SYSTEM-HARDENING.md", "aiqt-barebones.md",
                     ".aiqt/core/gates/manifest.toml")

# (name, pattern, guard): guard is "" (none), "neg" (skip when a negator is in the pre-match clause
# window), "intent" (skip when a negator OR an intent hedge is in that window), "sharealike" (skip only
# when the later-version AND BY-SA-compatible alternatives are both named in the surrounding sentence), or
# "release" (skip when an allowlisted third-party control title WHOLLY CONTAINS the match, or a negator
# DIRECTLY negates the banned term by tight adjacency; see _guard_clears).
#
# SITE_PATTERNS: the guarantee-flavoured MARKETING deny-list. Calibrated for the public site copy and run
# on the SITE PAGES ONLY, because the rule corpus and generated adapters legitimately use "guarantee",
# "ensure", and "every" as governance language.
SITE_PATTERNS = [
    # guarantee/ensure incl. gerunds ("guaranteeing", "ensuring"): the bare guarantee verbs. An honest
    # in-clause negation ("does not guarantee that generated code is secure") is not an overclaim.
    ("ensures", re.compile(r"\bensur(?:e|es|ed|ing)\b", re.IGNORECASE), "neg"),
    ("guarantees", re.compile(r"\bguarantee(?:s|d|ing)?\b", re.IGNORECASE), "neg"),
    ("always <efficacy verb>", re.compile(
        r"\balways\s+(?:catch(?:es)?|prevent(?:s)?|block(?:s)?|stop(?:s)?|find(?:s)?|"
        r"detect(?:s)?|fix(?:es)?|secure(?:s)?|guarantee(?:s)?|ensure(?:s)?|work(?:s)?)\b",
        re.IGNORECASE), False),
    # cannot-fail is the efficacy overclaim itself; "cannot/never/will not/won't fail(s/ed/ing)".
    ("cannot/never fails", re.compile(
        r"\b(?:cannot|can(?:'|’)?t|will\s+not|won(?:'|’)?t|never)\s+fail(?:s|ed|ing)?\b",
        re.IGNORECASE), False),
    ("makes ... impossible", re.compile(
        r"\bmake[s]?\b[^.]{0,40}\bimpossible\b", re.IGNORECASE), ""),
    ("impossible", re.compile(r"\bimpossible\b", re.IGNORECASE), "neg"),
    ("so claims match their sources", re.compile(
        r"\bso\s+(?:that\s+)?claims?\s+match(?:es|ed)?\b", re.IGNORECASE), ""),
    # universal-COMPATIBILITY: "works with/across/... all|every|any". An adverb may sit between the verb
    # and the preposition ("works seamlessly with every assistant") AND after the preposition ("works
    # with absolutely every assistant"), so allow a short gap on both sides.
    ("unconditional works with/across", re.compile(
        r"\bworks?\b[^.]{0,30}?\b(?:with|across|on|for|in)\s+(?:\w+\s+){0,2}?"
        r"(?:all|every|any|each|both|everything|the\s+full\s+range)\b", re.IGNORECASE), ""),
    # universal-COMPATIBILITY via serve/support/apply: "serves/supports/applies ... with|across|for ...
    # all|every|any" (an adverb may follow the preposition: "applies across virtually all"). The
    # preposition set is narrower than the works pattern (no "on"/"in") and the quantifier set excludes
    # "both"/"each", so an honest "applies on both sides" and "apply to it. For each source" stay clean.
    ("serves/supports/applies across all", re.compile(
        r"\b(?:serves?|supports?|appl(?:y|ies))\b[^.]{0,30}?\b(?:with|across|for)\s+"
        r"(?:\w+\s+){0,2}?(?:all|every|any)\b", re.IGNORECASE), ""),
    # universal-RESULT paraphrase: an efficacy/coverage verb immediately governing "all"/"every"/"any"
    # ("catches all mistakes", "prevents every error", "catches any mistake", "eliminates all errors",
    # "serves every assistant"). Adjacency keeps honest prose clear (e.g. "you stop trusting every
    # suggestion" has a word between the verb and "every").
    ("universal result (verb + all/every/any)", re.compile(
        r"\b(?:catch(?:es)?|detect(?:s)?|prevent(?:s)?|block(?:s)?|find(?:s)?|fix(?:es)?|"
        r"stop(?:s)?|secure(?:s)?|eliminat(?:e|es)|serves?|supports?|covers?)\s+"
        r"(?:all|every|any)\b", re.IGNORECASE), ""),
    # CATEGORICAL claim: an efficacy/coverage verb (reject/deny/catch/block/confirm/cover) governing
    # all|each|every. This closes the genericized categorical-overclaim gap (F-318b): the universal-result
    # pattern above misses reject/deny/confirm and the quantifier "each", so a description such as "Rejects
    # all edits anywhere" or "denies each schedule call" was NOT caught. It is a PLAIN scan (guard ""): the
    # earlier bound-allowance heuristic that tried to clear an "honestly bounded" categorical claim was
    # removed in round 7 (it was a fragile natural-language clause-parser with exploitable false clears), so
    # ANY such categorical shape flags. Register-page hand prose and manifest residues are kept marketing-
    # clean at their source instead of relying on a per-claim exemption. Adjacency is required (the verb
    # IMMEDIATELY governs the quantifier), so honest prose with a word between the verb and the quantifier
    # ("rejects a write outside each slice") does not trip. It rides the SITE surface set, so the rule corpus
    # and adapters legitimately carry "all"/"every"/"each" governance language and it stays site-scoped.
    ("categorical claim (verb + all/each/every)", re.compile(
        r"\b(?:reject(?:s)?|den(?:y|ies)|catch(?:es)?|block(?:s)?|confirm(?:s)?|cover(?:s)?)\s+"
        r"(?:all|each|every)\b", re.IGNORECASE), ""),
    # universal-SUBJECT: a single-standard subject claiming it serves/covers/works all|every|any target
    # ("one standard serves every assistant"). Requires the "one standard/rule/instruction" subject and
    # an in-range coverage verb governing the quantifier, so honest prose that merely contains "one
    # standard" ("one standard your whole team can work to", "one standard, three paths") stays clean.
    ("universal-subject (one standard serves every)", re.compile(
        r"\bone\s+(?:standard|rule|instruction)\b[^.]{0,40}?"
        r"\b(?:serves?|covers?|fits?|works?|applies|supports?)\s+(?:all|every|any|each)\b",
        re.IGNORECASE), False),
    # one-standard-across: reach carried by "across"/"for" (not a verb), so the serves/applies patterns
    # miss it ("share one standard across every assistant"). "across"/"for" must sit IMMEDIATELY after
    # "standard", so the softened "one standard, intended to reach across the assistants it uses" (a
    # comma follows "standard") stays clean.
    ("one standard across every|all assistant/team", re.compile(
        r"\bone\s+(?:shared\s+)?standard\s+(?:across|for)\s+(?:\w+\s+){0,2}?"
        r"(?:all|every|any)\s+(?:assistant|team)\b", re.IGNORECASE), ""),
    # coverage carried by a past-participle verb plus "by" ("applied by every assistant"), outside the
    # appl(y|ies) shape of the serves pattern. The softened "intended for every assistant" carries no
    # such verb and stays clean.
    ("applied by every|all assistant/team", re.compile(
        r"\b(?:applied|used|enforced|run)\s+by\s+(?:all|every|any)\s+(?:assistant|team)\b",
        re.IGNORECASE), False),
    # subject-first: the assistants asserted to work to one standard ("every assistant on the team works
    # to the same ordering"). Requires the FINITE "works" (a present-tense assertion of fact), so the
    # softened intent form ("every assistant ... is meant to work to the same ordering", bare "work")
    # stays clean.
    ("universal-subject (every assistant works to the same)", re.compile(
        r"\b(?:all|every)\s+assistants?\b[^.]{0,30}?\bworks\s+to\s+the\s+same\b", re.IGNORECASE), ""),
    # COMPATIBILITY, "portable across ...": the pack asserted portable across tools/assistants/toolchains
    # as a verified property ("the guardrails are portable across tools"). CC BY-SA aside, cross-assistant
    # reach is not yet verified (the evidence page marks every platform pending), so the flat assertion
    # overstates it. INTENT-guarded: the softened "designed/intended to be portable across ..." carries a
    # hedge in-clause and stays clean, while the bare "is portable across ..." trips.
    ("portable across (compat)", re.compile(r"\bportable\s+across\b", re.IGNORECASE), "intent"),
    # COMPATIBILITY, "across every|all assistant|team|tool": universal cross-target reach carried by
    # "across ... every|all assistant(s)|team(s)|tool(s)|toolchain(s)", the reach not tied to the "one
    # standard" subject the earlier pattern needs. INTENT-guarded, so "intended to reach across every
    # assistant" stays clean; the quantifier+noun requirement keeps honest "across every surface a change
    # touches" clean (its noun is not an assistant/team/tool).
    ("across every|all assistant/team/tool (compat)", re.compile(
        r"\bacross\s+(?:\w+\s+){0,2}?(?:all|every)\s+(?:assistant|team|toolchain|tool)s?\b",
        re.IGNORECASE), "intent"),
    # COMPATIBILITY, "working/works to the same ...": parties asserted to be working to one standard as a
    # present fact ("a colleague on one assistant is working to the same rules as a colleague on another").
    # This catches the present-continuous/finite assertion the earlier subject-first pattern misses (that
    # one needs an "all|every assistant" subject and finite "works"). INTENT-guarded, and the softened
    # intent form ("is meant to work to the same rules") stays clean via the INTENT guard (a "meant"/
    # "intended" hedge in its clause window), NOT via the verb form: bare "work" is now caught too, so a
    # bare-"work" assertion with no hedge ("All assistants work to the same rules") trips. "works under
    # the same rules as the session that spawned it" is "under", not "to", so it stays clean.
    ("working/works to the same (compat)", re.compile(
        r"\b(?:work|works|working)\s+to\s+the\s+same\b", re.IGNORECASE), "intent"),
    # COMPATIBILITY, reach carried by "wherever/whichever ... assistant|model|tool": one/the-same standard
    # or rules asserted to apply the same wherever the work runs or whichever assistant/model/tool does it
    # ("one standard applied the same way ... whichever model does the reviewing", "the same rules apply
    # whichever assistant you use"). This universal cross-target reach is carried by "wherever"/"whichever"
    # (not by the "across ... all|every" the earlier compat patterns need), so those miss it. INTENT-guarded,
    # and the softened form frames the aim ("its intent is to reduce variation ...") and reads "which tool",
    # not "whichever", so it stays clean by both the hedge and the quantifier form.
    ("standard/rules ... wherever/whichever assistant/model/tool (compat)", re.compile(
        r"\b(?:one|a|the\s+same)\s+(?:shared\s+)?(?:standard|rules?)\b[^.]{0,50}?"
        r"\b(?:appl(?:y|ies|ied)|works?)\b[^.]{0,80}?\b(?:wherever|whichever)\b"
        r"[^.]{0,40}?\b(?:assistant|model|tool)s?\b", re.IGNORECASE), "intent"),
    # ShareAlike imprecision: a "the same ... licence" claim that does not name the full permitted set from
    # LICENSE 3(b)(1) (same License Elements, this version or later, OR a BY-SA Compatible License). This
    # catches the absolute ("under/carry the same ShareAlike licence") AND the two-alternative form ("the
    # same or a compatible ShareAlike licence", which drops "or later"), with "ShareAlike" optionally
    # interposed and any of the verbs under/carry/come-back/reused. SHAREALIKE-guarded: clean only when the
    # surrounding sentence names BOTH the later-version and BY-SA-compatible alternatives, so the correct
    # form ("CC BY-SA 4.0 or later, or a BY-SA Compatible License", which drops "the same") does not even
    # match, and the LICENSE's own "the same License Elements, this version or later, or a BY-SA Compatible
    # License" matches but is cleared by the guard.
    ("sharealike imprecision (the same licence)", re.compile(
        r"\b(?:the\s+)?same\b[^.]{0,40}?\blicen[cs]e\b", re.IGNORECASE), "sharealike"),
    # "foolproof": a bare guarantee-of-perfection adjective, an overclaim wherever it appears.
    ("foolproof", re.compile(r"\bfool-?proof\b", re.IGNORECASE), ""),
]

# RELEASE_PATTERNS: the release-integrity deny-list (VER-CORE 4.4a). Runs on EVERY scanned surface. Every
# entry is RELEASE-guarded: it ALWAYS flags an our-product claim-form unless an allowlisted third-party
# control title WHOLLY CONTAINS the match, or a negator DIRECTLY negates the banned term by tight adjacency
# (see _guard_clears "release"). There is no future-tense clearance: a forward promise about tamper/signing
# is itself banned. Like the guarantee-flavoured set, this vocabulary is a maintainer-calibrated best-effort
# deny-list: it cannot catch every novel paraphrase, so it is grown when a new class is found rather than
# read as complete.
RELEASE_PATTERNS = [
    # Achieved-tense tamper evidence/resistance/proof/detectability. The narrow CLAIM-form shape (evident/
    # evidence/resistant/resistance/proof/detectable) deliberately does NOT match the attack-noun
    # "tampering"/"tampered", so an honest control title ("Dependency Tampering") or attack description ("a
    # hand-tampered registry") is not flagged. "detectable" is the adjective sibling of the others ("the
    # release is tamper-detectable"), the property-claim form the "tamper detection" pattern below (which
    # needs a detect... noun/verb) does not reach. RELEASE-guarded: "This layer does not provide tamper
    # evidence" clears via the tight-adjacency denial, while a bald "The release is tamper-evident." flags.
    ("tamper evidence/resistance/proof (achieved)", re.compile(
        r"\btamper[- ]?(?:evident|evidence|resistant|resistance|proof|detectable)\b", re.IGNORECASE),
        "release"),
    # Achieved-tense "tamper detection". Requires "tamper" immediately followed (via a hyphen OR
    # whitespace, so the hyphenated "tamper-detection" is caught too, F-VC5-E) by a "detect..." form, so
    # the attack-noun "tampering" and "detects accident, not tamper" (detect BEFORE tamper) do not match.
    ("tamper detection (achieved)", re.compile(
        r"\btamper[-\s]+detect(?:ion|s|ed|ing)?\b", re.IGNORECASE), "release"),
    # Achieved-tense tampering-detection in the ACTIVE ("detects tampering", "release detects tampering",
    # with an optional adjective/determiner run (up to two words: any|all|the, "malicious", ...) between the
    # detect verb and "tampering" so "detects any tampering" and "detects malicious tampering" are caught),
    # the NOUN form ("provides tampering detection"), and the PASSIVE ("tampering
    # is detected", "tampering
    # can be detected") voice, where the attack-noun "tampering" is the SUBJECT/OBJECT of a detect claim.
    # This is a CLAIM to detect tampering, so unlike the bare attack-noun it is matched; it still needs a
    # detect verb or the "tampering detection" noun adjacent to "tampering", so an honest control title
    # ("Dependency Tampering") and an attack description carry neither and stay clean. The passive form
    # admits a capability/FUTURE modal ("can/could/may/might/will/shall be detected") and the roadmap "is|are
    # to be detected" form: the claim is BANNED REGARDLESS OF TENSE (future clearance was removed), so
    # "tampering will be detected" flags just like "tampering is detected". A genuine adjacent denial
    # ("tampering is not detected") carries no "is detected" adjacency and stays clean. RELEASE-guarded.
    ("tampering detection (achieved)", re.compile(
        r"\bdetect(?:s|ed|ing)?\s+(?:\w+\s+){0,2}?tampering\b|\btampering\s+detection\b|"
        r"\btampering\s+(?:is|are|was|were|gets?|(?:can|could|may|might|will|shall)\s+be"
        r"|(?:is|are|was|were)\s+to\s+be)\s+detected\b",
        re.IGNORECASE), "release"),
    # Tamper PREVENTION/immunity claims: an assertion that the pack RESISTS, PREVENTS, or makes tampering
    # IMPOSSIBLE (the interim keyless layer does none of these; it detects accidental corruption, not
    # deliberate modification). The CLAIM forms are "cannot|can't be tampered (with)", "impossible to
    # tamper", "tampering is|remains impossible", "resists|prevents tampering", and "immune to
    # tamper(ing)". Each carries a prevention verb/modal adjacent to the tamper token, so the BARE
    # attack-noun ("Dependency Tampering", "a hand-tampered registry") carries none and stays clean.
    # RELEASE-guarded, so an honest tight-adjacency denial ("does not prevent tampering") still clears.
    ("tamper prevention/immunity (achieved)", re.compile(
        r"\b(?:cannot|can(?:'|’)?t)\s+be\s+tampered\b"
        r"|\bimpossible\s+to\s+tamper\b"
        r"|\btampering\s+(?:is|are|remains?|stays?)\s+impossible\b"
        r"|\b(?:resists?|prevents?)\s+tampering\b"
        r"|\bimmune\s+to\s+tamper(?:ing)?\b", re.IGNORECASE), "release"),
    # Achieved-tense independent integrity channel/anchor/reference (the 5.6-deferred anchor). Requires the
    # word order "independent ... channel|anchor|reference" within two words, so "a channel independent of
    # artefact delivery" (SECI-release-integrity) does not match. RELEASE-guarded.
    ("independent channel/anchor/reference (achieved)", re.compile(
        r"\bindependent,?\s+(?:\w+[- ]){0,2}?(?:channel|anchor|reference)\b",
        re.IGNORECASE), "release"),
    # The REVERSE word order "verified ... channel ... independent of" (the SECI-release-integrity example
    # phrasing), which the "independent ... channel" pattern above misses. Keyed on an ACHIEVED-verification
    # verb (verify/verifies/verified/verifying, NOT the capability form "verifiable" or "verification"), so
    # the obligation prose that describes the standard rather than claiming it ("a digest published through
    # an authenticated channel independent of artefact delivery", verb "published"; "integrity rests on a
    # ... digest published through a channel independent of the download", verb "rests") stays clean.
    # RELEASE-guarded.
    ("verified via channel independent of (achieved)", re.compile(
        r"\bverif(?:y|ies|ied|ying)\b[^.]{0,40}?\bchannel\b[^.]{0,25}?\bindependent\b",
        re.IGNORECASE), "release"),
    # The COPULA reverse "<channel|anchor|reference>(s) is/are/remains/stays independent of ..." (the
    # achieved reverse form the verif-keyed pattern above misses because it carries no verification verb).
    # Keyed on a linking copula (with an optional adverb before "independent", so "channel remains fully
    # independent of ..." is caught) before "independent of", so the ACHIEVED assertions "The integrity
    # channel is independent of artefact delivery", "The integrity anchor is independent of release
    # publication", and "The channels are independent of the download" all trip, while the obligation prose
    # that only DESCRIBES the standard as an adjectival post-modifier with NO copula ("a channel independent
    # of artefact delivery" (SECI-release-integrity), "a channel independent of the download" (RELEASING))
    # carries no "is/are/remains/stays independent of" and stays clean. RELEASE-guarded.
    ("channel/anchor/reference is independent of (achieved)", re.compile(
        r"\b(?:channel|anchor|reference)s?\s+(?:is|are|remains?|stays?)\s+(?:\w+ly\s+)?independent\s+of\b",
        re.IGNORECASE), "release"),
    # The stale keyed-signing claim D1 retired, in the PASSIVE ("releases are/is/was/were/get signed", an
    # adverb may sit between the copula and "signed", so "release is cryptographically signed" and "release
    # was cryptographically signed" are caught), the ACTIVE present ("we|aiqt sign(s) ... releases ...", kept
    # minisign/cryptographic-scoped so it needs a minisign/cryptographic token nearby, catching "We sign
    # releases with minisign" and "AIQT signs each release automatically using minisign" while honest prose
    # that merely says "sign" stays clean), the PRE-VERBAL adverb form where the crypto scope sits BEFORE the
    # verb with a generic subject ("cryptographically sign(s) ... releases", so "We cryptographically sign
    # releases" and "AIQT cryptographically signs every release" are caught), plus "signed with minisign",
    # "Minisign-signed", "carry Minisign signatures", and the BARE "cryptographically signed" (with no
    # explicit "releases" subject; D1 bans the crypto-signing claim on any surface). RELEASE-guarded, so an
    # honest tight-adjacency denial ("not cryptographically signed") still clears. The corrected CLAUDE.md
    # wording (4.4d) landed in this rework, so this pattern's only prior hit is gone.
    ("releases are signed (stale signing claim)", re.compile(
        r"\breleases?\s+(?:are|is|was|were|get|gets)\s+(?:\w+ly\s+)?signed\b|\bsigned\s+with\s+minisign\b|"
        r"\bminisign[-\s]signed\b|\bminisign\s+signatures?\b|"
        r"\b(?:we|aiqt)\s+signs?\s+(?:\w+\s+){0,3}?releases?\b[^.]{0,40}?"
        r"\b(?:minisign|cryptographic(?:ally)?)\b|"
        r"\bcryptographically\s+signs?\s+(?:\w+\s+){0,3}?releases?\b|"
        r"\bcryptographically\s+signed\b",
        re.IGNORECASE), "release"),
]

SKIP_TEXT_TAGS = {"script", "style"}

# Block-level (and line-breaking) elements separate their text content: "<p>guarantees</p><p>secure</p>"
# is two visible phrases, not the word "guaranteessecure". Their boundaries emit a space so adjacent text
# nodes never fuse into a phantom token an overclaim could hide across (a real evasion the earlier
# "".join let through). Inline elements (b, em, a, span, code, ...) deliberately do NOT separate, so a
# phrase marked up mid-word ("<b>guar</b>antee") stays one token.
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "button", "caption", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "label", "legend", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "ul",
}


# The SEO/social snippets, meta[name=description] and meta[property=og:description], are attribute
# content that never renders in the page body, so the visible-text scan below does not see them; yet they
# are public-facing copy a search result or a shared link shows, and an overclaim there ships just as
# surely. They are the ONE deliberate exception to "an overclaim hidden in an attribute is not flagged":
# their content is collected and scanned with the same PATTERNS. No other attribute is scanned.
META_DESC_NAMES = {"description", "og:description"}


def _collapse(value):
    """Whitespace-collapse a string, the same normalization the visible-text scan applies, so a rendered
    residual quotation compares equal to the ledger's own residue regardless of interior spacing."""
    return re.sub(r"\s+", " ", value).strip()


class VisibleText(HTMLParser):
    """Accumulate visible text, dropping <script>/<style> bodies. Entities are converted (default).
    A block-element boundary emits a space so text from two separate blocks cannot fuse into one
    token; inline elements do not separate, so mid-word markup stays a single word. The
    meta[name=description] and meta[property=og:description] snippets are captured separately (see
    META_DESC_NAMES) so their public-facing copy is scanned too, though it never renders in the body."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.meta = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            key = a.get("name") or a.get("property")
            if key and key.lower() in META_DESC_NAMES and a.get("content"):
                self.meta.append(a["content"])
        if tag in SKIP_TEXT_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_startendtag(self, tag, attrs):
        # a self-closing void block element (e.g. <br/>, <hr/>) still separates
        if tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in SKIP_TEXT_TAGS and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_data(self, data):
        if self._skip == 0:
            self.chunks.append(data)

    def text(self):
        return re.sub(r"\s+", " ", "".join(self.chunks)).strip()


def _snippet(text, start, end):
    a = max(0, start - 25)
    b = min(len(text), end + 25)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def _clause_window(text, start):
    """The text from the start of the clause containing `start` up to `start`. The window begins after
    the last CLAUSE_BOUNDARY before the match, which is sentence/clause punctuation OR a contrastive
    conjunction, so a negator only counts when it binds to the guarantee phrase: never one that leaked
    in from a prior sentence, and never one that a 'but'/'yet' has flipped away from the match."""
    boundary = 0
    for m in CLAUSE_BOUNDARY.finditer(text, 0, start):
        boundary = m.end()
    return text[boundary:start]


def _sentence_window(text, start, end):
    """The whole sentence around the match: from the last sentence-ending punctuation (.!?) before the
    match to the next one after it. Used by the SHAREALIKE guard, whose exonerating alternatives ("this
    version or later, or a BY-SA Compatible License") follow the licence noun and so fall outside the
    pre-match clause window, and by the future guard's third-party title allowlist."""
    left = 0
    for m in re.finditer(r"[.!?]", text[:start]):
        left = m.end()
    right = re.search(r"[.!?]", text[end:])
    return text[left:end + right.start()] if right else text[left:]


def _adjacent_denial_clears(text, start, end):
    """True when a negator DIRECTLY negates the banned term (tight adjacency), the ONLY negation clearance in
    the simplified deny-list. Within the pre-match clause (bounded by the last CLAUSE_BOUNDARY before the
    match), the negator CLOSEST to the match must be separated from the term by NO adverb, only an optional
    article/quantifier (a|an|any) OR an integrity verb (provide/offer/deliver/...) governing it, with NO
    be-copula between them (INTEGRITY_NEG_TAIL, anchored). So "does not provide tamper evidence" and "is not
    tamper-resistant" clear, while ANY adverb between the negator and the term keeps FLAGGING: "not only|
    merely|exclusively|principally|fully tamper-resistant but also X" affirms a partial property and is not a
    denial. A negator on a different word or across an intervening subject and copula does NOT clear either
    ("The not-expensive release is tamper-resistant", "No customers doubt that releases are tamper-resistant"),
    a negator in a prior clause is cut off by the CLAUSE_BOUNDARY, and a TRAILING negator (after the term)
    never clears ("Releases are tamper-resistant, not merely checksum-protected" flags). "not cryptographically
    signed" clears without a filler because the bare-signing match starts at "cryptographically" (empty tail).
    A CLOSED POLARITY check then rejects a DOUBLE negation: if the clearing negator is itself governed by an
    earlier negator in the same clause window, the parity is even (affirmation) and it does NOT clear ("not
    without tamper evidence" / "never without an independent anchor" affirm the property and keep FLAGGING).
    This tight binding is what makes the deny-list launder-free: nothing clears a banned term unless a negator
    is right next to it and not itself negated."""
    # PREDICATE-FINAL (post-match surface): the banned term must END the predicate. If the first non-space
    # after the match is not end-of-string, a clause boundary, or a contrastive, a trailing scope-reversing
    # qualifier ("in name only", "on paper only", "nominally") or a continuing phrase reverses the denial, so
    # it does NOT clear. "not tamper-resistant in name only; they resist real attacks" affirms and FLAGS.
    if not POSTMATCH_PREDICATE_FINAL.match(text, end):
        return False
    left = 0
    for m in CLAUSE_BOUNDARY.finditer(text, 0, start):
        left = m.end()
    window = text[left:start]
    neg = None
    for m in NEGATOR.finditer(window):
        neg = m  # the negator CLOSEST to the match governs
    if neg is None:
        return False
    tail = window[neg.end():]  # text between the governing negator and the banned term
    if BE_COPULA.search(tail) or not INTEGRITY_NEG_TAIL.match(tail):
        return False
    # CLOSED POLARITY: the clearing negator must not itself be governed by an EARLIER negator in the same
    # clause window. "not without tamper evidence" / "never without an independent anchor" are DOUBLE
    # negations (even parity) that AFFIRM the property, so they must NOT clear; a single negation ("without
    # tamper evidence", "does not provide tamper evidence") has no earlier in-clause negator and still clears.
    return not NEGATOR.search(window[:neg.start()])


def _title_allowlisted(text, start, end):
    """True when a shipped third-party control title occurs verbatim (exact, case-sensitive) in the
    match's sentence AND the ENTIRE matched span [start, end) is CONTAINED within that title occurrence
    (t_start <= start AND end <= t_end), so a claim-form phrase that is WHOLLY PART of the title is not read
    as an AIQT claim. A match that merely OVERLAPS or abuts the title is NOT cleared: "The Software Supply
    Chain Attacks & Dependency Tampering detection feature verifies every release" has "Tampering detection"
    straddling the title's "Tampering" tail and the word "detection" outside it, so the claim is the pack's,
    not the title's, and flags. Mere sentence co-occurrence is likewise not enough (F-VC5-D): a title that
    only shares the sentence with an unrelated claim elsewhere ("Software Supply Chain Attacks & Dependency
    Tampering is mapped, and our releases are tamper-evident") does not clear that claim. The sentence bound
    (last .!? before to first after the match) confines the search; the containment test binds the clear to
    a phrase fully inside the title itself."""
    left = 0
    for m in re.finditer(r"[.!?]", text[:start]):
        left = m.end()
    right = re.search(r"[.!?]", text[end:])
    right_abs = end + right.start() if right else len(text)
    for title in THIRD_PARTY_TITLES:
        pos = text.find(title, left)
        while pos != -1 and pos < right_abs:
            t_start, t_end = pos, pos + len(title)
            if t_start <= start and end <= t_end:  # the match is WHOLLY contained in the title span
                return True
            pos = text.find(title, pos + 1)
    return False


def _guard_clears(guard, text, m):
    """True when the pattern's guard exonerates this match. "neg": an in-clause negator. "intent": an
    in-clause negator OR intent hedge (the compat softening frames reach as an aim). "sharealike": the
    surrounding sentence names BOTH the later-version and the BY-SA-compatible alternatives, the full
    permitted set from LICENSE 3(b)(1). "release" (release-integrity): a shipped third-party control TITLE
    that WHOLLY CONTAINS the matched span, OR a negator that DIRECTLY negates the banned term by tight
    adjacency (_adjacent_denial_clears). There is no future-tense clearance, and there is no bound-allowance
    (the categorical pattern is a plain scan, guard ""). "" never clears."""
    if guard == "neg":
        return bool(NEGATOR.search(_clause_window(text, m.start())))
    if guard == "intent":
        window = _clause_window(text, m.start())
        return bool(NEGATOR.search(window) or INTENT_HEDGE.search(window))
    if guard == "sharealike":
        window = _sentence_window(text, m.start(), m.end())
        return bool(LATER_ALT.search(window) and COMPAT_ALT.search(window))
    if guard == "release":
        if _title_allowlisted(text, m.start(), m.end()):
            return True
        return _adjacent_denial_clears(text, m.start(), m.end())
    return False


def _scan_with(text, patterns):
    """Return (pattern_name, snippet) findings for one pattern set over one text, honouring each pattern's
    guard. `scan` uses this to run the release-integrity set alone on generated surfaces and both sets on
    the site pages; the source-side residue leg uses the same guard-honouring loop over each residue."""
    findings = []
    for name, pat, guard in patterns:
        for m in pat.finditer(text):
            if _guard_clears(guard, text, m):
                continue
            findings.append((name, _snippet(text, m.start(), m.end())))
    return findings


def scan(text, site=True):
    """Return a list of (pattern_name, snippet) overclaim findings in one surface's text. RELEASE_PATTERNS
    (release-integrity) always run; the guarantee-flavoured SITE_PATTERNS run only when `site` is True (the
    site pages), because the rule corpus and generated adapters legitimately carry that governance
    vocabulary."""
    return _scan_with(text, (SITE_PATTERNS + RELEASE_PATTERNS) if site else RELEASE_PATTERNS)


# FIX 4 (GER-1): the register page's STATIC asset closure. The register page loads shared chrome CSS/JS via
# docs/_shell.html's <link rel="stylesheet"> and <script src>, whose bodies the visible-text collector never
# sees (VisibleText drops <style>/<script>, and the external assets are separate files collector 1 does not
# read). A CSS content: string or a static marketing string in that closure would render or ship marketing no
# page scan catches, so the closure is scanned here for the marketing patterns AND for CSS content: string
# injection. This is a STATIC string scan; the runtime-JS residual is disclosed on _scan_asset_closure.
_STYLE_BODY_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_HREF_RE = re.compile(r'href\s*=\s*"([^"]*)"', re.IGNORECASE)
_ATTR_SRC_RE = re.compile(r'src\s*=\s*"([^"]*)"', re.IGNORECASE)
_ATTR_REL_RE = re.compile(r'rel\s*=\s*"([^"]*)"', re.IGNORECASE)
# A CSS content: DECLARATION (the PROPERTY, not the 'content' suffix of justify-content / align-content: the
# (?<![\w-]) lookbehind refuses a preceding word char or hyphen), and its value up to the ; or } that ends it.
_CSS_CONTENT_RE = re.compile(r"(?<![\w-])content\s*:\s*([^;}]*)", re.IGNORECASE)
# A CSS string literal (single- or double-quoted, backslash escapes honoured) inside a content: value.
_CSS_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"' + r"|'((?:[^'\\]|\\.)*)'")


# --- source-side residue-cleanliness leg (GER-1 / round 7) --------------------------------------------
# The enforcement register renders each mechanism's technical-limits residual VERBATIM from the ledger,
# inside a `<blockquote class="ledger-residual" data-mech="<ref>">`. Earlier rounds exempted those blocks
# from the marketing scan on the page (a verified-quotation carve-out); that approach was REFUTED as
# fundamentally fragile (a bound-allowance hole that suppressed every marketing pattern, invisible-Unicode
# laundering that passes a source scan yet renders as marketing, and static-DOM-vs-rendered fidelity gaps),
# so it was removed.
# _page_bound_sources recomputes the residues, ids, AND pending descriptions IN MEMORY from the manifests
# and roadmap (guard-input-soundness); _scan_page_bound_sources then runs ONE shared reject over every such
# string, rejecting any character outside the printable-plus-ASCII-whitespace allowlist on the RAW bytes
# (before any collapse) and scanning a collapsed copy with the PLAIN marketing patterns, so no page-bound
# channel can be missed and no invisible or non-ASCII laundering can reach the page through any of them.


def _page_bound_sources(root):
    """Every verbatim source string the enforcement register renders, recomputed IN MEMORY from the
    manifests and the roadmap (guard-input-soundness), as (channel, key, raw) tuples: each mechanism's RAW
    ledger residue (channel "residue") and its claim-bearing reference id ("id"), and each pending rule's
    roadmap description ("description"). These are exactly the strings the generator emits verbatim into the
    page (render_html / render_md), so scanning them at source is what keeps the page clean by construction.
    The residue is the RAW string (NOT collapsed) so the invisible-Unicode reject downstream judges the exact
    bytes the page ships; a non-ASCII space would otherwise be collapsed to an ASCII space before the reject
    (FIX 2). Reuses the register generator's OWN validated ledger index and roadmap loader, so the channel
    set here cannot fork from what the register renders. build_ledger / load_roadmap raise ValueError/OSError
    on a malformed or unreadable input, which the caller lets propagate to a fail-closed exit 2."""
    ledger = json.loads(gen_enforceability.build_ledger(root))
    by_cid, controls, linkage = gen_enforcement_register.ledger_index(ledger)
    roadmap = gen_enforcement_register.load_roadmap(
        root / gen_enforcement_register.ROADMAP_REL, set(by_cid), linkage)
    sources = []
    for ref, ctrl in sorted(controls.items()):
        sources.append(("residue", ref, ctrl["residue"]))
        sources.append(("id", ref, ref.split(":", 1)[-1].replace("-", " ")))
    for cid, row in sorted(roadmap.items()):
        if row["status"] == "pending" and row["description"]:
            sources.append(("description", cid, row["description"]))
    return sources


def _first_invisible(text):
    """Return (index, char) of the first character in `text` that is NOT printable ASCII or one of the four
    explicit ASCII whitespace characters (space, tab, newline, carriage return), else None. This is a
    printable-ASCII allowlist over the RAW string, stricter than the earlier category denylist and than a
    Unicode letter/number/punctuation/symbol allowlist. A page-bound source string (a mechanism residue, a
    reference id, a pending description) is house-authored technical English and is pure printable ASCII, so
    only 0x21-0x7E plus the four ASCII whitespace bytes are permitted; every other codepoint, whatever its
    Unicode category, is rejected on the exact raw bytes the page ships. This closes, by construction, the
    whole laundering class in one predicate: an INVISIBLE character (a zero-width space U+200B, a combining
    grapheme joiner U+034F, a variation selector U+FE00-FE0F, or a non-ASCII space such as U+200A HAIR SPACE)
    reads as a word break to the marketing regex yet renders as the intact marketing word, AND a CONFUSABLE
    HOMOGLYPH (a Cyrillic or Greek lookalike letter such as U+0430 that renders as an ASCII letter but evades
    the ASCII marketing regex) are BOTH non-ASCII and so refused here. No normalization is applied: the check
    is on the raw bytes, so a decomposed accent, an invisible mark, a non-ASCII space, and a homoglyph are all
    rejected without any collapse or NFC step that could disagree with what the page emits. DISCLOSED SCOPE: a
    legitimately non-ASCII residue would be rejected; this is by design for the pack's ASCII technical corpus,
    and a maintainer introducing non-ASCII source must widen this allowlist deliberately."""
    for i, ch in enumerate(text):
        if ch in (" ", "\t", "\n", "\r"):
            continue
        if 0x21 <= ord(ch) <= 0x7E:
            continue
        return (i, ch)
    return None


def _scan_page_bound_sources(sources):
    """Source-side cleanliness leg (GER-1). Every page-bound verbatim source string (each mechanism residue
    and reference id, and each pending description) must (a) carry NO character outside the printable-plus-
    ASCII-whitespace allowlist and (b) pass the PLAIN marketing patterns. ONE shared reject runs over EVERY
    channel, so no page-bound channel can be missed or diverge (FIX 3): a residue, a claim-bearing id, and a
    pending description are held to the identical bar. The invisible/allowlist reject runs on the RAW string,
    BEFORE any whitespace collapse (FIX 2), so the exact bytes the page ships are what is judged; the
    marketing scan runs on a whitespace-collapsed copy for phrase-matching robustness (its guards are
    honoured, and there is NO bound-allowance heuristic, so an 'honestly bounded' categorical or guarantee
    shape flags at source and is reworded there). The id's kebab body is scanned hyphens-as-spaces, so a
    hyphenated claim id (gate:catch-all-secrets -> "catch all secrets") fails at source even though the page
    render would not match a space-requiring pattern."""
    findings = []
    for channel, key, raw in sources:
        bad = _first_invisible(raw)
        if bad is not None:
            i, ch = bad
            findings.append("{} source [{}]: invisible or disallowed character U+{:04X} at index {}"
                            .format(channel, key, ord(ch), i))
        collapsed = _collapse(raw)
        for name, pat, guard in SITE_PATTERNS:
            for m in pat.finditer(collapsed):
                if _guard_clears(guard, collapsed, m):
                    continue
                findings.append("{} source [{}]: overclaim [{}] -> {}".format(
                    channel, key, name, _snippet(collapsed, m.start(), m.end())))
    return findings


def _scan_css_content_strings(css, where, findings):
    """Extract the STRING LITERALS from every CSS content: declaration in `css` and marketing-scan each, so a
    `content: " ... marketing ... "` injection (text a browser renders through ::before/::after but no HTML
    scan sees) is caught. Only the content PROPERTY is matched, never the 'content' tail of justify-content /
    align-content (the (?<![\\w-]) lookbehind). A fully hex-escaped payload (content: "\\...") is a DISCLOSED
    residual: decoding CSS escapes is out of scope for this static string scan (see _scan_asset_closure)."""
    for decl in _CSS_CONTENT_RE.finditer(css):
        for sm in _CSS_STRING_RE.finditer(decl.group(1)):
            literal = sm.group(1) if sm.group(1) is not None else sm.group(2)
            for name, snip in scan(literal, site=True):
                findings.append("{}: CSS content injection [{}] -> {}".format(where, name, snip))


def _closure_local_asset(url):
    """The repo-relative path under site/ for a same-origin CSS/JS asset URL, or None for an off-site or
    non-local reference (an absolute http(s) URL, a protocol-relative //host, a data:/mailto: URL). The site
    is served with site/ as web root, so a root-absolute "/styles.css?v=1" maps to site/styles.css; the query
    and fragment are stripped. A '..' segment is refused (returns None) so the closure can never resolve
    outside site/. An off-site URL returning None is the DISCLOSED runtime/off-origin boundary, not a failure:
    only same-origin static assets are in a static gate's reach."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    if not path or path.startswith(("http://", "https://", "//", "data:", "mailto:")):
        return None
    rel = path.lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    return rel


def _scan_asset_closure(root):
    """Scan the enforcement register page's STATIC asset closure for marketing overclaims (FIX 4, GER-1): the
    page's own inline <style>/<script> bodies (which the visible-text collector drops), plus the same-origin
    CSS/JS the page links, for the marketing patterns AND for CSS content: string injection. A referenced
    LOCAL asset that cannot be read is a fail-closed error (_FailClosed -> exit 2), never a silent skip
    (check-fails-closed-on-unreadable).

    DISCLOSED IRREDUCIBLE RESIDUAL (honest, not pretended closed): this is a STATIC string scan. Runtime-JS
    DOM construction of marketing text (theme.js building strings at run time) is beyond a static gate, and an
    ENCODED payload (for example a base64 or fully CSS-hex-escaped string) is likewise beyond it. Both are
    scoped OUT here as a chrome-integrity / code-review concern: the CSS/JS are hand-authored, reviewed,
    version-controlled SHARED chrome, not generated from the ledger, so their integrity rests on code review,
    not on this gate. An off-site (cross-origin) asset URL is out of a static gate's reach for the same
    reason and is not fetched."""
    findings = []
    page_path = root / HTML_REL
    try:
        page = page_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _FailClosed("register page {} is unreadable ({})".format(HTML_REL, exc))
    assets = set()
    # (a) inline <style> bodies: marketing scan + content: string injection.
    for m in _STYLE_BODY_RE.finditer(page):
        body = m.group(1)
        for name, snip in scan(body, site=True):
            findings.append("{} inline <style>: overclaim [{}] -> {}".format(HTML_REL, name, snip))
        _scan_css_content_strings(body, "{} inline <style>".format(HTML_REL), findings)
    # (b) <script> tags: an external src is a same-origin asset to scan; an inline body is scanned as text.
    for m in _SCRIPT_TAG_RE.finditer(page):
        attrs, body = m.group(1), m.group(2)
        src = _ATTR_SRC_RE.search(attrs)
        if src:
            rel = _closure_local_asset(src.group(1))
            if rel:
                assets.add(rel)
            continue
        for name, snip in scan(body, site=True):
            findings.append("{} inline <script>: overclaim [{}] -> {}".format(HTML_REL, name, snip))
    # (c) same-origin stylesheets the page links.
    for m in _LINK_TAG_RE.finditer(page):
        tag = m.group(0)
        rel_attr = _ATTR_REL_RE.search(tag)
        href = _ATTR_HREF_RE.search(tag)
        if rel_attr and "stylesheet" in rel_attr.group(1).lower() and href:
            rel = _closure_local_asset(href.group(1))
            if rel:
                assets.add(rel)
    # (d) each resolved same-origin asset: whole-text marketing scan, plus content: injection for CSS.
    for rel in sorted(assets):
        asset_path = root / "site" / rel
        try:
            text = asset_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise _FailClosed("register asset site/{} is absent or unreadable ({})".format(rel, exc))
        where = "site/{}".format(rel)
        for name, snip in scan(text, site=True):
            findings.append("{}: overclaim [{}] -> {}".format(where, name, snip))
        if rel.endswith(".css"):
            _scan_css_content_strings(text, where, findings)
    return findings


class _FailClosed(Exception):
    """A required surface is absent, unwalkable, or of the wrong type; the caller maps this to exit 2."""


def _scan_surface(path, rel, site, findings):
    """Read a required non-HTML surface as UTF-8 and scan it. A UTF-8 decode failure is a FINDING (the
    surface exists but is unreadable as text); any other OSError propagates for the caller to fail closed."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        findings.append("{}: could not read as UTF-8".format(rel))
        return
    for name, snip in scan(text, site=site):
        findings.append("{}: overclaim [{}] -> {}".format(rel, name, snip))


def _collect(root, registry, binary_set):
    """Run the three surface collectors against `root`, returning the list of finding strings. Raises
    _FailClosed on an absent/unwalkable/wrong-type required surface (caller -> exit 2); an OSError from a
    fail-closed walk or read likewise propagates. `registry` is the list of gensrc entries (target/kind/
    ...); `binary_set` is the [checkout].binary roster to skip. A resolved path scanned by an earlier
    collector is not re-scanned by a later one (dedupe without dropping errors)."""
    findings = []
    scanned = set()

    # Collector 1: site/*.html visible text + meta (SITE_PATTERNS + RELEASE_PATTERNS). site/ is REQUIRED.
    site = root / "site"
    if not site.is_dir():
        raise _FailClosed("site/ is a required surface but is absent or not a directory")
    for f in sorted(walk_files(site, suffixes={".html"})):
        rel = f.relative_to(root)
        scanned.add(f.resolve())
        try:
            raw = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append("{}: could not read as UTF-8".format(rel))
            continue
        parser = VisibleText()
        try:
            parser.feed(raw)
        except (ValueError, AssertionError):
            findings.append("{}: could not parse as HTML".format(rel))
            continue
        # The enforcement register page is scanned exactly like any other site page (no carve-out): its
        # verbatim ledger-residual blocks are just visible text, and the residues are kept marketing-clean
        # at their source by _scan_residue_marketing (called from main), so the plain scan below is enough.
        for name, snip in scan(parser.text(), site=True):
            findings.append("{}: overclaim [{}] -> {}".format(rel, name, snip))
        for meta in parser.meta:
            for name, snip in scan(meta, site=True):
                findings.append("{} (meta): overclaim [{}] -> {}".format(rel, name, snip))

    # Collector 2: the hand-authored repo prose roster (RELEASE_PATTERNS). Every path REQUIRED.
    for name in REPO_PROSE_ROSTER:
        p = root / name
        if not p.is_file():
            raise _FailClosed("required repo-prose surface {} is absent".format(name))
        scanned.add(p.resolve())
        _scan_surface(p, p.relative_to(root), False, findings)

    # Collector 3: every textual generated output enumerated from the gensrc registry (RELEASE_PATTERNS).
    for entry in registry:
        target = entry["target"]
        if target in binary_set:  # a binary generated artefact carries no scannable prose
            continue
        p = root / target
        if entry["kind"] == "tree":
            if not p.is_dir():
                raise _FailClosed("registered tree {} is absent or not a directory".format(target))
            members = sorted(walk_files(p))
        else:  # "file" or "block": the whole rendered file, block generators included (4.4c)
            if not p.is_file():
                raise _FailClosed("registered output {} is absent or not a file".format(target))
            members = [p]
        for f in members:
            resolved = f.resolve()
            if resolved in scanned:  # already scanned by an earlier collector (e.g. a site page)
                continue
            scanned.add(resolved)
            rel = f.relative_to(root)
            if str(rel) in binary_set:  # a binary member inside a registered tree
                continue
            _scan_surface(f, rel, False, findings)

    return findings


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    root = Path(__file__).resolve().parents[1]
    try:
        registry = json.loads(gen_gensrc.build_registry(root))["generated"]
        _, _, _, binary_set = gen_manifest.load_ownership(root)
        findings = _collect(root, registry, binary_set)
        # Source-side cleanliness leg: the plain marketing scan plus the printable-plus-ASCII-whitespace
        # allowlist reject, over EVERY page-bound verbatim source string the register renders (each mechanism
        # residue and reference id, and each pending description), so none can launder an overclaim onto the
        # page. build_ledger / load_roadmap raise on a bad input -> fail-closed exit 2.
        findings += _scan_page_bound_sources(_page_bound_sources(root))
        # Static asset-closure leg: the register page's inline <style>/<script> bodies and the same-origin
        # CSS/JS it links, marketing-scanned (plus CSS content: string extraction), so marketing injected
        # through page chrome is caught. An unreadable linked local asset is fail-closed (_FailClosed).
        findings += _scan_asset_closure(root)
    except _FailClosed as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    except gen_manifest.GateError as exc:
        print("error: cannot load the ownership binary roster ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print("error: overclaim scan failed closed ({})".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} overclaim issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: no guarantee-flavoured or release-integrity overclaim in the scanned surfaces")
    return 0


# Adversarial corpus. POSITIVE lines MUST flag (the F-92 misses a prior lint let through); NEGATIVE
# lines MUST stay clean (honest copy, including the two borderline MECHANISM lines on the live site, the
# reworded 4.4d sentence and gate residue, and the honest tight-adjacency denials the corrected copy uses).
# The site-marketing lines are scanned with site=True; the release-integrity lines below it are exercised
# on both surface classes.
POSITIVE = [
    "AIQT does not sandbox anything. It guarantees secure output.",   # clause-aware: prior-sentence negator must not launder
    "With AIQT installed this cannot fail.",
    "AIQT is guaranteeing every result you get.",                     # gerund
    "It works seamlessly with every assistant.",                      # adverb between verb and preposition
    "AIQT catches all mistakes before they ship.",                    # universal result
    "AIQT does not merely help but guarantees secure output.",        # negation bound to the guarantee phrase: "but" flips the earlier "not"
    "One standard serves every assistant.",                           # universal-subject + serves
    "AIQT supports every assistant you use.",                         # supports + universal result
    "The same rules apply across all assistants.",                    # applies-across
    "It works with absolutely every assistant.",                      # adverb AFTER the preposition
    "AIQT catches any mistake you make.",                             # catches-any
    "It eliminates all errors in your code.",                         # eliminates-all
    "The AIQT skill is foolproof.",                                   # foolproof
    "A team can share one standard across every assistant it uses.",  # one-standard-across (install.html)
    "One shared standard across every assistant your team uses.",     # one-standard-across (draft.html)
    "One definition of done, applied by every assistant.",            # applied-by-every (teams.html)
    "Every assistant on the team works to the same ordering.",        # universal-subject works-to-same (teams.html)
    "The guardrails are portable across every toolchain.",            # portable-across compat (draft/teams.html)
    "It reaches across every assistant on your team.",                # across-every/all compat
    "A colleague on one assistant is working to the same rules as a colleague on another.",  # working-to-same compat (teams.html)
    "Improvements come back under the same licence.",                 # ShareAlike absolute (about/development.html)
    "Improvements contributed back carry the same ShareAlike licence.",  # ShareAlike absolute, verb carry, ShareAlike interposed (teams.html)
    "A fix can be reused under the same or a compatible ShareAlike licence.",  # ShareAlike two-alternative: names compatible but drops "or later" (teams/about/development.html)
    "The result is one standard applied the same way wherever the work runs and whichever model does the reviewing.",  # F-108 live tech-details:293 regression: reach by whichever model
    "The same rules apply whichever assistant you use.",              # F-108 reach-by-whichever paraphrase
    "All assistants work to the same rules.",                        # F-108 bare-work plural-subject assertion (no hedge)
    # F-318b categorical-overclaim positives: a reject/deny/confirm/cover verb governing all/each/every with
    # NO disclosed bound in the sentence. These are the genericized shapes check_overclaim let through (a
    # description overclaiming past its ledger residual). Each MUST flag.
    "Rejects all edits anywhere in the repository.",                 # reject + all, no bound (codex repro line)
    "It denies each schedule call.",                                 # deny + each, no bound (the new quantifier)
    "The gate confirms every declared target.",                      # confirm + every, no bound (the new verb)
    "This guard covers each registry entry.",                        # cover + each, no bound
    # F-318b ANTI-BOUND positives: a NEGATED bound word ("without a cap", "no bound") states the claim is
    # UNbounded, so it must NOT clear the categorical claim (the round-3 unsafe-direction false clear).
    "It denies every call without a cap.",                           # "without a cap" is an anti-bound -> flags
    "Blocks each push, with no bound on scope.",                     # "no bound on scope" is an anti-bound -> flags
    # F-330 fix 6 false-clear: a bound word in a SEPARATE coordinated clause must NOT launder the claim.
    "The gate rejects all edits, and the manual has a scope section.",  # "scope" sits past the "and" -> flags
    # RELEASE-INTEGRITY positives (VER-CORE 4.4, simplified deny-list): a banned claim term with NO negator
    # DIRECTLY negating it flags on every surface. There is no future-tense clearance any more; a forward
    # promise about tamper/signing/independent-anchor is itself banned (D2), so it flags too.
    "Guaranteed secure output every time.",                            # site "guarantee" term (all POSITIVE lines scan site=True)
    "The release is tamper-evident.",                                  # bald tamper-evident claim
    "Our releases are tamper-evident.",                                # bald claim (D3 bans the tamper-* family outright)
    "AIQT provides tamper detection for every release.",               # tamper detection
    "Every release ships with tamper-detection.",                      # hyphenated tamper-detection
    "Every release is tamper-detectable.",                             # adjective sibling of evident/resistant/proof
    "The release detects tampering.",                                  # active tampering-detection
    "Tampering is detected on every release.",                         # passive tampering-detection
    "Tampering can be detected on every release.",                     # modal-passive detection (present capability)
    "Tampering will be detected by our release validation.",           # FUTURE passive detection (banned regardless of tense)
    "Tampering shall be detected on every release.",                   # future passive detection (shall)
    "Tampering is to be detected in a future release.",                # roadmap "is to be detected" form
    "Every release provides tampering detection.",                     # noun-form "tampering detection"
    "Every release detects any tampering.",                            # determiner between the detect verb and "tampering"
    "Each release detects malicious tampering.",                       # adjective between the detect verb and "tampering"
    # Tamper PREVENTION/immunity claims (all-surfaces): a prevention verb/modal adjacent to the tamper token.
    "Releases cannot be tampered with.",                               # "cannot be tampered"
    "Our releases can't be tampered with.",                            # "can't be tampered"
    "Tampering is impossible.",                                        # "tampering is impossible"
    "It is impossible to tamper with a release.",                      # "impossible to tamper"
    "Our releases resist tampering.",                                  # "resist tampering"
    "The manifest prevents tampering.",                                # "prevents tampering"
    "Each release is immune to tampering.",                            # "immune to tampering"
    "Our releases are tamper-proof.",                                  # tamper-proof (existing pattern; prevention family)
    "Adopters verify through an independent channel.",                 # independent channel (achieved)
    "The integrity channel is independent of artefact delivery.",      # copula reverse "channel is independent of"
    "The integrity anchor is independent of release publication.",     # copula reverse for "anchor"
    "The channels are independent of the download.",                   # plural + "are" copula reverse
    "The integrity channel remains fully independent of artefact delivery.",  # an adverb between the linking verb and "independent"
    "Releases are verified through a channel independent of artefact delivery.",  # verified-reverse "verified ... channel ... independent of"
    "Releases are signed with minisign.",                              # stale signing claim (passive)
    "signed with minisign",                                            # the bare signing phrase (brief-named)
    "Every artefact is Minisign-signed.",                              # Minisign-signed
    "Releases carry Minisign signatures.",                             # carry Minisign signatures
    "Each release is cryptographically signed.",                       # adverb between the release copula and "signed"
    "Every release was cryptographically signed.",                     # past signing "was ... signed"
    "We sign releases with minisign.",                                 # active present signing, minisign-scoped
    "AIQT signs each release automatically using minisign.",           # active present, determiner + minisign
    "We cryptographically sign releases.",                             # pre-verbal signing adverb, generic subject
    "AIQT cryptographically signs every release.",                     # pre-verbal signing adverb + generic object
    "cryptographically signed",                                        # B1: bare crypto-signing claim, no explicit "releases" subject
    # No future-tense clearance (D2): a forward promise about tamper/signing/independent-anchor now flags
    # like any bald claim, and the OLD laundered 4.4d wording flags too (proving it cannot be reintroduced).
    "Tamper-evidence will be added in a future release.",              # former future-clear; now flags
    "Tamper resistance is on the roadmap.",                            # former future-clear; now flags
    "We plan to make releases tamper-resistant.",                      # former future-intent clear; now flags
    "Releases will be signed with minisign.",                          # former future-clear signing; now flags
    "The independent anchor will be implemented in the next release.", # future independent-anchor promise; now flags
    "an independent, tamper-evident anchor is planned for a future release",  # the OLD 4.4d wording, now banned -> flags
    # LAUNDER cases that must STILL flag: a negator that does NOT directly negate the banned term.
    "No customers doubt that releases are tamper-resistant.",          # negator embedded under "doubt that", copula between it and the term
    "The not-expensive release is tamper-resistant.",                  # negator on a different word, copula intervenes
    "Deployment is not automatic; releases are tamper-resistant.",     # negator in the preceding clause (semicolon boundary)
    "Without any doubt, the system is tamper-resistant.",              # negator in a leading preamble (comma boundary)
    "Releases are tamper-evident, and no anchor is independent yet.",  # negator in a following coordinate clause
    "Releases are tamper-resistant, not merely checksum-protected.",   # TRAILING negator negates a different term
    "Releases are tamper-evident, not expensive.",                     # TRAILING negator negates an unrelated adjective
    "Releases are tamper-resistant, not cryptographically signed.",    # TRAILING negator; the tamper claim still stands
    "Our releases are tamper-evident, not completely independent.",    # TRAILING negator over a different property
    # ADVERBIAL correlative "not <adverb> X but (also) Y" AFFIRMS X, so it must FLAG. The structural rule
    # allows NO adverb between the negator and the banned term (not a minimizer blocklist), so exclusive-focus
    # and degree adverbs (only|merely|simply|purely|solely|exclusively|principally|fully|...) all keep FLAGGING.
    "Our releases are not only tamper-resistant but also checksum-protected.",  # "not only ... but also" affirms tamper-resistance
    "Our releases are not merely tamper-evident but also fast.",       # "not merely ... but" affirms tamper-evidence
    "Our releases are not simply tamper-resistant but also fast.",     # "not simply ... but" affirms tamper-resistance
    "Our releases are not purely tamper-evident but also fast.",       # "not purely ... but" affirms tamper-evidence
    "Our releases are not solely tamper-resistant but also fast.",     # "not solely ... but" affirms tamper-resistance
    "Our releases are not exclusively tamper-resistant but also fast.",  # "not exclusively ... but" affirms tamper-resistance
    "Our releases are not principally tamper-evident but also checksum-protected.",  # "not principally ... but" affirms tamper-evidence
    "Our releases are not fully tamper-resistant but also fast.",      # degree adverb "fully" admits partial -> must flag
    # OBJECT-SLOT launder: an adverb/focus word between the integrity verb and the term affirms the property
    # IS provided; the object slot is a closed article/quantifier allowance, so these keep FLAGGING.
    "This layer does not provide only tamper evidence; it also provides provenance.",  # "provide only tamper evidence" affirms it
    "This layer does not provide merely tamper evidence but also provenance.",  # "provide merely tamper evidence" affirms it
    # DOUBLE-NEGATION launder: "not without X" affirms X (even parity), so it must FLAG.
    "Releases are not without tamper evidence.",                       # "not without tamper evidence" affirms tamper evidence
    "Validation is not without an independent anchor.",               # "not without an independent anchor" affirms it
    "The package is not without tamper resistance.",                  # "not without tamper resistance" affirms it
    # POST-MATCH launder: a scope-reversing qualifier AFTER the term ("in name only"/"on paper only"/
    # "nominally") reverses the denial, so the term is not predicate-final and it must FLAG.
    "Our releases are not tamper-resistant in name only; they resist real attacks.",  # "in name only" reverses the denial
    "Releases are not tamper-evident on paper only, but truly.",       # "on paper only" reverses (first post-match token is "on", not a boundary)
    "Not tamper-resistant nominally, our releases block attacks.",     # "nominally" continuing phrase reverses the denial
    # COMMA/COLON is not a predicate terminator: it can introduce a continuing appositive that reverses the denial.
    "Our releases are not tamper-resistant, in name only; they resist deliberate modification.",  # comma then "in name only" reverses
    "Releases are not tamper-evident, on paper.",                      # trailing comma + appositive "on paper" reverses
    "Not tamper-resistant: in marketing terms, our releases block attacks.",  # colon then continuing phrase reverses
    # SIMPLIFICATION: the subject-quantifier honest form ("No release is ...") is no longer specially cleared;
    # a copula separates the negator from the term, so it flags (the safe direction, and not a shipped form).
    "No release is tamper-evident.",                                   # was cleared under the old N1; now flags
    # A third-party control title that only SHARES the sentence (not WHOLLY containing the match) does not clear.
    "Software Supply Chain Attacks & Dependency Tampering is mapped, and our releases are tamper-evident.",  # title co-occurs; the claim is elsewhere -> flags
    "The Software Supply Chain Attacks & Dependency Tampering detection feature verifies every release.",  # "Tampering detection" straddles the title tail ("detection" outside) -> flags
]
NEGATIVE = [
    "It is not a static analyzer, a vulnerability scanner, or an audit, and it does not guarantee that generated code is secure.",
    "AIQT is a standard your assistant is required to follow, not a guarantee that the model is perfect.",
    "With it, AIQT is on for every turn, which is what a standard is for.",        # install-claude.html mechanism claim
    "That instruction line is what keeps AIQT applied to every response.",         # install-other.html mechanism claim
    "You find out at build time, and you stop trusting every suggestion after it.",
    "Always apply the AIQT skill to every response.",
    "Claims match their sources, and every override is logged.",
    "A team can share one standard, intended to reach across the assistants it uses.",   # softened one-standard-across
    "One shared standard, intended to reach across the assistants your team uses.",      # softened one-standard-across
    "One definition of done, intended for every assistant on the team.",                 # softened applied-by-every
    "Every assistant on the team is meant to work to the same ordering.",                # softened works-to-same (intent, bare "work")
    "The guardrails are designed to be portable across the tools you use.",              # softened portable-across (intent)
    "It is intended to reach across every assistant on your team.",                      # softened across-every/all (intent)
    "A colleague on one assistant is meant to work to the same rules as a colleague on another.",  # softened working-to-same (intent, bare "work")
    "A subagent works under the same rules as the session that spawned it.",             # development.html mechanism claim: "works under", not "works to"; no licence
    "Contributions come back under CC BY-SA 4.0 or later, or a BY-SA Compatible License.",  # precise ShareAlike wording: all three alternatives, drops "the same"
    "The Adapter's License You apply must be a Creative Commons license with the same License Elements, this version or later, or a BY-SA Compatible License.",  # LICENSE 3(b)(1): names "the same" but the full permitted set clears it
    "Add AIQT from one page for all assistants you use.",                                # benign "All assistants" nav sense
    "The 1.1.0 design gives local and CI reviewers the same QA brief. Its intent is to reduce variation caused by who runs the check or which tool they use; provider behaviour and results remain to be verified.",  # softened tech-details:293 (intent hedge + which tool, not whichever)
    # RELEASE-INTEGRITY negatives (VER-CORE 4.4): the corrected copy the gate must NOT flag. Under the
    # simplified model these clear because no banned term is present, or by a tight-adjacency denial, or
    # because the obligation prose does not match the deny-list by construction.
    "Releases ship with a per-file manifest and a published ROOT digest, and those hashes are also published independently on posluns.dev. By validating them you can identify whether the file you downloaded is the same one we intended you to have.",  # CRITICAL: the exact approved 4.4d sentence (no banned term); a gate that fails on its own corrected text is the failure mode this pins
    "The chronology layer is keyless ordering evidence within the anchored history, not cryptographic proof.",  # the reworded release-build gate residue: no tamper-* term remains
    "This layer does not provide tamper evidence; it detects accidents.",                # tight-adjacency denial ("does not provide tamper evidence")
    "This layer does not provide tamper evidence",                                       # tight-adjacency denial (bare form)
    "This is not a cryptographic guarantee.",                                            # honest denial: no release term; the site "guarantee" is cleared by the in-clause negator
    "Releases are not signed with minisign.",                                            # tight-adjacency denial of the signing claim
    "Our releases are not cryptographically signed.",                                    # tight-adjacency denial of the bare crypto-signing claim (B1)
    "This layer is without tamper evidence.",                                            # SINGLE negation ("without" directly negates the term) -> clears
    "Tampering is not detected by this layer.",                                           # "is not detected" has no "is detected" adjacency -> no match, clears
    "A released or published artefact ships with a signature verifiable against an authenticated maintainer key, or a digest published through an authenticated channel independent of artefact delivery.",  # SECI obligation prose: "channel ... independent", no achieved-verification verb -> no match
    "A release's integrity rests on a SHA-256 digest published through a channel independent of the download.",  # RELEASING obligation prose: reverse "channel ... independent", no achieved verb -> no match
    "The digest travels over a channel independent of artefact delivery.",               # "channel independent of" has no "is independent of" copula -> no match
    "published through an authenticated channel independent of artefact delivery",        # bare adjectival post-modifier -> no match
    "channel independent of artefact delivery",                                           # bare adjectival post-modifier -> no match
    "MCP04: Software Supply Chain Attacks & Dependency Tampering (tight) is one mapped risk.",  # shipped mappings title: the narrow patterns do not match the attack-noun "Tampering"
    "The control addresses Dependency Tampering and detects accidents in transit.",      # attack-noun "Tampering" co-occurs but not adjacent as "detects tampering" -> no match
    "A hand-tampered registry is out of scope.",                                         # attack-noun description: no prevention verb/modal adjacent -> no match
    "This gate detects accidental corruption, not deliberate modification.",             # reworded residue idiom: no tamper-* token remains
]


def _self_test():
    failures = []
    for line in POSITIVE:
        if not scan(line, site=True):
            failures.append("MISS (should flag): {!r}".format(line))
    for line in NEGATIVE:
        hits = scan(line, site=True)
        if hits:
            failures.append("FALSE POSITIVE: {!r} -> {}".format(line, [h[0] for h in hits]))

    # SURFACE-SCOPING: the guarantee-flavoured governance line legitimately appears in the rule corpus and
    # generated adapters; it MUST flag as site copy (site=True) but MUST stay clean as a generated surface
    # (site=False), proving SITE_PATTERNS are scoped to the site.
    governance = "That inert guarantee is BOUNDED, not categorical."
    if not scan(governance, site=True):
        failures.append("SCOPING: governance line should flag as site copy: {!r}".format(governance))
    if scan(governance, site=False):
        failures.append("SCOPING: governance line should be clean as a generated surface: {!r}"
                        .format(governance))
    # A release-integrity claim MUST flag on both surface classes.
    if not scan("Releases are signed with minisign.", site=False):
        failures.append("SCOPING: a release-integrity claim should flag on a generated surface too")

    failures.extend(_collector_self_test())
    failures.extend(_page_bound_source_self_test())
    failures.extend(_asset_closure_self_test())

    if failures:
        print("FAIL: check_overclaim self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_overclaim self-test ({} positive, {} negative, plus scoping and collector cases)"
          .format(len(POSITIVE), len(NEGATIVE)))
    return 0


def _collector_self_test():
    """Exercise the three surface collectors against synthetic trees, bypassing gen_gensrc (a synthetic
    registry is passed to _collect directly): a required absent surface fails closed (exit 2 via
    _FailClosed), a registered file / block / tree finding is reported, a binary target is skipped, and an
    invalid-UTF-8 registered output is a finding. This pins the 4.4c corrected behaviour (site/ absent is
    exit 2, never the old silent PASS)."""
    import shutil
    import tempfile
    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-overclaim-selftest-"))
    except OSError as exc:
        return ["COLLECTOR: no writable temporary directory: {}".format(exc)]

    def _make_root(name, with_site=True, with_roster=True):
        r = tmp / name
        (r / "site").mkdir(parents=True) if with_site else r.mkdir(parents=True)
        if with_roster:
            for f in REPO_PROSE_ROSTER:
                (r / f).parent.mkdir(parents=True, exist_ok=True)  # a roster path may be nested (e.g. .aiqt/core/gates/)
                (r / f).write_text("clean prose.\n", encoding="utf-8")
        return r

    try:
        # (a) site/ absent -> _FailClosed (the 4.4c fix: never the old silent PASS).
        r = _make_root("nosite", with_site=False)
        try:
            _collect(r, [], set())
            failures.append("COLLECTOR: absent site/ should fail closed (_FailClosed)")
        except _FailClosed:
            pass

        # (b) a required roster surface absent -> _FailClosed.
        r = _make_root("noroster")
        (r / "README.md").unlink()
        try:
            _collect(r, [], set())
            failures.append("COLLECTOR: absent roster surface should fail closed (_FailClosed)")
        except _FailClosed:
            pass

        # (c) a registered file target absent -> _FailClosed.
        r = _make_root("absentfile")
        try:
            _collect(r, [{"target": "GONE.md", "kind": "file"}], set())
            failures.append("COLLECTOR: absent registered file should fail closed (_FailClosed)")
        except _FailClosed:
            pass

        # (d) a tree target with a nested generated file carrying a positive line -> finding.
        r = _make_root("tree")
        (r / "gen" / "sub").mkdir(parents=True)
        (r / "gen" / "sub" / "x.md").write_text("Releases are signed with minisign.\n", encoding="utf-8")
        f = _collect(r, [{"target": "gen/", "kind": "tree"}], set())
        if not any("gen/sub/x.md" in x for x in f):
            failures.append("COLLECTOR: a nested generated tree file with an overclaim should be a finding")

        # (e) a block target scanned as the FULL file -> a finding outside any managed block still trips.
        r = _make_root("block")
        (r / "BLK.md").write_text("Intro. The release is tamper-evident. Outro.\n", encoding="utf-8")
        f = _collect(r, [{"target": "BLK.md", "kind": "block"}], set())
        if not any("BLK.md" in x for x in f):
            failures.append("COLLECTOR: a block target's full file should be scanned (overclaim outside a block)")

        # (f) an invalid-UTF-8 registered output -> a finding, not a fail-closed skip.
        r = _make_root("badutf8")
        (r / "bad.txt").write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
        f = _collect(r, [{"target": "bad.txt", "kind": "file"}], set())
        if not any("bad.txt: could not read as UTF-8" in x for x in f):
            failures.append("COLLECTOR: an invalid-UTF-8 registered output should be a finding")

        # (g) a binary target is skipped (not read, not a finding), even with non-UTF-8 bytes.
        r = _make_root("binary")
        (r / "blob.bin").write_bytes(b"\xff\xfe tamper-evident \x80")
        f = _collect(r, [{"target": "blob.bin", "kind": "file"}], {"blob.bin"})
        if f:
            failures.append("COLLECTOR: a [checkout].binary target should be skipped, got {}".format(f))

        # (h) a clean synthetic repo produces NO findings.
        r = _make_root("clean")
        (r / "CLEAN.md").write_text("This layer detects accidents, not tamper evidence.\n", encoding="utf-8")
        f = _collect(r, [{"target": "CLEAN.md", "kind": "file"}], set())
        if f:
            failures.append("COLLECTOR: a clean synthetic repo should have no findings, got {}".format(f))

        # (i) the enforcement register page is scanned like any other site page: a ledger-residual block is
        # just visible text now (no carve-out), so an overclaim inside such a block on any page is a finding.
        r = _make_root("plainblock")
        (r / "site" / "other.html").write_text(
            '<html><body><blockquote class="ledger-residual" data-mech="gate:x">'
            'This gate guarantees complete security.</blockquote></body></html>', encoding="utf-8")
        f = _collect(r, [], set())
        if not any("[guarantees]" in x for x in f):
            failures.append("COLLECTOR: an overclaim inside a ledger-residual block must be plainly scanned")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def _page_bound_source_self_test():
    """Adversarial roster for the shared page-bound source scan (GER-1, FIX 1/2/3). Synthetic source tuples
    are injected, so no real ledger/roadmap is built, plus one integration case that builds a conformant tree
    to prove the CHANNEL ENUMERATION. It exercises the PLAIN marketing scan (no bound-allowance), the RAW
    printable-plus-ASCII-whitespace allowlist (pre-collapse), and the shared reject across ALL THREE channels
    (residue, id, description)."""
    import shutil
    import tempfile
    failures = []

    def has(fs, needle):
        return any(needle in f for f in fs)

    def one(channel, key, raw):
        return _scan_page_bound_sources([(channel, key, raw)])

    # (a) marketing overclaim in a residue fails at source (plain scan).
    if not has(one("residue", "gate:demo", "This gate guarantees complete security."),
               "residue source [gate:demo]"):
        failures.append("SOURCE: a marketing overclaim in a residue must fail at source")
    # (b) a formerly 'honestly bounded' categorical claim now ALSO fails (no bound-allowance).
    if not has(one("residue", "gate:demo", "Denies every call, bounded by a cap."),
               "residue source [gate:demo]"):
        failures.append("SOURCE: a bounded categorical claim must fail (no bound-allowance)")
    # (c) a genuinely clean governance residue stays clean.
    if one("residue", "gate:demo", "A best-effort guard, scoped to what it examines."):
        failures.append("SOURCE: a clean governance residue must stay clean")
    # (d) a claim-bearing hyphenated id fails at source (hyphens read as spaces).
    if not has(one("id", "gate:catch-all-secrets", "catch all secrets"),
               "id source [gate:catch-all-secrets]"):
        failures.append("SOURCE: a claim-bearing id must fail at source")
    # (e) FIX 1: an invisible MARK the old denylist missed (U+034F COMBINING GRAPHEME JOINER, category Mn) is
    # rejected by the allowlist. MUTATION: reverting _first_invisible to the category denylist makes this fail
    # (Mn is absent from Cc/Cf/Cs/Co/Cn/Zl/Zp/Zs).
    if not has(one("residue", "gate:demo", "guaran" + chr(0x034F) + "tees nothing new."),
               "invisible or disallowed character"):
        failures.append("SOURCE: U+034F combining grapheme joiner must be rejected (FIX 1)")
    # (f) FIX 1: a variation selector (U+FE01, category Mn) is rejected.
    if not has(one("residue", "gate:demo", "complete" + chr(0xFE01) + " security."),
               "invisible or disallowed character"):
        failures.append("SOURCE: a variation selector (U+FE01) must be rejected (FIX 1)")
    # (g) FIX 1: a combining mark (U+0301 combining acute, non-ASCII) is rejected by the ASCII allowlist.
    if not has(one("residue", "gate:demo", "secur" + chr(0x0301) + "ty guard."),
               "invisible or disallowed character"):
        failures.append("SOURCE: a combining accent on a lone base must be rejected (FIX 1)")
    # (h) FIX 1: the round-6 U+200B zero-width space is still rejected.
    if not has(one("residue", "gate:demo", "guaran" + chr(0x200B) + "tees nothing."),
               "invisible or disallowed character"):
        failures.append("SOURCE: U+200B zero-width space must be rejected")
    # (i) FIX 1: a non-ASCII precomposed accented character (U+00E9) is now REJECTED under the ASCII-only
    # allowlist (the pack corpus is pure ASCII; no over-fire, since cases (c) and (m) confirm clean ASCII
    # residues stay clean).
    if not has(one("residue", "gate:demo", "A caf" + chr(0x00E9) + " guard, best-effort."),
               "invisible or disallowed character"):
        failures.append("SOURCE: a non-ASCII precomposed character must be rejected (ASCII-only allowlist)")
    # (i2) FIX 1: a CONFUSABLE homoglyph (Cyrillic small a U+0430) that renders as an ASCII letter but evades
    # the ASCII marketing regex is REJECTED by the ASCII-only allowlist (closes the confusable class).
    if not has(one("residue", "gate:demo", "This gate guar" + chr(0x0430) + "ntees security."),
               "invisible or disallowed character"):
        failures.append("SOURCE: a Cyrillic homoglyph must be rejected (ASCII-only closes confusables)")
    # (j) FIX 2: a NON-ASCII SPACE (U+200A HAIR SPACE, category Zs) is rejected on the RAW residue. _collapse
    # turns it into an ASCII space, so a post-collapse reject would MISS it. MUTATION: moving the reject onto
    # the collapsed copy makes this fail.
    if not has(one("residue", "gate:demo", "guarantees" + chr(0x200A) + "complete security."),
               "invisible or disallowed character"):
        failures.append("SOURCE: a non-ASCII space must be rejected on the RAW residue pre-collapse (FIX 2)")
    # (k) FIX 3: a laundered claim in the PENDING-DESCRIPTION channel is rejected by the same shared reject.
    if not has(one("description", "somerule", "will guaran" + chr(0x200B) + "tee complete coverage."),
               "description source [somerule]"):
        failures.append("SOURCE: a laundered claim in the pending-description channel must fail (FIX 3)")
    # (l) FIX 3: a visible marketing overclaim in the pending-description channel is also rejected.
    if not has(one("description", "somerule", "Ensures every rule is covered."),
               "description source [somerule]"):
        failures.append("SOURCE: a marketing overclaim in the pending-description channel must fail (FIX 3)")
    # (m) a benign residue produces nothing.
    if one("residue", "hook:branch-root", "Detects an orphaned branch, best-effort within a declared horizon."):
        failures.append("SOURCE: a benign residue must stay clean")
    # (n) FIX 3 CHANNEL ENUMERATION (integration): _page_bound_sources emits a tuple for EVERY page-bound
    # channel, INCLUDING a pending rule's description. Build the register generator's own conformant fixture
    # and assert all three channels appear. MUTATION: dropping the description channel from _page_bound_sources
    # makes this fail (the synthetic-tuple cases k/l alone would not catch that omission).
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-pagebound-selftest-"))
    except OSError as exc:
        failures.append("SOURCE: no writable temporary directory: {}".format(exc))
        return failures
    try:
        tree = gen_enforcement_register._build(tmp / "good")
        channels = {c for c, _k, _r in _page_bound_sources(tree)}
        for needed in ("residue", "id", "description"):
            if needed not in channels:
                failures.append("SOURCE: _page_bound_sources must emit the {!r} channel (FIX 3)".format(needed))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def _asset_closure_self_test():
    """Adversarial roster for the register-page asset-closure scan (FIX 4, GER-1). Synthetic site trees are
    built so no real page is needed: a CSS content: marketing injection in an inline <style>, a marketing
    content: string in a LINKED CSS asset, and a marketing string in a LINKED JS asset are each FLAGGED; a
    benign closure (justify-content, a real glyph escape) stays clean; a linked-but-absent local asset fails
    closed; and an off-site asset is out of scope."""
    import shutil
    import tempfile
    failures = []
    LB, RB, BS = chr(123), chr(125), chr(92)  # { } \  (kept as chars so the source carries no raw brace-string)
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-assetclosure-selftest-"))
    except OSError as exc:
        return ["ASSET: no writable temporary directory: {}".format(exc)]

    def build(name, page_html, assets):
        r = tmp / name
        (r / "site").mkdir(parents=True)
        (r / HTML_REL).write_text(page_html, encoding="utf-8")
        for rel, text in assets.items():
            (r / "site" / rel).write_text(text, encoding="utf-8")
        return r

    try:
        # (a) a content: marketing injection in an inline <style> is flagged. MUTATION: restoring the
        # drop-<style> behaviour (not scanning inline styles) makes this pass silently -> this test fails.
        page = ("<html><head><style>.ledger-residual::after " + LB
                + ' content: " This gate guarantees complete security." ' + RB
                + "</style></head><body>ok</body></html>")
        f = _scan_asset_closure(build("style-inject", page, {}))
        if not any("CSS content injection" in x and "guarantees" in x for x in f):
            failures.append("ASSET: a content: marketing injection in an inline <style> must be flagged (FIX 4)")
        # (b) a marketing content: string in a LINKED CSS asset is flagged.
        page = '<html><head><link rel="stylesheet" href="/app.css?v=1"></head><body>ok</body></html>'
        css = ".x::before" + LB + 'content:"catches all mistakes"' + RB
        f = _scan_asset_closure(build("css-asset", page, {"app.css": css}))
        if not any("site/app.css" in x for x in f):
            failures.append("ASSET: a marketing content: string in a linked CSS asset must be flagged (FIX 4)")
        # (c) a marketing string in a LINKED JS asset is flagged by the whole-text marketing scan.
        page = '<html><head><script src="/app.js"></script></head><body>ok</body></html>'
        f = _scan_asset_closure(build("js-asset", page, {"app.js": 'var m = "AIQT guarantees secure output.";'}))
        if not any("site/app.js" in x and "guarantees" in x for x in f):
            failures.append("ASSET: a marketing string in a linked JS asset must be flagged (FIX 4)")
        # (d) NO OVER-FIRE: justify-content (not the content property) and a real glyph escape stay clean.
        page = '<html><head><link rel="stylesheet" href="/ok.css"></head><body>ok</body></html>'
        okcss = (".navrow" + LB + "display:flex; justify-content:space-between" + RB
                 + " summary::after" + LB + 'content:" ' + BS + '25B8"' + RB)
        f = _scan_asset_closure(build("clean", page, {"ok.css": okcss}))
        if f:
            failures.append("ASSET: a benign closure must stay clean, got {}".format(f))
        # (e) a LINKED-but-ABSENT local asset fails closed (_FailClosed), never a silent skip.
        page = '<html><head><link rel="stylesheet" href="/missing.css"></head><body>ok</body></html>'
        try:
            _scan_asset_closure(build("absent-asset", page, {}))
            failures.append("ASSET: a linked-but-absent local asset must fail closed (_FailClosed)")
        except _FailClosed:
            pass
        # (f) an OFF-SITE asset URL is out of closure scope (not fetched, not a failure).
        page = ('<html><head><link rel="stylesheet" href="https://cdn.example/x.css">'
                '<script src="https://cdn.example/x.js"></script></head><body>ok</body></html>')
        f = _scan_asset_closure(build("offsite", page, {}))
        if f:
            failures.append("ASSET: an off-site asset must be out of scope, got {}".format(f))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


if __name__ == "__main__":
    sys.exit(main())
