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
"""
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
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "JWT (JSON Web Token)"),
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

    failures = []

    def assign_hit(line):
        return any(_assign_is_secret(m) for m in ASSIGN.finditer(line))

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

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  " + f)
        return 1
    print("SELF-TEST PASS: {} ASSIGN + {} scan-candidate + {} PREFIXES cases".format(
        len(cases), len(names), len(prefix_cases)))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main())
