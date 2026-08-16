---
corpus-id: secmin
origin: pack
family: security
facet: SECP
secondary: [SECC]
slug: data-minimization
map-nist-airmf: [MAP 4.2]
map-nist-80053: [SI-12(1), SI-19]
map-owasp-llm: [LLM02]
map-owasp-asvs: [V14]
---

# Minimize personal data sent to AI services

Only the personal and sensitive data a task genuinely needs is sent to or retained by an AI service, and
minimizing what is exposed is preferred to controlling exposure after the fact. What is sent is redacted or
pseudonymized before it leaves the trust boundary wherever practical.
