#!/usr/bin/env python3
"""Stale pending-claim check: a record sentence that says a named pull request is pending, open, under
review, stacked on or waiting, while that pull request has merged or closed.

  check_pending_pr_claims.py --check-inputs           offline: validate the declaration and every
                                                     declared surface, list the candidate claims;
                                                     never reads pull request state
  check_pending_pr_claims.py --all --live             read every declared surface and the live state
                                                     of every bound pull request
  check_pending_pr_claims.py --draft PATH --target T  judge the complete proposed content PATH as the
                                                     declared surface T (live)
  check_pending_pr_claims.py --staged --live          judge the staged blob of every declared surface
                                                     the index changes (live)
  check_pending_pr_claims.py --self-test              synthetic fixtures and stubbed gh processes
  check_pending_pr_claims.py --self-test --execution-report ABS_PATH
  any other argument list is refused with exit 2

Exit codes: 0 every bound claim was evaluated and none contradicts its pull request's lifecycle
(advisory warnings may remain); 1 one or more hard findings, evaluation otherwise complete; 2 the
evaluation is incomplete (pull request state could not be read, a binding is ambiguous, a declared
surface is missing or malformed, or a usage error). Definite findings are printed alongside the
unknowns. A run with no candidates means only that no supported current claim was found.

The declaration is .aiqt/pending-pr-claims.toml; the contract, grammar and residuals are in
.aiqt/pending-pr-claims.md. State is read by tools/_pr_state.py (gh api, host pinned to github.com);
a caller-supplied state map is never accepted as authority. The --check-inputs mode is the only
mode the Quality workflow runs: it is deterministic and offline and does not assess currency.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_pending_pr_claims.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pr_state  # noqa: E402  (the lifecycle reader beside this checker)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = ".aiqt/pending-pr-claims.toml"
MAX_BYTES = 4 * 1024 * 1024
FORMATS = ("toml-strings", "markdown")
ROLES = ("source", "generated-view")
TOP_KEYS = {"schema-version", "limits", "surface"}
LIMIT_KEYS = {"max-prs": (1, 1000), "call-timeout-seconds": (1, 60), "deadline-seconds": (1, 600)}
SURFACE_KEYS = {"path", "format", "repository", "role"}
SURFACE_OPTIONAL = {"expected-repository", "generated-from", "status-column", "note"}

HARD, WARN, AMBIGUOUS, QUOTE = "hard", "warning", "ambiguous", "quote"


class InputError(Exception):
    """A declaration or declared surface that cannot be evaluated (exit 2)."""


# ---------------------------------------------------------------------------------------------------
# The declaration.

def _parse_toml(text, label):
    """(document, None), or (None, reason) for text that is not valid TOML. The one tomllib call site:
    a bare ValueError (an integer past the int-string limit), a RecursionError (deep nesting) and a
    MemoryError are each handled here, never re-raised."""
    try:
        return tomllib.loads(text), None
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "{}: is not valid TOML ({}: {})".format(label, type(exc).__name__, exc)


def load_config(root):
    """The validated declaration as a dict; InputError on any problem (fail closed)."""
    data, why = _parse_toml(_read_text(Path(root) / CONFIG_REL, CONFIG_REL), CONFIG_REL)
    if why is not None:
        raise InputError(why)
    return validate_config(data)


def validate_config(data):
    if set(data) != TOP_KEYS:
        raise InputError("{}: top-level keys must be exactly {} (got {})".format(
            CONFIG_REL, sorted(TOP_KEYS), sorted(data)))
    if type(data["schema-version"]) is not int or data["schema-version"] != 1:
        raise InputError("{}: schema-version must be exactly the integer 1".format(CONFIG_REL))
    limits = data["limits"]
    if not isinstance(limits, dict) or set(limits) != set(LIMIT_KEYS):
        raise InputError("{}: [limits] keys must be exactly {}".format(CONFIG_REL, sorted(LIMIT_KEYS)))
    for key, (low, high) in LIMIT_KEYS.items():
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise InputError("{}: limits.{} must be an integer from {} to {}".format(
                CONFIG_REL, key, low, high))
    surfaces = data["surface"]
    if not isinstance(surfaces, list) or not surfaces:
        raise InputError("{}: [[surface]] must be a non-empty array of tables".format(CONFIG_REL))
    seen = set()
    for index, row in enumerate(surfaces):
        where = "{}: surface[{}]".format(CONFIG_REL, index)
        if (not isinstance(row, dict) or not SURFACE_KEYS <= set(row)
                or not set(row) <= SURFACE_KEYS | SURFACE_OPTIONAL):
            raise InputError("{}: keys must be {} (optionally {})".format(
                where, sorted(SURFACE_KEYS), sorted(SURFACE_OPTIONAL)))
        for key, value in row.items():
            if not isinstance(value, str) or not value or any(ord(ch) < 32 for ch in value):
                raise InputError("{}: {} must be a non-empty single-line string".format(where, key))
        path = row["path"]
        if os.path.isabs(path) or ".." in Path(path).parts or "\\" in path or path in seen:
            raise InputError("{}: path {!r} must be a unique repo-relative path with no '..'".format(
                where, path))
        seen.add(path)
        if row["format"] not in FORMATS:
            raise InputError("{}: format must be one of {}".format(where, list(FORMATS)))
        if row["role"] not in ROLES:
            raise InputError("{}: role must be one of {}".format(where, list(ROLES)))
        if row["repository"] != "origin" and not _pr_state.valid_repo(row["repository"]):
            raise InputError("{}: repository must be \"origin\" or OWNER/REPO".format(where))
        if "expected-repository" in row and not _pr_state.valid_repo(row["expected-repository"]):
            raise InputError("{}: expected-repository must be OWNER/REPO".format(where))
        if "status-column" in row and row["format"] != "markdown":
            raise InputError("{}: status-column applies to a markdown surface only".format(where))
        if row["role"] == "generated-view" and "generated-from" not in row:
            raise InputError("{}: a generated-view surface must name generated-from".format(where))
    return data


def _read_bytes(path, label):
    """The bytes of a regular, non-symlink file of at most MAX_BYTES; InputError otherwise."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise InputError("{}: cannot be read ({})".format(label, exc.strerror or type(exc).__name__))
    if not stat.S_ISREG(st.st_mode):
        raise InputError("{}: is not a regular file (a symlink or directory is refused)".format(label))
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise InputError("{}: cannot be read ({})".format(label, exc.strerror or type(exc).__name__))
    if len(data) > MAX_BYTES:
        raise InputError("{}: larger than {} bytes".format(label, MAX_BYTES))
    return data


