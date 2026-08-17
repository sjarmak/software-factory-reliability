# Architecture review checklist

Scope: identity, authority, and the separation of facts, procedure, effects,
and policy. Each question carries the review rule id it maps to (from the
automated catalog) or the drill it corresponds to. Automated findings carry
rule id, severity, a message stating the defect, and a one-line remediation
hint; a run exits 1 on any FAIL, or on any WARN under --strict, else 0.

1. Are `work.logical_identity`, `work.attempt_identity`, and
   `work.session_identity` all declared, and are they three mutually distinct
   fields rather than aliases of one value? [IDENT-001]
2. Pick one retry path and trace it: where could a session identity or an
   attempt identity be used in place of the logical identity? A retry that
   keys on attempt identity creates parallel logical work. [IDENT-001]
3. Does any declared effect identity embed or reference the attempt identity?
   If so, every retry mints a new effect id and destination deduplication is
   dead on arrival. [IDENT-002]
4. Does `work.ownership` declare both a generation and a lease expiry, and is
   the generation monotonic across ownership transfers? A lease without a
   generation cannot fence a paused writer that wakes after expiry.
   [AUTH-001]
5. Is `ownership.fence.enforced_by` the destination (publisher, destination,
   or store), and is `fence.operation` compare-and-set or transactional? A
   caller-side check or a read-then-write sequence is a time-of-check to
   time-of-use hole: the world can change between the read and the write.
   [AUTH-002]
6. Show the rejection path: when a stale generation writes, what record is
   produced, and does it name the stale generation? Walk the generation-7
   late-return scenario end to end. [drill stale-writer-completes]
7. After a worker dies mid-claim, does the retry resolve the current logical
   claim and attach to the running session, and which durable record makes
   that start-or-attach lookup possible? [drill worker-dies-agent-survives]
8. Is there a reconciliation entry that resolves the running session for the
   current claim, so recovery can discover an orphaned live session instead
   of racing it? [RECON-002]
9. Which component is the authority for work facts, and can execution-engine
   or workflow state ever contradict it? Name the winner and the code path
   that enforces the precedence. [drill worker-dies-agent-survives]
10. Is completion evidence derived from the destination (artifact landed,
    commit ancestry, independent readback) rather than from worker
    self-report? A declared verification block is the minimum; its absence
    means completion rests on the claim of the process being verified.
    [VERIFY-003]
11. Does `artifacts.verification.binds_to` equal `artifacts.identity`? A
    verdict bound to a branch, tag, or work-item id can be inherited by
    content that was never verified. [VERIFY-001]
12. Do `artifacts.publication.conditions` include both `current_generation`
    and `verification_matches_artifact`, and are both evaluated at
    publication time rather than at claim time? [VERIFY-002]
13. Walk the swap scenario: verdict recorded for artifact A, the mutable
    reference moves to B, publication reads the reference. Which check stops
    B, and what does its refusal record contain?
    [drill artifact-changes-after-verification]
14. Are human approvals bound to the exact artifact identity and revision
    being approved, not to the work item or the intent? An approval that
    survives an artifact swap is the same defect as an inherited verdict.
    [VERIFY-001]
15. Do `observability.promises` cover all six lifecycle transitions in the
    normative contract? List any missing transitions; each one is a blind
    interval in which state can change without a promised observation.
    [OBS-001]
16. For each authority (facts, procedure, effects, policy): name the single
    owning component. Any state owned by two components, or by none, is a
    finding; write it against the closest matching rule with the defect
    stated. [IDENT-001]
