---
corpus-id: secred
origin: pack
family: security
facet: SECI
secondary: [SECC, SECP]
slug: log-redaction
map-nist-airmf: [MAP 4.2]
map-nist-80053: [AU-3(3)]
map-nist-ssdf: [PW.5.1]
map-atlas: [AML.T0055, AML.T0063]
map-iso-42001: [A.6.2.8]
map-iso-23894: [A.8, A.11]
map-owasp-asvs: [V16]
---

# Redact sensitive content from logs

Logs record events, not the raw sensitive content of prompts, arguments, or results. Secrets and personal
data are redacted before anything is written, never cleaned up after the fact.
