#!/usr/bin/env python3
"""Brief-claims lint: a dispatch brief's restatements of named review files must match those files.
Offline, stdlib only, fail-closed.

A brief often restates what a named review file says: its verdict, a finding's id or grade, a count. When
that restatement is carried over from text written about ANOTHER file (the template the brief was built
from, the previous round, the other reviewer), the brief asserts what a file contains without having read
it (read-before-characterizing, rdbchr) and hands a worker an unsound input (guard-input-soundness,
grdinp). This lint binds each restatement to the file it describes and compares it with that file.

The lint is ADOPTER-CONFIGURED: it ships NO family, file name, verdict or grade of its own. The adopter
authors `.aiqt/brief-claims.toml`. With NO configuration the scan prints NOT APPLICABLE and exits 0; a
present but unreadable or malformed configuration is cannot-evaluate (exit 2), never a clean pass.

Configuration schema (`.aiqt/brief-claims.toml`), every key REQUIRED:

  format-version = 1                  the exact integer 1 (true and 1.0 do not pass)
  families = ["alpha", "beta"]        reviewer family words (unique, case-insensitive)
  review-file-pattern = '...'         a regex FULL-matched against a file's basename, with the named
                                      groups item, round (digits) and family
  verdicts = ["NO BLOCKERS", ...]     the exact verdict tokens (case-sensitive)
  grades = ["BLOCKER", "MAJOR", ...]  finding grade words (case-insensitive)
  grade-labels = ["Grade"]            label words of the "Label: GRADE" finding form
  verbatim-begin = "BEGIN VERBATIM"   a line starting with this opens an embedded verbatim review; the
                                      first family word after it is the section's declared family
  verbatim-end = "END VERBATIM"       a line starting with this closes it
  regrade-token = "REGRADED"          a claim segment carrying it is exempt from the verdict check (logged)
  [aliases.<family>]                  per-family shorthand: M = "MAJOR" lets "M3" mean that family's MAJOR 3
                                      (the table may be empty; an undeclared alias is cannot-evaluate)

Optional registry (`--registry PATH`, TOML, every key required): format-version = 1, target = "ITEM-7",
aliases = [...], dependencies = [...], id-pattern = '...' (the item id shape). With it, an item id in the
brief's own text that is not the target, a declared alias or a declared dependency is a MISMATCH.

  check_brief_claims.py                                    validate the configuration (CI form)
  check_brief_claims.py --input-dir DIR [--template PATH] [--registry PATH] [--strict] BRIEF...
  check_brief_claims.py --self-test                        deterministic self-test (tempdir layer)

WHAT IT CHECKS. The brief's own text excludes fenced blocks, `>` lines, double-quoted strings and
declared verbatim sections. A named file is a token whose basename full-matches the review-file pattern,
or an absolute path under --input-dir; each must resolve inside --input-dir to a readable UTF-8 regular
file (a similarly named sibling is never substituted). A balanced parenthetical directly after a named
file is split at family words: the text before the first family word binds to that file, each family's
segment to that family's NAMED file of the same item and round ("from round N" rebinds to round N). A
family word followed by claims elsewhere in the brief's own text binds to the only named file of that
family. Claims: exact verdict tokens (against the source's single operative `VERDICT:` line), finding ids
(GRADE-n, GRADE n, ranges, declared aliases), "GRADE:" (at least one finding at that grade), "n GRADEs"
(the graded-finding count), and "N word" counts whose source predicate differs. A claim segment that is a
verbatim excerpt of its bound file passes; an excerpt of a different named file only is a misattribution.
Embedded verbatim sections are attributed to the named file holding a majority of their long lines, whose
family and verdict must equal the section's. With --template, a parenthetical identical to the template's
but attached to a different file, and an identical title naming a round the named inputs have moved past,
are mismatches.

TIERS. Blocking: unresolved or unreadable inputs, verdicts, misattributed excerpts, embedded attribution,
template carry-over, foreign ids. Advisory (printed WARN or CANNOT-EVALUATE, blocking only under
--strict): ids, grades, counts, and claims with no resolvable binding.

WHAT IT DOES NOT PROVE (class c, partial). It sees only the claim shapes above, inside one line; a claim
in another wording, a multi-line parenthetical, a binding through a file it cannot name, and a predicate
count whose source wording it cannot pair are outside it. Recall and the true-negative rate are not
measured. A MATCH says the restated token occurs in the bound file, not that the brief is right.

Exit convention (matches the repo's gates):
  0  clean, NOT APPLICABLE, or advisory results only (without --strict)
  1  a blocking MISMATCH (or, under --strict, an advisory WARN)
  2  cannot evaluate: a missing, unreadable, non-UTF-8, directory, or escaping named file; an unreadable
     brief, template or registry; a malformed configuration or registry; a blocking claim the lint cannot
     evaluate (or, under --strict, any cannot-evaluate). An input it cannot read never reads as clean.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_brief_claims.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import hashlib
import os
import re
import stat
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    sys.stderr.write(
        "error: check_brief_claims.py cannot import tomllib, part of the Python standard library; "
        "this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

CONFIG_REL = ".aiqt/brief-claims.toml"
CONFIG_KEYS = frozenset(("format-version", "families", "review-file-pattern", "verdicts", "grades",
                         "grade-labels", "verbatim-begin", "verbatim-end", "regrade-token", "aliases"))
REGISTRY_KEYS = frozenset(("format-version", "target", "aliases", "dependencies", "id-pattern"))
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ALIAS_KEY_RE = re.compile(r"^[A-Z]{1,2}$")
TOKEN_RE = re.compile(r"[^\s()\[\]{}<>,;\"'`|*]+")
FROM_ROUND_RE = re.compile(r"\bfrom\s+round\s+(\d+)\b", re.I)
TITLE_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
ROUND_RE = re.compile(r"\bround\s+(\d+)\b", re.I)
PRED_RE = re.compile(r"(?<![\w,.-])(\d{1,3}(?:,\d{3})+|\d{2,})\s+([A-Za-z][A-Za-z-]*)")
SRC_WORD_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+|[A-Za-z][A-Za-z-]*")
LONG_LINE = 40       # an embedded line this long (stripped) votes on the section's source
MIN_EXCERPT = 24     # a claim segment this long may pass as a verbatim excerpt of its bound file
MAX_RANGE = 50       # a wider id range is cannot-evaluate, never expanded
MATCH, MISMATCH, CANNOT, WARN, EXEMPT = "MATCH", "MISMATCH", "CANNOT-EVALUATE", "WARN", "EXEMPT"
BLOCKING, ADVISORY = "blocking", "advisory"


class GateError(Exception):
    """An input the lint cannot read, parse or resolve: reported as exit 2 (fail-closed)."""


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path, what):
    """Read a UTF-8 regular file fail-closed: missing, a directory, unreadable or non-UTF-8 is a GateError."""
    try:
        st = os.stat(path)
    except OSError as exc:
        raise GateError("{} {} cannot be read ({})".format(what, path, exc.strerror or exc))
    if not stat.S_ISREG(st.st_mode):
        raise GateError("{} {} is not a regular file".format(what, path))
    try:
        data = _read_bytes(path)
    except OSError as exc:
        raise GateError("{} {} cannot be read ({})".format(what, path, exc.strerror or exc))
    try:
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except UnicodeDecodeError:
        raise GateError("{} {} is not valid UTF-8".format(what, path))


def _load_toml(path, what):
    text, _ = read_text(path, what)
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise GateError("{} {} is malformed TOML ({})".format(what, path, exc))


def _keys(data, wanted, what):
    missing = sorted(wanted - set(data))
    extra = sorted(set(data) - wanted)
    if missing or extra:
        raise GateError("{}: missing key(s) {} / unknown key(s) {}".format(what, missing, extra))
    version = data["format-version"]
    if type(version) is not int or version != 1:
        raise GateError("{}: format-version must be the integer 1, got {!r}".format(what, version))


def _str(data, key, what):
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise GateError("{}: {!r} must be a non-empty string".format(what, key))
    return value.strip()


def _str_list(data, key, what, allow_empty=False, shape=None):
    value = data[key]
    if not isinstance(value, list) or (not value and not allow_empty) or not all(
            isinstance(x, str) and x.strip() for x in value):
        raise GateError("{}: {!r} must be a {}list of non-empty strings".format(
            what, key, "" if allow_empty else "non-empty "))
    items = [x.strip() for x in value]
    folded = [x.casefold() for x in items]
    if len(set(folded)) != len(folded):
        raise GateError("{}: {!r} has a duplicate entry".format(what, key))
    if shape is not None and not all(shape.match(x) for x in items):
        raise GateError("{}: {!r} has an entry outside the shape {}".format(what, key, shape.pattern))
    return items


def _alt(words):
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def load_config(path):
    """Parse and validate the adopter configuration; every defect is a GateError (exit 2)."""
    what = "configuration {}".format(CONFIG_REL)
    data = _load_toml(path, "configuration")
    _keys(data, CONFIG_KEYS, what)
    families = [f.casefold() for f in _str_list(data, "families", what, shape=WORD_RE)]
    try:
        pattern = re.compile(_str(data, "review-file-pattern", what))
    except re.error as exc:
        raise GateError("{}: review-file-pattern does not compile ({})".format(what, exc))
    if not {"item", "round", "family"} <= set(pattern.groupindex):
        raise GateError("{}: review-file-pattern must name the groups item, round and family".format(what))
    verdicts = _str_list(data, "verdicts", what)
    grades = [g.upper() for g in _str_list(data, "grades", what, shape=re.compile(r"^[A-Za-z]+$"))]
    labels = _str_list(data, "grade-labels", what, shape=re.compile(r"^[A-Za-z][A-Za-z ]*$"))
    aliases = data["aliases"]
    if not isinstance(aliases, dict):
        raise GateError("{}: aliases must be a table".format(what))
    alias_map = {}
    for fam, table in aliases.items():
        if fam.casefold() not in families or not isinstance(table, dict):
            raise GateError("{}: aliases.{} names no declared family or is not a table".format(what, fam))
        for key, grade in table.items():
            if not ALIAS_KEY_RE.match(key) or not isinstance(grade, str) or grade.upper() not in grades:
                raise GateError("{}: alias {}.{} must map one or two capitals to a declared grade".format(
                    what, fam, key))
        alias_map[fam.casefold()] = {k: v.upper() for k, v in table.items()}
    g = _alt(grades)
    return {
        "families": families,
        "pattern": pattern,
        "verdicts": sorted(verdicts, key=len, reverse=True),
        "grades": grades,
        "aliases": alias_map,
        "begin": _str(data, "verbatim-begin", what),
        "end": _str(data, "verbatim-end", what),
        "regrade": _str(data, "regrade-token", what),
        "family_re": re.compile(r"(?<![\w./-])(" + _alt(families) + r")(?![\w./-])", re.I),
        "numbered_re": re.compile(r"^\s*(\d+)[.)]\s+[*_\[]*\s*(" + g + r")\b", re.I),
        "heading_re": re.compile(r"^\s{0,3}#{1,6}\s+[*_]*(" + g + r")\b[*_]*[\s:-]*(?:n?(\d+)(?![\d.]))?",
                                 re.I),
        "label_re": re.compile(r"(?<!\w)(?:" + _alt(labels) + r")\s*:\s*[*_]*(" + g + r")\b", re.I),
        "count_re": re.compile(r"(?<![\w,.-])(\d+)\s+(" + g + r")(?:es|s)?(?![\w-])", re.I),
        "range_re": re.compile(r"(?<![\w.-])(" + g + r")[- ]?(\d+)\s*(?:-|to|through)\s*(?:(" + g
                               + r")[- ]?)?(\d+)(?![\w-]|\.\d)", re.I),
        "id_re": re.compile(r"(?<![\w.-])(" + g + r")[- ]?(\d+)(?![\w-]|\.\d)", re.I),
        "alias_re": re.compile(r"(?<![\w.-])([A-Z]{1,2})(\d+)(?:\s*(?:-|to)\s*\1(\d+))?(?![\w-]|\.\d)"),
        "colon_re": re.compile(r"(?<![\w-])(" + g + r"):", re.I),
    }


def load_registry(path):
    what = "registry {}".format(path)
    data = _load_toml(path, "registry")
    _keys(data, REGISTRY_KEYS, what)
    try:
        id_re = re.compile(r"(?<![\w-])(?:" + _str(data, "id-pattern", what) + r")(?![\w-])")
    except re.error as exc:
        raise GateError("{}: id-pattern does not compile ({})".format(what, exc))
    allowed = {_str(data, "target", what)}
    allowed.update(_str_list(data, "aliases", what, allow_empty=True))
    allowed.update(_str_list(data, "dependencies", what, allow_empty=True))
    return {"id_re": id_re, "allowed": allowed}


# --- text regions -----------------------------------------------------------------------------------

def _fence(stripped):
    m = re.match(r"(`{3,}|~{3,})", stripped)
    return m.group(1) if m else None


def _closes(stripped, fence):
    t = stripped.rstrip()
    return bool(t) and set(t) == {fence[0]} and len(t) >= len(fence)


def operative_lines(text):
    """A source file's lines outside fenced blocks and `>` quote lines, as (lineno, line)."""
    out, fence = [], None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if fence is not None:
            if _closes(s, fence):
                fence = None
            continue
        f = _fence(s)
        if f:
            fence = f
            continue
        if s.startswith(">"):
            continue
        out.append((n, line))
    return out


