#!/usr/bin/env python3
"""Release-stage gate: is a release's ROOT on a local copy of the published hash list?

  check_published_digest.py --list PATH --version X.Y.Z --project NAME --tree DIR --commit FULLSHA
  check_published_digest.py --list PATH --version X.Y.Z --project NAME --digest sha256:HEX [--digest ...]
  check_published_digest.py --self-test
  check_published_digest.py [--self-test] --execution-report ABS_PATH
  exit 0 every expected digest is listed for the version, 1 a digest or commit is missing or mismatched, 2
  cannot evaluate (any other argument list included)

The gate never fetches anything. The maintainer fetches the published hash list outside the tool, keeps
the fetch time and the body's SHA-256 (RELEASING.md step 6c), and passes the saved copy with --list; the
gate prints the SHA-256 of the bytes it read so the record can be compared. The list carries one digest
for this pack's project at a version: ROOT, the SHA-256 of the raw bytes of .aiqt/manifest.toml. The
expected digests come either from --tree, a checkout of the tagged commit, or from --digest, given once
per digest as sha256:<64 lowercase hex>, which must then name every digest the list carries for the
project at the version. --project is required: it is the name the list gives this pack (opfiles in the
copy saved on 2026-10-12), and the gate assumes no default.

With --tree the one digest expected on the list is ROOT, read from .aiqt/release/root.txt. The tree is
first checked locally, and each failure here is cannot-evaluate: VERSION and the version of the latest
[[release]] in changelog.toml must both equal --version; that release must record [release.artifacts];
ROOT must be the SHA-256 of the tree's .aiqt/manifest.toml; and each [release.artifacts] digest must equal
the sha256 that each [[sources]] or [[artifacts]] entry of that manifest gives for the same path, with at
least one such entry. The artifact digests are bound to the listed ROOT through the manifest it hashes and
are never looked for on the list. A download the list carries as a project of its own (one row per
version of that download) is checked only when asked, through --digest with that project and version.

The list format read is the one a copy saved on 2026-10-12 shows (tools/published-digest-sample-20261012.txt
holds that copy). A line whose first character is # is a comment. Exactly one comment, before the first
row, is the field-name line: after the #, the token fields: and then the names project version commit status
digest, in that order, separated by spaces or tabs. A list without that line, with a second one, with one
after a row, or with any other names or order after fields: is cannot-evaluate. Blank lines are skipped.
Every other line is a row of exactly five fields separated by spaces or tabs: a project name, a version
(X.Y.Z, optionally with a pre-release part), a commit id of 7 to 64 lowercase hex digits (the list writes a
short id), a status of release or pre-release, and a digest written as sha256:<64 lowercase hex>. A row
that does not fit makes the whole list cannot-evaluate. Only rows whose project equals --project are
judged; other projects' rows are held to the same shape and otherwise ignored.

Per expected digest: it passes when a release row of the project at that version carries it. A digest the
project lists at that version only on pre-release rows is cannot-evaluate (exit 2), since a pre-release row
does not publish the release. A digest listed only under another version is a finding (wrong version), and
one listed nowhere for the project is a finding (missing). A release row at that version whose digest is
none of the expected digests is a finding (mismatched), and so are release rows at that version whose
commit ids cannot all name one commit (two ids conflict when neither is a prefix of the other). With
--tree, --commit names the full id of the tagged commit, and each release row of the project at that
version must carry a commit id that is a prefix of it, or that row is a finding (mismatched commit). A list
that cannot be stat'ed or read, is not a regular file, is over MAX_BYTES, is not UTF-8, holds a CR or NUL,
or holds no row; a tree file that is missing, unreadable or malformed, or that does not bind the version;
a tree whose manifest does not hash to ROOT or does not carry the recorded artifact digests; and a bad
argument are cannot-evaluate (exit 2), never a pass.

Not covered: the gate cannot show that the saved copy is what the list served, or when; that rests on the
fetch record the maintainer keeps. The commit comparison shows only that the list's short id agrees with
the --commit the maintainer gives: it does not show that --tree is a checkout of that commit, or of the
tag, rather than another tree declaring the same version; a short id is unique only among the commits that
exist when it is written, so a 7-digit prefix can also match another commit; and the column is the list's
own statement, not a signature. Each file is checked with lstat and then opened, so a concurrent writer
must be excluded by the caller.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_published_digest.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1048576
TREE_MAX_BYTES = 16777216
ROOT_REL = ".aiqt/release/root.txt"
MANIFEST_REL = ".aiqt/manifest.toml"
MANIFEST_TABLES = ("sources", "artifacts")
VERSION_REL = "VERSION"
CHANGELOG_REL = "changelog.toml"
SAMPLE_REL = "tools/published-digest-sample-20261012.txt"
FIELDS_TOKEN = "fields:"
FIELD_NAMES = ("project", "version", "commit", "status", "digest")
RELEASE, PRE_RELEASE = "release", "pre-release"
STATUSES = (RELEASE, PRE_RELEASE)
_CORE = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
VERSION_RE = re.compile(_CORE, re.ASCII)
ROW_VERSION_RE = re.compile(_CORE + r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", re.ASCII)
PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)
ROW_COMMIT_RE = re.compile(r"[0-9a-f]{7,64}", re.ASCII)
FULL_COMMIT_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.ASCII)
PREFIXED_RE = re.compile(r"sha256:([0-9a-f]{64})", re.ASCII)
SEPARATOR_RE = re.compile(r"[ \t]+")
USAGE = ("usage: check_published_digest.py --list PATH --version X.Y.Z --project NAME (--tree DIR --commit "
         "FULLSHA | --digest sha256:HEX [--digest ...]) | --self-test")


class CannotEvaluate(Exception):
    """An input that cannot be judged: unreadable, oversized, malformed, or a bad argument."""


class Row:
    __slots__ = ("project", "version", "commit", "status", "digest")

    def __init__(self, project, version, commit, status, digest):
        self.project, self.version, self.commit, self.status, self.digest = project, version, commit, status, digest


def _read_bounded(path, limit):
    """The file's bytes, at most limit + 1 of them (so an oversized file is seen, never read whole)."""
    with open(path, "rb") as handle:
        return handle.read(limit + 1)


