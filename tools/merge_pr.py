#!/usr/bin/env python3
"""The repository-owned merge entry point (merge-pr): land ONE pull request, by squash only, onto the
commit the caller checked.

  merge_pr.py --pr N --head SHA --base main --method squash --expect-base SHA (--train | --not-train)
              [--require-status CONTEXT] [--repair-red-main]
  merge_pr.py --self-test [--execution-report ABS_PATH]
  any other argument list is refused (exit 2) before any read

Exit: 0 merged, every landing property below confirmed after the merge, main's push run for it green, and
main's tip, read after that run, containing the landing commit;
1 refused (a method other than squash, a base other than main, squash disabled on the hosting service, a
pull request that is not the one named, the RELEASING.md step 6a attestation pull request, a base commit
other than --expect-base, an empty pull request body, a red gate or an API rejection, and with
--repair-red-main also --not-train, a green main, a train tree with a failed Quality run or no successful
one, or a main tip other than the checked base commit, a red main being no refusal in that mode only;
nothing was merged), or merged but the post-merge check found a landing property that does not hold,
main red, or main's tip no longer containing the landing (one RESULT line per property that fails); 2
cannot evaluate (a usage error, settings or state that cannot be read or are malformed, an identity
mismatch, an interruption or unexpected error before the request; nothing was merged), or an UNKNOWN
merge outcome (the request was sent and its result could not be read, or an interruption or unexpected
error ended the run once it was sent), or merged with main's push run for it, or main's tip, not evaluable.

THE LANDING PROPERTIES. What this tool claims about a landing, each checked before the request where it
can be and after it always (the self-test drives a refusal row and its flip for each):
  head     before: the pull request read names --head, and the head commit read is that commit;
           the request pins it (sha). After: the pull request reads back merged at --head.
  base     before: --base is main (tools/ci-status.sh --main-health reads only main), the pull request
           targets it, and the compare read's base commit is --expect-base. After: the pull request
           reads back with base main.
  parent   before: the head is up to date with --expect-base (behind_by 0, that commit the merge base).
           After: the landing commit has exactly one parent, and it is --expect-base (a moved parent is
           reported on its own line; whether the tree is the checked one is judged apart, below).
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
  on main  after: main's push run for the landing commit is green (tools/ci-status.sh --main-health
           --merge-commit), and then main's tip, read fresh (GET git/ref/heads/main), contains the
           landing commit (the compare read LANDING...TIP has behind_by 0 and the landing as its merge
           base); a green run alone is history and does not show that main still holds the landing.

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
validated OWNER/REPO, so GH_REPO and gh's {owner}/{repo} placeholders select nothing; tools/ci-status.sh,
whose reads decide the gates, is bound to the same target: it is started with --api-host github.com
(which makes it pass --hostname github.com to every gh api call it makes) in an environment built from
this process's with GH_HOST set to github.com, GH_REPO removed and CI_STATUS_REPO set to the validated
OWNER/REPO (ci-status.sh exits 2 unless its own origin names exactly that); a repository this tool did
not validate starts no gate (cannot evaluate), and a ci-status.sh that does not accept --api-host exits
2, a cannot-evaluate, never an unbound read; the pull request
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
repository settings; the settings then let the run go on to its remaining checks, and it sends the squash
request only if every check before the request passes (a WARNING is not a landing). The post-merge checks
run after the landing: their failure is exit 1 or 2 after a merge (see Exit above), so only exit 0
confirms a landing that satisfies them all, and a nonzero exit does not show that nothing landed.
Nothing here changes a hosting setting.

THE SEQUENCE. Before any read the argument list is validated: the method and the base (a missing,
unknown or malformed value is a usage refusal, exit 2; merge, rebase and a base other than main are
refused, exit 1); exactly one of --train and --not-train (neither or both given is a usage refusal,
exit 2); and --repair-red-main with --not-train (refused, exit 1). (1) Bind the target
and read the settings; (2) read the pull request and require it open, not a draft, not merged, at the
named head and base, its head branch in this repository (with --train, also a merge-train-* head branch
carrying the merge-train label), and compose the squash title and message from it; (3) require the head
to be up to date with the base (the compare read's behind_by is 0, its base_commit and merge_base_commit
the same full commit id, and that id --expect-base, recorded as the checked base commit), require the
compare's file list not to name the attestation path, and read the head commit's tree; (4) run the
existing gates in this checkout, in this order:
`tools/ci-status.sh --main-health` (main must be green; only THE RED-MAIN REPAIR MODE below requires it
red instead, refuses a green main and prints a NOTE line after the gate's own MAIN IS RED output), then
`tools/ci-status.sh HEAD --require-base BASE`, plus `--pr N --require-status CONTEXT` when
--require-status is given (its MERGE-SHA line must name the head), then, in the repair mode only,
`tools/ci-status.sh --match-tree --head HEAD` (exit 0 with its COMMIT line naming the head); (5) read the
settings, the pull request, the compare and the head commit again and require the same decision, the
same head, base commit, tree, title and body: a change is a refusal that asks for fresh checks, never an
authorization carried over; then, in the repair mode only, read main's tip (GET git/ref/heads/main) and
require it to be the checked base commit (another tip is a refusal, an unreadable one cannot-evaluate);
(6) assert the method is squash and send
exactly one PUT repos/OWNER/REPO/pulls/N/merge with {"merge_method": "squash", "sha": HEAD,
"commit_title": TITLE, "commit_message": BODY}, so a head that moved after the last read is rejected by
the hosting service itself; (7) require merged true and a full landing commit id, read the pull request
back and the landing commit and check every landing property above, then run `tools/ci-status.sh
--main-health --merge-commit SHA` and, when that is green, read main's tip again and require it to contain
the landing commit. The request pins only the head, so a base that advanced between the
last read and the merge is not refused by the hosting service unless its branch protection requires an
up-to-date branch; --expect-base narrows that window to the last read, and this tool detects the rest
after the landing (exit 1, the RESULT line says so) and cannot undo it. A rejection (HTTP 405, 409 or 422 with a message)
is exit 1 with no fallback to another method and no retry; any other failure after the request was sent,
an unexpected error included, is an UNKNOWN outcome (exit 2): reconcile by reading the pull request
before any further landing, never by sending the request again. An unexpected error or an interruption
(KeyboardInterrupt, SystemExit, any BaseException) before the request is cannot-evaluate (exit 2,
nothing was merged), and from the moment the request is handed to the API it is an UNKNOWN outcome (exit
2, reconcile), never a traceback; a second interruption that arrives while that line is being written can
still end the process by Python's own handling (a non-zero exit, never 0). Deferred landing (auto-merge, a merge
queue) and an administrator bypass are not offered: no option reaches them.

THE RED-MAIN REPAIR MODE (--repair-red-main, RELEASING.md's red-main repair route). Without the option
the main-health gate of step (4) refuses every landing while main is red, so the train that fixes a red
main could never land through this tool. With it, and only with it, a red main is not a refusal; the
option is the only way to land while main is red, and it lands only when all of these hold:
  train    the pull request is a merge-train pull request: --train is required (--repair-red-main with
           --not-train is refused, exit 1, before any read) and the --train check of step (2) applies,
           so the head is a merge-train-* branch of this repository carrying the merge-train label;
  red      main's newest completed push run is red: `tools/ci-status.sh --main-health` exits 1, which it
           does when the first completed Quality push run on main in its listing (the newest 500 runs)
           concluded anything but success; a newer push run still in progress neither passes nor blocks,
           and a schedule or workflow_dispatch run never decides. A green main (exit 0) is refused (exit 1,
           land through the normal path instead) and an unevaluable one (any other exit) is
           cannot-evaluate;
  green    the train's own Quality run on the exact head is green: the head gate of step (4),
           `tools/ci-status.sh HEAD --require-base BASE`, unchanged; and the train's tree has a successful
           Quality run and no failed one among the runs `tools/ci-status.sh --match-tree --head HEAD` reads:
           it must exit 0 with its COMMIT line naming the head (exit 1 is a refusal, any other exit
           cannot-evaluate). That read's window is limited: it sees the latest attempt of each run only (a
           failed attempt later re-run to success is not seen), the newest 500 runs listed only, the trees
           of the newest 20 distinct head commits of completed Quality runs only, and no schedule or
           workflow_dispatch run, so a failed run on the same tree outside that window does not refuse;
           and it refuses a green train whose successful run is listed after a run on a further head
           commit (past those 20);
  tip      the pull request's base is main's current tip: after the fresh reads of step (5), main's tip
           is read (GET git/ref/heads/main) and must be the checked base commit, which is --expect-base
           (another tip is a refusal, an unreadable one cannot-evaluate). This read narrows the window
           before the request but does not close it: the request pins only the head, as on the normal
           path, so a main that moves after this read is not prevented and is detected after the landing
           (MERGED ONTO A MOVED BASE, exit 1);
  the rest every other check and gate the normal path applies, before and after the request, applies
           unchanged (the settings, the pull request, the compare, the step 6a refusal, the message, the
           fresh reads with their refusal of a base or head tree that changed while the gates ran, the
           --require-status gate when given, the one pinned squash request and every post-merge check).
The mode is recorded in the output: a MODE line after the BOUNDARY line, a NOTE line right after the
main-health gate's echoed output when that gate exits 1 (the gate's own output is shown unchanged; its
advice to merge nothing else onto main holds for every other pull request, while this train is the
repair), and a note on the MERGED line.
The post-merge `tools/ci-status.sh --main-health --merge-commit SHA` still runs and reports: the repair
is exit 0 only when main's push run for the landing commit is green, and a repair that leaves main red is
exit 1 (RESULT: LANDED, MAIN IS RED), as on the normal path. Without the option nothing changes: the
normal path refuses a red main exactly as before, and nothing it refuses is accepted outside the
conditions above. Whether the train actually fixes what made main red is the caller's judgment; this
tool reads only that main's newest completed push run is red and that the train is green within the
windows above.

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
authentication decides which account acts (GH_HOST is fixed to github.com for the gate, but a token in
the environment, such as GH_TOKEN, is passed through as it is); and the gates run are this checkout's
tools/ci-status.sh, whose own header states what it reads and does not.
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
# The gate child (tools/ci-status.sh) is bound to the same target as this tool's own reads: the option
# makes it pass --hostname API_HOST to every gh api call, GH_HOST is set to API_HOST, GH_REPO is removed
# and CI_STATUS_REPO is set to the validated OWNER/REPO, which ci-status.sh requires its own origin to equal.
GATE_HOST_OPTION = "--api-host"
GATE_ENV_REMOVED = ("GH_REPO",)
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
# THE RED-MAIN REPAIR MODE: the only option under which a red main is not a refusal.
REPAIR_OPTION = "--repair-red-main"
FLAG_OPTIONS = ("--train", "--not-train", REPAIR_OPTION)
REPAIR_MODE_LINE = ("MODE: REPAIR-RED-MAIN (--repair-red-main): lands only while main's newest completed push "
                    "run is red, only a merge-train pull request whose own Quality run on this head is green and "
                    "whose tree has a successful Quality run and no failed one within the window "
                    "tools/ci-status.sh --match-tree reads (latest attempts, the newest 500 runs, the newest 20 "
                    "head commits, no schedule or workflow_dispatch run), only on main's tip as read before the "
                    "request (the request pins only the head, as on the normal path), and every other gate of the "
                    "normal path applies unchanged.")
# Printed right after the main-health gate's echoed output when that gate exits 1 in the repair mode: the
# gate's own MAIN IS RED line is shown unchanged, and this line says how it bears on this run.
REPAIR_RED_NOTE = ("NOTE: REPAIR-RED-MAIN (--repair-red-main): the main-health result above is the gate's own "
                   "output, shown unchanged; in this mode a red main is the condition for landing this one "
                   "merge-train pull request, not a refusal, and the run goes on to its remaining gates and "
                   "reads. The gate's advice holds for every other pull request: merge nothing else onto main "
                   "until it is green.")


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
                                 "--expect-base SHA, --train or --not-train, --require-status CONTEXT and "
                                 "--repair-red-main are accepted; no option is passed through)".format(_shown(name)))
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
    if REPAIR_OPTION in options and "--not-train" in options:
        return EXIT_REFUSED, ("REFUSED: {} lands only a merge-train pull request (--train); a pull request that "
                              "is not a train cannot land while main is red".format(REPAIR_OPTION))
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
            "disable them in the repository settings so squash is the only allowed method. These settings "
            "let this run go on to its remaining checks; it sends a squash request only, and only if every "
            "check before the request passes; a check after the merge can still fail (exit 1 or 2 after a "
            "merge), so only exit 0 confirms the landing.".format(repo, " and ".join(extras)))
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


def judge_branch_tip(doc, branch):
    """The full commit id refs/heads/BRANCH points at, from a ref read that names that ref and a commit;
    CannotEvaluate otherwise."""
    obj = doc.get("object")
    if doc.get("ref") != "refs/heads/" + branch or not isinstance(obj, dict) or obj.get("type") != "commit":
        raise CannotEvaluate("the read of {}'s tip does not name refs/heads/{} pointing at a commit".format(
            branch, branch))
    tip = _sha_at(obj, "sha")
    if tip is None:
        raise CannotEvaluate("the read of {}'s tip carries no full commit id".format(branch))
    return tip


def judge_contains(doc, sha):
    """True when the compare read of SHA...TIP shows SHA an ancestor of TIP (or TIP itself): its base commit
    is SHA, behind_by is 0 and the merge base is SHA; False when behind_by is positive and the merge base
    is another commit; CannotEvaluate for anything else."""
    behind = doc.get("behind_by")
    if _sha_at(doc, "base_commit", "sha") != sha or type(behind) is not int or behind < 0:
        raise CannotEvaluate("the compare read from {} carries no usable base commit or behind_by".format(sha))
    merge_base = _sha_at(doc, "merge_base_commit", "sha")
    if merge_base is None:
        raise CannotEvaluate("the compare read from {} carries no full merge base id".format(sha))
    if behind == 0 and merge_base == sha:
        return True
    if behind > 0 and merge_base != sha:
        return False
    raise CannotEvaluate("the compare read from {} is inconsistent (behind_by {}, merge base {})".format(
        sha, behind, merge_base))


# ---------- the landing ----------

class _Run:
    """One landing run: the bound target, the I/O it goes through, and the lines it writes."""

    def __init__(self, api, gate, origin, out, err):
        self.api, self.gate, self.origin, self.out, self.err = api, gate, origin, out, err
        # Set immediately before the merge request is handed to the API; from then on every way out that
        # is not a read-back result is an UNKNOWN OUTCOME, never "nothing was merged".
        self.request_sent = False

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

    def run_gate(self, repo, args, what):
        """tools/ci-status.sh with args, bound to the validated repo (the gate binds the API host)."""
        try:
            rc, text = self.gate(repo, args)
        except subprocess.TimeoutExpired:
            raise CannotEvaluate("{}: tools/ci-status.sh did not finish in time".format(what)) from None
        except OSError as exc:
            raise CannotEvaluate("{}: tools/ci-status.sh could not start ({})".format(
                what, type(exc).__name__)) from None
        for line in text.splitlines():
            self.out("  | " + line)
        return rc, text.splitlines()

    def gates(self, repo, opts):
        rc, _lines = self.run_gate(repo, ["--main-health"], "main health")
        if opts.get(REPAIR_OPTION):
            # THE RED-MAIN REPAIR MODE: main must be red; green is a refusal, anything else cannot evaluate.
            if rc == 0:
                raise Refused("main is green (tools/ci-status.sh --main-health exit 0); {} lands only while "
                              "main is red: land through the normal path, without {}".format(
                                  REPAIR_OPTION, REPAIR_OPTION))
            if rc != 1:
                raise CannotEvaluate("main's health cannot be evaluated (tools/ci-status.sh --main-health exit "
                                     "{})".format(rc))
            self.out(REPAIR_RED_NOTE)
        else:
            if rc == 1:
                raise Refused("main is red (tools/ci-status.sh --main-health exit 1)")
            if rc != 0:
                raise CannotEvaluate("main's health cannot be evaluated (tools/ci-status.sh --main-health exit "
                                     "{})".format(rc))
        args = [opts["--head"], "--require-base", opts["--base"]]
        if "--require-status" in opts:
            args += ["--pr", opts["--pr"], "--require-status", opts["--require-status"]]
        rc, lines = self.run_gate(repo, args, "head CI")
        if rc == 1:
            raise Refused("the head's CI does not pass (tools/ci-status.sh exit 1)")
        if rc != 0:
            raise CannotEvaluate("the head's CI cannot be evaluated (tools/ci-status.sh exit {})".format(rc))
        if not lines or lines[0] != "COMMIT: " + opts["--head"]:
            raise CannotEvaluate("tools/ci-status.sh did not report the checked head as its COMMIT")
        if "--require-status" in opts and lines[-1] != "MERGE-SHA: " + opts["--head"]:
            raise CannotEvaluate("tools/ci-status.sh did not print MERGE-SHA for the checked head")
        if opts.get(REPAIR_OPTION):
            # The repair mode also requires that no Quality run on the head's tree failed.
            rc, lines = self.run_gate(repo, ["--match-tree", "--head", opts["--head"]], "head tree")
            if rc == 1:
                raise Refused("the train's tree has a failed Quality run or no successful one (tools/ci-status.sh "
                              "--match-tree exit 1); {} lands only a train whose own run on this head is green "
                              "with no failed run on the same tree".format(REPAIR_OPTION))
            if rc != 0:
                raise CannotEvaluate("the train's tree runs cannot be evaluated (tools/ci-status.sh --match-tree "
                                     "exit {})".format(rc))
            if not lines or lines[0] != "COMMIT: " + opts["--head"]:
                raise CannotEvaluate("tools/ci-status.sh --match-tree did not report the checked head as its COMMIT")

    def repair_tip(self, repo, opts, checked):
        """THE RED-MAIN REPAIR MODE's tip condition: main's tip, read now, is the checked base commit."""
        tip = judge_branch_tip(read_json(self.api, "repos/{}/git/ref/heads/{}".format(repo, opts["--base"]),
                                         "main tip (repair)"), opts["--base"])
        if tip != checked["base"]:
            raise Refused("{}'s tip is {}, not the pull request's base {}; {} lands only a train based on main's "
                          "current tip: update the train on it, check it again and run with that "
                          "--expect-base".format(opts["--base"], tip, checked["base"], REPAIR_OPTION))

    def submit(self, repo, opts, checked):
        """The one merge request; returns (exit code, landing sha or None)."""
        if opts["--method"] != ALLOWED_METHOD:  # the final assertion, immediately before the request
            raise Refused("merge method is not squash at submission")
        payload = squash_payload(opts, checked)
        endpoint = "repos/{}/pulls/{}/merge".format(repo, opts["--pr"])
        self.request_sent = True
        try:
            rc, out, err = self.api.put(endpoint, payload)
        except BaseException as exc:  # a timeout, a failed start, an undecodable reply, an interruption
            # (KeyboardInterrupt, SystemExit): the request may have landed
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
            # The parent alone: whether the tree is the checked one is judged, and reported, on its own.
            failed.append("MERGED ONTO A MOVED BASE: landing commit {}'s parent is {}, not the checked base "
                          "commit {}".format(sha, parent_ids[0], checked["base"]))
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
        self.out("MERGED: #{} squashed onto {} at {} as {}, tree {} (the head's), message {}{}".format(
            opts["--pr"], opts["--base"], checked["base"], sha, landed_tree, _shown(checked["text"][0], 120),
            " (red-main repair mode, {}: main was red before the request)".format(REPAIR_OPTION)
            if opts.get(REPAIR_OPTION) else ""))
        try:
            rc, _lines = self.run_gate(repo, ["--main-health", "--merge-commit", sha], "post-merge main health")
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate: {}".format(exc))
            return EXIT_CANNOT
        if rc == 1:
            self.out("RESULT: LANDED, MAIN IS RED: main's push run for {} did not pass".format(sha))
            return EXIT_REFUSED
        if rc != 0:
            self.err("ERROR: cannot evaluate main's push run for {} (tools/ci-status.sh exit {})".format(sha, rc))
            return EXIT_CANNOT
        # A green push run is a historical fact; that main contains the landing NOW is read separately,
        # from main's tip as it stands after the wait, so a main rewritten meanwhile is not reported as
        # holding it.
        try:
            tip = judge_branch_tip(read_json(self.api, "repos/{}/git/ref/heads/{}".format(repo, opts["--base"]),
                                             "main tip (after)"), opts["--base"])
            contained = judge_contains(read_json(self.api, "repos/{}/compare/{}...{}".format(repo, sha, tip),
                                                 "main containment (after)"), sha)
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate whether {} still contains {}: {}; main's push run for it is "
                     "green".format(opts["--base"], sha, exc))
            return EXIT_CANNOT
        if not contained:
            self.out("RESULT: LANDED, NOT ON {}: {}'s tip {}, read after main's push run for {} went green, "
                     "does not contain it; read {} again and reconcile before relying on the landing".format(
                         opts["--base"].upper(), opts["--base"], tip, sha, opts["--base"]))
            return EXIT_REFUSED
        self.out("RESULT: LANDED: {} is on {} ({}'s tip {}, read after main's push run for it went green, "
                 "contains it) and main's push run for it is green".format(sha, opts["--base"], opts["--base"],
                                                                           tip))
        return EXIT_OK

    def land(self, opts):
        try:
            repo = repo_from_origin(self.origin())
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate: {}".format(exc))
            return EXIT_CANNOT
        self.out("TARGET: {} pull request #{} into {} at head {} on base commit {}".format(
            repo, opts["--pr"], opts["--base"], opts["--head"], opts["--expect-base"]))
        self.out(BOUNDARY)
        if opts.get(REPAIR_OPTION):
            self.out(REPAIR_MODE_LINE)
        try:
            verdict, first = self.snapshot(repo, opts)
            if verdict[0] == "warn":
                self.err(verdict[2])
            self.gates(repo, opts)
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
            if opts.get(REPAIR_OPTION):
                self.repair_tip(repo, opts, checked)
        except Refused as exc:
            self.err("REFUSED: {}; nothing was merged".format(exc))
            return EXIT_REFUSED
        except CannotEvaluate as exc:
            self.err("ERROR: cannot evaluate: {}; nothing was merged".format(exc))
            return EXIT_CANNOT
        except BaseException as exc:  # an error nothing judged, or an interruption: never a traceback
            return self.interrupted(opts, None, exc)
        sha = None
        try:
            code, sha = self.submit(repo, opts, checked)
            if code != EXIT_OK:
                return code
            return self.confirm(repo, opts, sha, checked)
        except BaseException as exc:  # from the request on: never a traceback, always the unknown outcome
            if isinstance(exc, Refused) and not self.request_sent:  # the final assertion, before the request
                self.err("REFUSED: {}; nothing was merged".format(exc))
                return EXIT_REFUSED
            return self.interrupted(opts, sha, exc)

    def interrupted(self, opts, sha, exc):
        """Exit 2 for an unexpected error or an interruption (KeyboardInterrupt, SystemExit and every other
        BaseException), worded by the phase: nothing merged before the request was sent, an UNKNOWN
        OUTCOME to reconcile from then on."""
        what = type(exc).__name__
        if not self.request_sent:
            self.err("ERROR: cannot evaluate: an unexpected {} before the merge request; nothing was "
                     "merged".format(what))
        elif sha is None:
            self.err("UNKNOWN OUTCOME: the squash request for #{} was sent and an unexpected {} ended its "
                     "handling; do not retry: read pull request #{} to reconcile before any further "
                     "landing".format(opts["--pr"], what, opts["--pr"]))
        else:
            self.err("UNKNOWN OUTCOME: merged as {} by the response, but the post-merge check was ended by an "
                     "unexpected {}; do not retry: read pull request #{} to reconcile before any further "
                     "landing".format(sha, what, opts["--pr"]))
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


def _gate_env(repo):
    """The gate child's environment: this process's, with the API host fixed to API_HOST (GH_HOST), the
    repository fixed to the validated OWNER/REPO (CI_STATUS_REPO, which ci-status.sh requires its own
    origin to equal, exit 2 otherwise) and GH_REPO removed. CannotEvaluate when repo is not a validated
    OWNER/REPO, so the child is never started on a binding this tool did not establish."""
    owner, sep, name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
    if not sep or not OWNER_RE.fullmatch(owner) or not REPO_RE.fullmatch(name) or name in (".", ".."):
        raise CannotEvaluate("the gate cannot be bound to the validated repository, so tools/ci-status.sh "
                             "is not run")
    env = {key: value for key, value in os.environ.items() if key not in GATE_ENV_REMOVED}
    env["GH_HOST"] = API_HOST
    env["CI_STATUS_REPO"] = repo
    return env


def _live_gate(repo, args):
    """tools/ci-status.sh with args, its every gh api call pinned to API_HOST (GATE_HOST_OPTION) and its
    repository bound to repo (_gate_env)."""
    env = _gate_env(repo)
    proc = subprocess.run(["bash", str(ROOT / CI_STATUS_REL), GATE_HOST_OPTION, API_HOST] + list(args),
                          capture_output=True, text=True, timeout=GATE_TIMEOUT, cwd=str(ROOT), env=env,
                          check=False)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode, proc.stdout


def _print_out(line):
    print(line, flush=True)


def _print_err(line):
    print(line, file=sys.stderr, flush=True)


def land_cli(argv, api=None, gate=None, origin=None, out=_print_out, err=_print_err):
    """The landing for argv through the given I/O (the live gh, ci-status.sh and git by default). Every
    way out is an exit code: anything that escapes the landing's own handling (an interruption included)
    is exit 2, worded by whether the merge request was sent."""
    run = None
    try:
        opts = parse_args(argv)
        if isinstance(opts, tuple):
            code, message = opts
            err(message if code == EXIT_REFUSED else "error: " + message + "; nothing was read or merged")
            return code
        # `is None`, never truthiness: a falsy stub must not fall through to the live gh, git or ci-status.sh.
        run = _Run(_GhApi() if api is None else api, _live_gate if gate is None else gate,
                   _live_origin if origin is None else origin, out, err)
        return run.land(opts)
    except CannotEvaluate as exc:
        if run is not None and run.request_sent:
            return run.interrupted(opts, None, exc)
        err("ERROR: cannot evaluate: {}; nothing was merged".format(exc))
        return EXIT_CANNOT
    except BaseException as exc:
        if run is not None:
            return run.interrupted(opts, None, exc)
        err("ERROR: cannot evaluate: an unexpected {} before any read; nothing was read or merged".format(
            type(exc).__name__))
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
FX_TIP = "34" * 20
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


def _tip_ref(sha=FX_TIP, ref="refs/heads/main", kind="commit"):
    return {"ref": ref, "object": {"sha": sha, "type": kind}}


def _contains(behind=0, base=FX_LANDED, merge_base=FX_LANDED):
    """The compare read FX_LANDED...tip: by default the tip contains the landing commit."""
    return {"behind_by": behind, "ahead_by": 2, "base_commit": {"sha": base}, "merge_base_commit": {"sha": merge_base}}


def _gets(settings=None, pr=None, compare=None, after=None, commit=None, head_commit=None, repo=FX_REPO,
          tip=None, contains=None):
    pr_answers = [pr if pr is not None else _pr()]
    if after is not None:
        pr_answers = pr_answers * 2 + [after]
    return {"repos/" + repo: settings if settings is not None else _settings(),
            "repos/{}/pulls/7".format(repo): pr_answers,
            "repos/{}/compare/main...{}".format(repo, FX_HEAD): compare if compare is not None else _compare(),
            "repos/{}/commits/{}".format(repo, FX_LANDED): commit if commit is not None else _commit(),
            "repos/{}/commits/{}".format(repo, FX_HEAD):
                head_commit if head_commit is not None else _commit(FX_HEAD, ("aa" * 20,)),
            "repos/{}/git/ref/heads/main".format(repo): tip if tip is not None else _tip_ref(),
            "repos/{}/compare/{}...{}".format(repo, FX_LANDED, FX_TIP):
                contains if contains is not None else _contains()}


def _gate_fn(calls, main=0, head=0, post=0, commit_line=None, merge_sha_line=None, repos=None, main_raises=None,
             post_raises=None, tree=0, tree_commit_line=None, tree_raises=None):
    """A recording gate stub: calls gets each argument tuple, repos (when given) the repository each call
    was bound to; main_raises and post_raises are raised by the main-health and post-merge calls; tree and
    tree_commit_line answer the repair mode's --match-tree call, and tree_raises is raised by it."""
    def gate(repo, args):
        calls.append(tuple(args))
        if repos is not None:
            repos.append(repo)
        if args == ["--main-health"]:
            if main_raises is not None:
                raise main_raises
            return main, "RESULT: main fixture\n"
        if args[:1] == ["--main-health"]:
            if post_raises is not None:
                raise post_raises
            return post, "RESULT: post-merge fixture\n"
        if args[:1] == ["--match-tree"]:
            if tree_raises is not None:
                raise tree_raises
            return tree, (tree_commit_line or ("COMMIT: " + FX_HEAD)) + "\nRESULT: tree fixture\n"
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
                      ("GET", "repos/{}/commits/{}".format(FX_REPO, FX_HEAD)),
                      ("GET", "repos/{}/git/ref/heads/main".format(FX_REPO)),
                      ("GET", "repos/{}/compare/{}...{}".format(FX_REPO, FX_LANDED, FX_TIP))]
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


