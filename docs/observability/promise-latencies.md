# Promise Latencies

Six stage latencies measure the factory's lifecycle promises. Each is the
elapsed time between two events defined in
[semantic-conventions.md](semantic-conventions.md), measured per work item
from the durable record. The pattern rationale is in
[promise-oriented-observability](../../patterns/promise-oriented-observability.md).

The interpretive rule: silence past the bound is the failure signal. Every
latency here has an open-ended failure mode where the second event never
arrives, and that absence is invisible to event-driven consumers by
construction. Detection is a periodic query over the store, so measured
detection latency is the promise bound plus up to one scan interval.

## The six stage latencies

| # | Latency | From | To | Promise |
|---|---|---|---|---|
| 1 | Claim latency | `work.ready` | `work.claimed` | Ready work gets claimed. |
| 2 | Start latency | `work.claimed` | `execution.started` | Claimed work starts. |
| 3 | Progress interval | latest of `execution.started`, `execution.attached`, `execution.progressed` | next `execution.progressed` or `artifact.prepared` | Running work progresses. |
| 4 | Verification latency | `artifact.prepared` | `verification.completed` | Completed work verifies. |
| 5 | Publication latency | `verification.completed` (verdict `pass`) | `publication.committed` | Verified work publishes. |
| 6 | Acknowledgement latency | `publication.committed` | `work.acknowledged` | Published work is confirmed in the system of record. |

All six are computed within one `work_id` and, for stages 3 and later,
within the current generation; a superseded generation's missing events are
resolved by reconciliation, not by these clocks.

## What silence means, stage by stage

**1. Ready with no claim.** The dispatch layer is dead, starved, or failing
closed. Gas City's old scheduler guaranteed 4 order launches per 2-minute
patrol against a demand of 11.07 per minute, a structural starvation
invisible to any per-order view (local observation,
gascity2026:docs/recovery/scheduler-capacity-review-9ad10d428.md).
Fail-closed on controller loss deliberately leaves work queued (local
observation,
gascity2026:docs/recovery/connection-cure-review-02669a98f.md), which
is correct exactly when this latency makes the queue visible.

**2. Claimed with no start.** A poisoned claim or a dead assignee. The
2026-07-16 chaos test SIGKILLed a worker mid-dispatch and produced a
`pending` claim refused forever with zero escalation (local observation,
gascity2026:docs/design/city-reliability-surface.md). Claims in that
system carry no TTL; a scan re-derives assignee liveness and reopens, which
only works if claimed-with-no-start is being measured.

**3. Running with no progress.** A wedged session, a dead worker whose
execution has not been reattached, or a host stalled below the process
layer. On 2026-07-27 every unit reported alive while supervisor HTTP, tmux,
and database TCP all timed out (local observation,
gascity2026:docs/recovery/demand-driven-city-recovery-2026-07-27.md).
Elapsed-time liveness cannot distinguish an idle session from one that
shipped (local observation,
gascity2026:docs/design/city-reliability-surface.md), so the progress
clock must reset only on evidence-bearing events, never on wall-clock
aliveness. Worker death is detected as missing heartbeats, silence again,
not as a positive failure report (local observation,
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).

**4. Prepared with no verification.** The verification lane never executed.
Green means executed, not asserted (local observation,
gascity2026:docs/design/software-factory-philosophy.md); an artifact
aging without a `verification.completed` is unexamined work drifting toward
a stale base, not a safe backlog.

**5. Verified with no publication.** The strand class: work finished,
verified, and never landed. Before a reaper existed, Gas City stranded
closed-but-never-merged branches at roughly 12 per day (local observation,
gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md).
Status said done; the artifact never moved.

**6. Published with no acknowledgement.** The execute-then-log interval: the
destination may hold a committed external effect that no durable local
record names (agent-era, book ch08). Recovery cannot distinguish
never-happened from happened-but-unrecorded, which is how retries duplicate
external effects (local observation, 18 of 18 unsafe worker-death trials
left two physical effects,
temporallab2026:docs/guarantees.md). This is the
shortest bound of the six because the ambiguity it measures is the one the
factory can least afford.

## Suggested alert conditions

Phrase alerts as promises broken, not resources consumed. An alert that says
"queue depth 40" invites tuning; an alert that says "the factory has broken
the claim promise for 12 work items" names the failure and its owner. Every
condition below is a query against the events table; implementations are in
[sample-queries.sql](sample-queries.sql).

| Alert | Condition | Suggested initial bound |
|---|---|---|
| Claim promise broken | latest event for a work item is `work.ready`, older than bound | 15 minutes |
| Start promise broken | claim with no execution event after it | 10 minutes |
| Progress promise broken | live execution with no evidence-bearing event within bound | 30 minutes |
| Verification promise broken | prepared artifact with no verification | 60 minutes |
| Publication promise broken | passing verification with no publication | 60 minutes |
| Acknowledgement promise broken | publication (or committed effect) with no durable acknowledgement | 5 minutes |

The bounds are tunable policy defaults, not measured optima; no source in
our evidence base derives an optimal bound (inference). Two constraints on
tuning are grounded, though. First, the bound floor is the delivery
mechanism's own latency: a 15-minute poll produced a 60m22s
review-to-dispatch latency in the pr-state-poller measurements (local
observation,
gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md),
so a bound tighter than the pipeline's event cadence only alarms on the
plumbing. Second, the query path must stay cheap enough to run at cadence:
on 2026-08-01 an aggregate history lookup blew its 15-second deadline and
`gc status` took 10.18s (local observation,
gascity2026:docs/incidents/2026-08-01-status-path-latency-recurrence.md);
a promise scan that cannot meet its own deadline is one more silent cover.

Two further promises sit outside the six stage clocks: blocked work becomes
visible (every `work.blocked` routed to its named `owner`; measure routing
latency) and recovery drains (backlog shrinks while `recovery`-lane
admissions stay inside their budget; see the reserve query in
[sample-queries.sql](sample-queries.sql) and the
[retry-storm drill](../../drills/retry-storm/)).
