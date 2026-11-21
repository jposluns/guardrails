#!/usr/bin/env python3
"""round-gate-selftest: the self-test of tools/orch_round_gate.py (the ENF-ROUND-PROGRESS-GATE record
reader), and the suite's registered runner in tools/selftest_checks.toml.

  selftest_orch_round_gate.py                              exit 0 on pass, 1 on a failed check
  selftest_orch_round_gate.py --execution-report ABS_PATH  also write the executed check IDs
                                                           (tools/_selftest_exit_report.py)

Exit 2 is cannot-evaluate: a harness error or any other argument (refused before anything runs); never
a pass. A bare launch runs the whole self-test, so the git-fixture-env config lanes, which launch every
registered suite's runner bare under their poisoned configuration, run it as it is; it starts no git
and no child process.

Every refusal case (fc/...) is paired with a flip (flip/...) built by the same function with one value
changed, which must validate; so a reader that refused everything fails every flip, and a reader that
dropped one rule fails that rule's fc case. The accept cases pin the documented form, the plan's filled
example included, as valid. Fixtures name the reviewer families fam-a and fam-b only.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_orch_round_gate.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import orch_round_gate as org  # noqa: E402

SUITE_ID = "round-gate-selftest"
EXECUTED = []
FAILURES = []

FAM_A, FAM_B = "fam-a", "fam-b"
ITEM = "ITEM-0042"
HEADS = ("1a" * 20, "2b" * 20, "3c" * 20, "4d" * 20)
PERMITS = (None, "7d" * 32, "8e" * 32, "9f" * 32)
DELETE = object()


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


# ---------- fixtures ----------

def _tag_hex(tag, width):
    return hashlib.sha256(tag.encode("ascii")).hexdigest()[:width]


def _leg(family, findings, tag, state="complete"):
    return {org.F_FAMILY: family, org.F_STATE: state, org.F_DISPATCH: _tag_hex("d" + tag, 32),
            org.F_REPORT: _tag_hex("r" + tag, 64),
            org.F_FINDINGS: [{org.F_ID: fid, org.F_STATUS: status} for fid, status in findings]}


def _grade(fid, severity, source, **extra):
    grade = {org.F_ID: fid, org.F_SEVERITY: severity, org.F_SOURCE: source}
    grade.update(extra)
    return grade


def _round(number, attempt, head, permit, legs, grades, withdrawn=()):
    """One round line; a leg that names no head reviewed the line's head (null on a missing leg)."""
    for leg in legs:
        leg.setdefault(org.F_HEAD, None if leg[org.F_STATE] == "missing" else head)
    return {org.F_KIND: "round", org.F_ITEM: ITEM, org.F_ROUND: number, org.F_ATTEMPT: attempt,
            org.F_HEAD: head, org.F_PERMIT: permit, org.F_LEGS: legs, org.F_GRADES: grades,
            org.F_WITHDRAWN: list(withdrawn)}


def _golden():
    """The plan's filled example (section 1) as a whole record: the header, the baseline, the first fix
    round, and the second fix round exactly as the example shows it, with full-length hashes."""
    return [
        {org.F_SCHEMA: org.SCHEMA, org.F_KIND: "header", org.F_ITEM: ITEM,
         org.F_ROSTER: [FAM_A, FAM_B], org.F_PLAN: 9, org.F_POLICY: "c3" * 32},
        _round(0, 1, HEADS[0], None,
               [_leg(FAM_A, [("F-4", "open"), ("F-9", "open")], "a0"),
                _leg(FAM_B, [("F-4", "open"), ("F-7", "open")], "b0")],
               [_grade("F-4", "HIGH", "fam-a report, entry one"),
                _grade("F-7", "MEDIUM", "fam-b report, entry two"),
                _grade("F-9", "MEDIUM", "fam-a report, entry two")]),
        _round(1, 1, HEADS[1], PERMITS[1],
               [_leg(FAM_A, [("F-4", "open"), ("F-9", "open")], "a1"),
                _leg(FAM_B, [("F-4", "open"), ("F-7", "open")], "b1")],
               []),
        _round(2, 1, HEADS[2], PERMITS[2],
               [_leg(FAM_A, [("F-4", "open"), ("F-9", "closed"), ("F-15", "open"), ("F-20", "open")],
                     "a2"),
                _leg(FAM_B, [("F-4", "open"), ("F-7", "closed")], "b2")],
               [_grade("F-15", "MEDIUM", "fam-a report, entry three", replaces="F-9"),
                _grade("F-20", "LOW", "fam-a report, entry four", followup="ITEM-0051",
                       impact={"verdict_change": False, "user_visible_change": False,
                               "security_effect": False})]),
    ]


def _set(objs, path, value):
    """Set (or, with DELETE, remove) the value at path, a tuple of keys and indexes from the line list."""
    node = objs
    for step in path[:-1]:
        node = node[step]
    if value is DELETE:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = value
    return objs