def _decode(data, label):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("{}: is not UTF-8".format(label))
    if "\x00" in text:
        raise InputError("{}: carries a NUL character".format(label))
    return text


def _read_text(path, label):
    return _decode(_read_bytes(path, label), label)


# ---------------------------------------------------------------------------------------------------
# Record units: complete paragraphs, list items, table cells and decoded TOML strings, with locations.

def toml_units(text, label):
    """[(location, prose, context)] for every decoded string value of a TOML document."""
    data, why = _parse_toml(text, label)
    if why is not None:
        raise InputError(why)
    units = []

    def walk(value, where):
        if isinstance(value, str):
            units.append(("{}: {}".format(label, where), value, "prose"))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, "{}.{}".format(where, key) if where else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "{}[{}]".format(where, index))

    walk(data, "")
    return units


FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
QUOTE_RE = re.compile(r" {0,3}>[ ]?(.*)")
HEADING_RE = re.compile(r" {0,3}#{1,6}(?:\s+(.*))?$")
ITEM_RE = re.compile(r"\s*(?:[-*+]|[0-9]{1,9}[.)])\s+(?:\[[ xX]\]\s+)?(.*)")
SEPARATOR_CELL_RE = re.compile(r"\s*:?-{3,}:?\s*")
HISTORY_INTRO_RE = re.compile(r"(?i)(?:earlier note|previously recorded)\s*:\s*$")
HISTORY_LEAD_RE = re.compile(r"(?i)\s*(?:earlier note|previously recorded)\s*:")


def _cells(line):
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.replace("\ue002", "\\|").strip() for cell in body.replace("\\|", "\ue002").split("|")]


def markdown_units(text, label, status_column=None):
    """[(location, prose, context)] for a Markdown document. Fenced blocks are skipped; a blockquote
    whose lead-in (the paragraph just before it, or its own first words) is `Earlier note:` or
    `Previously recorded:` is historical and skipped; any other blockquote is context "quote"; a cell
    of the declared status column is context "status"; everything else is "prose". Wrapped lines of
    one paragraph or list item are joined into one unit."""
    units = []
    lines = text.split("\n")
    fence = None
    para, para_line = [], 0
    quote, quote_line = [], 0
    last_prose = ""
    header = None

    def flush_para():
        nonlocal para, last_prose
        if para:
            joined = " ".join(para)
            units.append(("{}:{}".format(label, para_line), joined, "prose"))
            last_prose = joined
        para = []

    def flush_quote():
        nonlocal quote, last_prose
        if quote:
            joined = " ".join(quote)
            historical = (HISTORY_INTRO_RE.search(last_prose) is not None
                          or HISTORY_LEAD_RE.match(joined) is not None)
            if not historical:
                units.append(("{}:{} (blockquote)".format(label, quote_line), joined, "quote"))
            last_prose = ""
        quote = []

    for number, line in enumerate(lines, 1):
        if fence is not None:
            stripped = line.strip()
            if stripped.startswith(fence) and set(stripped) == {fence[0]}:
                fence = None
            continue
        match = FENCE_RE.match(line)
        if match:
            flush_para()
            flush_quote()
            fence = match.group(1)
            header = None
            continue
        if not line.strip():
            flush_para()
            flush_quote()
            header = None
            continue
        match = QUOTE_RE.match(line)
        if match:
            flush_para()
            if not quote:
                quote_line = number
            quote.append(match.group(1).strip())
            continue
        flush_quote()
        if line.lstrip().startswith("|"):
            flush_para()
            cells = _cells(line)
            if all(SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells):
                continue
            following = lines[number] if number < len(lines) else ""
            if header is None and following.lstrip().startswith("|") and all(
                    SEPARATOR_CELL_RE.fullmatch(cell) for cell in _cells(following)):
                header = cells
                continue
            for column, cell in enumerate(cells):
                name = header[column] if header is not None and column < len(header) else None
                context = "status" if status_column is not None and name == status_column else "prose"
                units.append(("{}:{} ({} cell)".format(label, number, name or "column {}".format(
                    column + 1)), cell, context))
            continue
        header = None
        match = HEADING_RE.match(line)
        if match:
            flush_para()
            units.append(("{}:{}".format(label, number), (match.group(1) or "").strip(), "prose"))
            last_prose = units[-1][1]
            continue
        match = ITEM_RE.match(line)
        if match:
            flush_para()
            para, para_line = [match.group(1).strip()], number
            continue
        if not para:
            para_line = number
        para.append(line.strip())
    if fence is not None:
        raise InputError("{}: a fenced block opened is never closed".format(label))
    flush_para()
    flush_quote()
    return units


def surface_units(surface, data):
    """The record units of one declared surface given its complete bytes; InputError when malformed."""
    label = surface["path"]
    text = _decode(data, label)
    if surface["format"] == "toml-strings":
        return toml_units(text, label)
    return markdown_units(text, label, surface.get("status-column"))


# ---------------------------------------------------------------------------------------------------
# The grammar: pull request references, clause boundaries, and bound claim phrases.

_O = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_R = r"[A-Za-z0-9._-]{1,100}"
REF_RE = re.compile(
    r"(?:\b(?:pull\s+requests?|PRs?)\s+)?"
    r"(?:https://github\.com/(?P<uo>" + _O + r")/(?P<ur>" + _R + r")/pull/(?P<un>[0-9]{1,9})(?![\w/])"
    r"|(?<![\w./-])(?P<qo>" + _O + r")/(?P<qr>" + _R + r")#(?P<qn>[0-9]{1,9})(?![\w])"
    r"|\bpr:#?(?P<pn>[0-9]{1,9})(?![\w])"
    r"|(?<![\w&#/])#(?P<hn>[0-9]{1,9})(?![\w]))",
    re.IGNORECASE)
# The placeholder a pull request reference is replaced with before the phrase rules run (private-use
# characters, so no record text can spell it).
REF_MARK = "\ue000[0-9]+\ue001"
REF_MARK_RE = re.compile("\ue000([0-9]+)\ue001")
LIST = r"(?P<l>{0}(?:\s*,\s*(?:and\s+|or\s+)?{0}|\s+(?:and|or)\s+{0})*)".format(REF_MARK)
VERB = r"(?:(?:is|are|remains?|stays?)(?:\s+still)?|still)"
# A historical lead-in followed by a quoted span: only that span is exempt.
HISTORY_SPAN_RE = re.compile(
    r"(?i)\b(?:previously recorded|earlier note|the old note read|old note read|was recorded as|"
    r"previously read)\s*:?\s*(?:\u201c[^\u201d]*\u201d|\"[^\"]*\"|`[^`]*`|\u2018[^\u2019]*\u2019)")
