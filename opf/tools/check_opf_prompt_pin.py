#!/usr/bin/env python3
"""OPF implementation-prompt pin drift gate.

opf/spec/OPF-IMPLEMENTATION-PROMPT.md pins the sources it quotes: one commit of
jposluns/guardrails (named in the pin sentence, the raw-URL template and the example URL), a table
of SHA-256 values for the seven pinned files, and the pack facts (format and version) it states for
opf/prompt-pack/pack.toml. The prompt can only pin an earlier commit of this repository, so without
a gate it drifts whenever a pinned source changes without a re-pin. This gate compares bytes, not
commits, so it fails in the same change that edits a pinned source, and it runs in a shallow clone
and in the isolated opf/ copy of the standalone-closure gate.

Checks, each a named guard:
  prompt-read     the prompt is a readable regular file within the size bound (else CANNOT-EVALUATE)
  pin-shape       the prompt names at least three 40-lowercase-hex commits and they are identical
  table-shape     the pin table lists exactly the seven pinned paths, each once, each digest 64
                  lowercase hex
  table-drift     each table digest equals the SHA-256 of that file's bytes in the checked tree
  pin-history     where the checked tree is a non-shallow git repository: the pinned commit must be
                  present (absent is CANNOT-EVALUATE) and each pinned blob at that commit must hash
                  to the table value; a shallow clone or a tree that is no git repository skips
                  this leg, so the byte comparison above still binds the table
  pack-facts      the format and version the prompt states match opf/prompt-pack/pack.toml
  quotation       every quotation the prompt introduces as source text (with says, states,
                  requires, continues, rules, adds, quotes, allows, fixes or bounds) appears word
                  for word, whitespace-insensitively, in one of the seven pinned files
  required-rule   a fixed list of section 16.1 and section 9.2 sentences appears verbatim
                  (whitespace-insensitively) in both the specification and the prompt

  check_opf_prompt_pin.py              verify the shipped prompt beside this tool (../spec)
  check_opf_prompt_pin.py --self-test  synthetic trees assert each guard's exact status and name,
                                       and that each red fixture turns green when the one check it
                                       covers is disabled, so no other guard also fires on it

Exit convention (matches the repo's gates): 0 VALID, 1 INVALID (a finding, such as pin drift),
2 CANNOT-EVALUATE (unreadable input, a missing pinned commit under full history, a harness error).

The quotation leg checks only quotations introduced with the listed words; a quotation introduced
any other way is not checked, and an exact quotation does not prove the prose around it reads the
source correctly. The history leg authenticates nothing: it reads whatever object store the checked
tree carries.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_prompt_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _opf_store import VALID, INVALID, CANNOT_EVALUATE  # noqa: E402
from _opf_observe import _scrubbed_env  # noqa: E402

PROMPT_REL = "spec/OPF-IMPLEMENTATION-PROMPT.md"
PACK_REL = "prompt-pack/pack.toml"
# The seven pinned paths, exactly the rows the prompt's table must carry (repo-root-relative).
TABLE_PATHS = (
    "opf/spec/OPF-SPEC.md",
    "opf/spec/OPF-QUICKSTART.md",
    "opf/spec/OPF-INIT-D2B.md",
    "opf/spec/OPF-INIT-D2B-REVIEW.md",
    "opf/enforcement/ci/opf-ci.sh",
    "opf/enforcement/ci/github-actions.yml",
    "opf/prompt-pack/pack.toml",
)
SPEC_TABLE_PATH = "opf/spec/OPF-SPEC.md"
MAX_FILE_BYTES = 16 * 1024 * 1024
MIN_PIN_OCCURRENCES = 3
MIN_QUOTE_CHARS = 40
_COMMIT_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])")
_TABLE_ROW_RE = re.compile(r"^\|\s*`([^`|]+)`\s*\|[^|]*\|\s*`([0-9a-f]{64})`\s*\|\s*$", re.MULTILINE)
_INTRODUCER_RE = re.compile(
    r"\b(?:says|say|states|requires|continues|rules|adds|quotes|allows|fixes|bounds)\b")
# A quotation is checked only where its context attributes it to a pinned source; the prompt also
# quotes documents outside the pin (git man pages), which this gate cannot pin.
_SOURCE_CONTEXT_RE = re.compile(
    r"(?i)\bsection\b|OPF-SPEC|QUICKSTART|OPF-INIT-D2B|\bspecification\b|opf-ci\.sh|"
    r"github-actions\.yml|pack\.toml|prompt pack|\brecipe\b|\btemplate\b")
_PIN_SECTION_HEAD = "## Read the specification first"
_PIN_SECTION_TAIL = "## What you must not do"
_GIT_TIMEOUT_S = 60
EXIT_CODES = {VALID: 0, INVALID: 1, CANNOT_EVALUATE: 2}
DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent

# Sentences the prompt must restate word for word (whitespace-insensitively) and the specification
# must still carry; each is quoted from opf/spec/OPF-SPEC.md at the pinned commit (sections 9.2 and
# 16.1). Deleting a restatement from the prompt, or changing the specification so one no longer
# appears there, is a finding that forces a reviewed re-pin.
REQUIRED_RULES = (
    ("one-file",
     "Each release MUST ship that declaration as one file, the declaration file."),
    ("carry-digest-apart",
     "Each release MUST also carry, in the implementation and apart from that file, the SHA-256 "
     "digest of that file's bytes as the release ships them (the shipped-file digest), and the "
     "documentation of each release MUST state where the implementation carries it. A release that "
     "carries no such digest, or whose documentation states no such place, does not conform to "
     "this section, and a conformance claim MUST NOT be made for it."),
    ("expected-report-digests",
     "each of its digests equals the SHA-256 digest of that file's bytes as the release ships "
     "them"),
    ("release-nonconformance",
     "Each such failure is a release nonconformance, never a runtime state of the declaration: it "
     "MUST NOT be treated as making the declaration malformed or contradictory, and it does not "
     "change what a runtime check of an installed copy yields."),
    ("no-class-bound",
     "The file declares no class only where it omits its class and no other field: a file that is "
     "absent, or that also omits its release identity, its `spec_version`, its homes generation, "
     "or its worklog storage generation, is malformed and MUST yield cannot-evaluate."),
    ("imported-counter-ancestry",
     "Where imported ancestry exists in a namespace, the imported counter row added for it MUST "
     "hold the highest imported ID number in that namespace, the largest `<n>` of its "
     "`imported:<NS>-<n>` IDs."),
)


class _Refusal(Exception):
    def __init__(self, status, guard, detail):
        super().__init__(detail)
        self.status = status
        self.guard = guard


def _require(condition, guard, detail, status=INVALID):
    """Each named guard is also the discriminator the self-test asserts."""
    if not condition:
        raise _Refusal(status, guard, detail)


def _normalize(text):
    """One comparison form for quotation and rule matching: runs of whitespace collapse to one
    space, so re-wrapped lines still match, while every other byte stays significant."""
    return " ".join(text.split())


def _read_regular(path, what):
    """Exact bytes of a regular file, bounded, never following a final symlink."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise _Refusal(CANNOT_EVALUATE, what + "-read", "{} unreadable: {}".format(what, exc))
    _require(stat.S_ISREG(mode), what + "-read",
             "{} {!s} is not a regular file".format(what, path), CANNOT_EVALUATE)
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise _Refusal(CANNOT_EVALUATE, what + "-read", "{} unreadable: {}".format(what, exc))
    _require(len(data) <= MAX_FILE_BYTES, what + "-bound",
             "{} exceeds the size bound".format(what), CANNOT_EVALUATE)
    return data


