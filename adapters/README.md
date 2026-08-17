# Factory adapter protocol

A drill talks to a factory only through this protocol. The same drill then
runs unchanged against the in-memory reference factory in `in_memory/` and
against a real factory behind an adapter you write. Message shapes live in
`protocol.schema.json` (JSON Schema draft 2020-12).

## Transport

JSON over stdio, one message per line. The driver writes one command object
per line; the adapter answers with exactly one observation object per line,
in order. Strict request/response; the adapter never sends unsolicited
messages. Commands are `{"op": ..., "params": {...}}`; observations are
`{"op": ..., "ok": true, "data": ...}` on success and
`{"op": ..., "ok": false, "error": ...}` on failure. The reference adapter
also exposes the same ops as an importable API
(`adapters.in_memory.adapter.InMemoryAdapter.handle`), which the tests use
for speed; the stdio loop is `python3 -m adapters.in_memory.adapter`.

## Ops and what each must guarantee

- `seed(scenario, mode)`: after `ok`, the factory holds exactly the
  scenario's initial state and nothing has executed. `protected` wires the
  safety mechanism under test; `unsafe` wires the negative control the
  oracle must catch.
- `start`: marks the run started. Scenario steps advance only inside
  `wait_for_barrier`, so the driver controls how far execution goes.
- `wait_for_barrier(name)`: on `ok`, every step before the named barrier
  has run, no step after it has, and the system is paused at that exact
  point. Reaching an earlier barrier on the way is recorded in coverage but
  does not pause. The terminal barrier is named `run-complete`.
- `inject_fault(kind, at_barrier)`: applies the fault while paused at
  `at_barrier`; the adapter must reject the call if the run is paused
  anywhere else. Kinds: `kill_worker` (the worker process dies; sessions it
  launched survive), `drop_event` (the bus drops the next emitted event),
  `expire_lease` (the work item's current lease is invalidated),
  `drift_resource` (a resource a guarded write depends on moves out of the
  state that write requires, and is otherwise untouched).
- `advance_generation(work_id)`: allocates the next monotonic ownership
  generation with a fresh lease; the returned generation is strictly
  greater than every earlier one for that work item.
- `read_authoritative_state`: the application's ledger view (work items,
  claims, sessions, effect records). This view may be stale; some drills
  measure exactly that gap.
- `read_external_effects`: ground truth at the destination (mutations with
  effect ids and receipts, the published artifact). Must be read from the
  destination itself, never derived from the ledger, or the event-loss and
  duplicate-effect oracles become circular.
- `read_running_executors`: workers and sessions currently alive, reported
  independently, because a session can outlive the worker that launched it.
- `read_campaign_coverage`: barriers reached, faults injected, and whether
  the run reached `run-complete`.
- `collect_evidence`: the full record: scenario, mode, barriers, faults,
  the ordered per-tick event log, and the identity mappings (work,
  generation, attempt, session, worker, effect, artifact). The methodology
  contract requires those identity classes in evidence (local observation,
  temporallab2026:docs/experiment-methodology.md).

## Why barriers are named points, not delays

A drill's conclusion is a claim about a causal order: the fault landed
after event A and before event B. A named barrier makes that order part of
the protocol; the adapter proves the system is paused at the stated point
before the fault is injected, so the evidence can name the events
immediately before and after injection. A sleep proves nothing about
order: it makes the result depend on scheduler and load timing, and a
passing run cannot distinguish "the fence held" from "the race never
happened." The methodology this repository follows states both halves
directly: "Named barriers make the dangerous window causal" and
"Wall-clock sleeps are not a synchronization contract" (local observation,
temporallab2026:docs/architecture.md).

## Writing an adapter for a real factory

Implement the ten ops against your factory and speak the transport above.
The hard parts are the guarantees, not the plumbing:

- Barriers need real instrumentation: a hook, a breakpoint queue, or a
  coordination point inside the factory that reports the boundary and
  blocks until released. If your factory cannot pause at a named point,
  say so; do not fake the barrier with a sleep.
- `inject_fault` must act on the real component: `kill_worker` should kill
  the actual worker process (and evidence should record which process, by
  PID plus start identity, since a PID alone is reusable; local
  observation, temporallab2026:docs/findings/
  0006-cancellation-requires-application-revocation.md).
- `read_external_effects` must query the destination system of record.
- Evidence must preserve raw ordered events before interpretation, and the
  identity mappings must distinguish logical identity (work, session,
  generation, effect) from delivery and process observations (attempt
  numbers, PIDs); that distinction is the point of the drills (local
  observation, temporallab2026:docs/
  experiment-methodology.md).
- The reference factory is fully deterministic (logical ticks, counter
  ids). A real adapter cannot promise that, but it must still emit a
  totally ordered event log with stable identities so two runs are
  comparable.

Driver exit-code contract: 0 when the protected arm's oracle passes, 2 when
the unsafe arm's oracle detects the expected violation (the violation is
the demonstration), 1 for anything else.
