# Recipe: Issue to Pull Request

The most common factory shape: one issue in, one verified pull request out.
This recipe names the patterns that make the shape safe, the contract
sections that declare them, and the drills that test them. The full
normative contract it excerpts is
[examples/issue-to-pr/factory.yaml](../examples/issue-to-pr/factory.yaml),
with its guarantee ledger beside it in
[examples/issue-to-pr/guarantees/](../examples/issue-to-pr/guarantees/).

## Workload

One repository, one logical change, one agent execution lasting minutes to
hours, and two external mutations at the end: a branch push and a
pull-request creation at a code host. Retries are routine, because worker
death, host pressure, and rate limits are routine. Three failure boundaries
define the workload.

First, the executor outlives its orchestrator. A coding agent is a separate
operating-system process; it survives the worker, the attempt, and the
workflow that launched it. A naive retry then produces two capable agents
with the same assignment. In the durability lab, a stable session key made
attempt 2 attach to the surviving executor attempt 1 had launched, one
executor and one effect, where the unsafe arm launched a second process
(local observation,
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).

Second, the code host does not deduplicate creation for you. A retry that
arrives under a fresh identity is a second, independent request as far as
the destination can tell. Duplicate workflow roots for one target are a
live production failure class: two roots created 33 seconds apart, and
again 26 seconds apart, because the dedup query looked in a different store
from where the duplicate lived (local observation, gc-28jm,
gascity2026:docs/design/city-reliability-surface.md).

Third, verified work strands. Publication is a separate promise from
verification, and it breaks silently: roughly 12 closed-but-never-merged
branches per day accumulated before a level-triggered reaper existed
(local observation,
gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md).

## Required patterns

Four patterns carry the recipe:

- [stable-work-identity](../patterns/stable-work-identity.md): one logical
  `work_id` per issue-shaped change, minted once, resolved (never invented)
  by every retry and replacement, so recovery converges on the same work
  instead of forking it.
- [start-or-attach](../patterns/start-or-attach.md): before launching an
  executor, a retried attempt resolves the live session bound to the
  current claim; it attaches when one exists and starts only when none
  does, atomically with the claim.
- [verify-before-publish](../patterns/verify-before-publish.md): the
  verification verdict binds to the immutable artifact identity (a commit
  hash, never a branch name), and publication rechecks ownership, base
  position, and that binding as one atomic condition.
- [reconciliation](../patterns/reconciliation.md): pull-request state and
  base position are reread from the code host on a cadence. The factory's
  belief about the PR is a cache; the destination is the record.

Three more back them up:
[fenced-authority](../patterns/fenced-authority.md) (the publisher rejects
writes from superseded generations, destination-side),
[effect-identity](../patterns/effect-identity.md) with
[durable-intent](../patterns/durable-intent.md) (the create call carries a
stable operation identity, recorded durably before dispatch), and
[explicit-unknown-state](../patterns/explicit-unknown-state.md) (an
unresolved create blocks and escalates instead of guessing either way).

## Contract

The sections of the factory contract that carry this workload:

```yaml
work:
  logical_identity: work_id
  attempt_identity: attempt_id
  session_identity: session_id
  ownership:
    generation: claim_generation     # incremented on every reclaim
    lease_expiry: claim_expires_at   # missed renewal makes owner death detectable
    fence:
      enforced_by: publisher         # destination-side; the caller never fences itself
      operation: compare-and-set

artifacts:
  identity: commit_sha
  verification:
    identity: ci_run_id
    binds_to: commit_sha             # must equal artifacts.identity; never a branch
  publication:
    conditions:
      - current_generation
      - expected_base_revision
      - verification_matches_artifact

effects:
  - name: create_pull_request
    destination: code_host
    effect_identity: pull_request_operation_id   # stable across retries
    retry_contract: reconcile        # the code host does not deduplicate creates
    readback: find_by_head_reference # how a retry finds the prior attempt's PR
    unknown_state_policy: block_and_escalate

reconciliation:
  - fact: pull_request_state
    query: read_current_pull_request
    interval: 5m
  - fact: running_session
    query: resolve_session_for_current_claim
    interval: 1m
```

## Recommended drills

Run each in unsafe mode first; a drill whose unsafe arm passes is testing
nothing.

- [worker-dies-agent-survives](../drills/worker-dies-agent-survives/DRILL.md):
  kills the worker while its agent survives; proves start-or-attach
  produces one executor, not two.
- [stale-writer-completes](../drills/stale-writer-completes/DRILL.md): a
  superseded attempt finishes late; proves the generation fence rejects its
  publication.
- [artifact-changes-after-verification](../drills/artifact-changes-after-verification/DRILL.md):
  swaps the artifact after the verdict; proves the verdict binds to the
  commit identity, not to a mutable reference.
- [effect-commits-ack-is-lost](../drills/effect-commits-ack-is-lost/DRILL.md):
  kills between the PR-create commit and its local record; proves the
  reconcile-plus-readback path finds the existing PR instead of creating a
  second one.
- [repository-base-moves](../drills/repository-base-moves/DRILL.md): lands
  a competing change on the base branch mid-flight; proves publication
  rechecks `expected_base_revision`.

## Observability fields

Every event carries `work_id`; execution events add `generation`,
`attempt_id`, `session_id`, and `base_revision`; the artifact chain adds
`artifact_digest`, `verification_id`, and `publication_id`; the two
external mutations carry `effect_id`. Field definitions are in
[semantic-conventions.md](../observability/semantic-conventions.md).

All six lifecycle promises apply, with `verified_to_published` and
`published_to_acknowledged` the ones this workload breaks most often
(strand class and execute-then-log interval respectively; bounds and alert
conditions in
[promise-latencies.md](../observability/promise-latencies.md)). The
acknowledgement is system-of-record evidence: the commit is an ancestor of
the target branch, not a status field saying so (local observation,
gascity2026:docs/design/software-factory-philosophy.md).

## What stays destination-specific

Guarantees this recipe cannot give you:

- Exactly-once pull-request creation without destination cooperation. The
  recipe gives a stable effect identity and a readback; whether the
  destination accepts an idempotency key, and what the readback query can
  actually see (head reference, title marker, API-issued receipt), is the
  code host's contract, not the factory's.
- The acknowledgement predicate. Ancestor-of-target-branch works for
  merge-based flows; squash and rebase flows need a destination-specific
  equivalence check, since the published commit identity changes at merge.
- Receipt durability. `effect.acknowledged` assumes the destination issued
  something worth recording; some hosts return nothing usable on create.
- Verification quality. The recipe guarantees the verdict is bound to the
  artifact and rechecked at publish; whether the test suite would catch a
  real defect is a property of the tests.
- Review policy. Whether a human must approve the PR, and what their
  approval binds to, is the destination's branch-protection contract.