def _check_pin_shape(commits):
    """At least MIN_PIN_OCCURRENCES 40-hex commit spellings, all identical."""
    _require(len(commits) >= MIN_PIN_OCCURRENCES, "pin-shape",
             "the prompt names the pinned commit {} time(s); expected at least {}".format(
                 len(commits), MIN_PIN_OCCURRENCES))
    _require(len(set(commits)) == 1, "pin-shape",
             "the prompt's pinned-commit spellings disagree: {}".format(sorted(set(commits))))


def _parse_table(prompt_text):
    """The pin table as {path: digest}: exactly TABLE_PATHS, each once."""
    rows = _TABLE_ROW_RE.findall(prompt_text)
    found = [path for path, _ in rows]
    _require(sorted(found) == sorted(set(found)), "table-shape",
             "a pin-table path is listed twice: {}".format(
                 sorted(set(p for p in found if found.count(p) > 1))))
    _require(set(found) == set(TABLE_PATHS), "table-shape",
             "pin-table paths differ from the pinned set: missing {}, extra {}".format(
                 sorted(set(TABLE_PATHS) - set(found)), sorted(set(found) - set(TABLE_PATHS))))
    return dict(rows)


def _check_drift(root, table):
    """Each table digest equals the SHA-256 of that file's bytes in the checked tree."""
    for rel in TABLE_PATHS:
        actual = hashlib.sha256(_read_regular(root / rel, "pinned-source")).hexdigest()
        _require(actual == table[rel], "table-drift",
                 "{} drifted from the prompt's pin table: pinned {}, actual {}".format(
                     rel, table[rel], actual))


