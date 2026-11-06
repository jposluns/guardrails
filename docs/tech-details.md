+++
title = "Technical details of the AIQT development assistant (1.1.0, in development)"
description = "The technical detail of the AIQT development assistant (1.1.0, in development): the project-practice files it sets up, the config file you own, how new-guardrail seed PRs work, CI integration, and per-language depth composed at install."
+++

<div class="wrap"><div class="doc">
<p class="lbl first">Under the hood</p>
<h1>Technical details of the development assistant.</h1>
<div class="rail"><img class="rail-logo" src="/draft/assets/baseline/logos/aiqt.svg" alt="A shield bearing a check mark, set on a guardrail"></div>
<p class="lead">Everything on this page is the AIQT 1.1.0 design, in development: what you
  own, what you configure, what can travel back, how it fits your pipeline, and how it
  composes per-language depth. The <a href="/development">overview</a> is the place to start;
  this is the detail once you are interested.</p>

<section id="directory">
<p class="lbl sec">What it creates</p>
<h2>The directory it sets up, file by file</h2>
<p class="lead">Everything AIQT keeps for your project lives in one directory,
  <code>.aiqt/</code>, apart from the instruction files it generates for each assistant (at
  the repository root and in folders such as <code>.claude/</code>). The directory is created
  at setup if it does not already exist. During setup the
  wizard asks which of these files you want, and the default is all of them, so your
  assistant follows good practice from the first day rather than from when you remember to
  ask.</p>
<p>This layout is the 1.1.0 design for an adoption that skips OPFiles. An AIQT adoption sets
  up OPFiles (OPF) by default, and with OPF in place these project records live where the OPF
  specification puts them (TODO.md and the registers in the store under
  <code>.working/</code>, the curated change log at the repository root), not inside
  <code>.aiqt/</code>.</p>
<pre>your-project/
&#9500;&#9472;&#9472; .aiqt/
&#9474;   &#9500;&#9472;&#9472; config.md              yours: own it, edit it, commit it
&#9474;   &#9500;&#9472;&#9472; CHANGELOG.md            what changed, and when
&#9474;   &#9500;&#9472;&#9472; TODO.md                 the work coming up
&#9474;   &#9500;&#9472;&#9472; DONE.md                 what has shipped
&#9474;   &#9500;&#9472;&#9472; ROADMAP.md              where the project is heading
&#9474;   &#9500;&#9472;&#9472; open-findings.md        defects, tracked to closure
&#9474;   &#9500;&#9472;&#9472; pending-decisions.md    decisions waiting on you
&#9474;   &#9500;&#9472;&#9472; ...                     the QA audit registers (below)
&#9474;   &#9492;&#9472;&#9472; core/                   the updater's only write location
&#9500;&#9472;&#9472; src/                        untouched
&#9492;&#9472;&#9472; ...                         untouched</pre>
<ul>
  <li><strong>config.md</strong>: your configuration, including which
    guardrails are active and the sharing setting. Yours to own, edit, and commit, and
    updates never rewrite it. The next section covers it in full.</li>
  <li><strong>The practice files</strong> (CHANGELOG.md, TODO.md, DONE.md,
    ROADMAP.md): what changed, the work coming up, what has shipped, and where the project
    is heading, so you and your assistant share one record of where things stand.</li>
  <li><strong>The QA audit trail</strong> (open-findings.md,
    pending-decisions.md, the validation-sweep history, the publications-screening register,
    the deep-assessment register, the verifier-override log, and an improvement log): a
    committed, reviewable record of what the assistant's quality checks did, so a teammate
    or a manager can audit the QA itself.</li>
  <li><strong>core/</strong>: the one directory AIQT's updater writes to. A
    guard refuses every write outside it, so a diff after any update shows changes in
    exactly one place, and nothing else in your repository changes without your say-so.</li>
</ul>
<div class="tablewrap" tabindex="0"><table>
  <caption class="vh">Who can create or change AIQT files, by phase</caption>
  <thead><tr><th scope="col">Who</th><th scope="col">When</th><th scope="col">May create or change</th><th scope="col">Never touches</th></tr></thead>
  <tbody>
    <tr><td>Setup wizard</td><td>Install, with your per-file consent</td><td><code>.aiqt/</code> and its files; the generated agent files at the repository root and in folders such as <code>.claude/</code></td><td>Your source</td></tr>
    <tr><td>Updater</td><td>Update</td><td><code>.aiqt/core/</code> only (guard-enforced)</td><td>Everything else, including your <code>config.md</code></td></tr>
    <tr><td>Your assistant</td><td>While you work</td><td>The <code>.aiqt/</code> practice and QA records; your project files as you direct it</td><td><code>.aiqt/core/</code></td></tr>
    <tr><td>Doctor</td><td>Verification</td><td>Nothing (read-only)</td><td>Everything</td></tr>
    <tr><td>CI and local exec workers</td><td>Pipeline and desk checks</td><td>pending, documented before 1.1.0 ships</td><td>pending</td></tr>
  </tbody>