CLAUSE_SPLIT_RE = re.compile(r"[;:!?\u00a6]|\.(?=\s|$)|\s[\u2013\u2014]\s|\s-\s")
FORMATTING_RE = re.compile("[`\u201c\u201d\u2018\u2019\"*]|~~")
INSTRUCTION_WORDS = {"leave", "leaves", "left", "keep", "keeps", "kept", "to", "will", "should",
                     "must", "please", "re"}

PATTERNS = (
    # (kind, phrase, regex, contexts) in priority order; a token bound HARD is not also a warning.
    (HARD, "in progress (an open pull request under review)",
     re.compile(r"^\s*in\s+progress\s*\(\s*" + LIST + r"\s*\)", re.I), ("status",)),
    (HARD, "pending", re.compile(LIST + r"\s+" + VERB + r"\s+pending\b", re.I), None),
    (HARD, "pending", re.compile(LIST + r"\s*\(\s*pending\s*\)", re.I), None),
    (HARD, "a pending batch member", re.compile(r"\bpending\s+batch\s*\(\s*" + LIST + r"\s*\)", re.I),
     None),
    (HARD, "pending", re.compile(r"\bpending\s+(?:merge\s+of\s+)?" + LIST, re.I), None),
    (HARD, "open", re.compile(LIST + r"\s+" + VERB + r"\s+open\b", re.I), None),
    (HARD, "open", re.compile(r"(?<![\w-])open\s+" + LIST, re.I), None),
    (HARD, "under review", re.compile(LIST + r"\s+(?:is|are)\s+(?:still\s+)?(?:under|in)\s+review\b",
                                      re.I), None),
    (HARD, "yet to merge", re.compile(
        r"\b(?:waiting|wait|waits)\s+(?:for|on)\s+" + LIST + r"\s+to\s+(?:merge|land|be\s+merged)\b",
        re.I), None),
    (HARD, "yet to merge", re.compile(r"\bawaiting\s+(?:the\s+)?merge\s+of\s+" + LIST, re.I), None),
    (HARD, "yet to merge", re.compile(
        r"\buntil\s+" + LIST + r"\s+(?:merges?|lands?|(?:is|are)\s+merged)\b", re.I), None),
    (HARD, "yet to merge", re.compile(
        r"\b(?:once|after)\s+" + LIST + r"\s+(?:merges?|lands?|(?:is|are)\s+merged)\b", re.I), None),
    (HARD, "not yet merged", re.compile(LIST + r"\s+(?:is|are)\s+not\s+yet\s+merged\b", re.I), None),
    (WARN, "a dependency", re.compile(r"\b(?:waiting|wait|waits)\s+(?:for|on)\s+" + LIST, re.I), None),
    (WARN, "a dependency", re.compile(r"\bawaiting\s+" + LIST, re.I), None),
    (WARN, "a dependency", re.compile(r"\bblocked\s+(?:on|by)\s+" + LIST, re.I), None),
    (WARN, "a stack base", re.compile(r"\b(?:stacked|stacks)\s+(?:on|atop)\s+" + LIST, re.I), None),
)
AMBIGUOUS_RES = (
    re.compile(r"\bpending\s+(?:PRs?|pull\s+requests?)\b(?!\s*\ue000)", re.I),
    re.compile(r"\b(?:the|this|that)\s+(?:PR|pull\s+request)\s+" + VERB + r"\s+(?:pending|open)\b", re.I),
)


def tokenize(prose):
    """(clauses, refs): the prose with each pull request reference replaced by a token, split into
    clauses; refs[k] = (repo or None for the surface default, number, original text)."""
    text = " ".join(prose.split())
    text = HISTORY_SPAN_RE.sub(" \u00a6 ", text)
    text = FORMATTING_RE.sub("", text)
    refs = []

    def replace(match):
        if match.group("un"):
            repo, number = "{}/{}".format(match.group("uo"), match.group("ur")), match.group("un")
        elif match.group("qn"):
            repo, number = "{}/{}".format(match.group("qo"), match.group("qr")), match.group("qn")
        else:
            repo, number = None, match.group("pn") or match.group("hn")
        number = int(number)
        if number < 1 or (repo is not None and not _pr_state.valid_repo(repo)):
            return match.group(0)
        refs.append((repo, number, match.group(0)))
        return "\ue000{}\ue001".format(len(refs) - 1)

    text = REF_RE.sub(replace, text)
    clauses = [clause.strip() for clause in CLAUSE_SPLIT_RE.split(text)]
    return [clause for clause in clauses if clause], refs


def _instruction_before(clause, start):
    words = re.findall(r"[A-Za-z]+", clause[:start])
    return bool(words) and words[-1].lower() in INSTRUCTION_WORDS


def find_candidates(prose, context):
    """[(kind, phrase, ref, clause text)] for one unit. A quote context reports any claim as QUOTE (it
    needs a historical lead-in or a move out of the quote); an ambiguous phrase is AMBIGUOUS."""
    clauses, refs = tokenize(prose)
    found = []
    for clause in clauses:
        bound = {}
        for kind, phrase, regex, contexts in PATTERNS:
            if contexts is not None and context not in contexts:
                continue
            for match in regex.finditer(clause):
                if phrase == "open" and regex.pattern.startswith("(?<!") and _instruction_before(
                        clause, match.start()):
                    continue
                for index in REF_MARK_RE.findall(match.group("l")):
                    index = int(index)
                    if index not in bound or (bound[index][0] == WARN and kind == HARD):
                        bound[index] = (kind, phrase)
        shown = REF_MARK_RE.sub(lambda m: refs[int(m.group(1))][2], clause)
        for index in sorted(bound):
            kind, phrase = bound[index]
            if context == "quote":
                kind = QUOTE
            found.append((kind, phrase, refs[index], shown))
        if not bound and any(regex.search(clause) for regex in AMBIGUOUS_RES):
            found.append((AMBIGUOUS, "pending or open", None, shown))
    return found


# ---------------------------------------------------------------------------------------------------
# Evaluation.

class Result:
    def __init__(self):
        self.hard, self.warnings, self.unknown, self.candidates = [], [], [], 0

    def exit_code(self):
        return 2 if self.unknown else (1 if self.hard else 0)


