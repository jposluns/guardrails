#!/usr/bin/env python3
"""The implementation declaration of the OPF reference tooling (spec 16.1): its runtime check and its emitter.

Each release ships its declaration (its release identity and the spec_version, homes generation and worklog
storage generation it supports) as one file, the declaration file DECLARATION_NAME, beside opf.py. Apart from
that file, this module carries the SHA-256 digest of that file's bytes as the release ships them, the
shipped-file digest SHIPPED_DECLARATION_SHA256, and the release documentation states that place
(opf/site/tooling.html, opf/spec/OPF-QUICKSTART.md and RELEASING.md; tools/check_opf_declaration.py holds
each restatement byte-equal to the file).

runtime_check(path) is the runtime check. It reads the installed copy at `path` alone and yields one state:

  absent           the path names nothing (ENOENT)                                   cannot-evaluate, not-read
  unreadable       any other open or read failure, a symlink (never followed), a     cannot-evaluate, not-read
                   directory, FIFO or other non-regular file, or more than
                   MAX_DECLARATION_BYTES bytes
  digest-mismatch  the bytes differ from SHIPPED_DECLARATION_SHA256, compared        cannot-evaluate, digest
                   before any field is read
  malformed        not exactly the four fields, a release or spec_version that is    cannot-evaluate, digest
                   not a bare SemVer string, a homes or worklog that is not an
                   integer (a bool is not one), or bytes other than the canonical
                   rendering of those values (declaration_bytes)
  contradictory    a value other than RELEASE_IDENTITY or this tooling's supported   cannot-evaluate, digest
                   _opf_store.SPEC_VERSION, HOMES or SUPPORTED_WORKLOG
  ok               none of the above                                                 authorizes, digest

opf.py runs it before dispatching every verb that can touch a store (render, doctor, init, absorb, record and
adopt); a state other than ok refuses that verb at exit 2 before it resolves any store. upgrade and the
not-yet-implemented verbs read and write nothing, and the read-only import surface (spec 14.1) writes nothing,
so it performs no store operation; none of them is gated. The malformed and contradictory states are
reachable only on bytes whose digest equals the shipped one, so for a released build they are fixed when the
release is cut; the vectors below reach them by patching the digest.

emit_report(check, store_operations_performed, stream) is the emitter: it writes the four parts spec 16.1
compares, the restatement (SHIPPED_DECLARATION_BYTES verbatim, never the installed copy, so it equals the file
as shipped even on a digest mismatch), the shipped-file digest, the installed-copy digest (64 lowercase hex
digits, or not-read) and the runtime result (cannot_evaluate exactly where the check did not yield ok, else pass
where the run performed a store operation and fail where it did not). `opf.py --declaration` and `opf doctor`
write it; the writer verbs do not yet.

Residual, disclosed by spec 16.1 and section 17: an edit of both the declaration file and the digest here goes
undetected; the comparison detects an edit of the installed copy alone.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_declaration.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import io
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_store  # noqa: E402
from _semver import _parse as _parse_version  # noqa: E402

DECLARATION_NAME = "opf-declaration.toml"
# The release identity this build carries apart from the declaration file: the pack release version (VERSION
# and the release-version of .aiqt/manifest.toml), which tools/check_opf_declaration.py pins equal to it.
RELEASE_IDENTITY = "1.0.5"
# The declaration file's bytes as this release ships them, and their SHA-256 digest (the shipped-file digest).
SHIPPED_DECLARATION_BYTES = b'release = "1.0.5"\nspec_version = "2.0.0"\nhomes = 2\nworklog = 1\n'
SHIPPED_DECLARATION_SHA256 = "4b448a4b30fa2358ce1b19587897276d0d79ffc43a4db89355105eb4a739db4f"
MAX_DECLARATION_BYTES = 4096
FIELDS = ("release", "spec_version", "homes", "worklog")
NOT_READ = "not-read"

OK = "ok"
ABSENT = "absent"
UNREADABLE = "unreadable"
DIGEST_MISMATCH = "digest-mismatch"
MALFORMED = "malformed"
CONTRADICTORY = "contradictory"

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_CANNOT_EVALUATE = "cannot_evaluate"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class Check:
    """One runtime check's outcome: its state, the installed-copy digest it read (NOT_READ where it read
    none), the bytes it read (None where it read none) and a located detail."""
    __slots__ = ("state", "installed_sha256", "data", "detail")

    def __init__(self, state, installed_sha256, data=None, detail=""):
        self.state = state
        self.installed_sha256 = installed_sha256
        self.data = data
        self.detail = detail

    @property
    def ok(self):
        return self.state == OK


class _Unreadable(Exception):
    """The installed copy opened but is no regular file of at most MAX_DECLARATION_BYTES bytes."""


def declaration_bytes(release, spec_version, homes, worklog):
    """The canonical rendering of a declaration: the four fields in FIELDS order, one per line, LF-ended."""
    return 'release = "{}"\nspec_version = "{}"\nhomes = {}\nworklog = {}\n'.format(
        release, spec_version, homes, worklog).encode("ascii")


def _read_installed(path):
    """The installed copy's bytes, read no-follow and nonblocking (a symlink refuses with ELOOP and a FIFO
    opens at once), from a regular file of at most MAX_DECLARATION_BYTES bytes, checked on the open
    descriptor and again while reading. FileNotFoundError means absent; any other OSError or _Unreadable
    means unreadable."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise _Unreadable("not a regular file (mode {:o})".format(st.st_mode))
        if st.st_size > MAX_DECLARATION_BYTES:
            raise _Unreadable("larger than {} bytes ({})".format(MAX_DECLARATION_BYTES, st.st_size))
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, MAX_DECLARATION_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_DECLARATION_BYTES:
                raise _Unreadable("grew past {} bytes while read".format(MAX_DECLARATION_BYTES))
        return b"".join(chunks)
    finally:
        os.close(fd)


