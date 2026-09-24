#!/usr/bin/env python3
"""Hooks-preview channel gate: the published preview hooks are runnable, hashed, and pinned consistently.
Offline, stdlib only, fail-closed.

The hooks-preview/ directory is a public preview channel: a reader (usually an AI coding assistant acting
for a person) downloads one hook file from a pinned raw link, checks its SHA-256 against the value the
channel's README.md publishes, runs the hook's own self-test, and wires it into settings.json. This gate
keeps the three things that reader relies on true of the working tree.

NOT APPLICABLE. When hooks-preview/ is absent this gate prints NOT APPLICABLE and exits 0, so retiring the
channel (deleting the directory) needs no gate edit. A hooks-preview path that is present but is not a
real directory (a symbolic link, a regular file) is a cannot-evaluate, never absent.

When the directory is present, the channel's declared inputs are README.md and SHA256SUMS (both required;
an absent or unreadable one is exit 2), and every other entry must be a hook file named like
`clock-inject.py` (lowercase letters, digits and hyphens, ending .py). Three legs:

  (a) SELF-TEST. Each hooks-preview/*.py runs as [sys.executable, "-I", "-B", <path>, "--self-test"] in a
      fresh temporary working directory, with stdin closed and every AIQT_, ORCH_ and CLAUDE_ variable
      removed from its environment, so an operator's live hook configuration cannot steer the verdict.
      After that scrub the one variable AIQT_HOOKS_REQUIRE_SIBLINGS=1 is set: a hook skips its
      sibling-parity tests when a sibling file is absent unless that variable is "1", and here the
      siblings are present, so a parity test can never skip silently under this gate.
      A nonzero exit is a finding. A run that outlives SELFTEST_TIMEOUT seconds delivered no verdict and
      is a cannot-evaluate (exit 2), attributed to this gate's deadline, not reported as a hook failure.
  (b) INTEGRITY. The README.md integrity table (the one table whose header row is exactly
      `| File | SHA-256 | Pinned raw link |`; every line from its separator row to the first blank line
      or the end of the file must be a `| a | b | c |` row, since GitHub renders a row written without a
      leading pipe too) and SHA256SUMS (`sha256sum` text format, `<64 hex>  <name>`, with `#` comment
      lines allowed, lines ended by LF only) are parsed. README.md lines are split where GitHub splits
      them (LF, CR, CRLF). A line separator Python would honour but the consumer does not (VT, FF, FS,
      GS, RS, NEL, U+2028, U+2029, and in SHA256SUMS also CR) is a cannot-evaluate anywhere in either
      file, so the gate never parses a line its reader does not see. The two must list the same files
      with the same hashes;
      each listed hash must equal the SHA-256 of that file's working-tree bytes; a hook file present but
      unlisted, or listed but absent, is a finding. A SHA256SUMS name containing `/` (an absolute or
      relative path, not a bare file name) is a finding, because `sha256sum -c` would then check the file
      at that path rather than the downloaded one. A table or SHA256SUMS line that cannot be parsed is a
      cannot-evaluate (exit 2). An empty listing is valid only when no hook file is present.
  (c) PIN. Each table row's raw link must have the shape
      https://raw.githubusercontent.com/<owner>/<repo>/refs/tags/<tag>/hooks-preview/<file>, where <file>
      names the same file as the row and <tag> matches hooks-preview-v<N>. The explicit refs/tags/ form is
      required because a bare <tag> segment is ambiguous (a branch of the same name could be served
      instead of the tag this leg verifies); a bare tag, refs/heads/, or any other ref form is a finding.
      When refs/tags/<tag> exists in the local repository, it must resolve to a readable commit and the
      blob at <tag>:hooks-preview/<file> must exist and hash to the README value. When the ref does not
      exist (the normal state on the pull request that introduces or bumps it, before the maintainer
      pushes the tag after merge), a note is printed and the leg is skipped for that row. A git failure
      other than an absent tag ref or a path genuinely absent at the tag (git missing, not a repository,
      a tag ref that exists but does not resolve to a readable commit, an unreadable object) is a
      cannot-evaluate.
  A scratch directory that cannot be created for a hook self-test is a cannot-evaluate for that hook.

DISCLOSED RESIDUALS (what this gate does not catch):
  - Leg (a) trusts each hook's own self-test: a hook whose self-test is weak, or that exits 0 without
    testing, passes. The gate judges the exit status only, never the self-test's content.
  - Leg (c) is skipped, with a printed note, whenever the tag ref is absent locally, including a CI
    checkout that fetched no tags; the pin is then enforced only for shape. The <owner>/<repo> part of a
    link is not validated against any canonical origin, so a link to a different repository with the
    right shape passes; the SHA-256 in the README is the trust anchor, and it is checked in leg (b).
  - README hash, SHA256SUMS, and hook bytes share one origin (this repository), so this gate proves the
    three agree with each other and with the tag, not that the repository itself is uncompromised.
  - Only the one integrity table is parsed; hashes or links written elsewhere in README.md prose are not.
  - The environment scrub removes three variable families and sets AIQT_HOOKS_REQUIRE_SIBLINGS=1, and
    changes nothing else; a hook self-test that reads another ambient input (the clock, the time zone,
    the locale) is responsible for pinning it itself.

  check_hooks_preview.py              run the gate over hooks-preview/
  check_hooks_preview.py --self-test  synthetic temp-tree fixtures proving each leg fails on a seeded fault

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE
  1  a real finding (a failing hook self-test, a hash or listing disagreement, a bad or stale pin)
  2  cannot-evaluate: a missing or unreadable required input, an unparseable table or SHA256SUMS line, a
     self-test that timed out or had no scratch directory, a git failure other than an absent tag ref or
     an absent path, an unrecognized argument, or (under --self-test) a mandatory case that was skipped
"""
import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PREVIEW_DIR = "hooks-preview"
README_NAME = "README.md"
SUMS_NAME = "SHA256SUMS"
# A hook file name: lowercase letters, digits and hyphens, ending .py (safe in a URL and a shell command).
HOOK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.py$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
# sha256sum text-mode line: 64 lowercase hex, exactly two spaces, a name without whitespace. The name is
# parsed even when it carries a `/` so leg (b) can report a path entry as a finding (not a parse failure).
SUMS_LINE_RE = re.compile(r"^([0-9a-f]{64})  (\S+)$")
TABLE_HEADER = "| File | SHA-256 | Pinned raw link |"
TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|$")
TAG_RE = re.compile(r"^hooks-preview-v\d+$")
# A raw link to a channel file; group 3 is the ref part, which leg (c) requires to be refs/tags/<tag>.
RAW_LINK_RE = re.compile(
    r"^https://raw\.githubusercontent\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(\S+?)/"
    + re.escape(PREVIEW_DIR) + r"/([^/\s]+)$")
