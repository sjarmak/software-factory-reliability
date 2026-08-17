# Falsifiable Checks

## Problem

Every other page in this kit ends by telling you to add a check. Reconcile on a
scan. Verify the postcondition before publishing. Alert on the promise that
went unconfirmed. None of them say how you know the check you added is a check.

A check is code that returns a verdict, so it has at least two branches, and
writing it does not make both of them reachable. When the red branch cannot be
entered, the check passes in every state, including the states it was written
to catch. When the green branch cannot be entered, the check fires in every
state, and its detections pile up against repairs that never happen. Both keep
reporting on schedule. Both survive review, because reading a check tells you
what it intends and not which of its branches the running system can reach. And
both are invisible to a test suite that exercises only the normal path, since
on the normal path the correct verdict and the constant verdict are the same
verdict.

The red branch goes unreachable in three recurring ways, and none of them look
like a bug at the call site. The check reads a value produced by the thing it
is checking, so it is measuring the writer. It keys on a field nothing in the
system writes, so its predicate is quantified over an empty set and passes for
everything that has ever existed. Or it sits behind earlier guards that have
already excluded the condition it tests, so it is dead code wearing the costume
of rigour. The signature all three share, and the cheapest thing to look for,
is an instrument with one word in its entire recorded output history.

The green branch goes unreachable more rarely and costs more per occurrence,
because a check that fires looks like a check doing its job. The usual cause is
that the detection leg and the repair leg are separate code paths and only the
first one runs: the repair times out, or lacks permission, or was wired to a
queue nobody drains. Detections accumulate. Somebody reads the count as a
measure of how bad the problem is, which is the one thing it is not.

A check that never reaches its own verdict lands in the same direction by a
different route. A fail-closed precondition can be correct in isolation and
permanently unsatisfiable in situ, so the check refuses on every run. This is
worse to detect than the timeout version, because refusing to act and having
nothing to act on produce the same output, and a board that renders a refusal
as a failure teaches its readers to skip the row.

The two directions are the same defect. In each case the check's verdict is
constant across a state change that crosses its own claim, which means the
verdict is a property of the check's implementation rather than of the system.
That is what makes one comparison sufficient to catch both, and it is why this
page treats them together rather than as separate failures of overcaution and
undercaution.

Adding tests does not close this, and the reason it does not is the step most
teams reach for next. A check and the test double it runs against are usually
written in one sitting, by one person, from one understanding of what the tool
under check emits. When that understanding is wrong it is wrong in both places
at once: the predicate asserts a value the tool cannot produce, and the double
produces exactly that value. Every mutation the author then thinks to write is
a departure from a fixture that was never the tool's output, so the suite goes
green and stays green over an instrument that could not have verified one real
subject. The green is manufactured by the harness, and it is evidence about the
double rather than weak evidence about the check.

Three pages in this kit border this one and none of them cover it.
[verify-before-publish](verify-before-publish.md) requires an independent
mechanism to establish the postcondition before an artifact is published; this
page asks what makes that mechanism independent, and answers it with a
demonstrated mutation rather than with the mechanism's own report. Their
relation is concrete rather than philosophical: the verdict binding that page
demands is defeated by a gate whose passing value was written by the code
reading it, which is the input-provenance half of the invariant below.
[promise-oriented-observability](promise-oriented-observability.md) makes the
absence of a confirming event alertable, and its own alerting rules are checks
with the same two branches; a monitor that has only ever been observed green is
covered by this page and not by that one.
[reconciliation](reconciliation.md) requires a level-triggered scan that
detects and repairs the same transition; a scan whose repair leg cannot run
still satisfies the shape of that pattern while converging nothing, which is
the never-green direction and is exactly the failure this page names.

## Observed failure

A reap protocol in a production installation ran four preconditions before
acting, and one of them keyed on `gc.claimed_at`, a metadata field that no
writer in the system ever set. The field was present on 0 of 1,298 work items
in the store the guard queried, so that leg returned true for every item that
had ever existed. The protocol was described in its own documentation as four
independent confirmations; three were doing the work, and the fourth had never
been able to say no (local observation,
gascity2026:docs/conventions/city-learnings.md). Re-measured later across all
23 bead databases rather than the one, the same key was present on 0 of
16,646 items, and the audit that found it recorded the finding as confirmed and
worse than first reported (local observation,
gascity2026:.gc-reports/factory-contract-audit-2026-08-16/audit-one-work-item.md).

