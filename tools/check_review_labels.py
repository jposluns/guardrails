#!/usr/bin/env python3
"""Changed-lines gate: refuse review and work labels that added lines write into tracked files.

  check_review_labels.py [--protected REF] [--head REV]   exit 0 clean (a NOTE line may precede the
                                                         PASS), 1 finding, 2 cannot evaluate
  check_review_labels.py --write-baseline [--protected REF] [--head REV]
                                                         print the proposed baseline (stdout) and its
                                                         review listing (stderr); exit 1 when it grows
  check_review_labels.py --self-test                     fixture repositories, each row with its flip
  check_review_labels.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

WHAT IT JUDGES. Only the lines a change ADDS: git diff from the merge base of the protected branch and
the candidate, with renames followed (--find-renames), every file read as text (--text, so a file the
change or the base declares binary, with -diff, binary or a binary diff driver, is still read), no
context lines, no external diff driver and no textconv. The candidate is the working tree (every tracked
file, staged or not) unless --head names a commit, in which case it is that commit's tree. A line that
already sits on the merge base, a line the change deletes, and a file the change only renames are not
judged. An added line whose bytes are exactly those of a line the same diff deletes, in any file but an
excluded one (whose deleted lines credit nothing), is a
moved or reordered line and is not charged either (a line deleted from a file whose baseline entry has
its digest credits only that same file; see BASELINE): each deleted line credits one identical added line,
so a line copied more times than it is deleted is charged for each extra copy, and an edited label line
(any byte changed, its indentation included) is charged. Every other added line is searched for each
label shape in .aiqt/review-labels.toml (a tracked TOML file: a test name numbered after a review cycle,
a parenthesised or bracketed review or QA cycle tag, a numbered round, the words review and round side
by side, a numbered finding and a tracker backlog id by default, each one Python regular expression),
both as written and folded: compatibility-normalized with format characters (a zero-width space, say)
and combining marks removed, a closed table of Cyrillic and Greek look-alike letters (CONFUSABLES) read
as the Latin letters they imitate and any other script's decimal digit read as its ASCII digit, so a
fullwidth digit, a no-break space, a zero-width space, a combining accent, a Cyrillic o or an
Arabic-Indic digit does not hide a label; a line holding a NUL byte (a UTF-16 text file, say) is also
read with its NUL bytes removed. A line carrying the file's allow-marker as a word of its own is a
deliberate quotation and is exempt; the marker exempts that one line only, and inside a longer word it
exempts nothing. The marker is tested in every view the shapes are searched in, so a UTF-16 line
carrying it is exempt like its UTF-8 twin, while a view in which the marker is not a word of its own
(inside a longer word, or joined to a neighbour once the NUL bytes are removed or a look-alike is
folded) is still searched.

EXCLUDED PATHS. The configuration's exclude-paths list names the only files whose added lines are not
judged, each a reviewed exception justified beside it in that file: the sibling gate
tools/check_internal_names.py, whose source has to name the review-cycle vocabulary it refuses, its
ratchet baseline .aiqt/tracker-label-baseline.toml, whose canonical per-shape count keys (a shape name
then a count) that gate requires and regenerates, and the gates manifest .aiqt/core/gates/manifest.toml,
whose one-line residue for that gate has to name the same vocabulary. The match is on the file's path in the candidate,
exact and whole; nothing else in those files is judged, so a label added anywhere in them, the other
gate residues of the gates manifest included, is not caught here. Each entry must be a relative path
naming a regular file in the working tree the configuration is read from (a missing path, a directory,
a symlink or another non-regular file is refused, so an entry cannot outlive the file it excuses).

THE PROTECTED BRANCH is derived from the repository, in this order: --protected REF when given; else
origin/GITHUB_BASE_REF when that variable is set and not empty (a pull request run); else, when
refs/remotes/origin/HEAD exists (a local clone), origin/main: the branch origin/HEAD names when that is
origin/main, and origin/main too when origin/HEAD names any other branch and origin/main resolves (the
summary says origin/HEAD was set aside), while an origin/HEAD naming another branch with no origin/main
is cannot-evaluate; else, only when GITHUB_ACTIONS is "true", origin/GITHUB_REF_NAME (a push or manual
run, where the candidate usually IS that branch, so nothing is added and the run passes: there the pull
request run is the effective gate). No other fallback exists. A branch origin/HEAD names other than
origin/main is never judged against, whatever the remote's own HEAD says: in a clone of a checkout on a
feature branch (a linked worktree, say) origin/HEAD and the remote's HEAD both name that feature branch,
so judging against it would hide every line its commits add, the candidate at its tip or any number of
local commits past it. A repository whose protected branch is not main passes --protected.

A CANDIDATE AT OR BEHIND THE PROTECTED BRANCH (the merge base is the candidate commit itself, so every
committed line of the candidate already sits on that branch: a local main at origin/main, a clone of
main, a branch behind main) has no committed line of its own, so nothing committed can hide there; the
working tree is judged against the merge base as always (an uncommitted edit on main is judged against
the tip), and a NOTE line says the candidate is at or behind the branch, and that there is nothing to
judge when no line is added. This trusts origin/main no more than every other run does: a candidate
ahead of origin/main is never judged on the lines origin/main already holds either. With --protected,
GITHUB_BASE_REF or GITHUB_REF_NAME the caller named the branch, and a candidate at or behind it is
judged the same way with the same NOTE.

FAIL CLOSED (exit 2, never a pass): no protected branch can be derived, an origin/HEAD that names no
origin tracking branch, an origin/HEAD naming a branch other than origin/main when origin/main does not
resolve, a ref or revision that does not resolve to a commit, a
shallow repository (its merge base can be wrong), no common ancestor, more than one merge base (a
criss-cross history), git that cannot run or exits nonzero (an unreadable tracked file makes git diff
fail), a diff line the parser does not recognise, a hunk whose line counts do not add up or a binary
notice git prints despite --text, and a configuration file that is missing, not a regular file, over
MAX_CONFIG_BYTES, not UTF-8, not TOML, or off its closed schema, an exclude-paths entry naming no
regular file included. A finding (exit 1) names the file, the line number in the candidate and the
shape.

DISCLOSED LIMITS. A genuine binary file (one whose changed lines hold a NUL byte: an image, a font or a
zip archive) is read as raw lines like any other and counted in the summary, so a label stored in it
uncompressed is caught, but compressed or encoded content (UTF-16 aside) is not readable and a shape
that happens to appear in its bytes is a false positive; the excluded paths above are not judged at
all; a moved line is credited by exact bytes, so deleting a label line from any judged file lets the
same line be added once anywhere else (in that same file only, when the baseline has an entry for the
line there); an untracked file is not judged; a label outside the configured
shapes is not matched, among them a look-alike letter outside the CONFUSABLES table, punctuation the
shapes do not allow, a parenthesised tag with nothing after its number or with a comma after it, a
lower-case backlog id, a number written as a word, a cycle tag written after the word review (an
upper-case R and a number, which is this pack's rule-number spelling) and a label split, encoded or
wrapped across lines; prose that matches a shape without being a label is a
false positive that the allow-marker or a rewording settles (a numbered game or protocol round, its
snake_case name joined to the number by an underscore, prose in another script that folds onto a shape, a retry
round written with two spaces, the word finding before a count of matches, a test named after a cycle
for a reason of its own, a tag-like aside of a lower-case r or QA, a number and a word, a self-test
id or a tool, branch or gate named with the review-cycle vocabulary, such as an internal-name self-test
id in tools/selftest_checks.toml naming the tracker shapes that gate refuses); only line content is
judged, so a label in a path alone (a file named after a review cycle) is not caught; the
configuration is trusted
reviewed input, so a change that edits it can weaken it in the same change, which review of that file's
diff has to catch. origin/main is trusted as the protected branch by name: in a clone of a local
checkout it is that checkout's local main, so a commit there that was never pushed, or a labelled
branch merged into it locally, already sits on the merge base and is not judged (git ls-remote cannot
settle this, since the clone's remote is that checkout itself, which reports its own main); compare
with the upstream remote by passing --protected with the upstream branch or commit there. The other
direction holds too: an origin/main behind the upstream main (a checkout whose local main was not
updated) puts the merge base further back, so lines the upstream main added since, which a candidate
based on the newer main holds, are charged to it, a false positive that updating that main or the same
--protected settles. Commit
messages and pull request text are outside this gate.

BASELINE (shrink-only). .aiqt/review-label-baseline.toml grandfathers imported finding lines; it adds an
exemption source and replaces nothing, and an absent file is the empty baseline, which behaves exactly
as the gate did without one. A grandfathered line is not the only line it lets pass: the partition below
counts the lines its file holds, so the moved-line credit is pinned to keep one deleted line from
counting twice. Its closed schema is byte-exact to its own canonical render: the fixed
header, format-version = 1, source-commit (a full lower-case commit id) and one [[entry]] table per key
with path, line-sha256, shape and count, sorted by (path, line-sha256, shape), no duplicate, no comment,
LF line ends. A path must be normalized (the exclude-paths rules), printable ASCII without a quote or
backslash, and neither an excluded file nor the baseline itself; a shape must be a shape id of the
configuration; a count is an integer of at least 1. The digest is sha256 of the exact bytes the gate
judges: the line after the diff sigil in git's clean form, its LF excluded, a CR and any NUL byte kept,
never a folded or decoded view. It is read from the working tree (lstat a regular file, opened without
blocking or following a link, at most MAX_BASELINE_BYTES) or, with --head, from that commit's blob (mode
100644 or 100755), and the merge base's copy from the merge-base commit. A present file that is
malformed, non-canonical, not a regular file, oversized or not UTF-8 is cannot-evaluate at either
revision. The merge base's copy is checked for structure only: its shapes and paths are not held to the
candidate's configuration, so a change that retires a shape or excludes a baselined path while dropping
those entries is judged (RELEASED), not refused; the candidate's copy is held to that configuration.
PINNED MOVE CREDIT: a line deleted from a file for which the candidate baseline has an entry with that
line's digest (under any shape) credits only an identical line added to that same file, never one in
another file, and an added line takes such a same-file credit before an unpinned one; every other
deleted line credits as described above. Without the pin one deletion counts twice: as the move credit
for a copy added elsewhere, and as room under the entry (m(k) falls by one) for a copy added again in its
own file, so an extra copy of a baselined line passes. With it, a deleted baselined line either credits
a copy added to its own file (m(k) unchanged) or is left unspent, which happens only when every
identical line added to its file was credited, so no charged copy can use the room it leaves. Dropping the entry in the same change unpins the line, so a baselined line
moved to another file with its entry dropped is still a credited move. After the moved-line credit and
the allow-marker, for each key m(k) is the
number of finding lines with that digest and shape in the candidate file (one diff from the empty tree
with the gate's own diff options and parser, so core.autocrlf and filters give the same clean bytes) and
n(k) the candidate entry's count:
  - STALE: m(k) < n(k) is a finding (exit 1) for every entry, changed path or not;
  - GROWTH: a candidate count above the merge base's count for the same key (0 when absent there) is a
    finding (exit 1), key by key and never netted across keys; the header and source-commit are outside
    the comparison, deleting the file or lowering a count is a shrink (RELEASED), and a nonempty file
    where the merge base has none is growth;
  - PARTITION: of the a(k) added findings with key k, min(a(k), max(0, m(k) - n(k))) are charged, the
    last ones in diff order, and the rest are grandfathered (a NOTE line each); a line matching two
    shapes is charged for each shape that has no entry;
  - SOURCE COMMIT: only when the candidate baseline bytes differ from the merge base's, it must be the
    candidate commit or an ancestor of it, else cannot-evaluate, the message naming the --write-baseline
    run that records the candidate; an unchanged baseline is not checked, so a squash merge that drops
    it from history blocks no later change.
The review listing prints a BASELINE header, then GROWTH path:line: shape (description): excerpt [digest
count old to new] once per current occurrence, STALE and RELEASED lines; a digest is never shown alone.
In every listing line (the gate's FAIL, GROWTH, STALE, RELEASED and NOTE lines and --write-baseline's)
each byte below 0x20 (a tab and a carriage return included) and DEL is printed as \\xNN and every
non-ASCII character as its backslash escape, so a terminal control sequence in a line cannot hide the
path or the label the maintainer authorizes from.
--write-baseline reads committed objects only (B..C and C's blobs; the configuration, like the gate's,
from the working tree): each key with a finding on a line B..C adds gets its count in C, every other
entry of C's baseline is carried at min(its count, its count in C), zeros dropped, and the canonical
render with source-commit = C goes to stdout; no file is written. BOOTSTRAP: commit the import at C, run
python3 -I -B tools/check_review_labels.py --write-baseline --head C, redirect its stdout into the
baseline and commit it; the gate then fails on the pull request with GROWTH lines, which are the review
artefact, and the maintainer records an authorization naming the baseline's sha256 and entry count before
the change merges by the recorded override path; later changes pass. A second commit on the same branch
does not move the merge base, so no step inside a branch can authorize growth.

BASELINE RESIDUALS, disclosed rather than closed: (R1) identical lines within one key are
interchangeable, so one copy can be deleted and another added in the same file; (R2) a missing final LF
is not distinguished; (R3) a rename of a file with entries is stale plus growth and needs authorization,
and a path that is not printable ASCII (or holds a quote or backslash) cannot be baselined; (R4)
source-commit is provenance, checked only for ancestry when the file changes, so a hand-edited canonical
file that does not grow passes; (R5) growth authorization depends on an override mechanism outside the
repository, which this gate does not name or check; (R6) exclude-paths can still be widened inside a
change; (R7) a working-tree baseline with CRLF line ends is non-canonical, so cannot-evaluate; (R8) the
baseline file is judged like any other, so an entry whose path matches a shape is charged there and such
a file cannot be grandfathered; (R9) digests are not secret: a known label line can be hashed and
matched; (R10) editing a shape's regex can make entries stale, forcing a baseline shrink in the same
change; (R11) the gate's trust in origin/main carries over to the merge-base baseline; (R12) a malformed
baseline on main is cannot-evaluate for every branch based on it; (R13) the cost of the extra diff is
unmeasured; (R14) the pinned move credit makes a baselined line moved to another file with its entry
kept a charged line there as well as a stale entry, until the entry is dropped in the same change.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_review_labels.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import hashlib
import io
import os
import re
import stat
import subprocess
import tempfile
import tomllib
import unicodedata
from collections import Counter, namedtuple
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CONFIG_RELPATH = ".aiqt/review-labels.toml"
MAX_CONFIG_BYTES = 65536
TIMEOUT = 120
MAX_EXCERPT = 160
GIT = "git"
# The environment variables and the ref the protected-branch derivation reads (see the docstring).
BASE_REF_VAR = "GITHUB_BASE_REF"
REF_NAME_VAR = "GITHUB_REF_NAME"
ACTIONS_VAR = "GITHUB_ACTIONS"
ORIGIN_HEAD = "refs/remotes/origin/HEAD"
ORIGIN_PREFIX = "refs/remotes/origin/"
# The source name a protected branch derived from origin/HEAD carries.
ORIGIN_HEAD_SOURCE = "origin/HEAD"
# The one branch a clone's origin/HEAD derives: origin/HEAD naming any other branch is set aside for it.
DEFAULT_TRACKING = ORIGIN_PREFIX + "main"
# The diff the gate reads. Every option that config could change is named, so a local diff.renames,
# diff.external, textconv driver, prefix or colour setting cannot change what is judged, and --text
# reads a file a -diff or binary attribute (or a binary diff driver) declares binary as lines.
DIFF_OPTIONS = ("--no-color", "--no-ext-diff", "--no-textconv", "--text", "--unified=0", "--find-renames",
                "--src-prefix=a/", "--dst-prefix=b/", "--no-relative", "--ignore-submodules=all")
# The hunk line kinds that are judged: added lines only.
JUDGED_SIGILS = ("+",)
# The header lines a diff of this shape prints outside a hunk. A "Binary files" notice is not one: with
# --text git never prints it, so one is refused (cannot evaluate) rather than counted.
HEADER_PREFIXES = (b"diff --git ", b"index ", b"--- ", b"new file mode ", b"deleted file mode ",
                   b"old mode ", b"new mode ", b"similarity index ", b"dissimilarity index ",
                   b"rename from ", b"rename to ", b"copy from ", b"copy to ")
BINARY_NOTICE = b"Binary files "
HUNK_RE = re.compile(rb"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@")
CONFIG_KEYS = frozenset(("format-version", "allow-marker", "exclude-paths", "shape"))
SHAPE_KEYS = frozenset(("id", "description", "regex", "ignore-case"))
SHAPE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.ASCII)
MARKER_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,63}", re.ASCII)
# The look-alike letters the folded view replaces by the Latin letter they imitate: a closed table of
# Cyrillic and Greek letters (by code point, so this source stays ASCII), not the whole Unicode
# confusables list.
CONFUSABLES = str.maketrans({chr(code): latin for code, latin in (
    # Cyrillic lower case: a, e, o, p, c, y, x, i, j, s, d, q, w, k.
    (0x0430, "a"), (0x0435, "e"), (0x043E, "o"), (0x0440, "p"), (0x0441, "c"), (0x0443, "y"),
    (0x0445, "x"), (0x0456, "i"), (0x0458, "j"), (0x0455, "s"), (0x0501, "d"), (0x051B, "q"),
    (0x051D, "w"), (0x043A, "k"),
    # Cyrillic upper case: A, B, E, K, M, H, O, P, C, T, X, I, J, S.
    (0x0410, "A"), (0x0412, "B"), (0x0415, "E"), (0x041A, "K"), (0x041C, "M"), (0x041D, "H"),
    (0x041E, "O"), (0x0420, "P"), (0x0421, "C"), (0x0422, "T"), (0x0425, "X"), (0x0406, "I"),
    (0x0408, "J"), (0x0405, "S"),
    # Greek lower case: o, i, k, v, p and upper case A, B, E, Z, H, I, K, M, N, O, P, T, Y, X.
    (0x03BF, "o"), (0x03B9, "i"), (0x03BA, "k"), (0x03BD, "v"), (0x03C1, "p"),
    (0x0391, "A"), (0x0392, "B"), (0x0395, "E"), (0x0396, "Z"), (0x0397, "H"), (0x0399, "I"),
    (0x039A, "K"), (0x039C, "M"), (0x039D, "N"), (0x039F, "O"), (0x03A1, "P"), (0x03A4, "T"),
    (0x03A5, "Y"), (0x03A7, "X"))})
_C_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10, ord("v"): 11, ord("f"): 12,
              ord("r"): 13, ord('"'): 34, ord("\\"): 92}

Shape = namedtuple("Shape", ("id", "description", "pattern"))
Config = namedtuple("Config", ("marker", "shapes", "excluded"))
# kind is the hunk sigil ("+" for an added line); raw is the line's exact bytes, which the moved-line
# credit compares.
Added = namedtuple("Added", ("path", "number", "text", "kind", "raw"))
Diff = namedtuple("Diff", ("added", "deleted", "nul_paths"))
# raw is the added line's exact bytes, whose sha256 keys the finding in the baseline.
Finding = namedtuple("Finding", ("path", "number", "shape", "description", "text", "raw"))

# The shrink-only finding baseline (see the docstring's BASELINE section).
BASELINE_RELPATH = ".aiqt/review-label-baseline.toml"
MAX_BASELINE_BYTES = 1048576
BASELINE_KEYS = frozenset(("format-version", "source-commit", "entry"))
BASELINE_ENTRY_KEYS = frozenset(("path", "line-sha256", "shape", "count"))
DIGEST_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
COMMIT_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.ASCII)
BASELINE_HEADER = (
    "# The shrink-only finding baseline of tools/check_review_labels.py: each entry grandfathers up to count",
    "# added lines of the named file whose exact bytes have that sha256 and match that shape. Print it with",
    "# python3 -I -B tools/check_review_labels.py --write-baseline --head COMMIT; never edit it by hand.",
)
Baseline = namedtuple("Baseline", ("source", "entries", "data"))
# One finding line of the candidate file a baseline key names: the m(k) the gate counts.
Occurrence = namedtuple("Occurrence", ("path", "number", "shape", "description", "text"))
# growth: (key, old count, new count, occurrences); stale: (key, count, found); released: (key, old, new).
Ledger = namedtuple("Ledger", ("header", "growth", "stale", "released", "grandfathered"))
Evaluation = namedtuple("Evaluation", ("findings", "summary", "marker", "note", "ledger"))


class CannotEvaluate(Exception):
    """A required input (the base, the diff, a file or the configuration) cannot be read or judged."""


# --- configuration -------------------------------------------------------------------------------------
def _nonblocking_opener(path, flags):
    return os.open(path, flags | os.O_NONBLOCK)


def load_config(path):
    """The Config held in the file at `path`; CannotEvaluate when it cannot be read or is malformed. The
    file is opened without blocking (a FIFO in its place is refused, never waited on) and read at most
    MAX_CONFIG_BYTES + 1 bytes, so an oversized file is seen without being read whole."""
    try:
        with open(path, "rb", opener=_nonblocking_opener) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise CannotEvaluate("{} is not a regular file".format(path))
            data = handle.read(MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("{} cannot be read ({})".format(path, exc)) from None
    return parse_config(data, str(path))


def parse_config(data, where):
    """The Config of the configuration bytes `data`; CannotEvaluate on anything off the closed schema."""
    if len(data) > MAX_CONFIG_BYTES:
        raise CannotEvaluate("{} is larger than {} bytes".format(where, MAX_CONFIG_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{} is not UTF-8 ({})".format(where, exc)) from None
    try:
        doc = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError) as exc:
        # TOMLDecodeError is a ValueError; tomllib can also raise a bare ValueError, RecursionError
        # (deep nesting) or MemoryError, each disposed of here fail-closed.
        raise CannotEvaluate("{} cannot be parsed as TOML ({!r})".format(where, exc)) from None
    unknown = sorted(set(doc) - CONFIG_KEYS)
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(where, ", ".join(unknown)))
    version = doc.get("format-version")
    if type(version) is not int or version != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(where))
    marker = doc.get("allow-marker")
    if not isinstance(marker, str) or not MARKER_RE.fullmatch(marker):
        raise CannotEvaluate("{}: allow-marker must be a word of 3 to 64 letters, digits and hyphens "
                             "starting with a letter".format(where))
    rows = doc.get("shape")
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("{}: at least one [[shape]] table is required".format(where))
    shapes, seen = [], set()
    for index, row in enumerate(rows, 1):
        label = "{}: shape {}".format(where, index)
        if not isinstance(row, dict):
            raise CannotEvaluate("{} is not a table".format(label))
        extra = sorted(set(row) - SHAPE_KEYS)
        missing = sorted(key for key in ("id", "description", "regex") if key not in row)
        if extra or missing:
            raise CannotEvaluate("{}: unknown key(s) {} and missing key(s) {}".format(
                label, extra or "none", missing or "none"))
        shape_id, description, regex = row["id"], row["description"], row["regex"]
        ignore_case = row.get("ignore-case", False)
        if not isinstance(shape_id, str) or not SHAPE_ID_RE.fullmatch(shape_id) or shape_id in seen:
            raise CannotEvaluate("{}: id must be unique lower-case words joined by hyphens".format(label))
        seen.add(shape_id)
        if not isinstance(description, str) or not description.strip():
            raise CannotEvaluate("{}: description must be a non-empty string".format(label))
        if not isinstance(regex, str) or not regex:
            raise CannotEvaluate("{}: regex must be a non-empty string".format(label))
        if type(ignore_case) is not bool:
            raise CannotEvaluate("{}: ignore-case must be a boolean".format(label))
        try:
            pattern = re.compile(regex, re.ASCII | (re.IGNORECASE if ignore_case else 0))
        except (re.error, RecursionError, MemoryError, OverflowError) as exc:
            raise CannotEvaluate("{}: regex does not compile ({})".format(label, exc)) from None
        if pattern.search(""):
            raise CannotEvaluate("{}: regex matches an empty line, so it would refuse every line".format(
                label))
        shapes.append(Shape(shape_id, description, pattern))
    return Config(marker, tuple(shapes), _parse_excluded(doc.get("exclude-paths", []), where))


def _parse_excluded(entries, where):
    """The exclude-paths entries as a frozenset; CannotEvaluate unless each is a unique, normalized
    relative POSIX path (no empty, . or .. segment, no backslash, no control character)."""
    if not isinstance(entries, list):
        raise CannotEvaluate("{}: exclude-paths must be an array of path strings".format(where))
    seen = set()
    for entry in entries:
        if not isinstance(entry, str) or not entry or entry in seen:
            raise CannotEvaluate("{}: exclude-paths entries must be unique non-empty strings".format(where))
        parts = entry.split("/")
        if entry.startswith("/") or "\\" in entry or any(part in ("", ".", "..") for part in parts) \
                or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in entry) \
                or PurePosixPath(entry).as_posix() != entry:
            raise CannotEvaluate("{}: exclude-paths entry {!r} is not a normalized relative path".format(
                where, entry))
        seen.add(entry)
    return frozenset(seen)


def require_excluded_files(root, excluded):
    """CannotEvaluate unless every exclude-paths entry names a regular file under `root` (lstat, so a
    symlink is refused too): an entry naming nothing or a directory would excuse more than one reviewed
    file, or outlive the file it excuses."""
    for entry in sorted(excluded):
        try:
            mode = os.lstat(Path(root) / entry).st_mode
        except OSError as exc:
            raise CannotEvaluate("exclude-paths entry {!r} names no file ({})".format(entry, exc)) from None
        if stat.S_ISDIR(mode):
            raise CannotEvaluate("exclude-paths entry {!r} names a directory".format(entry))
        if not stat.S_ISREG(mode):
            raise CannotEvaluate("exclude-paths entry {!r} is not a regular file".format(entry))


# --- git ---------------------------------------------------------------------------------------------
def _git_env():
    """The caller's environment with every GIT_ variable dropped (no repository, index or config
    redirection), the system config skipped, the global config pinned to the null device and HOME and
    XDG_CONFIG_HOME pointed there too (so neither the caller's ~/.gitconfig, its XDG git config nor the
    attributes or ignore files they default to are read: a caller-configured core.fsmonitor never runs,
    and a caller's safe.directory trust no longer applies), and an offline, non-interactive, lock-free
    posture with replace refs ignored, so a grafted parent cannot move the merge base."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, HOME=os.devnull,
               XDG_CONFIG_HOME=os.devnull, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    return env


def _git(root, *args):
    try:
        # The auto-maintenance pins ride the argv in option position, so no read here can start a
        # detached gc or maintenance child that outlives the gate.
        return subprocess.run([GIT, "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                               "-c", "maintenance.auto=false", *args], capture_output=True,
                              timeout=TIMEOUT, env=_git_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git {} could not run ({})".format(args[0], exc)) from None


def _stderr(proc):
    return proc.stderr.decode("utf-8", "backslashreplace").strip() or "no message"


def _git_lines(root, what, *args):
    proc = _git(root, *args)
    if proc.returncode != 0:
        raise CannotEvaluate("{}: git exited {} ({})".format(what, proc.returncode, _stderr(proc)))
    return [line for line in proc.stdout.decode("utf-8", "backslashreplace").splitlines() if line]


def _resolve_commit(root, revision, what):
    lines = _git_lines(root, "{} {!r} does not resolve to a commit".format(what, revision),
                       "rev-parse", "--verify", "--quiet", "--end-of-options", revision + "^{commit}")
    if len(lines) != 1:
        raise CannotEvaluate("{} {!r} did not resolve to one commit".format(what, revision))
    return lines[0]


def protected_ref(root, explicit, environ):
    """(ref, source): the protected branch, derived in the docstring's order; CannotEvaluate when none."""
    if explicit is not None:
        return explicit, "--protected"
    base_ref = environ.get(BASE_REF_VAR, "")
    if base_ref:
        return ORIGIN_PREFIX + base_ref, BASE_REF_VAR
    proc = _git(root, "symbolic-ref", "--quiet", ORIGIN_HEAD)
    if proc.returncode == 0:
        target = proc.stdout.decode("utf-8", "backslashreplace").strip()
        if not target.startswith(ORIGIN_PREFIX) or target == ORIGIN_HEAD:
            raise CannotEvaluate("origin/HEAD names {!r}, not an origin tracking branch".format(target))
        return origin_head_branch(root, target)
    if proc.returncode != 1:
        raise CannotEvaluate("origin/HEAD cannot be read: git exited {} ({})".format(
            proc.returncode, _stderr(proc)))
    if environ.get(ACTIONS_VAR) == "true" and environ.get(REF_NAME_VAR, ""):
        return ORIGIN_PREFIX + environ[REF_NAME_VAR], REF_NAME_VAR
    raise CannotEvaluate("no protected branch: pass --protected REF, or point origin/HEAD at it with "
                         "git remote set-head origin --auto")


def origin_head_branch(root, target):
    """(ref, source) for an origin/HEAD naming the tracking branch `target`: origin/main itself, or
    origin/main in place of any other branch when origin/main resolves (the source says origin/HEAD was
    set aside); CannotEvaluate otherwise. A branch origin/HEAD names other than origin/main may be a
    feature branch holding the candidate (a clone of a linked worktree, whose remote HEAD is that branch
    too), and judging against it hides every line its commits add, at its tip and past it."""
    if target == DEFAULT_TRACKING:
        return target, ORIGIN_HEAD_SOURCE
    proc = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options",
                DEFAULT_TRACKING + "^{commit}")
    if proc.returncode == 0:
        return DEFAULT_TRACKING, "{}, which names {} and is set aside".format(ORIGIN_HEAD_SOURCE, target)
    if proc.returncode != 1:
        raise CannotEvaluate("{} cannot be read: git exited {} ({})".format(
            DEFAULT_TRACKING, proc.returncode, _stderr(proc)))
    raise CannotEvaluate(
        "origin/HEAD names {!r}, not {!r}, and {!r} does not resolve; a branch origin/HEAD names other "
        "than main may be a feature branch holding the candidate, so it is never judged against: pass "
        "--protected REF".format(target, DEFAULT_TRACKING, DEFAULT_TRACKING))


def _require_full_history(root):
    lines = _git_lines(root, "the shallow-repository probe failed", "rev-parse", "--is-shallow-repository")
    if lines != ["false"]:
        raise CannotEvaluate("the repository is shallow (or the probe printed {!r}), so its merge base "
                             "can be wrong; fetch the full history".format(lines))


def _one_base(bases, head_sha):
    """The single merge base; CannotEvaluate on none or several. `head_sha` lets the self-test's flip
    show that refusing a missing base is load-bearing."""
    if not bases:
        raise CannotEvaluate("the protected branch and the candidate share no common ancestor")
    if len(bases) != 1:
        raise CannotEvaluate("{} merge bases ({}); the added lines are ambiguous".format(
            len(bases), ", ".join(bases)))
    return bases[0]


def merge_base(root, protected_sha, head_sha):
    proc = _git(root, "merge-base", "--all", protected_sha, head_sha)
    if proc.returncode not in (0, 1):
        raise CannotEvaluate("git merge-base exited {} ({})".format(proc.returncode, _stderr(proc)))
    bases = [line for line in proc.stdout.decode("ascii", "replace").splitlines() if line]
    if proc.returncode == 1 and bases:
        raise CannotEvaluate("git merge-base exited 1 but printed a base")
    return _one_base(bases, head_sha)


def _position_note(ref, protected_sha, base, head_sha, head, added):
    """The NOTE for a candidate at or behind the protected branch (the merge base is the candidate
    commit), else None: what is judged there, and that nothing is when no line is added."""
    if base != head_sha:
        return None
    where = "at the tip of" if protected_sha == head_sha else "behind"
    judged = ("the commit --head names is judged on no line" if head is not None
              else "only the working tree's uncommitted edits are judged, against {}".format(base[:12]))
    return "the candidate {} is {} {}, so it has no committed line of its own and {}{}".format(
        head_sha[:12], where, ref, judged,
        "; no line is added, so there is nothing to judge" if head is None and not added else "")


def _require_diff_ok(proc):
    if proc.returncode != 0:
        raise CannotEvaluate("git diff exited {} ({})".format(proc.returncode, _stderr(proc)))


def read_diff(root, base, head):
    """The raw diff bytes from `base` to the commit `head`, or to the working tree when `head` is None."""
    args = ["-c", "core.quotePath=true", "diff", *DIFF_OPTIONS, base]
    if head is not None:
        args.append(head)
    proc = _git(root, *args, "--")
    _require_diff_ok(proc)
    return proc.stdout


# --- diff parsing ------------------------------------------------------------------------------------
def _unquote(raw):
    """A C-quoted git path (core.quotePath) back to its bytes; CannotEvaluate on a malformed quote."""
    if len(raw) < 2 or not raw.endswith(b'"'):
        raise CannotEvaluate("malformed quoted path {!r} in the diff".format(raw))
    body, out, index = raw[1:-1], bytearray(), 0
    while index < len(body):
        byte = body[index]
        if byte != 0x5C:
            out.append(byte)
            index += 1
            continue
        octal = body[index + 1:index + 4]
        if len(octal) == 3 and all(0x30 <= b <= 0x37 for b in octal):
            out.append(int(octal, 8) & 0xFF)
            index += 4
        elif index + 1 < len(body) and body[index + 1] in _C_ESCAPES:
            out.append(_C_ESCAPES[body[index + 1]])
            index += 2
        else:
            raise CannotEvaluate("malformed escape in quoted path {!r} in the diff".format(raw))
    return bytes(out)


def _new_path(raw, prefix=b"b/"):
    """The display path of a +++ header's target, or with prefix a/ of a --- header's source (None for
    /dev/null)."""
    # git ends a header path holding a space with one tab, quoted or not; a tab inside a path is
    # always quoted, so the tab after the last byte is the terminator alone.
    if raw.endswith(b"\t"):
        raw = raw[:-1]
    if raw == b"/dev/null":
        return None
    if raw.startswith(b'"'):
        raw = _unquote(raw)
    if not raw.startswith(prefix):
        raise CannotEvaluate("diff path {!r} lacks the {} prefix".format(raw, prefix.decode("ascii")))
    return raw[2:].decode("utf-8", "backslashreplace")


def added_lines(diff):
    """Diff(added, deleted, nul_paths): every judged hunk line of `diff` as Added(path, number, text,
    kind, raw), a Counter of (source path, exact bytes) of every deleted line (the moved-line credit,
    the path so that an excluded file's deleted lines credit nothing), and the
    paths whose changed lines hold a NUL byte (a genuine binary read as lines). CannotEvaluate on an
    unrecognised line, a binary notice or a hunk whose line counts do not add up."""
    lines = diff.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    added, deleted, nul_paths, path, old_path = [], Counter(), set(), None, None
    old_no = new_no = old_left = new_left = 0
    for raw in lines:
        if old_left or new_left:
            sigil = raw[:1]
            if sigil == b"+" and new_left:
                number, new_no, new_left = new_no, new_no + 1, new_left - 1
            elif sigil == b"-" and old_left:
                number, old_no, old_left = old_no, old_no + 1, old_left - 1
                deleted[(old_path, raw[1:])] += 1
            elif sigil == b" " and old_left and new_left:
                number, old_no, new_no = new_no, old_no + 1, new_no + 1
                old_left, new_left = old_left - 1, new_left - 1
            elif sigil == b"\\":
                continue
            else:
                raise CannotEvaluate("unexpected line {!r} inside a hunk of {}".format(raw[:80], path))
            if b"\x00" in raw:
                nul_paths.add(path if path is not None else old_path)
            if sigil.decode("ascii") in JUDGED_SIGILS:
                added.append(Added(path, number, raw[1:].decode("utf-8", "replace"), sigil.decode("ascii"),
                                   raw[1:]))
            continue
        if raw.startswith(b"\\"):
            continue
        if raw.startswith(b"diff --git "):
            path = old_path = None
            continue
        if raw.startswith(b"--- "):
            old_path = _new_path(raw[4:], b"a/")
            continue
        if raw.startswith(b"+++ "):
            path = _new_path(raw[4:])
            continue
        if raw.startswith(BINARY_NOTICE):
            raise CannotEvaluate("git reported a binary file despite --text ({!r})".format(raw[:80]))
        if raw.startswith(b"@@ "):
            match = HUNK_RE.match(raw)
            if match is None:
                raise CannotEvaluate("malformed hunk header {!r}".format(raw[:80]))
            old_no, new_no = int(match.group(1)), int(match.group(3))
            old_left = 1 if match.group(2) is None else int(match.group(2))
            new_left = 1 if match.group(4) is None else int(match.group(4))
            if new_left and path is None:
                raise CannotEvaluate("a hunk adds lines with no target path")
            continue
        if raw.startswith(HEADER_PREFIXES):
            continue
        raise CannotEvaluate("unrecognised diff line {!r}".format(raw[:80]))
    if old_left or new_left:
        raise CannotEvaluate("the diff ends inside a hunk of {}".format(path))
    return Diff(added, deleted, nul_paths)


# --- judging -----------------------------------------------------------------------------------------
def _allowed(text, marker):
    """Whether the line carries the allow-marker as a word of its own, not inside a longer word."""
    return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(marker) + r"(?![A-Za-z0-9_-])", text) is not None


def _excluded(path, excluded):
    return path in excluded


def _credits(deleted, excluded):
    """The moved-line credits: a Counter of the exact bytes of every deleted line outside the excluded
    files, so a line deleted from an excluded file cannot credit the same line added elsewhere."""
    credits = Counter()
    for (path, raw), count in deleted.items():
        if not _excluded(path, excluded):
            credits[raw] += count
    return credits


def _pinned_lines(baseline):
    """The (path, line-sha256) pairs the candidate baseline has an entry for, under any shape: a line
    deleted from such a file with such a digest is a pinned credit (see _move_credits)."""
    if baseline is None:
        return frozenset()
    return frozenset((path, digest) for path, digest, _shape in baseline.entries)


def _move_credits(deleted, excluded, pinned):
    """The moved-line credits with the baseline's pin applied. A deleted line whose (path, digest) the
    candidate baseline has an entry for credits only an added line of exactly the same bytes in that same
    file (a Counter key (path, bytes)); every other deleted line outside the excluded files credits one
    in any file (a key of the bytes alone, from _credits). The partition counts a baselined file's
    deleted line as room under its entry, so the pin keeps one deletion from being spent twice: once as
    a move credit for a copy in another file and again as room for a copy in its own file."""
    free = Counter({key: count for key, count in deleted.items()
                    if (key[0], _line_digest(key[1])) not in pinned})
    credits = _credits(free, excluded)
    for (path, raw), count in deleted.items():
        if (path, _line_digest(raw)) in pinned and not _excluded(path, excluded):
            credits[(path, raw)] += count
    return credits


def credit_moves(added, deleted):
    """(charged, moved): the added lines left after each deleted line credits one added line of exactly
    the same bytes, in diff order, and the number credited (a moved or reordered line). A credit keyed
    (path, bytes) is pinned to that file and is taken before an unpinned credit of the same bytes, so a
    pinned credit is never left unspent while an identical line added to its file is charged."""
    left, charged, moved = Counter(deleted), [], 0
    for line in added:
        if line.kind == "+":
            for key in ((line.path, line.raw), line.raw):
                if left[key] > 0:
                    left[key] -= 1
                    moved += 1
                    break
            else:
                charged.append(line)
            continue
        charged.append(line)
    return charged, moved


def _views(text):
    """The text as written and, when it differs, its folded form: compatibility-decomposed (NFKD) with
    format characters (category Cf: a zero-width space or joiner, a byte-order mark) and combining marks
    (category Mn) removed, recomposed (NFKC), each CONFUSABLES letter replaced by the Latin letter it
    imitates and each other decimal digit by its ASCII digit, so such characters do not hide a label."""
    stripped = "".join(ch for ch in unicodedata.normalize("NFKD", text)
                       if unicodedata.category(ch) not in ("Cf", "Mn"))
    folded = "".join(str(unicodedata.decimal(ch)) if not ch.isascii() and unicodedata.decimal(ch, None)
                     is not None else ch
                     for ch in unicodedata.normalize("NFKC", stripped).translate(CONFUSABLES))
    return (text,) if folded == text else (text, folded)


def _line_views(line):
    """The views of an added line: its text's views and, for a line holding a NUL byte (a UTF-16 text
    file, say), also the views of its bytes with every NUL removed."""
    views = _views(line.text)
    if b"\x00" in line.raw:
        views += _views(line.raw.replace(b"\x00", b"").decode("utf-8", "replace"))
    return views


def _unmarked_views(line, marker):
    """The views of an added line the shapes are searched in: every view of _line_views that does not
    carry the allow-marker as a word of its own. The marker is tested in each view the shapes read, so a
    UTF-16 line carrying it (seen only once its NUL bytes are removed) is exempt like its UTF-8 twin,
    while a view in which the marker sits inside a longer word, or is joined to a neighbour once the NUL
    bytes are removed or a look-alike is folded, is still searched."""
    return tuple(view for view in _line_views(line) if not _allowed(view, marker))


def _shapes_of(line, config):
    """The shapes an added line is a finding for: each shape some unmarked view of it matches."""
    views = _unmarked_views(line, config.marker)
    return [shape for shape in config.shapes if any(shape.pattern.search(view) for view in views)]


def judge(added, config):
    findings = []
    for line in added:
        for shape in _shapes_of(line, config):
            findings.append(Finding(line.path, line.number, shape.id, shape.description, line.text, line.raw))
    return findings


# --- baseline ----------------------------------------------------------------------------------------
def _line_digest(raw):
    """The baseline digest of a line: sha256 of the exact bytes the gate judges (the line after the diff
    sigil in git's clean form, its LF excluded, a CR and any NUL byte kept), never a decoded view."""
    return hashlib.sha256(raw).hexdigest()


def _finding_key(item):
    return (item.path, _line_digest(item.raw), item.shape)


def _baseline_path_ok(path):
    """Whether a path can be written in the baseline: printable ASCII with no quote or backslash."""
    return all(0x20 <= ord(ch) <= 0x7E and ch not in "\"\\" for ch in path)


def render_baseline(source, entries):
    """The canonical bytes of a baseline: the fixed header, the version, the source commit and one
    [[entry]] table per key, sorted by (path, line-sha256, shape)."""
    lines = list(BASELINE_HEADER) + ["format-version = 1", "source-commit = \"{}\"".format(source)]
    for key in sorted(entries):
        path, digest, shape = key
        lines += ["", "[[entry]]", "path = \"{}\"".format(path), "line-sha256 = \"{}\"".format(digest),
                  "shape = \"{}\"".format(shape), "count = {}".format(entries[key])]
    return ("\n".join(lines) + "\n").encode("ascii")


def parse_baseline(data, where, config):
    """The Baseline held in the bytes `data`; CannotEvaluate on anything off its closed schema or not
    byte-exact to its own canonical render. With `config` None (the merge base's copy) the checks that
    read a configuration (a shape id of it, a path it excludes) are skipped: structure only."""
    if len(data) > MAX_BASELINE_BYTES:
        raise CannotEvaluate("{} is larger than {} bytes".format(where, MAX_BASELINE_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{} is not UTF-8 ({})".format(where, exc)) from None
    try:
        doc = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{} cannot be parsed as TOML ({!r})".format(where, exc)) from None
    unknown = sorted(set(doc) - BASELINE_KEYS)
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(where, ", ".join(unknown)))
    version = doc.get("format-version")
    if type(version) is not int or version != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(where))
    source = doc.get("source-commit")
    if not isinstance(source, str) or not COMMIT_RE.fullmatch(source):
        raise CannotEvaluate("{}: source-commit must be a full lower-case commit id".format(where))
    rows = doc.get("entry", [])
    if not isinstance(rows, list):
        raise CannotEvaluate("{}: entry must be an array of [[entry]] tables".format(where))
    shape_ids = None if config is None else {shape.id for shape in config.shapes}
    excluded = frozenset() if config is None else config.excluded
    entries = {}
    for index, row in enumerate(rows, 1):
        label = "{}: entry {}".format(where, index)
        if not isinstance(row, dict):
            raise CannotEvaluate("{} is not a table".format(label))
        if set(row) != BASELINE_ENTRY_KEYS:
            raise CannotEvaluate("{}: unknown key(s) {} and missing key(s) {}".format(
                label, sorted(set(row) - BASELINE_ENTRY_KEYS) or "none",
                sorted(BASELINE_ENTRY_KEYS - set(row)) or "none"))
        path, digest, shape, count = row["path"], row["line-sha256"], row["shape"], row["count"]
        if not isinstance(path, str) or not _baseline_path_ok(path):
            raise CannotEvaluate("{}: path must be printable ASCII with no quote or backslash".format(label))
        _parse_excluded([path], label)
        if path in excluded or path == BASELINE_RELPATH:
            raise CannotEvaluate("{}: path {!r} names an excluded file or the baseline itself".format(
                label, path))
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise CannotEvaluate("{}: line-sha256 must be 64 lower-case hex digits".format(label))
        if not isinstance(shape, str) or not SHAPE_ID_RE.fullmatch(shape):
            raise CannotEvaluate("{}: shape {!r} is not a shape id".format(label, shape))
        if shape_ids is not None and shape not in shape_ids:
            raise CannotEvaluate("{}: shape {!r} names no shape of the configuration".format(label, shape))
        if type(count) is not int or count < 1:
            raise CannotEvaluate("{}: count must be an integer of at least 1".format(label))
        if (path, digest, shape) in entries:
            raise CannotEvaluate("{}: duplicate entry for {} {} {}".format(label, path, digest[:12], shape))
        entries[(path, digest, shape)] = count
    if render_baseline(source, entries) != data:
        raise CannotEvaluate("{} is not the canonical render of its own entries (the fixed header, no "
                             "comment, entries sorted by path, line-sha256 and shape, LF line ends); "
                             "print it with --write-baseline".format(where))
    return Baseline(source, entries, data)


def _absent_baseline(where):
    """An absent baseline file is the empty baseline."""
    return None


def _nofollow_opener(path, flags):
    return os.open(path, flags | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))


