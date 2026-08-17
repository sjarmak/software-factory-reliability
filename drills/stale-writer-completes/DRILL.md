# Drill: stale-writer-completes

## Question

Worker A holds ownership generation 7 of a logical claim. A's lease expires,
ownership advances to generation 8, and worker B publishes the authoritative
artifact. A then returns late and attempts its own publication. Is A's write
rejected at the destination, with the rejection naming generation 7 as stale,
while A's artifact remains inspectable?

This drill descends from two documented results. The fenced arm of the
worker-death experiment rejected a delayed generation-1 effect and its
completion after the generation-2 outcome was accepted (local observation:
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md,
events 15 through 18). The ABA owner-label probe showed both unsafe systems
accepting four obsolete actions from a re-labeled old owner while both fenced
systems accepted zero: "the fence, not the owner label or durability substrate,
supplies safety" (local observation:
temporallab2026:docs/guarantees.md).

## Invariant

A publication is accepted only when the writer's generation equals the current
generation, compared and applied atomically at the destination. A stale
generation's publication attempt is rejected with a durable record naming the
stale generation, and the authoritative artifact for the claim is only ever the
one published by the current generation holder. Lease expiry plus generation
advance is the foundational fencing-token pattern: a lease alone cannot stop a
paused writer that wakes after expiry, so the destination must check the token
(foundational fencing-token literature; the factory design uses the same
split, a fenced lease with generation-checked writes back to the fact store,
local observation:
gascity2026:docs/design/software-factory-philosophy.md).

## Initial state

- Logical claim `claim-1` is owned by worker A under generation 7 with a lease
  expiry recorded alongside the generation.
- A has produced artifact `art-A` locally but has not yet published.
- The destination enforces publication through an atomic compare-and-set on
  (claim id, current generation); it retains rejected-write records.
- Worker B is available to take over ownership when the lease expires.
- The ordered event store assigns sequence numbers, so ordering claims are
  provable from the log rather than from timing.

## Fault barrier

Named barrier: `stale-return-pre-publish`. The writer A is held (execution
suspended by the harness at a declared hold point) after producing `art-A` and
immediately before its publish call. The events on either side of the
injection: before, the durable record that generation 8 is current and B's
artifact `art-B` is published; after, A's publish attempt arriving at the
destination fence. The barrier is checkable: the controller releases A only
after reading back both the generation-8 ownership record and B's publication
record. Ordering is proven by store sequence, never by sleeping (local
observation: temporallab2026:docs/architecture.md, "named
barriers make the dangerous window causal").

## Injected fault

While A is held: expire A's lease, advance ownership to generation 8, assign B,
let B produce and publish `art-B`. Then release A so it completes its work and
attempts publication carrying generation 7. The fault is the late return of a
dispossessed writer, the ABA shape in which an old owner acts after the world
has moved on.

## Expected observations

- A's publish attempt reaches the destination fence carrying generation 7.
- The compare-and-set fails because current generation is 8; no bytes of
  `art-A` replace the authoritative artifact.
- The rejection record names generation 7 as the stale generation and 8 as
  current, and is joined to A's attempt identity.
- `art-A` remains inspectable as a non-authoritative record (for diagnosis and
  for comparing what the stale writer would have shipped).
- The authoritative artifact for `claim-1` is `art-B` before, during, and after
  A's attempt.
- If A also attempts a completion or status write, that write is rejected by
  the same generation check; completion authority and effect authority are the
  same fence here, and the drill exercises both.

## Unsafe negative control

Replace the destination-side fence with a caller-side read-then-write: A reads
the current generation (or skips the read), decides it may publish, then
writes. This is the classic time-of-check to time-of-use gap (foundational).
Expected violation: A's late write replaces `art-B` as the authoritative
artifact, or A's completion is accepted after B's, so the log shows the
authoritative artifact changing identity after the generation-8 publication.
The oracle must detect the overwrite, mirroring the unsafe ABA-probe systems
that accepted obsolete actions.

## Pass condition

1. Barrier report shows generation-8 ownership and `art-B` publication durably
   recorded before A's publish attempt (by store sequence).
2. A's publish attempt is present in the log and rejected; the rejection names
   generation 7 as stale.
3. The authoritative artifact identity for `claim-1` equals `art-B` at every
   event from B's publication to the end of the run.
4. `art-A` exists as an inspectable non-authoritative record.
5. The unsafe mode run shows the authoritative artifact replaced by the stale
   writer and the oracle flags it. Protected exits 0; unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, UTC time, event kind, claim,
  generation, worker, attempt, artifact identity).
- Ownership history: (generation, holder, lease expiry, transition cause) for
  generations 7 and 8.
- The rejection record verbatim, including the named stale generation.
- Authoritative-artifact history: every (sequence, artifact identity) pair.
- Both artifacts' content identities, so overwrite detection is by identity
  comparison, not by inference.
- Oracle per-check output for both modes.

## What a pass does not establish

- It does not establish revocation of credentials A may have copied out of
  band; arbitrary copied credentials are explicitly untested in the source
  experiments (local observation:
  temporallab2026:docs/guarantees.md).
- It covers one held-writer schedule, not a concurrent publish race; the
  atomicity of the adapter's compare-and-set stands in for a real destination's
  transaction, which must be verified per destination (inference: we expect the
  shape to transfer, not yet demonstrated against each real destination).
- It does not establish that generation 8's artifact was correct, only that it
  was the one the fence protected.
- Lease-expiry tuning (how long a wedged writer holds the claim) is a capacity
  and liveness question outside this drill.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 src/adapters/in_memory/run_drill.py stale-writer-completes --mode protected
python3 src/adapters/in_memory/run_drill.py stale-writer-completes --mode unsafe
```
