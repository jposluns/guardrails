#!/usr/bin/env python3
"""Offline gate for the published security.txt files (RFC 9116) of aiqt.ai and opfiles.ai.

  check_security_txt.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_security_txt.py --self-test  assert every refusal and a pass over tempdir fixtures
  check_security_txt.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

Each site root (site/ for aiqt.ai, opf/site/ for opfiles.ai) must carry .well-known/security.txt as a
regular file whose lines are exactly the owner's 6 fields, in order, each once, with the owner's values
and the Canonical URL of that root's own domain. A missing file, a non-regular file (a directory, a
symlink, a FIFO), a missing, duplicated, unknown or out-of-order field, a wrong value and a wrong
Canonical are findings (exit 1). The Expires value is how the files are kept current: RFC 9116 keeps
Expires at most a year ahead, so an Expires more than MAX_AHEAD (365 days) ahead of the run, one within
REFRESH_WINDOW (30 days) of expiry or already past it, and one that is not a %Y-%m-%dT%H:%M:%SZ UTC
timestamp are findings too; the gate turns red 30 days before the published files expire, which forces a
fresh EXPIRES_VALUE here and in both files before the published ones lapse. A file that cannot be stat'ed or read, one over MAX_BYTES, one that is
not UTF-8 (decoded strictly), or one carrying a line that is not a `Name: value` field, a CR anywhere
(a lone CR included) or a NUL is cannot-evaluate (exit 2), never a pass. The gate reads only the
repository; it never fetches the live files.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_security_txt.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REL = ".well-known/security.txt"
MAX_BYTES = 4096

# The published Expires value and the rule that keeps it current: RFC 9116 section 2.5.5 keeps Expires
# under a year in the future, so judge_expires() refuses a value more than MAX_AHEAD ahead, and it
# refuses one with REFRESH_WINDOW or less remaining, so this constant and both published files are
# refreshed while the published ones are still valid.
EXPIRES_VALUE = "2027-11-01T00:00:00Z"
EXPIRES_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
REFRESH_WINDOW = datetime.timedelta(days=30)
MAX_AHEAD = datetime.timedelta(days=365)

# The fields every site publishes, in this order; Canonical is per root (SITE_ROOTS).
COMMON_FIELDS = (
    ("Contact", "mailto:jeff@posluns.ca"),
    ("Expires", EXPIRES_VALUE),
    ("Encryption", "https://posluns.ca/security/pgp-key.txt"),
    ("Policy", "https://posluns.ca/security/"),
    ("Preferred-Languages", "en, fr"),
)
SITE_ROOTS = (
    ("site", "https://aiqt.ai/.well-known/security.txt"),
    ("opf/site", "https://opfiles.ai/.well-known/security.txt"),
)
FIELD_RE = re.compile(r"([A-Za-z][A-Za-z-]*): (\S(?:.*\S)?)")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, oversized, not UTF-8, or not field-shaped."""


def expected_fields(canonical):
    return COMMON_FIELDS + (("Canonical", canonical),)


def _read_bytes(path):
    """The file's bytes, at most MAX_BYTES + 1 of them (so an oversized file is seen, never read whole)."""
    with open(path, "rb") as handle:
        return handle.read(MAX_BYTES + 1)


def parse(data, where):
    """The (name, value) pairs of a security.txt body, in file order; CannotEvaluate on a malformed body."""
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
    pairs = []
    for number, line in enumerate(text[:-1].split("\n"), 1):
        match = FIELD_RE.fullmatch(line)
        if match is None:
            raise CannotEvaluate("{}:{}: not a `Name: value` field: {!r}".format(where, number, line))
        pairs.append((match.group(1), match.group(2)))
    return pairs


def _now():
    """The current UTC time; the self-test patches this to pin the freshness boundaries."""
    return datetime.datetime.now(datetime.timezone.utc)


def judge_expires(value, where, now=None):
    """The findings for one Expires value: a %Y-%m-%dT%H:%M:%SZ UTC timestamp, more than REFRESH_WINDOW
    from expiry, and at most MAX_AHEAD ahead (RFC 9116 keeps Expires under a year in the future)."""
    try:
        expiry = datetime.datetime.strptime(value, EXPIRES_FORMAT).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return ["{}: Expires {!r} is not a {} UTC timestamp".format(where, value, EXPIRES_FORMAT)]
    now = _now() if now is None else now
    findings = []
    if expiry - now <= REFRESH_WINDOW:
        findings.append("{}: Expires {} is within {} days of expiry, or past it; publish a fresh "
                        "date".format(where, value, REFRESH_WINDOW.days))
    if expiry - now > MAX_AHEAD:
        findings.append("{}: Expires {} is more than {} days ahead; RFC 9116 keeps it at most a year "
                        "out".format(where, value, MAX_AHEAD.days))
    return findings