def _read_worktree_baseline(root, config):
    path = Path(root) / BASELINE_RELPATH
    where = "{} in the working tree".format(BASELINE_RELPATH)
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return _absent_baseline(where)
    except OSError as exc:
        raise CannotEvaluate("{} cannot be read ({})".format(where, exc)) from None
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{} is not a regular file (a symlink, a directory or a FIFO, say)".format(where))
    try:
        with open(path, "rb", opener=_nofollow_opener) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise CannotEvaluate("{} is not a regular file".format(where))
            data = handle.read(MAX_BASELINE_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("{} cannot be read ({})".format(where, exc)) from None
    return parse_baseline(data, where, config)


def _read_committed_baseline(root, revision, config):
    where = "{} at {}".format(BASELINE_RELPATH, revision[:12])
    proc = _git(root, "ls-tree", "-z", "--full-tree", revision, "--", BASELINE_RELPATH)
    if proc.returncode != 0:
        raise CannotEvaluate("{}: git ls-tree exited {} ({})".format(where, proc.returncode, _stderr(proc)))
    records = [record for record in proc.stdout.split(b"\x00") if record]
    if not records:
        return _absent_baseline(where)
    meta, _tab, name = records[0].partition(b"\t")
    parts = meta.split(b" ")
    if len(records) != 1 or name != BASELINE_RELPATH.encode("ascii") or len(parts) != 3:
        raise CannotEvaluate("{}: unexpected git ls-tree output {!r}".format(where, proc.stdout[:120]))
    mode, kind, oid = parts
    if kind != b"blob" or mode not in (b"100644", b"100755"):
        raise CannotEvaluate("{} is not a regular file (mode {}, {})".format(
            where, mode.decode("ascii", "replace"), kind.decode("ascii", "replace")))
    size = _git_lines(root, "{}: its size cannot be read".format(where), "cat-file", "-s",
                      oid.decode("ascii", "replace"))
    if len(size) != 1 or not size[0].isdigit():
        raise CannotEvaluate("{}: git cat-file -s printed {!r}".format(where, size))
    if int(size[0]) > MAX_BASELINE_BYTES:
        raise CannotEvaluate("{} is larger than {} bytes".format(where, MAX_BASELINE_BYTES))
    proc = _git(root, "cat-file", "blob", oid.decode("ascii", "replace"))
    if proc.returncode != 0:
        raise CannotEvaluate("{}: git cat-file exited {} ({})".format(where, proc.returncode, _stderr(proc)))
    return parse_baseline(proc.stdout, where, config)


def read_baseline(root, revision, config):
    """The Baseline at the commit `revision`, or in the working tree when it is None; None when absent."""
    if revision is None:
        return _read_worktree_baseline(root, config)
    return _read_committed_baseline(root, revision, config)


def _empty_tree(root):
    lines = _git_lines(root, "the empty tree id cannot be computed", "hash-object", "-t", "tree", "--stdin")
    if len(lines) != 1:
        raise CannotEvaluate("git hash-object printed {!r} for the empty tree".format(lines))
    return lines[0]


def _occurrence_revision(base, head_sha, head):
    """The revision m(k) is counted at: the candidate (the working tree when --head is not given)."""
    return None if head is None else head_sha


def occurrences(root, revision, paths, config):
    """{key: [Occurrence]}: every finding line of the candidate files `paths`, at the commit `revision` or
    in the working tree when it is None, read as one diff from the empty tree with the gate's own diff
    options and parser, so the bytes counted are the clean-form bytes judged."""
    found = {}
    if not paths:
        return found
    args = ["-c", "core.quotePath=true", "diff", *DIFF_OPTIONS, _empty_tree(root)]
    if revision is not None:
        args.append(revision)
    proc = _git(root, *args, "--", *(":(literal)" + path for path in sorted(paths)))
    _require_diff_ok(proc)
    for line in added_lines(proc.stdout).added:
        for shape in _shapes_of(line, config):
            found.setdefault((line.path, _line_digest(line.raw), shape.id), []).append(
                Occurrence(line.path, line.number, shape.id, shape.description, line.text))
    return found


def _occurrence_count(items):
    return len(items)


def baseline_growth(candidate, prior):
    """The keys whose candidate count exceeds their merge-base count (0 when absent there), key by key:
    no sum is ever taken across keys."""
    return sorted(key for key, count in candidate.items() if count > prior.get(key, 0))


def stale_entries(entries, counts, findings):
    """The keys whose candidate file holds fewer finding lines with that digest than the entry's count.
    `findings` is unused here; a flip reads it."""
    return sorted(key for key, count in entries.items() if counts.get(key, 0) < count)


def partition(findings, entries, counts):
    """(charged, grandfathered): for each key, min(a, max(0, m - n)) of its a added findings are charged,
    the last ones in diff order, and the rest are grandfathered; a key with no entry is charged whole."""
    by_key = {}
    for item in findings:
        by_key.setdefault(_finding_key(item), []).append(item)
    exempt = set()
    for key, items in by_key.items():
        granted = entries.get(key, 0)
        if not granted:
            continue
        charged = min(len(items), max(0, counts.get(key, 0) - granted))
        exempt.update(id(item) for item in items[:len(items) - charged])
    return ([item for item in findings if id(item) not in exempt],
            [item for item in findings if id(item) in exempt])


def _baseline_changed(candidate, prior):
    return (None if candidate is None else candidate.data) != (None if prior is None else prior.data)


def _require_source_ancestor(root, source, head_sha):
    """CannotEvaluate unless the baseline's source commit is the candidate commit or an ancestor of it."""
    rewrite = ("; print the baseline again with python3 -I -B tools/check_review_labels.py --write-baseline "
               "--head {}, which records the candidate as its source-commit".format(head_sha[:12]))
    proc = _git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", source + "^{commit}")
    if proc.returncode != 0:
        raise CannotEvaluate("{} names source-commit {} that is not a commit here{}".format(
            BASELINE_RELPATH, source[:12], rewrite))
    proc = _git(root, "merge-base", "--is-ancestor", source, head_sha)
    if proc.returncode == 1:
        raise CannotEvaluate("{} names source-commit {} that is not an ancestor of the candidate {}{}".format(
            BASELINE_RELPATH, source[:12], head_sha[:12], rewrite))
    if proc.returncode != 0:
        raise CannotEvaluate("git merge-base --is-ancestor exited {} ({})".format(
            proc.returncode, _stderr(proc)))


def _merge_base_config(config):
    """The configuration the merge base's baseline is checked against: none, so it is checked for
    structure only (schema, canonical render, paths, digests, counts). Its shapes and paths are not held
    to the candidate's configuration, so a change that retires a shape or excludes a baselined path
    while dropping those entries is judged rather than refused."""
    return None


def apply_baseline(root, config, base, head_sha, head, findings, candidate):
    """(charged findings, Ledger): the baseline steps of the gate (the docstring's BASELINE section),
    given the candidate's baseline as already read for the moved-line credit."""
    prior = read_baseline(root, base, _merge_base_config(config))
    changed = _baseline_changed(candidate, prior)
    if candidate is not None and changed:
        _require_source_ancestor(root, candidate.source, head_sha)
    entries = {} if candidate is None else candidate.entries
    before = {} if prior is None else prior.entries
    found = occurrences(root, _occurrence_revision(base, head_sha, head), {key[0] for key in entries}, config)
    counts = {key: _occurrence_count(items) for key, items in found.items()}
    growth = [(key, before.get(key, 0), entries[key], found.get(key, []))
              for key in baseline_growth(entries, before)]
    stale = [(key, entries[key], counts.get(key, 0)) for key in stale_entries(entries, counts, findings)]
    released = [(key, count, entries.get(key, 0)) for key, count in sorted(before.items())
                if entries.get(key, 0) < count]
    charged, grandfathered = partition(findings, entries, counts)
    header = None
    if changed or entries:
        header = "{} source-commit {}, {} entr(ies) in the candidate, {} at the merge base".format(
            BASELINE_RELPATH, "none" if candidate is None else candidate.source[:12], len(entries),
            len(before))
    return charged, Ledger(header, growth, stale, released, grandfathered)


def _review_lines(ledger):
    """The review listing of the baseline: each growing key once per current occurrence (path, line,
    shape and excerpt beside its digest), each stale entry and each released count. Every text field is
    escaped (_escaped), so a control byte in a line, a path or a description cannot rewrite the listing."""
    lines = []
    for (path, digest, shape), old, new, items in ledger.growth:
        for item in items:
            lines.append("  GROWTH {}:{}: {} ({}): {} [{} count {} to {}]".format(
                _printable(item.path), item.number, _escaped(item.shape), _escaped(item.description),
                _printable(item.text), digest[:12], old, new))
        if not items:
            lines.append("  GROWTH {}: {} [{} count {} to {}], no such line in the file".format(
                _escaped(path), _escaped(shape), digest[:12], old, new))
    for (path, digest, shape), count, found in ledger.stale:
        lines.append("  STALE {}: {} {} count {}, {} found: lower to {} or drop the entry".format(
            _escaped(path), _escaped(shape), digest[:12], count, found, found))
    for (path, digest, shape), old, new in ledger.released:
        lines.append("  RELEASED {}: {} {} {} to {}".format(_escaped(path), _escaped(shape), digest[:12],
                                                             old, new))
    return lines


def _grandfathered_lines(ledger):
    return ["NOTE: review-label gate: grandfathered by {}: {}:{}: {} ({}): {}".format(
        BASELINE_RELPATH, _printable(item.path), item.number, _escaped(item.shape),
        _escaped(item.description), _printable(item.text)) for item in ledger.grandfathered]


def _write_revision(head_sha):
    """The revision the writer reads: the candidate commit, never the working tree."""
    return head_sha


def propose_baseline(root, protected=None, head=None, environ=None):
    """(canonical bytes, review lines, growth keys) of the baseline --write-baseline proposes for the
    candidate commit C: each key with a finding on a line B..C adds set to its count in C, every other
    entry of C's baseline carried at min(its count, the count in C), zeros dropped."""
    environ = os.environ if environ is None else environ
    config = load_config(Path(root) / CONFIG_RELPATH)
    require_excluded_files(root, config.excluded)
    _require_full_history(root)
    ref, _source = protected_ref(root, protected, environ)
    protected_sha = _resolve_commit(root, ref, "protected branch")
    head_sha = _resolve_commit(root, "HEAD" if head is None else head, "candidate")
    base = merge_base(root, protected_sha, head_sha)
    revision = _write_revision(head_sha)
    diff = added_lines(read_diff(root, base, revision))
    judged = [line for line in diff.added if not _excluded(line.path, config.excluded)]
    current = read_baseline(root, head_sha, config)
    judged, _moved = credit_moves(judged, _move_credits(diff.deleted, config.excluded,
                                                        _pinned_lines(current)))
    findings = judge(judged, config)
    for item in findings:
        if not _baseline_path_ok(item.path) or item.path == BASELINE_RELPATH:
            raise CannotEvaluate("{}:{} cannot be baselined (a path that is not printable ASCII, holds a "
                                 "quote or backslash, or is the baseline itself)".format(
                                     _printable(item.path), item.number))
    prior = read_baseline(root, base, _merge_base_config(config))
    old = {} if current is None else current.entries
    before = {} if prior is None else prior.entries
    found = occurrences(root, revision, {item.path for item in findings} | {key[0] for key in old}, config)
    counts = {key: _occurrence_count(items) for key, items in found.items()}
    proposal = {}
    for item in findings:
        proposal[_finding_key(item)] = counts.get(_finding_key(item), 0)
    for key, count in old.items():
        if key not in proposal:
            proposal[key] = min(count, counts.get(key, 0))
    proposal = {key: count for key, count in proposal.items() if count > 0}
    growth = baseline_growth(proposal, before)
    ledger = Ledger(None, [(key, before.get(key, 0), proposal[key], found.get(key, [])) for key in growth],
                    [], [(key, count, proposal.get(key, 0)) for key, count in sorted(before.items())
                         if proposal.get(key, 0) < count], [])
    return render_baseline(head_sha, proposal), _review_lines(ledger), growth


def write_baseline(root, protected=None, head=None, environ=None):
    """--write-baseline: the proposal on stdout (no file is written), its review listing on stderr; exit
    1 when it grows against the merge base's baseline, 0 otherwise, 2 when it cannot evaluate."""
    try:
        data, review, growth = propose_baseline(root, protected, head, environ)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: review-label baseline writer: {}".format(exc), file=sys.stderr)
        return 2
    sys.stdout.write(data.decode("ascii"))
    for line in review:
        print(line, file=sys.stderr)
    print("{}: the proposal {} against the merge base's baseline".format(
        "GROWTH" if growth else "NO GROWTH", "grows {} key(s)".format(len(growth)) if growth
        else "does not grow"), file=sys.stderr)
    return 1 if growth else 0


def evaluate(root, protected=None, head=None, environ=None):
    """The Evaluation of the repository at `root`; CannotEvaluate when it cannot be judged."""
    environ = os.environ if environ is None else environ
    config = load_config(Path(root) / CONFIG_RELPATH)
    require_excluded_files(root, config.excluded)
    _require_full_history(root)
    ref, source = protected_ref(root, protected, environ)
    protected_sha = _resolve_commit(root, ref, "protected branch")
    head_sha = _resolve_commit(root, "HEAD" if head is None else head, "candidate")
    base = merge_base(root, protected_sha, head_sha)
    diff = added_lines(read_diff(root, base, None if head is None else head_sha))
    judged = [line for line in diff.added if not _excluded(line.path, config.excluded)]
    skipped = len(diff.added) - len(judged)
    candidate = read_baseline(root, None if head is None else head_sha, config)
    judged, moved = credit_moves(judged, _move_credits(diff.deleted, config.excluded,
                                                       _pinned_lines(candidate)))
    findings, ledger = apply_baseline(root, config, base, head_sha, head, judge(judged, config), candidate)
    summary = "{} added line(s) in {} file(s) judged against merge base {} of {} (from {}){}{}{}{}{}".format(
        len(judged), len({line.path for line in judged}), base[:12], ref, source,
        "" if head is None else ", candidate {}".format(head_sha[:12]),
        "; {} moved line(s) not charged".format(moved) if moved else "",
        "; {} line(s) in excluded file(s) not judged".format(skipped) if skipped else "",
        "; {} file(s) with a NUL byte read as raw lines".format(len(diff.nul_paths))
        if diff.nul_paths else "",
        "; {} added finding(s) grandfathered".format(len(ledger.grandfathered)) if ledger.grandfathered
        else "")
    note = _position_note(ref, protected_sha, base, head_sha, head, diff.added)
    return Evaluation(findings, summary, config.marker, note, ledger)


def _escaped(text):
    """The text with every character outside printable ASCII escaped: a non-ASCII character as Python's
    backslash escape, and a control character (below 0x20, a tab and a carriage return included) or DEL
    as \\xNN, so no terminal control sequence (an erase-line escape, a carriage return that rewinds the
    line) reaches a listing line."""
    return "".join(ch if 0x20 <= ord(ch) < 0x7F else "\\x{:02x}".format(ord(ch)) if ord(ch) < 0x80
                   else ch.encode("ascii", "backslashreplace").decode("ascii") for ch in text)


def _printable(text):
    text = text.strip()
    if len(text) > MAX_EXCERPT:
        text = text[:MAX_EXCERPT] + "..."
    return _escaped(text)


def run(root, protected=None, head=None, environ=None):
    try:
        findings, summary, marker, note, ledger = evaluate(root, protected, head, environ)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: review-label gate: {}".format(exc), file=sys.stderr)
        return 2
    if note is not None:
        print("NOTE: review-label gate: {}".format(note))
    if ledger.header is not None:
        print("BASELINE: review-label gate: {}".format(ledger.header))
    for line in _review_lines(ledger):
        print(line)
    if findings or ledger.growth or ledger.stale:
        if findings:
            print("FAIL: {} review or work label(s) on added lines ({}):".format(len(findings), summary))
            for item in findings:
                print("  {}:{}: {} ({}): {}".format(_printable(item.path), item.number, _escaped(item.shape),
                                                    _escaped(item.description), _printable(item.text)))
        if ledger.growth or ledger.stale:
            print("FAIL: review-label baseline: {} key(s) grow against the merge base and {} entr(ies) are "
                  "stale ({}); growth needs the maintainer's recorded authorization, and a stale entry is "
                  "lowered or dropped".format(len(ledger.growth), len(ledger.stale), summary))
        if findings:
            print("Say what the code guards and why, not which review or tracker item asked for it; a "
                  "deliberate quotation carries the {} marker on its line.".format(marker))
        return 1
    for line in _grandfathered_lines(ledger):
        print(line)
    print("PASS: no review or work label on an added line ({})".format(summary))
    return 0


# --- self-test ---------------------------------------------------------------------------------------
# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "review-labels-selftest"
EXECUTED = []
FAILURES = []
NOT_RUN = []
# The incomplete exit: a run that skipped a row and failed none never exits 0.
INCOMPLETE_EXIT = 3


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


@contextlib.contextmanager
def _patched(**values):
    """Replace module attributes for the length of a block (a flip: one check removed), then restore."""
    module = sys.modules[__name__]
    saved = {name: getattr(module, name) for name in values}
    for name, value in values.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


def _fx_git(repo, *args, stdin_text=None):
    """A fixture git call under the shared fixture scrub (no caller config, no GIT_ redirection)."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1")
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Review Labels Self Test",
         "-c", "user.email=review-labels@example.invalid", "-c", "commit.gpgsign=false", *args],
        input="" if stdin_text is None else stdin_text, capture_output=True, text=True,
        timeout=TIMEOUT, env=env)
    if proc.returncode != 0:
        raise RuntimeError("fixture git {} failed: {}".format(args, proc.stderr.strip()))
    return proc.stdout.strip()


def _write(repo, rel, text):
    path = Path(repo) / rel
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _commit_all(repo, message):
    _fx_git(repo, "add", "-A")
    _fx_git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _fx_git(repo, "rev-parse", "HEAD")


def _gate(repo, protected="main", head=None, environ=None):
    """run()'s exit code and its whole output for one fixture repository."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run(repo, protected, head, {} if environ is None else environ)
    return code, out.getvalue() + err.getvalue()


def _config_text():
    with open(ROOT / CONFIG_RELPATH, encoding="utf-8") as handle:
        return handle.read()


def _without_shape(shape_id):
    real = load_config

    def loader(path):
        config = real(path)
        return config._replace(shapes=tuple(s for s in config.shapes if s.id != shape_id))
    return loader


def _widened(shape_id, regex):
    real = load_config

    def loader(path):
        config = real(path)
        return config._replace(shapes=tuple(
            s._replace(pattern=re.compile(regex, re.ASCII | re.IGNORECASE)) if s.id == shape_id else s
            for s in config.shapes))
    return loader


def _fail_open_loader(path):
    """A flip of the configuration's fail-closed load: any refusal reads as a configuration of no shapes."""
    try:
        return _REAL_LOAD_CONFIG(path)
    except CannotEvaluate:
        return Config("review-label-allow", (), frozenset())


_REAL_LOAD_CONFIG = load_config
_REAL_OCCURRENCES = occurrences
_REAL_ADDED_LINES = added_lines
_REAL_READ_BASELINE = read_baseline
_REAL_PARSE_BASELINE = parse_baseline
_REAL_RENDER_BASELINE = render_baseline


# The label lines the refused rows add, assembled at run time so that no contiguous label sits in this
# source (this gate judges its own added lines, and the internal-name gate's tracker layer scans it).
def _label_lines():
    rd = "rou" + "nd"
    return {
        "review-test-name": "def test_" + "r4_guard(self):",
        "review-tag-r": "x = 1  # (" + "r3 note) kept",
        "review-tag-qa": "y = 2  # (" + "qa2 M1)",
        "round-number": "# fixed in " + rd + " 5",
        "review-cycle-words": "# raised in the review " + rd,
        "finding-number": "# closes " + "find" + "ing 7",
        "backlog-id": "# tracked as " + "FI" + "X-" + "12",
    }


def _twin_lines(marker):
    rd = "rou" + "nd"
    return {
        "round-call": "total = " + rd + "(" + "1.5, 2)",
        "round-of": "# stop after " + rd + " 3 of 5",
        "round-retry": "# give up on retry " + rd + " 2",
        "tag-tuple": "pair = (r1, r2)",
        "tag-keyword": "ok = (r1 and r2)",
        "allow-marker": "# fixed in " + rd + " 6  # " + marker,
        "rule-number": "# the prose rules (" + "R1 execution verbs) apply",
    }


def _base_repo(tmp, name):
    """A fixture repository on branch main: the shipped configuration, a file whose third line already
    carries a label (seed.py), a file with a label line to delete (old.py) and one to rename (moved.py)."""
    repo = Path(tmp) / name
    os.makedirs(repo)
    _fx_git(repo, "init", "-q", "-b", "main")
    labels = _label_lines()
    _write(repo, CONFIG_RELPATH, _config_text())
    # Every path the shipped configuration excludes exists here as a placeholder, so the configuration
    # loads as it does in the repository.
    for entry in sorted(_REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH).excluded):
        _write(repo, entry, "placeholder = 1\n")
    _write(repo, "seed.py", "a = 1\nb = 2\n{}\n".format(labels["round-number"]))
    _write(repo, "old.py", "keep = 1\n{}\nkeep2 = 2\n".format(labels["finding-number"]))
    _write(repo, "moved.py", "{}\nvalue = 3\n".format(labels["backlog-id"]))
    first = _commit_all(repo, "base")
    if _FIXTURE_EMPTY_BASELINE:
        _write_bytes(repo, BASELINE_RELPATH, render_baseline(first, {}))
        _commit_all(repo, "an empty canonical baseline")
    return repo


