---
corpus-id: secsec
origin: pack
family: security
facet: SECC
secondary: [SECI]
slug: keep-secrets-out
map-nist-airmf-broad: [MAP 4.2]
map-nist-80053-tight: [IA-5(7)]
map-nist-ssdf-broad: [PW.5.1]
map-atlas-tight: [AML.T0055, AML.T0095.000]
map-atlas-broad: [AML.T0082, AML.T0083, AML.T0098]
map-iso-23894-broad: [A.11]
map-owasp-llm-broad: [LLM02]
map-owasp-mcp-tight: [MCP01]
map-owasp-asvs-broad: [V14]
map-owasp-cheatsheet-tight: [secrets-management]
---

# Keep secrets out

No credential, token, key, or other secret is committed to a repository or written to any shared or persisted
location, including prompts, logs, transcripts, tool output, and generated files. Pattern scanning and a leak
gate are compensating controls, never a substitute for keeping secrets out in the first place.
