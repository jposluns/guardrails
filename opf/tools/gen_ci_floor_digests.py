#!/usr/bin/env python3
"""Generate the OPF CI floor digest region of opf/tools/_opf_adopt_complete.py.

The adoption completion evaluator (opf/tools/_opf_adopt_complete.py) authenticates the pack's shipped CI
floor, the GitHub Actions template opf/enforcement/ci/github-actions.yml and the recipe it runs,
opf/enforcement/ci/opf-ci.sh, against sha256 constants embedded in a sentinel-delimited generated region
(spec 14.1 check 4). This generator renders exactly that region from exactly those two files, so an edit to
either that is not regenerated here fails --check, and a reference file replaced in a working tree after the
fact fails the evaluator closed. It belongs to the OPF tooling and uses only the standard library.

  gen_ci_floor_digests.py              rewrite the generated region
  gen_ci_floor_digests.py --check      exit 1 on drift; exit 2 when the region cannot be evaluated: a CI
                                       floor file or the target that is absent or unreadable, a CI floor
                                       file that is a symbolic link, a BEGIN or END sentinel that is
                                       missing, duplicated or out of order, or a directory above the
                                       target that cannot be read as one inside the repository
  gen_ci_floor_digests.py --self-test  run each of those cases end to end over synthetic trees, and show
                                       each going red under a flip that removes the handling it guards

The region is bounded by two sentinel lines; only the text from BEGIN through END is rewritten, and the
target must hold exactly one BEGIN and exactly one END, BEGIN first, so a mangled, removed or duplicated
region fails closed rather than gaining a second copy or splicing only the first of several. Deterministic:
one bare-hex sha256 constant per file, in CI_FLOOR_SOURCES order, rendered with repr(), so a second run
makes no change. Nothing is written unless the region renders.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_ci_floor_digests.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import stat
from pathlib import Path

# The CI floor files, the digest constant each one fills, and the evaluator they are embedded in, all
# repository-root-relative.
CI_FLOOR_SOURCES = (("_SHIPPED_TEMPLATE_SHA256", "opf/enforcement/ci/github-actions.yml"),
                    ("_SHIPPED_RECIPE_SHA256", "opf/enforcement/ci/opf-ci.sh"))
TARGET_REL = "opf/tools/_opf_adopt_complete.py"
BEGIN = ("# BEGIN generated OPF CI floor digests (source: opf/enforcement/ci/; regenerate with "
         "opf/tools/gen_ci_floor_digests.py)")
END = "# END generated OPF CI floor digests"

# Declares this generator's output for the generated-source registry (tools/gen_gensrc.py reads it
# statically); additive metadata only. The target is a generated block inside the hand-authored evaluator.
GENSRC_OUTPUTS = (
    {"target": "opf/tools/_opf_adopt_complete.py", "kind": "block",
     "sources": ("opf/enforcement/ci/github-actions.yml", "opf/enforcement/ci/opf-ci.sh"),
     "regenerate": "python3 opf/tools/gen_ci_floor_digests.py"},
)


# The directory probe below is copied from tools/_standards.py (dir_or_absent, dir_chain_or_absent) and
# tools/_gen_common.py (target_dir_present), so the OPF tooling carries it without importing from tools/.
NOT_A_DIRECTORY = "is not a directory"
LINK_DANGLING = "is a symbolic link whose target does not exist"
LINK_NOT_A_DIRECTORY = "is a symbolic link whose target is not a directory"
LINK_OUTSIDE_ROOT = "is a symbolic link whose target resolves outside the repository"


def _inside(real, real_root):
    """True when the resolved path `real` is the resolved root `real_root` or under it."""
    try:
        return os.path.commonpath([real, real_root]) == real_root
    except ValueError:
        return False


def dir_or_absent(path, root):
    """(True, None) for a directory, (False, None) when nothing is there, and (False, reason) when something
    is there that cannot be read as that directory under root: a non-directory, or a symbolic link that is
    dangling, whose target is not a directory or resolves outside root. A link to a directory inside root is
    a directory. Raises OSError when the path cannot be examined other than by absence."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return (False, None)
    if not stat.S_ISLNK(st.st_mode):
        if not stat.S_ISDIR(st.st_mode):
            return (False, NOT_A_DIRECTORY)
        return (True, None)
    try:
        real = os.path.realpath(path, strict=True)
    except FileNotFoundError:
        return (False, LINK_DANGLING)
    if not _inside(real, os.path.realpath(root)):
        return (False, LINK_OUTSIDE_ROOT)
    if not stat.S_ISDIR(os.lstat(real).st_mode):
        return (False, LINK_NOT_A_DIRECTORY)
    return (True, None)