The same installation's review gate is the provenance case. The code that
closed a work item wrote `evidence.reviewer_verdict = "pass"` into the item's
metadata and then gated the close on that key, and the shell rail beside it
validated the key's name without ever reading its value. The gate could not
refuse a close, because the value it consulted was written by the thing it was
gating, and the exposure stood from 2026-08-03 to 2026-08-11 across 216 closes
in 10 databases before either half was fixed (local observation,
gascity2026:.gc-reports/factory-contract-audit-2026-08-16/README.md).

The never-green direction appeared in the same installation on a close-gate
reaper, which recorded 32,618 detections against zero repairs over 11 days. The
cause
was a timeout killing every repair run before it could act, so the detection
leg ran and the repair leg did not (local observation,
gascity2026:docs/asks-and-outcomes.md, work item `dr-qg34j`). Two details make
this the useful example rather than a simple outage. The counter had converged
before: lifetime it records 209 and 73 repairs of its two kinds against its
detections, so this was a check that used to work and stopped, not one that
never worked (local observation,
gascity2026:.gc-reports/factory-contract-audit-2026-08-16/factory.yaml). And
the detection count was legible for 11 days as evidence of a large problem,
which is precisely the reading a never-green check invites.

The reading side of the same invariant failed twice in one night in that
installation, and the second failure was inside the tool written to fix the
first. Counting how often a launch hook fired was done by grepping an agent's
session transcript for the hook's name, which also matches the agent's own prose
about hooks in tool results and file edits, so the count was assembled partly
out of the writing that described it and yielded a per-turn distribution that
did not exist. The replacement keys on the record type instead of the string
(local observation, gascity2026:bin/hook-duplication-check). Hours after it
shipped, a second operator read a duplicate count off that tool with `grep -c
submit` and got 19 where the answer was 17. The per-offender line read
`<timestamp>  submit <key>`, so the grep returned 17 offender rows plus the
tool's own header, `75 submit(s), 160 hook record(s)`, plus its own verdict
line, `DUPLICATED: 17 of 75 submit(s)`. The verbose per-submit rows carried no
`submit` token at all, so that grep was never counting submits; it counted
duplicate rows and two of the instrument's summary lines, and landed close
enough to pass for data. The gap of two was explained away as a population
difference between operators before either of them checked the arithmetic. An
instrument that is wrong by two survives scrutiny in a way that one wrong by
fifty does not.

The harness case is visible in the same installation on two store-maintenance
instruments, both rewritten from shell into Python on 2026-08-14. Each calls the
storage engine's garbage collector and compares the engine's report against a
literal. One required the byte string `status\n1\n` from a `CALL DOLT_GC()`
probe, where the engine declares that column as an integer and returns 0 on
success. The other carried the same inversion at three call sites, plus a
predicate requiring a dry run's key set to be exactly `{"commit_count"}` where
the tool emits seven keys, plus a whole-dictionary comparison against a
revalidation record it could not match. No predicate among them could be
satisfied by the real tool at any point in its history. The first instrument
raised `garbage collection unproved` on the first of 25 databases and never
reached the other 24, and its store grew 308 MiB against a 184 MiB mean nightly
reclaim; the second stayed inert while its corpus went from 2,075 to 9,245
commits, 85 percent past the threshold it exists to enforce (local observation,
gascity2026:bin/dolt-gc-maintenance, gascity2026:bin/dolt-flatten-maintenance).

Each shipped with a Python suite, and each suite was green: 23 tests on one, 29
on the other. The stubs returned the values the broken predicates demanded, a
status byte of 1 and a single-key dictionary, so the two instruments were tested
against a tool that does not exist. In the first suite the engine's real output
was wired in as the injected fault, which means the test written to catch this
defect asserted it. Repairing the doubles to emit what the engine emits, then
running the repaired suites against the code as it shipped, fails 11 of 23 tests
on one instrument and 14 of 29 on the other. Among the failures on each is the
mutation test itself, because several of its named mutants can no longer be
applied: the shipped source already contained the mutation the test wanted to
introduce.

