# Drill: guard-refuses-repair-never-runs

## Question

An operation enforces a precondition on a resource it also knows how to put
right. The resource drifts out of that precondition. Does the operation
recover, and if it does not, does the next run do any better than this one?

The stakes are set by a scheduled instrument that stopped examining its
subjects for four days without anyone noticing. Its write path refused any
audit log that was group or world writable, and the call that forced the mode
back to owner-only ran after that refusal, on a descriptor the refusal
prevented it from opening. The log drifted to 0664 on a run that itself
succeeded, at 2026-08-13T00:25 local; the next 94 scheduled runs exited
non-zero without checking a single subject, and the last of them failed
exactly as the first had (local observation:
gascity2026:bin/pool-worktree-provision-check,
gascity2026:.gc/events.jsonl). The read path carried the same defect one
call earlier, and its state file is still unreadable to the tool for that
reason.

## Invariant

A guard refuses only what the operation cannot repair. When the operation
holds a repair step for the precondition it enforces, that step runs first and
the check verifies the result
([repairable-preconditions](../../patterns/repairable-preconditions.md)). A
check placed ahead of the repair that would satisfy it is a one-way door: it
is invisible while the resource is good, and the first drift is permanent,
because the only code that could clear the refusal sits behind it.

## Initial state

- Work item `w-1` exists at generation 1 and is claimed by attempt 1, which
  launches session `sess-1` on `worker-1`.
- Resource `res-1` exists and is in the state the guarded write requires,
  `owned-and-private`. The required state is recorded on the resource at
  creation, so the transition the guard is about is named before anything
  crosses it.
- The operation holds both a precondition check over `res-1` and a repair that
  restores `res-1` to the required state. Both arms hold both. They differ
  only in which one runs first, so nothing about the guard's strictness or the
  repair's capability can account for a difference in outcome.
- One guarded write of record `eff-0` lands before the barrier, in both arms,
  while the resource conforms. That write is the control for the ordering: up
  to the barrier the two arms produce the same events in the same order.
- The destination honors record identity, so a retried write of the same
  record is a duplicate of a known identity rather than a second mutation.
- The ordered event store assigns sequence numbers, so every ordering claim
  below is provable from the log rather than from timing.

## Fault barrier

Named barrier: `resource-conforming`. The run is held after the conforming
write has landed and before the resource drifts. Holding here matters because
it fixes the order in the one way that makes the finding readable: the
operation is demonstrably working at the moment the fault is injected, so a
later refusal cannot be attributed to the operation having been broken all
along. The events on either side of the injection: before, the claim, the
session launch, the repair, and the applied write of `eff-0`; after, the drift
and two attempts at `eff-1`. The barrier is checkable, since the controller
releases the run only after reading back the `resource-added` and
`guarded-write-applied` records.

## Injected fault

At the barrier: `drift_resource`, which moves `res-1` from `owned-and-private`
to `shared-writable`. Nothing else is disturbed. The resource still exists, is
still owned by the factory, still holds its contents, and is still repairable
by the operation's own repair step. This is drift, not tampering, and the
distinction is the point: the state the guard refuses is one the operation can
fix without help.

The same guarded write of `eff-1` is then attempted twice. The second attempt
is not a retry with different inputs; it is the next scheduled run, and it
exists in this drill so that permanence is on the record rather than inferred
from one failure.

## Expected observations

- `resource-added` for `res-1` carries `required_state:
  owned-and-private`, and precedes everything else about the resource.
- `guarded-write-applied` for `eff-0` appears before the fault in both arms,
  and the event kinds before `fault-injected` are identical between arms.
- `resource-drifted` follows the fault injection, carrying `from_state:
  owned-and-private` and `to_state: shared-writable`.
- In the protected run: `precondition-anomaly-reported` names the observed and
  required states, then `resource-repaired` moves the resource back, then
  `guarded-write-applied` records the write. The same three appear for the
  second attempt, whose repair is a no-op on an already conforming resource.
- In the unsafe run: two `guarded-write-refused` events, each carrying
  `outcome: refused-before-repair` and `repair_reached: false`, and no
  `resource-repaired` event anywhere after the drift.
