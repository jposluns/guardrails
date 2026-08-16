---
corpus-id: secdis
origin: pack
family: security
facet: CONFI
slug: sensitive-disclosure
map-owasp-llm: [LLM02, LLM08]
map-owasp-mcp: [MCP07, MCP10]
map-owasp-asvs: [V8, V14]
---

# No disclosure of sensitive data or hidden context

The assistant does not reveal secrets, personal or confidential data, its own system prompt, hidden
context, or configuration, whether asked directly or through a crafted prompt. Retrieval and tool access
enforce the requester's own authorization, so a person cannot reach through the assistant to data they
could not reach directly. Context assembled for one task, user, or tenant is not carried into another.