Ten instruments in that installation have a single outcome word in their entire
recorded vocabulary. One reaper has written exactly one line in its lifetime,
and that line reports a detection, so nobody knows what it does when there is
nothing to detect (local observation,
gascity2026:docs/conventions/instrument-contract.md).

## Invariant

A check is admitted only after someone has demonstrated a state change that
flips its verdict, and the demonstration is recorded next to the check: the
specific mutation applied, and the named test that goes red under it. The
check's verdict is computed from an input that neither the check nor the
component under check produced, so the branch reporting the other verdict is
reachable in the running system. A check whose verdict is constant across a
transition that crosses its own claim is not evidence about the system and is
removed or repaired, in either direction of constancy.

The same separation binds the check's output. A correct verdict is still
unusable when the only way to extract it also matches the check's own
commentary, because then the consumer's number is computed from what the check
said about itself. Data rows carry a token that no summary, advice, or heading
line contains, and the suite asserts that extracting by that token returns the
same count the check reports in prose.

Provenance binds the demonstration as well as the verdict. A test double is an
assertion about what the tool emits, so a double composed from the check's
expectation supplies the check with its own belief and every verdict computed
against it is self-supplied at one remove. The double's values are traceable to
a recorded run of the real tool, and the suite is run once against the subject
as it shipped, where it must fail. A suite that has only been observed passing
against the revision it was written beside carries no information about which of
the two was wrong.

## Mechanism

**Name the transition when you write the check, not when it fails.** A check
carries two declarations: the claim it makes about the system, and the subject
that claim is about. Those two fix the transition that must flip the verdict,
which is what makes the demonstration a specific piece of work rather than a
matter of taste. A check that cannot name the state change it distinguishes has
already failed this pattern, because there is nothing to demonstrate.

**Demonstrate by mutation, and observe the red by running it.** Break the thing
the check watches, or pin the check's verdict, and watch a named test fail.
Reading the test is not the demonstration. In the installation supplying the
evidence here, the author of that rule broke it on the day it was written: the
first regression test for a fix passed with the defect reintroduced, because
one assertion checked files the test had planted itself and the other passed on
an accident of which component wrote a shared file last. Nothing about reading
the suite revealed either (local observation,
gascity2026:docs/conventions/instrument-contract.md).

**Record the pair where the check lives.** The mutation and the test that goes
red under it belong in a comment on the check or in the test file beside it,
not in a commit message. The next person to touch the check needs to know which
red they must preserve, and a commit message is not a place anyone looks before
editing a guard. This is also what makes the claim's width visible: the check
is demonstrated for that transition, and for no other.

**Compute the verdict from input the subject did not produce.** A check that
reads a value written by the component it examines is testing the writer, and
the failure is silent because the value is usually correct. Prefer reading the
destination, the external system, or an ordered log that the subject cannot
edit after the fact. When the only available input is written by the subject,
the check is a consistency check between two of the subject's claims, and it
should say so in its own name rather than presenting as a check on the world.

**Build the double from the tool's output, not from the check's expectation.**
Capture a real invocation and paste what it printed. A fixture composed from
what the check is looking for cannot disagree with the check, and disagreement
is the only thing a fixture is for. Keep the captured bytes in the test file
with a comment naming the tool version they came from, so the next reviewer can
compare the fixture against the tool rather than against the predicate. The
inverse move is the one to watch for at review: a stub whose values were
adjusted until the suite went green is a record of what the author believed, and
it will hold that belief against every later reader.

**Run the suite against the code as it shipped, and require it to fail.**
Passing against the fix is one direction of a two-direction claim, and only the
second direction separates a suite that measures the oracle from a suite that
was fitted to it. Give the subject an environment override so the control is one
command rather than a branch checkout, and record its result beside the mutation
pair. The failing test names are themselves a report: a named mutant that cannot
be applied, because the shipped source already contains it, is the suite saying
the shipped code was the mutant.

**Give the check more than one outcome word, and make absence meaningful.** An
instrument that writes a record only when it finds something cannot distinguish
a clean run from a run that never happened. Emitting a verdict on every run,
including the boring one, turns the check's own silence into a detectable
condition and makes the never-green case visible as a ratio rather than a
count.

