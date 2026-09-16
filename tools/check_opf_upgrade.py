#!/usr/bin/env python3
"""OPF store-schema upgrade gate (spec 9.2), exercised exclusively through isolated temporary fixtures.

Both the default entry and --self-test run the fixture suite. There is no live-adopter mutation leg and
no root option: the gate drives `opf upgrade` (isolated, -I -B) over BYTE-PINNED 1.0.0 stores built beneath
fresh temporary git repositories, and asserts the 1.0.0 -> 1.1.0 contract over an enumerated adopter-shape
matrix, a set of refusal fixtures, direct postcondition unit vectors, and a seeded migration property test.

FIXTURE FIDELITY. The 1.0.0 fixture bytes are FROZEN, anchored on the frozen `_FIX_MANIFEST` /
`_FIX_COUNTERS` baseline (not regenerated from the current builders), so the fixture cannot silently drift
into some other schema as the tooling evolves: it is the store an already-shipped 1.0.0 tool would have
written. Module-enabled and view-omitting variants are the frozen baseline PLUS exactly the rows a valid
1.0.0 store of that shape must carry (a module boolean, `[types]` rows with normative namespaces, counters,
index files), derived by explicit canonical string operations and RE-VERIFIED canonical in-gate through the
canonical emitter (the same precondition `opf upgrade` enforces), so an emitter change that would invalidate
any frozen fixture is caught HERE rather than in the field. The genuine-1.0.0-store shape of each module-
enabled variant was additionally graded doctor-VALID at authoring time by the actual merge-base 1.0.0 tooling
(commit 1c90fbb, `tools/opf.py doctor`); at 1.0.0 the module-tier records (maintainer_decision /
preference_pattern) are schema-DEFERRED by the baseline validator and become fully-validated baseline records
only at 1.1.0, so a POPULATED module-tier fixture's records are validated at run time by the actual
upgrade -> 1.1.0 doctor leg here rather than by the 1.0.0 baseline. In-gate re-verification is canonicity-only;
1.0.0-doctor fidelity is authoring-time evidence.

VECTOR ROSTER (U1-U25, P1):
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
  U13 baseline plus a malformed lease.toml: exit 2 (present is held, never absent); not deleted; unchanged.
  U14 baseline plus an uncommitted worklog.toml edit: exit 2 BEFORE any mutation; the refusal names the
      dirty path and advises commit-your-changes, never a whole-tree restore; the owner edit intact.
  U15 [types.contribution] pre-declared: exit 2 naming contribution as an impossible 1.0.0 shape; unchanged.
  U16 governance=false plus [types.maintainer_decision]: exit 2 naming the module inconsistency; unchanged.
  U17 the above-tooling, non-canonical, NOT-ADOPTED, and partial-1.1.0 triage refusals (with the scoped
      recovery text).
  U18 postcondition unit vectors: call opf._upgrade_postcondition directly with hand-mutated new models; each
      mutation refuses and the genuine planner output passes (the check that fails without the m1 fix). Also
      the R6 porcelain-grammar refusal vectors: opf._upgrade_parse_porcelain refuses a lone NUL, a non-NUL-
      terminated payload, and a mis-framed record (never a clean-empty result), and excludes a nested-store
      lease after the prefix strip; PLUS the R6 fail-open vectors (a MALFORMED status -- ZZ, a blank pair, a
      rename R, a copy C -- for the lease path refuses rather than being silently dropped by the exclusion,
      while a well-formed dirty lease record is still excluded); PLUS the status-PAIR vectors (FIX2/FIX3: an
      IMPOSSIBLE pair -- UT/TU with U only legal in an unmerged pair, ignored !! which --ignored-less status
      cannot emit, AND the impossible ORDINARY pairs a {space,M,T,A,D} cartesian would wrongly admit, DM/DT/DA
      (X=D pairs only with space) and MA/TA (Y=A pairs only with space X) -- each refuses whole-pair, not
      per-char, for the lease AND a non-lease path, while a positive sweep of every emittable pair (all 17
      ordinary pairs including ' A' intent-to-add and 'D ', plus ?? and the 7 unmerged) still parses; fails
      under the old per-char check or a cartesian superset); and the
      R1b leading-space prefix test (_upgrade_probe_dirty keeps a " leading/"-prefixed lease excluded; fails
      under the old .strip()).
  U19 R1 recovery-text root-binding: opf._upgrade_recovery_text called directly with DISTINCT store/product
      roots; the `.working` restore + created-file removal name the store root, the product target the product
      root (fails without the distinct-roots fix).
  U20 R8 module-coupling refusal: governance=true with maintainer_action but maintainer_decision ABSENT (a
      module-inconsistent 1.0.0 shape the delta would silently cure) refuses exit 2 before mutation; unchanged.
  U21 R2 absent-table migration: the missing [modules], missing [views], and both-missing origins (each oracle-
      graded doctor-VALID at merge-base 1c90fbb) migrate to a doctor-VALID 1.1.0 store; an absent table is
      never invented as an empty table beyond the two new views [views] must carry.
  U22 R1 nested-store held lease: a store root BELOW the git repo root with a foreign held lease reaches step
      4's never-seize message, not step 3's dirty-store remedy (the lease_excl prefix-normalization fix).
  U23 R3 FIFO lease: a FIFO at lease.toml refuses PROMPTLY (bounded, no blocking hang) naming a present non-
      regular lease; the FIFO is untouched.
  U24 R4 never-seize on a mid-acquisition failure: with journal._write_all monkeypatched to fail after the
      O_EXCL create, _upgrade_acquire_lease (called direct) LEAVES the lease as a reconcilable leftover rather
      than an ownership-blind by-name unlink -- surfacing a reconcilable _UpgradeError -- and when the write
      first REPLACES the lease with a peer holder's bytes, that replacement is NOT removed (the never-seize
      guarantee; the old by-name unlink deleted the replacement).
  U25 R5 release-before-success: with _upgrade_release_lease monkeypatched to fail, a valid store exits 2 and
      emits NO success line (success is never reported over a still-held / failed-to-release lease). U25b
      (FIX1 release never-seize, class-width): _upgrade_acquire_lease returns the on-disk payload; after a
      peer REPLACES the lease with its own well-formed bytes, _upgrade_release_lease refuses (never-seize) and
      LEAVES the replacement, yet an ordinary release of this run's OWN lease still removes it (fails under the
      old ownership-blind unlink, which deleted the peer's lease).
  U26 R8 non-boolean module: the planner precondition refuses a non-boolean value on ANY module (governance,
      operational_policy, decision_support), matching the merge-base _validate_modules, while a fully-boolean
      module set still plans; end-to-end, a stored governance="x" refuses exit 2 before any mutation. FIX3: an
      UNKNOWN [modules] key refuses UPFRONT (merge-base _validate_modules parity), while the known-but-retired
      decision_support key still plans; fails without the upfront unknown-key check.
  U27 R1a relocated partial-recovery: a RELOCATED store (store_root != product_root) at spec_version 1.1.0 but
      not doctor-VALID names the STORE root (not the CLI product root) for the F2 .working restore advice.
  P1  a seeded migration property test: 12 generated genuine-VALID 1.0.0 variants (module subset with the
      G2 coupling, 0-2 records per migrated type with matching high-waters, DECISIONS.md declared/omitted,
      the decision_support key present/absent) each upgrade to a doctor-VALID 1.1.0 store with every index
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
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180, env=env)
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


def _suite():
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
                 "-c", "user.email=t@t", "-c", "user.name=t"] + list(args),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, env=env_holder["env"])
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
            env_holder["env"] = dict(os.environ, HOME=str(home))
            env_holder["env"].pop("XDG_CONFIG_HOME", None)
            env_holder["env"].pop("XDG_CONFIG_DIRS", None)

            # U1) Happy path: 1.0.0 -> 1.1.0, doctor VALID, exact delta applied.
            s1 = base / "u1-happy"
            s1.mkdir()
            mach1 = build_store(s1)
            rc, out = upgrade(s1)
            check("U1 upgrade exits 0", rc == EXIT_OK)
            check("U1 reports staged not committed",
                  "staged, NOT committed" in out and '"event": "upgraded"' in out)
            man1 = man_of(mach1)
            check("U1 base table renamed to [opf]", "opf" in man1 and "devprocess" not in man1)
            check("U1 discovery token renamed to opf", man1["opf"].get("standard") == "opf")
            check("U1 spec_version bumped to 1.1.0", man1["opf"]["spec_version"] == "1.1.0")
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
            check("U2 idempotent second run reports no-op", "already at spec_version 1.1.0" in out2)
            check("U2 idempotent second run is a byte no-op", _snapshot(s1) == before)

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

            # U14) baseline plus an uncommitted worklog.toml edit (the reproduced dirty-store case).
            s14 = base / "u14-dirty"
            s14.mkdir()
            mach14 = build_store(s14)
            (mach14 / _opf_check.WORKLOG_NAME).write_text(
                "entry = []\nschema = 1\n# an uncommitted owner edit\n", encoding="utf-8")
            before14 = _snapshot(s14)
            rc14, out14 = upgrade(s14)
            check("U14 dirty store refuses BEFORE mutation (exit 2)", rc14 == EXIT_ERROR)
            check("U14 refusal names the dirty path", "worklog.toml" in out14 and "clean" in out14)
            check("U14 advises commit-your-changes and offers no restore command (the dirt is the owner's)",
                  "Commit your store changes" in out14 and "restore --staged" not in out14)
            check("U14 owner edit intact and tree unchanged", _snapshot(s14) == before14)

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

            # U17) the above-tooling / non-canonical / NOT-ADOPTED / partial-1.1.0 triage refusals.
            s17a = base / "u17-above"
            s17a.mkdir()
            above_manifest = _FIX_MANIFEST.replace('spec_version = "1.0.0"', 'spec_version = "2.0.0"')
            build_store(s17a, manifest=above_manifest)
            rc17a, out17a = upgrade(s17a)
            check("U17 above-tooling store refused (exit 2)", rc17a == EXIT_ERROR)
            check("U17 above-tooling refusal names the reason", "ABOVE the 1.1.0" in out17a)

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

            # partial 1.1.0 (F2): take the VALID 1.1.0 store from U1, break it, re-run: never a false no-op.
            (mach1 / idx("maintainer_decision")).unlink()
            rc17d, out17d = upgrade(s1)
            check("U17 partial 1.1.0 store is not a false rc-0 no-op (exit 2)", rc17d == EXIT_ERROR)
            check("U17 partial store fail-closed names not-doctor-VALID", "NOT doctor-VALID" in out17d)
            check("U17 partial-store recovery offers the .working-scoped restore command",
                  "--literal-pathspecs restore --staged --worktree -- .working" in out17d)
            check("U17 partial-store recovery warns against a whole-tree restore",
                  "Never run a whole-tree restore" in out17d)
            drc17d, dout17d = doctor(s1)
            check("U17 partial 1.1.0 store doctor is not VALID (exit 2)", drc17d == EXIT_ERROR)

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
                    return opf._upgrade_parse_porcelain(raw, prefix, lease), None
                except opf._UpgradeError as exc:
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
            # copy C (impossible under --no-renames), ignored !! (--ignored not passed), U in a non-unmerged
            # position (UT/TU), AND the impossible ORDINARY pairs a {space,M,T,A,D} cartesian would wrongly
            # admit -- DM/DT/DA (git emits X=D only with a space Y) and MA/TA (git emits Y=A only with a space
            # X). Each must refuse on BOTH the lease path and a non-lease path; validating the whole XY pair
            # (FIX2), against the EXACT man-page enumeration not the cartesian (FIX3), is what closes it.
            _lease_rel = ".working/toml/lease.toml"
            _neg_pairs = ((b"ZZ", "out-of-vocabulary ZZ"), (b"  ", "blank status"),
                          (b"R ", "rename R"), (b"C ", "copy C"),
                          (b"UT", "impossible pair UT (U only in unmerged pairs)"),
                          (b"TU", "impossible pair TU (U only in unmerged pairs)"),
                          (b"!!", "ignored !! (--ignored not passed)"),
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
            # independent oracle) as the full emittable set: ?? untracked; the 17 ordinary pairs -- INCLUDING
            # ' A' (intent-to-add) and 'D ', whose silent omission by a future over-tightening a dropped vector
            # here would catch; and the seven unmerged pairs.
            for _vp in (b"??",
                        b" A", b" M", b" T", b" D",
                        b"M ", b"MM", b"MT", b"MD",
                        b"T ", b"TM", b"TT", b"TD",
                        b"A ", b"AM", b"AT", b"AD",
                        b"D ",
                        b"DD", b"AU", b"UD", b"UA", b"DU", b"AA", b"UU"):
                _vres, _verr = _grammar_ok(_vp + b" .working/toml/x\x00", prefix="", lease=None)
                check("U18/R6 valid pair {!r} parses (no over-refusal)".format(_vp),
                      _vres == [".working/toml/x"] and _verr is None)
            # a WELL-FORMED record is unaffected: a dirty lease record is still EXCLUDED (well-formed only),
            # a dirty non-lease record is still surfaced as dirt, and a clean tree still passes.
            _exok, _ = _grammar_ok(b" M " + _lease_rel.encode("utf-8") + b"\x00", prefix="", lease=_lease_rel)
            check("U18/R6 well-formed dirty lease record still excluded", _exok == [])
            _dnl, _ = _grammar_ok(b" M .working/toml/x\x00", prefix="", lease=_lease_rel)
            check("U18/R6 well-formed dirty non-lease record still surfaced", _dnl == [".working/toml/x"])
            check("U18/R6 clean tree (empty payload) still passes", _grammar_ok(b"")[0] == [])

            # R1b: the show-prefix normalization must strip ONLY the trailing newline, never LEADING
            # whitespace, so a store dir whose name begins with a space keeps its prefix and its lease is
            # correctly excluded. Drive _upgrade_probe_dirty with a stubbed git that reports a " leading/\n"
            # prefix and a modified-lease record under it. With the .strip() bug the leading space is lost,
            # the prefix no longer matches, and the lease surfaces as (spurious) dirt.
            import _opf_observe as _obs_r1b
            _GO = _obs_r1b._GitOutcome
            _orig_run_git = _obs_r1b._run_git
            try:
                def _fake_run_git(_git, _root, args, timeout=None):
                    if "rev-parse" in args:
                        return _GO(True, 0, b" leading/\n", b"")
                    return _GO(True, 0, b" M  leading/.working/toml/lease.toml\x00", b"")
                _obs_r1b._run_git = _fake_run_git
                _r1b_dirty = opf._upgrade_probe_dirty("git", base, [".working"],
                                                      ".working/toml/lease.toml")
            finally:
                _obs_r1b._run_git = _orig_run_git
            check("U18/R1b leading-space store prefix keeps the lease excluded (rstrip newline only)",
                  _r1b_dirty == [])

            # U19) R1: the post-mutation recovery text threads DISTINCT roots. Called directly (like U18):
            # `.working` restore + created-file removal name the STORE root; a product target names the
            # PRODUCT root. Without the fix (one root for both) the product line names the store root.
            _rt = opf._upgrade_recovery_text("/store/root", "/product/root",
                                             ["toml/contribution.index.toml"], ["VERSION"])
            check("U19 .working restore names the store root",
                  "git -C /store/root --literal-pathspecs restore --staged --worktree -- .working" in _rt)
            check("U19 created-file removal is under the store root",
                  "/store/root/toml/contribution.index.toml" in _rt and "/product/root/toml" not in _rt)
            check("U19 product target restore names the product root",
                  "git -C /product/root --literal-pathspecs restore --staged --worktree -- VERSION" in _rt)
            check("U19 inspect line names the store root",
                  "inspect first: git -C /store/root --literal-pathspecs status -- .working" in _rt)

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
            # doctor-VALID at merge-base 1c90fbb) migrates to a doctor-VALID 1.1.0 store.
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
            # _upgrade_acquire_lease directly, and assert it raises a reconcilable _UpgradeError AND leaves the
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
                    opf._upgrade_acquire_lease(fd24a, mrel24)
                except BaseException as _e24:
                    _r4_exc = _e24
            finally:
                _opf_store._journal._write_all = _orig_wa
                os.close(fd24a)
            check("U24 ordinary mid-acquisition failure raises a reconcilable _UpgradeError",
                  isinstance(_r4_exc, opf._UpgradeError) and "never seized" in str(_r4_exc))
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
                    opf._upgrade_acquire_lease(fd24b, mrel24)
                except BaseException:
                    _ns_raised = True
            finally:
                _opf_store._journal._write_all = _orig_wa
                os.close(fd24b)
            check("U24 never-seize: acquisition still raises", _ns_raised)
            check("U24 never-seize: the REPLACEMENT holder's lease is NOT removed",
                  (mach24b / _opf_check.LEASE_NAME).is_file()
                  and (mach24b / _opf_check.LEASE_NAME).read_bytes() == _peer24)

            # U25) R5: the lease is released BEFORE success is reported. Monkeypatch _upgrade_release_lease to
            # fail; a valid store must exit 2 with NO success line emitted (without the fix, success prints
            # first and only then does the finally's release fail). Driven in-process (like U18).
            import contextlib as _ctx
            import io as _io
            s25 = base / "u25-release-first"
            s25.mkdir()
            build_store(s25)
            _orig_rel = opf._upgrade_release_lease
            try:
                opf._upgrade_release_lease = lambda *a, **k: (_ for _ in ()).throw(
                    opf._UpgradeError("synthetic release failure"))
                _buf25 = _io.StringIO()
                with _ctx.redirect_stdout(_buf25), _ctx.redirect_stderr(_buf25):
                    rc25 = opf._cmd_upgrade(["--root", str(s25)])
                out25 = _buf25.getvalue()
            finally:
                opf._upgrade_release_lease = _orig_rel
            check("U25 a release failure surfaces exit 2", rc25 == EXIT_ERROR)
            check("U25 no success is reported when release fails (released-before-success)",
                  "staged, NOT committed" not in out25 and '"event": "upgraded"' not in out25)

            # U25b) FIX1 release never-seize (class-width): the RELEASE path (not only the acquisition path)
            # is ownership-verified. Acquire a lease, capture the payload, then have a peer REPLACE the lease
            # with its own well-formed bytes; releasing MUST NOT unlink the peer's replacement (the old
            # ownership-blind os.unlink deleted it -- a spec-5.7 never-seize violation). It raises a
            # reconcilable _UpgradeError and LEAVES the replacement in place, and a valid ordinary release of
            # this run's OWN lease still removes it. Driven directly (like U24).
            s25b = base / "u25b-release-never-seize"
            s25b.mkdir()
            mach25b = build_store(s25b)
            _lp25 = mach25b / _opf_check.LEASE_NAME
            fd25b = _opf_store._open_dir_nofollow(str(s25b.resolve()))
            _pay25 = opf._upgrade_acquire_lease(fd25b, mrel24)
            check("U25b acquire returns the exact on-disk lease payload (ownership token)",
                  _pay25 == _lp25.read_bytes())
            _peer25 = (b'acquired_at = "2026-03-03T00:00:00Z"\nholder = "peer-runner"\n'
                       b'operation = "upgrade"\nschema = 1\n')
            _lp25.unlink()
            _lp25.write_bytes(_peer25)              # peer replaces our lease with its own well-formed lease
            _seize_raised = False
            try:
                opf._upgrade_release_lease(fd25b, mrel24, _pay25)
            except opf._UpgradeError as _e25:
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
            opf._upgrade_release_lease(fd25b, mrel24, _pay25)
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

            # U27) R1a: a RELOCATED store (store_root != product_root) whose F2 partial-recovery advice must
            # name the STORE root for the .working restore, not the CLI product root. Build a product repo, a
            # store in a subdir with the pointer at the product root, migrate to VALID 1.1.0 and commit, then
            # break the store so the F2 partial-recovery branch fires. With the R1a fix the .working restore
            # is `git -C <store-subdir>`; without it (CLI product root) it names the product root.
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
            check("U27 relocated store migrates to 1.1.0 (exit 0)", rc27a == EXIT_OK)
            git_call(prod27, ["--literal-pathspecs", "add", "-A"])
            git_call(prod27, ["commit", "-m", "commit staged migration"])
            (mach27 / idx("maintainer_decision")).unlink()
            rc27b, out27b = upgrade(prod27)
            check("U27 relocated partial store fails closed (exit 2)", rc27b == EXIT_ERROR)
            check("U27 relocated partial store names not-doctor-VALID", "NOT doctor-VALID" in out27b)
            _want27 = ("git -C {} --literal-pathspecs restore --staged --worktree -- .working"
                       .format(_shlex_r1a.quote(str(store27))))
            _bad27 = ("git -C {} --literal-pathspecs restore --staged --worktree -- .working"
                      .format(_shlex_r1a.quote(str(prod27))))
            check("U27 relocated .working restore names the STORE root, not the product root (R1a)",
                  _want27 in out27b and _bad27 not in out27b)

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
