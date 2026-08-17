# Drill: child-completes-after-join

## Question

A coordinator fans one intent out to three children. Child C3 stops renewing
its lease, so the coordinator dispositions it blocked and joins over C1 and C2,
publishing a merged artifact. C3 then finishes anyway: it commits its own
external effect, records its completion, and writes into the merge slot. Does
the factory detect that the fold is now wrong and correct it, or does it end
holding a merged result that silently contradicts its own child records?

Two documented results set the stakes. The dr-61j inventory counted 37 open or
in-progress workflow markers for work that had fanned out and never joined,
the oldest 87 days (local observation:
gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md). The ABA
owner-label probe showed both unsafe systems accepting four obsolete
generation-7 actions after generation 9 became current while both fenced
systems accepted zero (local observation:
temporallab2026:docs/guarantees.md). This drill puts those two failures at the
same point: a late write, landing on the artifact that represents everyone
else's work.

## Invariant

The merged artifact published for a fan-in carries an identity derived from
exactly the set of children whose own evidence says published, and a write into
the merge slot is accepted only when the writer carries the current join
generation, compared atomically at the destination. A child that becomes
published after the join has fired makes the existing merge incomplete, and
completeness is re-derived from the durable child records rather than from the
roster the coordinator held when it fired
([fan-out-fan-in](../../patterns/fan-out-fan-in.md)). Re-deriving from the
source of record every pass is the stated discipline the pattern inherits;
watching by remembering goes wrong silently (local observation:
gascity2026:docs/design/software-factory-philosophy.md).

## Initial state

- Parent P holds logical identity `parent-1` and join generation 1, with a
  lease expiry recorded alongside the generation.
- Children C1, C2, C3 are full work items, each with its own identity, its own
  generation 1, its own lease, and its own effect identity at the destination.
- C1 and C2 have committed their effects and recorded completion, so each folds
  to published from its own evidence.
- C3 holds a claim and has computed what it would write into the merge slot,
  observing an empty destination at that moment. It has committed nothing.
- The destination enforces publication through an atomic compare-and-set on
  (merge slot, current join generation) and retains rejected-write records.
- The ordered event store assigns sequence numbers, so every ordering claim
  below is provable from the log rather than from timing.

## Fault barrier

Named barrier: `before-join`. The run is held after C1 and C2 have published
and after C3 has computed its merge-slot write, and before the coordinator
evaluates the fold. The events on either side of the injection: before, the
durable records of C1's and C2's published dispositions and C3's prepared
write; after, the coordinator's disposition derivation over all three children.
The barrier is checkable, since the controller releases the run only after
reading back both published dispositions and C3's prepared-write record.
Ordering is proven by store sequence, never by sleeping (local observation:
temporallab2026:docs/architecture.md, "named barriers make the dangerous window
causal").

## Injected fault

At the barrier: expire C3's lease, which is how the coordinator learns C3 is
gone, and advance P's join generation from 1 to 2, which is the coordinator
taking the fan-in over in a new epoch. The coordinator then derives
dispositions (C1 published, C2 published, C3 blocked), folds the published
set, and publishes the merged artifact for {C1, C2} under join generation 2.

Then C3 returns. It commits its effect, records its completion, and publishes
its prepared write into the merge slot carrying join generation 1 and its stale
observation of an empty destination. The fault is the straggler that was
written off and came back: a child that is legitimately current on its own work
item and dispossessed of the merge slot.

## Expected observations

- The disposition derivation at join time names C3 blocked, and the fold is
  {C1, C2}. The blocked child is recorded, not dropped.
- The merged artifact's identity names the folded set, so {C1, C2} and
  {C1, C2, C3} are distinguishable artifacts rather than two writes to one
  mutable name.
- C3's completion is accepted on C3's own work item. The drill does not reject
  the child; it rejects the child's write to the parent's slot.
- C3's merge-slot write reaches the destination fence carrying join generation
  1, and the compare-and-set fails against current generation 2. The rejection
  record names generation 1 as stale and 2 as current.
- The recomputation pass re-derives dispositions from the durable child
  records, finds C3 published, and publishes the merge for {C1, C2, C3} under
  join generation 2.
- At the end of the run, the merged artifact at the destination folds exactly
  the children whose own evidence says published.

## Unsafe negative control

Two protections are removed together, because a factory that lacks one
typically lacks the other: publication becomes a caller-side read-then-write
(C3 checks the destination generation it observed earlier, finds nothing, and
writes), and the fan-in is edge-triggered, firing once and never recomputing.

Expected violation: C3's late write replaces the {C1, C2} merge with a merged
artifact folding only C3, while the coordinator's own record still reports a
complete join over C1 and C2. The end state contradicts itself in both
directions, and the oracle detects it by comparing the folded set named in the
surviving artifact's identity against the children whose evidence says
published. The caller-side check is the classic time-of-check to time-of-use
gap (foundational), and its stale-observation form is what the ABA probe
measured.

## Pass condition

1. The event log shows C3's write-off and the join publication after it, by
   store sequence.
2. C3's completion is recorded after the join publication, and C3's merge-slot
   write is present in the log and rejected, with the rejection naming join
   generation 1 as stale.
3. The recomputation pass runs after C3's completion and publishes a merge
   whose identity folds all three children.
4. The merged artifact at the destination at end of run folds exactly the
   published set.
5. The unsafe run ends with the merged artifact folding only C3 while the
   published set is all three, and the oracle flags it. Protected exits 0;
   unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, event kind, work item, generation,
  session, attempt, artifact identity).
