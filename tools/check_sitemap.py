#!/usr/bin/env python3
"""Offline gate for the sitemap.xml and robots.txt Sitemap line of aiqt.ai and opfiles.ai.

  check_sitemap.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_sitemap.py --self-test  assert every refusal and a pass over tempdir fixtures
  check_sitemap.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

For each site root (site/ for aiqt.ai, opf/site/ for opfiles.ai) the indexable pages are every *.html
file under the root except a 404.html and anything under the root's draft/ or downloads/ directory;
a page maps to its served URL (index.html to the directory, x.html to /x). The root's sitemap.xml must
list each indexable page exactly once and nothing else, every <url> with a <loc> on the root's own
https origin and a <lastmod> that is a valid YYYY-MM-DD date, and no draft URL (the path /draft or a
path under /draft/; a path such as /drafty or /drafts/old is not a draft URL and is refused as having
no page); the root's robots.txt must carry `Sitemap: https://<host>/sitemap.xml` exactly once. A
missing sitemap or robots file, a sitemap or robots path that is not a regular file, and each of
those defects are findings (exit 1). An input that cannot be stat'ed or read, an unwalkable
directory, a root with no indexable page, an input over MAX_BYTES, a non-UTF-8 input (decoded
strictly), a DOCTYPE, XML that does not parse into a sitemap urlset, a urlset child other than <url>,
and a <url> without exactly one <loc>, exactly one <lastmod> and no other child are cannot-evaluate
(exit 2), never a pass. The gate reads only the repository; it fetches nothing.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_sitemap.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import os
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 262144
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
SITE_ROOTS = (
    ("site", "aiqt.ai"),
    ("opf/site", "opfiles.ai"),
)
# Top-level directories of a site root whose pages are never indexed: the staged draft and the downloads.
UNINDEXED_DIRS = ("draft", "downloads")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, oversized, not UTF-8, or not a sitemap urlset."""


def _read_bytes(path):
    """The file's bytes, at most MAX_BYTES + 1 of them (so an oversized file is seen, never read whole)."""
    with open(path, "rb") as handle:
        return handle.read(MAX_BYTES + 1)


def _walk_error(exc):
    raise exc


def page_url_path(rel):
    """The served URL path of a page at rel (a POSIX path relative to its site root)."""
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel[:-len("index.html")]
    return "/" + rel[:-len(".html")]


def indexable_pages(site_dir):
    """The sorted URL paths of every indexable page under site_dir; OSError on an unwalkable tree."""
    paths = []
    for current, dirs, files in os.walk(site_dir, onerror=_walk_error):
        rel_dir = os.path.relpath(current, site_dir)
        if rel_dir == ".":
            dirs[:] = [d for d in dirs if d not in UNINDEXED_DIRS]
        dirs.sort()
        for name in sorted(files):
            if not name.endswith(".html") or name == "404.html":
                continue
            rel = name if rel_dir == "." else "{}/{}".format(rel_dir.replace(os.sep, "/"), name)
            paths.append(page_url_path(rel))
    return sorted(paths)