PINNED_REF_RE = re.compile(r"^refs/tags/([^/\s]+)$")
# Environment families removed from a hook self-test (live hook configuration must not steer the verdict).
SCRUB_PREFIXES = ("AIQT_", "ORCH_", "CLAUDE_")
# Set after the scrub: a hook's sibling-parity tests must run, never skip, under this gate.
REQUIRE_SIBLINGS_VAR = "AIQT_HOOKS_REQUIRE_SIBLINGS"
# Characters str.splitlines() treats as line breaks that the channel's readers do not. `sha256sum -c` ends
# a line at LF only; GitHub (CommonMark) ends a README line at LF, CR or CRLF, so CR is allowed there.
SUMS_BREAK_CHARS = "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"
README_BREAK_CHARS = SUMS_BREAK_CHARS.replace("\r", "")
# The whitespace GitHub trims around a table row or cell and treats as blank (space and tab only).
GFM_SPACE = " \t"
# Seconds one hook self-test may run. Set well above the slowest shipped self-test (a few seconds today),
# so a correctly running self-test is never cut short; exceeding it is reported as this gate's deadline.
SELFTEST_TIMEOUT = 600
GIT_TIMEOUT = 60
# Characters of an OS or git error kept in a diagnostic, so a failure is reported, never dumped.
DIAG_LIMIT = 300


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Reported as exit 2 (fail-closed): an unreadable
    or malformed input is never treated as an empty or clean result."""


def _repo_root():
    here = Path(__file__).resolve()
    for anc in here.parents:
        if (anc / ".git").exists():
            return anc
    return here.parent.parent


# --- inputs (fail-closed) -------------------------------------------------------------------------

def _classify_preview(root):
    """Return None when hooks-preview/ is genuinely absent, else the directory Path. A present entry that
    is not a real directory (a symbolic link included, never followed) raises GateError."""
    path = root / PREVIEW_DIR
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GateError("cannot stat {}: {}".format(PREVIEW_DIR, exc))
    if not stat.S_ISDIR(st.st_mode):
        raise GateError("{} is present but is not a real directory (a symbolic link or another entry "
                        "type); fail closed".format(PREVIEW_DIR))
    return path


def _list_entries(pdir):
    """Return {name: lstat mode} for every entry in the preview directory, fail-closed on a listing error."""
    try:
        names = sorted(os.listdir(pdir))
    except OSError as exc:
        raise GateError("cannot list {}: {}".format(PREVIEW_DIR, exc))
    entries = {}
    for name in names:
        try:
            entries[name] = os.lstat(pdir / name).st_mode
        except OSError as exc:
            raise GateError("cannot stat {}/{}: {}".format(PREVIEW_DIR, name, exc))
    return entries


def _read_required(pdir, name, entries):
    """Read a required regular file of the channel as bytes. Absent, non-regular, or unreadable is exit 2."""
    mode = entries.get(name)
    if mode is None:
        raise GateError("{}/{} is required when {}/ exists and is absent".format(PREVIEW_DIR, name, PREVIEW_DIR))
    if not stat.S_ISREG(mode):
        raise GateError("{}/{} is not a regular file".format(PREVIEW_DIR, name))
    try:
        return (pdir / name).read_bytes()
    except OSError as exc:
        raise GateError("cannot read {}/{}: {}".format(PREVIEW_DIR, name, exc))


def _decode(name, data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError("{}/{} is not valid UTF-8: {}".format(PREVIEW_DIR, name, exc))


def _reject_breaks(name, lines, chars, reader, endings):
    """Raise GateError at the first line holding one of `chars`, a line break Python would honour but the
    file's reader does not, so the gate never parses a line that reader does not see."""
    for number, line in enumerate(lines, 1):
        for ch in line:
            if ch in chars:
                raise GateError("{}:{}: U+{:04X} is not a line break for {}; lines end with {} only".format(
                    name, number, ord(ch), reader, endings))


def parse_sums(text):
    """Parse SHA256SUMS. Returns {name: hex}. Lines end with LF only (one trailing LF allowed); `#` comment
    lines and blank lines are skipped; any other line must be `<64 lowercase hex>  <name>`. Another line
    separator anywhere, a malformed line, or a duplicate name raises GateError."""
    listed = {}
    lines = (text[:-1] if text.endswith("\n") else text).split("\n")
    _reject_breaks(SUMS_NAME, lines, SUMS_BREAK_CHARS, "`sha256sum -c`", "LF")
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        m = SUMS_LINE_RE.match(line)
        if m is None:
            raise GateError("{}:{}: not a `<64 lowercase hex>  <name>` line".format(SUMS_NAME, number))
        hexd, name = m.group(1), m.group(2)
        if name in listed:
            raise GateError("{}:{}: {} is listed twice".format(SUMS_NAME, number, name))
        listed[name] = hexd
    return listed


def _cell_code(cell):
    """Unwrap a single backtick code span, or return None."""
    cell = cell.strip(GFM_SPACE)
    if len(cell) >= 2 and cell.startswith("`") and cell.endswith("`") and "`" not in cell[1:-1]:
        return cell[1:-1]
    return None


