#!/usr/bin/env python3
"""Mistakes-register gate (GD-112 component 6, rules mstreg/secaud): the register is APPEND-ONLY with
permanent ids and a digest chain, and a landed row's claims are verified against the repository.

Row schema (one JSON object per line):
  {"seq": N, "id": "MR-N", "ts": "<iso-utc>", "mistake": "...", "evidence": "...", "rule": "...",
   "guardrail": "...", "status": "proposed|accepted|landed|declined|superseded",
   "prev": "<sha256 of the previous raw line, 64*'0' for the first>",
   ...landed rows also: "commit": "<sha>", "check_ref": "<repo-relative path>",
   ...optional: "class": "<row classifier, e.g. systemic-lapse>", "ref": "<the blocker ref an
   attestation row covers>"}
A status change is a NEW row re-naming an earlier id at a higher seq; nothing is ever edited in place.
A registry-declared `attestations` register (GD-127 C.2) is verified by this same gate under the
AT- id prefix; the id prefix is per-register, MR- for the mistakes register.

Append-only enforcement, two modes: a git-TRACKED register is checked as an exact line-prefix of its
merge-base state with origin/HEAD (so the protected-branch reference must be resolvable: on a fresh
clone or CI checkout run `git remote set-head origin -a`); an UNTRACKED register is checked against a
persisted anchor file (<register>.anchor: the last verified seq and line digest), which only
--update-anchor moves forward on a green run (and establishes the first anchor). An EMPTY register
has no tip to anchor: --update-anchor inspects the anchor entry first (an unusable entry still
refuses), writes nothing and reports no advance. RESIDUAL: concurrent --update-anchor runs are not
serialized against each other; each advance is atomic, but the last replace wins, so a slower green
run can move the anchor back to an older verified tip. A stale anchor temporary left by an
interrupted run AT THE CURRENT PROCESS ID'S PATHNAME blocks the advance (exclusive creation) until it
is removed by hand; a temporary under another process id blocks nothing and is left in place. A failed
advance removes its temporary and says so, and a cleanup that itself fails is reported with the
temporary's path beside the write failure, never swallowed. The anchor write
never opens an existing entry: a non-regular anchor entry (a symlink, dangling or live, included)
is refused as cannot-evaluate with its target neither created nor rewritten, and a green advance
lands by atomic os.replace of an exclusively created temporary in the anchor's directory. Fail-closed: a
DECLARED register that is absent or unreadable, a broken chain or sequence, a reused id, an illegal
status transition, or a landed row whose commit or repo-relative check_ref does not exist is a
violation (exit 1); and an append-only AUTHORITY that cannot be read (no computable merge-base, a git
failure deciding whether the register is tracked, a malformed or unreadable anchor, an anchor
entry that exists but is unusable (anchor presence is an explicit lstat whose only absence answer
is ENOENT: a dangling symlink, a non-regular file, or an lstat failure such as EIO is never read
as a missing anchor; the anchor is then read through one non-blocking open that never follows a
final symlink and whose descriptor must be a regular file, so a live symlink never supplies the
anchor), or a declared untracked register with no anchor) is a distinct cannot-evaluate
failure (exit 2), never a
silent skip nor a HEAD-against-itself self-comparison (chkfcl/grdinp). The LIVE leg resolves its
validation root from this gate's own installed location (gate_root: the nearest ancestor holding a
.git entry of ANY type, inspected through entry_state, whose only absence answer is ENOENT) and
requires git itself to confirm that root as a repository toplevel (confirmed_root: rev-parse
--show-toplevel, realpath-compared) BEFORE any registry or register is read: an unresolvable,
uninspectable or unconfirmed root is cannot-evaluate (exit 2), never a cwd fallback and never a
trusted marker. Every PRODUCTION git call this gate makes (the root probe and the authority reads:
ls-files, merge-base, show, cat-file) runs under git_authority_env(), with EVERY GIT_-prefixed
environment variable removed (GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR, GIT_INDEX_FILE,
GIT_OBJECT_DIRECTORY, GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_CEILING_DIRECTORIES,
GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_CONFIG*, GIT_NAMESPACE included), so a caller-supplied git
environment never redirects discovery or an authority read, and ONLY the config-ISOLATION pins
re-applied, each at the single subtractive value the self-test scrub sets (GIT_CONFIG_NOSYSTEM at
"1", GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at os.devnull), so a scrubbed self-test's authority
reads keep reading NO system or global git configuration while a pin at any other value stays
removed as DEFENCE IN DEPTH (a caller-valued pin names a config file of the caller's choosing;
HOME and XDG_CONFIG_HOME stay, so a caller who controls the environment can already name a
global config file, and the drop narrows that surface rather than closing a boundary); HOME and
the on-disk git configuration are kept (the live reads need the caller's safe.directory
trust). The self-test's own FIXTURE SETUP git calls (init, add, commit) run under the scrubbed
fixture environment, which carries the same config-isolation pins, not under
git_authority_env(). With a confirmed root but no
registry, or no declared
register path, the live leg is NOT APPLICABLE and the self-test carries the assurance in CI; registry
PRESENCE is an explicit lstat whose only absence answer is ENOENT (a dangling or uninspectable
registry entry is cannot-evaluate, never NOT APPLICABLE and never a silent fall-through to the
lower-priority registry file), a registry that is PRESENT but not a JSON object is cannot-evaluate
(exit 2), never NOT APPLICABLE, and a declared register value that is not a non-empty string is
cannot-evaluate (exit 2), never a skipped surface.
  check_mistakes_register.py                   run the live leg
  check_mistakes_register.py --update-anchor   verify, then advance the anchor on green
  check_mistakes_register.py --self-test       synthetic fixtures for every invariant
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_mistakes_register.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import errno
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

REGISTRY_FILES = (".aiqt/orchestration.local.json", ".aiqt/orchestration.json")
# The anchor read's open, the one tools/check_entry_guard.py uses for its control inputs (that
# gate is not imported: this gate installs standalone beside the appender). Non-blocking, so a
# FIFO opens at once and is then refused by type; no final-symlink follow, so a symlink, live or
# dangling, is refused at open (ELOOP); close-on-exec, so no launched child inherits it.
READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
READ_CHUNK = 1 << 16
STATUSES = ("proposed", "accepted", "landed", "declined", "superseded")
ZERO = "0" * 64
TRANSITIONS = {"proposed": {"accepted", "declined", "superseded"},
               "accepted": {"landed", "declined", "superseded"},
               "landed": {"superseded"}, "declined": set(), "superseded": set()}


class CannotEvaluate(RuntimeError):
    """The append-only authority itself could not be read (a distinct cannot-evaluate case, exit 2),
    kept separate from a register-content violation (exit 1) so the guard never self-certifies against
    an authority it could not compute (chkfcl/grdinp)."""


class _DuplicateKey(ValueError):
    """A register row carried the same key twice: json.loads would silently keep the last value, so
    a reader and this gate could disagree on the row (for example two status fields)."""


def _pairs_without_duplicates(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise _DuplicateKey(key)
        seen.add(key)
    return dict(pairs)


def git_authority_env():
    """A copy of the CURRENT process environment with every GIT_-prefixed variable removed and
    ONLY the config-ISOLATION pins re-applied, for every PRODUCTION git call this gate and
    the appender make (the root-confirmation probe and the authority reads: ls-files,
    merge-base, show, cat-file, rev-parse); the self-tests' fixture setup calls instead
    inherit the scrubbed fixture environment, which carries the same pins. A
    caller-supplied GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR,
    GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_ALTERNATE_OBJECT_DIRECTORIES,
    GIT_CEILING_DIRECTORIES, GIT_DISCOVERY_ACROSS_FILESYSTEM, GIT_CONFIG* or GIT_NAMESPACE
    would redirect git's repository discovery or its object, ref and index reads to a
    repository of the caller's choosing (git exports several of these to hook children; an
    environment-supplied config source, GIT_CONFIG_PARAMETERS or the GIT_CONFIG_COUNT/KEY/VALUE
    family included, can carry configuration such as safe.directory), so an unscrubbed
    probe could confirm a root
    that is only a marker and an unscrubbed authority read could consult the wrong repository.
    The drop is the allowlist stance the pack's other scrubs take: EVERY GIT_-prefixed
    variable, not an enumerated family. THREE pins are then re-applied, each ONLY at the exact
    value the self-test scrub (_git_fixture_env) sets, because at that value each is purely
    SUBTRACTIVE: it removes a configuration source while naming no repository, object store,
    index, worktree or configuration content, so it cannot redirect discovery or an authority
    read, and its worst effect is a lost trust entry, on which git fails and this gate refuses
    (fail closed). The pins are GIT_CONFIG_NOSYSTEM at "1" (skip the system config), and
    GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at os.devnull (an EMPTY config in place of the
    global and system files); a pin at ANY other value is dropped with the rest, as DEFENCE
    IN DEPTH rather than a boundary (HOME and XDG_CONFIG_HOME are KEPT, so a caller who
    controls the environment can already name a global config file; the drop narrows that
    surface). This keeps a
    scrubbed self-test's config isolation intact across the probe and the authority reads (the
    fixture-environment suite's system lane plants a system git config and fails a member
    whose git call sheds the pins). HOME, XDG_CONFIG_HOME and the on-disk git configuration
    are KEPT, because the live gate reads the real repository and needs the caller's trust
    configuration (safe.directory)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if os.environ.get("GIT_CONFIG_NOSYSTEM") == "1":
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    for pin in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        if os.environ.get(pin) == os.devnull:
            env[pin] = os.devnull
    return env


def entry_state(path):
    """The ONE entry-state read behind every absence test in this gate and the appender (registry
    and anchor presence, the .git marker walk, the mkdir ancestor walk, the landed check_ref, and
    the self-tests' own absence assertions): an explicit os.lstat whose only absence answer is
    ENOENT. Returns the lstat result when an entry of ANY type is present (a dangling symlink
    included: presence is the entry, not its target), None only on confirmed absence
    (FileNotFoundError), and raises CannotEvaluate naming the path and the error for any other
    failure (EIO, EACCES, ENOTDIR, ...): an uninspectable entry is never read as absent."""
    try:
        return os.lstat(str(path))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CannotEvaluate("the entry at {} cannot be inspected ({}); only confirmed absence "
                             "(ENOENT) reads as absent".format(path, exc))


