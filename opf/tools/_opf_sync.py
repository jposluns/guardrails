#!/usr/bin/env python3
"""opf sync: the surfaced reconciliation step of the store consistency contract (OPF-SPEC.md
section 5.7). Stdlib plus the adopter's git, fail-closed.

  opf sync [--root DIR]          report the store's state against its recorded sync target (read-only)
  opf sync --pull [--root DIR]   fast-forward a store that is BEHIND its target
  opf sync --push [--root DIR]   push the pending local commits of a store that is AHEAD of its target
  _opf_sync.py --self-test       the unit leg (also registered in opf.py --self-test)

Bare `opf sync` is READ-ONLY: it resolves the store, admits the manifest, fetches the recorded sync
target by explicit URL and reports EQUAL, BEHIND, AHEAD or DIVERGENT. It never changes the working
tree or the target: the fast-forward pull and the authorized push are each their own explicit,
surfaced step (`--pull` / `--push`, spec 5.7), never folded into the report or into one another, so
a pull or a push is a deliberate act the operator can see. A divergence halts in every mode: sync
merges nothing and picks no side, because a textual merge of the store's TOML records can mangle the
records the standard exists to protect.

Exit contract: 0 when the store and its target are equal, or once a requested pull or push
reconciled them and (for a pull) the store is doctor-VALID after the fast-forward; 2 for every
refusal, surfaced non-equal state, divergence, or cannot-evaluate. Exit 1 is not used (it is
doctor's own finding code). A NOT-ADOPTED root is a cannot-evaluate (sync is a requested operation,
like `record` and `import`, never an applicability probe), and a failed fetch is a cannot-evaluate,
never read as equal.

Scope by store pattern (spec 5.7): only a store whose manifest records a dedicated sync target
([store].sync_target non-empty) is in this verb's scope. The in-repo default ([store].sync_target
empty, the store riding the product repository) refuses with exit 2: its consistency across systems
is the product repository's own branch-and-merge discipline. A relocated local-only store (empty
sync_target behind a pointer) refuses too: it has no behind/ahead axis, and durability is the
adopter's recorded backup responsibility (spec 5.3).

Order of operations. 1. Resolve the store (spec 4.3). 2. ADMISSION (spec 9.2): the manifest
discovery finds must declare exactly spec_version 2.0.0 with the integer homes = 2 and must pass the
section 9.2 validation refusals (validate_manifest VALID), BEFORE any lease claim, fetch or write;
this tooling defines no other store. 3. Classify the pattern from [store].sync_target and refuse a
store with no dedicated target. 4. Validate the target BEFORE any git launch: a typed github:/gitlab:
form is a creation convenience that spec 5.5 resolves, after creation, to an ordinary git URL and is
refused here (record that resolved URL); a local dir:/path target names no git remote and is refused
(a filesystem target is reachable as git:<absolute path>, the file transport); a value that begins
with `-` (an option look-alike), a remote-helper `<helper>::<address>` transport (ext:: included),
and any scheme outside file, https and ssh are refused. 5. Require the destination agreement of
spec 5.6 before any fetch or push: the resolved pointer (where it names a remote) and the store
repository's own single recorded remote must agree with the recorded sync_target, by canonical
host+path where both sides canonicalize and by exact string equality otherwise; a destination that
appears from anywhere else is surfaced and refused, never fetched from or pushed to. 6. Fetch by
explicit URL (never by remote name) on the store's CURRENT branch, recording only FETCH_HEAD from
the target; a detached or unborn HEAD refuses. 7. Compare with `merge-base --is-ancestor` both ways
and report, or run the one requested step:

- BEHIND + --pull: the section 9.2 refusals are applied AGAIN to the manifest the fetched state
  carries, with discovery's one-manifest rule over the fetched `.working/` tree, so a fast-forward
  never brings a refused manifest into the store (spec 9.2); the clean-state gate (check_clean) runs
  over the paths changed between HEAD and FETCH_HEAD plus every declared view destination; the
  single-writer claim is taken (below); `merge --ff-only FETCH_HEAD` lands the target's own history;
  a full doctor then grades the result. A doctor result other than VALID reports and exits 2 with
  the fast-forward LEFT IN PLACE: the history is the target's own, rolling back automatically would
  rewrite HEAD silently, and `git reset --keep <prior tip>` is the reviewed way back.
- AHEAD + --push: the destination agreement is re-checked, the single-writer claim is taken (a push
  is allowed only while NO lease is present; any present lease refuses and is never seized), and the
  pending commits are pushed `HEAD:refs/heads/<branch>` to the verified URL, never with --force. A
  rejected non-fast-forward push (the target advanced between the fetch and the push) is reported as
  a divergence, exit 2, with the target left exactly as the other side wrote it.
- DIVERGENT: exit 2 in every mode, naming both tips; no merge, no reset, no side picked.

The single-writer claim is the homes-2 operation capability (_opf_oplock.acquire_operation, verb
"sync"), whose mandatory lease leg IS the spec 5.7 single-writer lease; it is held across the
fast-forward and its doctor, or across the push, and released BEFORE the outcome is reported, so
success is never reported over a still-held or failed-to-release claim. The claim and its release
are not themselves store operations; the fast-forward is one, and a push is one too (it writes the
recorded target), while a fetch is not: a fetch writes only git transport state (objects and
FETCH_HEAD under .git, and the refs/bundles/* refs of a repository fetch.bundleURI, below), never
the store tree, which is disclosed here rather than gated.

The transport boundary (fetch and push only; every other git call runs under the read-only scrubbed
boundary of _opf_observe._run_git): the adopter's global and system git configuration IS honoured,
so credential helpers and SSH keys work (HOME, XDG_CONFIG_HOME and the safe GIT_CONFIG_NOSYSTEM
toggle are carried, with SSH_AUTH_SOCK for an agent-held key), while every other ambient GIT_*
variable is dropped by construction and the env-based config overrides are dropped, not honoured
(they can set any key). Repository-local exec surfaces are forced off for the transport call:
core.hooksPath to the null device (no repository pre-push or other hook runs), core.fsmonitor off,
protocol.allow=never with only file, https and ssh re-allowed (a remote-helper or ext:: transport
cannot run even where repository config allows it), and core.sshCommand pinned to the adopter's own
global-or-system value (or plain `ssh` where none is set), so a repository-local core.sshCommand
never executes while a global one is honoured as the adopter's own setting. Two enumerations then
gate every fetch and push, both fail-closed. First, the repository scopes (--local and --worktree)
are listed by name, and a credential helper, core.askpass, core.gitProxy, an http.sslVerify /
sslCAInfo / sslCAPath server-trust override, an http.cookieFile / saveCookies cookie path, a
url.*.insteadOf / pushInsteadOf rewrite, and include.path / includeIf.*.path are each REFUSED, in
the plain and the subsection form alike (http.cookieFile and http.<url>.cookieFile): a
repository-planted helper is a program launch, a repository trust override can authenticate an
interposed https server, a repository cookie path makes the https transport read cookies from, or
write a cookie jar to, a file the repository chooses (a filesystem effect, not routing or shaping),
and a repository-local include is read by the transport but not expanded by a scoped listing, so
its keys could not be verified by name. Second, the EFFECTIVE url.* rewrites are read under the
exact environment and override pairs the transport call runs with, so every source git would honour
(global, system, local, worktree, include, command) is the source checked, and any insteadOf or
pushInsteadOf whose prefix matches the verified URL refuses: the URL the destination agreement
verified is then, byte for byte, the URL the transport uses. Refusal is chosen over an override in
both gates because a GIT_CONFIG_* pair appends to a multi-valued key rather than resetting it (an
empty insteadOf value is itself a match-everything rewrite), while an unrelated rewrite of the
adopter's own matches nothing here and keeps working.

DISCLOSED RESIDUALS: the lease is NOT made observable at the sync target in this build; the planned
mechanism for the writes sync itself makes is a lock ref at the target (for example refs/opf/lease)
written with an atomic compare-and-swap push, and until it ships the lease is local to this host, so
the section 17 propagation window stands: two systems can both begin, and the divergence check here
is the overlapping control that catches that collision after the fact (the two layers together, not
the lease alone, are the guarantee, spec 5.7/17). The pre-operation reconciliation spec 5.7 requires
of EVERY other operation is not yet enforced by the other verbs; the shared pre-operation reconcile
is its own later unit, and until it lands a writing verb on a dedicated-target store does not refuse
a stale store (this module is the surfaced step it will name). A pull or push operates only on a
store that is its repository's toplevel (`rev-parse --show-prefix` empty): a fast-forward or push
moves the WHOLE repository, so a store nested below the toplevel of a shared repository refuses with
exit 2 rather than moving content outside the store. The push runs the TARGET repository's own
receive-side hooks, as any git push does: the target is the adopter's recorded companion repository
and its receive side is its own trust domain. Repository-scope configuration that stays effective
on the transport after the refusals above never replaces the verified destination, and is one of
the following. Routing: http.proxy (and http.<url>.proxy) and http.curloptResolve (which remaps the
host and port a connection reaches) can route the https connection, but the server is still
authenticated against the adopter's own trust roots (the repository-scope trust overrides are
refused), so the verified destination cannot be silently replaced and a wrong proxy or mapping
fails the transport closed. Shaping: http.extraHeader and the tuning keys (http.postBuffer, the
pack and fetch sizing family, protocol.version) shape an already-authenticated connection to the
verified URL and can at worst fail it; ssh.variant shapes the arguments of the pinned ssh and names
no program; remote.<name>.* is never consulted (fetch and push go by explicit URL, never by remote
name); and a per-protocol protocol.<name>.allow at repository scope can re-widen a protocol, but
the verified URL is already restricted to file, https and ssh before any launch and URL rewriting
is refused, so a widened protocol is reachable only through the verified target's own redirect,
inside that trust domain. File reads: http.sslCert / sslKey, the proxy TLS paths
(http.proxySSLCert / proxySSLKey / proxySSLCAInfo) and http.pinnedPubkey make the transport READ a
file the repository names, to present a client certificate to the verified server or its proxy, to
trust the proxy's own TLS, or to narrow the server's trust; they write nothing and leave the
server's authentication to the adopter's trust roots. Extra fetch source: fetch.bundleURI makes a
fetch also download bundles from the location it names, writing their objects and refs/bundles/*
refs under .git (transport state, like FETCH_HEAD); FETCH_HEAD, the only fetched state the
comparison and the fast-forward read, still comes from the verified URL alone.
The destination agreement compares by exact string
where a form (a file path, a file:// URL) has no canonical host+path, so two spellings of one
filesystem target (a path and its symlink) read as disagreement and over-refuse, never under-refuse.
A branch at the target is assumed to carry the store's current branch name (the spec names no
branch); a target that lacks it is a failed fetch, a cannot-evaluate. The fetch timeout and output
bound fail the run closed, never partially. A signal delivered at any point may leave the operation
capability's records in place, which the next run's recovery acquisition handles (never seized from
a live holder)."""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_sync.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal          # noqa: E402
import _opf_check        # noqa: E402
import _opf_observe      # noqa: E402
import _opf_oplock       # noqa: E402
import _opf_store        # noqa: E402
import _opf_write_guard  # noqa: E402

