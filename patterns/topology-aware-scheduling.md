# Topology-Aware Scheduling

## Problem

A capacity-only scheduler answers one question: is a slot free. A software
factory needs a second question answered before dispatch: are these two jobs
safe to run at the same time. Two changes that each fit comfortably within
capacity can still edit the same generated artifact, migrate the same schema,
or invalidate assumptions the other change depends on. Capacity math sees two
independent units of load; the codebase sees one contested surface.

File overlap is the conflict signal people reach for first, and it is the
weakest one. Two jobs can touch disjoint files and still collide through a
generated artifact, a shared interface, a database schema, a shared test
fixture, a deployment environment, a lock, or a logical invariant that spans
files neither job owns. A scheduler that checks only file paths dispatches
those pairs as if they were independent.

The failure is asymmetric. A missing conflict edge produces unsafe
concurrency: two workers make changes that corrupt each other, and the damage
surfaces later as a broken merge, a duplicated external effect, or a defect
neither worker introduced alone. A false edge produces needless
serialization: work that could safely overlap runs one job at a time, and the
factory pays in throughput. Correctness losses and capacity losses have
different costs, so the edge set has to be managed as a first-class artifact,
not inferred casually at dispatch time.

Related pages: [cross-repo-campaigns](cross-repo-campaigns.md) fans work out
across repositories and inherits every problem on this page per child;
[promise-oriented-observability](promise-oriented-observability.md) covers
how a scheduler's broken promises become visible.

## Observed failure

gc-28jm, open as a P0 in the Gas City reliability surface as of 2026-07-16:
the factory created duplicate workflow roots for single targets. For target
gc-89e, roots gc-5wse and gc-r7c6 were created 33 seconds apart; for target
gc-z9z, roots gc-hlyl and gc-647v were created 26 seconds apart. The
deduplication query that should have prevented the second root looked in a
different store from the one where the duplicate lived, so the safety check
ran and passed while the unsafe pair went out.
(gascity2026:docs/design/city-reliability-surface.md)

The same surface produced a second instance of unsafe concurrency at the
claim layer on 2026-07-28: with a Dolt server capped at 32 connections, 12
fresh claims yielded only 10 distinct winning beads; two beads each reported
being freshly claimed by two distinct actors, via cross-session working-set
overwrite, and the condition persisted under strict concurrency one and a
city-wide file lock.
(gascity2026:docs/recovery/connection-cure-review-02669a98f.md)

Both failures share a shape: the conflict answer was computed somewhere other
than where the conflicting facts were committed.

## Invariant

No two jobs whose effect sets can interact run concurrently unless a conflict
answer, computed against the repository state the workers will actually edit,
asserts they are independent. Every conflict answer carries the revision it
was computed against, and an answer computed against a different revision
than the dispatch target is treated as no answer at all.

## Mechanism

The scheduler consults three topologies before admitting work.

Task topology is declared: each work item carries `depends_on` (prerequisites
that must succeed first) and `conflicts_with` (resources or items that must
not overlap in time), alongside stable node identity, immutable input
versions, an attempt identity, and versioned outputs. The attempt identity
prevents a late response from an expired worker from overwriting a successful
retry; immutable input versions reveal when a ready node was planned against
stale state.

Code topology is derived: given two candidate changes at a specific
repository revision, compute whether their effect sets intersect. File
overlap is one signal among several; the derivation must also consider
generated artifacts, interfaces and their implementations, schemas, shared
test fixtures, deployment environments, locks and leases, external services,
mutable caches, and logical invariants that span disjoint files.

In-flight topology is the change-graph of work currently running: the claims,
uncommitted branches, and provisional head revisions of every active job.
A candidate can conflict not only with the repository as committed but with
what a running peer is about to commit. This third layer is our synthesis; it
is marked as inference in the evidence section and has not been demonstrated
in any system we cite.

```
admit(ready_set, running_set, repo_state):
    for each candidate c in ready_set:
        unsafe if any of:
            explicit conflicts_with edge to a running or co-admitted job
            code-topology intersection with a running or co-admitted job,
                computed at repo_state.revision
            in-flight intersection with an uncommitted change    # inference
    dispatch a maximal set of mutually safe candidates
    stamp each dispatched claim with repo_state.revision
```

The scheduler selects a maximal independent set of nonconflicting work rather
than maximizing simultaneous workers. In the book's scheduling illustration,
six items appeared ready to run together; a pre-dispatch overlap check found
four touched the same adapter file and its test, and the schedule became two
waves, three parallel items and then three sequential in increasing risk
order.

Freshness is part of the answer, not an optimization. A topology answer is a
retrieval artifact, and stale retrieval is an active hazard rather than an
ordinary miss: in the stale-retrieval experiment (Weng et al. 2026, 17
curated examples with changed Python helper signatures), stale-only retrieval
produced outputs incompatible with the current helper signature in 15 of 17
cases for one model and 13 of 17 for another, while current-only retrieval
produced zero incompatible outputs for either model; adding current snippets
to stale ones lowered the incompatible-output rate by 47 to 65 percentage
points. Without retrieval the models tended to fail visibly; with stale
context they produced executable-looking code against the wrong contract. A
conflict answer built from an index of last week's repository has the same
property: it converts uncertainty into a confident, specific, wrong dispatch.
Every topology answer therefore carries two identities: the repository state
it was built from and the state the worker is allowed to edit. When they
differ, the scheduler waits for an index generation built from an accepted
state or fails the dispatch with an explicit freshness error; it never
silently uses the stale answer.

