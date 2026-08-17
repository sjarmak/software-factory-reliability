# Reconciliation

> **Problem** A lost event leaves the system out of sync forever, and
> nothing notices.
>
> **Rule** Events make it fast; reconciliation makes it true.
>
> **Required property** For every transition the system advances by event,
> a level-triggered query against authoritative state detects and repairs
> the same transition with no event delivered. Repairs are idempotent, and
> a failed lookup yields LOOKUP_FAILED rather than a negative result.
>
> **Wrong** `event arrives -> advance state; no event -> nothing happens`
>
> **Right** `scan authoritative state -> compare against intent -> repair idempotently`
>
> **See it fail**
>
> - `make drill DRILL=event-is-lost MODE=unsafe` exits 2
> - `make drill DRILL=event-is-lost MODE=protected` exits 0
>
> **Checked by** `RECON-001` in the [rule
> catalog](../docs/contract-reference.md)

## Problem

Event-driven wiring is edge-triggered: it fires when a transition is
announced and stays quiet otherwise. Announcement channels lose events at
every integration boundary. In Gas City the transport drops silently by
design: errors are written to stderr and never returned, cross-process flock
contention discards records after a 250ms timeout, and the exec provider is
fire-and-forget. A system whose correctness requires every event to arrive
will diverge from reality, and the divergence is silent, because the handler
that never fired leaves no trace of not having fired.

The complement failure is treating a failed lookup as a negative result. A
query that errors, times out, or runs against the wrong store did not
observe absence; it observed nothing. Concluding "not present" from it turns
an infrastructure fault into a state decision, usually a duplicating one.

## Observed failure

2026-07-14 supervisor wedge (~20:00 to 21:11 EDT): event consumption stalled
with a 247-deep `bead.updated` backlog while the active events.jsonl stood
at 198MB. Orders kept firing throughout on their level-triggered scans; the
event lane wedged and the scan lane carried the factory.
(gascity2026:docs/rca-supervisor-wedge-2026-07-14-eventflow.md)

A signal contract with no reconciler rotted invisibly: Temporal signal
bridges waited on bead metadata no producer ever populated. Across 446
complete observe ticks, zero beads carried the contract and zero workflows
entered a waiting phase; nothing failed loudly, and the dormant wiring was
deleted on 2026-08-03 after an audit.
(gascity2026:docs/design/temporal-decision.md;
gascity2026:docs/design/software-factory-philosophy.md)

Before the `work-landing-reaper` existed, closed-bead-but-never-merged
strands accumulated at roughly 12 branches per day. Every close event had
fired and been handled; the outcome was still wrong, because nothing
level-triggered asked whether the work had actually landed.
(gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md)

That reaper is disabled in the system this page draws on, and the way it was
disabled is the failure. On 2026-08-07 a capacity decision reduced the fleet to
three standing seats and disarmed 42 scheduled orders in a single commit,
described as work patrols. At least two of the 42 were not work patrols. One is
the level-triggered landing scan cited above. The other closes workflow roots
left in progress after their work item has already closed. Neither, **as
scheduled**, dispatches anything or mutates anything: both default to a
report-only mode, and the disabled order files invoke them without the flag that
turns mutation on. The reason for disarming a patrol under reduced capacity is
that it hands work to seats that are not there. That reason does not apply to a
scan that only reports on the record, and no one separated the two classes before
disabling them as a group.

The qualifier "as scheduled" is doing real work, and getting it wrong is easy.
Read as scripts, these two are not alike at all. The workflow-root scan's
mutating mode closes beads in the system's own store and is reversible by
reopening them. The landing scan's mutating mode rebases and fast-forward merges
real branches, sets bead status, and mails an escalation, all of which are
reachable only past its apply guard. Judging either one by its name, or by
grepping its source for dispatch verbs, gets the wrong answer: both source files
contain calls that look like dispatch, and in the scheduled configuration
neither can reach them. Classify the invocation the scheduler actually runs, not
the capability the file contains.

Ten days later the disarmed cleanup scan, run by hand in its default read-only
mode, reported 11 stranded workflow roots and 58 stranded finalize steps, with
one correctly skipped as still live. Thirty-six finalize markers stood open, the
oldest from 2026-07-22, every one with an update timestamp equal to its creation
timestamp. Six of the stranded roots wrapped a work item that had already
closed: the work was finished and the record still said in progress, which is
the exact condition that scan exists to repair. Nothing had failed. The scan had
been switched off as a side effect of a decision about something else, and no
signal marked its absence, because a scan that does not run emits nothing to
notice.
(gascity2026:bin/orphaned-molecule-reaper,
orders/orphaned-molecule-reaper.toml.disabled)