- At end of run the destination holds `eff-1` in the protected arm and does
  not in the unsafe arm, and `res-1` conforms in the protected arm and is
  still `shared-writable` in the unsafe arm.
- The unsafe arm emits no `precondition-anomaly-reported` event. The refusal is
  the only signal it produces, which is why deleting a refusal without adding
  the report loses something real.

## Unsafe negative control

One protection is removed: the ordering. The unsafe arm runs the identical
precondition check ahead of the identical repair, returns on the refusal, and
never reaches the repair. Nothing else changes: same resource, same drift, same
repair implementation, same destination, same two attempts.

Expected violation: the write never reaches the destination, the resource is
never repaired, and attempt 2 is refused exactly as attempt 1 was. The oracle
detects it on two rails computed from ground truth. The progress rail asks
whether `eff-1` is at the destination, reading the destination's mutation list
and never the resource or a refusal event. The repair rail asks whether the
repair ran after the drift and whether the resource conforms at end of run,
reading the resource's own state and the repair events and never the
destination. Neither rail can carry the other, and the second exists because
passing the first is not sufficient: a write that lands while the resource
stays drifted has deleted the guard rather than reordered it, and has lost the
anomaly report along with it.

## Pass condition

1. The event log shows the conforming write, then the fault injection, then
   the drift, then two attempts at the same write, in that order by store
   sequence.
2. The event kinds before `fault-injected` are identical in both arms, so the
   arms are distinguishable only after the drift.
3. `eff-1` is at the destination at end of run.
4. A `resource-repaired` event for `res-1` exists after the drift, and `res-1`
   conforms at end of run. This is asserted separately from condition 3,
   because a write that landed without the repair satisfies one and not the
   other.
5. The unsafe run refuses both attempts with `repair_reached: false`, leaves
   the resource drifted, leaves the destination without `eff-1`, and the
   oracle flags both rails. Protected exits 0; unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, event kind, resource identity,
  observed and required state, record identity, attempt, outcome).
- Both `guarded-write-refused` events with their `repair_reached` field, which
  is what separates a refusal the operation could have cleared from one it
  could not. Without that field the log shows a guard doing its job in both
  arms.
- Every `resource-repaired` and `resource-repair-failed` event, so the repair's
  reachability is a matter of record rather than of reading the code.
- The resource snapshot at end of run, which is the ground truth the repair
  rail recomputes from.
- The destination snapshot at end of run, which is the ground truth the
  progress rail recounts from.
- Oracle per-rail output for both modes, reported separately (`progress` and
  `repair_reachable`), so a run that fails one rail is not read as failing the
  other.

## What a pass does not establish

- It establishes that this operation recovers from this drift. A resource can
  leave the required state in ways the repair does not cover, and the claim is
  exactly as wide as the drift that was injected.
- The drill does not show that the guard is worth keeping in any form. It shows
  that the refusal must not precede the repair. Whether the check should be
  reordered, converted to a report, or deleted outright is a judgment about
  what else enforces the property, and the pattern page argues that deletion is
  often correct.
- Two attempts demonstrate that the second is no better than the first. They do
  not measure how long the condition would persist in a real installation,
  which depends on whether anything else ever writes the resource.
- The protected arm still refuses a resource its repair cannot move, and that
  branch is exercised by a unit test rather than by this drill. A drill that
  only ever shows the guard yielding would not distinguish repair-then-verify
  from having no guard at all.
- Nothing here addresses whether the precondition is the right one. An
  operation can order its guard and its repair correctly and still be
  enforcing a property that does not matter.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 src/adapters/in_memory/run_drill.py guard-refuses-repair-never-runs --mode protected
python3 src/adapters/in_memory/run_drill.py guard-refuses-repair-never-runs --mode unsafe
```

In the simulator the work item is `w-1`, the resource is `res-1`, and the
record under contention is `eff-1`. Both runs land `eff-0` before the barrier
and both drift the resource at it. The protected run reports the anomaly,
repairs `res-1`, and records `attempt_outcomes: ["applied", "applied"]`; the
unsafe run records `["refused-before-repair", "refused-before-repair"]` and
ends with `res-1` still `shared-writable` and the destination without `eff-1`.
