---
corpus-id: prtbrn
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [TRUST]
slug: protected-branch-integrity
map-nist-80053: [CM-3, CM-5]
map-nist-ssdf: [PS.1.1, PW.7.1]
---

# Protected-branch integrity

The protected line of development is never rewritten, overwritten, or changed directly; it changes only
through a reviewed, verified integration. On git that means no force-push and no direct commit to the
protected branch, only a merged pull request.
