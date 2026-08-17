# Verify Before Publish

> **Problem** The artifact that ships is not the artifact that was tested.
>
> **Rule** The verdict binds to an immutable artifact identity, never to a
> mutable reference.
>
> **Required property** An artifact stays a candidate until an independent
> mechanism establishes the postcondition, and publication rechecks at
> publish time that the artifact being published is identical to the one
> verified, or the verdict is void and verification reruns.
>
> **Wrong** `verify the branch -> publish the branch`
>
> **Right** `verify commit abc123 -> publish confirms the reference still resolves to abc123 -> publish`
>
> **See it fail**
>
> - [`drills/artifact-changes-after-verification/`](../drills/artifact-changes-after-verification/), a specification with no executable arm yet
>
> **Checked by** `VERIFY-001`, `VERIFY-002`, `VERIFY-003`, `VERIFY-004` in
> the [rule catalog](../docs/contract-reference.md)

## Problem

A worker's completion report is testimony. It is produced from the same
context that may already be wrong, so it cannot be evidence about its own
correctness. Publication (merging, releasing, closing the work item,
recording the audit event) converts testimony into system fact, and every
downstream consumer treats published facts as load-bearing: schedulers
release dependent work, reapers stop watching, humans stop checking.
Publishing on testimony therefore launders an unverified claim into the
factory's ground truth.

A second failure axis is verdict binding. A verification verdict attached to
a mutable reference (a branch name, a "latest" tag, a work item whose
artifact can be swapped) survives mutation of the thing it vouched for. The
verdict stays green while the artifact it examined no longer exists, and the
publish step happily ships whatever the reference points at now.

## Observed failure

Gas City gc-na2o: gc-sling wrote metadata to the wrong rig, leaving roughly
40 orphan worktrees (~2.8GB) and 1384 false `worktree-recorded` audit
events. The surface map's summary: "the audit log asserted success that
never happened." The publish step (recording the audit fact) ran on the
worker's claim with no independent read-back of the state it described, and
the false record was found by a human reading source, not by any automated
cover. (gascity2026:docs/design/city-reliability-surface.md)

The book manuscript's ch07 records the testimony failure in miniature: a
resumed agent checked off the remaining steps of its checklist on an
inherited clean worktree where the prior attempt had changed nothing. The
checklist satisfied itself; only an observation with different provenance
(diff plus tests run from the branch) would have caught it.
(ercabook2026:chapters/,
ch07)

Binding failures appeared in the Temporal evaluation's own infrastructure:
the running shadow worker binary's hash could not be tied to a reviewed
commit (no VCS stamp), so no review verdict provably applied to the running
artifact; separately, the soak checker falsely counted synthetic
`temporal-shadow/...` references as dispatched beads (dr-l90r), a verifier
reading the wrong population and reporting green.
(gascity2026:docs/design/temporal-decision.md)

## Invariant

An artifact is a candidate until an independent mechanism establishes the
postcondition, and the resulting verdict binds to an immutable artifact
identity (content hash, commit ID, published generation), never to a mutable
reference. Publication checks the binding at publish time: the artifact
being published must be identical to the artifact that was verified, or the
verdict is void and verification reruns. Worker testimony may inform
scheduling; it never satisfies the publish gate.

## Mechanism

```
candidate --verify--> verified --publish--> published

verdict = (artifact_content_id,     # immutable identity, not a ref
           postcondition_id,        # what was established
           verifier_identity,       # who/what established it
           evidence_refs)           # test run, read-back, replay record

publish gate:
  current_artifact_id == verdict.artifact_content_id
  AND verdict.postcondition_id covers the publish action
  else -> back to candidate, re-verify
```

Independence is a provenance property, not a personnel property. Completion
evidence must originate at the effect boundary: tests executed from the
branch, a commit identifier, a verifier result, a read-back from the system
of record tied to the attempt identity. Gas City states the norm as "green
means executed, not asserted," with reviews kept as records on beads rather
than self-reports. The reviewer role change makes the same point for humans
and agents: a separate context starts from the acceptance criteria and the
artifacts, assigned the stance that it did not write the code and must
actively test each claim. Independence comes from the evidence path and the
task, not from using a different model.

Binding rules observed working in the source systems:

- Approvals bind to the exact artifact hash and revision, not to intent; a
  changed artifact requires a new approval.
- Terminal outcome checks read artifact movement (is the commit an ancestor
  of main?), not the work item's status field, because a closed bead is a
  status signal rather than an outcome signal.
- Evidence inputs get the same treatment as outputs: a retrieval artifact
  carries the repository-state identity it was built from, the gate compares
  it against the state being edited, and evidence without revision identity
  is treated as stale rather than current.

## Where enforcement occurs