**Emit the repair as its own event, separate from the detection.** The
never-green direction is only visible when detections and repairs are counted
separately over the same window. A check that reports detections alone cannot
be distinguished from a check whose repair leg has been dead for eleven days,
and the count will be read as severity by whoever sees it next.

**Delete a branch you have shown to be unreachable.** When the demonstration
fails because the branch genuinely cannot be entered, the honest outcome is
removal rather than retention as insurance. One instrument in the evidence base
records this decision in the source: a hash re-read after an atomic rename was
removed because replacing it with `if false` left every test case green, and
the comment says why the removal is not a loss of rigour (local observation,
gascity2026:bin/asks-vault-mirror). A retained unreachable branch is worse than
no branch, because it is counted by every later reader as coverage.

## Where enforcement occurs

Enforcement is at admission, in the review of the change that introduces or
edits a check, and in the test file that ships with it. It cannot be at
runtime. From inside a running check there is no difference between the
property holding and the property being invisible to it, which is the same
reason a component cannot certify its own liveness. The demonstration is a
property of the change, so it lives where changes are examined.

One cheap structural rail belongs in continuous integration rather than in a
reviewer's head: every metadata key a check reads must have a writer elsewhere
in the tree that is not the check itself. A grep returning exactly one hit, the
read, means the key has no producer and the predicate is quantified over an
empty set; a grep whose only other hit is the check's own write means the
verdict is self-supplied. Both are mechanical, neither requires judging what
the check means, and together they catch the empty-domain and self-supplied
cases without a reviewer having to reason about reachability. Asking that
question once, in one afternoon, turned up three such keys in the installation
supplying the evidence here, one of which had left 25 contributor pull requests
under a standing block at a 42 day median (local observation,
gascity2026:docs/conventions/city-learnings.md).

The control run belongs on the same rail, for the narrower case of a change that
repairs a check. Requiring the suite to pass against the change and fail against
its parent is mechanical, needs no judgment about what the check means, and is
the one automatic rail that catches a harness written from the same
misunderstanding as the code it tests. It costs one extra suite run, and it is
only available while the parent revision still holds the defect, which is an
argument for running it at review time rather than filing it as future work.

The demonstration itself does not automate. Whether a mutation is the one that
crosses the check's claim is a judgment about what the check is for, and a
mutation generator will produce many that flip a verdict for reasons unrelated
to the property. Ask for the pair (mutation, red test) at review time and read
it; the rail above only removes the cases that never needed a human.

## Does not guarantee

A demonstrated check can still be measuring the wrong property. Discrimination
and input provenance together establish that the verdict tracks something in
the world and was not self-supplied. They say nothing about whether that
something is what the check's name claims. A check that correctly discriminates present from absent on the
wrong record is falsifiable and useless.

The claim is exactly as wide as the mutation. Demonstrating that a check
separates missing from present does not show it can see wrong-value,
stale-value, or duplicate. Every additional property is a separate
demonstration, and a page of green mutation records is a list of specific
things the check can see rather than a general competence.

The demonstration decays. It was performed at one revision, and a refactor can
strand it: the check keeps its shape, the mutation no longer reaches it, and
the recorded pair reads as current evidence. This is why the pair names a test
rather than describing an outcome in prose. A named test is re-run by the
suite; a described outcome is only re-read, and re-reading is what fails here.

A control run against the parent revision proves the suite discriminates between
those two revisions of the subject and nothing wider. It says nothing about
whether the double still matches the tool. A fixture captured from one version
of an external tool ages with that tool, and when the tool's output format moves,
the double keeps asserting the old shape while the check in production meets the
new one. That restores the original defect with a green suite sitting over it,
and the provenance comment on the capture is the only thing that makes the
staleness legible on a later read.

Nothing here addresses false positives or the cost of a check that fires
correctly but too often. A check can discriminate perfectly and still be
noise, and the remedy for that is a different conversation than this one.

Enumerating what to check is out of scope and is its own failure. A check
demonstrated against an incorrectly enumerated corpus is falsifiable within a
population that omits the cases it exists to catch, and enumerating instruments
by name matching is the recurring version of that error in the evidence base.

