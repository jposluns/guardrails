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

SCOPE, deliberately narrow and explicit (all but the role-name rule and the decision-id layer, whose
whole-tree scopes are described below). Layers 1 and 2 scan ONLY the QA-suite shipped surfaces plus the shipped
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

DECISION-ID LAYER, tree-wide (scan_tree_decision_ids). The internal maintainer-decision id shape (DECISION_ID)
is checked over the WHOLE working tree, walked fail-closed with the skips and text suffixes above, not only
over SCOPE_RELPATHS: no file in the tree carries one, so this layer has no legitimate occurrence to exempt.
A line carrying an internal-allow marker is exempt here as well. In this layer a file that is not valid
UTF-8 is skipped, where the whole-tree leak gate decodes it losslessly and scans it; a scoped surface that
is not valid UTF-8 still fails closed. The walk reads untracked and git-ignored files too (everything on
disk outside the skipped directories), does not follow a symlinked directory, and reads a file only when
its suffix is one of the leak gate's TEXT_SUFFIXES or it has no suffix, so a file under any other suffix
(a .csv, say) is not read. A bare ordinal with no further segment (D-3) is not matched. An unlistable
directory, an unreadable file and a non-regular file (a FIFO) each fail closed with exit 2; a file that
vanishes between the walk and the read is a fail-closed finding, as in the scoped layer.

SHIPPED TRACKER-LABEL LAYER, per-file ratchet (scan_shipped_tracker_labels). Four generic label SHAPES
(a tracker fix id, a review-round word next to a digit, a QA-round token, and a reviewer family named as
a review source) are counted per shipped file: every [[sources]] path of the working-tree
.aiqt/manifest.toml plus every tracked file under .github/ and opf/. The hand-delivered ratchet baseline
.aiqt/tracker-label-baseline.toml records, per shipped path that still carries hits, the hit COUNT per
shape (counts and paths only, never label text); a path absent from the baseline has zero. A count ABOVE
its baseline is a finding naming the path, the shape and each matching line (exit 1: no new label can
enter without a deliberate baseline edit), and a count BELOW its baseline is a finding telling the
maintainer to lower the baseline to the new count, so the baseline stays exact in both directions and
moves only through regeneration. `--write-baseline` prints the canonical baseline from a real scan; the
delivered file is that exact output, regenerated in the same reviewed change whenever an edit
legitimately moves a count: it is lowered as later per-area changes remove labels, and REVIEW POLICY,
stated here but NOT enforced by this gate, allows a raise only when a refresh merges upstream files
that already carry labels (the shipped set follows the release inventory, so growth merged from
upstream lands here as count increases); either movement is counts and paths only, visible whole in
the regenerated file's diff, and a label added without a regeneration fails. The gate itself cannot
tell merged growth from growth the same change adds: it accepts a regenerated baseline at whatever
counts a real scan yields, so holding a raise to refreshes is the reviewer's recount of the
regenerated file's diff against the merge parent, never a comparison this gate makes.
The gate ENFORCES the exactness: a baseline that is not the byte-exact canonical
render of its own entries is refused, so no TOML comment, alternate integer spelling, reordering or other
text the parser would drop can ride in the one file this layer skips by name. This layer honours no
internal-allow marker and no other skip-by-name (the gate's own
source is scanned like any other shipped file; its self-test vectors are assembled at run time); the
path-only allowlists TRACKER_LABEL_FROZEN (relpath plus sha256 pairs, exempt only while the file's RAW
BYTES still hash to the record) and TRACKER_LABEL_HISTORY both ship EMPTY. FAIL CLOSED (exit 2): a
missing or unparseable manifest, a manifest with no [[sources]] array, a listed path that is absent or
not a regular file (checked before any suffix or decode decision, so an excluded suffix never hides a
missing path; the baseline file itself is the one exception, skipped by name BEFORE that presence
check, and when absent it is the empty baseline, so every remaining hit then exceeds zero and fails
loud rather than silently), a text-suffix shipped file that
is not valid UTF-8, and a malformed, non-canonical, duplicate, unknown or non-shipped baseline entry.
Every present shipped file is read; one whose suffix is outside the leak gate's text-suffix list is
skipped only when its bytes do not decode as UTF-8 (the committed .zip archives), the role rule's
existing decode-then-skip rule, so a decodable file is scanned whatever its suffix; the one by-name skip
is the baseline file itself (its shape-key-next-to-count lines would
match the review-round pattern by construction; its closed schema plus the canonical-render byte check
above is what holds its content instead). DISCLOSED LIMITS of this layer: a label moved between shapes or between lines
within the same per-file count is not caught; the baseline is trusted input like any configuration file;
a regeneration admits any raise, including one the same change adds (mixed changes included, where an
added label is folded into the same count increase), so telling merged growth from added growth needs a
recount against the merge parent in review;
a label shape outside the four patterns (a bare one-letter revision token, a lower-case fix word with a
number, a model id used as a source, a family name for the product it names outside the parenthesized
family-then-version-digit form below, a split or encoded label) is not matched; prose that legitimately
puts a review-round word next to a digit (any letter case), an upper-case FIX word next to a number, a QA
word glued to a number, a reviewer family name next to a severity word, or a parenthesized family name
directly before a version digit (ordinary product-version prose included) is a false
positive this shape layer cannot avoid, absorbed by its path's baseline entry rather than exempted by
line; and archive contents are not unpacked.

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
file is exempt by name. Every tracked file whose suffix is TRACKED_ARCHIVE_SUFFIX (.zip, the format of the
committed skill archives) first goes through the leak gate's archive rule, whether or not its bytes decode
as UTF-8: check_leaks.verify_archive, the predicate built from the same functions the leak gate's scan
applies (the archive-size bound on a read that stops one byte past it, the parsed records covering the
file exactly, and every member read in full under zipfile's CRC and header checks; nothing is extracted to
disk). A .zip that fails it CANNOT BE EVALUATED and fails closed at exit 2, named with the leak gate's
reason. A .zip that passes is scanned when it decodes as UTF-8 and is otherwise skipped as binary, the only
non-UTF-8 tracked file the role rule skips. Any other non-UTF-8 tracked file whatever its suffix (a
malformed .csv, or a .csv that starts with the ZIP signature, say), a tracked path absent from the working
tree, or a tracked path that is not a readable regular file CANNOT BE EVALUATED and fails closed at exit 2,
the way an unreadable input does. A .gitattributes `binary` attribute is NOT a skip: a binary-attributed
file that decodes as UTF-8 is scanned. When git cannot list the tracked set (no git, not a repository, a
failed or empty listing) the rule cannot run and the gate exits 2.
WHAT THE ROLE RULE CANNOT SEE: an untracked or git-ignored file (outside the scoped scan), a generated
output that is not committed and is not handed to it as a tracked file (a built release archive, a rendered
site that is not checked in), the content of a tracked .zip's members, which the archive rule reads in
memory to verify the archive but this rule never scans (the leak gate scans them), a staged change that
differs from the working-tree copy (it reads the working tree), and the word split or encoded (an HTML
entity, a zero-width character) so the plain pattern does not match.

REVIEW-ROUND LABEL RULE, WHOLE TRACKED TREE. The review-round label patterns (_REVIEW_LABELS: a reviewer
family name followed by a QA round number, and a QA round number followed by a severity grade or a finding
id, in each case with at most one space between the parts, both case-insensitive) run inside the role rule
above, over the same tracked set with the same internal-allow exemption, skips and fail-closed exits, and
inside the scoped scan. "QA" alone, "quality", a bare grade word, a family name alone and a round number
with neither a family name before it nor a grade or finding id after it are not matched. The grade or
finding id must end at a word boundary, so a round number before an ordinary word that starts with a grade
("minority", "majority") or before an F, digits and then letters is not matched. A review label written in
another shape (a family name after the round number, a tab, two spaces or punctuation between the parts, or
a grade or finding id run straight into a letter, digit or underscore, say) is outside this rule.

Exit 0 clean, 1 on any finding or a malformed hashes file, 2 on a read error or a tracked file the role
rule cannot evaluate (fail-closed). `--self-test` proves the gate FAILS on a seeded internal name (a
provenance id, a host path, a hashed codename, the role name and a review-round label of each shape in a
tracked file outside SCOPE_RELPATHS, and a maintainer-decision id of each shape anywhere in the tree) and
passes clean generic content, so removing a
layer makes a case fail. It also runs the real scan over a planted fixture repository to prove the tree-wide
decision-id layer is wired in and fails closed on an unreadable file, an unlistable directory, a FIFO and a
vanished file, and over fixture repositories to prove the role rule skips a non-UTF-8 tracked file only when
it is a .zip the leak gate's archive rule accepts (a real ZIP archive passes, with the archive-size bound at
its exact size and one byte above too, and an accepted archive that decodes as UTF-8 is scanned; a malformed
.csv and a .csv carrying the ZIP signature each exit 2 as not valid UTF-8; a .zip without the signature, a
signature-prefixed .zip that is not an archive, valid UTF-8 or not, a .zip with one member failing its CRC, a
.zip of bytes ahead of an empty archive's end record, the archive with the bound one byte under its size,
and the archive followed by bytes past the bound each exit 2 named with the leak gate's reason, and the
archive deleted from the working tree exits 2 as absent). The self-test exits 0 only when
every case ran and passed; 1 when any case failed (it also prints the cases it did not run); otherwise, when
it skipped a case, 3. It never exits 0 after a skip. Skip reasons it prints include, as examples rather than
a complete set:
  "case 11 (a dangling symlink could not be created)"
  "case 12 (a dangling symlink could not be created)"
  "case 19 at the {} path (a FIFO could not be created)" ({} is hash-file, scoped-file or tracked-zip)
  "case 19 (os.mkfifo is unavailable)"
  "cases 19 to 23 and 25 (the child sentinel matched the parent pid)"
  "case 23 (the inherited-probe sentinel matched the parent pid)"
  "case 25 (an unreadable file could not be planted: permissions do not bind here)"
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
import tomllib
import zipfile
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
# The suffix of the one binary format the repository tracks, the ZIP archive (the committed skill archives
# under site/downloads/). Every tracked file under it goes through the leak gate's archive rule
# (check_leaks.verify_archive) whether or not its bytes decode as UTF-8, so the suffix only selects the rule:
# a file the rule refuses cannot be evaluated, and only a file it accepts can be skipped as binary. A
# tracked file in any other binary format cannot be evaluated until a rule for it is added here.
TRACKED_ARCHIVE_SUFFIX = ".zip"
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
# Internal REVIEW-ROUND LABEL patterns: a reviewer family name followed by a QA round number, and a QA round
# number followed by a severity grade (BLOCKER, MAJOR, MEDIUM or MINOR) or a finding id, in each case with at
# most one space between the parts (a tab, two spaces or punctuation there is not matched). A shipped
# comment or message says what the code guards and why, never which review round asked for it.
# Case-insensitive, since such labels are written in either case. Each needs the round NUMBER, so "QA" alone
# in prose, "quality", a bare grade word, a family name alone and a round number with neither a family before
# it nor a grade after it never match. The grade or finding id must end at a word boundary, so an ordinary
# word that starts with a grade ("minority", "majority", "mediums") or an F, digits and then letters is not
# a label; a grade or finding id run straight into a letter, digit or underscore (MINOR1, F2a) is therefore
# not matched either. Like the role-name pattern, these run over every tracked file, this
# one included; the pattern sources do not match themselves because a family name here is followed by a bar
# or a parenthesis and "QA" by a bracket.
_REVIEW_LABELS = [
    (re.compile(r'\b(?:codex|claude|gemini)[ ]?QA[0-9]+', re.IGNORECASE), "internal review-round label"),
    (re.compile(r'\bQA[0-9]+[ ]?(?:BLOCKER|MAJOR|MEDIUM|MINOR|F[0-9]+)\b', re.IGNORECASE),
     "internal review-round label"),
]
# The patterns the tracked-file rule applies to every tracked file (see scan_role_text).
_TRACKED_RULES = _ROLE_NAMES + _REVIEW_LABELS
# Reuse the leak gate's structural host/IP/token/account patterns verbatim (imported, never re-typed, so
# this file stays self-clean the way check_leaks.py does), and add the provenance-id, role-name and
# review-round label shapes.
STRUCTURAL = list(check_leaks.STRUCTURAL) + _PROVENANCE + _TRACKED_RULES

# Internal maintainer-decision id SHAPE (generic; carries no codename), checked over the WHOLE tree by
# scan_tree_decision_ids rather than over SCOPE_RELPATHS alone. A D- head that is an ordinal or an upper-case
# word of two or more characters, then one or more segments of upper-case letters and digits: an ordinal head
# with word segments, a segment carrying a digit, a word head with word segments, a final one-letter segment.
# It does NOT match a bare ordinal with no further segment (D-3), a lower-case check id or label (D-no-remote,
# D-a4), a lower-case prose word (D-part), or a D- glued to a longer word (GD-, PD-: no word boundary before
# the D).
DECISION_ID = (re.compile(r'\bD-(?:[0-9]+|[A-Z][A-Z0-9]+)(?:-[A-Z0-9]+)+\b'), "internal maintainer-decision id")

# --- shipped tracker-label layer (per-file ratchet) ---------------------------------------------------
# The four internal tracker-label SHAPES counted over every shipped file (the manifest [[sources]] paths
# plus the tracked files under .github/ and opf/). The patterns are generic shapes, never a live label:
# no contiguous label text sits in this source (checked by this very layer, which scans this file too).
_FAM = r"(?:codex|claude|gemini|fable|Codex|Claude|Gemini|Fable|CODEX|CLAUDE|GEMINI|FABLE)"
_SEV = (r"(?:MINOR|MEDIUM|MED|MAJOR|HIGH|LOW|BLOCKER|CRITICAL|NIT|minor|medium|major|high|low|blocker"
        r"|\[[HMLB]\]|findings?|QA)")
TRACKER_LABELS = (
    (re.compile(r"\bFIX[- ](?:[A-Z]?\d+|[A-Z])(?:-R\d+)?\b"), "internal tracker fix id"),
    (re.compile(r"\brounds?\W{0,3}\d", re.IGNORECASE), "internal review-round label"),
    (re.compile(r"\bQA-?\d+\b"), "internal QA-round label"),
    (re.compile(r"\b" + _FAM + r"(?:'s)?(?: PR #\d+)?(?: (?:QA )?(?:round|r)[- ]?\d+)?,? " + _SEV + r"(?![\w-])"
                r"|\b(?:CLAUDE|CODEX|GEMINI|FABLE)-F\d+\b"
                r"|\(" + _FAM + r" \d"
                r"|\b" + _FAM + r" (?:QA )?round[- ]?\d"), "reviewer family named as a review source"),
)
# The baseline's per-shape count keys, positionally parallel to TRACKER_LABELS.
TRACKER_SHAPE_KEYS = ("fix-id", "review-round", "qa-round", "reviewer-source")
# Path-only exemptions, both EMPTY at this release. A frozen pair exempts its path only while the file's
# bytes still hash to the recorded sha256 (a changed byte is scanned like any other file); a history
# relpath exempts a history record that has to quote a label. There is no exemption by pattern and no
# per-line marker for this layer.
TRACKER_LABEL_FROZEN = ()   # (relpath, sha256) pairs
TRACKER_LABEL_HISTORY = ()  # relpaths
TRACKER_MANIFEST_RELPATH = ".aiqt/manifest.toml"
TRACKER_BASELINE_RELPATH = ".aiqt/tracker-label-baseline.toml"


def _tracker_hits(text, labels=None):
    """Per-shape 1-based line numbers of every match in `text` (a line number repeats when one line
    carries two matches of one shape, so counts are match counts). No internal-allow exemption: this
    layer honours no line marker. `labels` lets the self-test show each pattern row is load-bearing."""
    rows = TRACKER_LABELS if labels is None else labels
    hits = {key: [] for key in TRACKER_SHAPE_KEYS}
    for number, line in enumerate(text.splitlines(), 1):
        for key, (pattern, _label) in zip(TRACKER_SHAPE_KEYS, rows):
            hits[key] += [number for _ in pattern.finditer(line)]
    return hits


