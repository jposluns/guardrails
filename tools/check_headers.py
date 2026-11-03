#!/usr/bin/env python3
"""Offline gate for the Cloudflare Pages _headers files of aiqt.ai and opfiles.ai.

  check_headers.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_headers.py --self-test  assert every refusal and a pass over tempdir fixtures
  check_headers.py [--self-test] --execution-report ABS_PATH

Each site root (site/ for aiqt.ai, opf/site/ for opfiles.ai) must carry _headers as a regular file holding
exactly 2 rule blocks, in order. `/*` sets Content-Security-Policy (the site's live policy),
X-Frame-Options: DENY, Permissions-Policy and Cross-Origin-Opener-Policy: same-origin. `/draft/*` removes
the live policy with `! Content-Security-Policy`, then sets Content-Security-Policy to the shared draft
policy and X-Robots-Tag: noindex. Each header appears once per block; the Permissions-Policy and the draft
policy are byte-identical to the constants below; the live policy keeps every directive of the draft policy
with every one of its sources and may only add sources. X-Content-Type-Options, Referrer-Policy and
Strict-Transport-Security are never set here (Cloudflare sends them). A missing file, a non-regular file, a
missing, duplicated, unknown or out-of-order block or header, a forbidden header, a wrong value and a live
policy that drops a directive or a source are findings (exit 1). An unreadable file, one over MAX_BYTES, one
that is not UTF-8, or one carrying a CR, a NUL, no final line feed, or a line that is not blank, a rule
path, an indented `Name: value` header or an indented `! Name` removal is cannot-evaluate (exit 2), never a
pass. The gate reads only the repository; it never fetches the live headers.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_headers.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
import stat
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REL = "_headers"
MAX_BYTES = 8192
SITE_ROOTS = ("site", "opf/site")

# Both strings are copied byte for byte from the shared site template, baseline 1.2.1, section 9
# (Security headers baseline): the shared draft policy and the Permissions-Policy value.
DRAFT_CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline' https://ajax.cloudflare.com "
             "https://static.cloudflareinsights.com; connect-src 'self' https://cloudflareinsights.com; "
             "style-src 'self'; font-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; "
             "frame-ancestors 'none'")
PERMISSIONS_POLICY = ("accelerometer=(), autoplay=(), bluetooth=(), browsing-topics=(), camera=(), "
                      "display-capture=(), fullscreen=(), geolocation=(), gyroscope=(), hid=(), "
                      "magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=()")

CSP = "Content-Security-Policy"
# The live policy has no fixed value (LIVE_CSP marks it); judge_live_csp() holds it to the draft policy.
LIVE_CSP = None
# (kind, name, value) per entry, in file order; kind is "set" or "remove".
EXPECTED_BLOCKS = (
    ("/*", (
        ("set", CSP, LIVE_CSP),
        ("set", "X-Frame-Options", "DENY"),
        ("set", "Permissions-Policy", PERMISSIONS_POLICY),
        ("set", "Cross-Origin-Opener-Policy", "same-origin"),
    )),
    ("/draft/*", (
        ("remove", CSP, None),
        ("set", CSP, DRAFT_CSP),
        ("set", "X-Robots-Tag", "noindex"),
    )),
)
# Cloudflare sends these itself; a copy in _headers would make the header appear twice.
FORBIDDEN = ("X-Content-Type-Options", "Referrer-Policy", "Strict-Transport-Security")

RULE_RE = re.compile(r"/\S*")
SET_RE = re.compile(r"[ \t]+([A-Za-z][A-Za-z0-9-]*): (\S(?:.*\S)?)")
REMOVE_RE = re.compile(r"[ \t]+! ([A-Za-z][A-Za-z0-9-]*)")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, oversized, not UTF-8, or not _headers-shaped."""


def _read_bytes(path):
    """The file's bytes, at most MAX_BYTES + 1 of them (so an oversized file is seen, never read whole)."""
    with open(path, "rb") as handle:
        return handle.read(MAX_BYTES + 1)


