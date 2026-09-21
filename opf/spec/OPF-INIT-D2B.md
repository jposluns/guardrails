# OPF-INIT-D2B

This document specifies the normative contract v1 for coupled D2b initialization.
The shipped D2a behavior is identified as `opf.init.source-only/v1` and retains its existing behavior.

## Success wording

Coupled init success (`opf.init.coupled/v1`) emits this exact wording:

> opf init: at the final recorded observation, init-created sources have their validated bytes and modes and exact staged entries; initial views match those sources and remain unstaged; the whole store is VALID. Operation finalization and owned-lock release succeeded. No commit was created.

Exit 0 requires fresh observations supporting each assertion, including the required checker roster actually executing. Final store validation follows the last store write, including lease removal, while the outer mutex still excludes participating writers.

## Keep Schema

The Keep schema defines structural validation. The operation identifier is exactly `opf-init-keep` and `schema` is 1.

The canonical representation of acceptance and inventory models requires `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`, and `allow_nan=False`, appended with a single `\n` byte. Digests use `sha256:` followed by 64 hex characters of this exact serialization.

Limits are fixed:
- Acceptance bytes and canonical bytes: 1,048,576
- JSON nesting depth: 16
- Decisions and inventory entries: 4,096
- Path depth: 32
- Single path: 4,096 bytes
- Component length: 255 bytes
- Actor ID: 256 bytes
- Reason: 4,096 bytes

A directory group encapsulates a parent and every recursive descendant it contains. Duplicate decisions, overlapping group boundaries, and missing coverage result in refusal. Noncanonical paths, absolute paths, backslashes, unreadable files, or traversal directories matching the reserved managed set refuse execution.

## Formats

### Plan Format

```text
{
  schema: 1,
  format: "opf.init.plan/v1",
  operation: "opf-init",
  operation_id,
  versions,
  binding,
  head,
  first_adoption,
  inventory_digest,
  acceptance,
  application_time,
  sets,
  permitted_directories,
  staging_set,
  required_checks,
  publication_boundaries,
  recovery_policy,
  plan_digest
}
```

The plan requires discrete explicit sets for Sources (S), Views (V), Keep decisions (K), Existing files (E), and Control records (C). No overlaps may cross these boundaries.

### Outcome Format

```text
{
  schema: 1,
  format: "opf.init.outcome/v1",
  operation: "opf-init",
  operation_id,
  binding,
  head,
  plan,
  last_attempted_phase,
  primary_failure,
  finalization_failures,
  path_observations,
  source_index_observations,
  creation_evidence,
  git_object_observations,
  checks_executed,
  controls,
  durability,
  completed_phases,
  rollback
}
```

### Bootstrap Provenance

The ratified managed provenance artifact is `.working/toml/init.toml`. It is a new managed store artifact and therefore rides a base-schema version bump with a tested `opf upgrade` route (OPF-SPEC section 9.2); no provenance is fabricated for existing D2a stores. Its `source_digest` is computed over an enumerated bootstrap source set that EXCLUDES `init.toml` itself; the outer plan digest is computed afterward.

```text
{
  schema: 1,
  format: "opf.init.bootstrap/v1",
  spec_version,
  operation_id,
  binding,
  head,
  first_adoption,
  inventory_digest,
  acceptance,
  source_set,
  source_digest
}
```

PR1 freezes these formats only; it does not implement their producers, persistence, or resume consumers. The `--decisions` CLI flag and resume dispatch are the PR7 activation boundary and are not added in PR1.

## Residuals

Platform and filesystem compatibility requires verified containment, exclusive creation, advisory locking, stable open-file identity, supported index semantics, and file durability semantics on the targets. Unsupported operating systems result in CANNOT-EVALUATE.
