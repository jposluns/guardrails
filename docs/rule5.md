+++
title = "Rule 5: propose an underlying fix"
description = "Rule 5 of AIQT: when your assistant's own gap lets an issue through, it proposes a guardrail so that class of error should not recur. What a Guardrail Seed is, and how to contribute one back."
canonical = "https://aiqt.ai/rule5"
og-title = "Rule 5: propose an underlying fix"
og-description = "When your assistant's own gap lets an issue through, it proposes a guardrail so that class of error should not recur."
og-url = "https://aiqt.ai/rule5"
sidebar-active = "rule5"
+++

<div class="wrap pagehead">
  <p class="eyebrow">The five rules</p>
  <h1>Rule 5: propose an underlying fix.</h1>
  <p class="lead">"Propose an underlying fix. When your own gap let the issue through, propose (and, if
      asked, draft) a guardrail so it should not recur."</p>
</div>

<section id="what-rule-5-is">
  <div class="wrap">
    <h2>What Rule 5 is</h2>
    <p>It is the fifth of the five working rules of AIQT. It fires when the assistant's own gap let
      an issue through: not every problem, but the ones its own reasoning, habit, or blind spot
      allowed. Instead of quietly recovering and moving on, the assistant turns that one-off mistake
      into a durable guardrail, so the same class of error is caught next time rather than repeated.</p>
  </div>
</section>

<section id="giving-back">
  <div class="wrap">
    <h2>Giving the lesson back</h2>
    <p>Rule 5 improves your own project first: the guardrail lands in your workspace and protects
      your work. The Guardrail-Seed contribution is a separate, optional step that lets you send the
      lesson back to AIQT, so a fix discovered in your project can help every other adopter.</p>
    <p>What travels is a lesson: a general requirement expressed in the pack's own terms. It is not
      your code, your prompts, your conversations, or your data. The give-back is entirely voluntary,
      and turning it off never reduces your licence rights.</p>
  </div>
</section>

<section id="seed-contains">
  <div class="wrap">
    <h2>What a Guardrail Seed contains</h2>
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

<section id="seed-never">
  <div class="wrap">
    <h2>What a Guardrail Seed must never contain</h2>
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

<section id="how-it-travels">
  <div class="wrap">
    <h2>How a Guardrail Seed travels</h2>
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

<section id="example">
  <div class="wrap">
    <h2>Rule 5 in action: an example seed</h2>
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

<section id="contributing">
  <div class="wrap">
    <h2>Contributing</h2>
    <p>Contributions are welcome. The contribution terms are separate from the software licence and
      are shown before you submit, and seeds are accepted under a broad open grant so a lesson can be
      freely reused. The pack's licensing model is documented separately.</p>
  </div>
</section>