def _blank(m):
    return " " * len(m.group(0))


def brief_regions(text, cfg):
    """Split a brief into its own text (lineno, line with quoted strings and backticks blanked) and its
    declared verbatim sections. An unterminated section is a GateError (the brief cannot be read)."""
    own, sections, fence, sec = [], [], None, None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if sec is not None:
            if s.startswith(cfg["end"]):
                sections.append(sec)
                sec = None
            else:
                sec["lines"].append(line)
            continue
        if fence is not None:
            if _closes(line.lstrip(), fence):
                fence = None
            continue
        f = _fence(line.lstrip())
        if f:
            fence = f
            continue
        if s.startswith(">"):
            continue
        if s.startswith(cfg["begin"]):
            m = cfg["family_re"].search(s[len(cfg["begin"]):])
            sec = {"lineno": n, "family": m.group(1).casefold() if m else None, "lines": []}
            continue
        line = re.sub(r'"[^"]*"|“[^”]*”', _blank, line)
        own.append((n, line.replace("`", " ")))
    if sec is not None:
        raise GateError("verbatim section opened at brief line {} is never closed".format(sec["lineno"]))
    return own, sections


def _groups(cfg, base):
    m = cfg["pattern"].fullmatch(base)
    if m is None:
        return None
    rnd = m.group("round")
    return {"item": m.group("item"), "round": int(rnd) if rnd and rnd.isdigit() else None,
            "family": (m.group("family") or "").casefold()}


