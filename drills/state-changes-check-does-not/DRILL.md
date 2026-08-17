# Drill: state-changes-check-does-not

## Question

A factory runs a check over its own state. The state moves across the boundary
the check claims to police, from violating to satisfied. What does the check
report on either side of that move, and how would anyone know if the answer
were the same both times?

The stakes are set by a guard that ran for months over an empty domain. A reap
protocol keyed one of its four preconditions on `gc.claimed_at`, a field
written on 0 of 1,298 work items in the store it queried, so that leg passed
for everything that had ever existed and the protocol was reported as four
independent confirmations when three were doing the work (local observation:
gascity2026:docs/conventions/city-learnings.md). Re-measured across all 23
bead databases the same key was present on 0 of 16,646 items, recorded in the
audit as confirmed and worse than first reported (local observation:
gascity2026:.gc-reports/factory-contract-audit-2026-08-16/audit-one-work-item.md).
The same shape appears with the sign flipped: a close-gate reaper accumulated
32,618 detections against zero repairs over 11 days, because a timeout was
killing every repair run before it could act (local observation:
gascity2026:docs/asks-and-outcomes.md, work item `dr-qg34j`). One check could
not go red, the other could not go green, and both kept reporting.

## Invariant

A check earns its place by discriminating: someone has demonstrated a state
change that flips its verdict, and the demonstration is recorded next to the
check. Its verdict is computed from an input the check did not itself produce,
so the branch reporting the other verdict is reachable
([falsifiable-checks](../../patterns/falsifiable-checks.md)). A check whose
verdict is constant across a transition that crosses its own claim is reporting
its implementation, not the state.

## Initial state

- Work item `w-1` exists at generation 1 and is claimed by attempt 1, which
  launches session `sess-1` on `worker-1`.
- Check `chk-1` is registered with an explicit claim, that the destination
  holds record `eff-1`, and an explicit subject, the record `eff-1` itself. The
  claim and the subject are recorded at registration, because a check that does
  not name the state change it distinguishes cannot be shown to distinguish
  one.
- The destination is empty. Its semantics are identical in both arms of this
  drill: the arms differ only in how the check computes its verdict, so nothing
  about the destination's behaviour can account for a difference in what the
  check reports.
- The application of `eff-1` travels over the delivery bus, so it is droppable
  and the destination can be held in the state the check exists to catch.
- The ordered event store assigns sequence numbers, so every ordering claim
  below is provable from the log rather than from timing.

## Fault barrier

Named barrier: `check-registered`. The run is held immediately after the check
is registered and before anything it examines has happened. Holding here matters
because it fixes the order: the check exists before the state it will report
on, so neither evaluation can be explained by the check having been written
after the fact to match what it found. The events on either side of the
injection: before, the claim, the session launch, and the check registration;
after, the attempted application of `eff-1`. The barrier is checkable, since
the controller releases the run only after reading back the `check-registered`
record.

## Injected fault

At the barrier: `drop_event`, which arms the delivery bus to drop the next
event. The application carrying `eff-1` is emitted and dropped, so the
destination never receives it and the state the check claims to be about is
false. The check is then evaluated. A retry applies `eff-1` for real, making
the claim true, and the check is evaluated a second time against the same
subject.

Nothing else is disturbed. The session is alive, the check runs on schedule,
it returns a verdict on both occasions, and it never errors. This is the shape
that makes the failure expensive: a check that cannot fail and a check that is
passing look identical from outside, and so do a check that cannot succeed and
a check with real work to report.

## Expected observations

- `check-registered` for `chk-1` appears before the fault, carrying the claim
  and the subject record.
- `effect-application-dropped` for `eff-1` follows the fault injection, and
  `effect-applied` for `eff-1` follows the first evaluation. Exactly one
  `effect-applied` event for `eff-1` exists in the whole run.
- The first `check-evaluated` records `basis: destination-readback`, `read_key:
  destination/eff-1`, and verdict `fail`.
- The second `check-evaluated` records the same basis and read key, and verdict
  `pass`.
- No `check-key-written` event exists in the protected run. The check writes
  nothing it later reads, so no evaluation can be answering from its own
  output.
- At end of run the destination holds `eff-1` and the check's recorded verdict
  list is `["fail", "pass"]`. Two verdicts over one subject across one state
  change is the smallest evidence that the check discriminates.

## Unsafe negative control

