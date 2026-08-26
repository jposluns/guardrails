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
adapter or a shipped doc as from a site page (4.4: at first release no surface may claim tamper detection,
tamper evidence, or an independent channel while the 5.7 upgrade condition is unmet; tamper-resistance
wording may appear only as explicitly future-tense roadmap).

THREE SURFACE COLLECTORS (VER-CORE 4.4c; replaces the former site-only scan):
  1. site/*.html: visible-text + meta scanning, SITE_PATTERNS + RELEASE_PATTERNS. site/ is a REQUIRED
     surface: an absent, unwalkable, or non-directory site/ is a fail-closed exit 2, never a silent PASS
     (the 4.4c correction of the old "no site/ directory -> PASS" shape).
  2. The hand-authored repo prose roster (README.md, SCOPE.md, SYSTEM-HARDENING.md): RELEASE_PATTERNS.
     The spec's other named prose surfaces (DISCLOSURE.md, CHANGELOG.md, ROADMAP.md, CLAUDE.md) are
     gensrc-REGISTERED generated outputs and so arrive through collector 3; the roster carries only the
     hand-authored remainder. Every rostered path is REQUIRED; an absent one is exit 2. Nothing is dormant
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
dodges the vocabulary below will pass. The third-party title allowlist and the tense guards are
maintainer-calibrated against the live surfaces. It is not a complete overclaim detector, and it does not
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
    tampering, which it does not (5.2: it detects accident, not tamper). FUTURE-guarded, so the approved
    roadmap wording ("an independent, tamper-evident anchor is planned for a future release") and 5.6's own
    roadmap text stay clean while a present-tense assertion trips. The CLAIM forms are evident/evidence/
    resistant/resistance/proof, the hyphen-or-space "tamper-detection"/"tamper detection", the noun
    "tampering detection", and a detect verb governing the attack-noun ("detects tampering", "tampering is
    detected"). A BARE attack-noun with no detect verb ("Dependency Tampering", "a hand-tampered registry")
    is deliberately NOT matched, so an honest control TITLE or attack description does not false-positive;
    the third-party title allowlist below is the forward-safety layer for a shipped title that carries a
    CLAIM-form phrase.
  - "independent ... channel/anchor/reference", the reverse "verified ... channel ... independent of", AND
    the copula reverse "<channel|anchor|reference>(s) is/are/remains/stays independent of": an achieved claim
    of the independent integrity channel or anchor that 5.6 defers, in any word order. FUTURE-guarded. The
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
    minisign/cryptographic-scoped so honest prose that merely says "sign" stays clean. FUTURE-guarded.

RELEASE-INTEGRITY clearing is a TIGHT proposition-adjacent ALLOWLIST bound to the matched tamper/release
noun (VER-CORE 4.4/5.6). It INVERTS the former loose model (round 5): rather than "any future predicate or
any negator in the window clears", it clears ONLY a narrow enumerated set of constructions and FLAGS
everything else, so it is launder-free by construction and its false positives fall in the safe direction.
A novel-but-honest future or negation phrasing that misses the allowlist may flag by design. Three families:
  - a shipped third-party control TITLE (THIRD_PARTY_TITLES, enumerated from the live site/mappings.html)
    that WHOLLY CONTAINS the matched span clears it, so a framework/control title that itself carries the
    vocabulary is not read as an AIQT claim; a match that merely OVERLAPS or abuts the title ("Software
    Supply Chain Attacks & Dependency Tampering detection feature ...", where "detection" falls outside the
    title) or only co-occurs in the sentence does not clear (F-VC5-D);
  - a tight NEGATION disclosure bound to the matched proposition (see _disclosure_clears), three rules: N1 a
    leading SUBJECT QUANTIFIER (no|neither|none as the FIRST token of the pre-match clause window, not an
    emphatic idiom, and DIRECTLY determining the tamper copula's subject with no reporting/embedding verb or
    "that" between it and the match: "No release is tamper-evident" clears, "Releases with no downtime are
    tamper-evident", "There is no doubt that our releases are tamper-resistant", and "No customers doubt that
    releases are tamper-resistant" all flag); N2 a DIRECT integrity-predicate negation
    (the closest pre-match negator negating an integrity verb governing the tamper term, no be-copula between:
    "This layer does not provide tamper evidence" clears, "We do not doubt that releases provide tamper
    evidence" and "The not-expensive release is tamper-resistant" flag); N3 a TRAILING residual whose EXACT
    phrase is on the RESIDUAL_DISCLOSURES allowlist and contains the match verbatim ("keyless tamper-evident
    ordering within the anchored history, not cryptographic proof"). Any other trailing negator flags ("...,
    not merely checksum-protected", "..., not expensive", a bare "..., not cryptographically signed"), and the
    window breaks on a comma, semicolon, coordinating and/or/plus/so, or subordinator, so a leading preamble, a
    separate coordinate/semicolon clause, a "so" result clause, or a "because" clause does not launder it
    ("Without any doubt, ..."; "Releases are tamper-evident, and no anchor is independent yet"; "We do not sign
    so releases are tamper-evident"; "Releases are not expensive because they are tamper-resistant") (F-VC5-B);
  - one of the three tight FUTURE constructions F1-F3 bound to the matched noun (see _future_clears): F1 a
    COPULA-BOUND status predicate the match's OWN copula points to ("anchor is planned for a future release",
    "Tamper detection is to be introduced in a future release", "Tamper resistance is on the roadmap"; the
    roadmap forward phrase clears ONLY as that copular predicate, so a present copula with a trailing roadmap
    ADJUNCT flags: "is tamper-resistant in the next release", "is tamper-evident on the roadmap"), F2
    "will|shall be" governing the match's subject ("Tamper-evidence will be added in a future release"), or F3
    a LEADING verbal future-intent that GOVERNS the tamper proposition ("We plan to make releases
    tamper-resistant.", "Releases are planned to be tamper-resistant."; an intent that governs a DIFFERENT
    object it reports on flags: "We plan to make a logo saying releases are tamper-resistant"). A bare status
    adjective pre-modifying a DIFFERENT noun ("The planned release is tamper-evident today", "The upcoming
    release is signed with minisign"), a future predicate whose subject is a different noun, a "will" governing
    a DIFFERENT verb ("A tamper-evident release will give your team confidence"), a future word across a
    preposition/paren/finite-verb ("... with a new logo planned for next year", "... ships on our planned
    schedule"), or a future word in a separate clause ("... and a further channel is planned"; "... according
    to our roadmap") cannot launder a present claim (F-VC5-C, Class B).

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
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
import gen_gensrc  # noqa: E402  build_registry: the in-memory gensrc recomputation (collector 3)
import gen_manifest  # noqa: E402  load_ownership: the [checkout].binary roster (collector 3 skip set)

# Negation is CLAUSE-aware, not a fixed char window: a negator only marks a match honest when it sits
# in the SAME clause as the match. A fixed window let a negator in a PRIOR sentence launder a fresh
# overclaim (e.g. "AIQT does not sandbox anything. It guarantees secure output." would wrongly pass).
# The negator alternation drives NEGATOR (the general in-clause/pre-match negation guard) and the tight
# NEGATION rule N2 (_disclosure_clears), so both read the same negator set.
_NEGATOR_ALT = (
    r"not|no|never|cannot|can't|without|nor|neither|hardly|rarely|"
    r"n't|doesn't|don't|isn't|aren't|won't|wouldn't")
NEGATOR = re.compile(r"\b(?:" + _NEGATOR_ALT + r")\b", re.IGNORECASE)
# A be-form COPULA between the governing negator and the match means the negator governs a DIFFERENT
# predicate and the tamper claim is a fresh copular assertion, so the negator does NOT launder it (the
# NEGATION rule N2 in _disclosure_clears checks this): "The not-expensive release IS tamper-resistant"
# ("is" intervenes -> flags), "Releases with no downtime ARE tamper-evident" ("are" intervenes -> flags),
# while the honest disclaimer "does not provide tamper evidence" (no copula between "not" and the match)
# clears. "is not tamper-resistant" also clears: the copula sits BEFORE the negator, not between it and
# the match, so the negation binds directly to the property.
BE_COPULA = re.compile(r"\b(?:is|are|was|were|be|been|being)\b", re.IGNORECASE)
# NEGATION rule N1 (SUBJECT-QUANTIFIER): a bare "no"/"neither"/"none" that is the LEADING token of the
# pre-match clause window directly negates the very noun the copula predicates the tamper property onto, so
# it clears even with a be-copula between it and the match ("No release is tamper-evident", "Neither release
# is tamper-resistant" honestly assert no such release exists). It clears ONLY as the leading token: an
# oblique mid-clause "no <noun>" ("Releases with no downtime are tamper-evident") does not begin the window
# and keeps FLAGGING. The further exception is an EMPHATIC-IDIOM noun immediately after it ("no doubt", "no
# question", "no way"): there the quantifier is an intensifier a launder exploits ("There is no doubt that
# ... releases are tamper-resistant"), NOT a subject quantifier, so those keep FLAGGING.
SUBJECT_QUANTIFIER = {"no", "neither", "none"}
EMPHATIC_IDIOM_NOUN = re.compile(
    r"\s*(?:doubt|doubts|doubting|question|questions|denying|way|means|sense)\b", re.IGNORECASE)
# NEGATION rule N1 also requires the leading quantifier to DIRECTLY determine the subject the tamper copula
# predicates onto: between the quantifier and the match there is NO reporting/embedding verb and NO "that"
# complementizer. Otherwise the quantifier governs a DIFFERENT noun and the tamper claim is embedded under a
# report ("No customers doubt that releases are tamper-resistant" -> "No" quantifies "customers"; the tamper
# claim sits under "doubt that"), so it FLAGS, while "No release is tamper-evident" (no reporting verb, no
# "that") still clears.
REPORTING_EMBED = re.compile(
    r"\b(?:doubt|doubts|believe|believes|say|says|saying|think|thinks|claim|claims|know|knows|deny|"
    r"denies|dispute|disputes|question|questions|argue|argues|assume|assumes|expect|expects|hope|hopes|"
    r"fear|fears|insist|insists|suggest|suggests|state|states|report|reports|contend|contends|that)\b",
    re.IGNORECASE)
# NEGATION rule N2 (DIRECT integrity-predicate negation): the text BETWEEN the governing negator and the
# match, with no be-copula in it, is either empty/adverbs-only (the negator negates the tamper property
# directly, "is not tamper-resistant") or an optional adverb run then an INTEGRITY VERB governing the tamper
# term ("does not provide tamper evidence", "did not deliver an independent anchor"). It is anchored end to
# end, so a NON-integrity verb or a new subject between the negator and the match breaks it: "We do not doubt
# that releases provide tamper evidence" ("doubt" is not an integrity verb) and "The not-expensive release is
# tamper-resistant" (a modifier, plus an intervening copula) both keep FLAGGING.
INTEGRITY_NEG_TAIL = re.compile(
    r"^\s*(?:[A-Za-z]+ly\s+){0,2}"
    r"(?:(?:provide|provides|provided|offer|offers|offered|deliver|delivers|delivered|give|gives|given|"
    r"make|makes|made|guarantee|guarantees|guaranteed|ensure|ensures|ensured|have|has|had)\s+"
    r"(?:\w+\s+){0,2})?$", re.IGNORECASE)
# Sentence and clause punctuation ends the clause a match belongs to. A CONTRASTIVE conjunction
# (but/yet/however/...) also ends the negation window: it flips polarity, so a negator before it does
# NOT scope over a guarantee after it. Binding negation to the guarantee-phrase segment this way makes
# "does not merely help but guarantees secure output" flag, where a whole-clause negation window let
# the earlier "not" (which negates "help", not "guarantees") launder the overclaim.
CLAUSE_BOUNDARY = re.compile(
    r"[.!?;:,]|\b(?:but|yet|however|nonetheless|nevertheless|rather|though|although|whereas)\b",
    re.IGNORECASE)
# FUTURE_WINDOW_BOUNDARY is the SUBORDINATOR-AWARE clause boundary: sentence/clause punctuation, a
# contrastive, a coordinating and/or/plus/so, AND the subordinators that open an attributive or subordinate
# clause. TWO guards bind to a SINGLE proposition through it. (1) The FUTURE window (_future_hedge_window):
# a roadmap predicate must occur AFTER the match, in the FORWARD part of the window (match end to the next
# boundary), so "Releases are tamper-resistant according to our roadmap" and "... because the next release
# adds documentation" cut the future words out at "according"/"because" and the present-tense claim still
# flags, while "an independent, tamper-evident anchor is planned for a future release" (no boundary between
# the tamper noun and "planned") keeps the predicate in the forward window and clears. (2) The residual-
# disclosure LEADING edge (_disclosure_clears): a leading negator clears only when NO subordinator stands
# between it and the match, so "Releases are not expensive because they are tamper-resistant" flags ("not"
# negates "expensive", bounded by "because") while "This layer does not provide tamper evidence" (no
# subordinator) still clears. It also breaks on a comma and a coordinating and/or/plus/so, so a leading
# preamble negator ("Without any doubt, ..."), a separate coordinate/semicolon clause, and a "so" result
# clause ("We do not sign so releases are tamper-evident") do not leak in.
FUTURE_WINDOW_BOUNDARY = re.compile(
    r"[.!?;:,]|\b(?:but|yet|however|nonetheless|nevertheless|rather|though|although|whereas|and|or|plus|so|"
    r"because|since|as|when|while|according|per|given|unless|until|after|before|if)\b",
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
# FUTURE clearance is a TIGHT proposition-adjacent allowlist (VER-CORE 4.4/5.6: tamper wording may appear
# only as explicitly future-tense roadmap), NOT the former "any future predicate in the forward window"
# co-occurrence, which laundered an unrelated future word governing a DIFFERENT noun. A match clears via
# future ONLY when one of three bound constructions holds (see _future_clears); anything else FLAGS, so a
# novel-but-honest forward phrasing may flag by design (the safe direction). The constructions:
#
# F1 (COPULA-BOUND status predicate): from the match end, ONLY head-noun-phrase tokens (determiners,
#    adjectives, nouns, hyphens; see _HEADNOUN) up to a be-copula, then an optional "to be", optional -ly
#    adverbs, an optional passive/infinitive participle, and a future STATUS predicate the copula points to:
#    a status adjective (planned/deferred/postponed/upcoming/forthcoming), an "in|for a/an/the next|future|
#    later|upcoming|coming release" phrase, or "on the roadmap". Because the copula must sit in the FORWARD
#    window, the match is the SUBJECT of that future predicate, so "anchor is planned for a future release"
#    and "Tamper detection is to be introduced in a future release" clear, while a PRESENT copula (which sits
#    BEFORE the match) leaves the forward window with no copula and the roadmap phrase is a mere ADJUNCT that
#    does NOT clear ("is tamper-resistant in the next release", "is tamper-evident on the roadmap",
#    "is signed with minisign for a future release" all FLAG). A preposition, "(", punctuation, a
#    coordinating/subordinating boundary (the forward window ends at one), or a finite verb between the match
#    and the copula breaks the adjacency, so "tamper-resistant with a new logo is planned" (preposition
#    first) and "release ships on our planned schedule" (a finite verb, no copula) FLAG. This FOLDS the former
#    standalone copula-less F4 (any forward roadmap phrase cleared), which laundered a present claim carrying
#    a trailing roadmap adjunct.
# F2 ("will|shall be" governing the match's own subject): the same head-noun-only run up to "will|shall be",
#    e.g. "Tamper-evidence will be added in a future release", "Releases will be signed with minisign". A
#    "will" that governs a DIFFERENT verb ("... release will give your team confidence") is NOT "will be" and
#    FLAGS.
# F3 (LEADING verbal future-intent that GOVERNS the tamper proposition): in the pre-match clause window a
#    future-intent verb phrase (plan|aim|intend|hope|expect|mean|prepare|aspire|set|going ... to [be|become|
#    make|have]), "planned|set|going|scheduled to be", or "will be|become|make|ship|sign|add|introduce", e.g.
#    "We plan to make releases tamper-resistant.", "Releases are planned to be tamper-resistant." The intent
#    must govern the tamper proposition itself: a reporting/relative construction between the intent phrase
#    and the match (F3_GOVERN_BREAK: saying/stating/that/which/..., or a second object-introducing verb) means
#    the intent governs a DIFFERENT object it reports on, so "We plan to make a logo saying releases are
#    tamper-resistant" FLAGS. A status word followed by a NOUN, not "to be" ("The planned release is
#    tamper-evident today"), is not a verbal intent and FLAGS.
_FUTURE_PREP = (r"with|for|in|on|at|by|of|from|into|onto|over|under|through|across|per|as|about|against|"
                r"between|among|during|without|within|to|upon|via|toward|towards|after|before")
# A head-noun-phrase token for the forward F1/F2 scan: a determiner/adjective/noun/hyphenated word (with its
# trailing whitespace) that is NOT a preposition, NOT will/shall, and NOT a be-copula, so the run from the
# match to the governing copula (or "will|shall be") crosses ONLY the matched noun's own head phrase.
_HEADNOUN = (r"(?:(?!(?:" + _FUTURE_PREP + r"|will|shall|is|are|was|were|be|been|being)\b)"
             r"[A-Za-z][\w'-]*\s+)")
FUTURE_F1 = re.compile(
    r"^\s*" + _HEADNOUN + r"*?(?:is|are|was|were|be|been|being)\b(?:\s+to\s+be)?"
    r"(?:\s+[A-Za-z]+ly\b)*(?:\s+[A-Za-z]+(?:ed|en))?(?:\s+[A-Za-z]+ly\b)*\s+"
    r"(?:planned|deferred|postponed|upcoming|forthcoming"
    r"|(?:in|for)\s+(?:a|an|the)\s+(?:next|future|later|upcoming|coming)\s+release\b"
    r"|on\s+the\s+roadmap\b)", re.IGNORECASE)
FUTURE_F2 = re.compile(r"^\s*" + _HEADNOUN + r"*?(?:will|shall)\s+be\b", re.IGNORECASE)
FUTURE_F3 = re.compile(
    r"\b(?:plan|aim|intend|hope|expect|mean|prepare|aspire|set|going)(?:s|ned|ning|ed)?\s+to\b"
    r"(?:\s+(?:be|become|make|have))?"
    r"|\b(?:planned|set|going|scheduled)\s+to\s+be\b"
    r"|\bwill\s+(?:be|become|make|ship|sign|add|introduce)\b", re.IGNORECASE)
# F3 clears only when the leading future-intent verb phrase GOVERNS the tamper proposition, not an object it
# merely reports on. A reporting/relative construction between the intent phrase and the match (saying,
# stating, that, which, ..., or a second object-introducing verb) means the intent governs a DIFFERENT noun
# ("We plan to make a logo saying releases are tamper-resistant" makes a logo, not the releases), so it
# FLAGS, while "We plan to make releases tamper-resistant" (make governs releases directly) still clears.
F3_GOVERN_BREAK = re.compile(
    r"\b(?:saying|stating|declaring|announcing|labeled|labelled|reading|titled|showing|indicating|"
    r"claiming|promising|calling|that|which|who|where)\b", re.IGNORECASE)
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

# Verbatim honest residual-disclosure phrases enumerated from the live corpus (never guessed): the ONE
# shipped residual is the release-build gate's, in .aiqt/core/gates/manifest.toml. A future-guarded match
# clears via the residual TRAILING path ONLY when its span falls INSIDE one of these phrases occurring
# verbatim in the text (_residual_disclosed), the same exact-phrase shape as _title_allowlisted. This
# REPLACES the former STRENGTH_DISCLAIMER heuristic, which over-cleared a trailing negator over a DIFFERENT
# property ("tamper-evident, not completely independent") and widened further under an adverb filler. It is
# fail-closed otherwise, so a bare "tamper-resistant, not cryptographically signed" now flags (it asserts
# tamper-resistance). Grow this list only from verbatim corpus residuals; like the vocabulary itself it is
# a maintainer-calibrated best-effort allowlist, not a complete one.
RESIDUAL_DISCLOSURES = (
    "keyless tamper-evident ordering within the anchored history, not cryptographic proof",
)

# The hand-authored repo prose roster (collector 2): the spec's named prose surfaces that are NOT
# gensrc-registered generated outputs. DISCLOSURE.md/CHANGELOG.md/ROADMAP.md/CLAUDE.md are registered and
# arrive through collector 3; these three are the hand-authored remainder. Every path is REQUIRED (absent
# = exit 2). Nothing is dormant today, so this is a single declared constant, not idle dormant/armed
# machinery.
REPO_PROSE_ROSTER = ("README.md", "SCOPE.md", "SYSTEM-HARDENING.md")

# (name, pattern, guard): guard is "" (none), "neg" (skip when a negator is in the pre-match clause
# window), "intent" (skip when a negator OR an intent hedge is in that window), "sharealike" (skip only
# when the later-version AND BY-SA-compatible alternatives are both named in the surrounding sentence), or
# "future" (skip when an allowlisted third-party title, a tight NEGATION disclosure N1-N3, or a tight FUTURE
# construction F1-F3 bound to the match clears it; see _guard_clears).
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
# entry is FUTURE-guarded (an allowlisted title overlapping the match, a residual disclosure, or a future
# predicate clears; see _guard_clears "future"). Like the guarantee-flavoured set, this vocabulary is a
# maintainer-calibrated best-effort deny-list: it cannot catch every novel paraphrase, so it is grown when
# a new class is found rather than read as complete.
RELEASE_PATTERNS = [
    # Achieved-tense tamper evidence/resistance/proof/detectability. The narrow CLAIM-form shape (evident/
    # evidence/resistant/resistance/proof/detectable) deliberately does NOT match the attack-noun
    # "tampering"/"tampered", so an honest control title ("Dependency Tampering") or attack description ("a
    # hand-tampered registry") is not flagged. "detectable" is the adjective sibling of the others ("the
    # release is tamper-detectable"), the property-claim form the "tamper detection" pattern below (which
    # needs a detect... noun/verb) does not reach. FUTURE-guarded: "an independent, tamper-evident anchor is
    # planned for a future release" clears; "This layer does not provide tamper evidence" clears via the
    # residual-disclosure negator.
    ("tamper evidence/resistance/proof (achieved)", re.compile(
        r"\btamper[- ]?(?:evident|evidence|resistant|resistance|proof|detectable)\b", re.IGNORECASE),
        "future"),
    # Achieved-tense "tamper detection". Requires "tamper" immediately followed (via a hyphen OR
    # whitespace, so the hyphenated "tamper-detection" is caught too, F-VC5-E) by a "detect..." form, so
    # the attack-noun "tampering" and "detects accident, not tamper" (detect BEFORE tamper) do not match.
    ("tamper detection (achieved)", re.compile(
        r"\btamper[-\s]+detect(?:ion|s|ed|ing)?\b", re.IGNORECASE), "future"),
    # Achieved-tense tampering-detection in the ACTIVE ("detects tampering", "release detects tampering",
    # with an optional adjective/determiner run (up to two words: any|all|the, "malicious", ...) between the
    # detect verb and "tampering" so "detects any tampering" and "detects malicious tampering" are caught),
    # the NOUN form ("provides tampering detection"), and the PASSIVE ("tampering
    # is detected", "tampering
    # can be detected") voice, where the attack-noun "tampering" is the SUBJECT/OBJECT of a detect claim.
    # This is a CLAIM to detect tampering, so unlike the bare attack-noun it is matched; it still needs a
    # detect verb or the "tampering detection" noun adjacent to "tampering", so an honest control title
    # ("Dependency Tampering") and an attack description carry neither and stay clean. The passive form also
    # admits a present-capability modal ("can/could/may/might be detected"), a present claim that tampering
    # is caught; it stays FUTURE-guarded, so "tampering can be detected is planned for a future release"
    # still clears. FUTURE-guarded.
    ("tampering detection (achieved)", re.compile(
        r"\bdetect(?:s|ed|ing)?\s+(?:\w+\s+){0,2}?tampering\b|\btampering\s+detection\b|"
        r"\btampering\s+(?:is|are|was|were|gets?|(?:can|could|may|might)\s+be)\s+detected\b",
        re.IGNORECASE), "future"),
    # Achieved-tense independent integrity channel/anchor/reference (the 5.6-deferred anchor). Requires the
    # word order "independent ... channel|anchor|reference" within two words, so "a channel independent of
    # artefact delivery" (SECI-release-integrity) does not match. FUTURE-guarded.
    ("independent channel/anchor/reference (achieved)", re.compile(
        r"\bindependent,?\s+(?:\w+[- ]){0,2}?(?:channel|anchor|reference)\b",
        re.IGNORECASE), "future"),
    # The REVERSE word order "verified ... channel ... independent of" (the SECI-release-integrity example
    # phrasing), which the "independent ... channel" pattern above misses. Keyed on an ACHIEVED-verification
    # verb (verify/verifies/verified/verifying, NOT the capability form "verifiable" or "verification"), so
    # the obligation prose that describes the standard rather than claiming it ("a digest published through
    # an authenticated channel independent of artefact delivery", verb "published"; "integrity rests on a
    # ... digest published through a channel independent of the download", verb "rests") stays clean.
    # FUTURE-guarded.
    ("verified via channel independent of (achieved)", re.compile(
        r"\bverif(?:y|ies|ied|ying)\b[^.]{0,40}?\bchannel\b[^.]{0,25}?\bindependent\b",
        re.IGNORECASE), "future"),
    # The COPULA reverse "<channel|anchor|reference>(s) is/are/remains/stays independent of ..." (the
    # achieved reverse form the verif-keyed pattern above misses because it carries no verification verb).
    # Keyed on a linking copula (with an optional adverb before "independent", so "channel remains fully
    # independent of ..." is caught) before "independent of", so the ACHIEVED assertions "The integrity
    # channel is independent of artefact delivery", "The integrity anchor is independent of release
    # publication", and "The channels are independent of the download" all trip, while the obligation prose
    # that only DESCRIBES the standard as an adjectival post-modifier with NO copula ("a channel independent
    # of artefact delivery" (SECI-release-integrity), "a channel independent of the download" (RELEASING))
    # carries no "is/are/remains/stays independent of" and stays clean. FUTURE-guarded.
    ("channel/anchor/reference is independent of (achieved)", re.compile(
        r"\b(?:channel|anchor|reference)s?\s+(?:is|are|remains?|stays?)\s+(?:\w+ly\s+)?independent\s+of\b",
        re.IGNORECASE), "future"),
    # The stale keyed-signing claim D1 retired, in the PASSIVE ("releases are/is/was/were/get signed", an
    # adverb may sit between the copula and "signed", so "release is cryptographically signed" and "release
    # was cryptographically signed" are caught), the ACTIVE present ("we|aiqt sign(s) ... releases ...", kept
    # minisign/cryptographic-scoped so it needs a minisign/cryptographic token nearby, catching "We sign
    # releases with minisign" and "AIQT signs each release automatically using minisign" while honest prose
    # that merely says "sign" stays clean), the PRE-VERBAL adverb form where the crypto scope sits BEFORE the
    # verb with a generic subject ("cryptographically sign(s) ... releases", so "We cryptographically sign
    # releases" and "AIQT cryptographically signs every release" are caught), plus "signed with minisign",
    # "Minisign-signed", or "carry Minisign signatures". FUTURE-guarded. The corrected CLAUDE.md wording (4.4d)
    # lands in the same change, so this pattern's only current hit is removed together with the pattern's
    # introduction.
    ("releases are signed (stale signing claim)", re.compile(
        r"\breleases?\s+(?:are|is|was|were|get|gets)\s+(?:\w+ly\s+)?signed\b|\bsigned\s+with\s+minisign\b|"
        r"\bminisign[-\s]signed\b|\bminisign\s+signatures?\b|"
        r"\b(?:we|aiqt)\s+signs?\s+(?:\w+\s+){0,3}?releases?\b[^.]{0,40}?"
        r"\b(?:minisign|cryptographic(?:ally)?)\b|"
        r"\bcryptographically\s+signs?\s+(?:\w+\s+){0,3}?releases?\b",
        re.IGNORECASE), "future"),
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


def _future_hedge_window(text, end):
    """The FORWARD part of the clause AFTER the match, for the F1/F2 forward checks: from the match END to
    the first FUTURE_WINDOW_BOUNDARY after it. Only the forward half counts, because a roadmap predicate must
    GOVERN the matched tamper/release noun as its copular predicate, which sits AFTER it ("an independent,
    tamper-evident anchor is planned for a future release", "Tamper-evidence will be added in a future
    release"). A future word in the PRE-match part is a leading attributive pre-modifier of a DIFFERENT noun
    ("The planned release is tamper-evident today") and must NOT launder the present claim, so it is excluded
    by construction. Because FUTURE_WINDOW_BOUNDARY breaks on a coordinating and/or/plus/so AND on a
    subordinator, "The anchor is tamper-evident and a further channel is planned" (bare "and") cuts "planned"
    out of the forward window, and "Releases are tamper-resistant according to our roadmap" / "... because the
    next release adds documentation" cut the hedge out at "according"/"because", so the predicate must
    directly follow the matched noun to clear it."""
    nxt = FUTURE_WINDOW_BOUNDARY.search(text, end)
    return text[end:nxt.start() if nxt else len(text)]


def _future_leading_window(text, start):
    """The pre-match clause window for the F3 leading future-intent check: from the last
    FUTURE_WINDOW_BOUNDARY (subordinator-aware) before the match to the match. This binds a leading intent
    verb to the same clause, so "We plan to make releases tamper-resistant" clears while a "will be" or
    "plan to" in a prior clause or sentence (cut off at the boundary) does not reach a fresh claim ("The
    anchor is planned for a future release. It is tamper-evident today." flags: the period bounds it out)."""
    left = 0
    for m in FUTURE_WINDOW_BOUNDARY.finditer(text, 0, start):
        left = m.end()
    return text[left:start]


def _future_clears(text, start, end):
    """True when one of the three TIGHT proposition-bound FUTURE constructions binds a roadmap sense to the
    matched tamper/release noun (F1 copula-bound status predicate the match's own copula points to, folding in
    the former standalone roadmap phrase; F2 "will|shall be"; F3 leading verbal intent that GOVERNS the tamper
    proposition, not a DIFFERENT object it reports on). Anything else FLAGS: a bare status adjective on a
    forward or leading DIFFERENT noun, a future predicate whose subject is a different noun, a present copula
    with a trailing roadmap ADJUNCT, an intent governing a reported object (F3_GOVERN_BREAK), or a future word
    across a preposition, paren, or finite verb from the match cannot launder a present claim. A novel honest
    forward phrasing that misses all three may flag by design (the safe direction)."""
    forward = _future_hedge_window(text, end)
    if FUTURE_F1.match(forward) or FUTURE_F2.match(forward):  # F1 / F2: copula-adjacent, no intervening prep
        return True
    lead_window = _future_leading_window(text, start)  # F3: leading verbal future-intent that GOVERNS the match
    m3 = None
    for mm in FUTURE_F3.finditer(lead_window):
        m3 = mm  # the future-intent phrase CLOSEST to the match governs it
    return bool(m3 and not F3_GOVERN_BREAK.search(lead_window[m3.end():]))


def _residual_disclosed(text, start, end):
    """True when the matched span [start, end) falls INSIDE a verbatim (exact, case-sensitive) occurrence of
    an allowlisted RESIDUAL_DISCLOSURES phrase, the honest downgrade the live corpus ships. This is the
    residual TRAILING clearance path, the exact-phrase replacement for the removed STRENGTH_DISCLAIMER
    heuristic: it clears the tamper-evident match inside "keyless tamper-evident ordering within the anchored
    history, not cryptographic proof", but NOT a bare "tamper-resistant, not cryptographically signed" (no
    allowlisted phrase occurs), which now flags. Fail-closed on anything not verbatim on the list."""
    for phrase in RESIDUAL_DISCLOSURES:
        pos = text.find(phrase)
        while pos != -1:
            if pos <= start and end <= pos + len(phrase):  # the match sits inside the residual phrase
                return True
            pos = text.find(phrase, pos + 1)
    return False


def _disclosure_clears(text, start, end):
    """True when a NEGATION disclosure, bound to the MATCHED proposition, clears a future-guarded match. This
    is the TIGHT allowlist, NOT the former "any negator in the pre-match window clears": a negator on a
    different word or modifier, an oblique mid-clause "no <noun>", or an emphatic idiom now FLAGS. Three rules:
      - N1 (SUBJECT-QUANTIFIER): the pre-match clause window (from the last subordinator-aware
        FUTURE_WINDOW_BOUNDARY to the match), left-stripped, BEGINS with no|neither|none, that quantifier is
        not an emphatic idiom (no doubt / question / way / ...), AND it DIRECTLY determines the tamper copula's
        subject: no reporting/embedding verb (doubt|believe|say|think|...) and no "that" complementizer
        (REPORTING_EMBED) between the quantifier and the match. It clears even with a be-copula between ("No
        release is tamper-evident", "Neither release is tamper-resistant" assert no such release exists). It
        clears ONLY as the LEADING clause token that governs the tamper subject, e.g. "Releases with no
        downtime are tamper-evident" (window begins with "Releases"), "There is no doubt that ...
        tamper-resistant" (emphatic idiom), and "No customers doubt that releases are tamper-resistant" ("No"
        quantifies "customers"; the tamper claim is embedded under "doubt that") all keep FLAGGING.
      - N2 (DIRECT integrity-predicate negation): the closest pre-match negator negates an integrity predicate
        governing the tamper term, i.e. the text between the negator and the match has NO be-copula and is
        empty/adverbs-only or an adverb run then an integrity verb (INTEGRITY_NEG_TAIL). So "This layer does
        not provide tamper evidence" and "is not tamper-resistant" clear, while "We do not doubt that releases
        provide tamper evidence" ("doubt" is not an integrity verb) and "The not-expensive release is
        tamper-resistant" (a modifier, plus an intervening copula) FLAG. The window breaks on a comma,
        semicolon, coordinating and/or/plus/so, contrastive, sentence punctuation, and a subordinator, so a
        leading preamble, a separate coordinate/semicolon clause, a "so" result clause, or a "because" clause
        does not reach across ("Without any doubt, ..."; "Deployment is not automatic; ..."; "We do not sign
        so ..."; "Releases are not expensive because they are tamper-resistant" all flag).
      - N3 (TRAILING residual): the match's span falls inside a verbatim allowlisted residual phrase
        (_residual_disclosed, RESIDUAL_DISCLOSURES), so "..., not cryptographic proof" inside the shipped
        residual clears while "..., not merely checksum-protected", "..., not expensive", and a bare "..., not
        cryptographically signed" leave the claim FLAGGED. Fail-closed otherwise."""
    left = 0
    for m in FUTURE_WINDOW_BOUNDARY.finditer(text, 0, start):
        left = m.end()
    window = text[left:start]
    # N1: a bare subject quantifier as the LEADING token, not an emphatic idiom, and directly determining the
    # tamper copula's subject (no reporting/embedding verb or "that" between the quantifier and the match).
    stripped = window.lstrip()
    lead = re.match(r"[A-Za-z]+", stripped)
    if (lead and lead.group().lower() in SUBJECT_QUANTIFIER
            and not EMPHATIC_IDIOM_NOUN.match(stripped[lead.end():])
            and not REPORTING_EMBED.search(stripped[lead.end():])):
        return True
    # N2: the closest negator directly negates an integrity predicate governing the tamper term (no
    # intervening be-copula; only adverbs or an integrity verb between the negator and the match).
    neg = None
    for m in NEGATOR.finditer(window):
        neg = m  # the negator CLOSEST to the match governs
    if neg is not None:
        tail = window[neg.end():]  # text between the governing negator and the match
        if not BE_COPULA.search(tail) and INTEGRITY_NEG_TAIL.match(tail):
            return True
    # N3: the existing trailing residual-disclosure exact-phrase allowlist.
    return _residual_disclosed(text, start, end)


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
    permitted set from LICENSE 3(b)(1). "future" (release-integrity): a shipped third-party control TITLE
    that WHOLLY CONTAINS the matched span, OR a tight NEGATION disclosure bound to the match (N1 leading
    subject quantifier, N2 direct integrity-predicate negation, or N3 the trailing residual allowlist, see
    _disclosure_clears), OR one of the tight FUTURE constructions F1-F3 bound to the match (_future_clears).
    "" never clears."""
    if guard == "neg":
        return bool(NEGATOR.search(_clause_window(text, m.start())))
    if guard == "intent":
        window = _clause_window(text, m.start())
        return bool(NEGATOR.search(window) or INTENT_HEDGE.search(window))
    if guard == "sharealike":
        window = _sentence_window(text, m.start(), m.end())
        return bool(LATER_ALT.search(window) and COMPAT_ALT.search(window))
    if guard == "future":
        if _title_allowlisted(text, m.start(), m.end()):
            return True
        if _disclosure_clears(text, m.start(), m.end()):
            return True
        return _future_clears(text, m.start(), m.end())
    return False


def scan(text, site=True):
    """Return a list of (pattern_name, snippet) overclaim findings in one surface's text. RELEASE_PATTERNS
    (release-integrity) always run; the guarantee-flavoured SITE_PATTERNS run only when `site` is True (the
    site pages), because the rule corpus and generated adapters legitimately carry that governance
    vocabulary."""
    findings = []
    patterns = (SITE_PATTERNS + RELEASE_PATTERNS) if site else RELEASE_PATTERNS
    for name, pat, guard in patterns:
        for m in pat.finditer(text):
            if _guard_clears(guard, text, m):
                continue
            findings.append((name, _snippet(text, m.start(), m.end())))
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
# lines MUST stay clean (honest copy, including the two borderline MECHANISM lines on the live site and
# the release-integrity residual disclosures the corrected copy relies on). The site-marketing lines are
# scanned with site=True; the release-integrity lines below it are exercised on both surface classes.
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
    # RELEASE-INTEGRITY positives (VER-CORE 4.4): achieved-tense claims with no future hedge / negator / title.
    "The release is tamper-evident.",                                 # bare tamper-evident claim
    "AIQT provides tamper detection for every release.",             # tamper detection
    "Adopters verify through an independent channel.",               # independent channel
    "Releases are signed with minisign.",                            # stale signing claim (the CLAUDE.md line 4.4d removes)
    "The anchor is tamper-evident, and a further channel is planned.",  # trailing-hedge laundering: "planned" is a SEPARATE clause after the comma
    "The anchor is planned for a future release. It is tamper-evident today.",  # prior-sentence future hedge must not launder a fresh present-tense claim
    # F-VC5-C: a future hedge in a SEPARATE coordinate clause (bare "and", no comma) must not launder.
    "The anchor is tamper-evident and a further channel is planned.",  # future window breaks on the coordinating "and"
    "The manifest is tamper-evident and an anchor will ship.",         # "will" is past the coordinating "and", out of the future window
    # F-VC5-D: a title that merely SHARES the sentence (not overlapping the matched span) must not clear.
    "Software Supply Chain Attacks & Dependency Tampering is mapped, and our releases are tamper-evident.",  # title co-occurs; the tamper-evident claim is elsewhere in the sentence
    "The controls that address Software Supply Chain Attacks & Dependency Tampering also provide tamper detection.",  # title in the sentence but not abutting "tamper detection" -> flags (was wrongly cleared)
    # F-VC5-B: a negator in a leading preamble or a separate coordinate/semicolon clause must not clear.
    "Releases are tamper-evident, and no anchor is independent yet.",   # "no" is in the following coordinate clause after "and"
    "Deployment is not automatic; releases are tamper-resistant.",      # "not" is in the preceding clause before the semicolon
    "Without any doubt, the system is tamper-resistant.",              # "Without" is a leading preamble past the comma
    # F-VC5-E: release-integrity vocabulary completeness.
    "Every release ships with tamper-detection.",                      # hyphenated tamper-detection (the old \s+ missed it)
    "The release detects tampering.",                                  # active tampering-detection
    "Tampering is detected on every release.",                         # passive tampering-detection
    "Releases are verified through a channel independent of artefact delivery.",  # reverse "verified ... channel ... independent of" word order
    "Every artefact is Minisign-signed.",                              # Minisign-signed
    "Releases carry Minisign signatures.",                             # carry Minisign signatures
    # Round-2 fixtures (guard over-clearing + vocab gaps). A trailing negator over a WEAKER or UNRELATED
    # term must not clear (a, b); a future word in a subordinate/attributive clause must not govern (c, d, e).
    "Releases are tamper-resistant, not merely checksum-protected.",    # (a) trailing negator negates checksum-protected, not the tamper claim
    "Releases are tamper-evident, not expensive.",                     # (b) trailing negator negates an unrelated adjective
    "Releases are tamper-evident, not remotely affordable.",           # adverb filler "remotely" before a NON-strength term must not open a hole
    "Releases are tamper-resistant according to our roadmap.",         # (c) future word "roadmap" in an attributive clause past "according"
    "Releases are tamper-resistant because the next release adds documentation.",  # (d) future word in a "because" subordinate clause
    "An independent anchor verifies every release today while the signing roadmap remains open.",  # (e) future word in a "while" subordinate clause
    "Every release is tamper-detectable.",                            # (f) adjective sibling of tamper-evident/resistant/proof
    "Tampering can be detected on every release.",                     # (g) modal-passive detection
    # Round-3 Class B: a FUTURE token that governs a DIFFERENT verb (not a copula-bound roadmap predicate of
    # the tamper noun) must NOT launder the present-tense tamper claim.
    "A tamper-evident release will give your team confidence.",        # "will" governs "give", not a future predicate -> flags
    "Every tamper-evident release we have published will remain verifiable.",  # "will" governs "remain" -> flags
    "The roadmap states releases are tamper-resistant today.",         # leading "roadmap" names the source; "today" is present -> flags
    # Round-3 Class B (tense-ambiguity): a BARE action verb (added/introduced/coming/scheduled) with no
    # explicit future marker reads PAST or RECURRING, so it must NOT clear the present-tense tamper claim.
    "The tamper-resistant design was added last year.",                # "was added" is PAST, no future marker -> flags
    "Tamper detection was introduced in version 1.",                   # "was introduced" is PAST -> flags
    "Our tamper-resistant pipeline is coming along nicely.",           # "is coming along" is progress, not future -> flags
    "Tamper-evident releases are scheduled every week.",               # "scheduled every week" is recurring present -> flags
    # Round-3 Class C1: a leading negator across a SUBORDINATOR does not bind to the matched claim.
    "Releases are not expensive because they are tamper-resistant.",   # "not" negates "expensive", bounded by "because" -> flags
    # Round-3 Class C2: with STRENGTH_DISCLAIMER removed, a trailing negator over a DIFFERENT property no
    # longer clears; only a verbatim RESIDUAL_DISCLOSURES phrase does. These assert tamper-evidence.
    "Releases are tamper-resistant, not cryptographically signed.",    # not the allowlisted residual -> flags (asserts tamper-resistance)
    "Our releases are tamper-evident, not completely independent.",    # different-property trailing negator -> flags
    "Releases are tamper-evident, not absolutely a cryptographic guarantee.",  # not the allowlisted residual -> flags
    # Round-3 Class D: added present-tense vocab.
    "Every release provides tampering detection.",                     # noun-form "tampering detection"
    "Each release is cryptographically signed.",                       # adverb between the release copula and "signed"
    "The integrity channel is independent of artefact delivery.",      # copula reverse "channel is independent of" (achieved)
    # Round-4 Fix B (future launder): a future-status adjective as a leading attributive PRE-modifier of a
    # DIFFERENT noun must NOT clear a present-tense claim; the roadmap predicate must sit AFTER the match.
    "The planned release is tamper-evident today.",                    # "planned" pre-modifies release; tamper asserted now
    "The upcoming release is signed with minisign.",                   # "upcoming" pre-modifier; present signing claim
    "Our forthcoming release is tamper-resistant.",                    # "forthcoming" pre-modifier
    "The forthcoming report confirms releases are tamper-resistant.",  # "forthcoming" modifies a different noun (report)
    # Round-4 Fix C (negation launder): a leading negator on a DIFFERENT word with a be-form copula between
    # it and the match, or a "so" result clause, does not launder the fresh copular tamper claim.
    "The not-expensive release is tamper-resistant.",                  # "not" negates price; "is" intervenes -> flags
    "There is no doubt that our releases are tamper-resistant today.", # emphatic idiom; "are" intervenes between "no" and the claim
    "We do not sign so releases are tamper-evident.",                  # "so" result-clause boundary; "not" is before "so"
    # Round-4 Fix D (title over-clear): "Tampering detection" overlaps the title's tail but is not WHOLLY
    # contained in it ("detection" falls outside the title), so the claim is the pack's and flags.
    "The Software Supply Chain Attacks & Dependency Tampering detection feature verifies every release.",
    # Round-4 Fix E (vocab): copula-reverse plural/linking for anchor and channel; active + past signing; a
    # determiner between the detect verb and "tampering".
    "The integrity anchor is independent of release publication.",     # copula reverse for "anchor"
    "The channels are independent of the download.",                   # plural + "are" copula reverse
    "We sign releases with minisign.",                                 # active present signing, minisign-scoped
    "AIQT signs each release automatically using minisign.",           # active present, determiner + minisign
    "Every release was cryptographically signed.",                     # past signing "was ... signed"
    "Every release detects any tampering.",                            # determiner "any" between detect verb and tampering
    # Round-5 launder-free restructure: the tight FUTURE/NEGATION allowlist FLAGS everything not bound to the
    # matched noun. A forward future word across a preposition, paren, or finite verb, a status word on a
    # DIFFERENT noun, and a negator on a modifier/oblique noun or emphatic idiom must all flag.
    "Releases are tamper-resistant with a new logo planned for next year.",  # "planned" across the preposition "with" -> F1 adjacency broken
    "Releases are tamper-resistant (a new logo is planned for next year).",  # a paren opens a new subject before the future word
    "Every tamper-resistant release ships on our planned schedule.",   # "ships" is a finite verb; no copula reaches "planned"
    "Our tamper-evident pipeline follows the postponed audit calendar.",  # "postponed" modifies a different noun, no copula
    "The tamper-evident anchor planned earlier now ships to every adopter.",  # "planned earlier" is a post-modifier, not a copular predicate
    "We do not doubt that releases provide tamper evidence.",          # "doubt" is not an integrity verb -> N2 fails
    "Releases with no downtime are tamper-evident.",                   # oblique "no downtime": the window does not BEGIN with the negator
    "A release with no caveats is tamper-evident.",                    # oblique "no caveats": not a leading subject quantifier
    # Round-6: N1/F3/F4 must GOVERN the tamper proposition (an embedded/oblique noun or a trailing roadmap
    # adjunct on a present claim must not launder) + the B4 signing/channel/detection vocab inflections.
    "No customers doubt that releases are tamper-resistant today.",     # N1: "No" quantifies "customers"; the tamper claim is embedded under "doubt that" -> flags
    "We plan to make a logo saying releases are tamper-resistant today.",  # F3: the intent makes "a logo"; "saying" reports a DIFFERENT noun's tamper claim -> flags
    "Releases are tamper-resistant today with new branding in a future release.",  # F4 fold: present "are tamper-resistant"; the roadmap phrase is an adjunct on "branding" -> flags
    "Every release is tamper-resistant in the next release.",           # F4 fold: present "is tamper-resistant"; "in the next release" is an adjunct -> flags
    "Releases are already tamper-evident on the roadmap.",              # F4 fold: present "are ... tamper-evident"; "on the roadmap" is an adjunct -> flags
    "Releases are signed with minisign for a future release.",          # F4 fold: present "are signed"; trailing "for a future release" is an adjunct -> flags
    "We cryptographically sign releases.",                             # B4: pre-verbal signing adverb, generic subject
    "AIQT cryptographically signs every release.",                     # B4: pre-verbal signing adverb + generic object
    "The integrity channel remains fully independent of artefact delivery.",  # B4: an adverb between the linking verb and "independent"
    "Each release detects malicious tampering.",                       # B4: an adjective between the detect verb and "tampering"
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
    # RELEASE-INTEGRITY negatives (VER-CORE 4.4): the corrected copy the gate must NOT flag.
    "Releases ship with a per-file manifest and a published ROOT digest so an adopter can verify their copy is intact; an independent, tamper-evident anchor is planned for a future release.",  # CRITICAL: the exact approved 4.4d sentence -- a gate that fails on its own corrected text is the failure mode this pins
    "This layer does not provide tamper evidence; it detects accidents.",                # residual-disclosure negator (pre-match)
    "The chronology layer is keyless tamper-evident ordering within the anchored history, not cryptographic proof.",  # the release-build gate residual disclosure: negator "not" past a plain comma clears it
    "A released or published artefact ships with a signature verifiable against an authenticated maintainer key, or a digest published through an authenticated channel independent of artefact delivery.",  # SECI-release-integrity: "channel ... independent", not "independent ... channel"; no tamper token
    "The independent anchor will be implemented in the next release.",                   # 5.2/5.6 future-tense roadmap disclosure
    "an independent, tamper-evident anchor is planned for a future release",             # the 4.4d clause embedded mid-paragraph
    "MCP04: Software Supply Chain Attacks & Dependency Tampering (tight) is one mapped risk.",  # the shipped mappings title in context stays clean (narrow patterns do not match the attack-noun)
    "A release's integrity rests on a SHA-256 digest published through a channel independent of the download.",  # RELEASING obligation prose: reverse "channel ... independent" but no achieved-verification verb -> stays clean
    "The control addresses Dependency Tampering and detects accidents in transit.",      # attack-noun "Tampering" co-occurs with a detect verb but not adjacent as "detects tampering" -> stays clean
    # Round-2 must-clear invariants exercising the tightened paths (invariant 1, the 4.4d sentence, and
    # invariant 4, the bare roadmap clause, are already pinned above).
    "keyless tamper-evident ordering within the anchored history, not cryptographic proof",  # (invariant 2) the verbatim RESIDUAL_DISCLOSURES phrase clears the match inside it
    "This layer does not provide tamper evidence",                                       # (invariant 3) pre-match negator (bare form)
    # Round-3 Class B must-clear: a copula-bound roadmap PREDICATE of the tamper noun still clears.
    "Tamper-evidence will be added in a future release.",                                # "will be" + "added" + "in a future release" -> future predicate clears
    # Round-3 Class D must-clear: the adjectival "channel independent of" (no copula) stays clean, so the
    # copula-reverse pattern does not regress the SECI/RELEASING obligation prose.
    "The digest travels over a channel independent of artefact delivery.",               # "channel independent of" has no "is independent of" copula -> stays clean
    # Round-4 Fix B must-clear: a copula-bound roadmap PREDICATE occurring AFTER the tamper noun still clears.
    "Tamper detection is to be introduced in a future release.",                          # "to be introduced" + "in a future release" follow the match -> clears
    "An independent, tamper-evident anchor is upcoming.",                                 # "upcoming" is the forward copular predicate of the anchor -> clears
    # Round-4 Fix C carve-out: a bare SUBJECT QUANTIFIER (no|neither) negating the release noun clears even
    # with a copula between it and the match; it is honest (no such release exists), not an emphatic idiom.
    "No release is tamper-evident.",                                                      # "No" negates the subject noun -> clears
    "Neither release is tamper-resistant.",                                               # "Neither" negates the subject noun -> clears
    # Round-5 launder-free restructure must-clear: the tight FUTURE allowlist still clears the bound
    # constructions. F2 "will be" governing the subject, F3 leading verbal future-intent ("plan to",
    # "planned to be"), and the adjectival "channel independent of" (no copula) that stays clean.
    "Releases will be signed with minisign.",                                             # F2/F3: leading "will be" governs the signing claim
    "We plan to make releases tamper-resistant.",                                         # F3: leading "plan to make" future-intent
    "Releases are planned to be tamper-resistant.",                                       # F3: "planned to be" verbal future-intent
    "Releases are planned to be signed with minisign.",                                   # F3: "planned to be" governs the signing claim
    "published through an authenticated channel independent of artefact delivery",        # reverse "channel independent" with no copula/verif verb -> no match
    "channel independent of artefact delivery",                                           # bare adjectival post-modifier -> no match
    # Round-6 must-clear: the copula-bound roadmap predicate (F1 fold) still clears "on the roadmap" and the
    # "is to be introduced in a future release" future construction folded out of the removed standalone F4.
    "Tamper resistance is on the roadmap.",                                                # F1: "is on the roadmap" is the match's OWN copular predicate -> clears
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


if __name__ == "__main__":
    sys.exit(main())
