#!/usr/bin/env python3
"""OPF store-schema upgrade gate (spec 9.2), exercised exclusively through isolated temporary fixtures.

Both the default entry and --self-test run the fixture suite. There is no live-adopter mutation leg and
no root option: the gate drives `opf upgrade` (isolated, -I -B) over BYTE-PINNED 1.0.0 stores built beneath
fresh temporary git repositories, and asserts the 1.0.0 and 1.1.0 -> 1.2.0 contract over an
enumerated adopter-shape matrix, a set of refusal fixtures, direct postcondition unit vectors, and a seeded migration property test.

FIXTURE FIDELITY. The 1.0.0 fixture bytes are FROZEN, anchored on the frozen `_FIX_MANIFEST` /
`_FIX_COUNTERS` baseline (not regenerated from the current builders), so the fixture cannot silently drift
into some other schema as the tooling evolves: it is the store an already-shipped 1.0.0 tool would have
written. Module-enabled and view-omitting variants are the frozen baseline PLUS exactly the rows a valid
1.0.0 store of that shape must carry (a module boolean, `[types]` rows with normative namespaces, counters,
index files), derived by explicit canonical string operations and RE-VERIFIED canonical in-gate through the
canonical emitter (the same precondition `opf upgrade` enforces), so an emitter change that would invalidate
any frozen fixture is caught HERE rather than in the field. The genuine-1.0.0-store shape of each module-
enabled variant was additionally graded doctor-VALID at authoring time by the actual merge-base 1.0.0 tooling
(commit 1c90fbb, `tools/opf.py doctor`, its pre-move path at that commit); at 1.0.0 the module-tier records (maintainer_decision /
preference_pattern) are schema-DEFERRED by the baseline validator and become fully-validated baseline records
only at 1.1.0, so a POPULATED module-tier fixture's records are validated at run time by the actual
upgrade -> 1.2.0 doctor leg here rather than by the 1.0.0 baseline. In-gate re-verification is canonicity-only;
1.0.0-doctor fidelity is authoring-time evidence.

VECTOR ROSTER (U1-U28, P1):
  U1  pristine baseline: full delta, doctor VALID.
  U2  idempotence: a second run is a byte no-op reporting already-current.
  U3  governance=true, MA+MD rows + empty indexes + counters: contribution+preference_pattern indexes
      created; MA stays module-tier; governance stays true; MD/MA rows and indexes preserved; doctor VALID.
  U4  governance=true, POPULATED maintainer_decision index (one frozen ruling), MD=1: index bytes and the
      MD counter preserved byte-for-byte; doctor VALID.
  U5  decision_support=true, PP row + empty index + counter: contribution+maintainer_decision indexes
      created; the decision_support module key removed; PP row preserved; doctor VALID.
  U6  decision_support=true, POPULATED preference_pattern index carrying a maintainer bare-active PP and an
      assistant-created RATIFIED PP (active, updated_at > created_at): bytes and high-water preserved;
      doctor VALID. This vector FAILS without the M5 creation-snapshot fix (its pipeline regression).
  U7  governance AND decision_support, both populated: only the contribution index created; both preserved.
  U8  baseline with the optional decision_support module key ABSENT: [modules] unchanged; doctor VALID.
  U9  baseline with DECISIONS.md omitted from [views]: it is neither widened nor created; the two new views
      are added and rendered; doctor VALID.
  U10 U8 and U9 combined.
  U11 delivery_assurance=true with its four type rows/indexes/counters (a representative non-migrated
      module): every delivery_assurance row/index/counter preserved value-exact; doctor VALID.
  U12 baseline plus an untracked well-formed foreign lease.toml: exit 2, the refusal names the holder, the
      lease is NOT deleted, the tree is unchanged.
  U12b ignored lease: held-lease never-seize refusal, unchanged bytes; exclusion flip restores dirt advice.
  U12c ignored/untracked lease directories: never-seize refusal; exclusion flip restores dirt advice.
  U13 baseline plus a malformed lease.toml: exit 2 (present is held, never absent); not deleted; unchanged.
  U14 baseline plus an uncommitted counters.toml edit: exit 2 BEFORE any mutation; the refusal names the
      dirty path and advises commit-your-changes, never a whole-tree restore; the owner edit intact.
  U14b ignored render destinations and directory collisions refuse before mutation; ignored prefix
      siblings and declared unmanaged content permit doctor-VALID upgrades. Companion flips restore
      the erroneous refusals. Collapsed ignored ancestors still expose occupied destinations.
  U14c non-UTF-8 nested prefixes: ignored manifest, counters, render target and collapsed ancestor
      refuse with unchanged files/index/HEAD; the lossy-prefix flip loses dirt advice (the absent-path
      guard still refuses an ignored ancestor).
  U14d absent ignored view/index/product destinations: refuse before mutation; probe-removal flips mutate.
  U15 [types.contribution] pre-declared: exit 2 naming contribution as an impossible 1.0.0 shape; unchanged.
  U16 governance=false plus [types.maintainer_decision]: exit 2 naming the module inconsistency; unchanged.
  U17 the above-tooling, non-canonical, NOT-ADOPTED, and partial-1.2.0 triage refusals (with the scoped
      recovery text).
  U17b post-manifest exception: planned restore/rm advice and F2 candidates, with suppression flips.
  U18 postcondition unit vectors: call opf._upgrade_postcondition directly with hand-mutated new models; each
      mutation refuses and the genuine planner output passes (the check that fails without the m1 fix). Also
      the R6 porcelain-grammar refusal vectors: _opf_write_guard.parse_porcelain refuses a lone NUL, a non-NUL-
      terminated payload, and a mis-framed record (never a clean-empty result), and excludes a nested-store
      lease after the prefix strip; PLUS the R6 fail-open vectors (a MALFORMED status -- ZZ, a blank pair, a
      rename R, a copy C -- for the lease path refuses rather than being silently dropped by the exclusion,
      while a well-formed dirty lease record is still excluded); PLUS the status-PAIR vectors (FIX2/FIX3: an
      IMPOSSIBLE pair -- UT/TU with U only legal in an unmerged pair, AND the impossible ORDINARY pairs a
      {space,M,T,A,D} cartesian would wrongly admit, DM/DT/DA
      (X=D pairs only with space) and MA/TA (Y=A pairs only with space X) -- each refuses whole-pair, not
      per-char, for the lease AND a non-lease path, while a positive sweep of every emittable pair (all 17
      ordinary pairs including ' A' intent-to-add and 'D ', plus ??, !! and the 7 unmerged) still parses; fails
      under the old per-char check or a cartesian superset); and the
      R1b leading-space prefix test (_opf_write_guard.probe_dirty keeps a " leading/"-prefixed lease excluded; fails
      under the old .strip()).
  U19 R1 recovery-text root-binding: opf._upgrade_recovery_text called directly with DISTINCT store/product
      roots; planned-path restore + created-file removal name the store root, the product target the product
      root (fails without the distinct-roots fix).
  U20 R8 module-coupling refusal: governance=true with maintainer_action but maintainer_decision ABSENT (a
      module-inconsistent 1.0.0 shape the delta would silently cure) refuses exit 2 before mutation; unchanged.
  U21 R2 absent-table migration: the missing [modules], missing [views], and both-missing origins (each oracle-
      graded doctor-VALID at merge-base 1c90fbb) migrate to a doctor-VALID 1.2.0 store; an absent table is
      never invented as an empty table beyond the two new views [views] must carry.
  U22 R1 nested-store held lease: a store root BELOW the git repo root with a foreign held lease reaches step
      4's never-seize message, not step 3's dirty-store remedy (the lease_excl prefix-normalization fix).
  U23 R3 FIFO lease: a FIFO at lease.toml refuses PROMPTLY (bounded, no blocking hang) naming a present non-
      regular lease; the FIFO is untouched.
  U24 R4 never-seize on a mid-acquisition failure: with journal._write_all monkeypatched to fail after the
      O_EXCL create, _opf_write_guard.acquire_lease (called direct) LEAVES the lease as a reconcilable leftover
      rather than an ownership-blind by-name unlink -- surfacing a reconcilable WriteGuardError -- and when the write
      first REPLACES the lease with a peer holder's bytes, that replacement is NOT removed (the never-seize
      guarantee; the old by-name unlink deleted the replacement).
  U25 R5 release-before-success: with _opf_write_guard.release_lease monkeypatched to fail, a valid store exits 2 and
      emits NO success line (success is never reported over a still-held / failed-to-release lease). U25b
      (FIX1 release never-seize, class-width): _opf_write_guard.acquire_lease returns the on-disk payload; after a
      peer REPLACES the lease with its own well-formed bytes, _opf_write_guard.release_lease refuses (never-seize) and
      LEAVES the replacement, yet an ordinary release of this run's OWN lease still removes it (fails under the
      old ownership-blind unlink, which deleted the peer's lease). U25c (F-LEASE-RELEASE-ANY-EXC): _upgrade_run
      called from inside a caller's `except`, with a nonzero render (a return path) and an inert failing
      release, propagates the release error; a flip restoring the finally's sys.exc_info() test turns it red,
      and the flip leg asserts its exact swallow outcome (exit 2 plus the "additionally" release-failure note).
  U26 R8 non-boolean module: the planner precondition refuses a non-boolean value on ANY module (governance,
      operational_policy, decision_support), matching the merge-base _validate_modules, while a fully-boolean
      module set still plans; end-to-end, a stored governance="x" refuses exit 2 before any mutation. FIX3: an
      UNKNOWN [modules] key refuses UPFRONT (merge-base _validate_modules parity), while the known-but-retired
      decision_support key still plans; fails without the upfront unknown-key check.
  U27 R1a relocated partial-recovery: a RELOCATED store (store_root != product_root) at spec_version 1.2.0 but
      not doctor-VALID names the STORE root for inspection; no unverified subtree restore is offered.
  U28 1.1.0 -> 1.2.0: only spec_version changes with current declared views; no provenance is
      fabricated; doctor VALID; a second run is a byte no-op.
  P1  a seeded migration property test: 12 generated genuine-VALID 1.0.0 variants (module subset with the
      G2 coupling, 0-2 records per migrated type with matching high-waters, DECISIONS.md declared/omitted,
      the decision_support key present/absent) each upgrade to a doctor-VALID 1.2.0 store with every index
      byte-identical, every pre-existing counter preserved, and worklog/version byte-identical.

Exit convention: 0 observed assertions pass; 1 an assertion fails; 2 cannot evaluate the harness.
"""
import copy
import os
import random
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2

# --- the FROZEN 1.0.0 store fixture (byte-pinned; NOT regenerated from the current builders) ----------
# maintainer_decision and preference_pattern were module-tier and contribution did not exist; the store
# declares the retired decision_support module and its [types] table carries ONLY the 1.0.0 baseline types
# (no maintainer_decision / preference_pattern / contribution). This is what a real 1.0.0 adopter actually
# has: DECISIONS.md is a 2-SOURCE composed view (pending_decision, autonomous_decision), since the other two
# decision types did not yet exist. The spec-9.2 allowed delta WIDENS DECISIONS.md to the four 1.1.0 sources.
_FIX_MANIFEST = ("""\
[archive]
period = "year"

[deliverables]

[deliverables."CHANGELOG.md"]
kind = "curated"
target = "CHANGELOG.md"

[devprocess]
import_status = "none"
layout = "inline"
posture = "required"
spec_version = "1.0.0"
standard = "devprocess"

[modules]
concurrent_operation = false
decision_support = false
delivery_assurance = false
governance = false
operational_policy = false

[providers]

[providers.generic-git-remote]
handler = "builtin"
roles = ["sync"]

[providers.local-directory]
handler = "builtin"
roles = ["create", "sync"]

[store]
sync_target = ""

[types]

[types.autonomous_decision]
namespace = "AD"

[types.backlog_item]
namespace = "BI"

[types.block]
namespace = "BL"

[types.done]
namespace = "DN"

[types.finding]
namespace = "FN"

[types.handoff]
namespace = "HO"

[types.pending_decision]
namespace = "PD"

[types.reference]
namespace = "RF"

[types.worklog]
namespace = "WL"

[unmanaged]
paths = []

[vendors]
registered = []

[views]

[views."BACKLOG.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/BACKLOG.md"

[views."BLOCKS.md"]
kind = "composed"
sources = ["block"]
target = ".working/BLOCKS.md"

[views."DECISIONS.md"]
kind = "composed"
sources = ["pending_decision", "autonomous_decision"]
target = ".working/DECISIONS.md"

[views."DONE.md"]
kind = "composed"
sources = ["done"]
target = ".working/DONE.md"

[views."FINDINGS.md"]
kind = "composed"
sources = ["finding"]
target = ".working/FINDINGS.md"

[views."HANDOFF.md"]
kind = "composed"
sources = ["handoff"]
target = ".working/HANDOFF.md"

[views."PIPELINE.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/PIPELINE.md"

[views."REFERENCES.md"]
kind = "composed"
sources = ["reference"]
target = ".working/REFERENCES.md"

[views."TODO.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/TODO.md"

[views."VERSION.md"]
kind = "deterministic"
sources = ["version"]
target = ".working/VERSION.md"

[views."WORKLOG.md"]
kind = "deterministic"
sources = ["worklog"]
target = ".working/WORKLOG.md"
""")
_FIX_COUNTERS = ("""\
schema = 1

[counters]
AD = 0
BI = 0
BL = 0
DN = 0
FN = 0
HO = 0
PD = 0
RF = 0
WL = 0
""")
_FIX_VERSION = "release = []\nschema = 1\nsummary = []\n"
_FIX_WORKLOG = "entry = []\nschema = 1\n"
_FIX_INDEX = "record = []\nschema = 1\n"
# The 1.0.0 baseline non-ledger index types (the current baseline minus the three 1.1.0 additions and
# the worklog ledger). Frozen alongside the manifest above.
_FIX_INDEX_TYPES = ("autonomous_decision", "backlog_item", "block", "done", "finding", "handoff",
                    "pending_decision", "reference")

# --- FROZEN populated module-tier indexes (synthetic data only; SECP-synthetic-fixture-data) ----------
# Authored 1.0.0 module-tier records: at 1.0.0 they are schema-deferred by the baseline validator and
# become fully-validated baseline records at 1.1.0. The preference_pattern index carries a maintainer
# bare-active PP AND an assistant-created RATIFIED PP (active, updated_at > created_at), the record shape
# the M5 creation-snapshot fix admits at rest and the old at-rest rule wrongly flagged (U6's regression).
_FIX_MD_INDEX = ("""\
schema = 1

[[record]]
created_at = "2026-07-01T00:00:00Z"
decision = "Do the synthetic thing"
id = "MD-1"
status = "recorded"
title = "A synthetic ruling"
type = "maintainer_decision"
updated_at = "2026-07-01T00:00:00Z"

[record.actor]
kind = "maintainer"
""")
_FIX_PP_INDEX = ("""\
schema = 1

[[record]]
context = "ctx"
created_at = "2026-07-01T00:00:00Z"
id = "PP-1"
rationale = "why"
status = "active"
title = "Synthetic pattern one"
type = "preference_pattern"
updated_at = "2026-07-01T00:00:00Z"

[record.actor]
kind = "maintainer"

[[record]]
context = "ctx2"
created_at = "2026-07-01T00:00:00Z"
id = "PP-2"
rationale = "why2"
status = "active"
title = "Synthetic pattern two"
type = "preference_pattern"
updated_at = "2026-07-02T00:00:00Z"

[record.actor]
kind = "assistant"
""")

# --- module-enabled / view-omitting manifest and counter derivations (frozen baseline + exactly the rows a
# valid 1.0.0 store of that shape carries, canonicalized). Each is RE-VERIFIED canonical in-gate. The type
# rows and counter entries are inserted at their canonical (alphabetical) positions, so the derived bytes
# are byte-identical to the canonical emitter's output for that model (asserted below). --------------------
def _man_gov(m=_FIX_MANIFEST):
    m = m.replace("governance = false\n", "governance = true\n", 1)
    return m.replace('[types.pending_decision]',
                     '[types.maintainer_action]\nnamespace = "MA"\n\n'
                     '[types.maintainer_decision]\nnamespace = "MD"\n\n[types.pending_decision]', 1)


def _man_ds(m=_FIX_MANIFEST):
    m = m.replace("decision_support = false\n", "decision_support = true\n", 1)
    return m.replace('[types.reference]',
                     '[types.preference_pattern]\nnamespace = "PP"\n\n[types.reference]', 1)


def _man_da(m=_FIX_MANIFEST):
    m = m.replace("delivery_assurance = false\n", "delivery_assurance = true\n", 1)
    m = m.replace('[types.autonomous_decision]',
                  '[types.artifact]\nnamespace = "AR"\n\n[types.autonomous_decision]', 1)
    m = m.replace('[types.handoff]', '[types.gate_run]\nnamespace = "GR"\n\n[types.handoff]', 1)
    return m.replace('[types.worklog]',
                     '[types.release]\nnamespace = "RL"\n\n[types.waiver]\nnamespace = "WV"\n\n'
                     '[types.worklog]', 1)


_DECISIONS_MD_BLOCK = ('[views."DECISIONS.md"]\nkind = "composed"\n'
                       'sources = ["pending_decision", "autonomous_decision"]\n'
                       'target = ".working/DECISIONS.md"\n\n')


def _man_drop_dskey(m=_FIX_MANIFEST):
    return m.replace("decision_support = false\n", "", 1)


def _man_drop_decisions(m=_FIX_MANIFEST):
    return m.replace(_DECISIONS_MD_BLOCK, "", 1)


def _man_add_contribution(m=_FIX_MANIFEST):
    return m.replace('[types.done]', '[types.contribution]\nnamespace = "CN"\n\n[types.done]', 1)


def _man_add_md(m=_FIX_MANIFEST):
    return m.replace('[types.pending_decision]',
                     '[types.maintainer_decision]\nnamespace = "MD"\n\n[types.pending_decision]', 1)


def _man_gov_no_md(m=_FIX_MANIFEST):
    """governance=true with maintainer_action declared but maintainer_decision ABSENT: a module-inconsistent
    (1.0.0-INVALID, C-ROSTER) origin the delta would silently CURE by adding the now-baseline MD row. The R8
    symmetric precondition refuses it upfront."""
    m = m.replace("governance = false\n", "governance = true\n", 1)
    return m.replace('[types.pending_decision]',
                     '[types.maintainer_action]\nnamespace = "MA"\n\n[types.pending_decision]', 1)


def _cnt_gov_ma_only(c=_FIX_COUNTERS):
    return c.replace("PD = 0\n", "MA = 0\nPD = 0\n", 1)


