# Releasing OPF

This is OPF's own release document. It moves with the `opf/` tree, and every path in it is relative to
that tree. While OPF is nested in its parent repository, that repository's `RELEASING.md` keeps its own
copy of the checklist below, and both copies are checked. The parent restates the declaration as a future
adopter of a released OPF: it carries OPF's source only while OPF is nested, and it does not author OPF
releases.

This document is shipped and `pack-immutable`: every OPF release carries its exact bytes. A change to it is
a reviewed change for a later release, made before that release's manifest is generated. While OPF is
nested, the parent repository's ownership map holds this class for it.

## Declaration checklist (OPF-SPEC section 16.1)

The reference tooling's declaration file is `tools/opf-declaration.toml`, and the shipped-file digest is
carried in `tools/_opf_declaration.py` (`opf/tools/_opf_declaration.py` from the parent repository) as
`SHIPPED_DECLARATION_SHA256`. Its `release` is OPF's own release version in `VERSION` (in this tree),
which moves independently of the parent pack's release version.

A change of OPF's version sets, in one change: `VERSION`; `release` in the declaration file;
`SHIPPED_DECLARATION_BYTES` and `SHIPPED_DECLARATION_SHA256` in `tools/_opf_declaration.py`; and the
restatement below, the one in `spec/OPF-QUICKSTART.md` and the one in the parent repository's
`RELEASING.md`. `RELEASE_IDENTITY` in `tools/_opf_declaration.py` is read from `VERSION` when that module
loads, and a missing or malformed `VERSION` leaves the build without one, so every gated verb refuses.

A change of the supported `spec_version`, homes generation or worklog storage generation changes its
value in `tools/_opf_store.py`, the matching field in the declaration file, `SHIPPED_DECLARATION_BYTES`
and `SHIPPED_DECLARATION_SHA256` in `tools/_opf_declaration.py`, and the same three restatements. It
does not by itself touch `VERSION` or `release`.

The declaration gate, `tools/check_opf_declaration.py` in this tree, then must exit 0. It reads nothing
outside this tree. It refuses a restatement that differs from the file, a missing restatement, a
document with more than one restatement block, a document in this tree that restates the declaration
but is not one of its surfaces, and a release other than the version in `VERSION`. While OPF is nested,
the parent repository's own `tools/check_opf_declaration.py` must exit 0 as well: it checks the same
rules over the whole repository and reports findings in this tree apart from findings in the parent's
release document.

This release declares:

```toml opf-declaration
release = "1.0.5"
spec_version = "1.2.0"
homes = 1
worklog = 1
```

## Release procedure

This section is procedure only. The release manifest, the digests and the release package it describes are
not built yet, and neither are the tools that are to implement them: the release manifest generator, the
manifest checker, and the release package tooling, which builds the package and publishes its digest. Until
each exists, the steps that depend on it cannot be carried out, and no OPF release has a manifest, a root
digest or a package digest.

### Release identity

An OPF release is identified by OPF's own release version, the one line of `VERSION`. The declaration's
`release` and `RELEASE_IDENTITY` in `tools/_opf_declaration.py` equal it, as the checklist above requires.
The parent pack's release version is not part of OPF's release identity. Once the release manifest
generator and the release package tooling exist, a release is further identified by the root digest of its
release manifest and by the SHA-256 of its release package.

### Release manifest

The release manifest is to list every file the release ships, with its byte count and SHA-256, and a root
digest over that list. The release manifest generator is to produce it from OPF's own release inventory,
the authority for which files ship and in which class, with no second ownership map and no import from the
parent's tools. The manifest and the files generated with it are self-outputs: they are left out of the
list they would otherwise change. The manifest checker is to verify it: that a second generation leaves it
unchanged, and that the member set, the byte counts, the per-file digests, the tree digest and the root
digest all match the tree.

### Digest publication

The release package tooling is to build the release package from the manifest, after the manifest is
generated, with the manifest inside it, and to compute the package's SHA-256. The release publishes OPF's
release version, the manifest's root digest and the package's SHA-256 apart from the package itself, so an
adopter can check the package's bytes against the published digest before extracting or running anything
from it.

### Release order

1. Land every change that ships in the release, this document included, and complete the declaration
   checklist above; the declaration gate must exit 0.
2. Run every content generator, then run the release manifest generator last.
3. Verify the manifest against the tree with the manifest checker.
4. Build the release package from the manifest and compute its SHA-256 with the release package tooling.
5. Verify the package against the manifest and its digest with the release package tooling, then publish
   the release version, the manifest's root digest and the package's SHA-256.
6. An adopter, the parent repository among them, adopts the published package only after checking it
   against the published digest, and never from OPF's source tree.