def parse_readme_table(text):
    """Parse the one integrity table. Returns a list of (line_number, name, hex, link). Lines split where
    GitHub splits them (LF, CR, CRLF); any other line break Python would honour raises GateError. The
    header row must appear exactly once and be followed by a separator row; every line from there to the
    first blank line (or the end of the file) is a row, because GitHub renders a line without a leading
    `|` as a row too, so each must be a well-formed `| a | b | c |` row. A missing or duplicated header, a
    missing separator, or a malformed row raises GateError."""
    lines = re.split(r"\r\n|\r|\n", text)
    _reject_breaks(README_NAME, lines, README_BREAK_CHARS, "GitHub", "LF, CR or CRLF")
    headers = [i for i, line in enumerate(lines) if line.strip(GFM_SPACE) == TABLE_HEADER]
    if len(headers) != 1:
        raise GateError("{}: expected exactly one integrity table header row `{}`, found {}".format(
            README_NAME, TABLE_HEADER, len(headers)))
    h = headers[0]
    if h + 1 >= len(lines) or TABLE_SEPARATOR_RE.match(lines[h + 1].strip(GFM_SPACE)) is None:
        raise GateError("{}:{}: the integrity table header is not followed by a separator row".format(
            README_NAME, h + 2))
    rows = []
    seen = set()
    for i in range(h + 2, len(lines)):
        line = lines[i].strip(GFM_SPACE)
        if not line:
            break
        number = i + 1
        if not line.startswith("|"):
            raise GateError("{}:{}: a line inside the integrity table does not start with `|`; GitHub renders "
                            "it as a row, so write it as `| a | b | c |` or end the table with a blank line "
                            "first".format(README_NAME, number))
        if not line.endswith("|"):
            raise GateError("{}:{}: integrity table row does not end with `|`".format(README_NAME, number))
        cells = line[1:-1].split("|")
        if len(cells) != 3:
            raise GateError("{}:{}: integrity table row has {} cells, expected 3".format(
                README_NAME, number, len(cells)))
        name = _cell_code(cells[0])
        hexd = _cell_code(cells[1])
        link = cells[2].strip(GFM_SPACE)
        if link.startswith("<") and link.endswith(">"):
            link = link[1:-1]
        if name is None or hexd is None or not link:
            raise GateError("{}:{}: integrity table row must be | `file` | `sha256` | link |".format(
                README_NAME, number))
        if HEX64_RE.match(hexd) is None:
            raise GateError("{}:{}: {!r} is not 64 lowercase hex".format(README_NAME, number, hexd))
        if name in seen:
            raise GateError("{}:{}: {} is listed twice in the integrity table".format(README_NAME, number, name))
        seen.add(name)
        rows.append((number, name, hexd, link))
    return rows


# --- git (the pin leg) ----------------------------------------------------------------------------

