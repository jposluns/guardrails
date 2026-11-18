+++
title = "Adopter tracker-label shapes and the added-line diff check"
description = "How an adopter adds tracker-label shapes to the internal-names gate, the limits and budget that bound them, the supported baseline procedure, and the --diff BASE added-line check."
canonical = "https://aiqt.ai/tracker-labels"
og-title = "Adopter tracker-label shapes and the added-line diff check"
og-description = "How an adopter adds tracker-label shapes to the internal-names gate, the limits and budget that bound them, the supported baseline procedure, and the --diff BASE added-line check."
og-url = "https://aiqt.ai/tracker-labels"
sidebar-active = "tech-details"
+++

<div class="wrap pagehead">
<h1>Adopter tracker-label shapes and the added-line diff check</h1>
<p class="lead"><code>tools/check_internal_names.py</code> counts four built-in tracker-label shapes in every shipped file and holds each per-file count to the ratchet baseline <code>.aiqt/tracker-label-baseline.toml</code>. An adopter can add its own shapes, and can run an extra check that reports labels on lines a branch adds.</p>
</div>

<section class="wrap">
<h2>Configuration: <code>.aiqt/tracker-labels.toml</code></h2>
<p>The adopter owns this file. This repository tracks it with no shape and does not ship it. If the file is absent, no extra shape applies. A present file is checked before any shape is matched, and any fault fails closed with exit 2.</p>
<pre><code>format-version = 1
[[shape]]
name = "backlog-id"
regex = '\bTASK-[0-9]+\b'
red = ["References TASK-123 here."]
green = ["References TASK-next here."]
</code></pre>
<ul>
<li>The schema is closed. The file holds only <code>format-version</code> (the integer 1) and <code>[[shape]]</code> tables, and each table carries exactly <code>name</code>, <code>regex</code>, <code>red</code> and <code>green</code>.</li>
<li><code>name</code> is a lower-case ASCII identifier (letters, digits and single hyphens, at most 48 characters), and names are unique. The baseline key is <code>adopter-&lt;name&gt;</code>, so it never collides with a built-in key.</li>
<li><code>regex</code> is a Python regular expression, applied to each line separately. Letter case follows the regex, inline flags included.</li>
<li><code>red</code> lists fixture strings the shape must match, and <code>green</code> lists strings it must not match. Both arrays are nonempty, and each fixture is one nonempty line.</li>
<li>A shape fails closed when its regex does not compile, a red fixture is not matched, a green fixture is matched, or any match is zero-width (in a fixture or in a scanned line).</li>
</ul>
<h3>Limits</h3>
<table>
<tr><th>Bound</th><th>Value</th></tr>
<tr><td>File size</td><td>64 KiB</td></tr>
<tr><td>Shapes</td><td>32</td></tr>
<tr><td>Regex length</td><td>512 characters</td></tr>
<tr><td>Fixtures of each colour per shape</td><td>16</td></tr>
<tr><td>Fixture length</td><td>4096 characters</td></tr>
<tr><td>Wall-clock budget for one run of adopter patterns</td><td>300 seconds</td></tr>
</table>
<p>Adopter regexes are compiled, checked against their fixtures and matched only in a separate worker process. The worker is stopped when the budget runs out. A timeout, a worker failure or malformed worker output exits 2. A slow shape is never dropped so that the scan can continue clean.</p>
<p>The budget comes from one measurement. The worker alone matched 32 linear-time shapes of the form <code>\bZQX00-[0-9]+\b</code> over this repository's shipped set (926 decodable files, 721,140 lines, about 48 MB) in 39.6 seconds on one shared, loaded host. One shape took 2.6 seconds and four took 6.4 seconds, so each shape adds about 1.2 seconds. The 300-second budget leaves about 7.5 times the 32-shape figure. This is one host's figure, not a guarantee: a slower host, a larger shipped set or a costlier regex can still run out of budget and fail closed. The other limits are proposed bounds and are not measured.</p>
</section>

