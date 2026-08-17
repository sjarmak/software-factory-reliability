-- Factory observability queries over the event conventions in
-- semantic-conventions.md. Portable SQL: plain DDL, common table
-- expressions, and standard window functions only. Interval literals use
-- the SQL standard form (INTERVAL 'n' MINUTE). Adjust the spelling if your
-- engine differs. Thresholds are the tunable defaults from
-- promise-latencies.md.

CREATE TABLE events (
    ts               TIMESTAMP     NOT NULL,
    event            VARCHAR(64)   NOT NULL,
    work_id          VARCHAR(64)   NOT NULL,
    campaign_id      VARCHAR(64),
    generation       INTEGER,
    attempt_id       INTEGER,
    prior_attempt_id INTEGER,
    session_id       VARCHAR(64),
    host_id          VARCHAR(64),
    repository       VARCHAR(128),
    base_revision    VARCHAR(64),
    head_revision    VARCHAR(64),
    artifact_digest  VARCHAR(128),
    effect_id        VARCHAR(64),
    verification_id  VARCHAR(64),
    publication_id   VARCHAR(64),
    tenant_id        VARCHAR(64),
    lane             VARCHAR(16),
    conflict_key     VARCHAR(128),
    verdict          VARCHAR(16),
    reason           VARCHAR(512),
    owner            VARCHAR(64),
    receipt          VARCHAR(128),
    action           VARCHAR(64),
    step             VARCHAR(128),
    evidence         VARCHAR(512)
);

-- Query 1: ready work with no claim.
-- Promise: ready work gets claimed. Silence here means the dispatch layer
-- is dead, starved, or failing closed with the queue growing.
WITH last_ready AS (
    SELECT work_id, MAX(ts) AS ready_ts
    FROM events
    WHERE event = 'work.ready'
    GROUP BY work_id
),
claimed_after AS (
    SELECT DISTINCT r.work_id
    FROM last_ready r
    JOIN events e
      ON e.work_id = r.work_id
     AND e.event = 'work.claimed'
     AND e.ts >= r.ready_ts
)
SELECT r.work_id, r.ready_ts
FROM last_ready r
WHERE r.work_id NOT IN (SELECT work_id FROM claimed_after)
  AND r.ready_ts < CURRENT_TIMESTAMP - INTERVAL '15' MINUTE
ORDER BY r.ready_ts;

-- Query 2: claimed work with no execution.
-- Promise: claimed work starts. Silence here is a poisoned claim or a dead
-- assignee holding work no one is doing.
WITH last_claim AS (
    SELECT work_id, MAX(ts) AS claim_ts
    FROM events
    WHERE event = 'work.claimed'
    GROUP BY work_id
),
started_after AS (
    SELECT DISTINCT c.work_id
    FROM last_claim c
    JOIN events e
      ON e.work_id = c.work_id
     AND e.event IN ('execution.started', 'execution.attached')
     AND e.ts >= c.claim_ts
)
SELECT c.work_id, c.claim_ts
FROM last_claim c
WHERE c.work_id NOT IN (SELECT work_id FROM started_after)
  AND c.claim_ts < CURRENT_TIMESTAMP - INTERVAL '10' MINUTE
ORDER BY c.claim_ts;

-- Query 3: running work with no recent progress.
-- Promise: running work progresses. The clock resets only on
-- evidence-bearing events, never on process liveness.
WITH exec_activity AS (
    SELECT work_id, MAX(ts) AS last_activity_ts
    FROM events
    WHERE event IN ('execution.started', 'execution.attached',
                    'execution.progressed')
    GROUP BY work_id
),
left_running AS (
    SELECT DISTINCT work_id
    FROM events
    WHERE event IN ('artifact.prepared', 'work.blocked', 'work.acknowledged')
)
SELECT a.work_id, a.last_activity_ts
FROM exec_activity a
WHERE a.work_id NOT IN (SELECT work_id FROM left_running)
  AND a.last_activity_ts < CURRENT_TIMESTAMP - INTERVAL '30' MINUTE
ORDER BY a.last_activity_ts;