def _self_test_exit_paths():
    """Every exit path bound and checked: an interruption or other BaseException at each phase (before any
    read, before the request, during the gates, during the request, during each post-merge read, during
    the post-merge gate) ends in exit 2 worded by the phase, each beside its flip, the same fixture
    uninterrupted, landing; main must contain the landing commit at its tip as read after the wait; a moved
    parent and a different tree are each reported on their own line; the gates are bound to the validated
    repository; the settings warning does not claim a landing."""
    landed = "repos/{}/commits/{}".format(FX_REPO, FX_LANDED)
    tip_read = "repos/{}/git/ref/heads/main".format(FX_REPO)
    contains_read = "repos/{}/compare/{}...{}".format(FX_REPO, FX_LANDED, FX_TIP)
    nothing = "nothing was merged"
    unknown = "UNKNOWN OUTCOME"
    for name, gets, put, gate_kwargs, origin, code_expected, sent, needle in (
            ("interrupt/origin-read-cannot", _gets(after=_merged_after()), None, {}, KeyboardInterrupt(), 2, 0,
             nothing),
            ("interrupt/settings-read-cannot", _gets(settings=KeyboardInterrupt(), after=_merged_after()), None,
             {}, FX_ORIGIN, 2, 0, nothing),
            ("interrupt/main-gate-cannot", _gets(after=_merged_after()), None, {"main_raises": KeyboardInterrupt()},
             FX_ORIGIN, 2, 0, nothing),
            ("interrupt/before-request-system-exit-cannot",
             dict(_gets(after=_merged_after()), **{"repos/" + FX_REPO: [_settings(), SystemExit(0)]}), None, {},
             FX_ORIGIN, 2, 0, nothing),
            ("interrupt/during-request-unknown", _gets(after=_merged_after()), KeyboardInterrupt(), {}, FX_ORIGIN, 2,
             1, "squash request for #7 was sent"),
            ("interrupt/during-request-system-exit-unknown", _gets(after=_merged_after()), SystemExit(0), {},
             FX_ORIGIN, 2, 1, "squash request for #7 was sent"),
            ("interrupt/readback-unknown",
             dict(_gets(), **{"repos/{}/pulls/7".format(FX_REPO): [_pr(), _pr(), KeyboardInterrupt()]}), None, {},
             FX_ORIGIN, 2, 1, "merged as " + FX_LANDED),
            ("interrupt/landing-read-unknown", dict(_gets(after=_merged_after()), **{landed: KeyboardInterrupt()}),
             None, {}, FX_ORIGIN, 2, 1, "merged as " + FX_LANDED),
            ("interrupt/post-merge-gate-unknown", _gets(after=_merged_after()), None,
             {"post_raises": KeyboardInterrupt()}, FX_ORIGIN, 2, 1, "merged as " + FX_LANDED),
            ("interrupt/main-tip-read-unknown", dict(_gets(after=_merged_after()), **{tip_read: KeyboardInterrupt()}),
             None, {}, FX_ORIGIN, 2, 1, "merged as " + FX_LANDED),
            ("interrupt/containment-read-unknown",
             dict(_gets(after=_merged_after()), **{contains_read: SystemExit(0)}), None, {}, FX_ORIGIN, 2, 1,
             "merged as " + FX_LANDED),
            ("interrupt/flip-uninterrupted-lands", _gets(after=_merged_after()), None, {}, FX_ORIGIN, 0, 1,
             "RESULT: LANDED:"),
            # main must contain the landing at its tip, read after the post-merge gate
            ("after/main-contains-landing-lands", _gets(after=_merged_after(), contains=_contains()), None, {},
             FX_ORIGIN, 0, 1, "contains it) and main's push run for it is green"),
            ("after/main-tip-is-landing-lands",
             dict(_gets(after=_merged_after(), tip=_tip_ref(FX_LANDED)),
                  **{"repos/{}/compare/{}...{}".format(FX_REPO, FX_LANDED, FX_LANDED): _contains(behind=0)}), None,
             {}, FX_ORIGIN, 0, 1, "RESULT: LANDED:"),
            ("after/main-rewritten-not-on-main-refused",
             _gets(after=_merged_after(), contains=_contains(behind=1, merge_base="77" * 20)), None, {}, FX_ORIGIN,
             1, 1, "RESULT: LANDED, NOT ON MAIN"),
            ("after/main-tip-unreadable-cannot", _gets(after=_merged_after(), tip=(1, "", "gh: (HTTP 502)")), None,
             {}, FX_ORIGIN, 2, 1, "still contains"),
            ("after/main-tip-other-ref-cannot", _gets(after=_merged_after(), tip=_tip_ref(ref="refs/heads/dev")),
             None, {}, FX_ORIGIN, 2, 1, "still contains"),
            ("after/main-tip-not-commit-cannot", _gets(after=_merged_after(), tip=_tip_ref(kind="tag")), None, {},
             FX_ORIGIN, 2, 1, "still contains"),
            ("after/containment-unreadable-cannot", _gets(after=_merged_after(), contains=(1, "", "gh: timeout")),
             None, {}, FX_ORIGIN, 2, 1, "still contains"),
            ("after/containment-other-base-cannot", _gets(after=_merged_after(), contains=_contains(base="11" * 20)),
             None, {}, FX_ORIGIN, 2, 1, "still contains"),
            ("after/containment-inconsistent-cannot",
             _gets(after=_merged_after(), contains=_contains(behind=0, merge_base="77" * 20)), None, {}, FX_ORIGIN,
             2, 1, "still contains")):
        code, api, gates, out, err = _land(gets, put=put, origin=origin, **gate_kwargs)
        lines = out + err
        check(name, code == code_expected and len(api.puts()) == sent
              and any(needle in line for line in lines)
              and (code_expected == 0) == any(line.startswith("RESULT: LANDED:") for line in lines)
              and (code_expected != 2 or sent == 0 or any(unknown in line for line in err)
                   or any("ERROR: cannot evaluate whether" in line for line in err)),
              (code, api.calls, gates, out, err))
    # The containment is read only after the post-merge gate is green: a red or unevaluable gate reads no tip.
    for name, post, code_expected in (("after/red-gate-reads-no-tip", 1, 1), ("after/unknown-gate-reads-no-tip", 2, 2),
                                      ("after/green-gate-reads-tip-flip", 0, 0)):
        code, api, _gates, _out, _err = _land(_gets(after=_merged_after()), post=post)
        read_tip = any(call[1] == tip_read for call in api.calls)
        check(name, code == code_expected and read_tip == (code_expected == 0), (code, api.calls))

    # A moved parent and a different tree are separate facts, each on its own RESULT line.
    moved = ("11" * 20,)
    for name, commit, moved_line, tree_line in (
            ("after/moved-parent-same-tree-no-tree-claim", _commit(parents=moved), True, False),
            ("after/moved-parent-other-tree-both-reported", _commit(parents=moved, tree="13" * 20), True, True),
            ("after/checked-parent-other-tree-tree-only", _commit(tree="13" * 20), False, True),
            ("after/checked-parent-same-tree-flip-lands", _commit(), False, False)):
        code, _api, _gates, out, _err = _land(_gets(after=_merged_after(), commit=commit))
        got = (code, any("MOVED BASE" in line for line in out), any("TREE DIFFERS" in line for line in out),
               any("tree was not" in line for line in out))
        check(name, got == (0 if not (moved_line or tree_line) else 1, moved_line, tree_line, False), (got, out))

    # Every gate call is bound to the validated repository, the adopter's own included.
    adopter = "adopter-org/their-repo"
    adopter_pr = _pr(base={"repo": {"full_name": adopter}}, head={"repo": {"full_name": adopter}})
    for name, gets, origin, repo in (
            ("land/gates-bound-to-validated-repo", _gets(after=_merged_after()), FX_ORIGIN, FX_REPO),
            ("land/gates-bound-to-adopter-repo",
             _gets(settings=_settings(full_name=adopter), pr=adopter_pr, after=_merged_after(), repo=adopter),
             b"git@github.com:adopter-org/their-repo.git\n", adopter)):
        repos = []
        code, _api, gates, _out, _err = _land(gets, origin=origin, repos=repos)
        check(name, code == 0 and len(repos) == len(gates) == 3 and set(repos) == {repo}, (code, repos, gates))

    # The settings warning states what the settings permit, not that a landing happened.
    warning = evaluate_policy("squash", _settings(merge=True), FX_REPO)[2]
    check("policy/warning-claims-no-landing",
          "proceeds" not in warning and "lands" not in warning
          and "only if every check before the request passes" in warning
          and "only exit 0 confirms the landing" in warning, warning)


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

    # The gate child: under an ambient environment that names another host and repository, and under one
    # that names none (the flip), its command line pins the host and its environment fixes the host and the
    # validated repository; a repository this tool did not validate starts no child (cannot evaluate).
    gate_runs = []

    def fake_gate_run(argv, **kwargs):
        gate_runs.append((list(argv), kwargs.get("env")))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    hostile = {"GH_HOST": "ghe.example.invalid", "GH_REPO": "other/repo", "CI_STATUS_REPO": "other/repo"}
    script = str(ROOT / CI_STATUS_REL)
    for name, ambient, clear in (("live/gate-pins-host-and-repo-over-ambient", hostile, False),
                                 ("live/gate-pins-host-and-repo-flip-clean-env", {}, True)):
        del gate_runs[:]
        with mock.patch.dict(os.environ, ambient, clear=clear), mock.patch.object(subprocess, "run", fake_gate_run):
            _live_gate(FX_REPO, ["--main-health"])
        argv, env = gate_runs[0] if len(gate_runs) == 1 else ([], None)
        check(name, argv == ["bash", script, GATE_HOST_OPTION, API_HOST, "--main-health"] and isinstance(env, dict)
              and env.get("GH_HOST") == API_HOST and "GH_REPO" not in env and env.get("CI_STATUS_REPO") == FX_REPO,
              gate_runs)
    for name, repo, started in (("live/gate-unvalidated-repo-refused", "not a repository", False),
                                ("live/gate-other-host-repo-refused", "ghe.example.invalid/x/y", False),
                                ("live/gate-validated-repo-flip-starts", FX_REPO, True)):
        del gate_runs[:]
        with mock.patch.object(subprocess, "run", fake_gate_run):
            try:
                _live_gate(repo, ["--main-health"])
                refused = False
            except CannotEvaluate:
                refused = True
        check(name, refused != started and len(gate_runs) == (1 if started else 0), (refused, gate_runs))


