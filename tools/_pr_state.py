#!/usr/bin/env python3
"""Pull request lifecycle reader for tools/check_pending_pr_claims.py (imported, not run on its own).

It reads the lifecycle of named pull requests directly from the GitHub REST API through the gh CLI,
one argument-list subprocess per pull request:

  gh api --hostname github.com -H "Accept: application/vnd.github+json" repos/OWNER/REPO/pulls/N

and classifies each response as OPEN, MERGED, CLOSED or UNKNOWN (cannot evaluate). Only a single
JSON object whose fields validate strictly earns a lifecycle:

  number      exactly the requested positive integer (a boolean is refused)
  base        base.repo.full_name names the requested OWNER/REPO (compared case-insensitively,
              as GitHub names are)
  html_url    exactly https://github.com/OWNER/REPO/pull/N (case-insensitive in OWNER/REPO)
  state       exactly "open" or "closed"
  merged_at   present, and null or a UTC timestamp YYYY-MM-DDTHH:MM:SSZ that parses
  merged      when present, a boolean that agrees with merged_at

  open + null merged_at -> OPEN; closed + timestamp -> MERGED; closed + null -> CLOSED; anything
  missing, contradictory (open with a merge time), mismatched or malformed -> UNKNOWN.

A missing gh, a launch failure, a nonzero exit (401, 403 and 404 included: a 404 never establishes
closure), a signal termination, a timeout, output that is not one JSON object, and an exhausted
request or deadline bound are all UNKNOWN, never a lifecycle. Lifecycle is never inferred from
commit subjects, changelog references, open-list omission, CI results or mergeability.

The default repository of a record surface is derived from the checkout's origin remote, read with
`git remote get-url --all origin`, accepted only in the strict forms tools/ci-status.sh accepts:
https://github.com/OWNER/REPO, ssh://git@github.com/OWNER/REPO and git@github.com:OWNER/REPO, each
optionally ending in .git. Unlike ci-status.sh, this reader pins the API host: it passes --hostname
github.com and drops GH_HOST and GH_REPO from the child environment.

Bounds: at most MAX_PRS unique pull requests per invocation, CALL_TIMEOUT seconds per call and
DEADLINE seconds overall, with one retry of a transient failure (a timeout, or a gh error naming an
HTTP 5xx status) inside the deadline. Reads are deduplicated by (host, repository, number) and never
cached across invocations. Diagnostics never echo gh's stderr or the origin URL (either may carry
credentials); they name the repository, number, classification and retrieval time only.

RESIDUALS (disclosed): gh's own handling of --hostname, GH_ENTERPRISE_TOKEN and its configuration is
gh's behaviour and is not verified here; the reader trusts the response of the host it asked. A pull
request's state can change after it is read (a closed one can be reopened). The origin is read as git
config resolves it, so a url.<base>.insteadOf rewrite can map it to another accepted URL.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _pr_state.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import datetime
import json
import os
import re
import subprocess
import time

HOST = "github.com"
OPEN, MERGED, CLOSED, UNKNOWN = "OPEN", "MERGED", "CLOSED", "UNKNOWN"
MAX_PRS = 100
CALL_TIMEOUT = 10
DEADLINE = 120
# The character classes are spelled out, never ranges, so the match does not depend on the locale
# (the same posture as tools/ci-status.sh).
_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
OWNER_RE = re.compile("[{0}](?:[{0}-]{{0,37}}[{0}])?".format(_ALNUM))
REPO_RE = re.compile("[{0}._-]{{1,100}}".format(_ALNUM))
URL_PREFIXES = ("https://github.com/", "ssh://git@github.com/", "git@github.com:")
TIMESTAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
TRANSIENT_RE = re.compile(r"HTTP 5[0-9][0-9]")
# Variables that could steer gh to another host or repository; dropped from the child environment.
DROPPED_ENV = ("GH_HOST", "GH_REPO")


def valid_repo(text):
    """True when text is OWNER/REPO in the strict GitHub name grammar."""
    if not isinstance(text, str) or text.count("/") != 1:
        return False
    owner, name = text.split("/")
    return (OWNER_RE.fullmatch(owner) is not None and REPO_RE.fullmatch(name) is not None
            and name not in (".", ".."))


def repo_from_url(url):
    """OWNER/REPO for an origin URL in an accepted form, else None."""
    for prefix in URL_PREFIXES:
        if url.startswith(prefix):
            path = url[len(prefix):]
            if path.endswith(".git"):
                path = path[:-4]
            return path if valid_repo(path) else None
    return None


def run_git_origin(root):
    """(returncode, stdout bytes) of `git remote get-url --all origin` in root; OSError propagates."""
    child = subprocess.run(["git", "-C", str(root), "remote", "get-url", "--all", "origin"],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=CALL_TIMEOUT)
    return child.returncode, child.stdout


def origin_repo(root, git_runner=run_git_origin):
    """(OWNER/REPO, None) from the checkout's origin remote, or (None, reason). The URL itself is never
    echoed: it may carry credentials."""
    try:
        code, out = git_runner(root)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "git could not be run to read the origin remote ({})".format(type(exc).__name__)
    if code == 2 or (code == 0 and not out):
        return None, "this checkout has no origin remote, or it has no URL (no default repository is assumed)"
    if code != 0:
        return None, "git remote get-url exited {}".format(code)
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError:
        return None, "the origin remote URL is not UTF-8"
    if text.endswith("\n"):
        text = text[:-1]
    if "\n" in text or "\r" in text:
        return None, "the origin remote has more than one URL, or a URL containing a line break"
    repo = repo_from_url(text)
    if repo is None:
        return None, ("the origin remote URL is not one of https://github.com/OWNER/REPO, "
                      "ssh://git@github.com/OWNER/REPO or git@github.com:OWNER/REPO (each optionally "
                      "ending in .git)")
    return repo, None


def _parse_timestamp(value):
    if not isinstance(value, str) or TIMESTAMP_RE.fullmatch(value) is None:
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _reject_duplicate_keys(pairs):
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key in response object")
    return dict(pairs)


def _reject_constant(name):
    raise ValueError("non-standard JSON constant {}".format(name))


def classify(raw, repo, number):
    """(state, reason) for the raw bytes of one pulls/N response; reason is None for a lifecycle."""
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
                         parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        return UNKNOWN, "response is not one JSON object ({})".format(type(exc).__name__)
    if not isinstance(obj, dict):
        return UNKNOWN, "response is not a JSON object"
    got = obj.get("number")
    if type(got) is not int or got != number:
        return UNKNOWN, "response number {!r} is not the requested {}".format(got, number)
    base = obj.get("base")
    base_repo = base.get("repo") if isinstance(base, dict) else None
    full_name = base_repo.get("full_name") if isinstance(base_repo, dict) else None
    if not isinstance(full_name, str) or full_name.casefold() != repo.casefold():
        return UNKNOWN, "response base repository {!r} is not {}".format(full_name, repo)
    html_url = obj.get("html_url")
    expected_url = "https://{}/{}/pull/{}".format(HOST, repo, number)
    if not isinstance(html_url, str) or html_url.casefold() != expected_url.casefold():
        return UNKNOWN, "response html_url is not {}".format(expected_url)
    state = obj.get("state")
    if state not in ("open", "closed") or type(state) is not str:
        return UNKNOWN, "response state {!r} is neither open nor closed".format(state)
    if "merged_at" not in obj:
        return UNKNOWN, "response has no merged_at field"
    merged_at = obj["merged_at"]
    if merged_at is not None and _parse_timestamp(merged_at) is None:
        return UNKNOWN, "response merged_at {!r} is not a UTC timestamp".format(merged_at)
    if "merged" in obj:
        merged = obj["merged"]
        if type(merged) is not bool or merged != (merged_at is not None):
            return UNKNOWN, "response merged {!r} contradicts merged_at".format(merged)
    if state == "open":
        if merged_at is not None:
            return UNKNOWN, "response is open but carries a merge time"
        return OPEN, None
    return (MERGED, None) if merged_at is not None else (CLOSED, None)


class GhTransport:
    """The production transport: one gh api child per call, argument list, no shell, host pinned. gh
    is found on the child environment's PATH; search_path replaces that PATH (the self-test points it
    at a stub gh)."""

    def __init__(self, search_path=None):
        self.search_path = search_path

    def __call__(self, repo, number, timeout):
        """(returncode, stdout bytes, stderr bytes); OSError (gh missing included) and
        subprocess.TimeoutExpired propagate."""
        env = {key: value for key, value in os.environ.items() if key not in DROPPED_ENV}
        if self.search_path is not None:
            env["PATH"] = self.search_path
        child = subprocess.run(
            ["gh", "api", "--hostname", HOST, "-H", "Accept: application/vnd.github+json",
             "repos/{}/pulls/{}".format(repo, number)],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, env=env)
        return child.returncode, child.stdout, child.stderr


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_states(keys, transport=None, max_prs=MAX_PRS, call_timeout=CALL_TIMEOUT, deadline=DEADLINE,
                clock=time.monotonic):
    """{(repo, number): (state, reason, retrieved_at)} for each unique (repo, number) in keys, read once
    each in first-seen order. Every key gets an entry; one past a bound is UNKNOWN with the bound named."""
    transport = transport or GhTransport()
    results = {}
    start = clock()
    reads = 0
    for key in keys:
        if key in results:
            continue
        repo, number = key
        if not valid_repo(repo) or type(number) is not int or number < 1:
            results[key] = (UNKNOWN, "not a valid repository and pull request number", None)
            continue
        if reads >= max_prs:
            results[key] = (UNKNOWN, "request limit of {} pull requests reached; not read".format(max_prs),
                            None)
            continue
        reads += 1
        results[key] = _read_one(transport, repo, number, call_timeout, deadline, start, clock)
    return results


def _read_one(transport, repo, number, call_timeout, deadline, start, clock):
    reason = None
    for _attempt in range(2):
        remaining = deadline - (clock() - start)
        if remaining <= 0:
            return UNKNOWN, "overall deadline of {} s reached; not read".format(deadline), None
        try:
            code, out, err = transport(repo, number, min(call_timeout, remaining))
        except subprocess.TimeoutExpired:
            reason = "gh timed out"
            continue
        except OSError as exc:
            return UNKNOWN, "gh could not be launched ({})".format(type(exc).__name__), None
        retrieved = _utc_now()
        if code < 0:
            return UNKNOWN, "gh was ended by signal {}".format(-code), retrieved
        if code != 0:
            text = err.decode("utf-8", "replace") if isinstance(err, bytes) else ""
            for status in ("401", "403", "404"):
                if "HTTP " + status in text:
                    return UNKNOWN, "gh exited {} (HTTP {}; not evidence of closure)".format(
                        code, status), retrieved
            reason = "gh exited {}".format(code)
            if TRANSIENT_RE.search(text):
                reason += " (server error)"
                continue
            return UNKNOWN, reason, retrieved
        state, why = classify(out, repo, number)
        return state, why, retrieved
    return UNKNOWN, reason + " after one retry", None
