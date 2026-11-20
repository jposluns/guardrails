#!/usr/bin/env python3
"""Pinned bash build for CI (ci-build-bash): fetch GNU bash 5.3 and its official patches 001 to 009, verify every
file against its pin, apply the patches in order, build bash, install it under the runner's temporary directory and
add that directory's bin to GITHUB_PATH. The hooks behaviour self-test parses its heredoc cases with the bash it
finds on PATH and fails (never skips) below bash 5.3, so each CI job that runs it builds this one first, whatever
bash the runner image ships: the generated surfaces job, and the three git-fixture-env config lanes, which rerun
that self-test as a roster member.

  ci_build_bash.py [--self-test]
                                offline vectors over small fixture files in a temporary directory
  ci_build_bash.py [--self-test] --execution-report ABS_PATH
                                the self-test, also writing its execution report, finalized at interpreter
                                exit (tools/_selftest_exit_report.py), as the execution gate runs it
  ci_build_bash.py --build      fetch, verify, build and install; CI only (it needs RUNNER_TEMP and GITHUB_PATH)

The build runs only on the explicit --build. Started with no argument, the script runs its offline self-test, so a
test lane that starts this suite's runner bare (the git-fixture-env lanes start every suite runner directly) never
fetches and never writes GITHUB_PATH, even where RUNNER_TEMP and GITHUB_PATH are set. Any other argument is a usage
error.

Every path under the GNU tree, byte count and sha256 is a literal in PINS, one pin per file, and every source is a
literal base URL in GNU_SOURCES: the GNU project's own server (https://ftp.gnu.org/gnu/), then two mirrors from the
GNU mirror list (https://mirrors.kernel.org/gnu/ and https://mirrors.ocf.berkeley.edu/gnu/), each serving a copy of
that tree. A file's URL from a source is the source's base URL followed by the file's pinned path, so every source is
asked for the same file by construction. The sources only supply bytes: the pin is the one acceptance test whichever
source served them, and every file is fetched and checked against its pin (its sha256, and the archive's byte count
too) before anything is extracted. Each file gets up to FETCH_ATTEMPTS (4) attempts, and each attempt tries every
source once, in the run's source order. That order starts as GNU_SOURCES and is kept for the rest of the run: a
source that fails a download moves to the back of it, so the source that served the previous file is the first one
tried for the next, and a source that keeps failing (or keeps timing out) is asked last. A failure tied to one
source moves on to the next source at once (no wait): a transient transport failure (below), any HTTP status (an
HTTPError of any code, or a final status other than 200), a refused connection, a permanent DNS failure (EAI_NONAME
or EAI_FAIL, and EAI_NODATA or EAI_ADDRFAMILY where the platform defines them), a certificate or other TLS failure,
a malformed response head (a bad status line, an over-long header line, an unknown protocol or transfer encoding,
and any other ValueError or http.client exception raised while the response is opened, such as a head of too many
header lines or a redirect Location that does not parse), a redirect that is not followed (below), a Content-Length
that is not a byte count, a body over FETCH_LIMIT and a body longer than its Content-Length. An attempt whose every
source failed so is retried only when at least one of those failures was transient, with waits of RETRY_DELAYS (2,
4 and 8 seconds) before the second, third and fourth attempt; when none was, the build stops after that attempt. A
transient transport failure is a network or host that is unreachable, a connection reset (including a server that
closes the connection before it answers), a broken pipe, an aborted connection, a timeout, a temporary DNS failure
(EAI_AGAIN) or a response read cut short (IncompleteRead), each also when urllib wraps it in URLError, and a body
that ends before the byte count its Content-Length header declares. Everything else stops the
build at once, without asking another source or retrying: a URL that is not https, a deadline that has passed, a
local file error (creating, writing, linking or removing a download file), any other network exception and a
completed download that differs from its pin. A redirect is followed only to an https URL on the host and port of
the URL it redirects from (a URL that names no port is on 443; one that names a port, 0 included, is on that port)
that carries no userinfo (no @ in its netloc, so no username, a percent-encoded or backslash form included); any
other is refused before it is opened, and the refusal names the hop that returned the redirect and the Location
it gave, the conditions of that rule the Location failed and the whole rule (SAME_ORIGIN_RULE, made from the
conditions the check enforces); a response served from a URL that fails the rule is refused naming the same. An @
outside the netloc (in the path or query) is no userinfo. A redirect's own body is never read, a redirect that
arrives once the deadline has passed is not followed
(the build stops, as at any passed deadline), and the next hop opens with FETCH_TIMEOUT cut down to the time then
left. A failure while opening a followed hop names that hop. Every download writes into a fresh temporary file in
the downloads directory, created exclusively; a failed download's file is removed, and only that one. The first
download of a file that completes, from whichever source, is linked to its pinned name, which must not exist yet,
and is checked against its pin there before the next file is fetched: a size or sha256 mismatch is a refusal at
once, never retried and never fetched again from another source. Every failure line of a download (each line
logged for a source that failed, and the line the build stops with) names the source it concerns, the sources that
already failed in that attempt, the sources not tried in it and, from the second attempt on, each source's failure
in the previous attempt; the next source is announced only after the deadline check before it has passed; once
every attempt is spent, the line names each source with its failure in the last attempt. Every fetched line and
every refusal of a completed download names the URL that served the bytes (where a followed redirect led). All ten
files, their sources, their retries and their waits share one
DOWNLOAD_DEADLINE (300 seconds) on a monotonic clock: no download from any source starts once it has passed, no wait
starts that would not end before it, and a download that finds it passed before a body read stops without trying
another source or retrying. Each download opens its connection with a socket timeout of FETCH_TIMEOUT cut down to
the time left then, and before each body read the timeout is set again to FETCH_TIMEOUT cut down to the time
left at that read. That bound is checked by this process between blocking operations, not enforced during them:
while urllib connects, sends the request and reads the response head, each socket operation it makes may wait up
to the timeout that hop opened with (the download's own, or the time left when a redirect to it was followed), so
the opening can overrun the deadline by that much per operation, in each hop (name resolution is not covered by
the socket timeout at all); a body read is one read1, one socket read for a body of declared length (a chunked body
also reads its chunk size lines, one socket read or more), and each of those may wait up to the time left when
the read began. No separate process is terminated at the deadline. The patches are applied in PINS order
with patch -p0 --force, which asks nothing and does not reverse a patch that looks reversed, so a patch that does
not apply stops the build. The built bash must report BASH_VERSINFO 5, 3 and 9 (major, minor, patch level), in
the build tree before make install and again as installed, so the patch level the applied patches left is read
from bash itself. GITHUB_PATH is written only after both checks pass.

Exit of --build: 0 installed and added to GITHUB_PATH; 1 refused (a file that is missing or differs from its pin,
or a built bash that does not print 5 3 9 or exits non-zero); 2 not completed (RUNNER_TEMP or GITHUB_PATH unset,
relative or not an existing directory and file; the install or work directory already present; a fetch error that
stops the build at once; an attempt whose every source failed with none failing transiently, or every source still
failing in the last attempt; the download deadline passed; an archive
that does not extract; a patch, configure, make or make install step that fails; a built bash that cannot
be started). On exit 1 or 2 nothing is installed: the install directory is removed if make install created it,
and GITHUB_PATH is not written. The work directory (the downloads and the build tree) is left under the runner's
temporary directory. Exit of the self-test: 0 every vector passed; 1 any failed; with --execution-report, 2 when
the report's finalizer refuses the run (tools/_selftest_exit_report.py). A usage error exits 2.

Not checked: the GNU signature of the archive and the patches (the pinned digests stand in for it, so a changed
upstream file, or a mirror's copy that differs, is a refusal, never a silent update); that a mirror is current or
answers at all (a mirror is only a source, so one that lags or is gone costs a failed download, never acceptance);
where on its own host a followed redirect leads (the pin still decides); the compiler, make and patch that the
runner image supplies,
which run as trusted tools; and which bash a later step runs. The hooks self-test looks bash up on its PATH, and
the runner adds each GITHUB_PATH line to the front of PATH for the later steps of the same job; that is GitHub's
documented behaviour, which this script does not observe. In a config lane the execution gate and the lane pass
that PATH on to the rerun hooks self-test with only the lane's git wrapper directories in front. The self-test
drives the whole pipeline over fixture files with sh, patch and make; it never fetches, and a fetch attempted while
it runs fails it. The sources, the download, the wait and the clock are injected (download and provision take
sources, fetcher, sleep and clock; build takes all but the sources, which are GNU_SOURCES), so the move to the next
source, the source order, the retry, the waits and the shared deadline are tested with fixture sources and
fetchers, a recording sleep and a fake clock, never the network and never a real wait; fetch_attempt takes an
opener, so the redirect rule is tested through urllib's own redirect handling over offline https responses.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: ci_build_bash.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import errno
import hashlib
import http.client
import io
import os
import shutil
import socket
import ssl
import stat
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = "tools/selftest_checks.toml"
# This script's own self-test suite in the registry.
SUITE_ID = "ci-build-bash-selftest"

# (file name, path under the GNU tree, byte count or None, sha256): the GNU bash 5.3 archive, then its official
# patches in the order they apply; one pin per file, whichever source serves it. Every value is a literal, so a new
# patch level is a reviewed change to this table and to WANT_VERSINFO together.
PINS = (
    ("bash-5.3.tar.gz", "bash/bash-5.3.tar.gz", 11355854,
     "0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba"),
    ("bash53-001", "bash/bash-5.3-patches/bash53-001", None,
     "1f608434364af86b9b45c8b0ea3fb3b165fb830d27697e6cdfc7ac17dee3287f"),
    ("bash53-002", "bash/bash-5.3-patches/bash53-002", None,
     "e385548a00130765ec7938a56fbdca52447ab41fabc95a25f19ade527e282001"),
    ("bash53-003", "bash/bash-5.3-patches/bash53-003", None,
     "f245d9c7dc3f5a20d84b53d249334747940936f09dc97e1dcb89fc3ab37d60ed"),
    ("bash53-004", "bash/bash-5.3-patches/bash53-004", None,
     "9591d245045529f32f0812f94180b9d9ce9023f5a765c039b852e5dfc99747d0"),
    ("bash53-005", "bash/bash-5.3-patches/bash53-005", None,
     "cca1ef52dbbf433bc98e33269b64b2c814028efe2538be1e2c9a377da90bc99d"),
    ("bash53-006", "bash/bash-5.3-patches/bash53-006", None,
     "29119addefed8eff91ae37fd51822c31780ee30d4a28376e96002706c995ff10"),
    ("bash53-007", "bash/bash-5.3-patches/bash53-007", None,
     "c0976bbfffa1453c7cfdd62058f206a318568ff2d690f5d4fa048793fa3eb299"),
    ("bash53-008", "bash/bash-5.3-patches/bash53-008", None,
     "097cd723cbfb8907674ac32214063a3fd85282657ec5b4e544d2c0f719653fb4"),
    ("bash53-009", "bash/bash-5.3-patches/bash53-009", None,
     "eee30fe78a4b0cb2fe20e010e00308899cfc613e0774ebb3c8557a1552f24f8c"),
)
# The official GNU sources, tried in this order by every attempt: the GNU project's own server, then two mirrors
# from the GNU mirror list on separate networks. Each base URL is the top of a copy of the GNU tree (the gnu/
# directory of ftp.gnu.org), so a file's URL from a source is its base URL followed by the file's PINS path. A source
# only supplies bytes; the file's pin alone decides whether they are accepted.
GNU_SOURCES = (
    "https://ftp.gnu.org/gnu/",
    "https://mirrors.kernel.org/gnu/",
    "https://mirrors.ocf.berkeley.edu/gnu/",
)
SOURCE_DIR = "bash-5.3"  # the archive's top directory
# BASH_VERSINFO[0], [1] and [2] (major, minor, patch level) as VERSION_SCRIPT prints them.
WANT_VERSINFO = "5 3 9"
VERSION_SCRIPT = 'printf "%s %s %s\\n" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}" "${BASH_VERSINFO[2]}"'
INSTALL_NAME = "bash-5.3.9"  # the install directory under RUNNER_TEMP; the work directory adds -build
FETCH_TIMEOUT = 60  # seconds for each socket operation, cut down to the time the deadline leaves at open and per read
FETCH_LIMIT = 64 << 20  # bytes per file; every pinned file is far smaller
FETCH_ATTEMPTS = 4  # attempts per file, the first included
RETRY_DELAYS = (2, 4, 8)  # seconds waited before the second, third and fourth attempt
DOWNLOAD_DEADLINE = 300  # seconds on a monotonic clock for all the files, their retries and their waits together
# errno values of an OSError that are a transient network failure: a network or host that cannot be reached.
TRANSIENT_ERRNOS = frozenset((errno.ENETUNREACH, errno.EHOSTUNREACH))
# getaddrinfo error codes of a permanent DNS failure (the name or its address does not resolve), the platform's own.
DNS_PERMANENT = frozenset(code for code in (getattr(socket, name, None) for name in (
    "EAI_NONAME", "EAI_FAIL", "EAI_NODATA", "EAI_ADDRFAMILY")) if code is not None)
# http.client exceptions of a malformed response head, as a server sends it.
MALFORMED_HEAD = (http.client.BadStatusLine, http.client.LineTooLong, http.client.UnknownProtocol,
                  http.client.UnknownTransferEncoding)
READ_CHUNK = 1 << 16
STEP_TIMEOUT = 1800  # seconds for one patch, configure, make or make install
VERSION_TIMEOUT = 30
OUTPUT_TAIL = 40  # lines of a failed step's output shown


class Refused(Exception):
    """A file that is missing or differs from its pin, or a built bash of another version: exit 1."""


class NotCompleted(Exception):
    """A fetch, extract, patch or build step that did not complete, or an unusable environment: exit 2."""


class SourceFailed(NotCompleted):
    """A failure of one download tied to the source that served it (an HTTP status, a refused connection, a
    permanent DNS failure, a TLS failure, a malformed response head, a ValueError or another http.client exception
    raised while opening, a redirect not followed, a Content-Length that is not a byte count, a body over FETCH_LIMIT
    or longer than its Content-Length): the next source is tried at once;
    exit 2 when no source completes."""


class Transient(SourceFailed):
    """A transient transport failure of one download: the next source is tried at once, and an attempt with one of
    these is retried; exit 2 once the attempts run out."""


def is_transient(exc):
    """Whether a network exception is a transient transport failure worth another attempt: a network or host that
    cannot be reached, a connection reset, a broken pipe, an aborted connection, a timeout, a temporary DNS failure
    or a response read cut short, also
    as the reason of a URLError. An HTTPError (any status), a TLS failure (a certificate one included), a permanent
    DNS failure and every other exception are not."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, BaseException) and is_transient(exc.reason)
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    if isinstance(exc, (TimeoutError, ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
                        http.client.IncompleteRead)):
        return True
    return isinstance(exc, OSError) and exc.errno in TRANSIENT_ERRNOS


