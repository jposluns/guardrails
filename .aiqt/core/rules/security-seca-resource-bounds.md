---
corpus-id: secres
origin: pack
family: security
facet: SECA
slug: resource-bounds
map-owasp-llm: [LLM06]
map-owasp-asi: [ASI08]
map-owasp-api: [API4]
---

# Bounded consumption and safe failure

Tool-call depth and recursion, token and cost budgets, and request rate are bounded, and the assistant
fails safe when a bound is reached rather than continuing unchecked. Loops that call tools or spawn work
carry a limit and a timeout, so a manipulated or runaway agent cannot exhaust resources, run up cost, or
cascade a failure across a system.