def _lines(objs, seq=None, prev=None, edit=None):
    """Serialize objs as chained lines: seq and prev lead each line; seq/prev map a line index to a
    forced value, and edit maps a line index to a function over that line's text, applied before the
    next line chains to it, so a case carries exactly its one defect."""
    out, digest = [], org.ZERO_DIGEST
    for index, body in enumerate(objs):
        line_obj = {org.F_SEQ: (seq or {}).get(index, index), org.F_PREV: (prev or {}).get(index, digest)}
        line_obj.update(body)
        text = json.dumps(line_obj, separators=(",", ":"))
        if edit and index in edit:
            text = edit[index](text)
        out.append(text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return out


def _anchor_bytes(lines):
    tip = lines[-1].encode("utf-8")
    return (json.dumps({org.F_SEQ: len(lines) - 1, org.F_DIGEST: hashlib.sha256(tip).hexdigest()})
            + "\n").encode("ascii")


def _put(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def _write(d, lines, data_edit=None, anchor=True, anchor_data=None, name="item.jsonl"):
    path = os.path.join(d, name)
    data = "".join(line + "\n" for line in lines).encode("utf-8")
    if data_edit is not None:
        data = data_edit(data)
    _put(path, data)
    if anchor:
        _put(path + ".anchor", anchor_data if anchor_data is not None else _anchor_bytes(lines))
    return path


def _validate(path, **kwargs):
    try:
        return "ok", org.validate_record(path, **kwargs)
    except org.CannotEvaluate as exc:
        return "cannot", str(exc)


def _cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = org.main(argv)
    return code, out.getvalue(), err.getvalue()


def _obj(d, bad, path, bad_value, good_value):
    """The golden record with the value at path set to bad_value (bad) or good_value (flip)."""
    objs = _set(_golden(), path, bad_value if bad else good_value)
    return _validate(_write(d, _lines(objs)))


def _edit(d, bad, index, fn):
    """The golden record with line index edited by fn on the bad side; the flip is the golden record."""
    return _validate(_write(d, _lines(_golden(), edit={index: fn} if bad else None)))


def _bytes(d, bad, fn):
    """The golden record's bytes changed by fn on the bad side; the anchor is the golden anchor."""
    return _validate(_write(d, _lines(_golden()), data_edit=fn if bad else None))


def _extra_round(d, bad, make):
    """The golden record plus one more round line (number three) built by make(bad)."""
    objs = _golden()
    objs.append(make(bad))
    return _validate(_write(d, _lines(objs)))


def _r3(legs_a, legs_b, grades=(), withdrawn=(), number=3, attempt=1, permit=PERMITS[3]):
    return _round(number, attempt, HEADS[3], permit, [_leg(FAM_A, legs_a, "a3"), _leg(FAM_B, legs_b, "b3")],
                  list(grades), withdrawn)


# ---------- the cases (each returns ("ok", summary) or ("cannot", reason)) ----------

def c_missing_file(d, bad):
    path = _write(d, _lines(_golden()))
    return _validate(os.path.join(d, "absent.jsonl") if bad else path)


def c_directory(d, bad):
    path = os.path.join(d, "item.jsonl")
    if bad:
        os.mkdir(path)
        _put(path + ".anchor", _anchor_bytes(_lines(_golden())))
        return _validate(path)
    return _validate(_write(d, _lines(_golden())))


def c_symlink(d, bad):
    real = _write(d, _lines(_golden()), name="real.jsonl")
    if not bad:
        return _validate(real)
    link = os.path.join(d, "item.jsonl")
    os.symlink(real, link)
    _put(link + ".anchor", _anchor_bytes(_lines(_golden())))
    return _validate(link)


def c_symlink_parent(d, bad):
    real_dir = os.path.join(d, "real")
    os.mkdir(real_dir)
    path = _write(real_dir, _lines(_golden()))
    if not bad:
        return _validate(path)
    os.symlink(real_dir, os.path.join(d, "alias"))
    return _validate(os.path.join(d, "alias", "item.jsonl"))


def c_fifo(d, bad):
    if not bad:
        return _validate(_write(d, _lines(_golden())))
    path = os.path.join(d, "item.jsonl")
    os.mkfifo(path)
    _put(path + ".anchor", _anchor_bytes(_lines(_golden())))
    return _validate(path)


def c_oversize(d, bad):
    path = _write(d, _lines(_golden()))
    size = os.path.getsize(path)
    return _validate(path, max_bytes=size - 1 if bad else size)


def c_unnormalized(d, bad):
    path = _write(d, _lines(_golden()))
    return _validate(os.path.join(d, ".", "item.jsonl") if bad else path)


def c_relative_path(d, bad):
    path = _write(d, _lines(_golden()))
    code, out, err = _cli(["validate", "--record", os.path.relpath(path) if bad else path])
    return ("ok", out) if code == 0 else ("cannot", err)


def c_argv_unknown(d, bad):
    path = _write(d, _lines(_golden()))
    code, out, err = _cli(["verify" if bad else "validate", "--record", path])
    return ("ok", out) if code == 0 else ("cannot", err)


def c_argv_extra(d, bad):
    path = _write(d, _lines(_golden()))
    code, out, err = _cli(["validate", "--record", path] + (["--repo", d] if bad else []))
    return ("ok", out) if code == 0 else ("cannot", err)


def c_argv_later_mode(d, bad):
    path = _write(d, _lines(_golden()))
    argv = (["check", "--record", path, "--item", ITEM, "--next", "3"] if bad
            else ["validate", "--record", path])
    code, out, err = _cli(argv)
    return ("ok", out) if code == 0 else ("cannot", err)


def c_empty(d, bad):
    return _bytes(d, bad, lambda data: b"")


def c_anchor_missing(d, bad):
    return _validate(_write(d, _lines(_golden()), anchor=not bad))


def c_anchor_mismatch(d, bad):
    lines = _lines(_golden())
    good = _anchor_bytes(lines)
    wrong = good.replace(b'"digest": "', b'"digest": "0', 1)
    wrong = wrong[:wrong.index(b'"digest": "') + 11 + 64] + b'"}\n'
    return _validate(_write(d, lines, anchor_data=wrong if bad else good))


def c_anchor_stale(d, bad):
    lines = _lines(_golden())
    return _validate(_write(d, lines, anchor_data=_anchor_bytes(lines[:-1]) if bad else None))


def c_anchor_symlink(d, bad):
    lines = _lines(_golden())
    path = _write(d, lines, anchor=not bad)
    if bad:
        _put(os.path.join(d, "copy.anchor"), _anchor_bytes(lines))
        os.symlink(os.path.join(d, "copy.anchor"), path + ".anchor")
    return _validate(path)


def c_anchor_malformed(d, bad):
    lines = _lines(_golden())
    tip = hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
    seq = "true" if bad else str(len(lines) - 1)
    data = ('{"seq": ' + seq + ', "digest": "' + tip + '"}\n').encode("ascii")
    return _validate(_write(d, lines, anchor_data=data))


def c_anchor_crlf(d, bad):
    lines = _lines(_golden())
    good = _anchor_bytes(lines)
    return _validate(_write(d, lines, anchor_data=good.replace(b"\n", b"\r\n") if bad else good))


def c_chain_break(d, bad):
    return _validate(_write(d, _lines(_golden(), prev={2: "d" * 64} if bad else None)))


def c_first_prev(d, bad):
    return _validate(_write(d, _lines(_golden(), prev={0: "e" * 64} if bad else None)))


def c_seq_gap(d, bad):
    return _validate(_write(d, _lines(_golden(), seq={2: 3, 3: 4} if bad else None)))


def c_round_gap(d, bad):
    return _obj(d, bad, (3, org.F_ROUND), 3, 2)


def c_attempt_gap(d, bad):
    objs = _golden()
    retry = copy.deepcopy(objs[3])
    _set(objs, (3, org.F_GRADES), [])
    _set(objs, (3, org.F_LEGS, 0, org.F_STATE), "degraded")
    _set(objs, (3, org.F_LEGS, 0, org.F_FINDINGS), [{org.F_ID: "F-4", org.F_STATUS: "open"}])
    _set(objs, (3, org.F_LEGS, 1, org.F_FINDINGS), [{org.F_ID: "F-4", org.F_STATUS: "open"}])
    retry[org.F_ATTEMPT] = 3 if bad else 2
    objs.append(retry)
    return _validate(_write(d, _lines(objs)))


def c_first_round(d, bad):
    objs = _golden()[:2]
    _set(objs, (1, org.F_ROUND), 1 if bad else 0)
    _set(objs, (1, org.F_PERMIT), PERMITS[1] if bad else None)
    return _validate(_write(d, _lines(objs)))


def c_baseline_permit(d, bad):
    return _obj(d, bad, (1, org.F_PERMIT), PERMITS[1], None)


def c_fix_round_permit(d, bad):
    return _obj(d, bad, (2, org.F_PERMIT), None, PERMITS[1])


def c_second_header(d, bad):
    objs = _golden()
    if bad:
        objs.insert(1, copy.deepcopy(objs[0]))
    return _validate(_write(d, _lines(objs)))


def c_bad_kind(d, bad):
    return _obj(d, bad, (2, org.F_KIND), "verdict", "round")


def c_bad_schema(d, bad):
    return _obj(d, bad, (0, org.F_SCHEMA), "aiqt-round-gate/2", org.SCHEMA)


def c_plan_rounds(d, bad):
    return _obj(d, bad, (0, org.F_PLAN), 10, 9)


def c_plan_rounds_float(d, bad):
    return _obj(d, bad, (0, org.F_PLAN), 9.0, 9)


def c_policy_hex(d, bad):
    return _obj(d, bad, (0, org.F_POLICY), "C3" * 32, "c3" * 32)


def c_roster_change(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 1, org.F_FAMILY), "fam-c", FAM_B)


def c_roster_missing_leg(d, bad):
    objs = _golden()
    if bad:
        del objs[2][org.F_LEGS][1]
    return _validate(_write(d, _lines(objs)))


def c_duplicate_leg(d, bad):
    objs = _golden()
    if bad:
        objs[2][org.F_LEGS].append(copy.deepcopy(objs[2][org.F_LEGS][0]))
    return _validate(_write(d, _lines(objs)))


def c_roster_duplicate(d, bad):
    return _obj(d, bad, (0, org.F_ROSTER), [FAM_A, FAM_B, FAM_A], [FAM_A, FAM_B])


def c_roster_empty(d, bad):
    objs = _golden()[:1]
    _set(objs, (0, org.F_ROSTER), [] if bad else [FAM_A])
    return _validate(_write(d, _lines(objs)))


def c_item_mismatch(d, bad):
    return _obj(d, bad, (2, org.F_ITEM), "ITEM-0043", ITEM)


def c_item_shape(d, bad):
    objs = _golden()[:1]
    _set(objs, (0, org.F_ITEM), "ITEM 0042" if bad else "ITEM-0042")
    return _validate(_write(d, _lines(objs)))


def c_head_hex(d, bad):
    return _obj(d, bad, (2, org.F_HEAD), "2B" * 20, "2b" * 20)


def c_head_short(d, bad):
    return _obj(d, bad, (2, org.F_HEAD), "2b" * 19 + "2", "2b" * 20)


def c_dispatch_hex(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_DISPATCH), "g" * 32, "f" * 32)


