#!/usr/bin/env python3
"""Pinned HTTPS observations and non-executing quarantine for OPF adoption.

Public contract:
    gather_release(request, policy) -> (observation, notes)
    self_test() -> 0 / 1 / 2 (local fixtures require openssl, git and bash)

request is a plain dict with exactly:
    product_root: absolute, lexically contained POSIX directory path
    version:      bare pack SemVer
    commit:       full lowercase SHA-1 commit identifier

policy is a plain dict supplied ONLY by the installed bootstrap trust caller:
    format:       "opf.adoption.https-policy/v1"
    repository:   canonical "https://github.com/<owner>/<repository>"
    release_url:  exact codeload URL for request["commit"]
    anchors:      ["https://posluns.dev/hashes.txt"]

This module does not discover, parse, or activate a reference registry. The
caller must read its bootstrap-pinned registry before calling this function.
The downloaded archive cannot supply policy, URLs, quorum, or TLS settings.

A VALID observation means that the requested observations were obtained and
that the archive satisfied this module's containment grammar. It is NOT a
trust verdict, an inventory freeze, or permission to apply anything. Anchor
bodies remain uninterpreted bytes. No manifest, ROOT, or TREE is parsed or
computed here.

On refusal, notes contain named statuses and the observation omits both
"quarantine" and "members". Already completed HTTP observations may remain as
diagnostic evidence. No partial HTTP body is reported as a completed response.
Unexpected exceptions map to CANNOT_EVALUATE. BaseException cancellation
propagates without returning an observation.

Supported transport is deliberately narrow: HTTPS/443, one numeric-address
connection, no retry, no redirect, HTTP/1.0 or HTTP/1.1 status 200, identity
content encoding, and either an unambiguous Content-Length or strict chunked
framing without extensions or trailers. Connection closure must follow the
complete body; an extra byte or unclean TLS EOF refuses.

Supported archive dialect is a single gzip member containing POSIX USTAR.
One optional leading PAX global header is accepted only when its sole record
is the canonical git-archive comment naming request["commit"]. It supplies no
path or extraction metadata. Every other PAX/GNU extension, sparse archive,
alternate numeric encoding, and other
dialects refuse. USTAR members are validated before materialization; only
regular files and directories are accepted. Exactly one wrapper is removed.
All materialized files have mode 0600 and directories mode 0700. Original
permission bits are inert metadata only.

Quarantine creation uses atomic mkdir, which is the directory equivalent of
exclusive creation; O_EXCL is used for every file creation. Existing run or
quarantine directories are never reused. Files are opened descriptor-relative
with no-follow and nonblocking flags and reread with inode/stamp checks.
Each run name contains 128 CSPRNG bits and is registered as PENDING before mkdir.
Cleanup authority requires this invocation's recorded (st_dev, st_ino), captured
by fstat of the new directory opened no-follow under the parent descriptor after
successful exclusive mkdir. Removal rechecks that identity against the entry;
a PENDING name without a recorded identity is never deleted. EEXIST refuses
without adopting the colliding entry. Creation needs no signal control. An
asynchronous interruption between creation and identity capture can leak an
empty run directory; it is never reported VALID and never deleted blindly.
Finalization and the return handoff stay inside the BaseException handler, with
no trailing finalizer after disarm: escaping cancellation clears even sealed
VALID evidence and attempts removal only with recorded ownership.
Cleanup failure refuses with a named cleanup note and no capability or sealed
success record. OS deletion failure, process death, cancellation during rmtree
(which can leave a partial tree), and same-privilege interference remain outside
the cleanup guarantee; private residue or held descriptors may remain.
Scaffolding (.working/adopt) may remain. Callers must never reconstruct a
capability from request_id.

R1: bootstrap code/policy, the Python runtime, OS, resolver, and system CA store
are trusted. Compromise of those components defeats these guarantees.
R7: byte/count/depth caps are not a process sandbox. Parsing relies on Python's
zlib/tarfile implementations on capped input. Filesystem and scheduling latency
are not hard-real-time guarantees. noexec mounts can add protection; the
prohibition here is that fetched bytes are never imported, executed, or checked
out. One daemon resolver worker may survive a timeout until the OS resolver
returns; a process-wide slot prevents accumulating such workers. Its result is
never reused by another request, and it cannot fetch an HTTP body.
R8: an adversarial same-privilege concurrent writer to process memory,
environment, or quarantine is out of scope. Gather is main-thread-only and
non-reentrant because its environment scrub is process-global.
R10: larger responses and unsupported publisher formats refuse until a reviewed
bootstrap update. Transport bounds refuse as CANNOT_EVALUATE; understood archive
content violating an archive bound refuses as INVALID.

OQ-2 recommendation: capture the oldest instant at gather entry using
_capture_instant(). PR-C3 owns the proposed ten-minute freshness bound and must
also establish the monotonic clock domain before comparing persisted records.
"""

import contextlib
import datetime
import hashlib
import ipaddress
import os
import queue
import re
import secrets
import shutil
import signal
import socket
import ssl
import stat
import sys
import tarfile
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit

# The standalone CLI needs the installed sibling directory under python -I.
# No quarantine path is ever added. Preserve sibling imports' ambient path
# edits so a lazy public gather call does not change its caller's sys.path.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
_import_path = list(sys.path)
try:
    import _opf_adopt as schema
    import _opf_adopt_plan as planning
    import _opf_store as store
    import _opf_pack_manifest as manifest
    from _semver import _parse as parse_version
finally:
    sys.path[:] = _import_path
    del _import_path

VALID = store.VALID
INVALID = store.INVALID
CANNOT_EVALUATE = store.CANNOT_EVALUATE

MAX_ENTRIES = planning.MAX_ENTRIES
MAX_DEPTH = planning.MAX_DEPTH
MAX_PATH_BYTES = planning.MAX_PATH_BYTES
MAX_FILE_BYTES = planning.MAX_FILE_BYTES
MAX_TOTAL_BYTES = planning.MAX_TOTAL_BYTES

MAX_ANCHOR_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES
MAX_HEADER_BYTES = MAX_ANCHOR_BYTES

CONNECT_SECONDS = 10.0
INACTIVITY_SECONDS = 10.0
REQUEST_SECONDS = 60.0
GATHER_SECONDS = 120.0

POLICY_FORMAT = "opf.adoption.https-policy/v1"
OBSERVATION_FORMAT = "opf.adoption.release-observation/v1"
ANCHOR_URL = "https://posluns.dev/hashes.txt"

_GATHER_LOCK = threading.Lock()
_RESOLVER_SLOT = threading.BoundedSemaphore(1)
_REPOSITORY = re.compile(
    r"https://github[.]com/"
    r"([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"([A-Za-z0-9][A-Za-z0-9_.-]{0,99})\Z",
    re.ASCII,
)
_COMMIT = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_HTTP_STATUS = re.compile(rb"HTTP/1[.][01] ([0-9]{3})(?: [\x20-\x7e]*)?\r\n\Z")
_EXTRA_DENIED_NETWORKS = (
    ipaddress.ip_network("168.63.129.16/32"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)

SELF_TEST_ROSTER = ()


class ObserveError(Exception):
    def __init__(self, status, phase, detail):
        super().__init__(detail)
        self.status = status
        self.phase = phase
        self.detail = detail


def _require(condition, status, phase, detail):
    if not condition:
        raise ObserveError(status, phase, detail)


def _capture_instant():
    """Oldest observation instant; freshness policy belongs to PR-C3."""
    return time.monotonic_ns()


class _Deadline:
    def __init__(self, seconds, parent=None):
        _require(type(seconds) in (int, float) and 0 < seconds <= 120,
                 CANNOT_EVALUATE, "deadline", "invalid deadline control")
        # Reserve cleanup time. Tests assert return before the nominal deadline.
        reserve = min(0.05, seconds / 10.0)
        self.end = time.monotonic() + seconds - reserve
        if parent is not None:
            self.end = min(self.end, parent.end)

    def left(self, maximum=None):
        remaining = self.end - time.monotonic()
        _require(remaining > 0, CANNOT_EVALUATE, "deadline",
                 "observation deadline expired")
        return remaining if maximum is None else min(remaining, maximum)


def _note(notes, status, phase, detail):
    notes.append({"status": status, "phase": phase, "detail": detail})


def _url(value, expected):
    _require(type(value) is str, CANNOT_EVALUATE, "destination",
             "destination is not a string")
    _require(value.isascii() and value == expected,
             CANNOT_EVALUATE, "destination",
             "destination differs from the bootstrap-pinned route")
    parsed = urlsplit(value)
    _require(
        parsed.scheme == "https"
        and parsed.port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and "%" not in value
        and "\\" not in value
        and all(ord(ch) >= 0x21 and ord(ch) < 0x7f for ch in value),
        CANNOT_EVALUATE, "destination", "unsupported URL spelling",
    )
    return value


def _validate(request, policy):
    _require(type(request) is dict, CANNOT_EVALUATE, "request",
             "request is not a table")
    _require(type(policy) is dict, CANNOT_EVALUATE, "policy",
             "bootstrap policy is not a table")

    for key in ("version", "commit"):
        _require(key in request and request[key] is not None,
                 CANNOT_EVALUATE, "request", "release identity unavailable")
    _require(set(request) == {"product_root", "version", "commit"},
             INVALID, "request", "request key set is not the closed schema")

    _require(all(type(n) is int and n > 0 for n in (
        MAX_ENTRIES, MAX_DEPTH, MAX_PATH_BYTES, MAX_FILE_BYTES,
        MAX_TOTAL_BYTES, MAX_ARCHIVE_BYTES, MAX_ANCHOR_BYTES, MAX_HEADER_BYTES,
    )), CANNOT_EVALUATE, "policy", "invalid observation bounds")
    root = request["product_root"]
    version = request["version"]
    commit = request["commit"]
    _require(
        type(root) is str and root.startswith("/") and not root.startswith("//")
        and root != "/" and not root.endswith("/")
        and manifest._path_ok(root[1:]),
        INVALID, "request", "product_root is not a contained absolute path",
    )
    try:
        root.encode("utf-8")
    except UnicodeError:
        raise ObserveError(INVALID, "request", "product_root is not UTF-8")
    _require(type(version) is str and parse_version(version) is not None,
             INVALID, "request", "malformed release version")
    _require(type(commit) is str and _COMMIT.fullmatch(commit) is not None,
             INVALID, "request", "malformed pinned commit")

    _require(
        set(policy) == {"format", "repository", "release_url", "anchors"},
        CANNOT_EVALUATE, "policy", "bootstrap policy unavailable or unsupported",
    )
    _require(policy["format"] == POLICY_FORMAT,
             CANNOT_EVALUATE, "policy", "unsupported bootstrap policy format")
    repository = policy["repository"]
    _require(type(repository) is str, CANNOT_EVALUATE, "policy",
             "repository is not a string")
    match = _REPOSITORY.fullmatch(repository)
    _require(match is not None, CANNOT_EVALUATE, "policy",
             "unsupported bootstrap repository")
    _require(match.group(2) not in (".", ".."),
             CANNOT_EVALUATE, "policy", "unsupported repository component")

    expected = "https://codeload.github.com/{}/{}/tar.gz/{}".format(
        match.group(1), match.group(2), commit,
    )
    release = _url(policy["release_url"], expected)
    anchors = policy["anchors"]
    _require(type(anchors) is list, CANNOT_EVALUATE, "policy",
             "anchor roster is not an array")
    _require(len(anchors) == 1, CANNOT_EVALUATE, "policy",
             "bootstrap anchor roster is unsupported")
    anchor = _url(anchors[0], ANCHOR_URL)

    # Copy immutable leaves before inspecting any candidate byte.
    return (
        {"product_root": root, "version": version, "commit": commit},
        {"release_url": release, "anchors": (anchor,)},
    )


@contextlib.contextmanager
def _environment():
    _require(threading.current_thread() is threading.main_thread(),
             CANNOT_EVALUATE, "environment", "gather requires the main thread")
    _require(_GATHER_LOCK.acquire(blocking=False),
             CANNOT_EVALUATE, "environment", "another gather is active")
    previous = dict(os.environ)
    try:
        clean = {key: previous[key] for key in ("PATH", "HOME") if key in previous}
        os.environ.clear()
        os.environ.update(clean)
        yield
    finally:
        try:
            os.environ.clear()
            os.environ.update(previous)
        finally:
            _GATHER_LOCK.release()


def _client_context():
    # Called only inside _environment: CA overrides and SSLKEYLOGFILE are absent.
    context = ssl.create_default_context()
    _require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED,
             CANNOT_EVALUATE, "tls", "TLS verification is not enabled")
    _require(context.keylog_filename is None,
             CANNOT_EVALUATE, "tls", "TLS key logging is not permitted")
    return context


def _public(address):
    ip = ipaddress.ip_address(address)
    if not ip.is_global or ip.is_multicast or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None:
            return False
    return not any(
        ip.version == network.version and ip in network
        for network in _EXTRA_DENIED_NETWORKS
    )


def _lookup(host):
    return socket.getaddrinfo(
        host, 443, family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )


