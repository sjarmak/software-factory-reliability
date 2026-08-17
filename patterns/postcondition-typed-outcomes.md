# Postcondition-Typed Outcomes

> **Problem** A command returns success because the call returned, not
> because the change landed.
>
> **Rule** The return value classifies the postcondition, read back from
> the destination.
>
> **Required property** Success is returned only when the requested change
> is durably present; otherwise the outcome names which weaker state holds
> instead, from a closed vocabulary the caller can branch on, with unknown
> available when neither assertion can be supported.
>
> **Wrong** `destination replies 202 -> return success`
>
> **Right** `read back the destination -> return applied, accepted but not applied, or unknown`
>
> **See it fail**
>
> - `make drill DRILL=request-accepted-effect-never-applied MODE=unsafe` exits 2
> - `make drill DRILL=request-accepted-effect-never-applied MODE=protected` exits 0

## Problem

A command boundary sits between an operator or an agent and the system: a CLI
invocation, an RPC, a tool call, a dispatch API. The caller asks for a change
and reads one line and one exit status back. That line usually describes what
the boundary did on its own side, which is the dispatch, and the caller reads
it as a statement about the destination, which is the application. Between the
two sits every partial mutation the boundary performed and did not mention.

The failure is a command that exits zero after doing part of what was asked.
Routing state is written and the worker is never woken. A message bead is
created and nobody is notified. An unknown recipe degrades into a plain route
instead of failing. A second call against already-routed work is an idempotent
no-op whose output reads exactly like a fresh dispatch. Each of these is one
command, one exit status, and several independently changing effects
(gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md, sections 1 and 4).

The caller then makes a decision on a claim nobody checked. It waits for work
that was never delivered, retries an operation whose first half already landed,
or reports a dispatch it believes succeeded. An agent is a fast and literal
consumer of exit statuses, which makes it the caller most exposed to this: it
does not carry the operator's habit of reading a success line as provisional.

The mirror image of that costs as much and is easier to miss, because it
happens after the boundary got it right. A command surface can carry a
considered vocabulary and lose it in the runtime that invokes it: the
scheduler, the runner, the shell wrapper, the pipeline step that reads the
process back. Those layers are usually written once, against the assumption
that a process either worked or did not, and they are rarely revisited when a
tool underneath them grows a richer contract. The result is a system where the
instrument is honest, the caller would branch correctly if it could, and the
value between them is truncated in transit, so a finding and a crash arrive at
the operator in the same shape. Nobody has to make a mistake for this to
happen, which is why it survives review: the boundary passes its own tests, the
runner passes its own tests, and the composition is what fails.

Three pages in this kit border this one and none of them cover it.
[explicit-unknown-state](explicit-unknown-state.md) models three outcome states
in the durable store during recovery, after a process died; this page is about
the synchronous return value at the moment of the call, when nothing has
crashed and no recovery is in question. [promise-oriented-observability](
promise-oriented-observability.md) detects a broken promise later, as absence
over time, from outside the call path; a typed outcome catches the same break
immediately, at the caller, in time to change what the caller does next. They
are complements rather than alternatives: observability is the backstop for
exactly the outcomes a boundary could not classify, and it is what turns an
`unknown-because-observation-failed` into a resolved fact.
[verify-before-publish](verify-before-publish.md) governs whether an artifact is
fit to publish; this page governs whether the command that moved it told the
truth about what it moved.

## Observed failure

The production installation measured both directions of the lie in the same
system on the same day. A dispatch wrapper reported success and delivered
nothing on three occasions, while the session-nudge command reported failure and
delivered correctly (gascity2026:CLAUDE.md). The first shape strands work; the
second drives a retry against an effect that already landed. A caller cannot
correct for either without reading the destination itself, which is the work the
boundary was supposed to have done.

The false negative has a measured cost. One queued nudge (`nudge-df7019a0034a`,
2026-08-16) was delivered three times out of three and recorded zero out of
three, its `last_error` naming a confirmation that never observed the expected
busy state. The queue read its own record as an undelivered item and re-sent it
about every ten minutes for a full day, carrying work direction the recipient
had finished after the first delivery. The command surface offers no cancel
verb, so stopping it meant hand-editing queue state
(gascity2026:docs/conventions/bead-dispatch.md).

