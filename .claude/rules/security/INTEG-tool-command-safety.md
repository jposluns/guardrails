---
corpus-id: sectcs
origin: pack
family: security
facet: INTEG
slug: tool-command-safety
map-owasp-asi: [ASI05]
map-owasp-mcp: [MCP05]
map-owasp-web: [A05]
map-owasp-asvs: [V2]
---

# Validate tool calls; never build commands from unvalidated output

Every argument the assistant passes to a tool, shell, database, or file operation is validated against an
expected schema before use. A command, query, path, or request is never assembled directly from unvalidated
model output or untrusted content, which is how command, query, and path-traversal injection occur. Where
the platform allows it, tool execution is sandboxed and bounded so a single call cannot reach beyond its task.
