# Quickstart: use this on your own factory

This is the adopt path. One session, under an hour: review a deliberately
unsafe contract, compare it with a clean one, render, run a fault drill in
both modes, then start a contract for your own factory.

If you have not run `make demo` yet, start with the
[README](README.md) instead. That is the learn path, and it takes five
minutes.

Requirements: Python 3.10 or later, plus the packages in
`requirements-dev.txt` (`pip install -r requirements-dev.txt`: pyyaml and
jsonschema for the CLI, pytest for the test suite). Everything runs locally,
with no install step and no `PYTHONPATH`.

## 1. Validate the unsafe example

```
python3 src/factory_check.py validate examples/unsafe-factory.yaml
```

Expected output:

```
examples/unsafe-factory.yaml: OK (factory.schema.json)
```

Validation checks structure only. A contract can be schema-valid and still
promise nothing enforceable; that is the point of this example.

## 2. Review it

```
python3 src/factory_check.py review examples/unsafe-factory.yaml
```

The review prints each finding as a severity and rule id, then the defect, a
remediation hint, and the place in the contract the hint is about. The first
finding today:

```
FAIL AUTH-002
  Fence enforced by the caller: between the caller's ownership check and its
  write the claim can change hands, a time-of-check to time-of-use race.
  Move enforcement to the destination side (publisher, destination, or
  store), which evaluates the generation atomically with the write.
  at work.ownership.fence.enforced_by
```

The last line is the one to act on first. This contract fails AUTH-002 twice,
and the two findings differ only in that line.

and the totals line for this contract is:

```
6 FAIL, 11 WARN
```

Exit status is nonzero on any FAIL. Every rule id maps to a pattern page, and
[docs/contract-reference.md](docs/contract-reference.md) is the index: what
each of the twenty-five rules checks, which contract path it lands on, and
which pattern explains it. The totals are pinned by the test suite, so if your
checkout prints different numbers, the catalog changed and this page is what
needs updating.

If you would rather find the defects yourself before the checker names them,
[examples/README.md](examples/README.md) turns this contract into an exercise
with the answer key folded away.

Older write-ups quote **6 FAIL, 9 WARN** for this contract. <!-- historical -->
That was correct
at v0.1 (`4744374`); `03e36b8` added the CODE-000 rule and the tenth warning
came with it. Until 2026-08-16 the suite pinned the rule multiset but nothing
compared it to the numbers printed on this page, so this line was updated by
hand and the published article was not. The catalog and its documentation
drifted with no mechanism that could notice. `test_published_counts_match_the_examples`
now reads the totals line out of this file, and out of every other page that
quotes one, which is the whole point the kit makes about cached beliefs,
applied to the kit.

## 3. Review a clean contract

```
python3 src/factory_check.py review examples/issue-to-pr/factory.yaml
```

Expected:

```
0 FAIL, 0 WARN
```

Diff the two contracts to see what changed: where the fence is enforced, what
the verification verdict binds to, and how completion is defined.

## 4. Render it

```
python3 src/factory_check.py render examples/issue-to-pr/factory.yaml
ls out/
```

Render writes a human-readable view of the contract (promises, boundaries,
evidence states) into `out/`. This is the artifact you hand to a reviewer who
will not read YAML.

## 5. Run a drill, both modes

```
python3 src/adapters/in_memory/run_drill.py stale-writer-completes --mode unsafe
```

Expected output:

```
drill stale-writer-completes (unsafe): oracle detected the expected
violation; evidence out/evidence/stale-writer-completes-unsafe.json
```

Exit status 2: the drill reproduced the violation, which is what the unsafe
mode is for. The evidence file holds the ordered per-tick event log, the
identity mappings, and the oracle's verdict; read it to see the stale
generation-7 write and completion being accepted after generation 8.

```
python3 src/adapters/in_memory/run_drill.py stale-writer-completes --mode protected
```

Expected output:

```
drill stale-writer-completes (protected): oracle pass; evidence
out/evidence/stale-writer-completes-protected.json
```

