---
corpus-id: secsec
origin: pack
family: security
facet: SECC
secondary: [SECI]
slug: keep-secrets-out
map-owasp-llm: [LLM02]
map-owasp-mcp: [MCP01]
map-owasp-asvs: [V14]
map-owasp-cheatsheet: [secrets-management]
---

# Keep secrets out

No credential, token, key, or other secret is committed to a repository or written to any shared or persisted
location, including prompts, logs, transcripts, tool output, and generated files. Pattern scanning and a leak
gate are compensating controls, never a substitute for keeping secrets out in the first place.
