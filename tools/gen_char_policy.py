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

Exit: 0 clean (or written); 1 drift in --check; 2 cannot evaluate: an unreadable or unwritable file, or a file
without exactly one BEGIN and exactly one END marker line, in that order (a line starting "# --- BEGIN COPY"
or "# --- END COPY" counts as a marker, so a stray or nested one fails closed rather than being copied).

Byte equality covers the region only. Whether a name the region reads is rebound outside it, in either file,
is checked by the hook's own self-test (H12), which walks both files' module scope. After regenerating, the
hook's SHA-256 in .preview/SHA256SUMS and the .preview/README.md table changes too; tools/check_hooks_preview.py
fails until both are updated.
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
