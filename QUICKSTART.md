# Quickstart

One session, under an hour: review a deliberately unsafe contract, compare it
with a clean one, render, run a fault drill in both modes, then start a
contract for your own factory.

Requirements: Python 3.10 or later, plus `pyyaml` and `jsonschema`
(`pip install pyyaml jsonschema`). Everything runs locally.

## 1. Validate the unsafe example

```
python3 cmd/factory-check/factory_check.py validate examples/unsafe-factory.yaml
```

Expected shape of the output:

```
examples/unsafe-factory.yaml: valid against schemas/factory.schema.json
```

Validation checks structure only. A contract can be schema-valid and still
promise nothing enforceable; that is the point of this example.

## 2. Review it

```
python3 cmd/factory-check/factory_check.py review examples/unsafe-factory.yaml
```

Expected shape of the findings (exact wording will differ; rule IDs are
stable):

```
FAIL AUTH-002   workers.migrator: authority fence is checked by the caller,
                not the destination; a stale writer that skips the check
                still lands writes
WARN VERIFY-001 promises.pr-merged: verification verdict is bound to a branch
                name, not a commit id; the branch can move after the verdict
FAIL CAMP-001   campaign.completion: completion is defined as "all children
                finished", not "no unverified targets remain"; a lost child
                ends the campaign early

2 FAIL, 1 WARN
```

Exit status is nonzero on any FAIL. Each rule ID maps to a pattern page:
AUTH-002 to `patterns/fenced-authority.md`, VERIFY-001 to
`patterns/verify-before-publish.md`, CAMP-001 to
`patterns/cross-repo-campaigns.md`.

## 3. Review a clean contract

```
python3 cmd/factory-check/factory_check.py review examples/issue-to-pr/factory.yaml
```

Expected:

```
0 FAIL, 0 WARN
```

Diff the two contracts to see what changed: where the fence is enforced, what
the verification verdict binds to, and how completion is defined.

## 4. Render it

```
python3 cmd/factory-check/factory_check.py render examples/issue-to-pr/factory.yaml
ls out/
```

Render writes a human-readable view of the contract (promises, boundaries,
evidence states) into `out/`. This is the artifact you hand to a reviewer who
will not read YAML.

## 5. Run a drill, both modes

```
python3 -m adapters.in_memory.run_drill stale-writer-completes --mode unsafe
```

Expected shape:

```
drill: stale-writer-completes  mode: unsafe
inject: writer A stalls after its effect; writer B claims and completes
violation: stale writer A's completion accepted after B's generation
           became current
evidence: out/evidence/stale-writer-completes-unsafe.jsonl
```

Exit status 2: the drill reproduced the violation, which is what the unsafe
mode is for. The retained evidence file is the ordered event log; read it to
see the exact acceptance of the stale write.

```
python3 -m adapters.in_memory.run_drill stale-writer-completes --mode protected
```

Expected shape:

```
drill: stale-writer-completes  mode: protected
outcome: stale completion rejected by generation check at the ledger
evidence: out/evidence/stale-writer-completes-protected.jsonl
```

Exit status 0. Same fault, same timing; the difference is a fence checked at
the destination. Keep both evidence files: the unsafe run is your proof that
the drill can detect the violation at all. A drill whose unsafe mode passes is
testing nothing.

## 6. Start your own contract

```
python3 cmd/factory-check/factory_check.py init my-factory.yaml
```

Then work the loop:

1. **Describe**: fill in the generated contract with your workers, promises,
   boundaries, and external systems, as they actually are today.
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
