#!/usr/bin/env python3
"""Refuse a commit message that states the opposite of what the commit does to the release files.

The release files are .aiqt/manifest.toml and every file under .aiqt/release/ (the root and the announce
snippet), all written by tools/gen_manifest.py. A message that says they are not regenerated, not changed or
not staged, in a commit that changes one of them, is false; so is a message that says they are regenerated, in
a commit that changes none of them. Both have landed: a commit message reused from a proposal said the release
files were not regenerated after they had been, and a commit whose message said the manifest was regenerated
changed only a workflow file. This gate reads each commit's message and the paths it changes against its first
parent (a root commit against the empty tree) and refuses either contradiction.

  check_commit_claims.py                  the commits the CI event names, or HEAD alone outside CI
  check_commit_claims.py --range BASE HEAD
                                          every commit in BASE..HEAD (git rev-list order, oldest first)
  check_commit_claims.py --self-test      message rows, git fixture rows and a removed-check pass

COMMITS. With GITHUB_EVENT_NAME set to pull_request, the commits in base.sha..head.sha of the event file
GITHUB_EVENT_PATH names; push, before..after (an all-zero after, a branch deletion, names no commit, and an
all-zero before, a branch creation, is cannot-evaluate, because the complete range cannot be established);
workflow_dispatch or schedule, HEAD alone; any other event is cannot-evaluate. With GITHUB_EVENT_NAME unset
or empty, HEAD alone. Every revision field of the event must be a canonical 40-hex object id, and every git
call is bound to this repository (git -C ROOT) with every ambient GIT_-prefixed variable dropped.

CLAIMS. The message is read one sentence at a time. A sentence is a claim only when it names a release file as
its subject or object: .aiqt/manifest.toml, .aiqt/release or a path under it, root.txt, announce-snippet.txt,
"release" followed by manifest, root, files, records, outputs, metadata, digests, hashes, pins, artefacts or
snippet, or a bare "manifest" whose preceding word is none but the, its, and, or, plus, also, nor, with, a
comma or the start of the clause (so the hooks, gates, store or prompt-pack manifest is never a release file).
  NOT-REGENERATED: the subject, then a negated verb (is not regenerated, are not changed, was not staged,
  has not been touched, isn't updated, not modified, also with still, deliberately or intentionally), or the
  subject, then is or are or stays or remains or left, unchanged or untouched or unmodified or unstaged; the
  change itself (this commit, this change, it, or no subject at all) as subject, does not regenerate, change,
  stage, touch or update the release file; without regenerating, changing, staging or touching it; and no
  manifest change, no release-file regeneration and similar. "never" is not read as a claim: it states a
  process, not this commit.
  REGENERATED: the subject, then regenerated with or without is, are, was, were, get or gets (also, now, then
  or all may come between); and regenerate, regenerates or regenerated followed by the release file as its
  object.
Neither reading accepts a release file named more than twenty words before its verb, or one in a clause
before a comma-and, a semicolon, so, but, while, which or where.

NARROWING. A claim the message quotes or hedges is not read: a line opening with ">", a fenced block, a span in
double quotes (straight or curly), curly single quotes or backquotes, and a straight single-quoted span that
opens and closes outside a word are dropped before the read; a sentence holding a conditional, modal or
reporting word (if, when, whenever, unless, whether, once, until, would, could, might, should, says, said,
claims, claimed, claiming, states, stated, wrote, writes, message) or a residual word (residual, residue,
e.g., for example, such as, not checked) is not read; a paragraph headed by a line naming residuals, residue,
not checked, known limits or gaps, limitations, caveats or out of scope is not read; and a claim whose verb is
followed by by, from, through, via, during, at, on, under, unless, until, if, when, whenever or for is not read
(it describes a tool or a process) unless that word is followed by this commit, this change, this branch, this
patch or here, or, for by, by a generator (gen_manifest, tools/gen_manifest.py, the generator or generators).
"to regenerate" is an instruction or a purpose, not a claim. The adjective form (the regenerated manifest) is
not read.

AGGREGATES. A commit with more than one parent, or whose subject starts with "Merge train " followed by a train
name and a colon (a merge train squash, whose body is every member's message), carries claims about changes
other than its whole diff, so only the REGENERATED direction is checked on it: a member that regenerated the
release files leaves them changed in the aggregate, while a member's true NOT-REGENERATED sentence says nothing
about the others.

ALLOW MARKER. A line of the message that reads exactly "Release-claim-allow: " followed by a non-empty reason
lets that commit pass; its findings print as ALLOWED with the reason. The marker line is not read as a claim.
The marker with no reason is not honoured and is named on a NOTE line.

Exit: 0 no contradiction (or every one allowed); 1 a contradiction; 2 cannot evaluate (a git call that fails or
times out, a revision that does not resolve to a commit, a malformed or missing event, an unsupported event, a
bad argument).

RESIDUE. The read is lexical and narrow by design. A claim phrased outside the shapes above (a paraphrase such
as "no generated output moves", a claim spread over two sentences, a pronoun standing for the release files
across a sentence, a claim inside a hedged sentence or a quotation) is not read, and a sentence the shapes do
read can still state a process rather than this commit. A merge train squash is checked in the REGENERATED
direction only. The commit message is not checked before the commit exists: this gate reads committed history
(a pull request's range in CI, HEAD locally), not a commit-msg hook.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_commit_claims.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_TIMEOUT = 120  # seconds, per git call
_ZERO_SHA = "0" * 40
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_MANIFEST = ".aiqt/manifest.toml"
RELEASE_DIR = ".aiqt/release/"
ALLOW_PREFIX = "Release-claim-allow:"
_TRAIN_SUBJECT_RE = re.compile(r"^Merge train [^\s:]+:")
_MAX_SPAN_WORDS = 20


class CannotEvaluate(Exception):
    """A condition under which the gate exits 2 rather than risk a false clean."""


# ---------------------------------------------------------------- message reading

# A release file named as a noun phrase. The bare-manifest form is accepted only after a qualifier check
# (_bare_manifest_ok), so a hooks, gates, store or prompt-pack manifest is never read as a release file.
_RELEASE_NOUNS = (r"manifest(?:\s+(?:files?|outputs?|records?|artefacts?|artifacts?))?|root(?:\s+digests?)?|"
                  r"files?|records?|outputs?|metadata|digests?|hashes|pins|artefacts?|artifacts?|"
                  r"(?:announce(?:ment)?\s+)?snippets?")
_ATOM_RE = re.compile(
    r"(?P<path>\.aiqt/manifest\.toml|\.aiqt/release(?:/[\w.*-]*)?|(?:release/)?root\.txt|"
    r"(?:release/)?announce-snippet\.txt)"
    r"|(?P<release>\brelease[\s-]+(?:" + _RELEASE_NOUNS + r")\b)"
    r"|(?P<bare>\b(?:(?:pack|generated|derived)\s+)?manifest(?:\s+(?:files?|outputs?|records?|artefacts?|"
    r"artifacts?|triple|hashes|digests?|pins))?\b)",
    re.I)
_BARE_OK_BEFORE = frozenset(("", "the", "its", "and", "or", "plus", "also", "nor", "with", ","))
_WORD_BEFORE_RE = re.compile(r"([\w./-]+|,)\s*$")


def _bare_manifest_ok(text, start):
    """True when the word before a bare "manifest" phrase at text[start:] is one that leaves it the release
    manifest: nothing (the clause start), a determiner, a conjunction or a comma."""
    found = _WORD_BEFORE_RE.search(text[:start])
    return (found.group(1).lower() if found else "") in _BARE_OK_BEFORE


def names_release_file(text):
    """True when text names a release file (module docstring, CLAIMS)."""
    for match in _ATOM_RE.finditer(text):
        if match.group("bare") is None or _SEAMS["bare_qualifier"](text, match.start()):
            return True
    return False


_QUOTE_RES = (
    re.compile(r'"[^"\n]*"'),
    re.compile("“[^”]*”"),
    re.compile("‘[^’]*’"),
    re.compile(r"`[^`]*`"),
    re.compile(r"(?<![\w])'[^'\n]*'(?![\w])"),
)


def strip_quotes(text):
    """text with each quoted span replaced by a placeholder word (module docstring, NARROWING)."""
    for pattern in _QUOTE_RES:
        text = pattern.sub(" QUOTED ", text)
    return text


_HEDGE_RE = re.compile(
    r"\b(?:if|when|whenever|unless|whether|once|until|would|could|might|should|says|said|claims|claimed|"
    r"claiming|states|stated|wrote|writes|message|residual|residuals|residue|for example|such as|"
    r"not checked)\b|\be\.g\.", re.I)


def hedged(sentence):
    """True when the sentence holds a conditional, modal, reporting or residual word."""
    return _HEDGE_RE.search(sentence) is not None


_RESIDUAL_HEAD_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:residuals?|residue|not checked|known (?:limits?|gaps?)|limitations?|caveats?|"
    r"out of scope)\b[^.]*:?\s*$", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s+")


def message_chunks(message):
    """The message as a list of text chunks (a paragraph or a list item, its lines joined), with the allow
    marker line, quoted lines, fenced blocks and residual paragraphs dropped."""
    chunks, current, fenced, residual = [], [], False, False
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced or stripped.startswith(">") or line.startswith(ALLOW_PREFIX):
            continue
        if not stripped:
            if current:
                chunks.append(" ".join(current))
            current, residual = [], False
            continue
        if _RESIDUAL_HEAD_RE.match(line):
            if current:
                chunks.append(" ".join(current))
            current, residual = [], True
            continue
        if residual:
            continue
        if _BULLET_RE.match(line) and current:
            chunks.append(" ".join(current))
            current = []
        current.append(_BULLET_RE.sub("", line, count=1).strip() if _BULLET_RE.match(line) else stripped)
    if current and not fenced:
        chunks.append(" ".join(current))
    return chunks


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+")


def sentences(message):
    """Every sentence of the message that is read for a claim, quotes stripped (module docstring)."""
    out = []
    for chunk in _SEAMS["chunks"](message):
        for sentence in _SENTENCE_SPLIT_RE.split(_SEAMS["strip_quotes"](chunk)):
            sentence = sentence.strip()
            if sentence:
                out.append(sentence)
    return out


# The verb phrases. Each match's start ends the subject span; a verb-first form's object follows it.
_NEG_PASSIVE_RE = re.compile(
    r"\b(?:(?:(?:is|are|was|were|has|have|had)\s+(?:been\s+)?)?(?:still\s+|also\s+|deliberately\s+|"
    r"intentionally\s+)?not\s+(?:been\s+)?|(?:isn't|aren't|wasn't|weren't|hasn't|haven't)\s+(?:been\s+)?)"
    r"(?:re-?generated|changed|staged|touched|updated|modified|rebuilt)\b"
    r"|\b(?:is|are|was|were|stays?|remains?|left)\s+(?:also\s+)?(?:unchanged|untouched|unmodified|unstaged)\b",
    re.I)
_POS_PASSIVE_RE = re.compile(
    r"(?<![\w-])(?:(?:is|are|was|were|get|gets|got|been)\s+(?:also\s+|now\s+|then\s+|all\s+)?)?"
    r"re-?generated\b", re.I)
_NEG_ACTIVE_RE = re.compile(
    r"\b(?:does\s+not|do\s+not|did\s+not|doesn't|don't|didn't)\s+(?:regenerate|change|stage|touch|update|"
    r"modify)\b|\b(?:without|no)\s+(?:re-?generating|regeneration\s+of|changing|staging|touching|"
    r"updating)\b|\bno\s+(?:changes?|regeneration|updates?)\s+(?:to|of|in)\b", re.I)
_NEG_NOUN_RE = re.compile(
    r"\bno\s+(?:release[\s-]+(?:manifest|root|files?)|manifest|\.aiqt/manifest\.toml)[\s-]+"
    r"(?:changes?|regeneration|updates?|diff)\b", re.I)
_POS_ACTIVE_RE = re.compile(r"(?<![\w-])(?:regenerate|regenerates|regenerated)\b", re.I)
_TO_BEFORE_RE = re.compile(r"\bto\s*$", re.I)
_DET_BEFORE_RE = re.compile(r"\b(?:the|a|an|its|their|this|that|these|those)\s*$", re.I)
_NEG_BEFORE_RE = re.compile(r"(?:\bnot|\bnever|\bwithout|n't)\s*$", re.I)
_CHANGE_SUBJECTS = frozenset(("", "this commit", "this change", "the commit", "the change", "it", "this",
                              "this patch", "the patch", "this branch"))
_CLAUSE_BREAK_RE = re.compile(r",\s*(?:and|but|so|while|which|where)\b|;|\b(?:but|so|while|which|where)\b",
                              re.I)
_TAIL_WORD_RE = re.compile(r"^\s*(by|from|through|via|during|at|on|under|unless|until|if|when|whenever|for)\b"
                           r"(.*)$", re.I)
_TAIL_SELF_RE = re.compile(r"^\s*(?:this\s+(?:commit|change|branch|patch)|here)\b", re.I)
_TAIL_GENERATOR_RE = re.compile(r"^\s*(?:tools/)?(?:gen_manifest(?:\.py)?|the\s+generators?|its\s+generators?)\b",
                                re.I)


def _span_before(sentence, end):
    """The subject span: the text from the last clause break before end (or the sentence start) to end,
    or None when it is more than _MAX_SPAN_WORDS words."""
    head = sentence[:end]
    breaks = list(_CLAUSE_BREAK_RE.finditer(head))
    span = head[breaks[-1].end():] if breaks else head
    return span if len(span.split()) <= _MAX_SPAN_WORDS else None


def _span_after(sentence, start):
    """The object span: the text from start to the next clause break or the sentence end, capped at
    _MAX_SPAN_WORDS words."""
    tail = sentence[start:]
    found = _CLAUSE_BREAK_RE.search(tail)
    tail = tail[:found.start()] if found else tail
    return " ".join(tail.split()[:_MAX_SPAN_WORDS])


def describes_process(sentence, verb_end):
    """True when the word after the verb is one that makes the claim describe a tool or a process (module
    docstring, NARROWING)."""
    found = _TAIL_WORD_RE.match(sentence[verb_end:])
    if found is None:
        return False
    rest = found.group(2)
    if _TAIL_SELF_RE.match(rest):
        return False
    return not (found.group(1).lower() == "by" and _SEAMS["by_generator"](rest))


def _by_generator(rest):
    """True when the text after "by" names the release generator, which keeps the sentence a claim."""
    return _TAIL_GENERATOR_RE.match(rest) is not None


def _passive_claims(sentence, pattern, skip_negated):
    for match in pattern.finditer(sentence):
        if skip_negated and _SEAMS["neg_before"](sentence[:match.start()]):
            continue
        span = _SEAMS["span_before"](sentence, match.start())
        if span is not None and _SEAMS["names"](span) and not _SEAMS["process"](sentence, match.end()):
            return True
    return False


def _change_subject(subject, verb):
    """True when the subject of a verb-first negation is the change itself, or the verb needs none."""
    return subject in _CHANGE_SUBJECTS or verb.lower().startswith(("without", "no"))


def neg_claim(sentence):
    """True when the sentence says a release file is not regenerated, changed or staged."""
    if _passive_claims(sentence, _NEG_PASSIVE_RE, False):
        return True
    for match in _NEG_ACTIVE_RE.finditer(sentence):
        subject = " ".join(sentence[:match.start()].split()).lower().rstrip(",")
        if _SEAMS["change_subject"](subject, match.group(0)) and _SEAMS["names"](
                _span_after(sentence, match.end())):
            return True
    return _NEG_NOUN_RE.search(sentence) is not None


def pos_claim(sentence):
    """True when the sentence says a release file is regenerated."""
    if _passive_claims(sentence, _POS_PASSIVE_RE, True):
        return True
    for match in _POS_ACTIVE_RE.finditer(sentence):
        head = sentence[:match.start()]
        if _SEAMS["to_before"](head) or _SEAMS["det_before"](head) or _SEAMS["neg_before"](head):
            continue
        if _SEAMS["names"](_span_after(sentence, match.end())):
            return True
    return False


def _to_before(head):
    return _TO_BEFORE_RE.search(head) is not None


def _det_before(head):
    return _DET_BEFORE_RE.search(head) is not None


def _neg_before(head):
    return _NEG_BEFORE_RE.search(head) is not None


def allow_reason(message):
    """(reason or None, bare marker seen): the allow marker's reason when one line carries it."""
    bare = False
    for line in message.splitlines():
        if line.startswith(ALLOW_PREFIX):
            reason = line[len(ALLOW_PREFIX):].strip()
            if line[len(ALLOW_PREFIX):len(ALLOW_PREFIX) + 1] == " " and reason:
                return reason, bare
            bare = True
    return None, bare


