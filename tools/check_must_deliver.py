#!/usr/bin/env python3
"""Must-deliver prefix gate: every rule listed in .aiqt/core/must-deliver.toml stays WHOLE inside the
first check_release_build.CAP_BYTES bytes of AGENTS.md.

The first-pin demonstration (tools/gen_first_pin_demo.py) DERIVES its delivered list from AGENTS.md, so
that list shrinks silently when the rules grow and every byte-identity gate stays green: a rule can fall
out of the delivered prefix with no check failing. This gate closes that gap for the rules a maintainer
has declared must stay delivered. The list is hand-edited policy, like .aiqt/core/instruction-budget.toml:
additions may be proposed in any PR and stay free; a removal must be declared (see LIST RATCHET below).

LIST RATCHET. The list is compared with the list at a comparison base: --base REF, or with no flag the
merge base of HEAD and origin/main (no base that resolves is exit 2, never "nothing to compare"). The
default is the merge base, not the origin/main tip, on purpose: it is the commit this branch forked
from, so the comparison sees only the list changes made on this branch, and a rule added to
origin/main after the fork is not reported as dropped by a branch that has not merged it yet. A remote
base is read as its fully qualified ref, never by short name: the default reads exactly
refs/remotes/origin/main, and a --base naming a remote-tracking branch (REF starts with refs/remotes/,
read as REF, or with a configured remote's name and a slash, read as refs/remotes/<REF>; nothing else
names one) is read as exactly that ref, so a tag or a local branch of the same short name, which
git's short-name search order prefers, cannot stand in for it (an absent remote-tracking ref is exit
2). Any other --base (a full or abbreviated object id, HEAD, any other name or expression) is resolved
as git resolves it, after git is asked which ref it resolves the value, and each name leading a ~, ^
or @{ in it, through (git rev-parse --symbolic-full-name with core.warnAmbiguousRefs off, so git names
the ref its own search takes, by whichever of its name rules, refs/remotes/<name>/HEAD included): it
is exit 2 when that ref is under refs/remotes/, or is where a symbolic ref under refs/remotes/ also
ends (git names only the end of a symbolic chain), so a ref under refs/remotes/, plain or symbolic,
cannot stand in for it (remotes/origin/main, which git reads as refs/remotes/origin/main, is exit 2
too); it is also exit 2 as ambiguous when refs/remotes/<REF>, or refs/remotes/<name> for the name
leading an expression, exists, and a full object id whose object does not exist is exit 2. A --base
that is a bare remote name (origin), alone or leading a revision expression (origin~1), and a
revision expression whose leading name is a remote-tracking branch that does not exist (origin/gone~0)
or is shadowed by refs/<name>, refs/tags/<name> or refs/heads/<name> (origin/main~1 with a tag
origin/main), are exit 2: pass the remote-tracking branch, its fully qualified form
(refs/remotes/origin/main~1) or a commit id instead. Every git read runs with replace objects and
grafts off.
tools/run_all_checks.sh runs the bare form, so a
local run compares with that default; CI passes --base (the pull request's target branch, a push's
before SHA, or HEAD^ for any other event when it resolves). The
base file is read from the base commit through the same strict parser as the working file; a base
commit without it, or with it as a symlink or malformed, is exit 2. A rule listed at the base and no
longer listed is exit 1 naming it, unless a [[removal]] row (exactly id and reason) names it; each
such removal is printed with its reason. A [[removal]] row that is duplicated, also listed in [[rule]],
has an empty reason or carries an unknown key is exit 2. Removal rows may stay after the merge.

The spans come from gen_first_pin_demo.measure, the ONE span algorithm the demonstration generator also
uses: AGENTS.md must be byte-identical to a fresh composition of the rule corpus and the block registry
(a stale AGENTS.md is exit 2, never measured), offsets are UTF-8 bytes made absolute against the composed
rule region, and a rule is delivered only when its end offset (exclusive) is at most the cap. This gate
never reads the demonstration's own lists; it recomputes everything. The cap is read from
check_release_build.CAP_BYTES at call time, never copied here.

The declared file carries exactly format-version, warn-margin-bytes and [[rule]] rows of exactly id and
reason, and optionally [[removal]] rows of exactly id and reason; every listed id must be a corpus-id
with a measured span. An absent, unreadable, symlinked, non-UTF-8,
malformed or EMPTY list is exit 2 (a missing input is a failure, never "nothing to check"), per the
check-fails-closed-on-unreadable rule. The gate prints every listed rule's end offset and margin on every
run, and a WARNING (still exit 0) when the tightest margin drops under warn-margin-bytes, so growth is
visible before a listed rule falls out.

DISCLOSED RESIDUAL (disclose-guard-residuals): a PR that removes a rule from the list AND adds a
[[removal]] row for it passes this gate (the row turns a silent deletion into a named, reasoned record the
gate prints; review must judge the removal itself); the ratchet sees only what changed since the base it
is given, so it is as good as that base (a stale or wrong --base compares against the wrong list; an
unshadowed revision expression such as origin/main~1 is still resolved by git's own short-name order,
so a pseudo-ref file of its leading name directly under the git directory, which git tries first, is
not checked; an abbreviated object id is resolved by git, which prefers a ref of the same name outside
refs/remotes/ (refs/<name>, a tag, a branch or a pseudo-ref file; only one git resolves through
refs/remotes/ is refused); a ref git names that a symbolic ref under refs/remotes/ also ends at is
refused even when git reached it directly, an over-refusal; a --base or expression led by a local
branch or tag name, not a remote-tracking one, gets git's tag-before-branch order; and grafts are off
but a .git/shallow file, which also cuts a commit's parents, is read as git reads it, so a write into
the git directory can still move the merge base or a ~ or ^ base through it); a
listed rule cut down to a stub still passes (content is not judged);
the gate measures the repository file against the configured cap, not any platform's actual truncation
behaviour or a budget other instruction files may share; and a client that checks AGENTS.md out with CRLF
line endings inflates what a reader there receives, which this gate does not model (.gitattributes pins
AGENTS.md to eol=lf).

  check_must_deliver.py [--base REF]  check the live tree (default base: merge base of HEAD and
                                      refs/remotes/origin/main)
  check_must_deliver.py --self-test   build synthetic trees and assert each vector is discriminated

Exit convention: 0 every listed rule is delivered and no rule left the list undeclared; 1 a listed rule
ends past the cap, or a rule listed at the base is no longer listed with no [[removal]] row; 2 an
unreadable, malformed or ambiguous input, or a comparison base that does not resolve (fail-closed).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_must_deliver.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import stat
import subprocess
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from gen_rules import CID_RE  # noqa: E402
from gen_first_pin_demo import measure, DemoError  # noqa: E402  the ONE span algorithm, shared
import check_release_build  # noqa: E402  the cap, read as check_release_build.CAP_BYTES at call time

DECL_REL = ".aiqt/core/must-deliver.toml"
TOP_KEYS = frozenset({"format-version", "warn-margin-bytes", "rule"})
OPTIONAL_TOP_KEYS = frozenset({"removal"})
ROW_KEYS = frozenset({"id", "reason"})
REMOVAL_KEYS = frozenset({"id", "reason"})
DEFAULT_BASE_REMOTE = "origin/main"
REMOTE_REF_PREFIX = "refs/remotes/"
GIT_TIMEOUT = 60  # seconds per git call; a call that does not finish is exit 2, never a pass
# GIT_GRAFT_FILE for every git read: a path below the null device, which is not a directory, so it can
# never exist and git reads no grafts at all.
NO_GRAFT_FILE = os.path.join(os.devnull, "aiqt-no-graft-file")


def _read_decl_bytes(path):
    """The one read of the declaration's bytes, a module attribute so the self-test can inject a
    PermissionError here on every runner, including one where a mode-000 file stays readable."""
    return path.read_bytes()


def read_declared(path):
    """The validated must-deliver list: ([rule ids], warn_margin_bytes, {removed id: reason}). Absent, a
    symlink or non-regular entry, unreadable, not UTF-8, not TOML, or outside the exact schema is a
    DemoError (exit 2): the list is the gate's whole policy input, so nothing else may stand in for it."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise DemoError("{} does not exist; the must-deliver policy cannot be read".format(DECL_REL))
    except OSError as exc:
        raise DemoError("cannot stat {} ({})".format(DECL_REL, exc))
    if not stat.S_ISREG(st.st_mode):
        raise DemoError("{} is not a regular file (a symlink, directory or special entry)".format(DECL_REL))
    try:
        raw = _read_decl_bytes(path)
    except OSError as exc:
        raise DemoError("{} is unreadable ({})".format(DECL_REL, exc))
    return parse_declared(raw, DECL_REL)


def parse_declared(raw, label):
    """The one strict parser of the declaration's bytes, shared by the working file and the file at the
    comparison base (label names which in every message). Returns ([rule ids], warn_margin_bytes,
    {removed id: reason}); anything outside the exact schema is a DemoError (exit 2)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DemoError("{} is not valid UTF-8 ({})".format(label, exc))
    try:
        data = tomllib.loads(text)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table; neither is a TOMLDecodeError.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise DemoError("{} does not parse ({})".format(label, exc))
    if not TOP_KEYS <= set(data) or not set(data) <= TOP_KEYS | OPTIONAL_TOP_KEYS:
        raise DemoError("{} must carry exactly the keys {} (and optionally {}), found {}".format(
            label, sorted(TOP_KEYS), sorted(OPTIONAL_TOP_KEYS), sorted(data)))
    version = data["format-version"]
    # The type is checked before the value: TOML 1.0 parses to a float that compares equal to 1, and a
    # bool is an int subclass.
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise DemoError("{}: format-version must be the integer 1, found {!r}".format(label, version))
    warn = data["warn-margin-bytes"]
    if isinstance(warn, bool) or not isinstance(warn, int) or warn < 1:
        raise DemoError("{}: warn-margin-bytes must be a positive integer, found {!r}".format(label, warn))
    rows = data["rule"]
    if not isinstance(rows, list) or not rows:
        raise DemoError("{}: rule must be a non-empty array of [[rule]] tables; an empty list is a "
                        "failure, never \"nothing to check\"".format(label))
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise DemoError("{}: each [[rule]] row must carry exactly the keys {}, found {!r}".format(
                label, sorted(ROW_KEYS), row))
        rid, reason = row["id"], row["reason"]
        if not isinstance(rid, str) or not CID_RE.match(rid):
            raise DemoError("{}: id {!r} is not a well-formed corpus-id".format(label, rid))
        if not isinstance(reason, str) or not reason.strip():
            raise DemoError("{}: rule {} must carry a non-empty reason string".format(label, rid))
        if rid in ids:
            raise DemoError("{}: rule {} is listed more than once".format(label, rid))
        ids.append(rid)
    removals = {}
    if "removal" in data:
        removal_rows = data["removal"]
        if not isinstance(removal_rows, list) or not removal_rows:
            raise DemoError("{}: removal must be a non-empty array of [[removal]] tables (omit the key "
                            "when no removal is recorded)".format(label))
        for row in removal_rows:
            if not isinstance(row, dict) or set(row) != REMOVAL_KEYS:
                raise DemoError("{}: each [[removal]] row must carry exactly the keys {}, found {!r}".format(
                    label, sorted(REMOVAL_KEYS), row))
            rid, reason = row["id"], row["reason"]
            if not isinstance(rid, str) or not CID_RE.match(rid):
                raise DemoError("{}: removal id {!r} is not a well-formed corpus-id".format(label, rid))
            if not isinstance(reason, str) or not reason.strip():
                raise DemoError("{}: removal {} must carry a non-empty reason string".format(label, rid))
            if rid in removals:
                raise DemoError("{}: removal {} is recorded more than once".format(label, rid))
            if rid in ids:
                raise DemoError("{}: removal {} is also listed in [[rule]]; a rule is either listed or "
                                "removed".format(label, rid))
            removals[rid] = reason
    return ids, warn, removals


# --- the comparison base (every git return code is checked; a nonzero exit is never an empty result) --

def _git_env():
    """The ambient environment without any GIT_-prefixed variable, so a GIT_DIR, GIT_INDEX_FILE or
    GIT_OBJECT_DIRECTORY set by a hook or a wrapper cannot point the comparison at another repository,
    with replace refs off so a replacement object cannot stand in for the base commit, and grafts off
    (GIT_GRAFT_FILE names NO_GRAFT_FILE, a path that cannot exist, so <git dir>/info/grafts is never read)
    so a graft cannot give the base, HEAD or origin/main other parents. A .git/shallow file is still read
    as git reads it (the disclosed residual in the module docstring)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = NO_GRAFT_FILE
    return env


