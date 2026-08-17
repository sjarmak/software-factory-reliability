# Start-or-Attach

> **Problem** A retry launches a second executor while the first is still
> running.
>
> **Rule** Look for a live session before you launch one.
>
> **Required property** For one work_id and generation, at most one live
> executor session exists, and executor creation happens only through a
> decision that is atomic with the session registry.
>
> **Wrong** `retry -> launch an executor`
>
> **Right** `retry -> atomic registry lookup -> attach to the live session, or launch and register in one step`
>
> **See it fail**
>
> - `make drill DRILL=worker-dies-agent-survives MODE=unsafe` exits 2
> - `make drill DRILL=worker-dies-agent-survives MODE=protected` exits 0
>
> **Checked by** `RECON-002` in the [rule
> catalog](../docs/contract-reference.md)

## Problem

A retrying orchestrator must produce an executor for a claim it holds. Naive
retry code launches a fresh executor every time, on the assumption that the
previous attempt died with the previous worker. The assumption is false for
coding agents: the agent is a separate operating-system process, and it can
outlive the orchestrator, the worker, and the workflow attempt that launched
it. When the retry arrives, a fully capable agent may still be running,
holding the workspace, mid-way through the task.

Launching under that condition creates two capable agents with the same
assignment, the same instructions, and the same target repository. The
duplicate is expensive even when fencing makes its writes inert, and without
fencing it produces duplicate external effects. So executor creation is a
decision, and the decision must be resolved against a registry of live
sessions before any process is spawned, atomically with the claim that
authorizes it.

## Observed failure

In the temporal_projects worker-death experiment, the controller SIGKILLed
Worker 1's exact PID between the before-effect and first-heartbeat barriers,
then reread /proc/<pid>/stat and the Linux boot id to prove the registered
agent child, not a reused PID, was still alive. The unsafe control's retry
launched blind: the evidence records two launch decisions, two process
identities, and two accepted effects for one logical operation, with one
accepted outcome and one terminal completion rejection.
(temporallab2026:docs/findings/0001-worker-death-surviving-agent.md)

The reattachment arm under the identical fault made two task attempts
converge on one external process: attempt 2 resolved the stable session key,
received the generation-1 lease, and launched no child; the child retained
the same PID and start identity across the worker death, and the run ended
with one executor, one effect, one outcome.
(temporallab2026:experiments/worker-death/README.md;
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md)

The same shape recurs in production settings without any injected fault: Gas
City's duplicate singleton-pool sessions from alias collision reappear on
every restart and are drained by a supervising cycle as a stopgap (dr-l66ch).
(gascity2026:docs/design/city-reliability-surface.md)

## Invariant

For one (work_id, generation), at most one live executor session exists, and
a retry attempt resolves to the existing session when one is live instead of
creating another. Executor creation happens only through a decision that is
atomic with the session registry.

## Mechanism

The decision runs as one transaction against the session registry in the
store of record, keyed by the stable application session key (see
[Stable Work Identity](./stable-work-identity.md)):

```
resolve_executor(work_id):
  tx:
    s = registry.get(session_key(work_id))
    if s is absent:
        registry.put(session_key, state=launch_pending, gen=next)
        return START            # spawn, then register pid + start identity
    if s.state == running and generation/owner checks pass:
        return ATTACH(s)        # adopt the live session, same generation
    if s.state == launch_pending:
        return UNDECIDABLE      # the flag alone cannot choose
```

The launch_pending case is the trap. The lab observed the identical
launch_pending, no-PID store state meaning two different realities: no
process exists (the worker died before exec) and a live unregistered child
exists (the worker died after exec, before registration). Neither attach nor
replace follows from the flag alone; launch_pending cannot choose the
policy.
(temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md)

Two safe resolutions were demonstrated:

- Trusted process discovery: bind registry rows to process reality with
  pidfd, process start identity, and boot id, then attach to the discovered
  child. This launched no competitor. A bare PID is insufficient; pidfds
  prevent a reused PID from retargeting a signal.
- Fenced conditional replacement: atomically supersede the old generation
  and proceed under a new one, so that even if the old child is alive its
  registration and writes are rejected (ErrStaleOwner before it can record
  progress or reach an effect barrier). This pairs the pattern with
  [Fenced Authority](./fenced-authority.md).
  (temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md)

Blind attach has its own failure: attaching to a phantom (a registration
with no live process) stalls the operation forever. The control run showed
attempt 2 attached to a PID-less phantom; the fenced arm recovered by
superseding generation 1 and completing under generation 2. A launch
decision recorded durably is not process liveness.
(temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md)