def dir_chain_or_absent(path, root):
    """dir_or_absent for every directory component of path under root, from the first one below root down
    to path itself, so a symbolic link at any depth is judged. A path not lexically under root, or holding a
    .. component, is (False, reason) as well. A link retargeted between this probe and the later read (by
    another writer at the same moment) is not detected."""
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        return (False, "is not under the repository root {}".format(root))
    if ".." in parts:
        return (False, "holds a .. component")
    current = Path(root)
    for depth, part in enumerate(parts, 1):
        current = current / part
        present, problem = dir_or_absent(current, root)
        if problem is not None:
            if depth < len(parts):
                problem = "has a directory component {} that {}".format(current, problem)
            return (False, problem)
        if not present:
            return (False, None)
    return (True, None)


def target_dir_present(path, root):
    """Judge the directory holding path under root before the first read or write of path: one that is
    there but cannot be read as a directory inside the repository, or cannot be examined, exits 2 with a
    message, so nothing is read through it, reported clean from it or written to it."""
    parent = Path(path).parent
    try:
        present, problem = dir_chain_or_absent(parent, Path(root))
    except OSError as exc:
        present, problem = False, "cannot be examined ({})".format(exc)
    if problem is not None:
        print("error: {} {} (cannot evaluate); fail-closed".format(parent, problem), file=sys.stderr)
        raise SystemExit(2)
    return present