def parse_declaration(data):
    """(fields, None) for bytes that are a well-formed declaration, else (None, the rule they break). Applies
    the malformed rules only; it never compares the values with this build (runtime_check does)."""
    try:
        table = tomllib.loads(data.decode("ascii"))
    except (RecursionError, MemoryError, ValueError) as exc:   # a decode or TOML error is a ValueError
        return None, "is not an ASCII TOML table ({})".format(type(exc).__name__)
    missing = [key for key in FIELDS if key not in table]
    if missing:
        return None, "omits {}".format(", ".join(missing))
    extra = sorted(set(table) - set(FIELDS))
    if extra:
        return None, "states a field the declaration does not carry: {}".format(", ".join(extra))
    for key in ("release", "spec_version"):
        if type(table[key]) is not str or _parse_version(table[key]) is None:
            return None, "{} is not a SemVer string ({!r})".format(key, table[key])
    for key in ("homes", "worklog"):
        if type(table[key]) is not int:
            return None, "{} is not an integer ({!r})".format(key, table[key])
    if declaration_bytes(table["release"], table["spec_version"], table["homes"], table["worklog"]) != data:
        return None, "is not in the canonical form (four lines in fixed order, no comment, blank line or variant)"
    return table, None


def _contradiction(fields):
    """The fields whose value contradicts this build, as one detail, or "" where none does."""
    expected = (
        ("release", RELEASE_IDENTITY),
        ("spec_version", _opf_store.SPEC_VERSION),
        ("homes", _opf_store.HOMES),
        ("worklog", _opf_store.SUPPORTED_WORKLOG),
    )
    return "; ".join("{} = {!r}, but this build carries {!r}".format(key, fields[key], value)
                     for key, value in expected if fields[key] != value)


def runtime_check(path):
    """Run the spec 16.1 runtime check over the installed copy at `path` (module docstring: the states)."""
    try:
        data = _read_installed(path)
    except FileNotFoundError:
        return Check(ABSENT, NOT_READ, None, "no declaration file at {!r}".format(str(path)))
    except (OSError, _Unreadable) as exc:
        return Check(UNREADABLE, NOT_READ, None, "{!r}: {}".format(str(path), exc))
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHIPPED_DECLARATION_SHA256:
        return Check(DIGEST_MISMATCH, digest, data, "the installed copy's sha256 {} is not the shipped-file "
                     "digest {}".format(digest, SHIPPED_DECLARATION_SHA256))
    fields, problem = parse_declaration(data)
    if problem is not None:
        return Check(MALFORMED, digest, data, "the declaration " + problem)
    contradiction = _contradiction(fields)
    if contradiction:
        return Check(CONTRADICTORY, digest, data, contradiction)
    return Check(OK, digest, data, "")


