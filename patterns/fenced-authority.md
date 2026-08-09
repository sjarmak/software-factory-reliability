# Fenced Authority

## Problem

Two executors can simultaneously believe they own the same work. A retry
starts while the original attempt still runs; an orchestrator dies and its
agent process survives; a canceled workflow's detached child keeps going. A
lease names the intended owner, and that name is necessary for routing and
diagnosis, but a name on a lease record does not stop a process that already
holds open connections, a warm workspace, and valid credentials. The design
question is where authority is actually removed from the stale writer.

The tempting answers all fail the same way. Checking the lease before writing
(caller-side) leaves a window between the check and the write during which
ownership can change; the write then lands with no check at all. Killing the
old process is best-effort: signals miss detached children, PIDs get reused,
and a frozen process resumes later with its authority intact. Canceling the
orchestration record changes what the orchestrator does; it changes nothing
about what the already-running executor can do.

## Observed failure

In the temporal_projects cancellation experiment, all six engine-only control
runs canceled the Workflow, the Workflow closed as canceled, and the
still-live detached agent afterwards committed one effect and one accepted
outcome anyway. Cancellation of the procedure did not revoke the authority of
the process the procedure had launched.
(temporallab2026:docs/findings/0006-cancellation-requires-application-revocation.md)

The ABA probe shows the same hole through ownership churn rather than
cancellation: after owner A under generation 7 was replaced and A later
became current again under generation 9, both unsafe systems accepted four
obsolete A/g7 actions, because the owner label matched even though the
generation did not. Both fenced systems accepted zero. The ledger's summary:
the fence, not the owner label or the durability substrate, supplies safety.
(temporallab2026:docs/guarantees.md)

Gas City hit the caller-side variant in production tooling on 2026-07-28:
with a Dolt server capped at 32 connections, 12 fresh claims yielded only 10
distinct winning beads; two beads were each reported freshly claimed by two
distinct actors via cross-session working-set overwrite. This persisted under
strict concurrency one and a city-wide file lock, because the lock lived with
the callers and the store applied writes without validating them.
(gascity2026:docs/recovery/connection-cure-review-02669a98f.md)

## Invariant

For one logical operation, at most one generation is current at any moment,
and no destination applies a write presented under a non-current generation.
Validation of the generation and application of the write are a single atomic
step at the destination.

## Mechanism

The lease record names the intended owner and carries the fencing state:

```
lease := { work_id, generation, owner, capability_hash }
```

Ownership transfer atomically increments the generation and replaces the
active capability hash; only the hash of the opaque capability is persisted.
Every writer presents (work_id, generation, capability) with every mutation,
and the destination compares them inside the same transaction that applies
the write.

The caller-side check leaves the time-of-check-to-time-of-use race open:

```
stale writer                          destination
------------                          -----------
read lease: "I am current (gen 1)"
                                      new owner fenced in: gen 1 -> 2
write effect  ----------------------> applied, no check at apply time   WRONG
```

The destination-side fence closes it, because the check and the apply cannot
be separated by a concurrent transfer:

```
write(work_id, gen=1, cap) ---------> BEGIN
                                        current generation == 1 ?  no (2)
                                      ROLLBACK -> ErrStaleOwner
```

Replacement is ordered by monotonic attempt: an incoming attempt may replace
the active executor only if its attempt number exceeds the active one, and
progress, effects, and first completion additionally require running state
plus a valid generation and capability.
(temporallab2026:docs/architecture.md)

Cancellation under this pattern is an application-owned terminal transition:
one work-store transaction revokes the active generation, rejects later
registration, progress, effects, and completion, and blocks replacement.
Stopping the process is a separate best-effort step against the exact
session, generation, owner hash, PID, start identity, and process group.
The kill is an efficiency measure that reclaims resources sooner; the fence
is what makes a failed or late kill safe.

Two fences must not be conflated. An engine's server-side attempt validation
in the task token protects engine completions of an obsolete task; it does
not protect application mutations by a child that holds no task token. The
application fence at the destination covers the second class.
(temporallab2026:docs/findings/0001-worker-death-surviving-agent.md)

## Where enforcement occurs

- At every destination that applies writes for the operation (work store,
  workspace registry, artifact store, message broker): an atomic
  generation-and-capability compare inside the applying transaction, or
  explicit delegation of that compare to a store that can perform it.
