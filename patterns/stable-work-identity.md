# Stable Work Identity

> **Problem** A retry forks the work instead of converging on it.
>
> **Rule** One logical operation, one work_id, for life.
>
> **Required property** A retry may create a new attempt_id under the same
> work_id and must never create a second work_id. Every durable record
> joins back to the work_id, and observational identifiers (PID, vendor
> session id, engine attempt number) stay diagnostics, never join keys.
>
> **Wrong** `retry -> new work record keyed on the attempt`
>
> **Right** `retry -> new attempt_id under the same work_id -> every record joins on work_id`
>
> **See it fail**
>
> - `make drill DRILL=worker-dies-agent-survives MODE=unsafe` exits 2
> - `make drill DRILL=worker-dies-agent-survives MODE=protected` exits 0
>
> **Checked by** `IDENT-001` in the [rule
> catalog](../docs/contract-reference.md)

## Problem

A software factory runs nondeterministic executors against durable work, and
every layer of the system mints its own identifiers. The work queue has a task
id, the workflow engine has workflow, run, and attempt ids, the operating
system has PIDs, the model vendor has session ids, the version-control system
has commit hashes. When a retry, a worker replacement, or an orchestrator
restart occurs, some component must decide whether two identifiers name the
same logical operation or two different ones. Without a declared identity
stack, that decision gets made ad hoc at each call site, against whatever
identifier happens to be in scope.

Each wrong answer produces one of two losses. Treating one operation as two
creates duplicate external work: two workflow roots, two agent sessions, two
pull requests for one task. Treating two operations as one silently drops
work: a retry is dismissed as already done, or two legitimate requests
collapse into a single effect. Both losses are invisible at the layer that
caused them, because each layer's own identifier remains internally
consistent.

The stakes are highest at retry. A retry is the normal recovery action of
every durable-execution and queueing layer, so the identity question "is this
the same operation" is asked most often exactly when the system is already
degraded.

## Observed failure

Gas City issue gc-28jm (open P0 as of the 2026-07-16 reliability surface
audit): one target bead acquired duplicate workflow roots. For target gc-89e,
roots gc-5wse and gc-r7c6 were created 33 seconds apart; for gc-z9z, roots
gc-hlyl and gc-647v were created 26 seconds apart. The dedup query that was
supposed to answer "does a root already exist for this target" looked in a
different store from the one where the duplicate lived. The logical operation
had no single authoritative identity record, so the existence check was
answered against the wrong table and both creations won.
(gascity2026:docs/design/city-reliability-surface.md)

The complementary failure appears when an incidental identifier is mistaken
for work identity. In the temporal_projects lab, 21 direct model-CLI
invocations emitted 21 distinct vendor session ids; a system keying on vendor
session identity would classify every retry as a brand-new operation, and the
lab's finding states directly that transcript identity does not authorize a
workspace writer and is not a destination transaction.
(temporallab2026:docs/findings/0010-direct-claude-activity-retry-duplicates-turns-and-effects.md)

## Invariant

One logical operation carries exactly one work_id for its entire lifetime. A
retry may create a new attempt_id under that work_id; it must never create a
second work_id. Every durable record the operation produces (claims, leases,
effects, artifacts, publications) joins back to the work_id. Observational
identifiers (PID, vendor session id, engine attempt number, physical
destination attempt id) are recorded as diagnostics and are never used as
join keys for correctness decisions.

## Mechanism

The identity stack, from most to least durable:

```
work_id         the logical operation; minted once, at admission, by the
                store of record; unique-keyed there
generation      ownership epoch within a work_id; increments only on
                ownership transfer (new claimant, fenced replacement)
attempt_id      one delivery or execution try; increments on every retry;
                always subordinate to (work_id, generation)
session_id      one executor session (agent process, terminal session,
                sandbox); registered under (work_id, generation)
effect_id       one intended external mutation; derived deterministically
                from (work_id, step, input version); stable across attempts
artifact_id     content address of a produced artifact; joined to the
                effect_id that produced it
publication_id  one outbound message or publication; a stable id the
                destination can deduplicate on
```

Derivation rules that keep the stack coherent:

- work_id is minted exactly once, by an atomic insert with a uniqueness
  constraint in the store of record. The executor never mints it, because an
  executor cannot know whether it is the first executor.
- The existence check and the creation are one atomic operation in one
  store. A dedup query against any other store is the gc-28jm failure shape.
- generation changes only when ownership changes. A retry that reattaches to
  a live session keeps the current generation; a fenced replacement takes a
  new one (see [Fenced Authority](./fenced-authority.md) and
  [Start-or-Attach](./start-or-attach.md)).
