"""Shared render-and-reconcile driver for the single-source generators (changelog, roadmap).

Both generators share one shape: read a source TOML, render one or more targets from it, then
reconcile each target against its file on disk (write it, or in --check mode report drift). This
module factors that shape into one driver so each generator declares only what is particular to it,
its source file, its targets, its regenerate hint, and the exception set that means a malformed
source, while the load/render/reconcile/exit orchestration lives here once.

Behaviour is identical to the hand-written mains it replaces: the same console strings, the same exit
codes (0 clean, 1 drift under --check, 2 on a read or render error), and the same per-target drift
lines in the same order. The driver runs in two phases so this identity holds even on a degraded input.
First it RENDERS the payload of every target from the source, so a malformed source (raising schema_excs)
surfaces before any target is reconciled, exactly as the hand-written mains rendered every payload inside
one schema try/except before touching disk; a render error never leaves a half-written set behind. Then it
MATERIALIZES and RECONCILES each target in declaration order, so an earlier target's `drift: <path>` line
prints, and its file is written, before a later target's materialization failure aborts the run, matching
what the hand-written mains did per target. Building every target fully before reconciling any (the shape
this replaced) would instead let a later target's build failure suppress an earlier target's drift line and
write, a behaviour change this ordering avoids.

Stdlib only; the low-level primitives (repo_root, load_toml, reconcile, replace_block) live in
_gen_common. This module carries no GENSRC_OUTPUTS and is not a tools/gen_*.py, so the generated-source
registry never treats it as a generator.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, reconcile, replace_block  # noqa: E402


class TargetError(Exception):
    """A target-specific build failure carrying the exact message the driver prints before exit 2.

    Used for conditions outside the malformed-source class, such as a missing generated page or a
    page whose markers cannot be found, which the hand-written mains reported with their own message
    rather than the generic schema-error line."""


class FileTarget:
    """A whole-file generated target: its entire bytes are render(data).

    path is the repo-relative target, used both to locate the file under the repo root and as the
    label in its `drift: <path>` line."""

    def __init__(self, path, render):
        self.path = path
        self.render = render

    def render_payload(self, data):
        """The schema-render step: turn the source data into this target's payload. Raises the
        generator's schema_excs on a malformed source, before any target is reconciled."""
        return self.render(data)

    def materialize(self, root, payload):
        """Turn the rendered payload into the (path, text) reconcile pair. A whole-file target touches
        no disk here, so it never fails at this step."""
        return root / self.path, payload


class BlockTarget:
    """A generated block inside a hand-authored page: the named marker block's inner text is
    render(data) and the rest of the page is preserved.

    render(data) is evaluated in the render phase, so a malformed source surfaces as the generic
    schema error just as it did when the render call sat in the main's schema try/except; a missing
    page or absent markers then surface in the materialize phase as a TargetError (exit 2) with the
    page-specific message."""

    def __init__(self, path, marker, render):
        self.path = path
        self.marker = marker
        self.render = render

    def render_payload(self, data):
        """The schema-render step: render the block's inner text. Raises the generator's schema_excs
        on a malformed source, before any target is reconciled (and before this page is read)."""
        return self.render(data)

    def materialize(self, root, payload):
        """Splice the rendered inner text into the page's marker block, preserving the rest. Raises
        TargetError (exit 2) when the page is missing or its markers are absent, matching the
        page-specific messages the hand-written main printed."""
        target = root / self.path
        if not target.exists():
            raise TargetError("error: {} not found (expected generated target)".format(self.path))
        try:
            new_text = replace_block(target.read_text(encoding="utf-8"), self.marker, payload)
        except (ValueError, OSError) as exc:
            # OSError too: an unreadable page is a read error, fail-closed exit 2, not a traceback.
            raise TargetError("error: {}".format(exc))
        return target, new_text


def run_generator(argv, *, source, targets, regen_hint, schema_excs):
    """Drive one generator: load source, render every target, reconcile each, and return the exit code.

    argv is the process arguments after the program name (--check selects drift-report mode). source
    is the repo-relative TOML. targets is an ordered sequence of FileTarget/BlockTarget. regen_hint is
    the line printed under --check when drift is found. schema_excs is the exception tuple that means
    the source is missing or misuses a key (it differs per generator and is preserved exactly).

    targets is materialized once (tuple), then consumed twice (render, then reconcile). Materializing
    it fails closed on an empty declaration and prevents a one-shot iterator from being exhausted by the
    render phase and then silently skipped by the reconcile phase (a fail-open that would return a clean
    0 while reconciling nothing)."""
    targets = tuple(targets)
    if not targets:
        raise ValueError("run_generator requires at least one target; got an empty target set")
    check = "--check" in argv
    root = repo_root()
    try:
        data = load_toml(root / source)
    except (OSError, ValueError) as exc:
        print("error: cannot read {}: {}".format(source, exc))
        return 2
    # Render phase: render every target's payload from the source before any is reconciled, so a
    # malformed source (schema_excs) aborts before a single target is written, exactly as the
    # hand-written mains rendered every payload inside one schema try/except.
    try:
        rendered = [(target, target.render_payload(data)) for target in targets]
    except schema_excs as exc:
        print("error: {} is missing or misuses a key: {}".format(source, exc))
        return 2
    # Reconcile phase: materialize and reconcile each target in declaration order, so an earlier
    # target's drift line prints (and its file is written) before a later target's materialization
    # failure aborts the run, matching the hand-written mains' per-target ordering on every path.
    drift = False
    for target, payload in rendered:
        try:
            path, text = target.materialize(root, payload)
        except TargetError as exc:
            print(str(exc))
            return 2
        if reconcile(path, text, check):
            print("drift: {}".format(target.path))
            drift = True
    if check and drift:
        print(regen_hint)
        return 1
    return 0
