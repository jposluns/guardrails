# Releasing AIQT Guardrails

This is the reproducible protocol for cutting a public release. It is deterministic: the same steps on
the same frozen content produce the same published artifacts and the same recorded digests.

## Integrity model

A release's integrity rests on a SHA-256 digest published through a channel independent of the download.
Each release records its artifact digests in `changelog.toml` under `[release.artifacts]` (held in this
GitHub repository), and the same digests appear on the evidence page. Because the recorded digest lives
in the repository, separate from the download host, an adopter can verify that what they downloaded
matches the authenticated reference even if the download host is compromised. Releases are not separately
signed; the independently published digest is the authenticated reference.

## Steps

Each pull request below, like every pull request into main, gets a Quality run only when a label is
added to it (the merge lane adds the `merge-train` label), and its heavy jobs run only when its head is
a `merge-train-*` branch of this repository, so "merged on green" means green on such a labelled train
pull request (CLAUDE.md, Gates and CI).

1. Freeze. On the release branch, confirm `python3 tools/gen_skill.py --check` is clean and the full
   `bash tools/run_all_checks.sh` is green at the freeze commit. That run FAILS when gitleaks is not on
   PATH or reports another version than the `Secrets (gitleaks)` step of `.github/workflows/quality.yml`
   pins (the gitleaks-pin gate), and on any CPython patch
   release other than the one `.aiqt/python-host.toml` names (the python-host-pin gate), so run it on that
   release; when the host interpreter has changed, first land one change that updates `python-host-patch`
   there and the `python-version` pin of every host-patch job (`opf-host-patch`,
   `git-fixture-env-core-host-patch` and the 43 config shard jobs
   `git-fixture-env-config-<lane>-shard-K-host-patch`) in `.github/workflows/quality.yml` together. After the freeze the
   release artifacts (the version-numbered `site/downloads/aiqt-skill-<skill version>.zip`, which the site
   links to, and `site/downloads/aiqt-instructions.txt`) and their generating inputs (the corpus and
   `tools/gen_skill.py`) do not change; `site/downloads/aiqt-skill.zip` is a stable "latest" alias kept
   byte-identical to the version-numbered copy (both are written from the same bytes, and `gen_skill
   --check` compares each to disk, so they cannot diverge). The hooks plugin's copy of the skill,
   `plugin/aiqt-guardrails-hooks/skills/aiqt/SKILL.md`, does not change after the freeze either: `gen_skill.py`
   writes it from the same bytes as `site/downloads/aiqt/SKILL.md` (the `SKILL.md` inside the zip), and
   `gen_skill --check` compares the two copies on disk. It is pack content, not a separate release artifact
   (step 2). The
   release-metadata edits prescribed below (the recorded digests, the evidence fields, the attestation
   row with its regenerated branch-integrity artifacts, and the tag key) are the only changes permitted
   after this point. For the genesis (first recorded) release, the freeze pull request also carries the
   `demonstration` file that the step 5b first-pin evidence references: the gate resolves that reference
   in the candidate tree, so the file must be committed before the freeze. Its content is not defined
   here; it remains open under the first-release-evidence item.

   Fold the version declaration. Between cuts `changelog.toml` ends with an `[unreleased]` table whose
   `version` is the planned release N (a bare `X.Y.Z`), and the generators write `N-dev` into `VERSION`,
   `.aiqt/manifest.toml`, the announce snippet and the site version labels. The freeze pull request moves
   that table into a new last `[[release]]` with `version = N`, a real `date` (a quoted `YYYY-MM-DD`), a
   `title` and the collected `items`, deletes `[unreleased]`, and writes `.aiqt/release-cut.toml`
   (`operation = "append"`, `previous-version` the previous release, `next-version = N`, and the base
   changelog digest). The `python3 tools/gen_changelog.py` run that follows the fold makes `VERSION` the
   plain `N`, and the generator sweep below then turns every other one of those outputs back into the
   plain `N`. The declaration is a tracked path, so it needs an ownership class: `python3 tools/gen_manifest.py`
   refuses an unclassified tracked path (exit 2, "COMPLETENESS: ... carry no class, e.g.
   .aiqt/release-cut.toml"). The first cut that writes the declaration therefore also adds this row to
   `.aiqt/core/ownership.toml`, beside the `changelog.toml` row; it cannot land earlier, because the map
   refuses a row whose path is not tracked (exit 2, NO-STRAYS), and later cuts keep it, since the
   declaration stays tracked (the cut gate refuses a change that removes it):

   ```toml
   [[release-class]]
   path = ".aiqt/release-cut.toml"
   class = "pack-immutable"
   note = "The release-cut declaration tools/check_release_cut.py reads (RELEASING step 1). Added by the first cut that writes it and tracked from then on, since that gate refuses a change that removes it. Release metadata beside changelog.toml, whose digest it binds, and shipped like it."
   ```

   A cut that keeps a `.aiqt/pending-fixes.toml` list (optional; `tools/check_release_cut.py` refuses
   the cut while it lists an open item) adds, in the change that first tracks the list, this exclusion
   beside the other `[[exclusion]]` rows, and removes it in the change that deletes the list (a
   stale exclusion is refused too):

   ```toml
   [[exclusion]]
   path = ".aiqt/pending-fixes.toml"
   reason = "Repository-local hand-kept pending-fix list tools/check_release_cut.py reads on a cut; tracked, not shipped, like .aiqt/check-removals.toml. Added with the change that first tracks the list and removed with the change that deletes it."
   ```

   These are the rows `CUT_RECORD_OWNERSHIP_ROWS` and `PENDING_FIXES_OWNERSHIP_ROWS` in
   `tools/gen_manifest.py`, whose self-test stages a cut through manifest generation with them.
   `python3 -I -B tools/check_release_cut.py` refuses a cut that releases another version than the
   declared N or that keeps `[unreleased]`, and step 5b refuses a candidate whose manifest carries a `-dev`
   version or whose `changelog.toml` still carries `[unreleased]`.

   Then, before the skill checklist below, run `python3 tools/gen_changelog.py`. It regenerates `VERSION`
   and `CHANGELOG.md` from the folded `changelog.toml`, so `VERSION` reads the plain `N` that the
   checklist's generators compare against. Until it runs, `VERSION` still says `N-dev` while
   `changelog.toml` has no `[unreleased]` table, and `python3 tools/gen_draft_site.py` refuses that (exit 2,
   "VERSION N-dev is a development version and there is no [unreleased] table").

   Skill version bump checklist. The skill is independently versioned before the 1.1.1 pack release
   (skill 1.0.6 under pack release 1.0.5); from the 1.1.1 pack release on, the skill version equals the
   pack version. `gen_skill.build_outputs` enforces this: once `VERSION` is at or above
   `gen_skill.PACK_ALIGN_FIRST_VERSION` (1.1.1), a skill meta version that differs from `VERSION` fails
   closed (exit 2). The hooks plugin follows the same rule from the same release: `gen_hooks.py` fails
   closed (exit 2) when the `[plugin]` `version` in `.aiqt/core/hooks/manifest.toml` differs from `VERSION`,
   so the release cut also sets that value to the pack version and regenerates
   `plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json` with `python3 tools/gen_hooks.py`. The root
   `VERSION` is the pack version only in this repository (the tree carrying `.aiqt/authoring.toml`), so the
   rule is a release-time rule of this repository and is enforced only here. A development `VERSION`
   (`X.Y.Z-dev`, main between cuts) skips both checks, and the `gen_changelog.py` run after the freeze's fold
   makes `VERSION` plain again, so the rule re-arms exactly at the cut; any other suffix still fails closed (exit 2); in an adopter's tree, where
   `VERSION` is the adopter's own, neither generator nor `conformance.py` re-checks it (a release ships only
   after this check passed). A deleted `.aiqt/authoring.toml` that the git index still tracks is refused
   (exit 2), not read as an adopter tree. Only the latest skill version is served: exactly
   one version-numbered zip sits under `site/downloads/`, and `gen_skill.py --check` reports a stale or
   missing one as latest-only drift. The concrete version-numbered
   filename `aiqt-skill-<skill version>.zip` is spelled as a literal in four places, kept consistent by a
   fail-closed version-match assertion in `gen_skill.build_outputs` plus `gen_skill.py`'s latest-only
   refusal and `--check` (each names a stale version-numbered zip; the generator never deletes it), not by
   true single-sourcing (`versioned_zip_basename` is only the shared
   filename SHAPE). To bump the skill version, edit all five spots below, then regenerate:
     - a. `.aiqt/core/skill/skill-source.md`: the `meta` `version` (the authoritative source the assertion
       and both generators read).
     - b. `tools/gen_skill.py`: the `ZIP_VERSIONED_PARTS` literal AND its matching `GENSRC_OUTPUTS` target
       (both spell the version-numbered basename).
     - c. `tools/check_portability.py`: the `BINARY_ALLOW` set (the version-numbered zip is a shipped
       binary allowed past the portability text scan).
     - d. `.aiqt/core/ownership.toml`: the `[checkout]` `binary` list (the version-numbered zip is a
       tracked binary artefact).
     - e. `docs/evidence.md`: the install-page sentence must name the new skill version. This check exists
       to catch a forgotten bump: `gen_skill.py --check` reports a stale or missing plain sentence, or a
       missing page, as drift. The page must mention "served from the" exactly once (any case, comments and
       markup included, counted with plain whitespace between the three words), in the one plain canonical
       sentence "served from the install page is <skill version>" on one line, lower-case, not inside an HTML
       comment, with no markup, link or entity between the phrase and the version, and the version followed
       only by a space, a tab, the line end or a `.` (that `.` followed only by a space, a tab, the line end
       or `<`; so `1.0.6.9` and `1.0.6<b>-rc1</b>` are refused). A second plain-whitespace mention (stale,
       styled, linked, commented or capitalized) is drift too. Deliberate markup or entities around or inside
       the phrase or the version are out of its scope and are not reliably detected (some forms pass, for
       example `1.0.6.<!---->9`, `served <b>from</b> the`, `served&nbsp;from the` or a whole sentence wrapped
       in `<b>`; some are refused, for example `**1.0.6**`). Known false
       refusal: a `<!--` earlier on the page, even inside an attribute value such as `title="<!--"`, counts
       as an open comment and refuses the plain sentence; reword that value. After editing it, regenerate the site with `python3 tools/gen_site.py` (otherwise
       `gen_site.py --check` reports `drift: site/evidence.html`).
   Run the bump in a normal git clone or worktree: `gen_skill.py` refuses (exit 2) any input or output
   with more than one hard link, so a hard-link-copied checkout (`cp -al`) is refused. For an input
   (`LICENSE`, `skill-source.md`, the hooks manifest, `docs/evidence.md`, `VERSION`) break the link by copying the
   file and moving the copy over it; never `git rm` an input.
   Then run `python3 tools/gen_skill.py`. The generator never deletes a file: this first run refuses
   (exit 2) and names the stale prior-version `aiqt-skill-<old>.zip`. Remove it yourself with
   `git rm site/downloads/aiqt-skill-<old>.zip`, then rerun `python3 tools/gen_skill.py`, which repacks
   both zips (a surviving stale zip stays a `--check` latest-only drift), and stage the new zip with
   `git add site/downloads/aiqt-skill-<new>.zip`. Note that from the 1.1.1 pack
   release on, the skill version equals the pack version (see the top of this checklist), so this bump
   happens with each release cut. A bump that misses spot a leaves
   the assertion firing (fail-closed exit 2); a bump that misses spot b, c, or d is caught by the relevant
   drift or portability gate; a bump that misses spot e is caught by `gen_skill.py --check`. Next,
   repoint the install pages' downloads at the new versioned filename, in four places that spell the
   skill version as literals (not derived from it): the download link in `site/draft-src/install.toml`;
   in `site/draft-src/install-details.toml`, the Skill `download` entry's `href`
   (`/downloads/aiqt-skill-<skill version>.zip`), its `filename` (`aiqt-skill-<skill version>.zip`) and
   the version prose in its `html` ("The Skill carries its own version, <skill version>, which ships under
   AIQT pack release <pack version>.", reworded once the two versions are equal); and the
   `/downloads/aiqt-skill-<skill version>.zip` entry of `KEPT_LIVE_PATHS` in `tools/gen_draft_site.py`.
   Search `site/draft-src/` for the old skill version and update any other prose that names it (the
   teams and future pages state the chat Skill's version). Then run `python3 tools/gen_draft_site.py`,
   which must exit 0. Its exit 2 is a refusal whose message names the cause, so read the message, not
   the exit code alone: once `KEPT_LIVE_PATHS` names the new zip, a link left at the old zip is refused
   ("is not a site-relative href or a kept live path"); a new zip that `gen_skill.py` has not yet written
   is refused ("kept live path(s) with no target under site/"); and a `VERSION` still at `N-dev` is
   refused when `gen_changelog.py` has not run since the fold. Finally, set the `Version X.Y.Z` line in the three
   hand-written condensations (`site/downloads/aiqt-instructions-8k.txt`, `-5k.txt`, `-1_5k.txt`) and
   re-bless them with `python3 tools/check_sized_instructions.py --update`. Before the generator sweep,
   confirm that both index changes are in place: `git rm site/downloads/aiqt-skill-<old>.zip` and
   `git add site/downloads/aiqt-skill-<new>.zip`. Otherwise `gen_manifest.py` refuses the untracked binary
   (exit 2, "is not a tracked in-scope path; fail-closed"). Stage the cut records the same way
   (`git add .aiqt/release-cut.toml .aiqt/core/ownership.toml`, plus `.aiqt/pending-fixes.toml` when the
   cut keeps one): `gen_manifest.py` reads the tracked set from the git index, so it refuses an unstaged
   declaration whose ownership row is in place (exit 2, "selector '.aiqt/release-cut.toml' matches no
   tracked in-scope path (NO-STRAYS)") rather than leaving it out of the manifest. Then run every
   `tools/gen_*.py` generator, with `python3 tools/gen_manifest.py` last: besides the generators named
   above, the bump also changes the outputs of `python3 tools/gen_renderers.py` (`.aiqt/core/renderers.toml`)
   and `python3 tools/gen_gensrc.py` (`.aiqt/gensrc.json`). Finally run each generator with `--check`
   (again `gen_manifest.py --check` last); every one must exit 0.

   OPF declaration checklist (OPF-SPEC section 16.1). The reference tooling's declaration file is
   `opf/tools/opf-declaration.toml`, and the shipped-file digest is carried in
   `opf/tools/_opf_declaration.py` as `SHIPPED_DECLARATION_SHA256`. Its `release` is OPF's own release
   version in `opf/VERSION`, which moves independently of the pack release version in `VERSION`, so each
   change of OPF's version sets, in one change: `opf/VERSION`; `release` in that file;
   `SHIPPED_DECLARATION_BYTES` and `SHIPPED_DECLARATION_SHA256` in `_opf_declaration.py`; and the
   restatement below and in `opf/spec/OPF-QUICKSTART.md`. A change of the supported `spec_version`, homes generation or worklog
   storage generation changes its value in `_opf_store.py`, the matching field in
   `opf/tools/opf-declaration.toml`, `SHIPPED_DECLARATION_BYTES` and `SHIPPED_DECLARATION_SHA256` in
   `_opf_declaration.py`, and the restatement below and in `opf/spec/OPF-QUICKSTART.md`; it does not by
   itself touch `opf/VERSION` or `release`. `python3 -I -B
   tools/check_opf_declaration.py` must then exit 0. It refuses a restatement that differs from the file
   and a release other than the version in `opf/VERSION`; a pack release that leaves OPF's version
   unchanged changes none of these places. `RELEASE_IDENTITY` is read from `opf/VERSION` when the module
   loads, and a missing or malformed `opf/VERSION` leaves the build without one, so every gated verb
   refuses. Adoption is bound to OPF's own version: `python3 tools/gen_manifest.py` records `opf/VERSION`
   as the release manifest's `opf-version`, and the `opf adopt` trust gate checks the plan's release
   version against that `opf-version`, never the pack manifest's `release-version` (taken from the root
   `VERSION`), refusing a manifest that records none. This release declares:

   ```toml opf-declaration
   release = "1.0.5"
   spec_version = "1.2.0"
   homes = 1
   worklog = 1
   ```
2. Compute. From the repository root on the frozen tree, run
   `sha256sum site/downloads/aiqt-skill-<skill version>.zip site/downloads/aiqt-instructions.txt` (the one
   version-numbered zip present under `site/downloads/`). These two files are the release artifacts (the
   packaged skill and its instructions), matching the set named in
   the evidence page and the `changelog.toml` reserved-key example. The mapping exports under
   `site/downloads/` (`mappings.csv`, `mappings.json`) are reference data regenerated from the corpus and
   covered by the drift and reference-facts gates, so they are not part of the release-integrity set.
   Neither is the hooks plugin's copy of the skill, `plugin/aiqt-guardrails-hooks/skills/aiqt/SKILL.md`: it
   is pack content recorded in `.aiqt/manifest.toml`, and `gen_skill --check` ties its bytes to
   `site/downloads/aiqt/SKILL.md`, the `SKILL.md` packed inside the recorded zip.
3. Record. Add a `[release.artifacts]` sub-table to the latest `[[release]]` in `changelog.toml`,
   listing every release artifact from step 2 with its `sha256:<64 lowercase hex>` digest. This arms the
   artifact-checksum gate, which hashes the latest recorded release's artifacts and fails if any recorded
   digest does not match its file. Two things are this protocol's responsibility, not the gate's:
   recording the complete release-artifact set (the gate does not establish completeness), and the
   integrity of earlier releases' recorded digests (the gate checks only the latest recorded release, so
   rewriting an older release's table is outside its scope).
4. Evidence. Fill the "Built from" and "Checksum" fields on the evidence page with the release tag and
   the per-artifact digests, each digest reproduced verbatim. This reproduction is a manual step: the
   gate confirms only that each recorded digest string appears somewhere on the page (a best-effort
   staleness check), not that it is shown in the correct field or bound to its artifact, so place each
   digest in its proper Checksum field by hand.
5. Verify. `python3 tools/check_artifact_checksums.py` must report armed and passing, and
   `bash tools/run_all_checks.sh` must be green end to end. Steps 3, 4, and 5 land as one pull request,
   merged on green. Main stays frozen from this merge until step 6b has merged: the freeze covers the
   tag and BOTH post-release pull requests (6a and 6b), which is what makes the release-delta gate print
   its `release-delta: POST-RELEASE` line on each of them. Reopening development (step 8) declares a later
   version, so it waits for step 6b too. If the freeze is broken and a commit declaring
   a later version lands before step 6b, the step 6b gate prints its ordinary `PASS:` line instead of
   `POST-RELEASE`; treat that as evidence the freeze was broken and re-check the intervening commits, not
   as the step 6b 'exit 0' evidence.
   - 5b. Pre-tag check. Run `python3 tools/check_release_build.py --pre-tag --candidate-sha <full step 5
     merge SHA> --qa-path <QA object> --qa-sha256 <its SHA-256>` and require exit 0. For the genesis
     (first recorded) release only, that is, while `.aiqt/core/releases.toml` has no `[[release]]` row,
     add `--first-pin --evidence <evidence file>`: both are required there, and the gate accepts
     `--evidence` only together with `--first-pin`. Later releases omit both. The QA object and the
     evidence file live in the maintainer QA store, outside the tree. The evidence's `demonstration`
     reference must resolve in the candidate tree, so that file is committed before the freeze (step 1).
6. Tag. The release tag is `vX.Y.Z`, where `X.Y.Z` is the release's `changelog.toml` version (for a
   release at version X.Y.Z, the tag is exactly `vX.Y.Z`); the tag-monotonicity gate requires exactly this
   `v` + version form. Apply the annotated tag to the step 5 merge commit checked in step 5b and push it.
   - 6a. Attestation commit. Keep main frozen. Branch from the tag, append one fully attested
     `[[release]]` row to `.aiqt/core/releases.toml` with every required field (`version`, `tag`,
     `tag_object_sha`, `commit_sha`, `qa-sha256`, `qa-store-path`, and `attestation-timestamps`), then
     run `python3 tools/gen_manifest.py`, which regenerates `.aiqt/manifest.toml`,
     `.aiqt/release/root.txt`, and `.aiqt/release/announce-snippet.txt`. Commit only those four files.
     `python3 tools/check_release_build.py --post-tag --attestation-commit <full attestation commit SHA>
     --qa-path <QA object>` must exit 0 before the merge, and the release-delta gate
     (`python3 tools/check_release_delta.py`) must exit 0 with its `release-delta: POST-RELEASE` status
     line, which it prints only when the head changes nothing but the post-release paths from the
     tagged commit. The gate judges the repository found from the physical location of the running
     gate file, not the current directory, and it refuses (exit 2) a launch whose path does not
     resolve to a file on disk. By design it also refuses (exit 2) a tree that is not a git checkout
     but has, in its ancestry, a `.git` entry, a git-directory name (`HEAD`, `objects`, `refs`,
     `commondir`, `gitdir`), or an entry it cannot read; the message names the directory and the
     entry it found, or the entry it could not read; it also refuses (exit 2) when the gate module is
     imported and `main()` is called directly, with no arguments and no stage-1 hand-off recorded (the
     single-stage checkout gate never falls back to the current directory). Merge without rewriting the
     gated commit (no
     squash or rebase), so the commit post-tag certified lands unchanged and its parent stays the tagged
     commit.
   - 6b. Tag key. Only after 6a has merged, record `tag = "vX.Y.Z"` in that release's `changelog.toml`
     entry: place the line inside that release's `[[release]]` table, immediately after its `version`
     line and before any sub-table header such as `[release.artifacts]` (a line appended at the end of
     the file would land inside the last sub-table and the release-delta gate rejects it). Then run
     `python3 tools/gen_manifest.py`, and land both through a separate pull request, merged on
     green before step 7. On that pull request `python3 tools/check_release_delta.py` must again exit 0
     with `release-delta: POST-RELEASE` (main is still frozen here, per step 5, so no later version is
     declared and `POST-RELEASE` is the line to expect); in `changelog.toml` it accepts only the newest release's
     `tag = "vX.Y.Z"` key. The tag-monotonicity check arms from the recorded changelog `tag` key, not from
     the git tag alone, so pushing the git tag without landing the recorded key on the protected branch
     leaves that check dormant.
   - Why the order is fixed. Post-tag allows the attestation commit to change only the release row and
     the three regenerated branch-integrity artifacts from the tagged commit; `changelog.toml` is excluded
     from that attestation delta. A tag-key commit landed between the tag and the attestation row puts
     `changelog.toml` in the delta, and post-tag fails. The tag key cannot move into the step 5 pull
     request either: the tag does not exist yet at that point, so the tag-monotonicity check cannot
     resolve it.
7. Publish. The public flip is a separate, maintainer-owned step; nothing in steps 1 to 6 depends on it.
8. Reopen development. After step 6b has merged, one pull request adds an `[unreleased]` table at the end
   of `changelog.toml` with `version` set to the next planned release (a bare `X.Y.Z` above the release
   just cut; never write the `-dev` suffix, the generators add it) and runs every `tools/gen_*.py`
   generator, with `python3 tools/gen_manifest.py` last. `VERSION`, the manifest and the announce snippet
   then say `X.Y.Z-dev`, the snippet calls it a development build that is not a release, the site shows
   "Unreleased" beside the last published release, and `CHANGELOG.md` gains an "Unreleased" section. The
   release-delta gate leaves its post-release route and checks the declared version against the attested
   release. Once `releases.toml` has a row, it refuses (exit 2) a reopen while the newest `changelog.toml`
   release has no `releases.toml` row. At genesis (no row yet: the first recorded release before its
   step 6a attestation row has merged) it does not: its genesis route only binds the manifest to the
   `[unreleased]` version plus `-dev`, so a reopen landed before step 6a passes it, and waiting for step 6b
   is then this protocol's rule alone, not a gate's. A
   later pull request whose required version bump is larger than the declared version raises it; the
   version-monotonicity gate allows raising it and refuses lowering it or removing `[unreleased]` without a
   cut. Between step 6a and this pull request main shows the plain release version with a root that
   differs from the one published for the tag (6a regenerates the manifest), so verify a release against
   the root published for its tag, never against main.

## Note on the evidence "Built from" field

The evidence page names the release tag, whose name (`vX.Y.Z`) is determined by the release version and
so is known at step 4, before the tag is applied to the merge commit in step 6. Integrity does not depend
on the commit pointer: the digests attest the artifact bytes, which do not change between recording them
and tagging.
