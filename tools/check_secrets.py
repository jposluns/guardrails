#!/usr/bin/env python3
"""Block obvious hardcoded credentials from reaching the repository.

HONEST SCOPE. This is a pattern scanner, not a secrets-management control. It
catches the common, obvious shapes: recognizable token prefixes and assignments
of a literal to a credential-named variable. It will miss a high-entropy string
with no telltale name or prefix, an encoded secret, and a secret in a binary.

MEASURED RESIDUE (2026-08-08, differential test against gitleaks 8.30.1 on
synthetic non-allowlisted values). Both this scanner and gitleaks catch a
provider-prefixed token and a credential-named assignment. NEITHER catches a
high-entropy string assigned to a name with no credential keyword near it, for
example `opaque: <40 hex chars>`. That gap is real and unmitigated by either tool.

Native GitHub secret scanning is NOT available here: it requires GitHub Enterprise
Cloud with Enterprise Managed Users, or GHES with Secret Protection, for a private
repository owned by a personal account. This gate plus gitleaks in CI is the
compensating control, not a second-best version of a control we could have had.

See `.claude/rules/security/SECC-rotate-leaked-secret.md`: a secret that reaches a remote is treated
as compromised and rotated, whatever any scanner said.

DISCLOSED RESIDUE (F-127, 2026-08-21). To stop flagging a JavaScript-style environment lookup such as
`token = process.env.OPENAI_KEY_V2` (a code reference, not a literal), an UNQUOTED credential value that
is a pure dotted-identifier path AND begins with a recognized env-access root (process.env.,
import.meta.env., or a leading env.) is treated as a reference, not a secret. The root anchor is
deliberate: a dotted value that merely contains an `env` segment elsewhere, such as
`myorg.env.production.<value>`, is still scanned as a literal, so a secret is not excluded just for having
an `env` segment. Residual: a value that genuinely begins with an env-access root and is a pure dotted
identifier is treated as a reference even in the rare case it is a real secret shaped exactly like an env
lookup; and, per the best-effort scope above, a secret a non-value character splits off after such a
prefix (adversarial fragmentation) is not independently caught. This gate does not claim to catch either,
and neither is guaranteed to be caught by gitleaks. A long dotted config path with no env-access root is
still flagged (a false positive in the safe direction).

GD-121 (2026-09-01). Two additive detectors close part of the local-vs-CI gap witnessed against the
pinned gitleaks 8.30.1 binary (SHA-256 verified; the fire side of the differential): (A) new distinctive-
prefix provider families (Google, Stripe live and test, GitLab, SendGrid, npm, PyPI, and the
triple-segment Slack webhook URL), and (B) an entropy-gated generic-assignment detector for a
credential-like name assigned a token of 40 or more characters whose first 150 characters (the entropy
window) carry Shannon entropy at least 3.5
bits/char, letters AND digits required, PLACEHOLDER and env-reference excluded, and a metadata-named LHS
excluded. This narrows the gap; it does NOT make local equal CI. Enumerated residue (not implied
complete):
  R1 keyword-free high-entropy literals (opaque = <hex>): still uncaught by BOTH tools (the 2026-08-08
     measured residue above); gitleaks skips them too.
  R2 deferred families (Twilio-class short-prefix-over-hex, Telegram bot tokens, and any family the pin
     did not corroborate): left to gitleaks in CI, each named there.
  R3 generic-keyword values of 12 to 39 characters with entropy above 3.5: gitleaks' generic rule FIRES
     here where the local 40-character floor does not. CONFIRMED against pinned gitleaks 8.30.1 (its
     generic floor is about 12, not 40); a deliberate noise-control choice, not parity.
  R4 a real secret under a metadata-named variable (secret_id = <value>): excluded by the metadata-
     component rule (safe direction). SPECIFICALLY, gitleaks FIRES on public_key and key_path, while this
     detector keeps `public` and `path` in the metadata set and stays silent on those two names; CI still
     catches such a secret. A disclosed divergence, not a silent one.
  R5 secrets split across lines, concatenated, encoded, or carried in a binary file (a suffix outside
     TEXT_SUFFIXES is never read; a suffixless file that is not valid UTF-8 and takes no wide view is
     treated as binary and skipped): unchanged, gitleaks-or-nothing territory. A dotenv or TEXT_SUFFIXES file that is not valid
     UTF-8 is NOT skipped: it is decoded losslessly and scanned (_read_scan_text). A scan candidate that
     starts with a UTF-32 byte-order mark is ALWAYS scanned through a UTF-32 wide view AND through
     the lossless byte view, the findings merged: the decode uses errors="surrogatepass", and when
     that still raises (a scalar above U+10FFFF, or a truncated final unit) the
     REPLACE policy decodes the view with errors="replace", each invalid unit a U+FFFD that is
     neither a word character nor a line break, so a well-formed token before or after a
     four-byte-aligned bad unit, or before a truncated FINAL unit, is still matched (PR #530 QA
     round-4: the round-3 strict decode lost the whole wide view to one bad unit, and an
     even-length suffixless UTF-32-BE file fell through to a binary SKIP; round-5: stray bytes in
     the MIDDLE of a UTF-32 file misalign every later unit, a residual disclosed below). The
     UTF-32 marks are tested before the UTF-16 ones because the little-endian UTF-32 mark begins
     with the UTF-16-LE mark bytes; when a file carrying that overlapping mark fails the strict
     UTF-32 decode and has EVEN length, the UTF-16 reading is kept as a FURTHER view beside the
     UTF-32 and byte views (round-5: FF FE 00 00 is also a UTF-16-LE mark followed by U+0000, and
     the round-4 mark-alone branch hid the token of such a UTF-16-LE file from every view). A
     candidate that starts with a UTF-16 byte-order mark and has
     EVEN length is scanned through the UTF-16 wide view AND the lossless byte view (the decode
     uses errors="surrogatepass", so an unpaired surrogate never costs that view; round-3 claude
     MEDIUM-1 / MINOR-1); almost any even-length byte string takes the UTF-16 wide view, so a
     BOM-prefixed file that actually holds 8-bit or ASCII text keeps the lossless-view coverage it
     had before the wide views. The merge drops exact duplicates PER REPORTED LINE AND
     LABEL, never per secret: two DIFFERENT tokens that share a path, view-relative line number and
     label (one per view) are ONE reported finding, so the FAIL total counts reported findings, not an
     inventory of distinct credentials (round-3 claude MINOR-2). Only an odd-length UTF-16-marked file
     takes no wide view: with a declared-text suffix it falls back to the lossless byte view, and
     suffixless it stays binary and is skipped (a UTF-32-marked file keeps its wide view at every
     length). Two or more stray
     non-ASCII bytes that together form a valid UTF-8 letter decode as that letter, so beside a token they
     break its word boundary and can hide it (the same word-boundary behaviour as in valid UTF-8 text);
     UTF-16 or UTF-32 without a byte-order mark and other non-UTF-8 encodings are not decoded as such, so
     a secret in them can be missed. Further residuals (round-5, disclosed): a byte-order mark in the
     MIDDLE of a file selects no wide view; MIXED-WIDTH content behind one leading mark is decoded at
     that mark width only (the overlapping FF FE 00 00 case above is the one kept mixed reading); a
     declared-text ODD-length UTF-16-marked file keeps only the lossless byte view, where the NULs of a
     real UTF-16 secret defeat the patterns; and stray bytes in the middle of a UTF-32 file misalign
     every later unit, so the replace decode recovers only a truncated FINAL unit.
  R6 the F-127 env-reference exclusion residual: unchanged, and it now ALSO governs the entropy path's
     unquoted branch.
  R7 a high-entropy value that is actually a filesystem path, URL fragment, or opaque identifier assigned
     to a GENERIC credential component (a bare `client`/`key`/`auth`) can over-fire: safe-direction NOISE (a
     false positive, never a miss), parity-preserving, no live-repo instance; the 40-char floor damps it.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_secrets.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import codecs
import math
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files, read_bytes_nonblocking  # noqa: E402  fail-closed tree walk and non-blocking read
from _gen_common import precheck_special_files  # noqa: E402  SPECIAL-FILE-PRECHECK

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".working"}
# Skip THIS file (it enumerates credential patterns) by its repo-relative PATH, not by basename, so a
# secret in any OTHER file that happens to be named check_secrets.py elsewhere in the tree is still scanned.
SKIP_PATHS = {"tools/check_secrets.py"}

# Targeted, documented exemptions. Each is a file whose PURPOSE is to enumerate
# credential patterns as prohibited examples, so a match there is the document
# doing its job. This is narrow by design: it names individual files, never a
# directory, so every other rule file and every other Markdown file is still
# scanned. Stated residue: a real secret pasted into one of these files would
# not be caught here, which is one more reason GitHub secret scanning with push
# protection is the control this gate only supplements.
# Currently empty (F-15): no security rule's body is a credential-pattern list, so nothing needs an
# exemption. A prior entry named a `security/*-secrets.md` file that never existed; removed, not repointed.
EXEMPT_PATHS = set()
TEXT_SUFFIXES = {
    ".md", ".py", ".sh", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg",
    ".env", ".conf", ".tf", ".tfvars", ".ps1", ".rb", ".js", ".ts", ".go",
    ".txt", ".properties", ".xml", ".config",
    # PEM/PGP-text credential files: a private-key header is caught by PREFIXES; a public cert is inert.
    # .ovpn is here because an OpenVPN profile can carry an inline PEM private key. Binary key stores
    # (.p12/.pfx/.jks) are not text: excluded up front by this allow-list (never read, so never a
    # UnicodeDecodeError). This allow-list is CURATED high-signal, NOT an exhaustive list of credential
    # carriers; an extension not here is left to gitleaks in CI, a BROADER independent scanner - a stronger
    # net, NOT a guarantee: gitleaks has its own coverage limits (rule and entropy scope, size caps, and
    # configured allowlists), so neither layer is exhaustive. A carrier is added here when it recurs
    # (periodic curation, backlog L12).
    ".pem", ".key", ".crt", ".cer", ".asc", ".p8", ".pk8", ".ovpn",
}

# Recognizable provider token shapes.
PREFIXES = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained PAT"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "OpenAI-style secret key"),
    (re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}"), "OpenAI project key"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}"), "Anthropic key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"), "Slack app-level token"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"), "private key block"),
    # A JWT: a base64url header that always begins 'eyJ' (base64 of '{"'), then two more base64url
    # segments. Distinctive 3-segment shape -> low false-positive risk. Local parity with CI's gitleaks
    # for this class (GD-113; the private-key-block class above was already covered).
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"), "JWT (JSON Web Token)"),
    # GD-121 additions: distinctive-prefix provider families the pinned CI gitleaks 8.30.1 flags that this
    # scanner previously missed (each fire OBSERVED behaviourally against the pinned binary). Selection bar:
    # a fixed distinctive prefix + constrained charset + fixed/min length, so false-positive risk stays near
    # zero (the same bar as the entries above).
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"), "Google API key"),
    (re.compile(r"\b[sr]k_(?:live|test)_[0-9a-zA-Z]{20,}\b"), "Stripe API key"),
    (re.compile(r"\bglpat-[0-9A-Za-z_-]{20,}"), "GitLab personal access token"),
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"), "SendGrid API key"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), "npm access token"),
    (re.compile(r"\bpypi-AgEIcHlwaS[A-Za-z0-9_-]{50,}"), "PyPI upload token"),
    # Slack incoming-webhook URL: the REAL triple-segment shape gitleaks fires on (grounding witness
    # supersedes the plan's loose services/<base64> shape, which the pinned binary MISSED). Segments:
    # T<team>/B<bot>/<secret>. gitleaks rule: slack-webhook-url.
    (re.compile(r"https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{24,}"),
     "Slack webhook URL"),
]

# A credential-named variable assigned a literal of real length.
#
# Two defects were found on 2026-08-08 by differential-testing this scanner against
# gitleaks on synthetic values, and both are fixed here:
#   1. The value had to be QUOTED, so unquoted YAML, .env, and TOML assignments (most
#      real config) were invisible. Unquoted values are now matched.
#   2. The keyword was anchored with \b, but "_" is a word character, so `aws_secret`
#      never matched the `secret` keyword. The anchor is now a non-word-or-separator
#      boundary that allows a `word_` prefix.
ASSIGN = re.compile(
    r"""(?ix)
    (?:^|[^A-Za-z0-9])                       # start, or a non-alphanumeric
    [A-Za-z0-9]*[_-]?                        # optional prefix such as aws_ or my-
    (passwd|password|secret|token|api[_-]?key|access[_-]?key|
       client[_-]?secret|auth[_-]?token|private[_-]?key|credential)
    \s*[:=]\s*
    (?:
        (?P<q>['"])(?P<qvalue>(?:(?!(?P=q))[^\n]){12,})(?P=q)  # quoted; qvalue excludes only the OPENING
                                                     # delimiter (not both quotes), so a value that embeds
                                                     # the other quote, e.g. "ab'cd...", is not truncated
      | (?P<value>[A-Za-z0-9+/=_.\-]{16,})              # or unquoted; charset excludes {$<( so
                                                     # templates and f-string holes cannot match
    )
    """
)

# Values that are obviously not real credentials.
PLACEHOLDER = re.compile(
    r"(?i)^(x{3,}|\.{3,}|\*{3,}|<[^>]+>|\$\{[^}]+\}|\$[A-Z_]+|"
    r"(your|my|the)[_-]?\w*|change[_-]?me|placeholder|example|sample|dummy|"
    r"redacted|fake|test|todo|none|null|n/?a|actual_password_here)$"
)

# A JavaScript-style environment lookup such as process.env.OPENAI_KEY_V2 or import.meta.env.VITE_KEY:
# a pure dotted-identifier path (DOTTED_PATH) that BEGINS with a recognized env-access root (_ENV_REF:
# process.env., import.meta.env., or a leading env.). It is a CODE REFERENCE to a variable, not a literal
# secret, so it is excluded from the unquoted credential match (F-127). The root anchor is required, not a
# mere `env` segment anywhere, so a dotted token that only looks identifier-shaped - a HashiCorp Vault
# `hvs.<random>`, a PASETO `v2.local.<payload>`, `prod.secret.auth.<value>`, or `myorg.env.prod.<value>`
# (env NOT at the root) - stays caught.
DOTTED_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_ENV_REF = re.compile(r"(?i)\A(?:process\.env|import\.meta\.env|env)\.")

# GD-121: an entropy-gated GENERIC assignment detector, a SECOND detector BESIDE ASSIGN (ASSIGN is left
# untouched). ASSIGN keys on high-signal credential keywords with a loose value bar; this one covers the
# generic keywords ASSIGN omits (a bare `key`, `auth`, `creds`, `passphrase`, ...) and pays for the wider
# keyword set with a STRICT value bar: one contiguous token over a bounded alphabet, length 40 or more
# (the whole value is captured so the env-ref exclusion sees it in full; entropy is judged on the first 150),
# letters AND digits, Shannon entropy >= 3.5 bits/char, PLACEHOLDER and env-reference excluded, and a
# metadata-named LHS excluded. The length bounds are single-sourced FROM the constants below INTO the
# compiled pattern (via % formatting), so the invariant check and the regex cannot silently drift apart.
ENTROPY_MIN_LEN = 40
ENTROPY_MAX_LEN = 150   # the ENTROPY WINDOW: entropy is judged on the value's first 150 chars (a
                        # gitleaks-style capture cap), NOT a hard limit on the matched token length
ENTROPY_THRESHOLD = 3.5   # bits/char; anchored to pinned gitleaks 8.30.1's generic default
# ENTROPY_MAX_LEN is a CAPTURE cap (matching gitleaks' 150-char generic capture), NOT a rejection:
# a value longer than 150 chars is still a secret and correctly fires on its 150-char prefix, exactly
# as gitleaks does (both the quoted and unquoted branches; the quoted branch deliberately does NOT anchor
# the CLOSING quote, so a >150-char quoted value is not missed). An upper cap as a rejection would MISS a
# long secret, so it is not done.
# The bounded value alphabet, as an explicit set, so the invariant check can derive its theoretical
# maximum entropy (log2 of the alphabet size). `:` `@` `<` `{` `$` are OUTSIDE it; combined with the
# whole-value-alphabet requirement on the quoted branch and the (?![@:]) guard on the unquoted branch, a
# URL, an email, JSON, a template, or prose does not become a candidate even on a qualifying prefix.
ENTROPY_VALUE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./+=-"
)
ENTROPY_ASSIGN = re.compile(
    (r"""(?x)
    (?:^|[^A-Za-z0-9])
    (?P<name>[A-Za-z][A-Za-z0-9_.\-]*)
    \s*[:=]\s*
    (?:
        # QUOTED: the WHOLE inter-quote value must be alphabet (closing quote required), so a value that
        # embeds a non-alphabet char (an email's @, a URL's :) does NOT partially match on its prefix; no
        # upper cap here because the closing quote bounds it, so a >150-char quoted secret still fires.
        (?P<q>['"])(?P<qvalue>[A-Za-z0-9_./+=\-]{%(min)d,})(?P=q)
        # UNQUOTED: a possessive run (no backtracking) of 40 OR MORE alphabet chars that is NOT followed by @ or
        # : (which would make it a prefix of a larger structured value such as an email or a URL); a longer
        # all-alphabet unquoted value still fires on its 150-char prefix (char 151 is alphabet, not @/:).
      | (?P<value>[A-Za-z0-9_./+=\-]{%(min)d,}+)(?![@:])
    )
    """ % {"min": ENTROPY_MIN_LEN})
)
# Credential keyword components (D9) and metadata components (D8), matched on EXACT normalized components
# after splitting the LHS on `_ - .` and camelCase boundaries, so `apiKey`/`client-secret`/`auth.token`
# match while `monkey`/`author`/`keyboard` (substring collisions) cannot. A metadata component WINS over a
# credential component, so `api_version` and `key_id` are excluded (corroborated: pinned gitleaks skips
# both). `public` and `path` are KEPT in the metadata set (D8 option b): gitleaks FIRES on public_key and
# key_path, so those two names are a DISCLOSED safe-direction residual (local quieter, CI still catches).
CREDENTIAL_COMPONENTS = frozenset({
    "key", "api", "apikey", "auth", "token", "secret", "password", "passwd", "pwd", "pass",
    "passphrase", "credential", "credentials", "creds", "access", "client",
})
METADATA_COMPONENTS = frozenset({
    "id", "name", "version", "url", "uri", "endpoint", "path", "file", "dir", "alias", "length",
    "size", "count", "type", "checksum", "digest", "fingerprint", "public",
})
_COMPONENT_SPLIT = re.compile(r"[_.\-]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _assign_is_secret(match):
    """True when an ASSIGN match assigns a real (non-placeholder) credential literal. Shared decision
    logic; the secsec hook (_scan_secret in aiqt_hooks.py) mirrors this EXACTLY. A quoted value keeps the
    looser bar. An UNQUOTED value must contain both a letter and a digit AND must not be an environment
    lookup such as process.env.X (F-127). A PLACEHOLDER value is never a secret."""
    value = (match.group("qvalue") or match.group("value") or "").strip()
    if not value:
        return False
    if match.group("qvalue") is None:  # unquoted
        if not (any(c.isalpha() for c in value) and any(c.isdigit() for c in value)):
            return False
        if DOTTED_PATH.fullmatch(value) and _ENV_REF.match(value):
            return False
    return not PLACEHOLDER.match(value)


def _shannon_entropy(value):
    """Shannon entropy of value in bits per character (stdlib math only, no new dependency). 0.0 for an
    empty or single-repeated-character value; exactly log2(k) for k distinct characters in equal counts."""
    if not value:
        return 0.0
    counts = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _split_components(name):
    """Lower-cased EXACT components of an identifier, split on `_ - .` and camelCase boundaries, so
    `apiKey` -> ['api', 'key'] and `client-secret` -> ['client', 'secret']. Used for exact-component
    keyword matching, which a substring test (the defect in a rejected seed) would get wrong."""
    components = []
    for part in _COMPONENT_SPLIT.split(name):
        if not part:
            continue
        for token in _CAMEL.findall(part):
            components.append(token.lower())
    return components


def _entropy_assign_is_secret(match):
    """True when an ENTROPY_ASSIGN match is a high-entropy literal assigned to a credential-like name. The
    secsec hook (_scan_secret in aiqt_hooks.py) mirrors this EXACTLY. Ordered gates: a metadata component
    on the LHS WINS (return False); the name must carry a credential component; a PLACEHOLDER value is
    excluded BEFORE entropy; an UNQUOTED env-lookup (process.env.X) is a reference, not a literal; the
    value must carry both a letter and a digit; finally Shannon entropy must clear ENTROPY_THRESHOLD. The
    value is never echoed."""
    value = (match.group("qvalue") or match.group("value") or "").strip()
    if not value:
        return False
    components = _split_components(match.group("name"))
    if any(component in METADATA_COMPONENTS for component in components):
        return False
    if not any(component in CREDENTIAL_COMPONENTS for component in components):
        return False
    if PLACEHOLDER.match(value):
        return False
    if match.group("qvalue") is None:  # unquoted
        if DOTTED_PATH.fullmatch(value) and _ENV_REF.match(value):
            return False
    # The regex captures the WHOLE value (so the env-ref exclusion above sees the full text, not a
    # truncated fragment); entropy and the letter+digit bar are then judged on the first ENTROPY_MAX_LEN
    # chars (the gitleaks-style capture cap), so a >150-char secret still fires on its high-entropy
    # 150-char prefix even when padded with a low-entropy tail that would dilute the whole-value entropy.
    candidate = value[:ENTROPY_MAX_LEN]
    if not (any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate)):
        return False
    return _shannon_entropy(candidate) >= ENTROPY_THRESHOLD


def _validate_entropy_constants():
    """Defence-in-depth (D11) check on the entropy control constants, run once before any scan. Returns an
    error string on a bad hand-edit, or None when the constants are sound: min length positive, max not
    below min, threshold within (0, log2(alphabet size)]."""
    if not (isinstance(ENTROPY_MIN_LEN, int) and not isinstance(ENTROPY_MIN_LEN, bool) and ENTROPY_MIN_LEN > 0):
        return "ENTROPY_MIN_LEN must be a positive integer"
    if not (isinstance(ENTROPY_MAX_LEN, int) and not isinstance(ENTROPY_MAX_LEN, bool)
            and ENTROPY_MAX_LEN >= ENTROPY_MIN_LEN):
        return "ENTROPY_MAX_LEN must be an integer not below ENTROPY_MIN_LEN"
    if isinstance(ENTROPY_THRESHOLD, bool) or not isinstance(ENTROPY_THRESHOLD, (int, float)):
        return "ENTROPY_THRESHOLD must be a real number"
    max_entropy = math.log2(len(ENTROPY_VALUE_CHARS))
    if not (0 < ENTROPY_THRESHOLD <= max_entropy):
        return "ENTROPY_THRESHOLD must be within (0, log2(alphabet size)]"
    return None


def _is_declared_text(path):
    """True when a file is TEXT BY DECLARATION: a dotenv name (`.env`, or starting `.env.`, case-insensitive)
    or a suffix in TEXT_SUFFIXES. Only the other scan candidates (no suffix at all) can be binary."""
    lowered = path.name.lower()
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def _read_scan_text(path):
    """Return (texts, note) for a scan candidate; texts is a tuple of decoded VIEWS to scan (the caller
    merges the views' findings and drops exact duplicates per reported line and label, see main()), or
    None when the file is skipped as binary. Valid UTF-8 decodes as is (one view, note None). A candidate
    that is text by declaration (_is_declared_text) but is NOT valid UTF-8 is decoded LOSSLESSLY with
    errors="surrogateescape": every valid character keeps its meaning and each undecodable byte becomes
    its own lone surrogate, which is neither a word character nor a line break, so every ASCII pattern
    still matches beside it (a latin-1 view would turn 0xFE into a letter that breaks a word boundary,
    and 0x85 into a line break). A candidate that is not valid UTF-8 but starts with a byte-order mark
    gets a WIDE view BESIDE the lossless surrogateescape view (a byte view of real UTF-16 or UTF-32
    carries NULs between the ASCII characters, which defeat every pattern); the wide attempt only ever
    ADDS a view (PR #530 QA round-2 claude MEDIUM-1). The UTF-32 marks are tested FIRST, decided by the
    MARK alone, because the little-endian UTF-32 mark BEGINS with the UTF-16-LE mark bytes (the
    big-endian marks do not overlap), so a UTF-32-marked file is scanned as UTF-32, never as UTF-16
    with a NUL between every two code units in BOTH views (round-3 claude MINOR-1). The UTF-32 decode
    uses errors="surrogatepass" (an unpaired surrogate decodes to its lone code point, neither a
    word character nor a line break); when that still raises, on a scalar above U+10FFFF or a
    truncated final unit, the REPLACE policy decodes the view with errors="replace"
    (round-4: the round-3 strict decode lost the whole wide view to one bad unit, and an even-length
    suffixless UTF-32-BE file fell through to a binary SKIP), so a UTF-32-marked file ALWAYS keeps
    its wide view; and because FF FE 00 00 is BOTH the UTF-32-LE mark and a UTF-16-LE mark followed
    by U+0000, a file with that mark whose UTF-32 surrogatepass decode fails and whose length is EVEN
    also
    keeps the UTF-16 surrogatepass reading as a THIRD view (round-5: the round-4 mark-alone branch
    hid the token of a UTF-16-LE file with a leading U+0000 from every view, where round-3, decided
    by a successful decode, had fallen through to the UTF-16 branch). Residuals (round-5, disclosed):
    a mark in the MIDDLE of a file selects no wide view; mixed-width content behind one leading mark
    is decoded at that mark width only (the overlapping-mark case above is the one kept mixed
    reading); and stray bytes in the middle of a UTF-32 file misalign every later unit, so the
    replace decode recovers only a truncated FINAL unit; and (round-6) an ODD-length file beginning
    with the overlapping FF FE 00 00 mark takes the UTF-32 branch by its mark alone and keeps no
    UTF-16 reading (the even-length condition above), so a UTF-16-LE token in it is missed by both
    kept views, while the suffixless one is still decoded and scanned under the UTF-32 NOTE, never
    skipped as binary with a named SKIP line. The UTF-16 decode uses
    errors="surrogatepass", so a UTF-16-marked file of EVEN
    length ALWAYS gets the wide view: an unpaired surrogate decodes to its lone code point instead
    of costing the whole view and hiding a well-formed UTF-16 token elsewhere in the file (round-3
    claude MEDIUM-1). Only an ODD-length UTF-16-marked file whose first four bytes are not the
    overlapping UTF-32-LE mark (that one keeps its UTF-32 views; the round-6 residual above) yields
    no wide view; it is judged as any other non-UTF-8 file: a SUFFIXLESS one is treated as binary and skipped, as the existing rule
    (_is_scan_candidate) defines, and a declared-text one keeps the lossless view. The bytes are read
    through read_bytes_nonblocking, never a plain blocking read: the walk meets git-ignored paths the
    D-400 special-file precheck does not hold and files created after it ran, so a FIFO or other
    special file there (or a symlink to one) is refused as OSError, which main() reports as exit 2,
    instead of blocking the scan forever."""
    data = read_bytes_nonblocking(path)
    try:
        return (data.decode("utf-8"),), None
    except UnicodeDecodeError:
        pass
    if data[:4] in (codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE):
        try:
            wide32 = data.decode("utf-32", errors="surrogatepass")
        except UnicodeDecodeError:
            # surrogatepass still refuses a scalar above U+10FFFF and a truncated final unit;
            # REPLACE policy (round-4): each invalid unit becomes U+FFFD, neither a word character
            # nor a line break, so a well-formed token before or after a four-byte-aligned bad
            # unit still matches, where a longest-valid-prefix policy would lose every token after
            # the first bad unit (round-5: pinned by the *_pre.txt/*_mid.txt self-test vectors;
            # stray mid-file bytes that misalign every later unit stay a disclosed residual).
            wide32 = data.decode("utf-32", errors="replace")
            if data[:2] == codecs.BOM_UTF16_LE and len(data) % 2 == 0:
                # round-5 (claude 1): FF FE 00 00 is BOTH the UTF-32-LE mark and a UTF-16-LE mark
                # followed by U+0000, so when the UTF-32 surrogatepass decode fails (round-6: that
                # decode is the deciding one; the replace decode never raises) and the data also
                # reads as even-length UTF-16, the UTF-16 reading is KEPT as a further view (the
                # round-4 mark-alone branch dropped the round-3 UTF-16 fallback and hid the token
                # of a UTF-16-LE file with a leading U+0000 from every view).
                return ((wide32, data.decode("utf-16", errors="surrogatepass"),
                         data.decode("utf-8", errors="surrogateescape")),
                        "NOTE (UTF-32 byte-order mark overlapping a UTF-16-LE one: decoded as "
                        "UTF-32 AND as UTF-16 and also scanned losslessly as bytes; findings "
                        "merged)")
        return ((wide32, data.decode("utf-8", errors="surrogateescape")),
                "NOTE (UTF-32 byte-order mark: decoded as UTF-32 and also scanned losslessly as "
                "bytes; findings merged)")
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        try:
            return ((data.decode("utf-16", errors="surrogatepass"),
                     data.decode("utf-8", errors="surrogateescape")),
                    "NOTE (UTF-16 byte-order mark: decoded as UTF-16 and also scanned losslessly as "
                    "bytes; findings merged)")
        except UnicodeDecodeError:
            pass  # an ODD length (a truncated final code unit) is the ONE failure surrogatepass leaves:
            # no wide view exists, so judged as any other non-UTF-8 file below
    if not _is_declared_text(path):
        return None, "SKIP (not utf-8, no suffix: treated as binary)"
    return ((data.decode("utf-8", errors="surrogateescape"),),
            "NOTE (not valid utf-8: scanned, each undecodable byte kept as a placeholder)")


def _is_scan_candidate(path):
    """A file is scanned when it is a dotenv file (name `.env` or starting `.env.`, case-insensitive, such
    as `.env.local` or `.env.production`, which hold real credentials), has a suffix in TEXT_SUFFIXES, or
    has no suffix at all (an extensionless PEM key like id_rsa). Suffix matching is case-insensitive, so a
    `.PEM` or `.TXT` file scans too. A file whose suffix is NOT in TEXT_SUFFIXES is skipped up front; a
    suffixless file that is NOT valid UTF-8 and takes no wide view (no UTF-32 byte-order mark, and
    no even-length UTF-16 one) is binary: it is read and skipped later (a UTF-8-decodable suffixless
    file is scanned). A dotenv or TEXT_SUFFIXES file is
    text by declaration and is scanned even when it is not valid UTF-8 (_read_scan_text). TEXT_SUFFIXES is
    a curated allow-list, so a text format not on it is skipped here and left to gitleaks; add it to
    TEXT_SUFFIXES to cover it."""
    return _is_declared_text(path) or not path.suffix


def main(root=None) -> int:
    if root is None and "--self-test" in sys.argv[1:]:
        # The process entry: --self-test anywhere in the arguments runs the self-test, as before; a
        # fixture root (the self-test's own calls) always scans.
        return _self_test()
    error = _validate_entropy_constants()
    if error is not None:
        print(f"error: invalid entropy control constant ({error}); fail-closed", file=sys.stderr)
        return 2
    root = precheck_special_files(Path(__file__).resolve().parents[1]) if root is None else Path(root)
    findings = []
    try:
        for path in sorted(walk_files(root, SKIP_DIRS)):
            if path.relative_to(root).as_posix() in SKIP_PATHS:
                continue
            if path.relative_to(root).as_posix() in EXEMPT_PATHS:
                continue
            if not _is_scan_candidate(path):
                continue
            # A text-by-declaration file with a stray non-UTF-8 byte is scanned (lossless decode); only
            # a suffixless non-UTF-8 file that took no wide view (no UTF-32 mark, no even-length
            # UTF-16 one) is binary and skipped (gitleaks scans binaries).
            texts, note = _read_scan_text(path)
            if note is not None:
                print(f"{note}: {path.relative_to(root)}")
            if texts is None:
                continue
            rel = path.relative_to(root)
            file_findings = []
            for text in texts:
                for number, line in enumerate(text.splitlines(), 1):
                    before = len(file_findings)
                    for pattern, label in PREFIXES:
                        if pattern.search(line):
                            file_findings.append(f"{rel}:{number}: {label}")
                    # Scan EVERY credential-named assignment on the line, not just the first: a
                    # placeholder assignment earlier on the line must not mask a real one after it.
                    # One finding per line suffices, so append on the first real match and stop.
                    for match in ASSIGN.finditer(line):
                        if _assign_is_secret(match):
                            file_findings.append(
                                f"{rel}:{number}: credential-named variable assigned a literal"
                            )
                            break
                    # GD-121: only when neither a provider prefix nor a credential-named ASSIGN
                    # flagged this line, try the entropy-gated generic detector. One finding per line;
                    # the first real match wins, and finditer skips earlier placeholder/metadata matches
                    # so they cannot mask it (parity with the ASSIGN scan-all-assignments behaviour
                    # above).
                    if len(file_findings) == before:
                        for match in ENTROPY_ASSIGN.finditer(line):
                            if _entropy_assign_is_secret(match):
                                file_findings.append(f"{rel}:{number}: high-entropy literal assigned "
                                                     f"to a credential-like name")
                                break
            # The views of ONE file are merged with exact duplicates dropped: a finding both views
            # produce (the same path, view-relative line number and label) is one finding, never two.
            # The dedup key is the REPORTED LINE AND LABEL, never the secret itself: two DIFFERENT
            # tokens that share that key (such as one token per view on the same line number) also
            # merge to ONE finding, so the FAIL total counts reported findings, not an inventory of
            # distinct credentials (PR #530 QA round-3 claude MINOR-2).
            seen = set()
            for finding in file_findings:
                if finding not in seen:
                    seen.add(finding)
                    findings.append(finding)
    except OSError as exc:
        # An unreadable directory or file is a read error, not a clean skip: fail closed (exit 2) so the
        # secret gate never reports clean without having scanned an unreadable subtree.
        print(f"error: cannot scan the tree ({exc}); fail-closed", file=sys.stderr)
        return 2
    if findings:
        print(f"FAIL: {len(findings)} possible hardcoded secret(s)")
        for finding in sorted(set(findings)):
            print(f"  {finding}")
        print("\nIf any is real, treat it as COMPROMISED: rotate first, then remove.")
        return 1
    print("PASS: no obvious hardcoded secrets (pattern scan; not a substitute "
          "for GitHub secret scanning)")
    return 0


def _self_test() -> int:
    """Exercise the credential-value decision and the scan-candidate predicate against fixtures, so the
    F-127 env-lookup exclusion and the extensionless/dotenv coverage are guarded by a check that fails
    without them. Provider-shaped token fixtures are assembled from parts (SECP) so they never appear as a
    contiguous literal. The credential-ASSIGNMENT fixtures below (e.g. `password = <value>`) DO read as
    secrets to the scanner, but stay literal because THIS file is path-exempt from the scan (SKIP_PATHS),
    so they never trip the live gate. Exit 0 on PASS, 1 on FAIL."""
    from pathlib import PurePath

    error = _validate_entropy_constants()
    if error is not None:
        print(f"SELF-TEST FAIL: invalid entropy control constant ({error})", file=sys.stderr)
        return 2

    failures = []

    def assign_hit(line):
        return any(_assign_is_secret(m) for m in ASSIGN.finditer(line))

    def _n(seed, length):
        # A deterministic length-`length` token drawn from `seed` (assembled at runtime; SECP: no
        # contiguous credential literal sits in this source). `seed` carries letters AND digits.
        return (seed * (length // len(seed) + 1))[:length]

    def entropy_hit(line):
        return any(_entropy_assign_is_secret(m) for m in ENTROPY_ASSIGN.finditer(line))

    real = "A7bce9f2Xk1mNq8Z"                          # 16 mixed alnum chars, real-length, not placeholder
    vault = "hvs." + "CvmS4c0DPTvHv5eJgXWMJg9r"          # HashiCorp Vault hvs.<random> shape (dotted, real)
    paseto = "v2." + "local." + "abcd1234efgh5678"       # PASETO-style dotted token
    prodsec = "prod.secret.auth." + "a1b2c3d4e5f6"        # dotted non-env provider secret
    envmid = "myorg.env.production." + "secretkey12345"   # env NOT at the root: a real secret shape, CAUGHT
    longcfg = "application.services.oauth2.client.credentials.providerToken1"  # long dotted config, no env
    cases = [
        ("password = " + real, True),                        # unquoted real literal: caught
        ("token = process.env.OPENAI_KEY_V2", False),         # F-127 env root: excluded
        ("api_key = import.meta.env.VITE_API_KEY2", False),   # env root (Vite): excluded
        ("secret = env.SECRET_VALUE2", False),                # leading env root: excluded
        ("token = " + vault, True),                          # Vault token, dotted but no env root: CAUGHT
        ("api_key = " + paseto, True),                       # PASETO token, dotted but no env root: CAUGHT
        ("api_key = " + prodsec, True),                      # dotted non-env secret: CAUGHT
        ("token = " + envmid, True),                         # env NOT at root (round-3): CAUGHT
        ("secret = " + longcfg, True),                       # long dotted config, no env root: CAUGHT (safe)
        ('secret = "' + real + '"', True),                   # quoted real literal: caught
        ('password = "' + ("ab" + chr(39) + "cd456efghij") + '"', True),  # dq value w/ embedded apostrophe: caught
        ("password = process_env_KEY2", True),               # single identifier, not dotted: caught
        ('password = "xxxxxxxxxxxx"', False),                # quoted placeholder: excluded
        ('token = "process.env.OPENAI_KEY_V2"', True),       # QUOTED env-ref: caught (exclusion is unquoted-only)
        ("token = process.env.OPENAI_KEY1" + "+" + "Abcdef1234567890", True),  # + breaks fullmatch: caught
    ]
    for line, want in cases:
        got = assign_hit(line)
        if got != want:
            failures.append("ASSIGN {!r}: want {}, got {}".format(line, want, got))

    # scan-candidate keys on the file NAME (case-insensitive dotenv), so dotenv variants scan even when the
    # last suffix is unknown; ("id_rsa", True) guards the extensionless fix; (".env.local", True) and
    # (".ENV.local", True) guard the dotenv-by-name (case-insensitive) fix; ("creds.txt", True) and
    # ("service.properties", True) guard the added text suffixes.
    names = [("id_rsa", True), ("notes.md", True), (".env", True), (".env.local", True),
             (".ENV.local", True), ("creds.txt", True), ("service.properties", True),
             ("config.xml", True), ("app.config", True),
             ("server.pem", True), ("id_rsa.key", True), ("cert.crt", True),
             ("chain.cer", True), ("key.asc", True), ("AuthKey.p8", True),
             ("SERVER.PEM", True), ("CREDS.TXT", True),
             ("client.ovpn", True), ("legacy.pk8", True),
             ("image.png", False), ("doc.pdf", False)]
    for name, want in names:
        got = _is_scan_candidate(PurePath(name))
        if got != want:
            failures.append("scan-candidate {!r}: want {}, got {}".format(name, want, got))

    # Guard the path-scoped self-exemption: a basename revert (SKIP_PATHS = {"check_secrets.py"}) would
    # skip a same-named file anywhere, so assert the repo-relative PATH form and reject a bare basename.
    if "tools/check_secrets.py" not in SKIP_PATHS or "check_secrets.py" in SKIP_PATHS:
        failures.append("SKIP_PATHS must be repo-relative-path-scoped, not basename")

    # PREFIXES token-shape coverage (GD-113): fixtures assembled from parts (SECP), no contiguous literal.
    def prefix_hit(text):
        return any(rx.search(text) for rx, _label in PREFIXES)
    jwt = "eyJ" + "abcdefghij" + "." + "eyJzdWIiOjEyMw" + "." + "s1gnatureAbc123"
    pem = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"
    prefix_cases = [(jwt, True), (pem, True),
                    ("eyJustAWord in some prose here", False),
                    ("just.some.dotted.words go here", False)]
    for text, want in prefix_cases:
        if prefix_hit(text) != want:
            failures.append("PREFIXES {!r}: want {}, got {}".format(text, want, prefix_hit(text)))

    # GD-121 PROVIDER coverage (label-asserted, so a broad regex cannot silently steal a classification).
    # Every token assembled from parts (SECP). New families were witnessed firing on pinned gitleaks
    # 8.30.1; here we assert THIS scanner's own label.
    def prefix_label(text):
        for rx, label in PREFIXES:
            if rx.search(text):
                return label
        return None
    _seed = "aB3dE7gH9k"                                    # 10 distinct, letters + digits
    _upper = "ABCDEFGHIJKLMNOP"                             # 16 uppercase for AWS/Slack-team segments
    label_cases = [
        # existing seven families (Section 4: close the enumeration gap; JWT/PEM already covered above)
        ("gh" + "p_" + _n(_seed, 30), "GitHub token"),
        ("github_" + "pat_" + _n(_seed, 25), "GitHub fine-grained PAT"),
        ("sk-" + _n(_seed, 24), "OpenAI-style secret key"),
        ("sk-" + "proj-" + _n(_seed, 24), "OpenAI project key"),
        ("sk-" + "ant-" + _n(_seed, 24), "Anthropic key"),
        ("AK" + "IA" + _upper, "AWS access key id"),
        ("xox" + "b-" + _n(_seed, 14), "Slack token"),
        ("xapp-" + _n(_seed, 14), "Slack app-level token"),
        # GD-121 new families
        ("AI" + "za" + _n(_seed, 35), "Google API key"),
        ("sk" + "_live_" + _n(_seed, 24), "Stripe API key"),
        ("rk" + "_test_" + _n(_seed, 24), "Stripe API key"),
        ("glpat-" + _n(_seed, 22), "GitLab personal access token"),
        ("SG." + _n(_seed, 22) + "." + _n(_seed, 43), "SendGrid API key"),
        ("npm_" + _n(_seed, 36), "npm access token"),
        ("pypi-" + "AgEIcHlwaS" + _n(_seed, 52), "PyPI upload token"),
        ("AI"+"za" + _n(_seed, 34) + "-", "Google API key"),           # GD121-QA-2: terminal - before EOL
        ("SG." + _n(_seed, 22) + "." + _n(_seed, 42) + "-", "SendGrid API key"),  # terminal - fires
        ("https://hooks.slack.com" + "/services/" + "T" + _upper + "/B" + _upper + "/" + _n(_seed, 24),
         "Slack webhook URL"),
    ]
    for text, want in label_cases:
        got = prefix_label(text)
        if got != want:
            failures.append("PREFIXES label {!r}: want {!r}, got {!r}".format(text, want, got))
    # Must NOT fire, provider (shape/length/word-boundary breaks).
    label_neg = [
        "AI" + "za" + "<PLACEHOLDER>",                      # shape broken by '<'
        "SG." + "short" + "." + "tail",                    # segment lengths wrong
        "sk" + "_live_" + "short1",                        # below the 20 floor
        "npm_" + _n(_seed, 35),                            # one short of the fixed 36
        "The AIza prefix appears in prose",                # AIza not followed by 35 token chars
        "https://hooks.slack.com" + "/services/T1/B2/3",   # webhook segments below real length floors
    ]
    for text in label_neg:
        if prefix_label(text) is not None:
            failures.append("PREFIXES neg {!r}: expected no label, got {!r}".format(text, prefix_label(text)))

    # GD-121 ENTROPY path. `_hi16` uses a 16-distinct seed so entropy comfortably clears 3.5; the exact
    # boundary distributions are pinned by the helper-unit assertions further below.
    _hi16 = "aB3dE7gH9kLmN2pQ"                              # 16 distinct, letters + digits
    _hi48 = _n(_hi16, 48)                                  # 48 chars, 16 distinct each x3 -> entropy 4.0
    _hex = _n("0123456789abcdef", 40)                      # 40-char hex, high entropy, letters + digits
    _ph_hi = "your_key_here_replace_before_use_1234567890"  # PLACEHOLDER ('your'...) yet high entropy
    entropy_cases = [
        ('key = "' + _hi48 + '"', True),                          # bare key, quoted, entropy 4.0
        ('key = "' + _n(_hi16, 300) + '"', True),                 # >150 QUOTED value fires on its prefix (F1-quoted)
        ('key = "' + _n(_hi16, 150) + 'a' * 63 + '"', True),         # 150 hi-entropy + low-entropy pad: fires on the 150-prefix (no dilution evasion)
        ('token = ' + ('process.env.SOME_VAR9_' * 10)[:151], False),  # >150 unquoted env-ref: excluded on the FULL value (R6, not a truncated fragment)
        ('key = ' + _n(_hi16, 300), True),                        # >150 unquoted value fires on its prefix
        ("passphrase: " + _n(_hi16, 40), True),                   # broadened keyword, unquoted YAML
        ('signingKey = "' + _n(_hi16, 44) + '"', True),           # camelCase component extraction
        ("creds" + " = " + _hex, True),                           # hex above 3.5, unquoted
        ('key = "' + _n(_hi16, 40) + '"', True),                  # exactly 40: length boundary fires
        ('key = "' + _ph_hi + '"; token = "' + _n(_hi16, 40) + '"', True),  # placeholder then real
        # must NOT fire
        ('key = "' + "a" * 40 + '"', False),                      # keyword present, entropy ~0
        ('key = "' + _n("abcd1234", 40) + '"', False),            # 8-distinct equal-count: entropy 3.0
        ('key = "' + _n(_hi16, 39) + '"', False),                 # 39: below length floor
        ('key = "' + _n("abcdefghijklmnop", 40) + '"', False),    # all-letter: letter+digit bar
        ('key = "<your-key-here>"', False),                       # '<' outside token + placeholder
        ('key = "' + _ph_hi + '"', False),                        # placeholder precedes entropy
        ("auth = " + "process.env.SERVICE_TOKEN2_padding_abcdefgh", False),  # env-ref exclusion (unquoted)
        ('public_key = "' + _hi48 + '"', False),                  # metadata component 'public'
        ('api_version = "' + _hi48 + '"', False),                 # metadata component 'version'
        ('monkey = "' + _hi48 + '"', False),                      # anti-substring ('key' not a component)
        ('author = "' + _hi48 + '"', False),                      # anti-substring ('auth')
        ('keyboard = "' + _hi48 + '"', False),                    # anti-substring ('key')
        ('opaque = "' + _hi48 + '"', False),                      # deliberate keyword-context boundary
        ('key = "this is a long descriptive sentence value ok"', False),  # whitespace breaks the token
        ('key = "' + _hi48 + '@example.com"', False),      # quoted email: whole value not alphabet (@) -> no fire
        ('key = ' + _hi48 + '@example.com', False),        # unquoted email: (?![@:]) guard -> no fire
        ('key = "' + _hi48 + ':8080/tail"', False),        # quoted URL-ish (:) -> no fire
        ('key = "\u212a' + _n(_hi16, 41) + '"', False),      # Kelvin sign (U+212A) outside the ASCII alphabet -> no fire (no (?i))
    ]
    for line, want in entropy_cases:
        got = entropy_hit(line)
        if got != want:
            failures.append("ENTROPY {!r}: want {}, got {}".format(line, want, got))

    # Entropy helper unit assertions on engineered equal-count distributions (log2(k) for k distinct).
    entropy_units = [("z" * 12, 0.0), ("aabb", 1.0), ("abcdefgh", 3.0), ("abcdefghijklmnop", 4.0)]
    for value, want in entropy_units:
        got = _shannon_entropy(value)
        if abs(got - want) > 1e-9:
            failures.append("_shannon_entropy({!r}): want {}, got {}".format(value, want, got))

    # GD-121 (codex r3): the invariant rejects a boolean control (bool is an int subclass in Python), so a
    # bad hand-edit like ENTROPY_MIN_LEN = True cannot silently disable or invert the guard. Each constant
    # is swapped by a direct global assignment, never a computed globals() key, which the reserved-name
    # scan in tools/gen_renderers.py reports as an unfoldable module-namespace key.
    global ENTROPY_MIN_LEN, ENTROPY_MAX_LEN, ENTROPY_THRESHOLD
    _entropy_saved = (ENTROPY_MIN_LEN, ENTROPY_MAX_LEN, ENTROPY_THRESHOLD)
    try:
        for _index, _name in enumerate(("ENTROPY_MIN_LEN", "ENTROPY_MAX_LEN", "ENTROPY_THRESHOLD")):
            _swapped = list(_entropy_saved)
            _swapped[_index] = True
            ENTROPY_MIN_LEN, ENTROPY_MAX_LEN, ENTROPY_THRESHOLD = _swapped
            if _validate_entropy_constants() is None:
                failures.append("entropy invariant must reject a boolean {}".format(_name))
    finally:
        ENTROPY_MIN_LEN, ENTROPY_MAX_LEN, ENTROPY_THRESHOLD = _entropy_saved

    # Non-UTF-8 scan, driven through main() over a private temp root so a revert of the lossless decode is
    # red. notes.md carries one invalid byte beside a planted token and another beside a credential
    # assignment; creds.txt puts the invalid byte directly ADJACENT to the token (a latin-1 view would turn
    # it into a letter and break the token word boundary); blob is a TRUE binary (no suffix, NUL bytes, not
    # UTF-8) carrying the same token, and stays skipped as before. Tokens are assembled from parts (SECP).
    import io
    import tempfile
    from contextlib import redirect_stdout
    _tok = ("gh" + "p_" + _n(_seed, 30)).encode("ascii")
    with tempfile.TemporaryDirectory(prefix="check-secrets-selftest-") as tmp:
        troot = Path(tmp)
        (troot / "notes.md").write_bytes(b"intro line\n\xff " + _tok + b"\n\xff"
                                         + ("password = " + real).encode("ascii") + b"\n")
        (troot / "creds.txt").write_bytes(b"\xfe" + _tok + b"\n")
        (troot / "blob").write_bytes(b"\x7fELF\x00\x00\xff\xfe " + _tok + b"\n")
        # UTF-16 with a byte-order mark, little- and big-endian and suffixless: decoded as UTF-16 and
        # scanned (a UTF-8 view puts a placeholder between every two characters and finds nothing).
        _line16 = "password = " + real + "\n"
        (troot / "win16le.txt").write_bytes(codecs.BOM_UTF16_LE + _line16.encode("utf-16-le"))
        (troot / "win16be.md").write_bytes(codecs.BOM_UTF16_BE + (_tok.decode("ascii") + "\n").encode(
            "utf-16-be"))
        (troot / "winkey16").write_bytes(codecs.BOM_UTF16_LE + (_tok.decode("ascii") + "\n").encode(
            "utf-16-le"))
        # A BOM-prefixed file that holds 8-BIT TEXT, not UTF-16 (PR #530 QA round-2 claude MEDIUM-1):
        # even length, so it decodes as UTF-16 (to CJK text with no match); the merged lossless byte
        # view must still find the ASCII token. The odd-length variants cannot decode as UTF-16, with
        # surrogatepass included: the .txt one falls through to the lossless view (round-2 claude
        # MINOR-1) and the suffixless one stays a binary SKIP. dup16.txt carries the token in BOTH
        # views at line 1, so the merge must count it ONCE (the FAIL total below pins the
        # deduplication).
        (troot / "bom8.txt").write_bytes(codecs.BOM_UTF16_LE + b"caf\xe9 " + _tok + b"\n")
        (troot / "bomodd.txt").write_bytes(codecs.BOM_UTF16_BE + _tok + b"\n")
        (troot / "bombin").write_bytes(codecs.BOM_UTF16_LE + _tok + b"\n")
        (troot / "dup16.txt").write_bytes(codecs.BOM_UTF16_LE + _tok + ("  " + _tok.decode("ascii")
                                                                        + "\n").encode("utf-16-le"))
        # EVEN-length BOM-marked files with an UNPAIRED SURROGATE after the secret (PR #530 QA round-3
        # claude MEDIUM-1), little- and big-endian and suffixless: a STRICT decode raised and dropped
        # the whole wide view, hiding the well-formed UTF-16 token before the surrogate (the byte view
        # holds NUL-separated characters, which defeat every pattern); surrogatepass keeps the view, so
        # each is a finding, and the suffixless one is text with two views, never a binary SKIP.
        _sur = (_tok.decode("ascii") + "\n").encode("utf-16-le") + b"\x00\xd8" + "x".encode("utf-16-le")
        (troot / "lone_sur.txt").write_bytes(codecs.BOM_UTF16_LE + _sur)
        (troot / "lone_sur_be.md").write_bytes(
            codecs.BOM_UTF16_BE + (_tok.decode("ascii") + "\n").encode("utf-16-be") + b"\xd8\x00"
            + "x".encode("utf-16-be"))
        (troot / "lone_sur_bin").write_bytes(codecs.BOM_UTF16_LE + _sur)
        # A UTF-32-LE file (round-3 claude MINOR-1): its mark BEGINS with the UTF-16-LE one, so a
        # UTF-16-first test took it for UTF-16 and every view carried a NUL between the characters;
        # tested as UTF-32 FIRST (round-4: decided by the MARK alone), the token is a finding.
        (troot / "u32le.txt").write_bytes(codecs.BOM_UTF32_LE + (_tok.decode("ascii")
                                                                 + "\n").encode("utf-32-le"))
        # Round-4 UTF-32 vectors (claude MEDIUM-1 / codex MINOR-2; the BE mark pin is round-3
        # claude MINOR-1). u32be.txt pins the big-endian mark on its own, so an LE-only revert of
        # the mark test is red. The *_sur* files put a lone surrogate (U+D800) after a well-formed
        # token: the round-3 STRICT decode dropped the whole wide view on it (the LE file fell
        # through to the UTF-16 branch, whose views carry a NUL between the characters; the BE file
        # fell to the lossless byte view; the suffixless BE file became a binary SKIP despite its
        # EVEN length). u32be_bad.txt ends in a scalar above U+10FFFF and u32le_trunc.txt in a
        # truncated final unit: surrogatepass still refuses both, so each pins the
        # errors="replace" fallback.
        _t32 = _tok.decode("ascii") + "\n"
        (troot / "u32be.txt").write_bytes(codecs.BOM_UTF32_BE + _t32.encode("utf-32-be"))
        (troot / "u32le_sur.txt").write_bytes(
            codecs.BOM_UTF32_LE + _t32.encode("utf-32-le") + b"\x00\xd8\x00\x00")
        (troot / "u32le_sur_bin").write_bytes(
            codecs.BOM_UTF32_LE + _t32.encode("utf-32-le") + b"\x00\xd8\x00\x00")
        (troot / "u32be_sur.md").write_bytes(
            codecs.BOM_UTF32_BE + _t32.encode("utf-32-be") + b"\x00\x00\xd8\x00")
        (troot / "u32be_sur_bin").write_bytes(
            codecs.BOM_UTF32_BE + _t32.encode("utf-32-be") + b"\x00\x00\xd8\x00")
        (troot / "u32be_bad.txt").write_bytes(
            codecs.BOM_UTF32_BE + _t32.encode("utf-32-be") + b"\x00\x11\x00\x00")
        (troot / "u32le_trunc.txt").write_bytes(
            codecs.BOM_UTF32_LE + _t32.encode("utf-32-le") + b"\x41\x00")
        # Round-5 (claude 1): the overlapping-mark case (see _read_scan_text): the kept
        # UTF-16 reading must report the planted value, with a declared suffix and without.
        _nul16 = codecs.BOM_UTF16_LE + ("\x00" + _t32).encode("utf-16-le")
        (troot / "u16le_nul.txt").write_bytes(_nul16)
        (troot / "u16le_nul_bin").write_bytes(_nul16)
        # Round-5 (codex 3): *_pre/*_mid pin the REPLACE policy itself, both byte orders: a
        # bad unit BEFORE the token and BETWEEN two tokens (a longest-valid-prefix mutant
        # loses every token after the bad unit, so each is red under it). u32be_r1/u32le_r3
        # pin the replace fallback on lengths congruent to 1 and 3 modulo 4 (u32le_trunc
        # covers remainder 2; the bad units here are four-byte-aligned, so later units stay
        # aligned, unlike the disclosed stray-mid-file-bytes residual).
        _bad_le, _bad_be = b"\x00\x00\x11\x00", b"\x00\x11\x00\x00"
        (troot / "u32le_pre.txt").write_bytes(
            codecs.BOM_UTF32_LE + _bad_le + _t32.encode("utf-32-le"))
        (troot / "u32be_pre.txt").write_bytes(
            codecs.BOM_UTF32_BE + _bad_be + _t32.encode("utf-32-be"))
        (troot / "u32le_mid.txt").write_bytes(
            codecs.BOM_UTF32_LE + _t32.encode("utf-32-le") + _bad_le + _t32.encode("utf-32-le"))
        (troot / "u32be_mid.txt").write_bytes(
            codecs.BOM_UTF32_BE + _t32.encode("utf-32-be") + _bad_be + _t32.encode("utf-32-be"))
        (troot / "u32be_r1.txt").write_bytes(
            codecs.BOM_UTF32_BE + _t32.encode("utf-32-be") + b"\x00")
        (troot / "u32le_r3.txt").write_bytes(
            codecs.BOM_UTF32_LE + _t32.encode("utf-32-le") + b"\x00\x41\x00")
        out = io.StringIO()
        with redirect_stdout(out):
            scan_rc = main(troot)
        # The non-UTF-8 decode is LOSSLESS: re-encoding with surrogateescape gives back the exact bytes
        # for distinct invalid bytes and truncated multi-byte sequences, so a lossy decode (errors="replace",
        # a latin-1 view) is red even where it would keep the same findings.
        lossless_cases = [b"a\x80b\xfe\xffc\xc0\n", b"\xe2\x82 tail\n\xf0\x9f\x98"]
        for index, data in enumerate(lossless_cases):
            path = troot / "lossless{}.md".format(index)
            path.write_bytes(data)
            texts, _note = _read_scan_text(path)
            if texts is None or texts[0].encode("utf-8", errors="surrogateescape") != data:
                failures.append("non-UTF-8 decode of {!r} must be lossless (got {!r})".format(
                    data, texts))
        # Round-5 (codex 3): the surrogatepass-then-replace ORDER, pinned at the unit level.
        # Token findings cannot discriminate surrogatepass from a replace-only decode: a lone
        # surrogate and U+FFFD are both non-word, non-break placeholders beside an ASCII token,
        # so the SURROGATE-BEARING token fixtures above (lone_sur*, u32le_sur*, u32be_sur*) stay
        # green under a replace-only mutant (round-6: NOT every assertion does; the u16le_nul
        # overlap rows go red under it, because a replace decode that succeeds never enters the
        # except branch that keeps the UTF-16 reading). The decoded
        # wide view must therefore carry the LONE CODE POINT itself (lossless for the wide
        # view), which a replace-only mutant turns into U+FFFD.
        sur_path = troot / "u32sur_unit.txt"
        sur_path.write_bytes(codecs.BOM_UTF32_LE + "x".encode("utf-32-le") + b"\x00\xd8\x00\x00")
        sur_texts, _sur_note = _read_scan_text(sur_path)
        if sur_texts is None or "\ud800" not in sur_texts[0]:
            failures.append("UTF-32 wide view must keep a lone surrogate as its lone code point "
                            "(surrogatepass first; errors=replace only when surrogatepass itself "
                            "raises), got {!r}".format(sur_texts and sur_texts[0]))
        # The SAME pin for the UTF-16 decodes, at the unit level: the decoded UTF-16 view must
        # itself carry the lone code point, little- and big-endian, and in the kept UTF-16 reading
        # of the overlapping FF FE 00 00 mark (the view at index 1 there; index 0 in the plain
        # UTF-16 branch). The surrogate-bearing token fixtures above cannot discriminate here: a
        # sanitizing decode that keeps the view but maps the lone surrogate to U+FFFD leaves every
        # ASCII token finding intact, and only these direct code-point assertions go red under it.
        u16_units = (
            ("u16sur_le_unit.txt", codecs.BOM_UTF16_LE + "x".encode("utf-16-le") + b"\x00\xd8", 0),
            ("u16sur_be_unit.txt", codecs.BOM_UTF16_BE + "x".encode("utf-16-be") + b"\xd8\x00", 0),
            ("u16sur_nul_unit.txt",
             codecs.BOM_UTF16_LE + "\x00x".encode("utf-16-le") + b"\x00\xd8", 1),
        )
        for name16, raw16, view16 in u16_units:
            path16 = troot / name16
            path16.write_bytes(raw16)
            texts16, _note16 = _read_scan_text(path16)
            if texts16 is None or len(texts16) <= view16 or "\ud800" not in texts16[view16]:
                failures.append("UTF-16 wide view ({}) must keep a lone surrogate as its lone code "
                                "point (surrogatepass, lossless for the wide view), got "
                                "{!r}".format(name16, texts16))
    scan_out = out.getvalue()
    non_utf8_cases = [
        ("  notes.md:2: GitHub token", True),            # one invalid byte BESIDE a planted token: a finding
        ("  notes.md:3: credential-named variable assigned a literal", True),  # ASSIGN beside the byte
        ("  creds.txt:1: GitHub token", True),           # the invalid byte directly adjacent: still a finding
        ("  blob:", False),                              # a true binary: never scanned, so no finding
        ("SKIP (not utf-8, no suffix: treated as binary): blob", True),  # and it is named as skipped
        ("  win16le.txt:1: credential-named variable assigned a literal", True),  # UTF-16 LE with a BOM
        ("  win16be.md:1: GitHub token", True),          # UTF-16 BE with a BOM
        ("  winkey16:1: GitHub token", True),            # a suffixless UTF-16 file is text, not binary
        ("NOTE (UTF-16 byte-order mark: decoded as UTF-16 and also scanned losslessly as bytes; "
         "findings merged): win16le.txt", True),
        ("  bom8.txt:1: GitHub token", True),            # BOM + 8-bit text: the byte view finds the token
        ("  bomodd.txt:1: GitHub token", True),          # odd length: not UTF-16; the lossless view scans it
        ("NOTE (not valid utf-8: scanned, each undecodable byte kept as a placeholder): "
         "bomodd.txt", True),
        ("  bombin:", False),                            # suffixless odd-length BOM: still a binary SKIP
        ("SKIP (not utf-8, no suffix: treated as binary): bombin", True),
        ("  dup16.txt:1: GitHub token", True),           # present in both views, counted once (see below)
        ("  lone_sur.txt:1: GitHub token", True),        # even length, unpaired surrogate after the token:
                                                         # surrogatepass keeps the wide view (round-3
                                                         # claude MEDIUM-1)
        ("  lone_sur_be.md:1: GitHub token", True),      # the same, big-endian
        ("  lone_sur_bin:1: GitHub token", True),        # the same, suffixless: two views, never binary
        ("SKIP (not utf-8, no suffix: treated as binary): lone_sur_bin", False),
        ("  u32le.txt:1: GitHub token", True),           # UTF-32-LE, tested before the UTF-16 mark it
                                                         # embeds (round-3 claude MINOR-1)
        ("NOTE (UTF-32 byte-order mark: decoded as UTF-32 and also scanned losslessly as bytes; "
         "findings merged): u32le.txt", True),
        ("  u32be.txt:1: GitHub token", True),          # the UTF-32-BE mark, pinned on its own
                                                         # (round-3 claude MINOR-1)
        ("  u32le_sur.txt:1: GitHub token", True),      # lone surrogate after the token:
                                                         # surrogatepass keeps the UTF-32 wide view
                                                         # (round-4)
        ("  u32le_sur_bin:1: GitHub token", True),      # the same, suffixless: text, never a SKIP
        ("  u32be_sur.md:1: GitHub token", True),       # the same, big-endian, declared text
        ("  u32be_sur_bin:1: GitHub token", True),      # EVEN length, suffixless, big-endian:
                                                         # round-3 judged this a binary SKIP
        ("SKIP (not utf-8, no suffix: treated as binary): u32le_sur_bin", False),
        ("SKIP (not utf-8, no suffix: treated as binary): u32be_sur_bin", False),
        ("  u32be_bad.txt:1: GitHub token", True),      # a scalar above U+10FFFF: the replace
                                                         # fallback keeps the view (round-4)
        ("  u32le_trunc.txt:1: GitHub token", True),    # a truncated final unit: the replace
                                                         # fallback keeps the view (round-4)
        ("  u16le_nul.txt:1: GitHub token", True),     # round-5: the kept UTF-16 reading of
                                                         # the overlapping FF FE 00 00 mark
        ("  u16le_nul_bin:1: GitHub token", True),      # the same, suffixless: text, no SKIP
        ("SKIP (not utf-8, no suffix: treated as binary): u16le_nul_bin", False),
        ("NOTE (UTF-32 byte-order mark overlapping a UTF-16-LE one: decoded as UTF-32 AND as "
         "UTF-16 and also scanned losslessly as bytes; findings merged): u16le_nul.txt", True),
        ("  u32le_pre.txt:1: GitHub token", True),      # round-5: a bad unit BEFORE the token
        ("  u32be_pre.txt:1: GitHub token", True),      # the same, big-endian
        ("  u32le_mid.txt:2: GitHub token", True),      # the token AFTER a bad unit between
                                                         # two tokens (replace, never prefix)
        ("  u32be_mid.txt:2: GitHub token", True),      # the same, big-endian
        ("  u32be_r1.txt:1: GitHub token", True),       # length congruent to 1 modulo 4
        ("  u32le_r3.txt:1: GitHub token", True),       # length congruent to 3 modulo 4
    ]
    if "FAIL: 30 possible hardcoded secret(s)" not in scan_out:
        failures.append("non-UTF-8 scan: expected exactly 30 merged findings (dup16.txt counted once, "
                        "never per view); output was {!r}".format(scan_out))
    if scan_rc != 1:
        failures.append("non-UTF-8 scan: main() over the fixture root must exit 1, got {}".format(scan_rc))
    for needle, want in non_utf8_cases:
        if (needle in scan_out) != want:
            failures.append("non-UTF-8 scan {!r}: want present={}, output was {!r}".format(
                needle, want, scan_out))

    # The scan reads every candidate through read_bytes_nonblocking (_read_scan_text), never a plain
    # blocking read: a FIFO with no writer at a scan candidate (under a git-ignored directory the
    # special-file precheck does not hold, or created after it ran) is refused, exit 2, at once. The
    # scan runs in a worker thread with a bound, so a revert to a blocking read is red here (the FIFO
    # is then opened for writing to release the stuck reader) instead of hanging the self-test.
    import os
    import threading
    from contextlib import redirect_stderr
    fifo_cases = 0
    if hasattr(os, "mkfifo"):
        with tempfile.TemporaryDirectory(prefix="check-secrets-fifo-") as tmp:
            froot = Path(tmp)
            (froot / "ignored").mkdir()
            fifo = froot / "ignored" / "notes.txt"
            os.mkfifo(str(fifo))
            outcome = []

            def _fifo_scan():
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    outcome.append(main(froot))
            worker = threading.Thread(target=_fifo_scan, daemon=True)
            worker.start()
            worker.join(30)
            fifo_cases = 1
            if worker.is_alive():
                failures.append("a FIFO at a scan candidate blocked the scan for 30 s (a plain blocking "
                                "read); it must be refused at once, exit 2")
                try:
                    os.close(os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK))
                except OSError:
                    pass
                worker.join(5)
            elif outcome != [2]:
                failures.append("a FIFO at a scan candidate must be refused with exit 2, got "
                                "{!r}".format(outcome))
    else:
        failures.append("the FIFO scan vector was NOT RUN (os.mkfifo unavailable); failing closed")

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  " + f)
        return 1
    print("SELF-TEST PASS: {} ASSIGN + {} scan-candidate + {} PREFIXES + {} provider-label + {} "
          "provider-neg + {} ENTROPY + {} entropy-unit + {} non-UTF-8 scan + {} lossless-decode + {} "
          "FIFO-scan cases".format(
              len(cases), len(names), len(prefix_cases), len(label_cases), len(label_neg),
              len(entropy_cases), len(entropy_units), len(non_utf8_cases), len(lossless_cases),
              fifo_cases))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("check_secrets.py") or main())
