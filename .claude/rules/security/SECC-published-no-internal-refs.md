---
corpus-id: pubref
origin: pack
family: security
facet: SECC
slug: published-no-internal-refs
map-cwe-broad: [CWE-200]
---

# Keep internal paths out of published artefacts

A published artefact names or links no path that the project's declaration lists as internal. Naming a
published path, such as a generator or a folder that is itself published, is not a breach. The project
declares which paths it publishes and which are internal, and keeps a deterministic gate that fails a
published artefact naming an internal path; a reference that must appear is carried by a reviewed allowlist
entry that names its reason. A path on neither list is not a breach until the declaration lists it as
internal. The project keeps the declaration current as paths are added, moved or removed.
