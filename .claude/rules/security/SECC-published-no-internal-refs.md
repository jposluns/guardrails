---
corpus-id: pubref
origin: pack
family: security
facet: SECC
slug: published-no-internal-refs
map-cwe-broad: [CWE-200]
---

# Keep internal paths out of published artefacts

A published artefact names or links no path the project declares internal, that is, not part of what it
publishes. Naming a published file, such as a generator that is itself published, is not a breach. The
project declares which paths it publishes and which are internal, and keeps a deterministic gate that fails a
published artefact naming an internal path; a reference that must appear is carried by a reviewed allowlist
entry that names its reason.
