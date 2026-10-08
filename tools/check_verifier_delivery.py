#!/usr/bin/env python3
"""Verifier delivery check: a QA delivery counts toward the merge quorum only when its quotes are
verbatim in the reviewed tree and its clean verdict covers answered items.

  check_verifier_delivery.py --brief FILE --delivery FILE --repo DIR
  check_verifier_delivery.py                 (validates its own static tables; reads no input, runs no git)
  check_verifier_delivery.py --self-test
  check_verifier_delivery.py [--self-test] --execution-report ABS_PATH   (the execution-set gate's launch)

The standing rule this enforces: a verifier's clean verdict counts only after its specific claims are
confirmed at source, and a delivery carrying a quote that is not verbatim in the reviewed tree is a
FAILED delivery. A whole-tree search alone is not enough: a sentence quoted as text from one file can
exist, reworded or not, only in another.

The reviewed tree is the commit named by the brief's one BRIEF-PIN line (a full 40-hex SHA). The
delivery's first line that does not start with WORKER_STATUS must be exactly that SHA (surrounding
whitespace aside); a differing or malformed SHA line is CANNOT_EVALUATE. Every byte of reviewed content
comes from that commit through git -C REPO --no-replace-objects (cat-file -t, ls-tree, cat-file blob,
cat-file --batch; each launch also carries the gc.auto=0, gc.autoDetach=false and maintenance.auto=false
pins), never from the working tree, under an environment that drops every ambient GIT_
variable and sets GIT_NO_REPLACE_OBJECTS, GIT_NO_LAZY_FETCH, GIT_TERMINAL_PROMPT=0 and
GIT_OPTIONAL_LOCKS=0. An unreadable, oversized, non-regular or non-UTF-8 input file, a missing or
ambiguous BRIEF-PIN, an unresolvable SHA, an unclosed fenced block and every git failure are
CANNOT_EVALUATE (exit 2), never a pass.

Rules:
  C1 coverage. A verdict of any kind (VERDICT: NO BLOCKERS, VERDICT: BLOCKERS FOUND or another) is
     reclassed NO_VOTE (exit 1, reason named) when no numbered item is answered (every item
     UNVERIFIABLE, or no item at all): a verdict counts only for answered items, so a BLOCKERS verdict
     with coverage 0/m is no vote. A clean verdict is also NO_VOTE when every numbered question of the
     brief asks for a quote and no delivery item carries one. One UNVERIFIABLE item among answered ones
     is legitimate. Coverage n/m is reported: n answered items, m the brief's numbered questions (the
     delivery's items when the brief numbers none); an unanswered question counts as uncovered. A
     delivery with no VERDICT line, or with conflicting VERDICT lines, is NO_VOTE.
  C2 verbatim. Every fenced code block, every blockquote run and every inline double-quoted span must
     appear byte for byte (UTF-8) as a contiguous span of one file of the pinned tree. A quote may
     start or end mid-line, so an exact excerpt passes; joining non-adjacent lines, dropping a line,
     re-wrapping, re-indenting or adding an escape fails. Leading indentation inside a fenced block
     counts (only the opening fence's own indentation, up to three spaces, is removed, as CommonMark
     does). When the item's heading names a file, the quote must match in that file; otherwise, when
     the brief's question with the item's number names a file, in that file; otherwise anywhere in the
     tree. A named path with a slash binds exactly; a bare file name (no slash) binds to every file of
     the pinned tree with that basename, root or not (a root README.md and .preview/README.md are both
     named by "README.md"), and the result's found_in names the file that matched. A match only in
     another file (another path, or another basename) fails, naming both; a heading that names a path
     absent from the pinned tree fails every quote under it. Any failing quote fails the whole delivery (exit 1),
     each listed with the files searched and, when one exists, the nearest whitespace-normalised match
     (a diagnostic only, never a pass).

Output: one JSON object on stdout (verdict PASS, FAIL, NO_VOTE or CANNOT_EVALUATE; reasons; per-item
results with every quote; coverage) and a one-line human summary on stderr. Exit 0 PASS; 1 FAIL or
NO_VOTE (FAIL wins when both apply); 2 CANNOT_EVALUATE or a usage error.

The grammar read (a declared subset of Markdown, not a general parser):
  * Lines are split at LF; one trailing CR per line is the delivery's transport, not quote content.
  * A fenced block opens on a line of three or more backticks or tildes indented at most three spaces
    (a backtick fence's info string holds no backtick) and closes on a line of the same character, at
    least as long, with nothing after it but spaces or tabs. Every fenced block with a non-blank line
    is a quote, whatever its info string: a command or output shown in a fence is held to the same
    rule as a quote.
  * A blockquote run is consecutive lines starting with ">" (at most three spaces before it); one
    space after the ">" is removed and the run's lines are joined with LF into one quote.
  * An inline quote is the text between a straight double quote and the next one on the same line,
    or between a left and a right curly double quote, outside inline code spans. A quote that does not
    close on its line is not read.
  * A numbered item is a line at column 0 such as "1. ", "Q1: ", "**2.**", "### 3) " or "Item 4. ". When
    any such line is a Markdown heading (#), only heading lines are items, so a numbered list nested in
    an answer is not mistaken for one. A duplicate item number, or one the brief's numbered questions
    do not hold, is CANNOT_EVALUATE. Text before the first item is the preamble; its quotes are checked
    against the whole tree.
  * An item is UNVERIFIABLE when the word UNVERIFIABLE (upper case) appears in its heading or its text
    outside fenced blocks and blockquotes.
  * The brief's questions are its first run of column-0 numbered lines counting 1, 2, 3 and so on
    (after a line reading QUESTIONS: when the brief has one); a number out of sequence ends the run, so
    a later numbered list that restarts at 1 is not a question. A question's text is its line plus the
    lines after it up to a blank line. A question asks for a quote when it holds the word quote (or
    quotes, quoted, quoting).
  * A file is named when a token of the heading or question (split at whitespace, quotes, backticks,
    brackets, commas, semicolons and asterisks; a ":LINE" or ":LINE-LINE" suffix, a "#" anchor and
    trailing dots or colons removed) is a blob path of the pinned tree, or is a bare name (no slash)
    that is the basename of a blob and either is a root blob itself or carries a file extension; a
    bare name names every blob with that basename. In a heading, a token shaped like a relative file
    path (a slash and a file extension) that is absent from the tree is also a named file, which no
    quote can match.

DISCLOSED RESIDUAL. C2 proves a quote EXISTS in the named file at the pinned SHA, not that it supports
the grade the verifier gave; a grade resting on an earlier answer is not linked to that answer;
runtime claims (that a name exists, or does not, at runtime) are out of scope here (a later rule). A
Gemini-family verifier's actual read path is undeclared, so a verbatim quote proves the text is in the
tree, not that the verifier read it there. Inline code spans and single-quoted text are not read as
quotes, so a fabricated quote presented only that way passes unseen; a double-quoted span that
breaks across lines is not read; attribution inside an answer's body (a "from file X:" lead-in) is not
parsed, only the heading and the question are; a question naming several files accepts a match in any
of them; a quote of the brief's own text is held to the tree like any other; the UNVERIFIABLE and
quote-question tests are word tests, so a stray mention moves an item toward NO_VOTE, never toward a
pass. The whole tree is read into memory (bounded at MAX_TREE_BYTES) only when a quote needs a
tree-wide search or a failure needs its diagnostic. git is resolved through PATH, a trusted-toolchain
concern; a repository in SHA-256 object format never resolves a 40-hex pin and so cannot be evaluated.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_verifier_delivery.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 120
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_TREE_BYTES = 512 * 1024 * 1024
MAX_LISTED = 10
SHA_RE = re.compile(r"[0-9a-f]{40}")
PIN_LINE_RE = re.compile(r"BRIEF-PIN:(.*)")
STATUS_PREFIX = "WORKER_STATUS"
VERDICT_RE = re.compile(r"[ \t]*(?:\*\*)?VERDICT(?:\*\*)?[ \t]*:(?:\*\*)?[ \t]*(.*?)[ \t]*$", re.I)
CLEAN_VERDICT = "NO BLOCKERS"
ITEM_RE = re.compile(r"(#{1,6}[ \t]+)?(?:\*\*)?(?:Q|Item[ \t]+|Question[ \t]+)?(\d{1,3})[.):](?:\*\*)?(?=[ \t]|$)",
                     re.I)
QUESTIONS_MARKER_RE = re.compile(r"QUESTIONS:?", re.I)
QUOTE_WORD_RE = re.compile(r"\bquot(?:e|es|ed|ing)\b", re.I)
UNVERIFIABLE_RE = re.compile(r"\bUNVERIFIABLE\b")
FENCE_RE = re.compile(r"( {0,3})(`{3,}|~{3,})(.*)$")
FENCE_CLOSE_RE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")
BLOCKQUOTE_RE = re.compile(r" {0,3}>[ ]?(.*)$")
CODE_SPAN_RE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
INLINE_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"))
PATH_TOKEN_RE = re.compile(r"[^\s`'\"()\[\]<>{},;*\u201c\u201d]+")
LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")
PATHLIKE_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
FILE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\.[A-Za-z0-9]+")
WHOLE_TREE = "<pinned tree>"


class CannotEvaluate(RuntimeError):
    """An input, the pinned commit, or a git result could not answer the question."""


# ---------------------------------------------------------------------------------------------------
# The pinned tree
# ---------------------------------------------------------------------------------------------------

def _git_env():
    """Drop every ambient GIT_ variable (a GIT_DIR, GIT_INDEX_FILE or GIT_OBJECT_DIRECTORY would pick
    another repository's objects) and pin an offline, non-interactive, replace-free posture."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(repo, *args, stdin=None):
    """git's stdout bytes for one read of the object database; any failure is CannotEvaluate."""
    try:
        result = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                 "-C", str(repo), "--no-replace-objects", *args], input=stdin,
                                capture_output=True, timeout=TIMEOUT, env=_git_env(), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git {} could not run: {}".format(args[0], exc))
    if result.returncode != 0:
        raise CannotEvaluate("git {} exited {}: {}".format(
            " ".join(args[:2]), result.returncode,
            result.stderr.decode("utf-8", "replace").strip()[:300] or "no message"))
    return result.stdout