# When true, every fixture _base_repo builds also commits an empty canonical baseline on main (the
# section 8 accounting row runs the rows that predate the baseline again this way).
_FIXTURE_EMPTY_BASELINE = False


def _self_test():
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle(), tempfile.TemporaryDirectory(prefix="review-labels-selftest-") as tmp:
        _rows(tmp)
    if FAILURES:
        print("SELF-TEST FAIL: check_review_labels {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        for item in NOT_RUN:
            print("  not run: " + item)
        return 1
    if NOT_RUN:
        for item in NOT_RUN:
            print("SELF-TEST INCOMPLETE: not run: " + item)
        return INCOMPLETE_EXIT
    print("SELF-TEST PASS: check_review_labels {} checks (each default label shape refused on an added "
          "line, named by file and line, and passing once its shape is removed; round() in code, a "
          "retry or counted round, a register tuple and an allow-marked line pass and are refused once "
          "the narrowing or the marker is removed; a deleted line, a pre-existing line and a pure rename "
          "are not judged and are once the diff judges them; the working tree is read; every "
          "fail-closed case exits 2 and passes once its refusal is removed; the protected branch is "
          "derived from GITHUB_BASE_REF (ahead of an existing origin/HEAD), origin/HEAD (origin/main, "
          "which an origin/HEAD naming another branch is set aside for, and cannot-evaluate without it) "
          "and the Actions ref name; a UTF-16 line carrying the allow-marker passes like its UTF-8 twin "
          "and one with the marker inside a longer word or none is refused; excluded "
          "files are not judged and every other file is; a moved or reordered line is not charged while "
          "an edited line and an extra copy are; a text file declared binary, a genuine binary and "
          "look-alike characters are read; textconv, an external diff, a submodule log, a GIT_DIR "
          "redirect, a replace ref and a caller's home configuration and fsmonitor are held off; "
          "git output off its shape is refused; a clean main, a "
          "clone of main and a branch behind main pass with a NOTE and an uncommitted label there is "
          "refused; a clone of a feature-branch checkout is judged against origin/main at the branch "
          "tip, one commit past it and several past it, and hides the branch's label once origin/HEAD's "
          "branch is trusted; the configuration size bound holds under, at and over; bad arguments exit "
          "2 without a scan; the baseline grandfathers exactly its counted keys per path, digest and "
          "shape, refuses componentwise growth, stale entries and every malformed or non-regular file at "
          "either revision, and its writer reads committed objects and round-trips)".format(len(EXECUTED)))
    return 0


def _rows(tmp):
    labels = _label_lines()
    start = len(EXECUTED)
    _existing_rows(tmp)
    existing = EXECUTED[start:]
    _baseline_rows(tmp, labels)
    _baseline_fail_closed_rows(tmp, labels)
    _baseline_write_rows(tmp, labels)
    _baseline_pin_rows(tmp, labels)
    _baseline_listing_rows(tmp, labels)
    _baseline_config_change_rows(tmp, labels)
    _baseline_merge_base_read_rows(tmp)
    _existing_rows_with_empty_baseline(tmp, existing)


def _existing_rows(tmp):
    """The rows that predate the baseline."""
    labels = _label_lines()
    marker = load_config(ROOT / CONFIG_RELPATH).marker
    twins = _twin_lines(marker)

    # Refused shapes: one commit adds every label line to added.py, at lines 1 to 7.
    repo = _base_repo(tmp, "refused")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    order = ("review-test-name", "review-tag-r", "review-tag-qa", "round-number", "review-cycle-words",
             "finding-number", "backlog-id")
    _write(repo, "added.py", "".join(labels[key] + "\n" for key in order))
    _commit_all(repo, "add labels")
    code, out = _gate(repo)
    for check_id, line_no, shape_id in (
            ("refuse/test-name", 1, "review-test-name"),
            ("refuse/paren-review-tag", 2, "review-tag"),
            ("refuse/paren-qa-tag", 3, "review-tag"),
            ("refuse/round-number", 4, "round-number"),
            ("refuse/review-cycle-words", 5, "review-cycle-words"),
            ("refuse/finding-number", 6, "finding-number"),
            ("refuse/backlog-id", 7, "backlog-id")):
        check(check_id, code == 1 and "  added.py:{}: {} (".format(line_no, shape_id) in out, out)
    check("refuse/one-finding-per-line", code == 1 and out.count("  added.py:") == 7, out)
    for check_id, line_no, shape_id in (
            ("flip/test-name-shape-removed", 1, "review-test-name"),
            ("flip/review-tag-shape-removed", 2, "review-tag"),
            ("flip/round-number-shape-removed", 4, "round-number"),
            ("flip/review-cycle-words-shape-removed", 5, "review-cycle-words"),
            ("flip/finding-number-shape-removed", 6, "finding-number"),
            ("flip/backlog-id-shape-removed", 7, "backlog-id")):
        with _patched(load_config=_without_shape(shape_id)):
            code, out = _gate(repo)
        check(check_id, "  added.py:{}: ".format(line_no) not in out, out)

    # Passing twins: one commit adds every twin to twins.py, at lines 1 to 6.
    repo = _base_repo(tmp, "twins")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    order = ("round-call", "round-of", "round-retry", "tag-tuple", "tag-keyword", "allow-marker",
             "rule-number")
    _write(repo, "twins.py", "".join(twins[key] + "\n" for key in order))
    _commit_all(repo, "add twins")
    code, out = _gate(repo)
    for check_id, line_no in (("pass/round-call-in-code", 1), ("pass/round-count-in-prose", 2),
                              ("pass/register-tuple", 4), ("pass/allow-marker", 6),
                              ("pass/upper-case-rule-number", 7)):
        check(check_id, code == 0 and "  twins.py:{}: ".format(line_no) not in out, out)
    with _patched(load_config=_widened("review-tag", r"\((?:r|qa)" + r"[0-9]+[a-z]?:?[ ]+[a-z]")):
        code, out = _gate(repo)
    check("flip/rule-number-flagged-when-case-folded", code == 1 and "  twins.py:7: review-tag" in out, out)
    bare = r"\bround" + r"\W{0,3}[0-9]"
    with _patched(load_config=_widened("round-number", bare)):
        code, out = _gate(repo)
    check("flip/round-call-flagged-when-widened", code == 1 and "  twins.py:1: round-number" in out, out)
    with _patched(load_config=_widened("round-number", r"\bround" + r"[ -]?[0-9]+\b")):
        code, out = _gate(repo)
    check("flip/round-count-flagged-without-exceptions",
          code == 1 and "  twins.py:2: round-number" in out and "  twins.py:3: round-number" in out, out)
    with _patched(load_config=_widened("review-tag", r"\((?:r|qa)" + r"[0-9]+")):
        code, out = _gate(repo)
    check("flip/register-tuple-flagged-when-widened",
          code == 1 and "  twins.py:4: review-tag" in out and "  twins.py:5: review-tag" in out, out)
    with _patched(_allowed=lambda text, marker: False):
        code, out = _gate(repo)
    check("flip/allow-marker-ignored", code == 1 and "  twins.py:6: round-number" in out, out)

    # Lines the change does not add: a deleted label line, a pre-existing label line beside an added
    # clean line, and a file renamed whole.
    repo = _base_repo(tmp, "not-added")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "old.py", "keep = 1\nkeep2 = 2\n")
    _write(repo, "seed.py", "a = 1\nb = 2\n{}\nc = 4\n".format(labels["round-number"]))
    _fx_git(repo, "mv", "moved.py", "renamed.py")
    _commit_all(repo, "delete, append and rename")
    code, out = _gate(repo)
    check("pass/deleted-line", code == 0 and "old.py" not in out, out)
    check("pass/pre-existing-line", code == 0 and "seed.py" not in out, out)
    check("pass/rename-only", code == 0 and "renamed.py" not in out, out)
    with _patched(JUDGED_SIGILS=("+", "-")):
        code, out = _gate(repo)
    check("flip/deleted-line-judged", code == 1 and "  old.py:2: finding-number" in out, out)
    empty_tree = _fx_git(repo, "hash-object", "-t", "tree", "--stdin")
    with _patched(merge_base=lambda root, protected_sha, head_sha: empty_tree):
        code, out = _gate(repo)
    check("flip/pre-existing-line-judged", code == 1 and "  seed.py:3: round-number" in out, out)
    # A pure rename is covered twice: rename detection prints no lines for it, and without detection its
    # added lines are credited as moved by the deleted ones; the flip removes both.
    no_renames = tuple("--no-renames" if o == "--find-renames" else o for o in DIFF_OPTIONS)
    with _patched(DIFF_OPTIONS=no_renames, credit_moves=lambda added, deleted: (added, 0)):
        code, out = _gate(repo)
    check("flip/rename-judged-without-renames-or-move-credit",
          code == 1 and "  renamed.py:1: backlog-id" in out, out)

    # The working tree is read: an uncommitted edit to a tracked file is judged; the committed tree
    # alone (the flip) does not see it.
    _write(repo, "seed.py", "a = 1\nb = 2\n{}\nc = 4\n{}\n".format(labels["round-number"],
                                                                   labels["review-test-name"]))
    code, out = _gate(repo)
    check("refuse/working-tree-edit", code == 1 and "  seed.py:5: review-test-name" in out, out)
    code, out = _gate(repo, head="HEAD")
    check("flip/committed-tree-misses-working-tree-edit", code == 0, out)

    # A path git quotes (a space and a non-ASCII letter) is named whole.
    repo = _base_repo(tmp, "quoted")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "dir x/café \"q\".py", labels["backlog-id"] + "\n")
    _commit_all(repo, "quoted path")
    code, out = _gate(repo)
    check("refuse/quoted-path-named", code == 1
          and "  dir x/caf\\xe9 \"q\".py:1: backlog-id" in out, out)

    _fail_closed_rows(tmp, labels)
    _derivation_rows(tmp, labels)
    _position_rows(tmp, labels)
    _exclusion_rows(tmp, labels)
    _move_rows(tmp, labels)
    _text_rows(tmp, labels)
    _hardening_rows(tmp, labels)
    _replace_ref_rows(tmp, labels)
    _git_output_rows()
    _bound_and_cli_rows()


