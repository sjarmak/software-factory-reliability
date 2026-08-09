# Drill: repository-base-moves

## Question

Work is planned and produced against repository state R1. While the worker
runs, an independent change publishes R2. The worker then attempts publication
from its R1 base. Is the stale base detected, and does the result get an
explicit disposition (rebase, replan, quarantine, or reject) rather than a
silent publish?

The hazard is documented at both ends of the pipeline. At planning time, stale
context is worse than no context: under stale-only retrieval, models produced
current-state-incompatible outputs in 15 of 17 and 13 of 17 curated cases, and
zero under current-only retrieval; retrieval converted uncertainty into a
specific but incompatible implementation (agent-era: Weng et al. 2026,
summarized in manuscript ch12 under
ercabook2026:chapters/).
At publication time, the terminal check in the factory is artifact
movement against the real repository, not any status field (local observation:
gascity2026:docs/design/software-factory-philosophy.md).

## Invariant

Publication succeeds only when the base state identity the work declares
equals the destination's current state identity at the moment of an atomic
reference update; equality is evaluated by the destination, not by the caller.
On mismatch, the result receives exactly one explicit disposition: rebase onto
R2 followed by re-verification against the new base, replan from current
state, quarantine for inspection, or reject with reason. A silent publish over
R2, and equally a silent drop of the work, both violate the invariant. The
enforcement shape is the foundational compare-and-swap on a reference
(foundational); the freshness rule that makes the declared base mandatory is
"evidence without revision identity is stale, not current" (local observation:
manuscript ch12).

## Initial state

- Repository state R1 is current at the destination; its state identity
  `id(R1)` is readable.
- Worker W plans `claim-1` against R1 and records `planned_base = id(R1)` as
  an immutable input of the claim (input versions recorded at planning reveal
  when a ready item was planned against stale state; local observation:
  manuscript ch17 task-node schema).
- W produces artifact `art-W` deterministically derived from R1.
- An independent actor holds change C2, unrelated to `claim-1` at the file
  level or overlapping with it (the drill runs both variants; the invariant
  is the same).
- The destination supports atomic reference update conditioned on the
  expected old value, and retains reference history.

## Fault barrier

Named barrier: `base-advanced-pre-publish`. Events on either side of
injection: before, the durable publication of R2 (C2 applied to R1) at the
destination; after, W's publication attempt declaring `planned_base = id(R1)`.
The component faulted is the repository base, not a process. The barrier is
checkable: the controller lets W's publish proceed only after reading the
destination's reference history showing current = `id(R2)` and
`id(R2) != id(R1)`. Ordering is proven by the destination's reference history
sequence, never by timing.

## Injected fault

Publish R2 at the destination after W has planned and produced `art-W` and
before W's publication attempt. Nothing about W is perturbed; the world moved
while W worked, which is the normal condition of a busy repository, made
deterministic here.

## Expected observations

- W's publication attempt carries `planned_base = id(R1)`.
- The conditional reference update fails because current is `id(R2)`, or the
  freshness gate refuses before the attempt reaches the destination; either
  detection point satisfies the invariant, and the destination-side check
  must exist even when the gate also checks (the gate is an optimization,
  the destination condition is the fence).
- A disposition record is written naming the stale base `id(R1)`, the current
  base `id(R2)`, and the chosen disposition.
- If the disposition is rebase: the rebased artifact is re-verified against
  `id(R2)` before any new publication attempt; the old verdict does not carry
  across bases (this couples to the artifact-changes-after-verification
  drill: a verdict binds to an identity, and the rebased artifact is a new
  identity).
- R2's content is intact at the destination throughout; no observation shows
  C2's change reverted or overwritten.

## Unsafe negative control

Publish with an unconditional reference update (last-write-wins), or gate
publication on a mergeability check alone with no base-identity comparison.
Expected violation: the R1-based artifact publishes over R2; destination
history shows C2's change absent or reverted after W's publication, with no
disposition record anywhere. The oracle must detect either the content
regression (C2 missing from the published state) or the acceptance of a
publication whose declared base differs from the pre-publication current state
with no disposition recorded.

## Pass condition

1. Barrier report shows `id(R2)` current at the destination before W's
   attempt, from reference history sequence.
2. Protected mode: the R1-based publication does not become the published
   state; exactly one disposition record exists and names both identities;
   if rebase was chosen, a re-verification record against `id(R2)` precedes
   the successful publication.
3. C2's change is present in the final published state.
4. No silent outcome: absence of both a publication and a disposition record
   is a failure.
5. Unsafe mode: the stale-base publication succeeds, C2 regresses, and the
   oracle flags it. Protected exits 0; unsafe exits 2.

## Evidence to retain

- Destination reference history: every (sequence, old identity, new identity,
  actor) transition.
- The claim record with its immutable `planned_base`.
- The publication attempt record: declared base, artifact identity, outcome.
- The disposition record verbatim.
- Content-level proof for C2's presence or absence in the final state (the
  file or hunk itself, not a status flag).
- Re-verification record for the rebased artifact, when that path runs.

## What a pass does not establish

- Textual rebase success is not semantic compatibility. A rebase can apply
  cleanly while breaking an invariant spanning disjoint files (local
  observation: manuscript ch17, missing edges versus false edges).
  Re-verification narrows this, to the strength of the verification suite and
  no further.
- Nothing about planning-time retrieval freshness; a worker can plan against
  a stale index long before publication, and that boundary needs its own gate
  at the retrieval layer (local observation: manuscript ch12).
- One writer and one mover; concurrent publication races among many writers
  are exercised only to the extent the destination's conditional update is
  actually atomic, which must be verified per real destination (inference).

## Run

Specification drill: execute against a real factory through its adapter; no
in-memory implementation exists as of 2026-08-09.
