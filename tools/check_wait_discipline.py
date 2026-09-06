#!/usr/bin/env python3
"""Wait-discipline gate (GD-164; rule livbak): lint the registry-declared coordination scripts for the
unbounded-wait and unsafe-completion classes that a bounded, fail-loud, dormancy-backstopped wait must
avoid. POSIX shell and Python are checked in v1.

Two checks over every entry in the registry key `coordination_scripts` (a list of string paths, or
{"path": ..., "language": ..., "validator": ...} objects):

  Unbounded-wait lint  (rejects): a poll loop with no elapsed-time or attempt bound; a timeout branch
    that silently re-enters the same loop; a short-wait loop that recomputes its deadline inside the
    body (the deadline never arrives); a blocking wait call with no timeout.
  Completion-derivation lint  (rejects): completion derived solely from a sentinel file's existence
    with no artefact-plus-terminal-status fallback; a kill/pkill/kill %job with no adjacent consumer
    enumeration or ledger reconciliation.

FAIL-CLOSED (chkfcl): a declared script that is absent, unreadable, or unparseable in its declared
language fails the gate NAMING the script; an unsupported language with no declared validator is
cannot-evaluate and fails, while a declared validator defers the script to that validator (recorded,
passed). The machine result enumerates the exact declared set the gate covered (setcmp applied to the
gate itself): a declared script the gate could not cover is a failure, never a silent skip.

Exit: 0 every declared script covered and clean; 1 a real finding (a lint rejection); 2 usage or
cannot-evaluate (no registry, an unreadable/unparseable/absent declared script, an unsupported language
with no validator).

  check_wait_discipline.py             lint the declared roster in this repo
  check_wait_discipline.py --self-test the wd/* rejected/accepted/fail-closed fixtures
  check_wait_discipline.py --result-json PATH   also write the machine result

DISCLOSED RESIDUAL (open grammar, class c): the lints are lexical over an open command grammar and
catch only recognizable shapes. An obfuscated or indirected loop (a bound hidden behind a variable or
a helper function, a `sleep` reached through an alias), an indirected sentinel (a completion signal
read through a computed path or a library call), library-internal polling (a blocking primitive inside
a dependency the gate does not read), and any language outside {POSIX shell, Python} without a declared
validator are NOT caught here. The yield-boundary re-enumeration (the livbak wait guard) is the net
behind this lint; this gate narrows the class at commit time, it does not close it.
"""
import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

sys.path.insert(0, str(repo_root() / ".aiqt" / "core" / "hooks" / "scripts"))
import aiqt_hooks  # noqa: E402

SH_EXT = (".sh", ".bash")
PY_EXT = (".py",)


def _language_for(path, declared):
    if declared:
        return declared
    ext = os.path.splitext(path)[1].lower()
    if ext in SH_EXT:
        return "posix-shell"
    if ext in PY_EXT:
        return "python"
    return "unsupported"


# ---- POSIX shell heuristics (lexical; residual disclosed) ---------------------------------------
_SH_LOOP_HEAD = re.compile(r"\b(while|until)\b\s+(true|false|:|\[\s*1\s*\])", re.IGNORECASE)
_SH_SLEEP = re.compile(r"\bsleep\b")
_SH_BOUND = re.compile(r"\bbreak\b|-(ge|gt|le|lt)\b|\bSECONDS\b|date\s+\+%s")
_SH_TIMEOUT_CONT = re.compile(r"(timeout|timed?\s*out|TMOUT|-t\s+\d)", re.IGNORECASE)
_SH_DEADLINE_ASSIGN = re.compile(r"\bdeadline\s*=.*(date\s+\+%s|SECONDS|now)", re.IGNORECASE)
_SH_BLOCKING_WAIT = re.compile(r"\bwait\b\s+(\"?\$?\w+\"?|%\d+|%\w+)?\s*$")
_SH_SENTINEL = re.compile(r"\[\s*(?:!\s*)?-[ef]\s+\"?\$?\w+\"?\s*\]|test\s+-[ef]\b")
_SH_STATUS = re.compile(r"\$\?|returncode|\brc\b|exit\s+[0-9]|terminal|status")
_SH_KILL = re.compile(r"\b(kill|pkill)\b|\bkill\s+%\w+")
_SH_CONSUMER = re.compile(r"ledger|reconcile|for\s+\w+\s+in\b|consume|drain")