class PinnedTree:
    """The blobs of one commit's tree, read from the object database only."""

    def __init__(self, repo, sha):
        self.repo = repo
        self.sha = sha
        kind = _git(repo, "cat-file", "-t", sha).strip()
        if kind != b"commit":
            raise CannotEvaluate("pinned SHA {} is a {}, not a commit".format(
                sha, kind.decode("utf-8", "replace")))
        listing = _git(repo, "ls-tree", "-r", "-t", "-l", "-z", "--full-tree", sha)
        self.blobs = {}
        self.dirs = set()
        self._content = {}
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            meta, sep, raw_path = entry.partition(b"\t")
            fields = meta.split()
            if not sep or len(fields) != 4 or not raw_path:
                raise CannotEvaluate("git ls-tree returned an unparseable entry")
            path = raw_path.decode("utf-8", "surrogateescape")
            if fields[1] == b"blob":
                try:
                    size = int(fields[3])
                except ValueError:
                    raise CannotEvaluate("git ls-tree returned a non-numeric size for {}".format(path))
                self.blobs[path] = (fields[2].decode("ascii"), size)
            elif fields[1] == b"tree":
                self.dirs.add(path)

    def paths(self):
        return sorted(self.blobs)

    def read(self, path):
        if path not in self._content:
            self._content[path] = _git(self.repo, "cat-file", "blob", self.blobs[path][0])
        return self._content[path]

    def load_all(self):
        """Read every blob not yet read, through one cat-file --batch, within MAX_TREE_BYTES."""
        missing = [path for path in self.paths() if path not in self._content]
        if not missing:
            return
        if sum(size for _, size in self.blobs.values()) > MAX_TREE_BYTES:
            raise CannotEvaluate("pinned tree exceeds {} bytes; a tree-wide search is not attempted"
                                 .format(MAX_TREE_BYTES))
        oids = [self.blobs[path][0] for path in missing]
        data = _git(self.repo, "cat-file", "--batch", stdin=("\n".join(oids) + "\n").encode("ascii"))
        pos = 0
        for path, oid in zip(missing, oids):
            end = data.find(b"\n", pos)
            header = data[pos:end].split() if end >= 0 else []
            if len(header) != 3 or header[0].decode("ascii", "replace") != oid or header[1] != b"blob":
                raise CannotEvaluate("git cat-file --batch did not return blob {}".format(oid))
            size = int(header[2])
            content = data[end + 1:end + 1 + size]
            if len(content) != size or data[end + 1 + size:end + 2 + size] != b"\n":
                raise CannotEvaluate("git cat-file --batch returned a truncated blob {}".format(oid))
            self._content[path] = content
            pos = end + 2 + size


def _blob_bytes(tree, path):
    return tree.read(path)


# ---------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------

def _read_input(path, label):
    """The bytes of one regular input file, opened non-blocking (a FIFO never stalls the check)."""
    try:
        with open(path, "rb", opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK)) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise CannotEvaluate("{} {} is not a regular file".format(label, path))
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("{} {} unreadable: {}".format(label, path, exc))
    if len(raw) > MAX_INPUT_BYTES:
        raise CannotEvaluate("{} {} exceeds {} bytes".format(label, path, MAX_INPUT_BYTES))
    return raw


