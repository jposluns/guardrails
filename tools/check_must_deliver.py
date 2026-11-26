#!/usr/bin/env python3
"""Must-deliver prefix gate: the whole core layer of every rule listed in .aiqt/core/must-deliver.toml
stays inside the first check_release_build.CAP_BYTES bytes of AGENTS.md. A rule with no `## Detail` split
is all core, so its whole text must fit; a split rule's detail layer is rendered after every rule's core
(gen_agents) and is never measured. A reader capped at CAP_BYTES receives no detail layer only while the
first detail section starts at or past the cap: that holds for this corpus, whose core layers alone run
past the cap, but it follows from the corpus, not from the layout, and this gate does not check it (a
corpus whose core layers end inside the cap puts detail text inside it).

The first-pin demonstration (tools/gen_first_pin_demo.py) DERIVES its delivered list from AGENTS.md, so
that list shrinks silently when the rules grow and every byte-identity gate stays green: a rule can fall
out of the delivered prefix with no check failing. This gate closes that gap for the rules a maintainer
has declared must stay delivered. The list is hand-edited policy, like .aiqt/core/instruction-budget.toml:
additions may be proposed in any PR and stay free; a removal must be declared (see LIST RATCHET below).

LIST RATCHET. The list is compared with the list at a comparison base: --base REF, or with no flag the
merge base of HEAD and origin/main (no base that resolves is exit 2, never "nothing to compare"). The
default is the merge base, not the origin/main tip, on purpose: it is the commit this branch forked
from, so the comparison sees only the list changes made on this branch, and a rule added to
origin/main after the fork is not reported as dropped by a branch that has not merged it yet.
--base REF is a closed grammar (an allowlist that fails closed): REF is accepted only as a full 40-hex
object id that exists, HEAD, or refs/remotes/<name> or <configured remote>/<name> where git
check-ref-format accepts the ref (refs/remotes/<name> as written, or refs/remotes/<REF> for the
configured-remote form), each optionally followed by a run of ~N or ^N suffixes (N digits, possibly
empty), and every other value (an abbreviated object id, a describe form, a bare remote name, a local
branch or tag name, remotes/origin/main, any brace, @{, : or other revision syntax) is exit 2 with a
message naming the accepted forms, before git resolves anything. Within the grammar git is never
handed a short name: a remote base is read as its fully qualified ref with show-ref --verify: the
default reads exactly refs/remotes/origin/main and HEAD (show-ref --verify HEAD) and hands git
merge-base the two object ids, and a remote-tracking --base without suffixes is read
as exactly that ref, so a tag, a local branch or a pseudo-ref file of the same short name, which git's
short-name search order prefers, cannot stand in for it (an absent remote-tracking ref is exit 2); a
remote-tracking name leading a suffix run is exit 2 when that remote-tracking ref does not exist
(origin/gone~0) or is shadowed by refs/<name>, refs/tags/<name> or refs/heads/<name> (origin/main~1
with a tag origin/main): pass the fully qualified form (refs/remotes/origin/main~1) or a commit id
instead; otherwise git is handed the object id that ref holds followed by the suffixes. HEAD, alone
or leading a suffix run, is read as HEAD (show-ref --verify HEAD) before any remote name is
considered, so a remote named HEAD does not change it, and git is handed the object id it holds
followed by the suffixes; a full object id is handed to git as written. Before that, git is asked
which ref it resolves a full object id or HEAD, and the value with each part of its suffix run,
through (git rev-parse --symbolic-full-name with core.warnAmbiguousRefs off, so git names the ref its
own search takes, by whichever of its name rules, refs/remotes/<name>/HEAD included): it is exit 2
when that ref is under refs/remotes/, or is where a symbolic ref under refs/remotes/ also ends (git
names only the end of a symbolic chain); it is also exit 2 as ambiguous when refs/remotes/<id> or
refs/remotes/HEAD exists for the id or HEAD leading the value, and a full object id whose object does
not exist is exit 2. Every git read runs with replace objects, grafts and the commit-graph file off.
tools/run_all_checks.sh runs the bare form, so a
local run compares with that default; CI passes --base (the pull request's target branch, a push's
before SHA, or HEAD^ for any other event when it resolves). The
base file is read from the base commit by the base parser (_must_deliver.parse_base_declared), which
accepts exactly the format-version 1 schema or exactly the format-version 2 schema, so a base written
before version 2 still compares; a base commit without it, or with it as a symlink or malformed (any
other version, a version 1 file carrying reserve-bytes, a version 2 file without it), is exit 2. A rule listed at the base and no
longer listed is exit 1 naming it, unless a [[removal]] row (exactly id and reason) names it; each
such removal is printed with its reason. A [[removal]] row that is duplicated, also listed in [[rule]],
has an empty reason or carries an unknown key is exit 2. Removal rows may stay after the merge.

The spans come from gen_first_pin_demo.measure, the ONE span algorithm the demonstration generator also
uses: AGENTS.md must be byte-identical to a fresh composition of the rule corpus and the block registry
(a stale AGENTS.md is exit 2, never measured), offsets are UTF-8 bytes made absolute against the composed
rule region, a rule's span is its core body (its whole body when it has no split), and a rule is
delivered only when its end offset (exclusive) is at most the cap. This gate
never reads the demonstration's own lists; it recomputes everything. The cap is read from
check_release_build.CAP_BYTES at call time, never copied here.

CORE MOVES. A listed rule's core layer cannot shrink by moving its text into a detail layer, its own or
another rule's, where the prefix measure no longer sees it (core_moves): against the same comparison base
as the list ratchet, each sentence of a listed rule's core layer at the base (whitespace runs read as one
space, then the text cut at each space that follows a . ! or ?, so a dot inside a word such as v1.2 ends no
sentence) that the working core layer holds fewer times than the base core does (so one copy of a repeated
sentence counts) has moved when any rule's working detail layer holds the whole sentence, or a piece of
that one sentence of at least MIN_PIECE_BYTES UTF-8 bytes that the working core holds fewer times than the
base core does. The floor applies to each sentence on its own: a piece never runs past its sentence's end,
so a move that crosses a sentence end counts each side of it on its own: a side that is its whole sentence
counts at any length, and any other side only when it reaches MIN_PIECE_BYTES. A
piece starts at a word's start or at the word's first letter or digit (so after an opening bracket or
quote), and ends where the detail layer's copy of it ends or, when the working core holds that longer
piece at least as often as the base core, at the latest earlier word end that passes the count; so a
wrapped line or a clause moved word for word is a piece. Any moved text is exit 1 naming the rule, the
moved bytes, the first FIRST_PIECE_CHARS characters of the first moved piece and the rule whose detail
layer holds it. A listed rule that has no detail layer of
its own is compared too. A
listed rule whose working core layer is empty while it has a detail layer is exit 1 whatever the base
holds (its title alone stays in the prefix; the prefix measure also exits 2 then, since a title-only core
body recurs inside its own detail heading). Both layers are cut as gen_rules.core_layer and detail_layer
cut them (source_layers), without the body grammar, so a base source written under an older grammar is
still read; the base sources are read from the object store with the same git reads as the base list, and
a base without the rule directory, or a source there that is not UTF-8, carries no corpus-id or title, or
repeats another's corpus-id, is exit 2.

The declared file carries exactly format-version (the integer 2), warn-margin-bytes, reserve-bytes and
[[rule]] rows of exactly id and reason, and optionally [[removal]] rows of exactly id and reason; every
listed id must be a corpus-id with a measured span. RESERVE. reserve-bytes (an integer of at least 0 and
at most warn-margin-bytes, never a bool or a float) is enforced: a listed rule whose core layer ends
fewer than reserve-bytes bytes inside the cap (margin = cap - end below reserve-bytes) is exit 1 naming
the tightest margin and its rule, so the margin is held, not only reported; a margin equal to
reserve-bytes passes. The reserve protects listed rules only, never an unlisted one. RESERVE RATCHET.
The list ratchet also compares reserve-bytes with the reserve-bytes listed at the same comparison base (a
format-version 1 base, which has none, counts as 0): a working reserve lower than the base's is exit 1
naming both, and a reserve kept or raised passes. The list's one strict reader is tools/_must_deliver.py, shared with gen_agents (which puts
the listed rules first in AGENTS.md, after the apex), gen_first_pin_demo.measure and conformance. An absent, unreadable, symlinked, non-UTF-8,
malformed or EMPTY list is exit 2 (a missing input is a failure, never "nothing to check"), per the
check-fails-closed-on-unreadable rule. The gate prints every listed rule's end offset and margin and the
reserve on every run, and a WARNING (still exit 0) when the tightest margin is at least reserve-bytes but
under warn-margin-bytes, so growth is visible before the reserve fails it.

DISCLOSED RESIDUAL (disclose-guard-residuals): a PR that removes a rule from the list AND adds a
[[removal]] row for it passes this gate (the row turns a silent deletion into a named, reasoned record the
gate prints; review must judge the removal itself); the ratchet sees only what changed since the base it
is given, so it is as good as that base (a stale or wrong --base compares against the wrong list;
the grammar refuses values git would resolve as meant (an abbreviated object id, a local branch or
tag name), an over-refusal: pass a full object id; a full object id, which git reads as that object,
and HEAD, which is read exactly, are still refused when refs/remotes/<id> or refs/remotes/HEAD exists
or when git names a ref under refs/remotes/ for them (refs/remotes/<id>/HEAD), an over-refusal a ref
written there can use to fail a push's base closed; a ref git names that a symbolic ref under
refs/remotes/ also ends at is refused even when git reached it directly, an over-refusal; and grafts
and the commit-graph file are off, but any write into the git directory can still change the base or
the list read at it: a .git/shallow file, which also cuts a commit's parents, is read as git reads
it, an object written into the object store under the id of the base list's blob or of a commit is
read as git reads it, and a rewritten ref (refs/remotes/origin/main, HEAD) moves the base it names);
the reserve ratchet is as good as the same base (a lowering already present at the base is not seen),
a deliberate lowering has no declared path like a [[removal]] row, so it takes a reviewed change to this
gate, and a pull request that edits this gate or its self-test rows (T63, T65) together with the list
passes CI with a lowered reserve, so only review sees that; warn-margin-bytes is not compared with the
base; a
listed rule cut down to a stub still passes (content is not judged); the core-move check sees only text
that reaches a detail layer as written, as a whole sentence or as a piece of one sentence of at least
MIN_PIECE_BYTES bytes, so a moved piece of one sentence shorter than MIN_PIECE_BYTES (a parenthetical of a
few words, the last word of a wrapped sentence), a move that crosses a sentence end with each side shorter
than MIN_PIECE_BYTES and neither side a whole sentence (up to almost twice MIN_PIECE_BYTES of one line), a
piece reworded on its way, even by one word, a case change or punctuation inside it, so that no run of it
of MIN_PIECE_BYTES starting at a word survives, a piece the working core still holds at least as often as
the base core did (also elsewhere in that core), core text dropped and rewritten in a detail layer, and
core text moved anywhere other than a rule's detail layer (an unlisted rule's core, which may itself lie
past the cap, a block, or another file) pass; a sentence dropped from a listed core is refused though
nothing moved when some detail layer already holds, anywhere in its text and even inside a longer word,
the whole sentence at any length (Stop. inside Non-Stop.) or a piece of it of at least MIN_PIECE_BYTES,
an over-refusal (reword the detail text to pass); a move
already present at the base is not seen, and a rule that is not listed, or has no source at the base, is
not compared;
the gate measures the repository file against the configured cap, not any platform's actual truncation
behaviour or a budget other instruction files may share; and a client that checks AGENTS.md out with CRLF
line endings inflates what a reader there receives, which this gate does not model (.gitattributes pins
AGENTS.md to eol=lf).

  check_must_deliver.py [--base REF]  check the live tree (default base: merge base of HEAD and
                                      refs/remotes/origin/main)
  check_must_deliver.py --self-test   build synthetic trees and assert each vector is discriminated

Exit convention: 0 every listed rule's core layer ends at least reserve-bytes inside the cap, no rule left
the list undeclared and no listed core layer shrank into a detail layer; 1 a listed rule's core layer ends
past the cap or fewer than reserve-bytes inside it, a rule
listed at the base is no longer listed with no [[removal]] row, or a listed rule's core text moved into a
detail layer or its core layer is empty beside a detail layer (CORE MOVES); 2 an
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
import re
import subprocess
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from gen_rules import CID_RE, DETAIL_HEADING, load_corpus, read_rule_source  # noqa: E402
from gen_first_pin_demo import measure, DemoError  # noqa: E402  the ONE span algorithm, shared
import check_release_build  # noqa: E402  the cap, read as check_release_build.CAP_BYTES at call time
# The list's one strict reader, shared with gen_agents, gen_first_pin_demo and conformance; its read,
# _must_deliver._read_decl_bytes, is a module attribute there, where the self-test injects its read error.
import _must_deliver  # noqa: E402
from _must_deliver import DECL_REL, parse_base_declared, read_declared  # noqa: E402

DEFAULT_BASE_REMOTE = "origin/main"
REMOTE_REF_PREFIX = "refs/remotes/"
GIT_TIMEOUT = 60  # seconds per git call; a call that does not finish is exit 2, never a pass
# GIT_GRAFT_FILE for every git read: a path below the null device, which is not a directory, so it can
# never exist and git reads no grafts at all.
NO_GRAFT_FILE = os.path.join(os.devnull, "aiqt-no-graft-file")


# --- the comparison base (every git return code is checked; a nonzero exit is never an empty result) --

def _git_env():
    """The ambient environment without any GIT_-prefixed variable, so a GIT_DIR, GIT_INDEX_FILE or
    GIT_OBJECT_DIRECTORY set by a hook or a wrapper cannot point the comparison at another repository,
    with replace refs off so a replacement object cannot stand in for the base commit, and grafts off
    (GIT_GRAFT_FILE names NO_GRAFT_FILE, a path that cannot exist, so <git dir>/info/grafts is never read)
    so a graft cannot give the base, HEAD or origin/main other parents (the commit-graph file is
    switched off in _git's argv). A .git/shallow file is still read as git reads it (the disclosed
    residual in the module docstring)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = NO_GRAFT_FILE
    return env


