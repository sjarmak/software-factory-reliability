# Case study: the stale writer

A worker loses its claim, a successor takes over and finishes the work, and
then the original worker publishes its own artifact and records the work
complete. The successor's output is gone, and the completion record says
everything went fine.

This is the failure the essay describes in "Authority has to expire cleanly",
and it is what `make demo` runs.

## Regenerate

```bash
python3 src/adapters/in_memory/run_drill.py stale-writer-completes --mode unsafe
python3 src/adapters/in_memory/run_drill.py stale-writer-completes --mode protected
diff out/evidence/stale-writer-completes-unsafe.json unsafe.json
diff out/evidence/stale-writer-completes-protected.json protected.json
```

Both runs are deterministic. `unsafe.json` and `protected.json` in this
directory are byte-for-byte what those commands produce, and
`tests/test_case_studies.py` asserts it.

## Setup

One work item `w-1`. Worker A holds ownership generation 7 and has prepared
`artifact-g7`. The fault `expire_lease` lands at the barrier
`before-publication`: A's lease expires, ownership advances to generation 8,
and worker B publishes `artifact-g8` and records completion. A then wakes up
and does what it was going to do.

The two arms differ in exactly one place. In `unsafe`, the fence is checked by
the writer before it writes. In `protected`, the fence is checked at the
destination, atomically with the write.

## What happened

| Tick | Unsafe | Protected |
| --- | --- | --- |
| lease expiry | generation 7 loses ownership | generation 7 loses ownership |
| reclaim | generation 8 becomes current | generation 8 becomes current |
| successor publishes | `publish-unfenced-applied` for `artifact-g8` | `publish-accepted` for `artifact-g8` |
| successor completes | `outcome-accepted` | `outcome-accepted` |
| stale publish | `publish-unfenced-applied` for `artifact-g7` | `publish-rejected-stale`, current is 8 |
| stale completion | `outcome-accepted` | `completion-rejected-stale` |

## Oracle

`stale-writer-rejected-at-destination`.

| | Expected | Unsafe observed | Protected observed |
| --- | --- | --- | --- |
| `artifact` | `artifact-g8` | `artifact-g7` | `artifact-g8` |
| `stale_publish` | `publish-rejected-stale` | `publish-unfenced-applied` | `publish-rejected-stale` |
| `stale_completion` | `completion-rejected-stale` | `outcome-accepted` | `completion-rejected-stale` |

Verdicts: `violation` (unsafe, exit 2) and `pass` (protected, exit 0).

## What this does and does not establish

It establishes that a caller-side ownership check does not prevent a stale
write, and that moving the same check to the destination does. Both arms run
against the same simulator with the same fault at the same barrier, so the
enforcement point is the only variable.

It does not establish anything about a particular production system. The
simulator has one destination, no network, and no partial failure. What it
reproduces is the ordering: a check, then a window, then a write. That
ordering is what makes the caller-side variant unsafe in any implementation,
and the corresponding production and lab observations are cited in
[patterns/fenced-authority.md](../../../patterns/fenced-authority.md) with
their sources.
