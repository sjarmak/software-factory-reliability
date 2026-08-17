# Promise-Oriented Observability

> **Problem** Nothing is erroring and no work is moving.
>
> **Rule** Alert on the promise that was not kept, not on the component
> that did not complain.
>
> **Required property** Every promise in the lifecycle has a written bound
> to its confirming event, and the absence of that event past the bound is
> itself a first-class alertable observation, derived by querying the
> durable record rather than by trusting a component's self-report.
>
> **Wrong** `every component reports healthy -> page nobody`
>
> **Right** `claimed at T, no started event by T plus the bound -> page on the broken promise`
>
> **See it fail**
>
> - [`drills/retry-storm/`](../drills/retry-storm/), a specification with no executable arm yet
>
> **Checked by** `OBS-001` in the [rule
> catalog](../docs/contract-reference.md)

## Problem

Conventional monitoring watches processes and resources: is the service up,
is CPU below threshold, is the queue shallow. A software factory's
characteristic failures do not present that way. They present as absence:
work that is ready and never claimed, a claim that never starts, a running
job that stops emitting progress, a verified artifact that never publishes.
Every process involved can be alive and green while the factory as a whole
produces nothing. A thousand healthy agent processes can coexist with a dead
factory, because process health and promise keeping are different properties
measured at different layers.

The factory makes a chain of promises, and each one is only observable as an
event that follows another event within a bounded time: ready work gets
claimed, claimed work starts, running work progresses, completed work
verifies, verified work publishes, published work is acknowledged in the
system of record, blocked work becomes visible to someone who can act, and
recovery backlogs drain. Monitoring that cannot see a broken promise is
monitoring the wrong object.

Related pages: the event vocabulary these promises are measured in is
[semantic-conventions](../docs/observability/semantic-conventions.md); the stage
latencies and alert conditions are
[promise-latencies](../docs/observability/promise-latencies.md); campaigns add a
coverage promise described in
[cross-repo-campaigns](cross-repo-campaigns.md).

## Observed failure

On 2026-07-15 the Gas City host died hard at 08:11 EDT, an outage of about
85 minutes. Committed_AS had been at 68 to 75G on a 62.4G box with swap free
between 0.0 and 0.2MB for hours. The `resource-sweep` cover ran on schedule
at 06:32, saw MemAvailable at 14.5G with swap 99 percent consumed, and
stayed silent, because it gated only on MemAvailable below 4G. tmux refresh
degraded from 3m14s at 07:26 to 18m22s at 07:49. The RCA's summary: the
cover ran, stayed green, and watched the box die; right cadence, wrong
metric.
(gascity2026:docs/rca-host-death-2026-07-15-memory-exhaustion.md)

The pattern is general in that system's own audit. Order `maintenance-cycle`
sat dormant for 10 days (2026-07-06 to 2026-07-16) before anyone noticed,
and the cause was a deliberate uncommented override, not a bug (gc-qo3). Of
106 live orders, 83 never check whether work moved, and 105 have no
dedicated watchdog. The fastest loops are the blindest: a 30-second liveness
check reports green 120 times an hour while zero beads are dispatched. At
incident time there was zero automated cover; every one of these failures
was found by a human reading journals or source.
(gascity2026:docs/design/city-reliability-surface.md)

## Invariant

Every promise in the lifecycle has a bounded time to its confirming event,
the bound is written down, and the absence of the confirming event past the
bound is itself a first-class, alertable observation, derived by querying the
durable record, never by trusting a component's self-report or a monitor's
memory.

## Mechanism

The lifecycle is instrumented as events against durable identities
(see [semantic-conventions](../docs/observability/semantic-conventions.md)). Each
promise is a pair of events and a bound:

```
promise                     confirming interval
ready gets claimed          work.ready        -> work.claimed
claimed starts              work.claimed      -> execution.started
running progresses          execution.*       -> next execution.progressed
completed verifies          artifact.prepared -> verification.completed
verified publishes          verification.completed -> publication.committed
published acknowledges      publication.committed  -> work.acknowledged
blocked becomes visible     work.blocked      -> routed to a named owner
recovery drains             backlog size falls while reserve holds
```

Silence in any interval is the failure signal. A detector for silence cannot
itself be event-driven, because the failure being detected is exactly that an
expected event did not arrive; events drop silently at transport boundaries
by design in real systems. Detection is therefore a level-triggered scan that
re-derives the set of broken promises from the store on every tick. The scan
holds no state between ticks; a reliability layer is a query, not a memory.

Cardinality is split deliberately. High-cardinality identities (work_id,
attempt_id, session_id, artifact_digest, effect_id) go into traces and
queryable records, where joins answer identity questions: which work item,
which attempt, which artifact. Metrics carry only low-cardinality labels
(promise, stage, lane, outcome) and answer rate and distribution questions:
how many claim promises are currently broken, what the p95 publication
latency is. Putting a work_id into a metric label explodes the metric store;
keeping identities out of queryable records makes broken promises
undiagnosable. Both layers exist because they answer different questions.

The recovery-drains promise needs one more measurement: recovery traffic must
consume only its reserved share of capacity. A fleet that all retries at once
turns a provider outage into a second outage on the way back up, so recovery
is admitted from a separate budget (a starting split of roughly 70 percent
current work, 30 percent recovery) and the observable promise is that the
backlog shrinks while the interactive reserve stays intact. The matching
query is in [sample-queries](../docs/observability/sample-queries.sql).

