# Drill: worker-dies-agent-survives

## Question

When the worker process that started an agent session dies before recording any
checkpoint, does the retry attempt resolve the current logical claim, discover
the already-running session, and attach to it instead of launching a second one?

This drill descends from the worker-death experiment (local observation:
temporallab2026:experiments/worker-death/README.md and
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
There, "a stable application session key made two task attempts converge on one
external process," and the engine "did not directly detect or adopt the child.
It detected missing Activity heartbeats and retried procedure." The agent
process is an external managed resource that can outlive the worker that
launched it (local observation:
temporallab2026:docs/architecture.md).

## Invariant

One logical operation has no more than one authorized writer at a time and
eventually exposes one accepted terminal outcome (local observation, taken
verbatim from
temporallab2026:experiments/worker-death/README.md).
Concretely for this drill: across worker death and retry, the logical claim maps
to exactly one launched session, at most one current writer generation, and one
accepted outcome. Recovery must re-derive state from the durable session
registry, never from the dead worker's memory or the agent's self-report
(local observation: gascity2026:docs/design/software-factory-philosophy.md,
constraint "Agents are nondeterministic; the factory must not be").

## Initial state

- One logical claim `claim-1` is open and assigned; no prior attempts exist.
- The session registry is empty for `claim-1`: no session identity recorded.
- Worker W1 holds the claim under ownership generation 1.
- The destination (session substrate) can launch sessions and report their
  identity; the registry supports an atomic start-or-attach lookup keyed by the
  stable session key derived from `claim-1`.
- The retry mechanism is armed: loss of W1 produces a second delivery of the
  same logical work to worker W2 (at-least-once delivery is the foundational
  baseline for any redelivery mechanism).

## Fault barrier

Named barrier: `session-registered-pre-checkpoint`. The event immediately
before injection is the durable registration of the running session (the
registry maps the stable session key for `claim-1` to session S1 with its
process identity, under generation 1). The event immediately after injection is
W2's first read of that registry entry. The component killed is worker W1. The
barrier is checkable: the drill controller proceeds only after reading the
registry entry back and confirming S1 is registered and no checkpoint record
exists. No sleeps; "wall-clock sleeps are not a synchronization contract"
(local observation: temporallab2026:docs/architecture.md).
In the five-interval kill taxonomy this is a kill after the external
commitment (the session launch) and before the local completion record (local
observation: the five-point kill sweep, manuscript ch09 under
ercabook2026:chapters/).

## Injected fault

Kill worker W1 ungracefully at the barrier: after S1's registration is durable
and before any checkpoint or heartbeat is recorded. The controller must confirm
the kill actually landed on W1's exact process (exit status or process-identity
reread); an unconfirmed kill invalidates the trial rather than counting as a
pass (local observation: manuscript ch09 worst-case protocol).

## Expected observations

- Retry delivers `claim-1` to worker W2 as a new attempt (attempt 2).
- W2 resolves the current logical claim and performs the atomic stable session
  lookup; the lookup returns S1 with its recorded identity and generation.
- W2 attaches to S1 and launches no second session; the session's identity is
  unchanged across the kill (local observation: finding 0001, "Attempt 2
  receives the generation-1 lease and launches no child").
- Exactly one launch decision exists for `claim-1` over the whole run.
- One writer generation is current at every point; one accepted terminal
  outcome is eventually exposed.

## Unsafe negative control

Remove the start-or-attach lookup: the retry path launches a fresh session
unconditionally. The invariant must demonstrably break, and the oracle must
catch it; a protection you cannot watch fail is not being tested (local
observation: methodology requirement, run the unsafe control and prove the
oracle catches its violation,
temporallab2026:docs/experiment-methodology.md). The
expected violation signature matches the recorded control: two launch
decisions, two process identities, two accepted effects, one accepted outcome
(local observation: finding 0001 unsafe control).

## Pass condition

All of the following, checked mechanically from the retained evidence:

1. The barrier report names the registration event before the kill and the
   attempt-2 lookup after it, and the kill confirmation is present.
2. Launch-decision count for `claim-1` equals 1.
3. Attempt 2's resolved session identity equals attempt 1's registered session
   identity (same S1).
4. At most one current writer generation at every event in the ordered log.
5. Exactly one accepted terminal outcome.
6. The unsafe mode run violates check 2 (two launches) and the oracle reports
   the violation. Protected mode exits 0; unsafe mode exits 2.

## Evidence to retain

- Raw ordered event log (JSONL, store-assigned sequence, UTC time, event kind,
  session, generation, worker, process identity, attempt number), append-only.
- Identity mapping table: logical claim id, attempt ids, session key, session
  id, process identity, generation. All must be mutually distinct fields.
- Barrier report naming the pre-kill and post-kill events.
- Kill confirmation record for W1.
- Final registry snapshot and the oracle's per-check output for both modes.

## What a pass does not establish

- It does not establish that a launch record implies a live process; the same
  `launch_pending` state was observed to mean both no process and a live
  unregistered child, and the pre-exec phantom gap is a separate boundary
  (local observation: findings 0002 and 0005 under
  temporallab2026:docs/findings/).
- One kill placement under one configuration, not general fault tolerance
  (local observation: manuscript ch09 makes this restriction explicit).
- The in-memory adapter does not exercise OS process identity, PID reuse, or
  real scheduler and heartbeat timing; those claims require a live substrate
  (local observation: methodology,
  temporallab2026:docs/experiment-methodology.md).
- It says nothing about a stale writer returning later; that is the
  stale-writer-completes drill.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 -m adapters.in_memory.run_drill worker-dies-agent-survives --mode protected
python3 -m adapters.in_memory.run_drill worker-dies-agent-survives --mode unsafe
```
