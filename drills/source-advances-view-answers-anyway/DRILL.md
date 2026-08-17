# Drill: source-advances-view-answers-anyway

## Question

A derived read model (a search index, a cache, a materialized view, a
projection) is built by consuming the source of truth's ordered history. The
source advances and the update carrying the new record never reaches the view.
The view keeps serving. What does a query against it return, and what does its
health surface say, while it holds a strict prefix of the source?

The stakes are set by a stale search index over agent session history. Five
friction records were written by one agent inside a 46 minute window on
2026-08-16, all naming the same cause: the shared lexical index was months
behind, so prior-session lookup was unusable and the fallback was manual
reconstruction from other stores (local observation:
gascity2026:.papercuts.jsonl). One of the five records both halves of the
failure in one line: health reported the index stale, last indexed 2026-04-22,
and an exact search for a known term returned zero hits (local observation:
gascity2026:.papercuts.jsonl). The freshness signal existed. It was not on the
path the query took, so the query returned an empty result set that a caller
cannot tell apart from the record never having existed.

## Invariant

A derived view publishes the position in the source's ordered history it has
consumed, and answers a query only within the freshness the caller requires,
measured against that position. A query whose requirement the view cannot meet
returns a lag-exceeded outcome naming both positions rather than a result set
computed from the prefix it happens to hold
([lag-bounded-reads](../../patterns/lag-bounded-reads.md)). Every surface that
reports the view's health derives its verdict from those same two positions, so
a running indexer is never by itself evidence of a fresh view.

## Initial state

- Work item `w-1` exists at generation 1 and is claimed by attempt 1, which
  launches session `sess-1` on `worker-1`.
- Derived view `view-1` exists with a declared freshness contract of zero
  positions. An exact lookup for a named record is a read-your-writes query, so
  the contract it declares for that query admits no lag at all.
- The source's first record `eff-0` is applied at the destination and consumed
  by the view over the delivery bus, so at the barrier the view's published
  position equals the source position and the view holds one entry.
- The indexing path travels over the delivery bus, so it is droppable
  independently of the write to the source. The write itself does not travel
  over the bus: the source advancing and the view learning about it are
  different mechanisms, which is the property that makes the gap possible.
- The ordered event store assigns sequence numbers, so every ordering claim
  below is provable from the log rather than from timing.

## Fault barrier

Named barrier: `view-current`. The run is held at the one moment the view is
provably caught up: published position 1, source position 1, one entry indexed.
Holding here matters, because a view that was never current is a different and
easier failure. The events on either side of the injection: before, the claim,
the session launch, the application of `eff-0`, and the delivered index update
for it; after, the application of `eff-1` at the source. The barrier is
checkable, since the controller releases the run only after reading back the
`view-indexed` record. Ordering is proven by store sequence, never by sleeping.

## Injected fault

At the barrier: `drop_event`, which arms the delivery bus to drop the next
event. The source then applies `eff-1` at the destination, and the index update
carrying `eff-1` is emitted and dropped. The view's published position stays at
1 while the source position becomes 2, and the view's entry list never gains
`eff-1`.

Nothing else is disturbed. The indexer is running and has consumed records
successfully in this run, the source write committed, the session is alive, and
the view answers every query it is given. This is the shape that makes the
failure expensive: nothing on the view's own side of the gap looks wrong.

## Expected observations

- `view-indexed` for `eff-0` appears before the fault, recording published
  position 1 against source position 1.
- `effect-applied` for `eff-1` follows the fault injection, and
  `index-update-dropped` for `eff-1` follows that. Exactly one `view-indexed`
  event exists in the whole run.
- `view-queried` for `eff-1` records `basis:
  published-position-compared-to-source`, outcome `lag-exceeded`, lag 1 against
  a required lag of 0, and carries both positions. It returns no result set at
  all rather than an empty one.
- `view-health-reported` records `basis:
  published-position-compared-to-source`, state `stale`, and lag 1.
