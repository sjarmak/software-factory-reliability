# Case studies

Evidence you can inspect without access to any private repository.

Two kinds of study live here, and the difference is stated on every page.

**Reproducible bundles** commit the unsafe run, the protected run, and the
command that regenerates both. They come out of the in-memory simulator in
this repository, they are deterministic, and `tests/test_case_studies.py`
re-runs each drill and compares the fresh output against the committed file.
If the committed evidence and the code ever disagree, the suite goes red.

**Written case studies** describe a result from a source that is not public.
They state the experimental design, the arm counts, the software versions, and
the outcome, and they mark the raw per-trial evidence as unpublished. A reader
can judge whether the design supports the conclusion; a reader cannot rerun
it from here, and the page says so.

| Case study | Kind | Failure | Pattern |
| --- | --- | --- | --- |
| [stale-writer](stale-writer/) | reproducible | a worker that lost its claim publishes and completes anyway | [Fenced authority](../../patterns/fenced-authority.md) |
| [duplicate-effect](duplicate-effect/) | reproducible | one logical effect applied twice because the acknowledgment was lost | [Effect identity](../../patterns/effect-identity.md) |
| [cancellation](cancellation/) | written | canceling the procedure did not revoke the authority of the process it launched | [Fenced authority](../../patterns/fenced-authority.md) |

## How to read a bundle

Each JSON file is one drill run. The fields that carry the argument:

| Field | What it holds |
| --- | --- |
| `mode` | `unsafe` or `protected` |
| `fault` | what was injected and at which barrier |
| `events` | the ordered event log, one entry per tick |
| `external_effects` | what the destination was left holding |
| `authoritative_state` | claims, sessions, work items, effect ledger at the end |
| `oracle` | the check's name, its expected value, what was observed, and the verdict |

The oracle verdict is `pass` or `violation`. An unsafe run is required to
produce `violation` and a protected run is required to produce `pass`. The
run exits 2 on a violation, which is why `make drill DRILL=... MODE=unsafe`
returning 2 is the drill working rather than the drill failing.

Nothing in these files is hand-edited. Regenerating a bundle is one command,
printed at the top of each page.

The labeling scheme these pages use, and the reason the kit publishes no
aggregate score, are in
[docs/evidence-methodology.md](../../docs/evidence-methodology.md).