EXIT_OK = 0
EXIT_MALFORMED = 2

VERB = "sync"
CLEAN_SCOPE = "fast-forward destinations"
_USAGE = "usage: opf sync [--pull | --push] [--root DIR] (bare sync is the read-only report; spec 5.7)"
# The transport surface (spec 5.6 / the module docstring): file for a local bare target, https and
# ssh for a hosted one. git:// (cleartext, unauthenticated) and every other scheme refuse.
_ALLOWED_SCHEMES = ("file", "https", "ssh")
# A remote-helper transport (`<helper>::<address>`, ext:: included) names a program for git to run.
_HELPER_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9+.]*::")
# Repository-SCOPE config keys the transport refuses by name, over both the --local and --worktree
# listings (section/key folded, the subsection kept): a URL rewrite silently redirects the verified
# destination; a repository-planted credential helper, askpass or git proxy is a program launch on
# fetch or push; a repository-scope https trust override can authenticate an interposed server; a
# repository-scope cookie file or cookie save makes the https transport read cookies from, or write a
# cookie jar to, a path the repository chooses; and a repository-local include is read by the
# transport but not expanded by a scoped listing, so its keys could not be verified by name.
# Overriding these would also discard the adopter's own global configuration, so presence refuses
# instead; the effective url.* rewrites of EVERY scope are additionally matched against the verified
# URL before each transport call. Each entry is a folded section.key, and a name refuses when it is
# that key with or without a subsection between the two (http.sslVerify and http.<url>.sslVerify,
# url.<base>.insteadOf, includeIf.<condition>.path); _refuse_local_redirect_config reads these lists
# and nothing else, so the stated and the enforced lists are one.
_LOCAL_REDIRECT_KEYS = (b"url.insteadof", b"url.pushinsteadof")
_LOCAL_EXEC_KEYS = (b"credential.helper", b"core.askpass", b"core.gitproxy")
_LOCAL_TRUST_KEYS = (b"http.sslverify", b"http.sslcainfo", b"http.sslcapath")
_LOCAL_COOKIE_KEYS = (b"http.cookiefile", b"http.savecookies")
_LOCAL_INCLUDE_KEYS = (b"include.path", b"includeif.path")
_TRANSPORT_TIMEOUT_S = 120
_TRANSPORT_MAX_OUTPUT = 8 << 20
# The one fast-forward command; --ff-only is load-bearing (a divergence must never merge).
_FF_MERGE_ARGS = ("merge", "--ff-only", "FETCH_HEAD")


class SyncError(Exception):
    """A fail-closed refusal or cannot-evaluate carrying the operator-facing reason (mapped to exit 2)."""


def parse_request(argv):
    """Parse `opf sync` arguments into (mode, root): mode is "report" (bare), "pull" or "push". At
    most one of --pull/--push (one surfaced step at a time), --root at most once with a non-empty
    value; anything else is a usage refusal carrying _USAGE."""
    mode = "report"
    root = None
    seen_mode = None
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--root":
            if i + 1 >= len(args):
                raise SyncError("--root requires an argument; " + _USAGE)
            if root is not None:
                raise SyncError("--root given more than once; " + _USAGE)
            root = args[i + 1]
            if not root:
                raise SyncError("--root must name a directory (an empty value is refused); " + _USAGE)
            i += 2
            continue
        if arg in ("--pull", "--push"):
            if seen_mode is not None:
                raise SyncError("give at most one of --pull and --push (each is its own surfaced "
                                "step, spec 5.7); " + _USAGE)
            seen_mode = arg
            mode = arg[2:]
            i += 1
            continue
        raise SyncError("unrecognized argument {!r}; {}".format(arg, _USAGE))
    return mode, (root if root is not None else ".")


# --- admission (spec 9.2) and pattern classification ---------------------------------------------------

def _admit_manifest(model, where, landing):
    """Apply the spec 9.2 refusals to one manifest model: the [opf].homes refusal (exactly
    spec_version 2.0.0 with the integer homes = 2; a bool or float never reads as a generation) and
    the validation refusals (validate_manifest VALID), BEFORE any lease claim, fetch or write.
    `where` names the manifest for the refusal and `landing` names what the refusal prevents."""
    base = model.get(_opf_store.STANDARD_TOKEN) if isinstance(model, dict) else None
    version = base.get("spec_version") if isinstance(base, dict) else None
    declared = base.get("homes") if isinstance(base, dict) else None
    if (version != _opf_store.SPEC_VERSION or type(declared) is not int
            or declared != _opf_store.HOMES):
        parsed = _opf_store._parse(version) if isinstance(version, str) else None
        remedy = ""
        if parsed is not None and parsed < _opf_store._parse(_opf_store.SPEC_VERSION):
            remedy = (" A spec_version below {} names no version this specification "
                      "defines (spec 9.2).".format(_opf_store.SPEC_VERSION))
        elif parsed is not None and parsed > _opf_store._parse(_opf_store.SPEC_VERSION):
            remedy = " Upgrade the tooling before operating on this store."
        raise SyncError("{} declares spec_version {} with homes {}, but this tooling admits only "
                        "spec_version {} with homes = {} (spec 9.2).{} {} (fail-closed)".format(
                            where, _opf_store._safe_display(version), _opf_store._safe_display(declared),
                            _opf_store.SPEC_VERSION, _opf_store.HOMES, remedy, landing))
    mv = _opf_store.validate_manifest(model, manifest_rel=where)
    if mv.status != _opf_store.VALID:
        raise SyncError("{} is not VALID ({}); {} (spec 9.2, fail-closed)".format(
            where, "; ".join(mv.findings), landing))
    return model


def _read_local_manifest(res):
    """Read and parse the manifest discovery resolved, through the store-root no-follow boundary."""
    rel = "{}/{}".format(res.machine_rel, _opf_store.MANIFEST_NAME)
    try:
        root_fd = _opf_store._open_dir_nofollow(res.store_root)
    except OSError as exc:
        raise SyncError("cannot open the store root {} ({}); fail-closed".format(res.store_root, exc))
    try:
        try:
            model = _opf_store._read_toml_contained(root_fd, rel)
        except _opf_store.StoreError as exc:
            raise SyncError("cannot read the store manifest {} ({}); fail-closed".format(rel, exc))
    finally:
        _journal._close_fd_quietly(root_fd)
    if model is None:
        raise SyncError("the store manifest {} disappeared after resolution; fail-closed".format(rel))
    return model


def _sync_target_of(manifest):
    """The manifest's [store].sync_target as a string; a non-string value refuses fail-closed."""
    store_tbl = manifest.get("store")
    value = store_tbl.get("sync_target") if isinstance(store_tbl, dict) else ""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SyncError("[store].sync_target is not a string; fail-closed")
    return value


def _refuse_no_dedicated_target(res):
    """The exit-2 refusal of a store with no dedicated sync target (spec 5.7): the in-repo default
    rides the product repository, and a relocated local-only store has no behind/ahead axis."""
    product = res.product_root if res.product_root is not None else res.store_root
    in_repo = os.path.abspath(str(product)) == os.path.abspath(str(res.store_root))
    if in_repo:
        raise SyncError("this store has no dedicated sync target ([store].sync_target is empty) and "
                        "rides the product repository (the in-repo default): its consistency across "
                        "systems is the product repository's own branch-and-merge discipline, not a "
                        "sync step (spec 5.7); fail-closed")
    raise SyncError("this store has no dedicated sync target ([store].sync_target is empty): a "
                    "relocated local-only store has no behind/ahead axis, and durability is the "
                    "adopter's recorded backup responsibility (spec 5.3/5.7); fail-closed")


# --- target validation and destination agreement (spec 5.5/5.6) ----------------------------------------

def validate_target(sync_target):
    """Validate the recorded sync target BEFORE any git launch and return (Target, transport URL).
    Refuses a typed github:/gitlab: creation convenience (spec 5.5 resolves it, after creation, to an
    ordinary git URL, which is what belongs in the manifest), a local dir:/path target (it names no
    git remote; a filesystem target is reachable as git:<absolute path>, the file transport), a value
    beginning with `-` (an option look-alike is never a destination), a remote-helper
    `<helper>::<address>` transport (ext:: included; it names a program for git to run), and any
    scheme outside file, https and ssh."""
    try:
        tgt = _opf_store.classify_target(sync_target)
    except _opf_store.StoreError as exc:
        raise SyncError("[store].sync_target {!r} does not classify as a target (spec 5.5): "
                        "{} (fail-closed)".format(sync_target, exc))
    if tgt.kind in ("github", "gitlab"):
        raise SyncError("[store].sync_target {!r} is a typed {} creation convenience; spec 5.5 "
                        "resolves it, after creation, to an ordinary git URL, and that resolved URL "
                        "is what sync fetches and pushes. Record the ordinary git URL as the "
                        "sync_target (fail-closed)".format(sync_target, tgt.kind))
    if tgt.local:
        raise SyncError("[store].sync_target {!r} names a local {} target, not a git remote; a "
                        "filesystem target is reachable as git:<absolute path> (the file transport) "
                        "(fail-closed)".format(sync_target, tgt.kind))
    url = tgt.value.strip()
    if url.startswith("-"):
        raise SyncError("[store].sync_target {!r} begins with '-'; such a value could be read as a "
                        "git option, never as a destination (fail-closed)".format(sync_target))
    if url.startswith("::") or _HELPER_RE.match(url):
        raise SyncError("[store].sync_target {!r} names a remote-helper transport "
                        "('<helper>::<address>', ext:: included), which can execute a configured "
                        "program; the transport surface is file, https and ssh only "
                        "(fail-closed)".format(sync_target))
    if "://" in url:
        scheme = url.split("://", 1)[0].lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise SyncError("[store].sync_target {!r} uses the {} scheme, outside the allowed "
                            "transport surface (file, https, ssh) (fail-closed)".format(
                                sync_target, scheme))
    else:
        slash = url.find("/")
        colon = url.find(":")
        scp = colon != -1 and (slash == -1 or colon < slash)
        if not scp and not url.startswith("/"):
            raise SyncError("[store].sync_target {!r} is neither an absolute filesystem path (the "
                            "file transport), an scp-style host:path (the ssh transport), nor an "
                            "allowed scheme URL (fail-closed)".format(sync_target))
    return tgt, url


def _actual_remote(git, store_root):
    """The store repository's single recorded push destination, through the read-only observation
    boundary. "" is an observed no-remote; an unobservable destination (several remotes, or a git
    error) is a cannot-evaluate refusal, never a silently skipped agreement."""
    notes = []
    actual = _opf_observe._observe_remote(git, store_root, notes)
    if actual is None:
        raise SyncError("the store repository's push destination cannot be established ({}); the "
                        "spec 5.6 destination agreement is required before any fetch or push "
                        "(fail-closed)".format("; ".join(notes) if notes else "unobservable"))
    return actual