def _read_input(path, where, findings):
    """The decoded text of a present regular file, None (with a finding) when absent or non-regular."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        findings.append("{}: missing".format(where))
        return None
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(where, exc)) from None
    if not stat.S_ISREG(mode):
        findings.append("{}: not a regular file".format(where))
        return None
    try:
        data = _read_bytes(path)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(where, exc)) from None
    if len(data) > MAX_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_BYTES))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None


def parse_sitemap(text, where):
    """The (loc, lastmod) pairs of a sitemap urlset, in file order; CannotEvaluate when it is not one."""
    if "<!DOCTYPE" in text.upper():
        raise CannotEvaluate("{}: carries a DOCTYPE".format(where))
    try:
        top = ET.fromstring(text)
    except ET.ParseError as exc:
        raise CannotEvaluate("{}: not well-formed XML ({})".format(where, exc)) from None
    if top.tag != NS + "urlset":
        raise CannotEvaluate("{}: root element is {!r}, not a sitemap urlset".format(where, top.tag))
    pairs = []
    for child in top:
        if child.tag != NS + "url":
            raise CannotEvaluate("{}: unexpected element {!r} in the urlset".format(where, child.tag))
        locs, mods = child.findall(NS + "loc"), child.findall(NS + "lastmod")
        if len(locs) != 1 or len(mods) != 1 or len(child) != 2:
            raise CannotEvaluate("{}: a <url> needs exactly one <loc> and one <lastmod>".format(where))
        pairs.append(((locs[0].text or "").strip(), (mods[0].text or "").strip()))
    return pairs


def valid_date(value):
    if DATE_RE.fullmatch(value) is None:
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def judge_sitemap(pairs, pages, host, where):
    findings = []
    origin = "https://{}".format(host)
    listed = []
    for loc, lastmod in pairs:
        if not loc.startswith(origin + "/"):
            findings.append("{}: {} is not on {}".format(where, loc, origin))
            continue
        path = loc[len(origin):]
        # Only /draft itself and paths under /draft/ are draft URLs; /drafty or /drafts/old is an
        # ordinary URL and still needs a page.
        is_draft = path == "/draft" or path.startswith("/draft/")
        if is_draft:
            findings.append("{}: lists a draft URL {}".format(where, loc))
        if path in listed:
            findings.append("{}: lists {} more than once".format(where, loc))
        listed.append(path)
        if path not in pages and not is_draft:
            findings.append("{}: {} has no page".format(where, loc))
        if not valid_date(lastmod):
            findings.append("{}: lastmod {!r} of {} is not a valid date".format(where, lastmod, loc))
    for path in pages:
        if path not in listed:
            findings.append("{}: indexable page {}{} is missing".format(where, origin, path))
    return findings


def judge_robots(text, host, where):
    line = "Sitemap: https://{}/sitemap.xml".format(host)
    count = text.split("\n").count(line)
    if count == 1:
        return []
    if count == 0:
        return ["{}: lacks the line {!r}".format(where, line)]
    return ["{}: carries {!r} {} times".format(where, line, count)]


def scan(root):
    """(findings, errors) over every site root under root; errors make the run cannot-evaluate."""
    findings, errors = [], []
    for site, host in SITE_ROOTS:
        site_dir = Path(root) / site
        try:
            pages = indexable_pages(site_dir)
        except OSError as exc:
            errors.append("{}: cannot walk ({})".format(site, exc))
            continue
        if not pages:
            errors.append("{}: no indexable page found".format(site))
            continue
        try:
            text = _read_input(site_dir / "sitemap.xml", site + "/sitemap.xml", findings)
            if text is not None:
                pairs = parse_sitemap(text, site + "/sitemap.xml")
                findings.extend(judge_sitemap(pairs, pages, host, site + "/sitemap.xml"))
        except CannotEvaluate as exc:
            errors.append(str(exc))
        try:
            text = _read_input(site_dir / "robots.txt", site + "/robots.txt", findings)
            if text is not None:
                findings.extend(judge_robots(text, host, site + "/robots.txt"))
        except CannotEvaluate as exc:
            errors.append(str(exc))
    return findings, errors


def run_gate(root):
    findings, errors = scan(root)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: sitemap.xml and the robots.txt Sitemap line match the indexable pages on {} site roots".format(
        len(SITE_ROOTS)))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "sitemap-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _sitemap(host, entries):
    rows = "".join("  <url>\n    <loc>https://{}{}</loc>\n    <lastmod>{}</lastmod>\n  </url>\n".format(
        host, path, lastmod) for path, lastmod in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n")


def _robots(host):
    return "User-agent: *\nDisallow:\nSitemap: https://{}/sitemap.xml\n".format(host)


# Fixture pages per root: the indexable ones, plus a 404 page, a draft page and a download page.
_PAGES = ("index.html", "about.html", "guide/index.html", "404.html", "draft/index.html",
          "downloads/note.html")
_ENTRIES = (("/", "2026-11-02"), ("/about", "2026-10-01"), ("/guide/", "2026-02-28"))


def _fixture(tmp, overrides=None):
    """A fixture tree under tmp; overrides maps a relative path to a body (str or bytes) or None (absent)."""
    files = {}
    for site, host in SITE_ROOTS:
        for page in _PAGES:
            files["{}/{}".format(site, page)] = "<!doctype html><title>x</title>\n"
        files[site + "/sitemap.xml"] = _sitemap(host, _ENTRIES)
        files[site + "/robots.txt"] = _robots(host)
    files.update(overrides or {})
    for rel, body in files.items():
        if body is None:
            continue
        path = Path(tmp) / rel
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(body.encode("utf-8") if isinstance(body, str) else body)
    return tmp


def _scan_case(overrides=None):
    with tempfile.TemporaryDirectory(prefix="sitemap-selftest-") as tmp:
        return scan(_fixture(tmp, overrides))


def _one_finding(result, needle):
    findings, errors = result
    return errors == [] and len(findings) == 1 and needle in findings[0]


def _self_test():
    result = _scan_case()
    check("pass/both-roots-exact", result == ([], []), result)

    check("pages/url-path-mapping", page_url_path("guide/index.html") == "/guide/"
          and page_url_path("about.html") == "/about" and page_url_path("index.html") == "/", None)

    # The fixture root also holds a 404.html, a draft/ page and a downloads/ page; none is indexable.
    with tempfile.TemporaryDirectory(prefix="sitemap-selftest-") as tmp:
        got = indexable_pages(Path(_fixture(tmp)) / "site")
    check("pages/excludes-404-draft-downloads", got == ["/", "/about", "/guide/"], got)

    with tempfile.TemporaryDirectory(prefix="sitemap-selftest-") as tmp:
        _fixture(tmp, {"site/sitemap.xml": None})
        os.makedirs(Path(tmp) / "site" / "sitemap.xml")
        result = scan(tmp)
    check("refuse/sitemap-not-regular-file", _one_finding(result, "site/sitemap.xml: not a regular file"),
          result)

    real_lstat = os.lstat

    def _lstat_denied(path, *args, **kwargs):
        if Path(path).name == "sitemap.xml":
            raise PermissionError(13, "stat refused by the self-test", str(path))
        return real_lstat(path, *args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="sitemap-selftest-") as tmp:
        _fixture(tmp)
        os.lstat = _lstat_denied
        try:
            findings, errors = scan(tmp)
        finally:
            os.lstat = real_lstat
    check("refuse/cannot-stat", findings == [] and len(errors) == 2
          and all("cannot stat" in line for line in errors), (findings, errors))

    # os.walk lists each directory through os.scandir; a refused listing of guide/ must stop the scan.
    real_scandir = os.scandir

    def _scandir_denied(path=".", *args, **kwargs):
        if os.path.basename(os.fspath(path)) == "guide":
            raise PermissionError(13, "listing refused by the self-test", os.fspath(path))
        return real_scandir(path, *args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="sitemap-selftest-") as tmp:
        _fixture(tmp)
        os.scandir = _scandir_denied
        try:
            findings, errors = scan(tmp)
        finally:
            os.scandir = real_scandir
    check("refuse/unwalkable-directory", findings == [] and len(errors) == 2
          and all("cannot walk" in line for line in errors), (findings, errors))

    result = _scan_case({"site/index.html": None, "site/about.html": None, "site/guide/index.html": None})
    check("refuse/no-indexable-page", result == ([], ["site: no indexable page found"]), result)

    result = _scan_case({"site/sitemap.xml": _sitemap("aiqt.ai", _ENTRIES[:2])})
    check("refuse/page-missing-from-sitemap", _one_finding(result, "indexable page https://aiqt.ai/guide/"),
          result)

    for name, path in (("refuse/url-without-page", "/gone"), ("refuse/drafty-without-page", "/drafty"),
                       ("refuse/drafts-old-without-page", "/drafts/old")):
        result = _scan_case({"site/sitemap.xml": _sitemap("aiqt.ai", _ENTRIES + ((path, "2026-01-01"),))})
        check(name, _one_finding(result, "https://aiqt.ai{} has no page".format(path)), result)

    result = _scan_case({"site/sitemap.xml": _sitemap("aiqt.ai", _ENTRIES + (("/about", "2026-01-01"),))})
    check("refuse/duplicate-url", _one_finding(result, "more than once"), result)

    result = _scan_case({"opf/site/sitemap.xml": _sitemap("aiqt.ai", _ENTRIES)})
    check("refuse/wrong-origin", result[1] == [] and len(result[0]) == 6
          and all("not on https://opfiles.ai" in line or "is missing" in line for line in result[0]), result)

    # The date check at its ends: the last day of February 2026 passes; the next two do not exist.
    for name, value, ok in (("lastmod/2026-02-28", "2026-02-28", True),
                            ("lastmod/2026-02-29", "2026-02-29", False),
                            ("lastmod/2026-02-30", "2026-02-30", False),
                            ("lastmod/unpadded", "2026-2-28", False),
                            ("lastmod/timestamp", "2026-02-28T00:00:00Z", False),
                            ("lastmod/empty", "", False)):
        entries = _ENTRIES[:2] + (("/guide/", value),)
        result = _scan_case({"site/sitemap.xml": _sitemap("aiqt.ai", entries)})
        got = result == ([], []) if ok else _one_finding(result, "is not a valid date")
        check(name, got, result)

    for name, path in (("refuse/draft-bare", "/draft"), ("refuse/draft-root", "/draft/"),
                       ("refuse/draft-page", "/draft/index.html")):
        result = _scan_case({"site/sitemap.xml": _sitemap("aiqt.ai", _ENTRIES + ((path, "2026-01-01"),))})
        check(name, _one_finding(result, "lists a draft URL"), result)

    result = _scan_case({"site/robots.txt": "User-agent: *\nDisallow:\n"})
    check("refuse/robots-lacks-sitemap", _one_finding(result, "site/robots.txt: lacks the line"), result)

    result = _scan_case({"opf/site/robots.txt": _robots("aiqt.ai")})
    check("refuse/robots-wrong-host", _one_finding(result, "opf/site/robots.txt: lacks the line"), result)

    result = _scan_case({"site/robots.txt": _robots("aiqt.ai") + "Sitemap: https://aiqt.ai/sitemap.xml\n"})
    check("refuse/robots-duplicate-sitemap", _one_finding(result, "2 times"), result)

    result = _scan_case({"site/sitemap.xml": None})
    check("refuse/sitemap-missing", _one_finding(result, "site/sitemap.xml: missing"), result)

    result = _scan_case({"opf/site/robots.txt": None})
    check("refuse/robots-missing", _one_finding(result, "opf/site/robots.txt: missing"), result)

    original = _read_bytes

    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_read_bytes"] = _deny
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_read_bytes"] = original
    check("refuse/unreadable-cannot-evaluate", findings == [] and len(errors) == 4
          and all("unreadable" in line for line in errors), (findings, errors))

    for name, body in (("refuse/malformed-not-xml", "<urlset"),
                       ("refuse/malformed-not-urlset", "<foo/>\n"),
                       ("refuse/malformed-doctype",
                        '<!DOCTYPE urlset [<!ENTITY a "b">]>\n' + _sitemap("aiqt.ai", _ENTRIES)),
                       ("refuse/malformed-not-utf8", b"\xff\xfe<urlset/>")):
        findings, errors = _scan_case({"site/sitemap.xml": body})
        check(name, findings == [] and len(errors) == 1, (findings, errors))

    # Each body below is otherwise a valid sitemap (or one a looser parse would accept), so it is refused
    # only by the check its needle names.
    def guide_row(row):
        return _sitemap("aiqt.ai", _ENTRIES[:2])[:-len("</urlset>\n")] + row + "</urlset>\n"

    loc, lastmod = "<loc>https://aiqt.ai/guide/</loc>", "<lastmod>2026-02-28</lastmod>"
    good = _sitemap("aiqt.ai", _ENTRIES)
    for name, body, needle in (
            ("refuse/doctype-after-declaration", good.replace("?>\n", "?>\n<!DOCTYPE urlset>\n", 1),
             "carries a DOCTYPE"),
            ("refuse/not-utf8-strict", good.encode("utf-8").replace(b"<urlset", b"<!-- \xff -->\n<urlset", 1),
             "not UTF-8"),
            ("refuse/unexpected-element", guide_row("  <link>" + loc + lastmod + "</link>\n"),
             "unexpected element"),
            ("refuse/url-extra-child",
             guide_row("  <url>" + loc + lastmod + "<changefreq>daily</changefreq></url>\n"),
             "exactly one <loc> and one <lastmod>"),
            ("refuse/url-two-locs", guide_row("  <url>" + loc + loc + lastmod + "</url>\n"),
             "exactly one <loc> and one <lastmod>"),
            ("refuse/url-no-lastmod", guide_row("  <url>" + loc + "</url>\n"),
             "exactly one <loc> and one <lastmod>")):
        findings, errors = _scan_case({"site/sitemap.xml": body})
        check(name, findings == [] and len(errors) == 1 and needle in errors[0], (findings, errors))

    # The size bound at its ends: one byte under, exactly at, and one byte over MAX_BYTES.
    base = _sitemap("aiqt.ai", _ENTRIES)
    pad = MAX_BYTES - len(base.encode("utf-8"))
    for name, extra, refused in (("bound/size-under", pad - 1, False), ("bound/size-at", pad, False),
                                 ("bound/size-over", pad + 1, True)):
        findings, errors = _scan_case({"site/sitemap.xml": base + " " * extra})
        got = (len(errors) == 1 and "larger than" in errors[0]) if refused else (findings, errors) == ([], [])
        check(name, got, (findings, errors))

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    got = run_gate(ROOT)
    check("live/real-tree-passes", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_sitemap {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_sitemap {} checks (exact sitemaps and robots lines pass, with 404, draft/ "
          "and downloads/ pages not indexed; a page missing from the sitemap, a URL with no page (/drafty "
          "and /drafts/old included), a duplicate URL, a foreign origin, an invalid or non-date lastmod, a "
          "/draft or /draft/ URL, a robots file lacking, mis-hosting or repeating its Sitemap line, a "
          "missing sitemap or robots file and a non-regular sitemap each fail; an input that cannot be "
          "stat'ed or read, an unwalkable directory, a root with no indexable page, XML that does not "
          "parse, a non-urlset root, a DOCTYPE before or after the declaration, a non-UTF-8 body, an "
          "unexpected urlset child and a <url> without exactly one <loc> and one <lastmod> are "
          "cannot-evaluate; an unrecognized argument exits 2; the size bound holds one byte under, at and "
          "over; the live tree passes)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(ROOT)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