def _decode(raw, label):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{} is not valid UTF-8: {}".format(label, exc))


def _lines(text):
    """(line number, line) pairs split at LF, one trailing CR removed from each line."""
    return [(number, line[:-1] if line.endswith("\r") else line)
            for number, line in enumerate(text.split("\n"), 1)]


def _brief_pin(text):
    pins = {match.group(1).strip() for _, line in _lines(text) if (match := PIN_LINE_RE.match(line))}
    if len(pins) != 1:
        raise CannotEvaluate("the brief carries {} distinct BRIEF-PIN values; exactly one is required"
                             .format(len(pins)))
    pin = pins.pop()
    if not SHA_RE.fullmatch(pin):
        raise CannotEvaluate("the brief's BRIEF-PIN {!r} is not a full 40-hex SHA".format(pin[:80]))
    return pin


def _pin_matches(delivered, pin):
    return delivered == pin


def _delivery_body(text, pin):
    """The delivery's lines after its SHA line, once that line is proven to be the pin."""
    lines = _lines(text)
    for index, (number, line) in enumerate(lines):
        if line.startswith(STATUS_PREFIX):
            continue
        candidate = line.strip()
        if not SHA_RE.fullmatch(candidate):
            raise CannotEvaluate("delivery line {} (its first non-WORKER_STATUS line) is not a full "
                                 "40-hex SHA".format(number))
        if not _pin_matches(candidate, pin):
            raise CannotEvaluate("delivery SHA {} differs from BRIEF-PIN {}".format(candidate, pin))
        return lines[index + 1:]
    raise CannotEvaluate("the delivery has no SHA line")


# ---------------------------------------------------------------------------------------------------
# The declared Markdown subset
# ---------------------------------------------------------------------------------------------------

def _fence_content_line(line, indent):
    """A content line with up to `indent` leading spaces (the opening fence's own) removed."""
    count = 0
    while count < indent and count < len(line) and line[count] == " ":
        count += 1
    return line[count:]


def _segments(lines):
    """("line", number, text) for each line outside a fence and ("fence", number, content lines) for
    each fenced block; an unclosed fence is CannotEvaluate."""
    out = []
    fence = None
    for number, line in lines:
        if fence is None:
            match = FENCE_RE.match(line)
            if match and not (match.group(2)[0] == "`" and "`" in match.group(3)):
                fence = (number, len(match.group(1)), match.group(2), [])
                continue
            out.append(("line", number, line))
            continue
        start, indent, marker, content = fence
        close = FENCE_CLOSE_RE.fullmatch(line)
        if close and close.group(1)[0] == marker[0] and len(close.group(1)) >= len(marker):
            out.append(("fence", start, content))
            fence = None
            continue
        content.append(_fence_content_line(line, indent))
    if fence is not None:
        raise CannotEvaluate("unclosed fenced block opened at line {}".format(fence[0]))
    return out


def _inline_quotes(line):
    """The double-quoted spans of one line, read outside its inline code spans."""
    masked = list(line)
    for match in CODE_SPAN_RE.finditer(line):
        masked[match.start():match.end()] = ["\0"] * (match.end() - match.start())
    quotes = []
    index = 0
    while index < len(masked):
        closer = next((pair[1] for pair in INLINE_QUOTE_PAIRS if pair[0] == masked[index]), None)
        if closer is None:
            index += 1
            continue
        end = index + 1
        while end < len(masked) and masked[end] != closer:
            end += 1
        if end >= len(masked):
            break
        if line[index + 1:end].strip():
            quotes.append(line[index + 1:end])
        index = end + 1
    return quotes


def _new_item(number, line, heading):
    return {"item": number, "line": line, "heading": heading, "body": [], "quotes": []}


def _parse_delivery(lines):
    """(items, preamble, verdict lines) of the delivery body."""
    segments = _segments(lines)
    heads = [ITEM_RE.match(seg[2]) for seg in segments if seg[0] == "line"]
    heading_style = any(match and match.group(1) for match in heads)
    preamble = _new_item(None, None, "")
    current = preamble
    items = []
    verdicts = []
    quote_run = []

    def flush():
        if quote_run and any(text.strip() for _, text in quote_run):
            current["quotes"].append({"kind": "blockquote", "line": quote_run[0][0],
                                      "text": "\n".join(text for _, text in quote_run)})
        quote_run.clear()

    for kind, number, payload in segments:
        if kind == "fence":
            flush()
            if any(text.strip() for text in payload):
                current["quotes"].append({"kind": "fenced", "line": number, "text": "\n".join(payload)})
            continue
        quoted = BLOCKQUOTE_RE.match(payload)
        if quoted:
            quote_run.append((number, quoted.group(1)))
            continue
        flush()
        head = ITEM_RE.match(payload)
        if head and (head.group(1) or not heading_style):
            current = _new_item(int(head.group(2)), number, payload)
            items.append(current)
        else:
            current["body"].append(payload)
        verdict = VERDICT_RE.match(payload)
        if verdict:
            verdicts.append((number, verdict.group(1)))
        for text in _inline_quotes(payload):
            current["quotes"].append({"kind": "inline", "line": number, "text": text})
    flush()
    return items, preamble, verdicts


def _parse_questions(lines):
    """The brief's numbered questions: its first run of column-0 numbered lines counting from 1."""
    text_lines = [(number, text) for kind, number, text in _segments(lines) if kind == "line"]
    start = 0
    for index, (_, text) in enumerate(text_lines):
        if QUESTIONS_MARKER_RE.fullmatch(text.strip()):
            start = index + 1
            break
    questions = []
    current = None
    for number, text in text_lines[start:]:
        match = ITEM_RE.match(text)
        if match:
            if int(match.group(2)) != len(questions) + 1:
                if questions:
                    break
                continue
            current = {"number": len(questions) + 1, "line": number, "text": text, "open": True}
            questions.append(current)
            continue
        if current is not None and current["open"]:
            if text.strip():
                current["text"] += "\n" + text
            else:
                current["open"] = False
    return questions


def _verdict_value(raw):
    return " ".join(raw.replace("*", " ").split()).rstrip(".").upper()


# ---------------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------------

def _named_paths(text, tree, keep_unresolved):
    """(blob paths of the tree named in text, path-shaped names absent from the tree)."""
    found, unresolved = [], []
    for match in PATH_TOKEN_RE.finditer(text):
        token = match.group(0)
        if "://" in token:
            continue
        token = LINE_SUFFIX_RE.sub("", token.split("#", 1)[0].rstrip(".:")).rstrip(".:")
        if token.startswith("./"):
            token = token[2:]
        if not token:
            continue
        bound = [token] if "/" in token and token in tree.blobs else _basename_bindings(token, tree)
        if bound:
            found.extend(path for path in bound if path not in found)
        elif (keep_unresolved and PATHLIKE_RE.fullmatch(token) and token not in tree.dirs
              and FILE_NAME_RE.fullmatch(token.rsplit("/", 1)[1]) and token not in unresolved):
            unresolved.append(token)
    return found, unresolved


