# Lag-Bounded Reads

> **Problem** A derived view answers a query from whatever prefix of
> history it happens to hold.
>
> **Rule** A view that cannot state its position cannot state a result.
>
> **Required property** The view publishes the source position it has
> consumed and answers only within a declared freshness contract; a query
> it cannot meet returns lag-exceeded naming both positions, and a view
> with no published position returns lag-unknown.
>
> **Wrong** `query the view -> return rows`
>
> **Right** `query the view -> compare its published position against the contract -> answer or return lag-exceeded`
>
> **See it fail**
>
> - `make drill DRILL=source-advances-view-answers-anyway MODE=unsafe` exits 2
> - `make drill DRILL=source-advances-view-answers-anyway MODE=protected` exits 0

## Problem

A derived read model is any store built by consuming the source of truth's
ordered history: a search index, a cache, a materialized view, a projection, a
graph built from a repository, an embedding store. It exists because the source
cannot answer the query cheaply, and it is correct only up to the point in the
source's history it has consumed.

The gap between the source and the view is not the failure. Every such model
lags, by design, and a system that could not tolerate lag would not have built
one. The failure is that the gap is invisible at the moment it matters. A query
against a view holding a strict prefix of the source returns a result set that
is complete with respect to the index and silent about the source, and the two
are indistinguishable in the answer. A record that exists and has not been
consumed comes back as zero hits, which is the same value a record that never
existed comes back as.

This is worse in an agent factory than in an application, for two reasons that
compound. The first is that agents query these views to establish absence.
"Search prior sessions for whether this was already tried" is an absence query,
and the answer that changes behavior is the empty one. An application that shows
a user a stale product listing shows something slightly wrong; an agent that
reads zero hits concludes the work was never done and does it again. The second
is that agents are structurally unable to notice the staleness the way a person
would. A person searching for something they wrote last week and finding nothing
suspects the index. An agent has no memory of last week, so a false empty read
is consistent with everything else it knows.

The health surface should catch this, and routinely does not, because health for
a derived view is usually derived from the indexer process rather than from the
view's contents. An indexer that is running, that has consumed records
successfully, and that reports no errors is a healthy indexer sitting in front
of a view that is months behind. Liveness of the producer is not freshness of
the product.

## Observed failure

A shared lexical index over agent session history in the installation this kit
draws from went stale and stayed stale. Five friction records were written by
one agent inside a 46 minute window on 2026-08-16, each naming the same cause:
prior-session search was unusable because the index was months behind, and the
fallback was manual reconstruction from other stores (local observation:
gascity2026:.papercuts.jsonl). One record carries both halves of the failure in
one line. Health reported the index stale, last indexed 2026-04-22, and an exact
search for a known term returned zero hits.

The precise shape of that pair is the point, and it is worth stating carefully
rather than dramatically. The freshness signal existed. A health command
computed it, said "stale", and exited non-zero. What it did not do was reach the
query path: the search returned zero hits without consulting it, and returned
them as an ordinary empty result. A caller running the search alone had no
indication that anything was wrong, and a caller running both had two answers
and no rule for combining them. The signal being present somewhere in the system
is not the same as the signal being on the path the caller took.

A later record from a different agent shows the same view answering with two
disagreeing surfaces. A rebuild printed "index completed" and then "index
failed: out of memory" in the same run. Afterwards one diagnostic command
reported "Index Status: OK" while the health command reported "index stale" and
exited 1. Untangling which was true cost roughly fifteen minutes of probing, and
the resolution was that both were partly right: the historical corpus had
rebuilt and was searchable, while the most recently ingested month never reached
the lexical index. A probe term from the old corpus returned 36886 matches; a
phrase from 2026-08-08 returned zero (local observation:
gascity2026:.papercuts.jsonl). Two surfaces, one view, no reconciliation between
them, and the disagreement was itself the most accurate description of the
state.

The same installation's tooling audit names this class directly. Its
meta-pattern reads: "The expensive class is not a loud failure. It is a
success-shaped no-op, partial mutation, false empty result, stale assignment, or
missing delivery receipt." Its recommendation for the store boundary is to add a
health command that proves a current read/write round trip rather than only
checking that a process or job is running, and never to print an empty result as
a successful absence when the query itself was not trustworthy (local
observation:
gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md, section 7 and
meta-pattern).

