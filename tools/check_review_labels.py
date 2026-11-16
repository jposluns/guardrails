#!/usr/bin/env python3
"""Changed-lines gate: refuse review and work labels that added lines write into tracked files.

  check_review_labels.py [--protected REF] [--head REV]   exit 0 clean, 1 finding, 2 cannot evaluate
  check_review_labels.py --self-test                     fixture repositories, each row with its flip
  check_review_labels.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

WHAT IT JUDGES. Only the lines a change ADDS: git diff from the merge base of the protected branch and
the candidate, with renames followed (--find-renames), no context lines, no external diff driver and no
textconv. The candidate is the working tree (every tracked file, staged or not) unless --head names a
commit, in which case it is that commit's tree. A line that already sits on the merge base, a line the
change deletes, and a file the change only renames are not judged, so a branch is charged only with what
it writes. Every added line is searched for each label shape in .aiqt/review-labels.toml (a tracked TOML
file: a test name numbered after a review cycle, a parenthesised review or QA cycle tag, a numbered
round, the words review and round side by side, a numbered finding and a tracker backlog id by default,
each one Python regular expression). A line carrying the file's allow-marker is a deliberate quotation
and is exempt; the marker exempts that one line only.

THE PROTECTED BRANCH is derived from the repository, in this order: --protected REF when given; else
origin/GITHUB_BASE_REF when that variable is set and not empty (a pull request run); else the branch
refs/remotes/origin/HEAD names (a local clone); else, only when GITHUB_ACTIONS is "true",
origin/GITHUB_REF_NAME (a push or manual run, where the candidate usually IS that branch, so nothing is
added and the run passes: there the pull request run is the effective gate). No other fallback exists.

FAIL CLOSED (exit 2, never a pass): no protected branch can be derived, a ref or revision that does not
resolve to a commit, a shallow repository (its merge base can be wrong), no common ancestor, more than
one merge base, git that cannot run or exits nonzero (an unreadable tracked file makes git diff fail),
a diff line the parser does not recognise or a hunk whose line counts do not add up, and a configuration
file that is missing, not a regular file, over MAX_CONFIG_BYTES, not UTF-8, not TOML, or off its closed
schema. A finding (exit 1) names the file, the line number in the candidate and the shape.

DISCLOSED LIMITS. A binary file is not read (git prints no lines for it); an untracked file is not
judged; a label outside the configured shapes, or split, encoded or wrapped across lines, is not
matched; prose that matches a shape without being a label is a false positive that the allow-marker or
a rewording settles; the configuration is trusted reviewed input, so a change that edits it can weaken
it in the same change, which review of that file's diff has to catch. Commit messages and pull request
text are outside this gate.
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
from collections import namedtuple
from pathlib import Path

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
# The diff the gate reads. Every option that config could change is named, so a local diff.renames,
# diff.external, textconv driver, prefix or colour setting cannot change what is judged.
DIFF_OPTIONS = ("--no-color", "--no-ext-diff", "--no-textconv", "--unified=0", "--find-renames",
                "--src-prefix=a/", "--dst-prefix=b/", "--no-relative", "--ignore-submodules=all")
# The hunk line kinds that are judged: added lines only.
JUDGED_SIGILS = ("+",)
# The header lines a diff of this shape prints outside a hunk.
HEADER_PREFIXES = (b"diff --git ", b"index ", b"--- ", b"new file mode ", b"deleted file mode ",
                   b"old mode ", b"new mode ", b"similarity index ", b"dissimilarity index ",
                   b"rename from ", b"rename to ", b"copy from ", b"copy to ", b"Binary files ")
HUNK_RE = re.compile(rb"@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@")
CONFIG_KEYS = frozenset(("format-version", "allow-marker", "shape"))
SHAPE_KEYS = frozenset(("id", "description", "regex", "ignore-case"))
SHAPE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", re.ASCII)
MARKER_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,63}", re.ASCII)
_C_ESCAPES = {ord("a"): 7, ord("b"): 8, ord("t"): 9, ord("n"): 10, ord("v"): 11, ord("f"): 12,
              ord("r"): 13, ord('"'): 34, ord("\\"): 92}

Shape = namedtuple("Shape", ("id", "description", "pattern"))
Config = namedtuple("Config", ("marker", "shapes"))
Added = namedtuple("Added", ("path", "number", "text"))
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
    return Config(marker, tuple(shapes))


# --- git ---------------------------------------------------------------------------------------------
def _git_env():
    """The caller's environment with every GIT_ variable dropped (no repository, index or config
    redirection), the system config skipped, and an offline, non-interactive, lock-free posture with
    replace refs ignored, so a grafted parent cannot move the merge base."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
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
        return target, "origin/HEAD"
    if proc.returncode != 1:
        raise CannotEvaluate("origin/HEAD cannot be read: git exited {} ({})".format(
            proc.returncode, _stderr(proc)))
    if environ.get(ACTIONS_VAR) == "true" and environ.get(REF_NAME_VAR, ""):
        return ORIGIN_PREFIX + environ[REF_NAME_VAR], REF_NAME_VAR
    raise CannotEvaluate("no protected branch: pass --protected REF, or point origin/HEAD at it with "
                         "git remote set-head origin --auto")


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