def _require_destination_agreement(sync_target, tgt, url, pointer_target, actual):
    """The spec 5.6 egress rule before any fetch or push: the resolved pointer (where it names a
    remote) and the store repository's own recorded remote must agree with the recorded sync target,
    by canonical host+path where both sides canonicalize, by exact string equality otherwise. A
    destination that appears from anywhere else is surfaced and refused, never fetched or pushed."""
    tcanon, _tun = _opf_check._target_remote_canon(tgt)
    if pointer_target is not None and not pointer_target.local:
        pcanon, _pun = _opf_check._target_remote_canon(pointer_target)
        if pcanon and tcanon:
            agree = _opf_check._canon_sets_agree(pcanon, tcanon, tgt.kind)
        else:
            agree = pointer_target.value.strip() == url
        if not agree:
            raise SyncError("the resolved pointer remote {!r} does not agree with the manifest "
                            "sync_target {!r} (spec 5.6); nothing is fetched or pushed "
                            "(fail-closed)".format(pointer_target.value, sync_target))
    if actual == "":
        raise SyncError("the manifest declares a dedicated sync_target {!r} but the store repository "
                        "has no recorded remote (spec 5.6); the destination cannot be confirmed "
                        "against the repository's own record, so nothing is fetched or pushed; add "
                        "the agreeing remote and re-run (fail-closed)".format(sync_target))
    acanon = _opf_check._canonical_remote(actual)
    if acanon is not None and tcanon:
        agree = _opf_check._canon_sets_agree({acanon}, tcanon, tgt.kind)
    else:
        agree = actual.strip() == url
    if not agree:
        raise SyncError("the store repository's recorded remote {!r} does not agree with the "
                        "manifest sync_target {!r} (spec 5.6): a destination from ambient "
                        "configuration or a stale remote is surfaced and refused, never fetched "
                        "from or pushed to (fail-closed)".format(actual, sync_target))


# --- read-only git questions (the scrubbed observation boundary) ---------------------------------------

def _git_or_refuse():
    git = _opf_observe._git_path()
    if git is None:
        raise SyncError("cannot locate git; without it the store's freshness against its sync "
                        "target cannot be evaluated (fail-closed)")
    return git


def _read_git(git, store_root, args, what):
    """One read-only git call whose completion and rc 0 are required; anything else refuses."""
    out = _opf_observe._run_git(git, store_root, list(args))
    if not out.completed:
        raise SyncError("could not run git to {} ({}); fail-closed".format(what, out.err.strip()))
    if out.rc != 0:
        if _opf_observe._is_no_repo(out):
            raise SyncError("the store at this root is not a git repository, so it has no sync "
                            "state to reconcile (spec 5.1/5.7); fail-closed")
        raise SyncError("git could not {} (rc {}): {}; fail-closed".format(
            what, out.rc, out.err.strip()))
    return out


def _current_branch(git, store_root):
    """The store repository's current branch name. A detached HEAD refuses (it names no branch to
    fetch or push), and an unborn branch (no commit yet) refuses too."""
    out = _opf_observe._run_git(git, store_root, ["symbolic-ref", "--quiet", "HEAD"])
    if not out.completed:
        raise SyncError("could not run git to read the current branch ({}); fail-closed".format(
            out.err.strip()))
    if out.rc != 0:
        if _opf_observe._is_no_repo(out):
            raise SyncError("the store at this root is not a git repository, so it has no sync "
                            "state to reconcile (spec 5.1/5.7); fail-closed")
        raise SyncError("the store repository's HEAD is detached; sync pulls and pushes the store's "
                        "current branch, and a detached HEAD names none; check out the store branch "
                        "and re-run (fail-closed)")
    ref = out.out.decode("utf-8", "replace").strip()
    prefix = "refs/heads/"
    if not ref.startswith(prefix) or len(ref) <= len(prefix):
        raise SyncError("the store repository's HEAD names {!r}, not a branch; fail-closed".format(ref))
    verify = _opf_observe._run_git(git, store_root, ["rev-parse", "--verify", "--quiet", "HEAD"])
    if not verify.completed:
        raise SyncError("could not run git to verify HEAD ({}); fail-closed".format(verify.err.strip()))
    if verify.rc != 0:
        raise SyncError("the store repository's branch is unborn (no commit yet); commit the store "
                        "before reconciling it against a sync target (fail-closed)")
    return ref[len(prefix):]


def _rev(git, store_root, name):
    out = _read_git(git, store_root, ["rev-parse", "--verify", "--quiet", name + "^{commit}"],
                    "resolve {}".format(name))
    return out.out.decode("utf-8", "replace").strip()


def _is_ancestor(git, store_root, earlier, later):
    """Whether `earlier` is an ancestor of (or equal to) `later`; any outcome but git's documented
    0/1 answer is a cannot-evaluate."""
    out = _opf_observe._run_git(git, store_root, ["merge-base", "--is-ancestor", earlier, later])
    if not out.completed or out.rc not in (0, 1):
        raise SyncError("git could not compare the store against the fetched target state ({}); "
                        "fail-closed".format(out.err.strip() if out.completed is False
                                             else "rc {}: {}".format(out.rc, out.err.strip())))
    return out.rc == 0


def compare(git, store_root, local, fetched):
    """Classify the store against the fetched target tip: equal, behind, ahead or divergent."""
    if local == fetched:
        return "equal"
    behind = _is_ancestor(git, store_root, local, fetched)
    ahead = _is_ancestor(git, store_root, fetched, local)
    if behind and ahead:
        raise SyncError("git reports {} and {} each as the other's ancestor while they differ; the "
                        "comparison cannot be trusted (fail-closed)".format(local, fetched))
    if behind:
        return "behind"
    if ahead:
        return "ahead"
    return "divergent"


def _require_toplevel(git, store_root):
    """A pull or push moves the WHOLE repository, so the store must be its repository's toplevel; a
    store nested below the toplevel of a shared repository refuses (a disclosed scope bound)."""
    out = _read_git(git, store_root, ["rev-parse", "--show-prefix"], "locate the store in its repository")
    if out.out.removesuffix(b"\n"):
        raise SyncError("the store root sits below its git repository's toplevel; a fast-forward or "
                        "push moves the whole repository, so sync operates only on a store that is "
                        "its repository's toplevel (a disclosed scope bound; fail-closed)")


# --- the hardened transport (fetch and push only) -------------------------------------------------------

def _transport_env():
    """The transport environment: the adopter's real global and system configuration is discoverable
    (credential helpers and SSH keys work), every other ambient GIT_* variable is dropped by
    construction, the env-based config overrides are dropped (they can set any key), trace is forced
    off, the call is non-interactive and deterministic, and lazy fetching is off."""
    env = {}
    for name in _opf_observe._CONFIG_DISCOVERY_KEEP + ("SSH_AUTH_SOCK",):
        val = os.environ.get(name)
        if val is not None:
            env[name] = val
    env.update(_opf_observe._TRACE_OFF)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["LC_ALL"] = "C"
    return env


def _global_ssh_command(git):
    """The adopter's own global-or-system core.sshCommand, or None where none is set (any unreadable
    scope reads as unset: the override below then pins plain `ssh`, the behaviour-preserving
    default, so an unreadable global config can only narrow, never widen, what executes)."""
    for scope in ("--global", "--system"):
        out = _opf_observe._capture_bounded([git, "config", scope, "--get", "core.sshCommand"],
                                            _transport_env(), 30, 1 << 20)
        if out.completed and out.rc == 0:
            value = out.out.decode("utf-8", "replace").rstrip("\n")
            if value:
                return value
    return None


def _transport_overrides(git):
    """The command-level config pairs every transport call runs under (injected as separate
    GIT_CONFIG_KEY/VALUE env strings, never `-c` argv): repository-local exec surfaces forced off
    while the adopter's global configuration stays honoured."""
    return [("core.hooksPath", os.devnull),
            ("core.fsmonitor", "false"),
            ("protocol.allow", "never"),
            ("protocol.file.allow", "always"),
            ("protocol.https.allow", "always"),
            ("protocol.ssh.allow", "always"),
            ("core.sshCommand", _global_ssh_command(git) or "ssh")]


def _local_key_listed(low, keys):
    """Whether one folded config name is a listed section.key, with or without a subsection between
    the section and the key (http.sslverify and http.<url>.sslverify both match http.sslverify)."""
    for listed in keys:
        section, _dot, key = listed.partition(b".")
        if low.startswith(section + b".") and low.endswith(b"." + key):
            return True
    return False


def _refuse_local_redirect_config(git, store_root):
    """Refuse, by name, a repository-scope config key that would redirect, exec, weaken the trust
    decision or reach a repository-chosen path on the transport call: url.*.insteadOf /
    pushInsteadOf (a silent rewrite of the verified URL), a credential helper, core.askpass,
    core.gitProxy, an http.sslVerify / sslCAInfo / sslCAPath server-trust override, an
    http.cookieFile / saveCookies cookie path (the https transport reads cookies from, or writes a
    cookie jar to, a file the repository names), and include.path / includeIf.*.path (the transport
    reads the included file, but a scoped listing does not expand it, so the included keys could not
    be verified by name). The names are matched from the _LOCAL_* lists alone (_local_key_listed).
    Both repository scopes are enumerated, --local and --worktree (without the worktreeConfig
    extension the latter reads the same .git/config), through the scrubbed read-only boundary
    (--name-only: no value, so a malicious command body is never read); an unreadable scope is a
    cannot-evaluate, never read as empty."""
    refusals = (
        (_LOCAL_REDIRECT_KEYS,
         ", a URL rewrite that would silently redirect the verified destination (spec 5.6); remove "
         "it from the repository config and re-run (fail-closed)"),
        (_LOCAL_EXEC_KEYS,
         ", a repository-planted program launch on fetch or push; credentials belong in the "
         "adopter's own global configuration (spec 5.6); remove it and re-run (fail-closed)"),
        (_LOCAL_INCLUDE_KEYS,
         ": the transport reads the included file, but this scoped enumeration does not expand it, "
         "so the included keys cannot be verified by name; move the settings you own into your "
         "global configuration or inline them here, remove the include, and re-run (fail-closed)"),
        (_LOCAL_TRUST_KEYS,
         ", a repository-scope override of the https server trust; the trust decision belongs to "
         "the adopter's own global or system configuration (spec 5.6); remove it and re-run "
         "(fail-closed)"),
        (_LOCAL_COOKIE_KEYS,
         ", a repository-chosen cookie path: the https transport would read cookies from, or write "
         "a cookie jar to, a file the repository names; cookie storage belongs to the adopter's own "
         "global configuration (spec 5.6); remove it and re-run (fail-closed)"))
    for scope in ("--local", "--worktree"):
        listing = _opf_observe._run_git(git, store_root,
                                        ["config", scope, "-z", "--name-only", "--list"])
        if not listing.completed:
            raise SyncError("could not enumerate the store repository's {} git configuration ({}); "
                            "the transport cannot be verified (fail-closed)".format(
                                scope.lstrip("-"), listing.err.strip()))
        if listing.rc != 0:
            raise SyncError("git config {} --list failed (rc {}); the store repository's {} "
                            "configuration is unreadable, so the transport cannot be verified "
                            "(fail-closed)".format(scope, listing.rc, scope.lstrip("-")))
        raw = listing.out
        if raw and not raw.endswith(b"\x00"):
            raise SyncError("git config --list returned a payload that is not NUL-terminated; the "
                            "store repository's configuration cannot be trusted (fail-closed)")
        for kb in raw.split(b"\x00"):
            if not kb:
                continue
            low = kb.lower()
            for keys, why in refusals:
                if _local_key_listed(low, keys):
                    raise SyncError("the store repository's git configuration carries {!r}{}".format(
                        kb.decode("utf-8", "replace"), why))


