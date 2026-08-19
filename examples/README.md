# Examples

Four worked factory contracts, one deliberately unsafe contract, and the
fixtures the drills and tests read.

## The exercise: find the defects before the checker does

[`unsafe-factory.yaml`](unsafe-factory.yaml) is a plausible-looking software
factory. It is schema-valid, it reads like a contract someone thought about,
and it contains six defects serious enough to fail review, plus ten more that
draw warnings.

Read it first. Give yourself five minutes and write down what you would
change.

```bash
python3 src/factory_check.py validate examples/unsafe-factory.yaml   # OK
```

Validation passes. Every defect in the file is semantic, so a schema cannot
see any of them.

### Where to look

Six questions, one per failing defect. Each maps to a place in the file.

1. **The fence.** A worker checks that it still owns the claim, then writes.
   What happens in the gap between those two operations?
2. **The fence, again.** Who evaluates the ownership generation, and is that
   evaluation part of the same operation as the write?
3. **The verification binding.** The artifact identity is a commit. CI reports
   its verdict against a branch. What can happen to the branch between those
   two facts?
4. **The publication conditions.** The publish rechecks the generation and the
   base revision. What does it not recheck?
5. **The notification effect.** Its retry contract is declared. What happens
   when an attempt's outcome cannot be determined at all?
6. **The campaign completion rule.** Completion is "every child task
   finished". Which targets does that sentence fail to mention?

Now run the review.

```bash
python3 src/factory_check.py review examples/unsafe-factory.yaml
```

It prints `6 FAIL, 11 WARN` and writes `out/findings.json`.

### The answer key

<details>
<summary>Six failures</summary>

| Rule | Contract path | The defect |
| --- | --- | --- |
| `AUTH-002` | `work.ownership.fence.enforced_by` | The caller checks its own ownership, then writes. Between the check and the write the claim can change hands, and the write then lands with nothing checking it. This is the failure `make demo` runs end to end. |
| `AUTH-002` | `work.ownership.fence.operation` | `read-then-write` is the same race stated as an operation. Only `compare-and-set` or a transaction makes the generation check and the write one atomic step. |
| `VERIFY-001` | `artifacts.verification.binds_to` | The artifact identity is `commit_sha`, but the verdict binds to `branch_name`. The branch can move after checks pass, so the verified content and the published content can differ while the evidence still reads green. |
| `VERIFY-002` | `artifacts.publication.conditions` | The conditions omit `verification_matches_artifact`, so a publish can ship an artifact whose evidence belongs to a different one. |
| `EFFECT-003` | `effects[1].unknown_state_policy` | The notification effect declares no unknown-state policy. The schema permits the omission so partial contracts can still be recorded; review fails it, because an undeclared ambiguity policy decays into whichever assumption the on-call person makes at 3am. |
| `CAMP-001` | `campaigns.completion` | Completion is defined as "every child task finished". That says nothing about targets discovered after kickoff, or about a task that silently dropped a target, so the campaign can read complete while targets sit untouched. |

</details>

<details>
<summary>Eleven warnings</summary>

| Rule | Contract path | What is undecided |
| --- | --- | --- |
| `IDENT-002` | `effects[0].effect_identity` | The pull-request effect identity embeds `attempt_id`, so every retry mints a fresh effect key and no destination can deduplicate. The comment above it ("each attempt gets its own operation key so retries are traceable") is exactly the reasoning that produces duplicate pull requests. |
| `EFFECT-004` | `effects[0].readback` | `retry_contract: reconcile` with no readback declared. The caller has no way to ask the destination whether the prior attempt landed before repeating it. |
| `RECON-001` | `effects[0].destination` | Nothing rereads the code host's actual state; the factory trusts its cached belief. |
| `RECON-001` | `effects[1].destination` | Same for the messaging destination. |
| `RECON-002` | `reconciliation` | Nothing resolves the running session for the current claim, so a retry cannot start-or-attach and can only launch blind. |
| `FLEET-001` | `scheduling.classes.recovery` | No recovery ceiling, so a retry storm can occupy every slot in the pool of 100. |
| `FLEET-002` | `scheduling.classes` | No reserved interactive capacity, so human-facing work queues behind the storm. |
| `FLEET-003` | `scheduling.fairness.levels` | No fairness levels, so a single tenant or repository can hold the whole pool. |
| `CODE-000` | `code_estate` | Campaigns are declared with no code estate, so discovery and completion cannot be checked against current repository state. |
| `OBS-001` | `observability.promises` | Four of the six lifecycle promises are unwatched (`started_to_progress`, `completed_to_verified`, `verified_to_published`, `published_to_acknowledged`), so a stall between any of those states is invisible. |
| `OBS-002` | `observability.objectives` | The two promises that ARE declared carry no threshold, so neither can be breached. A promise with no number is a dashboard, and a reviewer counting promises reads it as coverage. |

</details>

The counts are pinned by `tests/test_factory_check.py`, per rule and per
multiplicity. A rule change that moves them fails the suite until this page
and QUICKSTART.md are updated, which is the same mechanism the kit asks of
your factory.

## The worked contracts

| Example | Shape | Recipe |
| --- | --- | --- |
| [`minimal-factory.yaml`](minimal-factory.yaml) | the smallest contract worth reviewing; reviews at `5 FAIL, 8 WARN`, and the findings are the worklist | |
| [`issue-to-pr/`](issue-to-pr/) | one issue in, one verified pull request out; includes guarantee files and retained evidence | [issue-to-pull-request](../docs/recipes/issue-to-pull-request.md) |
| [`long-running-agent/`](long-running-agent/) | a session that outlives the process that launched it | [background-maintenance](../docs/recipes/background-maintenance.md) |
| [`cross-repo-migration/`](cross-repo-migration/) | one change fanned across many repositories, joined by coverage; includes a campaign file | [cross-repository-migration](../docs/recipes/cross-repository-migration.md) |

The three directory examples review clean (0 FAIL, 0 WARN). They are what a
contract looks like once the boundaries are closed, and diffing one of them
against `unsafe-factory.yaml` is a faster way to see the difference than
reading either alone.

```bash
diff examples/unsafe-factory.yaml examples/issue-to-pr/factory.yaml
```

## Fixtures

[`fixtures/`](fixtures/) holds the repository snapshots the campaign drills
and the coverage queries read: a multi-repository API migration and a
single-repository case. They are inputs to tests and documentation examples,
not contracts, so `factory-check validate` reports them as skipped rather than
checking them against a schema they were never written to satisfy.
