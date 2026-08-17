# software-factory-reliability

Contracts, fault drills, and observability conventions for coding-agent fleets.

This kit is for anyone building a software factory: a system that converts
issues, migration plans, and operational evidence into reviewed, tested, landed
code changes, using coding agents as the workers. It is stack-neutral by
design. Whether the factory runs on cron and flat files, a queue and a
database, or a durable workflow engine, the kit gives you a vocabulary to
declare the promises the factory makes, a checker that reviews those promises
against known failure boundaries, and executable fault drills that try to break
them before production does.

## The premise

The workers are probabilistic; the factory still keeps its promises. A coding
agent can produce different output on identical input, misreport its own
progress, and keep writing after it has been replaced. None of that excuses the
factory from being deterministic about three things: which work exists, who may
write, and which effects happened. One production installation states the
constraint directly, "agents are nondeterministic; the factory must not be",
and rebuilds state from durable facts rather than an agent's memory on every
recovery (local observation,
gascity2026:docs/design/software-factory-philosophy.md).

Implementations vary; the failure boundaries recur. A controlled durability lab
ran the same crash placement (kill the worker after the external effect, before
the completion record) against four different integration layers: a direct
model CLI, a sandbox harness, a native agent loop, and a plain activity retry.
Every unsafe arm applied the external effect twice while the engine recorded
one completion; every protected arm applied it once (local observation,
temporallab2026:docs/guarantees.md). In production, two
independently written loops, a workflow-engine cycle and a shell poller, showed
the identical at-most-once-then-abandon shape on the same day, 2026-07-16
(local observation,
gascity2026:docs/design/durable-execution-walkthrough-pr-state-poller.md).
The boundaries belong to the problem; a different stack relocates them.

Work identity survives executors; authority does not. These two properties move
in opposite directions, and conflating them is behind most duplicate-effect
incidents in the evidence base. A work item needs a stable logical identity
that outlives any process, so that a retry converges on the same work instead
of forking it: in the lab, a stable session key made two activity attempts
converge on one external process after a worker was killed (local observation,
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
An executor's authority must do the opposite and die with its generation, so
that a stale writer cannot land effects after replacement: in the same lab,
unsafe systems accepted four obsolete generation-7 actions after generation 9
became current, and fenced systems accepted zero (local observation,
temporallab2026:docs/guarantees.md).

The kit operationalizes this. You describe your factory in a contract file,
`factory-check review` flags the recurring failure boundaries it can see in the
description, the pattern pages explain each boundary with its enforcement
point, and the drills inject the corresponding fault against a simulated
factory so you can watch the unsafe variant fail and the protected variant
hold. Evidence from each drill run is retained, because a guarantee you cannot
show evidence for is a guess.

## Repo map

| Path | Contents |
| --- | --- |
| `schemas/` | JSON Schemas for factory contracts, campaigns, guarantees, and work manifests |
| `cmd/factory-check/` | Python CLI: `init`, `validate`, `review`, `render` |
| `examples/` | A minimal factory, an issue-to-PR pipeline, a long-running agent, a cross-repo migration, and a deliberately unsafe contract |
| `patterns/` | Sixteen pattern pages, one per recurring failure boundary |
| `drills/` | Thirteen fault-drill specifications; nine are executable against the simulator |
| `adapters/` | The adapter protocol and an in-memory simulator that runs drills |
| `recipes/` | Five worked recipes: four factory shapes, plus a recovery path |
| `fixtures/` | Multi-repo and single-repo fixtures used by drills and tests |
| `observability/` | Event conventions, latency expectations, sample events, queries, coverage |
| `evidence/` | The evidence map (per-pattern claims with basis labels) and sources |
| `diagrams/` | Mermaid diagrams: authority planes, identity stack, campaign coverage loop |
| `scripts/` | `prose-check.py` and `schema_check.py`, run by `make check` |
| `tests/` | Test suite for the CLI, adapters, and drills |

## Recipes

Each recipe names the patterns a factory shape requires, the contract sections
that declare them, and the drills that test them.

- [issue-to-pull-request](recipes/issue-to-pull-request.md): one issue in, one
  verified pull request out.
- [background-maintenance](recipes/background-maintenance.md): recurring
  convergent housekeeping, and the case for a timer over an engine.
- [cross-repository-migration](recipes/cross-repository-migration.md): one
  change fanned across many repositories, joined by coverage.
- [human-approved-production-effect](recipes/human-approved-production-effect.md):
  an irreversible mutation gated on a named human decision.
- [factory-recovery](recipes/factory-recovery.md): the entry point when the
  factory is already broken. The other four assume you can describe your
  factory; this one recovers enough truth to write the contract in the first
  place.

## Evidence states

Every guarantee in a factory contract carries one of three evidence states:

- **declared**: the contract names the promise and the boundary it holds at.
  Nothing checks it yet. This is still useful; a named promise can be reviewed
  and falsified, an unnamed one cannot.
- **enforced**: a mechanism at the named boundary rejects violations, and the
  review can point at it. Enforcement at the wrong layer does not count; a
  caller-side check does not enforce a destination-side promise.
- **fault-tested**: an executed drill injected the specific fault the promise
  guards against, the unsafe control violated the oracle, the protected run
  passed, and the evidence from both runs is retained.

Evidence states attach to individual guarantees, and the kit deliberately
defines no aggregate maturity score. A factory with nine fault-tested
guarantees and one declared-only guarantee on its merge path is not ninety
percent safe; it is unsafe at the merge path, and an average conceals exactly
the number that matters. External mutations are the worst loss class in the
production evidence (a duplicate merge is worse than a skipped cycle), so
safety is set by the weakest external-mutation boundary, not by the mean.
Aggregates also redirect effort toward raising a count: the evidence base
includes a health surface where 83 of 106 live checks reported green without
ever examining whether work moved (local observation,
gascity2026:docs/design/city-reliability-surface.md). A single score
invites the same drift at the contract level.

## Sources

Three evidence bases inform the patterns, drills, and conventions here. The
first is a production multi-agent installation whose field failures between
2026-04 and 2026-08 are documented in incident reports, root-cause analyses,
and reliability surveys; it supplies most of the named outages and the
counts. The second is a controlled durability lab that ran preregistered
fault-injection experiments against a workflow engine, always with an unsafe
negative control and preserved raw evidence; it supplies the identity and
fencing results. The third is the experiments in a book manuscript on
engineering reliable coding agents (2026), which supply the guarded-mutation
demonstrations and the scheduling replay. Each factual claim in the kit is
labeled with its basis in `evidence/evidence-map.yaml` (foundational,
agent-era, local observation, or inference), and system names appear only in
citations, never as recommendations. Local observations are cited as
`<bibkey>:<path>`, where the key names one of the three unpublished sources
in `evidence/sources.bib` and the path identifies a document inside it; the
underlying documents are not public, so these citations locate a claim's
origin rather than link to it. Inferences are marked as such; two of the
syntheses in this kit (the in-flight change graph for campaigns and
conflict-graph scheduling for fleets) have not been demonstrated end to end.

## Getting started

Read `QUICKSTART.md` for a first session that takes under an hour: validate
and review the deliberately unsafe example, compare it with a clean contract,
run one drill in both unsafe and protected modes, then initialize a contract
for your own factory. `STYLE.md` covers the writing rules if you contribute
prose; `make check` runs the schema checker, the prose checker, the tests, and
the executable drills.
