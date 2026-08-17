---
corpus-id: seclpt
origin: pack
family: security
facet: SECI
secondary: [SECC, TRUST]
slug: least-privilege-tools
map-nist-airmf: [MAP 4.2]
map-nist-80053: [AC-6, CM-7, SC-39]
map-atlas: [AML.T0053, AML.T0086, AML.T0098, AML.T0101, AML.T0112.000]
map-iso-23894: [A.11]
map-owasp-llm: [LLM03]
map-owasp-asi: [ASI02]
map-owasp-mcp: [MCP02]
---

# Least-privilege tool and file access

The assistant operates with the least tool and file access its task requires, and no more, with grants scoped
to that task rather than held as standing privilege. It neither expands its own authority nor acts beyond the
work it was asked to do. Where the platform allows it, this scope is enforced by sandboxing or isolating
tool execution, not left to policy alone.
