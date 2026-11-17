#!/usr/bin/env python3
"""Offline gate for the pinned copy of the shared site template (baseline package).

  check_baseline_pin.py                    exit 0 clean, 1 finding, 2 cannot evaluate
  check_baseline_pin.py --base REF         the same, plus the base-coupling leg against the commit REF names
  check_baseline_pin.py --local-base REF   the same, plus that leg against the fork point of HEAD and the full
                                           ref name REF (refs/remotes/origin/main); a REF the clone lacks or a
                                           short name is cannot-evaluate
  check_baseline_pin.py --probe-local-base REF
                                           the local runner's probe: the --local-base reads of REF alone, exit 0
                                           when they succeed, 1 (nothing written) when the clone genuinely lacks
                                           REF and its ref store is shown readable and well formed, 2 (a CANNOT
                                           EVALUATE line on stderr) for anything else
  check_baseline_pin.py --self-test        assert every refusal and a pass over tempdir fixtures
  check_baseline_pin.py [--self-test] --execution-report ABS_PATH

Each pin records, in the package README's format, the baseline version (`version = "X.Y.Z"`) and a [files]
table from each copied file's repository path to its SHA-256. Every path in a pin lies under one copy root, a
directory whose last 2 components are assets/baseline. PINS registers every pin with the 1 copy root it is
bound to (.baseline/pin.toml for site/draft/assets/baseline, .baseline/opfiles-pin.toml for
opf/site/draft/assets/baseline while staging); a pin that names any other root, and a registry that repeats
a pin or a root, are cannot-evaluate, so 2 pins can never validate the same copy and leave the other one
unchecked. For each registered pair the gate hashes every pinned file under the copy root and reports a
file whose bytes differ, a
pinned file that is missing, a file under a copy root that the pin does not list (a stray), a
symbolic link or other non-regular entry anywhere in a copy root, including the root itself, and a parent
folder of a copy root (such as site/draft) that is a symbolic link or not a directory (exit 1).

The one exception to the copy-root rule is BUILD_TOOLS: the template's build tools that a site copies outside
its served tree and pins in the same [files] table (only .baseline/check_palette.py, the palette gate). A
pin may list such a path at its own repository path, beside its copy-root files; the gate hashes it as 1
regular file (only its last component is checked for a symbolic link; a linked parent directory is
followed), and reports it missing, a symbolic link, not a regular file
or differing (exit 1), or unreadable (exit 2). Its directory gets no stray scan, since .baseline/ also holds
the pins. Any other path outside a copy root is still cannot-evaluate, and a pin must list at least 1
copy-root file. A pin that is missing, unreadable, over MAX_PIN_BYTES, not UTF-8, not TOML or not of the pinned shape, and
a copied file that cannot be read, are cannot-evaluate (exit 2), never a pass.

Every registered pin must name the same version, and that version must equal the site template version
recorded in .aiqt/attribution.toml, table [vendored."site-template"], key version: the single in-tree
source tools/gen_notice.py renders the root NOTICE from (the NOTICE is generated output, so it is not
read). The same table's homepage, the source archive URL, must end in /baseline-<version>.tar.gz for that
recorded version, so the archive cannot name another version. The file is read under the scanned root. An
attribution file that is missing, unreadable, not a regular file, over MAX_ATTRIBUTION_BYTES, not UTF-8 or
not TOML, a missing [vendored."site-template"] table, a version there that is missing or not an X.Y.Z
string, a homepage there that is missing, not a string or not ending that way, a pin whose version
differs from the recorded version, and 2 pins that name different versions are cannot-evaluate (exit 2).
The gate reads only the repository; it never touches the network.

The base-coupling leg (only with --base REF or --local-base REF): a pin in the tree cannot certify itself, since
the change that edits a copied file can edit its digest row too. The anchor that change cannot rewrite is each
registered pin as committed at the base. With --base REF the base is exactly the commit REF names, which must
be an ancestor of HEAD (git merge-base --is-ancestor); a REF that is not, such as the before SHA of a force or
otherwise divergent push, is cannot-evaluate, never replaced by a common ancestor. With --local-base REF (the
local runner's form) the base is the fork point of HEAD and the commit REF points at (git merge-base, the form of
check_version_monotonicity.py's default base, which reads the short name origin/main instead, so where a tag or a
local branch shadows that short name the two bases can differ). REF must be a full ref name (refs/remotes/origin/main):
git resolves a short name such as origin/main through a tag or a local branch of that name before
refs/remotes/, so the base could be the change itself, and a short name is cannot-evaluate. The name is read
once (git rev-parse --verify --quiet REF, without a peel), git for-each-ref over every ref must list exactly that
name (git otherwise takes a full name it lacks through refs/heads/REF), and the peel and the merge-base read the
commit id. A REF the clone lacks is cannot-evaluate, with the fetch that provides it; so are any git diagnostic
on either read (a broken loose ref, a broken packed-refs line, a dangling symbolic ref), a ref store that cannot be
shown readable and well formed when REF reads as absent (see --probe-local-base below), a REF that exists but
does not resolve to a readable commit (its object is missing, or it names a blob or a tree) and any other git
failure. The leg compares the base and HEAD endpoints only: a row
changed in one commit of a range and restored by a later one is not seen. The leg reads each pin at the base (git
ls-tree and cat-file, with replacement objects off and every GIT_ variable dropped but the config-isolation
pins) and compares the parsed tables, not the text, so a comment edit or a reordering is ignored. A pin whose
version differs from its base version has moved, so its rows may change (a NOTE says the hand comparison
against the published SHA256SUMS is due). Under an unchanged version, every row in both tables, keyed by its
path within the copy root or its build-tool path, must keep its base digest; a changed digest is a finding
(exit 1). A removed row passes, and an added row passes with a NOTE. A pin absent at the base is compared,
path by path, with each registered pin present at the base under the same version. The pin records no
archive digest, so the version is the only pin key: a change that also moves the version passes. A REF that
is empty, starts with "-" or holds ":", whitespace or a control character, a --base REF that does not resolve
to a commit or is not an ancestor of HEAD, a --local-base REF that is a short name, that the clone lacks or that
git reports broken or dangling, a --local-base
REF that exists but does not resolve to a readable commit, a --local-base merge-base that cannot be resolved, a base pin that is not a regular file, and a base
pin that is oversized or malformed are cannot-evaluate (exit 2), never a pass. So is a --local-base fork point whose
tree is HEAD's own (REF names HEAD, a commit HEAD descends from with no change, or a commit that descends from HEAD,
as a symbolic ref to the checked-out branch does), since the leg would then compare nothing, and, with --base, any
exception the run raises other than a cannot-evaluate one (named on the CANNOT EVALUATE line), never Python's exit 1.
With neither flag the leg is not evaluated, and the run says so on a NOTE line.

--probe-local-base REF runs only those --local-base reads and scans nothing, so tools/run_all_checks.sh decides
whether to pass --local-base by this gate's own reading rather than a second one: the probe and --local-base both
take their answer from 1 function (local_base_decision, under the same scrubbed git environment), so they cannot
disagree. It exits 0, writing nothing, when REF resolves to a usable fork point, and 1, writing nothing, only when
the clone genuinely lacks REF: the name read is a clean miss (exit 1, no output, nothing on stderr), git
for-each-ref lists every ref cleanly without it, and the ref store is shown readable and well formed (git reads
some broken stores, such as a packed-refs that is a directory, as a clean miss). For that, git must report the
files ref store (a reftable store, or a git that reports neither, is refused); in the common git directory,
packed-refs must be absent or a regular, readable file of well-formed lines (an optional "# pack-refs with:"
header first, then "<object id> <ref name>" lines, each optionally followed by a "^<object id>" peel line, every
line ending in LF); refs must be a readable directory, not a symbolic link; each directory on REF's loose path
that exists must be a readable directory, not a symbolic link; and nothing may exist at REF's loose path itself.
A per-worktree name (under refs/bisect/, refs/worktree/ or refs/rewritten/) that reads as absent is never read as
absence (exit 2); one that resolves is read like any other name. Anything else exits 2
with one CANNOT EVALUATE line on stderr: an unsafe or short name, a broken loose ref, a broken packed-refs line, a
dangling symbolic ref, a ref store not shown readable and well formed, a full name git resolves only through
another ref (a local branch refs/heads/REF), an object that is missing or not a commit, a merge-base that does not
resolve, a fork point whose tree is HEAD's own, and any other git failure.

The entry line runs an inline copy of the canonical child-status guard (opf/tools/_child_status.py) before
main(), since the leg reads git's exit statuses.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_baseline_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import hashlib
import io
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Every pin and the 1 copy root it is bound to, in check order; both columns are unique.
PINS = (
    (".baseline/pin.toml", "site/draft/assets/baseline"),
    (".baseline/opfiles-pin.toml", "opf/site/draft/assets/baseline"),
)
PIN_REL = PINS[0][0]
MAX_PIN_BYTES = 1 << 20
CHUNK = 1 << 16
VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
# A copy root or a file path: relative, "/"-separated, no empty, "." or ".." component.
PART_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
TOP_KEYS = {"version", "files"}
# The copy root's own last components: a pinned path is <prefix>assets/baseline/<file>, and its copy root is
# <prefix>assets/baseline.
COPY_DIR = "assets/baseline"
# The build tools a pin may list outside any copy root, each at exactly this repository path: the template's
# palette gate, copied from the package's tools/, never served, and run by tools/run_all_checks.sh and the
# Quality workflow (template specification section 4 item 3 also requires it in the Pages build command,
# which is set outside this repository).
BUILD_TOOLS = (".baseline/check_palette.py",)
# The in-tree record of the template version every pin must name: the [vendored."site-template"] table of
# the attribution file tools/gen_notice.py renders the root NOTICE from, read under the scanned root.
ATTRIBUTION_REL = ".aiqt/attribution.toml"
ATTRIBUTION_BLOCK = "site-template"
ATTRIBUTION_WHERE = '{} [vendored."{}"]'.format(ATTRIBUTION_REL, ATTRIBUTION_BLOCK)
MAX_ATTRIBUTION_BYTES = 1 << 20
# The base-coupling leg: a bound on each git read, the shape of a commit id git prints, and the longest REF.
GIT_TIMEOUT = 60
COMMIT_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
MAX_BASE_REF = 256
# The files ref store's layout, checked before a clean miss is read as absence (_ref_store_problem): the
# per-worktree name prefixes (stored outside the common git directory, so never classified), and the packed-refs
# line shapes (an optional header first, then "<object id> <ref name>" lines, each optionally followed by 1
# "^<object id>" peel line).
PER_WORKTREE_PREFIXES = ("refs/bisect/", "refs/worktree/", "refs/rewritten/")
PACKED_HEADER = b"# pack-refs with:"
PACKED_REF_RE = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64}) [\x21-\x7e\x80-\xff]+")
PACKED_PEEL_RE = re.compile(rb"\^(?:[0-9a-f]{40}|[0-9a-f]{64})")


class CannotEvaluate(Exception):
    """An input that cannot be judged: a missing, unreadable, oversized or malformed pin or attribution file,
    or an unreadable file."""


class RefAbsent(CannotEvaluate):
    """A --local-base full ref name this clone genuinely lacks: its name read was a clean miss (exit 1, no output,
    nothing on stderr), git for-each-ref listed every ref cleanly without it, and the ref store was shown readable
    and well formed (_ref_store_problem). Still cannot-evaluate for the gate; only --probe-local-base reads it as
    absence (exit 1)."""


def _safe_rel(value):
    return (isinstance(value, str) and value != ""
            and all(PART_RE.fullmatch(part) and part not in (".", "..") for part in value.split("/")))


def _read_pin_bytes(path):
    """The pin's bytes, at most MAX_PIN_BYTES + 1 of them (so an oversized pin is seen, never read whole)."""
    with open(path, "rb") as handle:
        return handle.read(MAX_PIN_BYTES + 1)


def parse_pin(data, where):
    """The pin as (version, [(root, {path: sha256})], {build tool path: sha256}); CannotEvaluate on any
    malformed input."""
    if len(data) > MAX_PIN_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_PIN_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    doc, problem = None, None
    try:
        doc = tomllib.loads(text)
    except (RecursionError, MemoryError, ValueError) as exc:
        problem = "{}: not valid TOML ({}: {})".format(where, type(exc).__name__, exc)
    if problem is not None:
        raise CannotEvaluate(problem)
    unknown = sorted(set(doc) - TOP_KEYS)
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(where, ", ".join(unknown)))
    version = doc.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise CannotEvaluate("{}: version must be an X.Y.Z string".format(where))
    files = doc.get("files")
    if not isinstance(files, dict) or not files:
        raise CannotEvaluate("{}: a non-empty [files] table is required".format(where))
    root, pinned, tools = None, {}, {}
    for path, digest in files.items():
        if path in BUILD_TOOLS:
            if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
                raise CannotEvaluate("{}: {}: the SHA-256 must be 64 lowercase hex digits".format(where, path))
            tools[path] = digest
            continue
        if not _safe_rel(path):
            raise CannotEvaluate("{}: unsafe file path {!r}".format(where, path))
        head, sep, rel = path.partition(COPY_DIR + "/")
        if not sep or not rel or (head and not head.endswith("/")):
            raise CannotEvaluate("{}: {} is not under an {} copy root".format(where, path, COPY_DIR))
        if root is None:
            root = head + COPY_DIR
        elif head + COPY_DIR != root:
            raise CannotEvaluate("{}: {} is under a second copy root (the first is {})".format(where, path, root))
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise CannotEvaluate("{}: {}: the SHA-256 must be 64 lowercase hex digits".format(where, path))
        pinned[rel] = digest
    if root is None:
        raise CannotEvaluate("{}: no file under an {} copy root".format(where, COPY_DIR))
    return version, [(root, pinned)], tools


def _read_attribution_bytes(path):
    """The attribution file's bytes, at most MAX_ATTRIBUTION_BYTES + 1 of them."""
    with open(path, "rb") as handle:
        return handle.read(MAX_ATTRIBUTION_BYTES + 1)


