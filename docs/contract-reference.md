# Contract reference

The commands, the contract sections, and the full rule catalog that
`factory-check review` runs. Use this page when a review prints a rule id you
want to understand, or when you are deciding what to put in a section.

## Commands

Every command runs from a clean checkout with no install step.

| Command | What it does |
| --- | --- |
| `python3 src/factory_check.py init [target]` | writes a commented starter `factory.yaml` with `unknown` in every field it cannot decide for you; refuses to overwrite an existing file |
| `python3 src/factory_check.py validate <files...>` | schema-validates contract, campaign, guarantee, effect, and work-manifest documents; reports a recognized document type with no schema as SKIP rather than checking it against a schema it was never written to satisfy |
| `python3 src/factory_check.py review <file> [--strict] [--out DIR]` | runs the rule catalog below over one contract, prints FAIL findings before WARN findings, and writes `findings.json`; exits 1 on any FAIL, or on any WARN with `--strict` |
| `python3 src/factory_check.py render <file> [--out DIR]` | renders diagrams and tables from one contract |

`validate` answers "is this document well formed". `review` answers "does what
it says leave a known failure boundary open". A contract can be perfectly
valid and fail eleven rules; the deliberately unsafe example in
[`examples/`](../examples/) does exactly that.

## Contract sections

| Section | Required | What it declares | Rules |
| --- | --- | --- | --- |
| `version` | yes | fixed to `factory.reliability/v1` | schema only |
| `factory` | yes | the name of the factory this contract describes | schema only |
| `authorities` | no | which system is authoritative for facts, procedure, policy, and effects | `AUTH-000` |
| `work` | no | the three identity classes, the ownership generation, the lease, and the fence | `IDENT-001`, `AUTH-001`, `AUTH-002` |
| `artifacts` | no | artifact identity, verification binding, and publication conditions | `VERIFY-001` to `VERIFY-005` |
| `effects` | no | one entry per class of external mutation, with its identity, retry contract, and unknown-state policy | `EFFECT-000` to `EFFECT-004`, `IDENT-002` |
| `reconciliation` | no | the level-triggered loops that reread external truth | `RECON-001`, `RECON-002` |
| `scheduling` | no | how execution capacity is partitioned and made fair | `FLEET-001` to `FLEET-003` |
| `code_estate` | no | how the factory names the code it operates on (repository plus revision) | `CODE-000` |
| `campaigns` | no | the completion rule for multi-target campaigns | `CAMP-001` |
| `observability` | no | which lifecycle promises are watched | `OBS-001` |

Absence is never a silent pass. A section you omit produces the not-declared
finding under the rule that governs it, because "we have not decided" and "we
decided it is safe" are different states and only one of them is defensible in
an incident review.

## Rule catalog

Twenty-three rules. FAIL means the contract describes a system with an open
failure boundary. WARN means the contract leaves something undecided that will
be decided under pressure later.

### Identity

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `IDENT-001` | FAIL | no `work` section, an identity class is missing or `unknown`, or two classes share a field name | [stable-work-identity](../patterns/stable-work-identity.md) |
| `IDENT-002` | WARN | an effect keys its `effect_identity` on the attempt identity, so every retry mints a new effect key and no destination can deduplicate | [effect-identity](../patterns/effect-identity.md) |

### Authority

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `AUTH-000` | WARN | an authority plane (facts, procedure, policy, effects) is undeclared or `unknown`, so one layer can silently impersonate another | [fenced-authority](../patterns/fenced-authority.md) |
| `AUTH-001` | FAIL | `work.ownership` declares neither a claim generation nor a lease expiry, or leaves either undecided | [fenced-authority](../patterns/fenced-authority.md) |
| `AUTH-002` | FAIL | no fence is declared, the fence is enforced by the caller, the enforcer is not a destination-side component, or the operation is read-then-write | [fenced-authority](../patterns/fenced-authority.md) |

`AUTH-002` is the rule `make demo` dramatizes. A caller-side fence is a
time-of-check to time-of-use race with the reclaim it is supposed to stop, so
the rule accepts only `publisher`, `destination`, or `store` as an enforcer,
and only `compare-and-set` or `transactional` as an operation.

### External effects

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `EFFECT-000` | WARN | no `effects` section, so retry behavior at every external destination is unspecified | [effect-identity](../patterns/effect-identity.md) |
| `EFFECT-001` | FAIL | an effect has no decided `effect_identity`, so the destination cannot recognize a repeat | [effect-identity](../patterns/effect-identity.md) |
| `EFFECT-002` | FAIL | an effect's `retry_contract` is missing or `unknown`; retries redeliver work and the destination's behavior on a repeat is undefined | [effect-identity](../patterns/effect-identity.md) |
| `EFFECT-003` | FAIL | an effect's `unknown_state_policy` is undecided, `assume_success`, or `assume_failure`; one loses the effect and the other duplicates it | [explicit-unknown-state](../patterns/explicit-unknown-state.md) |
| `EFFECT-004` | WARN | an effect declares `retry_contract: reconcile` with no readback query, so a retry cannot ask the destination whether the prior attempt landed | [durable-intent](../patterns/durable-intent.md) |

Accepted `retry_contract` values are `deduplicate`, `converge`, and
`reconcile`. Accepted `unknown_state_policy` values are `block_and_escalate`,
`reconcile_then_block`, and `manual_review`.

#### `effect_identity` is prose; `effect_identity_key` is a token