def parse(data, where):
    """The (rule path, [(kind, name, value)]) blocks of a _headers body, in file order."""
    if len(data) > MAX_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    if "\r" in text or "\x00" in text:
        raise CannotEvaluate("{}: carries a CR or NUL character".format(where))
    if not text.endswith("\n"):
        raise CannotEvaluate("{}: does not end with a line feed".format(where))
    blocks = []
    for number, line in enumerate(text[:-1].split("\n"), 1):
        if line == "":
            continue
        if RULE_RE.fullmatch(line):
            blocks.append((line, []))
            continue
        set_match, remove_match = SET_RE.fullmatch(line), REMOVE_RE.fullmatch(line)
        if (set_match or remove_match) and not blocks:
            raise CannotEvaluate("{}:{}: a header line before any rule path".format(where, number))
        if set_match:
            blocks[-1][1].append(("set", set_match.group(1), set_match.group(2)))
        elif remove_match:
            blocks[-1][1].append(("remove", remove_match.group(1), None))
        else:
            raise CannotEvaluate("{}:{}: not a rule path, header or removal line: {!r}".format(
                where, number, line))
    return blocks


def csp_directives(value):
    """The (lowercased directive name, [sources]) pairs of a policy, in order; empty segments skipped."""
    pairs = []
    for segment in value.split(";"):
        tokens = segment.split()
        if tokens:
            pairs.append((tokens[0].lower(), tokens[1:]))
    return pairs


def judge_live_csp(value, where):
    """The findings for a live policy: every draft directive kept with every draft source, nothing else."""
    findings = []
    live = csp_directives(value)
    names = [name for name, _sources in live]
    draft = csp_directives(DRAFT_CSP)
    for name, sources in draft:
        count = names.count(name)
        if count == 0:
            findings.append("{}: live policy lacks directive {}".format(where, name))
            continue
        if count > 1:
            findings.append("{}: live policy directive {} appears {} times".format(where, name, count))
        got = live[names.index(name)][1]
        for source in sources:
            if source not in got:
                findings.append("{}: live policy narrows {}: lacks {}".format(where, name, source))
    known = {name for name, _sources in draft}
    for name in names:
        if name not in known:
            findings.append("{}: live policy adds directive {}, which the draft policy lacks".format(where, name))
    return findings


def judge_block(entries, expected, where):
    """The findings for one rule block against its expected (kind, name, value) entries."""
    findings = []
    forbidden = {name.lower() for name in FORBIDDEN}
    for kind in ("set", "remove"):
        want = [name for got_kind, name, _value in expected if got_kind == kind]
        have = [name.lower() for got_kind, name, _value in entries if got_kind == kind]
        label = "header" if kind == "set" else "removal of"
        for name in want:
            count = have.count(name.lower())
            if count == 0:
                findings.append("{}: missing {} {}".format(where, label, name))
            elif count > 1:
                findings.append("{}: {} {} appears {} times".format(where, label, name, count))
        known = {name.lower() for name in want}
        for got_kind, name, _value in entries:
            if got_kind != kind or name.lower() in known:
                continue
            if name.lower() in forbidden:
                findings.append("{}: forbidden {} {} (Cloudflare sends it)".format(where, label, name))
            else:
                findings.append("{}: unknown {} {}".format(where, label, name))
    values = {name.lower(): value for kind, name, value in expected if kind == "set"}
    for kind, name, value in entries:
        if kind != "set" or name.lower() not in values:
            continue
        want = values[name.lower()]
        if want is LIVE_CSP:
            findings.extend(judge_live_csp(value, where))
        elif value != want:
            findings.append("{}: wrong value for {} {!r} (expected {!r})".format(where, name, value, want))
    if not findings and [(kind, name) for kind, name, _value in entries] \
            != [(kind, name) for kind, name, _value in expected]:
        findings.append("{}: entries are not exactly, in order, {}".format(where, ", ".join(
            ("! " + name) if kind == "remove" else name for kind, name, _value in expected)))
    return findings


