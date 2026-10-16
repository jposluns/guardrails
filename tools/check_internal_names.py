#!/usr/bin/env python3
"""Internal-name leak gate: block internal provenance vocabulary from the QA-suite shipped surfaces.

Genericization enforcement (Section D of the QA-suite plan). The shipped pack must carry no internal
provenance vocabulary: no sibling-repo name, no fixed host path, no guardrail-decision or finding id, no
internal incident id, no account string. This gate extends the pack's leak-checking capability with an
internal-name denylist over exactly the surfaces the QA suite adds and controls.

TWO LAYERS, mirroring tools/check_leaks.py (whose normalization and structural host/account/token patterns
are reused, so the two gates agree and no plaintext internal name is re-typed here):
  1. STRUCTURAL SHAPE patterns (generic, reveal nothing): the leak gate's host-path / private-IP / token /
     account shapes, PLUS internal provenance-id shapes (a guardrail-decision or finding id, an internal
     incident id), and the whole capitalized word naming an internal approver role (case-sensitive and
     word-bounded, so "Architecture" and "architectural" never match). These need no codename literal.
  2. A HASHED codename denylist (tools/internal-name-hashes.txt): SHA-256 only, for names a generic shape
     cannot catch, above all a sibling-repo name. Shipped EMPTY by default; the private generator fills it.

SCOPE, deliberately narrow and explicit (all but the role-name rule, whose whole-tree scope is described
below). This gate scans ONLY the QA-suite shipped surfaces plus the shipped
gate-runner and CI-workflow files this PR modifies (tools/run_all_checks.sh and .github/workflows/quality.yml,
both verified clean of provenance shapes), plus the public .preview/ channel, NOT the whole tree: the existing pack legitimately carries
guardrail-decision ids in its hooks and a dogfood-adopter name in its site and docs, so a whole-tree
provenance-id scan would be a wall of false positives. The scope is the surfaces listed in SCOPE_RELPATHS
(files scanned directly, directories walked fail-closed);
absent future surfaces are skipped, a present one that cannot be read fails closed (an OSError is exit 2, a
non-UTF-8/undecodable file is a fail-closed unscannable-surface finding). A BROKEN SYMLINK (a present dentry
whose target is missing) is a present-but-unreadable input, not an absent one: it fails closed (re-raised as
an OSError, exit 2) at both the hashes-file load and the scoped-file scan, never read as absent. A line
carrying an `internal-allow`
marker is exempt from the STRUCTURAL layer (the same escape hatch the leak gate offers).

DISCLOSED SCOPE LIMITS. SCOPE_RELPATHS is a HAND-MAINTAINED allowlist with no drift-guard: a new QA-suite
surface must be ADDED HERE BY HAND to be covered, and a surface not yet listed is out of this gate's reach
by omission. Mixed-content metadata files that legitimately carry existing provenance tokens (for example
.aiqt/core/gates/manifest.toml and .aiqt/core/ownership.toml) are DELIBERATELY OUT OF SCOPE: scanning them
would be a wall of false positives on legitimate ids, so their QA-suite-relevant content is hand-verified
rather than machine-scanned here. Adding either class of file to this scope is a deliberate, reviewed edit,
not an automatic one.

ROLE-NAME RULE, WHOLE TRACKED TREE. The internal role-name pattern is a separate rule with its own, wider
scope, INDEPENDENT of SCOPE_RELPATHS and of SKIP_NAMES: it reads every file `git ls-files -z --cached` lists
under the repository root (code, Markdown, site HTML, generated outputs that are committed, and this gate's
own source alike), decoded as UTF-8, and also runs inside the scoped scan above (so an untracked scoped
surface is still covered). The only exemption is the internal-allow marker, which exempts the ONE line that
carries it; this gate's own self-test vectors carry that marker on each line that holds the word, and no
file is exempt by name. A tracked file that is not valid UTF-8 is skipped only under the rule this gate
already applies to walked directories (a file whose suffix is present and not in TEXT_SUFFIXES, such as the
committed .zip archives, is not text and is not read as text); any other non-UTF-8 tracked file, a tracked
path absent from the working tree, or a tracked path that is not a readable regular file CANNOT BE
EVALUATED and fails closed at exit 2, the way an unreadable input does. A .gitattributes `binary` attribute
is NOT a skip: a binary-attributed file that decodes as UTF-8 is scanned. When git cannot list the tracked
set (no git, not a repository, a failed or empty listing) the rule cannot run and the gate exits 2.
WHAT THE ROLE RULE CANNOT SEE: an untracked or git-ignored file (outside the scoped scan), a generated
output that is not committed and is not handed to it as a tracked file (a built release archive, a rendered
site that is not checked in), the contents of an archive or other non-UTF-8 file skipped by suffix, a staged
change that differs from the working-tree copy (it reads the working tree), and the word split or encoded
(an HTML entity, a zero-width character) so the plain pattern does not match.

Exit 0 clean, 1 on any finding or a malformed hashes file, 2 on a read error or a tracked file the role
rule cannot evaluate (fail-closed). `--self-test` proves the gate FAILS on a seeded internal name (a
provenance id, a host path, a hashed codename, and the role name in a tracked file outside SCOPE_RELPATHS)
and passes clean generic content, so removing a layer makes a case fail.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_internal_names.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
import stat
import subprocess
from pathlib import Path

# Import the sibling modules WITHOUT placing this script's own directory AHEAD of the stdlib on sys.path.
# Under `python3 -I` a sys.path insertion at index 0 would let a sibling json.py or hashlib.py (each
# `raise SystemExit(0)`) shadow a stdlib import and silently neuter this gate. Appending keeps stdlib
# precedence (a stdlib import still resolves from the stdlib first) while still resolving our own sibling
# modules from this directory. The check_python_launcher_isolation gate enforces this by flagging a
# reintroduced index-0 insertion in this file.
sys.path.append(str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
import check_leaks  # noqa: E402  reuse the leak gate's normalization + structural host/account patterns

SKIP_DIRS = {".git", "node_modules", "__pycache__"}
SKIP_NAMES = {"internal-name-hashes.txt", "check_internal_names.py", "check_leaks.py", "leak-hashes.txt"}
TEXT_SUFFIXES = check_leaks.TEXT_SUFFIXES
HASHES_RELPATH = ("tools", "internal-name-hashes.txt")

# The QA-suite shipped surfaces this gate is responsible for keeping genericized. Files are scanned
# directly; directories are walked fail-closed. Absent entries are skipped (a not-yet-created future
# surface is not a declared-required input); a present-but-unreadable entry fails closed.
SCOPE_RELPATHS = (
    "tools/_qa_adapter.py",
    "tools/audit_reference.py",
    "tools/run_all_checks.sh",   # shipped local gate runner (this PR modifies it)
    ".github/workflows/quality.yml",  # shipped CI workflow (this PR modifies it)
    ".github/workflows/msg-leaks.yml",  # shipped CI workflow (the message/metadata leak scan)
    ".aiqt/assurance.toml",
    ".aiqt/assurance-schema.md",
    ".aiqt/core/qa-skills",      # future QA skill sources (multi-skill generator input)
    "site/downloads/qa-skills",  # future generated QA skill outputs
    "docs/qa-suite.md",          # future QA-suite adopter docs
    ".preview",                  # the public hooks preview channel (skipped once retired)
)

# Internal provenance-id SHAPE patterns (generic; carry no codename). A guardrail-decision or finding id
# and an internal incident id are internal mechanics that must never reach a shipped QA-suite surface.
_PROVENANCE = [
    (re.compile(r'\b(?:GD|EN|FR|PD)-\d+\b'), "internal guardrail-decision or finding id"),
    (re.compile(r'\bP-\d+\.\d+\b'), "internal incident id"),
]
# Internal ROLE-NAME pattern: the capitalized word that names an internal approver role must never reach a
# shipped surface. It is a whole word and case-sensitive, so "Architecture", "architectural", a lowercase
# common noun, and the word inside a longer identifier never match. The role rule scans this file too (it
# honours no SKIP_NAMES); the pattern source below does not match itself because the word follows the "b"
# of the boundary escape, a word character.
_ROLE_NAMES = [
    (re.compile(r'\bArchitect\b'), "internal role name"),
]
# Reuse the leak gate's structural host/IP/token/account patterns verbatim (imported, never re-typed, so
# this file stays self-clean the way check_leaks.py does), and add the provenance-id and role-name shapes.
STRUCTURAL = list(check_leaks.STRUCTURAL) + _PROVENANCE + _ROLE_NAMES


def _read_present_text(path):
    """Read `path` as UTF-8 text, failing closed on a present-but-unreadable input WITHOUT blocking. Presence
    is classified with an S_ISREG check BEFORE any open (mirroring the QA adapter's _classify_presence), so a
    NON-REGULAR file (a FIFO above all, whose open() would BLOCK waiting for a writer, but also a device,
    socket, or directory) is a fail-closed OSError rather than a hanging open, and a BROKEN SYMLINK (a present
    dentry whose target is missing) re-raises fail-closed. A genuinely ABSENT path (no dentry at all) returns
    None so a caller for whom absence is intended (the denylist) can treat it as empty; every PRESENT dentry
    that is not a readable regular file raises OSError to the fail-closed caller (exit 2). path.stat() FOLLOWS
    a symlink, so a dangling link raises FileNotFoundError exactly as an absent path, and path.is_symlink()
    (lstat) is what tells a present-but-dangling dentry from a truly missing one."""
    try:
        st = path.stat()
    except FileNotFoundError:
        if path.is_symlink():
            raise
        return None
    if not stat.S_ISREG(st.st_mode):
        raise OSError("{} is present but not a regular file; fail closed".format(path))
    return path.read_text(encoding="utf-8")


def load_hashes(root):
    """Return (hashes, maxn, bad_lines) for the internal-name denylist, mirroring the leak gate's loader.
    An ABSENT denylist is intended (empty set). A PRESENT-but-unreadable one (a broken symlink, a NON-REGULAR
    file such as a FIFO, an unreadable regular file) fails closed via the shared _read_present_text, which
    classifies presence with S_ISREG BEFORE any open so a FIFO cannot BLOCK the load; the OSError reaches the
    fail-closed caller (exit 2)."""
    f = root.joinpath(*HASHES_RELPATH)
    hashes, maxn, bad, maxn_set = set(), 3, [], False
    content = _read_present_text(f)
    if content is not None:
        for number, line in enumerate(content.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                parts = s.split()
                if len(parts) >= 2 and parts[0] == "#" and parts[1] == "maxn":
                    if maxn_set:
                        bad.append("internal-name-hashes.txt:{}: duplicate '# maxn' directive".format(number))
                    elif (len(parts) == 3 and parts[2].isascii() and parts[2].isdigit()
                          and 1 <= len(parts[2]) <= 3 and 1 <= int(parts[2]) <= 100):
                        maxn, maxn_set = int(parts[2]), True
                    else:
                        bad.append("internal-name-hashes.txt:{}: malformed '# maxn N' (need one integer 1..100)".format(number))
                continue
            if check_leaks._HEX64.match(s):
                hashes.add(s)
            else:
                bad.append("internal-name-hashes.txt:{}: not a 64-hex hash".format(number))
    return hashes, maxn, bad


def scan_text(text, hashes, maxn):
    """Return (line_or_None, label) findings. A STRUCTURAL match carries a 1-based line; a hash match
    carries None. A line with an `internal-allow` marker is exempt from STRUCTURAL (hash layer still runs)."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if "internal-allow" in line:
            continue
        found += [(number, label) for pattern, label in STRUCTURAL if pattern.search(line)]
    if hashes:
        import hashlib
        for gram in check_leaks.ngram_forms(text, maxn):
            if hashlib.sha256(gram.encode("utf-8")).hexdigest() in hashes:
                found.append((None, "internal codename (hash match)"))
                break
    return found


def _iter_scope(root, scope_relpaths):
    """Yield in-scope files. A file entry is yielded if present; a directory entry is walked fail-closed.
    An absent entry is skipped; an unreadable present one raises (caller fails closed)."""
    for rel in scope_relpaths:
        if Path(rel).name in SKIP_NAMES:  # compare the BASENAME (mirrors the f.name test below); a full
            continue                       # relpath never matches a bare-basename SKIP_NAMES entry
        p = root / rel
        try:
            st = p.stat()
        except FileNotFoundError:
            # An ABSENT scope entry is skipped, but a BROKEN SYMLINK is a present dentry whose target is
            # missing: p.stat() (which follows the link) raises FileNotFoundError just as for an absent path,
            # yet it must FAIL CLOSED, never be silently skipped. Re-raise it to the fail-closed caller.
            if p.is_symlink():
                raise
            continue
        if (st.st_mode & 0o170000) == 0o040000:  # directory
            for f in walk_files(p, SKIP_DIRS):
                if f.name in SKIP_NAMES:
                    continue
                if f.suffix and f.suffix not in TEXT_SUFFIXES:
                    continue
                yield f
        else:
            yield p


def scan_scope(root, scope_relpaths, hashes, maxn):
    findings = []
    for path in _iter_scope(root, scope_relpaths):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        try:
            text = _read_present_text(path)
        except UnicodeDecodeError:
            # A non-UTF-8 in-scope surface is UNSCANNABLE, not clean: an internal name held in bytes this
            # text scanner cannot decode would otherwise evade silently. Fail closed with a finding (exit
            # 1), the same fail-closed posture as an unreadable present file, never a silent skip.
            findings.append("{}: unscannable in-scope surface (not valid UTF-8); fail-closed".format(rel))
            continue
        # A NON-REGULAR in-scope file (a FIFO above all) is classified before any open by _read_present_text
        # and raises OSError to the fail-closed caller (exit 2), never a blocking read. A yielded entry that
        # has since vanished (returns None) is a race, failed closed as a finding rather than silently skipped.
        if text is None:
            findings.append("{}: in-scope surface vanished during scan; fail-closed".format(rel))
            continue
        for number, label in scan_text(text, hashes, maxn):
            findings.append("{}:{}: {}".format(rel, number, label) if number is not None
                            else "{}: {}".format(rel, label))
    return findings


def tracked_files(root):
    """The sorted tracked relpaths under `root` from `git ls-files -z --cached`, the role rule's input set.
    Pinned like gen_manifest's index read: the GIT_* environment is dropped (a repository-selecting variable
    cannot redirect the read), core.fsmonitor=false (a repository-configured hook never runs under a gate
    read), protocol.allow=never and GIT_NO_LAZY_FETCH=1. Like that read, a real-checkout scan keeps the
    caller's on-disk git configuration (where safe.directory trust for the checkout lives). A self-test
    entered through self_test_main (the --self-test entry point) runs every call here inside the fixture git
    lifecycle, so it reads no caller home, XDG or system configuration; a direct _self_test() call bypasses
    that isolation. No git, a nonzero exit, a timeout, an empty listing
    or an undecodable path raises OSError or UnicodeDecodeError to the fail-closed caller (exit 2), never an
    empty tree that would read as clean."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        proc = subprocess.run(["git", "-C", str(root), "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                               "ls-files", "-z", "--cached"], capture_output=True, timeout=120, env=env)
    except subprocess.SubprocessError as exc:
        raise OSError("cannot list the tracked files ({})".format(exc))
    if proc.returncode != 0:
        raise OSError("git ls-files failed (exit {}): {}".format(
            proc.returncode, proc.stderr.decode("utf-8", "replace").strip()))
    rels = {record.decode("utf-8") for record in proc.stdout.split(b"\x00") if record}
    if not rels:
        raise OSError("git ls-files listed no tracked files; an empty tree is never assumed")
    return sorted(rels)


def scan_role_text(text):
    """(line, label) role-name findings in `text`. A line carrying an `internal-allow` marker is exempt; the
    marker exempts that line only, never the rest of the file."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if "internal-allow" in line:
            continue
        found += [(number, label) for pattern, label in _ROLE_NAMES if pattern.search(line)]
    return found


def scan_tracked_roles(root, relpaths):
    """Return (findings, unevaluable) for the role rule over the tracked `relpaths`, independent of
    SCOPE_RELPATHS and SKIP_NAMES. A file that is not valid UTF-8 is skipped only under the walk rule this
    gate already applies (a present suffix outside TEXT_SUFFIXES marks a non-text file); any other
    non-UTF-8 file, or a tracked path absent from the working tree, is unevaluable. A tracked path that is
    present but not a readable regular file raises OSError (exit 2) through _read_present_text."""
    findings, unevaluable = [], []
    for rel in relpaths:
        try:
            text = _read_present_text(root / rel)
        except UnicodeDecodeError:
            suffix = Path(rel).suffix
            if suffix and suffix not in TEXT_SUFFIXES:
                continue
            unevaluable.append("{}: tracked file is not valid UTF-8; the role-name rule cannot evaluate it".format(rel))
            continue
        if text is None:
            unevaluable.append("{}: tracked file is absent from the working tree; the role-name rule cannot "
                               "evaluate it".format(rel))
            continue
        for number, label in scan_role_text(text):
            findings.append("{}:{}: {}".format(rel, number, label))
    return findings, unevaluable


def run_gate(root):
    """The real scan of the tree at `root`: the scoped layers over SCOPE_RELPATHS plus the role rule over
    every tracked file. Returns the exit code and prints the report."""
    try:
        hashes, maxn, findings = load_hashes(root)
        findings = list(findings)
        findings += scan_scope(root, SCOPE_RELPATHS, hashes, maxn)
        role_findings, unevaluable = scan_tracked_roles(root, tracked_files(root))
        findings += role_findings
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read a required input (denylist, scoped surface or tracked file) ({}); "
              "fail-closed".format(exc), file=sys.stderr)
        return 2
    # A role-name hit inside a tracked scoped file is reported by both scans in the same form; report it once.
    findings = sorted(set(findings))
    if findings:
        print("FAIL: {} internal-name leak(s) in the scanned surfaces".format(len(findings)))
        for finding in findings:
            print("  " + finding)
    if unevaluable:
        print("error: the role-name rule cannot evaluate {} tracked file(s); fail-closed".format(len(unevaluable)),
              file=sys.stderr)
        for item in unevaluable:
            print("  " + item, file=sys.stderr)
        return 2
    if findings:
        return 1
    print("PASS: no internal provenance vocabulary in the scanned QA-suite surfaces, and no internal role "
          "name in any tracked file")
    return 0