def repo_root(start=None):
    p = Path(start or __file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return anc
    return Path.cwd()


def render_region(root):
    """The region, sentinels included: one bare-hex sha256 constant per CI floor file, in CI_FLOOR_SOURCES
    order. A symlinked file raises ValueError (the digest must be of the file itself); an absent or
    unreadable one raises OSError."""
    lines = [BEGIN]
    for name, rel in CI_FLOOR_SOURCES:
        path = Path(root) / rel
        if path.is_symlink():
            raise ValueError("{} is a symlink; the CI floor digest is taken of the file itself".format(rel))
        lines.append("{} = {}".format(name, repr(hashlib.sha256(path.read_bytes()).hexdigest())))
    lines.append(END)
    return "\n".join(lines)


def _splice(text, region):
    """Replace BEGIN..END (inclusive) in text with region. Raises ValueError when either sentinel is not
    there exactly once, or END comes before BEGIN, so a mangled, removed or multi-region target fails
    closed rather than gaining a second copy, writing nothing, or splicing only the first region."""
    begin_count = text.count(BEGIN)
    if begin_count != 1:
        raise ValueError("expected exactly one BEGIN sentinel in {}, found {}".format(TARGET_REL, begin_count))
    end_count = text.count(END)
    if end_count != 1:
        raise ValueError("expected exactly one END sentinel in {}, found {}".format(TARGET_REL, end_count))
    i = text.find(BEGIN)
    j = text.find(END, i)
    if j == -1:
        raise ValueError("END sentinel not found after BEGIN in {}".format(TARGET_REL))
    return text[:i] + region + text[j + len(END):]


def run(root, check):
    """Render the region into the target, or (check mode) report drift: 0 clean or written, 1 drift in
    check mode, 2 when it cannot be evaluated or written."""
    root = Path(root)
    target = root / TARGET_REL
    target_dir_present(target, root)
    try:
        region = render_region(root)
        current = target.read_text(encoding="utf-8")
        desired = _splice(current, region)
    except (OSError, ValueError) as exc:
        print("error: {}; fail-closed (cannot evaluate)".format(exc), file=sys.stderr)
        return 2
    if current == desired:
        return 0
    if check:
        print("drift: the generated region in {} is out of date".format(TARGET_REL))
        print("run opf/tools/gen_ci_floor_digests.py to regenerate")
        return 1
    try:
        target.write_text(desired, encoding="utf-8")
    except OSError as exc:
        print("error: cannot write {} ({}); fail-closed".format(target, exc), file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------------------------------------------------
# Self-test. Each case builds a synthetic tree holding the two CI floor files, the target with a stale
# region, an empty .git marker and a copy of this tool's source (genuine or flipped) at bin/, and launches
# that copy as a child, so its repository root is the synthetic tree and its exit status is the real one.

_STALE = "# head\n{}\nstale\n{}\n# tail\n".format(BEGIN, END)
_LAUNCH_TIMEOUT_S = 120
_PAST_NS = 10 ** 9


def _build(base, source):
    (base / ".git").mkdir(parents=True)
    for _name, rel in CI_FLOOR_SOURCES:
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("# self-test CI floor file {}\n".format(rel), encoding="utf-8")
    (base / TARGET_REL).parent.mkdir(parents=True, exist_ok=True)
    (base / TARGET_REL).write_text(_STALE, encoding="utf-8")
    script = base / "bin" / "gen_ci_floor_digests.py"
    script.parent.mkdir(parents=True)
    script.write_text(source, encoding="utf-8")
    return script


def _launch(script, *args):
    import subprocess
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(script), *args], capture_output=True,
                              text=True, timeout=_LAUNCH_TIMEOUT_S, cwd=str(script.parent))
    except (OSError, subprocess.SubprocessError) as exc:
        return "not run ({})".format(exc)
    return proc.returncode


def _expected_region(base):
    lines = [BEGIN]
    for name, rel in CI_FLOOR_SOURCES:
        lines.append("{} = {}".format(name, repr(hashlib.sha256((base / rel).read_bytes()).hexdigest())))
    return "\n".join(lines + [END])


def _both(script, want, target, failures, label):
    """--check and a regeneration must each exit `want` with the target's bytes unchanged."""
    before = target.read_bytes()
    got = (_launch(script, "--check"), _launch(script))
    if got != (want, want) or target.read_bytes() != before:
        failures.append("{}: expected --check and write exit {} with the target unchanged, got exit {}, "
                        "changed {}".format(label, want, got, target.read_bytes() != before))


def _case_generate(base, script, failures):
    target = base / TARGET_REL
    first = _launch(script)
    want = "# head\n{}\n# tail\n".format(_expected_region(base))
    if first != 0 or target.read_text(encoding="utf-8") != want:
        failures.append("generate: expected exit 0 and the region holding the sha256 of each CI floor file, "
                        "got exit {}".format(first))
        return
    os.utime(target, ns=(_PAST_NS, _PAST_NS))
    again = _launch(script)
    if again != 0 or os.stat(target).st_mtime_ns != _PAST_NS or target.read_text(encoding="utf-8") != want:
        failures.append("generate: a second run expected exit 0 with nothing written, got exit {}".format(again))
    check = _launch(script, "--check")
    if check != 0:
        failures.append("generate: --check over a fresh region expected exit 0, got {}".format(check))


def _case_drift(base, script, failures):
    if _launch(script) != 0:
        failures.append("drift: the tree did not generate")
        return
    target = base / TARGET_REL
    before = target.read_bytes()
    (base / CI_FLOOR_SOURCES[1][1]).write_text("# edited recipe\n", encoding="utf-8")
    got = _launch(script, "--check")
    if got != 1 or target.read_bytes() != before:
        failures.append("drift: an edited CI floor file expected --check exit 1 with the target unchanged, "
                        "got exit {}".format(got))
        return
    got = (_launch(script), _launch(script, "--check"))
    if got != (0, 0):
        failures.append("drift: regenerating then --check expected exit (0, 0), got {}".format(got))