## Where enforcement occurs

Enforcement happens at admission, before the claim is granted, not inside the
worker. A worker asked to check its own conflicts is a worker that can skip
the check.

The conflict and deduplication queries must read the same authoritative store
where claims and roots are committed, inside the same transaction that grants
the claim. gc-28jm is the direct consequence of relaxing this: a dedup query
against a store other than the one holding the duplicate is a check that
passes by construction.

Timeouts fail closed. When a running job exceeds its limit, the attempt fails
visibly, its conflict edges stay in force, and dependent nodes remain blocked
until retry, escalation, or cancellation policy runs. Treating a timeout as
success releases conflicting work without the failed job's edges and destroys
the failure evidence.

The blocked-to-ready transition is mechanical: explicit conditions become
true and the scheduler flips the state. Deciding whether a new task should
exist is a semantic judgment that belongs to a model or a person; the
scheduler validates and executes the result under policy.

## Does not guarantee

- Correctness of the edge set. The mechanism enforces the edges it has; a
  missing conflict edge still dispatches an unsafe pair.
- Exactly-once external effects. Two safely scheduled jobs can still
  duplicate a publication without fencing and idempotency at the destination.
- Semantic merge safety. Two changes independent under every modeled signal
  can still conflict logically at review or integration time.
- Throughput. False edges serialize work that could overlap; the mechanism
  preserves correctness at capacity cost and does not detect over-declared
  edges.
- Completeness of discovery. Work never entered into the task topology is
  never checked against it.
- Protection against bypass. A worker or human that mutates the repository
  without a claim is invisible to admission-time checks.

## Failure drill

The matching drill is [repository-base-moves](../drills/repository-base-moves/):
the repository advances under an in-flight job, the stamped revision on the
claim no longer matches current state, and the scheduler must surface the
staleness rather than let the job publish against a base that moved.

## Evidence

- Task node schema with `depends_on`, `conflicts_with`, attempt identity,
  immutable input versions: agent-era (book ch17, task-eligibility section).
- Conflict signals beyond file overlap (generated artifacts, fixtures,
  schemas, environments, locks, external services, caches, cross-file
  invariants): agent-era (book ch17).
- Missing-edge versus false-edge asymmetry as the central risk: agent-era
  (book ch17).
- Six-ready-items, four-conflicting scheduling illustration: agent-era (book
  ch17; single-author illustration, not a controlled result).
- Timeouts fail closed; retry is a new attempt of the same logical node:
  agent-era (book ch17).
- Stale-retrieval numbers (15/17 and 13/17 incompatible under stale-only,
  zero under current-only, 47 to 65 point reduction, McNemar exact two-sided
  6.1e-5 and 2.4e-4): agent-era (Weng et al. 2026, via book ch12; the values
  establish a difference within the 17 curated examples, not an effect size
  elsewhere).
- Repository-at-revision freshness gate requiring built-from and
  allowed-to-edit identities, atomic index-generation publication, explicit
  freshness failure over silent stale answers: agent-era (book ch12).
- Duplicate workflow roots from a dedup query against the wrong store
  (gc-28jm, roots 33s and 26s apart): local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- Two actors winning one claim through cross-session working-set overwrite,
  2026-07-28: local observation
  (gascity2026:docs/recovery/connection-cure-review-02669a98f.md).
- Scheduling under a conflict graph as independent-set selection:
  foundational (classical scheduling and graph theory).
- In-flight change-graph as a third topology layer: inference (our
  synthesis, not yet demonstrated).

## Limits

The in-flight topology is a proposal. No system in our evidence base computes
conflict answers against the uncommitted state of running peers, and the cost
of keeping such a graph current is unmeasured.

The stale-retrieval numbers are about retrieval into a model's context over
17 curated examples; carrying them to scheduler conflict answers is a
transfer we argue for, not one that has been measured. The direction of the
hazard (confident wrong answers from stale state) is the load-bearing part.

Elaborate scheduling policy needs a contended pool to pay for itself. In the
book's eleven-week replay of 1,286 work items across 22 pools, four policies
landed within 0.1 percent of one another on priority-weighted flow time, and
the only visible improvement concentrated in one pool where eligible work
regularly exceeded capacity. Conflict checking is a correctness mechanism and
justifies itself differently, but any throughput claim for a cleverer
admission order deserves the same skepticism.

Pairwise conflict derivation grows with the square of concurrent work; none
of our sources report the cost of computing code topology at dispatch rates.

## Sources

- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/recovery/connection-cure-review-02669a98f.md
- Book manuscript ch12 (localization funnels, repository indexes, freshness
  checks) and ch17 (agent topology and dynamic task allocation),
  ercabook2026:chapters/
- Book manuscript ch18 (cost-aware fleet scheduling), same tree, for the
  contended-pool caveat.
- Weng et al. 2026, stale-retrieval experiment, as digested in book ch12.
