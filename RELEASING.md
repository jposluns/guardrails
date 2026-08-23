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
   `bash tools/run_all_checks.sh` is green at the freeze commit. No content changes after this point.
2. Compute. From the repository root on the frozen tree, run
   `sha256sum site/downloads/aiqt-skill.zip site/downloads/aiqt-instructions.txt`. These two files are
   the 1.0.0 release artifacts (the packaged skill and its instructions), matching the set named in the
   evidence page and the `changelog.toml` reserved-key example. The mapping exports under
   `site/downloads/` (`mappings.csv`, `mappings.json`) are reference data regenerated from the corpus and
   covered by the drift and reference-facts gates, so they are not part of the release-integrity set.
3. Record. Add a `[release.artifacts]` sub-table to the latest `[[release]]` in `changelog.toml`,
   listing every release artifact from step 2 with its `sha256:<64 lowercase hex>` digest. This arms the
   artifact-checksum gate. Recording the complete release-artifact set is this protocol's
   responsibility: the gate verifies that each recorded entry matches its file, but it does not itself
   establish that the recorded set is complete.
4. Evidence. Fill the "Built from" and "Checksum" fields on the evidence page with the release tag and
   the per-artifact digests, each digest reproduced verbatim.
5. Verify. `python3 tools/check_artifact_checksums.py` must report armed and passing, and
   `bash tools/run_all_checks.sh` must be green end to end. Steps 3, 4, and 5 land as one pull request,
   merged on green.
6. Tag. Apply an annotated tag (for example `v1.0.0`) to the merge commit and push it, AND add
   `tag = "v1.0.0"` to that release's entry in `changelog.toml` as a separate committed change. The
   tag-monotonicity check arms from the recorded changelog `tag` key, not from the git tag alone, so
   pushing the git tag without recording the key leaves that check dormant.
7. Publish. The public flip is a separate, maintainer-owned step; nothing in steps 1 to 6 depends on it.

## Note on the evidence "Built from" field

The evidence page names the release tag, whose name is fixed by this protocol before the tag is applied
to the merge commit in step 6. Integrity does not depend on the commit pointer: the digests attest the
artifact bytes, which do not change between recording them and tagging.
