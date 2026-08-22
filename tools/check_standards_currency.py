#!/usr/bin/env python3
"""Offline staleness audit for the standards crosswalk manifests (CW-3, smallest-first slice).

Every .aiqt/standards/*.toml carries a `retrieved` date and a `status` (stable/beta/snapshot). Content
in a cited standard moves, so a pinned manifest goes stale. This audit reads `retrieved` + `status`,
compares to today, and reports manifests that are due for review or overdue. Stdlib-only, offline; it
reuses _standards.load_manifests (which fully validates every manifest and fails closed on a malformed
one), so a manifest that cannot even be parsed is an exit-2 fail-closed, never a silent "current".

  check_standards_currency.py [--std DIR]   audit the manifests (default .aiqt/standards under repo root)
  check_standards_currency.py --warn-only    never exit 1 on a stale finding; still exit 2 on malformed
  check_standards_currency.py --self-test    deterministic self-test (fixed reference date, no wall clock)

Exit convention (mirrors check_mappings.py):
  0  all manifests current enough (warnings may still print)
  1  at least one stale-manifest FAIL finding (suppressed to 0 under --warn-only)
  2  a malformed manifest, an unparseable/missing `retrieved` date, or a read error (fail-closed)

Why this is NOT a pull-request gate: its result depends on wall-clock time, not on the diff under
review, so the same commit would pass today and fail next month. It runs only on the scheduled
`currency.yml` gate-on-main (a durable red run is the intended staleness signal) and is available
locally in `--warn-only` mode. Only its deterministic `--self-test` is wired into the PR gate. See the
CW-3 synthesis, sections 2 and 4.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _standards import Manifest, load_manifests, ManifestError  # noqa: E402

try:
    from gen_rules import repo_root  # noqa: E402  reuse the repo's own root finder
except Exception:  # pragma: no cover - gen_rules is a sibling; this only guards an odd import env
    def repo_root():
        return Path(__file__).resolve().parent.parent


# Per-status policy, in days of age since `retrieved`. warn=None means "warn immediately" (a
# point-in-time or draft capture is inherently a review-me state); otherwise warn once age exceeds the
# warn count. FAIL once age exceeds the fail count. Confirmed defaults (CW-3 Q1, Architect-set):
#   stable   warn > 365, fail > 730   (slow-moving published standards; a year's notice, two-year backstop)
#   snapshot warn now,   fail > 365   (a pinned content version is always "review me"; fail once a year old)
#   beta     warn now,   fail > 180   (a draft is volatile; do not let it linger past six months)
POLICY = {
    "stable": (365, 730),
    "snapshot": (None, 365),
    "beta": (None, 180),
}

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
MALFORMED = "MALFORMED"


def _age_days(retrieved, today):
    """Age in days of a `retrieved` value against `today`. Raises ValueError if unparseable."""
    d = date.fromisoformat(str(retrieved).strip())
    return (today - d).days


def classify(status, retrieved, today):
    """Classify one manifest's currency. Returns (level, age_days_or_None, threshold_or_None, detail).

    level is OK / WARN / FAIL / MALFORMED. MALFORMED (unparseable date or unknown status) is fail-closed:
    an unreadable date must never read as current."""
    if status not in POLICY:
        return (MALFORMED, None, None, "unknown status {!r}".format(status))
    try:
        age = _age_days(retrieved, today)
    except (ValueError, TypeError) as exc:
        return (MALFORMED, None, None, "unparseable retrieved date {!r}: {}".format(retrieved, exc))
    if age < 0:
        # A retrieved date in the future is not a staleness pass; it is a bad date. Fail-closed.
        return (MALFORMED, age, None, "retrieved date {!r} is in the future".format(retrieved))
    warn_at, fail_at = POLICY[status]
    if age > fail_at:
        return (FAIL, age, fail_at, "age {} days exceeds FAIL threshold {} days".format(age, fail_at))
    if warn_at is None:
        return (WARN, age, None, "{} status is inherently review-me (age {} days)".format(status, age))
    if age > warn_at:
        return (WARN, age, warn_at, "age {} days exceeds WARN threshold {} days".format(age, warn_at))
    return (OK, age, None, "age {} days is within thresholds".format(age))


def audit(manifests, today):
    """Classify every manifest. Returns (results, worst) where results is a list of
    (map_key, name, edition, status, retrieved, level, detail, path) and worst is the exit code 0/1/2."""
    results = []
    worst = 0
    for map_key in sorted(manifests):
        m = manifests[map_key]
        level, _age, _thr, detail = classify(m.status, m.retrieved, today)
        results.append((map_key, m.name, m.edition, m.status, m.retrieved, level, detail,
                        m.path.name))
        if level == MALFORMED:
            worst = 2
        elif level == FAIL and worst < 1:
            worst = 1
    return results, worst


def run(std_dir, today, warn_only=False):
    """Audit the manifests under std_dir as of `today`; print a report; return the exit code."""
    try:
        manifests = load_manifests(std_dir)
    except (ManifestError, OSError) as exc:
        # OSError too: an unreadable manifest file or an unlistable standards dir is a read error, not a
        # clean skip. Fail closed (exit 2) rather than escape as a traceback.
        print("MALFORMED: {}".format(exc), file=sys.stderr)
        return 2
    if not manifests:
        # Fail-closed. The standards manifests are a committed source-of-truth; an absent or empty
        # .aiqt/standards/ is an integrity anomaly (deleted or misplaced), not "nothing to audit", the
        # same reasoning check_mappings.py applies to an absent corpus. Never a false clean.
        print("MALFORMED: no standards manifests found under {} (source-of-truth missing)".format(
            std_dir), file=sys.stderr)
        return 2

    results, worst = audit(manifests, today)
    fails = [r for r in results if r[5] == FAIL]
    warns = [r for r in results if r[5] == WARN]

    for map_key, name, edition, status, retrieved, level, detail, fname in results:
        if level == OK:
            continue
        print("{}: .aiqt/standards/{}: {} {} ({}) retrieved {}: {}".format(
            level, fname, map_key, status, edition, retrieved, detail))

    # Exit from audit()'s `worst` (the precedence the self-test exercises), so the shipped exit contract
    # and the tested one are the same logic. --warn-only downgrades ONLY a FAIL (worst==1) to 0; it never
    # masks a MALFORMED (worst==2), which stays a fail-closed read/parse error.
    n = len(results)
    if worst == 2:
        print("MALFORMED: at least one manifest could not be audited (fail-closed)", file=sys.stderr)
        return 2
    if worst == 1:
        if warn_only:
            print("WARN-ONLY: {} manifest(s) past their FAIL threshold (not failing under --warn-only); "
                  "{} at WARN".format(len(fails), len(warns)))
            return 0
        print("FAIL: {} stale standards manifest(s) of {} audited".format(len(fails), n))
        return 1
    if warns:
        print("PASS: {} manifest(s) current; {} at WARN (review-due, not failing)".format(n, len(warns)))
        return 0
    print("PASS: all {} standards manifest(s) are current".format(n))
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Deterministic against a fixed reference date (no wall clock). It drives classify() with crafted
# (status, retrieved) pairs across every threshold boundary, checks audit()'s precedence, and exercises
# run() itself over synthetic manifest files written to a private tempdir (current->0, stale->1,
# stale+--warn-only->0, malformed->2, malformed+--warn-only->2, absent->2). The run()-level cases need a
# writable tempdir and are skipped (with a printed note, never a false pass) where none exists; CI always
# has one. This is the only part of the tool wired into the PR gate.

def self_test_main():
    today = date(2026, 8, 17)

    def days_ago(n):
        return (today.fromordinal(today.toordinal() - n)).isoformat()

    cases = [
        # (status, retrieved, expected_level)
        ("stable", days_ago(10), OK),
        ("stable", days_ago(365), OK),        # exactly 365, not > 365
        ("stable", days_ago(366), WARN),      # just over warn
        ("stable", days_ago(730), WARN),      # exactly 730, not > 730
        ("stable", days_ago(731), FAIL),      # just over fail
        ("snapshot", days_ago(0), WARN),      # warn immediately
        ("snapshot", days_ago(365), WARN),    # still only warn at exactly 365
        ("snapshot", days_ago(366), FAIL),    # over the year backstop
        ("beta", days_ago(0), WARN),          # warn immediately
        ("beta", days_ago(180), WARN),        # exactly 180
        ("beta", days_ago(181), FAIL),        # over six months
        ("stable", "not-a-date", MALFORMED),  # unparseable
        ("stable", days_ago(-5), MALFORMED),  # future date, fail-closed
        ("weird", days_ago(10), MALFORMED),   # unknown status, fail-closed
    ]
    failures = []
    for status, retrieved, expected in cases:
        level, age, thr, detail = classify(status, retrieved, today)
        if level != expected:
            failures.append("classify({!r}, {!r}) -> {} (age={}, detail={}); expected {}".format(
                status, retrieved, level, age, detail, expected))

    # audit() aggregation precedence: a MALFORMED must dominate a FAIL must dominate OK.
    class _M:
        def __init__(self, mk, status, retrieved):
            self.map_key, self.status, self.retrieved = mk, status, retrieved
            self.name, self.edition = mk, "ed"
            self.path = Path(mk + ".toml")

    only_fail = {"a": _M("a", "beta", days_ago(400)), "b": _M("b", "stable", days_ago(1))}
    _r, worst = audit(only_fail, today)
    if worst != 1:
        failures.append("audit with a FAIL and an OK expected worst=1, got {}".format(worst))
    with_malformed = {"a": _M("a", "beta", days_ago(400)), "b": _M("b", "stable", "bad")}
    _r, worst = audit(with_malformed, today)
    if worst != 2:
        failures.append("audit with a MALFORMED and a FAIL expected worst=2, got {}".format(worst))
    all_ok = {"a": _M("a", "stable", days_ago(1))}
    _r, worst = audit(all_ok, today)
    if worst != 0:
        failures.append("audit with only OK expected worst=0, got {}".format(worst))

    # Manifest.url validation (CW-3): the optional canonical url. When present it must be an https://
    # URL with a host; a malformed or non-string url fails closed with ManifestError, a valid one
    # passes, and an absent url is None. Deterministic, no network (offline validation only).
    def _url_data(url=None, with_url=False):
        data = {
            "map-key": "map-urltest", "name": "URL test", "publisher": "AIQT self-test",
            "edition": "2026", "kind": "control", "status": "stable", "citation-unit": "control",
            "id-pattern": "ST[0-9]{2}", "source-artefact": "self-test fixture",
            "retrieved": days_ago(1), "id": [{"code": "ST01", "title": "one"}],
        }
        if with_url:
            data["url"] = url
        return data

    def _parses(url=None, with_url=False):
        return Manifest(Path("urltest.toml"), _url_data(url, with_url))

    def _rejects(url):
        try:
            Manifest(Path("urltest.toml"), _url_data(url, with_url=True))
        except ManifestError:
            return True
        return False

    if _parses(with_url=False).url is not None:
        failures.append("Manifest with no url expected url None")
    good = _parses("https://example.org/path", with_url=True)
    if good.url != "https://example.org/path":
        failures.append("Manifest with a valid https url expected url preserved, got {!r}".format(
            good.url))
    for bad in ("http://x", "ftp://x", "https://", "example.org", "", 123, ["https://x"],
                "https://exa mple.org", "https://ho\tst.org", "https://h\x01ost.org",
                "https://:443/path", "https://user@", "https:///path",
                "https://[broken", "https://example.org:bad"):
        if not _rejects(bad):
            failures.append("Manifest with a malformed url {!r} expected ManifestError".format(bad))

    # run()-level exit contract, against real manifest files: the actual CLI entry point, including the
    # --warn-only masking behaviour (a FAIL downgrades to 0, a MALFORMED must NOT), absent-dir
    # fail-closed, and current -> 0. Needs a writable tempdir; skipped (not failed) where none exists,
    # since CI always has one and this is supplementary to the classify/audit coverage above.
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def _manifest(mk, status, retrieved):
        return (
            'map-key = "map-{k}"\nname = "Self-test {k}"\npublisher = "AIQT self-test"\n'
            'edition = "2026"\nkind = "control"\nstatus = "{s}"\ncitation-unit = "control"\n'
            'id-pattern = "ST[0-9]{{2}}"\nsource-artefact = "self-test fixture"\nretrieved = "{r}"\n'
            '[[id]]\ncode = "ST01"\ntitle = "one"\n'
        ).format(k=mk, s=status, r=retrieved)

    def _run_quiet(sd, warn_only=False):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run(sd, today, warn_only=warn_only)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-currency-selftest-"))
    except OSError:
        tmp = None
        print("SELF-TEST NOTE: no writable temp directory; run()-level cases SKIPPED "
              "(classify()/audit() coverage above still ran)", file=sys.stderr)
    if tmp is not None:
        try:
            std = tmp / "standards"
            std.mkdir()
            (std / "cur.toml").write_text(_manifest("cur", "stable", days_ago(10)), encoding="utf-8")
            if _run_quiet(std) != 0:
                failures.append("run() with a current manifest expected exit 0")
            (std / "old.toml").write_text(_manifest("old", "beta", days_ago(400)), encoding="utf-8")
            if _run_quiet(std) != 1:
                failures.append("run() with a stale manifest expected exit 1")
            if _run_quiet(std, warn_only=True) != 0:
                failures.append("run() stale under --warn-only expected exit 0")
            (std / "bad.toml").write_text(_manifest("bad", "stable", "not-a-date"), encoding="utf-8")
            if _run_quiet(std) != 2:
                failures.append("run() with a malformed date expected exit 2")
            if _run_quiet(std, warn_only=True) != 2:
                failures.append("run() malformed under --warn-only MUST stay exit 2 (never masked)")
            if _run_quiet(tmp / "does-not-exist") != 2:
                failures.append("run() with an absent standards dir expected fail-closed exit 2")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: per-status thresholds, warn-immediately, unparseable/future/unknown "
          "fail-closed, audit precedence (MALFORMED > FAIL > OK), and url validation (https-with-host "
          "required, malformed fails closed, absent -> None) all hold")
    return 0


def _parse_args(argv):
    std = None
    warn_only = False
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--std" and i + 1 < len(argv):
            std = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--warn-only":
            warn_only = True
            i += 1
        elif argv[i] == "--self-test":
            self_test = True
            i += 1
        else:
            print("usage: check_standards_currency.py [--std DIR] [--warn-only] | --self-test",
                  file=sys.stderr)
            return None
    return (std, warn_only, self_test)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    std, warn_only, self_test = parsed
    if self_test:
        return self_test_main()
    std_dir = std if std is not None else (repo_root() / ".aiqt" / "standards")
    # UTC, not date.today(): the `retrieved` dates and every project record are UTC, and a local-tz
    # "today" could shift a boundary-day age by a day on a differently-zoned runner. Deterministic in UTC.
    today = datetime.now(timezone.utc).date()
    return run(std_dir, today, warn_only=warn_only)


if __name__ == "__main__":
    sys.exit(main())
