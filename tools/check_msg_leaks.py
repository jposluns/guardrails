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
  - --pre-push --repo <abs> --body-file <abs> --base <40-hex sha> [--title <text>]: the LOCAL,
    pre-publication mode, run by tools/push_branch.py BEFORE a first push leaves the machine (the one
    mode that is prevention rather than detection). Every input is explicit (no ambient default): the
    repository root, the drafted PR body file (composed with --title when one is given, the same
    one-blob rule as the CI mode), and the base SHA. Channels: the body file, the changelog.toml
    release items added in base..HEAD (parsed with tomllib at both revisions, a multiset difference),
    the commit messages in base..HEAD, and the current branch name.
  - --self-test: run embedded fail-cases in memory (CI invokes this alongside the real run).

ATTRIBUTION LAYER (pre-merge and pre-push phases; the post-merge push scan keeps its landed-message
denylist scope). Three narrow patterns, nothing broader: a trailer-shaped `*-by:` line at line start
whose value names an AI identity, a generated/written/drafted with/by/using phrase followed within a
bounded window by an AI identity, and an AI vendor address. The identity set is single-sourced: the
pattern is byte-identical to AI_IDENTITY_RE in the commit-identity hook
(.aiqt/core/hooks/scripts/aiqt_hooks.py), and the self-test extracts the hook's pattern from its source
and fails on any drift. A bare product mention ("fix the Claude adapter's matcher") is NOT attribution
and stays silent by design.

