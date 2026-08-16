---
corpus-id: secrot
origin: pack
family: security
facet: SECC
secondary: [SECI]
slug: rotate-leaked-secret
map-nist-airmf: [MANAGE 2.3, MANAGE 4.3]
map-nist-80053: [IA-5, IR-4]
map-atlas: [AML.T0012, AML.T0091.000, AML.T0091.001]
map-iso-23894: [A.11]
map-owasp-mcp: [MCP01]
map-owasp-cheatsheet: [secrets-management]
---

# Rotate a leaked secret

A secret that reaches a remote or an external service is treated as compromised and rotated, whatever any
scanner said. Rotation happens regardless of whether the exposure was intentional, brief, or already deleted
from the destination.
