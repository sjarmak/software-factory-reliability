# Drill: artifact-changes-after-verification

## Question

Artifact A passes verification. Before publication, the mutable reference the
pipeline uses (a branch head, a "latest" pointer, a ref name) moves to artifact
B. Publication reads the reference. Can B ride A's verdict into production, or
does the publication gate detect that the thing being published is not the
thing that was verified?

This drill descends from the factory's policy rule that approvals bind to the
exact artifact hash and revision, not to intent (local observation:
gascity2026:docs/design/software-factory-philosophy.md), and from the
artifact identity contract established in the external-effects work:
content-addressed blob plus stable reference, atomically published and
content-validated (local observation:
temporallab2026:docs/findings/0004-one-temporal-completion-can-hide-two-effects.md).
The freshness principle generalizes it: evidence without revision identity is
stale, not current (local observation:
ercabook2026:chapters/ ch12).

## Invariant

A verification verdict binds to an immutable artifact identity (a content
address or equivalent). Publication is permitted only when the identity of the
artifact actually being published equals the identity named in the verdict and
the publisher holds the current ownership generation. A verdict never transfers
across identities: artifact B can only be published on the strength of a
verdict naming B. This is the VERIFY-001 and VERIFY-002 contract from the
review catalog, stated as a runtime property.

## Initial state

- Logical claim `claim-1` has produced artifact A with content identity
  `id(A)`.
- A verification run has executed against `id(A)` and recorded a passing
  verdict; the verdict record names `id(A)` explicitly, not a branch, tag, or
  work-item id.
- A mutable reference `ref` currently resolves to A; the publication pipeline
  is configured to publish "whatever `ref` resolves to."
- Artifact B exists with content identity `id(B)`, `id(B) != id(A)`, and no
  verdict names `id(B)`.
- The publication gate is armed with its declared conditions: current
  generation held, and verification identity matches publication identity.

## Fault barrier

Named barrier: `ref-moved-post-verdict`. Events on either side of injection:
before, the durable verdict record naming `id(A)`; after, the publication
gate's read of `ref`. The component faulted is the reference, not a process.
The barrier is checkable: the controller proceeds to publication only after
reading back both the verdict record (still naming `id(A)`) and the reference
resolution (now yielding `id(B)`). The dangerous window is defined by these
two reads, not by elapsed time.

## Injected fault

Move `ref` from A to B after the verdict is recorded and before publication
reads the reference. The move is legitimate-looking: an ordinary update by
another actor, not a corruption. That is what makes the fault dangerous; every
individual step is locally normal, and only the binding check sees the swap.

## Expected observations

- The publication gate re-resolves the artifact identity at publication time
  and obtains `id(B)`.
- The gate compares `id(B)` against the verdict's bound identity `id(A)`,
  finds the mismatch, and refuses publication.
- The refusal record names both identities and the verdict it consulted.
- Nothing is published; the claim returns to verification (B may be verified
  on its own merits and published under a verdict naming `id(B)`), or the
  claim is quarantined for inspection.
- If the pipeline retries publication without a new verdict, the gate refuses
  again; the refusal is stable, not a race that a retry can slip through.

## Unsafe negative control

Bind the verdict to anything mutable or indirect: the work-item id, the branch
name, or a "verification passed" flag on the claim. Publication then checks
"has this claim passed verification" and reads `ref`. Expected violation: B is
published carrying A's verdict; the published artifact identity differs from
the verified identity while the publication succeeds and the audit trail
asserts the artifact was verified. An audit record asserting success that
never happened is the documented failure smell (local observation: gc-na2o,
1384 false audit events,
gascity2026:docs/design/city-reliability-surface.md). The oracle must
detect published-identity != verified-identity on a successful publication.

## Pass condition

1. Barrier report shows the verdict record naming `id(A)` durably present
   before the reference moved, and the reference resolving to `id(B)` before
   the publication attempt.
2. Protected mode: publication is refused; the refusal names `id(A)` and
   `id(B)`; no artifact is published under the stale verdict; a subsequent
   publication succeeds only after a verdict naming `id(B)` exists.
3. Unsafe mode: publication succeeds with published identity `id(B)` while the
   only verdict names `id(A)`, and the oracle flags the inheritance.
4. In both modes the oracle decides from retained records, not from the
   pipeline's own success report.

## Evidence to retain

- The verdict record verbatim: bound identity, verifier, inputs, result.
- Reference resolution history: every (sequence, ref, resolved identity) pair.
- The publication gate's decision record: identities compared, conditions
  evaluated, outcome.
- Content identities `id(A)` and `id(B)` and the artifacts themselves, so the
  mismatch is reproducible by rehashing.
- The ordered event log joining verdict, reference move, and publication
  attempt by sequence number.

## What a pass does not establish

- Nothing about verification quality. The gate proves the verdict and the
  artifact match; whether the verdict was earned is a separate property
  ("green means executed, not asserted," local observation:
  gascity2026:docs/design/software-factory-philosophy.md).
- Content identity of a clean tree does not cover uncommitted or generated
  state; a commit identity suffices only for a clean tree, and dirty trees
  need content identities for the affected files (local observation:
  manuscript ch12).
- The window between blob write and reference publication is a distinct
  unresolved boundary in the source experiments and is not exercised here
  (local observation:
  temporallab2026:experiments/external-effects/README.md).
- A pass here does not protect against a stale writer publishing under an old
  generation; that is the stale-writer-completes drill, and the two gates must
  both hold at publication.

## Run

Specification drill: execute against a real factory through its adapter; no
in-memory implementation exists as of 2026-08-09.