def _self_test_repair():
    """THE RED-MAIN REPAIR MODE: with --repair-red-main a green train on main's tip lands on a red main;
    it is refused on a green main, a red train run (on the head or on its tree), a base that is not main's
    tip, a pull request that is not a train, and by every other normal gate; the normal path still refuses
    a red main and never runs the repair mode's tree gate or tip read; the post-merge check still reports;
    the repair mode's NOTE line follows the main-health gate's unchanged output only when that gate is red."""
    endpoint_put = "repos/{}/pulls/7/merge".format(FX_REPO)
    tip_read = "repos/{}/git/ref/heads/main".format(FX_REPO)
    tree_gate = ("--match-tree", "--head", FX_HEAD)
    repair = FX_ARGS + [REPAIR_OPTION]
    non_train = _pr(head={"ref": "feature-x"}, labels=[])

    def repair_gets(tip=FX_BASE, **changes):
        """The fixture with main's tip read twice: before the request (the repair mode's tip condition)
        and after the post-merge gate (the containment read)."""
        gets = _gets(after=_merged_after(), **changes)
        gets[tip_read] = [tip if isinstance(tip, (tuple, dict, BaseException)) else _tip_ref(tip), _tip_ref()]
        return gets

    # The repair landing: every normal read and gate, the tree gate, the tip read, then the one request.
    code, api, gates, out, err = _land(repair_gets(), argv=repair, main=1)
    order = [call[:2] for call in api.calls]
    check("repair/green-train-on-red-main-lands",
          code == 0 and api.puts() == [("PUT", endpoint_put, _good_put_payload())]
          and gates == [("--main-health",), (FX_HEAD, "--require-base", "main"), tree_gate,
                        ("--main-health", "--merge-commit", FX_LANDED)]
          and order[8] == ("GET", tip_read) and order[9] == ("PUT", endpoint_put)
          and any(line.startswith("RESULT: LANDED:") for line in out), (code, api.calls, gates, out, err))
    check("repair/mode-recorded-in-output",
          code == 0 and REPAIR_MODE_LINE in out and out.index(REPAIR_MODE_LINE) == out.index(BOUNDARY) + 1
          and any(line.startswith("MERGED: ") and REPAIR_OPTION in line for line in out), out)
    # The main-health gate's echoed output is shown unchanged, and the NOTE line follows it at once.
    check("repair/red-main-note-beside-gate-output",
          code == 0 and "  | RESULT: main fixture" in out and out.count(REPAIR_RED_NOTE) == 1
          and out.index(REPAIR_RED_NOTE) == out.index("  | RESULT: main fixture") + 1, out)
    # --require-status applies unchanged in the repair mode: forwarded to the head gate, before the tree gate.
    code, api, gates, out, err = _land(repair_gets(), argv=repair + ["--require-status", "local-quality"], main=1)
    check("repair/require-status-forwarded-lands",
          code == 0 and api.puts() == [("PUT", endpoint_put, _good_put_payload())]
          and gates == [("--main-health",),
                        (FX_HEAD, "--require-base", "main", "--pr", "7", "--require-status", "local-quality"),
                        tree_gate, ("--main-health", "--merge-commit", FX_LANDED)], (code, api.calls, gates, out, err))
    head_commit = "repos/{}/commits/{}".format(FX_REPO, FX_HEAD)
    head_reads = [_commit(FX_HEAD, ("aa" * 20,), tree="13" * 20), _commit(FX_HEAD, ("aa" * 20,))]
    same_reads = [_commit(FX_HEAD, ("aa" * 20,))] * 2
    interrupted_before = "an unexpected KeyboardInterrupt before the merge request; nothing was merged"

    for name, gets, argv, gate_kwargs, code_expected, sent, needle in (
            # the red condition: main's newest push run must be red
            ("repair/green-main-refused", repair_gets(), repair, {"main": 0}, 1, 0, "main is green"),
            ("repair/main-unknown-cannot", repair_gets(), repair, {"main": 2}, 2, 0, "cannot be evaluated"),
            ("repair/red-main-flip-lands", repair_gets(), repair, {"main": 1}, 0, 1, "RESULT: LANDED:"),
            # the green condition: the train's own run on the head, and no failed run on its tree
            ("repair/train-head-run-red-refused", repair_gets(), repair, {"main": 1, "head": 1}, 1, 0,
             "head's CI does not pass"),
            ("repair/train-head-run-unknown-cannot", repair_gets(), repair, {"main": 1, "head": 2}, 2, 0,
             "head's CI cannot be evaluated"),
            ("repair/train-tree-failed-run-refused", repair_gets(), repair, {"main": 1, "tree": 1}, 1, 0,
             "no failed run on the same tree"),
            ("repair/train-tree-unknown-cannot", repair_gets(), repair, {"main": 1, "tree": 2}, 2, 0,
             "tree runs cannot be evaluated"),
            ("repair/train-tree-other-commit-cannot", repair_gets(), repair,
             {"main": 1, "tree_commit_line": "COMMIT: " + "ee" * 20}, 2, 0, "--match-tree did not report"),
            # a timeout or an interruption in the tree gate: cannot evaluate, nothing sent
            ("repair/train-tree-gate-timeout-cannot", repair_gets(), repair,
             {"main": 1, "tree_raises": subprocess.TimeoutExpired("ci-status.sh", GATE_TIMEOUT)}, 2, 0,
             "head tree: tools/ci-status.sh did not finish in time; nothing was merged"),
            ("repair/train-tree-gate-interrupted-cannot", repair_gets(), repair,
             {"main": 1, "tree_raises": KeyboardInterrupt()}, 2, 0, interrupted_before),
            ("repair/train-tree-green-flip-lands", repair_gets(), repair, {"main": 1, "tree": 0}, 0, 1,
             "RESULT: LANDED:"),
            # the tip condition: the pull request's base is main's current tip
            ("repair/base-not-main-tip-refused", repair_gets(tip="11" * 20), repair, {"main": 1}, 1, 0,
             "not the pull request's base"),
            ("repair/main-tip-unreadable-cannot", repair_gets(tip=(1, "", "gh: (HTTP 502)")), repair, {"main": 1},
             2, 0, "main tip (repair)"),
            ("repair/main-tip-other-ref-cannot", repair_gets(tip=_tip_ref(FX_BASE, ref="refs/heads/dev")), repair,
             {"main": 1}, 2, 0, "does not name refs/heads/main"),
            # a timeout or an interruption in the tip read: cannot evaluate, nothing sent
            ("repair/main-tip-read-timeout-cannot",
             repair_gets(tip=subprocess.TimeoutExpired("gh", READ_TIMEOUT)), repair, {"main": 1}, 2, 0,
             "main tip (repair): the read timed out; nothing was merged"),
            ("repair/main-tip-read-interrupted-cannot", repair_gets(tip=KeyboardInterrupt()), repair, {"main": 1},
             2, 0, interrupted_before),
            ("repair/base-is-main-tip-flip-lands", repair_gets(tip=FX_BASE), repair, {"main": 1}, 0, 1,
             "RESULT: LANDED:"),
            # the train condition
            ("repair/not-train-option-refused", repair_gets(pr=non_train), _argv(train="--not-train",
                                                                                 extra=[REPAIR_OPTION]),
             {"main": 1}, 1, 0, "lands only a merge-train pull request"),
            ("repair/non-train-pr-refused", repair_gets(pr=non_train), repair, {"main": 1}, 1, 0,
             "is not a labelled"),
            ("repair/train-label-missing-refused", repair_gets(pr=_pr(labels=[])), repair, {"main": 1}, 1, 0,
             "is not a labelled"),
            ("repair/train-pr-flip-lands", repair_gets(pr=_pr()), repair, {"main": 1}, 0, 1, "RESULT: LANDED:"),
            # every other gate of the normal path applies unchanged
            ("repair/behind-main-refused", repair_gets(compare=_compare(behind=1)), repair, {"main": 1}, 1, 0,
             "behind"),
            ("repair/expect-base-differs-refused", repair_gets(), _argv(expect="11" * 20, extra=[REPAIR_OPTION]),
             {"main": 1}, 1, 0, "not the expected base"),
            ("repair/attestation-pr-refused",
             repair_gets(compare=_compare(files=FX_FILES + [{"filename": ATTESTATION_PATH, "status": "modified"}])),
             repair, {"main": 1}, 1, 0, "step 6a"),
            ("repair/empty-body-refused", repair_gets(pr=_pr(body="")), repair, {"main": 1}, 1, 0, "empty body"),
            ("repair/squash-disabled-refused", repair_gets(settings=_settings(squash=False)), repair, {"main": 1},
             1, 0, "squash merging is disabled"),
            ("repair/merge-method-refused", repair_gets(), _argv(method="merge", extra=[REPAIR_OPTION]),
             {"main": 1}, 1, 0, "squash only"),
            ("repair/head-moved-during-gates-refused",
             dict(repair_gets(), **{"repos/{}/pulls/7".format(FX_REPO): [_pr(), _pr(head={"sha": "ee" * 20})]}),
             repair, {"main": 1}, 1, 0, "not the checked head"),
            ("repair/require-status-other-sha-cannot", repair_gets(), repair + ["--require-status", "local-quality"],
             {"main": 1, "merge_sha_line": "MERGE-SHA: " + "ee" * 20}, 2, 0, "did not print MERGE-SHA"),
            ("repair/head-tree-changed-during-gates-refused", dict(repair_gets(), **{head_commit: head_reads}),
             repair, {"main": 1}, 1, 0, "the head's tree changed while the gates ran"),
            ("repair/head-tree-unchanged-flip-lands", dict(repair_gets(), **{head_commit: same_reads}), repair,
             {"main": 1}, 0, 1, "RESULT: LANDED:"),
            ("repair/repeated-option-refused", repair_gets(), repair + [REPAIR_OPTION], {"main": 1}, 2, 0,
             "more than once"),
            # the post-merge main-health check still runs and reports
            ("repair/post-merge-main-red-reported", repair_gets(), repair, {"main": 1, "post": 1}, 1, 1,
             "RESULT: LANDED, MAIN IS RED"),
            ("repair/post-merge-main-unknown-reported", repair_gets(), repair, {"main": 1, "post": 2}, 2, 1,
             "cannot evaluate main's push run"),
            # the normal path is unchanged: a red main is refused, with no tree gate, tip read or MODE line
            ("repair/normal-path-red-main-refused", _gets(after=_merged_after()), FX_ARGS, {"main": 1}, 1, 0,
             "main is red"),
            ("repair/normal-path-red-main-not-train-refused", _gets(pr=non_train, after=_merged_after()),
             _argv(train="--not-train"), {"main": 1}, 1, 0, "main is red"),
            ("repair/normal-path-head-tree-changed-refused",
             dict(_gets(after=_merged_after()), **{head_commit: head_reads}), FX_ARGS, {"main": 0}, 1, 0,
             "the head's tree changed while the gates ran"),
            ("repair/normal-path-green-main-flip-lands", _gets(after=_merged_after()), FX_ARGS, {"main": 0}, 0, 1,
             "RESULT: LANDED:")):
        code, api, gates, out, err = _land(gets, argv=argv, **gate_kwargs)
        repairing = REPAIR_OPTION in argv
        parsed = not isinstance(parse_args(argv), tuple)
        tip_reads = [call for call in api.calls[:api.calls.index(api.puts()[0])] if call[1] == tip_read] \
            if api.puts() else [call for call in api.calls if call[1] == tip_read]
        check(name, code == code_expected and len(api.puts()) == sent
              and (sent == 0 or api.puts()[0][2] == _good_put_payload())
              and any(needle in line for line in out + err)
              and (REPAIR_MODE_LINE in out) == (repairing and parsed)
              and (REPAIR_RED_NOTE in out) == (repairing and ("--main-health",) in gates
                                               and gate_kwargs.get("main", 0) == 1)
              and (repairing or (tree_gate not in gates and tip_reads == []))
              and (code_expected == 0) == any(line.startswith("RESULT: LANDED:") for line in out),
              (code, api.calls, gates, out, err))


