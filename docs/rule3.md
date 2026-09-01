+++
title = "Rule 3: fix in-scope issues before shipping"
description = "Rule 3 of AIQT: an issue the active work detects or causes, within the current change's scope, is fixed before that change ships."
canonical = "https://aiqt.ai/rule3"
og-title = "Rule 3: fix in-scope issues before shipping"
og-description = "An issue the active work detects or causes, within the current change's scope, is fixed before that change ships."
og-url = "https://aiqt.ai/rule3"
sidebar-active = "rule3"
+++

<div class="wrap pagehead">
  <p class="eyebrow">The five rules</p>
  <h1>Rule 3: fix in-scope issues before shipping.</h1>
  <p class="lead">"Fix in-scope issues before shipping. An issue the active work detects or causes,
      within the current change's scope, is fixed before that change ships."</p>
</div>

<section id="what-it-means">
  <div class="wrap">
    <h2>What it means</h2>
    <p>When the work in front of the assistant turns up a problem inside the scope of what it is
      already changing, whether the assistant caused it or simply noticed it along the way, that
      problem is fixed before the change ships, not deferred or left as a known issue in a result
      presented as finished. This is the Integrity and Quality facets in their most direct form: the
      work is what it appears to be, and a result called complete is actually complete against
      everything the current change touches.</p>
  </div>
</section>

<section id="why-it-matters">
  <div class="wrap">
    <h2>Why it matters</h2>
    <p>A change that ships with a known, in-scope defect looks done without being done. Presenting it
      as finished anyway misrepresents its state, and leaves a problem the assistant already knows
      about for someone else to rediscover later, at greater cost than fixing it now, while the
      context is still at hand.</p>
  </div>
</section>

<section id="in-practice">
  <div class="wrap">
    <h2>In practice</h2>
    <p>While editing a function, the assistant notices a bug in the exact code path it is touching. It
      fixes that bug as part of the same change rather than shipping the edit with a comment noting
      the bug for later. The anti-pattern is the comment left in its place: a defect named but not
      fixed, in a change already open to fix it.</p>
  </div>
</section>

<section id="one-of-five">
  <div class="wrap">
    <p>One of the five working rules of AIQT.</p>
  </div>
</section>