-- Query 4: external commitment with no durable acknowledgement.
-- The execute-then-log interval left open: the destination holds an applied
-- effect that no durable local record names. Recovery that cannot see the
-- receipt will retry the effect.
WITH committed AS (
    SELECT work_id, effect_id, MAX(ts) AS commit_ts
    FROM events
    WHERE event = 'effect.committed'
    GROUP BY work_id, effect_id
),
acknowledged AS (
    SELECT DISTINCT effect_id
    FROM events
    WHERE event = 'effect.acknowledged'
)
SELECT c.work_id, c.effect_id, c.commit_ts
FROM committed c
WHERE c.effect_id NOT IN (SELECT effect_id FROM acknowledged)
  AND c.commit_ts < CURRENT_TIMESTAMP - INTERVAL '5' MINUTE
ORDER BY c.commit_ts;

-- Query 5: verified artifact with no publication.
-- Promise: verified work publishes. This is the strand class: finished,
-- verified, never landed.
WITH passing AS (
    SELECT work_id, verification_id, artifact_digest, MAX(ts) AS verify_ts
    FROM events
    WHERE event = 'verification.completed'
      AND verdict = 'pass'
    GROUP BY work_id, verification_id, artifact_digest
),
published AS (
    SELECT DISTINCT work_id
    FROM events
    WHERE event = 'publication.committed'
)
SELECT p.work_id, p.verification_id, p.artifact_digest, p.verify_ts
FROM passing p
WHERE p.work_id NOT IN (SELECT work_id FROM published)
  AND p.verify_ts < CURRENT_TIMESTAMP - INTERVAL '60' MINUTE
ORDER BY p.verify_ts;

-- Query 6: publication referencing a different artifact than its
-- verification. The approval bound to one digest must not launder a
-- different artifact into the destination. Any row here is an incident,
-- with no time threshold.
SELECT p.work_id,
       p.publication_id,
       p.verification_id,
       p.artifact_digest AS published_digest,
       v.artifact_digest AS verified_digest,
       p.ts              AS published_ts
FROM events p
JOIN events v
  ON v.event = 'verification.completed'
 AND v.verification_id = p.verification_id
WHERE p.event = 'publication.committed'
  AND p.artifact_digest <> v.artifact_digest;

-- Query 7: current target with no campaign disposition.
-- A campaign is complete only when every target is published, blocked with
-- an owner, or exempted with a reason. Rows here are undispositioned
-- targets, defined in campaign-coverage.md.
WITH members AS (
    SELECT DISTINCT campaign_id, work_id, repository
    FROM events
    WHERE event = 'work.discovered'
      AND campaign_id IS NOT NULL
),
dispositioned AS (
    SELECT DISTINCT work_id
    FROM events
    WHERE event IN ('work.acknowledged', 'work.blocked')
       OR (event = 'work.reconciled' AND action = 'exempted')
)
SELECT m.campaign_id, m.work_id, m.repository
FROM members m
WHERE m.work_id NOT IN (SELECT work_id FROM dispositioned)
ORDER BY m.campaign_id, m.repository;