def judge(blocks, where):
    """The findings for one parsed _headers file against EXPECTED_BLOCKS."""
    findings = []
    paths = [path for path, _entries in blocks]
    expected_paths = [path for path, _entries in EXPECTED_BLOCKS]
    for path, expected in EXPECTED_BLOCKS:
        count = paths.count(path)
        if count == 0:
            findings.append("{}: missing rule block {}".format(where, path))
            continue
        if count > 1:
            findings.append("{}: rule block {} appears {} times".format(where, path, count))
        findings.extend(judge_block(blocks[paths.index(path)][1], expected, "{} [{}]".format(where, path)))
    for path in paths:
        if path not in expected_paths:
            findings.append("{}: unknown rule block {}".format(where, path))
    if not findings and paths != expected_paths:
        findings.append("{}: rule blocks are not in the order {}".format(where, ", ".join(expected_paths)))
    return findings


def scan(root):
    """(findings, errors) over every site root under root; errors make the run cannot-evaluate."""
    findings, errors = [], []
    for site in SITE_ROOTS:
        where = "{}/{}".format(site, REL)
        path = Path(root) / site / REL
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            findings.append("{}: missing".format(where))
            continue
        except OSError as exc:
            errors.append("{}: cannot stat ({})".format(where, exc))
            continue
        if not stat.S_ISREG(mode):
            findings.append("{}: not a regular file".format(where))
            continue
        try:
            data = _read_bytes(path)
        except OSError as exc:
            errors.append("{}: unreadable ({})".format(where, exc))
            continue
        try:
            blocks = parse(data, where)
        except CannotEvaluate as exc:
            errors.append(str(exc))
            continue
        findings.extend(judge(blocks, where))
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
    print("PASS: _headers carries the exact rule blocks on {} site roots".format(len(SITE_ROOTS)))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "headers-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


GOOD_LIVE_CSP = DRAFT_CSP.replace("style-src 'self';", "style-src 'self' 'unsafe-inline';")


def _good_body(live_csp=GOOD_LIVE_CSP):
    return ("/*\n"
            "  Content-Security-Policy: " + live_csp + "\n"
            "  X-Frame-Options: DENY\n"
            "  Permissions-Policy: " + PERMISSIONS_POLICY + "\n"
            "  Cross-Origin-Opener-Policy: same-origin\n"
            "\n"
            "/draft/*\n"
            "  ! Content-Security-Policy\n"
            "  Content-Security-Policy: " + DRAFT_CSP + "\n"
            "  X-Robots-Tag: noindex\n")


def _fixture(tmp, bodies):
    """A fixture tree under tmp holding the given body (str, bytes or None for absent) per site root."""
    for site, body in zip(SITE_ROOTS, bodies):
        if body is None:
            continue
        directory = Path(tmp) / site
        os.makedirs(directory, exist_ok=True)
        with open(directory / REL, "wb") as handle:
            handle.write(body.encode("utf-8") if isinstance(body, str) else body)
    return tmp


def _scan_case(bodies):
    with tempfile.TemporaryDirectory(prefix="headers-selftest-") as tmp:
        return scan(_fixture(tmp, bodies))


def _one_finding(body, fragment):
    """(ok, detail): the aiqt.ai body alone yields exactly 1 finding carrying fragment and no error."""
    findings, errors = _scan_case((body, _good_body()))
    return errors == [] and len(findings) == 1 and fragment in findings[0], (findings, errors)