def collect(surfaces_with_bytes):
    """[(surface, location, kind, phrase, ref, clause)] over (surface, bytes) pairs; InputError
    propagates."""
    out = []
    for surface, data in surfaces_with_bytes:
        for location, prose, context in surface_units(surface, data):
            for kind, phrase, ref, clause in find_candidates(prose, context):
                out.append((surface, location, kind, phrase, ref, clause))
    return out


def evaluate(candidates, config, root, transport=None, git_runner=_pr_state.run_git_origin):
    """The Result of judging the candidates against live pull request state."""
    result = Result()
    result.candidates = len(candidates)
    origin_cache = {}
    keyed = []
    for surface, location, kind, phrase, ref, clause in candidates:
        if kind == AMBIGUOUS:
            result.unknown.append("{}: {!r}: a pending or open phrase whose pull request is not named; "
                                  "the binding is ambiguous".format(location, clause))
            continue
        if kind == QUOTE:
            result.unknown.append("{}: {!r}: a claim inside a blockquote with no historical lead-in "
                                  "(Earlier note: or Previously recorded:); review it".format(
                                      location, clause))
            continue
        repo, number, text = ref
        if repo is None:
            repo, why = _surface_repo(surface, root, origin_cache, git_runner)
            if repo is None:
                result.unknown.append("{}: {!r}: {} cannot be bound to a repository: {}".format(
                    location, clause, text, why))
                continue
        keyed.append(((repo, number), location, kind, phrase, clause))
    limits = config["limits"]
    states = _pr_state.read_states([key for key, *_rest in keyed], transport=transport,
                                   max_prs=limits["max-prs"],
                                   call_timeout=limits["call-timeout-seconds"],
                                   deadline=limits["deadline-seconds"]) if keyed else {}
    for (repo, number), location, kind, phrase, clause in keyed:
        state, why, when = states[(repo, number)]
        name = "{}#{}".format(repo, number)
        read = " (read {})".format(when) if when else ""
        if state == _pr_state.UNKNOWN:
            result.unknown.append("{}: {!r}: {} state cannot be evaluated: {}{}".format(
                location, clause, name, why, read))
        elif state != _pr_state.OPEN:
            line = "{}: {!r}: says {} is {}, but it is {}{}".format(location, clause, name, phrase,
                                                                     state, read)
            (result.hard if kind == HARD else result.warnings).append(line)
    return result


def _surface_repo(surface, root, cache, git_runner):
    if surface["repository"] != "origin":
        return surface["repository"], None
    if "origin" not in cache:
        cache["origin"] = _pr_state.origin_repo(root, git_runner)
    repo, why = cache["origin"]
    expected = surface.get("expected-repository")
    if repo is not None and expected is not None and repo.casefold() != expected.casefold():
        return None, "the origin names {}, not the declared expected repository {}".format(repo, expected)
    return repo, why


def report(result, stream=None):
    stream = stream or sys.stdout
    for line in result.hard:
        print("FINDING: " + line, file=stream)
    for line in result.warnings:
        print("WARNING: " + line, file=stream)
    for line in result.unknown:
        print("CANNOT EVALUATE: " + line, file=stream)
    code = result.exit_code()
    if code == 0:
        if result.candidates:
            print("PASS: {} candidate claim(s) evaluated; none contradicts its pull request's lifecycle"
                  "{}".format(result.candidates, " (advisory warnings above)" if result.warnings else ""),
                  file=stream)
        else:
            print("PASS: no supported current pending-claim found (this is not proof that none exists)",
                  file=stream)
    elif code == 2:
        print("INCOMPLETE: the evaluation did not complete; this is not a pass", file=stream)
    return code


# ---------------------------------------------------------------------------------------------------
# Modes.

def _declared(config, rel):
    for surface in config["surface"]:
        if surface["path"] == rel:
            return surface
    return None


def check_inputs(root):
    """--check-inputs: offline validation; returns the exit code."""
    try:
        config = load_config(root)
        pairs = [(s, _read_bytes(Path(root) / s["path"], s["path"])) for s in config["surface"]]
        candidates = collect(pairs)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    print("PASS: {} declared surface(s) parse; {} candidate claim(s) found. Offline input validation "
          "only: pull request currency was NOT assessed".format(len(pairs), len(candidates)))
    return 0


def run_all(root, transport=None, git_runner=_pr_state.run_git_origin):
    try:
        config = load_config(root)
        pairs = [(s, _read_bytes(Path(root) / s["path"], s["path"])) for s in config["surface"]]
        candidates = collect(pairs)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(evaluate(candidates, config, root, transport, git_runner))


def judge_draft_bytes(root, target, data, transport=None, git_runner=_pr_state.run_git_origin):
    """The exit code for complete proposed bytes of the declared surface target (a repo-relative path),
    with the findings printed. Used by --draft and by tools/apply_record_draft.py."""
    try:
        config = load_config(root)
        surface = _declared(config, target)
        if surface is None:
            raise InputError("{}: is not a declared surface in {}".format(target, CONFIG_REL))
        candidates = collect([(surface, data)])
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(evaluate(candidates, config, root, transport, git_runner))


def run_draft(root, draft, target, transport=None):
    try:
        data = _read_bytes(Path(draft), draft)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return judge_draft_bytes(root, target, data, transport)


STAGED_NAMES = ["diff", "--cached", "--name-only", "-z", "--no-renames"]


def _git(root, args):
    """(returncode, stdout bytes) of one of the two read-only index reads run_staged makes: the staged
    path list (args == STAGED_NAMES) or one staged blob (args == ["cat-file", "blob", ":PATH"]). Each
    launch is a literal argument list; any other args is a ValueError."""
    if args == STAGED_NAMES:
        child = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only", "-z",
                                "--no-renames"], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    elif len(args) == 3 and args[:2] == ["cat-file", "blob"] and args[2].startswith(":"):
        child = subprocess.run(["git", "-C", str(root), "cat-file", "blob", args[2]], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=60)
    else:
        raise ValueError("unsupported git read {!r}".format(args))
    return child.returncode, child.stdout