def named_refs(own, cfg, input_dir):
    """Every named-file token in the brief's own text: a basename full-matching the review-file pattern,
    or an absolute path under the input directory."""
    root = os.path.abspath(input_dir) if input_dir else None
    refs = []
    for n, line in own:
        for m in TOKEN_RE.finditer(line):
            tok = m.group(0).rstrip(".:!?")
            if not tok:
                continue
            base = tok.rsplit("/", 1)[-1]
            groups = _groups(cfg, base)
            under = bool(root) and tok.startswith(root + os.sep)
            if groups is None and not under:
                continue
            refs.append({"lineno": n, "start": m.start(), "end": m.start() + len(tok), "token": tok,
                         "base": base, "groups": groups})
    return refs


def resolve_named(token, input_dir):
    """Resolve one named file inside the input directory, fail-closed. Returns (path, text, sha256)."""
    root = os.path.realpath(input_dir)
    path = token if token.startswith("/") else os.path.join(input_dir, token)
    try:
        os.lstat(path)
    except FileNotFoundError:
        raise GateError("named file {} does not exist (a similarly named sibling is never "
                        "substituted)".format(token))
    except OSError as exc:
        raise GateError("named file {} cannot be examined ({})".format(token, exc.strerror or exc))
    real = os.path.realpath(path)
    if os.path.commonpath([real, root]) != root:
        raise GateError("named file {} resolves outside --input-dir".format(token))
    text, digest = read_text(path, "named file")
    return os.path.abspath(path), text, digest


