# Fan-Out and Fan-In

> **Problem** A join publishes a merged result while one child is still
> running.
>
> **Rule** The join folds durable child records, not whatever the
> coordinator happened to hold.
>
> **Required property** A fan-in completes only when a fresh query over
> durable child records shows every child terminal, and the merged
> artifact carries an identity derived from exactly the dispositions it
> folded, written under a join generation the destination compares
> atomically with the write.
>
> **Wrong** `children I heard from -> merge -> publish`
>
> **Right** `query every child record -> all terminal -> merge -> publish under the join generation`
>
> **See it fail**
>
> - `make drill DRILL=child-completes-after-join MODE=unsafe` exits 2
> - `make drill DRILL=child-completes-after-join MODE=protected` exits 0

## Problem

A coordinator splits one intent into several children, dispatches them to run
in parallel, and later merges their results into one outcome. The fan-out half
is easy to see in the code: a loop over targets, a formula stage that spawns
workers, a batch of dispatch calls. The fan-in half is usually a single wait
over the handles the coordinator happens to be holding, and that wait is where
the guarantees go missing. A join written that way reports on the coordinator's
memory rather than on the work.

Five defects hide behind the same shape, and a factory can have all five while
every individual child behaves correctly.

- **Partial fan-in reported as complete.** The join fires when the children it
  knows about finish, so a child spawned late, retried under a fresh identity,
  or recorded by a different writer sits outside the fold. This is the defect
  [cross-repo-campaigns](cross-repo-campaigns.md) names at campaign scale, a
  completion claim computed over the kickoff set instead of the current one,
  and it takes the same remedy: the join is a reduction over durable records,
  recomputed at every evaluation.
- **The straggler that returns after the join.** A child the coordinator wrote
  off finishes anyway and writes into a merged result nobody is watching any
  more. Authority over the merged artifact has already moved to a later join
  epoch, which makes the late write a dispossessed writer's write, the case
  [fenced-authority](fenced-authority.md) and the
  [stale-writer-completes](../drills/stale-writer-completes/) drill cover for a
  single work item.
- **No identity on the merged result.** A join interrupted after its external
  write and retried publishes a second merged artifact, because nothing about
  the merge is keyed by what it folded. [effect-identity](effect-identity.md)
  supplies the missing key: an identity derived from the logical invocation and
  its inputs, honored inside the destination's atomicity domain rather than by
  a caller-side lookup.
- **A join that owns no fence.** A retried parent, a supervisor restart, or a
  duplicated dispatch can leave two coordinators each believing it owns the
  fan-in. With no generation compared at the destination, both merges land and
  the later one silently reverts the earlier.
- **Children that are not idempotent under re-fan-out.** Recomputing the roster
  fixes the first defect and arms this one, since re-dispatch reaches children
  that already committed external effects. Without a stable effect identity per
  child, every recomputation is a new duplicate.

Related pages: [reconciliation](reconciliation.md) is the loop that makes the
join level-triggered instead of edge-triggered;
[durable-intent](durable-intent.md) keeps the fan-out re-derivable after the
coordinator dies; [stable-work-identity](stable-work-identity.md) is what makes
a re-dispatched child resolve to the existing child rather than a new one.

## Observed failure

The dr-61j inventory of 2026-07-15 counted 37 open or in-progress
workflow-marker beads for multi-step work that had fanned out and never joined.
The oldest strand was 87 days old, and a cluster of six molecules whose
finalize steps routed to a dispatcher was live respawn-loop fuel: the fan-out
kept producing while the fan-in never fired.
(gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md)

The same system measured the gap between a child's status and a child's
outcome. Before the `work-landing-reaper` existed, closed-but-never-merged
branches stranded at roughly 12 per day. A join summing child status fields
over that population would have reported complete coverage while the artifacts
never reached main.
(gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md)

Late writers are accepted whenever nothing at the destination compares
generations. In the ABA owner-label probes over 30 publication pairs per
system, both unsafe systems accepted four obsolete generation-7 actions after
generation 9 became current, and both fenced systems accepted zero.
(temporallab2026:docs/guarantees.md)

Re-dispatch without effect identity duplicates at the destination rather than
at the record. All 18 unsafe trials in the durability lab recorded two external
effects under one recorded completion; every protected arm applied the effect
once. A join that recomputes its roster and re-fans-out to satisfy it inherits
that arithmetic once per recomputation.
(temporallab2026:docs/guarantees.md)

The record itself can be the thing that lies about the fold. In gc-na2o,
metadata written to the wrong rig left about 40 orphan worktrees and 1384 false
`worktree-recorded` audit events, so a reducer reading that log would have
counted successes that never happened.
(gascity2026:docs/design/city-reliability-surface.md)

