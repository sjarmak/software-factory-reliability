# Drill: effect-commits-ack-is-lost

## Question

An external effect commits at the destination; the worker dies before recording
completion; retry begins. Does recovery leave exactly one physical effect, with
the retry either receiving the original result, converging on the same external
state, or stopping in an explicit unknown state? A second silent mutation fails
the drill.

This drill descends from the external-effects experiment: all 18 unsafe
trials left two physical effects with different receipts; all 18 protected
trials left one, returning the same receipt to both attempts; "Activity
completion cardinality is not external-effect cardinality" (local observation:
temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md).
The factory design ranks this loss class worst: "a duplicate PR, merge, or
push is worse than a skipped cycle" (local observation:
gascity2026:docs/design/software-factory-philosophy.md).

## Invariant

For one logical effect identity, recovery leaves at most one physical effect at
the destination, and the work eventually exposes either the receipt of that
effect or an explicit unknown-state record that halts further mutation. The
recovery rule admits exactly three outcomes: return the previously recorded
result, converge on the same external state, or stop with an explicit
unknown-state escalation; silently assuming success loses work and silently
assuming failure repeats the effect (local observation: manuscript ch08 under
ercabook2026:chapters/).
The interval cannot be closed by any delivery mechanism (foundational:
at-least-once redelivery after the last durable fact is correct engine
behavior), so safety must come from the effect identity crossing into the
destination.

## Initial state

- Logical claim `claim-1` includes one external effect with a declared stable
  effect identity `claim-1/effect-1`, minted from the logical work, not from
  the attempt (an attempt-derived id defeats deduplication).
- The destination atomically stores the effect identity with the applied effect
  and its receipt, and returns the stored receipt on a repeated request with
  the same identity.
- Worker W1 holds the claim; no effect has been applied; no completion record
  exists.
- Retry is armed: loss of W1 redelivers the work to W2 as attempt 2.

## Fault barrier

Named barrier: `effect-committed-pre-ack`. Events on either side of injection:
before, the destination's durable commit of the effect and its receipt arriving
at W1; after, W2's first action on redelivery. The component killed is W1, after
it has received the destination's response and before it writes the completion
record. This is the third interval of the five-point kill sweep, after external
commitment and before local acknowledgment (local observation: manuscript
ch09).
The boundary is proven by ordering, not sleeps: destination commit sequence <=
barrier arrival <= kill <= attempt-2 start, mirroring the source experiment's
timestamp proof (local observation:
temporallab2026:experiments/external-effects/README.md).

## Injected fault

Kill W1 ungracefully inside the execute-then-log gap: the destination has
committed and the completion record has not been written. The controller
confirms the kill landed (exit status or process-identity reread); an
unconfirmed kill invalidates the trial (local observation: manuscript ch09
worst-case protocol).

## Expected observations

- Attempt 2 begins with the same logical effect identity `claim-1/effect-1`.
- The destination recognizes the identity and returns the original receipt
  without a second application (deduplicate), or a pre-mutation readback
  finds the committed effect and skips the mutation (reconcile), or the run
  halts with an explicit unknown-state record and no further mutation
  (escalate). Any one of the three satisfies the invariant.
- Destination effect count for `claim-1/effect-1` is exactly 1 at end of run.
- If the dedup path ran, attempt 2's receipt equals attempt 1's receipt.
- The completion record eventually written binds the receipt to the logical
  effect identity, so a later retry returns it directly.

## Unsafe negative control

The retry re-executes the mutation blind: either with no effect identity or
with a fresh one derived from the attempt. Expected violation: two physical
effects at the destination with different receipts, while the work layer shows
one accepted outcome, exactly the shape observed in all 18 unsafe source trials
(local observation: finding 0004). The oracle must count destination records
and flag the duplicate; if the unsafe control passes, the harness is
suppressing effects or misplacing the kill (local observation: manuscript ch09
negative-control requirement).

## Pass condition

1. Barrier ordering proven from the log: commit before kill, kill before
   attempt-2 start; kill confirmation present.
2. Destination effect count for the logical effect identity equals 1.
3. One of: receipts of attempt 1 and attempt 2 are equal; or a readback record
   precedes the skipped mutation; or an explicit unknown-state record exists
   and no mutation follows it.
4. No silent path: absence of both a second effect and any of the three
   outcome records is a failure, not a pass.
5. Unsafe mode records two physical effects and the oracle flags it.
   Protected exits 0; unsafe exits 2.

## Evidence to retain

- Raw ordered event log (JSONL: sequence, UTC time, event kind, claim, attempt,
  effect identity, receipt, worker).
- Destination-side effect table snapshot: every applied effect with its stored
  identity and receipt.
- Both attempts' request and response records, joined by effect identity.
- The kill confirmation and barrier report.
- The completion or unknown-state record, and oracle output for both modes.

## What a pass does not establish

- It does not claim external exactly-once execution; the source experiment
  refuses that claim explicitly, and a passing protected arm is evidence about
  this destination protocol, not about the engine (local observation:
  temporallab2026:experiments/external-effects/README.md).
- One destination class, one mechanism. Six destination classes required six
  distinct mechanisms in the source experiment; no single generic mechanism
  covered all six (local observation: finding 0004).
- Reconciliation is weaker than atomic destination deduplication and the
  reconcile conclusions require serialized same-identity callers; concurrent
  check-then-act is not covered (local observation:
  temporallab2026:docs/architecture.md).
- One kill placement; the remaining four intervals of the sweep need their own
  trials, one sweep per effect when a step has several.

## Run

Protected mode exits 0; unsafe mode exits 2. Both write evidence under
out/evidence/.

```
python3 src/adapters/in_memory/run_drill.py effect-commits-ack-is-lost --mode protected
python3 src/adapters/in_memory/run_drill.py effect-commits-ack-is-lost --mode unsafe
```