def _balanced(line, i):
    depth = 0
    for j in range(i, len(line)):
        if line[j] == "(":
            depth += 1
        elif line[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def split_families(content, cfg):
    """Split a parenthetical at family words: [(family or None, segment text)]."""
    marks = list(cfg["family_re"].finditer(content))
    if not marks:
        return [(None, content)]
    out = []
    if content[:marks[0].start()].strip(" ,;:"):
        out.append((None, content[:marks[0].start()]))
    for k, m in enumerate(marks):
        stop = marks[k + 1].start() if k + 1 < len(marks) else len(content)
        out.append((m.group(1).casefold(), content[m.end():stop]))
    return out


# --- source analysis --------------------------------------------------------------------------------

def _verdict_class(value, cfg):
    for token in cfg["verdicts"]:
        if value == token or (value.startswith(token) and not re.match(r"[\w-]", value[len(token)])):
            return token
    return None


def source_verdicts(text, cfg):
    """The distinct verdict classes of a text's operative `VERDICT:` lines (outside fences and quotes);
    an unclassifiable value is kept raw so it still counts as distinct."""
    found = []
    for _, line in operative_lines(text):
        m = re.match(r"VERDICT:\s*(.*?)\s*$", line)
        if m and m.group(1):
            v = _verdict_class(m.group(1), cfg) or m.group(1)
            if v not in found:
                found.append(v)
    return found


def analyze_source(text, cfg):
    """Graded findings of a source file: numbered items, grade headings, and labelled lines (table rows
    skipped). Returns a dict: ids, the set of (grade, n); counts, findings per grade; text, the operative
    text."""
    findings, ordinal = [], dict()
    lines = operative_lines(text)
    for _, line in lines:
        if line.lstrip().startswith("|"):
            continue
        m = cfg["numbered_re"].match(line)
        if m:
            findings.append((m.group(2).upper(), int(m.group(1))))
            continue
        m = cfg["heading_re"].match(line)
        if m:
            findings.append((m.group(1).upper(), int(m.group(2)) if m.group(2) else None))
            continue
        m = cfg["label_re"].search(line)
        if m:
            findings.append((m.group(1).upper(), None))
    ids, counts = set(), dict()
    for grade, num in findings:
        counts[grade] = counts.get(grade, 0) + 1
        if num is None:
            ordinal[grade] = ordinal.get(grade, 0) + 1
            num = ordinal[grade]
        ids.add((grade, num))
    return dict(ids=ids, counts=counts, text="\n".join(l for _, l in lines))


def id_present(info, grade, num):
    if (grade, num) in info["ids"]:
        return True
    lit = re.compile(r"(?<![\w.-])" + re.escape(grade) + r"[- ]?" + str(num) + r"(?![\w-]|\.\d)", re.I)
    return bool(lit.search(info["text"]))


def predicate_check(num, pred, text):
    """(status, detail) for an "N word" claim, or None when the source does not pair either way."""
    toks = SRC_WORD_RE.findall(text)
    low = [t.lower() for t in toks]
    for i, t in enumerate(toks):
        if t.replace(",", "") == num and pred in low[i + 1:i + 4]:
            return MATCH, "source states {} {}".format(t, pred)
    for i, t in enumerate(low):
        if t == pred:
            for x in toks[max(0, i - 3):i]:
                if x[0].isdigit() and x.replace(",", "") != num:
                    return MISMATCH, "source binds {!r} to {}, not {}".format(pred, x, num)
    return None


# --- claims -----------------------------------------------------------------------------------------

def parse_claims(text, cfg):
    """Claims in one bound segment, in a fixed order with each match masked from the later passes."""
    claims = []
    buf = FROM_ROUND_RE.sub(_blank, text)

    def take(regex, kind):
        nonlocal buf
        for m in list(regex.finditer(buf)):
            claims.append((kind, m))
        buf = regex.sub(_blank, buf)

    for token in cfg["verdicts"]:
        take(re.compile(r"(?<![\w-])" + re.escape(token) + r"(?![\w-])"), "verdict")
    take(cfg["count_re"], "count")
    take(cfg["range_re"], "range")
    take(cfg["id_re"], "id")
    take(cfg["alias_re"], "alias")
    take(cfg["colon_re"], "colon")
    for m in PRED_RE.finditer(buf):
        word = m.group(2).upper()
        if word in cfg["grades"] or word.rstrip("S") in cfg["grades"]:
            continue
        claims.append(("pred", m))
    return claims


def _res(status, tier, lineno, claim, src, detail):
    return dict(status=status, tier=tier, lineno=lineno, claim=claim, src=src, detail=detail)


def _ids_of(kind, m, fam, cfg):
    """Expand an id, range or alias claim to [(grade, n)], or raise ValueError(reason) (cannot-evaluate)."""
    if kind == "id":
        return [(m.group(1).upper(), int(m.group(2)))]
    if kind == "range":
        g1, g2 = m.group(1).upper(), (m.group(3) or m.group(1)).upper()
        lo, hi = int(m.group(2)), int(m.group(4))
        if g1 != g2 or hi < lo or hi - lo > MAX_RANGE:
            raise ValueError("range {!r} cannot be expanded".format(m.group(0)))
        return [(g1, n) for n in range(lo, hi + 1)]
    letters = m.group(1)
    grade = cfg["aliases"].get(fam or "", dict()).get(letters)
    if grade is None:
        raise ValueError("alias {!r} is not declared for family {!r}".format(letters, fam))
    lo = int(m.group(2))
    hi = int(m.group(3)) if m.group(3) else lo
    if hi < lo or hi - lo > MAX_RANGE:
        raise ValueError("range {!r} cannot be expanded".format(m.group(0)))
    return [(grade, n) for n in range(lo, hi + 1)]


def _judge_verdict(claim, f, cfg, lineno, src, regrade):
    if regrade:
        return _res(EXEMPT, BLOCKING, lineno, claim, src,
                    "regrade disclosed ({}); verdict not compared".format(cfg["regrade"]))
    found = source_verdicts(f["text"], cfg)
    if not found:
        return _res(CANNOT, BLOCKING, lineno, claim, src, "source has no operative VERDICT")
    if len(found) > 1:
        return _res(CANNOT, BLOCKING, lineno, claim, src, "source has distinct operative VERDICTs {}".format(found))
    if found[0] == claim:
        return _res(MATCH, BLOCKING, lineno, claim, src, "VERDICT: " + found[0])
    return _res(MISMATCH, BLOCKING, lineno, claim, src, "source VERDICT is {!r}".format(found[0]))


def judge_segment(seg, files, cfg, infos):
    """Results for one claim segment. seg: lineno, text, token (bound file or None), why (if unbound)."""
    out = []
    n = seg["lineno"]
    claims = parse_claims(seg["text"], cfg)
    if seg["token"] is None:
        for kind, m in claims:
            out.append(_res(CANNOT, ADVISORY, n, m.group(0).strip(), None, "no resolvable binding: " + seg["why"]))
        return out
    f = files[seg["token"]]
    src = (f["path"], f["sha"])
    excerpt = seg["text"].strip(" ,;:")
    if len(excerpt) >= MIN_EXCERPT:
        if excerpt in f["text"]:
            return [_res(MATCH, BLOCKING, n, excerpt, src, "verbatim excerpt of its bound file")]
        others = sorted(o["path"] for t, o in files.items() if t != seg["token"] and excerpt in o["text"])
        if others:
            return [_res(MISMATCH, BLOCKING, n, excerpt, src,
                         "misattributed: a verbatim excerpt of {}, not of its bound file".format(others[0]))]
    if not claims:
        return out
    info = infos[seg["token"]]
    fam = (f["groups"] or dict()).get("family")
    regrade = cfg["regrade"] in seg["text"]
    for kind, m in claims:
        claim = m.group(0).strip()
        if kind == "verdict":
            out.append(_judge_verdict(claim, f, cfg, n, src, regrade))
        elif kind in ("id", "range", "alias"):
            try:
                wanted = _ids_of(kind, m, fam, cfg)
            except ValueError as exc:
                out.append(_res(CANNOT, ADVISORY, n, claim, src, str(exc)))
                continue
            for grade, num in wanted:
                label = "{}-{}".format(grade, num)
                ok = id_present(info, grade, num)
                out.append(_res(MATCH if ok else MISMATCH, ADVISORY, n, "id " + label, src,
                                "present" if ok else "no finding {} in the source".format(label)))
        elif kind == "colon":
            have = info["counts"].get(m.group(1).upper(), 0)
            out.append(_res(MATCH if have else MISMATCH, ADVISORY, n, claim, src,
                            "{} {} finding(s) in the source".format(have, m.group(1).upper())))
        elif kind == "count":
            grade, num = m.group(2).upper(), int(m.group(1))
            have = info["counts"].get(grade, 0)
            out.append(_res(MATCH if have == num else MISMATCH, ADVISORY, n, claim, src,
                            "the source grades {} finding(s) {}".format(have, grade)))
        else:
            verdict = predicate_check(m.group(1).replace(",", ""), m.group(2).lower(), info["text"])
            if verdict is not None:
                out.append(_res(verdict[0], ADVISORY, n, claim, src, verdict[1]))
    return out


def _paren_after(line, end):
    """(open index, close index or None) of a parenthetical directly after position end, or None."""
    i = end
    while i < len(line) and line[i] in " \t":
        i += 1
    if i >= len(line) or line[i] != "(":
        return None
    return i, _balanced(line, i)


def collect_segments(own, refs, cfg):
    """Bind claim segments to named files. Returns (segments, bound parentheticals [(ref, content)])."""
    segs, parens, used, by_key = [], [], dict(), dict()
    for ref in refs:
        g = ref["groups"]
        if g is not None:
            by_key.setdefault((g["item"], g["round"], g["family"]), ref["token"])
    lines = dict(own)
    for ref in refs:
        line = lines[ref["lineno"]]
        found = _paren_after(line, ref["end"])
        if found is None:
            continue
        i, j = found
        if j is None:
            segs.append(dict(lineno=ref["lineno"], text=line[i + 1:], token=None,
                             why="unbalanced parenthetical after {}".format(ref["token"])))
            used.setdefault(ref["lineno"], []).append((i, len(line)))
            continue
        content = line[i + 1:j]
        used.setdefault(ref["lineno"], []).append((i, j + 1))
        parens.append((ref, content))
        for fam, text in split_families(content, cfg):
            g = ref["groups"]
            if fam is None:
                segs.append(dict(lineno=ref["lineno"], text=text, token=ref["token"], why=""))
            elif g is None:
                segs.append(dict(lineno=ref["lineno"], text=text, token=None,
                                 why="{} carries no item and round for family {}".format(ref["token"], fam)))
            else:
                m = FROM_ROUND_RE.search(text)
                rnd = int(m.group(1)) if m else g["round"]
                segs.append(dict(lineno=ref["lineno"], text=text, token=by_key.get((g["item"], rnd, fam)),
                                 why="no named {} file for item {} round {}".format(fam, g["item"], rnd)))
    # Family-attributed claims elsewhere in the brief's own text bind to the only named file of the family.
    for n, line in own:
        spans = list(used.get(n, [])) + [(r["start"], r["end"]) for r in refs if r["lineno"] == n]
        for a, b in spans:
            line = line[:a] + " " * (b - a) + line[b:]
        marks = list(cfg["family_re"].finditer(line))
        for k, m in enumerate(marks):
            stop = marks[k + 1].start() if k + 1 < len(marks) else len(line)
            end = re.search(r"[.;](?:\s|$)", line[m.end():stop])
            text = line[m.end():m.end() + end.start()] if end else line[m.end():stop]
            fam = m.group(1).casefold()
            named = sorted(set(r["token"] for r in refs if r["groups"] and r["groups"]["family"] == fam))
            segs.append(dict(lineno=n, text=text, token=named[0] if len(named) == 1 else None,
                             why="{} named {} file(s)".format(len(named), fam)))
    return segs, parens


def check_sections(sections, files, cfg):
    out = []
    for sec in sections:
        longs = [l.strip() for l in sec["lines"]
                 if len(l.strip()) >= LONG_LINE and not l.strip().startswith("VERDICT:")]
        best = None
        for token in sorted(files):
            hits = sum(1 for l in longs if l in files[token]["text"])
            if longs and hits * 2 > len(longs) and (best is None or hits > best[1]):
                best = (token, hits)
        n, claim = sec["lineno"], "embedded section ({})".format(sec["family"])
        if best is None:
            out.append(_res(CANNOT, BLOCKING, n, claim, None, "no named file holds a majority of its long lines"))
            continue
        f = files[best[0]]
        src = (f["path"], f["sha"])
        sfam = (f["groups"] or dict()).get("family")
        if sec["family"] is None or sfam is None:
            out.append(_res(CANNOT, BLOCKING, n, claim, src, "family not determinable"))
        elif sfam != sec["family"]:
            out.append(_res(MISMATCH, BLOCKING, n, claim, src, "the source is family {!r}".format(sfam)))
        else:
            out.append(_res(MATCH, BLOCKING, n, claim, src, "family attribution"))
        embedded = source_verdicts("\n".join(sec["lines"]), cfg)
        if embedded:
            real = source_verdicts(f["text"], cfg)
            label = "embedded VERDICT: " + embedded[0]
            if len(real) != 1 or len(embedded) != 1:
                out.append(_res(CANNOT, BLOCKING, n, label, src, "not exactly one operative VERDICT on each side"))
            elif real[0] != embedded[0]:
                out.append(_res(MISMATCH, BLOCKING, n, label, src, "source VERDICT is {!r}".format(real[0])))
            else:
                out.append(_res(MATCH, BLOCKING, n, label, src, "verdict"))
    return out


def _title(own):
    for n, line in own:
        m = TITLE_RE.match(line)
        if m:
            return n, " ".join(m.group(1).split())
    return None, None


def check_template(own, refs, parens, files, cfg, template_text):
    out = []
    t_own, _ = brief_regions(template_text, cfg)
    t_lines = dict(t_own)
    t_parens = []
    for ref in named_refs(t_own, cfg, None):
        found = _paren_after(t_lines[ref["lineno"]], ref["end"])
        if found is not None and found[1] is not None:
            t_parens.append((ref["base"], " ".join(t_lines[ref["lineno"]][found[0] + 1:found[1]].split())))
    for ref, content in parens:
        norm = " ".join(content.split())
        moved = sorted(set(b for b, c in t_parens if c == norm and b != ref["base"]))
        f = files[ref["token"]]
        if moved:
            out.append(_res(MISMATCH, BLOCKING, ref["lineno"], "({})".format(norm), (f["path"], f["sha"]),
                            "carried over: the template attaches this parenthetical to {}".format(moved[0])))
    n, title = _title(own)
    if title is not None and title == _title(t_own)[1]:
        m = ROUND_RE.search(title)
        rounds = set(r["groups"]["round"] for r in refs if r["groups"] and r["groups"]["round"] is not None)
        if m and rounds and int(m.group(1)) not in rounds and max(rounds) > int(m.group(1)):
            out.append(_res(MISMATCH, BLOCKING, n, title, None,
                            "title carried over from the template names round {}; the named inputs are "
                            "round {}".format(m.group(1), max(rounds))))
    return out


def check_foreign(own, registry):
    out = []
    for n, line in own:
        for ident in sorted(set(m.group(0) for m in registry["id_re"].finditer(line))):
            if ident not in registry["allowed"]:
                out.append(_res(MISMATCH, BLOCKING, n, ident, None,
                                "foreign item id: not the target, a declared alias or a declared dependency"))
    return out


def _shown(r):
    return WARN if r["status"] == MISMATCH and r["tier"] == ADVISORY else r["status"]


def _rc(results, strict):
    rc = 0
    for r in results:
        hard = r["tier"] == BLOCKING or strict
        if r["status"] == CANNOT and hard:
            rc = 2
        elif r["status"] == MISMATCH and hard:
            rc = max(rc, 1)
    return rc


def check_brief(path, cfg, opts, template_text, registry):
    text, digest = read_text(path, "brief")
    own, sections = brief_regions(text, cfg)
    refs = named_refs(own, cfg, opts["input_dir"])
    files, errors = dict(), []
    for ref in refs:
        if ref["token"] in files:
            continue
        try:
            p, t, d = resolve_named(ref["token"], opts["input_dir"])
        except GateError as exc:
            errors.append("brief line {}: {}".format(ref["lineno"], exc))
            continue
        files[ref["token"]] = dict(path=p, text=t, sha=d, groups=ref["groups"])
    print("BRIEF {} sha256={}".format(path, digest))
    if errors:
        for e in errors:
            print("CANNOT-EVALUATE [blocking] {}; fail-closed".format(e))
        print("brief-claims: {}: {} named file(s) could not be read; exit 2".format(path, len(errors)))
        return 2
    for token in sorted(files):
        print("INPUT {} sha256={}".format(files[token]["path"], files[token]["sha"]))
    infos = dict((t, analyze_source(f["text"], cfg)) for t, f in files.items())
    segs, parens = collect_segments(own, refs, cfg)
    results = []
    for seg in segs:
        results.extend(judge_segment(seg, files, cfg, infos))
    results.extend(check_sections(sections, files, cfg))
    if template_text is not None:
        results.extend(check_template(own, refs, parens, files, cfg, template_text))
    if registry is not None:
        results.extend(check_foreign(own, registry))
    else:
        print("foreign-id: not evaluated (no --registry)")
    tally = dict()
    for r in results:
        src = "{} sha256={}".format(*r["src"]) if r["src"] else "(no source)"
        print("{} [{}] brief line {}: {} -> {}: {}".format(_shown(r), r["tier"], r["lineno"], r["claim"], src,
                                                           r["detail"]))
        tally[_shown(r)] = tally.get(_shown(r), 0) + 1
    rc = _rc(results, opts["strict"])
    print("brief-claims: {}: {} result(s) {}; exit {}".format(
        path, len(results), ", ".join("{} {}".format(v, k) for k, v in sorted(tally.items())) or "none", rc))
    return rc


def run(root, opts):
    """Check the named briefs under the adopter configuration at root. Returns the exit code 0/1/2."""
    config_path = Path(root) / CONFIG_REL
    try:
        try:
            st = os.lstat(config_path)
        except FileNotFoundError:
            print("NOT APPLICABLE: no {} configuration; the brief-claims lint is adopter-configured and "
                  "inert without one ({} brief(s) named, none checked)".format(CONFIG_REL, len(opts["briefs"])))
            return 0
        except OSError as exc:
            raise GateError("configuration {} cannot be examined ({})".format(CONFIG_REL, exc.strerror or exc))
        if stat.S_ISLNK(st.st_mode):
            raise GateError("configuration {} is a symlink; it is not followed".format(CONFIG_REL))
        cfg = load_config(config_path)
        if not opts["briefs"]:
            print("PASS: configuration {} is valid; no brief named, nothing to check".format(CONFIG_REL))
            return 0
        if opts["input_dir"] is None or not os.path.isdir(opts["input_dir"]):
            raise GateError("--input-dir must name an existing directory when a brief is named")
        template_text = read_text(opts["template"], "template")[0] if opts["template"] else None
        registry = load_registry(opts["registry"]) if opts["registry"] else None
        rc = 0
        for brief in opts["briefs"]:
            rc = max(rc, check_brief(brief, cfg, opts, template_text, registry))
        return rc
    except GateError as exc:
        print("CANNOT-EVALUATE: {}; fail-closed".format(exc))
        return 2


def parse_args(argv):
    opts = dict(self_test=False, strict=False, input_dir=None, template=None, registry=None, briefs=[])
    it = iter(argv)
    for arg in it:
        if arg == "--self-test":
            opts["self_test"] = True
        elif arg == "--strict":
            opts["strict"] = True
        elif arg in ("--input-dir", "--template", "--registry"):
            value = next(it, None)
            if value is None:
                return None
            opts[arg[2:].replace("-", "_")] = value
        elif arg.startswith("-"):
            return None
        else:
            opts["briefs"].append(arg)
    return opts


# --- self-test --------------------------------------------------------------------------------------
# Every vector runs the real run() over a synthetic tempdir tree. Placeholder families, items and files
# only. No wall clock, no randomness, no network.

GOOD_CFG = """format-version = 1
families = ["alpha", "beta"]
review-file-pattern = '(?P<item>[a-z0-9]+)-r(?P<round>[0-9]+)-(?P<family>[a-z]+)\\.txt'
verdicts = ["NO BLOCKERS", "BLOCKERS FOUND"]
grades = ["BLOCKER", "MAJOR", "MEDIUM", "MINOR"]
grade-labels = ["Grade", "Defect"]
verbatim-begin = "BEGIN VERBATIM"
verbatim-end = "END VERBATIM"
regrade-token = "REGRADED"

[aliases.alpha]
M = "MAJOR"

[aliases.beta]
M = "MEDIUM"
"""

ALPHA5 = """Review of item seven, round five, by the first reviewer family.
1. **Blocker** - the parser drops the trailing record when the input ends without a newline.
2. **Major** - the retry loop never backs off between attempts on a busy server.
3. **Major** - the cache key ignores the locale and returns stale rows.
### MEDIUM 1
The error message names the wrong flag.
VERDICT: BLOCKERS FOUND
"""

BETA5 = """Review of item seven, round five, by the second reviewer family.
### MEDIUM 1
The help text omits the default value of the timeout option entirely.
### MEDIUM 2
A log line repeats the same word.
### MINOR n1
Typo.
### MINOR n2
Another typo.
VERDICT: NO BLOCKERS
"""


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data.encode("utf-8") if isinstance(data, str) else data)