def parse_attribution(data, where=ATTRIBUTION_REL):
    """The site template version the attribution file records; CannotEvaluate on any malformed input."""
    if len(data) > MAX_ATTRIBUTION_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_ATTRIBUTION_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    doc, problem = None, None
    try:
        doc = tomllib.loads(text)
    except (RecursionError, MemoryError, ValueError) as exc:
        problem = "{}: not valid TOML ({}: {})".format(where, type(exc).__name__, exc)
    if problem is not None:
        raise CannotEvaluate(problem)
    vendored = doc.get("vendored")
    block = vendored.get(ATTRIBUTION_BLOCK) if isinstance(vendored, dict) else None
    if not isinstance(block, dict):
        raise CannotEvaluate('{}: no [vendored."{}"] table, so the version every pin must name is '
                             'unknown'.format(where, ATTRIBUTION_BLOCK))
    version = block.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise CannotEvaluate('{}: [vendored."{}"] version must be an X.Y.Z string'.format(
            where, ATTRIBUTION_BLOCK))
    # The source archive must be the recorded version's own, so the version and the archive cannot drift apart.
    homepage = block.get("homepage")
    ending = "/baseline-{}.tar.gz".format(version)
    if not isinstance(homepage, str) or not homepage.endswith(ending):
        raise CannotEvaluate('{}: [vendored."{}"] homepage must end in {} for version {}, but it is {}'.format(
            where, ATTRIBUTION_BLOCK, ending, version, "missing" if homepage is None else repr(homepage)))
    return version


def _attribution_version(root):
    """(version, None) read from the attribution file under root, or (None, the cannot-evaluate line)."""
    path = Path(root) / ATTRIBUTION_REL
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise CannotEvaluate("{}: not a regular file".format(ATTRIBUTION_REL))
        return parse_attribution(_read_attribution_bytes(path)), None
    except FileNotFoundError:
        return None, "{}: missing".format(ATTRIBUTION_REL)
    except OSError as exc:
        return None, "{}: unreadable ({})".format(ATTRIBUTION_REL, exc)
    except CannotEvaluate as exc:
        return None, str(exc)


def _version_errors(root, versions):
    """Cannot-evaluate lines for pins that name different versions, or a version other than the one the
    attribution file records; versions is [(pin_rel, version)] for every pin that parsed."""
    expected, problem = _attribution_version(root)
    if problem is not None:
        return [problem]
    errors = []
    if versions:
        first_rel, first_version = versions[0]
        for pin_rel, version in versions[1:]:
            if version != first_version:
                errors.append("the pins disagree: {} names version {} but {} names version {}; every "
                              "registered pin must name the same version".format(
                                  first_rel, first_version, pin_rel, version))
    for pin_rel, version in versions:
        if version != expected:
            errors.append("{}: version {} differs from the site template version {} recorded in {}".format(
                pin_rel, version, expected, ATTRIBUTION_WHERE))
    return errors


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(base, findings, errors, where):
    """Every regular file under base as a sorted list of relative paths; a symlink or other non-regular
    entry is a finding and is never followed."""
    found = []
    pending = [""]
    while pending:
        rel_dir = pending.pop()
        try:
            with os.scandir(os.path.join(base, rel_dir) if rel_dir else base) as entries:
                listing = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            errors.append("{}/{}: cannot list ({})".format(where, rel_dir, exc))
            continue
        for entry in listing:
            rel = entry.name if not rel_dir else rel_dir + "/" + entry.name
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                errors.append("{}/{}: cannot stat ({})".format(where, rel, exc))
                continue
            if stat.S_ISLNK(mode):
                findings.append("{}/{}: is a symbolic link".format(where, rel))
            elif stat.S_ISDIR(mode):
                pending.append(rel)
            elif stat.S_ISREG(mode):
                found.append(rel)
            else:
                findings.append("{}/{}: not a regular file".format(where, rel))
    return sorted(found)


def _registry_errors(pins):
    """Cannot-evaluate lines for a registry that is empty or repeats a pin or a copy root."""
    if not pins:
        return ["the pin registry is empty"]
    errors = []
    for column, what in ((0, "pin"), (1, "copy root")):
        values = [pair[column] for pair in pins]
        for value in sorted({value for value in values if values.count(value) > 1}):
            errors.append("the pin registry lists the {} {} more than once".format(what, value))
    return errors


def scan(root, pins=PINS):
    """(findings, errors) for every registered pin under root; errors make the run cannot-evaluate."""
    findings, errors = [], []
    problems = _registry_errors(pins)
    if problems:
        return findings, problems
    versions = []
    for pin_rel, assigned in pins:
        _scan_pin(root, pin_rel, assigned, findings, errors, versions)
    errors.extend(_version_errors(root, versions))
    return findings, errors


def _scan_pin(root, pin_rel, assigned, findings, errors, versions):
    """Check 1 pin against its assigned copy root, appending to findings and errors, and (pin_rel, version)
    to versions once the pin parses."""
    pin_path = Path(root) / pin_rel
    try:
        mode = os.lstat(pin_path).st_mode
        if not stat.S_ISREG(mode):
            raise CannotEvaluate("{}: not a regular file".format(pin_rel))
        data = _read_pin_bytes(pin_path)
        version, roots, tools = parse_pin(data, pin_rel)
        versions.append((pin_rel, version))
    except FileNotFoundError:
        errors.append("{}: missing".format(pin_rel))
        return
    except OSError as exc:
        errors.append("{}: unreadable ({})".format(pin_rel, exc))
        return
    except CannotEvaluate as exc:
        errors.append(str(exc))
        return
    for copy_root, _pinned in roots:
        if copy_root != assigned:
            errors.append("{}: names the copy root {}, but its registered root is {}".format(
                pin_rel, copy_root, assigned))
            return
    for copy_root, pinned in roots:
        base = Path(root) / copy_root
        # Every folder on the way to the copy root, then the root itself, each checked with os.lstat from the
        # top down: a linked or non-directory parent folder (site/draft as a link, say) would otherwise be
        # followed to a copy outside the tree. The first such component is reported and the root skipped.
        parts = copy_root.split("/")
        linked = None
        try:
            for depth in range(1, len(parts) + 1):
                mode = os.lstat(Path(root).joinpath(*parts[:depth])).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    linked = "/".join(parts[:depth])
                    break
        except FileNotFoundError:
            findings.extend("{}/{}: missing".format(copy_root, rel) for rel in sorted(pinned))
            continue
        except OSError as exc:
            errors.append("{}: cannot stat ({})".format(copy_root, exc))
            continue
        if linked == copy_root:
            findings.append("{}: copy root is a symbolic link or not a directory".format(copy_root))
            continue
        if linked is not None:
            findings.append("{}: parent folder {} is a symbolic link or not a directory".format(
                copy_root, linked))
            continue
        present = _inventory(base, findings, errors, copy_root)
        for rel in sorted(set(present) - set(pinned)):
            findings.append("{}/{}: stray file, not listed in {}".format(copy_root, rel, pin_rel))
        for rel in sorted(pinned):
            if rel not in present:
                if not any(line.startswith("{}/{}:".format(copy_root, rel)) for line in findings):
                    findings.append("{}/{}: missing".format(copy_root, rel))
                continue
            try:
                got = _sha256(base / rel)
            except OSError as exc:
                errors.append("{}/{}: unreadable ({})".format(copy_root, rel, exc))
                continue
            if got != pinned[rel]:
                findings.append("{}/{}: SHA-256 {} differs from the pinned {}".format(
                    copy_root, rel, got, pinned[rel]))
    for rel in sorted(tools):
        _check_tool(root, rel, tools[rel], findings, errors)


def _check_tool(root, rel, digest, findings, errors):
    """Check 1 pinned build tool: a regular file at its own path whose bytes match the pin. Only the last
    path component is checked for a symbolic link (os.lstat); a symbolic link among its parent directories,
    such as .baseline/ itself, is followed, and the file is then opened by path."""
    path = Path(root) / rel
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        findings.append("{}: missing".format(rel))
        return
    except OSError as exc:
        errors.append("{}: cannot stat ({})".format(rel, exc))
        return
    if stat.S_ISLNK(mode):
        findings.append("{}: is a symbolic link".format(rel))
        return
    if not stat.S_ISREG(mode):
        findings.append("{}: not a regular file".format(rel))
        return
    try:
        got = _sha256(path)
    except OSError as exc:
        errors.append("{}: unreadable ({})".format(rel, exc))
        return
    if got != digest:
        findings.append("{}: SHA-256 {} differs from the pinned {}".format(rel, got, digest))


def _git_env():
    """The process environment with every GIT_-prefixed variable dropped and only the config-isolation pins
    re-applied at the exact values the fixture scrub sets (the rule of git_authority_env() in
    tools/check_mistakes_register.py), so no inherited variable can point a base read at another repository."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if os.environ.get("GIT_CONFIG_NOSYSTEM") == "1":
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    for pin in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        if os.environ.get(pin) == os.devnull:
            env[pin] = os.devnull
    return env


def _git_stdout(proc, what):
    """A finished git read's standard output as bytes; CannotEvaluate on a nonzero exit."""
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CannotEvaluate("base: {} failed (exit {}{})".format(
            what, proc.returncode, ": " + detail[-1] if detail else ""))
    return proc.stdout


def _check_ref(base):
    """CannotEvaluate when base is not a usable REF."""
    if (not isinstance(base, str) or not base or len(base) > MAX_BASE_REF or base.startswith("-")
            or ":" in base or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in base)):
        raise CannotEvaluate("base: {!r} is not a usable REF (empty, over {} characters, starting with -, or "
                             "holding :, whitespace or a control character)".format(base, MAX_BASE_REF))