def _git_env():
    """Remove ambient git redirection and config channels, reassert an offline non-interactive posture,
    and disable replace refs so a replacement object cannot stand in for the tagged blob."""
    env = dict(os.environ)
    for key in [key for key in env if key.startswith("GIT_")]:
        env.pop(key, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _git(root, *args):
    try:
        return subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                              capture_output=True, timeout=GIT_TIMEOUT, env=_git_env(),
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError("git invocation failed: {}".format(exc))


def resolve_tag(root, tag):
    """Return the commit id the tag names, or None when refs/tags/<tag> does not exist. Existence is read
    from the ref store itself (`git for-each-ref`, which lists a ref whatever its object's state), so a
    tag ref that exists but does not resolve to a readable commit raises GateError instead of reading as
    absent. Any other git outcome (not a repository, git missing) raises GateError."""
    probe = _git(root, "rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0:
        raise GateError("the pin leg cannot evaluate: {} is not a readable git repository ({})".format(
            root, probe.stderr.decode("utf-8", "replace").strip() or "git rev-parse failed"))
    ref = "refs/tags/{}".format(tag)
    listed = _git(root, "for-each-ref", "--format=%(refname)", ref)
    if listed.returncode != 0 or listed.stderr.strip():
        raise GateError("cannot list {}: {}".format(ref, _bounded(
            listed.stderr or "git for-each-ref exit {}".format(listed.returncode))))
    if ref.encode("utf-8") not in listed.stdout.split(b"\n"):
        return None
    res = _git(root, "rev-parse", "--verify", "--quiet", "{}^{{commit}}".format(ref))
    if res.returncode == 0:
        sha = res.stdout.decode("ascii", "replace").strip()
        if re.match(r"^[0-9a-f]{40}([0-9a-f]{24})?$", sha) is None:
            raise GateError("git returned an unrecognized commit id for tag {}: {!r}".format(tag, sha))
        return sha
    raise GateError("tag {} exists but does not resolve to a readable commit: {}".format(tag, _bounded(
        res.stderr or "git rev-parse exit {}".format(res.returncode))))


def _bounded(data, limit=DIAG_LIMIT):
    """One-line diagnostic text from bytes or str, cut to `limit` characters so a failure never floods."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    text = " ".join(str(data).split())
    return text if len(text) <= limit else text[:limit] + "..."


def blob_at(root, commit, name):
    """Return the bytes of hooks-preview/<name> at commit, or None when that path is genuinely absent there
    or is not a blob. The tree is listed with `git ls-tree`, which exits 0 with no output for a missing
    path, so any other git outcome (a nonzero exit, stderr output, an unparseable entry, an unreadable
    object) is a cannot-evaluate raised as GateError, never collapsed into a missing blob."""
    path = "{}/{}".format(PREVIEW_DIR, name)
    res = _git(root, "--literal-pathspecs", "ls-tree", "-z", commit, "--", path)
    if res.returncode != 0 or res.stderr.strip():
        raise GateError("cannot list {} at {}: {}".format(path, commit, _bounded(
            res.stderr or "git ls-tree exit {}".format(res.returncode))))
    entries = [e for e in res.stdout.split(b"\0") if e]
    if not entries:
        return None
    m = re.match(rb"^([0-7]{6}) ([a-z]+) ([0-9a-f]{40}(?:[0-9a-f]{24})?)\t(.*)$", entries[0], re.S)
    if len(entries) != 1 or m is None or m.group(4) != path.encode("utf-8"):
        raise GateError("git ls-tree returned an unrecognized listing for {} at {}: {}".format(
            path, commit, _bounded(res.stdout)))
    if m.group(2) != b"blob":
        return None
    oid = m.group(3).decode("ascii")
    blob = _git(root, "cat-file", "blob", oid)
    if blob.returncode != 0 or blob.stderr.strip():
        raise GateError("cannot read {} at {}: {}".format(path, commit, _bounded(
            blob.stderr or "git cat-file exit {}".format(blob.returncode))))
    return blob.stdout


# --- the three legs -------------------------------------------------------------------------------

def _selftest_env():
    """The hook self-test environment: the three families scrubbed, then only REQUIRE_SIBLINGS_VAR=1 set."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB_PREFIXES)}
    env[REQUIRE_SIBLINGS_VAR] = "1"
    return env


def _tail(data, n=15):
    lines = data.decode("utf-8", "replace").rstrip().splitlines()
    return lines[-n:]


def leg_selftest(pdir, hooks, findings, unverifiable):
    """(a) Run each hook's own --self-test isolated; nonzero is a finding, a timeout is unverifiable. The
    environment is scrubbed of AIQT_, ORCH_ and CLAUDE_ variables and then carries AIQT_HOOKS_REQUIRE_SIBLINGS=1
    (set after the scrub), so a hook's sibling-parity tests run rather than skip silently in CI."""
    for name in hooks:
        path = str((pdir / name).resolve())
        try:
            work = tempfile.mkdtemp(prefix="aiqt-hooks-preview-")
        except OSError as exc:
            unverifiable.append("{}/{}: cannot create a scratch directory for --self-test: {}".format(
                PREVIEW_DIR, name, _bounded(exc)))
            continue
        try:
            try:
                res = subprocess.run([sys.executable, "-I", "-B", path, "--self-test"], cwd=work,
                                     capture_output=True, timeout=SELFTEST_TIMEOUT, env=_selftest_env(),
                                     stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                unverifiable.append("{}/{}: --self-test did not finish within this gate's {}s deadline; no "
                                    "verdict was delivered".format(PREVIEW_DIR, name, SELFTEST_TIMEOUT))
                continue
            except (OSError, subprocess.SubprocessError) as exc:
                unverifiable.append("{}/{}: --self-test could not be launched: {}".format(PREVIEW_DIR, name, exc))
                continue
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if res.returncode != 0:
            detail = _tail(res.stdout) + _tail(res.stderr)
            findings.append("{}/{}: --self-test exited {} (a):\n      {}".format(
                PREVIEW_DIR, name, res.returncode, "\n      ".join(detail) or "(no output)"))
        else:
            print("  {}/{}: --self-test exit 0".format(PREVIEW_DIR, name))


def leg_integrity(pdir, hooks, rows, sums, findings):
    """(b) README table and SHA256SUMS agree, each hash matches the working-tree bytes, and the listing is
    exactly the set of hook files present."""
    table = {name: hexd for _, name, hexd, _ in rows}
    for name in sorted(set(table) | set(sums)):
        if name not in sums:
            findings.append("{} lists {} but {} does not (b)".format(README_NAME, name, SUMS_NAME))
        elif name not in table:
            findings.append("{} lists {} but the {} integrity table does not (b)".format(SUMS_NAME, name, README_NAME))
        elif table[name] != sums[name]:
            findings.append("{}: {} records {} but {} records {} (b)".format(
                name, README_NAME, table[name], SUMS_NAME, sums[name]))
    listed = set(table) | set(sums)
    for name in sorted(listed):
        if name in sums and "/" in name:
            findings.append("{} lists {!r}, a path, not a bare file name; an adopter's `sha256sum -c` would "
                            "check the file at that path instead of the downloaded one (b)".format(SUMS_NAME, name))
            continue
        if HOOK_NAME_RE.match(name) is None:
            findings.append("{} is listed but is not a hook file name (lowercase, digits, hyphens, .py) (b)"
                            .format(name))
            continue
        if name not in hooks:
            findings.append("{} is listed but {}/{} is absent (b)".format(name, PREVIEW_DIR, name))
            continue
        try:
            got = hashlib.sha256((pdir / name).read_bytes()).hexdigest()
        except OSError as exc:
            raise GateError("cannot read {}/{}: {}".format(PREVIEW_DIR, name, exc))
        for source, recorded in ((README_NAME, table.get(name)), (SUMS_NAME, sums.get(name))):
            if recorded is not None and recorded != got:
                findings.append("{}/{}: {} records {} but the file hashes to {} (b)".format(
                    PREVIEW_DIR, name, source, recorded, got))
    for name in sorted(set(hooks) - listed):
        findings.append("{}/{} is present but listed in neither {} nor {} (b)".format(
            PREVIEW_DIR, name, README_NAME, SUMS_NAME))


def leg_pin(root, rows, findings):
    """(c) Each raw link names its own file under refs/tags/hooks-preview-v<N>; when that tag ref exists
    locally the blob at the tag must hash to the README value."""
    commits = {}
    for number, name, hexd, link in rows:
        m = RAW_LINK_RE.match(link)
        if m is None:
            findings.append("{}:{}: {} link is not a raw.githubusercontent.com/<owner>/<repo>/refs/tags/<tag>/"
                            "{}/<file> link (c)".format(README_NAME, number, name, PREVIEW_DIR))
            continue
        ref, link_file = m.group(3), m.group(4)
        if link_file != name:
            findings.append("{}:{}: the {} row links to {} (c)".format(README_NAME, number, name, link_file))
        pinned = PINNED_REF_RE.match(ref)
        if pinned is None:
            findings.append("{}:{}: the {} link names ref {!r}; use refs/tags/<tag>, so the tag and never a "
                            "same-named branch is served (c)".format(README_NAME, number, name, ref))
            continue
        tag = pinned.group(1)
        if TAG_RE.match(tag) is None:
            findings.append("{}:{}: the {} link is pinned to {!r}, not a hooks-preview-v<N> tag (c)".format(
                README_NAME, number, name, tag))
            continue
        if link_file != name:
            continue
        if tag not in commits:
            commits[tag] = resolve_tag(root, tag)
        commit = commits[tag]
        if commit is None:
            print("  note: tag {} does not exist locally; the at-tag hash check for {} is skipped (the tag "
                  "is pushed after merge)".format(tag, name))
            continue
        blob = blob_at(root, commit, name)
        if blob is None:
            findings.append("{}:{}: tag {} has no {}/{} (c)".format(README_NAME, number, tag, PREVIEW_DIR, name))
            continue
        got = hashlib.sha256(blob).hexdigest()
        if got != hexd:
            findings.append("{}:{}: {}/{} at tag {} hashes to {} but {} records {} (c)".format(
                README_NAME, number, PREVIEW_DIR, name, tag, got, README_NAME, hexd))


# --- run ------------------------------------------------------------------------------------------

def run(root):
    """Run the gate against `root`. Returns the exit code 0/1/2."""
    findings, unverifiable = [], []
    try:
        pdir = _classify_preview(root)
        if pdir is None:
            print("hooks-preview: NOT APPLICABLE (no {}/ directory)".format(PREVIEW_DIR))
            return 0
        entries = _list_entries(pdir)
        readme = _decode(README_NAME, _read_required(pdir, README_NAME, entries))
        sums = parse_sums(_decode(SUMS_NAME, _read_required(pdir, SUMS_NAME, entries)))
        rows = parse_readme_table(readme)
        hooks = []
        for name, mode in sorted(entries.items()):
            if name in (README_NAME, SUMS_NAME):
                continue
            if not stat.S_ISREG(mode):
                findings.append("{}/{} is not a regular file (a symbolic link, directory, or other entry) "
                                "(b)".format(PREVIEW_DIR, name))
            elif HOOK_NAME_RE.match(name) is None:
                findings.append("{}/{} is not an expected channel file (README.md, SHA256SUMS, or a hook "
                                "named like clock-inject.py) (b)".format(PREVIEW_DIR, name))
            else:
                hooks.append(name)
        print("hooks-preview: {} hook file(s), {} integrity-table row(s)".format(len(hooks), len(rows)))
        leg_selftest(pdir, hooks, findings, unverifiable)
        leg_integrity(pdir, hooks, rows, sums, findings)
        leg_pin(root, rows, findings)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} hooks-preview finding(s)".format(len(findings)))
        for finding in findings:
            print("  " + finding)
    if unverifiable:
        print("CANNOT EVALUATE: {} hooks-preview input(s) delivered no verdict".format(len(unverifiable)),
              file=sys.stderr)
        for item in unverifiable:
            print("  " + item, file=sys.stderr)
        return 2
    if findings:
        return 1
    print("PASS: hooks-preview holds (every hook self-test passes, README and SHA256SUMS agree with the "
          "files, and every pin is well formed and matches its tag where the tag resolves)")
    return 0


# --- self-test ------------------------------------------------------------------------------------
# Synthetic temp trees in the house style (no wall clock; a random temp-root suffix does not affect the
# verdict). The git-backed cases need a git binary; without one they are SKIPPED, the skipped cases are
# named, and the self-test exits 2 (INCOMPLETE), never a pass.

_STUB_OK = b"import sys\nsys.exit(0 if '--self-test' in sys.argv else 3)\n"
_STUB_FAIL = b"import sys\nprint('stub self-test failure')\nsys.exit(1)\n"
_STUB_SLOW = b"import time\ntime.sleep(30)\n"
# Passes only when its environment carries REQUIRE_SIBLINGS_VAR=1 and no other scrubbed-family variable.
_STUB_ENV = (b"import os, sys\nkeys = sorted(k for k in os.environ if k.startswith(('AIQT_', 'ORCH_', 'CLAUDE_')))\n"
             b"sys.exit(0 if keys == ['AIQT_HOOKS_REQUIRE_SIBLINGS'] and "
             b"os.environ['AIQT_HOOKS_REQUIRE_SIBLINGS'] == '1' else 1)\n")
_LINK = "https://raw.githubusercontent.com/example/pack/refs/tags/{tag}/hooks-preview/{name}"
_BARE_LINK = "https://raw.githubusercontent.com/example/pack/{ref}/hooks-preview/{name}"
# Scrubbed-family variables seeded into the gate's own environment to prove leg (a) removes or overrides them.
_SEEDED_ENV = {"AIQT_SEEDED": "x", REQUIRE_SIBLINGS_VAR: "0", "ORCH_SEEDED": "x", "CLAUDE_SEEDED": "x"}


def _readme(rows, header=TABLE_HEADER):
    out = ["# Hooks preview", "", "## Integrity", "", header, "|---|---|---|"]
    for name, hexd, link in rows:
        out.append("| `{}` | `{}` | {} |".format(name, hexd, link))
    out += ["", "## Status", "", "Preview.", ""]
    return "\n".join(out)


def _sums(pairs):
    lines = ["# SHA-256 checksums for the hooks-preview files"]
    lines += ["{}  {}".format(hexd, name) for name, hexd in pairs]
    return "\n".join(lines) + "\n"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _build(base, files, rows=None, sums=None, readme=None):
    """Write base/hooks-preview/ with files {name: bytes}. rows/sums default to a consistent listing of
    every file, linked at tag hooks-preview-v1."""
    pdir = base / PREVIEW_DIR
    pdir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (pdir / name).write_bytes(data)
    if rows is None:
        rows = [(n, _sha(d), _LINK.format(tag="hooks-preview-v1", name=n)) for n, d in sorted(files.items())]
    if sums is None:
        sums = [(n, _sha(d)) for n, d in sorted(files.items())]
    (pdir / README_NAME).write_text(readme if readme is not None else _readme(rows), encoding="utf-8")
    if sums is not False:
        (pdir / SUMS_NAME).write_text(_sums(sums), encoding="utf-8")
    return base


def _fixture_git(root, *args):
    env = _git_env()
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=selftest", "-c",
                           "user.email=selftest@example.invalid", "-c", "commit.gpgsign=false", "-c",
                           "tag.gpgsign=false", "-c", "core.hooksPath=" + os.devnull, *args],
                          capture_output=True, timeout=GIT_TIMEOUT, env=env, stdin=subprocess.DEVNULL,
                          check=True)


