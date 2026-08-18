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

The review prints each finding as a severity and rule id followed by the
defect and a remediation hint. The first finding today:

```
FAIL AUTH-002
  Fence enforced by the caller: between the caller's ownership check and its
  write the claim can change hands, a time-of-check to time-of-use race.
  Move enforcement to the destination side (publisher, destination, or
  store), which evaluates the generation atomically with the write.
```

and the totals line for this contract is:

```
6 FAIL, 10 WARN
```

Exit status is nonzero on any FAIL. Every rule id maps to a pattern page, and
[docs/contract-reference.md](docs/contract-reference.md) is the index: what
each of the twenty-three rules checks, which contract path it lands on, and
which pattern explains it. The totals are pinned by the test suite, so if your
checkout prints different numbers, the catalog changed and this page is what
needs updating.

If you would rather find the defects yourself before the checker names them,
[examples/README.md](examples/README.md) turns this contract into an exercise
with the answer key folded away.

Older write-ups quote **6 FAIL, 9 WARN** for this contract. That was correct
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
`git push` is a description of a call site rather than one. Re-run with
`--exclude <dir>` for each directory that is output, until the numbers describe
your code.

Then derive:

```
python3 src/factory_check.py infer /path/to/your/factory --probes probes.yaml
```

Every identity will read `unknown`, because a generated pack declares none.
That is the starting state, not an error: an effect identity is a claim that
the DESTINATION can tell a repeat from a new request, and nothing in your
caller's code establishes it. Decide each one in `probes.yaml`, naming the
value and the flags that carry it, and re-run. The derivation confirms an
identity only when every scripted call site carries it and no site is an
instruction to an agent, since an instruction has no argument list until run
time and no static marker can bind it.

`out/factory.derived.yaml` is the result, with the reason for every value on
the line above it, and `out/evidence.json` holds the call sites a script can
read.

Once you have a hand-written contract as well, hold the two against each other:

```
python3 src/factory_check.py reconcile my-factory.yaml /path/to/your/factory --probes probes.yaml
```

It reports DRIFT where the contract claims an identity the call sites do not
carry, OPEN where both are undecided, UNVERIFIED where the installation
supports more than the contract claims, and UNDECLARED for an effect your
factory performs and your contract never mentions. Exit is nonzero on drift.

Run against the installation this kit was written against, the answer on
2026-08-18 was 3 drift, 2 open, 0 confirmed. Nothing confirmed, which is the
correct reading of a factory that performs four of these effects with no
written behaviour for a half-success.

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
