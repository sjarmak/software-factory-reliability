# Recipe: Background Maintenance

Recurring housekeeping: pruning merged branches, expiring quarantines,
compacting stores, reaping orphaned workspaces, re-deriving caches. This
recipe is mostly about restraint. The work is short, frequent, and
convergent, and the evidence says the correct machinery for it is a timer,
a lock, and a level-triggered query, with durable-execution engines
reserved for shapes this workload does not have.

## Workload

Each run lasts seconds to minutes, waits on no external event, and either
mutates nothing external or performs convergent mutations (deleting an
already-merged branch twice is one deletion). The adoption test from the
evidence base asks three questions of any candidate for a workflow engine:
whether the procedure is crash-exposed mid-flight, whether it waits on
external events, and whether its side effects are irreversible. Background
maintenance typically answers no to all three, and the evaluated verdict
for that case is cron (agent-era, book ch08; local observation,
gascity2026:docs/design/software-factory-philosophy.md).

The production evaluation that anchors this recipe measured its own
maintenance floor before deciding: 44 seconds of actual work per
120-minute window, overlap protection that fired zero times, no long-lived
state, no event wait. Its conclusion, verbatim: "Temporal reduces to cron
plus a lockfile" (local observation,
gascity2026:docs/design/temporal-decision.md). The same evaluation
found that every durable wakeup in the fleet reduces to a durable
timestamp in the store plus a periodic scan, with the existence proof
being rate-limit backoff, a `quarantined_until` field checked on every
reconciler tick, and that all three of the seemingly timer-shaped
candidates dissolved on inspection, because "the shape is a property of
the implementation, not of the problem" (local observation,
gascity2026:docs/design/city-reliability-surface.md).

The failure boundary that remains is silence. A maintenance loop that
stops running looks exactly like a maintenance loop with nothing to do:
one order sat dormant for 10 days, 2026-07-06 to 2026-07-16, unnoticed,
because a deliberate override disabled it and nothing watched for the
absence (local observation, gc-qo3,
gascity2026:docs/design/city-reliability-surface.md). A cover that
runs on the right cadence but checks the wrong metric is the same failure
wearing green: the resource sweep ran while the host died of memory
exhaustion on 2026-07-15, gated on a metric that stayed healthy
throughout (local observation,
gascity2026:docs/rca-host-death-2026-07-15-memory-exhaustion.md).

## Required patterns

- [reconciliation](../../patterns/reconciliation.md): the whole correctness
  path. Each run re-derives the work from the source of record (which
  branches are merged, which quarantines expired) and converges current
  state; it remembers nothing between runs. A reliability layer is a
  query, not a memory (local observation,
  gascity2026:docs/design/software-factory-philosophy.md).
- [promise-oriented-observability](../../patterns/promise-oriented-observability.md):
  watch outcome, not liveness. In the production fleet, 83 of 106 live
  checks never examined whether work moved, and a 30-second liveness loop
  reports green 120 times an hour while nothing is dispatched (local
  observation,
  gascity2026:docs/design/city-reliability-surface.md).
- [effect-identity](../../patterns/effect-identity.md) and
  [explicit-unknown-state](../../patterns/explicit-unknown-state.md): needed
  only where a sweep mutates a destination that does not converge
  naturally (filing a ticket, sending a notification). Convergent deletes
  get a `converge` retry contract with a readback; non-convergent effects
  get the full treatment or move out of this recipe.
- [stable-work-identity](../../patterns/stable-work-identity.md): minimal
  here, one identity per sweep kind for the lock and the run record, so
  two concurrent runs of one sweep are impossible by construction.

## Contract

A maintenance factory declares less, deliberately. The load-bearing
sections:

```yaml
authorities:
  procedure: { system: os-timer }    # a host timer and a lock; no engine

reconciliation:
  - fact: expired_quarantines
    query: select_sessions_past_quarantined_until
    interval: 5m
  - fact: stranded_branches
    query: merge_base_check_over_open_branches
    interval: 15m
  - fact: orphaned_workspaces
    query: workspaces_without_live_claim
    interval: 1h

effects:
  - name: delete_merged_branch
    destination: code_host
    effect_identity: branch-delete/{repository}/{branch}
    retry_contract: converge         # deleting twice is one deletion
    readback: branch_exists
    unknown_state_policy: reconcile_then_block

scheduling:
  classes:
    recovery: { maximum: 8 }         # a backlog of missed sweeps drains
                                     # inside a budget, not all at once

observability:
  promises:
    - ready_to_claim                 # a sweep that finds work must claim it;
                                     # silence here is the dormant-loop failure
```

## Recommended drills

- [event-is-lost](../../drills/event-is-lost/DRILL.md): the defining drill
  for this shape. The trigger event drops silently; proves the
  level-triggered scan carries correctness on its own, with events
  supplying only latency.
- [retry-storm](../../drills/retry-storm/DRILL.md): a host outage makes every
  sweep's backlog due simultaneously; proves recovery drains inside its
  budget instead of turning the outage into a second outage (local
  observation on the failure class,
  gascity2026:docs/design/retry-and-recovery-capacity.md).
- [effect-commits-ack-is-lost](../../drills/effect-commits-ack-is-lost/DRILL.md):
  run it only for sweeps with non-convergent external effects; a pass on
  the convergent path is expected and proves little (the readback makes
  redelivery harmless by construction).

One check no drill in this kit covers: the cover for the sweep itself.
Whatever notices that the sweep stopped running must live outside the
sweep's failure domain, or it moves the single point of failure instead
of removing it (local observation,
gascity2026:docs/design/software-factory-philosophy.md).

## Observability fields

The sweep's own record is `work.reconciled` with `action` naming the
repair and `reason` carrying the evidence
([semantic-conventions.md](../observability/semantic-conventions.md)).
The record must assert the state that was converged and cite what was
reread, never "the sweep ran": 1384 audit events once asserted worktree
creation that never happened (local observation,
gascity2026:docs/design/city-reliability-surface.md).

Two promise checks matter most. Ready-with-no-claim past its bound is the
dormant-loop alarm. And the scan's own query cost bounds its cadence: an
aggregate lookup that blew a 15-second deadline took a status path to
10.18 seconds on 2026-08-01, making the check itself a silent cover
(local observation,
gascity2026:docs/incidents/2026-08-01-status-path-latency-recurrence.md);
bounds discussion in
[promise-latencies.md](../observability/promise-latencies.md).

## What stays destination-specific

Guarantees this recipe cannot give you:

- Queryability of the pending state. Reconciliation works only when the
  fact to converge is written where a scan can see it; if the pending
  state lives in a process's memory, no cadence of scanning finds it, and
  making it queryable is implementation work this recipe assumes done
  (local observation,
  gascity2026:docs/design/city-reliability-surface.md).
- Lock scope. A per-host lock, a per-repository lock, and a fleet-wide
  lock give different concurrency and different blast radius on wedge;
  the choice depends on what the sweep touches.
- Deletion safety. Retention windows, soft-delete support, and whether a
  reaped artifact is recoverable are destination properties; the recipe
  guarantees convergence on the declared state, not that the declared
  state was wise.
- The independent cover's substrate. The recipe requires that the
  watchdog for the sweep share no failure domain with the sweep; whether
  that is a host timer, a second host, or an external scheduler is an
  infrastructure decision the evidence base does not settle beyond the
  independence requirement itself (local observation,
  gascity2026:docs/design/temporal-decision.md).
