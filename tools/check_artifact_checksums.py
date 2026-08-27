#!/usr/bin/env python3
"""Published-artifact checksum gate for the pack downloads (GD-7). Offline, stdlib only, fail-closed.

check_site.py:21-22 records the deferral this closes: "Download-artifact checksums are tracked separately
(they need a final content baseline)." The baseline does not exist until the 1.0.0 content freeze, so this
gate ships DORMANT in the GA-3 layer-B pattern: dormancy is decided from the single-source FILE, never by
probing the environment (check_version_monotonicity.py:25-28, the guard-input-soundness rationale).

Where the digests live. In changelog.toml, as an optional per-release sub-table `[release.artifacts]`,
mirroring the optional `tag` key precedent: the sub-table is inert to gen_changelog.render_md (reads
title/version/date/items), check_versions (reads version only), and the version-monotonicity gate (extracts
only version and tag), so all three stay green with and without it. The digests sit beside the version they
attest, whose history GA-3 already freezes append-only. Shape:

    [[release]]
    version = "1.0.0"
    ...
    [release.artifacts]
    "site/downloads/aiqt-skill-1.0.1.zip" = "sha256:<64 lowercase hex>"
    "site/downloads/aiqt-instructions.txt" = "sha256:<64 lowercase hex>"

DORMANT (no release carries `artifacts`): print NOT APPLICABLE and contribute exit 0. One dormant-side
honesty invariant still runs, because a page must not claim a baseline that does not yet exist:
  D1 site/evidence.html must not assert any full digest (a `sha256:` followed by 64 lowercase hex) while no release
     records one; a checksum published with no recorded baseline is a finding (exit 1). Today the page
     honestly reads "Checksum: pending", so D1 passes.

ARMED (any release carries `artifacts`), fail-closed once set. Only the LATEST artifacts-carrying release
is hashed against the working tree; older releases' recorded digests are neither re-hashed here nor frozen
by this gate (see RESIDUALS below):
  A1 each artifact key is a repo-relative path under site/downloads/; anything else is malformed (exit 2:
     the gate attests only the published tree).
  A2 each value matches ^sha256:[0-9a-f]{64}$; a malformed or empty table is exit 2.
  A3 each named file exists and is readable; a missing or unreadable file is exit 2 (a check fails closed
     on input it cannot read, never "nothing to check").
  A4 the computed SHA-256 of the file equals the recorded value; a mismatch is exit 1, naming path,
     expected, and got.
  A5 each recorded digest's 64-hex value appears verbatim in site/evidence.html; absent is exit 1. This
     closes the false-clean where the repo digest matches but the public page still says pending or shows a
     stale value. The page must render the digest unsplit inside one element so the raw-text match holds.
  R1 (forward ratchet): once any release carries `artifacts`, every LATER release must carry it too; a
     newer release without it is exit 1. This constrains later releases only; it does not prevent
     disarming the armed release itself by editing its own table (see RESIDUALS below).

Interplay accepted by design: after arming, a change to the bytes of a RECORDED artifact (a skill-source or
rule edit regenerating the deterministic zip) fails this gate until the release protocol runs again with new
digests. That fail-closed behaviour holds only for the artifacts a release actually records; the gate does
not verify the recorded set is complete (see RESIDUALS B3), so a post-freeze change to an artifact the
release omitted is not caught here.

RESIDUALS (this dormant gate, over trusted in-repo release records; closed when the gate arms, tracked as
the LA-2 PR-2 cross-history layer):
  B1 disarm: editing the armed release's OWN [release.artifacts] table out (and resetting evidence.html)
     returns the gate to dormant / exit 0. R1 catches a later release dropping the table, not the armed
     release disarming itself.
  B2 older-release rewrite: only the latest artifacts-carrying release is hashed, and the monotonicity
     gate's identity key is version-only, so rewriting an OLDER release's recorded digests is not caught.
  M1 evidence-comment: the D1/A5 evidence scan matches raw page text, so a digest inside an HTML comment
     can satisfy A5 while the visible page still reads pending.
  M2 bare-hex: D1 keys on the `sha256:` prefix, so a bare 64-hex digest with no prefix is not matched.
  B3 set-completeness: only the artifacts a release RECORDS are hashed, and one recorded path under
     site/downloads/ satisfies the gate; the gate does not know the full published set, so a release that
     records an incomplete set (or only an unrelated file) leaves an omitted, changed artifact unattested.
     Recording the complete set is the release protocol's responsibility, not this gate's.
  B4 symlink: A1's path check is lexical (a prefix and path-parts test) while hashing follows symlinks, so
     a recorded path that is a symlink under site/downloads/ pointing outside the published tree is hashed
     without objection. The portability gate rejects committed symlinks; this gate does not.
  M3 evidence-token: the D1/A5 digest match is a lowercase substring test, neither case-insensitive nor
     token-bounded, so an uppercase digest evades D1 and a digest that is a substring of a longer hex run
     satisfies A5; A5 also tests presence, not adjacency to the correct artifact filename.
The PR-2 arming layer (a cross-history check: a release armed at BASE must be armed at HEAD, and a shipped
release's artifacts table byte-identical base-to-head) closes B1/B2 before the gate arms; the
set-completeness (B3), real-path containment (B4), and evidence-token (M3) hardenings land with arming too.

  check_artifact_checksums.py            check the invariants against the working tree
  check_artifact_checksums.py --self-test  deterministic self-test (no wall clock; a random temp-root path suffix does not affect the verdict)

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE
  1  a real finding (a digest mismatch, a stale/absent evidence claim, or a forward-ratchet break)
  2  malformed input, a missing/unreadable artifact or evidence file, or a read error (fail-closed)
"""
import hashlib
import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402