def _git(root, args, env):
    # F-367: no detached auto-gc or auto-maintenance may outlive a call and churn .git while a
    # later read or a fixture teardown traverses it.
    return subprocess.run(["git", "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                           "-c", "maintenance.auto=false"] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=_GIT_TIMEOUT_S, env=env)


def _check_history(root, pin, table):
    """Where full history is present, the blobs at the pinned commit must match the table. Returns
    a one-line description of what ran, for the VALID detail."""
    if shutil.which("git") is None:
        return "history: skipped (no git)"
    env = _scrubbed_env()
    try:
        inside = _git(root, ["rev-parse", "--is-inside-work-tree"], env)
        if inside.returncode != 0 or inside.stdout.strip() != b"true":
            return "history: skipped (not a git work tree)"
        shallow = _git(root, ["rev-parse", "--is-shallow-repository"], env)
        if shallow.returncode != 0:
            raise _Refusal(CANNOT_EVALUATE, "pin-history",
                           "git rev-parse --is-shallow-repository failed: {}".format(
                               shallow.stderr.decode(errors="backslashreplace").strip()))
        if shallow.stdout.strip() == b"true":
            return "history: skipped (shallow repository)"
        present = _git(root, ["cat-file", "-e", pin + "^{commit}"], env)
        _require(present.returncode == 0, "pin-history",
                 "pinned commit {} is absent under full history".format(pin), CANNOT_EVALUATE)
        for rel in TABLE_PATHS:
            shown = _git(root, ["show", "{}:{}".format(pin, rel)], env)
            _require(shown.returncode == 0, "pin-history",
                     "pinned blob {}:{} is unreadable: {}".format(
                         pin, rel, shown.stderr.decode(errors="backslashreplace").strip()),
                     CANNOT_EVALUATE)
            actual = hashlib.sha256(shown.stdout).hexdigest()
            _require(actual == table[rel], "pin-history",
                     "{} at the pinned commit does not match the pin table: pinned {}, "
                     "at {} {}".format(rel, table[rel], pin, actual))
        return "history: verified at {}".format(pin)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Refusal(CANNOT_EVALUATE, "pin-history", "git could not be run: {}".format(exc))


def _pin_section(prompt_text):
    head = prompt_text.find(_PIN_SECTION_HEAD)
    tail = prompt_text.find(_PIN_SECTION_TAIL)
    _require(0 <= head < tail, "pin-section",
             "the prompt lacks the pin section between {!r} and {!r}".format(
                 _PIN_SECTION_HEAD, _PIN_SECTION_TAIL))
    return prompt_text[head:tail]


def _check_pack_facts(root, prompt_text):
    """The format and version the pin section states match opf/prompt-pack/pack.toml."""
    section = _pin_section(prompt_text)
    stated_fmt = re.findall(r"`format = \"([^\"`]+)\"`", section)
    stated_ver = re.findall(r"`version = \"([^\"`]+)\"`", section)
    _require(len(stated_fmt) == 1 and len(stated_ver) == 1, "pack-facts",
             "the pin section must state the pack format and version exactly once each; found "
             "{} format and {} version statement(s)".format(len(stated_fmt), len(stated_ver)))
    raw = _read_regular(root / "opf" / PACK_REL, "pack-manifest")
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError, MemoryError) as exc:
        raise _Refusal(CANNOT_EVALUATE, "pack-manifest-parse",
                       "pack.toml does not parse: {}".format(exc))
    for key, stated in (("format", stated_fmt[0]), ("version", stated_ver[0])):
        actual = doc.get(key)
        _require(stated == actual, "pack-facts",
                 "the prompt states pack {} {!r}; pack.toml carries {!r}".format(
                     key, stated, actual))


def _introduced_quotes(prompt_text):
    """Quotations the prompt introduces as source text: for each introducer word, the first
    double-quoted span whose opening quote lies within 160 characters, with no blank line between
    the introducer and that quote. Spans shorter than MIN_QUOTE_CHARS after normalization are
    tokens, not quotations, and are skipped."""
    quotes = []
    last_end = 0
    for match in _INTRODUCER_RE.finditer(prompt_text):
        if match.start() < last_end:
            # An introducer word inside a quotation already captured introduces nothing.
            continue
        window_start = match.end()
        opening = prompt_text.find('"', window_start)
        if opening < 0 or opening - window_start > 160:
            continue
        if "\n\n" in prompt_text[window_start:opening]:
            continue
        if not _SOURCE_CONTEXT_RE.search(prompt_text[max(0, match.start() - 200):opening]):
            continue
        closing = prompt_text.find('"', opening + 1)
        if closing < 0:
            continue
        last_end = closing + 1
        quote = _normalize(prompt_text[opening + 1:closing])
        if len(quote) >= MIN_QUOTE_CHARS:
            quotes.append(quote)
    return quotes


def _strip_comment_leaders(text):
    """The prompt quotes shell and workflow comments as prose, without their leading `#` markers,
    so each source also joins the corpus with those markers stripped per line."""
    return "\n".join(re.sub(r"^\s*#+\s?", "", line) for line in text.splitlines())


def _check_quotations(prompt_text, sources_norm):
    for quote in _introduced_quotes(prompt_text):
        _require(quote in sources_norm, "quotation",
                 "a quotation the prompt introduces as source text appears in no pinned "
                 "source: {!r}".format(quote[:160]))


def _check_required_rules(spec_norm, prompt_norm):
    for name, sentence in REQUIRED_RULES:
        wanted = _normalize(sentence)
        _require(wanted in spec_norm, "required-rule",
                 "required rule {!r} is absent from the pinned specification".format(name))
        _require(wanted in prompt_norm, "required-rule",
                 "required rule {!r} is not restated in the prompt".format(name))


def _validate(root):
    root = Path(root)
    prompt_text = _read_regular(root / "opf" / PROMPT_REL, "prompt").decode(
        "utf-8", errors="backslashreplace")
    commits = _COMMIT_RE.findall(prompt_text)
    _check_pin_shape(commits)
    pin = commits[0] if commits else None
    table = _parse_table(prompt_text)
    _check_drift(root, table)
    history = _check_history(root, pin, table) if pin else "history: skipped (no pin found)"
    _check_pack_facts(root, prompt_text)
    source_texts = [_read_regular(root / rel, "pinned-source").decode(
        "utf-8", errors="backslashreplace") for rel in TABLE_PATHS]
    sources_norm = "\n".join(
        [_normalize(text) for text in source_texts]
        + [_normalize(_strip_comment_leaders(text)) for text in source_texts])
    spec_norm = _normalize(_read_regular(root / SPEC_TABLE_PATH, "pinned-source").decode(
        "utf-8", errors="backslashreplace"))
    _check_quotations(prompt_text, sources_norm)
    _check_required_rules(spec_norm, _normalize(prompt_text))
    return "pin {}, {} table row(s), {}".format(pin, len(table), history)


def validate_tree(root):
    """(status, guard, detail) for the tree at root. Reads only; writes nothing."""
    try:
        detail = _validate(root)
    except _Refusal as refusal:
        return refusal.status, refusal.guard, str(refusal)
    return VALID, None, detail


# ---- self-test ------------------------------------------------------------------------------------


_FIXTURE_SOURCES = {
    "opf/spec/OPF-QUICKSTART.md": "# Quickstart\n\nA short orientation; the specification governs.\n",
    "opf/spec/OPF-INIT-D2B.md": "# Init contract\n\nThe coupled initialization contract.\n",
    "opf/spec/OPF-INIT-D2B-REVIEW.md": "# Review register\n\nThe review register for that contract.\n",
    "opf/enforcement/ci/opf-ci.sh": "#!/bin/sh\n# The reference CI recipe. Views regenerate cleanly.\n",
    "opf/enforcement/ci/github-actions.yml": "name: opf\n# The reference CI workflow template.\n",
}
_FIXTURE_PACK = ('format = "opf.prompt-pack/v1"\nversion = "0.5.1"\n'
                 'digest = "sha256:' + "0" * 64 + '"\nmembers = []\n')
_FIXTURE_PIN = "c" * 40


def _fixture_spec():
    rules = "\n\n".join(sentence for _, sentence in REQUIRED_RULES)
    return ("# Specification\n\n" + rules +
            "\n\nViews regenerate cleanly. The emitter writes canonical bytes for every view it "
            "renders.\n")


def _fixture_prompt(root, pin=_FIXTURE_PIN,
                    quote="The emitter writes canonical bytes for every view it renders.",
                    pack_version="0.5.1", drop_rule=None, digest_overrides=None):
    digests = {}
    for rel in TABLE_PATHS:
        digests[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    if digest_overrides:
        digests.update(digest_overrides)
    table = "\n".join("| `{}` | pinned | `{}` |".format(rel, digests[rel]) for rel in TABLE_PATHS)
    rules = []
    for name, sentence in REQUIRED_RULES:
        if name != drop_rule:
            rules.append('The specification says "{}"'.format(sentence))
    return (_PIN_SECTION_HEAD + "\n\nRead these files at the pinned commit `" + pin +
            "` of the repository.\n\n| File | Role | SHA-256 at the pinned commit |\n|---|---|---|\n" +
            table + "\n\nEach raw URL names the commit:\n\n```text\nhttps://example.invalid/" + pin +
            "/<path>\n```\n\nFor example, https://example.invalid/" + pin + "/opf/spec/OPF-SPEC.md.\n\n" +
            'The pack manifest (`format = "opf.prompt-pack/v1"`, `version = "' + pack_version +
            '"`) lists no members.\n\n' + _PIN_SECTION_TAIL + "\n\n" +
            'The specification says "' + quote + '"\n\n' + "\n\n".join(rules) + "\n")


def _build_tree(base, **prompt_kwargs):
    root = Path(base)
    for rel, text in _FIXTURE_SOURCES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / SPEC_TABLE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / SPEC_TABLE_PATH).write_text(_fixture_spec(), encoding="utf-8")
    (root / "opf" / PACK_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / "opf" / PACK_REL).write_text(_FIXTURE_PACK, encoding="utf-8")
    prompt = root / "opf" / PROMPT_REL
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(_fixture_prompt(root, **prompt_kwargs), encoding="utf-8")
    return root


def _spec_edit(root):
    """One byte, in a sentence no fixture quotation and no required rule carries, so only the
    byte comparisons can catch it."""
    spec = root / SPEC_TABLE_PATH
    spec.write_text(spec.read_text(encoding="utf-8").replace(
        "regenerate cleanly", "regenerate Cleanly", 1), encoding="utf-8")


def _vectors():
    """(name, expected status, expected guard, covering check, builder(base)). The covering check
    is the module function whose removal must turn the fixture green."""
    bad = "d" * 64

    def valid(base):
        _build_tree(base)

    def spec_edited(base):
        _spec_edit(_build_tree(base))

    def digest_edited(base):
        _build_tree(base, digest_overrides={SPEC_TABLE_PATH: bad})

    def pins_disagree(base):
        root = _build_tree(base)
        prompt = root / "opf" / PROMPT_REL
        text = prompt.read_text(encoding="utf-8")
        prompt.write_text(text.replace("https://example.invalid/" + _FIXTURE_PIN + "/<path>",
                                       "https://example.invalid/" + "e" * 40 + "/<path>", 1),
                          encoding="utf-8")

    def pack_bumped(base):
        # The prompt's stated version drifts from pack.toml; the pack bytes themselves stay
        # pinned, so only the pack-facts comparison can catch it.
        _build_tree(base, pack_version="0.6.0")

    def rule_deleted(base):
        _build_tree(base, drop_rule="one-file")

    def quote_reworded(base):
        _build_tree(base, quote="The emitter writes non-canonical bytes for every view it renders.")

    def quote_at_floor(base):
        # Exactly MIN_QUOTE_CHARS: the shortest span the quotation leg still checks.
        _build_tree(base, quote="z" * MIN_QUOTE_CHARS)

    def quote_below_floor(base):
        # One character under the floor: a token, never a quotation, so it is not checked.
        _build_tree(base, quote="z" * (MIN_QUOTE_CHARS - 1))

    def prompt_absent(base):
        root = _build_tree(base)
        (root / "opf" / PROMPT_REL).unlink()

    return [
        ("valid-tree", VALID, None, None, valid),
        ("spec-one-byte-edit", INVALID, "table-drift", "_check_drift", spec_edited),
        ("table-digest-edited", INVALID, "table-drift", "_check_drift", digest_edited),
        ("pin-occurrences-disagree", INVALID, "pin-shape", "_check_pin_shape", pins_disagree),
        ("pack-version-bumped", INVALID, "pack-facts", "_check_pack_facts", pack_bumped),
        ("required-rule-deleted", INVALID, "required-rule", "_check_required_rules", rule_deleted),
        ("quotation-reworded", INVALID, "quotation", "_check_quotations", quote_reworded),
        ("quotation-at-floor", INVALID, "quotation", "_check_quotations", quote_at_floor),
        ("quotation-below-floor", VALID, None, None, quote_below_floor),
        ("prompt-absent", CANNOT_EVALUATE, "prompt-read", None, prompt_absent),
    ]


def _with_check_disabled(name):
    """Replace the named module-level check with a no-op; returns the restorer. Attribute patching,
    so the disabled run exercises the real _validate with one guard removed."""
    real = globals()[name]

    def restore():
        globals()[name] = real
    globals()[name] = lambda *args, **kwargs: None
    return restore


def _history_vectors(tmp, check):
    """The pin-history leg needs real repositories, so these vectors run only where git is on
    PATH; the in-tree byte checks above stay git-independent for the standalone-closure copy."""
    if shutil.which("git") is None:
        print("SKIP history vectors: git is not on PATH")
        return 0
    env = _scrubbed_env()

    def repo(base):
        root = _build_tree(base)
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                      "commit", "-q", "-m", "fixture"]):
            proc = _git(root, args, env)
            if proc.returncode != 0:
                raise RuntimeError("fixture git failed: {}".format(
                    proc.stderr.decode(errors="backslashreplace")))
        head = _git(root, ["rev-parse", "HEAD"], env).stdout.decode().strip()
        prompt = root / "opf" / PROMPT_REL
        prompt.write_text(prompt.read_text(encoding="utf-8").replace(_FIXTURE_PIN, head),
                          encoding="utf-8")
        return root, head

    ran = 0
    root, _ = repo(Path(tmp) / "history-valid")
    got = validate_tree(root)
    ran += 1
    check("history-valid", got, (VALID, None))

    root, _ = repo(Path(tmp) / "history-drifted")
    _spec_edit(root)
    prompt = root / "opf" / PROMPT_REL
    new_digest = hashlib.sha256((root / SPEC_TABLE_PATH).read_bytes()).hexdigest()
    text = prompt.read_text(encoding="utf-8")
    old_row = re.search(r"\| `{}` \| pinned \| `([0-9a-f]{{64}})` \|".format(
        re.escape(SPEC_TABLE_PATH)), text).group(1)
    prompt.write_text(text.replace(old_row, new_digest), encoding="utf-8")
    got = validate_tree(root)
    ran += 1
    check("history-blob-mismatch", got, (INVALID, "pin-history"))
    restore = _with_check_disabled("_check_history")
    try:
        disabled = validate_tree(root)
    finally:
        restore()
    ran += 1
    check("history-blob-mismatch-disabled", disabled, (VALID, None))

    root, head = repo(Path(tmp) / "history-missing")
    prompt = root / "opf" / PROMPT_REL
    prompt.write_text(prompt.read_text(encoding="utf-8").replace(head, "f" * 40),
                      encoding="utf-8")
    got = validate_tree(root)
    ran += 1
    check("history-commit-missing", got, (CANNOT_EVALUATE, "pin-history"))
    return ran