def run_staged(root, transport=None, git_runner=_pr_state.run_git_origin, git=_git):
    """--staged: judge the staged blob (complete content, unchanged context included) of every declared
    surface the index changes."""
    try:
        config = load_config(root)
        code, out = git(root, STAGED_NAMES)
        if code != 0:
            raise InputError("git diff --cached exited {}".format(code))
        staged = set(out.decode("utf-8", "surrogateescape").split("\0")) - {""}
        pairs = []
        for surface in config["surface"]:
            if surface["path"] not in staged:
                continue
            code, blob = git(root, ["cat-file", "blob", ":" + surface["path"]])
            if code != 0:
                raise InputError("{}: the staged blob cannot be read (git exited {})".format(
                    surface["path"], code))
            if len(blob) > MAX_BYTES:
                raise InputError("{}: staged blob larger than {} bytes".format(surface["path"], MAX_BYTES))
            pairs.append((surface, blob))
        candidates = collect(pairs)
    except (InputError, OSError, subprocess.SubprocessError) as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    if not pairs:
        print("PASS: no declared record surface is staged; nothing to judge")
        return 0
    return report(evaluate(candidates, config, root, transport, git_runner))


# ---------------------------------------------------------------------------------------------------
# Self-test (synthetic lifecycle responses only; no network, no real gh).

SUITE_ID = "pending-pr-claims-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


REPO = "acme/widgets"


def _response(number, state, merged_at=None, repo=REPO, **extra):
    import json
    obj = {"number": number, "state": state, "merged_at": merged_at,
           "html_url": "https://github.com/{}/pull/{}".format(repo, number),
           "base": {"repo": {"full_name": repo}}}
    obj.update(extra)
    return json.dumps(obj).encode("utf-8")


MERGED_AT = "2026-11-01T10:00:00Z"
LIFECYCLE = {"open": ("open", None), "merged": ("closed", MERGED_AT), "closed": ("closed", None)}


