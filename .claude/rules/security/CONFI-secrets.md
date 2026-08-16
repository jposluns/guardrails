---
corpus-id: secsec
origin: pack
family: security
facet: CONFI
slug: secrets
map-owasp-llm: [LLM02]
map-owasp-mcp: [MCP01]
map-owasp-asvs: [V11, V14]
map-owasp-web: [A04]
map-owasp-proactive: [C2]
map-owasp-cheatsheet: [secrets-management, key-management]
---

# Secrets

No credential, token, key, or other secret is committed to a repository or written to any shared or
persisted location, including prompts, logs, transcripts, tool output, and generated files. A secret that
reaches a remote or an external service is treated as COMPROMISED and rotated, whatever any scanner said.
Pattern scanning and a leak gate are compensating controls, never a substitute for keeping secrets out.
