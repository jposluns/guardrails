#!/usr/bin/env python3
"""Changed-lines gate: refuse review and work labels that added lines write into tracked files.

  check_review_labels.py [--protected REF] [--head REV]   exit 0 clean (a NOTE line may precede the
                                                         PASS), 1 finding, 2 cannot evaluate
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
moved or reordered line and is not charged either: each deleted line credits one identical added line,
so a line copied more times than it is deleted is charged for each extra copy, and an edited label line
(any byte changed, its indentation included) is charged. Every other added line is searched for each
label shape in .aiqt/review-labels.toml (a tracked TOML file: a test name numbered after a review cycle,
a parenthesised or bracketed review or QA cycle tag, a numbered round, the words review and round side
by side, a numbered finding, a tracker backlog id (a fix-round id included), an upper-case QA round
number and a reviewer family name beside an upper-case severity grade, in either order, by default, each
one Python regular expression),
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
same line be added once anywhere else; an untracked file is not judged; a label outside the configured
shapes is not matched, among them a look-alike letter outside the CONFUSABLES table, punctuation the
shapes do not allow, a parenthesised tag with nothing after its number or with a comma after it, a
lower-case backlog id, a lower-case QA token, a lower-case severity grade, a reviewer family other than
the four the shape names, a grade written before its family with a comma, colon or semicolon between
(rule text lists them so), a grade before a family name followed by the word code (a product name), a
number written as a word, a cycle tag written after the word review (an
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
Finding = namedtuple("Finding", ("path", "number", "shape", "description", "text"))


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


def credit_moves(added, deleted):
    """(charged, moved): the added lines left after each deleted line credits one added line of exactly
    the same bytes, in diff order, and the number credited (a moved or reordered line)."""
    left, charged, moved = Counter(deleted), [], 0
    for line in added:
        if line.kind == "+" and left[line.raw] > 0:
            left[line.raw] -= 1
            moved += 1
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


def judge(added, config):
    findings = []
    for line in added:
        views = _unmarked_views(line, config.marker)
        for shape in config.shapes:
            if any(shape.pattern.search(view) for view in views):
                findings.append(Finding(line.path, line.number, shape.id, shape.description, line.text))
    return findings


def evaluate(root, protected=None, head=None, environ=None):
    """(findings, summary) for the repository at `root`; CannotEvaluate when it cannot be judged."""
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
    judged, moved = credit_moves(judged, _credits(diff.deleted, config.excluded))
    summary = "{} added line(s) in {} file(s) judged against merge base {} of {} (from {}){}{}{}{}".format(
        len(judged), len({line.path for line in judged}), base[:12], ref, source,
        "" if head is None else ", candidate {}".format(head_sha[:12]),
        "; {} moved line(s) not charged".format(moved) if moved else "",
        "; {} line(s) in excluded file(s) not judged".format(skipped) if skipped else "",
        "; {} file(s) with a NUL byte read as raw lines".format(len(diff.nul_paths))
        if diff.nul_paths else "")
    note = _position_note(ref, protected_sha, base, head_sha, head, diff.added)
    return judge(judged, config), summary, config.marker, note


def _printable(text):
    text = text.strip()
    if len(text) > MAX_EXCERPT:
        text = text[:MAX_EXCERPT] + "..."
    return text.encode("ascii", "backslashreplace").decode("ascii")


def run(root, protected=None, head=None, environ=None):
    try:
        findings, summary, marker, note = evaluate(root, protected, head, environ)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: review-label gate: {}".format(exc), file=sys.stderr)
        return 2
    if note is not None:
        print("NOTE: review-label gate: {}".format(note))
    if findings:
        print("FAIL: {} review or work label(s) on added lines ({}):".format(len(findings), summary))
        for item in findings:
            print("  {}:{}: {} ({}): {}".format(_printable(item.path), item.number, item.shape,
                                                item.description, _printable(item.text)))
        print("Say what the code guards and why, not which review or tracker item asked for it; a "
              "deliberate quotation carries the {} marker on its line.".format(marker))
        return 1
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
    _commit_all(repo, "base")
    return repo


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
          "look-alike characters are read; a fix-round id, a QA round number and a reviewer family "
          "beside a severity grade are refused and pass once their shape is removed, while a grade word "
          "alone, a finding id, quoted rule text, a lower-case grade, a family file name, a lower-case "
          "QA path and a grade before a product name pass and are refused once their narrowing is removed; "
          "textconv, an external diff, a submodule log, a GIT_DIR "
          "redirect, a replace ref and a caller's home configuration and fsmonitor are held off; "
          "git output off its shape is refused; a clean main, a "
          "clone of main and a branch behind main pass with a NOTE and an uncommitted label there is "
          "refused; a clone of a feature-branch checkout is judged against origin/main at the branch "
          "tip, one commit past it and several past it, and hides the branch's label once origin/HEAD's "
          "branch is trusted; the configuration size bound holds under, at and over; bad arguments exit "
          "2 without a scan)".format(len(EXECUTED)))
    return 0


def _rows(tmp):
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
    _grade_rows(tmp)
    _hardening_rows(tmp, labels)
    _replace_ref_rows(tmp, labels)
    _git_output_rows()
    _bound_and_cli_rows()


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


def _shipped_regex(shape_id):
    """The regular expression the shipped configuration gives the shape `shape_id`."""
    for row in tomllib.loads(_config_text())["shape"]:
        if row["id"] == shape_id:
            return row["regex"]
    raise RuntimeError("the shipped configuration has no shape {!r}".format(shape_id))


def _swapped(regex, old, new):
    """`regex` with its one occurrence of `old` replaced by `new` (a flip that drops one narrowing)."""
    if regex.count(old) != 1:
        raise RuntimeError("the shipped regex holds {!r} {} time(s), not once".format(old, regex.count(old)))
    return regex.replace(old, new)


def _grade_label_lines():
    """The fix-round, QA-round and reviewer-grade label lines (graded.py, lines 1 to 20), assembled at run
    time so that no contiguous label sits in this source (the internal-name gate's tracker layer scans
    it too). Lines 10 to 20 pin the joiners the first nine leave open: a QA number after one space, in a
    snake_case name, with a letter or a finding suffix, and a grade after a colon, a square bracket or a
    backtick, and before its family with a number and a plain space, a slash, a hyphen or a bracket."""
    return (
        "# tracked as " + "FI" + "X-123-R" + "4",
        "# closes " + "co" + "dex Q" + "A18 F3",
        "# raised in Q" + "A-18",
        "# see GD121-Q" + "A-2",
        "# guards the pidfd (" + "co" + "dex BLO" + "CKER 1)",
        "# kept narrow, ME" + "DIUM (co" + "dex)",
        "# F1 (Fa" + "ble/co" + "dex, BLO" + "CKER): a FIFO",
        "# cla" + "ude r3 MI" + "NOR-2: hidden",
        "# CO" + "DEX's MA" + "JOR 2",
        "# raised in Q" + "A 18",
        "def test_Q" + "A_18_case():",
        "# Q" + "A18a follow-up",
        "# closes Q" + "A18F" + "3",
        "# Co" + "dex: MA" + "JOR",
        "# Co" + "dex [BLO" + "CKER]",
        "# Co" + "dex `MI" + "NOR`",
        "# MI" + "NOR-3 CL" + "AUDE",
        "# BLO" + "CKER/Ge" + "mini",
        "# MA" + "JOR-co" + "dex",
        "# MA" + "JOR [co" + "dex]")


def _grade_twin_lines():
    """The legitimate lines beside them (ungraded.py, lines 1 to 7), assembled the same way."""
    return (
        'bump = "MAJOR"  # a MAJOR or MINOR version bump, a BLOCKER severity',
        "# the store refuses it (F-OPF-STATUSFILTER-EQ-BYPASS)",
        'RULE = "grade each finding BLOCKER, MAJOR, MEDIUM or MINOR; Claude, Codex and Gemini name '
        'no QA run"',
        "# cla" + "ude ma" + "jor changes land in a minor release",
        "# MINOR CLAUDE.md edits",
        "# the process pattern qa2451/ and the QA suite",
        "# the MINOR Claude Code changes ship in 2.4")


def _grade_rows(tmp):
    """A fix-round id, a QA round number (after a family name, alone with a hyphen or a space, inside a
    longer id or a snake_case name, with a letter or a finding suffix) and a reviewer family name beside
    a severity grade (in either order, a family pair, a numbered round between, an upper-case possessive,
    a colon, a square bracket or a backtick before the grade, a numbered grade, a slash, a hyphen or a
    square bracket before the family) are refused on added lines, each once; a severity word alone, a
    finding id, quoted rule text, a lower-case grade, a family name opening a file name, a lower-case QA
    path and a grade before a product name pass. Each refused row passes once its shape is removed; each
    passing row is refused once the narrowing that lets it through is removed."""
    repo = _base_repo(tmp, "grades")
    _fx_git(repo, "checkout", "-q", "-b", "work")
    labels, twins = _grade_label_lines(), _grade_twin_lines()
    _write(repo, "graded.py", "".join(text + "\n" for text in labels))
    _write(repo, "ungraded.py", "".join(text + "\n" for text in twins))
    _commit_all(repo, "graded labels and their legitimate twins")
    code, out = _gate(repo)
    for check_id, index, shape_id in (("refuse/fix-round-id", 1, "backlog-id"),
                                      ("refuse/qa-round-after-family", 2, "qa-round"),
                                      ("refuse/qa-round-hyphen-alone", 3, "qa-round"),
                                      ("refuse/qa-round-inside-id", 4, "qa-round"),
                                      ("refuse/family-then-grade", 5, "reviewer-grade"),
                                      ("refuse/grade-then-family", 6, "reviewer-grade"),
                                      ("refuse/family-pair-then-grade", 7, "reviewer-grade"),
                                      ("refuse/family-numbered-then-grade", 8, "reviewer-grade"),
                                      ("refuse/family-possessive-upper-case", 9, "reviewer-grade"),
                                      ("refuse/qa-round-space-joined", 10, "qa-round"),
                                      ("refuse/qa-round-snake-case", 11, "qa-round"),
                                      ("refuse/qa-round-letter-suffix", 12, "qa-round"),
                                      ("refuse/qa-round-finding-suffix", 13, "qa-round"),
                                      ("refuse/family-colon-grade", 14, "reviewer-grade"),
                                      ("refuse/family-bracketed-grade", 15, "reviewer-grade"),
                                      ("refuse/family-backticked-grade", 16, "reviewer-grade"),
                                      ("refuse/grade-numbered-then-family", 17, "reviewer-grade"),
                                      ("refuse/grade-slash-family", 18, "reviewer-grade"),
                                      ("refuse/grade-hyphen-family", 19, "reviewer-grade"),
                                      ("refuse/grade-bracketed-family", 20, "reviewer-grade")):
        check(check_id, code == 1 and "  graded.py:{}: {} (".format(index, shape_id) in out, out)
    check("refuse/graded-one-finding-per-line", code == 1 and out.count("  graded.py:") == len(labels), out)
    for check_id, index in (("pass/severity-word-alone", 1), ("pass/finding-id", 2),
                            ("pass/quoted-rule-text", 3), ("pass/lower-case-grade-prose", 4),
                            ("pass/family-file-name", 5), ("pass/lower-case-qa-path", 6),
                            ("pass/grade-before-product-name", 7)):
        check(check_id, "  ungraded.py:{}: ".format(index) not in out, out)
    check("pass/graded-twins-clean", "ungraded.py" not in out, out)
    removed = {}
    for shape_id in ("backlog-id", "qa-round", "reviewer-grade"):
        with _patched(load_config=_without_shape(shape_id)):
            removed[shape_id] = _gate(repo)[1]
    for check_id, index, shape_id in (
            ("flip/fix-round-id-shape-removed", 1, "backlog-id"),
            ("flip/qa-round-after-family-shape-removed", 2, "qa-round"),
            ("flip/qa-round-hyphen-alone-shape-removed", 3, "qa-round"),
            ("flip/qa-round-inside-id-shape-removed", 4, "qa-round"),
            ("flip/family-then-grade-shape-removed", 5, "reviewer-grade"),
            ("flip/grade-then-family-shape-removed", 6, "reviewer-grade"),
            ("flip/family-pair-then-grade-shape-removed", 7, "reviewer-grade"),
            ("flip/family-numbered-then-grade-shape-removed", 8, "reviewer-grade"),
            ("flip/family-possessive-upper-case-shape-removed", 9, "reviewer-grade"),
            ("flip/qa-round-space-joined-shape-removed", 10, "qa-round"),
            ("flip/qa-round-snake-case-shape-removed", 11, "qa-round"),
            ("flip/qa-round-letter-suffix-shape-removed", 12, "qa-round"),
            ("flip/qa-round-finding-suffix-shape-removed", 13, "qa-round"),
            ("flip/family-colon-grade-shape-removed", 14, "reviewer-grade"),
            ("flip/family-bracketed-grade-shape-removed", 15, "reviewer-grade"),
            ("flip/family-backticked-grade-shape-removed", 16, "reviewer-grade"),
            ("flip/grade-numbered-then-family-shape-removed", 17, "reviewer-grade"),
            ("flip/grade-slash-family-shape-removed", 18, "reviewer-grade"),
            ("flip/grade-hyphen-family-shape-removed", 19, "reviewer-grade"),
            ("flip/grade-bracketed-family-shape-removed", 20, "reviewer-grade")):
        check(check_id, "  graded.py:{}: ".format(index) not in removed[shape_id], removed[shape_id])

    grade = _shipped_regex("reviewer-grade")
    qa = _shipped_regex("qa-round")
    for check_id, index, patterns in (
            ("flip/severity-word-alone-flagged-without-family", 1,
             {"reviewer-grade": (r"(?<![A-Za-z0-9_])(?:BLOCKER|MAJOR|MEDIUM|MINOR)(?![A-Za-z_])", 0)}),
            ("flip/finding-id-flagged-when-widened", 2,
             {"reviewer-grade": (grade + r"|\bF-[A-Z]+-[A-Z]", 0)}),
            ("flip/quoted-rule-text-flagged-with-list-punctuation", 3,
             {"reviewer-grade": (_swapped(grade, r"(?:[ \t]*[(/\[\x60][ \t]*|[ \t]+|-)",
                                          r"[ \t]*[,:;(/\[\x60-]?[ \t]*"), 0)}),
            ("flip/lower-case-grade-flagged-when-case-folded", 4, {"reviewer-grade": (grade, re.IGNORECASE)}),
            ("flip/family-file-name-flagged-without-dot-exception", 5,
             {"reviewer-grade": (_swapped(grade, r"|\.[A-Za-z]|", "|"), 0)}),
            ("flip/lower-case-qa-path-flagged-when-case-folded", 6, {"qa-round": (qa, re.IGNORECASE)}),
            ("flip/grade-before-product-name-flagged-without-exemption", 7,
             {"reviewer-grade": (_swapped(grade, r"|[ \t-]+(?i:code)(?![A-Za-z0-9_])", ""), 0)})):
        with _patched(load_config=_with_patterns(patterns)):
            code, out = _gate(repo)
        check(check_id, code == 1 and "  ungraded.py:{}: ".format(index) in out, out)


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
