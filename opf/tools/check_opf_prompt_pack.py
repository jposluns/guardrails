#!/usr/bin/env python3
"""OPF prompt-pack manifest and digest drift gate (PR7 slice 1).

The prompt pack is one versioned, digest-bound document set under opf/prompt-pack/. Its manifest,
opf/prompt-pack/pack.toml, has a closed schema:

  format   "opf.prompt-pack/v1"
  version  bare SemVer MAJOR.MINOR.PATCH (the shared _semver grammar; no prefix, pre-release or build)
  digest   "sha256:" + 64 lowercase hex, the pack digest defined below
  members  an array of {path, sha256} rows (sha256 is 64 lowercase hex), possibly empty

Member paths are relative to the pack directory, contained, unique and in ascending code-point order,
and pack.toml cannot list itself. Every regular file under the pack directory other than pack.toml
must be a member, so no document ships beside the pack outside its digest. A symlink or other
non-regular entry anywhere in the pack directory is CANNOT-EVALUATE.

The pack digest is sha256 over the UTF-8 bytes of "opf.prompt-pack/v1\\n", then "version <version>\\n",
then one "member <sha256> <path>\\n" line per member in order. It binds the version and each member's
exact bytes; TOML layout and comments never participate.

  check_opf_prompt_pack.py              verify the shipped pack beside this tool (../prompt-pack)
  check_opf_prompt_pack.py --self-test  synthetic packs assert each guard's exact status and guard name

Exit convention (matches the repo's gates): 0 VALID, 1 INVALID (a finding, such as digest drift),
2 CANNOT-EVALUATE (unreadable, unparseable, out-of-vocabulary format, non-regular entry, over a bound).

Inert: nothing consumes the pack in this slice; binding a plan's prompt_pack {version, digest} to this
pack is a later slice. VALID means the manifest agrees with the bytes on disk. It is not
authentication, it does not check that a content change also bumped the version, and it does not
review what the documents say.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_prompt_pack.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _opf_adopt import (  # noqa: E402
    VALID, INVALID, CANNOT_EVALUATE, _DIGEST_RE, _is_contained_filepath,
)
from _semver import _parse as _parse_version  # noqa: E402

PACK_FORMAT = "opf.prompt-pack/v1"
MANIFEST_NAME = "pack.toml"
TOP_KEYS = frozenset({"format", "version", "digest", "members"})
MEMBER_KEYS = frozenset({"path", "sha256"})
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 1000
_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
EXIT_CODES = {VALID: 0, INVALID: 1, CANNOT_EVALUATE: 2}
DEFAULT_PACK_DIR = Path(__file__).resolve().parent.parent / "prompt-pack"


class _Refusal(Exception):
    def __init__(self, status, guard, detail):
        super().__init__(detail)
        self.status = status
        self.guard = guard


def _require(condition, guard, detail, status=INVALID):
    """Each named guard is also the discriminator the self-test asserts."""
    if not condition:
        raise _Refusal(status, guard, detail)


def compute_digest(version, members):
    """The pack digest over the version and the ordered member rows."""
    lines = [PACK_FORMAT, "version " + version]
    lines += ["member {} {}".format(m["sha256"], m["path"]) for m in members]
    return "sha256:" + hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).hexdigest()


def _close_fd_exc_safe(fd):
    """_opf_store._close_fd_exc_safe without the store import: a failing close never replaces an
    exception in flight in the CALLING frame, and still propagates fail-closed when none is, including
    under an exception a caller is merely handling. One os.close either way (P1): a raising close has
    released the number (close(2)), which is never probed or closed again."""
    tb = sys.exc_info()[2]
    in_flight = tb is not None and tb.tb_frame is sys._getframe(1)
    try:
        os.close(fd)
    except OSError:
        if not in_flight:
            raise


def _read_regular(path, limit, what):
    """Exact bytes of a regular file, never following a final symlink, bounded by limit. The file object is
    made with closefd=False, so it never closes fd (not when os.fdopen fails after creating its raw file and
    closes that file, nor when a dropped object is collected); the finally's close is the one close of fd
    on every path, so who closes fd is never in doubt (P1, #378). That close is _close_fd_exc_safe's (#377):
    a failing close never replaces an exception in flight here, and fails closed on the normal path."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as exc:
        raise _Refusal(CANNOT_EVALUATE, what + "-read", "{} unreadable: {}".format(what, exc))
    try:
        st = os.fstat(fd)
        _require(stat.S_ISREG(st.st_mode), what + "-read",
                 "{} {!s} is not a regular file".format(what, path),
                 CANNOT_EVALUATE)
        _require(st.st_size <= limit, what + "-bound", what + " exceeds the size bound",
                 CANNOT_EVALUATE)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
    finally:
        _close_fd_exc_safe(fd)
    _require(len(data) <= limit, what + "-bound", what + " exceeds the size bound", CANNOT_EVALUATE)
    return data


def _walk_files(pack_dir):
    """Every regular file under pack_dir as a sorted list of '/'-joined relative paths."""
    found = []

    def _raise(exc):
        raise _Refusal(CANNOT_EVALUATE, "pack-walk", "pack directory unreadable: {}".format(exc))

    for top, dirs, files in os.walk(pack_dir, onerror=_raise, followlinks=False):
        for name in sorted(dirs + files):
            full = os.path.join(top, name)
            rel = os.path.relpath(full, pack_dir).replace(os.sep, "/")
            try:
                rel.encode("utf-8")
                mode = os.lstat(full).st_mode
            except (UnicodeError, OSError) as exc:
                raise _Refusal(CANNOT_EVALUATE, "pack-walk", "unreadable entry {!r}: {}".format(rel, exc))
            if stat.S_ISDIR(mode):
                continue
            _require(stat.S_ISREG(mode), "pack-walk",
                     "non-regular entry in the pack directory: {!r}".format(rel), CANNOT_EVALUATE)
            found.append(rel)
    return sorted(found)


def _validate(pack_dir):
    try:
        mode = os.lstat(pack_dir).st_mode
    except OSError as exc:
        raise _Refusal(CANNOT_EVALUATE, "pack-dir", "pack directory unreadable: {}".format(exc))
    _require(stat.S_ISDIR(mode), "pack-dir", "pack directory is not a real directory", CANNOT_EVALUATE)
    raw = _read_regular(pack_dir / MANIFEST_NAME, MAX_MANIFEST_BYTES, "manifest")
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _Refusal(CANNOT_EVALUATE, "manifest-parse", "pack.toml does not parse: {}".format(exc))

    keys = set(doc)
    _require(keys <= TOP_KEYS, "top-keys", "unknown manifest key(s): {}".format(sorted(keys - TOP_KEYS)))
    _require(keys >= TOP_KEYS, "top-keys", "missing manifest key(s): {}".format(sorted(TOP_KEYS - keys)))
    fmt = doc["format"]
    _require(type(fmt) is str, "format", "format is not a string")
    _require(fmt == PACK_FORMAT, "format", "unsupported pack format {!r}".format(fmt), CANNOT_EVALUATE)
    version = doc["version"]
    _require(type(version) is str and _parse_version(version) is not None, "version",
             "version {!r} is not a bare MAJOR.MINOR.PATCH token".format(version))
    recorded = doc["digest"]
    _require(type(recorded) is str and _DIGEST_RE.match(recorded) is not None, "digest-shape",
             "digest is not sha256: plus 64 lowercase hex")

    members = doc["members"]
    _require(type(members) is list, "members-shape", "members is not an array")
    _require(len(members) <= MAX_MEMBERS, "members-bound", "too many members", CANNOT_EVALUATE)
    paths = []
    for row in members:
        _require(type(row) is dict and set(row) == MEMBER_KEYS, "member-shape",
                 "a member row is not exactly path and sha256")
        path, digest = row["path"], row["sha256"]
        _require(type(path) is str and _is_contained_filepath(path), "member-path",
                 "member path {!r} is not a contained relative file path".format(path))
        _require(path != MANIFEST_NAME, "member-path", "pack.toml cannot list itself")
        _require(type(digest) is str and _HEX_RE.match(digest) is not None, "member-sha256-shape",
                 "member {!r} sha256 is not 64 lowercase hex".format(path))
        _require(path not in paths, "member-duplicate", "member {!r} is listed twice".format(path))
        _require(not paths or paths[-1] < path, "member-order", "member rows are not in ascending order")
        paths.append(path)

    on_disk = [p for p in _walk_files(pack_dir) if p != MANIFEST_NAME]
    missing = sorted(set(paths) - set(on_disk))
    _require(not missing, "member-missing", "listed member(s) absent: {}".format(missing))
    unlisted = sorted(set(on_disk) - set(paths))
    _require(not unlisted, "unlisted-file", "file(s) in the pack directory not listed: {}".format(unlisted))
    for row in members:
        data = _read_regular(pack_dir / row["path"], MAX_MEMBER_BYTES, "member")
        actual = hashlib.sha256(data).hexdigest()
        _require(actual == row["sha256"], "member-sha256",
                 "member {!r} bytes drifted: recorded {}, actual {}".format(row["path"], row["sha256"], actual))
    computed = compute_digest(version, members)
    _require(computed == recorded, "pack-digest",
             "pack digest drifted: recorded {}, computed {}".format(recorded, computed))
    return version, recorded, len(members)


def validate_pack(pack_dir):
    """(status, guard, detail) for the pack at pack_dir. Reads only; writes nothing."""
    try:
        version, digest, count = _validate(Path(pack_dir))
    except _Refusal as refusal:
        return refusal.status, refusal.guard, str(refusal)
    return VALID, None, "version {}, digest {}, {} member(s)".format(version, digest, count)


# ---- self-test ------------------------------------------------------------------------------------


def _rows_digest(version, rows):
    return compute_digest(version, [dict(path=p, sha256=h) for p, h in rows])


def _manifest_text(version, rows, digest=None, fmt=PACK_FORMAT, extra=""):
    """A pack.toml for rows [(path, sha256)], with the correct digest unless one is given."""
    if digest is None:
        digest = _rows_digest(version, rows)
    head = "format = {}\nversion = {}\ndigest = {}\n{}".format(
        json.dumps(fmt), json.dumps(version), json.dumps(digest), extra)
    if not rows:
        return head + "members = []\n"
    body = "".join("\n[[members]]\npath = {}\nsha256 = {}\n".format(json.dumps(p), json.dumps(h))
                   for p, h in rows)
    return head + body


def _sha(data):
    return hashlib.sha256(data).hexdigest()


_DOCS = dict([("adoption-sop.md", b"# Adoption\n\nStep one.\n"),
              ("examples/worklog.toml", b"id = 'WL-1'\n")])


def _vectors():
    """(name, expected status, expected guard, builder(pack_dir)). Each builder writes one pack."""
    good = sorted((p, _sha(b)) for p, b in _DOCS.items())

    def write(pack, docs=_DOCS, manifest=None):
        for rel, data in docs.items():
            (pack / rel).parent.mkdir(parents=True, exist_ok=True)
            (pack / rel).write_bytes(data)
        if manifest is not None:
            (pack / MANIFEST_NAME).write_bytes(manifest if type(manifest) is bytes else manifest.encode())

    def std(pack, version="1.0.0", rows=None, **kw):
        write(pack, manifest=_manifest_text(version, good if rows is None else rows, **kw))

    def edited(pack):
        std(pack)
        (pack / "adoption-sop.md").write_bytes(b"# Adoption\n\nStep One.\n")

    def sha_only(pack):
        new = b"# Adoption\n\nStep two.\n"
        rows = sorted([("adoption-sop.md", _sha(new)),
                       ("examples/worklog.toml", _sha(_DOCS["examples/worklog.toml"]))])
        write(pack, manifest=_manifest_text("1.0.0", rows, digest=_rows_digest("1.0.0", good)))
        (pack / "adoption-sop.md").write_bytes(new)

    def bumped(pack):
        write(pack, manifest=_manifest_text("1.1.0", good, digest=_rows_digest("1.0.0", good)))

    def stray(pack):
        std(pack)
        (pack / "notes.md").write_bytes(b"unlisted\n")

    def symlinked(pack):
        std(pack)
        os.symlink("adoption-sop.md", pack / "link.md")

    def not_string(pack):
        write(pack, manifest=_manifest_text("1.0.0", good).replace("version = \"1.0.0\"", "version = 1"))

    def oversized_member(pack):
        data = b"x" * (MAX_MEMBER_BYTES + 1)
        write(pack, docs={"large.md": data},
              manifest=_manifest_text("1.0.0", [("large.md", _sha(data))]))

    def too_many_members(pack):
        docs = {"{:04d}.md".format(i): b"" for i in range(MAX_MEMBERS + 1)}
        rows = sorted((p, _sha(data)) for p, data in docs.items())
        write(pack, docs=docs, manifest=_manifest_text("1.0.0", rows))

    vectors = [
        ("member-oversize", CANNOT_EVALUATE, "member-bound", oversized_member),
        ("members-oversize", CANNOT_EVALUATE, "members-bound", too_many_members),
        ("valid-two-members", VALID, None, std),
        ("valid-empty", VALID, None, lambda p: write(p, docs={}, manifest=_manifest_text("0.1.0", []))),
        ("member-edit-without-digest-update", INVALID, "member-sha256", edited),
        ("member-sha-updated-digest-stale", INVALID, "pack-digest", sha_only),
        ("version-bumped-digest-stale", INVALID, "pack-digest", bumped),
        ("unlisted-file", INVALID, "unlisted-file", stray),
        ("member-missing", INVALID, "member-missing",
         lambda p: write(p, docs=dict([("adoption-sop.md", _DOCS["adoption-sop.md"])]),
                         manifest=_manifest_text("1.0.0", good))),
        ("symlink-in-pack", CANNOT_EVALUATE, "pack-walk", symlinked),
        ("manifest-absent", CANNOT_EVALUATE, "manifest-read", lambda p: write(p, docs={})),
        ("manifest-unparseable", CANNOT_EVALUATE, "manifest-parse", lambda p: write(p, manifest="format = \n")),
        ("manifest-non-utf8", CANNOT_EVALUATE, "manifest-parse", lambda p: write(p, manifest=b"\xff\n")),
        ("manifest-oversize", CANNOT_EVALUATE, "manifest-bound",
         lambda p: write(p, manifest=b"#" * (MAX_MANIFEST_BYTES + 1))),
        ("format-unknown", CANNOT_EVALUATE, "format", lambda p: std(p, fmt="opf.prompt-pack/v2")),
        ("format-not-string", INVALID, "format",
         lambda p: write(p, manifest=_manifest_text("1.0.0", good).replace(
             "format = \"opf.prompt-pack/v1\"", "format = 1"))),
        ("unknown-top-key", INVALID, "top-keys", lambda p: std(p, extra="note = 'x'\n")),
        ("missing-top-key", INVALID, "top-keys", lambda p: write(p, manifest="format = 'opf.prompt-pack/v1'\n")),
        ("digest-shape", INVALID, "digest-shape", lambda p: std(p, digest="sha256:ABC")),
        ("members-not-array", INVALID, "members-shape",
         lambda p: write(p, docs={}, manifest=_manifest_text("0.1.0", []).replace("[]", "'none'"))),
        ("member-extra-key", INVALID, "member-shape",
         lambda p: write(p, manifest=_manifest_text("1.0.0", good).replace(
             "\n[[members]]\n", "\n[[members]]\nnote = 'x'\n", 1))),
        ("member-path-escape", INVALID, "member-path", lambda p: std(p, rows=[("../x.md", "0" * 64)])),
        ("member-path-absolute", INVALID, "member-path", lambda p: std(p, rows=[("/x.md", "0" * 64)])),
        ("member-lists-manifest", INVALID, "member-path", lambda p: std(p, rows=[(MANIFEST_NAME, "0" * 64)])),
        ("member-sha256-shape", INVALID, "member-sha256-shape", lambda p: std(p, rows=[("a.md", "0" * 63)])),
        ("member-duplicate", INVALID, "member-duplicate", lambda p: std(p, rows=[good[0], good[0]])),
        ("member-order", INVALID, "member-order", lambda p: std(p, rows=list(reversed(good)))),
        ("version-not-string", INVALID, "version", not_string),
    ]
    # Tokens outside the bare MAJOR.MINOR.PATCH grammar, including a non-ASCII decimal digit.
    for bad in ("1.0", "v1.0.0", "01.0.0", "1.0.0-rc.1", "1.0.0+build", "1.0.0\n", " 1.0.0",
                "1." + chr(0x662) + ".0", "", "1.0.0.0"):
        vectors.append(("version-token-" + ascii(bad), INVALID, "version",
                        lambda p, v=bad: std(p, version=v)))
    return vectors


def _snapshot(root):
    return dict((str(p.relative_to(root)), p.read_bytes()) for p in sorted(Path(root).rglob("*"))
                if p.is_file() and not p.is_symlink())


def _close_vectors(tmp):
    """#378 P1: _read_regular's finally makes the only close of its descriptor, on the normal path, when
    os.fdopen refuses before creating anything, and when os.fdopen fails after its raw file exists (io.open
    then closes that file). Each vector fails that close after its number is released to a reuser (the
    shared opf/tools/_journal.py close harness, loaded from its sibling FILE by explicit path so this runs
    under `python3 -I`). The close is _close_fd_exc_safe's (#377): where the refusal is in flight (the two
    fdopen failures) its close error is dropped and the refusal propagates (masking), and on the normal path
    the close error propagates. Green is no problem at all. The wrapping-failure vector is also run against
    the pre-fix body (fdopen taking fd, `fd = None` as the with body's first statement) and must be red by
    NOFIRE alone: the failed wrapper's own close released fd first, so the finally's close was a second
    close. Returns the failures."""
    import inspect
    harness = _load_sibling("_prompt_pack_close_harness", Path(__file__).resolve().parent / "_journal.py")
    member = Path(tmp) / "close-member.md"
    member.write_bytes(b"member\n")
    sent = harness._StSentinel("in flight at _read_regular")

    def read(mode, site=None):
        def call(fault):
            real = os.fdopen

            def spy(fd, *args, **kwargs):
                fault.arm(fd)
                if mode == "refused":
                    raise sent
                handle = real(fd, *args, **kwargs)
                if mode == "wrap":
                    handle.close()                # as io.open closes the raw file of a wrapper that failed
                    raise sent
                return handle
            os.fdopen = spy
            try:
                (site or _read_regular)(member, MAX_MEMBER_BYTES, "member")
            finally:
                os.fdopen = real
        return call

    failures = []
    def in_flight(exc):
        return exc is sent

    for label, mode, masking, expect in (("refused", "refused", True, in_flight),
                                         ("raw file exists", "wrap", True, in_flight),
                                         ("normal path", "read", False, None)):
        got = harness._st_close_run(read(mode), masking, expect)
        if got:
            failures.append("close vector _read_regular ({}): expected green, got {}".format(label, got))
    source = inspect.getsource(_read_regular)
    new = ('        with os.fdopen(fd, "rb", closefd=False) as handle:\n'
           "            data = handle.read(limit + 1)\n    finally:\n        _close_fd_exc_safe(fd)\n")
    old = ('        with os.fdopen(fd, "rb") as handle:\n            fd = None\n'
           "            data = handle.read(limit + 1)\n    finally:\n        if fd is not None:\n"
           "            _close_fd_exc_safe(fd)\n")
    if source.count(new) != 1:
        return failures + ["close vector _read_regular revert: target found {} times".format(source.count(new))]
    reverted = dict(globals())
    exec(compile(source.replace(new, old), __file__, "exec"), reverted)
    red = harness._st_close_run(read("wrap", reverted["_read_regular"]), True, in_flight, False)
    if [problem.split(":")[0] for problem in red] != ["NOFIRE"]:
        failures.append("close vector _read_regular under the pre-fix fdopen ownership: expected red by NOFIRE "
                        "alone, got {}".format(red or "green"))
    return failures


def _load_sibling(name, path):
    """Load a sibling FILE by explicit path (so this runs under `python3 -I`) and return the module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Threat model: the code this self-test loads in-process (the shared _journal.py close harness) is reviewed
# in-repo code; the guard catches an ACCIDENTAL process ending from it (a stray sys.exit, SystemExit,
# KeyboardInterrupt, GeneratorExit, or any other BaseException) at load and in every later call. Every
# BaseException is caught, so loaded code can never end the self-test with its own status; a KeyboardInterrupt
# (an operator's Ctrl-C and one raised by loaded code are not told apart) is re-raised as a fresh
# KeyboardInterrupt, so it stops the gate runner instead of reading as this gate's exit 2.
_PROCESS_ENDING = (BaseException,)


def _ending_kind(exc):
    """A fixed name for the family of exc's class, found by issubclass on type(exc) alone, so a diagnostic
    never calls back into a loaded object (no repr, str or format of exc, of exc.code, or of any loaded
    value)."""
    cls = type(exc)
    for base, name in ((SystemExit, "SystemExit"), (KeyboardInterrupt, "KeyboardInterrupt"),
                       (GeneratorExit, "GeneratorExit"), (Exception, "Exception")):
        if issubclass(cls, base):
            return name
    return "BaseException"


def _backstop(run):
    """Return run()'s status, mapping ANY escaping exception to exit 2 (cannot evaluate) with a fixed
    message, never a false 0 and never the loaded code's own status (SystemExit 0, None or a non-int code
    included). A KeyboardInterrupt is the one exception: after a fixed message it is re-raised as a fresh
    KeyboardInterrupt (its context suppressed, so no loaded object is formatted), which stops the runner.
    Background fault channels are settled at this gate's entry (_fail_closed_main): a fault ending a
    worker thread or a destructor is recorded by the hooks installed there and forces exit 2 over a passing
    verdict, and the verdict ends the process with os._exit, so an atexit callback registered by loaded
    code never runs after it. Residuals, not covered: os._exit called by the loaded code itself, signal
    handlers it installs, mutation of sys or of this module's globals by the loaded code,
    deliberately hostile objects (the one residual stated in the _opf_views class disclosure),
    and a process exit raised while this module's own top-level imports run, before this guard is
    entered."""
    try:
        return run()
    except KeyboardInterrupt:
        print("check_opf_prompt_pack: self-test interrupted: KeyboardInterrupt re-raised to stop the run; "
              "fail-closed", file=sys.stderr)
        raise KeyboardInterrupt from None
    except _PROCESS_ENDING as exc:
        print("check_opf_prompt_pack: cannot evaluate: in-process code raised {}; fail-closed".format(
            _ending_kind(exc)), file=sys.stderr)
        return 2


# Each case is a module loaded through _load_sibling, the self-test's sibling loader: (label, source, call
# run()). A case whose label names KeyboardInterrupt must re-raise a fresh KeyboardInterrupt (the probe exits
# 130); every other case must give exit 2.
_LOADED_EXIT_CASES = (
    ("load SystemExit(0)", "raise SystemExit(0)\n", False),
    ("load SystemExit(None)", "raise SystemExit\n", False),
    ("load KeyboardInterrupt", "raise KeyboardInterrupt\n", False),
    ("call KeyboardInterrupt", "def run():\n    raise KeyboardInterrupt\n", True),
    ("load GeneratorExit", "raise GeneratorExit\n", False),
    ("load BaseException subclass", "class B(BaseException):\n    pass\nraise B()\n", False),
    ("load SystemExit(code whose repr exits 0)",
     "class R:\n    def __repr__(self):\n        raise SystemExit(0)\n    __str__ = __repr__\n"
     "raise SystemExit(R())\n", False),
    ("load Exception whose repr exits 0",
     "class E(Exception):\n    def __repr__(self):\n        raise SystemExit(0)\n    __str__ = __repr__\n"
     "raise E()\n", False),
    ("call SystemExit(0)", "def run():\n    raise SystemExit(0)\n", True),
)

# Run in a child through the real entry: load this file by path, replace only the vector body with a load
# of (and call into) one case module through the real _load_sibling, then exit with main(["--self-test"]).
_LOADED_EXIT_PROBE = """import importlib.util, sys
tool, case, call = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("_prompt_pack_entry_probe", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
def body():
    module = gate._load_sibling("_prompt_pack_loaded_exit", gate.Path(case))
    if call == "call":
        module.run()
    return 0
gate._self_test_vectors = body
try:
    code = gate.main(["--self-test"])
except KeyboardInterrupt as exc:
    sys.exit(130 if type(exc) is KeyboardInterrupt and exc.__suppress_context__ else 3)
sys.exit(code)
"""


def _loaded_exit_vectors(tmp):
    """Each case, at load or in a later call, must give exit 2 with the fixed backstop message through the
    real `--self-test` entry in a child process, so the vector is red if the backstop is reverted
    (_PROCESS_ENDING emptied) or removed from self_test. An interrupt case must instead re-raise a fresh
    KeyboardInterrupt with the fixed interrupt message (probe exit 130), so the vector is red if the
    interrupt is absorbed as exit 2 (which would let a gate runner run on past an operator's Ctrl-C).
    Returns the failures."""
    import subprocess
    failures = []
    tool = str(Path(__file__).resolve())
    probe = Path(tmp) / "loaded_exit_probe.py"
    probe.write_text(_LOADED_EXIT_PROBE, encoding="utf-8")
    for index, (label, body, call) in enumerate(_LOADED_EXIT_CASES):
        case = Path(tmp) / "loaded_exit_{}.py".format(index)
        case.write_text(body, encoding="utf-8")
        try:
            child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case),
                                    "call" if call else "load"], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append("loaded-exit vector {}: probe did not run ({})".format(label, type(exc).__name__))
            continue
        if "KeyboardInterrupt" in label:
            if child.returncode != 130 or "check_opf_prompt_pack: self-test interrupted: KeyboardInterrupt " \
                    "re-raised" not in child.stderr:
                failures.append("loaded-exit vector {}: expected a fresh KeyboardInterrupt (130) with the "
                                "interrupt message, got {}".format(label, child.returncode))
        elif child.returncode != 2 or "check_opf_prompt_pack: cannot evaluate: in-process code raised" \
                not in child.stderr:
            failures.append("loaded-exit vector {}: expected exit 2 with the backstop message, got {}".format(
                label, child.returncode))
    return failures


def self_test():
    """The canonical --self-test entry's suite hook (F-SELFTEST-NO-MAIN). Run as this file's process
    entry (the module __name__ is "__main__"), it routes the suite through _fail_closed_main, which
    installs the fault recorders before any in-process load, settles them after the suite, and ends the
    process with os._exit, so sys.exit(self_test()) never returns and no atexit callback registered by
    loaded code runs after the verdict. Called in-process (the opf.py aggregate), it runs the suite
    plainly and returns its result; that aggregate entry is the disclosed HARDEN-GATE-ENTRY-WRAP residual
    until the unit runner of #385 lands."""
    if __name__ == "__main__":
        _fail_closed_main(_self_test)
    return _self_test()


def _self_test():
    """Run the vectors behind the cannot-evaluate backstop. Both the canonical `--self-test` entry and
    `main(["--self-test"])` call this, so an exception escaping the vectors is exit 2 on either path."""
    return _backstop(_self_test_vectors)


def _self_test_vectors():
    """0 every vector returned its exact status and guard, 1 a discriminator failed, 2 harness error."""
    failures = []
    count = 0
    try:
        with tempfile.TemporaryDirectory(prefix="opf-prompt-pack-selftest-") as tmp:
            for index, (name, status, guard, build) in enumerate(_vectors()):
                pack = Path(tmp) / "case{}".format(index) / "prompt-pack"
                pack.mkdir(parents=True)
                build(pack)
                before = _snapshot(pack)
                got = validate_pack(pack)
                again = validate_pack(pack)
                count += 1
                ok = got[:2] == (status, guard) and again == got and _snapshot(pack) == before
                print("{} {}: {} {}".format("PASS" if ok else "FAIL", name, got[0], got[1] or "").rstrip())
                if not ok:
                    failures.append("{} (wanted {} {}, got {} {}: {})".format(
                        name, status, guard, got[0], got[1], got[2]))
            # chmod cannot make a directory unreadable to root. Restore access before snapshot/cleanup.
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                print("SKIP unreadable-subdirectory: running as root")
            else:
                pack = Path(tmp) / "unreadable" / "prompt-pack"
                hidden = pack / "hidden"
                hidden.mkdir(parents=True)
                (pack / MANIFEST_NAME).write_text(_manifest_text("0.1.0", []), encoding="utf-8")
                (hidden / "unlisted.md").write_bytes(b"unlisted\n")
                before = _snapshot(pack)
                hidden.chmod(0)
                try:
                    got = validate_pack(pack)
                    again = validate_pack(pack)
                finally:
                    hidden.chmod(0o700)
                count += 1
                ok = (got[:2] == (CANNOT_EVALUATE, "pack-walk") and again == got
                      and _snapshot(pack) == before)
                print("{} unreadable-subdirectory: {} {}".format(
                    "PASS" if ok else "FAIL", got[0], got[1] or "").rstrip())
                if not ok:
                    failures.append("unreadable-subdirectory: {!r}".format(got))

            # Use a child timeout so a blocking open is a failed check, never a hung self-test.
            # Probe member reads directly: the walk's earlier refusal must not mask a lost open guard.
            probe = """\
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import check_opf_prompt_pack as gate
path, what = Path(sys.argv[2]), sys.argv[3]
if what == "manifest":
    gate.DEFAULT_PACK_DIR = path.parent
    sys.exit(gate.main([]))
try:
    gate._read_regular(path, gate.MAX_MEMBER_BYTES, what)
except gate._Refusal as exc:
    print("[{}] {}".format(exc.guard, exc), file=sys.stderr)
    sys.exit(gate.EXIT_CODES[exc.status])
sys.exit(0)
"""
            for what in ("manifest", "member"):
                for kind in ("fifo", "symlink"):
                    name = what + "-" + kind
                    pack = Path(tmp) / name
                    pack.mkdir()
                    path = pack / (MANIFEST_NAME if what == "manifest" else "member.md")
                    if kind == "fifo":
                        os.mkfifo(path)
                    else:
                        target = pack / "target.md"
                        target.write_text(_manifest_text("0.1.0", []), encoding="utf-8")
                        path.symlink_to(target.name)
                    count += 1
                    try:
                        result = subprocess.run(
                            [sys.executable, "-I", "-B", "-c", probe,
                             str(Path(__file__).resolve().parent), str(path), what],
                            capture_output=True, text=True, timeout=5)
                        # Match only the exit status and structured guard tag, never diagnostic prose.
                        ok = result.returncode == 2 and "[" + what + "-read]" in result.stderr
                        detail = "exit {}: {}".format(result.returncode, result.stderr.strip())
                    except subprocess.TimeoutExpired:
                        ok, detail = False, "open timed out after 5 seconds"
                    print("{} {}: {}".format("PASS" if ok else "FAIL", name, detail))
                    if not ok:
                        failures.append(name + ": " + detail)
            close_failures = _close_vectors(tmp)
            count += 4
            print("{} close-vectors: {}".format("FAIL" if close_failures else "PASS",
                                                "; ".join(close_failures) or "3 green, 1 pre-fix red"))
            failures.extend(close_failures)
            loaded_failures = _loaded_exit_vectors(tmp)
            count += len(_LOADED_EXIT_CASES)
            print("{} loaded-exit-vectors: {}".format(
                "FAIL" if loaded_failures else "PASS",
                "; ".join(loaded_failures) or "{} exit 2 or interrupt through the entry".format(len(_LOADED_EXIT_CASES))))
            failures.extend(loaded_failures)
            fault_failures = _fault_channel_vectors()
            count += len(_FAULT_CHANNEL_CASES) + 2
            print("{} fault-channel-vectors: {}".format(
                "FAIL" if fault_failures else "PASS",
                "; ".join(fault_failures) or "{} channels settled".format(len(_FAULT_CHANNEL_CASES))))
            failures.extend(fault_failures)
        count += 1
        if compute_digest("1.0.0", []) != "sha256:" + _sha(b"opf.prompt-pack/v1\nversion 1.0.0\n"):
            failures.append("digest-definition")
    except Exception as exc:  # noqa: BLE001  a harness error is cannot-evaluate, never a pass
        print("OPF-PROMPT-PACK SELF-TEST: CANNOT EVALUATE ({!r})".format(exc), file=sys.stderr)
        return 2
    if failures:
        print("OPF-PROMPT-PACK SELF-TEST: FAIL ({} of {} checks failed): {}".format(
            len(failures), count, "; ".join(failures)))
        return 1
    print("OPF-PROMPT-PACK SELF-TEST: PASS ({} checks)".format(count))
    return 0


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return self_test()
        if args:
            print("usage: check_opf_prompt_pack.py [--self-test]", file=sys.stderr)
            return 2
        status, guard, detail = validate_pack(DEFAULT_PACK_DIR)
        if status == VALID:
            print("OPF PROMPT PACK: VALID ({})".format(detail))
        else:
            print("OPF PROMPT PACK: {} [{}] {}".format(status, guard, detail), file=sys.stderr)
        return EXIT_CODES[status]
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("check_opf_prompt_pack: cannot evaluate: unexpected error ({!r})".format(exc), file=sys.stderr)
        return 2



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass; the
# verdict then ends the process with os._exit, so an atexit callback registered by loaded code can never
# run after it (that channel is unreachable, not merely disclosed). Only a SystemExit with code None
# or 0 ending a worker thread is the interpreter's normal SUCCESSFUL thread exit (default-hook parity)
# and is not recorded; any other code is an unsuccessful exit a worker reported, so it is recorded.
# Each recorder chains to the hook it wrapped, so the usual traceback still reaches stderr after the
# marker line. The settle re-checks the record after the exit flushes, so a fault recorded while a
# flush was blocked on a full pipe still fails the verdict.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (D-411-HOSTILE-DISCLOSED).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    import threading

    previous_unraisable = sys.unraisablehook
    previous_thread = threading.excepthook

    def _record(channel, chained, args):
        _LOADED_FAULTS.append(channel)
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a {} fault was recorded; a passing verdict will fail closed\n".format(
                _FAULT_MARKER, channel))
        finally:
            chained(args)

    def _record_unraisable(args):
        _record("destructor-or-callback", previous_unraisable, args)

    def _benign_thread_exit(args):
        # Only a worker thread ending by SystemExit(None) or SystemExit(0) is the interpreter's normal
        # SUCCESSFUL thread exit (default-hook parity). Any other SystemExit code is an unsuccessful exit
        # a worker reported, recorded like any other fault (never a silent pass); the code is read
        # through exact built-in types alone and never formatted.
        if args.exc_type is None or not issubclass(args.exc_type, SystemExit):
            return False
        code = getattr(args.exc_value, "code", None) if args.exc_value is not None else None
        return code is None or (type(code) is int and code == 0)

    def _record_thread(args):
        if _benign_thread_exit(args):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    threading.excepthook = _record_thread


