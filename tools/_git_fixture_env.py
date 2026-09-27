#!/usr/bin/env python3
"""Hermetic git environment for self-test FIXTURES: drop the caller's repository-selecting git
variables so a fixture's git commands can never act on the CALLER's repository.

git exports repository-selecting variables to the processes it launches (a pre-commit or other hook
runs with GIT_INDEX_FILE, and often GIT_DIR, in its environment), and CI runners and wrappers export
their own. A self-test fixture that inherits os.environ and runs git init/add/commit inside its
throwaway temp repo therefore stages and commits into the CALLER's index and refs instead: the
GIT_INDEX_FILE / GIT_DIR / GIT_WORK_TREE family overrides git's cwd-based repository discovery, and
GIT_CONFIG_* and HOME / XDG_CONFIG_HOME inject the caller's configuration (a core.hooksPath there
even executes the caller's hook programs). That breaks test-hermeticity in both directions: the host
is changed, and the verdict depends on ambient state.

The scrub takes the ALLOWLIST stance the pack's other scrubs take (the recovery-snapshot layer in
aiqt_hooks, _opf_observe._scrubbed_env on the OPF side, the selftest-execution gate's launch
environment): EVERY GIT_-prefixed variable is dropped, not an enumerated family, so a variable
outside the author's own examples cannot slip through; the per-user configuration surfaces are
pointed at a scratch HOME and XDG_CONFIG_HOME (a fresh, empty, process-lifetime directory removed at
interpreter exit); and the global and system config files are pinned to os.devnull with
GIT_CONFIG_NOSYSTEM=1. A fixture re-applies only what it sets itself (identity variables and the
like) through keyword overrides.

Two entry points, one scrub, so no call site hand-rolls a variant that reintroduces the inheritance:

  git_fixture_env(**overrides) -> dict   a scrubbed COPY of os.environ for a subprocess env= argument
  scrub_git_environment()                the same scrub applied to os.environ IN PLACE, for a
                                         self-test entry point whose fixture git calls (and code
                                         under test) inherit the process environment

DISCLOSED RESIDUAL: the scrub neutralizes the ENVIRONMENT only. The system-wide config is neutralized
only while a descendant keeps the GIT_CONFIG_NOSYSTEM pin (the pin is itself GIT_-prefixed, so a
descendant that scrubs GIT_* drops it, which is why HOME and XDG_CONFIG_HOME are ALSO re-pointed);
the system-wide attributes file is not neutralized (root-owned, the git binary's own trust tier);
and git itself still resolves through the ambient PATH, a trusted-toolchain concern.
"""
import atexit
import os
import shutil
import tempfile

_SCRATCH_HOME = None


def _scratch_home():
    """A process-lifetime empty directory serving as HOME and XDG_CONFIG_HOME for fixture git calls,
    created lazily and removed at interpreter exit (a test leaves the host as it found it)."""
    global _SCRATCH_HOME
    if _SCRATCH_HOME is None or not os.path.isdir(_SCRATCH_HOME):
        _SCRATCH_HOME = tempfile.mkdtemp(prefix="aiqt-git-fixture-home-")
        atexit.register(shutil.rmtree, _SCRATCH_HOME, ignore_errors=True)
    return _SCRATCH_HOME


def git_fixture_env(**overrides):
    """A copy of os.environ with every GIT_-prefixed variable dropped, HOME and XDG_CONFIG_HOME
    pointed at the scratch home, the global and system git config pinned to os.devnull with
    GIT_CONFIG_NOSYSTEM=1, and overrides applied last (a fixture keeps only what it sets)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    home = _scratch_home()
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = home
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env.update(overrides)
    return env


def scrub_git_environment():
    """Apply the git_fixture_env scrub to os.environ IN PLACE and return the applied mapping: for a
    self-test whose fixture subprocesses (and code under test) inherit the process environment. Call
    it at the self-test entry point, before the first fixture git call."""
    env = git_fixture_env()
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        del os.environ[key]
    for key in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_NOSYSTEM",
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        os.environ[key] = env[key]
    return env