def _man_drop_modules_table(m=_FIX_MANIFEST):
    """Remove the WHOLE [modules] table (R2): an ABSENT table is 1.0.0-valid-empty (oracle-graded VALID at
    1c90fbb), distinct from U8 which drops only the optional decision_support KEY."""
    return m.replace("[modules]\nconcurrent_operation = false\ndecision_support = false\n"
                     "delivery_assurance = false\ngovernance = false\noperational_policy = false\n\n", "", 1)


def _man_drop_views_table(m=_FIX_MANIFEST):
    """Remove the WHOLE [views] table (R2): an ABSENT table is 1.0.0-valid-empty (oracle-graded VALID at
    1c90fbb), distinct from U9 which drops only the DECISIONS.md view ROW. [views] is the last table, so the
    canonical form is the manifest truncated at its header."""
    return m[:m.index("[views]\n")].rstrip("\n") + "\n"


def _cnt(c=_FIX_COUNTERS):
    return c


def _cnt_gov(md=0):
    c = _FIX_COUNTERS.replace("PD = 0\n", "MA = 0\nMD = {}\nPD = 0\n".format(md), 1)
    return c


def _cnt_ds(pp=0):
    return _FIX_COUNTERS.replace("RF = 0\n", "PP = {}\nRF = 0\n".format(pp), 1)


def _cnt_govds(md=1, pp=2):
    c = _cnt_gov(md=md)
    return c.replace("RF = 0\n", "PP = {}\nRF = 0\n".format(pp), 1)


def _cnt_da():
    c = _FIX_COUNTERS.replace("BI = 0\n", "AR = 0\nBI = 0\n", 1)
    c = c.replace("HO = 0\n", "GR = 0\nHO = 0\n", 1)
    return c.replace("WL = 0\n", "RL = 0\nWL = 0\nWV = 0\n", 1)


def _run_opf(argv, env):
    """Run the real dispatcher isolated (-I -B); preserve its output for discriminating assertions."""
    script = Path(__file__).resolve().parent / "opf.py"
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(script)] + list(argv),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="backslashreplace", timeout=180, env=env)
    if proc.returncode not in (EXIT_OK, EXIT_FINDING, EXIT_ERROR):
        raise OSError("opf child returned unexpected status {}".format(proc.returncode))
    return proc.returncode, proc.stdout + proc.stderr


def _snapshot(root):
    """Read the whole fixture tree (excluding .git), keyed by relpath, for byte-equality assertions."""
    result = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        st = path.lstat()
        rel = path.relative_to(root).as_posix()
        if stat.S_ISREG(st.st_mode):
            result[rel] = path.read_bytes()
        elif stat.S_ISDIR(st.st_mode):
            result[rel] = "dir"
        else:
            result[rel] = ("other", st.st_mode)
    return result


def _round4_tests(opf, check):
    """Fault-injected Git observations; no permissions-based skip or clean-on-error path."""
    from types import SimpleNamespace
    from unittest.mock import patch

    obs = opf._opf_observe
    outcome = obs._GitOutcome
    target = ".working/CONTRIBUTIONS.md"

    guard = opf._opf_write_guard

    def refuses(call):
        try:
            call()
        except guard.WriteGuardError:
            return True
        return False

    with patch.object(obs, "_git_path", return_value="git"), \
            patch.object(obs, "_filter_neutralizing_config", return_value=[]):
        for source in (".gitignore", ".git/info/exclude", "core.excludesFile"):
            warning = "warning: unable to access '{}': Permission denied\n".format(source)
            for rc, payload in ((1, b""), (0, b"./.working/CONTRIBUTIONS.md\x00")):
                with patch.object(obs, "_run_git", return_value=outcome(True, rc, payload, warning)):
                    check("R4 ignore diagnostic refuses: {} rc={}".format(source, rc),
                          refuses(lambda: guard.check_ignored("/fixture", [target], "upgrade")))
        with patch.object(obs, "_run_git", return_value=outcome(True, 1, b"", "")):
            check("R4 quiet non-match passes",
                  not refuses(lambda: guard.check_ignored("/fixture", [target], "upgrade")))

        for where in ("matching", "expanded"):
            def status(_git, _root, args, **kwargs):
                if "rev-parse" in args:
                    return outcome(True, 0, b"\n", "")
                if "ls-files" in args:
                    return outcome(True, 0, b"", "")
                expanded = "--ignored=traditional" in args
                err = "warning: cannot read ignore input\n" if expanded == (where == "expanded") else ""
                raw = b"" if expanded else b"!! .working/\x00"
                return outcome(True, 0, raw, err)
            with patch.object(obs, "_run_git", side_effect=status):
                check("R4 {} status diagnostic refuses".format(where),
                      refuses(lambda: guard.probe_dirty("git", "/fixture", [target], None, "upgrade")))

        for tag in (b"H", b"S", b"h", b"s"):
            def index_flags(_git, _root, args, **kwargs):
                raw = tag + b" " + os.fsencode(target) + b"\x00" if "ls-files" in args else b""
                return outcome(True, 0, raw, "")
            with patch.object(obs, "_run_git", side_effect=index_flags):
                check("R4 index flag {!r}".format(tag),
                      refuses(lambda: guard.probe_dirty("git", "/fixture", [target], None, "upgrade"))
                      == (tag != b"H"))
        for tag in (b"M", b"m"):
            def unmerged(_git, _root, args, **kwargs):
                raw = ((tag + b" " + os.fsencode(target) + b"\x00") * 3
                       if "ls-files" in args else
                       b"UU " + os.fsencode(target) + b"\x00" if "status" in args else b"\n")
                return outcome(True, 0, raw, "")
            with patch.object(obs, "_run_git", side_effect=unmerged):
                try:
                    guard.probe_dirty("git", "/fixture", [target], None, "upgrade")
                except guard.WriteGuardError as exc:
                    check("R5 unmerged {!r} names the conflict".format(tag),
                          target in str(exc) and "is unmerged; resolve the conflict" in str(exc)
                          and "malformed" not in str(exc))
                else:
                    check("R5 unmerged {!r} refuses".format(tag), False)
        for bad in (b"H", b"H path", b"\x00", b"? path\x00"):
            def bad_flags(_git, _root, args, **kwargs):
                return outcome(True, 0, bad if "ls-files" in args else b"", "")
            with patch.object(obs, "_run_git", side_effect=bad_flags):
                check("R4 malformed index flags {!r} refuse".format(bad),
                      refuses(lambda: guard.probe_dirty("git", "/fixture", [target], None, "upgrade")))

    res = SimpleNamespace(store_root=Path("/fixture"), product_root=None, machine_rel=".working/toml")
    for views in (5, [{"a": 1}]):
        text = opf._upgrade_partial_recovery_text(res, {"views": views})
        check("R4 malformed views {!r} retain recovery guidance".format(views),
              "Cannot derive candidate destinations" in text and "inspect only" not in text
              and "Never run a whole-tree restore" in text)

    lease = ".working/toml/lease.toml"
    for pair in (b" M", b"M ", b" D"):
        try:
            guard.parse_porcelain(pair + b" " + os.fsencode(lease) + b"/child\x00",
                                  b"", lease, [lease], "upgrade")
        except guard.WriteGuardError as exc:
            check("R4 tracked lease descendant {!r}".format(pair), "TRACKED" in str(exc))
        else:
            check("R4 tracked lease descendant {!r}".format(pair), False)
    for pair in (b"??", b"!!"):
        check("R4 held lease descendant {!r}".format(pair),
              guard.parse_porcelain(pair + b" " + os.fsencode(lease) + b"/child\x00",
                                    b"", lease, [lease], "upgrade") == [])


