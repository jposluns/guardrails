#!/usr/bin/env python3
"""Skill frontmatter gate: every tracked SKILL.md, and every SKILL.md member of each tracked zip under
site/downloads/, must carry frontmatter the restricted reader (opf/tools/_skill_frontmatter.py) reads as
VALID.

  check_skill_frontmatter.py              exit 0 clean, 1 finding, 2 cannot evaluate
  check_skill_frontmatter.py --self-test  the reader's vectors, the gate over tempdir fixtures
  check_skill_frontmatter.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

The files are found from `git ls-files` by base name, never from a fixed list. A zip is read into memory
and each member whose base name is SKILL.md is read from it; nothing is extracted to disk. A file or
member the reader decides INVALID is a finding (exit 1). A file or member it decides CANNOT_EVALUATE, a
tracked path that is not a regular file, an unreadable or oversized file, a zip that cannot be opened or
read, a git failure, and a tree with no tracked SKILL.md at all are cannot-evaluate (exit 2), never a
pass. The reader is the one shared source in opf/tools/ so the pack's adopters receive it.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_skill_frontmatter.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import io
import os
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# opf/tools/ carries the ONE shared reader; this gate reaches down into it (a permitted downward edge).
sys.path.insert(0, str(ROOT / "opf" / "tools"))
import _skill_frontmatter as fm  # noqa: E402

SKILL_NAME = "SKILL.md"
ZIP_DIR = "site/downloads/"
# A tracked zip larger than this is cannot-evaluate before it is read.
MAX_ZIP_BYTES = 16 * 1024 * 1024


class CannotEvaluate(Exception):
    """An input the gate cannot read or trust: reported as exit 2."""


def _git_tracked(root):
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git ls-files could not run ({})".format(exc))
    if proc.returncode != 0:
        raise CannotEvaluate("git ls-files failed: {}".format(proc.stderr.decode("utf-8", "replace").strip()))
    try:
        return [p for p in proc.stdout.decode("utf-8").split("\0") if p]
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("git ls-files returned a path that is not UTF-8 ({})".format(exc))


def _read_bytes(path, limit):
    """The bytes of a regular file, refusing a non-regular entry and a file over limit bytes."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise CannotEvaluate("cannot stat ({})".format(exc))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("not a regular file")
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise CannotEvaluate("unreadable ({})".format(exc))
    if len(data) > limit:
        raise CannotEvaluate("larger than {} bytes".format(limit))
    return data


def targets(root, tracked):
    """(skill paths, zip paths): tracked files whose base name is SKILL.md, and tracked *.zip files under
    site/downloads/."""
    skills = sorted(p for p in tracked if p.rsplit("/", 1)[-1] == SKILL_NAME)
    zips = sorted(p for p in tracked if p.startswith(ZIP_DIR) and p.endswith(".zip"))
    return skills, zips


def zip_members(data, where):
    """[(member name, bytes)] for every member of the zip bytes whose base name is SKILL.md."""
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if info.filename.rsplit("/", 1)[-1] != SKILL_NAME:
                    continue
                if info.file_size > fm.MAX_BYTES:
                    raise CannotEvaluate("{}!{}: member larger than {} bytes".format(
                        where, info.filename, fm.MAX_BYTES))
                with archive.open(info) as member:
                    body = member.read(fm.MAX_BYTES + 1)
                out.append((info.filename, body))
    except CannotEvaluate:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, ValueError,
            EOFError, OSError) as exc:
        raise CannotEvaluate("{}: zip cannot be read ({})".format(where, exc))
    return out


