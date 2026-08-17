---
corpus-id: secssr
origin: pack
family: security
facet: SECI
slug: ssrf-prevention
map-owasp-api-tight: [API7]
map-owasp-proactive-tight: [C10]
map-owasp-cheatsheet-tight: [server-side-request-forgery-prevention]
---

# Validate server-initiated requests

Code the assistant writes that makes a server-initiated request to an externally-influenced URL validates
the destination before the request is made, preferring an allow-list over a deny-list. Internal,
loopback, link-local, and cloud-metadata address ranges are denied, and a redirect is not followed to a
target outside the allowed set.
