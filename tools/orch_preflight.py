#!/usr/bin/env python3
"""Pre-yield preflight (GD-112/GD-164, D2: non-blocking): print the disposition table and the bounded
surface-N liveness block the guards would act on, so the orchestrator sees per-item (id, class, proof)
and the wind-down summary BEFORE yielding, idling, draining, or waiting. The hooks re-enumerate
authoritatively at yield time; this CLI adds visibility, never enforcement.
  orch_preflight.py [stop|idle|drain|wait] [wait-text]   exit 0 with the table; 2 on no registry/bad op
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402


def main():
    argv = sys.argv[1:]
    op = argv[0] if argv else "stop"
    wake_text = argv[1] if len(argv) > 1 else None
    # a drained declaration and a wait are verified by their own decision kinds; an idle wake uses the
    # schedule path so it gets deny-on-cannot-evaluate, never the stop path's fail-open.
    kinds = {"stop": "stop", "drain": "stop", "idle": "schedule_idle", "wait": "wait"}
    if op not in kinds:
        print("usage: orch_preflight.py [stop|idle|drain|wait] [wait-text]", file=sys.stderr)
        return 2
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status != "ok":
        print("no usable orchestration registry ({}); nothing to preflight".format(status))
        return 2
    # A preview makes NO state change (secpv): the checkpoint union is computed for display but not
    # persisted, and no guard-events are emitted. The real hook path keeps record_checkpoint=True.
    ctx, _ts, _basis = aiqt_hooks._orch_build_ctx(
        reg, root, kinds[op], {}, wake_text=wake_text, record_checkpoint=False)
    verdict, reason, disposition = aiqt_hooks.decide_yield(ctx)
    n = aiqt_hooks._orch_validate("surface_n", reg.get("surface_n"))[1]["n"]
    print("operation: {} (decision kind: {})".format(op, kinds[op]))
    print("verdict if you {} now: {}".format(op, verdict))
    print("reason: {}".format(reason))
    print(aiqt_hooks._orch_surface(ctx, disposition, n))
    print("disposition ({} row(s), untruncated):".format(len(disposition)))
    for klass, iid, a, b in disposition:
        print("  {:<15} {:<16} {} {}".format(klass, iid, a, b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
