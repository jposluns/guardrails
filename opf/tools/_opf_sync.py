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
spec 5.6 before any fetch or push: the resolved pointer (where it names a remote) must agree with
the recorded sync_target, the store repository's single recorded remote must carry a PLAIN remote
name (below), and EVERY url and pushurl value of that remote (`git remote get-url --all` and
`get-url --push --all`, so a second pushurl can never hide behind an agreeing first one) must agree
with the recorded sync_target, by canonical host+path where both sides canonicalize and by exact
string equality otherwise; several values that do not all agree refuse, and a destination that
appears from anywhere else is surfaced and refused, never fetched from or pushed to. 6. Fetch by
explicit URL on the store's CURRENT branch, recording only FETCH_HEAD from the target. git resolves
a fetch or push argument that EQUALS a configured remote's name to that remote's configuration, so
a remote named with the target's URL string would redirect the fetch to its url values and the push
to every pushurl; sync therefore refuses any configured remote whose name is not a plain remote
name (letters, digits, '-', '_' and '.' only, never '.', '..' or a leading '-'), both at the
repository scopes (the allowlist admits a remote.<name>.* key only under a plain name) and at the
recorded remote itself, and the effective-configuration gate below refuses a remote of ANY scope
whose name equals the verified URL string. Every allowed target string contains ':' or '/', which a
plain name cannot, so once these hold the URL argument cannot resolve as a remote name and needs no
re-encoding. A detached or unborn HEAD refuses. 7. Compare with `merge-base --is-ancestor` both ways
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
FETCH_HEAD under .git, and, where the adopter's own global or system configuration sets
fetch.bundleURI, that bundle's objects and refs/bundles/* refs; a repository-scope one refuses,
below), never the store tree, which is disclosed here rather than gated.

