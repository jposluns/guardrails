#!/usr/bin/env python3
"""Standing property/mutation-fuzz harness over the wait-state READ + PARSE + decide boundary (GD-164
addendum Step 23b; mirrors the GD-137 boundary-fuzz idiom). It imports the APPLIED engine and exercises
its wait/liveness decision path directly, asserting the load-bearing invariant BY CONSTRUCTION so a
future fail-open sibling is caught here rather than round-by-round in QA.

LOAD-BEARING INVARIANT: every unreadable, malformed, absent, or ambiguous input on the wait/liveness
decision path resolves to DENY-the-wind-down (cannot-evaluate / hold), NEVER to a cap-relieving ALLOW.
The ONLY plain ALLOW is the fully-evaluated single case (enumeration ok, zero actionable, zero
cannot-evaluate, at least one live waiting row named as a whole token, not cap-relieved). The ONLY
ALLOW_WITH_FINDINGS is the sanctioned repeated-same-basis ACTIONABLE-work wedge (a recorded forced
exit); a cap-relieving ALLOW_WITH_FINDINGS under an ignorance path (enum!=ok, a cannot-evaluate item,
or an unnamed/absent wait target) is a BREACH (B1). An operator escape ALLOW is the operator-owned
release, not a cap-relieving fail-open, and is excluded.

Two layers:
  1. Pure-core sweep over decide_yield(ctx): a deterministic, bounded mutation of every ctx field on
     the wait/liveness path (enum_status, cannot_evaluate, actionable, waiting, wake_named, escape,
     schedule_denials/basis_unchanged for the cap). Every mutation is classified and the invariant
     asserted, INCLUDING that a capped ignorance path is DENY (never a cap-relieving ALLOW_WITH_FINDINGS)
     and that the only sanctioned cap relief is the actionable-work wedge.
  2. Read/parse boundary cases through _orch_build_ctx + orch_wait_guard against hermetic temp repos:
     a bad registry (handler ASK/hold), a v2 malformed close (ENUMERATOR_ERROR -> DENY), a wait naming
     no live row (DENY), and the single valid ALLOW case (a live ledger row the wait names -> the sole
     licensed ALLOW).

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


def _capped(ctx):
    """The cap-relief basis: the shared schedule/wait denial cap reached on an UNCHANGED basis."""
    return (ctx.get("schedule_denials", 0) >= aiqt_hooks._ORCH_SCHEDULE_CAP
            and ctx.get("basis_unchanged") is True)


def _is_ignorance(ctx):
    """An IGNORANCE path: a non-ok enumeration OR at least one cannot-evaluate item. Ignorance must
    resolve to DENY on EVERY operation kind, and is NEVER cap-relieved (B1/B5-class)."""
    return ctx.get("enum_status") != "ok" or bool(ctx.get("cannot_evaluate"))


def _is_wait_absent_target(ctx):
    """kind=wait only: an ABSENT or UNNAMED wait target (no waiting row, or the wait names none live).
    This is an ignorance path denied UNCONDITIONALLY, even with actionable work at the cap (B1)."""
    return not ctx.get("waiting") or ctx.get("wake_named") is not True


def _is_wait_sanctioned_cap_relief(ctx):
    """The ONLY case a wait ALLOW_WITH_FINDINGS is sanctioned: the single-live-waiting-row wedge - enum
    ok, NO cannot-evaluate, at least one ACTIONABLE item, a NAMED LIVE WAITING ROW (waiting non-empty
    AND wake_named True), and the cap reached on an unchanged basis. B1: waiting+wake_named are REQUIRED,
    so an absent/unnamed target can never buy the relief (the residual this harness must make impossible)."""
    return (ctx.get("enum_status") == "ok"
            and not ctx.get("cannot_evaluate")
            and bool(ctx.get("actionable"))
            and bool(ctx.get("waiting"))
            and ctx.get("wake_named") is True
            and _capped(ctx))


def _is_schedule_sanctioned_cap_relief(ctx):
    """The ONLY case a schedule_idle ALLOW_WITH_FINDINGS is sanctioned: the actionable-work wedge - enum
    ok, NO cannot-evaluate, at least one ACTIONABLE item, and the cap reached on an unchanged basis."""
    return (ctx.get("enum_status") == "ok"
            and not ctx.get("cannot_evaluate")
            and bool(ctx.get("actionable"))
            and _capped(ctx))


def _classify(ctx, verdict):
    """Return an invariant-breach string or None, over ANY operation kind (stop | schedule_idle | wait)
    below the stop loop bound (counter 0). The load-bearing invariants, asserted BY CONSTRUCTION:
      (1) an operator escape ALLOW is the operator-owned release, excluded;
      (2) IGNORANCE (enum!=ok or a cannot-evaluate item) must be DENY on every kind, never cap-relieved;
      (3) for a wait, an ABSENT/UNNAMED target must be DENY even with actionable work at the cap (B1);
      (4) any ALLOW_WITH_FINDINGS must be the kind's SANCTIONED cap-relief wedge (for a wait, the wedge
          REQUIRES a named live waiting row) - a cap relief anywhere else is a breach;
      (5) any plain ALLOW must be the kind's fully-evaluated clean case (for a wait, the single
          live-waiting-row case)."""
    kind = ctx.get("kind")
    if ctx.get("escape"):
        return None                              # (1) operator-owned release
    if verdict == "DENY":
        return None                              # DENY is unconditionally safe
    tag = ("enum={} kind={} actionable={} cannot_evaluate={} waiting={} wake_named={} "
           "schedule_denials={} basis_unchanged={}").format(
               ctx.get("enum_status"), kind, len(ctx.get("actionable", [])),
               len(ctx.get("cannot_evaluate", [])), len(ctx.get("waiting", [])),
               ctx.get("wake_named"), ctx.get("schedule_denials"), ctx.get("basis_unchanged"))
    # (2)+(3): an ignorance path (or, for a wait, an absent/unnamed target) that did NOT DENY is a breach.
    if _is_ignorance(ctx) or (kind == "wait" and _is_wait_absent_target(ctx)):
        return "INVARIANT BREACH: an ignorance/absent-target path was NOT denied ({}): {}".format(
            verdict, tag)
    if verdict == "ALLOW_WITH_FINDINGS":
        sanctioned = (_is_wait_sanctioned_cap_relief(ctx) if kind == "wait"
                      else _is_schedule_sanctioned_cap_relief(ctx) if kind == "schedule_idle"
                      else False)          # stop has no cap relief below the loop bound
        if sanctioned:
            return None
        return "INVARIANT BREACH: a cap relief outside the sanctioned wedge: {}".format(tag)
    if verdict == "ALLOW":
        if kind == "wait":
            ok = (not ctx.get("actionable") and bool(ctx.get("waiting"))
                  and ctx.get("wake_named") is True)
        elif kind == "schedule_idle":
            rechecked = list(ctx.get("waiting", [])) + list(ctx.get("blocked", []))
            ok = (not ctx.get("actionable")
                  and not (rechecked and ctx.get("wake_named") is False))
        else:                              # stop
            ok = not ctx.get("actionable")
        if ok:
            return None
        return "INVARIANT BREACH: a plain ALLOW outside the fully-evaluated clean case: {}".format(tag)
    return "INVARIANT BREACH: an unexpected verdict {!r}: {}".format(verdict, tag)


def _base_ctx():
    return {"kind": "wait", "escape": False, "loop_signal": False, "counter": 0,
            "enum_status": "ok", "enum_detail": "", "actionable": [], "waiting": [],
            "blocked": [], "cannot_evaluate": [], "proposed": [], "wake_named": None,
            "schedule_denials": 0, "basis_unchanged": False}


def sweep_pure(rounds):
    """Layer 1: EXHAUSTIVE deterministic sweep over the ignorance x verdict x operation-kind space -
    every combination of kind (stop/schedule_idle/wait), enum_status, item cannot-evaluate present/absent,
    waiting present/absent, wake_named T/F/None, actionable present/absent, blocked present/absent, escape,
    and cap-relief basis - asserting the invariant in _classify BY CONSTRUCTION. This makes the B1/B5-class
    residuals (an ignorance or absent-target path reaching a cap-relieving ALLOW) impossible to ship, not
    caught per-instance."""
    rng = random.Random(_SEED)
    breaches = []
    kinds = ["stop", "schedule_idle", "wait"]
    enum_states = ["ok", "ENUMERATOR_ERROR", "unreadable"]
    wa = [[], [("A-1", "A-1", "seedable")], [("A-1", "A-1", "x"), ("A-2", "A-2", "y")]]
    wt = [[], [("W-1", "tracked-task", "T-1")], [("W-1", "tracked-task", "T-1"),
                                                 ("W-2", "tracked-task", "T-2")]]
    bl = [[], [("B-1", "external", "up")]]
    ce = [[], [("C-1", "cannot-evaluate", "unreadable")]]
    wn = [None, True, False]
    esc = [False, True]
    caps = [(0, False), (3, True), (3, False)]   # (schedule_denials, basis_unchanged)
    combos = list(itertools.product(kinds, enum_states, wa, wt, bl, ce, wn, esc, caps))
    rng.shuffle(combos)
    selected = combos if not rounds else combos[:rounds]
    for combo in selected:
        k, es, a, w, b, c, n, e, (sd, bu) = combo
        ctx = _base_ctx()
        ctx.update({"kind": k, "enum_status": es, "actionable": a, "waiting": w, "blocked": b,
                    "cannot_evaluate": c, "wake_named": n, "escape": e,
                    "schedule_denials": sd, "basis_unchanged": bu})
        verdict, _reason, _disp = aiqt_hooks.decide_yield(ctx)
        breach = _classify(ctx, verdict)
        if breach:
            breaches.append(breach + " | verdict={}".format(verdict))
    return breaches


_TOOL_POSITIVE = [{"ok": True}, {"success": True}, {"is_error": False}, {"status": "completed"},
                  {"status": "scheduled"}]
_TOOL_NEG_TEXT = ["Wake was not scheduled", "Request was unsuccessful", "the wake was not created",
                  "FAILED", "could not schedule", "no wake scheduled", "unable to register"]
_TOOL_POS_TEXT = ["Wake scheduled for 5pm", "done", "registered successfully", "scheduled"]


def sweep_tool_succeeded():
    """B5 by-construction: a POSITIVE-shaped field beside NEGATED/contradictory content must register NO
    wake (strict positive confirmation; a positive field never overrides negative content). Exhaustively
    cross every positive-shaped field with every negated content text and assert False; and cross a bare
    positive field OR a positive field with clean-positive content and assert True."""
    breaches = []
    for pos in _TOOL_POSITIVE:
        for neg in _TOOL_NEG_TEXT:
            resp = dict(pos, content=[{"type": "text", "text": neg}])
            if aiqt_hooks._orch_tool_succeeded(resp) is not False:
                breaches.append("B5 BREACH: positive field {} beside negative content {!r} registered a "
                                "wake".format(pos, neg))
        if aiqt_hooks._orch_tool_succeeded(dict(pos)) is not True:
            breaches.append("B5 BREACH: a bare positive-shaped field {} did NOT register".format(pos))
        for good in _TOOL_POS_TEXT:
            resp = dict(pos, content=[{"type": "text", "text": good}])
            if aiqt_hooks._orch_tool_succeeded(resp) is not True:
                breaches.append("B5 BREACH: positive field {} with clean-positive content {!r} did NOT "
                                "register".format(pos, good))
    # a negated content with NO positive field is likewise never a wake (ambiguity fails safe)
    for neg in _TOOL_NEG_TEXT:
        if aiqt_hooks._orch_tool_succeeded({"content": [{"type": "text", "text": neg}]}) is not False:
            breaches.append("B5 BREACH: negated content {!r} with no positive field registered".format(neg))
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
               enumerator={"argv": ["/bin/sh", str(enum)]}, wait_tools=["Bash"],
               # test-hermeticity: pin the machine-written state dir INSIDE the fixture tmp root, so no
               # guard-events/ledger/turn-state write touches ambient ${XDG_STATE_HOME:-~/.local/state}.
               state_dir=str(tmp / ".aiqt" / "state"))
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
    """Layer 2: read/parse boundary cases. Each non-single case must NOT yield a cap-relieving allow,
    and the single genuinely-licensed case MUST reach the sole ALLOW through the full read/parse path
    (so a false-DENY there would be caught too)."""
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
    with tempfile.TemporaryDirectory(prefix="aiqt-wbf-allow-") as tmp:
        # the SINGLE VALID ALLOW through the full path (its own fresh repo, so the anti-shrinkage
        # checkpoint has no prior window): a live ledger row for T-1, a WA-1 item blocked on it, and a
        # wait naming WA-1 -> the sole licensed ALLOW (a false-DENY here would be a breach too).
        root = _mkrepo(tmp, {}, [{"id": "WA-1", "state": "open", "granted": True,
                                  "blocker": {"kind": "tracked-task", "ref": "T-1"}}])
        sd = Path(aiqt_hooks._orch_state_dir_for_root(root))
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "dispatch-ledger.jsonl").write_text(json.dumps({
            "ts": aiqt_hooks._orch_now().isoformat(), "event": "launch", "task_id": "T-1",
            "tool": "Bash", "wake": True}) + "\n", encoding="utf-8")
        if _wait_verdict(root, "collect WA-1") != "allow":
            breaches.append("the single genuinely-licensed wait (a named live waiting row) did NOT "
                            "reach the sole ALLOW through the full read/parse path")
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
    breaches = sweep_pure(rounds) + sweep_tool_succeeded() + sweep_boundary()
    return breaches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=0, help="0 = the full deterministic combo set")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    # --self-test runs the FULL deterministic combo set (rounds 0), so the invariant is asserted
    # EXHAUSTIVELY over the ignorance x verdict x operation-kind space, not merely a 64-sample.
    rounds = 0 if a.self_test else a.rounds
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