- At the lease store: atomic generation increment plus capability
  replacement, so there is never an interval with two current generations.
- At the terminal-transition path: revocation, rejection of future writes,
  and blocking of replacement commit in one transaction; first durable
  terminal transition wins.
- Nowhere on the caller. Caller-side checks are latency optimizations that
  avoid wasted work; they enforce nothing.

## Does not guarantee

- Does not stop the stale process from running, consuming quota, or holding
  resources; it only makes that process's writes inert.
- Does not deduplicate effects at destinations that ignore the fence, such
  as an external API with no validation hook; those need effect-level dedup
  and reconciliation ([Durable Intent](./durable-intent.md)).
- Does not detect that a stale writer exists; discovery belongs to a
  level-triggered scan, not to the fence.
- Does not revoke arbitrary copied credentials used outside fenced write
  paths; the lab marks that class explicitly untested.
- Does not decide whether to attach to or replace an existing executor;
  that is the start-or-attach decision
  ([Start-or-Attach](./start-or-attach.md)).

## Failure drill

[../drills/stale-writer-completes/](../drills/stale-writer-completes/)

## Evidence

- Fenced arm of the worker-death experiment: the generation-2 outcome was
  accepted at event 15; the delayed generation-1 effect was rejected at
  event 16 and its completion at event 18. Basis: local observation
  (temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
- All six engine-only cancellation controls mutated after Workflow
  cancellation; all 18 application-revoked runs accepted no mutation.
  Basis: local observation
  (temporallab2026:docs/findings/0006-cancellation-requires-application-revocation.md).
- ABA probe over 30 publication pairs per system: unsafe systems accepted
  four obsolete A/g7 actions after A/g9 became current; fenced systems
  accepted zero. Basis: local observation
  (temporallab2026:docs/guarantees.md).
- Stale asynchronous completion by task token was rejected by the server in
  three live trials, while by-logical-ID completion accepted a stale result
  in three unsafe trials and was rejected only by the application fence.
  Basis: local observation
  (temporallab2026:docs/guarantees.md).
- Three unsafe attached sandbox references wrote after replacement; all
  three fenced arms rejected the stale write, and workflow routing identity
  is not a revocable owner capability. Basis: local observation
  (temporallab2026:docs/guarantees.md).
- The work ledger carries claims, generations, and fencing tokens, and the
  adopted execution model uses a fenced Activity lease with
  generation-checked writes back to the ledger. Basis: local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- 2026-07-28: two beads each claimed by two distinct actors through
  cross-session working-set overwrite, despite a city-wide file lock; the
  ratified repair fails closed on controller loss instead of falling back to
  unfenced writes. Basis: local observation
  (gascity2026:docs/recovery/connection-cure-review-02669a98f.md).
- Claims carry no lease TTL; a per-tick scan re-derives whether a claim's
  assignee maps to a live session and reopens the bead, emitting the event
  purely as a record of an already-completed repair. Basis: local
  observation (gascity2026:docs/design/city-reliability-surface.md).
- Fencing tokens issued with leases and validated by the resource are the
  established remedy for stale lease holders in distributed systems. Basis:
  foundational.

## Limits

- Every destination must enforce or delegate the fence; one unfenced
  destination reopens the hole. Gas City's own audit lists the merge step of
  its merge molecule as an unguarded external write as of 2026-08-02, which
  is the adopt-candidate lane for exactly this reason
  (gascity2026:docs/design/temporal-decision.md).
- Process-group signaling is not a kernel-atomic identity fence; hostile or
  multi-tenant containment may require cgroup-level isolation
  (temporallab2026:docs/findings/0006-cancellation-requires-application-revocation.md).
- Cross-host executors, uncooperative processes, and copied credentials are
  untested in the cited experiments.
- A destination that offers only blind writes (no transactional hook) can
  host effect deduplication but cannot host an authority fence; for such
  destinations the fence degrades to dedup plus reconciliation. Basis:
  inference.

## Sources

- temporallab2026:docs/findings/0001-worker-death-surviving-agent.md
- temporallab2026:docs/findings/0006-cancellation-requires-application-revocation.md
- temporallab2026:docs/guarantees.md
- temporallab2026:docs/architecture.md
- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/temporal-decision.md
- gascity2026:docs/recovery/connection-cure-review-02669a98f.md
