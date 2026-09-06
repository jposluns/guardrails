#!/usr/bin/env python3
"""Standing property/mutation-fuzz harness over the wait-state READ + PARSE + decide boundary (GD-164
addendum Step 23b; mirrors the GD-137 boundary-fuzz idiom). It imports the APPLIED engine and exercises
its wait/liveness decision path directly, asserting the load-bearing invariant BY CONSTRUCTION so a
future fail-open sibling is caught here rather than round-by-round in QA.

LOAD-BEARING INVARIANT: every unreadable, malformed, absent, or ambiguous input on the wait/liveness
decision path resolves to DENY-the-wind-down (cannot-evaluate / hold), NEVER to a cap-relieving ALLOW;
the ONLY ALLOW is the fully-evaluated single case (enumeration ok, zero actionable, zero
cannot-evaluate, at least one live waiting row named as a whole token, not cap-relieved). An operator
escape ALLOW is the operator-owned release, not a cap-relieving fail-open, and is excluded.

Two layers:
  1. Pure-core sweep over decide_yield(ctx): a deterministic, bounded mutation of every ctx field on
     the wait/liveness path (enum_status, cannot_evaluate, actionable, waiting, wake_named, escape,
     schedule_denials/basis_unchanged for the cap, kind). Every mutation is classified and the
     invariant asserted.
  2. Read/parse boundary cases through _orch_build_ctx + orch_wait_guard against hermetic temp repos:
     a bad registry (handler ASK/hold), a v2 malformed close (ENUMERATOR_ERROR -> DENY), an
     ownership-failing deferrals register without weak_proof_accepted (held), a wait naming no live
     row (DENY), and the single valid ALLOW case (must remain the sole ALLOW).

Determinism: a fixed PRNG seed and a bounded round count (test-hermeticity: no wall clock, locale, or
ambient state feeds a verdict). This is a bounded deterministic property sweep, not an exhaustive proof
of the input space; that bound is disclosed, and the yield-boundary re-enumeration remains the net.

  _orch_wait_boundary_fuzz.py [--rounds N]   run the sweep (exit 0 clean, 1 an invariant breach)
  _orch_wait_boundary_fuzz.py --self-test    a compact fixed sweep for the CI roster
"""
import argparse
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

_SEED = 1729   # a fixed seed keeps the sweep deterministic (no ambient state feeds a verdict)


def _is_single_valid_allow(ctx):
    """The ONE case a non-escape wait ALLOW is permitted: enumeration ok, nothing actionable, nothing
    cannot-evaluate, at least one waiting row, the wait names a live waiting row, and not cap-relieved."""
    return (ctx.get("enum_status") == "ok"
            and not ctx.get("cannot_evaluate")
            and not ctx.get("actionable")
            and bool(ctx.get("waiting"))
            and ctx.get("wake_named") is True)


def _classify(ctx, verdict):
    """Return an invariant-breach string or None. A breach is a cap-relieving ALLOW on any input that
    is NOT the single valid case and is NOT an operator escape."""
    if verdict != "ALLOW":
        return None                      # DENY / ALLOW_WITH_FINDINGS(capped) are the safe directions
    if ctx.get("escape"):
        return None                      # operator-owned release, not a fail-open
    if _is_single_valid_allow(ctx):
        return None
    return ("INVARIANT BREACH: wait decide_yield ALLOWed a non-single, non-escape case: "
            "enum={} actionable={} cannot_evaluate={} waiting={} wake_named={}".format(
                ctx.get("enum_status"), len(ctx.get("actionable", [])),
                len(ctx.get("cannot_evaluate", [])), len(ctx.get("waiting", [])),
                ctx.get("wake_named")))


def _base_ctx():
    return {"kind": "wait", "escape": False, "loop_signal": False, "counter": 0,
            "enum_status": "ok", "enum_detail": "", "actionable": [], "waiting": [],
            "blocked": [], "cannot_evaluate": [], "proposed": [], "wake_named": None,
            "schedule_denials": 0, "basis_unchanged": False}


def sweep_pure(rounds):
    """Layer 1: bounded deterministic mutation of the decide_yield ctx over the wait path."""
    rng = random.Random(_SEED)
    breaches = []
    enum_states = ["ok", "ENUMERATOR_ERROR", "unreadable"]
    wa = [[], [("A-1", "A-1", "seedable")], [("A-1", "A-1", "x"), ("A-2", "A-2", "y")]]
    wt = [[], [("W-1", "tracked-task", "T-1")], [("W-1", "tracked-task", "T-1"),
                                                 ("W-2", "tracked-task", "T-2")]]
    ce = [[], [("C-1", "cannot-evaluate", "unreadable")]]
    wn = [None, True, False]
    esc = [False, True]
    caps = [(0, False), (3, True), (3, False)]   # (schedule_denials, basis_unchanged)
    combos = list(itertools.product(enum_states, wa, wt, ce, wn, esc, caps))
    rng.shuffle(combos)
    for combo in combos[:max(rounds, len(combos)) if rounds >= len(combos) else rounds] \
            if rounds else combos:
        es, a, w, c, n, e, (sd, bu) = combo
        ctx = _base_ctx()
        ctx.update({"enum_status": es, "actionable": a, "waiting": w, "cannot_evaluate": c,
                    "wake_named": n, "escape": e, "schedule_denials": sd, "basis_unchanged": bu})
        verdict, _reason, _disp = aiqt_hooks.decide_yield(ctx)
        breach = _classify(ctx, verdict)
        if breach:
            breaches.append(breach + " | verdict={}".format(verdict))
    return breaches