HONEST SCOPE. Like check_leaks, this catches ACCIDENTAL reintroduction of internal terms/host specifics; it
is not an exfiltration control, and deliberate obfuscation (mid-word splits, zero-width, homoglyphs) can
evade the hash layer. The attribution layer is the same honesty: paraphrased attribution ("drafted with an
assistant") and AI names outside the shared identity set are a disclosed residual, and a push made without
tools/push_branch.py never runs the pre-push mode at all. It never echoes the matching text, token,
codename, or a malformed field value (it reports the channel and, for a commit, the short SHA).

FAIL-CLOSED. A missing/unreadable/malformed/empty denylist, missing/unreadable/malformed event JSON, a PR
event missing or mistyping a required field, a revision field that is not a canonical 40-hex SHA, a
branch-creation push whose complete range cannot be established, an unresolvable commit range, or an event
kind whose channel set cannot be established -> exit 2. In --pre-push mode, additionally: an unknown,
repeated or valueless argument, a missing required argument, a relative --repo or --body-file, a body file
that is missing, a symlink, not a regular file, over MAX_BODY_BYTES or not UTF-8, an empty --title, a
--base that is not a canonical non-zero 40-hex SHA, a changelog.toml that does not parse or has an
unexpected shape at either revision, a detached HEAD, or any git failure -> exit 2, and
tools/push_branch.py refuses the push on it. A finding -> exit 1. Clean -> exit 0. Missing input
is never read as clean. A confidentiality gate that cannot load its codename denylist does not run:
absent/empty is exit 2, stricter than the file gate, because here there is no compensating repo-wide file
scan behind it.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_msg_leaks.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import os
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_leaks import load_denylist, scan_text  # noqa: E402  shared denylist + scanner (single source)

ROOT = Path(__file__).resolve().parents[1]
_ZERO_SHA = "0" * 40
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")   # a canonical git object id (the zero-SHA also matches)

# --- the AI-attribution layer (pre-merge and pre-push phases) -------------------------------------
# AI_IDENTITY_PATTERN is byte-identical to AI_IDENTITY_RE in the commit-identity hook
# (.aiqt/core/hooks/scripts/aiqt_hooks.py), which owns the identity set; the self-test extracts the
# hook's pattern from its source (AST, never an import of that module) and fails on any drift, so
# the gate and the hook cannot diverge silently.
AI_IDENTITY_PATTERN = (
    r"(?i)\b(claude|anthropic|openai|chatgpt|codex|copilot|gemini|gpt-?[0-9o][a-z0-9.-]*)\b"
    r"|@anthropic\.com|@openai\.com")
AI_IDENTITY_RE = re.compile(AI_IDENTITY_PATTERN)
# A trailer-shaped line: a `*-by:` key at line start (any leading blanks), so prose that merely
# QUOTES a trailer key mid-sentence never matches; the finding needs an AI identity in the value.
_AI_TRAILER_RE = re.compile(r"^[ \t]*[A-Za-z][A-Za-z0-9-]*-by[ \t]*:[ \t]*(?P<value>.*)$",
                            re.IGNORECASE)
# An authorship verb bound to with/by/using; the AI identity must then match INSIDE the bounded
# window right after the phrase, so a later unrelated mention on the same line is never bound to it.
_AI_PHRASE_RE = re.compile(r"(?i)\b(?:generated|written|drafted)[ \t]+(?:with|by|using)\b")
_AI_PHRASE_WINDOW = 60
# An AI vendor address names the vendor as a party wherever it appears (its usual carrier is a
# generated Co-authored-by trailer, but the address alone is already attribution).
_AI_ADDRESS_RE = re.compile(r"(?i)@(?:anthropic|openai)\.com")
# The pre-push phase runs locally before anything is published; pre-merge is the CI backstop for a
# body edited after the push. The post-merge scan deliberately keeps its landed-message scope.
_ATTRIBUTION_PHASES = ("pre-merge", "pre-push")

MAX_BODY_BYTES = 1024 * 1024   # a drafted PR body; anything larger is not one (fail closed)


class FailClosed(Exception):
    """A condition under which the gate must exit 2 rather than risk a false clean."""


def _run_git(args, root=None):
    """Run git with an argv array (never a shell); fail closed on any error, without echoing git's stderr or
    the revision values (which may reflect a leaked string). root (pre-push mode) defaults to ROOT (CI)."""
    try:
        proc = subprocess.run(["git", "-C", str(root or ROOT), *args], capture_output=True, encoding="utf-8")
    except (OSError, ValueError) as exc:                       # git missing, or undecodable output
        raise FailClosed("git invocation failed ({})".format(type(exc).__name__))
    if proc.returncode != 0:
        raise FailClosed("git {} exited non-zero ({})".format(args[0] if args else "?", proc.returncode))
    return proc.stdout


def commit_messages(base, head, root=None):
    """Return [(short_sha, message)] for base..head, parsed with unambiguous separators (not a shell).
    base and head are canonical SHAs validated by the caller before they reach git."""
    out = _run_git(["log", "--no-color", "-z", "--format=%h%x1f%B", "{}..{}".format(base, head)], root)
    parsed = []
    for rec in (r for r in out.split("\0") if r):
        sha, _, body = rec.partition("\x1f")
        parsed.append((sha, body))
    return parsed


def require_denylist(root=None):
    """Load the hashed denylist, failing closed on absent/empty/unreadable/malformed (a codename gate must
    not run without its denylist). Returns (hashes, maxn). root (pre-push mode) defaults to ROOT (CI)."""
    try:
        hashes, maxn, bad = load_denylist(root or ROOT)
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


def scan_attribution(channels):
    """channels = [(label, text)]. The narrow AI-attribution layer (see the module docstring): a
    `*-by:` trailer line whose value names an AI identity, a generated/written/drafted with/by/using
    phrase with an AI identity inside the window right after it, and an AI vendor address. Returns
    finding strings, never echoing the matched text. The window is a slice, so an identity that only
    STARTS inside it does not match: the whole name must land inside (tested at both edges)."""
    findings = []
    for label, text in channels:
        for line in text.splitlines():
            trailer = _AI_TRAILER_RE.match(line)
            if trailer and AI_IDENTITY_RE.search(trailer.group("value")):
                findings.append("{}: AI attribution (trailer)".format(label))
            for phrase in _AI_PHRASE_RE.finditer(line):
                if AI_IDENTITY_RE.search(line[phrase.end():phrase.end() + _AI_PHRASE_WINDOW]):
                    findings.append("{}: AI attribution (generated-with phrase)".format(label))
                    break
            if _AI_ADDRESS_RE.search(line):
                findings.append("{}: AI attribution (vendor address)".format(label))
    return findings


def all_findings(channels, phase, hashes, maxn):
    """Every finding for one run: the denylist/STRUCTURAL scan on every phase, plus the attribution
    layer on the _ATTRIBUTION_PHASES (commit-time attribution on the post-merge path is the
    commit-identity hook's surface, and this keeps the post-merge tripwire's scope unchanged)."""
    findings = scan_channels(channels, hashes, maxn)
    if phase in _ATTRIBUTION_PHASES:
        findings += scan_attribution(channels)
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


def read_body_file(path_text):
    """The drafted PR body for --pre-push: an absolute, non-symlink, regular file of at most
    MAX_BODY_BYTES, strict UTF-8. Anything else fails closed; a missing or unreadable body is never
    read as an empty (clean) one. An empty FILE is a valid empty body, matching how the CI mode
    treats a JSON-null body."""
    if not isinstance(path_text, str) or not os.path.isabs(path_text):
        raise FailClosed("--body-file must be an absolute path")
    if os.path.islink(path_text):
        raise FailClosed("--body-file must not be a symlink")
    path = Path(path_text)
    if not path.is_file():
        raise FailClosed("--body-file is missing or not a regular file")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise FailClosed("cannot read --body-file ({})".format(type(exc).__name__))
    if len(data) > MAX_BODY_BYTES:
        raise FailClosed("--body-file is over MAX_BODY_BYTES ({} bytes)".format(MAX_BODY_BYTES))
    try:
        return data.decode("utf-8")
    except UnicodeError:
        raise FailClosed("--body-file is not valid UTF-8")


def _release_items(doc, which):
    """The release item strings of one parsed changelog.toml document; any shape surprise fails
    closed rather than scanning a subset and reading the rest as clean."""
    if not isinstance(doc, dict):
        raise FailClosed("changelog.toml at {} is not a table".format(which))
    releases = doc.get("release", [])
    if not isinstance(releases, list):
        raise FailClosed("changelog.toml at {}: release is not an array of tables".format(which))
    items = []
    for release in releases:
        if not isinstance(release, dict):
            raise FailClosed("changelog.toml at {}: a release entry is not a table".format(which))
        entry_items = release.get("items", [])
        if not isinstance(entry_items, list) or not all(isinstance(i, str) for i in entry_items):
            raise FailClosed("changelog.toml at {}: items is not an array of strings".format(which))
        items += entry_items
    return items


def changelog_added_items(base_text, head_text):
    """The changelog.toml release items present at head but not at base, as a MULTISET difference
    (an item added a second time is an addition), sorted for stable output. None stands for a
    revision whose tree has no changelog.toml (no items there). A parse error fails closed."""
    counts = []
    for which, text in (("base", base_text), ("head", head_text)):
        if text is None:
            counts.append(Counter())
            continue
        try:
            doc = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
            # tomllib raises a bare ValueError on an over-long integer literal, RecursionError on deep
            # nesting, and parsing a pathological document can exhaust memory; each is the same verdict
            # here: the changelog revision cannot be evaluated, so the gate fails closed.
            raise FailClosed("changelog.toml at {} does not parse ({})".format(which, type(exc).__name__))
        counts.append(Counter(_release_items(doc, which)))
    return sorted((counts[1] - counts[0]).elements())


def _rev_file_text(rev, rel, root):
    """rel's text at revision rev, or None when rev's tree has no rel. Presence is decided by
    ls-tree (exit 0 with empty output means absent), so absence is never conflated with another git
    failure, which stays fail-closed through _run_git."""
    listing = _run_git(["ls-tree", "-z", rev, "--", rel], root)
    if not listing.strip("\0").strip():
        return None
    return _run_git(["show", "{}:{}".format(rev, rel)], root)


def pre_push_channels(repo, base, body, title):
    """Build the pre-push channel list over the repository at repo: the composed title+body (one
    blob, the CI rule), the changelog items added in base..HEAD, the commit messages in base..HEAD,
    and the current branch name. A detached HEAD fails closed: the helper pushes the current branch,
    so without one there is nothing this mode can bind the scan to."""
    head = _run_git(["rev-parse", "--verify", "HEAD^{commit}"], repo).strip()
    if not _SHA_RE.match(head):
        raise FailClosed("cannot resolve HEAD to a canonical 40-hex SHA")
    branch = _run_git(["symbolic-ref", "--short", "HEAD"], repo).strip()
    if not branch:
        raise FailClosed("cannot establish the current branch (detached HEAD)")
    channels = [("commit {}".format(sha), msg) for sha, msg in commit_messages(base, head, repo)]
    channels.append(("PR body file", (title + "\n\n" + body) if title is not None else body))
    added = changelog_added_items(_rev_file_text(base, "changelog.toml", repo),
                                  _rev_file_text(head, "changelog.toml", repo))
    for number, item in enumerate(added, 1):
        channels.append(("changelog item added #{}".format(number), item))
    channels.append(("branch name", branch))
    return channels


def parse_pre_push_argv(argv):
    """Strict flag parsing for --pre-push: exactly --repo, --body-file, --base and optionally
    --title, each with one value, each at most once. Anything else fails closed (an unknown flag
    silently skipped could drop a channel)."""
    opts = {"repo": None, "body-file": None, "base": None, "title": None}
    arguments = list(argv)
    while arguments:
        flag = arguments.pop(0)
        name = flag[2:] if flag.startswith("--") else None
        if name not in opts:
            raise FailClosed("unknown --pre-push argument")
        if opts[name] is not None:
            raise FailClosed("repeated --pre-push argument: --{}".format(name))
        if not arguments:
            raise FailClosed("--{} requires a value".format(name))
        opts[name] = arguments.pop(0)
    if any(opts[required] is None for required in ("repo", "body-file", "base")):
        raise FailClosed("--pre-push requires --repo, --body-file and --base")
    return opts


def require_base(value):
    """The explicit --base: a canonical, non-zero, lower-case 40-hex SHA (so a branch name, HEAD, an
    abbreviation or the zero SHA can never be interpolated into git revision syntax)."""
    if not isinstance(value, str) or not _SHA_RE.match(value) or value == _ZERO_SHA:
        raise FailClosed("--base is not a canonical non-zero 40-hex SHA")
    return value


def run_pre_push(argv):
    opts = parse_pre_push_argv(argv)
    if not os.path.isabs(opts["repo"]) or not os.path.isdir(opts["repo"]):
        raise FailClosed("--repo must be an absolute path to an existing directory")
    repo = Path(opts["repo"])
    base = require_base(opts["base"])
    title = opts["title"]
    if title is not None and not title.strip():
        raise FailClosed("--title, when given, must be non-empty")
    body = read_body_file(opts["body-file"])
    hashes, maxn = require_denylist(repo)
    findings = all_findings(pre_push_channels(repo, base, body, title), "pre-push", hashes, maxn)
    if findings:
        print("FAIL: {} possible finding(s) in pre-push channels".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print(_SCOPE.format(phase="pre-push", scanned=_SCANNED["pre-push"]))
    return 0


_SCANNED = {
    "pre-merge": "the commit messages in base..head, the composed PR title+body, and the PR head ref",
    "post-merge": "the messages of the newly landed commits (before..after)",
    "pre-push": "the PR body file (composed with any --title), the changelog items added in "
                "base..HEAD, the commit messages in base..HEAD, and the current branch name",
}
_SCOPE = ("PASS ({phase}): scanned STRUCTURAL patterns and the hashed codename denylist, plus (pre-merge "
          "and pre-push only) the narrow AI-attribution patterns (a *-by: trailer naming an AI, a "
          "generated/written/drafted with/by/using phrase naming an AI, an AI vendor address), across "
          "{scanned}. NOT CHECKED here: file contents (check_leaks + gitleaks own those), "
          "reviews/comments/issues, paraphrased attribution or AI names outside the shared identity set, "
          "other branches/tags. The CI phases detect accidental reintroduction in text push has already "
          "made public (not an exfiltration control; they cannot un-expose pushed text); only the "
          "pre-push mode runs before publication, and only over the channels it is handed.")


def run(event_name, event):
    hashes, maxn = require_denylist()
    channels, phase = gather_channels(event_name, event)
    findings = all_findings(channels, phase, hashes, maxn)
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
        if argv and argv[0] == "--pre-push":
            return run_pre_push(argv[1:])
        event_name = os.environ.get("GITHUB_EVENT_NAME", "")
        if not event_name:
            raise FailClosed("GITHUB_EVENT_NAME is not set (this gate runs in CI on a pull_request/push event)")
        return run(event_name, load_event())
    except FailClosed as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2


def _hook_identity_pattern():
    """AI_IDENTITY_RE's pattern string as the commit-identity hook defines it, extracted from the
    AST of .aiqt/core/hooks/scripts/aiqt_hooks.py (never imported: it is a large event-hook module,
    and the gate must not execute it). Self-test only; a hook source without the assignment is a
    self-test failure, never a silent pass."""
    import ast
    source = (ROOT / ".aiqt" / "core" / "hooks" / "scripts" / "aiqt_hooks.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "AI_IDENTITY_RE"
                and isinstance(node.value, ast.Call) and node.value.args
                and isinstance(node.value.args[0], ast.Constant)
                and isinstance(node.value.args[0].value, str)):
            return node.value.args[0].value
    raise AssertionError("AI_IDENTITY_RE assignment not found in the hooks source")


def _expect(condition, message=None):
    """A self-test verdict that python -O and -OO cannot strip, unlike an assert statement."""
    if not condition:
        if message is None:
            raise AssertionError
        raise AssertionError(message)


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
    _expect(scan_channels([("PR title/body", "a normal title\n\nnormal body")], hashes, maxn) == [])
    # 2. structural hit reported without echoing the value
    priv = "192.168.1.99"  # leak-allow: a synthetic RFC1918 fixture the structural path must flag
    f = scan_channels([("commit abc1234", "fix networking on " + priv)], hashes, maxn)
    _expect(f == ["commit abc1234: private IP (192.168/16)"], f)
    _expect(priv not in " ".join(f), "must not echo the matched value (only the class label)")
    # 3. codename hash hit across the title/body boundary (composed blob catches it; separate would not)
    ch = channels_for_pr({"pull_request": {"title": "ship alpha", "body": "bravo landed",
                                           "head": {"ref": "feat/x"}}}, [])
    _expect(scan_channels(ch, hashes, maxn) == ["PR title/body: internal codename (hash match)"])
    # 4. leak-allow is NOT honored here
    _expect(scan_channels([("commit d", "10.0.0.1 leak-allow")], hashes, maxn) == ["commit d: private IP (10/8)"])
    # 5. null body is valid empty text
    _expect(("PR title/body", "t\n\n") in channels_for_pr(
        {"pull_request": {"title": "t", "body": None, "head": {"ref": "r"}}}, []))
    # 6. fail-closed: malformed PR field shapes
    for bad in ({"pull_request": {"title": "t", "head": {"ref": "r"}}},              # no body
                {"pull_request": {"title": "t", "body": "b"}},                       # no head.ref
                {"pull_request": {"title": "", "body": "b", "head": {"ref": "r"}}},   # empty title
                {"pull_request": {"title": "t", "body": "b", "head": {"ref": ""}}},   # empty ref
                {"pull_request": {"title": "t", "body": 5, "head": {"ref": "r"}}},    # body wrong type
                {}):
        try:
            channels_for_pr(bad, [])
            raise AssertionError("expected FailClosed for {}".format(bad))
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
            raise AssertionError("expected FailClosed for {} {}".format(name, ev))
        except FailClosed:
            pass
    # 8. branch deletion (after == zero): no channels, no git call, no exception
    channels, phase = gather_channels("push", {"before": forty, "after": _ZERO_SHA})
    _expect(channels == [] and phase == "post-merge", (channels, phase))
    # 9. unknown event kind fails closed - including a name that merely PREFIXES "pull_request"
    for kind in ("issues", "pull_request_review", "pull_request_typo"):
        try:
            gather_channels(kind, {"pull_request": {"base": {"sha": forty}, "head": {"sha": forty, "ref": "r"},
                                                    "title": "t", "body": None}})
            raise AssertionError("expected FailClosed for unsupported event kind {}".format(kind))
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
    _expect(denylist("# maxn 0\n" + h + "\n")[2], "maxn 0 must be rejected (would disable the codename layer)")
    _expect(denylist("# maxn 3 extra\n" + h + "\n")[2], "a trailing token on the maxn directive must be rejected")
    _expect(denylist("# maxn nope\n" + h + "\n")[2], "a non-integer maxn must be rejected")
    _expect(denylist(h + " plaintext\n")[2], "a trailing token on a hash line must be rejected")
    hs, mx, bad = denylist("# maxn 2\n" + h + "\n")
    _expect(bad == [] and mx == 2 and h in hs, (bad, mx, hs))
    _expect(denylist("# maxn 3\n# maxn 1\n" + h + "\n")[2], "a duplicate/contradictory maxn must be rejected")
    _expect(denylist("# maxn 101\n" + h + "\n")[2], "an over-cap maxn (>100) must be rejected")
    _expect(denylist("# maxn " + "9" * 5000 + "\n" + h + "\n")[2], "an oversized maxn must be rejected, not crash")
    try:
        json.loads('{"x": NaN}', parse_constant=_reject_json_constant)
        raise AssertionError("a non-JSON constant (NaN) must be rejected by strict event parsing")
    except ValueError:
        pass
    # 11. attribution layer: each finding shape fires and reports only the channel + class label
    for text, kind in (("t\n\nCo-authored-by: Claude <c@example.org>", "trailer"),
                       ("CO-AUTHORED-BY: Codex\r\nmore text", "trailer"),            # case + CRLF
                       ("  Generated-by: gemini", "trailer"),                        # indented trailer
                       ("This change was generated with Claude Code.", "generated-with phrase"),
                       ("Docs drafted using ChatGPT.", "generated-with phrase"),
                       ("written by claude", "generated-with phrase"),
                       ("Contact noreply@anthropic.com for details.", "vendor address")):
        f = scan_attribution([("ch", text)])
        _expect(f == ["ch: AI attribution ({})".format(kind)], (text, f))
        _expect("claude" not in " ".join(f).lower() and "codex" not in " ".join(f).lower()
                and "gemini" not in " ".join(f).lower() and "chatgpt" not in " ".join(f).lower(),
                "attribution findings must not echo the matched identity")
    # a generated trailer usually carries the vendor address too: both classes are reported
    both = scan_attribution([("ch", "Co-authored-by: Claude <noreply@anthropic.com>")])
    _expect(sorted(both) == ["ch: AI attribution (trailer)", "ch: AI attribution (vendor address)"], both)
    # 12. attribution narrowness: product mentions, quoted keys, human or lookalike values stay silent
    for quiet in ("as a Skill you upload to Claude or a portable instruction file for other "
                  "assistants (ChatGPT, Copilot, Gemini)",
                  "fix the Claude adapter's matcher",
                  "`Co-authored-by:` trailers naming an AI are refused",
                  "Docs: the line Co-authored-by: Claude is refused at commit time",  # key not at start
                  "Co-authored-by: Jane Doe <jane@example.org>",
                  "Co-authored-by: Claudette <c@example.org>",                        # word boundary
                  "benchmarks against gpt-4o show parity",
                  ""):
        _expect(scan_attribution([("ch", quiet)]) == [], quiet)
    # 13. phrase-window boundaries: an identity that ENDS exactly at the window edge matches; one
    # letter past the edge, or starting past it, does not (the window is a slice of the line, so a
    # partial name at the cut can never match). The filler is non-word so only the window decides.
    inside = "generated with" + "." * (_AI_PHRASE_WINDOW - 6) + "claude"    # ends exactly at the edge
    _expect(len(inside) - len("generated with") == _AI_PHRASE_WINDOW, "fixture arithmetic")
    _expect(scan_attribution([("ch", inside)]) == ["ch: AI attribution (generated-with phrase)"], inside)
    straddle = "generated with" + "." * (_AI_PHRASE_WINDOW - 5) + "claude"   # last letter is outside
    _expect(scan_attribution([("ch", straddle)]) == [], straddle)
    outside = "generated with" + "." * _AI_PHRASE_WINDOW + "claude"          # starts outside
    _expect(scan_attribution([("ch", outside)]) == [], outside)
    # 14. the phase wiring: attribution runs pre-merge and pre-push, never post-merge
    ch = [("commit abc1234", "Co-authored-by: Claude <c@example.org>")]
    _expect(all_findings(ch, "pre-merge", hashes, maxn) == ["commit abc1234: AI attribution (trailer)"])
    _expect(all_findings(ch, "pre-push", hashes, maxn) == ["commit abc1234: AI attribution (trailer)"])
    _expect(all_findings(ch, "post-merge", hashes, maxn) == [])
    # 15. the identity set is single-sourced: the gate's pattern is byte-identical to the hook's
    _expect(_hook_identity_pattern() == AI_IDENTITY_PATTERN,
            "AI_IDENTITY_PATTERN drifted from the commit-identity hook's AI_IDENTITY_RE")
    # 16. changelog delta: a multiset difference over release items, fail-closed on parse/shape
    base_toml = 'title = "t"\n[[release]]\nitems = ["kept item"]\n'
    head_toml = 'title = "t"\n[[release]]\nitems = ["kept item", "new: drafted using ChatGPT"]\n'
    added = changelog_added_items(base_toml, head_toml)
    _expect(added == ["new: drafted using ChatGPT"], added)
    _expect(scan_attribution([("changelog item added #1", added[0])])
            == ["changelog item added #1: AI attribution (generated-with phrase)"])
    _expect(changelog_added_items(head_toml, base_toml) == [])      # a removal adds nothing
    _expect(changelog_added_items(None, base_toml) == ["kept item"])  # file new at head
    _expect(changelog_added_items(base_toml, None) == [])           # file absent at head
    dup_head = 'title = "t"\n[[release]]\nitems = ["kept item", "kept item"]\n'
    _expect(changelog_added_items(base_toml, dup_head) == ["kept item"], "multiset, not set")
    for bad_base, bad_head in ((base_toml, "not toml ["), ("= bad", head_toml),
                               (base_toml, 'release = 5\n'),
                               (base_toml, '[[release]]\nitems = "str"\n'),
                               (base_toml, '[[release]]\nitems = [5]\n')):
        try:
            changelog_added_items(bad_base, bad_head)
            raise AssertionError("expected FailClosed for changelog {!r}/{!r}".format(bad_base, bad_head))
        except FailClosed:
            pass
    # 17. the body file: fail-closed set, and the size bound just inside / at / just over
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        good = root / "body.md"
        good.write_text("hello", encoding="utf-8")
        _expect(read_body_file(str(good)) == "hello")
        (root / "empty.md").write_bytes(b"")
        _expect(read_body_file(str(root / "empty.md")) == "")       # empty body is a valid empty text
        (root / "inside.md").write_bytes(b"a" * (MAX_BODY_BYTES - 1))
        (root / "at.md").write_bytes(b"a" * MAX_BODY_BYTES)
        _expect(len(read_body_file(str(root / "inside.md"))) == MAX_BODY_BYTES - 1)
        _expect(len(read_body_file(str(root / "at.md"))) == MAX_BODY_BYTES)
        (root / "over.md").write_bytes(b"a" * (MAX_BODY_BYTES + 1))
        (root / "binary.md").write_bytes(b"\xff\xfe\x00")
        (root / "link.md").symlink_to(good)
        for bad in ("body.md",                                      # relative
                    str(root / "missing.md"),                       # absent
                    str(root),                                      # a directory
                    str(root / "over.md"),                          # over the bound
                    str(root / "binary.md"),                        # not UTF-8
                    str(root / "link.md")):                         # a symlink
            try:
                read_body_file(bad)
                raise AssertionError("expected FailClosed for body file {!r}".format(bad))
            except FailClosed:
                pass
    # 18. --pre-push argv and --base validation fail closed on every malformed shape
    parsed = parse_pre_push_argv(["--repo", "/r", "--body-file", "/b", "--base", forty])
    _expect(parsed == {"repo": "/r", "body-file": "/b", "base": forty, "title": None}, parsed)
    for argv in (["--repo", "/r"],                                   # missing required flags
                 ["--repo", "/r", "--body-file", "/b", "--base", forty, "--bogus", "x"],
                 ["--repo", "/r", "--repo", "/r2", "--body-file", "/b", "--base", forty],
                 ["--repo", "/r", "--body-file", "/b", "--base"],    # valueless flag
                 ["positional"]):
        try:
            parse_pre_push_argv(argv)
            raise AssertionError("expected FailClosed for argv {!r}".format(argv))
        except FailClosed:
            pass
    _expect(require_base(forty) == forty)
    for bad in ("HEAD", "a" * 39, "a" * 41, "A" * 40, _ZERO_SHA, 5, None, ""):
        try:
            require_base(bad)
            raise AssertionError("expected FailClosed for base {!r}".format(bad))
        except FailClosed:
            pass
    print("SELF-TEST PASS: scanner reuse, composed title/body boundary catch, no-echo, and leak-allow "
          "ignored; the fail-closed paths hold (malformed PR fields, empty title/ref, a non-40-hex "
          "base/head/before/after, a branch-creation push, an unknown event kind); a branch-deletion push "
          "yields no channels; malformed denylist metadata (maxn 0, duplicate, over-cap, oversized, "
          "trailing tokens) is rejected; a non-JSON NaN constant fails closed; the attribution layer "
          "fires on trailers, bounded generated-with phrases and vendor addresses, stays silent on bare "
          "mentions, runs pre-merge and pre-push only, and matches the hook's identity set; and the "
          "pre-push inputs (argv, --base, body file, changelog delta) each fail closed when malformed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
