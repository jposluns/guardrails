---
corpus-id: secau1
origin: pack
family: security
facet: INTEG
slug: authentication-authorization
map-owasp-web: [A01, A07]
map-owasp-api: [API1, API2, API5]
map-owasp-asvs: [V6, V7, V8]
map-owasp-proactive: [C1, C7]
---

# Strong authentication and least-privilege authorization

Code the assistant writes and operations it performs enforce strong authentication, least-privilege
authorization checked at every access, and secure session and token handling. Authorization is verified on
each request at the object and function level rather than inferred from an earlier step, so a caller cannot
reach data or actions beyond their rights.