def judge(pairs, canonical, where):
    """The findings for one parsed file against the expected fields for its root."""
    findings = []
    expected = expected_fields(canonical)
    names = [name for name, _value in pairs]
    for name, value in expected:
        count = names.count(name)
        if count == 0:
            findings.append("{}: missing field {}".format(where, name))
            continue
        if count > 1:
            findings.append("{}: field {} appears {} times".format(where, name, count))
        for got_name, got_value in pairs:
            if got_name == name and got_value != value:
                label = "wrong Canonical" if name == "Canonical" else "wrong value for " + name
                findings.append("{}: {} {!r} (expected {!r})".format(where, label, got_value, value))
    known = {name for name, _value in expected}
    for name in names:
        if name not in known:
            findings.append("{}: unknown field {}".format(where, name))
    for got_name, got_value in pairs:
        if got_name == "Expires":
            findings.extend(judge_expires(got_value, where))
    if not findings and pairs != list(expected):
        findings.append("{}: fields are not in the order {}".format(
            where, ", ".join(name for name, _value in expected)))
    return findings


def scan(root):
    """(findings, errors) over every site root under root; errors make the run cannot-evaluate."""
    findings, errors = [], []
    for site, canonical in SITE_ROOTS:
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
            pairs = parse(data, where)
        except CannotEvaluate as exc:
            errors.append(str(exc))
            continue
        findings.extend(judge(pairs, canonical, where))
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
    print("PASS: security.txt is present and exact on {} site roots".format(len(SITE_ROOTS)))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "security-txt-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _good_body(canonical):
    return "".join("{}: {}\n".format(name, value) for name, value in expected_fields(canonical))


def _fixture(tmp, bodies):
    """A fixture tree under tmp holding the given body (str, bytes or None for absent) per site root."""
    for (site, _canonical), body in zip(SITE_ROOTS, bodies):
        if body is None:
            continue
        directory = Path(tmp) / site / ".well-known"
        os.makedirs(directory, exist_ok=True)
        with open(directory / "security.txt", "wb") as handle:
            handle.write(body.encode("utf-8") if isinstance(body, str) else body)
    return tmp


def _scan_case(bodies):
    with tempfile.TemporaryDirectory(prefix="security-txt-selftest-") as tmp:
        return scan(_fixture(tmp, bodies))


