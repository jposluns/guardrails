"""Which pages are generated template pages: the targets tools/gen_draft_site.py declares in its GENSRC_OUTPUTS.

The gates that hold a template page to the template's own rules (check_site.py, check_footer.py) ask this
module, never the name of the folder the pages sit in, so the same rule holds while the pages are staged
under a root's draft/ folder and once they are promoted to the root itself. The declaration is read
statically (gen_gensrc's reader: the GENSRC_OUTPUTS literal only, the generator is never imported or run).
A declaration that cannot be read or evaluated, or holds an entry gen_gensrc's own entry validation refuses
(not a dict, a missing or non-string target, a missing source), raises OSError or ValueError; each caller
fails closed.

A page path is named by its coverage root ("site" or "opf/site") and its path under that root. The
template base of a root is the folder, relative to the root, that holds every generated page of that root
("draft/" while staged, "" once promoted); the landing page and the disclosure URL derive from it.
"""
from functools import cache
from pathlib import Path, PurePosixPath

import gen_gensrc

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = "tools/gen_draft_site.py"


@cache
def _declared(root):
    # Each entry goes through gen_gensrc's validation before its target is read, so a malformed entry raises
    # ValueError (exit 2 in each gate) rather than a KeyError or TypeError, or a non-string target.
    seen = set()
    return frozenset(gen_gensrc._validate_entry(raw, GENERATOR, root, seen)["target"]
                     for raw in gen_gensrc._read_declaration(root / GENERATOR, GENERATOR))


def template_targets(root=None):
    """The repo-relative target of every generated template page (a frozenset of POSIX paths), under `root`
    (ROOT, read at call time, by default)."""
    return _declared(Path(ROOT if root is None else root))


def is_template_page(subdir, page_rel, targets=None):
    """True only when the page at `page_rel` under the coverage root `subdir` is a generated target."""
    if targets is None:
        targets = template_targets()
    return "{}/{}".format(subdir, PurePosixPath(Path(page_rel).as_posix())) in targets


def template_base(subdir, targets=None):
    """The folder under `subdir` holding all its generated pages, as "" or "<folder>/"; None with none."""
    if targets is None:
        targets = template_targets()
    prefix = subdir + "/"
    folders = [PurePosixPath(t[len(prefix):]).parent.parts for t in targets if t.startswith(prefix)]
    if not folders:
        return None
    common = folders[0]
    for parts in folders[1:]:
        size = 0
        while size < min(len(common), len(parts)) and common[size] == parts[size]:
            size += 1
        common = common[:size]
    return "".join(part + "/" for part in common)
