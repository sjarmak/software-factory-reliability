# Repairable Preconditions

> **Problem** A guard refuses a run over a condition the run itself knows
> how to fix.
>
> **Rule** Refuse only what you cannot repair.
>
> **Required property** When an operation holds a repair step for the
> precondition it enforces, that step runs before the check and the check
> verifies the repair's result; every refusal then names a condition no
> code path in the operation could have cleared.
>
> **Wrong** `state has drifted -> refuse -> wait for an operator`
>
> **Right** `state has drifted -> run the repair -> the check verifies the repaired state`
>
> **See it fail**
>
> - `make drill DRILL=guard-refuses-repair-never-runs MODE=unsafe` exits 2
> - `make drill DRILL=guard-refuses-repair-never-runs MODE=protected` exits 0

## Problem

An operation needs a resource to be in a particular state before it acts, so
it checks. The same operation also knows how to put the resource into that
state, so it does that too. Both halves are correct. When the check runs first
and returns on failure, the repair sits downstream of a refusal that only
fires in the states the repair exists to clear, and the operation can no longer
reach it.

Nothing looks wrong while the resource is good. The check passes, the repair
runs, the operation proceeds, and every run confirms the arrangement works.
The defect is entirely in the order of two lines, and order is what a
single-line reading does not surface. A reviewer reads the check and asks
whether it enforces the right property. A reviewer reads the repair and asks
whether it establishes the property. Neither question is about their sequence,
and both answers are yes.

The first drift is permanent. Not slow to recover, not degraded: permanent,
because the only code in the system that would restore the resource is behind
the refusal, and the refusal is what the drift causes. Run N fails, and run
N+1 fails in exactly the same way, having done exactly as much work. A
scheduled operation in this state produces a long run of identical failures,
which reads as one persistent problem rather than as a self-inflicted lockout,
and the two get triaged very differently.

The shape recurs well away from file permissions. A schema-version guard that
refuses to open a store older than the current binary, placed ahead of the
migration step that would upgrade it, cannot migrate anything. A lock-file
staleness check that aborts when it finds a lock, placed ahead of the clearer
that removes stale locks, leaves every stale lock in place forever. A refusal
to operate on a dirty worktree, placed ahead of the cleanup step that would
clean it, keeps the worktree dirty. A health gate that will not start a
component reporting unhealthy, placed ahead of the self-heal routine that
would make it healthy, is a component that never starts again. In each case
the guard's condition and the repair's postcondition are the same predicate,
and the guard is standing on the repair's only entrance.

Two pages in this kit border this one. [reconciliation](reconciliation.md)
requires a level-triggered scan that observes actual state and converges it;
a scan whose convergence step sits behind a precondition check on the state it
converges satisfies the shape of that pattern and converges nothing, and this
page is about that specific way of failing it.
[falsifiable-checks](falsifiable-checks.md) asks whether both of a check's
branches are reachable, and a guard-before-repair passes that audit: both
branches are reachable, the check discriminates correctly, and it reports the
resource's state accurately every time. The property that fails here is not
reachability of the check's branches but reachability of the repair, which is
a different question asked of a different piece of code.

One discriminator is worth carrying, because a neighbouring shape produces the
same long run of identical failures and wants a different fix. This page is
about a guard whose condition is the repair's own postcondition, so the system
holds the cure behind the symptom and the lockout is self-inflicted. The other
shape is a guard whose condition the repair could never clear at all: a
wrapper's copy of a downstream tool's interface that has drifted from the tool,
refusing a command the tool would accept. Both refuse forever and both look
like one persistent external problem in the log. Ask whether running the repair
would satisfy the guard. If it would, the order of two lines is the bug. If it
could not, the guard is asserting something it no longer knows, and the fix is
to stop keeping a second copy of somebody else's contract, or to test the copy
against the original. The reconciliation page carries a measured instance of
the second kind.

## Observed failure

