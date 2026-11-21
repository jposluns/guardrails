#!/usr/bin/env python3
"""The repository-owned merge entry point (merge-pr): land ONE pull request, by squash only, onto the
commit the caller checked.

  merge_pr.py --pr N --head SHA --base main --method squash --expect-base SHA (--train | --not-train)
              [--require-status CONTEXT]
  merge_pr.py --self-test [--execution-report ABS_PATH]
  any other argument list is refused (exit 2) before any read

Exit: 0 merged, every landing property below confirmed after the merge and main's push run for it green;
1 refused (a method other than squash, a base other than main, squash disabled on the hosting service, a
pull request that is not the one named, the RELEASING.md step 6a attestation pull request, a base commit
other than --expect-base, an empty pull request body, a red gate or an API rejection; nothing was
merged), or merged but the post-merge check found a landing property that does not hold, or main red
(one RESULT line per property that fails); 2 cannot evaluate (a usage error, settings or state that cannot
be read or are malformed, an identity mismatch; nothing was merged), or an UNKNOWN merge outcome (the
request was sent and its result could not be read), or merged with main's push run for it not evaluable.

THE LANDING PROPERTIES. What this tool claims about a landing, each checked before the request where it
can be and after it always (the self-test drives a refusal row and its flip for each):
  head     before: the pull request read names --head, and the head commit read is that commit;
           the request pins it (sha). After: the pull request reads back merged at --head.
  base     before: --base is main (tools/ci-status.sh --main-health reads only main), the pull request
           targets it, and the compare read's base commit is --expect-base. After: the pull request
           reads back with base main.
  parent   before: the head is up to date with --expect-base (behind_by 0, that commit the merge base).
           After: the landing commit has exactly one parent, and it is --expect-base.
  tree     before: the head commit's tree is read and recorded. After: the landing commit's tree is it.
  message  before: the squash title is the pull request title and number, "TITLE (#N)", and the squash
           message its body (CRLF line ends as LF, trailing white space dropped; an empty body is refused),
           both the same in the read before the gates and the read after them; the request sends them as
           commit_title and commit_message. After: the landing commit's message is "TITLE (#N)", a blank
           line and that body (trailing line feeds ignored).
  method   before: an explicit --method squash, squash allowed by the settings, asserted again
           immediately before the request. After: the landing commit is a new commit (not the head) with
           one parent, the head's tree and the fixed message; a merge commit or a fast-forward does not
           produce that, and a rebase produces it only when the head's single commit already carried that
           exact message (the hosting service's own record of the method is not read).

THE ATTESTATION PULL REQUEST. RELEASING.md step 6a lands the commit check_release_build.py --post-tag
certified, unchanged, with the tagged commit as its parent; a squash writes a new commit instead. So the
step 6a pull request, the one that appends a [[release]] row to .aiqt/core/releases.toml (any pull request
whose compare read lists that path, as filename or previous_filename, is treated as it), is refused (exit
1, the message points to RELEASING.md step 6a); a compare read whose file list is missing, malformed or
300 or more entries long is cannot-evaluate (exit 2; the hosting service lists at most 300 files, so the
path could be hidden).
Changing step 6a for squash-only landings waits for the maintainer's open decision on squash-only landings
(PD-SQUASH-ONLY-MERGE-METHOD); until it is taken, this tool never lands that pull request.

THE BINDING. Every read and the one action are bound to one validated target, fixed before the first
read and never re-derived: the repository is the one this checkout's origin remote names (read with
`git remote get-url --all origin` in the checkout this file lives in, accepted only as
https://github.com/OWNER/REPO, ssh://git@github.com/OWNER/REPO or git@github.com:OWNER/REPO, each
optionally ending in .git, the same forms tools/ci-status.sh accepts; an absent, unreadable, repeated or
other origin is cannot-evaluate); the API host is github.com, passed to every `gh api` call as
--hostname, so GH_HOST and gh's configured default host select nothing; every endpoint spells the
validated OWNER/REPO, so GH_REPO and gh's {owner}/{repo} placeholders select nothing; the pull request
number, the base branch, the full head commit id and the full expected base commit id are the caller's,
validated as they are consumed (--pr decimal with no leading zero, --head and --expect-base 40 or 64
lowercase hex, --base a plain branch name and then only main, refused otherwise with exit 1). Exactly
one of --train and --not-train is required, so train eligibility is never left out by omission. The
repository read must return that OWNER/REPO as its full_name, and the pull request read that number,
that base branch of that repository, that head commit and a head branch of that same repository; any
other value is a refusal or a cannot-evaluate, never a substitution.

THE POLICY (evaluate_policy, the table the self-test drives). The requested method is judged first,
before any read: only an explicit --method squash is accepted; merge and rebase are refused (exit 1)
whatever the hosting settings say; a missing, repeated or unknown method is a usage refusal (exit 2).
Then the repository read (GET repos/OWNER/REPO) must carry allow_squash_merge, allow_merge_commit and
allow_rebase_merge, each a JSON boolean (a missing field, null, a string, a number or a malformed
response is cannot-evaluate, never a default). Squash disabled: refused (exit 1). Squash enabled and
merge commits or rebase merges enabled too: a WARNING names the repository and each extra method,
states that those methods stay available outside this tool and recommends disabling them in the
repository settings; the squash landing then proceeds. Nothing here changes a hosting setting.

THE SEQUENCE. (1) Bind the target and read the settings; (2) read the pull request and require it open,
not a draft, not merged, at the named head and base, its head branch in this repository (with --train,
also a merge-train-* head branch carrying the merge-train label), and compose the squash title and
message from it; (3) require the head to be up to date with the base (the compare read's behind_by is 0,
its base_commit and merge_base_commit the same full commit id, and that id --expect-base, recorded as
the checked base commit), require the compare's file list not to name the attestation path, and read
the head commit's tree; (4) run the existing gates in this checkout:
`tools/ci-status.sh --main-health` (main must be green) and `tools/ci-status.sh HEAD --require-base
BASE`, plus `--pr N --require-status CONTEXT` when --require-status is given (its MERGE-SHA line must
name the head); (5) read the settings, the pull request, the compare and the head commit again and
require the same decision, the same head, base commit, tree, title and body: a change is a refusal that
asks for fresh checks, never an authorization carried over; (6) assert the method is squash and send
exactly one PUT repos/OWNER/REPO/pulls/N/merge with {"merge_method": "squash", "sha": HEAD,
"commit_title": TITLE, "commit_message": BODY}, so a head that moved after the last read is rejected by
the hosting service itself; (7) require merged true and a full landing commit id, read the pull request
back and the landing commit and check every landing property above, then run `tools/ci-status.sh
--main-health --merge-commit SHA`. The request pins only the head, so a base that advanced between the
last read and the merge is not refused by the hosting service unless its branch protection requires an
up-to-date branch; --expect-base narrows that window to the last read, and this tool detects the rest
after the landing (exit 1, the RESULT line says so) and cannot undo it. A rejection (HTTP 405, 409 or 422 with a message)
is exit 1 with no fallback to another method and no retry; any other failure after the request was sent,
an unexpected error included, is an UNKNOWN outcome (exit 2): reconcile by reading the pull request
before any further landing, never by sending the request again. An unexpected error before the request
is cannot-evaluate (exit 2), never a traceback. Deferred landing (auto-merge, a merge
queue) and an administrator bypass are not offered: no option reaches them.

THE ENFORCEMENT BOUNDARY (printed as the BOUNDARY line of every landing run). This tool makes squash the
only method a landing THROUGH IT can use. It cannot stop anyone with write access, or other automation,
from merging by another method through the hosting service's web interface or API while the
repository settings still allow it; only those settings (squash the one allowed method) and restricting
who can merge close that, and this tool warns while they are wider. Linear-history protection alone does
not tell a squash from a rebase. Through this tool the landing commit message is the pull request's
title, number and body as read before the request; the tool cannot show that a person reviewed that
text, only that the landing carries it.

Disclosed residuals: the origin is read as git configuration resolves it (a url.<base>.insteadOf rewrite
or GIT_CONFIG_* in the environment can map it to another accepted URL, as for a fetch); gh's own
authentication decides which account acts; and the gates run are this checkout's tools/ci-status.sh,
whose own header states what it reads and does not.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: merge_pr.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import io
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI_STATUS_REL = "tools/ci-status.sh"
API_HOST = "github.com"
API_HEADERS = ("Accept: application/vnd.github+json", "X-GitHub-Api-Version: 2022-11-28")
READ_TIMEOUT = 60
GATE_TIMEOUT = 1800
ALLOWED_METHOD = "squash"
REFUSED_METHODS = ("merge", "rebase")
SETTINGS_FIELDS = ("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge")
EXTRA_METHODS = (("allow_merge_commit", "merge commits"), ("allow_rebase_merge", "rebase merges"))
TRAIN_PREFIX = "merge-train-"
TRAIN_LABEL = "merge-train"
ALLOWED_BASE = "main"
ATTESTATION_PATH = ".aiqt/core/releases.toml"
COMPARE_FILE_CAP = 300
REJECTED_HTTP = ("405", "409", "422")
EXIT_OK, EXIT_REFUSED, EXIT_CANNOT = 0, 1, 2
BOUNDARY = ("BOUNDARY: this tool lands a pull request by squash only; it cannot stop a merge by another "
            "method made outside it (the hosting service's web interface or API) while the repository "
            "settings allow that method.")

# The character classes are spelled out, never ranges, so the match does not depend on the locale.
_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
OWNER_RE = re.compile("[{0}](?:[{0}-]{{0,37}}[{0}])?".format(_ALNUM))
REPO_RE = re.compile("[{0}._-]{{1,100}}".format(_ALNUM))
PR_RE = re.compile("[123456789][0123456789]{0,8}")
SHA_RE = re.compile("[0123456789abcdef]{40}|[0123456789abcdef]{64}")
BRANCH_RE = re.compile("[{0}_](?:[{0}._/-]{{0,198}}[{0}_])?".format(_ALNUM))
CONTEXT_RE = re.compile("[{0}][{0}._/:-]{{0,99}}".format(_ALNUM))
HTTP_STATUS_RE = re.compile(r"\(HTTP ([0-9]{3})\)")
ORIGIN_PREFIXES = ("https://github.com/", "ssh://git@github.com/", "git@github.com:")
VALUE_OPTIONS = ("--pr", "--head", "--base", "--method", "--expect-base", "--require-status")
FLAG_OPTIONS = ("--train", "--not-train")


class CannotEvaluate(Exception):
    """A read or input that cannot be judged: nothing is merged, exit 2."""


class Refused(Exception):
    """A judged refusal: nothing is merged, exit 1."""


def _shown(text, limit=200):
    """text as a printable, bounded, quoted string (API and git values are never echoed raw)."""
    shown = json.dumps(str(text), ensure_ascii=True)
    return shown if len(shown) <= limit else shown[:limit] + '..."'


# ---------- the target binding ----------

def repo_from_origin(raw):
    """OWNER/REPO from the raw bytes `git remote get-url --all origin` printed; CannotEvaluate otherwise.
    Exactly one URL, ended by exactly one line feed, in one of the accepted forms."""
    if not isinstance(raw, bytes) or not raw.endswith(b"\n") or raw.count(b"\n") != 1 or raw == b"\n":
        raise CannotEvaluate("the origin remote has no URL, more than one URL, or a URL holding a line "
                             "break, so the repository to merge into is ambiguous")
    try:
        url = raw[:-1].decode("ascii")
    except UnicodeDecodeError:
        raise CannotEvaluate("the origin remote URL is not ASCII") from None
    for prefix in ORIGIN_PREFIXES:
        if url.startswith(prefix):
            path = url[len(prefix):]
            break
    else:
        raise CannotEvaluate("the origin remote URL is not one of https://github.com/OWNER/REPO, "
                             "ssh://git@github.com/OWNER/REPO or git@github.com:OWNER/REPO (each optionally "
                             "ending in .git)")
    if path.endswith(".git"):
        path = path[:-4]
    owner, sep, name = path.partition("/")
    if not sep or not OWNER_RE.fullmatch(owner) or not REPO_RE.fullmatch(name) or name in (".", ".."):
        raise CannotEvaluate("the origin remote URL does not name exactly one OWNER/REPO")
    return owner + "/" + name


def parse_args(argv):
    """{option: value} for a landing argument list; (EXIT_CANNOT or EXIT_REFUSED, message) when refused.
    Every value is validated as it is consumed; a repeated option is refused, never read last-wins."""
    options = {}
    index = 0
    while index < len(argv):
        name = argv[index]
        if name in FLAG_OPTIONS:
            if name in options:
                return EXIT_CANNOT, "{} is given more than once".format(name)
            options[name] = True
            index += 1
            continue
        if name not in VALUE_OPTIONS:
            return EXIT_CANNOT, ("unknown argument {} (only --pr N, --head SHA, --base main, --method squash, "
                                 "--expect-base SHA, --train or --not-train and --require-status CONTEXT are "
                                 "accepted; no option is passed through)".format(_shown(name)))
        if name in options:
            return EXIT_CANNOT, "{} is given more than once; a repeated option is refused".format(name)
        if index + 1 >= len(argv):
            return EXIT_CANNOT, "{} needs a value and none was given".format(name)
        value = argv[index + 1]
        if not value or value.startswith("-"):
            return EXIT_CANNOT, "{} needs a value; an empty or hyphen-led value is refused".format(name)
        options[name] = value
        index += 2
    method = options.get("--method")
    if method is None:
        return EXIT_CANNOT, "an explicit --method squash is required; no default method is assumed"
    if method in REFUSED_METHODS:
        return EXIT_REFUSED, ("REFUSED: merge method {} is refused; this repository lands pull requests by "
                              "squash only".format(method))
    if method != ALLOWED_METHOD:
        return EXIT_CANNOT, "unknown merge method {}; only squash is accepted".format(_shown(method))
    for name, pattern, what in (("--pr", PR_RE, "a pull-request number (decimal, no leading zero)"),
                                ("--head", SHA_RE, "the full head commit id (40 or 64 lowercase hex)"),
                                ("--base", BRANCH_RE, "a plain base branch name"),
                                ("--expect-base", SHA_RE, "the full commit id of the base the head was checked "
                                                          "on (40 or 64 lowercase hex)")):
        if name not in options:
            return EXIT_CANNOT, "{} is required ({})".format(name, what)
        if not pattern.fullmatch(options[name]) or ".." in options[name] or "//" in options[name]:
            return EXIT_CANNOT, "{} needs {}".format(name, what)
    if ("--train" in options) == ("--not-train" in options):
        return EXIT_CANNOT, ("exactly one of --train and --not-train is required; train eligibility is never "
                             "assumed or skipped by omission")
    if "--require-status" in options and not CONTEXT_RE.fullmatch(options["--require-status"]):
        return EXIT_CANNOT, "--require-status needs a status context name"
    if options["--base"] != ALLOWED_BASE:
        return EXIT_REFUSED, ("REFUSED: base {} is refused; this tool lands only into {} (tools/ci-status.sh "
                              "--main-health reads only main's push runs)".format(_shown(options["--base"]),
                                                                                  ALLOWED_BASE))
    return options


# ---------- the JSON reads ----------

def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key {}".format(_shown(key)))
        seen[key] = value
    return seen


def _json(text, what):
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates)
    except (ValueError, TypeError, RecursionError) as exc:
        raise CannotEvaluate("{}: the response is not valid JSON ({})".format(what, exc)) from None


def read_json(api, endpoint, what):
    """The JSON object a GET of endpoint returns; CannotEvaluate on a failed, timed-out or malformed read."""
    try:
        rc, out, _err = api.get(endpoint)
    except subprocess.TimeoutExpired:
        raise CannotEvaluate("{}: the read timed out".format(what)) from None
    except OSError as exc:
        raise CannotEvaluate("{}: the read could not start ({})".format(what, type(exc).__name__)) from None
    if rc != 0:
        raise CannotEvaluate("{}: the read failed (gh exit {}); authentication, permission, rate limit or "
                             "network, it is not judged".format(what, rc))
    doc = _json(out, what)
    if not isinstance(doc, dict):
        raise CannotEvaluate("{}: the response is not a JSON object".format(what))
    return doc


def evaluate_policy(method, settings, repo):
    """(verdict, extras, message) for the requested method and the repository read: verdict is permit,
    warn, refuse or cannot; extras the enabled methods beyond squash. The method is judged first."""
    if method != ALLOWED_METHOD:
        return "refuse", (), "merge method {} is refused; only squash is accepted".format(_shown(method))
    if not isinstance(settings, dict):
        return "cannot", (), "the repository settings are not a JSON object"
    if settings.get("full_name") != repo:
        return "cannot", (), ("the repository read names {}, not {}; refusing to judge another repository's "
                              "settings".format(_shown(settings.get("full_name")), repo))
    for field in SETTINGS_FIELDS:
        if field not in settings:
            return "cannot", (), "the repository settings carry no {} field".format(field)
        if type(settings[field]) is not bool:
            return "cannot", (), "the repository setting {} is not a JSON boolean ({})".format(
                field, _shown(settings[field], 40))
    if settings["allow_squash_merge"] is not True:
        return "refuse", (), "squash merging is disabled for {}, so no landing is possible".format(repo)
    extras = tuple(label for field, label in EXTRA_METHODS if settings[field] is True)
    if extras:
        return "warn", extras, (
            "WARNING: {} also allows {}. Those methods stay available to anyone merging outside this tool; "
            "disable them in the repository settings so squash is the only allowed method. This landing "
            "proceeds by squash.".format(repo, " and ".join(extras)))
    return "permit", (), "squash is the only merge method {} allows".format(repo)


def judge_pr(pr, repo, opts):
    """None when the pull request read is exactly the named one; Refused or CannotEvaluate otherwise."""
    number = int(opts["--pr"])
    try:
        got_number = pr["number"]
        state, draft, merged = pr["state"], pr["draft"], pr["merged"]
        base, head = pr["base"], pr["head"]
        base_ref, base_repo = base["ref"], base["repo"]["full_name"]
        head_sha, head_ref = head["sha"], head["ref"]
        head_repo = head["repo"]["full_name"] if head["repo"] is not None else None
        labels = [label["name"] for label in pr["labels"]]
    except (KeyError, TypeError):
        raise CannotEvaluate("the pull request read is missing a field it must carry") from None
    if type(got_number) is not int or got_number != number:
        raise CannotEvaluate("the pull request read names another pull request")
    for value in (state, base_ref, base_repo, head_sha, head_ref) + tuple(labels):
        if not isinstance(value, str):
            raise CannotEvaluate("the pull request read carries a malformed field")
    if type(draft) is not bool or type(merged) is not bool:
        raise CannotEvaluate("the pull request read carries a malformed draft or merged field")
    if base_repo != repo:
        raise CannotEvaluate("the pull request's base repository is {}, not {}".format(_shown(base_repo), repo))
    if merged or state != "open":
        raise Refused("pull request #{} is not open (state {}, merged {})".format(number, _shown(state), merged))
    if draft:
        raise Refused("pull request #{} is a draft".format(number))
    if base_ref != opts["--base"]:
        raise Refused("pull request #{} targets {}, not {}".format(number, _shown(base_ref), opts["--base"]))
    if head_sha != opts["--head"]:
        raise Refused("pull request #{} is at head {}, not the checked head {}; run the checks on the new "
                      "head".format(number, _shown(head_sha), opts["--head"]))
    if head_repo != repo:
        raise Refused("pull request #{}'s head branch is not in {}".format(number, repo))
    if opts.get("--train") and (not head_ref.startswith(TRAIN_PREFIX) or TRAIN_LABEL not in labels):
        raise Refused("pull request #{} is not a labelled {}* train (head branch {}, label {} {})".format(
            number, TRAIN_PREFIX, _shown(head_ref), TRAIN_LABEL, "present" if TRAIN_LABEL in labels else "absent"))


def squash_text(pr, opts):
    """(commit title, commit message) for the squash: "TITLE (#N)" and the pull request body (CRLF as LF,
    trailing white space dropped); Refused for an empty body, CannotEvaluate for a malformed title or body."""
    title, body = pr.get("title"), pr.get("body")
    if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
        raise CannotEvaluate("the pull request read carries no usable one-line title for the squash commit")
    if body is not None and not isinstance(body, str):
        raise CannotEvaluate("the pull request read carries a malformed body")
    message = (body or "").replace("\r\n", "\n").rstrip()
    if not message:
        raise Refused("pull request #{} has an empty body, so the landing commit message would carry no "
                      "reviewed text; describe the change in the pull request body and run again".format(opts["--pr"]))
    if "\r" in message:
        raise CannotEvaluate("the pull request body holds a carriage return that is not part of a CRLF line end")
    return "{} (#{})".format(title.strip(), opts["--pr"]), message


def expected_message(text):
    """The landing commit message the squash request asks for: the title, a blank line, the message."""
    return text[0] + "\n\n" + text[1]


def squash_payload(opts, checked):
    """The merge request body: squash, pinned to the head, with the fixed title and message."""
    return {"merge_method": ALLOWED_METHOD, "sha": opts["--head"], "commit_title": checked["text"][0],
            "commit_message": checked["text"][1]}


def _sha_at(doc, *keys):
    """The full commit id at doc[keys[0]][keys[1]]...; None when absent or not a full lowercase id."""
    for key in keys:
        if not isinstance(doc, dict):
            return None
        doc = doc.get(key)
    return doc if isinstance(doc, str) and SHA_RE.fullmatch(doc) else None


def judge_compare(doc, opts):
    """The checked base commit id: the head is up to date with it (behind_by 0, and the compare's base
    commit is the merge base), it is --expect-base, and the change is not the RELEASING.md step 6a
    attestation pull request; Refused or CannotEvaluate otherwise."""
    behind = doc.get("behind_by")
    if type(behind) is not int or behind < 0:
        raise CannotEvaluate("the compare read carries no usable behind_by")
    if behind != 0:
        raise Refused("the head is {} commit(s) behind {}; update the pull request and check it again".format(
            behind, opts["--base"]))
    base_sha = _sha_at(doc, "base_commit", "sha")
    if base_sha is None or _sha_at(doc, "merge_base_commit", "sha") != base_sha:
        raise CannotEvaluate("the compare read does not name one full base commit that is also the merge "
                             "base, so the base the head is checked against is unknown")
    if base_sha != opts["--expect-base"]:
        raise Refused("{} is at {}, not the expected base {}; check the head on the current {} and run again "
                      "with that --expect-base".format(opts["--base"], base_sha, opts["--expect-base"],
                                                       opts["--base"]))
    files = doc.get("files")
    if not isinstance(files, list) or len(files) >= COMPARE_FILE_CAP:
        raise CannotEvaluate("the compare read carries no complete file list (missing, malformed, or {} or more "
                             "entries, which the hosting service may have cut short), so the step 6a "
                             "attestation pull request cannot be ruled out".format(COMPARE_FILE_CAP))
    for entry in files:
        names = (entry.get("filename"), entry.get("previous_filename", "")) if isinstance(entry, dict) else (None,)
        if not all(isinstance(name, str) for name in names) or not names[0]:
            raise CannotEvaluate("the compare read's file list carries a malformed entry")
        if ATTESTATION_PATH in names:
            raise Refused("pull request #{} changes {}, so it is the RELEASING.md step 6a attestation pull "
                          "request; this tool never lands it by squash: follow RELEASING.md step 6a, which lands "
                          "the certified commit itself".format(opts["--pr"], ATTESTATION_PATH))
    return base_sha


def judge_head_commit(doc, opts):
    """The head commit's full tree id, from a read that names the head itself; CannotEvaluate otherwise."""
    if _sha_at(doc, "sha") != opts["--head"]:
        raise CannotEvaluate("the head commit read names another commit than {}".format(opts["--head"]))
    tree = _sha_at(doc, "commit", "tree", "sha")
    if tree is None:
        raise CannotEvaluate("the head commit read carries no full tree id")
    return tree