def _quiet(root, argv):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = run(root, parse_args(argv))
    return rc, buf.getvalue()


def _scenario(base, name, brief, files=None, cfg=GOOD_CFG, args=(), extra=None):
    """Build base/name with a configuration, an input dir and a brief; run; return (rc, output)."""
    root = os.path.join(base, name)
    indir = os.path.join(root, "in")
    os.makedirs(indir)
    if cfg is not None:
        _write(os.path.join(root, CONFIG_REL), cfg)
    if files is None:
        files = dict([("it7-r5-alpha.txt", ALPHA5), ("it7-r5-beta.txt", BETA5)])
    for rel, data in files.items():
        _write(os.path.join(indir, rel), data)
    for rel, data in (extra or dict()).items():
        _write(os.path.join(root, rel), data)
    brief_path = os.path.join(root, "brief.md")
    if brief is not None:
        _write(brief_path, brief.replace("@IN@", indir))
    argv = [a.replace("@ROOT@", root) for a in args]
    return _quiet(root, ["--input-dir", indir] + argv + [brief_path])



def _cases_inputs(sc, check, skipped, base):
    """Plan step 2: configuration, inputs and exit codes."""
    os.makedirs(os.path.join(base, "nocfg"))
    rc, out = _quiet(os.path.join(base, "nocfg"), [])
    check("cfg/absent-not-applicable", rc == 0 and "NOT APPLICABLE" in out, out)
    bad = [("cfg/version-float", GOOD_CFG.replace("format-version = 1", "format-version = 1.0")),
           ("cfg/version-bool", GOOD_CFG.replace("format-version = 1", "format-version = true")),
           ("cfg/missing-key", GOOD_CFG.replace('regrade-token = "REGRADED"\n', "")),
           ("cfg/duplicate-family", GOOD_CFG.replace('"alpha", "beta"]', '"alpha", "beta", "Alpha"]')),
           ("cfg/empty-vocabulary", GOOD_CFG.replace('["NO BLOCKERS", "BLOCKERS FOUND"]', "[]")),
           ("cfg/pattern-groups", GOOD_CFG.replace("(?P<family>[a-z]+)", "([a-z]+)")),
           ("cfg/malformed-toml", GOOD_CFG + "[[[\n"),
           ("cfg/alias-undeclared-grade", GOOD_CFG.replace('M = "MAJOR"', 'M = "SEVERE"'))]
    for name, text in bad:
        rc, out = sc(name.replace("/", "_"), "Nothing named.\n", cfg=text)
        check(name, rc == 2, out)
    rc, out = sc("cfg_valid", "Nothing named here.\n")
    check("cfg/valid-no-claims", rc == 0, out)
    root = os.path.join(base, "cfglink")
    _write(os.path.join(root, "real.toml"), GOOD_CFG)
    os.makedirs(os.path.join(root, ".aiqt"))
    os.symlink(os.path.join(root, "real.toml"), os.path.join(root, CONFIG_REL))
    check("cfg/symlink", _quiet(root, [])[0] == 2)
    root = os.path.join(base, "noindir")
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
    _write(os.path.join(root, "b.md"), "x\n")
    check("in/missing-input-dir", _quiet(root, [os.path.join(root, "b.md")])[0] == 2)
    rc, out = sc("abs_missing", "Read @IN@/notes/summary.md first.\n")
    check("in/missing-absolute-path", rc == 2 and "does not exist" in out, out)
    opened = []
    me = sys.modules[__name__]
    real_read = me._read_bytes
    me._read_bytes = lambda p: opened.append(os.path.basename(p)) or real_read(p)
    try:
        rc, out = sc("failed_sibling", "Inputs: it7-r5-alpha.txt (NO BLOCKERS).\n",
                     files=dict([("it7-r5-alpha-FAILED.txt", ALPHA5)]))
    finally:
        me._read_bytes = real_read
    check("in/failed-sibling-not-substituted", rc == 2 and "it7-r5-alpha-FAILED.txt" not in opened, out)
    if os.geteuid() != 0:
        root = os.path.join(base, "mode000")
        p = os.path.join(root, "in")
        _write(os.path.join(p, "it7-r5-alpha.txt"), ALPHA5)
        _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
        _write(os.path.join(root, "b.md"), "Inputs: it7-r5-alpha.txt.\n")
        os.chmod(os.path.join(p, "it7-r5-alpha.txt"), 0)
        try:
            rc0 = _quiet(root, ["--input-dir", p, os.path.join(root, "b.md")])[0]
        finally:
            os.chmod(os.path.join(p, "it7-r5-alpha.txt"), 0o644)
        check("in/mode-000", rc0 == 2)
    else:
        skipped.append("in/mode-000 (running as root, permissions do not bind)")
    rc, out = sc("non_utf8", "Inputs: it7-r5-alpha.txt.\n", files=dict([("it7-r5-alpha.txt", b"\xff\xfe x")]))
    check("in/non-utf8", rc == 2, out)
    rc, out = sc("directory", "Inputs: it7-r5-alpha.txt.\n", files=dict([("it7-r5-alpha.txt/x", "x")]))
    check("in/directory", rc == 2 and "not a regular file" in out, out)
    outside = os.path.join(base, "outside.txt")
    _write(outside, ALPHA5)
    root = os.path.join(base, "symesc")
    indir = os.path.join(root, "in")
    os.makedirs(indir)
    os.symlink(outside, os.path.join(indir, "it7-r5-alpha.txt"))
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
    _write(os.path.join(root, "b.md"), "Inputs: it7-r5-alpha.txt (BLOCKERS FOUND).\n")
    check("in/symlink-escape", _quiet(root, ["--input-dir", indir, os.path.join(root, "b.md")])[0] == 2)
    rc, out = sc("no_brief", None)
    check("in/unreadable-brief", rc == 2, out)
    rc, out = sc("unterminated", "BEGIN VERBATIM alpha\nline\n")
    check("in/unterminated-verbatim", rc == 2, out)