## Invariant

A fan-in is complete only when a fresh query over the durable child records
shows every child of this fan-out carrying a terminal disposition, and the
merged artifact published for that fan-in carries an identity derived from
exactly the set of dispositions it folded, written under a join generation the
destination compares atomically with the write.

Read against a formula you already run, the invariant splits into two checks. If
the join stage can name which children it folded, and that name is a function of
durable records rather than of what the stage had in hand, the first half holds.
If a child returning after the merge is rejected by the destination rather than
by a check the child runs on itself, the second half holds. A join that passes
one and fails the other produces a merged artifact that is complete and
overwritable, or fenced and partial.

## Mechanism

The parent carries a logical identity and an intent expressed as a predicate
over durable state, so the child roster can be re-derived at any moment rather
than retrieved from the coordinator. Each child is a full work item: its own
identity, its own ownership generation, its own effect identities, its own
evidence at the effect boundary. Children recover independently, which is what
keeps the blast radius of one failure at one child.

The join is a fold, and its result is two values rather than one: a completeness
verdict and the child set the verdict was computed over.

```
fan_in(parent, now):
    children = discover(parent.intent, durable_records)   # never a cached list
    for c in children:
        disposition(c) = fold over c's effect-boundary evidence:
            published  if the effect committed and the receipt is in the record
            blocked    if a blocked record with reason and owner is current
            exempted   if an exemption record with reason is current
            undispositioned otherwise
    if any child is undispositioned:
        return incomplete, naming those children
    merge_id = identity(parent.logical_identity,
                        sorted(dispositioned children))
    publish(merge_id, generation = parent.join_generation)
```

Three properties of that fold carry the pattern.

The disposition vocabulary is the campaign vocabulary, deliberately. A child is
published, blocked with a named owner, or exempted with a stated reason; a
child that is merely "done according to itself" is undispositioned, and an
undispositioned child blocks completion instead of being dropped from the fold.
This is what makes a written-off straggler a visible incompleteness rather than
a silent subtraction.

The merge identity is derived from the folded set, so two joins that folded
different sets produce different identities and two joins that folded the same
set produce the same one. A retried join is then a duplicate write of a known
identity, which a destination with deduplication semantics absorbs, and a join
that folded a larger set is a distinct artifact rather than an accidental
overwrite of the smaller one.

The join generation is the parent's, not any child's. It advances when the
fan-in is reassigned or reopened, and it is compared at the destination in the
same operation as the write. A child's own generation fences that child's
publications; only the parent's generation can fence a write into the merged
result, which is why a straggler that is still legitimately generation 1 on its
own work item must still be rejected at the merge slot.

The kit's contract schema has no fan-in block. A fan-in is declared today out
of fields that already exist in `schemas/factory.schema.json`, and the property
above is checkable exactly when these are filled in:

```yaml
work:
  logical_identity: parent_intent_id       # the fan-out's stable key
  ownership:
    generation: join_generation            # the parent's epoch, not a child's
    lease_expiry: join_lease_expires_at
    fence:
      enforced_by: destination             # never "caller" for the merge slot
      operation: compare-and-set

artifacts:
  identity: merged_tree_digest             # a function of the folded child set
  publication:
    conditions:
      - current_generation                 # the join epoch, rechecked at write
      - verification_matches_artifact

effects:
  - name: publish_merged_result
    destination: code_host
    effect_identity: parent_id + folded_child_set_digest
    retry_contract: deduplicate

reconciliation:
  - fact: child_dispositions
    query: read_children_of_parent_from_record
    interval: 5m

campaigns:
  completion:
    all_current_targets_have_disposition: [published, exempted, blocked_with_owner]
```

The `campaigns.completion` block is doing double duty here. Its rule is written
for discovery targets, and a fan-out's children are the same kind of population:
a set that changes under the job, judged over its current members rather than
its original ones.

## Where enforcement occurs

Completeness is enforced by the reducer, and the reducer must read the durable
child records on every evaluation. Nothing about the roster is cached between
runs, because a reliability layer that watches by remembering goes wrong
silently and reports green while doing it.

Merge authority is enforced at the destination, in the same operation as the
merged write, by comparing the join generation. A coordinator that checks its
own ownership and then writes has a time-of-check to time-of-use window
covering the whole merge, which is the window a straggler returns into. The ABA
probe result is the measurement that separates the two designs: the fence, not
the owner label and not the durability substrate, supplied the safety.

Merge identity is enforced at the destination's atomicity domain, which is the
only place a duplicate can be recognized as one. A join that computes a merge
identity and then asks the destination whether it exists has moved the check
back to the caller and rebuilt the same race one layer up.

