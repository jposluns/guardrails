#!/usr/bin/env python3
"""Write the character-policy validator region of the preview hook from the CI gate's (single source).

tools/check_no_dashes.py is the SINGLE SOURCE OF TRUTH for the character-policy validator: the region between
its BEGIN COPY SOURCE and END COPY SOURCE lines holds validate_policy, parse_policy, their helpers, and every
module-level name they read (the json import, POLICY_CAP, the key sets, LINE_BOUNDARIES and PolicyError). The
preview hook .preview/char-policy-write.py is a standalone file that cannot import the gate, so it carries the
same region between its BEGIN COPY and END COPY lines. This generator copies the gate's region into the hook
byte for byte, keeping both marker lines and everything outside them, so the two validators cannot drift.

  gen_char_policy.py              rewrite the hook's region from the gate's
  gen_char_policy.py --check      exit 1 on any byte difference (run gen_char_policy.py to regenerate)
  gen_char_policy.py --self-test  synthetic sources and targets for the splice and its fail-closed cases

Exit: 0 clean (or written); 1 drift in --check; 2 cannot evaluate: an unknown argument (a usage line on
stderr), an unreadable or unwritable file, a file without exactly one BEGIN and exactly one END marker line,
in that order (a line starting "# --- BEGIN COPY" or "# --- END COPY" counts as a marker, so a stray or
nested one fails closed rather than being copied), or a marker line whose text is not the exact expected
marker ("a marker line is not the expected text").

Byte equality covers the region only. The hook's self-test H12 walks both files' module-level statements, compound
statements' bodies included, and fails when one outside the copied region binds a name the region binds or reads, or
__builtins__, at module scope, or a function outside it declares one global; or when one makes an attribute or item
store (an assignment, an augmented or annotated assignment, a for, with or comprehension target) or deletion, at
module scope, in a class body or in a def's or lambda's decorators or defaults, whose target chain starts from such
a name, json, builtins, sys.modules, this module or an alias of one of them. Conservatively, whatever the stored
value, it also fails on a store whose chain starts from anything but a name (a call such as __import__("json"),
globals() or logging.getLogger("app"), a conditional expression) or has a link (an attribute or a constant string
key) named sys, json, builtins or modules. An alias is a name bound, at module scope (a class body included) or in a
def that declares it global, by one of these forms: an import of json, builtins or sys, of a submodule of one or of
a name from one (from sys import modules binds an alias of sys.modules); an import of this module (import __main__,
or the gate's module check_no_dashes imported under any name); a from-import from this module, whose name is an
alias of the name it imports; and a for, with, comprehension or match target whose source is such a name or alias,
sys.modules (sys or an alias of sys, then .modules), or an item of sys.modules, which counts as this module. The
source is the iterable, the context expression or the subject and, recursively, each element of a tuple, list or set
display, the element of a comprehension and each positional argument of a call, never the function called or a
keyword argument. Every other aliasing form is out of scope; diff review is the control. Ordinary stores are not
flagged: sys.path[0] = ..., an item of os.environ or of a module-level dict under a key not named sys, json,
builtins or modules, a store to an attribute not so named of any other name (_Options.verbose = True), and a store
whose chain has no link so named and starts from a target whose source is none of those (for _row in sorted(_rows,
key=len): _row[0] = 2). That walk catches accidental drift between the two copies; it is not a defence against a
deliberate edit that replaces behaviour through a path the walk does not model. Examples, not a complete list: an
alias made by plain assignment or a walrus (m = sys.modules[__name__], then m.validate_policy = ...) or by a target
over a call's result (for m in (importlib.import_module("__main__"),): ...); an item key that is not a constant
string; a call such as setattr or exec; an in-place method call such as globals().update, sys.modules.update or
json.__dict__.update; a store in a function or lambda body; reflective access through an object the walk cannot
name; another module patching the file; and a module that shadows a standard library module, such as a json.py. The
gate keeps a tools/json.py out only under python3 -I, as CI runs it. The hashes recorded in .preview/SHA256SUMS and
.aiqt/manifest.toml let an installer or a release check detect a shipped copy that differs from the reviewed one,
and whoever makes an edit can record new hashes in the same change. After regenerating, the hook's SHA-256 in
.preview/SHA256SUMS and the .preview/README.md table changes too; tools/check_hooks_preview.py fails until both are
updated.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: gen_char_policy.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

SOURCE_REL = "tools/check_no_dashes.py"
TARGET_REL = ".preview/char-policy-write.py"
# The exact marker lines. Any other line starting with a marker prefix is a fault, never copied.
BEGIN_PREFIX, END_PREFIX = b"# --- BEGIN COPY", b"# --- END COPY"
SOURCE_BEGIN = (b"# --- BEGIN COPY SOURCE: tools/gen_char_policy.py writes this region into "
                b".preview/char-policy-write.py ---")
SOURCE_END = b"# --- END COPY SOURCE ---"
TARGET_BEGIN = (b"# --- BEGIN COPY: generated from tools/check_no_dashes.py by tools/gen_char_policy.py; "
                b"do not edit ---")
TARGET_END = b"# --- END COPY ---"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata only,
# it does not affect what this generator produces. The target is a generated region inside the hand-written
# hook (the BEGIN COPY and END COPY lines), so it is recorded as kind block.
GENSRC_OUTPUTS = (
    {"target": ".preview/char-policy-write.py", "kind": "block",
     "sources": ("tools/check_no_dashes.py",), "regenerate": "python3 tools/gen_char_policy.py"},
)


def _marked(data, begin, end, where):
    """(lines, begin index, end index) for data split on LF. Raises ValueError unless data holds exactly one
    line starting with each marker prefix, that line is the exact marker, and BEGIN comes first."""
    lines = data.split(b"\n")
    begins = [i for i, line in enumerate(lines) if line.startswith(BEGIN_PREFIX)]
    ends = [i for i, line in enumerate(lines) if line.startswith(END_PREFIX)]
    if len(begins) != 1 or len(ends) != 1:
        raise ValueError(f"{where}: expected exactly one BEGIN COPY and one END COPY marker line, found "
                         f"{len(begins)} and {len(ends)}")
    if lines[begins[0]] != begin or lines[ends[0]] != end:
        raise ValueError(f"{where}: a marker line is not the expected text ({begin!r} and {end!r})")
    if begins[0] >= ends[0]:
        raise ValueError(f"{where}: the END COPY marker comes before the BEGIN COPY marker")
    return lines, begins[0], ends[0]


def render(source, target):
    """target's bytes with the region between its markers replaced by source's region, byte for byte."""
    s_lines, s_begin, s_end = _marked(source, SOURCE_BEGIN, SOURCE_END, SOURCE_REL)
    t_lines, t_begin, t_end = _marked(target, TARGET_BEGIN, TARGET_END, TARGET_REL)
    return b"\n".join(t_lines[:t_begin + 1] + s_lines[s_begin + 1:s_end] + t_lines[t_end:])


def run(root, check):
    """Regenerate (or, with check, compare) the hook's region under root; print the result; return the exit."""
    root = Path(root)
    target = root / TARGET_REL
    try:
        current = target.read_bytes()
        desired = render((root / SOURCE_REL).read_bytes(), current)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}; fail-closed", file=sys.stderr)
        return 2
    if current == desired:
        return 0
    if check:
        print(f"drift: the policy validator region in {TARGET_REL} differs from {SOURCE_REL}")
        print("run tools/gen_char_policy.py to regenerate")
        return 1
    try:
        target.write_bytes(desired)
    except OSError as exc:
        print(f"error: cannot write {target} ({exc}); fail-closed", file=sys.stderr)
        return 2
    return 0