# --- self-test: the baseline ---------------------------------------------------------------------------
def _write_bytes(repo, rel, data):
    path = Path(repo) / rel
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _sha(data):
    """A fixture digest computed apart from _line_digest, so a flip of that function cannot move it."""
    return hashlib.sha256(data).hexdigest()


def _put_baseline(repo, source, entries):
    """Write the canonical baseline of `entries` ({(path, line bytes, shape): count}) into the fixture."""
    _write_bytes(repo, BASELINE_RELPATH, render_baseline(
        source, {(path, _sha(raw), shape): count for (path, raw, shape), count in entries.items()}))


def _partition_whole_line(findings, entries, counts):
    """A flip of partition: an entry for any shape exempts every finding on that line."""
    lines = {(path, digest) for path, digest, _shape in entries}
    exempt = [item for item in findings if (item.path, _line_digest(item.raw)) in lines]
    return [item for item in findings if item not in exempt], exempt


def _partition_all_copies(findings, entries, counts):
    """A flip of partition: every added copy at a key with an entry is exempt."""
    exempt = [item for item in findings if _finding_key(item) in entries]
    return [item for item in findings if item not in exempt], exempt


def _partition_pathless(findings, entries, counts):
    """A flip of partition: the key drops the path, so an entry for one file exempts the line in any."""
    granted, found = Counter(), Counter()
    for (_path, digest, shape), count in entries.items():
        granted[(digest, shape)] += count
    for (_path, digest, shape), count in counts.items():
        found[(digest, shape)] += count
    by_key = {}
    for item in findings:
        by_key.setdefault((_line_digest(item.raw), item.shape), []).append(item)
    exempt = []
    for key, items in by_key.items():
        if granted[key]:
            charged = min(len(items), max(0, found[key] - granted[key]))
            exempt += items[:len(items) - charged]
    return [item for item in findings if item not in exempt], exempt


def _growth_by_total(candidate, prior):
    """A flip of baseline_growth: the totals are compared, so one key's growth nets against another's fall."""
    return sorted(candidate) if sum(candidate.values()) > sum(prior.values()) else []


def _growth_by_path_shape(candidate, prior):
    """A flip of baseline_growth: counts keyed by (path, shape), so a re-keyed line of the same count passes."""
    def totals(entries):
        out = Counter()
        for (path, _digest, shape), count in entries.items():
            out[(path, shape)] += count
        return out
    new, old = totals(candidate), totals(prior)
    return sorted(key for key in candidate if new[(key[0], key[2])] > old[(key[0], key[2])])


def _stale_without_added_line(entries, counts, findings):
    """A flip of stale_entries: an entry with no matching added line is stale, so every entry goes stale
    once its import merges."""
    added = {_finding_key(item) for item in findings}
    return sorted(key for key in entries if key not in added)


def _worktree_occurrences(root, revision, paths, config):
    """A flip of occurrences: the working tree's raw bytes are split on LF and counted, no clean form."""
    if revision is not None:
        return _REAL_OCCURRENCES(root, revision, paths, config)
    found = {}
    for path in sorted(paths):
        with open(Path(root) / path, "rb") as handle:
            raws = handle.read().split(b"\n")
        if raws and raws[-1] == b"":
            raws.pop()
        for number, raw in enumerate(raws, 1):
            line = Added(path, number, raw.decode("utf-8", "replace"), "+", raw)
            for shape in _shapes_of(line, config):
                found.setdefault((path, _line_digest(raw), shape.id), []).append(
                    Occurrence(path, number, shape.id, shape.description, line.text))
    return found


def _lf_marking_added_lines(diff):
    """A flip of added_lines: a line git marks as having no final LF is read with a marker appended, so
    the final LF is distinguished."""
    return _REAL_ADDED_LINES(diff.replace(b"\n\\ No newline at end of file", b"\\NOLF"))


def _fail_open_read_baseline(root, revision, config):
    """A flip of the baseline's fail-closed read: any refusal reads as the empty baseline."""
    try:
        return _REAL_READ_BASELINE(root, revision, config)
    except CannotEvaluate:
        return None


def _refuse_absent_baseline(where):
    raise CannotEvaluate("{} is absent (flip)".format(where))


def _refuse_entryless_baseline(data, where, config):
    parsed = _REAL_PARSE_BASELINE(data, where, config)
    if not parsed.entries:
        raise CannotEvaluate("{} has no entry (flip)".format(where))
    return parsed


def _review_lines_digests_only(ledger):
    return ["  GROWTH {}".format(digest[:12]) for (_path, digest, _shape), _old, _new, _items in ledger.growth]


def _baseline_repo(tmp, name):
    """A fixture repository on main with no baseline, and the base commit's id."""
    repo = _base_repo(tmp, name)
    return repo, _fx_git(repo, "rev-parse", "HEAD")


