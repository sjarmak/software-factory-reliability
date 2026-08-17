# Case study: cancellation is not revocation

Canceling a workflow stops the workflow. It does not stop the process the
workflow launched. In a preregistered experiment, every run that relied on
engine cancellation alone saw the detached agent commit an external effect and
record an accepted outcome after the workflow had already closed as canceled.

This is a written case study. The raw per-trial evidence lives in a private
repository and is not published here. What follows is the design, the counts,
and the versions, so the conclusion can be judged on the design rather than
taken on trust. The reproducible bundles in
[stale-writer](../stale-writer/) and [duplicate-effect](../duplicate-effect/)
are the ones you can rerun yourself.

## The question

A long-running agent session is launched by a durable workflow. The operator
cancels the workflow. Does canceling the procedure remove the authority of the
process that procedure started?

## Design

Four scenarios, run under both of the engine's activity cancellation policies
(wait for cancellation, and do not wait), three trials per cell. That is 24
valid final live trials. The arms split into two groups by what performs the
revocation:

| Arm | Runs | Revocation mechanism |
| --- | --- | --- |
| Engine-only control | 6 | cancel the workflow; nothing else |
| Application-revoked | 18 | cancel the workflow, and the destination refuses writes from the revoked generation |

Each trial ends by asking the destination two questions, not by asking the
engine: how many external effects carry this work's effect identity, and how
many accepted outcomes were recorded. Asking the engine would only report what
the engine believes.

## Result

All 6 engine-only control runs canceled the workflow, the workflow closed as
canceled, and the still-live detached agent afterwards committed one external
effect and one accepted outcome anyway.

All 18 application-revoked runs accepted nothing after revocation: zero
effects, zero outcomes.

The split is clean and it falls exactly on the revocation mechanism, not on
the cancellation policy. Both activity cancellation policies appear in both
groups.

The ordering inside the safe arm matters as much as the mechanism. Revocation
commits first, in one work-store transaction that records the cancellation,
revokes the active generation, rejects later registration, progress, effect,
and completion, and prevents replacement. Stopping the process is a separate
best-effort operation afterwards, aimed at an exact identity (session,
generation, owner hash, PID, process-start identity, process group). Six of
the safe runs killed the first worker with `SIGKILL` before cancellation and a
second worker performed the disconnected cleanup; six more froze the entire
process group before cancellation, so revocation committed and stop delivery
was recorded while the acknowledgment was still absent, and the tree
acknowledged and exited only after an exact resume. In every one of those
runs, nothing was accepted after revocation, including while the process was
demonstrably still alive.

## Versions

Go 1.25.12, Temporal Go SDK 1.47.0, Temporal CLI 1.8.0, Temporal Server
1.31.2, Linux amd64, evidence protocol `cancellation-v1`.

Naming the stack is a fact about where the result came from. The conclusion is
not about this engine: an engine's cancellation signal changes what the engine
does, and a process holding open connections, a warm workspace, and valid
credentials is not listening to it. Any stack that separates the orchestrator
from the executor has the same gap.

## What this establishes

Cancellation and revocation are different operations, and only one of them is
enforced at the destination. A factory that treats "the workflow is canceled"
as "the work has stopped" is reading a status field, which is the same class
of error as reading a worker's self-reported completion.

The corresponding pattern is
[fenced authority](../../../patterns/fenced-authority.md): the generation the
executor writes under must be compared at the destination, atomically with the
write, and cancellation must advance that generation rather than merely close
a workflow.

## What is not published here

The 24 per-trial evidence files, the harness source, and the destination
ledger dumps. The claim above is labeled `local observation` in
[`evidence/evidence-map.yaml`](../../evidence-map.yaml) and cited as
`temporallab2026:docs/findings/0006-cancellation-requires-application-revocation.md`.
A reader without access to that repository can check the design, the arm
counts, and the internal consistency of the numbers, and can reproduce the
same shape of failure with the stale-writer bundle, which is public and
runnable. That is the limit, and it is stated here rather than in a footnote.