A scheduled instrument in a production installation examined worktree
provisioning and wrote two files under a private directory: an append-only
audit log and a small JSON state file. Its write path called
`_check_write_target`, which refused any target that was group or world
writable, and the call that forced the mode back to owner-only ran afterward,
inside `_append_audit`, on a descriptor the refusal had prevented it from
opening. The audit log drifted to 0664 on a run that itself succeeded, at
2026-08-13T00:25 local time. That run was the last one to examine anything.
The next 94 scheduled runs exited 2 without checking a single subject, from
2026-08-13T12:05 to 2026-08-17T04:01, with no successful run among them
(local observation, gascity2026:bin/pool-worktree-provision-check,
gascity2026:.gc/events.jsonl).

The read path carried the identical defect one call earlier. `_safe_read`
refused a state file that was group writable, and the state file is written by
a step that runs after the read at the single call site, so a state file that
went 0664 could never be read, never be rewritten, and the tool went non-green
on every nudging run with no path back. That file is still 0664, and the
record says so as a measured fact rather than as a historical note (local
observation, gascity2026:bin/pool-worktree-provision-check).

The fix deleted both mode refusals and was net negative. What replaced them
was not a reordering of the same check. The write path already forced the mode
after opening the descriptor, so the refusal was duplicating an enforcement
that ran downstream of it and could not add safety to it. The remaining
refusals are the ones the operation cannot satisfy on its own: `O_NOFOLLOW`,
an ownership test, a regular-file test, and a size bound on the read. The
argument for deleting rather than reordering is recorded at both sites: the
mode is forced after open on the write path, an atomic rename propagates the
source inode's mode and never dereferences the destination's final component,
everything the payload is trusted for is re-derived after parsing, and the
containing directory is 0700, so a lax mode on a file inside it buys an
attacker a malformed payload that is rejected on content (local observation,
gascity2026:bin/pool-worktree-provision-check).

The lost signal was real and is worth naming. Before the change, a mode that
had drifted produced a loud refusal. After it, the drift is silently
corrected. The replacement is to report and then repair, rather than to refuse:
the anomaly is emitted as its own event, with the observed and required state
on it, and the operation continues. That keeps the alarm and removes the
lockout, which is the trade the refusal was never actually making, since a
refusal that also disables every future run is not a durable alarm either.

## Invariant

An operation refuses only preconditions it cannot repair. When the operation
holds a repair step for the precondition it enforces, that step runs before
the check, and the check verifies the repair's result rather than gating its
entry. Every refusal names a condition that no code path in the operation
could have cleared, so a refused run is a run that genuinely needs an operator.

The corollary binds the reader as much as the writer. A precondition check
whose predicate is the negation of a downstream repair's postcondition adds no
safety, because the state it refuses is the state the repair produces. Such a
check is removed rather than reordered, and the alarm it was carrying is
re-emitted as an observation on the repair path.

## Mechanism

**Ask one question of every guard: is there a code path in this operation that
would repair what I am refusing?** Follow the operation forward from the check
and look for a step whose postcondition is the check's condition. If one
exists downstream, the check is a one-way door. This is a mechanical question
about the call graph rather than a judgment about severity, and it separates a
real instance from a false alarm without anyone having to reason about how
likely the drift is.

**Repair, then verify.** The safe order runs the repair unconditionally and
checks afterward. Unconditional matters: a repair that runs only when an
earlier reading says it is needed has reintroduced the same ordering with an
extra step, and the reading can be stale by the time the repair would act.
Forcing the state costs nothing on a resource that already holds it, and the
verification after the repair is the check that was wanted in the first place,
now asking a question the operation can act on.

**When the check cannot move, make it report instead of block.** Some guards
sit where a repair cannot precede them, and some are wanted for their alarm
rather than their refusal. Both are served by emitting the anomaly as an event
carrying the observed state and the required state, and continuing. A blocking
guard converts one anomaly into an outage; a reporting guard converts it into
a record that something else can act on, and the record survives the run
whereas the refusal does not.

**Prefer deleting the check to reordering it.** A check whose condition is
already enforced by a step downstream of it is duplication, and reordering
duplication produces two enforcements of the same property in sequence. Delete
it, keep the enforcement that actually establishes the state, and write down
at the site why the deletion is not a loss of rigour. The enforcements worth
keeping are the ones the operation cannot satisfy for itself: identity,
ownership, symlink refusal, size bounds, authorization.