def scan(root, tracked=None):
    """(findings, errors, checked): one INVALID line per finding, one line per cannot-evaluate, and the
    number of files and members decided."""
    findings, errors, checked = [], [], 0
    root = Path(root)
    try:
        if tracked is None:
            tracked = _git_tracked(root)
    except CannotEvaluate as exc:
        return findings, [str(exc)], checked
    skills, zips = targets(root, tracked)
    if not skills:
        errors.append("no tracked {} found; nothing to evaluate".format(SKILL_NAME))
    items = []
    for rel in skills:
        try:
            items.append((rel, _read_bytes(root / rel, fm.MAX_BYTES)))
        except CannotEvaluate as exc:
            errors.append("{}: {}".format(rel, exc))
    for rel in zips:
        try:
            for name, body in zip_members(_read_bytes(root / rel, MAX_ZIP_BYTES), rel):
                items.append(("{}!{}".format(rel, name), body))
        except CannotEvaluate as exc:
            errors.append(str(exc) if str(exc).startswith(rel) else "{}: {}".format(rel, exc))
    for where, body in items:
        result = fm.evaluate_bytes(body)
        checked += 1
        if result.status == fm.INVALID:
            findings.append("{}: {}".format(where, result.describe()))
        elif result.status != fm.VALID:
            errors.append("{}: {}".format(where, result.describe()))
    return findings, errors, checked


def run_gate(root, tracked=None):
    findings, errors, checked = scan(root, tracked)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: {} skill file(s) and zip member(s) carry VALID frontmatter".format(checked))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "skill-frontmatter-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _doc(*lines):
    return "---\n" + "".join(line + "\n" for line in lines) + "---\n\n# Body\n"


GOOD_NAME = "name: demo"
GOOD_DESC = "description: A demo skill."

