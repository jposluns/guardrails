# Clean start: adopting OPF with no prior records

This is the clean-start companion to `SKILL.md` beside it, the OPF adoption procedure. Read that
first; its three rules apply here unchanged. Section references with a decimal point name
sections of OPF-SPEC.md; a whole-number reference names a section of `SKILL.md`.

## What clean start means

Clean start is first-class (section 14): preserve and retire the old operational files,
establish the new store and its enforcement, and import nothing. It makes no claim that any old
obligation was fulfilled or converted. Two cases qualify:

- **No prior records.** Investigation (section 3) lists no candidate and no empty directory,
  and reports `first-adoption`. There is nothing to preserve or retire.
- **Prior files, nothing imported.** Each candidate takes `retire` (or `keep` or `move`), and no
  candidate takes `migrate`. The store starts empty, and its `import_status` stays `none`. With
  no migrate source, the skip policy has nothing to govern.

In both cases the plan for a first adoption carries `init-store` (no store resolves yet),
`render-views` listing exactly the views of the default manifest (thirteen in this build, all
under `.working/`), and the operations that install each enforcement member (section 4). The
sample worksheet in section 4 is a plan of this kind.

## What runs in this build

Investigation and `opf adopt plan` run for both cases. Approval capture, apply and completion
do not (section 1). So:

- **Prior files, nothing imported:** produce and present the plan (sections 4 and 5), then stop
  at section 6. Do not retire, move or delete any old file yourself, and do not run `opf init`
  over the tree.
- **No prior records:** the same stop applies to `opf adopt`. This build supports one interim
  route instead, described next. It is initialization, not an adoption, and the adopter chooses
  it knowing what it leaves out.

## The interim route for a project with no prior records

Use it only when all of these hold: the investigation lists no candidate and no empty
directory, reports `first-adoption`, and resolves no store; `opf adopt status` reports that no
adoption run exists; and the adopter has agreed to the limits below.

What it leaves out, which you tell the adopter before any write:

- No adoption plan, approval, receipt or completion check is recorded.
- No enforcement member is installed. The CI recipe, pre-commit check and deny hook in the
  reference repository can be installed by hand, each as its own header describes; adoption
  does not do it.
- A later `opf adopt` run reads the project as re-adoption, because a store now resolves. In
  this build its investigation then lists the store's rendered views and the root
  `CHANGELOG.md` as candidates, each needing a disposition.

The writes this route makes are an ordinary change the adopter reviews and commits; their
go-ahead is not the single adoption approval of section 5. The steps, from the product root:

1. `opf init --root .` creates the machine sources under `.working/toml/` (the manifest, the ID
   counters, the version ledger, the worklog and the eleven typed indexes), the `.opf.toml`
   pointer, and a `CHANGELOG.md` when none exists. It refuses rather than absorb an existing
   store, pointer or foreign `.working/` content, and it leaves every created path untracked.
2. Have the adopter review the created paths, then stage exactly the paths `opf init` printed
   and commit them. The store must be tracked before views can render: `opf render --write` on
   an untracked store refuses with exit 2.
3. `opf render --write --root .` writes the declared views under `.working/`. Review and commit
   them.
4. Verify: `opf doctor --root . --require-store` must exit 0 with `store integrity: VALID`, and
   `opf render --check --root .` must exit 0. Report both outputs. A finding or cannot-evaluate
   is a stop and a report, never a reason to edit store files by hand.
5. From here, work records-first: change records only through `opf record` and the other `opf`
   verbs, and run the operating loop in the `flow` member of this pack.

## When the apply stages land

Then a clean start runs end to end through `opf adopt`: one plan, one approval, apply, and a
green completion check before anything is recorded as retired. Until that build ships, describe
a clean start done by the interim route as initialized, not adopted.