def _shipped_tracker_set(root, tracked):
    """The sorted shipped set this layer scans: every [[sources]] path of the working-tree manifest plus
    every tracked relpath under .github/ or opf/. A missing or unparseable manifest, a manifest with no
    [[sources]] array at all (a misspelled table name must never read as an empty inventory), or a
    malformed sources entry, raises OSError to the fail-closed caller (exit 2); the manifest is read at
    run time, never baked in, so the scope follows the release inventory."""
    manifest = _read_present_text(root / TRACKER_MANIFEST_RELPATH)
    if manifest is None:
        raise OSError("{} is missing; the shipped set cannot be read".format(TRACKER_MANIFEST_RELPATH))
    try:
        data = tomllib.loads(manifest)
    except (ValueError, RecursionError, MemoryError) as exc:
        # TOMLDecodeError is a ValueError; tomllib can also raise a bare ValueError (an over-long
        # integer), RecursionError (deep nesting) or MemoryError, each disposed of here fail-closed.
        raise OSError("{} cannot be parsed ({!r})".format(TRACKER_MANIFEST_RELPATH, exc))
    if "sources" not in data:
        raise OSError("{} carries no [[sources]] array; the shipped set cannot be read".format(
            TRACKER_MANIFEST_RELPATH))
    sources = data["sources"]
    if not isinstance(sources, list):
        raise OSError("{}: [[sources]] is not an array".format(TRACKER_MANIFEST_RELPATH))
    shipped = set()
    for entry in sources:
        rel = entry.get("path") if isinstance(entry, dict) else None
        if (not isinstance(rel, str) or not rel or rel.startswith("/")
                or ".." in rel.split("/") or "\\" in rel):
            raise OSError("{}: a [[sources]] entry carries no usable relative path".format(
                TRACKER_MANIFEST_RELPATH))
        shipped.add(rel)
    shipped.update(rel for rel in tracked if rel.startswith((".github/", "opf/")))
    return sorted(shipped)


def load_tracker_baseline(root, shipped):
    """The ratchet baseline as {relpath: {shape key: count}}. An ABSENT baseline is an empty one (every
    path then has zero); a PRESENT baseline is validated CLOSED: only format-version = 1 and [[path]]
    entries, each entry a path plus at least one shape-count key from TRACKER_SHAPE_KEYS with an integer
    count of one or more, and the whole file must be the BYTE-EXACT canonical render of those entries
    (render_tracker_baseline over its own counts, the exact --write-baseline output), because the parser
    drops a TOML comment and normalizes an alternate integer spelling, so the closed schema alone can
    never see text riding in the one shipped file this layer skips by name. A malformed file, an unknown
    key, a duplicate path, an entry for a path outside the shipped set, or a non-canonical byte raises
    OSError to the fail-closed caller (exit 2), never a silent default."""
    raw = _read_present_bytes(root / TRACKER_BASELINE_RELPATH)
    if raw is None:
        return {}

    def refuse(reason):
        raise OSError("{}: {}".format(TRACKER_BASELINE_RELPATH, reason))

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        refuse("not valid UTF-8 ({})".format(exc))
    try:
        data = tomllib.loads(content)
    except (ValueError, RecursionError, MemoryError) as exc:
        # TOMLDecodeError is a ValueError; the other classes are tomllib's non-TOMLDecodeError
        # refusals (a bare ValueError, deep nesting, memory), each disposed of here fail-closed.
        raise OSError("{}: malformed TOML ({!r})".format(TRACKER_BASELINE_RELPATH, exc))
    unknown = set(data) - {"format-version", "path"}
    if unknown:
        refuse("unknown top-level key {!r}".format(sorted(unknown)[0]))
    version = data.get("format-version")
    # The TYPE is pinned as well as the value: bool is an int subclass and a TOML float compares equal
    # to its integer value, so true and 1.0 would otherwise both read as the integer 1.
    if type(version) is not int or version != 1:
        refuse("format-version must be the integer 1")
    entries = data.get("path", [])
    if not isinstance(entries, list):
        refuse("[[path]] is not an array")
    shipped_set = set(shipped)
    baseline = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
            refuse("a [[path]] entry carries no string path")
        rel = entry["path"]
        unknown = set(entry) - ({"path"} | set(TRACKER_SHAPE_KEYS))
        if unknown:
            refuse("entry {}: unknown key {!r}".format(rel, sorted(unknown)[0]))
        if rel in baseline:
            refuse("duplicate entry for {}".format(rel))
        if rel not in shipped_set:
            refuse("entry for {}, which is not a shipped path".format(rel))
        row = {}
        for key in TRACKER_SHAPE_KEYS:
            if key in entry:
                value = entry[key]
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    refuse("entry {}: {} must be an integer count of one or more (zero is the "
                           "absent default; drop the key)".format(rel, key))
                row[key] = value
        if not row:
            refuse("entry {} carries no shape count; drop the entry".format(rel))
        baseline[rel] = row
    canonical = render_tracker_baseline(
        {rel: {key: range(count) for key, count in row.items()} for rel, row in baseline.items()})
    if content != canonical:
        refuse("not the byte-exact canonical render of its own entries; regenerate it with "
               "--write-baseline (a comment, an alternate integer spelling or a reordering could "
               "otherwise carry text this layer never scans)")
    return baseline


def _collect_tracker_counts(root, tracked, frozen, history):
    """{relpath: per-shape line lists} over the shipped set. Every listed path but one must be PRESENT
    and a REGULAR file before any suffix or decode decision: an absent or non-regular shipped path
    raises OSError (fail closed, exit 2) whatever its suffix. The one exception is the baseline file
    itself, skipped by name BEFORE that presence check: an absent baseline reads as all-zero in
    load_tracker_baseline, so every remaining hit then exceeds zero and the gate fails loud, never a
    silent pass of label-bearing files. A present file is read as raw bytes and decoded
    as UTF-8; one whose suffix is outside TEXT_SUFFIXES is skipped only when its bytes do not decode
    (the committed .zip archives), the role rule's existing decode-then-skip rule, so a decodable file
    is scanned whatever its suffix, while a non-UTF-8 text-suffix shipped file raises to the same
    fail-closed caller. A frozen-allowlisted path whose RAW on-disk bytes still hash to its recorded
    sha256, and a history-allowlisted path, are skipped."""
    import hashlib
    frozen_map = dict(frozen)
    counts = {}
    for rel in _shipped_tracker_set(root, tracked):
        if rel == TRACKER_BASELINE_RELPATH:
            # The baseline itself is a shipped file whose shape-key-next-to-count lines would match the
            # review-round pattern by construction, so scanning it can never settle. Its content is held
            # instead by load_tracker_baseline's CLOSED schema plus its byte-exact canonical-render
            # check (shipped paths and integer counts only, rendered exactly as --write-baseline prints
            # them, so no comment or other label-bearing text can ride in it), the one by-name skip
            # this layer carries.
            continue
        raw = _read_present_bytes(root / rel)
        if raw is None:
            raise OSError("shipped path {} is absent from the working tree; fail closed".format(rel))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            suffix = Path(rel).suffix
            if suffix and suffix not in TEXT_SUFFIXES:
                continue
            raise OSError("shipped path {} is not valid UTF-8 ({}); fail closed".format(rel, exc))
        if rel in frozen_map and hashlib.sha256(raw).hexdigest() == frozen_map[rel]:
            continue
        if rel in history:
            continue
        counts[rel] = _tracker_hits(text)
    return counts


def _tracker_compare(counts, baseline):
    """The ratchet findings: per (shipped path, shape), a match count above its baseline (zero when the
    path has no entry) names the path, the shape and each matching line; a count below its baseline asks
    for the baseline to be lowered to the new count, so the baseline stays exact in both directions and
    moves only through a deliberate regeneration (--write-baseline).
    A label moved between shapes or between lines within one per-file count is a disclosed non-finding."""
    labels = dict(zip(TRACKER_SHAPE_KEYS, (label for _pattern, label in TRACKER_LABELS)))
    findings = []
    for rel in sorted(set(counts) | set(baseline)):
        hits = counts.get(rel, {})
        base = baseline.get(rel, {})
        for key in TRACKER_SHAPE_KEYS:
            lines = hits.get(key, [])
            have = len(lines)
            allowed = base.get(key, 0)
            if have > allowed:
                findings.append("{}: {} count {} exceeds its ratchet baseline {} ({}; lines {})".format(
                    rel, key, have, allowed, labels[key],
                    ", ".join(str(n) for n in sorted(set(lines)))))
            elif have < allowed:
                findings.append("{}: {} count {} is below its ratchet baseline {}; lower the baseline "
                                "entry to {} so the ratchet stays exact".format(
                                    rel, key, have, allowed, have))
    return findings


def scan_shipped_tracker_labels(root, tracked, frozen=None, history=None):
    """The shipped tracker-label layer: count the four label shapes over the shipped set and hold every
    per-file per-shape count exactly to the ratchet baseline. `tracked` is the tracked-file list run_gate
    already read; `frozen` and `history` default to the shipped (empty) path-only allowlists and are
    parameters so the self-test can show each exemption honoured and bounded."""
    frozen = TRACKER_LABEL_FROZEN if frozen is None else frozen
    history = TRACKER_LABEL_HISTORY if history is None else history
    counts = _collect_tracker_counts(root, tracked, frozen, history)
    baseline = load_tracker_baseline(root, _shipped_tracker_set(root, tracked))
    return _tracker_compare(counts, baseline)


def render_tracker_baseline(counts):
    """The canonical baseline text for `counts`: a fixed header, then one [[path]] entry per path that
    carries any hit, paths sorted, shape keys in TRACKER_SHAPE_KEYS order, zero counts omitted. The
    delivered baseline file must be byte-identical to this render over a real scan."""
    lines = [
        "# Ratchet baseline for the shipped tracker-label layer of tools/check_internal_names.py.",
        "# Generated by: python3 -I -B tools/check_internal_names.py --write-baseline",
        "# One [[path]] entry per shipped path that still carries hits, with its per-shape match",
        "# counts; paths and counts only, never label text. The gate refuses a count above an entry",
        "# and asks for the entry to be lowered when a count falls below it, so it stays exact.",
        "format-version = 1",
    ]
    for rel in sorted(counts):
        if not any(counts[rel].get(key) for key in TRACKER_SHAPE_KEYS):
            continue
        if '"' in rel or "\\" in rel or not all(" " <= ch <= "~" for ch in rel):
            raise OSError("shipped path {!r} cannot be rendered as a plain TOML string".format(rel))
        lines.append("")
        lines.append("[[path]]")
        lines.append('path = "{}"'.format(rel))
        for key in TRACKER_SHAPE_KEYS:
            if counts[rel].get(key):
                lines.append("{} = {}".format(key, len(counts[rel][key])))
    return "\n".join(lines) + "\n"


def write_baseline_main(root):
    """`--write-baseline`: print the canonical ratchet baseline from a real scan of the shipped set
    (exit 0), or fail closed (exit 2) on any input this layer cannot read."""
    try:
        counts = _collect_tracker_counts(root, tracked_files(root), TRACKER_LABEL_FROZEN,
                                         TRACKER_LABEL_HISTORY)
        rendered = render_tracker_baseline(counts)
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read a required input for --write-baseline ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    sys.stdout.write(rendered)
    return 0


def _present(path):
    """True when `path` is a present regular file, False when it is genuinely ABSENT (no dentry at all).
    Presence is classified with an S_ISREG check BEFORE any open (mirroring the QA adapter's
    _classify_presence), so a NON-REGULAR file (a FIFO above all, whose open() would BLOCK waiting for a
    writer, but also a device, socket, or directory) is a fail-closed OSError rather than a hanging open, and a
    BROKEN SYMLINK (a present dentry whose target is missing) re-raises fail-closed: every PRESENT dentry that
    is not a regular file raises OSError to the fail-closed caller (exit 2). path.stat() FOLLOWS a symlink, so
    a dangling link raises FileNotFoundError exactly as an absent path, and path.is_symlink() (lstat) is what
    tells a present-but-dangling dentry from a truly missing one."""
    try:
        st = path.stat()
    except FileNotFoundError:
        if path.is_symlink():
            raise
        return False
    if not stat.S_ISREG(st.st_mode):
        raise OSError("{} is present but not a regular file; fail closed".format(path))
    return True


def _read_present_text(path):
    """Read `path` as UTF-8 text, failing closed on a present-but-unreadable input WITHOUT blocking: _present
    classifies the path before any open. A genuinely ABSENT path returns None so a caller for whom absence is
    intended (the denylist) can treat it as empty; every PRESENT dentry that is not a readable regular file
    raises OSError to the fail-closed caller (exit 2)."""
    if not _present(path):
        return None
    return path.read_text(encoding="utf-8")


def _read_present_bytes(path):
    """_read_present_text's RAW-BYTE sibling for the tracker-label layer: the same presence
    classification (S_ISREG before any open so a FIFO never blocks, a broken symlink re-raised
    fail-closed, a genuinely absent path None), returning the file's bytes undecoded. The tracker
    layer hashes and decodes these same bytes itself: read_text's universal-newline translation would
    otherwise hash a normalized form that never existed on disk, letting a digest of that form exempt
    a file whose on-disk bytes differ."""
    try:
        st = path.stat()
    except FileNotFoundError:
        if path.is_symlink():
            raise
        return None
    if not stat.S_ISREG(st.st_mode):
        raise OSError("{} is present but not a regular file; fail closed".format(path))
    return path.read_bytes()


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


def scan_tree_decision_ids(root):
    """Return findings for the maintainer-decision id shape (DECISION_ID) over the WHOLE tree under root,
    walked fail-closed (an unlistable directory raises OSError) with SKIP_DIRS, SKIP_NAMES and TEXT_SUFFIXES
    as in _iter_scope. A line carrying an internal-allow marker is exempt, as in scan_text. A present file
    that is not a readable regular file raises OSError (through _read_present_text, so a FIFO never blocks);
    a file that vanished during the walk is a fail-closed finding; a file that is not valid UTF-8 is skipped
    (the whole-tree leak gate instead decodes such a file losslessly and scans it)."""
    pattern, label = DECISION_ID
    findings = []
    for path in sorted(walk_files(root, SKIP_DIRS)):
        if path.name in SKIP_NAMES:
            continue
        if path.suffix and path.suffix not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            text = _read_present_text(path)
        except UnicodeDecodeError:
            continue
        if text is None:
            findings.append("{}: file vanished during the tree scan; fail-closed".format(rel))
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if "internal-allow" not in line and pattern.search(line):
                findings.append("{}:{}: {}".format(rel, number, label))
    return findings


def tracked_files(root):
    """The sorted tracked relpaths under `root` from `git ls-files -z --cached`, the role rule's input set.
    Pinned like gen_manifest's index read: the GIT_* environment is dropped (a repository-selecting variable
    cannot redirect the read), core.fsmonitor=false (a repository-configured hook never runs under a gate
    read), protocol.allow=never and GIT_NO_LAZY_FETCH=1. Unlike that read, which also pins the caller's
    system, home and XDG git configuration away, a real-checkout scan here keeps the caller's on-disk git
    configuration (where safe.directory trust for the checkout lives), so a malformed file there fails the
    scan closed (exit 2); self-test case 21 relies on this read honouring that configuration to prove the
    self-test's lifecycle isolation (case 23 builds on case 21). A self-test
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
    """(line, label) role-name and review-round label findings in `text`. A line carrying an `internal-allow`
    marker is exempt; the marker exempts that line only, never the rest of the file."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if "internal-allow" in line:
            continue
        found += [(number, label) for pattern, label in _TRACKED_RULES if pattern.search(line)]
    return found


def scan_tracked_roles(root, relpaths):
    """Return (findings, unevaluable) for the role rule (the role name and the review-round labels) over the
    tracked `relpaths`, independent of SCOPE_RELPATHS and SKIP_NAMES. A file whose suffix is
    TRACKED_ARCHIVE_SUFFIX goes through check_leaks.verify_archive, the leak gate's archive rule, whether or
    not its bytes decode as UTF-8: one the rule refuses is unevaluable, named with the leak gate's reason (and
    the member, when one member is the cause); one it accepts is scanned when its bytes decode as UTF-8 and is
    otherwise skipped as binary. Any other file that is not valid UTF-8, whatever its suffix, and a tracked
    path absent from the working tree are unevaluable too. A tracked path that is present but not a regular
    file raises OSError (exit 2) through _present, before any open, and so does a read error."""
    findings, unevaluable = [], []
    cannot = "; the role-name and review-round label rule cannot evaluate it"
    for rel in relpaths:
        path = root / rel
        try:
            if path.suffix != TRACKED_ARCHIVE_SUFFIX:
                text = _read_present_text(path)
            elif _present(path):
                data = check_leaks.verify_archive(path)
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # an archive the leak gate reads in full, in binary form: skipped
            else:
                text = None
        except UnicodeDecodeError:
            unevaluable.append("{}: tracked file is not valid UTF-8{}".format(rel, cannot))
            continue
        except check_leaks.CannotEvaluate as exc:
            unevaluable.append("{}: tracked .zip fails the leak gate's archive rule ({}{}){}".format(
                rel, "" if exc.member is None else "member " + exc.member + ": ", exc, cannot))
            continue
        if text is None:
            unevaluable.append("{}: tracked file is absent from the working tree{}".format(rel, cannot))
            continue
        for number, label in scan_role_text(text):
            findings.append("{}:{}: {}".format(rel, number, label))
    return findings, unevaluable