<section class="wrap">
<h2>The ratchet</h2>
<p>Adopter shapes join the per-file ratchet after the built-in shapes, sorted by name. The baseline's exactness holds in both directions for them as well. With no adopter shape, the baseline render is unchanged. If you remove a shape while its baseline key remains, the baseline is refused as carrying an unknown key.</p>
<p>The baseline <code>.aiqt/tracker-label-baseline.toml</code> is pack content: the release ships it, and an adopter tree must not change it. The supported adopter procedure is therefore:</p>
<ul>
<li>An adopter shape must match nothing in the shipped set of the adopter tree. A shape with no match leaves the baseline byte-identical, so the shipped baseline still passes.</li>
<li>Do not run <code>--write-baseline</code> in an adopter tree to admit adopter counts. Editing the shipped baseline is drift from the released content.</li>
<li>If a shape matches shipped text, narrow the shape, or remove the label from your own tracked file under <code>.github/</code>. To report labels only on lines a branch adds, use <code>--diff BASE</code>, which ignores the baseline.</li>
</ul>
<p>In this repository, where the baseline is authored, regenerate it with <code>python3 -I -B tools/check_internal_names.py --write-baseline</code> after a change that legitimately moves a count. Treat both the configuration change and the regenerated baseline as reviewed policy changes.</p>
<p>The configuration file has no filename exemption. If the release inventory listed it, it would be scanned like any shipped file: its red fixture lines, which its own shapes can match, would count and need reviewed baseline entries, which only this repository, where the baseline is authored, can add. The release inventory does not list it, and its path is outside <code>.github/</code> and <code>opf/</code>, so it is not shipped and the adopter procedure above needs no baseline entry for it.</p>
</section>

<section class="wrap">
<h2><code>--diff BASE</code></h2>
<p><code>python3 -I -B tools/check_internal_names.py --diff BASE</code> reports every built-in or adopter shape match on a line added between the merge base of BASE and HEAD, in HEAD's shipped set, whatever the baseline allows. A label deleted in one place and added in another, with the count unchanged, is still reported.</p>
<ul>
<li>The check reads the HEAD commit's manifest, configuration, baseline (validated, not compared), tree and file contents. It also reads the merge base's manifest and tree, to find which paths the merge base shipped. The working tree and the index are not read.</li>
<li>Every Git read ignores replace refs (<code>refs/replace/</code>), any grafts file and any commit-graph file, so none of them can change the commits, parents or file contents the check reads or move the merge base.</li>
<li>Lines are counted by LF. A carriage return stays part of the line, and a last line without LF counts.</li>
<li>An added file counts all its lines as added. A rename from a path the merge base did not ship reports every line, with or without edits. Deleted files, deletion-only changes, pure renames within the shipped set and mode-only changes add nothing. Within the shipped set, a rename with edits reports added lines at the destination path. A rename Git does not detect counts as a delete plus an add.</li>
<li>Added lines are read with command-line options that outrank user and repository Git configuration: no colour, no external diff, no text conversion, fixed <code>a/</code> and <code>b/</code> prefixes, and no inter-hunk context.</li>
<li>A binary attribute does not hide a change. A blob that is not UTF-8 fails closed under a text suffix and is skipped under any other suffix. Archive contents are not examined.</li>
<li>Exit codes: 0 means no label on an added line, 1 means at least one, and 2 means the check cannot be evaluated. Exit 2 covers: BASE is not a readable commit, the repository is shallow, there is no merge base or more than one, Git fails or times out, Git output is malformed, a shipped path is absent or is not a regular file in HEAD, a manifest present at the merge base is not a regular file, is not UTF-8 or is not a usable inventory, or the HEAD configuration or baseline is invalid (also when no line was added). The check never fetches history.</li>
<li><code>--diff</code> takes exactly one operand that does not start with <code>-</code>, and it cannot be combined with another option.</li>
</ul>
<p>The diff check is an extra check. Keep the full gate required in CI. A clean diff check does not mean the full gate passes.</p>
<p>The gate also has an internal <code>--adopter-match-worker</code> entry. It is the budgeted worker the gate launches itself, reads its request as JSON on standard input, and is not a user option.</p>
</section>

<section class="wrap">
<h2>What this does not cover</h2>
<p>A fixture shows only that its own examples behave as stated. Labels can still go unreported when they are written in prose no shape describes, belong to an undeclared shape, are split or encoded, sit inside binary or archive contents, or sit on lines the diff check does not see as added. Removing or narrowing a pattern is a reviewed configuration change.</p>
</section>