A durable queue can also lose the request without ever producing an error to
classify. Queued dispatches are bound to a session generation rather than to the
logical work item, so a recycled session discards the intent instead of
re-targeting it, with the work item's own identifier sitting in the record. The
alerting contract for that path terminates in a log line and produces no mail,
no work item, and no message. Dead entries are pruned an hour after they die,
while the reporter over that queue runs every six hours and overwrites its own
output, so up to five hours of entries leave no per-entry record at all
(gascity2026:.gc-reports/factory-contract-audit-2026-08-16/drills-3-4-effects-reconciliation.md).

Accumulation is the visible end state of that. On 2026-08-12 the dead-letter
queue held 67 undelivered dispatches accumulated since 2026-06-12, every one of
them work direction, read by nothing (gascity2026:CLAUDE.md). Two things were
already written down about the same defect at the time, and neither had produced
a mechanism. The 2026-08-16 audit confirms that historical figure at its own
date against the order written for it, and records that the backlog was later
pruned rather than triaged, which is the accumulation ending in deletion rather
than in a decision
(gascity2026:.gc-reports/factory-contract-audit-2026-08-16/drills-3-4-effects-reconciliation.md).

Exit statuses can be disconnected from the postcondition by construction. A
statement wrapper of the form `-q "USE db; stmt"` exits zero when the statement
was refused, which was verified on 2026-08-09 by reading the table rather than
the exit status
(gascity2026:docs/adr/0021-idempotent-convergence-and-fenced-publication.md).
For that boundary the exit status is not a weak postcondition claim, it is not a
postcondition claim at all, and the only correct use of it is to ignore it.

The truncation-in-transit failure was measured across a whole fleet of
instruments on 2026-08-17. A maintenance campaign four days earlier had
upgraded eleven scheduled checks from a boolean exit to a three-valued
contract: zero for a clean reading, one for a reading that found actionable
work, two for a reading the check could not take. The contract is written into
the scripts themselves, one of them documenting the tri-state in its own module
docstring. The scheduler that runs them maps any non-zero exit to a single
execution-failed outcome and emits a failure event carrying the numeric status
as a message string, and its order configuration carries no field for an
expected or actionable exit code. The richer vocabulary therefore reached
exactly one surface, an event log nobody reads, and everywhere else "I checked
and found work" renders identically to "I crashed"
(gascity2026:cmd/gc/order_dispatch.go, gascity2026:internal/orders/order.go).

The cost is a board that cannot be read. Across those eleven checks, 1,313 runs
were recorded as failures in the three days after the campaign landed. Seven
were examined in detail and six of the seven were instruments reporting
correctly. One publishes a report, mails its finding, and exits one because the
finding is non-empty; it has never exited zero and it cannot, because a second
cell in the same report is permanently unavailable pending a deployment step
nobody has run, in 47 of 47 recorded runs. Another refuses to report green
without an operator authority file, exactly as its own configuration specifies
it must, and that correct refusal is recorded as a crash on every fire. A third
did its whole job on each failing run, balanced capacity, posted a real alert
and persisted its dedupe state, then returned two because two of the five
accounts it is required to observe can never be observed, one of them retired
by decision a month earlier. The seventh was genuinely broken, and it had been
sitting in the same colour as the other six for four days.

What that concealment cost is better stated in outcomes than in board colours,
because the case for repairing a display problem is otherwise easy to defer. The
genuinely broken instrument was an approval gate. A hosted CI provider withholds
workflow runs submitted from forks until a maintainer approves them, and the
gate exists to find those runs and say so. A strictness change four days earlier
made it require that every run it examined carry exactly one attached pull
request. Runs submitted from forks carry zero attachments, every time, in 100 of
100 measured. Because the census reads runs in arrival order, a single fork run
at the first row of the first page raised before anything was examined, so the
gate did not degrade to missing fork runs; it returned an incomplete reading on
every fire and examined nothing at all. It said so honestly, thirteen times in
the day it was finally read, using the tri-state contract the campaign had given
it. Eleven pull requests from people outside the team had their tests blocked
for four days, and nine of the eleven arrived after the gate went dark, so
nobody had ever seen them. Two properties had to hold at once for that to last
four days: the check refused to read precisely the population it existed to
find, and the layer above it could not tell that refusal apart from the
eighty-nine healthy runs recorded as failures on the same board on the same day.

