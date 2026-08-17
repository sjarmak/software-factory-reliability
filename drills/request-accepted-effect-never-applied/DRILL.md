# Drill: request-accepted-effect-never-applied

## Question

An operator or an agent calls a command boundary and asks for one effect at a
destination. The boundary durably accepts the request and its own outbound call
succeeds. The application leg then never runs: the event carrying it is
dropped, and nothing reaches the destination. What does the boundary return to
the caller, and what is left behind when the request is never applied?

Two documented results set the stakes. A dead-letter queue held 67 undelivered
dispatches accumulated between 2026-06-12 and 2026-08-12, all of them work
direction and read by nothing (local observation: gascity2026:CLAUDE.md); the
audit that re-checked that figure found the backlog pruned rather than triaged,
under a one-hour dead retention and a six-hourly reporter that overwrites its
own output (local observation:
gascity2026:.gc-reports/factory-contract-audit-2026-08-16/drills-3-4-effects-reconciliation.md).
In the other direction, one queued dispatch was delivered three times out of
three and recorded zero out of three, and was re-sent for a full day against a
recipient that had already acted (local observation:
gascity2026:docs/conventions/bead-dispatch.md). This drill puts a boundary in
the middle of that: acceptance succeeded, application did not, and the return
value is the caller's only evidence.

## Invariant

The outcome the boundary returns classifies the postcondition the caller asked
for, read back from the destination rather than inferred from the boundary's
own outbound call, so a success-shaped outcome is returned only when the
requested effects are present at the destination
([postcondition-typed-outcomes](../../patterns/postcondition-typed-outcomes.md)).
Every accepted request additionally holds a durable terminal record at end of
run: the returned value is a value the caller may discard, and a promise with
no record expires into nothing that a later scan can find
([promise-oriented-observability](../../patterns/promise-oriented-observability.md)).

## Initial state

- Work item `w-1` exists at generation 1 and is claimed by attempt 1, which
  launches session `sess-1` on `worker-1`.
- The destination is empty: no mutations, no artifact.
- The boundary holds a request record type carrying the request identity, the
  work item, and the set of effect identities the caller asked for. Acceptance
  and application are separate fields, which is what makes the classification
  below expressible at all.
- The application leg travels over the delivery bus, so it is droppable
  independently of the acceptance record, matching a real boundary whose accept
  path and apply path are different mechanisms.
- The ordered event store assigns sequence numbers, so every ordering claim
  below is provable from the log rather than from timing.

## Fault barrier

