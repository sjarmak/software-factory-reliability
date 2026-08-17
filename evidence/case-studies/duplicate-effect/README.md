# Case study: the duplicate effect

A worker commits an external effect. The acknowledgment is lost before the
factory records it. The retry runs, and the destination applies the same
logical mutation a second time.

This is the failure the essay describes in "External effects need their own
contract".

## Regenerate

```bash
python3 src/adapters/in_memory/run_drill.py effect-commits-ack-is-lost --mode unsafe
python3 src/adapters/in_memory/run_drill.py effect-commits-ack-is-lost --mode protected
diff out/evidence/effect-commits-ack-is-lost-unsafe.json unsafe.json
diff out/evidence/effect-commits-ack-is-lost-protected.json protected.json
```

Both runs are deterministic. `unsafe.json` and `protected.json` in this
directory are byte-for-byte what those commands produce, and
`tests/test_case_studies.py` asserts it.

## Setup

One work item `w-1`, one logical effect `eff-1`. Attempt 1 applies the effect
at tick 7 and the destination records `mutation_id: mut-1` with
`receipt: receipt-1`. The fault `drop_event` lands at the barrier
`effect-committed-ack-pending`: the acknowledgment is dropped at tick 11, so
the factory never learns the effect landed. Attempt 2 attaches to the same
session at tick 12 and repeats the effect.

The two arms differ in what the destination does with the second arrival of
`eff-1`. In `unsafe` the destination has no dedup semantics
(`"semantics": "none"`). In `protected` it deduplicates on the effect identity
(`"semantics": "dedup"`).

## What happened

| Tick | Unsafe | Protected |
| --- | --- | --- |
| 7 | `effect-applied`, `mut-1`, `receipt-1` | `effect-applied`, `mut-1`, `receipt-1` |
| 11 | `ack-dropped` for `eff-1` | `ack-dropped` for `eff-1` |
| 12 | `session-attached`, attempt 2 | `session-attached`, attempt 2 |
| 13 | `effect-applied`, `mut-2`, `receipt-2` | `effect-deduplicated`, returns `mut-1` and `receipt-1` |
| 15 | `outcome-accepted` | `outcome-accepted` |

The unsafe run ends with two mutations in `external_effects.mutations`,
`mut-1` and `mut-2`, both carrying `payload-1` and both keyed to `eff-1`. The
protected run ends with one.

Note what is identical across the arms: the same retry, the same attached
session, the same effect identity on the wire, and the same accepted outcome
at the end. The retry is not the defect. The destination's behavior on the
second arrival is.

## Oracle

`one-mutation-per-effect-identity`. Expected 1; unsafe observed 2; protected
observed 1. Verdicts: `violation` (unsafe, exit 2) and `pass` (protected,
exit 0).

## What this does and does not establish

It establishes that carrying a stable effect identity is necessary and not
sufficient. Both arms send the same `eff-1` on both attempts; only the arm
where the destination uses that identity inside its own atomicity domain ends
with one physical effect. A caller that generates a fresh key per attempt
cannot even reach this test, which is what the `IDENT-002` rule looks for in a
contract.

It does not model a destination that is idempotent for some operations and not
others, a receipt store with its own durability limit, or a destination that
returns the stored receipt but has since had the effect reverted. Those are
real and they are why
[patterns/effect-identity.md](../../../patterns/effect-identity.md) admits
three retry contracts (deduplicate, converge, reconcile) rather than one.
