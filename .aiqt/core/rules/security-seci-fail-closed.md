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

Catching exceptions does not make such a check fail closed over code it loads into its own process: that
code can also end the process with any status, replace the machinery the check reports through, or fault
outside any handler during formatting, cleanup, background work, or shutdown. Unless that code is reviewed
and pinned with the check, it runs in a separate process whose verdict is read under the gate-discipline
rule, or it stays in process only with each such channel recorded as covered, unreachable, or disclosed, and
a channel that can turn a fault into a pass is never left merely disclosed.