# ---------- the landing ----------

class _Run:
    """One landing run: the bound target, the I/O it goes through, and the lines it writes."""

    def __init__(self, api, gate, origin, out, err):
        self.api, self.gate, self.origin, self.out, self.err = api, gate, origin, out, err

    def snapshot(self, repo, opts):
        """(policy verdict tuple, checked) from fresh reads, checked being what the landing must carry:
        {"base": base commit id, "tree": the head's tree id, "text": (squash title, squash message)};
        raises on anything not landable."""
        settings = read_json(self.api, "repos/{}".format(repo), "repository settings")
        verdict = evaluate_policy(opts["--method"], settings, repo)
        if verdict[0] == "cannot":
            raise CannotEvaluate(verdict[2])
        if verdict[0] == "refuse":
            raise Refused(verdict[2])
        pr = read_json(self.api, "repos/{}/pulls/{}".format(repo, opts["--pr"]), "pull request")
        judge_pr(pr, repo, opts)
        text = squash_text(pr, opts)
        compare = read_json(self.api, "repos/{}/compare/{}...{}".format(repo, opts["--base"], opts["--head"]),
                            "compare")
        base_sha = judge_compare(compare, opts)
        tree = judge_head_commit(read_json(self.api, "repos/{}/commits/{}".format(repo, opts["--head"]),
                                           "head commit"), opts)
        return verdict, {"base": base_sha, "tree": tree, "text": text}

    def run_gate(self, args, what):
        try:
            rc, text = self.gate(args)
        except subprocess.TimeoutExpired:
            raise CannotEvaluate("{}: tools/ci-status.sh did not finish in time".format(what)) from None
        except OSError as exc:
            raise CannotEvaluate("{}: tools/ci-status.sh could not start ({})".format(
                what, type(exc).__name__)) from None
        for line in text.splitlines():
            self.out("  | " + line)
        return rc, text.splitlines()

    def gates(self, opts):
        rc, _lines = self.run_gate(["--main-health"], "main health")
        if rc == 1:
            raise Refused("main is red (tools/ci-status.sh --main-health exit 1)")
        if rc != 0:
            raise CannotEvaluate("main's health cannot be evaluated (tools/ci-status.sh --main-health exit "
                                 "{})".format(rc))
        args = [opts["--head"], "--require-base", opts["--base"]]
        if "--require-status" in opts:
            args += ["--pr", opts["--pr"], "--require-status", opts["--require-status"]]
        rc, lines = self.run_gate(args, "head CI")
        if rc == 1:
            raise Refused("the head's CI does not pass (tools/ci-status.sh exit 1)")
        if rc != 0:
            raise CannotEvaluate("the head's CI cannot be evaluated (tools/ci-status.sh exit {})".format(rc))
        if not lines or lines[0] != "COMMIT: " + opts["--head"]:
            raise CannotEvaluate("tools/ci-status.sh did not report the checked head as its COMMIT")
        if "--require-status" in opts and lines[-1] != "MERGE-SHA: " + opts["--head"]:
            raise CannotEvaluate("tools/ci-status.sh did not print MERGE-SHA for the checked head")

    def submit(self, repo, opts, checked):
        """The one merge request; returns (exit code, landing sha or None)."""
        if opts["--method"] != ALLOWED_METHOD:  # the final assertion, immediately before the request
            raise Refused("merge method is not squash at submission")
        payload = squash_payload(opts, checked)
        endpoint = "repos/{}/pulls/{}/merge".format(repo, opts["--pr"])
        try:
            rc, out, err = self.api.put(endpoint, payload)
        except Exception as exc:  # a timeout, a failed start, an undecodable reply: the request may have landed
            self.err("UNKNOWN OUTCOME: the squash request for #{} was sent and did not complete ({}); do not "
                     "retry: read pull request #{} to reconcile before any further landing".format(
                         opts["--pr"], type(exc).__name__, opts["--pr"]))
            return EXIT_CANNOT, None
        try:
            doc = json.loads(out, object_pairs_hook=_no_duplicates)
        except (ValueError, TypeError, RecursionError):
            doc = None
        if rc == 0 and isinstance(doc, dict) and doc.get("merged") is True \
                and isinstance(doc.get("sha"), str) and SHA_RE.fullmatch(doc["sha"]):
            return EXIT_OK, doc["sha"]
        status = HTTP_STATUS_RE.search(err or "")
        if rc != 0 and status and status.group(1) in REJECTED_HTTP and isinstance(doc, dict) \
                and isinstance(doc.get("message"), str) and "merged" not in doc:
            self.err("REFUSED: the hosting service rejected the squash request (HTTP {}: {}); no other method "
                     "is tried and the request is not retried".format(status.group(1), _shown(doc["message"])))
            return EXIT_REFUSED, None
        self.err("UNKNOWN OUTCOME: the squash request for #{} returned no readable success (gh exit {}); do not "
                 "retry: read pull request #{} to reconcile before any further landing".format(
                     opts["--pr"], rc, opts["--pr"]))
        return EXIT_CANNOT, None

    def confirm(self, repo, opts, sha, checked):
        """The post-merge reads and main's health for the landing commit sha, every landing property
        checked against what the last pre-request read recorded (checked); an exit code."""
        try:
            pr = read_json(self.api, "repos/{}/pulls/{}".format(repo, opts["--pr"]), "pull request (after)")
            if pr.get("merged") is not True or pr.get("merge_commit_sha") != sha:
                raise CannotEvaluate("pull request #{} does not read back as merged at {}".format(opts["--pr"], sha))
            pr_head = _sha_at(pr, "head", "sha")
            pr_base = pr.get("base", {}).get("ref") if isinstance(pr.get("base"), dict) else None
            if pr_head is None or not isinstance(pr_base, str):
                raise CannotEvaluate("the pull request read back carries no full head id or base branch")
            commit = read_json(self.api, "repos/{}/commits/{}".format(repo, sha), "landing commit")
            if _sha_at(commit, "sha") != sha:
                raise CannotEvaluate("the landing commit read names another commit than {}".format(sha))
            parents = commit.get("parents")
            if not isinstance(parents, list):
                raise CannotEvaluate("the landing commit read carries no parents list")
            parent_ids = [_sha_at(parent, "sha") for parent in parents]
            landed_tree = _sha_at(commit, "commit", "tree", "sha")
            message = commit.get("commit", {}).get("message") if isinstance(commit.get("commit"), dict) else None
            # A landing commit without exactly one parent is NOT A SQUASH (exit 1) whatever else it
            # carries; a single-parent one must carry every value the remaining properties are judged on.
            if len(parents) == 1:
                if parent_ids[0] is None or landed_tree is None or not isinstance(message, str):
                    raise CannotEvaluate("the landing commit read carries no full parent or tree id or no message")
                head_tree = judge_head_commit(read_json(
                    self.api, "repos/{}/commits/{}".format(repo, opts["--head"]), "head commit (after)"), opts)
                if head_tree != checked["tree"]:
                    raise CannotEvaluate("the head commit reads back with another tree than the one recorded")
        except CannotEvaluate as exc:
            self.err("UNKNOWN OUTCOME: merged as {} by the response, but {}; reconcile before any further "
                     "landing".format(sha, exc))
            return EXIT_CANNOT
        failed = []
        if pr_head != opts["--head"]:
            failed.append("MERGED AT ANOTHER HEAD: pull request #{} reads back at head {}, not {}".format(
                opts["--pr"], pr_head, opts["--head"]))
        if pr_base != opts["--base"]:
            failed.append("MERGED INTO ANOTHER BASE: pull request #{} reads back with base {}, not {}".format(
                opts["--pr"], _shown(pr_base), opts["--base"]))
        if sha == opts["--head"] or len(parents) != 1:
            failed.append("MERGED BUT NOT A SQUASH: landing commit {} is {} with {} parent(s)".format(
                sha, "the head itself" if sha == opts["--head"] else "a new commit", len(parents)))
        elif parent_ids[0] != checked["base"]:
            failed.append("MERGED ONTO A MOVED BASE: landing commit {}'s parent is {}, not the checked base "
                          "commit {}; the landed tree was not the one checked".format(sha, parent_ids[0],
                                                                                      checked["base"]))
        if landed_tree != checked["tree"]:
            failed.append("MERGED BUT THE TREE DIFFERS: landing commit {} has tree {}, not the head's tree "
                          "{}".format(sha, landed_tree, checked["tree"]))
        if not isinstance(message, str) or message.rstrip("\n") != expected_message(checked["text"]):
            failed.append("MERGED BUT THE MESSAGE DIFFERS: landing commit {}'s message is not the squash title "
                          "and body this tool sent (it begins {})".format(sha, _shown(message, 120)))
        if failed:
            for line in failed:
                self.out("RESULT: " + line)
            return EXIT_REFUSED
        self.out("MERGED: #{} squashed onto {} at {} as {}, tree {} (the head's), message {}".format(
            opts["--pr"], opts["--base"], checked["base"], sha, landed_tree, _shown(checked["text"][0], 120)))
        try:
            rc, _lines = self.run_gate(["--main-health", "--merge-commit", sha], "post-merge main health")
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate: {}".format(exc))
            return EXIT_CANNOT
        if rc == 0:
            self.out("RESULT: LANDED: {} is on {} and main's push run for it is green".format(sha, opts["--base"]))
            return EXIT_OK
        if rc == 1:
            self.out("RESULT: LANDED, MAIN IS RED: main's push run for {} did not pass".format(sha))
            return EXIT_REFUSED
        self.err("ERROR: cannot evaluate main's push run for {} (tools/ci-status.sh exit {})".format(sha, rc))
        return EXIT_CANNOT

    def land(self, opts):
        try:
            repo = repo_from_origin(self.origin())
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate: {}".format(exc))
            return EXIT_CANNOT
        self.out("TARGET: {} pull request #{} into {} at head {} on base commit {}".format(
            repo, opts["--pr"], opts["--base"], opts["--head"], opts["--expect-base"]))
        self.out(BOUNDARY)
        try:
            verdict, first = self.snapshot(repo, opts)
            if verdict[0] == "warn":
                self.err(verdict[2])
            self.gates(opts)
            # The fresh read governs: settings, pull request, compare and head commit are read again after
            # the gates, and what they record is what the landing must carry; a title or body that changed
            # while the gates ran is refused, never sent unreviewed.
            verdict, checked = self.snapshot(repo, opts)
            if verdict[0] == "warn":
                self.err(verdict[2])
            if checked["text"] != first["text"]:
                raise Refused("pull request #{}'s title or body changed while the gates ran; review it and run "
                              "again".format(opts["--pr"]))
            if checked != first:
                raise Refused("the base commit or the head's tree changed while the gates ran; check again")
        except Refused as exc:
            self.err("REFUSED: {}; nothing was merged".format(exc))
            return EXIT_REFUSED
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate: {}; nothing was merged".format(exc))
            return EXIT_CANNOT
        except Exception as exc:  # no traceback exit 1 ("refused") for an error nothing judged
            self.err("ERROR: cannot evaluate: an unexpected {} before the merge request; nothing was "
                     "merged".format(type(exc).__name__))
            return EXIT_CANNOT
        code, sha = self.submit(repo, opts, checked)
        if code != EXIT_OK:
            return code
        try:
            return self.confirm(repo, opts, sha, checked)
        except Exception as exc:  # after the merge: never a traceback exit 1, always the unknown outcome
            self.err("UNKNOWN OUTCOME: merged as {} by the response, but the post-merge check raised an "
                     "unexpected {}; reconcile before any further landing".format(sha, type(exc).__name__))
            return EXIT_CANNOT