def _read_regular(path, limit, where):
    """The bytes of a regular file at path; CannotEvaluate when it is absent, not regular, unreadable or too big."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat ({})".format(where, exc)) from None
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(where))
    try:
        data = _read_bounded(path, limit)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(where, exc)) from None
    if len(data) > limit:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, limit))
    return data


def _parse_row(line, where):
    fields = SEPARATOR_RE.split(line.strip(" \t"))
    if len(fields) != 5:
        raise CannotEvaluate("{}: expected 5 fields (project version commit status digest), found {}".format(
            where, len(fields)))
    project, version, commit, status, digest = fields
    if PROJECT_RE.fullmatch(project) is None:
        raise CannotEvaluate("{}: malformed project {!r}".format(where, project))
    if ROW_VERSION_RE.fullmatch(version) is None:
        raise CannotEvaluate("{}: malformed version {!r}".format(where, version))
    if ROW_COMMIT_RE.fullmatch(commit) is None:
        raise CannotEvaluate("{}: malformed commit {!r} (expected 7 to 64 lowercase hex digits)".format(
            where, commit))
    if status not in STATUSES:
        raise CannotEvaluate("{}: unknown status {!r} (expected one of {})".format(
            where, status, ", ".join(STATUSES)))
    match = PREFIXED_RE.fullmatch(digest)
    if match is None:
        raise CannotEvaluate("{}: malformed digest {!r} (expected sha256:<64 lowercase hex>)".format(
            where, digest))
    return Row(project, version, commit, status, match.group(1))


def _field_names_ok(names):
    """True when the names after fields: on the field-name comment are the five columns in order."""
    return tuple(names) == FIELD_NAMES


def parse_list(data, where):
    """The rows of a published-list body, in file order; CannotEvaluate on anything but the list format."""
    if len(data) > MAX_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_BYTES))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None
    if "\r" in text or "\x00" in text:
        raise CannotEvaluate("{}: carries a CR or NUL character".format(where))
    rows, field_line = [], None
    for number, line in enumerate(text.split("\n"), 1):
        here = "{}:{}".format(where, number)
        if line.startswith("#"):
            tokens = SEPARATOR_RE.split(line[1:].strip(" \t"))
            if tokens[0] != FIELDS_TOKEN:
                continue
            if field_line is not None:
                raise CannotEvaluate("{}: a second field-name line (the first is line {})".format(here, field_line))
            if not _field_names_ok(tokens[1:]):
                raise CannotEvaluate("{}: the field-name line names {!r}, not {}".format(
                    here, " ".join(tokens[1:]), " ".join(FIELD_NAMES)))
            field_line = number
            continue
        if line.strip(" \t") == "":
            continue
        if field_line is None:
            raise CannotEvaluate("{}: a row before any field-name line (# {} {})".format(
                here, FIELDS_TOKEN, " ".join(FIELD_NAMES)))
        rows.append(_parse_row(line, here))
    if field_line is None:
        raise CannotEvaluate("{}: no field-name line (# {} {})".format(where, FIELDS_TOKEN, " ".join(FIELD_NAMES)))
    if not rows:
        raise CannotEvaluate("{}: holds no row".format(where))
    return rows


def _version_matches(row, version):
    return row.version == version


def _is_release(row):
    return row.status == RELEASE


def _same_digest(listed, expected):
    return listed == expected


def _commit_matches(listed, tagged):
    """True when the list's commit id is a prefix of (or equal to) the full tagged commit id."""
    return tagged.startswith(listed)


def _commits_conflict(first, second):
    """True when two commit ids cannot name one commit: neither is a prefix of the other."""
    return not (first.startswith(second) or second.startswith(first))


def _unexpected(released, expected):
    """The digests on the version's release rows that match none of the expected digests, sorted."""
    return sorted({row.digest for row in released
                   if not any(_same_digest(row.digest, want) for _label, want in expected)})


def evaluate(rows, version, expected, project, tagged=None):
    """(findings, errors) for the expected (label, hex) digests of one project version against the rows;
    tagged, when given, is the full commit id each release row's commit id must be a prefix of."""
    findings, errors = [], []
    mine = [row for row in rows if row.project == project]
    at_version = [row for row in mine if _version_matches(row, version)]
    released = [row for row in at_version if _is_release(row)]
    for label, want in expected:
        if any(_same_digest(row.digest, want) for row in released):
            continue
        if any(_same_digest(row.digest, want) for row in at_version):
            errors.append("pre-release only: {} sha256:{} is listed for {} {} only on a pre-release row, which "
                          "does not publish the release".format(label, want, project, version))
            continue
        others = sorted({row.version for row in mine if _same_digest(row.digest, want)})
        if others:
            findings.append("wrong version: {} sha256:{} is listed for {} under {}, not {}".format(
                label, want, project, ", ".join(others), version))
        else:
            findings.append("missing: {} sha256:{} is not listed for {} {}".format(label, want, project, version))
    for digest in _unexpected(released, expected):
        findings.append("mismatched: {} {} lists sha256:{}, which is none of the expected digests".format(
            project, version, digest))
    commits = sorted({row.commit for row in released})
    if any(_commits_conflict(first, second) for first in commits for second in commits if first < second):
        findings.append("mismatched: the release rows of {} {} name commit ids that cannot all be one commit "
                        "({})".format(project, version, ", ".join(commits)))
    if tagged is not None:
        for commit in commits:
            if not _commit_matches(commit, tagged):
                findings.append("mismatched commit: a release row of {} {} names commit {}, which is not a "
                                "prefix of the tagged commit {}".format(project, version, commit, tagged))
    return findings, errors