def _case_unreadable(base, script, failures):
    if _launch(script) != 0:
        failures.append("unreadable: the tree did not generate")
        return
    target = base / TARGET_REL
    template = base / CI_FLOOR_SOURCES[0][1]
    template.unlink()
    _both(script, 2, target, failures, "unreadable: an absent CI floor file")
    template.mkdir()
    _both(script, 2, target, failures, "unreadable: a CI floor file that is a directory")


def _case_unreadable_target(base, script, failures):
    (base / TARGET_REL).unlink()
    got = (_launch(script, "--check"), _launch(script))
    if got != (2, 2) or (base / TARGET_REL).exists():
        failures.append("unreadable target: an absent target expected --check and write exit 2 with nothing "
                        "created, got exit {}".format(got))


def _case_symlink(base, script, failures):
    if _launch(script) != 0:
        failures.append("symlink: the tree did not generate")
        return
    recipe = base / CI_FLOOR_SOURCES[1][1]
    held = base / "held-recipe.sh"
    held.write_bytes(recipe.read_bytes())
    recipe.unlink()
    recipe.symlink_to(held)
    _both(script, 2, base / TARGET_REL, failures, "symlink: a CI floor file symlinked to its own bytes")


def _sentinel_case(label, text):
    def case(base, script, failures):
        target = base / TARGET_REL
        target.write_text(text, encoding="utf-8")
        _both(script, 2, target, failures, label)
    return case


_case_duplicate = _sentinel_case("duplicate: two BEGIN..END regions", _STALE + _STALE)
_case_order = _sentinel_case("order: END before BEGIN", "# head\n{}\nstale\n{}\n# tail\n".format(END, BEGIN))
_case_missing_end = _sentinel_case("missing END", "# head\n{}\nstale\n# tail\n".format(BEGIN))
_case_missing_begin = _sentinel_case("missing BEGIN", "# head\nstale\n{}\n# tail\n".format(END))


def _case_link_outside(base, script, failures):
    import shutil
    if _launch(script) != 0:
        failures.append("link outside: the tree did not generate")
        return
    link = base / "opf" / "tools"
    held = base.parent / (base.name + "-held-tools")
    shutil.move(str(link), str(held))
    link.symlink_to(held)
    target = held / Path(TARGET_REL).name
    target.write_text(_STALE, encoding="utf-8")
    os.utime(target, ns=(_PAST_NS, _PAST_NS))
    got = (_launch(script, "--check"), _launch(script))
    if got != (2, 2) or os.stat(target).st_mtime_ns != _PAST_NS:
        failures.append("link outside: opf/tools symlinked outside the root expected --check and write exit 2 "
                        "with the target there untouched, got exit {}".format(got))


def _case_link_inside(base, script, failures):
    import shutil
    if _launch(script) != 0:
        failures.append("link inside: the tree did not generate")
        return
    link = base / "opf" / "tools"
    held = base / "shared" / "tools"
    held.parent.mkdir()
    shutil.move(str(link), str(held))
    link.symlink_to(os.path.relpath(held, link.parent))
    target = held / Path(TARGET_REL).name
    target.write_text(_STALE, encoding="utf-8")
    got = (_launch(script), _launch(script, "--check"))
    if got != (0, 0) or target.read_text(encoding="utf-8") == _STALE:
        failures.append("link inside: opf/tools symlinked inside the root expected write and --check exit 0 with "
                        "the region rewritten there, got exit {}".format(got))


CASES = (("generate", _case_generate), ("drift", _case_drift), ("unreadable", _case_unreadable),
         ("unreadable-target", _case_unreadable_target), ("symlink", _case_symlink),
         ("duplicate", _case_duplicate), ("order", _case_order), ("missing-end", _case_missing_end),
         ("missing-begin", _case_missing_begin), ("link-outside", _case_link_outside),
         ("link-inside", _case_link_inside))

