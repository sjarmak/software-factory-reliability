# Explicit Unknown State

## Problem

Every external call has a window between the destination committing the
effect and the caller durably recording the response. A worker killed inside
that window leaves the system holding a durable record that says the step
started and nothing that says how it ended. The successor process cannot
distinguish "the effect never happened" from "the effect happened and the
acknowledgment died with the worker." This interval cannot be closed by any
amount of durability on the caller's side; it can only be made safe.

Systems that model outcomes as a boolean force this third condition into one
of two wrong answers. Mapping it to FAILED triggers a retry, and if the
effect committed, the retry duplicates it. Mapping it to SUCCEEDED fabricates
history: work the destination never saw gets recorded as done. Both mappings
destroy the one piece of information recovery actually holds, which is that
the outcome is not known.

## Observed failure

The book manuscript's ch08 fault demonstration killed a naive pipeline after
it requested a merge and before it recorded completion. On recovery the
pipeline retried the merge and reported success without alerting anyone; the
duplicate was silent. Under the same kill placement, the guarded variant
requested one merge, emitted one escalation, and marked the workflow failed.
The failed outcome preserved the uncertainty instead of papering over it.
(ercabook2026:chapters/,
ch08)

Gas City's gc-4zf.4 chaos test (2026-07-16) SIGKILLed the Temporal worker
mid-sling. The result was a poisoned pending claim refused forever
(TerminalExecError, retryable:false), an orphan bead, a FAILED workflow, and
zero escalation: an unknown outcome converted into a permanent, silent dead
end. The fix was deployed and re-proven by live injection on 2026-07-17,
with two residues that show how sticky UNKNOWN is: the escalation's bead_ref
is fixed at Propose time before the bead exists, so the orphan is never
named, and the original poisoned record (claimed 2026-07-16 16:43:20Z)
remains pending permanently.
(gascity2026:docs/design/city-reliability-surface.md)

The temporal_projects launch experiments show the ambiguity is real state
rather than a rare corner: the identical launch_pending/no-PID store state
was observed to mean both "no process exists" (pre-exec kill) and "a live
unregistered child is running" (post-exec kill). Neither attach nor replace
follows from the label alone.
(temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md)

## Invariant

Recovery models three outcome states: KNOWN_SUCCEEDED, KNOWN_FAILED, and
UNKNOWN. UNKNOWN is not FAILED, and is never silently mapped to either known
state. An interrupted external effect resolves through exactly one of three
paths: return the previously recorded result; converge on the same external
state; or stop with an explicit escalation that names the unresolved effect
and reaches a decision owner. Silent assumption in either direction is a
defect.

## Mechanism

Guarded mutation: record an intent claim before the irreversible action, and
resolve the claim with the observed result.

```
before effect:  write INTENT(effect_id)              # durable, atomic claim
after effect:   resolve INTENT -> RESULT(receipt)    # durable

recovery, on finding an unresolved INTENT:
  r = query destination by effect_id                 # authoritative read
  receipt found            -> resolve with r          # KNOWN_SUCCEEDED
  authoritative absence    -> KNOWN_FAILED            # retry permitted,
                                                      #   same effect_id
  lookup failed/ambiguous  -> remain UNKNOWN          # stop; escalate with
                                                      #   effect_id + evidence
```

A resolved claim returns its cached result on redelivery. An unresolved claim
found during recovery means the effect may or may not have occurred; guessing
either way is unsafe, since assumed failure repeats the effect and assumed
success loses work.

Two conditions make the recovery query meaningful. The destination must
expose state keyed by the effect identity, which is the contract described in
[effect-identity](effect-identity.md). And an "absent" verdict must come from
an authoritative read that completed; a failed, timed-out, or wrong-store
lookup is LOOKUP_FAILED and leaves the state UNKNOWN (see
[reconciliation](reconciliation.md)).

The escalation path is mandatory equipment. Gas City's two-layer doctrine
makes the work layer at-most-once and fail-closed with a required escalation
hook, on the grounds that fail-closed without escalation self-erases: the
work stops, nothing reports the stop, and the stoppage is discovered later by
archaeology. gc-4zf.4's zero-escalation dead end is the observed instance.
An escalation should carry the outcome at stake, a one-line question, the
options, a recommendation, and why the system cannot decide; the success
criterion is that engineers do not learn to ignore the messages.

## Where enforcement occurs

