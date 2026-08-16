---
corpus-id: valinf
origin: pack
family: aiqt
tier: 10
facet: ACCUR
slug: validate-inference-before-action
---

# Validate an inferred premise before acting

Validate an inferred premise before acting on it. Guard inputs: a check whose logic is correct is still
worthless when its INPUT cannot answer the question asked of it. Ask the authority question of every
consequential guard, not "is this value correct" but "can this source, even in principle, answer what I
am asking?" When it cannot, change the input, do not harden the check.
