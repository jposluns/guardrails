---
corpus-id: seclpr
origin: pack
family: security
facet: SECC
secondary: [SECI]
slug: least-privilege-retrieval
map-nist-airmf: [MAP 4.2]
map-nist-80053: [AC-3, AC-6]
map-atlas: [AML.T0053, AML.T0082, AML.T0085]
map-owasp-mcp: [MCP07]
map-owasp-asi: [ASI03]
---

# Retrieval enforces the requester's authorization

Retrieval and tool access enforce the requester's own authorization, not the assistant's broader access, so a
person can never reach through the assistant to data or systems they could not reach directly.