def _baseline_rows(tmp, labels):
    """The gate's baseline: componentwise growth, stale entries, the per-key partition and its digest."""
    la, lb = labels["backlog-id"], labels["finding-number"]
    la_raw, lb_raw = la.encode("utf-8"), lb.encode("utf-8")

    # An import whose entry already sits on main: the branch adds the line and it is grandfathered.
    repo, base = _baseline_repo(tmp, "baseline-import")
    _put_baseline(repo, base, {("imported.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "authorized entry")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "imported.py", "x = 1\n" + la + "\n")
    _commit_all(repo, "the import")
    code, out = _gate(repo)
    check("pass/baseline-import-grandfathered", code == 0 and "  imported.py:2:" not in out, out)
    check("review/pass-notes-grandfathered", code == 0 and "NOTE: review-label gate: grandfathered by "
          "{}: imported.py:2: backlog-id (".format(BASELINE_RELPATH) in out
          and "1 added finding(s) grandfathered" in out, out)
    with _patched(partition=lambda findings, entries, counts: (findings, [])):
        code, out = _gate(repo)
    check("flip/baseline-partition-grants-nothing", code == 1 and "  imported.py:2: backlog-id" in out, out)
    with _patched(_grandfathered_lines=lambda ledger: []):
        code, out = _gate(repo)
    check("flip/baseline-grandfathered-note-omitted", code == 0 and "grandfathered by" not in out, out)

    # A nonempty baseline appearing where the merge base has none is growth, listed per occurrence.
    repo, base = _baseline_repo(tmp, "baseline-growth")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "imported.py", la + "\nx = 1\n" + la + "\n")
    _put_baseline(repo, base, {("imported.py", la_raw, "backlog-id"): 2})
    _commit_all(repo, "import with its entry")
    code, out = _gate(repo)
    growth_line = "  GROWTH imported.py:{}: backlog-id (a tracker backlog id): {} [{} count 0 to 2]"
    check("refuse/baseline-growth-new-entry", code == 1 and "  GROWTH imported.py:1: backlog-id" in out
          and "  imported.py:1: backlog-id" not in out, out)
    check("review/growth-lists-occurrences", all(growth_line.format(number, la, _sha(la_raw)[:12]) in out
                                                  for number in (1, 3)), out)
    with _patched(baseline_growth=lambda candidate, prior: []):
        code, out = _gate(repo)
    check("flip/baseline-growth-unchecked", code == 0, out)
    with _patched(_review_lines=_review_lines_digests_only):
        code, out = _gate(repo)
    check("flip/growth-listed-by-digest-only", not all(
        growth_line.format(number, la, _sha(la_raw)[:12]) in out for number in (1, 3)), out)

    # Growth is componentwise: one key up and another down by the same amount still grows.
    repo, base = _baseline_repo(tmp, "baseline-netted")
    _write(repo, "a.py", la + "\n")
    _write(repo, "b.py", lb + "\nkeep = 1\n" + lb + "\n")
    _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1, ("b.py", lb_raw, "finding-number"): 2})
    main = _commit_all(repo, "two entries")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "a.py", la + "\n" + la + "\n")
    _write(repo, "b.py", lb + "\nkeep = 1\n")
    _put_baseline(repo, main, {("a.py", la_raw, "backlog-id"): 2, ("b.py", lb_raw, "finding-number"): 1})
    _commit_all(repo, "one key up, the other down")
    code, out = _gate(repo)
    check("refuse/baseline-growth-netted", code == 1 and "  GROWTH a.py:" in out
          and "RELEASED b.py: finding-number" in out, out)
    with _patched(baseline_growth=_growth_by_total):
        code, out = _gate(repo)
    check("flip/baseline-growth-netted-by-total", code == 0, out)

    # An entry whose line the branch deletes is stale; the same entry survives an unrelated branch.
    repo, base = _baseline_repo(tmp, "baseline-stale")
    _write(repo, "a.py", "x = 1\n" + la + "\n")
    _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "import merged")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "a.py", "x = 1\n")
    _commit_all(repo, "the line deleted")
    code, out = _gate(repo)
    check("refuse/baseline-stale-entry", code == 1 and "  STALE a.py: backlog-id {} count 1, 0 found".format(
        _sha(la_raw)[:12]) in out, out)
    with _patched(stale_entries=lambda entries, counts, findings: []):
        code, out = _gate(repo)
    check("flip/baseline-stale-unchecked", code == 0, out)
    _fx_git(repo, "checkout", "-q", "-b", "unrelated", "main")
    _write(repo, "clean.py", "value = 1\n")
    _commit_all(repo, "an unrelated change")
    code, out = _gate(repo)
    check("pass/baseline-survives-merge", code == 0, out)
    with _patched(stale_entries=_stale_without_added_line):
        code, out = _gate(repo)
    check("flip/baseline-stale-means-no-added-line", code == 1 and "  STALE a.py:" in out, out)

    # Entry counts: one entry of count 1 grants one copy; count 2 grants two.
    repo, base = _baseline_repo(tmp, "baseline-copies")
    _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1, ("c.py", la_raw, "backlog-id"): 2})
    _commit_all(repo, "two entries")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "a.py", la + "\n" + la + "\n")
    _write(repo, "c.py", la + "\n" + la + "\n")
    _commit_all(repo, "two copies each")
    code, out = _gate(repo)
    check("refuse/baseline-extra-copy-charged", code == 1 and out.count("  a.py:") == 1
          and "  a.py:2: backlog-id" in out, out)
    check("pass/baseline-two-copies-count-two", "  c.py:" not in out and "STALE c.py" not in out, out)
    with _patched(partition=_partition_all_copies):
        code, out = _gate(repo)
    check("flip/baseline-every-copy-exempt", code == 0, out)
    with _patched(_occurrence_count=lambda items: min(1, len(items))):
        code, out = _gate(repo)
    check("flip/baseline-count-capped-at-one", code == 1 and "  STALE c.py:" in out, out)

    # The exemption is per shape, per path and per digest.
    two_shapes = la + " after the review " + "rou" + "nd"
    repo, base = _baseline_repo(tmp, "baseline-keys")
    _put_baseline(repo, base, {("s.py", two_shapes.encode("utf-8"), "backlog-id"): 1,
                               ("a.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "entries")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "s.py", two_shapes + "\n")
    _write(repo, "a.py", la + "\n")
    _write(repo, "other.py", la + "\n")
    _commit_all(repo, "a two-shape line, and one line in its own file and another")
    code, out = _gate(repo)
    check("refuse/baseline-shape-scoped", code == 1 and "  s.py:1: review-cycle-words" in out
          and "  s.py:1: backlog-id" not in out, out)
    check("refuse/baseline-other-path", code == 1 and "  other.py:1: backlog-id" in out
          and "  a.py:1:" not in out, out)
    with _patched(partition=_partition_whole_line):
        code, out = _gate(repo)
    check("flip/baseline-exempts-whole-line", "  s.py:1: review-cycle-words" not in out, out)
    with _patched(partition=_partition_pathless):
        code, out = _gate(repo)
    check("flip/baseline-key-without-path", "  other.py:1: backlog-id" not in out, out)

    # A label replaced by different text of the same shape, its entry re-keyed: growth on the new key.
    la2 = la.replace("12", "13")
    repo, base = _baseline_repo(tmp, "baseline-replace")
    _write(repo, "a.py", la + "\n")
    _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1})
    main = _commit_all(repo, "import merged")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "a.py", la2 + "\n")
    _put_baseline(repo, main, {("a.py", la2.encode("utf-8"), "backlog-id"): 1})
    _commit_all(repo, "replaced and re-keyed")
    code, out = _gate(repo)
    check("refuse/baseline-same-count-replacement", code == 1 and "  GROWTH a.py:1: backlog-id" in out, out)
    with _patched(baseline_growth=_growth_by_path_shape):
        code, out = _gate(repo)
    check("flip/baseline-keyed-by-path-and-shape", code == 0, out)

    # The digest is over the exact judged bytes: a CR kept, git's clean form, no final LF, NUL bytes raw.
    utf16 = la.encode("utf-16-le")
    repo, base = _baseline_repo(tmp, "baseline-bytes")
    _write(repo, ".gitattributes", "*.txt -text\n")
    _write(repo, "f.py", "x = 1\n" + la + "\n")
    _put_baseline(repo, base, {("c.txt", la_raw + b"\r", "backlog-id"): 1, ("f.py", la_raw, "backlog-id"): 1,
                               ("u.bin", utf16, "backlog-id"): 1})
    _commit_all(repo, "entries")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write_bytes(repo, "c.txt", la_raw + b"\r\n")
    _write_bytes(repo, "f.py", b"x = 1\n" + la_raw)
    _write_bytes(repo, "u.bin", utf16 + "\n".encode("utf-16-le"))
    _commit_all(repo, "a CRLF line, a lost final LF and a UTF-16 line")
    code, out = _gate(repo)
    check("pass/baseline-crlf-kept", code == 0 and "  c.txt:" not in out and "STALE c.txt" not in out, out)
    check("pass/baseline-final-line-no-lf", code == 0 and "  f.py:" not in out and "STALE f.py" not in out, out)
    check("pass/baseline-utf16-raw", code == 0 and "  u.bin:" not in out and "STALE u.bin" not in out, out)
    with _patched(_line_digest=lambda raw: hashlib.sha256(raw.rstrip(b"\r")).hexdigest()):
        code, out = _gate(repo)
    check("flip/baseline-digest-strips-cr", code == 1 and "  c.txt:1: backlog-id" in out, out)
    with _patched(added_lines=_lf_marking_added_lines):
        code, out = _gate(repo)
    check("flip/baseline-final-lf-distinguished", code == 1 and "STALE f.py" in out, out)
    with _patched(_line_digest=lambda raw: hashlib.sha256(raw.replace(b"\x00", b"")).hexdigest()):
        code, out = _gate(repo)
    check("flip/baseline-digest-of-decoded-view", code == 1 and "  u.bin:1: backlog-id" in out, out)

    # core.autocrlf with a CRLF working tree: m(k) is read in git's clean form, not from the raw bytes.
    repo, base = _baseline_repo(tmp, "baseline-autocrlf")
    _put_baseline(repo, base, {("w.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "entry")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "w.py", la + "\n")
    _commit_all(repo, "the line")
    _fx_git(repo, "config", "core.autocrlf", "true")
    _write_bytes(repo, "w.py", la_raw + b"\r\n")
    code, out = _gate(repo)
    check("pass/baseline-autocrlf-worktree", code == 0, out)
    with _patched(occurrences=_worktree_occurrences):
        code, out = _gate(repo)
    check("flip/baseline-counted-from-raw-worktree", code == 1 and "STALE w.py" in out, out)

    # An entry's line moved to another file with its entry kept leaves the entry stale (m counted in the
    # candidate); the pinned move credit also charges the copy in the other file (R14).
    repo, base = _baseline_repo(tmp, "baseline-moved")
    _write(repo, "a.py", "x = 1\n" + la + "\n")
    _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "import merged")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "a.py", "x = 1\n")
    _write(repo, "g.py", la + "\n")
    _commit_all(repo, "the line moved")
    code, out = _gate(repo)
    check("refuse/baseline-moved-away-stale", code == 1 and "STALE a.py" in out
          and "  g.py:1: backlog-id" in out, out)
    with _patched(_occurrence_revision=lambda base_sha, head_sha, head: base_sha):
        code, out = _gate(repo)
    check("flip/baseline-counted-at-merge-base", "STALE a.py" not in out, out)

    # Absent, deleted and empty baselines.
    repo, base = _baseline_repo(tmp, "baseline-absent")
    _write(repo, "a.py", la + "\n")
    _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1})
    main = _commit_all(repo, "import merged")
    _fx_git(repo, "checkout", "-q", "-b", "shrink")
    _fx_git(repo, "rm", "-q", BASELINE_RELPATH)
    _commit_all(repo, "baseline deleted")
    code, out = _gate(repo)
    check("pass/baseline-deleted-is-shrink", code == 0 and "RELEASED a.py: backlog-id" in out, out)
    with _patched(_absent_baseline=_refuse_absent_baseline):
        code, out = _gate(repo)
    check("flip/baseline-deleted-refused", code == 2, out)
    _write(repo, "added.py", la.replace("12", "14") + "\n")
    _commit_all(repo, "a label with no baseline")
    code, out = _gate(repo)
    check("refuse/baseline-absent-label-charged", code == 1 and "  added.py:1: backlog-id" in out, out)
    with _patched(_absent_baseline=_refuse_absent_baseline):
        code, out = _gate(repo)
    check("flip/baseline-absent-refused", code == 2, out)
    repo, base = _baseline_repo(tmp, "baseline-empty")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _put_baseline(repo, base, {})
    _write(repo, "clean.py", "value = 1\n")
    _commit_all(repo, "an empty canonical baseline")
    code, out = _gate(repo)
    check("pass/baseline-empty-file", code == 0, out)
    with _patched(parse_baseline=_refuse_entryless_baseline):
        code, out = _gate(repo)
    check("flip/baseline-empty-file-refused", code == 2, out)

    # The source commit: checked for ancestry only when the baseline changed.
    repo, base = _baseline_repo(tmp, "baseline-source")
    _fx_git(repo, "checkout", "-q", "-b", "side")
    side = _commit_all(repo, "a commit main never merges")
    _fx_git(repo, "checkout", "-q", "main")
    _put_baseline(repo, side, {})
    _commit_all(repo, "a baseline naming the side commit (a squash merge)")
    _fx_git(repo, "checkout", "-q", "-b", "later")
    _write(repo, "clean.py", "value = 1\n")
    _commit_all(repo, "a later change")
    code, out = _gate(repo)
    check("pass/baseline-source-commit-unreachable-unchanged", code == 0, out)
    with _patched(_baseline_changed=lambda candidate, prior: True):
        code, out = _gate(repo)
    check("flip/baseline-source-commit-always-checked", code == 2 and "not an ancestor" in out, out)
    _fx_git(repo, "checkout", "-q", "-b", "grow", "main")
    _write(repo, "a.py", la + "\n")
    _put_baseline(repo, side, {("a.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "a changed baseline naming the side commit")
    code, out = _gate(repo)
    check("fail-closed/baseline-source-commit-not-ancestor", code == 2 and "not an ancestor" in out, out)
    check("review/not-ancestor-names-writer", code == 2 and "--write-baseline --head {}".format(
        _fx_git(repo, "rev-parse", "HEAD")[:12]) in out, out)
    with _patched(_require_source_ancestor=lambda root, source, head_sha: None):
        code, out = _gate(repo)
    check("flip/baseline-source-commit-accepted", code == 1, out)

    # The baseline file is itself judged: an entry whose path matches a shape is charged there.
    noted = "notes/review-" + "rou" + "nd.txt"
    repo, base = _baseline_repo(tmp, "baseline-judged")
    _write(repo, noted, la + "\n")
    main = _commit_all(repo, "a file whose path matches a shape")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _put_baseline(repo, main, {(noted, la_raw, "backlog-id"): 1})
    _commit_all(repo, "its entry")
    code, out = _gate(repo)
    check("refuse/baseline-file-judged", code == 1 and "  {}:".format(BASELINE_RELPATH) in out
          and ": review-cycle-words (" in out, out)
    with _patched(_excluded=lambda path, excluded: path in excluded or path == BASELINE_RELPATH):
        code, out = _gate(repo)
    check("flip/baseline-file-excluded", "  {}:".format(BASELINE_RELPATH) not in out, out)


def _baseline_fail_closed_rows(tmp, labels):
    """A malformed, non-canonical or non-regular baseline, at the candidate or the merge base, exits 2."""
    la = labels["backlog-id"]
    repo, base = _baseline_repo(tmp, "baseline-malformed")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "labelled.py", la + "\n")
    _commit_all(repo, "a label")
    digest = _sha(la.encode("utf-8"))
    good = render_baseline(base, {("labelled.py", digest, "backlog-id"): 1})
    block = render_baseline(base, {("labelled.py", digest, "backlog-id"): 1})[len(render_baseline(base, {})):]
    path = Path(repo) / BASELINE_RELPATH
    excluded = sorted(_REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH).excluded)[0]
    target = Path(tmp) / "baseline-target.toml"
    with open(target, "wb") as handle:
        handle.write(good)
    bodies = {
        "not-toml": b"[[entry\n",
        "unknown-key": good.replace(b"format-version = 1\n", b"format-version = 1\nextra = 1\n"),
        "unknown-shape": good.replace(b"\"backlog-id\"", b"\"no-such-shape\""),
        "duplicate": good + block,
        "unsorted": render_baseline(base, {("z.py", digest, "backlog-id"): 1}) + block,
        "comment": good + b"# a note\n",
        "count-zero": good.replace(b"count = 1", b"count = 0"),
        "count-bool": good.replace(b"count = 1", b"count = true"),
        "path-absolute": good.replace(b"\"labelled.py\"", b"\"/labelled.py\""),
        "path-dotdot": good.replace(b"\"labelled.py\"", b"\"../labelled.py\""),
        "excluded-path": good.replace(b"\"labelled.py\"", "\"{}\"".format(excluded).encode("ascii")),
        "own-path": good.replace(b"\"labelled.py\"", "\"{}\"".format(BASELINE_RELPATH).encode("ascii")),
        "bad-source-commit": good.replace(base.encode("ascii"), b"main"),
        "oversized": good + b"#" * MAX_BASELINE_BYTES,
        "not-utf8": b"\xff\n",
    }
    for check_id, flip_id, name, needle in (
            ("fail-closed/baseline-not-toml", "flip/baseline-not-toml-read-as-empty", "not-toml",
             "cannot be parsed"),
            ("fail-closed/baseline-unknown-key", "flip/baseline-unknown-key-read-as-empty", "unknown-key",
             "unknown key"),
            ("fail-closed/baseline-unknown-shape", "flip/baseline-unknown-shape-read-as-empty",
             "unknown-shape", "names no shape"),
            ("fail-closed/baseline-duplicate", "flip/baseline-duplicate-read-as-empty", "duplicate",
             "duplicate entry"),
            ("fail-closed/baseline-unsorted", "flip/baseline-unsorted-read-as-empty", "unsorted", "canonical"),
            ("fail-closed/baseline-comment", "flip/baseline-comment-read-as-empty", "comment", "canonical"),
            ("fail-closed/baseline-count-zero", "flip/baseline-count-zero-read-as-empty", "count-zero",
             "count must be"),
            ("fail-closed/baseline-count-bool", "flip/baseline-count-bool-read-as-empty", "count-bool",
             "count must be"),
            ("fail-closed/baseline-path-absolute", "flip/baseline-path-absolute-read-as-empty",
             "path-absolute", "normalized relative"),
            ("fail-closed/baseline-path-dotdot", "flip/baseline-path-dotdot-read-as-empty", "path-dotdot",
             "normalized relative"),
            ("fail-closed/baseline-excluded-path", "flip/baseline-excluded-path-read-as-empty",
             "excluded-path", "excluded file or the baseline itself"),
            ("fail-closed/baseline-own-path", "flip/baseline-own-path-read-as-empty", "own-path",
             "excluded file or the baseline itself"),
            ("fail-closed/baseline-bad-source-commit", "flip/baseline-bad-source-commit-read-as-empty",
             "bad-source-commit", "source-commit must be"),
            ("fail-closed/baseline-symlink", "flip/baseline-symlink-read-as-empty", "SYMLINK",
             "not a regular file"),
            ("fail-closed/baseline-directory", "flip/baseline-directory-read-as-empty", "DIRECTORY",
             "not a regular file"),
            ("fail-closed/baseline-fifo", "flip/baseline-fifo-read-as-empty", "FIFO", "not a regular file"),
            ("fail-closed/baseline-oversized", "flip/baseline-oversized-read-as-empty", "oversized",
             "larger than"),
            ("fail-closed/baseline-not-utf8", "flip/baseline-not-utf8-read-as-empty", "not-utf8",
             "not UTF-8")):
        body = bodies.get(name, name)
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)
        elif os.path.lexists(path):
            os.unlink(path)
        os.makedirs(path.parent, exist_ok=True)
        if body == "SYMLINK":
            os.symlink(target, path)
        elif body == "DIRECTORY":
            os.mkdir(path)
        elif body == "FIFO":
            os.mkfifo(path)
        else:
            _write_bytes(repo, BASELINE_RELPATH, body)
        code, out = _gate(repo)
        check(check_id, code == 2 and needle in out, out)
        with _patched(read_baseline=_fail_open_read_baseline):
            code, out = _gate(repo)
        check(flip_id, code == 1 and "  labelled.py:1: backlog-id" in out, out)
    if os.path.isdir(path) and not os.path.islink(path):
        os.rmdir(path)
    elif os.path.lexists(path):
        os.unlink(path)

    # The merge base's baseline is read with the same refusals.
    for check_id, flip_id, name, kind in (
            ("fail-closed/baseline-merge-base-symlink", "flip/baseline-merge-base-symlink-read-as-empty",
             "merge-base-symlink", "SYMLINK"),
            ("fail-closed/baseline-merge-base-malformed", "flip/baseline-merge-base-malformed-read-as-empty",
             "merge-base-malformed", "MALFORMED")):
        repo, base = _baseline_repo(tmp, "baseline-" + name)
        os.makedirs((Path(repo) / BASELINE_RELPATH).parent, exist_ok=True)
        if kind == "SYMLINK":
            os.symlink("../seed.py", Path(repo) / BASELINE_RELPATH)
        else:
            _write_bytes(repo, BASELINE_RELPATH, b"[[entry\n")
        _commit_all(repo, "a bad baseline on main")
        _fx_git(repo, "checkout", "-q", "-b", "work")
        _fx_git(repo, "rm", "-q", BASELINE_RELPATH)
        _write(repo, "labelled.py", la + "\n")
        _commit_all(repo, "baseline removed, label added")
        code, out = _gate(repo)
        check(check_id, code == 2 and "at {}".format(
            _fx_git(repo, "merge-base", "main", "HEAD")[:12]) in out, out)
        with _patched(read_baseline=_fail_open_read_baseline):
            code, out = _gate(repo)
        check(flip_id, code == 1, out)


