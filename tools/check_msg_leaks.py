#!/usr/bin/env python3
"""Leak-denylist gate for the COMMIT-MESSAGE and PR-METADATA channels (F-144).

check_leaks.py scans FILE CONTENT; this gate covers the channels files never touch: the commit messages in
a PR's base..head range, the PR title and body (scanned as one composed blob so a denied term split across
the title/body boundary is still caught), and the PR head-ref name. It reuses check_leaks' shared scanner
(scan_text) and hashed denylist (load_denylist), single-sourced from tools/leak-hashes.txt, so the
STRUCTURAL patterns and the codename hashes live in exactly one place. It does NOT honor the `leak-allow`
marker: a marker cannot be trusted in text bound for a permanent public record.

MODES (auto-detected from GITHUB_EVENT_NAME/$GITHUB_EVENT_PATH; overridable for testing):
  - pull_request: scan base..head commit messages + composed PR title/body + head ref. The required PR
    check: detection plus (once required) a merge block. It runs AFTER push, so it is detection, not
    prevention, and cannot un-expose already-pushed text.
  - push (to main): scan the messages of the newly landed commits (before..after). The post-merge tripwire,
    the authoritative check on the text that actually landed (a title/body edit after the PR check, or a
    merge-dialog edit of the squash message, is caught only here). Fires after publication -> drives removal.
  - --self-test: run embedded fail-cases in memory (CI invokes this alongside the real run).

HONEST SCOPE. Like check_leaks, this catches ACCIDENTAL reintroduction of internal terms/host specifics; it
is not an exfiltration control, and deliberate obfuscation (mid-word splits, zero-width, homoglyphs) can
evade the hash layer. It never echoes the matching text, token, codename, or a malformed field value (it
reports the channel and, for a commit, the short SHA).

FAIL-CLOSED. A missing/unreadable/malformed/empty denylist, missing/unreadable/malformed event JSON, a PR
event missing or mistyping a required field, a revision field that is not a canonical 40-hex SHA, a
branch-creation push whose complete range cannot be established, an unresolvable commit range, or an event
kind whose channel set cannot be established -> exit 2. A finding -> exit 1. Clean -> exit 0. Missing input
is never read as clean. A confidentiality gate that cannot load its codename denylist does not run:
absent/empty is exit 2, stricter than the file gate, because here there is no compensating repo-wide file
scan behind it.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_leaks import load_denylist, scan_text  # noqa: E402  shared denylist + scanner (single source)

ROOT = Path(__file__).resolve().parents[1]
_ZERO_SHA = "0" * 40
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")   # a canonical git object id (the zero-SHA also matches)


class FailClosed(Exception):
    """A condition under which the gate must exit 2 rather than risk a false clean."""


def _run_git(args):
    """Run git with an argv array (never a shell); fail closed on any error, without echoing git's stderr or
    the revision values (which may reflect a leaked string)."""
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, encoding="utf-8")
    except (OSError, ValueError) as exc:                       # git missing, or undecodable output
        raise FailClosed("git invocation failed ({})".format(type(exc).__name__))
    if proc.returncode != 0:
        raise FailClosed("git {} exited non-zero ({})".format(args[0] if args else "?", proc.returncode))
    return proc.stdout


def commit_messages(base, head):
    """Return [(short_sha, message)] for base..head, parsed with unambiguous separators (not a shell).
    base and head are canonical SHAs validated by the caller before they reach git."""
    out = _run_git(["log", "--no-color", "-z", "--format=%h%x1f%B", "{}..{}".format(base, head)])
    parsed = []
    for rec in (r for r in out.split("\0") if r):
        sha, _, body = rec.partition("\x1f")
        parsed.append((sha, body))
    return parsed


def require_denylist():
    """Load the hashed denylist, failing closed on absent/empty/unreadable/malformed (a codename gate must
    not run without its denylist). Returns (hashes, maxn)."""
    try:
        hashes, maxn, bad = load_denylist(ROOT)
    except (OSError, UnicodeError) as exc:                      # unreadable / non-UTF-8 denylist
        raise FailClosed("cannot read the leak denylist: {}".format(type(exc).__name__))
    if bad:
        raise FailClosed("malformed leak denylist: {}".format("; ".join(bad)))
    if not hashes:
        raise FailClosed("empty or absent leak denylist (tools/leak-hashes.txt): a codename gate does not "
                         "run without its denylist")
    return hashes, maxn


def scan_channels(channels, hashes, maxn):
    """channels = [(label, text)]. Returns finding strings, never echoing the matched text."""
    findings = []
    for label, text in channels:
        for _number, kind in scan_text(text, hashes, maxn, honor_leak_allow=False):
            findings.append("{}: {}".format(label, kind))
    return findings


def _require(event, *path):
    """Fetch event[path...], failing closed if a step is missing."""
    node = event
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise FailClosed("event JSON missing required field: {}".format(".".join(path)))
        node = node[key]
    return node


def _require_sha(event, *path):
    """Fetch a required field and fail closed unless it is a canonical 40-hex SHA (so a value like 'HEAD',
    '', or a non-string can never be interpolated into git revision syntax and read as a clean empty range)."""
    value = _require(event, *path)
    if not isinstance(value, str) or not _SHA_RE.match(value):
        raise FailClosed("event JSON field {} is not a 40-hex SHA".format(".".join(path)))
    return value


def channels_for_pr(event, messages):
    """Build the channel list for a pull_request event. `messages` is [(sha, body)] for base..head."""
    title = _require(event, "pull_request", "title")
    body = _require(event, "pull_request", "body")            # may be JSON null -> empty text
    ref = _require(event, "pull_request", "head", "ref")
    if not isinstance(title, str) or not title.strip():
        raise FailClosed("event JSON PR title is missing or empty")
    if not isinstance(ref, str) or not ref.strip():
        raise FailClosed("event JSON PR head ref is missing or empty")
    if body is not None and not isinstance(body, str):
        raise FailClosed("event JSON PR body has an unexpected type")
    composed = title + "\n\n" + (body or "")                  # one blob: catches title/body-boundary n-grams
    channels = [("commit {}".format(sha), msg) for sha, msg in messages]
    channels.append(("PR title/body", composed))
    channels.append(("PR head ref", ref))
    return channels


def _reject_json_constant(token):
    """json.loads calls this for NaN/Infinity/-Infinity (non-standard JSON); reject them so a malformed
    event fails closed rather than parsing as if valid."""
    raise ValueError("non-JSON constant in event: {}".format(token))


def load_event():
    """Read and parse $GITHUB_EVENT_PATH, failing closed if unset/unreadable/malformed (NaN/Infinity too)."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise FailClosed("GITHUB_EVENT_PATH is not set")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise FailClosed("cannot read/parse event JSON at {}: {}".format(path, type(exc).__name__))