def c_dispatch_null(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_DISPATCH), None, "f" * 32)


def c_report_hex(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_REPORT), "a1" * 31, None)


def c_bad_state(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_STATE), "partial", "degraded")


def c_bad_status(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_FINDINGS, 0, org.F_STATUS), "fixed", "closed")


def _missing_leg(bad, findings, report):
    objs = _golden()
    leg = objs[2][org.F_LEGS][1]
    leg[org.F_STATE] = "missing"
    leg[org.F_HEAD] = None
    leg[org.F_DISPATCH] = None
    leg[org.F_REPORT] = report if bad else None
    leg[org.F_FINDINGS] = findings if bad else []
    return objs


def c_missing_findings(d, bad):
    return _validate(_write(d, _lines(_missing_leg(bad, [{org.F_ID: "F-4", org.F_STATUS: "open"}],
                                                     None))))


def c_missing_report(d, bad):
    return _validate(_write(d, _lines(_missing_leg(bad, [], "b2" * 32))))


def c_empty_findings(d, bad):
    objs = _golden()
    leg = objs[2][org.F_LEGS][1]
    leg[org.F_STATE] = "empty"
    leg[org.F_FINDINGS] = [{org.F_ID: "F-4", org.F_STATUS: "open"}] if bad else []
    return _validate(_write(d, _lines(objs)))