def _write_exit(repo, protected="main", head=None):
    """write_baseline()'s exit code, its stdout and its stderr for one fixture repository."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = write_baseline(repo, protected, head, {})
    return code, out.getvalue(), err.getvalue()


def _unsorted_render(source, entries):
    """A flip of render_baseline: entries in insertion order, unsorted."""
    lines = list(BASELINE_HEADER) + ["format-version = 1", "source-commit = \"{}\"".format(source)]
    for (path, digest, shape), count in entries.items():
        lines += ["", "[[entry]]", "path = \"{}\"".format(path), "line-sha256 = \"{}\"".format(digest),
                  "shape = \"{}\"".format(shape), "count = {}".format(count)]
    return ("\n".join(lines) + "\n").encode("ascii")


def _sourceless_render(source, entries):
    """A flip of render_baseline: the source-commit field omitted."""
    return b"".join(line + b"\n" for line in _REAL_RENDER_BASELINE(source, entries).split(b"\n")
                    if line and not line.startswith(b"source-commit"))


def _baseline_write_rows(tmp, labels):
    """--write-baseline: deterministic, from committed objects only, records C, reports its own growth,
    and the gate grants exactly the keys it writes."""
    la, lb = labels["backlog-id"], labels["finding-number"]
    # Two label lines in one file, the larger digest first, so insertion order is not sorted order.
    first, second = sorted((la, lb), key=lambda text: _sha(text.encode("utf-8")), reverse=True)
    repo, base = _baseline_repo(tmp, "baseline-writer")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "imported.py", first + "\n" + second + "\n" + la + "\n")
    _write_bytes(repo, "u.bin", la.encode("utf-16-le") + "\n".encode("utf-16-le"))
    head_sha = _commit_all(repo, "an import")
    uncommitted = labels["review-test-name"]
    _write(repo, "imported.py", first + "\n" + second + "\n" + la + "\n" + uncommitted + "\n")
    config = _REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH)

    code, out, err = _write_exit(repo)
    code2, out2, _err2 = _write_exit(repo)
    try:
        parsed = parse_baseline(out.encode("ascii"), "writer output", config)
    except CannotEvaluate as exc:
        parsed = exc
    check("write/deterministic", out == out2 and isinstance(parsed, Baseline) and len(parsed.entries) == 3,
          (out, out2, parsed))
    with _patched(render_baseline=_unsorted_render):
        code_f, out_f, _err_f = _write_exit(repo)
    check("flip/write-unsorted-render-refused", _refused(parse_baseline, out_f.encode("ascii"), "flip",
                                                          config) == "refused", out_f)
    check("write/reads-committed-objects", isinstance(parsed, Baseline) and _sha(uncommitted.encode("utf-8"))
          not in out, out)
    with _patched(_write_revision=lambda head_sha: None):
        code_f, out_f, _err_f = _write_exit(repo)
    check("flip/write-reads-working-tree", _sha(uncommitted.encode("utf-8")) in out_f, out_f)
    check("write/records-source-commit", "source-commit = \"{}\"\n".format(head_sha) in out
          and isinstance(parsed, Baseline) and parsed.source == head_sha, out)
    with _patched(render_baseline=_sourceless_render):
        code_f, out_f, _err_f = _write_exit(repo)
    check("flip/write-source-commit-omitted", _refused(parse_baseline, out_f.encode("ascii"), "flip",
                                                        config) == "refused", out_f)
    check("write/reports-growth", code == 1 and code2 == 1 and "  GROWTH imported.py:" in err, (code, err))
    with _patched(baseline_growth=lambda candidate, prior: []):
        code_f, _out_f, err_f = _write_exit(repo)
    check("flip/write-growth-unreported", code_f == 0, err_f)

    # Round trip: the writer's output committed on the branch; the gate grants exactly its keys (growth
    # aside, which is the review step), so no added label is charged and no entry is stale.
    _fx_git(repo, "checkout", "-q", "--", "imported.py")
    _write_bytes(repo, BASELINE_RELPATH, out.encode("ascii"))
    _commit_all(repo, "the writer's baseline")
    code, gate_out = _gate(repo, head="HEAD")
    check("write/roundtrip", code == 1 and gate_out.count("  GROWTH ") == 4 and "STALE" not in gate_out
          and "review or work label(s) on added lines" not in gate_out, gate_out)
    _fx_git(repo, "checkout", "-q", "-b", "flip-decoded", head_sha)
    with _patched(_line_digest=lambda raw: hashlib.sha256(
            raw.replace(b"\x00", b"").decode("utf-8", "replace").encode("utf-8")).hexdigest()):
        code_f, out_f, _err_f = _write_exit(repo)
    _write_bytes(repo, BASELINE_RELPATH, out_f.encode("ascii"))
    _commit_all(repo, "a writer that hashed the decoded text")
    code, gate_out = _gate(repo, head="HEAD")
    check("flip/write-hashes-decoded-text", "STALE u.bin" in gate_out and "  u.bin:1: backlog-id" in gate_out,
          gate_out)

    # Bad write-mode argument lists exit 2 without a scan.
    bad = (["--write-baseline", "--bogus"], ["--write-baseline", "--write-baseline"],
           ["--write-baseline", "--head"], ["--write-baseline", "--self-test"],
           ["--write-baseline", "--head", "HEAD", "--head", "HEAD"])
    with _patched(run=lambda *args, **kwargs: 99, write_baseline=lambda *args, **kwargs: 98):
        got = [_main_exit(argv) for argv in bad]
        good_exit = _main_exit(["--write-baseline", "--protected", "main", "--head", "HEAD"])
    check("refuse/write-baseline-bad-args", got == [2] * len(bad) and good_exit == 98, (got, good_exit))
    with _patched(run=lambda *args, **kwargs: 99, write_baseline=lambda *args, **kwargs: 98,
                  _parse_options=lambda argv: {}):
        got = [_main_exit(argv) for argv in bad]
    check("flip/write-baseline-lenient-args", got == [98] * len(bad), got)


_REAL_GIT = _git


def _no_pins(baseline):
    """A flip of _pinned_lines: no line is pinned, so a deleted baselined line credits any file."""
    return frozenset()


def _all_pinned_credits(deleted, excluded, pinned):
    """A flip of _move_credits: every deleted line credits only its own file, baselined or not."""
    credits = Counter()
    for (path, raw), count in deleted.items():
        if not _excluded(path, excluded):
            credits[(path, raw)] += count
    return credits


def _baseline_pin_rows(tmp, labels):
    """A deleted line of a file whose baseline entry has its digest credits only that file, so it cannot
    be spent twice: as the move credit for a copy in another file and as room under the entry."""
    la = labels["backlog-id"]
    la_raw = la.encode("utf-8")
    # The layout of the case: zz/F.py holds the line with an entry of count 1; the branch deletes it,
    # adds it back lower in F and adds a copy in a second file, which sorts before F (aa/G.py, first in
    # diff order) or after it (zzz/G.py).
    repos = {}
    for other in ("aa/G.py", "zzz/G.py"):
        repo, base = _baseline_repo(tmp, "baseline-pin-" + other.split("/")[0])
        _write(repo, "zz/F.py", "a = 1\n" + la + "\nb = 2\n")
        _put_baseline(repo, base, {("zz/F.py", la_raw, "backlog-id"): 1})
        _commit_all(repo, "a baselined line")
        _fx_git(repo, "checkout", "-q", "-b", "work")
        _write(repo, "zz/F.py", "a = 1\nb = 2\n" + la + "\n")
        _write(repo, other, la + "\n")
        _commit_all(repo, "the line moved lower in its file and copied into another")
        repos[other] = repo
    code, out = _gate(repos["aa/G.py"])
    check("refuse/baseline-deletion-credits-once", code == 1 and "  aa/G.py:1: backlog-id" in out
          and "  zz/F.py:" not in out and "grandfathered" not in out and "STALE" not in out, out)
    with _patched(_pinned_lines=_no_pins):
        code, out = _gate(repos["aa/G.py"])
    check("flip/baseline-deletion-credits-twice", code == 0 and "grandfathered by" in out, out)
    code, out = _gate(repos["zzz/G.py"])
    check("refuse/baseline-deletion-credits-once-sorted-after", code == 1
          and "  zzz/G.py:1: backlog-id" in out and "  zz/F.py:" not in out and "grandfathered" not in out
          and "STALE" not in out, out)
    code, out, err = _write_exit(repos["aa/G.py"])
    check("write/pinned-deletion-reports-growth", code == 1 and "  GROWTH aa/G.py:1: backlog-id" in err,
          (code, out, err))
    with _patched(_pinned_lines=_no_pins):
        code, out, err = _write_exit(repos["aa/G.py"])
    check("flip/write-deletion-credits-twice", code == 0 and "GROWTH aa/G.py" not in err, (code, err))

    # Dropping the entry in the same change unpins the line: moved to another file, it is credited.
    repo, base = _baseline_repo(tmp, "baseline-pin-dropped")
    _write(repo, "zz/F.py", "a = 1\n" + la + "\n")
    _put_baseline(repo, base, {("zz/F.py", la_raw, "backlog-id"): 1})
    _commit_all(repo, "a baselined line")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "zz/F.py", "a = 1\n")
    _write(repo, "aa/G.py", la + "\n")
    _put_baseline(repo, base, {})
    _commit_all(repo, "the line moved to another file, its entry dropped")
    code, out = _gate(repo)
    check("pass/baseline-dropped-entry-unpins-move", code == 0 and "1 moved line(s) not charged" in out
          and "RELEASED zz/F.py: backlog-id" in out, out)
    with _patched(_move_credits=_all_pinned_credits):
        code, out = _gate(repo)
    check("flip/baseline-every-deletion-pinned", code == 1 and "  aa/G.py:1: backlog-id" in out, out)


def _ascii_only_escaped(text):
    """A flip of _escaped: only non-ASCII characters are escaped, control bytes pass raw."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _baseline_listing_rows(tmp, labels):
    """A carriage return and an erase-line escape in a listed line are escaped in the gate's listing and
    in --write-baseline's, never printed raw."""
    la = labels["backlog-id"]
    hidden = la.encode("utf-8") + b"\r\x1b[2Kvalue = 1"
    shown = la + "\\x0d\\x1b[2Kvalue = 1"
    repo, base = _baseline_repo(tmp, "baseline-listing")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write_bytes(repo, "c.py", hidden + b"\n")
    _put_baseline(repo, base, {("c.py", hidden, "backlog-id"): 1})
    _commit_all(repo, "a line holding a carriage return and an erase-line escape, with its entry")
    code, out = _gate(repo)
    _wcode, _wout, err = _write_exit(repo)
    check("review/listing-escapes-control-bytes", code == 1 and "  GROWTH c.py:1: backlog-id" in out
          and shown in out and "\r" not in out and "\x1b" not in out, out)
    check("review/write-listing-escapes-control-bytes", "  GROWTH c.py:1: backlog-id" in err and shown in err
          and "\r" not in err and "\x1b" not in err, err)
    with _patched(_escaped=_ascii_only_escaped):
        code, out = _gate(repo)
        _wcode, _wout, err = _write_exit(repo)
    check("flip/listing-control-bytes-raw", "\x1b" in out and "\x1b" in err, (out, err))


SHAPE_BLOCK_RE = r'(?ms)^\[\[shape\]\]\nid = "{}"\n.*?(?=^\[\[shape\]\]|\Z)'


def _config_without_shape(shape_id):
    """The shipped configuration text with the [[shape]] table of `shape_id` removed."""
    text, count = re.subn(SHAPE_BLOCK_RE.format(re.escape(shape_id)), "", _config_text())
    if count != 1:
        raise RuntimeError("the shipped configuration has no single shape {}".format(shape_id))
    return text


def _baseline_config_change_rows(tmp, labels):
    """The merge base's baseline is checked for structure only: a change that retires a shape, or
    excludes a baselined path, while dropping those entries is judged, not refused."""
    la = labels["backlog-id"]
    la_raw = la.encode("utf-8")
    excluded = sorted(_REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH).excluded)
    for check_id, flip_id, config_text, needle in (
            ("pass/baseline-shape-retired-with-its-entry", "flip/baseline-merge-base-held-to-retired-shape",
             _config_without_shape("backlog-id"), "names no shape"),
            ("pass/baseline-path-excluded-with-its-entry", "flip/baseline-merge-base-held-to-new-exclusion",
             _config_with_excluded(excluded + ["a.py"]), "excluded file or the baseline itself")):
        repo, base = _baseline_repo(tmp, "baseline-" + check_id.split("/")[1])
        _write(repo, "a.py", la + "\n")
        _put_baseline(repo, base, {("a.py", la_raw, "backlog-id"): 1})
        main = _commit_all(repo, "a baselined line")
        _fx_git(repo, "checkout", "-q", "-b", "work")
        _write(repo, CONFIG_RELPATH, config_text)
        _put_baseline(repo, main, {})
        _commit_all(repo, "the configuration changed and the entry dropped")
        code, out = _gate(repo)
        check(check_id, code == 0 and "RELEASED a.py: backlog-id" in out, out)
        with _patched(_merge_base_config=lambda config: config):
            code, out = _gate(repo)
        check(flip_id, code == 2 and needle in out, out)


def _ls_tree_link_as_regular(root, *args):
    """A flip of the committed baseline's mode check: git ls-tree reports a symlink blob as mode 100644."""
    proc = _REAL_GIT(root, *args)
    if args[:1] == ("ls-tree",):
        proc = subprocess.CompletedProcess(proc.args, proc.returncode,
                                           proc.stdout.replace(b"120000 blob ", b"100644 blob "), proc.stderr)
    return proc


def _small_blob_size(root, *args):
    """A flip of the committed baseline's size pre-check: git cat-file -s reports every blob as 1 byte."""
    proc = _REAL_GIT(root, *args)
    if args[:2] == ("cat-file", "-s"):
        proc = subprocess.CompletedProcess(proc.args, proc.returncode, b"1\n", proc.stderr)
    return proc


def _baseline_merge_base_read_rows(tmp):
    """The merge base's committed baseline: a symlink blob is refused even when its bytes are a canonical
    baseline, and an oversized blob is refused from its size before its bytes are read."""
    repo, base = _baseline_repo(tmp, "baseline-merge-base-link-canonical")
    blob = _fx_git(repo, "hash-object", "-w", "--stdin", stdin_text=render_baseline(base, {}).decode("ascii"))
    _fx_git(repo, "update-index", "--add", "--cacheinfo", "120000,{},{}".format(blob, BASELINE_RELPATH))
    _fx_git(repo, "commit", "-q", "-m", "a symlink blob whose bytes are a canonical baseline")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _fx_git(repo, "rm", "-q", "--cached", BASELINE_RELPATH)
    _write(repo, "clean.py", "value = 1\n")
    _commit_all(repo, "baseline removed, a clean line added")
    code, out = _gate(repo)
    check("fail-closed/baseline-merge-base-link-canonical", code == 2 and "mode 120000" in out, out)
    with _patched(_git=_ls_tree_link_as_regular):
        code, out = _gate(repo)
    check("flip/baseline-merge-base-link-mode-unchecked", code == 0, out)

    repo, base = _baseline_repo(tmp, "baseline-merge-base-oversized")
    _write_bytes(repo, BASELINE_RELPATH, render_baseline(base, {}) + b"#" * MAX_BASELINE_BYTES)
    _commit_all(repo, "an oversized baseline on main")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _fx_git(repo, "rm", "-q", BASELINE_RELPATH)
    _write(repo, "clean.py", "value = 1\n")
    _commit_all(repo, "baseline removed, a clean line added")
    for check_id, flip, expect_read in (
            ("fail-closed/baseline-merge-base-oversized-unread", None, False),
            ("flip/baseline-merge-base-size-unchecked", _small_blob_size, True)):
        calls = []
        inner = _REAL_GIT if flip is None else flip

        def recording(root, *args):
            calls.append(args)
            return inner(root, *args)
        with _patched(_git=recording):
            code, out = _gate(repo)
        read = any(args[:2] == ("cat-file", "blob") for args in calls)
        check(check_id, code == 2 and "larger than" in out and read == expect_read, (read, out))


def _existing_rows_with_empty_baseline(tmp, existing):
    """The section 8 accounting: every row that predates the baseline, run again with an empty canonical
    baseline committed on main in each fixture, gives the same result as without one."""
    results = {}

    def collect(name, ok, detail=""):
        results.setdefault(name, []).append((ok, detail))
    sub = Path(tmp) / "existing-with-empty-baseline"
    os.makedirs(sub)
    with _patched(check=collect, _FIXTURE_EMPTY_BASELINE=True):
        _existing_rows(sub)
    committed = _fx_git(sub / "refused", "ls-tree", "--name-only", "main", "--", BASELINE_RELPATH)
    failed = sorted(name for name, runs in results.items() if not all(ok for ok, _detail in runs))
    check("pass/existing-rows-with-empty-baseline", committed == BASELINE_RELPATH and not failed
          and sorted(results) == sorted(existing), (committed, failed, [results[name] for name in failed],
                                                    sorted(set(existing) ^ set(results))))


def _fail_closed_rows(tmp, labels):
    repo = _base_repo(tmp, "fail-closed")
    base_sha = _fx_git(repo, "rev-parse", "HEAD")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "clean.py", "value = 1\n")
    _commit_all(repo, "clean change")

    # No protected branch can be derived: no --protected, no environment, no origin/HEAD.
    code, out = _gate(repo, protected=None)
    check("fail-closed/no-protected-branch", code == 2 and "no protected branch" in out, out)

    def head_fallback(root, explicit, environ):
        try:
            return _REAL_PROTECTED_REF(root, explicit, environ)
        except CannotEvaluate:
            return "HEAD", "flip"
    with _patched(protected_ref=head_fallback):
        code, out = _gate(repo, protected=None)
    check("flip/no-protected-branch-falls-back", code == 0, out)

    # A protected ref that does not resolve (a pull request target with no origin tracking branch).
    code, out = _gate(repo, protected=None, environ={BASE_REF_VAR: "absent"})
    check("fail-closed/unresolved-protected-ref", code == 2 and "does not resolve" in out, out)

    def resolve_fallback(root, revision, what):
        try:
            return _REAL_RESOLVE_COMMIT(root, revision, what)
        except CannotEvaluate:
            return _REAL_RESOLVE_COMMIT(root, "HEAD", what)
    with _patched(_resolve_commit=resolve_fallback):
        code, out = _gate(repo, protected=None, environ={BASE_REF_VAR: "absent"})
    check("flip/unresolved-protected-ref-falls-back", code == 0, out)

    # No common ancestor: the protected branch is an orphan.
    _fx_git(repo, "checkout", "-q", "--orphan", "orphan")
    _fx_git(repo, "rm", "-q", "-r", "--cached", ".")
    _commit_all(repo, "orphan root")
    _fx_git(repo, "checkout", "-q", "-f", "work")
    code, out = _gate(repo, protected="orphan")
    check("fail-closed/no-common-ancestor", code == 2 and "no common ancestor" in out, out)
    with _patched(_one_base=lambda bases, head_sha: bases[0] if bases else head_sha):
        code, out = _gate(repo, protected="orphan")
    check("flip/no-common-ancestor-reads-empty", code == 0, out)

    # Two merge bases (a criss-cross merge).
    _fx_git(repo, "checkout", "-q", "-b", "cross-p", base_sha)
    p1 = _commit_all(repo, "p1")
    _fx_git(repo, "checkout", "-q", "-b", "cross-h", base_sha)
    h1 = _commit_all(repo, "h1")
    _fx_git(repo, "checkout", "-q", "cross-p")
    _fx_git(repo, "merge", "-q", "--no-ff", "--no-edit", h1)
    _fx_git(repo, "checkout", "-q", "cross-h")
    _fx_git(repo, "merge", "-q", "--no-ff", "--no-edit", p1)
    code, out = _gate(repo, protected="cross-p")
    check("fail-closed/two-merge-bases", code == 2 and "2 merge bases" in out, out)
    with _patched(_one_base=lambda bases, head_sha: bases[0] if bases else head_sha):
        code, out = _gate(repo, protected="cross-p")
    check("flip/two-merge-bases-takes-one", code == 0, out)
    _fx_git(repo, "checkout", "-q", "work")

    # A tracked file the working-tree diff cannot read.
    _write(repo, "clean.py", "value = 2\n")
    target = Path(repo) / "clean.py"
    os.chmod(target, 0)
    try:
        binds = True
        try:
            with open(target, "rb"):
                binds = False
        except PermissionError:
            pass
        if binds:
            code, out = _gate(repo)
            check("fail-closed/unreadable-file", code == 2 and "git diff exited" in out, out)
            with _patched(_require_diff_ok=lambda proc: None):
                code, out = _gate(repo)
            check("flip/unreadable-file-ignored", code != 2, out)
        else:
            NOT_RUN.append("fail-closed/unreadable-file and its flip (permissions do not bind here)")
    finally:
        os.chmod(target, 0o644)
    _fx_git(repo, "checkout", "-q", "--", "clean.py")

    # A shallow repository.
    shallow = Path(tmp) / "shallow"
    _fx_git(tmp, "clone", "-q", "--depth", "1", "--branch", "work", Path(repo).as_uri(), str(shallow))
    code, out = _gate(shallow, protected="HEAD")
    check("fail-closed/shallow-repository", code == 2 and "shallow" in out, out)
    with _patched(_require_full_history=lambda root: None):
        code, out = _gate(shallow, protected="HEAD")
    check("flip/shallow-repository-accepted", code == 0, out)

    # Git that cannot run at all.
    with _patched(GIT="git-review-labels-selftest-absent"):
        code, out = _gate(repo)
    check("fail-closed/git-cannot-run", code == 2 and "could not run" in out, out)

    # The configuration: absent, not a regular file, and each malformed shape; the flip reads a refused
    # configuration as one of no shapes, which lets the label line through.
    _write(repo, "labelled.py", labels["backlog-id"] + "\n")
    _commit_all(repo, "a label")
    good = _config_text()
    config_path = Path(repo) / CONFIG_RELPATH
    for check_id, flip_id, body, needle in (
            ("fail-closed/config-absent", "flip/config-absent-read-as-empty", None, "cannot be read"),
            ("fail-closed/config-not-regular", "flip/config-not-regular-read-as-empty", "FIFO",
             "not a regular file"),
            ("fail-closed/config-not-utf8", "flip/config-not-utf8-read-as-empty", b"\xff\n", "not UTF-8"),
            ("fail-closed/config-not-toml", "flip/config-not-toml-read-as-empty", "[[shape\n",
             "cannot be parsed"),
            ("fail-closed/config-unknown-key", "flip/config-unknown-key-read-as-empty",
             "extra = 1\n", "unknown key"),
            ("fail-closed/config-bad-regex", "flip/config-bad-regex-read-as-empty",
             "SHAPE:(unclosed", "does not compile"),
            ("fail-closed/config-empty-match", "flip/config-empty-match-read-as-empty",
             "SHAPE:x?", "matches an empty line"),
            ("fail-closed/config-no-shapes", "flip/config-no-shapes-read-as-empty",
             "NOSHAPES", "at least one"),
            ("fail-closed/config-wrong-version", "flip/config-wrong-version-read-as-empty",
             "VERSION", "format-version")):
        # A FIFO left by the row before is removed first: writing to it would block.
        if os.path.lexists(config_path):
            os.unlink(config_path)
        if body is None:
            pass
        elif body == "FIFO":
            os.mkfifo(config_path)
        elif isinstance(body, bytes):
            with open(config_path, "wb") as handle:
                handle.write(body)
        elif body.startswith("SHAPE:"):
            _write(repo, CONFIG_RELPATH, good + "\n[[shape]]\nid = \"extra\"\ndescription = \"d\"\n"
                   "regex = '{}'\n".format(body[len("SHAPE:"):]))
        elif body == "NOSHAPES":
            _write(repo, CONFIG_RELPATH, "format-version = 1\nallow-marker = \"review-label-allow\"\n")
        elif body == "VERSION":
            _write(repo, CONFIG_RELPATH, good.replace("format-version = 1", "format-version = 2"))
        else:
            _write(repo, CONFIG_RELPATH, good + body)
        # The committed tree is judged here, so git never reads the configuration path itself (git
        # refuses a FIFO in the working tree on its own, which would hide the loader's refusal).
        code, out = _gate(repo, head="HEAD")
        check(check_id, code == 2 and needle in out, out)
        with _patched(load_config=_fail_open_loader):
            code, out = _gate(repo, head="HEAD")
        check(flip_id, code == 0, out)
    if os.path.lexists(config_path):
        os.unlink(config_path)
    _write(repo, CONFIG_RELPATH, good)
    code, out = _gate(repo)
    check("refuse/config-restored-finds-label", code == 1 and "  labelled.py:1: backlog-id" in out, out)

    # Diff text the parser does not recognise, and a hunk cut short.
    odd = b"diff --git a/x b/x\nsomething odd\n"
    short = b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -0,0 +1,2 @@\n+one\n"
    for check_id, data in (("fail-closed/diff-line-unrecognised", odd), ("fail-closed/diff-hunk-cut-short",
                                                                           short)):
        try:
            added_lines(data)
            got = "accepted"
        except CannotEvaluate:
            got = "refused"
        check(check_id, got == "refused", got)
    with _patched(HEADER_PREFIXES=HEADER_PREFIXES + (b"",)):
        try:
            added_lines(odd)
            got = "accepted"
        except CannotEvaluate:
            got = "refused"
    check("flip/diff-line-unrecognised-accepted", got == "accepted", got)


