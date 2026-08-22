# System-level hardening

Most AIQT guardrails are guidance your coding assistant can follow on its own. A few describe
protections that only fully hold with setup on the machine itself, which a portable pack cannot do
for you. This file lists those, in plain terms: what the guardrail asks for, what the pack can and
cannot do, and what you set up on your host to raise the bar against a determined attacker, not just an
honest mistake. These are risk-reducing measures, not guarantees: each narrows the gap, and each entry
says what remains.

You do not need any of this to adopt the pack. Add a given item only if its threat applies to you, and
match its depth to your own threat model.

Each entry follows the same shape:

- **Guardrail** - the rule it strengthens.
- **What the pack does** - the part the pack enforces or prescribes on its own.
- **The gap** - why that is not enough on its own against an adversary.
- **What you set up** - the host-level step that narrows it, and what still remains.

---

## 1. Isolate anything that runs untrusted code or touches your files

- **Guardrail:** Least-privilege tool and file access (`security/SECI-least-privilege-tools`), and any
  workflow where your assistant, a tool, or a generated script runs code or reads and writes your files.
- **What the pack does:** it tells the assistant to take only the access its task needs and to prefer
  sandboxed or isolated execution where the platform offers it, and it ships gates that DETECT a bad
  write after the fact (for example a checker that compares a file against a fresh regeneration).
  Detection and policy are not containment.
- **The gap:** a process that runs code is as privileged as whatever launched it. It can read and write
  files outside its intended working area (any path its user can reach), reach the network (host
  services, or exfiltration of anything it can read), and share state with another run through any
  location both can reach (a temporary directory, a home directory, IPC, an inherited file descriptor, a
  fixed path). An after-the-fact check catches only a change left behind, not one made and then undone.
  None of this is preventable from inside the language the code runs in.
- **What you set up:** run untrusted or semi-trusted execution inside a vetted sandbox or container
  runtime, so the boundary is the operating system's rather than the code's good behaviour. Aim for all
  of the following; each reduces a specific risk, and none alone is containment:
  - **Filesystem containment** - the task's own working directory is present and writable, and everything
    else is ABSENT, not merely read-only. A read-only host still lets untrusted code READ secrets (keys,
    tokens, source, configuration) and carry them out through any allowed channel, so expose an outside
    path only when it is individually required and safe to reveal.
  - **Network isolation.** Full isolation (no network namespace reachability) is strongest. An enforced
    egress allow-list is a weaker fallback: it REDUCES exfiltration but does not prevent it, because code
    can still send to any allowed endpoint and the list does not constrain what is sent, so pair it with
    task-specific data and protocol controls.
  - **Kernel-enforced resource limits** - cgroups for CPU, memory, and process count; rlimits
    (`RLIMIT_FSIZE`, `RLIMIT_NOFILE`) for file size and open files; and an external wall-clock timeout.
    A per-file size cap does not bound aggregate disk or inode use, so bound those too where they matter.
  - **A syscall allow-list (seccomp)**, so the kernel attack surface the process can reach is narrowed.
  - **A fresh home, temporary, and IPC space per run**, plus explicit sanitization of inherited file
    descriptors (close-on-exec) and of the shared channels an IPC namespace does NOT cover (filesystem
    and abstract Unix sockets, POSIX shared memory). These reduce shared state between runs; they do not
    close every channel.
- **What remains even then:** a shared kernel (a kernel or runtime vulnerability can cross the boundary),
  any endpoint you allowed egress to, and any path you deliberately exposed. Treat this as raising the
  bar in proportion to your threat model, not as a guarantee; the individual Linux primitives behind it
  (mount, user, PID, and network namespaces, `pivot_root` and bind-mounts, seccomp, cgroups, and rlimits)
  are error-prone to assemble by hand, so prefer a vetted runtime (a container runtime or a rootless
  sandbox). Partial isolation does not confine: creating namespaces alone (for example `unshare` without
  a `pivot_root`/bind-mount setup and without a network namespace) leaves the host filesystem view and
  network reach in place and can give a false sense of safety. Reach for this whenever the code, plugin,
  or agent is not your own reviewed, trusted code.

---

*More entries are added here as guardrails with a host-level component are identified. Likely next
candidates: an enforced network egress allow-list (`security/SECC-egress-destinations`), kernel-enforced
resource bounds (`security/SECA-resource-bounds`), and audit records held under an authority separate from
the actor they record (`security/SECI-protect-audit-records`). If an entry does not apply to your
environment, skip it.*