def _read_regular(path, label):
    """Read one authority file as bytes: opened ONCE with READ_FLAGS, refused unless the OPENED
    descriptor is a regular file (fstat), and read from that same descriptor, so an entry
    swapped after an earlier inspection is judged as it was opened and a FIFO never blocks the
    gate. Every failure raises CannotEvaluate naming label and path: a symlink, live or
    dangling (ELOOP), an entry that cannot be opened (one gone since its inspection included),
    a directory, a FIFO or any other non-regular file, and a read error."""
    try:
        fd = os.open(str(path), READ_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CannotEvaluate("{} at {} is a symlink, which is never followed".format(
                label, path))
        raise CannotEvaluate("{} at {} cannot be opened ({})".format(label, path, exc))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CannotEvaluate("{} at {} is not a regular file".format(label, path))
        chunks = []
        while True:
            chunk = os.read(fd, READ_CHUNK)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError as exc:
        raise CannotEvaluate("{} at {} cannot be read ({})".format(label, path, exc))
    finally:
        os.close(fd)


def gate_root(start):
    """The validation-root CANDIDATE shared by this gate's live leg and the appender: the nearest
    ancestor of START (the installed tool's own directory) holding a .git ENTRY of ANY type,
    selected through entry_state, so ENOENT is the only absence (a dangling .git symlink is
    SELECTED, never walked past, and left for confirmed_root to refuse) and any other inspection
    failure raises CannotEvaluate, never reads as absence and never walks past the marker.
    Returns None when no ancestor holds a .git entry. The candidate is only a MARKER until
    confirmed_root accepts it."""
    start = Path(start)
    for anc in [start, *start.parents]:
        if entry_state(anc / ".git") is not None:
            return anc
    return None


def confirmed_root(root):
    """True only when git itself, run under git_authority_env() (a caller-supplied GIT_DIR or
    GIT_WORK_TREE could otherwise make git confirm an arbitrary directory), confirms ROOT as a
    repository toplevel: rev-parse --show-toplevel must succeed AND name ROOT itself
    (realpath-compared, so a discovery that walked past the candidate's marker to an OUTER
    repository, past an empty nested .git directory for example, is refused). A bare .git entry
    (an invalid gitfile, an empty .git directory, a dangling .git symlink) is a repository
    MARKER, not a repository. A valid linked worktree passes: git reports the worktree's own
    toplevel."""
    try:
        probe = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=10,
                               env=git_authority_env())
    except (OSError, subprocess.SubprocessError):
        return False
    if probe.returncode != 0 or not probe.stdout.strip():
        return False
    try:
        return os.path.realpath(probe.stdout.splitlines()[0]) == os.path.realpath(str(root))
    except OSError:
        return False


def verify_lines(lines, root=None, prefix="MR-"):
    """Return a list of violation strings for the raw register lines (empty = clean). prefix is the
    register's id family (MR- for the mistakes register, AT- for an attestations register). The
    landed-row commit read runs git under git_authority_env(), and a launch error or timeout of
    that read raises CannotEvaluate (never read as a missing commit); a landed check_ref whose
    entry inspection fails with anything but ENOENT raises CannotEvaluate through entry_state (an
    uninspectable check_ref is never read as absent or present)."""
    problems, prev_line, ids_born, last_status = [], None, {}, {}
    for n, line in enumerate(lines, 1):
        if not line.strip():
            problems.append("line {}: blank line inside the register".format(n))
            continue
        try:
            row = json.loads(line, object_pairs_hook=_pairs_without_duplicates)
        except _DuplicateKey as exc:
            problems.append("line {}: duplicated key {!r} in the row (the parser would silently "
                            "keep the last value)".format(n, exc.args[0]))
            continue
        except ValueError:
            problems.append("line {}: not a JSON object".format(n))
            continue
        if not isinstance(row, dict):
            problems.append("line {}: not a JSON object".format(n))
            continue
        want_prev = ZERO if prev_line is None else hashlib.sha256(
            prev_line.encode("utf-8")).hexdigest()
        if row.get("prev") != want_prev:
            problems.append("line {}: digest chain broken".format(n))
        seq = row.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != n:
            problems.append("line {}: seq {} is not the line number".format(n, seq))
        rid = row.get("id")
        status = row.get("status")
        if status not in STATUSES:
            problems.append("line {}: unknown status {!r}".format(n, status))
        elif not isinstance(rid, str) or not rid.startswith(prefix):
            problems.append("line {}: id {!r} is not an {} id".format(n, rid, prefix))
        elif rid in ids_born and status == "proposed":
            problems.append("line {}: id {} reused as a new proposal".format(n, rid))
        elif rid in ids_born:
            if status not in TRANSITIONS.get(last_status[rid], set()):
                problems.append("line {}: illegal status transition {} -> {} on {}".format(
                    n, last_status[rid], status, rid))
            last_status[rid] = status
        else:
            if status != "proposed":
                problems.append("line {}: first row for {} must be proposed".format(n, rid))
            ids_born[rid] = n
            last_status[rid] = status
        for key in ("ts", "mistake", "evidence", "rule", "guardrail"):
            if not isinstance(row.get(key), str) or not row.get(key):
                problems.append("line {}: missing field {}".format(n, key))
        if status == "landed" and root is not None:
            commit, check_ref, check_ok = row.get("commit"), row.get("check_ref"), False
            commit_ok = False
            if isinstance(commit, str):
                # an authority read like the others: a git that cannot be launched or that
                # times out leaves the question open (cannot-evaluate), never a missing commit
                try:
                    probe = subprocess.run(
                        ["git", "-C", str(root), "cat-file", "-e", commit + "^{commit}"],
                        capture_output=True, timeout=10, env=git_authority_env())
                except (OSError, subprocess.SubprocessError) as exc:
                    raise CannotEvaluate("cannot read whether the landed row on line {} cites an "
                                         "existing commit ({})".format(n, exc))
                commit_ok = probe.returncode == 0
            if not commit_ok:
                problems.append("line {}: landed row cites a commit that does not exist".format(n))
            # check_ref is a REPO-RELATIVE path (schema); an absolute value would discard root and let
            # any host file satisfy the check, so reject it and confirm containment under root (grdinp).
            if isinstance(check_ref, str) and check_ref and not Path(check_ref).is_absolute():
                try:
                    check_root = Path(root).resolve()
                    check_path = (check_root / check_ref).resolve()
                    check_path.relative_to(check_root)
                    check_ok = entry_state(check_path) is not None
                except (OSError, ValueError):
                    check_ok = False
            if not check_ok:
                problems.append("line {}: landed row cites check_ref {!r} which is not an existing "
                                "repo-relative path".format(n, check_ref))
        prev_line = line
    return problems


def verify_append_only(root, path, lines, allow_missing_anchor=False):
    """Tracked register: exact full-line prefix of the merge-base state. Untracked: the anchor file.
    Returns a list of violations. Raises CannotEvaluate when the append-only AUTHORITY itself cannot be
    read: a git failure deciding whether the register is tracked (any outcome other than git's
    confirmed tracked exit 0 or not-tracked exit 1); tracked: no computable merge-base with
    origin/HEAD, or its historical file unreadable; untracked: a malformed or unreadable anchor, or a
    declared register present with no anchor and allow_missing_anchor False. So the guard never falls
    back to comparing a possibly-tampered HEAD against itself, and a broken git never routes a tracked
    register into the anchor leg. allow_missing_anchor is set only by the --update-anchor bootstrap
    that establishes the first anchor (it covers any unanchored untracked register, not only an empty
    one, because a second append must still land before the first anchor is set) and by the
    appender's --bootstrap, which appends a register's first row and then establishes the anchor
    the same way; an ordinary append passes False, as this gate does without --update-anchor.
    Every git read
    here runs under git_authority_env(), so a caller-supplied git environment never redirects the
    tracked decision or the authority reads, and anchor PRESENCE goes through the shared
    entry_state (ENOENT-only absence)."""
    rel = None
    try:
        rel = str(path.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        pass
    if rel:
        # The tracked/untracked DECISION is itself an authority read: git confirms "not tracked"
        # only with exit 1; any other failure (a corrupt index: exit 128, an unrunnable git, a
        # timeout) leaves the question open and is cannot-evaluate, never a fall-through into the
        # untracked anchor leg and its missing-anchor allowance (chkfcl).
        try:
            probe = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", rel],
                                   capture_output=True, timeout=10,
                                   env=git_authority_env())
        except (OSError, subprocess.SubprocessError) as exc:
            raise CannotEvaluate("cannot decide whether the register is tracked ({})".format(exc))
        if probe.returncode not in (0, 1):
            raise CannotEvaluate("cannot decide whether the register is tracked (git ls-files "
                                 "exited {}: {})".format(
                                     probe.returncode,
                                     probe.stderr.decode("utf-8", "replace").strip()))
        if probe.returncode == 0:
            try:
                base = subprocess.run(["git", "-C", str(root), "merge-base", "HEAD",
                                       "origin/HEAD"], capture_output=True, text=True,
                                      timeout=10, env=git_authority_env())
            except (OSError, subprocess.SubprocessError) as exc:
                raise CannotEvaluate("cannot compute the register authority from origin/HEAD: "
                                     "{}".format(exc))
            if base.returncode != 0 or not base.stdout.strip():
                raise CannotEvaluate("cannot compute the register authority from origin/HEAD: {}"
                                     .format(base.stderr.strip() or "no merge-base"))
            ref = base.stdout.strip()
            try:
                old = subprocess.run(["git", "-C", str(root), "show", "{}:{}".format(ref, rel)],
                                     capture_output=True, text=True, timeout=10,
                                     env=git_authority_env())
            except (OSError, subprocess.SubprocessError) as exc:
                raise CannotEvaluate("cannot read the register at its merge-base authority: "
                                     "{}".format(exc))
            if old.returncode != 0:
                raise CannotEvaluate("cannot read the register at its merge-base authority: {}"
                                     .format(old.stderr.strip() or "git show failed"))
            old_lines = old.stdout.splitlines()
            if lines[:len(old_lines)] != old_lines:
                return ["the register is not an append-only extension of its merge-base state"]
            return []
    anchor = Path(str(path) + ".anchor")
    # Anchor PRESENCE is an EXPLICIT lstat whose only absence answer is ENOENT: the
    # missing-anchor allowance covers only an anchor path with NO entry at all. An lstat failure
    # other than ENOENT (EIO, EACCES, ENOTDIR, ...) leaves the presence question OPEN, and an
    # entry that exists but cannot be used (a symlink, live or dangling, a directory, a FIFO or
    # any other non-regular file) is an authority that cannot be read: each is cannot-evaluate,
    # never the allowance.
    try:
        anchor_entry = entry_state(anchor)
    except CannotEvaluate as exc:
        raise CannotEvaluate("the anchor entry cannot be inspected ({}); an inspection failure "
                             "that is not ENOENT is never the missing-anchor "
                             "allowance".format(exc))
    if anchor_entry is None:
        if not allow_missing_anchor:
            raise CannotEvaluate("the declared untracked register has no anchor; run "
                                 "--update-anchor to establish the baseline (an absent authority "
                                 "is not a clean result)")
        return []
    # The present entry is then opened ONCE without following a final symlink and read from
    # that same descriptor (_read_regular), so a link, live or dangling, never supplies the
    # authority from a file it names, and an entry swapped after the inspection above is
    # judged as it was opened, never followed and never a blocked read.
    try:
        raw = _read_regular(anchor, "the anchor entry")
    except CannotEvaluate as exc:
        raise CannotEvaluate("{}; an anchor entry that exists but cannot be used is never the "
                             "missing-anchor allowance".format(exc))
    # The anchor is the append-only AUTHORITY: one that cannot be read, or that does not hold
    # the declared shape (an object with an integer seq >= 1, bools excluded, and a 64-hex
    # digest), is cannot-evaluate, never a violation list and never a silently skipped digest
    # comparison (a zero, negative or false seq used to bypass the anchored-row check; a true
    # seq was coerced to 1 and still compared).
    try:
        a = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise CannotEvaluate("the anchor file cannot be read as JSON ({})".format(exc))
    n = a.get("seq") if isinstance(a, dict) else None
    digest = a.get("digest") if isinstance(a, dict) else None
    if (not isinstance(n, int) or isinstance(n, bool) or n < 1
            or not isinstance(digest, str) or len(digest) != 64
            or set(digest) - set("0123456789abcdef")):
        raise CannotEvaluate("the anchor file is malformed (an anchor is an object with an "
                             "integer seq >= 1 and a 64-hex digest)")
    if len(lines) < n:
        return ["the register is shorter than its verified anchor (rows removed)"]
    if hashlib.sha256(lines[n - 1].encode("utf-8")).hexdigest() != digest:
        return ["the anchored row was rewritten (tamper or reorder before seq {})".format(n)]
    return []


