#!/usr/bin/env python3
"""Shared, data-only release-manifest grammar (PR-C1).

ROOT hashes exact bytes, never parsed/re-serialized TOML. TREE hashes source
rows in their supplied order. VALID means schema-valid, not authenticated.
TREE equality and actual content/ownership checks remain consumer obligations.

R6: coverage is bounded to the manifest and consumer contract; a managed-block
digest covers only that block, never the whole containing file.
R7: input caps are not a process sandbox or a transport/quarantine boundary.
R10: a larger release or changed publisher format refuses until reviewed update.
R11: the generator remains a separate emitter; repository parity is required.

Both rosters are required and nonempty. Repeated artifact coverage is permitted
only with equal digests and distinct artifact-ids. Source/file-artifact digests
must agree; source/managed-block digests have different subjects.
"""
import argparse
import hashlib
import re
import sys
import tomllib
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _opf_adopt import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, AdoptValidation,
    _is_contained_filepath, _is_token,
)
from _opf_adopt_plan import (  # noqa: E402
    MAX_FILE_BYTES, MAX_ENTRIES, MAX_PATH_BYTES, MAX_DEPTH,
)
from _semver import _parse as _parse_version  # noqa: E402

MANIFEST_TOP_KEYS = frozenset({
    "format-version", "release-version", "genesis", "tree-sha256",
    "sources", "artifacts",
})
SOURCE_ROW_KEYS = frozenset({"path", "bytes", "sha256"})
ARTIFACT_KINDS = ("file", "managed-block")
_ARTIFACT_REQUIRED = frozenset({"artifact-id", "path", "kind", "sha256"})
_ROOT_LINE_RE = re.compile(r"^sha256:[0-9a-f]{64}\n\Z")
_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")


class _Refusal(Exception):
    def __init__(self, status, guard):
        super().__init__(guard)
        self.status = status


def _require(condition, guard, status=INVALID):
    """Named checks also identify the precise guard disabled by mutation tests."""
    if not condition:
        raise _Refusal(status, guard)


def _hex_ok(value):
    return type(value) is str and _HEX_RE.fullmatch(value) is not None


