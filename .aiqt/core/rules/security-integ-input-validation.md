---
corpus-id: secinp
origin: pack
family: security
facet: INTEG
slug: input-validation
map-owasp-web: [A05]
map-owasp-asvs: [V1, V2]
map-owasp-proactive: [C3]
---

# Validate input and encode output at every boundary

External input is validated for type, range, and format where it enters, and output is encoded for the
specific sink that will consume it. Validation prefers an allow-list, and the injection classes, whether
SQL, operating-system command, markup, or template, are prevented by construction rather than filtered
after the fact.