def is_source_failure(exc):
    """Whether a network exception is tied to the one source it came from, so the next source is worth asking: a
    transient transport failure, an HTTPError (any status), a TLS failure (a certificate one included), a refused
    connection, a permanent DNS failure (DNS_PERMANENT) or a malformed response head (MALFORMED_HEAD), also as the
    reason of a URLError. Every other exception (a local error, an invalid URL, an unknown URL type) is not. This is
    the classification of the exception alone: fetch_attempt raises SourceFailed for every ValueError and every
    http.client exception raised while it opens a URL, so an invalid URL found there (http.client's InvalidURL for a
    nonnumeric port, say) is tied to the source and the next source is tried."""
    if is_transient(exc) or isinstance(exc, urllib.error.HTTPError):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, BaseException) and is_source_failure(exc.reason)
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, socket.gaierror):
        return exc.errno in DNS_PERMANENT
    return isinstance(exc, (ConnectionRefusedError,) + MALFORMED_HEAD)


def _where(url, final):
    """url, and the URL a followed redirect led to when that is another."""
    return url if final == url else "{} (redirected to {})".format(url, final)


def fetch_error(url, exc, final=None):
    """The exception that stands for a network exception exc of a download attempt of url (final: the URL a followed
    redirect led to): Transient when it is a transient transport failure, SourceFailed when it is another failure
    tied to the source (is_source_failure), NotCompleted otherwise. An HTTPError names the URL that answered with
    its status when that is another than url."""
    if isinstance(exc, urllib.error.HTTPError):
        answered = exc.filename if isinstance(exc.filename, str) else final
        return SourceFailed("{}: HTTP status {}".format(_where(url, answered or url), exc.code))
    if is_transient(exc):
        kind = Transient
    elif is_source_failure(exc):
        kind = SourceFailed
    else:
        kind = NotCompleted
    return kind("{}: fetch failed ({}: {})".format(_where(url, final or url), type(exc).__name__, exc))


def _https_port(parts):
    """The port of a parsed https URL: the one it names, explicit 0 included, or 443 when it names none."""
    return 443 if parts.port is None else parts.port


# The conditions under which a URL is on the origin of the https URL it is checked against, each (wording, test),
# test taking the urlsplit parts of that URL and of the other. Every refusal of a redirect or of a served URL is
# worded from this table alone (SAME_ORIGIN_RULE and the wording of each condition failed), so a condition added
# here is named in them.
_SAME_ORIGIN_CONDITIONS = (
    ("https", lambda old, new: old.scheme == "https" and new.scheme == "https"),
    ("on the same host", lambda old, new: new.hostname is not None and new.hostname == old.hostname),
    ("on the same port", lambda old, new: _https_port(new) == _https_port(old)),
    ("with no userinfo", lambda old, new: "@" not in new.netloc),
)
SAME_ORIGIN_RULE = ", ".join(wording for wording, _test in _SAME_ORIGIN_CONDITIONS)
# what a URL that does not parse (urlsplit or urljoin raising ValueError) fails
_UNPARSED = "parsing"


def _same_origin_failures(url, other):
    """The wording of each condition of _SAME_ORIGIN_CONDITIONS that other fails against url, itself an https URL, in
    table order; () when it fails none, (_UNPARSED,) when either does not parse. A condition whose test raises
    ValueError (a port that is not a number, say) is failed. A URL that names no port is on port 443; one that names
    a port, 0 included, is on that port. A URL that carries userinfo (an @ in its netloc, which any username needs, a
    percent-encoded or backslash form before it included) fails: urllib connects to its whole netloc, not to the
    host urlsplit reports. A netloc whose @ is itself percent-encoded (%40) has no userinfo to urlsplit, and its
    host then differs from the host of url. An @ in the path or query is not in the netloc."""
    try:
        old, new = urllib.parse.urlsplit(url), urllib.parse.urlsplit(other)
    except ValueError:
        return (_UNPARSED,)
    failed = []
    for wording, test in _SAME_ORIGIN_CONDITIONS:
        try:
            held = test(old, new)
        except ValueError:
            held = False
        if not held:
            failed.append(wording)
    return tuple(failed)


def _same_host_https(url, other):
    """Whether other is on the origin of url, an https URL: it fails none of _SAME_ORIGIN_CONDITIONS."""
    return not _same_origin_failures(url, other)


def _origin_refusal(failed):
    """The reason a refusal gives for a URL that failed the conditions failed: those, then the whole rule."""
    return "failed: {}; only {} is allowed".format(", ".join(failed), SAME_ORIGIN_RULE)


class _RedirectRefused(Exception):
    """A redirect, returned by the hop source (the URL whose response carried it), that is not followed: one to a URL
    that fails the origin rule against source (failed: the wording of each condition it failed, as
    _same_origin_failures gives it), or one whose Location does not parse, is never opened; with deadline set, the
    download deadline had passed when it arrived."""

    def __init__(self, code, target, source, deadline=False, failed=()):
        super().__init__(code, target, source)
        self.code, self.target, self.source, self.deadline, self.failed = code, target, source, deadline, failed


class _SameHostHttpsRedirect(urllib.request.HTTPRedirectHandler):
    """urllib's own redirect handling, following a redirect only to an https URL on the host and port of the URL it
    redirects from. Before urllib parses the Location, it is resolved against that URL and checked here: one that
    does not parse, or leads elsewhere, closes the redirect response unread and raises _RedirectRefused, so it is
    never opened; urllib's own resolution is checked again in redirect_request. A request that carries the
    download's remaining() (fetch_attempt sets it) is not followed once the deadline has passed, and the next hop
    gets FETCH_TIMEOUT cut down to the time then left. A followed redirect's body is never read: its response is
    closed before urllib reads it. Each followed URL is appended to the hops list the request carries."""

    def http_error_302(self, req, fp, code, msg, headers):
        target = headers.get("location", headers.get("uri"))
        if target is None:
            return super().http_error_302(req, fp, code, msg, headers)
        try:
            failed = _same_origin_failures(req.full_url, urllib.parse.urljoin(req.full_url, target))
        except ValueError:
            failed = (_UNPARSED,)
        if failed:
            fp.close()
            raise _RedirectRefused(code, target, req.full_url, failed=failed)
        remaining = getattr(req, "remaining", None)
        if remaining is not None:
            left = remaining()
            if left <= 0:
                fp.close()
                raise _RedirectRefused(code, target, req.full_url, deadline=True)
            req.timeout = min(FETCH_TIMEOUT, left)
        fp.close()
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        failed = _same_origin_failures(req.full_url, newurl)
        if failed:
            fp.close()
            raise _RedirectRefused(code, newurl, req.full_url, failed=failed)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        hops = getattr(req, "hops", None)
        if new is not None and hops is not None:
            new.hops, new.remaining = hops, getattr(req, "remaining", None)
            hops.append(newurl)
        return new


def _opener(*handlers):
    """A urllib opener with its default handlers, the redirect one replaced by _SameHostHttpsRedirect; handlers are
    added to it (the self-test adds offline ones)."""
    return urllib.request.build_opener(_SameHostHttpsRedirect(), *handlers)


def _declared_length(url, response):
    """The body length that the Content-Length header of response declares, or None when it sends none or a chunked
    body (whose own framing http.client checks, raising IncompleteRead when it is cut short). A Content-Length that
    is not one decimal byte count raises SourceFailed."""
    encoding = response.getheader("Transfer-Encoding")
    if encoding is not None and encoding.lower() == "chunked":
        return None
    value = response.getheader("Content-Length")
    if value is None:
        return None
    value = value.strip()
    if not (value.isascii() and value.isdigit()):
        raise SourceFailed("{}: Content-Length {!r} is not a byte count".format(url, value))
    return int(value)


def _set_read_timeout(url, response, seconds):
    """Give the next body read of response, an http.client.HTTPResponse as urlopen returns it, a socket timeout of
    seconds. A response whose body is all read (http.client sets its file to None then) reads no socket again and
    is left as it is; one with no file attribute at all, or whose socket cannot be reached, raises NotCompleted, so
    no read runs on a timeout this attempt did not set."""
    if not hasattr(response, "fp"):
        raise NotCompleted("{}: the response has no file to bound its read timeout".format(url))
    stream = response.fp
    if stream is None:
        return
    sock = getattr(getattr(stream, "raw", None), "_sock", None)
    if not isinstance(sock, socket.socket):
        raise NotCompleted("{}: the response's socket cannot be reached to bound its read timeout".format(url))
    sock.settimeout(seconds)


def fetch_attempt(url, out, remaining, opener=None):
    """One download attempt: write the body of url into out, an open binary file, and return the URL that served it
    (url, or the https URL on its host that a followed redirect led to). remaining() gives the seconds the shared
    download deadline leaves. opener (_opener() when None) gets FETCH_TIMEOUT or that, whichever is less, for each
    socket operation it makes, and follows a redirect only to https on the same host and port; before each body read
    the socket timeout is set again to FETCH_TIMEOUT or the time then left, whichever is less, and each read is one
    read1, so for a body of declared length it makes at most one socket read. The request carries remaining() and
    the list of the URLs it opened (url, then each followed redirect hop), so a redirect arriving after the deadline
    is not followed, the next hop's timeout is cut to the time then left, and a failure names the hop it came from.
    A URL that is not https and a deadline that has passed (before the attempt, at a redirect or before a read) raise
    NotCompleted, as does a response whose socket cannot be reached (_set_read_timeout); a redirect not followed
    (naming the hop that returned it and the Location it gave), a ValueError or another http.client exception raised
    while opening (a Location urllib cannot parse, a response head http.client refuses, such as one of too many
    header lines, or an invalid URL found there, such as a nonnumeric port, which is_source_failure alone would not
    tie to the source), a response served from a URL that is not https on the host and port of url or that carries
    userinfo, a status other than
    200, a Content-Length that is not a byte count, a body over FETCH_LIMIT bytes and one longer than its
    Content-Length raise SourceFailed; a body that ends before its Content-Length raises Transient, as a read cut
    short; any other network error raises what fetch_error gives for it. A local write error propagates as the
    OSError it is."""
    if not url.startswith("https://"):
        raise NotCompleted("{}: not an https URL".format(url))
    timeout = min(FETCH_TIMEOUT, remaining())
    if timeout <= 0:
        raise NotCompleted("{}: the {} s download deadline has passed".format(url, DOWNLOAD_DEADLINE))
    request = urllib.request.Request(url)
    request.hops, request.remaining = [url], remaining
    try:
        response = (_opener() if opener is None else opener).open(request, timeout=timeout)
    except _RedirectRefused as exc:
        where = _where(url, exc.source)
        if exc.deadline:
            raise NotCompleted("{}: the {} s download deadline passed before its redirect ({}) to {} was "
                               "followed".format(where, DOWNLOAD_DEADLINE, exc.code, exc.target)) from None
        raise SourceFailed("{}: redirect ({}) to {} refused ({})".format(
            where, exc.code, exc.target, _origin_refusal(exc.failed))) from None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        failure = fetch_error(url, exc, request.hops[-1])
        if type(failure) is NotCompleted and not isinstance(exc, OSError):
            # a ValueError or an http.client exception raised while opening (a response head urllib or
            # http.client cannot take, or an invalid URL found there, such as a nonnumeric port) is tied to the
            # source, whatever is_source_failure says of it elsewhere
            failure = SourceFailed(str(failure))
        raise failure from None
    with response:
        final = getattr(response, "url", None)
        if not isinstance(final, str):
            final = url
        where = _where(url, final)
        failed = _same_origin_failures(url, final)
        if failed:
            raise SourceFailed("{}: served from a URL that is refused ({})".format(where, _origin_refusal(failed)))
        if response.status != 200:
            raise SourceFailed("{}: HTTP status {}".format(where, response.status))
        declared = _declared_length(where, response)
        total = 0
        while True:
            left = remaining()
            if left <= 0:
                raise NotCompleted("{}: the {} s download deadline passed during the download".format(
                    where, DOWNLOAD_DEADLINE))
            _set_read_timeout(where, response, min(FETCH_TIMEOUT, left))
            try:
                chunk = response.read1(READ_CHUNK)
            except (OSError, http.client.HTTPException, ValueError) as exc:
                raise fetch_error(url, exc, final) from None
            if not chunk:
                break
            total += len(chunk)
            if total > FETCH_LIMIT:
                raise SourceFailed("{}: more than {} bytes".format(where, FETCH_LIMIT))
            out.write(chunk)
    if declared is not None and total != declared:
        kind = Transient if total < declared else SourceFailed
        raise kind("{}: fetch failed (the body has {} bytes; its Content-Length declares {})".format(
            where, total, declared))
    return final


def _sources_note(failed, untried, previous=None):
    """The part of a failure line that names the other sources of the attempt: failed holds the failure messages of
    the sources that already failed in it (each names its URL), untried the URLs not tried in it, and previous, from
    the second attempt on, the failure messages of the previous attempt joined."""
    parts = []
    if previous:
        parts.append("previous attempt: " + previous)
    if failed:
        parts.append("earlier sources in this attempt: " + "; ".join(failed))
    if untried:
        parts.append("not tried: " + ", ".join(untried))
    return " ({})".format("; ".join(parts)) if parts else ""


def _remove_part(part):
    """Remove a download's own temporary file: None, or what kept it (an OSError) as text."""
    try:
        os.unlink(part)
    except OSError as exc:
        return "its download file {} was not removed ({}: {})".format(part, type(exc).__name__, exc)
    return None


def _publish(part, dest, size, digest):
    """Link the completed download part to its pinned name dest, which must not exist yet, remove part, and check
    dest against its pin (verify). A link or removal that fails raises NotCompleted, naming dest or part."""
    problems = []
    try:
        os.link(part, dest)
    except FileExistsError:
        problems.append("{} already exists; it was left as it was".format(dest))
    except OSError as exc:
        problems.append("{} could not be linked to the download ({}: {})".format(dest, type(exc).__name__, exc))
    kept = _remove_part(part)
    if kept is not None:
        problems.append(kept)
    if problems:
        raise NotCompleted("; ".join(problems))
    verify(dest, size, digest)