def _gate_exit(code):
    """Settle and END the process: force a recorded background fault to exit 2 over a passing verdict,
    flush both streams, re-check the record AFTER the flushes (a fault recorded while a flush was blocked,
    on a full pipe say, must still fail the verdict; its late marker is written straight to fd 2, past the
    buffers), then os._exit, so no atexit callback registered by loaded code runs after the verdict (the
    gate's own cleanup runs inside its work; this gate registers no atexit work of its own)."""
    import os
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        sys.stderr.write("{}: {} background fault(s) from loaded code over a passing verdict; "
                         "fail-closed\n".format(_FAULT_MARKER, len(_LOADED_FAULTS) or 1))
        code = 2
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        if code == 0:
            code = 2
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        try:
            os.write(2, ("{}: a background fault was recorded during the exit flush; "
                         "fail-closed\n".format(_FAULT_MARKER)).encode("utf-8"))
        except OSError:
            pass
        code = 2
    os._exit(code)


def _fail_closed_main(run):
    """The canonical process entry: install the fault recorders, run the gate's own work, settle, end."""
    _install_fault_hooks()
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    _gate_exit(code)


# The background fault channels, each driven through the gate's REAL _fail_closed_main in a child that
# loads one fixture the way this gate loads code in process: a clean load passes; an atexit callback
# registered by loaded code never runs (unreachable behind os._exit); a worker-thread fault and a
# destructor fault are recorded and force exit 2.
_FAULT_CHANNEL_PROBE = """import importlib.util, sys
tool, fixture = sys.argv[1:3]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_fault_channel_probe_target", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
def load():
    case = importlib.util.spec_from_file_location("_fault_channel_fixture", fixture)
    module = importlib.util.module_from_spec(case)
    case.loader.exec_module(module)
    return 0
gate._fail_closed_main(load)
"""

