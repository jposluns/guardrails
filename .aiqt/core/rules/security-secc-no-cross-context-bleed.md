---
corpus-id: secncb
origin: pack
family: security
facet: SECC
secondary: [SECP]
slug: no-cross-context-bleed
map-owasp-mcp: [MCP10]
---

# No cross-context bleed

Context assembled for one task, user, tenant, or trust boundary is not carried into another. Each new task or
session starts from a clean boundary, so information gathered under one authorization never surfaces in a
response served under a different one.
