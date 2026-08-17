---
corpus-id: sectok
origin: pack
family: security
facet: SECI
secondary: [SECC]
slug: session-token-management
map-nist-80053-tight: [SC-23, SC-23(1)]
map-nist-80053-broad: [IA-5]
map-nist-ssdf-broad: [PW.5.1]
map-atlas-broad: [AML.T0055, AML.T0091.000, AML.T0091.001, AML.T0113]
map-iso-23894-broad: [A.11]
map-owasp-asvs-tight: [V7, V9]
map-owasp-cheatsheet-tight: [session-management]
map-csa-ccm-tight: [IAM-14]
map-csa-ccm-broad: [CEK-03, IAM-13]
map-csa-aicm-tight: [IAM-14]
map-csa-aicm-broad: [CEK-03, IAM-13]
---

# Secure session and token handling

Code the assistant writes and operations it performs handle sessions and tokens securely. Tokens carry
sufficient entropy, are transmitted and stored safely, are scoped and time-limited, and are invalidated on
logout, rotation, or suspected compromise.
