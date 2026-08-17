# Recipe: Cross-Repository Migration

One intent applied everywhere it holds: retire an API, rotate a credential,
upgrade a dependency across an estate. The recipe treats the campaign as a
fan-out of independently recoverable children joined by a computed coverage
reduction, never by a memory of what was scheduled. The reference contract
is
[examples/cross-repo-migration/factory.yaml](../../examples/cross-repo-migration/factory.yaml)
with its target ledger in
[examples/cross-repo-migration/campaign.yaml](../../examples/cross-repo-migration/campaign.yaml);
a worked end state with manifests and a coverage report is the
[multi-repo-api-migration fixture](../../examples/fixtures/multi-repo-api-migration/).

## Workload

The campaign runs for days to weeks while the estate keeps changing
underneath it. Targets are discovered by a query over the code estate, one
child work item is minted per target, each child is an ordinary
issue-to-pull-request execution, and the campaign's own question is
coverage: is the intent satisfied at every target that currently exists.

The failure boundaries are the ones child-level safety cannot see. Work
strands between child completion and campaign accounting: a production
inventory found 37 open workflow-marker beads, the oldest stranded 87 days
(local observation,
gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md).
Rediscovery mints duplicate children when child identity is not derived
from the target (local observation, gc-28jm,
gascity2026:docs/design/city-reliability-surface.md). And a
completion rule that counts finished children closes over a coverage hole
whenever a target appears after kickoff, because a closed child is a status
signal, not an outcome signal (local observation,
gascity2026:docs/design/software-factory-philosophy.md).

Evidence about staleness raises the stakes on discovery versioning: under
stale-only retrieval, two models produced current-state-incompatible
output in 15 of 17 and 13 of 17 curated cases, and zero under current-only
retrieval (agent-era, book ch12, citing Weng et al. 2026). A target list
without a snapshot identity is the campaign-scale version of that hazard.

## Required patterns

- [cross-repo-campaigns](../../patterns/cross-repo-campaigns.md): the spine.
  One global intent, many independently recoverable children, and a join
  that is computed from current state at every evaluation.
- [stable-work-identity](../../patterns/stable-work-identity.md): child
  identity is derived from (campaign id, target id), so rerunning
  discovery resolves to existing children instead of minting duplicates.
- [topology-aware-scheduling](../../patterns/topology-aware-scheduling.md):
  the campaign needs the task graph and the code topology together.
  Dependency edges order children (a client library lands before the tools
  built on it); conflict edges keep two children from editing one
  contested surface at once.
- [reconciliation](../../patterns/reconciliation.md): discovery reruns on a
  cadence against the latest estate snapshot, and again at close time. New
  matches become open targets; completion is judged over the current set,
  never the kickoff set.
- [verify-before-publish](../../patterns/verify-before-publish.md) and
  [fenced-authority](../../patterns/fenced-authority.md): inherited per child
  from the [issue-to-pull-request](issue-to-pull-request.md) recipe. A
  campaign is only as safe as its least-safe child publisher.

## Contract

The sections that distinguish a campaign factory from a single-repo one:

```yaml
code_estate:
  canonical_identity: repository@revision   # targets are pinned, not named
  topology_provider: code-impact-adapter
  in_flight_manifest: work-manifests/       # one manifest per in-flight child

reconciliation:
  # Discovery drift: the estate changes while the campaign runs, so the
  # target set is re-derived on a cadence and completion is judged over
  # the current set.
  - fact: campaign_target_set
    query: rerun_discovery_against_latest_snapshot
    interval: 1d

scheduling:
  fairness:
    # repository sits in the hierarchy so a campaign fanning out over many
    # repositories cannot monopolize any one repository's review
    # bandwidth; tenant sits above it so one campaign cannot monopolize
    # the factory.
    levels: [tenant, repository, priority]

campaigns:
  completion:
    all_current_targets_have_disposition:
      - published
      - exempted
      - blocked_with_owner
```

The target ledger itself validates against
[schemas/campaign.schema.json](../../schemas/campaign.schema.json): every
target pins a repository and revision, a blocked target must name an
owner, and the discovery query is kept verbatim so it can be rerun.

## Recommended drills

- [campaign-coverage-drifts](../../drills/campaign-coverage-drifts/DRILL.md):
  the defining drill. A target appears after kickoff while children run;
  proves close evaluation reruns discovery and refuses to close over the
  undispositioned target, and that rediscovery mints no duplicate
  children.
- [repository-base-moves](../../drills/repository-base-moves/DRILL.md):
  campaigns race normal development in every target repository; proves a
  child's prepared change is invalidated when its base moves.
- [stale-writer-completes](../../drills/stale-writer-completes/DRILL.md): with
  many children in flight, reclaim races multiply; proves the per-child
  generation fence.
- [retry-storm](../../drills/retry-storm/DRILL.md): a provider outage fails
  many children at once, and fan-out amplifies the correlated recovery;
  proves the recovery lane stays inside its budget.

## Observability fields

`campaign_id` is required on every event of a campaign child, which is
what makes the coverage reduction computable from the event store
([semantic-conventions.md](../observability/semantic-conventions.md)).
Exemptions enter as `work.reconciled` with `action: exempted` and a
mandatory `reason`. Blocked children carry `reason` and `owner`, so no
blocked target is anonymous.

The campaign-level surface is the coverage report
([campaign-coverage.md](../observability/campaign-coverage.md)):
recomputed from a fresh discovery pass on every run, never cached, with
counts that must sum (published + blocked + exempted + stale +
undispositioned = current targets). The
[multi-repo-api-migration fixture](../../examples/fixtures/multi-repo-api-migration/)
contains a full worked report.

## What stays destination-specific

Guarantees this recipe cannot give you:

- Discovery correctness. The coverage loop proves the closing check
  queries current state; it cannot prove the query is right. A query that
  cannot see a class of target misses it identically in protected and
  unsafe modes, and fails silently (inference, stated as a limitation in
  the coverage drill).
- Index freshness. The recipe pins targets to repository@revision;
  whether the code index that answers the discovery query is current, and
  how stale its snapshot may lag the estate, is the index provider's
  contract.
- Cross-repository atomicity. There is no transaction spanning
  repositories. The campaign converges target by target, and the estate
  holds mixed states (some repositories migrated, some not) for the whole
  campaign duration; whether that intermediate state is tolerable is a
  property of the change being made, not of the campaign machinery.
- Per-repository merge policy. Each target repository keeps its own
  review requirements, protected branches, and merge queue; the campaign
  inherits every one of them per child.
- Exemption governance. The machinery records exemptions with reasons and
  review dates; who may grant one, and what happens when a review date
  passes, is policy the destination organization must own.