class FakeTransport:
    """In-process stand-in for gh: {(repo, number): lifecycle name or (code, stdout, stderr)}."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, repo, number, timeout):
        self.calls.append((repo, number))
        entry = self.table.get((repo, number), (1, b"", b"gh: Not Found (HTTP 404)"))
        if isinstance(entry, str):
            state, merged_at = LIFECYCLE[entry]
            return 0, _response(number, state, merged_at, repo), b""
        if isinstance(entry, BaseException):
            raise entry
        return entry


def _origin_ok(_root):
    return 0, ("https://github.com/" + REPO + ".git\n").encode()


MD_SURFACE = {"path": "NOTES.md", "format": "markdown", "repository": "origin", "role": "source",
              "status-column": "Status"}
TOML_SURFACE = {"path": "changes.toml", "format": "toml-strings", "repository": "origin", "role": "source"}
CONFIG = {"schema-version": 1, "limits": {"max-prs": 100, "call-timeout-seconds": 10,
                                          "deadline-seconds": 120}, "surface": [MD_SURFACE, TOML_SURFACE]}


def _judge(text, table, surface=MD_SURFACE, config=CONFIG, git_runner=_origin_ok):
    """(exit code, Result, transport) for one synthetic surface body."""
    transport = FakeTransport(table)
    candidates = collect([(surface, text.encode("utf-8"))])
    result = evaluate(candidates, config, "/nonexistent", transport, git_runner)
    return result.exit_code(), result, transport


def _numbers(lines):
    return sorted(int(n) for line in lines for n in re.findall(r"#([0-9]+)", line.split(": says ")[-1]
                                                                 .split(" is ")[0]))


def _with_rules_removed(kinds, fn):
    """Run fn() with every PATTERNS row of the given phrases removed (the red-when-reverted leg)."""
    global PATTERNS
    saved = PATTERNS
    PATTERNS = tuple(row for row in saved if row[1] not in kinds)
    try:
        return fn()
    finally:
        PATTERNS = saved


def _self_test():
    import contextlib
    import io
    import tempfile
    import time
    m = {(REPO, 17): "merged"}
    c = {(REPO, 17): "closed"}
    o = {(REPO, 17): "open"}

    # --- lifecycle outcomes for one hard claim ---
    code, res, _t = _judge("PR #17 remains pending.\n", m)
    check("state/merged-hard-finding", code == 1 and len(res.hard) == 1 and "MERGED" in res.hard[0], res.hard)
    red = _with_rules_removed({"pending"}, lambda: _judge("PR #17 remains pending.\n", m)[0])
    check("state/merged-hard-finding/red-when-reverted", red == 0, red)
    code, res, _t = _judge("PR #17 remains pending.\n", c)
    check("state/closed-hard-finding-names-closed", code == 1 and "CLOSED" in res.hard[0]
          and "MERGED" not in res.hard[0], res.hard)
    code, res, _t = _judge("PR #17 remains pending.\n", o)
    check("state/open-passes", code == 0 and not res.hard and not res.unknown, (code, res.hard))
    code, res, _t = _judge("PR #17 remains pending.\n", {})
    check("state/unknown-404-cannot-evaluate", code == 2 and not res.hard and "404" in res.unknown[0],
          res.unknown)

    # --- reference forms ---
    for name, text in (("ref/pr-colon", "Fix is pending pr:17.\n"), ("ref/pr-colon-hash", "Fix is pending pr:#17.\n"),
                       ("ref/pull-request-words", "pull request #17 is still pending\n"),
                       ("ref/github-url", "https://github.com/acme/widgets/pull/17 is pending\n")):
        code, _res, t = _judge(text, m)
        check(name, code == 1 and t.calls == [(REPO, 17)], (code, t.calls))
    code, _res, t = _judge("PR 17 remains pending\n", m)
    check("ref/bare-number-not-a-reference", code == 0 and t.calls == [], (code, t.calls))
    code, _res, t = _judge("peer/repo#17 is pending\n", {("peer/repo", 17): "merged", (REPO, 17): "open"})
    check("ref/explicit-repository-never-local", code == 1 and t.calls == [("peer/repo", 17)], t.calls)

    # --- batch and lists ---
    text = "This is stacked on the pending batch (#17, #18).\n"
    code, res, _t = _judge(text, {(REPO, 17): "closed", (REPO, 18): "closed"})
    check("batch/both-closed-two-findings", code == 1 and _numbers(res.hard) == [17, 18], res.hard)
    red = _with_rules_removed({"a pending batch member"},
                              lambda: _judge(text, {(REPO, 17): "closed", (REPO, 18): "closed"})[1])
    check("batch/both-closed-two-findings/red-when-reverted", not red.hard, red.hard)
    code, res, _t = _judge(text, {(REPO, 17): "open", (REPO, 18): "merged"})
    check("batch/only-stale-member", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, t = _judge("#17 merged; #18 remains pending\n", {(REPO, 17): "merged", (REPO, 18): "merged"})
    check("bind/semicolon-binds-only-second", code == 1 and t.calls == [(REPO, 18)], t.calls)
    code, res, t = _judge("#17 and #18 remain open\n", {(REPO, 17): "merged", (REPO, 18): "closed"})
    check("bind/coordinated-list-binds-both", code == 1 and _numbers(res.hard) == [17, 18], res.hard)
    red = _with_rules_removed({"open"}, lambda: _judge("#17 and #18 remain open\n",
                                                       {(REPO, 17): "merged", (REPO, 18): "closed"})[0])
    check("bind/coordinated-list-binds-both/red-when-reverted", red == 0, red)
    code, res, _t = _judge("The open pull requests #17 and #18 carry it.\n",
                           {(REPO, 17): "merged", (REPO, 18): "open"})
    check("bind/open-noun-phrase", code == 1 and _numbers(res.hard) == [17], res.hard)
    code, _res, t = _judge("Leave open #17 for the maintainer.\n", m)
    check("bind/instruction-not-a-claim", code == 0 and t.calls == [], t.calls)

    # --- warnings versus hard findings ---
    code, res, _t = _judge("This branch is stacked on #17.\n", m)
    check("warn/stacked-merged-is-warning", code == 0 and len(res.warnings) == 1 and not res.hard,
          (code, res.warnings))
    red = _with_rules_removed({"a stack base"}, lambda: _judge("This branch is stacked on #17.\n", m)[1])
    check("warn/stacked-merged-is-warning/red-when-reverted", not red.warnings, red.warnings)
    code, res, _t = _judge("Still waiting on #17.\n", c)
    check("warn/waiting-on-closed-is-warning", code == 0 and len(res.warnings) == 1, res.warnings)
    code, res, _t = _judge("Waiting for #17 to merge.\n", c)
    check("hard/waiting-to-merge-closed", code == 1 and len(res.hard) == 1 and not res.warnings,
          (res.hard, res.warnings))
    red = _with_rules_removed({"yet to merge"}, lambda: _judge("Waiting for #17 to merge.\n", c)[1])
    check("hard/waiting-to-merge-closed/red-when-reverted", not red.hard and len(red.warnings) == 1,
          (red.hard, red.warnings))
    code, res, _t = _judge("#17 is not yet merged\n", m)
    check("hard/not-yet-merged", code == 1, res.hard)
    code, res, _t = _judge("PR #17 is under review\n", c)
    check("hard/under-review", code == 1, res.hard)

    # --- the TODO Status cell ---
    table = "| ID | Status | Item |\n| --- | --- | --- |\n| X-1 | In progress (#17, #18) | Do it |\n"
    code, res, t = _judge(table, {(REPO, 17): "open", (REPO, 18): "merged"})
    check("status/in-progress-binds-both", code == 1 and sorted(t.calls) == [(REPO, 17), (REPO, 18)]
          and _numbers(res.hard) == [18], (t.calls, res.hard))
    red = _with_rules_removed({"in progress (an open pull request under review)"},
                              lambda: _judge(table, {(REPO, 17): "open", (REPO, 18): "merged"})[0])
    check("status/in-progress-binds-both/red-when-reverted", red == 0, red)
    code, _res, t = _judge("| ID | Status | Item |\n| --- | --- | --- |\n| X-1 | Planned | In progress (#17) |\n", m)
    check("status/only-the-status-column", code == 0 and t.calls == [], t.calls)
    code, _res, t = _judge("- [ ] Tidy the widget (fixed by #17)\n", m)
    check("checkbox/open-checkbox-no-claim", code == 0 and t.calls == [], t.calls)

    # --- what must not trigger ---
    for name, text in (
            ("quiet/deployment-pending-after-merge", "Deployment is pending after #17 merged.\n"),
            ("quiet/waiting-for-deployment", "Waiting for deployment of #17.\n"),
            ("quiet/no-longer-pending", "#17 is no longer pending.\n"),
            ("quiet/unmerged-closed", "#17 is unmerged.\n"),
            ("quiet/past-tense", "#17 was pending before the merge.\n"),
            ("quiet/earlier-attempt", "An earlier attempt, closed pull request #17, was not merged.\n"),
            ("quiet/no-cross-sentence", "The release is pending. #17 merged the fix.\n"),
            ("quiet/history-quoted-span", "Previously recorded: \u201cPR #17 is pending.\u201d\n"),
            ("quiet/history-backtick-span", "The old note read `waiting on #17`.\n"),
            ("quiet/history-blockquote", "Earlier note:\n\n> waiting for #17 to merge\n"),
            ("quiet/fenced-example", "```\nPR #17 is pending\n```\n")):
        code, res, t = _judge(text, {(REPO, 17): "closed"})
        check(name, code == 0 and t.calls == [] and not res.hard and not res.warnings, (code, t.calls))
    saved = HISTORY_SPAN_RE

    def _no_history():
        globals()["HISTORY_SPAN_RE"] = re.compile(r"(?!x)x")
        try:
            return _judge("Previously recorded: \u201cPR #17 is pending.\u201d\n", c)[0]
        finally:
            globals()["HISTORY_SPAN_RE"] = saved
    red = _no_history()
    check("quiet/history-quoted-span/red-when-reverted", red == 1, red)

    code, res, _t = _judge("Previously recorded: \u201cPR #17 is pending.\u201d Now PR #18 remains open.\n",
                           {(REPO, 18): "merged"})
    check("history/current-assertion-after-quote", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, _t = _judge("#17 was pending; #18 remains pending\n", {(REPO, 18): "merged"})
    check("history/only-current-clause", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, _t = _judge("PR `#17` is \u201cpending\u201d.\n", m)
    check("format/inline-formatting-does-not-hide", code == 1, res.hard)
    code, res, _t = _judge("Previously blocked; #17 remains pending\n", m)
    check("format/previously-does-not-exempt-row", code == 1, res.hard)
    code, res, _t = _judge("> PR #17 is pending\n", m)
    check("quote/bare-blockquote-incomplete", code == 2 and not res.hard and "blockquote" in res.unknown[0],
          res.unknown)
    code, res, _t = _judge("The pending PR still needs review.\n", m)
    check("ambiguous/unnamed-target-incomplete", code == 2 and "ambiguous" in res.unknown[0], res.unknown)

    # --- TOML decoding and wrapped prose ---
    toml_text = 'items = ["""Fix is\\u0020waiting for #17\n   to merge."""]\n'
    code, res, _t = _judge(toml_text, c, surface=TOML_SURFACE)
    check("toml/escaped-wrapped-string-decoded", code == 1, (code, res.hard))
    code, res, _t = _judge("Fix is waiting for\n#17 to merge, as noted.\n", c)
    check("markdown/wrapped-paragraph-joined", code == 1, (code, res.hard))
    try:
        _judge("items = [\n", c, surface=TOML_SURFACE)
        got = None
    except InputError as exc:
        got = str(exc)
    check("toml/malformed-input-error", got is not None and "not valid TOML" in got, got)

    # --- deduplication ---
    code, res, t = _judge("PR #17 remains pending. PR #17 is still open.\n", m)
    check("dedupe/one-read-shared-result", t.calls == [(REPO, 17)] and len(res.hard) == 2, (t.calls, res.hard))

    # --- the reader's validation ---
    for name, body in (
        ("reader/wrong-number", _response(18, "open")),
        ("reader/wrong-repository", _response(17, "open", repo="peer/repo")),
        ("reader/invalid-timestamp", _response(17, "closed", "2026-11-01 10:00:00")),
        ("reader/open-with-merge-time", _response(17, "open", MERGED_AT)),
        ("reader/missing-merged-at", b'{"number": 17, "state": "closed", "html_url": '
                                     b'"https://github.com/acme/widgets/pull/17", '
                                     b'"base": {"repo": {"full_name": "acme/widgets"}}}'),
        ("reader/invalid-json", b"{not json"),
        ("reader/boolean-number", _response(17, "open").replace(b'"number": 17', b'"number": true')),
        ("reader/merged-flag-contradiction", _response(17, "closed", MERGED_AT, merged=False)),
        ("reader/unknown-state", _response(17, "draft")),
        ("reader/two-objects", _response(17, "open") + _response(17, "open")),
    ):
        code, res, _t = _judge("PR #17 remains pending\n", {(REPO, 17): (0, body, b"")})
        check(name, code == 2 and not res.hard, (code, res.unknown))
    for name, entry in (("reader/denied-403", (1, b"", b"gh: Forbidden (HTTP 403)")),
                        ("reader/timeout", subprocess.TimeoutExpired(["gh"], 1)),
                        ("reader/launch-failure", FileNotFoundError(2, "gh"))):
        code, res, _t = _judge("PR #17 remains pending\n", {(REPO, 17): entry})
        check(name, code == 2 and not res.hard, (code, res.unknown))
    code, res, t = _judge("PR #17 remains pending\n", {(REPO, 17): (1, b"", b"gh: HTTP 502")})
    check("reader/transient-retried-once", code == 2 and len(t.calls) == 2, t.calls)
    many = " ".join("#{} is pending.".format(n) for n in range(1, 4)) + "\n"
    small = dict(CONFIG, limits={"max-prs": 2, "call-timeout-seconds": 10, "deadline-seconds": 120})
    code, res, t = _judge(many, {(REPO, n): "open" for n in range(1, 4)}, config=small)
    check("bound/request-limit-names-unread", code == 2 and len(t.calls) == 2
          and "request limit" in " ".join(res.unknown), res.unknown)
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0])
    states = _pr_state.read_states([(REPO, 1), (REPO, 2)], FakeTransport({(REPO, 1): "open", (REPO, 2): "open"}),
                                   deadline=120, clock=lambda: next(ticks))
    check("bound/deadline-names-unread", states[(REPO, 1)][0] == "OPEN"
          and states[(REPO, 2)][0] == "UNKNOWN" and "deadline" in states[(REPO, 2)][1], states)
    code, res, t = _judge("Train subject (#17) shipped. PR #17 remains pending.\n", o)
    check("reader/direct-state-not-subject", code == 0, (code, res.hard))
    code, res, _t = _judge("PR #17 remains pending. PR #18 remains pending.\n",
                           {(REPO, 17): "merged", (REPO, 18): (1, b"", b"HTTP 404")})
    check("result/hard-and-unknown-both-reported", code == 2 and len(res.hard) == 1 and len(res.unknown) == 1,
          (res.hard, res.unknown))

    # --- origin binding ---
    for name, runner in (("origin/missing", lambda _r: (2, b"")),
                         ("origin/unsupported-host", lambda _r: (0, b"https://gitlab.com/acme/widgets\n")),
                         ("origin/two-urls", lambda _r: (0, b"https://github.com/a/b\nhttps://github.com/c/d\n")),
                         ("origin/credential-url", lambda _r: (0, b"https://tok@github.com/acme/widgets\n"))):
        code, res, t = _judge("PR #17 remains pending\n", m, git_runner=runner)
        check(name, code == 2 and t.calls == [] and "tok" not in " ".join(res.unknown), res.unknown)
    pinned = dict(MD_SURFACE, **{"expected-repository": "other/widgets"})
    code, res, t = _judge("PR #17 remains pending\n", m, surface=pinned)
    check("origin/expected-repository-mismatch", code == 2 and t.calls == [], res.unknown)
    for name, url in (("origin/accepted-form/scp", "git@github.com:acme/widgets.git"),
                      ("origin/accepted-form/ssh", "ssh://git@github.com/acme/widgets"),
                      ("origin/accepted-form/https", "https://github.com/acme/widgets")):
        check(name, _pr_state.repo_from_url(url) == REPO, url)

    # --- declaration validation and declared files ---
    for name, mutate in (("config/unknown-top-key", lambda d: d.update(extra=1)),
                         ("config/bad-format", lambda d: d["surface"][0].update(format="html")),
                         ("config/escaping-path", lambda d: d["surface"][0].update(path="../x.md")),
                         ("config/limit-out-of-range", lambda d: d["limits"].update({"max-prs": 0}))):
        data = {"schema-version": 1, "limits": dict(CONFIG["limits"]),
                "surface": [dict(MD_SURFACE), dict(TOML_SURFACE)]}
        mutate(data)
        try:
            validate_config(data)
            got = None
        except InputError as exc:
            got = str(exc)
        check(name, got is not None, got)
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-") as tmp:
        try:
            _read_bytes(Path(tmp) / "absent.md", "absent.md")
            got = None
        except InputError as exc:
            got = str(exc)
        check("input/unreadable-declared-file", got is not None and "cannot be read" in got, got)

    # --- real processes: a stub gh executable behind the production transport ---
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-gh-") as tmp:
        stub = Path(tmp) / "gh"
        stub.write_text(
            "#!{} -I\n"
            "import os, sys, signal, time\n"
            "mode = open(os.path.join(os.path.dirname(sys.argv[0]), 'mode')).read()\n"
            "assert sys.argv[1:5] == ['api', '--hostname', 'github.com', '-H'], sys.argv\n"
            "assert 'GH_HOST' not in os.environ\n"
            "if mode == 'merged':\n"
            "    sys.stdout.write({!r})\n"
            "elif mode == 'signal':\n"
            "    os.kill(os.getpid(), signal.SIGTERM)\n"
            "elif mode == 'exit3':\n"
            "    sys.exit(3)\n"
            "elif mode == 'slow':\n"
            "    time.sleep(30)\n".format(sys.executable, _response(17, "closed", MERGED_AT).decode()))
        os.chmod(stub, 0o700)
        transport = _pr_state.GhTransport(search_path=tmp)
        os.environ["GH_HOST"] = "evil.example"
        try:
            for name, mode, want in (("process/stub-gh-merged", "merged", 1),
                                     ("process/signal-termination", "signal", 2),
                                     ("process/unexpected-exit", "exit3", 2)):
                (Path(tmp) / "mode").write_text(mode)
                candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
                got = evaluate(candidates, CONFIG, "/nonexistent", transport, _origin_ok).exit_code()
                check(name, got == want, got)
            (Path(tmp) / "mode").write_text("slow")
            quick = dict(CONFIG, limits={"max-prs": 100, "call-timeout-seconds": 1, "deadline-seconds": 3})
            began = time.monotonic()
            candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
            got = evaluate(candidates, quick, "/nonexistent", transport, _origin_ok)
            check("process/timeout-bounded", got.exit_code() == 2 and time.monotonic() - began < 15,
                  got.unknown)
        finally:
            del os.environ["GH_HOST"]
        missing = _pr_state.GhTransport(search_path=str(Path(tmp) / "absent-dir"))
        candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
        got = evaluate(candidates, CONFIG, "/nonexistent", missing, _origin_ok).exit_code()
        check("process/launch-failure", got == 2, got)

    # --- staged content, draft application, commit adapter ---
    _self_test_adapters(tempfile, contextlib, io)

    got = [_main_exit(argv) for argv in (["--bogus"], [], ["--all"], ["--draft", "x"],
                                         ["--execution-report", "relative/report"])]
    check("cli/unrecognized-arguments-exit-2", got == [2, 2, 2, 2, 2], got)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        got = check_inputs(ROOT)
    check("live/real-tree-inputs-valid", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_pending_pr_claims {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_pending_pr_claims {} checks (merged and closed pull requests under a "
          "pending, open, in-progress or yet-to-merge claim are findings, an open one passes, an "
          "unreadable state is cannot-evaluate, stacking and dependency wording warn, historical, "
          "fenced, negated, past-tense and cross-sentence text does not trigger, and each "
          "red-when-reverted row fails with its rule removed)".format(len(EXECUTED)))
    return 0


def _self_test_adapters(tempfile, contextlib, io):
    import apply_record_draft
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-stage-") as tmp:
        root = Path(tmp)
        os.makedirs(root / ".aiqt")
        (root / CONFIG_REL).write_text(
            'schema-version = 1\n[limits]\nmax-prs = 100\ncall-timeout-seconds = 10\ndeadline-seconds = 120\n'
            '[[surface]]\npath = "NOTES.md"\nformat = "markdown"\nrepository = "origin"\nrole = "source"\n')
        (root / "NOTES.md").write_text("PR #17 remains pending.\n")
        blobs = {":NOTES.md": b"Nothing current here.\n"}

        def fake_git(_root, args):
            if args[:2] == ["diff", "--cached"]:
                return 0, b"NOTES.md\0"
            return (0, blobs[args[2]]) if args[2] in blobs else (128, b"")

        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/uses-staged-bytes", got == 0, (got, sink.getvalue()))
        blobs[":NOTES.md"] = b"Fix is waiting for\n#17 to merge.\n"
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/complete-unit-evaluated", got == 1, got)

        target = root / "NOTES.md"
        before = target.read_bytes()
        for name, body, table, want in (
                ("apply/withheld-on-finding", b"PR #17 remains pending.\n", {(REPO, 17): "merged"}, 1),
                ("apply/withheld-on-unknown", b"PR #17 remains pending.\n", {}, 2)):
            draft = root / "draft.md"
            draft.write_bytes(body)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport(table), _origin_ok)
            check(name, got == want and target.read_bytes() == before, got)
        draft = root / "draft.md"
        draft.write_bytes(b"PR #17 remains pending.\n")

        def mutate():
            target.write_bytes(b"changed underneath\n")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok, after_judge=mutate)
        check("apply/destination-changed-withheld", got == 2
              and target.read_bytes() == b"changed underneath\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/clean-draft-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "OTHER.md", FakeTransport({}), _origin_ok)
        check("apply/undeclared-target-withheld", got == 2 and not (root / "OTHER.md").exists(), got)

    adapter = ROOT / "tools" / "check_pending_pr_commit.sh"
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-hook-") as tmp:
        bindir = Path(tmp)
        (bindir / "git").write_text("#!/bin/sh\nprintf '%s\\n' \"" + tmp + "\"\n")
        os.makedirs(bindir / "tools")
        (bindir / "tools" / "check_pending_pr_claims.py").write_text("")
        for name, code, needle in (("commit/advisory-continues-on-incomplete", 2, "INCOMPLETE"),
                                   ("commit/advisory-continues-on-finding", 1, "ADVISORY")):
            (bindir / "python3").write_text("#!/bin/sh\nexit {}\n".format(code))
            for exe in ("git", "python3"):
                os.chmod(bindir / exe, 0o700)
            child = subprocess.run(["/bin/sh", str(adapter)], capture_output=True, stdin=subprocess.DEVNULL,
                                    env={"PATH": str(bindir)}, timeout=60)
            check(name, child.returncode == 0 and needle in child.stderr.decode("utf-8", "replace"),
                  (child.returncode, child.stderr))


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if argv == ["--check-inputs"]:
        return check_inputs(ROOT)
    if argv == ["--all", "--live"]:
        return run_all(ROOT)
    if argv == ["--staged", "--live"]:
        return run_staged(ROOT)
    if len(argv) == 4 and argv[0] == "--draft" and argv[2] == "--target":
        return run_draft(ROOT, argv[1], argv[3])
    print("error: unrecognized arguments {!r}; expected --check-inputs, --all --live, --staged --live, "
          "--draft PATH --target PATH or --self-test; fail closed".format(argv), file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The live modes start gh and git and read
# their exit status (subprocess), so under an ignored SIGCHLD a failing read would read as a pass; the
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
    sys.exit(_child_status_refusal_exit("check_pending_pr_claims.py") or main())