def _refuse_effective_url_rewrite(git, store_root, env, url):
    """Refuse any url.<base>.insteadOf / pushInsteadOf whose recorded prefix matches the verified
    URL, read from the EFFECTIVE configuration under the exact environment and override pairs the
    transport call runs with, so every source git would honour (global, system, local, worktree,
    include, command) is the source checked, and the URL the destination agreement verified is,
    byte for byte, the URL the transport uses. Refusal is chosen over an override because a
    GIT_CONFIG_* pair appends to a multi-valued key rather than resetting it, and an empty
    insteadOf value is itself a match-everything rewrite; a rewrite whose prefix matches nothing
    here is left alone, so the adopter's own unrelated rewrites keep working. pushInsteadOf refuses
    the fetch too (one gate for the one transport boundary, over-refusing, never under-refusing).
    An enumeration that does not complete at git's documented match/no-match codes refuses."""
    cmd = [git, "--no-pager", "--no-replace-objects", "-C", str(store_root),
           "config", "-z", "--get-regexp", r"^url\."]
    out = _opf_observe._capture_bounded(cmd, env, 30, 1 << 20)
    if not out.completed:
        raise SyncError("could not enumerate the url.* configuration the transport would honour "
                        "({}); the verified URL cannot be confirmed as the transport URL "
                        "(fail-closed)".format(out.err.strip()))
    if out.rc not in (0, 1):
        raise SyncError("git config --get-regexp over the transport configuration failed (rc {}): "
                        "{}; the verified URL cannot be confirmed as the transport URL "
                        "(fail-closed)".format(out.rc, out.err.strip()))
    raw = out.out
    if raw and not raw.endswith(b"\x00"):
        raise SyncError("git config returned a payload that is not NUL-terminated; the transport "
                        "configuration cannot be trusted (fail-closed)")
    url_b = os.fsencode(url)
    for record in raw.split(b"\x00"):
        if not record:
            continue
        name, _sep, value = record.partition(b"\n")
        low = name.lower()
        if not (low.endswith(b".insteadof") or low.endswith(b".pushinsteadof")):
            continue
        if url_b.startswith(value):
            raise SyncError("the git configuration the transport honours carries {!r} whose prefix "
                            "{!r} matches the verified URL, a rewrite that would silently redirect "
                            "the fetch or push away from the agreed destination (spec 5.6); remove "
                            "or re-scope the rewrite, or record the rewritten URL as the "
                            "sync_target, and re-run (fail-closed)".format(
                                name.decode("utf-8", "replace"), value.decode("utf-8", "replace")))


def _run_transport(git, store_root, args, url, timeout=_TRANSPORT_TIMEOUT_S):
    """Run ONE fetch or push of the verified URL under the transport environment and overrides,
    bounded, after the repository-scope refusals and the effective-rewrite gate over that URL.
    Every other git call this module makes goes through the read-only scrubbed boundary instead."""
    _refuse_local_redirect_config(git, store_root)
    overrides = _transport_overrides(git)
    env = _transport_env()
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (key, value) in enumerate(overrides):
        env["GIT_CONFIG_KEY_{}".format(i)] = key
        env["GIT_CONFIG_VALUE_{}".format(i)] = value
    _refuse_effective_url_rewrite(git, store_root, env, url)
    cmd = [git, "--no-pager", "--no-replace-objects", "-C", str(store_root)] + list(args)
    return _opf_observe._capture_bounded(cmd, env, timeout, _TRANSPORT_MAX_OUTPUT)


def fetch_target(git, store_root, url, branch):
    """Fetch the store's current branch from the verified URL (never a remote name), recording only
    FETCH_HEAD. A fetch that does not complete at rc 0 is a cannot-evaluate, never read as equal."""
    out = _run_transport(git, store_root, ["fetch", "--no-tags", "--no-recurse-submodules", url,
                                           "refs/heads/{}".format(branch)], url)
    if not out.completed:
        raise SyncError("could not fetch the sync target ({}); the store's freshness cannot be "
                        "evaluated, and a failed fetch is never read as equal (fail-closed)".format(
                            out.err.strip()))
    if out.rc != 0:
        raise SyncError("git fetch from the sync target failed (rc {}): {}; the store's freshness "
                        "cannot be evaluated, and a failed fetch is never read as equal "
                        "(fail-closed)".format(out.rc, out.err.strip()))


def _push_args(url, branch):
    """The one push command: the pending commits to the verified URL, never with --force."""
    return ["push", url, "HEAD:refs/heads/{}".format(branch)]


# --- the fetched-state admission and the fast-forward (spec 9.2 / 5.7) ---------------------------------

def _show_blob(git, store_root, objpath):
    """One committed object's bytes via `git show`, or None when the object is absent."""
    out = _opf_observe._run_git(git, store_root, ["show", objpath])
    if not out.completed:
        raise SyncError("could not read {} ({}); fail-closed".format(objpath, out.err.strip()))
    if out.rc != 0:
        return None
    return out.out


def _admit_fetched(git, store_root, machine_rel):
    """Apply discovery's one-manifest rule and the spec 9.2 refusals to the STORE STATE the fetch
    brought in, before a fast-forward lands it, so a fast-forward never brings a refused manifest
    into the store (spec 9.2). Returns the fetched manifest model (the post-fast-forward manifest,
    which the write scope is planned from)."""
    import tomllib
    working = _opf_store.WORKING_DIRNAME
    listing = _opf_observe._run_git(git, store_root, ["ls-tree", "--name-only", "-z",
                                                      "FETCH_HEAD:{}".format(working)])
    if not listing.completed:
        raise SyncError("could not list the fetched {} tree ({}); fail-closed".format(
            working, listing.err.strip()))
    if listing.rc != 0:
        raise SyncError("the fetched state carries no {} store tree; a fast-forward would remove "
                        "the store (fail-closed)".format(working))
    names = [os.fsdecode(n) for n in listing.out.split(b"\x00") if n]
    candidates = []
    for name in names:
        raw = _show_blob(git, store_root, "FETCH_HEAD:{}/{}/{}".format(
            working, name, _opf_store.MANIFEST_NAME))
        if raw is None:
            continue
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError, MemoryError, UnicodeDecodeError) as exc:
            raise SyncError("the fetched state carries a manifest at {}/{}/{} that cannot be parsed "
                            "({}); discovery's one-manifest rule cannot be evaluated over it, so "
                            "the fast-forward refuses (spec 9.2, fail-closed)".format(
                                working, name, _opf_store.MANIFEST_NAME, exc))
        base = data.get(_opf_store.STANDARD_TOKEN) if isinstance(data, dict) else None
        if not (isinstance(base, dict) and base.get("standard") == _opf_store.STANDARD_TOKEN):
            raise SyncError("the fetched state carries a manifest at {}/{}/{} that does not declare "
                            "the {} standard; a fast-forward never lands a store discovery would "
                            "refuse (spec 9.2, fail-closed)".format(
                                working, name, _opf_store.MANIFEST_NAME, _opf_store.STANDARD_TOKEN))
        candidates.append((name, data))
    if len(candidates) != 1:
        raise SyncError("the fetched state carries {} machine-store manifest(s) under {}; discovery "
                        "requires exactly one, so the fast-forward refuses (spec 4.5/9.2, "
                        "fail-closed)".format(len(candidates), working))
    name, model = candidates[0]
    expected = machine_rel.split("/", 1)[1] if "/" in machine_rel else machine_rel
    if name != expected:
        raise SyncError("the fetched state's machine store is {}/{}, not this store's {}; the "
                        "fast-forward refuses (fail-closed)".format(working, name, machine_rel))
    return _admit_manifest(model, "the fetched manifest at FETCH_HEAD:{}/{}/{}".format(
        working, name, _opf_store.MANIFEST_NAME),
        "a fast-forward never brings a refused manifest into the store")


def _changed_paths(git, store_root):
    """The store-root-relative paths the fast-forward would change (HEAD against FETCH_HEAD), the
    clean-state scope together with every declared view destination."""
    out = _read_git(git, store_root, ["diff", "--name-only", "-z", "HEAD", "FETCH_HEAD"],
                    "list the paths the fast-forward changes")
    if out.out and not out.out.endswith(b"\x00"):
        raise SyncError("git diff returned a payload that is not NUL-terminated; the fast-forward "
                        "scope cannot be trusted (fail-closed)")
    return [os.fsdecode(p) for p in out.out.split(b"\x00") if p]


def _fast_forward(git, store_root):
    """Land the fetched target state with `merge --ff-only FETCH_HEAD` (repository hooks off for
    this local write, as for the transport)."""
    out = _opf_observe._run_git(git, store_root, list(_FF_MERGE_ARGS),
                                config_overrides=[("core.hooksPath", os.devnull)])
    if not out.completed:
        raise SyncError("the fast-forward merge could not run ({}); nothing is offered as "
                        "reconciled (fail-closed)".format(out.err.strip()))
    if out.rc != 0:
        raise SyncError("the fast-forward merge failed (rc {}): {}; nothing is offered as "
                        "reconciled (fail-closed)".format(out.rc, out.err.strip()))


def _doctor(root):
    """The full offline doctor over the working tree (validate_store with fresh git observations),
    the gate a fast-forward must pass before it is offered as reconciled."""
    res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    if res.status != _opf_store.RESOLVED:
        raise SyncError("the store at {!r} no longer resolves after the fast-forward ({}); the "
                        "fast-forward is left in place (fail-closed)".format(root, res.detail))
    obs, _notes = _opf_observe.gather(res)
    return _opf_check.validate_store(res, observations=obs)


