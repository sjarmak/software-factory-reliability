# Drill: campaign-coverage-drifts

## Question

A campaign runs discovery, finds a set of targets, and starts a child work item
per target. While the children run, a new target appears, or an index refresh
reveals one that discovery missed. The original children all finish. Does
campaign completion rerun discovery against current state and give the new
target a disposition, or does all-scheduled-children-finished close the
campaign with a coverage hole?

The failure shape is documented in the field: "a closed bead is a status
signal, not an outcome signal," and the terminal check is artifact movement,
not child state (local observation:
gascity2026:docs/design/software-factory-philosophy.md). Watchers that
remember instead of querying strand work silently: 37 open workflow markers,
oldest 87 days (local observation:
gascity2026:docs/dr-61j-stranded-molecule-inventory-2026-07-14.md), and
about 12 closed-but-never-merged branches per day before a level-triggered
reaper existed (local observation:
gascity2026:docs/gc-4zf-track-a-reliability-surface-map-2026-07-16.md).

## Invariant

A campaign may close only when a discovery pass executed at close time against
current state yields no target lacking a disposition. Coverage is defined over
the current target set, not the scheduled child set. A disposition is an
explicit per-target record: completed with evidence, skipped with reason,
quarantined, superseded, or scheduled into a new child. Completion is
therefore disposition-based; any all-children-finished completion rule is a
violation by construction (this is the CAMP-001 contract from the review
catalog, stated as a runtime property). The closing check is a query over
current state, not a memory of the start-time discovery ("a reliability layer
is a query, not a memory," local observation:
gascity2026:docs/design/software-factory-philosophy.md).

## Initial state

- A campaign `camp-1` declares a discovery query Q over an external target
  source (for example: all repositories matching a predicate, all call sites
  of a deprecated function).
- At start, Q yields targets {T1, T2, T3}; three children are created, one
  per target, each carrying a stable child identity derived from
  (campaign id, target id), so re-running discovery cannot mint a duplicate
  child for an already-covered target.
- All three children are running; no dispositions exist yet.
- The target source is under drill control: a new target T4 can be made to
  appear, durably and observably, at a chosen point.
- Campaign completion is armed with its declared rule (protected: rerun Q and
  check dispositions; unsafe: count terminal children).

## Fault barrier

Named barrier: `children-terminal-target-added-pre-close`. Events on either
side of injection: before, the durable appearance of T4 in the target source
while at least one original child is still running; after, the campaign's
close evaluation, which runs only when all of T1 through T3's children are
terminal. The faulted component is coverage itself: the world grew after
discovery. The barrier is checkable: the controller invokes close evaluation
only after reading back (a) T4 present in the target source and (b) all three
original children terminal. No timing dependence; both conditions are read
from durable state.

## Injected fault

Introduce T4 into the target source after the initial discovery pass and
before close evaluation. The index-refresh variant injects instead a target
that existed at start but was invisible to the initial query (the index was
stale) and becomes visible on refresh; the invariant treats both identically,
since the closing query sees only current state.

## Expected observations

- Close evaluation reruns Q against current state and receives
  {T1, T2, T3, T4}.
- T1 through T3 have dispositions from their finished children; T4 has none.
- The campaign does not close. It schedules a child for T4 (stable identity
  (camp-1, T4)) or records an explicit non-work disposition (out-of-scope
  with reason, superseded); it closes only on a later evaluation in which
  every current target has a disposition.
- Rerunning discovery creates no duplicate children for T1 through T3; the
  stable child identity deduplicates them. Duplicate roots per target are a
  live failure class (local observation: gc-28jm,
  gascity2026:docs/design/city-reliability-surface.md).
- The close record enumerates the target set it verified and the disposition
  of each.

## Unsafe negative control

Completion counts children: when all scheduled children are terminal, close
the campaign. Expected violation: `camp-1` closes while T4 exists in current
state with no disposition; nothing ever visits T4; the campaign's own records
assert complete coverage that current state contradicts. The oracle must
detect a closed campaign whose close-time target query yields a target with no
disposition.

## Pass condition

1. Barrier report shows T4 durable in the target source and all original
   children terminal before close evaluation ran.
2. Protected mode: the first close evaluation does not close the campaign;
   T4 receives a disposition (child scheduled or explicit non-work record);
   the campaign closes only after a close evaluation at which every target
   returned by the close-time query has a disposition.
3. No duplicate child exists for any of T1 through T3 after the discovery
   rerun.
4. The close record's enumerated target set equals the close-time query
   result, not the start-time result.
5. Unsafe mode: the campaign closes with T4 undispositioned and the oracle
   flags the coverage hole. Protected exits 0; unsafe exits 2.

## Evidence to retain

- Target-source history: every (sequence, target id, appearance event).
- Discovery pass records: query, time, result set, for the start pass and
  every close-time pass.
- Child identity table: (campaign id, target id) -> child id, attempt ids,
  terminal states.
- Disposition records per target, verbatim.
- The close evaluation record: target set checked, per-target disposition,
  decision.
- Oracle output for both modes.

## What a pass does not establish

- Discovery correctness. If Q cannot see a class of target at all, both
  modes miss it identically; a wrong query fails silently in both arms
  (inference: limitation follows from the drill design).
- Freshness after the final close: bounding post-close drift needs a
  recurring level-triggered sweep outside the campaign, a separate cover with
  its own failure domain.
- Child-level safety: each child's own effects, bases, and verdicts are
  covered by the other drills, not this one.
- Scale behavior: one campaign, four targets. Coverage rechecks over very
  large target sets have a cost profile this drill does not measure.

## Run

Specification drill: execute against a real factory through its adapter; no
in-memory implementation exists as of 2026-08-09.
