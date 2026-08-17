---
corpus-id: secres
origin: pack
family: security
facet: SECA
secondary: [COST]
slug: resource-bounds
map-nist-airmf-broad: [MAP 4.2]
map-nist-80053-tight: [SC-5, SC-6]
map-atlas-tight: [AML.T0029, AML.T0034.000, AML.T0034.001, AML.T0034.002]
map-atlas-broad: [AML.T0046]
map-iso-42001-broad: [A.6.2.6]
map-iso-23894-broad: [A.9, A.11, B.4]
map-owasp-llm-tight: [LLM06]
map-owasp-asi-broad: [ASI08]
map-owasp-api-tight: [API4]
map-csa-ccm-broad: [I&S-02]
map-csa-aicm-broad: [AIS-13, MDS-11]
---

# Bounded consumption and safe failure

Tool-call depth and recursion, token and cost budgets, and request rate are bounded, and the assistant
fails safe when a bound is reached rather than continuing unchecked. Loops that call tools or spawn work
carry a limit and a timeout, so a manipulated or runaway agent cannot exhaust resources, run up cost, or
cascade a failure across a system.