class _GhApi:
    """The live API: gh api, pinned to github.com, one bounded call per read."""

    def _call(self, args, stdin=None):
        argv = ["gh", "api", "--hostname", API_HOST]
        for header in API_HEADERS:
            argv += ["-H", header]
        proc = subprocess.run(argv + args, input=stdin, capture_output=True, text=True, timeout=READ_TIMEOUT,
                              cwd=str(ROOT), check=False)
        return proc.returncode, proc.stdout, proc.stderr

    def get(self, endpoint):
        return self._call(["--method", "GET", endpoint])

    def put(self, endpoint, payload):
        return self._call(["--method", "PUT", endpoint, "--input", "-"], json.dumps(payload))


def _live_origin():
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "--all", "origin"],
                              capture_output=True, timeout=READ_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise CannotEvaluate("git could not read the origin remote URL ({})".format(type(exc).__name__)) from None
    if proc.returncode != 0:
        raise CannotEvaluate("git could not read the origin remote URL (exit {})".format(proc.returncode))
    return proc.stdout


def _live_gate(args):
    proc = subprocess.run(["bash", str(ROOT / CI_STATUS_REL)] + list(args), capture_output=True, text=True,
                          timeout=GATE_TIMEOUT, cwd=str(ROOT), check=False)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode, proc.stdout


