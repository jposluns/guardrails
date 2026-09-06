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
    malformed (fail-closed). B8: a blank line is rejected exactly as the engine reader rejects it (the
    engine's _orch_chained_rows treats a blank line as a non-JSON chain break), so a trailing/interior
    blank line reads as unreadable here too, never a silently-skipped clean pass (reader parity)."""
    lines = text.splitlines()
    rows, prev = [], None
    import hashlib
    for n, line in enumerate(lines, 1):
        if not line.strip():
            return (None, "line {} is blank (a chain break; the engine reader rejects it too)".format(n))
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


def join(backlog_text, ds_text, df_text, fixed_reconcile=None):
    """Pure join over ONE committed surface. Returns (findings, cannot_evaluate, covered_terminals).
    fixed_reconcile(item_id, ref, declared_paths) -> (verdict, consultable): the B3 fix-attestation
    rail, binding the cited commit to the ITEM BY CONTENT (its changed set intersecting the item's
    declared close.paths), not merely resolving it and never on an assistant-writable id-in-message
    match. When it is None the join cannot verify a `fixed` close, so a `fixed` terminal is
    cannot-evaluate (held), never accepted unconditionally. verdict "terminal" is bound (justified);
    "unrelated"/non-resolving is a finding; "unreconcilable" (a `fixed` close with no declared paths, so
    the commit cannot be bound to the item by content) is cannot-evaluate (held). NOTE: the committed
    backlog markdown grammar cannot express close.paths, so a `fixed` close in a markdown backlog surface
    always reconciles to "unreconcilable" (held), not accepted; adopters that need a `fixed` terminal to
    pass this structural join declare the close.paths in a JSON AEI surface (see ORCHESTRATION.md)."""
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
            # B3: `fixed` is not accepted unconditionally; the cited commit must CONTAIN the fix for THIS
            # item by content (its changed set touches the item's declared close.paths). An id merely
            # named in the commit message is assistant-writable text and never binds (F-331).
            if fixed_reconcile is None:
                cannot.append("{} is closed (fixed) but the fix-attestation rail is unavailable to "
                              "verify {!r}".format(it["id"], ref))
                continue
            verdict, consultable = fixed_reconcile(it["id"], ref, close.get("paths"))
            if not consultable:
                cannot.append("{} is closed (fixed) but the fix-attestation rail could not be "
                              "consulted for {!r}".format(it["id"], ref))
            elif verdict == "unreconcilable":
                cannot.append("{} is closed (fixed) with a commit {!r} that cannot be bound to the item "
                              "by content (no declared close.paths; a `fixed` terminal binds only by a "
                              "changed-set/paths intersection); held (vfxcmt)".format(it["id"], ref))
            elif verdict != "terminal":
                findings.append("{} is closed (fixed) with a ref {!r} that does not contain the fix for "
                                "this item (vfxcmt: an unrelated or non-resolving commit)".format(
                                    it["id"], ref))
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


def resolve_surfaces(reg):
    """Return (surfaces_list, error_or_None). B8/setcmp: a DECLARED-but-malformed route (non-dict) or
    route.surfaces (non-list) is an ERROR (cannot-evaluate), never silently coerced to a "nothing to
    join" clean pass. gemini FINDING 2: when BOTH route.surfaces AND a legacy backlog key are declared,
    the backlog is UNIONED in (order-preserving dedup), so a close on the backlog surface cannot bypass
    the join."""
    raw_route = reg.get("route")
    if raw_route is not None and not isinstance(raw_route, dict):
        return (None, "route is declared but is not a table/dict ({!r})".format(
            type(raw_route).__name__))
    route = raw_route if isinstance(raw_route, dict) else {}
    raw_surfaces = route.get("surfaces")
    if raw_surfaces is not None and not isinstance(raw_surfaces, list):
        return (None, "route.surfaces is declared but is not a list ({!r})".format(
            type(raw_surfaces).__name__))
    surfaces = list(raw_surfaces) if isinstance(raw_surfaces, list) else []
    if reg.get("backlog") is not None:
        surfaces = surfaces + [reg.get("backlog")]
    seen, ordered = set(), []
    for s in surfaces:
        key = s if isinstance(s, str) else json.dumps(s, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(s)
    return (ordered, None)


def resolve_register_rel(reg, key):
    """Return (rel_or_None, declared_bool, error_or_None) for a dispositions/deferrals register spec.
    B8/chkfcl: a DECLARED-but-malformed spec (a dict without a usable path, or a blank string) is an
    ERROR (cannot-evaluate), never a silent skip that would let an unjustified terminal escape for want
    of the register; an UNDECLARED register is (None, False, None)."""
    spec = reg.get(key)
    if spec is None:
        return (None, False, None)
    rel = spec.get("path") if isinstance(spec, dict) else spec
    if not isinstance(rel, str) or not rel.strip():
        return (None, True, "{} register is declared but has no usable path ({!r})".format(key, spec))
    return (rel, True, None)


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
    surfaces, serr = resolve_surfaces(reg)
    if serr is not None:
        print("CANNOT-EVALUATE: " + serr, file=sys.stderr)
        return 2
    if not surfaces:
        print("check_liveness_joins: no route.surfaces/backlog declared; nothing to join")
        return 0

    ds_text = df_text = None
    for key, sink in (("dispositions", "ds"), ("deferrals", "df")):
        rel, declared, rerr = resolve_register_rel(reg, key)
        if rerr is not None:
            print("CANNOT-EVALUATE: " + rerr, file=sys.stderr)
            return 2
        if not declared:
            continue                     # genuinely not declared: nothing to join for this register
        text, d = _git_show(root, rel)
        if text is None:
            print("CANNOT-EVALUATE: committed {} register {}: {}".format(key, rel, d), file=sys.stderr)
            return 2
        if sink == "ds":
            ds_text = text
        else:
            df_text = text

    def _fixed_reconcile(item_id, ref, declared_paths):
        return aiqt_hooks._orch_commit_fixes_item(root, ref, item_id, declared_paths)

    all_findings, all_cannot, all_terminals = [], [], []
    # B8: EVERY declared surface is joined (setcmp), not only surfaces[0]; an unjustified terminal in
    # any surface is a finding.
    for rel in surfaces:
        if not rel:
            all_cannot.append("a route.surfaces entry is blank/unusable")
            continue
        backlog_text, d = _git_show(root, rel)
        if backlog_text is None:
            all_cannot.append("committed backlog {}: {}".format(rel, d))
            continue
        findings, cannot, terminals = join(backlog_text, ds_text, df_text,
                                           fixed_reconcile=_fixed_reconcile)
        all_findings.extend("[{}] {}".format(rel, f) for f in findings)
        all_cannot.extend("[{}] {}".format(rel, c) for c in cannot)
        all_terminals.extend((rel, i, r, ref) for i, r, ref in terminals)
    for c in all_cannot:
        print("CANNOT-EVALUATE: " + c, file=sys.stderr)
    for f in all_findings:
        print("FINDING: " + f, file=sys.stderr)
    if all_cannot:
        return 2
    if all_findings:
        return 1
    # B8: emit the COMPLETE per-terminal machine evaluation on success, not a bare count.
    print(json.dumps({"gate": "liveness-joins", "terminal_count": len(all_terminals),
                      "terminals": [{"surface": s, "id": i, "reason": r, "ref": ref}
                                    for s, i, r, ref in all_terminals]}, sort_keys=True))
    print("liveness-joins: {} terminal disposition(s) across {} surface(s) all justified".format(
        len(all_terminals), len(surfaces)))
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
    # the fix-attestation reconcile stub: commit-x is BOUND to its item (terminal); "fabricated" is a
    # non-resolving/unrelated ref; "unrelated-real" is a real-but-unrelated commit that cannot be bound
    # to the item (unreconcilable -> held). git WAS consultable in each case.
    def fr(item_id, ref, paths):
        if ref == "commit-x":
            return ("terminal", True)
        if ref == "unrelated-real":
            return ("unreconcilable", True)
        return ("unrelated", True)
    findings, cannot, terminals = join(backlog, ds, df, fixed_reconcile=fr)
    if findings or cannot:
        fails.append("justified/fixed join should pass: findings={} cannot={}".format(findings, cannot))
    # lj/unjustified-terminal-fails: A-1 closed no-fix-needed but no covering DS row
    findings, cannot, _ = join(backlog, chain([]), df, fixed_reconcile=fr)
    if not findings:
        fails.append("an unjustified terminal must fail (lj/unjustified-terminal-fails)")
    # lj/unreadable-register-fails-closed: a malformed register chain
    findings, cannot, _ = join(backlog, "not json\n", df, fixed_reconcile=fr)
    if not cannot:
        fails.append("a malformed committed register must fail closed (lj/unreadable-register-fails-closed)")
    # B3 lj/fixed-fabricated-ref-fails: a fixed close whose ref does not bind to the item is a finding
    fab = "- Z-1 [x] z :: close:fixed=fabricated\n"
    findings, cannot, _ = join(fab, chain([]), chain([]), fixed_reconcile=fr)
    if not findings:
        fails.append("a fixed close with a non-resolving/unrelated ref must fail (lj/fixed-fabricated-ref-fails)")
    # B3 lj/fixed-unrelated-real-commit-holds: a REAL but UNRELATED commit that cannot be reconciled to
    # the item is HELD (cannot-evaluate), never accepted merely because it resolves (the codex residual)
    unrel = "- Z-2 [x] z :: close:fixed=unrelated-real\n"
    findings, cannot, _ = join(unrel, chain([]), chain([]), fixed_reconcile=fr)
    if not cannot:
        fails.append("a real-but-unrelated fixed commit must be cannot-evaluate (lj/fixed-unrelated-real-commit-holds)")
    # B3 lj/fixed-rail-unavailable-holds: no rail -> cannot-evaluate (held), never accepted
    findings, cannot, _ = join(fab, chain([]), chain([]), fixed_reconcile=None)
    if not cannot:
        fails.append("a fixed close with no rail must be cannot-evaluate (lj/fixed-rail-unavailable-holds)")
    # B8 lj/blank-line-register-fails-closed: a register with an interior blank line reads unreadable,
    # matching the engine reader (never a silently-skipped clean pass)
    ds_blank = ds.rstrip("\n") + "\n\n"
    findings, cannot, _ = join(backlog, ds_blank, df, fixed_reconcile=fr)
    if not cannot:
        fails.append("a register with a blank line must fail closed (lj/blank-line-register-fails-closed)")
    # B8 lj/malformed-route-fails-closed: a declared-but-non-dict route is cannot-evaluate, never {}
    if resolve_surfaces({"route": "malformed-declared-route"})[1] is None:
        fails.append("a declared non-dict route must fail closed (lj/malformed-route-fails-closed)")
    # B8 lj/malformed-surfaces-fails-closed: a declared-but-non-list route.surfaces is cannot-evaluate
    if resolve_surfaces({"route": {"surfaces": "x.md"}})[1] is None:
        fails.append("a declared non-list route.surfaces must fail closed (lj/malformed-surfaces-fails-closed)")
    # gemini FINDING 2 lj/backlog-and-surfaces-both-covered: both declared -> BOTH joined (union)
    both, berr = resolve_surfaces({"route": {"surfaces": ["other.md"]}, "backlog": "SCOPE.md"})
    if berr is not None or "other.md" not in both or "SCOPE.md" not in both:
        fails.append("both route.surfaces AND backlog must be covered (lj/backlog-and-surfaces-both-covered), got {}".format(both))
    # a surface declared in both is joined ONCE (order-preserving dedup)
    dedup, _e = resolve_surfaces({"route": {"surfaces": ["SCOPE.md"]}, "backlog": "SCOPE.md"})
    if dedup != ["SCOPE.md"]:
        fails.append("a surface declared in both must dedup (lj/surface-dedup), got {}".format(dedup))
    # B8 lj/malformed-register-path-fails-closed: a declared register spec with no usable path is
    # cannot-evaluate, never a silent skip
    if resolve_register_rel({"dispositions": {"weak_proof_accepted": True}}, "dispositions")[2] is None:
        fails.append("a declared register with no usable path must fail closed (lj/malformed-register-path-fails-closed)")
    if resolve_register_rel({"deferrals": "   "}, "deferrals")[2] is None:
        fails.append("a declared register with a blank path must fail closed (lj/blank-register-path-fails-closed)")
    # an UNDECLARED register is a clean skip (not an error)
    if resolve_register_rel({}, "dispositions") != (None, False, None):
        fails.append("an undeclared register must be a clean skip, not an error")
    if fails:
        for f in fails:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