def _run_vectors():
    """Run every reader vector, (check id, frontmatter text, expected status), the ids written as
    literals for the execution gate. Returns the ids of the INVALID vectors; _run_flips covers each."""
    invalid = []
    for cid, text, want in (
    ("vector/valid/plain", _doc(GOOD_NAME, GOOD_DESC), fm.VALID),
    ("vector/valid/single-quoted", _doc(GOOD_NAME, "description: 'It''s a demo.'"), fm.VALID),
    ("vector/valid/double-quoted", _doc(GOOD_NAME, 'description: "A \\"demo\\" skill."'), fm.VALID),
    ("vector/valid/folded-block", _doc(GOOD_NAME, "description: >-", "  First line: with a colon.",
                                "  Second line."), fm.VALID),
    ("vector/valid/literal-block", _doc(GOOD_NAME, "description: |", "  key: value text", "", "  more"),
     fm.VALID),
    ("vector/valid/plain-multiline", _doc(GOOD_NAME, "description: A demo skill that", "  continues here."),
     fm.VALID),
    ("vector/valid/extra-key", _doc(GOOD_NAME, GOOD_DESC, "license: Apache-2.0"), fm.VALID),
    ("vector/valid/blank-line-between-keys", _doc(GOOD_NAME, "", GOOD_DESC), fm.VALID),
    ("vector/boundary/colon-space-in-single-quotes", _doc(GOOD_NAME, "description: 'in order: one'"), fm.VALID),
    ("vector/boundary/colon-space-in-double-quotes", _doc(GOOD_NAME, 'description: "in order: one"'), fm.VALID),
    ("vector/boundary/url-in-plain", _doc(GOOD_NAME, "description: See https://example.org/x for more."),
     fm.VALID),
    ("vector/boundary/colon-no-space-in-plain", _doc(GOOD_NAME, "description: ratio a:b holds."), fm.VALID),
    ("vector/boundary/colon-space-in-plain", _doc(GOOD_NAME, "description: in order: one"), fm.INVALID),
    ("vector/boundary/url-with-space-after-colon", _doc(GOOD_NAME, "description: See https: //example.org"),
     fm.INVALID),
    ("vector/invalid/colon-space-on-continuation", _doc(GOOD_NAME, "description: A demo that", "  runs: fast."),
     fm.INVALID),
    ("vector/invalid/line-final-colon", _doc(GOOD_NAME, "description: A demo, in order:", "  one, two."),
     fm.INVALID),
    ("vector/invalid/tab-key-line", _doc(GOOD_NAME, "\t" + GOOD_DESC), fm.INVALID),
    ("vector/invalid/duplicate-key", _doc(GOOD_NAME, GOOD_DESC, "name: other"), fm.INVALID),
    ("vector/invalid/missing-name", _doc(GOOD_DESC), fm.INVALID),
    ("vector/invalid/missing-description", _doc(GOOD_NAME), fm.INVALID),
    ("vector/invalid/empty-name", _doc("name:", GOOD_DESC), fm.INVALID),
    ("vector/invalid/empty-quoted-description", _doc(GOOD_NAME, "description: ''"), fm.INVALID),
    ("vector/invalid/empty-block-description", _doc(GOOD_NAME, "description: >-", "license: x"), fm.INVALID),
    ("vector/cannot/no-frontmatter", "# Title\n", fm.CANNOT_EVALUATE),
    ("vector/cannot/unclosed-frontmatter", "---\nname: demo\n", fm.CANNOT_EVALUATE),
    ("vector/cannot/crlf", _doc(GOOD_NAME, GOOD_DESC).replace("\n", "\r\n"), fm.CANNOT_EVALUATE),
    ("vector/cannot/bom", "\ufeff" + _doc(GOOD_NAME, GOOD_DESC), fm.CANNOT_EVALUATE),
    ("vector/cannot/comment-line", _doc("# note", GOOD_NAME, GOOD_DESC), fm.CANNOT_EVALUATE),
    ("vector/cannot/list-item", _doc(GOOD_NAME, GOOD_DESC, "- item"), fm.CANNOT_EVALUATE),
    ("vector/cannot/flow-value", _doc(GOOD_NAME, "description: [a, b]"), fm.CANNOT_EVALUATE),
    ("vector/cannot/anchor", _doc(GOOD_NAME, "description: &a text"), fm.CANNOT_EVALUATE),
    ("vector/cannot/non-string-name", _doc("name: true", GOOD_DESC), fm.CANNOT_EVALUATE),
    ("vector/cannot/number-name", _doc("name: 12", GOOD_DESC), fm.CANNOT_EVALUATE),
    ("vector/cannot/multiline-quoted", _doc(GOOD_NAME, "description: 'a demo", "  skill'"), fm.CANNOT_EVALUATE),
    ("vector/cannot/unknown-escape", _doc(GOOD_NAME, 'description: "a \\x41"'), fm.CANNOT_EVALUATE),
    ("vector/cannot/indent-indicator", _doc(GOOD_NAME, "description: >2", "   text"), fm.CANNOT_EVALUATE),
    ("vector/cannot/comment-in-plain", _doc(GOOD_NAME, "description: a demo #note"), fm.CANNOT_EVALUATE),
    ("vector/cannot/value-on-next-line", _doc(GOOD_NAME, "description:", "  a demo"), fm.CANNOT_EVALUATE),
    ("vector/cannot/empty-other-key", _doc(GOOD_NAME, GOOD_DESC, "license:"), fm.CANNOT_EVALUATE),
    ("vector/cannot/nested-mapping", _doc(GOOD_NAME, GOOD_DESC, "meta:", "  a: b"), fm.CANNOT_EVALUATE),
    ("vector/cannot/block-dedent-inside", _doc(GOOD_NAME, "description: |", "    deep", "  shallow"),
     fm.CANNOT_EVALUATE),
    ):
        got = fm.evaluate_bytes(text.encode("utf-8"))
        check(cid, got.status == want, got.describe())
        if want == fm.INVALID:
            invalid.append(cid)
    return invalid