- At end of run the destination holds `eff-0` and `eff-1`, and the view holds
  only `eff-0` at published position 1. The protected arm does not close that
  gap; it refuses to answer across it.

## Unsafe negative control

Two protections are removed, one per surface. The query path answers from the
index contents alone, never reading a position, so it returns
`results-complete` with an empty match list. The health surface reports from
the indexer having started, so it reports `fresh` and cannot report a lag at
all. They are the same defect at two moments: a component that never compares
positions has nothing to be stale about.

Expected violation: an exact lookup for a record that is present at the
destination returns a complete result set holding none of it, while the health
surface calls the view fresh. The oracle detects it by recomputing both facts
from ground truth. For the answer, it recounts the record's presence at the
destination against the view's own entry list, never reading a position, so a
view that advanced its position without indexing the record still fails. For
the surface, it recomputes the lag from the destination's mutation count and
the view's published position and compares it against the declared contract,
never reading an entry. Neither check can carry the other. This is a false
empty read, the case the local audit named as the expensive class: a
success-shaped no-op, partial mutation, false empty result, stale assignment,
or missing delivery receipt (local observation:
gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md).

## Pass condition

1. The event log shows the view current, then the fault injection, then the
   source write, then the dropped index update, then the query, then the health
   report, in that order by store sequence.
2. The query outcome is not a complete result set while the destination holds
   the queried record and the view does not.
3. The reported health state is not fresh while the recomputed lag exceeds the
   view's declared contract.
4. The query outcome names both positions, so a caller can act on the gap
   rather than only being refused.
5. The unsafe run returns a complete and empty result set over a record that is
   at the destination, reports the view fresh, and the oracle flags both.
   Protected exits 0; unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, event kind, view identity, effect
  identity, published position, source position).
- The `view-queried` event with its basis, outcome, lag, required lag, and both
  positions, so a wrong answer can be traced to the comparison it was computed
  from rather than to the index's contents.
- The `view-health-reported` event with its basis, so a health verdict computed
  the wrong way is visible even when it happens to be right.
- The destination snapshot and the view snapshot side by side, which are the
  ground truth both oracle checks are recounted from.
- Oracle per-check output for both modes, with the answer half and the surface
  half reported separately (`answer_truthful` and `health_agrees`).

## What a pass does not establish

- It says nothing about the view catching up. The protected arm ends with
  `eff-1` still unindexed; refusing to answer is not repair, and recovery is
  [reconciliation](../../patterns/reconciliation.md) territory.
- The published position and the entry advance together in the simulator, so
  the run does not exercise an indexer that advances its position without
  writing the entry. The oracle's answer check is written not to trust the
  position for exactly that reason, but a drill that drives that fault is a
  separate case.
- One dropped update on one held schedule is not a delivery test. A view whose
  updates arrive out of order, or whose position is itself lost, are separate
  cases (inference: we expect position comparison to cover the first, not yet
  demonstrated).
- The source position here is a count of committed mutations at a single
  destination. Whether a real source exposes a comparable position, and whether
  a reader can obtain it cheaply enough to check on every query, must be
  verified per system; where it cannot be obtained, the honest outcome is
  `lag-unknown`, which this drill exercises in unit tests rather than through a
  fault.
- A freshness contract bounds the staleness a caller accepts. It does not make
  a missing record appear: a caller that declares a tolerance of one position
  gets an answer, and that answer is still silent about the record inside the
  gap. The pass establishes that the caller was told which regime it was in,
  not that the result was complete.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 -m adapters.in_memory.run_drill source-advances-view-answers-anyway --mode protected
python3 -m adapters.in_memory.run_drill source-advances-view-answers-anyway --mode unsafe
```

In the simulator the work item is `w-1`, the view is `view-1` with a declared
contract of zero positions, the indexed record is `eff-0`, and the record that
never reaches the view is `eff-1`. Both runs end with source position 2 and
published position 1. The protected run answers `lag-exceeded` and reports
health `stale`; the unsafe run answers `results-complete` with an empty match
list and reports health `fresh`.