def _post_pull_gate(root):
    """Run the doctor after the fast-forward. Returns [] when the store is doctor-VALID, else the
    report lines; the fast-forward is left in place either way (the history is the target's own, and
    an automatic rollback would rewrite HEAD silently)."""
    try:
        result = _doctor(root)
    except SyncError:
        raise
    except Exception as exc:  # noqa: BLE001  a doctor escape must not read as a reconciled store
        raise SyncError("the post-fast-forward doctor could not run ({!r}); the fast-forward is "
                        "left in place (fail-closed)".format(exc))
    if result.status == _opf_store.VALID:
        return []
    lines = ["the store is NOT doctor-VALID after the fast-forward ({})".format(result.status)]
    lines += ["  FINDING: {}".format(f) for f in result.findings]
    lines += ["  CANNOT-EVALUATE: {}".format(c) for c in result.cannot_evaluate]
    return lines


# --- the single-writer claim (spec 5.7) -----------------------------------------------------------------

def _acquire_guard(store_root):
    """The homes-2 operation capability, whose mandatory lease leg IS the spec 5.7 single-writer
    lease: any present lease refuses the acquisition and is never seized, so a pull or push runs
    only while no other run holds the store."""
    try:
        return _opf_oplock.acquire_operation(str(store_root), VERB)
    except _opf_oplock.OpLockError as exc:
        raise SyncError(str(exc))


def _release_guard(guard):
    try:
        _opf_oplock.release_operation(guard)
    except _opf_oplock.OpLockError as exc:
        raise SyncError(str(exc))


def _under_guard(store_root, body):
    """Run `body` under the held claim and release BEFORE the caller reports, so success is never
    reported over a still-held or failed-to-release claim; a release failure after a body failure is
    surfaced while the body's failure still governs."""
    guard = _acquire_guard(store_root)
    pending = None
    try:
        return body()
    except BaseException as exc:  # noqa: BLE001  recorded for the cleanup, re-raised unchanged
        pending = exc
        raise
    finally:
        try:
            _release_guard(guard)
        except BaseException:  # noqa: BLE001  surfaced, never displaces the body's own failure
            if pending is None:
                raise
            try:
                print("opf sync: additionally, releasing the single-writer claim failed; whichever "
                      "of its records the release did not remove may be left in place (never "
                      "seized, spec 5.7), and the failure above still governs.", file=sys.stderr)
            except BaseException:
                pass


# --- the operation ---------------------------------------------------------------------------------------

def _pull(git, res, root, local, fetched):
    """The surfaced fast-forward of a BEHIND store (spec 5.7): fetched-state admission, the
    clean-state gate, the claim, `merge --ff-only`, then the doctor. Returns the exit code."""
    _require_toplevel(git, res.store_root)
    fetched_manifest = _admit_fetched(git, res.store_root, res.machine_rel)
    changed = _changed_paths(git, res.store_root)
    try:
        scope = _opf_write_guard.plan_write_scope(res.machine_rel, fetched_manifest, changed, VERB)
        _opf_write_guard.check_clean(res, scope, VERB, CLEAN_SCOPE)
    except _opf_write_guard.WriteGuardError as exc:
        raise SyncError(str(exc))

    def body():
        _fast_forward(git, res.store_root)
        return _post_pull_gate(root)

    gate = _under_guard(res.store_root, body)
    if gate:
        print("opf sync: fast-forwarded the store from {} to its sync target's tip {} ({} path(s) "
              "changed), but {}".format(local, fetched, len(changed), gate[0]), file=sys.stderr)
        for line in gate[1:]:
            print(line, file=sys.stderr)
        print("opf sync: the fast-forward is LEFT IN PLACE (the history is the target's own; an "
              "automatic rollback would rewrite HEAD silently). Review the findings; "
              "`git -C {} reset --keep {}` is the reviewed way back. Exit 2.".format(
                  res.store_root, local), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf sync: fast-forwarded the store to its sync target: {} -> {} ({} path(s) changed), "
          "and the store is doctor-VALID.".format(local, fetched, len(changed)))
    return EXIT_OK


def _push_pending(git, res, sync_target, tgt, url, branch, local, fetched):
    """The authorized push of an AHEAD store's pending commits (spec 5.7): the destination agreement
    re-checked, the claim taken (a push is allowed only while no lease is present), then one push to
    the verified URL, never with --force. Returns the exit code."""
    _require_toplevel(git, res.store_root)
    _require_destination_agreement(sync_target, tgt, url, res.target, _actual_remote(git, res.store_root))

    def body():
        return _run_transport(git, res.store_root, _push_args(url, branch), url)

    out = _under_guard(res.store_root, body)
    if not out.completed:
        raise SyncError("the push to the sync target could not run ({}); nothing is offered as "
                        "reconciled (fail-closed)".format(out.err.strip()))
    if out.rc != 0:
        err = out.err.strip()
        rejected = ("[rejected]" in err or "non-fast-forward" in err or "fetch first" in err
                    or "stale info" in err)
        if rejected:
            print("opf sync: DIVERGENT: the push was rejected as a non-fast-forward (the target "
                  "advanced between the fetch and the push): the store ({}) and its sync target "
                  "have diverged. Sync merges nothing and picks no side (spec 5.7); the target is "
                  "left exactly as the other side wrote it. Reconcile by hand, then re-run. "
                  "Exit 2.".format(local), file=sys.stderr)
            return EXIT_MALFORMED
        raise SyncError("the push to the sync target failed (rc {}): {}; nothing is offered as "
                        "reconciled (fail-closed)".format(out.rc, err))
    print("opf sync: pushed the pending local commits: the sync target's branch {} now holds "
          "{} (was {}).".format(branch, local, fetched))
    return EXIT_OK


def _run_sync(mode, root):
    """The one operation sequence (see the module docstring). Returns the exit code; raises
    SyncError for a refusal."""
    try:
        res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a mutation
        raise SyncError("unexpected error resolving the store at {!r} ({!r})".format(root, exc))
    if res.status == _opf_store.NOT_ADOPTED:
        raise SyncError("no OPF store at {!r} ({}); run `opf init` first".format(root, res.detail))
    if res.status != _opf_store.RESOLVED:
        raise SyncError("cannot evaluate the store at {!r}: {}".format(root, res.detail))
    manifest = _read_local_manifest(res)
    manifest_rel = "{}/{}".format(res.machine_rel, _opf_store.MANIFEST_NAME)
    _admit_manifest(manifest, "the store manifest {}".format(manifest_rel), "nothing is read further")
    sync_target = _sync_target_of(manifest)
    if sync_target == "":
        _refuse_no_dedicated_target(res)
    tgt, url = validate_target(sync_target)
    git = _git_or_refuse()
    branch = _current_branch(git, res.store_root)
    _require_destination_agreement(sync_target, tgt, url, res.target,
                                   _actual_remote(git, res.store_root))
    fetch_target(git, res.store_root, url, branch)
    local = _rev(git, res.store_root, "HEAD")
    fetched = _rev(git, res.store_root, "FETCH_HEAD")
    state = compare(git, res.store_root, local, fetched)

    if state == "equal":
        print("opf sync: EQUAL: the store and its sync target both hold {} (branch {}); nothing to "
              "reconcile.".format(local, branch))
        return EXIT_OK
    if state == "divergent":
        print("opf sync: DIVERGENT: the store ({}) and its sync target ({}) have diverged (unsynced "
              "commits on two sides). Sync merges nothing and picks no side, halting for the human "
              "in every mode (spec 5.7): a textual merge of the store's TOML records can mangle the "
              "records the standard protects. Reconcile by hand, then re-run. Exit 2.".format(
                  local, fetched), file=sys.stderr)
        return EXIT_MALFORMED
    if state == "behind":
        if mode == "pull":
            return _pull(git, res, root, local, fetched)
        if mode == "push":
            raise SyncError("the store is BEHIND its sync target ({} behind {}); --push pushes an "
                            "AHEAD store only, and the remedy here is the surfaced fast-forward "
                            "pull, `opf sync --pull` (spec 5.7)".format(local, fetched))
        print("opf sync: BEHIND: the store ({}) is behind its sync target ({}). The remedy is the "
              "surfaced fast-forward pull, `opf sync --pull`, then re-run the refused operation "
              "(spec 5.7); bare sync reports and changes nothing. Exit 2.".format(local, fetched),
              file=sys.stderr)
        return EXIT_MALFORMED
    # state == "ahead"
    if mode == "push":
        return _push_pending(git, res, sync_target, tgt, url, branch, local, fetched)
    if mode == "pull":
        raise SyncError("the store is AHEAD of its sync target ({} ahead of {}); --pull "
                        "fast-forwards a BEHIND store only, and the remedy here is the authorized "
                        "push, `opf sync --push` (spec 5.7)".format(local, fetched))
    print("opf sync: AHEAD: the store ({}) is ahead of its sync target ({}): local commits are not "
          "yet pushed. The remedy is the authorized push, `opf sync --push` (spec 5.7); bare sync "
          "reports and changes nothing. Exit 2.".format(local, fetched), file=sys.stderr)
    return EXIT_MALFORMED