## Where enforcement occurs

The scanner runs outside the failure domain of the thing it watches. A cover
that shares its subject's failure domain has moved the single point of
failure, not removed it; whatever watches the scan layer must not be a scan.
Gas City's substrate-independent lane for this is a plain OS-level timer,
chosen precisely because it shares nothing with the supervisor it covers.

Bounds are enforced against the durable event record, not against in-process
state. On 2026-07-27 the same host showed why: a kernel flush-workqueue storm
put all 16 CPUs at 95 to 100 percent system time, and supervisor HTTP,
pprof, mail, tmux, and the database TCP port all timed out while the service
manager reported every unit alive. Liveness answered; no promise-bearing
event could have.
(gascity2026:docs/recovery/demand-driven-city-recovery-2026-07-27.md)

Broken-promise alerts route to an owner, and fail-closed states must
escalate: a promise parked as blocked without an escalation path self-erases.

## Does not guarantee

- Presence of events does not prove outcomes. gc-na2o produced 1384 audit
  events asserting success that never happened; the acknowledgement promise
  must be confirmed at the system of record, not at the emitter.
- No detection of confidently wrong work. A job progressing briskly toward a
  bad artifact keeps every promise until verification.
- No detection latency below the scan cadence. A bound of N minutes checked
  every M minutes alerts in up to N+M.
- No protection when the watcher shares the watched failure domain; that
  independence must be arranged structurally and re-checked as the system
  changes.
- No effect-level safety. Observability sees a duplicated publication; it
  does not prevent one. Fencing and idempotency live in the execution path.
- No semantic quality judgment. Verification verdicts are inputs to the
  promise chain, not products of it.

## Failure drill

The matching drill is [retry-storm](../drills/retry-storm/): a provider
outage ends, every failed work item becomes eligible at once, and the
recovery-drains promise is the one under test; the drill checks that the
backlog drains from its own budget without consuming the interactive reserve
and without synchronized retry waves.

## Evidence

- Cover ran, stayed green, watched the box die (2026-07-15, ~85 minute
  outage, wrong-metric gate): local observation
  (gascity2026:docs/rca-host-death-2026-07-15-memory-exhaustion.md).
- 10-day dormant order found by a human diff (gc-qo3, 2026-07-06 to
  2026-07-16): local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- 83 of 106 live orders never check whether work moved; 105 of 106 have no
  dedicated watchdog; 30-second liveness loops green while zero beads
  dispatch: local observation, same file.
- Zero automated cover at incident time; every failure found by a human:
  local observation, same file.
- All-green process table over a wedged host (2026-07-27 flush-workqueue
  storm): local observation
  (gascity2026:docs/recovery/demand-driven-city-recovery-2026-07-27.md).
- Events drop silently at the transport (errors to stderr, flock-timeout
  drops, fire-and-forget exec provider); correctness path is a
  level-triggered reconciler: local observation
  (gascity2026:docs/design/city-reliability-surface.md,
  gascity2026:docs/design/software-factory-philosophy.md).
- Silence as the detection channel for worker death (missing heartbeats, not
  positive failure reports, triggered recovery): local observation
  (temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
- A session alive and idle is indistinguishable from one that shipped when
  only liveness is measured: local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- Poll-bound blindness costs real latency (60m22s review-to-dispatch on a
  15-minute poll; ~72 API calls/hour to learn nothing): local observation
  (gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md).
- Correlated recovery as a second outage; separate recovery budget; recovery
  slower than the provider can absorb: local observation
  (gascity2026:docs/design/retry-and-recovery-capacity.md); backoff
  with jitter breaking retry synchronization: foundational.
- High-cardinality identities in records and traces, low-cardinality labels
  in metrics: foundational (established observability practice).
- The eight-promise decomposition as the factory's alerting surface:
  inference (our synthesis; each promise's failure mode is observed, the
  unified surface is not).

## Limits

The promise bounds themselves are policy, and our sources give little help
choosing them: the observed systems either had no bounds (the 10-day dormant
order) or bounds gated on the wrong variable (the 4G MemAvailable gate). The
suggested starting values in
[promise-latencies](../docs/observability/promise-latencies.md) are stated as
tunable defaults, not measured optima.

Watching outcome instead of liveness costs queries against the store on
every scan tick; Gas City's 2026-08-01 recurrence (an aggregate order-history
lookup exceeding its 15s doctor deadline, `gc status` at 10.18s) shows the
observability path itself can breach its own latency promises under store
pressure. The scan must be cheap enough to run at cadence or it becomes
another silent cover.

The blocked-becomes-visible and recovery-drains promises lack the crisp
two-event structure of the other six; their measurements (routing latency,
backlog slope under a held reserve) are less portable across systems.

## Sources

- gascity2026:docs/rca-host-death-2026-07-15-memory-exhaustion.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/recovery/demand-driven-city-recovery-2026-07-27.md
- gascity2026:docs/design/retry-and-recovery-capacity.md
- gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md
- gascity2026:docs/incidents/2026-08-01-status-path-latency-recurrence.md
- temporallab2026:docs/findings/0001-worker-death-surviving-agent.md
