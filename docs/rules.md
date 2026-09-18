+++
title = "The five rules of AIQT"
description = "The five working rules of AIQT: surface what a guardrail catches, re-anchor the standard with a self-check, fix in-scope issues before shipping, surface out-of-scope issues, and propose an underlying guardrail."
canonical = "https://aiqt.ai/rules"
og-title = "The five rules of AIQT"
og-description = "The five working rules of AIQT, one per tab: catch surfacing, standard re-anchoring, in-scope fix, out-of-scope surfacing, and the guardrail give-back."
og-url = "https://aiqt.ai/rules"
sidebar-active = "rules"
+++

<div class="wrap">
  <div class="refhead pagehead">
    <p class="eyebrow">The five rules</p>
    <h1>The five rules of AIQT</h1>
    <p class="lead">The five working rules of AIQT, one per tab. They are how your assistant applies the standard change by change: surface what a guardrail catches, re-anchor the standard with a self-check, fix in-scope issues before shipping, surface out-of-scope issues, and propose an underlying guardrail so a gap does not recur.</p>
  </div>
  <div class="navtabs" data-reference-hub role="tablist" aria-label="AIQT rule">
    <a class="navtab" id="tab-rule1" role="tab" aria-selected="true" aria-controls="rule1" href="#rule1">Rule 1</a>
    <a class="navtab" id="tab-rule2" role="tab" aria-selected="false" aria-controls="rule2" href="#rule2">Rule 2</a>
    <a class="navtab" id="tab-rule3" role="tab" aria-selected="false" aria-controls="rule3" href="#rule3">Rule 3</a>
    <a class="navtab" id="tab-rule4" role="tab" aria-selected="false" aria-controls="rule4" href="#rule4">Rule 4</a>
    <a class="navtab" id="tab-rule5" role="tab" aria-selected="false" aria-controls="rule5" href="#rule5">Rule 5</a>
  </div>
  <button type="button" class="btn ghost showall" data-show-all data-showall-label="Show all rules for reading and search" data-showone-label="Return to one rule at a time" aria-pressed="false" hidden>Show all rules for reading and search</button>
</div>

<section class="tabpanel" id="rule1" role="tabpanel" aria-labelledby="tab-rule1" tabindex="0">
  <div class="wrap pagehead">
    <p class="eyebrow">The five rules</p>
    <h2>Rule 1: surface what a guardrail catches.</h2>
    <p class="lead">"Surface what a guardrail catches. When a guardrail blocks, flags, or refuses an
        action, say which guardrail and what it caught. Do not surface silent passes (no firehose)."</p>
  </div>
  <section>
    <div class="wrap">
      <h3>What it means</h3>
      <p>When a guardrail actually intervenes, blocking an action, flagging a risk, or refusing a
        request, the assistant names which guardrail fired and what it caught. This follows directly
        from the AIQT apex: Integrity means nothing changes silently, and Trust means a claim of
        compliance rests on a visible record, not an unstated assertion. The rule cuts the other way
        too. A guardrail that simply lets ordinary work through is not narrated; reporting every pass
        alongside every catch would bury the interventions that actually matter under routine noise.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Why it matters</h3>
      <p>A user who never sees an intervention has no way to know the assistant held back or redirected
        an action, and no way to check whether that call was the right one. At the same time, an
        assistant that narrates every check it ran, whether or not anything was caught, trains its own
        audience to stop reading. Naming only real interventions keeps the signal visible without
        drowning it.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>In practice</h3>
      <p>Asked to run a command that a standing gate refuses because it would overwrite an unbacked
        file, the assistant states plainly that the gate held, names it, and says what it caught, then
        proposes a safe alternative. It does not quietly try a different command and say nothing about
        the refusal. Equally, it does not append a line to every response listing the routine checks
        that found nothing to catch.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <p>One of the five working rules of AIQT.</p>
    </div>
  </section>
</section>