A loop can satisfy every rule on this page and still never converge, because
convergence also depends on the write at the end of it being accepted. The
completion reconciler in the authoring installation is level-triggered,
re-derives from the store each tick, holds nothing between ticks, and repairs
idempotently. From 2026-08-10 its repair write was refused, with the store
naming the remedy in the refusal: the work item was held by a live claim, and
the error text said to pass a force flag if that claim was abandoned. A commit
on 2026-08-12 titled "recover abandoned completion claims" added the flag, two
lines, exactly as instructed. Seventeen minutes before that commit the last
refusal of the old kind was logged. At the next scheduled tick the refusal
changed shape and has not stopped since. The command wrapper the reconciler
calls carries a fail-closed argument guard that aborts on any flag it does not
recognise rather than risk resolving an ambiguous identifier to the wrong
record. The guard is right to do that, and its table of recognised flags omits
this flag for the update subcommand while listing it for four sibling
subcommands, against a tool whose own help documents it. The store named the
flag, the caller passed the flag, and the caller's own wrapper rejected it as
unrecognised. Four days on, the count is 671 refusals across six work items,
every one still stuck, the fix that was supposed to free them having never once
executed, and no signal anywhere above a line in a log file.

The loop already held the evidence that separates a repair which has not
converged yet from one that never will. In the same file, 68 repairs succeeded:
63 on the first attempt, 5 on the second, and none in the file's history on any
later attempt. Items sitting at attempt 220 are not being retried, they are
being refused, and a loop that never compares an attempt count against its own
distribution of successes cannot tell those apart. At-least-once repair is safe
to run forever, which is precisely why permanent refusal hides inside it: from
outside, a loop failing every tick and a loop with nothing to do produce the
same silence. Only the guard's own honesty made this recoverable at all, since
it refused loudly into a log rather than dropping the write.
(gascity2026:bin/completion-reconciler,
gascity2026:internal/bdflags/bdflags.go)

Lookup failure treated as absence: gc-28jm's duplicate workflow roots were
minted after a dedup query returned empty because it ran against a different
store from the one holding the earlier root.
(gascity2026:docs/design/city-reliability-surface.md)

## Invariant

For every state transition the system advances by event, there exists a
level-triggered query against authoritative state that detects and repairs
the same transition with no event having been delivered. Repairs are
idempotent, so lost, duplicated, delayed, and reordered events all reduce to
one case: the next scan converges actual state with intended state. A lookup
that fails, times out, or cannot reach authoritative state yields
LOOKUP_FAILED, an explicit error distinct from a negative result, and no
repair is derived from it.

## Mechanism

```
edge lane  (latency):   producer -> transport -> handler       # fast, lossy
level lane (complete):  tick -> read source of record
                             -> compute desired vs actual
                             -> idempotent repair of the diff  # no memory
```

Rules that make the pair work:

- The scan re-derives from the source of record on every tick and remembers
  nothing between ticks. Gas City's formulation: "A reliability layer is a
  query, not a memory," because "anything that watches by remembering goes
  wrong silently." A watcher that caches "already handled" has recreated the
  lost-event problem inside itself.
- Repairs are idempotent, which is what makes at-least-once scanning safe to
  run forever; the destination side of that idempotence is the contract in
  [effect-identity](effect-identity.md).
- Events are demoted, never trusted. The Gas City dispatcher's patrol tick
  cancels pending event-driven fires because the patrol scans every
  reconciler state authoritatively, making the pending events redundant; raw
  closes that produced no event are caught on a re-poll within 5 seconds.
- The scan checks outcomes, not liveness. On the 2026-07-16 surface map, 83
  of 106 live orders never check whether work moved, and a 30-second
  liveness loop "reports green 120 times an hour while zero beads are
  dispatched." A reconciler over liveness signals reconciles nothing.
- Durable wakeups reduce to a durable timestamp plus a periodic scan. The
  existence proof is Gas City's rate-limit backoff: a `quarantined_until`
  field checked by `healExpiredTimers` on every reconciler tick, with no
  timer service, no marker, and no engine.
