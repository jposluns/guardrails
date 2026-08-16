---
corpus-id: seclog
origin: pack
family: security
facet: INTEG
slug: logging-audit
map-owasp-web: [A09]
map-owasp-asvs: [V16]
map-owasp-proactive: [C9]
map-owasp-mcp: [MCP08]
---

# Security logging and auditability without leaking data

Security-relevant events, including authentication, authorization, and privileged actions, are logged with
enough context to investigate, and the human, agent, and tool chain behind an action stays traceable. Logs
record events, not the raw sensitive content of prompts, arguments, or results; secrets and personal data
are redacted before anything is written.
