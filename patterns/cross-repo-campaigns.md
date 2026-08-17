# Cross-Repo Campaigns

## Problem

Some intents span repositories: migrate every caller of a changed API, rotate
a credential everywhere it appears, upgrade a dependency across a fleet of
services. Executed as one monolithic job, the work fails as one unit. A crash
half way loses the record of which repositories were already done; a retry
either redoes finished work (duplicating external effects) or skips unfinished
work (silently losing coverage). The completion claim of a monolithic job is a
self-report, and self-reports are exactly the evidence class a factory must
not trust.

The opposite decomposition fails differently. A pile of independent tickets
with no joining structure has no answer to the only question the campaign
owner cares about: is the intent satisfied everywhere it applies. Individual
children can each look done while targets that were never discovered, or that
appeared after discovery ran, carry the old API forever.

The pattern in between is one global intent fanned out into many
independently recoverable children, with a join that is computed, not
remembered.

Related pages: each child's dispatch is subject to
[topology-aware-scheduling](topology-aware-scheduling.md); the campaign's
stall modes are detected by
[promise-oriented-observability](promise-oriented-observability.md). The
report format for the join is specified in
[campaign-coverage](../observability/campaign-coverage.md).

## Observed failure

The dr-61j stranded-molecule inventory of 2026-07-15 counted 37 open or
in-progress workflow-marker beads for multi-step work that had fanned out and
never joined. The oldest strand was 87 days old (gc-1920/gc-1927, from a
2026-04-18 codeprobe). A cluster of six `mol-do-work` molecules whose
finalize steps routed to `core.control-dispatcher` was live respawn-loop
fuel, and the safe close path required closing whole molecules at once so no
orphan step could re-route.
(gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md)

The same system measured the gap between child status and child outcome:
before the `work-landing-reaper` existed, closed-but-never-merged branches
stranded at roughly 12 per day. A closed work item asserted completion while
the artifact never reached main.
(gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md)

And gc-na2o showed the record itself lying: metadata written to the wrong
rig left about 40 orphan worktrees (~2.8GB) and 1384 false
`worktree-recorded` audit events; the audit log asserted success that never
happened.
(gascity2026:docs/design/city-reliability-surface.md)

A campaign join built by summing child status fields inherits all three
failures at once.

The discovery pass itself can also be narrower than the population, and that
failure is quieter than any of the above because nothing reports it. One
system runs a scheduled check for externally submitted CI runs held awaiting
approval. Two repositories receive those submissions. The check reads its
target repository from an environment variable with a single-repository
default, exposes no flag to override it, and is scheduled with no environment
block, so the default is what runs. Its own allowlist already names both
repositories, so the second one is permitted and never selected. A sibling
automerge job in the same directory did get a second scheduled instance for
the second repository; this one did not.

The watched repository accumulated eleven blocked contributor pull requests
over four days, which was noticed because the check was also crashing and its
failures were visible. The unwatched repository accumulated 174 held runs
over three weeks, ninety of them at the current head of an open pull request,
across 44 outside contributors. No clock ever started there, because a
discovery pass that is pointed at one of two populations returns a complete
and correct answer about the population it was pointed at. The narrower
failure was found in four days; the wider one was four times larger and had
been running for three weeks when someone happened to query the second
repository by hand.
(gascity2026:bin/fork-pr-approval-gate, orders/fork-pr-approval-gate.toml)

## Invariant

A campaign is complete only when a fresh discovery pass over current
repository state yields no target lacking a durable disposition, where a
disposition is one of: published with verification and acknowledgement
evidence, blocked with a stated reason and a named owner, or exempted with a
stated reason. Every disposition is derived from child-local evidence at the
effect boundary, never from a child's status field.

## Mechanism

The campaign carries a `campaign_id` and an intent expressed as a discovery
predicate that can be re-evaluated at any time against current state.
Discovery produces children; each child is a full work item in its own right:

- its own `work_id` and generation (ownership epoch),
- its own `repository` and `base_revision`, stamped at planning time,
- its own artifact with a content digest,
- its own verification record naming the digest it verified,
- its own fenced publication: the external commitment checks the child's
  generation at the destination, so a stale or superseded child cannot
  publish over a current one.

Children recover independently. A dead worker on one child triggers
reattachment or retry of that child alone; no other child's state is
consulted or disturbed. This is what makes the campaign survivable: the blast
radius of any single failure is one child.

The join is a reduction over child dispositions, recomputed from the durable
record every time it is asked for:

```
coverage(campaign, now):
    targets   = discover(campaign.intent, current_state)   # revision-stamped
    children  = durable record of children ever spawned
    for t in targets:
        disposition(t) = fold over t's child evidence:
            published  if publication committed, artifact digest matches
                       verification, and acknowledgement observed in the
                       system of record
            blocked    if a blocked record with reason and owner is current
            exempted   if an exemption record with reason is current
            stale      if in-flight work's base_revision no longer matches
                       current state
            undispositioned otherwise
    complete iff no target is stale or undispositioned
```