def _toml(data, where):
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: invalid UTF-8/TOML ({})".format(where, exc)) from None


def _root_binds_manifest(root, manifest_bytes):
    """True when ROOT is the SHA-256 of the exact manifest bytes."""
    return hashlib.sha256(manifest_bytes).hexdigest() == root


def _manifest_digests(manifest, path):
    """The sha256 values the manifest's [[sources]] and [[artifacts]] entries give for path."""
    found = []
    for table in MANIFEST_TABLES:
        entries = manifest.get(table, [])
        if not isinstance(entries, list):
            raise CannotEvaluate("{}: {} is not an array of tables".format(MANIFEST_REL, table))
        found.extend(entry.get("sha256") for entry in entries
                     if isinstance(entry, dict) and entry.get("path") == path)
    return found


def _artifact_bound(recorded, listed):
    """True when the manifest has an entry for the path and every such entry carries the recorded digest."""
    return bool(listed) and all(value == recorded for value in listed)


def _listed_expectations(root, artifacts):
    """The (label, hex) digests the hash list must carry for a tree: ROOT alone. The list publishes one
    digest per project version; the artifacts are bound to ROOT through the manifest, checked locally."""
    return [root]


def expected_from_tree(tree, version):
    """[(label, hex)] the list must carry for the tagged tree (ROOT alone), after checking locally that
    the tree binds the version, that ROOT is the SHA-256 of its manifest, and that each [release.artifacts]
    digest of the latest release equals the manifest's digest for that path."""
    tree = Path(tree)
    raw = _read_regular(tree / ROOT_REL, TREE_MAX_BYTES, ROOT_REL)
    match = re.fullmatch(rb"sha256:([0-9a-f]{64})\n", raw)
    if match is None:
        raise CannotEvaluate("{}: not exactly one sha256:<64 lowercase hex> line".format(ROOT_REL))
    root = ("ROOT ({})".format(ROOT_REL), match.group(1).decode("ascii"))
    raw = _read_regular(tree / VERSION_REL, TREE_MAX_BYTES, VERSION_REL)
    if raw != (version + "\n").encode("ascii"):
        raise CannotEvaluate("{}: does not hold exactly {} (holds {!r})".format(VERSION_REL, version, raw[:80]))
    data = _toml(_read_regular(tree / CHANGELOG_REL, TREE_MAX_BYTES, CHANGELOG_REL), CHANGELOG_REL)
    releases = data.get("release")
    if not isinstance(releases, list) or not releases or not isinstance(releases[-1], dict):
        raise CannotEvaluate("{}: no [[release]] table".format(CHANGELOG_REL))
    latest = releases[-1]
    if latest.get("version") != version:
        raise CannotEvaluate("{}: the latest release is {!r}, not {}".format(
            CHANGELOG_REL, latest.get("version"), version))
    artifacts = latest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise CannotEvaluate("{}: release {} records no [release.artifacts] digests (RELEASING.md step 3 "
                             "records them before the tag)".format(CHANGELOG_REL, version))
    recorded = []
    for path in sorted(artifacts):
        value = artifacts[path]
        found = PREFIXED_RE.fullmatch(value) if isinstance(value, str) else None
        if found is None:
            raise CannotEvaluate("{}: artifact {!r} has no sha256:<64 lowercase hex> digest".format(
                CHANGELOG_REL, path))
        recorded.append((path, found.group(1)))
    manifest_bytes = _read_regular(tree / MANIFEST_REL, TREE_MAX_BYTES, MANIFEST_REL)
    if not _root_binds_manifest(root[1], manifest_bytes):
        raise CannotEvaluate("{}: holds sha256:{}, which is not the SHA-256 of {} (sha256:{})".format(
            ROOT_REL, root[1], MANIFEST_REL, hashlib.sha256(manifest_bytes).hexdigest()))
    manifest = _toml(manifest_bytes, MANIFEST_REL)
    for path, want in recorded:
        listed = _manifest_digests(manifest, path)
        if not _artifact_bound(want, listed):
            raise CannotEvaluate("{}: artifact {!r} records sha256:{}, but {} gives {} for that path".format(
                CHANGELOG_REL, path, want, MANIFEST_REL,
                ", ".join(repr(value) for value in listed) if listed else "no entry"))
    return _listed_expectations(root, recorded)


def parse_args(argv):
    """The options of a gate run; CannotEvaluate on any argument list outside the usage."""
    single = {"--list": None, "--version": None, "--tree": None, "--commit": None, "--project": None}
    digests = []
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag not in single and flag != "--digest":
            raise CannotEvaluate("unrecognized argument {!r}".format(flag))
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            raise CannotEvaluate("{} needs a value".format(flag))
        value = argv[index + 1]
        if flag == "--digest":
            digests.append(value)
        elif single[flag] is not None:
            raise CannotEvaluate("{} given twice".format(flag))
        else:
            single[flag] = value
        index += 2
    if single["--list"] is None or single["--version"] is None or single["--project"] is None:
        raise CannotEvaluate("--list, --version and --project are required")
    if VERSION_RE.fullmatch(single["--version"]) is None:
        raise CannotEvaluate("--version {!r} is not a bare X.Y.Z release version".format(single["--version"]))
    if (single["--tree"] is None) == (not digests):
        raise CannotEvaluate("give exactly one of --tree or --digest")
    if (single["--tree"] is None) != (single["--commit"] is None):
        raise CannotEvaluate("--commit is required with --tree and accepted only with it")
    if single["--commit"] is not None and FULL_COMMIT_RE.fullmatch(single["--commit"]) is None:
        raise CannotEvaluate("--commit {!r} is not a full commit id (40 or 64 lowercase hex)".format(
            single["--commit"]))
    if PROJECT_RE.fullmatch(single["--project"]) is None:
        raise CannotEvaluate("--project {!r} is malformed".format(single["--project"]))
    given = []
    for number, value in enumerate(digests, 1):
        match = PREFIXED_RE.fullmatch(value)
        if match is None:
            raise CannotEvaluate("--digest {!r} is not sha256:<64 lowercase hex>".format(value))
        given.append(("given digest {}".format(number), match.group(1)))
    return {"list": single["--list"], "version": single["--version"], "tree": single["--tree"],
            "commit": single["--commit"], "project": single["--project"], "given": given}