</table></div>
<p>With your permission, AIQT sets these up, and every one is
  yours to keep, edit, or remove. They are committed with your project, not hidden away, so
  the record stays reviewable. They help you and your development assistant equally: the
  assistant reads them to pick up context and updates them as it works, so the two of you
  are never out of sync.</p>
<p>A tool that updates itself inside your repository earns a hard question: what else can it
  touch? With AIQT you can check the answer yourself, because the updater writes only inside
  <code>.aiqt/core/</code>, and reviewing an update takes the same shape as reviewing any
  other change: read the diff, and the diff is small and contained.</p>
</section>

<section id="config">
<p class="lbl sec">Your configuration</p>
<h2>The config file you own</h2>
<p class="lead">Your choices live in <code>.aiqt/config.md</code>, and that file
  is yours: you own it, you edit it, you commit it. In the 1.1.0 design, updates never
  rewrite it.</p>
<h3>Guardrails, individually</h3>
<p>The config records which guardrails are active, and the switch is per guardrail,
  not all-or-nothing. Enable the ones that fit your project, disable the ones that
  do not, and the file is the record of what you chose.</p>
<h3>A file, not a dashboard</h3>
<p>Because the configuration is a file in your repository, it behaves like the rest
  of your project: it is versioned, it is diffed, it is reviewed, and a teammate can
  see exactly which guardrails your project runs and when that changed.</p>
<p>The config also carries the sharing setting described in the
  next section, so the decision about what leaves your project sits in a file you
  control, alongside every other decision you have made.</p>
</section>

<section id="seeds">
<p class="lbl sec">Sharing back</p>
<h2>New-guardrail seed PRs</h2>
<p class="lead">When a guardrail gap lets an issue through, rule 5 closes the gap
  locally. The 1.1.0 design adds an optional last step: contributing the new guardrail
  back to the AIQT project as a seed PR, so a gap found once is closed for every
  developer who uses AIQT.</p>
<h3>You choose it at install</h3>
<p>Sharing is governed by one setting in your config file,
  <code>contribute_guardrails</code>, with three values: <code>never</code>,
  <code>ask</code>, or <code>always</code>. Setup asks you which you want, with
  <code>ask</code> selected by default: the assistant checks with you before each
  contribution, so nothing is submitted without your say-so. Choose <code>never</code>
  and it is never offered. Choose <code>always</code> and each contribution is opened
  automatically, using a credential you provide for that purpose, still as a pull request
  to the AIQT project, never a merge into your own code.</p>
<h3>What travels, and what never does</h3>
<p>A seed is scrubbed to concept, trigger, and goal before anything leaves your machine:
  the guardrail's concept, what triggers it, and the goal it serves, and nothing else.
  The scrub is designed to remove your project's specifics, and you preview exactly what
  a seed contains before it is submitted, so you confirm that the scrub did its job. Under
  <code>ask</code> you open the pull request yourself from your own account; under
  <code>always</code> it is opened from the credential you set. Either way the pull
  request is openly attributable, and what stays private is your project's specifics,
  not the fact that you contributed.</p>
<p><strong>Why this is worth turning
  on.</strong> Every team's assistant misses different things. A seed PR turns one team's
  miss into everyone's guardrail, and because seeds are scrubbed to concept, trigger,
  and goal, you preview what travels before it goes. The exchange runs
  both ways: the guardrails your assistant ships with include the ones other developers
  contributed the same way, as Rule 5's voluntary Guardrail-Seed give-back, separate from the
  software licence.</p>
<p><strong>How the credential works.</strong> The
  <code>always</code> mode uses a credential scoped to opening pull requests on the AIQT
  project, which you create and can revoke at any time. Before any seed is sent it is
  scrubbed and shown to you in a preview, and the AIQT project reviews each pull request like
  any other contribution.</p>
<p>Exact credential scope and preview format: pending, documented before 1.1.0 ships</p>
</section>

<section id="pipeline">
<p class="lbl sec">Continuous integration (CI)</p>
<h2>In your pipeline</h2>
<h3>CI integration</h3>
<p>The development assistant integrates with your CI, so the standard that governs
  the assistant during development also holds at the gate. The same discipline
  applies on both sides: a check that passes has actually run, and a check that
  fails is surfaced, never quietly weakened to get a green build.</p>
<h3>The findings loop</h3>
<p>When a finding comes in, the loop validates it before it acts on it. A finding
  that survives validation is worth an action; a finding that fails validation is
  recorded as such. Acting on validated findings keeps the signal clean, and it is
  the 5 rules of AIQT at work: say what was caught, fix in-scope issues in the
  current change, and report out-of-scope findings, proposing them for separately
  authorized work. The 5 rules are on the
  <a href="/development#rules">overview page</a>.</p>
</section>

<section id="qa-standard">
<p class="lbl sec">Standardizing quality assurance</p>
<h2>One QA standard, whichever model checks the work</h2>
<p class="lead">A review by a single model shares that model's blind spots. AIQT
  standardizes the check itself: for substantive work, independent verifiers from different
  model families are given the same brief, each instructed to refute the change rather than
  confirm it, and their verdicts are reconciled. Different families miss systematically
  different things, so cross-family review catches what any one alone would let through.
  AIQT itself is built and reviewed this way, across three families (Claude, GPT/Codex, and
  Gemini); in the 1.1.0 design, the development assistant brings the same standard to your
  project.</p>
