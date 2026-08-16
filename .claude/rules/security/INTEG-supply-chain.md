---
corpus-id: secsup
origin: pack
family: security
facet: INTEG
slug: supply-chain
map-owasp-llm: [LLM04]
map-owasp-asi: [ASI04]
map-owasp-mcp: [MCP04, MCP09]
map-owasp-web: [A03, A08]
---

# Trusted, verified software supply chain

Dependencies, tools, external servers, and any model or artefact file that executes on load come from
trusted sources with pinned provenance. A dependency the assistant proposes is verified to exist in an
approved registry before it is added, so an invented or typosquatted name is never introduced. Files that
run code when they are loaded are scanned before use. What enters the project has its provenance checked,
not assumed.