_REAL_PROTECTED_REF = protected_ref
_REAL_RESOLVE_COMMIT = _resolve_commit


def _derivation_rows(tmp, labels):
    """The protected branch is derived from the pull request target, origin/HEAD and the Actions ref
    name; each row judges a label line added after the base, so a pass is never a missed derivation,
    and each flip removes that one source."""
    repo = _base_repo(tmp, "derive")
    base_sha = _fx_git(repo, "rev-parse", "HEAD")
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "main", base_sha)
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "labelled.py", labels["backlog-id"] + "\n")
    _commit_all(repo, "a label")
    finding = "  labelled.py:1: backlog-id"

    target = {BASE_REF_VAR: "main"}
    code, out = _gate(repo, protected=None, environ=target)
    check("derive/pull-request-target", code == 1 and finding in out and "(from GITHUB_BASE_REF)" in out,
          out)
    with _patched(BASE_REF_VAR="REVIEW_LABELS_SELFTEST_UNSET"):
        code, out = _gate(repo, protected=None, environ=target)
    check("flip/pull-request-target-ignored", code == 2, out)

    actions = {ACTIONS_VAR: "true", REF_NAME_VAR: "main"}
    code, out = _gate(repo, protected=None, environ=actions)
    check("derive/actions-ref-name", code == 1 and finding in out and "(from GITHUB_REF_NAME)" in out, out)
    code, out = _gate(repo, protected=None, environ={REF_NAME_VAR: "main"})
    check("derive/ref-name-needs-actions", code == 2 and "no protected branch" in out, out)
    with _patched(REF_NAME_VAR="REVIEW_LABELS_SELFTEST_UNSET"):
        code, out = _gate(repo, protected=None, environ=actions)
    check("flip/actions-ref-name-ignored", code == 2, out)

    _fx_git(repo, "symbolic-ref", ORIGIN_HEAD, ORIGIN_PREFIX + "main")
    code, out = _gate(repo, protected=None)
    check("derive/origin-head", code == 1 and finding in out and "(from origin/HEAD)" in out, out)
    with _patched(ORIGIN_HEAD=ORIGIN_PREFIX + "review-labels-selftest-unset"):
        code, out = _gate(repo, protected=None)
    check("flip/origin-head-ignored", code == 2, out)
    _fx_git(repo, "symbolic-ref", ORIGIN_HEAD, "refs/heads/main")
    code, out = _gate(repo, protected=None)
    check("fail-closed/origin-head-not-tracking", code == 2 and "not an origin tracking branch" in out, out)

    # origin/HEAD naming another branch (here a feature branch at the candidate, which would judge
    # nothing) is set aside for origin/main, which resolves here; the flip trusts the branch origin/HEAD
    # names, which hides the label line.
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "feature", _fx_git(repo, "rev-parse", "work"))
    _fx_git(repo, "symbolic-ref", ORIGIN_HEAD, ORIGIN_PREFIX + "feature")
    code, out = _gate(repo, protected=None)
    check("derive/origin-head-feature-branch-set-aside", code == 1 and finding in out
          and "which names {} and is set aside".format(ORIGIN_PREFIX + "feature") in out, out)
    with _patched(origin_head_branch=_trusting_origin_head_branch):
        code, out = _gate(repo, protected=None)
    check("flip/origin-head-feature-branch-trusted", code == 0 and "0 added line(s)" in out, out)
    code, out = _gate(repo, protected=_fx_git(repo, "rev-parse", "work"))
    check("pass/explicit-protected-at-candidate", code == 0 and "0 added line(s)" in out, out)
    # With no origin/main an origin/HEAD naming another branch cannot be evaluated, whatever that branch
    # is called: a branch named trunk here sits at the candidate, as a feature branch would in a clone of
    # a feature checkout of a repository without main.
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "trunk", _fx_git(repo, "rev-parse", "work"))
    _fx_git(repo, "symbolic-ref", ORIGIN_HEAD, ORIGIN_PREFIX + "trunk")
    _fx_git(repo, "update-ref", "-d", ORIGIN_PREFIX + "main")
    code, out = _gate(repo, protected=None)
    check("fail-closed/origin-head-other-branch-without-main", code == 2 and "does not resolve" in out
          and "pass --protected" in out, out)
    with _patched(origin_head_branch=_trusting_origin_head_branch):
        code, out = _gate(repo, protected=None)
    check("flip/origin-head-other-branch-without-main-trusted", code == 0 and "0 added line(s)" in out, out)

    # GITHUB_BASE_REF comes before an existing origin/HEAD: here origin/HEAD names origin/main at the
    # candidate (judging nothing) while the pull request target sits at the base, so only the target
    # finds the label; the flip reads origin/HEAD first whenever it exists.
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "main", _fx_git(repo, "rev-parse", "work"))
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "target", base_sha)
    _fx_git(repo, "symbolic-ref", ORIGIN_HEAD, ORIGIN_PREFIX + "main")
    target = {BASE_REF_VAR: "target"}
    code, out = _gate(repo, protected=None, environ=target)
    check("derive/pull-request-target-before-origin-head", code == 1 and finding in out
          and "(from GITHUB_BASE_REF)" in out, out)
    with _patched(protected_ref=_origin_head_first_protected_ref):
        code, out = _gate(repo, protected=None, environ=target)
    check("flip/origin-head-before-pull-request-target", code == 0 and "(from origin/HEAD)" in out, out)


def _origin_head_first_protected_ref(root, explicit, environ):
    """A flip of the derivation order: an existing origin/HEAD is read before GITHUB_BASE_REF."""
    if explicit is None and _git(root, "symbolic-ref", "--quiet", ORIGIN_HEAD).returncode == 0:
        return _REAL_PROTECTED_REF(root, explicit, {})
    return _REAL_PROTECTED_REF(root, explicit, environ)


def _trusting_origin_head_branch(root, target):
    """A flip of the origin/HEAD derivation: the branch origin/HEAD names is trusted as the protected
    branch, whatever it is (the earlier derivation's acceptance of a remote default other than main)."""
    return target, ORIGIN_HEAD_SOURCE


def _position_rows(tmp, labels):
    """A candidate at or behind origin/main (named by origin/HEAD) has no committed line of its own: a
    clean main, a clone of main and a branch behind main pass with a NOTE, and an uncommitted label is
    judged against the merge base. A clone of a checkout on a feature branch (origin/HEAD and the
    remote's HEAD on that branch) is judged against origin/main at the branch tip, one local commit past
    it and several past it; each flip trusts the branch origin/HEAD names, which hides the branch's label
    line."""
    repo = _base_repo(tmp, "position")
    tip = _fx_git(repo, "rev-parse", "HEAD")
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "main", tip)
    _fx_git(repo, "symbolic-ref", ORIGIN_HEAD, ORIGIN_PREFIX + "main")
    nothing = "there is nothing to judge"

    # A clean main at origin/main: nothing to judge, a pass with a NOTE.
    code, out = _gate(repo, protected=None)
    check("pass/clean-main-at-tip", code == 0 and "NOTE: " in out and "at the tip of" in out
          and nothing in out and "(from origin/HEAD)" in out, out)

    # An uncommitted label on main is judged against the tip.
    _write(repo, "seed.py", "a = 1\nb = 2\n{}\n{}\n".format(labels["round-number"], labels["backlog-id"]))
    code, out = _gate(repo, protected=None)
    check("refuse/main-uncommitted-label-judged-against-tip", code == 1 and "  seed.py:4: backlog-id" in out
          and out.count("  seed.py:") == 1 and "merge base {}".format(tip[:12]) in out
          and "NOTE: " in out and nothing not in out, out)
    _fx_git(repo, "checkout", "-q", "--", "seed.py")

    # A branch behind main: origin/main moves on, the branch stays at the old tip.
    _fx_git(repo, "branch", "behind")
    _write(repo, "ahead.py", "value = 1\n")
    new_tip = _commit_all(repo, "main moves ahead")
    _fx_git(repo, "update-ref", ORIGIN_PREFIX + "main", new_tip)
    _fx_git(repo, "checkout", "-q", "behind")
    code, out = _gate(repo, protected=None)
    check("pass/branch-behind-main", code == 0 and "is behind" in out and nothing in out, out)
    _write(repo, "seed.py", "a = 1\nb = 2\n{}\n{}\n".format(labels["round-number"], labels["backlog-id"]))
    code, out = _gate(repo, protected=None)
    check("refuse/branch-behind-main-uncommitted-label", code == 1 and "  seed.py:4: backlog-id" in out
          and "merge base {}".format(tip[:12]) in out, out)
    _fx_git(repo, "checkout", "-q", "--", "seed.py")
    _fx_git(repo, "checkout", "-q", "main")

    # A clone of main: git clone sets origin/HEAD to origin/main, which is the candidate itself.
    clone = Path(tmp) / "position-clone"
    _fx_git(tmp, "clone", "-q", "--no-hardlinks", str(repo), str(clone))
    named = _fx_git(clone, "symbolic-ref", ORIGIN_HEAD)
    at = _fx_git(clone, "rev-parse", "HEAD")
    code, out = _gate(clone, protected=None)
    check("pass/clone-of-main-origin-head-is-candidate", code == 0 and named == ORIGIN_PREFIX + "main"
          and at == new_tip and "at the tip of" in out and nothing in out, (named, at, out))

    # A clone of a checkout on a feature branch (a linked worktree, say) whose commit adds a label: the
    # clone's origin/HEAD and the remote's own HEAD name that branch. origin/HEAD is set aside for
    # origin/main, so the branch's label is judged with the candidate at the branch tip, one clean local
    # commit past it (the fix-round workflow) and several past it.
    source = _base_repo(tmp, "position-feature-source")
    _fx_git(source, "checkout", "-q", "-b", "feature")
    _write(source, "labelled.py", labels["backlog-id"] + "\n")
    _commit_all(source, "a label on the feature branch")
    feature = Path(tmp) / "position-feature-clone"
    _fx_git(tmp, "clone", "-q", "--no-hardlinks", str(source), str(feature))
    named = _fx_git(feature, "symbolic-ref", ORIGIN_HEAD)
    remote_head = _fx_git(feature, "ls-remote", "--symref", "origin", "HEAD")
    set_aside = "which names {} and is set aside".format(ORIGIN_PREFIX + "feature")
    past = 0
    for check_id, flip_id, commits in (
            ("refuse/clone-of-feature-checkout-at-tip",
             "flip/clone-of-feature-checkout-at-tip-label-hidden-when-trusted", 0),
            ("refuse/clone-of-feature-checkout-one-commit-past",
             "flip/clone-of-feature-checkout-one-commit-past-label-hidden-when-trusted", 1),
            ("refuse/clone-of-feature-checkout-several-commits-past",
             "flip/clone-of-feature-checkout-several-commits-past-label-hidden-when-trusted", 3)):
        while past < commits:
            past += 1
            _write(feature, "local{}.py".format(past), "value = {}\n".format(past))
            _commit_all(feature, "a clean local commit")
        code, out = _gate(feature, protected=None)
        check(check_id, code == 1 and named == ORIGIN_PREFIX + "feature"
              and "ref: refs/heads/feature\tHEAD" in remote_head and "  labelled.py:1: backlog-id" in out
              and out.count(": backlog-id (") == 1 and set_aside in out
              and "{} added line(s)".format(1 + past) in out, (named, remote_head, out))
        with _patched(origin_head_branch=_trusting_origin_head_branch):
            code, out = _gate(feature, protected=None)
        check(flip_id, code == 0 and "labelled.py" not in out, out)
    code, out = _gate(feature, protected="origin/main")
    check("refuse/clone-of-feature-checkout-judged-with-protected", code == 1
          and "  labelled.py:1: backlog-id" in out, out)


EXCLUDE_BLOCK_RE = re.compile(r"(?ms)^exclude-paths = \[.*?^\]\n")


def _config_with_excluded(entries):
    """The shipped configuration text with its exclude-paths array replaced by `entries`."""
    block = "exclude-paths = [{}]\n".format(", ".join('"{}"'.format(entry) for entry in entries))
    text, count = EXCLUDE_BLOCK_RE.subn(lambda match: block, _config_text())
    if count != 1:
        raise RuntimeError("the shipped configuration has no single exclude-paths array")
    return text


def _exclusion_rows(tmp, labels):
    """A label added to each excluded file is not judged; the same label in any other file is; every
    exclude-paths entry must name a normalized regular file."""
    repo = _base_repo(tmp, "excluded")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    excluded = sorted(_REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH).excluded)
    for entry in excluded:
        _write(repo, entry, "placeholder = 1\n" + labels["review-cycle-words"] + "\n")
    _commit_all(repo, "labels in the excluded files")
    code, out = _gate(repo)
    check("pass/excluded-paths-not-judged", code == 0 and bool(excluded)
          and "{} line(s) in excluded file(s) not judged".format(len(excluded)) in out, out)
    with _patched(_excluded=lambda path, excluded: False):
        code, out = _gate(repo)
    check("flip/excluded-paths-judged", code == 1 and all(
        "  {}:2: review-cycle-words".format(entry) in out for entry in excluded), out)
    _write(repo, "other.py", labels["review-cycle-words"] + "\n")
    _commit_all(repo, "the same label in another file")
    code, out = _gate(repo)
    check("refuse/label-outside-excluded-paths", code == 1 and "  other.py:1: review-cycle-words" in out
          and out.count(": review-cycle-words (") == 1, out)

    os.makedirs(Path(repo) / "adir")
    _write(repo, "adir/keep.txt", "keep = 1\n")
    os.symlink("seed.py", Path(repo) / "alink.py")
    for check_id, flip_id, entries, needle in (
            ("fail-closed/exclude-path-absent", "flip/exclude-path-absent-accepted", ["absent.py"],
             "names no file"),
            ("fail-closed/exclude-path-directory", "flip/exclude-path-directory-accepted", ["adir"],
             "names a directory"),
            ("fail-closed/exclude-path-symlink", "flip/exclude-path-symlink-accepted", ["alink.py"],
             "not a regular file")):
        _write(repo, CONFIG_RELPATH, _config_with_excluded(entries))
        code, out = _gate(repo, head="HEAD")
        check(check_id, code == 2 and needle in out, out)
        with _patched(require_excluded_files=lambda root, excluded: None):
            code, out = _gate(repo, head="HEAD")
        check(flip_id, code == 1, out)
    _write(repo, CONFIG_RELPATH, _config_with_excluded(["../excluded/other.py"]))
    code, out = _gate(repo, head="HEAD")
    check("fail-closed/exclude-path-not-normalized", code == 2 and "not a normalized relative path" in out,
          out)
    with _patched(_parse_excluded=lambda entries, where: frozenset(entries)):
        code, out = _gate(repo, head="HEAD")
    check("flip/exclude-path-not-normalized-accepted", code == 1, out)


def _move_rows(tmp, labels):
    """A line moved to another file or reordered in its own is not charged; an edited label line and a
    copy beyond the deleted ones are."""
    repo = _base_repo(tmp, "moves")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "seed.py", "a = 1\nb = 2\n")
    _write(repo, "target.py", "first = 1\n{}\n".format(labels["round-number"]))
    _write(repo, "moved.py", "value = 3\n{}\n".format(labels["backlog-id"]))
    _write(repo, "old.py", "keep = 1\n{}\nkeep2 = 2\n".format(labels["finding-number"].replace("7", "8")))
    _commit_all(repo, "move, reorder and edit")
    code, out = _gate(repo)
    check("pass/line-moved-across-files", code == 1 and "target.py" not in out, out)
    check("pass/line-reordered", code == 1 and "moved.py" not in out, out)
    check("refuse/edited-label-line-charged", code == 1 and "  old.py:2: finding-number" in out
          and "2 moved line(s) not charged" in out, out)
    with _patched(credit_moves=lambda added, deleted: (added, 0)):
        code, out = _gate(repo)
    check("flip/moved-lines-charged-without-credit", code == 1 and "  target.py:2: round-number" in out
          and "  moved.py:2: backlog-id" in out, out)

    _fx_git(repo, "checkout", "-q", "-b", "copies", "main")
    _write(repo, "seed.py", "a = 1\nb = 2\n")
    _write(repo, "copy.py", "{0}\n{0}\n".format(labels["round-number"]))
    _commit_all(repo, "one line deleted, two copies added")
    code, out = _gate(repo)
    check("refuse/copy-beyond-deleted-charged", code == 1 and out.count("  copy.py:") == 1
          and "  copy.py:2: round-number" in out, out)

    def set_credit(added, deleted):
        return [line for line in added if not deleted[line.raw]], 0
    with _patched(credit_moves=set_credit):
        code, out = _gate(repo)
    check("flip/copies-credited-as-a-set", code == 0, out)

    # A label line deleted from an excluded file credits nothing: the same line added elsewhere is charged.
    excluded = sorted(_REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH).excluded)[0]
    _fx_git(repo, "checkout", "-q", "-b", "excluded-credit", "main")
    _write(repo, excluded, "placeholder = 1\n{}\n".format(labels["review-cycle-words"]))
    protected = _commit_all(repo, "a label line in an excluded file")
    _write(repo, excluded, "placeholder = 1\n")
    _write(repo, "taken.py", labels["review-cycle-words"] + "\n")
    _commit_all(repo, "the line deleted there and added in another file")
    code, out = _gate(repo, protected=protected)
    check("refuse/excluded-file-deletion-credits-nothing",
          code == 1 and "  taken.py:1: review-cycle-words" in out and "moved line" not in out, out)

    def all_credits(deleted, excluded):
        credits = Counter()
        for (_path, raw), count in deleted.items():
            credits[raw] += count
        return credits
    with _patched(_credits=all_credits):
        code, out = _gate(repo, protected=protected)
    check("flip/excluded-file-deletion-credited", code == 0 and "1 moved line(s) not charged" in out, out)


