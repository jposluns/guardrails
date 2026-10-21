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
  - --pre-push --repo <abs> --body-file <abs> --base <40-hex sha> [--title <text>] [--head <40-hex sha>]
    [--branch <name>] [--remote <name-or-url>]: the LOCAL, pre-publication mode, run by
    tools/push_branch.py BEFORE a first push leaves the machine (the one mode that is prevention rather
    than detection). Every input is explicit (no ambient default): the repository root (and it must BE
    the root: a subdirectory would read the changelog as absent), the drafted PR body file (composed
    with --title when one is given, the same one-blob rule as the CI mode), and the base SHA. The
    SCANNED SET is bound to what the push would actually publish: every commit reachable from the
    scanned commit and not reachable from any object the remote advertises, read with git ls-remote at
    check time (local remote-tracking refs can be stale, and a path or URL remote has none, so neither
    is consulted); when that set cannot be computed, or it is empty, the run fails closed. That binding
    holds only when the advertisement read and the push destination are one repository, so the remote's
    push URL set must be exactly its one fetch URL (a remote.<name>.pushurl, an added URL or a
    pushInsteadOf rewrite fails closed, and so does a --remote given as a URL while any pushInsteadOf
    rewrite is configured); and every git read here is taken on the true object graph the push
    transports (replace refs and a grafts file are ignored), so neither mechanism can substitute a
    clean message, parent list or changelog for the ones the push sends. --base does
    not define the set (it could not: commits below it that the remote lacks are published too); it is
    kept as a CROSS-CHECK of the caller's intent and must still be a proper ancestor of the scanned
    commit; when the default branch has advanced past the branch point, pass the merge-base
    (git merge-base <default-branch> HEAD), not the advanced tip. --remote defaults to origin. --head
    and --branch, when given, bind the scan to the exact commit and branch name the helper resolved
    once and will push; without them HEAD and the current branch are resolved here. Channels: the body
    file, the message of every commit in the scanned set, every string each of those commits adds to
    the parsed changelog.toml against its parent or parents (keys and values alike, a multiset
    difference per commit, so a string added and removed again inside the range is still scanned at the
    intermediate state that carried it), and the branch name. On a clean run it prints `body sha256:`,
    the digest of the exact body bytes it scanned, for the helper to reprint.
  - --self-test (honoured only as the SOLE argument): run embedded fail-cases in memory (CI invokes this
    alongside the real run). Mixed with anything else it is an unknown argument and the run fails closed,
    so it can never stand in for a requested production scan.