Where the fix has to go is what separates this from the boundary-side failure.
Eleven scripts could each be edited to surrender their vocabulary and return
zero for anything short of a crash, which is the change a red board pressures
someone into making, and it would remove precisely the distinction the campaign
was run to create. The single change that preserves it is one field on the
runner.

## Invariant

A command's return value classifies the postcondition the caller asked for, read
back from the destination rather than inferred from the command's own outbound
call. A success outcome is returned only when the requested change is durably
present; when it is not, the outcome names which weaker state holds instead,
from a closed vocabulary the caller can branch on. The claim binds in both
directions: a failure outcome asserts that the change is absent, and a boundary
that cannot support either assertion returns the unknown outcome rather than
picking one. Every request the boundary accepted carries an identity that
resolves to a durable terminal record under that same identity, whatever the
boundary returned synchronously.

## Mechanism

The boundary performs the requested operation, then reads the destination back
under the request's identity, then classifies. Six outcomes are enough to carry
every case observed above, and each one answers a different question for the
caller: may I proceed, may I retry, and may I retry unchanged.

**requested-and-applied.** The destination holds every element of the requested
change, confirmed by a readback under the request identity. A boundary is
entitled to this outcome only when the readback completed and covered the whole
requested set. The caller may proceed as though the change is in force, and may
treat an identical repeat of the request as a no-op.

**requested-and-queued.** The request is durably accepted for later application.
The boundary has confirmed the acceptance record and nothing else; it has not
observed the change at the destination, and it says so. A boundary is entitled
to this outcome when acceptance is durable and carries an identity the caller
can resolve later. The caller may not proceed as though applied. It may wait on
the identity, poll for the terminal record, or hand the identity to something
that will, and it may re-submit only under that same identity.

**partially-applied.** Some elements of the requested set are present at the
destination and others are absent, with both subsets named in the outcome. A
boundary is entitled to this outcome whenever its readback covers the whole set
and finds it split. This state is the reason a boolean is insufficient rather
than merely coarse: it has no truthful projection onto two values. Reporting it
as success loses the absent elements permanently, because nothing downstream
will look for them. Reporting it as failure invites a retry over the elements
that already landed. The caller's safe move is to retry the named absent subset
only, under effect identities that make a repeat of the landed subset harmless
([effect-identity](effect-identity.md)).

**rejected-before-mutation.** The boundary refused the request and no part of it
reached the destination. A boundary is entitled to this outcome only when the
refusal is structurally before the first mutation, which means validation,
admission control, or authorization ran to completion first. A boundary that
merely believes nothing landed is not entitled to it. This is the one outcome
after which an unchanged retry is safe with no further reasoning, which is why
the entitlement bar on it is the highest of the six. The installation's own
recommendation follows from this: unknown recipes and ineligible work should
fail before mutation rather than after, because failing early is what makes the
retry cheap.

**failed-after-mutation.** At least one mutation reached the destination and the
operation then failed. A boundary is entitled to this outcome when it can name
what landed; when it cannot, the honest outcome is the unknown one. The caller
must not retry blindly. The available paths are re-application under a stable
effect identity the destination deduplicates, compensation of the named
mutations, or escalation to an owner ([effect-identity](effect-identity.md)).

**unknown-because-observation-failed.** The boundary could not read back its own
postcondition: the readback timed out, reached a store that could not answer, or
returned an empty result the boundary has no reason to trust. A boundary is
entitled to this outcome exactly when its observation failed, and it is a
legitimate answer rather than a defect report. The caller treats the request as
neither applied nor absent and resolves it through the recovery path in
[explicit-unknown-state](explicit-unknown-state.md). The failure this outcome
exists to prevent is printing an empty result as a successful absence when the
query itself was not trustworthy, which the installation observed turning
transient read failures into apparent deletions and inviting duplicate routing
(gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md, section 7).

