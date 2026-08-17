# Recipe: Recovering a Factory That Is Already Broken

The other recipes in this kit assume you can describe your factory. This one
is for the case where you cannot. The topology drifted, the store disagrees
with the world, work items have duplicated or disappeared, and the machinery
added to prevent exactly that state is now the reason it is hard to touch.
A contract file cannot be the first step, because writing one requires
knowing your topology, effects, and identities, which is the knowledge that
was lost.

What this recipe produces is not a safe factory. It produces a factory you
can describe accurately enough that `factory-check review`, the pattern
pages, and the other four recipes become usable, plus an inventory of what
is duplicated, orphaned, and unresolved. Hardening comes after, through the
adoption sequence in
[the essay](https://www.sjarmak.ai/writing/software-factories-are-distributed-systems).

The premise of the recipe is reasoning rather than measurement: each added
role, patrol, or scheduled loop is another writer, and repair cost grows
with the number of interacting writers rather than with the number of
components, because the interactions are what nobody enumerated (inference).

## The wreck

Three properties define this workload, and each one is visible in the
evidence base as an ordinary production condition rather than a catastrophe.

The record disagrees with the world in both directions. Gas City's gc-na2o
wrote metadata to the wrong rig and produced about 40 orphan worktrees
(~2.8GB) alongside 1384 `worktree-recorded` audit events asserting creation
that never happened (local observation,
gascity2026:docs/design/city-reliability-surface.md). One defect generated
both classes at once: state the record does not know about, and records
whose subject does not exist.

Fan-out work strands and stays stranded. The dr-61j inventory of 2026-07-15
counted 37 open or in-progress workflow-marker beads for multi-step work
that had fanned out and never joined, with the oldest strand 87 days old
(gc-1920/gc-1927, from a 2026-04-18 run). Six of those molecules were live
respawn-loop fuel, and the safe close path required closing whole molecules
at once so no orphan step could re-route (local observation,
gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md).

Nothing reports the state as broken. On the same fleet, 83 of 106 live
checks never examined whether work moved, and a 30-second liveness loop
reports green 120 times an hour while zero items are dispatched (local
observation, gascity2026:docs/design/city-reliability-surface.md). A wrecked
factory and a quiet one produce the same dashboard.

## The order, and why it is that order

Stop producing effects, then establish what is true, then recover identity,
then re-establish contracts. The ordering principle is the one the essay
states for hardening: early mistakes corrupt state, later ones waste work
([the essay](https://www.sjarmak.ai/writing/software-factories-are-distributed-systems),
adoption sequence).

One edge in this order never reverses, and it is the reason the sequence
exists at all. Every repair action is itself an external effect. Closing a
strand, deleting an orphan, re-dispatching lost work, and rewriting a
metadata field are all mutations, and this kit's entire effect discipline
applies to them. A repair carries an effect identity or it duplicates
([effect-identity](../../patterns/effect-identity.md)), and an effect identity
is derived from the logical work item, which is the thing a wrecked factory
has lost. You cannot dedupe work you cannot name, so identity precedes
repair, and measurement precedes identity because identity assignment is
itself a repair.

### 1. Stop producing effects

External mutations are the worst loss class: a duplicate merge, push, or
publication is worse than a skipped cycle (local observation,
gascity2026:docs/design/software-factory-philosophy.md). During recovery the
factory's output is unreviewed by construction, so the first move is to
remove its ability to change the outside world.

Stopping is not killing. A coding agent is a separate operating-system
process that outlives the worker that launched it (local observation,
temporallab2026:docs/architecture.md), so killing the orchestrator converts
supervised writers into unsupervised ones. Restarting is worse: Gas City's
duplicate singleton-pool sessions from alias collision reappear on every
restart and are drained by a supervising cycle as a stopgap (local
observation, gascity2026:docs/design/city-reliability-surface.md). The
effective stop is at the capability boundary. Revoke the credentials that
reach each destination, take the publisher offline, or advance the ownership
generation so surviving writers are fenced at the destination when they
return ([fenced-authority](../../patterns/fenced-authority.md)).

Stop the effect-producing loops, keep the read-only ones. The scan lane is
what carries a recovery, and it is the lane that survives: during the
2026-07-14 supervisor wedge, event consumption stalled with a 247-deep
backlog against a 198MB active event log while level-triggered orders kept
firing throughout (local observation,
gascity2026:docs/rca-supervisor-wedge-2026-07-14-eventflow.md). A blanket
disable removes the instrument along with the hazard.

Partial stops of fan-out work re-route. The dr-61j finding is the direct
warning: orphan steps became respawn-loop fuel, and the safe path was to
close whole molecules rather than individual steps. Where an effect is
mid-sequence and aborting would leave a half-applied change, record the
intent and escalate rather than abort
([durable-intent](../../patterns/durable-intent.md),
[explicit-unknown-state](../../patterns/explicit-unknown-state.md)).

### 2. Establish what is true

Four inventories, each enumerated from ground truth rather than from
configuration or from the factory's own records.

- **Effect surface.** Every path by which this factory can change something
  outside itself, derived from the credentials and network reachability that
  exist, not from the effects the config declares. An effect you cannot name
  is one you did not stop in step 1.
- **Live executors.** Processes and sessions that exist right now, joined
  back to claims. The join direction matters: start from the processes and
  look for their claim, because the registry is the artifact under suspicion.
- **Durable work records.** Every store that holds work items, including the
  ones that hold fragments. A factory whose work records span several stores
  reintroduces the duplicate-identity class unless exactly one store is the
  mint (local observation plus inference,
  [stable-work-identity](../../patterns/stable-work-identity.md)).
- **Scheduled loops.** What runs on a cadence, what each one writes, and
  whether it fired. Silence is the failure mode here: one order sat dormant
  for 10 days, 2026-07-06 to 2026-07-16, because a deliberate override
  disabled it and nothing watched for the absence (local observation, gc-qo3,
  gascity2026:docs/design/city-reliability-surface.md).

### 3. Recover identity

Diff each inventory against the records in both directions and classify what
falls out.

```
world without record   ->  ORPHAN     (the ~40 untracked worktrees)
record without world   ->  PHANTOM    (the 1384 false audit events)
two records, one world ->  DUPLICATE  (gc-28jm's two roots, one target)
record, world unknown  ->  UNRESOLVED (the poisoned pending claim)
```

Duplicates are the class that decides the rest. gc-28jm minted duplicate
workflow roots for single targets, 33 seconds apart for one and 26 seconds
apart for another, because the dedup query ran against a different store
from the one holding the earlier root (local observation,
gascity2026:docs/design/city-reliability-surface.md). Identity existed; the
uniqueness check did not run where it mattered. In a wrecked factory both
halves are usually gone, so assign one logical identity per work item across
the fragments before merging anything, and merge at the store designated as
the mint.

UNRESOLVED is a real column, not a staging area for optimism. Recovery
models three outcome states, and mapping UNKNOWN to either known state is a
defect in both directions: assumed failure repeats the effect, assumed
success fabricates history
([explicit-unknown-state](../../patterns/explicit-unknown-state.md)). Expect
this column to be large and to stay large. After the gc-4zf.4 fix, the
originally poisoned record remained pending permanently, and 53 session-model
findings were still open and unreconciled on 2026-08-01, where the warning's
disappearance was recorded as "a visibility change, not evidence of repaired
state" (local observation,
gascity2026:docs/recovery/session-model-53-reconciliation-2026-08-01.md).

### 4. Re-establish contracts

Only now is a contract file a description rather than a guess. The section
below says what the smallest honest one contains.

### Pressure-testing the order

Steps 2 and 3 interleave in practice, because every identity decision
changes what the next measurement should ask, and the inventory has to be
re-read after each merge. Treat them as one loop with a direction rather
than two phases.

Step 1 has a real cost, and it is worth naming: stopping the effect surface
strands in-flight work, and some of that work will land in the UNRESOLVED
column that step 3 then has to carry. The trade is deliberate. Unresolved
work is recoverable by inventory; a duplicated external effect is a
published fact with downstream consumers.

The order that gets attempted instead is contracts first, on the theory that
writing the contract will reveal what is broken. The hazard is the direction
of the error. A contract asserted from belief is a stale map, and stale
context fails confidently: in the stale-retrieval experiment, stale-only
retrieval produced outputs incompatible with the current interface in 15 of
17 cases for one model and 13 of 17 for another, while current-only
retrieval produced zero, and without retrieval the models tended to fail
visibly instead (agent-era, Weng et al. 2026 via book ch12). A contract that
is merely absent produces explicit not-declared findings; a contract that is
confidently wrong passes review and licenses repairs against a topology that
does not exist.

## Stop doing these first

Five actions that make a wrecked factory worse. Each is the ordinary,
well-intentioned response.

- **Adding automation on top.** New patrols and reapers are new writers
  against state you have not measured, and they compete for the capacity the
  recovery needs. Sixty-three enabled cooldown orders once demanded 11.07
  launches per minute against a patrol guaranteeing 2; the repair was to
  derive cadence from measured demand plus 25 percent headroom under an
  absolute per-tick ceiling, not to add another loop (local observation,
  gascity2026:docs/recovery/scheduler-capacity-review-9ad10d428.md).
- **Re-running reconcilers over wrong input.** A reconciler is an
  at-least-once repair lane, and without effect identity at the destination
  it amplifies duplicates rather than converging
  ([reconciliation](../../patterns/reconciliation.md)). Absence may be
  concluded only from an authoritative read that completed; a lookup that
  errors, times out, or hits the wrong store is LOOKUP_FAILED, and gc-28jm
  is what deriving a repair from one produces.
- **Trusting liveness.** The 83-of-106 figure above is the steady-state
  version. The acute version: on 2026-07-27 a kernel flush-workqueue storm
  put all 16 CPUs at 95 to 100 percent system time, and supervisor HTTP,
  pprof, mail, tmux, and the database port all timed out while the service
  manager reported every unit alive (local observation,
  gascity2026:docs/recovery/demand-driven-city-recovery-2026-07-27.md).
  During recovery, read outcomes.
- **Repairing a store with writers attached.** On 2026-07-28, 12 fresh
  claims yielded only 10 distinct winning items, with two items each
  reported freshly claimed by two distinct actors through cross-session
  working-set overwrite, and the condition persisted under strict
  concurrency one and a city-wide file lock (local observation,
  gascity2026:docs/recovery/connection-cure-review-02669a98f.md). Detaching
  writers is step 1, and a lock is not evidence that it happened.
- **Reading a symptom's disappearance as a repair.** The 53 unreconciled
  findings above disappeared from a warning surface without changing state.
  A repair is confirmed by re-measuring the thing that was wrong, at the
  place it was wrong.

## Measure before you repair

The kit's rule for prose is that every claim carries its evidence basis
(`docs/style.md`). The operational form during a recovery pass: every claim about
current state carries the command that would refute it, and the command is
re-run before acting on the claim rather than on a schedule (inference,
extending the kit's evidence-basis discipline to operations).

A count that returns zero has two explanations, and they are opposite. The
drill discipline names the general form: without an unsafe negative control,
a passing test means either the mechanism worked or the test never exercised
the failure ([the essay](https://www.sjarmak.ai/writing/software-factories-are-distributed-systems),
recovery-is-a-measurement). Both explanations appear in the evidence base
for the same shape of zero. gc-28jm's dedup query returned empty because it
was pointed at the wrong store, and duplicates were minted on the strength
of that empty result. The Temporal signal bridges returned zero for a true
reason: across 446 complete observe ticks, zero work items carried the
contract and zero workflows entered a waiting phase, because the wiring was
dormant (local observation,
gascity2026:docs/design/temporal-decision.md). One zero meant a broken
instrument, the other meant a dead subject, and nothing in the output
distinguished them.

So give every instrument a positive control before its answer is load
bearing. Run it against a case you have constructed to be non-empty; an
instrument that cannot return non-zero on a known-present case has not
measured absence. This applies hardest to the queries you write during the
pass, because they are new code written under incident pressure against
schemas you are still learning.

Two more rules that the failure record supports directly. Enumerate from
ground truth, never from the record under suspicion, since gc-na2o's audit
log asserted 1384 creations that never happened. And diff in both
directions, since the same defect produced phantoms and orphans
simultaneously; a one-directional check finds one class and reports clean.

## Required patterns

- [explicit-unknown-state](../../patterns/explicit-unknown-state.md): the state
  model the whole pass runs on. A wrecked factory is mostly third column,
  and the inventory has to be able to represent that; a schema holding only
  success and failure will be lied to by every recovery. The pattern's
  escalation requirement is what keeps the pass from failing closed into
  silence.
- [reconciliation](../../patterns/reconciliation.md): the surviving lane and
  the method. Rebuilding the picture is a level-triggered query against
  authoritative state, re-derived rather than remembered, and the pattern's
  LOOKUP_FAILED discipline is the rule that stops the pass from inventing
  repairs out of failed reads.
- [stable-work-identity](../../patterns/stable-work-identity.md): step 3 is
  this pattern applied retroactively. It supplies the identity stack to sort
  the fragments into (work, generation, attempt, session, effect) and the
  rule that observational identifiers are diagnostics, never join keys.
- [effect-identity](../../patterns/effect-identity.md): required because the
  repairs are effects. Every close, delete, re-dispatch, and metadata
  rewrite needs a stable identity carried across retries, or a retried
  recovery script duplicates what it was cleaning up.
- [topology-aware-scheduling](../../patterns/topology-aware-scheduling.md): the
  freshness half. A topology answer carries the revision it was computed
  against, and an answer computed against a different state is treated as no
  answer. A recovery inventory is exactly such an answer, and it goes stale
  the moment writers reattach.

Three more back them up.
[fenced-authority](../../patterns/fenced-authority.md) is how a writer you
cannot kill gets stopped, by advancing the generation and rejecting the old
one at the destination. [start-or-attach](../../patterns/start-or-attach.md)
governs what to do with a surviving executor found in step 2: adopt it under
the current claim rather than launch a replacement beside it.
[promise-oriented-observability](../../patterns/promise-oriented-observability.md)
supplies the restart criterion, because the question after a pass is whether
work moves, which liveness cannot answer.

## The minimum viable contract

The smallest honest contract declares one section completely and records
everything else as undecided. The complete section is `effects`, because
that is the inventory step 1 depended on and the one whose gaps are
unbounded in cost.

```yaml
version: factory.reliability/v1

factory:
  name: the-factory-you-have          # the one running, not the one intended

# Complete, or the pass is not finished. One entry per path by which this
# factory can change something outside itself, including the paths found in
# credentials rather than in configuration.
effects:
  - name: open_change_request
    destination: code_host
    effect_identity: unknown          # EFFECT-001: nothing dedups today
    retry_contract: unknown           # EFFECT-002: destination untested
    unknown_state_policy: manual_review   # the honest value during a pass
  - name: comment_on_issue
    destination: code_host
    effect_identity: unknown
    retry_contract: unknown
    unknown_state_policy: manual_review

# Undecided until measured. Each FAIL this produces is a worklist item.
work:
  logical_identity: unknown           # IDENT-001 until one store mints it
  attempt_identity: unknown
  session_identity: unknown

# The one loop worth declaring during a pass: what is actually running.
reconciliation:
  - fact: live_executor_sessions
    query: enumerate_processes_and_join_to_claims
    interval: 15m
    destination: code_host
```

That document is schema-valid (`factory-check validate`) and fails review
loudly, which is the intended result. The schema accepts `unknown` wherever
a builder has not decided, so an undecided contract can still be recorded
and reviewed, and the review rules flag the undecided values instead of the
schema rejecting the file (`schemas/factory.schema.json`, contract
description). Review the file above and it names IDENT-001, AUTH-001,
AUTH-002, EFFECT-001, EFFECT-002 and VERIFY-002 as failures, with AUTH-000,
VERIFY-003, FLEET-001 through FLEET-003 and OBS-001 as warnings. The totals
are deliberately not quoted here: `QUICKSTART.md` records what happens to a
count in prose that nothing compares against the catalog, which drifted
between `4744374` and `03e36b8` with no mechanism able to notice.

The rule that keeps the file honest: write `unknown` for every field you
cannot name the command for, and never write a decided-looking value from
belief. Three different unknowns meet in a recovery pass and only the first
belongs in the contract as a value.

1. Contract `unknown`: the builder has not decided. Schema-accepted,
   review-flagged, and the correct entry for anything the pass has not
   measured.
2. Runtime UNKNOWN: an effect whose outcome cannot be determined. This is
   `unknown_state_policy` territory, and the allowed policies all stop and
   surface the ambiguity
   ([explicit-unknown-state](../../patterns/explicit-unknown-state.md)).
3. Inventory unresolved: a record whose subject you have not yet looked for.
   This one belongs in the inventory, not the contract, and it is the column
   that shrinks as the pass proceeds.

The review output is the bridge back to the rest of the kit. Order the
findings by the adoption sequence in
[the essay](https://www.sjarmak.ai/writing/software-factories-are-distributed-systems): stable
identity and resolve-before-create first, then the generation fence at the
destination, then effect identity with intent recorded before dispatch, then
verification bound to an immutable digest, then one reconciliation loop over
the most consequential divergence, then capacity classes, then the promise
chain. Steps one through four are safety; five through seven are how you
learn whether the safety holds. From there the shape-specific recipes apply:
[issue-to-pull-request](issue-to-pull-request.md),
[background-maintenance](background-maintenance.md),
[cross-repository-migration](cross-repository-migration.md), and
[human-approved-production-effect](human-approved-production-effect.md).

## Drills come after the pass

Drills inject faults, and a factory in recovery has a surplus. Run them
against the repaired factory to check the repair, not against the wreck to
characterize it.

- [event-is-lost](../../drills/event-is-lost/DRILL.md): first, because it tests
  the lane the recovery depended on. If the level-triggered scan does not
  carry correctness on its own, the picture built in step 2 decays as soon
  as writers reattach.
- [worker-dies-agent-survives](../../drills/worker-dies-agent-survives/DRILL.md):
  proves the executor inventory is maintainable, and that a retry attaches
  to a surviving session rather than starting a second one beside it.
- [effect-commits-ack-is-lost](../../drills/effect-commits-ack-is-lost/DRILL.md):
  proves the UNRESOLVED column now resolves through readback or escalation
  instead of accumulating.
- [stale-writer-completes](../../drills/stale-writer-completes/DRILL.md): the
  direct test of the step-1 stop. A writer that survived the pass and
  returns must be rejected at the destination, not merely warned about.

## When rebuilding beats repairing

Rebuild is a legitimate outcome, and the evidence base contains a
measurement-backed instance of it: the Temporal signal bridges were audited,
found to carry zero traffic across 446 observe ticks, and deleted on
2026-08-03 rather than repaired (local observation,
gascity2026:docs/design/temporal-decision.md). The deletion was justified by
a measurement, which is the pattern worth copying regardless of which way
the decision goes.

Three questions decide it, and only the first has a hard answer (the rest
are inference).

1. **Is the work record re-derivable from a source outside the factory?**
   If the issues, branches, and pull requests at the code host are the
   original and the factory's store is a cache, a rebuild costs the cache.
   If the factory's store is the only place the work exists, a rebuild
   destroys it, and the export becomes mandatory before anything else.
2. **Can you enumerate the effect surface?** A rebuild that stands a new
   factory beside an old one that still holds credentials gives you two
   factories addressing one destination under two different identities,
   which is the duplicate-effect shape at organizational scale
   ([effect-identity](../../patterns/effect-identity.md)). If step 1 could not
   be completed, a rebuild is more dangerous than a repair, not less.
3. **Is the wreck in the structure or in the state?** Structural wreckage
   (roles, patrols, and loops wired to each other in ways nobody has
   enumerated) is cheap to rebuild and expensive to repair, because a repair
   has to preserve interactions that were never written down. State
   divergence is the opposite: a rebuild inherits the diverged state on its
   first tick, so the identity work has to happen either way.

Which is why a rebuild does not exempt anyone from steps 1 through 3. The
old factory's surviving executors, credentials, and destination-side state
all outlive the machinery that created them, and the inventory is what gets
carried across. Replacing the machinery is the cheap half; carrying the
identity and effect inventory across the boundary is the work. A rebuild
skips step 4 only in the sense that the new contract describes a factory you
chose rather than one you inherited.

## Where this recipe stops

What a recovery pass does not establish:

- It does not establish that the factory is safe. It establishes that the
  factory is describable. Every guarantee is at `declared` at best, and
  `declared` means a promise that can be reviewed and falsified, not one
  that is enforced (`README.md`, evidence states).
- It does not establish that the inventory is complete. Enumeration reaches
  the surfaces you know how to query, and a worker or a person that mutates
  a destination without a claim is invisible to it
  ([topology-aware-scheduling](../../patterns/topology-aware-scheduling.md),
  bypass).
- It does not undo effects already published. Two merged pull requests for
  one change are a destination-side problem with destination-side remedies;
  dedup state the destination has expired no longer deduplicates anything
  ([effect-identity](../../patterns/effect-identity.md)).
- It does not bound time to resolution. UNKNOWN can persist indefinitely,
  and the cited instance remains pending permanently
  ([explicit-unknown-state](../../patterns/explicit-unknown-state.md), limits).
- It does not decide whether the recovered backlog is worth running. Whether
  a work item should still exist is a semantic judgment for a model or a
  person; the mechanism validates and executes the result
  ([topology-aware-scheduling](../../patterns/topology-aware-scheduling.md)).
- It has no executable drill. The kit's eight drills inject faults into a
  factory that works; none of them simulate inheriting one that does not,
  and the pass leaves a written inventory as its evidence rather than a
  drill artifact.