Replacement ordering: the incoming attempt must exceed the active executor's
attempt number, and progress, effects, and completion additionally require
running state plus a valid generation and token. A heartbeat may speed
detection and carry recovery hints, but it is not the creation fence; retry
lookup starts from the stable session key.
(temporallab2026:docs/architecture.md)

## Where enforcement occurs

- In the session registry within the store of record: lookup, launch_pending
  registration, and the attach decision are one transaction there.
- In the process-probe layer: pidfd, start identity, and boot id bind
  registry rows to real processes when launch_pending must be resolved.
- In the replacement path: destination-side generation fencing makes a wrong
  replace decision safe, so the two patterns are deployed together.
- Not in the workflow engine. Engine retries deliver attempts and detect
  missing heartbeats; they carry no knowledge of external process liveness.
  The lab's summary: procedure durability did not imply external-process
  liveness, and the engine detected missing heartbeats and retried
  procedure rather than detecting or adopting the child.
  (temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md;
  temporallab2026:docs/findings/0001-worker-death-surviving-agent.md)

## Does not guarantee

- Does not make the surviving agent's work correct or still wanted; it only
  prevents a competing executor.
- Does not resolve attach-versus-replace from the launch_pending flag; that
  requires a process-reality probe or a fenced replacement.
- Does not establish progress. A session that is alive and idle is
  indistinguishable from one that shipped when liveness is the only signal;
  Gas City's session code measures "productive" as elapsed time only.
  (gascity2026:docs/design/city-reliability-surface.md)
- Does not revoke a replaced executor's authority by itself; the fence does
  that ([Fenced Authority](./fenced-authority.md)).
- Does not cover executors on another host or behind another registry; the
  cited evidence is single-host.

## Failure drill

[../drills/worker-dies-agent-survives/](../drills/worker-dies-agent-survives/)

## Evidence

- Unsafe control after Worker SIGKILL: two launch decisions, two process
  identities, two accepted effects, one accepted outcome. Basis: local
  observation
  (temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
- Reattachment arm: attempt 2 received the generation-1 lease via atomic
  stable-session lookup and launched no child; the child kept its PID and
  start identity across the worker death. Basis: local observation
  (temporallab2026:experiments/worker-death/README.md;
  temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
- launch_pending with no PID observed to mean both no process and a live
  unregistered child; trusted-discovery attach launched no competitor, and
  fenced replacement rejected the old child's exact PID/start registration
  with stale_owner. Basis: local observation
  (temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md).
- Blind attach stalled on a phantom; fenced recovery completed in two
  preserved trials. Basis: local observation
  (temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md).
- Guarantee-ledger entry: no duplicate launch on retry requires atomic
  start-or-attach plus explicit lifecycle, observed across registered-child
  reattach, known-live unregistered-child reattach, and phantom replacement.
  Basis: local observation
  (temporallab2026:docs/guarantees.md).
- All nine authenticated unsafe direct-CLI trials launched two agent
  sessions and applied two physical effects while the engine accepted one
  outcome; direct relaunch is unsafe. Basis: local observation
  (temporallab2026:docs/guarantees.md).
- Duplicate singleton-pool sessions from alias collision recur on every
  restart and are drained each cycle as a stopgap (dr-l66ch). Basis: local
  observation (gascity2026:docs/design/city-reliability-surface.md).
- Sessions are external managed resources: dispatch, return the work id,
  then wait on a signal or reconcile; never block an engine activity on an
  interactive agent session. Basis: local observation
  (gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md).
- Orphan-detection and adoption of long-running children by a supervisor is
  a long-standing operating-systems and cluster-management concern; the
  registry-based resolution here is the standard remedy shape. Basis:
  foundational.

## Limits

- The registry decision is only as strong as its binding to process reality;
  pidfd and start-identity probes are Linux-specific, and the cited
  experiments are single-host. Cross-host session registries would need a
  different liveness protocol. Basis: inference.
- The pattern assumes the executor cooperates with registration (records its
  PID and start identity on exec). An executor that mutates before
  registering leaves a window that only destination-side fencing covers.
  Basis: local observation plus inference
  (temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md).
- Attach adopts the session's history along with its liveness: a wedged or
  poisoned session gets attached to just as readily as a healthy one, so
  attach needs a progress deadline behind it. Basis: inference, consistent
  with Gas City's elapsed-time-only productivity signal.

## Sources

- temporallab2026:experiments/worker-death/README.md
- temporallab2026:docs/findings/0001-worker-death-surviving-agent.md
- temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md
- temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md
- temporallab2026:docs/guarantees.md
- temporallab2026:docs/architecture.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md