def _suite():
    """Keep caller HOME/XDG out of fixture reads, including in-process production helpers."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return _suite_isolated()


def _suite_isolated():
    """Build byte-pinned 1.0.0 fixtures and drive `opf upgrade` over them, asserting the spec-9.2 contract."""
    try:
        import tomllib
        import _opf_check
        import _opf_emit
        import _opf_store
        import opf
        opf._bootstrap()

        failures = []
        checked = []

        def check(label, condition):
            checked.append(label)
            if not condition:
                failures.append(label)

        _round4_tests(opf, check)

        # --- fixture canonicity (the upgrade's own precondition), extended to every frozen / derived fixture:
        # each manifest and counters literal must round-trip through the canonical emitter, so an emitter
        # change that would break the field precondition (or a mis-authored derivation) fails HERE.
        def canon_man(label, text):
            check("canonical manifest: " + label,
                  _opf_emit.emit_checked(tomllib.loads(text)) == text)

        def canon_cnt(label, text):
            check("canonical counters: " + label,
                  _opf_emit.emit_checked(tomllib.loads(text)) == text)

        canon_man("baseline", _FIX_MANIFEST)
        canon_man("governance", _man_gov())
        canon_man("decision_support", _man_ds())
        canon_man("gov+ds", _man_ds(_man_gov()))
        canon_man("delivery_assurance", _man_da())
        canon_man("drop-ds-key", _man_drop_dskey())
        canon_man("drop-decisions", _man_drop_decisions())
        canon_man("drop-both", _man_drop_decisions(_man_drop_dskey()))
        canon_man("add-contribution", _man_add_contribution())
        canon_man("add-md", _man_add_md())
        canon_man("gov-no-md", _man_gov_no_md())
        canon_man("drop-modules-table", _man_drop_modules_table())
        canon_man("drop-views-table", _man_drop_views_table())
        canon_man("drop-both-tables", _man_drop_views_table(_man_drop_modules_table()))
        canon_cnt("baseline", _FIX_COUNTERS)
        canon_cnt("governance", _cnt_gov())
        canon_cnt("gov-md1", _cnt_gov(md=1))
        canon_cnt("decision_support", _cnt_ds())
        canon_cnt("ds-pp2", _cnt_ds(pp=2))
        canon_cnt("gov+ds", _cnt_govds())
        canon_cnt("delivery_assurance", _cnt_da())
        canon_cnt("gov-ma-only", _cnt_gov_ma_only())
        for label, text in (("md-index", _FIX_MD_INDEX), ("pp-index", _FIX_PP_INDEX)):
            check("canonical index: " + label,
                  _opf_emit.emit_checked(tomllib.loads(text)) == text)
        check("frozen fixture declares spec_version 1.0.0",
              tomllib.loads(_FIX_MANIFEST)["devprocess"]["spec_version"] == "1.0.0")
        check("frozen fixture DECISIONS.md is a 2-source 1.0.0 view",
              tomllib.loads(_FIX_MANIFEST)["views"]["DECISIONS.md"]["sources"]
              == ["pending_decision", "autonomous_decision"])
        check("frozen fixture [types] carries only 1.0.0 baseline types (no MD/PP/CN)",
              all(t not in tomllib.loads(_FIX_MANIFEST)["types"]
                  for t in ("maintainer_decision", "preference_pattern", "contribution")))

        def git_call(store, args):
            proc = subprocess.run(
                ["git", "-C", str(store), "-c", "init.templateDir=", "-c", "init.defaultBranch=main",
                 "-c", "user.email=t@t", "-c", "user.name=t",
                 # F-367: no DETACHED auto-gc/auto-maintenance may outlive a fixture commit and
                 # churn .git while a later read or the teardown rmtree traverses it.
                 "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + list(args),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="backslashreplace", timeout=120, env=env_holder["env"])
            if proc.returncode != 0:
                raise OSError("fixture git failed at {!r}: {}".format(str(store), proc.stderr))
            return proc.stdout

        env_holder = {}

        def machdir(store):
            return store / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR

        def build_store(store, manifest=_FIX_MANIFEST, counters=_FIX_COUNTERS, extra_files=None,
                        commit=True):
            """Write a frozen 1.0.0 store beneath `store` and commit it (doctor needs a committed HEAD).
            `extra_files` (relpath -> text) adds or overrides machine-store files (extra index types, a
            populated index, a lease). Returns the machine-store dir."""
            mach = machdir(store)
            mach.mkdir(parents=True)
            git_call(store, ["init"])
            (mach / _opf_store.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
            (mach / _opf_check.COUNTERS_NAME).write_text(counters, encoding="utf-8")
            (mach / _opf_check.VERSION_NAME).write_text(_FIX_VERSION, encoding="utf-8")
            (mach / _opf_check.WORKLOG_NAME).write_text(_FIX_WORKLOG, encoding="utf-8")
            for tname in _FIX_INDEX_TYPES:
                (mach / (tname + _opf_check.INDEX_SUFFIX)).write_text(_FIX_INDEX, encoding="utf-8")
            for rel, data in (extra_files or {}).items():
                (mach / rel).write_text(data, encoding="utf-8")
            (store / _opf_store.POINTER_REL).write_text('[store]\ntarget = "dir:."\n', encoding="utf-8")
            (store / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            if commit:
                git_call(store, ["--literal-pathspecs", "add", "-A"])
                git_call(store, ["commit", "-m", "seed 1.0.0 store"])
            return mach

        def idx(t):
            return t + _opf_check.INDEX_SUFFIX

        def upgrade(store):
            return _run_opf(["upgrade", "--root", str(store)], env_holder["env"])

        def flipped_upgrade(store, flip):
            """Run a controlled regression in an isolated child; the normal fixture is unchanged on refusal."""
            script = (
                "import os, sys\nsys.path.insert(0, {!r})\nimport opf\nopf._bootstrap()\n"
                "guard = opf._opf_write_guard\n".format(
                    str(Path(__file__).resolve().parent))
                + flip + "\nsys.exit(opf._cmd_upgrade(['--root', sys.argv[1]]))\n")
            proc = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(store)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="backslashreplace", timeout=180,
                env=env_holder["env"])
            return proc.returncode, proc.stdout + proc.stderr

        no_ignored_filter = (
            "original = guard.parse_porcelain\n"
            "def parse(raw, prefix, lease, specs, verb):\n"
            "    extra = [os.fsdecode(r[3:]).removeprefix(os.fsdecode(prefix)).rstrip('/')\n"
            "             for r in raw.split(b'\\x00') if r.startswith(b'!! ')]\n"
            "    return original(raw, prefix, lease, list(specs) + extra, verb)\n"
            "guard.parse_porcelain = parse\n")
        whole_store_scope = (
            "original = opf._upgrade_write_scope\n"
            "def scope(*args):\n"
            "    plan = original(*args)\n"
            "    plan['store'] = ('.working',)\n"
            "    return plan\n"
            "opf._upgrade_write_scope = scope\n")

        def doctor(store):
            return _run_opf(["doctor", "--root", str(store)], env_holder["env"])

        def man_of(mach):
            return tomllib.loads((mach / _opf_store.MANIFEST_NAME).read_text(encoding="utf-8"))

        def cnt_of(mach):
            return tomllib.loads((mach / _opf_check.COUNTERS_NAME).read_text(encoding="utf-8"))["counters"]

        with tempfile.TemporaryDirectory(prefix="opf-upgrade-gate-") as temporary:
            base = Path(temporary).resolve()
            home = base / "home"
            home.mkdir()
            # Hermetic fixture env (test-hermeticity): an inherited GIT_INDEX_FILE / GIT_DIR
            # (git exports these to hook children) would redirect git_call's init/add/commit
            # into the CALLER's repository; route through the pack's allowlist scrub, HOME
            # re-pinned to the fixture home (the scrub carries only PATH and HOME, so the
            # XDG_CONFIG_HOME / XDG_CONFIG_DIRS drop this fixture needs is kept too).
            import _opf_observe
            env_holder["env"] = dict(_opf_observe._scrubbed_env(), HOME=str(home))

            # U1) Happy path: 1.0.0 -> 1.2.0, doctor VALID, exact delta applied.
            s1 = base / "u1-happy"
            s1.mkdir()
            mach1 = build_store(s1)
            rc, out = upgrade(s1)
            check("U1 upgrade exits 0", rc == EXIT_OK)
            check("U1 reports uncommitted, not staged or committed",
                  "uncommitted, NOT staged or committed" in out and '"event": "upgraded"' in out)
            man1 = man_of(mach1)
            check("U1 base table renamed to [opf]", "opf" in man1 and "devprocess" not in man1)
            check("U1 discovery token renamed to opf", man1["opf"].get("standard") == "opf")
            check("U1 spec_version bumped to the tooling spec_version",
                  man1["opf"]["spec_version"] == _opf_store.SUPPORTED_SPEC_VERSION)
            check("U1 base table body otherwise carried over", all(
                man1["opf"].get(k) == v for k, v in (
                    ("layout", "inline"), ("posture", "required"), ("import_status", "none"))))
            check("U1 decision_support module retired", "decision_support" not in man1["modules"])
            check("U1 three baseline type rows added", all(
                man1["types"].get(t) == {"namespace": _opf_store.BASELINE_TYPES[t]}
                for t in ("contribution", "maintainer_decision", "preference_pattern")))
            check("U1 two new view rows added",
                  "CONTRIBUTIONS.md" in man1["views"] and "DECISIONS.toml" in man1["views"])
            check("U1 DECISIONS.md view widened to the four 1.1.0 sources",
                  man1["views"]["DECISIONS.md"]["sources"]
                  == ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"])
            check("U1 decisions_view widened", '"decisions_view": "widened"' in out)
            check("U1 pre_declared_types empty", '"pre_declared_types": []' in out)
            cnt1 = cnt_of(mach1)
            check("U1 counters extended CN/MD/PP = 0",
                  cnt1.get("CN") == 0 and cnt1.get("MD") == 0 and cnt1.get("PP") == 0)
            check("U1 three empty indexes created", all(
                (mach1 / idx(t)).is_file()
                for t in ("contribution", "maintainer_decision", "preference_pattern")))
            drc, dout = doctor(s1)
            check("U1 upgraded store is doctor-VALID", drc == EXIT_OK and "integrity: VALID" in dout)

            # U2) Idempotence: a second run is a byte no-op reporting already-current.
            before = _snapshot(s1)
            rc2, out2 = upgrade(s1)
            check("U2 idempotent second run exits 0", rc2 == EXIT_OK)
            check("U2 idempotent second run reports no-op", "already at spec_version {}".format(
                _opf_store.SUPPORTED_SPEC_VERSION) in out2)
            check("U2 idempotent second run is a byte no-op", _snapshot(s1) == before)

            # U28) OPF-D2B PR3a: the 1.1.0 -> 1.2.0 schema delta changes only spec_version.
            # This fixture has current declared views, so regeneration leaves their bytes unchanged;
            # every other manifest field, the counters, every index, version, and worklog stay byte-
            # identical, no init.toml provenance is fabricated, the store is doctor-VALID, and a second
            # run is a byte no-op. Fails without the 1.1.0 origin (the 1.0.0 planner refuses [opf]).
            s28 = base / "u28-d2a"
            s28.mkdir()
            mach28 = build_store(s28)
            rc28a, _out28a = upgrade(s28)
            man28 = man_of(mach28)
            man28["opf"]["spec_version"] = "1.1.0"
            (mach28 / _opf_store.MANIFEST_NAME).write_text(_opf_emit.emit_checked(man28),
                                                            encoding="utf-8")
            git_call(s28, ["--literal-pathspecs", "add", "-A"])
            git_call(s28, ["commit", "-m", "a 1.1.0 store"])
            before28 = _snapshot(s28)
            rc28, out28 = upgrade(s28)
            check("U28 1.1.0 store upgrades (exit 0)", rc28a == EXIT_OK and rc28 == EXIT_OK)
            check("U28 reports the 1.1.0 origin", '"from": "1.1.0"' in out28
                  and '"decisions_view": "unchanged"' in out28)
            man28b = man_of(mach28)
            check("U28 spec_version bumped", man28b["opf"]["spec_version"]
                  == _opf_store.SUPPORTED_SPEC_VERSION)
            man28b["opf"]["spec_version"] = "1.1.0"
            check("U28 manifest otherwise unchanged", man28b == man28)
            after28 = _snapshot(s28)
            mrel = "{}/{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR,
                                     _opf_store.MANIFEST_NAME)
            check("U28 current-view fixture changes only the manifest",
                  {k: v for k, v in after28.items() if k != mrel}
                  == {k: v for k, v in before28.items() if k != mrel})
            check("U28 no provenance fabricated", not (mach28 / _opf_check.INIT_PROVENANCE_NAME).exists())
            drc28, dout28 = doctor(s28)
            check("U28 upgraded 1.1.0 store is doctor-VALID", drc28 == EXIT_OK and "integrity: VALID"
                  in dout28)
            again28 = _snapshot(s28)
            rc28c, out28c = upgrade(s28)
            check("U28 second run is a byte no-op", rc28c == EXIT_OK and _snapshot(s28) == again28
                  and "nothing to upgrade" in out28c)

            # U3) governance=true, MA+MD rows, empty MA/MD indexes, MA/MD counters.
            s3 = base / "u3-gov"
            s3.mkdir()
            mach3 = build_store(s3, manifest=_man_gov(), counters=_cnt_gov(),
                                extra_files={idx("maintainer_action"): _FIX_INDEX,
                                             idx("maintainer_decision"): _FIX_INDEX})
            ma_before = (mach3 / idx("maintainer_action")).read_bytes()
            md_before = (mach3 / idx("maintainer_decision")).read_bytes()
            rc3, out3 = upgrade(s3)
            check("U3 upgrade exits 0", rc3 == EXIT_OK)
            check("U3 only contribution+preference_pattern indexes created",
                  '"created_indexes": ["contribution", "preference_pattern"]' in out3)
            man3 = man_of(mach3)
            check("U3 governance stays true", man3["modules"].get("governance") is True)
            check("U3 MA stays module-tier", man3["types"].get("maintainer_action") == {"namespace": "MA"})
            check("U3 MD row preserved", man3["types"].get("maintainer_decision") == {"namespace": "MD"})
            check("U3 MA/MD indexes preserved byte-for-byte",
                  (mach3 / idx("maintainer_action")).read_bytes() == ma_before
                  and (mach3 / idx("maintainer_decision")).read_bytes() == md_before)
            drc3, dout3 = doctor(s3)
            check("U3 doctor-VALID", drc3 == EXIT_OK and "integrity: VALID" in dout3)

            # U4) governance=true, POPULATED maintainer_decision index, MD=1.
            s4 = base / "u4-gov-populated"
            s4.mkdir()
            mach4 = build_store(s4, manifest=_man_gov(), counters=_cnt_gov(md=1),
                                extra_files={idx("maintainer_action"): _FIX_INDEX,
                                             idx("maintainer_decision"): _FIX_MD_INDEX})
            md4_before = (mach4 / idx("maintainer_decision")).read_bytes()
            rc4, out4 = upgrade(s4)
            check("U4 upgrade exits 0", rc4 == EXIT_OK)
            check("U4 populated MD index preserved byte-for-byte",
                  (mach4 / idx("maintainer_decision")).read_bytes() == md4_before)
            check("U4 MD counter high-water preserved", cnt_of(mach4).get("MD") == 1)
            drc4, dout4 = doctor(s4)
            check("U4 doctor-VALID", drc4 == EXIT_OK and "integrity: VALID" in dout4)

            # U5) decision_support=true, PP row, empty PP index, PP counter.
            s5 = base / "u5-ds"
            s5.mkdir()
            mach5 = build_store(s5, manifest=_man_ds(), counters=_cnt_ds(),
                                extra_files={idx("preference_pattern"): _FIX_INDEX})
            pp5_before = (mach5 / idx("preference_pattern")).read_bytes()
            rc5, out5 = upgrade(s5)
            check("U5 upgrade exits 0", rc5 == EXIT_OK)
            check("U5 only contribution+maintainer_decision indexes created",
                  '"created_indexes": ["contribution", "maintainer_decision"]' in out5)
            man5 = man_of(mach5)
            check("U5 decision_support module key removed", "decision_support" not in man5["modules"])
            check("U5 PP row preserved", man5["types"].get("preference_pattern") == {"namespace": "PP"})
            check("U5 PP index preserved", (mach5 / idx("preference_pattern")).read_bytes() == pp5_before)
            drc5, dout5 = doctor(s5)
            check("U5 doctor-VALID", drc5 == EXIT_OK and "integrity: VALID" in dout5)

            # U6) decision_support=true, POPULATED PP index (bare-active + RATIFIED assistant PP), PP=2.
            # This vector FAILS without the M5 creation-snapshot fix (the ratified PP at bare active).
            s6 = base / "u6-ds-ratified"
            s6.mkdir()
            mach6 = build_store(s6, manifest=_man_ds(), counters=_cnt_ds(pp=2),
                                extra_files={idx("preference_pattern"): _FIX_PP_INDEX})
            pp6_before = (mach6 / idx("preference_pattern")).read_bytes()
            rc6, out6 = upgrade(s6)
            check("U6 upgrade exits 0 (ratified PP admitted at rest, needs M5)", rc6 == EXIT_OK)
            check("U6 populated PP index preserved byte-for-byte",
                  (mach6 / idx("preference_pattern")).read_bytes() == pp6_before)
            check("U6 PP high-water preserved", cnt_of(mach6).get("PP") == 2)
            drc6, dout6 = doctor(s6)
            check("U6 doctor-VALID (the M5 pipeline regression)",
                  drc6 == EXIT_OK and "integrity: VALID" in dout6)

            # U7) governance AND decision_support, both populated.
            s7 = base / "u7-gov-ds"
            s7.mkdir()
            mach7 = build_store(s7, manifest=_man_ds(_man_gov()), counters=_cnt_govds(md=1, pp=2),
                                extra_files={idx("maintainer_action"): _FIX_INDEX,
                                             idx("maintainer_decision"): _FIX_MD_INDEX,
                                             idx("preference_pattern"): _FIX_PP_INDEX})
            md7_before = (mach7 / idx("maintainer_decision")).read_bytes()
            pp7_before = (mach7 / idx("preference_pattern")).read_bytes()
            rc7, out7 = upgrade(s7)
            check("U7 upgrade exits 0", rc7 == EXIT_OK)
            check("U7 only contribution index created", '"created_indexes": ["contribution"]' in out7)
            check("U7 both populated indexes preserved",
                  (mach7 / idx("maintainer_decision")).read_bytes() == md7_before
                  and (mach7 / idx("preference_pattern")).read_bytes() == pp7_before)
            check("U7 both high-waters preserved",
                  cnt_of(mach7).get("MD") == 1 and cnt_of(mach7).get("PP") == 2)
            drc7, dout7 = doctor(s7)
            check("U7 doctor-VALID", drc7 == EXIT_OK and "integrity: VALID" in dout7)

            # U8) baseline with the optional decision_support module key ABSENT.
            s8 = base / "u8-no-ds-key"
            s8.mkdir()
            mach8 = build_store(s8, manifest=_man_drop_dskey())
            rc8, out8 = upgrade(s8)
            check("U8 upgrade exits 0", rc8 == EXIT_OK)
            man8 = man_of(mach8)
            check("U8 [modules] unchanged (no decision_support key invented)",
                  "decision_support" not in man8["modules"])
            drc8, dout8 = doctor(s8)
            check("U8 doctor-VALID", drc8 == EXIT_OK and "integrity: VALID" in dout8)

            # U9) baseline with DECISIONS.md omitted from [views].
            s9 = base / "u9-no-decisions"
            s9.mkdir()
            mach9 = build_store(s9, manifest=_man_drop_decisions())
            rc9, out9 = upgrade(s9)
            check("U9 upgrade exits 0", rc9 == EXIT_OK)
            man9 = man_of(mach9)
            check("U9 DECISIONS.md neither widened nor created", "DECISIONS.md" not in man9["views"])
            check("U9 two new views still added and rendered",
                  "CONTRIBUTIONS.md" in man9["views"] and "DECISIONS.toml" in man9["views"]
                  and (mach9.parent / "DECISIONS.toml").is_file())
            check("U9 decisions_view reported not-declared", '"decisions_view": "not-declared"' in out9)
            drc9, dout9 = doctor(s9)
            check("U9 doctor-VALID", drc9 == EXIT_OK and "integrity: VALID" in dout9)

            # U10) U8 and U9 combined.
            s10 = base / "u10-both"
            s10.mkdir()
            mach10 = build_store(s10, manifest=_man_drop_decisions(_man_drop_dskey()))
            rc10, out10 = upgrade(s10)
            check("U10 upgrade exits 0", rc10 == EXIT_OK)
            man10 = man_of(mach10)
            check("U10 modules unchanged and DECISIONS.md absent",
                  "decision_support" not in man10["modules"] and "DECISIONS.md" not in man10["views"])
            drc10, dout10 = doctor(s10)
            check("U10 doctor-VALID", drc10 == EXIT_OK and "integrity: VALID" in dout10)

            # U11) delivery_assurance=true (a representative non-migrated module).
            s11 = base / "u11-delivery"
            s11.mkdir()
            da_extra = {idx(t): _FIX_INDEX for t in ("artifact", "gate_run", "release", "waiver")}
            mach11 = build_store(s11, manifest=_man_da(), counters=_cnt_da(), extra_files=da_extra)
            da_before = {t: (mach11 / idx(t)).read_bytes()
                         for t in ("artifact", "gate_run", "release", "waiver")}
            rc11, out11 = upgrade(s11)
            check("U11 upgrade exits 0", rc11 == EXIT_OK)
            man11 = man_of(mach11)
            check("U11 every delivery_assurance row preserved value-exact", all(
                man11["types"].get(t) == {"namespace": ns} for t, ns in
                (("artifact", "AR"), ("gate_run", "GR"), ("release", "RL"), ("waiver", "WV"))))
            check("U11 every delivery_assurance index preserved byte-for-byte",
                  all((mach11 / idx(t)).read_bytes() == b for t, b in da_before.items()))
            check("U11 every delivery_assurance counter preserved",
                  all(cnt_of(mach11).get(ns) == 0 for ns in ("AR", "GR", "RL", "WV")))
            drc11, dout11 = doctor(s11)
            check("U11 doctor-VALID", drc11 == EXIT_OK and "integrity: VALID" in dout11)

            # U12) baseline plus an UNTRACKED well-formed foreign lease.toml.
            s12 = base / "u12-lease-held"
            s12.mkdir()
            mach12 = build_store(s12)
            (mach12 / _opf_check.LEASE_NAME).write_text(
                'acquired_at = "2026-01-01T00:00:00Z"\nholder = "peer-runner"\n'
                'operation = "upgrade"\nschema = 1\n', encoding="utf-8")   # untracked
            before12 = _snapshot(s12)
            rc12, out12 = upgrade(s12)
            check("U12 held lease refuses (exit 2)", rc12 == EXIT_ERROR)
            check("U12 refusal names the holder", "peer-runner" in out12 and "lease" in out12.lower())
            check("U12 lease NOT deleted", (mach12 / _opf_check.LEASE_NAME).is_file())
            check("U12 tree unchanged", _snapshot(s12) == before12)

            # U12b) An ignored lease reaches O_EXCL, with the never-seize remedy, never commit/move.
            s12b = base / "u12b-ignored-lease"
            s12b.mkdir()
            mach12b = build_store(s12b)
            lease12b = mach12b / _opf_check.LEASE_NAME
            lease12b.write_bytes((mach12 / _opf_check.LEASE_NAME).read_bytes())
            rel12b = lease12b.relative_to(s12b).as_posix()
            (s12b / ".git/info").mkdir()
            (s12b / ".git/info/exclude").write_text("/" + rel12b + "\n", encoding="utf-8")
            before12b = _snapshot(s12b)
            check("U12b fixture lease is ignored",
                  git_call(s12b, ["check-ignore", "--", rel12b]).strip() == rel12b)
            rc12b, out12b = upgrade(s12b)
            check("U12b ignored lease gets held-lease never-seize refusal",
                  rc12b == EXIT_ERROR and "peer-runner" in out12b
                  and "The lease is never seized (spec 5.7)" in out12b
                  and "confirmed NO opf run is live" in out12b
                  and "Commit your changes" not in out12b)
            flip12b, text12b = flipped_upgrade(s12b,
                "original = guard.parse_porcelain\n"
                "guard.parse_porcelain = lambda raw, prefix, lease, specs, verb: "
                "original(raw, prefix, None, specs, verb)\n")
            check("U12b FLIP excluding no lease restores the incorrect dirty-store remedy",
                  flip12b == EXIT_ERROR and "Commit your changes" in text12b)
            check("U12b both refusals preserve lease and tree", _snapshot(s12b) == before12b)

            # U12c) Non-regular leases are held too, including collapsed ignored directories.
            for ignored_lease in (False, True):
                sc = base / ("u12c-lease-directory-" + str(ignored_lease))
                sc.mkdir()
                mc = build_store(sc)
                lease = mc / _opf_check.LEASE_NAME
                lease.mkdir()
                (lease / "owner").write_bytes(b"held by another run\n")
                rel = lease.relative_to(sc).as_posix()
                if ignored_lease:
                    (sc / ".git/info").mkdir()
                    (sc / ".git/info/exclude").write_text("/" + rel + "/\n", encoding="utf-8")
                before = _snapshot(sc)
                rc, out = upgrade(sc)
                check("U12c directory lease gets never-seize refusal ({})".format(ignored_lease),
                      rc == EXIT_ERROR and "The lease is never seized (spec 5.7)" in out
                      and "Commit your changes" not in out)
                frc, fout = flipped_upgrade(sc,
                    "original = guard.parse_porcelain\n"
                    "guard.parse_porcelain = lambda raw, prefix, lease, specs, verb: "
                    "original(raw, prefix, None, specs, verb)\n")
                check("U12c FLIP directory lease gets dirt advice ({})".format(ignored_lease),
                      frc == EXIT_ERROR and "Commit your changes" in fout)
                check("U12c lease directory and bytes preserved ({})".format(ignored_lease),
                      lease.is_dir() and _snapshot(sc) == before)

            # U13) baseline plus a MALFORMED lease.toml (present is held, never absent).
            s13 = base / "u13-lease-malformed"
            s13.mkdir()
            mach13 = build_store(s13)
            (mach13 / _opf_check.LEASE_NAME).write_text("not valid = = = toml\n", encoding="utf-8")
            before13 = _snapshot(s13)
            rc13, out13 = upgrade(s13)
            check("U13 malformed lease refuses (exit 2)", rc13 == EXIT_ERROR)
            check("U13 lease NOT deleted", (mach13 / _opf_check.LEASE_NAME).is_file())
            check("U13 tree unchanged", _snapshot(s13) == before13)

            # U14) A canonical, uncommitted counters edit at a planned rewrite destination.
            s14 = base / "u14-dirty"
            s14.mkdir()
            mach14 = build_store(s14)
            (mach14 / _opf_check.COUNTERS_NAME).write_text(
                _FIX_COUNTERS.replace("BI = 0", "BI = 1"), encoding="utf-8")
            before14 = _snapshot(s14)
            rc14, out14 = upgrade(s14)
            check("U14 dirty store refuses BEFORE mutation (exit 2)", rc14 == EXIT_ERROR)
            check("U14 refusal names the dirty path", "counters.toml" in out14 and "clean" in out14)
            check("U14 advises commit-your-changes and offers no restore command (the dirt is the owner's)",
                  "Commit your changes" in out14 and "restore --staged" not in out14)
            check("U14 owner edit intact and tree unchanged", _snapshot(s14) == before14)

            # An existing index collision candidate is checked even though creation leaves it alone.
            collision_store = base / "dirty-existing-index"
            collision_store.mkdir()
            collision_mach = build_store(
                collision_store, manifest=_man_gov(), counters=_cnt_gov(),
                extra_files={idx("maintainer_action"): _FIX_INDEX,
                             idx("maintainer_decision"): _FIX_INDEX})
            collision_path = collision_mach / idx("maintainer_decision")
            collision_path.write_text(_FIX_INDEX + "\n", encoding="utf-8")
            collision_rel = collision_path.relative_to(collision_store).as_posix()
            collision_before = _snapshot(collision_store)
            collision_index = git_call(collision_store, ["ls-files", "--stage", "-z"])
            collision_head = git_call(collision_store, ["rev-parse", "HEAD"])
            collision_rc, collision_out = upgrade(collision_store)
            check("dirty existing index collision candidate refuses and is named",
                  collision_rc == EXIT_ERROR and collision_rel in collision_out
                  and "Commit your changes (or move them aside)" in collision_out
                  and "restore --staged" not in collision_out)
            check("dirty refusal describes the planned destinations and collision scope",
                  "planned schema and render destinations and index collision candidates "
                  "(store and product roots)" in collision_out)
            check("dirty collision refusal preserves owner bytes, index and HEAD",
                  _snapshot(collision_store) == collision_before
                  and git_call(collision_store, ["ls-files", "--stage", "-z"]) == collision_index
                  and git_call(collision_store, ["rev-parse", "HEAD"]) == collision_head)

            # U14b) An ignored pre-existing render target is owner work, not a clean destination.
            # FLIP: removing --ignored=matching from the probe must fail the refusal/advice/unchanged
            # assertions below. Moving the file aside is the supported route back to a clean upgrade.
            s14b = base / "u14b-ignored"
            s14b.mkdir()
            build_store(s14b)
            ignored_rel = opf._opf_views._spec_destination("CONTRIBUTIONS.md")[1]
            (s14b / ".git/info").mkdir()
            (s14b / ".git/info/exclude").write_text(
                "/{}\n/outside-build/\n".format(ignored_rel), encoding="utf-8")
            ignored_path = s14b / ignored_rel
            ignored_path.write_text("owner's pre-existing contribution notes\n", encoding="utf-8")
            outside = s14b / "outside-build"
            outside.mkdir()
            (outside / "cache").write_text("unrelated ignored content\n", encoding="utf-8")
            check("U14b fixture target is ignored by git",
                  git_call(s14b, ["check-ignore", "--", ignored_rel]).strip() == ignored_rel)
            before14b = _snapshot(s14b)
            index14b = git_call(s14b, ["ls-files", "--stage", "-z"])
            rc14b, out14b = upgrade(s14b)
            check("U14b ignored written path refuses BEFORE mutation (exit 2)", rc14b == EXIT_ERROR)
            check("U14b ignored path gets the ordinary dirty-store remedy",
                  ignored_rel in out14b and "Commit your changes (or move them aside)" in out14b
                  and "restore --staged" not in out14b)
            check("U14b ignored owner bytes and tree unchanged", _snapshot(s14b) == before14b)
            check("U14b index unchanged", git_call(s14b, ["ls-files", "--stage", "-z"]) == index14b)
            saved14b = base / "u14b-owner-notes"
            ignored_path.rename(saved14b)
            rc14c, out14c = upgrade(s14b)
            check("U14b absent destination still refuses its ignore rule",
                  rc14c == EXIT_ERROR and "ignored planned destinations" in out14c)
            (s14b / ".git/info/exclude").write_text("/outside-build/\n", encoding="utf-8")
            rc14c, out14c = upgrade(s14b)
            check("U14b moving owner work and correcting its ignore rule permits upgrade", rc14c == EXIT_OK)
            check("U14b saved owner bytes survive",
                  saved14b.read_bytes() == before14b[ignored_rel])
            check("U14b ignored content outside the written scope is untouched",
                  (outside / "cache").read_bytes() == before14b["outside-build/cache"])

            # U14b scope regressions: component-prefix siblings and declared unmanaged ignored content.
            # The .working.bak case also probes the old coarse pathspec directly: the new write plan
            # no longer selects .working, so this is the discriminating companion for that Git quirk.
            for sibling, version_view in ((".working.bak", False),
                                          (".working/CONTRIBUTIONS.md.bak", False),
                                          ("VERSIONS", True)):
                sc = base / ("u14b-sibling-" + sibling.replace("/", "-"))
                sc.mkdir()
                model = tomllib.loads(_FIX_MANIFEST)
                extra = {}
                if sibling.startswith(".working/"):
                    model["unmanaged"] = {"paths": [sibling]}
                if version_view:
                    import _opf_release
                    import _opf_changelog
                    changelog = "# Changelog\n\n## 0.1.0\n"
                    entries, _findings = _opf_changelog._changelog_entries(changelog)
                    model["views"]["VERSION"] = {
                        "kind": "deterministic", "sources": ["version"], "target": "VERSION"}
                    extra[_opf_check.VERSION_NAME] = _opf_emit.emit_checked({
                        "schema": 1, "summary": [{"covers": "0.1.0", "status": "published",
                            "digest": _opf_changelog.freeze_digest(entries[0][1])}], "release": [{
                            "version": "0.1.0", "date": "2026-01-01T00:00:00Z",
                            "worklog_span": [], "coverage_digest": _opf_release.coverage_digest([])}]})
                build_store(sc, manifest=_opf_emit.emit_checked(model), extra_files=extra)
                if version_view:
                    (sc / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
                    git_call(sc, ["add", "--", "CHANGELOG.md"])
                    git_call(sc, ["commit", "-m", "release heading"])
                (sc / ".git/info").mkdir()
                (sc / ".git/info/exclude").write_text("/" + sibling + "/\n", encoding="utf-8")
                owner = sc / sibling / "owner"
                owner.parent.mkdir(parents=True)
                owner.write_bytes(b"unrelated ignored owner bytes\n")
                if sibling == ".working.bak":
                    raw = git_call(sc, ["--literal-pathspecs", "status", "--porcelain=v1", "-z",
                                        "--untracked-files=all", "--ignored=matching", "--no-renames",
                                        "--", ".working"]).encode("utf-8")
                    check("U14b .working.bak fixture exercises Git's prefix-sibling record",
                          b"!! .working.bak/\x00" in raw)
                    check("U14b .working.bak component filter excludes the real Git record",
                          opf._opf_write_guard.parse_porcelain(raw, "", None, [".working"], "upgrade") == [])
                    check("U14b .working.bak FLIP admitting the sibling surfaces it as dirt",
                          opf._opf_write_guard.parse_porcelain(raw, "", None, [".working", sibling], "upgrade")
                          == [sibling + "/"])
                else:
                    flipped_rc, flipped_out = flipped_upgrade(sc, no_ignored_filter)
                    check("U14b {} FLIP removing ignored filtering refuses".format(sibling),
                          flipped_rc == EXIT_ERROR and sibling in flipped_out
                          and "Commit your changes" in flipped_out)
                src, sout = upgrade(sc)
                check("U14b {} upgrades to doctor-VALID".format(sibling),
                      src == EXIT_OK and "doctor-VALID" in sout)
                check("U14b {} owner bytes survive".format(sibling),
                      owner.read_bytes() == b"unrelated ignored owner bytes\n")

            su = base / "u14b-unmanaged"
            su.mkdir()
            unmanaged_model = tomllib.loads(_FIX_MANIFEST)
            unmanaged_model["unmanaged"] = {"paths": [".working/cache"]}
            build_store(su, manifest=_opf_emit.emit_checked(unmanaged_model))
            (su / ".git/info").mkdir()
            (su / ".git/info/exclude").write_text("/.working/cache/\n", encoding="utf-8")
            cache = su / ".working/cache/output.bin"
            cache.parent.mkdir()
            cache.write_bytes(b"unmanaged output\n")
            flipped_rc, flipped_out = flipped_upgrade(su, whole_store_scope)
            check("U14b unmanaged FLIP whole-subtree scope refuses the ignored cache",
                  flipped_rc == EXIT_ERROR and ".working/cache/" in flipped_out
                  and "Commit your changes" in flipped_out)
            urc, uout = upgrade(su)
            check("U14b declared unmanaged ignored content upgrades to doctor-VALID",
                  urc == EXIT_OK and "doctor-VALID" in uout)
            check("U14b unmanaged bytes survive", cache.read_bytes() == b"unmanaged output\n")
            import shlex

            def scoped_staging(text):
                commands = [shlex.split(line) for line in text.splitlines()
                            if line.startswith("  git -C ") and " add " in line]
                return bool(commands) and all(
                    "--" in command and not any(
                        p in (".working", ".working/cache") or p.endswith("/lease.toml")
                        for p in command[command.index("--") + 1:])
                    for command in commands)

            check("U14b staging uses individual destinations, excludes cache and lease",
                  scoped_staging(uout))
            for force in ("", "-f "):
                broad = "  git -C {} --literal-pathspecs add {}-- .working\n".format(
                    shlex.quote(str(su)), force)
                check("U14b FLIP broad add {}fails scoped staging assertion".format(force),
                      not scoped_staging(uout + broad))
            check("U14b missing staging advice fails scoped staging assertion", not scoped_staging(""))

            # A collapsed ignored ancestor must not hide a write destination, and a directory at a
            # destination is a collision, even when Git reports it with a trailing slash.
            for collision in ("ancestor", "directory"):
                sa = base / ("u14b-" + collision)
                sa.mkdir()
                build_store(sa)
                (sa / ".git/info").mkdir()
                target = sa / ignored_rel
                if collision == "ancestor":
                    rule = "/.working/\n"
                    target.write_bytes(b"owner content\n")
                else:
                    rule = "/" + ignored_rel + "/\n"
                    target.mkdir()
                    (target / "owner").write_bytes(b"owner content\n")
                (sa / ".git/info/exclude").write_text(rule, encoding="utf-8")
                before_collision = _snapshot(sa)
                arc, aout = upgrade(sa)
                check("U14b {} refuses before mutation".format(collision),
                      arc == EXIT_ERROR and ignored_rel in aout and "Commit your changes" in aout)
                check("U14b {} preserves owner content and tree".format(collision),
                      _snapshot(sa) == before_collision)

            # U14c) Git -z paths and --show-prefix carry filesystem bytes, not necessarily UTF-8.
            # Use real nested repositories and prove matching mode actually emits the ignored record.
            # The flip restores the old lossy prefix round trip AND its silent non-match fallback.
            lossy_prefix = (
                "def old_status_path(pbytes, prefix):\n"
                "    prefix_b = os.fsencode(prefix).decode('utf-8', 'replace').encode('utf-8')\n"
                "    return pbytes[len(prefix_b):] if prefix_b and pbytes.startswith(prefix_b) else pbytes\n"
                "guard.status_path = old_status_path\n")
            for case, target_rel in (
                    ("manifest", ".working/toml/manifest.toml"),
                    ("counters", ".working/toml/counters.toml"),
                    ("render", ignored_rel),
                    ("ancestor", ignored_rel)):
                repo = base / ("u14c-" + case)
                nested = repo / os.fsdecode(b"nested-\xff")
                build_store(nested, commit=False)
                (nested / ".git").rename(repo / ".git")
                git_call(repo, ["--literal-pathspecs", "add", "-A"])
                git_call(repo, ["commit", "-m", "seed non-UTF-8 nested store"])
                if case in ("render", "ancestor"):
                    (nested / target_rel).write_bytes(b"owner render destination\n")
                untrack = ".working" if case == "ancestor" else target_rel
                if case != "render":
                    git_call(nested, ["--literal-pathspecs", "rm", "--cached", "-r", "--", untrack])
                    git_call(repo, ["commit", "-m", "retain ignored owner content outside HEAD"])
                prefix = b"nested-\xff/"
                ignored = b".working/" if case == "ancestor" else os.fsencode(target_rel)
                (repo / ".git/info").mkdir(exist_ok=True)
                (repo / ".git/info/exclude").write_bytes(b"/" + prefix + ignored + b"\n")
                observed_prefix = opf._opf_observe._run_git("git", nested, ["rev-parse", "--show-prefix"])
                check("U14c {} real non-UTF-8 prefix".format(case),
                      observed_prefix.completed and observed_prefix.rc == 0
                      and observed_prefix.out == prefix + b"\n")
                observed = opf._opf_observe._run_git("git", nested,
                    ["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                     "--ignored=matching", "--no-renames", "--", target_rel])
                check("U14c {} real ignored record (collapsed for ancestor)".format(case),
                      observed.completed and observed.rc == 0
                      and b"!! " + prefix + ignored in observed.out.split(b"\x00"))
                before = _snapshot(repo)
                before_index = (repo / ".git/index").read_bytes()
                before_head = git_call(repo, ["rev-parse", "HEAD"])
                rc, out = upgrade(nested)
                check("U14c {} refuses before mutation with dirty-store advice".format(case),
                      rc == EXIT_ERROR and target_rel in out and "Commit your changes" in out)
                check("U14c {} unchanged tree, index and HEAD".format(case),
                      _snapshot(repo) == before and (repo / ".git/index").read_bytes() == before_index
                      and git_call(repo, ["rev-parse", "HEAD"]) == before_head)
                flipped_rc, flipped_out = flipped_upgrade(nested, lossy_prefix)
                if case == "ancestor":
                    check("U14c ancestor FLIP loses dirt advice but the absent-path guard still refuses",
                          flipped_rc == EXIT_ERROR and "Commit your changes" not in flipped_out
                          and "ignored planned destinations" in flipped_out and _snapshot(repo) == before)
                else:
                    check("U14c {} FLIP loses pre-mutation refusal and changes manifest".format(case),
                          flipped_rc in (EXIT_OK, EXIT_ERROR) and "Commit your changes" not in flipped_out
                          and (nested / ".working/toml/manifest.toml").read_bytes()
                              != before[os.fsdecode(prefix) + ".working/toml/manifest.toml"])

            # The stdin transport must drain output while feeding more than a pipe buffer.
            transport = opf._opf_observe._capture_bounded(
                [sys.executable, "-I", "-B", "-c",
                 "import sys; sys.stdout.buffer.write(b'o' * 131072); sys.stdout.buffer.flush(); "
                 "sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                opf._opf_observe._scrubbed_env(), 10, 524288, input_bytes=b"i" * 131072)
            check("U14d bounded stdin transport drains output without deadlock",
                  transport.completed and transport.rc == 0
                  and transport.out == b"o" * 131072 + b"i" * 131072)

            # U14d) Absent destinations need an ignore-rule probe: status has no entry to report.
            for label, ignored_rel in (("view", ".working/CONTRIBUTIONS.md"),
                                       ("index", ".working/toml/contribution.index.toml"),
                                       ("product", "VERSION")):
                si = base / ("u14d-absent-" + label)
                si.mkdir()
                model = tomllib.loads(_FIX_MANIFEST)
                if label == "product":
                    model["views"]["VERSION"] = {
                        "kind": "deterministic", "sources": ["version"], "target": "VERSION"}
                build_store(si, manifest=_opf_emit.emit_checked(model))
                (si / ".git/info").mkdir()
                (si / ".git/info/exclude").write_text("/" + ignored_rel + "\n", encoding="utf-8")
                before = _snapshot(si)
                irc, iout = upgrade(si)
                check("U14d {} ignore rule refuses before mutation".format(label),
                      irc == EXIT_ERROR and "ignored planned destinations" in iout and ignored_rel in iout
                      and _snapshot(si) == before)
                frc, fout = flipped_upgrade(si, "guard.check_ignored = lambda *args: None\n")
                check("U14d {} FLIP removing check reaches manifest mutation".format(label),
                      _snapshot(si) != before and "ignored planned destinations" not in fout)
                if label != "product":
                    check("U14d {} FLIP bypasses conservative ignore refusal and reaches doctor-VALID".format(label),
                          frc == EXIT_OK and "doctor-VALID" in fout)

            # R4: execute the printed command under ignore configuration that the probes omit.
            import shlex
            for ignored_by in ("global", "system", "default", "xdg", "repository", "indexed"):
                sf = base / ("r4-force-" + ignored_by)
                sf.mkdir()
                build_store(sf)
                rule = "/.working/CONTRIBUTIONS.md\n"
                real_env = dict(env_holder["env"])
                if ignored_by == "indexed":
                    (sf / ".gitignore").write_text(rule, encoding="utf-8")
                    git_call(sf, ["add", "--", ".gitignore"])
                    git_call(sf, ["commit", "-m", "indexed ignore fixture"])
                    git_call(sf, ["update-index", "--skip-worktree", "--", ".gitignore"])
                    (sf / ".gitignore").unlink()
                elif ignored_by in ("default", "xdg"):
                    ignore_home = base / ("r5-home-" + ignored_by)
                    ignore_home.mkdir()
                    real_env["HOME"] = str(ignore_home)
                    config_home = ignore_home / ".config" if ignored_by == "default" else base / "r5-xdg"
                    if ignored_by == "xdg":
                        # Advice-only coverage: _scrubbed_env drops XDG_CONFIG_HOME, so this case
                        # does not discriminate core.excludesFile neutralization in the probes.
                        # The printed add command still runs with this override in real_env.
                        real_env["XDG_CONFIG_HOME"] = str(config_home)
                    (config_home / "git").mkdir(parents=True)
                    (config_home / "git/ignore").write_text(rule, encoding="utf-8")
                else:
                    excludes = base / ("r4-" + ignored_by + "-excludes")
                    excludes.write_text(rule, encoding="utf-8")
                    config = base / ("r4-" + ignored_by + "-config")
                    config.write_text('[core]\nexcludesFile = "{}"\n'.format(excludes), encoding="utf-8")
                    if ignored_by == "repository":
                        git_call(sf, ["config", "core.excludesFile", str(excludes)])
                    else:
                        real_env["GIT_CONFIG_" + ignored_by.upper()] = str(config)
                    if ignored_by == "system":
                        real_env.pop("GIT_CONFIG_NOSYSTEM", None)
                frc, fout = _run_opf(["upgrade", "--root", str(sf)], real_env)
                check("R4 {} reaches doctor-VALID with force advice".format(ignored_by),
                      frc == EXIT_OK and "doctor-VALID" in fout)
                commands = [shlex.split(line) for line in fout.splitlines() if line.startswith("  git -C ")
                            and " add " in line]
                check("R4 {} emits a scoped force command".format(ignored_by),
                      len(commands) == 1 and commands[0][1:4] == ["-C", str(sf), "--literal-pathspecs"]
                      and commands[0][4:7] == ["add", "-f", "--"]
                      and ".working/CONTRIBUTIONS.md" in commands[0]
                      and ".working" not in commands[0] and "-A" not in commands[0])
                for command in commands:
                    # Flip only the advice: ordinary add must reproduce the reported ignore failure.
                    ordinary = subprocess.run([a for a in command if a != "-f"], env=real_env,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                    check("R4 {} FLIP ordinary add refuses".format(ignored_by),
                          ordinary.returncode != 0 and b"ignored" in ordinary.stderr)
                    forced = subprocess.run(command, env=real_env, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, timeout=30)
                    check("R4 {} printed add -f stages the view".format(ignored_by),
                          forced.returncode == 0
                          and ".working/CONTRIBUTIONS.md" in
                          git_call(sf, ["ls-files", "--", ".working/CONTRIBUTIONS.md"]).splitlines())

            for flag in ("skip-worktree", "assume-unchanged"):
                sf = base / ("r4-hidden-" + flag)
                sf.mkdir()
                build_store(sf)
                rel = ".working/DECISIONS.md"
                (sf / rel).write_bytes(b"tracked view\n")
                git_call(sf, ["add", "--", rel])
                git_call(sf, ["commit", "-m", "tracked view fixture"])
                git_call(sf, ["update-index", "--" + flag, "--", rel])
                (sf / rel).write_bytes(b"owner edit hidden from status\n")
                before = _snapshot(sf)
                index_before = (sf / ".git/index").read_bytes()
                frc, fout = upgrade(sf)
                check("R4 {} refuses without changing tree or index".format(flag),
                      frc == EXIT_ERROR and "skip-worktree or assume-unchanged" in fout
                      and _snapshot(sf) == before and (sf / ".git/index").read_bytes() == index_before)

            # U15) [types.contribution] pre-declared: an impossible 1.0.0 shape.
            s15 = base / "u15-contribution-predeclared"
            s15.mkdir()
            mach15 = build_store(s15, manifest=_man_add_contribution(),
                                 extra_files={idx("contribution"): _FIX_INDEX})
            before15 = _snapshot(s15)
            rc15, out15 = upgrade(s15)
            check("U15 pre-declared contribution refuses (exit 2)", rc15 == EXIT_ERROR)
            check("U15 refusal names contribution as an impossible 1.0.0 shape",
                  "contribution" in out15 and "impossible" in out15)
            check("U15 tree unchanged", _snapshot(s15) == before15)

            # U16) governance=false plus [types.maintainer_decision]: a module-inconsistent 1.0.0 shape.
            s16 = base / "u16-md-no-gov"
            s16.mkdir()
            mach16 = build_store(s16, manifest=_man_add_md(),
                                 extra_files={idx("maintainer_decision"): _FIX_INDEX})
            before16 = _snapshot(s16)
            rc16, out16 = upgrade(s16)
            check("U16 module-inconsistent MD refuses (exit 2)", rc16 == EXIT_ERROR)
            check("U16 refusal names the module inconsistency",
                  "maintainer_decision" in out16 and "module" in out16.lower())
            check("U16 tree unchanged", _snapshot(s16) == before16)

            # U17) the above-tooling / non-canonical / NOT-ADOPTED / partial-1.2.0 triage refusals.
            s17a = base / "u17-above"
            s17a.mkdir()
            above_manifest = _FIX_MANIFEST.replace('spec_version = "1.0.0"', 'spec_version = "2.0.0"')
            build_store(s17a, manifest=above_manifest)
            rc17a, out17a = upgrade(s17a)
            check("U17 above-tooling store refused (exit 2)", rc17a == EXIT_ERROR)
            check("U17 above-tooling refusal names the reason", "ABOVE the {}".format(
                _opf_store.SUPPORTED_SPEC_VERSION) in out17a)

            s17b = base / "u17-noncanonical"
            s17b.mkdir()
            build_store(s17b, manifest="# hand-edited by an adopter\n" + _FIX_MANIFEST)
            rc17b, out17b = upgrade(s17b)
            check("U17 non-canonical manifest refused (exit 2)", rc17b == EXIT_ERROR)
            check("U17 non-canonical refusal names canonicity", "canonical" in out17b)

            s17c = base / "u17-not-adopted"
            s17c.mkdir()
            rc17c, out17c = upgrade(s17c)
            check("U17 not-adopted root is NOT APPLICABLE (exit 0)",
                  rc17c == EXIT_OK and "NOT APPLICABLE" in out17c)

            # partial 1.2.0 (F2): take the VALID 1.2.0 store from U1, break it, re-run: never a false no-op.
            (mach1 / idx("maintainer_decision")).unlink()
            rc17d, out17d = upgrade(s1)
            check("U17 partial 1.2.0 store is not a false rc-0 no-op (exit 2)", rc17d == EXIT_ERROR)
            check("U17 partial store fail-closed names not-doctor-VALID", "NOT doctor-VALID" in out17d)
            check("U17 partial-store recovery requires reconstructing the earlier write plan",
                  "earlier run's planned destinations" in out17d and "Exclude unmanaged paths and the lease"
                  in out17d and "restore --staged" not in out17d)
            check("U17 partial-store recovery warns against a whole-tree restore",
                  "Never run a whole-tree restore" in out17d)
            drc17d, dout17d = doctor(s1)
            check("U17 partial 1.2.0 store doctor is not VALID (exit 2)", drc17d == EXIT_ERROR)

            # U17b) Exceptions immediately after manifest replacement retain the entire planned scope.
            for suppress_recovery in (False, True):
                se = base / ("u17b-exception-" + str(suppress_recovery))
                se.mkdir()
                me = build_store(se)
                injection = (
                    "original = opf._upgrade_replace\n"
                    "def replace(fd, rel, data):\n"
                    "    original(fd, rel, data)\n"
                    "    if rel.endswith('/manifest.toml'):\n"
                    "        raise OSError('injected after manifest write')\n"
                    "opf._upgrade_replace = replace\n")
                if suppress_recovery:
                    injection += "opf._upgrade_recovery_text = lambda *args: ''\n"
                erc, eout = flipped_upgrade(se, injection)
                check("U17b injected exception actually follows the manifest write",
                      erc == EXIT_ERROR and "injected after manifest write" in eout
                      and man_of(me)["opf"]["spec_version"] == opf._UPGRADE_TO)
                import shlex
                restore = "git -C {} --literal-pathspecs restore --staged --worktree -- ".format(
                    shlex.quote(str(se))) + ".working/toml/counters.toml .working/toml/manifest.toml"
                removal = next((line for line in eout.splitlines() if ": rm -- " in line), "")
                has_plan = (restore in eout and str(se / ".working/CONTRIBUTIONS.md") in removal
                            and str(me / idx("contribution")) in removal)
                check("U17b {}planned restore/rm list".format("FLIP suppresses " if suppress_recovery else "prints "),
                      has_plan == (not suppress_recovery))
                check("U17b owned lease released after exception", not (me / _opf_check.LEASE_NAME).exists())
                rrc, rout = upgrade(se)
                check("U17b F2 derives candidates from the current manifest",
                      rrc == EXIT_ERROR and "Candidate store destinations" in rout
                      and ".working/CONTRIBUTIONS.md" in rout
                      and ".working/toml/contribution.index.toml" in rout
                      and "restore --staged" not in rout)
                frc, fout = flipped_upgrade(se,
                    "original = opf._upgrade_write_scope\n"
                    "def scope(*args):\n"
                    "    plan = original(*args)\n"
                    "    plan['store'] = ()\n"
                    "    return plan\n"
                    "opf._upgrade_write_scope = scope\n")
                candidate_lines = [line for line in fout.splitlines() if "Candidate store destinations" in line]
                check("U17b FLIP removing derived candidates removes the path list",
                      frc == EXIT_ERROR and bool(candidate_lines)
                      and all("CONTRIBUTIONS.md" not in line for line in candidate_lines))

            # U18) postcondition unit vectors: call opf._upgrade_postcondition directly (the m1 check).
            old_m = tomllib.loads(_FIX_MANIFEST)
            old_c = tomllib.loads(_FIX_COUNTERS)
            old_c["counters"]["BI"] = 4      # a nonzero existing high-water, to exercise lowered/raised
            new_m, new_c, _added, origin = opf._upgrade_plan(copy.deepcopy(old_m), copy.deepcopy(old_c))

            def post_passes(nm, nc):
                try:
                    opf._upgrade_postcondition(old_m, nm, old_c, nc, origin)
                    return True
                except opf._UpgradeError:
                    return False

            check("U18 genuine planner output passes the postcondition", post_passes(new_m, new_c))
            _m = copy.deepcopy(new_m); _m["modules"]["governance"] = True
            check("U18 flipped retained module boolean refuses", not post_passes(_m, new_c))
            _m = copy.deepcopy(new_m); _m["types"]["backlog_item"] = {"namespace": "XX"}
            check("U18 mutated retained [types] row refuses", not post_passes(_m, new_c))
            _c = copy.deepcopy(new_c); _c["counters"]["BI"] = 3
            check("U18 lowered existing counter refuses", not post_passes(new_m, _c))
            _c = copy.deepcopy(new_c); _c["counters"]["BI"] = 9
            check("U18 raised existing counter refuses", not post_passes(new_m, _c))
            _c = copy.deepcopy(new_c); del _c["counters"]["CN"]
            check("U18 missing CN zero refuses", not post_passes(new_m, _c))
            _m = copy.deepcopy(new_m)
            _m["views"]["DECISIONS.md"]["sources"] = list(reversed(_m["views"]["DECISIONS.md"]["sources"]))
            check("U18 wrong-order DECISIONS.md sources refuses", not post_passes(_m, new_c))
            _m = copy.deepcopy(new_m)
            _src = _m["views"]["DECISIONS.md"]["sources"]
            _m["views"]["DECISIONS.md"]["sources"] = _src + [_src[0]]
            check("U18 duplicated DECISIONS.md source refuses", not post_passes(_m, new_c))
            # R6: the porcelain -z grammar is validated in a PURE parser; a malformed payload is a fail-
            # closed refusal, never a clean-empty result. (Directly unit-tested, like the U18 postcondition
            # vectors.) A lone NUL that a naive split would read as clean must refuse.
            def _grammar_ok(raw, prefix="", lease=None):
                try:
                    return opf._opf_write_guard.parse_porcelain(raw, prefix, lease, [".working"], "upgrade"), None
                except opf._opf_write_guard.WriteGuardError as exc:
                    return None, str(exc)
            _clean, _ = _grammar_ok(b"")
            check("U18/R6 empty payload is clean (no dirt)", _clean == [])
            _d, _ = _grammar_ok(b"?? .working/toml/x\x00")
            check("U18/R6 well-formed untracked record parses", _d == [".working/toml/x"])
            _n, _err = _grammar_ok(b"\x00")
            check("U18/R6 lone NUL refuses (never clean-empty)", _n is None and _err is not None)
            _n2, _ = _grammar_ok(b"?? .working/toml/x")     # no trailing NUL terminator
            check("U18/R6 non-NUL-terminated payload refuses", _n2 is None)
            _n3, _ = _grammar_ok(b"XYZno-space\x00")        # no status/space framing at byte 2
            check("U18/R6 malformed record framing refuses", _n3 is None)
            _lx, _ = _grammar_ok(b"?? sub/.working/toml/lease.toml\x00", prefix="sub/",
                                 lease=".working/toml/lease.toml")
            check("U18/R6 nested-store lease excluded after prefix strip", _lx == [])
            # R6 (fail-open guard): a MALFORMED or IMPOSSIBLE status must RAISE fail-closed, never be read as a
            # normal record. For the LEASE PATH the danger is acute: a bogus pair a per-char (or cartesian)
            # check accepts would match the lease exclusion and be SILENTLY DROPPED, making the M3 cleanliness
            # guard return CLEAN over dirt. The vectors cover out-of-vocabulary (ZZ), a blank pair, rename R and
            # copy C (impossible under --no-renames), U in a non-unmerged
            # position (UT/TU), AND the impossible ORDINARY pairs a {space,M,T,A,D} cartesian would wrongly
            # admit -- DM/DT/DA (git emits X=D only with a space Y) and MA/TA (git emits Y=A only with a space
            # X). Each must refuse on BOTH the lease path and a non-lease path; validating the whole XY pair
            # (FIX2), against the EXACT man-page enumeration not the cartesian (FIX3), is what closes it.
            _lease_rel = ".working/toml/lease.toml"
            _neg_pairs = ((b"ZZ", "out-of-vocabulary ZZ"), (b"  ", "blank status"),
                          (b"R ", "rename R"), (b"C ", "copy C"),
                          (b"UT", "impossible pair UT (U only in unmerged pairs)"),
                          (b"TU", "impossible pair TU (U only in unmerged pairs)"),
                          (b"DM", "impossible ordinary DM (X=D pairs only with space)"),
                          (b"DT", "impossible ordinary DT (X=D pairs only with space)"),
                          (b"DA", "impossible ordinary DA (X=D pairs only with space)"),
                          (b"MA", "impossible ordinary MA (Y=A pairs only with space X)"),
                          (b"TA", "impossible ordinary TA (Y=A pairs only with space X)"))
            for _bad, _lbl in _neg_pairs:
                _rawl = _bad + b" " + _lease_rel.encode("utf-8") + b"\x00"
                _mres, _merr = _grammar_ok(_rawl, prefix="", lease=_lease_rel)
                check("U18/R6 impossible/malformed lease status ({}) refuses, never a silent drop".format(_lbl),
                      _mres is None and _merr is not None)
                _rawn = _bad + b" .working/toml/x\x00"
                _nres, _nerr = _grammar_ok(_rawn, prefix="", lease=_lease_rel)
                check("U18/R6 impossible/malformed non-lease status ({}) refuses fail-closed".format(_lbl),
                      _nres is None and _nerr is not None)
            # Positive sweep: EVERY genuinely-emittable porcelain v1 pair for this command parses correctly, so
            # the tightened check never OVER-refuses. Enumerated independently of the parser's own table (an
            # independent oracle) as the full emittable set: ?? untracked, !! ignored; the 17 ordinary pairs -- INCLUDING
            # ' A' (intent-to-add) and 'D ', whose silent omission by a future over-tightening a dropped vector
            # here would catch; and the seven unmerged pairs.
            for _vp in (b"??", b"!!",
                        b" A", b" M", b" T", b" D",
                        b"M ", b"MM", b"MT", b"MD",
                        b"T ", b"TM", b"TT", b"TD",
                        b"A ", b"AM", b"AT", b"AD",
                        b"D ",
                        b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"):
                _vres, _verr = _grammar_ok(_vp + b" .working/toml/x\x00", prefix="", lease=None)
                check("U18/R6 valid pair {!r} parses (no over-refusal)".format(_vp),
                      _vres == [".working/toml/x"] and _verr is None)
            # Both UNTRACKED ("??") and IGNORED ("!!") exact leases are excluded for step 4's
            # never-seize refusal. A TRACKED lease record is a spec-5.7
            # committed/tracked-lease anomaly and is REFUSED fail-closed here, never silently excluded (a
            # silent drop of a " D" committed-then-deleted lease is exactly the M3 fail-open this closes: it
            # would let step 4's O_EXCL acquire succeed on the now-absent file and sweep the deletion into the
            # uncommitted change set). Every emittable tracked status on the lease path must refuse.
            _exok, _ = _grammar_ok(b"?? " + _lease_rel.encode("utf-8") + b"\x00", prefix="", lease=_lease_rel)
            check("U18/R6 well-formed UNTRACKED lease record still excluded (step-4 never-seize)", _exok == [])
            _ignored, _ierr = _grammar_ok(b"!! sub/" + _lease_rel.encode("utf-8") + b"\x00",
                                          prefix="sub/", lease=_lease_rel)
            check("U18/R6 ignored lease excluded (step-4 never-seize)",
                  _ignored == [] and _ierr is None)
            for _tp in (b" D", b"D ", b" M", b"MM", b"M ", b"MD", b"A ", b"AD", b"DD", b"AU", b"UU"):
                _tres, _terr = _grammar_ok(_tp + b" " + _lease_rel.encode("utf-8") + b"\x00",
                                           prefix="", lease=_lease_rel)
                check("U18/R6 TRACKED lease status ({!r}) refuses spec-5.7, never a silent drop".format(_tp),
                      _tres is None and _terr is not None and "5.7" in _terr)
            _dnl, _ = _grammar_ok(b" M .working/toml/x\x00", prefix="", lease=_lease_rel)
            check("U18/R6 well-formed dirty non-lease record still surfaced", _dnl == [".working/toml/x"])
            check("U18/R6 clean tree (empty payload) still passes", _grammar_ok(b"")[0] == [])

            # Scope filtering is restricted to well-formed ignored records. Malformed records outside
            # scope still refuse, and tracked/untracked records outside scope are never silently dropped.
            for payload, expected in (
                    (b"!! .working\x00", [".working"]),
                    (b"!! .working/\x00", [".working/"]),
                    (b"!! .working/child\x00", [".working/child"]),
                    (b"!! .working.bak/\x00", []),
                    (b"?? .working.bak/child\x00", [".working.bak/child"]),
                    (b" M .working.bak/child\x00", [".working.bak/child"])):
                parsed, error = _grammar_ok(payload)
                check("U18 component scope {!r}".format(payload), parsed == expected and error is None)
            malformed, error = _grammar_ok(b"ZZ .working.bak/child\x00")
            check("U18 malformed out-of-scope status still refuses", malformed is None and error is not None)

            # Byte paths remain lossless through prefix stripping, lease exclusion and scope matching.
            byte_prefix = b"nested-\xff/"
            byte_path = b".working/\xfe"
            check("U18 byte path scope preserves filesystem bytes",
                  opf._opf_write_guard.parse_porcelain(b"!! " + byte_prefix + byte_path + b"\x00",
                      byte_prefix, None, [os.fsdecode(byte_path)], "upgrade") == [os.fsdecode(byte_path)])
            check("U18 byte prefix lease exclusion",
                  opf._opf_write_guard.parse_porcelain(b"!! " + byte_prefix + b".working/toml/lease.toml\x00",
                      byte_prefix, ".working/toml/lease.toml", [".working"], "upgrade") == [])
            for bad_path in (b"elsewhere/file", b"sub/", b"sub//file", b"sub/../file",
                             b"sub/./file", b"/sub/file"):
                parsed, error = _grammar_ok(b"!! " + bad_path + b"\x00", prefix="sub/")
                check("U18 unnormalizable ignored path {!r} refuses".format(bad_path),
                      parsed is None and error is not None)

            # R1b: the show-prefix normalization must strip ONLY the trailing newline, never LEADING
            # whitespace, so a store dir whose name begins with a space keeps its prefix and its lease is
            # correctly excluded. Drive _opf_write_guard.probe_dirty with a stubbed git that reports a " leading/\n"
            # prefix and an UNTRACKED ("??") lease record under it (the legitimate held-lease exclusion; a
            # tracked lease record is refused, not excluded). With the .strip() bug the leading space is lost,
            # the prefix no longer matches, and the lease surfaces as (spurious) dirt.
            import _opf_observe as _obs_r1b
            _GO = _obs_r1b._GitOutcome
            _orig_run_git = _obs_r1b._run_git
            try:
                def _fake_run_git(_git, _root, args, timeout=None, allow_lazy_fetch=False,
                                  config_overrides=None, max_output_bytes=8 << 20, input_bytes=None):
                    if "ls-files" in args:
                        return _GO(True, 0, b"", "")
                    if "rev-parse" in args:
                        return _GO(True, 0, b" leading/\n", b"")
                    if "config" in args:
                        return _GO(True, 0, b"", b"")   # no filters configured: empty (NUL-free) --list output
                    return _GO(True, 0, b"??  leading/.working/toml/lease.toml\x00", b"")
                _obs_r1b._run_git = _fake_run_git
                _r1b_dirty = opf._opf_write_guard.probe_dirty("git", base, [".working"],
                                                              ".working/toml/lease.toml", "upgrade")
            finally:
                _obs_r1b._run_git = _orig_run_git
            check("U18/R1b leading-space store prefix keeps the lease excluded (rstrip newline only)",
                  _r1b_dirty == [])

            # U19) R1: the post-mutation recovery text threads DISTINCT roots. Called directly (like U18):
            # Planned-path restore + created-file removal name the STORE root; a product target names the
            # PRODUCT root. Without the fix (one root for both) the product line names the store root.
            _rt = opf._upgrade_recovery_text("/store/root", "/product/root",
                                             [".working/toml/contribution.index.toml"], ["VERSION"],
                                             [".working/toml/manifest.toml",
                                              ".working/toml/contribution.index.toml"])
            check("U19 restore names only the planned pre-existing store path",
                  "git -C /store/root --literal-pathspecs restore --staged --worktree "
                  "-- .working/toml/manifest.toml\n" in _rt)
            check("U19 created-file removal is under the store root",
                  "/store/root/.working/toml/contribution.index.toml" in _rt
                  and "/product/root/.working/toml" not in _rt)
            check("U19 product target restore names the product root",
                  "git -C /product/root --literal-pathspecs restore --staged --worktree -- VERSION" in _rt)
            check("U19 inspect line names the store root",
                  "inspect first: git -C /store/root --literal-pathspecs status --ignored=matching "
                  "--untracked-files=all -- .working/toml/contribution.index.toml "
                  ".working/toml/manifest.toml\n" in _rt)

            # U20) R8: governance=true with maintainer_action but maintainer_decision ABSENT (a module-
            # inconsistent 1.0.0 shape the delta would silently cure) now REFUSES unchanged, before mutation.
            s20 = base / "u20-gov-no-md"
            s20.mkdir()
            mach20 = build_store(s20, manifest=_man_gov_no_md(), counters=_cnt_gov_ma_only(),
                                 extra_files={idx("maintainer_action"): _FIX_INDEX})
            before20 = _snapshot(s20)
            rc20, out20 = upgrade(s20)
            check("U20 gov-with-MD-absent refuses (exit 2)", rc20 == EXIT_ERROR)
            check("U20 refusal names the module coupling",
                  "governance" in out20 and "maintainer_decision" in out20 and "silently cure" in out20)
            check("U20 tree unchanged (refused before mutation)", _snapshot(s20) == before20)

            # U21) R2: a 1.0.0 origin that OMITS the WHOLE [modules] / [views] table (each oracle-graded
            # doctor-VALID at merge-base 1c90fbb) migrates to a doctor-VALID 1.2.0 store.
            for _lbl, _man in (("modules", _man_drop_modules_table()),
                               ("views", _man_drop_views_table()),
                               ("both", _man_drop_views_table(_man_drop_modules_table()))):
                s21 = base / ("u21-absent-" + _lbl)
                s21.mkdir()
                mach21 = build_store(s21, manifest=_man)
                rc21, out21 = upgrade(s21)
                check("U21 absent-{} migrates (exit 0)".format(_lbl), rc21 == EXIT_OK)
                man21 = man_of(mach21)
                check("U21 absent-{} gains the two new views".format(_lbl),
                      "CONTRIBUTIONS.md" in man21.get("views", {})
                      and "DECISIONS.toml" in man21.get("views", {}))
                if _lbl in ("modules", "both"):
                    check("U21 absent-{} keeps [modules] absent (no empty table invented)".format(_lbl),
                          "modules" not in man21)
                if _lbl in ("views", "both"):
                    check("U21 absent-{} DECISIONS.md not invented".format(_lbl),
                          "DECISIONS.md" not in man21.get("views", {}))
                drc21, dout21 = doctor(s21)
                check("U21 absent-{} upgraded store is doctor-VALID".format(_lbl),
                      drc21 == EXIT_OK and "integrity: VALID" in dout21)

            # U22) R1: a NESTED store (store root BELOW the git repo root) with a foreign held lease reaches
            # step 4's never-seize message, not step 3's dirty-store remedy (the un-normalized lease_excl
            # missed under the repo-root-relative porcelain path). Build the repo at a PARENT, the store in a
            # subdir, commit through the parent, then plant an untracked foreign lease.
            repo22 = base / "u22-nested-repo"
            sub22 = repo22 / "product" / "store"
            sub22.mkdir(parents=True)
            mach22 = sub22 / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
            mach22.mkdir(parents=True)
            git_call(repo22, ["init"])
            (mach22 / _opf_store.MANIFEST_NAME).write_text(_FIX_MANIFEST, encoding="utf-8")
            (mach22 / _opf_check.COUNTERS_NAME).write_text(_FIX_COUNTERS, encoding="utf-8")
            (mach22 / _opf_check.VERSION_NAME).write_text(_FIX_VERSION, encoding="utf-8")
            (mach22 / _opf_check.WORKLOG_NAME).write_text(_FIX_WORKLOG, encoding="utf-8")
            for _t in _FIX_INDEX_TYPES:
                (mach22 / (_t + _opf_check.INDEX_SUFFIX)).write_text(_FIX_INDEX, encoding="utf-8")
            (sub22 / _opf_store.POINTER_REL).write_text('[store]\ntarget = "dir:."\n', encoding="utf-8")
            (sub22 / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            git_call(repo22, ["--literal-pathspecs", "add", "-A"])
            git_call(repo22, ["commit", "-m", "seed nested 1.0.0 store"])
            check("U22 store root is BELOW the repo root (nested)",
                  git_call(sub22, ["rev-parse", "--show-prefix"]).strip().rstrip("/") == "product/store")
            (mach22 / _opf_check.LEASE_NAME).write_text(
                'acquired_at = "2026-01-01T00:00:00Z"\nholder = "peer-runner"\n'
                'operation = "upgrade"\nschema = 1\n', encoding="utf-8")   # untracked foreign lease
            before22 = _snapshot(repo22)
            rc22, out22 = upgrade(sub22)
            check("U22 nested held-lease refuses (exit 2)", rc22 == EXIT_ERROR)
            check("U22 reaches the step-4 never-seize message (not the step-3 dirty remedy)",
                  "never seized" in out22 and "peer-runner" in out22
                  and "the store working tree is not clean" not in out22)
            check("U22 lease NOT deleted and tree unchanged",
                  (mach22 / _opf_check.LEASE_NAME).is_file() and _snapshot(repo22) == before22)

            # U23) R3: a FIFO planted at lease.toml refuses PROMPTLY (bounded, never a blocking hang) and the
            # FIFO is untouched. Bounded call so a regression (blocking open) fails fast instead of hanging.
            if hasattr(os, "mkfifo"):
                s23 = base / "u23-fifo-lease"
                s23.mkdir()
                mach23 = build_store(s23)
                os.mkfifo(str(mach23 / _opf_check.LEASE_NAME))
                before23 = _snapshot(s23)
                try:
                    proc23 = subprocess.run(
                        [sys.executable, "-I", "-B", str(Path(__file__).resolve().parent / "opf.py"),
                         "upgrade", "--root", str(s23)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30,
                        env=env_holder["env"])
                    rc23, out23 = proc23.returncode, proc23.stdout + proc23.stderr
                except subprocess.TimeoutExpired:
                    rc23, out23 = None, "TIMEOUT (blocking FIFO open, R3 regression)"
                check("U23 FIFO lease refuses promptly (exit 2, no hang)", rc23 == EXIT_ERROR)
                check("U23 refusal names a present non-regular lease",
                      "non-regular" in out23 and "never seized" in out23)
                check("U23 FIFO untouched and tree unchanged",
                      stat.S_ISFIFO((mach23 / _opf_check.LEASE_NAME).lstat().st_mode)
                      and _snapshot(s23) == before23)

            # U24) R4 never-seize: a mid-acquisition failure (the payload write fails AFTER the O_EXCL create)
            # must NOT perform an ownership-blind by-name unlink. It LEAVES the lease as a reconcilable
            # leftover (leave-and-reconcile, spec 5.7). Monkeypatch journal._write_all to fail, call
            # _opf_write_guard.acquire_lease directly, and assert it raises a reconcilable WriteGuardError AND leaves the
            # lease in place. (a) ordinary failure.
            mrel24 = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)
            s24a = base / "u24a-leave-and-reconcile"
            s24a.mkdir()
            mach24a = build_store(s24a)
            fd24a = _opf_store._open_dir_nofollow(str(s24a.resolve()))
            _orig_wa = _opf_store._journal._write_all
            _r4_exc = None
            try:
                _opf_store._journal._write_all = lambda *a, **k: (_ for _ in ()).throw(
                    OSError("synthetic payload-write failure"))
                try:
                    opf._opf_write_guard.acquire_lease(fd24a, mrel24, "upgrade")
                except BaseException as _e24:
                    _r4_exc = _e24
            finally:
                _opf_store._journal._write_all = _orig_wa
                os.close(fd24a)
            check("U24 ordinary mid-acquisition failure raises a reconcilable WriteGuardError",
                  isinstance(_r4_exc, opf._opf_write_guard.WriteGuardError) and "never seized" in str(_r4_exc))
            check("U24 ordinary failure LEAVES the lease in place (leftover, never a racy unlink)",
                  (mach24a / _opf_check.LEASE_NAME).is_file())
            # (b) never-seize under an external replacement: the write REPLACES the lease with a peer holder's
            # bytes in the failure window, then fails. The replacement must SURVIVE (the old by-name unlink
            # would have deleted the peer's lease, a spec-5.7 never-seize violation).
            s24b = base / "u24b-never-seize-replace"
            s24b.mkdir()
            mach24b = build_store(s24b)
            _peer24 = (b'acquired_at = "2026-02-02T00:00:00Z"\nholder = "peer-runner"\n'
                       b'operation = "upgrade"\nschema = 1\n')
            fd24b = _opf_store._open_dir_nofollow(str(s24b.resolve()))
            _ns_raised = False

            def _replace_then_fail(*a, **k):
                _lp = mach24b / _opf_check.LEASE_NAME
                _lp.unlink()                       # external actor unlinks our just-created lease
                _lp.write_bytes(_peer24)           # ... and installs its OWN lease at the same name
                raise OSError("synthetic write failure after external lease replace")

            try:
                _opf_store._journal._write_all = _replace_then_fail
                try:
                    opf._opf_write_guard.acquire_lease(fd24b, mrel24, "upgrade")
                except BaseException:
                    _ns_raised = True
            finally:
                _opf_store._journal._write_all = _orig_wa
                os.close(fd24b)
            check("U24 never-seize: acquisition still raises", _ns_raised)
            check("U24 never-seize: the REPLACEMENT holder's lease is NOT removed",
                  (mach24b / _opf_check.LEASE_NAME).is_file()
                  and (mach24b / _opf_check.LEASE_NAME).read_bytes() == _peer24)

            # U25) R5: the lease is released BEFORE success is reported. Monkeypatch _opf_write_guard.release_lease to
            # fail; a valid store must exit 2 with NO success line emitted (without the fix, success prints
            # first and only then does the finally's release fail). Driven in-process (like U18).
            import contextlib as _ctx
            import io as _io
            s25 = base / "u25-release-first"
            s25.mkdir()
            build_store(s25)
            _orig_rel = opf._opf_write_guard.release_lease
            try:
                opf._opf_write_guard.release_lease = lambda *a, **k: (_ for _ in ()).throw(
                    opf._UpgradeError("synthetic release failure"))
                _buf25 = _io.StringIO()
                with _ctx.redirect_stdout(_buf25), _ctx.redirect_stderr(_buf25):
                    rc25 = opf._cmd_upgrade(["--root", str(s25)])
                out25 = _buf25.getvalue()
            finally:
                opf._opf_write_guard.release_lease = _orig_rel
            check("U25 a release failure surfaces exit 2", rc25 == EXIT_ERROR)
            check("U25 no success is reported when release fails (released-before-success)",
                  "uncommitted, NOT staged or committed" not in out25 and '"event": "upgraded"' not in out25)

            check("U25 doctor-VALID release failure offers reconciliation without rollback commands",
                  "reached doctor-VALID before lease release" in out25
                  and "Confirm no opf run is live" in out25
                  and "restore --staged" not in out25 and "rm -- " not in out25)

            # R5: a nonzero render/doctor result plus a release failure prints recovery ONCE.
            for failure in ("render", "doctor"):
                sf = base / ("r5-recovery-once-" + failure)
                sf.mkdir()
                build_store(sf)
                injection = (
                    "def release(*args):\n"
                    "    raise opf._UpgradeError('synthetic release failure')\n"
                    "guard.release_lease = release\n")
                if failure == "render":
                    injection += "opf._opf_views.render = lambda *a, **k: 2\n"
                else:
                    injection += (
                        "from types import SimpleNamespace\n"
                        "opf._upgrade_doctor = lambda *a: SimpleNamespace(status='INVALID')\n"
                        "opf._doctor_report = lambda *a: None\n")
                rrc, rout = flipped_upgrade(sf, injection)
                check("R5 {} plus release failure emits recovery once".format(failure),
                      rrc == EXIT_ERROR and "synthetic release failure" in rout
                      and rout.count("Confirm no opf run is live (spec 5.7)") == 1
                      and "restore --staged" in rout
                      and (sf / ".working/toml/lease.toml").is_file()
                      and '"event": "upgraded"' not in rout)

            # U25c) F-LEASE-RELEASE-ANY-EXC: the finally's "is an exception propagating" test is THIS frame's
            # own exception path, not sys.exc_info(). _upgrade_run is called from inside a CALLER's `except`
            # with a nonzero render (a return path) and an inert failing release (it raises, touching
            # nothing): the release error must propagate, never be printed and swallowed because the
            # caller's handled exception looked in flight. The flip restores the sys.exc_info() test and
            # must turn the vector red with the EXACT swallow outcome (exit 2 plus the "additionally" note),
            # so a flip that fails for an unrelated reason does not pass the leg.
            import importlib.util as _importlib_util
            import inspect as _inspect
            import types as _types

            def _caller_release_outcome(run, label):
                # ("raised", message, stderr) for an _UpgradeError, ("other", repr, stderr) for any other
                # Exception, or ("returned", rc, stderr) for a normal return.
                sc = base / ("u25c-caller-except-" + label)
                sc.mkdir()
                build_store(sc)
                _orig_render = opf._opf_views.render
                opf._opf_write_guard.release_lease = lambda *a, **k: (_ for _ in ()).throw(
                    opf._UpgradeError("synthetic release failure"))
                opf._opf_views.render = lambda *a, **k: 2
                err = _io.StringIO()
                try:
                    with _ctx.redirect_stdout(_io.StringIO()), _ctx.redirect_stderr(err):
                        try:
                            raise RuntimeError("the caller's handled exception")
                        except RuntimeError:
                            try:
                                rc = run(str(sc))
                            except opf._UpgradeError as exc:
                                return ("raised", str(exc), err.getvalue())
                            except Exception as exc:  # noqa: BLE001  recorded, so the check names it red
                                return ("other", repr(exc), err.getvalue())
                            return ("returned", rc, err.getvalue())
                finally:
                    opf._opf_write_guard.release_lease = _orig_rel
                    opf._opf_views.render = _orig_render

            _fixed25c = _caller_release_outcome(opf._upgrade_run, "fixed")
            check("U25c a release failure on a return path propagates from inside a caller's except",
                  _fixed25c[:2] == ("raised", "synthetic release failure")
                  and "releasing the upgrade lease failed" not in _fixed25c[2])
            _src25c = _inspect.getsource(opf._upgrade_run)
            _new25c = "                if not propagating:\n"
            _flip25c = None
            if _src25c.count(_new25c) == 1:
                # The reverted body is written to a scratch module and loaded; its code is then bound to
                # opf's live globals, so the flip sees exactly the module state the fixed function sees.
                _path25c = base / "u25c_flip_upgrade_run.py"
                _path25c.write_text(_src25c.replace(_new25c, "                if sys.exc_info()[1] is None:\n"),
                                    encoding="utf-8")
                _spec25c = _importlib_util.spec_from_file_location("_opf_u25c_flip", _path25c)
                _mod25c = _importlib_util.module_from_spec(_spec25c)
                _spec25c.loader.exec_module(_mod25c)
                _flip25c = _types.FunctionType(_mod25c._upgrade_run.__code__, vars(opf), "_upgrade_run")
            check("U25c flip target (the frame-local propagating test) found exactly once", _flip25c is not None)
            _flipout25c = _caller_release_outcome(_flip25c, "flip") if _flip25c is not None else None
            check("U25c flip: the sys.exc_info() test swallows the release failure (returns 2 and prints the "
                  "release-failure note; vector red)",
                  _flipout25c is not None and _flipout25c[:2] == ("returned", EXIT_ERROR)
                  and "opf upgrade: additionally, releasing the upgrade lease failed (synthetic release "
                      "failure)" in _flipout25c[2])

            # U25b) FIX1 release never-seize (class-width): the RELEASE path (not only the acquisition path)
            # is ownership-verified. Acquire a lease, capture the payload, then have a peer REPLACE the lease
            # with its own well-formed bytes; releasing MUST NOT unlink the peer's replacement (the old
            # ownership-blind os.unlink deleted it -- a spec-5.7 never-seize violation). It raises a
            # reconcilable WriteGuardError and LEAVES the replacement in place, and a valid ordinary release of
            # this run's OWN lease still removes it. Driven directly (like U24).
            s25b = base / "u25b-release-never-seize"
            s25b.mkdir()
            mach25b = build_store(s25b)
            _lp25 = mach25b / _opf_check.LEASE_NAME
            fd25b = _opf_store._open_dir_nofollow(str(s25b.resolve()))
            _pay25 = opf._opf_write_guard.acquire_lease(fd25b, mrel24, "upgrade")
            check("U25b acquire returns the exact on-disk lease payload (ownership token)",
                  _pay25 == _lp25.read_bytes())
            _peer25 = (b'acquired_at = "2026-03-03T00:00:00Z"\nholder = "peer-runner"\n'
                       b'operation = "upgrade"\nschema = 1\n')
            _lp25.unlink()
            _lp25.write_bytes(_peer25)              # peer replaces our lease with its own well-formed lease
            _seize_raised = False
            try:
                opf._opf_write_guard.release_lease(fd25b, mrel24, _pay25, "upgrade")
            except opf._opf_write_guard.WriteGuardError as _e25:
                _seize_raised = ("never seized" in str(_e25) or "NEVER seized" in str(_e25)) \
                    and "peer-runner" in str(_e25)
            check("U25b release of a REPLACED lease raises never-seize (no false success)", _seize_raised)
            check("U25b the peer REPLACEMENT survives release (never seized)",
                  _lp25.is_file() and _lp25.read_bytes() == _peer25)
            # ordinary release of THIS run's own lease still removes it (no over-refusal). Tolerant restore so
            # a REGRESSION (a seizing release that already deleted the peer) fails these checks cleanly rather
            # than crashing the suite on a missing file.
            if _lp25.exists():
                _lp25.unlink()
            _lp25.write_bytes(_pay25)               # restore this run's own lease
            opf._opf_write_guard.release_lease(fd25b, mrel24, _pay25, "upgrade")
            os.close(fd25b)
            check("U25b ordinary release removes this run's OWN lease", not _lp25.exists())

            # U26) R8: a non-boolean value on ANY module (not only the retired decision_support) is refused by
            # the planner precondition, matching the merge-base _validate_modules (a non-boolean module value
            # is 1.0.0-INVALID). Without the fix only decision_support was checked, so e.g. governance="x"
            # slipped through the planner (caught only later, post-mutation, by the final doctor).
            def _plan_refuses(mut):
                m = tomllib.loads(_FIX_MANIFEST)
                mut(m["modules"])
                try:
                    opf._upgrade_plan(m, tomllib.loads(_FIX_COUNTERS))
                    return False
                except opf._UpgradeError:
                    return True
            check("U26/R8 non-boolean governance module refuses",
                  _plan_refuses(lambda mods: mods.__setitem__("governance", "x")))
            check("U26/R8 non-boolean operational_policy module refuses",
                  _plan_refuses(lambda mods: mods.__setitem__("operational_policy", 3)))
            check("U26/R8 non-boolean decision_support module still refuses",
                  _plan_refuses(lambda mods: mods.__setitem__("decision_support", "x")))
            # FIX3: an UNKNOWN [modules] key is refused UPFRONT (matching the merge-base _validate_modules),
            # not carried through to a post-mutation doctor failure. The retired decision_support key stays a
            # KNOWN 1.0.0 module (union of current KNOWN_MODULES + the retired module), so a bare boolean
            # decision_support does NOT trip this check.
            check("U26/FIX3 unknown module key refuses upfront",
                  _plan_refuses(lambda mods: mods.__setitem__("unknown_present", True)))
            check("U26/FIX3 a known-but-retired decision_support key still plans (no false refusal)",
                  not _plan_refuses(lambda mods: mods.__setitem__("decision_support", False)))
            _r8_ok = True
            try:
                opf._upgrade_plan(tomllib.loads(_FIX_MANIFEST), tomllib.loads(_FIX_COUNTERS))
            except opf._UpgradeError:
                _r8_ok = False
            check("U26/R8 fully-boolean module set still plans (no false refusal)", _r8_ok)
            # end-to-end: a stored governance="x" refuses BEFORE any mutation (exit 2), tree unchanged.
            s26 = base / "u26-nonbool-module"
            s26.mkdir()
            _man26 = _FIX_MANIFEST.replace("governance = false\n", 'governance = "x"\n', 1)
            mach26 = build_store(s26, manifest=_man26)
            before26 = _snapshot(s26)
            rc26, out26 = upgrade(s26)
            check("U26/R8 stored non-boolean module refuses (exit 2)", rc26 == EXIT_ERROR)
            check("U26/R8 refusal names the module and boolean requirement",
                  "governance" in out26 and "boolean" in out26)
            check("U26/R8 tree unchanged (refused before mutation)", _snapshot(s26) == before26)

            # U27) A relocated interrupted run names the STORE root for inspection. Its earlier plan
            # is unknown, so advice must not offer a subtree restore over unchecked owner content.
            import shlex as _shlex_r1a
            prod27 = base / "u27-relocated"
            store27 = prod27 / "sub-store"
            mach27 = store27 / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
            mach27.mkdir(parents=True)
            git_call(prod27, ["init"])
            (mach27 / _opf_store.MANIFEST_NAME).write_text(_FIX_MANIFEST, encoding="utf-8")
            (mach27 / _opf_check.COUNTERS_NAME).write_text(_FIX_COUNTERS, encoding="utf-8")
            (mach27 / _opf_check.VERSION_NAME).write_text(_FIX_VERSION, encoding="utf-8")
            (mach27 / _opf_check.WORKLOG_NAME).write_text(_FIX_WORKLOG, encoding="utf-8")
            for _t27 in _FIX_INDEX_TYPES:
                (mach27 / (_t27 + _opf_check.INDEX_SUFFIX)).write_text(_FIX_INDEX, encoding="utf-8")
            (prod27 / _opf_store.POINTER_REL).write_text('[store]\ntarget = "dir:sub-store"\n',
                                                         encoding="utf-8")
            (prod27 / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            git_call(prod27, ["--literal-pathspecs", "add", "-A"])
            git_call(prod27, ["commit", "-m", "seed relocated 1.0.0 store"])
            rc27a, out27a = upgrade(prod27)
            check("U27 relocated store migrates to 1.2.0 (exit 0)", rc27a == EXIT_OK)
            git_call(prod27, ["--literal-pathspecs", "add", "-A"])
            git_call(prod27, ["commit", "-m", "commit staged migration"])
            (mach27 / idx("maintainer_decision")).unlink()
            rc27b, out27b = upgrade(prod27)
            check("U27 relocated partial store fails closed (exit 2)", rc27b == EXIT_ERROR)
            check("U27 relocated partial store names not-doctor-VALID", "NOT doctor-VALID" in out27b)
            _want27 = ("git -C {} --literal-pathspecs status --ignored=matching"
                       .format(_shlex_r1a.quote(str(store27))))
            _bad27 = ("git -C {} --literal-pathspecs status --ignored=matching"
                      .format(_shlex_r1a.quote(str(prod27))))
            check("U27 partial-run inspection names STORE root and offers no unverified restore",
                  _want27 in out27b and _bad27 not in out27b and "restore --staged" not in out27b)

            # U28) FIX1 compound-failure surfaces BOTH: a mid-run failure (the view render RAISES after the
            # manifest+counters mutation) is already propagating with its own "uncommitted change is left for
            # review" recovery advice WHEN the finally's lease release ALSO fails (a peer replaced the lease
            # -> the release's never-seize WriteGuardError). The release error must NOT displace the propagating
            # render failure: _cmd_upgrade surfaces BOTH on stderr (the render recovery advice AND the
            # lease-replaced note), exits 2, and LEAVES the peer lease (never seized). Driven in-process (U25).
            import contextlib as _ctx28
            import io as _io28
            s28 = base / "u28-compound-failure"
            s28.mkdir()
            mach28 = build_store(s28)
            _lp28 = mach28 / _opf_check.LEASE_NAME
            _peer28 = (b'acquired_at = "2026-04-04T00:00:00Z"\nholder = "peer-runner"\n'
                       b'operation = "upgrade"\nschema = 1\n')
            _orig_render28 = opf._opf_views.render

            def _render_replace_lease_then_fail(*a, **k):
                # a peer swaps the lease in the (post-mutation) render window, then the render fails
                if _lp28.exists():
                    _lp28.unlink()
                _lp28.write_bytes(_peer28)
                raise RuntimeError("synthetic render failure after mutation")

            try:
                opf._opf_views.render = _render_replace_lease_then_fail
                _buf28 = _io28.StringIO()
                with _ctx28.redirect_stdout(_buf28), _ctx28.redirect_stderr(_buf28):
                    rc28 = opf._cmd_upgrade(["--root", str(s28)])
                out28 = _buf28.getvalue()
            finally:
                opf._opf_views.render = _orig_render28
            check("U28/FIX1 compound failure exits 2", rc28 == EXIT_ERROR)
            check("U28/FIX1 the mid-run render recovery advice still reaches the operator",
                  any(line.startswith("opf upgrade: refused: view render after the schema delta failed")
                      and "uncommitted change is left for review" in line
                      for line in out28.splitlines()))
            check("post-lease exception prints planned-scope recovery commands",
                  "recover it scoped to the paths this run planned" in out28
                  and "restore --staged --worktree -- .working/toml/counters.toml "
                  ".working/toml/manifest.toml" in out28)
            check("U28/FIX1 the render failure (not the lease error) governs the refusal line",
                  "view render after the schema delta failed" in out28)
            check("U28/FIX1 the lease-replaced note is ALSO surfaced (not displaced)",
                  "releasing the upgrade lease failed" in out28
                  and "REPLACED by another holder" in out28 and "peer-runner" in out28)
            check("U28/FIX1 the peer lease is LEFT in place (never seized)",
                  _lp28.is_file() and _lp28.read_bytes() == _peer28)

            # U29) FIX3 absent-vs-replaced release wording: the release refusal DISTINGUISHES a genuine
            # ABSENCE (the lease was deleted, not replaced) from a REPLACEMENT (a present-but-different
            # payload). Both stay fail-closed / never-seize; only the operator-facing wording differs. Driven
            # directly (like U25b), reusing mrel24 and the _peer24 well-formed peer lease.
            s29 = base / "u29-absent-vs-replaced"
            s29.mkdir()
            mach29 = build_store(s29)
            _lp29 = mach29 / _opf_check.LEASE_NAME
            fd29 = _opf_store._open_dir_nofollow(str(s29.resolve()))
            _pay29 = opf._opf_write_guard.acquire_lease(fd29, mrel24, "upgrade")
            _absent_msg = None
            _lp29.unlink()                          # ABSENT: removed, not replaced
            try:
                opf._opf_write_guard.release_lease(fd29, mrel24, _pay29, "upgrade")
            except opf._opf_write_guard.WriteGuardError as _e29a:
                _absent_msg = str(_e29a)
            check("U29/FIX3 absent lease release refuses (fail-closed, no false success)",
                  _absent_msg is not None)
            check("U29/FIX3 absent case names ABSENCE, not replacement",
                  _absent_msg is not None and "absent" in _absent_msg.lower()
                  and "replaced by another holder" not in _absent_msg.lower())
            _lp29.write_bytes(_peer24)              # REPLACED: present-but-different payload
            _replaced_msg = None
            try:
                opf._opf_write_guard.release_lease(fd29, mrel24, _pay29, "upgrade")
            except opf._opf_write_guard.WriteGuardError as _e29b:
                _replaced_msg = str(_e29b)
            check("U29/FIX3 replaced lease release refuses (fail-closed)", _replaced_msg is not None)
            check("U29/FIX3 replaced case names REPLACEMENT and the holder, not absence",
                  _replaced_msg is not None and "replaced by another holder" in _replaced_msg.lower()
                  and "peer-runner" in _replaced_msg and "absent at release" not in _replaced_msg.lower())
            os.close(fd29)

            # FIX2) the TOCTOU disclosure on _opf_write_guard.unlink_owned_lease now also discloses the false-success
            # (exit-0 "released") over a swapped peer lease, not only the errant unlink, and names the
            # release-only-when-no-run-is-live reachability condition. Assert the extended clause is present.
            import _optlevel
            _fix2_doc = (_optlevel.source_docstring(opf._opf_write_guard.__file__, "unlink_owned_lease") or "").lower()
            check("FIX2 TOCTOU disclosure covers the false-success residual",
                  "reports exit-0 success" in _fix2_doc and 'false "released"' in _fix2_doc
                  and "release-only-when-no-run-is-live" in _fix2_doc)

            # U30) FIX5 forward-drift pin: opf pins the valid 1.0.0 [modules] vocabulary to a FROZEN expected
            # set, and _upgrade_plan reconciles the LIVE _opf_store.KNOWN_MODULES-derived set against it, so a
            # future KNOWN_MODULES edit that shifts the 1.0.0 vocabulary fails HERE. Binds derived to frozen.
            _expected_1_0_0_modules = frozenset({
                "governance", "delivery_assurance", "operational_policy", "concurrent_operation",
                "decision_support"})
            check("U30/FIX5 opf pins the 1.0.0 module vocabulary to the frozen expected set",
                  opf._VALID_1_0_0_MODULES == _expected_1_0_0_modules)
            check("U30/FIX5 the pin still equals the live KNOWN_MODULES + retired-module derivation",
                  opf._VALID_1_0_0_MODULES
                  == (frozenset(_opf_store.KNOWN_MODULES) | {opf._UPGRADE_RETIRED_MODULE}))

            # U31) R6 committed/tracked-lease corner: a lease.toml COMMITTED at HEAD (itself a spec-5.7
            # violation: a lease is present only while held) and DELETED in the worktree emits a " D" TRACKED
            # porcelain record on the lease path. The old lease EXCLUSION dropped it silently, so step 4's
            # O_EXCL acquire then succeeded on the now-absent file and the upgrade MUTATED (exit 0). It now
            # REFUSES at step 3, naming the tracked/committed-lease spec-5.7 violation, before any mutation.
            # (This fixture FAILS under the old code: the " D" lease is silently excluded and the run proceeds.)
            _lease_payload = ('acquired_at = "2026-01-01T00:00:00Z"\nholder = "peer-runner"\n'
                              'operation = "upgrade"\nschema = 1\n')
            s31 = base / "u31-lease-committed-deleted"
            s31.mkdir()
            mach31 = build_store(s31, extra_files={_opf_check.LEASE_NAME: _lease_payload})  # committed via add -A
            (mach31 / _opf_check.LEASE_NAME).unlink()        # deleted in the worktree -> " D" tracked record
            before31 = _snapshot(s31)
            rc31, out31 = upgrade(s31)
            check("U31 committed-then-deleted lease refuses at step 3 (exit 2)", rc31 == EXIT_ERROR)
            check("U31 refusal names the tracked/committed lease and spec 5.7",
                  "TRACKED" in out31 and "5.7" in out31 and "lease" in out31.lower())
            check("U31 no mutation (still 1.0.0 [devprocess]) and tree unchanged",
                  man_of(mach31).get("devprocess", {}).get("spec_version") == "1.0.0"
                  and "opf" not in man_of(mach31) and _snapshot(s31) == before31)
            # Re-confirm NO regression of the held-lease never-seize path: an ordinary UNTRACKED foreign lease
            # still reaches step 4's never-seize refusal (as U12), distinct from the tracked-lease step-3 refusal.
            s31b = base / "u31b-lease-untracked-noregress"
            s31b.mkdir()
            mach31b = build_store(s31b)
            (mach31b / _opf_check.LEASE_NAME).write_text(_lease_payload, encoding="utf-8")   # untracked (never added)
            before31b = _snapshot(s31b)
            rc31b, out31b = upgrade(s31b)
            check("U31b untracked foreign lease still reaches step-4 never-seize (exit 2, names holder)",
                  rc31b == EXIT_ERROR and "peer-runner" in out31b and "seized" in out31b.lower())
            check("U31b untracked lease NOT deleted and tree unchanged",
                  (mach31b / _opf_check.LEASE_NAME).is_file() and _snapshot(s31b) == before31b)

            # P1) seeded migration property test: 12 generated genuine-VALID 1.0.0 variants all migrate.
            _NS = {"maintainer_action": "MA", "maintainer_decision": "MD", "preference_pattern": "PP",
                   "artifact": "AR", "gate_run": "GR", "release": "RL", "waiver": "WV"}
            _MODT = {"governance": ["maintainer_action", "maintainer_decision"],
                     "decision_support": ["preference_pattern"],
                     "delivery_assurance": ["artifact", "gate_run", "release", "waiver"]}
            _MIGRATED = {"maintainer_decision", "preference_pattern"}   # gain records; become baseline at 1.1.0

            def _p1_record(t, ns, n):
                t0 = "2026-06-0{}T00:00:00Z".format(1 + (n % 9))
                if t == "maintainer_decision":
                    return {"id": "{}-{}".format(ns, n), "type": t, "status": "recorded",
                            "title": "synthetic {} {}".format(t, n), "created_at": t0, "updated_at": t0,
                            "actor": {"kind": "maintainer"}, "decision": "d{}".format(n)}
                # preference_pattern: an assistant-created RATIFIED pattern (active, updated_at > created_at)
                return {"id": "{}-{}".format(ns, n), "type": t, "status": "active",
                        "title": "synthetic {} {}".format(t, n), "created_at": t0,
                        "updated_at": "2026-06-15T00:00:00Z", "actor": {"kind": "assistant"},
                        "context": "c", "rationale": "r"}

            def _p1_variant(seed):
                rng = random.Random(seed)
                m = tomllib.loads(_FIX_MANIFEST)
                c = tomllib.loads(_FIX_COUNTERS)
                mods = [x for x in ("governance", "decision_support", "delivery_assurance")
                        if rng.random() < 0.6]
                if "decision_support" not in mods and rng.random() < 0.5:
                    del m["modules"]["decision_support"]      # optional key absent
                if rng.random() < 0.4:
                    del m["views"]["DECISIONS.md"]             # optional view absent
                idxtypes = []
                populated = {}
                for mod in mods:
                    m["modules"][mod] = True
                    for t in _MODT[mod]:
                        m["types"][t] = {"namespace": _NS[t]}
                        c["counters"][_NS[t]] = 0
                        idxtypes.append(t)
                        if t in _MIGRATED:
                            k = rng.randint(0, 2)
                            if k > 0:
                                recs = [_p1_record(t, _NS[t], i + 1) for i in range(k)]
                                populated[t] = _opf_emit.emit_checked({"schema": 1, "record": recs})
                                c["counters"][_NS[t]] = k       # high-water matches
                return (_opf_emit.emit_checked(m), _opf_emit.emit_checked(c), idxtypes, populated)

            p1_ok = True
            for seed in range(12):
                man, cnt, idxtypes, populated = _p1_variant(1000 + seed)
                sp = base / "p1-{}".format(seed)
                sp.mkdir()
                extra = {idx(t): populated.get(t, _FIX_INDEX) for t in idxtypes}
                machp = build_store(sp, manifest=man, counters=cnt, extra_files=extra)
                pre_idx = {t: (machp / idx(t)).read_bytes()
                           for t in set(_FIX_INDEX_TYPES) | set(idxtypes)}
                pre_wl = (machp / _opf_check.WORKLOG_NAME).read_bytes()
                pre_ver = (machp / _opf_check.VERSION_NAME).read_bytes()
                pre_cnt = tomllib.loads(cnt)["counters"]
                rcp, outp = upgrade(sp)
                if rcp != EXIT_OK:
                    p1_ok = False
                    continue
                drcp, doutp = doctor(sp)
                if not (drcp == EXIT_OK and "integrity: VALID" in doutp):
                    p1_ok = False
                    continue
                if any((machp / idx(t)).read_bytes() != b for t, b in pre_idx.items()):
                    p1_ok = False
                if (machp / _opf_check.WORKLOG_NAME).read_bytes() != pre_wl:
                    p1_ok = False
                if (machp / _opf_check.VERSION_NAME).read_bytes() != pre_ver:
                    p1_ok = False
                post_cnt = cnt_of(machp)
                if any(post_cnt.get(k) != v for k, v in pre_cnt.items()):
                    p1_ok = False
            check("P1 all 12 seeded variants migrate doctor-VALID with byte-preservation", p1_ok)

            # U28) OPF-STATUS-FILTER-SUPPRESS (F-OPF-UPGRADE-CLEANFILTER-EXEC): a repo/worktree-configured
            # clean|process filter driver must NOT be EXECUTED during the read-only upgrade dirty-probe
            # (exec-on-observe; SECI-config-is-executable-trust-gate, the class OPF-FSMON-SUPPRESS closed for
            # core.fsmonitor), while genuine dirtiness is still detected and git's BUILT-IN text/eol
            # conversion is preserved. Real git repos under the tempdir; the security assertion is sentinel
            # ABSENCE. Each planted clean filter writes a sentinel via `sh -c 'touch <store>/SENTINEL; cat'`
            # (identity passthrough); the target's mtime is bumped (content UNCHANGED) so `git status` MUST
            # re-hash it and thus run the filter (a size/mtime CHANGE short-circuits to dirty without hashing).
            import _opf_observe as _obs_flt
            _git_flt = _obs_flt._git_path()
            _mach_rel_flt = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)
            _lease_flt = "{}/{}".format(_mach_rel_flt, _opf_check.LEASE_NAME)
            _FUTURE = (4102444800, 4102444800)   # 2100-01-01: a fixed future mtime, host-clock-independent
            def _probe(git, root, specs, lease):
                return opf._opf_write_guard.probe_dirty(git, root, specs, lease, "upgrade")
            _status_args = ["--literal-pathspecs", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                            "--no-renames", "--", _opf_store.WORKING_DIRNAME]

            def _reset(st, rel="{}/pwn.dat".format(_mach_rel_flt)):
                """Remove any sentinel and re-bump the target's mtime so the next status must re-hash it."""
                try:
                    (st / "SENTINEL").unlink()
                except FileNotFoundError:
                    pass
                os.utime(str(st / rel), _FUTURE)

            def _flt_store(name, driver, form, attr_value, target="pwn.dat", content="hello\n",
                           required=None, worktree_scope=False):
                """A committed store with a tracked `.working/toml/<target>`, a git clean|process filter
                `driver` whose command writes `<store>/SENTINEL`, and a working-tree .gitattributes assigning
                that filter to the target. `worktree_scope` sets the driver in .git/config.worktree (proving
                enumeration sees worktree config and command-scope -c overrides it)."""
                st = base / name
                st.mkdir()
                build_store(st, extra_files={target: content})
                cmd = "sh -c 'touch \"{}\"; cat'".format(str(st / "SENTINEL"))
                scope = ["--worktree"] if worktree_scope else []
                if worktree_scope:
                    git_call(st, ["config", "extensions.worktreeConfig", "true"])
                git_call(st, ["config"] + scope + ["filter.{}.{}".format(driver, form), cmd])
                if required is not None:
                    git_call(st, ["config"] + scope
                             + ["filter.{}.required".format(driver), "true" if required else "false"])
                (st / ".gitattributes").write_text(
                    "{}/{} filter={}\n".format(_mach_rel_flt, target, attr_value), encoding="utf-8")
                return st

            # (a) filter.<name>.clean: POSITIVE CONTROL (an un-neutralized status DOES run it), the FIX
            # (the neutralized probe does NOT), and a FLIP (neutralizer stubbed to [] == pre-fix: it runs
            # again) so the discriminator has teeth (change-carries-check).
            sA = _flt_store("u28a-clean", "pwn", "clean", "pwn")
            _reset(sA)
            _obs_flt._run_git(_git_flt, str(sA), _status_args)   # positive control: un-neutralized status
            check("U28a positive control: an un-neutralized status executes the planted clean filter",
                  (sA / "SENTINEL").exists())
            _reset(sA)
            dA = _probe(_git_flt, str(sA), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28a FIX: the clean filter is NOT executed by the neutralized dirty-probe",
                  not (sA / "SENTINEL").exists())
            check("U28a FIX: a racy-but-unchanged store reads CLEAN under neutralization", dA == [])
            _reset(sA)
            _orig_neut = _obs_flt._filter_neutralizing_config
            try:
                _obs_flt._filter_neutralizing_config = lambda *a, **k: []
                _probe(_git_flt, str(sA), [_opf_store.WORKING_DIRNAME], _lease_flt)
            finally:
                _obs_flt._filter_neutralizing_config = _orig_neut
            check("U28a FLIP: without the neutralization the probe executes the filter (the exploit; teeth)",
                  (sA / "SENTINEL").exists())

            # (b) filter.<name>.process (the long-running form; git spawns it before the protocol handshake,
            # so the exec happens): suppressed by the fix.
            sB = _flt_store("u28b-process", "pwn", "process", "pwn")
            _reset(sB)
            _obs_flt._run_git(_git_flt, str(sB), _status_args)
            check("U28b positive control: an un-neutralized status spawns the planted process filter",
                  (sB / "SENTINEL").exists())
            _reset(sB)
            dB = _probe(_git_flt, str(sB), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28b FIX: the process filter is NOT spawned by the neutralized dirty-probe",
                  not (sB / "SENTINEL").exists())
            check("U28b FIX: process-filter store reads CLEAN under neutralization", dB == [])

            # (c) a driver NAME containing dots (subsection `a.b`): the name-parse strips exactly one trailing
            # component, so the enumeration still neutralizes it.
            sC = _flt_store("u28c-dotname", "a.b", "clean", "a.b")
            _reset(sC)
            _probe(_git_flt, str(sC), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28c FIX: a dotted driver name (filter.a.b.clean) is still neutralized",
                  not (sC / "SENTINEL").exists())

            # (d) filter.<name>.required=true: emptying a REQUIRED driver's clean/process would itself make
            # status FAIL ("clean filter failed") and mask the verdict, so the fix also emits
            # `filter.<name>.required=false`. FIX: the probe reads CLEAN (no exec, no error). FLIP: a
            # neutralizer that empties clean/process but OMITS required=false makes the probe fail-closed
            # (WriteGuardError), proving the required=false component is load-bearing (teeth).
            sD = _flt_store("u28d-required", "pwn", "clean", "pwn", required=True)
            _reset(sD)
            dD = _probe(_git_flt, str(sD), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28d FIX: a required (emptied) driver is not executed and does not mask the verdict",
                  not (sD / "SENTINEL").exists() and dD == [])
            _reset(sD)
            _orig_neut = _obs_flt._filter_neutralizing_config
            _raised_d = False
            try:
                _obs_flt._filter_neutralizing_config = (
                    lambda *a, **k: [("filter.pwn.clean", ""), ("filter.pwn.process", "")])
                _probe(_git_flt, str(sD), [_opf_store.WORKING_DIRNAME], _lease_flt)
            except opf._opf_write_guard.WriteGuardError:
                _raised_d = True
            finally:
                _obs_flt._filter_neutralizing_config = _orig_neut
            check("U28d FLIP: omitting required=false lets a required emptied driver break the probe (teeth)",
                  _raised_d)

            # (e) worktree-scoped driver (.git/config.worktree via extensions.worktreeConfig): enumeration
            # sees it and command-scope -c overrides worktree scope.
            sE = _flt_store("u28e-worktree", "wpwn", "clean", "wpwn", worktree_scope=True)
            _reset(sE)
            _obs_flt._run_git(_git_flt, str(sE), _status_args)
            check("U28e positive control: an un-neutralized status runs the worktree-scoped filter",
                  (sE / "SENTINEL").exists())
            _reset(sE)
            _probe(_git_flt, str(sE), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28e FIX: a worktree-scoped (.git/config.worktree) filter is still neutralized",
                  not (sE / "SENTINEL").exists())

            # (f) genuine dirtiness is STILL detected under neutralization (no false negative): a store that
            # carries a clean filter AND a real modification/addition/deletion/mode-change/staged change is
            # reported dirty (the neutralization removes exec, not the modified-check).
            _dtgt = "{}/pwn.dat".format(_mach_rel_flt)
            # (f1) unstaged content modification
            sF1 = _flt_store("u28f1-modified", "pwn", "clean", "pwn")
            (sF1 / _dtgt).write_text("changed\n", encoding="utf-8")
            dF1 = _probe(_git_flt, str(sF1), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28f1 unstaged modification detected dirty under neutralization", _dtgt in dF1)
            check("U28f1 filter not executed while detecting real dirt", not (sF1 / "SENTINEL").exists())
            # (f2) untracked in-scope file
            sF2 = _flt_store("u28f2-untracked", "pwn", "clean", "pwn")
            (machdir(sF2) / "new.txt").write_text("new\n", encoding="utf-8")
            dF2 = _probe(_git_flt, str(sF2), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28f2 untracked in-scope file detected dirty under neutralization",
                  "{}/new.txt".format(_mach_rel_flt) in dF2)
            # (f3) deleted tracked file
            sF3 = _flt_store("u28f3-deleted", "pwn", "clean", "pwn")
            (sF3 / _dtgt).unlink()
            dF3 = _probe(_git_flt, str(sF3), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28f3 deleted tracked file detected dirty under neutralization", _dtgt in dF3)
            # (f4) staged change (index vs HEAD)
            sF4 = _flt_store("u28f4-staged", "pwn", "clean", "pwn")
            (sF4 / _dtgt).write_text("staged change\n", encoding="utf-8")
            git_call(sF4, ["--literal-pathspecs", "add", "--", _dtgt])
            dF4 = _probe(_git_flt, str(sF4), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28f4 staged change detected dirty under neutralization", _dtgt in dF4)
            # (f5) executable-mode change on a tracked file
            sF5 = _flt_store("u28f5-mode", "pwn", "clean", "pwn")
            os.chmod(str(sF5 / _dtgt), 0o755)
            dF5 = _probe(_git_flt, str(sF5), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28f5 executable-mode change detected dirty under neutralization", _dtgt in dF5)

            # (g) built-in text/eol conversion PRESERVED (the A-vs-B discriminator): a `text eol=crlf` file
            # checked out with CRLF but stored LF is CLEAN to git-with-conversion and MUST stay clean under
            # neutralization, where a raw --no-filters reimplementation would false-positive dirty.
            sG = base / "u28g-eol"
            sG.mkdir()
            machG = build_store(sG, extra_files={"text.dat": "a\nb\nc\n"})
            (sG / ".gitattributes").write_text(
                "{}/text.dat text eol=crlf\n".format(_mach_rel_flt), encoding="utf-8")
            git_call(sG, ["--literal-pathspecs", "add", "-A"])
            git_call(sG, ["commit", "-m", "eol attrs"])
            (machG / "text.dat").unlink()
            git_call(sG, ["--literal-pathspecs", "checkout", "--", "{}/text.dat".format(_mach_rel_flt)])
            with open(str(machG / "text.dat"), "rb") as _fh:
                _wt_bytes = _fh.read()
            os.utime(str(machG / "text.dat"), _FUTURE)
            dG = _probe(_git_flt, str(sG), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28g the worktree file is genuinely CRLF (fixture is meaningful)", b"\r\n" in _wt_bytes)
            check("U28g text/eol built-in conversion preserved: CRLF worktree stays CLEAN under the fix",
                  dG == [])

            # (h) FAIL-CLOSED enumeration: a corrupt .git/config makes `config --list` fail; the helper
            # RAISES and the probe turns that into a fail-closed WriteGuardError, never a silent clean pass.
            sH = _flt_store("u28h-corrupt", "pwn", "clean", "pwn")
            (sH / ".git" / "config").write_text("[this is not valid\n = = =\n", encoding="utf-8")
            _raised_h = False
            try:
                _obs_flt._filter_neutralizing_config(_git_flt, str(sH))
            except RuntimeError:
                _raised_h = True
            check("U28h helper fails closed (RuntimeError) on a corrupt .git/config", _raised_h)
            _probe_raised_h = False
            try:
                _probe(_git_flt, str(sH), [_opf_store.WORKING_DIRNAME], _lease_flt)
            except opf._opf_write_guard.WriteGuardError:
                _probe_raised_h = True
            check("U28h probe refuses fail-closed (WriteGuardError) when the enumeration cannot run",
                  _probe_raised_h)

            # (i) helper config-pair unit vector: only clean/process keys are neutralized (a smudge-only driver
            # is not), each yielding the exact three (key, value) overrides, the neutralized set == the
            # exec-able set. Values are separate strings (no `-c` first-`=` split hazard).
            sI = base / "u28i-config"
            sI.mkdir()
            build_store(sI)
            git_call(sI, ["config", "filter.pwn.clean", "sh -c cat"])
            git_call(sI, ["config", "filter.lfs.smudge", "git-lfs smudge -- %f"])   # smudge is checkout-side
            _cfg = _obs_flt._filter_neutralizing_config(_git_flt, str(sI))
            check("U28i helper neutralizes the clean driver with the three exact config overrides",
                  _cfg == [("filter.pwn.clean", ""), ("filter.pwn.process", ""),
                           ("filter.pwn.required", "false")])
            check("U28i helper does not neutralize a smudge-only driver (not exec-able on status)",
                  not any(k.startswith("filter.lfs.") for k, _v in _cfg))

            # (j) BOTH check_clean call sites are covered: the fix lives inside _opf_write_guard.probe_dirty,
            # which check_clean calls for the store root AND, when the product render target has a
            # different root, for the product root. Prove the neutralization holds when the probe is bound to
            # a SEPARATE product-root repo (the second call site's binding) with its own planted filter.
            sJ = _flt_store("u28j-product-root", "ppwn", "clean", "ppwn")
            _reset(sJ)
            _probe(_git_flt, str(sJ), [_opf_store.WORKING_DIRNAME], None)
            check("U28j the fix holds for a product-root binding (second check_clean call site)",
                  not (sJ / "SENTINEL").exists())

            # (k) SECURITY (F-OPF-STATUSFILTER-EQ-BYPASS): a driver whose SUBSECTION NAME contains `=`
            # ([filter "pwn=bypass"], reachable via `.gitattributes: <target> filter=pwn=bypass`). A `-c
            # filter.pwn=bypass.clean=` argv splits at the FIRST `=` (parsed as key `filter.pwn` = value
            # `bypass.clean=`), leaving the REAL `filter.pwn=bypass.clean` driver EXECUTABLE; the GIT_CONFIG_*
            # env mechanism passes key and value as SEPARATE strings and neutralizes ANY subsection name. The
            # identity `cat` clean filter keeps the store reading CLEAN under neutralization so the only
            # observable is sentinel exec. FLIP renders the SAME neutralization set the OLD -c key=value way to
            # prove the env injection (not merely the enumeration) is what closes the vector (change-carries-check).
            sK = _flt_store("u28k-eqname", "pwn=bypass", "clean", "pwn=bypass")
            _reset(sK)
            _obs_flt._run_git(_git_flt, str(sK), _status_args)   # positive control: un-neutralized status
            check("U28k positive control: an un-neutralized status executes the =-named clean filter",
                  (sK / "SENTINEL").exists())
            _reset(sK)
            dK = _probe(_git_flt, str(sK), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28k FIX: the =-in-subsection-name clean filter is NOT executed under env neutralization",
                  not (sK / "SENTINEL").exists())
            check("U28k FIX: an identity-filter =-named store reads CLEAN under neutralization", dK == [])
            _reset(sK)
            # FLIP: render the neutralization set the pre-fix way (-c key=value argv). git's first-`=` split
            # mis-parses the =-name and the REAL filter FIRES -- the exact bypass the env fix closes.
            _cfgK = _obs_flt._filter_neutralizing_config(_git_flt, str(sK))
            _old_c = []
            for _k, _v in _cfgK:
                _old_c += ["-c", "{}={}".format(_k, _v)]
            _obs_flt._run_git(_git_flt, str(sK), _old_c + _status_args)
            check("U28k FLIP: the old -c key=value mechanism leaves the =-named filter EXECUTABLE (the bypass; teeth)",
                  (sK / "SENTINEL").exists())

            # (l) ACCURACY residual (F-OPF-STATUSFILTER-LFS-FALSEPOS): an EXTERNAL NORMALIZING clean filter
            # (git-lfs shape: index holds the CLEANED blob, worktree holds the smudged body, required=true)
            # makes a genuinely-CLEAN store read DIRTY in the racy-clean window under neutralization (the
            # cleaned blob cannot be reproduced, so raw worktree bytes mismatch the index) -- a FAIL-CLOSED
            # over-refusal (never a false-clean, never a filter exec). A digit-stripping clean filter models
            # the normalization: worktree "abc123" -> cleaned index "abc". No sentinel: this vector is the
            # false-positive, not exec.
            sL = base / "u28l-normalizing"
            sL.mkdir()
            machL = build_store(sL, commit=False)
            _normtgt = opf._opf_views._spec_destination("DECISIONS.md")[1]
            (sL / _normtgt).write_text("abc123\n", encoding="utf-8")
            git_call(sL, ["config", "filter.norm.clean", "sed 's/[0-9]//g'"])
            git_call(sL, ["config", "filter.norm.required", "true"])   # git-lfs shape: a required driver
            (sL / ".gitattributes").write_text("{} filter=norm\n".format(_normtgt), encoding="utf-8")
            git_call(sL, ["--literal-pathspecs", "add", "-A"])   # runs clean: index holds "abc\n"
            git_call(sL, ["commit", "-m", "seed normalizing store"])
            check("U28l fixture is the git-lfs shape (index CLEANED != smudged worktree)",
                  git_call(sL, ["show", ":{}".format(_normtgt)]) == "abc\n"
                  and (sL / _normtgt).read_text() == "abc123\n")
            # positive control: with the filter ACTIVE (un-neutralized) the store is genuinely CLEAN (git
            # re-runs clean -> "abc" == index), so the dirty read below is purely the neutralization residual.
            os.utime(str(sL / _normtgt), _FUTURE)
            _natL = _obs_flt._run_git(_git_flt, str(sL), _status_args)
            check("U28l positive control: with the filter active the normalizing store is genuinely CLEAN",
                  _natL.completed and _natL.out.strip() == b"")
            os.utime(str(sL / _normtgt), _FUTURE)
            dL = _probe(_git_flt, str(sL), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28l FAIL-CLOSED residual: the normalizing store reads DIRTY under neutralization (over-refuse)",
                  _normtgt in dL)
            # operator-clear refusal end-to-end: `opf upgrade` refuses (exit 2) and NAMES the filter and the
            # settle-the-worktree remedy, rather than a bare "dirty".
            os.utime(str(sL / _normtgt), _FUTURE)
            _rcL, _outL = upgrade(sL)
            check("U28l upgrade refuses (exit 2) the normalizing-filter store in the racy-clean window",
                  _rcL == 2)
            check("U28l refusal is operator-clear: names the git-lfs-shape filter and settling the worktree",
                  "git-lfs-shape" in _outL and "Settle the worktree" in _outL)
            # genuine dirt is STILL detected alongside the filter (neutralization removes exec, not detection).
            (machL / "genuine.txt").write_text("real\n", encoding="utf-8")
            os.utime(str(sL / _normtgt), _FUTURE)
            dL2 = _probe(_git_flt, str(sL), [_opf_store.WORKING_DIRNAME], _lease_flt)
            check("U28l genuine dirt (untracked file) still detected under neutralization",
                  "{}/genuine.txt".format(_mach_rel_flt) in dL2)

        if failures:
            for label in failures:
                print("check_opf_upgrade: FAIL: " + label, file=sys.stderr)
            return EXIT_FINDING
        print("check_opf_upgrade: PASS ({} executed fixture assertions)".format(len(checked)))
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  missing resources and harness errors cannot skip clean
        print("check_opf_upgrade: cannot evaluate fixture suite: {}; exit 2".format(ascii(exc)),
              file=sys.stderr)
        return EXIT_ERROR


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args not in ([], ["--self-test"]):
            print("usage: check_opf_upgrade.py [--self-test]", file=sys.stderr)
            return EXIT_ERROR
        return _suite()
    except Exception as exc:  # noqa: BLE001  final class-width fail-closed backstop
        print("check_opf_upgrade: cannot evaluate: {}; exit 2".format(ascii(exc)), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
