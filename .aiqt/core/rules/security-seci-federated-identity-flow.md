---
corpus-id: secfid
origin: pack
family: security
facet: SECI
slug: federated-identity-flow
map-owasp-asvs: [V9, V10]
map-owasp-proactive: [C7]
map-owasp-cheatsheet: [json-web-token, oauth2]
---

# Validate federated identity and token flows

Code the assistant writes that implements OAuth, OIDC, or JWT-based authentication validates the full
protocol flow before granting access: the token issuer, audience, signature, and expiry; the state and
nonce; the redirect binding and PKCE where applicable; and that the granted scopes match what the exact
relying party requested. A token is trusted only after every one of these checks passes.