- effect_id is computed from stable inputs, so attempt 1 and attempt 2
  present the same effect_id to the destination. A fresh identifier per
  attempt makes the destination see new work every retry; an identifier
  scoped only to the user collapses two legitimate operations into one.
- PIDs, engine attempt numbers, and vendor session ids are attached to
  attempt records as diagnostic fields. They answer "what happened", never
  "is this the same operation".

The retry rule in one line: retry moves attempt_id, reattachment keeps
generation, replacement moves generation, and nothing moves work_id.

## Where enforcement occurs

- In the store of record: a uniqueness constraint on work_id, and on
  (work_id, generation) for ownership rows. This is the only place the
  question "new operation or existing one" can be decided, because it is the
  only place where the check and the insert are one transaction.
- At each destination: dedup on effect_id or publication_id, applied
  atomically by the destination (see [Durable Intent](./durable-intent.md)
  for the intent side and [Fenced Authority](./fenced-authority.md) for the
  authority side).
- Not in the executor. Caller-side memory of "I already created this" does
  not survive process death, and the executor's own identifiers (session,
  PID) are the ones most likely to have changed across the retry.

## Does not guarantee

- Does not prevent a stale owner from writing; that requires generation
  fencing at the destination ([Fenced Authority](./fenced-authority.md)).
- Does not prevent two executors from being launched for one work_id; that
  requires an atomic start-or-attach decision
  ([Start-or-Attach](./start-or-attach.md)).
- Does not make external effects exactly-once. It supplies the key a
  destination can deduplicate on; the destination must actually do so.
- Does not survive a dedup query pointed at the wrong store. The identity is
  exactly as authoritative as the single store that mints it.
- Does not indicate liveness or progress. A work_id with a registered
  session says nothing about whether work is moving.

## Failure drill

[../drills/worker-dies-agent-survives/](../drills/worker-dies-agent-survives/)

## Evidence

- The identity classes that must appear in experiment evidence are named as
  workflow/run/Activity attempt, logical operation/session, ownership
  generation/token, Worker, process, effect, and artifact identities. Basis:
  local observation
  (temporallab2026:docs/experiment-methodology.md).
- "A PID is diagnostic data, not durable identity"; the agent simulator is a
  separate OS process that can outlive the Worker that launched it. Basis:
  local observation (temporallab2026:docs/architecture.md).
- 21 direct model invocations emitted 21 distinct vendor session ids;
  transcript identity does not authorize a workspace writer. Basis: local
  observation
  (temporallab2026:docs/findings/0010-direct-claude-activity-retry-duplicates-turns-and-effects.md).
- "A stable application session key made two task attempts converge on one
  external process." Basis: local observation
  (temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
- gc-28jm duplicate workflow roots, 33 and 26 seconds apart, with the dedup
  query in a different store from the duplicate. Basis: local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- The ledger of record owns work identity, dependencies, priority, claims,
  generations, and fencing tokens as a single authority. Basis: local
  observation (gascity2026:docs/design/software-factory-philosophy.md).
- A task-node schema whose attempt identity prevents a late response from an
  expired worker from overwriting a successful retry, with immutable input
  versions and versioned outputs. Basis: local observation
  (ercabook2026:chapters/,
  ch17).
- Idempotency keys at side-effect boundaries make the execute-then-log crash
  window safe on replay, with the stable key identifying the logical
  invocation (example key run-42/step-9). Basis: agent-era (Morling 2025,
  cited in
  ercabook2026:chapters/
  ch08).
- Monotonic epoch or fencing-token identifiers as the standard defense
  against stale clients acting under an old ownership label. Basis:
  foundational.

## Limits

- The stack assumes one store of record can mint work_id atomically. A
  factory whose work records span several stores reintroduces the gc-28jm
  class unless exactly one store is designated the mint and every existence
  check runs there. Basis: local observation plus inference.
- Deterministic effect_id derivation depends on stable step naming across
  deploys. A refactor that renames a step silently changes effect identity
  and defeats destination dedup for in-flight work. Basis: inference.
- The supporting experiments are single-host and single-cluster. Federating
  work identity across organizations or across independently minted stores
  is untested here. Basis: inference.

## Sources

- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- temporallab2026:docs/experiment-methodology.md
- temporallab2026:docs/architecture.md
- temporallab2026:docs/findings/0001-worker-death-surviving-agent.md
- temporallab2026:docs/findings/0010-direct-claude-activity-retry-duplicates-turns-and-effects.md
- ercabook2026:chapters/ (ch08, ch17)