def _self_test():
    """Synthetic sources and targets: the splice is exact, drift of any byte is found, and every marker fault
    is exit 2."""
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    failures = []
    region = b"X = 1\n\ndef f():\n    return X  \n"  # trailing blanks are bytes too
    source = b"head\n" + SOURCE_BEGIN + b"\n" + region + SOURCE_END + b"\ntail\n"
    target = b"top\r\n" + TARGET_BEGIN + b"\n" + region + TARGET_END + b"\nbottom"

    def faulty(label, src, tgt):
        try:
            render(src, tgt)
            failures.append(f"{label}: rendered, want ValueError")
        except ValueError:
            pass

    if render(source, target) != target:
        failures.append("an up-to-date target changed")
    for drifted in (region.replace(b"X  \n", b"X \n"), region.replace(b"1", b"2"), region + b"\n", b""):
        stale = target.replace(region, drifted)
        if render(source, stale) != target:
            failures.append(f"drifted region {drifted!r} not restored byte for byte")
    faulty("no source BEGIN", source.replace(SOURCE_BEGIN, b"# begin"), target)
    faulty("no target END", source, target.replace(TARGET_END, b"# end"))
    faulty("two source BEGIN", source + SOURCE_BEGIN + b"\n", target)
    faulty("two target END", source, target + b"\n" + TARGET_END)
    faulty("END before BEGIN", source.replace(SOURCE_BEGIN, b"#").replace(b"tail", SOURCE_BEGIN), target)
    faulty("a marker-like line inside the region", source.replace(b"X = 1", b"# --- BEGIN COPY extra"), target)
    faulty("a target marker in the source", source.replace(SOURCE_BEGIN, TARGET_BEGIN), target)
    faulty("a marker with a trailing CR", source.replace(SOURCE_END, SOURCE_END + b"\r"), target)

    scratch = tempfile.mkdtemp(prefix="gen_char_policy_selftest_")
    try:
        for rel, data in ((SOURCE_REL, source), (TARGET_REL, target.replace(region, b"X = 0\n"))):
            path = Path(scratch, *rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for label, check, want in (("check on drift", True, 1), ("regenerate", False, 0), ("check after", True, 0)):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = run(scratch, check)
            if rc != want:
                failures.append(f"run: {label}: want exit {want}, got {rc}")
        if Path(scratch, TARGET_REL).read_bytes() != target:
            failures.append("run: the regenerated target is not the expected bytes")
        Path(scratch, SOURCE_REL).unlink()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = run(scratch, True)
        if rc != 2:
            failures.append(f"run: an absent source: want exit 2, got {rc}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} vector(s)")
        return 1
    print("SELF-TEST PASS: gen_char_policy.py splice, drift and marker vectors")
    return 0


def main(argv):
    if argv[1:] == ["--self-test"]:
        return _self_test()
    if argv[1:] not in ([], ["--check"]):
        print("usage: gen_char_policy.py [--check | --self-test]", file=sys.stderr)
        return 2
    return run(repo_root(), argv[1:] == ["--check"])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