def _resolve(host, deadline):
    _require(_RESOLVER_SLOT.acquire(blocking=False),
             CANNOT_EVALUATE, "dns", "a prior resolver call is still outstanding")
    result = queue.Queue(maxsize=1)

    def worker():
        try:
            try:
                result.put((True, _lookup(host)))
            except Exception as exc:
                result.put((False, exc))
        finally:
            _RESOLVER_SLOT.release()

    thread = threading.Thread(
        target=worker, name="opf-adopt-resolver", daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        _RESOLVER_SLOT.release()
        raise
    try:
        succeeded, value = result.get(timeout=deadline.left())
    except queue.Empty:
        raise ObserveError(CANNOT_EVALUATE, "dns", "resolver deadline expired")
    deadline.left()
    _require(succeeded, CANNOT_EVALUATE, "dns", "resolver could not answer")
    _require(type(value) is list and 0 < len(value) <= MAX_ENTRIES,
             CANNOT_EVALUATE, "dns", "invalid or empty resolver answer")

    addresses = []
    for row in value:
        _require(
            type(row) is tuple and len(row) == 5
            and row[0] in (socket.AF_INET, socket.AF_INET6)
            and row[1] == socket.SOCK_STREAM
            and row[2] == socket.IPPROTO_TCP
            and type(row[4]) is tuple,
            CANNOT_EVALUATE, "dns", "unsupported resolver answer",
        )
        family, _, _, _, endpoint = row
        expected_length = 2 if family == socket.AF_INET else 4
        _require(len(endpoint) == expected_length and endpoint[1] == 443,
                 CANNOT_EVALUATE, "dns", "unexpected resolved endpoint")
        if family == socket.AF_INET6:
            _require(endpoint[2:] == (0, 0), CANNOT_EVALUATE, "dns",
                     "scoped or flow-labelled address is not supported")
        address = ipaddress.ip_address(endpoint[0])
        _require(
            address.version == (4 if family == socket.AF_INET else 6)
            and _public(address),
            CANNOT_EVALUATE, "address", "resolved address is prohibited",
        )
        pair = (family, str(address))
        if pair not in addresses:
            addresses.append(pair)
    return addresses[0]  # No automatic fallback or retry.


def _connect(sock, endpoint, timeout):
    sock.settimeout(timeout)
    sock.connect(endpoint)


def _peer(sock):
    peer = sock.getpeername()
    return peer[0], peer[1]


def _check_peer(sock, selected):
    address, port = _peer(sock)
    _require(
        port == 443 and ipaddress.ip_address(address) == ipaddress.ip_address(selected)
        and _public(address),
        CANNOT_EVALUATE, "address", "connected peer differs from checked address",
    )


def _tls(context, sock, host, deadline):
    _require(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED,
             CANNOT_EVALUATE, "tls", "TLS verification is not enabled")
    wrapped = context.wrap_socket(
        sock, server_hostname=host, do_handshake_on_connect=False,
        suppress_ragged_eofs=False,
    )
    try:
        wrapped.settimeout(deadline.left())
        wrapped.do_handshake()
        deadline.left()
        return wrapped
    except BaseException:
        wrapped.close()
        raise


class _Wire:
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline
        self.buffer = bytearray()

    def _receive(self):
        self.sock.settimeout(self.deadline.left(INACTIVITY_SECONDS))
        block = self.sock.recv(65536)
        self.deadline.left()
        return block

    def take(self, size, eof=False):
        while len(self.buffer) < size:
            block = self._receive()
            if not block:
                _require(eof and not self.buffer, CANNOT_EVALUATE, "http",
                         "body ended before its declared framing")
                break
            self.buffer.extend(block)
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def line(self):
        while True:
            end = self.buffer.find(b"\r\n")
            if end >= 0:
                _require(end + 2 <= MAX_HEADER_BYTES,
                         CANNOT_EVALUATE, "http", "line exceeds bound")
                result = bytes(self.buffer[:end + 2])
                del self.buffer[:end + 2]
                return result
            _require(len(self.buffer) <= MAX_HEADER_BYTES,
                     CANNOT_EVALUATE, "http", "line exceeds bound")
            block = self._receive()
            _require(bool(block), CANNOT_EVALUATE, "http", "incomplete HTTP line")
            self.buffer.extend(block)


def _header_policy(status, headers):
    _require(status == 200 and "location" not in headers,
             CANNOT_EVALUATE, "http", "non-200 or redirect response")
    _require(headers.get("content-encoding", "identity").lower() == "identity",
             CANNOT_EVALUATE, "http", "non-identity content encoding")


def _response(wire, cap):
    first = wire.line()
    match = _HTTP_STATUS.fullmatch(first)
    _require(match is not None, CANNOT_EVALUATE, "http", "invalid HTTP status line")
    status = int(match.group(1))
    headers = {}
    header_bytes = len(first)
    while True:
        line = wire.line()
        header_bytes += len(line)
        _require(header_bytes <= MAX_HEADER_BYTES,
                 CANNOT_EVALUATE, "http", "headers exceed bound")
        if line == b"\r\n":
            break
        _require(b":" in line and line[:1] not in (b" ", b"\t"),
                 CANNOT_EVALUATE, "http", "malformed or folded header")
        name, value = line[:-2].split(b":", 1)
        _require(_HEADER_NAME.fullmatch(name) is not None,
                 CANNOT_EVALUATE, "http", "invalid header name")
        _require(all(byte == 9 or 32 <= byte < 127 for byte in value),
                 CANNOT_EVALUATE, "http", "invalid header value")
        key = name.decode("ascii").lower()
        _require(key not in headers, CANNOT_EVALUATE, "http", "duplicate header")
        headers[key] = value.decode("ascii").strip(" \t")

    _header_policy(status, headers)
    length = headers.get("content-length")
    transfer = headers.get("transfer-encoding")
    _require(not (length is not None and transfer is not None),
             CANNOT_EVALUATE, "http", "conflicting body framing")
    body = bytearray()

    def append(size):
        _require(size <= cap - len(body),
                 CANNOT_EVALUATE, "http", "streamed response bound exceeded")
        while size:
            amount = min(size, 65536)
            body.extend(wire.take(amount))
            size -= amount

    if length is not None:
        _require(re.fullmatch(r"[0-9]{1,20}", length, re.ASCII) is not None,
                 CANNOT_EVALUATE, "http", "invalid Content-Length")
        append(int(length))
    else:
        _require(transfer is not None and transfer.lower() == "chunked",
                 CANNOT_EVALUATE, "http", "unsupported or absent body framing")
        while True:
            line = wire.line()
            _require(re.fullmatch(rb"[0-9A-Fa-f]{1,16}\r\n", line) is not None,
                     CANNOT_EVALUATE, "http", "invalid chunk framing")
            size = int(line[:-2], 16)
            if size == 0:
                _require(wire.line() == b"\r\n", CANNOT_EVALUATE, "http",
                         "chunk trailers are unsupported")
                break
            append(size)
            _require(wire.take(2) == b"\r\n",
                     CANNOT_EVALUATE, "http", "invalid chunk terminator")

    _require(wire.take(1, eof=True) == b"",
             CANNOT_EVALUATE, "http", "bytes follow the framed response")
    wire.deadline.left()
    return bytes(body)


def _fetch(url, cap, parent, context):
    request_deadline = _Deadline(REQUEST_SECONDS, parent)
    connection_deadline = _Deadline(CONNECT_SECONDS, request_deadline)
    parsed = urlsplit(url)
    family, address = _resolve(parsed.hostname, connection_deadline)
    endpoint = (address, 443) if family == socket.AF_INET else (address, 443, 0, 0)
    with contextlib.ExitStack() as stack:
        raw = stack.enter_context(socket.socket(family, socket.SOCK_STREAM))
        _connect(raw, endpoint, connection_deadline.left())
        connection_deadline.left()
        _check_peer(raw, address)
        secured = stack.enter_context(
            _tls(context, raw, parsed.hostname, connection_deadline)
        )
        _check_peer(secured, address)
        secured.settimeout(request_deadline.left(INACTIVITY_SECONDS))
        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "Connection: close\r\n"
            "Accept-Encoding: identity\r\n"
            "User-Agent: opf-adopt-observe/1\r\n\r\n"
        ).format(parsed.path, parsed.hostname).encode("ascii")
        secured.sendall(request)
        request_deadline.left()
        return _response(_Wire(secured, request_deadline), cap)


def _open_directory(parent, name, fresh=False, owner=None):
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        if owner is not None:
            owner.state = "COLLISION"
        if fresh:
            raise ObserveError(CANNOT_EVALUATE, "quarantine",
                               "exclusive directory already exists")
    else:
        created = True
    fd = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=parent,
    )
    try:
        opened = os.fstat(fd)
        if created and owner is not None:
            # Capture authority before any validation or registration callback.
            # Cancellation before this assignment may leak an empty directory.
            owner.identity = (opened.st_dev, opened.st_ino)
            owner.created()
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(planning._stamp(opened) == planning._stamp(named),
                 CANNOT_EVALUATE, "quarantine", "directory changed while opening")
        if fresh:
            _require(
                stat.S_IMODE(opened.st_mode) == 0o700
                and opened.st_uid == os.geteuid(),
                CANNOT_EVALUATE, "quarantine", "quarantine is not private",
            )
        return fd
    except BaseException:
        os.close(fd)
        raise


class _QuarantineOwner:
    """Keep rollback usable after quarantine descriptor teardown.

    The private random name is pending before exclusive creation, but only an
    identity captured from the newly opened directory authorizes removal. A
    pending name without that identity is never deleted, even after EEXIST.
    Cancellation before capture can leak an empty, never-VALID run directory.
    Reopen the parent without following symlinks and confirm both identities
    before removal, so descriptor teardown does not lose rollback authority.
    The entry check and rmtree are not atomic against a concurrent writer.
    OS deletion failure,
    process death, cancellation during rmtree (possibly leaving a partial tree),
    and same-privilege interference remain outside the cleanup guarantee.
    """

    def __init__(self, parent, name, parent_path):
        _require(shutil.rmtree.avoids_symlink_attacks,
                 CANNOT_EVALUATE, "cleanup", "safe cleanup unavailable")
        parent_stat = os.fstat(parent)
        self.parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
        self.parent_path = parent_path
        self.name = name
        self.state = "PENDING"
        self.identity = None

    def created(self):
        self.state = "OWNED"

    def remove(self):
        if self.identity is not None and self.state in ("PENDING", "OWNED"):
            parent = store._open_dir_nofollow(self.parent_path)
            try:
                opened = os.fstat(parent)
                _require((opened.st_dev, opened.st_ino) == self.parent_identity,
                         CANNOT_EVALUATE, "cleanup", "cleanup parent changed")
                try:
                    named = os.stat(self.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    self.state = "REMOVED"
                    return
                _require(stat.S_ISDIR(named.st_mode),
                         CANNOT_EVALUATE, "cleanup", "cleanup entry is not a directory")
                _require((named.st_dev, named.st_ino) == self.identity,
                         CANNOT_EVALUATE, "cleanup", "cleanup entry identity changed")
                shutil.rmtree(self.name, dir_fd=parent)
                self.state = "REMOVED"
            finally:
                os.close(parent)

    def finish(self, keep):
        if not keep:
            self.remove()


@contextlib.contextmanager
def _quarantine(root, owners):
    store._journal.require_containment()
    with contextlib.ExitStack() as stack:
        def hold(fd):
            stack.callback(os.close, fd)
            return fd

        root_fd = hold(store._open_dir_nofollow(root))
        working = hold(_open_directory(root_fd, ".working"))
        adopt = hold(_open_directory(working, "adopt"))
        # Independent of the public request ID: never infer a capability from it.
        run_name = "observe-" + secrets.token_hex(16)
        owner = _QuarantineOwner(adopt, run_name, root + "/.working/adopt")
        owners.append(owner)
        run = hold(_open_directory(
            adopt, run_name, fresh=True, owner=owner,
        ))
        quarantine = hold(_open_directory(run, "quarantine", fresh=True))
        path = root + "/.working/adopt/" + run_name + "/quarantine"
        for entry in list(sys.path) + os.environ.get("PATH", "").split(os.pathsep):
            if not isinstance(entry, str):
                continue
            absolute = os.path.abspath(entry or os.getcwd())
            _require(
                absolute != path and not absolute.startswith(path + "/"),
                CANNOT_EVALUATE, "quarantine",
                "quarantine overlaps an import or executable search entry",
            )
        yield quarantine, path


def _put(parent, name, payload, deadline):
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600, dir_fd=parent,
    )
    try:
        opened = os.fstat(fd)
        _require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1,
                 CANNOT_EVALUATE, "quarantine", "output is not an exclusive regular file")
        os.fchmod(fd, 0o600)
        remaining = memoryview(payload)
        while remaining:
            deadline.left()
            written = os.write(fd, remaining[:65536])
            _require(written > 0, CANNOT_EVALUATE, "quarantine", "short file write")
            remaining = remaining[written:]
        deadline.left()
    finally:
        os.close(fd)


def _read_archive(parent, deadline):
    before = os.stat("archive.tar.gz", dir_fd=parent, follow_symlinks=False)
    fd = os.open(
        "archive.tar.gz", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=parent,
    )
    try:
        opened = os.fstat(fd)
        _require(
            stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1
            and planning._stamp(before) == planning._stamp(opened)
            and opened.st_size <= MAX_ARCHIVE_BYTES,
            CANNOT_EVALUATE, "quarantine", "archive inode or bound changed",
        )
        data = bytearray()
        while True:
            deadline.left()
            block = os.read(fd, min(65536, MAX_ARCHIVE_BYTES - len(data) + 1))
            if not block:
                break
            data.extend(block)
            _require(len(data) <= MAX_ARCHIVE_BYTES,
                     CANNOT_EVALUATE, "quarantine", "archive read exceeds bound")
        _require(
            planning._stamp(os.fstat(fd)) == planning._stamp(opened)
            and planning._stamp(os.stat(
                "archive.tar.gz", dir_fd=parent, follow_symlinks=False,
            )) == planning._stamp(opened),
            CANNOT_EVALUATE, "quarantine", "archive changed during read",
        )
        return bytes(data)
    finally:
        os.close(fd)


def _inflate(archive, deadline):
    _require(archive.startswith(b"\x1f\x8b"),
             CANNOT_EVALUATE, "archive", "unsupported compression dialect")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    remaining = archive
    output = bytearray()
    while True:
        deadline.left()
        allowance = min(65536, MAX_TOTAL_BYTES - len(output) + 1)
        block = decoder.decompress(remaining, allowance)
        output.extend(block)
        _require(len(output) <= MAX_TOTAL_BYTES,
                 INVALID, "archive", "total archive expansion exceeds bound")
        remaining = decoder.unconsumed_tail
        if decoder.eof:
            _require(not decoder.unused_data and not remaining,
                     INVALID, "archive", "concatenated gzip or trailing data")
            return bytes(output)
        _require(bool(block) or bool(remaining),
                 CANNOT_EVALUATE, "archive", "truncated gzip stream")


def _tar_text(field):
    head, separator, tail = field.partition(b"\0")
    _require(not separator or not any(tail),
             INVALID, "archive", "conflicting string field padding")
    try:
        return head.decode("utf-8")
    except UnicodeError:
        raise ObserveError(CANNOT_EVALUATE, "archive", "unparseable member name")


def _archive_member_policy(info, global_header=False):
    known_special = (tarfile.LNKTYPE, tarfile.SYMTYPE, tarfile.CHRTYPE,
                     tarfile.BLKTYPE, tarfile.FIFOTYPE)
    _require(info.type not in known_special,
             INVALID, "archive", "link or special member")
    allowed = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
    if global_header:
        allowed += (tarfile.XGLTYPE,)
    _require(info.type in allowed,
             CANNOT_EVALUATE, "archive", "unsupported tar member dialect")
    _require(
        not info.linkname and info.devmajor == 0 and info.devminor == 0
        and info.sparse is None and 0 <= info.mode <= 0o7777
        and info.size >= 0
        and (info.type != tarfile.DIRTYPE or info.size == 0),
        INVALID, "archive", "conflicting member metadata",
    )


def _member_path(name, directory):
    if directory and name.endswith("/"):
        name = name[:-1]
    _require(manifest._path_ok(name),
             INVALID, "archive", "uncontained member path")
    name.encode("utf-8")
    return name