def refusal_line(verb, check):
    """The one stderr line a verb the check refuses prints."""
    return ("opf {}: cannot evaluate: implementation declaration {} (spec 16.1); no store operation was "
            "performed".format(verb, check.state))


def runtime_result(check, store_operations_performed):
    """The spec 16.1 runtime-result token of a run."""
    if not check.ok:
        return RESULT_CANNOT_EVALUATE
    return RESULT_PASS if store_operations_performed else RESULT_FAIL


def emit_report(check, store_operations_performed, stream):
    """Write the four compared parts of a spec 16.1 report to `stream`. Raises ValueError, writing nothing,
    for a digest outside the report's form or for not-read beside a result other than cannot_evaluate."""
    result = runtime_result(check, store_operations_performed)
    if not _DIGEST_RE.match(SHIPPED_DECLARATION_SHA256):
        raise ValueError("the shipped-file digest {!r} is not 64 lowercase hex digits".format(
            SHIPPED_DECLARATION_SHA256))
    if check.installed_sha256 != NOT_READ and not _DIGEST_RE.match(check.installed_sha256):
        raise ValueError("the installed-copy digest {!r} is neither 64 lowercase hex digits nor {}".format(
            check.installed_sha256, NOT_READ))
    if check.installed_sha256 == NOT_READ and result != RESULT_CANNOT_EVALUATE:
        raise ValueError("a report carries {} only beside {}, not {}".format(NOT_READ, RESULT_CANNOT_EVALUATE,
                                                                           result))
    stream.write("[declaration]\n" + SHIPPED_DECLARATION_BYTES.decode("ascii") + "\n[runtime]\n"
                 'shipped_file_sha256 = "{}"\ninstalled_copy_sha256 = "{}"\nruntime_result = "{}"\n'.format(
                     SHIPPED_DECLARATION_SHA256, check.installed_sha256, result))


def report_text(check, store_operations_performed):
    """emit_report's output as a string."""
    out = io.StringIO()
    emit_report(check, store_operations_performed, out)
    return out.getvalue()


# --- self-test vectors -------------------------------------------------------------------------------------
# Each vector takes a fresh scratch directory and returns (ok, detail). self_test runs them all;
# tools/check_opf_declaration.py runs each under its own registered check id and shows it red under its revert.

def _write(directory, data, name=DECLARATION_NAME):
    path = Path(directory) / name
    path.write_bytes(data)
    return path


def _with_shipped_digest(data, fn):
    """Run fn with the shipped-file digest patched to the digest of `data` (the malformed and contradictory
    states are reachable only on bytes that pass the digest step), restoring it after."""
    global SHIPPED_DECLARATION_SHA256
    saved = SHIPPED_DECLARATION_SHA256
    SHIPPED_DECLARATION_SHA256 = hashlib.sha256(data).hexdigest()
    try:
        return fn()
    finally:
        SHIPPED_DECLARATION_SHA256 = saved


def _variant(**fields):
    values = dict(release=RELEASE_IDENTITY, spec_version=_opf_store.SPEC_VERSION,
                  homes=_opf_store.HOMES, worklog=_opf_store.SUPPORTED_WORKLOG)
    values.update(fields)
    return declaration_bytes(**values)


def _without(key):
    return b"".join(line for line in SHIPPED_DECLARATION_BYTES.splitlines(keepends=True)
                    if not line.startswith(key.encode("ascii") + b" ="))


def _state(check, state, digest):
    return (check.state == state and check.installed_sha256 == digest,
            (check.state, check.installed_sha256, check.detail))


def _v_shipped_digest_matches_file(tmp):
    data = (Path(__file__).resolve().parent / DECLARATION_NAME).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return (digest == SHIPPED_DECLARATION_SHA256 == hashlib.sha256(SHIPPED_DECLARATION_BYTES).hexdigest()
            and data == SHIPPED_DECLARATION_BYTES, (digest, SHIPPED_DECLARATION_SHA256))