The outcome is a two-sided claim. Most discussion of this failure treats the
false positive as the whole problem, and the false negative costs as much by a
different route: a boundary that reports failure while the effect landed drives
a retry against a mutation that already happened. The 3/3-delivered and
0/3-recorded nudge is that case with a measured blast radius, a full day of
re-delivered work direction against a recipient who had already acted. A
boundary tuned to resolve its own ambiguity toward failure has not become
conservative; it has traded lost work for duplicated work, and it has done so
silently in both configurations.

A queued outcome owes a terminal record. `requested-and-queued` is a promise the
boundary made on behalf of a downstream applier, and a promise with no terminal
record expires into nothing: the caller holds a stale success, the system holds
no evidence that a promise was broken, and the pruning window eventually removes
the queue entry that was the only trace. The requirement is written over
accepted requests rather than over returned outcomes, because the return value
is the half the caller can discard: every request the boundary accepted resolves,
under the same request identity, to a durable record of
`requested-and-applied`, `failed-after-mutation`, or an explicit expiry naming
the reason. Writing it over the returned outcome instead leaves the worst case
uncovered, since a boundary that wrongly returned success has by its own
reckoning nothing left to resolve. The record is the object the backstop scan
looks for, which is where this pattern hands off to
[promise-oriented-observability](promise-oriented-observability.md); without it
the scan has nothing to find and reports green.

```
handle(request):
    outcome = perform(request)              # may mutate the destination
    observed = read_back(request.identity)  # the destination, not the response
    if observed.failed:
        return unknown_because_observation_failed(request.identity, why)
    landed  = observed.elements_present
    return classify(request.requested_set, landed, outcome.refused_before_mutation)

resolve(request):                            # runs until terminal, out of band
    if terminal_record_exists(request.identity): return
    if past_deadline(request):
        write_terminal(request.identity, "expired", reason)
```

The two blocks are separate on purpose. The first is synchronous and belongs to
the caller's thread; the second is level-triggered and belongs to whatever
outlives the call. A boundary that implements only the first produces honest
outcomes that then rot in a queue; one that implements only the second is this
kit's reconciliation pattern with no improvement at the call site.

## Where enforcement occurs

At the command surface itself, in the path that constructs the return value. The
check that matters is mechanical and a reviewer can apply it to any boundary in
an afternoon: trace the value the boundary returns back to its source. If it
derives from the result of the boundary's own outbound call, the boundary is
reporting on its dispatch. A dispatch call returning 200 is an observation about
the dispatch. If the value derives from a read of the destination performed
after the mutation, keyed by the request's identity, the boundary is reporting
on the postcondition.

Two shapes fail that trace while looking correct. The first is a boundary that
holds a richer internal result and flattens it on the way out: the installation
has a delivery result type carrying an explicit undelivered reason, and the
calling path never reads that field, so three distinguishable outcomes collapse
into one boolean at the surface
(gascity2026:.gc-reports/factory-contract-audit-2026-08-16/drills-3-4-effects-reconciliation.md).
The vocabulary existed and the boundary discarded it. The second is a boundary
whose exit status was never wired to the operation at all, which is the
statement wrapper that exits zero on a refused statement; there the enforcement
has to move to the caller, which must read the table, and the boundary's
contribution is to document that its own status means nothing.

Enforcement has a second site, and it is the one the fleet measurement above
found empty: every runtime between the boundary and the operator has to be
audited for the same truncation. A scheduler, a wrapper, a retry loop and a
pipeline step are all callers, and each of them is entitled to collapse a
vocabulary it was never told about. The mechanical check is the mirror of the
one above: for each consuming layer, name the values it can distinguish, then
compare that set against what the boundaries beneath it emit. Where the
consumer's set is smaller, the difference is being discarded, and the place to
widen it is the consumer, not the boundaries. Widening a consuming layer is one
change; narrowing every boundary beneath it to fit is as many changes as there
are boundaries, and it destroys the information rather than delivering it.