One protection is removed. The check computes its verdict from a metadata key
it stamps at the start of each evaluation rather than from the destination, so
each evaluation reads back the value it just wrote. Nothing else changes: same
fault, same drop, same retry, same destination.

Expected violation: the check reports `pass` while the record is missing, and
`pass` again after the record arrives, so it is silent about the fault it was
registered to catch and its `fail` branch is unreachable in every state. The
oracle detects it on two rails computed from ground truth. The discrimination
rail compares the two verdicts across the state change and confirms from the
destination's mutation list that the state actually moved, reading no key. The
provenance rail compares the key each evaluation read against the keys that
check wrote earlier in the log, reading no verdict. Neither rail can carry the
other, and the second exists because passing the first is not sufficient: a
self-supplied input that happens to vary would satisfy discrimination while
still testing the writer rather than the property.

This is the live gate in the installation supplying the observations. Its
review gate is two mutable metadata keys on the work item, and the code that
closed the work wrote the passing verdict it then read
(`producer_disposition_close.go`), while the shell rail beside it validated the
key's name and never its value (`bin/gc-outcome-close`). Both halves are fixed
in code, and all 216 affected closes predate the fix (local observation:
gascity2026:.gc-reports/factory-contract-audit-2026-08-16/README.md).

## Pass condition

1. The event log shows the check registered, then the fault injection, then the
   dropped application, then the first evaluation, then the record applied, then
   the second evaluation, in that order by store sequence.
2. The destination holds `eff-1` at end of run, so the state genuinely crossed
   the check's claim between the two evaluations.
3. The two verdicts differ. A check returning the same verdict on both sides
   fails whether that verdict is pass or fail, because a never-red check and a
   never-green check are the same defect measured in opposite directions.
4. No key read by an evaluation appears among the keys that check wrote.
5. The unsafe run returns the same verdict on both sides over the same state
   change, reads a key it wrote moments earlier, and the oracle flags both.
   Protected exits 0; unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, event kind, check identity, subject
  record, verdict, basis, read key).
- Both `check-evaluated` events with their basis and read key, so a verdict can
  be traced to the input it was computed from rather than to the check's name.
- Every `check-key-written` event with the check that made it, which is what
  makes self-supplied input visible at all. A check reading its own output is
  indistinguishable from a check reading the world unless the writes are
  recorded.
- The destination snapshot at end of run, which is the ground truth the
  discrimination rail recounts the state change from.
- Oracle per-rail output for both modes, reported separately (`discriminates`
  and `independent_input`), so a run that fails one rail is not read as failing
  the other.

## What a pass does not establish

- It establishes that this check discriminates on this transition. It says
  nothing about any other transition: a check that separates missing from
  present may still be blind to wrong-value, stale-value, or duplicate. The
  demonstrated mutation bounds the claim, and the claim is exactly as wide as
  the mutation.
- The drill mutates the state the check reads, not the check's own code. A
  suite that only ever runs the check against real states cannot tell a passing
  check from an unreachable branch, which is why the pattern asks for the
  complementary evidence: disable the check and watch a specific named test go
  red.
- The never-green direction is exercised through the discrimination rail rather
  than through a separate arm. A check pinned to fail and a check pinned to
  pass are caught by the same comparison here, which is the point, but a drill
  driving an actual unreachable repair path (detections accumulating while the
  repair leg times out) is a separate case.
- One check with one subject is not a survey. Whether a factory's other checks
  discriminate is not implied by this one doing so, and the audit evidence says
  the base rate is poor: on 2026-08-08 every instrument audited against that
  installation's own instrument contract failed the make-it-fail clause before
  it was touched, four for four, one with no test at all and three with suites
  that had only ever been observed passing (local observation:
  gascity2026:docs/conventions/instrument-contract.md).
- A check that discriminates can still be measuring the wrong property. Passing
  both rails means the verdict tracks something real and was not self-supplied;
  it does not mean the something is what the check's name says it is.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 src/adapters/in_memory/run_drill.py state-changes-check-does-not --mode protected
python3 src/adapters/in_memory/run_drill.py state-changes-check-does-not --mode unsafe
```

In the simulator the work item is `w-1`, the check is `chk-1`, and its subject
is the record `eff-1`. Both runs end with `eff-1` present at the destination
and two evaluations recorded. The protected run reports `fail` then `pass` from
`destination/eff-1`; the unsafe run reports `pass` then `pass` from
`chk-1/verdict`, a key it wrote before each read.
