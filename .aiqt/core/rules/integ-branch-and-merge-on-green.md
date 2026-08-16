---
corpus-id: artbr1
origin: pack
family: aiqt
tier: 10
facet: INTEG
secondary: [QUALI]
slug: branch-and-merge-on-green
---

# Branch and merge only on green

Develop a change in isolation from the shared line of development, put it through a review gate, and
integrate it only when its checks pass. What lands on the shared line is the reviewed, verified state,
never a work in progress. On git the usual form is a feature branch and a pull request merged on green;
the mechanism varies, the gate does not.