Child idempotence is enforced per child, at each child's effect boundary,
through effect identities that survive re-dispatch. Recomputing the roster is
safe only to the degree that acting on the recomputation is safe.

## Does not guarantee

- No atomicity across children. Partial states are visible for the entire life
  of the fan-out, and any consumer reading mid-flight sees some children landed
  and some not.
- No ordering between children. If the merge requires children to land in an
  order, that is a dependency graph between children, not a property of the
  join.
- No liveness for a blocked child. The pattern makes an undispositioned child
  block completion visibly; it does not unblock anything, and a fan-in with a
  permanently blocked child stays permanently incomplete by design.
- No correctness of the merge itself. The invariant constrains which children
  the merge folded and who may write it, and says nothing about whether merging
  those results produced a working artifact.
- No completeness beyond the discovery predicate. A child the predicate cannot
  see is never dispositioned and never blocks completion, so the predicate
  bounds the coverage claim.
- No protection against a wrong exemption. An exemption with a plausible reason
  removes a child from the fold exactly as effectively as a correct one.

## Failure drill

[child-completes-after-join](../drills/child-completes-after-join/) injects the
sharpest of the five defects: a child the coordinator has written off completes
after the join has already fired and published. The drill is executable against
the in-memory simulator in both modes.

```
python3 src/adapters/in_memory/run_drill.py child-completes-after-join --mode protected
python3 src/adapters/in_memory/run_drill.py child-completes-after-join --mode unsafe
```

The protected arm rejects the straggler's write at the merge slot and
recomputes the fold to include it. The unsafe arm accepts the straggler's late
write over the merged artifact and never recomputes, leaving a destination
artifact that folds one child while the coordinator's record claims a complete
join over two others. Evidence for both arms lands in `out/evidence/`.

## Evidence

- 37 stranded workflow markers for work that fanned out and never joined,
  oldest 87 days, with a six-molecule respawn-loop cluster: local observation
  (gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md).
- Closed-but-never-merged branches stranding at roughly 12 per day before a
  reaper existed: local observation
  (gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md).
- Closed state is a status signal, not an outcome signal; the terminal check is
  artifact ancestry: local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- Unsafe systems accepting four obsolete generation-7 actions after generation
  9 became current, fenced systems accepting zero, over 30 publication pairs
  per system: local observation (temporallab2026:docs/guarantees.md).
- All 18 unsafe trials applying the external effect twice under one recorded
  completion: local observation (temporallab2026:docs/guarantees.md).
- 1384 false `worktree-recorded` audit events and about 40 orphan worktrees
  from metadata written to the wrong rig: local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- Re-derive from the source of record every tick; watching by remembering goes
  wrong silently: local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- Fencing tokens checked at the resource, not at the caller: foundational
  (Kleppmann 2017, ch. 8).
- Structured synchronizing merge as a named workflow control-flow pattern with
  its own correctness conditions: foundational (van der Aalst et al. 2003).
- The fan-in expressed as a fold whose result carries the folded set, with the
  merge identity derived from that set and the parent's generation fencing the
  merge slot: inference (our synthesis; the components above are observed, this
  composition is demonstrated only against the in-memory simulator).

## Limits

No system in the evidence base runs this pattern end to end. The production
installation supplies the stranded-join failure record and the status-versus-
outcome discipline, the durability lab supplies the fencing and duplicate-effect
results at the single-work level, and the composed join is designed from those
parts. The drill demonstrates the composition against a simulator, which is
evidence that the design is coherent and not evidence that a real destination
implements the compare atomically.

Recomputation cost is unaddressed. Re-deriving the roster on every evaluation
is the correctness move and, for a wide fan-out, also the expensive one; none
of the sources measure discovery latency at fleet scale or the staleness window
left by a slow cadence.

The pattern assumes child evidence is queryable in one place. A fan-out whose
children record their evidence in different stores reopens the gc-28jm shape
(the check reads a different store than the one holding the fact), this time at
the join, where a lookup that returns empty for the wrong reason reads exactly
like a child that never ran.

Nothing here addresses a merge that must be recomputed because a child's result
changed after it was folded. The invariant treats a disposition as terminal;
factories where children can revise a published result need an additional rule
about when a merged artifact is invalidated, which this page does not supply.

## Sources

- gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md
- gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- temporallab2026:docs/guarantees.md
- Kleppmann 2017, Designing Data-Intensive Applications, ch. 8
- van der Aalst et al. 2003, Workflow Patterns
- Executable drill: [child-completes-after-join](../drills/child-completes-after-join/DRILL.md)