def _git(root, args):
    """Run git -C root with args; returns the CompletedProcess (bytes). The three `-c` pairs in option
    position switch off automatic gc and maintenance (tools/_git_fixture_env.py _NO_AUTO_MAINTENANCE),
    the posture tools/check_no_dashes.py takes for its own git reads, so no call here can leave a
    detached repack behind. A missing git binary or a call past GIT_TIMEOUT is a DemoError (exit 2):
    the comparison cannot run."""
    try:
        return subprocess.run(["git", "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                               "-c", "maintenance.auto=false", *args], capture_output=True,
                              env=_git_env(), timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DemoError("git {} could not run ({})".format(args[0], exc))


def _git_err(proc):
    return proc.stderr.decode("utf-8", "replace").strip() or "exit {}".format(proc.returncode)


def _object_id(text):
    """text as a full hexadecimal object id (SHA-1 or SHA-256), or None."""
    oid = text.strip()
    if len(oid) in (40, 64) and all(c in "0123456789abcdef" for c in oid):
        return oid
    return None


def _exact_ref_oid(root, full):
    """The object id the fully qualified ref `full` holds, read with show-ref --verify (that exact ref
    name and no other, never git's short-name search order), or None when there is no such ref."""
    proc = _git(root, ["show-ref", "--verify", "--hash", full])
    return _object_id(proc.stdout.decode("ascii", "replace")) if proc.returncode == 0 else None


def _remotes(root):
    """The configured remote names (git remote); a failing call is a DemoError (exit 2)."""
    proc = _git(root, ["remote"])
    if proc.returncode != 0:
        raise DemoError("git remote failed ({})".format(_git_err(proc)))
    return [r for r in proc.stdout.decode("utf-8", "replace").splitlines() if r]


def _remote_tracking_ref(root, base):
    """refs/remotes/<base> (or base itself when it already starts with refs/remotes/) when base names a
    remote-tracking branch, otherwise None. base names one ONLY when it starts with refs/remotes/ or with
    a configured remote's name and a slash; a ref existing under refs/remotes/ never makes a name
    remote-tracking, so a ref planted at refs/remotes/<name> cannot turn a full or abbreviated object id,
    HEAD or any other name into one (resolve_base refuses such a name as ambiguous,
    _ambiguous_base_refusal). A revision expression that is not a valid ref name (origin/main~1) is not
    a branch name and is left to resolve_base's refusals and then git's own resolution."""
    if base.startswith(REMOTE_REF_PREFIX):
        full = base
    elif any(base.startswith(r + "/") for r in _remotes(root)):
        full = REMOTE_REF_PREFIX + base
    else:
        return None
    return full if _git(root, ["check-ref-format", full]).returncode == 0 else None


REV_OPERATORS = ("~", "^", ":", "@{")


def _leading_name(base):
    """The ref name leading base: base up to its first revision operator (origin/main of origin/main~1)."""
    cut = min([i for i in (base.find(op) for op in REV_OPERATORS) if i >= 0] or [len(base)])
    return base[:cut]


def _ambiguous_base_refusal(root, base):
    """Why base, which names no remote-tracking branch, is ambiguous, or None when it is not. Any such
    value is resolved as git resolves it, and git's short-name search also tries refs/remotes/<name>
    (and prefers a ref over an abbreviated object id), so when refs/remotes/<base>, or refs/remotes/<name>
    for the name leading a revision expression when that name is not itself remote-tracking, exists,
    that ref could stand in for the object id, HEAD or name meant: the base is refused (exit 2), never
    resolved either way."""
    for name in dict.fromkeys((base, _leading_name(base))):
        if not name or _remote_tracking_ref(root, name) is not None:
            continue  # a remote-tracking leading name is _short_name_refusal's to judge
        full = REMOTE_REF_PREFIX + name
        if _exact_ref_oid(root, full) is not None:
            return "{} also exists, so {!r} is ambiguous (that ref could stand in for it); a " \
                   "remote-tracking base is passed as <configured remote>/<branch> or " \
                   "refs/remotes/<remote>/<branch>".format(full, name)
    return None


def _short_name_refusal(root, base):
    """Why base, which names no remote-tracking branch itself, cannot be left to git's short-name search
    order, or None when it can. git resolves a bare remote name (origin) as refs/remotes/origin/HEAD only
    after refs/origin, refs/tags/origin and refs/heads/origin, and a revision expression
    (origin/main~1) through its leading ref name in the same order, so a tag or branch of that short name
    would stand in for the remote-tracking ref. A bare remote name (alone or leading an expression) is
    always refused; an expression whose leading name is a remote-tracking branch is refused when that
    remote-tracking ref does not exist (git would then resolve the name through any other ref its search
    finds, refs/remotes/<name>/HEAD included), and when refs/<name>, refs/tags/<name> or
    refs/heads/<name> exists."""
    cut = min([i for i in (base.find(op) for op in REV_OPERATORS) if i >= 0] or [len(base)])
    lead = base[:cut]
    if lead in _remotes(root):
        return "{!r} is the bare remote name {!r}, which a tag or branch of that name would shadow; " \
               "pass the remote-tracking branch, such as {}/main".format(base, lead, lead)
    if lead == base or not lead:
        return None
    full = _remote_tracking_ref(root, lead)
    if full is None:
        return None
    if _exact_ref_oid(root, full) is None:
        return "the leading ref name {!r} of {!r} names the remote-tracking ref {}, which does not exist, " \
               "so git would resolve the name through another ref; pass a remote-tracking branch that " \
               "exists or a commit id".format(lead, base, full)
    if lead.startswith(REMOTE_REF_PREFIX):
        return None
    shadows =[r for r in ("refs/" + lead, "refs/tags/" + lead, "refs/heads/" + lead)
               if _exact_ref_oid(root, r) is not None]
    if not shadows:
        return None
    return "the leading ref name {!r} of {!r} names {}, but {} of the same short name would stand in for " \
           "it; pass the base as {} or a commit id".format(lead, base, full, " and ".join(shadows),
                                                         full + base[cut:])


def _written_remote_tracking(name, remotes):
    """True when name is written as a remote-tracking branch: it starts with refs/remotes/ or with a
    configured remote's name and a slash (the same text test _remote_tracking_ref applies)."""
    return name.startswith(REMOTE_REF_PREFIX) or any(name.startswith(r + "/") for r in remotes)


def _resolved_names(base):
    """The names git resolves on the way to base: base itself and each part of it that ends right before
    a ~, ^ or @{ outside braces, up to its first ':' outside braces (what follows is a path or search
    text). For origin/main@{u}~1 that is the whole value, origin/main and origin/main@{u}."""
    names = [base]
    depth = 0
    for i, c in enumerate(base):
        if depth == 0:
            if c == ":":
                break
            if c in "~^" or base.startswith("@{", i):
                names.append(base[:i])
        if c == "{":
            depth += 1
        elif c == "}" and depth:
            depth -= 1
    return [n for n in dict.fromkeys(names) if n]


def _git_resolved_refs(root, name):
    """The refs git reports resolving name through: git rev-parse --symbolic-full-name with
    core.warnAmbiguousRefs off, so git names the ref its own short-name search takes (the first that
    exists, by whichever of its name rules, refs/remotes/<name>/HEAD included) instead of declining to
    name one when a later rule also matches. Empty when name is no ref (an object id, a describe form, an
    expression or a name that resolves to nothing). git follows a symbolic ref and names the ref the chain
    ends at. A failing call is a DemoError (exit 2): which ref git takes cannot be established."""
    proc = _git(root, ["-c", "core.warnAmbiguousRefs=false", "rev-parse", "--revs-only",
                       "--symbolic-full-name", name])
    if proc.returncode != 0:
        raise DemoError("git rev-parse --symbolic-full-name {!r} failed ({})".format(name, _git_err(proc)))
    return [line.strip().lstrip("^") for line in proc.stdout.decode("utf-8", "replace").splitlines()
            if line.strip()]


def _remote_symbolic_refs(root):
    """[(symbolic ref, the ref git resolves it to)] for every symbolic ref under refs/remotes/ (git
    for-each-ref, then git rev-parse --symbolic-full-name on each, so a chain is followed as git follows
    it). A failing call is a DemoError (exit 2)."""
    proc = _git(root, ["for-each-ref", "--format=%(refname)%00%(symref)", REMOTE_REF_PREFIX])
    if proc.returncode != 0:
        raise DemoError("git for-each-ref {} failed ({})".format(REMOTE_REF_PREFIX, _git_err(proc)))
    pairs = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        ref, _nul, target = line.partition("\0")
        if ref and target:
            pairs += [(ref, end) for end in _git_resolved_refs(root, ref)]
    return pairs


def _resolved_ref_refusal(root, base):
    """Why base, which is not written as a remote-tracking branch, resolves through a ref under
    refs/remotes/, or None when git reports it does not. git is asked which ref it resolves base, and each
    name leading an operator in it, through (_git_resolved_refs), never this module's copy of git's name
    rules, so a ref under refs/remotes/ that any of git's rules reaches is refused (exit 2): one git names
    directly, and, because git names only the end of a symbolic chain, a symbolic ref under refs/remotes/
    that ends at the ref git names (that ref is then refused even when git reached it directly, a
    fail-closed over-refusal)."""
    remotes = _remotes(root)
    symbolic = None
    hint = "a remote-tracking base is passed as <configured remote>/<branch> or " \
           "refs/remotes/<remote>/<branch>"
    for name in _resolved_names(base):
        if _written_remote_tracking(name, remotes):
            continue  # read exactly, or refused, by resolve_base and _short_name_refusal
        for ref in _git_resolved_refs(root, name):
            if ref.startswith(REMOTE_REF_PREFIX):
                return "git resolves {!r} through a ref under refs/remotes/ ({}), but {!r} is not written " \
                       "as a remote-tracking branch; {}".format(name, ref, base, hint)
            if symbolic is None:
                symbolic = _remote_symbolic_refs(root)
            for sym, end in symbolic:
                if end == ref:
                    return "git may resolve {!r} through a ref under refs/remotes/ (the symbolic ref {}, " \
                           "which ends at {}, the ref git names for it), but {!r} is not written as a " \
                           "remote-tracking branch; {}".format(name, sym, ref, base, hint)
    return None


def _peel_commit(root, rev):
    proc = _git(root, ["rev-parse", "--verify", "--quiet", rev + "^{commit}"])
    return _object_id(proc.stdout.decode("ascii", "replace")) if proc.returncode == 0 else None


def resolve_base(root, base):
    """(commit, label) of the comparison base: the explicit ref, or with base None the merge base of
    HEAD and refs/remotes/origin/main. A remote base is read as its fully qualified ref with show-ref
    --verify, so a tag or a local branch of the same short name (refs/tags/origin/main,
    refs/heads/origin/main), which git's short-name search order prefers, cannot stand in for it. Any
    other base is resolved as git resolves it, except that one (or the name leading it) for which
    refs/remotes/<name> also exists is refused as ambiguous (_ambiguous_base_refusal). A bare remote
    name, and a revision expression whose leading remote-tracking name does not exist or is shadowed by
    a ref of the same short name, are refused (_short_name_refusal), and so is a base git resolves
    through a ref under refs/remotes/ when it is not written as a remote-tracking branch
    (_resolved_ref_refusal). A base that does not resolve to one commit (a full
    object id whose object does not exist included) is a DemoError (exit 2), never "nothing to
    compare"."""
    if base is None:
        full = REMOTE_REF_PREFIX + DEFAULT_BASE_REMOTE
        tip = _exact_ref_oid(root, full)
        if tip is None:
            raise DemoError("no comparison base resolves: {} does not exist; pass --base REF or fetch "
                            "{}".format(full, DEFAULT_BASE_REMOTE))
        proc = _git(root, ["merge-base", "HEAD", tip])
        commit = _object_id(proc.stdout.decode("ascii", "replace")) if proc.returncode == 0 else None
        if commit is None:
            raise DemoError("no comparison base resolves: git merge-base HEAD {} failed ({}); pass "
                            "--base REF or fetch {}".format(full, _git_err(proc), DEFAULT_BASE_REMOTE))
        return commit, "merge base of HEAD and {} ({}, {})".format(DEFAULT_BASE_REMOTE, full, commit[:12])
    if not base or base.startswith("-"):
        raise DemoError("--base {!r} is not a ref".format(base))
    full = _remote_tracking_ref(root, base)
    if full is not None:
        tip = _exact_ref_oid(root, full)
        commit = _peel_commit(root, tip) if tip is not None else None
        if commit is None:
            raise DemoError("cannot resolve the comparison base {!r}: the remote-tracking ref {} does not "
                            "exist or is not a commit".format(base, full))
        return commit, "{} ({}, {})".format(base, full, commit[:12])
    refusal = _ambiguous_base_refusal(root, base)
    if refusal is not None:
        raise DemoError("cannot resolve the comparison base {!r}: {}".format(base, refusal))
    refusal = _short_name_refusal(root, base)
    if refusal is not None:
        raise DemoError("cannot resolve the comparison base {!r} by short name: {}".format(base, refusal))
    refusal = _resolved_ref_refusal(root, base)
    if refusal is not None:
        raise DemoError("cannot resolve the comparison base {!r}: {}".format(base, refusal))
    commit = _peel_commit(root, base)
    if commit is None:
        raise DemoError("cannot resolve the comparison base {!r} to a commit".format(base))
    return commit, "{} ({})".format(base, commit[:12])


def read_base_declared(root, commit, label):
    """The declaration as listed at the base commit, read through parse_declared. A base commit without
    the file, or with it as anything but a regular blob (a symlink, a tree, a submodule), is a DemoError
    (exit 2): the base list is the ratchet's whole reference, so its absence is never an empty list."""
    where = "{} at the base {}".format(DECL_REL, label)
    proc = _git(root, ["ls-tree", "-z", "--full-tree", commit, "--", DECL_REL])
    if proc.returncode != 0:
        raise DemoError("git ls-tree failed for {} ({})".format(where, _git_err(proc)))
    entries = [e for e in proc.stdout.split(b"\0") if e]
    if not entries:
        raise DemoError("{} does not exist; the list at the base cannot be read".format(where))
    meta, _tab, path = entries[0].partition(b"\t")
    fields = meta.split()
    if (len(entries) != 1 or path != DECL_REL.encode("ascii") or len(fields) != 3
            or fields[0] not in (b"100644", b"100755") or fields[1] != b"blob"):
        raise DemoError("{} is not a regular file (a symlink, directory or other entry)".format(where))
    proc = _git(root, ["cat-file", "blob", fields[2].decode("ascii", "replace")])
    if proc.returncode != 0:
        raise DemoError("{} is unreadable ({})".format(where, _git_err(proc)))
    return parse_declared(proc.stdout, where)


def ratchet(root, base):
    """Compare the working list with the list at the comparison base. Exit 0 when every rule listed at
    the base is still listed or named by a [[removal]] row, 1 when one is neither (named), 2 when the
    base does not resolve or either list cannot be read. Additions are free."""
    try:
        ids, _warn, removals = read_declared(root / DECL_REL)
        commit, label = resolve_base(root, base)
        base_ids, _base_warn, _base_removals = read_base_declared(root, commit, label)
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    dropped = [rid for rid in base_ids if rid not in ids]
    undeclared = [rid for rid in dropped if rid not in removals]
    added = [rid for rid in ids if rid not in base_ids]
    print("must-deliver: list ratchet against base {}: {} listed there, {} listed now ({} added, {} "
          "removed)".format(label, len(base_ids), len(ids), len(added), len(dropped)))
    for rid in dropped:
        if rid in removals:
            print("must-deliver: removal: rule {} left the list since the base; [[removal]] reason: {}".format(
                rid, removals[rid]))
    for rid in sorted(removals):
        if rid not in dropped:
            print("must-deliver: removal row kept: rule {} (not listed at the base); reason: {}".format(
                rid, removals[rid]))
    for rid in undeclared:
        print("must-deliver: FAIL: rule {} is listed at the base {} and is no longer listed, with no "
              "[[removal]] row naming it".format(rid, label))
    return 1 if undeclared else 0


def gate(root, base):
    """The whole gate: the prefix check (run) and the list ratchet, both always run so every finding is
    printed. The worse exit wins: 2 over 1 over 0."""
    return max(run(root), ratchet(root, base))


def run(root):
    """Check the tree at root. Exit 0 when every listed rule's whole span lies inside the cap, 1 when a
    listed rule ends past it, 2 on an unreadable, malformed or ambiguous input. Parameterized on root so
    the self-test drives it against synthetic trees, never the real repo."""
    try:
        ids, warn, _removals = read_declared(root / DECL_REL)
        agents, spans, _apex_id = measure(root)
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    cap = check_release_build.CAP_BYTES  # at call time, so the consumer's cap is the only cap
    span_by_id = {rid: (start, end) for rid, start, end in spans}
    missing = [rid for rid in ids if rid not in span_by_id]
    if missing:
        print("error: {} lists {} with no measured rule span (renamed, removed, or never a corpus-id); "
              "fail-closed".format(DECL_REL, ", ".join(missing)), file=sys.stderr)
        return 2
    print("must-deliver: AGENTS.md {} bytes; cap {} (check_release_build.CAP_BYTES)".format(
        len(agents), cap))
    listed = sorted(((cap - span_by_id[rid][1], rid) for rid in ids))
    for margin, rid in listed:
        print("  {:8s}end {:6d}  margin {}".format(rid, span_by_id[rid][1], margin))
    over = [(rid, span_by_id[rid][1]) for margin, rid in listed if margin < 0]
    delivered_count = len(ids) - len(over)
    for rid, end in over:
        print("must-deliver: FAIL: rule {} ends at {}, {} bytes past the cap {}".format(
            rid, end, end - cap, cap))
    tightest_margin, tightest_rid = listed[0]
    if over:
        print("must-deliver: {} of {} listed rules delivered".format(delivered_count, len(ids)))
    else:
        print("must-deliver: {} listed rules delivered; tightest margin {} bytes ({})".format(
            len(ids), tightest_margin, tightest_rid))
    unlisted_over = [(rid, end) for rid, _start, end in spans if rid not in ids and end > cap]
    if unlisted_over:
        rid, end = unlisted_over[0]
        print("must-deliver: first unlisted rule past the cap: {} ends {} ({} past)".format(
            rid, end, end - cap))
    else:
        print("must-deliver: no unlisted rule ends past the cap")
    if not over and tightest_margin < warn:
        print("WARNING: tightest margin {} is below warn-margin-bytes {}".format(tightest_margin, warn))
    return 1 if over else 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    base = None
    if len(args) == 2 and args[0] == "--base":
        base = args[1]
    elif len(args) == 1 and args[0].startswith("--base="):
        base = args[0][len("--base="):]
    elif args:
        print("error: unknown arguments {}; usage: check_must_deliver.py [--base REF] | --self-test".format(
            args), file=sys.stderr)
        return 2
    return gate(repo_root(), base)


# --- self-test ------------------------------------------------------------------------------------
# Synthetic temp trees only, never the real tree, built with gen_first_pin_demo's fixture helpers
# (_write_tree, _tuned) against the real CAP_BYTES. Each vector is a discriminating fixture: it fails
# against a mutant that removes the check it covers (named per vector). Vectors:
#   T1  positive control: a listed rule ending 10 bytes inside the cap is exit 0, its margin line and the
#       tightest-margin summary printed exactly (a mutant with inverted exits, or margin computed as
#       cap - start, fails here);
#   T2  boundaries: a listed rule ending EXACTLY at the cap is exit 0; one ending ONE byte past is exit 1
#       naming the rule and the overage (a mutant testing end < cap fails the first; end <= cap + 1
#       passes the second and fails here);
#   T3  a listed rule STRADDLING the cap is exit 1 (a mutant testing the span START passes a straddler);
#   T4  spans are UTF-8 BYTES: a listed rule whose character span ends inside the cap and whose byte span
#       ends past it is exit 1 (a mutant measuring str offsets passes it);
#   T5  a stale AGENTS.md (corpus edited after it was written) is exit 2 from measure's identity proof,
#       never measured. A mutant without that proof still exits 2 on this fixture, because the edited rule
#       body is then not found in the stale bytes ("body is not found"), so the vector catches it only
#       by asserting the identity proof's own message ("not the gen_agents rendering");
#   T6  a committed demonstration listing the rule as delivered while the fresh spans put it past the cap
#       is exit 1 (a mutant reading delivered-prefix-obligations from the file passes);
#   T7  an id with no span (zzzzzz) and a clause-id (prjint1.1) are each exit 2 naming the id. zzzzzz is
#       well-formed and is refused by run's missing-span check (a mutant that silently skips unknown ids
#       passes it). prjint1.1 is refused earlier, by read_declared's corpus-id shape check (CID_RE); with
#       that check removed the missing-span check still refuses it, so this half fails only when both
#       checks are removed;
#   T8  an absent list, rule = [], and a misspelt [[rules]] table are each exit 2 (a mutant treating an
#       absent or empty list as "nothing to check" returns 0);
#   T9  an unknown row key, an integer id, an empty reason, and a duplicate id are each exit 2 (a mutant
#       ignoring unknown keys or deduplicating silently passes). The top-level values: format-version
#       1.0, 0, 2 and "1", and warn-margin-bytes 0 and -1, are each exit 2 naming the key, while
#       warn-margin-bytes 1 passes (a mutant comparing format-version by value alone passes 1.0, one
#       coercing it with int() passes "1" and 1.0, one without the value check passes 0 and 2, one
#       without the lower bound on warn-margin-bytes passes 0 and -1, and one testing warn < 2 fails 1);
#   T10 a symlinked list, a directory, invalid UTF-8, bad TOML, an injected PermissionError at the
#       declaration read, and a mode-000 list are each exit 2 (a catch-all returning 0 passes them all;
#       a read error not reported as unreadable fails the last two). The symlink case asserts the
#       regular-file check's message; the directory case does not, because without that check the read
#       raises IsADirectoryError, which also maps to exit 2. The injected error runs on every runner; the
#       mode-000 case runs only where the mode binds the runner, and the PASS line names it when skipped;
#   T11 the cap is read from check_release_build.CAP_BYTES at call time: patching it lower flips the
#       verdict to exit 1 (a mutant with a locally copied cap keeps exit 0);
#   T12 a before-rules registry block pushing a listed rule past the cap is exit 1 (a mutant reading
#       offsets relative to the rule region, without the rules_start shift, passes);
#   T13 the margin report: the unlisted-rule line, and a WARNING line under warn-margin-bytes that still
#       exits 0 (a mutant whose warning exits non-zero, or margin off by one, fails).
# The list ratchet (T14 to T35) runs gate() against temporary git repositories built with the same
# helpers and committed through _git_fixture_env's hermetic environment, the whole self-test inside
# fixture_git_lifecycle so the gate's own base reads see no caller git configuration either:
#   T14 the base lists ruleaa and the working list drops it with no [[removal]] row: exit 1 naming ruleaa
#       (a mutant that never reads the base, comparing the working list with itself, passes);
#   T15 the same drop with a [[removal]] row naming ruleaa: exit 0, the removal and its reason printed (a
#       mutant that rejects every removal fails);
#   T16 a [[removal]] row with an empty reason, an extra key, a duplicate, or an id also in [[rule]]: each
#       exit 2 with its own message (a mutant without that one check exits 0);
#   T17 a --base that does not resolve, and no origin/main with no --base: each exit 2; with origin/main
#       present and no --base, the merge base is read and the drop is exit 1 (a mutant treating a missing
#       base as nothing to compare passes the first two);
#   T18 a base commit without the list, and one whose list is malformed: each exit 2 (a mutant treating an
#       absent or unreadable base list as nothing to compare passes);
#   T19 the working list adds an id the base does not list: exit 0 (a mutant failing on any difference
#       between the two lists fails);
#   T20 ruleaa grows past the cap, the working list drops it, and the first-pin demonstration is
#       regenerated without it: exit 1 naming ruleaa from the ratchet, with no prefix FAIL line (a mutant
#       comparing against the demonstration's delivered list instead of the base list passes);
#   T21 the base lists ruleaa, a later commit (HEAD) drops it with no [[removal]] row and the working list
#       matches HEAD: against the base, exit 1 naming ruleaa (a mutant that resolves --base but reads the
#       list at HEAD sees no drop and passes);
#   T22 refs/remotes/origin/main is the base listing ruleaa under a configured remote origin, and a
#       local branch and then also a tag, each named origin/main, point at a later commit (HEAD) that drops it: with no --base and with --base
#       origin/main, exit 1 naming ruleaa and naming refs/remotes/origin/main (a mutant resolving the
#       short name lets the branch or the tag stand in, sees no drop, and passes);
#   T23 a base commit whose list is a symlink: exit 2 naming it not a regular file (a mutant without the
#       blob-mode check reads the link's target text and fails for another reason);
#   T24 GIT_DIR in the caller's environment points at a decoy repository whose origin/main lists no
#       ruleaa: the base is still read from the fixture repository and the drop is exit 1 (a mutant that
#       keeps GIT_* variables reads the decoy and passes);
#   T25 a replace ref substitutes, for the base commit, a commit that drops ruleaa: the base is still read
#       as committed and the drop is exit 1 (a mutant that leaves replace objects on passes);
#   T26 a --base value starting with - (-h, --git-dir=/nonexistent): each exit 2 naming it not a ref (a
#       mutant accepting it hands it to git as an option and fails for another reason);
#   T27 the base lists ruleaa and HEAD drops it: --base <base id> is exit 1; with a ref
#       refs/remotes/<base id> planted at HEAD, --base <base id> is exit 2 naming it ambiguous (a mutant
#       looking a full object id up under refs/remotes/ reads HEAD's list and passes; the round 4 rule,
#       reading the id as that commit, is exit 1 and fails too);
#   T28 refs/remotes/origin/main (and origin/HEAD) is the base listing ruleaa under a configured remote
#       origin, and HEAD drops it: --base origin/main~0 is exit 1 unshadowed; with a tag origin at HEAD,
#       --base origin and origin~0 are exit 2 naming the bare remote name (a mutant without that refusal
#       lets the tag stand in and passes); with a branch and then also a tag origin/main at HEAD,
#       origin/main~0 and origin/main^0 are exit 2 naming the shadow (a mutant without the shadow
#       refusal lets the branch or tag stand in and passes);
#   T29 the configured-remote fallback: origin is configured, refs/remotes/origin/gone is absent and a tag
#       origin/gone points at a commit that drops ruleaa: --base origin/gone is exit 2 naming the absent
#       remote-tracking ref (a mutant without the fallback resolves the tag and passes);
#   T30 a full-length id whose object does not exist: exit 2 unplanted; with refs/remotes/<that id>
#       planted at HEAD, which drops ruleaa, exit 2 naming it ambiguous (a mutant treating a name with a
#       ref under refs/remotes/ as remote-tracking reads HEAD's list and passes);
#   T31 HEAD lists ruleaa and the working list drops it: --base HEAD is exit 1; with refs/remotes/HEAD
#       planted at a commit that drops ruleaa, --base HEAD is exit 2 naming it ambiguous (the same mutant
#       reads the planted commit and passes);
#   T32 an abbreviated id of the base, with HEAD dropping ruleaa: exit 1; with refs/remotes/<abbreviation>
#       planted at HEAD, the abbreviation and <abbreviation>~0 are each exit 2 naming it ambiguous (the
#       same mutant passes the first; one checking the whole value but not the name leading an
#       expression lets git prefer the planted ref for the second and passes);
#   T33 an abbreviated id of the base, with HEAD dropping ruleaa: with refs/remotes/<abbreviation>/HEAD
#       planted at HEAD, the abbreviation, <abbreviation>~0 and <abbreviation>^0 are each exit 2 naming
#       that ref; the describe form x-0-g<abbreviation> is exit 1 unplanted and exit 2 with
#       refs/remotes/x-0-g<abbreviation>/HEAD planted; with refs/remotes/<abbreviation>/HEAD a symbolic
#       ref to a branch at HEAD, the abbreviation and <abbreviation>~0 are each exit 2 naming it (a
#       mutant without _resolved_ref_refusal lets git take the planted ref and passes every case; one
#       refusing only a ref git names under refs/remotes/, without the symbolic-ref check, passes the
#       symbolic cases);
#   T34 origin is configured, refs/remotes/origin/absent is absent and refs/remotes/origin/absent/HEAD is
#       planted at HEAD, which drops ruleaa: origin/absent~0 and origin/absent^0 are each exit 2 naming
#       the absent remote-tracking ref (a mutant without the leading-ref existence check lets git take
#       the planted /HEAD and passes);
#   T35 a grafts file gives the base (refs/remotes/origin/main) and HEAD, which drops ruleaa, one
#       parentless commit that drops ruleaa as their parent: with no --base and with --base HEAD^, the
#       base is still read as committed and the drop is exit 1 (a mutant that leaves grafts on reads
#       the grafted commit as the merge base or HEAD^ and passes).

import gen_first_pin_demo as gfpd  # noqa: E402  fixture helpers (_write_tree, _tuned) and CAP_BYTES


class _FixtureError(Exception):
    """A self-test fixture's own git command failed: the vector cannot run (exit 2, never a pass)."""


def _decl(root, rows, warn=512, raw=None):
    path = root / Path(DECL_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        lines = ["format-version = 1", "warn-margin-bytes = {}".format(warn)]
        for rid, reason in rows:
            lines += ["", "[[rule]]", 'id = {!r}'.format(rid).replace("'", '"'),
                      'reason = {!r}'.format(reason).replace("'", '"')]
        raw = "\n".join(lines) + "\n"
    path.write_text(raw, encoding="utf-8")
    return path


def self_test_main():
    """The self-test runs inside _git_fixture_env.fixture_git_lifecycle, as the sibling gates' self-tests
    do: HOME, XDG_CONFIG_HOME, GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM are pinned to fixture-owned
    values for the whole run, and its PATH wrapper reasserts the system-config pin after _git_env drops
    every GIT_ variable, so neither the fixture git calls nor the gate's own base reads under test can
    read the caller's git configuration."""
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle():
        return _self_test_main_isolated()


def _self_test_main_isolated():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr
    from _git_fixture_env import git_fixture_env

    CAP = check_release_build.CAP_BYTES

    def capture(root):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = run(root)
        return code, buf.getvalue()

    def capture_gate(root, base):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = gate(root, base)
        return code, buf.getvalue()

    def fixture_git(root, *args):
        env = git_fixture_env(GIT_AUTHOR_NAME="must-deliver self-test",
                              GIT_AUTHOR_EMAIL="selftest@example.invalid",
                              GIT_COMMITTER_NAME="must-deliver self-test",
                              GIT_COMMITTER_EMAIL="selftest@example.invalid")
        try:
            proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env,
                                  timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise _FixtureError("fixture git {} could not run ({})".format(args[0], exc))
        if proc.returncode != 0:
            raise _FixtureError("fixture git {} failed in {}: {}".format(args[0], root, _git_err(proc)))
        return proc.stdout.decode("utf-8", "replace").strip()

    def commit_base(root):
        """Commit the tree at root as the fixture's one commit; returns its object id."""
        fixture_git(root, "init", "-q")
        fixture_git(root, "add", "-A")
        fixture_git(root, "commit", "-q", "-m", "fixture base")
        return fixture_git(root, "rev-parse", "HEAD")

    def raw_decl(rules, removals=(), extra=""):
        lines = ["format-version = 1", "warn-margin-bytes = 512"]
        for rid in rules:
            lines += ["", "[[rule]]", 'id = "{}"'.format(rid), 'reason = "fixture rule"']
        for rid, reason in removals:
            lines += ["", "[[removal]]", 'id = "{}"'.format(rid), 'reason = "{}"'.format(reason)]
        return "\n".join(lines) + "\n" + extra

    failures = []
    skipped = []
    fixture_error = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-check-must-deliver-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        base_specs = [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400)]
        both = [("prjint1", "the apex"), ("ruleaa", "fixture rule")]

        # T1. Positive control: ruleaa ends CAP - 10; margin lines and the summary are exact.
        t1 = tmp / "t1"
        sp = gfpd._tuned(t1, base_specs, "ruleaa", CAP - 10)
        _decl(t1, both)
        code, out = capture(t1)
        if code != 0:
            failures.append("T1: expected exit 0, got {}\n{}".format(code, out))
        if "ruleaa  end {:6d}  margin 10".format(sp["ruleaa"][1]) not in out:
            failures.append("T1: the ruleaa margin line is absent or wrong:\n{}".format(out))
        if "must-deliver: 2 listed rules delivered; tightest margin 10 bytes (ruleaa)" not in out:
            failures.append("T1: the tightest-margin summary is absent or wrong:\n{}".format(out))

        # T2. Boundaries: end == CAP passes; end == CAP + 1 fails naming the rule and the overage.
        t2 = tmp / "t2"
        sp = gfpd._tuned(t2, base_specs, "ruleaa", CAP)
        _decl(t2, both)
        code, out = capture(t2)
        if sp["ruleaa"][1] != CAP or code != 0:
            failures.append("T2: a rule ending exactly at the cap must pass (fixture end {}, exit {})\n{}"
                            .format(sp["ruleaa"][1], code, out))
        sp = gfpd._tuned(t2, base_specs, "ruleaa", CAP + 1)
        code, out = capture(t2)
        if sp["ruleaa"][1] != CAP + 1 or code != 1:
            failures.append("T2: a rule ending one byte past the cap must fail (fixture end {}, exit {})\n{}"
                            .format(sp["ruleaa"][1], code, out))
        elif "FAIL: rule ruleaa ends at {}, 1 bytes past the cap {}".format(CAP + 1, CAP) not in out:
            failures.append("T2: the failure does not name the rule and its overage:\n{}".format(out))

        # T3. A listed straddler fails. rulebb starts inside the cap and ends past it.
        t3 = tmp / "t3"
        sp = gfpd._tuned(t3, base_specs, "ruleaa", CAP - 10)
        if not (sp["rulebb"][0] < CAP < sp["rulebb"][1]):
            failures.append("T3: fixture did not straddle the cap: {}".format(sp))
        _decl(t3, both + [("rulebb", "straddles")])
        code, out = capture(t3)
        if code != 1 or "FAIL: rule rulebb" not in out:
            failures.append("T3: a listed straddling rule must be exit 1 naming it, got {}\n{}".format(
                code, out))

        # T4. Bytes, not characters: 100 U+00E9 in a before-rules block (a rule body is ASCII) put ruleaa's
        # character end inside the cap and its byte end past it (the generator's own multi-byte vector).
        t4 = tmp / "t4"
        two = "é"
        mb_block = ("OPF-SELFTEST", "AGENTS.md", "before-rules", 1, "opf/blocks/selftest.md", two * 100 + "\n")
        mb_registry = gfpd.adapter_compose.registry_text([("AGENTS.md", "composed")], [mb_block])

        def mb_specs(fill):
            return [("prjint1", True, 0), ("ruleaa", False, fill), ("rulebb", False, 100)]

        sp = gfpd._write_tree(t4, mb_specs(20000), mb_registry, [mb_block])
        sp = gfpd._write_tree(t4, mb_specs(20000 + CAP + 50 - sp["ruleaa"][1]), mb_registry, [mb_block])
        raw = (t4 / "AGENTS.md").read_bytes()
        char_end = len(raw[:sp["ruleaa"][1]].decode("utf-8"))
        if not (char_end <= CAP < sp["ruleaa"][1]):
            failures.append("T4: fixture did not split characters from bytes: char end {}, byte span {}"
                            .format(char_end, sp["ruleaa"]))
        _decl(t4, both)
        code, out = capture(t4)
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T4: byte-measured spans must fail ruleaa (char span inside the cap), got {}\n{}"
                            .format(code, out))

        # T5. A stale AGENTS.md is exit 2: the corpus changes after AGENTS.md was written.
        t5 = tmp / "t5"
        gfpd._tuned(t5, base_specs, "ruleaa", CAP - 10)
        agents_before = (t5 / "AGENTS.md").read_bytes()
        rule_src = t5 / ".aiqt" / "core" / "rules" / "ruleaa.md"
        rule_src.write_text(rule_src.read_text(encoding="utf-8") + "A fresh sentence.\n", encoding="utf-8")
        (t5 / "AGENTS.md").write_bytes(agents_before)
        _decl(t5, both)
        code, out = capture(t5)
        if code != 2 or "not the gen_agents rendering" not in out:
            failures.append("T5: a stale AGENTS.md must be exit 2, never measured, got {}\n{}".format(
                code, out))

        # T6. A committed demonstration claiming ruleaa delivered is ignored: fresh spans decide.
        t6 = tmp / "t6"
        gfpd._tuned(t6, base_specs, "ruleaa", CAP + 50)
        demo = t6 / Path(gfpd.DEMO_REL)
        demo.parent.mkdir(parents=True, exist_ok=True)
        demo.write_text('agents-sha256 = "{}"\ndelivered-prefix-obligations = ["prjint1", "ruleaa"]\n'
                        'floor-profile-obligations = ["prjint1", "ruleaa"]\n'.format("0" * 64),
                        encoding="utf-8")
        _decl(t6, both)
        code, out = capture(t6)
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T6: the demonstration's delivered list must not be read; fresh spans must "
                            "fail ruleaa, got {}\n{}".format(code, out))

        # T7. An id with no span, and a clause-id, are each exit 2 naming the id.
        t7 = tmp / "t7"
        gfpd._tuned(t7, base_specs, "ruleaa", CAP - 10)
        for bad in ("zzzzzz", "prjint1.1"):
            _decl(t7, both + [(bad, "no such rule")])
            code, out = capture(t7)
            if code != 2 or bad not in out:
                failures.append("T7: id {!r} must be exit 2 naming it, got {}\n{}".format(bad, code, out))

        # T8. Absent list, empty rule array, misspelt table: each exit 2.
        t8 = tmp / "t8"
        gfpd._tuned(t8, base_specs, "ruleaa", CAP - 10)
        code, out = capture(t8)  # no must-deliver.toml written yet
        if code != 2 or "does not exist" not in out:
            failures.append("T8: an absent list must be exit 2, got {}\n{}".format(code, out))
        _decl(t8, [], raw="format-version = 1\nwarn-margin-bytes = 512\nrule = []\n")
        code, out = capture(t8)
        if code != 2 or "non-empty" not in out:
            failures.append("T8: an empty rule array must be exit 2, got {}\n{}".format(code, out))
        _decl(t8, [], raw='format-version = 1\nwarn-margin-bytes = 512\n[[rules]]\nid = "prjint1"\n'
                          'reason = "x"\n')
        code, out = capture(t8)
        if code != 2:
            failures.append("T8: a misspelt [[rules]] table must be exit 2, got {}\n{}".format(code, out))

        # T9. Unknown row key, integer id, empty reason, duplicate id, and out-of-schema top-level values:
        # each exit 2; warn-margin-bytes 1, the smallest accepted value, passes.
        t9 = tmp / "t9"
        gfpd._tuned(t9, base_specs, "ruleaa", CAP - 10)
        bad_rows = (
            ("unknown key", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = "prjint1"\n'
                            'reason = "x"\nextra = 1\n'),
            ("integer id", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = 1\nreason = "x"\n'),
            ("empty reason", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = "prjint1"\n'
                             'reason = " "\n'),
            ("duplicate id", 'format-version = 1\nwarn-margin-bytes = 512\n[[rule]]\nid = "prjint1"\n'
                             'reason = "x"\n[[rule]]\nid = "prjint1"\nreason = "y"\n'),
            ("unknown top-level key", 'format-version = 1\nwarn-margin-bytes = 512\ncap = 1\n[[rule]]\n'
                                      'id = "prjint1"\nreason = "x"\n'),
            ("boolean format-version", 'format-version = true\nwarn-margin-bytes = 512\n[[rule]]\n'
                                       'id = "prjint1"\nreason = "x"\n'),
            ("boolean warn margin", 'format-version = 1\nwarn-margin-bytes = true\n[[rule]]\n'
                                    'id = "prjint1"\nreason = "x"\n'),
        )
        for label, raw in bad_rows:
            _decl(t9, [], raw=raw)
            code, out = capture(t9)
            if code != 2:
                failures.append("T9 {}: expected exit 2, got {}\n{}".format(label, code, out))
        version_msg = "format-version must be the integer 1"
        warn_msg = "warn-margin-bytes must be a positive integer"
        value_rows = (
            ("float format-version 1.0", "format-version = 1.0\nwarn-margin-bytes = 512\n", version_msg),
            ("format-version 0", "format-version = 0\nwarn-margin-bytes = 512\n", version_msg),
            ("format-version 2", "format-version = 2\nwarn-margin-bytes = 512\n", version_msg),
            ("string format-version", 'format-version = "1"\nwarn-margin-bytes = 512\n', version_msg),
            ("warn margin 0", "format-version = 1\nwarn-margin-bytes = 0\n", warn_msg),
            ("warn margin -1", "format-version = 1\nwarn-margin-bytes = -1\n", warn_msg),
        )
        for label, head, want in value_rows:
            _decl(t9, [], raw=head + '[[rule]]\nid = "prjint1"\nreason = "x"\n')
            code, out = capture(t9)
            if code != 2 or want not in out:
                failures.append("T9 {}: expected exit 2 with {!r}, got {}\n{}".format(label, want, code, out))
        _decl(t9, both, warn=1)  # the smallest positive margin is accepted
        code, out = capture(t9)
        if code != 0:
            failures.append("T9 warn margin 1: expected exit 0, got {}\n{}".format(code, out))

        # T10. Symlink, directory, invalid UTF-8, bad TOML, injected read error, mode 000: each exit 2.
        t10 = tmp / "t10"
        gfpd._tuned(t10, base_specs, "ruleaa", CAP - 10)
        decl = _decl(t10, both)
        aside = t10 / "aside.toml"
        aside.write_text(decl.read_text(encoding="utf-8"), encoding="utf-8")
        decl.unlink()
        os.symlink(aside, decl)
        code, out = capture(t10)
        if code != 2 or "not a regular file" not in out:
            failures.append("T10: a symlinked list must be exit 2, got {}\n{}".format(code, out))
        decl.unlink()
        decl.mkdir()
        code, out = capture(t10)
        if code != 2:
            failures.append("T10: a directory at the list path must be exit 2, got {}\n{}".format(code, out))
        decl.rmdir()
        decl.write_bytes(b"format-version = 1\n\xff\n")
        code, out = capture(t10)
        if code != 2 or "not valid UTF-8" not in out:
            failures.append("T10: an invalid-UTF-8 list must be exit 2, got {}\n{}".format(code, out))
        decl.write_text("format-version = = 1\n", encoding="utf-8")
        code, out = capture(t10)
        if code != 2 or "does not parse" not in out:
            failures.append("T10: a non-TOML list must be exit 2, got {}\n{}".format(code, out))
        _decl(t10, both)
        # The injected PermissionError at the declaration read runs on every runner.
        this = sys.modules[__name__]
        real_read = this._read_decl_bytes

        def denied(path):
            raise PermissionError(13, "Permission denied (injected by the self-test)", str(path))

        this._read_decl_bytes = denied
        try:
            code, out = capture(t10)
        finally:
            this._read_decl_bytes = real_read
        if code != 2 or "is unreadable" not in out or "injected by the self-test" not in out:
            failures.append("T10: an injected PermissionError at the declaration read must be exit 2 "
                            "reporting the list unreadable, got {}\n{}".format(code, out))
        os.chmod(decl, 0)
        try:
            if os.access(decl, os.R_OK):  # root or a DAC bypass: mode 000 is still readable
                print("SELF-TEST NOTE: mode-000 vector not applicable (file still readable)")
                skipped.append("the filesystem mode-000 list (this runner can read a mode-000 file; the "
                               "injected PermissionError covered the unreadable case)")
            else:
                code, out = capture(t10)
                if code != 2 or "is unreadable" not in out:
                    failures.append("T10: a mode-000 list must be exit 2, got {}\n{}".format(code, out))
        finally:
            os.chmod(decl, 0o644)

        # T11. The cap is check_release_build.CAP_BYTES at call time: patched lower, the verdict flips.
        t11 = tmp / "t11"
        gfpd._tuned(t11, base_specs, "ruleaa", CAP - 10)
        _decl(t11, both)
        code, out = capture(t11)
        if code != 0:
            failures.append("T11: the unpatched leg must pass, got {}\n{}".format(code, out))
        check_release_build.CAP_BYTES = CAP - 100
        try:
            code, out = capture(t11)
        finally:
            check_release_build.CAP_BYTES = CAP
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T11: lowering check_release_build.CAP_BYTES must flip the verdict to "
                            "exit 1, got {}\n{}".format(code, out))

        # T12. A before-rules registry block pushes ruleaa (CAP - 10 in the legacy layout) past the cap.
        t12 = tmp / "t12"
        specs = base_specs
        delta = CAP - 10 - gfpd._write_tree(t12, specs)["ruleaa"][1]
        specs = [(c, a, p + delta if c == "ruleaa" else p) for c, a, p in specs]
        before = ("OPF-SELFTEST", "AGENTS.md", "before-rules", 1, "opf/blocks/selftest.md",
                  "A reviewed block placed before the rules.\n")
        sp = gfpd._write_tree(t12, specs,
                              gfpd.adapter_compose.registry_text([("AGENTS.md", "composed")], [before]),
                              [before])
        if not sp["ruleaa"][1] > CAP:
            failures.append("T12: the before-rules block did not push ruleaa past the cap: {}".format(sp))
        _decl(t12, both)
        code, out = capture(t12)
        if code != 1 or "FAIL: rule ruleaa" not in out:
            failures.append("T12: absolute offsets must fail ruleaa once a before-rules block pushes it "
                            "past the cap, got {}\n{}".format(code, out))

        # T13. The unlisted-rule report and the warning: under warn-margin-bytes is a WARNING, still 0.
        t13 = tmp / "t13"
        sp = gfpd._tuned(t13, [("prjint1", True, 0), ("ruleaa", False, 20000), ("rulebb", False, 400),
                               ("rulecc", False, 30000)], "ruleaa", CAP - 10)
        _decl(t13, both, warn=64)
        code, out = capture(t13)
        if code != 0:
            failures.append("T13: expected exit 0, got {}\n{}".format(code, out))
        if "WARNING: tightest margin 10 is below warn-margin-bytes 64" not in out:
            failures.append("T13: the warning line is absent or wrong:\n{}".format(out))
        want = "first unlisted rule past the cap: rulebb ends {} ({} past)".format(
            sp["rulebb"][1], sp["rulebb"][1] - CAP)
        if want not in out:
            failures.append("T13: the unlisted-rule line is absent or wrong (want {!r}):\n{}".format(
                want, out))
        _decl(t13, both, warn=10)  # margin 10 is NOT below 10: no warning
        code, out = capture(t13)
        if code != 0 or "WARNING" in out:
            failures.append("T13: margin equal to warn-margin-bytes must not warn, got {}\n{}".format(
                code, out))

        # The list ratchet. Each fixture commits a base listing prjint1 and ruleaa (ruleaa CAP - 10),
        # then edits the working list; the base is passed as its full object id unless stated.
        drop_msg = "FAIL: rule ruleaa is listed at the base"

        def ratchet_fixture(name, base_rules=("prjint1", "ruleaa")):
            root = tmp / name
            gfpd._tuned(root, base_specs, "ruleaa", CAP - 10)
            _decl(root, [], raw=raw_decl(base_rules))
            return root, commit_base(root)

        # T14. Dropped with no [[removal]] row: exit 1 naming ruleaa.
        t14, base14 = ratchet_fixture("t14")
        _decl(t14, [], raw=raw_decl(["prjint1"]))
        code, out = capture_gate(t14, base14)
        if code != 1 or drop_msg not in out:
            failures.append("T14: a rule dropped from the list with no [[removal]] row must be exit 1 "
                            "naming it, got {}\n{}".format(code, out))

        # T15. Dropped with a [[removal]] row: exit 0, the removal and its reason printed.
        t15, base15 = ratchet_fixture("t15")
        _decl(t15, [], raw=raw_decl(["prjint1"], [("ruleaa", "fixture removal reason")]))
        code, out = capture_gate(t15, base15)
        if (code != 0 or "removal: rule ruleaa left the list since the base; [[removal]] reason: "
                         "fixture removal reason" not in out):
            failures.append("T15: a drop with a [[removal]] row must be exit 0 printing the removal, got "
                            "{}\n{}".format(code, out))

        # T16. Malformed [[removal]] rows: each exit 2 with its own message.
        t16, base16 = ratchet_fixture("t16")
        bad_removals = (
            ("empty reason", raw_decl(["prjint1"], [("ruleaa", " ")]),
             "removal ruleaa must carry a non-empty reason"),
            ("extra key", raw_decl(["prjint1"], [("ruleaa", "x")], extra="approved = true\n"),
             "each [[removal]] row must carry exactly the keys"),
            ("duplicate", raw_decl(["prjint1"], [("ruleaa", "x"), ("ruleaa", "y")]),
             "removal ruleaa is recorded more than once"),
            ("also in [[rule]]", raw_decl(["prjint1", "ruleaa"], [("ruleaa", "x")]),
             "removal ruleaa is also listed in [[rule]]"),
        )
        for label, raw, want in bad_removals:
            _decl(t16, [], raw=raw)
            code, out = capture_gate(t16, base16)
            if code != 2 or want not in out:
                failures.append("T16 {}: expected exit 2 with {!r}, got {}\n{}".format(label, want, code, out))

        # T17. An unresolvable --base, and no origin/main with no --base: each exit 2. With origin/main
        # present and no --base, the merge base is the comparison base and the drop is caught.
        t17, base17 = ratchet_fixture("t17")
        _decl(t17, [], raw=raw_decl(["prjint1"]))
        code, out = capture_gate(t17, "no-such-ref")
        if code != 2 or "cannot resolve the comparison base 'no-such-ref'" not in out:
            failures.append("T17: an unresolvable --base must be exit 2, got {}\n{}".format(code, out))
        code, out = capture_gate(t17, None)
        if code != 2 or "no comparison base resolves" not in out:
            failures.append("T17: no origin/main and no --base must be exit 2, got {}\n{}".format(code, out))
        fixture_git(t17, "update-ref", "refs/remotes/origin/main", base17)
        code, out = capture_gate(t17, None)
        if code != 1 or drop_msg not in out or "merge base of HEAD and origin/main" not in out:
            failures.append("T17: with origin/main present the default base must catch the drop (exit 1), "
                            "got {}\n{}".format(code, out))

        # T18. A base commit without the list, and one whose list is malformed: each exit 2.
        t18 = tmp / "t18"
        gfpd._tuned(t18, base_specs, "ruleaa", CAP - 10)
        base18 = commit_base(t18)  # no must-deliver.toml at the base
        _decl(t18, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t18, base18)
        if code != 2 or "at the base" not in out or "does not exist" not in out:
            failures.append("T18: a base commit without the list must be exit 2, got {}\n{}".format(code, out))
        _decl(t18, [], raw="format-version = 1\nwarn-margin-bytes = 512\nrule = []\n")
        fixture_git(t18, "add", "-A")
        fixture_git(t18, "commit", "-q", "-m", "fixture malformed base")
        base18b = fixture_git(t18, "rev-parse", "HEAD")
        _decl(t18, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t18, base18b)
        if code != 2 or "at the base" not in out or "non-empty" not in out:
            failures.append("T18: a malformed list at the base must be exit 2, got {}\n{}".format(code, out))

        # T19. An addition is free: the base lists prjint1 only, the working list adds ruleaa.
        t19, base19 = ratchet_fixture("t19", base_rules=("prjint1",))
        _decl(t19, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t19, base19)
        if code != 0 or "(1 added, 0 removed)" not in out:
            failures.append("T19: adding a listed rule must be exit 0, got {}\n{}".format(code, out))

        # T20. ruleaa grows past the cap; the working list drops it and the demonstration is regenerated
        # without it, so every byte-identity surface agrees. Only the ratchet still fails, naming ruleaa.
        t20 = tmp / "t20"
        gfpd._tuned(t20, base_specs, "ruleaa", CAP - 10)
        demo20 = t20 / Path(gfpd.DEMO_REL)
        demo20.parent.mkdir(parents=True, exist_ok=True)
        demo20.write_text(gfpd.build(t20), encoding="utf-8")

        def demo_delivered():
            """The demonstration's delivered list, or None when it does not parse (a T20 failure)."""
            try:
                return tomllib.loads(demo20.read_text(encoding="utf-8"))["delivered-prefix-obligations"]
            except (ValueError, RecursionError, MemoryError, KeyError):
                return None

        delivered = demo_delivered()
        if delivered is None or "ruleaa" not in delivered:
            failures.append("T20: the base demonstration does not list ruleaa as delivered")
        _decl(t20, [], raw=raw_decl(["prjint1", "ruleaa"]))
        base20 = commit_base(t20)
        gfpd._tuned(t20, base_specs, "ruleaa", CAP + 50)
        demo20.write_text(gfpd.build(t20), encoding="utf-8")
        _decl(t20, [], raw=raw_decl(["prjint1"]))
        code, out = capture_gate(t20, base20)
        delivered = demo_delivered()
        if delivered is None or "ruleaa" in delivered:
            failures.append("T20: the regenerated demonstration still lists ruleaa as delivered")
        if code != 1 or drop_msg not in out or "FAIL: rule ruleaa ends at" in out:
            failures.append("T20: a rule dropped from the list together with a regenerated demonstration "
                            "must be exit 1 from the ratchet naming it, got {}\n{}".format(code, out))

        # T21. The list is read from the named base, not HEAD: the base lists ruleaa, a later commit (HEAD)
        # drops it with no [[removal]] row, and the working list matches HEAD. Against the base the drop is
        # exit 1; a gate reading HEAD's list instead sees no drop and exits 0.
        t21, base21 = ratchet_fixture("t21")
        _decl(t21, [], raw=raw_decl(["prjint1"]))
        fixture_git(t21, "add", "-A")
        fixture_git(t21, "commit", "-q", "-m", "fixture head drops ruleaa")
        head21 = fixture_git(t21, "rev-parse", "HEAD")
        if head21 == base21:
            failures.append("T21: the fixture HEAD did not move past the base ({})".format(base21))
        code, out = capture_gate(t21, base21)
        if code != 1 or drop_msg not in out or "base {} ({})".format(base21, base21[:12]) not in out:
            failures.append("T21: with HEAD past the base, the list must be read at the named base and the "
                            "drop be exit 1 naming ruleaa, got {}\n{}".format(code, out))

        # T22. refs/remotes/origin/main lists ruleaa; a local branch, then also a tag, named origin/main
        # point at HEAD, which drops it. The default base and --base origin/main must still read the
        # remote-tracking ref; git's short-name order would pick the tag, then the branch.
        t22, base22 = ratchet_fixture("t22")
        fixture_git(t22, "remote", "add", "origin", str(tmp / "t22-nowhere"))
        fixture_git(t22, "update-ref", "refs/remotes/origin/main", base22)
        _decl(t22, [], raw=raw_decl(["prjint1"]))
        fixture_git(t22, "add", "-A")
        fixture_git(t22, "commit", "-q", "-m", "fixture head drops ruleaa")
        head22 = fixture_git(t22, "rev-parse", "HEAD")
        for shadow in ("refs/heads/origin/main", "refs/tags/origin/main"):
            fixture_git(t22, "update-ref", shadow, head22)
            for base in (None, "origin/main"):
                code, out = capture_gate(t22, base)
                if code != 1 or drop_msg not in out or "(refs/remotes/origin/main, {})".format(
                        base22[:12]) not in out:
                    failures.append("T22: with {} shadowing it, base {!r} must read refs/remotes/origin/main "
                                    "and the drop be exit 1, got {}\n{}".format(shadow, base, code, out))

        # T23. A base commit whose list is a symlink: exit 2 from the blob-mode check.
        t23, _base23 = ratchet_fixture("t23")
        decl23 = t23 / Path(DECL_REL)
        (t23 / "aside.toml").write_text(decl23.read_text(encoding="utf-8"), encoding="utf-8")
        decl23.unlink()
        os.symlink(os.path.join("..", "..", "aside.toml"), decl23)
        fixture_git(t23, "add", "-A")
        fixture_git(t23, "commit", "-q", "-m", "fixture symlinked base list")
        base23 = fixture_git(t23, "rev-parse", "HEAD")
        decl23.unlink()
        _decl(t23, [], raw=raw_decl(["prjint1", "ruleaa"]))
        code, out = capture_gate(t23, base23)
        if code != 2 or "at the base" not in out or "is not a regular file" not in out:
            failures.append("T23: a symlinked list at the base must be exit 2 naming it not a regular file, "
                            "got {}\n{}".format(code, out))

        # T24. GIT_DIR in the environment names a decoy repository whose origin/main lists no ruleaa; the
        # base must still be read from the fixture repository.
        t24, base24 = ratchet_fixture("t24")
        fixture_git(t24, "update-ref", "refs/remotes/origin/main", base24)
        _decl(t24, [], raw=raw_decl(["prjint1"]))
        decoy = tmp / "t24-decoy"
        decoy.mkdir()
        _decl(decoy, [], raw=raw_decl(["prjint1"]))
        fixture_git(decoy, "update-ref", "refs/remotes/origin/main", commit_base(decoy))
        saved_git_dir = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = str(decoy / ".git")
        try:
            code, out = capture_gate(t24, None)
        finally:
            if saved_git_dir is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = saved_git_dir
        if code != 1 or drop_msg not in out or base24[:12] not in out:
            failures.append("T24: with GIT_DIR naming a decoy repository, the base must be read from the "
                            "checked repository and the drop be exit 1, got {}\n{}".format(code, out))

        # T25. A replace ref substitutes a parentless commit that drops ruleaa for the base commit; the
        # base must still be read as committed.
        t25, base25 = ratchet_fixture("t25")
        _decl(t25, [], raw=raw_decl(["prjint1"]))
        fixture_git(t25, "add", "-A")
        repl25 = fixture_git(t25, "commit-tree", fixture_git(t25, "write-tree"), "-m", "fixture replacement")
        fixture_git(t25, "replace", base25, repl25)
        code, out = capture_gate(t25, base25)
        if code != 1 or drop_msg not in out:
            failures.append("T25: with a replace ref on the base commit, the base must be read as committed "
                            "and the drop be exit 1, got {}\n{}".format(code, out))

        # T26. A --base value starting with - is refused before git sees it.
        t26, _base26 = ratchet_fixture("t26")
        for bad in ("-h", "--git-dir=/nonexistent"):
            code, out = capture_gate(t26, bad)
            if code != 2 or "--base {!r} is not a ref".format(bad) not in out:
                failures.append("T26: --base {!r} must be exit 2 naming it not a ref, got {}\n{}".format(
                    bad, code, out))

        def drop_at_head(root):
            """Commit the working list without ruleaa as HEAD; returns HEAD's object id."""
            _decl(root, [], raw=raw_decl(["prjint1"]))
            fixture_git(root, "add", "-A")
            fixture_git(root, "commit", "-q", "-m", "fixture head drops ruleaa")
            return fixture_git(root, "rev-parse", "HEAD")

        ambiguous = "is ambiguous"

        # T27. A full object id is that commit; with refs/remotes/<base id> planted at HEAD (which drops
        # ruleaa) the base is ambiguous and refused, so the planted ref never stands in for it.
        t27, base27 = ratchet_fixture("t27")
        head27 = drop_at_head(t27)
        code, out = capture_gate(t27, base27)
        if code != 1 or drop_msg not in out or "base {} ({})".format(base27, base27[:12]) not in out:
            failures.append("T27: unplanted, --base <base id> must read that commit and the drop be exit 1, "
                            "got {}\n{}".format(code, out))
        fixture_git(t27, "update-ref", REMOTE_REF_PREFIX + base27, head27)
        code, out = capture_gate(t27, base27)
        if code != 2 or ambiguous not in out or REMOTE_REF_PREFIX + base27 not in out:
            failures.append("T27: with refs/remotes/<base id> planted, --base <base id> must be exit 2 naming "
                            "it ambiguous, got {}\n{}".format(code, out))

        # T28. A bare remote name, and a revision expression whose leading remote-tracking name is
        # shadowed by a branch or tag of the same short name, are refused (exit 2).
        t28, base28 = ratchet_fixture("t28")
        fixture_git(t28, "remote", "add", "origin", str(tmp / "t28-nowhere"))
        fixture_git(t28, "update-ref", "refs/remotes/origin/main", base28)
        fixture_git(t28, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        head28 = drop_at_head(t28)
        code, out = capture_gate(t28, "origin/main~0")
        if code != 1 or drop_msg not in out:
            failures.append("T28: unshadowed, --base origin/main~0 must read refs/remotes/origin/main and the "
                            "drop be exit 1, got {}\n{}".format(code, out))
        fixture_git(t28, "update-ref", "refs/tags/origin", head28)
        for base in ("origin", "origin~0"):
            code, out = capture_gate(t28, base)
            if code != 2 or "is the bare remote name 'origin'" not in out:
                failures.append("T28: with a tag origin, --base {!r} must be exit 2 naming the bare remote "
                                "name, got {}\n{}".format(base, code, out))
        fixture_git(t28, "update-ref", "-d", "refs/tags/origin")
        for shadow in ("refs/heads/origin/main", "refs/tags/origin/main"):
            fixture_git(t28, "update-ref", shadow, head28)
            for base in ("origin/main~0", "origin/main^0"):
                code, out = capture_gate(t28, base)
                if code != 2 or "would stand in" not in out or shadow not in out:
                    failures.append("T28: with {} shadowing it, --base {!r} must be exit 2 naming the shadow, "
                                    "got {}\n{}".format(shadow, base, code, out))

        # T29. The configured-remote fallback: origin is configured and refs/remotes/origin/gone is
        # absent, so --base origin/gone is exit 2 even with a tag origin/gone at a commit dropping ruleaa.
        t29, _base29 = ratchet_fixture("t29")
        fixture_git(t29, "remote", "add", "origin", str(tmp / "t29-nowhere"))
        fixture_git(t29, "update-ref", "refs/tags/origin/gone", drop_at_head(t29))
        code, out = capture_gate(t29, "origin/gone")
        if code != 2 or "the remote-tracking ref refs/remotes/origin/gone does not exist" not in out:
            failures.append("T29: with origin configured and refs/remotes/origin/gone absent, --base "
                            "origin/gone must be exit 2 naming the absent ref, got {}\n{}".format(code, out))

        # T30. A full-length id whose object does not exist is exit 2, also with refs/remotes/<that id>
        # planted at HEAD (which drops ruleaa).
        t30, base30 = ratchet_fixture("t30")
        missing30 = ("0123456789abcdef" * 4)[:len(base30)]
        head30 = drop_at_head(t30)
        if missing30 in (base30, head30):
            failures.append("T30: the fixture's missing id {} names a fixture commit".format(missing30))
        code, out = capture_gate(t30, missing30)
        if code != 2 or "cannot resolve the comparison base" not in out:
            failures.append("T30: --base <missing full id> must be exit 2, got {}\n{}".format(code, out))
        fixture_git(t30, "update-ref", REMOTE_REF_PREFIX + missing30, head30)
        code, out = capture_gate(t30, missing30)
        if code != 2 or ambiguous not in out or REMOTE_REF_PREFIX + missing30 not in out:
            failures.append("T30: with refs/remotes/<missing full id> planted, --base <missing full id> must "
                            "be exit 2 naming it ambiguous, got {}\n{}".format(code, out))

        # T31. HEAD (listing ruleaa) is read as git reads it; with refs/remotes/HEAD planted at a commit
        # that drops ruleaa the base is ambiguous and refused.
        t31, base31 = ratchet_fixture("t31")
        drop31 = drop_at_head(t31)
        fixture_git(t31, "reset", "-q", "--soft", base31)  # HEAD lists ruleaa; the working list drops it
        code, out = capture_gate(t31, "HEAD")
        if code != 1 or drop_msg not in out:
            failures.append("T31: unplanted, --base HEAD must read HEAD and the drop be exit 1, got {}\n{}"
                            .format(code, out))
        fixture_git(t31, "update-ref", REMOTE_REF_PREFIX + "HEAD", drop31)
        code, out = capture_gate(t31, "HEAD")
        if code != 2 or ambiguous not in out or REMOTE_REF_PREFIX + "HEAD" not in out:
            failures.append("T31: with refs/remotes/HEAD planted, --base HEAD must be exit 2 naming it "
                            "ambiguous, got {}\n{}".format(code, out))

        # T32. An abbreviated id of the base is read as git reads it; with refs/remotes/<abbreviation>
        # planted at HEAD (which drops ruleaa) the abbreviation, alone or leading an expression, is
        # ambiguous and refused.
        t32, base32 = ratchet_fixture("t32")
        head32 = drop_at_head(t32)
        abbrev32 = base32[:12]
        code, out = capture_gate(t32, abbrev32)
        if code != 1 or drop_msg not in out:
            failures.append("T32: unplanted, --base <abbreviated id> must read the base and the drop be "
                            "exit 1, got {}\n{}".format(code, out))
        fixture_git(t32, "update-ref", REMOTE_REF_PREFIX + abbrev32, head32)
        for base in (abbrev32, abbrev32 + "~0"):
            code, out = capture_gate(t32, base)
            if code != 2 or ambiguous not in out or REMOTE_REF_PREFIX + abbrev32 not in out:
                failures.append("T32: with refs/remotes/<abbreviated id> planted, --base {!r} must be exit 2 "
                                "naming it ambiguous, got {}\n{}".format(base, code, out))

        # T33. refs/remotes/<name>/HEAD, plain or symbolic, cannot stand in for an abbreviated id or a
        # describe form: git is asked which ref it takes, and one under refs/remotes/ is refused.
        through = "through a ref under refs/remotes/"
        t33, base33 = ratchet_fixture("t33")
        head33 = drop_at_head(t33)
        abbrev33 = base33[:12]
        plant33 = REMOTE_REF_PREFIX + abbrev33 + "/HEAD"
        fixture_git(t33, "update-ref", plant33, head33)
        for base in (abbrev33, abbrev33 + "~0", abbrev33 + "^0"):
            code, out = capture_gate(t33, base)
            if code != 2 or through not in out or plant33 not in out:
                failures.append("T33: with {} planted, --base {!r} must be exit 2 naming it, got {}\n{}".format(
                    plant33, base, code, out))
        fixture_git(t33, "update-ref", "-d", plant33)
        describe33 = "x-0-g" + abbrev33
        code, out = capture_gate(t33, describe33)
        if code != 1 or drop_msg not in out:
            failures.append("T33: unplanted, the describe form {!r} must read the base and the drop be exit 1, "
                            "got {}\n{}".format(describe33, code, out))
        plant33d = REMOTE_REF_PREFIX + describe33 + "/HEAD"
        fixture_git(t33, "update-ref", plant33d, head33)
        code, out = capture_gate(t33, describe33)
        if code != 2 or through not in out or plant33d not in out:
            failures.append("T33: with {} planted, --base {!r} must be exit 2 naming it, got {}\n{}".format(
                plant33d, describe33, code, out))
        fixture_git(t33, "update-ref", "-d", plant33d)
        fixture_git(t33, "update-ref", "refs/heads/qa-drop", head33)
        fixture_git(t33, "symbolic-ref", plant33, "refs/heads/qa-drop")
        for base in (abbrev33, abbrev33 + "~0"):
            code, out = capture_gate(t33, base)
            if code != 2 or through not in out or plant33 not in out:
                failures.append("T33: with {} a symbolic ref to a branch at HEAD, --base {!r} must be exit 2 "
                                "naming it, got {}\n{}".format(plant33, base, code, out))

        # T34. An expression led by a configured remote's absent remote-tracking branch is refused, so a
        # planted refs/remotes/<that name>/HEAD cannot stand in for it.
        t34, base34 = ratchet_fixture("t34")
        fixture_git(t34, "remote", "add", "origin", str(tmp / "t34-nowhere"))
        fixture_git(t34, "update-ref", "refs/remotes/origin/main", base34)
        fixture_git(t34, "update-ref", "refs/remotes/origin/absent/HEAD", drop_at_head(t34))
        for base in ("origin/absent~0", "origin/absent^0"):
            code, out = capture_gate(t34, base)
            if code != 2 or "refs/remotes/origin/absent, which does not exist" not in out:
                failures.append("T34: with refs/remotes/origin/absent absent and its /HEAD planted, --base {!r} "
                                "must be exit 2 naming the absent ref, got {}\n{}".format(base, code, out))

        # T35. A grafts file cannot move the base: the base commit and HEAD are grafted onto one parentless
        # commit that drops ruleaa, and the base is still read as committed.
        t35, base35 = ratchet_fixture("t35")
        fixture_git(t35, "update-ref", "refs/remotes/origin/main", base35)
        head35 = drop_at_head(t35)
        graft35 = fixture_git(t35, "commit-tree", fixture_git(t35, "rev-parse", "HEAD^{tree}"), "-m",
                              "fixture graft parent")
        info35 = Path(fixture_git(t35, "rev-parse", "--absolute-git-dir")) / "info"
        info35.mkdir(parents=True, exist_ok=True)
        (info35 / "grafts").write_text("{} {}\n{} {}\n".format(base35, graft35, head35, graft35),
                                       encoding="ascii")
        if fixture_git(t35, "merge-base", "HEAD", base35) != graft35:
            failures.append("T35: the fixture grafts file did not move git's merge base")
        for base, label in ((None, "(refs/remotes/origin/main, {})".format(base35[:12])),
                            ("HEAD^", "HEAD^ ({})".format(base35[:12]))):
            code, out = capture_gate(t35, base)
            if code != 1 or drop_msg not in out or label not in out:
                failures.append("T35: with a grafts file, base {!r} must be read as committed and the drop be "
                                "exit 1, got {}\n{}".format(base, code, out))
    except _FixtureError as exc:
        fixture_error = str(exc)
    finally:
        for dirpath, _dirnames, filenames in os.walk(tmp):
            for fn in filenames:
                try:
                    os.chmod(os.path.join(dirpath, fn), 0o644)
                except OSError:
                    pass
        shutil.rmtree(tmp, ignore_errors=True)

    if fixture_error is not None:
        print("SELF-TEST ERROR: a list-ratchet fixture could not be built ({}); cannot evaluate".format(
            fixture_error), file=sys.stderr)
        return 2
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a listed rule ending at the cap passes and one ending one byte past, "
          "straddling it, or byte-measured past it fails naming the rule; a stale AGENTS.md and every "
          "malformed (a non-integer or unsupported format-version, a warn margin below 1), absent, empty, "
          "symlinked, unreadable (an injected read error) or unparseable list fail closed (exit 2); the "
          "demonstration's own lists are never read; an unknown or clause id fails closed naming it; the "
          "cap is read from check_release_build.CAP_BYTES at call time; a before-rules block moves the "
          "spans it measures; the margin report, the unlisted-rule line and the warn-margin WARNING "
          "(still exit 0) are printed exactly; and against a committed base, a rule dropped from the list "
          "with no [[removal]] row fails naming it (also with the demonstration regenerated without it, "
          "and with HEAD past the base, the list read at the base), "
          "a declared removal passes and is printed, an addition passes, and a malformed removal row, an "
          "unresolvable or missing base and an absent or malformed base list fail closed (exit 2); a "
          "branch or tag shadowing origin/main, a GIT_DIR in the environment and a replace ref do not "
          "change the base read, a full or abbreviated object id and HEAD are read as git reads them, "
          "a full object id whose object does not exist fails closed, and a planted refs/remotes/<id>, "
          "refs/remotes/<abbreviation> or refs/remotes/HEAD makes the base ambiguous (exit 2), never the "
          "base read; a planted refs/remotes/<abbreviation>/HEAD makes the abbreviation, <abbreviation>~0 "
          "and <abbreviation>^0 exit 2, and as a symbolic ref to a branch the abbreviation and "
          "<abbreviation>~0, and one for a describe form makes that form exit 2, never the base read; a "
          "grafts file does not change the base read, "
          "with no --base or --base HEAD^; and a symlinked base list, a --base starting with -, a bare "
          "remote name, an expression led by a shadowed remote-tracking name, an absent remote-tracking "
          "ref of a configured remote, and an expression led by one (also with its /HEAD planted) fail "
          "closed. "
          "{}".format(
              "SKIPPED on this runner: {}.".format("; ".join(skipped)) if skipped
              else "No case was skipped (the mode-000 list ran too)."))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The modules it imports hold child launch
# and wait calls, which that gate's import scan counts, so under an ignored SIGCHLD a failing child they
# start would read as a pass. The gate's list ratchet starts git children (the base reads) and its
# self-test starts git children too (the fixture commits), each judged by its exit status; the guard runs
# at the entry line, before main() dispatches either, so every launch on either path is covered.
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
    sys.exit(_child_status_refusal_exit("check_must_deliver.py") or main())