def main():
    root = Path(__file__).resolve().parents[1]
    argv = sys.argv[1:]
    # UNKNOWN-OPTION REJECTION precedes the --self-test dispatch: this gate accepts only --self-test (or no
    # args for a real scan), so any other token (a misspelled --self-testx, a stray flag) is a LOUD exit 2,
    # never a silent fall-through to the default scan path that would exit without running the self-test the
    # caller asked for (which would let --self-testx masquerade as --self-test in CI parity).
    unknown = [a for a in argv if a != "--self-test"]
    if unknown:
        print("error: unrecognized option {!r}; fail closed".format(unknown[0]), file=sys.stderr)
        return 2
    if "--self-test" in argv:
        return self_test_main()
    return run_gate(root)


def self_test_main():
    """Run the self-test with every git call it makes, including the gate's own tracked-file read on its
    fixture repository, isolated from the caller's git configuration: the fixture git lifecycle applies the
    shared fixture scrub (HOME and XDG_CONFIG_HOME at a scratch directory, the global and system config
    pinned away), and its PATH wrapper reasserts the system-config pin after tracked_files drops the GIT_*
    environment. A caller's home, XDG or system configuration (malformed or hostile) therefore cannot change
    a self-test verdict or run a configured program. The lifecycle saves the environment before its scrub
    and restores it on exit, so no scrub runs here first: an in-process call leaves the caller's environment
    exactly as it found it (self-test case 22)."""
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle():
        return _self_test()