def update_anchor(path, lines):
    """Advance the anchor to the verified tip on a green run; returns True when the anchor
    advanced and False when the register is EMPTY (no tip to anchor: the anchor shape requires
    seq >= 1, so nothing is written and no advance is reported). The anchor ENTRY is inspected
    BEFORE the empty-register return, so an unusable entry refuses even for an empty register.
    The anchor write NEVER opens an existing entry: a non-regular entry at the anchor path (a
    symlink, dangling or live, included) is refused as CannotEvaluate, and the payload lands by
    atomic os.replace of an exclusively created temporary in the same directory (O_NOFOLLOW
    included), so no link is ever followed and no link target is ever created or rewritten; a
    failed write, short write, close or replace removes the temporary, leaves the previous
    anchor in place, and the refusal SAYS what happened to the temporary; a cleanup that itself
    fails is reported with the temporary's path beside the write failure, never swallowed. A
    STALE temporary refuses the advance only at the CURRENT process id's pathname (the
    exclusive creation's own name); a temporary left under another process id blocks nothing
    and is left in place, and a creation failure that is not EEXIST names the actual error
    instead of blaming a stale temporary. The entry inspection goes through the shared
    entry_state (ENOENT-only absence). Residuals: an entry planted between the inspection and
    the replace is REPLACED as a whole entry, never followed; concurrent --update-anchor runs
    are not serialized against each other (the last replace wins)."""
    anchor = Path(str(path) + ".anchor")
    try:
        entry = entry_state(anchor)
    except CannotEvaluate as exc:
        raise CannotEvaluate("the anchor entry cannot be inspected before the anchor write "
                             "({})".format(exc))
    if entry is not None and not stat.S_ISREG(entry.st_mode):
        raise CannotEvaluate("the anchor entry is not a regular file; the anchor write never "
                             "follows a link (move the entry aside before --update-anchor)")
    if not lines:
        return False
    payload = json.dumps({"seq": len(lines), "digest": hashlib.sha256(
        lines[-1].encode("utf-8")).hexdigest()}).encode("utf-8")
    tmp_path = "{}.tmp.{}".format(anchor, os.getpid())
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError as exc:
        raise CannotEvaluate("the anchor temporary cannot be created ({}); a stale temporary "
                             "at this process id's pathname ({}), left by an interrupted "
                             "earlier run, blocks the advance and must be removed by hand "
                             "before the anchor can advance".format(exc, tmp_path))
    except OSError as exc:
        raise CannotEvaluate("the anchor temporary at {} cannot be created "
                             "({})".format(tmp_path, exc))
    problem = None
    try:
        try:
            written = os.write(fd, payload)
        finally:
            os.close(fd)
        if written != len(payload):
            problem = "short write to the anchor temporary ({} of {} byte(s))".format(
                written, len(payload))
        else:
            os.replace(tmp_path, str(anchor))
    except OSError as exc:
        problem = "the anchor could not be written atomically ({})".format(exc)
    if problem is not None:
        try:
            os.unlink(tmp_path)
        except OSError as exc:
            problem += ("; the anchor temporary at {} could not be removed ({}) and must be "
                        "removed by hand".format(tmp_path, exc))
        else:
            problem += "; the anchor temporary at {} was removed".format(tmp_path)
        raise CannotEvaluate(problem)
    return True


def run(root, update=False):
    reg, reg_found = None, False
    for rel in REGISTRY_FILES:
        p = Path(root) / rel
        # Registry PRESENCE is an explicit lstat whose only absence answer is ENOENT: a
        # dangling symlink or an uninspectable entry SELECTS this registry file and fails
        # closed, never NOT APPLICABLE and never a silent fall-through to the lower-priority
        # registry file.
        try:
            reg_entry = entry_state(p)
        except CannotEvaluate as exc:
            print("error: the orchestration registry at {} cannot be inspected ({}); only "
                  "confirmed absence (ENOENT) is a missing registry; fail-closed".format(
                      rel, exc))
            return 2
        if reg_entry is None:
            continue
        reg_found = True
        try:
            reg = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("error: cannot read the orchestration registry: {}".format(exc))
            return 2
        break
    if reg_found and not isinstance(reg, dict):
        print("error: the orchestration registry is not a JSON object; cannot evaluate its "
              "declared surfaces (a malformed registry is never NOT APPLICABLE)")
        return 2
    surfaces = []
    if isinstance(reg, dict):
        for key, prefix in (("mistakes_register", "MR-"), ("attestations", "AT-")):
            if key not in reg:
                continue
            declared = reg[key]
            if not isinstance(declared, str) or not declared:
                print("error: the registry's {} declaration is not a non-empty string; a "
                      "malformed declaration is never NOT APPLICABLE and never a skipped "
                      "surface".format(key))
                return 2
            surfaces.append((key, prefix, declared))
    if not surfaces:
        print("NOT APPLICABLE: no orchestration registry (or no declared mistakes or attestations "
              "register); the self-test carries the assurance")
        return 0
    worst, green = 0, []
    for key, prefix, declared in surfaces:
        path = Path(declared) if os.path.isabs(declared) else Path(root) / declared
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print("error: the DECLARED {} is unreadable ({}); fail-closed".format(key, exc))
            return 2
        try:
            line_problems = verify_lines(lines, root=root, prefix=prefix)
        except CannotEvaluate as exc:
            print("error: cannot evaluate the {}'s rows ({}); fail-closed".format(key, exc))
            return 2
        try:
            append_problems = verify_append_only(root, path, lines, allow_missing_anchor=update)
        except CannotEvaluate as exc:
            print("error: cannot evaluate the {}'s append-only history ({}); fail-closed"
                  .format(key, exc))
            return 2
        problems = line_problems + append_problems
        if problems:
            print("FAIL: {} {} violation(s):".format(len(problems), key))
            for p in problems:
                print("  " + p)
            worst = 1
            continue
        green.append((key, path, lines))
    if worst:
        return 1
    for key, path, lines in green:
        if update:
            try:
                advanced = update_anchor(path, lines)
            except CannotEvaluate as exc:
                print("error: cannot advance the {}'s anchor ({}); fail-closed".format(key, exc))
                return 2
            if advanced:
                print("PASS: {} verified; anchor advanced to seq {}".format(key, len(lines)))
            else:
                print("PASS: {} verified; the register is empty, so there is no tip to "
                      "anchor and the anchor was not advanced".format(key))
        else:
            print("PASS: the {} is a verified append-only chain ({} row(s))".format(
                key, len(lines)))
    return 0