-- Query 8: work planned against a stale base revision.
-- In-flight work whose stamped base no longer matches the newest base
-- observed for its repository. Stale evidence produces confident,
-- incompatible output, so these rows need replanning, not patience.
WITH current_base AS (
    SELECT repository, base_revision
    FROM (
        SELECT repository, base_revision,
               ROW_NUMBER() OVER (PARTITION BY repository
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event = 'work.discovered'
          AND base_revision IS NOT NULL
    ) ranked
    WHERE rn = 1
),
work_repo AS (
    SELECT work_id, repository
    FROM (
        SELECT work_id, repository,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event = 'work.discovered'
    ) ranked
    WHERE rn = 1
),
in_flight AS (
    SELECT work_id, base_revision
    FROM (
        SELECT work_id, base_revision,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event IN ('execution.started', 'execution.attached')
    ) ranked
    WHERE rn = 1
),
finished AS (
    SELECT DISTINCT work_id
    FROM events
    WHERE event IN ('work.acknowledged', 'work.blocked')
)
SELECT f.work_id,
       w.repository,
       f.base_revision  AS planned_base,
       cb.base_revision AS current_base
FROM in_flight f
JOIN work_repo w   ON w.work_id = f.work_id
JOIN current_base cb ON cb.repository = w.repository
WHERE f.work_id NOT IN (SELECT work_id FROM finished)
  AND f.base_revision <> cb.base_revision
ORDER BY w.repository, f.work_id;

-- Query 9: two current workers claiming conflicting entities.
-- Two live claims on one conflict_key is unsafe concurrency the scheduler
-- should have prevented. Any row is an incident.
WITH latest_state AS (
    SELECT work_id, event
    FROM (
        SELECT work_id, event,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event IN ('work.claimed', 'execution.started',
                        'execution.attached', 'execution.progressed',
                        'work.blocked', 'work.acknowledged')
    ) ranked
    WHERE rn = 1
),
latest_claim AS (
    SELECT work_id, session_id, conflict_key
    FROM (
        SELECT work_id, session_id, conflict_key,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event = 'work.claimed'
    ) ranked
    WHERE rn = 1
),
active AS (
    SELECT c.work_id, c.session_id, c.conflict_key
    FROM latest_claim c
    JOIN latest_state s ON s.work_id = c.work_id
    WHERE s.event NOT IN ('work.blocked', 'work.acknowledged')
      AND c.conflict_key IS NOT NULL
)
SELECT a.conflict_key,
       a.work_id   AS work_a,
       a.session_id AS session_a,
       b.work_id   AS work_b,
       b.session_id AS session_b
FROM active a
JOIN active b
  ON a.conflict_key = b.conflict_key
 AND a.work_id < b.work_id
ORDER BY a.conflict_key;

-- Query 10: a tenant exceeding its capacity share.
-- Attribution question, so it runs over records, not metrics. The 0.40
-- share is policy. Replace it with the deployed allocation.
WITH latest_state AS (
    SELECT work_id, event
    FROM (
        SELECT work_id, event,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event IN ('execution.started', 'execution.attached',
                        'execution.progressed', 'artifact.prepared',
                        'work.blocked', 'work.acknowledged')
    ) ranked
    WHERE rn = 1
),
running AS (
    SELECT work_id
    FROM latest_state
    WHERE event IN ('execution.started', 'execution.attached',
                    'execution.progressed')
),
work_tenant AS (
    SELECT work_id, tenant_id
    FROM (
        SELECT work_id, tenant_id,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE tenant_id IS NOT NULL
    ) ranked
    WHERE rn = 1
),
per_tenant AS (
    SELECT t.tenant_id, COUNT(*) AS running_count
    FROM running r
    JOIN work_tenant t ON t.work_id = r.work_id
    GROUP BY t.tenant_id
),
shares AS (
    SELECT tenant_id,
           running_count,
           SUM(running_count) OVER () AS total_running
    FROM per_tenant
)
SELECT tenant_id,
       running_count,
       total_running,
       1.0 * running_count / total_running AS share
FROM shares
WHERE 1.0 * running_count / total_running > 0.40
ORDER BY share DESC;

-- Query 11: recovery traffic consuming the interactive reserve.
-- Recovery draws from its own budget. When recovery-lane executions exceed
-- that budget they are eating the reserve held for current work, which is
-- how a recovered outage causes a second one. total_slots and the 0.70
-- interactive reserve are policy values. Replace them with the deployed
-- split.
WITH capacity AS (
    SELECT 20 AS total_slots, 0.70 AS interactive_reserve
),
latest_state AS (
    SELECT work_id, event
    FROM (
        SELECT work_id, event,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event IN ('execution.started', 'execution.attached',
                        'execution.progressed', 'artifact.prepared',
                        'work.blocked', 'work.acknowledged')
    ) ranked
    WHERE rn = 1
),
running AS (
    SELECT work_id
    FROM latest_state
    WHERE event IN ('execution.started', 'execution.attached',
                    'execution.progressed')
),
work_lane AS (
    SELECT work_id, lane
    FROM (
        SELECT work_id, lane,
               ROW_NUMBER() OVER (PARTITION BY work_id
                                  ORDER BY ts DESC) AS rn
        FROM events
        WHERE event IN ('execution.started', 'execution.attached')
          AND lane IS NOT NULL
    ) ranked
    WHERE rn = 1
),
recovery_running AS (
    SELECT COUNT(*) AS recovery_count
    FROM running r
    JOIN work_lane l ON l.work_id = r.work_id
    WHERE l.lane = 'recovery'
)
SELECT rr.recovery_count,
       c.total_slots,
       c.total_slots * (1.0 - c.interactive_reserve) AS recovery_budget
FROM recovery_running rr
CROSS JOIN capacity c
WHERE rr.recovery_count > c.total_slots * (1.0 - c.interactive_reserve);
