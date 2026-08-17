# Fleet capacity review checklist

Scope: scheduling classes, reserves, fairness, retry dispersion, and
serialized resources under correlated failure and recovery. Each question
carries the review rule id it maps to or the drill it corresponds to.
Automated findings carry rule id, severity, a message stating the defect, and
a one-line remediation hint; a run exits 1 on any FAIL, or on any WARN under
--strict, else 0.

1. Does `scheduling.classes.recovery` exist, and does it declare a maximum?
   Recovery work without a ceiling becomes the whole fleet the moment a
   shared dependency comes back. [FLEET-001]
2. Is there a class with reserved capacity for interactive work, and is the
   reserve enforced at admission rather than by priority ordering? Priority
   without reservation still starves interactive work behind a large enough
   backlog. [FLEET-002]
3. Are `scheduling.fairness.levels` declared and non-empty, and do they say
   who yields when demand exceeds capacity? [FLEET-003]
4. Walk the storm: a shared dependency fails, N items become retryable at
   once, the dependency recovers at reduced capacity. What bounds the peak
   admitted retry rate in the first interval after recovery?
   [drill retry-storm]
5. What breaks retry synchronization? Backoff alone preserves phase
   alignment; the design must name the jitter source and where it is
   applied. [drill retry-storm]
6. Is the recovery drain rate bounded below the dependency's ceiling, not
   merely at it? Draining at the maximum the destination can absorb is the
   storm from the other direction. [drill retry-storm]
7. Can the admission controller refuse work rather than defer it, and is a
   refusal an explicit recorded decision rather than a silent drop?
   [drill retry-storm]
8. Is backlog work interruptible by newly arriving interactive work, or does
   an item that entered the queue hold its slot to completion? Lower
   admission priority alone does not free a running slot. [FLEET-002]
9. Which single layer owns retry policy fleet-wide, and what audit shows the
   other layers are configured to zero retries? Assume an uncontrolled retry
   layer exists underneath any harness that has not been measured at a
   deliberately failing endpoint, and size limits accordingly.
   [drill retry-storm]
10. Are limits declared per tenant or per account rather than only per
    provider, so one account hitting its ceiling rebalances instead of
    stalling the fleet? [FLEET-003]
11. Enumerate the serialized resources the fleet shares: connection budgets,
    single-writer stores, lock-holding maintenance operations, per-host
    memory. For each: what is the cap, and is it enforced somewhere the work
    cannot route around? A limit enforced only in the well-behaved client is
    a suggestion. [drill retry-storm]
12. For each serialized resource: what happens at exhaustion? Distinguish
    refuse-new (bounded, recoverable) from wedge-all (a full stop that needs
    an operator); a design that cannot answer has chosen wedge-all by
    default. [FLEET-001]
13. Does scheduled cadence derive from measured demand plus headroom, or
    from a constant chosen at design time? A fixed budget below demand is a
    permanent starvation regime, not a tuning knob. [FLEET-003]
14. Is recovery throughput measured and retained per failure ordinal (first
    fault, second fault, third), so degradation across repeated faults is
    visible instead of averaged away? [drill retry-storm]
15. During recovery, do interactive latency and admission stay within their
    declared bounds while the backlog drains, and is that claim tested with
    interactive load present rather than asserted from the quiet?
    [FLEET-002]