def _self_test():
    good = _good_body()
    live_line = "  Content-Security-Policy: " + GOOD_LIVE_CSP + "\n"
    draft_line = "  Content-Security-Policy: " + DRAFT_CSP + "\n"

    findings, errors = _scan_case((good, good))
    check("pass/both-roots-exact", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case((_good_body(DRAFT_CSP), good))
    check("pass/live-equal-to-draft", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case((None, good))
    check("refuse/missing-file", errors == [] and findings == ["site/_headers: missing"], (findings, errors))

    with tempfile.TemporaryDirectory(prefix="headers-selftest-") as tmp:
        _fixture(tmp, (None, good))
        os.makedirs(Path(tmp) / "site" / REL)
        findings, errors = scan(tmp)
    check("refuse/not-regular-file", errors == [] and findings == ["site/_headers: not a regular file"],
          (findings, errors))

    draft_block = good[good.index("/draft/*"):]
    for name, body, fragment in (
        ("refuse/missing-rule-block", good[:good.index("\n/draft/*")] + "\n", "missing rule block /draft/*"),
        ("refuse/unknown-rule-block", good + "\n/downloads/*\n  X-Robots-Tag: noindex\n",
         "unknown rule block /downloads/*"),
        ("refuse/duplicate-rule-block", good + "\n" + draft_block, "rule block /draft/* appears 2 times"),
        ("refuse/wrong-block-order", draft_block + "\n" + good[:good.index("\n/draft/*")] + "\n",
         "not in the order /*, /draft/*"),
        ("refuse/missing-header", good.replace("  Cross-Origin-Opener-Policy: same-origin\n", ""),
         "missing header Cross-Origin-Opener-Policy"),
        ("refuse/duplicate-header", good.replace("  X-Frame-Options: DENY\n",
                                                  "  X-Frame-Options: DENY\n  X-Frame-Options: DENY\n"),
         "header X-Frame-Options appears 2 times"),
        ("refuse/duplicate-header-other-case", good.replace(
            "  X-Frame-Options: DENY\n", "  X-Frame-Options: DENY\n  x-frame-options: DENY\n"),
         "header X-Frame-Options appears 2 times"),
        ("refuse/unknown-header", good.replace("  X-Robots-Tag: noindex\n",
                                               "  X-Robots-Tag: noindex\n  Cache-Control: no-store\n"),
         "unknown header Cache-Control"),
        ("refuse/wrong-x-frame-options", good.replace("X-Frame-Options: DENY", "X-Frame-Options: SAMEORIGIN"),
         "wrong value for X-Frame-Options 'SAMEORIGIN'"),
        ("refuse/wrong-coop", good.replace("Opener-Policy: same-origin", "Opener-Policy: unsafe-none"),
         "wrong value for Cross-Origin-Opener-Policy"),
        ("refuse/wrong-x-robots-tag", good.replace("X-Robots-Tag: noindex", "X-Robots-Tag: noindex, nofollow"),
         "wrong value for X-Robots-Tag"),
        ("refuse/permissions-policy-feature-dropped", good.replace("serial=(), usb=()", "serial=()"),
         "wrong value for Permissions-Policy"),
        ("refuse/permissions-policy-one-byte-off", good.replace("usb=()\n", "usb=(),\n"),
         "wrong value for Permissions-Policy"),
        ("refuse/draft-csp-not-byte-identical", good.replace(draft_line, live_line),
         "/draft/*]: wrong value for Content-Security-Policy"),
        ("refuse/draft-missing-removal", good.replace("  ! Content-Security-Policy\n", ""),
         "missing removal of Content-Security-Policy"),
        ("refuse/draft-removal-after-set", good.replace(
            "  ! Content-Security-Policy\n" + draft_line, draft_line + "  ! Content-Security-Policy\n"),
         "entries are not exactly, in order, ! Content-Security-Policy"),
        ("refuse/removal-on-live-block", good.replace("/*\n", "/*\n  ! Content-Security-Policy\n", 1),
         "[/*]: unknown removal of Content-Security-Policy"),
        ("refuse/wrong-header-order", good.replace(
            "  X-Frame-Options: DENY\n  Permissions-Policy: " + PERMISSIONS_POLICY + "\n",
            "  Permissions-Policy: " + PERMISSIONS_POLICY + "\n  X-Frame-Options: DENY\n"),
         "entries are not exactly, in order, Content-Security-Policy, X-Frame-Options"),
        ("refuse/live-csp-missing-directive", good.replace(live_line, live_line.replace(
            "; frame-ancestors 'none'", "")), "live policy lacks directive frame-ancestors"),
        ("refuse/live-csp-narrows-source", good.replace(live_line, live_line.replace(
            " https://ajax.cloudflare.com", "")), "live policy narrows script-src: lacks https://ajax.cloudflare.com"),
        ("refuse/live-csp-adds-directive", good.replace(live_line, live_line.replace(
            "\n", "; worker-src 'self'\n")), "live policy adds directive worker-src"),
        ("refuse/live-csp-duplicate-directive", good.replace(live_line, live_line.replace(
            "\n", "; img-src 'self' data:\n")), "live policy directive img-src appears 2 times"),
    ):
        ok, detail = _one_finding(body, fragment)
        check(name, ok, detail)

    live_anchor, draft_anchor = "  Cross-Origin-Opener-Policy: same-origin\n", "  X-Robots-Tag: noindex\n"
    nosniff = "X-Content-Type-Options: nosniff"
    referrer = "Referrer-Policy: strict-origin-when-cross-origin"
    hsts = "Strict-Transport-Security: max-age=31536000; includeSubDomains; preload"
    for name, anchor, line in (("refuse/forbidden-x-content-type-options-live", live_anchor, nosniff),
                               ("refuse/forbidden-x-content-type-options-draft", draft_anchor, nosniff),
                               ("refuse/forbidden-referrer-policy-live", live_anchor, referrer),
                               ("refuse/forbidden-referrer-policy-draft", draft_anchor, referrer),
                               ("refuse/forbidden-strict-transport-security-live", live_anchor, hsts),
                               ("refuse/forbidden-strict-transport-security-draft", draft_anchor, hsts)):
        body = good.replace(anchor, anchor + "  " + line + "\n")
        ok, detail = _one_finding(body, "forbidden header " + line.split(":")[0])
        check(name, ok, detail)

    original = _read_bytes

    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_read_bytes"] = _deny
    try:
        findings, errors = _scan_case((good, good))
    finally:
        globals()["_read_bytes"] = original
    check("refuse/unreadable-cannot-evaluate", findings == [] and len(errors) == 2
          and all("unreadable" in line for line in errors), (findings, errors))

    for name, body in (("refuse/malformed-not-utf8", good.encode("utf-8") + b"\xff\n"),
                       ("refuse/malformed-crlf", good.replace("\n", "\r\n")),
                       ("refuse/malformed-nul", good.replace("DENY", "DE\x00NY")),
                       ("refuse/malformed-no-final-newline", good[:-1]),
                       ("refuse/malformed-header-before-rule", "  X-Frame-Options: DENY\n" + good),
                       ("refuse/malformed-unindented-header", good.replace("  X-Frame-Options", "X-Frame-Options")),
                       ("refuse/malformed-no-separator", good.replace("X-Frame-Options: DENY", "X-Frame-Options DENY")),
                       ("refuse/malformed-comment-line", "# headers\n" + good)):
        findings, errors = _scan_case((body, good))
        check(name, findings == [] and len(errors) == 1, (findings, errors))

    # The size bound at its ends: one byte under, exactly at, and one byte over MAX_BYTES.
    pad = MAX_BYTES - len(good.encode("utf-8"))
    for name, extra, refused in (("bound/size-under", pad - 1, False), ("bound/size-at", pad, False),
                                 ("bound/size-over", pad + 1, True)):
        # parse() does not judge values, so lengthening the last value changes only the size.
        body = good[:-1] + "x" * extra + "\n"
        try:
            parse(body.encode("utf-8"), "fixture")
            got = False
        except CannotEvaluate:
            got = True
        check(name, got == refused, (len(body), got))

    got = run_gate(ROOT)
    check("live/real-tree-passes", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_headers {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_headers {} checks (both exact files pass, and so does a live policy equal "
          "to the draft; a missing file, a directory in its place, a missing, unknown, duplicated or "
          "reordered rule block, a missing, duplicated, unknown or reordered header, each forbidden header in "
          "either block, a wrong X-Frame-Options, Cross-Origin-Opener-Policy or X-Robots-Tag, a "
          "Permissions-Policy or draft policy that is not byte-identical, a missing or misplaced removal, and "
          "a live policy that drops a directive or a source, adds a directive or repeats one each fail; an "
          "unreadable file, a non-UTF-8 body, CRLF line ends, a NUL, a missing final line feed and a line "
          "outside the _headers grammar are cannot-evaluate; the size bound holds one byte under, at and "
          "over; the live tree passes)".format(len(EXECUTED)))
    return 0


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