The installation's own operating document generalizes the failure past derived
views. It requires that a load-bearing claim about system state carry the
command that would refute it, and that the command be the one that can refute
the claim rather than a related one. Its worked example is a version claim: a
command reading the binary on disk agreed with a stale claim, while a query
against the running server settled it, and the two differ by exactly the failure
being guarded. It states the reason the failure is silent: "A claim that was
true when written is indistinguishable from one that is true now, at every site
except the one that re-measures" (local observation: gascity2026:CLAUDE.md). A
derived view is a claim about the source, written once at index time and read
many times afterwards, so it is that failure with a cache in front of it.

## Invariant

A derived view publishes the position in the source's ordered history that it
has consumed, and answers a query only within a declared freshness contract
measured against that position. A query whose requirement the view cannot meet
returns a lag-exceeded outcome naming both the view's published position and the
source's current position, rather than a result set computed from the prefix the
view happens to hold. A view that publishes no position returns lag-unknown,
which is a third state and not a slow version of fresh. Every surface that
reports the view's health derives its verdict from those same two positions, so
no two surfaces can disagree about staleness and a running indexer is never by
itself evidence of a fresh view.

Three things in that wording changed while it was being implemented, and each
change came from the implementation refusing to work as first stated.

The first draft said callers can compare the view's published position against
the source's current position. Building it showed that this is a lever, not an
invariant. Exposing a comparable number puts the check on every caller, at every
call site, forever, and a caller that skips it gets exactly the old behavior
with no signal that it did. The enforcement had to move inside the query path,
so the view refuses to answer rather than publishing a number for someone else
to consult. What callers may still do is widen the contract for a query that
tolerates lag, which is a decision the caller states rather than a check the
caller performs.

The first draft treated lag as a number. Implementing it produced a third state
immediately: a view that has never published a position has no lag, and
answering the query with "lag is zero" or "lag is infinite" both lose the same
information. `lag-unknown` had to become a first-class outcome, for the same
reason `unknown` is a first-class outcome in
[explicit-unknown-state](explicit-unknown-state.md) and in
[postcondition-typed-outcomes](postcondition-typed-outcomes.md).

The health clause was not in the first draft at all. It was added because the
evidence has two surfaces disagreeing about one view, and a lag-checking query
path sitting next to a liveness-based status page reproduces that exactly: the
operator gets two answers and picks one. Making the query path honest is not
sufficient when the surface a human reads is computed from something else.

## Mechanism

**Publish a position, not a timestamp.** The view exposes how far into the
source's ordered history it has consumed, in the source's own coordinates: a
sequence number, a log offset, a commit identity, a mutation count. A wall-clock
"last indexed at" is the common implementation and it is weaker in a specific
way. It measures when the indexer last ran, not what it consumed, so an indexer
that runs every minute and has been failing to ingest for a month reports a
fresh timestamp over a stale view. The position is a statement about content;
the timestamp is a statement about the process.

**Derive the source position from the source.** The comparison is only as good
as its right-hand side, and the tempting implementation caches the source
position on the view. In the reference implementation the source position is
recomputed from the destination's own record on every call, so it cannot drift
from the source it describes. A view's position is a claim the view makes about
itself; the source position must be a count of what exists.

**Declare a contract per query, defaulting per view.** Freshness requirements
are not uniform. An exact lookup for a named record is a read-your-writes query
and admits no lag at all. A dashboard aggregate over a month tolerates a great
deal. The view carries a default contract and the caller may widen it for a
query that genuinely tolerates staleness, which makes the tolerance an explicit
decision recorded at the call site.

**Answer, refuse, or say unknown.** The query path resolves to one of three
outcomes, and the vocabulary is closed so a caller can branch on it:

```
query(view, term, required_lag = view.declared_contract):
    consumed = view.published_position          # the view's claim
    current  = source.current_position()        # recomputed from the source
    if consumed is None:
        return lag-unknown(view)                # publishes no position
    lag = current - consumed
    if lag > required_lag:
        return lag-exceeded(consumed, current, required_lag)
    return results-complete(matches(view, term), lag)
```

The refusal carries both positions, so the caller can act on the gap rather than
only being blocked by it: wait for the view to catch up, fall back to the source
for this one query, or re-issue with a wider contract and record that it did.

**Compute every health surface from the same two positions.** A status page, a
diagnostic command, a monitoring probe, and the query path all resolve freshness
the same way. The reference implementation makes each health record carry the
basis it was computed from, which is what makes a right-looking verdict computed
the wrong way visible before it becomes a disagreement.

**Do not derive freshness from the indexer.** A running indexer, a recent
successful run, and an absence of errors are all statements about the producer.
The reference implementation includes the liveness-derived surface as the unsafe
control precisely because it is the common implementation and it looks
reasonable in code review.