def _print_out(line):
    print(line, flush=True)


def _print_err(line):
    print(line, file=sys.stderr, flush=True)


def land_cli(argv, api=None, gate=None, origin=None, out=_print_out, err=_print_err):
    """The landing for argv through the given I/O (the live gh, ci-status.sh and git by default)."""
    opts = parse_args(argv)
    if isinstance(opts, tuple):
        code, message = opts
        err(message if code == EXIT_REFUSED else "error: " + message + "; nothing was read or merged")
        return code
    # `is None`, never truthiness: a falsy stub must not fall through to the live gh, git or ci-status.sh.
    run = _Run(_GhApi() if api is None else api, _live_gate if gate is None else gate,
               _live_origin if origin is None else origin, out, err)
    try:
        return run.land(opts)
    except CannotEvaluate as exc:
        err("ERROR: cannot evaluate: {}; nothing was merged".format(exc))
        return EXIT_CANNOT


# ---------- the self-test (offline: recorded API responses, no network, no child process) ----------

SUITE_ID = "merge-pr-selftest"
EXECUTED = []
FAILURES = []
FX_REPO = "aiqt-fixture/guardrails"
FX_HEAD = "ab" * 20
FX_LANDED = "cd" * 20
FX_BASE = "ef" * 20
FX_TREE = "12" * 20
FX_ORIGIN = b"https://github.com/aiqt-fixture/guardrails.git\n"
FX_TITLE = "Merge train 20261121a: #5"
FX_BODY = "Train of #5.\r\n\r\nReviewed in #5.\r\n"
FX_SQUASH_TITLE = FX_TITLE + " (#7)"
FX_SQUASH_MESSAGE = "Train of #5.\n\nReviewed in #5."
FX_LANDED_MESSAGE = FX_SQUASH_TITLE + "\n\n" + FX_SQUASH_MESSAGE + "\n"
FX_FILES = [{"filename": "tools/merge_pr.py", "status": "modified"}]


def _argv(pr="7", head=FX_HEAD, base="main", method="squash", expect=FX_BASE, train="--train", extra=()):
    """A landing argument list; a None value leaves its option out."""
    argv = []
    for name, value in (("--pr", pr), ("--head", head), ("--base", base), ("--method", method),
                        ("--expect-base", expect)):
        if value is not None:
            argv += [name, value]
    return argv + ([train] if train is not None else []) + list(extra)


FX_ARGS = _argv()


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _settings(squash=True, merge=False, rebase=False, **changes):
    doc = {"full_name": FX_REPO, "allow_squash_merge": squash, "allow_merge_commit": merge,
           "allow_rebase_merge": rebase}
    doc.update(changes)
    return doc


def _pr(**changes):
    doc = {"number": 7, "state": "open", "draft": False, "merged": False, "title": FX_TITLE, "body": FX_BODY,
           "labels": [{"name": TRAIN_LABEL}],
           "base": {"ref": "main", "repo": {"full_name": FX_REPO}},
           "head": {"sha": FX_HEAD, "ref": "merge-train-20261121a", "repo": {"full_name": FX_REPO}}}
    for key, value in changes.items():
        if key in ("base", "head"):
            doc[key] = dict(doc[key], **value)
        else:
            doc[key] = value
    return doc


