---
corpus-id: prsunc
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [ACCUR]
slug: preserve-uncommitted-work
---

# Preserve uncommitted work

A command that reverts a file to its committed state discards every uncommitted change in it, including a real fix that has not been committed yet. When undoing a temporary or experimental change, revert only those specific lines, or commit the genuine change first; never blind-revert a whole file (git checkout -- <path>, git restore <path>, git reset --hard) whose uncommitted work you still need. A discard is safe only once the work you intend to keep is already committed or independently saved.
