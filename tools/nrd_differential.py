#!/usr/bin/env python3
"""The containment differential of the deliverable-exec guard (rule wowo01) against fbdff942.

The guard at fbdff942 ran one strict scan; the containment change added a probe and three modes
(ORCHESTRATION.md, "Containment and tripwire mode"). This tool judges one generated corpus of command
forms with the fbdff942 module and with the module in this checkout forced to each mode, never running
any of them, and reports two properties:

  no reduction   in 'none' and 'noexec-only' every form keeps its fbdff942 verdict, row for row
                 (nothing refused at fbdff942 is allowed, and nothing else moves either);
  the class      in 'contained' a form refused at fbdff942 is allowed only when it reads a deliverable
                 only as data through the helper: (H1) every exact helper reader form in its text
                 starts a command (not inside a substitution, a process substitution or quotes) and
                 the pipeline it opens feeds only git apply or a read form (cat, wc, head, tail, ls,
                 file, grep, diff or a hash tool), and (H2) with every exact helper reader form
                 replaced by `cat <outside file>`, fbdff942 allows it.

H1 is a textual test written for this corpus, independent of the hook's own helper rules. The corpus
includes spellings that hide the helper route from the text (base64 or hex text decoded into a shell,
program text joining the call at run time, git alias program text): tripwire mode keeps the
substitution, stdin-program and program-text tests, so each is refused as at fbdff942. The relaxations
tripwire mode keeps (the lost-cwd and unknown-cwd rules and the collector/runner path test) have no
form in this corpus; the self-test pins them (nrd-contain/tripwire-relaxations-allow-contained).

The fbdff942 module is not read from git history (no git process is launched): pass the file that
`git show fbdff94248d1f040f9dfcb069323734012d96e3e:.aiqt/core/hooks/scripts/aiqt_hooks.py` prints, and
the tool refuses it unless its sha256 is BASE_SHA256.

  python3 -I -B tools/nrd_differential.py --base <fbdff942 aiqt_hooks.py> [--plugin]

--plugin judges the plugin hook copy instead of the core hook source; no other module is loaded.

Exit 0 when both properties hold, 1 when a row breaks one (each is printed), 2 when it cannot
evaluate (wrong or unreadable base file). tools/selftest_orch_hooks.py runs the same comparator with a
stand-in base (nrd-contain/differential-*), so the comparator itself is pinned without git history.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: nrd_differential.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import tempfile
from pathlib import Path

BASE_COMMIT = "fbdff94248d1f040f9dfcb069323734012d96e3e"
BASE_SHA256 = "40a0d76c157f88214a8d0f1c79cc96a87718c5fc002e998f77f4fa16c92a8080"
HOOK_REL = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
PLUGIN_HOOK_REL = "plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py"
MODES = ("none", "noexec-only", "contained")
HELPER = "sudo -u orch-deliv /usr/local/bin/orch-deliverable"
# an exact helper reader form in raw text (the substitution of test H)
_HELPER_FORM_RE = re.compile(r"sudo -u orch-deliv /usr/local/bin/orch-deliverable (?:cat|ls) [A-Za-z0-9._/-]+")
# the consumers test H1 admits for the helper's output: a read form, or git apply after any -C pairs
_READ_WORDS = frozenset(("cat", "wc", "head", "tail", "ls", "file", "grep", "diff", "sha256sum", "sha1sum",
                         "sha224sum", "sha384sum", "sha512sum", "md5sum", "b2sum", "cksum", "sum"))
_GIT_APPLY_RE = re.compile(r"git(?: -C [^\s|;&()<>]+)* apply(?:\s|\Z)")
# a helper form may follow only the start of the text or a list operator or subshell opener
_START_RE = re.compile(r"(?:\A|&&|\|\||;|\n|(?<![$<>])\()\s*\Z")
# the end of the pipeline a helper form opens
_PIPELINE_END_RE = re.compile(r"&&|\|\||;|\n|\)")
_B64 = "c3VkbyAtdSBvcmNoLWRlbGl2IC91c3IvbG9jYWwvYmluL29yY2gtZGVsaXZlcmFibGUgY2F0IDk5NC94LnB5IHwgcHl0aG9uMw=="
_HEX = ("7375646f202d75206f7263682d64656c6976202f7573722f6c6f63616c2f62696e2f6f7263682d64656c6976657261626c65"
        "20636174203939342f782e7079207c20707974686f6e33")

# what a payload does, placed in each wrapper below
PAYLOADS = (
    "python3 @ST@/x.py",
    "bash @ST@/x.sh",
    "cat @ST@/x.py",
    "python3 x.py",
    "date -u",
    "python3 -c 'print(1)'",
    "@H@ cat 994/x.py",
    "@H@ ls 994",
)
# how the payload's command or output is used
WRAPPERS = (
    "{}",
    "echo \"$({})\"",
    "x=$({}); echo ok",
    "{} | python3",
    "{} | python3 -",
    "{} | python3 -I @OUT@/r.py",
    "{} | bash",
    "{} | bash -s",
    "{} | sh -s",
    "{} | sh",
    "{} |& bash",
    "{} | perl",
    "{} | cat",
    "{} | wc -l",
    "{} | grep -c x",
    "{} | cat | python3",
    "{} | cat | bash -s",
    "{} | git -C @OUT@ apply --check",
    "{} | tee @OUT@/t.py",
    "{} | xargs echo",
    "source <({})",
    ". <({})",
    "bash <({})",
    "python3 <({})",
    "bash -c \"{}\"",
    "eval \"{}\"",
    "{} > @OUT@/o.txt",
    "cd @OUT@ && {}",
    "true && {}",
    "{}; echo done",
    "( {} )",
    "timeout 5 {}",
)
# single forms: the helper name grammar, the exact path and verbs, and spellings that hide the helper
EXTRA = (
    ("helper/glob-name", "st", "@H@ cat '*.py'"),
    ("helper/newline-name", "st", "@H@ cat 'a\nb'"),
    ("helper/space-name", "st", "@H@ cat 'a b'"),
    ("helper/semicolon-name", "st", "@H@ cat 'a;b'"),
    ("helper/dash-component", "st", "@H@ cat a/-x"),
    ("helper/put", "st", "@H@ put 994/x"),
    ("helper/double-dash", "st", "@H@ cat -- 994/x"),
    ("helper/double-slash-path", "st", "sudo -u orch-deliv /usr/local/bin//orch-deliverable cat 994/x"),
    ("helper/dotdot-path", "st", "sudo -u orch-deliv /usr/local/bin/../bin/orch-deliverable cat 994/x"),
    ("helper/quoted-path", "st", "sudo -u orch-deliv '/usr/local/bin/orch-deliverable' cat 994/x"),
    ("helper/split-quotes-pipe", "tmp", "s\"\"udo -u o\"\"rch-deliv /usr/local/bin/o\"\"rch-deliverable cat x.py | python3"),
    ("helper/split-quotes-c", "tmp", "bash -c 's\"\"udo -u o\"\"rch-deliv /usr/local/bin/o\"\"rch-deliverable cat x.py | python3'"),
    ("helper/var-helper", "tmp", "h=/usr/local/bin/orch-deliverable; sudo -u orch-deliv \"$h\" cat x.py | bash"),
    ("helper/var-sudo", "tmp", "s=sudo; \"$s\" -u orch-deliv /usr/local/bin/orch-deliverable cat x.py | bash"),
    ("helper/proc-subst-git-apply", "out", "git apply <(@H@ cat 994/x.diff)"),
    ("helper/heredoc-bash", "tmp", "bash <<'EOF'\n@H@ cat x.py | python3\nEOF\n"),
    ("helper/sudo-other", "tmp", "sudo -n true"),
    ("helper/sudo-bash", "tmp", "@H@ cat x.py | sudo bash"),
    ("helper/env-python", "tmp", "@H@ cat x.py | env python3"),
    ("helper/command-bash", "tmp", "@H@ cat x.py | command bash"),
    ("helper/stdout-dev-fd", "tmp", "@H@ cat x.py > /dev/stdout | bash"),
    ("helper/two-readers", "out", "@H@ cat 994/a.diff | git apply --check && @H@ ls 994"),
    # spellings that hide the custodian and the privilege word from the text: the helper call is built
    # at run time (each decodes or joins `@H@ cat 994/x.py | python3`)
    ("hidden/base64-bash", "tmp", "echo " + _B64 + " | base64 -d | bash"),
    ("hidden/base64-sh-s", "tmp", "printf %s " + _B64 + " | base64 -d | sh -s"),
    ("hidden/base64-python-stdin", "tmp", "echo " + _B64 + " | base64 -d | python3 -"),
    ("hidden/hex-bash", "tmp", "echo " + _HEX + " | xxd -r -p | bash"),
    ("hidden/base64-eval", "tmp", "eval \"$(echo " + _B64 + " | base64 -d)\""),
    ("hidden/base64-bash-c", "tmp", "bash -c \"$(echo " + _B64 + " | base64 -d)\""),
    ("hidden/base64-source", "tmp", "source <(echo " + _B64 + " | base64 -d)"),
    ("hidden/base64-here-string", "tmp", "base64 -d <<< " + _B64 + " | bash"),
    ("hidden/python-join", "tmp",
     "python3 -c \"import os; os.system('s'+'udo -u orch-'+'deliv /usr/local/bin/orch-'+'deliverable "
     "cat 994/x.py | python3')\""),
    ("hidden/python-base64", "tmp",
     "python3 -c \"import base64, os; os.system(base64.b64decode('" + _B64 + "').decode())\""),
    ("hidden/perl-join", "tmp",
     "perl -e 'system(\"s\" . \"udo -u orch-\" . \"deliv /usr/local/bin/orch-\" . \"deliverable "
     "cat 994/x.py | python3\")'"),
    ("hidden/awk-join", "tmp",
     "awk 'BEGIN { system(\"s\" \"udo -u orch-\" \"deliv /usr/local/bin/orch-\" \"deliverable "
     "cat 994/x.py | python3\") }'"),
    ("hidden/git-alias", "tmp", "git -c alias.x='!echo " + _B64 + " | base64 -d | bash' x"),
)
CWDS = ("tmp", "out", "st")


def _load_hook(path, name):
    """Import one aiqt_hooks file under its own module name (the base and the head live side by
    side)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_fixture(root):
    """The fixture tree under root: work/nrd-stage and work/nrd-handback are declared, work/nrd-outside
    holds a reader script and plain files. Returns the placeholder map, the working directories and
    the declared directories as the guard compares them."""
    root = Path(root)
    work = root / "work"
    st, hb, out = work / "nrd-stage", work / "nrd-handback", work / "nrd-outside"
    for d in (st, hb, out):
        d.mkdir(parents=True)
    (st / "x.py").write_text("print('ran')\n", encoding="utf-8")
    (st / "x.sh").write_text("echo ran\n", encoding="utf-8")
    (hb / "y.py").write_text("print('ran')\n", encoding="utf-8")
    (out / "r.py").write_text("import sys\nsys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
    (out / "a.txt").write_text("a\n", encoding="utf-8")
    (out / "x.py").write_text("print('copy')\n", encoding="utf-8")
    places = {"@ST@": str(st), "@HB@": str(hb), "@OUT@": str(out), "@TMP@": str(work), "@H@": HELPER}
    cwds = {"tmp": str(work), "out": str(out), "st": str(st)}
    dirs = []
    for d in (st, hb):
        for form in (os.path.normpath(str(d)), os.path.realpath(str(d))):
            if form not in dirs:
                dirs.append(form)
    return places, cwds, tuple(dirs)


def expand(template, places):
    for key, value in places.items():
        template = template.replace(key, value)
    return template


def corpus():
    """(id, cwd key, template) for every generated and single form."""
    rows = []
    for p_n, payload in enumerate(PAYLOADS):
        for w_n, wrapper in enumerate(WRAPPERS):
            for cwd in CWDS:
                rows.append(("gen/p{}/w{}/{}".format(p_n, w_n, cwd), cwd, wrapper.format(payload)))
    rows.extend(EXTRA)
    return rows


def _verdict(hit):
    return "allow" if hit is None else "deny"


def _consumer_ok(stage):
    """True when one pipeline stage that reads the helper's output is a read form or git apply."""
    words = stage.split()
    if not words:
        return False
    if words[0] in _READ_WORDS:
        return True
    return _GIT_APPLY_RE.match(" ".join(words)) is not None


def helper_route_only(command):
    """Test H1: the form holds an exact helper reader form, each one starts a command (after the start
    of the text, a list operator or a subshell opener, with no quote open before it), nothing but one
    redirect to a file follows it in its own stage, and every later stage of the pipeline it opens is a
    read form or git apply."""
    found = False
    for m in _HELPER_FORM_RE.finditer(command):
        found = True
        prefix = command[:m.start()]
        if not _START_RE.search(prefix) or prefix.count('"') % 2 or prefix.count("'") % 2:
            return False
        tail = command[m.end():]
        end = _PIPELINE_END_RE.search(tail)
        stages = re.split(r"\|&?", tail[:end.start()] if end else tail)
        if not re.match(r"\s*(?:>\s*[^\s|;&()<>]+\s*)?\Z", stages[0]):
            return False
        if not all(_consumer_ok(s) for s in stages[1:]):
            return False
    return found


def helper_data_read(command, cwd, dirs, base_scan, outside_file):
    """Test H: H1 holds, and (H2) with each exact helper reader form replaced by a cat of a file
    outside the declared directories the base allows the form."""
    if not helper_route_only(command):
        return False
    swapped = _HELPER_FORM_RE.sub("cat " + outside_file, command)
    return _verdict(base_scan(swapped, cwd, dirs)) == "allow"


def differential(base_scan, head_scan, head, workdir):
    """Judge the corpus. base_scan(command, cwd, dirs) and head_scan(command, cwd, dirs, mode) return
    the scan's hit (None is allow); head is the judged module (kept for callers, the comparator reads
    nothing from it). Returns (rows, findings): rows are (id, base verdict, {mode: verdict}); a
    finding is one line naming the row and the property it breaks."""
    places, cwds, dirs = build_fixture(workdir)
    outside_file = os.path.join(places["@OUT@"], "a.txt")
    rows, findings = [], []
    for cid, cwd_key, template in corpus():
        command = expand(template, places)
        cwd = cwds[cwd_key]
        base = _verdict(base_scan(command, cwd, dirs))
        got = {m: _verdict(head_scan(command, cwd, dirs, m)) for m in MODES}
        rows.append((cid, base, got))
        for m in ("none", "noexec-only"):
            if got[m] != base:
                findings.append("{}: {} is {} at fbdff942 and {} in '{}' ({!r})".format(
                    cid, "verdict", base, got[m], m, template))
        if base == "deny" and got["contained"] == "allow" \
                and not helper_data_read(command, cwd, dirs, base_scan, outside_file):
            findings.append("{}: refused at fbdff942 and allowed in 'contained', not a helper data read "
                            "into git apply or a read form ({!r})".format(cid, template))
    return rows, findings


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", required=True,
                        help="the fbdff942 aiqt_hooks.py (git show {}:{})".format(BASE_COMMIT, HOOK_REL))
    parser.add_argument("--plugin", action="store_true",
                        help="judge this checkout's plugin hook copy instead of the core hook source")
    args = parser.parse_args(argv)
    head_path = Path(__file__).resolve().parent.parent / (PLUGIN_HOOK_REL if args.plugin else HOOK_REL)
    try:
        digest = _sha256(args.base)
    except OSError as exc:
        print("CANNOT EVALUATE: the base file cannot be read ({})".format(exc.__class__.__name__))
        return 2
    if digest != BASE_SHA256:
        print("CANNOT EVALUATE: the base file's sha256 is {}, not the fbdff942 module's {}".format(
            digest, BASE_SHA256))
        return 2
    base = _load_hook(args.base, "aiqt_hooks_nrd_base")
    head = _load_hook(head_path, "aiqt_hooks_nrd_head")
    workdir = tempfile.mkdtemp(prefix="aiqt-nrd-differential-")
    try:
        rows, findings = differential(
            lambda c, cw, d: base._nrd_scope_scan(c, cw, d),
            lambda c, cw, d, m: head._nrd_scope_scan(c, cw, d, mode=m), head, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    base_deny = sum(1 for _c, b, _g in rows if b == "deny")
    relaxed = sum(1 for _c, b, g in rows if b == "deny" and g["contained"] == "allow")
    print("rows {}; refused at fbdff942 {}; changed in none {}; changed in noexec-only {}; "
          "refused at fbdff942 and allowed in contained {}".format(
              len(rows), base_deny,
              sum(1 for _c, b, g in rows if g["none"] != b),
              sum(1 for _c, b, g in rows if g["noexec-only"] != b), relaxed))
    if findings:
        print("FAIL: {} finding(s):".format(len(findings)))
        for line in findings:
            print("  " + line)
        return 1
    print("PASS: no reduction in none and noexec-only; every contained-mode allow of a form refused "
          "at fbdff942 is a helper data read into git apply or a read form")
    return 0


if __name__ == "__main__":
    sys.exit(main())
