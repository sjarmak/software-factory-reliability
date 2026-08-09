# Semantic Conventions

Event and identity conventions for factory observability. Events are
append-only JSON records; one event per line in transport and storage.
Timestamps are UTC, ISO 8601, field name `ts`. Every event names its kind in
the `event` field. The queryable table shape for these events is in
[sample-queries.sql](sample-queries.sql); a worked event stream is in
[sample-events.jsonl](sample-events.jsonl); the latency and alerting
interpretation is in [promise-latencies.md](promise-latencies.md).

Two rules govern the whole vocabulary.

Identity lives in records, not metric labels. Every field in the identity
table below is high-cardinality and belongs in events, traces, and queryable
stores. Metrics derived from these events may carry only low-cardinality
labels (event kind, lane, verdict, promise name). This split is established
observability practice (foundational).

Status is not outcome. An event asserts that its emitter recorded something,
not that the world changed. Gas City recorded 1384 audit events asserting
worktree creation that never happened (local observation,
gascity2026:docs/design/city-reliability-surface.md), and its design
doctrine draws the conclusion: the terminal check is artifact movement, not
recorded state (local observation,
gascity2026:docs/design/software-factory-philosophy.md). The
`work.acknowledged` event exists to carry system-of-record evidence for
exactly this reason.

## Identity fields

| Field | Type | Meaning |
|---|---|---|
| `campaign_id` | string | Identity of a multi-repository intent. Present on every event belonging to a campaign child. Absent on standalone work. |
| `work_id` | string | Stable identity of one logical work item. Never reused. The unit of claims, dispositions, and recovery. |
| `generation` | integer | Ownership epoch of the work item, monotonic per `work_id`. Increments when ownership is reassigned or the item is replanned. Fenced writes compare generations; a lower generation loses. |
| `attempt_id` | integer | Execution attempt within a generation, monotonic. A retry is a new attempt of the same logical work, never a new `work_id`. Prevents a late result from an expired attempt overwriting a successful retry. |
| `session_id` | string | Logical execution session. Stable across worker death and reattachment; the durable name recovery uses to find surviving execution state. Not a vendor transcript identity and not a process address. |
| `host_id` | string | Host or runtime placement of the acting process. Diagnostic, never authoritative; a PID or host is an address, not an identity. |
| `repository` | string | Repository the work targets. |
| `base_revision` | string | Repository state the work was planned against and is allowed to assume. Evidence without revision identity is stale, not current (agent-era, book ch12). |
| `head_revision` | string | Repository state the work produced, once an artifact exists. |
| `artifact_digest` | string | Content digest of the produced artifact. Approvals, verifications, and publications bind to this digest, not to intent. |
| `effect_id` | string | Stable logical identity of one external side effect, constant across retries. The destination deduplicates or reconciles on it. |
| `verification_id` | string | Identity of one verification execution. A verdict without a `verification_id` is an assertion, not a record. |
| `publication_id` | string | Identity of one fenced external commitment (merge, release, deploy). References the `verification_id` and `artifact_digest` it publishes. |

The generation and effect fields carry the load. Application-level fencing
on generation was the mechanism that rejected all stale writes in the
temporal-lab probes (0 of 30 obsolete publication actions accepted by
fenced arms versus 4 by unsafe arms; local observation,
temporallab2026:docs/guarantees.md), and a stable effect
identity plus destination-side deduplication was the only combination that
produced one physical effect across worker-death retries (18 of 18 unsafe
trials duplicated; local observation, same file).

## Attribution fields

Present where policy needs them; not identities.

| Field | Type | Meaning |
|---|---|---|
| `tenant_id` | string | Accounting owner of the work, for capacity-share policy. |
| `lane` | string | Admission budget the execution draws from: `interactive`, `batch`, or `recovery`. Recovery capacity is a separate budget (local observation, gascity2026:docs/design/retry-and-recovery-capacity.md). |
| `conflict_key` | string | Scheduler conflict entity claimed by this work (see [topology-aware-scheduling](../patterns/topology-aware-scheduling.md)). Two live claims on one key is an incident. |

## Event payload fields

