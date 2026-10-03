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

1. Freeze. On the release branch, confirm `python3 tools/gen_skill.py --check` is clean and the full
   `bash tools/run_all_checks.sh` is green at the freeze commit. After the freeze the release artifacts
   (the version-numbered `site/downloads/aiqt-skill-<skill version>.zip`, which the site links to, and
   `site/downloads/aiqt-instructions.txt`) and their generating inputs (the corpus and
   `tools/gen_skill.py`) do not change; `site/downloads/aiqt-skill.zip` is a stable "latest" alias kept
   byte-identical to the version-numbered copy (both are written from the same bytes, and `gen_skill
   --check` compares each to disk, so they cannot diverge). The
   release-metadata edits prescribed below (the recorded digests, the evidence fields, and the tag key)
   are the only changes permitted after this point.

   Skill version bump checklist. The skill is independently versioned today (skill 1.0.6 under pack
   release 1.0.5); from the 1.1.1 pack release on, the skill version equals the pack version, and the
   check that mirrors the two lands with the 1.1.1 cut. Only the latest skill version is served: exactly
   one version-numbered zip sits under `site/downloads/`, and `gen_skill.py --check` reports a stale or
   missing one as latest-only drift. The concrete version-numbered
   filename `aiqt-skill-<version>.zip` is spelled as a literal in four places, kept consistent by a
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
       sentence "served from the install page is <version>" on one line, lower-case, not inside an HTML
       comment, with no markup, link or entity between the phrase and the version, and the version followed
       only by a space, a tab, the line end or a `.` (that `.` followed only by a space, a tab, the line end
       or `<`; so `1.0.6.9` and `1.0.6<b>-rc1</b>` are refused). A second plain-whitespace mention (stale,
       styled, linked, commented or capitalized) is drift too. Deliberate markup or entities around or inside
       the phrase or the version are out of its scope and are not detected, for example `1.0.6.<!---->9`,
       `served <b>from</b> the`, `served&nbsp;from the` or a whole sentence wrapped in `<b>`. Known false
       refusal: a `<!--` earlier on the page, even inside an attribute value such as `title="<!--"`, counts
       as an open comment and refuses the plain sentence; reword that value. After editing it, regenerate the site with `python3 tools/gen_site.py` (otherwise
       `gen_site.py --check` reports `drift: site/evidence.html`).
   Run the bump in a normal git clone or worktree: `gen_skill.py` refuses (exit 2) any input or output
   with more than one hard link, so a hard-link-copied checkout (`cp -al`) is refused. For an input
   (`LICENSE`, `skill-source.md`, the hooks manifest, `docs/evidence.md`) break the link by copying the
   file and moving the copy over it; never `git rm` an input.
   Then run `python3 tools/gen_skill.py`. The generator never deletes a file: this first run refuses
   (exit 2) and names the stale prior-version `aiqt-skill-<old>.zip`. Remove it yourself with
   `git rm site/downloads/aiqt-skill-<old>.zip`, then rerun `python3 tools/gen_skill.py`, which repacks
   both zips (a surviving stale zip stays a `--check` latest-only drift), and stage the new zip with
   `git add site/downloads/aiqt-skill-<new>.zip`. Note that from the 1.1.1 pack
   release on, the skill version equals the pack version (see the top of this checklist), so this bump
   happens with each release cut. A bump that misses spot a leaves
   the assertion firing (fail-closed exit 2); a bump that misses spot b, c, or d is caught by the relevant
   drift or portability gate; a bump that misses spot e is caught by `gen_skill.py --check`. Next, run
   `python3 tools/gen_install.py` to repoint the install-page
   download button at the new versioned filename. Finally, set the `Version X.Y.Z` line in the three
   hand-written condensations (`site/downloads/aiqt-instructions-8k.txt`, `-5k.txt`, `-1_5k.txt`) and
   re-bless them with `python3 tools/check_sized_instructions.py --update`. Before the generator sweep,
   confirm that both index changes are in place: `git rm site/downloads/aiqt-skill-<old>.zip` and
   `git add site/downloads/aiqt-skill-<new>.zip`. Otherwise `gen_manifest.py` refuses the untracked binary
   (exit 2, "is not a tracked in-scope path; fail-closed"). Then run every
   `tools/gen_*.py` generator, with `python3 tools/gen_manifest.py` last: besides the generators named
   above, the bump also changes the outputs of `python3 tools/gen_renderers.py` (`.aiqt/core/renderers.toml`)
   and `python3 tools/gen_gensrc.py` (`.aiqt/gensrc.json`). Finally run each generator with `--check`
   (again `gen_manifest.py --check` last); every one must exit 0.
2. Compute. From the repository root on the frozen tree, run
   `sha256sum site/downloads/aiqt-skill-<skill version>.zip site/downloads/aiqt-instructions.txt` (the one
   version-numbered zip present under `site/downloads/`). These two files are the release artifacts (the
   packaged skill and its instructions), matching the set named in
   the evidence page and the `changelog.toml` reserved-key example. The mapping exports under
   `site/downloads/` (`mappings.csv`, `mappings.json`) are reference data regenerated from the corpus and
   covered by the drift and reference-facts gates, so they are not part of the release-integrity set.
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
   merged on green.
6. Tag. The release tag is `vX.Y.Z`, where `X.Y.Z` is the release's `changelog.toml` version (for a
   release at version 1.0.5, the tag is `v1.0.5`); the tag-monotonicity gate requires exactly this `v` + version form. Apply
   the annotated tag to the step 5 merge commit and push it, then record `tag = "vX.Y.Z"` in that
   release's `changelog.toml` entry through a second pull request, merged on green before step 7. The
   tag-monotonicity check arms from the recorded changelog `tag` key, not from the git tag alone, so
   pushing the git tag without landing the recorded key on the protected branch leaves that check
   dormant.
7. Publish. The public flip is a separate, maintainer-owned step; nothing in steps 1 to 6 depends on it.

## Note on the evidence "Built from" field

The evidence page names the release tag, whose name (`vX.Y.Z`) is determined by the release version and
so is known at step 4, before the tag is applied to the merge commit in step 6. Integrity does not depend
on the commit pointer: the digests attest the artifact bytes, which do not change between recording them
and tagging.
