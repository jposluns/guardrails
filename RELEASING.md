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

1. Freeze. On the release branch, confirm `tools/gen_skill.py --check` is clean and the full
   `tools/run_all_checks.sh` is green at the freeze commit. No content changes after this point.
2. Compute. Run `sha256sum` over each published download artifact on the frozen tree.
3. Record. Add a `[release.artifacts]` sub-table to the latest `[[release]]` in `changelog.toml`,
   listing every published artifact with its `sha256:<64 lowercase hex>` digest. This arms the
   artifact-checksum gate.
4. Evidence. Fill the "Built from" and "Checksum" fields on the evidence page with the release tag and
   the per-artifact digests, each digest reproduced verbatim.
5. Verify. `tools/check_artifact_checksums.py` must report armed and passing, and
   `tools/run_all_checks.sh` must be green end to end. Steps 3, 4, and 5 land as one pull request,
   merged on green.
6. Tag. Apply an annotated tag (for example `v1.0.0`) to the merge commit and push it. Recording the tag
   on the release entry arms the tag-monotonicity check.
7. Publish. The public flip is a separate, maintainer-owned step; nothing in steps 1 to 6 depends on it.

## Note on the evidence "Built from" field

The evidence page names the release tag, whose name is fixed by this protocol before the tag is applied
to the merge commit in step 6. Integrity does not depend on the commit pointer: the digests attest the
artifact bytes, which do not change between recording them and tagging.