def _self_test():
    _self_test_policy()
    _self_test_binding()
    _self_test_landing()
    _self_test_properties()
    _self_test_exit_paths()
    _self_test_live_argv()
    _self_test_repair()
    got = [_main_exit(argv) for argv in (["--self-test", "--bogus"], ["--execution-report", "relative/report"],
                                         [])]
    check("cli/unrecognized-arguments-exit-2", got == [2, 2, 2], got)
    if FAILURES:
        print("SELF-TEST FAIL: merge_pr {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: merge_pr {} checks on recorded API responses with no network (the policy table "
          "with its flips: squash-only permits, each wider setting warns and, every other check green, lands "
          "by squash, squash disabled "
          "and merge or rebase are refused under narrow and broad settings, missing, null, wrong-type and "
          "other-repository settings cannot evaluate; the origin binding; every argument refusal before any "
          "read; the landing's request order and exact pinned squash payload; every pre-request refusal "
          "sends no request; a fresh read before submission governs; a rejection is never retried or "
          "replaced by another method; an unreadable or unexpected outcome is unknown; the post-merge reads "
          "(single parent, the checked base commit as that parent, the head's tree) and main's health decide "
          "the result; each landing property (head, base, parent, tree, message, method) refused before the "
          "request and reported after it, each beside its flip; a moved parent and a different tree "
          "reported apart; main's tip, read after the post-merge wait, must contain the landing; an "
          "interruption at each phase is exit 2, nothing merged before the request and an unknown outcome "
          "from it on; the step 6a attestation pull request refused; the live gh and git command lines pin "
          "the host and read every origin URL, and the gate child is bound to the validated host and "
          "repository whatever the ambient environment names; the red-main repair mode lands a green train "
          "on main's tip while main is red and is refused on a green main, a red train run, a base that is "
          "not main's tip and a pull request that is not a train, every other gate applying unchanged "
          "(--require-status and the refusal of a head tree that changed while the gates ran included), while "
          "the normal path still refuses a red main)".format(
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
