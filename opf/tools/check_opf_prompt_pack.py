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
import sys
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
    import importlib.util
    import inspect
    spec = importlib.util.spec_from_file_location("_prompt_pack_close_harness",
                                                  Path(__file__).resolve().parent / "_journal.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
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


def self_test():
    """Run the vectors behind main()'s cannot-evaluate backstop, so the canonical `--self-test` entry maps an
    exception escaping them to exit 2 exactly as `main(["--self-test"])` does."""
    try:
        return _self_test_vectors()
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("check_opf_prompt_pack: cannot evaluate: unexpected error ({!r})".format(exc), file=sys.stderr)
        return 2


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


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