**Separate what the operation owns from what an operator owns.** The
distinction the invariant turns on is whether the operation holds a repair for
the precondition, and that is a property of the code rather than of the
resource. A file mode the operation forces on every write is owned by the
operation. A file owned by another user is not. Guards on the second class are
correct and should stay; guards on the first class are lockouts wearing the
same costume.

**Make refusals carry whether the repair ran.** A log line saying the operation
refused is compatible with both orders. A log line saying it refused after
attempting the repair, or refused without reaching it, distinguishes the
recoverable case from the permanent one at the moment of the first failure
rather than after the ninety-fourth. This is the cheapest instrumentation on
this page and the one that turns the failure from an investigation into a
reading.

## Where enforcement occurs

Enforcement is at review of the change that introduces or moves a guard, and
in a test that drives the operation against a drifted resource. It cannot be
at runtime: from inside the refusing run there is no difference between a
precondition the operation could have fixed and one it could not, which is
precisely the information the ordering destroyed.

The test is the part that mechanizes. Put the resource into the state the
guard refuses, run the operation twice, and assert that the second run is not
identical to the first. A suite that only ever exercises the operation against
a conforming resource cannot see this class at all, because on the conforming
path the safe order and the unsafe order produce the same events in the same
sequence. That equivalence is not incidental; it is why the defect survives
review, and it is why the drill below lands a write while the resource
conforms before injecting anything.

The review question is the falsifying test from the mechanism section, asked
out loud: does any code path that could repair this resource sit downstream of
this check? Reviewers answer it by reading forward rather than by reasoning
about the guard, which is why it works on a diff that shows only the guard.

## Does not guarantee

Correct ordering says nothing about whether the precondition is worth
enforcing. An operation can repair-then-verify a property that does not matter
and be exactly as wrong as before, with better availability.

The claim is as wide as the repair. Repair-then-verify covers the drifts the
repair step handles. A resource can leave the required state in ways the
repair does not address, and for those the operation refuses correctly and the
refusal is not a lockout. Enumerating which drifts the repair covers is
separate work, and assuming it covers all of them is how a reordered guard
turns into a guard that yields to everything.

Removing a blocking check removes a signal, and replacing it with an event
only helps if something reads the event. A report nobody consumes is quieter
than a refusal and no more useful, which makes this pattern dependent on
[promise-oriented-observability](promise-oriented-observability.md) rather
than a substitute for it.

Nothing here addresses a repair that is itself unsafe. Forcing a resource into
the required state is a mutation, and an operation that repairs aggressively
on every run can destroy state a human deliberately set. The pattern asks for
the repair to precede the check, not for the repair to be unconditional in its
effects on data.

## Failure drill

[guard-refuses-repair-never-runs](../drills/guard-refuses-repair-never-runs/):
a guarded write lands while the resource conforms, the resource is drifted out
of the state that write requires, and the same write is attempted twice more.
The drill is executable against the in-memory simulator in both modes.

```
python3 src/adapters/in_memory/run_drill.py guard-refuses-repair-never-runs --mode protected
python3 src/adapters/in_memory/run_drill.py guard-refuses-repair-never-runs --mode unsafe
```

Both arms hold the same precondition check and the same repair, and they
produce identical events up to the fault, which is the control for the claim
that ordering is the only variable. The protected arm reports the anomaly,
repairs the resource, verifies it, and writes; the unsafe arm refuses before
the repair and refuses the second attempt identically, leaving the resource
drifted and the record absent. The oracle checks two rails independently: the
record must reach the destination, and the repair must have run after the
drift with the resource conforming at end of run. Each rail was shown to fail
on its own by mutating the protected path for that rail only, with the other
staying green. Evidence for both arms lands in `out/evidence/`.

## Evidence

- A precondition check ordered ahead of the enforcement that establishes the
  precondition is a one-way door: an audit log drifted to a group-writable
  mode on the last run that succeeded, 2026-08-13T00:25, and the following 94
  scheduled runs exited non-zero without examining a single subject, through
  2026-08-17T04:01, with no successful run among them (local observation,
  gascity2026:bin/pool-worktree-provision-check,
  gascity2026:.gc/events.jsonl).
- The same instrument carried the defect twice, once on the write path and
  once on the read path, and the read-path instance is still in effect on its
  state file because the rewrite that would clear it runs after the read that
  refuses (local observation,
  gascity2026:bin/pool-worktree-provision-check).