def _unpack(parent, archive, deadline, commit=None):
    raw = _inflate(archive, deadline)
    _require(len(raw) % 512 == 0, CANNOT_EVALUATE, "archive",
             "truncated tar block")
    offset = 0
    wrapper = None
    explicit = set()
    nodes = {}
    files = []
    entry_count = 0
    raw_path_bytes = 0
    materialized_path_bytes = 0
    payload_bytes = 0
    terminated = False

    def add_node(path, kind):
        nonlocal materialized_path_bytes
        old = nodes.get(path)
        _require(old is None or old == kind,
                 INVALID, "archive", "file/directory collision")
        if old is None:
            nodes[path] = kind
            materialized_path_bytes += len(path.encode("utf-8"))
        _require(len(nodes) <= MAX_ENTRIES, INVALID, "archive",
                 "materialized entry bound exceeded")
        _require(materialized_path_bytes <= MAX_PATH_BYTES,
                 INVALID, "archive", "materialized path-byte bound exceeded")

    while offset < len(raw):
        deadline.left()
        header = raw[offset:offset + 512]
        offset += 512
        if header == b"\0" * 512:
            _require(
                raw[offset:offset + 512] == b"\0" * 512
                and not any(raw[offset + 512:]),
                INVALID, "archive", "missing terminator or trailing tar content",
            )
            terminated = True
            break
        _require(header[257:265] == b"ustar\x0000",
                 CANNOT_EVALUATE, "archive", "unsupported tar dialect")
        for start, end in ((100, 108), (108, 116), (116, 124),
                           (124, 136), (136, 148), (148, 156),
                           (329, 337), (337, 345)):
            field = header[start:end]
            _require(not (field[0] & 0x80),
                     CANNOT_EVALUATE, "archive", "unsupported numeric encoding")
            text = field.strip(b" \0")
            _require(not text or all(48 <= byte <= 55 for byte in text),
                     INVALID, "archive", "malformed USTAR numeric field")
        checksum = header[148:156].strip(b" \0")
        _require(bool(checksum), INVALID, "archive", "missing tar checksum")
        _require(
            int(checksum, 8) == sum(header[:148]) + 8 * 32 + sum(header[156:]),
            INVALID, "archive", "tar checksum mismatch",
        )
        _require(not any(header[500:]), INVALID, "archive",
                 "conflicting USTAR reserved metadata")
        info = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        global_header = info.type == tarfile.XGLTYPE
        _archive_member_policy(info, global_header=global_header)
        # Validate all string fields even on the inert global header.
        for field in (header[:100], header[157:257], header[265:297],
                      header[297:329], header[345:500]):
            _tar_text(field)
        if global_header:
            expected = (b"52 comment=" + commit.encode("ascii") + b"\n"
                        if type(commit) is str and _COMMIT.fullmatch(commit)
                        else None)
            _require(
                offset == 512 and expected is not None
                and header[124:136] == b"00000000064\0"
                and header[:100] == b"pax_global_header".ljust(100, b"\0")
                and not any(header[345:500])
                and header[100:108] == b"0000666\0"
                and header[108:116] == b"0000000\0"
                and header[116:124] == b"0000000\0"
                and header[265:297] == b"root".ljust(32, b"\0")
                and header[297:329] == b"root".ljust(32, b"\0")
                and raw[offset:offset + 512] == expected.ljust(512, b"\0"),
                CANNOT_EVALUATE, "archive",
                "global header is not the sole leading pinned git comment",
            )
            offset += 512
            continue
        directory = info.type == tarfile.DIRTYPE
        leaf = _tar_text(header[:100])
        prefix = _tar_text(header[345:500])
        name = _member_path((prefix + "/" if prefix else "") + leaf, directory)
        # Validate even unused string fields; do not accept shadow metadata.
        for field in (header[157:257], header[265:297], header[297:329]):
            _tar_text(field)

        entry_count += 1
        raw_path_bytes += len(name.encode("utf-8"))
        _require(entry_count <= MAX_ENTRIES, INVALID, "archive",
                 "archive entry bound exceeded")
        _require(raw_path_bytes <= MAX_PATH_BYTES, INVALID, "archive",
                 "archive path-byte bound exceeded")
        components = name.split("/")
        if wrapper is None:
            wrapper = components[0]
        _require(components[0] == wrapper, INVALID, "archive",
                 "multiple wrapper directories")
        path = "/".join(components[1:])
        _require(path not in explicit, INVALID, "archive",
                 "duplicate effective path after wrapper removal")
        explicit.add(path)
        _require(len(components) - 1 <= MAX_DEPTH, INVALID, "archive",
                 "member depth bound exceeded")
        _require(bool(path) or directory, INVALID, "archive",
                 "wrapper is not a directory")
        _require(info.size <= MAX_FILE_BYTES, INVALID, "archive",
                 "member size bound exceeded")
        payload_bytes += info.size
        _require(payload_bytes <= MAX_TOTAL_BYTES, INVALID, "archive",
                 "member expansion bound exceeded")

        end = offset + info.size
        padded_end = offset + ((info.size + 511) // 512) * 512
        _require(padded_end <= len(raw), CANNOT_EVALUATE, "archive",
                 "truncated member content")
        _require(not any(raw[end:padded_end]), INVALID, "archive",
                 "nonzero member padding")
        payload = memoryview(raw)[offset:end]
        offset = padded_end
        if path:
            pieces = path.split("/")
            for count in range(1, len(pieces)):
                add_node("/".join(pieces[:count]), "directory")
            add_node(path, "directory" if directory else "file")
            if not directory:
                files.append((path, info.mode, payload))

    _require(terminated and wrapper is not None,
             INVALID, "archive", "archive has no supported wrapped member stream")
    members_fd = _open_directory(parent, "members", fresh=True)
    try:
        for path, kind in sorted(nodes.items(), key=lambda item: (item[0].count("/"), item[0])):
            if kind != "directory":
                continue
            pfd, name = store._journal._open_parent(members_fd, path)
            try:
                child = _open_directory(pfd, name, fresh=True)
                os.close(child)
            finally:
                os.close(pfd)
        result = []
        budget = [0]
        for path, mode, payload in files:
            deadline.left()
            pfd, name = store._journal._open_parent(members_fd, path)
            try:
                _put(pfd, name, payload, deadline)
                before = os.stat(name, dir_fd=pfd, follow_symlinks=False)
                reread = planning._read(pfd, name, before, budget)
                _require(reread == payload, CANNOT_EVALUATE, "quarantine",
                         "member reread differs from written bytes")
                _require(stat.S_IMODE(before.st_mode) == 0o600,
                         CANNOT_EVALUATE, "quarantine", "member is executable or non-private")
            finally:
                os.close(pfd)
            result.append({"path": path, "size": len(payload), "archive_mode": mode})
        return result
    finally:
        os.close(members_fd)


def _work(request, policy, observation, notes, deadline, owners):
    request, policy = _validate(request, policy)
    observation["release_identity"] = {
        "version": request["version"], "commit": request["commit"],
    }
    with _environment():
        context = _client_context()
        with _quarantine(
            request["product_root"], owners,
        ) as (qfd, path):
            archive = None
            anchors = []
            destinations = [
                ("archive", policy["release_url"], MAX_ARCHIVE_BYTES),
                *[("anchor", url, MAX_ANCHOR_BYTES) for url in policy["anchors"]],
            ]
            for kind, url, cap in destinations:
                try:
                    body = _fetch(url, cap, deadline, context)
                except ObserveError as exc:
                    _note(notes, exc.status, exc.phase, exc.detail)
                    continue
                except (OSError, ssl.SSLError) as exc:
                    _note(notes, CANNOT_EVALUATE, kind,
                          "transport could not complete: " + type(exc).__name__)
                    continue
                if kind == "archive":
                    archive = body
                    observation["archive"] = {
                        "url": url, "size": len(body),
                        "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                    }
                else:
                    anchors.append({"url": url, "body": body})
            if anchors:
                observation["anchors"] = anchors
            if notes:
                return
            _require(archive is not None and len(anchors) == len(policy["anchors"]),
                     CANNOT_EVALUATE, "observation", "required response omitted")
            _put(qfd, "archive.tar.gz", archive, deadline)
            reread = _read_archive(qfd, deadline)
            _require(reread == archive, CANNOT_EVALUATE, "quarantine",
                     "archive reread differs from response")
            members = _unpack(qfd, reread, deadline, request["commit"])
            deadline.left()
        # Publish the locator only after descriptor teardown succeeded.
        observation["quarantine"] = path
        observation["members"] = members
    deadline.left()


def _seal_observation(observation, notes):
    document = dict(observation)
    if "anchors" in document:
        document["anchors"] = [
            {"url": row["url"], "body_hex": row["body"].hex()}
            for row in document["anchors"]
        ]
    document["notes"] = list(notes)
    return planning._seal(document, "observation_digest")


def _finish_observation(observation, notes, owners):
    keep = observation.get("status") == VALID and "record" in observation
    for owner in owners:
        try:
            owner.finish(keep)
        except Exception as exc:
            observation.pop("quarantine", None)
            observation.pop("members", None)
            observation.pop("record", None)
            observation["status"] = CANNOT_EVALUATE
            _note(notes, CANNOT_EVALUATE, "cleanup",
                  "quarantine cleanup or descriptor close failed; "
                  "private residue may remain: " + type(exc).__name__)


def gather_release(request, policy):
    """Return inert evidence and structured omission notes; never a trust verdict."""
    observation = {}
    notes = []
    owners = []
    try:
        _gather_release(request, policy, observation, notes, owners)
        _finish_observation(observation, notes, owners)
        # The return itself is the disarm; no finally runs after this handoff.
        return observation, notes
    except BaseException:
        observation.clear()
        for owner in owners:
            try:
                owner.finish(False)
            except Exception as exc:
                _note(notes, CANNOT_EVALUATE, "cleanup",
                      "quarantine rollback failed; private residue may remain: "
                      + type(exc).__name__)
        raise


def _gather_release(request, policy, observation, notes, owners):
    try:
        observation.update({
            "format": OBSERVATION_FORMAT,
            "request_id": "adopt-{}-{}".format(
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                secrets.token_hex(8),
            ),
            "captured_monotonic_ns": _capture_instant(),
        })
        deadline = _Deadline(GATHER_SECONDS)
        _work(request, policy, observation, notes, deadline, owners)
    except ObserveError as exc:
        _note(notes, exc.status, exc.phase, exc.detail)
    except Exception as exc:
        _note(notes, CANNOT_EVALUATE, "observer",
              "unexpected observer failure: " + type(exc).__name__)
    except BaseException:
        observation.clear()
        raise

    if notes:
        observation.pop("quarantine", None)
        observation.pop("members", None)
    observation["status"] = (
        CANNOT_EVALUATE if any(row["status"] == CANNOT_EVALUATE for row in notes)
        else INVALID if notes else VALID
    )
    try:
        observation["record"] = _seal_observation(observation, notes)
    except Exception as exc:
        observation.pop("quarantine", None)
        observation.pop("members", None)
        observation.pop("record", None)
        observation["status"] = CANNOT_EVALUATE
        _note(notes, CANNOT_EVALUATE, "record",
              "observation could not be sealed: " + type(exc).__name__)


def _ownership_self_test():
    """Real directory ownership probes; no transport or mocked mkdir outcome."""
    import tempfile
    from unittest.mock import patch

    module = sys.modules[__name__]
    original_remove = _QuarantineOwner.remove
    original_require = _require
    rows = []

    def delete_pending(owner):
        # Reintroduce name-only deletion authority for the pending-state mutant.
        if owner.state == "PENDING" and owner.identity is None:
            parent = store._open_dir_nofollow(owner.parent_path)
            try:
                shutil.rmtree(owner.name, dir_fd=parent)
            finally:
                os.close(parent)
        else:
            original_remove(owner)

    def skip_identity(condition, status, phase, detail):
        if detail != "cleanup entry identity changed":
            original_require(condition, status, phase, detail)

    def probe(kind):
        with tempfile.TemporaryDirectory(prefix="opf-owner-", dir="/dev/shm") as temp:
            root = Path(temp)
            run = root / "run"
            parent = store._open_dir_nofollow(temp)
            try:
                owner = _QuarantineOwner(parent, "run", temp)
                if kind == "identity-mismatch":
                    fd = _open_directory(parent, "run", fresh=True, owner=owner)
                    try:
                        opened = os.fstat(fd)
                        if owner.identity != (opened.st_dev, opened.st_ino):
                            raise AssertionError("ownership identity was not captured")
                    finally:
                        os.close(fd)
                    # Retain the original inode, preventing reuse by the replacement.
                    run.rename(root / "saved")
                if kind != "before-identity":
                    run.mkdir(mode=0o700)
                    (run / "foreign").write_bytes(b"preserve\n")
                    foreign = run.stat()

                if kind == "eexist":
                    boundary = []
                    previous_trace = sys.gettrace()

                    def collide(frame, event, arg):
                        if frame.f_code is _open_directory.__code__:
                            if event == "exception" and isinstance(arg[1], FileExistsError):
                                boundary.append("EEXIST")
                            elif event == "line" and boundary == ["EEXIST"]:
                                boundary.append("cancelled")
                                raise KeyboardInterrupt
                        return collide

                    try:
                        sys.settrace(collide)
                        try:
                            _open_directory(parent, "run", fresh=True, owner=owner)
                        except KeyboardInterrupt:
                            pass
                    finally:
                        sys.settrace(previous_trace)
                    if (boundary != ["EEXIST", "cancelled"]
                            or owner.state != "PENDING" or owner.identity is not None):
                        raise AssertionError("fixture missed the pending EEXIST boundary")
                elif kind == "before-identity":
                    original_open = os.open
                    interrupted = []

                    def interrupt_open(name, flags, *args, **kwargs):
                        if name == "run" and kwargs.get("dir_fd") == parent:
                            interrupted.append(True)
                            raise KeyboardInterrupt
                        return original_open(name, flags, *args, **kwargs)

                    with patch.object(os, "open", interrupt_open):
                        try:
                            _open_directory(parent, "run", fresh=True, owner=owner)
                        except KeyboardInterrupt:
                            pass
                    if interrupted != [True] or owner.identity is not None:
                        raise AssertionError("fixture missed the pre-identity boundary")

                refusal = None
                try:
                    owner.remove()
                except ObserveError as exc:
                    refusal = (exc.status, exc.phase, exc.detail)
                try:
                    current = run.stat()
                    entries = sorted(path.name for path in run.iterdir())
                except FileNotFoundError:
                    return False
                if kind == "before-identity":
                    return refusal is None and entries == [] and owner.state == "PENDING"
                preserved = (
                    (current.st_dev, current.st_ino) == (foreign.st_dev, foreign.st_ino)
                    and entries == ["foreign"]
                    and (run / "foreign").read_bytes() == b"preserve\n"
                )
                if kind == "identity-mismatch":
                    saved = (root / "saved").stat()
                    return (preserved and (saved.st_dev, saved.st_ino) == owner.identity
                            and refusal == (CANNOT_EVALUATE, "cleanup",
                                            "cleanup entry identity changed"))
                return preserved and refusal is None
            finally:
                os.close(parent)

    for kind in ("eexist", "identity-mismatch", "before-identity"):
        baseline = probe(kind)
        mutation = ("skip-identity-check" if kind == "identity-mismatch"
                    else "delete-on-pending")
        target = (patch.object(module, "_require", skip_identity)
                  if kind == "identity-mismatch"
                  else patch.object(_QuarantineOwner, "remove", delete_pending))
        with target:
            mutant = probe(kind)
        rows.append({
            "id": "unit/ownership/" + kind, "guard": mutation,
            "expected": VALID, "observed": VALID if baseline else INVALID,
            "mutant_observed": VALID if mutant else INVALID,
            "mutation_detected": not mutant,
            "test_status": VALID if baseline and not mutant else INVALID,
        })
    return rows


def _guard_self_test():
    """Exact named-guard mutations, including independent HTTP wire vectors.

    These supplement the end-to-end TLS fixtures. A unit mutant may still be
    refused by a later containment guard; a changed status makes the original
    status discriminator red without weakening that second guard.
    """
    import gzip
    import tempfile
    from unittest.mock import patch

    module = sys.modules[__name__]
    original = _require
    rows = []

    def check(identifier, guard, expected, probe):
        def status():
            try:
                return probe()
            except ObserveError as exc:
                return exc.status
            except Exception:
                return CANNOT_EVALUATE

        baseline = status()

        def disabled(condition, result, phase, detail):
            if detail != guard:
                original(condition, result, phase, detail)

        with patch.object(module, "_require", disabled):
            mutant = status()
        rows.append({
            "id": identifier, "guard": guard, "expected": expected,
            "observed": baseline, "mutant_observed": mutant,
            "mutation_detected": mutant != expected,
            "test_status": VALID if baseline == expected and mutant != expected else INVALID,
        })

    class LocalWire(_Wire):
        def __init__(self, raw):
            self.raw = raw
            self.buffer = bytearray()
            self.deadline = _Deadline(1)

        def _receive(self):
            result, self.raw = self.raw[:7], self.raw[7:]
            return result

    http_cases = (
        ("truncated", "body ended before its declared framing",
         b"Content-Length: 20\r\n\r\nshort", 64),
        ("conflict", "conflicting body framing",
         b"Content-Length: 3\r\nTransfer-Encoding: chunked\r\n\r\nabc", 64),
        ("duplicate", "duplicate header",
         b"Content-Length: 3\r\nContent-Length: 3\r\n\r\nabc", 64),
        ("absent", "unsupported or absent body framing",
         b"\r\n3\r\nabc\r\n0\r\n\r\n", 64),
        ("extra", "bytes follow the framed response",
         b"Content-Length: 3\r\n\r\nabcd", 64),
        ("encoding", "non-identity content encoding",
         b"Content-Length: 3\r\nContent-Encoding: gzip\r\n\r\nabc", 64),
        ("cap", "streamed response bound exceeded",
         b"Content-Length: 3\r\n\r\nabc", 2),
        ("chunk-cap", "streamed response bound exceeded",
         b"Transfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n", 2),
        ("terminator", "invalid chunk terminator",
         b"Transfer-Encoding: chunked\r\n\r\n3\r\nabcXX0\r\n\r\n", 64),
    )
    for name, guard, body, cap in http_cases:
        def probe(body=body, cap=cap):
            _response(LocalWire(b"HTTP/1.1 200 OK\r\n" + body), cap)
            return VALID
        check("unit/http/" + name, guard, CANNOT_EVALUATE, probe)

    root = ("wrap/", tarfile.DIRTYPE, b"")

    def archive(entries):
        blocks = []
        for name, kind, payload in entries:
            member = tarfile.TarInfo(name)
            member.type, member.size = kind, len(payload)
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "outside"
            blocks += [member.tobuf(format=tarfile.USTAR_FORMAT), payload,
                       b"\0" * ((-len(payload)) % 512)]
        return gzip.compress(b"".join(blocks) + b"\0" * 1024, mtime=0)

    archive_cases = [
        ("traversal", "uncontained member path",
         [root, ("wrap/../escape", tarfile.REGTYPE, b"x")], None),
        ("duplicate", "duplicate effective path after wrapper removal",
         [root, ("wrap/a", tarfile.REGTYPE, b"x"),
          ("wrap/a", tarfile.REGTYPE, b"y")], None),
        ("wrapper", "multiple wrapper directories",
         [root, ("other/a", tarfile.REGTYPE, b"x")], None),
        ("collision", "file/directory collision",
         [root, ("wrap/a", tarfile.REGTYPE, b"x"),
          ("wrap/a/b", tarfile.REGTYPE, b"x")], None),
        ("entries", "archive entry bound exceeded",
         [root, ("wrap/a", tarfile.REGTYPE, b"x"),
          ("wrap/b", tarfile.REGTYPE, b"x")], ("MAX_ENTRIES", 2)),
        ("depth", "member depth bound exceeded",
         [root, ("wrap/a/b/c", tarfile.REGTYPE, b"x")], ("MAX_DEPTH", 2)),
        ("paths", "archive path-byte bound exceeded",
         [root, ("wrap/" + "a" * 20, tarfile.REGTYPE, b"x")], ("MAX_PATH_BYTES", 24)),
        ("member", "member size bound exceeded",
         [root, ("wrap/a", tarfile.REGTYPE, b"x" * 65)], ("MAX_FILE_BYTES", 64)),
        ("expansion", "total archive expansion exceeds bound",
         [root, ("wrap/a", tarfile.REGTYPE, b"x" * 3000)], ("MAX_TOTAL_BYTES", 4096)),
    ]
    for name, kind in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE),
                       ("fifo", tarfile.FIFOTYPE), ("char", tarfile.CHRTYPE),
                       ("block", tarfile.BLKTYPE)):
        archive_cases.append((name, "link or special member",
                              [root, ("wrap/a", kind, b"")], None))
    for name, guard, entries, limit in archive_cases:
        raw = archive(entries)

        def probe(raw=raw):
            with tempfile.TemporaryDirectory(prefix="opf-observe-guard-") as tmp:
                fd = store._open_dir_nofollow(tmp)
                try:
                    _unpack(fd, raw, _Deadline(5))
                finally:
                    os.close(fd)
            return VALID

        with contextlib.ExitStack() as stack:
            if limit:
                stack.enter_context(patch.object(module, *limit))
            check("unit/archive/" + name, guard, INVALID, probe)
    expected_ids = (["unit/http/" + case[0] for case in http_cases]
                    + ["unit/archive/" + case[0] for case in archive_cases])
    if [row["id"] for row in rows] != expected_ids:
        raise AssertionError("unit/guard-roster")
    return rows


