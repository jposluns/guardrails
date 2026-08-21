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
is a pure dotted-identifier path AND carries an `.env.` accessor segment is treated as a reference, not a
secret. Residual: a real secret is missed only if it is unquoted, a pure dotted identifier, and itself
carries an `.env.` segment, or if ASSIGN captures an `.env.`-carrying dotted PREFIX of a longer token a
non-value character truncated; both are narrow and unusual, and gitleaks scans them regardless. A long
dotted config path with no `.env.` accessor is still flagged (a false positive in the safe direction).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".working"}
SKIP_NAMES = {"check_secrets.py"}

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
        (?P<q>['"])(?P<qvalue>[^'"\n]{12,})(?P=q)    # quoted
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
# a pure dotted-identifier path (DOTTED_PATH) whose accessor is an `env` segment (_ENV_ACCESSOR). It is a
# CODE REFERENCE to a variable, not a literal secret, so it is excluded from the unquoted credential
# match (F-127). The `env` accessor is REQUIRED, not merely a dotted shape, so a dotted token that only
# looks identifier-shaped - a HashiCorp Vault `hvs.<random>` token, a PASETO `v2.local.<payload>`, or
# `prod.secret.auth.<value>` - stays caught.
DOTTED_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_ENV_ACCESSOR = re.compile(r"(?i)(?:^|\.)env\.")


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
        if DOTTED_PATH.fullmatch(value) and _ENV_ACCESSOR.search(value):
            return False
    return not PLACEHOLDER.match(value)


def _is_scan_candidate(path):
    """A file is scanned when it is a dotenv file (name `.env` or starting `.env.`, such as `.env.local`
    or `.env.production`, which hold real credentials), has a known text suffix, or has no suffix at all
    (an extensionless PEM key like id_rsa). A binary with no known suffix is still skipped later on a
    UnicodeDecodeError; only a KNOWN non-text suffix (.png, .pdf) is skipped up front."""
    name = path.name
    if name == ".env" or name.startswith(".env."):
        return True
    suffix = path.suffix
    return not suffix or suffix in TEXT_SUFFIXES


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    try:
        for path in sorted(walk_files(root, SKIP_DIRS)):
            if path.name in SKIP_NAMES:
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
    without them. Secret-shaped fixtures are assembled from parts so no contiguous secret literal appears
    in this source (SECP). Exit 0 on PASS, 1 on FAIL."""
    from pathlib import PurePath

    failures = []

    def assign_hit(line):
        return any(_assign_is_secret(m) for m in ASSIGN.finditer(line))

    real = "A7bce9f2Xk1mNq8Z"                          # 16 mixed alnum chars, real-length, not placeholder
    vault = "hvs." + "CvmS4c0DPTvHv5eJgXWMJg9r"          # HashiCorp Vault hvs.<random> shape (dotted, real)
    paseto = "v2." + "local." + "abcd1234efgh5678"       # PASETO-style dotted token
    prodsec = "prod.secret.auth." + "a1b2c3d4e5f6"        # dotted non-env provider secret
    longcfg = "application.services.oauth2.client.credentials.providerToken1"  # long dotted config, no env
    cases = [
        ("password = " + real, True),                        # unquoted real literal: caught
        ("token = process.env.OPENAI_KEY_V2", False),         # F-127 env lookup: excluded
        ("api_key = import.meta.env.VITE_API_KEY2", False),   # env lookup (Vite): excluded
        ("secret = env.SECRET_VALUE2", False),                # leading env accessor: excluded
        ("token = " + vault, True),                          # Vault token, dotted but no env: CAUGHT
        ("api_key = " + paseto, True),                       # PASETO token, dotted but no env: CAUGHT
        ("api_key = " + prodsec, True),                      # dotted non-env secret: CAUGHT
        ("secret = " + longcfg, True),                       # long dotted config, no env: CAUGHT (safe)
        ('secret = "' + real + '"', True),                   # quoted real literal: caught
        ("password = process_env_KEY2", True),               # single identifier, not dotted: caught
        ('password = "xxxxxxxxxxxx"', False),                # quoted placeholder: excluded
    ]
    for line, want in cases:
        got = assign_hit(line)
        if got != want:
            failures.append("ASSIGN {!r}: want {}, got {}".format(line, want, got))

    # scan-candidate keys on the file NAME, so dotenv variants scan even when the last suffix is unknown;
    # ("id_rsa", True) guards the extensionless fix; (".env.local", True) guards the dotenv-by-name fix.
    names = [("id_rsa", True), ("notes.md", True), (".env", True), (".env.local", True),
             (".env.production", True), ("image.png", False), ("doc.pdf", False)]
    for name, want in names:
        got = _is_scan_candidate(PurePath(name))
        if got != want:
            failures.append("scan-candidate {!r}: want {}, got {}".format(name, want, got))

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  " + f)
        return 1
    print("SELF-TEST PASS: {} ASSIGN + {} scan-candidate cases".format(len(cases), len(names)))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main())