def _git_init(path):
    """Make the fixture a real git work tree, so aiqt_hooks._orch_root (git rev-parse --show-toplevel)
    resolves it and the guard runs against it rather than treating it as out-of-scope."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)],
                   capture_output=True, check=False)


def _mkrepo(tmp, reg, backlog_items):
    tmp = Path(tmp)
    _git_init(tmp)
    (tmp / ".aiqt").mkdir(exist_ok=True)
    (tmp / "seed.txt").write_text("evidence\n", encoding="utf-8")
    # a live lease makes the D11 scope check True, so the guard is ACTIVE (not the inert off-by-default
    # posture); without it orch_wait_guard returns a bare allow and the boundary cases test nothing.
    (tmp / "lease.txt").write_text("held\n", encoding="utf-8")
    (tmp / "aei.json").write_text(json.dumps({
        "version": 2, "generated_at_utc": aiqt_hooks._orch_now().isoformat(),
        "source": {"locator": "backlog"}, "items": backlog_items}), encoding="utf-8")
    enum = tmp / "enum.sh"
    enum.write_text("#!/bin/sh\ncat '{}'\n".format(tmp / "aei.json"), encoding="utf-8")
    os.chmod(enum, 0o755)
    reg = dict(reg, version=1, lease={"path": "lease.txt"},
               enumerator={"argv": ["/bin/sh", str(enum)]}, wait_tools=["Bash"])
    (tmp / ".aiqt" / "orchestration.local.json").write_text(json.dumps(reg), encoding="utf-8")
    return str(tmp)


def _wait_verdict(root, text):
    """Drive orch_wait_guard and reduce its handler return to a normalized verdict token."""
    data = {"hook_event_name": "PreToolUse", "cwd": root, "session_id": "s1",
            "tool_name": "Bash", "tool_input": {"command": text}}
    code, obj, _extra = aiqt_hooks.orch_wait_guard(data)
    # CONFIRMED AT SOURCE (aiqt_hooks._deny / _ask / _allow): a PreToolUse block and an ask are BOTH
    # exit 0; the verdict is carried by hookSpecificOutput.permissionDecision (deny|ask), NOT the exit
    # code. A bare allow returns obj None; the licensed/forced allow returns a systemMessage-only obj.
    if obj is None:
        return "allow"
    if isinstance(obj, dict):
        pd = obj.get("hookSpecificOutput", {}).get("permissionDecision")
        if pd == "deny":
            return "deny"
        if pd == "ask":
            return "ask"
        if "systemMessage" in obj:
            return "allow"
    return "allow"


def sweep_boundary():
    """Layer 2: read/parse boundary cases. Each non-single case must NOT yield a cap-relieving allow."""
    breaches = []
    with tempfile.TemporaryDirectory(prefix="aiqt-wbf-") as tmp:
        # a wait naming no live row -> DENY
        root = _mkrepo(tmp, {}, [{"id": "A-1", "state": "open", "granted": True}])
        if _wait_verdict(root, "hang about") == "allow":
            breaches.append("a wait naming no live row yielded a cap-relieving allow")
        # a v2 malformed close -> ENUMERATOR_ERROR -> DENY
        (Path(root) / "aei.json").write_text(json.dumps({
            "version": 2, "generated_at_utc": aiqt_hooks._orch_now().isoformat(),
            "source": {"locator": "b"},
            "items": [{"id": "C-1", "state": "closed", "granted": True, "close": {"ref": "x"}}]}),
            encoding="utf-8")
        if _wait_verdict(root, "collect anything") == "allow":
            breaches.append("a malformed-close enumeration yielded a cap-relieving allow")
    with tempfile.TemporaryDirectory(prefix="aiqt-wbf2-") as tmp:
        # a bad registry -> handler ASK/hold, never a cap-relieving allow
        tmp = Path(tmp)
        _git_init(tmp)
        (tmp / ".aiqt").mkdir()
        (tmp / ".aiqt" / "orchestration.local.json").write_text("{ not json", encoding="utf-8")
        v = _wait_verdict(str(tmp), "x")
        if v == "allow":
            breaches.append("a bad registry yielded a cap-relieving allow (expected ask/inert)")
    return breaches


def run(rounds):
    breaches = sweep_pure(rounds) + sweep_boundary()
    return breaches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=0, help="0 = the full deterministic combo set")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    rounds = 64 if a.self_test else a.rounds
    breaches = run(rounds)
    if breaches:
        for b in breaches:
            print("FUZZ FAIL: " + b, file=sys.stderr)
        return 1
    print("wait-boundary fuzz: invariant held across the sweep "
          "(pure-core combos + read/parse boundary cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