## Where enforcement occurs

In the view's query path, in the function that constructs the result. The
mechanical check for a reviewer is the same trace
[postcondition-typed-outcomes](postcondition-typed-outcomes.md) asks for: take
the value the query returns and trace it back. If it derives only from the
index's contents, the query is reporting on the index. If it derives from a
comparison between the view's published position and a freshly obtained source
position, the query is reporting on the view's relationship to the source.

Two shapes fail that trace while looking correct. The first is a system where
the freshness signal exists on a different surface, which is exactly the
observed case: a health command that computes staleness correctly, next to a
search command that never calls it. Nothing is missing from the system; the
signal is simply not on the path the caller took. The second is a view that
advances its published position as it processes records and writes the record
contents separately, so a partial ingest leaves the position ahead of the
contents. The position check then passes on a view that answers wrongly, and the
observed out-of-memory rebuild is that shape: the run advanced far enough to
report completion while an entire month never reached the lexical index.

The second shape is why the reference oracle for the executable drill does not
trust the position when it checks the answer. It recomputes the record's
presence at the destination and compares it against the view's actual entries,
reading no position at all, so a view that advanced its position without
indexing the record still fails the check. The health check is the mirror image:
it recomputes the lag from the source's record count and the view's published
position and reads no entry. The two checks share no input, which is what makes
them independent rather than merely separate.

There is one case where the enforcement is not available, and it has an answer.
Where the source exposes no position a reader can obtain, or obtaining it costs
as much as the query the view exists to avoid, the view cannot bound its lag and
must return `lag-unknown` rather than a result set. That is a correct result from
a correctly built view. What is not permitted is returning a complete-looking
result because the position was never fetched.

## Does not guarantee

- No freshness. The invariant constrains what the view says about its lag, and
  makes a caller's staleness tolerance explicit. It does not make the view catch
  up, and a view that refuses every query is fully compliant and useless.
- No completeness within the contract. A caller that widens its tolerance to one
  position gets an answer, and that answer is still silent about whatever is
  inside the gap. The contract bounds the staleness a caller accepts; it does not
  make a missing record appear. The reference tests pin this case explicitly
  because it is the one most likely to be read as a guarantee.
- No protection against a view that lies about its position. A published
  position is a claim by the view. Detecting a view whose position runs ahead of
  its contents requires recounting against the source, which is
  [reconciliation](reconciliation.md) work and is not on the query path.
- No correctness of the source position. If the reader obtains the source
  position from a stale replica, the comparison resolves confidently and wrongly.
- No help for a caller that discards the outcome. A view can return three
  distinguishable outcomes to a caller that checks only whether the result list
  is non-empty, which is the same failure mode
  [postcondition-typed-outcomes](postcondition-typed-outcomes.md) has with exit
  status.
- No coverage of ordering faults. Bounding lag says nothing about updates
  arriving out of order, and a view that consumed positions 1 and 3 can publish
  position 3 while missing 2 unless the consumer refuses gaps.

## Failure drill

[source-advances-view-answers-anyway](../drills/source-advances-view-answers-anyway/):
the view is held at the one moment it is provably current, the source then
advances, and the update carrying the new record is dropped. The drill is
executable against the in-memory simulator in both modes.

```
python3 src/adapters/in_memory/run_drill.py source-advances-view-answers-anyway --mode protected
python3 src/adapters/in_memory/run_drill.py source-advances-view-answers-anyway --mode unsafe
```

The protected arm compares the view's published position against a freshly
computed source position, answers `lag-exceeded` naming both, and reports health
`stale` with the lag. The unsafe arm answers from the index alone, returns
`results-complete` with an empty match list over a record that is present at the
destination, and reports health `fresh` because the indexer started. The oracle
checks both halves independently: a complete-looking answer over a record the
view lacks is a violation, and so is a `fresh` verdict while the recomputed lag
exceeds the declared contract. Each half was shown to fail on its own by
mutating the protected path for that half only, with the other half staying
green. Evidence for both arms lands in `out/evidence/`.

## Evidence

- Five friction records written by one agent inside a 46 minute window on
  2026-08-16, all naming an unusable prior-session search over a lexical index
  months behind, with manual reconstruction from other stores as the fallback:
  local observation (gascity2026:.papercuts.jsonl, ids pc_843e91e18367,
  pc_3f2f2d85ca18, pc_f6c929da6db5, pc_0bb3136d0c73, pc_9d506c39742a).
- Health reporting the index stale with a last-indexed date of 2026-04-22 while
  an exact search for a known term returned zero hits, so the freshness signal
  existed and was not on the query path: local observation (same source, id
  pc_9d506c39742a).