class _Recorded:
    """A recording API stub: each endpoint answers from a list of recorded responses, in order (the last
    one repeats); every call is recorded, the PUT with its exact payload."""

    def __init__(self, gets, put=None):
        self.gets = {key: list(value) if isinstance(value, list) else [value] for key, value in gets.items()}
        self.put_answer = put
        self.calls = []

    @staticmethod
    def _answer(answer):
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, tuple):
            return answer
        return 0, json.dumps(answer), ""

    def get(self, endpoint):
        self.calls.append(("GET", endpoint))
        answers = self.gets.get(endpoint)
        if not answers:
            return 1, "", "gh: Not Found (HTTP 404)"
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return self._answer(answer)

    def put(self, endpoint, payload):
        self.calls.append(("PUT", endpoint, json.dumps(payload, sort_keys=True)))
        return self._answer(self.put_answer)

    def puts(self):
        return [call for call in self.calls if call[0] == "PUT"]


def _compare(behind=0, base=FX_BASE, merge_base=FX_BASE, files=FX_FILES):
    return {"behind_by": behind, "ahead_by": 1, "base_commit": {"sha": base},
            "merge_base_commit": {"sha": merge_base}, "files": files}


def _commit(sha=FX_LANDED, parents=(FX_BASE,), tree=FX_TREE, message=FX_LANDED_MESSAGE):
    return {"sha": sha, "commit": {"tree": {"sha": tree}, "message": message},
            "parents": [{"sha": parent} for parent in parents]}


def _gets(settings=None, pr=None, compare=None, after=None, commit=None, head_commit=None, repo=FX_REPO):
    pr_answers = [pr if pr is not None else _pr()]
    if after is not None:
        pr_answers = pr_answers * 2 + [after]
    return {"repos/" + repo: settings if settings is not None else _settings(),
            "repos/{}/pulls/7".format(repo): pr_answers,
            "repos/{}/compare/main...{}".format(repo, FX_HEAD): compare if compare is not None else _compare(),
            "repos/{}/commits/{}".format(repo, FX_LANDED): commit if commit is not None else _commit(),
            "repos/{}/commits/{}".format(repo, FX_HEAD):
                head_commit if head_commit is not None else _commit(FX_HEAD, ("aa" * 20,))}


def _gate_fn(calls, main=0, head=0, post=0, commit_line=None, merge_sha_line=None):
    def gate(args):
        calls.append(tuple(args))
        if args == ["--main-health"]:
            return main, "RESULT: main fixture\n"
        if args[:1] == ["--main-health"]:
            return post, "RESULT: post-merge fixture\n"
        lines = [commit_line or ("COMMIT: " + FX_HEAD), "RESULT: head fixture"]
        if "--require-status" in args:
            lines.append(merge_sha_line or ("MERGE-SHA: " + FX_HEAD))
        return head, "\n".join(lines) + "\n"
    return gate


def _land(gets, put=None, argv=None, origin=FX_ORIGIN, **gate_kwargs):
    """(exit code, recorded api, gate calls, stdout lines, stderr lines) for one landing on fixtures."""
    api = _Recorded(gets, put if put is not None else {"merged": True, "sha": FX_LANDED, "message": "ok"})
    gate_calls, out, err = [], [], []

    def read_origin():
        if isinstance(origin, BaseException):
            raise origin
        return origin
    code = land_cli(list(argv if argv is not None else FX_ARGS), api=api, gate=_gate_fn(gate_calls, **gate_kwargs),
                    origin=read_origin, out=out.append, err=err.append)
    return code, api, gate_calls, out, err


def _merged_after():
    return _pr(merged=True, state="closed", merge_commit_sha=FX_LANDED)


def _good_put_payload():
    return json.dumps({"merge_method": "squash", "sha": FX_HEAD, "commit_title": FX_SQUASH_TITLE,
                       "commit_message": FX_SQUASH_MESSAGE}, sort_keys=True)


def _self_test_policy():
    repo = FX_REPO
    # Each row: id, method, settings, expected verdict, expected extras. The rows come in flip pairs: the
    # same settings with one value changed move the verdict across the boundary under test.
    for name, method, settings, verdict, extras in (
            ("policy/squash-only-permit", "squash", _settings(), "permit", ()),
            ("policy/merge-commit-enabled-warns", "squash", _settings(merge=True), "warn", ("merge commits",)),
            ("policy/merge-commit-flip-permit", "squash", _settings(merge=False), "permit", ()),
            ("policy/rebase-enabled-warns", "squash", _settings(rebase=True), "warn", ("rebase merges",)),
            ("policy/rebase-flip-permit", "squash", _settings(rebase=False), "permit", ()),
            ("policy/both-enabled-warns", "squash", _settings(merge=True, rebase=True), "warn",
             ("merge commits", "rebase merges")),
            ("policy/squash-disabled-refused", "squash", _settings(squash=False), "refuse", ()),
            ("policy/squash-disabled-broad-refused", "squash", _settings(squash=False, merge=True, rebase=True),
             "refuse", ()),
            ("policy/squash-disabled-flip-permit", "squash", _settings(squash=True), "permit", ()),
            ("policy/method-merge-refused-narrow", "merge", _settings(), "refuse", ()),
            ("policy/method-merge-refused-broad", "merge", _settings(merge=True, rebase=True), "refuse", ()),
            ("policy/method-rebase-refused-narrow", "rebase", _settings(), "refuse", ()),
            ("policy/method-rebase-refused-broad", "rebase", _settings(merge=True, rebase=True), "refuse", ()),
            ("policy/method-refused-before-settings", "merge", None, "refuse", ()),
            ("policy/method-unknown-refused", "fast-forward", _settings(), "refuse", ()),
            ("policy/settings-not-object-cannot", "squash", [], "cannot", ()),
            ("policy/settings-null-cannot", "squash", None, "cannot", ()),
            ("policy/field-missing-cannot", "squash", {"full_name": FX_REPO, "allow_squash_merge": True,
                                                       "allow_merge_commit": False}, "cannot", ()),
            ("policy/field-null-cannot", "squash", _settings(squash=None), "cannot", ()),
            ("policy/field-string-cannot", "squash", _settings(squash="true"), "cannot", ()),
            ("policy/field-number-cannot", "squash", _settings(merge=0), "cannot", ()),
            ("policy/field-number-flip-permit", "squash", _settings(merge=False), "permit", ()),
            ("policy/identity-mismatch-cannot", "squash", _settings(full_name="other/guardrails"), "cannot", ()),
            ("policy/identity-case-mismatch-cannot", "squash", _settings(full_name="AIQT-fixture/guardrails"),
             "cannot", ()),
            ("policy/identity-flip-permit", "squash", _settings(full_name=FX_REPO), "permit", ())):
        got = evaluate_policy(method, settings, repo)
        check(name, (got[0], got[1]) == (verdict, extras), got)
    got = evaluate_policy("squash", _settings(merge=True, rebase=True), repo)
    check("policy/warning-names-repo-and-methods",
          FX_REPO in got[2] and "merge commits and rebase merges" in got[2] and "outside this tool" in got[2]
          and "disable them" in got[2], got)


def _self_test_binding():
    for name, raw, expected in (
            ("origin/https-accepted", b"https://github.com/aiqt-fixture/guardrails\n", FX_REPO),
            ("origin/https-git-accepted", FX_ORIGIN, FX_REPO),
            ("origin/ssh-url-accepted", b"ssh://git@github.com/aiqt-fixture/guardrails.git\n", FX_REPO),
            ("origin/scp-accepted", b"git@github.com:aiqt-fixture/guardrails.git\n", FX_REPO),
            ("origin/other-host-refused", b"https://github.example.com/aiqt-fixture/guardrails\n", None),
            ("origin/credentials-refused", b"https://token@github.com/aiqt-fixture/guardrails\n", None),
            ("origin/two-urls-refused", FX_ORIGIN + FX_ORIGIN, None),
            ("origin/empty-refused", b"", None),
            ("origin/no-final-newline-refused", FX_ORIGIN[:-1], None),
            ("origin/extra-segment-refused", b"https://github.com/aiqt-fixture/guardrails/extra\n", None),
            ("origin/dot-repo-refused", b"https://github.com/aiqt-fixture/..\n", None),
            ("origin/non-ascii-refused", "https://github.com/aiqt-fixture/guardräils\n".encode("utf-8"), None)):
        try:
            got = repo_from_origin(raw)
        except CannotEvaluate:
            got = None
        check(name, got == expected, (raw, got))

    for name, argv, code in (
            ("args/method-missing-refused", _argv(method=None), EXIT_CANNOT),
            ("args/method-repeated-refused", FX_ARGS + ["--method", "squash"], EXIT_CANNOT),
            ("args/method-conflicting-refused", FX_ARGS + ["--method", "merge"], EXIT_CANNOT),
            ("args/method-merge-refused", _argv(method="merge"), EXIT_REFUSED),
            ("args/method-rebase-refused", _argv(method="rebase"), EXIT_REFUSED),
            ("args/method-unknown-refused", _argv(method="Squash"), EXIT_CANNOT),
            ("args/auto-refused", FX_ARGS + ["--auto"], EXIT_CANNOT),
            ("args/admin-refused", FX_ARGS + ["--admin"], EXIT_CANNOT),
            ("args/passthrough-refused", FX_ARGS + ["--", "--merge"], EXIT_CANNOT),
            ("args/positional-refused", FX_ARGS + ["7"], EXIT_CANNOT),
            ("args/short-head-refused", _argv(head=FX_HEAD[:12]), EXIT_CANNOT),
            ("args/upper-head-refused", _argv(head=FX_HEAD.upper()), EXIT_CANNOT),
            ("args/leading-zero-pr-refused", _argv(pr="07"), EXIT_CANNOT),
            ("args/base-missing-refused", _argv(base=None), EXIT_CANNOT),
            ("args/base-dotdot-refused", _argv(base="a..b"), EXIT_CANNOT),
            ("args/hyphen-value-refused", ["--pr", "7", "--head"] + _argv(pr=None, head=None), EXIT_CANNOT),
            ("args/train-repeated-refused", FX_ARGS + ["--train"], EXIT_CANNOT),
            ("args/base-double-slash-refused", _argv(base="a//b"), EXIT_CANNOT),
            ("args/base-not-main-refused", _argv(base="release"), EXIT_REFUSED),
            ("args/base-not-main-broad-refused", _argv(base="main-next"), EXIT_REFUSED),
            ("args/expect-base-missing-refused", _argv(expect=None), EXIT_CANNOT),
            ("args/expect-base-short-refused", _argv(expect=FX_BASE[:12]), EXIT_CANNOT),
            ("args/expect-base-repeated-refused", FX_ARGS + ["--expect-base", FX_BASE], EXIT_CANNOT),
            ("args/train-choice-missing-refused", _argv(train=None), EXIT_CANNOT),
            ("args/train-and-not-train-refused", FX_ARGS + ["--not-train"], EXIT_CANNOT),
            ("args/not-train-repeated-refused", _argv(train="--not-train", extra=["--not-train"]), EXIT_CANNOT)):
        code_got, api, gate_calls, _out, _err = _land(_gets(), argv=argv)
        check(name, (code_got, api.calls, gate_calls) == (code, [], []), (code_got, api.calls, gate_calls))