def run_gate(argv):
    try:
        options = parse_args(argv)
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}; {}".format(exc, USAGE), file=sys.stderr)
        return 2
    try:
        data = _read_regular(options["list"], MAX_BYTES, options["list"])
        print("list sha256: {} ({} bytes read from {})".format(
            hashlib.sha256(data).hexdigest(), len(data), options["list"]))
        rows = parse_list(data, options["list"])
        expected = (options["given"] if options["tree"] is None
                    else expected_from_tree(options["tree"], options["version"]))
    except CannotEvaluate as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    findings, errors = evaluate(rows, options["version"], expected, options["project"], options["commit"])
    for line in errors:
        print("CANNOT EVALUATE: " + line, file=sys.stderr)
    for line in findings:
        print("FAIL: " + line)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: all {} expected digest(s) are listed on release rows of {} {}{}".format(
        len(expected), options["project"], options["version"],
        "" if options["commit"] is None else ", each naming a prefix of commit " + options["commit"]
        + "; the tree's ROOT is the SHA-256 of its manifest, which carries each [release.artifacts] digest"))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "published-digest-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


_V = "1.1.1"
_ZIP_HEX = hashlib.sha256(b"fixture skill zip").hexdigest()
_TXT_HEX = hashlib.sha256(b"fixture instructions").hexdigest()
_ZIP_PATH, _TXT_PATH = "site/downloads/aiqt-skill-1.1.1.zip", "site/downloads/aiqt-instructions.txt"
_ARTIFACTS = {_ZIP_PATH: "sha256:" + _ZIP_HEX, _TXT_PATH: "sha256:" + _TXT_HEX}
# The fixture manifest lists both downloads under [[sources]] and the zip under [[artifacts]] as well, as
# the real one does; the fixture ROOT is the SHA-256 of its bytes.
_MANIFEST = ('format-version = 1\n\n[[sources]]\npath = "README.md"\nbytes = 1\nsha256 = "{0}"\n\n'
             '[[sources]]\npath = "{1}"\nbytes = 1\nsha256 = "{2}"\n\n'
             '[[sources]]\npath = "{3}"\nbytes = 1\nsha256 = "{4}"\n\n'
             '[[artifacts]]\npath = "{1}"\nkind = "file"\nsha256 = "{2}"\n').format(
                 hashlib.sha256(b"fixture readme").hexdigest(), _ZIP_PATH, _ZIP_HEX, _TXT_PATH, _TXT_HEX)
_ROOT_HEX = hashlib.sha256(_MANIFEST.encode("utf-8")).hexdigest()
_OLD_HEX = hashlib.sha256(b"fixture older root").hexdigest()
_RC_HEX = hashlib.sha256(b"fixture candidate").hexdigest()
_EXTRA_HEX = hashlib.sha256(b"fixture extra").hexdigest()
_COMMIT = hashlib.sha1(b"fixture tagged commit").hexdigest()
_OTHER_COMMIT = hashlib.sha1(b"fixture other commit").hexdigest()
_SHORT, _OTHER_SHORT = _COMMIT[:7], _OTHER_COMMIT[:7]
_P = "opfiles"
# The saved copy of the published list (SAMPLE_REL): its bytes' SHA-256, the one row's ROOT digest and
# version, and a full commit id that begins with the row's short id 058abd9 (the fixture invents the rest).
_SAMPLE_SHA256 = "f434deb0ed85379d0d7ca602ea3e354926d21e92eb51ee47a86b3f7ba6cf7a81"
_SAMPLE_ROOT = "435cb9659316125701b1479a35da4be74ac4a6b62c6530cfaff1c5bb31e229dc"
_SAMPLE_VERSION = "1.0.0"
_SAMPLE_COMMIT = "058abd9" + hashlib.sha1(b"fixture sample commit").hexdigest()[7:]


def _row(version, status, digest, commit=_SHORT, project=_P):
    return "{} {} {} {} {}\n".format(project, version, commit, status, digest)


def _ours(version=_V, status=RELEASE, root="sha256:" + _ROOT_HEX, commit=_SHORT):
    """This project's row for a version: the list carries ROOT alone."""
    return [_row(version, status, root, commit=commit)]


_PREAMBLE = "# fixture hash list\n#\n# fields:  project   version   commit    status        digest\n\n"


def _list_body(ours):
    """A list holding comment lines with the field-name line, another project's row carrying the ROOT
    digest, an older release, a pre-release of a later version, and the given rows of this project."""
    return "".join([_PREAMBLE,
                    _row(_V, RELEASE, "sha256:" + _ROOT_HEX, project="another-project"),
                    _row("1.1.0", RELEASE, "sha256:" + _OLD_HEX, commit=_OTHER_SHORT),
                    _row("1.2.0-rc.1", PRE_RELEASE, "sha256:" + _RC_HEX, commit=_OTHER_SHORT)] + ours)


