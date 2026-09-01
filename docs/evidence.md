+++
title = "AIQT evidence and assurance: the proof behind the claims"
description = "The evidence behind AIQT: the 1.0.0 release and its source, the known limitations, platform test status, documented cases from the GRC Library, and the change history. Facts not yet published are marked pending."
canonical = "https://aiqt.ai/evidence"
og-title = "AIQT evidence and assurance: the proof behind the claims"
og-description = "The evidence behind AIQT: the 1.0.0 release and its source, the known limitations, platform test status, documented cases from the GRC Library, and the change history. Facts not yet published are marked pending."
og-url = "https://aiqt.ai/evidence"
sidebar-active = "evidence"
+++

<div class="wrap pagehead">
  <p class="eyebrow">Evidence and assurance</p>
  <h1>The proof behind the claims.</h1>
  <p class="lead">AIQT asks your assistant to back a claim with evidence, so this page holds AIQT
    to the same rule. Here is what is available now, what has been tested, and what is
    documented. Where a fact is not yet published, it is marked pending rather than implied.</p>
</div>

<section id="release">
  <div class="wrap">
    <p class="eyebrow">The release</p>
    <h2>AIQT 1.0.0</h2>
    <ul class="clean">
      <li><b style="color:var(--ink)">Version:</b> 1.0.0, the chat-assistant Skill.</li>
      <li><b style="color:var(--ink)">The artefact:</b> the packaged skill zip, plus a
        portable instruction file, both on the <a href="/install">install page</a>.</li>
      <li><b style="color:var(--ink)">Built from:</b> <span class="evidence-label">the exact source tag or commit: pending</span></li>
      <li><b style="color:var(--ink)">Checksum:</b> <span class="evidence-label">pending</span></li>
    </ul>
  </div>
</section>

<section id="limits">
  <div class="wrap">
    <p class="eyebrow">Known limitations</p>
    <h2>What AIQT does not do</h2>
    <p class="lead">AIQT is a behavioural standard your assistant is required to follow, not a
      control that technically prevents a model from erring. Results still depend on the model,
      the platform, competing instructions, and the tools available in the conversation. AIQT
      does not make an AI infallible, and it does not replace your judgement, your organization's
      policy, or the law.</p>
  </div>
</section>

<section id="platform-tests">
  <div class="wrap">
    <p class="eyebrow">Platform test status</p>
    <h2>What has been tested, and when</h2>
    <div class="tablewrap"><table class="dtable">
      <caption class="vh">Platform test status for AIQT 1.0.0</caption>
      <thead><tr><th scope="col">Platform</th><th scope="col">Install method</th><th scope="col">Last tested</th><th scope="col">Result</th><th scope="col">Known constraints</th></tr></thead>
      <tbody>
        <tr><td>Claude</td><td>Skill upload</td><td class="pending">pending</td><td class="pending">pending</td><td>Desktop browser; mobile Skill upload not documented</td></tr>
        <tr><td>ChatGPT</td><td class="pending">not yet tested</td><td class="pending">pending</td><td class="pending">pending</td><td class="pending">pending</td></tr>
        <tr><td>Gemini</td><td class="pending">not yet tested</td><td class="pending">pending</td><td class="pending">pending</td><td class="pending">pending</td></tr>
        <tr><td>Copilot</td><td class="pending">not yet tested</td><td class="pending">pending</td><td class="pending">pending</td><td class="pending">pending</td></tr>
      </tbody>
    </table></div>
    <p>An untested platform says so plainly. A tested one will carry its date and result here.</p>
  </div>
</section>

<section id="cases">
  <div class="wrap">
    <p class="eyebrow">Documented cases</p>
    <h2>Real calls from the GRC Library</h2>
    <p class="lead">AIQT was built and tested on the GRC Library first. The
      <a href="/examples">examples</a> narrate several real calls; each links here to its source
      record when that record is public.</p>
    <ul class="clean">
      <li>A fixed retention baseline attributed to sources that prescribe no fixed interval, caught and corrected. <span class="evidence-label">Source record link: pending</span></li>
      <li>A recycled backlog number that would have mis-resolved later citations, caught by a permanence check. <span class="evidence-label">Source record link: pending</span></li>
      <li>A completeness check that read "the file exists" as "the delivery is complete", fixed at the class. <span class="evidence-label">Source record link: pending</span></li>
    </ul>
  </div>
</section>

<section id="history">
  <div class="wrap">
    <p class="eyebrow">Change history</p>
    <h2>What changed, and when</h2>
    <p class="lead">Every substantive change to AIQT is recorded; the public change log is a
      curated release-level view. <a href="https://github.com/jposluns/guardrails/blob/main/CHANGELOG.md" target="_blank" rel="noopener noreferrer">The public change log</a></p>
    <div class="cta" style="justify-content:flex-start; margin-top:1.2rem">
      <a class="btn primary" href="/about">Who is behind AIQT</a>
      <a class="btn ghost" href="https://github.com/jposluns/guardrails" target="_blank" rel="noopener noreferrer">View the source on GitHub</a>
    </div>
  </div>
</section>