def _cases_claims(sc, check, base):
    """Plan steps 3 to 6: binding, verdicts, ids and grades, counts."""
    rc, out = sc("bind_list", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (alpha MEDIUM-1; beta NO BLOCKERS)\n",
                 args=["--strict"])
    check("bind/list-family-segment", rc == 0 and "id MEDIUM-1 -> " + os.path.join(
        base, "bind_list", "in", "it7-r5-alpha.txt") in out, out)
    r9 = dict([("it7-r9-alpha.txt", ALPHA5), ("it7-r8-beta.txt", BETA5.replace("### MINOR n2", "### MINOR 7"))])
    brief = "Inputs: it7-r9-alpha.txt (alpha MAJOR-2, beta MINOR-7 from round 8)\n"
    rc, out = sc("bind_round_unnamed", brief, files=r9)
    check("bind/from-round-unnamed", rc == 0 and "CANNOT-EVALUATE [advisory]" in out and "round 8" in out, out)
    rc, out = sc("bind_round_unnamed_strict", brief, files=r9, args=["--strict"])
    check("bind/from-round-unnamed-strict", rc == 2, out)
    rc, out = sc("bind_round_named", "Inputs: it7-r8-beta.txt and " + brief[8:], files=r9, args=["--strict"])
    check("bind/from-round-named", rc == 0 and "MATCH [advisory] brief line 1: id MINOR-7" in out, out)
    hidden = [("bind/fence-not-extracted", "```\nit7-r5-alpha.txt (NO BLOCKERS)\n```\nit7-r5-alpha.txt\n"),
              ("bind/quote-line-not-extracted", "> it7-r5-alpha.txt (NO BLOCKERS)\nit7-r5-alpha.txt\n"),
              ("bind/dquote-not-extracted", 'See it7-r5-alpha.txt "alpha NO BLOCKERS".\n'),
              ("bind/verbatim-not-extracted", "it7-r5-alpha.txt\nBEGIN VERBATIM alpha\nalpha NO BLOCKERS\n"
               "2. **Major** - the retry loop never backs off between attempts on a busy server.\nEND VERBATIM\n")]
    for name, text in hidden:
        rc, out = sc(name.replace("/", "_").replace("-", "_"), text, args=["--strict"])
        check(name, rc == 0, out)
    rc, out = sc("nested", "Inputs: it7-r5-alpha.txt (see (the log) for MAJOR-3 and BLOCKERS FOUND)\n")
    check("bind/nested-parens", rc == 0 and "MATCH [advisory] brief line 1: id MAJOR-3" in out
          and "MATCH [blocking] brief line 1: BLOCKERS FOUND" in out, out)
    rc, out = sc("prose_family", "Fix the alpha MEDIUM-1 finding; it7-r5-alpha.txt is the source.\n",
                 args=["--strict"])
    check("bind/prose-family-claim", rc == 0 and "MATCH [advisory] brief line 1: id MEDIUM-1" in out, out)
    rc, out = sc("prose_ambiguous", "Fix alpha MAJOR-2 now. Inputs: it7-r5-alpha.txt, it7-r4-alpha.txt.\n",
                 files=dict([("it7-r5-alpha.txt", ALPHA5), ("it7-r4-alpha.txt", ALPHA5)]))
    check("bind/prose-family-ambiguous", rc == 0 and "CANNOT-EVALUATE [advisory]" in out, out)

    rc, out = sc("v_mismatch", "Inputs: it7-r5-alpha.txt (NO BLOCKERS)\n")
    check("verdict/mismatch", rc == 1 and "MISMATCH [blocking]" in out, out)
    rc, out = sc("v_match", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n")
    check("verdict/match", rc == 0 and "MATCH [blocking]" in out, out)
    rc, out = sc("v_none", "Inputs: it7-r5-alpha.txt (NO BLOCKERS)\n",
                 files=dict([("it7-r5-alpha.txt", ALPHA5.replace("VERDICT: BLOCKERS FOUND\n", ""))]))
    check("verdict/none", rc == 2, out)
    fenced = "```\nVERDICT: BLOCKERS FOUND\n```\n> VERDICT: BLOCKERS FOUND\n" + BETA5
    rc, out = sc("v_fenced", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n", files=dict([("it7-r5-beta.txt", fenced)]))
    check("verdict/fenced-and-quoted-ignored", rc == 0, out)
    rc, out = sc("src_quoted_id", "Inputs: it7-r5-alpha.txt (MAJOR 9)\n",
                 files=dict([("it7-r5-alpha.txt", "> MAJOR 9 was quoted from elsewhere.\n" + ALPHA5)]))
    check("src/quoted-id-ignored", "WARN [advisory] brief line 1: id MAJOR-9" in out, out)
    rc, out = sc("v_two", "Inputs: it7-r5-beta.txt (NO BLOCKERS)\n",
                 files=dict([("it7-r5-beta.txt", BETA5 + "VERDICT: BLOCKERS FOUND\n")]))
    check("verdict/two-distinct", rc == 2, out)
    rc, out = sc("v_severity", "Inputs: it7-r5-alpha.txt (no BLOCKER-grade finding remains)\n", args=["--strict"])
    check("verdict/severity-wording-not-a-claim", rc == 0 and "result(s) none" in out, out)
    rc, out = sc("v_regrade", "Inputs: it7-r5-alpha.txt (NO BLOCKERS, REGRADED after fix)\n")
    check("verdict/regrade-exempt-logged", rc == 0 and "EXEMPT [blocking]" in out, out)


    blob = BETA5.replace("### MEDIUM 1\n", "### NOTE\naGVsbG8M1d29ybGQ=\n")
    rc, out = sc("id_base64", "Inputs: it7-r5-beta.txt (beta M1)\n", files=dict([("it7-r5-beta.txt", blob)]))
    check("id/base64-substring-absent", "WARN [advisory] brief line 1: id MEDIUM-1" in out, out)
    rc, out = sc("id_whole", "Inputs: it7-r5-beta.txt (MEDIUM-1)\n",
                 files=dict([("it7-r5-beta.txt", "Notes on MEDIUM-12 only.\nVERDICT: NO BLOCKERS\n")]))
    check("id/whole-token", "WARN [advisory] brief line 1: id MEDIUM-1" in out, out)
    rc, out = sc("id_alias", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (alpha M3; beta M1)\n", args=["--strict"])
    check("id/alias-per-family", rc == 0 and "id MAJOR-3" in out and "id MEDIUM-1" in out, out)
    rc, out = sc("id_alias_undeclared", "Inputs: it7-r5-alpha.txt (alpha X2)\n")
    check("id/undeclared-alias", rc == 0 and "CANNOT-EVALUATE [advisory]" in out and "not declared" in out, out)
    rc, out = sc("id_minor_n", "Inputs: it7-r5-beta.txt (2 MINOR, MINOR-2)\n", args=["--strict"])
    check("id/minor-n-headings", rc == 0 and "MATCH [advisory] brief line 1: 2 MINOR" in out, out)
    rc, out = sc("id_section", "Inputs: it7-r5-alpha.txt (see MEDIUM 16.1)\n", args=["--strict"])
    check("id/section-number-not-id", rc == 0 and "result(s) none" in out, out)
    rc, out = sc("id_absent", "Inputs: it7-r5-alpha.txt (MAJOR 9)\n")
    rc2 = sc("id_absent_strict", "Inputs: it7-r5-alpha.txt (MAJOR 9)\n", args=["--strict"])[0]
    check("id/absent-injected", rc == 0 and rc2 == 1 and "WARN [advisory]" in out, out)
    rc, out = sc("id_wrong_grade", "Inputs: it7-r5-alpha.txt (alpha blocker 2)\n")
    check("id/wrong-grade-item", "WARN [advisory] brief line 1: id BLOCKER-2" in out, out)
    rc, out = sc("id_colon", "Inputs: it7-r5-beta.txt (beta MAJOR: the timeout)\n")
    check("id/grade-colon-absent", "WARN [advisory] brief line 1: MAJOR:" in out, out)
    rc, out = sc("id_range", "Inputs: it7-r5-beta.txt (MINOR-1 to MINOR-3)\n")
    check("id/range-expands", "MATCH [advisory] brief line 1: id MINOR-2" in out
          and "WARN [advisory] brief line 1: id MINOR-3" in out, out)
    rc, out = sc("id_alias_range", "Inputs: it7-r5-alpha.txt (alpha M2-M4)\n")
    check("id/alias-range-expands", "MATCH [advisory] brief line 1: id MAJOR-3" in out
          and "WARN [advisory] brief line 1: id MAJOR-4" in out, out)

    seven = dict([("it7-r5-alpha.txt", "1. **Blocker** - a.\n2. **Blocker** - b.\n3. **Major** - c.\n"
                   "4. **Major** - d.\n5. **Major** - e.\n6. **Major** - f.\n7. **Major** - g.\n"
                   "VERDICT: BLOCKERS FOUND\n")])
    rc, out = sc("count_warn", "Inputs: it7-r5-alpha.txt (2 blockers, 4 majors)\n", files=seven)
    rc2 = sc("count_warn_strict", "Inputs: it7-r5-alpha.txt (2 blockers, 4 majors)\n", files=seven,
             args=["--strict"])[0]
    check("count/grade-count-warn", rc == 0 and rc2 == 1 and "WARN [advisory] brief line 1: 4 majors" in out
          and "MATCH [advisory] brief line 1: 2 blockers" in out, out)
    pred = dict([("it7-r5-beta.txt", "Across 16,000 seeded mutations, 2,642 were accepted by the grammar.\n"
                  "VERDICT: NO BLOCKERS\n")])
    rc, out = sc("count_pred", "Inputs: it7-r5-beta.txt (16,000 accepted mutations)\n", files=pred)
    check("count/predicate-mismatch", "WARN [advisory] brief line 1: 16,000 accepted" in out, out)
    rc, out = sc("count_pred_ok", "Inputs: it7-r5-beta.txt (2,642 were accepted)\n", files=pred, args=["--strict"])
    check("count/predicate-match", rc == 0 and "MATCH [advisory]" in out, out)
    forms = dict([("it7-r5-beta.txt", "Item one. Grade: MAJOR\nItem two. Defect: MAJOR\n"
                   "| Grade: MAJOR | table |\nVERDICT: NO BLOCKERS\n")])
    rc, out = sc("count_forms", "Inputs: it7-r5-beta.txt (2 MAJORs)\n", files=forms, args=["--strict"])
    check("count/label-forms-counted-table-skipped", rc == 0 and "MATCH [advisory] brief line 1: 2 MAJORs" in out,
          out)
    rc, out = sc("count_round", "Inputs: it7-r5-beta.txt (the round-7 minors stay open)\n", args=["--strict"])
    check("count/round-prefix-not-count", rc == 0 and "result(s) none" in out, out)



def _cases_template(sc, check):
    """Plan steps 7 to 9: template carry-over, foreign ids, embedded attribution, excerpts, report."""
    tmpl = dict([("tmpl.md", "# Fix item seven round 4\n"
                  "Inputs: it7-r4-alpha.txt (alpha MEDIUM-1 and MAJOR-2 remain open)\n")])
    targ = ["--template", "@ROOT@/tmpl.md"]
    rc, out = sc("t_reattach", "# Fix item seven round 5\n"
                 "Inputs: it7-r5-alpha.txt (alpha MEDIUM-1 and MAJOR-2 remain open)\n", extra=tmpl, args=targ)
    check("tmpl/parenthetical-reattached", rc == 1 and "carried over" in out, out)
    rc, out = sc("t_title", "# Fix item seven round 4\nInputs: it7-r5-alpha.txt.\n", extra=tmpl, args=targ)
    check("tmpl/title-round-carried", rc == 1 and "title carried over" in out, out)
    rc, out = sc("t_clean", "# Fix item seven round 5\nInputs: it7-r5-alpha.txt (alpha MEDIUM-1).\n",
                 extra=tmpl, args=targ)
    check("tmpl/clean", rc == 0, out)
    reg = dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\naliases = ["ITEM-7B"]\n'
                 'dependencies = ["ITEM-2"]\nid-pattern = \'ITEM-[0-9]+[A-Z]?\'\n')])
    rarg = ["--registry", "@ROOT@/reg.toml"]
    rc, out = sc("f_foreign", "TASK: finish ITEM-6 using it7-r5-alpha.txt.\n", extra=reg, args=rarg)
    check("tmpl/foreign-id", rc == 1 and "foreign item id" in out, out)
    rc, out = sc("f_clean", "TASK: finish ITEM-7 (alias ITEM-7B) after ITEM-2.\n> history: ITEM-6 was done\n",
                 extra=reg, args=rarg)
    check("tmpl/foreign-id-quoted-dependency-alias-clean", rc == 0, out)
    rc, out = sc("f_none", "TASK: finish ITEM-6.\n")
    check("tmpl/no-registry-not-evaluated", rc == 0 and "foreign-id: not evaluated" in out, out)
    rc, out = sc("t_unreadable", "x\n", args=["--template", "@ROOT@/absent.md"])
    check("tmpl/unreadable-template", rc == 2, out)
    rc, out = sc("r_unreadable", "x\n", args=["--registry", "@ROOT@/absent.toml"])
    check("tmpl/unreadable-registry", rc == 2, out)
    rc, out = sc("r_malformed", "x\n", extra=dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\n')]),
                 args=rarg)
    check("tmpl/malformed-registry", rc == 2, out)


    section = ("2. **Major** - the retry loop never backs off between attempts on a busy server.\n"
               "3. **Major** - the cache key ignores the locale and returns stale rows.\n")
    rc, out = sc("e_swapped", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM beta\n" + section + "END VERBATIM\n")
    check("emb/swapped-family", rc == 1 and "the source is family 'alpha'" in out, out)
    rc, out = sc("e_verdict", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n" + section
                 + "VERDICT: NO BLOCKERS\nEND VERBATIM\n")
    check("emb/verdict-differs", rc == 1 and "MISMATCH [blocking]" in out, out)
    rc, out = sc("e_ok", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n" + section
                 + "VERDICT: BLOCKERS FOUND\nEND VERBATIM\n", args=["--strict"])
    check("emb/attribution-match", rc == 0 and "MATCH [blocking]" in out, out)
    rc, out = sc("e_nosource", "Inputs: it7-r5-alpha.txt\nBEGIN VERBATIM alpha\n"
                 "This sentence appears in no named review file at all, anywhere.\nEND VERBATIM\n")
    check("emb/no-source", rc == 2, out)
    rc, out = sc("x_wrong", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (the cache key ignores the locale and returns stale rows)\n")
    check("emb/excerpt-wrong-file", rc == 1 and "misattributed" in out, out)
    rc, out = sc("x_own", "Inputs: it7-r5-alpha.txt (the retry loop never backs off between attempts)\n",
                 args=["--strict"])
    check("emb/excerpt-own-file-passes", rc == 0 and "verbatim excerpt of its bound file" in out, out)

    rc, out = sc("report", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (beta NO BLOCKERS)\n")
    digests = [hashlib.sha256(d.encode()).hexdigest() for d in (ALPHA5, BETA5)]
    check("report/every-sha256", rc == 0 and all("sha256=" + d in out for d in digests), out)


def self_test_main():
    import shutil
    import tempfile
    failures, skipped, ran = [], [], []

    def check(name, cond, out=""):
        ran.append(name)
        if not cond:
            failures.append((name, " | ".join(out.strip().splitlines()[-4:])))

    base = tempfile.mkdtemp(prefix="brief-claims-selftest-")
    try:
        def sc(name, brief, **kw):
            return _scenario(base, name, brief, **kw)
        _cases_inputs(sc, check, skipped, base)
        _cases_claims(sc, check, base)
        _cases_template(sc, check)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL: {} of {} vector(s)".format(len(failures), len(ran)))
        for name, out in failures:
            print("  - FAIL {}: {}".format(name, out))
        return 1
    if skipped:
        print("SELF-TEST PASS (PARTIAL): {} vector(s) held; SKIPPED (UNVERIFIED this run): {}".format(
            len(ran), "; ".join(skipped)))
    else:
        print("SELF-TEST PASS: {} vector(s) held (configuration and input fail-closed, binding, verdicts, ids "
              "and grades, counts, template carry-over, foreign ids, embedded attribution, excerpts, report "
              "digests)".format(len(ran)))
    return 0


def main():
    opts = parse_args(sys.argv[1:])
    if opts is None:
        print("usage: check_brief_claims.py [--self-test] [--strict] [--input-dir DIR] [--template PATH] "
              "[--registry PATH] [BRIEF...]; fail-closed", file=sys.stderr)
        return 2
    if opts["self_test"]:
        return self_test_main()
    return run(Path(__file__).resolve().parents[1], opts)


if __name__ == "__main__":
    sys.exit(main())