At the publish boundary, in a mechanism outside the worker's context and
authority. The manuscript's ch16 distinguishes mechanical gates (execution
blocks until a condition holds) from attentional gates (evidence plus a
required human decision), and notes that agent memory is neither: a
remembered instruction has no independent causal path to enforcement. The
gate must demonstrably change execution. The author's human-approval queue
failed its own audit on exactly this point: scripts failed open when a
required component was missing, and command construction reached execution
without validation, so approval records existed while actions bypassed the
person. The corrective is to test the gate as a failure experiment: remove
each dependency in turn and confirm a rejected action fails to execute
through every path, and that modified actions require renewed review.

The verdict store is append-only with respect to published artifacts; a
re-verification of a changed artifact produces a new verdict against the new
content identity rather than editing the old one, so the history of what was
vouched for, and when, survives.

## Does not guarantee

- Properties outside the checked postcondition. A green verdict is exactly
  as wide as its oracle.
- Anything about the destination after publication; the binding fixes the
  publish instant only.
- Deduplication of the publish effect itself; publishing twice is an effect
  identity problem ([effect-identity](effect-identity.md)).
- Reviewer engagement. Approval rates near 100 percent can coexist with
  reviewers modifying every action's parameters, which is why approval
  metrics need matched denominators over the same action class.
- Verifier correctness. A verifier reading the wrong population, or sharing
  the worker's failure domain, produces confident false verdicts.
- Outcome resolution for interrupted publishes; that is the recovery problem
  of [explicit-unknown-state](explicit-unknown-state.md).

## Failure drill

[artifact-changes-after-verification](../drills/artifact-changes-after-verification/):
verify a candidate, mutate the artifact behind the same mutable reference,
then attempt to publish. The gate must refuse, because the verdict's content
identity no longer matches the artifact at the reference. A gate keyed on
the reference rather than the content identity publishes the unverified
artifact and fails the drill; a second variant deletes the verifier
mid-publish and requires the action to fail closed.

## Evidence

- 1384 false audit events asserting worktree creation that never happened;
  found by source reading, not automated cover. Local observation
  (gascity2026:docs/design/city-reliability-surface.md).
- "Green means executed, not asserted"; reviews are records, not
  self-reports; the terminal check is artifact ancestry in main, not bead
  status. Local observation
  (gascity2026:docs/design/software-factory-philosophy.md).
- A resumed agent's checklist satisfied itself on an inherited clean
  worktree. Local observation
  (ercabook2026:chapters/,
  ch07).
- Worker self-reports degrade as other failures are fixed: across 20,574
  real sessions in 1,639 repositories, 91.49 percent of visible resolutions
  required explicit user correction, and inaccurate self-reporting accounted
  for a growing share of remaining misalignment episodes. Agent-era (Tang et
  al. 2026, cited in ch07).
- An approval gate with no causal power records assent and nothing more: the
  author's approval queue failed open in its own audit. Local observation
  (ch16 of the manuscript).
- Provenance belongs at the review surface: 28 developers were unreliable at
  recognizing machine-generated code unaided; labeling improved verification
  and repair at the cost of workload. Agent-era (Tang et al. 2024, cited in
  ch16).
- Checking tracks its economics: verification engagement rose when checking
  cost less or error cost was more salient, across five experiments.
  Agent-era (Vasconcelos et al. 2023, cited in ch15).
- A running binary that cannot be tied to a reviewed commit voids the review
  verdict's binding. Local observation
  (gascity2026:docs/design/temporal-decision.md).
- Stale evidence produces confident wrong output: under stale-only
  retrieval, 15 of 17 (Qwen2.5-Coder-7B-Instruct) and 13 of 17
  (gpt-4.1-mini) outputs were incompatible with the current helper
  signature; zero were incompatible under current-only retrieval. Agent-era
  (Weng et al. 2026, cited in ch12).

## Limits

- Verification must stay cheaper than uncritical acceptance or it gets
  bypassed; the interventions that work best against overreliance are the
  ones users rate lowest, so a gate maintained by satisfaction feedback
  erodes by construction. Agent-era basis (Buçinca et al. 2021, ch15).
- The verifier is a component with its own failure domain. Gas City's
  covers-die-too rule applies: a cover sharing the failure domain of the
  thing it guards has moved the single point of failure rather than removed
  it.
- Immutable identity is as strong as the store that anchors it. Content
  addressing bounds the problem; mutable tag semantics and rewriteable
  history reopen it, so provenance markers must be tested through rebases,
  squash merges, and cherry-picks before they are trusted.
- Postcondition design is the residual hard problem. This pattern relocates
  trust from testimony to an oracle; a weak oracle is a weak gate, and no
  mechanism here strengthens it. Inference: oracle coverage should be
  audited against escaped defects per action class, which none of the cited
  systems yet measures.

## Sources

- gascity2026:docs/design/city-reliability-surface.md
- gascity2026:docs/design/software-factory-philosophy.md
- gascity2026:docs/design/temporal-decision.md
- ercabook2026:chapters/ (ch07, ch12, ch15, ch16)
- Related patterns: [effect-identity](effect-identity.md),
  [explicit-unknown-state](explicit-unknown-state.md),
  [reconciliation](reconciliation.md)