def _sh_loop_blocks(src):
    """Yield (start_line, body_lines) for each while/until ... done block (shallow, non-nested aware
    enough for the lint; disclosed residual on deep nesting)."""
    lines = src.splitlines()
    i = 0
    while i < len(lines):
        if re.search(r"\b(while|until)\b", lines[i]):
            depth = 0
            body = []
            j = i
            while j < len(lines):
                if re.search(r"\bdo\b", lines[j]):
                    depth += 1
                if j > i:
                    body.append(lines[j])
                if re.search(r"\bdone\b", lines[j]):
                    depth -= 1
                    if depth <= 0:
                        break
                j += 1
            yield i, lines[i], "\n".join(body)
            i = j + 1
        else:
            i += 1


def lint_shell(name, src):
    findings = []
    for _n, head, body in _sh_loop_blocks(src):
        block = head + "\n" + body
        if _SH_LOOP_HEAD.search(head) and _SH_SLEEP.search(block) and not _SH_BOUND.search(block):
            findings.append("{}: unbounded poll loop (while/until true with sleep and no elapsed-time "
                            "or attempt bound)".format(name))
        if _SH_TIMEOUT_CONT.search(block) and re.search(r"\bcontinue\b", block) \
                and not _SH_BOUND.search(block):
            findings.append("{}: a timeout branch re-enters the same loop with no bound advance".format(name))
        if _SH_DEADLINE_ASSIGN.search(body):
            findings.append("{}: a deadline is recomputed inside the loop body (it never arrives)".format(name))
    for ln in src.splitlines():
        if _SH_BLOCKING_WAIT.search(ln.strip()) and "timeout" not in ln.lower():
            findings.append("{}: a blocking wait call carries no timeout: {!r}".format(name, ln.strip()[:80]))
    if _SH_SENTINEL.search(src) and not _SH_STATUS.search(src):
        findings.append("{}: completion is derived from a sentinel file alone, with no "
                        "artefact-plus-terminal-status fallback".format(name))
    for ln in src.splitlines():
        if _SH_KILL.search(ln):
            window = src[max(0, src.find(ln) - 200): src.find(ln) + 200]
            if not _SH_CONSUMER.search(window):
                findings.append("{}: a kill/pkill has no adjacent consumer enumeration or ledger "
                                "reconciliation: {!r}".format(name, ln.strip()[:80]))
    return findings


def parse_shell_ok(src):
    """A cheap structural parse-ability check for shell (disclosed heuristic): balanced do/done and
    if/fi. An imbalance reads as unparseable (fail-closed). Comments and quoted spans are stripped
    first, so a structural keyword inside a string literal or a $variable (e.g. '^status: done',
    "$done") does not spuriously unbalance the count."""
    stripped = re.sub(r"#.*", "", src)
    stripped = re.sub(r"'[^']*'", "", stripped)
    stripped = re.sub(r'"[^"]*"', "", stripped)
    # B7: after balanced quote spans are removed, ANY residual quote is an UNBALANCED (unterminated)
    # quote, which reads as unparseable (fail-closed) rather than a clean pass on `echo 'unterminated`.
    if "'" in stripped or '"' in stripped:
        return False
    tokens = re.findall(r"(?<![\w$])(do|done|if|fi|case|esac)\b", stripped)
    bal = {"do": 0, "if": 0, "case": 0}
    for t in tokens:
        if t in bal:
            bal[t] += 1
        elif t == "done":
            bal["do"] -= 1
        elif t == "fi":
            bal["if"] -= 1
        elif t == "esac":
            bal["case"] -= 1
    return all(v == 0 for v in bal.values())


