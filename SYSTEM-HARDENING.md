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

## 2. Put the orchestration guard's state under an operator-owned directory

- **Guardrail:** The orchestration stop-guard's forgery-resistance, the operator escape sentinel, the anti-shrinkage checkpoint, and the attestation register that together decide whether the assistant may stop or treat work as blocked (`aiqt/10-TRUST-continue-by-default`, with audit records held under a separate authority, `security/SECI-protect-audit-records`).
- **What the pack does:** it treats these as operator-controlled in code. The escape sentinel is honoured only as a regular file with a single hard link, reached without a symlinked path component, resolved under the state-directory anchor, owned by a different uid, and not group- or other-writable; the attestation register is re-anchored against its git merge-base (or a companion anchor file) authority and, at yield, the validated snapshot re-bound to it and its refs re-derived from the register content; and the checkpoint distinguishes a genuine first window from a deleted one through an init marker. These raise the bar against naming, symlink, hard-link, forge-between-audits, and naive-deletion vectors.
- **The gap:** the state-directory items, the sentinel and its anchor, the checkpoint and its init marker, and the validated snapshot, live in a state directory the assistant itself can write. An actor running as that same account can delete the checkpoint and its marker together, place a passing sentinel, or write a snapshot; but the yield reader re-anchors the snapshot and re-derives its refs from the register content, so a swapped register or a fabricated-refs snapshot no longer substantiates. The one filesystem-write attestation forgery that remains is appending a valid approved row to the register itself (uncommitted, so the git merge-base or anchor authority, which checks only the committed or anchored prefix, still passes; that appended row substantiates its ref). That forgery needs write access to the register wherever it is declared, a separately-declared path that need not be the state directory, not merely to the state directory. Code running as that actor cannot categorically prevent it; the in-code checks are best-effort bar-raisers. More broadly, the stop-guard's whole blocker-substantiation surface (the backlog `state`/`granted` fields, a pending-decision row, the dispatch ledger, a `not-before` value, and free-text external evidence) is largely actor-authorable: the guard rejects an unknown blocker kind and requires a substantiating record in the declared authoritative source, but code running as the assistant's own account cannot categorically enforce that a block is granted by an authority other than the assistant. The stop path now fails closed-continue (an unconfirmable blocker or a non-enumerable backlog DENIES the stop, releasable by the operator-owned escape sentinel or the guard-owned loop bound (past which it becomes a deliberate, recorded forced exit), never a clean close); the categorical enforcement of "granted by another authority" is exactly what operator-owned, assistant-non-writable READ sources (the attestation register, the decision record, and the escape sentinel) provide here.
- **What you set up:** split the state by who must write it, because the guard's own process writes part of it as it records denials, events, and exhaustion state. Place the guard's READ authoritative sources under a path the assistant's account cannot write (owned by a different uid, the operator or a dedicated role, not group- or other-writable, with the assistant lacking write on the file and its parent chain): the operator escape sentinel, any declared attestation register, and the pending-decisions record. These become artefacts the assistant reads but cannot forge, and the in-code checks over them become enforced rather than best-effort; this is the categorical forgery-resistance closure. Leave the guard's WORKING channel assistant-writable: the turn-state denial counter, guard-events, the anti-shrinkage checkpoint and its init marker, forced-exit.jsonl, and the validated-attestations snapshot. The guard writes these as it records denials, events, and exhaustion state, and if the writes fail it cannot persist its denial counter and FAILS OPEN (a DENY degrades to an exit-0 warning), so putting the whole state directory out of the assistant's reach would DISABLE the guard rather than harden it. Forging the working channel is a bounded, disclosed residual, not the grant source: the validated snapshot is re-derived from the register and the checkpoint is anti-shrinkage evidence, both reconciled by the next resume audit. (To harden the working channel too, run the guard as a separate privileged writer that owns that channel, rather than making the shared account's own state directory non-writable.) Combine with entry 1's filesystem containment so the assistant's writable working area still excludes the READ sources.
- **What remains even then:** an operator or root who can write those operator-owned READ sources is trusted by construction; host clock control still defeats the freshness checks (out of threat model); and the shared-kernel and allowed-path residuals of entry 1 still apply.

---

## 3. Keep a destructive-push decision blocking, especially for irreversible bulk operations

- **Guardrail:** The protected-branch guard's decision on a remote-wide bulk push (`aiqt/10-INTEG-protected-branch-integrity`, rule `prtbrn`), with human authorization for consequential actions (`security/SECI-human-authorization`) and the verified-restore-path rule (`security/SECA-verified-restore-path`).
- **What the pack does:** the core hook returns a blocking `ask` for a `--mirror`, a forced `--all/--branches`, a `--prune` sweep (a wildcard or matching refspec, or `--all/--branches`), or a wildcard or matching refspec it cannot prove misses the protected branches, and a `deny` for a literal force-update or deletion of `main` or `master`. In Claude Code that `ask` is a blocking human prompt; the pack ships no notice or advisory mode that downgrades it. These sweeps are destructive: `--mirror` and `--prune` delete remote refs absent locally, and a deleted remote branch is not recoverable from the pushing side.
- **The gap:** the pack returns a decision, but your hook runner, wrapper, or policy layer controls whether that decision actually stops execution. A surface that maps `ask` to a warning or a non-blocking notice lets an irreversible remote-wide push proceed with no human in the loop, exactly how an unattended `--mirror` deletes branches remote-wide. The pack cannot force an external dispatcher to stop, prove server-side protection is enabled, or prove a deleted ref is recoverable.
- **What you set up:** make this guard's `ask` a blocking confirmation that halts until a human approves that exact command, and make `deny` non-executable; a missing prompt, a timeout, or a malformed decision holds rather than proceeds. Do not downgrade a `--mirror`, a `--prune` sweep, a matching or wildcard sweep, a forced `--all/--branches`, or any force or delete to a non-blocking notice. Notice or advisory rendering is safe only for an operation you have independently established is non-destructive; a per-branch force or delete can also lose the only reachable ref, so it is not automatically reversible. Keep server-side branch protection on every shared remote and maintain a restore path verified against it.
- **What remains even then:** a human can still approve the wrong remote or accept loss, and a client-side control can be bypassed. Server-side branch protection (it was the only thing that saved `main` in the incident that motivated this entry), least-privilege push credentials, protected-ref deletion restrictions, and tested backups remain independent layers.

---

*More entries are added here as guardrails with a host-level component are identified. Likely next
candidates: an enforced network egress allow-list (`security/SECC-egress-destinations`) and kernel-enforced
resource bounds (`security/SECA-resource-bounds`). If an entry does not apply to your
environment, skip it.*
