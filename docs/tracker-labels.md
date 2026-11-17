# Adopter tracker-label shapes and the added-line diff check

`tools/check_internal_names.py` counts four built-in tracker-label shapes in every shipped file and holds each per-file count to the ratchet baseline `.aiqt/tracker-label-baseline.toml`. An adopter can add its own shapes, and can run an extra check that reports labels on lines a branch adds.

## Configuration: `.aiqt/tracker-labels.toml`

The adopter owns this file. This repository ships it with no shape. If the file is absent, no extra shape applies. A present file is checked before any scan, and any fault fails closed with exit 2.

```toml
format-version = 1

[[shape]]
name = "backlog-id"
regex = '\bTASK-[0-9]+\b'
red = ["References TASK-123 here."]
green = ["References TASK-next here."]
```

- The schema is closed. The file holds only `format-version` (the integer 1) and `[[shape]]` tables, and each table carries exactly `name`, `regex`, `red` and `green`.
- `name` is a lower-case ASCII identifier (letters, digits and single hyphens, at most 48 characters), and names are unique. The baseline key is `adopter-<name>`, so it never collides with a built-in key.
- `regex` is a Python regular expression, applied to each line separately. Letter case follows the regex, inline flags included.
- `red` lists fixture strings the shape must match, and `green` lists strings it must not match. Both arrays are nonempty, and each fixture is one nonempty line.
- A shape fails closed when its regex does not compile, a red fixture is not matched, a green fixture is matched, or any match is zero-width (in a fixture or in a scanned line).

### Limits

| Bound | Value |
| --- | --- |
| File size | 64 KiB |
| Shapes | 32 |
| Regex length | 512 characters |
| Fixtures of each colour per shape | 16 |
| Fixture length | 4096 characters |
| Wall-clock budget for one run of adopter patterns | 10 seconds |

Adopter regexes are compiled, checked against their fixtures and matched only in a separate worker process. The worker is stopped when the budget runs out. A timeout, a worker failure or malformed worker output exits 2. A slow shape is never dropped so that the scan can continue clean. These limits are proposed bounds. They are not measured against representative repositories.

## The ratchet

Adopter shapes join the per-file ratchet after the built-in shapes, sorted by name. The baseline's exactness holds in both directions for them as well. With no adopter shape, the baseline render is unchanged. If you remove a shape while its baseline key remains, the baseline is refused as carrying an unknown key. After a configuration change, regenerate the baseline with `python3 -I -B tools/check_internal_names.py --write-baseline`. Treat both the configuration change and the regenerated baseline as reviewed policy changes.

The configuration file has no filename exemption. If the release inventory lists it, it is scanned like any shipped file, so its red fixtures count and need reviewed baseline entries.

## `--diff BASE`

`python3 -I -B tools/check_internal_names.py --diff BASE` reports every built-in or adopter shape match on a line added between the merge base of BASE and HEAD, in HEAD's shipped set, whatever the baseline allows. A label deleted in one place and added in another, with the count unchanged, is still reported.

- Every input comes from the HEAD commit: the manifest, the configuration, the baseline (validated, not compared), the tree and the file contents. The working tree and the index are not read.
- Lines are counted by LF. A carriage return stays part of the line, and a last line without LF counts.
- An added file counts all its lines as added. Deleted files, deletion-only changes, pure renames and mode-only changes add nothing. A rename with edits reports added lines at the destination path. A rename Git does not detect counts as a delete plus an add.
- A binary attribute does not hide a change. A blob that is not UTF-8 fails closed under a text suffix and is skipped under any other suffix. Archive contents are not examined.
- Exit codes: 0 means no label on an added line, 1 means at least one, and 2 means the check cannot be evaluated. Exit 2 covers: BASE is not a readable commit, the repository is shallow, there is no merge base or more than one, Git fails or times out, Git output is malformed, a shipped path is absent or is not a regular file in HEAD, or the HEAD configuration or baseline is invalid (also when no line was added). The check never fetches history.
- `--diff` takes exactly one operand that does not start with `-`, and it cannot be combined with another option.

The diff check is an extra check. Keep the full gate required in CI. A clean diff check does not mean the full gate passes.

## What this does not cover

A fixture shows only that its own examples behave as stated. Labels can still go unreported when they are written in prose no shape describes, belong to an undeclared shape, are split or encoded, sit inside binary or archive contents, or sit on lines the diff check does not see as added. Removing or narrowing a pattern is a reviewed configuration change.
