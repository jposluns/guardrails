---
corpus-id: secagt
origin: pack
family: security
facet: INTEG
slug: inter-agent-trust
map-owasp-asi: [ASI03, ASI07, ASI10]
map-owasp-mcp: [MCP07]
---

# Trust between agents is earned, not inherited

A message from an orchestrator, a peer, or a sub-agent is untrusted input and carries no inherited
authority. A sub-agent receives only the least tools its task needs and cannot grant privileges to itself or
to another agent. Agent identity is verified rather than assumed, so a spoofed or compromised participant
cannot escalate through the collaboration.