def judge(message, changes_release, aggregate):
    """The findings for one commit: a list of (direction, sentence). changes_release is True when the commit
    changes a release file; aggregate is True for a merge or a merge train squash (module docstring)."""
    findings = []
    for sentence in sentences(message):
        if _SEAMS["hedged"](sentence):
            continue
        if changes_release and not (aggregate and _SEAMS["aggregate_skip"]()) and _SEAMS["neg"](sentence):
            findings.append(("says the release files are not regenerated, but the commit changes them",
                             sentence))
        elif not changes_release and _SEAMS["pos"](sentence):
            findings.append(("says the release files are regenerated, but the commit changes none of them",
                             sentence))
    return findings


def _aggregate_skip():
    return True


def refused_findings(message, changes_release, aggregate):
    """judge's findings, or none when the message carries the allow marker with a reason."""
    if _SEAMS["allow"](message)[0] is not None:
        return []
    return judge(message, changes_release, aggregate)


# The named seams the self-test's removed-check pass replaces one at a time; production reads them only here.
_SEAMS = {
    "chunks": message_chunks,
    "strip_quotes": strip_quotes,
    "hedged": hedged,
    "names": names_release_file,
    "bare_qualifier": _bare_manifest_ok,
    "span_before": _span_before,
    "process": describes_process,
    "by_generator": _by_generator,
    "change_subject": _change_subject,
    "neg": neg_claim,
    "pos": pos_claim,
    "to_before": _to_before,
    "det_before": _det_before,
    "neg_before": _neg_before,
    "aggregate_skip": _aggregate_skip,
    "allow": allow_reason,
}