# ---- Python heuristics (AST) ---------------------------------------------------------------------
def lint_python(name, src):
    findings = []
    tree = ast.parse(src)   # SyntaxError -> caller treats as unparseable (fail-closed)

    class Visitor(ast.NodeVisitor):
        def visit_While(self, node):
            is_true = isinstance(node.test, ast.Constant) and node.test.value is True
            body_src = ast.dump(node)
            has_sleep = "time.sleep" in ast.unparse(node) if hasattr(ast, "unparse") else "sleep" in body_src
            has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
            has_bound = any(isinstance(n, ast.Compare) for n in ast.walk(node)) \
                or any(isinstance(n, ast.AugAssign) for n in ast.walk(node))
            if is_true and has_sleep and not (has_break or has_bound):
                findings.append("{}: unbounded poll loop (while True with sleep and no counter, "
                                "deadline, or break)".format(name))
            # deadline recomputed inside the loop body
            for n in ast.walk(node):
                if isinstance(n, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "deadline" for t in n.targets):
                    findings.append("{}: a deadline is reassigned inside the loop body (it never "
                                    "arrives)".format(name))
                    break
            self.generic_visit(node)

        def visit_Call(self, node):
            fn = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            blocking = ("wait", ".wait", ".join", ".recv", ".communicate", ".acquire")
            if any(fn.endswith(b) for b in blocking):
                has_timeout = any(kw.arg == "timeout" for kw in node.keywords) or bool(node.args)
                if not has_timeout:
                    findings.append("{}: blocking call {}() carries no timeout".format(name, fn))
            self.generic_visit(node)

    Visitor().visit(tree)
    if re.search(r"os\.path\.exists|Path\([^)]*\)\.exists", src) \
            and not re.search(r"returncode|\.wait\(|exit_code|status|terminal", src):
        findings.append("{}: completion derived from a sentinel file alone, no artefact-plus-terminal-"
                        "status fallback".format(name))
    for ln in src.splitlines():
        if re.search(r"\bos\.kill\b|subprocess.*kill|\.terminate\(|\bkill\b", ln):
            if not re.search(r"ledger|reconcile|for\s+\w+\s+in|consume|drain", src):
                findings.append("{}: a kill/terminate has no adjacent consumer enumeration or ledger "
                                "reconciliation: {!r}".format(name, ln.strip()[:80]))
                break
    return findings


def check_one(name, src, language, validator):
    """Returns (covered:bool, findings:list, cannot_evaluate:str|None)."""
    if language == "posix-shell":
        if not parse_shell_ok(src):
            return (False, [], "{}: declared POSIX shell script does not parse (unbalanced "
                    "do/done or if/fi)".format(name))
        return (True, lint_shell(name, src), None)
    if language == "python":
        try:
            findings = lint_python(name, src)
        except SyntaxError as exc:
            return (False, [], "{}: declared Python script does not parse ({})".format(name, exc))
        return (True, findings, None)
    if validator:
        return (True, [], None)   # deferred to the declared validator (recorded, passed)
    return (False, [], "{}: unsupported language {!r} with no declared validator "
            "(cannot-evaluate)".format(name, language))