def c_dup_finding(d, bad):
    objs = _golden()
    findings = objs[2][org.F_LEGS][0][org.F_FINDINGS]
    findings.append({org.F_ID: "F-4", org.F_STATUS: "closed" if bad else "open"})
    if not bad:
        findings[-1][org.F_ID] = "F-7"
    return _validate(_write(d, _lines(objs)))


def c_leg_severity(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_FINDINGS, 0, org.F_SEVERITY), "HIGH", DELETE)


def c_bad_severity(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 0, org.F_SEVERITY), "CRITICAL", "MEDIUM")


def c_severity_case(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 0, org.F_SEVERITY), "medium", "MEDIUM")


def c_regrade(d, bad):
    return _extra_round(d, bad, lambda b: _r3([("F-4", "open")], [("F-4", "open")],
                                             [_grade("F-4", "MEDIUM", "fam-a report, entry one")] if b else []))


def c_regrade_same(d, bad):
    return _extra_round(d, bad, lambda b: _r3([("F-4", "open")], [("F-4", "open")],
                                             [_grade("F-4", "HIGH", "fam-a report, entry one")] if b else []))


def c_ungraded(d, bad):
    return _extra_round(d, bad, lambda b: _r3([("F-4", "open"), ("F-30", "open")], [("F-4", "open")],
                                             [] if b else [_grade("F-30", "LOW", "fam-a report, entry two")]))


def c_grade_no_source(d, bad):
    return _extra_round(d, bad, lambda b: _r3([("F-4", "open"), ("F-31", "open")] if not b
                                             else [("F-4", "open")], [("F-4", "open")],
                                             [_grade("F-31", "LOW", "fam-a report, entry two")]))


def c_replaces_unknown(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 0, org.F_REPLACES), "F-99", "F-9")


def c_replaces_self(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 0, org.F_REPLACES), "F-15", "F-9")


def c_followup_shape(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 1, org.F_FOLLOWUP), "", "ITEM-0051")


def c_source_empty(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 0, org.F_SOURCE), "", "fam-a report, entry three")


def c_source_control(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 0, org.F_SOURCE), "fam-a report,\x1bentry three",
                "fam-a report, entry three")


def c_impact_type(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 1, org.F_IMPACT, "verdict_change"), "false", False)


def c_impact_int(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 1, org.F_IMPACT, "security_effect"), 0, False)


def c_impact_key(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 1, org.F_IMPACT, "cost_change"), False, DELETE)


def c_grade_unknown(d, bad):
    return _obj(d, bad, (3, org.F_GRADES, 1, "family_severity"), "HIGH", DELETE)


def _withdraw(bad_entry, good_entry):
    return lambda b: _r3([("F-4", "open")], [("F-4", "open")], [],
                         [bad_entry if b else good_entry])


def c_withdraw_ungraded(d, bad):
    return _extra_round(d, bad, _withdraw({org.F_ID: "F-99", org.F_BASIS: "duplicate of F-4"},
                                          {org.F_ID: "F-7", org.F_BASIS: "duplicate of F-4"}))


def c_withdraw_basis(d, bad):
    return _extra_round(d, bad, _withdraw({org.F_ID: "F-7", org.F_BASIS: ""},
                                          {org.F_ID: "F-7", org.F_BASIS: "duplicate of F-4"}))