- LOOKUP_FAILED discipline: absence may be concluded only from an
  authoritative read that completed. Cache unavailability must surface as an
  error, and a scan that concludes absence from a failed lookup will
  "repair" by re-creating what already exists, which is how gc-28jm minted
  duplicate roots.

The in-tree reference pair: edge-triggered `nudge-on-route` for latency,
level-triggered `routed-bead-nudger` as its backstop; the escalation surface
pairs event delivery with a 15-minute scan for the lost-signal case.

## Where enforcement occurs

The reconciler runs against the authoritative store, on a cadence, from a
failure domain independent of the event pipeline it backstops. Gas City's
covers-die-too rule sets the placement: a cover sharing the failure domain
of the thing it guards has moved the single point of failure rather than
removed it, so whatever watches the order system must not be an order.

The two-layer split assigns delivery semantics to the right layer: the work
layer is at-most-once and fail-closed; the watchdog and reconciliation layer
is at-least-once, idempotent, and re-derived every tick. Confusing them is
recorded as "the known catastrophic mistake," because at-least-once applied
to external mutation duplicates effects, and at-most-once applied to the
backstop silently abandons completion. Hand-rolled orchestration defaults
into the second error: it "reliably guarantees at-most-once, and silently
abandons completion," a shape two independent loops (a Temporal maintenance
cycle and a bash poller) both exhibited on 2026-07-16.

## Does not guarantee

- Latency below the scan cadence. PR #3958 waited 60m22s from review to
  dispatch on a 15-minute poll; events exist to close that gap, and the scan
  does not.
- Safety of the repairs it triggers; without effect identity at the
  destination, an at-least-once repair lane amplifies duplicates.
- That the scan itself runs. The single fleet-wide order-firing check was
  weekly, undocumented, skipped itself, and skipped every event-triggered
  order; a check nobody runs is not a check. A scan is also switched off
  deliberately, by a decision aimed at a different class of job that swept it
  up by schedule or by name. Classify scheduled jobs by whether they create
  work or only repair records, and disable the two classes separately; a
  capacity posture is a reason to stop handing out work, not a reason to stop
  reconciling the record.
- Outcome truth beyond the queried predicates. A scan over status fields
  inherits their lies; outcome predicates need independent evidence
  ([verify-before-publish](verify-before-publish.md)).
- Resolution of interrupted external effects; a scan can discover an
  unresolved intent but the recovery discipline belongs to
  [explicit-unknown-state](explicit-unknown-state.md).
- Bounded backlog. Reconciliation converges state, given capacity; it does
  not create the capacity.
- Convergence at all, when the repair write is refused at its destination. A
  level-triggered loop retries a permanent refusal on exactly the cadence it
  retries a transient one, indefinitely, and the two are indistinguishable
  from outside. Distinguishing them needs no new instrument in the common
  case: a loop that records attempt counts already knows the distribution of
  attempt counts on which its repairs succeed, and an item far outside that
  distribution is a refusal wearing a retry's clothes.

## Failure drill

[event-is-lost](../drills/event-is-lost/): suppress a transition event and
confirm the reconciler detects and repairs the transition within one scan
cadence. Then inject the same event duplicated, delayed, and reordered, and
confirm repairs converge rather than multiply. A further variant fails the
authoritative lookup and requires the scan to surface LOOKUP_FAILED instead
of deriving an absence repair.

## Evidence

- Level-triggered reconciliation against authoritative state as the
  correctness path, with events as a latency optimization. Foundational
  (edge-versus-level control loops in established distributed-systems
  practice), stated operationally as "Signals advance, queries repair" in
  gascity2026:docs/design/software-factory-philosophy.md.
- Event lane wedged, scan lane survived: 247-deep backlog, 198MB active
  event log, orders still firing. Local observation
  (gascity2026:docs/rca-supervisor-wedge-2026-07-14-eventflow.md).
- The transport drops silently by design, and the "critical" tier flag is
  discarded at the transport; actual critical delivery is achieved by a
  caller-side state scan at startup, meaning "the durability label is on the
  wrong layer." Local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- A signal contract with no reconciler rots invisibly: 446 observe ticks,
  zero contract carriers, wiring deleted 2026-08-03. Local observation
  (gascity2026:docs/design/temporal-decision.md).
- Roughly 12 branches per day stranded closed-but-never-merged before a
  level-triggered reaper existed. Local observation
  (gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md).
