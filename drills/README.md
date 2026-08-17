# Drills

Thirteen fault drills. Nine run against the in-memory simulator in both unsafe
and protected mode; four are specifications with no executable arm yet.

Every drill states a question, the exact fault placement, an oracle with an
explicit precondition gate, and what the evidence must contain. A drill whose
unsafe arm passes is testing nothing, so the unsafe run is required to exit 2
and the protected run is required to exit 0.

```bash
make drill DRILL=stale-writer-completes MODE=unsafe      # exits 2
make drill DRILL=stale-writer-completes MODE=protected   # exits 0
make drills                                              # all nine, protected
```

Each run writes `out/evidence/<drill>-<mode>.json`: the ordered event log, the
external effects the run left behind, and the oracle's expected and observed
values. Diffing the two modes of one drill is the fastest way to see what the
protection actually changed.

## Executable

| Drill | Fault injected | Oracle | Pattern |
| --- | --- | --- | --- |
| [worker-dies-agent-survives](worker-dies-agent-survives/) | kill the worker process that launched an agent session, before any checkpoint | `one-session-per-claim` | [Stable work identity](../patterns/stable-work-identity.md), [Start-or-attach](../patterns/start-or-attach.md) |
| [stale-writer-completes](stale-writer-completes/) | expire generation 7's lease, advance to 8, then let 7 publish and complete | `stale-writer-rejected-at-destination` | [Fenced authority](../patterns/fenced-authority.md) |
| [effect-commits-ack-is-lost](effect-commits-ack-is-lost/) | kill the worker after the destination commits the effect, before the acknowledgment is recorded | `one-mutation-per-effect-identity` | [Effect identity](../patterns/effect-identity.md), [Durable intent](../patterns/durable-intent.md), [Explicit unknown state](../patterns/explicit-unknown-state.md) |
| [event-is-lost](event-is-lost/) | drop the notification announcing an external state change | `ledger-matches-destination` | [Reconciliation](../patterns/reconciliation.md) |
| [child-completes-after-join](child-completes-after-join/) | a child written off as blocked completes after the join has fired and published | `join-folds-published-children` | [Fan-out and fan-in](../patterns/fan-out-fan-in.md) |
| [request-accepted-effect-never-applied](request-accepted-effect-never-applied/) | the boundary durably accepts the request, then the application leg is dropped | `outcome-matches-postcondition` | [Postcondition-typed outcomes](../patterns/postcondition-typed-outcomes.md) |
| [source-advances-view-answers-anyway](source-advances-view-answers-anyway/) | advance the source, drop the update carrying the new record, then query the view | `view-answers-within-its-lag` | [Lag-bounded reads](../patterns/lag-bounded-reads.md) |
| [state-changes-check-does-not](state-changes-check-does-not/) | move state across the boundary the check claims to police, in both directions | `check-discriminates-and-reads-what-it-did-not-write` | [Falsifiable checks](../patterns/falsifiable-checks.md) |
| [guard-refuses-repair-never-runs](guard-refuses-repair-never-runs/) | drift a resource out of the precondition the operation itself knows how to restore | `precondition-is-repairable` | [Repairable preconditions](../patterns/repairable-preconditions.md) |

## Specifications

These four are written to the same structure (question, fault placement,
oracle, evidence) and have no simulator arm yet. Two of them cover the kit's
two inference-labeled syntheses, which is why no executed result stands behind
them.

| Drill | Fault injected | Pattern |
| --- | --- | --- |
| [artifact-changes-after-verification](artifact-changes-after-verification/) | move the mutable reference to a different artifact after verification passes, then publish | [Verify before publish](../patterns/verify-before-publish.md) |
| [campaign-coverage-drifts](campaign-coverage-drifts/) | a new target appears while the children run, after discovery has already fixed the set | [Cross-repo campaigns](../patterns/cross-repo-campaigns.md) |
| [repository-base-moves](repository-base-moves/) | an independent change publishes a new revision under an in-flight job | [Topology-aware scheduling](../patterns/topology-aware-scheduling.md), [Cross-repo campaigns](../patterns/cross-repo-campaigns.md) |
| [retry-storm](retry-storm/) | a shared dependency fails, then recovers at reduced capacity while every in-flight item becomes retryable at once | [Topology-aware scheduling](../patterns/topology-aware-scheduling.md), [Promise-oriented observability](../patterns/promise-oriented-observability.md) |

## Running a drill against your own factory

The simulator is one adapter. `src/adapters/protocol.schema.json` defines what
any adapter must emit for the oracles to read it: an ordered event log, the
external effects left behind, and the fault placement. An adapter for a real
workflow engine or for your own factory satisfies the same protocol and reuses
these oracles unchanged.

Committed evidence bundles for three of these drills, with the exact command
that regenerates them, are in
[`evidence/case-studies/`](../evidence/case-studies/).
