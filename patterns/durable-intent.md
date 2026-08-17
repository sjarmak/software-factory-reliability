# Durable Intent

> **Problem** A worker dies between committing an external effect and
> recording that it happened.
>
> **Rule** Write down what you are about to do before you do it.
>
> **Required property** No irreversible external attempt begins without a
> durable intent record naming work_id, generation, effect_id, and
> destination, and at recovery every intent record yields a returned
> result, a converged state, or an explicit unknown-state escalation.
>
> **Wrong** `call the destination -> record the result`
>
> **Right** `record the intent -> call the destination -> record the result -> recovery reads the intent`
>
> **See it fail**
>
> - `make drill DRILL=effect-commits-ack-is-lost MODE=unsafe` exits 2
> - `make drill DRILL=effect-commits-ack-is-lost MODE=protected` exits 0
>
> **Checked by** `EFFECT-004` in the [rule
> catalog](../docs/contract-reference.md)

## Problem

Crossing an external boundary (merge, push, pull-request creation, message
publish, payment) opens an interval that no engine can close: the destination
may accept and commit the operation while the caller dies before recording
the response. The caller's last durable fact then says only that the step
started. An at-least-once delivery layer correctly redelivers the step, and a
naive retry repeats the effect. Duplicated external mutations are the worst
loss class for a software factory; a duplicate merge or push is worse than a
skipped cycle.

The interval cannot be eliminated, so it must be made safe, and safety is a
property of what the system knows at recovery time. If nothing durable names
the attempt, recovery cannot distinguish "never sent" from "sent and
committed" from "sent and rejected", and any policy it picks fabricates
history in some fraction of runs. Recording intent before the attempt is
what converts the unknowable case into a decidable one.

## Observed failure

The manuscript's fault demonstration: a naive pipeline was killed after
requesting a merge and before recording completion. On recovery it retried
the merge and reported success without alerting anyone; the duplicate was
invisible from inside the pipeline. Under the same kill placement, the
guarded variant (intent claim recorded before the irreversible action)
requested one merge, emitted one escalation, and marked the workflow failed;
the failed outcome preserved uncertainty instead of fabricating history.
(ercabook2026:chapters/,
ch08)

Gas City's chaos test gc-4zf.4 (2026-07-16) shows what happens when the
recovery side of the contract is missing: SIGKILL of the execution worker
mid-dispatch produced a poisoned pending claim refused forever
(TerminalExecError, retryable:false), an orphan work item, a FAILED workflow,
and zero escalation. A residue remains by design: the original poisoned
record, claimed 2026-07-16 16:43:20Z, is still pending and always will be,
and the escalation's record reference was fixed at propose time before the
record existed, so the orphan was never named.
(gascity2026:docs/design/city-reliability-surface.md)

The inverse failure also occurred: gc-na2o wrote outcome-shaped records at
intent time. A dispatch component wrote metadata to the wrong rig, leaving
about 40 orphan worktrees and 1384 false worktree-recorded audit events; the
audit log asserted success that never happened.
(gascity2026:docs/design/city-reliability-surface.md)

## Invariant

No irreversible external attempt begins without a durable intent record
naming the effect (work_id, generation, effect_id, destination, request
identity). At recovery, every intent record yields exactly one of three
actions: return the recorded result, converge on the observed destination
state, or stop with an explicit unknown-state escalation. Silently assuming
success or failure is never among the outcomes.

## Mechanism

Three steps, two of them transactions in the store of record:

```
1. tx: write intent { work_id, gen, effect_id, destination,
                      request_digest, state = INTENDED }
2.     perform the effect, presenting effect_id (idempotency key,
       correlation id, unique transactional key, or marker) to the
       destination
3. tx: resolve intent { state = RESOLVED, receipt, observed_result }
```

Recovery walks a decision table instead of guessing:

```
intent state   destination query result              action
------------   -----------------------------------   ------------------------
RESOLVED       (none needed)                         return recorded receipt
INTENDED       effect found by effect_id             resolve with found
                                                     receipt; do not repeat
INTENDED       effect definitively absent            retry under the same
                                                     effect_id
INTENDED       destination cannot answer             STOP; escalate
                                                     unknown-state to a human
absent         (none needed)                         nothing was attempted;
                                                     start at step 1
```

Rules that make the table sound:

- The intent record is written before the effect, write-ahead. An intent
  written after the attempt protects nothing, because the crash window sits
  between the attempt and the write.
- The record is intent-shaped, never outcome-shaped. gc-na2o's 1384 false
  audit events are what outcome-shaped intent looks like after a partial
  failure.
- Resolution evidence originates at the effect boundary (destination
  receipt, commit identifier, independent state read), never from the
  executor's self-report, which is produced from the same context that may
  already be wrong.
- The escalation must name a record that exists at escalation time; fixing
  the reference before the record exists leaves the orphan unnamed
  (gc-4zf residue 9.5a).
- The unresolved-intent scan is level-triggered: a reconciler re-derives the
  set of aged INTENDED records from the store every tick, rather than
  trusting any in-memory pending list. A reliability layer is a query, not a
  memory.