def _runner_check(expected, text=None, *, fail_own=0, scratch_only=False):
    """Prove exact dispatch using the real shell text, as the P0 suite does.

    Intercepted Python gates are stubbed. Only this suite's vector leg runs,
    except for the umask scratch-only case, which emits canned output.
    Vector-only dispatch avoids recursive registration checks.
    The scope is this suite only.
    """
    import errno
    import fcntl
    import json
    import shlex
    import subprocess
    import tempfile

    identity = "runner/adopt-observe-registration"
    # Vector-only entry points never call this registration check.
    # Refuse an escaped fixture invocation before any shell can launch.
    if "observe_log" in os.environ:
        raise RuntimeError(identity + "/cannot-evaluate/recursion")
    # env -i removes the environment marker, but preserves pass_fds. Use
    # the same private-file payload as the P0 and pack registration checks,
    # without an env-supplied descriptor number. Inspection errors refuse.
    marker_bytes = b"OPF runner registration recursion v1\n"
    try:
        for entry in os.listdir("/dev/fd"):
            fd = int(entry)
            try:
                info = os.fstat(fd)
            except OSError as exc:
                # The descriptor used to list /dev/fd has already closed.
                if exc.errno == errno.EBADF:
                    continue
                raise
            if stat.S_ISREG(info.st_mode) and info.st_size == len(marker_bytes):
                # Admit only readable descriptors. O_PATH has O_RDONLY access
                # bits but cannot be read. A failed flag query still refuses.
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                if (flags & getattr(os, "O_PATH", 0)
                        or flags & os.O_ACCMODE not in (os.O_RDONLY, os.O_RDWR)):
                    continue
                # Readable candidates still fail closed on inspection errors.
                if os.pread(fd, len(marker_bytes), 0) == marker_bytes:
                    raise RuntimeError(identity + "/cannot-evaluate/recursion")
    except (OSError, ValueError) as exc:
        raise RuntimeError(identity + "/cannot-evaluate/recursion-marker") from exc
    if type(fail_own) is not int or fail_own not in (0, 1, 2, 7):
        raise ValueError(identity + "/invalid-failure-code")
    here = Path(__file__).resolve().parent
    runner = here / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8") if text is None else text
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError(identity + "/cannot-evaluate/bash")
    bash = os.path.abspath(bash)

    # This self-test asserts that the runner dispatches THIS suite exactly
    # once with its exact argv (RED duplicate-own-call and wrong-own-argv,
    # using the runtime argv log), and propagates exits 1, 2 and 7 (RED
    # own-suite-failure variants, discriminated by swallowed-own-failure
    # and tolerate-1/tolerate-2). Other nonzero statuses are not injected.
    # It does not assert that other registered suites are dispatched:
    # sibling dispatch completeness is outside this check; a runner-level
    # dispatch audit would be a separate control.
    # In-runner PATH changes remain covered by the
    # function shim (PASS in-runner-path; RED removed-function-shim).
    # The PATH fixture also covers child shells, command and env forms.
    # Deliberately evasive runners are outside the threat model, including
    # wrappers recognizing canned output and swallowing real failure or
    # CANNOT diagnostics, and alternate interpreter names such as python3.14.
    # Absolute paths, or bypassing the function together with changing PATH,
    # remain outside interception. Deliberately closing inherited descriptors
    # while clearing the environment can bypass recursion refusal and spawn
    # nested sessions outside timeout killpg containment. Not a process sandbox.
    # Intercepted siblings return 0; their failure propagation is outside
    # this check too.
    fixture = r'''#!/bin/sh
printf '%s\0' "$#" "$@" >> "$observe_log" || exit 2
if [ "$#" -eq 4 ] && [ "$1" = "-I" ] && [ "$2" = "-B" ] \
    && [ "$3" = "$observe_test" ] && [ "$4" = "--self-test" ]; then
  if [ "$observe_scratch_only" -eq 1 ]; then
    printf '%s\n' "$observe_expected_output"
    exit "$observe_fail_own"
  fi
  if [ "$observe_fail_own" -ne 0 ]; then
    "$observe_python" -I -B "$observe_test" --self-test --vectors-only || exit "$?"
    exit "$observe_fail_own"
  fi
  exec "$observe_python" -I -B "$observe_test" --self-test --vectors-only
fi
case " $* " in *_opf_adopt_observe.py*) exit 2;; esac
exit 0
'''
    # Preserve ordinary caller variables (including CI) so conditional
    # dispatch is exercised. Remove the execution controls listed below, then
    # pin configuration and fixture variables; not an environment sandbox.
    # The runner resolves scripts from $0, so caller cwd is safe to preserve.
    caller_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opf-observe-registration-") as tmp, \
            contextlib.ExitStack() as resources:
        os.chmod(tmp, 0o700)
        marker = resources.enter_context(tempfile.TemporaryFile(dir=tmp))
        marker.write(marker_bytes)
        marker.flush()
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        executable = Path(tmp) / "python3"
        executable.write_text(fixture, encoding="utf-8")
        executable.chmod(0o700)
        log = Path(tmp) / "argv.log"
        log.write_bytes(b"")
        log.chmod(0o600)
        env = {name: value for name, value in os.environ.items()
               if name not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4")
               and not name.startswith(("GIT_", "BASH_FUNC_", "PYTHON", "LD_"))}
        env.update({name: tmp for name in env if name.startswith("XDG_")})
        env.update({"PATH": tmp + os.pathsep + os.defpath, "TMPDIR": tmp,
                    "HOME": tmp, "XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp,
                    "XDG_DATA_HOME": tmp, "XDG_STATE_HOME": tmp,
                    "XDG_CONFIG_DIRS": tmp, "XDG_DATA_DIRS": tmp,
                    "XDG_RUNTIME_DIR": tmp, "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1", "observe_log": str(log),
                    "observe_python": sys.executable,
                    "observe_test": str(here / "_opf_adopt_observe.py"),
                    "observe_fail_own": str(fail_own),
                    "observe_scratch_only": "1" if scratch_only else "0",
                    "observe_expected_output": (
                        json.dumps({"opf_adopt_observe_tests": [
                            {"id": item, "test_status": VALID} for item in expected
                        ]}, sort_keys=True) + "\n"
                        + "\n".join("PASS " + item for item in expected)
                        if scratch_only else "")})

        def run_shell(body):
            with subprocess.Popen(
                    [bash, "--noprofile", "--norc", "-c", body, str(runner)],
                    cwd=caller_cwd, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                    pass_fds=(marker.fileno(),)) as proc:
                try:
                    stdout, stderr = proc.communicate(timeout=120)
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
        # Requires dirname on this PATH and executable /bin/sh; absence
        # fails closed as cannot-evaluate/interception, never a clean skip.
        probe = run_shell("type -P dirname >/dev/null && test -x /bin/sh && "
                          "type -P python3")
        if probe.returncode != 0 or probe.stdout != str(executable) + "\n":
            raise RuntimeError(identity + "/cannot-evaluate/interception")
        # Exec only in the function subshell, so the runner can continue.
        shim = "python3() ( exec " + shlex.quote(str(executable)) + ' "$@" );\n'
        proc = run_shell(shim + source)
        try:
            argv_log = log.read_bytes()
        except OSError as exc:
            raise RuntimeError(identity + "/cannot-evaluate/argv-log") from exc

    # NUL-framed records begin with argc. Any argument containing THIS
    # basename counts as an own-call attempt, even embedded -c source; require
    # one exact argv. Incidental mentions are conservatively attempts too.
    # Dynamically constructed names without that substring are not classified.
    # Diagnose malformed own calls before their exit or missing output.
    # Absent calls retain the existing return-code/pass-lines identities.
    fields = argv_log.split(b"\0")
    if fields.pop() != b"":
        raise AssertionError(identity + "/own-argv")
    own = []
    offset = 0
    basename = os.fsencode(Path(env["observe_test"]).name)
    while offset < len(fields):
        count = fields[offset]
        offset += 1
        try:
            argc = int(count)
        except ValueError as exc:
            raise AssertionError(identity + "/own-argv") from exc
        if count != str(argc).encode("ascii") or argc < 0 or argc > len(fields) - offset:
            raise AssertionError(identity + "/own-argv")
        argv = tuple(fields[offset:offset + argc])
        offset += argc
        if any(basename in arg for arg in argv):
            own.append(argv)
    own_argv = tuple(os.fsencode(arg) for arg in (
        "-I", "-B", env["observe_test"], "--self-test"))
    if own and own != [own_argv]:
        raise AssertionError(identity + "/own-argv")

    if proc.returncode != 0:
        raise AssertionError(identity + "/return-code")
    try:
        reports = [json.loads(line)["opf_adopt_observe_tests"]
                   for line in proc.stdout.splitlines()
                   if line.startswith('{"opf_adopt_observe_tests":')]
        # Timing measurements vary; compare roster identities and statuses.
        if (len(reports) != 1
                or [row["id"] for row in reports[0]] != expected
                or any(row["test_status"] != VALID for row in reports[0])):
            raise AssertionError(identity + "/pass-lines")
    except (ValueError, KeyError, TypeError) as exc:
        raise AssertionError(identity + "/pass-lines") from exc

    if own != [own_argv]:
        raise AssertionError(identity + "/own-argv")


def _runner_non_readable_fd_checks(expected):
    import json
    import subprocess
    import tempfile

    kinds = [("write-only", os.O_WRONLY | os.O_APPEND)]
    if hasattr(os, "O_PATH"):
        kinds.append(("path", os.O_PATH))
    # Each child exercises the real descriptor scan and one registration leg,
    # including vector dispatch. It does not rerun the full registration REDs
    # or this helper, so no recursive self-test suppression is needed.
    for kind, flags in kinds:
        identity = "runner/adopt-observe-registration/inherited-" + kind + "-fd"
        with tempfile.TemporaryDirectory(prefix="opf-" + kind + "-fd-") as tmp:
            os.chmod(tmp, 0o700)
            log = Path(tmp) / "ordinary.log"
            log.write_bytes(b"x" * 37)
            log.chmod(0o600)
            fd = os.open(log, flags)
            try:
                child = (
                    "import fcntl, importlib, json, os, sys\n"
                    "from pathlib import Path\n"
                    f"assert os.fstat({fd}).st_size == 37\n"
                    f"flags = fcntl.fcntl({fd}, fcntl.F_GETFL)\n"
                    f"assert flags & os.O_ACCMODE == {flags & os.O_ACCMODE}\n"
                    f"assert flags & {flags} == {flags}\n"
                    "sys.argv = sys.argv[1:]\n"
                    "sys.path.insert(0, str(Path(sys.argv[0]).parent))\n"
                    "module = importlib.import_module(Path(sys.argv[0]).stem)\n"
                    "try:\n"
                    "    module._runner_check(json.loads(sys.argv[1]))\n"
                    "except RuntimeError as exc:\n"
                    "    if str(exc) != 'runner/adopt-observe-registration/cannot-evaluate/timeout':\n"
                    "        raise\n"
                    "    print(str(exc), file=sys.stderr)\n"
                    "    raise SystemExit(2)\n")
                try:
                    # One _runner_check has two shell calls, each bounded by
                    # 120s plus 5s cleanup. Allow 50s more for setup/teardown.
                    proc = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", child,
                         str(Path(__file__).resolve()), json.dumps(expected)],
                        pass_fds=(fd,), capture_output=True, text=True, timeout=300)
                except subprocess.TimeoutExpired as exc:
                    print(identity + ": child timed out after {}s; stderr={!r}".format(
                        exc.timeout, exc.stderr), file=sys.stderr)
                    raise RuntimeError(identity + "/cannot-evaluate/timeout") from exc
                except Exception as exc:
                    print(identity + ": child launch failed: {!r}".format(exc), file=sys.stderr)
                    raise AssertionError(identity) from exc
                if proc.returncode != 0:
                    # Surface the child's own diagnostics; failure identities stay exact.
                    print(identity + ": child rc={}\n{}".format(proc.returncode, proc.stderr),
                          file=sys.stderr)
                    if proc.returncode == 2:
                        raise RuntimeError(identity + "/cannot-evaluate/timeout")
                    raise AssertionError(identity)
            finally:
                os.close(fd)
        print("PASS " + identity)


