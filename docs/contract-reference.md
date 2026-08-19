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
| `effects` | no | one entry per class of external mutation, with its identity, retry contract, and unknown-state policy | `EFFECT-000` to `EFFECT-006`, `IDENT-002` |
| `reconciliation` | no | the level-triggered loops that reread external truth | `RECON-001`, `RECON-002` |
| `scheduling` | no | how execution capacity is partitioned and made fair | `FLEET-001` to `FLEET-003` |
| `code_estate` | no | how the factory names the code it operates on (repository plus revision) | `CODE-000` |
| `campaigns` | no | the completion rule for multi-target campaigns | `CAMP-001` |
| `observability` | no | which lifecycle promises are watched, and against what thresholds | `OBS-001` to `OBS-003` |

Absence is never a silent pass. A section you omit produces the not-declared
finding under the rule that governs it, because "we have not decided" and "we
decided it is safe" are different states and only one of them is defensible in
an incident review.

## Rule catalog

Twenty-seven rules. FAIL means the contract describes a system with an open
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
| `EFFECT-005` | FAIL | an effect declares `retry_contract: at_least_once` with no `duplicate_disposition`, so a repeat is known to land as a second copy and nothing states what that costs | [effect-identity](../patterns/effect-identity.md) |
| `EFFECT-006` | FAIL | some of an effect's call sites are agent instructions rather than code, so no static check can establish that those routes carry the identity; the declaration holds only where the code performs the effect | [effect-identity](../patterns/effect-identity.md) |

Accepted `retry_contract` values are `deduplicate`, `converge`, `reconcile`,
and `at_least_once`. The `unknown_state_policy` vocabulary is six values and
splits in two: `block_and_escalate`, `reconcile_then_block`, and `manual_review`
are sound and pass; `assume_success` and `assume_failure` are accepted so a
factory can record that it does one of them, and `EFFECT-003` fails both; and
`unknown` records indecision, which `EFFECT-003` also fails. Anything outside
those six fails as unrecognised rather than passing by default -- being absent
from the list of known-bad values is not the same as being good.

#### When the destination has no dedup property

`deduplicate` is a claim about the **destination**: it stores the identity with
the effect and returns the original receipt for a repeat. Plenty of real
destinations have no such property. Posting a chat message twice posts twice;
the API takes no idempotency key and would ignore one.

For those, the first three values are all false and the honest-looking
alternative is `unknown`, which says *the builder has not decided*. That is the
wrong record. The builder decided, and the answer is that repeats duplicate.

`at_least_once` is that answer. It is a decided value, and `EFFECT-005` requires
`duplicate_disposition` beside it: what a second copy does at the destination
and what bounds it.

What `EFFECT-005` checks is that the field is filled in with something other
than `unknown`. It does not, and cannot, check that the sentence is true or that
the bound it names is real -- reading `a second copy posts as a new message` and
deciding whether the factory can live with that is a human's job, and a rule
that scored the prose would be guessing. So a green `EFFECT-005` means the cost
is *written down where a reviewer will see it*, which is the whole claim. The
review checklist in `docs/reviews/effect-boundary-review.md` is where the
sentence gets read.

```yaml
- name: slack_publish
  destination: messaging
  effect_identity: idempotency_key
  retry_contract: at_least_once
  duplicate_disposition: >-
    a second copy posts as a new message in the channel; the publisher replays a
    delivered receipt for the same key for two minutes in one process, which
    covers a request-timeout retry and nothing longer
  unknown_state_policy: block_and_escalate
```

A caller-side cache is not `deduplicate`, and the distinction is the same one
`AUTH-002` makes about fences: a guarantee enforced by the caller is only as
durable as that process. Write the cache in `duplicate_disposition`, where it is
visible as a bound on the damage, rather than in `retry_contract`, where it
reads as a property the destination does not have.

#### The two unsound policies are writable on purpose

`assume_success` and `assume_failure` are accepted by both schemas, and
`EFFECT-003` fails them. That pairing is deliberate and it is the same argument
as `at_least_once` above.

Refusing the value at the schema does not stop a factory from assuming failure.
It stops the factory from *saying* it does. The contract then has to record
`unknown`, which means the builder has not decided -- so the builder who traced
the retry path, found an unconditional requeue after a timeout, and wrote it
down produces the same record as the builder who never opened the file. The
information that would have caused the fix is the information the refusal
destroys.