def gather_channels(event_name, event):
    """Dispatch on the event kind; fail closed on any kind whose channel set we cannot establish. Every
    revision field is validated as a canonical SHA before it reaches git."""
    if event_name == "pull_request":                          # exact match: a pull_request_review or a
        base = _require_sha(event, "pull_request", "base", "sha")  # typo'd name is an unsupported kind
        head = _require_sha(event, "pull_request", "head", "sha")
        return channels_for_pr(event, commit_messages(base, head)), "pre-merge"
    if event_name == "push":
        before = _require_sha(event, "before")
        after = _require_sha(event, "after")
        if after == _ZERO_SHA:                                # branch deletion (or no-op): no new commits
            msgs = []
        elif before == _ZERO_SHA:                             # branch creation: the full new range is
            raise FailClosed("branch-creation push (zero 'before'): cannot establish the complete "
                             "commit range; failing closed rather than scanning only the tip")
        else:
            msgs = commit_messages(before, after)
        return [("commit {}".format(sha), msg) for sha, msg in msgs], "post-merge"
    raise FailClosed("cannot establish the channel set for event kind: {}".format(event_name))


_SCANNED = {
    "pre-merge": "the commit messages in base..head, the composed PR title+body, and the PR head ref",
    "post-merge": "the messages of the newly landed commits (before..after)",
}
_SCOPE = ("PASS ({phase}): scanned STRUCTURAL patterns and the hashed codename denylist across {scanned}. "
          "NOT CHECKED here: file contents (check_leaks + gitleaks own those), reviews/comments/issues, "
          "author identity, other branches/tags. This detects accidental reintroduction in text push has "
          "already made public; it is not an exfiltration control and cannot un-expose pushed text.")


