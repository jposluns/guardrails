#!/usr/bin/env python3
"""Mistakes-register appender and AEI projection (GD-112 component 6; rules mstreg/slfgrd).

  orch_register.py append --register PATH --id MR-N --mistake T --evidence R --rule CID \
                          --guardrail T [--status proposed] [--commit SHA --check-ref PATH]
      Append one chained row (seq and prev computed from the file; a wrong --id fails).
  orch_register.py project --register PATH
      Emit an AEI v1 enumeration of the rows whose LATEST status is proposed or accepted and not yet
      landed, as granted open items, so the stop guard itself enforces mistake-to-guardrail
      follow-through. A registry can name this as (part of) its enumerator.
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

ZERO = "0" * 64


def _lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def cmd_append(a):
    path = Path(a.register)
    lines = _lines(path)
    prev = ZERO if not lines else hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
    seq = len(lines) + 1
    want_id = "MR-{}".format(sum(1 for l in lines if '"status": "proposed"' in l) + 1) \
        if a.status == "proposed" else a.id
    if a.status == "proposed" and a.id != want_id:
        sys.exit("append: a new proposal must take the next id {} (ids are permanent, "
                 "never reused)".format(want_id))
    row = {"seq": seq, "id": a.id, "ts": datetime.datetime.now(
        datetime.timezone.utc).isoformat(), "mistake": a.mistake, "evidence": a.evidence,
        "rule": a.rule, "guardrail": a.guardrail, "status": a.status, "prev": prev}
    if a.commit:
        row["commit"] = a.commit
    if a.check_ref:
        row["check_ref"] = a.check_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    print("appended {} seq {}".format(a.id, seq))
    return 0


def cmd_project(a):
    latest = {}
    for line in _lines(Path(a.register)):
        try:
            row = json.loads(line)
        except ValueError:
            sys.exit("project: malformed register line (run check_mistakes_register.py)")
        latest[row.get("id")] = row
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items = [{"id": "{}-guardrail".format(rid), "title": "land the guardrail: {}".format(
        row.get("guardrail", "")), "state": "open", "granted": row["status"] == "accepted"}
        for rid, row in sorted(latest.items())
        if row.get("status") in ("proposed", "accepted")]
    for it in items:  # a proposed row enumerates but does not compel (express authorization)
        if not it["granted"]:
            it["state"] = "proposed"
    print(json.dumps({"version": 1, "generated_at_utc": now,
                      "source": {"locator": a.register, "revision": "", "observed_at_utc": now},
                      "items": items}))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("append")
    for name, req in (("--register", True), ("--id", True), ("--mistake", True),
                      ("--evidence", True), ("--rule", True), ("--guardrail", True),
                      ("--status", False), ("--commit", False), ("--check-ref", False)):
        p1.add_argument(name, required=req, dest=name.lstrip("-").replace("-", "_"),
                        default="proposed" if name == "--status" else None)
    p2 = sub.add_parser("project")
    p2.add_argument("--register", required=True)
    a = ap.parse_args()
    return cmd_append(a) if a.cmd == "append" else cmd_project(a)


if __name__ == "__main__":
    sys.exit(main())