There is a second cost, and it is the one that hid this for as long as it did.
`review` validates against the schema before it runs any rule, so while the two
values were schema-invalid, `EFFECT-003`'s unsound branch could not execute:
every document carrying the value it tests for was rejected upstream, and every
document that reached the rule was one the rule had nothing to say about. The
catalog listed the severity as live. A rule whose branch cannot be taken reads
exactly like a rule that never finds anything.

So the ordering to keep in mind when adding any rule here: a value the schema
refuses is a value the rules never see. Put structural shape in the schema and
every judgement about whether the shape is *safe* in the rules, where the
finding can carry a reason and a hint.

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

#### Two derived fields that are observations, not guarantees

`effect_identity` says the identity is carried unchanged across retries at every
route that performs the effect. On an agent-driven factory some routes are
sentences in a prompt, and nothing static can read an argument list that does not
exist until an agent writes one at run time, so `infer` leaves the field
`unknown` there. It is a limit on what a scanner can establish, not a finding
that the agent omits the identity: an instruction can name the flag, and often
does.

Reporting only that would tell a reader nothing is established anywhere, so the
narrower reading is recorded beside it in two fields `infer` writes:

| field | what it says |
|---|---|
| `code_lane_identity` | the identity every readable scripted call site carries, or `unknown` |
| `instructed_call_sites` | how many routes are agent instructions rather than code |

Neither is a guarantee, and both are checked rather than trusted. `EFFECT-006`
fails on any nonzero `instructed_call_sites`, and `reconcile` compares a declared
value in either field against a fresh scan and reports `STALE` when they
disagree. Without that comparison the pair would be self-clearing: the review
rules read the contract, so a hand author could write `instructed_call_sites: 0`
and go green with the installation untouched, which is the hand-edit-the-
declaration move the whole tool exists to catch. An omitted field is not a
contradiction; it means nobody measured, which is honest and is not the same as
zero.

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
| `OBS-002` | WARN | a promise is declared with no objective, so nothing can breach it | [promise-oriented-observability](../patterns/promise-oriented-observability.md) |
| `OBS-003` | WARN | an objective names a transition that is not promised | [promise-oriented-observability](../patterns/promise-oriented-observability.md) |

The six canonical promises are `ready_to_claim`, `claimed_to_started`,
`started_to_progress`, `completed_to_verified`, `verified_to_published`, and
`published_to_acknowledged`. Each is a bounded expectation that one state
leads to the next, so a stall between two states is alertable without any
component reporting an error.

`observability.objectives` is where a promise stops being a dashboard. A
transition with no threshold cannot be breached, so a contract can list all six
and still watch nothing, and a review that counts promises reads that as
coverage. An objective is one percentile and one duration per promise, and the
duration carries an explicit unit: `p95: 30m`, never `p95: 30`. A bare number is
refused rather than assumed to be seconds, because an objective is written once
and re-read rarely, and a threshold whose unit was guessed fires constantly and
gets switched off.

Both objective rules are WARN and neither will ever be FAIL. An undeclared
promise is a transition nobody is looking at; an undecided threshold is a
transition somebody is looking at without having decided what bad means. Those
are different pieces of work.

`OBS-003` is the same defect pointing the other way and is the more misleading
of the two: a threshold left behind by a withdrawn promise makes the contract
read as more watched than it is. The schema says every `objectives` key must
also appear in `promises` and cannot enforce it, because JSON Schema has no
cross-field constraint, so until this rule it was documentation with nothing
behind it.

Adding these two rules moves the score of every contract written before they
existed, without any of those factories changing behaviour. That is deliberate
and it is not the same as editing a contract to turn a finding green: the
promises really are unwatched, and the new WARN says so where the old silence
did not. It is a WARN precisely so it reports the gap without gating anything.

## Practicing on the unsafe example

[`examples/unsafe-factory.yaml`](../examples/unsafe-factory.yaml) is a
plausible-looking contract carrying six serious defects. It validates cleanly
and reviews at `6 FAIL, 11 WARN`. See
[`examples/README.md`](../examples/README.md) for the version of the exercise
where you find them before the checker does.