def run(root, reg):
    declared = reg.get("coordination_scripts")
    if declared is None:
        print("no coordination_scripts declared: the gate is inert here (by design)")
        return 0, {"gate": "wait-discipline", "covered": [], "findings": []}
    if not isinstance(declared, list):
        # B7: a DECLARED-but-malformed coordination_scripts (e.g. a bare string) fails CLOSED
        # (cannot-evaluate), never a silent inert pass (chkfcl: a declared input the gate cannot read).
        return 2, {"gate": "wait-discipline", "covered": [], "findings": [],
                   "cannot_evaluate": ["coordination_scripts is declared but is not a list ({!r})"
                                       .format(type(declared).__name__)]}
    covered, findings, cannot = [], [], []
    for entry in declared:
        if isinstance(entry, dict):
            rel, language, validator = entry.get("path"), entry.get("language"), entry.get("validator")
        else:
            rel, language, validator = entry, None, None
        path = aiqt_hooks._orch_path(root, rel)
        if not path:
            cannot.append("a coordination_scripts entry has no usable path: {!r}".format(entry))
            continue
        try:
            src = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            cannot.append("{}: declared script is absent or unreadable ({})".format(rel, exc))
            continue
        # B7: a DECLARED validator must exist and be readable; a missing one is cannot-evaluate, never a
        # silent defer (the gate cannot claim to have covered a script via a validator that is not there).
        if validator:
            vpath = aiqt_hooks._orch_path(root, validator)
            if not vpath or not os.path.exists(vpath):
                cannot.append("{}: declared validator {!r} is absent (cannot-evaluate)".format(
                    rel, validator))
                continue
        lang = _language_for(path, language)
        ok, fnd, ce = check_one(rel, src, lang, validator)
        if ce:
            cannot.append(ce)
            continue
        if ok:
            covered.append(rel)
        findings.extend(fnd)
    result = {"gate": "wait-discipline", "covered": sorted(covered),
              "findings": findings, "cannot_evaluate": cannot}
    if cannot:
        return 2, result
    return (1 if findings else 0), result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--result-json", default=None)
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    root = str(repo_root())
    status, reg = aiqt_hooks._orch_registry(root)
    if status == "absent":
        print("no orchestration registry: wait-discipline gate is inert here")
        return 0
    if status != "ok":
        print("check_wait_discipline: registry unreadable ({}); cannot-evaluate".format(reg),
              file=sys.stderr)
        return 2
    code, result = run(root, reg)
    if a.result_json:
        Path(a.result_json).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    for c in result.get("cannot_evaluate", []):
        print("CANNOT-EVALUATE: " + c, file=sys.stderr)
    for f in result["findings"]:
        print("FINDING: " + f, file=sys.stderr)
    if code == 0:
        print("wait-discipline: {} declared script(s) covered and clean: {}".format(
            len(result["covered"]), ", ".join(result["covered"]) or "(none)"))
    return code


