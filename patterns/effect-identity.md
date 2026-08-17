# Effect Identity

> **Problem** A retry applies the same external mutation a second time.
>
> **Rule** Attempts are unbounded; physical effects per identity are one.
>
> **Required property** Every attempt of one logical effect crosses the
> boundary carrying the same effect identity, and the destination uses
> that identity inside its own atomicity domain to deduplicate, converge,
> or expose state a reconciler can query.
>
> **Wrong** `retry -> fresh request id -> destination sees a new request`
>
> **Right** `retry -> same effect_id -> destination returns the stored receipt`
>
> **See it fail**
>
> - `make drill DRILL=effect-commits-ack-is-lost MODE=unsafe` exits 2
> - `make drill DRILL=effect-commits-ack-is-lost MODE=protected` exits 0
>
> **Checked by** `EFFECT-000`, `EFFECT-001`, `EFFECT-002`, `IDENT-002` in
> the [rule catalog](../docs/contract-reference.md)

## Problem

An orchestrator that redelivers work after a crash is behaving correctly.
Once the engine's last durable fact says a step started and nothing says it
finished, at-least-once redelivery is the only recovery available; the engine
cannot know whether the destination committed the effect. The consequence is
that the destination can receive the same logical operation twice. If each
attempt arrives under a fresh identity (new request ID, new session,
regenerated payload), the destination has no basis for distinguishing a retry
from a second, independent request.

The damage concentrates at external mutation boundaries. A duplicated local
computation is waste; a duplicated merge, push, message, or payment is a
published fact with downstream consumers. Gas City's design doctrine ranks
this loss class explicitly: "External mutations are the worst loss class. A
duplicate PR, merge, or push is worse than a skipped cycle"
(gascity2026:docs/design/software-factory-philosophy.md). The
orchestrator's own records make the duplication invisible: it retried, one
attempt completed, and its history shows a single clean success.

Exactly-once names a contract between the caller and the destination,
established across the boundary. No orchestrator setting supplies it on its
own, because the orchestrator does not participate in the destination's
transaction.

## Observed failure

The temporal_projects external-effects experiment SIGKILLed a Worker in the
window after the destination committed the effect and before the completion
was recorded, with the boundary proven by timestamps rather than sleeps
(effect response <= barrier arrival <= Worker SIGKILL <= attempt 2 start).
All 18 unsafe Go trials left two physical effects with different receipts at
the destination while Temporal recorded one accepted completion. All 18
protected trials, which carried a stable effect ID into a destination-side
mechanism, left one physical effect and returned the same receipt to both
attempts.
(temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md)

The same shape reproduced at other integration layers: nine authenticated
unsafe direct Claude CLI trials each launched two agent sessions and applied
two physical effects while Temporal accepted one outcome; all 12 unsafe
sandbox-harness trials applied twice while all 12 receipt-keyed arms applied
once.
(temporallab2026:docs/findings/0010-direct-claude-activity-retry-duplicates-turns-and-effects.md;
temporallab2026:docs/findings/0009-sandbox-lifecycle-does-not-close-provider-gaps.md)

Gas City gc-28jm (open P0 on the 2026-07-16 surface map) shows the
enforcement-placement variant: duplicate workflow roots for one target,
gc-5wse/gc-r7c6 created 33 seconds apart and gc-hlyl/gc-647v 26 seconds
apart, because the dedup query looked in a different store from where the
duplicate lived. Identity existed; the uniqueness check did not run against
the state that mattered.
(gascity2026:docs/design/city-reliability-surface.md)

## Invariant

Every attempt of one logical effect crosses the boundary carrying the same
effect identity, and the destination uses that identity, inside its own
atomicity domain, to do one of three things: deduplicate (return the stored
receipt), converge (re-application yields the same state), or expose state a
reconciler can query before the operation repeats. The number of attempts is
unbounded; the number of physical effects per effect identity is one.

## Mechanism

```
mint:    effect_id = f(logical_invocation, input_version)   # once, at operation creation
carry:   every attempt sends effect_id unchanged
enforce: destination, atomically, one of:
           dedup      store (effect_id -> receipt) in the same transaction
                      as the effect; on repeat, return the stored receipt
           converge   define the operation so applying it twice yields
                      the same final state
           reconcile  record a correlation marker with the effect; the
                      caller queries the marker by effect_id before repeating
```

Key design rules: a fresh identifier per attempt fails, because the
destination sees new work each time; an over-broad key (for example,
user-scoped) collapses two legitimate operations into one. The stable key
identifies the logical invocation plus enough input and version identity to
distinguish it from its neighbors. Before the effect occurs, the destination
needs an atomic claim (a unique transactional record or compare-and-set)
deciding which caller owns the invocation.

The mechanism is destination-specific. The external-effects experiment needed
six distinct mechanisms for six destination classes: an idempotency key with
a stored response, a correlation-ID pre-query for a non-idempotent API, a
unique key inside the mutation transaction for a database, Git marker
reconciliation, message-ID dedup, and a content-addressed blob plus stable
reference for artifacts. No single generic mechanism covered all six.

