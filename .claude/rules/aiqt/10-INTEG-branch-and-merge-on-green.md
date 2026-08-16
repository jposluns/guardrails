---
corpus-id: artbr1
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: branch-and-merge-on-green
map-nist-ssdf: [PS.1.1, PW.7.1]
---

# Branch and merge only on green

Develop a change in isolation from the protected line of development, put it through a review gate, and
integrate it only when its checks pass. What lands on the protected line is the reviewed, verified state,
never a work in progress. On git the usual form is a feature branch and a pull request merged on green;
the mechanism varies, the gate does not.