def _basename_bindings(token, tree):
    """The blobs a bare name (no slash) names: every blob with that basename, the root one first, when
    the name is a root blob or carries a file extension; a name that is no basename binds nothing."""
    if "/" in token or not (token in tree.blobs or FILE_NAME_RE.fullmatch(token)):
        return []
    return sorted((path for path in tree.blobs if path.rsplit("/", 1)[-1] == token),
                  key=lambda path: ("/" in path, path))


def _heading_names(heading, tree):
    return _named_paths(heading, tree, True)


def _quote_targets(item, questions, tree):
    """(files the item's quotes must match in, named paths absent from the tree, what named them)."""
    if item["item"] is None:
        return [], [], "none"
    found, unresolved = _heading_names(item["heading"], tree)
    if found or unresolved:
        return found, unresolved, "heading"
    question = questions.get(item["item"])
    if question is not None:
        found, _ = _named_paths(question["text"], tree, False)
        if found:
            return found, [], "question"
    return [], [], "none"


def _verbatim_in(content, data):
    """C2's match: data is one contiguous byte span of content (an excerpt may start or end mid-line)."""
    return data in content


def _normalised(text):
    return " ".join(text.split())


def _normalise_with_map(text):
    """_normalised(text) with, for each of its characters, the index of the character it came from."""
    out, index = [], []
    pending = None
    for position, char in enumerate(text):
        if char.isspace():
            if out and pending is None:
                pending = position
            continue
        if pending is not None:
            out.append(" ")
            index.append(pending)
            pending = None
        out.append(char)
        index.append(position)
    return "".join(out), index


def _normalised_match(tree, text, targets):
    """The nearest whitespace-normalised match of a failing quote, named files first: a diagnostic."""
    wanted = _normalised(text)
    if not wanted:
        return None
    tree.load_all()
    for path in list(targets) + [path for path in tree.paths() if path not in targets]:
        if path not in tree.blobs:
            continue
        content = _blob_bytes(tree, path).decode("utf-8", "replace")
        if wanted not in _normalised(content):
            continue
        norm, index = _normalise_with_map(content)
        at = norm.find(wanted)
        start, end = index[at], index[at + len(wanted) - 1] + 1
        return {"file": path, "line": content.count("\n", 0, start) + 1, "excerpt": content[start:end][:400]}
    return None


def _check_quote(tree, quote, targets, unresolved, named_by):
    """The C2 result for one quote; result["ok"] is the verdict."""
    data = quote["text"].encode("utf-8")
    result = {"kind": quote["kind"], "line": quote["line"], "text": quote["text"],
              "named_by": named_by, "searched": list(targets) + list(unresolved) or [WHOLE_TREE],
              "unresolved_named_paths": list(unresolved), "found_in": [], "other_files": [],
              "normalised_match": None, "ok": False, "reason": ""}
    if targets or unresolved:
        result["found_in"] = [path for path in targets if _verbatim_in(_blob_bytes(tree, path), data)]
        if not result["found_in"]:
            tree.load_all()
            others = [path for path in tree.paths()
                      if path not in targets and _verbatim_in(_blob_bytes(tree, path), data)]
            result["other_files"] = others[:MAX_LISTED]
            named = ", ".join(list(targets) + ["{} (absent from the pinned tree)".format(path)
                                               for path in unresolved])
            result["reason"] = "not verbatim in the named file {}".format(named) + (
                "; found only in {}".format(", ".join(others[:MAX_LISTED])) if others else
                "; not verbatim anywhere in the pinned tree")
    else:
        tree.load_all()
        hits = [path for path in tree.paths() if _verbatim_in(_blob_bytes(tree, path), data)]
        result["found_in"] = hits[:MAX_LISTED]
        if not hits:
            result["reason"] = "not verbatim anywhere in the pinned tree"
    result["ok"] = bool(result["found_in"])
    if not result["ok"]:
        result["normalised_match"] = _normalised_match(tree, quote["text"], targets)
    return result


def _rule_all_unverifiable(verdict, answered, total):
    """C1: a verdict of any kind with no answered item is no vote (a verdict counts only for answered
    items)."""
    if verdict is not None and answered == 0:
        return "verdict {!r} with no answered item (every item UNVERIFIABLE or absent; coverage 0/{})" \
            .format(verdict, total)
    return None


def _rule_quote_only(clean, quote_only, items):
    """C1: a clean verdict on a quote-only question list with no quote in any item is no vote."""
    if clean and quote_only and not any(item["quotes"] for item in items):
        return "clean verdict on a quote-only question list with no quote in any item"
    return None


# ---------------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------------

