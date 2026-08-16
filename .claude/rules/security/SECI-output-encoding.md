---
corpus-id: secenc
origin: pack
family: security
facet: SECI
slug: output-encoding
map-nist-ssdf: [PW.5.1]
map-owasp-asvs: [V1]
map-owasp-cheatsheet: [cross-site-scripting-prevention]
---

# Encode output for its sink

Output is encoded for the specific sink that will consume it, such as HTML, SQL, a shell, or a template
engine. Encoding is chosen by destination rather than applied generically, since encoding for the wrong sink
still leaves the actual sink exploitable.
