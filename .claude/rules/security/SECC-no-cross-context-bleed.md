---
corpus-id: secncb
origin: pack
family: security
facet: SECC
secondary: [SECP]
slug: no-cross-context-bleed
map-nist-airmf: [MAP 4.2]
map-nist-80053: [AC-4, SC-4]
map-atlas: [AML.T0057, AML.T0080]
map-owasp-mcp: [MCP10]
---

# No cross-context bleed

Context assembled for one task, user, tenant, or trust boundary is not carried into another. Each new task or
session starts from a clean boundary, so information gathered under one authorization never surfaces in a
response served under a different one.
