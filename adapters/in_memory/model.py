"""Tiny in-memory factory used by the reliability drills.

Deterministic by construction: time is one integer tick that advances only
when an event is appended to the log, and identifiers come from counters.
No wall-clock reads, no randomness, no threads.

Three layers that real factories often conflate are kept separate here:

  ledger       what the application believes (work items, claims, sessions,
               effect records)
  destination  external ground truth (mutations, the published artifact)
  delivery     the event bus between them, which can drop events

Basis note: the layer split mirrors the guarantee split observed in the
Temporal experiment series (local observation,
/home/ds/projects/temporal_projects/docs/guarantees.md): completion
cardinality at the orchestrator is not effect cardinality at the
destination, and the ledger can lag the destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WorkItem:
    """One unit of work; generation is the ownership identity, lease the
    capability currently derived from it."""

    work_id: str
    status: str = "pending"
    generation: int = 1
    lease: str | None = None


@dataclass
class Claim:
    """Binds a work item to the sessions acting on it."""

    claim_id: str
    work_id: str
    lease: str
    session_ids: list[str] = field(default_factory=list)


@dataclass
class Session:
    """A logical operation/session. It is not a process: the launching
    worker can die while the session's executor survives."""

    session_id: str
    work_id: str
    worker_id: str | None
    alive: bool = True


@dataclass
class Worker:
    worker_id: str
    alive: bool = True
    session_ids: list[str] = field(default_factory=list)


@dataclass
class Mutation:
    mutation_id: str
    effect_id: str
    payload: str
    receipt: str


class Destination:
    """External destination for effects and artifact publication.

    semantics 'dedup': honors effect identity; applying a known effect_id
    returns the original mutation and records nothing new.
    semantics 'none': blindly applies every request.
    """

    def __init__(self, semantics: str) -> None:
        if semantics not in ("dedup", "none"):
            raise ValueError(f"unknown destination semantics: {semantics!r}")
        self.semantics = semantics
        self.mutations: list[Mutation] = []
        self._by_effect: dict[str, Mutation] = {}
        self.artifact: dict | None = None

    def apply(self, effect_id: str, payload: str) -> tuple[Mutation, bool]:
        """Apply one effect request; returns (mutation, created)."""
        if self.semantics == "dedup" and effect_id in self._by_effect:
            return self._by_effect[effect_id], False
        n = len(self.mutations) + 1
        mutation = Mutation(f"mut-{n}", effect_id, payload, f"receipt-{n}")
        self.mutations.append(mutation)
        self._by_effect.setdefault(effect_id, mutation)
        return mutation, True

    def compare_and_publish(self, artifact_id: str, generation: int) -> bool:
        """Atomic compare on generation at the destination (fenced path)."""
        current = self.artifact["generation"] if self.artifact else None
        if current is not None and generation <= current:
            return False
        self.artifact = {"artifact_id": artifact_id, "generation": generation}
        return True

    def overwrite(self, artifact_id: str, generation: int) -> None:
        """Unconditional write (the unfenced path lands here)."""
        self.artifact = {"artifact_id": artifact_id, "generation": generation}


class EventBus:
    """Delivery layer with a drop switch. inject_fault(drop_event) arms it;
    the next emitted event is then dropped instead of applied."""

    def __init__(self) -> None:
        self.drop_remaining = 0

    def arm_drop(self) -> None:
        self.drop_remaining += 1

    def deliver(self) -> bool:
        if self.drop_remaining > 0:
            self.drop_remaining -= 1
            return False
        return True


