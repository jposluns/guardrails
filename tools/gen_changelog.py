#!/usr/bin/env python3
"""Generate the user-facing public CHANGELOG.md and the single-source VERSION file from changelog.toml.

The public changelog carries ONLY user-facing release notes; the detailed per-change record is private.
changelog.toml is the SINGLE SOURCE OF TRUTH for the pack's SemVer: each [[release]] carries a `version`,
and the root VERSION file is GENERATED here from the latest release, so the version can never live in two
hand-maintained places. Releases are append-only (array order oldest to newest); the latest release is
the last table in the array, and its version is what VERSION carries.
  gen_changelog.py           regenerate CHANGELOG.md and VERSION
  gen_changelog.py --check    fail (exit 1) if either is out of date; exit 2 on error
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_changelog.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opf_render import run_generator, FileTarget  # noqa: E402

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
GENSRC_OUTPUTS = (
    {"target": "CHANGELOG.md", "kind": "file",
     "sources": ("changelog.toml",), "regenerate": "python3 tools/gen_changelog.py"},
    {"target": "VERSION", "kind": "file",
     "sources": ("changelog.toml",), "regenerate": "python3 tools/gen_changelog.py"},
)


def render_md(data):
    lines = ["# " + data["title"], "", data["note"], ""]
    for rel in data["release"]:
        head = rel["title"]
        if rel.get("version"):
            head = "{}: {}".format(rel["version"], head)
        if rel.get("date"):
            head += " ({})".format(rel["date"])
        lines.append("## " + head)
        lines.append("")
        for item in rel.get("items", []):
            lines.append("- " + item)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def latest_version(data):
    """The pack SemVer VERSION carries: the version of the latest release (the last table in the
    append-only array). Raises KeyError if the latest release has no version, so a release that omits
    the required version field fails closed at generation rather than emitting a blank VERSION."""
    return data["release"][-1]["version"]


def render_version(data):
    return latest_version(data) + "\n"


def main():
    return run_generator(
        sys.argv[1:],
        source="changelog.toml",
        targets=(
            FileTarget("CHANGELOG.md", render_md),
            FileTarget("VERSION", render_version),
        ),
        regen_hint="run tools/gen_changelog.py to regenerate",
        schema_excs=(KeyError, IndexError, TypeError),
    )


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("gen_changelog.py") or main())
