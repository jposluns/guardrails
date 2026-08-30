#!/usr/bin/env python3
"""Pre-yield preflight (GD-112, D2: non-blocking in v1): print the disposition table the stop guard
would act on, so the orchestrator sees per-item (id, class, proof) BEFORE yielding. The hooks
re-enumerate authoritatively at yield time; this CLI adds visibility, never enforcement, and
escalating it to a required permit is a recorded phase-2 option.
  orch_preflight.py [stop|idle|drain]   exit 0 with the table; exit 2 on no registry or a bad operation
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
    # a drained declaration is verified by the stop path; an idle wake must use the schedule path so it
    # gets deny-on-cannot-evaluate, never the stop path's fail-open (CX-preflight-mode finding).
    kinds = {"stop": "stop", "drain": "stop", "idle": "schedule_idle"}
    if op not in kinds:
        print("usage: orch_preflight.py [stop|idle|drain]", file=sys.stderr)
        return 2
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status != "ok":
        print("no usable orchestration registry ({}); nothing to preflight".format(status))
        return 2
    ctx, _ts, _basis = aiqt_hooks._orch_build_ctx(reg, root, kinds[op], {})
    verdict, reason, disposition = aiqt_hooks.decide_yield(ctx)
    print("operation: {} (decision kind: {})".format(op, kinds[op]))
    print("verdict if you {} now: {}".format(op, verdict))
    print("reason: {}".format(reason))
    print("disposition ({} row(s)):".format(len(disposition)))
    for klass, iid, a, b in disposition:
        print("  {:<11} {:<16} {} {}".format(klass, iid, a, b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