def _self_test():
    aiqt, opf = (canonical for _site, canonical in SITE_ROOTS)
    good = (_good_body(aiqt), _good_body(opf))

    findings, errors = _scan_case(good)
    check("pass/both-roots-exact", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case((None, good[1]))
    check("refuse/missing-file", errors == [] and findings == ["site/{}: missing".format(REL)],
          (findings, errors))

    with tempfile.TemporaryDirectory(prefix="security-txt-selftest-") as tmp:
        _fixture(tmp, (None, good[1]))
        os.makedirs(Path(tmp) / "site" / REL)
        findings, errors = scan(tmp)
    check("refuse/not-regular-file", errors == [] and findings == ["site/{}: not a regular file".format(REL)],
          (findings, errors))

    body = good[0].replace("Policy: https://posluns.ca/security/\n", "")
    findings, errors = _scan_case((body, good[1]))
    check("refuse/missing-field", errors == [] and findings == ["site/{}: missing field Policy".format(REL)],
          (findings, errors))

    body = good[0].replace("Preferred-Languages: en, fr", "Preferred-Languages: en")
    findings, errors = _scan_case((body, good[1]))
    check("refuse/wrong-value", errors == [] and len(findings) == 1
          and "wrong value for Preferred-Languages 'en'" in findings[0], (findings, errors))

    body = good[0].replace("Expires: " + EXPIRES_VALUE,
                           "Expires: " + EXPIRES_VALUE.replace("T00:00:00Z", "T00:00:01Z"))
    findings, errors = _scan_case((body, good[1]))
    check("refuse/wrong-value-one-second-late", errors == [] and len(findings) == 1
          and "wrong value for Expires" in findings[0], (findings, errors))

    body = good[0] + "Contact: mailto:jeff@posluns.ca\n"
    findings, errors = _scan_case((body, good[1]))
    check("refuse/duplicate-field", errors == []
          and findings == ["site/{}: field Contact appears 2 times".format(REL)], (findings, errors))

    findings, errors = _scan_case((good[1], good[0]))
    check("refuse/wrong-canonical", errors == [] and len(findings) == 2
          and all("wrong Canonical" in line for line in findings), (findings, errors))

    body = good[0] + "Hiring: https://posluns.ca/\n"
    findings, errors = _scan_case((body, good[1]))
    check("refuse/unknown-field", errors == [] and findings == ["site/{}: unknown field Hiring".format(REL)],
          (findings, errors))

    lines = good[0].splitlines(keepends=True)
    findings, errors = _scan_case(("".join([lines[1], lines[0]] + lines[2:]), good[1]))
    check("refuse/wrong-order", errors == [] and len(findings) == 1 and "not in the order" in findings[0],
          (findings, errors))

    original = _read_bytes

    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_read_bytes"] = _deny
    try:
        findings, errors = _scan_case(good)
    finally:
        globals()["_read_bytes"] = original
    check("refuse/unreadable-cannot-evaluate", findings == [] and len(errors) == 2
          and all("unreadable" in line for line in errors), (findings, errors))

    for name, body in (("refuse/malformed-not-utf8", good[0].encode("utf-8") + b"\xff\n"),
                       ("refuse/malformed-no-separator", good[0] + "Contact mailto:jeff@posluns.ca\n"),
                       ("refuse/malformed-crlf", good[0].replace("\n", "\r\n")),
                       ("refuse/malformed-no-final-newline", good[0][:-1])):
        findings, errors = _scan_case((body, good[1]))
        check(name, findings == [] and len(errors) == 1, (findings, errors))

    # Each body below is refused by the check its needle names, not by the line-shape check after it.
    for name, body, needle in (
            ("refuse/lone-cr-in-value", good[0].replace("en, fr", "en,\rfr"), "carries a CR or NUL"),
            ("refuse/nul-in-value", good[0].replace("en, fr", "en,\x00fr"), "carries a CR or NUL"),
            ("refuse/not-utf8-strict", good[0].encode("utf-8").replace(b"en, fr", b"en, fr\xff"),
             "not UTF-8")):
        findings, errors = _scan_case((body, good[1]))
        check(name, findings == [] and len(errors) == 1 and needle in errors[0], (findings, errors))

    real_lstat = os.lstat

    def _lstat_denied(path, *args, **kwargs):
        if Path(path).name == "security.txt":
            raise PermissionError(13, "stat refused by the self-test", str(path))
        return real_lstat(path, *args, **kwargs)

    with tempfile.TemporaryDirectory(prefix="security-txt-selftest-") as tmp:
        _fixture(tmp, good)
        os.lstat = _lstat_denied
        try:
            findings, errors = scan(tmp)
        finally:
            os.lstat = real_lstat
    check("refuse/cannot-stat", findings == [] and len(errors) == 2
          and all("cannot stat" in line for line in errors), (findings, errors))

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    # The size bound at its ends: one byte under, exactly at, and one byte over MAX_BYTES.
    pad = MAX_BYTES - len(good[0].encode("utf-8"))
    for name, extra, refused in (("bound/size-under", pad - 1, False), ("bound/size-at", pad, False),
                                 ("bound/size-over", pad + 1, True)):
        # parse() does not judge values, so lengthening the last value changes only the size.
        body = good[0][:-1] + "x" * extra + "\n"
        try:
            parse(body.encode("utf-8"), "fixture")
            got = False
        except CannotEvaluate:
            got = True
        check(name, got == refused, (len(body), got))

    # The freshness rule at each end (judge_expires, the gate's keep-current mechanism): 30 days or less
    # remaining is a finding, more than MAX_AHEAD ahead is a finding, and each boundary is tested one
    # second inside, exactly at, and one second outside it.
    expiry = datetime.datetime.strptime(EXPIRES_VALUE, EXPIRES_FORMAT).replace(tzinfo=datetime.timezone.utc)
    second = datetime.timedelta(seconds=1)
    for name, now, fragments in (
            ("bound/refresh-under", expiry - REFRESH_WINDOW + second, ["within 30 days of expiry"]),
            ("bound/refresh-at", expiry - REFRESH_WINDOW, ["within 30 days of expiry"]),
            ("bound/refresh-over", expiry - REFRESH_WINDOW - second, []),
            ("bound/ahead-under", expiry - MAX_AHEAD + second, []),
            ("bound/ahead-at", expiry - MAX_AHEAD, []),
            ("bound/ahead-over", expiry - MAX_AHEAD - second, ["more than 365 days ahead"])):
        got = judge_expires(EXPIRES_VALUE, "fixture", now)
        check(name, len(got) == len(fragments)
              and all(fragment in line for fragment, line in zip(fragments, got)), (now, got))

    for name, value in (("refuse/expires-not-a-timestamp", "sometime in 2027"),
                        ("refuse/expires-impossible-date", "2027-02-30T00:00:00Z")):
        got = judge_expires(value, "fixture")
        check(name, len(got) == 1 and "is not a" in got[0], got)

    original_now = _now
    globals()["_now"] = lambda: expiry - datetime.timedelta(days=10)
    try:
        findings, errors = _scan_case(good)
    finally:
        globals()["_now"] = original_now
    check("refuse/expires-within-refresh-window", errors == [] and len(findings) == 2
          and all("within 30 days of expiry" in line for line in findings), (findings, errors))

    got = run_gate(ROOT)
    check("live/real-tree-passes", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_security_txt {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_security_txt {} checks (both exact files pass; a missing file, a "
          "directory in its place, a missing field, a wrong value, a duplicate field, a swapped Canonical, "
          "an unknown field and a reordered pair each fail; a file that cannot be stat'ed or read, a "
          "non-UTF-8 body, a line without the field separator, CRLF line ends, a lone CR, a NUL and a "
          "missing final line feed are cannot-evaluate; an unrecognized argument exits 2; the size bound "
          "holds one byte under, at and over; the refresh window and the year bound each hold one second "
          "inside, at and outside, an unparseable Expires fails, and a tree within the refresh window "
          "fails on both roots; the live tree passes)".format(len(EXECUTED)))
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