def _fetch_from_sources(name, path, order, dest, size, digest, downloads, fetcher, sleep, remaining, log):
    """Up to FETCH_ATTEMPTS attempts of the file name at path under the GNU tree. Each attempt tries every source
    once, in the order the list order holds when the attempt starts, with fetcher(url, out, remaining), each
    download into a fresh temporary file created exclusively in downloads; a source whose download fails is moved to
    the back of order, which the caller keeps for the next file. The first download that completes is published to
    dest and checked against its pin (_publish), and its source and the URL that served it (what fetcher returns,
    or the URL asked for when it returns None) are returned; a Refused from that check is raised at once, so a file
    that differs from its pin is never fetched again, from that source or any other. SourceFailed (Transient among
    it) moves on to the next source at once, and an attempt whose every source failed so is retried, after the
    RETRY_DELAYS wait, only when one of them was Transient. No download starts once the deadline has passed (the
    next source is announced only after that check) and no wait starts that would not end before it; those, an
    attempt not retried, the last attempt failing, any other NotCompleted and any OSError of the downloads
    directory raise NotCompleted. Every such failure message, and every line logged for a failed source, names the
    source it concerns, the sources that failed earlier in the attempt and those not tried, and the previous
    attempt's failures; another exception (a programming error) propagates as it is, after the download's file is
    removed."""
    previous = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        tried = tuple(order)
        urls = tuple(base + path for base in tried)
        failed = []
        transient = False
        pending = None
        for index, (base, url) in enumerate(zip(tried, urls)):
            untried = urls[index + 1:]
            if remaining() <= 0:
                raise NotCompleted("{}: the {} s download deadline passed before attempt {} from {}{}".format(
                    name, DOWNLOAD_DEADLINE, attempt, url, _sources_note(failed, untried, previous)))
            if pending is not None:
                log("{}; trying {}".format(pending, url))
            try:
                handle, part = tempfile.mkstemp(prefix=name + ".attempt-", dir=downloads)
            except OSError as exc:
                raise NotCompleted("{}: no download file could be created in {} for attempt {} from {} ({}: {}){}"
                                   .format(name, downloads, attempt, url, type(exc).__name__, exc,
                                           _sources_note(failed, untried, previous))) from None
            try:
                with os.fdopen(handle, "wb") as out:
                    final = fetcher(url, out, remaining) or url
            except SourceFailed as exc:
                kept = _remove_part(part)
                if kept is not None:
                    raise NotCompleted("{}; {}{}".format(exc, kept, _sources_note(failed, untried, previous))) from None
                pending = "attempt {} of {} from {} failed ({}){}".format(
                    attempt, FETCH_ATTEMPTS, url, exc, _sources_note(failed, untried, previous))
                failed.append(str(exc))
                transient = transient or isinstance(exc, Transient)
                order.remove(base)
                order.append(base)
                continue
            except NotCompleted as exc:
                kept = _remove_part(part)
                raise NotCompleted("{}{}{}".format(exc, "; " + kept if kept else "",
                                                   _sources_note(failed, untried, previous))) from None
            except OSError as exc:
                kept = _remove_part(part)
                raise NotCompleted("{}: local error in attempt {} from {} ({}: {}){}{}".format(
                    name, attempt, url, type(exc).__name__, exc, "; " + kept if kept else "",
                    _sources_note(failed, untried, previous))) from None
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(part)
                raise
            try:
                _publish(part, dest, size, digest)
            except Refused as exc:
                raise Refused("{} (fetched from {}; refused at once, not fetched again from any source){}".format(
                    exc, final, _sources_note(failed, untried, previous))) from None
            except NotCompleted as exc:
                raise NotCompleted("{}: {} (fetched from {}; not published){}".format(
                    name, exc, final, _sources_note(failed, untried, previous))) from None
            return base, final
        every = "; ".join(failed)
        if not transient:
            raise NotCompleted("{}: every source failed in attempt {} of {} and none of the failures is transient, "
                               "so it is not retried ({}){}".format(name, attempt, FETCH_ATTEMPTS, every,
                                                                    _sources_note((), (), previous)))
        if attempt == FETCH_ATTEMPTS:
            raise NotCompleted("{}: every source failed in each of {} attempts (attempt {}: {})".format(
                name, FETCH_ATTEMPTS, attempt, every))
        delay = RETRY_DELAYS[attempt - 1]
        if delay >= remaining():
            raise NotCompleted("{}: attempt {} failed from every source ({}); not retried: a {} s wait would not end "
                               "before the {} s download deadline{}".format(name, attempt, every, delay,
                                                                            DOWNLOAD_DEADLINE,
                                                                            _sources_note((), (), previous)))
        log("attempt {} of {} failed from every source ({}); retrying in {} s".format(
            attempt, FETCH_ATTEMPTS, every, delay))
        sleep(delay)
        previous = every
    raise AssertionError("unreachable: every attempt returns or raises")


def download(pins, sources, downloads, fetcher, sleep, clock, log=print):
    """Fetch every file of a PINS-shaped table into the directory downloads, in order, from sources (a
    GNU_SOURCES-shaped tuple of base URLs), with retries, all under one DOWNLOAD_DEADLINE on clock (a monotonic
    clock in seconds). The source order starts as sources and is kept across the files: a source whose download
    fails moves to the back, so the source that served a file is the first one tried for the next. Each completed
    file is linked to its pinned name, which must not exist yet, and checked against its pin there before the next
    file is fetched; its fetched line names the URL that served it. Returns, for each file in order, the source that
    served it. Raises Refused for a file that differs from its pin and NotCompleted when a fetch does not
    complete."""
    deadline = clock() + DOWNLOAD_DEADLINE

    def remaining():
        return deadline - clock()

    order = list(sources)
    served = []
    for name, path, size, digest in pins:
        base, final = _fetch_from_sources(name, path, order, Path(downloads) / name, size, digest, downloads, fetcher,
                                          sleep, remaining, log)
        served.append(base)
        log("fetched {} from {}".format(name, final))
    return tuple(served)


def served_summary(sources, served):
    """How many files each source served, in source order, for the sources that served any."""
    return ", ".join("{} from {}".format(served.count(base), base) for base in sources if base in served)