<ol class="steps">
  <li><span><strong>Routine changes</strong> run the ordinary gates. Most work needs nothing heavier, and
    a review that fires on everything stops being read.</span></li>
  <li><span><strong>Substantive changes</strong> get the dual-family pair: the same refute-brief to both
    families, verdicts reconciled, disagreements examined rather than averaged away.</span></li>
  <li><span><strong>Delicate changes</strong> escalate to a heavier harness: where a wrong result is a
    correctness defect no simple check catches, the change is large or delicate, and an
    escaped error is costly, independent adversarial verifiers are briefed to refute the
    work, and the result is applied deterministically rather than by hand. The review is
    up-skilled for the work that warrants it, and only that work.</span></li>
</ol>
<p>The point of the tiers is that the standard scales with the
  stakes instead of depending on who happened to run the check. A passing review means the
  review actually ran; a failing one is surfaced, never quietly weakened. That is the AIQT
  ordering applied to review itself.</p>
</section>

<section id="qa-where">
<p class="lbl sec">Where it runs</p>
<h2>The same check, in your pipeline and on your machine</h2>
<p class="lead">A QA standard standardizes anything only if it holds in both places where
  work happens: at the gate, and at the desk. In the 1.1.0 design it runs in both.</p>
<h3>In your GitHub CI</h3>
<p>The model-based checks run in the pipeline at the tier the change warrants (routine changes run the ordinary gates alone, substantive changes add the dual-family pair, and delicate changes escalate to the heavier harness), alongside your
  usual gates. A change is reviewed by the dual-family pair before it lands, under the
  same discipline as every other check: it passes because it ran, and a failure blocks
  rather than disappears.</p>
<h3>In local development</h3>
<p>Exec workers, separate worker processes on your machine, run the same dual-family
  QA at the relevant points while you work, and the heavier harness on the delicate
  ones, so a finding surfaces before the change ever reaches the pipeline. Exec workers
  run on Linux today; Windows support is planned.</p>
<h3>The data flow, by tier</h3>
<div class="tablewrap" tabindex="0"><table>
  <caption class="vh">Model-review data flow, by tier</caption>
  <thead><tr><th scope="col">Tier</th><th scope="col">What is sent</th><th scope="col">To which providers</th><th scope="col">Credentials</th><th scope="col">Retention</th><th scope="col">Cost and latency</th></tr></thead>
  <tbody>
    <tr><td>Routine</td><td>Nothing to a model; the ordinary gates run locally</td><td>None</td><td>None</td><td>None</td><td>Gate time only</td></tr>
    <tr><td>Substantive</td><td>pending</td><td>pending</td><td>pending</td><td>pending</td><td>pending</td></tr>
    <tr><td>Delicate</td><td>pending</td><td>pending</td><td>pending</td><td>pending</td><td>pending</td></tr>
  </tbody>
</table></div>
<p>The 1.1.0 data flow, providers, credentials, retention, and cost: pending, documented before 1.1.0 ships</p>
<p>The 1.1.0 design gives local and CI reviewers the same QA
  brief. Its intent is to reduce variation caused by who runs the check or which tool they
  use; provider behaviour and results remain to be verified.</p>
</section>

<section id="composed">
<p class="lbl sec">Per-language depth</p>
<h2>Language and framework depth, composed at install</h2>
<p class="lead">The governance core is deliberately language-neutral. Deep per-language
  and per-framework guidance comes from vetted aligned sources, and the setup generator
  composes them in at install time.</p>
<p>During setup, the generator detects your stack and presents the aligned sources that
  apply to it. For each source you see the facts a decision needs: what it covers, its
  licence, when it was last reviewed, and the gaps it leaves. Then you choose, taking
  all that apply or deciding one by one. AIQT lists each source, shows you these facts,
  and credits and links the people who do that work. The decision to use a source is
  always yours.</p>
<ul>
  <li><strong>Coverage:</strong> what the source addresses in your stack.</li>
  <li><strong>Licence:</strong> the terms it is published under.</li>
  <li><strong>Last reviewed:</strong> when it was last checked, because
    per-language depth has a shelf life.</li>
  <li><strong>Gaps:</strong> what it leaves uncovered, stated up front.</li>
</ul>
</section>

<section id="back">
<p class="lbl sec">Ready to try it</p>
<h2>Set it up, or read the source</h2>
<p class="lead">The <a href="/development#setup">setup and verification steps</a> are on the
  overview page, and where 1.1.0 stands is <a href="/development#availability">stated plainly
  there too</a>.</p>
<p>
  <a class="btn inline" href="https://github.com/jposluns/guardrails" target="_blank" rel="noopener noreferrer">Read the pack on GitHub<span class="vh"> (opens in a new tab)</span></a>
</p>
<nav aria-label="Project"><a href="/disclosure/">Disclosure</a></nav>
</section>
</div></div>