def _run_flips():
    """Run each INVALID vector's flip: the same document with only its defect removed, which must read
    VALID. Returns the vector id each flip covers."""
    covered = []
    for cid, vector, text in (
        ("flip/boundary/colon-space-in-plain", "vector/boundary/colon-space-in-plain",
         _doc(GOOD_NAME, "description: 'in order: one'")),
        ("flip/boundary/url-with-space-after-colon", "vector/boundary/url-with-space-after-colon",
         _doc(GOOD_NAME, "description: See https://example.org")),
        ("flip/invalid/colon-space-on-continuation", "vector/invalid/colon-space-on-continuation",
         _doc(GOOD_NAME, "description: >-", "  A demo that", "  runs: fast.")),
        ("flip/invalid/line-final-colon", "vector/invalid/line-final-colon",
         _doc(GOOD_NAME, "description: >-", "  A demo, in order:", "  one, two.")),
        ("flip/invalid/tab-key-line", "vector/invalid/tab-key-line", _doc(GOOD_NAME, GOOD_DESC)),
        ("flip/invalid/duplicate-key", "vector/invalid/duplicate-key", _doc(GOOD_NAME, GOOD_DESC)),
        ("flip/invalid/missing-name", "vector/invalid/missing-name", _doc(GOOD_NAME, GOOD_DESC)),
        ("flip/invalid/missing-description", "vector/invalid/missing-description",
         _doc(GOOD_NAME, GOOD_DESC)),
        ("flip/invalid/empty-name", "vector/invalid/empty-name", _doc(GOOD_NAME, GOOD_DESC)),
        ("flip/invalid/empty-quoted-description", "vector/invalid/empty-quoted-description",
         _doc(GOOD_NAME, "description: 'x'")),
        ("flip/invalid/empty-block-description", "vector/invalid/empty-block-description",
         _doc(GOOD_NAME, "description: >-", "  x", "license: x")),
    ):
        got = fm.evaluate_bytes(text.encode("utf-8"))
        check(cid, got.status == fm.VALID, got.describe())
        covered.append(vector)
    return covered


def _unfixed(data):
    """The bytes before the content fix: the folded-block header and the two-space indent of the first
    description line removed again (a file without the header is returned unchanged)."""
    return data.replace(b"\ndescription: >-\n  ", b"\ndescription: ", 1)


def _zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, body in members:
            archive.writestr(name, body)
    return buf.getvalue()