Named barrier: `request-accepted`. The run is held after the boundary has
durably accepted the request and before the application leg is emitted. The
events on either side of the injection: before, the claim, the session launch,
and the `request-accepted` record naming `eff-1` as the requested effect; after,
the emission of the application leg. The barrier is checkable, since the
controller releases the run only after reading back the acceptance record.
Ordering is proven by store sequence, never by sleeping (local observation:
temporallab2026:docs/architecture.md, "named barriers make the dangerous window
causal").

## Injected fault

At the barrier: `drop_event`, which arms the delivery bus to drop the next
event. The application leg is then emitted and dropped, so `eff-1` never reaches
the destination and the destination holds zero mutations for the remainder of
the run.

Nothing else is disturbed. The acceptance record is intact, the session is
alive, the work item still holds its lease, and the boundary's own outbound call
reported success. This is the shape that makes the failure expensive: every
signal available on the boundary's own side of the call says the request went
through.

## Expected observations

- `request-accepted` names `req-1`, work item `w-1`, and requested effects
  `["eff-1"]`, before the barrier.
- `apply-effect-dropped` follows the fault injection, and no
  `effect-applied` event appears anywhere in the log.
- `outcome-classified` records `basis: destination-readback`, and its `landed`
  and `absent` fields name the readback's actual result: `landed` empty,
  `absent` holding `eff-1`.
- The returned outcome is `requested-and-queued`. It is not success-shaped, and
  a caller can distinguish it from applied without inspecting the destination
  itself.
- The window closes with `request-terminal` recording state `expired` and the
  reason naming an accepted request that was still unapplied.
- At end of run the destination holds no mutations, the request record holds
  the queued outcome, and the terminal record is present.

## Unsafe negative control

One protection is removed: the boundary classifies from its own dispatch result
rather than from a readback, which also removes the sweep that would resolve an
unsettled request (`request-sweep-absent` records its absence). Both are the
same defect seen at two moments, since a boundary that believes it applied the
effect has no reason to run a sweep over requests it considers settled.

Expected violation: the boundary returns `requested-and-applied` while the
destination holds zero mutations, and the run ends with no terminal record for
the request. The oracle detects it by recounting the landed effects from the
destination snapshot and comparing them against the outcome the boundary
returned, and by checking the request record for a terminal entry. The false
success is the classic case of reporting the result of one's own call rather
than the state of the destination (foundational), and its dead-letter form is
what the 67-dispatch backlog measured.

## Pass condition

1. The event log shows the acceptance, then the fault injection, then the
   dropped application leg, in that order by store sequence.
2. The classification happens after the drop and records
   `basis: destination-readback`.
3. The returned outcome is not `requested-and-applied` while the destination
   holds none of the requested effects.
4. The request holds a terminal record at end of run.
5. The unsafe run returns `requested-and-applied` over an empty destination and
   ends with no terminal record, and the oracle flags it. Protected exits 0;
   unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, event kind, request identity, work
  item, session, effect identity).
- The `outcome-classified` event with its basis, its landed set, and its absent
  set, so a wrong outcome can be traced to the readback it was computed from
  rather than to the boundary's belief.
- The request record at end of run: requested effects, acceptance, returned
  outcome, and terminal record.
- The destination snapshot, which is the ground truth every verdict above is
  recounted from.
- The terminal record verbatim, or its documented absence.
- Oracle per-check output for both modes, including the truthfulness and
  terminality halves separately.

## What a pass does not establish

- It says nothing about the request eventually being applied. The protected arm
  ends with the effect still absent and the request recorded as expired, which
  is the correct outcome for this schedule and is not a repair. Recovery is
  [reconciliation](../../patterns/reconciliation.md) territory.
- The protected arm's terminal record is written by a sweep that runs once, at
  a point the script chooses. Whether a real sweep runs often enough, and what
  its deadline should be, is a cadence question this drill does not measure.
- Only two of the six outcomes are exercised end to end here
  (`requested-and-queued` and the unsafe `requested-and-applied`). The
  classifier's other branches are unit-tested against the model rather than
  driven through a fault, and `partially-applied` in particular deserves its own
  drill with a multi-effect request.
- The destination readback is a dictionary lookup in the simulator. Whether a
  real destination can be queried by request identity at all, and whether that
  query is trustworthy enough to classify on, must be verified per destination;
  where it cannot, the honest outcome is
  `unknown-because-observation-failed`, which this drill does not exercise.
- One dropped leg on one held schedule is not a delivery test. A boundary under
  concurrent requests, or one whose acceptance record is itself lost, are
  separate cases (inference: we expect the readback to cover the first, not yet
  demonstrated).
- It does not establish that anything reads the terminal record. The drill
  proves the record exists; whether a scan finds it and acts is
  [promise-oriented-observability](../../patterns/promise-oriented-observability.md)
  territory.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 src/adapters/in_memory/run_drill.py request-accepted-effect-never-applied --mode protected
python3 src/adapters/in_memory/run_drill.py request-accepted-effect-never-applied --mode unsafe
```

In the simulator the work item is `w-1`, the request is `req-1`, and the
requested effect is `eff-1`. The protected run ends holding outcome
`requested-and-queued` and a terminal record with state `expired`; the unsafe
run ends holding outcome `requested-and-applied` and no terminal record. The
destination is empty in both.
