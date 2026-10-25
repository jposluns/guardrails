#!/usr/bin/env python3
"""SEPARATE, non-blocking reachability audit for the standards manifest `url` fields.

This is DELIBERATELY NOT part of the offline Quality gate (`run_all_checks.sh`) and is not wired into
CI: it makes live network requests, so its result depends on the network and on remote sites rather than
on the diff under review, exactly the property that would make CI flaky and non-hermetic. The offline
gate validates url SHAPE (https:// with a host) via _standards.load_manifests; this tool separately
audits live REACHABILITY. Run it manually or on a schedule, never as a pull-request gate.

For every manifest under .aiqt/standards/ that carries a `url`, it issues a stdlib urllib request (HEAD,
falling back to GET when a server rejects HEAD) with a timeout, and reports any url that does not answer
with a 2xx/3xx status or that errors.

  check_standards_urls_live.py [--std DIR] [--timeout SECONDS]
  check_standards_urls_live.py --self-test   offline self-test of the explicit --std probe (no network)

Exit convention:
  0  every manifest url is reachable (2xx/3xx)
  1  at least one url is unreachable (non-2xx/3xx or a network error)
  2  a malformed manifest, or a read error on .aiqt/standards/ (fail-closed, same as load_manifests)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_standards_urls_live.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _standards import load_manifests, ManifestError, std_root, _std_root_vectors  # noqa: E402

try:
    from gen_rules import repo_root  # noqa: E402  reuse the repo's own root finder
except Exception:  # pragma: no cover - gen_rules is a sibling; this only guards an odd import env
    def repo_root():
        return Path(__file__).resolve().parent.parent

DEFAULT_TIMEOUT = 10  # seconds
USER_AGENT = "aiqt-standards-url-audit/1.0"


def _reachable(url, timeout):
    """Return (ok, detail) for one url. ok is True on a 2xx/3xx status. Tries HEAD first, falls back to
    GET when a server answers HEAD with 405/501 (method not allowed / not implemented). urllib follows
    redirects, so a healthy site normally resolves to a final 2xx; a 3xx that is not followed still
    counts as reachable."""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
                if 200 <= code < 400:
                    return (True, "{} {}".format(method, code))
                return (False, "{} status {}".format(method, code))
        except urllib.error.HTTPError as exc:
            if method == "HEAD" and exc.code in (403, 405, 501):
                # Some servers refuse HEAD (or forbid it for a bot UA); retry once with GET.
                continue
            if 200 <= exc.code < 400:
                return (True, "{} {}".format(method, exc.code))
            return (False, "{} HTTP {}".format(method, exc.code))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return (False, "{} error: {}".format(method, exc))
    return (False, "HEAD refused and GET did not resolve")


def run(std_dir, timeout, *, root):
    # std_dir is probed beneath root (the repository root for the default dir; a --std directory is its
    # own root, which main first probes from the filesystem root down with _standards.std_root), so a
    # symlinked or dangling .aiqt or standards, or a --std that is a symlink or has one at any
    # component, fails closed (exit 2).
    try:
        manifests = load_manifests(std_dir, root=root)
    except (ManifestError, OSError) as exc:
        print("MALFORMED: {}".format(exc), file=sys.stderr)
        return 2
    if not manifests:
        print("MALFORMED: no standards manifests found under {} (source-of-truth missing)".format(
            std_dir), file=sys.stderr)
        return 2

    with_url = [manifests[k] for k in sorted(manifests) if manifests[k].url]
    if not with_url:
        print("PASS: no manifest carries a url; nothing to audit")
        return 0

    failures = []
    for m in with_url:
        ok, detail = _reachable(m.url, timeout)
        status = "OK" if ok else "FAIL"
        print("{}: {} -> {} ({})".format(status, m.path.name, m.url, detail))
        if not ok:
            failures.append(m.path.name)

    if failures:
        print("FAIL: {} of {} manifest url(s) unreachable: {}".format(
            len(failures), len(with_url), ", ".join(failures)))
        return 1
    print("PASS: all {} manifest url(s) reachable".format(len(with_url)))
    return 0


def self_test_main():
    """Offline self-test of the explicit --std probe, launched through main: a real directory exits 0,
    and a --std that is a symlink, live or dangling, or sits beneath a symlinked ancestor exits 2 naming
    it. The fixture manifest carries no url, so no vector reaches the network."""
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout
    from datetime import date

    def _launch(argv):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(argv)
        return code, err.getvalue()

    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-urls-live-selftest-"))
    except OSError as exc:
        tmp = None
        failures.append("NOT RUN: the explicit --std vectors: no writable temp directory ({}); coverage "
                        "that did not run FAILS the self-test, never a silent PASS".format(exc))
    if tmp is not None:
        try:
            _std_root_vectors(_launch, tmp, date(2026, 8, 17), failures)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: an explicit --std that is a real directory exits 0, and one that is a live or "
          "dangling symlink or sits beneath a symlinked ancestor exits 2 naming it (offline, no network)")
    return 0


def _parse_args(argv):
    std = None
    timeout = DEFAULT_TIMEOUT
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--std" and i + 1 < len(argv):
            std = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--self-test":
            self_test = True
            i += 1
        elif argv[i] == "--timeout" and i + 1 < len(argv):
            try:
                timeout = float(argv[i + 1])
            except ValueError:
                print("error: --timeout needs a number of seconds", file=sys.stderr)
                return None
            i += 2
        else:
            print("usage: check_standards_urls_live.py [--std DIR] [--timeout SECONDS] | --self-test",
                  file=sys.stderr)
            return None
    return (std, timeout, self_test)


def main(argv=None):
    parsed = _parse_args(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        return 2
    std, timeout, self_test = parsed
    if self_test:
        return self_test_main()
    if std is None:
        root = repo_root()
        std_dir = root / ".aiqt" / "standards"
    else:
        try:
            root, std_dir = std_root(std)
        except OSError as exc:
            print("MALFORMED: --std {}: {}".format(std, exc), file=sys.stderr)
            return 2
    return run(std_dir, timeout, root=root)


if __name__ == "__main__":
    sys.exit(main())
