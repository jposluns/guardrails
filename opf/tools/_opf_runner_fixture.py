"""Executable fixtures for the standalone OPF runner's registration checks.

These checks intercept Python gates and run only the selected in-memory vector
leg. They do not certify other gates or isolate arbitrary runner programs.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_registration(runner, source, target_args, identity):
    # The on-disk runner is the call roster, including for a mutated source.
    # This parser covers its one-line run_gate registrations, not general shell.
    expected = []
    for line in runner.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("run_gate "):
            continue
        words = shlex.split(line)
        if len(words) < 6 or words[2:5] != ["python3", "-I", "-B"]:
            raise ValueError("unsupported runner registration: " + line)
        expected.append([word.replace("$here", str(runner.parent))
                         for word in words[3:]])
    if not expected or expected.count(target_args) != 1:
        raise ValueError("runner must register the selected vector leg exactly once")
    bash = shutil.which("bash", path=os.defpath)
    if bash is None:
        raise OSError("bash is required for runner verification")
    dispatch = 'if "$@"; then'
    if source.count(dispatch) != 1:
        raise ValueError("unsupported runner dispatch")
    # Exercise Bash lookup, command (which bypasses shell functions), and a
    # child Bash process. The Python fixture execs only the selected vector leg
    # through the pinned interpreter; other registered Python gates are no-ops.
    routes = (
        dispatch,
        'if command "$@"; then',
        'if ' + shlex.quote(bash) + ' --noprofile --norc -c \'exec "$@"\' _ "$@"; then',
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="opf-registration-") as tmp:
        root = Path(tmp)
        executable = root / "python3"
        log = root / "calls.jsonl"
        executable.write_text(
            "#!{} -I\n".format(sys.executable)
            + "import sys\nsys.dont_write_bytecode = True\nimport json, os\n"
            + "args = sys.argv[1:]\n"
            + "with open({!r}, 'a', encoding='utf-8') as handle:\n".format(str(log))
            + "    handle.write(json.dumps(args) + '\\n')\n"
            + "if args == {!r}:\n".format(target_args)
            + "    os.execv({0!r}, [{0!r}, '-I', '-B', {1!r}, "
              "'--self-test', '--vectors-only'])\n".format(sys.executable, target_args[2])
            + "sys.exit(0 if args in {!r} else 2)\n".format(expected),
            encoding="utf-8")
        executable.chmod(0o700)
        # Controlled inputs, not an enforced sandbox. These fixture routes
        # read the suite and its imports and write only the private call log.
        env = {"PATH": tmp + os.pathsep + os.defpath, "HOME": tmp,
               "XDG_CONFIG_HOME": tmp, "TMPDIR": tmp, "LC_ALL": "C", "TZ": "UTC",
               "PYTHONDONTWRITEBYTECODE": "1"}
        for route in routes:
            log.write_text("", encoding="utf-8")
            proc = subprocess.run(
                [bash, "--noprofile", "--norc", "-c",
                 source.replace(dispatch, route, 1), str(runner)],
                cwd=tmp, env=env, capture_output=True, text=True, timeout=30)
            try:
                observed = [json.loads(line) for line in log.read_text(
                    encoding="utf-8").splitlines()]
            except (OSError, UnicodeError, ValueError) as exc:
                raise AssertionError(identity) from exc
            if proc.returncode != 0 or observed != expected:
                raise AssertionError(identity)
            results.append(proc)
    return results