def _self_test_landing():
    endpoint_put = "repos/{}/pulls/7/merge".format(FX_REPO)

    # The permitted landing: reads, gates, fresh reads, then exactly one squash request pinned to the head.
    code, api, gates, out, err = _land(_gets(after=_merged_after()))
    order = [call[:2] for call in api.calls]
    expected_order = [("GET", "repos/" + FX_REPO), ("GET", "repos/{}/pulls/7".format(FX_REPO)),
                      ("GET", "repos/{}/compare/main...{}".format(FX_REPO, FX_HEAD)),
                      ("GET", "repos/{}/commits/{}".format(FX_REPO, FX_HEAD)),
                      ("GET", "repos/" + FX_REPO), ("GET", "repos/{}/pulls/7".format(FX_REPO)),
                      ("GET", "repos/{}/compare/main...{}".format(FX_REPO, FX_HEAD)),
                      ("GET", "repos/{}/commits/{}".format(FX_REPO, FX_HEAD)),
                      ("PUT", endpoint_put), ("GET", "repos/{}/pulls/7".format(FX_REPO)),
                      ("GET", "repos/{}/commits/{}".format(FX_REPO, FX_LANDED)),
                      ("GET", "repos/{}/commits/{}".format(FX_REPO, FX_HEAD))]
    check("land/squash-only-one-pinned-request",
          code == 0 and order == expected_order and api.puts() == [("PUT", endpoint_put, _good_put_payload())]
          and gates == [("--main-health",), (FX_HEAD, "--require-base", "main"),
                        ("--main-health", "--merge-commit", FX_LANDED)]
          and not any(line.startswith("WARNING") for line in err), (code, api.calls, gates, err))
    check("land/boundary-stated", BOUNDARY in out and any(line.startswith("TARGET: " + FX_REPO) for line in out), out)

    for name, settings in (("land/broad-merge-warns-and-lands", _settings(merge=True)),
                           ("land/broad-rebase-warns-and-lands", _settings(rebase=True)),
                           ("land/broad-both-warns-and-lands", _settings(merge=True, rebase=True))):
        code, api, _gates, _out, err = _land(_gets(settings=settings, after=_merged_after()))
        check(name, code == 0 and api.puts() == [("PUT", endpoint_put, _good_put_payload())]
              and any(line.startswith("WARNING: " + FX_REPO) for line in err), (code, api.calls, err))

    # Refusals and cannot-evaluates before the request: each sends no merge request.
    for name, gets, gate_kwargs, origin, code_expected in (
            ("land/squash-disabled-no-request", _gets(settings=_settings(squash=False)), {}, FX_ORIGIN, 1),
            ("land/settings-read-failed-cannot", _gets(settings=(1, "", "gh: Bad credentials (HTTP 401)")), {},
             FX_ORIGIN, 2),
            ("land/settings-timeout-cannot", _gets(settings=subprocess.TimeoutExpired("gh", READ_TIMEOUT)), {},
             FX_ORIGIN, 2),
            ("land/settings-not-json-cannot", _gets(settings=(0, "<html>rate limited</html>", "")), {}, FX_ORIGIN, 2),
            ("land/settings-duplicate-key-cannot",
             _gets(settings=(0, '{"full_name": "aiqt-fixture/guardrails", "allow_squash_merge": true, '
                                '"allow_squash_merge": false, "allow_merge_commit": false, '
                                '"allow_rebase_merge": false}', "")), {}, FX_ORIGIN, 2),
            ("land/settings-missing-field-cannot", _gets(settings={"full_name": FX_REPO}), {}, FX_ORIGIN, 2),
            ("land/settings-string-bool-cannot", _gets(settings=_settings(squash="true")), {}, FX_ORIGIN, 2),
            ("land/settings-other-repo-cannot", _gets(settings=_settings(full_name="other/guardrails")), {},
             FX_ORIGIN, 2),
            ("land/origin-ambiguous-cannot", _gets(), {}, FX_ORIGIN + FX_ORIGIN, 2),
            ("land/origin-other-host-cannot", _gets(), {}, b"https://gitlab.com/aiqt-fixture/guardrails\n", 2),
            ("land/origin-unreadable-cannot", _gets(), {}, CannotEvaluate("git could not read the origin"), 2),
            ("land/pr-closed-refused", _gets(pr=_pr(state="closed")), {}, FX_ORIGIN, 1),
            ("land/pr-draft-refused", _gets(pr=_pr(draft=True)), {}, FX_ORIGIN, 1),
            ("land/pr-other-base-refused", _gets(pr=_pr(base={"ref": "release"})), {}, FX_ORIGIN, 1),
            ("land/pr-other-head-refused", _gets(pr=_pr(head={"sha": "ee" * 20})), {}, FX_ORIGIN, 1),
            ("land/pr-fork-head-refused", _gets(pr=_pr(head={"repo": {"full_name": "fork/guardrails"}})), {},
             FX_ORIGIN, 1),
            ("land/pr-base-other-repo-cannot", _gets(pr=_pr(base={"repo": {"full_name": "other/guardrails"}})), {},
             FX_ORIGIN, 2),
            ("land/pr-other-number-cannot", _gets(pr=_pr(number=8)), {}, FX_ORIGIN, 2),
            ("land/pr-malformed-cannot", _gets(pr={"number": 7}), {}, FX_ORIGIN, 2),
            ("land/behind-base-refused", _gets(compare={"behind_by": 2}), {}, FX_ORIGIN, 1),
            ("land/compare-malformed-cannot", _gets(compare={"behind_by": "0"}), {}, FX_ORIGIN, 2),
            ("land/main-red-refused", _gets(), {"main": 1}, FX_ORIGIN, 1),
            ("land/main-unknown-cannot", _gets(), {"main": 2}, FX_ORIGIN, 2),
            ("land/head-ci-red-refused", _gets(), {"head": 1}, FX_ORIGIN, 1),
            ("land/head-ci-unknown-cannot", _gets(), {"head": 2}, FX_ORIGIN, 2),
            ("land/head-ci-other-commit-cannot", _gets(), {"commit_line": "COMMIT: " + "ee" * 20}, FX_ORIGIN, 2),
            ("land/pr-head-repo-null-refused", _gets(pr=_pr(head={"repo": None})), {}, FX_ORIGIN, 1),
            ("land/compare-base-missing-cannot", _gets(compare={"behind_by": 0, "ahead_by": 1}), {}, FX_ORIGIN, 2),
            ("land/compare-base-not-merge-base-cannot", _gets(compare=_compare(merge_base="11" * 20)), {},
             FX_ORIGIN, 2),
            ("land/unexpected-error-before-request-cannot",
             _gets(settings=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")), {}, FX_ORIGIN, 2)):
        code, api, _gates, _out, _err = _land(gets, origin=origin, **gate_kwargs)
        check(name, code == code_expected and api.puts() == [], (code, api.calls, _err))

    # An adopter checkout: every read and the request spell the adopter's own repository, and recorded
    # answers for another repository are never read in its place.
    adopter = "adopter-org/their-repo"
    adopter_pr = _pr(base={"repo": {"full_name": adopter}}, head={"repo": {"full_name": adopter}})
    adopter_gets = _gets(settings=_settings(full_name=adopter), pr=adopter_pr, after=_merged_after(), repo=adopter)
    for name, gets, code_expected in (("land/adopter-target-only", adopter_gets, 0),
                                      ("land/adopter-never-reads-another-repo", _gets(), 2)):
        code, api, _gates, _out, _err = _land(gets, origin=b"git@github.com:adopter-org/their-repo.git\n")
        check(name, code == code_expected and api.calls != []
              and all(call[1] == "repos/" + adopter or call[1].startswith("repos/" + adopter + "/")
                      for call in api.calls)
              and len(api.puts()) == (1 if code_expected == 0 else 0), (code, api.calls))

    # Flips of the gate rows: the same fixture with the gate green lands.
    code, api, _gates, _out, _err = _land(_gets(after=_merged_after()), main=0, head=0)
    check("land/gates-green-flip-lands", code == 0 and len(api.puts()) == 1, (code, api.calls))

    # Train eligibility (--train): a merge-train-* head carrying the label lands; either missing refuses.
    # The explicit --not-train is the only way to skip it: the same non-train pull request then lands.
    non_train = _pr(head={"ref": "feature-x"}, labels=[])
    for name, pr, argv, code_expected in (
            ("land/train-eligible-lands", _pr(), FX_ARGS, 0),
            ("land/train-wrong-branch-refused", _pr(head={"ref": "feature-x"}), FX_ARGS, 1),
            ("land/train-label-missing-refused", _pr(labels=[]), FX_ARGS, 1),
            ("land/train-non-train-refused", non_train, FX_ARGS, 1),
            ("land/not-train-explicit-flip-lands", non_train, _argv(train="--not-train"), 0)):
        after = dict(pr, merged=True, state="closed", merge_commit_sha=FX_LANDED)
        code, api, _gates, _out, _err = _land(_gets(pr=pr, after=after), argv=argv)
        check(name, code == code_expected and len(api.puts()) == (1 if code_expected == 0 else 0), (code, api.calls))

    # The local-status requirement: forwarded to ci-status.sh, and its MERGE-SHA must name the head.
    argv = FX_ARGS + ["--require-status", "local-quality"]
    code, api, gates, _out, _err = _land(_gets(after=_merged_after()), argv=argv)
    check("land/require-status-forwarded",
          code == 0 and (FX_HEAD, "--require-base", "main", "--pr", "7", "--require-status", "local-quality") in gates,
          (code, gates))
    code, api, _gates, _out, _err = _land(_gets(), argv=argv, merge_sha_line="MERGE-SHA: " + "ee" * 20)
    check("land/require-status-other-sha-cannot", code == 2 and api.puts() == [], (code, api.calls))

    # The fresh read governs: a change between the first reads and the request refuses with no request.
    for name, gets, code_expected in (
            ("land/head-moved-during-gates-refused",
             dict(_gets(), **{"repos/{}/pulls/7".format(FX_REPO): [_pr(), _pr(head={"sha": "ee" * 20})]}), 1),
            ("land/settings-squash-disabled-before-submit-refused",
             dict(_gets(), **{"repos/" + FX_REPO: [_settings(), _settings(squash=False)]}), 1),
            ("land/settings-unreadable-before-submit-cannot",
             dict(_gets(), **{"repos/" + FX_REPO: [_settings(), (1, "", "gh: timeout")]}), 2),
            ("land/base-advanced-before-submit-refused",
             dict(_gets(), **{"repos/{}/compare/main...{}".format(FX_REPO, FX_HEAD):
                              [_compare(), _compare(behind=1)]}), 1)):
        code, api, _gates, _out, _err = _land(gets)
        check(name, code == code_expected and api.puts() == [], (code, api.calls))

    # The request outcome: a rejection is exit 1 with one request and no fallback; anything unreadable is
    # an unknown outcome (exit 2), one request, no retry.
    for name, put, code_expected, needle in (
            ("submit/rejected-405-no-fallback", (1, '{"message": "Base branch was modified"}', "gh: (HTTP 405)"), 1,
             "REFUSED"),
            ("submit/rejected-409-head-moved", (1, '{"message": "Head branch was modified"}', "gh: (HTTP 409)"), 1,
             "REFUSED"),
            ("submit/server-error-unknown", (1, '{"message": "Server Error"}', "gh: (HTTP 502)"), 2, "UNKNOWN OUTCOME"),
            ("submit/timeout-unknown", subprocess.TimeoutExpired("gh", READ_TIMEOUT), 2, "UNKNOWN OUTCOME"),
            ("submit/malformed-success-unknown", (0, "not json", ""), 2, "UNKNOWN OUTCOME"),
            ("submit/merged-false-unknown", {"merged": False, "message": "not merged"}, 2, "UNKNOWN OUTCOME"),
            ("submit/short-sha-unknown", {"merged": True, "sha": "cd" * 4}, 2, "UNKNOWN OUTCOME"),
            ("submit/gh-error-with-merged-body-unknown",
             (1, json.dumps({"merged": True, "sha": FX_LANDED}), "gh: Bad Gateway (HTTP 502)"), 2, "UNKNOWN OUTCOME"),
            ("submit/rejection-body-claims-merged-unknown",
             (1, json.dumps({"message": "conflict", "merged": True}), "gh: conflict (HTTP 409)"), 2, "UNKNOWN OUTCOME"),
            ("submit/undecodable-reply-unknown", UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), 2,
             "UNKNOWN OUTCOME")):
        code, api, gates, _out, err = _land(_gets(after=_merged_after()), put=put)
        check(name, code == code_expected and len(api.puts()) == 1
              and all(call[0] == "GET" for call in api.calls[api.calls.index(api.puts()[0]) + 1:])
              and not any(args[:2] == ("--main-health", "--merge-commit") for args in gates)
              and any(needle in line for line in err), (code, api.calls, err))

    # After the request: the read-back, the landing commit's parents and main's push run decide.
    for name, gets, post, code_expected, needle in (
            ("after/landed-green", _gets(after=_merged_after()), 0, 0, "RESULT: LANDED:"),
            ("after/not-single-parent-refused",
             _gets(after=_merged_after(), commit=_commit(parents=(FX_BASE, FX_HEAD))), 0, 1, "NOT A SQUASH"),
            ("after/moved-base-refused", _gets(after=_merged_after(), commit=_commit(parents=("11" * 20,))), 0, 1,
             "MOVED BASE"),
            ("after/tree-differs-refused", _gets(after=_merged_after(), commit=_commit(tree="13" * 20)), 0, 1,
             "TREE DIFFERS"),
            ("after/parents-not-list-unknown",
             _gets(after=_merged_after(), commit=dict(_commit(), parents=None)), 0, 2, None),
            ("after/landing-tree-missing-unknown",
             _gets(after=_merged_after(), commit=dict(_commit(), commit={})), 0, 2, None),
            ("after/head-commit-unreadable-unknown",
             _gets(after=_merged_after(), head_commit=[_commit(FX_HEAD, ("aa" * 20,))] * 2
                   + [(1, "", "gh: Not Found (HTTP 404)")]), 0, 2, None),
            ("after/readback-not-merged-unknown", _gets(after=_pr()), 0, 2, None),
            ("after/readback-other-sha-unknown",
             _gets(after=_pr(merged=True, state="closed", merge_commit_sha="11" * 20)), 0, 2, None),
            ("after/unexpected-error-unknown",
             _gets(after=_merged_after(), commit=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")),
             0, 2, None),
            ("after/main-red-after-merge", _gets(after=_merged_after()), 1, 1, "MAIN IS RED"),
            ("after/main-unknown-after-merge", _gets(after=_merged_after()), 2, 2, None)):
        code, api, _gates, out, err = _land(gets, post=post)
        check(name, code == code_expected and len(api.puts()) == 1
              and (needle is None or any(needle in line for line in out)), (code, out, err))

    # Flip of the final assertion: a run whose options are altered to merge after parsing never sends.
    opts = parse_args(FX_ARGS)
    opts["--method"] = "merge"
    run = _Run(_Recorded(_gets()), _gate_fn([]), lambda: FX_ORIGIN, lambda line: None, lambda line: None)
    try:
        run.submit(FX_REPO, opts, {"base": FX_BASE, "tree": FX_TREE, "text": (FX_SQUASH_TITLE, FX_SQUASH_MESSAGE)})
        refused = False
    except Refused:
        refused = True
    check("submit/final-assertion-refuses-non-squash", refused and run.api.puts() == [], run.api.calls)


def _self_test_properties():
    """Each landing property (head, base, parent, tree, message, method, and the attestation refusal):
    a row where it does not hold, before the request (no request sent) and after it (one request, a
    RESULT line, exit 1), each beside its flip, the same fixture with that one value restored, landing."""
    pulls = "repos/{}/pulls/7".format(FX_REPO)
    after_ok = _merged_after()

    def pr_reads(first, second, after=after_ok):
        return dict(_gets(after=after), **{pulls: [first, second, after]})

    landed_head_put = {"merged": True, "sha": FX_HEAD, "message": "ok"}
    many = [{"filename": "docs/f{}.md".format(index), "status": "added"} for index in range(COMPARE_FILE_CAP)]
    for name, gets, argv, put, code_expected, sent, needle in (
            # head
            ("prop/head-before-pr-other-refused", _gets(pr=_pr(head={"sha": "ee" * 20})), None, None, 1, 0,
             "not the checked head"),
            ("prop/head-before-flip-lands", _gets(pr=_pr(head={"sha": FX_HEAD}), after=after_ok), None, None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/head-before-commit-read-other-cannot", _gets(head_commit=_commit("ee" * 20, ("aa" * 20,))), None,
             None, 2, 0, "another commit"),
            ("prop/head-before-commit-read-flip-lands", _gets(head_commit=_commit(FX_HEAD, ("aa" * 20,)),
                                                              after=after_ok), None, None, 0, 1, "RESULT: LANDED:"),
            ("prop/head-after-other-refused", _gets(after=_pr(merged=True, state="closed", merge_commit_sha=FX_LANDED,
                                                              head={"sha": "ee" * 20})), None, None, 1, 1,
             "MERGED AT ANOTHER HEAD"),
            ("prop/head-after-flip-lands", _gets(after=_pr(merged=True, state="closed", merge_commit_sha=FX_LANDED,
                                                           head={"sha": FX_HEAD})), None, None, 0, 1,
             "RESULT: LANDED:"),
            # base
            ("prop/base-before-expect-differs-refused", _gets(), _argv(expect="11" * 20), None, 1, 0,
             "not the expected base"),
            ("prop/base-before-main-moved-refused", _gets(compare=_compare(base="11" * 20, merge_base="11" * 20)),
             None, None, 1, 0, "not the expected base"),
            ("prop/base-before-expect-flip-lands", _gets(after=after_ok), _argv(expect=FX_BASE), None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/base-before-not-main-refused", _gets(), _argv(base="release"), None, 1, 0, "only into main"),
            ("prop/base-before-main-flip-lands", _gets(after=after_ok), _argv(base="main"), None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/base-after-other-refused", _gets(after=_pr(merged=True, state="closed", merge_commit_sha=FX_LANDED,
                                                              base={"ref": "release"})), None, None, 1, 1,
             "MERGED INTO ANOTHER BASE"),
            ("prop/base-after-flip-lands", _gets(after=_pr(merged=True, state="closed", merge_commit_sha=FX_LANDED,
                                                           base={"ref": "main"})), None, None, 0, 1, "RESULT: LANDED:"),
            # parent
            ("prop/parent-before-behind-refused", _gets(compare=_compare(behind=1)), None, None, 1, 0, "behind"),
            ("prop/parent-before-behind-flip-lands", _gets(compare=_compare(behind=0), after=after_ok), None, None, 0,
             1, "RESULT: LANDED:"),
            ("prop/parent-after-moved-refused", _gets(after=after_ok, commit=_commit(parents=("11" * 20,))), None,
             None, 1, 1, "MOVED BASE"),
            ("prop/parent-after-flip-lands", _gets(after=after_ok, commit=_commit(parents=(FX_BASE,))), None, None,
             0, 1, "RESULT: LANDED:"),
            # tree
            ("prop/tree-before-missing-cannot", _gets(head_commit=dict(_commit(FX_HEAD, ("aa" * 20,)), commit={})),
             None, None, 2, 0, "no full tree id"),
            ("prop/tree-after-differs-refused", _gets(after=after_ok, commit=_commit(tree="13" * 20)), None, None,
             1, 1, "TREE DIFFERS"),
            ("prop/tree-after-flip-lands", _gets(after=after_ok, commit=_commit(tree=FX_TREE)), None, None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/tree-after-head-reread-differs-unknown",
             _gets(after=after_ok, head_commit=[_commit(FX_HEAD, ("aa" * 20,))] * 2
                   + [_commit(FX_HEAD, ("aa" * 20,), tree="13" * 20)]), None, None, 2, 1, "UNKNOWN OUTCOME"),
            # message
            ("prop/message-before-body-empty-refused", _gets(pr=_pr(body="")), None, None, 1, 0, "empty body"),
            ("prop/message-before-body-null-refused", _gets(pr=_pr(body=None)), None, None, 1, 0, "empty body"),
            ("prop/message-before-body-blank-refused", _gets(pr=_pr(body=" \r\n\r\n")), None, None, 1, 0,
             "empty body"),
            ("prop/message-before-body-flip-lands", _gets(pr=_pr(body=FX_BODY), after=after_ok), None, None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/message-before-title-missing-cannot", _gets(pr=_pr(title=None)), None, None, 2, 0, "title"),
            ("prop/message-before-body-lone-cr-cannot", _gets(pr=_pr(body="a\rb")), None, None, 2, 0,
             "carriage return"),
            ("prop/message-before-title-changed-refused", pr_reads(_pr(), _pr(title="Another title")), None, None,
             1, 0, "title or body changed"),
            ("prop/message-before-body-changed-refused", pr_reads(_pr(), _pr(body="Another body")), None, None,
             1, 0, "title or body changed"),
            ("prop/message-before-unchanged-flip-lands", pr_reads(_pr(), _pr()), None, None, 0, 1, "RESULT: LANDED:"),
            ("prop/message-after-differs-refused", _gets(after=after_ok, commit=_commit(message="Squash of #7\n")),
             None, None, 1, 1, "MESSAGE DIFFERS"),
            ("prop/message-after-crlf-body-refused",
             _gets(after=after_ok, commit=_commit(message=FX_SQUASH_TITLE + "\n\n" + FX_BODY)), None, None, 1, 1,
             "MESSAGE DIFFERS"),
            ("prop/message-after-flip-lands", _gets(after=after_ok, commit=_commit(message=FX_LANDED_MESSAGE)), None,
             None, 0, 1, "RESULT: LANDED:"),
            ("prop/message-after-no-trailing-newline-lands",
             _gets(after=after_ok, commit=_commit(message=FX_LANDED_MESSAGE.rstrip("\n"))), None, None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/message-after-missing-unknown",
             _gets(after=after_ok, commit=dict(_commit(), commit={"tree": {"sha": FX_TREE}})), None, None, 2, 1,
             "or no message; reconcile"),
            # method
            ("prop/method-before-merge-refused", _gets(), _argv(method="merge"), None, 1, 0, "squash only"),
            ("prop/method-before-squash-flip-lands", _gets(after=after_ok), _argv(method="squash"), None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/method-after-head-is-landing-refused",
             _gets(after=_pr(merged=True, state="closed", merge_commit_sha=FX_HEAD),
                   head_commit=_commit(FX_HEAD, (FX_BASE,))), None, landed_head_put, 1, 1, "NOT A SQUASH"),
            ("prop/method-after-two-parents-refused", _gets(after=after_ok, commit=_commit(parents=(FX_BASE, FX_HEAD))),
             None, None, 1, 1, "NOT A SQUASH"),
            ("prop/method-after-two-parents-malformed-refused",
             _gets(after=after_ok, commit=dict(_commit(parents=(FX_BASE, FX_HEAD)), commit={})), None, None, 1, 1,
             "NOT A SQUASH"),
            ("prop/method-after-flip-lands", _gets(after=after_ok, commit=_commit()), None, None, 0, 1,
             "RESULT: LANDED:"),
            ("prop/method-after-landing-read-other-sha-unknown", _gets(after=after_ok, commit=_commit(sha="11" * 20)),
             None, None, 2, 1, "UNKNOWN OUTCOME"),
            # the RELEASING.md step 6a attestation pull request is never landed by squash
            ("prop/attestation-pr-refused",
             _gets(compare=_compare(files=FX_FILES + [{"filename": ATTESTATION_PATH, "status": "modified"}])),
             None, None, 1, 0, "step 6a"),
            ("prop/attestation-renamed-from-refused",
             _gets(compare=_compare(files=[{"filename": "x.toml", "previous_filename": ATTESTATION_PATH,
                                            "status": "renamed"}])), None, None, 1, 0, "step 6a"),
            ("prop/attestation-flip-lands",
             _gets(compare=_compare(files=FX_FILES + [{"filename": ".aiqt/manifest.toml", "status": "modified"}]),
                   after=after_ok), None, None, 0, 1, "RESULT: LANDED:"),
            ("prop/attestation-files-missing-cannot",
             _gets(compare={key: value for key, value in _compare().items() if key != "files"}), None, None, 2, 0,
             "file list"),
            ("prop/attestation-files-malformed-cannot", _gets(compare=_compare(files=[{"status": "added"}])), None,
             None, 2, 0, "malformed entry"),
            ("prop/attestation-files-not-list-cannot", _gets(compare=_compare(files={})), None, None, 2, 0,
             "file list"),
            ("prop/attestation-files-at-cap-cannot", _gets(compare=_compare(files=many)), None, None, 2, 0,
             "file list"),
            ("prop/attestation-files-below-cap-flip-lands", _gets(compare=_compare(files=many[1:]), after=after_ok),
             None, None, 0, 1, "RESULT: LANDED:")):
        code, api, _gates, out, err = _land(gets, put=put, argv=argv)
        check(name, code == code_expected and len(api.puts()) == sent
              and (sent == 0 or api.puts()[0][2] == _good_put_payload())
              and any(needle in line for line in out + err), (code, api.calls, out, err))


def _self_test_live_argv():
    """The live wrappers' command lines, with subprocess.run replaced by a recorder for the duration (no
    child process starts and nothing reaches the network): every gh call pins --hostname github.com and the
    API headers, the PUT sends the payload on stdin, and the origin read asks git for every URL."""
    recorded = []

    def fake_run(argv, **kwargs):
        recorded.append((list(argv), kwargs.get("input")))
        return subprocess.CompletedProcess(argv, 0, stdout=b"" if not kwargs.get("text") else "", stderr="")
    from unittest import mock
    payload = squash_payload(parse_args(FX_ARGS), {"text": (FX_SQUASH_TITLE, FX_SQUASH_MESSAGE)})
    with mock.patch.object(subprocess, "run", fake_run):
        api = _GhApi()
        api.get("repos/" + FX_REPO)
        api.put("repos/{}/pulls/7/merge".format(FX_REPO), payload)
        _live_origin()
    pinned = ["gh", "api", "--hostname", "github.com", "-H", API_HEADERS[0], "-H", API_HEADERS[1]]
    check("live/gh-calls-pin-hostname-and-headers",
          len(recorded) == 3 and recorded[0][0] == pinned + ["--method", "GET", "repos/" + FX_REPO]
          and recorded[1][0] == pinned + ["--method", "PUT", "repos/{}/pulls/7/merge".format(FX_REPO), "--input", "-"]
          and json.loads(recorded[1][1]) == {"merge_method": "squash", "sha": FX_HEAD,
                                             "commit_title": FX_SQUASH_TITLE,
                                             "commit_message": FX_SQUASH_MESSAGE}, recorded)
    check("live/origin-read-asks-for-every-url",
          len(recorded) == 3 and recorded[2][0][-4:] == ["remote", "get-url", "--all", "origin"], recorded)


def _self_test():
    _self_test_policy()
    _self_test_binding()
    _self_test_landing()
    _self_test_properties()
    _self_test_live_argv()
    got = [_main_exit(argv) for argv in (["--self-test", "--bogus"], ["--execution-report", "relative/report"],
                                         [])]
    check("cli/unrecognized-arguments-exit-2", got == [2, 2, 2], got)
    if FAILURES:
        print("SELF-TEST FAIL: merge_pr {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: merge_pr {} checks on recorded API responses with no network (the policy table "
          "with its flips: squash-only permits, each wider setting warns and lands by squash, squash disabled "
          "and merge or rebase are refused under narrow and broad settings, missing, null, wrong-type and "
          "other-repository settings cannot evaluate; the origin binding; every argument refusal before any "
          "read; the landing's request order and exact pinned squash payload; every pre-request refusal "
          "sends no request; a fresh read before submission governs; a rejection is never retried or "
          "replaced by another method; an unreadable or unexpected outcome is unknown; the post-merge reads "
          "(single parent, the checked base commit as that parent, the head's tree) and main's health decide "
          "the result; each landing property (head, base, parent, tree, message, method) refused before the "
          "request and reported after it, each beside its flip; the step 6a attestation pull request "
          "refused; the live gh and git command lines pin the host and read every origin URL)".format(
              len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    argv = list(argv)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if not argv or argv[0] == "--self-test" or "--execution-report" in argv:
        print("error: unrecognized arguments {}; fail closed".format(_shown(argv)), file=sys.stderr)
        return 2
    return land_cli(argv)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This tool starts gh, git and
# tools/ci-status.sh and reads their exit statuses (subprocess), so under an ignored SIGCHLD a failing gate
# would read as a pass and a merge would follow; the guard runs at the entry line, before main() dispatches.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("merge_pr.py") or main())
