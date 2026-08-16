---
corpus-id: secmin
origin: pack
family: security
facet: PRIV
slug: data-minimization
map-owasp-llm: [LLM02]
map-owasp-asvs: [V14]
---

# Minimize and protect personal and sensitive data

Only the personal and sensitive data a task genuinely needs is sent to or retained by an AI service, and it
is redacted or pseudonymized before it leaves the trust boundary wherever practical. Data residency,
retention limits, and deletion requests are honoured, and raw prompts, tool arguments, and results carrying
personal data are not written to logs. Minimizing what is exposed is preferred to controlling exposure after.