# --- self-test --------------------------------------------------------------------------------------
def _self_test():
    import hashlib
    import shutil
    import tempfile

    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-selftest-"))
    try:
        toolsdir = tmp / "tools"
        toolsdir.mkdir()
        scope = ("tools/subject.md",)

        def write_hashes(terms):
            lines = ["# maxn 3"]
            for t in terms:
                lines.append(check_leaks.term_hash(t))
            (toolsdir / "internal-name-hashes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        def run(text, hash_terms=()):
            write_hashes(hash_terms)
            (toolsdir / "subject.md").write_text(text, encoding="utf-8")
            hashes, maxn, bad = load_hashes(tmp)
            if bad:
                return "bad-hashes", bad
            return "ok", scan_scope(tmp, scope, hashes, maxn)

        # 1. Clean generic content (using the sanctioned generic fixture names) -> no findings.
        state, f = run("The regression fixtures are actor-authored-wait, unmerged-completion-claim, "
                       "and fabricated-supersedes. All generic.\n")
        if state != "ok" or f:
            failures.append("clean generic content expected no findings, got {} {}".format(state, f))

        # 2. DISCRIMINATING: a seeded provenance id is caught. Removing the provenance SHAPE patterns
        #    makes this pass (no finding), failing the case. The token is a SYNTHETIC fixture: GD-000 has
        #    the guardrail-decision shape the detector matches, but the 000 ordinal is not a real project
        #    id (ordinals are 1-based), so no live internal provenance id sits in this shipped source.
        state, f = run("This references GD-000 in prose.\n")
        if state != "ok" or not any("guardrail-decision" in x for x in f):
            failures.append("seeded provenance id expected a finding, got {} {}".format(state, f))

        # 3. A seeded fixed host path is caught (reused structural layer). The path is assembled at
        #    runtime so no contiguous host-path literal sits in this source (the whole-tree leak gate
        #    scans this file); the runtime value is a real fixed host path the scan must catch.
        hostpath = "/" + "opt/somewhere/private"
        state, f = run("A hardcoded path {} crept in.\n".format(hostpath))
        if state != "ok" or not f:
            failures.append("seeded host path expected a finding, got {} {}".format(state, f))

        # 4. DISCRIMINATING: a seeded HASHED codename is caught. Removing the hash layer makes this pass,
        #    failing the case. The term is synthetic (never a real internal name in this source).
        secret_term = "zzsynthcodename"
        state, f = run("Someone wrote {} into a doc.\n".format(secret_term), hash_terms=[secret_term])
        if state != "ok" or not any("hash match" in x for x in f):
            failures.append("seeded hashed codename expected a hash-match finding, got {} {}".format(state, f))

        # 5. An `internal-allow` line is exempt from the STRUCTURAL layer (synthetic GD-000 fixture id).
        state, f = run("GD-000 kept on purpose  # internal-allow\n")
        if state != "ok" or f:
            failures.append("internal-allow line expected exemption, got {} {}".format(state, f))

        # 6. A malformed hashes file (a non-hash line) is a failure, never a silent empty denylist.
        (toolsdir / "internal-name-hashes.txt").write_text("# maxn 3\nnot-a-hash\n", encoding="utf-8")
        _h, _m, bad = load_hashes(tmp)
        if not bad:
            failures.append("malformed hashes file expected a bad-line report, got none")

        # 7. term_hash normalization agrees with check_leaks (so a codename matches across separators).
        if check_leaks.term_hash("Foo Bar") != hashlib.sha256(b"foo-bar").hexdigest():
            failures.append("term_hash normalization drifted from the leak gate's")

        # 8. DISCRIMINATING: a non-UTF-8 in-scope surface FAILS CLOSED (an unscannable-surface finding),
        #    never a silent skip that would let an internal name hide in undecodable bytes. Removing the
        #    fail-closed branch makes this pass (no finding), failing the case.
        write_hashes([])
        (toolsdir / "subject.md").write_bytes(b"\xff\xfe internal name in undecodable bytes \xff\n")
        hashes, maxn, bad = load_hashes(tmp)
        f = scan_scope(tmp, scope, hashes, maxn)
        if not any("unscannable" in x for x in f):
            failures.append("non-UTF-8 in-scope surface expected a fail-closed finding, got {}".format(f))

        # 9. DISCRIMINATING (SKIP_NAMES honoured for a SCOPED file by BASENAME): a scope entry whose
        #    BASENAME is in SKIP_NAMES (a gate's own source, which legitimately carries provenance shapes)
        #    is skipped, so it produces no finding even when it holds a structural host path. Reverting the
        #    basename test to `if rel in SKIP_NAMES` (a full relpath vs a bare basename) never matches, so
        #    the file is scanned and the host path is flagged, failing this case.
        write_hashes([])
        (toolsdir / "check_leaks.py").write_text(
            "A path {} sits in this gate source.\n".format("/" + "opt/x/private"), encoding="utf-8")
        hashes, maxn, bad = load_hashes(tmp)
        f = scan_scope(tmp, ("tools/check_leaks.py",), hashes, maxn)
        if f:
            failures.append("a scoped file whose basename is in SKIP_NAMES expected to be skipped, got {}".format(f))

        # 10. DISCRIMINATING (scope MEMBERSHIP of the shipped gate-runner and CI-workflow files): the real
        #     SCOPE_RELPATHS includes tools/run_all_checks.sh and .github/workflows/quality.yml, so a
        #     synthetic provenance id (GD-000; the 000 ordinal is not a real project id) planted in each is
        #     caught when scanning the LIVE scope. Removing either path from SCOPE_RELPATHS leaves it
        #     unscanned and the finding absent, failing this case. (Only these two fixtures exist under tmp;
        #     the other SCOPE_RELPATHS entries are absent and skipped.)
        write_hashes([])
        (toolsdir / "run_all_checks.sh").write_text("echo references GD-000\n", encoding="utf-8")
        wf = tmp / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "quality.yml").write_text("# a step that references GD-000\n", encoding="utf-8")
        hashes, maxn, bad = load_hashes(tmp)
        live = scan_scope(tmp, SCOPE_RELPATHS, hashes, maxn)
        for rel in ("tools/run_all_checks.sh", ".github/workflows/quality.yml"):
            if not any(rel in x and "guardrail-decision" in x for x in live):
                failures.append("live-scope member {} expected a provenance finding (scope membership "
                                "untested if absent), got {}".format(rel, live))

        # 11. DISCRIMINATING (finding-4: a BROKEN SYMLINK at a SCOPED path FAILS CLOSED, never reads as
        #     absent): a scope entry that is a present dentry whose symlink target is missing must raise
        #     (fail closed), not be silently skipped. Removing the is_symlink() re-raise in _iter_scope lets
        #     p.stat()'s FileNotFoundError read as absent (a silent skip, no finding), failing this. Needs
        #     os.symlink; asserted only where it is available and creating a dangling symlink succeeds.
        write_hashes([])
        broken = toolsdir / "broken.md"
        made = False
        try:
            os.symlink(str(tmp / "no-such-scope-target"), str(broken))
            made = True
        except (OSError, NotImplementedError, AttributeError):
            made = False
        if made:
            raised = False
            try:
                scan_scope(tmp, ("tools/broken.md",), set(), 3)
            except OSError:
                raised = True
            if not raised:
                failures.append("a broken symlink at a scoped path expected fail-closed (raise), got a silent skip")
            broken.unlink()

        # 12. DISCRIMINATING (finding-4: a BROKEN SYMLINK at the HASH-FILE path FAILS CLOSED): a broken
        #     symlink at tools/internal-name-hashes.txt must raise from load_hashes (a present dentry with a
        #     missing target), never read as an intended-absent empty denylist. Removing the is_symlink()
        #     re-raise in load_hashes lets read_text's FileNotFoundError read as absent, failing this.
        hf = toolsdir / "internal-name-hashes.txt"
        if hf.exists() or hf.is_symlink():
            hf.unlink()
        made = False
        try:
            os.symlink(str(tmp / "no-such-hashfile-target"), str(hf))
            made = True
        except (OSError, NotImplementedError, AttributeError):
            made = False
        if made:
            raised = False
            try:
                load_hashes(tmp)
            except OSError:
                raised = True
            if not raised:
                failures.append("a broken symlink at the hash-file path expected fail-closed (raise), got an "
                                "absent (empty-denylist) read")
            hf.unlink()

        # 13. DISCRIMINATING (internal role name): the whole capitalized word is caught, bare and in a
        #     hyphenated compound. Removing the role-name pattern leaves the hits unflagged; dropping its
        #     word boundaries flags "Architecture" and the identifiers; making it case-insensitive flags the
        #     lowercase noun; and an unbounded case-insensitive form flags "architectural". The allowlist
        #     paths of the SCOPED scan (an internal-allow line, a scoped file whose basename is in SKIP_NAMES)
        #     stay exempt there; the tracked-file role scan below honours no SKIP_NAMES. Each source line that
        #     holds the word carries the internal-allow marker, because the role rule scans this file too.
        write_hashes([])
        for text in ("Reviewed by the Architect.\n", "An Architect-approved change.\n"):  # internal-allow
            state, f = run(text)
            if state != "ok" or not any("internal role name" in x for x in f):
                failures.append("role-name hit {!r} expected a finding, got {} {}".format(text, state, f))
        for text in ("See the Architecture section.\n", "An architectural note.\n",
                     "A software architect wrote it.\n", "ArchitectReview = preArchitect + Architect_id\n",
                     "The Architect stays here on purpose  # internal-allow\n"):
            state, f = run(text)
            if state != "ok" or f:
                failures.append("role-name non-hit {!r} expected no finding, got {} {}".format(text, state, f))
        (toolsdir / "check_leaks.py").write_text("Reviewed by the Architect.\n", encoding="utf-8")  # internal-allow
        f = scan_scope(tmp, ("tools/check_leaks.py",), set(), 3)
        if f:
            failures.append("a role-name hit in a SKIP_NAMES basename expected to be skipped by the scoped scan, "
                            "got {}".format(f))

        # 14-17 run the real gate entry point (run_gate) on a fixture git repository, so the tracked-file
        # enumeration and its wiring into the gate are exercised, not only the helpers. Fixture git calls use
        # the shared hermetic fixture environment (no caller config, no auto-maintenance); only init and add
        # run, and the gate's own ls-files read is pinned in tracked_files and runs inside the fixture git
        # lifecycle self_test_main enters, so it reads no caller home, XDG or system configuration (case 21).
        import contextlib
        import io
        import _git_fixture_env

        repo = tmp / "tracked-repo"
        repo.mkdir()
        def gate(at):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = run_gate(at)
            return rc, out.getvalue() + err.getvalue()

        role = "internal role name"
        (repo / "docs").mkdir()
        (repo / "tools").mkdir()
        (repo / "docs" / "outside.md").write_text("Signed off by the Architect.\n", encoding="utf-8")  # internal-allow
        (repo / "tools" / "check_leaks.py").write_text("# the Architect said so\n", encoding="utf-8")  # internal-allow
        (repo / "notes.txt").write_text("Architect kept here  # internal-allow\n"  # internal-allow
                                        "the Architect again\n", encoding="utf-8")  # internal-allow
        (repo / "data.bin").write_text("binary-attributed but UTF-8: Architect\n", encoding="utf-8")  # internal-allow
        (repo / ".gitattributes").write_text("data.bin binary\nblob.zip binary\n", encoding="utf-8")
        (repo / "blob.zip").write_bytes(b"PK\x03\x04\xff\xfe Architect \xff\n")  # internal-allow
        (repo / "clean.md").write_text("An architectural note on the Architecture section.\n", encoding="utf-8")
        (repo / "untracked.md").write_text("the Architect, never added\n", encoding="utf-8")  # internal-allow
        init = subprocess.run(["git", "-C", str(repo), "init", "-q"], capture_output=True, text=True,
                              env=_git_fixture_env.git_fixture_env(), timeout=60)
        add = subprocess.run(["git", "-C", str(repo), "add", "--", ".gitattributes", "docs", "tools", "notes.txt",
                              "data.bin", "blob.zip", "clean.md"], capture_output=True, text=True,
                             env=_git_fixture_env.git_fixture_env(), timeout=60)
        if init.returncode != 0 or add.returncode != 0:
            failures.append("tracked-file role fixture could not be built: init rc={} add rc={} {!r}".format(
                init.returncode, add.returncode, init.stderr + add.stderr))
        else:
            rc, report = gate(repo)
            # 14. DISCRIMINATING (whole tracked tree, not SCOPE_RELPATHS): a role-name hit in a tracked file
            #     outside SCOPE_RELPATHS is caught by the real gate, and so is one in a file whose basename is
            #     in SKIP_NAMES (no file is exempt by name). Restricting the role rule to SCOPE_RELPATHS, or
            #     honouring SKIP_NAMES in it, drops these findings and fails the case.
            for expect in ("docs/outside.md:1: " + role, "tools/check_leaks.py:1: " + role):
                if rc != 1 or expect not in report:
                    failures.append("tracked role-name hit {!r} expected (exit 1), got rc={} {!r}".format(
                        expect, rc, report))
            if any(rel.startswith(("docs/outside", "tools/check_leaks")) for rel in SCOPE_RELPATHS):
                failures.append("the outside-scope fixture paths must lie outside SCOPE_RELPATHS")
            # 15. DISCRIMINATING (the allow marker exempts ONE line): line 1 of notes.txt carries the marker
            #     and is exempt; line 2 does not and is caught. A whole-file exemption drops the line-2
            #     finding; ignoring the marker adds a line-1 finding; either fails the case.
            if "notes.txt:2: " + role not in report or "notes.txt:1: " + role in report:
                failures.append("internal-allow expected to exempt notes.txt line 1 only, got {!r}".format(report))
            # 16. DISCRIMINATING (binary and non-UTF-8 files follow the stated rule): a .gitattributes
            #     binary file that decodes as UTF-8 is scanned (data.bin is caught); a non-UTF-8 file whose
            #     suffix is outside TEXT_SUFFIXES is skipped (blob.zip neither caught nor unevaluable); and
            #     an untracked file is out of the role rule's reach (untracked.md, a disclosed limit).
            if "data.bin:1: " + role not in report:
                failures.append("a binary-attributed UTF-8 tracked file expected to be scanned, got {!r}".format(
                    report))
            if "blob.zip" in report or "untracked.md" in report:
                failures.append("a non-text-suffix non-UTF-8 file and an untracked file expected to be outside "
                                "the role rule, got {!r}".format(report))
            # DISCRIMINATING (the tracked-file read drops the GIT_* environment): GIT_CONFIG_GLOBAL pointed at
            # a malformed file must not change the gate's result. A control first proves the file breaks a git
            # read that honours it; then the gate runs with it set in the process environment and must give
            # the same exit code and report as the run above. A tracked-file read that inherits the caller's
            # GIT_* environment instead of dropping it fails on the malformed file (exit 2) and fails this.
            # This guards the drop in tracked_files only; case 21 guards the home, XDG and system isolation
            # of the self-test entry point.
            malformed = tmp / "malformed-global-gitconfig"
            malformed.write_text("[core\n\tbroken\n", encoding="utf-8")
            control = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached"], capture_output=True,
                                     env=_git_fixture_env.git_fixture_env(GIT_CONFIG_GLOBAL=str(malformed)),
                                     timeout=60)
            if control.returncode == 0:
                failures.append("a malformed GIT_CONFIG_GLOBAL file expected to break a git read that honours it "
                                "(the global-config vector would be vacuous), got rc=0")
            saved_global = os.environ.get("GIT_CONFIG_GLOBAL")
            os.environ["GIT_CONFIG_GLOBAL"] = str(malformed)
            try:
                poisoned = gate(repo)
            finally:
                if saved_global is None:
                    os.environ.pop("GIT_CONFIG_GLOBAL", None)
                else:
                    os.environ["GIT_CONFIG_GLOBAL"] = saved_global
            if poisoned != (rc, report):
                failures.append("a malformed GIT_CONFIG_GLOBAL changed the gate result: rc={} {!r}, expected rc={} "
                                "{!r}".format(poisoned[0], poisoned[1], rc, report))
            # 17. DISCRIMINATING (cannot evaluate fails closed): a tracked non-UTF-8 file with a text suffix is
            #     not skipped; the gate reports it as unevaluable and exits 2, the unreadable-input exit.
            #     Skipping every non-UTF-8 file, or treating it as clean, exits 1 and fails the case.
            (repo / "bad.md").write_bytes(b"\xff\xfe the Architect in undecodable bytes \xff\n")  # internal-allow
            add = subprocess.run(["git", "-C", str(repo), "add", "--", "bad.md"], capture_output=True, text=True,
                                 env=_git_fixture_env.git_fixture_env(), timeout=60)
            rc, report = gate(repo)
            if add.returncode != 0 or rc != 2 or "bad.md: tracked file is not valid UTF-8" not in report:
                failures.append("a tracked non-UTF-8 text file expected cannot-evaluate (exit 2), got rc={} "
                                "{!r}".format(rc, report))
            # A tracked path deleted from the working tree is also unevaluable (exit 2), never read as clean.
            (repo / "bad.md").unlink()
            rc, report = gate(repo)
            if rc != 2 or "bad.md: tracked file is absent" not in report:
                failures.append("a tracked file absent from the working tree expected cannot-evaluate (exit 2), "
                                "got rc={} {!r}".format(rc, report))
        # Outside a git repository the tracked set cannot be listed: the gate fails closed (exit 2).
        norepo = tmp / "not-a-repo"
        norepo.mkdir()
        rc, report = gate(norepo)
        if rc != 2:
            failures.append("a root that is not a git repository expected exit 2, got rc={} {!r}".format(rc, report))

        # 18. DISCRIMINATING (finding-3: an unknown option is a LOUD exit 2, never a silent default path): a
        #     subprocess given a misspelled --self-testx or a stray --bogus exits 2, never 0 by silently
        #     running the default scan path (which would let --self-testx masquerade as --self-test in CI
        #     parity). Removing the unknown-option rejection lets the unknown option fall through to the scan.
        selfpath = str(Path(__file__).resolve())
        for badarg in ("--self-testx", "--bogus"):
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath, badarg],
                                  capture_output=True, text=True)
            if proc.returncode != 2:
                failures.append("unknown option {!r} expected a loud exit 2, got {}".format(
                    badarg, proc.returncode))

        # The subprocess-spawning cases below carry a sentinel so a child (which re-enters --self-test)
        # skips them rather than recursing without bound.
        if os.environ.get("AIQT_INTERNAL_NAMES_SELFTEST_CHILD") != "1":
            childenv = dict(os.environ, AIQT_INTERNAL_NAMES_SELFTEST_CHILD="1")
            toolsdir = str(Path(__file__).resolve().parent)

            # 19. DISCRIMINATING (finding-1: a NON-REGULAR input FAILS CLOSED, never blocks on open): a FIFO
            #     at the hash-file path and at a scoped path is classified non-regular BEFORE any open, so
            #     load_hashes/scan_scope raise a fail-closed OSError PROMPTLY. Reverting the S_ISREG-before-open
            #     guard (a bare read_text) blocks on open(FIFO) and the probe times out. Each probe imports
            #     this module in a child and calls the function on a FIFO under a short timeout; a hang is a
            #     recorded failure, never an unbounded wait. Needs os.mkfifo (POSIX); asserted where available.
            if hasattr(os, "mkfifo"):
                for label, target_rel, call in (
                        ("hash-file", "tools/internal-name-hashes.txt", "cin.load_hashes(root)"),
                        ("scoped-file", "tools/subject.md",
                         "cin.scan_scope(root, ('tools/subject.md',), set(), 3)")):
                    fifo_root = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-fifo-"))
                    (fifo_root / "tools").mkdir()
                    fifo_path = fifo_root / target_rel
                    made = False
                    try:
                        os.mkfifo(str(fifo_path))
                        made = True
                    except OSError:
                        made = False
                    if not made:
                        shutil.rmtree(fifo_root, ignore_errors=True)
                        continue
                    probe = (
                        "import sys\n"
                        "sys.path.append({tools!r})\n"
                        "from pathlib import Path\n"
                        "import check_internal_names as cin\n"
                        "root = Path({root!r})\n"
                        "try:\n"
                        "    {call}\n"
                        "except OSError:\n"
                        "    sys.exit(0)\n"
                        "sys.exit(3)\n"
                    ).format(tools=toolsdir, root=str(fifo_root), call=call)
                    try:
                        proc = subprocess.run([sys.executable, "-I", "-B", "-c", probe],
                                              capture_output=True, text=True, env=childenv, timeout=20)
                        if proc.returncode != 0:
                            failures.append("a FIFO at the {} path expected a prompt fail-closed OSError "
                                            "(probe exit 0), got rc={} err={!r}".format(
                                                label, proc.returncode, proc.stderr))
                    except subprocess.TimeoutExpired:
                        failures.append("a FIFO at the {} path BLOCKED on open instead of failing closed "
                                        "(S_ISREG must be checked before any open)".format(label))
                    finally:
                        shutil.rmtree(fifo_root, ignore_errors=True)

            # 20. DISCRIMINATING (finding-6: a hostile sibling json.py/hashlib.py must NOT shadow a stdlib
            #     import and neuter this gate). Copy this script and its sibling modules (_walk, check_leaks)
            #     beside a hostile json.py and hashlib.py (each `raise SystemExit(0)`) and run `--self-test`
            #     isolated: the self-test must actually RUN (it emits its SELF-TEST marker). Reverting the
            #     sys.path.append import fix to an index-0 insertion puts the script dir first, so under -I the
            #     sibling shadows check_leaks' `import hashlib` and silently exits 0 before the self-test runs.
            sib = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-sibling-"))
            try:
                for name in ("check_internal_names.py", "_walk.py", "check_leaks.py", "_git_fixture_env.py"):
                    shutil.copy(str(Path(toolsdir) / name), str(sib / name))
                for hostile in ("json.py", "hashlib.py"):
                    (sib / hostile).write_text("raise SystemExit(0)\n", encoding="utf-8")
                proc = subprocess.run([sys.executable, "-I", "-B", str(sib / "check_internal_names.py"),
                                       "--self-test"], capture_output=True, text=True, env=childenv)
                if "SELF-TEST" not in (proc.stdout + proc.stderr):
                    failures.append("a hostile sibling json.py/hashlib.py shadowed a stdlib import and "
                                    "prevented the self-test from running (finding-6): rc={} out={!r} "
                                    "err={!r}".format(proc.returncode, proc.stdout, proc.stderr))
            finally:
                shutil.rmtree(sib, ignore_errors=True)

            # 21. DISCRIMINATING (the self-test entry point isolates the gate's tracked-file read from the
            #     caller's git configuration): a child --self-test runs under each of three poisoned caller
            #     configurations, each a malformed config file where git reads it when the isolation is
            #     absent: .gitconfig under a scratch HOME, git/config under a scratch XDG_CONFIG_HOME, and a
            #     system configuration. tracked_files drops every GIT_* variable, so GIT_CONFIG_SYSTEM cannot
            #     carry the system case: a PATH shim in front of git instead points git at the malformed file
            #     whenever neither GIT_CONFIG_NOSYSTEM=1 nor GIT_CONFIG_SYSTEM is set (where git reads the
            #     host's system file), and only the fixture git lifecycle's PATH wrapper reasserts that pin
            #     after the drop. Each child must pass. Reverting self_test_main to a bare _self_test() lets
            #     the child's tracked-file read load the malformed file, so its cases 14 to 17 exit 2 and the
            #     child fails all three; applying the scrub without the lifecycle fails the system case. A
            #     control first proves each poisoned configuration breaks a git read that honours it; the home
            #     and XDG controls pin the host's system configuration away (the shim supplies the system
            #     control's), so no control reads the host's system file.
            import shlex
            caller = _git_fixture_env.caller_env_without_git()
            real_git = shutil.which("git", path=caller.get("PATH", os.defpath))
            poison = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-poison-"))
            try:
                clean_home = poison / "clean-home"
                clean_home.mkdir()
                bad_home = poison / "bad-home"
                bad_home.mkdir()
                (bad_home / ".gitconfig").write_text("[core\n\tbroken\n", encoding="utf-8")
                bad_xdg = poison / "bad-xdg"
                (bad_xdg / "git").mkdir(parents=True)
                (bad_xdg / "git" / "config").write_text("[core\n\tbroken\n", encoding="utf-8")
                bad_system = poison / "bad-system-gitconfig"
                bad_system.write_text("[core\n\tbroken\n", encoding="utf-8")
                shim_dir = poison / "system-config-shim"
                shim_dir.mkdir()
                base = dict(caller, HOME=str(clean_home), XDG_CONFIG_HOME=str(clean_home))
                no_system = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": os.devnull}
                lanes = [("home", dict(base, HOME=str(bad_home)), no_system),
                         ("XDG", dict(base, XDG_CONFIG_HOME=str(bad_xdg)), no_system)]
                if real_git is None:
                    failures.append("no git on the caller's PATH; the system-configuration isolation case "
                                    "cannot run")
                else:
                    (shim_dir / "git").write_text(
                        "#!/bin/sh\n"
                        'if [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && [ "${{GIT_CONFIG_SYSTEM+x}}" != x ]; then\n'
                        "  export GIT_CONFIG_SYSTEM={}\n"
                        "fi\n"
                        "exec {} \"$@\"\n".format(shlex.quote(str(bad_system)), shlex.quote(real_git)),
                        encoding="utf-8")
                    (shim_dir / "git").chmod(0o700)
                    lanes.append(("system", dict(base, PATH=str(shim_dir) + os.pathsep
                                                 + base.get("PATH", os.defpath)), {}))
                for label, lane_env, control_pins in lanes:
                    control = subprocess.run(["git", "-C", str(poison), "config", "--list"], capture_output=True,
                                             text=True, env=dict(lane_env, **control_pins), timeout=60)
                    if control.returncode == 0 or "bad config" not in control.stderr:
                        failures.append("a malformed caller {} git configuration expected to break a git read "
                                        "that honours it (the isolation case would be vacuous), got rc={} "
                                        "{!r}".format(label, control.returncode, control.stderr))
                    try:
                        child = subprocess.run([sys.executable, "-I", "-B", selfpath, "--self-test"],
                                               capture_output=True, text=True, timeout=600,
                                               env=dict(lane_env, AIQT_INTERNAL_NAMES_SELFTEST_CHILD="1"))
                    except subprocess.TimeoutExpired:
                        failures.append("a child self-test under a malformed caller {} git configuration "
                                        "timed out".format(label))
                        continue
                    if child.returncode != 0 or "SELF-TEST PASS" not in child.stdout:
                        failures.append("a malformed caller {} git configuration changed the self-test verdict "
                                        "(the gate's tracked-file read must not read it): rc={} out={!r} "
                                        "err={!r}".format(label, child.returncode, child.stdout, child.stderr))

                # 22. DISCRIMINATING (an in-process self_test_main() restores the caller's environment
                #     exactly): a child imports this module, sets a caller environment (HOME at a scratch
                #     directory, XDG_CONFIG_HOME and the GIT_CONFIG_* pins unset, a caller GIT_* variable
                #     set), calls self_test_main() and compares os.environ with its snapshot from before the
                #     call. A scrub applied before the fixture git lifecycle saves the environment makes the
                #     lifecycle restore the scrubbed values (HOME at the fixture scratch, XDG_CONFIG_HOME and
                #     GIT_CONFIG_GLOBAL set, the caller's GIT_* variable gone) and fails this.
                probe = (
                    "import os\n"
                    "import sys\n"
                    "sys.path.append({tools!r})\n"
                    "import check_internal_names as cin\n"
                    "os.environ['HOME'] = {home!r}\n"
                    "for key in ('XDG_CONFIG_HOME', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM', "
                    "'GIT_CONFIG_NOSYSTEM'):\n"
                    "    os.environ.pop(key, None)\n"
                    "os.environ['GIT_AIQT_CALLER_PROBE'] = 'kept'\n"
                    "before = dict(os.environ)\n"
                    "rc = cin.self_test_main()\n"
                    "changed = sorted(k for k in set(before) | set(os.environ) if before.get(k) != os.environ.get(k))\n"
                    "print('ENVIRONMENT CHANGED: {{}}'.format(changed))\n"
                    "sys.exit(0 if rc == 0 and not changed else 3)\n"
                ).format(tools=toolsdir, home=str(clean_home))
                try:
                    proc = subprocess.run([sys.executable, "-I", "-B", "-c", probe], capture_output=True, text=True,
                                          timeout=600, env=dict(caller, AIQT_INTERNAL_NAMES_SELFTEST_CHILD="1"))
                    if proc.returncode != 0 or "ENVIRONMENT CHANGED: []" not in proc.stdout:
                        failures.append("an in-process self_test_main() expected to pass and restore the caller's "
                                        "environment exactly, got rc={} out={!r} err={!r}".format(
                                            proc.returncode, proc.stdout, proc.stderr))
                except subprocess.TimeoutExpired:
                    failures.append("the in-process self_test_main() environment probe timed out")
            finally:
                shutil.rmtree(poison, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for x in failures:
            print("  - " + x)
        return 1
    print("SELF-TEST PASS: clean generic content passes; a seeded provenance id, a fixed host path, and a "
          "hashed codename each fail (removing a layer fails a case); an internal-allow line is exempt from "
          "the structural layer; the whole capitalized internal role name is caught, while Architecture, "
          "architectural, the lowercase noun, the word inside a longer identifier, an internal-allow line and "
          "a SKIP_NAMES basename are not in the scoped scan; the real gate run on a fixture git repository "
          "catches the role name in a tracked file outside SCOPE_RELPATHS and in a tracked file whose basename "
          "is in SKIP_NAMES, exempts only the one internal-allow line, scans a binary-attributed file that "
          "decodes as UTF-8, skips a non-UTF-8 file with a non-text suffix and an untracked file, and exits 2 "
          "(cannot evaluate) on a tracked non-UTF-8 text file, a tracked file absent from the working tree and "
          "a root that is not a git repository; a GIT_CONFIG_GLOBAL pointed at a malformed file does not "
          "change the gate result (the gate's tracked-file read drops the GIT_* environment); a child "
          "self-test still passes with a malformed .gitconfig under the caller's HOME, a malformed git/config "
          "under the caller's XDG_CONFIG_HOME, or a malformed system configuration (supplied through a PATH "
          "shim, since the gate drops GIT_CONFIG_SYSTEM), so the gate's tracked-file read does not read the "
          "caller's home, XDG or system configuration during the self-test; an in-process self_test_main() "
          "restores the caller's environment exactly; a malformed hashes file is reported, never a silent empty denylist; a "
          "non-UTF-8 in-scope surface fails closed as unscannable, never a silent skip; a broken symlink "
          "(a present dentry with a missing target) at a scoped path and at the hash-file path each fails "
          "closed (re-raised), never read as absent; a scoped file "
          "whose BASENAME is in SKIP_NAMES is skipped by basename (not by full relpath); and the shipped "
          "gate-runner and CI-workflow paths (tools/run_all_checks.sh, .github/workflows/quality.yml) are "
          "live SCOPE_RELPATHS members, so a synthetic provenance id planted in each is caught (removing "
          "either from scope fails the case); an UNRECOGNIZED option (a misspelled --self-testx or a "
          "stray flag) is a loud exit 2, never a silent default scan that would let it masquerade as "
          "--self-test; a NON-REGULAR input (a FIFO) at the hash-file path and at a scoped path fails closed "
          "PROMPTLY (classified non-regular before any open) rather than blocking on the open; and a hostile "
          "sibling json.py/hashlib.py beside the script does NOT shadow a stdlib import (the sibling modules "
          "are imported via sys.path.append, keeping stdlib precedence), so the self-test still runs under -I "
          "rather than being silently neutered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
