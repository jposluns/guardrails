---
corpus-id: secinp
origin: pack
family: security
facet: SECI
slug: input-validation
map-nist-80053: [SI-10, SI-10(5), SI-10(6)]
map-nist-ssdf: [PW.5.1]
map-atlas: [AML.T0049, AML.T0050]
map-iso-23894: [A.9, A.11]
map-owasp-web: [A05]
map-owasp-cheatsheet: [input-validation]
map-owasp-asvs: [V2]
map-owasp-proactive: [C3]
---

# Validate external input at the boundary

External input is validated for type, range, and format at the point where it enters, preferring an
allow-list over a deny-list. The injection classes, whether SQL, operating-system command, markup, or
template, are prevented by construction rather than filtered after the fact.
