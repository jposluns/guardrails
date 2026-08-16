---
corpus-id: secvde
origin: pack
family: security
facet: SECI
secondary: [ACCUR]
slug: verify-dependency-exists
map-owasp-llm: [LLM04]
---

# Verify a dependency exists before adding it

A dependency the assistant proposes is verified to exist in an approved registry before it is added, so an
invented or typosquatted package name is never introduced. Existence and identity are confirmed against the
registry itself, not assumed from a plausible-looking name in generated text.
