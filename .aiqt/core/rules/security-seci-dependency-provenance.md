---
corpus-id: secsup
origin: pack
family: security
facet: SECI
slug: dependency-provenance
map-nist-airmf: [GOVERN 6.1, MANAGE 3.1, MAP 4.2]
map-nist-80053: [SR-3, SR-4, SR-11]
map-nist-ssdf: [PO.3.2, PW.4.1, PW.4.4]
map-atlas: [AML.T0010.001, AML.T0010.003, AML.T0010.005, AML.T0011.000, AML.T0011.001, AML.T0011.002, AML.T0018.002, AML.T0104, AML.T0109, AML.T0111, AML.T0112.001]
map-owasp-llm: [LLM04]
map-owasp-asi: [ASI04]
map-owasp-mcp: [MCP04, MCP09]
map-owasp-web: [A03, A08]
---

# Trusted, pinned dependency provenance

Dependencies, tools, external servers, and any model or artefact file that executes on load come from trusted
sources with pinned provenance. A file that runs code when loaded is scanned before use, and none is
introduced on the strength of its name or popularity alone.