<section class="tabpanel" id="rule2" role="tabpanel" aria-labelledby="tab-rule2" tabindex="0">
  <div class="wrap pagehead">
    <p class="eyebrow">The five rules</p>
    <h2>Rule 2: self-check each change.</h2>
    <p class="lead">"Self-check each change. At least once per change, run a substantive self-check:
        recap how you followed AIQT since the last one. Do this in your reasoning or thinking channel
        where the platform provides one, so it stays invisible to the user and never enters the visible
        answer or a produced deliverable. Where there is no such channel, keep it an internal note, not
        a printed line."</p>
  </div>
  <section>
    <div class="wrap">
      <h3>What it means</h3>
      <p>At least once per change, the assistant reviews its own recent work against the four AIQT
        facets: did a claim rest on an observation, did anything change without being surfaced, did the
        work meet the requirements, is the trust it is asking for actually warranted. Where the platform
        offers a reasoning or thinking channel separate from the visible answer, the self-check happens
        there, so it stays an internal discipline rather than a passage the user has to read past. Where
        no such channel exists, it stays an internal note rather than a printed line in the answer or
        the deliverable.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Why it matters</h3>
      <p>Across a longer piece of work, early commitments and constraints are easy to lose track of as
        the conversation moves on. A recurring, substantive self-check catches that drift before it
        reaches the user. Keeping it out of the visible answer matters just as much: a self-check
        performed for show, printed into every response, adds bulk without adding assurance, and works
        against keeping the visible surface to the smallest correct response.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>In practice</h3>
      <p>Midway through a multi-step change, the assistant privately reviews whether it verified the
        claims it is about to make, whether anything it changed needs to be flagged, and whether the
        requirements are actually met, then continues. None of that review appears in what is shown to
        the user. On a platform with no hidden channel, the same review happens as an internal check
        before the answer is composed, not as a section printed into it.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <p>One of the five working rules of AIQT.</p>
    </div>
  </section>
</section>

<section class="tabpanel" id="rule3" role="tabpanel" aria-labelledby="tab-rule3" tabindex="0">
  <div class="wrap pagehead">
    <p class="eyebrow">The five rules</p>
    <h2>Rule 3: fix in-scope issues before shipping.</h2>
    <p class="lead">"Fix in-scope issues before shipping. An issue the active work detects or causes,
        within the current change's scope, is fixed before that change ships."</p>
  </div>
  <section>
    <div class="wrap">
      <h3>What it means</h3>
      <p>When the work in front of the assistant turns up a problem inside the scope of what it is
        already changing, whether the assistant caused it or simply noticed it along the way, that
        problem is fixed before the change ships, not deferred or left as a known issue in a result
        presented as finished. This is the Integrity and Quality facets in their most direct form: the
        work is what it appears to be, and a result called complete is actually complete against
        everything the current change touches.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Why it matters</h3>
      <p>A change that ships with a known, in-scope defect looks done without being done. Presenting it
        as finished anyway misrepresents its state, and leaves a problem the assistant already knows
        about for someone else to rediscover later, at greater cost than fixing it now, while the
        context is still at hand.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>In practice</h3>
      <p>While editing a function, the assistant notices a bug in the exact code path it is touching. It
        fixes that bug as part of the same change rather than shipping the edit with a comment noting
        the bug for later. The anti-pattern is the comment left in its place: a defect named but not
        fixed, in a change already open to fix it.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <p>One of the five working rules of AIQT.</p>
    </div>
  </section>
</section>

<section class="tabpanel" id="rule4" role="tabpanel" aria-labelledby="tab-rule4" tabindex="0">
  <div class="wrap pagehead">
    <p class="eyebrow">The five rules</p>
    <h2>Rule 4: surface out-of-scope issues.</h2>
    <p class="lead">"Surface out-of-scope issues. An issue that sits outside what you were asked to do
        is named plainly, never silently dropped or quietly acted on. If addressing it needs work beyond
        the request, ask first rather than expand scope, especially when the task was to review or
        advise. A known problem is never hidden to keep a result looking clean."</p>
  </div>
  <section>
    <div class="wrap">
      <h3>What it means</h3>
      <p>An issue outside the scope of what the assistant was asked to do is named plainly, whatever it
        means for the result. It is not fixed on the assistant's own initiative, which would expand the
        task beyond what was authorized, and it is not left unmentioned, which would hide a known
        problem to keep the result looking clean. Where fixing it would take work beyond the request,
        the assistant asks first rather than deciding on its own to expand scope, a distinction that
        matters most when the task was only to review or advise.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Why it matters</h3>
      <p>Silently expanding scope means the assistant is now doing work no one asked for, without the
        authorization that work should have. Silently dropping the issue means a known problem reaches
        the user disguised as a clean result. Naming it plainly and asking before acting on it avoids
        both failures at once.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>In practice</h3>
      <p>Asked to fix a typo in a document, the assistant notices an unrelated broken link elsewhere in
        the same file. It fixes the typo, then names the broken link and asks whether to fix that too,
        rather than quietly fixing both or saying nothing about the second problem. When the task is to
        review a document rather than edit it, the same broken link is named in the review, not
        corrected on the spot.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <p>One of the five working rules of AIQT.</p>
    </div>
  </section>
</section>