The transport boundary (fetch and push only; every other git call runs under the read-only scrubbed
boundary of _opf_observe._run_git): the adopter's global and system git configuration IS honoured,
so credential helpers and SSH keys work (HOME, XDG_CONFIG_HOME and the safe GIT_CONFIG_NOSYSTEM
toggle are carried, with SSH_AUTH_SOCK for an agent-held key), while every other ambient GIT_*
variable is dropped by construction and the env-based config overrides are dropped, not honoured
(they can set any key). Repository-local exec surfaces where a command-scope override wins are
forced off for the transport call: core.hooksPath to the null device (no repository pre-push or
other hook runs), core.fsmonitor off, protocol.allow=never with only file, https and ssh
re-allowed, and core.sshCommand pinned to the adopter's own global-or-system value (or plain `ssh`
where none is set), so a repository-local core.sshCommand never executes while a global one is
honoured as the adopter's own setting. Two gates then protect every fetch and push, both
fail-closed. First, the repository scopes (--local and --worktree) are listed by name and every
listed key must sit on a CLOSED ALLOWLIST of keys that, under the gates stated here, can neither
name a program nor redirect the transport: core.repositoryformatversion, core.filemode, core.bare,
core.logallrefupdates, core.ignorecase, core.precomposeunicode and core.symlinks (the keys a
DEFAULT `git init` writes; a reftable or partial-clone repository writes format keys outside the
allowlist, an accepted cost disclosed below), remote.<name>.url, remote.<name>.fetch and
remote.<name>.pushurl, admitted ONLY where <name> is a plain remote name (any other subsection
refuses: git resolves a transport argument equal to a remote's name to that remote's
configuration) and held to the spec 5.6 destination agreement, which reads EVERY url and pushurl
value of the one recorded remote (`git remote get-url --all` and `get-url --push --all`) and
refuses any value that does not agree with the recorded sync_target, branch.<name>.remote and
branch.<name>.merge
(upstream records), user.name and user.email (identity data), and extensions.worktreeConfig and
extensions.objectFormat (repository-format records; worktreeConfig is what gives the --worktree
scope enumerated here its own file). ANY key outside that allowlist refuses sync before the fetch
and before any local step that could reach a repository-chosen program, naming the key: the key
belongs in the adopter's own global configuration. Neither this module nor `opf init` writes a
repository-scope key at source (this module injects its transport overrides only as command-scope
GIT_CONFIG_* pairs, `opf init` runs no git config write, and the self-test fixtures write only what
`git init` and `git remote add` write, plus the hostile keys each vector plants deliberately), so
an ordinary store passes. The per-key refusals below are therefore DOCUMENTATION of why such a key
is dangerous, named in the refusal where one matches, while the allowlist is the gate, so the
property holds by construction. Among the documented keys: a
credential helper, core.askpass, core.gitProxy, core.alternateRefsCommand, gc.recentObjectsHook,
a push.gpgSign /
gpg.program / gpg.<format>.program / gpg.ssh.defaultKeyCommand push-signing key, a remote.<name>.receivepack /
uploadpack / vcs program name, a per-protocol protocol.<name>.allow (protocol.allow included), an
http.sslVerify / sslCAInfo / sslCAPath server-trust override, an http.cookieFile / saveCookies
cookie path, a fetch.bundleURI bundle source, a url.*.insteadOf / pushInsteadOf rewrite, and
include.path / includeIf.*.path are each REFUSED, in the plain and the subsection form alike
(http.cookieFile and http.<url>.cookieFile): a repository-planted helper is a program launch, a
repository core.alternateRefsCommand names a program the fetch itself runs with each alternate
recorded under .git/objects/info/alternates (a store made with clone --reference or --shared
carries that file legitimately) while it advertises the alternates' tips, a repository
gc.recentObjectsHook names a program the auto-maintenance a fetch can spawn in the store
repository would run, a
repository push.gpgSign makes the push sign its certificate where the target advertises signing,
running the signing program the configuration names, and a repository gpg.program, any
gpg.<format>.program or gpg.ssh.defaultKeyCommand makes that a repository-chosen program, a
repository remote.<name>.receivepack, uploadpack or vcs names the pack program or remote helper
git runs where the configured remote's name is the very URL the transport is given, a repository
per-protocol protocol.<name>.allow is read over the command-scope protocol.allow=never override
(the specific key outranks the general one, whatever the scope) and could re-widen the transport
onto a program-running helper, a repository trust override can authenticate an interposed https
server, a repository cookie path makes the https transport read cookies from, or write a cookie
jar to, a file the repository chooses (a filesystem effect, not routing or shaping), a repository
bundle source makes a fetch also download a bundle from a location the repository chooses and
write its objects and refs/bundles/* refs under .git (a fetch source the destination agreement
never verified), and a repository-local include is read by the transport but not expanded by a
scoped listing, so its keys could not be verified by name. The same enumeration refuses a
repository-scope clean, smudge or process filter driver: git runs the program such a key names
while it materializes working-tree content, so the --pull fast-forward's checkout (the first local
step that writes the worktree, with .gitattributes assigning the driver to a changed path) would
otherwise run a repository-chosen program before the post-pull doctor grades the result. It also
refuses a repository-scope external diff, textconv or merge driver: git runs the program such a
key names while it compares or merges working-tree content, and no step of the reference pull
path is shown to reach those drivers, so they are refused as fail-closed defence in depth, at the
cost that an adopter with a repository-scope textconv (or external diff or merge driver) must
move it to global configuration before sync will run. Every local step reads
global and system config as the null device (the scrubbed observation boundary), so the repository's
own scope is the only one from which such a program is reachable, and it is refused before any fetch
or checkout rather than forced off on each later local worktree step one by one.
Second, the EFFECTIVE url.* rewrites are read under the exact environment and override pairs the
transport call runs with, so every source git would honour (global, system, local, worktree,
include, command) is the source checked, and any insteadOf or pushInsteadOf whose prefix matches
the verified URL refuses: the URL the destination agreement verified is then, byte for byte, the
URL the transport uses. The same effective enumeration refuses a configured remote OF ANY SCOPE
whose name equals the verified URL string: git resolves a fetch or push argument that equals a
remote's name to that remote's configuration, so such a remote (even one in the adopter's own
global configuration, which the repository-scope gates cannot see) would redirect the transport
exactly as a matching insteadOf would, while every ordinarily named remote of the adopter's
matches nothing here and keeps working. Refusal is chosen over an override in these gates because
a GIT_CONFIG_* pair appends to a multi-valued key rather than resetting it (an empty insteadOf
value is itself a match-everything rewrite), while an unrelated rewrite of the adopter's own
matches nothing here and keeps working.

SUBMODULES: a store whose repository carries any git submodule machinery refuses before any step:
a .gitmodules file at the store root or at the repository toplevel (the only working-tree path git
reads it from), a gitlink (mode 160000) entry anywhere in the INDEX (`ls-files -s`, read bounded
from the toplevel: a staged-only embedded repository already carries its own live config, and the
clean-check status probe descends into it) or anywhere in HEAD's WHOLE tree
(`ls-tree -r --full-tree`, so a store nested below the toplevel cannot sit beside machinery
outside its own subtree), or any entry under the repository's modules directory (.git/modules). A
submodule's own repository (its config under .git/modules/<name>/, or inside a staged embedded
repository) is a configuration scope the repository-scope allowlist cannot enumerate,
and git's worktree operations (the clean-check status probe, a checkout) descend into a populated
submodule and can run a program that config names (a clean, smudge or process filter the
submodule's own .gitattributes assigns), so the whole class is refused rather than enumerated.
The shared status probe (_opf_write_guard.probe_dirty) is deliberately left unchanged: passing
--ignore-submodules=all there would also change the record verb's clean gate on a
submodule-bearing store (dirt inside a submodule would stop being surfaced), which is not shown
unaffected, while under sync the refusal here means the probe never sees a submodule.

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
and its receive side is its own trust domain. THE ALLOWLIST RULE AND ITS ACCEPTED COST: the
repository scopes (--local and --worktree) must hold ONLY the allowlisted keys named in the
transport paragraph above; a store whose repository configuration holds ANY other key is refused
until that key is moved to the adopter's global configuration. That refuses even keys that only
route, shape or make the transport read a file and were shown to name no program here (http.proxy,
http.curloptResolve, http.extraHeader, http.postBuffer and the sizing family, protocol.version,
ssh.variant, http.sslCert / sslKey, the proxy TLS paths and http.pinnedPubkey): at repository
scope each is refused by construction, and each keeps working from the adopter's global
configuration, which the transport honours. core.autocrlf is refused at repository scope too:
this module does not otherwise handle content rewriting, so it is not shown safe here.
extensions.refStorage (written by `git init --ref-format=reftable`), extensions.partialClone,
remote.<name>.promisor and remote.<name>.partialclonefilter (written by `git clone --filter`) are
refused too, and for THESE keys the usual remedy does not apply: git reads them in the repository
itself, so moving them to global configuration would break the repository rather than restore
sync. A store in a reftable or partial-clone repository is outside sync's scope until the store is
recreated in a default-format, full clone (an accepted cost: a partial clone can also lazily fetch
through a promisor remote this module never verified, so the class is not shown safe to
allowlist). remote.<name>.url and pushurl stay data under three gates that all run before any
transport: the single-remote rule (a second remote refuses), the plain-remote-name rule (a remote
named with a URL string, which git would resolve a transport argument to, refuses by
construction), and the destination agreement over EVERY url and pushurl value of the recorded
remote (`git remote get-url --all` and `get-url --push --all`; git fetches from the url values and
pushes to every pushurl, so several values that do not all agree refuse), while the program-naming
remote keys (receivepack,
uploadpack, vcs) are outside the allowlist like everything else. A store carrying any submodule
machinery is refused outright (above): a store with submodules is out of sync's scope until the
submodule is removed. The destination agreement compares by exact
string where a form (a file path, a file:// URL) has no canonical host+path, so two spellings of one
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
# DOCUMENTATION LISTS: repository-scope config keys whose danger is named, kept so the refusal can
# say WHY where one matches. The gate itself is the allowlist further below: a key on none of these
# lists still refuses when it is outside the allowlist. Over both the --local and --worktree
# listings (section/key folded, the subsection kept): a URL rewrite silently redirects the verified
# destination; a repository-planted credential helper, askpass or git proxy is a program launch on
# fetch or push, a repository-scope core.alternateRefsCommand names a program the fetch itself
# runs with each alternate recorded under .git/objects/info/alternates while it advertises the
# alternates' tips, and a repository-scope gc.recentObjectsHook names a program the
# auto-maintenance a fetch can spawn in the store repository would run;
# a repository-scope push.gpgSign turns on push-certificate signing where the target
# advertises it, which runs the configured signing program during the push, and a repository-scope
# gpg.program, gpg.<format>.program or gpg.ssh.defaultKeyCommand makes that a repository-chosen
# program; a repository-scope remote.<name>.receivepack, uploadpack or vcs names the pack program or
# remote helper git runs where the configured remote's name is the URL the transport is given; a
# repository-scope per-protocol protocol.<name>.allow is read over the command-scope
# protocol.allow=never override and could re-widen the transport onto a program-running helper; a
# repository-scope https trust override can authenticate an interposed server; a
# repository-scope cookie file or cookie save makes the https transport read cookies from, or write a
# cookie jar to, a path the repository chooses; a repository-scope bundle URI makes a fetch also
# download a bundle from a location the repository chooses, writing its objects and refs/bundles/*
# refs under .git; and a repository-local include is read by the transport but not expanded by a
# scoped listing, so its keys could not be verified by name.
# Overriding these would also discard the adopter's own global configuration, so presence refuses
# instead; the effective url.* rewrites of EVERY scope are additionally matched against the verified
# URL before each transport call. Each entry is a folded section.key, and a name refuses when it is
# that key with or without a subsection between the two (http.sslVerify and http.<url>.sslVerify,
# url.<base>.insteadOf, includeIf.<condition>.path, gpg.program and gpg.<format>.program,
# gpg.defaultkeycommand and gpg.ssh.defaultKeyCommand, protocol.allow and protocol.<name>.allow);
# _refuse_local_redirect_config matches these lists only to word its refusal, and the gate it
# enforces is the allowlist below, so a key outside every named list still refuses.
_LOCAL_REDIRECT_KEYS = (b"url.insteadof", b"url.pushinsteadof")
_LOCAL_EXEC_KEYS = (b"credential.helper", b"core.askpass", b"core.gitproxy",
                    b"core.alternaterefscommand", b"gc.recentobjectshook")
_LOCAL_SIGN_KEYS = (b"push.gpgsign", b"gpg.program", b"gpg.defaultkeycommand")
_LOCAL_REMOTE_PROGRAM_KEYS = (b"remote.receivepack", b"remote.uploadpack", b"remote.vcs")
_LOCAL_PROTOCOL_KEYS = (b"protocol.allow",)
_LOCAL_TRUST_KEYS = (b"http.sslverify", b"http.sslcainfo", b"http.sslcapath")
_LOCAL_COOKIE_KEYS = (b"http.cookiefile", b"http.savecookies")
_LOCAL_BUNDLE_KEYS = (b"fetch.bundleuri",)
_LOCAL_INCLUDE_KEYS = (b"include.path", b"includeif.path")
# Repository-scope keys that name a program git runs while it MATERIALIZES or COMPARES working-tree
# content (not on the fetch/push transport, which moves objects and never touches the worktree): a
# filter.<name>.clean, smudge or process driver runs on the --pull fast-forward's checkout (the first
# local step that writes the worktree, with .gitattributes assigning the driver to a changed path),
# and before the post-pull doctor sees the result. A diff.<name>.command / textconv or merge.<name>
# .driver names a program git runs while it compares or merges working-tree content; no step of the
# reference pull path is shown to reach those drivers (the fast-forward checks out, it never
# content-diffs or merges), so they stay refused as fail-closed defence in depth, at the cost that
# an adopter with a repository-scope textconv (or external diff or merge driver) must move it to
# global configuration before sync will run. Every local
# step reads global and system config as the null device (the scrubbed observation env), so the
# repository's OWN scope is the only one from which such a program is reachable on those steps: the key
# is refused before any fetch or checkout, rather than the fast-forward and every later local worktree
# step (check_clean's status, the post-pull doctor's reads) run with the driver forced off one by one,
# because refusing here leaves no step from which a repository-chosen program is reachable. The driver
# program belongs in the adopter's own global configuration (spec 5.6), like the exec keys above.
_LOCAL_FILTER_KEYS = (b"filter.clean", b"filter.smudge", b"filter.process")
_LOCAL_CONTENT_DRIVER_KEYS = (b"diff.command", b"diff.textconv", b"merge.driver")
# Repository-format and clone-shape records (extensions.refStorage is written by
# `git init --ref-format=reftable`; the other three by `git clone --filter`). They are refused like
# every other unlisted key, but with their OWN remedy text: unlike every other refused key they
# cannot move to the adopter's global configuration (git reads them in the repository itself, so
# moving them would break the repository), so the store must be recreated in a default-format, full
# clone. They are not allowlisted because the class is not shown data-only here: a partial clone can
# lazily fetch through a promisor remote this module never verified.
_LOCAL_FORMAT_KEYS = (b"extensions.refstorage", b"extensions.partialclone",
                      b"remote.promisor", b"remote.partialclonefilter")
# THE GATE: the closed allowlist of repository-scope keys sync accepts, each chosen because it is
# data that can neither name a program nor redirect the transport. Everything else refuses by
# construction (_refuse_local_redirect_config), so a newly learned dangerous key never needs a new
# list entry. Provenance of each entry:
# - core.repositoryformatversion, core.filemode, core.bare and core.logallrefupdates: `git init`
#   writes these into every repository it creates (the stores this module's own self-test builds,
#   _self_test_units _store and _paired, carry exactly these); core.ignorecase,
#   core.precomposeunicode and core.symlinks are the same `git init` probe results on other
#   platforms and filesystems.
# - remote.<name>.url, remote.<name>.fetch and remote.<name>.pushurl: `git remote add` writes the
#   first two (the _paired fixture does). They are admitted ONLY under a plain remote name
#   (_remote_name_plain; any other subsection refuses, because git resolves a fetch or push
#   argument equal to a configured remote's name to that remote's configuration, and a remote
#   named with the target's URL string would redirect the transport), and the spec 5.6 destination
#   agreement reads EVERY url and pushurl value of the one recorded remote
#   (`git remote get-url --all` and `get-url --push --all`, _remote_url_values) and refuses any
#   value that disagrees with the recorded sync_target before any transport.
# - branch.<name>.remote and branch.<name>.merge: upstream records (`git push -u` and
#   `git branch --set-upstream-to` write them); names of refs and remotes, not programs.
# - user.name and user.email: identity data.
# - extensions.worktreeconfig: the extension that gives the --worktree scope its own file, a scope
#   this gate already enumerates; extensions.objectformat: the repository's recorded hash
#   algorithm.
# Deliberately NOT listed: core.autocrlf (this module does not otherwise handle content rewriting,
# so it is not shown safe here and stays refused at repository scope); every http.*, protocol.*
# and ssh.* routing or shaping key (data-only cannot be shown for the class, so they belong in the
# adopter's global configuration); the format and clone-shape records (_LOCAL_FORMAT_KEYS above,
# whose remedy is recreating the store in a default-format full clone, never a move to global
# configuration); and submodule.* (a store with submodule machinery refuses
# outright, _refuse_submodules). No opf tool writes a repository-scope key in production code:
# `opf init` runs no git config write, and this module injects its transport overrides only as
# command-scope GIT_CONFIG_* pairs that never touch .git/config.
# Matching (_local_key_allowlisted): the exact folded name for _ALLOWED_EXACT_KEYS, or section.key
# with a NON-EMPTY subsection between them for _ALLOWED_SUBSECTION_KEYS (remote.<name>.url; plain
# remote.url, an empty subsection, or an extra subsection such as core.<x>.bare is outside the
# allowlist and refuses), where a remote.* subsection must additionally be a plain remote name
# (_remote_name_plain).
_ALLOWED_EXACT_KEYS = (
    b"core.repositoryformatversion", b"core.filemode", b"core.bare", b"core.logallrefupdates",
    b"core.ignorecase", b"core.precomposeunicode", b"core.symlinks",
    b"user.name", b"user.email",
    b"extensions.worktreeconfig", b"extensions.objectformat")
_ALLOWED_REMOTE_SUBSECTION_KEYS = (b"remote.url", b"remote.fetch", b"remote.pushurl")
_ALLOWED_SUBSECTION_KEYS = _ALLOWED_REMOTE_SUBSECTION_KEYS + (b"branch.remote", b"branch.merge")
# A fetch or push argument that EQUALS a configured remote's NAME resolves to that remote's
# configuration (git remote.c, remote_get: an exact name lookup over every config scope; only a
# name beginning with '/' is skipped at config parse, with a warning, and valid_remote_nick --
# no empty name, no '.' or '..', no directory separator -- gates just the legacy .git/branches and
# .git/remotes files), so git's own rule leaves a URL string usable as a remote name, and a remote
# named with the verified URL string would redirect the fetch to its url values and the push to
# EVERY pushurl. The plain-name rule here is deliberately stricter than git's: letters, digits,
# '-', '_' and '.' only, never '.', '..' or a leading '-'. Every allowed sync target string
# contains ':' or '/' (validate_target admits only scheme URLs, scp-style host:path and absolute
# paths), which a plain name cannot, so no plainly named remote can collide with the transport
# argument.
_REMOTE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
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


def _remote_name_plain(name):
    """Whether one configured remote name is a PLAIN remote name this module accepts: letters,
    digits, '-', '_' and '.' only (no ':', '/', '@', whitespace or any other character), never '.',
    '..' or a name beginning with '-'. git's own rule at source is far wider (remote.c:
    handle_config skips only a name beginning with '/', and valid_remote_nick gates just the legacy
    .git/branches and .git/remotes files), wide enough that a URL string is a usable remote name
    that a fetch or push argument would resolve to; this stricter rule exists so no configured
    remote name can equal an allowed sync target string, every one of which contains ':' or '/'."""
    return (_REMOTE_NAME_RE.fullmatch(name) is not None
            and name not in (".", "..") and not name.startswith("-"))


def _refuse_remote_shadow(name, url):
    """Refuse a recorded remote whose name could resolve as the transport argument: a name that is
    not a plain remote name (_remote_name_plain), or one that equals the verified URL string (the
    plain-name rule already excludes every allowed URL shape, so the equality leg is defence in
    depth). Validated BEFORE the name is ever passed back to git as an argument."""
    if not _remote_name_plain(name):
        raise SyncError("the store repository's recorded remote is named {!r}, not a plain remote "
                        "name (letters, digits, '-', '_' and '.' only, never '.', '..' or a "
                        "leading '-'): git resolves a fetch or push argument that equals a "
                        "configured remote's name to that remote's configuration, so such a name "
                        "could redirect the transport past the spec 5.6 destination agreement; "
                        "rename the remote and re-run (fail-closed)".format(name))
    if name == url:
        raise SyncError("the store repository's recorded remote is named exactly the verified sync "
                        "target string {!r}; git would resolve the transport argument to that "
                        "remote's configuration instead of the verified URL (spec 5.6); rename the "
                        "remote and re-run (fail-closed)".format(name))


def _remote_url_values(git, store_root, url):
    """The store repository's single recorded remote with EVERY one of its fetch and push URL
    values: (name, url values, pushurl values), through the read-only observation boundary.
    ("", [], []) is an observed no-remote. Several remotes refuse (which of several is
    authoritative is not git's to guess); the remote's name is validated BEFORE it is passed back
    to git as an argument (_refuse_remote_shadow); `git remote get-url --all` and
    `get-url --push --all` supply every value (git fetches from the url values and pushes to EVERY
    pushurl, and the push listing falls back to the url values exactly as git's own push does); an
    unreadable listing or an empty value set is a cannot-evaluate refusal, never a silently skipped
    agreement."""
    listing = _opf_observe._run_git(git, store_root, ["remote"])
    if not listing.completed or listing.rc != 0:
        raise SyncError("could not list the store repository's remotes ({}); the spec 5.6 "
                        "destination agreement is required before any fetch or push "
                        "(fail-closed)".format(
                            listing.err.strip() if not listing.completed
                            else "rc {}: {}".format(listing.rc, listing.err.strip())))
    names = [ln.strip() for ln in listing.out.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not names:
        return "", [], []
    if len(names) > 1:
        raise SyncError("the store repository declares {} remotes ({}); sync verifies ONE recorded "
                        "remote against the recorded sync_target, and which of several is "
                        "authoritative is not git's to guess (spec 5.6); nothing is fetched or "
                        "pushed (fail-closed)".format(len(names), ", ".join(sorted(names))))
    name = names[0]
    _refuse_remote_shadow(name, url)
    collected = []
    for what, args in (("url", ["remote", "get-url", "--all", name]),
                       ("pushurl", ["remote", "get-url", "--push", "--all", name])):
        out = _opf_observe._run_git(git, store_root, args)
        if not out.completed or out.rc != 0:
            raise SyncError("could not read every {} value of the remote {!r} ({}); the spec 5.6 "
                            "destination agreement is required before any fetch or push "
                            "(fail-closed)".format(
                                what, name,
                                out.err.strip() if not out.completed
                                else "rc {}: {}".format(out.rc, out.err.strip())))
        values = [v for v in out.out.decode("utf-8", "replace").splitlines() if v.strip()]
        if not values:
            raise SyncError("the remote {!r} reports no {} value; the spec 5.6 destination "
                            "agreement cannot be evaluated (fail-closed)".format(name, what))
        collected.append(values)
    return name, collected[0], collected[1]


def _require_destination_agreement(sync_target, tgt, url, pointer_target, git, store_root):
    """The spec 5.6 egress rule before any fetch or push: the resolved pointer (where it names a
    remote) must agree with the recorded sync target, the store repository's single recorded remote
    must carry a plain remote name (_refuse_remote_shadow, inside _remote_url_values), and EVERY
    url and pushurl value of that remote must agree with the recorded sync target, by canonical
    host+path where both sides canonicalize, by exact string equality otherwise. git fetches from
    the url values and pushes to EVERY pushurl, so an agreeing first value proves nothing about the
    rest; several values that do not all agree refuse. A destination that appears from anywhere
    else is surfaced and refused, never fetched or pushed."""
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
    name, fetch_urls, push_urls = _remote_url_values(git, store_root, url)
    if name == "":
        raise SyncError("the manifest declares a dedicated sync_target {!r} but the store repository "
                        "has no recorded remote (spec 5.6); the destination cannot be confirmed "
                        "against the repository's own record, so nothing is fetched or pushed; add "
                        "the agreeing remote and re-run (fail-closed)".format(sync_target))
    for what, values in (("url", fetch_urls), ("pushurl", push_urls)):
        for actual in values:
            acanon = _opf_check._canonical_remote(actual)
            if acanon is not None and tcanon:
                agree = _opf_check._canon_sets_agree({acanon}, tcanon, tgt.kind)
            else:
                agree = actual.strip() == url
            if not agree:
                raise SyncError("the store repository's recorded remote {!r} carries a {} value "
                                "{!r} that does not agree with the manifest sync_target {!r} "
                                "(spec 5.6): git fetches from the url values and pushes to EVERY "
                                "pushurl, so each value must agree; a destination from ambient "
                                "configuration or a stale remote is surfaced and refused, never "
                                "fetched from or pushed to (fail-closed)".format(
                                    name, what, actual, sync_target))


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


def _remote_key_subsection(low):
    """The subsection of a folded remote.<name>.url / .fetch / .pushurl key, or None where the name
    is not that shape (an empty subsection returns None and stays outside the allowlist). The
    subsection carries the remote's NAME, which git's -z listing preserves byte for byte between
    the folded section and key; folding the whole name first is harmless here because the plain
    charset is closed under case folding."""
    for listed in _ALLOWED_REMOTE_SUBSECTION_KEYS:
        section, _dot, key = listed.partition(b".")
        if (low.startswith(section + b".") and low.endswith(b"." + key)
                and len(low) > len(section) + len(key) + 2):
            return low[len(section) + 1:-(len(key) + 1)]
    return None


def _local_key_allowlisted(low):
    """Whether one folded repository-scope config name is on the closed sync allowlist: an exact
    listed name (_ALLOWED_EXACT_KEYS), or a listed section.key with a NON-EMPTY subsection between
    the section and the key (_ALLOWED_SUBSECTION_KEYS; the subsection itself may contain dots, as a
    remote named `a.b` does). A remote.<name>.url/.fetch/.pushurl key is allowlisted ONLY where
    <name> is a plain remote name (_remote_name_plain): git resolves a fetch or push argument equal
    to a configured remote's name to that remote's configuration, so a remote named with a URL
    string refuses by construction, whatever keys it carries. Plain remote.url, an empty subsection
    (remote..url) and an extra subsection on an exact key (core.<x>.bare) all fall outside the
    allowlist and refuse."""
    if low in _ALLOWED_EXACT_KEYS:
        return True
    sub = _remote_key_subsection(low)
    if sub is not None:
        return _remote_name_plain(sub.decode("utf-8", "replace"))
    for listed in (b"branch.remote", b"branch.merge"):
        section, _dot, key = listed.partition(b".")
        if (low.startswith(section + b".") and low.endswith(b"." + key)
                and len(low) > len(section) + len(key) + 2):
            return True
    return False


def _refuse_local_redirect_config(git, store_root):
    """THE REPOSITORY-CONFIG ALLOWLIST GATE: enumerate both repository scopes (--local and
    --worktree) by name and refuse the first key that is not allowlisted (_local_key_allowlisted),
    so a key that could name a program or redirect the transport is refused BY CONSTRUCTION rather
    than recognized one by one. The refusal names the key and, where the key sits on one of the
    documentation lists below, says why that family is dangerous; otherwise it says the key is
    outside the allowlist and belongs in the adopter's own global configuration. The documented
    families, each refused for a named reason: url.*.insteadOf /
    pushInsteadOf (a silent rewrite of the verified URL), a credential helper, core.askpass,
    core.gitProxy, core.alternateRefsCommand (a program the fetch itself runs with each alternate
    recorded under .git/objects/info/alternates while it advertises the alternates' tips),
    gc.recentObjectsHook (a program the auto-maintenance a fetch can spawn would run),
    a push.gpgSign / gpg.program / gpg.<format>.program / gpg.ssh.defaultKeyCommand
    push-signing key (push.gpgSign makes the push sign its certificate where the target advertises
    signing, running the configured signing program, and the gpg keys make that program a
    repository-chosen one), a remote.<name>.receivepack / uploadpack / vcs program name (git runs
    it where the configured remote's name is the URL the transport is given), a per-protocol
    protocol.<name>.allow (read over the command-scope protocol.allow=never override, so it could
    re-widen the transport onto a program-running helper), an http.sslVerify / sslCAInfo /
    sslCAPath server-trust override, an
    http.cookieFile / saveCookies cookie path (the https transport reads cookies from, or writes a
    cookie jar to, a file the repository names), a fetch.bundleURI bundle source (a fetch also
    downloads a bundle from the location the repository names, writing its objects and
    refs/bundles/* refs under .git), include.path / includeIf.*.path (the transport
    reads the included file, but a scoped listing does not expand it, so the included keys could not
    be verified by name), a filter.<name>.clean / smudge / process driver (git runs the program such
    a key names while it materializes working-tree content: the --pull fast-forward's checkout runs
    the smudge before the post-pull doctor sees the result, and the local steps read global and
    system config as the null device, so the repository's own scope is the only one that could reach
    such a program, refused here before any fetch or checkout), and a diff.<name>.command / textconv
    or merge.<name>.driver driver (git runs the program such a key names while it compares or merges
    working-tree content; no step of the reference pull path is shown to reach those drivers, so
    they stay refused as fail-closed defence in depth, at the cost that an adopter with a
    repository-scope textconv must move it to global configuration), and a repository-format or
    clone-shape record (extensions.refStorage, extensions.partialClone, remote.<name>.promisor,
    remote.<name>.partialclonefilter), whose refusal names its OWN remedy: such a key cannot move
    to global configuration (git reads it in the repository itself), so the store must be recreated
    in a default-format, full clone. A remote.<name>.url/.fetch/.pushurl key whose subsection is
    not a plain remote name refuses with the remote-name reason (git resolves a transport argument
    equal to a remote's name to that remote's configuration), not the move-to-global one. The
    family texts are matched
    from the _LOCAL_* lists alone (_local_key_listed) and only word the refusal; the gate is the
    allowlist.
    Both repository scopes are enumerated, --local and --worktree (without the worktreeConfig
    extension the latter reads the same .git/config), through the scrubbed read-only boundary
    (--name-only: no value, so a malicious command body is never read); an unreadable scope is a
    cannot-evaluate, never read as empty."""
    refusals = (
        (_LOCAL_REDIRECT_KEYS,
         ", a URL rewrite that would silently redirect the verified destination (spec 5.6); remove "
         "it from the repository config and re-run (fail-closed)"),
        (_LOCAL_EXEC_KEYS,
         ", a repository-planted program launch on fetch or push (a credential helper, askpass or "
         "git proxy runs when the transport needs it, core.alternateRefsCommand runs inside "
         "the fetch itself whenever the store has objects/info/alternates, and "
         "gc.recentObjectsHook runs inside the auto-maintenance a fetch can spawn); such programs "
         "belong in the adopter's own global configuration (spec 5.6); remove it and re-run "
         "(fail-closed)"),
        (_LOCAL_SIGN_KEYS,
         ", a repository-scope push-signing key: push.gpgSign makes the push sign its certificate "
         "where the target advertises signing, running the configured signing program, and "
         "gpg.program, gpg.<format>.program or gpg.ssh.defaultKeyCommand makes that a "
         "repository-chosen program; the signing decision and the signing program belong to the "
         "adopter's own global configuration (spec 5.6); remove it and re-run (fail-closed)"),
        (_LOCAL_REMOTE_PROGRAM_KEYS,
         ", a repository-scope pack program or remote helper name that git runs where the "
         "configured remote's name is the URL the transport is given; the pack programs and "
         "helpers belong to the adopter's own global configuration (spec 5.6); remove it and "
         "re-run (fail-closed)"),
        (_LOCAL_PROTOCOL_KEYS,
         ", a repository-scope protocol allow that is read over the transport's "
         "protocol.allow=never override and could re-widen the transport onto a program-running "
         "helper; the protocol surface belongs to the adopter's own global configuration "
         "(spec 5.6); remove it and re-run (fail-closed)"),
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
         "global configuration (spec 5.6); remove it and re-run (fail-closed)"),
        (_LOCAL_BUNDLE_KEYS,
         ", a repository-chosen bundle source: a fetch would also download a bundle from the "
         "location the repository names and write its objects and refs/bundles/* refs under .git; "
         "a bundle source belongs to the adopter's own global configuration (spec 5.6); remove it "
         "and re-run (fail-closed)"),
        (_LOCAL_FILTER_KEYS,
         ", a repository-scope clean, smudge or process filter driver: git runs the program it names "
         "while it materializes or compares working-tree content, so the --pull fast-forward's "
         "checkout (and any later worktree status or diff) would run a repository-chosen program "
         "before the post-pull doctor sees the result; filter drivers belong to the adopter's own "
         "global configuration (spec 5.6); remove it and re-run (fail-closed)"),
        (_LOCAL_CONTENT_DRIVER_KEYS,
         ", a repository-scope external diff, textconv or merge driver: git runs the program it "
         "names while it compares or merges working-tree content, and no step of the reference "
         "pull path is shown to reach it, so it is refused as fail-closed defence in depth; the "
         "cost is that an adopter with a repository-scope textconv (or external diff or merge "
         "driver) must move it to the adopter's own global configuration (spec 5.6); remove it "
         "and re-run (fail-closed)"),
        (_LOCAL_FORMAT_KEYS,
         ", a repository-format or clone-shape record (a reftable ref store, a partial or "
         "promisor clone) that sync does not allowlist: the class is not shown data-only here, "
         "and unlike every other refused key its remedy is NOT a move to global configuration "
         "(git reads it in the repository itself, so moving it would break the repository); "
         "recreate the store in a default-format, full clone before sync will run (an accepted "
         "cost; fail-closed)"))
    remote_name_why = (", a remote whose name is not a plain remote name (letters, digits, '-', "
                       "'_' and '.' only, never '.', '..' or a leading '-'): git resolves a fetch "
                       "or push argument that equals a configured remote's name to that remote's "
                       "configuration, so a remote named with a URL string could redirect the "
                       "transport past the spec 5.6 destination agreement; rename the remote and "
                       "re-run (fail-closed)")
    generic_why = (", a repository-scope key outside sync's closed allowlist of data-only keys "
                   "(only a key that can neither name a program nor redirect the transport is "
                   "allowlisted); the key belongs in the adopter's own global configuration "
                   "(spec 5.6); move it there and re-run (fail-closed)")
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
            if _local_key_allowlisted(low):
                continue
            why = generic_why
            sub = _remote_key_subsection(low)
            if sub is not None and not _remote_name_plain(sub.decode("utf-8", "replace")):
                why = remote_name_why
            else:
                for keys, family_why in refusals:
                    if _local_key_listed(low, keys):
                        why = family_why
                        break
            raise SyncError("the store repository's git configuration carries {!r}{}".format(
                kb.decode("utf-8", "replace"), why))


def _refuse_index_gitlinks(git, toplevel, why):
    """Refuse a gitlink (mode 160000) entry anywhere in the repository's INDEX, read bounded with
    `ls-files -s` from the toplevel so the scan covers the whole index even under a nested store. A
    staged-only embedded repository already carries its own live config (a scope the allowlist
    cannot enumerate), and the clean-check status probe descends into it, so the index is gated
    exactly as HEAD's tree is. An unreadable listing, or one that exceeds the read bound, is a
    cannot-evaluate, never read as no-gitlinks."""
    index = _opf_observe._run_git(git, toplevel, ["ls-files", "-s", "-z"],
                                  max_output_bytes=64 << 20)
    if not index.completed or index.rc != 0:
        raise SyncError("could not enumerate the index for gitlink (submodule) entries ({}); the "
                        "store cannot be shown submodule-free (fail-closed)".format(
                            index.err.strip() if not index.completed
                            else "rc {}: {}".format(index.rc, index.err.strip())))
    raw = index.out
    if raw and not raw.endswith(b"\x00"):
        raise SyncError("git ls-files returned a payload that is not NUL-terminated; the store "
                        "cannot be shown submodule-free (fail-closed)")
    for record in raw.split(b"\x00"):
        if record.startswith(b"160000 "):
            entry = record.partition(b"\t")[2].decode("utf-8", "replace")
            raise SyncError("the index carries a gitlink (submodule) entry at {!r}".format(entry)
                            + why)


def _refuse_submodules(git, store_root):
    """Refuse a store whose repository carries any git submodule machinery, before any fetch and
    before any local step that could reach a program: a .gitmodules file at the store root or at
    the repository toplevel (the only working-tree path git reads it from), a gitlink entry
    anywhere in the INDEX (_refuse_index_gitlinks) or anywhere in HEAD's WHOLE tree
    (`ls-tree -r --full-tree`, so a store nested below the toplevel cannot sit beside machinery
    outside its own subtree), or any entry under the repository's modules directory (.git/modules).
    A submodule's own repository config (under .git/modules/<name>/, or inside a staged embedded
    repository) is a configuration scope the repository-config allowlist cannot enumerate, and
    git's worktree operations (the clean-check status probe, a checkout) descend into a populated
    submodule and can run a program that config names (a clean, smudge or process filter the
    submodule's own .gitattributes assigns), so the class is refused rather than enumerated. An
    unreadable probe is a cannot-evaluate, never read as no-submodules; an index or tree listing
    that exceeds the read bound is likewise a cannot-evaluate (fail-closed, disclosed)."""
    why = (" (a submodule's own repository configuration is a scope the repository-config "
           "allowlist cannot enumerate, and a worktree step can run a program it names); sync "
           "refuses a store with any submodule machinery; remove the submodule and re-run "
           "(fail-closed)")
    if os.path.lexists(os.path.join(str(store_root), ".gitmodules")):
        raise SyncError("the store carries a .gitmodules file" + why)
    top = _read_git(git, store_root, ["rev-parse", "--show-toplevel"],
                    "locate the repository toplevel")
    toplevel = os.fsdecode(top.out.removesuffix(b"\n"))
    if os.path.lexists(os.path.join(toplevel, ".gitmodules")):
        raise SyncError("the repository carries a .gitmodules file at its toplevel" + why)
    _refuse_index_gitlinks(git, toplevel, why)
    tree = _opf_observe._run_git(git, store_root, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"],
                                 max_output_bytes=64 << 20)
    if not tree.completed or tree.rc != 0:
        raise SyncError("could not enumerate HEAD's tree for gitlink (submodule) entries ({}); the "
                        "store cannot be shown submodule-free (fail-closed)".format(
                            tree.err.strip() if not tree.completed
                            else "rc {}: {}".format(tree.rc, tree.err.strip())))
    raw = tree.out
    if raw and not raw.endswith(b"\x00"):
        raise SyncError("git ls-tree returned a payload that is not NUL-terminated; the store "
                        "cannot be shown submodule-free (fail-closed)")
    for record in raw.split(b"\x00"):
        if record.startswith(b"160000 "):
            entry = record.partition(b"\t")[2].decode("utf-8", "replace")
            raise SyncError("HEAD's tree carries a gitlink (submodule) entry at {!r}".format(entry)
                            + why)
    gd = _read_git(git, store_root, ["rev-parse", "--absolute-git-dir"],
                   "locate the repository's git directory")
    modules = os.path.join(os.fsdecode(gd.out.removesuffix(b"\n")), "modules")
    try:
        entries = os.listdir(modules)
    except FileNotFoundError:
        entries = []
    except OSError as exc:
        raise SyncError("the repository's modules directory (.git/modules) cannot be read ({}); "
                        "the store cannot be shown submodule-free (fail-closed)".format(exc))
    if entries:
        raise SyncError("the repository carries {} entr{} under its modules directory "
                        "(.git/modules)".format(len(entries), "y" if len(entries) == 1 else "ies")
                        + why)


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


def _refuse_effective_remote_shadow(git, store_root, env, url):
    """Refuse a configured remote OF ANY SCOPE whose name equals the verified URL string, read from
    the EFFECTIVE configuration under the exact environment and override pairs the transport call
    runs with (global, system, local, worktree, include, command). git resolves a fetch or push
    argument that equals a configured remote's name to that remote's configuration (an exact name
    lookup), so such a remote would redirect the fetch to its url values and the push to every
    pushurl, past the spec 5.6 destination agreement. The repository scopes are already held to
    plain remote names by the allowlist gate; this gate covers the scopes beyond them, where the
    adopter's own remotes keep working because only a name EQUAL to the verified URL string can
    shadow it, and no plain name can (every allowed target string contains ':' or '/'). The
    section and key of a listed name are folded by git while the subsection (the remote's name) is
    preserved byte for byte, which is exactly the byte-exact comparison the lookup uses. An
    enumeration that does not complete at git's documented match/no-match codes refuses."""
    cmd = [git, "--no-pager", "--no-replace-objects", "-C", str(store_root),
           "config", "-z", "--name-only", "--get-regexp", r"^remote\."]
    out = _opf_observe._capture_bounded(cmd, env, 30, 1 << 20)
    if not out.completed:
        raise SyncError("could not enumerate the remote.* configuration the transport would honour "
                        "({}); the verified URL cannot be confirmed as the transport URL "
                        "(fail-closed)".format(out.err.strip()))
    if out.rc not in (0, 1):
        raise SyncError("git config --get-regexp over the transport's remote configuration failed "
                        "(rc {}): {}; the verified URL cannot be confirmed as the transport URL "
                        "(fail-closed)".format(out.rc, out.err.strip()))
    raw = out.out
    if raw and not raw.endswith(b"\x00"):
        raise SyncError("git config returned a payload that is not NUL-terminated; the transport "
                        "configuration cannot be trusted (fail-closed)")
    url_b = os.fsencode(url)
    prefix = b"remote."
    for record in raw.split(b"\x00"):
        if not record:
            continue
        name = record.partition(b"\n")[0]
        if not name.startswith(prefix):
            continue
        last = name.rfind(b".")
        if last <= len(prefix):
            continue     # no subsection (remote.pushdefault and kin carry no remote name)
        if name[len(prefix):last] == url_b:
            raise SyncError("the git configuration the transport honours carries a remote named "
                            "exactly the verified URL ({!r}): git would resolve the fetch or push "
                            "argument to that remote's configuration instead of the verified "
                            "destination (spec 5.6); rename or remove that remote and re-run "
                            "(fail-closed)".format(name.decode("utf-8", "replace")))


def _run_transport(git, store_root, args, url, timeout=_TRANSPORT_TIMEOUT_S):
    """Run ONE fetch or push of the verified URL under the transport environment and overrides,
    bounded, after the repository-scope refusals, the effective-rewrite gate and the effective
    remote-name shadow gate over that URL.
    Every other git call this module makes goes through the read-only scrubbed boundary instead."""
    _refuse_local_redirect_config(git, store_root)
    overrides = _transport_overrides(git)
    env = _transport_env()
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (key, value) in enumerate(overrides):
        env["GIT_CONFIG_KEY_{}".format(i)] = key
        env["GIT_CONFIG_VALUE_{}".format(i)] = value
    _refuse_effective_url_rewrite(git, store_root, env, url)
    _refuse_effective_remote_shadow(git, store_root, env, url)
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
    _require_destination_agreement(sync_target, tgt, url, res.target, git, res.store_root)

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
    # Both repository gates run here, before the destination agreement (which itself holds the
    # recorded remote to a plain name and reads every url and pushurl value), the fetch and every
    # local step that could reach a repository-chosen program; _run_transport re-runs the allowlist
    # gate and the effective rewrite and remote-shadow gates before
    # each fetch and push. The submodule gate runs first, so a populated submodule is named as the
    # reason even where its submodule.* bookkeeping keys would refuse on the allowlist too.
    _refuse_submodules(git, res.store_root)
    _refuse_local_redirect_config(git, res.store_root)
    _require_destination_agreement(sync_target, tgt, url, res.target, git, res.store_root)
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
    destination agreement, the held-lease refusal (never seized), the REMOTE-NAME and
    EVERY-URL-VALUE gates (a remote named with the target's URL string over a file://, https or
    ssh-shaped target, a second pushurl, several remotes, and a global-scope remote named the
    verified URL, each refused before any transport with FETCH_HEAD shown absent, and the fetch and
    push redirects shown live once those gates are dropped), the repository-config ALLOWLIST
    gate (an ordinary store carrying every allowlisted key still syncs; every documented family key
    and every unlisted key, core.sshCommand and core.hooksPath included, refuses before any fetch
    with FETCH_HEAD shown absent; dropping the allowlist is shown running a repository-chosen
    program), the format-record refusals (a promisor or partial-clone key, and a real reftable
    repository where this git can create one, each refused naming the recreate-the-store remedy),
    the SUBMODULE refusals (a .gitmodules file at the store root or the repository toplevel, a
    gitlink in HEAD's full tree or in the INDEX (a staged embedded repository whose own config
    names a filter program, the program never running, shown running once the index scan is
    dropped), a nested store beside toplevel machinery, and a
    .git/modules entry each alone, plus a populated submodule whose own config names a filter
    program, refused with the program never running and shown running once the gates are dropped),
    the transport neutralization kept as defence in depth behind the gate, the effective
    URL-rewrite refusal over every config scope, the core.alternateRefsCommand refusal over a store
    with alternates (the fetch shown running the program once the allowlist is dropped), the
    gc.recentObjectsHook refusal, the repository-scope working-tree driver refusals
    (filter clean/smudge/process and the diff/textconv/merge drivers, with the fast-forward checkout
    shown running the smudge program once the allowlist is dropped), the pre-git target validation,
    and the fetch/detached/unborn refusals. The
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
          "classification, pull, push, destination agreement, remote-name and pushurl-value gates, "
          "lease, repository-config allowlist, format-record refusals, "
          "submodule and index-gitlink refusals, transport neutralization, URL-rewrite and "
          "remote-shadow refusals, working-tree driver "
          "refusals, target validation, fetch and HEAD refusals, dropped-guard flips)".format(
              checked[0]))
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

    def _no_fetch_head(root):
        # A refusal that fires before any fetch leaves no FETCH_HEAD behind.
        return not os.path.exists(os.path.join(root, ".git", "FETCH_HEAD"))

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

            # -- a remote named with the target's URL string cannot redirect the transport --------
            # git resolves a fetch or push argument that equals a configured remote's name to that
            # remote's configuration, so a remote named exactly the verified URL string would fetch
            # from its url values and push to EVERY pushurl. The plain-remote-name rule and the
            # every-value destination agreement refuse before any transport; the flips below put
            # back a first-push-value-only agreement and show both redirects live.
            def first_push_value_only(g, r, u):
                value = _opf_observe._observe_remote(g, r, [])
                return "origin", [value], [value]

            rd_bare = os.path.join(base, "shadow-target.git")
            _git(base, "init", "--bare", rd_bare)
            rd_url = "file://" + rd_bare
            rd_evil = os.path.join(base, "shadow-evil.git")
            _git(base, "init", "--bare", rd_evil)
            rd_root = _store("shadow", "git:" + rd_url)
            _git(rd_root, "push", rd_bare, "main")
            _git(rd_root, "push", rd_evil, "main")
            # the unverified repository advances past the store, so a redirected fetch is visible
            rdw = os.path.join(base, "shadow-writer")
            _git(base, "clone", rd_evil, rdw)
            (Path(rdw) / "data.txt").write_text("unverified\n", encoding="utf-8")
            _git(rdw, "add", "-A")
            _git(rdw, "commit", "-m", "advance the unverified repository")
            _git(rdw, "push", "origin", "main")
            rd_evil_tip = _tip(rd_evil)
            # the single remote is NAMED the verified URL string; its url points elsewhere while
            # its first push URL agrees (the exact shape a first-value-only agreement passes)
            _git(rd_root, "config", "remote.{}.url".format(rd_url), rd_evil)
            _git(rd_root, "config", "remote.{}.pushurl".format(rd_url), rd_url)
            rd_head = _head(rd_root)
            rc, out = run(["--root", rd_root])
            check("a remote named the verified URL string refuses before any transport",
                  rc == EXIT_MALFORMED and "plain remote name" in out and _no_fetch_head(rd_root)
                  and _head(rd_root) == rd_head and _tip(rd_evil) == rd_evil_tip)
            # the named flip: with the plain-name rule, the every-value agreement and the effective
            # shadow gate all dropped, the fetch argument resolves as the remote's name and the
            # fetch lands the UNVERIFIED repository's tip in FETCH_HEAD
            with patch.object(module, "_remote_name_plain", lambda name: True), \
                    patch.object(module, "_remote_url_values", first_push_value_only), \
                    patch.object(module, "_refuse_effective_remote_shadow", lambda *a, **k: None):
                rc, out = run(["--root", rd_root])
            check("dropping the remote gates redirects the fetch to the unverified repository "
                  "(shown red)",
                  rc == EXIT_MALFORMED and "BEHIND" in out
                  and _git(rd_root, "rev-parse", "FETCH_HEAD").strip() == rd_evil_tip
                  and _head(rd_root) == rd_head)

            # a second pushurl on a URL-named remote: the push would land at BOTH repositories
            p2_bare = os.path.join(base, "pushshadow-target.git")
            _git(base, "init", "--bare", p2_bare)
            p2_url = "file://" + p2_bare
            p2_evil = os.path.join(base, "pushshadow-evil.git")
            _git(base, "init", "--bare", p2_evil)
            p2_root = _store("pushshadow", "git:" + p2_url)
            _git(p2_root, "push", p2_bare, "main")
            _git(p2_root, "push", p2_evil, "main")   # shared history: a redirected push would land
            _git(p2_root, "config", "remote.{}.url".format(p2_url), p2_url)
            _git(p2_root, "config", "remote.{}.pushurl".format(p2_url), p2_url)
            _git(p2_root, "config", "--add", "remote.{}.pushurl".format(p2_url), p2_evil)
            _local_commit(p2_root, "data.txt", "pending\n")
            p2_tip = _tip(p2_bare)
            p2_evil_tip = _tip(p2_evil)
            rc, out = run(["--push", "--root", p2_root])
            check("a second pushurl behind a URL-named remote refuses the push before any "
                  "transport",
                  rc == EXIT_MALFORMED and "plain remote name" in out and _no_fetch_head(p2_root)
                  and _tip(p2_bare) == p2_tip and _tip(p2_evil) == p2_evil_tip)
            with patch.object(module, "_remote_name_plain", lambda name: True), \
                    patch.object(module, "_remote_url_values", first_push_value_only), \
                    patch.object(module, "_refuse_effective_remote_shadow", lambda *a, **k: None):
                rc, _out = run(["--push", "--root", p2_root])
            check("dropping the remote gates pushes HEAD to the verified target AND the second "
                  "pushurl (shown red)",
                  rc == EXIT_OK and _tip(p2_bare) == _head(p2_root)
                  and _tip(p2_evil) == _head(p2_root))

            # every pushurl value must agree even on a plainly named remote: the agreement reads
            # `git remote get-url --all` and `get-url --push --all`, never only the first value
            pv_root, pv_bare = _paired("pushvalues")
            pv_evil = os.path.join(base, "pushvalues-evil.git")
            _git(base, "init", "--bare", pv_evil)
            _git(pv_root, "config", "remote.origin.pushurl", pv_bare)
            _git(pv_root, "config", "--add", "remote.origin.pushurl", pv_evil)
            _local_commit(pv_root, "data.txt", "pending two\n")
            pv_tip = _tip(pv_bare)
            rc, out = run(["--push", "--root", pv_root])
            check("a second disagreeing pushurl value refuses before any transport",
                  rc == EXIT_MALFORMED and "does not agree" in out and "pushurl" in out
                  and _no_fetch_head(pv_root) and _tip(pv_bare) == pv_tip
                  and _tip_or_none(pv_evil) is None)

            # an https-shaped target: the remote named that URL would redirect the transport to a
            # LOCAL repository without any network, refused on the name before any transport
            hs_root = _store("httpsshadow", "git:https://example.invalid/repo.git")
            hs_evil = os.path.join(base, "httpsshadow-evil.git")
            _git(base, "init", "--bare", hs_evil)
            _git(hs_root, "push", hs_evil, "main")
            hsw = os.path.join(base, "httpsshadow-writer")
            _git(base, "clone", hs_evil, hsw)
            (Path(hsw) / "data.txt").write_text("unverified https\n", encoding="utf-8")
            _git(hsw, "add", "-A")
            _git(hsw, "commit", "-m", "advance")
            _git(hsw, "push", "origin", "main")
            hs_evil_tip = _tip(hs_evil)
            _git(hs_root, "config", "remote.https://example.invalid/repo.git.url", hs_evil)
            _git(hs_root, "config", "remote.https://example.invalid/repo.git.pushurl",
                 "https://example.invalid/repo.git")
            rc, out = run(["--root", hs_root])
            check("an https-shaped target whose remote is named that URL refuses before any "
                  "transport",
                  rc == EXIT_MALFORMED and "plain remote name" in out and _no_fetch_head(hs_root))
            with patch.object(module, "_remote_name_plain", lambda name: True), \
                    patch.object(module, "_remote_url_values", first_push_value_only), \
                    patch.object(module, "_refuse_effective_remote_shadow", lambda *a, **k: None):
                rc, out = run(["--root", hs_root])
            check("dropping the remote gates redirects the https-shaped fetch to a local "
                  "repository (shown red)",
                  rc == EXIT_MALFORMED and "BEHIND" in out
                  and _git(hs_root, "rev-parse", "FETCH_HEAD").strip() == hs_evil_tip)

            # an ssh-shaped (scp-style) target: the same name rule refuses it
            ss_root = _store("sshshadow", "git:git@example.invalid:repo.git")
            ss_evil = os.path.join(base, "sshshadow-evil.git")
            _git(base, "init", "--bare", ss_evil)
            _git(ss_root, "config", "remote.git@example.invalid:repo.git.url", ss_evil)
            _git(ss_root, "config", "remote.git@example.invalid:repo.git.pushurl",
                 "git@example.invalid:repo.git")
            rc, out = run(["--root", ss_root])
            check("an ssh-shaped target whose remote is named that URL refuses before any "
                  "transport",
                  rc == EXIT_MALFORMED and "plain remote name" in out and _no_fetch_head(ss_root))

            # several remotes refuse: which of several is authoritative is not git's to guess
            tr_root, tr_bare = _paired("tworemotes")
            _git(tr_root, "remote", "add", "second", tr_bare)
            rc, out = run(["--root", tr_root])
            check("several remotes refuse before any transport",
                  rc == EXIT_MALFORMED and "remotes" in out and _no_fetch_head(tr_root))

            # a remote of ANY scope named the verified URL redirects; the effective gate, read
            # under the exact transport environment, refuses a global one the repository gates
            # cannot see
            es_bare = os.path.join(base, "effshadow-target.git")
            _git(base, "init", "--bare", es_bare)
            es_url = "file://" + es_bare
            es_evil = os.path.join(base, "effshadow-evil.git")
            _git(base, "init", "--bare", es_evil)
            es_root = _store("effshadow", "git:" + es_url)
            _git(es_root, "remote", "add", "origin", es_url)
            _git(es_root, "push", es_bare, "main")
            _git(es_root, "push", es_evil, "main")
            esw = os.path.join(base, "effshadow-writer")
            _git(base, "clone", es_evil, esw)
            (Path(esw) / "data.txt").write_text("unverified global\n", encoding="utf-8")
            _git(esw, "add", "-A")
            _git(esw, "commit", "-m", "advance")
            _git(esw, "push", "origin", "main")
            es_evil_tip = _tip(es_evil)
            es_head = _head(es_root)
            gcfg = Path(home) / ".gitconfig"
            gcfg.write_text('[remote "{}"]\n\turl = {}\n'.format(es_url, es_evil),
                            encoding="utf-8")
            rc, out = run(["--root", es_root])
            check("a global-scope remote named the verified URL refuses before any transport",
                  rc == EXIT_MALFORMED and "named exactly the verified URL" in out
                  and _no_fetch_head(es_root) and _head(es_root) == es_head)
            # the named flip: with the effective shadow gate dropped the fetch resolves the URL
            # argument as the GLOBAL remote's name and lands the unverified tip in FETCH_HEAD
            with patch.object(module, "_refuse_effective_remote_shadow", lambda *a, **k: None):
                rc, out = run(["--root", es_root])
            check("dropping the effective remote-shadow gate redirects the fetch through the "
                  "global remote (shown red)",
                  rc == EXIT_MALFORMED and "BEHIND" in out
                  and _git(es_root, "rev-parse", "FETCH_HEAD").strip() == es_evil_tip
                  and _head(es_root) == es_head)
            gcfg.unlink()

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

            # -- hostile repository configuration refuses on the allowlist gate ------------------
            # core.hooksPath, core.fsmonitor and core.sshCommand sit at repository scope on no
            # named list, so each refuses through the allowlist alone, before any fetch
            # (FETCH_HEAD never appears); the transport overrides that also neutralize them stay
            # as defence in depth, shown below with the allowlist dropped.
            x_root, x_bare = _paired("hostile")
            hooks = os.path.join(base, "hostile-hooks")
            os.makedirs(hooks)
            sentinel = os.path.join(base, "hostile-sentinel")
            hook = Path(hooks) / "pre-push"
            hook.write_text("#!/bin/sh\n: > {}\nexit 0\n".format(sentinel), encoding="utf-8")
            os.chmod(str(hook), 0o755)
            _local_commit(x_root, "data.txt", "seven\n")
            x_tip = _tip(x_bare)
            for hx_key, hx_value in (("core.hooksPath", hooks),
                                     ("core.fsmonitor", os.path.join(hooks, "fsmon")),
                                     ("core.sshCommand", os.path.join(hooks, "ssh"))):
                _git(x_root, "config", "--local", hx_key, hx_value)
                rc, out = run(["--push", "--root", x_root])
                _git(x_root, "config", "--local", "--unset", hx_key)
                check("a repository-scope {} is outside the allowlist and refuses before any "
                      "fetch".format(hx_key),
                      rc == EXIT_MALFORMED and "allowlist" in out and hx_key.lower() in out
                      and _no_fetch_head(x_root) and _tip(x_bare) == x_tip
                      and not os.path.exists(sentinel))
            # defence in depth behind the gate: with the allowlist dropped, the transport
            # overrides still neutralize all three keys (the push completes, no hook runs)
            _git(x_root, "config", "core.hooksPath", hooks)
            _git(x_root, "config", "core.fsmonitor", os.path.join(hooks, "fsmon"))
            _git(x_root, "config", "core.sshCommand", os.path.join(hooks, "ssh"))
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, _out = run(["--push", "--root", x_root])
            check("with the allowlist dropped, the hostile-config push completes with the exec "
                  "surfaces neutralized",
                  rc == EXIT_OK and _tip(x_bare) == _head(x_root))
            check("the repository pre-push hook never ran (no sentinel)",
                  not os.path.exists(sentinel))
            # the named flip: without the allowlist AND the transport neutralization the
            # repository hook runs
            _local_commit(x_root, "data.txt", "eight\n")
            with patch.object(module, "_local_key_allowlisted", lambda low: True), \
                    patch.object(module, "_transport_overrides", lambda g: []):
                run(["--push", "--root", x_root])
            check("dropping the transport neutralization too runs the repository hook (shown red)",
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
                    and _tip(ins_bare) == ins_tip and _tip_or_none(ins_other) == ins_tip
                    and _no_fetch_head(ins_root))
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
                  and _head(inc_root) == inc_head and _no_fetch_head(inc_root))
            ap_root, _ap_bare = _paired("askpass")
            ap_inc = Path(base) / "include-askpass"
            ap_inc.write_text("[core]\n\taskpass = {}\n".format(planted_helper), encoding="utf-8")
            _git(ap_root, "config", "--local", "include.path", str(ap_inc))
            rc, out = run(["--root", ap_root])
            check("a repository-local include carrying core.askpass refuses before any transport",
                  rc == EXIT_MALFORMED and "include" in out and not os.path.exists(inc_sentinel)
                  and _no_fetch_head(ap_root))
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
                  rc == EXIT_MALFORMED and "include" in out and _no_fetch_head(ii_root))
            sv_root, _sv_bare = _paired("trust")
            _git(sv_root, "config", "--local", "http.sslVerify", "false")
            rc, out = run(["--root", sv_root])
            check("a repository-scope https trust override refuses before any transport",
                  rc == EXIT_MALFORMED and "trust" in out and _no_fetch_head(sv_root))
            # a repository-scope cookie path refuses, both keys in both forms (each set alone on an
            # equal store, so the refusal is the only thing between it and exit 0); a name just past
            # a listed key still refuses, through the allowlist
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
                      and not os.path.exists(ck_jar) and _no_fetch_head(ck_root))
            for ck_key in ("http.cookieFileX", "http.xcookieFile"):
                _git(ck_root, "config", "--local", ck_key, ck_jar)
                rc, out = run(["--root", ck_root])
                _git(ck_root, "config", "--local", "--unset", ck_key)
                check("{}, a name just past a listed key, still refuses through the "
                      "allowlist".format(ck_key),
                      rc == EXIT_MALFORMED and "allowlist" in out and _no_fetch_head(ck_root))
            # the named flip: with the allowlist dropped the repository cookie path passes, so the
            # allowlist itself is the gate
            _git(ck_root, "config", "--local", "http.cookieFile", ck_jar)
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, _out = run(["--root", ck_root])
            _git(ck_root, "config", "--local", "--unset", "http.cookieFile")
            check("dropping the allowlist lets the repository cookie path through (shown red)",
                  rc == EXIT_OK)
            # a repository-scope bundle URI refuses, plain and under a subsection (each set alone on
            # an equal store, naming a real bundle of another history); a name just past the key
            # matches nothing and the run reports EQUAL
            bu_root, _bu_bare = _paired("bundle")
            bu_head = _head(bu_root)
            bu_src = _store("bundle-source", "")
            bu_file = os.path.join(base, "planted.bundle")
            _git(bu_src, "bundle", "create", bu_file, "main")

            def _bundle_refs():
                return _git(bu_root, "for-each-ref", "refs/bundles/").strip()

            for bu_key in ("fetch.bundleURI", "fetch.https://example.invalid/.bundleURI"):
                _git(bu_root, "config", "--local", bu_key, bu_file)
                rc, out = run(["--root", bu_root])
                _git(bu_root, "config", "--local", "--unset", bu_key)
                check("a repository-scope {} refuses before any transport".format(bu_key),
                      rc == EXIT_MALFORMED and "bundle" in out and _head(bu_root) == bu_head
                      and not _bundle_refs() and _no_fetch_head(bu_root))
            for bu_key in ("fetch.bundleURIx", "fetch.xbundleURI"):
                _git(bu_root, "config", "--local", bu_key, bu_file)
                rc, out = run(["--root", bu_root])
                _git(bu_root, "config", "--local", "--unset", bu_key)
                check("{}, a name just past a listed key, still refuses through the "
                      "allowlist".format(bu_key),
                      rc == EXIT_MALFORMED and "allowlist" in out and not _bundle_refs()
                      and _no_fetch_head(bu_root))
            # the named flip: with the allowlist dropped the fetch downloads the planted bundle
            # and writes its refs/bundles/* refs, so the allowlist is what stops it
            _git(bu_root, "config", "--local", "fetch.bundleURI", bu_file)
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, _out = run(["--root", bu_root])
            _git(bu_root, "config", "--local", "--unset", "fetch.bundleURI")
            check("dropping the allowlist lets the repository bundle source write refs/bundles/* "
                  "(shown red)",
                  rc == EXIT_OK and _bundle_refs() != "")
            # a repository-scope push-signing key refuses, each listed key plain and under a
            # subsection (each set alone on an ahead store, pushed, so the refusal is the only
            # thing between it and the transport): the repository-chosen signing program never
            # runs (no marker) and the target never moves; a name just past a listed key matches
            # nothing and the run proceeds to the ordinary report
            sg_root, sg_bare = _paired("signing")
            sg_marker = os.path.join(base, "signing-marker")
            sg_prog = Path(base) / "signing-gpg"
            sg_prog.write_text("#!/bin/sh\n: > {}\nexit 1\n".format(sg_marker), encoding="utf-8")
            os.chmod(str(sg_prog), 0o755)
            _git(sg_bare, "config", "receive.certNonceSeed", "opf-selftest-nonce")
            _local_commit(sg_root, "data.txt", "ten\n")
            sg_tip = _tip(sg_bare)
            for sg_key, sg_value in (("push.gpgSign", "if-asked"),
                                     ("gpg.program", str(sg_prog)),
                                     ("gpg.openpgp.program", str(sg_prog)),
                                     ("gpg.x509.program", str(sg_prog)),
                                     ("gpg.ssh.program", str(sg_prog)),
                                     ("gpg.ssh.defaultKeyCommand", str(sg_prog))):
                _git(sg_root, "config", "--local", sg_key, sg_value)
                rc, out = run(["--push", "--root", sg_root])
                _git(sg_root, "config", "--local", "--unset", sg_key)
                check("a repository-scope {} refuses before any transport".format(sg_key),
                      rc == EXIT_MALFORMED and "signing" in out
                      and not os.path.exists(sg_marker) and _tip(sg_bare) == sg_tip
                      and _no_fetch_head(sg_root))
            for sg_key in ("push.gpgSignx", "gpg.xprogram"):
                _git(sg_root, "config", "--local", sg_key, "if-asked")
                rc, out = run(["--root", sg_root])
                _git(sg_root, "config", "--local", "--unset", sg_key)
                check("{}, a name just past a listed key, still refuses through the "
                      "allowlist".format(sg_key),
                      rc == EXIT_MALFORMED and "allowlist" in out and "signing" not in out
                      and _no_fetch_head(sg_root))
            # the named flip: with the allowlist dropped, the push signs its certificate against
            # the target's advertised nonce and the repository-chosen signing program RUNS (the
            # marker appears) before the push fails at signing, so the allowlist is what stops it
            # (the signing identity comes from the fixture's own global config)
            (Path(home) / ".gitconfig").write_text(
                "[user]\n\tname = OPF Self Test\n\temail = opf@example.invalid\n", encoding="utf-8")
            _git(sg_root, "config", "--local", "push.gpgSign", "if-asked")
            _git(sg_root, "config", "--local", "gpg.program", str(sg_prog))
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, _out = run(["--push", "--root", sg_root])
            _git(sg_root, "config", "--local", "--unset", "push.gpgSign")
            _git(sg_root, "config", "--local", "--unset", "gpg.program")
            (Path(home) / ".gitconfig").unlink()
            check("dropping the allowlist runs the repository-chosen signing program (shown red)",
                  rc == EXIT_MALFORMED and os.path.exists(sg_marker) and _tip(sg_bare) == sg_tip)
            # a repository-scope protocol allow refuses, plain and per-protocol (each set alone on
            # an equal store); protocol.version, a shaping key on no named list, refuses through
            # the allowlist (the accepted cost: it belongs in global configuration), and so does a
            # name just past the listed key
            pw_root, _pw_bare = _paired("protowiden")
            for pw_key in ("protocol.allow", "protocol.ext.allow"):
                _git(pw_root, "config", "--local", pw_key, "always")
                rc, out = run(["--root", pw_root])
                _git(pw_root, "config", "--local", "--unset", pw_key)
                check("a repository-scope {} refuses before any transport".format(pw_key),
                      rc == EXIT_MALFORMED and "re-widen" in out and _no_fetch_head(pw_root))
            for pw_key in ("protocol.version", "protocol.allowx"):
                _git(pw_root, "config", "--local", pw_key, "2")
                rc, out = run(["--root", pw_root])
                _git(pw_root, "config", "--local", "--unset", pw_key)
                check("{}, outside the allowlist and on no named list, refuses through the "
                      "allowlist".format(pw_key),
                      rc == EXIT_MALFORMED and "allowlist" in out and _no_fetch_head(pw_root))
            _git(pw_root, "config", "--local", "protocol.ext.allow", "always")
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, _out = run(["--root", pw_root])
            _git(pw_root, "config", "--local", "--unset", "protocol.ext.allow")
            check("dropping the allowlist lets the repository protocol allow through (shown red)",
                  rc == EXIT_OK)
            # a repository-scope remote program name refuses, plain and under a subsection (each
            # set alone on an equal store, whose remote.origin.url and remote.origin.fetch sit at
            # repository scope throughout, so the ALLOWLISTED remote keys are shown passing live
            # on every green run of this suite); a name just past a listed key still refuses,
            # through the allowlist
            rp_root, _rp_bare = _paired("remoteprog")
            for rp_key in ("remote.receivepack", "remote.origin.receivepack",
                           "remote.origin.uploadpack", "remote.origin.vcs"):
                _git(rp_root, "config", "--local", rp_key, "git-upload-pack")
                rc, out = run(["--root", rp_root])
                _git(rp_root, "config", "--local", "--unset", rp_key)
                check("a repository-scope {} refuses before any transport".format(rp_key),
                      rc == EXIT_MALFORMED and "pack program" in out and _no_fetch_head(rp_root))
            for rp_key in ("remote.origin.receivepackx", "remote.origin.xvcs"):
                _git(rp_root, "config", "--local", rp_key, "x")
                rc, out = run(["--root", rp_root])
                _git(rp_root, "config", "--local", "--unset", rp_key)
                check("{}, a name just past a listed key, still refuses through the "
                      "allowlist".format(rp_key),
                      rc == EXIT_MALFORMED and "allowlist" in out and _no_fetch_head(rp_root))
            _git(rp_root, "config", "--local", "remote.origin.vcs", "hg")
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, _out = run(["--root", rp_root])
            _git(rp_root, "config", "--local", "--unset", "remote.origin.vcs")
            check("dropping the allowlist lets the repository remote program through "
                  "(shown red)", rc == EXIT_OK)
            # a repository-scope core.alternateRefsCommand refuses before any fetch: with
            # .git/objects/info/alternates present (a store made with clone --reference or
            # --shared carries that file legitimately), the fetch itself runs the configured
            # program with each alternate's path while it advertises the alternates' tips, so the
            # key is a program launch inside the hardened transport, in the read-only report and
            # on --pull alike; the program's marker never appears and nothing moves
            ar_root, ar_bare = _paired("altrefs")
            _writer_commit(ar_bare, "altrefs")
            ar_marker = os.path.join(base, "altrefs-marker")
            ar_prog = Path(base) / "altrefs-prog"
            ar_prog.write_text("#!/bin/sh\n: > {}\nexit 0\n".format(ar_marker), encoding="utf-8")
            os.chmod(str(ar_prog), 0o755)
            ar_alt = os.path.join(base, "altrefs-alt.git")
            _git(base, "init", "--bare", ar_alt)
            ar_alt_file = Path(ar_root) / ".git" / "objects" / "info" / "alternates"
            ar_alt_file.parent.mkdir(parents=True, exist_ok=True)
            ar_alt_file.write_text(os.path.join(ar_alt, "objects") + "\n", encoding="utf-8")
            _git(ar_root, "config", "--local", "core.alternateRefsCommand", str(ar_prog))
            ar_head = _head(ar_root)
            for argv in ([], ["--pull"]):
                rc, out = run(argv + ["--root", ar_root])
                check("a repository-scope core.alternateRefsCommand with alternates present "
                      "refuses before any fetch ({})".format(" ".join(argv) or "bare"),
                      rc == EXIT_MALFORMED and "program launch" in out
                      and not os.path.exists(ar_marker) and _head(ar_root) == ar_head
                      and not _status(ar_root) and _no_fetch_head(ar_root))
            # the named flip the allowlist redesign is judged on: with the allowlist dropped, the
            # fetch itself runs the repository-chosen program (the marker appears) while the
            # report stays read-only, so the allowlist is what keeps a store-chosen program from
            # running, and the fetch is the transport step it protects
            with patch.object(module, "_local_key_allowlisted", lambda low: True):
                rc, out = run(["--root", ar_root])
            check("dropping the allowlist lets the fetch run the alternate-refs program "
                  "(shown red)",
                  rc == EXIT_MALFORMED and "BEHIND" in out and os.path.exists(ar_marker)
                  and _head(ar_root) == ar_head)
            # a repository-scope gc.recentObjectsHook refuses before any fetch, in the read-only
            # report and on --pull alike: the program the auto-maintenance a fetch can spawn
            # would run never runs, and FETCH_HEAD never appears
            gh_root, _gh_bare = _paired("gchook")
            gh_marker = os.path.join(base, "gchook-marker")
            gh_prog = Path(base) / "gchook-prog"
            gh_prog.write_text("#!/bin/sh\n: > {}\nexit 0\n".format(gh_marker), encoding="utf-8")
            os.chmod(str(gh_prog), 0o755)
            _git(gh_root, "config", "--local", "gc.recentObjectsHook", str(gh_prog))
            for argv in ([], ["--pull"]):
                rc, out = run(argv + ["--root", gh_root])
                check("a repository-scope gc.recentObjectsHook refuses before any fetch "
                      "({})".format(" ".join(argv) or "bare"),
                      rc == EXIT_MALFORMED and "program launch" in out
                      and not os.path.exists(gh_marker) and _no_fetch_head(gh_root))
            # -- a format or clone-shape record refuses with ITS OWN remedy: such a key cannot
            # move to global configuration (git reads it in the repository itself), so the refusal
            # names recreating the store in a default-format, full clone instead
            fm_root, _fm_bare = _paired("formatrec")
            for fm_key, fm_value in (("remote.origin.promisor", "true"),
                                     ("remote.origin.partialCloneFilter", "blob:none"),
                                     ("extensions.partialClone", "origin")):
                _git(fm_root, "config", "--local", fm_key, fm_value)
                rc, out = run(["--root", fm_root])
                _git(fm_root, "config", "--local", "--unset", fm_key)
                check("a repository-scope {} refuses as a format record, never with the "
                      "move-to-global remedy".format(fm_key),
                      rc == EXIT_MALFORMED and "default-format, full clone" in out
                      and "move it there" not in out and _no_fetch_head(fm_root))
            # a real reftable repository (where this git can create one) refuses the same way:
            # `git init --ref-format=reftable` itself writes extensions.refStorage
            rt_root = os.path.join(base, "reftable")
            os.makedirs(rt_root)
            rt_manifest = _opf_init._manifest_model()
            rt_manifest["store"]["sync_target"] = "git:" + os.path.join(base, "reftable-absent.git")
            _write_manifest(rt_root, rt_manifest)
            (Path(rt_root) / "data.txt").write_text("one\n", encoding="utf-8")
            rt_init = _git_run(rt_root, "init", "--ref-format=reftable")
            if rt_init.returncode == 0:
                _git(rt_root, "add", "-A")
                _git(rt_root, "commit", "-m", "seed reftable")
                rc, out = run(["--root", rt_root])
                check("a reftable repository refuses as a format record before any fetch",
                      rc == EXIT_MALFORMED and "default-format, full clone" in out
                      and _no_fetch_head(rt_root))
            else:
                check("this git cannot create a reftable repository; the extensions.refStorage "
                      "refusal rides the key sweep below", True)
            # -- every allowlisted repository-scope key still syncs (the gate's other edge) -------
            # One concrete instance per allowlist entry, all set together at --local scope on an
            # equal store; the coverage check pins the instance map to the allowlist, so a key
            # added to the allowlist without a passing vector fails here.
            al_root, al_bare = _paired("allowlisted")
            # repositoryformatversion 1, set first: objectFormat is a v1-only extension, and git
            # refuses to read a repository that declares it at version 0
            al_instances = {
                b"core.repositoryformatversion": ("core.repositoryformatversion", "1"),
                b"core.filemode": ("core.filemode", "true"),
                b"core.bare": ("core.bare", "false"),
                b"core.logallrefupdates": ("core.logallrefupdates", "true"),
                b"core.ignorecase": ("core.ignorecase", "false"),
                b"core.precomposeunicode": ("core.precomposeunicode", "false"),
                b"core.symlinks": ("core.symlinks", "true"),
                b"user.name": ("user.name", "OPF Self Test"),
                b"user.email": ("user.email", "opf@example.invalid"),
                b"extensions.worktreeconfig": ("extensions.worktreeConfig", "true"),
                b"extensions.objectformat": ("extensions.objectFormat", "sha1"),
                b"remote.url": ("remote.origin.url", al_bare),
                b"remote.fetch": ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
                b"remote.pushurl": ("remote.origin.pushurl", al_bare),
                b"branch.remote": ("branch.main.remote", "origin"),
                b"branch.merge": ("branch.main.merge", "refs/heads/main"),
            }
            check("the allowlist pass vector instantiates every allowlisted key (none untested)",
                  set(al_instances) == set(_ALLOWED_EXACT_KEYS) | set(_ALLOWED_SUBSECTION_KEYS))
            for al_name, al_value in al_instances.values():
                _git(al_root, "config", "--local", al_name, al_value)
            # the worktreeConfig extension is on, so give the --worktree scope its own file with
            # an allowlisted key in it (with the extension on and the file missing, git cannot
            # read the scope and the gate refuses as an unreadable scope, fail-closed)
            _git(al_root, "config", "--worktree", "user.name", "OPF Self Test")
            rc, out = run(["--root", al_root])
            check("an ordinary store carrying every allowlisted key still syncs (EQUAL)",
                  rc == EXIT_OK and "EQUAL" in out)
            # an unlisted key set from the --worktree scope refuses through the same gate
            _git(al_root, "config", "--worktree", "core.sshCommand", "/bin/false")
            rc, out = run(["--root", al_root])
            _git(al_root, "config", "--worktree", "--unset", "core.sshCommand")
            check("an unlisted key in the --worktree scope refuses through the allowlist",
                  rc == EXIT_MALFORMED and "allowlist" in out)
            # -- a repository-scope working-tree driver program is refused before any fetch or
            # checkout: a filter.<name>.clean / smudge / process driver runs on the --pull
            # fast-forward's CHECKOUT (the first local step that writes the worktree), and a
            # diff.<name>.command / textconv or merge.<name>.driver names the external diff, textconv
            # or merge program git would run the same way while materializing or comparing worktree
            # content. Each key is set alone at repository scope on a BEHIND store whose committed
            # .gitattributes assigns the driver to data.txt; the pull refuses, the repository-chosen
            # program never runs (its marker is never written) and HEAD and the worktree do not move.
            def _driver_paired(name):
                root, bare = _paired(name)
                (Path(root) / ".gitattributes").write_text(
                    "* filter=pwn diff=pwn merge=pwn\n", encoding="utf-8")
                _git(root, "add", "-A")
                _git(root, "commit", "-m", "assign working-tree drivers")
                _git(root, "push", "origin", "main")
                _writer_commit(bare, name)
                return root, bare

            drv_marker = os.path.join(base, "driver-marker")
            drv_prog = Path(base) / "driver-prog"
            drv_prog.write_text("#!/bin/sh\n: > {}\ncat\n".format(drv_marker), encoding="utf-8")
            os.chmod(str(drv_prog), 0o755)
            for dv_key in ("filter.pwn.clean", "filter.pwn.smudge", "filter.pwn.process",
                           "diff.pwn.command", "diff.pwn.textconv", "merge.pwn.driver"):
                dv_root, _dv_bare = _driver_paired("driver-{}".format(dv_key.replace(".", "-")))
                _git(dv_root, "config", "--local", dv_key, str(drv_prog))
                dv_head = _head(dv_root)
                if os.path.exists(drv_marker):
                    os.unlink(drv_marker)
                rc, out = run(["--pull", "--root", dv_root])
                check("a repository-scope {} refuses the pull before any fetch or checkout".format(
                    dv_key),
                    rc == EXIT_MALFORMED and "driver" in out and not os.path.exists(drv_marker)
                    and _head(dv_root) == dv_head and not _status(dv_root)
                    and _no_fetch_head(dv_root))
            for dv_key in ("filter.pwn.cleanx", "diff.pwn.commandx", "merge.pwn.driverx"):
                dv_root, _dv_bare = _driver_paired("driver-miss-{}".format(dv_key.replace(".", "-")))
                _git(dv_root, "config", "--local", dv_key, str(drv_prog))
                rc, out = run(["--pull", "--root", dv_root])
                check("{}, a name just past a listed key, refuses through the allowlist, not as a "
                      "driver".format(dv_key),
                      rc == EXIT_MALFORMED and "allowlist" in out
                      and "working-tree content" not in out and _no_fetch_head(dv_root))
            # the named flip: with the allowlist dropped the fast-forward's CHECKOUT runs the
            # repository-chosen smudge program (the marker appears) before the post-pull doctor
            # runs, so the allowlist is what stops it and the fast-forward is the local step it
            # protects
            fl_root, fl_bare = _driver_paired("driver-flip")
            _git(fl_root, "config", "--local", "filter.pwn.smudge", str(drv_prog))
            if os.path.exists(drv_marker):
                os.unlink(drv_marker)
            with patch.object(module, "_local_key_allowlisted", lambda low: True), \
                    patch.object(module, "_doctor", lambda root: valid_doctor):
                rc, _out = run(["--pull", "--root", fl_root])
            check("dropping the allowlist lets the fast-forward checkout run the smudge program "
                  "(shown red)",
                  rc == EXIT_OK and os.path.exists(drv_marker) and _head(fl_root) == _tip(fl_bare))
            # -- a store with git submodule machinery refuses before any step ---------------------
            # A submodule's own repository config (.git/modules/<name>/config) is a scope the
            # repository-config allowlist cannot enumerate, and the clean-check status probe
            # descends into a populated submodule and runs a clean filter that config names, so
            # the class refuses: the populated case first (the filter program shown never
            # running), then each trigger alone.
            sm_prog = Path(base) / "submodule-prog"
            sm_marker = os.path.join(base, "submodule-marker")
            sm_prog.write_text("#!/bin/sh\n: > {}\ncat\n".format(sm_marker), encoding="utf-8")
            os.chmod(str(sm_prog), 0o755)
            sm_inner = os.path.join(base, "submodule-inner")
            os.makedirs(sm_inner)
            (Path(sm_inner) / "inner.txt").write_text("inner\n", encoding="utf-8")
            (Path(sm_inner) / ".gitattributes").write_text("* filter=pwn\n", encoding="utf-8")
            _git(sm_inner, "init")
            _git(sm_inner, "add", "-A")
            _git(sm_inner, "commit", "-m", "inner seed")
            sm_root, sm_bare = _paired("submodule")
            _git(sm_root, "-c", "protocol.file.allow=always", "submodule", "add", sm_inner, "sub")
            _git(sm_root, "commit", "-m", "add submodule")
            _git(sm_root, "push", "origin", "main")
            # the target bumps the submodule pointer (the other system moves the gitlink)
            (Path(sm_inner) / "inner.txt").write_text("inner two\n", encoding="utf-8")
            _git(sm_inner, "add", "-A")
            _git(sm_inner, "commit", "-m", "inner advance")
            sm_new = _head(sm_inner)
            smw = os.path.join(base, "submodule-writer")
            _git(base, "clone", sm_bare, smw)
            _git(smw, "update-index", "--add", "--cacheinfo", "160000,{},sub".format(sm_new))
            _git(smw, "commit", "-m", "bump submodule")
            _git(smw, "push", "origin", "main")
            # the submodule's own config names the filter program and its worktree is dirty, the
            # exact shape under which a status probe over the changed gitlink path would run it
            sm_sub = os.path.join(sm_root, "sub")
            _git(sm_sub, "config", "filter.pwn.clean", str(sm_prog))
            (Path(sm_sub) / "inner.txt").write_text("locally edited\n", encoding="utf-8")
            sm_head = _head(sm_root)
            for argv in ([], ["--pull"]):
                rc, out = run(argv + ["--root", sm_root])
                check("a store with a populated submodule refuses before any step, the filter "
                      "program never running ({})".format(" ".join(argv) or "bare"),
                      rc == EXIT_MALFORMED and "submodule" in out
                      and not os.path.exists(sm_marker) and _head(sm_root) == sm_head
                      and _no_fetch_head(sm_root))
            # the named flip: with the submodule gate and the allowlist dropped (the submodule's
            # bookkeeping keys would refuse on the allowlist too), the pull's clean probe descends
            # into the submodule and RUNS the program its own config names, before the dirty-tree
            # refusal and with the parent HEAD unmoved: the vector above watches a real exec path
            with patch.object(module, "_refuse_submodules", lambda g, r: None), \
                    patch.object(module, "_local_key_allowlisted", lambda low: True):
                run(["--pull", "--root", sm_root])
            check("dropping the submodule gate runs the submodule-configured program (shown red)",
                  os.path.exists(sm_marker) and _head(sm_root) == sm_head)
            # each trigger alone refuses: a .gitmodules file, a gitlink in HEAD's tree, an entry
            # under .git/modules
            gm_root, _gm_bare = _paired("gitmodules-file")
            (Path(gm_root) / ".gitmodules").write_text("", encoding="utf-8")
            rc, out = run(["--root", gm_root])
            check("a .gitmodules file alone refuses before any step",
                  rc == EXIT_MALFORMED and ".gitmodules" in out and _no_fetch_head(gm_root))
            gl_root, _gl_bare = _paired("gitlink")
            _git(gl_root, "update-index", "--add",
                 "--cacheinfo", "160000,{},linked".format(_head(gl_root)))
            _git(gl_root, "commit", "-m", "gitlink only")
            rc, out = run(["--root", gl_root])
            check("a gitlink in HEAD's tree alone refuses before any step",
                  rc == EXIT_MALFORMED and "gitlink" in out and _no_fetch_head(gl_root))
            md_root, _md_bare = _paired("modules-dir")
            os.makedirs(os.path.join(md_root, ".git", "modules", "ghost"))
            rc, out = run(["--root", md_root])
            check("an entry under .git/modules alone refuses before any step",
                  rc == EXIT_MALFORMED and "modules" in out and _no_fetch_head(md_root))
            # an INDEX-ONLY gitlink (a staged embedded repository, absent from HEAD) refuses
            # before any step: the embedded repository's own config is live, and the clean-check
            # status probe would descend into it and run the clean filter that config names
            ix_marker = os.path.join(base, "indexlink-marker")
            ix_prog = Path(base) / "indexlink-prog"
            ix_prog.write_text("#!/bin/sh\n: > {}\ncat\n".format(ix_marker), encoding="utf-8")
            os.chmod(str(ix_prog), 0o755)
            ix_root, ix_bare = _paired("indexlink")
            ix_sub = os.path.join(ix_root, "sub")
            os.makedirs(ix_sub)
            (Path(ix_sub) / "inner.txt").write_text("inner\n", encoding="utf-8")
            (Path(ix_sub) / ".gitattributes").write_text("* filter=pwn\n", encoding="utf-8")
            _git(ix_sub, "init")
            _git(ix_sub, "add", "-A")
            _git(ix_sub, "commit", "-m", "embedded seed")
            _git(ix_sub, "config", "filter.pwn.clean", str(ix_prog))
            (Path(ix_sub) / "inner.txt").write_text("locally edited\n", encoding="utf-8")
            ix_inner = _head(ix_sub)
            _git(ix_root, "update-index", "--add", "--cacheinfo",
                 "160000,{},sub".format(ix_inner))
            # the target records a gitlink at the same path (the other system commits it)
            ixw = os.path.join(base, "indexlink-writer")
            _git(base, "clone", ix_bare, ixw)
            _git(ixw, "update-index", "--add", "--cacheinfo", "160000,{},sub".format(ix_inner))
            _git(ixw, "commit", "-m", "record gitlink")
            _git(ixw, "push", "origin", "main")
            ix_head = _head(ix_root)
            for argv in ([], ["--pull"]):
                rc, out = run(argv + ["--root", ix_root])
                check("an index-only gitlink refuses before any step, the embedded repository's "
                      "filter never running ({})".format(" ".join(argv) or "bare"),
                      rc == EXIT_MALFORMED and "index" in out and "gitlink" in out
                      and not os.path.exists(ix_marker) and _head(ix_root) == ix_head
                      and _no_fetch_head(ix_root))
            # the named flip: with the index scan dropped (nothing else fires: the gitlink is in
            # no tree and no .gitmodules or .git/modules entry exists), the pull's clean probe
            # descends into the staged embedded repository and RUNS the program its own config
            # names, before the dirty-tree refusal and with HEAD unmoved
            with patch.object(module, "_refuse_index_gitlinks", lambda *a, **k: None):
                run(["--pull", "--root", ix_root])
            check("dropping the index scan runs the staged embedded repository's configured "
                  "program (shown red)",
                  os.path.exists(ix_marker) and _head(ix_root) == ix_head)
            # a store NESTED below its repository toplevel cannot sit beside submodule machinery
            # outside its own subtree: the .gitmodules check covers the toplevel and the gitlink
            # scans cover the whole index and HEAD's full tree
            nt_top = os.path.join(base, "nested-top")
            nt_store = os.path.join(nt_top, "store")
            os.makedirs(nt_store)
            nt_manifest = _opf_init._manifest_model()
            nt_manifest["store"]["sync_target"] = "git:" + os.path.join(base, "nested-absent.git")
            _write_manifest(nt_store, nt_manifest)
            (Path(nt_store) / "data.txt").write_text("one\n", encoding="utf-8")
            _git(nt_top, "init")
            _git(nt_top, "add", "-A")
            _git(nt_top, "commit", "-m", "seed nested")
            (Path(nt_top) / ".gitmodules").write_text("", encoding="utf-8")
            rc, out = run(["--root", nt_store])
            check("a .gitmodules file at the repository toplevel refuses a nested store",
                  rc == EXIT_MALFORMED and ".gitmodules" in out and "toplevel" in out
                  and _no_fetch_head(nt_top))
            (Path(nt_top) / ".gitmodules").unlink()
            _git(nt_top, "update-index", "--add", "--cacheinfo",
                 "160000,{},linked".format(_head(nt_top)))
            rc, out = run(["--root", nt_store])
            check("a gitlink outside a nested store's subtree refuses through the index scan",
                  rc == EXIT_MALFORMED and "index" in out and "gitlink" in out
                  and _no_fetch_head(nt_top))
            _git(nt_top, "commit", "-m", "gitlink at toplevel")
            with patch.object(module, "_refuse_index_gitlinks", lambda *a, **k: None):
                rc, out = run(["--root", nt_store])
            check("a gitlink outside a nested store's subtree refuses through HEAD's full tree",
                  rc == EXIT_MALFORMED and "HEAD's tree" in out and "gitlink" in out
                  and _no_fetch_head(nt_top))

            # every entry of every documentation list refuses, plain, under a subsection and in
            # any case, through the same enumeration (its listing stubbed); a name one character
            # past an entry refuses TOO, through the allowlist (the generic refusal that names the
            # adopter's global configuration), never silently passing; every allowlisted name
            # passes in lower and upper case, and the boundary spellings just outside the
            # allowlist (no subsection, an empty subsection, an extra subsection, one character
            # past the key) refuse
            sweep_misses = []

            def _sweep(name, want_refused, want_needle=None):
                listing = _opf_observe._GitOutcome(True, 0, name + b"\x00", "")
                with patch.object(_opf_observe, "_run_git", lambda *a, **k: listing):
                    try:
                        _refuse_local_redirect_config(git, base)
                        refused, msg = False, ""
                    except SyncError as exc:
                        refused, msg = True, str(exc)
                if refused != want_refused or (want_needle is not None and refused
                                               and want_needle not in msg):
                    sweep_misses.append(name)

            for family in (_LOCAL_REDIRECT_KEYS, _LOCAL_EXEC_KEYS, _LOCAL_SIGN_KEYS,
                           _LOCAL_REMOTE_PROGRAM_KEYS, _LOCAL_PROTOCOL_KEYS, _LOCAL_TRUST_KEYS,
                           _LOCAL_COOKIE_KEYS, _LOCAL_BUNDLE_KEYS, _LOCAL_INCLUDE_KEYS,
                           _LOCAL_FILTER_KEYS, _LOCAL_CONTENT_DRIVER_KEYS, _LOCAL_FORMAT_KEYS):
                for listed in family:
                    section, _dot, key = listed.partition(b".")
                    _sweep(listed, True)
                    _sweep(listed.upper(), True)
                    _sweep(section + b".https://example.invalid/." + key, True)
                    _sweep(listed + b"x", True, want_needle="allowlist")
                    _sweep(section + b".x" + key, True, want_needle="allowlist")
            for allowed in _ALLOWED_EXACT_KEYS:
                section, _dot, key = allowed.partition(b".")
                _sweep(allowed, False)
                _sweep(allowed.upper(), False)
                _sweep(section + b".x." + key, True, want_needle="allowlist")
                _sweep(allowed + b"x", True, want_needle="allowlist")
            for allowed in _ALLOWED_SUBSECTION_KEYS:
                section, _dot, key = allowed.partition(b".")
                _sweep(section + b".origin." + key, False)
                _sweep((section + b".origin." + key).upper(), False)
                # a subsection may itself contain dots (a remote named a.b)
                _sweep(section + b".a.b." + key, False)
                _sweep(allowed, True, want_needle="allowlist")
                _sweep(section + b".." + key, True, want_needle="allowlist")
                _sweep(section + b".origin." + key + b"x", True, want_needle="allowlist")
                _sweep(section + b".origin.x" + key, True, want_needle="allowlist")
            # a remote.* subsection must be a PLAIN remote name: a URL-shaped or otherwise
            # resolvable name refuses with the remote-name reason (git resolves a transport
            # argument equal to a remote's name to that remote's configuration), tested just inside
            # and just outside the plain charset at each edge, while a branch subsection stays free
            # (a branch name legitimately contains '/')
            for bad_sub in (b"file:///t/example.git", b"https://example.invalid/r.git",
                            b"git@example.invalid:r.git", b"a/b", b"a:b", b"a@b", b"a b",
                            b"-lead", b".", b".."):
                for allowed in _ALLOWED_REMOTE_SUBSECTION_KEYS:
                    _sweep(b"remote." + bad_sub + b"." + allowed.partition(b".")[2], True,
                           want_needle="plain remote name")
            for ok_sub in (b"origin", b"a.b", b"mirror-2", b"UP_stream", b"lead-"):
                _sweep(b"remote." + ok_sub + b".url", False)
            _sweep(b"branch.feature/x.merge", False)
            _sweep(b"branch.a@b.remote", False)
            check("the stubbed sweep holds at every edge: documented keys refuse in each form, "
                  "near-miss names refuse through the allowlist, allowlisted names pass, and the "
                  "spellings just outside the allowlist refuse ({})".format(sweep_misses[:8]),
                  not sweep_misses)

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
