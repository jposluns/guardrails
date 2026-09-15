#!/usr/bin/env python3
"""OPF store-schema upgrade gate (spec 9.2), exercised exclusively through isolated temporary fixtures.

Both the default entry and --self-test run the fixture suite. There is no live-adopter mutation leg and
no root option: the gate drives `opf upgrade` (isolated, -I -B) over a BYTE-PINNED 1.0.0 store built beneath
a fresh temporary git repository, and asserts the full 1.0.0 -> 1.1.0 contract end to end.

The 1.0.0 fixture bytes are FROZEN literals below, not regenerated from the current builders, so the fixture
cannot silently drift into some other schema as the tooling evolves: it is the store an already-shipped 1.0.0
tool would have written. The gate re-asserts the fixture's own canonicity (the manifest and counters
round-trip through the canonical emitter), which is exactly the precondition `opf upgrade` enforces, so a
future emitter change that would invalidate the frozen fixture is caught here rather than in the field.

Vectors: the happy 1.0.0 -> 1.1.0 upgrade lands doctor-VALID with spec_version bumped, decision_support
retired, the three baseline type rows and two view rows added, counters extended CN/MD/PP, and the three
empty indexes created; a second run is a byte no-op (idempotence); a pre-existing empty maintainer_decision
index is SKIPPED and preserved byte-for-byte (create-only, no data loss); a store already at 1.1.0 is a
no-op; an above-tooling (2.0.0) store is refused; a hand-edited (comment-bearing, non-canonical) manifest is
refused; a NOT-ADOPTED root is NOT APPLICABLE. Missing git or unusable temporary storage returns 2, never a
clean skip.

Exit convention: 0 observed assertions pass; 1 an assertion fails; 2 cannot evaluate the harness.
"""
import os
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
# declares the retired decision_support module. DECISIONS.md already lists the four decision sources, so the
# spec-9.2 allowed delta (which does not touch DECISIONS.md) upgrades it without a view-source change.
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
sources = ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]
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

        failures = []
        checked = []

        def check(label, condition):
            checked.append(label)
            if not condition:
                failures.append(label)

        machine = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)

        # Fixture canonicity (the upgrade's own precondition): the frozen manifest and counters round-trip
        # through the canonical emitter, so this frozen fixture is a store the tool would accept, and an
        # emitter change that would break the field precondition fails HERE.
        check("frozen manifest is canonical",
              _opf_emit.emit_checked(tomllib.loads(_FIX_MANIFEST)) == _FIX_MANIFEST)
        check("frozen counters is canonical",
              _opf_emit.emit_checked(tomllib.loads(_FIX_COUNTERS)) == _FIX_COUNTERS)
        check("frozen fixture declares spec_version 1.0.0",
              tomllib.loads(_FIX_MANIFEST)["devprocess"]["spec_version"] == "1.0.0")

        def git_call(store, args):
            proc = subprocess.run(
                ["git", "-C", str(store), "-c", "init.templateDir=", "-c", "init.defaultBranch=main",
                 "-c", "user.email=t@t", "-c", "user.name=t"] + list(args),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, env=env_holder["env"])
            if proc.returncode != 0:
                raise OSError("fixture git failed at {!r}: {}".format(str(store), proc.stderr))
            return proc.stdout

        env_holder = {}

        def build_store(store, manifest=_FIX_MANIFEST, counters=_FIX_COUNTERS, extra_files=None):
            """Write a frozen 1.0.0 store beneath `store` and commit it (doctor needs a committed HEAD)."""
            mach = store / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
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
            git_call(store, ["--literal-pathspecs", "add", "-A"])
            git_call(store, ["commit", "-m", "seed 1.0.0 store"])

        def upgrade(store):
            return _run_opf(["upgrade", "--root", str(store)], env_holder["env"])

        def doctor(store):
            return _run_opf(["doctor", "--root", str(store)], env_holder["env"])

        with tempfile.TemporaryDirectory(prefix="opf-upgrade-gate-") as temporary:
            base = Path(temporary).resolve()
            home = base / "home"
            home.mkdir()
            env_holder["env"] = dict(os.environ, HOME=str(home))
            env_holder["env"].pop("XDG_CONFIG_HOME", None)
            env_holder["env"].pop("XDG_CONFIG_DIRS", None)

            # 1) Happy path: 1.0.0 -> 1.1.0, doctor VALID, exact delta applied.
            s1 = base / "happy"
            s1.mkdir()
            build_store(s1)
            rc, out = upgrade(s1)
            check("happy upgrade exits 0", rc == EXIT_OK)
            check("happy upgrade reports staged not committed",
                  "staged, NOT committed" in out and '"event": "upgraded"' in out)
            mach1 = s1 / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
            man1 = tomllib.loads((mach1 / _opf_store.MANIFEST_NAME).read_text(encoding="utf-8"))
            # OPFiles rebrand: the migration renames the base table [devprocess] -> [opf] and its discovery
            # token, as part of the same 1.0.0 -> 1.1.0 delta (spec 9.2).
            check("base table renamed to [opf]",
                  "opf" in man1 and "devprocess" not in man1)
            check("discovery token renamed to opf", man1["opf"].get("standard") == "opf")
            check("spec_version bumped to 1.1.0", man1["opf"]["spec_version"] == "1.1.0")
            check("base table body otherwise carried over", all(
                man1["opf"].get(k) == v for k, v in (
                    ("layout", "inline"), ("posture", "required"), ("import_status", "none"))))
            check("decision_support module retired", "decision_support" not in man1["modules"])
            check("three baseline type rows added", all(
                man1["types"].get(t) == {"namespace": _opf_store.BASELINE_TYPES[t]}
                for t in ("contribution", "maintainer_decision", "preference_pattern")))
            check("two new view rows added",
                  "CONTRIBUTIONS.md" in man1["views"] and "DECISIONS.toml" in man1["views"])
            cnt1 = tomllib.loads((mach1 / _opf_check.COUNTERS_NAME).read_text(encoding="utf-8"))["counters"]
            check("counters extended CN/MD/PP = 0",
                  cnt1.get("CN") == 0 and cnt1.get("MD") == 0 and cnt1.get("PP") == 0)
            check("three empty indexes created", all(
                (mach1 / (t + _opf_check.INDEX_SUFFIX)).is_file()
                for t in ("contribution", "maintainer_decision", "preference_pattern")))
            drc, dout = doctor(s1)
            check("happy upgraded store is doctor-VALID", drc == EXIT_OK and "integrity: VALID" in dout)

            # 2) Idempotence: a second run is a byte no-op reporting already-current.
            before = _snapshot(s1)
            rc2, out2 = upgrade(s1)
            check("idempotent second run exits 0", rc2 == EXIT_OK)
            check("idempotent second run reports no-op", "already at spec_version 1.1.0" in out2)
            check("idempotent second run is a byte no-op", _snapshot(s1) == before)

            # 3) Pre-existing empty maintainer_decision index is SKIPPED and preserved (no data loss).
            s3 = base / "preexisting-md"
            s3.mkdir()
            build_store(s3, extra_files={"maintainer_decision" + _opf_check.INDEX_SUFFIX: _FIX_INDEX})
            mach3 = s3 / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
            md_before = (mach3 / ("maintainer_decision" + _opf_check.INDEX_SUFFIX)).read_bytes()
            rc3, out3 = upgrade(s3)
            check("pre-existing MD index: upgrade exits 0", rc3 == EXIT_OK)
            check("pre-existing MD index not recreated",
                  '"created_indexes": ["contribution", "preference_pattern"]' in out3)
            check("pre-existing MD index preserved byte-for-byte",
                  (mach3 / ("maintainer_decision" + _opf_check.INDEX_SUFFIX)).read_bytes() == md_before)
            drc3, dout3 = doctor(s3)
            check("pre-existing MD index store is doctor-VALID",
                  drc3 == EXIT_OK and "integrity: VALID" in dout3)

            # 4) A store already at 1.1.0 is the idempotent no-op, exercised by vector (2) above.

            # 5) Above-tooling refusal: a 2.0.0 store is never downgraded.
            s5 = base / "above"
            s5.mkdir()
            above_manifest = _FIX_MANIFEST.replace('spec_version = "1.0.0"', 'spec_version = "2.0.0"')
            build_store(s5, manifest=above_manifest)
            rc5, out5 = upgrade(s5)
            check("above-tooling store refused (exit 2)", rc5 == EXIT_ERROR)
            check("above-tooling refusal names the reason", "ABOVE the 1.1.0" in out5)

            # 6) Non-canonical (hand-edited, comment-bearing) manifest is refused fail-closed.
            s6 = base / "noncanonical"
            s6.mkdir()
            build_store(s6, manifest="# hand-edited by an adopter\n" + _FIX_MANIFEST)
            rc6, out6 = upgrade(s6)
            check("non-canonical manifest refused (exit 2)", rc6 == EXIT_ERROR)
            check("non-canonical refusal names canonicity", "canonical" in out6)

            # 7) NOT-ADOPTED root: nothing to upgrade, NOT APPLICABLE (exit 0).
            s7 = base / "not-adopted"
            s7.mkdir()
            rc7, out7 = upgrade(s7)
            check("not-adopted root is NOT APPLICABLE (exit 0)",
                  rc7 == EXIT_OK and "NOT APPLICABLE" in out7)

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