def self_test():
    fails = []
    reject = {
        "wd/unbounded-poll-loop": ("posix-shell", "while true; do sleep 5; done\n"),
        "wd/py-unbounded-poll": ("python", "import time\nwhile True:\n    time.sleep(5)\n"),
        "wd/timeout-reenters-same-loop":
            ("posix-shell", "while true; do\n  read -t 5 x || continue\ndone\n"),
        "wd/deadline-resetting-short-wait":
            ("posix-shell", "while true; do\n  deadline=$(( $(date +%s) + 5 ))\n  sleep 1\ndone\n"),
        "wd/blocking-wait-no-timeout": ("posix-shell", "run &\npid=$!\nwait \"$pid\"\n"),
        "wd/py-blocking-wait": ("python", "import subprocess\np = subprocess.Popen(['x'])\np.wait()\n"),
        "wd/sentinel-only-completion":
            ("posix-shell", "while [ ! -f \"$done\" ]; do sleep 1; done\necho complete\n"),
        "wd/kill-no-consumer-enum": ("posix-shell", "kill %1\n"),
    }
    for name, (lang, src) in reject.items():
        ok, fnd, ce = check_one(name, src, lang, None)
        if ce or not fnd:
            fails.append("{} should be REJECTED (findings), got ok={} ce={} findings={}".format(
                name, ok, ce, fnd))
    accept_src = ("attempt=0\nwhile [ $attempt -lt 10 ]; do\n"
                  "  attempt=$((attempt+1))\n"
                  "  if [ -f \"$art\" ] && grep -q '^status: done' \"$art\"; then exit 0; fi\n"
                  "  timeout 5 wait_for_event || true\ndone\nexit 1\n")
    ok, fnd, ce = check_one("wd/bounded-fail-loud", accept_src, "posix-shell", None)
    if ce or fnd:
        fails.append("wd/bounded-fail-loud should be ACCEPTED, got ce={} findings={}".format(ce, fnd))
    # fail-closed
    ok, fnd, ce = check_one("wd/unparseable-declared-script", "def broken(:\n", "python", None)
    if ok or not ce:
        fails.append("unparseable python should be cannot-evaluate/fail-closed")
    ok, fnd, ce = check_one("wd/unsupported-language-cannot-evaluate", "x", "ruby", None)
    if ok or not ce:
        fails.append("unsupported language with no validator should be cannot-evaluate")
    ok, fnd, ce = check_one("wd/unsupported-with-validator", "x", "ruby", "tools/ruby_wait_lint.rb")
    if not ok or ce:
        fails.append("unsupported language WITH a declared validator should defer (pass)")
    # B7 wd/until-false-unbounded: `until false` is an unbounded poll loop (the regex-gap gemini flagged)
    ok, fnd, ce = check_one("wd/until-false-unbounded", "until false; do sleep 5; done\n",
                            "posix-shell", None)
    if ce or not fnd:
        fails.append("`until false` with sleep and no bound must be REJECTED (wd/until-false-unbounded)")
    # B7 wd/unterminated-quote-fails-closed: an unbalanced quote reads as unparseable (fail-closed)
    ok, fnd, ce = check_one("wd/unterminated-quote", "echo 'unterminated\n", "posix-shell", None)
    if ok or not ce:
        fails.append("an unterminated quote must be cannot-evaluate/fail-closed (wd/unterminated-quote)")
    # absent declared script + machine-result set enumeration via run()
    import tempfile
    with tempfile.TemporaryDirectory(prefix="aiqt-wd-") as tmp:
        tmp = Path(tmp)
        (tmp / "coord.sh").write_text("attempt=0\nwhile [ $attempt -lt 3 ]; do attempt=$((attempt+1)); "
                                      "if [ -f a ] && test $? -eq 0; then exit 0; fi; done\nexit 1\n",
                                      encoding="utf-8")
        reg = {"coordination_scripts": ["coord.sh", "missing.sh"]}
        code, result = run(str(tmp), reg)
        if code != 2 or not any("missing.sh" in c for c in result["cannot_evaluate"]):
            fails.append("an absent declared script must fail closed naming it (wd/absent-declared-script)")
        if result["covered"] != ["coord.sh"]:
            fails.append("machine result must enumerate exactly the covered set "
                         "(wd/machine-result-enumerates-covered-set), got {}".format(result["covered"]))
        # B7 wd/malformed-declared-fails-closed: a declared-but-not-a-list coordination_scripts is
        # cannot-evaluate (exit 2), never a silent inert pass
        code2, _r2 = run(str(tmp), {"coordination_scripts": "missing.sh"})
        if code2 != 2:
            fails.append("a declared-but-malformed coordination_scripts must fail closed (exit 2)")
        # B7 wd/missing-validator-fails-closed: a declared validator that is absent is cannot-evaluate,
        # never a silent defer/pass
        reg3 = {"coordination_scripts": [{"path": "coord.rb", "language": "ruby",
                                          "validator": "tools/does-not-exist.rb"}]}
        (tmp / "coord.rb").write_text("puts 'x'\n", encoding="utf-8")
        code3, result3 = run(str(tmp), reg3)
        if code3 != 2 or not any("validator" in c for c in result3.get("cannot_evaluate", [])):
            fails.append("a declared-but-absent validator must fail closed (wd/missing-validator-fails-closed)")
    if fails:
        for f in fails:
            print("SELF-TEST FAIL: " + f, file=sys.stderr)
        return 1
    print("self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