def _v_valid_authorizes(tmp):
    check = runtime_check(_write(tmp, SHIPPED_DECLARATION_BYTES))
    return _state(check, OK, SHIPPED_DECLARATION_SHA256)


def _v_absent_not_read(tmp):
    return _state(runtime_check(Path(tmp) / DECLARATION_NAME), ABSENT, NOT_READ)


def _v_unreadable_directory(tmp):
    path = Path(tmp) / DECLARATION_NAME
    path.mkdir()
    check = runtime_check(path)
    ok, detail = _state(check, UNREADABLE, NOT_READ)
    return ok and "not a regular file" in check.detail, detail


def _v_unreadable_fifo(tmp):
    path = Path(tmp) / DECLARATION_NAME
    os.mkfifo(path)
    return _state(runtime_check(path), UNREADABLE, NOT_READ)


def _v_unreadable_symlink(tmp):
    target = _write(tmp, SHIPPED_DECLARATION_BYTES, "target.toml")
    path = Path(tmp) / DECLARATION_NAME
    path.symlink_to(target)
    return _state(runtime_check(path), UNREADABLE, NOT_READ)


def _v_unreadable_oversize(tmp):
    return _state(runtime_check(_write(tmp, b"#" * 4097)), UNREADABLE, NOT_READ)


def _v_size_cap_boundary(tmp):
    # The cap is 4096 bytes, written here as literals so a changed cap is red: just inside and exactly at it
    # the file is read (and differs from the shipped digest), just outside it the file is not read.
    rows = []
    for size, state in ((4095, DIGEST_MISMATCH), (4096, DIGEST_MISMATCH), (4097, UNREADABLE)):
        data = b"#" * size
        check = runtime_check(_write(tmp, data))
        digest = NOT_READ if state == UNREADABLE else hashlib.sha256(data).hexdigest()
        rows.append((size, check.state == state and check.installed_sha256 == digest, check.state))
    return all(ok for _size, ok, _got in rows), rows


def _v_crlf_is_digest_mismatch(tmp):
    data = SHIPPED_DECLARATION_BYTES.replace(b"\n", b"\r\n")
    return _state(runtime_check(_write(tmp, data)), DIGEST_MISMATCH, hashlib.sha256(data).hexdigest())


def _v_field_edit_is_digest_mismatch(tmp):
    data = _variant(homes=_opf_store.HOMES + 1)
    return _state(runtime_check(_write(tmp, data)), DIGEST_MISMATCH, hashlib.sha256(data).hexdigest())


def _malformed(tmp, data, phrase):
    check = _with_shipped_digest(data, lambda: runtime_check(_write(tmp, data)))
    ok, detail = _state(check, MALFORMED, hashlib.sha256(data).hexdigest())
    return ok and phrase in check.detail, detail


def _v_malformed_missing_release(tmp):
    return _malformed(tmp, _without("release"), "omits release")


def _v_malformed_missing_spec_version(tmp):
    return _malformed(tmp, _without("spec_version"), "omits spec_version")


def _v_malformed_missing_homes(tmp):
    return _malformed(tmp, _without("homes"), "omits homes")


def _v_malformed_missing_worklog(tmp):
    return _malformed(tmp, _without("worklog"), "omits worklog")


def _v_malformed_extra_key(tmp):
    return _malformed(tmp, SHIPPED_DECLARATION_BYTES + b'class = "full"\n', "states a field")


def _v_malformed_bool_homes(tmp):
    return _malformed(tmp, SHIPPED_DECLARATION_BYTES.replace(b"homes = 2", b"homes = true"),
                      "homes is not an integer")


def _v_malformed_bad_semver(tmp):
    data = _variant(release="1.0")
    return _malformed(tmp, data, "release is not a SemVer string")


def _v_malformed_unparseable(tmp):
    return _malformed(tmp, SHIPPED_DECLARATION_BYTES + b"release =\n", "is not an ASCII TOML table")


def _v_malformed_comment(tmp):
    return _malformed(tmp, SHIPPED_DECLARATION_BYTES + b"# a comment\n", "is not in the canonical form")


def _contradictory(tmp, data, key):
    check = _with_shipped_digest(data, lambda: runtime_check(_write(tmp, data)))
    ok, detail = _state(check, CONTRADICTORY, hashlib.sha256(data).hexdigest())
    return ok and check.detail.startswith(key + " = "), detail