def _git(root, args):
    """Run git -C root with args; returns the CompletedProcess (bytes). The first three `-c` pairs in
    option position switch off automatic gc and maintenance (tools/_git_fixture_env.py
    _NO_AUTO_MAINTENANCE), the posture tools/check_no_dashes.py takes for its own git reads, so no call
    here can leave a detached repack behind. The fourth, core.commitGraph=false (the control
    tools/check_release_delta.py _stage1_git uses), makes git read every commit's parents from the
    commit object, never from a commit-graph file, so a forged graph cannot give a commit other parents
    and move the merge base or a ~ or ^ base. A missing git binary or a call past GIT_TIMEOUT is a
    DemoError (exit 2): the comparison cannot run."""
    try:
        return subprocess.run(["git", "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                               "-c", "maintenance.auto=false", "-c", "core.commitGraph=false", *args],
                              capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT)
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


def _ambiguous_base_refusal(root, core):
    """Why the full object id or HEAD leading the base (core) is ambiguous, or None when it is not. git's
    short-name search also tries refs/remotes/<name>, so when refs/remotes/<core> exists that ref is
    taken as a sign the value was meant as something else: the base is refused (exit 2), never resolved
    either way. git reads a full object id as the object and resolve_base reads HEAD exactly, so this is
    the fail-closed over-refusal the module docstring discloses."""
    full = REMOTE_REF_PREFIX + core
    if _exact_ref_oid(root, full) is not None:
        return "{} also exists, so {!r} is ambiguous (that ref could stand in for it); a " \
               "remote-tracking base is passed as <configured remote>/<branch> or " \
               "refs/remotes/<remote>/<branch>".format(full, core)
    return None


def _short_name_refusal(root, base, lead, full):
    """Why base, a remote-tracking name (lead, whose fully qualified ref is full) leading a run of ~N or
    ^N suffixes, is refused, or None when it is not. resolve_base hands git the object id full holds,
    never the short name, so git's short-name search never runs for it; these refusals keep the
    fail-closed posture of the short form: lead is refused when it is itself a configured remote's name
    (possible only for a remote whose name contains a slash), when full does not exist, and, for the
    <configured remote>/<name> form, when refs/<name>, refs/tags/<name> or refs/heads/<name> exists, a
    ref of the same short name that git's short-name search order would prefer."""
    if lead in _remotes(root):
        return "{!r} is the bare remote name {!r}, which a tag or branch of that name would shadow; " \
               "pass the remote-tracking branch, such as {}/main".format(base, lead, lead)
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
                                                         full + base[len(lead):])


def _written_remote_tracking(name, remotes):
    """True when name is written as a remote-tracking branch: it starts with refs/remotes/ or with a
    configured remote's name and a slash (the text test _grammar_refusal applies)."""
    return name.startswith(REMOTE_REF_PREFIX) or any(name.startswith(r + "/") for r in remotes)


def _resolved_names(base):
    """The names git resolves on the way to base, which the grammar holds to a full object id or HEAD
    and a run of ~N or ^N suffixes: base itself and each part of it that ends right before a ~ or ^. For
    HEAD~1^2 that is the whole value, HEAD and HEAD~1."""
    names = [base] + [base[:i] for i, c in enumerate(base) if c in "~^"]
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
    """Why base, a full object id or HEAD alone or leading a suffix run, resolves through a ref under
    refs/remotes/, or None when git reports it does not. git is asked which ref it resolves base, and each
    part of it ending before a ~ or ^, through (_git_resolved_refs), never this module's copy of git's name
    rules, so a ref under refs/remotes/ that any of git's rules reaches is refused (exit 2): one git names
    directly, and, because git names only the end of a symbolic chain, a symbolic ref under refs/remotes/
    that ends at the ref git names (that ref is then refused even when git reached it directly, a
    fail-closed over-refusal)."""
    symbolic = None
    hint = "a remote-tracking base is passed as <configured remote>/<branch> or " \
           "refs/remotes/<remote>/<branch>"
    for name in _resolved_names(base):
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


ACCEPTED_BASE_FORMS = ("a full 40-hex object id that exists, HEAD, or refs/remotes/<name> or "
                       "<configured remote>/<name> where git check-ref-format accepts the ref, each "
                       "optionally followed by a run of ~N or ^N suffixes (N digits, possibly empty)")


def _split_suffixes(base):
    """(core, suffixes): base cut at its first ~ or ^ (HEAD and ~1^2 of HEAD~1^2)."""
    cut = min([i for i in (base.find("~"), base.find("^")) if i >= 0] or [len(base)])
    return base[:cut], base[cut:]


def _id_or_head(core):
    """True when core is a full 40-hex object id (lower case) or HEAD, the grammar's non-remote forms."""
    return (len(core) == 40 and all(c in "0123456789abcdef" for c in core)) or core == "HEAD"


def _grammar_refusal(root, base):
    """Why base is outside the closed --base grammar (ACCEPTED_BASE_FORMS), or None when it is inside it.
    The grammar is an allowlist that fails closed: base is cut at its first ~ or ^, the rest must be a run
    of ~N or ^N suffixes (N ASCII digits, possibly empty), and what leads it must be a full 40-hex object
    id (lower case), HEAD, or a name starting with refs/remotes/ or with a configured remote's name and a
    slash whose fully qualified ref (the name itself, or refs/remotes/<name>) git check-ref-format
    accepts. Every other value, whatever name form git might read it by, is refused (exit 2) before git
    resolves anything; whether an accepted value resolves is left to resolve_base's refusals."""
    core, suffixes = _split_suffixes(base)
    if not all(c in "~^0123456789" for c in suffixes):
        why = "{!r} after {!r} is not a run of ~N or ^N suffixes".format(suffixes, core)
    elif _id_or_head(core):
        return None
    elif core.startswith(REMOTE_REF_PREFIX) or any(core.startswith(r + "/") for r in _remotes(root)):
        full = core if core.startswith(REMOTE_REF_PREFIX) else REMOTE_REF_PREFIX + core
        if _git(root, ["check-ref-format", full]).returncode == 0:
            return None
        why = "git check-ref-format does not accept {}".format(full)
    else:
        why = "{!r} is not a full 40-hex object id, HEAD, refs/remotes/<name> or " \
              "<configured remote>/<name>".format(core)
    return "it is not one of the accepted --base forms ({}); {}".format(ACCEPTED_BASE_FORMS, why)


def resolve_base(root, base):
    """(commit, label) of the comparison base: the explicit ref, or with base None the merge base of
    HEAD and refs/remotes/origin/main, each read exactly with show-ref --verify and handed to git merge-base
    as an object id. An explicit base outside the closed grammar (_grammar_refusal,
    ACCEPTED_BASE_FORMS) is refused first, and git is never handed a short name for one inside it. A
    full object id or HEAD (checked first, so a remote named HEAD does not change it) is refused when
    refs/remotes/<id> or refs/remotes/HEAD exists (_ambiguous_base_refusal) or when git resolves it
    through a ref under refs/remotes/ (_resolved_ref_refusal); HEAD is read exactly with show-ref
    --verify, and git is handed the object id followed by the suffix run. A remote base is read as its
    fully qualified ref with show-ref --verify, so a tag, a local branch (refs/tags/origin/main,
    refs/heads/origin/main) or a pseudo-ref file (<git dir>/origin/main) of the same short name, which
    git's short-name search order prefers, cannot stand in for it; when it leads a suffix run it is
    refused when that ref does not exist or is shadowed by a ref of the same short name
    (_short_name_refusal), and git is handed the object id the ref holds followed by the suffix run. A
    base that does not resolve to one commit (a full object id whose object does not exist included) is
    a DemoError (exit 2), never "nothing to compare"."""
    if base is None:
        full = REMOTE_REF_PREFIX + DEFAULT_BASE_REMOTE
        tip = _exact_ref_oid(root, full)
        if tip is None:
            raise DemoError("no comparison base resolves: {} does not exist; pass --base REF or fetch "
                            "{}".format(full, DEFAULT_BASE_REMOTE))
        head = _exact_ref_oid(root, "HEAD")
        if head is None:
            raise DemoError("no comparison base resolves: HEAD does not resolve (show-ref --verify HEAD); "
                            "pass --base REF")
        proc = _git(root, ["merge-base", head, tip])
        commit = _object_id(proc.stdout.decode("ascii", "replace")) if proc.returncode == 0 else None
        if commit is None:
            raise DemoError("no comparison base resolves: git merge-base HEAD {} failed ({}); pass "
                            "--base REF or fetch {}".format(full, _git_err(proc), DEFAULT_BASE_REMOTE))
        return commit, "merge base of HEAD and {} ({}, {})".format(DEFAULT_BASE_REMOTE, full, commit[:12])
    if not base or base.startswith("-"):
        raise DemoError("--base {!r} is not a ref".format(base))
    refusal = _grammar_refusal(root, base)
    if refusal is not None:
        raise DemoError("cannot resolve the comparison base {!r}: {}".format(base, refusal))
    core, suffixes = _split_suffixes(base)
    if _id_or_head(core):
        refusal = _ambiguous_base_refusal(root, core)
        if refusal is not None:
            raise DemoError("cannot resolve the comparison base {!r}: {}".format(base, refusal))
        refusal = _resolved_ref_refusal(root, base)
        if refusal is not None:
            raise DemoError("cannot resolve the comparison base {!r}: {}".format(base, refusal))
        oid = _exact_ref_oid(root, "HEAD") if core == "HEAD" else core
        commit = _peel_commit(root, oid + suffixes) if oid is not None else None
        if commit is None:
            raise DemoError("cannot resolve the comparison base {!r} to a commit".format(base))
        return commit, "{} ({})".format(base, commit[:12])
    # Inside the grammar, anything else is written as a remote-tracking branch.
    full = core if core.startswith(REMOTE_REF_PREFIX) else REMOTE_REF_PREFIX + core
    if not suffixes:
        tip = _exact_ref_oid(root, full)
        commit = _peel_commit(root, tip) if tip is not None else None
        if commit is None:
            raise DemoError("cannot resolve the comparison base {!r}: the remote-tracking ref {} does not "
                            "exist or is not a commit".format(base, full))
        return commit, "{} ({}, {})".format(base, full, commit[:12])
    refusal = _short_name_refusal(root, base, core, full)
    if refusal is not None:
        raise DemoError("cannot resolve the comparison base {!r} by short name: {}".format(base, refusal))
    tip = _exact_ref_oid(root, full)
    commit = _peel_commit(root, tip + suffixes) if tip is not None else None
    if commit is None:
        raise DemoError("cannot resolve the comparison base {!r} to a commit".format(base))
    return commit, "{} ({})".format(base, commit[:12])


