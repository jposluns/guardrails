---
corpus-id: secout
origin: pack
family: security
facet: SECI
secondary: [QUALI]
slug: output-handling
map-nist-airmf: [GOVERN 4.1, MAP 4.2, MEASURE 2.9]
map-nist-80053: [SA-11, SI-10(6), SI-15]
map-nist-ssdf: [PW.5.1, PW.7.1, PW.8.1]
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