- The disposition derivation at every evaluation, with its inputs, so a wrong
  fold can be traced to the record it was computed from rather than to the
  coordinator's memory.
- Join generation history for the parent: (generation, holder, transition
  cause) for generations 1 and 2.
- The rejection record for the late merge-slot write, verbatim.
- Merged-artifact history: every (sequence, artifact identity) pair, so an
  overwrite is detected by identity comparison rather than by inference.
- Per-child effect receipts at the destination, which are what the published
  disposition is derived from.
- Oracle per-check output for both modes.

## What a pass does not establish

- The protected arm changes two things at once: the merge slot is fenced and
  the fold is recomputed. In this schedule the recomputation alone would leave
  the same end state, so the oracle's final-state check does not isolate the
  fence. The suite asserts the fence half separately
  (`test_join_fence_decides_the_late_merge_write`), and a schedule where the
  straggler returns after the last recomputation would separate them in the
  drill itself. That schedule is not implemented here.
- It says nothing about a child that returns after the fan-in is closed and no
  further recomputation is scheduled. Liveness of the recomputation loop is a
  cadence question this drill does not measure.
- It does not establish that merging those three results produces a working
  artifact. The invariant constrains which children were folded and who may
  write, not whether the merge is correct.
- One straggler on one held schedule is not a concurrency test. Two children
  returning at once, or two coordinators evaluating the fold simultaneously,
  are separate cases (inference: we expect the fence to cover the second, not
  yet demonstrated).
- The adapter's compare-and-set stands in for a real destination's
  transaction. Whether a given code host, database, or queue evaluates the
  comparison atomically with the write must be verified per destination.
- It does not establish that a blocked disposition reaches a human. The drill
  records the blocked child; escalation is
  [promise-oriented-observability](../../patterns/promise-oriented-observability.md)
  territory.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 src/adapters/in_memory/run_drill.py child-completes-after-join --mode protected
python3 src/adapters/in_memory/run_drill.py child-completes-after-join --mode unsafe
```

In the simulator the parent is `w-1`, the children are `c-1`, `c-2`, and the
straggler `c-3`, and the merged artifacts are named for the set they fold:
`merge-c-1+c-2` at the join, `merge-c-1+c-2+c-3` after recomputation, and
`merge-c-3` for the straggler's own write. The protected run ends holding
`merge-c-1+c-2+c-3`; the unsafe run ends holding `merge-c-3`.