def _path_ok(value):
    if type(value) is not str or not _is_contained_filepath(value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def compute_root(manifest_bytes: bytes) -> str:
    """SHA-256 of exact bytes, including comments, whitespace and line endings."""
    if type(manifest_bytes) is not bytes:
        raise TypeError("ROOT requires exact bytes")
    return hashlib.sha256(manifest_bytes).hexdigest()


def compute_tree(source_rows) -> str:
    """Hash mappings with path/sha256 in supplied order; caller validates rows.

    Size is intentionally absent from TREE. This helper neither sorts nor
    normalizes paths and makes no claim about file availability or contents.
    """
    raw = "".join(
        "{}\t{}\n".format(row["path"], row["sha256"]) for row in source_rows
    ).encode("utf-8")
    return compute_root(raw)


def parse_root_txt(raw):
    """Return 64 lowercase hex, or an AdoptValidation refusal."""
    if type(raw) is not bytes:
        return AdoptValidation(CANNOT_EVALUATE, ["root.txt bytes unavailable"])
    # Bound decoding even when a caller passes a huge root.txt response.
    if len(raw) != 72:
        return AdoptValidation(INVALID, ["root.txt byte grammar"])
    try:
        text = raw.decode("ascii")
    except UnicodeError:
        return AdoptValidation(INVALID, ["root.txt ASCII grammar"])
    if _ROOT_LINE_RE.fullmatch(text) is None:
        return AdoptValidation(INVALID, ["root.txt byte grammar"])
    return text[7:-1]


def _parse_manifest(raw):
    _require(type(raw) is bytes, "manifest-bytes", CANNOT_EVALUATE)
    _require(
        all(type(n) is int and n > 0 for n in
            (MAX_FILE_BYTES, MAX_ENTRIES, MAX_PATH_BYTES, MAX_DEPTH)),
        "invalid-parser-bounds", CANNOT_EVALUATE,
    )
    _require(len(raw) <= MAX_FILE_BYTES, "manifest-byte-bound")
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise _Refusal(CANNOT_EVALUATE, "manifest-unparseable") from exc

    _require(type(document) is dict, "manifest-table", CANNOT_EVALUATE)
    _require(set(document) == MANIFEST_TOP_KEYS, "manifest-keys")
    _require(type(document["format-version"]) is int, "format-type")
    _require(document["format-version"] == 1, "format-vocabulary", CANNOT_EVALUATE)
    _require(type(document["genesis"]) is bool, "genesis-type")
    _require(
        type(document["release-version"]) is str
        and _parse_version(document["release-version"]) is not None,
        "release-version",
    )
    _require(_hex_ok(document["tree-sha256"]), "tree-digest")

    for name in ("sources", "artifacts"):
        rows = document[name]
        _require(type(rows) is list, name + "-array", CANNOT_EVALUATE)
        _require(bool(rows), name + "-nonempty")
    _require(
        len(document["sources"]) + len(document["artifacts"]) <= MAX_ENTRIES,
        "manifest-entry-bound",
    )
    for name in ("sources", "artifacts"):
        for row in document[name]:
            _require(type(row) is dict, name + "-row-table", CANNOT_EVALUATE)

    source_digests = {}
    path_bytes = 0
    for row in document["sources"]:
        _require(set(row) == SOURCE_ROW_KEYS, "source-keys")
        _require(_path_ok(row["path"]), "source-path")
        _require(len(row["path"].split("/")) <= MAX_DEPTH, "source-depth")
        _require(type(row["bytes"]) is int and row["bytes"] >= 0, "source-size")
        _require(_hex_ok(row["sha256"]), "source-digest")
        _require(row["path"] not in source_digests, "source-unique")
        source_digests[row["path"]] = row["sha256"]
        path_bytes += len(row["path"].encode("utf-8"))

    paths = [row["path"] for row in document["sources"]]
    _require(
        paths == sorted(paths, key=lambda path: path.encode("utf-8")),
        "source-order",
    )

    artifact_ids = set()
    coverage = {}
    for row in document["artifacts"]:
        _require(
            _ARTIFACT_REQUIRED <= set(row) <= _ARTIFACT_REQUIRED | {"block-id"},
            "artifact-keys",
        )
        _require(type(row["kind"]) is str, "artifact-kind-type")
        _require(row["kind"] in ARTIFACT_KINDS, "artifact-kind", CANNOT_EVALUATE)
        _require(_path_ok(row["path"]), "artifact-path")
        _require(len(row["path"].split("/")) <= MAX_DEPTH, "artifact-depth")
        _require(_is_token(row["artifact-id"]), "artifact-id")
        _require(row["artifact-id"] not in artifact_ids, "artifact-unique")
        artifact_ids.add(row["artifact-id"])
        _require(_hex_ok(row["sha256"]), "artifact-digest")

        if row["kind"] == "managed-block":
            # Block identifiers are opaque nonempty single-line tokens. Do not
            # invent an uppercase/length vocabulary from the current publisher.
            _require(_is_token(row.get("block-id")), "block-id")
        else:
            _require("block-id" not in row, "file-block-id")
            if row["path"] in source_digests:
                _require(
                    row["sha256"] == source_digests[row["path"]],
                    "source-artifact-consistency",
                )

        key = (row["path"], row["kind"], row.get("block-id", ""))
        _require(
            key not in coverage or coverage[key] == row["sha256"],
            "artifact-coverage-consistency",
        )
        coverage[key] = row["sha256"]
        path_bytes += len(row["path"].encode("utf-8"))
    _require(path_bytes <= MAX_PATH_BYTES, "manifest-path-byte-bound")
    return document


def parse_manifest(raw):
    """Return (document, VALID), or (None, INVALID/CANNOT_EVALUATE).

    Containers and unknown closed tokens are CANNOT_EVALUATE. Parseable schema
    violations, including empty required rosters and exceeded caps, are INVALID.
    A shape-valid but incorrect TREE is left to the consumer's recomputation.
    """
    try:
        return _parse_manifest(raw), AdoptValidation(VALID, [])
    except _Refusal as exc:
        return None, AdoptValidation(exc.status, [str(exc)])
    except Exception as exc:
        # Unexpected runtime failures refuse; cancellation (BaseException)
        # propagates. No partially validated document escapes.
        return None, AdoptValidation(
            CANNOT_EVALUATE, ["manifest evaluation failed: " + type(exc).__name__],
        )


def _fixture():
    """Pinned generator-shaped bytes, independent of the emitter under test."""
    return (
        b'format-version = 1\n'
        b'release-version = "1.0.0"\n'
        b'genesis = true\n'
        b'tree-sha256 = "70dd851c0eb5019ec6ecf0c6b0470392160f4dbaaa34fd07a6f32e0ff122f45e"\n'
        b'\n[[sources]]\npath = "a"\nbytes = 0\nsha256 = "' + b"0" * 64 + b'"\n'
        b'\n[[artifacts]]\nartifact-id = "file:a"\npath = "a"\nkind = "file"\n'
        b'sha256 = "' + b"0" * 64 + b'"\n'
        b'\n[[artifacts]]\nartifact-id = "block:a"\npath = "a"\n'
        b'kind = "managed-block"\nblock-id = "RULES-INDEX"\n'
        b'sha256 = "' + b"1" * 64 + b'"\n'
    )


def _test_vectors():
    """One-change cases from a passing fixture; expected statuses are literal.

    Independent malformed TOML and inline-table forms additionally exercise
    paths that the generator-shaped fixture cannot produce.
    """
    import json

    base = _fixture()
    header, rest = base.split(b"\n[[sources]]", 1)
    source, artifacts = (b"\n[[sources]]" + rest).split(b"\n[[artifacts]]", 1)
    artifacts = b"\n[[artifacts]]" + artifacts
    file_row, block_row = artifacts.split(b"\n[[artifacts]]", 2)[1:]
    file_row = b"\n[[artifacts]]" + file_row
    block_row = b"\n[[artifacts]]" + block_row
    cases = [("generator-shaped", base, VALID)]

    def flip(name, raw, old, new, status=INVALID):
        if raw.count(old) != 1:
            raise AssertionError("ambiguous fixture edit: " + name)
        cases.append((name, raw.replace(old, new, 1), status))

    def source_flip(name, old, new, status=INVALID):
        flip(name, source, old, new, status)
        label, changed, want = cases.pop()
        cases.append((label, header + changed + artifacts, want))

    def artifact_flip(name, old, new, status=INVALID):
        flip(name, file_row, old, new, status)
        label, changed, want = cases.pop()
        cases.append((label, header + source + changed + block_row, want))

    flip("unknown-top", base, b"genesis = true", b"genesis = true\nextra = 1")
    flip("missing-top", base, b"genesis = true\n", b"")
    for value, want in ((b"2", CANNOT_EVALUATE), (b"true", INVALID),
                        (b"1.0", INVALID), (b'"1"', INVALID)):
        flip("format-" + value.decode(), base, b"format-version = 1",
             b"format-version = " + value, want)
    flip("genesis", base, b"genesis = true", b"genesis = 1")
    for value in (b'"01.0.0"', b'"1.0.0\\n"', b'"1.0.0-beta"', b"1"):
        flip("version-" + value.decode(), base, b'release-version = "1.0.0"',
             b"release-version = " + value)
    flip("tree-digest", base, b'tree-sha256 = "70dd', b'tree-sha256 = "G0dd')
    for value in (b"true", b"-1", b"0.0", b'"0"'):
        source_flip("size-" + value.decode(), b"bytes = 0", b"bytes = " + value)
    source_flip("source-extra", b"bytes = 0", b"bytes = 0\nextra = 1")
    source_flip("source-missing", b"bytes = 0\n", b"")
    source_flip("source-digest", b"0" * 64, b"g" * 64)
    artifact_flip("artifact-extra", b'kind = "file"', b'kind = "file"\nextra = 1')
    artifact_flip("artifact-missing", b'artifact-id = "file:a"\n', b"")
    artifact_flip("unknown-kind", b'kind = "file"', b'kind = "future"', CANNOT_EVALUATE)
    artifact_flip("kind-type", b'kind = "file"', b"kind = 1")
    artifact_flip("empty-id", b'"file:a"', b'""')
    artifact_flip("control-id", b'"file:a"', b'"file:\\u0085a"')
    artifact_flip("artifact-digest", b"0" * 64, b"A" * 64)
    artifact_flip("digest-conflict", b"0" * 64, b"2" * 64)
    artifact_flip("file-block-id", b'kind = "file"',
                  b'kind = "file"\nblock-id = "RULES-INDEX"')
    flip("block-missing", base, b'block-id = "RULES-INDEX"\n', b"")
    flip("block-empty", base, b'block-id = "RULES-INDEX"', b'block-id = ""')
    flip("block-type", base, b'block-id = "RULES-INDEX"', b"block-id = 1")
    flip("duplicate-id", base, b'"block:a"', b'"file:a"')
    cases += [
        ("duplicate-source", header + source + source + artifacts, INVALID),
        ("unsorted-source", header + source + source.replace(b'"a"', b'"0"')
         + artifacts, INVALID),
        ("equal-coverage", base + block_row.replace(b'"block:a"', b'"other:a"'), VALID),
        ("coverage-conflict", base + block_row.replace(b'"block:a"', b'"other:a"')
         .replace(b"1" * 64, b"2" * 64), INVALID),
    ]
    # JSON quoting is also valid TOML basic-string quoting for these characters.
    for index, path in enumerate((
        ".", "..", "../a", "/a", "C:/a", "a\\b", "a//b", "a/./b",
        "a/../b", "a/", "a\tb", "a\nb", "a\x00b", "a\x7fb",
        "a\u0085b", "a\u2028b", "a\u2029b",
    )):
        replacement = b"path = " + json.dumps(path, ensure_ascii=True).encode("ascii")
        source_flip("source-path-" + str(index), b'path = "a"', replacement)
        artifact_flip("artifact-path-" + str(index), b'path = "a"', replacement)
    # Independently authored containers, not emitted by the generator.
    for value, status in ((b"[]", INVALID), (b"{}", CANNOT_EVALUATE),
                          (b"1", CANNOT_EVALUATE), (b"[1]", CANNOT_EVALUATE),
                          (b"[{}]", INVALID)):
        cases.append(("sources-" + value.decode(),
                      header + b"\nsources = " + value + b"\n" + artifacts, status))
        cases.append(("artifacts-" + value.decode(),
                      header + b"\nartifacts = " + value + b"\n" + source, status))
    cases += [
        ("utf8", b"\xff", CANNOT_EVALUATE),
        ("duplicate-toml-key", base + b'kind = "file"\n', CANNOT_EVALUATE),
        ("independent-duplicate-key", b"x=1\nx=2\n", CANNOT_EVALUATE),
        ("independent-syntax", b"[", CANNOT_EVALUATE),
        ("independent-missing", b"x=1\n", INVALID),
        ("wrong-input-type", "not bytes", CANNOT_EVALUATE),
    ]
    return cases


def _case_passes(case):
    _, raw, expected = case
    parsed, result = parse_manifest(raw)
    return (
        isinstance(result, AdoptValidation)
        and result.status == expected
        and ((type(parsed) is dict and not result.findings) if expected == VALID
             else (parsed is None and bool(result.findings)))
    )


def _runner_check(expected, text=None):
    """Prove exact dispatch using the real shell text, as the P0 suite does.

    Other Python gates are intercepted. Only this parser's vector leg runs,
    avoiding recursive registration checks. This proves this registration,
    not the other gates' health or arbitrary shell-wrapper equivalence.
    """
    import ast
    import os
    import shlex
    import shutil
    import signal
    import subprocess
    import tempfile

    here = Path(__file__).resolve().parent
    runner = here / "run_all_checks.sh"
    registered = runner.read_text(encoding="utf-8")
    source = registered if text is None else text
    identity = "runner/pack-manifest-registration"
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError(identity + "/cannot-evaluate/bash")
    bash = os.path.abspath(bash)

    def registrations(body):
        # Deliberately bounded grammar: quoted gate name, python3, literal
        # arguments or double-quoted $here paths, on one unindented line.
        # This is not a Bash parser: every line spelling the run_gate token
        # must be canonical, except the exact definition line below. This
        # refuses prefixes, groups and function wrappers on those lines.
        # Invocations that never spell the token (variables, eval of
        # computed text, aliases) are invisible to this static check.
        calls = []
        for line in body.splitlines():
            if not re.search(r"\brun_gate\b", line) or line == "run_gate() {":
                continue
            try:
                words = shlex.split(line)
            except ValueError as exc:
                raise RuntimeError(identity + "/cannot-evaluate/grammar") from exc
            if not re.fullmatch(
                    r'run_gate "[A-Za-z0-9_-]+" +python3'
                    r'(?: +(?:[A-Za-z0-9_./=-]+|"\$here/[A-Za-z0-9_./-]+"))+ *',
                    line):
                raise RuntimeError(identity + "/cannot-evaluate/grammar")
            calls.append([word.replace("$here/", str(here) + "/") for word in words[3:]])
        return calls

    # The file defines expected dispatch, not an independently required roster.
    # Validate candidate grammar too, but never derive expected argv from it.
    calls = registrations(registered)
    registrations(source)
    if not calls:
        raise RuntimeError(identity + "/cannot-evaluate/grammar")
    wanted = b"".join(os.fsencode(word) + b"\0"
                      for args in calls for word in [str(len(args)), *args])
    # Equality of argc-framed records binds the number, order and arguments of
    # intercepted calls to the parsed registrations, including dispatcher skips.
    # Absolute paths or a changed PATH can run a real gate before a missing
    # expected call is detected. Extra unintercepted executions need not change
    # this log at all. This is not a process sandbox or a roster-coverage check.
    # Intercepted siblings return 0, so sibling failure propagation via
    # failed=1 and the final exit is outside this check.
    fixture = r'''#!/bin/sh
printf '%s\0' "$#" "$@" >> "$manifest_log" || exit 2
if [ "$#" -eq 4 ] && [ "$1" = "-I" ] && [ "$2" = "-B" ] \
    && [ "$3" = "$manifest_test" ] && [ "$4" = "--self-test" ]; then
  exec "$manifest_python" -I -B "$manifest_test" --self-test --vectors-only
fi
case " $* " in *_opf_pack_manifest.py*) exit 2;; esac
exit 0
'''
    # No inherited BASH_ENV, exported functions, Python or Git controls.
    with tempfile.TemporaryDirectory(prefix="opf-pack-registration-") as tmp:
        os.chmod(tmp, 0o700)
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        executable = Path(tmp) / "python3"
        executable.write_text(fixture, encoding="utf-8")
        executable.chmod(0o700)
        log = Path(tmp) / "argv.log"
        log.write_bytes(b"")
        log.chmod(0o600)
        env = {"PATH": tmp + os.pathsep + os.defpath, "TMPDIR": tmp,
               "HOME": tmp, "XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp,
               "XDG_DATA_HOME": tmp, "XDG_STATE_HOME": tmp, "LC_ALL": "C",
               "PYTHONDONTWRITEBYTECODE": "1", "manifest_log": str(log),
               "manifest_python": sys.executable,
               "manifest_test": str(here / "_opf_pack_manifest.py")}

        def run_shell(body):
            with subprocess.Popen(
                    [bash, "--noprofile", "--norc", "-c", body, str(runner)],
                    cwd=tmp, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
                try:
                    stdout, stderr = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    # Kill descendants even when the shell itself has exited.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        # An escaped session may still hold a pipe. Closing it
                        # bounds collection; such processes are not contained.
                        proc.stdout.close()
                        proc.stderr.close()
                    raise RuntimeError(identity + "/cannot-evaluate/timeout") from None
                return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

        # Probe with exactly the runner's cwd, flags and environment. A noexec
        # fixture or unusable PATH must never fall through to the real gates.
        # Also require the runner utility and the fixture interpreter.
        probe = run_shell("type -P dirname >/dev/null && test -x /bin/sh && "
                          "type -P python3")
        if probe.returncode != 0 or probe.stdout != str(executable) + "\n":
            raise RuntimeError(identity + "/cannot-evaluate/interception")
        proc = run_shell(source)
        recorded = log.read_bytes()
    if proc.returncode != 0:
        raise AssertionError(identity + "/return-code")
    try:
        reports = [ast.literal_eval(line[len("PACK-MANIFEST "):])
                   for line in proc.stdout.splitlines()
                   if line.startswith("PACK-MANIFEST ")]
    except (SyntaxError, ValueError) as exc:
        raise AssertionError(identity + "/pass-lines") from exc
    if reports != [{"executed": expected, "failures": []}]:
        raise AssertionError(identity + "/pass-lines")
    if recorded != wanted:
        raise AssertionError(identity + "/argv-log")


def _runner_red_checks(expected):
    import os
    import subprocess
    import tempfile
    from unittest.mock import patch

    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    identity = "runner/pack-manifest-registration"
    sibling = [line for line in source.splitlines(keepends=True)
               if line.startswith('run_gate "opf-homes-selftest"')]
    anchor = '  local name="$1"; shift\n'
    if len(sibling) != 1 or source.count(anchor) != 1:
        raise AssertionError(identity + "/red-fixture")
    skip = source.replace(
        anchor, anchor + '  if [ "$name" = opf-homes-selftest ]; then return 0; fi\n', 1)

    def red(label, call, error, wanted):
        try:
            call()
        except error as exc:
            if str(exc) != wanted:
                raise AssertionError(identity + "/" + label + "/wrong-red") from exc
        except Exception as exc:
            raise AssertionError(identity + "/" + label + "/wrong-error") from exc
        else:
            raise AssertionError(identity + "/" + label + "/not-red")
        print("RED {} -> {}".format(label, wanted))

    # Same registered text; the dispatcher skips a canonical sibling call.
    # This tests dispatch divergence, not deletion from the on-disk roster.
    red("dispatch-divergence", lambda: _runner_check(expected, skip),
        AssertionError, identity + "/argv-log")
    # Exit from the dispatcher before the runner can report success.
    red("return-code", lambda: _runner_check(
        expected, source.replace(anchor, anchor + "  exit 1\n", 1)),
        AssertionError, identity + "/return-code")
    original_read = Path.read_text
    grammar_cases = (
        ("colon-prefix", ":; " + sibling[0]),
        ("true-prefix", "true; " + sibling[0]),
        ("brace-group", "{ " + sibling[0].rstrip("\n") + "; }\n"),
        ("conditional", "if :; then " + sibling[0].rstrip("\n") + "; fi\n"),
        ("and-prefix", ": && " + sibling[0]),
        ("function-wrapper", "wrapper() { " + sibling[0].rstrip("\n") + "; }\n"),
        ("indented-registration-dispatch-skip", "  " + sibling[0]),
        ("tab-separated-registration", sibling[0].replace("run_gate ", "run_gate\t", 1)),
        ("trailing-comment", sibling[0].rstrip("\n") + " # comment\n"),
        ("non-python3", sibling[0].replace("python3", "sh", 1)),
        ("continuation", sibling[0].replace("python3 ", "python3 \\\n", 1)),
        ("unclosed-quote", sibling[0].rstrip("\n") + ' "\n'),
    )
    for label, line in grammar_cases:
        changed = skip.replace(sibling[0], line, 1)

        def read(path, *args, **kwargs):
            if path == runner:
                return changed
            return original_read(path, *args, **kwargs)

        # Exercise production's registered==source path, not only a candidate.
        with patch.object(Path, "read_text", read):
            red(label, lambda: _runner_check(expected), RuntimeError,
                identity + "/cannot-evaluate/grammar")
        red(label + "-candidate", lambda: _runner_check(expected, changed), RuntimeError,
            identity + "/cannot-evaluate/grammar")

    # Remove every execute bit, including for root. Permit only the probe:
    # a reverted interception guard must never launch the real runner.
    original_chmod = Path.chmod
    original_popen = subprocess.Popen
    launches = 0

    def non_executable(path, mode, *args, **kwargs):
        if path.name == "python3":
            mode = 0o600
        return original_chmod(path, mode, *args, **kwargs)

    def probe_only(*args, **kwargs):
        nonlocal launches
        launches += 1
        if launches > 1:
            raise AssertionError(identity + "/interception/unexpected-launch")
        return original_popen(*args, **kwargs)

    with patch.object(Path, "chmod", non_executable), \
            patch("subprocess.Popen", side_effect=probe_only):
        red("non-executable-fixture", lambda: _runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Discriminates only where TMPDIR has no default ACL and the process
    # lacks CAP_DAC_OVERRIDE (root commonly has it in CI containers).
    # Either a default ACL overriding umask or that capability makes this
    # case non-discriminating: it can pass with or without the chmods.
    saved = os.umask(0o200)
    try:
        try:
            _runner_check(expected)
        except Exception as exc:
            raise AssertionError(identity + "/umask-0200") from exc
    finally:
        os.umask(saved)
    print("PASS " + identity + "/umask-0200")

    # Block every launch so a reverted guard cannot execute a real gate.
    # Reset tempfile's cache as well as TMPDIR to exercise this exact directory.
    with tempfile.TemporaryDirectory(prefix="opf-path" + os.pathsep) as tmp:
        with patch.dict(os.environ, {"TMPDIR": tmp}), patch.object(tempfile, "tempdir", tmp):
            with patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/pathsep/unexpected-launch")) as launch:
                red("tmpdir-pathsep", lambda: _runner_check(expected), RuntimeError,
                    identity + "/cannot-evaluate/pathsep")
                if launch.call_count:
                    raise AssertionError(identity + "/pathsep/unexpected-launch")


def _runner_registration_test(expected):
    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    _runner_check(expected, source)
    print("PASS runner/pack-manifest-registration")
    lines = [line for line in source.splitlines(keepends=True)
             if line.startswith('run_gate "opf-pack-manifest-selftest"')]
    if len(lines) != 1:
        raise AssertionError("runner/pack-manifest-unique-registration")
    # Delete only this registration; a manifest refresh cannot repair the
    # dedicated assertion because it never reads the release manifest.
    try:
        _runner_check(expected, source.replace(lines[0], "", 1))
    except AssertionError as exc:
        if str(exc) != "runner/pack-manifest-registration/pass-lines":
            raise
    else:
        raise AssertionError("runner/pack-manifest-registration-not-red")
    print("RED own-dispatch -> runner/pack-manifest-registration/pass-lines")
    _runner_red_checks(expected)


def self_test(vectors_only=False):
    """0 success, 1 discriminator failure, 2 harness cannot-evaluate.

    Guard mutations are local, restored by mock.patch, and judged solely by
    the same status discriminator used for the unmodified parser. They prove
    sensitivity only for the explicitly enumerated guards, not all mutations.
    """
    from unittest.mock import patch

    failures = []
    executed = []

    def check(name, condition):
        executed.append(name)
        if not condition:
            failures.append(name)

    try:
        empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        check("TG-23/root-empty", compute_root(b"") == empty)
        check("TG-23/root-abc", compute_root(b"abc") ==
              "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        check("TG-23/tree-empty", compute_tree([]) == empty)
        check("TG-23/tree-row", compute_tree([{"path": "a", "sha256": "0" * 64}]) ==
              "70dd851c0eb5019ec6ecf0c6b0470392160f4dbaaa34fd07a6f32e0ff122f45e")
        check("TG-23/root-fixture", compute_root(_fixture()) ==
              "f1f587808aa56bb3b0efe155d37e99fa9bce284b3c5251c27ca61275bc799c7d")
        # Encoding/order vector includes UTF-8 and unequal, nonempty rows.
        rows = [{"path": "\u00e9", "sha256": "2" * 64},
                {"path": "z", "sha256": "1" * 64}]
        check("TG-23/tree-utf8-order", compute_tree(rows) ==
              "81a456976d598ff5643e1c54395e9f8f364a6578db971c7a5d3c95db3920022b")
        check("TG-19/positive", parse_root_txt(b"sha256:" + b"a" * 64 + b"\n") == "a" * 64)
        root_bad = (
            b"sha256:" + b"a" * 63 + b"\n",
            b"sha256:" + b"A" * 64 + b"\n",
            b"sha256:" + b"a" * 64,
            b"sha256:" + b"a" * 64 + b"\n\n",
            b"sha256:" + b"a" * 64 + b"\r\n",
            b"sha256:" + b"a" * 63 + b"\xff\n",
            b"sha512:" + b"a" * 64 + b"\n",
        )
        for index, raw in enumerate(root_bad):
            result = parse_root_txt(raw)
            check("TG-19/invalid-" + str(index),
                  isinstance(result, AdoptValidation) and result.status == INVALID)
        for raw in (None, "", bytearray(b"x")):
            result = parse_root_txt(raw)
            check("TG-19/type-" + type(raw).__name__,
                  isinstance(result, AdoptValidation) and result.status == CANNOT_EVALUATE)

        cases = _test_vectors()
        for case in cases:
            check("TG-24/" + case[0], _case_passes(case))
        by_name = {case[0]: case for case in cases}
        check("unique-case-names", len(by_name) == len(cases))

        # Inclusive caps, then one unit over; no huge allocations are needed.
        module = sys.modules[__name__]
        base = _fixture()
        for cap, boundary in (("MAX_FILE_BYTES", len(base)),
                              ("MAX_ENTRIES", 3), ("MAX_PATH_BYTES", 3)):
            with patch.object(module, cap, boundary):
                check(cap + "/at", _case_passes(("at", base, VALID)))
            with patch.object(module, cap, boundary - 1):
                check(cap + "/over", _case_passes(("over", base, INVALID)))
        for cap in ("MAX_FILE_BYTES", "MAX_ENTRIES", "MAX_PATH_BYTES", "MAX_DEPTH"):
            with patch.object(module, cap, True):
                check(cap + "/bad-control",
                      _case_passes(("control", base, CANNOT_EVALUATE)))
        # Separate source and artifact depth discriminators, still one field.
        for locus, old in (
            ("source", b'[[sources]]\npath = "a"'),
            ("artifact", b'artifact-id = "file:a"\npath = "a"'),
        ):
            changed = base.replace(old, old.replace(b'"a"', b'"a/b"'), 1)
            with patch.object(module, "MAX_DEPTH", 2):
                check(locus + "-depth/at", _case_passes(("depth", changed, VALID)))
            with patch.object(module, "MAX_DEPTH", 1):
                check(locus + "-depth/over", _case_passes(("depth", changed, INVALID)))
        utf8_paths = base.replace(b'path = "a"', 'path = "\\u00e9"'.encode("utf-8"))
        with patch.object(module, "MAX_PATH_BYTES", 6):
            check("utf8-path-budget/at", _case_passes(("utf8", utf8_paths, VALID)))
        with patch.object(module, "MAX_PATH_BYTES", 5):
            check("utf8-path-budget/over", _case_passes(("utf8", utf8_paths, INVALID)))
        with patch.object(tomllib, "loads", return_value=[]):
            check("top-container", _case_passes(("top", base, CANNOT_EVALUATE)))
        with patch.object(tomllib, "loads", side_effect=RecursionError):
            check("recursion", _case_passes(("recursion", base, CANNOT_EVALUATE)))
        with patch.object(tomllib, "loads", side_effect=OSError):
            check("unexpected-error", _case_passes(("error", base, CANNOT_EVALUATE)))
        with patch.object(tomllib, "loads", side_effect=KeyboardInterrupt):
            try:
                parse_manifest(base)
            except KeyboardInterrupt:
                check("cancellation", True)
            else:
                check("cancellation", False)

        # Deliberately remove exactly one named guard. The unchanged negative
        # fixture must now yield VALID AND its original discriminator go red.
        mutations = (
            ("manifest-keys", "unknown-top"),
            ("format-vocabulary", "format-2"),
            ("source-size", "size-true"),
            ("source-unique", "duplicate-source"),
            ("source-order", "unsorted-source"),
            ("artifact-kind", "unknown-kind"),
            ("artifact-unique", "duplicate-id"),
            ("source-artifact-consistency", "digest-conflict"),
            ("artifact-coverage-consistency", "coverage-conflict"),
        )
        original = _require
        for guard, name in mutations:
            def disabled(condition, candidate_guard, status=INVALID, skip=guard):
                if candidate_guard != skip:
                    original(condition, candidate_guard, status)
            with patch.object(module, "_require", disabled):
                case = by_name[name]
                _, result = parse_manifest(case[1])
                check("RED/" + guard,
                      result.status == VALID and not _case_passes(case))
        for cap, boundary, guard in (
            ("MAX_FILE_BYTES", len(base), "manifest-byte-bound"),
            ("MAX_ENTRIES", 3, "manifest-entry-bound"),
            ("MAX_PATH_BYTES", 3, "manifest-path-byte-bound"),
        ):
            def disabled(condition, candidate_guard, status=INVALID, skip=guard):
                if candidate_guard != skip:
                    original(condition, candidate_guard, status)
            with patch.object(module, cap, boundary - 1):
                with patch.object(module, "_require", disabled):
                    check("RED/" + guard,
                          _case_passes(("disabled", base, VALID))
                          and not _case_passes(("over", base, INVALID)))
        if not vectors_only and not failures:
            _runner_registration_test(executed)
    except AssertionError as exc:
        print("PACK-MANIFEST FAIL:", str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print("PACK-MANIFEST CANNOT_EVALUATE:", type(exc).__name__, str(exc),
              file=sys.stderr)
        return 2
    print("PACK-MANIFEST", {"executed": executed, "failures": failures})
    return int(bool(failures))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--vectors-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.self_test:
        parser.error("--self-test is required; use the library for parsing")
    return self_test(vectors_only=args.vectors_only)


if __name__ == "__main__":
    sys.exit(main())