# (label, fixture source, expected exit, marker required, text that must NOT appear on stderr)
_FAULT_CHANNEL_CASES = (
    ("clean", "x = 1\n", 0, False, None),
    ("atexit", "import atexit, sys\n"
     "def callback():\n"
     "    sys.stderr.write('FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "    raise RuntimeError('fault-fixture-atexit')\n"
     "atexit.register(callback)\n", 0, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("joined-thread", "import threading\n"
     "def fault():\n"
     "    raise RuntimeError('fault-fixture-thread')\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("destructor", "import gc\n"
     "class Fault:\n"
     "    def __del__(self):\n"
     "        raise RuntimeError('fault-fixture-destructor')\n"
     "Fault()\n"
     "gc.collect()\n", 2, True, None),
    ("thread-systemexit", "import threading\n"
     "def fault():\n"
     "    raise SystemExit(7)\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("thread-systemexit-zero", "import sys\nimport threading\n"
     "def done():\n"
     "    sys.exit(0)\n"
     "worker = threading.Thread(target=done)\n"
     "worker.start()\n"
     "worker.join()\n", 0, False, None),
)


# The flush-window channel: _gate_exit's fault decision must hold across the exit flushes. The fixture
# fills the child's stdout pipe to its exact capacity (F_GETPIPE_SZ) and leaves one byte in the stream's
# buffer, then starts a worker that faults after a delay, so the fault is recorded while the exit flush is
# blocked on the full pipe; the parent drains stdout only after stderr shows the fault marker. Exit 2 with
# the marker is required. Red when the fault decision runs only before the flushes: the child then ends 0
# with the marker on stderr.
_FLUSH_WINDOW_FIXTURE = """import fcntl, os, sys, threading, time
os.write(1, b"x" * fcntl.fcntl(1, fcntl.F_GETPIPE_SZ))
sys.stdout.write("y")
def fault():
    time.sleep(1.0)
    raise RuntimeError("fault-fixture-flush-window")
threading.Thread(target=fault).start()
"""


def _flush_window_failures():
    """One failure string per flush-window requirement the child missed (see _FLUSH_WINDOW_FIXTURE)."""
    import subprocess
    import tempfile
    import threading
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-prompt-pack-flush-window-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        case = Path(tmp) / "flush_window.py"
        case.write_text(_FLUSH_WINDOW_FIXTURE, encoding="utf-8")
        try:
            child = subprocess.Popen([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (OSError, subprocess.SubprocessError) as exc:
            return ["fault-channel/flush-window: probe did not run ({})".format(type(exc).__name__)]
        with child:
            killer = threading.Timer(120, child.kill)
            killer.start()
            try:
                header = b""
                while (_FAULT_MARKER + ":").encode("ascii") not in header:
                    line = child.stderr.readline()
                    if not line:
                        break
                    header += line
                child.stdout.read()
                trailer = child.stderr.read()
                code = child.wait()
            finally:
                killer.cancel()
        stderr_text = (header + trailer).decode("utf-8", "replace")
        if code != 2:
            failures.append("fault-channel/flush-window: expected exit 2, got {}".format(code))
        elif (_FAULT_MARKER + ":") not in stderr_text:
            failures.append("fault-channel/flush-window: fault marker absent")
    return failures


# The entry wiring, checked statically on this file's own source: the final __main__ block must be
# exactly _ENTRY_WIRING by AST (spacing and comments aside), so a rewiring that keeps _fail_closed_main
# defined but routes an entry branch around it (a plain sys.exit around the work, say) is red even though
# the channel probes above drive _fail_closed_main directly.
_ENTRY_WIRING = '''if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    _fail_closed_main(main)
'''


# For the canonical --self-test entry (F-SELFTEST-NO-MAIN), self_test itself must be the entry-aware
# wrapper, so sys.exit(self_test()) still settles fail-closed and ends with os._exit when this file is
# the process entry.
_SELF_TEST_WIRING = '''def self_test():
    if __name__ == "__main__":
        _fail_closed_main(_self_test)
    return _self_test()
'''

def _entry_wiring_failures():
    """One failure string per entry-wiring requirement this file's own source misses (see _ENTRY_WIRING)."""
    import ast
    failures = []
    try:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        return ["fault-channel/entry-wiring: this file could not be parsed ({})".format(type(exc).__name__)]
    last = tree.body[-1] if tree.body else None
    expected = ast.parse(_ENTRY_WIRING).body[0]
    if last is None or ast.dump(last) != ast.dump(expected):
        failures.append("fault-channel/entry-wiring: the final __main__ block is not the expected "
                        "fail-closed form")
    wrappers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "self_test"]
    body = list(wrappers[0].body) if len(wrappers) == 1 else []
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and type(body[0].value.value) is str:
        body = body[1:]
    expected_body = ast.parse(_SELF_TEST_WIRING).body[0].body
    if len(wrappers) != 1 or [ast.dump(node) for node in body] != [ast.dump(node) for node in expected_body]:
        failures.append("fault-channel/entry-wiring: self_test is not the entry-aware wrapper (the "
                        "canonical sys.exit(self_test()) must settle through _fail_closed_main)")
    return failures

def _fault_channel_vectors():
    """One failure string per fault-channel case whose child did not end as required: the expected exit
    code, the fault marker exactly when a fault must be recorded, and for the atexit case no trace of the
    callback (it must never run). Red without the entry wrapper (the probe then ends 1, AttributeError on
    _fail_closed_main, where clean and atexit expect 0) and red with the wrapper reverted to a plain exit
    (the thread and destructor children then end 0 with the default hooks' output alone, where exit 2 with
    the marker is required, and the atexit child then runs the callback)."""
    import subprocess
    import tempfile
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="opf-prompt-pack-fault-channel-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for index, (label, body, expected, marked, absent) in enumerate(_FAULT_CHANNEL_CASES):
            case = Path(tmp) / "fault_channel_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            try:
                child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                       capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            if child.returncode != expected:
                failures.append("fault-channel/{}: expected exit {}, got {}".format(
                    label, expected, child.returncode))
            elif marked != ((_FAULT_MARKER + ":") in child.stderr):
                failures.append("fault-channel/{}: fault marker {}".format(
                    label, "absent" if marked else "present"))
            elif absent is not None and absent in child.stderr:
                failures.append("fault-channel/{}: the atexit callback ran".format(label))
    failures.extend(_flush_window_failures())
    failures.extend(_entry_wiring_failures())
    return failures


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    _fail_closed_main(main)