## Failure drill

[state-changes-check-does-not](../drills/state-changes-check-does-not/): a
check is registered over one destination record before anything happens, the
write that would satisfy it is dropped, the check is evaluated, the write is
retried for real, and the check is evaluated again. The drill is executable
against the in-memory simulator in both modes.

```
python3 -m adapters.in_memory.run_drill state-changes-check-does-not --mode protected
python3 -m adapters.in_memory.run_drill state-changes-check-does-not --mode unsafe
```

The protected arm reads the destination back and reports `fail` then `pass`
over the same subject. The unsafe arm stamps a metadata key at the start of
each evaluation and reads that key as its evidence, so it reports `pass` while
the record is missing and `pass` again after it arrives. The destination
behaves identically in both arms, so nothing but the check's own input can
account for the difference. The oracle checks two rails independently: the same
verdict on both sides of a confirmed state change is a violation, and so is an
evaluation reading a key that check wrote. Each rail was shown to fail on its
own by mutating the protected path for that rail only, with the other staying
green. Evidence for both arms lands in `out/evidence/`.

## Evidence

- A guard keyed on a metadata field that no writer sets returns true for every
  item in the store, and the protocol containing it was documented as four
  independent confirmations while three were load-bearing: 0 of 1,298 items
  carried the field in the store first measured, and 0 of 16,646 across all 23
  databases when re-measured (local observation,
  gascity2026:docs/conventions/city-learnings.md,
  gascity2026:.gc-reports/factory-contract-audit-2026-08-16/audit-one-work-item.md).
- A review gate whose passing value was written by the code that read it could
  not refuse a close; 216 closes across 10 databases ran under it between
  2026-08-03 and 2026-08-11 (local observation,
  gascity2026:.gc-reports/factory-contract-audit-2026-08-16/README.md).
- A check whose repair leg cannot run accumulates detections that read as
  severity: 32,618 detections against zero repairs over 11 days, from a timeout
  killing every repair run, on a check whose lifetime counters show it
  converging before that (local observation,
  gascity2026:docs/asks-and-outcomes.md,
  gascity2026:.gc-reports/factory-contract-audit-2026-08-16/factory.yaml).
- Two instruments verifying the same storage engine both required a status byte
  of 1 where the engine returns 0, and both shipped suites forged that byte: 23
  and 29 tests green over instruments that could not have verified one real
  database, one of them dead since a rewrite three days earlier. The repaired
  suites run against the shipped code fail 11 of 23 and 14 of 29 (local
  observation, gascity2026:bin/dolt-gc-maintenance,
  gascity2026:bin/dolt-flatten-maintenance).
- The first of those instruments verified 25 of 25 databases on its next
  production run and reclaimed 171 MiB, its first complete sweep in four days
  (local observation, gascity2026:docs/asks-and-outcomes.md).
- A reconciler for messaging bindings refused to run on 93 consecutive
  occasions over three days, after 91 completions, because it keyed an agent
  slot on a session template and a warm pool put four live sessions behind one
  template. That is correct pool behaviour rather than drift, so the
  precondition can never be satisfied again. Nothing was lost while it held:
  the installation had two bindings in total and both sat on live sessions, so
  the instrument was blind rather than failing to repair (local observation,
  gascity2026:bin/slack-binding-reaper).
- Ten instruments in one installation have a single outcome word in their
  entire recorded history, one of them having written exactly one line ever
  (local observation, gascity2026:docs/conventions/instrument-contract.md).
- Instruments audited against that installation's own instrument contract
  failed its make-it-fail clause four times out of four before being touched:
  one had no test, and three had suites that had only ever been observed
  passing, including the watchdog whose job is noticing silence (local
  observation, gascity2026:docs/conventions/instrument-contract.md).
- Reading a test is not equivalent to running it against a mutation: a
  regression test written for a specific fix passed with that fix reverted,
  because one assertion read files the test had planted and another passed on
  an accident of write ordering (local observation,
  gascity2026:docs/conventions/instrument-contract.md).
