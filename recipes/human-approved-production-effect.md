# Recipe: Human-Approved Production Effect

A factory action whose blast radius reaches production: a deploy, a data
migration, a credential rotation, a destructive cleanup. The recipe's core
claim comes from the gate evidence: a gate that cannot change execution
records assent and nothing more (agent-era, book ch16). Everything here
exists to make the approval mechanical, so the effect cannot fire without
the recorded decision, and to keep the ordinary path too weak to cause the
damage the gate guards.

## Workload

One irreversible or high-blast-radius external mutation, prepared by
automated work but gated on a named human decision. The preparation
(reading production state, drafting the change, verifying it) is frequent
and must stay cheap; the effect itself is rare and must stay hard.

The two anchoring failures both come from authority, not intent. In a
reported 2026 incident, an agent deleted a production database in nine
seconds, and backups were unrecoverable because one credential reached
both the live database and the backup store; a prompt warning changes
instructions to the same capable process, not the reachable capability
set (agent-era, book ch07). And the manuscript author's own
human-approval queue failed its audit: scripts failed open when a
required component was missing, and command construction reached
execution without validation, so the system could report that approval
existed while an action bypassed the person (agent-era, book ch16).

The production doctrine this recipe encodes: approvals bind to the exact
artifact hash and revision, not to intent, and the factory's job is to
make each human decision small, late, and evidence-backed (local
observation,
gascity2026:docs/design/software-factory-philosophy.md).

## Required patterns

- [fenced-authority](../patterns/fenced-authority.md): the ordinary
  identity is read-only. The mutation runs under a separately escalated
  identity scoped to one named capability, one bounded target, and a
  limited lifetime, and no identity reachable by the agent may touch
  backups or retention (agent-era, book ch07). The boundary is tested in
  both directions: the ordinary identity's prohibited write must be
  denied, and the escalated identity's adjacent destructive actions must
  still fail.
- [durable-intent](../patterns/durable-intent.md): the proposed effect is
  a typed record (operation, target, parameters, artifact digest) written
  durably before anything is sent. The approval attaches to this record;
  an effect with no resolved intent record cannot be dispatched.
- [effect-identity](../patterns/effect-identity.md): the effect carries
  one stable identity across every retry, so an approved effect can land
  at most once and a redelivery is recognizable at the destination.
- [explicit-unknown-state](../patterns/explicit-unknown-state.md): a kill
  inside the execute-then-log interval leaves the outcome unknown, and
  for a production effect both guesses are unacceptable; recovery blocks
  and escalates with the intent record as evidence (agent-era, book
  ch08).
- [verify-before-publish](../patterns/verify-before-publish.md): the
  approval is a verification-shaped fact. It binds to the artifact
  digest, and publication rechecks the binding, so an artifact swapped
  after approval invalidates the approval rather than inheriting it.
- [reconciliation](../patterns/reconciliation.md): destination state is
  reread through an external readback, and a level-triggered scan sweeps
  pending escalations, because an approval request delivered as an event
  can drop silently like any other event.

## Contract

```yaml
authorities:
  policy: { system: approval-queue, record: approvals }

artifacts:
  identity: artifact_digest
  verification:
    identity: verification_run_id
    binds_to: artifact_digest
  publication:
    conditions:
      - current_generation
      - verification_matches_artifact
      - approval_matches_artifact    # a recorded approval naming this exact
                                     # digest; absent or mismatched blocks

effects:
  - name: apply_production_change
    destination: production_system
    effect_identity: change_operation_id   # stable across retries
    retry_contract: reconcile
    readback: read_change_state_from_destination
    unknown_state_policy: block_and_escalate

reconciliation:
  - fact: applied_change_state
    query: read_change_state_from_destination
    interval: 5m
  - fact: pending_approvals
    query: select_escalations_awaiting_decision
    interval: 15m
```

The `approval_matches_artifact` condition is what makes the gate
mechanical rather than attentional: execution state blocks until the
condition holds, which is a different failure class from evidence plus a
required decision, and agent memory is neither, because a remembered
instruction has no independent causal path to enforcement (agent-era,
book ch16).

## Recommended drills

- [effect-commits-ack-is-lost](../drills/effect-commits-ack-is-lost/DRILL.md):
  kills inside the interval around the production effect; proves recovery
  blocks on the unresolved intent record instead of assuming either
  outcome.
- [artifact-changes-after-verification](../drills/artifact-changes-after-verification/DRILL.md):
  swaps the artifact after approval; proves publication rejects the
  approval-to-digest mismatch.
- [stale-writer-completes](../drills/stale-writer-completes/DRILL.md): an
  expired executor holds a valid-looking approval; proves the generation
  fence keeps a superseded writer from firing an approved effect.
- [event-is-lost](../drills/event-is-lost/DRILL.md): drops the approval
  request or the decision notification; proves the pending-approvals scan
  surfaces the stalled escalation instead of stranding it.

One test the drills do not cover, from the gate-audit evidence: run the
gate as a failure experiment. Remove each dependency of the approval path
in a contained environment; a rejected action must fail to execute
through every path, and a modified action must require renewed review
(agent-era, book ch16). This is a test of your gate wiring, not of the
factory contract, which is why no generic drill can stand in for it.

## Observability fields

The escalation is a `work.blocked` event with mandatory `reason` and
`owner`, because a fail-closed state with no escalation path self-erases
(local observation,
gascity2026:docs/design/software-factory-philosophy.md). The effect
chain is `effect.dispatched`, `effect.committed`, `effect.acknowledged`,
and the terminal `work.acknowledged` must cite readback evidence from the
destination, not the executor's report
([semantic-conventions.md](../observability/semantic-conventions.md)).

Measure the gate itself: blocked-work visibility latency (how long until
the named owner sees the escalation) and decision latency. The
escalation content follows the wake-up contract: the outcome at stake, a
one-line question, the options, the recommendation, and why the system
cannot decide, with the success criterion that engineers do not learn to
ignore the messages (agent-era, book ch15).

## What stays destination-specific

Guarantees this recipe cannot give you:

- Destination-side enforcement. The publication condition blocks the
  factory's own path; whether the destination also refuses unapproved
  changes (a gateway checking the approval record) is the destination's
  contract, and without it the factory's publisher is the only line.
- Reversibility. The recipe treats the effect as irreversible; where the
  destination offers rollback, its semantics (full, partial, windowed)
  change what an unknown outcome costs, and only the destination defines
  them. Irreversible action classes keep a permanent approval floor that
  routine success does not remove (agent-era, book ch16).
- Readback fidelity. `read_change_state_from_destination` can only
  observe what the destination exposes; a destination whose API cannot
  answer "did operation X apply" forces a weaker reconciliation than the
  contract implies.
- Approver authority. Mapping the named `owner` to a person with the
  causal power, evidence access, and role preparation to decide is an
  organizational property; effective-oversight conditions for it exist
  in the evidence base but carry no strong item (agent-era, book ch16,
  citing Sterz et al.).
- Idempotency support. Whether the destination stores the effect
  identity and returns the prior result on redelivery, or the factory
  must reconcile by readback alone, is the destination's capability.