def self_test():
    """0 every vector returned its exact status and guard (and turned green with its covering check
    disabled), 1 a discriminator failed, 2 harness error."""
    failures = []
    count = 0

    def check(name, got, wanted):
        ok = got[:2] == wanted
        print("{} {}: {} {}".format("PASS" if ok else "FAIL", name, got[0], got[1] or "").rstrip())
        if not ok:
            failures.append("{} (wanted {} {}, got {} {}: {})".format(
                name, wanted[0], wanted[1], got[0], got[1], got[2]))

    try:
        with tempfile.TemporaryDirectory(prefix="opf-prompt-pin-selftest-") as tmp:
            for index, (name, status, guard, covering, build) in enumerate(_vectors()):
                base = Path(tmp) / "case{}".format(index)
                base.mkdir()
                build(base)
                got = validate_tree(base)
                again = validate_tree(base)
                count += 1
                check(name, got if again == got else (got[0], got[1], "unstable: " + str(again)),
                      (status, guard))
                if covering is not None:
                    restore = _with_check_disabled(covering)
                    try:
                        disabled = validate_tree(base)
                    finally:
                        restore()
                    count += 1
                    check(name + "-disabled-" + covering, disabled, (VALID, None))
            count += _history_vectors(tmp, check)
    except Exception as exc:  # noqa: BLE001  a harness error is cannot-evaluate, never a pass
        print("OPF-PROMPT-PIN SELF-TEST: CANNOT EVALUATE ({!r})".format(exc), file=sys.stderr)
        return 2
    if failures:
        print("OPF-PROMPT-PIN SELF-TEST: FAIL ({} of {} checks failed): {}".format(
            len(failures), count, "; ".join(failures)))
        return 1
    print("OPF-PROMPT-PIN SELF-TEST: PASS ({} checks)".format(count))
    return 0


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return self_test()
        if args:
            print("usage: check_opf_prompt_pin.py [--self-test]", file=sys.stderr)
            return 2
        status, guard, detail = validate_tree(DEFAULT_ROOT)
        if status == VALID:
            print("OPF PROMPT PIN: VALID ({})".format(detail))
        else:
            print("OPF PROMPT PIN: {} [{}] {}".format(status, guard, detail), file=sys.stderr)
        return EXIT_CODES[status]
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("check_opf_prompt_pin: cannot evaluate: unexpected error ({!r})".format(exc),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