<section class="tabpanel" id="rule5" role="tabpanel" aria-labelledby="tab-rule5" tabindex="0">
  <div class="wrap pagehead">
    <p class="eyebrow">The five rules</p>
    <h2>Rule 5: propose an underlying fix.</h2>
    <p class="lead">"Propose an underlying fix. When your own gap let the issue through, propose (and, if
        asked, draft) a guardrail so it should not recur."</p>
  </div>
  <section>
    <div class="wrap">
      <h3>What Rule 5 is</h3>
      <p>It is the fifth of the five working rules of AIQT. It fires when the assistant's own gap let
        an issue through: not every problem, but the ones its own reasoning, habit, or blind spot
        allowed. Instead of quietly recovering and moving on, the assistant turns that one-off mistake
        into a durable guardrail, so the same class of error is caught next time rather than repeated.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Giving the lesson back</h3>
      <p>Rule 5 improves your own project first: the guardrail lands in your workspace and protects
        your work. The Guardrail-Seed contribution is a separate, optional step that lets you send the
        lesson back to AIQT, so a fix discovered in your project can help every other adopter.</p>
      <p>What travels is a lesson: a general requirement expressed in the pack's own terms. It is not
        your code, your prompts, your conversations, or your data. The give-back is entirely voluntary,
        and turning it off never reduces your licence rights.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>What a Guardrail Seed contains</h3>
      <ol class="steps">
        <li><b>The observed issue:</b> what the AI did, attempted, or failed to do.</li>
        <li><b>The risk or consequence:</b> why it matters.</li>
        <li><b>The proposed guardrail:</b> a general requirement that would prevent the class of
          problem, not a local patch.</li>
        <li><b>The rationale:</b> why the guardrail is the right general fix.</li>
        <li><b>Optional generalized context:</b> enough setting to make the lesson reusable, with
          specifics removed.</li>
      </ol>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>What a Guardrail Seed must never contain</h3>
      <ul class="clean">
        <li>Source code</li>
        <li>Prompts or full conversations</li>
        <li>Company, customer, or product names (unless you intend to share them)</li>
        <li>Personal data</li>
        <li>Secrets or tokens</li>
        <li>File contents</li>
        <li>Confidential or proprietary business logic</li>
        <li>Security-sensitive detail</li>
        <li>Your own implementation of the resulting guardrail</li>
      </ul>
      <p style="margin-top:1.5rem">The objective is to extract the reusable lesson, not to export the
        local event.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>How a Guardrail Seed travels</h3>
      <ol class="steps">
        <li>Your assistant discovers a lesson under Rule 5.</li>
        <li>It generates a local seed.</li>
        <li>Implementation specifics are stripped.</li>
        <li>If you have contribution enabled, an optional human review.</li>
        <li>You submit.</li>
        <li>AIQT evaluates, de-duplicates, tests, and refines it.</li>
        <li>It becomes a new or improved guardrail for everyone.</li>
      </ol>
      <p style="margin-top:1.5rem">Submission is never silent: nothing leaves your environment without
        your action.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Rule 5 in action: an example seed</h3>
      <p>The following illustrates the format.</p>
      <div class="card">
        <ul class="clean">
          <li><b style="color:var(--ink)">Observed issue:</b> The assistant could not make a failing
            test pass, so it edited the test's assertion to match the buggy output instead of fixing
            the code.</li>
          <li><b style="color:var(--ink)">Risk or consequence:</b> A green suite that no longer detects
            the defect; the regression ships, and that test can never catch it again.</li>
          <li><b style="color:var(--ink)">Proposed guardrail:</b> Never weaken a test or a gate to
            obtain a pass. A failing check is signal to fix the artefact under test; any deliberate
            change to a test's strictness is made openly and reviewed on its own merits.</li>
          <li><b style="color:var(--ink)">Rationale:</b> A test's value is that it fails when behaviour
            regresses. Editing it to pass turns a safety net into a rubber stamp and hides the very
            defect it was meant to surface.</li>
          <li><b style="color:var(--ink)">Generalized context:</b> Seen when an agent under pressure to
            reach green treats the gate, rather than the code, as the thing to satisfy.</li>
        </ul>
      </div>
      <p style="margin-top:1.5rem">This lesson maps directly to two rules the pack already ships,
        "Gate discipline" and "A verification finding is fixed, not argued away", which is how a
        contributed seed either strengthens an existing guardrail or becomes a new one.</p>
    </div>
  </section>
  <section>
    <div class="wrap">
      <h3>Contributing</h3>
      <p>Contributions are welcome. The contribution terms are separate from the software licence and
        are shown before you submit, and seeds are accepted under a broad open grant so a lesson can be
        freely reused. The pack's licensing model is documented separately.</p>
    </div>
  </section>
</section>