def _v_contradictory_release(tmp):
    return _contradictory(tmp, _variant(release="9.9.9"), "release")


def _v_contradictory_spec_version(tmp):
    return _contradictory(tmp, _variant(spec_version="9.9.9"), "spec_version")


def _v_contradictory_homes(tmp):
    return _contradictory(tmp, _variant(homes=_opf_store.HOMES + 1), "homes")


def _v_contradictory_worklog(tmp):
    return _contradictory(tmp, _variant(worklog=_opf_store.SUPPORTED_WORKLOG + 1), "worklog")


def _expected_report(installed, result):
    return ("[declaration]\n" + SHIPPED_DECLARATION_BYTES.decode("ascii") + "\n[runtime]\n"
            'shipped_file_sha256 = "{}"\ninstalled_copy_sha256 = "{}"\nruntime_result = "{}"\n'.format(
                SHIPPED_DECLARATION_SHA256, installed, result))


def _v_report_four_parts(tmp):
    text = report_text(runtime_check(_write(tmp, SHIPPED_DECLARATION_BYTES)), False)
    return text == _expected_report(SHIPPED_DECLARATION_SHA256, RESULT_FAIL), text


def _v_report_pass_with_store_operation(tmp):
    text = report_text(runtime_check(_write(tmp, SHIPPED_DECLARATION_BYTES)), True)
    return text == _expected_report(SHIPPED_DECLARATION_SHA256, RESULT_PASS), text


def _v_report_not_read_pairs_cannot_evaluate(tmp):
    text = report_text(runtime_check(Path(tmp) / DECLARATION_NAME), True)
    try:
        report_text(Check(OK, NOT_READ), False)
        raised = False
    except ValueError:
        raised = True
    return text == _expected_report(NOT_READ, RESULT_CANNOT_EVALUATE) and raised, (text, raised)


def _v_report_restates_shipped_on_mismatch(tmp):
    data = _variant(homes=_opf_store.HOMES + 1)
    text = report_text(runtime_check(_write(tmp, data)), False)
    return text == _expected_report(hashlib.sha256(data).hexdigest(), RESULT_CANNOT_EVALUATE), text


def _v_report_digest_form(tmp):
    text = report_text(runtime_check(_write(tmp, SHIPPED_DECLARATION_BYTES)), False)
    digests = re.findall(r'_sha256 = "([^"]*)"', text)
    form = len(digests) == 2 and all(_DIGEST_RE.match(digest) for digest in digests)
    try:
        report_text(Check(OK, "sha256:" + SHIPPED_DECLARATION_SHA256), False)
        raised = False
    except ValueError:
        raised = True
    return form and raised, (digests, raised)


def _v_declaration_tracks_constants(tmp):
    fields, problem = parse_declaration(SHIPPED_DECLARATION_BYTES)
    want = dict(release=RELEASE_IDENTITY, spec_version=_opf_store.SPEC_VERSION,
                homes=_opf_store.HOMES, worklog=_opf_store.SUPPORTED_WORKLOG)
    return problem is None and fields == want, (fields, problem, want)


VECTORS = (
    ("shipped-digest-matches-file", _v_shipped_digest_matches_file),
    ("valid-authorizes", _v_valid_authorizes),
    ("absent-not-read", _v_absent_not_read),
    ("unreadable-directory", _v_unreadable_directory),
    ("unreadable-fifo", _v_unreadable_fifo),
    ("unreadable-symlink", _v_unreadable_symlink),
    ("unreadable-oversize", _v_unreadable_oversize),
    ("size-cap-boundary", _v_size_cap_boundary),
    ("crlf-is-digest-mismatch", _v_crlf_is_digest_mismatch),
    ("field-edit-is-digest-mismatch", _v_field_edit_is_digest_mismatch),
    ("malformed-missing-release", _v_malformed_missing_release),
    ("malformed-missing-spec_version", _v_malformed_missing_spec_version),
    ("malformed-missing-homes", _v_malformed_missing_homes),
    ("malformed-missing-worklog", _v_malformed_missing_worklog),
    ("malformed-extra-key", _v_malformed_extra_key),
    ("malformed-bool-homes", _v_malformed_bool_homes),
    ("malformed-bad-semver", _v_malformed_bad_semver),
    ("malformed-unparseable", _v_malformed_unparseable),
    ("malformed-comment", _v_malformed_comment),
    ("contradictory-release", _v_contradictory_release),
    ("contradictory-spec_version", _v_contradictory_spec_version),
    ("contradictory-homes", _v_contradictory_homes),
    ("contradictory-worklog", _v_contradictory_worklog),
    ("report-four-parts", _v_report_four_parts),
    ("report-pass-with-store-operation", _v_report_pass_with_store_operation),
    ("report-not-read-pairs-cannot-evaluate", _v_report_not_read_pairs_cannot_evaluate),
    ("report-restates-shipped-on-mismatch", _v_report_restates_shipped_on_mismatch),
    ("report-digest-form", _v_report_digest_form),
    ("declaration-tracks-constants", _v_declaration_tracks_constants),
)


