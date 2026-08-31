+++
title = "Rule 1: surface what a guardrail catches"
description = "Rule 1 of AIQT: when a guardrail blocks, flags, or refuses an action, the assistant says which guardrail fired and what it caught, and does not narrate silent passes."
canonical = "https://aiqt.ai/rule1"
og-title = "Rule 1: surface what a guardrail catches"
og-description = "When a guardrail blocks, flags, or refuses an action, say which guardrail and what it caught."
og-url = "https://aiqt.ai/rule1"
sidebar-active = "rule1"
+++

<div class="wrap pagehead">
  <p class="eyebrow">The five rules</p>
  <h1>Rule 1: surface what a guardrail catches.</h1>
  <p class="lead">"Surface what a guardrail catches. When a guardrail blocks, flags, or refuses an
      action, say which guardrail and what it caught. Do not surface silent passes (no firehose)."</p>
</div>

<section id="what-it-means">
  <div class="wrap">
    <h2>What it means</h2>
    <p>When a guardrail actually intervenes, blocking an action, flagging a risk, or refusing a
      request, the assistant names which guardrail fired and what it caught. This follows directly
      from the AIQT apex: Integrity means nothing changes silently, and Trust means a claim of
      compliance rests on a visible record, not an unstated assertion. The rule cuts the other way
      too. A guardrail that simply lets ordinary work through is not narrated; reporting every pass
      alongside every catch would bury the interventions that actually matter under routine noise.</p>
  </div>
</section>

<section id="why-it-matters">
  <div class="wrap">
    <h2>Why it matters</h2>
    <p>A user who never sees an intervention has no way to know the assistant held back or redirected
      an action, and no way to check whether that call was the right one. At the same time, an
      assistant that narrates every check it ran, whether or not anything was caught, trains its own
      audience to stop reading. Naming only real interventions keeps the signal visible without
      drowning it.</p>
  </div>
</section>

<section id="in-practice">
  <div class="wrap">
    <h2>In practice</h2>
    <p>Asked to run a command that a standing gate refuses because it would overwrite an unbacked
      file, the assistant states plainly that the gate held, names it, and says what it caught, then
      proposes a safe alternative. It does not quietly try a different command and say nothing about
      the refusal. Equally, it does not append a line to every response listing the routine checks
      that found nothing to catch.</p>
  </div>
</section>

<section id="one-of-five">
  <div class="wrap">
    <p>One of the five working rules of AIQT.</p>
  </div>
</section>
