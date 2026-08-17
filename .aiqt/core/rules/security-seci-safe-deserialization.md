---
corpus-id: secdsz
origin: pack
family: security
facet: SECI
slug: safe-deserialization
map-owasp-web: [A08]
map-owasp-asi: [ASI05]
map-owasp-cheatsheet: [deserialization]
---

# Deserialize untrusted data only as data

Data from an untrusted source is never passed to a deserializer that can instantiate arbitrary types or
run code during parsing, such as pickle, Java native serialization, PHP unserialize, or unsafe YAML. A
data-only format or a schema-bound parser is used instead, so parsing cannot become execution.