# Each flip removes one piece of handling from the code above the self-test (old text, replaced by new
# text, which must occur there exactly once) and names the cases that handling guards: each of those cases
# must go red under the flip, so the case is shown to depend on it.
FLIPS = (
    ("drift comparison removed", "    if current == desired:\n        return 0\n", "    return 0\n",
     ("drift",)),
    ("read errors not caught", "    except (OSError, ValueError) as exc:\n", "    except ValueError as exc:\n",
     ("unreadable", "unreadable-target")),
    ("symlink refusal removed",
     "        if path.is_symlink():\n            raise ValueError(\"{} is a symlink; the CI floor digest is "
     "taken of the file itself\".format(rel))\n", "", ("symlink",)),
    ("sentinel count guard removed",
     "    if begin_count != 1:\n|    if end_count != 1:\n", "    if False:\n", ("duplicate",)),
    ("sentinel order guard removed", "    if j == -1:\n", "    if False:\n", ("order",)),
    ("every sentinel guard removed",
     "    if begin_count != 1:\n|    if end_count != 1:\n|    if j == -1:\n", "    if False:\n",
     ("missing-end", "missing-begin")),
    ("containment probe removed", "    target_dir_present(target, root)\n", "", ("link-outside",)),
)
_MARK = "\n# Self" + "-test. Each case builds a synthetic tree"


def _flipped(source, old, new):
    """source with each '|'-separated old text replaced by new above the self-test, or None when one of them
    does not occur there exactly once (the flip could not be applied, which the self-test reports)."""
    head, sep, tail = source.partition(_MARK)
    if not sep:
        return None
    for piece in old.split("|"):
        if head.count(piece) != 1:
            return None
        head = head.replace(piece, new)
    return head + sep + tail


def _run_cases(tmp, label, source, names):
    """Run the named cases over fresh trees with source as the tool; return {name: failures}."""
    results = {}
    for name, case in CASES:
        if name not in names:
            continue
        base = Path(tmp) / "{}-{}".format(label, name)
        script = _build(base, source)
        failures = []
        case(base, script, failures)
        results[name] = failures
    return results


def self_test_main():
    import shutil
    import tempfile
    try:
        source = Path(__file__).read_text(encoding="utf-8")
        tmp = Path(tempfile.mkdtemp(prefix="opf-gen-ci-floor-digests-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: cannot read this tool or make a temporary directory: {}".format(exc),
              file=sys.stderr)
        return 2
    failures = []
    try:
        genuine = _run_cases(tmp, "genuine", source, {name for name, _case in CASES})
        for name, _case in CASES:
            failures.extend("{}: {}".format(name, f) for f in genuine[name])
            if not genuine[name]:
                print("  ok   {}".format(name))
        for index, (flip, old, new, guarded) in enumerate(FLIPS):
            mutant = _flipped(source, old, new)
            if mutant is None:
                failures.append("flip {!r} could not be applied to the code above the self-test".format(flip))
                continue
            flipped = _run_cases(tmp, "flip{}".format(index), mutant, set(guarded))
            for name in guarded:
                if flipped[name]:
                    print("  red  {} under flip: {}".format(name, flip))
                else:
                    failures.append("{} stayed green under the flip {!r}, so it does not show that "
                                    "handling".format(name, flip))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} cases green on the genuine tool, and each guarded case red under each of {} "
          "flips that remove its handling.".format(len(CASES), len(FLIPS)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root(), "--check" in sys.argv[1:])


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The self-test launches each case's tool copy
# as a child and reads its exit status, so under an ignored SIGCHLD a failing case would read as a pass. The
# guard runs at the entry line, before main() dispatches the generator, its --check or its --self-test, and a
# copy launched by the self-test carries it too. It is placed below the self-test marker, so no flip edits it.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("gen_ci_floor_digests.py") or main())
