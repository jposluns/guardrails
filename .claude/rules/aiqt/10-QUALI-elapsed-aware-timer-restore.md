---
corpus-id: tmrrst
origin: pack
family: aiqt
tier: 10
facet: QUALI
secondary: [INTEG]
slug: elapsed-aware-timer-restore
---

# A borrowed process timer is restored elapsed-aware

Code that borrows a caller's process-global deadline facility for a bounded window, a signal handler,
an interval timer, or a pending single-shot alarm, restores the caller's state elapsed-aware, never
verbatim. A monotonic timestamp is recorded when the borrow opens; on restore the saved remaining
interval of an active caller timer is reduced by the elapsed time the window consumed. A caller
deadline that would have expired during the window is made immediately deliverable at the platform's
minimal positive delay, never dropped and never re-armed at its full original value. An inactive
caller timer remains inactive rather than being armed by the expiry clamp. A pending notification or
single-shot alarm is preserved, not consumed or discarded. A periodic timer's repeat interval, and
its phase to the extent the platform can express it, are preserved. The original handler is
reinstalled through exception-safe cleanup. The save and the elapsed-aware restore live once, in a
single shared, nesting-safe helper, so no call site can hand-roll a variant that reintroduces the
verbatim restore, and the helper itself remains subject to the same inspection as any call site.
Where the platform cannot preserve the caller's state reliably, the code uses a timeout mechanism
that is not process-global, or refuses the borrow, rather than corrupting the caller's deadline,
most dangerously a watchdog's. This is the companion of the kill-timeout rule: that one keeps a
wrapper's bound from cutting a correctly-waiting callee short, while this one keeps a borrower's
restore from silently moving or losing the deadline the caller had already set.