def _tree_case(files):
    """run_gate over a temp tree holding files {rel: bytes}, every one passed as tracked."""
    import contextlib
    with tempfile.TemporaryDirectory(prefix="skill-frontmatter-selftest-") as tmp:
        for rel, body in files.items():
            path = Path(tmp) / rel
            os.makedirs(path.parent, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(body)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_gate(tmp, sorted(files))


def _self_test():
    invalid = _run_vectors()
    covered = _run_flips()
    check("flip/coverage", sorted(covered) == sorted(invalid), (sorted(covered), sorted(invalid)))

    got = fm.evaluate_bytes(b"\xff" + _doc(GOOD_NAME, GOOD_DESC).encode("utf-8"))
    check("cannot/not-utf8", got.status == fm.CANNOT_EVALUATE, got.describe())
    check("cannot/not-bytes", fm.evaluate_bytes("text").status == fm.CANNOT_EVALUATE)
    # The size bound at its end: one byte under and exactly at MAX_BYTES are read, one over is refused.
    base = _doc(GOOD_NAME, GOOD_DESC).encode("utf-8")
    for name, size, want in (("bound/size-under", fm.MAX_BYTES - 1, fm.VALID),
                             ("bound/size-at", fm.MAX_BYTES, fm.VALID),
                             ("bound/size-over", fm.MAX_BYTES + 1, fm.CANNOT_EVALUATE)):
        body = base + b"x" * (size - len(base) - 1) + b"\n"
        got = fm.evaluate_bytes(body)
        check(name, len(body) == size and got.status == want, (len(body), got.describe()))

    # The two shipped files the gate was written for, with the sha256 of each as it stood before the
    # content fix that turned its plain description into a folded block scalar. The old bytes are rebuilt
    # from the tracked file by undoing that one edit (_unfixed) and their digest proved; they must read
    # INVALID and the tracked bytes VALID.
    import hashlib
    for before_id, after_id, rel, digest in (
        ("real-before/opf/prompt-pack/adopt/SKILL.md", "real-after/opf/prompt-pack/adopt/SKILL.md",
         "opf/prompt-pack/adopt/SKILL.md", "632ae20bcef0f92e47cf377fb9f9131c2562a1fe086873463f018891ce9e8c0b"),
        ("real-before/opf/prompt-pack/import/SKILL.md", "real-after/opf/prompt-pack/import/SKILL.md",
         "opf/prompt-pack/import/SKILL.md", "2557ac6f3307e3b520647b540a2108f2a8079c7dab8e4c40393b28bd154c62b6"),
    ):
        try:
            data = _read_bytes(ROOT / rel, fm.MAX_BYTES)
        except CannotEvaluate as exc:
            data = None
            detail = str(exc)
        old = b"" if data is None else _unfixed(data)
        before = fm.evaluate_bytes(old)
        after = fm.evaluate_bytes(b"" if data is None else data)
        check(before_id, data is not None and hashlib.sha256(old).hexdigest() == digest
              and before.status == fm.INVALID and "': '" in before.detail,
              detail if data is None else (hashlib.sha256(old).hexdigest(), before.describe()))
        check(after_id, data is not None and after.status == fm.VALID,
              detail if data is None else after.describe())

    good = _doc(GOOD_NAME, GOOD_DESC).encode("utf-8")
    bad = _doc(GOOD_NAME, "description: in order: one").encode("utf-8")
    odd = _doc("# note", GOOD_NAME, GOOD_DESC).encode("utf-8")
    check("gate/clean-tree-passes", _tree_case({"a/SKILL.md": good, "b/README.md": b"x\n"}) == 0)
    check("gate/invalid-file-fails", _tree_case({"a/SKILL.md": good, "b/SKILL.md": bad}) == 1)
    check("gate/cannot-evaluate-file", _tree_case({"a/SKILL.md": odd}) == 2)
    check("gate/no-skill-file", _tree_case({"b/README.md": b"x\n"}) == 2)
    check("gate/base-name-only", _tree_case({"a/SKILL.md": good, "b/SKILL.md.bak": bad,
                                             "c/NOTSKILL.md": bad}) == 0)
    zipped = {"a/SKILL.md": good, "site/downloads/s.zip": _zip_bytes([("s/SKILL.md", bad)])}
    check("gate/zip-member-invalid", _tree_case(zipped) == 1)
    zipped["site/downloads/s.zip"] = _zip_bytes([("s/SKILL.md", good), ("s/other.md", bad)])
    check("gate/zip-member-valid", _tree_case(zipped) == 0)
    zipped["site/downloads/s.zip"] = b"not a zip"
    check("gate/zip-unreadable", _tree_case(zipped) == 2)
    check("gate/zip-outside-downloads-ignored",
          _tree_case({"a/SKILL.md": good, "other/s.zip": b"not a zip"}) == 0)

    with tempfile.TemporaryDirectory(prefix="skill-frontmatter-selftest-") as tmp:
        os.makedirs(Path(tmp) / "a" / SKILL_NAME)
        findings, errors, _n = scan(tmp, ["a/SKILL.md"])
    check("gate/not-regular-file", findings == [] and len(errors) == 1 and "not a regular" in errors[0],
          (findings, errors))

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("gate/unrecognized-argument", got == [2, 2, 2], got)

    if FAILURES:
        print("SELF-TEST FAIL: check_skill_frontmatter {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_skill_frontmatter {} checks (VALID, INVALID and CANNOT_EVALUATE vectors, "
          "a flip for each INVALID vector, the ': ' boundaries, the size bound, the two shipped files "
          "INVALID before the content fix and VALID after, and the gate over tempdir trees and zips)".format(
              len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(ROOT)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
