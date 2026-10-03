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
   (the version-numbered `site/downloads/aiqt-skill-<version>.zip`, which the site links to, and
   `site/downloads/aiqt-instructions.txt`) and their generating inputs (the corpus and
   `tools/gen_skill.py`) do not change; `site/downloads/aiqt-skill.zip` is a stable "latest" alias kept
   byte-identical to the version-numbered copy (both are written from the same bytes, and `gen_skill
   --check` compares each to disk, so they cannot diverge). The
   release-metadata edits prescribed below (the recorded digests, the evidence fields, the attestation
   row with its regenerated branch-integrity artifacts, and the tag key) are the only changes permitted
   after this point. For the genesis (first recorded) release, the freeze pull request also carries the
   `demonstration` file that the step 5b first-pin evidence references: the gate resolves that reference
   in the candidate tree, so the file must be committed before the freeze. Its content is not defined
   here; it remains open under the first-release-evidence item.

   Skill version bump checklist. The skill is independently versioned. The concrete version-numbered
   filename `aiqt-skill-<version>.zip` is spelled as a literal in four places, kept consistent by a
   fail-closed version-match assertion in `gen_skill.build_outputs` and `gen_skill.py`'s orphan-clean plus
   `--check` (which flags a stale version-numbered zip), not by true single-sourcing (`versioned_zip_basename` is only the shared
   filename SHAPE). To bump the skill version, edit all four, then regenerate:
     - a. `.aiqt/core/skill/skill-source.md`: the `meta` `version` (the authoritative source the assertion
       and both generators read).
     - b. `tools/gen_skill.py`: the `ZIP_VERSIONED_PARTS` literal AND its matching `GENSRC_OUTPUTS` target
       (both spell the version-numbered basename).
     - c. `tools/check_portability.py`: the `BINARY_ALLOW` set (the version-numbered zip is a shipped
       binary allowed past the portability text scan).
     - d. `.aiqt/core/ownership.toml`: the `[checkout]` `binary` list (the version-numbered zip is a
       tracked binary artefact).
   Then run `python3 tools/gen_skill.py`, which repacks both zips and orphan-cleans the prior-version
   `aiqt-skill-<old>.zip` (its `--check` reports that stale zip as drift). A bump that misses spot a leaves
   the assertion firing (fail-closed exit 2); a bump that misses spot b, c, or d is caught by the relevant
   drift or portability gate. Finally, run `python3 tools/gen_install.py` to repoint the install-page
   download button at the new versioned filename.
2. Compute. From the repository root on the frozen tree, run
   `sha256sum site/downloads/aiqt-skill-<version>.zip site/downloads/aiqt-instructions.txt`, where
   `<version>` is the skill version from step 1. These two files are the release artifacts (the packaged
   skill and its instructions), matching the set named in
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
   merged on green. Main stays frozen from this merge until step 6b has merged: the freeze covers the
   tag and BOTH post-release pull requests (6a and 6b), which is what makes the release-delta gate print
   its `release-delta: POST-RELEASE` line on each of them. If the freeze is broken and a commit declaring
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
     gate file, and by design it refuses (exit 2) a tree that is not a git checkout but has a `.git`
     entry or a git-directory name (`HEAD`, `objects`, `refs`, `commondir`, `gitdir`) in its
     ancestry; the message names the directory and the entry it found. Merge without rewriting the
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

## Note on the evidence "Built from" field

The evidence page names the release tag, whose name (`vX.Y.Z`) is determined by the release version and
so is known at step 4, before the tag is applied to the merge commit in step 6. Integrity does not depend
on the commit pointer: the digests attest the artifact bytes, which do not change between recording them
and tagging.
