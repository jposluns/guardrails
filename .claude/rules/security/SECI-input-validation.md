---
corpus-id: secinp
origin: pack
family: security
facet: SECI
slug: input-validation
map-owasp-web: [A05]
map-owasp-asvs: [V2]
map-owasp-proactive: [C3]
---

# Validate external input at the boundary

External input is validated for type, range, and format at the point where it enters, preferring an
allow-list over a deny-list. The injection classes, whether SQL, operating-system command, markup, or
template, are prevented by construction rather than filtered after the fact.
