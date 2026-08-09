# Effect boundary review checklist

Scope: every external mutation the factory can perform, reviewed one effect at
a time: identity, retry contract, readback, and unknown state. Each question
carries the review rule id it maps to or the drill it corresponds to.
Automated findings carry rule id, severity, a message stating the defect, and
a one-line remediation hint; a run exits 1 on any FAIL, or on any WARN under
--strict, else 0.

1. Is there an effects section at all? Enumerate every external mutation the
   system can perform (pushes, merges, publications, messages, provider
   calls, records written to shared stores); an effect not declared is an
   effect not reviewed. [EFFECT-000]
2. Does each declared effect carry an `effect_identity`? [EFFECT-001]
3. Is each effect identity stable across retries: derived from the logical
   work and the effect's place in it, never from the attempt? [IDENT-002]
4. Does each effect declare a `retry_contract`, and is it one of
   `deduplicate`, `converge`, or `reconcile`? [EFFECT-002]
5. Does the chosen contract match the destination's actual capability? A
   `deduplicate` contract requires the destination to atomically store the
   effect identity with the applied effect and return the stored receipt on
   repeat; a destination that merely "usually ignores duplicates" does not
   qualify. [EFFECT-002]
6. Does each effect declare an `unknown_state_policy`, and is it neither
   `assume_success` nor `assume_failure`? Assumed success loses work; assumed
   failure repeats the effect. The policy must name what happens instead:
   halt, escalate, or reconcile. [EFFECT-003]
7. For every effect with `retry_contract: reconcile`: is a readback declared,
   and does the readback query the destination by the stable correlation
   identity before any repeat mutation? Reconcile without readback is a blind
   retry wearing a different label. [EFFECT-004]
8. For reconcile contracts: what serializes same-identity callers? The
   reconcile result holds only when two workers cannot run the
   check-then-act sequence concurrently for one effect identity. Name the
   serialization mechanism or downgrade the claim. [EFFECT-002]
9. Walk the kill placed between destination commit and local acknowledgment
   for this effect: does the retry return the original result, converge on
   the same state, or halt in explicit unknown state? A second silent
   mutation is the failure this whole review exists to prevent.
   [drill effect-commits-ack-is-lost]
10. Is the destination receipt stored in the completion record, bound to the
    effect identity, so a later retry can return it without touching the
    destination again? [EFFECT-002]
11. Does every effect destination have a reconciliation entry whose fact
    covers it, so a lost acknowledgment is eventually repaired by a
    level-triggered read of the destination rather than by event replay?
    [RECON-001]
12. Which single layer owns retries for this effect, and are all other
    layers (client library, harness, transport, engine) explicitly
    configured to zero retries for it? Multiplicative retry layering fires
    hardest during an outage. [drill retry-storm]
13. For each effect: what evidence would show it was applied twice? If the
    reviewer cannot name the record that exposes a duplicate, the drill
    oracle cannot either, and neither can production monitoring.
    [drill effect-commits-ack-is-lost]
14. Are irreversible effects (merge, publish, delete, external send)
    separated from reversible ones in the declaration, and do the
    irreversible ones carry the strictest contract available at their
    destination? [EFFECT-002]