Exit status 0. Same fault, same timing; the difference is a fence checked at
the destination. Keep both evidence files: the unsafe run is your proof that
the drill can detect the violation at all. A drill whose unsafe mode passes is
testing nothing.

Both runs of this drill are committed, with a line-by-line reading, in
[evidence/case-studies/stale-writer/](evidence/case-studies/stale-writer/).
Diff your output against them; they are byte-for-byte reproducible.

## 6. Derive a contract from your own factory

The contract is hand-written, which means it can be edited to a decided value
while your factory stays exactly as it was. Findings go green and nothing is
fixed. Start from what your installation actually shows instead.

```
python3 src/factory_check.py probes-init /path/to/your/factory --write probes.yaml
```

This scans for effects a factory commonly performs on something outside itself
(pushes, pull requests, releases, deploys, messages, mail, object-store writes)
and writes a probe pack naming the ones it found and the files it found them
in. Read the two lists it prints. The first is the effects; the second is call
sites per top-level directory, and it is there because a scan cannot tell a
directory your factory WRITES from one it RUNS. In a generated report or a log,
`git push` is a description of a call site rather than one.

Paths your own VCS already calls output are skipped, which is most of that
problem solved from a declaration you already maintain rather than a list you
have to write. On the installation this kit was written against, that took the
scaffolded pack from 738 lines to 512, and what it dropped was agent working
sets, pipeline scratch and a graph dump -- files in which these commands were
recorded, not run. `--scan-ignored-paths` turns it off. If the check cannot run
at all -- no git, or not a repository -- the run says so in a `SCAN` line
rather than reporting that nothing is ignored, because those two are not the
same answer.

For directories your VCS tracks but your factory only writes, re-run with
`--exclude <dir>` until the numbers describe your code.

Then derive:

```
python3 src/factory_check.py infer /path/to/your/factory --probes probes.yaml
```

Every identity will read `unknown`, because a generated pack declares none.
That is the starting state, not an error: an effect identity is a claim that
the DESTINATION can tell a repeat from a new request, and nothing in your
caller's code establishes it. Decide each one in `probes.yaml`, naming the
value and the flags that carry it, and re-run. `effect_identity` is a
guarantee about every route that performs the effect, so the derivation fills
it in only when every scripted call site carries the identity and no site is an
instruction to an agent: an instruction has no argument list until run time, so
nothing static can establish what the agent will type. That is a limit on what
this tool can show, not a finding that the agent omits the identity -- an
instruction can name the flag, and often does.

The narrower reading is recorded beside it rather than lost. `code_lane_identity`
is what the code that performs the effect carries, and `instructed_call_sites`
is how many routes are prose. Both are observations, never guarantees, and
`reconcile` compares a declared value against a fresh scan, so writing
`instructed_call_sites: 0` by hand does not clear the finding it produces.

Under each effect are up to two lists, and they ask for different work. The
first names call sites missing the marker: those are edits. The second is
headed `review`, and holds matches the scan cannot read as invocations at all
-- a command named inside a quoted string, a wrapper assigning its own name.
They are set aside from the first list because sending you to add a flag to a
`sed` replacement string wastes the list's credibility, and they are not
dropped because the tool genuinely cannot tell them from a deferred command
built for later, which is the unfenced write it exists to find. So an effect
with entries in the review list stays `unknown` no matter how clean the first
list is. Read them, and add `not_regex` to that effect's matcher in
`probes.yaml` for the ones that are not invocations. That is the edit that
moves the reading.

`out/factory.derived.yaml` is the result, with the reason for every value on
the line above it, and `out/evidence.json` holds the call sites a script can
read.

The derived file also closes with the eight sections it did NOT produce, each
with the question it answers and the reason this tool does not answer it.
Three of the schema's eleven sections are produced -- `version`, `factory`,
`effects` -- because an effect's call sites are in your tree. Carried is not
the same as fully observed even for those three: `version` is a constant,
`factory.name` falls back to the directory basename, the effect names and
destinations come from the probe pack you wrote, and `retry_contract` and
`unknown_state_policy` are fixed at `unknown`. What is read off your
installation is the call sites, and whether each scripted one carries its
marker.

