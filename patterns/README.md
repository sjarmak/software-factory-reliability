# Patterns

Sixteen recurring failure boundaries. Each page opens with a compact box
(problem, rule, required property, the wrong shape, the right shape, the drill
that falsifies it) and then states four things in full: the invariant, the
enforcement boundary, the falsifying test, and the evidence retained. A page
that cannot name all four does not merge.

If you are reading these for the first time, take the five in
[**Identity and authority**](#identity-and-authority) and
[**External effects**](#external-effects) first. The rest assume them.

## Identity and authority

| Pattern | The rule |
| --- | --- |
| [Stable work identity](stable-work-identity.md) | One logical operation, one `work_id`, for life. |
| [Fenced authority](fenced-authority.md) | Ownership is not authority. |
| [Start-or-attach](start-or-attach.md) | Look for a live session before you launch one. |

## External effects

| Pattern | The rule |
| --- | --- |
| [Effect identity](effect-identity.md) | Attempts are unbounded; physical effects per identity are one. |
| [Durable intent](durable-intent.md) | Write down what you are about to do before you do it. |
| [Explicit unknown state](explicit-unknown-state.md) | UNKNOWN is a third state, and it is not FAILED. |
| [Postcondition-typed outcomes](postcondition-typed-outcomes.md) | The return value classifies the postcondition, read back from the destination. |

## Truth and repair

| Pattern | The rule |
| --- | --- |
| [Reconciliation](reconciliation.md) | Events make it fast; reconciliation makes it true. |
| [Lag-bounded reads](lag-bounded-reads.md) | A view that cannot state its position cannot state a result. |
| [Repairable preconditions](repairable-preconditions.md) | Refuse only what you cannot repair. |
| [Falsifiable checks](falsifiable-checks.md) | A check nobody has watched go red is not evidence. |

## Publication and completion

| Pattern | The rule |
| --- | --- |
| [Verify before publish](verify-before-publish.md) | The verdict binds to an immutable artifact identity, never to a mutable reference. |
| [Fan-out and fan-in](fan-out-fan-in.md) | The join folds durable child records, not whatever the coordinator happened to hold. |
| [Cross-repo campaigns](cross-repo-campaigns.md) | A campaign is complete when discovery finds nothing left, not when the children finish. |

## Fleet and operations

| Pattern | The rule |
| --- | --- |
| [Topology-aware scheduling](topology-aware-scheduling.md) | The scheduler has to read the code, not only the queue. |
| [Promise-oriented observability](promise-oriented-observability.md) | Alert on the promise that was not kept, not on the component that did not complain. |

## How a pattern connects to the rest of the kit

Each pattern names the drill that falsifies it, and most name the
`factory-check` rules that look for it in a contract. The
[rule catalog](../docs/contract-reference.md) is the reverse index: start from
a rule id a review printed and it points back here.

The [drill index](../drills/README.md) lists all thirteen drills, nine of
which run against the in-memory simulator in both unsafe and protected modes.