def c_withdraw_twice(d, bad):
    objs = _golden()
    objs[3][org.F_WITHDRAWN] = [{org.F_ID: "F-7", org.F_BASIS: "duplicate of F-4"}]
    objs.append(_r3([("F-4", "open")], [("F-4", "open")], [],
                    [{org.F_ID: "F-7" if bad else "F-20", org.F_BASIS: "out of scope"}]))
    return _validate(_write(d, _lines(objs)))


def c_unknown_field(d, bad):
    return _obj(d, bad, (2, "note"), "x", DELETE)


def c_unknown_leg_field(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, "note"), "x", DELETE)


def c_leg_head_absent(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_HEAD), DELETE, HEADS[1])


def c_leg_head_hex(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_HEAD), "2B" * 20, "2b" * 20)


def c_leg_head_short(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 1, org.F_HEAD), "2b" * 19 + "2", "2b" * 20)


def c_leg_head_null(d, bad):
    return _obj(d, bad, (2, org.F_LEGS, 0, org.F_HEAD), None, HEADS[1])


def c_missing_leg_head(d, bad):
    objs = _missing_leg(False, [], None)
    objs[2][org.F_LEGS][1][org.F_HEAD] = HEADS[1] if bad else None
    return _validate(_write(d, _lines(objs)))


def c_missing_field(d, bad):
    return _obj(d, bad, (2, org.F_WITHDRAWN), DELETE, [])


def c_bool_as_int(d, bad):
    return _obj(d, bad, (2, org.F_ROUND), True, 1)


def c_float_as_int(d, bad):
    return _obj(d, bad, (2, org.F_ATTEMPT), 1.0, 1)


def c_seq_bool(d, bad):
    return _edit(d, bad, 0, lambda t: t.replace('{"seq":0,', '{"seq":false,', 1))


def c_nan(d, bad):
    return _edit(d, bad, 2, lambda t: t.replace('"attempt":1', '"attempt":NaN', 1))


def c_infinity(d, bad):
    return _edit(d, bad, 2, lambda t: t.replace('"attempt":1', '"attempt":Infinity', 1))


def c_dup_key(d, bad):
    return _edit(d, bad, 2, lambda t: t.replace('"kind":"round"', '"kind":"round","kind":"round"', 1))


def c_invalid_json(d, bad):
    return _edit(d, bad, 2, lambda t: t[:-1])


def c_non_object(d, bad):
    return _edit(d, bad, 2, lambda t: "[" + t + "]")


def c_crlf(d, bad):
    return _bytes(d, bad, lambda data: data.replace(b"\n", b"\r\n", 1))


def c_bom(d, bad):
    return _bytes(d, bad, lambda data: org.BOM + data)


def c_non_utf8(d, bad):
    return _bytes(d, bad, lambda data: data.replace(b"entry three", b"entry \xffhree", 1))


def c_final_lf(d, bad):
    return _bytes(d, bad, lambda data: data[:-1])


def c_blank_line(d, bad):
    return _bytes(d, bad, lambda data: data.replace(b"\n", b"\n\n", 1))


def c_line_cap(d, bad):
    objs = _golden()
    objs[3][org.F_GRADES][0][org.F_SOURCE] = "x" * org.MAX_TEXT
    old = org.MAX_LINE_BYTES
    lines = _lines(objs)
    org.MAX_LINE_BYTES = len(lines[3].encode("utf-8")) - (1 if bad else 0)
    try:
        return _validate(_write(d, lines))
    finally:
        org.MAX_LINE_BYTES = old