Running that audit on the same system found the second site immediately, in a
path nobody had thought of as a consumer. Besides the exec path, the scheduler
has a condition path: an order may declare a check command, and the check's
documented contract is that exit zero means fire and non-zero means the
condition is not met. One such check reports whether any undelivered escalation
exists, and its own header says non-zero means there are none, which is the
healthy steady state. The trigger evaluator returns not-due in both cases, so
behaviour is correct, and the reason string it attaches renders the healthy
state as "check command failed: exit status 1" on the operator's primary order
listing. That single string covers two different situations: the condition was
evaluated and is false, and the check could not be run at all because its
binary is missing or unreadable. An operator scanning the list cannot tell a
resting order from one that has never been able to evaluate its own trigger
(gascity2026:internal/orders/triggers.go).

The instructive part is that the exec path and the condition path are in the
same package, written by the same people, and only one of them was found by
looking at the red board, because this one does not produce a red anything. It
produces a normal-looking listing with an alarming word in it, which costs a
reader's time on every scan and hides the genuine could-not-run case
permanently. Auditing consuming layers means enumerating all of them, including
the ones whose truncation does not show up as a failure.

The enforcement fails to be available in one case, and that case has an answer.
When the destination cannot be read back (no query keyed by the request
identity, a read path that is down, an answer the boundary cannot trust), the
boundary cannot classify and must not guess. It returns
`unknown-because-observation-failed` with the identity and the reason for the
failed observation. That outcome is a correct result of a correctly built
boundary, not a bug in it, and treating it as a bug is what pressures
implementations back into guessing. What is not permitted is returning it when
the observation succeeded and the answer was inconvenient, or returning success
because the readback was never attempted.

Where the postcondition is genuinely unobservable for structural reasons (a
destination with no read-back path, a fire-and-forget transport), the honest
design returns `requested-and-queued` with an identity and accepts the terminal
record as the only place applied-ness will ever be established. That moves the
truth claim out of the synchronous return value on purpose, and it is a
different thing from a boundary that claims success while holding the same
ignorance.

## Does not guarantee

- No correctness of the requested change. The invariant constrains what the
  boundary says about what happened, and says nothing about whether what was
  asked for was the right thing to ask for.
- No atomicity. `partially-applied` is a truthful report of a state the pattern
  does not prevent; making the requested set atomic is a property of the
  destination, not of the outcome vocabulary.
- No bounded time to terminality. A queued outcome that owes a terminal record
  can owe it for a long time; the deadline and the scan cadence are policy
  ([promise-oriented-observability](promise-oriented-observability.md)).
- No safety of the retry the outcome licenses. `rejected-before-mutation` makes
  an unchanged retry safe with respect to this call; retry volume and retry
  synchronization are separate concerns.
- No protection against a wrong readback. A read against a stale replica or the
  wrong store resolves the classification incorrectly with full confidence, and
  the same discipline the recovery read needs applies here
  ([reconciliation](reconciliation.md)).
- No help for a caller that ignores the type, and the caller is often a runtime
  nobody thought of as one. A boundary can return six distinguishable outcomes
  to a scheduler, wrapper, or pipeline step that branches on zero versus
  non-zero, and the vocabulary dies there with the boundary entirely blameless.
  The fleet measurement above is this bullet with a number on it: eleven
  instruments, 1,313 runs recorded as failures in three days, six of the seven
  examined reporting correctly.

## Failure drill

[request-accepted-effect-never-applied](../drills/request-accepted-effect-never-applied/):
the boundary accepts a request durably, the application leg is dropped after
acceptance, and the destination never receives the mutation. The drill is
executable against the in-memory simulator in both modes.

```
python3 src/adapters/in_memory/run_drill.py request-accepted-effect-never-applied --mode protected
python3 src/adapters/in_memory/run_drill.py request-accepted-effect-never-applied --mode unsafe
```

The protected arm classifies by reading the destination back, returns
`requested-and-queued`, and writes a terminal expiry record when the window
closes with the effect still absent. The unsafe arm classifies from its own
accepted dispatch, returns `requested-and-applied` while the destination holds
no mutation, and lets the request expire with no terminal record. The oracle
checks both halves: a success-shaped outcome over an empty destination is a
violation, and so is a non-terminal outcome that reaches end of run with nothing
recorded. Evidence for both arms lands in `out/evidence/`.