Non-identity fields required by specific events: `prior_attempt_id`
(integer), `verdict` (string: `pass` or `fail`), `reason` (string), `owner`
(string), `receipt` (string, destination-issued), `action` (string),
`step` (string, progress marker), `evidence` (string, reference to the
observation backing the event).

## Lifecycle events

Every event requires `event`, `ts`, and `work_id`. `campaign_id` is required
on every event of a campaign child. The additional required fields below are
per event kind. Emitters may add fields; consumers must ignore unknown
fields.

| Event | Emitted when | Additional required fields |
|---|---|---|
| `work.discovered` | A discovery pass identifies a target and mints the work item. | `repository`, `base_revision` |
| `work.ready` | Dependencies are satisfied; the item is eligible for dispatch. | `generation` |
| `work.claimed` | An owner wins the claim transaction. | `generation`, `session_id`; `conflict_key` when the scheduler holds conflict edges for it |
| `execution.started` | The first attempt of a claim begins executing. | `generation`, `attempt_id`, `session_id`, `host_id`, `base_revision`, `lane` |
| `execution.attached` | A new attempt adopts the surviving execution session after worker loss, instead of launching a competitor. | `generation`, `attempt_id`, `prior_attempt_id`, `session_id`, `host_id`, `lane` |
| `execution.progressed` | The execution passes a nameable step with evidence. | `generation`, `attempt_id`, `step` |
| `artifact.prepared` | A candidate artifact exists with a content digest. | `generation`, `attempt_id`, `artifact_digest`, `base_revision`, `head_revision` |
| `effect.dispatched` | An external mutation is sent, before its result is known. | `generation`, `attempt_id`, `effect_id` |
| `effect.committed` | The destination reports the mutation applied. | `generation`, `effect_id`; `receipt` when the destination issues one |
| `effect.acknowledged` | The destination's receipt is durably recorded locally, closing the execute-then-log interval. | `effect_id` |
| `verification.completed` | An independent verification of a named artifact finishes. | `verification_id`, `artifact_digest`, `verdict` |
| `publication.committed` | The fenced terminal commitment succeeds at the destination. | `generation`, `publication_id`, `artifact_digest`, `verification_id` |
| `work.acknowledged` | The outcome is confirmed in the system of record (for code: the commit is an ancestor of the target branch). | `publication_id`, `evidence` |
| `work.blocked` | The item cannot proceed and is parked fail-closed. | `reason`, `owner` |
| `work.reconciled` | A level-triggered scan repairs or dispositions the item (reopen, reattach detection, exemption). | `generation`, `action`; `reason` when `action` is `exempted` |

Notes on specific events.

`execution.attached` exists because worker death does not imply execution
death. In the temporal-lab worker-death experiment, a stable application
session key let attempt 2 converge on the still-live child that attempt 1
launched, producing one executor, one effect, one outcome, where the unsafe
arm launched a second process (local observation,
temporallab2026:docs/findings/0001-worker-death-surviving-agent.md).
The event records the adoption: new `attempt_id`, same `session_id`.

`effect.acknowledged` is distinct from `effect.committed` because the
interval between an external commitment and its durable local record is the
crash window that cannot be closed, only made safe (agent-era, book ch08).
An `effect.committed` with no `effect.acknowledged` is the precise shape of
the recovery hazard, and has its own query in
[sample-queries.sql](sample-queries.sql).

`work.blocked` requires `reason` and `owner` because a fail-closed state
with no escalation path self-erases (local observation,
gascity2026:docs/design/software-factory-philosophy.md).

`work.reconciled` with `action` set to `exempted` is how a campaign target
is deliberately excluded; the exemption is a disposition decision made by
the reconciliation layer, and it must carry its `reason`. See
[campaign-coverage.md](campaign-coverage.md).

Events are for latency; scans are for correctness. Consumers must not treat
this stream as reliable delivery: real transports drop events silently
(local observation,
gascity2026:docs/design/city-reliability-surface.md). Every promise in
[promise-latencies.md](promise-latencies.md) is therefore checked by
querying the durable record, and a missing event past its bound is an alert,
never an assumed-fine.