def _tree(tmp, version=_V, root=None, artifacts=True, version_file=None, manifest=_MANIFEST):
    """A fixture checkout: root.txt, VERSION, a changelog whose latest release records artifacts (True
    for the fixture's two, False for none, or a path-to-digest dict), and the manifest (None for none)."""
    tree = Path(tmp) / "tree"
    os.makedirs(tree / ".aiqt" / "release", exist_ok=True)
    with open(tree / ROOT_REL, "w", encoding="utf-8") as handle:
        handle.write("sha256:{}\n".format(_ROOT_HEX) if root is None else root)
    with open(tree / VERSION_REL, "w", encoding="utf-8") as handle:
        handle.write(version + "\n" if version_file is None else version_file)
    if manifest is not None:
        with open(tree / MANIFEST_REL, "w", encoding="utf-8") as handle:
            handle.write(manifest)
    lines = ['[[release]]\nversion = "0.9.0"\n', '[[release]]\nversion = "{}"\n'.format(version)]
    if artifacts:
        lines.append("[release.artifacts]\n" + "".join('"{}" = "{}"\n'.format(path, value) for path, value in
                                                       (_ARTIFACTS if artifacts is True else artifacts).items()))
    with open(tree / CHANGELOG_REL, "w", encoding="utf-8") as handle:
        handle.write("".join(lines))
    return str(tree)


def _gate(argv):
    """run_gate's exit code and its captured stdout plus stderr."""
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = run_gate(list(argv))
    return code, out.getvalue()


def _case(body, mode="tree", version=_V, tree_kwargs=None, commit=_COMMIT, project=_P, digests=None):
    """The gate's (exit, output) on a list body (str or bytes) against a fixture tree (with --commit) or
    the given digests (the fixture's ROOT by default)."""
    with tempfile.TemporaryDirectory(prefix="published-digest-selftest-") as tmp:
        path = os.path.join(tmp, "hashes.txt")
        with open(path, "wb") as handle:
            handle.write(body.encode("utf-8") if isinstance(body, str) else body)
        argv = ["--list", path, "--version", version, "--project", project]
        if mode == "tree":
            argv += ["--tree", _tree(tmp, **(tree_kwargs or {})), "--commit", commit]
        else:
            for hexdigest in (digests or (_ROOT_HEX,)):
                argv += ["--digest", "sha256:" + hexdigest]
        return _gate(argv)


def _parses(body):
    """True when parse_list accepts the body, False when it is cannot-evaluate."""
    try:
        parse_list(body.encode("utf-8"), "fixture")
    except CannotEvaluate:
        return False
    return True