def _text_rows(tmp, labels):
    """A text file declared binary and a genuine binary are read as lines; a binary notice is refused;
    look-alike characters and the widened shape variants are matched."""
    repo = _base_repo(tmp, "text")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, ".gitattributes", "*.md -diff\n*.txt binary\n")
    _write(repo, "notes.md", labels["backlog-id"] + "\n")
    _write(repo, "notes.txt", labels["review-test-name"] + "\n")
    with open(Path(repo) / "blob.dat", "wb") as handle:
        handle.write(b"\x00\x01head\n" + labels["round-number"].encode("ascii") + b"\x00tail\n")
    _commit_all(repo, "text declared binary and a genuine binary")
    code, out = _gate(repo)
    check("refuse/text-declared-binary-read", code == 1 and "  notes.md:1: backlog-id" in out
          and "  notes.txt:1: review-test-name" in out, out)
    check("refuse/nul-file-read-as-lines", code == 1 and "  blob.dat:2: round-number" in out
          and "1 file(s) with a NUL byte read as raw lines" in out, out)
    with _patched(DIFF_OPTIONS=tuple(option for option in DIFF_OPTIONS if option != "--text")):
        code, out = _gate(repo)
    check("flip/binary-unread-without-text-option", code == 2 and "binary file" in out, out)

    notice = b"diff --git a/x b/x\nindex 1111111..2222222 100644\nBinary files a/x and b/x differ\n"
    try:
        added_lines(notice)
        got = "accepted"
    except CannotEvaluate:
        got = "refused"
    check("fail-closed/binary-notice", got == "refused", got)
    with _patched(BINARY_NOTICE=b"\x00never", HEADER_PREFIXES=HEADER_PREFIXES + (b"Binary files ",)):
        try:
            added_lines(notice)
            got = "accepted"
        except CannotEvaluate:
            got = "refused"
    check("flip/binary-notice-accepted", got == "accepted", got)

    repo = _base_repo(tmp, "variants")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    rd = "rou" + "nd"
    variants = (
        ("refuse/test-name-after-underscore", "_test_" + "r4_guard = 1", "review-test-name"),
        ("refuse/test-name-upper-case", "def test_" + "R4_guard(self):", "review-test-name"),
        ("refuse/bracketed-review-tag", "x = 1  # [" + "r3 note]", "review-tag"),
        ("refuse/round-hash-number", "# fixed in " + rd + " #5", "round-number"),
        ("refuse/round-tab-number", "# fixed in " + rd + "\t5", "round-number"),
        ("refuse/fullwidth-digit", "# fixed in " + rd + " \N{FULLWIDTH DIGIT FIVE}", "round-number"),
        ("refuse/no-break-space", "# fixed in " + rd + " 5", "round-number"),
        ("refuse/zero-width-space", "# tracked as " + "FI" + "X-\N{ZERO WIDTH SPACE}12", "backlog-id"),
        ("refuse/cyrillic-o", "# fixed in r\N{CYRILLIC SMALL LETTER O}und 5", "round-number"),
        ("refuse/greek-omicron", "# fixed in r\N{GREEK SMALL LETTER OMICRON}und 5", "round-number"),
        ("refuse/cyrillic-capital-i", "# tracked as F\N{CYRILLIC CAPITAL LETTER BYELORUSSIAN-UKRAINIAN I}"
         + "X-12", "backlog-id"),
        ("refuse/combining-mark", "# fixed in ro\N{COMBINING ACUTE ACCENT}und 5", "round-number"),
        ("refuse/arabic-indic-digit", "# fixed in " + rd + " \N{ARABIC-INDIC DIGIT FIVE}", "round-number"),
        ("refuse/snake-case-number", "x_" + rd + "_5 = 1", "round-number"))
    _write(repo, "variants.py", "".join(text + "\n" for _, text, _ in variants))
    _commit_all(repo, "label variants")
    code, out = _gate(repo)
    for check_id, index, shape_id in (("refuse/test-name-after-underscore", 1, "review-test-name"),
                                      ("refuse/test-name-upper-case", 2, "review-test-name"),
                                      ("refuse/bracketed-review-tag", 3, "review-tag"),
                                      ("refuse/round-hash-number", 4, "round-number"),
                                      ("refuse/round-tab-number", 5, "round-number"),
                                      ("refuse/fullwidth-digit", 6, "round-number"),
                                      ("refuse/no-break-space", 7, "round-number"),
                                      ("refuse/zero-width-space", 8, "backlog-id"),
                                      ("refuse/cyrillic-o", 9, "round-number"),
                                      ("refuse/greek-omicron", 10, "round-number"),
                                      ("refuse/cyrillic-capital-i", 11, "backlog-id"),
                                      ("refuse/combining-mark", 12, "round-number"),
                                      ("refuse/arabic-indic-digit", 13, "round-number"),
                                      ("refuse/snake-case-number", 14, "round-number")):
        check(check_id, code == 1 and "  variants.py:{}: {} (".format(index, shape_id) in out, out)
    narrow = {
        "review-test-name": (r"\btest_r" + r"[0-9]+[a-z]?_", 0),
        "review-tag": (r"\((?:r|[Qq][Aa])" + r"[0-9]+[a-z]?:?[ ]+(?!(?:and|or|not|in|is|if|else|for)\b)"
                       r"[A-Za-z]", 0),
        "round-number": (r"(?<!retry )\bround" + r"[ -]?[0-9]+\b(?!\s+of\b)", re.IGNORECASE)}
    with _patched(load_config=_with_patterns(narrow)):
        code, out = _gate(repo)
    check("flip/variants-missed-by-narrow-shapes", not any(
        "  variants.py:{}: ".format(index) in out for index in (1, 2, 3, 4, 5, 14)), out)
    with _patched(_views=lambda text: (text,)):
        code, out = _gate(repo)
    check("flip/look-alikes-missed-without-folding", not any(
        "  variants.py:{}: ".format(index) in out for index in range(6, 14)), out)

    # A UTF-16 text file (every other byte a NUL) is also read with its NUL bytes removed, and the
    # allow-marker exempts a line only as a word of its own.
    repo = _base_repo(tmp, "encodings")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    with open(Path(repo) / "wide.txt", "wb") as handle:
        handle.write(("clean line\n" + labels["round-number"] + "\n").encode("utf-16"))
    _write(repo, "marked.py", labels["round-number"] + "  # x" + _REAL_LOAD_CONFIG(
        ROOT / CONFIG_RELPATH).marker + "ed\n")
    _commit_all(repo, "a UTF-16 label and a marker inside a longer word")
    code, out = _gate(repo)
    check("refuse/utf16-file-read-without-nul", code == 1 and "  wide.txt:2: round-number" in out
          and out.count("  wide.txt:") == 1, out)
    check("refuse/marker-inside-longer-word", code == 1 and "  marked.py:1: round-number" in out, out)
    with _patched(_line_views=lambda line: _views(line.text)):
        code, out = _gate(repo)
    check("flip/utf16-label-missed-with-nul", "wide.txt" not in out, out)
    with _patched(_allowed=lambda text, marker: marker in text):
        code, out = _gate(repo)
    check("flip/marker-inside-longer-word-exempts", "marked.py" not in out, out)
    _wide_marker_rows(tmp, labels)


def _text_only_marker_views(line, marker):
    """A flip of the per-view marker test: the marker is tested on the line's text alone (its NUL bytes
    kept), and a line carrying it there skips every view."""
    return () if _allowed(line.text, marker) else _line_views(line)


def _any_view_marker_views(line, marker):
    """A flip of the per-view marker test: a marker that is a word of its own in any view exempts the
    whole line, even when another view the shapes match joins it to a neighbour."""
    views = _line_views(line)
    return () if any(_allowed(view, marker) for view in views) else views


def _wide_marker_rows(tmp, labels):
    """The allow-marker is tested in every view the shapes read: a UTF-16 label line carrying the marker
    as a word of its own passes like its UTF-8 twin, while a UTF-16 line with the marker inside a longer
    word, a UTF-16 label with no marker, and a line whose marker is a word of its own only until its NUL
    byte is removed (then joined to the word before it) are refused."""
    marker = _REAL_LOAD_CONFIG(ROOT / CONFIG_RELPATH).marker
    repo = _base_repo(tmp, "wide-marker")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    wide = ("clean line\n" + labels["round-number"] + "  # " + marker + "\n"
            + labels["round-number"] + "  # x" + marker + "ed\n" + labels["round-number"] + "\n")
    with open(Path(repo) / "wide-marked.txt", "wb") as handle:
        handle.write(wide.encode("utf-16"))
    with open(Path(repo) / "joined.dat", "wb") as handle:
        handle.write(labels["round-number"].encode("ascii") + b"  # x\x00" + marker.encode("ascii") + b"\n")
    _commit_all(repo, "UTF-16 lines with and without the marker, and a marker joined by NUL removal")
    code, out = _gate(repo)
    check("pass/utf16-allow-marker", "wide-marked.txt:2:" not in out, out)
    check("refuse/utf16-marker-inside-longer-word", code == 1 and "  wide-marked.txt:3: round-number" in out,
          out)
    check("refuse/utf16-label-without-marker", code == 1 and "  wide-marked.txt:4: round-number" in out, out)
    check("refuse/marker-joined-by-nul-removal", code == 1 and "  joined.dat:1: round-number" in out, out)
    check("refuse/utf16-marker-findings-only", code == 1 and out.count("  wide-marked.txt:") == 2
          and out.count("  joined.dat:") == 1, out)
    with _patched(_unmarked_views=_text_only_marker_views):
        code, out = _gate(repo)
    check("flip/utf16-allow-marker-tested-on-text-only",
          code == 1 and "  wide-marked.txt:2: round-number" in out, out)
    with _patched(_unmarked_views=_any_view_marker_views):
        code, out = _gate(repo)
    check("flip/marker-joined-by-nul-removal-exempts", "joined.dat" not in out, out)
    with _patched(_allowed=lambda text, marker: marker in text):
        code, out = _gate(repo)
    check("flip/utf16-marker-inside-longer-word-exempts", "wide-marked.txt:3:" not in out, out)
    with _patched(_line_views=lambda line: _views(line.text)):
        code, out = _gate(repo)
    check("flip/utf16-label-without-marker-missed-with-nul", "wide-marked.txt:4:" not in out, out)


def _with_patterns(patterns):
    """A loader whose shapes named in `patterns` ({id: (regex, flags)}) use those regular expressions."""
    real = _REAL_LOAD_CONFIG

    def loader(path):
        config = real(path)
        return config._replace(shapes=tuple(
            s._replace(pattern=re.compile(patterns[s.id][0], re.ASCII | patterns[s.id][1]))
            if s.id in patterns else s for s in config.shapes))
    return loader


def _hardening_rows(tmp, labels):
    """The diff options and the environment scrub hold against a repository configured to hide or
    garble the diff: a textconv driver and an external diff that print nothing, a submodule log format
    the parser cannot read, and a GIT_DIR pointing elsewhere."""
    repo = _base_repo(tmp, "hardening")
    link_old = _fx_git(repo, "rev-parse", "HEAD")
    _fx_git(repo, "update-index", "--add", "--cacheinfo", "160000,{},sub".format(link_old))
    _fx_git(repo, "commit", "-q", "-m", "a submodule link")
    link_new = _fx_git(repo, "rev-parse", "HEAD")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, ".gitattributes", "*.txt diff=hide\n")
    _write(repo, "notes.txt", labels["backlog-id"] + "\n")
    _fx_git(repo, "add", ".gitattributes", "notes.txt")
    _fx_git(repo, "update-index", "--cacheinfo", "160000,{},sub".format(link_new))
    _fx_git(repo, "commit", "-q", "-m", "a label, an attribute and a moved link")
    _fx_git(repo, "config", "diff.hide.textconv", "true")
    _fx_git(repo, "config", "diff.external", "true")
    _fx_git(repo, "config", "diff.submodule", "log")
    code, out = _gate(repo, head="HEAD")
    check("refuse/hardened-diff-reads-label", code == 1 and "  notes.txt:1: backlog-id" in out, out)
    for check_id, option, expected in (("flip/textconv-hides-label", "--no-textconv", 0),
                                       ("flip/external-diff-hides-label", "--no-ext-diff", 0),
                                       ("flip/submodule-log-unparsed", "--ignore-submodules=all", 2)):
        with _patched(DIFF_OPTIONS=tuple(o for o in DIFF_OPTIONS if o != option)):
            code, out = _gate(repo, head="HEAD")
        check(check_id, code == expected, out)

    saved = os.environ.get("GIT_DIR")
    os.environ["GIT_DIR"] = str(Path(tmp) / "no-such-git-dir")
    try:
        code, out = _gate(repo, head="HEAD")
        check("refuse/git-environment-scrubbed", code == 1 and "  notes.txt:1: backlog-id" in out, out)
        with _patched(_git_env=lambda: dict(os.environ, GIT_CONFIG_NOSYSTEM="1")):
            code, out = _gate(repo, head="HEAD")
        check("flip/git-environment-kept", code == 2, out)
    finally:
        if saved is None:
            os.environ.pop("GIT_DIR", None)
        else:
            os.environ["GIT_DIR"] = saved
    _caller_config_rows(tmp, repo)


def _caller_config_rows(tmp, repo):
    """A poisoned caller home is not read: with HOME's .gitconfig naming an fsmonitor hook that logs each
    call and XDG_CONFIG_HOME's git/config malformed, the working-tree diff still reads the label and the
    hook never runs; honouring the caller's home either way is refused (the malformed file) or runs the
    hook (the fsmonitor)."""
    home, xdg, clean = Path(tmp) / "caller-home", Path(tmp) / "caller-xdg", Path(tmp) / "caller-clean"
    log, hook = Path(tmp) / "caller-fsmonitor.log", Path(tmp) / "caller-fsmonitor.sh"
    for directory in (home, xdg / "git", clean):
        os.makedirs(directory)
    with open(hook, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\necho invoked >> '{}'\nexit 1\n".format(log))
    os.chmod(hook, 0o755)
    _write(home, ".gitconfig", "[core]\n\tfsmonitor = {}\n".format(hook))
    _write(xdg / "git", "config", "[core\n")
    honoured = _git_env

    def env_with_caller_home():
        # The scrub before the pin: every GIT_ variable still dropped, the caller's HOME and
        # XDG_CONFIG_HOME kept.
        env = honoured()
        env.pop("GIT_CONFIG_GLOBAL", None)
        env.update(HOME=os.environ["HOME"], XDG_CONFIG_HOME=os.environ["XDG_CONFIG_HOME"])
        return env
    saved = {key: os.environ.get(key) for key in ("HOME", "XDG_CONFIG_HOME")}
    os.environ["HOME"], os.environ["XDG_CONFIG_HOME"] = str(home), str(xdg)
    try:
        code, out = _gate(repo)
        check("refuse/caller-home-and-fsmonitor-unused",
              code == 1 and "  notes.txt:1: backlog-id" in out and not log.exists(), out)
        with _patched(_git_env=env_with_caller_home):
            code, out = _gate(repo)
        check("flip/caller-xdg-config-honoured", code == 2, out)
        os.environ["XDG_CONFIG_HOME"] = str(clean)
        with _patched(_git_env=env_with_caller_home):
            code, out = _gate(repo)
        check("flip/caller-fsmonitor-honoured", log.exists(), out)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _replace_ref_rows(tmp, labels):
    """A replace ref (git replace --graft) that would make the candidate an ancestor of the protected
    branch is ignored, so the merge base stays the real one and the label line is judged."""
    repo = _base_repo(tmp, "replace")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    _write(repo, "labelled.py", labels["backlog-id"] + "\n")
    candidate = _commit_all(repo, "a label")
    _fx_git(repo, "checkout", "-q", "main")
    _write(repo, "clean.py", "value = 1\n")
    tip = _commit_all(repo, "a clean change on main")
    _fx_git(repo, "replace", "--graft", tip, candidate)
    _fx_git(repo, "checkout", "-q", "work")
    code, out = _gate(repo, head="HEAD")
    check("refuse/replace-ref-ignored", code == 1 and "  labelled.py:1: backlog-id" in out, out)
    honoured = _git_env

    def env_with_replace_refs():
        env = honoured()
        env.pop("GIT_NO_REPLACE_OBJECTS", None)
        return env
    with _patched(_git_env=env_with_replace_refs):
        code, out = _gate(repo, head="HEAD")
    check("flip/replace-ref-moves-merge-base", code == 0 and "labelled.py" not in out, out)


def _fake_git(returncode, stdout, calls=None):
    """A _git replacement returning one fixed result, recording each argument list in `calls`."""
    def fake(root, *args):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout, b"fixture stderr")
    return fake


def _refused(function, *args):
    """'refused' when function(*args) raises CannotEvaluate, else 'accepted' or the other error's name."""
    try:
        function(*args)
    except CannotEvaluate:
        return "refused"
    except Exception as exc:
        return "error " + type(exc).__name__
    return "accepted"


def _git_output_rows():
    """Git output off the expected shape is refused: an origin/main read that git could not complete
    while origin/HEAD names another branch; a merge-base exit 1 that still printed a base; a revision
    that resolved to several lines; an origin/HEAD read that git could not complete; and a hunk adding
    lines to /dev/null. The diff is read with core.quotePath pinned on."""
    with _patched(_git=_fake_git(128, b"")):
        try:
            origin_head_branch(".", ORIGIN_PREFIX + "feature")
            got = "accepted"
        except CannotEvaluate as exc:
            got = "refused" if "origin/main cannot be read" in str(exc) else str(exc)
    check("fail-closed/origin-main-read-error", got == "refused", got)

    with _patched(_git=_fake_git(1, b"0123abcd\n")):
        got = _refused(merge_base, ".", "a", "b")
    check("fail-closed/merge-base-exit-1-with-base", got == "refused", got)

    with _patched(_git_lines=lambda root, what, *args: ["0123abcd", "4567ef01"]):
        got = _refused(_resolve_commit, ".", "main", "protected branch")
    check("fail-closed/revision-resolves-to-several", got == "refused", got)

    with _patched(_git=_fake_git(128, b"")):
        try:
            protected_ref(".", None, {ACTIONS_VAR: "true", REF_NAME_VAR: "main"})
            got = "accepted"
        except CannotEvaluate as exc:
            got = "refused" if "origin/HEAD cannot be read" in str(exc) else str(exc)
    check("fail-closed/origin-head-read-error", got == "refused", got)

    no_target = b"diff --git a/x b/x\n--- a/x\n+++ /dev/null\n@@ -0,0 +1 @@\n+z\n"
    got = _refused(added_lines, no_target)
    check("fail-closed/hunk-without-target", got == "refused", got)

    calls = []
    with _patched(_git=_fake_git(0, b"", calls)):
        read_diff(".", "0123abcd", None)
    args = list(calls[0]) if calls else []
    check("pin/diff-quote-path-on", any(args[index:index + 2] == ["-c", "core.quotePath=true"]
                                        for index in range(len(args))), args)


def _bound_and_cli_rows():
    # The configuration size bound at its ends: one byte under, exactly at, and one byte over.
    good = _config_text().encode("utf-8")
    results = {}
    for check_id, size, refused in (("bound/config-size-under", MAX_CONFIG_BYTES - 1, False),
                                    ("bound/config-size-at", MAX_CONFIG_BYTES, False),
                                    ("bound/config-size-over", MAX_CONFIG_BYTES + 1, True)):
        body = good + b"#" * (size - len(good) - 1) + b"\n"
        try:
            parse_config(body, "fixture")
            got = False
        except CannotEvaluate:
            got = True
        results[size] = body
        check(check_id, got == refused and len(body) == size, (len(body), got))
    over = results[MAX_CONFIG_BYTES + 1]
    with _patched(MAX_CONFIG_BYTES=MAX_CONFIG_BYTES + 1):
        try:
            parse_config(over, "fixture")
            got = "accepted"
        except CannotEvaluate:
            got = "refused"
    check("flip/config-size-over-accepted", got == "accepted", got)

    # A real scan is replaced by a sentinel exit, so an argument list that falls through to a scan reads
    # as 99, never as an exit 2 that the scan of this checkout happened to produce.
    bad = (["--bogus"], ["--self-test", "--bogus"], ["--protected"],
           ["--protected", "main", "--protected", "main"], ["--head", ""],
           ["--execution-report", "relative/report"])
    with _patched(run=lambda *args, **kwargs: 99):
        got = [_main_exit(argv) for argv in bad]
        good = _main_exit(["--protected", "main", "--head", "HEAD"])
    check("cli/bad-arguments-exit-2", got == [2, 2, 2, 2, 2, 2] and good == 99, (got, good))
    with _patched(run=lambda *args, **kwargs: 99, _parse_options=_lenient_options):
        got = [_main_exit(argv) for argv in bad[3:4]]
    check("flip/duplicate-protected-accepted", got == [99], got)


def _lenient_options(argv):
    """A flip of _parse_options: a repeated option is accepted, its last value kept."""
    if len(argv) % 2:
        return None
    return {argv[index]: argv[index + 1] for index in range(0, len(argv), 2)}


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def _parse_options(argv):
    """{option: value} for the real-scan options; None when argv is not exactly that shape."""
    options = {}
    index = 0
    while index < len(argv):
        name = argv[index]
        if name not in ("--protected", "--head") or name in options or index + 1 >= len(argv):
            return None
        value = argv[index + 1]
        if not value or value.startswith("-"):
            return None
        options[name] = value
        index += 2
    return options


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
        sys.path.append(str(Path(__file__).resolve().parent))
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if argv[:1] == ["--write-baseline"]:
        options = _parse_options(argv[1:])
        if options is None:
            print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
            return 2
        return write_baseline(ROOT, options.get("--protected"), options.get("--head"))
    options = _parse_options(argv)
    if options is None:
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    return run(ROOT, options.get("--protected"), options.get("--head"))


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate starts git and reads its exit
# status (subprocess), so under an ignored SIGCHLD a failing git would read as a pass; the guard runs at
# the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("check_review_labels.py") or main())
