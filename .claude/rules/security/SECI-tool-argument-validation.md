---
corpus-id: sectvl
origin: pack
family: security
facet: SECI
slug: tool-argument-validation
map-nist-80053: [SI-10, SI-10(6)]
map-atlas: [AML.T0050, AML.T0102]
map-iso-23894: [A.11]
map-owasp-asi: [ASI05]
map-owasp-mcp: [MCP05]
map-owasp-web: [A05]
map-owasp-asvs: [V2]
---

# Validate tool arguments before use

Every argument the assistant passes to a tool, shell, database, or file operation is validated against an
expected schema before use. A command, query, path, or request is never assembled directly from unvalidated
model output or untrusted content, which is how command, query, and path-traversal injection occur.