def _git_base_read(root, args):
    """A finished base read `git <args>` in root; CannotEvaluate when git could not run. The three `-c` pairs in
    option position switch off automatic gc and maintenance (tools/_git_fixture_env.py _NO_AUTO_MAINTENANCE, the
    posture of _staged_git in tools/check_no_dashes.py), so the maintenance-pin scan reads this launch as pinned
    whatever subcommand args carries."""
    try:
        return subprocess.run(["git", "--no-replace-objects", "-C", str(root), "-c", "gc.auto=0",
                               "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + args,
                              capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CannotEvaluate("base: git {} could not run ({})".format(" ".join(args), exc)) from None


def _commit_id(proc, what):
    """The 1 commit id a finished git read printed; CannotEvaluate on a nonzero exit or any other output."""
    text = _git_stdout(proc, what).decode("ascii", "replace")
    commit = text[:-1] if text.endswith("\n") else text
    if not COMMIT_RE.fullmatch(commit):
        raise CannotEvaluate("base: {} printed {!r}, not 1 commit id".format(what, text))
    return commit


def _base_commit(root, base):
    """The exact commit base names, which must be an ancestor of HEAD; CannotEvaluate when base is unsafe, does
    not resolve to a commit or is not an ancestor of HEAD (never replaced by a common ancestor)."""
    _check_ref(base)
    proc = _git_base_read(root, ["rev-parse", "--verify", "--quiet", base + "^{commit}"])
    if proc.returncode != 0:
        raise CannotEvaluate("base: {} does not resolve to a commit (git rev-parse --verify exit {})".format(
            base, proc.returncode))
    commit = _commit_id(proc, "git rev-parse --verify " + base)
    proc = _git_base_read(root, ["merge-base", "--is-ancestor", commit, "HEAD"])
    if proc.returncode == 1:
        raise CannotEvaluate("base: {} ({}) is not an ancestor of HEAD, as after a force or otherwise divergent "
                             "push, so no exact base names the state this change starts from; the leg compares "
                             "only against a commit HEAD descends from".format(base, commit))
    _git_stdout(proc, "git merge-base --is-ancestor {} HEAD".format(commit))
    return commit


def _last_line(data):
    """The last nonblank line of a git diagnostic, or "" when there is none."""
    lines = data.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else ""


def _fetch_hint(ref):
    """How to get a full ref name this clone lacks: for a remote-tracking name, the fetch whose explicit destination
    refspec creates exactly that ref (a bare git fetch origin main updates only FETCH_HEAD when the clone's fetch
    refspec does not cover main, as in a single-branch clone of another branch), else a generic line."""
    parts = ref.split("/", 3)
    if len(parts) == 4 and parts[:2] == ["refs", "remotes"] and parts[2] and parts[3]:
        return "fetch it with `git fetch {} {}:{}`".format(parts[2], parts[3], ref)
    return "create or fetch that ref"


def _git_line(root, args):
    """The 1 line a clean base read `git <args>` printed (exit 0, nothing on stderr, exactly 1 LF-ended line), without
    its LF, or None for any other result."""
    proc = _git_base_read(root, args)
    if proc.returncode != 0 or proc.stderr or proc.stdout.count(b"\n") != 1 or not proc.stdout.endswith(b"\n"):
        return None
    return proc.stdout[:-1]


def _packed_refs_problem(path):
    """None when packed-refs at path is absent or a regular, readable file of well-formed lines; otherwise why not.
    git reads a packed-refs it cannot open (a directory, an unreadable file) as holding no ref, silently."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "{} could not be examined ({})".format(path, exc)
    if not stat.S_ISREG(info.st_mode):
        return "{} is not a regular file (a directory, a symbolic link or another entry)".format(path)
    try:
        with open(path, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return "{} is not a regular file".format(path)
            data = handle.read()
    except OSError as exc:
        return "{} is unreadable ({})".format(path, exc)
    if data and not data.endswith(b"\n"):
        return "{} does not end in a line feed".format(path)
    after_ref = False
    for number, line in enumerate(data.split(b"\n")[:-1], 1):
        if number == 1 and line.startswith(PACKED_HEADER):
            continue
        if PACKED_REF_RE.fullmatch(line):
            after_ref = True
            continue
        if after_ref and PACKED_PEEL_RE.fullmatch(line):
            after_ref = False
            continue
        return "{} line {} is not a well-formed packed-refs line ({!r})".format(path, number, line[:80])
    return None


def _ref_store_problem(root, ref):
    """None when this clone's ref store is shown readable and well formed for ref, a full name git reads as a clean
    miss; otherwise why not, so the miss is not read as absence. git must report the files ref store (a reftable
    store, or a git that reports neither, is refused); in the common git directory, packed-refs must be absent or
    a regular, readable file of well-formed lines, refs a readable directory (not a symbolic link), each directory
    on ref's loose path that exists a readable directory (not a symbolic link), and nothing may exist at ref's loose
    path itself. A per-worktree name is refused, since it is stored outside the common git directory (this runs only
    for a name git reads as a clean miss; a per-worktree name that resolves never reaches it)."""
    if ref.startswith(PER_WORKTREE_PREFIXES):
        return "{} is a per-worktree ref name, whose absence this gate does not classify".format(ref)
    form = _git_line(root, ["rev-parse", "--show-ref-format"])
    if form == b"--show-ref-format":
        # A git older than the option echoes it, and such a git refuses a repository whose config names another
        # ref store, so the store is files when that config key is cleanly unset.
        config = _git_base_read(root, ["config", "--get", "extensions.refStorage"])
        if config.returncode == 1 and not config.stdout and not config.stderr:
            form = b"files"
    if form != b"files":
        return ("git does not report the files ref store (git rev-parse --show-ref-format printed {}); only that "
                "store's layout is checked, so a reftable or unknown ref store is refused".format(
                    "no single line" if form is None else repr(form.decode("utf-8", "replace"))))
    common = _git_line(root, ["rev-parse", "--git-common-dir"])
    if not common:
        return "git rev-parse --git-common-dir did not print 1 path"
    common = Path(root) / os.fsdecode(common)
    problem = _packed_refs_problem(common / "packed-refs")
    if problem is not None:
        return problem
    path = common
    parts = ref.split("/")
    for index, part in enumerate(parts):
        path = path / part
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return "{} does not exist".format(path) if index == 0 else None
        except OSError as exc:
            return "{} could not be examined ({})".format(path, exc)
        if index == len(parts) - 1:
            return "{} exists (mode {:o}), yet git reads {} as absent".format(path, info.st_mode, ref)
        if not stat.S_ISDIR(info.st_mode):
            return "{} is not a directory (a symbolic link, a file or another entry)".format(path)
        try:
            os.listdir(path)
        except OSError as exc:
            return "{} is not a readable directory ({})".format(path, exc)
    return None


def _local_base_commit(root, ref):
    """The fork point `git merge-base HEAD <commit>` (the form of check_version_monotonicity.py's default base, which
    reads the shadowable short name origin/main instead), where <commit> is the commit the full ref name ref points
    at. ref must be a full ref name (refs/remotes/origin/main, not origin/main): git resolves a short name through
    a tag or a local branch of that name before refs/remotes/, so the base could be the change itself. The name is read once (git rev-parse --verify --quiet
    ref, without a peel), git for-each-ref over every ref must list exactly that name (git otherwise takes a full
    name it lacks through refs/heads/<ref>), and the peel and the merge-base read the commit id, never the name
    again. CannotEvaluate when ref is unsafe or not a full ref name, when ref does not exist in this clone (with
    how to fetch it), when either read reports any diagnostic (a broken loose ref, a dangling symbolic ref, or a
    broken packed-refs line, which git may skip silently in the name read), when ref exists but does not resolve
    to a readable commit (its object is missing, or it names a blob or a tree), or when any git read fails
    otherwise. The name is read first because the peeled read exits 1 with no output for a missing object too. The
    absent ref is RefAbsent only when the name read was a clean miss (exit 1, no output, nothing on stderr) and the
    ref store is shown readable and well formed (_ref_store_problem; otherwise it is a plain CannotEvaluate naming
    the store's problem); a full name git resolved through another ref (refs/heads/<ref>) gives the absence message
    as a plain CannotEvaluate."""
    _check_ref(ref)
    if not ref.startswith("refs/"):
        raise CannotEvaluate("base: --local-base {} is not a full ref name; git resolves a short name through a tag "
                             "or a local branch of that name before refs/remotes/, so it can name the change itself. "
                             "Pass the full name, such as refs/remotes/{}".format(ref, ref))
    proc = _git_base_read(root, ["rev-parse", "--verify", "--quiet", ref])
    clean_miss = proc.returncode == 1 and not proc.stdout and not proc.stderr
    if proc.returncode == 1 and not proc.stdout:
        if proc.stderr:
            raise CannotEvaluate("base: {} does not resolve, and git rev-parse --verify {} reported a diagnostic, so "
                                 "it cannot be read as absent (a broken or dangling ref): {}".format(
                                     ref, ref, _last_line(proc.stderr)))
    refs = _git_base_read(root, ["for-each-ref", "--format=%(refname)"])
    if refs.returncode != 0 or refs.stderr:
        raise CannotEvaluate("base: {} does not resolve, and git for-each-ref over this clone's refs exited {} "
                             "with a diagnostic, so it cannot be read as absent (a broken ref): {}".format(
                                 ref, refs.returncode, _last_line(refs.stderr)))
    if ref.encode("utf-8") not in refs.stdout.split(b"\n"):
        absent = ("base: {} does not exist in this clone, so the base-coupling leg cannot run and a digest "
                  "row changed under an unchanged version would not be refused; {} and rerun, or run "
                  "without --local-base for the check without that leg".format(ref, _fetch_hint(ref)))
        if not clean_miss:
            raise CannotEvaluate(absent)
        problem = _ref_store_problem(root, ref)
        if problem is not None:
            raise CannotEvaluate("base: {} does not resolve, and this clone's ref store could not be shown readable "
                                 "and well formed, so it cannot be read as absent: {}".format(ref, problem))
        raise RefAbsent(absent)
    named = _commit_id(proc, "git rev-parse --verify " + ref)
    proc = _git_base_read(root, ["rev-parse", "--verify", "--quiet", named + "^{commit}"])
    if proc.returncode != 0:
        raise CannotEvaluate("base: {} exists in this clone but does not resolve to a readable commit (its object "
                             "{} is missing or is not a commit; git rev-parse --verify {}^{{commit}} exit {})".format(
                                 ref, named, named, proc.returncode))
    commit = _commit_id(proc, "git rev-parse --verify " + named + "^{commit}")
    fork = _commit_id(_git_base_read(root, ["merge-base", "HEAD", commit]), "git merge-base HEAD " + commit)
    if _tree_id(root, fork) == _tree_id(root, "HEAD"):
        raise CannotEvaluate("base: the fork point {} of HEAD and {} holds HEAD's own tree ({} names HEAD, a commit "
                             "HEAD descends from with no change, or a commit that descends from HEAD, as a symbolic "
                             "ref to the checked-out branch does), so the leg would compare nothing and pass; point {} "
                             "at the protected line's commit and rerun, or run without --local-base for the check "
                             "without that leg".format(fork, ref, ref, ref))
    return fork


def _tree_id(root, rev):
    """The id of the tree the commit rev names; CannotEvaluate on a nonzero exit or any other output."""
    what = "git rev-parse --verify {}^{{tree}}".format(rev)
    text = _git_stdout(_git_base_read(root, ["rev-parse", "--verify", "--quiet", rev + "^{tree}"]), what).decode(
        "ascii", "replace")
    tree = text[:-1] if text.endswith("\n") else text
    if not COMMIT_RE.fullmatch(tree):
        raise CannotEvaluate("base: {} printed {!r}, not 1 tree id".format(what, text))
    return tree


def local_base_decision(root, ref):
    """The one decision both --local-base (run_gate) and --probe-local-base take for ref over root, so the two
    cannot disagree: ("usable", the fork point commit) when the --local-base reads (_local_base_commit, under the
    _git_env scrub) succeed; ("absent", the message) only for RefAbsent, a clone that genuinely lacks ref with its
    ref store shown readable and well formed; ("cannot", the message) for anything else, an unexpected exception
    included."""
    try:
        return "usable", _local_base_commit(root, ref)
    except RefAbsent as exc:
        return "absent", str(exc)
    except CannotEvaluate as exc:
        return "cannot", str(exc)
    except Exception as exc:
        return "cannot", "base: the --local-base read of {!r} failed unexpectedly ({}: {})".format(
            ref, type(exc).__name__, exc)


def probe_local_base(root, ref):
    """--probe-local-base: the exit code of local_base_decision for ref over root, so the local runner passes
    --local-base by this gate's own decision. 0 when ref resolves to a usable fork point; 1 only when this clone
    genuinely lacks ref and its ref store is shown readable and well formed (RefAbsent); 2, with the CANNOT EVALUATE
    line --local-base reports, for anything else. Nothing is written on stdout, and nothing on stderr for 0 or 1."""
    state, value = local_base_decision(root, ref)
    if state == "usable":
        return 0
    if state == "absent":
        return 1
    print("CANNOT EVALUATE: " + value, file=sys.stderr)
    return 2


def _base_pin(root, commit, pin_rel):
    """The pin at pin_rel as committed at commit, parsed, or None when that commit holds no such path;
    CannotEvaluate when the entry is not a regular file, or is unreadable, oversized or malformed."""
    try:
        proc = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "ls-tree", "-l", "-z", commit,
                               "--", pin_rel], capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CannotEvaluate("base: git ls-tree {} -- {} could not run ({})".format(commit, pin_rel, exc)) from None
    listing = _git_stdout(proc, "git ls-tree {} -- {}".format(commit, pin_rel))
    entries = [entry for entry in listing.split(b"\0") if entry]
    if not entries:
        return None
    where = "{} at the base {}".format(pin_rel, commit)
    head, tab, name = entries[0].partition(b"\t")
    fields = head.split()
    if len(entries) != 1 or not tab or name != pin_rel.encode("utf-8") or len(fields) != 4:
        raise CannotEvaluate("{}: unexpected git ls-tree output {!r}".format(where, listing))
    mode, kind, oid, size = fields
    if mode not in (b"100644", b"100755") or kind != b"blob":
        raise CannotEvaluate("{}: not a regular file (mode {})".format(where, mode.decode("ascii", "replace")))
    if not size.isdigit() or int(size) > MAX_PIN_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_PIN_BYTES))
    try:
        proc = subprocess.run(["git", "--no-replace-objects", "-C", str(root), "cat-file", "blob",
                               oid.decode("ascii")],
                              capture_output=True, env=_git_env(), timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{}: git cat-file could not run ({})".format(where, exc)) from None
    return parse_pin(_git_stdout(proc, "git cat-file blob for " + where), where)


def _rows(parsed):
    """(version, {row key: sha256}) for a parsed pin: a copy-root file by its path within the copy root, a
    build tool by its repository path (the two never collide: a copy-root path cannot start with ".")."""
    version, roots, tools = parsed
    rows = dict(tools)
    for _copy_root, pinned in roots:
        rows.update(pinned)
    return version, rows


def coupling_findings(root, base, pins=PINS):
    """(findings, notes) of the base-coupling leg: every registered pin in the tree under root against the
    same pin at the exact commit base names (_base_commit); CannotEvaluate when the base or any pin cannot be
    read."""
    commit = _base_commit(root, base)
    based = {}
    for pin_rel, _assigned in pins:
        parsed = _base_pin(root, commit, pin_rel)
        if parsed is not None:
            based[pin_rel] = _rows(parsed)
    findings, notes = [], []
    for pin_rel, _assigned in pins:
        pin_path = Path(root) / pin_rel
        try:
            if not stat.S_ISREG(os.lstat(pin_path).st_mode):
                raise CannotEvaluate("{}: not a regular file".format(pin_rel))
            version, rows = _rows(parse_pin(_read_pin_bytes(pin_path), pin_rel))
        except OSError as exc:
            raise CannotEvaluate("{}: unreadable ({})".format(pin_rel, exc)) from None
        if pin_rel in based:
            against = [(pin_rel, based[pin_rel])]
        else:
            against = [(other, based[other]) for other, _root in pins if other in based]
            notes.append("{}: absent at the base {}; compared with {}".format(
                pin_rel, commit, ", ".join(other for other, _rows_ in against) or "no registered pin there"))
        for other, (base_version, base_rows) in against:
            if base_version != version:
                notes.append("{}: the pin moved from version {} ({} at the base {}) to {}; compare its rows by "
                             "hand with the published SHA256SUMS".format(pin_rel, base_version, other, commit,
                                                                          version))
                continue
            for key in sorted(set(rows) & set(base_rows)):
                if rows[key] != base_rows[key]:
                    findings.append("{}: the row for {} changed from {} ({} at the base {}) to {} under the "
                                    "unchanged version {}; a row may change only when the pin moves to a new "
                                    "baseline version".format(pin_rel, key, base_rows[key], other, commit,
                                                              rows[key], version))
            for key in sorted(set(rows) - set(base_rows)):
                notes.append("{}: the row for {} was added under the unchanged version {} ({} at the base "
                             "{} has none)".format(pin_rel, key, version, other, commit))
    return findings, notes


def run_gate(root, pins=PINS, base=None, local_base=None):
    """The gate's exit code over root. base is a --base REF (the exact commit); local_base is a --local-base full
    ref name (the leg runs against the fork point; a name this clone lacks is cannot-evaluate)."""
    findings, errors = scan(root, pins)
    notes = []
    against = base
    if local_base is not None:
        state, value = local_base_decision(root, local_base)
        if state == "usable":
            against = value
        else:
            errors.append(value)
    elif base is None:
        notes.append("the base-coupling leg was not evaluated (no --base REF), so a digest row changed under "
                     "an unchanged version is not refused by this run")
    if against is not None:
        try:
            more, more_notes = coupling_findings(root, against, pins)
            findings.extend(more)
            notes.extend(more_notes)
        except CannotEvaluate as exc:
            errors.append(str(exc))
    for line in notes:
        print("NOTE: " + line)
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: every file pinned in {} matches its SHA-256 under its registered copy root, with no stray "
          "or linked entry, every pinned build tool is a regular file with its pinned SHA-256, and every pin names the site template version recorded in {}, whose homepage ends in that version's archive name{}".format(
              ", ".join(pin_rel for pin_rel, _root in pins), ATTRIBUTION_WHERE,
              "" if against is None else ", and no digest row changed from the base {} under an unchanged "
              "version".format(base if local_base is None else "{} (the merge-base of HEAD and {})".format(
                  against, local_base))))
    return 0


def run_base_gate(root, base, pins=PINS):
    """--base REF at the top: run_gate's exit code, or 2 with one CANNOT EVALUATE line naming the exception when the
    run raises anything other than a cannot-evaluate one (which run_gate reports itself), so an unexpected failure
    never ends with Python's exit 1, the code of a finding."""
    try:
        return run_gate(root, pins, base=base)
    except Exception as exc:
        print("CANNOT EVALUATE: base: the --base {!r} run failed unexpectedly ({}: {})".format(
            base, type(exc).__name__, exc), file=sys.stderr)
        return 2


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "baseline-pin-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


FIXTURE_ROOT = "site/draft/assets/baseline"
FIXTURE_FILES = {"baseline.css": b"body{}\n", "fonts/a.woff2": b"wOF2\x00\x01", "marks/NOTICE.md": b"# n\n"}
# The single-pair registry most cases use, and the 2-pair registry the live tree uses.
FIXTURE_PINS = ((PIN_REL, FIXTURE_ROOT),)
SECOND_PIN = PINS[1][0]
SECOND_ROOT = PINS[1][1]
TWO_PINS = ((PIN_REL, FIXTURE_ROOT), (SECOND_PIN, SECOND_ROOT))
# The build tool a fixture pins outside its copy root, at the registered path.
TOOL_REL = BUILD_TOOLS[0]
FIXTURE_TOOLS = {TOOL_REL: b"import sys\n"}


# The fixture's template version, named by every fixture pin and by the fixture attribution file; it
# differs from the live one, so a pass shows the version is read under the scanned root.
FIXTURE_VERSION = "1.2.1"


def _pin_text(files, root=FIXTURE_ROOT, version=FIXTURE_VERSION, extra="", tools=None):
    lines = ['version = "{}"'.format(version), extra, "", "[files]"]
    for rel in sorted(tools or {}):
        lines.append('"{}" = "{}"'.format(rel, hashlib.sha256(tools[rel]).hexdigest()))
    for rel in sorted(files):
        lines.append('"{}/{}" = "{}"'.format(root, rel, hashlib.sha256(files[rel]).hexdigest()))
    return "\n".join(lines) + "\n"


def _attribution_text(version=FIXTURE_VERSION, block=ATTRIBUTION_BLOCK, homepage=None):
    """A fixture attribution file: another vendored block first, so the version is taken from the named one.
    homepage is the archive URL (None for the version's own archive, False to write none)."""
    if homepage is None:
        homepage = "https://example.org/baseline/baseline-{}.tar.gz".format(version)
    return ('[vendored."marko"]\nname = "Marko"\nversion = "9.9.9"\n\n'
            '[vendored."{}"]\nname = "Shared site template (baseline package)"\nversion = "{}"\n{}'
            'verified = true\n').format(block, version,
                                        "" if homepage is False else 'homepage = "{}"\n'.format(homepage))


def _fixture(tmp, files=None, pin=None, root=FIXTURE_ROOT, pin_rel=PIN_REL, attribution=None, tools=None):
    """A fixture tree under tmp: the copied files under root, the pin at pin_rel (text, bytes, None for
    the exact pin of FIXTURE_FILES under root, or False for absent), the attribution file (text, bytes,
    None for one recording FIXTURE_VERSION, or False to write none) and tools ({repository path: bytes}
    written at their own paths, outside the copy root)."""
    for rel, data in (tools or {}).items():
        os.makedirs((Path(tmp) / rel).parent, exist_ok=True)
        with open(Path(tmp) / rel, "wb") as handle:
            handle.write(data)
    if attribution is None:
        attribution = _attribution_text()
    if attribution is not False:
        os.makedirs((Path(tmp) / ATTRIBUTION_REL).parent, exist_ok=True)
        with open(Path(tmp) / ATTRIBUTION_REL, "wb") as handle:
            handle.write(attribution.encode("utf-8") if isinstance(attribution, str) else attribution)
    files = FIXTURE_FILES if files is None else files
    for rel, data in files.items():
        target = Path(tmp) / root / rel
        os.makedirs(target.parent, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
    if pin is None:
        pin = _pin_text(FIXTURE_FILES, root=root)
    if pin is not False:
        os.makedirs(Path(tmp) / ".baseline", exist_ok=True)
        with open(Path(tmp) / pin_rel, "wb") as handle:
            handle.write(pin.encode("utf-8") if isinstance(pin, str) else pin)
    return tmp


def _scan_case(files=None, pin=None, mutate=None, pins=FIXTURE_PINS, second=None, attribution=None, tools=None):
    """scan() over a fixture; second, when given, is (files, pin) for the second registered pair; tools
    are build-tool files written at their own paths."""
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp, files, pin, attribution=attribution, tools=tools)
        if second is not None:
            _fixture(tmp, second[0], second[1], root=SECOND_ROOT, pin_rel=SECOND_PIN, attribution=False)
        if mutate is not None:
            mutate(Path(tmp))
        return scan(tmp, pins)


def _tools_on_path():
    """Make this directory importable (python3 -I does not put it on sys.path) for the fixture git helpers."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.append(here)


def _commit_all(repo, message, init=False):
    """Commit every file under repo in a fixture repository (created first when init), with the fixture git
    environment: every caller GIT_ variable dropped, scratch HOME, no system or global config, no auto
    maintenance."""
    _tools_on_path()
    from _git_fixture_env import git_fixture_env
    if init:
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True,
                       env=git_fixture_env(), timeout=GIT_TIMEOUT)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True,
                   env=git_fixture_env(), timeout=GIT_TIMEOUT)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                    "-c", "commit.gpgSign=false", "commit", "-q", "--no-verify", "--allow-empty", "-m", message],
                   check=True, capture_output=True, env=git_fixture_env(), timeout=GIT_TIMEOUT)
    if init:
        subprocess.run(["git", "-C", str(repo), "tag", "base"], check=True, capture_output=True,
                       env=git_fixture_env(), timeout=GIT_TIMEOUT)


def _git_fixture(repo, *args):
    """Run `git -C repo <args>` in a fixture repository with the fixture git environment."""
    _tools_on_path()
    from _git_fixture_env import git_fixture_env
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True,
                   env=git_fixture_env(), timeout=GIT_TIMEOUT)


def _coupling_gate(tmp, base, pins, reverted, local=False):
    """(exit code, report text) of run_gate over tmp against base (as --local-base when local, else as --base);
    reverted runs it with the coupling leg replaced by one that finds nothing and reads nothing (the gate
    before this leg)."""
    original = coupling_findings
    if reverted:
        globals()["coupling_findings"] = lambda _root, _base, _pins=PINS: ([], [])
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = (run_gate(tmp, pins, local_base=base) if local else run_gate(tmp, pins, base=base))
    finally:
        globals()["coupling_findings"] = original
    return code, out.getvalue() + err.getvalue()


def _coupling_case(base_kw, head_kw, base, pins, local=False):
    """(code, report, reverted code, reverted report) of run_gate against base over a fixture repository: the
    base commit (tag base) holds the fixture base_kw names; the head commit, when head_kw is given, applies its
    change ("unlink" removes a copied file, "second" writes the second registered pair); local passes base as
    --local-base."""
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp, base_kw.get("files"), base_kw.get("pin"), tools=base_kw.get("tools"))
        _commit_all(tmp, "base", init=True)
        if head_kw is not None:
            if "unlink" in head_kw:
                os.unlink(Path(tmp) / FIXTURE_ROOT / head_kw["unlink"])
            if "second" in head_kw:
                _fixture(tmp, head_kw["second"][0], head_kw["second"][1], root=SECOND_ROOT,
                         pin_rel=SECOND_PIN, attribution=False)
            else:
                _fixture(tmp, head_kw.get("files"), head_kw.get("pin"),
                         attribution=head_kw.get("attribution"), tools=head_kw.get("tools"))
            _commit_all(tmp, "head")
        return _coupling_gate(tmp, base, pins, False, local) + _coupling_gate(tmp, base, pins, True, local)


def _coupling_cases():
    """The base-coupling leg over 2-commit fixture repositories: the base commit (tag base) holds the exact
    fixture; the head commit applies the case's change, which always keeps the copy and its pin consistent,
    so only the coupling leg can refuse it. A refusing case also runs with the leg reverted and must then
    pass (its -flip row), so each refusal is shown to come from this leg. The process environment is scrubbed
    first, so the leg's own git reads run with no caller, global or system git configuration."""
    _tools_on_path()
    from _git_fixture_env import scrub_git_environment
    scrub_git_environment()
    changed = dict(FIXTURE_FILES, **{"baseline.css": b"body{ }\n"})
    added = dict(FIXTURE_FILES, **{"x.js": b"x\n"})
    moved = "1.2.2"
    other_tool = {TOOL_REL: b"import os\n"}
    second = (SECOND_PIN, SECOND_ROOT)
    # The controls: each passes with the leg in place.
    for name, base_kw, head_kw, text in (
            ("couple/no-change-passes", {}, None, "no digest row changed"),
            ("couple/row-modified-version-moved-passes", {},
             {"files": changed, "pin": _pin_text(changed, version=moved),
              "attribution": _attribution_text(version=moved)},
             "NOTE: {}: the pin moved from version {} ".format(PIN_REL, FIXTURE_VERSION)),
            ("couple/comment-only-passes", {}, {"pin": "# a comment\n" + _pin_text(FIXTURE_FILES)},
             "no digest row changed"),
            ("couple/row-removed-passes", {"files": added, "pin": _pin_text(added)},
             {"pin": _pin_text(FIXTURE_FILES), "unlink": "x.js"}, "no digest row changed"),
            ("couple/row-added-same-version-notice", {}, {"files": added, "pin": _pin_text(added)},
             "NOTE: {}: the row for x.js was added under the unchanged".format(PIN_REL))):
        got, report, _reverted, _reverted_report = _coupling_case(base_kw, head_kw, "base", FIXTURE_PINS)
        check(name, got == 0 and text in report, (got, report))
    # The refusals: each fails (1) or is cannot-evaluate (2) with the leg, and passes with it reverted.
    for name, flip, base_kw, head_kw, base, pins, code, text in (
            ("couple/row-modified-same-version-refused", "couple/row-modified-same-version-refused-flip",
             {}, {"files": changed, "pin": _pin_text(changed)}, "base", FIXTURE_PINS, 1,
             "FAIL: {}: the row for baseline.css changed from ".format(PIN_REL)),
            ("couple/tool-row-modified-same-version-refused", "couple/tool-row-modified-same-version-refused-flip",
             {"pin": _pin_text(FIXTURE_FILES, tools=FIXTURE_TOOLS), "tools": FIXTURE_TOOLS},
             {"pin": _pin_text(FIXTURE_FILES, tools=other_tool), "tools": other_tool}, "base", FIXTURE_PINS, 1,
             "FAIL: {}: the row for {} changed from ".format(PIN_REL, TOOL_REL)),
            ("couple/second-pin-absent-at-base-row-modified-refused",
             "couple/second-pin-absent-at-base-row-modified-refused-flip",
             {}, {"second": (changed, _pin_text(changed, root=SECOND_ROOT))}, "base", (FIXTURE_PINS[0], second), 1,
             "FAIL: {}: the row for baseline.css changed from ".format(SECOND_PIN)),
            ("couple/base-unresolvable-cannot-evaluate", "couple/base-unresolvable-cannot-evaluate-flip",
             {}, None, "no-such-ref", FIXTURE_PINS, 2,
             "CANNOT EVALUATE: base: no-such-ref does not resolve to a commit"),
            ("couple/base-option-shaped-cannot-evaluate", "couple/base-option-shaped-cannot-evaluate-flip",
             {}, None, "--output=x", FIXTURE_PINS, 2,
             "CANNOT EVALUATE: base: '--output=x' is not a usable REF"),
            ("couple/base-pin-malformed-cannot-evaluate", "couple/base-pin-malformed-cannot-evaluate-flip",
             {"pin": _pin_text(FIXTURE_FILES) + "[files\n"}, {"pin": _pin_text(FIXTURE_FILES)}, "base",
             FIXTURE_PINS, 2, "CANNOT EVALUATE: {} at the base ".format(PIN_REL))):
        got, report, reverted, reverted_report = _coupling_case(base_kw, head_kw, base, pins)
        check(name, got == code and text in report, (got, report))
        check(flip, reverted == 0, (reverted, reverted_report))

    # Without --base the leg is not evaluated, and the run says so; the exact fixture still passes.
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp)
        got, report = _coupling_gate(tmp, None, FIXTURE_PINS, reverted=False)
    check("couple/no-base-not-evaluated", got == 0 and "NOTE: the base-coupling leg was not evaluated" in report,
          (got, report))

    # A divergent push: the before commit (tag before) adds x.js with one digest, and HEAD, built on the base
    # commit instead, adds it with another under the same version. Their common ancestor lacks the row, so a
    # comparison with it would pass with an added-row notice; --base before is the exact commit, is not an
    # ancestor of HEAD and fails closed, and passes with the leg reverted. --local-base before compares the
    # fork point, as a local run does by design.
    other_added = dict(FIXTURE_FILES, **{"x.js": b"y\n"})
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp)
        _commit_all(tmp, "base", init=True)
        _fixture(tmp, added, _pin_text(added))
        _commit_all(tmp, "before")
        _git_fixture(tmp, "tag", "before")
        _git_fixture(tmp, "reset", "-q", "--hard", "base")
        _fixture(tmp, other_added, _pin_text(other_added))
        _commit_all(tmp, "head")
        got, report = _coupling_gate(tmp, "before", FIXTURE_PINS, False)
        reverted, reverted_report = _coupling_gate(tmp, "before", FIXTURE_PINS, True)
        local, local_report = _coupling_gate(tmp, "refs/tags/before", FIXTURE_PINS, False, local=True)
    check("couple/base-not-ancestor-cannot-evaluate", got == 2 and "CANNOT EVALUATE: base: before (" in report
          and "is not an ancestor of HEAD" in report, (got, report))
    check("couple/base-not-ancestor-cannot-evaluate-flip", reverted == 0, (reverted, reverted_report))
    check("couple/local-base-diverged-compares-fork-point", local == 0
          and "NOTE: {}: the row for x.js was added under the unchanged".format(PIN_REL) in local_report,
          (local, local_report))

    # --local-base: a full ref name that resolves runs the leg against the fork point (a changed row fails, and
    # passes with the leg reverted); a full ref name this clone lacks is cannot-evaluate and names the fetch that
    # provides it, though the exact copy would pass the rest of the gate.
    got, report, reverted, reverted_report = _coupling_case(
        {}, {"files": changed, "pin": _pin_text(changed)}, "refs/tags/base", FIXTURE_PINS, local=True)
    check("couple/local-base-row-modified-refused", got == 1
          and "FAIL: {}: the row for baseline.css changed from ".format(PIN_REL) in report, (got, report))
    check("couple/local-base-row-modified-refused-flip", reverted == 0, (reverted, reverted_report))
    got, report, _reverted, _reverted_report = _coupling_case({}, None, "refs/remotes/origin/main", FIXTURE_PINS,
                                                              local=True)
    check("couple/local-base-absent-cannot-evaluate", got == 2
          and "CANNOT EVALUATE: base: refs/remotes/origin/main does not exist in this clone" in report
          and "`git fetch origin main:refs/remotes/origin/main`" in report and "PASS" not in report, (got, report))

    # A short --local-base name is cannot-evaluate, never resolved: git takes origin/main through a tag or a local
    # branch of that name before refs/remotes/origin/main. refs/remotes/origin/main names the base commit, the
    # shadow names HEAD, and HEAD changes a row under an unchanged version, so a run that resolved the short name
    # would compare HEAD with itself and pass. A full name the clone lacks, reachable only through a local branch
    # refs/heads/refs/remotes/origin/main at HEAD, is the absent ref, never that branch.
    for name, shadow, ref in (
            ("couple/local-base-short-name-tag-shadow-cannot-evaluate", "tag", "origin/main"),
            ("couple/local-base-short-name-branch-shadow-cannot-evaluate", "branch", "origin/main"),
            ("couple/local-base-full-name-branch-shadow-cannot-evaluate", "full", "refs/remotes/origin/main")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _commit_all(tmp, "base", init=True)
            if shadow != "full":
                _git_fixture(tmp, "update-ref", "refs/remotes/origin/main", "base")
            _fixture(tmp, changed, _pin_text(changed))
            _commit_all(tmp, "head")
            if shadow == "tag":
                _git_fixture(tmp, "tag", "origin/main")
            elif shadow == "branch":
                _git_fixture(tmp, "branch", "origin/main")
            else:
                _git_fixture(tmp, "update-ref", "refs/heads/refs/remotes/origin/main", "HEAD")
            got, report = _coupling_gate(tmp, ref, FIXTURE_PINS, False, local=True)
        want = ("is not a full ref name" if shadow != "full" else "does not exist in this clone")
        check(name, got == 2 and "CANNOT EVALUATE: base: " in report and want in report and "PASS" not in report,
              (got, report))

    # A --local-base REF that exists but does not resolve to a readable commit is cannot-evaluate, never read as
    # the absent ref: the peeled read exits 1 with no output for it too, so only the name read tells them
    # apart. HEAD changes a row under an unchanged version, so a skipped leg would pass the run.
    # refs/remotes/origin/main names a commit whose object is then deleted, or the base pin's blob.
    for name, broken in (("couple/local-base-missing-object-cannot-evaluate", "missing"),
                         ("couple/local-base-blob-cannot-evaluate", "blob")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _commit_all(tmp, "base", init=True)
            _fixture(tmp, changed, _pin_text(changed))
            _commit_all(tmp, "head")
            deleted = True
            if broken == "missing":
                _commit_all(tmp, "gone")
                from _git_fixture_env import git_fixture_env
                gone = subprocess.run(["git", "-C", tmp, "rev-parse", "--verify", "HEAD"], check=True,
                                      capture_output=True, env=git_fixture_env(),
                                      timeout=GIT_TIMEOUT).stdout.decode("ascii").strip()
                _git_fixture(tmp, "update-ref", "refs/remotes/origin/main", gone)
                _git_fixture(tmp, "reset", "-q", "--hard", "HEAD~1")
                loose = Path(tmp) / ".git" / "objects" / gone[:2] / gone[2:]
                deleted = loose.is_file()
                if deleted:
                    os.unlink(loose)
            else:
                _git_fixture(tmp, "update-ref", "refs/remotes/origin/main", "base:" + PIN_REL)
            got, report = _coupling_gate(tmp, "refs/remotes/origin/main", FIXTURE_PINS, False, local=True)
        check(name, deleted and got == 2 and "CANNOT EVALUATE: base: refs/remotes/origin/main exists in this clone "
              "but does not resolve to a readable commit" in report and "does not exist in this clone" not in report,
              (deleted, got, report))

    # A --local-base REF git reports broken is cannot-evaluate as broken, never read as absent, though the name read
    # exits 1 with no output on stdout for each: a loose ref file holding no object id (git warns it ignores a
    # broken ref), a symbolic ref to a ref that does not exist (git warns it ignores a dangling symref), and a
    # packed-refs line for origin/main ending in CRLF, which the name read skips silently and only the read of
    # every ref reports. HEAD changes a row under an unchanged version, so a skipped leg would pass the run.
    for name, broken in (("couple/local-base-broken-loose-ref-cannot-evaluate", "loose"),
                         ("couple/local-base-broken-packed-ref-cannot-evaluate", "packed"),
                         ("couple/local-base-dangling-symref-cannot-evaluate", "dangling")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _commit_all(tmp, "base", init=True)
            _fixture(tmp, changed, _pin_text(changed))
            _commit_all(tmp, "head")
            gitdir = Path(tmp) / ".git"
            written = not (gitdir / "packed-refs").exists()
            if broken == "packed":
                with open(gitdir / "packed-refs", "xb") as handle:
                    handle.write(b"1" * 40 + b" refs/remotes/origin/main\r\n")
            else:
                os.makedirs(gitdir / "refs" / "remotes" / "origin", exist_ok=True)
                with open(gitdir / "refs" / "remotes" / "origin" / "main", "xb") as handle:
                    handle.write(b"broken\n" if broken == "loose" else b"ref: refs/remotes/origin/nowhere\n")
            got, report = _coupling_gate(tmp, "refs/remotes/origin/main", FIXTURE_PINS, False, local=True)
        check(name, written and got == 2 and "CANNOT EVALUATE: base: refs/remotes/origin/main does not resolve, and "
              "git " in report and "does not exist in this clone" not in report, (written, got, report))

    # A --local-base fork point whose tree is HEAD's own leaves the leg nothing to compare, so it is cannot-evaluate,
    # never an empty pass: refs/remotes/origin/main is a symbolic ref to the checked-out branch, names HEAD itself, or
    # names the base commit when HEAD is an empty commit over it. In the first 2, HEAD changes a row under an unchanged
    # version, so a leg run against that fork point would compare HEAD with itself and pass.
    for name, setup in (("couple/local-base-symref-to-checked-out-branch-cannot-evaluate", "symref"),
                        ("couple/local-base-names-head-cannot-evaluate", "head"),
                        ("couple/local-base-unchanged-descent-cannot-evaluate", "empty")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _commit_all(tmp, "base", init=True)
            if setup == "empty":
                _commit_all(tmp, "head")
                _git_fixture(tmp, "update-ref", "refs/remotes/origin/main", "base")
            else:
                _fixture(tmp, changed, _pin_text(changed))
                _commit_all(tmp, "head")
                _git_fixture(tmp, "branch", "-M", "work")
                if setup == "symref":
                    _git_fixture(tmp, "symbolic-ref", "refs/remotes/origin/main", "refs/heads/work")
                else:
                    _git_fixture(tmp, "update-ref", "refs/remotes/origin/main", "HEAD")
            got, report = _coupling_gate(tmp, "refs/remotes/origin/main", FIXTURE_PINS, False, local=True)
        check(name, got == 2 and "CANNOT EVALUATE: base: the fork point " in report
              and "holds HEAD's own tree" in report and "PASS" not in report, (got, report))

    # Every base read runs with replacement objects off (--no-replace-objects). HEAD changes a row under an unchanged
    # version; a replacement of the base commit's tree or of the base pin's blob by HEAD's would make the base read
    # return HEAD's pin and pass, and a graft giving HEAD the divergent before commit (which already holds the changed
    # row) as its parent would make that commit an ancestor and pass.
    for name, setup, code, text in (
            ("couple/replaced-base-tree-ignored", "tree", 1, "FAIL: {}: the row for baseline.css changed from "),
            ("couple/replaced-base-pin-blob-ignored", "blob", 1, "FAIL: {}: the row for baseline.css changed from "),
            ("couple/grafted-parent-ignored", "graft", 2, "is not an ancestor of HEAD")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _commit_all(tmp, "base", init=True)
            base = "base"
            if setup == "graft":
                _fixture(tmp, changed, _pin_text(changed))
                _commit_all(tmp, "before")
                _git_fixture(tmp, "tag", "before")
                _git_fixture(tmp, "reset", "-q", "--hard", "base")
                base = "before"
            _fixture(tmp, changed, _pin_text(changed))
            _commit_all(tmp, "head")
            if setup == "tree":
                _git_fixture(tmp, "replace", "base^{tree}", "HEAD^{tree}")
            elif setup == "blob":
                _git_fixture(tmp, "replace", "base:" + PIN_REL, "HEAD:" + PIN_REL)
            else:
                _git_fixture(tmp, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                             "replace", "--graft", "HEAD", "before")
            got, report = _coupling_gate(tmp, base, FIXTURE_PINS, False)
        check(name, got == code and text.format(PIN_REL) in report and "PASS" not in report, (got, report))

    # The leg's git reads drop every caller GIT_ variable (_git_env). The scrub above clears this process, so the
    # gate runs here as a child, a copy of this file in a fixture tree (both registered pairs, so ROOT is the
    # fixture), under an ambient GIT_DIR naming a decoy repository whose tag base already holds HEAD's changed pin.
    # The true result compares the fixture's own base and fails; a read that kept GIT_DIR would compare the decoy
    # and pass.
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp, \
            tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-decoy-") as decoy:
        for tree, files in ((tmp, None), (decoy, changed)):
            _fixture(tree, files, None if files is None else _pin_text(files))
            _fixture(tree, None, None, root=SECOND_ROOT, pin_rel=SECOND_PIN, attribution=False)
            _commit_all(tree, "base", init=True)
        _fixture(tmp, changed, _pin_text(changed))
        _commit_all(tmp, "head")
        script = Path(tmp) / "tools" / Path(__file__).name
        os.makedirs(script.parent)
        with open(Path(__file__).resolve(), "rb") as source, open(script, "xb") as target:
            target.write(source.read())
        try:
            child = subprocess.run([sys.executable, "-I", "-B", str(script), "--base", "base"], capture_output=True,
                                   env=dict(os.environ, GIT_DIR=str(Path(decoy) / ".git")), timeout=GIT_TIMEOUT,
                                   check=False)
            got, report = child.returncode, (child.stdout + child.stderr).decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            got, report = None, repr(exc)
    check("couple/ambient-git-dir-ignored", got == 1
          and "FAIL: {}: the row for baseline.css changed from ".format(PIN_REL) in report, (got, report))


def _probe_child(tmp, ref, env=None, option="--probe-local-base"):
    """(exit code, stdout, stderr) of a copy of this file in the fixture tree tmp, run as a child with option ref
    (--probe-local-base by default; so ROOT is the fixture, and the command line, exit code and streams are the
    ones the local runner reads); env is the child's environment, this process's when None."""
    script = Path(tmp) / "tools" / Path(__file__).name
    if not script.exists():
        os.makedirs(script.parent, exist_ok=True)
        with open(Path(__file__).resolve(), "rb") as source, open(script, "xb") as target:
            target.write(source.read())
    try:
        child = subprocess.run([sys.executable, "-I", "-B", str(script), option, ref],
                               capture_output=True, env=dict(os.environ) if env is None else env,
                               timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, b"", repr(exc).encode("utf-8")
    return child.returncode, child.stdout, child.stderr


def _runner_block():
    """The local runner's baseline-pin block, extracted from tools/run_all_checks.sh as committed (the run_gate()
    definition and the block from its baseline_probe() definition through its fi), as a bash script that starts
    with the runner's shell options and failure-state initializers and exits with the runner's failed flag; None
    when the runner does not hold those lines."""
    try:
        lines = (ROOT / "tools" / "run_all_checks.sh").read_text(encoding="utf-8").splitlines(keepends=True)
        define = lines.index("run_gate() {\n")
        define_end = lines.index("}\n", define)
        start = [index for index, line in enumerate(lines) if line == "baseline_probe() {\n"]
        end = lines.index("fi\n", start[0])
    except (OSError, UnicodeDecodeError, ValueError, IndexError):
        return None
    if len(start) != 1:
        return None
    return ("set -uo pipefail\nfailed=0\nfailed_names=\"\"\n" + "".join(lines[define:define_end + 1])
            + "".join(lines[start[0]:end + 1]) + 'exit "$failed"\n')


def _runner_child(tmp, block):
    """(exit code, output) of the extracted runner block run by bash in the fixture tree tmp, its python3 gate lines
    resolving to the fixture's copy of this file (_probe_child writes it first)."""
    if block is None:
        return None, "the baseline-pin block could not be extracted from tools/run_all_checks.sh"
    script = Path(tmp) / "runner-block.sh"
    with open(script, "x", encoding="utf-8") as handle:
        handle.write(block)
    try:
        child = subprocess.run(["bash", str(script)], cwd=tmp, capture_output=True, env=dict(os.environ),
                               timeout=GIT_TIMEOUT * 4, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, repr(exc)
    return child.returncode, (child.stdout + child.stderr).decode("utf-8", "replace")


def _agrees(ref, probe, explicit):
    """Whether a --probe-local-base result and a --local-base result over the same fixture agree, the fixture's HEAD
    changing a row under an unchanged version: probe 0 (silent) with --local-base finding that row (exit 1, no
    CANNOT EVALUATE line); probe 1 (silent) with --local-base exit 2 on the absence line alone; probe 2 with
    --local-base exit 2 on the very line the probe wrote."""
    (pcode, pout, perr), (ecode, eout, eerr) = probe, explicit
    lines = [line for line in eerr.decode("utf-8", "replace").splitlines() if line.startswith("CANNOT EVALUATE")]
    if pout != b"":
        return False
    if pcode == 0:
        return perr == b"" and ecode == 1 and not lines and b"FAIL: " in eout
    if pcode == 1:
        return perr == b"" and ecode == 2 and len(lines) == 1 and lines[0].startswith(
            "CANNOT EVALUATE: base: {} does not exist in this clone".format(ref))
    return pcode == 2 and ecode == 2 and lines == perr.decode("utf-8", "replace").splitlines()


def _probe_cases():
    """--probe-local-base over 2-commit fixture repositories holding both registered pairs (HEAD changes a row under
    an unchanged version, so a probe that read a broken ref or ref store as absent would let the local runner skip
    the leg and pass). It exits 0 silently for a usable ref, 1 silently only for a ref the clone genuinely lacks,
    and 2 with one CANNOT EVALUATE line for a broken loose ref, a broken packed-refs line, a dangling symbolic ref,
    a packed-refs that is a directory, a directory at the ref's loose path, a full name git resolves only through a
    local branch refs/heads/REF and a short name. On every fixture, --local-base over the same tree must agree with
    the probe (_agrees), and the runner's baseline-pin block, extracted from tools/run_all_checks.sh and run by bash
    with real git, fails the present and packed-refs directory fixtures and passes the absent one with its NOTE.
    Run after _coupling_cases, whose scrub of this process's git environment the children inherit."""
    changed = dict(FIXTURE_FILES, **{"baseline.css": b"body{ }\n"})
    ref = "refs/remotes/origin/main"
    broken = "CANNOT EVALUATE: base: {} does not resolve, and git ".format(ref)
    store = "CANNOT EVALUATE: base: {} does not resolve, and this clone's ref store could not be shown ".format(ref)
    block = _runner_block()
    disagreements = []
    runs = {}
    for name, setup, probed, want, prefix in (
            ("probe/present-exits-0", "present", ref, 0, None),
            ("probe/absent-exits-1-silent", "absent", ref, 1, None),
            ("probe/broken-loose-ref-exits-2", "loose", ref, 2, broken),
            ("probe/broken-packed-ref-exits-2", "packed", ref, 2, broken),
            ("probe/dangling-symref-exits-2", "dangling", ref, 2, broken),
            ("probe/packed-refs-directory-exits-2", "packed-dir", ref, 2, store),
            ("probe/loose-path-directory-exits-2", "loose-dir", ref, 2, store),
            ("probe/full-name-branch-shadow-exits-2", "shadow", ref, 2,
             "CANNOT EVALUATE: base: {} does not exist in this clone".format(ref)),
            ("probe/short-name-exits-2", "present", "origin/main", 2,
             "CANNOT EVALUATE: base: --local-base origin/main is not a full ref name")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _fixture(tmp, None, None, root=SECOND_ROOT, pin_rel=SECOND_PIN, attribution=False)
            _commit_all(tmp, "base", init=True)
            _fixture(tmp, changed, _pin_text(changed))
            _commit_all(tmp, "head")
            gitdir = Path(tmp) / ".git"
            written = not (gitdir / "packed-refs").exists()
            if setup == "present":
                _git_fixture(tmp, "update-ref", ref, "base")
            elif setup == "shadow":
                _git_fixture(tmp, "update-ref", "refs/heads/" + ref, "HEAD")
            elif setup == "packed":
                with open(gitdir / "packed-refs", "xb") as handle:
                    handle.write(b"1" * 40 + b" " + ref.encode("ascii") + b"\r\n")
            elif setup == "packed-dir":
                os.mkdir(gitdir / "packed-refs")
            elif setup == "loose-dir":
                os.makedirs(gitdir / "refs" / "remotes" / "origin" / "main")
            elif setup in ("loose", "dangling"):
                os.makedirs(gitdir / "refs" / "remotes" / "origin", exist_ok=True)
                with open(gitdir / "refs" / "remotes" / "origin" / "main", "xb") as handle:
                    handle.write(b"broken\n" if setup == "loose" else b"ref: refs/remotes/origin/nowhere\n")
            got, out, err = _probe_child(tmp, probed)
            explicit = _probe_child(tmp, probed, option="--local-base")
            if probed == ref and setup in ("present", "absent", "packed-dir"):
                runs[setup] = _runner_child(tmp, block)
        text = err.decode("utf-8", "replace")
        check(name, written and got == want and out == b"" and (
            err == b"" if prefix is None else text.startswith(prefix) and text.count("\n") == 1),
              (written, got, out, err))
        if not _agrees(probed, (got, out, err), explicit):
            disagreements.append((setup, probed, (got, out, err), explicit))
    check("probe/explicit-local-base-agrees-on-every-fixture", not disagreements, disagreements)

    # The runner's own block, with real git: a usable ref runs the leg and fails the changed row; a genuine absence
    # prints the runner's NOTE and passes; a packed-refs that is a directory (which git reads as a clean miss) fails
    # the run under the baseline-pin name with the probe's exit 2, never the absence skip.
    note = "NOTE: baseline-pin base comparison did NOT RUN"
    got, report = runs.get("present", (None, ""))
    check("runner/present-row-modified-fails", got == 1 and "GATE FAILED: baseline-pin (exit 1)" in report
          and note not in report, (got, report))
    got, report = runs.get("absent", (None, ""))
    check("runner/absent-skips-with-note", got == 0 and note in report and "GATE FAILED" not in report,
          (got, report))
    got, report = runs.get("packed-dir", (None, ""))
    check("runner/packed-refs-directory-fails", got == 1 and note not in report
          and "GATE FAILED: baseline-pin (check_baseline_pin.py --probe-local-base {} exit 2, stderr: {}".format(
              ref, store) in report, (got, report))

    # The packed-refs shapes, read directly: absent, empty, and a header, a ref line and its peel line are well
    # formed; a directory, a line git does not write, a peel line with no ref line before it and a missing final
    # line feed are not.
    oid = b"1" * 40
    shapes = []
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        path = Path(tmp) / "packed-refs"
        shapes.append(_packed_refs_problem(path) is None)
        for data, ok in ((b"", True),
                         (PACKED_HEADER + b" peeled fully-peeled sorted \n" + oid + b" refs/tags/v1\n^" + oid + b"\n",
                          True),
                         (oid + b" refs/remotes/origin/main\r\n", False),
                         (b"^" + oid + b"\n", False),
                         (oid + b" refs/remotes/origin/main", False)):
            with open(path, "wb") as handle:
                handle.write(data)
            shapes.append((_packed_refs_problem(path) is None) == ok)
        os.unlink(path)
        os.mkdir(path)
        shapes.append(_packed_refs_problem(path) is not None)
    check("probe/packed-refs-shapes", all(shapes) and len(shapes) == 7, shapes)

    # A reftable ref store is refused, never read as absence: git's report of the store is replaced by "reftable"
    # over a fixture that lacks the ref, so only that report differs from a genuine absence.
    original = _git_base_read

    def _reftable(root, args):
        if args == ["rev-parse", "--show-ref-format"]:
            return subprocess.CompletedProcess(args, 0, b"reftable\n", b"")
        return original(root, args)

    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp)
        _commit_all(tmp, "base", init=True)
        control = local_base_decision(tmp, ref)
        globals()["_git_base_read"] = _reftable
        try:
            decision = local_base_decision(tmp, ref)
        finally:
            globals()["_git_base_read"] = original
    check("probe/reftable-store-refused", control[0] == "absent" and decision[0] == "cannot"
          and "reftable" in decision[1], (control, decision))

    # The ref store guards a fixture can reach, each over a clone git reads as a clean miss of the name, so each would
    # otherwise be the absence (exit 1): a per-worktree name that reads as absent (refs/bisect/good), while a present
    # one resolves like any other name; refs/remotes as a symbolic link to an empty directory; and refs/remotes/origin
    # as a directory that cannot be listed (mode 000; a privileged process lists it anyway, so under root os.listdir
    # refuses that one directory instead). A refs directory that is missing is not reachable: git then does not take
    # the directory for a repository at all.
    real_listdir = os.listdir
    for name, setup, probed, want, text in (
            ("probe/per-worktree-absent-refused", "absent", "refs/bisect/good", "cannot", "is a per-worktree ref name"),
            ("probe/per-worktree-present-usable", "bisect", "refs/bisect/good", "usable", None),
            ("probe/symlinked-ref-directory-refused", "linked", ref, "cannot",
             "is not a directory (a symbolic link, a file or another entry)"),
            ("probe/unlistable-ref-directory-refused", "unlistable", ref, "cannot", "is not a readable directory")):
        with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
            _fixture(tmp)
            _commit_all(tmp, "base", init=True)
            _fixture(tmp, changed, _pin_text(changed))
            _commit_all(tmp, "head")
            gitdir = Path(tmp) / ".git"
            locked = gitdir / "refs" / "remotes" / "origin"
            if setup == "bisect":
                _git_fixture(tmp, "update-ref", probed, "base")
            elif setup == "linked":
                os.mkdir(Path(tmp) / "elsewhere-refs")
                os.symlink(Path(tmp) / "elsewhere-refs", gitdir / "refs" / "remotes")
            elif setup == "unlistable":
                os.makedirs(locked)
                os.chmod(locked, 0)

            def _refuse(path=".", _locked=str(locked), _real=real_listdir):
                if os.fspath(path) == _locked:
                    raise PermissionError(13, "listing refused by the self-test", _locked)
                return _real(path)

            if setup == "unlistable" and os.geteuid() == 0:
                os.listdir = _refuse
            try:
                decision = local_base_decision(tmp, probed)
            finally:
                os.listdir = real_listdir
                if setup == "unlistable":
                    os.chmod(locked, 0o755)
        if want == "usable":
            ok = decision[0] == "usable" and COMMIT_RE.fullmatch(decision[1] or "") is not None
        else:
            ok = (decision[0] == "cannot" and text in decision[1] and decision[1].startswith(
                "base: {} does not resolve, and this clone's ref store could not be shown ".format(probed)))
        check(name, ok, decision)

    # The --base path at the top (main): an exception the run raises other than a cannot-evaluate one exits 2 on one
    # CANNOT EVALUATE line naming it, never Python's exit 1, the code of a finding. ROOT is the fixture (both
    # registered pairs, HEAD unchanged), and the leg raises.
    original_root, original_leg = ROOT, coupling_findings

    def _leg_raises(_root, _base, _pins=PINS):
        raise RuntimeError("injected")

    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp)
        _fixture(tmp, None, None, root=SECOND_ROOT, pin_rel=SECOND_PIN, attribution=False)
        _commit_all(tmp, "base", init=True)
        globals()["ROOT"], globals()["coupling_findings"] = Path(tmp), _leg_raises
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                got = main(["--base", "base"])
        except Exception as exc:
            got = "escaped main: {}: {}".format(type(exc).__name__, exc)
        finally:
            globals()["ROOT"], globals()["coupling_findings"] = original_root, original_leg
    line = "CANNOT EVALUATE: base: the --base 'base' run failed unexpectedly (RuntimeError: injected)"
    check("couple/base-unexpected-exception-exits-2", got == 2 and out.getvalue() == ""
          and err.getvalue() == line + "\n", (got, out.getvalue(), err.getvalue()))

    # An unexpected exception in the --local-base reads is cannot-evaluate, never absence: the probe exits 2 with one
    # CANNOT EVALUATE line naming it, and --local-base exits 2 on that same line. A decision that mapped it to absence
    # would let the local runner skip the leg and pass.
    original_commit = _local_base_commit

    def _raises(_root, _ref):
        raise RuntimeError("injected")

    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp:
        _fixture(tmp)
        _commit_all(tmp, "base", init=True)
        globals()["_local_base_commit"] = _raises
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                probed = probe_local_base(tmp, ref)
            explicit = _coupling_gate(tmp, ref, FIXTURE_PINS, False, local=True)
        finally:
            globals()["_local_base_commit"] = original_commit
    line = "CANNOT EVALUATE: base: the --local-base read of {!r} failed unexpectedly (RuntimeError: injected)".format(
        ref)
    check("probe/unexpected-exception-exits-2", probed == 2 and out.getvalue() == ""
          and err.getvalue() == line + "\n" and explicit[0] == 2 and line in explicit[1],
          (probed, out.getvalue(), err.getvalue(), explicit))

    # Under an ambient GIT_DIR naming a decoy repository that lacks the ref, the probe still reads the fixture's
    # own refs (_git_env drops it): the fixture holds the ref, so the answer is 0. A probe that kept GIT_DIR, as
    # git show-ref in the runner did, would read the decoy and report a clean absence (1).
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp, \
            tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-decoy-") as decoy:
        for tree in (tmp, decoy):
            _fixture(tree)
            _commit_all(tree, "base", init=True)
        _fixture(tmp, changed, _pin_text(changed))
        _commit_all(tmp, "head")
        _git_fixture(tmp, "update-ref", ref, "base")
        got, out, err = _probe_child(tmp, ref, env=dict(os.environ, GIT_DIR=str(Path(decoy) / ".git")))
    check("probe/ambient-git-dir-decoy-exits-0", got == 0 and out == b"" and err == b"", (got, out, err))


def _self_test():
    findings, errors = _scan_case()
    check("pass/fixture-exact", findings == [] and errors == [], (findings, errors))

    changed = dict(FIXTURE_FILES, **{"baseline.css": b"body{ }\n"})
    findings, errors = _scan_case(files=changed)
    check("refuse/differing-file", errors == [] and len(findings) == 1
          and findings[0].startswith(FIXTURE_ROOT + "/baseline.css: SHA-256"), (findings, errors))

    # One flipped byte in a binary file, the same length, is still a difference.
    changed = dict(FIXTURE_FILES, **{"fonts/a.woff2": b"wOF2\x00\x02"})
    findings, errors = _scan_case(files=changed)
    check("refuse/differing-file-same-length", errors == [] and len(findings) == 1
          and "fonts/a.woff2: SHA-256" in findings[0], (findings, errors))

    missing = {rel: data for rel, data in FIXTURE_FILES.items() if rel != "marks/NOTICE.md"}
    findings, errors = _scan_case(files=missing)
    check("refuse/missing-file", errors == [] and findings == [FIXTURE_ROOT + "/marks/NOTICE.md: missing"],
          (findings, errors))

    stray = dict(FIXTURE_FILES, **{"search.js": b"x\n"})
    findings, errors = _scan_case(files=stray)
    check("refuse/stray-file", errors == [] and len(findings) == 1 and "search.js: stray file" in findings[0],
          (findings, errors))

    stray = dict(FIXTURE_FILES, **{"reference/landing.html": b"<p>\n"})
    findings, errors = _scan_case(files=stray)
    check("refuse/stray-file-nested", errors == [] and len(findings) == 1
          and "reference/landing.html: stray file" in findings[0], (findings, errors))

    def _link_file(tmp):
        target = tmp / FIXTURE_ROOT / "baseline.css"
        os.unlink(target)
        os.symlink(tmp / PIN_REL, target)

    findings, errors = _scan_case(mutate=_link_file)
    check("refuse/symlink-file", errors == [] and findings == [FIXTURE_ROOT + "/baseline.css: is a symbolic link"],
          (findings, errors))

    def _link_dir(tmp):
        os.symlink(tmp / FIXTURE_ROOT / "fonts", tmp / FIXTURE_ROOT / "fonts2")

    findings, errors = _scan_case(mutate=_link_dir)
    check("refuse/symlink-dir", errors == [] and findings == [FIXTURE_ROOT + "/fonts2: is a symbolic link"],
          (findings, errors))

    def _link_root(tmp):
        real = tmp / "elsewhere"
        os.rename(tmp / FIXTURE_ROOT, real)
        os.symlink(real, tmp / FIXTURE_ROOT)

    findings, errors = _scan_case(mutate=_link_root)
    check("refuse/symlink-root", errors == [] and len(findings) == 1 and "copy root is a symbolic link" in findings[0],
          (findings, errors))

    # A copy root reached through a linked parent folder: site/draft is a link to a real folder holding the
    # exact copy, so only the parent folder check can refuse it (the root's own lstat sees a real directory).
    parent_rel = FIXTURE_ROOT.rsplit("/", 2)[0]

    def _link_parent(tmp):
        real = tmp / "elsewhere-parent"
        os.rename(tmp / parent_rel, real)
        os.symlink(real, tmp / parent_rel)

    findings, errors = _scan_case(mutate=_link_parent)
    check("refuse/symlink-parent", errors == [] and findings == [
        "{}: parent folder {} is a symbolic link or not a directory".format(FIXTURE_ROOT, parent_rel)],
          (findings, errors))

    # Its twin: the same link beside the parent folder, not on the path to the copy root, passes.
    def _link_beside_parent(tmp):
        os.symlink(tmp / parent_rel, tmp / (parent_rel + "-alias"))

    findings, errors = _scan_case(mutate=_link_beside_parent)
    check("pass/symlink-beside-parent", findings == [] and errors == [], (findings, errors))

    # A parent folder that is a regular file is a finding, not cannot-evaluate: red if the parent check
    # refuses only a link (the root's lstat then fails with ENOTDIR, an error).
    def _file_parent(tmp):
        os.rename(tmp / parent_rel, tmp / "elsewhere-parent")
        (tmp / parent_rel).write_bytes(b"not a folder\n")

    findings, errors = _scan_case(mutate=_file_parent)
    check("refuse/nondir-parent", errors == [] and findings == [
        "{}: parent folder {} is a symbolic link or not a directory".format(FIXTURE_ROOT, parent_rel)],
          (findings, errors))

    # The top-level folder (site) as a link to a real folder holding the exact copy: red if the parent check
    # starts below the top level.
    top_rel = FIXTURE_ROOT.split("/", 1)[0]

    def _link_top(tmp):
        real = tmp / "elsewhere-top"
        os.rename(tmp / top_rel, real)
        os.symlink(real, tmp / top_rel)

    findings, errors = _scan_case(mutate=_link_top)
    check("refuse/symlink-top-folder", errors == [] and findings == [
        "{}: parent folder {} is a symbolic link or not a directory".format(FIXTURE_ROOT, top_rel)],
          (findings, errors))

    # The folder just above the copy root (site/draft/assets) as a link to a real folder holding the exact
    # copy: red if the parent check skips the last parent before the root.
    middle_rel = FIXTURE_ROOT.rsplit("/", 1)[0]

    def _link_middle(tmp):
        real = tmp / "elsewhere-middle"
        os.rename(tmp / middle_rel, real)
        os.symlink(real, tmp / middle_rel)

    findings, errors = _scan_case(mutate=_link_middle)
    check("refuse/symlink-middle-parent", errors == [] and findings == [
        "{}: parent folder {} is a symbolic link or not a directory".format(FIXTURE_ROOT, middle_rel)],
          (findings, errors))

    def _fifo(tmp):
        os.mkfifo(tmp / FIXTURE_ROOT / "pipe")

    findings, errors = _scan_case(mutate=_fifo)
    check("refuse/fifo-not-regular", errors == [] and findings == [FIXTURE_ROOT + "/pipe: not a regular file"],
          (findings, errors))

    findings, errors = _scan_case(files={})
    check("refuse/root-missing", errors == [] and len(findings) == len(FIXTURE_FILES)
          and all(line.endswith(": missing") for line in findings), (findings, errors))

    findings, errors = _scan_case(pin=False)
    check("refuse/pin-missing-cannot-evaluate", findings == [] and errors == [PIN_REL + ": missing"],
          (findings, errors))

    original = _read_pin_bytes

    def _deny(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_read_pin_bytes"] = _deny
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_read_pin_bytes"] = original
    check("refuse/pin-unreadable-cannot-evaluate", findings == [] and len(errors) == 1
          and "unreadable" in errors[0], (findings, errors))

    original_hash = _sha256

    def _deny_hash(path):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_sha256"] = _deny_hash
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_sha256"] = original_hash
    check("refuse/file-unreadable-cannot-evaluate", findings == [] and len(errors) == len(FIXTURE_FILES)
          and all("unreadable" in line for line in errors), (findings, errors))

    good = _pin_text(FIXTURE_FILES)
    _css_digest = hashlib.sha256(FIXTURE_FILES["baseline.css"]).hexdigest()
    css_key = '"{}/baseline.css"'.format(FIXTURE_ROOT)
    for name, pin in (
            ("malformed/not-utf8", good.encode("utf-8") + b"# \xff\n"),
            ("malformed/not-toml", good + "[files\n"),
            ("malformed/version-missing", good.replace('version = "1.2.1"\n', "")),
            ("malformed/version-not-string", good.replace('version = "1.2.1"', "version = 1")),
            ("malformed/version-shape", _pin_text(FIXTURE_FILES, version="1.2")),
            ("malformed/unknown-key", _pin_text(FIXTURE_FILES, extra='colour = "blue"')),
            ("malformed/no-files", 'version = "1.2.1"\n'),
            ("malformed/files-not-table", 'version = "1.2.1"\nfiles = "baseline.css"\n'),
            ("malformed/outside-copy-root", _pin_text(FIXTURE_FILES, root="site/draft/assets/other")),
            ("malformed/copy-dir-inside-a-name", _pin_text(FIXTURE_FILES, root="site/draft/xassets/baseline")),
            ("malformed/absolute-path", _pin_text(FIXTURE_FILES, root="/site/draft/assets/baseline")),
            ("malformed/unsafe-path", good.replace(css_key, '"{}/../baseline.css"'.format(FIXTURE_ROOT))),
            ("malformed/short-digest", good.replace(_css_digest, _css_digest[:-1])),
            ("malformed/upper-digest", good.replace(_css_digest, _css_digest.upper())),
            ("malformed/duplicate-path", good + '{} = "{}"\n'.format(css_key, "0" * 64)),
            ("malformed/two-copy-roots", good + '"site/assets/baseline/x" = "{}"\n'.format("0" * 64))):
        findings, errors = _scan_case(pin=pin)
        check(name, findings == [] and len(errors) == 1, (findings, errors))

    # The parser's resource errors are handled at each parse site, never propagated. The injection raises
    # only for the document that carries the marker (a pin's [files] table or the attribution file's
    # [vendored. tables) and parses the other one, so each case reaches 1 parse site.
    for name, exc_type, marker, where in (
            ("malformed/recursion-error", RecursionError, "[files]", PIN_REL),
            ("malformed/memory-error", MemoryError, "[files]", PIN_REL),
            ("version/attribution-recursion-error", RecursionError, "[vendored.", ATTRIBUTION_REL),
            ("version/attribution-memory-error", MemoryError, "[vendored.", ATTRIBUTION_REL)):
        original_loads = tomllib.loads

        def _raise(text, _exc_type=exc_type, _marker=marker, _loads=original_loads):
            if _marker not in text:
                return _loads(text)
            raise _exc_type("raised by the self-test")

        tomllib.loads = _raise
        try:
            findings, errors = _scan_case()
        finally:
            tomllib.loads = original_loads
        check(name, findings == [] and len(errors) == 1 and errors[0].startswith(where + ":")
              and exc_type.__name__ in errors[0], (findings, errors))

    # The pin size bound at its ends: one byte under, exactly at, and one byte over MAX_PIN_BYTES.
    pad = MAX_PIN_BYTES - len(good.encode("utf-8"))
    for name, extra, refused in (("bound/pin-size-under", pad - 1, False), ("bound/pin-size-at", pad, False),
                                 ("bound/pin-size-over", pad + 1, True)):
        # A trailing comment changes only the size; its "#" and newline are part of the padding.
        body = good + "#" + "x" * (extra - 2) + "\n"
        try:
            parse_pin(body.encode("utf-8"), "fixture")
            got = False
        except CannotEvaluate:
            got = True
        check(name, got == refused and len(body.encode("utf-8")) == len(good.encode("utf-8")) + extra,
              (len(body), got))

    # The registry: every pin is bound to its own copy root. Both pairs pass together; a defect in the
    # second copy is reported against the second root and pin; a pin naming another root (so 2 pins would
    # validate 1 copy) and a registry repeating a pin or a root are cannot-evaluate.
    check("registry/live-pairs", PINS == ((".baseline/pin.toml", "site/draft/assets/baseline"),
                                          (".baseline/opfiles-pin.toml", "opf/site/draft/assets/baseline")),
          PINS)
    findings, errors = _scan_case(pins=TWO_PINS, second=(None, None))
    check("registry/two-pairs-pass", findings == [] and errors == [], (findings, errors))

    changed = dict(FIXTURE_FILES, **{"baseline.css": b"body{ }\n"})
    findings, errors = _scan_case(pins=TWO_PINS, second=(changed, None))
    check("registry/second-copy-differing", errors == [] and len(findings) == 1
          and findings[0].startswith(SECOND_ROOT + "/baseline.css: SHA-256"), (findings, errors))

    stray = dict(FIXTURE_FILES, **{"search.js": b"x\n"})
    findings, errors = _scan_case(pins=TWO_PINS, second=(stray, None))
    check("registry/second-copy-stray", errors == [] and findings == [
        "{}/search.js: stray file, not listed in {}".format(SECOND_ROOT, SECOND_PIN)], (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, second=(None, False))
    check("registry/second-pin-missing", findings == [] and errors == [SECOND_PIN + ": missing"],
          (findings, errors))

    # The second pin names the first copy root: it would re-validate the first copy and leave the second
    # unchecked, so it is refused even though every digest it lists matches.
    findings, errors = _scan_case(pins=TWO_PINS, second=(None, _pin_text(FIXTURE_FILES)))
    check("registry/pin-names-other-root", findings == [] and len(errors) == 1
          and errors[0].startswith(SECOND_PIN + ": names the copy root " + FIXTURE_ROOT), (findings, errors))

    # The first pin names a root that is not its own, with the files present there.
    findings, errors = _scan_case(pins=TWO_PINS, second=(None, None),
                                  pin=_pin_text(FIXTURE_FILES, root=SECOND_ROOT))
    check("registry/first-pin-names-second-root", findings == [] and len(errors) == 1
          and "registered root is " + FIXTURE_ROOT in errors[0], (findings, errors))

    for name, pins in (("registry/duplicate-root", ((PIN_REL, FIXTURE_ROOT), (SECOND_PIN, FIXTURE_ROOT))),
                       ("registry/duplicate-pin", ((PIN_REL, FIXTURE_ROOT), (PIN_REL, SECOND_ROOT))),
                       ("registry/empty", ())):
        findings, errors = _scan_case(pins=pins, second=(None, None))
        check(name, findings == [] and len(errors) == 1 and "registry" in errors[0], (findings, errors))

    # A build tool pinned outside the copy root: hashed as 1 regular file at its own path, with no stray scan
    # of its directory (the pin itself sits beside it); only a BUILD_TOOLS path may sit outside a copy root.
    check("tool/registered-path", BUILD_TOOLS == (".baseline/check_palette.py",), BUILD_TOOLS)
    tool_pin = _pin_text(FIXTURE_FILES, tools=FIXTURE_TOOLS)
    findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS)
    check("tool/pinned-passes", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, pin=tool_pin, tools=FIXTURE_TOOLS,
                                  second=(None, _pin_text(FIXTURE_FILES, root=SECOND_ROOT, tools=FIXTURE_TOOLS)))
    check("tool/both-pins-pass", findings == [] and errors == [], (findings, errors))

    findings, errors = _scan_case(pin=tool_pin, tools={TOOL_REL: b"import os\n"})
    check("tool/differing-refused", errors == [] and len(findings) == 1
          and findings[0].startswith(TOOL_REL + ": SHA-256"), (findings, errors))

    findings, errors = _scan_case(pin=tool_pin)
    check("tool/missing-refused", errors == [] and findings == [TOOL_REL + ": missing"], (findings, errors))

    def _link_tool(tmp):
        real = tmp / "real-tool.py"
        os.rename(tmp / TOOL_REL, real)
        os.symlink(real, tmp / TOOL_REL)

    findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS, mutate=_link_tool)
    check("tool/symlink-refused", errors == [] and findings == [TOOL_REL + ": is a symbolic link"],
          (findings, errors))

    def _fifo_tool(tmp):
        os.unlink(tmp / TOOL_REL)
        os.mkfifo(tmp / TOOL_REL)

    findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS, mutate=_fifo_tool)
    check("tool/fifo-not-regular-refused", errors == [] and findings == [TOOL_REL + ": not a regular file"],
          (findings, errors))

    def _deny_tool_hash(path):
        if str(path).endswith(TOOL_REL):
            raise PermissionError(13, "read refused by the self-test", str(path))
        return original_hash(path)

    globals()["_sha256"] = _deny_tool_hash
    try:
        findings, errors = _scan_case(pin=tool_pin, tools=FIXTURE_TOOLS)
    finally:
        globals()["_sha256"] = original_hash
    check("tool/unreadable-cannot-evaluate", findings == [] and len(errors) == 1
          and errors[0].startswith(TOOL_REL + ": unreadable"), (findings, errors))

    # Only the registered path is exempt: a sibling under .baseline/, a near-miss name and a copy-root-less
    # pin stay cannot-evaluate; a malformed tool digest is refused like any other.
    tool_digest = hashlib.sha256(FIXTURE_TOOLS[TOOL_REL]).hexdigest()
    for name, pin in (
            ("tool/other-baseline-path-malformed",
             tool_pin.replace('"{}"'.format(TOOL_REL), '".baseline/check_other.py"')),
            ("tool/near-miss-path-malformed",
             tool_pin.replace('"{}"'.format(TOOL_REL), '"baseline/check_palette.py"')),
            ("tool/only-tool-malformed",
             'version = "{}"\n\n[files]\n"{}" = "{}"\n'.format(FIXTURE_VERSION, TOOL_REL, tool_digest)),
            ("tool/upper-digest-malformed", tool_pin.replace(tool_digest, tool_digest.upper()))):
        findings, errors = _scan_case(pin=pin, tools=FIXTURE_TOOLS)
        check(name, findings == [] and len(errors) == 1 and errors[0].startswith(PIN_REL + ":"), (findings, errors))

    # The version rule: every pin names the version the attribution file records, and the pins agree. Each
    # case's digests all match, so only the version can refuse it.
    findings, errors = _scan_case(attribution=_attribution_text(version="1.2.2"))
    check("version/pin-differs-from-attribution", findings == [] and errors == [
        "{}: version {} differs from the site template version 1.2.2 recorded in {}".format(
            PIN_REL, FIXTURE_VERSION, ATTRIBUTION_WHERE)], (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, second=(None, None), attribution=_attribution_text(version="1.2.2"))
    check("version/both-pins-differ-from-attribution", findings == [] and len(errors) == 2
          and errors[0].startswith(PIN_REL + ": version " + FIXTURE_VERSION + " differs")
          and errors[1].startswith(SECOND_PIN + ": version " + FIXTURE_VERSION + " differs"), (findings, errors))

    findings, errors = _scan_case(pins=TWO_PINS, second=(None, _pin_text(FIXTURE_FILES, root=SECOND_ROOT,
                                                                         version="1.2.2")))
    check("version/pins-disagree", findings == [] and len(errors) == 2 and errors[0] == (
        "the pins disagree: {} names version {} but {} names version 1.2.2; every registered pin must name "
        "the same version".format(PIN_REL, FIXTURE_VERSION, SECOND_PIN))
          and errors[1].startswith(SECOND_PIN + ": version 1.2.2 differs"), (findings, errors))

    findings, errors = _scan_case(attribution=False)
    check("version/attribution-missing", findings == [] and errors == [ATTRIBUTION_REL + ": missing"],
          (findings, errors))

    original_attribution = _read_attribution_bytes
    globals()["_read_attribution_bytes"] = _deny
    try:
        findings, errors = _scan_case()
    finally:
        globals()["_read_attribution_bytes"] = original_attribution
    check("version/attribution-unreadable", findings == [] and len(errors) == 1
          and errors[0].startswith(ATTRIBUTION_REL + ": unreadable"), (findings, errors))

    def _link_attribution(tmp):
        real = tmp / "real-attribution.toml"
        os.rename(tmp / ATTRIBUTION_REL, real)
        os.symlink(real, tmp / ATTRIBUTION_REL)

    findings, errors = _scan_case(mutate=_link_attribution)
    check("version/attribution-not-regular", findings == []
          and errors == [ATTRIBUTION_REL + ": not a regular file"], (findings, errors))

    good_attribution = _attribution_text()
    for name, attribution in (
            ("version/attribution-not-utf8", good_attribution.encode("utf-8") + b"# \xff\n"),
            ("version/attribution-not-toml", good_attribution + "[vendored\n"),
            ("version/attribution-block-missing", _attribution_text(block="other-template")),
            ("version/attribution-vendored-not-table", 'vendored = "site-template"\n'),
            ("version/attribution-block-not-table", '[vendored]\n"site-template" = "{}"\n'.format(FIXTURE_VERSION)),
            ("version/attribution-version-missing",
             good_attribution.replace('version = "{}"\n'.format(FIXTURE_VERSION), "")),
            ("version/attribution-version-not-string",
             good_attribution.replace('version = "{}"'.format(FIXTURE_VERSION), "version = 1")),
            ("version/attribution-version-shape", _attribution_text(version="1.2"))):
        findings, errors = _scan_case(attribution=attribution)
        check(name, findings == [] and len(errors) == 1 and errors[0].startswith(ATTRIBUTION_REL + ":"),
              (findings, errors))

    # The attribution size bound at its ends: exactly at MAX_ATTRIBUTION_BYTES passes, one byte over refuses.
    pad = MAX_ATTRIBUTION_BYTES - len(good_attribution.encode("utf-8"))
    for name, extra, refused in (("bound/attribution-size-at", pad, False),
                                 ("bound/attribution-size-over", pad + 1, True)):
        body = good_attribution + "#" + "x" * (extra - 2) + "\n"
        findings, errors = _scan_case(attribution=body)
        check(name, findings == [] and len(body.encode("utf-8")) == MAX_ATTRIBUTION_BYTES + extra - pad
              and errors == ([ATTRIBUTION_REL + ": larger than {} bytes".format(MAX_ATTRIBUTION_BYTES)]
                             if refused else []), (len(body), findings, errors))

    # The homepage rule: the attribution table's archive URL ends in the recorded version's own archive name.
    # Every pin matches, so only the homepage can refuse a case.
    archive = "https://example.org/baseline/baseline-{}.tar.gz".format(FIXTURE_VERSION)
    findings, errors = _scan_case(attribution=_attribution_text(homepage=archive))
    check("homepage/matching-passes", findings == [] and errors == [], (findings, errors))

    ending = "/baseline-{}.tar.gz".format(FIXTURE_VERSION)
    for name, homepage, found in (
            ("homepage/other-version-refused", archive.replace(FIXTURE_VERSION, "1.2.2"), None),
            ("homepage/missing-refused", False, "missing"),
            ("homepage/other-file-name-refused", archive.replace(".tar.gz", ".zip"), None),
            ("homepage/name-prefix-refused", archive.replace(ending, "/x" + ending[1:]), None),
            ("homepage/trailing-suffix-refused", archive + ".sig", None)):
        findings, errors = _scan_case(attribution=_attribution_text(homepage=homepage))
        check(name, findings == [] and errors == [
            '{}: [vendored."{}"] homepage must end in {} for version {}, but it is {}'.format(
                ATTRIBUTION_REL, ATTRIBUTION_BLOCK, ending, FIXTURE_VERSION, found or repr(homepage))],
              (findings, errors))

    findings, errors = _scan_case(attribution=_attribution_text().replace('homepage = "{}"'.format(archive),
                                                                         "homepage = 1"))
    check("homepage/not-string-refused", findings == [] and len(errors) == 1
          and errors[0].startswith(ATTRIBUTION_REL + ":") and errors[0].endswith("but it is 1"), (findings, errors))

    _coupling_cases()
    _probe_cases()

    # The gate's own report lines go to a buffer: a self-test run writes nothing to its error stream.
    with tempfile.TemporaryDirectory(prefix="baseline-pin-selftest-") as tmp, \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stream:
        _fixture(tmp)
        codes = [run_gate(tmp, FIXTURE_PINS)]
        with open(Path(tmp) / FIXTURE_ROOT / "baseline.css", "ab") as handle:
            handle.write(b"x")
        codes.append(run_gate(tmp, FIXTURE_PINS))
        os.unlink(Path(tmp) / PIN_REL)
        codes.append(run_gate(tmp, FIXTURE_PINS))
        codes.append("CANNOT EVALUATE" in stream.getvalue())
    check("exit/codes-0-1-2", codes == [0, 1, 2, True], codes)

    got = run_gate(ROOT)
    check("live/real-tree-passes", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_baseline_pin {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_baseline_pin {} checks (the exact fixture passes; a differing file, a "
          "missing file, a stray file, a symbolic link at a file, a directory, the root or a parent folder of "
          "the root (the top-level folder included), a parent folder that is a regular file, a FIFO and a "
          "missing root each fail, and a link beside a parent folder passes; a missing, unreadable, non-UTF-8, non-TOML or misshapen pin and an "
          "unreadable copied file are cannot-evaluate; the pin size bound holds one byte under, at and "
          "over; 2 registered pairs pass together, a second copy's defect is reported against its own root "
          "and pin, and a pin naming another root or a registry repeating a pin or a root is "
          "cannot-evaluate; a pinned build tool passes in 1 or both pins and fails when differing, missing, "
          "a symbolic link or not a regular file, is cannot-evaluate when unreadable, and any other path "
          "outside a copy root, a tool-only pin or a misshapen tool digest is cannot-evaluate; a pin whose version differs from the attribution file's site template version, "
          "2 pins naming different versions, and an attribution file that is missing, unreadable, not a "
          "regular file, non-UTF-8, non-TOML, over its size bound, without the site template table or with "
          "a missing or misshapen version there are cannot-evaluate; a homepage ending in the recorded "
          "version's archive name passes, and one naming another version, another file name or a prefixed "
          "name, missing or not a string is cannot-evaluate; against a base commit, a digest row changed "
          "under an unchanged version (a copied file, a build tool, or a pin absent at the base against "
          "another pin there) fails and passes with the coupling leg reverted, while no change, a version "
          "move, a comment edit, a removed row and an added row (with a notice) pass, and an unresolvable "
          "or option-shaped base, a --base commit that is not an ancestor of HEAD (a divergent push) and a "
          "malformed base pin are cannot-evaluate and pass with the leg reverted; --local-base compares the "
          "fork point (a changed row fails and passes with the leg reverted, a diverged ref is compared at "
          "the fork point), and a full ref name the clone lacks, a short name shadowed by a tag or a local "
          "branch, a full name reachable only through a local branch, a ref whose commit object is "
          "missing or that names a blob, a broken loose ref, a broken packed-refs line and a dangling "
          "symbolic ref are cannot-evaluate, and so is a fork point holding HEAD's own tree (a symbolic ref to "
          "the checked-out branch, a ref naming HEAD, or HEAD an empty commit over the ref); a replaced base "
          "tree, a replaced base pin blob and a grafted parent are ignored; a child run under an ambient "
          "GIT_DIR naming a "
          "decoy repository reads its own base; --probe-local-base exits 0 silently for a usable ref, 1 "
          "silently for an absent one, 2 with one CANNOT EVALUATE line for a broken loose ref, a broken "
          "packed-refs line, a dangling symbolic ref, a packed-refs that is a directory, a directory at the "
          "ref's loose path, a full name reachable only through a local branch and "
          "a short name, and 0 under an ambient GIT_DIR naming a decoy repository that lacks the ref; "
          "--local-base agrees with the probe on every one of those fixtures; the runner's extracted "
          "baseline-pin block, with real git, fails a changed row against a usable ref and a packed-refs "
          "that is a directory, and passes a genuine absence with its NOTE; packed-refs shapes are checked "
          "and a reftable ref store is refused, never read as absence, as are a per-worktree name that reads as "
          "absent, a symbolically linked and an unlistable directory on the ref's loose path, while a present "
          "per-worktree name is usable; an unexpected exception in the "
          "--local-base reads makes the probe and --local-base exit 2 on one CANNOT EVALUATE line, and one in "
          "a --base run exits 2 on one CANNOT EVALUATE line naming it; "
          "without --base the leg is reported as not evaluated; the exit codes are 0, 1 and 2; "
          "the live tree passes)".format(len(EXECUTED)))
    return 0


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    # The base-coupling leg: --base REF or --local-base REF, alone, or the local runner's --probe-local-base REF
    # (the REF itself is checked where it is used).
    if len(argv) == 2 and argv[0] == "--base":
        return run_base_gate(ROOT, argv[1])
    if len(argv) == 2 and argv[0] == "--local-base":
        return run_gate(ROOT, local_base=argv[1])
    if len(argv) == 2 and argv[0] == "--probe-local-base":
        return probe_local_base(ROOT, argv[1])
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


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The base-coupling leg starts git and reads
# its exit status (subprocess), so under an ignored SIGCHLD a failing git read would read as a pass; the
# guard runs at the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("check_baseline_pin.py") or main())