ATTRIBUTION LAYER (pre-merge and pre-push phases; the post-merge push scan keeps its landed-message
denylist scope). Three narrow patterns, nothing broader: a trailer-shaped `*-by:` line at line start
whose value names an AI identity, a generated/written/drafted with/by/using phrase followed within a
bounded window by an AI identity (matched against the original line, and the identity must BEGIN inside
the window; its match may run past the edge, because a longer gpt- form such as a dated model id is
itself an AI identity), and an AI vendor address bounded so a longer domain is not read as the vendor's. The
identity set is defined in TWO PLACES kept byte-identical by a test: this gate's AI_IDENTITY_PATTERN and
AI_IDENTITY_RE in the commit-identity hook (.aiqt/core/hooks/scripts/aiqt_hooks.py); the self-test
extracts the hook's pattern from its source and fails on any drift, including a changed compile-call
shape, so divergence is caught whenever the self-test runs. A bare product mention ("fix the Claude
adapter's matcher") is NOT attribution and stays silent by design.

HONEST SCOPE. Like check_leaks, this catches ACCIDENTAL reintroduction of internal terms/host specifics; it
is not an exfiltration control, and deliberate obfuscation (mid-word splits, zero-width, homoglyphs) can
evade the hash layer. The attribution layer is the same honesty: paraphrased attribution ("drafted with an
assistant"), AI names outside the shared identity set, and attribution split across lines ("Generated
with" ending one line, the name opening the next) are a disclosed residual; and the layer has a disclosed
false-positive cost, because a bounded textual window cannot read intent: an authorship phrase followed
within the window by an unrelated AI name, or a `*-by:` trailer whose value merely mentions one, fires
(the self-test documents these). A push made without tools/push_branch.py never runs the pre-push mode at
all. It never echoes the matching text, token, codename, or a malformed field value (it reports the
channel and, for a commit, the short SHA).

FAIL-CLOSED. A missing/unreadable/malformed/empty denylist, missing/unreadable/malformed event JSON, a PR
event missing or mistyping a required field, a revision field that is not a canonical 40-hex SHA, a
branch-creation push whose complete range cannot be established, an unresolvable commit range, or an event
kind whose channel set cannot be established -> exit 2. In --pre-push mode, additionally: an unknown,
repeated or valueless argument, a missing required argument, a relative --repo or --body-file, a --repo
that is not the repository root, a body file that is missing, a symlink, not a regular file, over
MAX_BODY_BYTES or not UTF-8, an empty --title, --branch or --remote, a --base or --head that is not a
canonical non-zero 40-hex SHA, a --head that does not name a commit in the repository, a --base that is
not a proper ancestor of the scanned commit (base equal to it included; pass the merge-base when the
default branch has advanced past the branch point), a configured remote whose push URL set is not
exactly its one fetch URL (a pushurl, an added URL or a pushInsteadOf rewrite would send the push to a
repository other than the one whose advertisement defined the scanned set), a --remote given as a URL
while any url.<base>.pushInsteadOf rewrite is configured, a remote whose advertised refs cannot be read
with git ls-remote (without them the published set cannot be established), unexpected ls-remote or git log
output, a published set that is EMPTY (the remote already has every commit reachable from the scanned
commit: scanning nothing is never read as clean), a changelog.toml that does not parse or has an
unexpected shape at any published commit or any of its parents, a changelog.toml deleted at any
published commit relative to a parent, a detached HEAD, or any git failure -> exit 2, and
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

import hashlib
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
# AI_IDENTITY_PATTERN is kept byte-identical to AI_IDENTITY_RE in the commit-identity hook
# (.aiqt/core/hooks/scripts/aiqt_hooks.py). These are TWO COPIES under a parity test, not one shared
# runtime definition: the self-test extracts the hook's pattern from its source (AST, never an
# import of that module) and fails on any drift, a changed compile-call shape included, so the gate
# and the hook cannot diverge silently past a self-test run.
AI_IDENTITY_PATTERN = (
    r"(?i)\b(claude|anthropic|openai|chatgpt|codex|copilot|gemini|gpt-?[0-9o][a-z0-9.-]*)\b"
    r"|@anthropic\.com|@openai\.com")
AI_IDENTITY_RE = re.compile(AI_IDENTITY_PATTERN)
# A trailer-shaped line: a `*-by:` key at line start (any leading blanks), so prose that merely
# QUOTES a trailer key mid-sentence never matches; the finding needs an AI identity in the value.
# A value that merely MENTIONS an AI name also fires: a documented false-positive cost.
_AI_TRAILER_RE = re.compile(r"^[ \t]*[A-Za-z][A-Za-z0-9-]*-by[ \t]*:[ \t]*(?P<value>.*)$",
                            re.IGNORECASE)
# An authorship verb bound to with/by/using; the AI identity must then BEGIN inside the bounded
# window right after the phrase, matched against the ORIGINAL line, never a slice of it: a longer
# word that merely begins with an identity ("claudette") never matches at all, because the
# pattern's word boundary decides that, not the window; an identity beginning inside the window
# whose match runs past the edge still fires, because a longer gpt- form (a dated model id such
# as gpt-4o-2024-08-06) is itself an AI identity; and a mention beginning past the window is not
# bound to the phrase. A nearer mention that is unrelated ("generated by the protobuf compiler,
# used by the Claude adapter") still fires: a bounded textual window cannot read intent, a
# documented false-positive cost.
_AI_PHRASE_RE = re.compile(r"(?i)\b(?:generated|written|drafted)[ \t]+(?:with|by|using)\b")
_AI_PHRASE_WINDOW = 60
# An AI vendor address names the vendor as a party wherever it appears (its usual carrier is a
# generated Co-authored-by trailer, but the address alone is already attribution). The lookahead
# requires the domain to END at .com, so a longer domain that merely begins with the vendor's
# (@anthropic.community) is not read as the vendor's. The shared identity pattern above keeps its
# boundary-free address alternatives to stay byte-identical with the hook, so through the trailer
# and phrase layers such a longer domain can still fire: a documented false-positive cost.
_AI_ADDRESS_RE = re.compile(r"(?i)@(?:anthropic|openai)\.com(?![a-z0-9-])")
# The pre-push phase runs locally before anything is published; pre-merge is the CI backstop for a
# body edited after the push. The post-merge scan deliberately keeps its landed-message scope.
_ATTRIBUTION_PHASES = ("pre-merge", "pre-push")

MAX_BODY_BYTES = 1024 * 1024   # a drafted PR body; anything larger is not one (fail closed)


class FailClosed(Exception):
    """A condition under which the gate must exit 2 rather than risk a false clean."""


def _git_status(args, root=None, stdin_text=None):
    """Launch git with an argv array (never a shell) and return (returncode, stdout), for the few
    probes whose non-zero exit is an answer rather than an error; _run_git wraps this for every
    read that must fail closed. Every launch reads the TRUE object graph the push transports:
    --no-replace-objects keeps a refs/replace rewrite from substituting another commit's message,
    parents or changelog for the ones the push sends (the push always sends the original objects),
    and GIT_GRAFT_FILE pinned to the null device keeps an info/grafts file from cutting parents
    out of the ancestry walk, so neither mechanism can hide a published commit from the scan."""
    env = dict(os.environ)
    env["GIT_GRAFT_FILE"] = os.devnull
    try:
        proc = subprocess.run(["git", "--no-replace-objects", "-C", str(root or ROOT), *args],
                              capture_output=True, encoding="utf-8", input=stdin_text, env=env)
    except (OSError, ValueError) as exc:                       # git missing, or undecodable output
        raise FailClosed("git invocation failed ({})".format(type(exc).__name__))
    return proc.returncode, proc.stdout


def _run_git(args, root=None, stdin_text=None):
    """Run git through _git_status; fail closed on any error, without echoing git's stderr or
    the revision values (which may reflect a leaked string). root (pre-push mode) defaults to ROOT (CI).
    stdin_text, when given, is fed to git's standard input (used with --stdin so a large revision set is
    never spliced into the argument vector)."""
    returncode, stdout = _git_status(args, root, stdin_text)
    if returncode != 0:
        raise FailClosed("git {} exited non-zero ({})".format(args[0] if args else "?", returncode))
    return stdout


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
    finding strings, never echoing the matched text. The identity is matched against the ORIGINAL
    line, never a slice of it, and the match must BEGIN inside the window: a longer word that
    merely begins with an identity ("claudette") never matches at all (the pattern's word boundary
    decides), while a match that begins inside and runs past the edge still fires, because a
    longer gpt- form (a dated model id) is itself an AI identity (tested at both edges)."""
    findings = []
    for label, text in channels:
        for line in text.splitlines():
            trailer = _AI_TRAILER_RE.match(line)
            if trailer and AI_IDENTITY_RE.search(trailer.group("value")):
                findings.append("{}: AI attribution (trailer)".format(label))
            for phrase in _AI_PHRASE_RE.finditer(line):
                identity = AI_IDENTITY_RE.search(line, phrase.end())
                # The leftmost identity match at or after the phrase decides: any other match
                # begins no earlier, so when this one begins at or past the window's end no
                # identity begins inside it. (An end-inside rule would be unsound twice over: a
                # long match beginning inside the window is dropped although a longer gpt- form
                # is itself a model id, and an overlapped shorter identity ending inside the
                # window is skipped because the search only ever returns the leftmost match.)
                if identity and identity.start() < phrase.end() + _AI_PHRASE_WINDOW:
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
    treats a JSON-null body. Returns (text, sha256 hex of the exact bytes read), so the digest the
    helper prints can only describe the bytes this gate scanned."""
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
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except UnicodeError:
        raise FailClosed("--body-file is not valid UTF-8")


def _require_changelog_shape(doc, which):
    """The shape guard for one parsed changelog.toml document, kept as strict as it was when only
    release items were collected: the document is a table, release (when present) is an array of
    tables, and items (when present in a release) is an array of strings."""
    if not isinstance(doc, dict):
        raise FailClosed("changelog.toml at {} is not a table".format(which))
    releases = doc.get("release", [])
    if not isinstance(releases, list):
        raise FailClosed("changelog.toml at {}: release is not an array of tables".format(which))
    for release in releases:
        if not isinstance(release, dict):
            raise FailClosed("changelog.toml at {}: a release entry is not a table".format(which))
        entry_items = release.get("items", [])
        if not isinstance(entry_items, list) or not all(isinstance(i, str) for i in entry_items):
            raise FailClosed("changelog.toml at {}: items is not an array of strings".format(which))


def _document_strings(doc):
    """Every string in one parsed document: keys and values, nested tables and arrays included,
    walked iteratively so document depth cannot raise RecursionError here. A non-string scalar (an
    integer, float, boolean or date) carries no text to scan."""
    strings, stack = [], [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            for key, value in node.items():
                strings.append(key)
                stack.append(value)
    return strings


def _changelog_counter(text, which):
    """The multiset of EVERY string in one parsed changelog.toml revision, keys and values alike;
    None (a tree without the file) counts as empty. A parse or shape failure fails closed, naming
    which revision (never its content)."""
    if text is None:
        return Counter()
    try:
        doc = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        # tomllib raises a bare ValueError on an over-long integer literal, RecursionError on deep
        # nesting, and parsing a pathological document can exhaust memory; each is the same verdict
        # here: the changelog revision cannot be evaluated, so the gate fails closed.
        raise FailClosed("changelog.toml at {} does not parse ({})".format(which, type(exc).__name__))
    _require_changelog_shape(doc, which)
    return Counter(_document_strings(doc))


def changelog_added_items(base_text, head_text, base_name="base", head_name="head"):
    """The strings present in changelog.toml at head but not at base, as a MULTISET difference over
    EVERY string in each parsed document, keys and values alike (a string added a second time is an
    addition), sorted for stable output. Scanning every string rather than a named subset means a
    leak in any parsed field, a misspelled or unexpected one included, is scanned, never silently
    ignored; only TOML comments are outside the parse. None stands for a revision whose tree has no
    changelog.toml, with each absence defined explicitly: absent at both revisions, nothing was
    added between them; absent only at base means the file is new and every string at head is an
    addition; present at base but deleted at head fails closed (a deletion is never read as
    clean). A parse error fails closed. The pre-push mode applies this per PUBLISHED COMMIT against
    its parent (base_name and head_name label the refusals), never only at the endpoints, where a
    string added and removed again inside the range would cancel out of the difference while its
    intermediate blob is still published."""
    if base_text is None and head_text is None:
        return []
    if head_text is None:
        raise FailClosed("changelog.toml exists at {} but not at {}: a deleted changelog is "
                         "never read as clean".format(base_name, head_name))
    return sorted((_changelog_counter(head_text, head_name)
                   - _changelog_counter(base_text, base_name)).elements())


def _changelog_added_at(short, text, parent_texts):
    """The changelog strings ONE published commit adds: present in its changelog.toml and carried
    in from NO parent (for a merge, the multiset intersection of the per-parent differences, so a
    string one parent already carried is that parent's, not an addition here). A root commit adds
    every string. A changelog present at any parent but absent at the commit is a deletion and
    fails closed, so no intermediate deletion is skipped."""
    if not parent_texts:
        parent_texts = [None]
    if text is None:
        if all(parent is None for parent in parent_texts):
            return []
        raise FailClosed("changelog.toml exists at a parent of commit {} but not at the commit: a "
                         "deleted changelog is never read as clean".format(short))
    added = None
    commit_counter = _changelog_counter(text, "commit {}".format(short))
    for number, parent_text in enumerate(parent_texts, 1):
        step = commit_counter - _changelog_counter(
            parent_text, "parent {} of commit {}".format(number, short))
        added = step if added is None else (added & step)
    return sorted(added.elements())


def _rev_file_text(rev, rel, root):
    """rel's text at revision rev, or None when rev's tree has no rel. Presence is decided by
    ls-tree (exit 0 with empty output means absent), so absence is never conflated with another git
    failure, which stays fail-closed through _run_git. The caller guarantees root is the repository
    root (run_pre_push enforces it), so ls-tree's working-directory-relative path reading cannot
    silently drop the file."""
    listing = _run_git(["ls-tree", "-z", rev, "--", rel], root)
    if not listing.strip("\0").strip():
        return None
    return _run_git(["show", "{}:{}".format(rev, rel)], root)


def _require_push_url_matches(remote, root):
    """git ls-remote reads the advertisement of the remote's FETCH URL, while git push writes to
    its PUSH URL(s), so a scanned set derived from that advertisement describes the push only when
    both name one repository; otherwise a commit the fetch repository already holds is excluded
    from the scan yet still sent to the push repository. For a configured remote, git remote
    get-url expands remote.<name>.url, remote.<name>.pushurl and the url.<base>.insteadOf /
    pushInsteadOf rewrites, so this requires exactly one fetch URL and exactly one push URL,
    byte-identical: an extra URL (push sends to every configured URL), a differing pushurl, or a
    pushInsteadOf rewrite each fails closed. A --remote that is not a configured remote name is a
    URL; ls-remote and push apply an insteadOf rewrite to it alike, but a pushInsteadOf rewrite
    redirects only the push, so with any url.<base>.pushInsteadOf configured at all the run fails
    closed (whether a given rewrite matches a given URL is git's prefix logic, not re-derived
    here)."""
    rc, names = _git_status(["remote"], root)
    if rc != 0:
        raise FailClosed("cannot list the configured remotes: the push destination cannot be "
                         "established")
    if remote in names.splitlines():
        fetch_urls = _run_git(["remote", "get-url", "--all", remote], root).splitlines()
        push_urls = _run_git(["remote", "get-url", "--push", "--all", remote], root).splitlines()
        if len(fetch_urls) != 1 or push_urls != fetch_urls:
            raise FailClosed("the remote's push URL set is not exactly its one fetch URL (a "
                             "pushurl, an added URL or a pushInsteadOf rewrite): the refs it "
                             "advertises would describe a different repository than the push "
                             "writes to, so the set of commits the push would publish cannot "
                             "be established")
        return
    rc, _out = _git_status(["config", "--get-regexp", r"^url\..*\.pushinsteadof$"], root)
    if rc == 0:
        raise FailClosed("--remote is a URL and a url.<base>.pushInsteadOf rewrite is "
                         "configured: the push could be redirected away from the advertised "
                         "refs the scan read, so the set of commits the push would publish "
                         "cannot be established")
    if rc != 1:
        raise FailClosed("cannot read the URL rewrite configuration: the push destination "
                         "cannot be established")


def _remote_advertised_shas(remote, root):
    """Every object id the target remote advertises RIGHT NOW, read with git ls-remote at check
    time: local remote-tracking refs can be stale, and a path or URL remote has none at all, so
    neither is consulted. A failure to read the advertisement fails closed (without it the set of
    commits the push would publish cannot be established), and so does an output line without a
    canonical object id."""
    try:
        out = _run_git(["ls-remote", "--quiet", remote], root)
    except FailClosed:
        raise FailClosed("cannot read the refs the remote advertises (git ls-remote failed): the "
                         "set of commits the push would publish cannot be established")
    shas = set()
    for line in out.splitlines():
        sha = line.split("\t", 1)[0].strip()
        if not _SHA_RE.match(sha):
            raise FailClosed("unexpected git ls-remote output: the advertised refs cannot be "
                             "established")
        shas.add(sha)
    return shas


def _published_commits(head, advertised, root):
    """The commits the push would PUBLISH: every commit reachable from head and not reachable from
    any object the remote advertises, as [(sha, short_sha, [parent shas], message)] in rev-list
    order. The revision set is fed to git on stdin (never spliced into the argument vector), and
    --ignore-missing drops an advertised object this repository does not have: an object we do not
    have cannot be in head's ancestry, so dropping it excludes nothing. Unexpected output fails
    closed; an empty result (the remote already has head) is the caller's to refuse."""
    feed = "\n".join([head, "--not"] + sorted(advertised)) + "\n"
    out = _run_git(["log", "--no-color", "-z", "--format=%H%x1f%h%x1f%P%x1f%B",
                    "--ignore-missing", "--stdin"], root, stdin_text=feed)
    rows = []
    for rec in (r for r in out.split("\0") if r):
        parts = rec.split("\x1f", 3)
        if len(parts) != 4:
            raise FailClosed("unexpected git log output: the published commit set cannot be "
                             "established")
        full, short, parent_field, message = parts
        parents = parent_field.split()
        if not _SHA_RE.match(full) or not short or not all(_SHA_RE.match(p) for p in parents):
            raise FailClosed("unexpected git log output: the published commit set cannot be "
                             "established")
        rows.append((full, short, parents, message))
    return rows


def pre_push_channels(repo, base, body, title, head=None, branch=None, remote="origin"):
    """Build the pre-push channel list over the repository at repo: the composed title+body (one
    blob, the CI rule), the message of every commit the push would publish, every changelog.toml
    string each of those commits adds against its parent or parents, and the branch name. head and
    branch, when given, come from tools/push_branch.py, which resolves both ONCE and pushes exactly
    that commit to exactly that branch, so the scan and the push are bound to the same commit even
    if HEAD moves in between; without them HEAD and the current branch are resolved here (a
    detached HEAD fails closed: the push targets a branch, so without one there is nothing to bind
    the scan to). The SCANNED SET is every commit reachable from the scanned commit and not
    reachable from any object the remote advertises at check time (git ls-remote, never local
    remote-tracking refs), which is what the push sends once _require_push_url_matches has proven
    the advertisement read and the push destination are one repository; an empty set fails closed
    rather than scanning nothing and reading it as clean. base does NOT define the set (a commit below it
    the remote lacks is published and scanned all the same); it stays as a cross-check and must be
    a proper ancestor of the scanned commit. The changelog delta is taken PER COMMIT in the set, so
    every intermediate state the push publishes is scanned, including one later removed again."""
    if head is None:
        head = _run_git(["rev-parse", "--verify", "HEAD^{commit}"], repo).strip()
        if not _SHA_RE.match(head):
            raise FailClosed("cannot resolve HEAD to a canonical 40-hex SHA")
    else:
        resolved = _run_git(["rev-parse", "--verify", head + "^{commit}"], repo).strip()
        if resolved != head:
            raise FailClosed("--head does not name a commit in this repository")
    if branch is None:
        branch = _run_git(["symbolic-ref", "--short", "HEAD"], repo).strip()
        if not branch:
            raise FailClosed("cannot establish the current branch (detached HEAD)")
    if base == head:
        raise FailClosed("--base equals the scanned commit: the push would add no commits, and an "
                         "empty range is never read as clean")
    try:
        _run_git(["merge-base", "--is-ancestor", base, head], repo)
    except FailClosed:
        raise FailClosed("--base is not a proper ancestor of the scanned commit (a cross-check of "
                         "the caller's intent; when the default branch has advanced past the "
                         "branch point, pass the merge-base, from git merge-base <default-branch> "
                         "HEAD, not the advanced tip)")
    _require_push_url_matches(remote, repo)
    rows = _published_commits(head, _remote_advertised_shas(remote, repo), repo)
    if not rows:
        raise FailClosed("the remote already has every commit reachable from the scanned commit: "
                         "the push would publish no new commits, and scanning nothing is never "
                         "read as clean")
    channels = [("commit {}".format(short), message) for _full, short, _parents, message in rows]
    channels.append(("PR body file", (title + "\n\n" + body) if title is not None else body))
    texts = {}

    def changelog_at(sha):
        if sha not in texts:
            texts[sha] = _rev_file_text(sha, "changelog.toml", repo)
        return texts[sha]

    for full, short, parents, _message in rows:
        added = _changelog_added_at(short, changelog_at(full),
                                    [changelog_at(parent) for parent in parents])
        for number, item in enumerate(added, 1):
            channels.append(("commit {} changelog string added #{}".format(short, number), item))
    channels.append(("branch name", branch))
    return channels


def parse_pre_push_argv(argv):
    """Strict flag parsing for --pre-push: exactly --repo, --body-file, --base and optionally
    --title, --head, --branch and --remote, each with one value, each at most once. Anything else
    fails closed (an unknown flag silently skipped could drop a channel)."""
    opts = {"repo": None, "body-file": None, "base": None, "title": None, "head": None,
            "branch": None, "remote": None}
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


def require_head(value):
    """The explicit --head, the exact commit the helper will push: the same canonical rule as
    --base, for the same reason."""
    if not isinstance(value, str) or not _SHA_RE.match(value) or value == _ZERO_SHA:
        raise FailClosed("--head is not a canonical non-zero 40-hex SHA")
    return value


def run_pre_push(argv):
    opts = parse_pre_push_argv(argv)
    if not os.path.isabs(opts["repo"]) or not os.path.isdir(opts["repo"]):
        raise FailClosed("--repo must be an absolute path to an existing directory")
    repo = Path(opts["repo"])
    toplevel = _run_git(["rev-parse", "--show-toplevel"], repo).strip()
    if not toplevel or os.path.realpath(toplevel) != os.path.realpath(str(repo)):
        raise FailClosed("--repo must be the repository root: from a subdirectory the changelog "
                         "would read as absent (and therefore clean) under ls-tree's "
                         "working-directory-relative paths")
    base = require_base(opts["base"])
    head = require_head(opts["head"]) if opts["head"] is not None else None
    branch = opts["branch"]
    if branch is not None and not branch.strip():
        raise FailClosed("--branch, when given, must be non-empty")
    title = opts["title"]
    if title is not None and not title.strip():
        raise FailClosed("--title, when given, must be non-empty")
    if opts["remote"] is not None and not opts["remote"].strip():
        raise FailClosed("--remote, when given, must be non-empty")
    remote = opts["remote"] if opts["remote"] is not None else "origin"
    body, body_digest = read_body_file(opts["body-file"])
    hashes, maxn = require_denylist(repo)
    findings = all_findings(pre_push_channels(repo, base, body, title, head, branch, remote),
                            "pre-push", hashes, maxn)
    if findings:
        print("FAIL: {} possible finding(s) in pre-push channels".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("body sha256: {}".format(body_digest))
    print(_SCOPE.format(phase="pre-push", scanned=_SCANNED["pre-push"]))
    return 0


_SCANNED = {
    "pre-merge": "the commit messages in base..head, the composed PR title+body, and the PR head ref",
    "post-merge": "the messages of the newly landed commits (before..after)",
    "pre-push": "the PR body file (composed with any --title), the message of every commit the "
                "push would publish (reachable from the pushed commit, absent from every ref the "
                "remote advertises at check time), every string each published commit adds to the "
                "parsed changelog.toml against its parent or parents (keys and values; TOML "
                "comments are not parsed, so not scanned), and the branch name",
}
_SCOPE = ("PASS ({phase}): scanned STRUCTURAL patterns and the hashed codename denylist, plus (pre-merge "
          "and pre-push only) the narrow AI-attribution patterns (a *-by: trailer naming an AI, a "
          "generated/written/drafted with/by/using phrase naming an AI, an AI vendor address), across "
          "{scanned}. NOT CHECKED here: file contents (check_leaks + gitleaks own those), "
          "reviews/comments/issues, paraphrased attribution, AI names outside the shared identity set, "
          "attribution split across lines, other branches/tags. The CI phases detect accidental "
          "reintroduction in text push has already made public (not an exfiltration control; they cannot "
          "un-expose pushed text); only the pre-push mode runs before publication, and only over the "
          "channels it is handed.")


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
    if argv == ["--self-test"]:        # honoured only as the sole argument, never mixed into a real run
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
    self-test failure, never a silent pass. The call must be exactly one positional string constant
    with no other arguments: a flags argument would change matching while leaving the pattern
    strings equal, so any other call shape on the HOOK's side fails the parity test instead of
    passing it. This inspects the hook only; the GATE's own compile call is checked at run time in
    the self-test, which compares the compiled regex's pattern and flags against a bare compile of
    that pattern, so a flags argument on either side fails the parity test."""
    import ast
    source = (ROOT / ".aiqt" / "core" / "hooks" / "scripts" / "aiqt_hooks.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "AI_IDENTITY_RE"
                and isinstance(node.value, ast.Call)):
            if (len(node.value.args) != 1 or node.value.keywords
                    or not isinstance(node.value.args[0], ast.Constant)
                    or not isinstance(node.value.args[0].value, str)):
                raise AssertionError("the hook's AI_IDENTITY_RE is no longer a single-string compile "
                                     "call; pattern-string parity alone cannot prove equal matching")
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
    # 12. attribution narrowness: product mentions, quoted keys, human or lookalike values, longer
    # domains that merely begin with a vendor's, and line-split attribution all stay silent
    for quiet in ("as a Skill you upload to Claude or a portable instruction file for other "
                  "assistants (ChatGPT, Copilot, Gemini)",
                  "fix the Claude adapter's matcher",
                  "`Co-authored-by:` trailers naming an AI are refused",
                  "Docs: the line Co-authored-by: Claude is refused at commit time",  # key not at start
                  "Co-authored-by: Jane Doe <jane@example.org>",
                  "Co-authored-by: Claudette <c@example.org>",                        # word boundary
                  "benchmarks against gpt-4o show parity",
                  "Write to ops@anthropic.community for access",     # the domain must END at .com
                  "Generated with\nClaude Code",     # line-split attribution: a disclosed residual
                  ""):
        _expect(scan_attribution([("ch", quiet)]) == [], quiet)
    # 12b. documented precision residuals: a bounded textual window and a trailer value scan cannot
    # read intent, so an authorship phrase followed inside the window by an unrelated AI name, and a
    # trailer whose value merely mentions an AI name, fire as findings. Each is a disclosed false
    # positive, asserted here so a change to this behavior is a deliberate one.
    for text, kind in (
            ("Code generated by the protobuf compiler, used by the Claude adapter.",
             "generated-with phrase"),
            ("Fixtures generated using the OpenAI-compatible endpoint of our own server.",
             "generated-with phrase"),
            ("The checklist was written by a copilot of the flight crew.",
             "generated-with phrase"),
            ("Reported-by: Jane Doe, who maintains the Gemini adapter", "trailer")):
        _expect(scan_attribution([("ch", text)]) == ["ch: AI attribution ({})".format(kind)], text)
    # 13. phrase-window boundaries: the identity is matched against the ORIGINAL line and the match
    # must BEGIN inside the window (the window's last position is tested just inside, exactly at,
    # and just outside); a match beginning inside may run past the edge and still fires, because a
    # longer gpt- form (a dated model id) is itself an AI identity; and a longer word that merely
    # begins with an identity ("claudette") never matches at all: the pattern's word boundary
    # decides, not a slice. The filler is non-word so only the window decides.
    fires = ["ch: AI attribution (generated-with phrase)"]
    inside = "generated with" + "." * (_AI_PHRASE_WINDOW - 6) + "claude"    # begins well inside
    _expect(len(inside) - len("generated with") == _AI_PHRASE_WINDOW, "fixture arithmetic")
    _expect(scan_attribution([("ch", inside)]) == fires, inside)
    last_in = "generated with" + "." * (_AI_PHRASE_WINDOW - 1) + "claude"   # begins at the last
    _expect(scan_attribution([("ch", last_in)]) == fires, last_in)          # position inside
    straddle = "generated with" + "." * (_AI_PHRASE_WINDOW - 5) + "claude"  # begins inside, runs
    _expect(scan_attribution([("ch", straddle)]) == fires, straddle)        # one past the edge
    outside = "generated with" + "." * _AI_PHRASE_WINDOW + "claude"         # begins at the first
    _expect(scan_attribution([("ch", outside)]) == [], outside)             # position outside
    past = "generated with" + "." * (_AI_PHRASE_WINDOW + 1) + "claude"      # begins further out
    _expect(scan_attribution([("ch", past)]) == [], past)
    cut = "generated with" + "." * (_AI_PHRASE_WINDOW - 6) + "claudette"    # no word boundary:
    _expect(scan_attribution([("ch", cut)]) == [], cut)                     # never an identity
    dated = "generated with" + "." * (_AI_PHRASE_WINDOW - 5) + "gpt-4o-2024-08-06"
    _expect(scan_attribution([("ch", dated)]) == fires, dated)   # a dated model id begins inside
    _expect(scan_attribution([("ch", "Generated using the in-house summarisation wrapper around "
                                     "the gpt-4o-2024-08-06 model")]) == fires,
            "a dated gpt- model id past an end-inside window must still fire")
    # overlapping candidates: the leftmost match (a long gpt- form) begins inside the window and
    # fires even though it ends far outside; an end-inside rule read this line as silent while the
    # shorter identity ending inside was unreachable, because a search returns only the leftmost
    # of overlapping matches
    overlap = "generated with " + "." * 40 + "gpt-4.claude-" + "a" * 30
    _expect(scan_attribution([("ch", overlap)]) == fires, overlap)
    # 14. the phase wiring: attribution runs pre-merge and pre-push, never post-merge
    ch = [("commit abc1234", "Co-authored-by: Claude <c@example.org>")]
    _expect(all_findings(ch, "pre-merge", hashes, maxn) == ["commit abc1234: AI attribution (trailer)"])
    _expect(all_findings(ch, "pre-push", hashes, maxn) == ["commit abc1234: AI attribution (trailer)"])
    _expect(all_findings(ch, "post-merge", hashes, maxn) == [])
    # 15. the identity set: two copies kept byte-identical by this parity test (the gate's pattern
    # against the hook's, extracted from the hook's source), checked on BOTH sides: the extractor
    # constrains the hook's compile-call shape, and the gate's own compiled regex must carry the
    # published pattern and the flags a bare compile of it yields, so a flags argument added on
    # either side fails here instead of changing matching while the pattern strings stay equal
    _expect(_hook_identity_pattern() == AI_IDENTITY_PATTERN,
            "AI_IDENTITY_PATTERN drifted from the commit-identity hook's AI_IDENTITY_RE")
    _expect(AI_IDENTITY_RE.pattern == AI_IDENTITY_PATTERN,
            "the gate's compiled AI_IDENTITY_RE no longer carries AI_IDENTITY_PATTERN")
    _expect(AI_IDENTITY_RE.flags == re.compile(AI_IDENTITY_PATTERN).flags,
            "a flags argument on the gate's own compile call changes matching while the pattern "
            "strings stay equal")
    # 16. changelog delta: a multiset difference over EVERY string in the parsed document (keys and
    # values), fail-closed on parse/shape and on a changelog deleted at head
    base_toml = 'title = "t"\n[[release]]\nitems = ["kept item"]\n'
    head_toml = 'title = "t"\n[[release]]\nitems = ["kept item", "new: drafted using ChatGPT"]\n'
    added = changelog_added_items(base_toml, head_toml)
    _expect(added == ["new: drafted using ChatGPT"], added)
    _expect(scan_attribution([("changelog string added #1", added[0])])
            == ["changelog string added #1: AI attribution (generated-with phrase)"])
    _expect(changelog_added_items(head_toml, base_toml) == [])      # a removal adds nothing
    # a misspelled items key is still scanned: the key and its string value are both additions
    mis_head = base_toml + 'item = "notes drafted using ChatGPT"\n'
    _expect(changelog_added_items(base_toml, mis_head) == ["item", "notes drafted using ChatGPT"])
    # a release field other than items (a second release's title here) is scanned too
    title_head = base_toml + '[[release]]\ntitle = "alpha bravo drafted using ChatGPT"\n'
    added = changelog_added_items(base_toml, title_head)
    _expect(added == ["alpha bravo drafted using ChatGPT", "title"], added)
    _expect(scan_channels([("changelog string added #1", added[0])], hashes, maxn)
            == ["changelog string added #1: internal codename (hash match)"])
    _expect(changelog_added_items(None, base_toml)                  # file new at head: all strings added
            == ["items", "kept item", "release", "t", "title"])
    _expect(changelog_added_items(None, None) == [])                # a repository without a changelog
    try:
        changelog_added_items(base_toml, None)                      # deleted at head: never clean
        raise AssertionError("expected FailClosed for a changelog deleted at head")
    except FailClosed:
        pass
    dup_head = 'title = "t"\n[[release]]\nitems = ["kept item", "kept item"]\n'
    _expect(changelog_added_items(base_toml, dup_head) == ["kept item"], "multiset, not set")
    # 16b. the per-commit walk the pre-push mode uses over every published commit: an addition at
    # any intermediate state is that commit's, a merge adds only what NO parent carried, a root
    # commit adds its whole document, a commit with no changelog below parents without one adds
    # nothing, and a changelog deleted at any commit (a merge included) fails closed
    added = _changelog_added_at("abc1234", head_toml, [base_toml])
    _expect(added == ["new: drafted using ChatGPT"], added)
    _expect(_changelog_added_at("abc1234", base_toml, [base_toml, None]) == [],
            "a merge carrying one parent's changelog unchanged adds nothing")
    merged = 'title = "t"\n[[release]]\nitems = ["kept item", "merge only"]\n'
    _expect(_changelog_added_at("abc1234", merged, [base_toml, head_toml]) == ["merge only"],
            "a merge adds only the strings no parent carried")
    _expect(_changelog_added_at("abc1234", base_toml, [])
            == ["items", "kept item", "release", "t", "title"],
            "a root commit's whole changelog is an addition")
    _expect(_changelog_added_at("abc1234", None, []) == [], "no changelog anywhere: nothing added")
    for text, parent_texts in ((None, [base_toml]), (None, [None, base_toml]),
                               ("not toml [", [base_toml]), (base_toml, ["not toml ["])):
        try:
            _changelog_added_at("abc1234", text, parent_texts)
            raise AssertionError("expected FailClosed for per-commit changelog {!r}/{!r}"
                                 .format(text, parent_texts))
        except FailClosed:
            pass
    for bad_base, bad_head in ((base_toml, "not toml ["), ("= bad", head_toml),
                               (base_toml, 'release = 5\n'),
                               (base_toml, '[[release]]\nitems = "str"\n'),
                               (base_toml, '[[release]]\nitems = [5]\n')):
        try:
            changelog_added_items(bad_base, bad_head)
            raise AssertionError("expected FailClosed for changelog {!r}/{!r}".format(bad_base, bad_head))
        except FailClosed:
            pass
    # 17. the body file: fail-closed set, the size bound just inside / at / just over, and the
    # digest of the exact bytes read returned beside the text
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        good = root / "body.md"
        good.write_text("hello", encoding="utf-8")
        text, digest = read_body_file(str(good))
        _expect(text == "hello" and digest == hashlib.sha256(b"hello").hexdigest(), digest)
        (root / "empty.md").write_bytes(b"")
        text, digest = read_body_file(str(root / "empty.md"))      # empty body is a valid empty text
        _expect(text == "" and digest == hashlib.sha256(b"").hexdigest(), digest)
        (root / "inside.md").write_bytes(b"a" * (MAX_BODY_BYTES - 1))
        (root / "at.md").write_bytes(b"a" * MAX_BODY_BYTES)
        _expect(len(read_body_file(str(root / "inside.md"))[0]) == MAX_BODY_BYTES - 1)
        _expect(len(read_body_file(str(root / "at.md"))[0]) == MAX_BODY_BYTES)
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
    # 18. --pre-push argv and --base/--head validation fail closed on every malformed shape
    parsed = parse_pre_push_argv(["--repo", "/r", "--body-file", "/b", "--base", forty])
    _expect(parsed == {"repo": "/r", "body-file": "/b", "base": forty, "title": None,
                       "head": None, "branch": None, "remote": None}, parsed)
    parsed = parse_pre_push_argv(["--repo", "/r", "--body-file", "/b", "--base", forty,
                                  "--head", forty, "--branch", "feat/x", "--remote", "upstream"])
    _expect(parsed["head"] == forty and parsed["branch"] == "feat/x"
            and parsed["remote"] == "upstream", parsed)
    for argv in (["--repo", "/r"],                                   # missing required flags
                 ["--repo", "/r", "--body-file", "/b", "--base", forty, "--bogus", "x"],
                 ["--repo", "/r", "--repo", "/r2", "--body-file", "/b", "--base", forty],
                 ["--repo", "/r", "--body-file", "/b", "--base", forty,
                  "--head", forty, "--head", forty],                 # repeated optional flag
                 ["--repo", "/r", "--body-file", "/b", "--base"],    # valueless flag
                 ["positional"]):
        try:
            parse_pre_push_argv(argv)
            raise AssertionError("expected FailClosed for argv {!r}".format(argv))
        except FailClosed:
            pass
    _expect(require_base(forty) == forty)
    _expect(require_head(forty) == forty)
    for bad in ("HEAD", "a" * 39, "a" * 41, "A" * 40, _ZERO_SHA, 5, None, ""):
        try:
            require_base(bad)
            raise AssertionError("expected FailClosed for base {!r}".format(bad))
        except FailClosed:
            pass
        try:
            require_head(bad)
            raise AssertionError("expected FailClosed for head {!r}".format(bad))
        except FailClosed:
            pass
    # 19. --self-test is honoured only as the sole argument: mixed into a --pre-push invocation it
    # is an unknown argument and the run fails closed (its stderr line is captured and asserted)
    import contextlib
    import io
    sink = io.StringIO()
    with contextlib.redirect_stderr(sink):
        mixed = main(["--pre-push", "--self-test"])
    _expect(mixed == 2,
            "--self-test mixed into a production invocation must fail closed, never run fixtures")
    _expect("fail-closed" in sink.getvalue(), "the mixed invocation must report fail-closed")
    print("SELF-TEST PASS: scanner reuse, composed title/body boundary catch, no-echo, and leak-allow "
          "ignored; the fail-closed paths hold (malformed PR fields, empty title/ref, a non-40-hex "
          "base/head/before/after, a branch-creation push, an unknown event kind); a branch-deletion push "
          "yields no channels; malformed denylist metadata (maxn 0, duplicate, over-cap, oversized, "
          "trailing tokens) is rejected; a non-JSON NaN constant fails closed; the attribution layer "
          "fires on trailers, bounded generated-with phrases (a dated model id beginning inside the "
          "window included, however far its match runs) and vendor addresses, stays silent on bare "
          "mentions, longer vendor-lookalike domains, words without an identity boundary and "
          "identities beginning past the window, carries its documented false-positive cases, runs "
          "pre-merge and pre-push only, and matches the hook's identity set (two copies under a parity "
          "test, with the compiled pattern and flags checked on the gate's side too); the changelog "
          "delta covers every parsed string, per published commit against each of its parents, and "
          "fails closed on any deletion; the pre-push inputs (argv, --base, --head, --remote, body "
          "file, changelog delta) each fail closed when malformed; and --self-test mixed into a "
          "production invocation is rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