def run_gate(root):
    """The real scan of the tree at `root`: the scoped layers over SCOPE_RELPATHS, the role rule over every
    tracked file, and the decision-id layer over the whole tree. Returns the exit code and prints the report."""
    try:
        hashes, maxn, findings = load_hashes(root)
        findings = list(findings)
        findings += scan_scope(root, SCOPE_RELPATHS, hashes, maxn)
        tracked = tracked_files(root)
        role_findings, unevaluable = scan_tracked_roles(root, tracked)
        findings += role_findings
        findings += scan_tree_decision_ids(root)
        findings += scan_shipped_tracker_labels(root, tracked)
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read a required input (denylist, scoped surface, tracked file, tree, "
              "shipped manifest or tracker-label baseline) ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    # A role-name hit inside a tracked scoped file is reported by both scans in the same form; report it once.
    findings = sorted(set(findings))
    if findings:
        print("FAIL: {} finding(s): internal-name leak(s) in the scanned surfaces, decision id(s) in the "
              "tree, or tracker-label count(s) off the shipped ratchet baseline".format(len(findings)))
        for finding in findings:
            print("  " + finding)
    if unevaluable:
        print("error: the role-name and review-round label rule cannot evaluate {} tracked file(s); "
              "fail-closed".format(len(unevaluable)), file=sys.stderr)
        for item in unevaluable:
            print("  " + item, file=sys.stderr)
        return 2
    if findings:
        return 1
    print("PASS: no internal provenance vocabulary in the scanned QA-suite surfaces, no internal role "
          "name or review-round label in any tracked file, no maintainer-decision id in the tree, and "
          "every shipped tracker-label count equal to its ratchet baseline")
    return 0


def main():
    root = Path(__file__).resolve().parents[1]
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test (it implies the
    # self-test, as in the sibling registered runners).
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    # UNKNOWN-OPTION REJECTION precedes the dispatch: this gate accepts only --self-test, --write-baseline
    # alone, the --execution-report form above, or no args for a real scan, so any other token (a
    # misspelled --self-testx, a stray flag) is a LOUD exit 2, never a silent fall-through to the default
    # scan path that would exit without running the self-test the caller asked for (which would let
    # --self-testx masquerade as --self-test in CI parity).
    if argv == ["--write-baseline"]:
        return write_baseline_main(root)
    unknown = [a for a in argv if a != "--self-test"]
    if unknown or len(argv) > 1:
        print("error: unrecognized option {!r}; fail closed".format(
            (unknown or argv)[0]), file=sys.stderr)
        return 2
    if "--self-test" in argv:
        if report_path is not None:
            # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py);
            # nothing writes it in band. Imported only here: the self-test's scratch copies run without
            # a report and without that sibling module beside them.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
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
    exactly as it found it (self-test case 22). The snapshot taken here, before the lifecycle (inside it
    os.environ carries the fixture scrub and the wrapper's PATH), holds only the caller's _CALLER_VARS
    entries, PATH and TMPDIR. Case 21's lanes and controls, the case 22 probe and case 23's lanes are built
    from it, and every other variable they pass is a fixture value they set themselves. The git-fixture-env
    trust check that allows caller_env_without_git() only as the env of literal archives of the real
    checkout is syntactic: it matches that helper's name, so it would not see an inline copy of the helper's
    body. This snapshot carries only the listed variables (no HOME, no XDG_* and no GIT_* variable), so it
    is not a caller-environment use that check is meant to stop."""
    from _git_fixture_env import fixture_git_lifecycle
    caller_env = _caller_snapshot(os.environ)
    with fixture_git_lifecycle():
        return _self_test(caller_env)


# The only caller variables self-test cases 21 to 23 pass on to the runs they launch (see _caller_snapshot).
# PATH finds the caller's git, which the lanes' PATH shims and the system-config pin put themselves in front
# of; TMPDIR keeps those runs' fixture directories in the caller's temporary directory. No locale variable is
# carried, so git prints untranslated messages, which the case 21 controls match ("bad config").
_CALLER_VARS = ("PATH", "TMPDIR")


def _caller_snapshot(environ):
    """The _CALLER_VARS entries of `environ`, and nothing else from it: never HOME, XDG_CONFIG_HOME, any
    other XDG_* variable or any GIT_* variable, so a run built from this reads no caller configuration."""
    return {k: environ[k] for k in _CALLER_VARS if k in environ}


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the emitted report against the
# registry as an exact set, and the in-run self-guard at the end of _self_test reconciles the same set on
# a direct run whenever the registry file sits beside this script (the self-test's scratch copies run
# without it, so an absent registry is a disclosed skip of that guard, not of the checks themselves).
SUITE_ID = "internal-names-selftest"
EXECUTED = []


# A self-test that launches a child self-test sets _CHILD_SENTINEL in the child's environment to its OWN pid,
# and the child skips the subprocess-launching cases 19 to 23 and 25 (its parent runs them; a child running them
# would recurse). Case 23 likewise sets _INHERITED_PROBE to its own pid in three of the runs it launches (its
# two mutated lanes and its clean lane), which skips case 23 alone. A value is honoured only when it equals
# this process's parent pid. That match does not prove a self-test launched this process: a caller that
# exports its own pid, a "1" reaching a run whose parent is PID 1, or a pid reused by an unrelated parent is
# honoured as well. So a run that skipped a case and failed none, a recognized child included, exits 3; a
# run that failed a case exits 1 whether or not it also skipped one; neither exits 0. A parent accepts a
# child only by exit 3 together with the child line.
_CHILD_SENTINEL = "AIQT_INTERNAL_NAMES_SELFTEST_CHILD"
_INHERITED_PROBE = "AIQT_INTERNAL_NAMES_SELFTEST_INHERITED_PROBE"
# The exit status of a self-test that skipped a case and failed none: never 0, and distinct from 1, which a
# run that failed a case returns whether or not it also skipped one.
_INCOMPLETE_EXIT = 3


def _set_by_launcher(var):
    """True only when the environment variable `var` holds this process's parent pid (see _CHILD_SENTINEL)."""
    value = os.environ.get(var, "")
    return value.isascii() and value.isdigit() and int(value) == os.getppid()


def _stale_sentinel_values(own_pid, parent_pid):
    """The two inherited child-sentinel values case 23 runs: the first two of "1", parent_pid, own_pid + 1
    and own_pid + 2 that are neither own_pid nor a value already chosen. A run this one launches sees own_pid
    as its parent pid and honours only that value, so any other value stands for an inherited one. That is
    "1" and parent_pid unless this run or its parent is PID 1: a run that is PID 1 gets parent_pid and
    own_pid + 1 (["0", "2"] for a parent pid 0), or own_pid + 2 when parent_pid is own_pid + 1, and a run
    whose parent is PID 1 gets "1" and own_pid + 1. A replacement need not be a stale pid (own_pid + 1 can
    name a live process); it is an inherited value only because it is not own_pid. Self-test case 24 makes
    four direct calls, as (own_pid, parent_pid): (7, 5), (1, 0), (1, 2) and (7, 1). When case 23 runs, it
    runs one lane for each of the two values."""
    values = []
    for candidate in ("1", str(parent_pid), str(own_pid + 1), str(own_pid + 2)):
        if len(values) < 2 and candidate != str(own_pid) and candidate not in values:
            values.append(candidate)
    return values


def _load_ratchet_mutant(source, directory, tag):
    """Write one ratchet-mutant module under the self-test's scratch directory and load it with
    importlib (never a dynamic code string). This helper is the self-test's ONE code-loading site,
    so the dangerous-api allowlist entry is scoped to it and a code sink anywhere else in the
    self-test stays a finding."""
    import importlib.util
    path = Path(directory) / "tracker_mutant_{}.py".format(tag)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("tracker_mutant_{}".format(tag), str(path))
    module = importlib.util.module_from_spec(spec)
    saved_sys_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_sys_path
    return module


# --- self-test --------------------------------------------------------------------------------------
def _self_test(caller_env=None):
    import hashlib
    import shutil
    import tempfile

    failures = []
    # Each case this run does not run, with the reason; the full PASS text is printed only when it is empty.
    not_run = []

    def check(check_id, ok, detail=""):
        # The instrumented choke point for the tracker-label cases (27 to 31): every call records its
        # literal id in EXECUTED (reconciled by tools/check_selftest_execution.py against the registry)
        # and a failed one lands in `failures` exactly like the numbered cases above it.
        EXECUTED.append(check_id)
        if not ok:
            failures.append("{}: {!r}".format(check_id, detail))

    launched_child = _set_by_launcher(_CHILD_SENTINEL)
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
        else:
            not_run.append("case 11 (a dangling symlink could not be created)")

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
        else:
            not_run.append("case 12 (a dangling symlink could not be created)")

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

        # 27. DISCRIMINATING (internal review-round labels): a reviewer family name followed by a QA round
        #     number, and a QA round number followed by a severity grade (BLOCKER included) or a finding id,
        #     are each caught by the tracked-file rule (scan_role_text) and by the scoped scan, in either
        #     case and with or without the space. Removing _REVIEW_LABELS, or either of its patterns, leaves
        #     hits unflagged; making them case-sensitive misses the lower-case hits; dropping BLOCKER from
        #     the grade group leaves the BLOCKER hit unflagged; allowing one digit only in the finding id
        #     leaves the F12 hit unflagged. The controls stay unflagged, and each names the mutation it
        #     catches: dropping the round-number requirement in pattern 1 flags a family name before "QA"
        #     alone, dropping it in pattern 2 flags "QA" before a bare grade, dropping pattern 1's leading
        #     word boundary flags a family name glued to a longer word, dropping pattern 2's leading word
        #     boundary flags a round glued to a longer word, dropping the grade or finding id flags a round
        #     number alone, dropping the digit from the finding id leaves the F4 and F12 hits unflagged (with
        #     the trailing word boundary, a round number before an ordinary word that starts with F stays
        #     unmatched either way), dropping pattern 2's trailing word boundary flags a round number before
        #     "minority", "majority", "mediums", "Blockers" or an F, digits and then letters, narrowing that
        #     boundary to "no letter follows" flags the F, two digits and then letters, and the grade run
        #     straight into a digit, narrowing it to "no letter or digit follows" flags the grade or finding
        #     id run straight into an underscore, widening the finding id to zero digits (one or more digits
        #     is part of the pattern) flags the bare F after a round number, and widening either
        #     pattern's one optional space (to any run of spaces, any whitespace, any character, or a set
        #     that adds any one ASCII punctuation character) flags two spaces, a tab or that punctuation
        #     character between the parts, the documented separator limit: each pattern has a control for
        #     two spaces, a tab and every character of string.punctuation. Every vector is assembled at
        #     runtime, so no contiguous label sits in this source (the rule scans this file). The round
        #     numbers in the hits, the controls and the fixtures below are synthetic three-digit values naming
        #     no real round, and the subjects are neutral.
        write_hashes([])
        qa = "QA"
        review_label = "internal review-round label"
        label_hits = (
            "codex " + qa + "932: a sample note",
            "Claude" + qa + "909 wrote this line",
            "gemini " + qa.lower() + "935",
            "codex/claude " + qa + "934",
            qa + "916 MINOR-1: an example remark",
            qa + "904 MAJOR",
            qa + "917 MAJOR 2: a sample item",
            qa + "929 BLOCKER 1: a placeholder item",
            qa.lower() + "907 medium",
            qa + "919 F4: a sample entry",
            qa + "918 F12 in a sample list",
            qa.lower() + "908f2",
        )
        label_controls = (
            "the quality gate passes",
            "the QA suite and its QA skills",
            "a MAJOR change, then MINOR-1 and F2",
            "codex, claude and gemini, each alone",
            "the codex " + qa + " suite, with no round number",
            "the " + qa + " MAJOR backlog, with no round number",
            "a mutation vector of round " + qa + "901/" + qa + "902",
            "a planted " + qa + "945-CAUSE marker and a " + qa.lower() + "946-st-size probe",
            "vector:codex" + "901-1 and vector:claude" + "901-3",
            "a glued token non" + qa + "903 F1 and pre" + qa + "916 MINOR",
            "an autocodex " + qa + "901 build and a preclaude " + qa + "902 run",
            "the " + qa + "905 Final summary and a " + qa.lower() + "906 follow-up",
            "codex  " + qa + "937 and claude\t" + qa + "938, two spaces and a tab apart",
            "gemini:" + qa + "939 and codex: " + qa + "940, punctuation between",
            qa + "941  MINOR and " + qa + "942\tMAJOR, two spaces and a tab apart",
            qa + "943:F1 and " + qa + "944-BLOCKER, punctuation between",
            qa + "949 minority participation",
            qa + "950 majority vote",
            qa.lower() + "951 mediums and " + qa + "952 Blockers",
            qa + "953 F2fa, an F, a digit and then letters",
            qa + "954 F12abc, an F, digits and then letters",
            qa + "955 F: a bare F with no digits after it",
            qa + "956 MINOR1, a grade run straight into a digit",
            qa + "957 MINOR_note and " + qa + "958 F2_x, a grade and a finding id run into an underscore",
        )
        import string
        for mark in string.punctuation:
            label_controls += ("codex" + mark + qa + "947, one punctuation character between",
                               qa + "948" + mark + "MINOR, one punctuation character between")
        for text in label_hits:
            found = scan_role_text(text + "\n")
            if not found or {item for _number, item in found} != {review_label}:
                failures.append("review-round label {!r} expected a tracked-rule finding, got {}".format(text, found))
            state, f = run(text + "\n")
            if state != "ok" or not any(review_label in x for x in f):
                failures.append("review-round label {!r} expected a scoped finding, got {} {}".format(text, state, f))
        for text in label_controls:
            found = scan_role_text(text + "\n")
            state, f = run(text + "\n")
            if found or state != "ok" or f:
                failures.append("review-round label control {!r} expected no finding, got {} {} {}".format(
                    text, found, state, f))
        allowed = label_hits[0] + "  # internal-allow\n"
        if scan_role_text(allowed):
            failures.append("an internal-allow line holding a review-round label expected exemption, got {}".format(
                scan_role_text(allowed)))

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
        # The real gate now also runs the shipped tracker-label layer, which fails closed without a
        # shipped manifest; this fixture holds an explicitly empty inventory (a bare sources = [] array,
        # since a manifest with no sources array at all is refused), so the role and decision-id
        # expectations below stay the deciding layers.
        (repo / ".aiqt").mkdir()
        (repo / ".aiqt" / "manifest.toml").write_text("format-version = 1\nsources = []\n",
                                                      encoding="utf-8")
        def gate(at):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = run_gate(at)
            return rc, out.getvalue() + err.getvalue()

        role = "internal role name"
        (repo / "docs").mkdir()
        (repo / "tools").mkdir()
        (repo / "docs" / "outside.md").write_text("Signed off by the Architect.\n", encoding="utf-8")  # internal-allow
        # A review-round label of each shape on lines 1 and 2 and a control on line 3, assembled at runtime (case 27).
        (repo / "docs" / "labels.md").write_text("checked by codex " + qa + "932\n" + qa + "916 MINOR-1 in a sample\n"
                                                 "the QA suite runs every quality check\n", encoding="utf-8")
        (repo / "tools" / "check_leaks.py").write_text("# the Architect said so\n", encoding="utf-8")  # internal-allow
        (repo / "notes.txt").write_text("Architect kept here  # internal-allow\n"  # internal-allow
                                        "the Architect again\n", encoding="utf-8")  # internal-allow
        (repo / "data.bin").write_text("binary-attributed but UTF-8: Architect\n", encoding="utf-8")  # internal-allow
        (repo / ".gitattributes").write_text("data.bin binary\nblob.zip binary\n", encoding="utf-8")
        with zipfile.ZipFile(repo / "blob.zip", "w", zipfile.ZIP_STORED) as blob:
            blob.writestr("note.bin", b"\x80 Architect \xff\n")  # internal-allow
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
            # DISCRIMINATING (review-round labels in the whole tracked tree): both label shapes in a tracked file
            # outside SCOPE_RELPATHS are caught by the real gate and the control line is not. Leaving
            # _REVIEW_LABELS out of the tracked-file rule drops these findings and fails the case.
            for expect in ("docs/labels.md:1: " + review_label, "docs/labels.md:2: " + review_label):
                if rc != 1 or expect not in report:
                    failures.append("tracked review-round label {!r} expected (exit 1), got rc={} {!r}".format(
                        expect, rc, report))
            if "docs/labels.md:3:" in report:
                failures.append("the review-round label control line expected no finding, got {!r}".format(report))
            # 15. DISCRIMINATING (the allow marker exempts ONE line): line 1 of notes.txt carries the marker
            #     and is exempt; line 2 does not and is caught. A whole-file exemption drops the line-2
            #     finding; ignoring the marker adds a line-1 finding; either fails the case.
            if "notes.txt:2: " + role not in report or "notes.txt:1: " + role in report:
                failures.append("internal-allow expected to exempt notes.txt line 1 only, got {!r}".format(report))
            # 16. DISCRIMINATING (binary and non-UTF-8 files follow the stated rule): a .gitattributes
            #     binary file that decodes as UTF-8 is scanned (data.bin is caught); a non-UTF-8 .zip the leak
            #     gate's archive rule accepts is skipped (blob.zip, a real ZIP archive built with zipfile, is
            #     neither caught nor unevaluable; case 28 covers the inputs that are not skipped); and
            #     an untracked file is out of the role rule's reach (untracked.md, a disclosed limit).
            if "data.bin:1: " + role not in report:
                failures.append("a binary-attributed UTF-8 tracked file expected to be scanned, got {!r}".format(
                    report))
            if "blob.zip" in report or "untracked.md" in report:
                failures.append("a tracked ZIP archive and an untracked file expected to be outside "
                                "the role rule, got {!r}".format(report))
            # DISCRIMINATING (the tracked-file read drops the GIT_* environment): GIT_CONFIG_GLOBAL pointed at
            # a malformed file must not change the gate's result. A control first proves the file breaks a git
            # read that honours it (a nonzero exit reporting "bad config", so a failure for another reason is
            # not taken as proof); then the gate runs with it set in the process environment and must give
            # the same exit code and report as the run above. A tracked-file read that inherits the caller's
            # GIT_* environment instead of dropping it fails on the malformed file (exit 2) and fails this.
            # This guards the drop in tracked_files only; case 21 guards the home, XDG and system isolation
            # of the self-test entry point.
            malformed = tmp / "malformed-global-gitconfig"
            malformed.write_text("[core\n\tbroken\n", encoding="utf-8")
            control = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached"], capture_output=True,
                                     text=True, env=_git_fixture_env.git_fixture_env(GIT_CONFIG_GLOBAL=str(malformed)),
                                     timeout=60)
            if control.returncode == 0 or "bad config" not in control.stderr:
                failures.append("a malformed GIT_CONFIG_GLOBAL file expected to break a git read that honours it "
                                "(the global-config vector would be vacuous), got rc={} {!r}".format(
                                    control.returncode, control.stderr))
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
        # Outside a git repository the tracked set cannot be listed: the gate fails closed (exit 2)
        # NAMING the git listing. The fixture holds a valid empty manifest so this exit 2 can only come
        # from the listing: without one, a listing failure silently read as an empty tracked set (which
        # must stay a fail-closed error) could hide behind the missing manifest's own fail-closed exit,
        # and this assertion would still see a 2.
        norepo = tmp / "not-a-repo"
        norepo.mkdir()
        (norepo / ".aiqt").mkdir()
        (norepo / ".aiqt" / "manifest.toml").write_text("format-version = 1\nsources = []\n",
                                                        encoding="utf-8")
        rc, report = gate(norepo)
        if rc != 2 or "git ls-files" not in report:
            failures.append("a root that is not a git repository expected exit 2 naming the git listing, "
                            "got rc={} {!r}".format(rc, report))

        # 28. DISCRIMINATING (every tracked .zip goes through the leak gate's archive rule,
        #     check_leaks.verify_archive, whether or not its bytes decode as UTF-8, and only an archive the
        #     rule accepts is skipped as binary): a real ZIP archive with non-UTF-8 bytes, tracked beside a
        #     clean file, passes (exit 0). Each of the following, tracked in turn, cannot be evaluated (exit 2)
        #     with the expected report: a malformed .csv holding a review-round label and a .csv that starts
        #     with the ZIP signature (each reported as not valid UTF-8, since only the .zip suffix selects the
        #     archive rule); and, each named with the leak gate's reason, a .zip without the signature, a
        #     signature-prefixed .zip that is not an archive, a signature-prefixed .zip that is not an archive
        #     and whose bytes are valid UTF-8, a real archive with one stored member byte changed (zipfile
        #     opens it; its CRC fails when the member is read), and a review-round label in bytes ahead of an
        #     empty archive's end record (zipfile opens it; the records do not cover its bytes). Skipping by a
        #     suffix outside TEXT_SUFFIXES lets the .csv read clean; skipping on the signature, or on zipfile
        #     opening the file, lets the non-archive, the CRC-failing and the prefixed .zip read clean; applying
        #     the rule only to bytes that are not valid UTF-8 lets the valid UTF-8 one read clean; never
        #     skipping fails the archive; each fails this case. An archive the rule accepts whose bytes are
        #     all valid UTF-8 (one stored member holding a review-round label) is scanned and the label found
        #     (exit 1): skipping every accepted archive drops the finding. The archive-size bound
        #     (check_leaks.MAX_ARCHIVE_FILE_SIZE, patched around the archive's exact size) is then checked at
        #     both ends: one byte above the size and at it, the archive passes; one byte below, it cannot be
        #     evaluated, named with the size reason; and with the bound at the size, the same archive followed
        #     by a review-round label past the bound cannot be evaluated either, named with the size reason.
        #     Dropping the size check passes the archive one byte over the bound, and a read that stops at the
        #     bound instead of one byte past it reads the whole archive and skips the label behind it. Last, the
        #     tracked archive deleted from the working tree is reported as absent (exit 2).
        binrepo = tmp / "binary-repo"
        binrepo.mkdir()
        # An explicitly empty shipped inventory keeps the tracker-label layer from failing closed here, so
        # the archive rule and the tracked-file rule alone decide each result (as in the fixtures above).
        (binrepo / ".aiqt").mkdir()
        (binrepo / ".aiqt" / "manifest.toml").write_text("format-version = 1\nsources = []\n",
                                                         encoding="utf-8")
        (binrepo / "clean.md").write_text("A clean note.\n", encoding="utf-8")
        with zipfile.ZipFile(binrepo / "archive.zip", "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("member.bin", b"\xff\xfe stored bytes \xff\n")
        archive_bytes = (binrepo / "archive.zip").read_bytes()
        label_bytes = ("checked by codex " + qa + "932\n").encode("utf-8")
        plain_zip = b"PK\x03\x04plain text in valid UTF-8\n"
        stored_at = archive_bytes.index(b"stored bytes")
        corrupt_zip = archive_bytes[:stored_at] + b"S" + archive_bytes[stored_at + 1:]
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, "w"):
            pass
        prefixed_zip = b"PK\x03\x04 \xff " + label_bytes + empty.getvalue()
        # An accepted archive whose bytes are all valid UTF-8: one stored member with a fixed timestamp and a
        # read-only mode whose bytes are ASCII (zipfile writes mode 0o600, a 0x80 byte, when none is set), its
        # content ending in a counter raised until the CRC holds no byte that breaks the decode.
        utf8_zip = None
        for counter in range(4096):
            built = io.BytesIO()
            note = zipfile.ZipInfo("note.txt", (1980, 1, 1, 0, 0, 0))
            note.external_attr = 0o444 << 16
            with zipfile.ZipFile(built, "w") as archive:
                archive.writestr(note, label_bytes + "counter {}\n".format(counter).encode("ascii"))
            try:
                built.getvalue().decode("utf-8")
            except UnicodeDecodeError:
                continue
            utf8_zip = built.getvalue()
            break
        guarded = [("archive", archive_bytes, False), ("CRC-failing archive", corrupt_zip, False),
                   ("label ahead of an empty archive", prefixed_zip, False),
                   ("valid UTF-8 non-archive", plain_zip, True)]
        if utf8_zip is not None:
            guarded.append(("valid UTF-8 archive", utf8_zip, True))
        for what, content, decodes in guarded:
            try:
                content.decode("utf-8")
                valid = True
            except UnicodeDecodeError:
                valid = False
            if valid != decodes:
                failures.append("the case 28 {} expected {}to decode as UTF-8, so its vector would be "
                                "vacuous".format(what, "" if decodes else "not "))
        init = subprocess.run(["git", "-C", str(binrepo), "init", "-q"], capture_output=True, text=True,
                              env=_git_fixture_env.git_fixture_env(), timeout=60)
        add = subprocess.run(["git", "-C", str(binrepo), "add", "--", "clean.md", "archive.zip"],
                             capture_output=True, text=True, env=_git_fixture_env.git_fixture_env(), timeout=60)
        if init.returncode != 0 or add.returncode != 0:
            failures.append("the case 28 fixture could not be built: init rc={} add rc={} {!r}".format(
                init.returncode, add.returncode, init.stderr + add.stderr))
        else:
            rc, report = gate(binrepo)
            if rc != 0 or "archive.zip" in report:
                failures.append("a tracked ZIP archive expected to pass (exit 0), got rc={} {!r}".format(rc, report))
            not_utf8 = ": tracked file is not valid UTF-8"
            rule = ": tracked .zip fails the leak gate's archive rule ("
            not_archive = rule + "not a readable ZIP archive: "
            for name, content, expect in (
                    ("review.csv", label_bytes + b"\xff", not_utf8),
                    ("signed.csv", b"PK\x03\x04\xff\xfe not an archive \xff\n", not_utf8),
                    ("unsigned.zip", b"\xff\xfe not an archive \xff\n", not_archive),
                    ("malformed.zip", b"PK\x03\x04\xff\xfe not an archive \xff\n", not_archive),
                    ("plain.zip", plain_zip, not_archive),
                    ("corrupt.zip", corrupt_zip, rule + "member member.bin: member cannot be read: BadZipFile"),
                    ("prefixed.zip", prefixed_zip,
                     rule + "the parsed records do not cover the archive's bytes exactly: {} byte(s) at offsets 0 "
                     "to {} belong to no record".format(len(prefixed_zip) - 22, len(prefixed_zip) - 23))):
                (binrepo / name).write_bytes(content)
                add = subprocess.run(["git", "-C", str(binrepo), "add", "--", name], capture_output=True,
                                     text=True, env=_git_fixture_env.git_fixture_env(), timeout=60)
                rc, report = gate(binrepo)
                wrong = name + (rule if expect == not_utf8 else not_utf8)
                if add.returncode != 0 or rc != 2 or name + expect not in report or wrong in report:
                    failures.append("a tracked {} expected cannot-evaluate (exit 2) reported as {!r}, got rc={} "
                                    "{!r}".format(name, expect, rc, report))
                remove = subprocess.run(["git", "-C", str(binrepo), "rm", "-q", "-f", "--", name],
                                        capture_output=True, text=True, env=_git_fixture_env.git_fixture_env(),
                                        timeout=60)
                if remove.returncode != 0:
                    failures.append("the case 28 file {} could not be removed: {!r}".format(name, remove.stderr))
            if utf8_zip is None:
                failures.append("no case 28 archive with bytes that are all valid UTF-8 was found in 4096 tries")
            else:
                (binrepo / "utf8.zip").write_bytes(utf8_zip)
                add = subprocess.run(["git", "-C", str(binrepo), "add", "--", "utf8.zip"], capture_output=True,
                                     text=True, env=_git_fixture_env.git_fixture_env(), timeout=60)
                rc, report = gate(binrepo)
                hit = any(line.strip().startswith("utf8.zip:") and line.strip().endswith(": " + review_label)
                          for line in report.splitlines())
                if add.returncode != 0 or rc != 1 or not hit or "utf8.zip" + rule in report:
                    failures.append("an accepted archive whose bytes are valid UTF-8 expected to be scanned (exit 1, "
                                    "its label found), got rc={} {!r}".format(rc, report))
                remove = subprocess.run(["git", "-C", str(binrepo), "rm", "-q", "-f", "--", "utf8.zip"],
                                        capture_output=True, text=True, env=_git_fixture_env.git_fixture_env(),
                                        timeout=60)
                if remove.returncode != 0:
                    failures.append("the case 28 file utf8.zip could not be removed: {!r}".format(remove.stderr))
            # Both ends of the archive-size bound, on the tracked real archive (the leak gate's
            # MAX_ARCHIVE_FILE_SIZE, which the shared rule reads): one byte above the archive's exact size
            # and at it, the archive passes; one byte below, the read stops one byte past the bound, holding
            # the whole archive, and the archive cannot be evaluated, named with the size reason. Then, with
            # the bound at the size, the archive followed by a label past the bound cannot be evaluated
            # either. A bound comparison with the wrong direction or inclusiveness, a dropped size check, or
            # a read that stops at the bound fails one of these.
            size_reason = (rule + "a ZIP archive of more than MAX_ARCHIVE_FILE_SIZE ({}) bytes; the read stopped at "
                           "{} bytes")
            saved_bound = check_leaks.MAX_ARCHIVE_FILE_SIZE
            try:
                for bound, want_rc in ((len(archive_bytes) + 1, 0), (len(archive_bytes), 0),
                                       (len(archive_bytes) - 1, 2)):
                    check_leaks.MAX_ARCHIVE_FILE_SIZE = bound
                    rc, report = gate(binrepo)
                    over = "archive.zip" + size_reason.format(bound, bound + 1)
                    if rc != want_rc or (want_rc == 2) != (over in report):
                        failures.append("with the archive-size bound {} the archive ({} bytes) expected exit {}, "
                                        "got rc={} {!r}".format(bound, len(archive_bytes), want_rc, rc, report))
                (binrepo / "overrun.zip").write_bytes(archive_bytes + b"\xff " + label_bytes)
                add = subprocess.run(["git", "-C", str(binrepo), "add", "--", "overrun.zip"], capture_output=True,
                                     text=True, env=_git_fixture_env.git_fixture_env(), timeout=60)
                bound = len(archive_bytes)
                check_leaks.MAX_ARCHIVE_FILE_SIZE = bound
                rc, report = gate(binrepo)
                over = "overrun.zip" + size_reason.format(bound, bound + 1)
                if add.returncode != 0 or rc != 2 or over not in report or "archive.zip" in report:
                    failures.append("with the archive-size bound at the archive's size ({} bytes), the archive "
                                    "followed by a label past the bound expected exit 2 named with the size "
                                    "reason, got rc={} {!r}".format(bound, rc, report))
            finally:
                check_leaks.MAX_ARCHIVE_FILE_SIZE = saved_bound
            # A tracked .zip absent from the working tree is unevaluable as absent, like any tracked file,
            # classified before the archive read opens it (an archive read without that check is a read
            # error instead).
            (binrepo / "archive.zip").unlink()
            rc, report = gate(binrepo)
            if rc != 2 or "archive.zip: tracked file is absent from the working tree" not in report:
                failures.append("a tracked .zip absent from the working tree expected cannot-evaluate (exit 2) "
                                "reported as absent, got rc={} {!r}".format(rc, report))

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

        # The subprocess-spawning cases below give each child the sentinel naming this process, so a child
        # (which re-enters --self-test) skips them rather than recursing without bound. A run recognized as a
        # child (see _CHILD_SENTINEL) skips them and therefore exits 3, or 1 when a case also failed, never 0.
        if launched_child:
            not_run.append("cases 19 to 23 and 25 (the child sentinel matched the parent pid)")
        else:
            child_tag = {_CHILD_SENTINEL: str(os.getpid())}
            childenv = dict(os.environ, **child_tag)
            toolsdir = str(Path(__file__).resolve().parent)

            # 19. DISCRIMINATING (finding-1: a NON-REGULAR input FAILS CLOSED, never blocks on open): a FIFO
            #     at the hash-file path, at a scoped path and at a tracked .zip path is classified non-regular
            #     BEFORE any open, so load_hashes/scan_scope/scan_tracked_roles raise a fail-closed OSError
            #     PROMPTLY. Reverting the S_ISREG-before-open guard (a bare read_text, or the archive read
            #     without the _present check) blocks on open(FIFO) and the probe times out. Each probe imports
            #     this module in a child and calls the function on a FIFO under a short timeout; a hang is a
            #     recorded failure, never an unbounded wait. Needs os.mkfifo (POSIX); asserted where available.
            if hasattr(os, "mkfifo"):
                for label, target_rel, call in (
                        ("hash-file", "tools/internal-name-hashes.txt", "cin.load_hashes(root)"),
                        ("scoped-file", "tools/subject.md",
                         "cin.scan_scope(root, ('tools/subject.md',), set(), 3)"),
                        ("tracked-zip", "tools/skill.zip", "cin.scan_tracked_roles(root, ['tools/skill.zip'])")):
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
                        not_run.append("case 19 at the {} path (a FIFO could not be created)".format(label))
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
            else:
                not_run.append("case 19 (os.mkfifo is unavailable)")

            # 20. DISCRIMINATING (finding-6: a hostile sibling json.py/hashlib.py must NOT shadow a stdlib
            #     import and neuter this gate). Copy this script and its sibling modules (_walk, check_leaks)
            #     beside a hostile json.py and hashlib.py (each `raise SystemExit(0)`) and run `--self-test`
            #     isolated: the copy must actually RUN its self-test as a child and pass, so it prints the
            #     child line and exits 3 (any other exit, or a FAIL or other SELF-TEST text, fails this).
            #     Reverting the sys.path.append import fix to an index-0 insertion puts the script dir first,
            #     so under -I the sibling shadows check_leaks' `import hashlib` and silently exits 0 before
            #     the self-test runs.
            sib = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-sibling-"))
            try:
                for name in ("check_internal_names.py", "_walk.py", "check_leaks.py", "_git_fixture_env.py"):
                    shutil.copy(str(Path(toolsdir) / name), str(sib / name))
                for hostile in ("json.py", "hashlib.py"):
                    (sib / hostile).write_text("raise SystemExit(0)\n", encoding="utf-8")
                proc = subprocess.run([sys.executable, "-I", "-B", str(sib / "check_internal_names.py"),
                                       "--self-test"], capture_output=True, text=True, env=childenv)
                if proc.returncode != _INCOMPLETE_EXIT or "SELF-TEST CHILD PASS:" not in proc.stdout:
                    failures.append("a copy beside a hostile sibling json.py/hashlib.py did not run and pass "
                                    "its self-test as a child (a shadowed stdlib import exits before it "
                                    "runs): rc={} out={!r} err={!r}".format(
                                        proc.returncode, proc.stdout, proc.stderr))
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
            #     after the drop. Each child must pass, print the child line and exit 3 (it skips cases 19
            #     to 23 and 25, which this run runs). Reverting self_test_main to a bare _self_test() lets
            #     the child's tracked-file read load the malformed file, so its cases 14 to 17 exit 2 and the
            #     child fails all three; applying the scrub without the lifecycle fails the system case. A
            #     control first proves each poisoned configuration breaks a git read that honours it; the home
            #     and XDG controls pin the host's system configuration away (the shim supplies the system
            #     control's), so no control reads the host's system file. The caller environment is the
            #     snapshot self_test_main takes before the lifecycle (a direct _self_test() call runs outside
            #     the lifecycle and takes it from os.environ), reduced to _CALLER_VARS either way; each lane
            #     and control here sets HOME and XDG_CONFIG_HOME to this case's own directories, so none of
            #     them reads the caller's own configuration, and neither do the case 22 probe and case 23's
            #     lanes, which carry no HOME or XDG_CONFIG_HOME from the caller.
            import shlex
            caller = _caller_snapshot(os.environ if caller_env is None else caller_env)
            # When the caller's PATH is set, self_test_main's lifecycle has already searched it for git and
            # raised RuntimeError("self-test requires git") before any case ran if it found none, so the
            # real_git is None failures here and in case 23 are then reachable only where _self_test runs
            # without the lifecycle (a direct _self_test() call).
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
                                               env=dict(lane_env, **child_tag))
                    except subprocess.TimeoutExpired:
                        failures.append("a child self-test under a malformed caller {} git configuration "
                                        "timed out".format(label))
                        continue
                    if child.returncode != _INCOMPLETE_EXIT or "SELF-TEST CHILD PASS:" not in child.stdout:
                        failures.append("a malformed caller {} git configuration changed the self-test verdict "
                                        "(the gate's tracked-file read must not read it): rc={} out={!r} "
                                        "err={!r}".format(label, child.returncode, child.stdout, child.stderr))

                # 22. DISCRIMINATING (an in-process self_test_main() restores the caller's environment
                #     exactly): a child imports this module, sets a caller environment (HOME at a scratch
                #     directory, XDG_CONFIG_HOME and the GIT_CONFIG_* pins unset, a caller GIT_* variable
                #     set), calls self_test_main() and compares os.environ with its snapshot from before the
                #     call. The probe is a child of this run, so self_test_main() must print the child line
                #     and return 3. A scrub applied before the fixture git lifecycle saves the environment
                #     makes the lifecycle restore the scrubbed values (HOME at the fixture scratch, XDG_CONFIG_HOME and
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
                    "print('SELF_TEST_MAIN RETURNED: {{}}'.format(rc))\n"
                    "sys.exit(0 if rc == {incomplete} and not changed else 1)\n"
                ).format(tools=toolsdir, home=str(clean_home), incomplete=_INCOMPLETE_EXIT)
                try:
                    proc = subprocess.run([sys.executable, "-I", "-B", "-c", probe], capture_output=True, text=True,
                                          timeout=600, env=dict(caller, **child_tag))
                    if (proc.returncode != 0 or "ENVIRONMENT CHANGED: []" not in proc.stdout
                            or "SELF-TEST CHILD PASS:" not in proc.stdout):
                        failures.append("an in-process self_test_main() expected to pass and restore the caller's "
                                        "environment exactly, got rc={} out={!r} err={!r}".format(
                                            proc.returncode, proc.stdout, proc.stderr))
                except subprocess.TimeoutExpired:
                    failures.append("the in-process self_test_main() environment probe timed out")
            finally:
                shutil.rmtree(poison, ignore_errors=True)

            # 23. DISCRIMINATING (an inherited child sentinel does not skip cases 19 to 22, and a run that skips
            #     a case never exits 0): a run whose environment already holds the child sentinel as "1" or as
            #     a stale pid (this run's own parent; see _stale_sentinel_values for the values used where this
            #     run or its parent is PID 1) was not launched by this run, so it runs cases 19 to 22. Each lane
            #     runs a scratch copy of this script as a top-level --self-test with that inherited value. The
            #     copy carries three mutations for cases 19 to 22, so each of those cases records its own
            #     failure only if it runs: its case 19 probe reports the fail-closed OSError as exit 4 (a
            #     failure at both FIFO paths), its case 20 sibling copy omits _walk.py so that copy cannot run,
            #     and its self_test_main applies the fixture scrub without the lifecycle, the one mutation
            #     cases 21 and 22 share (it fails case 21's system lane and case 22). This case requires all
            #     five failure messages, so honouring the inherited value (as a child) fails it. It shows this
            #     for these two values only: a value equal to the launching run's pid is honoured by design, and
            #     a run misrecognized that way skips cases 19 to 23 and 25, so it exits 3, or 1 when a case also
            #     failed, never 0. The copy is launched with _INHERITED_PROBE naming this process, so it
            #     skips this case and does not recurse. Each copy therefore fails cases and skips one, and must
            #     exit 1 with its not-run line naming this case: a failure outranks a skip. A last lane runs
            #     this script unmutated as a top-level --self-test with only _INHERITED_PROBE naming this
            #     process, so it fails no case and skips this one, and must exit 3 with the SELF-TEST
            #     INCOMPLETE line naming this case; a top-level run that skipped a case and exited 0 fails it.
            #     A platform lane then runs a second scratch copy as a top-level --self-test with neither
            #     sentinel set, so neither sentinel route can account for its exit. That copy's case 11
            #     symlink creation raises OSError, so it skips case 11, and its case 23 is cut out (it launches
            #     nothing, so it cannot recurse, and adds no not-run line). It must exit 3 with the SELF-TEST
            #     INCOMPLETE line naming case 11, so a run that returns 0 unless it is a recognized child or an
            #     inherited-probe run fails this lane.
            #     The mutated copy's entry drops the lifecycle, so the tracked-file reads of the copy, of its
            #     case 21 home and XDG children and of its case 22 probe run without the system-config pin
            #     and would read the host's system file. Its two lanes therefore put a git shim first on PATH
            #     that points GIT_CONFIG_SYSTEM at os.devnull only where neither GIT_CONFIG_NOSYSTEM=1 nor
            #     GIT_CONFIG_SYSTEM is set: those reads stay isolated, while the copy's own case 21 system
            #     shim, which sets GIT_CONFIG_SYSTEM to its malformed file in front of this one, still
            #     reaches git and fails that lane as this case requires.
            lifecycle_entry = ("    from _git_fixture_env import fixture_git_lifecycle\n"
                               "    caller_env = _caller_snapshot(os.environ)\n"
                               "    with fixture_git_lifecycle():\n"
                               "        return _self_test(caller_env)\n")
            scrub_entry = ("    from _git_fixture_env import scrub_git_environment\n"
                           "    caller_env = _caller_snapshot(os.environ)\n"
                           "    scrub_git_environment()\n"
                           "    return _self_test(caller_env)\n")
            # Each (original, mutant) pair is source text that occurs exactly once in this file; the escaped
            # quotes and backslashes keep these literals from matching themselves.
            copy_mutations = (
                (lifecycle_entry, scrub_entry),
                ("\"    sys.exit(0)\\n\"", "\"    sys.exit(4)\\n\""),
                ("for name in (\"check_internal_names.py\", \"_walk.py\", ",
                 "for name in (\"check_internal_names.py\", "))
            ran_markers = ("a FIFO at the hash-file path expected a prompt fail-closed OSError",
                           "a FIFO at the scoped-file path expected a prompt fail-closed OSError",
                           "a copy beside a hostile sibling json.py/hashlib.py did not run and pass",
                           "malformed caller system git configuration changed",
                           "an in-process self_test_main() expected to pass")
            source = Path(selfpath).read_text(encoding="utf-8")
            mutant = source
            unbuildable = []
            for original, replacement in copy_mutations:
                if mutant.count(original) != 1:
                    unbuildable.append("{!r} occurs {} times".format(original, mutant.count(original)))
                mutant = mutant.replace(original, replacement)
            # The platform lane's copy: case 11 cannot create its symlink, and case 23 runs no lane.
            platform_mutations = (
                ("os.symlink(str(tmp / \"no-such-scope-target\"), str(broken))",
                 "raise OSError(\"symlink creation is disabled in this copy\")"),
                ("if _set_by_launcher(_INHERITED_PROBE):\n                not_run.append(inherited_skip)\n",
                 "if True:\n                pass\n"))
            platform_copy = source
            for original, replacement in platform_mutations:
                if platform_copy.count(original) != 1:
                    unbuildable.append("{!r} occurs {} times".format(original, platform_copy.count(original)))
                platform_copy = platform_copy.replace(original, replacement)
            platform_skip = "case 11 (a dangling symlink could not be created)"
            inherited_skip = "case 23 (the inherited-probe sentinel matched the parent pid)"
            if _set_by_launcher(_INHERITED_PROBE):
                not_run.append(inherited_skip)
            elif unbuildable:
                failures.append("the inherited-sentinel case could not build its mutated copies (each mutated source "
                                "text must occur once): {}".format("; ".join(unbuildable)))
            elif real_git is None:
                failures.append("no git on the caller's PATH; the inherited-sentinel case cannot pin its mutated "
                                "copies' system configuration")
            else:
                inherited_dir = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-inherited-"))
                try:
                    for name in ("_walk.py", "check_leaks.py", "_git_fixture_env.py"):
                        shutil.copy(str(Path(toolsdir) / name), str(inherited_dir / name))
                    (inherited_dir / "check_internal_names.py").write_text(mutant, encoding="utf-8")
                    pin_dir = inherited_dir / "system-config-pin"
                    pin_dir.mkdir()
                    (pin_dir / "git").write_text(
                        "#!/bin/sh\n"
                        'if [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && [ "${{GIT_CONFIG_SYSTEM+x}}" != x ]; then\n'
                        "  export GIT_CONFIG_SYSTEM={}\n"
                        "fi\n"
                        "exec {} \"$@\"\n".format(shlex.quote(os.devnull), shlex.quote(real_git)),
                        encoding="utf-8")
                    (pin_dir / "git").chmod(0o700)
                    pinned = dict(caller, PATH=str(pin_dir) + os.pathsep + caller.get("PATH", os.defpath))
                    for inherited in _stale_sentinel_values(os.getpid(), os.getppid()):
                        try:
                            proc = subprocess.run(
                                [sys.executable, "-I", "-B", str(inherited_dir / "check_internal_names.py"),
                                 "--self-test"], capture_output=True, text=True, timeout=1200,
                                env=dict(pinned, **{_CHILD_SENTINEL: inherited, _INHERITED_PROBE: str(os.getpid())}))
                        except subprocess.TimeoutExpired:
                            failures.append("a top-level self-test with the child sentinel inherited as {!r} timed "
                                            "out".format(inherited))
                            continue
                        missing = [m for m in ran_markers if m not in proc.stdout]
                        listed = any(line.startswith("  not run: ") and inherited_skip in line
                                     for line in proc.stdout.splitlines())
                        if proc.returncode != 1 or "SELF-TEST PASS" in proc.stdout or missing or not listed:
                            failures.append("a top-level self-test with the child sentinel inherited as {!r} must "
                                            "run cases 19 to 22 (its mutated copy fails each when it runs) and, "
                                            "having failed them and skipped case 23, exit 1 with a not-run line "
                                            "naming case 23, got rc={} missing={!r} not-run line found={} "
                                            "out={!r} err={!r}".format(inherited, proc.returncode, missing, listed,
                                                                       proc.stdout, proc.stderr))
                finally:
                    shutil.rmtree(inherited_dir, ignore_errors=True)
                clean_env = dict(caller, **{_INHERITED_PROBE: str(os.getpid())})
                clean_env.pop(_CHILD_SENTINEL, None)
                try:
                    proc = subprocess.run([sys.executable, "-I", "-B", selfpath, "--self-test"], capture_output=True,
                                          text=True, timeout=1200, env=clean_env)
                    incomplete = any(line.startswith("SELF-TEST INCOMPLETE:") and inherited_skip in line
                                     for line in proc.stdout.splitlines())
                    if proc.returncode != _INCOMPLETE_EXIT or not incomplete:
                        failures.append("a top-level self-test that fails no case and skips case 23 must exit 3 "
                                        "with a SELF-TEST INCOMPLETE line naming case 23, got rc={} out={!r} "
                                        "err={!r}".format(proc.returncode, proc.stdout, proc.stderr))
                except subprocess.TimeoutExpired:
                    failures.append("a top-level self-test that skips case 23 timed out")
                platform_dir = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-platform-"))
                try:
                    for name in ("_walk.py", "check_leaks.py", "_git_fixture_env.py"):
                        shutil.copy(str(Path(toolsdir) / name), str(platform_dir / name))
                    (platform_dir / "check_internal_names.py").write_text(platform_copy, encoding="utf-8")
                    platform_env = dict(caller)
                    platform_env.pop(_CHILD_SENTINEL, None)
                    platform_env.pop(_INHERITED_PROBE, None)
                    try:
                        proc = subprocess.run(
                            [sys.executable, "-I", "-B", str(platform_dir / "check_internal_names.py"),
                             "--self-test"], capture_output=True, text=True, timeout=1200, env=platform_env)
                        incomplete = any(line.startswith("SELF-TEST INCOMPLETE:") and platform_skip in line
                                         for line in proc.stdout.splitlines())
                        if proc.returncode != _INCOMPLETE_EXIT or not incomplete:
                            failures.append("a top-level self-test with neither sentinel set that fails no case and "
                                            "cannot create case 11's symlink must exit 3 with a SELF-TEST "
                                            "INCOMPLETE line naming case 11, got rc={} out={!r} err={!r}".format(
                                                proc.returncode, proc.stdout, proc.stderr))
                    except subprocess.TimeoutExpired:
                        failures.append("a top-level self-test that cannot create case 11's symlink timed out")
                finally:
                    shutil.rmtree(platform_dir, ignore_errors=True)

            # 25. DISCRIMINATING (the tree-wide decision-id layer is WIRED into the real scan): a copy of this
            #     gate (with _walk and check_leaks) in a fixture git repository, run with no arguments, exits 1
            #     naming a synthetic maintainer-decision id in docs/, a path outside SCOPE_RELPATHS, and exits 0
            #     once the id is gone. The copy and the note are tracked so the role rule's tracked-file read
            #     succeeds and the decision-id layer alone decides the result. Dropping the
            #     scan_tree_decision_ids call from run_gate lets the planted run exit 0, failing this. The id is
            #     assembled at runtime, as in case 26. The copy list starts with _walk.py so it does not repeat
            #     the case 20 list that case 23 mutates (each mutated text must occur once in this file).
            wired = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-wired-"))
            try:
                (wired / "tools").mkdir()
                (wired / "docs").mkdir()
                # An explicitly empty shipped inventory (a manifest with no sources array at all is
                # refused) keeps the tracker-label layer from failing closed here, so the planted
                # decision id alone decides each probe's result (as in the fixture above).
                (wired / ".aiqt").mkdir()
                (wired / ".aiqt" / "manifest.toml").write_text("format-version = 1\nsources = []\n",
                                                               encoding="utf-8")
                for name in ("_walk.py", "check_internal_names.py", "check_leaks.py"):
                    shutil.copy(str(Path(toolsdir) / name), str(wired / "tools" / name))
                note = wired / "docs" / "note.md"
                note.write_text("Decided under a plain rule here.\n", encoding="utf-8")
                init = subprocess.run(["git", "-C", str(wired), "init", "-q"], capture_output=True, text=True,
                                      env=_git_fixture_env.git_fixture_env(), timeout=60)
                add = subprocess.run(["git", "-C", str(wired), "add", "--", "tools", "docs/note.md"],
                                     capture_output=True, text=True, env=_git_fixture_env.git_fixture_env(),
                                     timeout=60)
                if init.returncode != 0 or add.returncode != 0:
                    failures.append("the wired decision-id fixture repository could not be built: init rc={} "
                                    "add rc={} {!r}".format(init.returncode, add.returncode,
                                                            init.stderr + add.stderr))
                for planted, want in ((True, 1), (False, 0)):
                    note.write_text("Decided under {} here.\n".format(
                        "D-" + "000-SYNTHETIC-FIXTURE" if planted else "a plain rule"), encoding="utf-8")
                    try:
                        proc = subprocess.run(
                            [sys.executable, "-I", "-B", str(wired / "tools" / "check_internal_names.py")],
                            capture_output=True, text=True, env=childenv, timeout=120)
                    except subprocess.TimeoutExpired:
                        failures.append("the wired tree-wide scan (planted={}) timed out".format(planted))
                        continue
                    named = "docs/note.md:1: internal maintainer-decision id" in proc.stdout
                    if proc.returncode != want or named != planted:
                        failures.append("the real scan over a tree {} a decision id expected exit {} (finding "
                                        "named: {}), got rc={} out={!r} err={!r}".format(
                                            "holding" if planted else "without", want, planted,
                                            proc.returncode, proc.stdout, proc.stderr))

                # 25 (continued). DISCRIMINATING (the tree-wide layer FAILS CLOSED in the real scan): each input
                #     below is planted alone, untracked and outside SCOPE_RELPATHS, in the otherwise clean tree
                #     above. An unreadable file, an unlistable directory and a FIFO each exit 2 with the
                #     read-error diagnostic naming the input; a file that vanishes between the walk listing it
                #     and the read exits 1 with the fail-closed vanished finding naming it, as in the scoped
                #     layer. Skipping an OSError in scan_tree_decision_ids, skipping a vanished file, an rglob
                #     walk (which lists nothing under an unlistable directory and bypasses the walker the vanish
                #     probe wraps), and a run_gate that swallows the tree layer's OSError each fail at least one
                #     probe. A plant this platform cannot produce (an unreadable entry when permissions do not
                #     bind, as for root, or a FIFO without os.mkfifo) is a case not run, so the run exits 3 (or 1
                #     when a case also failed), never 0.
                gate = str(wired / "tools" / "check_internal_names.py")
                readerr = "error: cannot read a required input"

                def expect_fail_closed(label, script_args, want, stream, needles):
                    try:
                        proc = subprocess.run([sys.executable, "-I", "-B", *script_args], capture_output=True,
                                              text=True, env=childenv, timeout=120)
                    except subprocess.TimeoutExpired:
                        failures.append("the tree-wide scan over {} timed out, not failed closed".format(label))
                        return
                    text = proc.stdout if stream == "stdout" else proc.stderr
                    if proc.returncode != want or not all(n in text for n in needles):
                        failures.append("the tree-wide scan over {} expected exit {} with {!r} on {}, got rc={} "
                                        "out={!r} err={!r}".format(label, want, needles, stream, proc.returncode,
                                                                   proc.stdout, proc.stderr))

                unreadable = wired / "docs" / "unreadable.md"
                unreadable.write_text("plain text\n", encoding="utf-8")
                os.chmod(str(unreadable), 0)
                try:
                    try:
                        unreadable.read_bytes()
                        not_run.append("case 25 (an unreadable file could not be planted: permissions do not "
                                       "bind here)")
                    except PermissionError:
                        expect_fail_closed("an unreadable file", (gate,), 2, "stderr",
                                           (readerr, "docs/unreadable.md"))
                finally:
                    os.chmod(str(unreadable), 0o600)
                    unreadable.unlink()

                locked = wired / "notes" / "locked"
                locked.mkdir(parents=True)
                (locked / "inner.md").write_text("plain text\n", encoding="utf-8")
                os.chmod(str(locked), 0)
                try:
                    try:
                        os.listdir(str(locked))
                        not_run.append("case 25 (an unlistable directory could not be planted: permissions do "
                                       "not bind here)")
                    except PermissionError:
                        expect_fail_closed("an unlistable directory", (gate,), 2, "stderr",
                                           (readerr, "notes/locked"))
                finally:
                    os.chmod(str(locked), 0o700)
                    shutil.rmtree(wired / "notes")

                pipe = wired / "docs" / "pipe.md"
                made = False
                if hasattr(os, "mkfifo"):
                    try:
                        os.mkfifo(str(pipe))
                        made = True
                    except OSError:
                        made = False
                if made:
                    try:
                        expect_fail_closed("a FIFO", (gate,), 2, "stderr",
                                           (readerr, "docs/pipe.md is present but not a regular file"))
                    finally:
                        pipe.unlink()
                else:
                    not_run.append("case 25 (a FIFO could not be planted: os.mkfifo unavailable or refused)")

                (wired / "docs" / "vanish.md").write_text("plain text\n", encoding="utf-8")
                vanish_probe = (
                    "import sys\n"
                    "sys.path.append({tools!r})\n"
                    "import check_internal_names as cin\n"
                    "listed = cin.walk_files\n"
                    "def vanishing_walk(*args, **kwargs):\n"
                    "    for p in listed(*args, **kwargs):\n"
                    "        if p.name == 'vanish.md':\n"
                    "            p.unlink()\n"
                    "        yield p\n"
                    "cin.walk_files = vanishing_walk\n"
                    "sys.exit(cin.main())\n"
                ).format(tools=str(wired / "tools"))
                expect_fail_closed("a file that vanishes during the walk", ("-c", vanish_probe), 1, "stdout",
                                   ("docs/vanish.md: file vanished during the tree scan; fail-closed",))
            finally:
                for entry in (wired / "docs" / "unreadable.md", wired / "notes" / "locked"):
                    try:
                        os.chmod(str(entry), 0o700)
                    except OSError:
                        pass
                shutil.rmtree(wired, ignore_errors=True)

        # 24. DISCRIMINATING (the inherited child-sentinel values case 23 runs, by direct calls, so no real
        #     PID 1 run is needed): "1" and the parent pid normally, and the replacements
        #     _stale_sentinel_values documents for a run that is PID 1 (parent pid 0, and parent pid 2, which
        #     equals own pid + 1) and for a run whose parent is PID 1. Each result is two distinct values and
        #     neither is the run's own pid, the one value a launched copy honours. Dropping the own-pid test
        #     returns "1" for a run that is PID 1, and dropping the repeat test returns "1" twice for a run
        #     whose parent is PID 1; either fails this case.
        for own, parent, expected in ((7, 5, ["1", "5"]), (1, 0, ["0", "2"]), (1, 2, ["2", "3"]),
                                      (7, 1, ["1", "8"])):
            got = _stale_sentinel_values(own, parent)
            if got != expected:
                failures.append("the inherited child-sentinel values for own pid {} and parent pid {} expected "
                                "{!r}, got {!r}".format(own, parent, expected, got))

        # 26. DISCRIMINATING (maintainer-decision id SHAPES, tree-wide): one synthetic id of each decision
        #     shape, planted in docs/ (outside SCOPE_RELPATHS), is caught by scan_tree_decision_ids, and so is
        #     the first shape planted outside docs/ (a suffix-less file at the tree root and a page in site/);
        #     removing DECISION_ID, or narrowing a head or segment class so a shape no longer matches, fails
        #     its case.
        #     Each non-id token class seen in the tree is a negative control that must stay unflagged, and an
        #     internal-allow line is exempt as for the other shapes. The ids are SYNTHETIC (the 000 ordinal
        #     and the SYNTHETIC word name no real decision) and assembled at runtime, so no contiguous
        #     decision-id shape sits in this source.
        tree = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-tree-"))
        try:
            (tree / "docs").mkdir()
            note = tree / "docs" / "note.md"
            positives = (
                ("an ordinal head with word segments", "D-" + "000-SYNTHETIC-FIXTURE"),
                ("an ordinal head with a segment carrying a digit", "D-" + "000-R0-FIXTURE"),
                ("a word head with word segments", "D-" + "SYNTHETIC-FIXTURE-ONLY"),
                ("a word head with a final one-letter segment", "D-" + "SYNTHETIC-Z"),
            )
            negatives = (
                ("a bare ordinal", "closes D-3; a duplicate item D-1"),
                ("a lower-case check id", 'ok("D-roster-excludes-init") and ("D-no-remote", argv)'),
                ("a lower-case short label", "the D-a2 rename and the D-a4 header"),
                ("a lower-case prose word", "a chain of depth D emits a D-part header"),
                ("a D- glued to a longer word", "GD-" + "000-SYNTHETIC-FIXTURE"),
            )
            for shape, token in positives:
                note.write_text("Decided under {} in prose.\n".format(token), encoding="utf-8")
                f = scan_tree_decision_ids(tree)
                if f != ["docs/note.md:1: internal maintainer-decision id"]:
                    failures.append("a decision id ({}) expected one tree-wide finding, got {}".format(shape, f))
                note.write_text("{} kept on purpose  # internal-allow\n".format(token), encoding="utf-8")
                f = scan_tree_decision_ids(tree)
                if f:
                    failures.append("an internal-allow line holding a decision id ({}) expected exemption, "
                                    "got {}".format(shape, f))
            for shape, text in negatives:
                note.write_text(text + "\n", encoding="utf-8")
                f = scan_tree_decision_ids(tree)
                if f:
                    failures.append("a non-id token ({}) expected no decision-id finding, got {}".format(shape, f))
            # The same id outside docs/: a suffix-less file at the tree root (read, as every file with no
            # suffix is) and a listed text suffix in a nested directory, each named by its own path.
            note.unlink()
            for rel in ("NOTICE", "site/pages/index.html"):
                elsewhere = tree / rel
                elsewhere.parent.mkdir(parents=True, exist_ok=True)
                elsewhere.write_text("Decided under {} here.\n".format(positives[0][1]), encoding="utf-8")
                f = scan_tree_decision_ids(tree)
                if f != ["{}:1: internal maintainer-decision id".format(rel)]:
                    failures.append("a decision id in {} (outside docs/) expected one tree-wide finding, got "
                                    "{}".format(rel, f))
                elsewhere.unlink()
        finally:
            shutil.rmtree(tree, ignore_errors=True)

        # 27. DISCRIMINATING (tracker-label SHAPES): each label shape is counted under its own key and
        #     each negative control stays unflagged. Every vector is assembled at run time, so no
        #     contiguous label text sits in this source (which the real layer scans as a shipped file).
        fixpos = ("FIX" + "-515", "FIX" + "-515-R2", "FIX" + " 1", "FIX" + " A1", "FIX" + " B")
        roundpos = ("rou" + "nd-7", "Rou" + "nd 25", "ROU" + "ND-2", "rou" + "nd7-email",
                    "rou" + "nds 4 to 6", "rou" + "nd: 12", "M-rou" + "nd3", "rou" + "nd - 3")
        qapos = ("QA" + "26", "QA" + "-1")
        revpos = ("codex" + " MINOR", "claude" + " MAJOR 1", "(gemini" + " 4", "CLAUDE" + "-F2",
                  "codex" + " PR #520 rou" + "nd 2 MAJOR", "codex" + " rou" + "nd-8",
                  "fable" + " finding")
        for check_id, shape_key, vectors in (
                ("tracker/shape-fix-id-caught", "fix-id", fixpos),
                ("tracker/shape-review-round-caught", "review-round", roundpos),
                ("tracker/shape-qa-round-caught", "qa-round", qapos),
                ("tracker/shape-reviewer-source-caught", "reviewer-source", revpos)):
            missed = [v for v in vectors if len(_tracker_hits("a {} b\n".format(v))[shape_key]) != 1]
            check(check_id, not missed, missed)
        negatives = ("fix the artefact", "--fix", "fix-only=true", "Fix A & B",
                     "reproduce-before-fix", "FIX" + "-MAIN-UNCOND", "Around 5", "rou" + "nd-trip",
                     "rou" + "nd -  3", "R1", "Q1 to Q3", "CW-3 Q1", "Claude Code",
                     "fix claude adapter", "the reviewer family is identical to the author family",
                     "Gemini CLI", "codex" + " high-water")
        wrongly_flagged = [v for v in negatives
                           if any(_tracker_hits("a {} b\n".format(v))[key] for key in TRACKER_SHAPE_KEYS)]
        check("tracker/negative-controls-unflagged", not wrongly_flagged, wrongly_flagged)
        # One line carrying TWO matches of one shape counts BOTH (counts are match counts, never
        # deduplicated per line), and the ratchet finding reports that line once: a count that
        # collapsed per-line duplicates would let a second label ride an already-counted line.
        twice = "x {} y {} z\n".format("QA" + "26", "QA" + "31")
        twice_hits = _tracker_hits(twice)
        twice_report = _tracker_compare({"docs/twice.md": twice_hits}, {})
        check("tracker/same-line-multiplicity-counted",
              twice_hits["qa-round"] == [1, 1] and len(twice_report) == 1
              and "qa-round count 2 exceeds its ratchet baseline 0" in twice_report[0]
              and twice_report[0].endswith("lines 1)"), (twice_hits, twice_report))

        # 28. DISCRIMINATING (path-only allowlists, scope membership, no line marker, and the closed
        #     baseline schema), in process over a fixture root; the tracked argument stands in for the
        #     git read the real gate already makes, so these cases need no fixture repository.
        ratch = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-tracker-"))
        try:
            (ratch / ".aiqt").mkdir()
            (ratch / "docs").mkdir()
            qa_label = "QA" + "26"
            man_two = ('format-version = 1\n\n[[sources]]\npath = "docs/a.md"\n'
                       '\n[[sources]]\npath = "docs/b.md"\n')
            (ratch / ".aiqt" / "manifest.toml").write_text(man_two, encoding="utf-8")
            (ratch / "docs" / "a.md").write_text("x {} y\n".format(qa_label), encoding="utf-8")
            (ratch / "docs" / "b.md").write_text("x {} y\n".format(qa_label), encoding="utf-8")
            digest = hashlib.sha256((ratch / "docs" / "a.md").read_bytes()).hexdigest()
            frozen_ok = scan_shipped_tracker_labels(ratch, [], frozen=(("docs/a.md", digest),),
                                                    history=("docs/b.md",))
            (ratch / "docs" / "a.md").write_text("x {} y z\n".format(qa_label), encoding="utf-8")
            frozen_stale = scan_shipped_tracker_labels(ratch, [], frozen=(("docs/a.md", digest),),
                                                       history=("docs/b.md",))
            check("tracker/frozen-allowlist-sha-bound",
                  not frozen_ok and any("docs/a.md" in f for f in frozen_stale),
                  (frozen_ok, frozen_stale))
            both = scan_shipped_tracker_labels(ratch, [], history=("docs/b.md",))
            check("tracker/history-allowlist-path-bound",
                  any("docs/a.md" in f for f in both) and not any("docs/b.md" in f for f in both), both)
            (ratch / "docs" / "c.md").write_text("x {} y\n".format(qa_label), encoding="utf-8")
            exempt_pair = ("docs/a.md", "docs/b.md")
            outside = scan_shipped_tracker_labels(ratch, ["docs/c.md"], history=exempt_pair)
            (ratch / ".github").mkdir()
            (ratch / ".github" / "note.md").write_text("x {} y\n".format(qa_label), encoding="utf-8")
            (ratch / "opf").mkdir()
            (ratch / "opf" / "util.py").write_text("x {} y\n".format(qa_label), encoding="utf-8")
            tracked_in = scan_shipped_tracker_labels(ratch, ["docs/c.md", ".github/note.md",
                                                             "opf/util.py"], history=exempt_pair)
            (ratch / ".aiqt" / "manifest.toml").write_text(
                man_two + '\n[[sources]]\npath = "docs/c.md"\n', encoding="utf-8")
            listed = scan_shipped_tracker_labels(ratch, [], history=exempt_pair)
            # Both supplemental tracked branches decide membership: a tracked file under .github/ AND
            # one under opf/ join the shipped set while a tracked file outside both stays out, so
            # dropping either branch turns this red.
            check("tracker/scope-membership-decides",
                  not outside and not any("docs/c.md" in f for f in tracked_in)
                  and any(".github/note.md" in f for f in tracked_in)
                  and any("opf/util.py" in f for f in tracked_in)
                  and any("docs/c.md" in f for f in listed), (outside, tracked_in, listed))
            (ratch / "docs" / "c.md").write_text("{} kept on purpose  # internal-allow\n".format(qa_label),
                                                 encoding="utf-8")
            marked = scan_shipped_tracker_labels(ratch, [], history=exempt_pair)
            check("tracker/internal-allow-not-honoured", any("docs/c.md" in f for f in marked), marked)
            (ratch / ".aiqt" / "manifest.toml").write_text(man_two, encoding="utf-8")
            base_path = ratch / ".aiqt" / "tracker-label-baseline.toml"
            # The baseline fixture texts are assembled at run time (a shape key next to its count would
            # otherwise be a review-round hit in this very source, which the real layer scans).
            qkey = "qa-" + "round"
            entry_a = 'format-version = 1\n\n[[path]]\npath = "docs/a.md"\n'

            def refused(text):
                base_path.write_text(text, encoding="utf-8")
                try:
                    scan_shipped_tracker_labels(ratch, [], history=("docs/b.md",))
                except OSError as exc:
                    return str(exc)
                return None

            accepted = [bad for bad in (
                'format-version = 1\n\n[[path]]\npath = 3\n',
                'format-version = 2\n',
                # The version's TYPE is pinned too: a boolean and a float each compare EQUAL to the
                # integer 1 in Python, so either would otherwise slip the equality test.
                'format-version = true\n',
                'format-version = 1.0\n',
                'zz = 3\nformat-version = 1\n',
                entry_a + "{} = 0\n".format(qkey),
                entry_a + "{} = -2\n".format(qkey),
                entry_a + "{} = true\n".format(qkey),
                entry_a,
                'format-version = 1\nnot closed [\n',
            ) if refused(bad) is None]
            check("tracker/baseline-malformed-refused", not accepted, accepted)
            dup = refused(entry_a + "{} = 1\n".format(qkey)
                          + '\n[[path]]\npath = "docs/a.md"\n' + "{} = 1\n".format(qkey))
            check("tracker/baseline-duplicate-refused", dup is not None and "duplicate" in dup, dup)
            unknown_key = refused(entry_a + "{}s = 1\n".format(qkey))
            check("tracker/baseline-unknown-key-refused",
                  unknown_key is not None and "unknown key" in unknown_key, unknown_key)
            nonshipped = refused('format-version = 1\n\n[[path]]\npath = "docs/zz.md"\n'
                                 + "{} = 1\n".format(qkey))
            check("tracker/baseline-nonshipped-path-refused",
                  nonshipped is not None and "not a shipped path" in nonshipped, nonshipped)
            # Each schema guard names its OWN refusal: most of these texts would also fail the
            # byte-exact canonical-render check below, so a refusal alone cannot show the guard is
            # live; a copy with the version-type pin, the string-path check or a count-schema branch
            # disabled would fall through to the canonical check's different message and go red here.
            misnamed = [(bad, want) for bad, want in (
                ('format-version = 2\n', "must be the integer 1"),
                ('format-version = true\n', "must be the integer 1"),
                ('format-version = 1.0\n', "must be the integer 1"),
                ('format-version = 1\n\n[[path]]\npath = 3\n', "carries no string path"),
                (entry_a + "{} = 0\n".format(qkey), "integer count of one or more"),
                (entry_a + "{} = -2\n".format(qkey), "integer count of one or more"),
                (entry_a + "{} = true\n".format(qkey), "integer count of one or more"),
                (entry_a, "carries no shape count"),
            ) if want not in (refused(bad) or "")]
            check("tracker/baseline-refusal-names-its-guard", not misnamed, misnamed)
            # The file must be the BYTE-EXACT canonical render of its own entries (the --write-baseline
            # output): a TOML comment is dropped by the parser, so the closed schema alone never sees
            # it, an alternate integer spelling or a missing header is equally parser-invisible, and a
            # REORDERING parses to the same entries while the bytes differ; each must be refused while
            # the exact canonical text stays accepted, for one entry and for two in sorted order.
            header_render = render_tracker_baseline({})
            entry_render = render_tracker_baseline({"docs/a.md": {qkey: [1]}})
            block_a = '\n[[path]]\npath = "docs/a.md"\n' + "{} = 1\n".format(qkey)
            block_b = '\n[[path]]\npath = "docs/b.md"\n' + "{} = 2\n".format(qkey)
            pair_render = header_render + block_a + block_b
            comment_line = "# " + "QA" + "26 riding in a comment\n"
            noncanonical = [bad for bad in (
                header_render + "\n" + comment_line,
                entry_render.replace("{} = 1".format(qkey), "{} = 0x1".format(qkey)),
                entry_render.replace("{} = 1".format(qkey), "{} = +1".format(qkey)),
                'format-version = 1\n',
                entry_render + "\n",
                header_render + block_b + block_a,
            ) if refused(bad) is None]
            canonical_ok = refused(entry_render)
            pair_ok = refused(pair_render)
            check("tracker/baseline-noncanonical-refused",
                  not noncanonical and canonical_ok is None and pair_ok is None
                  and pair_render == render_tracker_baseline(
                      {"docs/a.md": {qkey: [1]}, "docs/b.md": {qkey: [3, 5]}}),
                  (noncanonical, canonical_ok, pair_ok))
            # A manifest whose [[sources]] array is absent entirely (a misspelled table name, say) is
            # refused, never read as an empty shipped inventory that would scan nothing at exit 0.
            base_path.unlink()
            (ratch / ".aiqt" / "manifest.toml").write_text(
                'format-version = 1\n\n[[source]]\npath = "docs/a.md"\n', encoding="utf-8")
            try:
                scan_shipped_tracker_labels(ratch, [], history=exempt_pair)
                no_sources = None
            except OSError as exc:
                no_sources = str(exc)
            check("tracker/manifest-missing-sources-refused",
                  no_sources is not None and "sources" in no_sources, no_sources)
            # An unparseable manifest and a malformed [[sources]] entry are equally refused: neither
            # may silently shrink the shipped inventory.
            manifest_faults = []
            for bad_manifest in ('format-version = 1\nnot closed [\n',
                                 'format-version = 1\n\n[[sources]]\npath = 3\n'):
                (ratch / ".aiqt" / "manifest.toml").write_text(bad_manifest, encoding="utf-8")
                try:
                    scan_shipped_tracker_labels(ratch, [], history=exempt_pair)
                    manifest_faults.append(bad_manifest)
                except OSError:
                    pass
            check("tracker/manifest-malformed-refused", not manifest_faults, manifest_faults)
        finally:
            shutil.rmtree(ratch, ignore_errors=True)

        # 29. DISCRIMINATING (each pattern row, the case flag, the separator bound and the severity
        #     guard are load-bearing): with one row replaced by a never-matching pattern, that row's
        #     positive vector goes unflagged, so its case 27 assertion would go red; without the case
        #     flag the upper-case review-round vector is dropped; with the separator bound widened by
        #     one the four-separator negative control is flagged; and without the hyphen in the
        #     lookahead after the severity word the hyphenated-compound negative control is flagged.
        never = re.compile(r"(?!x)x")

        def swap_row(row, pattern):
            return tuple((pattern, label) if index == row else (pat, label)
                         for index, (pat, label) in enumerate(TRACKER_LABELS))

        for row, check_id, vector, shape_key in (
                (0, "tracker/mutation-pattern-fix-id", "FIX" + "-515", "fix-id"),
                (1, "tracker/mutation-pattern-review-round", "rou" + "nd-7", "review-round"),
                (2, "tracker/mutation-pattern-qa-round", "QA" + "26", "qa-round"),
                (3, "tracker/mutation-pattern-reviewer-source", "codex" + " MINOR", "reviewer-source")):
            intact = _tracker_hits("a {} b\n".format(vector))[shape_key]
            dropped = _tracker_hits("a {} b\n".format(vector), swap_row(row, never))[shape_key]
            check(check_id, len(intact) == 1 and not dropped, (vector, intact, dropped))
        flat = re.compile(r"\brounds?\W{0,3}\d")
        wide = re.compile(r"\brounds?\W{0,4}\d", re.IGNORECASE)
        unguarded = re.compile(r"\b" + _FAM + r"(?:'s)?(?: PR #\d+)?(?: (?:QA )?(?:round|r)[- ]?\d+)?,? "
                               + _SEV + r"(?!\w)")
        upper_vec = "ROU" + "ND-2"
        check("tracker/mutation-ignorecase-load-bearing",
              len(_tracker_hits(upper_vec + "\n")["review-round"]) == 1
              and not _tracker_hits(upper_vec + "\n", swap_row(1, flat))["review-round"], upper_vec)
        four_sep = "rou" + "nd -  3"
        check("tracker/mutation-separator-bound-load-bearing",
              not _tracker_hits(four_sep + "\n")["review-round"]
              and bool(_tracker_hits(four_sep + "\n", swap_row(1, wide))["review-round"]), four_sep)
        compound = "codex" + " high-water"
        check("tracker/mutation-severity-guard-load-bearing",
              not _tracker_hits(compound + "\n")["reviewer-source"]
              and bool(_tracker_hits(compound + "\n", swap_row(3, unguarded))["reviewer-source"]),
              compound)

        # 30. DISCRIMINATING (the ratchet comparison itself is load-bearing): a copy of this gate with
        #     the above-baseline branch disabled no longer reports a count above its baseline, and one
        #     with the below-baseline branch disabled no longer asks for the baseline to be lowered, so
        #     removing either comparison turns its ratchet case red. The copies are scratch modules
        #     loaded through _load_ratchet_mutant (never a dynamic code string), each mutated source
        #     text required to occur exactly once; the anchors below are concatenated so they do not
        #     match themselves.
        selfdir = Path(__file__).resolve().parent
        mdir = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-ratchet-mutant-"))
        try:
            gate_source = Path(__file__).resolve().read_text(encoding="utf-8")
            rkey = "review-" + "round"  # assembled so the key next to a count is no hit in this source
            sample_counts = {"docs/a.md": {rkey: [4, 9]}}
            tight = {"docs/a.md": {rkey: 1}}
            loose = {"docs/a.md": {rkey: 3}}
            intact_over = _tracker_compare(sample_counts, tight)
            intact_under = _tracker_compare(sample_counts, loose)
            ok_intact = (len(intact_over) == 1 and "exceeds" in intact_over[0]
                         and "lines 4, 9" in intact_over[0]
                         and len(intact_under) == 1 and "lower the baseline" in intact_under[0])
            mutant_faults = []
            for tag, anchor, replacement, probe_base, expect_gone in (
                    ("over", "if have > " + "allowed:", "if False:", tight, "exceeds"),
                    ("under", "elif have < " + "allowed:", "elif False:", loose, "lower the baseline")):
                if gate_source.count(anchor) != 1:
                    mutant_faults.append("{} anchor occurs {} times".format(tag, gate_source.count(anchor)))
                    continue
                module = _load_ratchet_mutant(gate_source.replace(anchor, replacement), mdir, tag)
                if any(expect_gone in f for f in module._tracker_compare(sample_counts, probe_base)):
                    mutant_faults.append("{} mutant still reports its finding".format(tag))
            check("tracker/mutation-ratchet-compare-load-bearing", ok_intact and not mutant_faults,
                  (intact_over, intact_under, mutant_faults))
        finally:
            shutil.rmtree(mdir, ignore_errors=True)

        # 31. DISCRIMINATING (the layer is WIRED into the real gate, the ratchet held at, one above and
        #     one below its baseline, --write-baseline round-tripping byte-exact, and every fail-closed
        #     case loud): a copy of this gate runs with no arguments in a fixture git repository whose
        #     shipped manifest lists docs/page.md. These runs never re-enter the self-test, so they run
        #     in a child too.
        wiredlab = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-tracker-wired-"))
        try:
            (wiredlab / "tools").mkdir()
            (wiredlab / "docs").mkdir()
            (wiredlab / ".aiqt").mkdir()
            for name in ("check_leaks.py", "_walk.py", "check_internal_names.py"):
                shutil.copy(str(selfdir / name), str(wiredlab / "tools" / name))
            page = wiredlab / "docs" / "page.md"
            page.write_text("A plain page.\n", encoding="utf-8")
            man = wiredlab / ".aiqt" / "manifest.toml"
            man_one = 'format-version = 1\n\n[[sources]]\npath = "docs/page.md"\n'
            man.write_text(man_one, encoding="utf-8")
            basefile = wiredlab / ".aiqt" / "tracker-label-baseline.toml"
            fixenv = _git_fixture_env.git_fixture_env()
            init = subprocess.run(["git", "-C", str(wiredlab), "init", "-q"], capture_output=True,
                                  text=True, env=fixenv, timeout=60)
            add = subprocess.run(["git", "-C", str(wiredlab), "add", "--", "tools", "docs/page.md"],
                                 capture_output=True, text=True, env=fixenv, timeout=60)
            if init.returncode != 0 or add.returncode != 0:
                failures.append("the tracker-label wired fixture could not be built: init rc={} add "
                                "rc={} {!r}".format(init.returncode, add.returncode,
                                                    init.stderr + add.stderr))
            gatecopy = str(wiredlab / "tools" / "check_internal_names.py")

            def tracker_gate(*args):
                try:
                    proc = subprocess.run([sys.executable, "-I", "-B", gatecopy, *args],
                                          capture_output=True, text=True, env=dict(os.environ),
                                          timeout=120)
                except subprocess.TimeoutExpired:
                    return None, "", "timed out"
                return proc.returncode, proc.stdout, proc.stderr

            rc, out, err = tracker_gate()
            check("tracker/wired-clean-tree-exit-0", rc == 0, (rc, out, err))
            rlabel = "rou" + "nd-7"
            page.write_text("x {} y\n".format(rlabel), encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-new-path-label-exit-1",
                  rc == 1 and "docs/page.md: review-round count 1 exceeds its ratchet baseline 0" in out
                  and "lines 1" in out, (rc, out, err))
            rc, out, err = tracker_gate("--write-baseline")
            rendered = render_tracker_baseline({"docs/page.md": _tracker_hits("x {} y\n".format(rlabel))})
            check("tracker/wired-write-baseline-canonical",
                  rc == 0 and out == rendered and err == "" and '"docs/page.md"' in out
                  and "review-rou" + "nd = 1" in out, (rc, out, err))
            basefile.write_text(out, encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-ratchet-equal-passes", rc == 0, (rc, out, err))
            # The one by-name skip, exercised in the wired gate: the baseline file itself may be
            # LISTED as a shipped source (the pack ships it), and its own shape-key-next-to-count
            # lines are skipped by name rather than counted, so the gate still passes; a copy
            # without that skip would read them as fresh hits for the baseline's own path and fail.
            man.write_text(man_one + '\n[[sources]]\npath = ".aiqt/tracker-label-baseline.toml"\n',
                           encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-baseline-listed-skipped-by-name",
                  rc == 0 and "tracker-label-baseline.toml" not in out, (rc, out, err))
            man.write_text(man_one, encoding="utf-8")
            page.write_text("x {} y\nmore {} text\n".format(rlabel, rlabel), encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-ratchet-exceeded-names-lines",
                  rc == 1 and "docs/page.md: review-round count 2 exceeds its ratchet baseline 1" in out
                  and "lines 1, 2" in out, (rc, out, err))
            # This case CHARACTERIZES EXISTING behaviour at a wired baseline above one; it guards no
            # logic that tells growth sources apart, because none exists: a regenerated baseline is
            # accepted at whatever grown counts a real scan yields, however the growth arose (here
            # the test itself grew the page; a refresh merging upstream files that already carry
            # labels raises counts the same way), while one more label above the regenerated
            # baseline still fails. Holding a raise to refresh merges is review policy over the
            # regenerated file's diff against the merge parent, never a comparison the gate makes.
            rc, out, err = tracker_gate("--write-baseline")
            regen_render = render_tracker_baseline(
                {"docs/page.md": _tracker_hits("x {0} y\nmore {0} text\n".format(rlabel))})
            regen_ok = rc == 0 and out == regen_render
            basefile.write_text(regen_render, encoding="utf-8")
            rc2, out2, err2 = tracker_gate()
            page.write_text("x {0} y\nmore {0} text\nyet {0} again\n".format(rlabel),
                            encoding="utf-8")
            rc3, out3, err3 = tracker_gate()
            check("tracker/wired-refresh-regeneration-absorbs-growth",
                  regen_ok and rc2 == 0 and rc3 == 1
                  and "docs/page.md: review-round count 3 exceeds its ratchet baseline 2" in out3
                  and "lines 1, 2, 3" in out3,
                  (rc, out, err, rc2, out2, err2, rc3, out3, err3))
            basefile.write_text(rendered, encoding="utf-8")
            page.write_text("A plain page.\n", encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-ratchet-below-asks-lower",
                  rc == 1 and "docs/page.md: review-round count 0 is below its ratchet baseline 1" in out
                  and "lower the baseline entry to 0" in out, (rc, out, err))
            basefile.write_text('format-version = 1\n\n[[path]]\npath = "docs/ghost.md"\n'
                                + "review-rou" + "nd = 1\n", encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-baseline-nonshipped-exit-2",
                  rc == 2 and "not a shipped path" in err, (rc, out, err))
            basefile.unlink()
            man.write_text(man_one + '\n[[sources]]\npath = "docs/gone.md"\n', encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-absent-source-exit-2",
                  rc == 2 and "docs/gone.md is absent" in err, (rc, out, err))
            (wiredlab / "docs" / "bad.py").write_bytes(b"\xff\xfe tracker bytes \xff\n")
            man.write_text(man_one + '\n[[sources]]\npath = "docs/bad.py"\n', encoding="utf-8")
            rc, out, err = tracker_gate()
            check("tracker/wired-non-utf8-source-exit-2",
                  rc == 2 and "docs/bad.py is not valid UTF-8" in err, (rc, out, err))
            man.unlink()
            rc, out, err = tracker_gate()
            check("tracker/wired-missing-manifest-exit-2",
                  rc == 2 and "manifest.toml is missing" in err, (rc, out, err))
        finally:
            shutil.rmtree(wiredlab, ignore_errors=True)

        # 32. DISCRIMINATING (presence and regularity precede any suffix decision, the decode decides
        #     an unfamiliar suffix, and the frozen allowlist binds raw bytes): a listed path that is
        #     absent or is a directory fails closed even under a suffix outside TEXT_SUFFIXES (a suffix
        #     skip taken before validation would read both as clean); a UTF-8 shipped file under an
        #     unfamiliar suffix is scanned like any text file; undecodable bytes under such a suffix (a
        #     committed archive) are skipped; undecodable bytes under a text suffix still fail closed.
        sufx = Path(tempfile.mkdtemp(prefix="aiqt-internal-names-suffix-"))
        try:
            (sufx / ".aiqt").mkdir()
            (sufx / "docs").mkdir()
            man_head = 'format-version = 1\n\n[[sources]]\npath = "docs/a.md"\n'
            (sufx / "docs" / "a.md").write_text("clean\n", encoding="utf-8")
            manifest = sufx / ".aiqt" / "manifest.toml"

            def suffix_scan(extra):
                manifest.write_text(man_head + extra, encoding="utf-8")
                try:
                    return None, scan_shipped_tracker_labels(sufx, [])
                except OSError as exc:
                    return str(exc), None

            absent_err, _unused = suffix_scan('\n[[sources]]\npath = "docs/missing.zip"\n')
            (sufx / "docs" / "d.zip").mkdir()
            nonreg_err, _unused = suffix_scan('\n[[sources]]\npath = "docs/d.zip"\n')
            check("tracker/excluded-suffix-path-still-validated",
                  absent_err is not None and "docs/missing.zip is absent" in absent_err
                  and nonreg_err is not None and "not a regular file" in nonreg_err,
                  (absent_err, nonreg_err))
            qa_vec = "QA" + "26"
            (sufx / "docs" / "t.csv").write_text("x {} y\n".format(qa_vec), encoding="utf-8")
            csv_err, csv_findings = suffix_scan('\n[[sources]]\npath = "docs/t.csv"\n')
            (sufx / "docs" / "blob.zip").write_bytes(b"PK\x03\x04\xff\xfe not text \xff\n")
            zip_err, zip_findings = suffix_scan('\n[[sources]]\npath = "docs/blob.zip"\n')
            (sufx / "docs" / "bad.md").write_bytes(b"\xff\xfe undecodable \xff\n")
            txt_err, _unused = suffix_scan('\n[[sources]]\npath = "docs/bad.md"\n')
            check("tracker/unfamiliar-suffix-decode-decides",
                  csv_err is None and any("docs/t.csv" in f for f in csv_findings)
                  and zip_err is None and not zip_findings
                  and txt_err is not None and "docs/bad.md is not valid UTF-8" in txt_err,
                  (csv_err, csv_findings, zip_err, zip_findings, txt_err))
            # The frozen allowlist binds a path to its RAW on-disk bytes: the digest of those bytes
            # exempts the file, and the digest of the newline-normalized text form does not, so a hash
            # of a form that never existed on disk can never exempt anything.
            crlf = sufx / "docs" / "c.md"
            crlf.write_bytes("x {} y\r\n".format(qa_vec).encode("utf-8"))
            manifest.write_text(man_head + '\n[[sources]]\npath = "docs/c.md"\n', encoding="utf-8")
            raw_digest = hashlib.sha256(crlf.read_bytes()).hexdigest()
            text_digest = hashlib.sha256("x {} y\n".format(qa_vec).encode("utf-8")).hexdigest()
            by_bytes = scan_shipped_tracker_labels(sufx, [], frozen=(("docs/c.md", raw_digest),))
            by_text = scan_shipped_tracker_labels(sufx, [], frozen=(("docs/c.md", text_digest),))
            check("tracker/frozen-allowlist-byte-bound",
                  not by_bytes and any("docs/c.md" in f for f in by_text), (by_bytes, by_text))
        finally:
            shutil.rmtree(sufx, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # The in-run self-guard (as in the sibling registered runners): the executed tracker check-id set is
    # reconciled against this suite's registry row whenever the registry sits beside this script. The
    # self-test's scratch copies run without tools/selftest_checks.toml, so the guard is skipped there;
    # the execution gate's static and report reconciles are the enforced layers either way.
    registry = Path(__file__).resolve().parent / "selftest_checks.toml"
    if registry.is_file():
        expected = None
        try:
            rows = tomllib.loads(registry.read_text(encoding="utf-8")).get("suite", [])
        except (OSError, UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
            failures.append("execution-set/registry: cannot read {} ({!r})".format(registry, exc))
            rows = None
        if rows is not None:
            for row in rows:
                if isinstance(row, dict) and row.get("id") == SUITE_ID:
                    ids = row.get("expected-check-ids")
                    if isinstance(ids, list) and all(isinstance(i, str) for i in ids) \
                            and len(set(ids)) == len(ids):
                        expected = set(ids)
                    break
            if expected is None:
                failures.append("execution-set/registry: missing or malformed suite {!r} in {}".format(
                    SUITE_ID, registry))
            elif len(set(EXECUTED)) != len(EXECUTED) or set(EXECUTED) != expected:
                failures.append("execution-set/reconciled: missing={} extra={} duplicates={}".format(
                    sorted(expected - set(EXECUTED)), sorted(set(EXECUTED) - expected),
                    len(EXECUTED) - len(set(EXECUTED))))

    # A failure outranks a skip: a run that failed a case exits 1 even when it also skipped one, and lists
    # the cases it did not run.
    if failures:
        print("SELF-TEST FAIL:")
        for x in failures:
            print("  - " + x)
        if not_run:
            print("  not run: " + "; ".join(not_run))
        return 1
    if not_run:
        # The full PASS text below claims every case, so a run that failed none but skipped any prints this
        # line instead and exits 3: a skipped case never reports success by exit code, even in a run
        # misrecognized as a child.
        print("{} every case this run ran passed; not run: {}".format(
            "SELF-TEST CHILD PASS:" if launched_child else "SELF-TEST INCOMPLETE:", "; ".join(not_run)))
        return _INCOMPLETE_EXIT
    print("SELF-TEST PASS: clean generic content passes; a seeded provenance id, a fixed host path, and a "
          "hashed codename each fail (removing a layer fails a case); an internal-allow line is exempt from "
          "the structural layer; the whole capitalized internal role name is caught, while Architecture, "
          "architectural, the lowercase noun, the word inside a longer identifier, an internal-allow line and "
          "a SKIP_NAMES basename are not in the scoped scan; a review-round label of each shape (a reviewer "
          "family name and a QA round number, a QA round number and a grade (BLOCKER included) or finding "
          "id) is caught by the "
          "tracked-file rule and the scoped scan in either case, and by the real gate in a tracked file "
          "outside SCOPE_RELPATHS, while \"quality\", \"QA\" alone, a bare grade word, a family name alone, a "
          "family name before \"QA\" with no round number, \"QA\" before a grade with no round number, a "
          "round number alone, a round number before a word that starts with F, a bare F with no digits, a "
          "grade or finding id run straight into a digit or an underscore, a family name or a round "
          "glued to a longer word and parts separated by two spaces, a tab or any one ASCII punctuation "
          "character are not, and an "
          "internal-allow line holding "
          "one is exempt; the real gate run on a fixture git repository "
          "catches the role name in a tracked file outside SCOPE_RELPATHS and in a tracked file whose basename "
          "is in SKIP_NAMES, exempts only the one internal-allow line, scans a binary-attributed file that "
          "decodes as UTF-8, skips a non-UTF-8 tracked ZIP archive the leak gate's archive rule accepts and an "
          "untracked file, and exits 2 "
          "(cannot evaluate) on a tracked non-UTF-8 text file, a tracked file absent from the working tree and "
          "a root that is not a git repository; every tracked .zip goes through the leak gate's archive rule "
          "(check_leaks.verify_archive) whether or not its bytes decode as UTF-8, and a non-UTF-8 tracked file "
          "is skipped only when it is a .zip that rule accepts (a real tracked archive passes, with the "
          "archive-size bound at its exact size and one byte above too, and an accepted archive whose bytes are "
          "valid UTF-8 is scanned and its label found; a malformed .csv and a .csv carrying the ZIP signature "
          "each exit 2 reported as not valid UTF-8; a .zip without the signature, a signature-prefixed .zip "
          "that is not an archive, valid UTF-8 or not, a .zip with one member failing its CRC, a label ahead of "
          "an empty archive's end record, the archive with the bound one byte under its size, and the archive "
          "followed by a label past the bound with the bound at its size each exit 2 named with the leak "
          "gate's reason, and the archive deleted from the working tree exits 2 reported as absent); a "
          "GIT_CONFIG_GLOBAL pointed at a malformed file does not "
          "change the gate result (the gate's tracked-file read drops the GIT_* environment); a child "
          "self-test still passes (child line, exit 3) with a malformed .gitconfig under the caller's HOME, a "
          "malformed git/config "
          "under the caller's XDG_CONFIG_HOME, or a malformed system configuration (supplied through a PATH "
          "shim, since the gate drops GIT_CONFIG_SYSTEM), so the gate's tracked-file read does not read the "
          "caller's home, XDG or system configuration during the self-test; an in-process self_test_main() "
          "restores the caller's environment exactly; a top-level self-test whose environment already holds "
          "the child sentinel as an inherited value still runs cases 19 to 22, shown through a mutated copy "
          "for each of the two values _stale_sentinel_values gives this run (\"1\" and the run's parent pid "
          "where neither the run nor its parent is PID 1); in such a run, the replacement values used where "
          "the run or its parent is PID 1 are covered only by four direct calls, which show each result is "
          "two distinct values other than the run's own pid; a run that fails no case but skips one exits 3, "
          "never 0, shown for a launched child (child line), for a top-level run whose inherited-probe "
          "sentinel matches its parent pid, and for a top-level run with neither sentinel set whose case 11 "
          "symlink creation raises OSError (SELF-TEST INCOMPLETE line each); "
          "a run that fails a case and skips one exits 1 and lists the case it did not run; a malformed "
          "hashes file is reported, never a silent empty denylist; a "
          "non-UTF-8 in-scope surface fails closed as unscannable, never a silent skip; a broken symlink "
          "(a present dentry with a missing target) at a scoped path and at the hash-file path each fails "
          "closed (re-raised), never read as absent; a scoped file "
          "whose BASENAME is in SKIP_NAMES is skipped by basename (not by full relpath); and the shipped "
          "gate-runner and CI-workflow paths (tools/run_all_checks.sh, .github/workflows/quality.yml) are "
          "live SCOPE_RELPATHS members, so a synthetic provenance id planted in each is caught (removing "
          "either from scope fails the case); an UNRECOGNIZED option (a misspelled --self-testx or a "
          "stray flag) is a loud exit 2, never a silent default scan that would let it masquerade as "
          "--self-test; a NON-REGULAR input (a FIFO) at the hash-file path, at a scoped path and at a tracked "
          ".zip path fails closed "
          "PROMPTLY (classified non-regular before any open) rather than blocking on the open; and a hostile "
          "sibling json.py/hashlib.py beside the script does NOT shadow a stdlib import (the sibling modules "
          "are imported via sys.path.append, keeping stdlib precedence), so the copy still runs and passes its "
          "self-test as a child under -I rather than being silently neutered; a maintainer-decision id of each "
          "shape (an ordinal or word head, word segments, a segment carrying a digit, a final one-letter "
          "segment) is caught anywhere in the tree, outside the scoped surfaces too, by the real scan on a "
          "fixture repository as well as by scan_tree_decision_ids, while a bare ordinal, a lower-case check "
          "id, label or prose word and a D- glued to a longer word are not, and an internal-allow line holding "
          "a decision id is exempt; and in the real scan the tree-wide layer fails closed outside the scoped "
          "surfaces: an unreadable file, an unlistable directory and a FIFO each exit 2 naming the input, and a "
          "file that vanishes during the walk is a fail-closed finding naming it; and the shipped "
          "tracker-label layer is held: each label shape is caught, each negative control stays "
          "unflagged and two matches on one line both count, over run-time-assembled vectors, the "
          "path-only frozen (bound to the sha256 of the RAW on-disk bytes) and history "
          "exemptions are honoured and bounded, a line carrying the internal-allow marker is still "
          "flagged, a tracked file under .github/ and one under opf/ each join the shipped set, the "
          "closed baseline schema refuses a malformed, wrongly-typed-version, duplicate, unknown, "
          "non-shipped, non-canonical or reordered "
          "entry (the file must be the byte-exact --write-baseline render of its own entries, sorted "
          "order included) with each schema guard naming its own refusal (so the canonical check "
          "cannot mask a disabled guard) and a "
          "manifest with no sources array is refused, each pattern row, the case flag, the separator "
          "bound, the severity guard and both "
          "ratchet comparisons are load-bearing (a disabled one turns its case red), a listed path that "
          "is absent or non-regular fails closed before any suffix decision while the decode decides an "
          "unfamiliar suffix, and the real gate "
          "on a fixture repository holds the per-file count at, one above and one below its baseline, "
          "round-trips --write-baseline byte-exact, skips the baseline file by name even when it is "
          "listed as a shipped source, accepts a baseline regenerated at grown counts whatever grew "
          "them (holding a raise to refresh merges is review policy over the baseline diff, not a "
          "comparison the gate makes) while one more label above the "
          "regenerated baseline still fails, and fails closed loud on a missing shipped manifest, "
          "an absent listed path, a non-UTF-8 listed text file and a baseline entry for a non-shipped "
          "path.")
    return 0


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