def _self_test():
    good = _list_body(_ours())

    got = _case(good)
    check("pass/match-tree", got[0] == 0 and "PASS: all 1 expected digest(s)" in got[1]
          and "prefix of commit " + _COMMIT in got[1], got)

    got = _case(good, mode="given")
    check("pass/match-given-digests", got[0] == 0, got)

    got = _case(_list_body([]))
    check("refuse/missing", got[0] == 1 and "missing: ROOT (.aiqt/release/root.txt) sha256:" + _ROOT_HEX in got[1]
          and got[1].count("FAIL: ") == 1, got)

    near = _ROOT_HEX[:-1] + ("0" if _ROOT_HEX[-1] != "0" else "1")
    mismatched = _list_body(_ours(root="sha256:" + near))
    got = _case(mismatched)
    check("refuse/mismatched", got[0] == 1 and "missing: ROOT" in got[1]
          and "mismatched: {} {} lists sha256:{}".format(_P, _V, near) in got[1], got)

    extra = _list_body(_ours() + [_row(_V, RELEASE, "sha256:" + _EXTRA_HEX)])
    got = _case(extra)
    check("refuse/extra-digest-at-version", got[0] == 1 and "lists sha256:" + _EXTRA_HEX in got[1]
          and got[1].count("FAIL: ") == 1, got)

    got = _case(_list_body(_ours() + _ours(commit=_OTHER_SHORT)), mode="given")
    check("refuse/conflicting-commits", got[0] == 1 and "cannot all be one commit" in got[1]
          and got[1].count("FAIL: ") == 1, got)

    # Commit ids that are abbreviations of one commit (one a prefix of the other) do not conflict.
    got = _case(_list_body(_ours() + _ours(commit=_COMMIT[:12])))
    check("pass/commit-abbreviations-agree", got[0] == 0, got)

    # The tagged-commit prefix rule at its end: the full id (equal) passes, one digit longer fails, and a
    # short id of another commit fails.
    got = _case(_list_body(_ours() + _ours(commit=_COMMIT)))
    check("pass/commit-full-id", got[0] == 0, got)

    longer = _list_body(_ours(commit=_COMMIT + "0"))
    got = _case(longer)
    check("refuse/commit-longer-than-tagged", got[0] == 1 and got[1].count("FAIL: mismatched commit:") == 1, got)

    got = _case(good, commit=_OTHER_COMMIT)
    check("refuse/commit-not-prefix", got[0] == 1 and got[1].count("FAIL: ") == 1
          and "names commit {}, which is not a prefix of the tagged commit {}".format(_SHORT, _OTHER_COMMIT)
          in got[1], got)

    wrong_version = _list_body(_ours(version="1.1.2"))
    got = _case(wrong_version)
    check("refuse/wrong-version", got[0] == 1 and got[1].count("FAIL: wrong version:") == 1
          and "under 1.1.2, not 1.1.1" in got[1], got)

    pre_release = _list_body(_ours(status=PRE_RELEASE))
    got = _case(pre_release)
    check("refuse/pre-release-only", got[0] == 2 and got[1].count("pre-release only:") == 1, got)

    fields_line = "# fields:  project   version   commit    status        digest\n"
    for name, body in (
            ("refuse/malformed-four-fields", good + "{} {} {} {}\n".format(_P, _V, _SHORT, RELEASE)),
            ("refuse/malformed-six-fields", good + _row(_V, RELEASE, "sha256:" + _ZIP_HEX)[:-1] + " extra\n"),
            ("refuse/malformed-status", good.replace(" release ", " final ", 1)),
            ("refuse/malformed-uppercase-hex", good.replace(_OLD_HEX, _OLD_HEX.upper())),
            ("refuse/malformed-short-digest", good.replace(_OLD_HEX, _OLD_HEX[:-1])),
            ("refuse/malformed-unprefixed-digest", good.replace("sha256:" + _OLD_HEX, _OLD_HEX)),
            ("refuse/malformed-version", good.replace(" 1.1.0 ", " v1.1.0 ")),
            ("refuse/malformed-commit", good.replace(" {} ".format(_SHORT), " {}g ".format(_SHORT[:-1]), 1)),
            ("refuse/malformed-bare-header", good.replace(fields_line, fields_line[2:])),
            ("refuse/malformed-indented-comment", good.replace(fields_line, fields_line + " # note\n")),
            ("refuse/field-names-missing", good.replace(fields_line, "")),
            ("refuse/field-names-reordered", good.replace("commit    status", "status    commit")),
            ("refuse/field-names-extra", good.replace("status        digest", "status digest note")),
            ("refuse/field-names-twice", good.replace(fields_line, fields_line * 2)),
            ("refuse/field-names-after-row", good.replace(fields_line, "") + fields_line),
            ("refuse/malformed-crlf", good.replace("\n", "\r\n")),
            ("refuse/malformed-not-utf8", good.encode("utf-8") + b"\xff\n"),
            ("refuse/malformed-nul", good + "\x00\n"),
            ("refuse/malformed-no-row", _PREAMBLE)):
        got = _case(body)
        check(name, got[0] == 2 and "CANNOT EVALUATE" in got[1] and "PASS" not in got[1], got)

    # The commit column's length at both ends: 6 digits out, 7 at the lower end, 8 inside; 63 inside, 64
    # at the upper end, 65 out.
    seed = (_COMMIT + _OTHER_COMMIT)[:65]
    for name, length, accepted in (("bound/commit-length-6", 6, False), ("bound/commit-length-7", 7, True),
                                   ("bound/commit-length-8", 8, True), ("bound/commit-length-63", 63, True),
                                   ("bound/commit-length-64", 64, True), ("bound/commit-length-65", 65, False)):
        body = _PREAMBLE + _row(_V, RELEASE, "sha256:" + _ROOT_HEX, commit=seed[:length])
        check(name, _parses(body) is accepted, (length, accepted))

    # The saved copy of the published list, as fetched on 2026-10-12: its one opfiles row is a pre-release
    # row, so the version it names cannot be evaluated as released (exit 2), and the release row variant
    # (the same row with status release) passes from its given ROOT digest. For a tree, the variant's
    # digest is also replaced by the fixture tree's ROOT, since a fixture cannot hold the manifest whose
    # SHA-256 the saved row carries; no row is added, so the list still carries ROOT alone. Against a tree
    # that records [release.artifacts], it passes when --commit begins with the row's short id and fails
    # when it does not.
    try:
        sample = _read_regular(ROOT / SAMPLE_REL, MAX_BYTES, SAMPLE_REL).decode("utf-8")
    except (CannotEvaluate, UnicodeDecodeError) as exc:
        sample = "unreadable sample: {}".format(exc)
    check("sample/bytes-pinned", hashlib.sha256(sample.encode("utf-8")).hexdigest() == _SAMPLE_SHA256, sample[:80])

    got = _case(sample, mode="given", version=_SAMPLE_VERSION, digests=(_SAMPLE_ROOT,))
    check("sample/pre-release-exit-2", got[0] == 2 and "pre-release only: given digest 1 sha256:" + _SAMPLE_ROOT
          in got[1] and "list sha256: " + _SAMPLE_SHA256 in got[1], got)

    got = _case(sample, mode="given", version=_SAMPLE_VERSION, digests=(_SAMPLE_ROOT,), project="aiqt-guardrails")
    check("sample/other-project-name", got[0] == 1 and "missing: given digest 1" in got[1], got)

    released = sample.replace("   pre-release   ", "   release       ", 1)
    got = _case(released, mode="given", version=_SAMPLE_VERSION, digests=(_SAMPLE_ROOT,))
    check("sample/release-variant", released != sample and got[0] == 0, got)

    root_only = released.replace(_SAMPLE_ROOT, _ROOT_HEX, 1)
    sample_tree = {"version": _SAMPLE_VERSION}
    got = _case(root_only, version=_SAMPLE_VERSION, tree_kwargs=sample_tree, commit=_SAMPLE_COMMIT)
    check("sample/release-variant-tree-commit-prefix", root_only != released and got[0] == 0
          and "PASS: all 1 expected digest(s)" in got[1], got)

    got = _case(root_only, version=_SAMPLE_VERSION, tree_kwargs=sample_tree, commit=_COMMIT)
    check("sample/release-variant-tree-commit-differs", got[0] == 1
          and got[1].count("FAIL: mismatched commit:") == 1 and "names commit 058abd9" in got[1], got)

    # Regression: that ROOT-only release row against a tree with [release.artifacts] passes with ROOT and
    # the commit matching, and restoring the old expectation (each artifact digest on the list as well)
    # turns it red, naming both artifacts as missing.
    before = _case(root_only, version=_SAMPLE_VERSION, tree_kwargs=sample_tree, commit=_SAMPLE_COMMIT)
    original = _listed_expectations
    globals()["_listed_expectations"] = lambda root, artifacts: [root] + list(artifacts)
    try:
        got = _case(root_only, version=_SAMPLE_VERSION, tree_kwargs=sample_tree, commit=_SAMPLE_COMMIT)
    finally:
        globals()["_listed_expectations"] = original
    check("regress/root-only-row-artifact-expectation", before[0] == 0 and got[0] == 1
          and got[1].count("FAIL: missing: ") == 2 and "missing: " + _ZIP_PATH in got[1]
          and "missing: " + _TXT_PATH in got[1], (before, got))

    original = _read_bounded

    def _deny(path, limit):
        raise PermissionError(13, "read refused by the self-test", str(path))

    globals()["_read_bounded"] = _deny
    try:
        got = _case(good)
    finally:
        globals()["_read_bounded"] = original
    check("refuse/unreadable", got[0] == 2 and "unreadable" in got[1], got)

    with tempfile.TemporaryDirectory(prefix="published-digest-selftest-") as tmp:
        tail = ["--version", _V, "--project", _P, "--tree", _tree(tmp), "--commit", _COMMIT]
        missing = _gate(["--list", os.path.join(tmp, "absent.txt")] + tail)
        os.makedirs(os.path.join(tmp, "directory.txt"))
        directory = _gate(["--list", os.path.join(tmp, "directory.txt")] + tail)
    check("refuse/list-absent", missing[0] == 2 and "cannot stat" in missing[1], missing)
    check("refuse/list-not-regular", directory[0] == 2 and "not a regular file" in directory[1], directory)

    # A tree's local bindings: ROOT must hash its manifest, and each recorded artifact digest must be the
    # manifest's digest for that path (one recorded digest changed, or one path the manifest lacks).
    differs = dict(_ARTIFACTS, **{_TXT_PATH: "sha256:" + _EXTRA_HEX})
    unlisted = dict(_ARTIFACTS, **{"site/downloads/x.zip": "sha256:" + _EXTRA_HEX})
    for name, kwargs, needle in (
            ("tree/version-file-differs", {"version_file": "1.1.0\n"}, "VERSION: does not hold exactly"),
            ("tree/latest-release-differs", {"version": "1.1.2", "version_file": _V + "\n"},
             "the latest release is '1.1.2'"),
            ("tree/no-artifacts", {"artifacts": False}, "records no [release.artifacts]"),
            ("tree/root-malformed", {"root": "sha256:{}".format(_ROOT_HEX)}, "not exactly one sha256"),
            ("tree/manifest-missing", {"manifest": None}, MANIFEST_REL + ": cannot stat"),
            ("tree/root-not-manifest-digest", {"manifest": _MANIFEST + "\n"}, "which is not the SHA-256 of"),
            ("tree/artifact-differs-from-manifest", {"artifacts": differs},
             "artifact {!r} records sha256:{}, but {} gives {!r}".format(
                 _TXT_PATH, _EXTRA_HEX, MANIFEST_REL, _TXT_HEX)),
            ("tree/artifact-not-in-manifest", {"artifacts": unlisted},
             "artifact 'site/downloads/x.zip' records sha256:{}, but {} gives no entry".format(
                 _EXTRA_HEX, MANIFEST_REL))):
        got = _case(good, tree_kwargs=kwargs)
        check(name, got[0] == 2 and needle in got[1] and "PASS" not in got[1], got)

    with tempfile.TemporaryDirectory(prefix="published-digest-selftest-") as tmp:
        tree = _tree(tmp)
        with open(os.path.join(tree, CHANGELOG_REL), "w", encoding="utf-8") as handle:
            handle.write("[[release]\n")
        path = os.path.join(tmp, "hashes.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(good)
        got = _gate(["--list", path, "--version", _V, "--project", _P, "--tree", tree, "--commit", _COMMIT])
    check("tree/changelog-not-toml", got[0] == 2 and "invalid UTF-8/TOML" in got[1], got)

    # Each bad argument list names a real list and a real tree that pass (the two controls), so the
    # argument guard is all that stands between it and a gate run; a case counts only when that guard's
    # own message is printed, so removing any one guard turns the check red.
    with tempfile.TemporaryDirectory(prefix="published-digest-selftest-") as tmp:
        listed = os.path.join(tmp, "hashes.txt")
        with open(listed, "w", encoding="utf-8") as handle:
            handle.write(good)
        tree = _tree(tmp)
        digest = "sha256:" + _ROOT_HEX
        base = ["--list", listed, "--version", _V, "--project", _P]
        required = "--list, --version and --project are required"
        one_of = "give exactly one of --tree or --digest"
        with_tree = "--commit is required with --tree and accepted only with it"
        not_bare = "is not a bare X.Y.Z release version"
        not_full = "is not a full commit id"
        bad = (([], required), (["--list"], "--list needs a value"), (["--list", listed], required),
               (["--list", listed, "--version", _V], required),
               (["--list", listed, "--version", _V, "--digest", digest], required),
               (base, one_of),
               (["--list", listed, "--version", "1.1", "--project", _P, "--digest", digest], not_bare),
               (["--list", listed, "--version", "1.1.1-rc.1", "--project", _P, "--digest", digest], not_bare),
               (base + ["--digest", _ROOT_HEX], "is not sha256:<64 lowercase hex>"),
               (base + ["--digest", digest, "--tree", tree, "--commit", _COMMIT], one_of),
               (base + ["--tree", tree], with_tree),
               (base + ["--digest", digest, "--commit", _COMMIT], with_tree),
               (base + ["--tree", tree, "--commit", _SHORT], not_full),
               (base + ["--tree", tree, "--commit", _COMMIT.upper()], not_full),
               (["--list", listed, "--list", listed, "--version", _V, "--project", _P, "--digest", digest],
                "--list given twice"),
               (["--list", "--version", _V, "--project", _P, "--digest", digest], "--list needs a value"),
               (["--list", listed, "--version", _V, "--project", "-bad", "--digest", digest],
                "--project '-bad' is malformed"),
               (base + ["--digest", digest, "--bogus", "z"], "unrecognized argument '--bogus'"),
               (["--self-test", "--bogus"], "unrecognized argument '--self-test'"),
               (["--execution-report", "relative/report"], "unrecognized argument '--execution-report'"))
        controls = [_main_run(base + ["--tree", tree, "--commit", _COMMIT]), _main_run(base + ["--digest", digest])]
        got = [(argv, _main_run(argv)) for argv, _needle in bad]
    misses = [(argv, out) for (argv, (code, out)), (_argv, needle) in zip(got, bad)
              if code != 2 or needle not in out or "PASS" in out]
    check("refuse/bad-arguments", [code for code, _out in controls] == [0, 0] and not misses, (controls, misses))

    # The list size bound at its ends: one byte under, exactly at, and one byte over MAX_BYTES. Blank
    # lines are skipped, so padding with line feeds changes only the size.
    pad = MAX_BYTES - len(good.encode("utf-8"))
    for name, padding, code in (("bound/size-under", pad - 1, 0), ("bound/size-at", pad, 0),
                                ("bound/size-over", pad + 1, 2)):
        got = _case(good + "\n" * padding)
        check(name, got[0] == code, (len(good) + padding, got))

    # Nine flips follow, each patching one guard to the broken form: version binding, pre-release counted
    # as release, digest prefix compare, unexpected digests ignored, commit prefix ignored, field names
    # unchecked, lenient row parse, ROOT not bound to the manifest, and artifact not bound to the manifest.
    # Each shows that its fixture, red as written, then passes, so the fixture is red only through that
    # guard. Guards outside these nine (the commit conflict rule, the field-name line's position, the
    # digest and commit patterns, CR and NUL, and the argument guards) are covered by their own refuse,
    # bound and pass cases above, not by a flip. The version flip uses a list holding only this project's
    # rows under the wrong version, so no other row can keep it red.
    only_wrong_version = _PREAMBLE + "".join(_ours(version="1.1.2"))
    before = _case(only_wrong_version)
    original = _version_matches
    globals()["_version_matches"] = lambda row, version: True
    try:
        got = _case(only_wrong_version)
    finally:
        globals()["_version_matches"] = original
    check("flip/version-binding", before[0] == 1 and got[0] == 0, (before, got))

    before = _case(pre_release)
    original = _is_release
    globals()["_is_release"] = lambda row: True
    try:
        got = _case(pre_release)
    finally:
        globals()["_is_release"] = original
    check("flip/pre-release-counted", before[0] == 2 and got[0] == 0, (before, got))

    before = _case(mismatched)
    original = _same_digest
    globals()["_same_digest"] = lambda listed, want: listed[:63] == want[:63]
    try:
        got = _case(mismatched)
    finally:
        globals()["_same_digest"] = original
    check("flip/digest-prefix-compare", before[0] == 1 and got[0] == 0, (before, got))

    before = _case(extra)
    original = _unexpected
    globals()["_unexpected"] = lambda released, expected: []
    try:
        got = _case(extra)
    finally:
        globals()["_unexpected"] = original
    check("flip/unexpected-ignored", before[0] == 1 and got[0] == 0, (before, got))

    before = _case(good, commit=_OTHER_COMMIT)
    original = _commit_matches
    globals()["_commit_matches"] = lambda listed, tagged: True
    try:
        got = _case(good, commit=_OTHER_COMMIT)
    finally:
        globals()["_commit_matches"] = original
    check("flip/commit-prefix-ignored", before[0] == 1 and got[0] == 0, (before, got))

    reordered = good.replace("commit    status", "status    commit")
    before = _case(reordered)
    original = _field_names_ok
    globals()["_field_names_ok"] = lambda names: True
    try:
        got = _case(reordered)
    finally:
        globals()["_field_names_ok"] = original
    check("flip/field-names-unchecked", before[0] == 2 and got[0] == 0, (before, got))

    original = _parse_row

    def _lenient(line, where):
        try:
            return original(line, where)
        except CannotEvaluate:
            return Row("skipped-row", "0.0.0", _SHORT, RELEASE, _EXTRA_HEX)

    short_row = good + "{} {} {}\n".format(_P, _V, RELEASE)
    before = _case(short_row)
    globals()["_parse_row"] = _lenient
    try:
        got = _case(short_row)
    finally:
        globals()["_parse_row"] = original
    check("flip/lenient-row-parse", before[0] == 2 and got[0] == 0, (before, got))

    unbound = {"manifest": _MANIFEST + "\n"}
    before = _case(good, tree_kwargs=unbound)
    original = _root_binds_manifest
    globals()["_root_binds_manifest"] = lambda root, manifest_bytes: True
    try:
        got = _case(good, tree_kwargs=unbound)
    finally:
        globals()["_root_binds_manifest"] = original
    check("flip/root-not-bound-to-manifest", before[0] == 2 and got[0] == 0, (before, got))

    before = _case(good, tree_kwargs={"artifacts": differs})
    original = _artifact_bound
    globals()["_artifact_bound"] = lambda recorded, listed: True
    try:
        got = _case(good, tree_kwargs={"artifacts": differs})
    finally:
        globals()["_artifact_bound"] = original
    check("flip/artifact-not-bound-to-manifest", before[0] == 2 and got[0] == 0, (before, got))

    if FAILURES:
        print("SELF-TEST FAIL: check_published_digest {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_published_digest {} checks (a list carrying the expected digest on a release "
          "row passes, from a tree with a matching commit prefix, where ROOT alone is expected, or from given "
          "digests; a missing digest, a near-miss digest, an extra digest at the version, conflicting "
          "commits, a commit id that is not a prefix of the tagged commit and a wrong version each fail; a "
          "pre-release-only row, malformed rows, a missing, changed or misplaced field-name line, an "
          "unreadable, absent or non-regular list, a tree that does not bind the version, a ROOT that is not "
          "the SHA-256 of the tree's manifest, an artifact digest the manifest does not carry, and each bad "
          "argument at its own guard are cannot-evaluate; the saved 2026-10-12 list exits 2 on its "
          "pre-release row, and its release row variant passes with ROOT alone, also against a tree with "
          "artifacts, and turns red when the artifact digests are expected on the list again; the commit "
          "length and size bounds hold at their ends; nine flips each turn their red fixture green: version "
          "binding, pre-release counted as release, digest prefix compare, unexpected digests ignored, commit "
          "prefix ignored, field names unchecked, lenient row parse, ROOT not bound to the manifest and "
          "artifact not bound to the manifest)".format(len(EXECUTED)))
    return 0


def _main_run(argv):
    """main()'s exit code for the argument list argv and its captured stdout plus stderr."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main()
    finally:
        sys.argv = saved
    return code, out.getvalue()


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate(argv)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