def evaluate(brief_path, delivery_path, repo):
    """(report, exit code) for one delivery against one brief and repository."""
    report = {"tool": "check_verifier_delivery", "verdict": None, "reasons": [], "pin": None,
              "delivery_verdict": None, "clean": False, "quote_only_questions": False,
              "coverage": None, "quotes_checked": 0, "quotes_failed": 0, "items": []}
    try:
        brief = _decode(_read_input(brief_path, "brief"), "brief")
        delivery = _decode(_read_input(delivery_path, "delivery"), "delivery")
        pin = _brief_pin(brief)
        report["pin"] = pin
        body = _delivery_body(delivery, pin)
        questions = _parse_questions(_lines(brief))
        items, preamble, verdicts = _parse_delivery(body)
        numbers = [item["item"] for item in items]
        for number in numbers:
            if numbers.count(number) > 1:
                raise CannotEvaluate("delivery item {} appears more than once".format(number))
            if questions and not 1 <= number <= len(questions):
                raise CannotEvaluate("delivery item {} answers no numbered question of the brief ({})"
                                     .format(number, len(questions)))
        tree = PinnedTree(repo, pin)
        by_number = {question["number"]: question for question in questions}
        failures = []
        for item in [preamble] + items:
            targets, unresolved, named_by = _quote_targets(item, by_number, tree)
            unverifiable = bool(UNVERIFIABLE_RE.search("\n".join([item["heading"]] + item["body"])))
            item["unverifiable"] = unverifiable
            results = [_check_quote(tree, quote, targets, unresolved, named_by) for quote in item["quotes"]]
            for result in results:
                if not result["ok"]:
                    failures.append("{} line {} {} quote: {}".format(
                        "preamble" if item["item"] is None else "item {}".format(item["item"]),
                        result["line"], result["kind"], result["reason"]))
            if item["item"] is not None or results:
                report["items"].append({"item": "preamble" if item["item"] is None else item["item"],
                                        "line": item["line"], "heading": item["heading"],
                                        "unverifiable": unverifiable if item["item"] is not None else None,
                                        "quotes": results})
            report["quotes_checked"] += len(results)
            report["quotes_failed"] += sum(1 for result in results if not result["ok"])
    except CannotEvaluate as exc:
        report["verdict"] = "CANNOT_EVALUATE"
        report["reasons"].append(str(exc))
        return report, 2
    no_vote = []
    values = sorted({_verdict_value(value) for _, value in verdicts})
    if not values:
        no_vote.append("no VERDICT line")
    elif len(values) > 1:
        no_vote.append("conflicting VERDICT lines: {}".format("; ".join(values)))
    else:
        report["delivery_verdict"] = values[0]
        report["clean"] = values[0] == CLEAN_VERDICT
    total = len(questions) if questions else len(items)
    answered = sum(1 for item in items if not item["unverifiable"])
    report["coverage"] = {"answered": answered, "total": total, "text": "{}/{}".format(answered, total)}
    quote_only = bool(questions) and all(QUOTE_WORD_RE.search(question["text"]) for question in questions)
    report["quote_only_questions"] = quote_only
    for reason in (_rule_all_unverifiable(report["delivery_verdict"], answered, total),
                   _rule_quote_only(report["clean"], quote_only, items)):
        if reason:
            no_vote.append(reason)
    if failures:
        report["verdict"] = "FAIL"
        report["reasons"] = failures + no_vote
        return report, 1
    if no_vote:
        report["verdict"] = "NO_VOTE"
        report["reasons"] = no_vote
        return report, 1
    report["verdict"] = "PASS"
    return report, 0


def _summary(report):
    head = report["reasons"][0] if report["reasons"] else "every quote verbatim in the pinned tree"
    coverage = report["coverage"]["text"] if report["coverage"] else "n/a"
    return "check_verifier_delivery: {}: {}; coverage {}; {} of {} quote(s) failed".format(
        report["verdict"], head, coverage, report["quotes_failed"], report["quotes_checked"])


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

SUITE_ID = "verifier-delivery-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
OTHER_SHA = "0123456789abcdef" * 2 + "01234567"
FIXTURE_FILES = {
    "docs/alpha.md": "# Alpha notes\n\nThe first rule is plain.\nThe second rule follows it.\n"
                     "The third rule closes the set.\n",
    "src/beta.py": "def beta(ready):\n    if ready:\n        return 1\n    return 0\n",
    "docs/gamma.md": "Only gamma carries this exact sentence.\n",
    "README.md": "Only the root readme carries this line.\n",
    ".preview/README.md": "| Tool | Role |\n| pattern-self-match.py | matches its own pattern |\n",
}
BRIEF_MIXED = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
               "1. Quote the first rule from docs/alpha.md.\n"
               "2. Quote the readiness guard from src/beta.py.\n"
               "3. Quote the sentence in docs/gamma.md.\n"
               "4. Does src/beta.py return 0 when not ready?\n\n"
               "Standing lines:\n1. This list restarts and holds no question.\n")
BRIEF_QUOTE_ONLY = BRIEF_MIXED.replace("4. Does src/beta.py return 0 when not ready?",
                                       "4. Quote the closing rule from docs/alpha.md.")
BRIEF_FIVE = BRIEF_MIXED.replace("\n\nStanding lines:", "\n5. Quote the closing rule from docs/alpha.md.\n\n"
                                                    "Standing lines:")
BRIEF_README = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
                "1. Quote the README.md table row for pattern-self-match.py.\n")
BRIEF_SLASH = BRIEF_README.replace("the README.md table row", "the table row of .preview/README.md")
A1 = '1. docs/alpha.md reads "The first rule is plain."'
A2 = "2. src/beta.py:\n```python\n    if ready:\n        return 1\n```"
A3 = '3. docs/gamma.md says "Only gamma carries this exact sentence."'
A4 = '4. Yes: "    return 0" ends beta().'


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _unverifiable(number):
    return "{}. UNVERIFIABLE: the file could not be read.".format(number)


def _delivery(pin, *items, verdict="VERDICT: NO BLOCKERS", lead=""):
    return "{}{}\n{}\n\n{}\n\nWORKER_STATUS: COMPLETE\n".format(lead, pin, verdict, "\n\n".join(items))


class _Fixture:
    """A throwaway repository with FIXTURE_FILES committed, plus brief and delivery files beside it."""

    def __init__(self, base):
        self.base = base
        self.repo = base / "repo"
        for rel, text in FIXTURE_FILES.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")
        self.pin = self._git("rev-parse", "HEAD").strip()
        self.count = 0

    def _git(self, *args):
        result = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                 "-C", str(self.repo), *args], capture_output=True, text=True,
                                timeout=TIMEOUT, check=False)
        if result.returncode != 0:
            raise CannotEvaluate("fixture git {} failed: {}".format(args[0], result.stderr.strip()))
        return result.stdout

    def files(self, delivery, brief=BRIEF_MIXED, pin=None):
        self.count += 1
        brief_path = self.base / "brief-{}.md".format(self.count)
        delivery_path = self.base / "delivery-{}.md".format(self.count)
        brief_path.write_text(brief.format(pin=pin or self.pin), encoding="utf-8")
        if isinstance(delivery, bytes):
            delivery_path.write_bytes(delivery)
        else:
            delivery_path.write_text(delivery, encoding="utf-8")
        return str(brief_path), str(delivery_path)

    def run(self, delivery, brief=BRIEF_MIXED, pin=None, repo=None):
        brief_path, delivery_path = self.files(delivery, brief, pin)
        return evaluate(brief_path, delivery_path, str(repo or self.repo))[0]


def _patched(name, replacement, thunk):
    """thunk() with this module's attribute `name` replaced, restored afterwards."""
    module = sys.modules[__name__]
    if not hasattr(module, name):
        raise CannotEvaluate("self-test patches an unknown attribute {}".format(name))
    saved = getattr(module, name)
    setattr(module, name, replacement)
    try:
        return thunk()
    finally:
        setattr(module, name, saved)


def _normalised_containment(content, data):
    return _normalised(data.decode("utf-8", "replace")) in _normalised(content.decode("utf-8", "replace"))


def _whole_line_only(content, data):
    return (b"\n" + data + b"\n") in (b"\n" + content + b"\n")