- One rebuild printing "index completed" and "index failed: out of memory" in
  the same run, after which one diagnostic reported "Index Status: OK" and the
  health command reported "index stale" and exited 1, both partly right, costing
  about fifteen minutes to untangle: local observation (same source, id
  pc_d89449dbe4e5).
- A view whose historical corpus was searchable while the most recent month was
  not, measured as 36886 matches for an old probe term against zero for a
  2026-08-08 phrase, so partial ingest left position and contents disagreeing:
  local observation (same source, id pc_d89449dbe4e5).
- The expensive failure class named as a success-shaped no-op, partial mutation,
  false empty result, stale assignment, or missing delivery receipt, with the
  recommendation that a health command prove a current read/write round trip
  rather than check that a process or job is running, and that an empty result
  never be printed as a successful absence when the query was not trustworthy:
  local observation
  (gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md, section 7 and
  meta-pattern).
- A load-bearing claim about system state must carry the command that would
  refute it, and that command must be the one that can refute it rather than a
  related one, because a claim that was true when written is indistinguishable
  from one that is true now at every site except the one that re-measures: local
  observation (gascity2026:CLAUDE.md).
- A freshness check that selected, by accident of directory order, the one file
  in the tree that cannot change while the server is running, and therefore
  reported static over a window in which a live database was being written:
  local observation (gascity2026:CLAUDE.md). The instrument inverted on exactly
  the case it existed to catch, which is the argument for recomputing the source
  position from the source on every call rather than from a proxy.
- Read-your-writes and monotonic reads as the consistency guarantees a lagging
  replica violates, and routing a read by its freshness requirement as the
  standard remedy: foundational (Kleppmann 2017, Designing Data-Intensive
  Applications, ch. 5).
- The three-outcome query vocabulary, the requirement that every health surface
  be computed from the same two positions, and the refusal to answer outside the
  contract: inference (our synthesis; the failure shapes are observed and the
  composition executes only against the in-memory simulator).

## Limits

The installation supplying every observation on this page fails this pattern in
the system the observations come from. Its search surface returns empty results
without consulting a freshness signal that the same installation computes
correctly on a different command. That is the strongest available evidence that
the pattern names something real, and it is also the reason nothing here may be
read as a description of a working system. The three-outcome query vocabulary
executes in the simulator and nowhere else.

The two agent-recorded clusters are one tool over about six hours, not a survey.
Five of the six records come from a single agent inside a 46 minute window, so
they are five recordings of one encounter rather than five independent
encounters, and the count measures how often that agent recorded friction rather
than how widespread the failure is. The sixth is a different agent on the
following day against the same tool. Treating this as evidence about derived
views in general is an extrapolation from one index.

Obtaining a source position on every query is a cost this kit does not measure.
The simulator computes it by counting a list. A real reader is making an
additional call to the source, on the read path the view exists to keep off the
source, and a system under load will be tempted to cache or sample that
position. A sampled source position converts this pattern into a freshness claim
with an unstated error rate, which is the failure the pattern was written
against.

Position comparison assumes the source has a position to compare against. A
single ordered log makes this easy. A view fed by several sources, or by a
source with no total order, has a vector of positions and no obvious scalar lag,
and none of the sources here exercise that case. The honest form is a
per-source position with the contract applied to each, which is written down and
not implemented.

The pattern does not address what a caller should do with a refusal, and the
answer is not uniform. Falling back to the source is correct for a cheap exact
lookup and impossible for the aggregate the view exists to serve. An agent that
receives `lag-exceeded` and treats it as an error to retry around will re-derive
the work the empty result would have caused it to re-derive, one step later.
Making the refusal actionable is the caller's design problem, and this page only
guarantees the caller knows which regime it is in.

## Sources

- gascity2026:.papercuts.jsonl
- gascity2026:.gc-reports/gc-tooling-ergonomics-2026-08-17.md
- gascity2026:CLAUDE.md
- Kleppmann 2017, Designing Data-Intensive Applications, ch. 5 (replication lag,
  read-your-writes, monotonic reads)
- Related patterns: [explicit-unknown-state](explicit-unknown-state.md),
  [postcondition-typed-outcomes](postcondition-typed-outcomes.md),
  [reconciliation](reconciliation.md),
  [promise-oriented-observability](promise-oriented-observability.md),
  [verify-before-publish](verify-before-publish.md)
- Executable drill: [source-advances-view-answers-anyway](../drills/source-advances-view-answers-anyway/DRILL.md)
