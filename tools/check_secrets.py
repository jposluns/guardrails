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
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}"), "Anthropic key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    try:
        for path in sorted(walk_files(root, SKIP_DIRS)):
            if path.name in SKIP_NAMES:
                continue
            if path.relative_to(root).as_posix() in EXEMPT_PATHS:
                continue
            if path.suffix not in TEXT_SUFFIXES:
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
                    value = match.group("qvalue") or match.group("value") or ""
                    value = value.strip()
                    # An UNQUOTED value must additionally look like a credential (letters AND
                    # digits), because an unquoted match is far likelier to be ordinary prose
                    # or code than a quoted one. Quoted values keep the looser bar.
                    if value and match.group("qvalue") is None:
                        if not (any(c.isalpha() for c in value) and any(c.isdigit() for c in value)):
                            value = ""
                    if value and not PLACEHOLDER.match(value):
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


if __name__ == "__main__":
    sys.exit(main())