def verify(path, size, digest):
    """Refuse a missing, non-regular or unreadable file, one of another byte count (when size is pinned) and one
    of another sha256."""
    path = Path(path)
    hasher = hashlib.sha256()
    total = 0
    try:
        with open(path, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise Refused("{}: not a regular file".format(path.name))
            while True:
                chunk = handle.read(READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)
    except FileNotFoundError:
        raise Refused("{}: missing".format(path.name)) from None
    except OSError as exc:
        raise Refused("{}: unreadable ({})".format(path.name, type(exc).__name__)) from None
    if size is not None and total != size:
        raise Refused("{}: {} bytes, pinned {}".format(path.name, total, size))
    got = hasher.hexdigest()
    if got != digest:
        raise Refused("{}: sha256 {}, pinned {}".format(path.name, got, digest))


@contextlib.contextmanager
def _step(label):
    """A launch that cannot start or that times out stops the run."""
    try:
        yield
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotCompleted("{}: could not run ({})".format(label, exc)) from None


def _require_success(label, proc):
    if proc.returncode != 0:
        tail = proc.stdout.decode("utf-8", "replace").splitlines()[-OUTPUT_TAIL:]
        raise NotCompleted("{}: exit {}\n{}".format(label, proc.returncode, "\n".join("    " + line for line in tail)))


def check_version(bindir, label):
    """Refuse unless bindir/bash, started with no startup file, prints WANT_VERSINFO and exits 0."""
    bash = Path(bindir) / "bash"
    with _step(label):
        proc = subprocess.run([str(bash), "--norc", "--noprofile", "-c", VERSION_SCRIPT], stdin=subprocess.DEVNULL,
                              capture_output=True, env={"PATH": os.defpath, "LC_ALL": "C"}, timeout=VERSION_TIMEOUT)
    if proc.returncode != 0 or proc.stdout != (WANT_VERSINFO + "\n").encode("ascii"):
        raise Refused("{}: BASH_VERSINFO printed {!r} with exit {} (stderr {!r}); want {!r}".format(
            label, proc.stdout.decode("utf-8", "replace"), proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip(), WANT_VERSINFO))


def provision(pins, sources, fetcher, runner_temp, github_path, log=print, sleep=time.sleep, clock=time.monotonic):
    """Fetch, verify, extract, patch, build, check, install, check again, then append the install's bin directory
    to github_path. pins is a PINS-shaped table (the archive first, then the patches in order); sources is a
    GNU_SOURCES-shaped tuple of base URLs; fetcher(url, out, remaining) is one download as fetch_attempt is; sleep
    and clock serve the retries and the download deadline. Returns the bin directory and, for each file, the source
    that served it; raises Refused or NotCompleted, with nothing installed."""
    runner_temp = Path(runner_temp)
    prefix = runner_temp / INSTALL_NAME
    work = runner_temp / (INSTALL_NAME + "-build")
    for path in (prefix, work):
        if os.path.lexists(path):
            raise NotCompleted("{} already exists; nothing was changed".format(path))
    downloads = work / "downloads"
    os.makedirs(downloads)
    served = download(pins, sources, downloads, fetcher, sleep, clock, log)
    log("verified {} files against their pinned sha256 (fetched {})".format(len(pins), served_summary(sources,
                                                                                                    served)))
    try:
        with tarfile.open(downloads / pins[0][0], "r:gz") as archive:
            archive.extractall(work, filter="data")
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise NotCompleted("{}: does not extract ({})".format(pins[0][0], exc)) from None
    src = work / SOURCE_DIR
    if not src.is_dir():
        raise NotCompleted("{}: holds no {} directory".format(pins[0][0], SOURCE_DIR))
    for name, _path, _size, _digest in pins[1:]:
        with _step("patch " + name):
            proc = subprocess.run(["patch", "-p0", "--force", "--no-backup-if-mismatch", "--input",
                                   str(downloads / name)], cwd=str(src), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
        _require_success("patch " + name, proc)
        log("applied {}".format(name))
    with _step("configure"):
        proc = subprocess.run([str(src / "configure"), "--prefix=" + str(prefix)], cwd=str(src),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=STEP_TIMEOUT)
    _require_success("configure", proc)
    with _step("make"):
        proc = subprocess.run(["make"], cwd=str(src), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
    _require_success("make", proc)
    check_version(src, "the built bash")
    log("built bash reports BASH_VERSINFO {}".format(WANT_VERSINFO))
    try:
        with _step("make install"):
            proc = subprocess.run(["make", "install"], cwd=str(src), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
        _require_success("make install", proc)
        bindir = prefix / "bin"
        check_version(bindir, "the installed bash")
        with open(github_path, "a", encoding="utf-8") as handle:
            handle.write(str(bindir) + "\n")
    except BaseException:
        shutil.rmtree(prefix, ignore_errors=True)
        raise
    return bindir, served


def build(environ, fetcher=fetch_attempt, log=print, sleep=time.sleep, clock=time.monotonic):
    """The CI entry: provision PINS from GNU_SOURCES under environ's RUNNER_TEMP and append to its GITHUB_PATH.
    Returns the exit."""
    runner_temp = environ.get("RUNNER_TEMP", "")
    github_path = environ.get("GITHUB_PATH", "")
    if not (os.path.isabs(runner_temp) and os.path.isdir(runner_temp)):
        print("error: RUNNER_TEMP must name an existing absolute directory (got {!r}); this build runs in CI only. "
              "Nothing was installed.".format(runner_temp), file=sys.stderr)
        return 2
    if not (os.path.isabs(github_path) and os.path.isfile(github_path)):
        print("error: GITHUB_PATH must name an existing absolute file (got {!r}); this build runs in CI only. "
              "Nothing was installed.".format(github_path), file=sys.stderr)
        return 2
    try:
        bindir, served = provision(PINS, GNU_SOURCES, fetcher, runner_temp, github_path, log, sleep, clock)
    except Refused as exc:
        print("REFUSED: {}. Nothing was installed.".format(exc), file=sys.stderr)
        return 1
    except (NotCompleted, OSError) as exc:
        print("NOT COMPLETED: {}. Nothing was installed.".format(exc), file=sys.stderr)
        return 2
    print("PASS: bash {} installed from {} files that each passed its pin (fetched {}); {} is added to GITHUB_PATH "
          "for the later steps of this job".format(WANT_VERSINFO.replace(" ", "."), len(PINS),
                                                   served_summary(GNU_SOURCES, served), bindir))
    return 0


# ---------------------------------------------------------------------------------------------- self-test

FIXTURE_CONFIGURE = """#!/bin/sh
case "$1" in
--prefix=/*) ;;
*) echo "configure: an absolute --prefix is required" ; exit 1 ;;
esac
printf 'prefix = %s\\n' "${1#--prefix=}" > config.mk
"""
FIXTURE_MAKEFILE = """include config.mk
bash: bash.in patchlevel
\tsed "s/@LEVEL@/$$(cat patchlevel)/" bash.in > bash
\tchmod 755 bash
install: bash
\tmkdir -p $(prefix)/bin
\tsed 's/^echo "5 [0-9]* /echo "5 @INSTALLED_MINOR@ /' bash > $(prefix)/bin/bash
\tchmod 755 $(prefix)/bin/bash
"""
# The fixture bash prints its version as VERSION_SCRIPT would, with the level the patches leave in patchlevel.
FIXTURE_BASH_IN = '#!/bin/sh\necho "5 @MINOR@ @LEVEL@"\n'
FIXTURE_PATCH = "--- patchlevel\n+++ patchlevel\n@@ -1 +1 @@\n-{}\n+{}\n"


def _fixture_archive(path, minor="3", installed_minor=None):
    """A gzip tar archive holding a bash-5.3 tree that builds a fixture bash printing "5 <minor> <level>"; its
    make install writes one printing "5 <installed_minor> <level>" (installed_minor defaults to minor)."""
    makefile = FIXTURE_MAKEFILE.replace("@INSTALLED_MINOR@", installed_minor or minor)
    members = (("configure", FIXTURE_CONFIGURE, 0o755), ("Makefile", makefile, 0o644),
               ("bash.in", FIXTURE_BASH_IN.replace("@MINOR@", minor), 0o644), ("patchlevel", "0\n", 0o644))
    with tarfile.open(path, "w:gz") as archive:
        for name, text, mode in members:
            data = text.encode("ascii")
            info = tarfile.TarInfo(SOURCE_DIR + "/" + name)
            info.size, info.mode = len(data), mode
            archive.addfile(info, io.BytesIO(data))


def _fixture_pins(directory, archive="bash-5.3.tar.gz", order=range(1, 10), skew=None):
    """A PINS-shaped table over the fixture files in directory: the archive, then the patches in order, each at its
    own name as its path (so the source "fixture://" serves it at fixture://NAME). skew names one file whose pinned
    sha256 is replaced by the digest of other bytes."""
    rows = []
    for name in [archive] + ["bash53-{:03d}".format(level) for level in order]:
        data = (Path(directory) / name).read_bytes()
        digest = hashlib.sha256(b"other bytes" if name == skew else data).hexdigest()
        rows.append((name, name, len(data) if name == archive else None, digest))
    return tuple(rows)


def _fixture_fetcher(directory, missing=()):
    """A download attempt that copies a fixture://NAME file from directory into out; a name in missing fails as a
    download error that is not retried would."""
    def fetcher(url, out, remaining):
        name = url[len("fixture://"):]
        if not url.startswith("fixture://") or name in missing:
            raise NotCompleted("{}: fetch failed (fixture)".format(url))
        out.write((Path(directory) / name).read_bytes())
    return fetcher


class _FakeClock:
    """A monotonic clock that moves only when told to: now() reads it, advance(seconds) moves it, and sleep(seconds)
    records the wait and moves it by that much."""

    def __init__(self, start=1000.0):
        self.now_value = start
        self.sleeps = []

    def now(self):
        return self.now_value

    def advance(self, seconds):
        self.now_value += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now_value += seconds


def _scripted_fetcher(script, watch, clock=None, cost=0):
    """A download attempt that follows script, a map from URL to a list of steps used one per attempt (the last one
    repeats): bytes are written whole; an exception is raised after b"partial" is written, so a failed attempt
    leaves partial bytes behind it. Each call moves clock by cost seconds first and is recorded in the returned
    list as (url, the attempt file's size when the attempt began, the sorted names in the directory watch). cost is
    a number of seconds, a tuple of them used one per call (the last one repeats), or a function of the URL giving
    them."""
    calls = []

    def fetcher(url, out, remaining):
        if clock is not None:
            if callable(cost):
                clock.advance(cost(url))
            else:
                clock.advance(cost[min(len(calls), len(cost) - 1)] if isinstance(cost, tuple) else cost)
        calls.append((url, out.tell(), sorted(os.listdir(watch))))
        steps = script[url]
        step = steps[min(sum(1 for call in calls if call[0] == url), len(steps)) - 1]
        if isinstance(step, BaseException):
            out.write(b"partial")
            out.flush()
            raise step
        out.write(step)
    return fetcher, calls


def _fake_bash(directory, text):
    """A fixture bash in a directory of its own; returns that directory."""
    os.makedirs(directory)
    path = Path(directory) / "bash"
    path.write_text("#!/bin/sh\n" + text, encoding="ascii")
    path.chmod(0o755)
    return directory


def _outcome(call):
    """Run call with its stdout and stderr kept: ("ok", value), ("refused", message), ("not-completed",
    message) or ("crashed", message)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return "ok", call()
        except Refused as exc:
            return "refused", str(exc)
        except NotCompleted as exc:
            return "not-completed", str(exc)
        except Exception as exc:
            return "crashed", "{}: {}".format(type(exc).__name__, exc)


def _entry_outcome(args):
    """main(args) with its stdout kept: (exit, stderr text), or ("crashed", message) when it raises."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            return main(args), err.getvalue()
        except Exception as exc:
            return "crashed", "{}: {}".format(type(exc).__name__, exc)


def _registered_check_ids():
    """This suite's registered check ids, read through the execution gate's own schema validator: (set of ids,
    None), or (None, reason)."""
    import check_selftest_execution
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


def self_test(report_path=None):
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    # The pin table: the archive, then patches 001 to 009 in order, each at its path under the GNU tree with one
    # sha256 digest; and the sources: ftp.gnu.org first, then the two mirrors, each an https base URL of the GNU tree
    # on a host of its own.
    names = ["bash-5.3.tar.gz"] + ["bash53-{:03d}".format(level) for level in range(1, 10)]
    paths = ["bash/bash-5.3.tar.gz"] + ["bash/bash-5.3-patches/" + name for name in names[1:]]
    shape = [(len(row) == 4 and isinstance(row[3], str) and len(row[3]) == 64
              and set(row[3]) <= set("0123456789abcdef")) for row in PINS]
    hosts = [base.split("/")[2] for base in GNU_SOURCES]
    check("pins/table-shape", [row[0] for row in PINS] == names and [row[1] for row in PINS] == paths
          and [row[2] for row in PINS] == [11355854] + [None] * 9 and all(shape)
          and len(set(row[3] for row in PINS)) == len(PINS)
          and GNU_SOURCES == ("https://ftp.gnu.org/gnu/", "https://mirrors.kernel.org/gnu/",
                              "https://mirrors.ocf.berkeley.edu/gnu/")
          and all(base.startswith("https://") and base.endswith("/gnu/") for base in GNU_SOURCES)
          and len(set(hosts)) == len(hosts), (PINS, GNU_SOURCES))
    fixture_sources = ("fixture://",)

    # Any fetch while the self-test runs is a failure: it never touches the network.
    reached = []

    def no_network(*args, **kwargs):
        reached.append(args[:1])
        raise OSError("the self-test never fetches")

    def no_network_open(handler, req):
        reached.append((req.full_url,))
        raise OSError("the self-test never fetches")

    # urlopen, and urllib's own https and http handlers (which every opener fetch_attempt builds has, unless a
    # self-test vector replaces its https handler with an offline one), all refuse while the self-test runs
    real_urlopen = urllib.request.urlopen
    real_https_open = urllib.request.HTTPSHandler.https_open
    real_http_open = urllib.request.HTTPHandler.http_open
    urllib.request.urlopen = no_network
    urllib.request.HTTPSHandler.https_open = no_network_open
    urllib.request.HTTPHandler.http_open = no_network_open
    try:
        with tempfile.TemporaryDirectory(prefix="ci-build-bash-selftest-") as base:
            base = Path(base)
            fixtures = base / "fixtures"
            os.makedirs(fixtures)
            _fixture_archive(fixtures / "bash-5.3.tar.gz")
            _fixture_archive(fixtures / "minor-2.tar.gz", minor="2")
            _fixture_archive(fixtures / "installed-minor-2.tar.gz", installed_minor="2")
            for level in range(1, 10):
                (fixtures / "bash53-{:03d}".format(level)).write_text(FIXTURE_PATCH.format(level - 1, level),
                                                                     encoding="ascii")
            sample = fixtures / "bash53-001"
            digest = hashlib.sha256(sample.read_bytes()).hexdigest()
            size = len(sample.read_bytes())

            # verify: a file that matches its pin passes; one byte off, another size, or no file at all refuses
            check("verify/match-passes", _outcome(lambda: verify(sample, size, digest)) == ("ok", None))
            changed = base / "changed"
            changed.write_bytes(sample.read_bytes()[:-1] + b"X")
            got = _outcome(lambda: verify(changed, size, digest))
            check("verify/sha256-mismatch-refuses", got[0] == "refused" and "sha256" in got[1], got)
            got = _outcome(lambda: verify(sample, size + 1, digest))
            check("verify/size-mismatch-refuses", got[0] == "refused" and "bytes, pinned" in got[1], got)
            got = _outcome(lambda: verify(base / "absent", None, digest))
            check("verify/missing-file-refuses", got == ("refused", "absent: missing"), got)

            # fetch refuses a URL that is not https before any network call
            plain = io.BytesIO()
            got = _outcome(lambda: fetch_attempt("http://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz", plain,
                                                 lambda: DOWNLOAD_DEADLINE))
            check("fetch/non-https-refuses", got[0] == "not-completed" and "not an https URL" in got[1]
                  and not reached and not plain.getvalue(), got)

            # the version check: exactly 5 3 9 passes; the patch level one below or one above, another minor
            # version, a non-zero exit and a missing binary each refuse
            got = _outcome(lambda: check_version(_fake_bash(base / "v539", 'echo "5 3 9"\n'), "fixture"))
            check("version/match-passes", got == ("ok", None), got)
            got = _outcome(lambda: check_version(_fake_bash(base / "v538", 'echo "5 3 8"\n'), "fixture"))
            check("version/patch-below-refuses", got[0] == "refused" and "'5 3 8\\n'" in got[1], got)
            got = _outcome(lambda: check_version(_fake_bash(base / "v5310", 'echo "5 3 10"\n'), "fixture"))
            check("version/patch-above-refuses", got[0] == "refused" and "'5 3 10\\n'" in got[1], got)
            got = _outcome(lambda: check_version(_fake_bash(base / "v529", 'echo "5 2 9"\n'), "fixture"))
            check("version/minor-refuses", got[0] == "refused" and "'5 2 9\\n'" in got[1], got)
            got = _outcome(lambda: check_version(_fake_bash(base / "vexit", 'echo "5 3 9"\nexit 1\n'), "fixture"))
            check("version/nonzero-exit-refuses", got[0] == "refused" and "exit 1" in got[1], got)
            os.makedirs(base / "vnone")
            got = _outcome(lambda: check_version(base / "vnone", "fixture"))
            check("version/no-binary-refuses", got[0] == "not-completed" and "could not run" in got[1], got)

            # the whole pipeline over the fixtures: each run gets its own runner temp and GITHUB_PATH
            # (a fake clock serves every run, so no run ever really waits)
            def run(name, pins, fetcher=None, clock=None, sources=fixture_sources, log=None):
                clock = clock or _FakeClock()
                temp = base / name
                os.makedirs(temp)
                github_path = temp / "github_path"
                github_path.write_text("/already/listed\n", encoding="utf-8")
                got = _outcome(lambda: provision(pins, sources, fetcher or _fixture_fetcher(fixtures), temp,
                                                 github_path, log=log or (lambda line: None), sleep=clock.sleep,
                                                 clock=clock.now))
                untouched = (github_path.read_text(encoding="utf-8") == "/already/listed\n"
                             and not os.path.lexists(temp / INSTALL_NAME))
                return got, temp, github_path, untouched

            got, temp, github_path, _untouched = run("success", _fixture_pins(fixtures))
            bindir = temp / INSTALL_NAME / "bin"
            check("provision/success-installs-and-appends", got == ("ok", (bindir, fixture_sources * 10))
                  and github_path.read_text(encoding="utf-8") == "/already/listed\n" + str(bindir) + "\n"
                  and (bindir / "bash").is_file(), got)
            got, temp, _github_path, untouched = run("skewed", _fixture_pins(fixtures, skew="bash53-005"))
            check("provision/mismatch-installs-nothing", got[0] == "refused" and "bash53-005: sha256" in got[1]
                  and untouched and not (temp / (INSTALL_NAME + "-build") / SOURCE_DIR).exists(), got)
            got, _temp, _github_path, untouched = run("unfetched", _fixture_pins(fixtures),
                                                      _fixture_fetcher(fixtures, missing=("bash53-009",)))
            check("provision/download-error-installs-nothing", got[0] == "not-completed"
                  and "bash53-009" in got[1] and untouched, got)
            got, _temp, _github_path, untouched = run("reordered",
                                                      _fixture_pins(fixtures, order=(1, 3, 2, 4, 5, 6, 7, 8, 9)))
            check("provision/patch-order-installs-nothing", got[0] == "not-completed"
                  and got[1].startswith("patch bash53-003: exit") and untouched, got)
            got, _temp, _github_path, untouched = run("minor-2", _fixture_pins(fixtures, archive="minor-2.tar.gz"))
            check("provision/built-version-installs-nothing", got[0] == "refused"
                  and got[1].startswith("the built bash:") and untouched, got)
            got, _temp, _github_path, untouched = run("installed-minor-2",
                                                      _fixture_pins(fixtures, archive="installed-minor-2.tar.gz"))
            check("provision/installed-version-removes-install", got[0] == "refused"
                  and got[1].startswith("the installed bash:") and untouched, got)
            temp = base / "present"
            os.makedirs(temp / INSTALL_NAME)
            (temp / "github_path").write_text("", encoding="utf-8")
            got = _outcome(lambda: provision(_fixture_pins(fixtures), fixture_sources, _fixture_fetcher(fixtures), temp,
                                             temp / "github_path", log=lambda line: None, sleep=_FakeClock().sleep,
                                             clock=_FakeClock().now))
            check("provision/existing-install-refuses", got[0] == "not-completed" and "already exists" in got[1]
                  and set(os.listdir(temp)) == {INSTALL_NAME, "github_path"} and not os.listdir(temp / INSTALL_NAME)
                  and (temp / "github_path").read_text(encoding="utf-8") == "", got)

            # the classification: the named transient transport failures, bare or as a URLError's reason, are
            # Transient (the next source, and a retry); an HTTP status of any code, a TLS or certificate failure, a
            # permanent DNS failure, a refused connection and a malformed response head are SourceFailed (the next
            # source, no retry of their own); a local file error, another DNS failure, an unknown URL type and an
            # invalid URL are NotCompleted (the build stops at once)
            def kind(exc):
                return type(fetch_error("https://fixture.invalid/f", exc)).__name__

            transient = [ConnectionResetError(errno.ECONNRESET, "reset"), http.client.RemoteDisconnected("closed"),
                         TimeoutError("timed out"), OSError(errno.ENETUNREACH, "network unreachable"),
                         OSError(errno.EHOSTUNREACH, "no route to host"),
                         socket.gaierror(socket.EAI_AGAIN, "temporary failure in name resolution"),
                         http.client.IncompleteRead(b"part", 10),
                         urllib.error.URLError(ConnectionResetError(errno.ECONNRESET, "reset")),
                         urllib.error.URLError(TimeoutError("timed out")),
                         urllib.error.URLError(OSError(errno.ENETUNREACH, "network unreachable")),
                         urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "temporary failure")),
                         BrokenPipeError(errno.EPIPE, "broken pipe"),
                         ConnectionAbortedError(errno.ECONNABORTED, "connection aborted"),
                         urllib.error.URLError(BrokenPipeError(errno.EPIPE, "broken pipe")),
                         urllib.error.URLError(ConnectionAbortedError(errno.ECONNABORTED, "connection aborted"))]
            got = [(repr(exc), kind(exc)) for exc in transient]
            check("fetch/transient-errors-classified", all(name == "Transient" for _exc, name in got), got)
            tied = [urllib.error.HTTPError("https://fixture.invalid/f", code, "status", None, None)
                    for code in (404, 408, 429, 500, 502, 503, 504)]
            tied += [ssl.SSLCertVerificationError(1, "certificate verify failed"), ssl.SSLError(1, "tls"),
                     socket.gaierror(socket.EAI_NONAME, "name or service not known"),
                     socket.gaierror(socket.EAI_FAIL, "non-recoverable failure in name resolution"),
                     urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "not known")),
                     urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
                     ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
                     urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")),
                     http.client.BadStatusLine("garbage"), http.client.LineTooLong("header line"),
                     http.client.UnknownProtocol("HTTP/9"), http.client.UnknownTransferEncoding()]
            got = [(repr(exc), kind(exc)) for exc in tied]
            check("fetch/source-errors-classified", all(name == "SourceFailed" for _exc, name in got), got)
            permanent = [urllib.error.URLError("unknown url type: fixture"), PermissionError(errno.EACCES, "no"),
                         FileNotFoundError(errno.ENOENT, "absent"), OSError(errno.ENOSPC, "disk full"),
                         socket.gaierror(socket.EAI_MEMORY, "memory allocation failure"),
                         OSError("no errno"), ValueError("unknown url type"), http.client.InvalidURL("bad")]
            got = [(repr(exc), kind(exc)) for exc in permanent]
            check("fetch/other-errors-not-retried", all(name == "NotCompleted" for _exc, name in got), got)

            # one attempt: urlopen gets FETCH_TIMEOUT cut down to the time left, and a deadline found passed before
            # a read stops the attempt with NotCompleted, never Transient (so it is not retried); a read cut short
            # is Transient. fetch_attempt gets an offline fake opener for these calls, which sets the timeout it is
            # given on the response's socket as urllib does. The fake responses read through a real socket pair's
            # socket, reached as http.client's own responses are (fp.raw._sock), so each read records the socket
            # timeout in force when it began.
            def raised(call):
                try:
                    call()
                except Exception as exc:
                    return exc
                return None

            pairs = []

            def socket_pair():
                pair = socket.socketpair()
                pairs.extend(pair)
                return pair

            class FakeFile:
                def __init__(self, sock):
                    self.raw, self._sock = self, sock

            class FakeResponse:
                status = 200

                def __init__(self, steps, clock, cost):
                    self.steps, self.clock, self.cost = list(steps), clock, cost
                    self.fp = FakeFile(socket_pair()[0])
                    self.timeouts = []

                def __enter__(self):
                    return self

                def __exit__(self, *exc_info):
                    return False

                def getheader(self, name, default=None):
                    return default

                def read1(self, size):
                    self.timeouts.append(self.fp.raw._sock.gettimeout())
                    self.clock.advance(self.cost)
                    step = self.steps.pop(0) if self.steps else b""
                    if isinstance(step, BaseException):
                        raise step
                    return step

            def http_response(head, body, timeout):
                """A real http.client.HTTPResponse, its head already parsed, over one end of a socket pair whose
                other end sent head, a blank line and body, then closed; returns it and its socket."""
                near, far = socket_pair()
                far.sendall(head + b"\r\n\r\n" + body)
                far.close()
                near.settimeout(timeout)
                response = http.client.HTTPResponse(near, method="GET", url="/")
                response.begin()
                return response, near

            class FakeOpener:
                """An opener whose open(url, timeout) is open_url(url, timeout), as fetch_attempt calls one."""

                def __init__(self, open_url):
                    self.open_url = open_url

                def open(self, url, timeout=None):
                    return self.open_url(getattr(url, "full_url", url), timeout)

            def through(open_url):
                """A download through fetch_attempt with a FakeOpener over open_url."""
                opener = FakeOpener(open_url)
                return lambda url, out, remaining: fetch_attempt(url, out, remaining, opener)

            opened = []
            fake = _FakeClock()
            ends = fake.now() + 45
            slow_response = FakeResponse([b"a" * 10, b"b" * 10, b"c" * 10], fake, 30)
            responses = [slow_response, FakeResponse([b"x", http.client.IncompleteRead(b"", 5)], fake, 0)]

            def fake_urlopen(url, timeout=None):
                opened.append((url, timeout))
                response = responses.pop(0)
                response.fp.raw._sock.settimeout(timeout)
                return response

            gnu_urls = [GNU_SOURCES[0] + row[1] for row in PINS]
            fake_opener = FakeOpener(fake_urlopen)
            slow, cut = io.BytesIO(), io.BytesIO()
            slow_exc = raised(lambda: fetch_attempt(gnu_urls[1], slow, lambda: ends - fake.now(), fake_opener))
            cut_exc = raised(lambda: fetch_attempt(gnu_urls[2], cut, lambda: DOWNLOAD_DEADLINE, fake_opener))
            check("fetch/deadline-stops-attempt-between-reads",
                  type(slow_exc) is NotCompleted and "passed during the download" in str(slow_exc)
                  and slow.getvalue() == b"a" * 10 + b"b" * 10 and type(cut_exc) is Transient
                  and opened == [(gnu_urls[1], 45), (gnu_urls[2], FETCH_TIMEOUT)], (slow_exc, cut_exc, opened))

            # each body read gets FETCH_TIMEOUT cut down to the time left when it begins, not the timeout the
            # attempt opened with: the slow attempt above opened with 45 s left and read again with 15 s left. Over
            # a real chunked HTTPResponse whose time left drops 10 s before each step (45 s at the open, then 35,
            # 25 and 15 s before its three reads), the socket's timeout as each step begins and after the last read
            # is 45, 35, 25 and 15 s.
            lefts = [45, 35, 25, 15]
            real, seen = [], []

            def ticking():
                if real:
                    seen.append(real[0].gettimeout())
                return lefts.pop(0)

            def real_urlopen_chunked(url, timeout=None):
                response, sock = http_response(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked",
                                               b"3\r\nabc\r\n4\r\ndefg\r\n0\r\n\r\n", timeout)
                real.append(sock)
                return response

            chunked = io.BytesIO()
            chunked_exc = raised(lambda: fetch_attempt(gnu_urls[3], chunked, ticking,
                                                       FakeOpener(real_urlopen_chunked)))
            if real:
                seen.append(real[0].gettimeout())
            check("fetch/read-timeout-cut-to-time-left", slow_response.timeouts == [45, 15] and chunked_exc is None
                  and chunked.getvalue() == b"abcdefg" and seen == [45, 35, 25, 15] and not lefts,
                  (slow_response.timeouts, chunked_exc, chunked.getvalue(), seen, lefts))

            # a body that ends before its Content-Length, with no IncompleteRead from http.client, is a read cut
            # short: Transient, so it is retried. Over real HTTPResponses declaring the patch's byte count, the
            # first attempt's peer sends five bytes and closes; after one 2 s wait the second sends it whole, and
            # the file is published under its pinned name and passes its pin.
            body = sample.read_bytes()
            head = "HTTP/1.1 200 OK\r\nContent-Length: {}".format(len(body)).encode("ascii")
            bodies = [body[:5], body]
            short_source = ("https://fixture.invalid/",)
            short_url = short_source[0] + "bash53-001"

            def short_then_whole(url, timeout=None):
                return http_response(head, bodies.pop(0), timeout)[0]

            short_dir = base / "short-body"
            os.makedirs(short_dir)
            logged = []
            clock = _FakeClock()
            got = _outcome(lambda: download((("bash53-001", "bash53-001", None, digest),), short_source, short_dir,
                                            through(short_then_whole), clock.sleep, clock.now, log=logged.append))
            check("fetch/short-body-retried", got == ("ok", short_source) and not bodies and clock.sleeps == [2]
                  and len(logged) == 2 and "the body has 5 bytes; its Content-Length declares {}".format(len(body))
                  in logged[0] and os.listdir(short_dir) == ["bash53-001"]
                  and (short_dir / "bash53-001").read_bytes() == body, (got, bodies, clock.sleeps, logged))

            # a Content-Length that is not one decimal byte count (one http.client's int() would accept, a signed
            # one, and two headers joined) fails the download from that source with no retry of its own: from the
            # one source here, one attempt, no wait, and no file left in the downloads directory
            def one_download(name, heads_and_bodies):
                queue = list(heads_and_bodies)
                directory = base / name
                os.makedirs(directory)
                clock = _FakeClock()
                fetcher = through(lambda url, timeout: http_response(*queue.pop(0), timeout)[0])
                got = _outcome(lambda: download((("bash53-001", "bash53-001", None, digest),), short_source,
                                                directory, fetcher, clock.sleep, clock.now, log=lambda line: None))
                return got, len(heads_and_bodies) - len(queue), clock.sleeps, sorted(os.listdir(directory))

            malformed = []
            for index, value in enumerate(("5_0", "+{}".format(len(body)), "{0}\r\nContent-Length: {0}".format(
                    len(body)))):
                malformed.append(one_download("malformed-length-{}".format(index), [(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}".format(value).encode("ascii"), body)] * FETCH_ATTEMPTS))
            check("fetch/malformed-content-length-not-retried", all(
                got[0] == "not-completed" and "is not a byte count" in got[1] and used == 1 and sleeps == []
                and "every source failed in attempt 1 of 4 and none of the failures is transient" in got[1]
                and listing == [] for got, used, sleeps, listing in malformed), malformed)

            # a chunked body is framed by its chunks, not by a Content-Length sent beside them (which http.client
            # ignores too): the whole body is published under its pinned name
            chunked_body = "{:x}\r\n".format(len(body)).encode("ascii") + body + b"\r\n0\r\n\r\n"
            got = one_download("chunked-with-length", [(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 3", chunked_body)])
            check("fetch/chunked-ignores-content-length", got[0] == ("ok", short_source) and got[1] == 1
                  and got[2] == []
                  and got[3] == ["bash53-001"] and (base / "chunked-with-length" / "bash53-001").read_bytes() == body,
                  got)

            # the read timeout reach fails closed: a response with no file attribute, or whose file reaches no
            # socket, stops the attempt with NotCompleted (never retried); a response whose body is all read (its
            # file set to None) is left as it is
            class NoFile:
                pass

            class Unreachable:
                fp = FakeFile(None)

            class Drained:
                fp = None

            reach = [_outcome(lambda: _set_read_timeout(short_url, obj, 5)) for obj in (NoFile(), Unreachable(),
                                                                                        Drained())]
            check("fetch/socket-reach-fails-closed", reach[0][0] == "not-completed" and "no file" in reach[0][1]
                  and reach[1][0] == "not-completed" and "cannot be reached" in reach[1][1]
                  and reach[2] == ("ok", None), reach)

            # redirects, through urllib's own redirect handling in the opener fetch_attempt builds (_opener), with
            # its https handler replaced by an offline one that answers each URL from routes over a real
            # HTTPResponse (and any other URL with the right bytes, so a redirect followed where it must not be is
            # seen); urllib's http handler is the self-test's refusing one
            class OfflineHttps(urllib.request.HTTPSHandler):
                """Answers each URL from routes (a head and body, or an exception raised as the open's own), moving
                clock by cost(url) seconds first; records each URL opened, its timeout and its response."""

                def __init__(self, routes, default, clock=None, cost=None):
                    super().__init__()
                    self.routes, self.default, self.clock, self.cost = routes, default, clock, cost
                    self.opened, self.timeouts, self.responses = [], [], []

                def https_open(self, req):
                    self.opened.append(req.full_url)
                    self.timeouts.append(req.timeout)
                    if self.cost is not None:
                        self.clock.advance(self.cost(req.full_url))
                    route = self.routes.get(req.full_url, self.default)
                    if isinstance(route, BaseException):
                        raise route
                    response = http_response(*route, req.timeout)[0]
                    response.url, response.msg = req.full_url, response.reason  # as urllib's do_open sets them
                    self.responses.append((req.full_url, response))
                    return response

            whole = (head, body)
            redirect_sources = ("https://first.invalid/gnu/", "https://second.invalid/gnu/")
            first_url, second_url = (source + "bash53-001" for source in redirect_sources)

            def redirected(name, routes, pins_digest=digest, cost=None, keep=None):
                clock = _FakeClock()
                https = OfflineHttps(routes, whole, clock, cost)
                opener = _opener(urllib.request.ProxyHandler({}), https)
                directory = base / name
                os.makedirs(directory)
                logged = []
                got = _outcome(lambda: download((("bash53-001", "bash53-001", None, pins_digest),), redirect_sources,
                                                directory, lambda url, out, remaining: fetch_attempt(
                                                    url, out, remaining, opener), clock.sleep, clock.now,
                                                log=logged.append))
                if keep is not None:
                    keep.append(https)
                return got, https.opened, logged, clock.sleeps

            def location(code, target, moved_body=b""):
                return ("HTTP/1.1 {} Moved\r\nLocation: {}\r\nContent-Length: {}".format(
                    code, target, len(moved_body)).encode("ascii"), moved_body)

            # a redirect to http (on the same host) and one to https on another host are refused before they are
            # opened, naming where they pointed; the next source serves the file and the fetched line names it
            outcomes = []
            for index, (target, failed) in enumerate((("http://first.invalid/gnu/bash53-001", "https"),
                                                      ("https://elsewhere.invalid/gnu/bash53-001",
                                                       "on the same host"))):
                outcomes.append((target, failed, redirected("redirect-refused-{}".format(index),
                                                            {first_url: location(302, target)})))
            check("sources/redirect-not-https-same-host-refused", all(
                got == ("ok", redirect_sources[1:]) and opened == [first_url, second_url] and sleeps == []
                and logged == ["attempt 1 of 4 from {0} failed ({0}: redirect (302) to {1} refused (failed: {3}; "
                               "only https, on the same host, on the same port, with no userinfo is allowed)) (not "
                               "tried: {2}); trying {2}".format(first_url, target, second_url, failed),
                               "fetched bash53-001 from {}".format(second_url)]
                for target, failed, (got, opened, logged, sleeps) in outcomes) and not reached, (outcomes, reached))

            # a redirect to https on the same host is followed, and the fetched line names where it led; a download
            # there that differs from its pin is refused naming that URL too
            moved = "https://first.invalid/mirror/gnu/bash53-001"
            followed = redirected("redirect-same-host", {first_url: location(301, "/mirror/gnu/bash53-001"),
                                                         moved: whole})
            wrong = redirected("redirect-same-host-wrong", {first_url: location(307, moved), moved: whole},
                               hashlib.sha256(b"other bytes").hexdigest())
            check("sources/redirect-same-host-https-followed", followed == (
                ("ok", redirect_sources[:1]), [first_url, moved], ["fetched bash53-001 from {}".format(moved)], [])
                and wrong[0][0] == "refused" and "(fetched from {}; refused at once".format(moved) in wrong[0][1]
                and wrong[1] == [first_url, moved] and not reached, (followed, wrong, reached))

            # the lines of a download that fails from the first source and completes from the second
            def moved_on(failure):
                return ["attempt 1 of 4 from {0} failed ({1}) (not tried: {2}); trying {2}".format(
                    first_url, failure, second_url), "fetched bash53-001 from {}".format(second_url)]

            # every refusal from the origin check names the conditions the URL failed, then the whole rule
            rule = "only https, on the same host, on the same port, with no userinfo is allowed"

            def refusal(code, target, failed):
                return "redirect ({}) to {} refused (failed: {}; {})".format(code, target, failed, rule)

            # the port: a URL that names no port is on 443 and one that names a port, 0 included, is on that port.
            # A redirect to the same host on explicit port 0 or on another port is refused before it is opened; one
            # to explicit port 443 from a URL that names none is followed.
            table = [(old, new, _same_host_https(old, new), want) for old, new, want in (
                (first_url, "https://first.invalid/m/f", True), (first_url, "https://first.invalid:443/m/f", True),
                ("https://first.invalid:443/gnu/f", "https://first.invalid/m/f", True),
                (first_url, "https://first.invalid:0/m/f", False), (first_url, "https://first.invalid:8443/m/f", False),
                ("https://first.invalid:0/gnu/f", "https://first.invalid/m/f", False),
                ("https://first.invalid:8443/gnu/f", "https://first.invalid:8443/m/f", True))]
            ports = []
            for index, target in enumerate(("https://first.invalid:0/gnu/bash53-001",
                                            "https://first.invalid:8443/gnu/bash53-001")):
                ports.append((target, redirected("redirect-port-{}".format(index), {first_url: location(302, target)})))
            explicit = "https://first.invalid:443/mirror/bash53-001"
            port_443 = redirected("redirect-port-443", {first_url: location(302, explicit), explicit: whole})
            check("sources/redirect-port-compared", all(got == want for _old, _new, got, want in table) and all(
                got == ("ok", redirect_sources[1:]) and opened == [first_url, second_url] and sleeps == []
                and logged == moved_on("{}: {}".format(first_url, refusal(302, target, "on the same port")))
                for target, (got, opened, logged, sleeps) in ports)
                and port_443 == (("ok", redirect_sources[:1]), [first_url, explicit],
                                 ["fetched bash53-001 from {}".format(explicit)], [])
                and not reached, (table, ports, port_443, reached))

            # a Location that does not parse (urllib's own parse would raise ValueError on it) is a refused redirect
            # naming that Location: the next source serves the file, with no wait
            unparsable = "https://[first.invalid/gnu/bash53-001"
            got = redirected("redirect-unparsable", {first_url: location(302, unparsable)})
            check("sources/redirect-location-unparsable-moves-on", got == (
                ("ok", redirect_sources[1:]), [first_url, second_url],
                moved_on("{}: {}".format(first_url, refusal(302, unparsable, "parsing"))), []) and not reached, got)

            # any other ValueError raised while opening moves on to the next source too, naming the URL opened
            got = redirected("open-value-error", {first_url: ValueError("a value the opening cannot take")})
            check("sources/open-value-error-moves-on", got == (
                ("ok", redirect_sources[1:]), [first_url, second_url],
                moved_on("{}: fetch failed (ValueError: a value the opening cannot take)".format(first_url)), [])
                and not reached, got)

            # an http.client exception from the response head that is none of the named malformed heads (a head of
            # more header lines than http.client takes) moves on to the next source, naming the URL opened
            crowded = b"HTTP/1.1 200 OK\r\n" + b"\r\n".join(b"X-Filler-%d: x" % index for index in range(120))
            got = redirected("head-too-many-headers", {first_url: (crowded, b"")})
            check("sources/head-http-exception-moves-on", got == (
                ("ok", redirect_sources[1:]), [first_url, second_url],
                moved_on("{}: fetch failed (HTTPException: got more than 100 headers)".format(first_url)), [])
                and not reached, got)

            # the final URL is checked again after the opening: a response served from another host (as an opener
            # whose redirect handler did not refuse would return it) is refused, its bytes never published, and the
            # next source serves the file
            elsewhere = "https://elsewhere.invalid/gnu/bash53-001"

            def served_elsewhere(url, timeout):
                response = http_response(*whole, timeout)[0]
                response.url = elsewhere if url == first_url else url
                return response

            recheck = base / "final-url-recheck"
            os.makedirs(recheck)
            logged = []
            clock = _FakeClock()
            got = _outcome(lambda: download((("bash53-001", "bash53-001", None, digest),), redirect_sources, recheck,
                                            through(served_elsewhere), clock.sleep, clock.now, log=logged.append))
            check("sources/final-url-rechecked", got == ("ok", redirect_sources[1:]) and clock.sleeps == []
                  and logged == moved_on("{} (redirected to {}): served from a URL that is refused (failed: on the "
                                         "same host; {})".format(first_url, elsewhere, rule))
                  and os.listdir(recheck) == ["bash53-001"], (got, logged, clock.sleeps))

            # userinfo: a URL that carries a username, or an @ in its netloc (percent-encoded or after a backslash
            # included), is not on the host urlsplit reports, since urllib connects to its whole netloc. Such a
            # redirect is refused before it is opened, and a response served from such a URL is refused after the
            # opening; in both the next source serves the file, and each refusal names the userinfo condition (or,
            # for a percent-encoded @, the host) as the one failed. An @ outside the netloc, in the path or the
            # query, is no userinfo: such a redirect on the same host is followed
            userinfo_forms = ("https://u@first.invalid/gnu/bash53-001", "https://u:p@first.invalid/gnu/bash53-001",
                              "https://@first.invalid/gnu/bash53-001",
                              "https://elsewhere.invalid%2F@first.invalid/gnu/bash53-001",
                              "https://elsewhere.invalid\\@first.invalid/gnu/bash53-001",
                              "https://elsewhere.invalid%40first.invalid/gnu/bash53-001",
                              "//u@first.invalid/gnu/bash53-001")
            userinfo_failed = ("with no userinfo",) * 5 + ("on the same host", "with no userinfo")
            userinfo_table = [(target, _same_host_https(first_url, urllib.parse.urljoin(first_url, target)),
                               _same_origin_failures(first_url, urllib.parse.urljoin(first_url, target)))
                              for target in userinfo_forms]
            userinfo_redirects = []
            for index, target in enumerate(userinfo_forms[:2] + userinfo_forms[3:5]):
                userinfo_redirects.append((target, redirected("redirect-userinfo-{}".format(index),
                                                              {first_url: location(302, target)})))
            at_outside = ("/m/a@elsewhere.invalid/bash53-001", "/m/bash53-001?u=a@elsewhere.invalid")
            at_outside_followed = []
            for index, target in enumerate(at_outside):
                moved_to = urllib.parse.urljoin(first_url, target)
                at_outside_followed.append((moved_to, _same_host_https(first_url, moved_to), redirected(
                    "redirect-at-outside-netloc-{}".format(index),
                    {first_url: location(302, target), moved_to: whole})))
            with_userinfo = userinfo_forms[0]

            def served_with_userinfo(url, timeout):
                response = http_response(*whole, timeout)[0]
                response.url = with_userinfo if url == first_url else url
                return response

            userinfo_final = base / "final-url-userinfo"
            os.makedirs(userinfo_final)
            final_logged = []
            clock = _FakeClock()
            got = _outcome(lambda: download((("bash53-001", "bash53-001", None, digest),), redirect_sources,
                                            userinfo_final, through(served_with_userinfo), clock.sleep, clock.now,
                                            log=final_logged.append))
            check("sources/redirect-userinfo-refused", [row[1:] for row in userinfo_table] == [
                (False, (failed,)) for failed in userinfo_failed] and all(
                outcome == (("ok", redirect_sources[1:]), [first_url, second_url],
                            moved_on("{}: {}".format(first_url, refusal(302, target, "with no userinfo"))), [])
                for target, outcome in userinfo_redirects)
                and got == ("ok", redirect_sources[1:]) and clock.sleeps == []
                and final_logged == moved_on("{} (redirected to {}): served from a URL that is refused (failed: with "
                                             "no userinfo; {})".format(first_url, with_userinfo, rule))
                and os.listdir(userinfo_final) == ["bash53-001"] and all(
                    same and outcome == (("ok", redirect_sources[:1]), [first_url, moved_to],
                                         ["fetched bash53-001 from {}".format(moved_to)], [])
                    for moved_to, same, outcome in at_outside_followed) and not reached,
                (userinfo_table, userinfo_redirects, got, final_logged, at_outside_followed, reached))

            # redirect_request checks urllib's own resolution of the Location again, called directly here (the
            # Location check before it is at least as strict, so no redirect through the opener reaches this one): a
            # newurl on another host, or carrying userinfo, is refused with its response closed and no hop added; one
            # on the same host gets a request that carries the hops list and remaining(), and is added to the hops
            class Closable(io.BytesIO):
                pass

            direct = []
            for newurl in ("https://elsewhere.invalid/gnu/bash53-001", "http://first.invalid/gnu/bash53-001",
                           with_userinfo, "https://first.invalid/m/bash53-001"):
                request = urllib.request.Request(first_url)
                request.hops, request.remaining = [first_url], _FakeClock().now
                response = Closable(b"")
                try:
                    new = _SameHostHttpsRedirect().redirect_request(request, response, 302, "Found",
                                                                    http.client.HTTPMessage(), newurl)
                    result = ("followed", new.full_url, new.hops is request.hops,
                              new.remaining is request.remaining)
                except _RedirectRefused as exc:
                    result = ("refused", exc.code, exc.target, exc.source, exc.failed)
                direct.append((newurl, result, response.closed, request.hops))
            check("sources/redirect-request-rechecked", [row[1:] for row in direct] == [
                (("refused", 302, newurl, first_url, (failed,)), True, [first_url]) for (newurl, *_rest), failed in zip(
                    direct[:3], ("on the same host", "https", "with no userinfo"))] + [
                (("followed", "https://first.invalid/m/bash53-001", True, True), False,
                 [first_url, "https://first.invalid/m/bash53-001"])], direct)

            # a failure line names the hop that returned the redirect or the error, and never a refused target as
            # one redirected to: a cross-host 302 from the second hop of a same-host 301, a redirect to file:, and a
            # connection reset while the followed hop opens
            hop = "https://first.invalid/m1/bash53-001"
            two_hop = redirected("redirect-hop-names", {first_url: location(301, "/m1/bash53-001"),
                                                        hop: location(302, elsewhere)})
            file_target = "file:///etc/passwd"
            file_scheme = redirected("redirect-file-scheme", {first_url: location(302, file_target)})
            hop_reset = redirected("redirect-hop-reset", {first_url: location(301, hop), hop: urllib.error.URLError(
                ConnectionResetError(errno.ECONNRESET, "reset"))})
            check("sources/redirect-failure-names-hop", two_hop == (
                ("ok", redirect_sources[1:]), [first_url, hop, second_url],
                moved_on("{} (redirected to {}): {}".format(first_url, hop, refusal(302, elsewhere,
                                                                                    "on the same host"))), [])
                and file_scheme == (("ok", redirect_sources[1:]), [first_url, second_url], moved_on("{}: {}".format(
                    first_url, refusal(302, file_target, "https, on the same host"))), [])
                and hop_reset == (("ok", redirect_sources[1:]), [first_url, hop, second_url], moved_on(
                    "{} (redirected to {}): fetch failed (URLError: <urlopen error [Errno 104] reset>)".format(
                        first_url, hop)), [])
                and not reached, (two_hop, file_scheme, hop_reset, reached))

            # a followed redirect's own body is never read: its response is closed with the 7 bytes it declares
            # still unread, and the hop it leads to serves the file
            kept = []
            got = redirected("redirect-body-unread", {first_url: location(301, hop, b"x" * 7), hop: whole}, keep=kept)
            moved_responses = [response for url, response in kept[0].responses if url == first_url]
            check("sources/redirect-body-never-read", got == (
                ("ok", redirect_sources[:1]), [first_url, hop], ["fetched bash53-001 from {}".format(hop)], [])
                and len(moved_responses) == 1 and moved_responses[0].isclosed() and moved_responses[0].length == 7,
                (got, [(response.isclosed(), response.length) for response in moved_responses]))

            # a redirect that arrives once the deadline has passed is not followed and stops the build, its hop never
            # opened; one that arrives with 10 s left opens its hop with a 10 s timeout, not the attempt's 60 s
            late = redirected("redirect-after-deadline", {first_url: location(301, hop)},
                              cost=lambda url: DOWNLOAD_DEADLINE if url == first_url else 0)
            kept = []
            cut = redirected("redirect-hop-timeout", {first_url: location(301, hop), hop: whole},
                             cost=lambda url: DOWNLOAD_DEADLINE - 10 if url == first_url else 0, keep=kept)
            check("sources/redirect-deadline-cuts-next-hop", late[0][0] == "not-completed"
                  and late[0][1] == "{}: the {} s download deadline passed before its redirect (301) to {} was "
                                    "followed (not tried: {})".format(first_url, DOWNLOAD_DEADLINE, hop, second_url)
                  and late[1] == [first_url] and late[3] == []
                  and cut[:2] == (("ok", redirect_sources[:1]), [first_url, hop])
                  and kept[0].timeouts == [FETCH_TIMEOUT, 10], (late, cut, kept[0].timeouts))
            for sock in pairs:
                sock.close()

            # the retry: a transient failure, then success. The archive's first attempt leaves partial bytes and
            # fails with a connection reset; one 2 s wait, then a second attempt into a new empty file completes and
            # the build installs. Each attempt saw only its own attempt file, and none is left behind.
            def fixture_url(row, source=fixture_sources[0]):
                return source + row[1]

            pins = _fixture_pins(fixtures)
            archive_url = fixture_url(pins[0])
            archive_bytes = (fixtures / pins[0][0]).read_bytes()
            script = {fixture_url(row): [(fixtures / row[0]).read_bytes()] for row in pins}
            script[archive_url] = [fetch_error(archive_url, ConnectionResetError(errno.ECONNRESET, "reset")),
                                   archive_bytes]
            watch = base / "retry-success" / (INSTALL_NAME + "-build") / "downloads"
            fetcher, calls = _scripted_fetcher(script, watch)
            clock = _FakeClock()
            got, temp, github_path, _untouched = run("retry-success", pins, fetcher, clock)
            bindir = temp / INSTALL_NAME / "bin"
            archive_calls = [call for call in calls if call[0] == archive_url]
            check("retry/transient-then-success", got == ("ok", (bindir, fixture_sources * 10))
                  and len(calls) == len(pins) + 1
                  and len(archive_calls) == 2 and clock.sleeps == [2] and all(call[1] == 0 for call in calls)
                  and all(len([n for n in call[2] if ".attempt-" in n]) == 1 for call in calls)
                  and not [n for n in os.listdir(watch) if ".attempt-" in n]
                  and (watch / pins[0][0]).read_bytes() == archive_bytes
                  and github_path.read_text(encoding="utf-8") == "/already/listed\n" + str(bindir) + "\n",
                  (got, calls, clock.sleeps))

            # the sources: three fixture sources stand in for GNU_SOURCES. The first is unreachable for every file
            # (Errno 101, as urllib reports it); the archive moves on to the second source at once, with no wait and
            # into a new empty file, and the first source, having failed, moves to the back of the order, so every
            # later file is asked of the second source first; the build installs from it. The third is never asked.
            mirror_sources = ("fixture-a://", "fixture-b://", "fixture-c://")
            unreachable = urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable"))
            script = {}
            for row in pins:
                script[fixture_url(row, mirror_sources[0])] = [fetch_error(fixture_url(row, mirror_sources[0]),
                                                                           unreachable)]
                for source in mirror_sources[1:]:
                    script[fixture_url(row, source)] = [(fixtures / row[0]).read_bytes()]
            watch = base / "second-source" / (INSTALL_NAME + "-build") / "downloads"
            fetcher, calls = _scripted_fetcher(script, watch)
            clock = _FakeClock()
            logged = []
            got, temp, github_path, _untouched = run("second-source", pins, fetcher, clock, mirror_sources,
                                                     logged.append)
            bindir = temp / INSTALL_NAME / "bin"
            check("sources/first-unreachable-second-completes",
                  got == ("ok", (bindir, mirror_sources[1:2] * len(pins)))
                  and served_summary(mirror_sources, got[1][1]) == "{} from fixture-b://".format(len(pins))
                  and [call[0] for call in calls] == [fixture_url(row, source) for row in pins[:1]
                                                      for source in mirror_sources[:2]]
                  + [fixture_url(row, mirror_sources[1]) for row in pins[1:]]
                  and clock.sleeps == [] and all(call[1] == 0 for call in calls)
                  and all(len([n for n in call[2] if ".attempt-" in n]) == 1 for call in calls)
                  and not [n for n in os.listdir(watch) if ".attempt-" in n]
                  and "attempt 1 of 4 from fixture-a://bash-5.3.tar.gz failed (fixture-a://bash-5.3.tar.gz: fetch "
                      "failed (URLError: <urlopen error [Errno 101] Network is unreachable>)) (not tried: "
                      "fixture-b://bash-5.3.tar.gz, fixture-c://bash-5.3.tar.gz); trying "
                      "fixture-b://bash-5.3.tar.gz" in logged
                  and "fetched bash-5.3.tar.gz from fixture-b://bash-5.3.tar.gz" in logged
                  and github_path.read_text(encoding="utf-8") == "/already/listed\n" + str(bindir) + "\n",
                  (got, calls, clock.sleeps, logged))

            # a mirror serving wrong bytes: the first source is unreachable and the second serves an archive of
            # another byte count. That is refused at once by the archive's one pin, naming the source that served
            # it, the one that failed before it and the one not tried; the third, which would serve the right
            # bytes, is never asked, nothing waits and nothing is installed.
            script[fixture_url(pins[0], mirror_sources[1])] = [b"not the pinned archive"]
            fetcher, calls = _scripted_fetcher(script, base / "wrong-mirror" / (INSTALL_NAME + "-build")
                                               / "downloads")
            clock = _FakeClock()
            got, temp, _github_path, untouched = run("wrong-mirror", pins, fetcher, clock, mirror_sources)
            archive_urls = [fixture_url(pins[0], source) for source in mirror_sources]
            check("sources/mirror-wrong-bytes-refused-at-once", got[0] == "refused"
                  and got[1].startswith("bash-5.3.tar.gz: 22 bytes, pinned {} (fetched from {}; refused at once".format(
                      pins[0][2], archive_urls[1]))
                  and "earlier sources in this attempt: {}: fetch failed".format(archive_urls[0]) in got[1]
                  and got[1].endswith("not tried: {})".format(archive_urls[2]))
                  and [call[0] for call in calls] == archive_urls[:2] and clock.sleeps == [] and untouched
                  and not (temp / (INSTALL_NAME + "-build") / SOURCE_DIR).exists(), (got, calls, clock.sleeps))

            # the deadline crossing between sources: the first source's failed download of the archive ends 1 s
            # past the 300 s deadline, so the second source is never asked, nor announced as the one tried next; the
            # build stops at once, without a wait, and the failure line names the source not started, the one that
            # failed and the one not tried
            script[fixture_url(pins[0], mirror_sources[1])] = [archive_bytes]
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher(script, base / "deadline-sources" / (INSTALL_NAME + "-build")
                                               / "downloads", clock, DOWNLOAD_DEADLINE + 1)
            logged = []
            got, _temp, _github_path, untouched = run("deadline-sources", pins, fetcher, clock, mirror_sources,
                                                      logged.append)
            check("sources/deadline-between-sources", got[0] == "not-completed"
                  and got[1].startswith("bash-5.3.tar.gz: the {} s download deadline passed before attempt 1 from "
                                        "{} (earlier sources in this attempt: {}: fetch failed".format(
                                            DOWNLOAD_DEADLINE, archive_urls[1], archive_urls[0]))
                  and got[1].endswith("not tried: {})".format(archive_urls[2])) and logged == []
                  and [call[0] for call in calls] == archive_urls[:1] and clock.sleeps == [] and untouched,
                  (got, calls, clock.sleeps, logged))

            # a failure tied to one source that is not transient moves on to the next source too: the archive's
            # first source resets the connection (transient), its second answers 404 and its third serves it, with
            # no wait; each failed source is logged naming its failure, the sources that failed before it and those
            # not tried, and the next source only once its deadline check has passed. Every later file is asked of
            # the third source first, as the other two failed.
            def source_script(steps):
                """A _scripted_fetcher script over the fixture pins: steps maps (file index, source index) to one
                step list; every other URL serves the file's right bytes."""
                return {fixture_url(row, source): steps.get((row_index, source_index),
                                                            [(fixtures / row[0]).read_bytes()])
                        for row_index, row in enumerate(pins) for source_index, source in enumerate(mirror_sources)}

            reset_a = fetch_error(archive_urls[0], ConnectionResetError(errno.ECONNRESET, "reset"))
            missing_b = fetch_error(archive_urls[1], urllib.error.HTTPError(archive_urls[1], 404, "Not Found", None,
                                                                            None))
            fetcher, calls = _scripted_fetcher(source_script({(0, 0): [reset_a], (0, 1): [missing_b]}),
                                               base / "transient-404" / (INSTALL_NAME + "-build") / "downloads")
            clock = _FakeClock()
            logged = []
            got, temp, _github_path, _untouched = run("transient-404", pins, fetcher, clock, mirror_sources,
                                                      logged.append)
            bindir = temp / INSTALL_NAME / "bin"
            check("sources/transient-then-404-then-success",
                  got == ("ok", (bindir, mirror_sources[2:] * len(pins))) and clock.sleeps == []
                  and [call[0] for call in calls] == archive_urls + [fixture_url(row, mirror_sources[2])
                                                                     for row in pins[1:]]
                  and logged[:3] == [
                      "attempt 1 of 4 from {0} failed ({1}) (not tried: {2}, {3}); trying {2}".format(
                          archive_urls[0], reset_a, archive_urls[1], archive_urls[2]),
                      "attempt 1 of 4 from {0} failed ({1}) (earlier sources in this attempt: {2}; not tried: {3}); "
                      "trying {3}".format(archive_urls[1], missing_b, reset_a, archive_urls[2]),
                      "fetched bash-5.3.tar.gz from {}".format(archive_urls[2])], (got, calls, clock.sleeps, logged))

            # a refused connection (as urllib wraps it) moves on to the next source, which serves the file
            refused_a = fetch_error(archive_urls[0], urllib.error.URLError(ConnectionRefusedError(
                errno.ECONNREFUSED, "Connection refused")))
            fetcher, calls = _scripted_fetcher(source_script({(0, 0): [refused_a]}),
                                               base / "refused" / (INSTALL_NAME + "-build") / "downloads")
            clock = _FakeClock()
            got, temp, _github_path, _untouched = run("refused", pins, fetcher, clock, mirror_sources)
            bindir = temp / INSTALL_NAME / "bin"
            check("sources/refused-then-success", type(refused_a) is SourceFailed
                  and got == ("ok", (bindir, mirror_sources[1:2] * len(pins))) and clock.sleeps == []
                  and [call[0] for call in calls][:2] == archive_urls[:2], (got, calls, clock.sleeps))

            # the source order lasts for the run: the first source is unreachable at once, the second times out
            # after 60 s and the third serves each file in 1 s. Only the archive asks all three; every later file
            # is asked of the third first, so the ten files take 70 s of the 300 (asked in GNU_SOURCES order every
            # time, the fifth file would pass the deadline).
            steps = {}
            for index, row in enumerate(pins):
                steps[(index, 0)] = [fetch_error(fixture_url(row, mirror_sources[0]), unreachable)]
                steps[(index, 1)] = [fetch_error(fixture_url(row, mirror_sources[1]), TimeoutError("timed out"))]
            costs = {mirror_sources[0]: 0, mirror_sources[1]: 60, mirror_sources[2]: 1}
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher(source_script(steps), base / "slow-second" / (INSTALL_NAME + "-build")
                                               / "downloads", clock, lambda url: costs[url.split("//")[0] + "//"])
            got, temp, _github_path, _untouched = run("slow-second", pins, fetcher, clock, mirror_sources)
            bindir = temp / INSTALL_NAME / "bin"
            check("sources/slow-second-source-ordering",
                  got == ("ok", (bindir, mirror_sources[2:] * len(pins))) and clock.sleeps == []
                  and clock.now() - 1000.0 == 60 + len(pins)
                  and [call[0] for call in calls] == archive_urls + [fixture_url(row, mirror_sources[2])
                                                                     for row in pins[1:]],
                  (got, calls, clock.sleeps, clock.now()))

            # a source that fails moves to the back of the order even when it was not first: the first source
            # serves the archive; patch 001 fails from it (404) and comes from the second; patch 002 fails from
            # the second (reset), so the third is asked next, not the first, which failed more recently than the
            # third was asked
            urls_001 = [fixture_url(pins[1], source) for source in mirror_sources]
            urls_002 = [fixture_url(pins[2], source) for source in mirror_sources]
            fetcher, calls = _scripted_fetcher(source_script({
                (1, 0): [fetch_error(urls_001[0], urllib.error.HTTPError(urls_001[0], 404, "Not Found", None, None))],
                (2, 1): [fetch_error(urls_002[1], ConnectionResetError(errno.ECONNRESET, "reset"))]}),
                base / "moved-back" / (INSTALL_NAME + "-build") / "downloads")
            clock = _FakeClock()
            got, temp, _github_path, _untouched = run("moved-back", pins, fetcher, clock, mirror_sources)
            bindir = temp / INSTALL_NAME / "bin"
            check("sources/failed-source-moved-back",
                  got == ("ok", (bindir, (mirror_sources[0], mirror_sources[1]) + mirror_sources[2:] * (len(pins) - 2)))
                  and [call[0] for call in calls] == [archive_urls[0]] + urls_001[:2] + [urls_002[1], urls_002[2]]
                  + [fixture_url(row, mirror_sources[2]) for row in pins[3:]] and clock.sleeps == [],
                  (got, calls, clock.sleeps))

            # the CI entry over the real pin table with a scripted fetcher (nothing reaches the network): a
            # persistent transient failure, a size mismatch, a digest mismatch and failures that are not transient
            def entry_run(name, script, cost=0):
                temp = base / name
                os.makedirs(temp)
                listed_here = temp / "github_path"
                listed_here.write_text("/already/listed\n", encoding="utf-8")
                work = temp / (INSTALL_NAME + "-build")
                fetcher, calls = _scripted_fetcher(script, work / "downloads")
                clock = _FakeClock()
                err = io.StringIO()
                logged = []

                def call():
                    with contextlib.redirect_stderr(err):
                        return build({"RUNNER_TEMP": str(temp), "GITHUB_PATH": str(listed_here)}, fetcher,
                                     log=logged.append, sleep=clock.sleep, clock=clock.now)
                got = _outcome(call)
                untouched = (listed_here.read_text(encoding="utf-8") == "/already/listed\n"
                             and not os.path.lexists(temp / INSTALL_NAME) and not (work / SOURCE_DIR).exists()
                             and not [n for n in os.listdir(work / "downloads") if ".attempt-" in n])
                return got, calls, clock.sleeps, untouched, err.getvalue(), logged

            # the archive's URL from each of GNU_SOURCES, in order
            urls0 = [source + PINS[0][1] for source in GNU_SOURCES]
            url0 = urls0[0]
            got, calls, sleeps, untouched, _err, _logged = entry_run(
                "retry-exhausted", {url: [fetch_error(url, TimeoutError("timed out"))] for url in urls0})
            check("retry/persistent-transient-exits-2", got == ("ok", 2)
                  and [call[0] for call in calls] == urls0 * 4 and sleeps == [2, 4, 8] and untouched,
                  (got, calls, sleeps))
            # every source unreachable, as on a runner with no route out (Errno 101 from each): every attempt asks
            # each source in order, the three waits are taken, the run exits 2 with nothing installed, and the
            # failure line and each retry line name every source with its failure
            got, calls, sleeps, untouched, err, logged = entry_run(
                "sources-all-unreachable", {url: [fetch_error(url, unreachable)] for url in urls0})
            each = "; ".join("{}: fetch failed (URLError: <urlopen error [Errno 101] Network is unreachable>)".format(
                url) for url in urls0)
            retried = [line for line in logged if "retrying in" in line]
            check("sources/all-unreachable-names-each", got == ("ok", 2)
                  and [call[0] for call in calls] == urls0 * FETCH_ATTEMPTS and sleeps == [2, 4, 8] and untouched
                  and err == "NOT COMPLETED: bash-5.3.tar.gz: every source failed in each of 4 attempts (attempt 4: "
                             "{}). Nothing was installed.\n".format(each)
                  and retried == ["attempt {} of 4 failed from every source ({}); retrying in {} s".format(
                      attempt, each, delay) for attempt, delay in zip((1, 2, 3), RETRY_DELAYS)],
                  (got, calls, sleeps, err, logged))
            # a completed archive of another byte count is refused by its size pin; one of the pinned byte count with
            # another sha256 gets past the size pin and is refused by its digest. Neither is retried, and both exit 1.
            got, calls, sleeps, untouched, err, _logged = entry_run("retry-size-mismatch",
                                                                    {url0: [b"not the pinned archive"]})
            check("retry/size-mismatch-not-retried", got == ("ok", 1) and [call[0] for call in calls] == [url0]
                  and sleeps == [] and untouched and "bash-5.3.tar.gz: 22 bytes, pinned {} (fetched from {}".format(
                      PINS[0][2], url0) in err, (got, calls, sleeps, err))
            got, calls, sleeps, untouched, err, _logged = entry_run("retry-digest-mismatch",
                                                                    {url0: [bytes(PINS[0][2])]})
            check("retry/digest-mismatch-not-retried", got == ("ok", 1) and [call[0] for call in calls] == [url0]
                  and sleeps == [] and untouched and "bash-5.3.tar.gz: sha256 {}, pinned {} (fetched from {}".format(
                      hashlib.sha256(bytes(PINS[0][2])).hexdigest(), PINS[0][3], url0) in err,
                  (got, calls, sleeps, err))
            # an attempt whose every source failed with no failure transient is not retried: each source is asked
            # once, nothing waits, and the failure line names each source with its failure
            outcomes = []
            for index, make in enumerate([lambda url: ssl.SSLCertVerificationError(1, "certificate verify failed"),
                                          lambda url: urllib.error.HTTPError(url, 503, "unavailable", None, None),
                                          lambda url: urllib.error.URLError(socket.gaierror(socket.EAI_NONAME,
                                                                                            "not known"))]):
                script = {url: [fetch_error(url, make(url))] for url in urls0}
                outcomes.append((entry_run("retry-permanent-{}".format(index), script),
                                 "; ".join(str(script[url][0]) for url in urls0)))
            check("retry/non-transient-not-retried", all(
                got == ("ok", 2) and [call[0] for call in calls] == urls0 and sleeps == [] and untouched
                and err == "NOT COMPLETED: bash-5.3.tar.gz: every source failed in attempt 1 of 4 and none of the "
                           "failures is transient, so it is not retried ({}). Nothing was installed.\n".format(each)
                for (got, calls, sleeps, untouched, err, _logged), each in outcomes), outcomes)
            # an attempt with one transient failure among failures that are not is retried: the first source times
            # out, the second answers 404 and the third refuses the connection, in every attempt; all four attempts
            # run, with the 2, 4 and 8 s waits, and the build exits 2
            mixed = {urls0[0]: [fetch_error(urls0[0], TimeoutError("timed out"))],
                     urls0[1]: [fetch_error(urls0[1], urllib.error.HTTPError(urls0[1], 404, "Not Found", None, None))],
                     urls0[2]: [fetch_error(urls0[2], ConnectionRefusedError(errno.ECONNREFUSED, "refused"))]}
            got, calls, sleeps, untouched, err, _logged = entry_run("retry-mixed", mixed)
            check("retry/mixed-failures-retried", got == ("ok", 2)
                  and [call[0] for call in calls] == urls0 * FETCH_ATTEMPTS and sleeps == [2, 4, 8] and untouched
                  and err.startswith("NOT COMPLETED: bash-5.3.tar.gz: every source failed in each of 4 attempts "
                                     "(attempt 4: {})".format("; ".join(str(mixed[url][0]) for url in urls0))),
                  (got, calls, sleeps, err))

            # a file already at a pinned name in the downloads directory is never replaced, even after a failed
            # attempt was cleaned up: the completed download is refused and the file keeps its bytes
            keep = base / "keep"
            os.makedirs(keep)
            (keep / "bash53-001").write_bytes(b"kept")
            one = _fixture_pins(fixtures)[1:2]
            fetcher, calls = _scripted_fetcher(
                {fixture_url(one[0]): [fetch_error(fixture_url(one[0]), ConnectionResetError(errno.ECONNRESET,
                                                                                            "reset")),
                                       sample.read_bytes()]}, keep)
            clock = _FakeClock()
            got = _outcome(lambda: download(one, fixture_sources, keep, fetcher, clock.sleep, clock.now,
                                            log=lambda line: None))
            check("retry/existing-destination-preserved", got[0] == "not-completed" and "already exists" in got[1]
                  and got[1].startswith("bash53-001: ") and got[1].endswith("(fetched from {}; not published) "
                                                                            "(previous attempt: {}: fetch failed "
                                                                            "(ConnectionResetError: [Errno 104] reset))"
                                                                            .format(fixture_url(one[0]),
                                                                                    fixture_url(one[0])))
                  and (keep / "bash53-001").read_bytes() == b"kept" and os.listdir(keep) == ["bash53-001"]
                  and len(calls) == 2 and clock.sleeps == [2], (got, calls, clock.sleeps, os.listdir(keep)))

            # the shared deadline across the files: each attempt costs 110 s of the 300, so three files complete,
            # the fourth attempt is never started, and nothing is installed
            pins = _fixture_pins(fixtures)
            script = {fixture_url(row): [(fixtures / row[0]).read_bytes()] for row in pins}
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher(script, base / "deadline-files" / (INSTALL_NAME + "-build")
                                               / "downloads", clock, 110)
            got, _temp, _github_path, untouched = run("deadline-files", pins, fetcher, clock)
            check("deadline/no-attempt-after-deadline", got[0] == "not-completed"
                  and "deadline passed before attempt 1" in got[1] and fixture_url(pins[3]) in got[1]
                  and [call[0] for call in calls] == [fixture_url(row) for row in pins[:3]] and clock.sleeps == []
                  and untouched, (got, calls, clock.sleeps))

            # no wait that would end after the deadline: the first two files take 200 s; the third fails at 295 s
            # (2 s wait, 5 s left: taken) and again at 299 s (4 s wait, 1 s left: not taken), so it stops at once
            script[fixture_url(pins[2])] = [fetch_error(fixture_url(pins[2]),
                                                        ConnectionResetError(errno.ECONNRESET, "reset"))]
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher(script, base / "deadline-wait" / (INSTALL_NAME + "-build")
                                               / "downloads", clock, (100, 100, 95, 2))
            got, _temp, _github_path, untouched = run("deadline-wait", pins, fetcher, clock)
            check("deadline/no-wait-past-deadline", got[0] == "not-completed" and "would not end before" in got[1]
                  and got[1].endswith("download deadline (previous attempt: {}: fetch failed (ConnectionResetError: "
                                      "[Errno 104] reset))".format(fixture_url(pins[2])))
                  and len(calls) == 4 and clock.sleeps == [2] and untouched, (got, calls, clock.sleeps))

            # a wait that would end exactly at the deadline is not taken either: the one source fails transiently
            # with as many seconds left as the first wait lasts, so the build stops at once, with no wait and no
            # second attempt
            one = _fixture_pins(fixtures)[1:2]
            one_url = fixture_url(one[0])
            reset_one = fetch_error(one_url, ConnectionResetError(errno.ECONNRESET, "reset"))
            exact = base / "deadline-exact"
            os.makedirs(exact)
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher({one_url: [reset_one]}, exact, clock,
                                               DOWNLOAD_DEADLINE - RETRY_DELAYS[0])
            got = _outcome(lambda: download(one, fixture_sources, exact, fetcher, clock.sleep, clock.now,
                                            log=lambda line: None))
            check("deadline/no-wait-at-deadline", got == (
                "not-completed", "bash53-001: attempt 1 failed from every source ({}); not retried: a {} s wait would "
                                 "not end before the {} s download deadline".format(reset_one, RETRY_DELAYS[0],
                                                                                   DOWNLOAD_DEADLINE))
                  and len(calls) == 1 and clock.sleeps == [], (got, calls, clock.sleeps))

            # the stop line of an attempt that is not retried names the previous attempt's failures: the one source
            # resets the connection (retried after 2 s), then answers 404, so the build stops after the second
            # attempt
            missing_one = fetch_error(one_url, urllib.error.HTTPError(one_url, 404, "Not Found", None, None))
            previous_dir = base / "not-retried-previous"
            os.makedirs(previous_dir)
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher({one_url: [reset_one, missing_one]}, previous_dir)
            got = _outcome(lambda: download(one, fixture_sources, previous_dir, fetcher, clock.sleep, clock.now,
                                            log=lambda line: None))
            check("retry/not-retried-names-previous-attempt", got == (
                "not-completed", "bash53-001: every source failed in attempt 2 of 4 and none of the failures is "
                                 "transient, so it is not retried ({}) (previous attempt: {})".format(missing_one,
                                                                                                    reset_one))
                  and len(calls) == 2 and clock.sleeps == [2], (got, calls, clock.sleeps))

            # the CI entry: an unusable RUNNER_TEMP or GITHUB_PATH stops it before any fetch
            fetched = []

            def recording(url, out, remaining):
                fetched.append(url)
                raise NotCompleted("recorded")

            listed = base / "listed"
            listed.write_text("", encoding="utf-8")
            got = _outcome(lambda: build({"GITHUB_PATH": str(listed)}, recording))
            check("env/missing-runner-temp-exits-2", got == ("ok", 2) and not fetched, got)
            got = _outcome(lambda: build({"RUNNER_TEMP": str(base), "GITHUB_PATH": "github_path"}, recording))
            check("env/relative-github-path-exits-2", got == ("ok", 2) and not fetched, got)

            # an OSError the pipeline does not expect ends the CI entry with exit 2 and nothing installed
            def failing(url, out, remaining):
                raise PermissionError("refused by the self-test")

            os.makedirs(base / "os-error")
            os_error_err = io.StringIO()

            def os_error_build():
                with contextlib.redirect_stderr(os_error_err):
                    return build({"RUNNER_TEMP": str(base / "os-error"), "GITHUB_PATH": str(listed)}, failing,
                                 log=lambda line: None)
            got = _outcome(os_error_build)
            check("env/os-error-exits-2", got == ("ok", 2) and listed.read_text(encoding="utf-8") == ""
                  and not os.path.lexists(base / "os-error" / INSTALL_NAME)
                  and os_error_err.getvalue() == "NOT COMPLETED: bash-5.3.tar.gz: local error in attempt 1 from {} "
                                                 "(PermissionError: refused by the self-test) (not tried: {}, {}). "
                                                 "Nothing was installed.\n".format(*urls0),
                  (got, os_error_err.getvalue()))

            # the entry: with no argument or --self-test (with or without --execution-report) it runs the
            # self-test, and it builds only on the explicit --build. self_test is stubbed for these calls, so the
            # self-test never runs itself again. A bare run with RUNNER_TEMP and GITHUB_PATH set, as on a CI
            # runner, runs the self-test and never fetches, writes GITHUB_PATH or creates anything under
            # RUNNER_TEMP, so a test lane that starts this suite's runner bare never starts the build.
            # Against an entry that builds on a bare run (as this script did before --build existed), four vectors
            # fail: main/bare-runs-self-test, whose bare run reaches the build and the stubbed urlopen;
            # main/build-without-env-exits-2, since --build was then a usage error, and also through the reached
            # list it shares with the bare run and the work directory that run leaves under RUNNER_TEMP;
            # main/unknown-argument-exits-2, through that shared reached list alone; and network/never-reached.
            # Resetting reached before the unknown-argument calls would let both of those last two pass against
            # that entry, so it is not reset. main/self-test-forms-run-self-test passes either way: it is a
            # regression guard for the self-test forms.
            from unittest.mock import patch
            entered = []

            def stub_self_test(report_path=None):
                entered.append(report_path)
                return 0

            this = sys.modules[__name__]
            bare = base / "bare"
            os.makedirs(bare)
            bare_listed = base / "bare-github-path"
            bare_listed.write_text("/already/listed\n", encoding="utf-8")
            with (patch.object(this, "self_test", stub_self_test),
                  patch.dict(os.environ, {"RUNNER_TEMP": str(bare), "GITHUB_PATH": str(bare_listed)})):
                got = _entry_outcome([])
            check("main/bare-runs-self-test", got == (0, "") and entered == [None] and not reached
                  and bare_listed.read_text(encoding="utf-8") == "/already/listed\n" and not os.listdir(bare),
                  (got, entered, reached))
            report = str(base / "report.json")
            entered.clear()
            with patch.object(this, "self_test", stub_self_test):
                got = [_entry_outcome(args) for args in (["--self-test"], ["--execution-report", report],
                                                         ["--self-test", "--execution-report", report])]
            check("main/self-test-forms-run-self-test", got == [(0, "")] * 3 and entered == [None, report, report],
                  (got, entered))

            # --build reaches the CI entry, which stops before any fetch while RUNNER_TEMP or GITHUB_PATH is
            # unset; every other argument list is a usage error. Both run with the two unset, so neither builds.
            entered.clear()
            with patch.object(this, "self_test", stub_self_test), patch.dict(os.environ):
                os.environ.pop("RUNNER_TEMP", None)
                os.environ.pop("GITHUB_PATH", None)
                unset = _entry_outcome(["--build"])
                os.environ["RUNNER_TEMP"] = str(bare)
                temp_only = _entry_outcome(["--build"])
                os.environ.pop("RUNNER_TEMP")
                unknown = [_entry_outcome(args) for args in (
                    ["--bogus"], ["--build", "--self-test"], ["--self-test", "--build"],
                    ["--build", "--execution-report", report], ["--execution-report", "report.json"])]
            check("main/build-without-env-exits-2",
                  unset[0] == 2 and unset[1].startswith("error: RUNNER_TEMP must name")
                  and temp_only[0] == 2 and temp_only[1].startswith("error: GITHUB_PATH must name")
                  and not entered and not reached and not os.listdir(bare), (unset, temp_only, entered, reached))
            check("main/unknown-argument-exits-2",
                  all(code == 2 and err.startswith("usage: ") for code, err in unknown) and not entered and not reached,
                  unknown)
    finally:
        urllib.request.urlopen = real_urlopen
        urllib.request.HTTPSHandler.https_open = real_https_open
        urllib.request.HTTPHandler.http_open = real_http_open
    check("network/never-reached", not reached, reached)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))

    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: ci-build-bash {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: ci-build-bash {} checks (the pin table, one pin per file, and the three GNU sources in "
          "order; a source that fails transiently, with an HTTP status or with a refused connection moves on to the "
          "next at once and the build completes from it, a source that failed moves to the back of the order for "
          "the rest of the run, an attempt is retried only when a source in it failed transiently, every source "
          "failing exits 2 naming each, a source serving wrong bytes is refused at once with no further source "
          "asked, no source is asked or announced once the deadline has passed, a redirect is followed only to "
          "https on the same host and port (explicit port 0 or another port refused) with no userinfo (refused "
          "before the opening and as the served URL, and by redirect_request called directly, as is another host), "
          "each refusal naming the conditions failed and the whole rule, an @ in the path or query is still "
          "followed, and the fetched line names where it led, a redirect's body is never read, one after the "
          "deadline is not followed and its next hop's "
          "timeout is cut to the time left, a failure line names the hop that returned it, an unparsable Location, "
          "a ValueError while opening and an over-long head move on, a response served from another host is "
          "refused after the opening, no wait ending exactly at the deadline is taken and a stop line that is not "
          "retried names the previous attempt; a sha256 or size mismatch "
          "and a missing file "
          "refuse; a non-https URL is refused before any fetch; only BASH_VERSINFO 5 3 9 passes; over fixture "
          "files, a full build installs and appends to GITHUB_PATH, and a mismatch, a failed download, patches out "
          "of order, a wrong built or installed version, an existing install and an unexpected OSError each leave "
          "nothing installed; the named transient transport failures and the failures tied to one source move on, "
          "a local or other error stops at once, four attempts "
          "over every source with 2, 4 and 8 s waits into fresh files, exit 2 once exhausted, a body cut short of its Content-Length is "
          "retried, a malformed Content-Length is not retried, a chunked body ignores a Content-Length beside it, "
          "the read timeout reach fails closed, each body read's socket timeout is cut to the time left, a size or digest mismatch exits 1 "
          "without a retry, an existing download is never replaced, and no attempt starts and no wait is planned past the shared 300 s deadline (opening, header and chunk reads can still overrun it, as the module docstring states); a bare run is the self-test even with RUNNER_TEMP and GITHUB_PATH set, only --build "
          "reaches the build, which exits 2 without them, and any other argument exits 2; no fetch reached the "
          "network)".format(len(results)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args in ([], ["--self-test"]):
        return self_test()
    if args == ["--build"]:
        return build(os.environ)
    print("usage: ci_build_bash.py [--self-test] [--execution-report ABS_PATH] | ci_build_bash.py --build",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