def _new_path(raw):
    """The display path of a +++ header's target (None for /dev/null)."""
    # git ends a header path holding a space with one tab, quoted or not; a tab inside a path is
    # always quoted, so the tab after the last byte is the terminator alone.
    if raw.endswith(b"\t"):
        raw = raw[:-1]
    if raw == b"/dev/null":
        return None
    if raw.startswith(b'"'):
        raw = _unquote(raw)
    if not raw.startswith(b"b/"):
        raise CannotEvaluate("diff target {!r} lacks the b/ prefix".format(raw))
    return raw[2:].decode("utf-8", "backslashreplace")


def added_lines(diff):
    """(added, binary_count): every judged hunk line of `diff` as Added(path, number, text), and the
    number of binary files git reported without lines. CannotEvaluate on an unrecognised line or a hunk
    whose line counts do not add up."""
    lines = diff.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    added, binary, path = [], 0, None
    old_no = new_no = old_left = new_left = 0
    for raw in lines:
        if old_left or new_left:
            sigil = raw[:1]
            if sigil == b"+" and new_left:
                number, new_no, new_left = new_no, new_no + 1, new_left - 1
            elif sigil == b"-" and old_left:
                number, old_no, old_left = old_no, old_no + 1, old_left - 1
            elif sigil == b" " and old_left and new_left:
                number, old_no, new_no = new_no, old_no + 1, new_no + 1
                old_left, new_left = old_left - 1, new_left - 1
            elif sigil == b"\\":
                continue
            else:
                raise CannotEvaluate("unexpected line {!r} inside a hunk of {}".format(raw[:80], path))
            if sigil.decode("ascii") in JUDGED_SIGILS:
                added.append(Added(path, number, raw[1:].decode("utf-8", "replace")))
            continue
        if raw.startswith(b"\\"):
            continue
        if raw.startswith(b"diff --git "):
            path = None
            continue
        if raw.startswith(b"+++ "):
            path = _new_path(raw[4:])
            continue
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
        if raw.startswith(b"Binary files "):
            binary += 1
            continue
        if raw.startswith(HEADER_PREFIXES):
            continue
        raise CannotEvaluate("unrecognised diff line {!r}".format(raw[:80]))
    if old_left or new_left:
        raise CannotEvaluate("the diff ends inside a hunk of {}".format(path))
    return added, binary


# --- judging -----------------------------------------------------------------------------------------
def _allowed(text, marker):
    return marker in text


def judge(added, config):
    findings = []
    for line in added:
        if _allowed(line.text, config.marker):
            continue
        for shape in config.shapes:
            if shape.pattern.search(line.text):
                findings.append(Finding(line.path, line.number, shape.id, shape.description, line.text))
    return findings


def evaluate(root, protected=None, head=None, environ=None):
    """(findings, summary) for the repository at `root`; CannotEvaluate when it cannot be judged."""
    environ = os.environ if environ is None else environ
    config = load_config(Path(root) / CONFIG_RELPATH)
    _require_full_history(root)
    ref, source = protected_ref(root, protected, environ)
    protected_sha = _resolve_commit(root, ref, "protected branch")
    head_sha = _resolve_commit(root, "HEAD" if head is None else head, "candidate")
    base = merge_base(root, protected_sha, head_sha)
    added, binary = added_lines(read_diff(root, base, None if head is None else head_sha))
    summary = "{} added line(s) in {} file(s) judged against merge base {} of {} (from {}){}{}".format(
        len(added), len({line.path for line in added}), base[:12], ref, source,
        "" if head is None else ", candidate {}".format(head_sha[:12]),
        "; {} binary file(s) not read".format(binary) if binary else "")
    return judge(added, config), summary, config.marker


def _printable(text):
    text = text.strip()
    if len(text) > MAX_EXCERPT:
        text = text[:MAX_EXCERPT] + "..."
    return text.encode("ascii", "backslashreplace").decode("ascii")


def run(root, protected=None, head=None, environ=None):
    try:
        findings, summary, marker = evaluate(root, protected, head, environ)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: review-label gate: {}".format(exc), file=sys.stderr)
        return 2
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
        return Config("review-label-allow", ())


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
          "derived from GITHUB_BASE_REF, origin/HEAD and the Actions ref name; the configuration size "
          "bound holds under, at and over; bad arguments exit 2)".format(len(EXECUTED)))
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
    no_renames = tuple("--no-renames" if o == "--find-renames" else o for o in DIFF_OPTIONS)
    with _patched(DIFF_OPTIONS=no_renames):
        code, out = _gate(repo)
    check("flip/rename-judged-without-rename-detection",
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

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"], ["--protected"],
                                         ["--protected", "main", "--protected", "main"], ["--head", ""],
                                         ["--execution-report", "relative/report"])]
    check("cli/bad-arguments-exit-2", got == [2, 2, 2, 2, 2, 2], got)


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


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


def main():
    argv = sys.argv[1:]
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


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
