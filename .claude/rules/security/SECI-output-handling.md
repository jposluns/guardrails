---
corpus-id: secout
origin: pack
family: security
facet: SECI
secondary: [QUALI]
slug: output-handling
map-owasp-llm: [LLM10]
map-owasp-asvs: [V1, V2]
map-owasp-web: [A05]
map-owasp-proactive: [C3]
map-owasp-cheatsheet: [cross-site-scripting-prevention, injection-prevention]
---

# Generated output is untrusted input

Everything the assistant produces, whether code, configuration, commands, queries, or markup, is untrusted
input to whatever will consume it. It is validated, encoded for its destination, and never executed or
trusted merely because the assistant produced it. The project's review, testing, and static-analysis gates
apply to generated artefacts exactly as they apply to human-written ones; no check is waived on the grounds
that the output came from a model.