`effect_identity` is a human sentence describing what identifies the effect at
the destination. Plenty of real identities are composites worth a sentence: a
push is identified by the remote, the ref, and the commit oid together, and no
single field names it.

`reconcile` compares against a probe pack's `identity.name`, which is the token
a scanner binds at call sites. Those are two vocabularies. Comparing them
directly confirms only when the author happened to guess the token, and can
never confirm a composite. That is not a cosmetic wrong answer: on those
effects, marking the last unmarked call site does not move the verdict, which is
doing the right thing and the reading not changing.

So write the sentence in `effect_identity` and, when a single token carries it
through the code, name that token in `effect_identity_key`:

```yaml
- name: git_push
  effect_identity: >-
    the (remote, ref, intended commit oid) triple; the oid is what the readback
    compares, so the identity survives the branch being force-updated
  effect_identity_key: expected_remote_ref
```

Reconcile compares the key when one is named and the prose otherwise. A prose
identity with no key reads `UNVERIFIED` and prints the exact line to add; it
never reads `DRIFT` for being a sentence.

The field is a way to say what you meant, not a way to declare yourself correct.
A key the call sites do not carry is the sharpest `DRIFT` there is. A key beside
a *different* key-shaped `effect_identity` is a contradiction and reports as
drift rather than being silently preferred. `IDENT-002` reads both fields, so an
attempt-scoped identity cannot enter through the key.

**What a confirmation on a named key does not cover.** A static scan can check
that every call site carries the token. It cannot check that the token's runtime
*value* is the identity the prose describes: a fresh execution nonce declared as
`idempotency_key` confirms here and is unstable across every retry. Reconcile
prints that limit on exactly those rows. Nothing in this kit closes it, and a
future version that claimed to would be lying about what a scanner can see.

### Artifacts and publication

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `VERIFY-001` | FAIL | `verification.binds_to` is not the artifact identity, so the verdict can outlive the thing it verified | [verify-before-publish](../patterns/verify-before-publish.md) |
| `VERIFY-002` | FAIL | no publication conditions, or the conditions omit `current_generation` or `verification_matches_artifact` | [verify-before-publish](../patterns/verify-before-publish.md) |
| `VERIFY-003` | WARN | no `artifacts.verification` block at all, so completion rests on worker self-report | [verify-before-publish](../patterns/verify-before-publish.md) |
| `VERIFY-004` | FAIL | `artifacts.identity` names a mutable reference such as a branch or a tag; whatever it points at can change between verification and publication | [verify-before-publish](../patterns/verify-before-publish.md) |
| `VERIFY-005` | FAIL | the verification identity names worker testimony; the worker's claim of completion is the hypothesis, not the observation that establishes it | [falsifiable-checks](../patterns/falsifiable-checks.md) |

### Reconciliation

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `RECON-001` | WARN | no reconciliation entry covers an effect destination, so the factory trusts its cached belief about that system's state | [reconciliation](../patterns/reconciliation.md) |
| `RECON-002` | WARN | nothing resolves the running session for the current claim, so a retry cannot start-or-attach and can only launch blind | [start-or-attach](../patterns/start-or-attach.md) |

Coverage is matched on an entry's explicit `destination` when it has one.
Entries without a declared destination fall back to token overlap between the
entry's `fact` and `query` and the effect's `name` and `destination`, which is
weaker evidence in both directions; declare the destination.

### Fleet and capacity

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `FLEET-001` | WARN | no recovery class with a decided numeric maximum, so a retry storm can occupy every slot in the pool | [topology-aware-scheduling](../patterns/topology-aware-scheduling.md) |
| `FLEET-002` | WARN | no class reserves capacity for interactive work, so human-facing work queues behind bulk and recovery load | [topology-aware-scheduling](../patterns/topology-aware-scheduling.md) |
| `FLEET-003` | WARN | no fairness levels declared, so a single tenant or repository can hold the whole pool | [topology-aware-scheduling](../patterns/topology-aware-scheduling.md) |

### Campaigns and code estate

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `CAMP-001` | FAIL | campaign completion is child-task based rather than disposition based over current targets, so a target discovered after kickoff never blocks completion | [cross-repo-campaigns](../patterns/cross-repo-campaigns.md) |
| `CODE-000` | WARN | campaigns are declared with no decided `code_estate`, so discovery and completion cannot be checked against current repository state | [cross-repo-campaigns](../patterns/cross-repo-campaigns.md) |

A disposition is one of `published`, `exempted`, or `blocked_with_owner`.
Anything else leaves a target unaccounted for.

### Observability

| Rule | Severity | Fires when | Pattern |
| --- | --- | --- | --- |
| `OBS-001` | WARN | a canonical lifecycle promise is unwatched | [promise-oriented-observability](../patterns/promise-oriented-observability.md) |

The six canonical promises are `ready_to_claim`, `claimed_to_started`,
`started_to_progress`, `completed_to_verified`, `verified_to_published`, and
`published_to_acknowledged`. Each is a bounded expectation that one state
leads to the next, so a stall between two states is alertable without any
component reporting an error.

## Practicing on the unsafe example

[`examples/unsafe-factory.yaml`](../examples/unsafe-factory.yaml) is a
plausible-looking contract carrying six serious defects. It validates cleanly
and reviews at `6 FAIL, 10 WARN`. See
[`examples/README.md`](../examples/README.md) for the version of the exercise
where you find them before the checker does.
