---
corpus-id: chkfcl
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: check-fails-closed-on-unreadable
map-nist-80053: [SI-17]
map-owasp-cheatsheet: [error-handling]
---

# A check fails closed on input it cannot read

A gate, validator, scan, or traversal that cannot access, read, or list an input it is meant to cover
reports that as a failure, never as an absent, empty, or clean input. An operation that silently yields
nothing on a permission or I/O error, a glob or a listing that returns empty, or an existence check that
returns false, is made to surface the error, so an unreadable input can never read as nothing to check
and pass.