def _self_test_cases(fx):
    sha = fx.pin
    verdict = lambda report: report["verdict"]
    never = re.compile(r"(?!)")

    # C1 coverage
    all_unverifiable = _delivery(sha, *(_unverifiable(n) for n in range(1, 5)))
    check("c1/all-unverifiable-no-vote", verdict(fx.run(all_unverifiable)), "NO_VOTE")
    check("flip/c1-all-unverifiable",
          verdict(_patched("_rule_all_unverifiable", lambda *a: None, lambda: fx.run(all_unverifiable))),
          "PASS")
    one = fx.run(_delivery(sha, A1, A2, _unverifiable(3), A4, lead="WORKER_STATUS: RUNNING\n"))
    check("c1/one-unverifiable-passes", (verdict(one), one["coverage"]["text"]), ("PASS", "3/4"))
    no_quotes = _delivery(sha, *("{}. Confirmed after reading the file.".format(n) for n in range(1, 5)))
    check("c1/quote-only-without-quotes-no-vote", verdict(fx.run(no_quotes, BRIEF_QUOTE_ONLY)), "NO_VOTE")
    check("flip/c1-quote-only",
          verdict(_patched("_rule_quote_only", lambda *a: None, lambda: fx.run(no_quotes, BRIEF_QUOTE_ONLY))),
          "PASS")
    check("c1/quote-only-with-quotes-passes", verdict(fx.run(
        _delivery(sha, A1, A2, A3, '4. docs/alpha.md: "The third rule closes the set."'), BRIEF_QUOTE_ONLY)),
        "PASS")
    check("c1/mixed-list-without-quotes-passes", verdict(fx.run(no_quotes)), "PASS")
    blockers_none = _delivery(sha, *(_unverifiable(n) for n in range(1, 6)), verdict="VERDICT: BLOCKERS FOUND")
    report = fx.run(blockers_none, BRIEF_FIVE)
    check("c1/blockers-zero-coverage-no-vote", (verdict(report), report["coverage"]["text"]), ("NO_VOTE", "0/5"))
    clean_only = lambda found, answered, total: "no vote" if found == CLEAN_VERDICT and answered == 0 else None
    check("flip/c1-blockers-zero-coverage",
          verdict(_patched("_rule_all_unverifiable", clean_only, lambda: fx.run(blockers_none, BRIEF_FIVE))), "PASS")
    report = fx.run(_delivery(sha, A1, *(_unverifiable(n) for n in range(2, 5)), verdict="VERDICT: BLOCKERS FOUND"))
    check("c1/blockers-with-answered-item-passes", (verdict(report), report["coverage"]["text"]), ("PASS", "1/4"))
    check("c1/absent-verdict-no-vote", verdict(fx.run(_delivery(sha, A1, A2, A3, A4, verdict="Summary."))),
          "NO_VOTE")
    check("c1/conflicting-verdicts-no-vote",
          verdict(fx.run(_delivery(sha, A1, A2, A3, A4 + "\nVERDICT: BLOCKERS"))), "NO_VOTE")

    # C2 verbatim
    check("c2/exact-delivery-passes", verdict(fx.run(_delivery(sha, A1, A2, A3, A4))), "PASS")
    joined = _delivery(sha, '1. docs/alpha.md reads "The first rule is plain. The second rule follows it."',
                       A2, A3, A4)
    report = fx.run(joined)
    check("c2/joined-lines-fails", (verdict(report), report["quotes_failed"]), ("FAIL", 1))
    check("flip/c2-verbatim-joined",
          verdict(_patched("_verbatim_in", _normalised_containment, lambda: fx.run(joined))), "PASS")
    check("flip/c2-inline-quotes", verdict(_patched("INLINE_QUOTE_PAIRS", (), lambda: fx.run(joined))), "PASS")
    dropped = _delivery(sha, "1. docs/alpha.md:\n```\nThe first rule is plain.\nThe third rule closes the set.\n```",
                        A2, A3, A4)
    check("c2/dropped-middle-line-fails", verdict(fx.run(dropped)), "FAIL")
    check("flip/c2-fenced-blocks", verdict(_patched("FENCE_RE", never, lambda: fx.run(dropped))), "PASS")
    other = _delivery(sha, '1. The rule reads "Only gamma carries this exact sentence."', A2, A3, A4)
    report = fx.run(other)
    reason = " ".join(report["reasons"])
    check("c2/other-file-than-question-fails",
          (verdict(report), "docs/alpha.md" in reason, "docs/gamma.md" in reason), ("FAIL", True, True))
    check("flip/c2-named-file",
          verdict(_patched("_quote_targets", lambda *a: ([], [], "none"), lambda: fx.run(other))), "PASS")
    precedence = _delivery(sha, A1, A2, '3. docs/alpha.md says "Only gamma carries this exact sentence."', A4)
    check("c2/heading-file-precedence-fails", verdict(fx.run(precedence)), "FAIL")
    check("flip/c2-heading-names",
          verdict(_patched("_heading_names", lambda *a: ([], []), lambda: fx.run(precedence))), "PASS")
    absent = _delivery(sha, A1, A2, '3. docs/missing.md says "Only gamma carries this exact sentence."', A4)
    check("c2/heading-names-absent-path-fails", verdict(fx.run(absent)), "FAIL")
    check("flip/c2-heading-absent-path",
          verdict(_patched("_heading_names", lambda *a: ([], []), lambda: fx.run(absent))), "PASS")
    rewrapped = _delivery(sha, "1. docs/alpha.md:\n```\nThe second rule\nfollows it.\n```", A2, A3, A4)
    report = fx.run(rewrapped)
    diagnostic = report["items"][0]["quotes"][0]["normalised_match"] if report["items"] else None
    check("c2/rewrapped-fails-with-diagnostic",
          (verdict(report), diagnostic and diagnostic["file"], diagnostic and diagnostic["excerpt"]),
          ("FAIL", "docs/alpha.md", "The second rule follows it."))
    check("flip/c2-normalised-is-not-a-pass",
          verdict(_patched("_verbatim_in", _normalised_containment, lambda: fx.run(rewrapped))), "PASS")
    excerpt = _delivery(sha, '1. docs/alpha.md: "second rule follows"', A2, A3, A4)
    check("c2/mid-line-excerpt-passes", verdict(fx.run(excerpt)), "PASS")
    check("flip/c2-excerpt", verdict(_patched("_verbatim_in", _whole_line_only, lambda: fx.run(excerpt))),
          "FAIL")
    indented = _delivery(sha, A2)
    check("c2/fenced-indentation-exact-passes", verdict(fx.run(indented)), "PASS")
    check("flip/c2-fenced-indentation",
          verdict(_patched("_fence_content_line", lambda line, indent: line.lstrip(), lambda: fx.run(indented))),
          "FAIL")
    check("c2/fenced-indentation-changed-fails",
          verdict(fx.run(_delivery(sha, "2. src/beta.py:\n```\nif ready:\n    return 1\n```"))), "FAIL")
    check("c2/indented-fence-strips-only-its-own-indent-passes",
          verdict(fx.run(_delivery(sha, "2. src/beta.py:\n  ```\n      if ready:\n          return 1\n  ```"))),
          "PASS")
    blockquote = _delivery(sha, "1. docs/alpha.md:\n> The first rule is plain\n> and the second follows.")
    check("c2/blockquote-reworded-fails", verdict(fx.run(blockquote)), "FAIL")
    check("flip/c2-blockquote", verdict(_patched("BLOCKQUOTE_RE", never, lambda: fx.run(blockquote))), "PASS")
    check("c2/blockquote-exact-passes", verdict(fx.run(
        _delivery(sha, "1. docs/alpha.md:\n> The first rule is plain.\n> The second rule follows it."))), "PASS")
    check("c2/curly-quote-fabricated-fails", verdict(fx.run(
        _delivery(sha, "1. docs/alpha.md reads \u201cThe first rule is optional.\u201d"))), "FAIL")
    check("c2/code-span-not-a-quote-passes", verdict(fx.run(
        _delivery(sha, '1. docs/alpha.md: the flag `"made up"` is not a quote here; "The first rule is plain."'))),
        "PASS")
    check("c2/escape-added-fails", verdict(fx.run(
        _delivery(sha, "2. src/beta.py:\n```\n    if ready\\:\n```"))), "FAIL")
    check("c2/preamble-quote-checked-fails", verdict(fx.run(
        _delivery(sha, A1, A2, A3, A4).replace("VERDICT:", 'Intro "never in the tree"\nVERDICT:', 1))), "FAIL")
    row = _delivery(sha, '1. The row reads "| pattern-self-match.py | matches its own pattern |"')
    report = fx.run(row, BRIEF_README)
    check("c2/bare-name-binds-by-basename-passes",
          (verdict(report), report["items"][0]["quotes"][0]["found_in"] if report["items"] else None),
          ("PASS", [".preview/README.md"]))
    root_only = lambda token, tree: [token] if token in tree.blobs else []
    check("flip/c2-bare-name-root-only",
          verdict(_patched("_basename_bindings", root_only, lambda: fx.run(row, BRIEF_README))), "FAIL")
    neither = _delivery(sha, '1. The row reads "Only gamma carries this exact sentence."')
    report = fx.run(neither, BRIEF_README)
    reason = " ".join(report["reasons"])
    check("c2/bare-name-other-basename-fails",
          (verdict(report), ".preview/README.md" in reason, "docs/gamma.md" in reason), ("FAIL", True, True))
    check("flip/c2-bare-name-binding",
          verdict(_patched("_basename_bindings", lambda token, tree: [], lambda: fx.run(neither, BRIEF_README))),
          "PASS")
    check("c2/slash-path-binds-exactly-fails", verdict(fx.run(
        _delivery(sha, '1. The line reads "Only the root readme carries this line."'), BRIEF_SLASH)), "FAIL")
    nested = _delivery(sha, "## 1. docs/alpha.md\n1. one\n2. two\n\"The first rule is plain.\"",
                       "## 2. src/beta.py\n1. again\n\"    if ready:\"")
    check("items/heading-style-ignores-nested-lists-passes", verdict(fx.run(nested)), "PASS")
    check("items/duplicate-number-cannot-evaluate", verdict(fx.run(_delivery(sha, A1, A1))), "CANNOT_EVALUATE")
    check("items/unknown-number-cannot-evaluate",
          verdict(fx.run(_delivery(sha, A1, '5. docs/alpha.md "The first rule is plain."'))), "CANNOT_EVALUATE")
    check("items/unclosed-fence-cannot-evaluate",
          verdict(fx.run(_delivery(sha, "1. docs/alpha.md:\n```\nThe first rule is plain."))), "CANNOT_EVALUATE")

    # Pin, decode, git
    mismatch = _delivery(OTHER_SHA, A1, A2, A3, A4)
    check("pin/sha-mismatch-cannot-evaluate", verdict(fx.run(mismatch)), "CANNOT_EVALUATE")
    check("flip/pin-match", verdict(_patched("_pin_matches", lambda *a: True, lambda: fx.run(mismatch))), "PASS")
    check("pin/short-sha-cannot-evaluate", verdict(fx.run(_delivery(sha[:12], A1))), "CANNOT_EVALUATE")
    check("pin/missing-brief-pin-cannot-evaluate",
          verdict(fx.run(_delivery(sha, A1), brief=BRIEF_MIXED.replace("BRIEF-PIN", "PIN"))), "CANNOT_EVALUATE")
    undecodable = _delivery(sha, A1, A2, A3, A4).encode("utf-8").replace(b"ends beta", b"ends \xff beta")
    check("decode/undecodable-delivery-cannot-evaluate", verdict(fx.run(undecodable)), "CANNOT_EVALUATE")
    check("flip/decode-strict",
          verdict(_patched("_decode", lambda raw, label: raw.decode("utf-8", "replace"),
                           lambda: fx.run(undecodable))), "PASS")
    check("git/unresolvable-sha-cannot-evaluate",
          verdict(fx.run(_delivery(OTHER_SHA, A1), pin=OTHER_SHA)), "CANNOT_EVALUATE")
    plain = fx.base / "plain"
    plain.mkdir()
    check("git/not-a-repository-cannot-evaluate",
          verdict(fx.run(_delivery(sha, A1), repo=plain)), "CANNOT_EVALUATE")
    check("input/directory-delivery-cannot-evaluate",
          verdict(evaluate(fx.files("x")[0], str(plain), str(fx.repo))[0]), "CANNOT_EVALUATE")

    # The pinned tree, never the working tree
    (fx.repo / "docs" / "alpha.md").write_text("A fabricated working-tree sentence.\n", encoding="utf-8")
    working = _delivery(sha, '1. docs/alpha.md reads "A fabricated working-tree sentence."')
    check("tree/working-tree-text-fails", verdict(fx.run(working)), "FAIL")
    check("flip/tree-pinned-only",
          verdict(_patched("_blob_bytes", lambda tree, path: (Path(tree.repo) / path).read_bytes(),
                           lambda: fx.run(working))), "PASS")

    # The command line: exit codes, one JSON object on stdout, one summary line on stderr
    script = str(Path(__file__).resolve())
    for check_id, delivery, want_code, want_verdict in (
            ("cli/pass-exit-0", _delivery(sha, A1, A2, A3, A4), 0, "PASS"),
            ("cli/fail-exit-1", joined, 1, "FAIL"),
            ("cli/no-vote-exit-1", all_unverifiable, 1, "NO_VOTE"),
            ("cli/cannot-evaluate-exit-2", mismatch, 2, "CANNOT_EVALUATE")):
        brief_path, delivery_path = fx.files(delivery)
        result = subprocess.run([sys.executable, "-I", "-B", script, "--brief", brief_path,
                                 "--delivery", delivery_path, "--repo", str(fx.repo)],
                                capture_output=True, text=True, timeout=TIMEOUT, check=False)
        try:
            shown = json.loads(result.stdout)["verdict"]
        except (ValueError, KeyError, TypeError):
            shown = None
        check(check_id, (result.returncode, shown, len(result.stderr.splitlines())), (want_code, want_verdict, 1))
    result = subprocess.run([sys.executable, "-I", "-B", script, "--brief"], capture_output=True, text=True,
                            timeout=TIMEOUT, check=False)
    check("cli/usage-exit-2", result.returncode, 2)
    result = subprocess.run([sys.executable, "-I", "-B", script], capture_output=True, text=True,
                            timeout=TIMEOUT, check=False)
    check("cli/bare-ok-exit-0",
          (result.returncode, len(result.stdout.splitlines()), result.stdout.startswith("check_verifier_delivery: OK"),
           result.stderr), (0, 1, True, ""))
    check("bare/static-tables-valid", _static_config_problems(), [])
    check("bare/broken-grammar-refused", bool(_patched("ITEM_RE", never, _static_config_problems)), True)
    check("bare/broken-quote-pairs-refused",
          bool(_patched("INLINE_QUOTE_PAIRS", (('"',),), _static_config_problems)), True)