ARTIFACT_ROOT = "site/downloads/"
EVIDENCE_PATH = "site/evidence.html"
# A well-formed recorded digest: the sha256 algorithm tag and 64 lowercase hex (A2).
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
# A published full digest token on the evidence page (D1 dormant honesty; A5 raw-text match uses the hex).
EVIDENCE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable or malformed input is never treated as an empty or clean result."""


# --- release and evidence extraction (fail-closed on unreadable or malformed input) -----------------

def extract_releases(data):
    """Return a list of {'version', 'artifacts'} dicts from parsed changelog data, in array order (oldest
    to newest). `artifacts` is the sub-table dict when present, else None. Raises GateError (fail-closed)
    on a missing array, a non-table entry, or an `artifacts` value that is present but not a table."""
    releases = data.get("release")
    if not isinstance(releases, list) or not releases:
        raise GateError("changelog.toml has no [[release]] tables")
    out = []
    for idx, rel in enumerate(releases):
        if not isinstance(rel, dict):
            raise GateError("release #{} is not a table ({!r})".format(idx + 1, rel))
        artifacts = rel.get("artifacts") if "artifacts" in rel else None
        if artifacts is not None and not isinstance(artifacts, dict):
            raise GateError("release #{} has a non-table `artifacts` value ({!r})".format(idx + 1, artifacts))
        out.append({"version": rel.get("version", "?"), "artifacts": artifacts})
    return out


def load_changelog(root):
    try:
        data = load_toml(root / "changelog.toml")
    except (OSError, ValueError) as exc:
        raise GateError("cannot read changelog.toml: {}".format(exc))
    return extract_releases(data)


def load_evidence(root):
    """Read site/evidence.html as text. An absent or unreadable page is fail-closed: the evidence check
    (D1 dormant, A5 armed) covers it, so it can never read as nothing to check and pass."""
    try:
        return (root / EVIDENCE_PATH).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise GateError("cannot read {}: {}".format(EVIDENCE_PATH, exc))


# --- pure logic (validation, ratchet, hashing; the value-level checks the self-test pins) -----------

def check_ratchet(releases):
    """R1. Once a release carries `artifacts`, every later release must too. Returns finding strings."""
    armed = [i for i, r in enumerate(releases) if r["artifacts"] is not None]
    if not armed:
        return []
    findings = []
    for i in range(armed[0], len(releases)):
        if releases[i]["artifacts"] is None:
            findings.append("release {} drops the artifacts table a preceding release set; a later release "
                            "cannot silently drop the checksum baseline (forward ratchet)".format(
                                releases[i]["version"]))
    return findings


def latest_armed(releases):
    """The last release carrying `artifacts` (the one attested against the working tree), or None."""
    for rel in reversed(releases):
        if rel["artifacts"] is not None:
            return rel
    return None


def validate_table(version, artifacts):
    """A1/A2. The target release's artifacts table must be non-empty, key every path under site/downloads/,
    and record each value as sha256 + 64 lowercase hex. Returns {path: hex}. Raises GateError (exit 2) on
    any malformed input: a malformed baseline can never be answered, so it fails closed rather than as a
    finding."""
    if not artifacts:
        raise GateError("release {} records an empty [release.artifacts] table; an armed release must name "
                        "at least one artifact and its digest".format(version))
    digests = {}
    for path, value in artifacts.items():
        if path.startswith("/") or ".." in Path(path).parts or not path.startswith(ARTIFACT_ROOT):
            raise GateError("release {} artifact key {!r} is not a repo-relative path under {} (A1)".format(
                version, path, ARTIFACT_ROOT))
        if not isinstance(value, str):
            raise GateError("release {} artifact {!r} has a non-string digest {!r} (A2)".format(
                version, path, value))
        m = DIGEST_RE.match(value)
        if m is None:
            raise GateError("release {} artifact {!r} digest {!r} is not sha256 + 64 lowercase hex "
                            "(A2)".format(version, path, value))
        digests[path] = m.group(1)
    return digests


def hash_file(root, path):
    """A3. The SHA-256 hex of the artifact at root/path. A missing or unreadable file is fail-closed
    (exit 2): a check that cannot read the artifact it attests reports that, never a clean pass."""
    try:
        data = (root / path).read_bytes()
    except OSError as exc:
        raise GateError("cannot read artifact {} (A3): {}".format(path, exc))
    return hashlib.sha256(data).hexdigest()


def check_evidence_dormant(evidence):
    """D1. While dormant, the evidence page must not assert any published digest. Returns findings."""
    if EVIDENCE_DIGEST_RE.search(evidence):
        return ["{} asserts a published checksum while no release records one; a page must not claim a "
                "baseline before it exists (D1)".format(EVIDENCE_PATH)]
    return []


# --- the two states ---------------------------------------------------------------------------------

def dormant(evidence):
    """No release carries artifacts. Prints the honest NOT APPLICABLE, runs D1, returns findings."""
    print("artifact-checksums: NOT APPLICABLE (no release records artifact checksums; the gate arms with "
          "the first recorded digest at the content freeze)")
    return check_evidence_dormant(evidence)


def armed(root, releases, evidence):
    """A release carries artifacts. Prints its status, returns findings. Raises GateError (exit 2) on any
    malformed input or unreadable artifact (A1/A2/A3); the ratchet (R1), the hash mismatch (A4), and the
    stale-evidence check (A5) are findings (exit 1)."""
    findings = check_ratchet(releases)
    target = latest_armed(releases)
    digests = validate_table(target["version"], target["artifacts"])
    for path, expected in sorted(digests.items()):
        got = hash_file(root, path)
        if got != expected:
            findings.append("release {} artifact {}: recorded sha256:{} but the working tree hashes to "
                            "sha256:{} (A4)".format(target["version"], path, expected, got))
        if expected not in evidence:
            findings.append("release {} artifact {}: recorded digest sha256:{} does not appear in {} "
                            "(the public page is stale or still pending) (A5)".format(
                                target["version"], path, expected, EVIDENCE_PATH))
    if not findings:
        print("artifact-checksums: ARMED (release {}: {} artifact(s) hashed and matched, each digest "
              "present in {})".format(target["version"], len(digests), EVIDENCE_PATH))
    return findings


def run(root):
    """Run the gate against `root`. Returns the exit code 0/1/2."""
    try:
        releases = load_changelog(root)
        evidence = load_evidence(root)
        if latest_armed(releases) is None:
            findings = dormant(evidence)
        else:
            findings = armed(root, releases, evidence)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} artifact-checksum finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: artifact checksums hold (dormant, or every recorded digest matches its file and the "
          "evidence page)")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Deterministic temp-root fixtures in the house style (no wall clock; the temp-root path suffix is random but the verdict is not): the dormant path, the
# D1 honesty catch, and each armed fail case (A1..A5, R1) run against a throwaway tree where a writable
# tempdir exists, and are reported PARTIAL (never a false pass) where none does. The inertness companion
# (a [release.artifacts] table is inert to gen_changelog and the monotonicity gate; check_versions reads
# only the version, so it is inert by construction and not re-exercised here) is a pure in-memory check
# that ALWAYS runs.

_EVIDENCE_CLEAN = "<html><body><p>Checksum: pending</p></body></html>\n"


def _changelog_text(specs):
    """specs is a list of (version, artifacts_dict_or_None), oldest to newest."""
    lines = ['title = "Changelog: self-test"', 'note = "self-test"', ""]
    for version, artifacts in specs:
        lines += ["[[release]]", 'title = "r"', 'version = "{}"'.format(version),
                  'date = "2026-01-01"', 'items = ["x"]']
        if artifacts is not None:
            lines.append("[release.artifacts]")
            for path, value in artifacts.items():
                lines.append('"{}" = "{}"'.format(path, value))
        lines.append("")
    return "\n".join(lines)


def _build(base, specs, evidence, downloads):
    """Write a throwaway root: changelog.toml from specs, site/evidence.html from evidence text, and each
    name->bytes under site/downloads/. Returns base."""
    base.mkdir(parents=True, exist_ok=True)
    (base / "changelog.toml").write_text(_changelog_text(specs), encoding="utf-8")
    ev = base / EVIDENCE_PATH
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(evidence, encoding="utf-8")
    dl = base / ARTIFACT_ROOT
    dl.mkdir(parents=True, exist_ok=True)
    for name, data in downloads.items():
        (dl / name).write_bytes(data)
    return base


def _run_quiet(root):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root)


def self_test_main():
    failures = []

    # --- always-run inertness companion (pure, no filesystem) --------------------------------------
    # A [release.artifacts] sub-table is provably inert to the two changelog readers exercised here
    # (gen_changelog and the monotonicity gate); check_versions reads only the version and is inert by
    # construction, so it is not re-exercised.
    from gen_changelog import render_md
    from check_version_monotonicity import _releases_from_data
    plain = {"title": "C", "note": "n", "release": [
        {"title": "r", "version": "1.0.0", "date": "2026-01-01", "items": ["x"]}]}
    with_artifacts = {"title": "C", "note": "n", "release": [
        {"title": "r", "version": "1.0.0", "date": "2026-01-01", "items": ["x"],
         "artifacts": {"site/downloads/aiqt-skill.zip": "sha256:" + "0" * 64}}]}
    if render_md(plain) != render_md(with_artifacts):
        failures.append("inertness: gen_changelog.render_md differs with a [release.artifacts] table")
    if _releases_from_data(plain, "head") != _releases_from_data(with_artifacts, "head"):
        failures.append("inertness: the monotonicity gate extracts a different result with artifacts")

    # --- temp-root cases (skipped with a note where no writable tempdir exists) --------------------
    import shutil
    import tempfile

    content = {"aiqt-skill.zip": b"skill artifact bytes", "aiqt-instructions.txt": b"instruction bytes"}
    hx = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    zip_key, txt_key = "site/downloads/aiqt-skill.zip", "site/downloads/aiqt-instructions.txt"
    good = {zip_key: "sha256:" + hx["aiqt-skill.zip"], txt_key: "sha256:" + hx["aiqt-instructions.txt"]}
    evidence_armed = "<html><body><code>{}</code><code>{}</code></body></html>\n".format(
        hx["aiqt-skill.zip"], hx["aiqt-instructions.txt"])

    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-checksums-selftest-"))
    except OSError:
        base_tmp = None

    if base_tmp is None:
        print("SELF-TEST NOTE: no writable temp directory; the dormant and armed temp-root cases were "
              "SKIPPED (the inertness companion above still ran)", file=sys.stderr)
        ran = False
    else:
        ran = True
        try:
            # 1. dormant clean: no artifacts, evidence has no digest token -> exit 0.
            r = _build(base_tmp / "dormant-clean", [("1.0.0", None)], _EVIDENCE_CLEAN, content)
            if _run_quiet(r) != 0:
                failures.append("dormant clean expected exit 0")

            # 2. dormant with a digest asserted in evidence.html -> exit 1 (D1).
            r = _build(base_tmp / "dormant-claim", [("1.0.0", None)],
                       "<code>sha256:{}</code>".format("a" * 64), content)
            if _run_quiet(r) != 1:
                failures.append("dormant with an evidence digest expected exit 1 (D1)")

            # 3. armed matching digests, both present on the evidence page -> exit 0.
            r = _build(base_tmp / "armed-match", [("1.0.0", good)], evidence_armed, content)
            if _run_quiet(r) != 0:
                failures.append("armed matching digests expected exit 0")

            # 4. armed mismatch: recorded digest does not match the file bytes -> exit 1 (A4).
            bad = dict(good)
            bad[zip_key] = "sha256:" + ("b" * 64)
            r = _build(base_tmp / "armed-mismatch", [("1.0.0", bad)],
                       evidence_armed + "<code>{}</code>".format("b" * 64), content)
            if _run_quiet(r) != 1:
                failures.append("armed digest mismatch expected exit 1 (A4)")

            # 5. malformed digest (not 64 hex) -> exit 2 (A2).
            r = _build(base_tmp / "armed-malformed", [("1.0.0", {zip_key: "sha256:xyz"})],
                       evidence_armed, content)
            if _run_quiet(r) != 2:
                failures.append("malformed digest expected fail-closed exit 2 (A2)")

            # 6. missing artifact file -> exit 2 (A3).
            r = _build(base_tmp / "armed-missing", [("1.0.0", good)], evidence_armed,
                       {"aiqt-instructions.txt": content["aiqt-instructions.txt"]})
            if _run_quiet(r) != 2:
                failures.append("missing artifact file expected fail-closed exit 2 (A3)")

            # 7. a path outside site/downloads/ -> exit 2 (A1).
            r = _build(base_tmp / "armed-badpath", [("1.0.0", {"site/other/x": "sha256:" + "c" * 64})],
                       evidence_armed, content)
            if _run_quiet(r) != 2:
                failures.append("artifact path outside site/downloads/ expected fail-closed exit 2 (A1)")

            # 8. a later release drops artifacts after an armed one -> exit 1 (R1).
            r = _build(base_tmp / "armed-disarm", [("1.0.0", good), ("1.1.0", None)],
                       evidence_armed, content)
            if _run_quiet(r) != 1:
                failures.append("forward ratchet (a later release without artifacts) expected exit 1 (R1)")

            # 9. armed with the digest absent from evidence.html -> exit 1 (A5).
            r = _build(base_tmp / "armed-stale", [("1.0.0", good)], _EVIDENCE_CLEAN, content)
            if _run_quiet(r) != 1:
                failures.append("armed with a digest absent from evidence.html expected exit 1 (A5)")

            # 10. an empty artifacts table -> exit 2 (malformed, decided per the plan).
            r = _build(base_tmp / "armed-empty", [("1.0.0", {})], evidence_armed, content)
            if _run_quiet(r) != 2:
                failures.append("an empty artifacts table expected fail-closed exit 2")

            # 11. an unreadable (invalid-UTF-8) evidence.html -> exit 2, fail-closed, never an uncaught
            #     traceback (the check-fails-closed-on-unreadable contract; the repo's F-142b convention).
            r = _build(base_tmp / "evidence-badutf8", [("1.0.0", None)], _EVIDENCE_CLEAN, content)
            (r / EVIDENCE_PATH).write_bytes(b"<html>\xff\xfe pending</html>")
            if _run_quiet(r) != 2:
                failures.append("invalid-UTF-8 evidence.html expected fail-closed exit 2 (not a traceback)")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    if ran:
        print("SELF-TEST PASS: the inertness companion, the dormant clean path and D1 honesty catch, and "
              "each armed fail case (A1 path, A2 malformed, A3 missing, A4 mismatch, A5 stale evidence, R1 "
              "forward ratchet), and the invalid-UTF-8 evidence fail-closed all hold")
    else:
        print("SELF-TEST PASS (PARTIAL): the inertness companion holds; the dormant and armed temp-root "
              "cases were SKIPPED (no writable temp directory), so those invariants are UNVERIFIED this run")
    return 0


def _parse_args(argv):
    self_test = False
    for arg in argv:
        if arg == "--self-test":
            self_test = True
        else:
            print("usage: check_artifact_checksums.py [--self-test]", file=sys.stderr)
            return None
    return (self_test,)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    (self_test,) = parsed
    if self_test:
        return self_test_main()
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
