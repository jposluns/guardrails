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

Exit convention:
  0  every manifest url is reachable (2xx/3xx)
  1  at least one url is unreachable (non-2xx/3xx or a network error)
  2  a malformed manifest, or a read error on .aiqt/standards/ (fail-closed, same as load_manifests)
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _standards import load_manifests, ManifestError  # noqa: E402

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


def run(std_dir, timeout):
    try:
        manifests = load_manifests(std_dir)
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


def _parse_args(argv):
    std = None
    timeout = DEFAULT_TIMEOUT
    i = 0
    while i < len(argv):
        if argv[i] == "--std" and i + 1 < len(argv):
            std = Path(argv[i + 1])
            i += 2
        elif argv[i] == "--timeout" and i + 1 < len(argv):
            try:
                timeout = float(argv[i + 1])
            except ValueError:
                print("error: --timeout needs a number of seconds", file=sys.stderr)
                return None
            i += 2
        else:
            print("usage: check_standards_urls_live.py [--std DIR] [--timeout SECONDS]",
                  file=sys.stderr)
            return None
    return (std, timeout)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    std, timeout = parsed
    std_dir = std if std is not None else (repo_root() / ".aiqt" / "standards")
    return run(std_dir, timeout)


if __name__ == "__main__":
    sys.exit(main())
