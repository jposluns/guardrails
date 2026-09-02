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
  R5 secrets split across lines, concatenated, encoded, or carried in a binary/non-UTF-8 file: unchanged,
     gitleaks-or-nothing territory.
  R6 the F-127 env-reference exclusion residual: unchanged, and it now ALSO governs the entropy path's
     unquoted branch.
  R7 a high-entropy value that is actually a filesystem path, URL fragment, or opaque identifier assigned
     to a GENERIC credential component (a bare `client`/`key`/`auth`) can over-fire: safe-direction NOISE (a
     false positive, never a miss), parity-preserving, no live-repo instance; the 40-char floor damps it.
"""
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

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


def _is_scan_candidate(path):
    """A file is scanned when it is a dotenv file (name `.env` or starting `.env.`, case-insensitive, such
    as `.env.local` or `.env.production`, which hold real credentials), has a suffix in TEXT_SUFFIXES, or
    has no suffix at all (an extensionless PEM key like id_rsa). Suffix matching is case-insensitive, so a
    `.PEM` or `.TXT` file scans too. A file whose suffix is NOT in TEXT_SUFFIXES is skipped up front; a
    suffixless file that is NON-UTF-8 binary is read and skipped later on a UnicodeDecodeError (a
    UTF-8-decodable suffixless file is scanned). TEXT_SUFFIXES is a curated allow-list, so a text format
    not on it is skipped here and left to gitleaks; add it to TEXT_SUFFIXES to cover it."""
    lowered = path.name.lower()
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    suffix = path.suffix.lower()
    return not suffix or suffix in TEXT_SUFFIXES


def main() -> int:
    error = _validate_entropy_constants()
    if error is not None:
        print(f"error: invalid entropy control constant ({error}); fail-closed", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    findings = []
    try:
        for path in sorted(walk_files(root, SKIP_DIRS)):
            if path.relative_to(root).as_posix() in SKIP_PATHS:
                continue
            if path.relative_to(root).as_posix() in EXEMPT_PATHS:
                continue
            if not _is_scan_candidate(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                # binary / non-utf8: a text secret-scanner skips it (gitleaks scans binaries)
                print(f"SKIP (not utf-8 text): {path.relative_to(root)}")
                continue
            for number, line in enumerate(lines, 1):
                rel = path.relative_to(root)
                before = len(findings)
                for pattern, label in PREFIXES:
                    if pattern.search(line):
                        findings.append(f"{rel}:{number}: {label}")
                # Scan EVERY credential-named assignment on the line, not just the first: a
                # placeholder assignment earlier on the line must not mask a real one after it.
                # One finding per line suffices, so append on the first real match and stop.
                for match in ASSIGN.finditer(line):
                    if _assign_is_secret(match):
                        findings.append(
                            f"{rel}:{number}: credential-named variable assigned a literal"
                        )
                        break
                # GD-121: only when neither a provider prefix nor a credential-named ASSIGN flagged this
                # line, try the entropy-gated generic detector. One finding per line; the first real
                # match wins, and finditer skips earlier placeholder/metadata matches so they cannot
                # mask it (parity with the ASSIGN scan-all-assignments behaviour above).
                if len(findings) == before:
                    for match in ENTROPY_ASSIGN.finditer(line):
                        if _entropy_assign_is_secret(match):
                            findings.append(
                                f"{rel}:{number}: high-entropy literal assigned to a credential-like name"
                            )
                            break
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
    # bad hand-edit like ENTROPY_MIN_LEN = True cannot silently disable or invert the guard.
    import builtins as _b
    for _name in ("ENTROPY_MIN_LEN", "ENTROPY_MAX_LEN", "ENTROPY_THRESHOLD"):
        _saved = globals()[_name]
        globals()[_name] = True
        if _validate_entropy_constants() is None:
            failures.append("entropy invariant must reject a boolean {}".format(_name))
        globals()[_name] = _saved

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  " + f)
        return 1
    print("SELF-TEST PASS: {} ASSIGN + {} scan-candidate + {} PREFIXES + {} provider-label + {} "
          "provider-neg + {} ENTROPY + {} entropy-unit cases".format(
              len(cases), len(names), len(prefix_cases), len(label_cases), len(label_neg),
              len(entropy_cases), len(entropy_units)))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main())