- A capacity decision disarmed 42 scheduled orders as work patrols, two of
  which only repaired records and dispatched nothing; ten days on, the
  disarmed cleanup scan reported 11 stranded workflow roots and 58 stranded
  finalize steps, 36 finalize markers open with the oldest dated 2026-07-22,
  and 6 roots whose work item had already closed. Local observation
  (gascity2026:bin/orphaned-molecule-reaper,
  orders/orphaned-molecule-reaper.toml.disabled). This page cites the landing
  reaper's benefit while that reaper is disarmed in the authoring
  installation; the claim about its value stands on the pre-disarm
  measurement, not on current practice.
- A reconciler correct by every rule on this page that has not converged
  since 2026-08-13: 671 refusals across six work items, the refusal introduced
  by the two-line commit intended to clear the previous refusal, measured
  against a success distribution of 63 first-attempt and 5 second-attempt
  repairs and none later anywhere in the log. Unfixed in the authoring
  installation at the time of writing; the fix is a table row in a wrapper
  owned by another repository. Local observation
  (gascity2026:bin/completion-reconciler,
  gascity2026:internal/bdflags/bdflags.go).
- Memory-holding watcher pathology in one component: 60m22s
  review-to-dispatch latency on a 15-minute poll, roughly 72 API calls per
  hour at steady state to learn nothing, 174 cache files for 8 open PRs with
  166 tombstones, and a per-review counting bug that dispatched a spurious
  iterate, proven by fault injection. Local observation
  (gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md).
- "Events give latency; reconciliation gives completeness. Events get lost
  at integration boundaries, so the scan must survive. What dies is the
  *memory*, not the scan." Local observation (same walkthrough).
- Problems that look timer-shaped dissolve into scans: all three v1
  timer-shaped candidates became server config, in-process cache expiry, and
  a query once the pending state was written where a scan could see it.
  Local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- The fire-and-forget family (`pr-state-poller`, `nudge-on-route`,
  `help-request-surface`, `pl-human-gate-surface`) stamps "I acted" rather
  than "the thing I triggered finished" and never re-checks; the in-tree
  repair is a level-triggered backstop, not a durable timer. Local
  observation (same surface map).

## Limits

- Scan capacity is a real budget. Sixty-three enabled cooldown orders
  demanded 11.07 launches per minute against a patrol guaranteeing 2; the
  repaired scheduler derives cadence from configured demand plus 25 percent
  headroom with an absolute per-tick ceiling, so pathological demand stays
  bounded instead of multiplying processes. Local observation
  (gascity2026:docs/recovery/scheduler-capacity-review-9ad10d428.md).
- Reconciliation load lands on the authoritative store. The 2026-08-01
  status-path recurrence blew a 15-second doctor deadline on an aggregate
  order-history lookup while hooks hung past bounds; scans compete with the
  work they exist to repair. Local observation
  (gascity2026:docs/incidents/2026-08-01-status-path-latency-recurrence.md).
- The authoritative state must exist where a scan can see it. State held in
  a process's memory is invisible to any reconciler; the shape of a problem
  is a property of the implementation, and moving pending state into the
  store is what makes it query-shaped.
- Query correctness bounds repair correctness. A wrong store, a wrong
  predicate, or a miscounted population produces confident wrong repairs, as
  in gc-28jm and the dr-l90r soak checker. The reconciler inherits every
  weakness of its query.
- Cadence selection trades staleness against load, and the cited systems
  give working points rather than a formula. Inference: cadence should be
  derived from measured demand with explicit headroom, as the scheduler
  repair did, rather than fixed by convention.

## Sources

- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/temporal-decision.md
- gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md
- gascity2026:docs/rca-supervisor-wedge-2026-07-14-eventflow.md
- gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md
- gascity2026:docs/recovery/scheduler-capacity-review-9ad10d428.md
- gascity2026:docs/incidents/2026-08-01-status-path-latency-recurrence.md
- gascity2026:bin/orphaned-molecule-reaper,
  gascity2026:orders/orphaned-molecule-reaper.toml.disabled
- gascity2026:bin/completion-reconciler,
  gascity2026:internal/bdflags/bdflags.go
- Related patterns: [effect-identity](effect-identity.md),
  [explicit-unknown-state](explicit-unknown-state.md),
  [verify-before-publish](verify-before-publish.md)
