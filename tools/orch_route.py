#!/usr/bin/env python3
"""Supported command to move a backlog item to a terminal, deferred, or held disposition (GD-164;
rules cntdef/setcmp). A hand-edit of a route surface is refused by the orch_route_guard hook, which
directs here; this command is the one path that re-derives the item, resolves its close ground against
the closed vocabulary, requires the qualifying register entry, and writes the disposition register row
plus the *-validated.json snapshot in exactly the schema the yield-time engine readers consume, so a
close can never enter the surface unjustified.

  orch_route.py --item ID --ground GROUND --ref REF [--check-ref PTR] [--backlog PATH] [--title T]
  orch_route.py --self-test

GROUND is one of the closed vocabulary (fixed, no-fix-needed, not-buildable, maintainer-deferred):
  fixed             the item was fixed; the attestation/vfcmt rail governs the landing, so no
                    disposition register row is authored here, only the surface is closed.
  no-fix-needed     an assistant-authorable ground: a DS- row is appended to the dispositions register
  not-buildable       (item, ground, ref, status accepted, ts, check_ref) and the dispositions
                    snapshot is refreshed; REF names the justification, CHECK-REF a resolvable pointer.
  maintainer-deferred  a maintainer-granted ground proven by provenance: this command NEVER authors the
                    DF- grant (a grant the assistant could write is indistinguishable from its own
                    proposal). It REQUIRES an already-present, validated, ownership-passing deferral row
                    covering (item, ref); it only refreshes the deferrals snapshot and closes the
                    surface. The maintainer authors the DF- row on the operator-owned register.

VALIDATION IS A GATE ON APPLY regardless of invoker (valgat): a fresh enumeration must re-derive the
item; the ground must be in the vocabulary; the qualifying register entry must be present, fresh, and
readable. ANY missing, stale, malformed, contradictory, or unreadable input NAMING the record is a
refusal (cannot-evaluate, exit 2), never a silent close. Every attempt, allowed or refused, appends one
attempted-transition row to guard-events.jsonl.

DISCLOSED RESIDUAL: the dispositions register and its .anchor are actor-writable (same-uid); this
command keeps the append-only chain and anchor consistent so the engine reader can bind a snapshot to
the tip, but a same-uid actor with filesystem write can still append a DS- row directly (the disclosed
_orch_register_authority residual). The maintainer-deferred ground is the one made operator-owned via
the deferrals ownership test; the categorical closure is an OS-layer operator-owned register.
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

ZERO = "0" * 64
DS_GROUNDS = ("no-fix-needed", "not-buildable")   # assistant-authorable, DS- register
DEFER_GROUND = "maintainer-deferred"              # operator-owned, DF- register (required, not authored)


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _event(root, sd, decision, detail):
    """Append one attempted-transition row to guard-events (best effort; a failed record is surfaced)."""
    ok = aiqt_hooks._orch_guard_event(root, "orch-route", decision, detail, state_dir=sd)
    if not ok:
        print("[{}] orch_route: WARNING the guard-events record could not be persisted".format(
            _now_iso()), file=sys.stderr)


def _append_chained_row(reg_path, prefix, fields):
    """Append one chained row to a DS-/DF- register and rewrite its .anchor to the new tip, mirroring
    tools/orch_register.py's append and the engine's _orch_register_authority anchor authority. Returns
    (seq, tip_digest)."""
    try:
        lines = Path(reg_path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    prev = ZERO if not lines else hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
    seq = len(lines) + 1
    row = dict(fields, seq=seq, prev=prev, id="{}{}".format(prefix, seq))
    line = json.dumps(row, sort_keys=True)
    Path(reg_path).parent.mkdir(parents=True, exist_ok=True)
    with open(reg_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    tip_digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
    Path(str(reg_path) + ".anchor").write_text(
        json.dumps({"seq": seq, "digest": tip_digest}), encoding="utf-8")
    return seq, tip_digest


def _reg_spec_path(root, spec):
    """Resolve a dispositions/deferrals registry spec (string path or {"path": ...}) to an abs path."""
    if isinstance(spec, dict):
        return aiqt_hooks._orch_path(root, spec.get("path"))
    return aiqt_hooks._orch_path(root, spec)


def write_ground_snapshot(reg, root, reg_key, snap_name, prefix, has_ground):
    """Re-anchor the ground register and write <state_dir>/<snap_name> bound to its tip, in EXACTLY the
    shape _orch_ground_refs consumes (status ok, fresh ts, register realpath, seq/digest from
    _orch_register_authority). The covered set is recomputed by the reader, so the stored 'covered'
    list is informational only. On any authority/chain failure a hold snapshot (status unreadable) is
    written and the prior ok snapshot is invalidated first, so a failed write never leaves a stale
    clean pass. Returns (ok, detail). Shared with tools/orch_doctor.py (imported there)."""
    spec = reg.get(reg_key)
    reg_path = _reg_spec_path(root, spec)
    sd = aiqt_hooks._state_dir_from_registry(root, reg)
    snap_path = os.path.join(sd, snap_name)
    canonical = os.path.realpath(reg_path)
    hold = {"version": 2, "ts": _now_iso(), "status": "unreadable",
            "register": canonical, "seq": None, "digest": None, "covered": []}
    rows, detail = aiqt_hooks._orch_chained_rows(reg_path, prefix)
    astatus, aseq, adigest = aiqt_hooks._orch_register_authority(root, reg_path)
    if rows is None or astatus != "ok":
        try:
            os.remove(snap_path)
        except OSError:
            pass
        aiqt_hooks._orch_write_json_atomic(snap_path, hold)
        return (False, "chain {} / authority {}".format(detail, aseq if astatus != "ok" else "ok"))
    covered = aiqt_hooks._orch_ground_derive(reg, root, sd, rows, has_ground)
    snap = {"version": 2, "ts": _now_iso(), "status": "ok", "register": canonical,
            "seq": aseq, "digest": adigest,
            "covered": [list(k) for k in sorted(covered)]}
    if not aiqt_hooks._orch_write_json_atomic(snap_path, snap):
        try:
            os.remove(snap_path)
        except OSError:
            pass
        return (False, "snapshot write failed")
    return (True, "covered {} row(s)".format(len(covered)))


def _find_item(reg, root, item_id):
    """Re-derive the item from a FRESH enumeration. Returns (item_dict, None) or (None, detail)."""
    status, payload = aiqt_hooks._orch_enumerate(reg, root)
    if status != "ok":
        return (None, "enumeration is not evaluable ({}: {})".format(status, payload))
    for it in payload["items"]:
        if it.get("id") == item_id:
            return (it, None)
    return (None, "item {!r} is not in the current enumeration".format(item_id))


def _close_surface(backlog_path, item_id, reason, ref):
    """Mark the reference markdown-backlog line closed and append the v2 close annotation, matching
    tools/aei_backlog_md.py's grammar (:: close:<reason>=<ref>). Fail-closed: the line must exist and
    must NOT already carry a DIVERGENT close (a different reason/ref). B3: an existing divergent close
    is REFUSED (never silently removed and replaced), honouring the contract in this docstring; only an
    exact re-statement of the same close is idempotently allowed. Returns (ok, detail)."""
    try:
        text = Path(backlog_path).read_text(encoding="utf-8")
    except OSError as exc:
        return (False, "backlog unreadable ({})".format(exc))
    import re
    pat = re.compile(r"^(-\s+" + re.escape(item_id) + r"\s+)\[(x|X| |\.|o|O|BLOCKED)\](.*)$")
    close_pat = re.compile(r"\s*::\s*close:([^=:]+)=(.+?)\s*$")
    out, hits = [], 0
    for line in text.splitlines():
        m = pat.match(line.strip())
        if not m:
            out.append(line)
            continue
        hits += 1
        rest = m.group(3)
        existing = close_pat.search(rest)
        if existing and (existing.group(1).strip(), existing.group(2).strip()) != (reason, ref):
            return (False, "{} already carries a divergent close (close:{}={}); refusing to overwrite "
                    "it with close:{}={} (hand-reconcile the surface first)".format(
                        item_id, existing.group(1).strip(), existing.group(2).strip(), reason, ref))
        rest = close_pat.sub("", rest).rstrip()
        out.append("- {} [x]{} :: close:{}={}".format(item_id, rest, reason, ref))
    if hits != 1:
        return (False, "expected exactly one line for {} in the surface, found {}".format(
            item_id, hits))
    Path(backlog_path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return (True, "closed {} ({}={})".format(item_id, reason, ref))


def route(root, reg, item_id, ground, ref, check_ref, backlog, paths=None):
    sd = aiqt_hooks._state_dir_from_registry(root, reg)
    if ground not in aiqt_hooks._ORCH_CLOSE_REASONS:
        _event(root, sd, "refused", "unknown ground {!r} (not in the closed vocabulary)".format(ground))
        print("orch_route: unknown ground {!r}; the closed vocabulary is {}".format(
            ground, "/".join(sorted(aiqt_hooks._ORCH_CLOSE_REASONS))), file=sys.stderr)
        return 2
    if not isinstance(ref, str) or not ref.strip():
        # B3: a whitespace-only or blank ref names nothing and can never substantiate a close.
        _event(root, sd, "refused", "blank/whitespace ref for ground {}".format(ground))
        print("orch_route: --ref must be a non-blank reference (a blank/whitespace ref substantiates "
              "no close)", file=sys.stderr)
        return 2
    it, detail = _find_item(reg, root, item_id)
    if it is None:
        _event(root, sd, "refused", "re-derivation failed: {}".format(detail))
        print("orch_route: {}".format(detail), file=sys.stderr)
        return 2
    staleness = aiqt_hooks._orch_validate("staleness", reg.get("staleness"))[1]

    if ground in DS_GROUNDS:
        if reg.get("dispositions") is None:
            _event(root, sd, "refused", "no dispositions register declared for ground {}".format(ground))
            print("orch_route: ground {} needs a declared dispositions register".format(ground),
                  file=sys.stderr)
            return 2
        if not check_ref:
            _event(root, sd, "refused", "ground {} needs a --check-ref pointer".format(ground))
            print("orch_route: ground {} requires --check-ref (a resolvable evidence pointer)".format(
                ground), file=sys.stderr)
            return 2
        reg_path = _reg_spec_path(root, reg.get("dispositions"))
        _append_chained_row(reg_path, "DS-", {"item": item_id, "ground": ground, "ref": ref,
                                              "status": "accepted", "ts": _now_iso(),
                                              "check_ref": check_ref})
        ok, sdetail = write_ground_snapshot(reg, root, "dispositions",
                                            "dispositions-validated.json", "DS-", True)
        if not ok:
            _event(root, sd, "refused", "dispositions snapshot could not be validated: {}".format(sdetail))
            print("orch_route: the dispositions snapshot did not validate ({})".format(sdetail),
                  file=sys.stderr)
            return 2
        refs = aiqt_hooks._orch_disposition_refs(reg, root, staleness, state_dir=sd)
        if not isinstance(refs, frozenset) or (item_id, ground, ref) not in refs:
            _event(root, sd, "refused",
                   "the appended row does not re-derive as covering ({}, {}, {})".format(
                       item_id, ground, ref))
            print("orch_route: the disposition did not re-derive as covered (held)", file=sys.stderr)
            return 2
    elif ground == DEFER_GROUND:
        if reg.get("deferrals") is None:
            _event(root, sd, "refused", "no deferrals register declared")
            print("orch_route: maintainer-deferred needs a declared deferrals register", file=sys.stderr)
            return 2
        # refresh the snapshot over the operator-owned register, then REQUIRE the pre-existing grant.
        write_ground_snapshot(reg, root, "deferrals", "deferrals-validated.json", "DF-", False)
        refs = aiqt_hooks._orch_deferral_refs(reg, root, staleness, state_dir=sd)
        if refs is None:
            _event(root, sd, "refused", "deferrals register undeclared at read")
            print("orch_route: no deferrals register to prove the grant", file=sys.stderr)
            return 2
        if refs == "unreadable":
            _event(root, sd, "refused",
                   "deferrals register is unreadable or fails the ownership test; held")
            print("orch_route: the deferrals grant is unreadable/unowned (held, never granted). A "
                  "maintainer-deferred close is proven by an operator-owned DF- row this command does "
                  "not author.", file=sys.stderr)
            return 2
        if (item_id, ref) not in refs:
            _event(root, sd, "refused",
                   "no qualifying maintainer-deferral row for ({}, {})".format(item_id, ref))
            print("orch_route: no validated, ownership-passing deferral row covers ({}, {}); the "
                  "maintainer authors it on the operator-owned register first".format(item_id, ref),
                  file=sys.stderr)
            return 2
    else:
        # ground == "fixed": B3 - NOT a self-authored bypass, and NOT terminal merely because the ref
        # resolves to SOME commit. The cited commit must CONTAIN the fix for THIS item: its changed set
        # must touch the item's declared paths (--paths or the item's close.paths), or the commit message
        # must name the item id (its identity). A fabricated/unrelated commit is refused; a real commit
        # that cannot be reconciled to the item (no declared paths, and the message does not name it) is
        # HELD (cannot-evaluate), never accepted.
        declared_paths = paths if paths else (it.get("close") or {}).get("paths")
        verdict, consultable = aiqt_hooks._orch_commit_fixes_item(root, ref, item_id, declared_paths)
        if not consultable:
            _event(root, sd, "refused", "fix-attestation rail unavailable for fixed close of {}"
                   .format(item_id))
            print("orch_route: could not consult the fix-attestation rail (git) to verify the fixed "
                  "close ref; held (cannot-evaluate)", file=sys.stderr)
            return 2
        if verdict == "unreconcilable":
            _event(root, sd, "refused",
                   "fixed close commit {!r} cannot be reconciled to item {}".format(ref, item_id))
            print("orch_route: a 'fixed' close on commit {!r} cannot be bound to {} (declare --paths the "
                  "fix touched, or name {} in the commit message); held (vfxcmt)".format(
                      ref, item_id, item_id), file=sys.stderr)
            return 2
        if verdict != "terminal":
            _event(root, sd, "refused",
                   "fixed close ref {!r} does not contain the fix for {}".format(ref, item_id))
            print("orch_route: a 'fixed' close requires --ref to name a commit that contains the fix for "
                  "{} (vfxcmt); {!r} is unrelated or resolves to no commit, so the close is refused"
                  .format(item_id, ref), file=sys.stderr)
            return 2

    if backlog:
        ok, cdetail = _close_surface(backlog, item_id, ground, ref)
        if not ok:
            _event(root, sd, "refused", "surface close failed: {}".format(cdetail))
            print("orch_route: could not close the surface ({})".format(cdetail), file=sys.stderr)
            return 2
    _event(root, sd, "routed", "{} -> closed ({}={}); surface {}".format(
        item_id, ground, ref, "updated" if backlog else "not touched"))
    print("[{}] orch_route: {} moved to closed ({}={})".format(_now_iso(), item_id, ground, ref))
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test()
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--ground", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--check-ref", dest="check_ref", default="")
    ap.add_argument("--backlog", default=None)
    ap.add_argument("--paths", nargs="+", default=None,
                    help="repo-relative path(s) the fixed commit touched, binding it to the item (B3)")
    ap.add_argument("--title", default=None)   # accepted for symmetry with the surface grammar; unused
    a = ap.parse_args(argv)
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status != "ok":
        print("orch_route: no usable orchestration registry ({}); cannot route".format(status),
              file=sys.stderr)
        return 2
    backlog = a.backlog
    if backlog is None:
        route_cfg = reg.get("route") if isinstance(reg.get("route"), dict) else {}
        surfaces = route_cfg.get("surfaces") if isinstance(route_cfg.get("surfaces"), list) else []
        if len(surfaces) == 1:
            backlog = aiqt_hooks._orch_path(root, surfaces[0])
    return route(root, reg, a.item, a.ground, a.ref, a.check_ref, backlog, paths=a.paths)


def self_test():
    import subprocess
    import tempfile
    fails = []
    with tempfile.TemporaryDirectory(prefix="aiqt-orch-route-") as tmp:
        tmp = Path(tmp)
        # a real git work tree: route() itself does not need repo_root (root is passed), but a git repo
        # gives the register-authority and state-dir helpers the same deterministic ground the engine's
        # own fixtures use. route() is driven IN-PROCESS with root=tmp, so the run is independent of
        # repo_root's __file__ anchoring (which would otherwise resolve to the host repo, not tmp).
        subprocess.run(["git", "init", "-q", "-b", "main", str(tmp)], capture_output=True, check=False)
        (tmp / ".aiqt").mkdir()
        (tmp / "seed.txt").write_text("evidence\n", encoding="utf-8")
        backlog = tmp / "backlog.md"
        backlog.write_text("- C-1 [ ] a closable item\n- C-2 [ ] another\n", encoding="utf-8")
        enum = tmp / "enum.sh"
        enum.write_text("#!/bin/sh\ncat '{}'\n".format(tmp / "aei.json"), encoding="utf-8")
        os.chmod(enum, 0o755)

        def write_aei(items):
            (tmp / "aei.json").write_text(json.dumps({
                "version": 2, "generated_at_utc": _now_iso(),
                "source": {"locator": str(backlog)}, "items": items}), encoding="utf-8")

        write_aei([{"id": "C-1", "state": "open", "granted": True},
                   {"id": "C-2", "state": "open", "granted": True}])
        reg = {"version": 1, "enumerator": {"argv": ["/bin/sh", str(enum)]},
               "dispositions": str(tmp / "dispositions.jsonl"),
               # test-hermeticity: pin the machine-written state dir INSIDE the fixture tmp root, so the
               # guard-events/snapshot writes never touch ambient ${XDG_STATE_HOME:-~/.local/state}.
               "state_dir": str(tmp / "state"),
               "route": {"surfaces": ["backlog.md"]}}
        (tmp / ".aiqt" / "orchestration.local.json").write_text(json.dumps(reg), encoding="utf-8")
        root = str(tmp)

        # (a) unknown ground refused
        if route(root, reg, "C-1", "wont-fix", "R", "", str(backlog)) != 2:
            fails.append("unknown ground should refuse (exit 2)")
        # (b) absent item refused
        if route(root, reg, "NOPE", "no-fix-needed", "R", "seed.txt", str(backlog)) != 2:
            fails.append("absent item should refuse (exit 2)")
        # (c) no-fix-needed happy path: DS row + snapshot written, surface closed, engine re-derives it
        rc = route(root, reg, "C-1", "no-fix-needed", "R-1", "seed.txt", str(backlog))
        if rc != 0:
            fails.append("no-fix-needed happy path should route (exit 0), got {}".format(rc))
        if "[x]" not in backlog.read_text(encoding="utf-8"):
            fails.append("no-fix-needed did not close the surface line")
        st = aiqt_hooks._orch_validate("staleness", reg.get("staleness"))[1]
        refs = aiqt_hooks._orch_disposition_refs(
            reg, root, st, state_dir=aiqt_hooks._state_dir_from_registry(root, reg))
        if not isinstance(refs, frozenset) or ("C-1", "no-fix-needed", "R-1") not in refs:
            fails.append("engine reader did not re-derive the written DS row as covered")
        # (d) maintainer-deferred without a qualifying DF row refused
        reg["deferrals"] = {"path": str(tmp / "deferrals.jsonl"), "weak_proof_accepted": True}
        (tmp / ".aiqt" / "orchestration.local.json").write_text(json.dumps(reg), encoding="utf-8")
        (tmp / "deferrals.jsonl").write_text("", encoding="utf-8")
        Path(str(tmp / "deferrals.jsonl") + ".anchor").write_text(
            json.dumps({"seq": 0, "digest": ZERO}), encoding="utf-8")
        if route(root, reg, "C-2", "maintainer-deferred", "PD-2", "", str(backlog)) != 2:
            fails.append("maintainer-deferred without a grant should refuse (exit 2)")

        # ---- B3: `fixed` is verified against the fix-attestation rail, not a self-authored bypass ----
        # (e) fixed with a FABRICATED ref (resolves to no commit) is refused (exit 2), surface untouched
        before = backlog.read_text(encoding="utf-8")
        if route(root, reg, "C-2", "fixed", "fabricated-not-a-commit", "", str(backlog)) != 2:
            fails.append("fixed with a fabricated ref should refuse (exit 2)")
        if backlog.read_text(encoding="utf-8") != before:
            fails.append("a refused fixed close must NOT touch the surface")
        # (f) a WHITESPACE-only ref is refused for every ground (exit 2)
        if route(root, reg, "C-2", "fixed", "   ", "", str(backlog)) != 2:
            fails.append("a whitespace-only ref should refuse (exit 2)")
        # (g) fixed HAPPY path: B3 - a real commit BOUND to the item by declared --paths that its changed
        # set touches routes (exit 0) and closes it. (The seed commit changed seed.txt.)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(tmp), "config", k, v], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(tmp), "add", "-A"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "seed"],
                       capture_output=True, check=False)
        head = subprocess.run(["git", "-C", str(tmp), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False)
        real_sha = head.stdout.strip()
        rc = route(root, reg, "C-2", "fixed", real_sha, "", str(backlog), paths=["seed.txt"])
        if rc != 0:
            fails.append("fixed bound by declared paths should route (exit 0), got {}".format(rc))
        if "close:fixed={}".format(real_sha) not in backlog.read_text(encoding="utf-8"):
            fails.append("fixed happy path did not write the close annotation")
        # (g2) B3 reconciliation verdicts (direct against _orch_commit_fixes_item): a REAL commit whose
        # message does NOT name the item and with NO declared paths is UNRECONCILABLE (held); declared
        # paths the commit does not touch are UNRELATED; matching declared paths, or the item id in the
        # message, are TERMINAL.
        vr, okc2 = aiqt_hooks._orch_commit_fixes_item(str(tmp), real_sha, "C-9", None)
        if not okc2 or vr != "unreconcilable":
            fails.append("a real commit not naming the item, no paths, must be unreconcilable, got {}".format(vr))
        vr, _ = aiqt_hooks._orch_commit_fixes_item(str(tmp), real_sha, "C-9", ["nowhere/x.py"])
        if vr != "unrelated":
            fails.append("declared paths the commit does not touch must be unrelated, got {}".format(vr))
        vr, _ = aiqt_hooks._orch_commit_fixes_item(str(tmp), real_sha, "C-9", ["seed.txt"])
        if vr != "terminal":
            fails.append("declared paths the commit touches must be terminal, got {}".format(vr))
        # (g3) a commit whose MESSAGE names the item id binds it by identity (no declared paths) -> terminal
        (tmp / "extra.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp), "add", "extra.txt"], capture_output=True, check=False)
        subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "fix C-9: land it"],
                       capture_output=True, check=False)
        named_sha = subprocess.run(["git", "-C", str(tmp), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, check=False).stdout.strip()
        vr, _ = aiqt_hooks._orch_commit_fixes_item(str(tmp), named_sha, "C-9", None)
        if vr != "terminal":
            fails.append("a commit whose message names the item id must be terminal, got {}".format(vr))
        # (g4) route-level: a `fixed` close on a real commit that cannot be reconciled to the item (no
        # paths, message does not name it) is REFUSED (exit 2), never accepted merely because it resolves.
        write_aei([{"id": "C-1", "state": "open", "granted": True},
                   {"id": "C-3", "state": "open", "granted": True}])
        before_g4 = backlog.read_text(encoding="utf-8")
        if route(root, reg, "C-3", "fixed", real_sha, "", str(backlog)) != 2:
            fails.append("a fixed close on an unrelated real commit must refuse (exit 2)")
        if backlog.read_text(encoding="utf-8") != before_g4:
            fails.append("a refused unreconcilable fixed close must NOT touch the surface")
        write_aei([{"id": "C-1", "state": "open", "granted": True},
                   {"id": "C-2", "state": "open", "granted": True}])
        # (h) _close_surface REFUSES a DIVERGENT existing close (never silently overwrites it), while an
        # exact re-statement of the same close is idempotently allowed.
        okc, _d = _close_surface(str(backlog), "C-2", "no-fix-needed", "R-2")
        if okc:
            fails.append("a divergent existing close must be refused by _close_surface")
        if "close:fixed={}".format(real_sha) not in backlog.read_text(encoding="utf-8"):
            fails.append("the refused divergent close must leave the prior close intact")
        okc, _d = _close_surface(str(backlog), "C-2", "fixed", real_sha)
        if not okc:
            fails.append("an exact re-statement of the same close should be idempotently allowed")
        # (i) an item absent from a FRESH enumeration (stale/removed) is cannot-evaluate (exit 2)
        write_aei([{"id": "C-1", "state": "open", "granted": True}])   # C-2 gone from the index
        if route(root, reg, "C-2", "fixed", real_sha, "", str(backlog)) != 2:
            fails.append("routing an item absent from the fresh enumeration should refuse (exit 2)")
    if fails:
        for f in fails:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