def cli(argv):
    """`opf sync ...`: parse, run, and map the outcome to the 0/2 exit contract. Never exit 1."""
    try:
        mode, root = parse_request(list(argv))
        return _run_sync(mode, root)
    except SyncError as exc:
        print("opf sync: refused: {}; exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf sync: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


# --- self-test (the unit leg) ---------------------------------------------------------------------------

def self_test():
    """Vectors over real local git repositories with bare sync targets over the file transport (no
    network): the argument grammar, the no-dedicated-target refusals, the spec 9.2 admission of the
    local and the fetched manifest, the equal/behind/ahead/divergent classification, the read-only
    bare report, the fast-forward pull with its doctor gate, the authorized push with its
    destination agreement, the held-lease refusal (never seized), the hostile-repository-config
    neutralization, the effective URL-rewrite refusal over every config scope, the
    repository-scope include/helper/trust/cookie refusals (every listed key in its plain and its
    subsection form), the pre-git target validation, and the fetch/detached/unborn refusals. The
    load-bearing guards are each also run with the guard dropped (an attribute-patched flip), so the
    vector that watches it is shown to go red. Judged on exit codes, repository state and lease
    bytes, never by output grepping alone. Returns 0 clean, 1 on a failure, 2 on a harness error."""
    _child_status_refusal_exit("_opf_sync.py")
    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    try:
        skipped = _self_test_units(check)
    except Exception as exc:  # noqa: BLE001  a harness fault is cannot-evaluate, never a pass
        print("opf-sync self-test: harness error ({!r}); fail-closed".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    if skipped:
        return EXIT_OK
    if failures:
        print("opf-sync self-test: FAIL ({} checks, {} failed):".format(checked[0], len(failures)),
              file=sys.stderr)
        for f in failures:
            print("  - " + f, file=sys.stderr)
        return 1
    print("opf-sync self-test: PASS ({} checks: grammar, pattern refusals, admission, "
          "classification, pull, push, destination agreement, lease, transport neutralization, "
          "URL-rewrite and repository-config refusals, target validation, fetch and HEAD "
          "refusals, dropped-guard flips)".format(checked[0]))
    return EXIT_OK


def _self_test_units(check):
    import contextlib
    import io
    import shutil
    import subprocess
    import tempfile
    from types import SimpleNamespace
    from unittest.mock import patch

    import _opf_emit
    import _opf_init

    git = _opf_observe._git_path()
    if git is None:
        print("opf-sync self-test: SKIP (git not found on PATH; the committed-store fixtures the "
              "vectors need cannot be built)", file=sys.stderr)
        return True

    module = sys.modules[__name__]
    base = tempfile.mkdtemp(prefix="opf-sync-selftest-")
    home = os.path.join(base, "home")
    os.makedirs(home)
    _GIT_S = 60

    def _git_run(cwd, *args):
        cmd = [git, "--no-replace-objects", "-C", str(cwd),
               "-c", "user.email=opf@example.invalid", "-c", "user.name=OPF Self Test",
               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
               "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + list(args)
        env = _opf_observe._scrubbed_env()
        env["HOME"] = home
        try:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                  timeout=_GIT_S)
        except subprocess.TimeoutExpired:
            raise OSError("git {} timed out after {}s in {}".format(" ".join(args), _GIT_S, cwd))

    def _git(cwd, *args):
        proc = _git_run(cwd, *args)
        if proc.returncode != 0:
            raise OSError("git {} failed in {} (rc {}): {}".format(
                " ".join(args), cwd, proc.returncode,
                (proc.stderr or b"").decode("utf-8", "replace").strip()))
        return (proc.stdout or b"").decode("utf-8", "replace")

    def _write_manifest(root, manifest):
        mdir = Path(root) / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / _opf_store.MANIFEST_NAME).write_text(_opf_emit.emit(manifest), encoding="utf-8")

    def _store(name, sync_target, manifest_mutate=None, commit=True):
        root = os.path.join(base, name)
        os.makedirs(root)
        manifest = _opf_init._manifest_model()
        manifest["store"]["sync_target"] = sync_target
        if manifest_mutate is not None:
            manifest_mutate(manifest)
        _write_manifest(root, manifest)
        (Path(root) / "data.txt").write_text("one\n", encoding="utf-8")
        _git(root, "init")
        if commit:
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "seed store")
        return root

    def _paired(name, manifest_mutate=None):
        # A committed store whose sync target is a local bare repository over the file transport,
        # the store's one remote agreeing with the recorded target.
        bare = os.path.join(base, name + "-target.git")
        _git(base, "init", "--bare", bare)
        root = _store(name + "-store", "git:" + bare, manifest_mutate)
        _git(root, "remote", "add", "origin", bare)
        _git(root, "push", "origin", "main")
        return root, bare

    def _writer_commit(bare, name, rel="data.txt", text="two\n", manifest_text=None):
        # Advance the bare target by one commit through a second working clone (the other system).
        w = os.path.join(base, name + "-writer")
        _git(base, "clone", bare, w)
        if manifest_text is not None:
            mpath = (Path(w) / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
                     / _opf_store.MANIFEST_NAME)
            mpath.write_text(manifest_text, encoding="utf-8")
        else:
            (Path(w) / rel).write_text(text, encoding="utf-8")
        _git(w, "add", "-A")
        _git(w, "commit", "-m", "advance")
        _git(w, "push", "origin", "main")
        return w

    def _local_commit(root, rel, text, message="local change"):
        (Path(root) / rel).write_text(text, encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", message)

    def _head(root):
        return _git(root, "rev-parse", "HEAD").strip()

    def _tip(bare):
        return _git(bare, "rev-parse", "refs/heads/main").strip()

    def _tip_or_none(bare):
        proc = _git_run(bare, "rev-parse", "--verify", "--quiet", "refs/heads/main")
        return (proc.stdout or b"").decode("utf-8", "replace").strip() if proc.returncode == 0 else None

    def _status(root):
        return _git(root, "status", "--porcelain")

    def _lease_path(root):
        return os.path.join(root, _opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR,
                            _opf_check.LEASE_NAME)

    def run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = cli(list(argv))
        return rc, buf.getvalue()

    valid_doctor = SimpleNamespace(status=_opf_store.VALID, findings=[], cannot_evaluate=[])
    failing_doctor = SimpleNamespace(status=_opf_store.INVALID, findings=["synthetic finding"],
                                     cannot_evaluate=[])

    try:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home, GIT_CONFIG_NOSYSTEM="1"):
            # -- the argument grammar and the usage refusal -------------------------------------
            rc, out = run(["--bogus"])
            check("an unrecognized argument is a usage refusal",
                  rc == EXIT_MALFORMED and "usage: opf sync" in out)
            rc, out = run(["--pull", "--push"])
            check("--pull with --push is refused (one surfaced step at a time)",
                  rc == EXIT_MALFORMED and "usage: opf sync" in out)
            rc, _out = run(["--root"])
            check("--root requires a value", rc == EXIT_MALFORMED)
            rc, _out = run(["--root", ""])
            check("an empty --root is refused", rc == EXIT_MALFORMED)

            # -- a not-adopted root is a requested operation, never an applicability probe ------
            empty = os.path.join(base, "empty")
            os.makedirs(empty)
            rc, out = run(["--root", empty])
            check("a not-adopted root is a cannot-evaluate",
                  rc == EXIT_MALFORMED and "no OPF store" in out)

            # -- no dedicated sync target refuses with a named reason ---------------------------
            inrepo = _store("inrepo", "")
            before = (_head(inrepo), _status(inrepo))
            rc, out = run(["--root", inrepo])
            check("an in-repo store with no sync target refuses, naming the named reason",
                  rc == EXIT_MALFORMED and "no dedicated sync target" in out
                  and "branch-and-merge" in out)
            check("the in-repo refusal leaves the tree byte-identical",
                  (_head(inrepo), _status(inrepo)) == before)
            relocated = _store("relocated", "")
            product = os.path.join(base, "relocated-product")
            os.makedirs(product)
            (Path(product) / _opf_store.POINTER_REL).write_text(
                '[store]\ntarget = "dir:{}"\n'.format(relocated), encoding="utf-8")
            rc, out = run(["--root", product])
            check("a relocated local-only store refuses, naming the named reason",
                  rc == EXIT_MALFORMED and "no dedicated sync target" in out and "local-only" in out)

            # -- equal: exit 0 in every mode, nothing written, no lease left --------------------
            eq_root, _eq_bare = _paired("equal")
            before = (_head(eq_root), _status(eq_root))
            for argv in ([], ["--pull"], ["--push"]):
                rc, out = run(argv + ["--root", eq_root])
                check("an equal store reports EQUAL and exits 0 ({})".format(
                    " ".join(argv) or "bare"), rc == EXIT_OK and "EQUAL" in out)
            check("the equal paths change nothing and leave no lease",
                  (_head(eq_root), _status(eq_root)) == before
                  and not os.path.exists(_lease_path(eq_root)))

            # -- behind: bare sync reports read-only; --pull fast-forwards under the claim ------
            b1_root, b1_bare = _paired("behind")
            _writer_commit(b1_bare, "behind")
            local_before = _head(b1_root)
            rc, out = run(["--root", b1_root])
            check("bare sync on a behind store reports BEHIND, exits 2 and moves nothing",
                  rc == EXIT_MALFORMED and "BEHIND" in out and "--pull" in out
                  and _head(b1_root) == local_before)
            rc, out = run(["--push", "--root", b1_root])
            check("--push on a behind store refuses naming --pull",
                  rc == EXIT_MALFORMED and "--pull" in out and _head(b1_root) == local_before)
            with patch.object(module, "_doctor", lambda root: valid_doctor):
                rc, out = run(["--pull", "--root", b1_root])
            check("--pull fast-forwards a behind store to the target tip",
                  rc == EXIT_OK and _head(b1_root) == _tip(b1_bare) and "fast-forward" in out)
            check("the pull releases the single-writer claim (no lease left)",
                  not os.path.exists(_lease_path(b1_root)))

            # a failing doctor after the fast-forward reports, exits 2, and the fast-forward stays
            b2_root, b2_bare = _paired("behind-doctor")
            _writer_commit(b2_bare, "behind-doctor")
            with patch.object(module, "_doctor", lambda root: failing_doctor):
                rc, out = run(["--pull", "--root", b2_root])
            check("a failing doctor after the fast-forward exits 2 with the fast-forward in place",
                  rc == EXIT_MALFORMED and "synthetic finding" in out
                  and _head(b2_root) == _tip(b2_bare) and "LEFT IN PLACE" in out)
            check("the failing-doctor pull still releases the single-writer claim",
                  not os.path.exists(_lease_path(b2_root)))

            # the real doctor runs through the same seam: this minimal fixture is not doctor-VALID,
            # so the pull reports that and exits 2 with the fast-forward in place
            b3_root, b3_bare = _paired("behind-real")
            _writer_commit(b3_bare, "behind-real")
            rc, _out = run(["--pull", "--root", b3_root])
            check("the real post-pull doctor governs the exit and the fast-forward stays",
                  rc == EXIT_MALFORMED and _head(b3_root) == _tip(b3_bare))

            # -- behind: a refused fetched manifest stops the fast-forward (spec 9.2) -----------
            variants = (
                ("drops homes", lambda m: m["opf"].pop("homes")),
                ("declares homes 1", lambda m: m["opf"].__setitem__("homes", 1)),
                ("declares spec_version 1.2.0",
                 lambda m: m["opf"].__setitem__("spec_version", "1.2.0")),
                ("declares a spec_version above the ceiling",
                 lambda m: m["opf"].__setitem__("spec_version", "9.9.9")),
            )
            admit_root = None
            admit_head = None
            for idx, (label, mutate) in enumerate(variants):
                root, bare = _paired("admit-{}".format(idx))
                bad = _opf_init._manifest_model()
                bad["store"]["sync_target"] = "git:" + bare
                mutate(bad)
                _writer_commit(bare, "admit-{}".format(idx), manifest_text=_opf_emit.emit(bad))
                head_before = _head(root)
                rc, _out = run(["--pull", "--root", root])
                check("a fetched manifest that {} refuses with HEAD and worktree unchanged".format(
                    label), rc == EXIT_MALFORMED and _head(root) == head_before
                    and not _status(root))
                admit_root, admit_head = root, head_before
            # the named flip: with the fetched-state admission dropped, the refused manifest lands
            good_model = _opf_init._manifest_model()
            with patch.object(module, "_admit_fetched", lambda g, r, m: good_model), \
                    patch.object(module, "_doctor", lambda root: valid_doctor):
                run(["--pull", "--root", admit_root])
            check("dropping the fetched-state admission lands the refused manifest (shown red)",
                  _head(admit_root) != admit_head)

            # -- the local manifest is admitted before any lease claim (spec 9.2) ---------------
            bad_local = _store("bad-local", "git:/nonexistent-target",
                               manifest_mutate=lambda m: m["opf"].__setitem__("homes", 1))
            guard_calls = []

            def recording_guard(store_root):
                guard_calls.append(store_root)
                raise SyncError("guard reached")

            with patch.object(module, "_acquire_guard", recording_guard):
                rc, out = run(["--root", bad_local])
            check("a refused local manifest stops before any lease claim",
                  rc == EXIT_MALFORMED and "admits only spec_version" in out and not guard_calls
                  and not os.path.exists(_lease_path(bad_local)))

            # -- ahead: bare sync reports; --push lands the pending commits ---------------------
            a1_root, a1_bare = _paired("ahead")
            _local_commit(a1_root, "data.txt", "three\n")
            tip_before = _tip(a1_bare)
            rc, out = run(["--root", a1_root])
            check("bare sync on an ahead store reports AHEAD, exits 2 and pushes nothing",
                  rc == EXIT_MALFORMED and "AHEAD" in out and "--push" in out
                  and _tip(a1_bare) == tip_before)
            rc, out = run(["--pull", "--root", a1_root])
            check("--pull on an ahead store refuses naming --push",
                  rc == EXIT_MALFORMED and "--push" in out and _tip(a1_bare) == tip_before)
            rc, _out = run(["--push", "--root", a1_root])
            check("--push on an ahead store lands the pending commits at the target",
                  rc == EXIT_OK and _tip(a1_bare) == _head(a1_root))
            check("the push releases the single-writer claim",
                  not os.path.exists(_lease_path(a1_root)))

            # -- the push destination must agree with the recorded target (spec 5.6) ------------
            d_root, d_bare = _paired("dest")
            other_bare = os.path.join(base, "dest-other.git")
            _git(base, "init", "--bare", other_bare)
            _git(d_root, "remote", "set-url", "origin", other_bare)
            _local_commit(d_root, "data.txt", "four\n")
            dest_tip = _tip(d_bare)
            rc, out = run(["--push", "--root", d_root])
            check("a push whose recorded remote disagrees with the sync_target refuses",
                  rc == EXIT_MALFORMED and "does not agree" in out)
            check("the destination refusal changes neither bare repository",
                  _tip(d_bare) == dest_tip and _tip_or_none(other_bare) is None)
            # the named flip: with the agreement gate dropped, the push goes out to the recorded URL
            with patch.object(module, "_require_destination_agreement", lambda *a, **k: None):
                run(["--push", "--root", d_root])
            check("dropping the destination-agreement gate pushes (shown red)",
                  _tip(d_bare) == _head(d_root))

            # -- divergence halts in every mode, merging nothing ---------------------------------
            v_root, v_bare = _paired("diverged")
            _writer_commit(v_bare, "diverged", rel="remote.txt", text="r\n")
            _local_commit(v_root, "local.txt", "l\n")
            lv, tv = _head(v_root), _tip(v_bare)
            for argv in ([], ["--pull"], ["--push"]):
                rc, out = run(argv + ["--root", v_root])
                check("a divergent store halts with both tips unchanged ({})".format(
                    " ".join(argv) or "bare"),
                    rc == EXIT_MALFORMED and "DIVERGENT" in out and _head(v_root) == lv
                    and _tip(v_bare) == tv)
            check("no merge commit was created on the divergent store",
                  len(_git(v_root, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 2)
            # the named flip: a non-fast-forward merge in the fast-forward's place lands the merge
            # commit the vector forbids

            def merging(g, r):
                _git(r, "merge", "--no-edit", "FETCH_HEAD")

            with patch.object(module, "compare", lambda g, r, a, b: "behind"), \
                    patch.object(module, "_admit_fetched",
                                 lambda g, r, m: _opf_init._manifest_model()), \
                    patch.object(module, "_fast_forward", merging), \
                    patch.object(module, "_doctor", lambda root: valid_doctor):
                run(["--pull", "--root", v_root])
            check("a non-fast-forward merge creates the forbidden merge commit (shown red)",
                  len(_git(v_root, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3)

            # -- a target advancing between fetch and push halts as divergence, never forced -----
            r_root, r_bare = _paired("race")
            _local_commit(r_root, "data.txt", "five\n")
            rw = os.path.join(base, "race-writer")
            _git(base, "clone", r_bare, rw)
            (Path(rw) / "race.txt").write_text("r\n", encoding="utf-8")
            _git(rw, "add", "-A")
            _git(rw, "commit", "-m", "the other system")
            real_fetch = fetch_target

            def racing_fetch(g, r, url, branch):
                real_fetch(g, r, url, branch)
                _git(rw, "push", "origin", "main")

            with patch.object(module, "fetch_target", racing_fetch):
                rc, out = run(["--push", "--root", r_root])
            other_tip = _head(rw)
            check("a target that advanced between fetch and push halts as a divergence",
                  rc == EXIT_MALFORMED and "DIVERGENT" in out and _tip(r_bare) == other_tip)
            # the named flip: a forced push rewrites the target over the other system's commit

            def forced_args(url, branch):
                return ["push", "--force", url, "HEAD:refs/heads/{}".format(branch)]

            with patch.object(module, "compare", lambda g, r, a, b: "ahead"), \
                    patch.object(module, "_push_args", forced_args):
                run(["--push", "--root", r_root])
            check("a forced push rewrites the target over the other system's commit (shown red)",
                  _tip(r_bare) == _head(r_root) and _tip(r_bare) != other_tip)

            # -- a held lease refuses and is never seized ----------------------------------------
            h_root, h_bare = _paired("held")
            _local_commit(h_root, "data.txt", "six\n")
            lease = Path(_lease_path(h_root))
            payload = ('schema = 1\nholder = "opf-record:{}:{}"\noperation = "record"\n'
                       'acquired_at = "2026-01-01T00:00:00Z"\n').format(
                           os.uname().nodename, os.getpid())
            lease.write_text(payload, encoding="utf-8")
            held_tip = _tip(h_bare)
            rc, _out = run(["--push", "--root", h_root])
            check("a held lease refuses the push with the target unmoved",
                  rc == EXIT_MALFORMED and _tip(h_bare) == held_tip)
            check("the held lease bytes are unchanged (never seized)",
                  lease.read_text(encoding="utf-8") == payload)
            # the named flip: a seizing acquisition removes the holder's lease and proceeds
            real_acquire = _acquire_guard

            def seizing(store_root):
                os.unlink(str(lease))
                return real_acquire(store_root)

            with patch.object(module, "_acquire_guard", seizing):
                run(["--push", "--root", h_root])
            check("a seizing acquisition writes over the held lease (shown red)",
                  not lease.exists() or lease.read_text(encoding="utf-8") != payload)

            # -- hostile repository configuration is neutralized on the transport ----------------
            x_root, x_bare = _paired("hostile")
            hooks = os.path.join(base, "hostile-hooks")
            os.makedirs(hooks)
            sentinel = os.path.join(base, "hostile-sentinel")
            hook = Path(hooks) / "pre-push"
            hook.write_text("#!/bin/sh\n: > {}\nexit 0\n".format(sentinel), encoding="utf-8")
            os.chmod(str(hook), 0o755)
            _git(x_root, "config", "core.hooksPath", hooks)
            _git(x_root, "config", "core.fsmonitor", os.path.join(hooks, "fsmon"))
            _git(x_root, "config", "core.sshCommand", os.path.join(hooks, "ssh"))
            _git(x_root, "config", "protocol.ext.allow", "always")
            _local_commit(x_root, "data.txt", "seven\n")
            rc, _out = run(["--push", "--root", x_root])
            check("the hostile-config push completes with the exec surfaces neutralized",
                  rc == EXIT_OK and _tip(x_bare) == _head(x_root))
            check("the repository pre-push hook never ran (no sentinel)",
                  not os.path.exists(sentinel))
            # the named flip: without the transport neutralization the repository hook runs
            _local_commit(x_root, "data.txt", "eight\n")
            with patch.object(module, "_transport_overrides", lambda g: []):
                run(["--push", "--root", x_root])
            check("dropping the transport neutralization runs the repository hook (shown red)",
                  os.path.exists(sentinel))

            # -- a URL rewrite anywhere in the transport's effective config refuses (spec 5.6) ----
            ins_root, ins_bare = _paired("rewrite")
            ins_other = os.path.join(base, "rewrite-other.git")
            _git(base, "init", "--bare", ins_other)
            _git(ins_root, "push", ins_other, "main:main")
            _local_commit(ins_root, "data.txt", "nine\n")
            ins_head = _head(ins_root)
            ins_tip = _tip(ins_bare)
            gcfg = Path(home) / ".gitconfig"
            gcfg.write_text('[url "{}"]\n\tinsteadOf = {}\n'.format(ins_other, ins_bare),
                            encoding="utf-8")
            for argv in ([], ["--pull"], ["--push"]):
                rc, out = run(argv + ["--root", ins_root])
                check("a global insteadOf matching the verified URL refuses ({})".format(
                    " ".join(argv) or "bare"),
                    rc == EXIT_MALFORMED and "rewrite" in out and _head(ins_root) == ins_head
                    and _tip(ins_bare) == ins_tip and _tip_or_none(ins_other) == ins_tip)
            # the named flip: with the rewrite gate dropped, the push reports success while it
            # lands at the REWRITTEN repository and the verified target never moves
            with patch.object(module, "_refuse_effective_url_rewrite", lambda *a, **k: None):
                rc, _out = run(["--push", "--root", ins_root])
            check("dropping the rewrite gate lands the push at the rewritten repository (shown red)",
                  rc == EXIT_OK and _tip(ins_other) == ins_head and _tip(ins_bare) == ins_tip)
            # pushInsteadOf: the push-side rewrite refuses too, and its flip shows the same redirect
            ins_other2 = os.path.join(base, "rewrite-other2.git")
            _git(base, "init", "--bare", ins_other2)
            gcfg.write_text('[url "{}"]\n\tpushInsteadOf = {}\n'.format(ins_other2, ins_bare),
                            encoding="utf-8")
            rc, out = run(["--push", "--root", ins_root])
            check("a global pushInsteadOf matching the verified URL refuses the push",
                  rc == EXIT_MALFORMED and "rewrite" in out and _tip(ins_bare) == ins_tip
                  and _tip_or_none(ins_other2) is None)
            with patch.object(module, "_refuse_effective_url_rewrite", lambda *a, **k: None):
                rc, _out = run(["--push", "--root", ins_root])
            check("dropping the gate reports success while pushInsteadOf redirects the push "
                  "(shown red)",
                  rc == EXIT_OK and _tip_or_none(ins_other2) == ins_head
                  and _tip(ins_bare) == ins_tip)
            # the match is a prefix match, judged at both edges: a recorded prefix just inside the
            # verified URL still matches and refuses; a value just past it matches nothing and the
            # run proceeds to the ordinary report
            gcfg.write_text('[url "{}"]\n\tinsteadOf = {}\n'.format(ins_other2, ins_bare[:-1]),
                            encoding="utf-8")
            rc, out = run(["--root", ins_root])
            check("an insteadOf prefix just inside the verified URL refuses",
                  rc == EXIT_MALFORMED and "rewrite" in out)
            gcfg.write_text('[url "{}"]\n\tinsteadOf = {}x\n'.format(ins_other2, ins_bare),
                            encoding="utf-8")
            rc, out = run(["--root", ins_root])
            check("an insteadOf value just past the verified URL matches nothing and sync runs",
                  rc == EXIT_MALFORMED and "AHEAD" in out and "rewrite" not in out)
            gcfg.unlink()

            # -- a repository-local include cannot put a planted program on the transport ---------
            inc_root, _inc_bare = _paired("include")
            planted_helper = Path(base) / "include-helper"
            inc_sentinel = os.path.join(base, "include-sentinel")
            planted_helper.write_text("#!/bin/sh\n: > {}\nexit 0\n".format(inc_sentinel),
                                      encoding="utf-8")
            os.chmod(str(planted_helper), 0o755)
            cred_inc = Path(base) / "include-credential"
            cred_inc.write_text("[credential]\n\thelper = !{}\n".format(planted_helper),
                                encoding="utf-8")
            _git(inc_root, "config", "--local", "include.path", str(cred_inc))
            inc_head = _head(inc_root)
            rc, out = run(["--root", inc_root])
            check("a repository-local include carrying a credential helper refuses before any "
                  "transport",
                  rc == EXIT_MALFORMED and "include" in out and not os.path.exists(inc_sentinel)
                  and _head(inc_root) == inc_head)
            ap_root, _ap_bare = _paired("askpass")
            ap_inc = Path(base) / "include-askpass"
            ap_inc.write_text("[core]\n\taskpass = {}\n".format(planted_helper), encoding="utf-8")
            _git(ap_root, "config", "--local", "include.path", str(ap_inc))
            rc, out = run(["--root", ap_root])
            check("a repository-local include carrying core.askpass refuses before any transport",
                  rc == EXIT_MALFORMED and "include" in out and not os.path.exists(inc_sentinel))
            # the named flip: with the repository-config refusal dropped the runs report clean and
            # the planted keys are live in the exact configuration the transport reads (the file
            # transport never prompts for credentials, so liveness is judged on that effective
            # configuration rather than on a prompt the fixture cannot force)
            with patch.object(module, "_refuse_local_redirect_config", lambda *a, **k: None):
                rc1, _out = run(["--root", inc_root])
                rc2, _out = run(["--root", ap_root])
            teff = _transport_env()
            eff1 = _opf_observe._capture_bounded([git, "-C", inc_root, "config", "-z",
                                                  "--name-only", "--list"], teff, 30, 1 << 20)
            eff2 = _opf_observe._capture_bounded([git, "-C", ap_root, "config", "-z",
                                                  "--name-only", "--list"], teff, 30, 1 << 20)
            check("dropping the repository-config refusal leaves the include-planted helper and "
                  "askpass live for the transport (shown red)",
                  rc1 == EXIT_OK and rc2 == EXIT_OK
                  and eff1.completed and b"credential.helper" in eff1.out
                  and eff2.completed and b"core.askpass" in eff2.out)
            # includeIf and a repository-scope https trust override refuse by the same enumeration
            ii_root, _ii_bare = _paired("includeif")
            _git(ii_root, "config", "--local", "includeIf.gitdir:{}/.path".format(ii_root),
                 str(ap_inc))
            rc, out = run(["--root", ii_root])
            check("a repository-local includeIf refuses before any transport",
                  rc == EXIT_MALFORMED and "include" in out)
            sv_root, _sv_bare = _paired("trust")
            _git(sv_root, "config", "--local", "http.sslVerify", "false")
            rc, out = run(["--root", sv_root])
            check("a repository-scope https trust override refuses before any transport",
                  rc == EXIT_MALFORMED and "trust" in out)
            # a repository-scope cookie path refuses, both keys in both forms (each set alone on an
            # equal store, so the refusal is the only thing between it and exit 0); a name just past
            # a listed key matches nothing and the run reports EQUAL
            ck_root, _ck_bare = _paired("cookie")
            ck_head = _head(ck_root)
            ck_jar = os.path.join(base, "cookie-jar")
            for ck_key, ck_value in (("http.cookieFile", ck_jar), ("http.saveCookies", "true"),
                                     ("http.https://example.invalid/.cookieFile", ck_jar),
                                     ("http.https://example.invalid/.saveCookies", "true")):
                _git(ck_root, "config", "--local", ck_key, ck_value)
                rc, out = run(["--root", ck_root])
                _git(ck_root, "config", "--local", "--unset", ck_key)
                check("a repository-scope {} refuses before any transport".format(ck_key),
                      rc == EXIT_MALFORMED and "cookie" in out and _head(ck_root) == ck_head
                      and not os.path.exists(ck_jar))
            for ck_key in ("http.cookieFileX", "http.xcookieFile"):
                _git(ck_root, "config", "--local", ck_key, ck_jar)
                rc, out = run(["--root", ck_root])
                _git(ck_root, "config", "--local", "--unset", ck_key)
                check("{}, a name just past a listed key, matches nothing and sync runs".format(ck_key),
                      rc == EXIT_OK and "EQUAL" in out)
            # the named flip: with the cookie list emptied the refusal no longer fires, so the
            # refusal reads the list, not a copy of it
            _git(ck_root, "config", "--local", "http.cookieFile", ck_jar)
            with patch.object(module, "_LOCAL_COOKIE_KEYS", ()):
                rc, _out = run(["--root", ck_root])
            _git(ck_root, "config", "--local", "--unset", "http.cookieFile")
            check("emptying the cookie list lets the repository cookie path through (shown red)",
                  rc == EXIT_OK)
            # every entry of every _LOCAL_* list refuses, plain, under a subsection and in any case,
            # through the same enumeration (its listing stubbed), while a name one character past
            # the entry does not: a list entry the refusal stopped enforcing shows here
            listed_misses = []
            for family in (_LOCAL_REDIRECT_KEYS, _LOCAL_EXEC_KEYS, _LOCAL_TRUST_KEYS,
                           _LOCAL_COOKIE_KEYS, _LOCAL_INCLUDE_KEYS):
                for listed in family:
                    section, _dot, key = listed.partition(b".")
                    for name, refuses in ((listed, True), (listed.upper(), True),
                                          (section + b".https://example.invalid/." + key, True),
                                          (listed + b"x", False), (section + b".x" + key, False)):
                        listing = _opf_observe._GitOutcome(True, 0, name + b"\x00", "")
                        with patch.object(_opf_observe, "_run_git", lambda *a, **k: listing):
                            try:
                                _refuse_local_redirect_config(git, base)
                                refused = False
                            except SyncError:
                                refused = True
                        if refused != refuses:
                            listed_misses.append(name)
            check("every listed repository-scope key refuses in each form and a name just past it "
                  "does not ({})".format(listed_misses), not listed_misses)

            # -- a dangerous or typed sync_target refuses before any git launch ------------------
            bad_targets = (("git:--upload-pack=/bin/false", "begins with"),
                           ("ext::sh -c true", "remote-helper"),
                           ("github:org/repo", "creation convenience"))
            first_bad_root = None
            for idx, (value, needle) in enumerate(bad_targets):
                root = _store("badtarget-{}".format(idx), value)
                if first_bad_root is None:
                    first_bad_root = root
                launches = []

                def record_launch(*a, **k):
                    launches.append(a)
                    raise AssertionError("git launched on a refused sync_target")

                with patch.object(_opf_observe, "_run_git", record_launch), \
                        patch.object(module, "_run_transport", record_launch), \
                        patch.object(_opf_observe, "_capture_bounded", record_launch):
                    rc, out = run(["--root", root])
                check("sync_target {!r} refuses before any git launch".format(value),
                      rc == EXIT_MALFORMED and needle in out and not launches)
            # the named flip: with the target validation dropped the run reaches a git launch

            def passthrough(sync_target):
                tgt = _opf_store.classify_target(sync_target)
                return tgt, tgt.value.strip()

            launches = []

            def record_only(*a, **k):
                launches.append(a)
                return _opf_observe._GitOutcome(False, None, b"", "recorded")

            with patch.object(module, "validate_target", passthrough), \
                    patch.object(_opf_observe, "_run_git", record_only):
                run(["--root", first_bad_root])
            check("dropping the target validation reaches a git launch (shown red)", bool(launches))

            # -- tracked dirt over the changed paths refuses the pull before the lease -----------
            t_root, t_bare = _paired("dirty")
            _writer_commit(t_bare, "dirty")
            (Path(t_root) / "data.txt").write_text("edited locally, uncommitted\n", encoding="utf-8")
            dirt_guard_calls = []

            def recording_guard_dirty(store_root):
                dirt_guard_calls.append(store_root)
                raise SyncError("guard reached")

            dirty_head = _head(t_root)
            with patch.object(module, "_acquire_guard", recording_guard_dirty):
                rc, out = run(["--pull", "--root", t_root])
            check("tracked dirt over the changed paths refuses the pull before any lease claim",
                  rc == EXIT_MALFORMED and "not clean" in out and not dirt_guard_calls
                  and _head(t_root) == dirty_head)

            # -- a failed fetch is a cannot-evaluate, never equal ---------------------------------
            absent = os.path.join(base, "absent-target.git")
            f_root = _store("fetchfail", "git:" + absent)
            _git(f_root, "remote", "add", "origin", absent)
            rc, out = run(["--root", f_root])
            check("a failed fetch is a cannot-evaluate, never read as equal",
                  rc == EXIT_MALFORMED and "fetch" in out and "EQUAL" not in out)

            # -- a detached or unborn HEAD refuses ------------------------------------------------
            dt_root, _dt_bare = _paired("detached")
            _git(dt_root, "checkout", "--detach")
            rc, out = run(["--root", dt_root])
            check("a detached HEAD refuses", rc == EXIT_MALFORMED and "detached" in out)
            ub_root = _store("unborn", "git:" + os.path.join(base, "unborn-target.git"),
                             commit=False)
            rc, out = run(["--root", ub_root])
            check("an unborn HEAD refuses", rc == EXIT_MALFORMED and "unborn" in out)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return False


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    print("usage: _opf_sync.py --self-test (the verb is `opf sync`)", file=sys.stderr)
    return EXIT_MALFORMED


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This module launches git for every
# fetch, push and read and judges each exit status, and its self-test does the same over real fixtures,
# so under an ignored SIGCHLD a failed fetch or push would read as a success. `opf sync` runs under
# opf.py's own guard; this copy guards a direct launch of this file. It runs as the first statement of
# self_test(), before any launch, and at the entry line before main(); the entry line comes after the
# --self-test statement that opf.py requires first in the __main__ block. The import opf.py makes of
# this module runs nothing; opf.py's opf-sync leg calls self_test() in process, so the guard runs there
# too, after opf.py's own guard at its entry line.
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
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("_opf_sync.py") or main())