def _git_init(root):
    _fixture_git(root, "init", "-q")


def _commit_and_tag(root, tag):
    _fixture_git(root, "add", "-A")
    _fixture_git(root, "commit", "-q", "-m", "fixture")
    _fixture_git(root, "tag", tag)


def _run_captured(root):
    """Run the gate with its output captured. Returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run(root)
    return code, out.getvalue(), err.getvalue()


def _no_scratch(*args, **kwargs):
    raise OSError(28, "No space left on device (seeded by the self-test)")


def _seed_env():
    """Seed _SEEDED_ENV into os.environ; returns the prior values for _restore_env."""
    saved = {k: os.environ.get(k) for k in _SEEDED_ENV}
    os.environ.update(_SEEDED_ENV)
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def self_test_main():
    global SELFTEST_TIMEOUT
    failures = []
    ok = {"clock-inject.py": _STUB_OK}
    ok_hex = _sha(_STUB_OK)
    link_v1 = _LINK.format(tag="hooks-preview-v1", name="clock-inject.py")

    try:
        base = Path(tempfile.mkdtemp(prefix="aiqt-hooks-preview-selftest-"))
    except OSError:
        print("SELF-TEST FAIL: no writable temp directory; no fixture could be built", file=sys.stderr)
        return 1
    git_ran = shutil.which("git") is not None
    ran = []
    skipped = []
    try:
        def case(label, expected, root, needle=None):
            """Run the gate on root and require exit `expected`; with `needle`, also require that text in
            the gate's combined output. An exception escaping the gate is a failure of the case."""
            ran.append(label)
            try:
                got, out, err = _run_captured(root)
            except Exception as exc:  # a raw traceback escaping the gate is itself the defect
                failures.append("{}: the gate raised {}: {}".format(label, type(exc).__name__, _bounded(exc)))
                return
            if got != expected:
                failures.append("{}: expected exit {}, got {}".format(label, expected, got))
            elif needle is not None and needle not in out + err:
                failures.append("{}: exit {} as expected but the output lacks {!r}".format(label, got, needle))

        def repo(name):
            """A fresh fixture root that is its own git repository, so the pin leg never walks up into an
            enclosing repository. Returns None when no git binary exists (the case is then SKIPPED)."""
            if not git_ran:
                return None
            r = base / name
            r.mkdir(parents=True)
            _git_init(r)
            return r

        # Pure parser cases (always run).
        try:
            parse_sums("abc  x.py\n")
            failures.append("parse_sums accepted a short hash")
        except GateError:
            pass
        if parse_sums("# only a comment\n") != {}:
            failures.append("parse_sums did not accept a comment-only listing as empty")
        if parse_sums("{}  x.py".format("0" * 64)) != {"x.py": "0" * 64}:
            failures.append("parse_sums did not accept a listing without a trailing LF")
        saved_env = _seed_env()
        try:
            got_env = {k: v for k, v in _selftest_env().items() if k.startswith(SCRUB_PREFIXES)}
        finally:
            _restore_env(saved_env)
        if got_env != {REQUIRE_SIBLINGS_VAR: "1"}:
            failures.append("the hook self-test environment carries {!r}, expected only {}=1 of the scrubbed "
                            "families".format(sorted(got_env.items()), REQUIRE_SIBLINGS_VAR))

        # Cases that never reach git (always run).
        # 1. absent directory -> NOT APPLICABLE, exit 0.
        (base / "absent").mkdir()
        case("absent hooks-preview/", 0, base / "absent")

        # 2. zero hooks: an empty table and a comment-only SHA256SUMS -> exit 0.
        case("zero-hook channel", 0, _build(base / "zero", {}))

        # 3. (b) a hook file present but unlisted -> exit 1.
        case("unlisted hook file", 1, _build(base / "unlisted", ok, rows=[], sums=[]))

        # 4. (b) an unexpected extra entry in the directory -> exit 1.
        r = _build(base / "extra-entry", {})
        (r / PREVIEW_DIR / "notes.txt").write_text("x\n", encoding="utf-8")
        case("unexpected channel entry", 1, r)

        # 5. malformed inputs -> exit 2: a missing SHA256SUMS, a missing table, a malformed row and line.
        case("missing SHA256SUMS", 2, _build(base / "no-sums", {}, sums=False))
        case("missing integrity table", 2, _build(base / "no-table", {}, readme="# Hooks preview\n"))
        r = _build(base / "bad-row", ok)
        text = (r / PREVIEW_DIR / README_NAME).read_text(encoding="utf-8")
        (r / PREVIEW_DIR / README_NAME).write_text(
            text.replace("| `clock-inject.py` |", "| clock-inject.py |"), encoding="utf-8")
        case("malformed table row", 2, r)
        r = _build(base / "bad-sums", ok)
        (r / PREVIEW_DIR / SUMS_NAME).write_text("{} *clock-inject.py\n".format(ok_hex), encoding="utf-8")
        case("malformed SHA256SUMS line", 2, r)

        # 6. (c) a link that names a different file -> exit 1.
        case("link names another file", 1, _build(
            base / "link-file", ok,
            rows=[("clock-inject.py", ok_hex, _LINK.format(tag="hooks-preview-v1", name="other.py"))]))

        # 7. (c) a link pinned to a branch, not a hooks-preview-v<N> tag -> exit 1.
        case("link pinned to a branch", 1, _build(
            base / "link-ref", ok,
            rows=[("clock-inject.py", ok_hex, _LINK.format(tag="main", name="clock-inject.py"))]))

        # 8. (c) a link that is not a raw link at all -> exit 1.
        case("non-raw link", 1, _build(
            base / "link-shape", ok, rows=[("clock-inject.py", ok_hex, "https://example.com/clock-inject.py")]))

        # 9. (a) no scratch directory can be created for a hook self-test -> exit 2 with a bounded
        #    diagnostic, never a raw OSError traceback. The empty table keeps the case off git.
        r = _build(base / "no-scratch", ok, rows=[], sums=[])
        saved_mkdtemp = tempfile.mkdtemp
        tempfile.mkdtemp = _no_scratch
        try:
            case("no scratch directory for a hook self-test", 2, r, needle="cannot create a scratch directory")
        finally:
            tempfile.mkdtemp = saved_mkdtemp

        # 10. (b) a SHA256SUMS entry that names a path (absolute or relative), not a bare file name -> exit 1.
        for label, listed in (("absolute", "/tmp/clock-inject.py"), ("relative", "sub/clock-inject.py")):
            case("SHA256SUMS entry is a path ({})".format(label), 1, _build(
                base / "sums-path-{}".format(label), ok, rows=[], sums=[(listed, ok_hex)]),
                needle="a path, not a bare file name")

        # Cases whose rows carry a well-formed tag reach git, so each runs in its own fixture repository.
        if git_ran:
            # 11. a clean one-hook tree whose tag does not resolve -> exit 0 with a note.
            case("clean tree, tag unresolved", 0, _build(repo("clean-untagged"), ok))

            # 12. (a) a failing hook self-test -> exit 1.
            case("failing hook self-test", 1, _build(repo("selftest-fail"), {"clock-inject.py": _STUB_FAIL}))

            # 13. (a) a self-test that outlives the deadline -> exit 2 (no verdict delivered).
            r = _build(repo("selftest-slow"), {"clock-inject.py": _STUB_SLOW})
            saved = SELFTEST_TIMEOUT
            SELFTEST_TIMEOUT = 1
            try:
                case("hook self-test past the deadline", 2, r)
            finally:
                SELFTEST_TIMEOUT = saved

            # 14. (b) a corrupted hash, README and SHA256SUMS agreeing with each other -> exit 1.
            bad = "0" * 64
            case("corrupted hash", 1, _build(repo("bad-hash"), ok, rows=[("clock-inject.py", bad, link_v1)],
                                             sums=[("clock-inject.py", bad)]))

            # 15. (b) README and SHA256SUMS disagree -> exit 1.
            case("README and SHA256SUMS disagree", 1, _build(repo("disagree"), ok,
                                                               sums=[("clock-inject.py", bad)]))

            # 16. (b) README lists a file but SHA256SUMS lists nothing -> exit 1.
            case("README lists files, SHA256SUMS empty", 1, _build(repo("sums-empty"), ok, sums=[]))

            # 17. (b) a listed file that is absent -> exit 1.
            case("listed but absent", 1, _build(repo("listed-absent"), {},
                                                 rows=[("clock-inject.py", ok_hex, link_v1)],
                                                 sums=[("clock-inject.py", ok_hex)]))

            # 18. (b) an alien file name in the table -> exit 1.
            alien = "README.md"
            case("alien file name in the table", 1, _build(
                repo("alien"), {}, rows=[(alien, ok_hex, _LINK.format(tag="hooks-preview-v1", name=alien))],
                sums=[(alien, ok_hex)]))

            # 19. (c) the tag resolves and its blob matches -> exit 0.
            r = _build(repo("tag-match"), ok)
            _commit_and_tag(r, "hooks-preview-v1")
            case("tag resolves and matches", 0, r)

            # 20. (c) the tag resolves but the tree has moved on (README and SUMS updated, tag not) -> exit 1.
            r = _build(repo("tag-stale"), ok)
            _commit_and_tag(r, "hooks-preview-v1")
            _build(r, {"clock-inject.py": _STUB_OK + b"# revised\n"})
            case("tag resolves to a different blob", 1, r)

            # 21. (c) the tag resolves but lacks the file -> exit 1.
            r = repo("tag-lacks")
            (r / "other").mkdir()
            (r / "other" / "x").write_text("x\n", encoding="utf-8")
            _commit_and_tag(r, "hooks-preview-v1")
            _build(r, ok)
            case("tag lacks the file", 1, r)

            # 22. (c) a well-formed tag in a tree git cannot read as a repository -> exit 2 (the pin leg
            #     cannot evaluate whether the tag resolves). The .git file points at a missing gitdir.
            r = _build(base / "no-repo", ok)
            (r / ".git").write_text("gitdir: {}\n".format(base / "missing-gitdir"), encoding="utf-8")
            case("pin leg outside a readable repository", 2, r)

            # 23. (c) the tag resolves but git cannot read the tree under it (its hooks-preview/ tree object
            #     is removed) -> exit 2, a cannot-evaluate, never collapsed into a missing-blob finding.
            r = _build(repo("tag-unreadable"), ok)
            _commit_and_tag(r, "hooks-preview-v1")
            oid = _fixture_git(r, "rev-parse", "hooks-preview-v1:" + PREVIEW_DIR).stdout.decode("ascii").strip()
            (r / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
            case("tag resolves but its tree is unreadable", 2, r, needle="cannot list")

            # 23b. (c) the tag ref exists but its commit object is gone -> exit 2, never read as an absent
            #      tag (which would skip the leg with a note).
            r = _build(repo("tag-commit-missing"), ok)
            _commit_and_tag(r, "hooks-preview-v1")
            oid = _fixture_git(r, "rev-parse", "hooks-preview-v1^{commit}").stdout.decode("ascii").strip()
            (r / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
            case("tag ref exists but its commit is unreadable", 2, r, needle="does not resolve to a readable commit")

            # 23c. (a) the hook self-test sees REQUIRE_SIBLINGS_VAR=1 and no other scrubbed-family variable,
            #      even when the gate's own environment carries them -> exit 0.
            r = _build(repo("selftest-env"), {"clock-inject.py": _STUB_ENV})
            saved_env = _seed_env()
            try:
                case("hook self-test environment", 0, r)
            finally:
                _restore_env(saved_env)

            # 23d. a README with CRLF line endings (GitHub splits there too) parses as the LF form -> exit 0.
            r = _build(repo("readme-crlf"), ok)
            text = (r / PREVIEW_DIR / README_NAME).read_text(encoding="utf-8")
            (r / PREVIEW_DIR / README_NAME).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            case("README with CRLF line endings", 0, r)

            # 23e. (b) a table line written without a leading `|` (GitHub still renders it as a row) -> exit 2.
            r = _build(repo("pipeless-row"), ok)
            text = (r / PREVIEW_DIR / README_NAME).read_text(encoding="utf-8")
            phantom = "`missing.py` | `{}` | {}".format(
                "a" * 64, _LINK.format(tag="hooks-preview-v1", name="missing.py"))
            row_end = text.index("\n", text.index("| `clock-inject.py` |"))
            (r / PREVIEW_DIR / README_NAME).write_text(
                text[:row_end + 1] + phantom + " |\n" + text[row_end + 1:], encoding="utf-8")
            case("table row without a leading pipe", 2, r, needle="does not start with `|`")

            # 23f. a line separator the reader does not honour -> exit 2: each one in SHA256SUMS, and U+2028
            #      joining a phantom row onto a README table row (one line to GitHub, two to str.splitlines()).
            for ch in SUMS_BREAK_CHARS:
                r = _build(repo("sums-break-{:04x}".format(ord(ch))), ok)
                sums_text = _sums([("clock-inject.py", ok_hex)])
                (r / PREVIEW_DIR / SUMS_NAME).write_bytes(sums_text.replace("\n", ch, 1).encode("utf-8"))
                case("SHA256SUMS line separator U+{:04X}".format(ord(ch)), 2, r, needle="is not a line break")
            r = _build(repo("readme-break"), ok)
            text = (r / PREVIEW_DIR / README_NAME).read_text(encoding="utf-8")
            row_end = text.index("\n", text.index("| `clock-inject.py` |"))
            (r / PREVIEW_DIR / README_NAME).write_text(
                text[:row_end] + "\u2028| " + phantom + " |" + text[row_end:], encoding="utf-8")
            case("README line separator U+2028", 2, r, needle="is not a line break")

            # 23g. (c) a link whose ref is a bare tag or a branch, not refs/tags/<tag> -> exit 1.
            for label, ref in (("bare tag", "hooks-preview-v1"), ("refs/heads", "refs/heads/hooks-preview-v1")):
                case("link pinned by {}".format(label), 1, _build(
                    repo("link-{}".format(label.replace(" ", "-").replace("/", "-"))), ok,
                    rows=[("clock-inject.py", ok_hex, _BARE_LINK.format(ref=ref, name="clock-inject.py"))]),
                    needle="use refs/tags/<tag>")

            # 24. the self-test itself: with no git binary on PATH the git-backed cases are skipped, so the
            #     self-test must exit 2 and name what it skipped, never report a pass.
            ran.append("self-test without git")
            empty_path = base / "empty-path"
            empty_path.mkdir()
            env = _selftest_env()
            env["PATH"] = str(empty_path)
            child = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--self-test"],
                                   cwd=str(base), capture_output=True, timeout=SELFTEST_TIMEOUT, env=env,
                                   stdin=subprocess.DEVNULL)
            if child.returncode != 2 or b"SKIPPED" not in child.stdout + child.stderr:
                failures.append("self-test without git: expected exit 2 naming the SKIPPED cases, got exit {}: "
                                "{}".format(child.returncode, _bounded(child.stdout + child.stderr)))
        else:
            skipped = ["clean tree, tag unresolved", "failing hook self-test", "hook self-test past the deadline",
                       "corrupted hash", "README and SHA256SUMS disagree", "README lists files, SHA256SUMS empty",
                       "listed but absent", "alien file name in the table", "tag resolves and matches",
                       "tag resolves to a different blob", "tag lacks the file",
                       "pin leg outside a readable repository", "tag resolves but its tree is unreadable",
                       "tag ref exists but its commit is unreadable", "hook self-test environment",
                       "README with CRLF line endings", "table row without a leading pipe"]
            skipped += ["SHA256SUMS line separator U+{:04X}".format(ord(ch)) for ch in SUMS_BREAK_CHARS]
            skipped += ["README line separator U+2028", "link pinned by bare tag", "link pinned by refs/heads",
                        "self-test without git"]
    except (OSError, subprocess.SubprocessError) as exc:
        failures.append("fixture construction failed: {}".format(exc))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    if skipped:
        print("SELF-TEST INCOMPLETE: {} case(s) ran and held, but {} mandatory git-backed case(s) were SKIPPED "
              "(no git binary), so those invariants are UNVERIFIED and the self-test does not pass: {}".format(
                  len(ran), len(skipped), "; ".join(skipped)))
        return 2
    print("SELF-TEST PASS: {} case(s): the NOT APPLICABLE path, the zero-hook channel, and each leg's seeded "
          "fault (a: failing and timed-out self-tests, no scratch directory, the scrubbed environment with "
          "{}=1; b: corrupted hash, disagreement, empty listing, unlisted, absent, alien and extra entries, "
          "path entries in SHA256SUMS, malformed inputs, a table row without a leading pipe, line separators "
          "the reader does not honour, a CRLF README; c: wrong file, non-tag ref, bare-tag and refs/heads "
          "links, non-raw link, matching, stale and missing blob at a resolving tag, unreadable tree at a "
          "resolving tag, an existing tag ref with an unreadable commit, unreadable repository; the "
          "self-test refusing to pass without git) all hold".format(len(ran), REQUIRE_SIBLINGS_VAR))
    return 0


def main():
    argv = sys.argv[1:]
    unknown = [a for a in argv if a != "--self-test"]
    if unknown:
        print("usage: check_hooks_preview.py [--self-test]; unrecognized argument(s) {}; fail-closed".format(
            " ".join(unknown)), file=sys.stderr)
        return 2
    if "--self-test" in argv:
        return self_test_main()
    return run(_repo_root())


if __name__ == "__main__":
    sys.exit(main())
