---
corpus-id: secrot
origin: pack
family: security
facet: CONFI
slug: rotate-leaked-secret
map-owasp-mcp: [MCP01]
map-owasp-cheatsheet: [secrets-management]
---

# Rotate a leaked secret

A secret that reaches a remote or an external service is treated as compromised and rotated, whatever any
scanner said. Rotation happens regardless of whether the exposure was intentional, brief, or already deleted
from the destination.