Completion reruns discovery deliberately. Repositories move under a campaign:
new callers of the old API appear after the first discovery pass, and bases
advance under in-flight children. A join computed over the remembered child
list answers a question about the past. The two drift modes have their own
drills: [campaign-coverage-drifts](../drills/campaign-coverage-drifts/) for
targets appearing or changing after discovery, and
[repository-base-moves](../drills/repository-base-moves/) for children
planned against a base that moved.

Published is defined by artifact movement, not by child state. In Gas City's
terms, a closed bead is a status signal, not an outcome signal; the terminal
check is whether the commit is an ancestor of main. The campaign reducer
applies the same rule per child: it reads the system of record, not the
child's claim about itself.

## Where enforcement occurs

Dispositions live in the durable store as events; the reducer is a
level-triggered scan that re-derives coverage from that store on every run.
Nothing about the join is cached between runs, because a reliability layer
that watches by remembering goes wrong silently.

The publication fence is enforced at the destination, in the same transaction
as the external commitment. Application-side fencing is the mechanism with
observed evidence: in the temporal-lab ABA probes over 30 publication pairs
per system, both unsafe systems accepted four obsolete generation-7 actions
after generation 9 became current, and both fenced systems accepted zero; the
fence, not the owner label or the durability substrate, supplied safety.

Blocked children must escalate. A blocked disposition parks the child
fail-closed, and fail-closed without escalation self-erases; the reason and
owner fields exist so the coverage report can route the block to someone who
can act on it, and so silence on a blocked child is visible as a broken
promise rather than as tidy bookkeeping.

## Does not guarantee

- No atomicity across repositories. Partial states are visible for the whole
  life of the campaign; consumers of the fleet see some repositories migrated
  and some not.
- No ordering between children. If the intent requires ordered rollout, that
  is task topology (`depends_on` edges between children), not campaign
  structure.
- No self-resolution of blocked children. The pattern makes blocks visible
  and owned; it does not clear them.
- No completeness beyond the discovery predicate. A target the predicate
  cannot see is never dispositioned; the predicate's quality bounds the
  campaign's coverage claim.
- No continued compatibility after publication. A published child can be
  invalidated by later changes in its repository; completion is a statement
  about the state discovery ran against, at the time it ran.
- No protection against wrong exemptions. An exemption with a plausible
  reason hides a real target exactly as effectively as a correct one.

## Failure drill

Two drills exercise this pattern's failure modes:
[campaign-coverage-drifts](../drills/campaign-coverage-drifts/), where
targets change under a completed-looking campaign, and
[repository-base-moves](../drills/repository-base-moves/), where a child's
planned base revision is invalidated mid-flight.

## Evidence

- 37 stranded workflow markers, oldest 87 days, respawn-loop fuel,
  whole-molecule close requirement: local observation
  (gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md).
- Closed-but-never-merged strand rate of ~12 branches/day before a reaper
  existed: local observation
  (gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md).
- Closed state is status, not outcome; terminal check is artifact ancestry:
  local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- 1384 false audit events asserting success that never happened (gc-na2o):
  local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- Re-derive from the source of record every tick; watching by remembering
  goes wrong silently: local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- Fenced publication rejecting obsolete-generation actions (4 accepted by
  each unsafe arm, 0 by each fenced arm, 30 pairs per probe): local
  observation (temporallab2026:docs/guarantees.md).
- Generation-checked writes and fenced leases as the execution model: local
  observation (gascity2026:docs/design/software-factory-philosophy.md,
  ADR-0012 summary).
- Fail-closed without escalation self-erases: local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- Evidence without revision identity is stale, not current: agent-era (book
  ch12 portable claim).
- Completion evidence must originate at the effect boundary, not the agent's
  self-report: agent-era (book ch08).
- A discovery pass pinned to one of two eligible repositories by an
  unoverridden default: 174 held runs over three weeks in the unselected
  repository, 90 at a current pull-request head across 44 outside
  contributors, against 11 over four days in the selected one: local
  observation (gascity2026:bin/fork-pr-approval-gate,
  orders/fork-pr-approval-gate.toml).
- The campaign as a fold over dispositions with discovery rerun at
  completion: inference (our synthesis; the components above are observed,
  the composition is not).

## Limits

No system in our evidence base runs this pattern end to end. Gas City
supplies the failure record and the status-versus-outcome discipline; the
temporal lab supplies the fencing and generation evidence at the child level;
the composed campaign shape is designed from those parts, not measured as a
whole.

Discovery cost is unaddressed. Rerunning discovery at every completion check
is the correctness move, and for large fleets it is also the expensive move;
none of our sources measure discovery latency at fleet scale or the staleness
window an infrequent discovery cadence leaves open.

The reduction assumes child evidence is queryable in one place. Campaigns
whose children live in stores with different consistency properties reopen
the gc-28jm class of failure (the check reads a different store than the one
holding the fact), this time at the join.

## Sources

- gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md
- gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md
- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- temporallab2026:docs/guarantees.md
- gascity2026:bin/fork-pr-approval-gate,
  gascity2026:orders/fork-pr-approval-gate.toml
- Book manuscript ch08 and ch12,
  ercabook2026:chapters/
- Worked coverage example:
  [campaign-coverage](../observability/campaign-coverage.md) against the
  fixtures/multi-repo-api-migration fixture.
