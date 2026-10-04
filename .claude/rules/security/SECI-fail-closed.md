---
corpus-id: secfcl
origin: pack
family: security
facet: SECI
slug: fail-closed
map-cwe-tight: [CWE-636]
map-owasp-web-tight: [A10]
map-owasp-cheatsheet-broad: [error-handling]
---

# Fail closed in security-relevant paths

An exception or error in an authentication, authorization, validation, or cryptographic check leaves the
system in the deny or otherwise safe state. A failed, unavailable, or unreadable check is treated as not
passed, never as a default-allow.

Code that such a check loads can exit with any status, fault outside any handler (error formatting,
cleanup, background work, or shutdown), or replace the check's reporting machinery; review and pinning with
the check exempt only the replacement channel from the in-process duty. Run in a child process, such code
yields a passing verdict only with a complete structured result, a zero exit, and no fault reported on an
error stream it cannot redirect or silence. In process, each channel is covered, unreachable, or disclosed
under the gate-discipline rule, a fault-to-pass channel never merely disclosed.