def run(event_name, event):
    hashes, maxn = require_denylist()
    channels, phase = gather_channels(event_name, event)
    findings = scan_channels(channels, hashes, maxn)
    if findings:
        print("FAIL: {} possible leak(s) in commit-message / PR-metadata channels".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print(_SCOPE.format(phase=phase, scanned=_SCANNED[phase]))
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    try:
        event_name = os.environ.get("GITHUB_EVENT_NAME", "")
        if not event_name:
            raise FailClosed("GITHUB_EVENT_NAME is not set (this gate runs in CI on a pull_request/push event)")
        return run(event_name, load_event())
    except FailClosed as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2


def self_test():
    """In-memory fail-cases; no network or live git needed. Exercises the scanner, the channel builder, the
    SHA/field validation, the push semantics, and the denylist-metadata hardening. Uses a synthetic denylist
    hash so no real codename appears here."""
    import tempfile
    import check_leaks as c
    hashes = {c.term_hash("alpha bravo")}
    maxn = 3
    forty = "a" * 40
    # 1. clean channels -> no findings
    assert scan_channels([("PR title/body", "a normal title\n\nnormal body")], hashes, maxn) == []
    # 2. structural hit reported without echoing the value
    priv = "192.168.1.99"  # leak-allow: a synthetic RFC1918 fixture the structural path must flag
    f = scan_channels([("commit abc1234", "fix networking on " + priv)], hashes, maxn)
    assert f == ["commit abc1234: private IP (192.168/16)"], f
    assert priv not in " ".join(f), "must not echo the matched value (only the class label)"
    # 3. codename hash hit across the title/body boundary (composed blob catches it; separate would not)
    ch = channels_for_pr({"pull_request": {"title": "ship alpha", "body": "bravo landed",
                                           "head": {"ref": "feat/x"}}}, [])
    assert scan_channels(ch, hashes, maxn) == ["PR title/body: internal codename (hash match)"]
    # 4. leak-allow is NOT honored here
    assert scan_channels([("commit d", "10.0.0.1 leak-allow")], hashes, maxn) == ["commit d: private IP (10/8)"]
    # 5. null body is valid empty text
    assert ("PR title/body", "t\n\n") in channels_for_pr(
        {"pull_request": {"title": "t", "body": None, "head": {"ref": "r"}}}, [])
    # 6. fail-closed: malformed PR field shapes
    for bad in ({"pull_request": {"title": "t", "head": {"ref": "r"}}},              # no body
                {"pull_request": {"title": "t", "body": "b"}},                       # no head.ref
                {"pull_request": {"title": "", "body": "b", "head": {"ref": "r"}}},   # empty title
                {"pull_request": {"title": "t", "body": "b", "head": {"ref": ""}}},   # empty ref
                {"pull_request": {"title": "t", "body": 5, "head": {"ref": "r"}}},    # body wrong type
                {}):
        try:
            channels_for_pr(bad, [])
            assert False, "expected FailClosed for {}".format(bad)
        except FailClosed:
            pass
    # 7. fail-closed: a revision field that is not a 40-hex SHA (caught before git is ever called)
    for name, ev in (("pull_request", {"pull_request": {"base": {"sha": "HEAD"},
                                                        "head": {"sha": forty, "ref": "r"},
                                                        "title": "t", "body": None}}),
                     ("pull_request", {"pull_request": {"base": {"sha": forty},
                                                        "head": {"sha": "", "ref": "r"},
                                                        "title": "t", "body": None}}),
                     ("push", {"before": "HEAD", "after": forty}),
                     ("push", {"before": forty, "after": []}),
                     ("push", {"before": _ZERO_SHA, "after": forty})):     # branch creation -> fail closed
        try:
            gather_channels(name, ev)
            assert False, "expected FailClosed for {} {}".format(name, ev)
        except FailClosed:
            pass
    # 8. branch deletion (after == zero): no channels, no git call, no exception
    channels, phase = gather_channels("push", {"before": forty, "after": _ZERO_SHA})
    assert channels == [] and phase == "post-merge", (channels, phase)
    # 9. unknown event kind fails closed - including a name that merely PREFIXES "pull_request"
    for kind in ("issues", "pull_request_review", "pull_request_typo"):
        try:
            gather_channels(kind, {"pull_request": {"base": {"sha": forty}, "head": {"sha": forty, "ref": "r"},
                                                    "title": "t", "body": None}})
            assert False, "expected FailClosed for unsupported event kind {}".format(kind)
        except FailClosed:
            pass
    # 10. denylist-metadata hardening (load_denylist): malformed maxn / trailing tokens fail closed
    def denylist(text):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tools").mkdir()
            (root / "tools" / "leak-hashes.txt").write_text(text, encoding="utf-8")
            return c.load_denylist(root)
    h = "0" * 64
    assert denylist("# maxn 0\n" + h + "\n")[2], "maxn 0 must be rejected (would disable the codename layer)"
    assert denylist("# maxn 3 extra\n" + h + "\n")[2], "a trailing token on the maxn directive must be rejected"
    assert denylist("# maxn nope\n" + h + "\n")[2], "a non-integer maxn must be rejected"
    assert denylist(h + " plaintext\n")[2], "a trailing token on a hash line must be rejected"
    hs, mx, bad = denylist("# maxn 2\n" + h + "\n")
    assert bad == [] and mx == 2 and h in hs, (bad, mx, hs)
    assert denylist("# maxn 3\n# maxn 1\n" + h + "\n")[2], "a duplicate/contradictory maxn must be rejected"
    assert denylist("# maxn 101\n" + h + "\n")[2], "an over-cap maxn (>100) must be rejected"
    assert denylist("# maxn " + "9" * 5000 + "\n" + h + "\n")[2], "an oversized maxn must be rejected, not crash"
    try:
        json.loads('{"x": NaN}', parse_constant=_reject_json_constant)
        assert False, "a non-JSON constant (NaN) must be rejected by strict event parsing"
    except ValueError:
        pass
    print("SELF-TEST PASS: scanner reuse, composed title/body boundary catch, no-echo, and leak-allow "
          "ignored; the fail-closed paths hold (malformed PR fields, empty title/ref, a non-40-hex "
          "base/head/before/after, a branch-creation push, an unknown event kind); a branch-deletion push "
          "yields no channels; malformed denylist metadata (maxn 0, duplicate, over-cap, oversized, "
          "trailing tokens) is rejected; and a non-JSON NaN constant fails closed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