The other eight are left to you, for two different reasons the file states per
section. Some are decisions -- which system is the authority for durable facts,
when a campaign is complete -- and two installations with byte-identical files
can have decided them differently, so a derivation that produced them would be
guessing, which is the failure this kit exists to catch one layer up. Others
hold fields a scanner could observe with probes this kit does not have; that is
a limit on the tool, not a property of your factory, and the reason says which
case a section is.

Write them in a contract file of your own, not into `factory.derived.yaml`.
That file is regenerated from the scan, so an edit to it is gone on the next
run, and `reconcile` only ever holds the `effects` section against your
installation. Every section you hand-write is a claim; nothing in this kit
contradicts it.

Leaving a section out is not the same as declaring it `unknown`, and the
difference does not run the way you would want. Several rule groups return
early when their section is absent -- `campaigns` is the clearest: omit it and
you get no campaign finding at all, declare it undecided and you get several.
A missing section can score better than an honest one, so a short findings list
is not a clean bill of health.

That split is checked, not asserted. `_emit_derived_yaml` reads the schema and
raises if a section is in neither list, so a schema that gains a section stops
the run instead of writing a contract whose closing block silently
under-reports what it left to you.

Once you have a hand-written contract as well, hold the two against each other:

```
python3 src/factory_check.py reconcile my-factory.yaml /path/to/your/factory --probes probes.yaml
```

It reports DRIFT where the contract claims an identity the call sites do not
carry, OPEN where both are undecided, UNVERIFIED where the installation
supports more than the contract claims, and UNDECLARED for an effect your
factory performs and your contract never mentions. Exit is nonzero on drift.

It also checks the two fields a contract can copy from a derivation:
STALE where a declared `instructed_call_sites` or `code_lane_identity`
disagrees with a fresh scan, and UNRECORDED where the scan finds
agent-instruction routes the contract never mentions. STALE is nonzero exit;
UNRECORDED is not, because never having measured is honest and a contract that
writes a comfortable zero is what STALE is for.

Run against the installation this kit was written against, the answer on
2026-08-18 was 3 drift, 1 unverified, 1 confirmed, 0 open, and 5 effects
UNRECORDED covering 93 agent-instruction call sites. It read 3 drift, 2
open, 0 confirmed earlier the same day, and the change was in this tool rather
than in that factory: `confirmed` was not merely empty but unreachable for any
factory with an agent-instruction call site, which is every factory this kit
is addressed to.

That number is not pinned by the test suite, because it is a property of an
installation this repository does not contain. It is dated for the same reason
the FAIL/WARN counts above carry their history: a measurement quoted without a
date is indistinguishable from one that is still true. Re-derive rather than
trust it.

To start a contract by hand instead, or alongside:

```
python3 src/factory_check.py init my-factory.yaml
```

If step 1 below is where you stall, because the topology drifted, the store
disagrees with the world, or nobody can say which work items still exist,
start with [docs/recipes/factory-recovery.md](docs/recipes/factory-recovery.md) instead
and come back here with its inventory.

Then work the loop:

1. **Describe**: fill in the contract with your workers, promises, boundaries,
   and external systems, as they actually are today. For the effects, start
   from `infer` rather than from memory: the parts a scan can establish should
   not be recalled, and the parts it cannot are the ones worth your attention.
2. **Review**: `factory-check review my-factory.yaml` and read each finding
   against its pattern page.
3. **Fix**: move enforcement to the boundary the pattern names, or record an
   explicit exception with a reason.
4. **Drill**: run the executable drill nearest to each promise you rely on;
   write a drill spec for the ones the kit does not cover.
5. **Record guarantees**: for each promise, record its evidence state
   (declared, enforced, fault-tested) as a guarantee file validated by
   `schemas/guarantee.schema.json`, pointing at the retained drill evidence.

Repeat when the factory changes. A promise's evidence state is about the
current implementation, so a refactor at a boundary demotes fault-tested back
to declared until the drill runs again.