def _expected_check_ids():
    import tomllib
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def self_test(report_path=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    from _git_fixture_env import fixture_git_lifecycle
    try:
        with fixture_git_lifecycle(), tempfile.TemporaryDirectory(prefix="verifier-delivery-selftest-") as raw:
            _self_test_cases(_Fixture(Path(raw)))
    except (CannotEvaluate, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (each rule red on its fixture and flipped when patched "
          "out); execution set reconciled against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


USAGE = ("usage: check_verifier_delivery.py --brief FILE --delivery FILE --repo DIR\n"
         "       check_verifier_delivery.py [--self-test] [--execution-report ABS_PATH]")
# The bare run's probes of the declared grammar: (pattern name, line, whether it must match).
GRAMMAR_PROBES = (
    ("ITEM_RE", "1. answer", True), ("ITEM_RE", "Q1: answer", True), ("ITEM_RE", "**2.** answer", True),
    ("ITEM_RE", "### 3) answer", True), ("ITEM_RE", "Item 4. answer", True), ("ITEM_RE", "1.5 answer", False),
    ("FENCE_RE", "```python", True), ("FENCE_RE", "   ~~~", True), ("FENCE_RE", "    ```", False),
    ("BLOCKQUOTE_RE", "> quoted", True), ("PIN_LINE_RE", "BRIEF-PIN: " + "0" * 40, True),
    ("VERDICT_RE", "**VERDICT:** NO BLOCKERS", True), ("QUESTIONS_MARKER_RE", "QUESTIONS:", True),
)


def _static_config_problems():
    """The faults of this tool's own static tables (grammar patterns, quote pairs, limits); no input
    is read and no git is run."""
    module = sys.modules[__name__]
    problems = []
    for name, line, want in GRAMMAR_PROBES:
        pattern = getattr(module, name, None)
        if not isinstance(pattern, re.Pattern) or bool(pattern.match(line)) != want:
            problems.append("{} {} {!r}".format(name, "does not match" if want else "matches", line))
    if not FENCE_CLOSE_RE.fullmatch("```") or FENCE_CLOSE_RE.fullmatch("``` x"):
        problems.append("FENCE_CLOSE_RE does not hold the declared closing fence")
    if not (SHA_RE.fullmatch("0" * 40) and not SHA_RE.fullmatch("0" * 39)):
        problems.append("SHA_RE does not hold exactly 40 hex digits")
    if _verdict_value("**NO BLOCKERS.**") != CLEAN_VERDICT:
        problems.append("the clean verdict {!r} does not survive its own normalisation".format(CLEAN_VERDICT))
    if not (UNVERIFIABLE_RE.search("UNVERIFIABLE") and not UNVERIFIABLE_RE.search("unverifiable")):
        problems.append("UNVERIFIABLE_RE is not an upper-case word test")
    if not all(QUOTE_WORD_RE.search(word) for word in ("quote", "quotes", "quoted", "quoting")):
        problems.append("QUOTE_WORD_RE misses a declared form of quote")
    if not INLINE_QUOTE_PAIRS or not all(
            isinstance(pair, tuple) and len(pair) == 2 and all(isinstance(c, str) and len(c) == 1 for c in pair)
            for pair in INLINE_QUOTE_PAIRS):
        problems.append("INLINE_QUOTE_PAIRS is not a table of (opener, closer) characters")
    for name in ("TIMEOUT", "MAX_INPUT_BYTES", "MAX_TREE_BYTES", "MAX_LISTED"):
        value = getattr(module, name, None)
        if not isinstance(value, int) or value <= 0:
            problems.append("{} is not a positive integer".format(name))
    return problems


def _bare_run():
    problems = _static_config_problems()
    if problems:
        print("check_verifier_delivery: CANNOT_EVALUATE: static configuration invalid: {}".format(
            "; ".join(problems)), file=sys.stderr)
        return 2
    print("check_verifier_delivery: OK: static tables valid ({} grammar probes); lint a delivery with "
          "check_verifier_delivery.py --brief FILE --delivery FILE --repo DIR".format(len(GRAMMAR_PROBES)))
    return 0


def main(argv):
    if not argv:
        return _bare_run()
    if argv and argv[0] in ("--self-test", "--execution-report"):
        rest = argv[1:] if argv[0] == "--self-test" else list(argv)
        if not rest:
            return self_test()
        if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
            return self_test(rest[1])
        print(USAGE, file=sys.stderr)
        return 2
    options = {}
    pairs = list(argv)
    while len(pairs) >= 2 and pairs[0] in ("--brief", "--delivery", "--repo") and pairs[0] not in options:
        options[pairs[0]] = pairs[1]
        pairs = pairs[2:]
    if pairs or len(options) != 3:
        report = {"tool": "check_verifier_delivery", "verdict": "CANNOT_EVALUATE",
                  "reasons": ["usage: --brief, --delivery and --repo are each required exactly once"]}
        print(json.dumps(report, indent=2))
        print(USAGE.splitlines()[0], file=sys.stderr)
        return 2
    report, code = evaluate(options["--brief"], options["--delivery"], options["--repo"])
    print(json.dumps(report, indent=2))
    print(_summary(report), file=sys.stderr)
    return code


if __name__ == "__main__":
    _code = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