def _runner_red_checks(expected):
    import shlex
    import subprocess
    import tempfile
    from unittest.mock import patch

    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    identity = "runner/adopt-observe-registration"
    anchor = '  local name="$1"; shift\n'
    if source.count(anchor) != 1:
        raise AssertionError(identity + "/red-fixture")

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

    # Set CI in the caller, not in the constructed runner environment.
    with patch.dict(os.environ, {"CI": "true"}):
        red("ci-conditional-skip", lambda: _runner_check(
            expected, source.replace(
                anchor, anchor + '  case "${CI:-}:$name" in '
                '?*:opf-adopt-observe-selftest) return 0;; esac\n', 1)),
            AssertionError, identity + "/pass-lines")

    # Exercise caller-cwd dispatch from the repository or standalone root.
    root = runner.parents[2]
    relative_runner = runner.relative_to(root)
    with contextlib.chdir(root):
        if not relative_runner.is_file():
            raise AssertionError(identity + "/cwd-fixture")
        red("cwd-conditional-skip", lambda: _runner_check(
            expected, source.replace(
                anchor, anchor + '  if [ -e ' + shlex.quote(str(relative_runner)) + ' ]; then '
                'case "$name" in opf-adopt-observe-selftest) return 0;; esac; fi\n', 1)),
            AssertionError, identity + "/pass-lines")

    # Exit from the dispatcher before the runner can report success.
    red("return-code", lambda: _runner_check(
        expected, source.replace(anchor, anchor + "  exit 1\n", 1)),
        AssertionError, identity + "/return-code")

    # Run the real vector leg, then fail THIS suite inside the fixture.
    def own_failure(text, status=7):
        label = "own-suite-failure" if status == 7 else "own-suite-failure-" + str(status)
        red(label, lambda: _runner_check(
            expected, text, fail_own=status), AssertionError, identity + "/return-code")

    for status in (1, 2, 7):
        own_failure(source, status)
    propagation = '  if "$@"; then :; else failed=1; fi'
    if source.count(propagation) != 1:
        raise AssertionError(identity + "/red-fixture")

    # The failure RED must go not-red if run_gate swallows the suite's exit.
    red("swallowed-own-failure", lambda: own_failure(
        source.replace(propagation, '  "$@" || true', 1)),
        AssertionError, identity + "/own-suite-failure/not-red")

    # Selective wrappers must defeat the corresponding failure RED.
    for status in (1, 2):
        wrapper = ('tolerate_own() { "$@"; local rc=$?; if [ "$rc" -eq '
                   + str(status) + ' ]; then return 0; fi; return "$rc"; }\n')
        mutant = wrapper + source.replace(
            anchor, anchor + '  set -- tolerate_own "$@"\n', 1)
        red("tolerate-" + str(status),
            lambda: own_failure(mutant, status), AssertionError,
            identity + "/own-suite-failure-" + str(status) + "/not-red")

    # Suppressed output cannot hide additional or re-argued own-suite calls.
    for label, command in (
        ("duplicate-own-call", '  "$@" >/dev/null 2>&1'),
        ("wrong-own-argv", '  "$@" --unexpected >/dev/null 2>&1 || true'),
        ("embedded-own-call", '  python3 -I -B -c "import runpy; '
         "runpy.run_path('$here/_opf_adopt_observe.py', run_name='__main__')"
         '" --self-test >/dev/null 2>&1 || true'),
    ):
        red(label, lambda command=command: _runner_check(
            expected, source.replace(propagation, propagation + "\n" + command, 1)),
            AssertionError, identity + "/own-argv")

    # Drop a required flag from exactly this suite's registration.
    own_lines = [line for line in source.splitlines(keepends=True)
                 if line.startswith('run_gate "opf-adopt-observe-selftest"')]
    if len(own_lines) != 1 or own_lines[0].count(" --self-test") != 1:
        raise AssertionError(identity + "/red-fixture")
    red("dropped-own-flag", lambda: _runner_check(
        expected, source.replace(own_lines[0],
                                 own_lines[0].replace(" --self-test", "", 1), 1)),
        AssertionError, identity + "/own-argv")

    # Presence, including an empty value, must refuse before any bash launch.
    for value in ("", "nested-argv.log"):
        with patch.dict(os.environ, {"observe_log": value}):
            with patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/recursion/unexpected-launch")) as launch:
                red("nested-invocation", lambda: _runner_check(expected), RuntimeError,
                    identity + "/cannot-evaluate/recursion")
                if launch.call_count:
                    raise AssertionError(identity + "/recursion/unexpected-launch")

    # Scrub only our registration's environment. The bounded entry probe
    # loads the real check, but forbids nested Popen even if the guard is
    # reverted. Require the exact child refusal and zero launch attempts,
    # not merely a nonzero outer runner exit.
    with tempfile.TemporaryDirectory(prefix="opf-recursion-red-") as tmp:
        os.chmod(tmp, 0o700)
        report = Path(tmp) / "refusal.txt"
        nested = Path(tmp) / Path(__file__).name
        nested.write_text(
            "import os, runpy, sys\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            f"sys.path.insert(0, {str(runner.parent)!r})\n"
            f"scope = runpy.run_path({str(Path(__file__).resolve())!r})\n"
            "assert 'observe_log' not in os.environ\n"
            "with patch('subprocess.Popen', side_effect=AssertionError('nested launch')) as launch:\n"
            "    try:\n"
            "        scope['_runner_check'](())\n"
            "    except RuntimeError as exc:\n"
            "        assert not launch.called\n"
            f"        Path({str(report)!r}).write_text(str(exc), encoding='utf-8')\n"
            "    else:\n"
            "        raise AssertionError('recursion accepted')\n"
            "raise SystemExit(2)\n", encoding="utf-8")
        nested.chmod(0o600)
        own_line = own_lines[0]
        script_arg = '"$here/_opf_adopt_observe.py"'
        if own_line.count("python3 ") != 1 or own_line.count(script_arg) != 1:
            raise AssertionError(identity + "/red-fixture")
        scrubbed = own_line.replace(
            "python3 ", "env -i PATH=/usr/bin:/bin " + shlex.quote(sys.executable) + " ",
            1).replace(script_arg, shlex.quote(str(nested)), 1)
        refusal_identity = identity + "/scrubbed-environment/wrong-refusal"
        try:
            try:
                _runner_check(expected, source.replace(own_line, scrubbed, 1))
            except AssertionError as exc:
                if str(exc) != identity + "/return-code":
                    raise
            else:
                raise AssertionError("scrubbed runner accepted")
            refusal = report.read_text(encoding="utf-8")
        except Exception as exc:
            # Missing/unreadable reports and unexpected runner outcomes are
            # failures of this RED, not harness cannot-evaluate outcomes.
            raise AssertionError(refusal_identity) from exc
        if refusal != identity + "/cannot-evaluate/recursion":
            raise AssertionError(refusal_identity)
        # The child writes this exact report only after asserting zero Popen
        # attempts. Announce the RED only once that evidence has been read.
        print("RED scrubbed-environment -> " + identity + "/cannot-evaluate/recursion")
        print("PASS " + identity + "/scrubbed-environment/no-nested-launch")

    # A harmless competing executable makes reverting the function safe.
    # The normal check requires exactly one own call in the fixture's log.
    with tempfile.TemporaryDirectory(prefix="opf-runner-path-") as tmp:
        os.chmod(tmp, 0o700)
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        stub = Path(tmp) / "python3"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o700)
        changed_path = "PATH=" + shlex.quote(tmp) + ":$PATH\n" + source
        _runner_check(expected, changed_path)
        print("PASS " + identity + "/in-runner-path")

        original_popen = subprocess.Popen
        removed = 0

        def without_function(*args, **kwargs):
            nonlocal removed
            command = list(args[0])
            if command[4].startswith("python3() ( exec "):
                command[4] = command[4].split("\n", 1)[1]
                removed += 1
            return original_popen(command, *args[1:], **kwargs)

        with patch("subprocess.Popen", side_effect=without_function):
            red("removed-function-shim", lambda: _runner_check(expected, changed_path),
                AssertionError, identity + "/pass-lines")
        if removed != 1:
            raise AssertionError(identity + "/in-runner-path/removed-count")

    # Remove every execute bit, including for root. Permit only the probe:
    # a reverted interception guard must never launch the real runner.
    original_chmod = Path.chmod
    original_popen = subprocess.Popen
    launches = 0
    probe_body = ("type -P dirname >/dev/null && test -x /bin/sh && "
                  "type -P python3")

    def non_executable(path, mode, *args, **kwargs):
        if path.name == "python3":
            mode = 0o600
        return original_chmod(path, mode, *args, **kwargs)

    def probe_only(*args, **kwargs):
        nonlocal launches
        launches += 1
        command = args[0] if args else kwargs.get("args", ())
        if (launches > 1 or len(command) != 6 or command[3] != "-c"
                or command[4] != probe_body):
            raise AssertionError(identity + "/interception/unexpected-launch")
        return original_popen(*args, **kwargs)

    with patch.object(Path, "chmod", non_executable), \
            patch("subprocess.Popen", side_effect=probe_only):
        red("non-executable-fixture", lambda: _runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Remove dirname from the probe's PATH while retaining the fixture.
    # The exact-body/launch-count guard also prevents a bypassed probe from
    # reaching a real runner when this interception check is reverted.
    launches = 0

    def without_dirname(*args, **kwargs):
        kwargs["env"] = dict(kwargs["env"], PATH=kwargs["env"]["TMPDIR"])
        return probe_only(*args, **kwargs)

    with patch("subprocess.Popen", side_effect=without_dirname):
        red("missing-dirname", lambda: _runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Exercise only the harness's scratch permissions: the vector leg's
    # own fixture setup is not required to work under umask 0200.
    # The fixture emits this suite's expected report and PASS lines instead.
    # Discriminates only where TMPDIR has no default ACL and the process
    # lacks CAP_DAC_OVERRIDE (root commonly has it in CI containers).
    # Either a default ACL overriding umask or that capability makes this
    # case non-discriminating: it can pass with or without the chmods.
    saved = os.umask(0o200)
    try:
        try:
            _runner_check(expected, scratch_only=True)
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

    _runner_non_readable_fd_checks(expected)


def _runner_registration_test(expected):
    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    _runner_check(expected, source)
    lines = [line for line in source.splitlines(keepends=True)
             if line.startswith('run_gate "opf-adopt-observe-selftest"')]
    if len(lines) != 1:
        raise AssertionError("runner/adopt-observe-unique-registration")
    try:
        _runner_check(expected, source.replace(lines[0], "", 1))
    except AssertionError as exc:
        if str(exc) != "runner/adopt-observe-registration/pass-lines":
            raise
    else:
        raise AssertionError("runner/adopt-observe-registration-not-red")
    print("PASS runner/adopt-observe-registration")
    print("RED runner-registration -> runner/adopt-observe-registration/pass-lines")
    _runner_red_checks(expected)


def self_test(vectors_only=False):
    """Local fixtures only; report executed rows and require mutation sensitivity.

    Socket routing is replaced only inside this function: production destinations
    and SNI stay unchanged while TCP is delivered to a loopback fixture. The peer
    adapter models the checked public endpoint separately from that physical
    fixture address. There is no production localhost, alternate-CA, timeout,
    verification-disable, or transport-injection option.

    The write-deny harness covers Python open/write/mkdir/unlink/rmdir entry points and
    process-launch entry points. It is not an OS sandbox for arbitrary native
    extension syscalls. The production environment scrub prevents SSL key-log
    output; R1 and R7 remain the native-runtime boundaries.
    """
    import builtins
    import copy
    import gzip
    import io
    import json
    import inspect
    import textwrap
    import subprocess
    import tempfile
    import shutil
    from unittest import mock

    global SELF_TEST_ROSTER
    SELF_TEST_ROSTER = ()
    executed = []
    module = sys.modules[__name__]
    original_context = ssl.create_default_context
    original_fetch = _fetch
    original_tls = _tls
    original_remove = _QuarantineOwner.remove
    original_public_gather = gather_release
    public_ip = "93.184.216.34"
    commit = "a" * 40
    release_url = "https://codeload.github.com/jposluns/guardrails/tar.gz/" + commit
    baseline_anchor = b"synthetic uninterpreted anchor\n"

    def archive(rows):
        blocks = []
        for name, kind, payload in rows:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.mode = 0o755 if name.endswith("pre-commit") else 0o644
            if kind == tarfile.XGLTYPE:
                info.mode = 0o666
                info.uname = info.gname = "root"
            info.size = len(payload)
            if kind in (tarfile.LNKTYPE, tarfile.SYMTYPE):
                info.linkname = "outside"
            if kind in (tarfile.CHRTYPE, tarfile.BLKTYPE):
                info.devmajor = 1
                info.devminor = 3
            blocks.append(info.tobuf(format=tarfile.USTAR_FORMAT))
            blocks.append(payload)
            blocks.append(b"\0" * ((-len(payload)) % 512))
        blocks.append(b"\0" * 1024)
        return gzip.compress(b"".join(blocks), mtime=0)

    root_row = ("wrap/", tarfile.DIRTYPE, b"")
    baseline_archive = archive([root_row, ("wrap/data", tarfile.REGTYPE, b"data")])

    def source_mutant(function, old, new):
        source = textwrap.dedent(inspect.getsource(function))
        if source.count(old) != 1:
            raise AssertionError("mutation must select exactly one source site")
        namespace = {}
        exec(compile(source.replace(old, new, 1), __file__, "exec"),
             module.__dict__, namespace)
        return namespace[function.__name__]

    class WatchdogExpired(BaseException):
        pass

    def watchdog_preconditions():
        if (threading.current_thread() is not threading.main_thread()
                or not all(callable(getattr(signal, name, None)) for name in (
                    "getitimer", "setitimer", "pthread_sigmask", "pthread_kill",
                ))
                or not hasattr(signal, "ITIMER_REAL")
                or not hasattr(signal, "SIGALRM")):
            raise RuntimeError("fixture watchdog primitives unavailable")
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise RuntimeError("fixture watchdog timer already in use")

    @contextlib.contextmanager
    def watchdog(seconds):
        # Independent of every production deadline and timeout. Refuse to
        # replace another caller's active timer.
        watchdog_preconditions()
        previous = signal.getsignal(signal.SIGALRM)

        def expired(signum, frame):
            raise WatchdogExpired()

        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def git_archive_fixture(base):
        executable = shutil.which("git", path=os.defpath)
        if executable is None:
            raise RuntimeError("git archive fixture builder unavailable")
        repo = base / "git-fixture"
        home = base / "git-home"
        template = base / "git-template"
        for directory in (repo, home, template):
            directory.mkdir(mode=0o700)
        # No ambient GIT_* value, HOME, XDG config, template or hooks survives.
        env = {
            "PATH": os.defpath, "HOME": str(home),
            "XDG_CONFIG_HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Archive fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Archive fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }

        def git(*args, data=None):
            return subprocess.run(
                [os.path.abspath(executable), "-C", str(repo),
                 "-c", "core.attributesFile=" + os.devnull, *args],
                input=data, cwd=repo, env=env, check=True, timeout=15,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).stdout

        git("init", "--object-format=sha1", "--template=" + str(template))
        blob = git("hash-object", "-w", "--stdin", data=b"data").strip().decode("ascii")
        git("update-index", "--add", "--cacheinfo", "100644," + blob + ",data")
        tree = git("write-tree").strip().decode("ascii")
        revision = git("commit-tree", tree, data=b"archive fixture\n").strip().decode("ascii")
        if _COMMIT.fullmatch(revision) is None:
            raise AssertionError("fixture did not produce a SHA-1 commit")
        raw = git("archive", "--format=tar", "--prefix=wrap/", revision)
        if (raw[156:157] != tarfile.XGLTYPE
                or raw[512:564] != b"52 comment=" + revision.encode("ascii") + b"\n"):
            raise AssertionError("git archive did not produce its pinned comment")
        return revision, gzip.compress(raw, mtime=0)

    def reply(body, extra=b"", status=b"200 OK", chunked=False):
        if chunked:
            headers = b"Transfer-Encoding: chunked\r\n"
            body = (
                format(len(body), "x").encode("ascii") + b"\r\n" + body
                + b"\r\n0\r\n\r\n"
            )
        else:
            headers = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        return b"HTTP/1.1 " + status + b"\r\n" + headers + extra + b"\r\n" + body

    class Server:
        def __init__(self, context, archive_body, anchor_response, mode="normal"):
            self.context = context
            self.archive_body = archive_body
            self.anchor_response = anchor_response
            self.mode = mode
            self.requests = []
            self.errors = []
            self.stop = threading.Event()
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(8)
            self.listener.settimeout(0.05)
            self.port = self.listener.getsockname()[1]
            self.thread = threading.Thread(target=self.serve, daemon=True)
            self.thread.start()

        def serve(self):
            while not self.stop.is_set():
                try:
                    raw, _ = self.listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                secured = None
                try:
                    raw.settimeout(2.0)
                    if self.mode == "tls-stall":
                        self.stop.wait(1.10)
                        continue
                    secured = self.context.wrap_socket(raw, server_side=True)
                    request = bytearray()
                    while not request.endswith(b"\r\n\r\n"):
                        block = secured.recv(4096)
                        if not block:
                            break
                        request.extend(block)
                        if len(request) > MAX_HEADER_BYTES:
                            raise AssertionError("fixture request exceeded bound")
                    self.requests.append(bytes(request))
                    anchor_request = request.startswith(b"GET /hashes.txt ")
                    response = (
                        self.anchor_response if anchor_request
                        else reply(self.archive_body)
                    )
                    if anchor_request and self.mode in ("slow", "stall"):
                        header, body = response.split(b"\r\n\r\n", 1)
                        secured.sendall(header + b"\r\n\r\n")
                        if self.mode == "stall":
                            self.stop.wait(1.0)
                        else:
                            for byte in body:
                                if self.stop.wait(0.04):
                                    break
                                secured.sendall(bytes([byte]))
                    else:
                        secured.sendall(response)
                    # Produce a genuine TLS close_notify. The peer can close
                    # without completing unwrap; that fixture teardown is benign.
                    try:
                        secured.unwrap().close()
                    except (OSError, ssl.SSLError):
                        pass
                except (OSError, ssl.SSLError):
                    # Expected for wrong-chain/name and deadline cases.
                    pass
                except Exception as exc:
                    self.errors.append(type(exc).__name__)
                finally:
                    if secured is not None:
                        secured.close()
                    raw.close()

        def close(self):
            self.stop.set()
            self.listener.close()
            self.thread.join(timeout=1.0)
            if self.thread.is_alive():
                raise AssertionError("local TLS fixture did not stop")

    def snapshot(root):
        result = {}
        for directory, dirs, files in os.walk(root, followlinks=False):
            relative = Path(directory).relative_to(root)
            if relative == Path(".working"):
                dirs[:] = [name for name in dirs if name != "adopt"]
            for name in files:
                path = Path(directory) / name
                result[str(path.relative_to(root))] = (
                    stat.S_IMODE(path.stat().st_mode), path.read_bytes(),
                )
        return result

    @contextlib.contextmanager
    def deny_effects(root, violations):
        actual_open = os.open
        actual_write = os.write
        actual_mkdir = os.mkdir
        actual_unlink = os.unlink
        actual_rmdir = os.rmdir
        builtin_open = builtins.open
        io_open = io.open

        def absolute(path, dir_fd=None):
            if isinstance(path, int):
                return Path(os.readlink("/proc/self/fd/" + str(path)))
            path = Path(os.fsdecode(path))
            if not path.is_absolute():
                base = (
                    Path(os.readlink("/proc/self/fd/" + str(dir_fd)))
                    if dir_fd is not None else Path.cwd()
                )
                path = base / path
            return Path(os.path.normpath(path))

        def allowed(path, scaffolding=False):
            try:
                parts = path.relative_to(root).parts
            except ValueError:
                return False
            if scaffolding and parts in ((".working",), (".working", "adopt")):
                return True
            if len(parts) < 3 or parts[:2] != (".working", "adopt"):
                return False
            if re.fullmatch(r"observe-[0-9a-f]{32}", parts[2], re.ASCII) is None:
                return False
            if scaffolding and len(parts) == 3:
                return True
            return len(parts) >= 4 and parts[3] == "quarantine"

        def refuse(label):
            violations.append(label)
            raise PermissionError(label)

        def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if writing and not allowed(absolute(path, dir_fd)):
                refuse("write-mode os.open outside quarantine")
            return actual_open(path, flags, mode, dir_fd=dir_fd)

        def guarded_write(fd, data):
            if not allowed(absolute(fd)):
                refuse("os.write outside quarantine")
            return actual_write(fd, data)

        def guarded_mkdir(path, mode=0o777, *, dir_fd=None):
            if not allowed(absolute(path, dir_fd), scaffolding=True):
                refuse("mkdir outside permitted scaffolding")
            return actual_mkdir(path, mode, dir_fd=dir_fd)

        def guarded_unlink(path, *, dir_fd=None):
            if not allowed(absolute(path, dir_fd)):
                refuse("unlink outside quarantine")
            return actual_unlink(path, dir_fd=dir_fd)

        def guarded_rmdir(path, *, dir_fd=None):
            if not allowed(absolute(path, dir_fd), scaffolding=True):
                refuse("rmdir outside permitted scaffolding")
            return actual_rmdir(path, dir_fd=dir_fd)

        def file_open(original):
            def call(file, mode="r", *args, **kwargs):
                if any(flag in mode for flag in "wax+") and not allowed(absolute(file)):
                    refuse("write-mode file open outside quarantine")
                return original(file, mode, *args, **kwargs)
            return call

        def no_process(*args, **kwargs):
            refuse("process execution attempted")

        # The containment probe reads dir_fd support by function identity. The
        # delegating wrappers forward dir_fd, so each inherits exactly its
        # original's actual membership; an unsupported platform still refuses.
        supports_dir_fd = set(os.supports_dir_fd)
        for original, wrapper in ((actual_open, guarded_open),
                                  (actual_mkdir, guarded_mkdir),
                                  (actual_unlink, guarded_unlink),
                                  (actual_rmdir, guarded_rmdir)):
            if original in os.supports_dir_fd:
                supports_dir_fd.add(wrapper)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(os, "open", guarded_open))
            stack.enter_context(mock.patch.object(os, "write", guarded_write))
            stack.enter_context(mock.patch.object(os, "mkdir", guarded_mkdir))
            stack.enter_context(mock.patch.object(os, "unlink", guarded_unlink))
            stack.enter_context(mock.patch.object(os, "rmdir", guarded_rmdir))
            stack.enter_context(mock.patch.object(os, "supports_dir_fd", supports_dir_fd))
            stack.enter_context(mock.patch.object(builtins, "open", file_open(builtin_open)))
            stack.enter_context(mock.patch.object(io, "open", file_open(io_open)))
            stack.enter_context(mock.patch.object(subprocess, "Popen", no_process))
            for name in (
                "system", "fork", "forkpty", "posix_spawn", "posix_spawnp",
                "execl", "execle", "execlp", "execlpe", "execv", "execve",
                "execvp", "execvpe",
            ):
                if hasattr(os, name):
                    stack.enter_context(mock.patch.object(os, name, no_process))
            yield

    # This table is the suite's authoritative executed-case roster. Each row
    # names an outcome and a guard-family mutation, not finding-text patterns.
    cases = []

    def add(identifier, expected=VALID, mutation="archive", **config):
        cases.append((identifier, expected, mutation, config))

    add("TG-05/candidate-policy-is-inert", mutation="candidate",
        archive=archive([
            root_row,
            ("wrap/.aiqt/core/references.toml", tarfile.REGTYPE,
             b'quorum = 0\nlocation = "https://candidate.invalid/anchor"\n'),
            ("wrap/data", tarfile.REGTYPE, b"data"),
        ]))
    for label, url in (
        ("off-list-host", release_url.replace("codeload.github.com", "evil.invalid")),
        ("http", release_url.replace("https:", "http:", 1)),
        ("userinfo", release_url.replace("https://", "https://user@", 1)),
        ("alternate-port", release_url.replace(".com/", ".com:444/", 1)),
        ("encoded-separator", release_url.replace("/tar.gz/", "%2ftar.gz/", 1)),
        ("query", release_url + "?next=elsewhere"),
        ("fragment", release_url + "#elsewhere"),
    ):
        add("TG-06/" + label, CANNOT_EVALUATE, "url", release_url=url,
            before_connect=True)
    add("TG-06/off-list-anchor", CANNOT_EVALUATE, "url",
        anchor_url="https://candidate.invalid/hashes.txt", before_connect=True)
    add("TG-07/wrong-chain", CANNOT_EVALUATE, "chain", wrong_chain=True)
    add("TG-07/wrong-hostname", CANNOT_EVALUATE, "hostname", wrong_hostname=True)
    for target in (ANCHOR_URL, "https://candidate.invalid/hashes.txt"):
        add("TG-08/redirect-" + ("same" if target == ANCHOR_URL else "cross"),
            CANNOT_EVALUATE, "headers",
            response=reply(b"anchor", b"Location: " + target.encode() + b"\r\n",
                           status=b"302 Found"), redirect=True)
    for label, address in (
        ("loopback", "127.0.0.1"),
        ("private", "10.0.0.1"),  # leak-allow: synthetic private-address refusal
        ("metadata", "169.254.169.254"),
        ("mapped", "::ffff:93.184.216.34"),
        ("platform-address", "168.63.129.16"),
    ):
        add("TG-09/" + label, CANNOT_EVALUATE, "address",
            address=address, before_connect=True)
    add("TG-09/rebound-peer", CANNOT_EVALUATE, "peer", rebound=True)
    add("TG-10/ambient-proxy-netrc-ca", mutation="environment", ambient=True)

    for label, response in (
        ("truncated-body", b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nshort"),
        ("wrong-framing", b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n"
                          b"Transfer-Encoding: chunked\r\n\r\nabc"),
        ("duplicate-length", b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n"
                             b"Content-Length: 3\r\n\r\nabc"),
        ("missing-framing", b"HTTP/1.1 200 OK\r\n\r\n3\r\nabc\r\n0\r\n\r\n"),
        ("truncated-chunk", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                            b"9\r\nabc"),
        ("extra-body", b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabcd"),
        ("content-encoding", reply(b"abc", b"Content-Encoding: gzip\r\n")),
    ):
        add("TG-11/" + label, CANNOT_EVALUATE, label, response=response,
            absent="anchors")
    add("TG-11/oversize-declared", CANNOT_EVALUATE, "anchor-cap",
        anchor_cap=64, response=reply(b"x" * 65), absent="anchors")
    add("TG-11/oversize-undeclared", CANNOT_EVALUATE, "anchor-cap",
        anchor_cap=64, response=reply(b"x" * 65, chunked=True), absent="anchors")
    # A cap mutant still uses the real transport. Absence of archive evidence
    # distinguishes streamed refusal from the later quarantine reread bound.
    add("TG-11/oversize-compressed", CANNOT_EVALUATE, "archive-cap",
        archive_cap=len(baseline_archive) + 64,
        archive=baseline_archive + b"x" * 65, absent="archive")
    add("TG-11/slow-drip", CANNOT_EVALUATE, "request-deadline",
        mode="slow", response=reply(b"x" * 200), fetch_bound=1.00)
    add("TG-11/read-inactivity", CANNOT_EVALUATE, "inactivity",
        mode="stall", fetch_bound=0.50)
    add("TG-11/stalled-resolver", CANNOT_EVALUATE, "resolver-deadline",
        stalled_resolver=True, fetch_bound=0.50)
    add("TG-11/connect-timeout", CANNOT_EVALUATE, "connect-deadline",
        connect_stall=True, fetch_bound=0.50)
    add("TG-11/tls-timeout", CANNOT_EVALUATE, "tls-deadline",
        mode="tls-stall", fetch_bound=0.50)
    add("TG-12/observer-backstop", CANNOT_EVALUATE, "backstop", exception=True)
    add("TG-12/public-wrapper-backstop", CANNOT_EVALUATE, "wrapper",
        wrapper_exception=True)
    add("TG-12/cancellation", "CANCELLED", "cancellation", cancellation=True,
        public_wrapper=True)
    add("TG-12/cancellation-after-mkdir", "CANCELLED", "creation-signals",
        cancel_after_mkdir=True, public_wrapper=True)
    add("TG-12/cancellation-after-seal", "CANCELLED", "cancelled-retention",
        cancel_after_seal=True, public_wrapper=True)
    add("TG-12/process-sigint-after-mkdir", "CANCELLED", "pending-process-sigint",
        cancel_after_mkdir=True, creation_signal="process", public_wrapper=True)
    add("TG-12/itimer-after-mkdir", "CANCELLED", "pending-real-timer",
        cancel_after_mkdir=True, creation_signal="timer", public_wrapper=True)
    add("TG-12/cancellation-during-finalization", "CANCELLED", "finalize-rollback",
        cancel_finalization="finalize", public_wrapper=True)
    add("TG-12/cancellation-at-return", "CANCELLED", "return-rollback",
        cancel_finalization="return", public_wrapper=True)
    for exception in (KeyboardInterrupt, SystemExit, GeneratorExit):
        add("TG-12/finalizer-boundary/" + exception.__name__, "CANCELLED",
            "finalizer-after-disarm", finalizer_exception=exception,
            public_wrapper=True)

    bad_archives = [
        ("traversal", [root_row, ("wrap/../escape", tarfile.REGTYPE, b"x")]),
        ("duplicate", [root_row, ("wrap/a", tarfile.REGTYPE, b"x"),
                       ("wrap/a", tarfile.REGTYPE, b"y")]),
        ("normalized-directory-duplicate",
         [root_row, ("wrap/d/", tarfile.DIRTYPE, b""),
          ("wrap/d", tarfile.DIRTYPE, b"")]),
        ("wrapper-collision", [root_row, ("other/b", tarfile.REGTYPE, b"x")]),
        ("file-directory-collision",
         [root_row, ("wrap/a", tarfile.REGTYPE, b"x"),
          ("wrap/a/b", tarfile.REGTYPE, b"y")]),
    ]
    for label, kind in (
        ("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE),
        ("fifo", tarfile.FIFOTYPE), ("character-device", tarfile.CHRTYPE),
        ("block-device", tarfile.BLKTYPE),
    ):
        bad_archives.append((label, [root_row, ("wrap/a", kind, b"")]))
    for label, rows in bad_archives:
        add("TG-13/" + label, INVALID, "archive", archive=archive(rows))
    add("TG-13/unsupported-dialect", CANNOT_EVALUATE, "archive",
        archive=archive([("wrap/pax", tarfile.XHDTYPE, b"")]))
    def pax_record(value):
        length = len(value) + 3
        while True:
            record = str(length).encode("ascii") + b" " + value + b"\n"
            if len(record) == length:
                return record
            length = len(record)

    comment = pax_record(b"comment=" + commit.encode("ascii"))
    global_row = ("pax_global_header", tarfile.XGLTYPE, comment)
    member = ("wrap/data", tarfile.REGTYPE, b"data")
    add("TG-13/git-global-name", CANNOT_EVALUATE, "git-comment",
        archive=archive([
            ("../../../../etc/cron.d/evil", tarfile.XGLTYPE, comment),
            root_row, member,
        ]))
    metadata = bytearray(gzip.decompress(archive([global_row, root_row, member])))
    for start, value in (
        (100, b"0000777\0"), (108, b"0000001\0"), (116, b"0000001\0"),
        (265, b"attacker".ljust(32, b"\0")),
        (297, b"attacker".ljust(32, b"\0")),
        (345, b"unexpected".ljust(155, b"\0")),
    ):
        metadata[start:start + len(value)] = value
    metadata[148:156] = b" " * 8
    metadata[148:156] = ("%06o\0 " % sum(metadata[:512])).encode("ascii")
    add("TG-13/git-global-metadata", CANNOT_EVALUATE, "git-comment",
        archive=gzip.compress(bytes(metadata), mtime=0))
    for label, start, value in (
        ("space-padded-owners", 108, b"0000000 " * 2),
        ("blank-owners", 108, b" " * 16),
        ("space-padded-size", 124, b"00000000064 "),
    ):
        metadata = bytearray(gzip.decompress(archive([global_row, root_row, member])))
        metadata[start:start + len(value)] = value
        metadata[148:156] = b" " * 8
        metadata[148:156] = ("%06o\0 " % sum(metadata[:512])).encode("ascii")
        add("TG-13/git-" + label, CANNOT_EVALUATE, "git-comment",
            archive=gzip.compress(bytes(metadata), mtime=0))
    for label, rows in (
        ("wrong-commit", [("pax_global_header", tarfile.XGLTYPE,
                          pax_record(b"comment=" + b"b" * 40)), root_row, member]),
        ("other-record", [("pax_global_header", tarfile.XGLTYPE,
                          pax_record(b"uid=0")), root_row, member]),
        ("extra-record", [("pax_global_header", tarfile.XGLTYPE,
                          comment + pax_record(b"uid=0")), root_row, member]),
        ("duplicate-record", [("pax_global_header", tarfile.XGLTYPE,
                              comment + comment), root_row, member]),
        ("malformed-record", [("pax_global_header", tarfile.XGLTYPE,
                              comment.replace(b"52 ", b"51 ", 1)), root_row, member]),
        ("late-global", [root_row, global_row, member]),
        ("second-global", [global_row, global_row, root_row, member]),
    ):
        add("TG-13/git-" + label, CANNOT_EVALUATE, "git-comment",
            archive=archive(rows))
    for label, kind in (("gnu-longname", tarfile.GNUTYPE_LONGNAME),
                        ("gnu-longlink", tarfile.GNUTYPE_LONGLINK),
                        ("gnu-sparse", tarfile.GNUTYPE_SPARSE)):
        add("TG-13/" + label, CANNOT_EVALUATE, "archive",
            archive=archive([("wrap/extension", kind, b"")]))
    add("A1/refusal-cleanup", CANNOT_EVALUATE, "cleanup",
        response=b"HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\nshort")
    add("A1/archive-cleanup", INVALID, "cleanup",
        archive=archive([root_row, ("wrap/../escape", tarfile.REGTYPE, b"x")]))
    add("A1/seal-cleanup", CANNOT_EVALUATE, "cleanup", seal_exception=True)
    add("A1/late-cleanup", CANNOT_EVALUATE, "cleanup", late_exception=True)
    add("A1/cleanup-failure", CANNOT_EVALUATE, "cleanup-failure",
        archive=archive([root_row, ("wrap/../escape", tarfile.REGTYPE, b"x")]),
        cleanup_failure=True)
    add("TG-13/absent-containment", CANNOT_EVALUATE, "containment",
        containment=False)

    add("TG-14/entries", INVALID, "limit", limit=("MAX_ENTRIES", 2),
        archive=archive([root_row, ("wrap/a", tarfile.REGTYPE, b"a"),
                         ("wrap/b", tarfile.REGTYPE, b"b")]))
    add("TG-14/depth", INVALID, "limit", limit=("MAX_DEPTH", 2),
        archive=archive([root_row, ("wrap/a/b/c", tarfile.REGTYPE, b"x")]))
    add("TG-14/path-bytes", INVALID, "limit", limit=("MAX_PATH_BYTES", 24),
        archive=archive([root_row, ("wrap/" + "a" * 20, tarfile.REGTYPE, b"x")]))
    add("TG-14/member-bytes", INVALID, "limit", limit=("MAX_FILE_BYTES", 64),
        archive=archive([root_row, ("wrap/a", tarfile.REGTYPE, b"x" * 65)]))
    add("TG-14/expansion", INVALID, "limit", limit=("MAX_TOTAL_BYTES", 4096),
        archive=archive([root_row, ("wrap/a", tarfile.REGTYPE, b"x" * 5000)]))

    hostile_names = archive([
        root_row,
        ("wrap/ssl.py", tarfile.REGTYPE, b"raise RuntimeError('must not import')\n"),
        ("wrap/sitecustomize.py", tarfile.REGTYPE, b"raise RuntimeError('must not import')\n"),
        ("wrap/_opf_adopt_observe.py", tarfile.REGTYPE, b"raise RuntimeError('shadow')\n"),
        ("wrap/.git/hooks/pre-commit", tarfile.REGTYPE,
         b"#!/bin/sh\nexit 97\n"),
    ])
    add("TG-15/no-process-execution", mutation="execute", archive=hostile_names,
        public_wrapper=True)
    add("TG-15/no-product-write", mutation="write", archive=hostile_names,
        public_wrapper=True)

    def run_case(base, contexts, case, mutated):
        identifier, expected, mutation, config = case
        product = base / secrets.token_hex(8)
        product.mkdir(mode=0o700)
        (product / "product-marker").write_bytes(b"unchanged\n")
        before = snapshot(product)
        case_commit = config.get("commit", commit)
        request = {"product_root": str(product), "version": "1.0.0", "commit": case_commit}
        policy = {
            "format": POLICY_FORMAT,
            "repository": "https://github.com/jposluns/guardrails",
            "release_url": config.get("release_url",
                                      release_url.rsplit("/", 1)[0] + "/" + case_commit),
            "anchors": [config.get("anchor_url", ANCHOR_URL)],
        }
        archive_body = config.get("archive", baseline_archive)
        server = Server(
            contexts[bool(config.get("wrong_hostname"))],
            archive_body, config.get("response", reply(baseline_anchor)),
            config.get("mode", "normal"),
        )
        violations = []
        connect_calls = []
        fetch_calls = []
        fetch_durations = []
        short_takes = []
        environments = []
        resolver_sockets = None
        backlog = None
        backlog_clients = []
        status = "ESCAPED"
        observation = None
        notes = None
        elapsed = 0.0
        path_before = list(sys.path)
        environment_before = None
        cancellation_boundary = []
        cancelled_observations = []
        original_created = _QuarantineOwner.created
        original_gather = _gather_release
        original_finish = _finish_observation

        @contextlib.contextmanager
        def unmasked_helper():
            # Force process-directed delivery through another thread, including
            # when a regressed implementation masks signals in the main thread.
            watched = {signal.SIGINT, signal.SIGALRM}
            previous = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
            ready = threading.Event()
            stop = threading.Event()
            errors = []

            def helper():
                old_mask = None
                try:
                    old_mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, watched)
                    ready.set()
                    stop.wait()
                except BaseException as exc:
                    errors.append(type(exc).__name__)
                    ready.set()
                finally:
                    if old_mask is not None:
                        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

            thread = threading.Thread(target=helper, daemon=True)
            try:
                thread.start()
                if not ready.wait(2.0) or errors:
                    raise RuntimeError("unmasked signal helper unavailable")
                yield
            finally:
                stop.set()
                if thread.ident is not None:
                    thread.join(timeout=2.0)
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)
                if thread.is_alive() or errors:
                    raise RuntimeError("unmasked signal helper teardown failed")

        def cancel_before_registration(owner):
            # This boundary follows mkdir AND fstat identity capture, but still
            # precedes the OWNED state transition. Pending alone is insufficient.
            if owner.identity is None:
                raise AssertionError("creation fixture has no captured identity")
            cancellation_boundary.append("after-mkdir")
            kind = config.get("creation_signal")
            if kind == "process":
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(1.0)
                raise AssertionError("process-directed SIGINT did not cancel")
            if kind == "timer":
                old_handler = signal.getsignal(signal.SIGALRM)
                old_timer = signal.getitimer(signal.ITIMER_REAL)
                started = time.monotonic()

                def interrupt(signum, frame):
                    raise KeyboardInterrupt

                try:
                    signal.signal(signal.SIGALRM, interrupt)
                    signal.setitimer(signal.ITIMER_REAL, 0.01)
                    time.sleep(1.0)
                    raise AssertionError("ITIMER_REAL did not cancel")
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, old_handler)
                    remaining, interval = old_timer
                    if remaining:
                        remaining = max(0.001, remaining - (time.monotonic() - started))
                    signal.setitimer(signal.ITIMER_REAL, remaining, interval)
            signal.pthread_kill(threading.get_ident(), signal.SIGINT)
            original_created(owner)

        def finalization_trace(frame, event, arg):
            if frame.f_code is module.gather_release.__code__ and event == "line":
                relative = frame.f_lineno - frame.f_code.co_firstlineno
                if relative == finalization_line:
                    observed = frame.f_locals["observation"]
                    if (observed.get("status") != VALID
                            or type(observed.get("record")) is not bytes):
                        raise AssertionError("finalization fixture did not reach sealed evidence")
                    cancelled_observations.append(observed)
                    cancellation_boundary.append(config["cancel_finalization"])
                    raise KeyboardInterrupt
            return finalization_trace

        def cancel_after_sealing(req, pol, observed, gathered_notes, owners):
            original_gather(req, pol, observed, gathered_notes, owners)
            if observed.get("status") != VALID or type(observed.get("record")) is not bytes:
                raise AssertionError("cancellation fixture did not reach a sealed observation")
            cancellation_boundary.append("after-seal")
            cancelled_observations.append(observed)
            raise KeyboardInterrupt

        def cancel_at_finalizer_boundary(observed, gathered_notes, owners):
            original_finish(observed, gathered_notes, owners)
            if observed.get("status") != VALID or type(observed.get("record")) is not bytes:
                raise AssertionError("finalizer fixture did not reach sealed evidence")
            cancelled_observations.append(observed)
            cancellation_boundary.append("finalizer-boundary")
            raise config["finalizer_exception"]()

        def fixture_context():
            environments.append(set(os.environ))
            return original_context(cadata=fixture_certificates)

        def lookup(host):
            if config.get("stalled_resolver"):
                resolver_sockets[0].recv(1)
            address = config.get("address", public_ip)
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            endpoint = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
            return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", endpoint)]

        def connect(sock, endpoint, timeout):
            connect_calls.append(endpoint)
            environments.append(set(os.environ))
            sock.settimeout(timeout)
            port = backlog.getsockname()[1] if backlog is not None else server.port
            sock.connect(("127.0.0.1", port))

        def tracked_fetch(url, cap, deadline, context):
            fetch_calls.append(url)
            if config.get("cancellation"):
                raise KeyboardInterrupt
            if config.get("exception"):
                raise RuntimeError("synthetic observer exception")
            started = time.monotonic()
            try:
                return original_fetch(url, cap, deadline, context)
            finally:
                fetch_durations.append(time.monotonic() - started)

        def no_containment():
            raise OSError("synthetic missing containment primitive")

        def raising_gather(*args, **kwargs):
            raise RuntimeError("synthetic public-wrapper exception")

        def swallowed_cancellation(req, pol):
            try:
                return schema.gather_release(req, pol)
            except KeyboardInterrupt:
                return {"status": CANNOT_EVALUATE}, []

        def unprotected_gather(req, pol):
            if config.get("exception"):
                raise RuntimeError("synthetic observer exception")
            return gather_release(req, pol)

        original_unpack = _unpack

        def execute_mutant(parent, body, deadline, pinned_commit):
            os.system("true")
            return original_unpack(parent, body, deadline, pinned_commit)

        def write_mutant(parent, body, deadline, pinned_commit):
            fd = os.open(str(product / "product-marker"), os.O_WRONLY | os.O_TRUNC)
            os.close(fd)
            return original_unpack(parent, body, deadline, pinned_commit)

        def candidate_mutant(parent, body, deadline, pinned_commit):
            fetch_calls.append("https://candidate.invalid/anchor")
            return original_unpack(parent, body, deadline, pinned_commit)

        def fail_cleanup(owner):
            raise OSError("synthetic cleanup failure")

        def fail_seal(*args):
            raise OSError("synthetic seal failure")

        original_work = _work

        def late_failure(*args):
            original_work(*args)
            raise OSError("synthetic failure after quarantine teardown")

        try:
            if config.get("stalled_resolver"):
                resolver_sockets = socket.socketpair()
                resolver_sockets[0].settimeout(1.0)
            if config.get("connect_stall"):
                backlog = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                backlog.bind(("127.0.0.1", 0))
                backlog.listen(1)
                saturated = False
                for _ in range(16):
                    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    client.settimeout(0.03)
                    try:
                        client.connect(backlog.getsockname())
                    except socket.timeout:
                        client.close()
                        saturated = True
                        break
                    backlog_clients.append(client)
                if not saturated:
                    raise AssertionError("local listener did not saturate")

            with contextlib.ExitStack() as stack:
                patch = lambda obj, name, value: stack.enter_context(
                    mock.patch.object(obj, name, value)
                )
                # Only the five TG-11 timing rows use short deadlines. Other
                # rows and their mutants need headroom under host load.
                timing = "fetch_bound" in config
                scale = 1.0 if timing else 10.0
                patch(module, "CONNECT_SECONDS", 0.20 * scale)
                patch(module, "INACTIVITY_SECONDS", 0.15 * scale)
                patch(module, "REQUEST_SECONDS", 0.80 * scale)
                patch(module, "GATHER_SECONDS", 1.60 * scale)
                patch(module, "_lookup", lookup)
                patch(module, "_connect", connect)
                patch(module, "_peer", lambda sock: (
                    "127.0.0.1" if config.get("rebound") else public_ip, 443,
                ))
                patch(ssl, "create_default_context",
                      original_context if config.get("wrong_chain") else
                      lambda *a, **k: fixture_context())
                patch(module, "_fetch", tracked_fetch)
                if "anchor_cap" in config:
                    patch(module, "MAX_ANCHOR_BYTES", config["anchor_cap"])
                if "archive_cap" in config:
                    patch(module, "MAX_ARCHIVE_BYTES", config["archive_cap"])
                if "limit" in config:
                    name, value = config["limit"]
                    patch(module, name, value)
                if config.get("containment") is False:
                    patch(store._journal, "require_containment", no_containment)
                if config.get("ambient"):
                    home = base / "home"
                    home.mkdir(exist_ok=True)
                    (home / ".netrc").write_text(
                        "machine codeload.github.com login planted password planted\n",
                        encoding="ascii",
                    )
                    stack.enter_context(mock.patch.dict(os.environ, {
                        "HOME": str(home),
                        "HTTPS_PROXY": "http://127.0.0.1:1",
                        "https_proxy": "http://127.0.0.1:1",
                        "ALL_PROXY": "http://127.0.0.1:1",
                        "NO_PROXY": "*",
                        "SSL_CERT_FILE": str(home / "absent-ca"),
                        "SSL_CERT_DIR": str(home / "absent-ca-dir"),
                        "REQUESTS_CA_BUNDLE": str(home / "absent-ca"),
                        "CURL_CA_BUNDLE": str(home / "absent-ca"),
                        "GIT_TRACE": str(home / "must-not-write"),
                    }))
                if config.get("cancel_after_mkdir"):
                    previous_interrupt = signal.signal(signal.SIGINT, signal.default_int_handler)
                    stack.callback(signal.signal, signal.SIGINT, previous_interrupt)
                    if config.get("creation_signal"):
                        stack.enter_context(unmasked_helper())
                    patch(_QuarantineOwner, "created", cancel_before_registration)
                if config.get("cancel_after_seal"):
                    patch(module, "_gather_release", cancel_after_sealing)
                if config.get("finalizer_exception"):
                    patch(module, "_finish_observation", cancel_at_finalizer_boundary)
                if config.get("seal_exception"):
                    patch(module, "_seal_observation", fail_seal)
                if config.get("late_exception"):
                    patch(module, "_work", late_failure)
                if config.get("cleanup_failure"):
                    patch(_QuarantineOwner, "remove", fail_cleanup)
                call = gather_release
                if config.get("public_wrapper") or config.get("wrapper_exception"):
                    call = schema.gather_release
                if config.get("wrapper_exception"):
                    patch(module, "gather_release", raising_gather)

                if mutated:
                    if mutation == "url":
                        patch(module, "_url", lambda value, expected: value)
                    elif mutation == "chain":
                        patch(module, "_client_context", fixture_context)
                    elif mutation == "hostname":
                        patch(module, "_tls", lambda ctx, sock, host, deadline:
                              original_tls(ctx, sock, "wrong.invalid", deadline))
                    elif mutation == "headers":
                        patch(module, "_header_policy", lambda status, headers: None)
                    elif mutation == "address":
                        patch(module, "_public", lambda address: True)
                    elif mutation == "peer":
                        patch(module, "_check_peer", lambda sock, selected: None)
                    elif mutation == "environment":
                        patch(module, "_environment", contextlib.nullcontext)
                    elif mutation in ("anchor-cap", "archive-cap"):
                        old = ('("archive", policy["release_url"], MAX_ARCHIVE_BYTES)'
                               if mutation == "archive-cap" else
                               '("anchor", url, MAX_ANCHOR_BYTES)')
                        patch(module, "_work", source_mutant(
                            _work, old, old[:-1] + " * 1000)",
                        ))
                    elif mutation in ("request-deadline", "connect-deadline"):
                        old, new = (
                            ("_Deadline(REQUEST_SECONDS, parent)", "parent")
                            if mutation == "request-deadline" else
                            ("_Deadline(CONNECT_SECONDS, request_deadline)",
                             "request_deadline")
                        )
                        fetch_mutant = source_mutant(original_fetch, old, new)

                        def mutated_fetch(url, cap, deadline, context):
                            fetch_calls.append(url)
                            started = time.monotonic()
                            try:
                                return fetch_mutant(url, cap, deadline, context)
                            finally:
                                fetch_durations.append(time.monotonic() - started)
                        patch(module, "_fetch", mutated_fetch)
                    elif mutation == "inactivity":
                        patch(_Wire, "_receive", source_mutant(
                            _Wire._receive, "self.deadline.left(INACTIVITY_SECONDS)",
                            "self.deadline.left()",
                        ))
                    elif mutation == "resolver-deadline":
                        patch(module, "_resolve", source_mutant(
                            _resolve, "result.get(timeout=deadline.left())", "result.get()",
                        ))
                    elif mutation == "tls-deadline":
                        patch(module, "_tls", source_mutant(
                            original_tls, "wrapped.settimeout(deadline.left())",
                            "wrapped.settimeout(None)",
                        ))
                    elif mutation in ("truncated-body", "truncated-chunk"):
                        patch(_Wire, "take", source_mutant(
                            _Wire.take, "eof and not self.buffer", "True",
                        ))
                    elif mutation in ("wrong-framing", "duplicate-length",
                                      "missing-framing", "extra-body"):
                        old = {
                            "wrong-framing": "not (length is not None and transfer is not None)",
                            "duplicate-length": "key not in headers",
                            "missing-framing": 'transfer is not None and transfer.lower() == "chunked"',
                            "extra-body": 'wire.take(1, eof=True) == b""',
                        }[mutation]
                        patch(module, "_response", source_mutant(_response, old, "True"))
                    elif mutation == "content-encoding":
                        patch(module, "_header_policy", source_mutant(
                            _header_policy,
                            'headers.get("content-encoding", "identity").lower() == "identity"',
                            "True",
                        ))
                    elif mutation == "git-comment":
                        original_require = _require

                        def skip_comment(condition, status, phase, detail):
                            if detail != "global header is not the sole leading pinned git comment":
                                original_require(condition, status, phase, detail)
                        patch(module, "_require", skip_comment)
                    elif mutation == "reject-git-comment":
                        policy_check = _archive_member_policy
                        patch(module, "_archive_member_policy",
                              lambda info, global_header=False: policy_check(info))
                    elif mutation in ("creation-signals", "pending-process-sigint",
                                      "pending-real-timer"):
                        # Keep the row/mutant names: identity is captured, but
                        # the OWNED state transition has not happened yet.
                        patch(_QuarantineOwner, "remove", source_mutant(
                            original_remove, 'self.state in ("PENDING", "OWNED")',
                            'self.state == "OWNED"',
                        ))
                    elif mutation in ("finalize-rollback", "return-rollback"):
                        patch(module, "gather_release", source_mutant(
                            original_public_gather, "owner.finish(False)", "pass",
                        ))
                    elif mutation == "finalizer-after-disarm":
                        source = textwrap.dedent(inspect.getsource(original_public_gather))
                        finalizer = "        _finish_observation(observation, notes, owners)\n"
                        if source.count(finalizer) != 1:
                            raise AssertionError("finalizer mutation must select one call")
                        moved = source.replace(finalizer, "", 1)
                        moved += "    finally:\n" + finalizer
                        patch(module, "gather_release", source_mutant(
                            original_public_gather, source, moved,
                        ))
                    elif mutation == "cancelled-retention":
                        patch(module, "gather_release", source_mutant(
                            gather_release, "observation.clear()", "pass",
                        ))
                    elif mutation == "cleanup":
                        patch(_QuarantineOwner, "remove", lambda owner: None)
                    elif mutation == "cleanup-failure":
                        patch(_QuarantineOwner, "remove", original_remove)
                    elif mutation == "backstop":
                        call = unprotected_gather
                    elif mutation == "wrapper":
                        call = raising_gather
                    elif mutation == "cancellation":
                        call = swallowed_cancellation
                    elif mutation == "archive":
                        patch(module, "_unpack", lambda parent, body, deadline, commit=None: [])
                    elif mutation == "containment":
                        patch(store._journal, "require_containment", lambda: None)
                    elif mutation == "limit":
                        name, _ = config["limit"]
                        patch(module, name, getattr(planning, name))
                    elif mutation == "execute":
                        patch(module, "_unpack", execute_mutant)
                    elif mutation == "write":
                        patch(module, "_unpack", write_mutant)
                    elif mutation == "candidate":
                        patch(module, "_unpack", candidate_mutant)
                    else:
                        raise AssertionError("unregistered mutation")

                if config.get("cancel_finalization"):
                    target = (
                        '_finish_observation(observation, notes, owners)'
                        if config["cancel_finalization"] == "finalize"
                        else "return observation, notes"
                    )
                    source_lines = inspect.getsource(original_public_gather).splitlines()
                    selected = [index for index, line in enumerate(source_lines)
                                if line.strip().startswith(target)]
                    if len(selected) != 1:
                        raise AssertionError("finalization injection must select one line")
                    finalization_line = selected[0]
                    previous_trace = sys.gettrace()
                    stack.callback(sys.settrace, previous_trace)
                    sys.settrace(finalization_trace)

                # Observe the return contract, not the diagnostic wording:
                # disabling the truncation guard must not hide behind a later
                # chunk-terminator refusal.
                original_take = _Wire.take

                def tracked_take(wire, size, eof=False):
                    result = original_take(wire, size, eof=eof)
                    if len(result) != size and not (eof and result == b""):
                        short_takes.append((size, len(result)))
                    return result
                patch(_Wire, "take", tracked_take)
                environment_before = dict(os.environ)
                started = time.monotonic()
                with deny_effects(product, violations):
                    try:
                        with watchdog(3.0 if timing else 30.0):
                            observation, notes = call(copy.deepcopy(request), copy.deepcopy(policy))
                        status = observation["status"]
                    except WatchdogExpired:
                        status = "WATCHDOG"
                    except (KeyboardInterrupt, SystemExit, GeneratorExit) as exc:
                        expected_exception = config.get("finalizer_exception", KeyboardInterrupt)
                        status = "CANCELLED" if type(exc) is expected_exception else "ESCAPED"
                    except Exception:
                        status = "ESCAPED"
                elapsed = time.monotonic() - started
                environment_restored = dict(os.environ) == environment_before

            passed = (
                status == expected
                and not violations
                and not short_takes
                and snapshot(product) == before
                and sys.path == path_before
                and environment_restored
                and not server.errors
            )
            if config.get("cancel_after_mkdir"):
                passed = passed and cancellation_boundary == ["after-mkdir"]
            if config.get("cancel_after_seal"):
                passed = passed and cancellation_boundary == ["after-seal"]
            if config.get("cancel_finalization"):
                passed = (passed and cancellation_boundary == [config["cancel_finalization"]])
            if config.get("finalizer_exception"):
                passed = passed and cancellation_boundary == ["finalizer-boundary"]
            if (config.get("cancel_after_seal") or config.get("cancel_finalization")
                    or config.get("finalizer_exception")):
                passed = (passed and len(cancelled_observations) == 1
                          and cancelled_observations[0] == {})
            if status == "CANCELLED":
                passed = passed and observation is None and not _GATHER_LOCK.locked()
            if status in (INVALID, CANNOT_EVALUATE):
                passed = passed and (
                    type(observation) is dict
                    and "quarantine" not in observation
                    and "members" not in observation
                    and bool(notes)
                    and all(row["status"] in (INVALID, CANNOT_EVALUATE) for row in notes)
                )
            if status in (INVALID, CANNOT_EVALUATE, "CANCELLED"):
                try:
                    residue = list((product / ".working" / "adopt").iterdir())
                except FileNotFoundError:
                    residue = []
                if config.get("cleanup_failure"):
                    passed = (passed and bool(residue)
                              and "record" not in observation
                              and any(row["phase"] == "cleanup" for row in notes))
                else:
                    passed = passed and not residue
            if config.get("absent"):
                passed = passed and config["absent"] not in (observation or {})
            if status == VALID:
                passed = passed and (
                    type(observation) is dict
                    and not notes
                    and type(observation.get("record")) is bytes
                    and type(observation.get("captured_monotonic_ns")) is int
                    and schema._RUN_ID_RE.fullmatch(observation["request_id"]) is not None
                    and Path(observation["quarantine"]).is_dir()
                    and stat.S_IMODE(Path(observation["quarantine"]).stat().st_mode) == 0o700
                    and len(observation.get("anchors", [])) == 1
                )
                if passed:
                    for member in observation["members"]:
                        candidate = Path(observation["quarantine"]) / "members" / member["path"]
                        passed = passed and stat.S_IMODE(candidate.stat().st_mode) == 0o600
            if config.get("before_connect"):
                passed = passed and not connect_calls
            if config.get("redirect"):
                passed = passed and fetch_calls == [release_url, ANCHOR_URL]
            if config.get("ambient"):
                passed = passed and bool(environments) and all(
                    names <= {"PATH", "HOME"} for names in environments
                )
                passed = passed and all(
                    b"authorization:" not in request.lower()
                    and b"cookie:" not in request.lower()
                    for request in server.requests
                )
            if "fetch_bound" in config:
                # Request 0.80 < slow-drip bound 1.00 < gather 1.60.
                # Connect 0.20 / inactivity 0.15 < other bounds 0.50
                # < request 0.80; _Deadline reserves at most 0.05 seconds.
                passed = (passed and elapsed < 1.60 and bool(fetch_durations)
                          and all(duration < config["fetch_bound"]
                                  for duration in fetch_durations))
            if identifier.startswith("TG-05/"):
                passed = passed and fetch_calls == [release_url, ANCHOR_URL]
            return passed, status, elapsed
        finally:
            if resolver_sockets is not None:
                resolver_sockets[1].close()
                resolver_sockets[0].close()
                if not _RESOLVER_SLOT.acquire(timeout=1.0):
                    raise AssertionError("resolver fixture retained its slot")
                _RESOLVER_SLOT.release()
            for client in backlog_clients:
                client.close()
            if backlog is not None:
                backlog.close()
            server.close()

    try:
        # Missing timer support is a setup cannot-evaluate, before any row.
        watchdog_preconditions()
        with watchdog(3.0):
            pass
        if not Path("/dev/shm").is_dir() or not Path("/proc/self/fd").is_dir():
            raise RuntimeError("local fixture filesystem primitives unavailable")
        with tempfile.TemporaryDirectory(prefix="opf-adopt-observe-", dir="/dev/shm") as temp:
            base = Path(temp)
            # Fresh disposable keys, never embedded in the source or used by
            # production. Missing openssl is a refusing harness error.
            executable = shutil.which("openssl", path=os.defpath)
            if executable is None:
                raise RuntimeError("openssl fixture builder unavailable")
            contexts = []
            fixture_certificates = ""
            for index, names in enumerate((
                "DNS:codeload.github.com,DNS:posluns.dev", "DNS:wrong.invalid",
            )):
                key = base / ("fixture-key-{}.pem".format(index))
                cert = base / ("fixture-cert-{}.pem".format(index))
                subprocess.run(
                    [os.path.abspath(executable), "req", "-x509", "-newkey",
                     "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=fixture",
                     "-addext", "subjectAltName=" + names,
                     "-keyout", str(key), "-out", str(cert)],
                    cwd=base, env={"PATH": os.defpath, "OPENSSL_CONF": os.devnull},
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=True, timeout=15,
                )
                os.chmod(key, 0o600)
                fixture_certificates += cert.read_text(encoding="ascii")
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(cert), str(key))
                contexts.append(context)

            git_commit, git_archive = git_archive_fixture(base)
            add("positive/git-archive", VALID, "reject-git-comment",
                commit=git_commit, archive=git_archive)

            # Establish an actual positive TLS/quarantine fixture before negatives.
            positive = ("positive/local-tls-quarantine", VALID, "archive", {})
            passed, status, elapsed = run_case(base, contexts, positive, False)
            executed.append({
                "id": positive[0], "expected": VALID, "observed": status,
                "test_status": VALID if passed else INVALID,
                "elapsed_seconds": elapsed,
            })
            if not passed:
                SELF_TEST_ROSTER = tuple(executed)
                print(json.dumps({"opf_adopt_observe_tests": executed}, sort_keys=True))
                return 1

            guard_rows = _guard_self_test() + _ownership_self_test()
            executed.extend(guard_rows)
            for case in cases:
                passed, status, elapsed = run_case(base, contexts, case, False)
                mutant_passed, mutant_status, mutant_elapsed = run_case(
                    base, contexts, case, True,
                )
                executed.append({
                    "id": case[0],
                    "guard": case[2],
                    "expected": case[1],
                    "observed": status,
                    "mutant_observed": mutant_status,
                    "mutant_test_status": VALID if mutant_passed else INVALID,
                    "mutation_detected": not mutant_passed,
                    "test_status": VALID if passed and not mutant_passed else INVALID,
                    "elapsed_seconds": elapsed,
                    "mutant_elapsed_seconds": mutant_elapsed,
                })
    except Exception as exc:
        executed.append({
            "id": "fixture/setup-or-teardown",
            "test_status": CANNOT_EVALUATE,
            "exception": type(exc).__name__,
        })
        SELF_TEST_ROSTER = tuple(executed)
        print(json.dumps({"opf_adopt_observe_tests": executed}, sort_keys=True))
        return 2

    SELF_TEST_ROSTER = tuple(executed)
    print(json.dumps({"opf_adopt_observe_tests": executed}, sort_keys=True))
    expected_ids = ([positive[0]] + [row["id"] for row in guard_rows]
                    + [case[0] for case in cases])
    if [row["id"] for row in executed] != expected_ids:
        return 1
    if any(row["test_status"] != VALID for row in executed):
        return 1
    if not vectors_only:
        try:
            _runner_registration_test(expected_ids)
        except AssertionError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:
            print("registration cannot evaluate:", type(exc).__name__, file=sys.stderr)
            return 2
    return 0


def main():
    if sys.argv[1:] in (["--self-test"], ["--self-test", "--vectors-only"]):
        # Let the public lazy wrapper address this exact module in a standalone
        # invocation, rather than importing a second copy of its test mutations.
        sys.modules.setdefault("_opf_adopt_observe", sys.modules[__name__])
        return self_test(vectors_only="--vectors-only" in sys.argv[1:])
    print("usage: _opf_adopt_observe.py --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
