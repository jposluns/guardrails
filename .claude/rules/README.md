# Generated rule tree

These rules are GENERATED from `.aiqt/core/rules/` by `tools/gen_rules.py` and are checked in CI for drift.
Do not hand-edit `aiqt/` or `security/` here; edit the source and regenerate. The two-axis taxonomy:
`aiqt/` is numbered by AIQT priority (NN-CODE-slug; the apex is 00-project-integrity), `security/` is coded
by the CIA-plus-privacy model with family-namespaced codes SECC/SECI/SECA/SECP (CODE-slug), so no security
code collides with an AIQT facet. Vendored packs live under `external/` and are never touched.