def _self_test():
    with tempfile.TemporaryDirectory(prefix="round-gate-selftest-") as tmp:
        base = os.path.realpath(tmp)
        counter = iter(range(100000))

        def fresh():
            d = os.path.join(base, "c{}".format(next(counter)))
            os.mkdir(d)
            return d

        def run(build, d, bad):
            try:
                return build(d, bad)
            except Exception as exc:  # an escaped exception is a failed check, never a crashed suite
                return "raised", "{}: {}".format(type(exc).__name__, str(exc)[:200])

        # ---------- accepted: the documented form ----------
        d = fresh()
        got = _validate(_write(d, _lines(_golden())))
        check("accept/documented-example", got[0] == "ok" and got[1] == {
            "item": ITEM, "lines": 4, "tip_seq": 3, "last_round": 2, "last_attempt": 1}, got)
        d = fresh()
        got = _validate(_write(d, _lines(_golden()[:1])))
        check("accept/header-only", got[0] == "ok" and got[1]["last_round"] is None
              and got[1]["tip_seq"] == 0, got)
        d = fresh()
        objs = _golden()
        objs.append(_round(3, 1, HEADS[3], PERMITS[3],
                           [_leg(FAM_A, [("F-4", "open")], "a3", state="degraded"),
                            _leg(FAM_B, [], "b3", state="empty")], []))
        objs.append(_round(3, 2, HEADS[3], PERMITS[3],
                           [_leg(FAM_A, [("F-4", "open")], "a4"),
                            {org.F_FAMILY: FAM_B, org.F_STATE: "missing", org.F_DISPATCH: None,
                             org.F_REPORT: None, org.F_FINDINGS: []}], []))
        got = _validate(_write(d, _lines(objs)))
        check("accept/every-leg-state-and-retry", got[0] == "ok" and got[1]["last_round"] == 3
              and got[1]["last_attempt"] == 2, got)
        d = fresh()
        objs = _golden()
        objs.append(_r3([("F-4", "open")], [("F-4", "open")], [],
                        [{org.F_ID: "F-7", org.F_BASIS: "duplicate of F-4"}]))
        check("accept/withdrawal", _validate(_write(d, _lines(objs)))[0] == "ok")
        d = fresh()
        objs = _set(_golden(), (3, org.F_GRADES, 1, org.F_IMPACT), {"verdict_change": True})
        got = _validate(_write(d, _lines(objs)))
        check("accept/partial-or-true-impact-is-structurally-valid", got[0] == "ok", got)
        d = fresh()
        objs = _set(_golden(), (3, org.F_LEGS, 0, org.F_REPORT), None)
        check("accept/null-report-on-complete-leg", _validate(_write(d, _lines(objs)))[0] == "ok")
        d = fresh()
        relinked = []
        digest = org.ZERO_DIGEST
        for line in _lines(_golden()):
            obj = json.loads(line)
            obj[org.F_PREV] = digest
            text = json.dumps(obj, separators=(", ", ": "))
            relinked.append(text)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        check("accept/any-json-spacing", _validate(_write(d, relinked))[0] == "ok")
        d = fresh()
        objs = _set(_golden(), (3, org.F_LEGS, 1, org.F_HEAD), HEADS[1])
        got = _validate(_write(d, _lines(objs)))
        stored = json.loads(_lines(objs)[3])
        check("accept/leg-head-differs-is-recorded-for-the-gate", got[0] == "ok"
              and stored[org.F_HEAD] == HEADS[2]
              and [leg[org.F_HEAD] for leg in stored[org.F_LEGS]] == [HEADS[2], HEADS[1]], (got, stored))
        heads_ok = all(leg[org.F_HEAD] == line_obj[org.F_HEAD] for line_obj in _golden()[1:]
                       for leg in line_obj[org.F_LEGS])
        check("fixture/golden-legs-reviewed-the-line-head", heads_ok)
        families = set()
        for line_obj in _golden()[1:]:
            for leg in line_obj[org.F_LEGS]:
                families.add(leg[org.F_FAMILY])
        check("fixture/neutral-family-names", families == {FAM_A, FAM_B}
              and _golden()[0][org.F_ROSTER] == [FAM_A, FAM_B], families)

        # ---------- command line ----------
        d = fresh()
        path = _write(d, _lines(_golden()))
        code, out, err = _cli(["validate", "--record", path])
        rows = out.split("\n")
        try:
            head = json.loads(rows[0])
        except ValueError:
            head = None
        check("cli/valid-stdout-one-object-then-one-line", code == 0 and err == "" and len(rows) == 3
              and rows[2] == "" and isinstance(head, dict) and head.get("decision") == "VALID"
              and rows[1].startswith("VALID item=ITEM-0042 lines=4"), (code, out, err))
        code, out, err = _cli(["validate", "--record", os.path.join(d, "absent.jsonl")])
        rows = out.split("\n")
        try:
            head = json.loads(rows[0])
        except ValueError:
            head = None
        check("cli/refusal-stdout-one-object-stderr-one-line", code == 2 and len(rows) == 3
              and isinstance(head, dict) and head.get("decision") == "CANNOT_EVALUATE"
              and rows[1].startswith("CANNOT_EVALUATE ") and err.startswith("CANNOT EVALUATE: ")
              and err.count("\n") == 1, (code, out, err))
        code, out, err = _cli([])
        check("cli/no-arguments-exit-2", code == 2 and err.startswith("CANNOT EVALUATE: usage"),
              (code, out, err))
        # A value carried in a reason (here the record path) cannot break the framing: stdout stays one
        # JSON object (holding the exact path) then one line, stderr one line, all printable ASCII.
        for cid, name in (("cli/refusal-path-lf-stays-framed", "no\nsuch.jsonl"),
                          ("cli/refusal-path-cr-stays-framed", "no\rsuch.jsonl"),
                          ("cli/refusal-path-line-separator-stays-framed", "no such.jsonl"),
                          ("cli/refusal-path-undecodable-byte-stays-framed", "no\udcffsuch.jsonl")):
            bad_path = os.path.join(d, name)
            code, out, err = _cli(["validate", "--record", bad_path])
            rows = out.split("\n")
            try:
                head = json.loads(rows[0])
            except ValueError:
                head = None
            check(cid, code == 2 and len(rows) == 3 and rows[2] == "" and out.isascii() and err.isascii()
                  and err.count("\n") == 1 and err.endswith("\n") and isinstance(head, dict)
                  and head.get("decision") == "CANNOT_EVALUATE" and bad_path in head.get("reason", "")
                  and rows[1].startswith("CANNOT_EVALUATE ") and org.printable(bad_path) in rows[1]
                  and err.startswith("CANNOT EVALUATE: ") and org.printable(bad_path) in err,
                  (code, out, err))
        sample = "a\nb\\c\x7fé \udcff\U0001f600"
        check("cli/printable-escape-is-ascii-and-unambiguous", org.printable(sample)
              == "a\\x0ab\\\\c\\x7f\\xe9\\u2028\\udcff\\U0001f600", org.printable(sample))

        # ---------- refusals, each with its flip ----------
        for cid, flip, build, want in (
                ("fc/unreadable", "flip/unreadable", c_missing_file, "is unreadable"),
                ("fc/directory", "flip/directory", c_directory, "not a regular file"),
                ("fc/symlink", "flip/symlink", c_symlink, "not a regular file"),
                ("fc/symlink-parent", "flip/symlink-parent", c_symlink_parent, "not a plain directory"),
                ("fc/fifo", "flip/fifo", c_fifo, "not a regular file"),
                ("fc/oversize", "flip/oversize", c_oversize, "over its"),
                ("fc/unnormalized-path", "flip/unnormalized-path", c_unnormalized, "normalized"),
                ("fc/relative-path", "flip/relative-path", c_relative_path, "normalized"),
                ("fc/argv-unknown", "flip/argv-unknown", c_argv_unknown, "usage"),
                ("fc/argv-extra", "flip/argv-extra", c_argv_extra, "usage"),
                ("fc/argv-later-mode", "flip/argv-later-mode", c_argv_later_mode, "usage"),
                ("fc/empty-record", "flip/empty-record", c_empty, "is empty"),
                ("fc/non-utf8", "flip/non-utf8", c_non_utf8, "not UTF-8"),
                ("fc/crlf", "flip/crlf", c_crlf, "carriage return"),
                ("fc/bom", "flip/bom", c_bom, "byte order mark"),
                ("fc/missing-final-lf", "flip/missing-final-lf", c_final_lf, "no LF"),
                ("fc/blank-line", "flip/blank-line", c_blank_line, "blank line"),
                ("fc/line-cap", "flip/line-cap", c_line_cap, "line cap"),
                ("fc/invalid-json", "flip/invalid-json", c_invalid_json, "invalid JSON"),
                ("fc/non-object-line", "flip/non-object-line", c_non_object, "one JSON object"),
                ("fc/dup-key", "flip/dup-key", c_dup_key, "duplicate key"),
                ("fc/nan", "flip/nan", c_nan, "non-finite"),
                ("fc/infinity", "flip/infinity", c_infinity, "non-finite"),
                ("fc/bool-as-int", "flip/bool-as-int", c_bool_as_int, "boolean or float"),
                ("fc/float-as-int", "flip/float-as-int", c_float_as_int, "boolean or float"),
                ("fc/seq-bool", "flip/seq-bool", c_seq_bool, "boolean or float"),
                ("fc/unknown-field", "flip/unknown-field", c_unknown_field, "unknown field"),
                ("fc/unknown-leg-field", "flip/unknown-leg-field", c_unknown_leg_field, "unknown field"),
                ("fc/missing-field", "flip/missing-field", c_missing_field, "missing field"),
                ("fc/leg-severity", "flip/leg-severity", c_leg_severity, "unknown field"),
                ("fc/grade-unknown-field", "flip/grade-unknown-field", c_grade_unknown, "unknown field"),
                ("fc/bad-schema", "flip/bad-schema", c_bad_schema, "schema"),
                ("fc/bad-kind", "flip/bad-kind", c_bad_kind, "is not one of"),
                ("fc/plan-rounds", "flip/plan-rounds", c_plan_rounds, "plan_rounds"),
                ("fc/plan-rounds-float", "flip/plan-rounds-float", c_plan_rounds_float, "plan_rounds"),
                ("fc/policy-not-hex", "flip/policy-not-hex", c_policy_hex, "lowercase hex"),
                ("fc/bad-severity", "flip/bad-severity", c_bad_severity, "is not one of"),
                ("fc/severity-case", "flip/severity-case", c_severity_case, "is not one of"),
                ("fc/bad-status", "flip/bad-status", c_bad_status, "is not one of"),
                ("fc/bad-state", "flip/bad-state", c_bad_state, "is not one of"),
                ("fc/impact-not-boolean", "flip/impact-not-boolean", c_impact_type, "JSON boolean"),
                ("fc/impact-int", "flip/impact-int", c_impact_int, "JSON boolean"),
                ("fc/impact-unknown-key", "flip/impact-unknown-key", c_impact_key, "unknown field"),
                ("fc/chain-break", "flip/chain-break", c_chain_break, "broken chain"),
                ("fc/first-prev-not-zero", "flip/first-prev-not-zero", c_first_prev, "broken chain"),
                ("fc/seq-gap", "flip/seq-gap", c_seq_gap, "seq gap"),
                ("fc/anchor-missing", "flip/anchor-missing", c_anchor_missing, "is unreadable"),
                ("fc/anchor-mismatch", "flip/anchor-mismatch", c_anchor_mismatch, "does not name"),
                ("fc/anchor-stale", "flip/anchor-stale", c_anchor_stale, "does not name"),
                ("fc/anchor-symlink", "flip/anchor-symlink", c_anchor_symlink, "not a regular file"),
                ("fc/anchor-malformed", "flip/anchor-malformed", c_anchor_malformed, "boolean or float"),
                ("fc/anchor-crlf", "flip/anchor-crlf", c_anchor_crlf, "one LF-ended line"),
                ("fc/round-gap", "flip/round-gap", c_round_gap, "round or attempt gap"),
                ("fc/attempt-gap", "flip/attempt-gap", c_attempt_gap, "round or attempt gap"),
                ("fc/first-round-not-baseline", "flip/first-round-not-baseline", c_first_round,
                 "must be the baseline"),
                ("fc/baseline-permit", "flip/baseline-permit", c_baseline_permit,
                 "null on the baseline round"),
                ("fc/fix-round-no-permit", "flip/fix-round-no-permit", c_fix_round_permit, "a fix round"),
                ("fc/second-header", "flip/second-header", c_second_header, "field"),
                ("fc/roster-change", "flip/roster-change", c_roster_change, "not in the header roster"),
                ("fc/roster-missing-leg", "flip/roster-missing-leg", c_roster_missing_leg,
                 "differ from the header roster"),
                ("fc/duplicate-leg", "flip/duplicate-leg", c_duplicate_leg, "two legs"),
                ("fc/roster-duplicate", "flip/roster-duplicate", c_roster_duplicate, "listed twice"),
                ("fc/roster-empty", "flip/roster-empty", c_roster_empty, "at least one family"),
                ("fc/item-mismatch", "flip/item-mismatch", c_item_mismatch, "differs from the header"),
                ("fc/item-shape", "flip/item-shape", c_item_shape, "an id"),
                ("fc/head-not-hex", "flip/head-not-hex", c_head_hex, "40 lowercase hex"),
                ("fc/head-short", "flip/head-short", c_head_short, "40 lowercase hex"),
                ("fc/leg-head-absent", "flip/leg-head-absent", c_leg_head_absent, "missing field"),
                ("fc/leg-head-not-hex", "flip/leg-head-not-hex", c_leg_head_hex, "40 lowercase hex"),
                ("fc/leg-head-short", "flip/leg-head-short", c_leg_head_short, "40 lowercase hex"),
                ("fc/leg-head-null", "flip/leg-head-null", c_leg_head_null, "40 lowercase hex"),
                ("fc/missing-leg-head", "flip/missing-leg-head", c_missing_leg_head,
                 "carries no head"),
                ("fc/dispatch-id-not-hex", "flip/dispatch-id-not-hex", c_dispatch_hex, "32 lowercase hex"),
                ("fc/dispatch-id-null", "flip/dispatch-id-null", c_dispatch_null, "32 lowercase hex"),
                ("fc/report-not-hex", "flip/report-not-hex", c_report_hex, "64 lowercase hex"),
                ("fc/missing-leg-findings", "flip/missing-leg-findings", c_missing_findings,
                 "carries no findings"),
                ("fc/missing-leg-report", "flip/missing-leg-report", c_missing_report,
                 "carries no report_sha256"),
                ("fc/empty-leg-findings", "flip/empty-leg-findings", c_empty_findings,
                 "carries no findings"),
                ("fc/duplicate-finding", "flip/duplicate-finding", c_dup_finding, "reported twice"),
                ("fc/regrade", "flip/regrade", c_regrade, "regrade is refused"),
                ("fc/regrade-same-severity", "flip/regrade-same-severity", c_regrade_same,
                 "regrade is refused"),
                ("fc/ungraded-finding", "flip/ungraded-finding", c_ungraded, "have no grade"),
                ("fc/grade-without-source", "flip/grade-without-source", c_grade_no_source,
                 "no leg on this line reports it"),
                ("fc/replaces-unknown", "flip/replaces-unknown", c_replaces_unknown, "graded before"),
                ("fc/replaces-self", "flip/replaces-self", c_replaces_self, "graded before"),
                ("fc/followup-shape", "flip/followup-shape", c_followup_shape, "an id"),
                ("fc/source-ref-empty", "flip/source-ref-empty", c_source_empty, "characters of text"),
                ("fc/source-ref-control", "flip/source-ref-control", c_source_control,
                 "characters of text"),
                ("fc/withdraw-ungraded", "flip/withdraw-ungraded", c_withdraw_ungraded, "never graded"),
                ("fc/withdraw-basis", "flip/withdraw-basis", c_withdraw_basis, "characters of text"),
                ("fc/withdraw-twice", "flip/withdraw-twice", c_withdraw_twice, "already withdrawn"),
        ):
            got = run(build, fresh(), True)
            check(cid, got[0] == "cannot" and want in got[1], got)
            got = run(build, fresh(), False)
            check(flip, got[0] == "ok", got)

    if FAILURES:
        print("round-gate-selftest: {} of {} checks failed:".format(len(FAILURES), len(EXECUTED)),
              file=sys.stderr)
        for failure in FAILURES:
            print("  " + failure, file=sys.stderr)
        return 1
    print("round-gate-selftest: PASS ({} checks: the documented record form validates; every refusal "
          "case is cannot-evaluate with its named reason and its one-value flip validates)".format(
              len(EXECUTED)))
    return 0


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    # The execution gate appends `--execution-report ABS_PATH`; accepted only as that whole two-token
    # argv, with an absolute path. A bare launch is the self-test itself.
    report_path = None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        report_path, argv = argv[1], []
    if argv:
        print("error: unrecognized arguments {!r}; fail closed (cannot evaluate)".format(argv), file=sys.stderr)
        return 2
    if report_path is not None:
        # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    return _self_test()


if __name__ == "__main__":
    sys.exit(main())