In the recovery path of the work layer, against the durable store the
replacement worker reads. The three-state model is only real if the schema
can represent it; a status enum holding only success and failure will be lied
to by every recovery. The intent claim must be written in the same durability
domain the recovery reads, before the effect fires, or the window reopens
between claim and effect.

Status fields are not outcome truth even when written in good faith. Gas
City's
rule that "a closed bead is a *status* signal, not an *outcome* signal" puts
the terminal check on artifact movement, not on the work item's state; the
three-state recovery model governs what the status layer may claim.

Escalation enforcement sits above the worker: an UNKNOWN that stops without a
delivered, tracked escalation has failed closed into silence. Gas City's
escalation surface pairs event delivery for speed with a 15-minute scan for
the lost-signal case, which is the reconciliation pattern applied to the
escalation channel itself.

## Does not guarantee

- Safety of the retry after KNOWN_FAILED; repeating the effect still
  requires destination-side effect identity discipline
  ([effect-identity](effect-identity.md)).
- Bounded time to resolution. UNKNOWN can persist indefinitely; Gas City's
  poisoned record remains pending permanently, and an age-gate sweeper was
  deliberately not built.
- That a recorded success reflects reality; a resolved claim inherits
  whatever the destination reported
  ([verify-before-publish](verify-before-publish.md)).
- Delivery of the escalation itself; the escalation channel needs its own
  level-triggered backstop ([reconciliation](reconciliation.md)).
- A decision. Escalation moves the question to an owner; it does not answer
  it.

## Failure drill

[effect-commits-ack-is-lost](../drills/effect-commits-ack-is-lost/): kill the
worker between external commitment and local acknowledgment. The system under
test must either return the prior result, converge on the same external
state, or stop with an escalation naming the effect. Silent duplicated
success and silent assumed failure are both failing outcomes. The naive
control must duplicate the effect, or the kill is not landing inside the
window.

## Evidence

- The execute-then-log gap: the engine's last durable fact says only that
  the step started, and redelivery is correct at-least-once behavior.
  Agent-era (ch08 of the manuscript, building on foundational delivery
  semantics;
  ercabook2026:chapters/).
- The recovery rule (return previous result, converge, or stop with explicit
  unknown-state escalation; never silently assume success or failure).
  Agent-era (same chapter).
- Naive retry merged twice and reported success; the guarded variant under
  the same kill produced one merge, one escalation, one failed workflow.
  Local observation (ch08 fault demonstration, same path).
- Mid-sling SIGKILL produced a poisoned pending claim, an orphan bead, a
  FAILED workflow, and zero escalation; fixed and re-proven by live
  injection 2026-07-17. Local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- One durable label, two physical realities: launch_pending observed to mean
  both no-process and live-unregistered-child in preserved trials. Local
  observation
  (temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md).
- Under that ambiguity, blind attach stalled on a phantom while fenced
  conditional replacement completed, in two preserved trials. Local
  observation
  (temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md).
- Status is not outcome: "A closed bead is a *status* signal, not an
  *outcome* signal." Local observation
  (gascity2026:docs/design/software-factory-philosophy.md).

## Limits

- UNKNOWN accumulates. After the gc-4zf.4 fix, the store still held a
  permanently pending record, and the 53 session-model findings were all
  still open and unreconciled on 2026-08-01, where the warning's
  disappearance was "a visibility change, not evidence of repaired state."
  A three-state model without an inventory and an aging policy trades silent
  corruption for silent backlog. Local observation
  (gascity2026:docs/recovery/session-model-53-reconciliation-2026-08-01.md).
- Escalation capacity is finite. A system that escalates every transient
  trains its owners to ignore the channel, which converts UNKNOWN back into
  silence by a different route.
- The convergence path assumes convergence is definable. For effects with no
  idempotent re-application (sending a message, triggering a deploy), the
  only paths are dedup by receipt or stop-and-escalate.
- The destination query can itself be wrong; a read against stale or
  non-authoritative state resolves UNKNOWN incorrectly with full confidence.
  Inference: the recovery read deserves the same authority discipline as the
  original effect, but the cited experiments did not test degraded-read
  recovery.

## Sources

- ercabook2026:chapters/ (ch08)
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/recovery/session-model-53-reconciliation-2026-08-01.md
- temporallab2026:docs/findings/0002-launch-decision-is-not-process-liveness.md
- temporallab2026:docs/findings/0005-launch-pending-does-not-identify-process-reality.md
- Related patterns: [effect-identity](effect-identity.md),
  [verify-before-publish](verify-before-publish.md),
  [reconciliation](reconciliation.md)
