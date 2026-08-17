# Evidence methodology

How claims in this kit are labeled, what counts as evidence for a guarantee,
which sources are public, and why the tool refuses to compute a score.

## Evidence states

Every guarantee in a factory contract carries one of three evidence states.

- **declared**: the contract names the promise and the boundary it holds at.
  Nothing checks it yet. This is still useful; a named promise can be reviewed
  and falsified, an unnamed one cannot.
- **enforced**: a mechanism at the named boundary rejects violations, and the
  review can point at it. Enforcement at the wrong layer does not count; a
  caller-side check does not enforce a destination-side promise.
- **fault-tested**: an executed drill injected the specific fault the promise
  guards against, the unsafe control violated the oracle, the protected run
  passed, and the evidence from both runs is retained.

The states are ordered, and each one adds something the previous one lacks:
declaring gives you a promise to review, enforcing gives you a code path that
rejects, and fault-testing gives you a run where the rejection happened, with
a log you can reread.

An unsafe control is mandatory at the third state. A drill whose unsafe arm
also passes has not demonstrated that the protection is load-bearing; it has
demonstrated that the fault never reached the boundary. That is the condition
[falsifiable-checks](../patterns/falsifiable-checks.md) exists to catch, and
`make drills` enforces it by running each drill in both modes and requiring
opposite exit statuses.

## Why there is no aggregate score

Evidence states attach to individual guarantees, and the kit deliberately
defines no aggregate maturity score.

A factory with nine fault-tested guarantees and one declared-only guarantee on
its merge path is not ninety percent safe; it is unsafe at the merge path, and
an average conceals exactly the number that matters. External mutations are
the worst loss class in the production evidence (a duplicate merge is worse
than a skipped cycle), so safety is set by the weakest external-mutation
boundary, not by the mean.

Aggregates also redirect effort toward raising a count. The evidence base
includes a health surface where 83 of 106 live checks reported green without
ever examining whether work moved (local observation,
gascity2026:docs/design/city-reliability-surface.md). A single score invites
the same drift at the contract level: the cheapest way to move it is to add
guarantees that were never at risk.

What the tool reports instead is a list: every finding, its rule id, the
contract path it landed on, and its severity. `factory-check review` writes
that list to `out/findings.json` so it can be diffed between runs. A run that
turns one FAIL into a PASS is a real change; a run whose total count dropped
because a section was deleted is visible in the same diff.

## Basis labels

Every factual claim in the kit carries one of four labels, recorded per claim
in [`evidence/evidence-map.yaml`](../evidence/evidence-map.yaml). An inference
is never presented as an established result.

| Label | What it means | Example |
| --- | --- | --- |
| **foundational** | established distributed-systems literature | fencing tokens prevent a stale lease holder from writing after expiry (Kleppmann 2017) |
| **agent-era** | published work on agent systems | a planner-to-subagent search design attributed 41.8 percent of its failures to handoff loss (manuscript ch17, citing Oskooei et al. 2026) |
| **local observation** | the production installation, the durability lab, or the book manuscript's experiments, always with a file path | all 18 unsafe trials recorded two external effects under one recorded completion (temporallab2026:docs/guarantees.md) |
| **inference** | our synthesis, not yet demonstrated | conflict-graph scheduling of agent fleets should reduce cross-agent interference; no end-to-end demonstration exists yet |

Two of the syntheses in this kit are labeled inference and have not been
demonstrated end to end: the in-flight change graph for campaigns, and
conflict-graph scheduling for fleets. They are written as designs with stated
invariants, and the drills for them are specifications rather than executable
runs.

## Sources

Three evidence bases inform the patterns, drills, and conventions here.

The first is a production multi-agent installation whose field failures
between 2026-04 and 2026-08 are documented in incident reports, root-cause
analyses, and reliability surveys. It supplies most of the named outages and
the counts.

The second is a controlled durability lab that ran preregistered
fault-injection experiments against a workflow engine, always with an unsafe
negative control and preserved raw evidence. It supplies the identity and
fencing results.

The third is the experiments in a book manuscript on engineering reliable
coding agents (2026), which supply the guarded-mutation demonstrations and the
scheduling replay.

## Reading a citation

Local observations are cited as `<bibkey>:<path>`, where the key names one of
the three sources in [`evidence/sources.bib`](../evidence/sources.bib) and the
path identifies a document inside it. Those documents are not public. The
citation locates a claim's origin and tells you which body of evidence to ask
about; it is not a link, and you cannot follow it from here.

That is a real limit on what a reader can check independently, and it is why
the kit publishes two things that do not depend on the private sources.

**The reproducible bundles.** Every claim the in-memory simulator can
demonstrate is published in full under
[`evidence/case-studies/`](../evidence/case-studies/): the unsafe run, the
protected run, the oracle verdict, and the exact command that regenerates
both. These are self-contained. You can rerun them from a clean checkout and
compare byte for byte against the committed evidence, without access to any
private repository.

**The written case studies.** Where a result came from a private source and
cannot be reproduced from this repository, the case study states the design,
the arm counts, the software versions, and the outcome, and marks the raw
per-trial evidence as unpublished. A reader can judge whether the design
supports the conclusion even when the raw arms are not in hand. The
cancellation case study is the current example.

Claims of the first kind are the ones the kit relies on for its executable
guarantees. Claims of the second kind inform the patterns and are labeled so
that the difference is visible at the point of use rather than in a footnote.

## Vendor neutrality

Normative text is vendor-neutral. Naming a system inside an evidence citation
is fine, because the citation is a fact about where a result came from.
Recommending one is not, because the patterns are properties of the problem
and every stack relocates them rather than removing them.