- Removal is a legitimate result of the demonstration. A destination hash
  re-read guarded by three earlier checks was deleted after replacing it with
  `if false` left the full test suite green, with the reasoning recorded at the
  site (local observation, gascity2026:bin/asks-vault-mirror).
- The complementary case is a guard whose removal is felt immediately: a
  freshness check comparing an index timestamp before and after a refresh, and
  a refusal to write to a destination path that exists and is not a regular
  file, each produced a specific named test failure when replaced with `if
  false` (local observation, gascity2026:bin/cass-index-refresh,
  gascity2026:bin/asks-vault-mirror).
- Mutation testing is the established form of the general argument: a test
  suite's value is measured by the faults it detects, not by the code it
  executes (foundational, DeMillo, Lipton, Sayward 1978; Papadakis et al. 2019).
- Falsifiability as the admission criterion for a claim is older than software
  and is the same move applied to a different object (foundational, Popper
  1959).

## Limits

The discipline costs a demonstration per check, and the failure mode under
schedule pressure is a reviewer accepting "I read it and it looks right" for
the guard rather than the code. That acceptance is the whole failure, since
reading is precisely what does not distinguish a passing check from an
unreachable one.

Demonstrations do not compose. Showing that ten checks each discriminate says
nothing about a protocol built from all ten, and the reap protocol in the
evidence base is the counterexample: four legs, three of them live, described
as four confirmations. Compose the checks and demonstrate the composition, or
state the count that is actually load-bearing.

The never-green direction is harder to catch in production than the never-red
direction. A firing check looks like a working check under load, and the only
cheap distinguishing signal is the ratio of detections to repairs over a
window, which requires the repair leg to emit its own event. A factory whose
checks report detections alone has no way to see this class at all.

This kit is subject to its own page, and meets it in one place only. The
oracles in the executable drills are checks, and each one is admitted here on a
per-rail mutation of the protected arm with the other rail held green. That
covers the oracles. The schema checker and the prose checker in this repository
were put through the same demonstration and each was observed red: removing a
required property from a work manifest fixture, removing an identity field from
one line of the sample event log, and adding a banned phrase and an em dash to a
probe page each produced a specific named failure, and the probes were deleted
afterwards. That satisfies the make-it-fail clause for those two and says
nothing about the provenance one. This repository does not run the control
described above on its own oracles: their
fixtures are composed in the simulator rather than captured from an external
tool, so the provenance rule is satisfied trivially and the fail-against-parent
rail has never been exercised here. That is the weaker half of this page as it
applies to the kit itself, and it is stated rather than fixed.

The installation supplying most of the observations above fails this pattern
broadly, and the failure is the default rather than the exception there: every
instrument audited against its own contract failed the make-it-fail clause
before being touched. Treat the pattern as a description of what the evidence
says goes wrong, and the numbers here as measurements of a system that had the
machinery and the written rule and still shipped guards nobody had made fail.

## Sources

- gascity2026:docs/conventions/instrument-contract.md
- gascity2026:docs/conventions/city-learnings.md
- gascity2026:.gc-reports/factory-contract-audit-2026-08-16/audit-one-work-item.md
- gascity2026:.gc-reports/factory-contract-audit-2026-08-16/README.md
- gascity2026:.gc-reports/factory-contract-audit-2026-08-16/factory.yaml
- gascity2026:docs/asks-and-outcomes.md
- gascity2026:bin/asks-vault-mirror
- gascity2026:bin/cass-index-refresh
- gascity2026:bin/dolt-gc-maintenance
- gascity2026:bin/dolt-flatten-maintenance
- gascity2026:bin/slack-binding-reaper
- DeMillo, Lipton, Sayward 1978, Hints on Test Data Selection
- Papadakis, Kintis, Zhang, Jia, Le Traon, Harman 2019, Mutation Testing
  Advances: An Analysis and Survey
- Popper 1959, The Logic of Scientific Discovery
- Related patterns: [verify-before-publish](verify-before-publish.md),
  [promise-oriented-observability](promise-oriented-observability.md),
  [reconciliation](reconciliation.md),
  [explicit-unknown-state](explicit-unknown-state.md)
- Executable drill: [state-changes-check-does-not](../drills/state-changes-check-does-not/DRILL.md)