## Evidence

- The highest-cost failure class is a command that succeeds syntactically while
  only part of the requested operation happened, and the recommended remedy is a
  typed outcome carrying enough postcondition state to distinguish applied,
  queued, partial, rejected-before-mutation, failed-after-mutation, and unknown:
  local observation
  (gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md, executive
  summary and meta-pattern).
- Routing state written and the wake never delivered, a formula attached to the
  wrong object under a success-shaped message, an unknown formula degrading into
  a plain route, and a repeat call reading like a fresh dispatch, all under one
  exit status: local observation (same source, section 1).
- Message creation reported as success while notification is opt-in, so the
  success line does not mean anyone was reached: local observation (same source,
  section 4).
- Transient read failures returning "no issue found" for valid records, then
  succeeding on immediate retry, so absence and uncertainty are indistinguishable
  at the boundary: local observation (same source, section 7).
- A dispatch wrapper reporting success and delivering nothing three times while
  a different dispatch command reported failure and delivered correctly: local
  observation (gascity2026:CLAUDE.md).
- A nudge delivered 3/3 and recorded 0/3, re-sent about every ten minutes for
  24 hours, carrying work direction the recipient had already completed, with no
  cancel verb available: local observation
  (gascity2026:docs/conventions/bead-dispatch.md).
- 67 undelivered dispatches accumulated between 2026-06-12 and 2026-08-12, all
  of them work direction, read by nothing: local observation
  (gascity2026:CLAUDE.md); confirmed at its own date and later pruned rather
  than triaged, with a one-hour dead retention under a six-hourly
  destructive-overwrite reporter, leaving up to five hours with no per-entry
  record: local observation
  (gascity2026:.gc-reports/factory-contract-audit-2026-08-16/drills-3-4-effects-reconciliation.md).
- Queued intent bound to a session generation rather than to the logical work
  item, discarded on session roll with the work identifier present in the
  record, and an alerting contract terminating in a log line: local observation
  (same source).
- A delivery result type carrying an explicit undelivered reason, never read by
  the calling path, collapsing three outcomes into one boolean at the surface:
  local observation (same source).
- A statement wrapper of the form `-q "USE db; stmt"` exiting zero on a refused
  statement, verified 2026-08-09 by reading the table rather than the exit
  status: local observation
  (gascity2026:docs/adr/0021-idempotent-convergence-and-fenced-publication.md).
- Eleven scheduled checks upgraded to a three-valued exit contract, run by a
  scheduler that maps every non-zero status to one execution-failed outcome and
  offers no per-order allowlist, so 1,313 runs across three days were recorded
  as failures with no way to separate a finding from a crash: local observation
  (gascity2026:cmd/gc/order_dispatch.go,
  gascity2026:internal/orders/order.go).
- Of seven of those checks examined in detail, six were reporting correctly and
  one was genuinely broken: a report that exits non-zero because its finding is
  non-empty and has never exited zero, its second cell unavailable in 47 of 47
  runs pending an unrun deployment step; a readout correctly refusing to report
  green without an operator authority file, as its own configuration requires; a
  quota alarm that posted a real alert and persisted dedupe state on each
  failing run, then returned two because two of five required accounts are
  permanently unobservable, one retired by decision a month earlier: local
  observation (same sources).
- The one genuinely broken check was an approval gate for externally submitted
  CI runs, and it was concealed for four days by the eighty-nine healthy runs
  recorded as failures beside it: a strictness change required exactly one
  attached pull request per run, fork-submitted runs carry zero in 100 of 100
  measured, and a fork run at the first row of the first page aborted the whole
  census, so the gate returned an incomplete reading on every fire and examined
  nothing. Thirteen of thirteen runs in the day it was finally read reported
  that incompleteness correctly. Eleven outside-contributor pull requests had
  their tests blocked, nine of them arriving after the gate went dark: local
  observation, measured 2026-08-17 by invoking the gate read-only and by
  re-querying the CI provider rather than trusting the tool's own exit line
  (gascity2026:bin/fork-pr-approval-gate).
