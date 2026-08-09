# Drill: retry-storm

## Question

A shared dependency fails and a large set of in-flight work becomes retryable
at the same moment. The dependency then recovers at reduced capacity. Does the
retry rate stay bounded, does interactive capacity stay available, and do the
retries disperse instead of arriving as synchronized waves?

This drill descends from the factory's recovery-capacity design: "The
correlated failure is not the problem; the correlated recovery is. A fleet that
all retries at once turns a provider outage into a second outage on the way
back up" (local observation:
gascity2026:docs/design/retry-and-recovery-capacity.md). The failure has
been lived: unbounded local work took the fleet down through the OS
out-of-memory path once, which is why the limit must be enforced somewhere the
work cannot route around (local observation, same file), and a scheduler
guaranteeing 2 launches per minute against 11.07 per minute of demand starved
the fleet (local observation:
gascity2026:docs/recovery/scheduler-capacity-review-9ad10d428.md).

## Invariant

Throughout recovery: the aggregate admitted retry rate never exceeds the
declared recovery-class maximum; the interactive class retains its reserved
capacity and its work is admitted within that reserve; retries are dispersed
in time rather than synchronized; and the drain rate toward the recovering
dependency stays below its declared ceiling, because draining at the maximum
the destination can technically absorb is the retry storm from the other
direction (local observation, same design file as above). Exponential backoff
plus random jitter plus a concurrency limit plus an admission controller that
can refuse rather than defer is the established anti-herd recovery path
(foundational; adopted as the required path in the same design file).

## Initial state

- N work items (N large relative to steady-state concurrency, for example
  N = 100 with steady concurrency 10) are in flight against one shared
  dependency `D`.
- Scheduling declares at least two classes: `interactive` with a reserved
  capacity, and `recovery` with a hard maximum; fairness levels are declared.
- Exactly one layer owns retry policy for `D`; every other layer (client
  library, harness, transport) is configured to zero retries; four
  uncoordinated layers each retrying three times is a multiplicative
  amplifier firing hardest during an outage (local observation, same file).
- `D` is healthy and metering: it records every arriving request with a
  harness sequence number and can enforce a capacity ceiling `r`.

## Fault barrier

Named barrier: `dependency-restored-backlog-pending`. Events on either side of
injection: before, the durable record that all N items have failed against `D`
and entered the retryable set; after, the first retry admission decision
following `D`'s restoration. The components faulted are `D` (taken down, then
restored at reduced capacity r much smaller than N per interval). The barrier
is checkable: the controller proceeds only after reading the retryable-set
size equal to N and `D`'s state flag showing restored-at-capacity-r. The
dangerous window opens at restoration, when everything is eligible at once.

## Injected fault

Fail `D` so all N items enter the retryable set; restore `D` with an enforced
ceiling of r requests per scheduling interval, well below the naive
simultaneous demand of N. Meanwhile, submit a steady trickle of interactive
work throughout the recovery, so the reserve claim is tested under pressure
rather than asserted in the quiet.

## Expected observations

- Retry wakeups spread across intervals: jitter breaks the synchronization
  that backoff alone preserves (local observation:
  gascity2026:docs/design/retry-and-recovery-capacity.md).
- Admitted retry concurrency never exceeds the recovery-class maximum in any
  interval; excess demand queues or is refused with an explicit admission
  decision, never silently dropped.
- Requests arriving at `D` stay at or below r per interval; `D` records no
  overload rejection after restoration.
- Interactive submissions are admitted within their reserve during the entire
  recovery; their latency does not degrade to the recovery queue's timescale.
- Backlog items are interruptible: a newly arriving interactive item preempts
  or displaces queued backlog rather than waiting behind it.
- The backlog drains to zero over multiple intervals; the drill retains the
  per-interval drain profile by failure ordinal, since a pooled mean conceals
  degradation across repeated faults (local observation: manuscript ch09 under
  ercabook2026:chapters/).

## Unsafe negative control

Remove jitter, the class split, and the admission ceiling: every item retries
immediately on restoration, in the same class as interactive work. Expected
violation signature: an arrival spike at `D` of order N in the first interval,
overload rejections or induced second failure at `D`, interactive work starved
behind the wave, and synchronized retry waves persisting across backoff cycles
(the herd re-forms because nothing broke the phase alignment). The oracle must
flag the peak arrival rate, the reserve violation, and the synchronization.

## Pass condition

1. Barrier report shows retryable-set size N and `D` restored at ceiling r
   before the first admission decision.
2. Max per-interval arrivals at `D` <= r, and max admitted recovery
   concurrency <= the declared recovery maximum, over the whole run.
3. Dispersion: no interval contains more than a declared fraction (for
   example 25 percent) of all retries; the measure is computed from `D`'s
   arrival log, not from the scheduler's intent.
4. Every interactive submission during recovery is admitted within the
   reserve, and interactive latency stays within its declared bound.
5. Unsafe mode violates checks 2 through 4 and the oracle reports which.
   Protected exits 0; unsafe exits 2.

## Evidence to retain

- `D`'s arrival log: (sequence, interval, item id, class) for every request.
- Admission decision log: admitted, queued, refused, with class and reason.
- Per-interval histogram of retry arrivals (the dispersion evidence).
- Interactive latency series across the recovery window.
- The class configuration snapshot: reserves, maxima, fairness levels.
- Backlog depth per interval until drain completes.

## What a pass does not establish

- Nothing about hidden retry layers underneath harnesses we do not control;
  the settling test is counting requests at a deliberately failing endpoint
  (local observation:
  gascity2026:docs/design/retry-and-recovery-capacity.md). The drill
  counts only the layers it instruments.
- Nothing about per-tenant or per-account limit sizing; the drill uses one
  dependency and one budget, while real fleets need per-account limits that
  rebalance independently (local observation, same file).
- Simulated capacity on one harness is not a production ranking; the source
  recovery-dynamics experiments carry the same restriction (local
  observation: temporallab2026:docs/guarantees.md,
  bounded recovery dynamics entry).
- Bounded recovery does not prove fast recovery; a factory can pass this
  drill and still drain its backlog slower than the business tolerates.

## Run

Specification drill: execute against a real factory through its adapter; no
in-memory implementation exists as of 2026-08-09.