Observation identities are not effect identity. Temporal attempt numbers,
Worker processes, model call IDs, vendor session IDs, and physical
destination attempt IDs vary across retries by design; 21 direct Claude
invocations emitted 21 distinct vendor session IDs. The effect ID is the one
identity the application must hold stable while everything around it churns.

## Where enforcement occurs

Inside the destination's atomicity domain, at the moment the effect commits.
The caller's contribution is identity discipline (mint once, carry always);
the destination's contribution is the atomic check-and-apply. A dedup check
that runs anywhere else is advisory. gc-28jm is the boundary case: the query
ran, returned empty, and duplicate roots were minted anyway, because it
consulted a store other than the one holding the earlier root. An empty
result from a non-authoritative lookup is LOOKUP_FAILED, and treating it as
absence mints duplicates (see [reconciliation](reconciliation.md)).

The reconciliation variant (correlation marker pre-query for non-idempotent
destinations) is weaker than atomic dedup: the cited evidence covers it only
when same-ID callers are serialized. Concurrent check-then-act against a
non-idempotent destination is not covered and should be treated as unsafe.

## Does not guarantee

- Safety under concurrent same-ID callers when enforcement is the
  reconciliation variant; only atomic destination-side dedup covers that.
- Writer authorization. A valid effect ID does not fence a stale owner; ABA
  reacquisition is defeated only by the destination atomically comparing the
  current generation and capability, a separate mechanism.
- That the effect is correct, reviewed, or verified; see
  [verify-before-publish](verify-before-publish.md).
- Outcome visibility for an interrupted attempt; the caller still faces the
  three-state recovery problem of
  [explicit-unknown-state](explicit-unknown-state.md).
- Cross-destination atomicity. One logical operation spanning two
  destinations gets two independent dedup domains, with no global
  transaction across them.
- Unbounded retry windows. Dedup state the destination has expired no longer
  deduplicates anything.

## Failure drill

[effect-commits-ack-is-lost](../drills/effect-commits-ack-is-lost/): kill the
worker after the destination commits the effect and before the acknowledgment
is recorded, then confirm recovery leaves one physical effect and returns its
receipt. The unsafe control must leave two effects with distinct receipts; if
it does not, the harness is not placing the kill inside the window it claims
to strike.

## Evidence

- One orchestrator completion can hide two external effects: 18 of 18 unsafe
  trials recorded two physical effects with different receipts under one
  accepted Temporal completion. Local observation
  (temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md).
- Carrying a stable effect ID into a destination-side mechanism converged
  every protected trial to one effect, with the same receipt returned to both
  attempts. Local observation (same finding; the experiment explicitly does
  not claim the orchestrator achieved external exactly-once).
- Six destination classes required six distinct mechanisms. Local observation
  (same finding).
- Duplicate agent sessions and duplicate physical effects on unsafe direct
  CLI relaunch, nine of nine trials. Local observation
  (temporallab2026:docs/findings/0010-direct-claude-activity-retry-duplicates-turns-and-effects.md).
- Dedup queried against the wrong store mints duplicates: gc-28jm duplicate
  workflow roots, 33s and 26s apart. Local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- Idempotency keys at side-effect boundaries make the execute-then-log crash
  window safe on replay. Agent-era (Morling 2025, cited in
  ercabook2026:chapters/,
  ch08).
- An Activity may execute or partially complete more than once even though
  the engine observes one completion; redelivery after worker loss is
  documented at-least-once behavior. Foundational (delivery semantics over
  lossy channels), independently observed after real Worker SIGKILL in the
  guarantee ledger
  (temporallab2026:docs/guarantees.md).

## Limits

- The sequential-retry qualification is load-bearing: Git and non-idempotent
  API conclusions in the evidence require serialized same-ID callers, and
  nothing here extends them to concurrent callers.
- Per-destination engineering cost is real. Each new destination class needs
  its own mechanism designed, and its dedup or marker state has a retention
  policy that must outlive the longest possible retry.
- Message dedup evidence used a stand-in destination; real broker semantics
  are untested in the cited trials.
- A passing protected arm demonstrates the application-plus-destination
  contract for that destination, under that fault placement. It is not a
  general exactly-once certificate. Inference: the contract shape transfers
  to destinations with equivalent atomicity primitives, but each transfer
  needs its own drill.

## Sources

- temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md
- temporallab2026:docs/findings/0009-sandbox-lifecycle-does-not-close-provider-gaps.md
- temporallab2026:docs/findings/0010-direct-claude-activity-retry-duplicates-turns-and-effects.md
- temporallab2026:docs/guarantees.md
- temporallab2026:experiments/external-effects/README.md
- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/design/city-reliability-surface.md
- ercabook2026:chapters/ (ch08)
- Related patterns: [explicit-unknown-state](explicit-unknown-state.md),
  [verify-before-publish](verify-before-publish.md),
  [reconciliation](reconciliation.md)