class Factory:
    """Holds the ledger, the destination, the bus, and the ordered event
    log. Every state change goes through a method here so the per-tick log
    stays complete."""

    def __init__(self, destination_semantics: str, publisher_mode: str,
                 reconciler_enabled: bool) -> None:
        if publisher_mode not in ("fenced", "unfenced"):
            raise ValueError(f"unknown publisher mode: {publisher_mode!r}")
        self.tick = 0
        self.events: list[dict] = []
        self.work_items: dict[str, WorkItem] = {}
        self.claims: dict[str, Claim] = {}
        self.sessions: dict[str, Session] = {}
        self.workers: dict[str, Worker] = {}
        self.effect_ledger: dict[str, dict] = {}
        self.attempt_sessions: dict[int, str] = {}
        self.bus = EventBus()
        self.destination = Destination(destination_semantics)
        self.publisher_mode = publisher_mode
        self.reconciler_enabled = reconciler_enabled
        self._session_counter = 0
        self.attempt_ids: list[int] = []
        self.effect_ids: list[str] = []
        self.artifact_ids: list[str] = []
        self.generations_seen: list[int] = []

    def log(self, event_kind: str, /, **detail) -> None:
        self.tick += 1
        event = {"tick": self.tick}
        event.update(detail)
        event["kind"] = event_kind
        self.events.append(event)

    @staticmethod
    def _note(seq: list, value) -> None:
        if value not in seq:
            seq.append(value)

    # ledger operations

    def add_work(self, work_id: str, generation: int = 1) -> WorkItem:
        item = WorkItem(work_id=work_id, generation=generation)
        self.work_items[work_id] = item
        self._note(self.generations_seen, generation)
        self.log("work-added", work_id=work_id, generation=generation)
        return item

    def start_worker(self, worker_id: str) -> Worker:
        worker = Worker(worker_id=worker_id)
        self.workers[worker_id] = worker
        self.log("worker-started", worker_id=worker_id)
        return worker

    def kill_worker(self, worker_id: str) -> None:
        """The worker process dies. Sessions it launched keep running;
        their worker binding becomes None, which models an executor that
        outlives the process that launched it."""
        worker = self.workers[worker_id]
        worker.alive = False
        survivors = []
        for session_id in worker.session_ids:
            session = self.sessions[session_id]
            if session.alive:
                session.worker_id = None
                survivors.append(session_id)
        self.log("worker-killed", worker_id=worker_id,
                 surviving_sessions=survivors)

    def claim_work(self, work_id: str, attempt: int) -> Claim:
        item = self.work_items[work_id]
        lease = f"lease-g{item.generation}"
        item.lease = lease
        item.status = "claimed"
        claim = Claim(claim_id=f"claim-{work_id}", work_id=work_id, lease=lease)
        self.claims[work_id] = claim
        self._note(self.attempt_ids, attempt)
        self.log("work-claimed", work_id=work_id, claim_id=claim.claim_id,
                 lease=lease, generation=item.generation, attempt=attempt)
        return claim

    def launch_session(self, work_id: str, worker_id: str, attempt: int) -> Session:
        self._session_counter += 1
        session_id = f"sess-{self._session_counter}"
        session = Session(session_id=session_id, work_id=work_id,
                          worker_id=worker_id)
        self.sessions[session_id] = session
        self.workers[worker_id].session_ids.append(session_id)
        self.claims[work_id].session_ids.append(session_id)
        self.attempt_sessions[attempt] = session_id
        self._note(self.attempt_ids, attempt)
        self.work_items[work_id].status = "in_progress"
        self.log("session-launched", work_id=work_id, session_id=session_id,
                 worker_id=worker_id, attempt=attempt)
        return session

    def attach_session(self, work_id: str, session_id: str, attempt: int) -> None:
        """Bind a new attempt to an existing session without launching a
        second executor. The claim's session list does not grow."""
        self.attempt_sessions[attempt] = session_id
        self._note(self.attempt_ids, attempt)
        self.log("session-attached", work_id=work_id, session_id=session_id,
                 attempt=attempt)

    def expire_lease(self, work_id: str) -> None:
        item = self.work_items[work_id]
        expired = item.lease
        item.lease = None
        self.log("lease-expired", work_id=work_id, lease=expired)

    def advance_generation(self, work_id: str) -> int:
        item = self.work_items[work_id]
        item.generation += 1
        item.lease = f"lease-g{item.generation}"
        self._note(self.generations_seen, item.generation)
        self.log("generation-advanced", work_id=work_id,
                 generation=item.generation, lease=item.lease)
        return item.generation

    def complete_work(self, work_id: str, session_id: str, attempt: int) -> None:
        self.work_items[work_id].status = "completed"
        self.log("outcome-accepted", work_id=work_id, session_id=session_id,
                 attempt=attempt)

    # destination operations

    def apply_effect(self, work_id: str, session_id: str, effect_id: str,
                     payload: str, attempt: int) -> tuple[Mutation, bool]:
        self._note(self.effect_ids, effect_id)
        self._note(self.attempt_ids, attempt)
        mutation, created = self.destination.apply(effect_id, payload)
        kind = "effect-applied" if created else "effect-deduplicated"
        self.log(kind, work_id=work_id, session_id=session_id,
                 effect_id=effect_id, mutation_id=mutation.mutation_id,
                 receipt=mutation.receipt, attempt=attempt)
        return mutation, created

    def prepare_artifact(self, work_id: str, generation: int,
                         session_id: str) -> tuple[str, int | None]:
        """Compute the artifact for a generation and record what the caller
        observed as the destination's current generation at prepare time.
        The unfenced publisher later checks against that possibly stale
        observation."""
        artifact_id = f"artifact-g{generation}"
        self._note(self.artifact_ids, artifact_id)
        current = self.destination.artifact
        observed = current["generation"] if current else None
        self.log("artifact-prepared", work_id=work_id, session_id=session_id,
                 artifact_id=artifact_id, generation=generation,
                 observed_generation=observed)
        return artifact_id, observed

    def publish(self, work_id: str, artifact_id: str, generation: int,
                session_id: str, observed_generation: int | None = None) -> bool:
        if self.publisher_mode == "fenced":
            accepted = self.destination.compare_and_publish(artifact_id, generation)
            kind = "publish-accepted" if accepted else "publish-rejected-stale"
            self.log(kind, work_id=work_id, session_id=session_id,
                     artifact_id=artifact_id, generation=generation)
            return accepted
        # Unfenced: caller-side check against its own earlier observation,
        # then an unconditional write. The check can pass on stale data.
        if observed_generation is None or generation > observed_generation:
            self.destination.overwrite(artifact_id, generation)
            self.log("publish-unfenced-applied", work_id=work_id,
                     session_id=session_id, artifact_id=artifact_id,
                     generation=generation,
                     observed_generation=observed_generation)
            return True
        self.log("publish-unfenced-skipped", work_id=work_id,
                 session_id=session_id, artifact_id=artifact_id,
                 generation=generation, observed_generation=observed_generation)
        return False

    # delivery

    def emit_event(self, kind: str, apply_fn, **detail) -> bool:
        """Emit one event on the bus. Delivered events run apply_fn against
        the ledger; a dropped event runs nothing."""
        if self.bus.deliver():
            apply_fn()
            self.log(f"{kind}-delivered", **detail)
            return True
        self.log(f"{kind}-dropped", **detail)
        return False

    # reconciler

    def reconcile(self, work_id: str) -> list[str]:
        """Read destination state and repair the ledger to match it."""
        repaired = []
        for mutation in self.destination.mutations:
            record = self.effect_ledger.get(mutation.effect_id)
            if record is None or record.get("receipt") != mutation.receipt:
                self.effect_ledger[mutation.effect_id] = {
                    "status": "reconciled",
                    "receipt": mutation.receipt,
                }
                repaired.append(mutation.effect_id)
        item = self.work_items[work_id]
        if self.destination.mutations and item.status != "completed":
            item.status = "completed"
        self.log("reconciled", work_id=work_id, repaired=repaired)
        return repaired

    # snapshots

    def state_snapshot(self) -> dict:
        return {
            "work_items": {
                wid: {"work_id": w.work_id, "status": w.status,
                      "generation": w.generation, "lease": w.lease}
                for wid, w in self.work_items.items()
            },
            "claims": {
                wid: {"claim_id": c.claim_id, "work_id": c.work_id,
                      "lease": c.lease, "session_ids": list(c.session_ids)}
                for wid, c in self.claims.items()
            },
            "sessions": {
                sid: {"session_id": s.session_id, "work_id": s.work_id,
                      "worker_id": s.worker_id, "alive": s.alive}
                for sid, s in self.sessions.items()
            },
            "effect_ledger": {k: dict(v) for k, v in self.effect_ledger.items()},
        }

    def effects_snapshot(self) -> dict:
        return {
            "semantics": self.destination.semantics,
            "mutations": [
                {"mutation_id": m.mutation_id, "effect_id": m.effect_id,
                 "payload": m.payload, "receipt": m.receipt}
                for m in self.destination.mutations
            ],
            "artifact": dict(self.destination.artifact)
            if self.destination.artifact else None,
        }

    def executors_snapshot(self) -> dict:
        return {
            "workers": [
                {"worker_id": w.worker_id, "session_ids": list(w.session_ids)}
                for w in self.workers.values() if w.alive
            ],
            "sessions": [
                {"session_id": s.session_id, "work_id": s.work_id,
                 "worker_id": s.worker_id}
                for s in self.sessions.values() if s.alive
            ],
        }

    def identity_snapshot(self) -> dict:
        return {
            "work_ids": sorted(self.work_items),
            "generations": list(self.generations_seen),
            "attempt_ids": list(self.attempt_ids),
            "session_ids": sorted(self.sessions),
            "worker_ids": sorted(self.workers),
            "claim_ids": sorted(c.claim_id for c in self.claims.values()),
            "effect_ids": list(self.effect_ids),
            "artifact_ids": list(self.artifact_ids),
            "mutation_ids": [m.mutation_id for m in self.destination.mutations],
            "receipt_ids": [m.receipt for m in self.destination.mutations],
            "attempt_to_session": {
                str(attempt): sid
                for attempt, sid in sorted(self.attempt_sessions.items())
            },
        }
