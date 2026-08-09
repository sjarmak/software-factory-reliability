# Drill: event-is-lost

## Question

External state changes and the notification announcing the change is dropped in
transit. Does a level-triggered reconciler observe the current external state
on its next pass and complete the pending transition, without anyone replaying
the lost event?

This drill descends from the factory's event-versus-reconciliation rule:
"Signals advance, queries repair. Events are for latency; the correctness path
is always a level-triggered reconciler" (local observation:
gascity2026:docs/design/software-factory-philosophy.md). The drop is not
hypothetical: the observed transport drops events silently by design,
discarding records on lock contention and writing errors to stderr, never
returning them (local observation:
gascity2026:docs/design/city-reliability-surface.md). A signal contract
with no reconciler "rots invisibly" (local observation: the same philosophy
document).

## Invariant

Liveness: every committed external state change eventually produces the
corresponding factory state transition, within one full reconciliation pass
after the change is observable, whether or not the notification was delivered.
Safety: the reconciler decides from a fresh read of the source of record, not
from remembered event history; "a reliability layer is a query, not a memory"
and "anything that watches by remembering goes wrong silently" (local
observation: gascity2026:docs/design/software-factory-philosophy.md).
Level-triggered reconciliation toward observed state is the foundational
control-loop pattern for exactly this loss mode (foundational).

## Initial state

- Factory state records entity `E` in state `awaiting-external`, correctly
  reflecting the external system at drill start.
- The external system supports a durable state change on `E` and an
  event emission announcing it; the transport between emitter and consumer is
  under drill control and can drop a named event deterministically.
- An event consumer exists that, on delivery, advances `E` promptly (the
  latency path).
- A reconciler exists that on each pass reads the external source of record
  for all entities in `awaiting-external` and repairs divergence (the
  correctness path). In the in-memory adapter, passes are explicit ticks
  driven by the harness, not wall-clock timers.

## Fault barrier

Named barrier: `change-committed-notification-dropped`. Events on either side
of injection: before, the external system's durable commit of `E`'s state
change and the handoff of the notification to the transport; after, the next
reconciler pass that includes `E` in its query. The component faulted is the
transport, not a process kill. The barrier is checkable: the controller
proceeds only after reading the external source showing the new state, and the
injection log showing the notification for `E` was discarded before consumer
delivery. No sleeps; the reconciler pass is invoked as a named step, so the
window between drop and repair is causal, not temporal.

## Injected fault

Drop the single notification for `E` at the transport, recording the drop in an
injection log. Nothing else is perturbed: no process dies, the external change
stands, and the consumer simply never hears about it. This models the observed
loss mode where the transport discards silently rather than failing loudly.

## Expected observations

- No event-driven transition occurs for `E`; the consumer's delivery log shows
  no delivery for the dropped event identity.
- The next reconciler pass reads the external source of record, observes `E`'s
  current state, detects the divergence from factory state, and performs the
  transition.
- Any event the repair emits is a record of an already-completed repair, not
  a prerequisite for it (local observation: the dead-assignee repair,
  gascity2026:docs/design/city-reliability-surface.md).
- The transition completes without the dropped event being replayed, retried,
  or reconstructed from any event archive.
- Factory state for `E` equals external state at end of run.

## Unsafe negative control

Disable the reconciler and rely on the event path alone, the edge-triggered
fire-and-forget shape observed in the field, where components stamp "I acted"
and never re-check (local observation: the fire-and-forget family,
gascity2026:docs/design/city-reliability-surface.md). Expected
violation: `E` stays in `awaiting-external` past the pass bound at which the
protected configuration converges; the work is stranded. The bound is counted
in harness-driven passes, so the unsafe run fails deterministically rather
than by timeout, and the oracle flags the liveness violation.

## Pass condition

1. Barrier report shows the external commit and the recorded drop, and shows
   no consumer delivery for the event identity.
2. Protected mode: factory state for `E` matches external state within one
   full reconciliation pass after the drop; the repairing read of the source
   of record is present in the log.
3. The repair consumed no replayed or reconstructed event; the only input to
   the decision is the fresh read.
4. Unsafe mode: divergence persists past the protected convergence bound and
   the oracle reports the stranded transition. Protected exits 0; unsafe
   exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, UTC time, event kind, entity,
  factory state, external state, pass number).
- The injection log entry for the dropped notification, with event identity.
- The consumer delivery log (empty for the dropped identity).
- The reconciler's query input and output for the repairing pass: what it read
  from the source of record and what divergence it computed.
- Before and after snapshots of factory state for `E`, and oracle output for
  both modes.

## What a pass does not establish

- Nothing about latency: the event path exists to be fast, and this drill
  never measures it. A factory that passes can still be slow to react by one
  full reconciliation interval.
- Nothing about reconciler correctness under a stale or cached read; a query
  cache keyed wrongly would pass here and still rot (inference: follows from
  the safety clause, not demonstrated as a separate drill).
- Nothing about the reconciler's own failure domain. A cover sharing the
  failure domain of what it guards has moved the single point of failure, not
  removed it (local observation: "covers die too,"
  gascity2026:docs/design/software-factory-philosophy.md).
- In-memory passes are harness ticks; a real deployment must also show the
  pass cadence is maintained, since a dormant scan went unnoticed from
  2026-07-06 to 2026-07-16 (local observation: gc-qo3,
  gascity2026:docs/design/city-reliability-surface.md).

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 -m adapters.in_memory.run_drill event-is-lost --mode protected
python3 -m adapters.in_memory.run_drill event-is-lost --mode unsafe
```