# ---------------------------------------------------------------- git reading

def _git(root, *args):
    """git bound to root with every ambient GIT_ variable dropped and automatic maintenance off; the stdout
    text, or CannotEvaluate on a launch failure, a timeout or a non-zero exit."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        done = subprocess.run(["git", "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                               "-c", "maintenance.auto=false", *args], capture_output=True, env=env,
                              timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git {} could not run ({})".format(args[0], type(exc).__name__)) from None
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()[:200]
        raise CannotEvaluate("git {} exited {} ({})".format(args[0], done.returncode, detail))
    return done.stdout.decode("utf-8", "replace")


def resolve_commit(root, rev):
    """The full object id of the commit rev names; CannotEvaluate when it names none."""
    if not isinstance(rev, str) or not rev or rev.startswith("-"):
        raise CannotEvaluate("revision {!r} is not a revision".format(rev))
    oid = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}").strip()
    if not _SHA_RE.match(oid):
        raise CannotEvaluate("revision {!r} does not resolve to a commit".format(rev))
    return oid


def commits_in(root, base, head):
    """The commit ids in base..head, oldest first."""
    out = _git(root, "rev-list", "--reverse", "{}..{}".format(base, head))
    return [line for line in out.split("\n") if line]


def commit_record(root, oid):
    """(parents, message) for one commit."""
    out = _git(root, "log", "-1", "--no-color", "--format=%P%x00%B", oid)
    parents, sep, message = out.partition("\0")
    if not sep:
        raise CannotEvaluate("git log printed no record for {}".format(oid[:12]))
    return parents.split(), message


def changes_release_files(root, oid, parents):
    """True when the commit changes .aiqt/manifest.toml or a file under .aiqt/release/ against its first
    parent (the empty tree for a root commit)."""
    spec = ("--", ":(literal)" + RELEASE_MANIFEST, ":(literal)" + RELEASE_DIR.rstrip("/"))
    if parents:
        out = _git(root, "diff-tree", "-r", "-z", "--name-only", "--no-renames", parents[0], oid, *spec)
    else:
        out = _git(root, "diff-tree", "-r", "-z", "--name-only", "--no-renames", "--root", oid, *spec)
    paths = [path for path in out.split("\0") if path and path != oid]
    return any(path == RELEASE_MANIFEST or path.startswith(RELEASE_DIR) for path in paths)


def check_commits(root, oids):
    """Judge each commit; print the result and return the exit status."""
    refused, allowed, notes = [], [], []
    for oid in oids:
        parents, message = commit_record(root, oid)
        subject = message.split("\n", 1)[0]
        aggregate = len(parents) > 1 or _TRAIN_SUBJECT_RE.match(subject) is not None
        findings = judge(message, changes_release_files(root, oid, parents), aggregate)
        reason, bare = _SEAMS["allow"](message)
        if bare and reason is None:
            notes.append("NOTE: commit {} carries {} with no reason, which is not honoured".format(
                oid[:12], ALLOW_PREFIX))
        for direction, sentence in findings:
            line = "commit {}: {}: {}".format(oid[:12], direction, sentence[:240])
            (allowed if reason is not None else refused).append(
                line + (" (ALLOWED: {})".format(reason[:120]) if reason is not None else ""))
    for note in notes:
        print(note)
    for line in allowed:
        print("ALLOWED " + line)
    if refused:
        print("FAIL: {} commit message claim(s) contradict what the commit changes in {} or {}".format(
            len(refused), RELEASE_MANIFEST, RELEASE_DIR))
        for line in refused:
            print("  " + line)
        print("Correct the message (git commit --amend or a rebase), or add a line "
              "'{} <reason>' when the sentence is not a claim about this commit.".format(ALLOW_PREFIX))
        return 1
    print("PASS: {} commit(s) read; no message claim contradicts what the commit changes in {} or {}. "
          "NOT CHECKED: claims phrased outside the read shapes, hedged or quoted sentences, and the "
          "NOT-REGENERATED direction on merges and merge train squashes.".format(
              len(oids), RELEASE_MANIFEST, RELEASE_DIR))
    return 0


# ---------------------------------------------------------------- event reading

def _reject_constant(token):
    raise ValueError("non-JSON constant {}".format(token))


def _event_sha(event, *path):
    node = event
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise CannotEvaluate("event JSON missing required field {}".format(".".join(path)))
        node = node[key]
    if not isinstance(node, str) or not _SHA_RE.match(node):
        raise CannotEvaluate("event JSON field {} is not a 40-hex object id".format(".".join(path)))
    return node


def event_range(root, environ):
    """The commit ids the environment names (module docstring, COMMITS)."""
    name = environ.get("GITHUB_EVENT_NAME", "")
    if name in ("", "workflow_dispatch", "schedule"):
        return [resolve_commit(root, "HEAD")]
    if name not in ("pull_request", "push"):
        raise CannotEvaluate("cannot establish the commits for event kind {!r}".format(name))
    path = environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        raise CannotEvaluate("GITHUB_EVENT_PATH is not set")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("cannot read or parse the event JSON ({})".format(type(exc).__name__)) from None
    if name == "pull_request":
        base, head = _event_sha(event, "pull_request", "base", "sha"), _event_sha(event, "pull_request", "head",
                                                                                 "sha")
    else:
        base, head = _event_sha(event, "before"), _event_sha(event, "after")
        if head == _ZERO_SHA:
            return []
        if base == _ZERO_SHA:
            raise CannotEvaluate("branch-creation push (all-zero before): the complete range cannot be "
                                 "established")
    return commits_in(root, resolve_commit(root, base), resolve_commit(root, head))


def main(argv, root=ROOT, environ=None):
    environ = os.environ if environ is None else environ
    try:
        if argv == ["--self-test"]:
            return self_test()
        if len(argv) == 3 and argv[0] == "--range":
            oids = commits_in(root, resolve_commit(root, argv[1]), resolve_commit(root, argv[2]))
        elif not argv:
            oids = event_range(root, environ)
        else:
            print("usage: check_commit_claims.py [--self-test | --range BASE HEAD]", file=sys.stderr)
            return 2
        return check_commits(root, oids)
    except CannotEvaluate as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2


# ---------------------------------------------------------------- self-test

# (id, message, changes_release, aggregate, want_findings, seam whose removal turns the row red). The seam is
# None for a row no single removal flips. Messages are drawn from the shapes this repository's history uses.
_MESSAGE_ROWS = (
    ("neg/observed-reused-proposal",
     "Fix the gate.\n\nThe release manifest and release files are not regenerated.", True, False, 1, "neg"),
    ("neg/observed-reused-proposal-twin-no-change",
     "Fix the gate.\n\nThe release manifest and release files are not regenerated.", False, False, 0, None),
    ("neg/path-not-staged", "Edit docs.\n\n.aiqt/manifest.toml is not staged.", True, False, 1, "names"),
    ("neg/contracted", "Edit docs.\n\nThe release root isn't regenerated.", True, False, 1, "neg"),
    ("neg/release-root-unchanged", "Edit docs.\n\nThe release root stays unchanged.", True, False, 1, "neg"),
    ("neg/this-commit-does-not", "Edit docs.\n\nThis commit does not regenerate the manifest.", True, False, 1,
     "neg"),
    ("neg/without-regenerating", "Edit docs without regenerating the release files.", True, False, 1, "neg"),
    ("neg/no-manifest-change", "Edit docs; no manifest change.", True, False, 1, "neg"),
    ("neg/tool-subject-not-read", "Edit docs.\n\ngen_site does not touch the release root.", True, False, 0,
     "change_subject"),
    ("neg/never-is-process", "The manifest is authored from source review and is never regenerated from a "
     "runtime capture.", True, False, 0, None),
    ("neg/by-tool-is-process", "Edit.\n\nThe release root is not regenerated by this check.", True, False, 0,
     "process"),
    ("neg/by-tool-twin-self", "Edit.\n\nThe release root is not regenerated in this commit.", True, False, 1,
     "neg"),
    ("neg/hooks-manifest-not-release", "Edit.\n\nThe hooks manifest is not changed.", True, False, 0,
     "bare_qualifier"),
    ("neg/double-quoted", 'Edit.\n\nThe proposal text "the release files are not regenerated" was reused.',
     True, False, 0, "strip_quotes"),
    ("neg/curly-quoted", "Edit.\n\nIts text “the release manifest is not regenerated” was reused.",
     True, False, 0, "strip_quotes"),
    ("neg/quoted-line", "Edit.\n\n> The release manifest is not regenerated.", True, False, 0, "chunks"),
    ("neg/fenced", "Edit.\n\n```\nThe release manifest is not regenerated.\n```", True, False, 0,
     ("chunks", "strip_quotes")),
    ("neg/hedged-when", "Edit.\n\nThe gate refuses a commit when the release root is not regenerated.", True,
     False, 0, "hedged"),
    ("neg/residual-heading", "Edit.\n\nKnown gaps:\n- The release manifest is not regenerated here.", True,
     False, 0, "chunks"),
    ("neg/residual-word", "Edit.\n\nA disclosed residual: the release manifest is not regenerated.", True,
     False, 0, "hedged"),
    ("neg/aggregate-skipped", "Merge train x: #1\n\n* a\n\nThe release manifest is not regenerated.", True, True,
     0, "aggregate_skip"),
    ("pos/false-converse-observed", "Run the gate in CI.\n\nThe manifest is regenerated.", False, False, 1,
     "pos"),
    ("pos/false-converse-twin-changed", "Run the gate in CI.\n\nThe manifest is regenerated.", True, False, 0,
     None),
    ("pos/imperative", "Edit.\n\nRegenerate the manifest and release root.", False, False, 1, "pos"),
    ("pos/telegraphic", "Edit.\n\nManifest regenerated.", False, False, 1, "pos"),
    ("pos/list-subject", "Edit.\n\nThe plugin copy, enforcement register and release manifest are regenerated.",
     False, False, 1, "pos"),
    ("pos/by-generator-still-claim", "Edit.\n\nThe release root is regenerated by gen_manifest.", False, False,
     1, "by_generator"),
    ("pos/adjective-not-read", "Edit.\n\nThe gate compares the regenerated manifest with the committed one.",
     False, False, 0, "det_before"),
    ("pos/aggregate-still-checked", "Merge train x: #1\n\n* a\n\nRegenerated .aiqt/manifest.toml.", False,
     True, 1, "pos"),
    ("pos/to-regenerate-instruction", "Edit.\n\nRun tools/gen_manifest.py to regenerate the manifest.", False,
     False, 0, "to_before"),
    ("pos/plugin-copy-not-release", "Edit.\n\nThe plugin hook copy is regenerated.", False, False, 0, None),
    ("pos/gates-manifest-not-release", "Edit.\n\nThe gates manifest description is regenerated.", False, False, 0,
     "bare_qualifier"),
    ("pos/backquoted", "Edit.\n\nThe step `regenerate the manifest` was dropped.", False, False, 0,
     "strip_quotes"),
    ("pos/hedged-until", "Edit.\n\nThe gate fails until the manifest is regenerated.", False, False, 0,
     "hedged"),
    ("pos/clause-break", "Edit.\n\nThe release manifest row moves, and the docs are regenerated.", False, False,
     0, "span_before"),
    ("pos/not-before", "Edit.\n\nThe manifest is not regenerated.", False, False, 0, "neg_before"),
    ("allow/with-reason", "Edit.\n\nThe release files are not regenerated.\nRelease-claim-allow: quoted from a "
     "proposal", True, False, 0, "allow"),
    ("allow/no-reason", "Edit.\n\nThe release files are not regenerated.\nRelease-claim-allow:", True, False, 1,
     "neg"),
)


def _expect(failures, condition, message):
    if not condition:
        failures.append(message)


def _message_rows(failures):
    for row_id, message, changes, aggregate, want, _seam in _MESSAGE_ROWS:
        got = len(refused_findings(message, changes, aggregate))
        _expect(failures, got == want, "{}: want {} finding(s), got {}".format(row_id, want, got))


# Each seam's removed form: the behaviour the gate has when that check is taken out.
_REMOVED = {
    "chunks": lambda message: [" ".join(message.split())],
    "strip_quotes": lambda text: text,
    "hedged": lambda sentence: False,
    "names": lambda text: False,
    "bare_qualifier": lambda text, start: True,
    "span_before": lambda sentence, end: sentence[:end],
    "process": lambda sentence, end: False,
    "by_generator": lambda rest: False,
    "change_subject": lambda subject, verb: True,
    "neg": lambda sentence: False,
    "pos": lambda sentence: False,
    "to_before": lambda head: False,
    "det_before": lambda head: False,
    "neg_before": lambda head: False,
    "aggregate_skip": lambda: False,
    "allow": lambda message: (None, False),
}


def _removed_checks(failures):
    """Each row that names a seam (or a tuple of seams that cover it together) must turn red when that seam is
    replaced by its removed form."""
    named = set()
    for row_id, message, changes, aggregate, want, seam in _MESSAGE_ROWS:
        if seam is None:
            continue
        seams = seam if isinstance(seam, tuple) else (seam,)
        named.update(seams)
        saved = {name: _SEAMS[name] for name in seams}
        for name in seams:
            _SEAMS[name] = _REMOVED[name]
        try:
            got = len(refused_findings(message, changes, aggregate))
        finally:
            _SEAMS.update(saved)
        _expect(failures, got != want, "{}: still {} finding(s) with the {} check removed".format(
            row_id, got, "+".join(seams)))
    missing = sorted(set(_REMOVED) - named)
    _expect(failures, not missing, "removed-check pass: no row names the seam(s) {}".format(missing))
    _expect(failures, set(_REMOVED) == set(_SEAMS), "removed-check pass: _REMOVED and _SEAMS differ")


def _allow_rows(failures):
    reason, bare = allow_reason("x\n\nRelease-claim-allow: describes the release tool, not this commit\n")
    _expect(failures, reason == "describes the release tool, not this commit" and not bare,
            "allow/with-reason: got {!r} {!r}".format(reason, bare))
    for row_id, text in (("allow/no-reason", "x\n\nRelease-claim-allow:\n"),
                         ("allow/blank-reason", "x\n\nRelease-claim-allow:   \n"),
                         ("allow/indented", "x\n\n  Release-claim-allow: reason\n"),
                         ("allow/glued", "x\n\nRelease-claim-allow:reason\n")):
        reason, _bare = allow_reason(text)
        _expect(failures, reason is None, "{}: a marker that is not honoured gave {!r}".format(row_id, reason))
    _expect(failures, allow_reason("x\n\nRelease-claim-allow:\n")[1], "allow/no-reason: bare marker not seen")
    marked = "x\n\nThe release files are not regenerated.\nRelease-claim-allow: quoted from a proposal\n"
    _expect(failures, len(judge(marked, True, False)) == 1, "allow/marker-line-is-not-the-claim")


def _git_rows(failures):
    """Commits on throwaway repositories, judged end to end by check_commits and main."""
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import fixture_git_lifecycle, git_fixture_env

    identity = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
    scratch = tempfile.mkdtemp(prefix="commit-claims-")

    def git(root, *args):
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, env=git_fixture_env(**identity),
                              timeout=GIT_TIMEOUT)
        if done.returncode != 0:
            raise RuntimeError("fixture git {} exited {}: {!r}".format(args[0], done.returncode,
                                                                       done.stderr[:200]))
        return done.stdout.decode("utf-8", "replace").strip()

    def write(root, rel, text):
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def commit(root, files, message):
        for rel, text in files.items():
            write(root, rel, text)
        git(root, "add", "-A")
        git(root, "commit", "-q", "--allow-empty", "-m", message)
        return git(root, "rev-parse", "HEAD")

    def run(argv, root, environ=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(argv, root=Path(root), environ=environ or {})
        return rc, out.getvalue() + err.getvalue()

    def expect_run(row_id, argv, root, want, needle, environ=None):
        rc, text = run(argv, root, environ)
        _expect(failures, rc == want and needle in text,
                "{}: want exit {} and {!r}, got {}: {!r}".format(row_id, want, needle, rc, text[-300:]))

    with fixture_git_lifecycle():
        try:
            root = os.path.join(scratch, "r")
            os.makedirs(root)
            git(root, "init", "-q")
            base = commit(root, {"README.md": "x\n", ".aiqt/manifest.toml": "a\n",
                                 ".aiqt/release/root.txt": "r1\n"}, "base")
            lie = commit(root, {".aiqt/release/root.txt": "r2\n"},
                         "Edit\n\nThe release manifest and release files are not regenerated.")
            expect_run("git/neg-lie-refused", ["--range", base, lie], root, 1, "are not regenerated, but the "
                       "commit changes them")
            honest = commit(root, {"README.md": "y\n"}, "Edit\n\nThe release files are not regenerated.")
            expect_run("git/neg-true-passes", ["--range", lie, honest], root, 0, "PASS: 1 commit(s) read")
            stale = commit(root, {"README.md": "z\n"}, "Edit\n\nThe manifest is regenerated.")
            expect_run("git/pos-lie-refused", ["--range", honest, stale], root, 1, "but the commit changes none")
            moved = commit(root, {".aiqt/manifest.toml": "b\n"}, "Edit\n\nThe manifest is regenerated.")
            expect_run("git/pos-true-passes", ["--range", stale, moved], root, 0, "PASS: 1 commit(s) read")
            allowed = commit(root, {".aiqt/manifest.toml": "c\n"},
                             "Edit\n\nThe manifest is not changed.\n\nRelease-claim-allow: test row")
            expect_run("git/allowed", ["--range", moved, allowed], root, 0, "ALLOWED commit")
            bare = commit(root, {".aiqt/manifest.toml": "d\n"},
                          "Edit\n\nThe manifest is not changed.\n\nRelease-claim-allow:")
            expect_run("git/bare-marker-refused", ["--range", allowed, bare], root, 1, "is not honoured")
            expect_run("git/range-oldest-first", ["--range", base, bare], root, 1, "FAIL: 3 commit message")
            expect_run("git/head-alone", [], root, 1, "commit " + bare[:12])
            expect_run("git/dispatch-head-alone", [], root, 1, "commit " + bare[:12],
                       {"GITHUB_EVENT_NAME": "schedule"})
            event = os.path.join(scratch, "event.json")
            write(scratch, "event.json", json.dumps({"pull_request": {"base": {"sha": honest},
                                                                      "head": {"sha": moved}}}))
            expect_run("git/pull-request-range", [], root, 1, "commit " + stale[:12],
                       {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": event})
            write(scratch, "event.json", json.dumps({"before": stale, "after": moved}))
            expect_run("git/push-range", [], root, 0, "PASS: 1 commit(s) read",
                       {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": event})
            write(scratch, "event.json", json.dumps({"before": stale, "after": _ZERO_SHA}))
            expect_run("git/push-deletion", [], root, 0, "PASS: 0 commit(s) read",
                       {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": event})
            write(scratch, "event.json", json.dumps({"before": _ZERO_SHA, "after": moved}))
            expect_run("git/push-creation-fails-closed", [], root, 2, "branch-creation push",
                       {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": event})
            write(scratch, "event.json", json.dumps({"before": "HEAD", "after": moved}))
            expect_run("git/push-not-sha", [], root, 2, "not a 40-hex object id",
                       {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": event})
            write(scratch, "event.json", "{\"before\": NaN}")
            expect_run("git/event-nan", [], root, 2, "cannot read or parse",
                       {"GITHUB_EVENT_NAME": "push", "GITHUB_EVENT_PATH": event})
            expect_run("git/event-unset-path", [], root, 2, "GITHUB_EVENT_PATH is not set",
                       {"GITHUB_EVENT_NAME": "push"})
            expect_run("git/event-unknown", [], root, 2, "event kind 'issues'", {"GITHUB_EVENT_NAME": "issues"})
            expect_run("git/bad-revision", ["--range", base, "nosuchref"], root, 2, "git rev-parse exited")
            expect_run("git/option-revision", ["--range", "--all", base], root, 2, "is not a revision")
            expect_run("git/usage", ["--range", base], root, 2, "usage:")
            # A merge whose first parent lacks the release change carries it in its diff; its NOT-REGENERATED
            # sentence is not read, its REGENERATED one is.
            git(root, "checkout", "-q", "-b", "side", base)
            side = commit(root, {".aiqt/release/announce-snippet.txt": "s\n"}, "side")
            git(root, "checkout", "-q", "-")
            git(root, "merge", "-q", "--no-ff", "-m", "Merge side\n\nThe release root is not regenerated.", side)
            merge = git(root, "rev-parse", "HEAD")
            expect_run("git/merge-neg-not-read", ["--range", bare, merge], root, 0, "PASS: 2 commit(s) read")
            squash = commit(root, {"README.md": "w\n"}, "Merge train t1: #1\n\n* a\n\nManifest regenerated.")
            expect_run("git/squash-pos-read", ["--range", merge, squash], root, 1, "commit " + squash[:12])
            train = commit(root, {".aiqt/manifest.toml": "e\n"},
                           "Merge train t2: #2\n\n* a\n\nThe release root is not regenerated.")
            expect_run("git/squash-neg-not-read", ["--range", squash, train], root, 0, "PASS: 1 commit(s) read")
            member = commit(root, {".aiqt/manifest.toml": "f\n"},
                            "Merge trains: a\n\nThe release root is not regenerated.")
            expect_run("git/train-subject-exact", ["--range", train, member], root, 1, "commit " + member[:12])
            rootless = os.path.join(scratch, "solo")
            os.makedirs(rootless)
            git(rootless, "init", "-q")
            first = commit(rootless, {".aiqt/manifest.toml": "a\n"}, "init\n\nThe manifest is not staged.")
            expect_run("git/root-commit-diff", [], rootless, 1, "commit " + first[:12])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


def self_test():
    failures = []
    _message_rows(failures)
    _removed_checks(failures)
    _allow_rows(failures)
    try:
        _git_rows(failures)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        failures.append("git fixture rows could not run: {}".format(exc))
    for failure in failures:
        print("FAIL: " + failure)
    if failures:
        print("SELF-TEST FAILED: {} row(s)".format(len(failures)))
        return 1
    print("SELF-TEST PASS: check_commit_claims.py: {} message rows, each named check removed turns its rows "
          "red, the allow marker rows, and the git fixture rows (ranges, events, merges, a train squash, a "
          "root commit and the fail-closed paths)".format(len(_MESSAGE_ROWS)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