- The same truncation recurs in a second consuming path in the same package,
  found by running this pattern's own audit rather than by reading the board:
  a condition trigger whose check contract is exit-zero-fires, non-zero-does-not,
  where the not-met case and the check-could-not-run case share one reason
  string, so a resting order and an order that has never evaluated its trigger
  are indistinguishable on the operator's primary listing: local observation,
  measured 2026-08-17 (gascity2026:internal/orders/triggers.go).
- Idempotent receivers and request identifiers as the precondition for a safe
  retry: foundational (Joshi 2023, Patterns of Distributed Systems).
- Fault classification into rejected-before-mutation, failed-after-mutation, and
  indeterminate is the standard remote-invocation partition; the request cannot
  be retried safely without it: foundational (Waldo et al. 1994; Kleppmann 2017,
  ch. 8).
- The six-state vocabulary as the boundary's complete return type, with the
  two-sided truth claim and the terminal-record obligation on queued outcomes:
  inference (our synthesis; the six states are recommended in the source report
  and each failure shape is observed, and the composition executes only against
  the in-memory simulator).

## Limits

No boundary in the evidence base returns this vocabulary today. The installation
that supplies every observation on this page is the one running this kit's own
guidance, and it fails this pattern at every command surface its own report
measured: dispatch, hook, nudge, mail, formula, event, store client, and
verification gate. That is the strongest available evidence that the pattern
names something real, and it is also the reason nothing here may be read as a
description of a working system. The six states are a proposal recorded in that
installation's report, and the only place they execute is the simulator.

Partial adoption exists and is the sharper warning. Eleven of that
installation's scheduled checks do return a considered vocabulary, three states
rather than six, deliberately introduced by a campaign whose whole purpose was
to make an unreadable board readable. It made the board less readable, because
the consuming runtime was not part of the change. That is the cheapest
available lesson on this page and the one most likely to be repeated: adopting
a typed outcome at the boundary, without widening every consumer between the
boundary and the human, converts working instruments into apparent failures and
creates pressure to reverse the improvement.

The pattern makes the boundary slower and more expensive by construction. Every
command that would have returned after its outbound call now performs a readback
against the destination, and neither the source report nor this kit measures
what that costs at fleet rates. A boundary whose readback is expensive will be
tempted to sample it, and a sampled readback is a success claim with an unstated
error rate.

The vocabulary's completeness is unproven. Six states covered every shape in the
nine sections of the source report, which is a claim about one installation's
observed failures and not about the space of them. A boundary that finds a
seventh case should add it rather than round it into `unknown`, and the state to
watch is applied-then-reverted, which none of the sources exercise.

Nothing here addresses a caller that must decide without waiting. A synchronous
readback lengthens the call, and a boundary under latency pressure will want to
return before observing. The honest form of that is `requested-and-queued` with
a terminal record, which is exactly the state whose resolution the sources show
failing most often; the pattern converts a latency problem into a liveness
obligation rather than removing it.

## Sources

- gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md
- gascity2026:.gc-reports/factory-contract-audit-2026-08-16/drills-3-4-effects-reconciliation.md
- gascity2026:CLAUDE.md
- gascity2026:docs/conventions/bead-dispatch.md
- gascity2026:docs/adr/0021-idempotent-convergence-and-fenced-publication.md
- gascity2026:cmd/gc/order_dispatch.go
- gascity2026:bin/fork-pr-approval-gate
- gascity2026:internal/orders/triggers.go
- gascity2026:internal/orders/order.go
- Joshi 2023, Patterns of Distributed Systems (idempotent receiver, request identifiers)
- Waldo, Wyant, Wollrath, Kendall 1994, A Note on Distributed Computing
- Kleppmann 2017, Designing Data-Intensive Applications, ch. 8
- Related patterns: [explicit-unknown-state](explicit-unknown-state.md),
  [effect-identity](effect-identity.md),
  [promise-oriented-observability](promise-oriented-observability.md),
  [verify-before-publish](verify-before-publish.md),
  [reconciliation](reconciliation.md)
- Executable drill: [request-accepted-effect-never-applied](../drills/request-accepted-effect-never-applied/DRILL.md)