- Deletion rather than reordering was the correct repair there, because the
  write path already forced the mode on the open descriptor: the refusal
  duplicated an enforcement downstream of itself, so it could not add safety
  to it (local observation,
  gascity2026:bin/pool-worktree-provision-check).
- Removal of a guard is a legitimate outcome of examining it, and the
  reasoning belongs at the site: a destination hash re-read guarded by three
  earlier checks was deleted in the same installation after replacing it with
  `if false` left the full test suite green (local observation,
  gascity2026:bin/asks-vault-mirror).
- The complementary case is a guard whose removal is felt at once, which is
  what a precondition the operation cannot satisfy for itself looks like:
  replacing the refusal to write to a destination path that exists and is not a
  regular file with a constant-false condition took that suite from 16 passed
  and 0 failed to 14 passed and 2 failed, each failure naming its case (local
  observation, gascity2026:bin/asks-vault-mirror.test).
- The general shape of a check that cannot reach its repair is already measured
  in this evidence base: 32,618 detections against zero repairs over 11 days,
  from a timeout killing every repair run, on a check whose lifetime counters
  show it converging before that (local observation,
  gascity2026:docs/asks-and-outcomes.md,
  gascity2026:.gc-reports/factory-contract-audit-2026-08-16/factory.yaml).
- Convergence toward a desired state by repeated observation and correction,
  rather than by refusing to act on states that differ from the expected one,
  is the established form of the general argument (foundational, Burgess 2003,
  On the theory of system administration).
- Fail-safe defaults and least common mechanism argue for the guards that
  survive here, and not for the ones that duplicate a downstream enforcement:
  the refusals worth keeping are those an operation cannot satisfy for itself
  (foundational, Saltzer and Schroeder 1975, The Protection of Information in
  Computer Systems).
- A repair that runs only when an earlier reading says it is needed carries
  the same ordering hazard as the guard it replaced, because the reading can
  be stale when the repair would act. This is an inference from the
  time-of-check to time-of-use literature applied to the repair path rather
  than a measured result here (inference).

## Limits

The discipline is cheap to state and easy to lose. Every guard added after the
original review is added by someone who has read the check above it and not
the operation below it, and the natural place to put a new precondition is at
the top of the function. Expect this to recur in the same file that was fixed.

The falsifying test does not survive refactoring on its own. It is a question
about the call graph at one revision, and moving the repair into a helper, or
adding a second caller that skips it, invalidates the answer without touching
the guard. A test that drives the operation twice against a drifted resource
is what re-answers the question on every run, and prose in a review comment is
not.

Deletion carries a cost that this page argues is usually worth paying, and it
is not always worth paying. Removing the refusal removes a loud, immediate
signal that a resource has drifted, and replaces it with an event and a silent
correction. In an installation where nothing consumes the event, that is a real
loss of visibility, and the correct response is to consume the event rather
than to restore the lockout.

This kit is subject to its own page. The oracles in its drills refuse on
preconditions and hold no repair for any of them, which is correct for a
verdict function and means the pattern's interesting case does not arise
there. The schema checker and prose checker in this repository have not been
examined for it.

The installation supplying the observations fixed this instance and has not
swept for others. One instrument was found because it failed loudly for four
days; the class it belongs to is silent by construction whenever the resource
happens to stay in the required state, and no count of remaining instances is
available.

## Sources

- gascity2026:bin/pool-worktree-provision-check
- gascity2026:.gc/events.jsonl
- gascity2026:bin/asks-vault-mirror
- gascity2026:bin/asks-vault-mirror.test
- gascity2026:docs/asks-and-outcomes.md
- gascity2026:.gc-reports/factory-contract-audit-2026-08-16/factory.yaml
- Burgess 2003, On the theory of system administration
- Saltzer and Schroeder 1975, The Protection of Information in Computer
  Systems
- Related patterns: [reconciliation](reconciliation.md),
  [falsifiable-checks](falsifiable-checks.md),
  [promise-oriented-observability](promise-oriented-observability.md)
- Executable drill: [guard-refuses-repair-never-runs](../drills/guard-refuses-repair-never-runs/DRILL.md)
