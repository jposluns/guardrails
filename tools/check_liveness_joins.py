#!/usr/bin/env python3
"""Liveness-join gate (GD-164; rules cntdef, setcmp): the CI join over the committed
backlog -> disposition register -> close-ref. Every terminal disposition in the committed backlog
source must resolve to a qualifying register row (a DS- row for no-fix-needed/not-buildable, a DF- row
for maintainer-deferred) or the `fixed` ground; an unjustified terminal disposition, or an unreadable
declared register, fails.

The committed backlog and registers are read from HEAD (the reviewed, integrated state), so a close
that is justified only in an uncommitted working tree cannot pass. The backlog is parsed with the
reference grammar (tools/aei_backlog_md.py); a register is read as a chained JSONL and reduced to its
covered keys (status accepted|landed) exactly as the engine reader keys them.

Exit: 0 every terminal disposition justified; 1 an unjustified terminal (a real finding); 2 usage or
cannot-evaluate (no registry, an unreadable/unparseable committed backlog or declared register).

  check_liveness_joins.py            join the committed state in this repo
  check_liveness_joins.py --self-test   the lj/* fixtures

DISCLOSED RESIDUAL (class c): this is the committed-state STRUCTURAL join. It confirms a qualifying,
approved register row exists for each terminal disposition, keyed as the engine keys it; it does NOT
re-check runtime freshness, the deferrals ownership sentinel, or check_ref pointer resolution (the
yield-time engine reader enforces those, and a register is itself actor-writable, the disclosed
_orch_register_authority residual). A close whose justification row is present-but-stale, or unowned,
passes this join and is caught at the yield boundary, not here.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import aei_backlog_md  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

APPROVED = ("accepted", "landed")


def _git_show(root, rel):
    """Read HEAD:<rel> (the committed state). Returns (text, None) or (None, detail)."""
    env = aiqt_hooks._isolate_git_env(dict(os.environ))
    try:
        r = subprocess.run(["git", "-C", root, "show", "HEAD:{}".format(rel)],
                           capture_output=True, text=True, timeout=15, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return (None, "git show failed ({})".format(exc))
    if r.returncode != 0:
        return (None, "not committed / unreadable at HEAD ({})".format(r.stderr.strip()[:120]))
    return (r.stdout, None)


def _covered_keys(text, prefix, has_ground):
    """Reduce a chained-register text to its approved covered keys, or (None, detail) when the chain is
    malformed (fail-closed)."""
    lines = text.splitlines()
    rows, prev = [], None
    import hashlib
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return (None, "line {} is not JSON".format(n))
        want = "0" * 64 if prev is None else hashlib.sha256(prev.encode("utf-8")).hexdigest()
        if not isinstance(row, dict) or row.get("prev") != want or row.get("seq") != n \
                or not str(row.get("id", "")).startswith(prefix):
            return (None, "line {} breaks the chain, sequence, or {} prefix".format(n, prefix))
        rows.append(row)
        prev = line
    latest = {}
    for row in rows:
        item, ref = row.get("item"), row.get("ref")
        ground = row.get("ground") if has_ground else None
        if not item or not ref or (has_ground and not ground):
            continue
        # Key exactly as the engine reader keys covered entries: a 3-tuple (item, ground, ref) for a
        # DS grant, a 2-tuple (item, ref) for a DF deferral (which carries no ground).
        latest[(item, ground, ref) if has_ground else (item, ref)] = row
    keys = set()
    for key, row in latest.items():
        if row.get("status") in APPROVED:
            keys.add(key)
    return (keys, None)


def join(backlog_text, ds_text, df_text):
    """Pure join. Returns (findings, cannot_evaluate, covered_terminals)."""
    findings, cannot, terminals = [], [], []
    items, errors = aei_backlog_md.parse(backlog_text, version=2)
    if errors:
        return ([], ["committed backlog fails the grammar: " + "; ".join(errors[:5])], [])
    ds_keys = df_keys = None
    if ds_text is not None:
        ds_keys, d = _covered_keys(ds_text, "DS-", True)
        if ds_keys is None:
            cannot.append("committed dispositions register is malformed: " + d)
    if df_text is not None:
        df_keys, d = _covered_keys(df_text, "DF-", False)
        if df_keys is None:
            cannot.append("committed deferrals register is malformed: " + d)
    if cannot:
        return (findings, cannot, terminals)
    for it in items:
        if it["state"] != "closed":
            continue
        close = it.get("close") or {}
        reason, ref = close.get("reason"), close.get("ref")
        terminals.append((it["id"], reason, ref))
        if reason == "fixed":
            continue
        if reason in ("no-fix-needed", "not-buildable"):
            if ds_keys is None:
                findings.append("{} is closed ({}) but no dispositions register is committed".format(
                    it["id"], reason))
            elif (it["id"], reason, ref) not in ds_keys:
                findings.append("{} is closed ({}) with no qualifying committed DS- row for "
                                "({}, {}, {})".format(it["id"], reason, it["id"], reason, ref))
        elif reason == "maintainer-deferred":
            if df_keys is None:
                findings.append("{} is closed (maintainer-deferred) but no deferrals register is "
                                "committed".format(it["id"]))
            elif (it["id"], ref) not in df_keys:
                findings.append("{} is closed (maintainer-deferred) with no qualifying committed DF- "
                                "row for ({}, {})".format(it["id"], it["id"], ref))
        else:
            findings.append("{} carries an unknown close reason {!r}".format(it["id"], reason))
    return (findings, cannot, terminals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status == "absent":
        print("no orchestration registry: liveness-joins gate is inert here")
        return 0
    if status != "ok":
        print("check_liveness_joins: registry unreadable ({}); cannot-evaluate".format(reg),
              file=sys.stderr)
        return 2
    route = reg.get("route") if isinstance(reg.get("route"), dict) else {}
    surfaces = route.get("surfaces") if isinstance(route.get("surfaces"), list) else []
    backlog_rel = surfaces[0] if surfaces else reg.get("backlog")
    if not backlog_rel:
        print("check_liveness_joins: no route.surfaces/backlog declared; nothing to join")
        return 0
    backlog_text, d = _git_show(root, backlog_rel)
    if backlog_text is None:
        print("CANNOT-EVALUATE: committed backlog {}: {}".format(backlog_rel, d), file=sys.stderr)
        return 2

    def _reg_rel(key):
        spec = reg.get(key)
        rel = spec.get("path") if isinstance(spec, dict) else spec
        return rel

    ds_text = df_text = None
    for key, sink in (("dispositions", "ds"), ("deferrals", "df")):
        rel = _reg_rel(key)
        if not rel:
            continue
        text, d = _git_show(root, rel)
        if text is None:
            print("CANNOT-EVALUATE: committed {} register {}: {}".format(key, rel, d), file=sys.stderr)
            return 2
        if sink == "ds":
            ds_text = text
        else:
            df_text = text
    findings, cannot, terminals = join(backlog_text, ds_text, df_text)
    for c in cannot:
        print("CANNOT-EVALUATE: " + c, file=sys.stderr)
    for f in findings:
        print("FINDING: " + f, file=sys.stderr)
    if cannot:
        return 2
    if findings:
        return 1
    print("liveness-joins: {} terminal disposition(s) all justified".format(len(terminals)))
    return 0


def self_test():
    fails = []
    # lj/justified-terminal-passes + lj/fixed-passes
    backlog = ("- A-1 [x] a :: close:no-fix-needed=R-1\n"
               "- A-2 [x] b :: close:fixed=commit-x\n"
               "- A-3 [x] c :: close:maintainer-deferred=PD-3\n"
               "- A-4 [ ] open\n")
    import hashlib

    def chain(rows):
        out, prev = [], "0" * 64
        for i, r in enumerate(rows, 1):
            line = json.dumps(dict(r, seq=i, prev=prev, id="{}{}".format(r["_p"], i)),
                              sort_keys=True)
            # _p is a helper key; strip it from the emitted row
            obj = json.loads(line)
            obj.pop("_p", None)
            line = json.dumps(obj, sort_keys=True)
            out.append(line)
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
        return "\n".join(out) + ("\n" if out else "")

    ds = chain([{"_p": "DS-", "item": "A-1", "ground": "no-fix-needed", "ref": "R-1",
                 "status": "accepted", "ts": "2026-09-01T00:00:00+00:00", "check_ref": "seed.txt"}])
    df = chain([{"_p": "DF-", "item": "A-3", "ref": "PD-3", "status": "accepted",
                 "ts": "2026-09-01T00:00:00+00:00", "check_ref": "seed.txt"}])
    findings, cannot, terminals = join(backlog, ds, df)
    if findings or cannot:
        fails.append("justified/fixed join should pass: findings={} cannot={}".format(findings, cannot))
    # lj/unjustified-terminal-fails: A-1 closed no-fix-needed but no covering DS row
    findings, cannot, _ = join(backlog, chain([]), df)
    if not findings:
        fails.append("an unjustified terminal must fail (lj/unjustified-terminal-fails)")
    # lj/unreadable-register-fails-closed: a malformed register chain
    findings, cannot, _ = join(backlog, "not json\n", df)
    if not cannot:
        fails.append("a malformed committed register must fail closed (lj/unreadable-register-fails-closed)")
    if fails:
        for f in fails:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