def self_test():
    import contextlib
    import io
    import shutil
    import tempfile
    import threading
    # Hermetic git fixtures (test-hermeticity): every fixture git call below inherits
    # os.environ, where an inherited GIT_INDEX_FILE / GIT_DIR (git exports these to hook
    # children) would redirect the fixture's init/add/commit into the CALLER's repository.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import scrub_git_environment
    scrub_git_environment()
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-register-"))
    failures = []

    def row(seq, rid, status, prev, extra=None):
        r = {"seq": seq, "id": rid, "ts": "2026-08-29T00:00:00+00:00", "mistake": "m",
             "evidence": "e", "rule": "setcmp", "guardrail": "g", "status": status, "prev": prev}
        r.update(extra or {})
        return json.dumps(r, sort_keys=True)

    def chain(*rows_wo_prev):
        out, prev = [], ZERO
        for build in rows_wo_prev:
            line = build(prev)
            out.append(line)
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
        return out

    def _case(name, lines, want_clean, root=None, prefix="MR-"):
        got = not verify_lines(lines, root=root, prefix=prefix)
        if got != want_clean:
            failures.append("{}: clean={}, want {}".format(name, got, want_clean))

    def _git_ci(repo, *paths, msg):
        subprocess.run(["git", "-C", str(repo), "add", *paths], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c",
                        "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", msg], check=True, capture_output=True, timeout=30)

    try:
        # the AUTHORITY environment: every GIT_-prefixed variable is dropped before the root
        # probe and the authority reads, and ONLY the scrub's exact-valued config-isolation
        # pins survive (GIT_CONFIG_NOSYSTEM at "1", GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM
        # at os.devnull): a repository-selecting variable and a config-CARRYING variable
        # (GIT_CONFIG_PARAMETERS, the GIT_CONFIG_COUNT/KEY/VALUE family, a pin at a caller's
        # own value) never reaches them. The suite-level assurance is the fixture-environment
        # suite's system lane (config/registered-system): it plants a system git config and
        # fails any member whose git call sheds the pins.
        contaminated = {
            "GIT_DIR": str(tmp / "junk"), "GIT_WORK_TREE": str(tmp / "junk"),
            "GIT_COMMON_DIR": str(tmp / "junk"), "GIT_INDEX_FILE": str(tmp / "junk"),
            "GIT_OBJECT_DIRECTORY": str(tmp / "junk"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp / "junk"),
            "GIT_CEILING_DIRECTORIES": str(tmp / "junk"),
            "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1", "GIT_NAMESPACE": "ns",
            "GIT_CONFIG_PARAMETERS": "'core.worktree=/'",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": "/"}
        pin_names = ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
        saved_env = {k: os.environ.get(k) for k in list(contaminated) + list(pin_names)}
        os.environ.update(contaminated)
        try:
            auth = git_authority_env()
            kept = [(k, auth[k]) for k in sorted(auth) if k.startswith("GIT_")]
            want = [(name, "1" if name == "GIT_CONFIG_NOSYSTEM" else os.devnull)
                    for name in sorted(pin_names)]
            if kept != want:
                failures.append("authority-env-pins-survive-scrub: want exactly the three "
                                "scrub-valued config-isolation pins, got {}".format(kept))
            for name in pin_names:
                os.environ[name] = str(tmp / "caller-config")
            os.environ["GIT_CONFIG_NOSYSTEM"] = "0"
            auth = git_authority_env()
            kept = sorted(k for k in auth if k.startswith("GIT_"))
            if kept:
                failures.append("authority-env-caller-valued-pins-dropped: a pin at a "
                                "caller's own value must be dropped, kept {}".format(kept))
        finally:
            for name, value in saved_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        good = chain(lambda p: row(1, "MR-1", "proposed", p),
                     lambda p: row(2, "MR-2", "proposed", p),
                     lambda p: row(3, "MR-1", "accepted", p))
        _case("pure-append-passes", good, True)
        _case("broken-chain-fails", good[:2] + [row(3, "MR-1", "accepted", ZERO)], False)
        _case("reused-id-fails", chain(lambda p: row(1, "MR-1", "proposed", p),
                                       lambda p: row(2, "MR-1", "proposed", p)), False)
        _case("bad-transition-fails", chain(lambda p: row(1, "MR-1", "proposed", p),
                                            lambda p: row(2, "MR-1", "declined", p),
                                            lambda p: row(3, "MR-1", "landed", p)), False)
        _case("bad-seq-fails", chain(lambda p: row(1, "MR-1", "proposed", p),
                                     lambda p: row(9, "MR-2", "proposed", p)), False)
        # a non-object line, a bool or float seq, and a duplicated key are named findings,
        # never a crash or a silent pass
        _case("non-object-line-fails", ["17"], False)
        _case("bool-seq-fails", chain(lambda p: row(True, "MR-1", "proposed", p)), False)
        _case("float-seq-fails", chain(lambda p: row(1.0, "MR-1", "proposed", p)), False)
        dup_fields = (("seq", 1), ("id", "MR-1"), ("ts", "t"), ("mistake", "m"),
                      ("evidence", "e"), ("rule", "r"), ("guardrail", "g"),
                      ("status", "landed"), ("status", "proposed"), ("prev", ZERO))
        dup_row = "{" + ", ".join(
            json.dumps(k) + ": " + json.dumps(v) for k, v in dup_fields) + "}"
        _case("duplicate-key-fails", [dup_row], False)
        # edit/delete/reorder of an anchored row fails via the anchor
        reg_dir = tmp / "r"
        reg_dir.mkdir()
        # the anchor leg needs a DECIDABLE tracked question (the fail-closed tracked decision
        # refuses a non-repository root), so the untracked fixture lives in its own repository
        subprocess.run(["git", "init", "-q", str(reg_dir)], check=True,
                       capture_output=True, timeout=30)
        rp = reg_dir / "register.jsonl"
        rp.write_text("\n".join(good) + "\n", encoding="utf-8")
        update_anchor(rp, good)
        tampered = [good[0], good[2], good[1]]
        if not verify_append_only(reg_dir, rp, tampered):
            failures.append("anchored reorder should fail")
        if verify_append_only(reg_dir, rp, good + [row(4, "MR-3", "proposed", "x")]):
            failures.append("anchored append should pass the append-only leg")
        # malformed anchor CONTROLS are cannot-evaluate, never a violation list or a silently
        # bypassed digest comparison
        anchor_path = Path(str(rp) + ".anchor")
        good_anchor = anchor_path.read_text(encoding="utf-8")
        for tag, seq_val, digest_val in (("seq -1", -1, "0" * 64),
                                         ("seq 0", 0, "0" * 64),
                                         ("seq true", True, "0" * 64),
                                         ("float seq", 1.0, "0" * 64),
                                         ("bad digest", 1, "Z" * 64),
                                         ("short hex digest", 1, "a" * 63)):
            anchor_path.write_text(json.dumps({"seq": seq_val, "digest": digest_val}),
                                   encoding="utf-8")
            try:
                verify_append_only(reg_dir, rp, good)
                failures.append("a {} anchor should be cannot-evaluate".format(tag))
            except CannotEvaluate as exc:
                if "malformed" not in str(exc):
                    failures.append("the {} anchor refusal should say malformed, got: "
                                    "{}".format(tag, exc))
        anchor_path.write_text("not json", encoding="utf-8")
        try:
            verify_append_only(reg_dir, rp, good)
            failures.append("a not-json anchor should be cannot-evaluate")
        except CannotEvaluate as exc:
            if "cannot be read as JSON" not in str(exc):
                failures.append("the not-json anchor refusal should name the JSON read, got: "
                                "{}".format(exc))
        anchor_path.write_text("[1, 2]", encoding="utf-8")
        try:
            verify_append_only(reg_dir, rp, good)
            failures.append("a non-object anchor should be cannot-evaluate")
        except CannotEvaluate as exc:
            if "malformed" not in str(exc):
                failures.append("the non-object anchor refusal should say malformed, got: "
                                "{}".format(exc))
        # anchor presence is lstat-based: an entry that EXISTS at the anchor path but cannot be
        # used (a dangling symlink, a directory) is cannot-evaluate, never the missing-anchor
        # allowance, with allow_missing_anchor True (the --update-anchor bootstrap) included
        anchor_path.unlink()
        anchor_path.symlink_to("missing-anchor-target")
        for allow in (False, True):
            try:
                verify_append_only(reg_dir, rp, good, allow_missing_anchor=allow)
                failures.append("a dangling-symlink anchor should be cannot-evaluate "
                                "(allow_missing_anchor={})".format(allow))
            except CannotEvaluate as exc:
                if "never the missing-anchor allowance" not in str(exc):
                    failures.append("the dangling-symlink anchor refusal should name the "
                                    "allowance, got: {}".format(exc))
        anchor_path.unlink()
        anchor_path.mkdir()
        try:
            verify_append_only(reg_dir, rp, good, allow_missing_anchor=True)
            failures.append("a directory at the anchor path should be cannot-evaluate")
        except CannotEvaluate as exc:
            if "not a regular file" not in str(exc):
                failures.append("the directory-anchor refusal should name the shape, got: "
                                "{}".format(exc))
        anchor_path.rmdir()
        # the anchor is opened ONCE without following a final symlink and its type is checked
        # on the opened descriptor: a live symlink to a VALID anchor (which the gate used to
        # follow and pass) and a live symlink to a directory (which the gate used to refuse
        # only by its target's type, so the refusal must name the link) are each
        # cannot-evaluate, bootstrap included
        anchor_store = reg_dir / "anchor-store.json"
        anchor_store.write_text(good_anchor, encoding="utf-8")
        (reg_dir / "anchor-store-dir").mkdir()
        for tag, target in (("live symlink to a valid anchor", "anchor-store.json"),
                            ("live symlink to a directory", "anchor-store-dir")):
            anchor_path.symlink_to(target)
            for allow in (False, True):
                try:
                    verify_append_only(reg_dir, rp, good, allow_missing_anchor=allow)
                    failures.append("a {} at the anchor path should be cannot-evaluate "
                                    "(allow_missing_anchor={})".format(tag, allow))
                except CannotEvaluate as exc:
                    if "is a symlink, which is never followed" not in str(exc) \
                            or "never the missing-anchor allowance" not in str(exc):
                        failures.append("the {} refusal should name the link and the "
                                        "allowance, got: {}".format(tag, exc))
            anchor_path.unlink()

        def timed_refusal(seconds=10):
            # a TIMED daemon thread, so a blocking anchor read is a named failure here, never
            # a hung self-test (the thread is reaped at interpreter exit)
            box = []

            def work():
                try:
                    box.append(("returned", verify_append_only(
                        reg_dir, rp, good, allow_missing_anchor=True)))
                except CannotEvaluate as exc:
                    box.append(("refused", str(exc)))
            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            worker.join(seconds)
            return box[0] if box else ("blocked", None)

        # a FIFO at the anchor path is refused without blocking (a CONTROL: a FIFO at rest was
        # refused before, by a type check on the path), and a FIFO swapped in AFTER the type
        # check of the path (simulated by masking lstat and stat to report the regular store
        # file) is refused on the opened descriptor (the gate used to block in its read)
        os.mkfifo(str(anchor_path))
        fifo_kind, fifo_detail = timed_refusal()
        if fifo_kind != "refused" or "not a regular file" not in fifo_detail:
            failures.append("a FIFO at the anchor path should be refused promptly as not a "
                            "regular file, got {}: {}".format(fifo_kind, fifo_detail))
        real_os_lstat, real_os_stat = os.lstat, os.stat
        store_stat = real_os_stat(str(anchor_store))

        def regular_view(real):
            def view(p, *largs, **lkw):
                if str(p) == str(anchor_path):
                    return store_stat
                return real(p, *largs, **lkw)
            return view

        os.lstat, os.stat = regular_view(real_os_lstat), regular_view(real_os_stat)
        try:
            swap_kind, swap_detail = timed_refusal()
        finally:
            os.lstat, os.stat = real_os_lstat, real_os_stat
        if swap_kind != "refused" or "not a regular file" not in swap_detail:
            failures.append("a FIFO swapped in after the type check should be refused on the "
                            "opened descriptor, got {}: {}".format(swap_kind, swap_detail))
        anchor_path.unlink()
        anchor_path.write_text(good_anchor, encoding="utf-8")
        # anchor presence is an EXPLICIT lstat whose only absence answer is ENOENT: any other
        # inspection failure (EIO here) leaves the presence question open and is
        # cannot-evaluate, never the missing-anchor allowance, bootstrap included
        real_os_lstat = os.lstat

        def eio_lstat(p, *largs, **lkw):
            if str(p) == str(anchor_path):
                raise OSError(5, "injected input-output error")
            return real_os_lstat(p, *largs, **lkw)

        os.lstat = eio_lstat
        try:
            for allow in (False, True):
                try:
                    verify_append_only(reg_dir, rp, good, allow_missing_anchor=allow)
                    failures.append("an anchor lstat failure should be cannot-evaluate "
                                    "(allow_missing_anchor={})".format(allow))
                except CannotEvaluate as exc:
                    if "cannot be inspected" not in str(exc):
                        failures.append("the anchor-lstat-failure refusal should name the "
                                        "inspection, got: {}".format(exc))
        finally:
            os.lstat = real_os_lstat
        # ATOMIC-REPLACE DISCRIMINATORS (the three write properties a green suite must pin):
        # (1) an entry planted AFTER the inspection is REPLACED as a whole entry, never
        # followed (the disclosed replace residual): simulate the race by masking the
        # inspection while a live link sits at the anchor path
        race_store = reg_dir / "race-anchor-store.json"
        race_store.write_text("sentinel", encoding="utf-8")
        anchor_path.unlink()
        anchor_path.symlink_to("race-anchor-store.json")
        store_path = str(race_store)

        def masking_lstat(p, *largs, **lkw):
            if str(p) == str(anchor_path):
                return real_os_lstat(store_path)
            return real_os_lstat(p, *largs, **lkw)

        os.lstat = masking_lstat
        try:
            try:
                update_anchor(rp, good)
            except CannotEvaluate as exc:
                failures.append("the race-window advance should land by whole-entry replace, "
                                "got: {}".format(exc))
        finally:
            os.lstat = real_os_lstat
        if race_store.read_text(encoding="utf-8") != "sentinel":
            failures.append("the anchor write followed a link planted after the inspection "
                            "(the link's target was rewritten)")
        if os.path.islink(str(anchor_path)):
            failures.append("a link planted after the inspection should be replaced as a "
                            "whole entry")
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the replaced anchor entry should hold the verified tip")
        if [e.name for e in reg_dir.iterdir() if ".anchor.tmp." in e.name]:
            failures.append("the race-window advance left a temporary behind")
        # (2) the temporary is created EXCLUSIVELY: a stale temporary (an interrupted earlier
        # run) refuses the advance, is never rewritten, and the refusal names its by-hand
        # removal
        stale_tmp = Path("{}.tmp.{}".format(anchor_path, os.getpid()))
        stale_tmp.write_text("sentinel-temporary", encoding="utf-8")
        try:
            update_anchor(rp, good)
            failures.append("an existing anchor temporary should refuse the advance "
                            "(exclusive creation), never rewrite it")
        except CannotEvaluate as exc:
            if "anchor temporary cannot be created" not in str(exc) \
                    or "removed by hand" not in str(exc):
                failures.append("the stale-temporary refusal should name the temporary and "
                                "its by-hand removal, got: {}".format(exc))
        if entry_state(stale_tmp) is None \
                or stale_tmp.read_text(encoding="utf-8") != "sentinel-temporary":
            failures.append("a refused advance consumed or rewrote the existing anchor "
                            "temporary")
        else:
            stale_tmp.unlink()
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("a refused advance changed the anchor")
        # (3) a failed replace is cannot-evaluate, removes the temporary and leaves the
        # previous anchor byte-identical (dropping the cleanup or the replace goes red here)
        real_os_replace = os.replace

        def failing_replace(rsrc, rdst, *rargs, **rkw):
            raise OSError(5, "injected replace failure")

        os.replace = failing_replace
        try:
            try:
                update_anchor(rp, good)
                failures.append("a failed anchor replace should be cannot-evaluate")
            except CannotEvaluate as exc:
                if "could not be written atomically" not in str(exc):
                    failures.append("the failed-replace refusal should name the atomic "
                                    "write, got: {}".format(exc))
        finally:
            os.replace = real_os_replace
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the previous anchor must survive a failed replace "
                            "byte-identical")
        if [e.name for e in reg_dir.iterdir() if ".anchor.tmp." in e.name]:
            failures.append("a failed replace left the anchor temporary behind")
        # (4) EVERY failed advance reports its temporary's fate beside the write failure: a
        # write error, a short write and a close error each remove the temporary and SAY so,
        # a cleanup that itself fails is reported with the temporary's path, never swallowed,
        # and the previous anchor stays byte-identical through each
        real_os_write = os.write
        real_os_close = os.close
        real_os_unlink = os.unlink
        tmp_name = "{}.tmp.{}".format(anchor_path, os.getpid())
        anchor_fired = {"write": False, "short": False, "close": False,
                        "replace": False, "unlink": False}

        def anchor_write_error(wfd, data):
            if not anchor_fired["write"]:
                anchor_fired["write"] = True
                raise OSError(5, "injected write failure")
            return real_os_write(wfd, data)

        os.write = anchor_write_error
        try:
            try:
                update_anchor(rp, good)
                failures.append("a failed anchor-temporary write should be cannot-evaluate")
            except CannotEvaluate as exc:
                if "could not be written atomically" not in str(exc) \
                        or "the anchor temporary at" not in str(exc) \
                        or "was removed" not in str(exc):
                    failures.append("the failed-write refusal should report the temporary's "
                                    "removal beside the write failure, got: {}".format(exc))
        finally:
            os.write = real_os_write
        if entry_state(tmp_name) is not None:
            failures.append("a failed anchor-temporary write left the temporary behind")
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the previous anchor must survive a failed temporary write "
                            "byte-identical")

        def anchor_short_write(wfd, data):
            if not anchor_fired["short"]:
                anchor_fired["short"] = True
                return real_os_write(wfd, data[:len(data) - 1])
            return real_os_write(wfd, data)

        os.write = anchor_short_write
        try:
            try:
                update_anchor(rp, good)
                failures.append("a short anchor-temporary write should be cannot-evaluate")
            except CannotEvaluate as exc:
                if "short write to the anchor temporary" not in str(exc) \
                        or "was removed" not in str(exc):
                    failures.append("the short-write refusal should report the temporary's "
                                    "removal beside the short write, got: {}".format(exc))
        finally:
            os.write = real_os_write
        if entry_state(tmp_name) is not None:
            failures.append("a short anchor-temporary write left the temporary behind")
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the previous anchor must survive a short temporary write "
                            "byte-identical")

        def anchor_close_error(cfd):
            if not anchor_fired["close"]:
                anchor_fired["close"] = True
                real_os_close(cfd)  # release the descriptor, then report the failure
                raise OSError(5, "injected close failure")
            return real_os_close(cfd)

        os.close = anchor_close_error
        try:
            try:
                update_anchor(rp, good)
                failures.append("a failed anchor-temporary close should be cannot-evaluate")
            except CannotEvaluate as exc:
                if "could not be written atomically" not in str(exc) \
                        or "injected close failure" not in str(exc) \
                        or "was removed" not in str(exc):
                    failures.append("the failed-close refusal should report the temporary's "
                                    "removal beside the close failure, got: {}".format(exc))
        finally:
            os.close = real_os_close
        if entry_state(tmp_name) is not None:
            failures.append("a failed anchor-temporary close left the temporary behind")
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the previous anchor must survive a failed temporary close "
                            "byte-identical")

        def cleanup_replace_error(rsrc, rdst, *rargs, **rkw):
            if not anchor_fired["replace"]:
                anchor_fired["replace"] = True
                raise OSError(5, "injected replace failure")
            return real_os_replace(rsrc, rdst, *rargs, **rkw)

        def cleanup_unlink_error(upath, *uargs, **ukw):
            if not anchor_fired["unlink"] and str(upath) == tmp_name:
                anchor_fired["unlink"] = True
                raise OSError(13, "injected cleanup failure")
            return real_os_unlink(upath, *uargs, **ukw)

        os.replace = cleanup_replace_error
        os.unlink = cleanup_unlink_error
        try:
            try:
                update_anchor(rp, good)
                failures.append("a failed replace with a failed cleanup should be "
                                "cannot-evaluate")
            except CannotEvaluate as exc:
                if "could not be written atomically" not in str(exc) \
                        or "could not be removed" not in str(exc) \
                        or tmp_name not in str(exc) \
                        or "injected cleanup failure" not in str(exc):
                    failures.append("a failed cleanup must be reported with the temporary's "
                                    "path beside the write failure, never swallowed, got: "
                                    "{}".format(exc))
        finally:
            os.replace = real_os_replace
            os.unlink = real_os_unlink
        if entry_state(tmp_name) is None:
            failures.append("the failed-cleanup vector should leave the temporary in place "
                            "(its removal failed)")
        else:
            real_os_unlink(tmp_name)
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the previous anchor must survive a failed replace with a failed "
                            "cleanup byte-identical")
        # a stale temporary under ANOTHER process id blocks nothing (the exclusive creation
        # collides only with the CURRENT pid's pathname) and is left in place unchanged
        other_pid_tmp = Path("{}.tmp.{}".format(anchor_path, os.getpid() + 1))
        other_pid_tmp.write_text("other-pid-temporary", encoding="utf-8")
        try:
            if update_anchor(rp, good) is not True:
                failures.append("a stale temporary under another pid must not block the "
                                "advance")
        except CannotEvaluate as exc:
            failures.append("a stale temporary under another pid must not block the advance, "
                            "got: {}".format(exc))
        if entry_state(other_pid_tmp) is None \
                or other_pid_tmp.read_text(encoding="utf-8") != "other-pid-temporary":
            failures.append("the other-pid stale temporary should be left in place unchanged")
        else:
            other_pid_tmp.unlink()
        if anchor_path.read_text(encoding="utf-8") != good_anchor:
            failures.append("the advance past an other-pid stale temporary should land the "
                            "same verified tip")
        # a temporary-creation failure that is NOT the stale-temporary collision (EACCES here)
        # names the actual error and never blames a stale temporary
        real_os_open = os.open

        def eacces_open(opath, oflags, *oargs):
            if str(opath) == tmp_name:
                raise OSError(13, "injected permission denied")
            return real_os_open(opath, oflags, *oargs)

        os.open = eacces_open
        try:
            try:
                update_anchor(rp, good)
                failures.append("an EACCES temporary creation should be cannot-evaluate")
            except CannotEvaluate as exc:
                if "cannot be created" not in str(exc) \
                        or "injected permission denied" not in str(exc) \
                        or "removed by hand" in str(exc):
                    failures.append("a non-EEXIST creation failure must name the actual error "
                                    "and never blame a stale temporary, got: {}".format(exc))
        finally:
            os.open = real_os_open
        # a landed row citing a missing commit or check fails
        repo = tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                       timeout=30)
        check_path = repo / "durable-check.py"
        check_path.write_text("# durable check\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "durable-check.py"], check=True,
                       capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=T",
                        "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", "seed"], check=True, capture_output=True, timeout=30)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True, timeout=30).stdout.strip()
        landed = chain(lambda p: row(1, "MR-1", "proposed", p),
                       lambda p: row(2, "MR-1", "accepted", p),
                       lambda p: row(3, "MR-1", "landed", p,
                                     {"commit": "0" * 40, "check_ref": "tools/nosuch.py"}))
        _case("landed-unverifiable-fails", landed, False, root=repo)
        # P3: an absolute check_ref is invalid even when it names an existing fixture file.
        landed_absolute = chain(
            lambda p: row(1, "MR-2", "proposed", p),
            lambda p: row(2, "MR-2", "accepted", p),
            lambda p: row(3, "MR-2", "landed", p,
                          {"commit": head, "check_ref": str(check_path.resolve())}))
        _case("landed-absolute-check-ref-fails", landed_absolute, False, root=repo)
        # a landed check_ref whose entry inspection fails with anything but ENOENT is
        # cannot-evaluate through the shared entry_state, never a silent violation or pass
        landed_eio = chain(
            lambda p: row(1, "MR-3", "proposed", p),
            lambda p: row(2, "MR-3", "accepted", p),
            lambda p: row(3, "MR-3", "landed", p,
                          {"commit": head, "check_ref": "durable-check.py"}))
        check_target = str(check_path.resolve())
        real_cr_lstat = os.lstat

        def check_ref_eio(cp, *largs, **lkw):
            if str(cp) == check_target:
                raise OSError(5, "injected input-output error")
            return real_cr_lstat(cp, *largs, **lkw)

        os.lstat = check_ref_eio
        try:
            try:
                verify_lines(landed_eio, root=repo, prefix="MR-")
                failures.append("a landed check_ref inspection failure should be "
                                "cannot-evaluate, never a silent result")
            except CannotEvaluate as exc:
                if "cannot be inspected" not in str(exc):
                    failures.append("the check_ref inspection refusal should name itself, "
                                    "got: {}".format(exc))
        finally:
            os.lstat = real_cr_lstat
        if verify_lines(landed_eio, root=repo, prefix="MR-"):
            failures.append("the landed rows should verify clean once the inspection "
                            "succeeds")
        # the landed row's commit read is an authority read: a git that cannot be launched or
        # that times out is cannot-evaluate (exit 2 through the live leg, no traceback), never
        # a missing commit and never an exception of its own class
        catfile_live = tmp / "catfile-live"
        catfile_live.mkdir()
        subprocess.run(["git", "init", "-q", str(catfile_live)], check=True,
                       capture_output=True, timeout=30)
        (catfile_live / "durable-check.py").write_text("# durable check\n", encoding="utf-8")
        (catfile_live / ".aiqt").mkdir()
        (catfile_live / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": "mistakes.jsonl"}), encoding="utf-8")
        catfile_reg = catfile_live / "mistakes.jsonl"
        catfile_reg.write_text("\n".join(landed_eio) + "\n", encoding="utf-8")
        update_anchor(catfile_reg, landed_eio)
        real_subprocess_run = subprocess.run
        for cf_tag, cf_exc in (
                ("timeout", subprocess.TimeoutExpired(["git", "cat-file"], 10)),
                ("launch-error", OSError(2, "injected launch failure"))):
            def failing_catfile(argv, *rargs, **rkw):
                if "cat-file" in argv:
                    raise cf_exc
                return real_subprocess_run(argv, *rargs, **rkw)

            subprocess.run = failing_catfile
            try:
                try:
                    verify_lines(landed_eio, root=repo, prefix="MR-")
                    cf_got = "no exception"
                except CannotEvaluate as exc:
                    cf_got = "CannotEvaluate: {}".format(exc)
                except Exception as exc:  # the pre-fix escape: the raw launch exception
                    cf_got = "{}: {}".format(type(exc).__name__, exc)
                cf_out = io.StringIO()
                try:
                    with contextlib.redirect_stdout(cf_out):
                        cf_rc = run(catfile_live)
                except Exception as exc:
                    cf_rc = "{}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.run = real_subprocess_run
            if not cf_got.startswith("CannotEvaluate: ") \
                    or "cites an existing commit" not in cf_got:
                failures.append("landed-commit-read-{}-cannot-evaluate: verify_lines should "
                                "raise CannotEvaluate naming the commit read, got "
                                "{}".format(cf_tag, cf_got))
            if cf_rc != 2 or "cites an existing commit" not in cf_out.getvalue():
                failures.append("landed-commit-read-{}-live-exit-2: the live leg should exit 2 "
                                "naming the commit read, got {}: {}".format(
                                    cf_tag, cf_rc, cf_out.getvalue().strip()))
        # P1: a tracked register cannot be checked against itself when origin/HEAD is absent -> exit 2.
        (repo / ".aiqt").mkdir()
        tracked = repo / "mistakes.jsonl"
        tracked_good = chain(
            lambda p: row(1, "MR-1", "proposed", p),
            lambda p: row(2, "MR-1", "accepted", p),
            lambda p: row(3, "MR-1", "landed", p,
                          {"commit": head, "check_ref": "durable-check.py"}))
        tracked.write_text("\n".join(tracked_good) + "\n", encoding="utf-8")
        registry = repo / ".aiqt" / "orchestration.local.json"
        registry.write_text(json.dumps(
            {"version": 1, "mistakes_register": "mistakes.jsonl"}), encoding="utf-8")
        _git_ci(repo, ".aiqt/orchestration.local.json", "mistakes.jsonl", msg="add register")
        rewritten = chain(lambda p: row(1, "MR-1", "proposed", p),
                          lambda p: row(2, "MR-1", "declined", p))
        tracked.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
        _git_ci(repo, "mistakes.jsonl", msg="rewrite register")
        if run(repo) != 2:
            failures.append("tracked rewrite without origin/HEAD should exit 2")
        # a git failure deciding WHETHER the register is tracked (a corrupt ON-DISK index:
        # exit 128, not git's confirmed not-tracked exit 1) is cannot-evaluate and names that
        # decision, never a fall-through into the untracked anchor leg
        index_path = repo / ".git" / "index"
        saved_index = index_path.read_bytes()
        index_path.write_bytes(b"broken-index")
        try:
            try:
                verify_append_only(repo, tracked, rewritten)
                failures.append("a git failure deciding tracked status should be "
                                "cannot-evaluate")
            except CannotEvaluate as exc:
                if "cannot decide whether the register is tracked" not in str(exc):
                    failures.append("the tracked-decision failure should name itself, got: "
                                    "{}".format(exc))
        finally:
            index_path.write_bytes(saved_index)
        # the authority reads run under a SCRUBBED environment: a junk GIT_INDEX_FILE in the
        # CALLER's environment no longer reaches them, so the tracked decision stands and the
        # tracked leg proceeds to its own origin/HEAD refusal (before this change the same
        # junk index flipped the decision itself into cannot-decide)
        junk_index = tmp / "junk-index"
        junk_index.write_text("broken-index", encoding="utf-8")
        os.environ["GIT_INDEX_FILE"] = str(junk_index)
        try:
            try:
                verify_append_only(repo, tracked, rewritten)
                failures.append("the tracked leg without origin/HEAD should still be "
                                "cannot-evaluate")
            except CannotEvaluate as exc:
                if "cannot compute the register authority" not in str(exc):
                    failures.append("a caller GIT_INDEX_FILE must never reach the scrubbed "
                                    "tracked decision, got: {}".format(exc))
        finally:
            os.environ.pop("GIT_INDEX_FILE", None)
        # each of the three git AUTHORITY reads (ls-files, merge-base, show) fails closed on a
        # launch error or a timeout: cannot-evaluate with the decision named, never an unhandled
        # exception and never a fall-through into the untracked anchor leg
        real_sp_run = subprocess.run

        def _raising_for(target, raised):
            def runner(cmd, *args, **kwargs):
                if isinstance(cmd, list) and target in cmd:
                    raise raised
                return real_sp_run(cmd, *args, **kwargs)
            return runner

        for tag, target, raised, named in (
                ("ls-files launch error", "ls-files",
                 OSError(5, "injected launch failure"),
                 "cannot decide whether the register is tracked"),
                ("ls-files timeout", "ls-files",
                 subprocess.TimeoutExpired(["git"], 10),
                 "cannot decide whether the register is tracked"),
                ("merge-base launch error", "merge-base",
                 OSError(5, "injected launch failure"),
                 "cannot compute the register authority"),
                ("merge-base timeout", "merge-base",
                 subprocess.TimeoutExpired(["git"], 10),
                 "cannot compute the register authority")):
            subprocess.run = _raising_for(target, raised)
            try:
                try:
                    verify_append_only(repo, tracked, rewritten)
                    failures.append("a {} should be cannot-evaluate".format(tag))
                except CannotEvaluate as exc:
                    if named not in str(exc):
                        failures.append("the {} refusal should name its decision, got: "
                                        "{}".format(tag, exc))
            finally:
                subprocess.run = real_sp_run

        def _show_raising(raised):
            def runner(cmd, *args, **kwargs):
                if isinstance(cmd, list) and "merge-base" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, stdout="1234abcd\n", stderr="")
                if isinstance(cmd, list) and "show" in cmd:
                    raise raised
                return real_sp_run(cmd, *args, **kwargs)
            return runner

        for tag, raised in (("git-show launch error", OSError(5, "injected launch failure")),
                            ("git-show timeout", subprocess.TimeoutExpired(["git"], 10))):
            subprocess.run = _show_raising(raised)
            try:
                try:
                    verify_append_only(repo, tracked, rewritten)
                    failures.append("a {} should be cannot-evaluate".format(tag))
                except CannotEvaluate as exc:
                    if "cannot read the register at its merge-base authority" not in str(exc):
                        failures.append("the {} refusal should name its read, got: "
                                        "{}".format(tag, exc))
            finally:
                subprocess.run = real_sp_run
        # P2: a declared UNTRACKED register present with no anchor is cannot-evaluate -> exit 2.
        untracked = repo / "untracked-register.jsonl"
        untracked.write_text("\n".join(tracked_good) + "\n", encoding="utf-8")
        registry.write_text(json.dumps(
            {"version": 1, "mistakes_register": "untracked-register.jsonl"}), encoding="utf-8")
        if run(repo) != 2:
            failures.append("declared untracked register with no anchor should exit 2")
        # bootstrap: --update-anchor establishes the first anchor (exit 0), then the gate passes.
        if run(repo, update=True) != 0:
            failures.append("--update-anchor bootstrap should establish the anchor and pass")
        if run(repo) != 0:
            failures.append("gate should pass once the anchor is established")
        # GD-127 C.2: an attestations register (AT- prefix, optional class/ref fields) is covered by
        # the same machinery; before the change a declared-attestations-only registry was NOT
        # APPLICABLE (exit 0), so these legs discriminate.
        at_good = chain(lambda p: row(1, "AT-1", "proposed", p,
                                      {"ref": "ci", "class": "attestation"}))
        _case("at-prefix-passes-under-at", at_good, True, prefix="AT-")
        _case("at-rows-fail-default-prefix", at_good, False)
        at_reg_path = repo / "attest.jsonl"
        at_reg_path.write_text("\n".join(at_good) + "\n", encoding="utf-8")
        registry.write_text(json.dumps(
            {"version": 1, "attestations": "attest.jsonl"}), encoding="utf-8")
        if run(repo) != 2:
            failures.append("declared untracked attestations register with no anchor should exit 2")
        if run(repo, update=True) != 0:
            failures.append("--update-anchor should bootstrap the attestations anchor and pass")
        if run(repo) != 0:
            failures.append("gate should pass the attestations register once anchored")
        # end to end: a malformed anchor is exit 2 in the gate, then green once restored
        at_anchor_path = Path(str(at_reg_path) + ".anchor")
        saved_anchor = at_anchor_path.read_text(encoding="utf-8")
        at_anchor_path.write_text(json.dumps({"seq": True, "digest": "beef"}),
                                  encoding="utf-8")
        if run(repo) != 2:
            failures.append("a malformed anchor should be cannot-evaluate (exit 2) in the gate")
        at_anchor_path.write_text(saved_anchor, encoding="utf-8")
        if run(repo) != 0:
            failures.append("gate should pass again once the anchor is restored")
        # a dangling-symlink anchor entry is exit 2 even for the --update-anchor bootstrap, and
        # the bootstrap never creates the link's target
        at_anchor_path.unlink()
        at_anchor_path.symlink_to("missing-at-anchor-target")
        if run(repo, update=True) != 2:
            failures.append("a dangling-symlink anchor should be cannot-evaluate (exit 2) even "
                            "with --update-anchor")
        if entry_state(repo / "missing-at-anchor-target") is not None:
            failures.append("the --update-anchor bootstrap wrote through a dangling anchor "
                            "symlink")
        at_anchor_path.unlink()
        at_anchor_path.write_text(saved_anchor, encoding="utf-8")
        if run(repo) != 0:
            failures.append("gate should pass again once the dangling anchor entry is replaced")
        # a LIVE anchor symlink is refused, by the anchor READ before the write is reached (the
        # tracked leg below holds the write's own refusal): the link is never followed, its
        # target is never rewritten, and the link entry survives the refusal
        at_store = repo / "at-anchor-store.json"
        at_store.write_text(saved_anchor, encoding="utf-8")
        at_anchor_path.unlink()
        at_anchor_path.symlink_to("at-anchor-store.json")
        if run(repo, update=True) != 2:
            failures.append("--update-anchor through a live untracked anchor symlink should "
                            "be cannot-evaluate (exit 2)")
        if at_store.read_text(encoding="utf-8") != saved_anchor:
            failures.append("--update-anchor wrote through a live untracked anchor symlink")
        if not os.path.islink(str(at_anchor_path)):
            failures.append("--update-anchor replaced the live untracked anchor symlink entry")
        at_anchor_path.unlink()
        at_anchor_path.write_text(saved_anchor, encoding="utf-8")
        if run(repo) != 0:
            failures.append("gate should pass again once the live-link anchor entry is "
                            "replaced")
        at_wrong = chain(lambda p: row(1, "MR-1", "proposed", p))
        at_reg_path.write_text("\n".join(at_wrong) + "\n", encoding="utf-8")
        if run(repo) != 1:
            failures.append("an MR- row in the attestations register should fail the AT- prefix")
        # a PRESENT registry that is not a JSON object (a list, null) is cannot-evaluate,
        # never NOT APPLICABLE: a malformed control input is not an absent one
        for bad in ("[1, 2]", "null"):
            registry.write_text(bad, encoding="utf-8")
            if run(repo) != 2:
                failures.append("a non-object registry ({}) should be cannot-evaluate "
                                "(exit 2), never NOT APPLICABLE".format(bad))
        # registry PRESENCE is ENOENT-only (exit 2, never NOT APPLICABLE and never the
        # lower-priority registry file): a dangling registry symlink entry is SELECTED and
        # refused, with and without a valid lower-priority registry present, and a registry
        # lstat failure that is not ENOENT fails closed
        registry.unlink()
        registry.symlink_to("missing-registry-target")
        if run(repo) != 2:
            failures.append("a dangling registry symlink should be cannot-evaluate (exit 2), "
                            "never NOT APPLICABLE")
        fallback_registry = repo / ".aiqt" / "orchestration.json"
        fallback_registry.write_text(json.dumps(
            {"version": 1, "attestations": "attest.jsonl"}), encoding="utf-8")
        if run(repo) != 2:
            failures.append("a dangling higher-priority registry entry must fail closed, "
                            "never silently select the lower-priority registry file")
        fallback_registry.unlink()
        registry.unlink()
        registry.write_text(json.dumps(
            {"version": 1, "attestations": "attest.jsonl"}), encoding="utf-8")
        real_reg_lstat = os.lstat

        def reg_eio_lstat(p, *largs, **lkw):
            if str(p) == str(registry):
                raise OSError(5, "injected input-output error")
            return real_reg_lstat(p, *largs, **lkw)

        os.lstat = reg_eio_lstat
        try:
            if run(repo) != 2:
                failures.append("a registry lstat failure that is not ENOENT should be "
                                "cannot-evaluate (exit 2)")
        finally:
            os.lstat = real_reg_lstat
        # a declared register value that is not a non-empty string (a number, a list, an
        # empty string, null) is a NAMED exit 2 for EACH form, never a TypeError traceback
        # and never a skipped surface; the exact refusal text is asserted per form, so a
        # mutant that skips one form (or renames the diagnostic) goes red here
        for bad_decl, bad_form in (('{"version": 1, "mistakes_register": 5}', "a number"),
                                   ('{"version": 1, "mistakes_register": ["x"]}', "a list"),
                                   ('{"version": 1, "mistakes_register": ""}',
                                    "an empty string"),
                                   ('{"version": 1, "mistakes_register": null}', "null")):
            registry.write_text(bad_decl, encoding="utf-8")
            decl_out = io.StringIO()
            with contextlib.redirect_stdout(decl_out):
                decl_rc = run(repo)
            if decl_rc != 2 or ("the registry's mistakes_register declaration is not a "
                                "non-empty string") not in decl_out.getvalue():
                failures.append("a mistakes_register declaration that is {} should be a "
                                "named cannot-evaluate (exit 2), got exit {}: {}".format(
                                    bad_form, decl_rc, decl_out.getvalue().strip()))
        registry.write_text('{"version": 1, "attestations": 5}', encoding="utf-8")
        decl_out = io.StringIO()
        with contextlib.redirect_stdout(decl_out):
            decl_rc = run(repo)
        if decl_rc != 2 or ("the registry's attestations declaration is not a non-empty "
                            "string") not in decl_out.getvalue():
            failures.append("a malformed attestations declaration should name its own key, "
                            "got exit {}: {}".format(decl_rc, decl_out.getvalue().strip()))
        # TRACKED registers and the anchor WRITE: a green tracked run reaches update_anchor
        # through the merge-base leg (no anchor inspection on the read side), so the write
        # itself must refuse a symlink entry, dangling or live, and advance only by atomic
        # replace of an exclusively created temporary
        trk = tmp / "trk"
        trk.mkdir()
        subprocess.run(["git", "init", "-q", str(trk)], check=True, capture_output=True,
                       timeout=30)
        (trk / ".aiqt").mkdir()
        trk_reg = trk / "trk-reg.jsonl"
        trk_rows = chain(lambda p: row(1, "MR-1", "proposed", p))
        trk_reg.write_text("\n".join(trk_rows) + "\n", encoding="utf-8")
        (trk / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": "trk-reg.jsonl"}), encoding="utf-8")
        _git_ci(trk, ".aiqt/orchestration.local.json", "trk-reg.jsonl", msg="track register")
        subprocess.run(["git", "-C", str(trk), "update-ref", "refs/remotes/origin/main",
                        "HEAD"], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(trk), "symbolic-ref", "refs/remotes/origin/HEAD",
                        "refs/remotes/origin/main"], check=True, capture_output=True,
                       timeout=30)
        if run(trk) != 0:
            failures.append("a clean tracked register with a computable merge-base should pass")
        trk_anchor = Path(str(trk_reg) + ".anchor")
        trk_anchor.symlink_to("missing-trk-anchor-target")
        if run(trk, update=True) != 2:
            failures.append("--update-anchor through a dangling tracked-register anchor "
                            "symlink should be cannot-evaluate (exit 2)")
        if entry_state(trk / "missing-trk-anchor-target") is not None:
            failures.append("--update-anchor created a dangling tracked anchor link's target")
        if not os.path.islink(str(trk_anchor)):
            failures.append("--update-anchor replaced the dangling tracked anchor symlink "
                            "entry")
        trk_anchor.unlink()
        trk_store = trk / "trk-anchor-store.json"
        trk_store.write_text("{}", encoding="utf-8")
        trk_anchor.symlink_to("trk-anchor-store.json")
        if run(trk, update=True) != 2:
            failures.append("--update-anchor through a LIVE tracked-register anchor symlink "
                            "should be cannot-evaluate (exit 2)")
        if trk_store.read_text(encoding="utf-8") != "{}":
            failures.append("--update-anchor wrote through a live tracked anchor symlink")
        if not os.path.islink(str(trk_anchor)):
            failures.append("--update-anchor replaced the live tracked anchor symlink entry")
        trk_anchor.unlink()
        if run(trk, update=True) != 0:
            failures.append("--update-anchor on a green tracked register should advance the "
                            "anchor and pass")
        if json.loads(trk_anchor.read_text(encoding="utf-8")) != {
                "seq": 1, "digest": hashlib.sha256(
                    trk_rows[-1].encode("utf-8")).hexdigest()}:
            failures.append("the advanced tracked anchor should carry the verified tip")
        leftovers = [e.name for e in trk.iterdir() if ".anchor.tmp." in e.name]
        if leftovers:
            failures.append("the anchor write left a temporary behind: {}".format(leftovers))
        # an EMPTY tracked register never routes around the anchor inspection, and no advance
        # is reported when none happened (there is no tip to anchor)
        empty_reg = trk / "empty-reg.jsonl"
        empty_reg.write_text("", encoding="utf-8")
        (trk / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": "empty-reg.jsonl"}), encoding="utf-8")
        _git_ci(trk, ".aiqt/orchestration.local.json", "empty-reg.jsonl",
                msg="track the empty register")
        subprocess.run(["git", "-C", str(trk), "update-ref", "refs/remotes/origin/main",
                        "HEAD"], check=True, capture_output=True, timeout=30)
        empty_anchor = Path(str(empty_reg) + ".anchor")
        empty_anchor.symlink_to("missing-empty-anchor-target")
        if run(trk, update=True) != 2:
            failures.append("an empty register with an unusable anchor entry should be "
                            "cannot-evaluate (exit 2), never a reported advance")
        if entry_state(trk / "missing-empty-anchor-target") is not None:
            failures.append("an empty-register update wrote through a dangling anchor "
                            "symlink")
        empty_anchor.unlink()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = run(trk, update=True)
        if rc != 0 or "anchor advanced" in out.getvalue() \
                or "the anchor was not advanced" not in out.getvalue():
            failures.append("an empty-register update should pass without claiming an "
                            "advance, got exit {}: {}".format(rc, out.getvalue().strip()))
        if entry_state(empty_anchor) is not None:
            failures.append("an empty-register update wrote an anchor")
        # the SHARED root resolution: the marker walk selects the nearest .git entry WHATEVER
        # its type (a dangling symlink included, inside a valid outer repository) and lets the
        # git confirmation refuse it; an uninspectable nearer marker is cannot-evaluate, never
        # a walk past it to the outer repository; a nested EMPTY .git directory makes git
        # discovery name the OUTER toplevel, which the realpath comparison refuses
        walk_outer = tmp / "walk-outer"
        (walk_outer / "w" / "tools").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(walk_outer)], check=True,
                       capture_output=True, timeout=30)
        (walk_outer / "w" / ".git").symlink_to("missing-git-target")
        selected = gate_root(walk_outer / "w" / "tools")
        if selected != walk_outer / "w":
            failures.append("the marker walk should select the dangling .git symlink, never "
                            "walk past it to the outer repository, got {}".format(selected))
        if confirmed_root(walk_outer / "w"):
            failures.append("a dangling .git symlink is a marker, never a repository git "
                            "confirms")
        if not confirmed_root(walk_outer):
            failures.append("the valid outer repository should be git-confirmed")
        (walk_outer / "x" / "tools").mkdir(parents=True)
        (walk_outer / "x" / ".git").mkdir()
        if gate_root(walk_outer / "x" / "tools") != walk_outer / "x":
            failures.append("the marker walk should select the nested empty .git directory")
        if confirmed_root(walk_outer / "x"):
            failures.append("git discovery past an empty .git directory names the OUTER "
                            "toplevel; the realpath comparison must refuse the nested root")
        (walk_outer / "e" / "tools").mkdir(parents=True)
        (walk_outer / "e" / ".git").mkdir()
        eio_marker = str(walk_outer / "e" / ".git")

        def marker_eio_lstat(mp, *largs, **lkw):
            if str(mp) == eio_marker:
                raise OSError(5, "injected input-output error")
            return real_os_lstat(mp, *largs, **lkw)

        os.lstat = marker_eio_lstat
        try:
            try:
                got_root = gate_root(walk_outer / "e" / "tools")
                failures.append("an uninspectable nearer .git marker should be "
                                "cannot-evaluate, never a root (got {})".format(got_root))
            except CannotEvaluate as exc:
                if "cannot be inspected" not in str(exc):
                    failures.append("the marker-walk refusal should name the inspection, "
                                    "got: {}".format(exc))
        finally:
            os.lstat = real_os_lstat
        # the LIVE gate (main) uses the same root confirmation BEFORE reading the registry:
        # an installed gate under an invalid gitfile or an empty .git directory refuses with
        # exit 2 (a contaminated caller git environment included: GIT_DIR, and GIT_DIR plus
        # GIT_WORK_TREE, pointed at a valid repository) and writes no anchor even for a
        # declared EXTERNAL register under --update-anchor; in a valid repository the live
        # leg still answers (NOT APPLICABLE with no registry, a green --update-anchor
        # bootstrap with one)
        gate_src = Path(__file__).resolve()
        live_ext = tmp / "live-ext"
        live_ext.mkdir()
        ext_rows = chain(lambda p: row(1, "MR-1", "proposed", p))
        for live_tag in ("invalid-gitfile", "empty-gitdir"):
            live_inst = tmp / ("live-" + live_tag)
            (live_inst / "tools").mkdir(parents=True)
            if live_tag == "invalid-gitfile":
                (live_inst / ".git").write_text("not a gitfile\n", encoding="utf-8")
            else:
                (live_inst / ".git").mkdir()
            shutil.copy(str(gate_src), str(live_inst / "tools" / gate_src.name))
            (live_inst / ".aiqt").mkdir()
            live_reg = live_ext / (live_tag + "-reg.jsonl")
            live_reg.write_text("\n".join(ext_rows) + "\n", encoding="utf-8")
            (live_inst / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
                {"version": 1, "mistakes_register": str(live_reg)}), encoding="utf-8")
            for env_tag, live_env in (
                    ("plain", None),
                    ("git-dir", dict(os.environ, GIT_DIR=str(reg_dir / ".git"))),
                    ("git-dir-worktree", dict(os.environ, GIT_DIR=str(reg_dir / ".git"),
                                              GIT_WORK_TREE=str(live_inst)))):
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(live_inst / "tools" / gate_src.name),
                     "--update-anchor"],
                    capture_output=True, text=True, timeout=60, cwd=str(tmp), env=live_env)
                if proc.returncode != 2 \
                        or "not a git repository git itself confirms" not in proc.stdout:
                    failures.append("live-{}-{}: the installed gate should refuse (exit 2) "
                                    "naming the unconfirmed root, got exit {}: {} {}".format(
                                        live_tag, env_tag, proc.returncode,
                                        proc.stdout.strip(), proc.stderr.strip()))
                if entry_state(str(live_reg) + ".anchor") is not None:
                    failures.append("live-{}-{}: the refused live gate wrote the external "
                                    "register's anchor".format(live_tag, env_tag))
        live_ok = tmp / "live-ok"
        (live_ok / "tools").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(live_ok)], check=True, capture_output=True,
                       timeout=30)
        shutil.copy(str(gate_src), str(live_ok / "tools" / gate_src.name))
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(live_ok / "tools" / gate_src.name)],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 0 or "NOT APPLICABLE" not in proc.stdout:
            failures.append("live-ok: a confirmed root with no registry should be NOT "
                            "APPLICABLE (exit 0), got exit {}: {} {}".format(
                                proc.returncode, proc.stdout.strip(), proc.stderr.strip()))
        (live_ok / ".aiqt").mkdir()
        ok_reg = live_ok / "ok-reg.jsonl"
        ok_reg.write_text("\n".join(ext_rows) + "\n", encoding="utf-8")
        (live_ok / ".aiqt" / "orchestration.local.json").write_text(json.dumps(
            {"version": 1, "mistakes_register": "ok-reg.jsonl"}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(live_ok / "tools" / gate_src.name),
             "--update-anchor"],
            capture_output=True, text=True, timeout=60, cwd=str(tmp))
        if proc.returncode != 0 or "anchor advanced to seq 1" not in proc.stdout:
            failures.append("live-ok-update: a confirmed root should verify and bootstrap "
                            "its anchor (exit 0), got exit {}: {} {}".format(
                                proc.returncode, proc.stdout.strip(), proc.stderr.strip()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: pure appends pass; a broken chain or sequence, a reused id, an illegal "
          "transition, a non-object line, a bool or float seq, a duplicated key, an anchored "
          "rewrite or reorder, an unverifiable or absolute-path landed check_ref, a tracked "
          "rewrite with no computable merge-base, and a declared untracked register with no "
          "anchor all fail; a malformed, non-object or wrongly shaped anchor (a zero, negative, "
          "boolean or float seq, a short or non-hex digest), an anchor entry that exists but is "
          "not a usable regular file (a dangling symlink, a directory, a live symlink to a "
          "valid anchor or to a directory, named as a link, a FIFO, and a FIFO swapped in "
          "after the type check, refused on the opened descriptor without blocking, the "
          "--update-anchor bootstrap included), an anchor lstat failure that is not ENOENT "
          "(absence is only ENOENT, bootstrap included), a present registry that is not a JSON "
          "object, a "
          "registry entry whose lstat fails with anything but ENOENT or that is a dangling "
          "symlink (registry presence is ENOENT-only: selected and refused, never NOT "
          "APPLICABLE and never the lower-priority registry file), a declared register value "
          "that is not a non-empty string, and a "
          "git launch failure or timeout on each of the four authority reads (ls-files, "
          "merge-base, git show, and the landed row's cat-file commit read, the last through "
          "verify_lines and the live leg alike) plus an undecidable ls-files exit are "
          "cannot-evaluate (exit 2); the anchor write refuses a symlink anchor entry, dangling or live, tracked and "
          "untracked registers alike, with the link's target neither created nor rewritten and "
          "the link entry left in place, and a green advance (a clean tracked register with a "
          "computable merge-base included) lands the verified tip by atomic replace of an "
          "exclusively created temporary, leaving no temporary behind (a stale temporary at "
          "this process id's pathname refuses the advance and names its by-hand removal, a "
          "stale temporary under another process id blocks nothing and is left in place "
          "unchanged, a failed write, short write, close or replace removes the temporary "
          "and says so, a cleanup that itself fails is reported with the temporary's path "
          "beside the write failure, never swallowed, a creation failure that is not EEXIST "
          "names the actual error without blaming a stale temporary, and an entry planted "
          "after the inspection is replaced as a whole entry, never followed); an empty register "
          "advances nothing and reports no advance, with its anchor entry still inspected "
          "first; the --update-anchor "
          "bootstrap establishes the baseline; a declared attestations register is "
          "verified under the AT- prefix with the optional class and ref fields accepted; "
          "the LIVE leg resolves and git-confirms its root from the gate's own installed "
          "location before reading any registry (an installed gate under an invalid gitfile "
          "or an empty .git directory refuses with exit 2, a caller GIT_DIR and GIT_DIR plus "
          "GIT_WORK_TREE included, and writes no anchor for a declared external register, "
          "while a confirmed root still answers NOT APPLICABLE or bootstraps its anchor); "
          "every PRODUCTION git call (the root probe and the authority reads) runs under the "
          "authority environment (a caller GIT_INDEX_FILE never flips the tracked decision, "
          "every GIT_-prefixed variable is dropped, and only the scrub's exact-valued "
          "config-isolation pins survive, a pin at a caller's own value included among the "
          "dropped; the self-test's fixture setup calls inherit the scrubbed fixture "
          "environment instead); the shared marker walk selects the nearest "
          ".git entry whatever its type (a dangling .git symlink is selected and refused, an "
          "uninspectable marker is cannot-evaluate, never a walk past it, and a nested empty "
          ".git directory is refused by the realpath comparison); a landed check_ref whose "
          "inspection fails with anything but ENOENT is cannot-evaluate through the shared "
          "entry state; and each malformed declaration form (a number, a list, an empty "
          "string, null) is refused with its named per-key diagnostic (a malformed "
          "attestations declaration names its own key)")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    # The LIVE leg shares the appender's root confirmation: the validation root is resolved
    # from this gate's own installed location and git itself must confirm it BEFORE any
    # registry or register is read (grdinp; never a cwd fallback, never a trusted marker).
    try:
        root = gate_root(Path(__file__).resolve().parent)
    except CannotEvaluate as exc:
        print("error: cannot establish the gate's validation root ({}); fail-closed".format(exc))
        return 2
    if root is None:
        print("error: this gate is not installed inside a git repository, so its validation "
              "root cannot be established (the root is resolved from the gate's own location, "
              "never from the invocation directory); fail-closed")
        return 2
    if not confirmed_root(root):
        print("error: the gate's validation root is not a git repository git itself confirms "
              "(rev-parse --show-toplevel under a scrubbed environment must name it; an "
              "invalid gitfile, an empty .git directory or a dangling .git symlink is a "
              "repository marker, not a repository), so neither its registry nor any register "
              "it declares, an external register included, is trusted; fail-closed")
        return 2
    return run(root, update="--update-anchor" in args)


if __name__ == "__main__":
    sys.exit(main())