def read_base_declared(root, commit, label):
    """The declaration as listed at the base commit, read through parse_base_declared (exactly format-version
    1 or exactly 2, so a base written before version 2 still compares). A base commit without
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
    return parse_base_declared(proc.stdout, where)


def ratchet(root, base):
    """Compare the working list with the list at the comparison base. Exit 0 when every rule listed at
    the base is still listed or named by a [[removal]] row and the working reserve-bytes is at least the
    reserve at the base (a format-version 1 base has none and counts as 0), 1 when a rule is neither
    listed nor removed (named) or the reserve was lowered (both reserves named), 2 when the base does not
    resolve or either list cannot be read. Additions and a raised reserve are free."""
    try:
        working = read_declared(root / DECL_REL)
        ids, removals = working.ids, working.removals
        commit, label = resolve_base(root, base)
        base_decl = read_base_declared(root, commit, label)
        base_ids = base_decl.ids
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    # parse_base_declared reads a format-version 1 base, which has no reserve-bytes, as reserve 0.
    lowered = working.reserve < base_decl.reserve
    print("must-deliver: reserve ratchet against base {}: reserve-bytes {} there (0 for a format-version 1 "
          "base), {} now".format(label, base_decl.reserve, working.reserve))
    if lowered:
        print("must-deliver: FAIL: reserve-bytes {} is lower than the reserve-bytes {} listed at the base {}; "
              "the reserve may be kept or raised, never lowered".format(working.reserve, base_decl.reserve,
                                                                         label))
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
    return 1 if undeclared or lowered else 0


# --- the core-move check (CORE MOVES in the module docstring) -------------------------------------------

RULES_REL = ".aiqt/core/rules"
_UNIT_END_RE = re.compile(r"(?<=[.!?])\s+")


def source_layers(text, label):
    """(corpus_id, core, detail) of one rule source's text: the body after the frontmatter's closing `---`
    line, its title line (the first non-blank body line) dropped, cut at the first line that is exactly the
    split line gen_rules.DETAIL_HEADING; core is the text above that line and detail the text below it (None
    with no split, when the whole body below the title is core). The same cut gen_rules.core_layer and
    detail_layer make, read without the body grammar, so a source at a base commit written under an older
    grammar is still read. A source with no frontmatter, no corpus-id line or no title line is a DemoError
    (exit 2)."""
    lines = text.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise DemoError("{} has no frontmatter block".format(label))
    close = lines.index("---", 1)
    ids = [line.split(":", 1)[1].strip() for line in lines[1:close]
           if line.split(":", 1)[0].strip() == "corpus-id" and ":" in line]
    if len(ids) != 1 or not CID_RE.fullmatch(ids[0]):
        raise DemoError("{} does not carry exactly one well-formed corpus-id line".format(label))
    body = lines[close + 1:]
    title = next((i for i, line in enumerate(body) if line.strip()), None)
    if title is None:
        raise DemoError("{} has no title line".format(label))
    rest = body[title + 1:]
    if DETAIL_HEADING in rest:
        cut = rest.index(DETAIL_HEADING)
        return ids[0], "\n".join(rest[:cut]), "\n".join(rest[cut + 1:])
    return ids[0], "\n".join(rest), None


def _units(text):
    """The sentence units of text: whitespace runs read as one space, then cut at each space that follows a
    . ! or ? (a dot inside a word, as in v1.2, ends no unit)."""
    return [u for u in _UNIT_END_RE.split(" ".join(text.split())) if u]


MIN_PIECE_BYTES = 24  # a moved piece of one sentence, shorter than it, counts from this many UTF-8 bytes
FIRST_PIECE_CHARS = 120  # a failing core-move line prints this many characters of the first moved piece


def _moved_piece(unit, base_text, core_text, detail_text):
    """The longest piece of unit (a base core sentence that has left the working core) that a detail layer
    holds and that the working core holds fewer times than the base core does, when the piece is the whole
    sentence or at least MIN_PIECE_BYTES long; None when there is none. The piece lies inside unit, so the
    floor applies to each sentence on its own. A piece starts at a word's start or at the word's first
    letter or digit (after an opening bracket or quote), and ends where the detail layer's copy of it ends
    or, when that piece fails the count, at the latest earlier word end where it passes while it stays at
    least MIN_PIECE_BYTES long. Every text here has its whitespace runs read as one space."""
    if unit in detail_text:
        return unit
    starts, pos = [], 0
    for word in unit.split(" "):
        lead = next((k for k, ch in enumerate(word) if ch.isalnum()), 0)
        starts += [pos] if lead == 0 else [pos, pos + lead]
        pos += len(word) + 1
    best = None
    for start in starts:
        lo, hi = start, len(unit)  # the longest prefix of unit[start:] a detail layer holds ends at lo
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if unit[start:mid] in detail_text:
                lo = mid
            else:
                hi = mid - 1
        for end in [lo] + [e for e in range(lo - 1, start, -1) if unit[e] == " "]:
            piece = unit[start:end].rstrip(" ")
            if len(piece.encode("utf-8")) < MIN_PIECE_BYTES:
                break
            if core_text.count(piece) < base_text.count(piece):
                if best is None or len(piece) > len(best):
                    best = piece
                break
    return best


def moved_units(base_core, core, details):
    """The text moved out of a rule's core layer into a detail layer: for each sentence unit of base_core (a
    rule's core layer at the comparison base) that the working core layer holds fewer times than base_core
    does, the longest piece of it that one of the working detail layers (details, every rule's) holds and
    the working core holds fewer times than base_core does, when that piece is the whole sentence or at
    least MIN_PIECE_BYTES long (_moved_piece). Every text is compared as a substring with whitespace runs
    read as one space, and a sentence repeated in base_core is judged once, by its count."""
    base_text, core_text = " ".join(base_core.split()), " ".join(core.split())
    detail_text = "\n".join(" ".join(d.split()) for d in details if d)
    moved = []
    for unit in dict.fromkeys(_units(base_core)):
        if core_text.count(unit) >= base_text.count(unit):
            continue
        piece = _moved_piece(unit, base_text, core_text, detail_text)
        if piece is not None:
            moved.append(piece)
    return moved


def _working_layers(root):
    """{corpus-id: (core, detail)} for every rule source in the working tree, read with the corpus loader
    (gen_rules.load_corpus) and cut by source_layers."""
    out = {}
    for src, _fm, _rel in load_corpus(root / RULES_REL):
        rid, core, detail = source_layers(read_rule_source(src), "{} ({})".format(src.name, RULES_REL))
        out[rid] = (core, detail)
    return out


def _base_layers(root, commit, label):
    """{corpus-id: core} for every rule source (*.md, a regular blob) under RULES_REL at the base commit,
    read from the object store with the gate's git reads. A base with no rule directory, a source that is
    not UTF-8, carries no corpus-id or no title, or a corpus-id carried twice is a DemoError (exit 2)."""
    where = "{} at the base {}".format(RULES_REL, label)
    proc = _git(root, ["ls-tree", "-r", "-z", "--full-tree", commit, "--", RULES_REL])
    if proc.returncode != 0:
        raise DemoError("git ls-tree failed for {} ({})".format(where, _git_err(proc)))
    entries = [e for e in proc.stdout.split(b"\0") if e]
    if not entries:
        raise DemoError("{} does not exist; the base core layers cannot be read".format(where))
    cores = {}
    for entry in entries:
        meta, _tab, path = entry.partition(b"\t")
        fields = meta.split()
        name = path.decode("utf-8", "replace")
        if not name.endswith(".md") or len(fields) != 3 or fields[1] != b"blob":
            continue
        if fields[0] not in (b"100644", b"100755"):
            raise DemoError("{} at the base {} is not a regular file".format(name, label))
        blob = _git(root, ["cat-file", "blob", fields[2].decode("ascii", "replace")])
        if blob.returncode != 0:
            raise DemoError("{} at the base {} is unreadable ({})".format(name, label, _git_err(blob)))
        try:
            text = blob.stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DemoError("{} at the base {} is not valid UTF-8 ({})".format(name, label, exc))
        rid, core, _detail = source_layers(text, "{} at the base {}".format(name, label))
        if rid in cores:
            raise DemoError("corpus-id {} is carried twice under {}".format(rid, where))
        cores[rid] = core
    return cores


def core_moves(root, base):
    """The core-move check: exit 1 when a listed rule's core layer is empty while it has a detail layer, or
    when text of a listed rule's core layer at the comparison base (the same base as the list ratchet) has
    moved into any rule's working detail layer, its own or another's, split or not itself (moved_units),
    naming the rule, the moved bytes, the first FIRST_PIECE_CHARS characters of the first moved piece and the
    rule whose detail layer holds it; 0
    otherwise; 2 when the base, the list or a source cannot be read. A rule not present at the base has
    nothing moved, and nothing is moved while no rule has a detail layer."""
    try:
        ids = read_declared(root / DECL_REL).ids
        working = _working_layers(root)
        commit, label = resolve_base(root, base)
        base_cores = _base_layers(root, commit, label)
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    failed = 0
    details = [(did, " ".join(d.split())) for did, (_core, d) in sorted(working.items()) if d is not None]
    for rid in ids:
        if rid not in working:
            continue  # run() reports a listed id with no measured span (exit 2)
        core, detail = working[rid]
        if detail is not None and not core.strip():
            print("must-deliver: FAIL: rule {} has an empty core layer, so all its text lies in its detail "
                  "layer, after every rule's core".format(rid))
            failed = 1
        moved = moved_units(base_cores.get(rid, ""), core, [d for _did, d in details])
        if moved:
            holder = next(did for did, d in details if moved[0] in d)
            print("must-deliver: FAIL: rule {} moves {} bytes of its core layer at the base {} into a detail "
                  "layer ({} piece(s), the first, in the detail layer of {}: {!r})".format(
                      rid, sum(len(u.encode("utf-8")) for u in moved), label, len(moved), holder,
                      moved[0][:FIRST_PIECE_CHARS]))
            failed = 1
    print("must-deliver: core-move check against base {}: {} listed rule(s), {} split in the working tree; "
          "{}".format(label, len(ids), sum(1 for rid in ids if rid in working and working[rid][1] is not None),
                      "a core layer shrank into a detail layer" if failed else "no core text moved into a "
                      "detail layer and no listed core layer is empty"))
    return failed


def gate(root, base):
    """The whole gate: the prefix check (run), the list ratchet and the core-move check (core_moves), all
    always run so every finding is printed. The worse exit wins: 2 over 1 over 0."""
    return max(run(root), ratchet(root, base), core_moves(root, base))


def run(root):
    """Check the tree at root. Exit 0 when every listed rule's whole span (its whole core layer) ends at least
    reserve-bytes inside the cap, 1 when a listed rule's core layer ends past it or fewer than reserve-bytes
    inside it, 2 on an unreadable, malformed or ambiguous input. Parameterized on root so
    the self-test drives it against synthetic trees, never the real repo."""
    try:
        declared = read_declared(root / DECL_REL)
        ids, warn, reserve = declared.ids, declared.warn, declared.reserve
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
    print("must-deliver: reserve-bytes {}: every listed rule must end at least that many bytes inside the "
          "cap".format(reserve))
    under = not over and tightest_margin < reserve
    if under:
        print("must-deliver: FAIL: tightest margin {} ({}) is below reserve-bytes {}".format(
            tightest_margin, tightest_rid, reserve))
    elif not over and tightest_margin < warn:
        print("WARNING: tightest margin {} is below warn-margin-bytes {}".format(tightest_margin, warn))
    return 1 if over or under else 0


def main():
    """The self-test runs only when the arguments are exactly --self-test; every other shape goes through
    the strict parser below, so --self-test mixed with any other argument is exit 2, never a self-test
    run that exits 0 without running the gate."""
    args = sys.argv[1:]
    if args == ["--self-test"]:
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
# The vectors run the gate against synthetic temp trees, built with gen_first_pin_demo's fixture helpers
# (_write_tree, _tuned) against the real CAP_BYTES, never against the real tree. Three vectors read files
# of the real tree, without running the gate on it: T49 reads the real gates manifest residue and the real
# list's whole text, T64 reads that same residue, and T63 reads the real list's reserve-bytes. Each vector is a discriminating fixture: it fails
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
#       well-formed and is refused by measure's corpus membership check (_must_deliver.listed_ids, read
#       before the sort, since the list sets the AGENTS.md order); run's missing-span check stays as
#       defence in depth, and T57 tests it on its own. prjint1.1 is refused earlier, by read_declared's
#       corpus-id shape check (CID_RE); with that check removed the membership check still refuses it, so
#       this half fails only when both checks are removed;
#   T8  an absent list (the fixture written with write_decl=False, its absence checked before the leg),
#       rule = [], and a misspelt [[rules]] table are each exit 2 (a mutant treating an absent or empty
#       list as "nothing to check" returns 0);
#   T9  an unknown row key, an integer id, an empty reason, and a duplicate id are each exit 2 (a mutant
#       ignoring unknown keys or deduplicating silently passes). The top-level values: format-version
#       2.0, 0, 1 (with and without a reserve line: a version 1 working file is refused), 3 and "2", and
#       warn-margin-bytes 0 and -1, are each exit 2 naming the key, while warn-margin-bytes 1 passes (a
#       mutant comparing format-version by value alone passes 2.0, one coercing it with int() passes "2"
#       and 2.0, one without the value check passes 0, 1 and 3, one reading the working file with the base
#       parser passes 1, one without the lower bound on warn-margin-bytes passes 0 and -1, and one testing
#       warn < 2 fails 1). T59, in the same loop: reserve-bytes true, -1, 1.0 and "0", the key missing,
#       and 513 under warn-margin-bytes 512 are each exit 2 naming the reserve (a mutant without the type
#       check passes true and 1.0, one without the lower bound -1, one defaulting a missing reserve to 0
#       passes the missing key, one without the warn bound passes 513).
#       An id written "prjint1\n" is exit 2 naming it not a well-formed corpus-id (a mutant testing
#       CID_RE with match, whose $ accepts one trailing newline, reaches the missing-span check instead);
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
#   T13b a listed rule using the `## Detail` split is measured on its core layer: its core ending 10 bytes
#       inside the cap with a 5000-byte detail layer is exit 0 with margin 10 (a mutant of
#       gen_agents.bodies_of_pairs rendering the detail at the rule's own place does not pass so), and its
#       core straddling the cap is exit 1 naming it.
# The list ratchet (T14 to T38) runs gate() against temporary git repositories built with the same
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
#       looking a full object id up under refs/remotes/ reads HEAD's list and passes; a rule reading the
#       id as that commit and ignoring the planted ref is exit 1 and fails too);
#   T28 refs/remotes/origin/main (and origin/HEAD) is the base listing ruleaa under a configured remote
#       origin, and HEAD drops it: --base origin/main~0 is exit 1 unshadowed; with a tag origin at HEAD,
#       --base origin and origin~0, outside the grammar, are exit 2 naming the accepted forms (a mutant
#       without the grammar refuses them only by the bare-remote check, without that message, and fails);
#       with a branch and then also a tag origin/main at HEAD,
#       origin/main~0 and origin/main^0 are exit 2 naming the shadow (a mutant without the shadow
#       refusal lets the branch or tag stand in and passes);
#   T29 the configured-remote fallback: origin is configured, refs/remotes/origin/gone is absent and a tag
#       origin/gone points at a commit that drops ruleaa: --base origin/gone is exit 2 naming the absent
#       remote-tracking ref (a mutant without the fallback resolves the tag and passes);
#   T30 a full-length id whose object does not exist: exit 2 unplanted; with refs/remotes/<that id>
#       planted at HEAD, which drops ruleaa, exit 2 naming it ambiguous (a mutant treating a name with a
#       ref under refs/remotes/ as remote-tracking reads HEAD's list and passes);
#   T31 HEAD lists ruleaa and the working list drops it: --base HEAD is exit 1; with refs/remotes/HEAD
#       planted at a commit that drops ruleaa, --base HEAD and HEAD~0 are each exit 2 naming it ambiguous
#       (the same mutant reads the planted commit and passes the first; one checking the whole value but
#       not the name leading a suffix run reads HEAD~0 and exits 1);
#   T32 an abbreviated id of the base, with HEAD dropping ruleaa, is outside the grammar: unplanted, and
#       with refs/remotes/<abbreviation> planted at HEAD, the abbreviation and <abbreviation>~0 are each
#       exit 2 naming the accepted forms (a mutant without the grammar reads the unplanted abbreviation
#       as git does and exits 1, and refuses the planted one only as ambiguous);
#   T33 the base's full id, with HEAD dropping ruleaa: with refs/remotes/<id>/HEAD planted at HEAD, <id>,
#       <id>~0 and <id>^0 are each exit 2 naming that ref, and with it a symbolic ref to a branch at HEAD,
#       <id> and <id>~0 are each exit 2 naming it (git reads a full id as the object, so these are the
#       fail-closed over-refusals the docstring discloses; a mutant without _resolved_ref_refusal exits 1
#       on every case; one refusing only a ref git names under refs/remotes/, without the symbolic-ref
#       check, exits 1 on the symbolic cases);
#   T34 origin is configured, refs/remotes/origin/absent is absent and refs/remotes/origin/absent/HEAD is
#       planted at HEAD, which drops ruleaa: origin/absent~0 and origin/absent^0 are each exit 2 naming
#       the absent remote-tracking ref (a mutant without the leading-ref existence check lets git take
#       the planted /HEAD and passes);
#   T35 a grafts file gives the base (refs/remotes/origin/main) and HEAD, which drops ruleaa, one
#       parentless commit that drops ruleaa as their parent: with no --base and with --base HEAD^, the
#       base is still read as committed and the drop is exit 1 (a mutant that leaves grafts on reads
#       the grafted commit as the merge base or HEAD^ and passes);
#   T36 a '{' in the leading name: with refs/remotes/<name>/HEAD planted at HEAD, which
#       drops ruleaa, a{b~0 and x{-0-g<abbreviation>~0 are each exit 2 naming the accepted forms (a mutant
#       without the grammar lets the brace hide the ~ from the leading-name checks, so git takes the
#       planted ref and the gate passes);
#   T37 a remote-tracking name git check-ref-format rejects is refused: origin is configured, and
#       with refs/remotes/origin/x./HEAD planted at HEAD, which drops ruleaa, origin/x. and origin/x.~0,
#       and with refs/remotes/refs/remotes/x./HEAD planted, refs/remotes/x. and refs/remotes/x.~0, are
#       each exit 2 naming the check-ref-format refusal (a mutant without the grammar skips both the
#       exact read and the resolved-ref check, so git takes the planted ref and the gate passes);
#   T38 a sample of forms outside the grammar, each of which git resolves to the base, which lists
#       ruleaa while HEAD drops it: an abbreviated id, the describe form x-0-g<abbreviation> and
#       <id>^{commit} are each exit 2 naming the accepted forms (a mutant without the grammar reads the
#       base and exits 1), while the accepted HEAD^, HEAD~ (N empty) and <id>~0^0 read the base and the
#       drop is exit 1 (a grammar refusing an empty N or a mixed suffix run fails);
#   T39 origin is configured, refs/remotes/origin/main is a child of the base and both
#       list ruleaa, HEAD drops it, and a plain file <git dir>/origin/main (a pseudo-ref, the first rule
#       of git's short-name search) holds a child of HEAD that also drops it: origin/main~0, ^0, ~, ^,
#       ~1, ^1 and ~0^0 each read refs/remotes/origin/main and the drop is exit 1 (a mutant handing git
#       the short name origin/main<suffixes> reads the planted commit and passes);
#   T40 a forged commit-graph file (generation v1, checksum recomputed) gives HEAD~1 a
#       decoy parent that drops ruleaa, a child of the base that refs/remotes/origin/main points at; plain
#       git then reads the decoy as HEAD~2 and as the merge base: with no --base and with --base HEAD~2
#       the base is still read as committed and the drop is exit 1 (a mutant without
#       core.commitGraph=false reads the decoy and passes);
#   T41 a remote named HEAD is configured, HEAD lists ruleaa and the working list drops
#       it: --base HEAD and HEAD~0 read HEAD and the drop is exit 1 (a mutant judging the bare-remote
#       name before HEAD refuses both, exit 2);
#   T42 main() is driven with self_test_main and gate replaced by recorders: --self-test
#       mixed with --base HEAD, --base=HEAD, a second --self-test or any other argument, in either order,
#       is exit 2 naming the unknown arguments with nothing dispatched, --base --self-test reaches the
#       gate with the value --self-test, and the real gate refuses that value as not a ref (exit 2), while
#       exactly --self-test runs the self-test and no argument, --base HEAD and --base=HEAD run the gate (a
#       mutant that runs the self-test when --self-test appears anywhere dispatches it and exits 0).
# The core-move check (T43 to T54) runs against committed bases built the same way:
#   T43 a sentence of listed ruleaa's core at the base moved into its detail layer: exit 1 naming the rule
#       and the 38 moved bytes (a mutant without core_moves in gate, or one comparing with the working
#       core instead of the base core, exits 0);
#   T44 ruleaa's whole core moved into its detail layer: core_moves is exit 1 naming the empty core and the
#       move, and the whole gate is not exit 0; an empty listed core whose base was already empty is exit
#       1 on the empty core alone (a mutant without the empty-core test passes it);
#   T45 a detail sentence the base never held, a core sentence dropped and held nowhere, the T43 move
#       in an unlisted rule, a dropped core sentence sharing only a piece one byte under MIN_PIECE_BYTES
#       with a detail layer, a detail piece the working core still holds as often as the base core, a
#       move that crosses a sentence end with each side under MIN_PIECE_BYTES, and a dropped sentence
#       whose text up to a dot inside a word (v1.2) a detail layer holds are each exit 0 with the
#       core-move summary (a mutant judging every rule fails the third, one with a smaller piece floor the
#       fourth, one not counting the piece in the working core the fifth, one whose pieces cross sentence
#       ends the sixth, and one cutting at a dot that no space follows the seventh);
#   T46 a base rule source that is not UTF-8 is exit 2 (a mutant skipping it exits 0);
#   T47 part of a sentence moved word for word into ruleaa's detail layer (its trailing comma left
#       behind), the T43 sentence moved into an unlisted rule's detail layer with ruleaa split and with it
#       unsplit, and one copy of a sentence the base core holds twice moved into ruleaa's detail layer are
#       each exit 1 naming the moved bytes and the rule whose detail layer holds them (a mutant matching
#       whole sentences only, reading only ruleaa's own detail layer, skipping an unsplit listed rule, or
#       testing presence instead of the count exits 0 on its case; one matching whole words only names
#       24 of the 30 bytes moved);
#   T48 a bracketed clause moved without its brackets, two short sentences ended by ? and ! moved whole,
#       a piece that passes the count only at an earlier word end, and a dropped sentence a base detail
#       layer already held (the disclosed over-refusal) are each exit 1 naming the moved bytes, the rule
#       whose detail layer holds them and the first moved piece (a mutant starting pieces only at word
#       starts exits 0 on the first, one not cutting after ? or after ! names the wrong bytes on the
#       second and one cutting only after . exits 0 on it, one trying only the detail copy's end names
#       30 of the 60 bytes on the third; the fourth pins the over-refusal the residue discloses); every
#       T47 row also names its first piece;
#   T49 the module docstring, the gate's manifest residue and the whole text of the declared file (its
#       comments and its data rows alike, so a value written in a reason string turns T49 red and a name
#       written there satisfies it) name MIN_PIECE_BYTES and CAP_BYTES (the first two also
#       FIRST_PIECE_CHARS) and never write a constant's value as a number (a copy that would go stale when
#       the constant changes); a gates manifest tomllib runs out of recursion depth or memory on, or whose
#       gate entries or must-deliver residue have the wrong type, is cannot-evaluate (exit 2), never a T49
#       failure or a traceback;
#   T50 a deeply nested gates manifest is that cannot-evaluate, naming RecursionError (a mutant whose
#       handler drops RecursionError raises it);
#   T51 the statements corrected for a crossing move's whole-sentence side, the dropped-sentence
#       over-refusal inside a longer word, "at least as often" and the printed piece's FIRST_PIECE_CHARS
#       cut are written in the docstring and the residue as pinned (the text, not its truth);
#   T52 a crossing move whose left side is a short whole sentence (Yes!) and a dropped short sentence
#       (Stop.) a detail layer holds inside a longer word (Non-Stop.) are each exit 1 naming the bytes,
#       the holder and the first piece;
#   T53 a moved sentence longer than FIRST_PIECE_CHARS characters is exit 1 printing exactly its first
#       FIRST_PIECE_CHARS characters (a mutant printing the whole piece fails it), and a sentence the
#       working core still holds as often as the base core, also held whole by a detail layer, is exit 0
#       (a mutant skipping a sentence only when the working core holds it more often refuses it);
#   T54 the cannot-evaluate path end to end: a deeply nested gates manifest read from a tree is that
#       cannot-evaluate and the self-test's verdict on it is exit 2 (a mutant whose verdict ignores it
#       returns 0 there); tomllib raising MemoryError (an injected text whose replace raises it) is
#       cannot-evaluate naming MemoryError (a mutant whose handler drops MemoryError raises it); and a
#       gate that is a table, a gate that is an integer and a residue that is not a string are each
#       cannot-evaluate, never a T49 failure or a traceback.
# The list's order and its one reader (T55 to T57):
#   T55 the listed rules come first: an unlisted rule sorting between two listed ones in AIQT priority
#       order, padded so that in that order the last listed rule would end past the cap, is exit 0 with
#       that rule's measured margin (a mutant composing in priority order alone exits 1);
#   T56 the injected PermissionError of T10 is made at _must_deliver._read_decl_bytes, the list's one read,
#       and the vector asserts it was reached (an injection left on this module never fires, and T10's
#       leg then reads the list and exits 0);
#   T57 a stand-in measure returning the real spans without ruleaa's is exit 2 naming the list's missing
#       span (a mutant without run's missing-span check raises a KeyError outside its try).
# The reserve and the format-version 2 migration (T58 to T63; T59 is in T9's loop, and T62, restoring a
# rule while keeping its [[removal]] row, is T16's "also in [[rule]]" row):
#   T58 with reserve-bytes 512, a listed rule ending exactly 512 bytes inside the cap is exit 0 reporting
#       the reserve, and one ending 511 bytes inside it is exit 1 naming the margin, the rule and the
#       reserve; under reserve-bytes 0 the same 511-byte margin is exit 0 with the warning (a warning-only
#       gate exits 0 at 511, and one testing margin <= reserve fails at 512);
#   T60 against a base written in format-version 1, an unchanged working list is exit 0, a drop with no
#       [[removal]] row exit 1 naming it and the drop with a row exit 0 printing it (a single strict
#       parser reading the base as the working file is exit 2 on all three);
#   T61 a base in format-version 3 or 1.0, a version 1 base carrying reserve-bytes, a version 2 base
#       without it, and a version 2 base whose reserve-bytes is false, -1 or 1.0, are each exit 2 naming
#       the defect at the base (a lenient base parser accepts them);
#   T63 the real list's reserve-bytes is 512, the reserve the maintainer set, so the gate run on this
#       tree enforces it (a list whose reserve was lowered or reset to 0 fails this row);
#   T64 the module docstring and the manifest residue state the enforced reserve, the base parser's two
#       accepted schemas and the reserve ratchet with its residual as pinned (the text, not its truth);
#   T65 the reserve ratchet: against a version 2 base with reserve-bytes 9 (the tightest fixture margin is
#       10, so a working reserve up to 10 passes the prefix check), a working reserve of 8 is exit 1 naming
#       both reserves, alone and together with an undeclared drop, while 9 (kept) and 10 (raised) are exit
#       0; against a version 1 base a working reserve of 10 is exit 0, the base read as reserve 0 (a
#       ratchet that never compares the reserve exits 0 on the lowering, one testing <= fails the kept
#       reserve, one testing != fails the raise, and one reading a version 1 base as a reserve above 10
#       fails the last row).

import gen_first_pin_demo as gfpd  # noqa: E402  fixture helpers (_write_tree, _tuned) and CAP_BYTES


class _FixtureError(Exception):
    """A self-test fixture's own git command failed: the vector cannot run (exit 2, never a pass)."""


def _decl(root, rows, warn=512, raw=None, reserve=0):
    """Write a format-version 2 list of rows (reserve-bytes 0 by default, so a vector judges its margin
    against the cap alone unless it passes reserve=), or raw as given."""
    path = root / Path(DECL_REL)
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        lines = ["format-version = 2", "warn-margin-bytes = {}".format(warn), "reserve-bytes = {}".format(reserve)]
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
    import hashlib
    import io
    import shutil
    import struct
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

    def raw_decl(rules, removals=(), extra="", version=2, reserve=0):
        """A list's text in format-version 2 (reserve-bytes = reserve), or with version=1 the exact version 1
        schema, which has no reserve-bytes line, so a comparison base can be written as version 1 on purpose."""
        lines = ["format-version = {}".format(version), "warn-margin-bytes = 512"]
        if version != 1:
            lines.append("reserve-bytes = {}".format(reserve))
        for rid in rules:
            lines += ["", "[[rule]]", 'id = "{}"'.format(rid), 'reason = "fixture rule"']
        for rid, reason in removals:
            lines += ["", "[[removal]]", 'id = "{}"'.format(rid), 'reason = "{}"'.format(reason)]
        return "\n".join(lines) + "\n" + extra

    failures = []
    skipped = []
    fixture_error = None
    t49_error = None
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
        gfpd._tuned(t8, base_specs, "ruleaa", CAP - 10, write_decl=False)
        if os.path.lexists(t8 / DECL_REL):
            raise _FixtureError("T8 precondition: a list exists before the absent-list leg")
        code, out = capture(t8)  # no must-deliver.toml written
        if code != 2 or "does not exist" not in out:
            failures.append("T8: an absent list must be exit 2, got {}\n{}".format(code, out))
        # The format-version 2 header the raw literals below share, so each tests only the defect it names.
        v2 = "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = 0\n"
        _decl(t8, [], raw=v2 + "rule = []\n")
        code, out = capture(t8)
        if code != 2 or "non-empty" not in out:
            failures.append("T8: an empty rule array must be exit 2, got {}\n{}".format(code, out))
        _decl(t8, [], raw=v2 + '[[rules]]\nid = "prjint1"\nreason = "x"\n')
        code, out = capture(t8)
        if code != 2:
            failures.append("T8: a misspelt [[rules]] table must be exit 2, got {}\n{}".format(code, out))

        # T9. Unknown row key, integer id, empty reason, duplicate id, and out-of-schema top-level values:
        # each exit 2; warn-margin-bytes 1, the smallest accepted value, passes.
        t9 = tmp / "t9"
        gfpd._tuned(t9, base_specs, "ruleaa", CAP - 10)
        bad_rows = (
            ("unknown key", v2 + '[[rule]]\nid = "prjint1"\nreason = "x"\nextra = 1\n'),
            ("integer id", v2 + '[[rule]]\nid = 1\nreason = "x"\n'),
            ("empty reason", v2 + '[[rule]]\nid = "prjint1"\nreason = " "\n'),
            ("duplicate id", v2 + '[[rule]]\nid = "prjint1"\nreason = "x"\n[[rule]]\nid = "prjint1"\nreason = "y"\n'),
            ("unknown top-level key", v2 + 'cap = 1\n[[rule]]\nid = "prjint1"\nreason = "x"\n'),
            ("boolean format-version", 'format-version = true\nwarn-margin-bytes = 512\nreserve-bytes = 0\n'
                                       '[[rule]]\nid = "prjint1"\nreason = "x"\n'),
            ("boolean warn margin", 'format-version = 2\nwarn-margin-bytes = true\nreserve-bytes = 0\n'
                                    '[[rule]]\nid = "prjint1"\nreason = "x"\n'),
        )
        for label, raw in bad_rows:
            _decl(t9, [], raw=raw)
            code, out = capture(t9)
            if code != 2:
                failures.append("T9 {}: expected exit 2, got {}\n{}".format(label, code, out))
        version_msg = "format-version must be the integer 2"
        warn_msg = "warn-margin-bytes must be a positive integer"
        reserve_msg = "reserve-bytes must be an integer of at least 0"
        keys_msg = "(format-version 2) must carry exactly the keys"
        value_rows = (
            ("float format-version 2.0", "format-version = 2.0\nwarn-margin-bytes = 512\nreserve-bytes = 0\n",
             version_msg),
            ("format-version 0", "format-version = 0\nwarn-margin-bytes = 512\nreserve-bytes = 0\n", version_msg),
            # A version 1 working file is refused, with or without a reserve line: only a base may be version 1.
            ("format-version 1", "format-version = 1\nwarn-margin-bytes = 512\n", version_msg),
            ("format-version 1 with a reserve", "format-version = 1\nwarn-margin-bytes = 512\nreserve-bytes = 0\n",
             version_msg),
            ("format-version 3", "format-version = 3\nwarn-margin-bytes = 512\nreserve-bytes = 0\n", version_msg),
            ("string format-version", 'format-version = "2"\nwarn-margin-bytes = 512\nreserve-bytes = 0\n',
             version_msg),
            ("warn margin 0", "format-version = 2\nwarn-margin-bytes = 0\nreserve-bytes = 0\n", warn_msg),
            ("warn margin -1", "format-version = 2\nwarn-margin-bytes = -1\nreserve-bytes = 0\n", warn_msg),
            # T59. A reserve outside its schema fails closed (exit 2 naming the key), never read as 0 or as a
            # number it is not: a bool, a negative, a float, a string, a missing key, and a reserve above
            # warn-margin-bytes.
            ("reserve boolean", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = true\n", reserve_msg),
            ("reserve -1", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = -1\n", reserve_msg),
            ("reserve float 1.0", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = 1.0\n", reserve_msg),
            ("reserve string", 'format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = "0"\n', reserve_msg),
            ("reserve missing", "format-version = 2\nwarn-margin-bytes = 512\n", keys_msg),
            ("reserve above the warn margin", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = 513\n",
             "reserve-bytes 513 is above warn-margin-bytes 512"),
        )
        for label, head, want in value_rows:
            _decl(t9, [], raw=head + '[[rule]]\nid = "prjint1"\nreason = "x"\n')
            code, out = capture(t9)
            if code != 2 or want not in out:
                failures.append("T9 {}: expected exit 2 with {!r}, got {}\n{}".format(label, want, code, out))
        # An id with one trailing newline (the TOML escape \n) fails the corpus-id shape check itself.
        _decl(t9, [], raw=v2 + '[[rule]]\nid = "prjint1\\n"\nreason = "x"\n')
        code, out = capture(t9)
        if code != 2 or "not a well-formed corpus-id" not in out:
            failures.append("T9 id with a trailing newline: expected exit 2 naming it not a well-formed "
                            "corpus-id, got {}\n{}".format(code, out))
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
        # The injected PermissionError at the declaration read runs on every runner. T56: the read is
        # _must_deliver._read_decl_bytes (the list's one reader moved there), so the injection goes there
        # and the vector asserts it was reached; an injection left on this module would never fire.
        real_read = _must_deliver._read_decl_bytes
        denied_calls = []

        def denied(path):
            denied_calls.append(path)
            raise PermissionError(13, "Permission denied (injected by the self-test)", str(path))

        _must_deliver._read_decl_bytes = denied
        try:
            code, out = capture(t10)
        finally:
            _must_deliver._read_decl_bytes = real_read
        if code != 2 or "is unreadable" not in out or "injected by the self-test" not in out:
            failures.append("T10: an injected PermissionError at the declaration read must be exit 2 "
                            "reporting the list unreadable, got {}\n{}".format(code, out))
        if not denied_calls:
            failures.append("T56: the injected read at _must_deliver._read_decl_bytes was never reached")
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

        # T13b. A listed split rule is measured on its core layer: ruleaa's core ends CAP - 10 and its 5000-byte
        #       detail layer goes after the last rule, so it is exit 0 with margin 10; the same tree built with
        #       gen_agents.bodies_of_pairs replaced by a mutant rendering the detail at the rule's own place is
        #       not exit 0 with margin 10. A listed split rule whose core straddles the cap is exit 1.
        t13b_detail = "Detail text " + "d" * 5000 + "."
        t13b_specs = [("prjint1", True, 0), ("ruleaa", False, 20000, t13b_detail), ("rulebb", False, 400)]
        t13b = tmp / "t13b"
        sp = gfpd._tuned(t13b, t13b_specs, "ruleaa", CAP - 10)
        _decl(t13b, both)
        code, out = capture(t13b)
        if code != 0 or "ruleaa  end {:6d}  margin 10".format(CAP - 10) not in out or sp["ruleaa"][1] != CAP - 10:
            failures.append("T13b: a listed split rule whose core ends 10 bytes inside the cap must be exit 0 with "
                            "margin 10 (fixture end {}), got {}\n{}".format(sp["ruleaa"][1], code, out))

        def t13b_detail_in_place(pairs):
            out = []
            for src, _fm in pairs:
                detail = gfpd.gen_agents.detail_body_of(src)
                core = gfpd.gen_agents.core_body_of(src)
                out.append(core if detail is None else core + "\n\n" + detail)
            return out

        real_bodies = gfpd.gen_agents.bodies_of_pairs
        gfpd.gen_agents.bodies_of_pairs = t13b_detail_in_place
        try:
            t13b_mutant = tmp / "t13b-mutant"
            gfpd._tuned(t13b_mutant, t13b_specs, "ruleaa", CAP - 10)
            _decl(t13b_mutant, both)
            code, out = capture(t13b_mutant)
        except Exception as exc:  # noqa: BLE001  a mutant may surface as any exception
            code, out = "raised {!r}".format(exc), ""
        finally:
            gfpd.gen_agents.bodies_of_pairs = real_bodies
        if code == 0 and "margin 10" in out:
            failures.append("T13b: with the detail rendered at the rule's own place the gate still passes with "
                            "margin 10, so the vector does not judge the detail placement:\n{}".format(out))
        t13b_over = tmp / "t13b-over"
        sp = gfpd._tuned(t13b_over, t13b_specs, "ruleaa", CAP + 5)
        _decl(t13b_over, both)
        code, out = capture(t13b_over)
        if sp["ruleaa"][1] != CAP + 5 or code != 1 or "FAIL: rule ruleaa ends at {}".format(CAP + 5) not in out:
            failures.append("T13b: a listed split rule whose core straddles the cap must be exit 1 naming it "
                            "(fixture end {}), got {}\n{}".format(sp["ruleaa"][1], code, out))

        # The list ratchet. Each fixture commits a base listing prjint1 and ruleaa (ruleaa CAP - 10),
        # then edits the working list; the base is passed as its full object id unless stated.
        drop_msg = "FAIL: rule ruleaa is listed at the base"

        def ratchet_fixture(name, base_rules=("prjint1", "ruleaa"), base_version=2):
            root = tmp / name
            gfpd._tuned(root, base_specs, "ruleaa", CAP - 10)
            _decl(root, [], raw=raw_decl(base_rules, version=base_version))
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
        gfpd._tuned(t18, base_specs, "ruleaa", CAP - 10, write_decl=False)
        if os.path.lexists(t18 / DECL_REL):
            raise _FixtureError("T18 precondition: a list exists before the base commit")
        base18 = commit_base(t18)  # no must-deliver.toml at the base
        if fixture_git(t18, "ls-tree", "--name-only", base18, "--", DECL_REL):
            raise _FixtureError("T18 precondition: the base commit carries a list")
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
        outside = "it is not one of the accepted --base forms"

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

        # T28. A bare remote name (outside the grammar), and a revision expression whose leading
        # remote-tracking name is shadowed by a branch or tag of the same short name, are refused (exit 2).
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
            if code != 2 or outside not in out:
                failures.append("T28: with a tag origin, --base {!r} (a bare remote name) must be exit 2 "
                                "naming the accepted forms, got {}\n{}".format(base, code, out))
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
        for base in ("HEAD", "HEAD~0"):
            code, out = capture_gate(t31, base)
            if code != 2 or ambiguous not in out or REMOTE_REF_PREFIX + "HEAD" not in out:
                failures.append("T31: with refs/remotes/HEAD planted, --base {!r} must be exit 2 naming it "
                                "ambiguous, got {}\n{}".format(base, code, out))

        # T32. An abbreviated id is outside the grammar: unplanted, and with refs/remotes/<abbreviation>
        # planted at HEAD (which drops ruleaa), the abbreviation, alone or leading a suffix run, is refused
        # naming the accepted forms, never read.
        t32, base32 = ratchet_fixture("t32")
        head32 = drop_at_head(t32)
        abbrev32 = base32[:12]
        for planted in (False, True):
            if planted:
                fixture_git(t32, "update-ref", REMOTE_REF_PREFIX + abbrev32, head32)
            for base in (abbrev32, abbrev32 + "~0"):
                code, out = capture_gate(t32, base)
                if code != 2 or outside not in out:
                    failures.append("T32: {}, --base {!r} (an abbreviated id) must be exit 2 naming the "
                                    "accepted forms, got {}\n{}".format(
                                        "with refs/remotes/<abbreviated id> planted" if planted
                                        else "unplanted", base, code, out))

        # T33. refs/remotes/<id>/HEAD, plain or symbolic, refuses the full id: git is asked which ref it
        # takes, and one under refs/remotes/ is refused (git reads a full id as the object, so this is the
        # disclosed fail-closed over-refusal).
        through = "through a ref under refs/remotes/"
        t33, base33 = ratchet_fixture("t33")
        head33 = drop_at_head(t33)
        plant33 = REMOTE_REF_PREFIX + base33 + "/HEAD"
        fixture_git(t33, "update-ref", plant33, head33)
        for base in (base33, base33 + "~0", base33 + "^0"):
            code, out = capture_gate(t33, base)
            if code != 2 or through not in out or plant33 not in out:
                failures.append("T33: with {} planted, --base {!r} must be exit 2 naming it, got {}\n{}".format(
                    plant33, base, code, out))
        fixture_git(t33, "update-ref", "-d", plant33)
        fixture_git(t33, "update-ref", "refs/heads/qa-drop", head33)
        fixture_git(t33, "symbolic-ref", plant33, "refs/heads/qa-drop")
        for base in (base33, base33 + "~0"):
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

        # T36. A '{' in the leading name is outside the grammar, so a planted
        # refs/remotes/<name>/HEAD cannot stand in for the base.
        t36, base36 = ratchet_fixture("t36")
        head36 = drop_at_head(t36)
        for name in ("a{b", "x{-0-g" + base36[:12]):
            plant36 = REMOTE_REF_PREFIX + name + "/HEAD"
            fixture_git(t36, "update-ref", plant36, head36)
            code, out = capture_gate(t36, name + "~0")
            if code != 2 or outside not in out:
                failures.append("T36: with {} planted, --base {!r} must be exit 2 naming the accepted forms, "
                                "got {}\n{}".format(plant36, name + "~0", code, out))
            fixture_git(t36, "update-ref", "-d", plant36)

        # T37. A name written as remote-tracking whose ref git check-ref-format rejects is
        # outside the grammar, so a planted refs/remotes/<name>/HEAD cannot stand in for the base.
        t37, _base37 = ratchet_fixture("t37")
        fixture_git(t37, "remote", "add", "origin", str(tmp / "t37-nowhere"))
        head37 = drop_at_head(t37)
        for name, plant37 in (("origin/x.", "refs/remotes/origin/x./HEAD"),
                              ("refs/remotes/x.", "refs/remotes/refs/remotes/x./HEAD")):
            fixture_git(t37, "update-ref", plant37, head37)
            for base in (name, name + "~0"):
                code, out = capture_gate(t37, base)
                if code != 2 or outside not in out or "git check-ref-format does not accept" not in out:
                    failures.append("T37: with {} planted, --base {!r} must be exit 2 naming the accepted forms "
                                    "and the check-ref-format refusal, got {}\n{}".format(plant37, base, code, out))
            fixture_git(t37, "update-ref", "-d", plant37)

        # T38. A sample of forms outside the grammar that git resolves to the base (an abbreviated id, a
        # describe form, a brace) is refused; accepted suffix runs still read the base.
        t38, base38 = ratchet_fixture("t38")
        drop_at_head(t38)
        for base in (base38[:12], "x-0-g" + base38[:12], base38 + "^{commit}"):
            code, out = capture_gate(t38, base)
            if code != 2 or outside not in out:
                failures.append("T38: --base {!r} must be exit 2 naming the accepted forms, got {}\n{}".format(
                    base, code, out))
        for base in ("HEAD^", "HEAD~", base38 + "~0^0"):
            code, out = capture_gate(t38, base)
            if code != 1 or drop_msg not in out or base38[:12] not in out:
                failures.append("T38: --base {!r}, inside the grammar, must read the base and the drop be "
                                "exit 1, got {}\n{}".format(base, code, out))

        # T39. A pseudo-ref file <git dir>/origin/main, which git's short-name search
        # tries first, cannot stand in for refs/remotes/origin/main leading a suffix run: git is handed
        # the object id the remote-tracking ref holds, never the short name.
        t39, base39 = ratchet_fixture("t39")
        fixture_git(t39, "remote", "add", "origin", str(tmp / "t39-nowhere"))
        fixture_git(t39, "commit", "-q", "--allow-empty", "-m", "fixture origin/main, a child of the base")
        main39 = fixture_git(t39, "rev-parse", "HEAD")
        fixture_git(t39, "update-ref", "refs/remotes/origin/main", main39)
        drop_at_head(t39)
        fixture_git(t39, "commit", "-q", "--allow-empty", "-m", "fixture planted commit, also dropping ruleaa")
        plant39 = fixture_git(t39, "rev-parse", "HEAD")
        pseudo39 = Path(fixture_git(t39, "rev-parse", "--absolute-git-dir")) / "origin" / "main"
        pseudo39.parent.mkdir(parents=True, exist_ok=True)
        pseudo39.write_text(plant39 + "\n", encoding="ascii")
        if fixture_git(t39, "rev-parse", "origin/main~0") != plant39:
            failures.append("T39: the fixture pseudo-ref file did not stand in for origin/main in git")
        for suffix, want39 in (("~0", main39), ("^0", main39), ("~", base39), ("^", base39), ("~1", base39),
                               ("^1", base39), ("~0^0", main39)):
            code, out = capture_gate(t39, "origin/main" + suffix)
            if code != 1 or drop_msg not in out or "origin/main{} ({})".format(suffix, want39[:12]) not in out:
                failures.append("T39: with {} planted, --base {!r} must read refs/remotes/origin/main and the "
                                "drop be exit 1, got {}\n{}".format(pseudo39, "origin/main" + suffix, code, out))

        # T40. A forged commit-graph file cannot move the base: it gives HEAD~1 a decoy
        # parent (a child of the base that drops ruleaa, at refs/remotes/origin/main), which plain git
        # honours one commit below the tip, and the base is still read from the commit objects.
        def forge_graph_parent(graph, child, parent):
            """Rewrite child's first parent in the single commit-graph file graph to parent, recompute
            every generation (v1 topological level) and the trailing checksum. A graph outside that shape
            (a chain, a GDA2 chunk) is a _FixtureError."""
            data = bytearray(graph.read_bytes())
            sig, version, hash_version, chunk_count, base_count = struct.unpack(">4sBBBB", data[:8])
            if sig != b"CGPH" or version != 1 or base_count != 0 or hash_version not in (1, 2):
                raise _FixtureError("the fixture commit-graph file has an unexpected header")
            hlen = 20 if hash_version == 1 else 32
            chunks = dict(struct.unpack(">4sQ", data[8 + 12 * i:20 + 12 * i]) for i in range(chunk_count))
            if b"GDA2" in chunks or not {b"OIDF", b"OIDL", b"CDAT"} <= set(chunks):
                raise _FixtureError("the fixture commit-graph file is not a generation v1 graph")
            count = struct.unpack(">I", data[chunks[b"OIDF"] + 1020:chunks[b"OIDF"] + 1024])[0]
            oids = [bytes(data[chunks[b"OIDL"] + i * hlen:chunks[b"OIDL"] + (i + 1) * hlen]).hex()
                    for i in range(count)]
            if child not in oids or parent not in oids:
                raise _FixtureError("the fixture commit-graph file does not hold both commits")
            at = [chunks[b"CDAT"] + i * (hlen + 16) + hlen for i in range(count)]
            struct.pack_into(">I", data, at[oids.index(child)], oids.index(parent))
            levels = [0] * count
            changed = True
            while changed:
                changed = False
                for i in range(count):
                    parents = struct.unpack(">II", data[at[i]:at[i] + 8])
                    level = 1 + max([levels[p] for p in parents if p < 0x70000000] or [0])
                    if levels[i] != level:
                        levels[i], changed = level, True
            for i in range(count):
                low = struct.unpack(">I", data[at[i] + 8:at[i] + 12])[0] & 3
                struct.pack_into(">I", data, at[i] + 8, (levels[i] << 2) | low)
            data[-hlen:] = (hashlib.sha1 if hash_version == 1 else hashlib.sha256)(bytes(data[:-hlen])).digest()
            graph.chmod(0o644)
            graph.write_bytes(bytes(data))

        t40, base40 = ratchet_fixture("t40")
        decoy40 = drop_at_head(t40)
        fixture_git(t40, "update-ref", "refs/remotes/origin/main", decoy40)
        fixture_git(t40, "reset", "-q", "--hard", base40)
        fixture_git(t40, "commit", "-q", "--allow-empty", "-m", "fixture HEAD~1, listing ruleaa")
        mid40 = fixture_git(t40, "rev-parse", "HEAD")
        drop_at_head(t40)
        fixture_git(t40, "-c", "commitGraph.generationVersion=1", "commit-graph", "write", "--reachable")
        forge_graph_parent(Path(fixture_git(t40, "rev-parse", "--absolute-git-dir")) / "objects" / "info" /
                           "commit-graph", mid40, decoy40)
        if (fixture_git(t40, "rev-parse", "HEAD~2") != decoy40
                or fixture_git(t40, "merge-base", "HEAD", decoy40) != decoy40):
            failures.append("T40: the forged commit-graph file did not move git's HEAD~2 and merge base")
        for base, label in ((None, "(refs/remotes/origin/main, {})".format(base40[:12])),
                            ("HEAD~2", "HEAD~2 ({})".format(base40[:12]))):
            code, out = capture_gate(t40, base)
            if code != 1 or drop_msg not in out or label not in out:
                failures.append("T40: with a forged commit-graph file, base {!r} must be read as committed and "
                                "the drop be exit 1, got {}\n{}".format(base, code, out))

        # T41. A remote named HEAD does not turn --base HEAD into a bare remote name.
        t41, base41 = ratchet_fixture("t41")
        drop_at_head(t41)
        fixture_git(t41, "reset", "-q", "--soft", base41)  # HEAD lists ruleaa; the working list drops it
        fixture_git(t41, "remote", "add", "HEAD", str(tmp / "t41-nowhere"))
        for base in ("HEAD", "HEAD~0"):
            code, out = capture_gate(t41, base)
            if code != 1 or drop_msg not in out or "{} ({})".format(base, base41[:12]) not in out:
                failures.append("T41: with a remote named HEAD, --base {!r} must read HEAD and the drop be "
                                "exit 1, got {}\n{}".format(base, code, out))

        # T42. --self-test runs only when it is the whole command line: main() is driven
        # with self_test_main and gate replaced by recorders, so no nested self-test or real gate runs.
        calls42 = []
        saved42 = (globals()["self_test_main"], globals()["gate"], sys.argv)
        globals()["self_test_main"] = lambda: calls42.append(("self-test",)) or 0
        globals()["gate"] = lambda root, base: calls42.append(("gate", base)) or 0
        try:
            for argv42, want42, code42 in (
                    (["--base", "HEAD", "--self-test"], [], 2),
                    (["--self-test", "--base", "HEAD"], [], 2),
                    (["--base=HEAD", "--self-test"], [], 2),
                    (["--self-test", "--base=HEAD"], [], 2),
                    (["--self-test", "--self-test"], [], 2),
                    (["--self-test", "extra"], [], 2),
                    (["--base", "--self-test"], [("gate", "--self-test")], 0),
                    (["--self-test"], [("self-test",)], 0),
                    ([], [("gate", None)], 0),
                    (["--base", "HEAD"], [("gate", "HEAD")], 0),
                    (["--base=HEAD"], [("gate", "HEAD")], 0)):
                del calls42[:]
                sys.argv = ["check_must_deliver.py"] + argv42
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    code = main()
                out = buf.getvalue()
                if code != code42 or calls42 != want42 or (code42 == 2 and "unknown arguments" not in out):
                    failures.append("T42: arguments {!r} must be exit {} dispatching {!r}, got exit {} "
                                    "dispatching {!r}\n{}".format(argv42, code42, want42, code, calls42, out))
        finally:
            globals()["self_test_main"], globals()["gate"], sys.argv = saved42
        # --base --self-test reaches the real gate with the value --self-test, which is not a ref (exit 2).
        t42, _base42 = ratchet_fixture("t42")
        code, out = capture_gate(t42, "--self-test")
        if code != 2 or "--base '--self-test' is not a ref" not in out:
            failures.append("T42: --base --self-test must be exit 2 naming it not a ref, got {}\n{}".format(
                code, out))

        # The core-move check. Each fixture commits a base listing prjint1 and ruleaa, then rewrites
        # ruleaa in the working tree (AGENTS.md recomposed, so run() passes) and runs the whole gate.
        moved_sentence = "A sentence the base holds in its core."
        move_msg = "FAIL: rule ruleaa moves {} bytes of its core layer".format(len(moved_sentence))
        empty_msg = "FAIL: rule ruleaa has an empty core layer"

        def put_source(root, text):
            """Write text as ruleaa's source and recompose AGENTS.md from the corpus, as gen_agents does: in
            agents_key order under the list on disk (gfpd._write_tree always leaves one)."""
            rdir = root.joinpath(*gfpd.RULES_PARTS)
            (rdir / "ruleaa.md").write_text(text, encoding="utf-8")
            listed = read_declared(root / DECL_REL)[0]
            pairs = sorted(((src, fm) for src, fm, _ in gfpd.load_corpus(rdir)),
                           key=lambda pf: gfpd.gen_agents.agents_key(pf[1], listed))
            (root / "AGENTS.md").write_bytes(gfpd.adapter_compose.compose(
                "AGENTS.md", gfpd.gen_agents.HEADER, gfpd.gen_agents.bodies_of_pairs(pairs),
                gfpd.adapter_compose.load_registry(root)).data)

        def move_fixture(name, base_spec, head_spec=None, head_source=None, rules=("prjint1", "ruleaa"),
                         base_source=None, head_bb=("rulebb", False, 400)):
            root = tmp / name
            gfpd._write_tree(root, [("prjint1", True, 0), base_spec, ("rulebb", False, 400)])
            if base_source is not None:
                put_source(root, base_source)
            _decl(root, [], raw=raw_decl(rules))
            commit = commit_base(root)
            if head_spec is not None:
                gfpd._write_tree(root, [("prjint1", True, 0), head_spec, head_bb])
            if head_source is not None:
                put_source(root, head_source)
            return root, commit

        # T43. A sentence of ruleaa's core at the base moved into its detail layer: exit 1 naming the rule
        #      and the moved bytes (a mutant without core_moves, or one comparing with the working core
        #      instead of the base core, exits 0).
        t43, base43 = move_fixture("t43", ("ruleaa", False, ". " + moved_sentence[:-1]),
                                   head_spec=("ruleaa", False, "", moved_sentence))
        code, out = capture_gate(t43, base43)
        if code != 1 or move_msg not in out:
            failures.append("T43: a core sentence moved into the detail layer must be exit 1 naming the rule "
                            "and {} moved bytes, got {}\n{}".format(len(moved_sentence), code, out))

        # T44. ruleaa's whole core moved into its detail layer, an empty core: exit 1 on both findings;
        #      and an empty core with nothing moved (the base already empty) is exit 1 on the empty core
        #      alone (a mutant without the empty-core test passes it).
        empty_core = ("---\ncorpus-id: ruleaa\norigin: pack\nfamily: aiqt\ntier: 10\nfacet: ACCUR\n"
                      "slug: fixture-ruleaa\ndetail-trigger: writing a self-test fixture\n---\n\n# Rule ruleaa\n\n"
                      "## Detail\n\n" + moved_sentence + "\n")
        #      The whole gate is exit 2 on both trees as well, because run() cannot locate a title-only core
        #      body that recurs in its own detail heading; core_moves is asserted on its own exit 1.
        def capture_moves(root, base):
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(buf):
                code = core_moves(root, base)
            return code, buf.getvalue()

        t44, base44 = move_fixture("t44", ("ruleaa", False, ". " + moved_sentence[:-1]), head_source=empty_core)
        code, out = capture_moves(t44, base44)
        if code != 1 or empty_msg not in out or "FAIL: rule ruleaa moves" not in out:
            failures.append("T44: a core moved whole into the detail layer must be exit 1 naming the empty "
                            "core and the move, got {}\n{}".format(code, out))
        if capture_gate(t44, base44)[0] == 0:
            failures.append("T44: the whole gate must not pass a listed rule whose core moved whole")
        t44e, base44e = move_fixture("t44e", ("ruleaa", False, ""), base_source=empty_core)
        code, out = capture_moves(t44e, base44e)
        if code != 1 or empty_msg not in out or "FAIL: rule ruleaa moves" in out:
            failures.append("T44: an empty listed core with nothing moved must be exit 1 on the empty core "
                            "alone, got {}\n{}".format(code, out))

        # T45. Positive controls, each exit 0: a detail sentence the base never held (the vfxcmt shape
        #      against main); a sentence dropped from the core and held nowhere (content is not judged);
        #      the T43 move in a rule that is not listed (a mutant judging every rule fails it); a dropped
        #      sentence sharing only a piece one byte under MIN_PIECE_BYTES with a detail layer (a mutant
        #      with a smaller floor fails it); a detail piece the working core still holds as often as
        #      the base core (a mutant not counting the piece in the working core fails it); a move of
        #      one line that crosses a sentence end, each side under MIN_PIECE_BYTES, since the floor
        #      applies to each sentence on its own (a mutant whose pieces cross sentence ends fails it);
        #      and a dropped sentence whose text up to a dot inside a word (v1.2) a detail layer holds,
        #      since only a dot that a space follows ends a sentence (a mutant cutting there fails it).
        crossing = "ending in a number. Code or document"
        if not all(len(side.encode("utf-8")) < MIN_PIECE_BYTES for side in crossing.split(". ")):
            failures.append("T45: each side of the crossing move must be under MIN_PIECE_BYTES")
        if len("sentence the base holds".encode("utf-8")) != MIN_PIECE_BYTES - 1:
            failures.append("T45: the shared phrase must be one byte under MIN_PIECE_BYTES")
        for label, base_spec, head_spec, rules in (
                ("a new detail sentence", ("ruleaa", False, ""), ("ruleaa", False, "", moved_sentence),
                 ("prjint1", "ruleaa")),
                ("a dropped core sentence", ("ruleaa", False, ". " + moved_sentence[:-1]),
                 ("ruleaa", False, "", "Other detail text."), ("prjint1", "ruleaa")),
                ("an unlisted rule's move", ("ruleaa", False, ". " + moved_sentence[:-1]),
                 ("ruleaa", False, "", moved_sentence), ("prjint1",)),
                ("a shared phrase one byte under the floor", ("ruleaa", False, ". " + moved_sentence[:-1]),
                 ("ruleaa", False, "", "Another sentence the base holds elsewhere."), ("prjint1", "ruleaa")),
                ("a piece the core still holds", ("ruleaa", False, ". " + moved_sentence[:-1]),
                 ("ruleaa", False, ". " + moved_sentence[:-1] + " now", moved_sentence[:-1]),
                 ("prjint1", "ruleaa")),
                ("a move across a sentence end",
                 ("ruleaa", False, ". The core keeps this first part of a line " + crossing
                  + " what follows in it"),
                 ("ruleaa", False, ". The core keeps this first part of a line what follows in it", crossing),
                 ("prjint1", "ruleaa")),
                ("a dot inside a word", ("ruleaa", False, ". Keep v1.2 here"), ("ruleaa", False, "", "Keep v1."),
                 ("prjint1", "ruleaa"))):
            root45, base45 = move_fixture("t45-" + label.replace(" ", "-").replace("'", ""), base_spec,
                                          head_spec=head_spec, rules=rules)
            code, out = capture_gate(root45, base45)
            if code != 0 or "core-move check against base" not in out:
                failures.append("T45 {}: expected exit 0 with the core-move summary, got {}\n{}".format(
                    label, code, out))

        # T46. A rule source at the base that is not UTF-8 is exit 2 (a mutant skipping unreadable base
        #      sources reads nothing moved and exits 0).
        t46, _base46 = move_fixture("t46", ("ruleaa", False, ""))
        (t46.joinpath(*gfpd.RULES_PARTS) / "zz-bad.md").write_bytes(b"---\ncorpus-id: zzbad\n---\n\xff\n")
        fixture_git(t46, "add", "-A")
        fixture_git(t46, "commit", "-q", "-m", "a base source that is not UTF-8")
        base46 = fixture_git(t46, "rev-parse", "HEAD")
        (t46.joinpath(*gfpd.RULES_PARTS) / "zz-bad.md").unlink()
        code, out = capture_gate(t46, base46)
        if code != 2 or "is not valid UTF-8" not in out:
            failures.append("T46: a base rule source that is not UTF-8 must be exit 2, got {}\n{}".format(
                code, out))

        # T47. Moves the whole-sentence test of an earlier revision passed, each exit 1 naming the moved
        #      bytes and the rule whose detail layer holds them: part of a sentence moved word for word
        #      into ruleaa's own detail layer, its trailing comma left behind (a mutant matching whole
        #      sentences only exits 0, and one matching whole words only names 24 bytes, not the 30
        #      moved); the T43 sentence moved into the detail layer
        #      of rulebb, an unlisted rule, while ruleaa is split and while ruleaa is unsplit (a mutant
        #      reading ruleaa's own detail layer only, or skipping an unsplit listed rule, exits 0); and one
        #      copy of a sentence the base core holds twice moved into ruleaa's detail layer (a mutant
        #      testing presence instead of the count exits 0).
        long_core = ". A sentence the base holds, with a clause of its own words, in its core"
        clause = "with a clause of its own words"
        # T48. Behaviours the residue states, each exit 1 naming the moved bytes, the holder and the first
        #      piece: a bracketed clause moved into ruleaa's detail layer without its brackets, a piece
        #      starting at the word's first letter (a mutant starting pieces only at word starts sees only
        #      the 16 bytes after the first word, under MIN_PIECE_BYTES, and exits 0); two short sentences
        #      ended by ? and ! moved whole (a mutant not cutting after ? names 16 bytes, one not cutting
        #      after ! names 11, one cutting only after . exits 0); a piece that passes the count only at
        #      an earlier word end, its longer form still held by the core (a mutant trying only the
        #      detail copy's end names 30 of the 60 bytes); and a sentence dropped from the core while a
        #      base detail layer already held it, the disclosed over-refusal, refused though nothing moved.
        bracketed = "sentence pieces count too"
        if (len(bracketed.encode("utf-8")) < MIN_PIECE_BYTES
                or len(bracketed.split(" ", 1)[1].encode("utf-8")) >= MIN_PIECE_BYTES):
            failures.append("T48: the bracketed clause must reach MIN_PIECE_BYTES only with its first word")
        alpha = "Alpha beta gamma delta epsilon"
        rows47 = (
            ("T47 part of a sentence", ("ruleaa", False, long_core),
             ("ruleaa", False, ". A sentence the base holds in its core", clause),
             ("rulebb", False, 400), len(clause), "ruleaa", clause),
            ("T47 a sentence into another rule's detail, split", ("ruleaa", False, ". " + moved_sentence[:-1]),
             ("ruleaa", False, "", "Other detail text."), ("rulebb", False, 400, moved_sentence),
             len(moved_sentence), "rulebb", moved_sentence),
            ("T47 a sentence into another rule's detail, unsplit",
             ("ruleaa", False, ". " + moved_sentence[:-1]), ("ruleaa", False, ""),
             ("rulebb", False, 400, moved_sentence), len(moved_sentence), "rulebb", moved_sentence),
            ("T47 one copy of a repeated sentence",
             ("ruleaa", False, ". " + moved_sentence + " " + moved_sentence[:-1]),
             ("ruleaa", False, ". " + moved_sentence[:-1], moved_sentence),
             ("rulebb", False, 400), len(moved_sentence), "ruleaa", moved_sentence),
            ("T48 a bracketed clause", ("ruleaa", False, ". A sentence the base holds (" + bracketed + ") in its core"),
             ("ruleaa", False, ". A sentence the base holds in its core", bracketed),
             ("rulebb", False, 400), len(bracketed), "ruleaa", bracketed),
            ("T48 short sentences ended by ? and !", ("ruleaa", False, ". Is it held? Yes! It stays in its core"),
             ("ruleaa", False, ". It stays in its core", "Is it held? Yes!"),
             ("rulebb", False, 400), len("Is it held?") + len("Yes!"), "ruleaa", "Is it held?"),
            ("T48 a piece ending at an earlier word end",
             ("ruleaa", False, ". " + alpha + " zeta. " + alpha + " theta"),
             ("ruleaa", False, ". " + alpha + " zeta now", alpha + " zeta"),
             ("rulebb", False, 400), 2 * len(alpha), "ruleaa", alpha),
            ("T48 a sentence a base detail layer already held",
             ("ruleaa", False, ". " + moved_sentence[:-1], moved_sentence), ("ruleaa", False, "", moved_sentence),
             ("rulebb", False, 400), len(moved_sentence), "ruleaa", moved_sentence),
            ("T52 a crossing move whose left side is a short whole sentence",
             ("ruleaa", False, ". Yes! Something else entirely here"),
             ("ruleaa", False, ". Something else entirely here", "Yes! Some"),
             ("rulebb", False, 400), len("Yes!"), "ruleaa", "Yes!"),
            ("T52 a dropped short sentence a detail layer holds inside a longer word",
             ("ruleaa", False, ". Stop. Keep going with the rest", "Run Non-Stop. Fine."),
             ("ruleaa", False, ". Keep going with the rest", "Run Non-Stop. Fine."),
             ("rulebb", False, 400), len("Stop."), "ruleaa", "Stop."))
        for label, base_spec, head_spec, head_bb, nbytes, holder, first in rows47:
            root47, base47 = move_fixture(
                "t4x-" + label.replace(" ", "-").replace("'", "").replace(",", "").replace("?", "q")
                .replace("!", "x"), base_spec, head_spec=head_spec, head_bb=head_bb)
            code, out = capture_gate(root47, base47)
            want = "FAIL: rule ruleaa moves {} bytes of its core layer".format(nbytes)
            if code != 1 or want not in out or "in the detail layer of {}: {!r}".format(holder, first) not in out:
                failures.append("{}: expected exit 1 naming {} moved bytes held by {}, the first {!r}, got {}\n"
                                "{}".format(label, nbytes, holder, first, code, out))

        # T49. The module docstring, the gate's manifest residue and the whole text of the declared file
        #      (its comments and its data rows alike: a value in a reason string turns T49 red, and a name
        #      there satisfies it) name the constants they state and never write a constant's value as a
        #      number, so none goes stale when the constant changes: MIN_PIECE_BYTES and CAP_BYTES in all
        #      three, FIRST_PIECE_CHARS in the docstring and the residue.
        def t49_residue(gates_text, sink):
            """(the must-deliver residue in gates_text, None); (None, None) after a T49 failure appended to
            sink when gates_text is not TOML or holds no such residue; (None, reason) when tomllib runs out of
            recursion depth or memory on it, or its gate entries or the residue have the wrong type (a gate
            that is a table or a scalar, a residue that is not a string), which is cannot-evaluate (exit 2),
            never a T49 failure or a traceback."""
            try:
                gates_doc = tomllib.loads(gates_text)
                found = next(g["residue"] for g in gates_doc["gate"] if g.get("id") == "must-deliver")
            except (RecursionError, MemoryError) as exc:
                return None, "tomllib raised {} on .aiqt/core/gates/manifest.toml".format(type(exc).__name__)
            except (ValueError, KeyError, StopIteration) as exc:
                sink.append("T49: the must-deliver residue cannot be read ({})".format(exc))
                return None, None
            except (AttributeError, TypeError) as exc:
                return None, ("the gate entries of .aiqt/core/gates/manifest.toml are not tables "
                              "({}: {})".format(type(exc).__name__, exc))
            if not isinstance(found, str):
                return None, ("the must-deliver residue in .aiqt/core/gates/manifest.toml is a {}, not a "
                              "string".format(type(found).__name__))
            return found, None

        def t49_read(root, sink):
            """(the residue, the declared file's whole text, the cannot-evaluate reason or None) read from the
            gates manifest and DECL_REL under root; a file that cannot be read appends a T49 failure to sink
            and gives (None, None, None)."""
            try:
                gates_text = (root / ".aiqt/core/gates/manifest.toml").read_text(encoding="utf-8")
                decl_text = (root / DECL_REL).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                sink.append("T49: the gates manifest or {} cannot be read ({})".format(DECL_REL, exc))
                return None, None, None
            residue, error = t49_residue(gates_text, sink)
            return residue, decl_text, error

        residue, decl_text, t49_error = t49_read(repo_root(), failures)
        constants = (("MIN_PIECE_BYTES", MIN_PIECE_BYTES), ("CAP_BYTES", check_release_build.CAP_BYTES),
                     ("FIRST_PIECE_CHARS", FIRST_PIECE_CHARS))
        for where, text, names in (("the module docstring", __doc__, ("MIN_PIECE_BYTES", "CAP_BYTES",
                                                                       "FIRST_PIECE_CHARS")),
                                   ("the manifest residue", residue, ("MIN_PIECE_BYTES", "CAP_BYTES",
                                                                      "FIRST_PIECE_CHARS")),
                                   (DECL_REL, decl_text, ("MIN_PIECE_BYTES", "CAP_BYTES"))):
            if text is None:
                continue
            for name in names:
                if name not in text:
                    failures.append("T49: {} must name {}".format(where, name))
            for name, value in constants:
                if re.search(r"(?<![\w.]){}(?![\w])".format(value), text):
                    failures.append("T49: {} must never write the value {} of {}".format(where, value, name))

        # T50. tomllib running out of recursion depth on the gates manifest is cannot-evaluate, never a
        #      traceback or a T49 failure: a deeply nested array (a mutant whose handler drops RecursionError
        #      raises it here). T54 runs the MemoryError arm; tools/check_python_floor.py also guards it.
        too_deep = "a = " + "[" * 100000 + "]" * 100000
        try:
            nested = t49_residue(too_deep, failures)
        except RecursionError:
            nested = (None, None)
        if nested[0] is not None or nested[1] is None or "RecursionError" not in nested[1]:
            failures.append("T50: a gates manifest tomllib cannot nest must be cannot-evaluate naming "
                            "RecursionError, got {!r}".format(nested))

        # T51. The statements this round corrected, each pinned as written (the text, not its truth: the
        #      T52 and T53 rows and the T45 to T48 rows pin the behaviour): in the module docstring a crossing
        #      move's whole-sentence side, the dropped-sentence over-refusal inside a longer word, "at least
        #      as often" (twice) and the printed piece cut to FIRST_PIECE_CHARS; in the residue the same three.
        pinned = (
            ("the module docstring", __doc__, (
                "a side that is its whole sentence counts at any length, and any other side only when it "
                "reaches MIN_PIECE_BYTES",
                "when the working core holds that longer piece at least as often as the base core",
                "a piece the working core still holds at least as often as the base core did",
                "already holds, anywhere in its text and even inside a longer word, the whole sentence at any "
                "length (Stop. inside Non-Stop.)",
                "the first FIRST_PIECE_CHARS characters of the first moved piece")),
            ("the manifest residue", residue, (
                "when the working core holds that longer piece at least as often as the base core",
                "a piece the working core still holds at least as often as the base core did",
                "already holds, anywhere in its text and even inside a longer word, the whole sentence at any "
                "length (Stop. inside Non-Stop.)",
                "the first FIRST_PIECE_CHARS characters of the first moved piece")))
        for where, text, phrases in pinned:
            flat = " ".join((text or "").split())
            for phrase in phrases:
                if phrase not in flat:
                    failures.append("T51: {} must state {!r}".format(where, phrase))

        # T53. The two T51 statements no earlier row pins: a moved sentence longer than FIRST_PIECE_CHARS
        #      characters is exit 1 printing exactly its first FIRST_PIECE_CHARS characters (a mutant printing
        #      the whole piece fails it); and a sentence the working core still holds as often as the base
        #      core, which a detail layer also holds whole, is exit 0 (a mutant skipping a sentence only when
        #      the working core holds it more often, > for >=, refuses it).
        long_sentence = ("A long core sentence " + "with many more words " * (FIRST_PIECE_CHARS // 21 + 1)
                         + "here.")
        if len(long_sentence) <= FIRST_PIECE_CHARS:
            failures.append("T53: the long sentence must be longer than FIRST_PIECE_CHARS characters")
        root53, base53 = move_fixture("t53-cut", ("ruleaa", False, ". " + long_sentence[:-1]),
                                      head_spec=("ruleaa", False, "", long_sentence))
        code, out = capture_gate(root53, base53)
        want = "FAIL: rule ruleaa moves {} bytes of its core layer".format(len(long_sentence.encode("utf-8")))
        cut = "in the detail layer of ruleaa: {!r})".format(long_sentence[:FIRST_PIECE_CHARS])
        if code != 1 or want not in out or cut not in out or repr(long_sentence) in out:
            failures.append("T53 a moved sentence longer than FIRST_PIECE_CHARS: expected exit 1 printing its "
                            "first FIRST_PIECE_CHARS characters only, got {}\n{}".format(code, out))
        root53, base53 = move_fixture("t53-held", ("ruleaa", False, ". " + moved_sentence[:-1]),
                                      head_spec=("ruleaa", False, ". " + moved_sentence[:-1], moved_sentence))
        code, out = capture_gate(root53, base53)
        if code != 0 or "core-move check against base" not in out:
            failures.append("T53 a sentence the core still holds as often, also held by a detail layer: "
                            "expected exit 0 with the core-move summary, got {}\n{}".format(code, out))

        # T54. The cannot-evaluate path end to end. A tree whose gates manifest tomllib cannot nest is read
        #      by t49_read, the reader T49 runs on the repository, as cannot-evaluate naming RecursionError
        #      with no T49 failure, and the self-test's verdict on that is exit 2 reporting it (a mutant whose
        #      verdict ignores t49_error returns 0 there). tomllib raising MemoryError, injected by a text whose
        #      replace raises it, is cannot-evaluate naming MemoryError (a mutant whose handler drops
        #      MemoryError raises it here). A gate that is a table, a gate that is an integer and a residue
        #      that is not a string are each cannot-evaluate with no T49 failure (a mutant without those
        #      arms raises AttributeError or TypeError here, or returns the residue).
        root54 = tmp / "t54"
        (root54 / ".aiqt/core/gates").mkdir(parents=True)
        (root54 / ".aiqt/core/gates/manifest.toml").write_text(too_deep, encoding="utf-8")
        _decl(root54, [("ruleaa", "fixture rule")])
        sink54 = []
        try:
            read54 = t49_read(root54, sink54)
        except RecursionError:
            read54 = (None, None, None)
        if (read54[0] is not None or read54[1] is None or not read54[2] or "RecursionError" not in read54[2]
                or sink54):
            failures.append("T54: a tree's gates manifest tomllib cannot nest must read as cannot-evaluate "
                            "naming RecursionError with no T49 failure, got {!r} {!r}".format(read54, sink54))
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            code = _self_test_verdict(None, read54[2], [], [])
        if code != 2 or "cannot evaluate" not in buf.getvalue() or str(read54[2]) not in buf.getvalue():
            failures.append("T54: the verdict on a T49 cannot-evaluate must be exit 2 reporting it, got {}\n"
                            "{}".format(code, buf.getvalue()))

        class _MemoryErrorText(str):
            def replace(self, *args):
                raise MemoryError("injected by the T54 row")

        for label, text, want in (
                ("tomllib raising MemoryError", _MemoryErrorText("[[gate]]\n"), "MemoryError"),
                ("a gate that is a table", '[gate]\nid = "must-deliver"\nresidue = "r"\n', "AttributeError"),
                ("a gate that is an integer", "gate = 1\n", "TypeError"),
                ("a residue that is not a string", '[[gate]]\nid = "must-deliver"\nresidue = 1\n', "not a string")):
            sink54 = []
            try:
                got = t49_residue(text, sink54)
            except (MemoryError, AttributeError, TypeError) as exc:
                got = ("raised", type(exc).__name__)
            if got[0] is not None or not got[1] or want not in got[1] or sink54:
                failures.append("T54 {}: must be cannot-evaluate naming {} with no T49 failure, got {!r} "
                                "{!r}".format(label, want, got, sink54))

        # T55. Listed rules come first: the unlisted ruleaa sorts between the listed prjint1 and rulebb in
        # AIQT priority order and is padded past the cap, so under priority order rulebb would end past it.
        # In the listed-first order rulebb ends inside the cap: exit 0 with its measured margin.
        t55 = tmp / "t55"
        sp = gfpd._write_tree(t55, [("prjint1", True, 0), ("ruleaa", False, CAP), ("rulebb", False, 400)],
                              listed=("prjint1", "rulebb"))
        if not sp["prjint1"][1] <= sp["rulebb"][0] < sp["rulebb"][1] <= sp["ruleaa"][0]:
            raise _FixtureError("T55 precondition: the fixture did not place rulebb between prjint1 and "
                                "ruleaa: {}".format(sp))
        code, out = capture(t55)
        if code != 0 or "rulebb  end {:6d}  margin {}".format(sp["rulebb"][1], CAP - sp["rulebb"][1]) not in out:
            failures.append("T55: a listed rule must come before an unlisted rule of higher priority (exit 0 "
                            "with rulebb's margin), got {}\n{}".format(code, out))

        # T57. The missing-span check in run() stays as defence in depth behind measure's own membership
        # check: a stand-in measure returning the real spans without ruleaa's is exit 2 naming the list.
        t57 = tmp / "t57"
        gfpd._tuned(t57, base_specs, "ruleaa", CAP - 10)
        _decl(t57, both)
        real_measure = globals()["measure"]

        def measure_without_ruleaa(root):
            agents57, spans57, apex57 = real_measure(root)
            return agents57, [s for s in spans57 if s[0] != "ruleaa"], apex57

        globals()["measure"] = measure_without_ruleaa
        try:
            code, out = capture(t57)
        except Exception as exc:  # a removed check raises KeyError outside run()'s try
            code, out = "raised {!r}".format(exc), ""
        finally:
            globals()["measure"] = real_measure
        if code != 2 or "with no measured rule span" not in out:
            failures.append("T57: a listed rule with no measured span must be exit 2 naming it, got {}\n{}"
                            .format(code, out))

        # T58. The reserve is enforced: with reserve-bytes 512, ruleaa ending exactly 512 bytes inside the cap
        # is exit 0 with no FAIL line, and ending 511 bytes inside it is exit 1 naming the margin, the rule and
        # the reserve; the same 511-byte margin under reserve-bytes 0 is exit 0, so the failure is the
        # reserve's own.
        t58 = tmp / "t58"
        reserve58 = 512
        sp = gfpd._tuned(t58, base_specs, "ruleaa", CAP - reserve58)
        _decl(t58, both, reserve=reserve58)
        code, out = capture(t58)
        if (sp["ruleaa"][1] != CAP - reserve58 or code != 0 or "FAIL" in out
                or "reserve-bytes {}:".format(reserve58) not in out):
            failures.append("T58: a margin equal to reserve-bytes must be exit 0 reporting the reserve "
                            "(fixture end {}), got {}\n{}".format(sp["ruleaa"][1], code, out))
        sp = gfpd._tuned(t58, base_specs, "ruleaa", CAP - reserve58 + 1)
        code, out = capture(t58)
        want58 = "FAIL: tightest margin {} (ruleaa) is below reserve-bytes {}".format(reserve58 - 1, reserve58)
        if sp["ruleaa"][1] != CAP - reserve58 + 1 or code != 1 or want58 not in out:
            failures.append("T58: a margin one byte below reserve-bytes must be exit 1 with {!r} (fixture end "
                            "{}), got {}\n{}".format(want58, sp["ruleaa"][1], code, out))
        _decl(t58, both, reserve=0)
        code, out = capture(t58)
        if (code != 0 or "FAIL" in out
                or "WARNING: tightest margin {} is below warn-margin-bytes 512".format(reserve58 - 1) not in out):
            failures.append("T58: the same margin under reserve-bytes 0 must be exit 0 with the warning, got "
                            "{}\n{}".format(code, out))

        # T60. A comparison base written in format-version 1 still compares (the migration): against a
        # version 1 base listing prjint1 and ruleaa, an unchanged version 2 working list is exit 0, a drop
        # with no [[removal]] row is exit 1 naming ruleaa, and the drop with a row is exit 0 printing it.
        for label, rules, removals, want_code, want in (
                ("unchanged", ["prjint1", "ruleaa"], (), 0, "(0 added, 0 removed)"),
                ("a drop with no row", ["prjint1"], (), 1, drop_msg),
                ("a drop with a row", ["prjint1"], (("ruleaa", "fixture removal reason"),), 0,
                 "[[removal]] reason: fixture removal reason")):
            t60, base60 = ratchet_fixture("t60-" + label.replace(" ", "-"), base_version=1)
            base_text60 = fixture_git(t60, "show", "{}:{}".format(base60, DECL_REL))
            if "format-version = 1\n" not in base_text60 or "reserve-bytes" in base_text60:
                raise _FixtureError("T60 precondition: the base list is not in the version 1 schema")
            _decl(t60, [], raw=raw_decl(rules, removals))
            code, out = capture_gate(t60, base60)
            if code != want_code or want not in out:
                failures.append("T60 {} against a version 1 base: expected exit {} with {!r}, got {}\n{}".format(
                    label, want_code, want, code, out))

        # T61. The base parser accepts exactly version 1 or exactly version 2: a base in format-version 3 or
        # 1.0, a version 1 base carrying reserve-bytes, a version 2 base without it, and a version 2 base whose
        # reserve-bytes is a bool, -1 or 1.0, are each exit 2 naming the defect at the base.
        t61, _base61 = ratchet_fixture("t61")
        rule61 = '[[rule]]\nid = "prjint1"\nreason = "x"\n[[rule]]\nid = "ruleaa"\nreason = "x"\n'
        base_msg61 = "format-version must be the integer 1 or 2"
        for label, head, want in (
                ("format-version 3", "format-version = 3\nwarn-margin-bytes = 512\nreserve-bytes = 0\n", base_msg61),
                ("format-version 1.0", "format-version = 1.0\nwarn-margin-bytes = 512\n", base_msg61),
                ("version 1 with reserve-bytes", "format-version = 1\nwarn-margin-bytes = 512\nreserve-bytes = 0\n",
                 "(format-version 1) must carry exactly the keys"),
                ("version 2 without reserve-bytes", "format-version = 2\nwarn-margin-bytes = 512\n",
                 "(format-version 2) must carry exactly the keys"),
                ("reserve boolean", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = false\n", reserve_msg),
                ("reserve -1", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = -1\n", reserve_msg),
                ("reserve float", "format-version = 2\nwarn-margin-bytes = 512\nreserve-bytes = 1.0\n", reserve_msg)):
            _decl(t61, [], raw=head + rule61)
            fixture_git(t61, "add", "-A")
            fixture_git(t61, "commit", "-q", "-m", "fixture base: " + label)
            base61 = fixture_git(t61, "rev-parse", "HEAD")
            _decl(t61, [], raw=raw_decl(["prjint1", "ruleaa"]))
            code, out = capture_gate(t61, base61)
            if code != 2 or "at the base" not in out or want not in out:
                failures.append("T61 base {}: expected exit 2 with {!r} at the base, got {}\n{}".format(
                    label, want, code, out))

        # T63. The real list holds the reserve the maintainer set (512 bytes), so the gate run on this tree
        # enforces it; lowering or dropping it is a reviewed edit of this row too.
        try:
            real63 = read_declared(repo_root() / DECL_REL)
        except (ValueError, OSError) as exc:
            failures.append("T63: the real {} cannot be read ({})".format(DECL_REL, exc))
        else:
            if real63.reserve != 512:
                failures.append("T63: the real {} declares reserve-bytes {}, not the 512 the maintainer "
                                "set".format(DECL_REL, real63.reserve))

        # T64. The reserve and the base parser are stated as pinned (the text, not its truth: T58 to T61 pin
        #      the behaviour): in the module docstring and in the manifest residue.
        pinned64 = (
            ("the module docstring", __doc__, (
                "RESERVE. reserve-bytes (an integer of at least 0 and at most warn-margin-bytes, never a bool "
                "or a float) is enforced",
                "accepts exactly the format-version 1 schema or exactly the format-version 2 schema")),
            ("the module docstring", __doc__, (
                "The list ratchet also compares reserve-bytes with the reserve-bytes listed at the same "
                "comparison base (a format-version 1 base, which has none, counts as 0)",
                "a deliberate lowering has no declared path like a [[removal]] row")),
            ("the manifest residue", residue, (
                "The list's reserve-bytes (format-version 2) is enforced",
                "accepts exactly format-version 1 (no reserve-bytes) or exactly format-version 2",
                "The list ratchet also compares reserve-bytes with the reserve-bytes listed at the same "
                "comparison base (a format-version 1 base, which has none, counts as 0)",
                "a deliberate lowering has no declared path like a [[removal]] row")))
        for where, text, phrases in pinned64:
            flat = " ".join((text or "").split())
            for phrase in phrases:
                if phrase not in flat:
                    failures.append("T64: {} must state {!r}".format(where, phrase))

        # T65. The reserve ratchet. Each fixture commits a base listing prjint1 and ruleaa (ruleaa CAP - 10,
        # so the tightest margin is 10 and a working reserve up to 10 passes the prefix check), in format-
        # version 2 with reserve-bytes 9 or in format-version 1, then writes the working list.
        lower65 = "FAIL: reserve-bytes 8 is lower than the reserve-bytes 9 listed at the base"
        for label, base_version, rules, reserve, want_code, wants in (
                ("lowered", 2, ["prjint1", "ruleaa"], 8, 1, (lower65,)),
                ("lowered with an undeclared drop", 2, ["prjint1"], 8, 1, (lower65, drop_msg)),
                ("kept", 2, ["prjint1", "ruleaa"], 9, 0, ("reserve-bytes 9 there (0 for a format-version 1 "
                                                          "base), 9 now",)),
                ("raised", 2, ["prjint1", "ruleaa"], 10, 0, ("reserve-bytes 9 there (0 for a format-version 1 "
                                                             "base), 10 now",)),
                ("raised over a version 1 base", 1, ["prjint1", "ruleaa"], 10, 0,
                 ("reserve-bytes 0 there (0 for a format-version 1 base), 10 now",))):
            t65 = tmp / ("t65-" + label.replace(" ", "-"))
            gfpd._tuned(t65, base_specs, "ruleaa", CAP - 10)
            _decl(t65, [], raw=raw_decl(["prjint1", "ruleaa"], version=base_version, reserve=9))
            base65 = commit_base(t65)
            _decl(t65, [], raw=raw_decl(rules, reserve=reserve))
            code, out = capture_gate(t65, base65)
            if (code != want_code or any(w not in out for w in wants)
                    or (want_code == 0 and "FAIL" in out)):
                failures.append("T65 {}: expected exit {} with {!r}{}, got {}\n{}".format(
                    label, want_code, wants, " and no FAIL line" if want_code == 0 else "", code, out))
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

    return _self_test_verdict(fixture_error, t49_error, failures, skipped)


def _self_test_verdict(fixture_error, t49_error, failures, skipped):
    """The self-test's report and exit: 2 when a fixture could not be built or T49 cannot evaluate the gates
    manifest, else 1 when a vector failed, else 0 (T54 runs the cannot-evaluate path through it)."""
    if fixture_error is not None:
        print("SELF-TEST ERROR: a list-ratchet fixture could not be built ({}); cannot evaluate".format(
            fixture_error), file=sys.stderr)
        return 2
    if t49_error is not None:
        print("SELF-TEST ERROR: T49 cannot read the must-deliver residue ({}); cannot evaluate".format(
            t49_error), file=sys.stderr)
        return 2
    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a listed rule ending at the cap passes and one ending one byte past, "
          "straddling it, or byte-measured past it fails naming the rule; a stale AGENTS.md and every "
          "malformed (a non-integer or unsupported format-version, a version 1 working file, a warn margin "
          "below 1, a reserve that is not an integer of at least 0, missing or above the warn margin), "
          "absent, empty, "
          "symlinked, unreadable (an injected read error) or unparseable list fail closed (exit 2); the "
          "demonstration's own lists are never read; an unknown or clause id fails closed naming it, and "
          "a listed rule with no measured span still does behind the membership check; the listed rules "
          "come before an unlisted rule of higher priority; the "
          "cap is read from check_release_build.CAP_BYTES at call time; a before-rules block moves the "
          "spans it measures; the margin report, the unlisted-rule line and the warn-margin WARNING "
          "(still exit 0) are printed exactly; a listed split rule is measured on its core layer, its "
          "detail after the last rule (a mutant placing it at the rule changes that), and a straddling core "
          "fails; a listed rule ending exactly reserve-bytes inside the cap passes and one byte less fails "
          "naming the margin, and the real list holds the reserve the maintainer set; a reserve lowered "
          "below the base's fails naming both while a kept or raised one passes (a version 1 base read as "
          "reserve 0); and against a "
          "committed base, written in format-version 1 or 2 (any other base schema fails closed), a rule "
          "dropped from the list "
          "with no [[removal]] row fails naming it (also with the demonstration regenerated without it, "
          "and with HEAD past the base, the list read at the base), "
          "a declared removal passes and is printed, an addition passes, and a malformed removal row, an "
          "unresolvable or missing base and an absent or malformed base list fail closed (exit 2); a "
          "branch or tag shadowing origin/main, a GIT_DIR in the environment and a replace ref do not "
          "change the base read, a full object id and HEAD are read as git reads them, "
          "a full object id whose object does not exist fails closed, and a planted refs/remotes/<id> or "
          "refs/remotes/HEAD makes the base (also HEAD~0) ambiguous (exit 2), never the base read; a "
          "planted refs/remotes/<id>/HEAD makes <id>, <id>~0 and <id>^0 exit 2, and as a symbolic ref to "
          "a branch <id> and <id>~0, never the base read; a grafts file does not change the base read, "
          "with no --base or --base HEAD^; --base is a closed grammar: an abbreviated id (also with "
          "refs/remotes/<abbreviation> planted), a describe form, a brace, a bare remote name, and, each "
          "with its /HEAD planted, a '{{' in a leading name and a remote-tracking name check-ref-format "
          "rejects fail closed naming the accepted forms, while HEAD^, HEAD~ and <id>~0^0 read the base; "
          "a pseudo-ref file <git dir>/origin/main does not change origin/main read with any suffix run, "
          "a forged commit-graph file does not change the merge base or HEAD~2, and a remote named HEAD "
          "does not change --base HEAD or HEAD~0; --self-test runs only when it is the whole command "
          "line, and mixed with any other argument it is exit 2, never a self-test run; "
          "and a symlinked base list, a --base starting with -, an expression led by a shadowed "
          "remote-tracking name, an absent remote-tracking ref of a configured remote, and an expression "
          "led by one (also with its /HEAD planted) fail closed; and a listed rule's core sentence at the "
          "base moved into its detail layer fails naming the moved bytes, and so do part of a sentence "
          "moved word for word, a sentence moved into another rule's detail layer (the listed rule split "
          "or not), one copy of a repeated sentence, a bracketed clause moved without its brackets, short "
          "sentences ended by ? and ! moved whole, a piece ending at an earlier word end and a sentence a "
          "base detail layer already held, each naming the holder and the first piece; an empty listed "
          "core fails (also with nothing moved), a new detail sentence, a dropped core sentence, an unlisted "
          "rule's move, a shared phrase one byte under MIN_PIECE_BYTES, a piece the core still holds, a "
          "move across a sentence end with each side under MIN_PIECE_BYTES and a dot inside a word pass, "
          "a base rule source that is not UTF-8 fails closed, a crossing move whose one side is a short "
          "whole sentence and a dropped short sentence a detail layer holds inside a longer word fail "
          "naming it; the docstring, the residue and the whole declared file name MIN_PIECE_BYTES and "
          "CAP_BYTES (the first two also FIRST_PIECE_CHARS), never a value, a gates manifest tomllib cannot "
          "nest is cannot-evaluate, and the corrected statements are written as pinned; a moved piece is "
          "printed cut to FIRST_PIECE_CHARS and a sentence the core still holds as often passes; a gates "
          "manifest read from a tree that tomllib cannot nest is exit 2 through the verdict, and MemoryError, "
          "a gate that is a table or an integer and a residue that is not a string are cannot-evaluate. "
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