def run_vector(name):
    """Run the vector `name` in a fresh scratch directory: (ok, detail). An exception the vector lets escape
    is a failure of that vector, named in its detail."""
    fn = dict(VECTORS)[name]
    with tempfile.TemporaryDirectory(prefix="opf-declaration-") as tmp:
        try:
            ok, detail = fn(tmp)
        except Exception as exc:  # noqa: BLE001  an escaping error is that vector's failure, never a pass
            return False, "raised {!r}".format(exc)
    return ok is True, detail


def self_test():
    failures = []
    for name, _fn in VECTORS:
        ok, detail = run_vector(name)
        if not ok:
            failures.append((name, detail))
    if failures:
        print("opf declaration self-test: FAIL ({} of {} vectors)".format(len(failures), len(VECTORS)),
              file=sys.stderr)
        for name, detail in failures:
            print("  {}: {!r}".format(name, detail), file=sys.stderr)
        return 1
    print("opf declaration self-test: PASS ({} vectors)".format(len(VECTORS)))
    return 0


def main():
    print("usage: _opf_declaration.py --self-test", file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. No path of this file launches a child other than
# the guard's own probe: it reads only constants from _opf_store, and the launches in its local import closure are
# in code of _opf_store and the modules that imports, which this file never calls. The guard is here because that
# gate classifies an entry point by a static scan of its whole local import closure, which counts those launches,
# so without it this file would be a new direct-launch residual, a finding; with it, the entry line refuses an
# ignored SIGCHLD before main() runs. The --self-test statement stays first in the entry, where opf.py's
# aggregator requires it: self_test() also runs in process under opf.py --self-test, behind opf.py's own guard, so
# it carries no guard call, and its vectors launch no child (that gate's self-test runs it under a
# process-creation audit hook that records the _posixsubprocess.fork_exec event too, so the multiprocessing
# launches are recorded, and records an import of _interpreters as a launch, as the hook cannot see a
# subinterpreter's own launches; the hook writes each launch as it happens to a private record stream, a pipe the
# self-test passes to the driver, which holds it at a descriptor of its own before the self-test runs and checks
# that descriptor's identity at each launch, so a launch while fd 1 or fd 2 is closed or points at /dev/null is
# recorded too, and a launch whose record cannot be written ends the run with a failing status before that launch
# runs; the report prints on stdout at exit, after the exit handlers and thread joins, and the self-test fails on
# a launch in either, so a launch from a finalizer as the modules are torn down, after the report, fails it too;
# the audit records accidental launches by the audited self-test until the interpreter clears its audit hooks late
# in teardown (a finalizer that runs after that, as one reached only from an interpreter-state registry does: the
# codec search and error registries, at-fork hooks, sys.monitoring callbacks, gc callbacks, warnings filters, is
# not seen); a launch made below Python (a native extension, ctypes, a Tcl exec through tkinter) is not recorded;
# and code that deliberately evades an in-process hook (by any means, among them clearing the hooks below Python,
# reading the record back from the record descriptor (for example through /proc/self/fd), rewriting the driver's
# own state so the record goes elsewhere, which closing or redirecting the record descriptor alone does not do, as
# that ends the run, forging or unregistering the report, a subinterpreter not made through _interpreters) is
# outside what it can see).
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
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("_opf_declaration.py") or main())
