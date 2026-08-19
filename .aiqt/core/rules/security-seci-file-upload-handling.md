---
corpus-id: secupl
origin: pack
family: security
facet: SECI
slug: file-upload-handling
map-owasp-asvs-tight: [V5]
map-owasp-cheatsheet-tight: [file-upload]
---

# Validate and contain uploaded files

A file received from an untrusted source is validated by its actual content, never by its declared
name, extension, or content type, and its size and archive expansion are bounded before processing.
It is stored under a generated name outside any executable or directly served path and served back
only with a safe, explicit content type, so an upload can never become code on the system that
accepted it.