The intent record makes recovery decidable; it does not by itself
deduplicate. Dedup requires the effect_id to cross into the destination and
be honored there, and the required mechanism is destination-specific: the
lab's external-effects experiment needed six distinct mechanisms for six
destination classes (idempotency key, correlation-ID pre-query, unique key
inside the mutation transaction, marker reconciliation for version control,
message-ID dedup, content-addressed blob plus stable reference). See
[Stable Work Identity](./stable-work-identity.md) for how effect_id is
derived and [Fenced Authority](./fenced-authority.md) for keeping stale
writers out of the same window.

## Where enforcement occurs

- In the store of record: intent write and resolution are transactions
  keyed by effect_id; the unresolved-intent scan runs there.
- At the effect boundary in code: a single choke point through which every
  irreversible external call passes, so that no unguarded write path exists.
  Gas City's audit singles out its merge and push steps as the lane where
  this guard belongs, one of them still an unguarded external write as of
  2026-08-02 (gascity2026:docs/design/temporal-decision.md).
- At the destination: deduplication or reconciliation state keyed on
  effect_id; the destination must atomically apply-and-record or expose
  state a reconciler can query.
- At the escalation surface: the unknown-state stop must reach a named
  human with a reference to the live intent record; fail-closed without
  escalation self-erases
  (gascity2026:docs/design/software-factory-philosophy.md).

## Does not guarantee

- Does not close the crash window; the interval between effect and
  resolution remains, and the record only makes it recoverable.
- Does not deduplicate at the destination by itself; a destination that
  ignores effect_id still applies duplicates.
- Does not decide the unknown case; it routes that case to a human with
  evidence.
- Does not verify that the applied effect matches the intent semantically;
  it establishes cardinality and attribution, not correctness of content.
- Does not protect reversible or cheap deterministic work economically;
  those steps should simply re-run rather than each carrying a durable
  boundary.

## Failure drill

[../drills/effect-commits-ack-is-lost/](../drills/effect-commits-ack-is-lost/)

## Evidence

- All 18 unsafe external-effects trials recorded two physical effects with
  different receipts despite one engine completion; all 18 protected trials
  left one physical effect and returned the same receipt to both attempts.
  Basis: local observation
  (temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md;
  temporallab2026:docs/guarantees.md).
- "Activity completion cardinality is not external-effect cardinality."
  Basis: local observation
  (temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md).
- Six destination classes required six distinct dedup or reconciliation
  mechanisms; no single generic mechanism covered all six. Basis: local
  observation
  (temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md).
- Guarded-mutation kill demonstration: naive variant duplicated the merge
  and reported success; guarded variant produced one merge, one escalation,
  one preserved-uncertainty failure. Basis: local observation
  (ercabook2026:chapters/,
  ch08).
- The three-outcome recovery rule (recorded result, convergence, or
  explicit unknown-state escalation; never silent assumption). Basis: local
  observation
  (ercabook2026:chapters/,
  ch08).
- The five-point kill sweep around an external commitment, including a
  deliberate kill between external commitment and local acknowledgment for
  every effect a step performs. Basis: local observation
  (ercabook2026:chapters/,
  ch09).
- gc-4zf.4: worker SIGKILL mid-dispatch yielded a permanently pending
  poisoned claim, an orphan record, and zero escalation; fixed and re-proven
  by live injection on 2026-07-17. Basis: local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- gc-na2o: 1384 false success-shaped audit events for worktrees that were
  never created. Basis: local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- Idempotency keys at side-effect boundaries make the execute-then-log
  crash window safe on replay. Basis: agent-era (Morling 2025, cited in
  ercabook2026:chapters/,
  ch08).
- Engine vendor documentation states an Activity may execute or partially
  complete more than once even though the engine observes one completion;
  the lab cites this as motivation and supplies its own live runs as the
  evidence. Basis: agent-era
  (temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md).
- Write-ahead logging and the transactional outbox: record the intended
  change durably before applying it, so recovery replays or reconciles
  instead of guessing. Basis: foundational.

## Limits

- Reconciliation is weaker than atomic destination deduplication; the
  non-idempotent API and version-control results hold only under serialized
  same-ID callers, and concurrent check-then-act is explicitly not covered
  (temporallab2026:docs/architecture.md).
- The artifact-publication variant has an unresolved boundary: the
  blob-written, reference-missing window was not exercised
  (temporallab2026:experiments/external-effects/README.md).
- Unresolved intents are a poison class of their own. Gas City deliberately
  declined to build an age-gate sweeper, leaving one record permanently
  pending; a deployment of this pattern must either age-escalate INTENDED
  records or accept that residue knowingly. Basis: local observation plus
  inference (gascity2026:docs/design/city-reliability-surface.md).
- Each intent costs a durable write per external effect. The pattern pays
  for itself at irreversible or costly boundaries (model calls, merges,
  publishes); wrapping cheap deterministic steps in it is overhead without
  a matching risk. Basis: local observation
  (ercabook2026:chapters/,
  ch08).

## Sources

- temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md
- temporallab2026:docs/guarantees.md
- temporallab2026:docs/architecture.md
- temporallab2026:experiments/external-effects/README.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/design/temporal-decision.md
- ercabook2026:chapters/ (ch08, ch09)
