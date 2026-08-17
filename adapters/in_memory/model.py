"""Tiny in-memory factory used by the reliability drills.

Deterministic by construction: time is one integer tick that advances only
when an event is appended to the log, and identifiers come from counters.
No wall-clock reads, no randomness, no threads.

Three layers that real factories often conflate are kept separate here:

  ledger       what the application believes (work items, claims, sessions,
               effect records)
  destination  external ground truth (mutations, the published artifact)
  delivery     the event bus between them, which can drop events

A fourth concern crosses all three: the command boundary a caller talks to,
which returns an outcome for a request. Its two classification paths
(classify_by_readback and classify_by_dispatch) are the difference between
reporting the destination and reporting one's own dispatch.

A fifth sits beside the ledger: a derived view (an index, cache, or
projection) built by consuming the source's history over the same droppable
delivery layer. Its two query paths (query_view_lag_checked and
query_view_index_only) are the difference between an answer bounded by the
view's distance from the source and an answer bounded by nothing.

Basis note: the layer split mirrors the guarantee split observed in the
Temporal experiment series (local observation,
temporallab2026:docs/guarantees.md): completion cardinality at the
orchestrator is not effect cardinality at the destination, and the ledger
can lag the destination.

Fencing model: in fenced mode the destination validates a presented
generation against current authoritative ownership as one atomic step.
The simulator models the ledger and the destination's fence register as a
single atomic authority boundary, which is the contract AUTH-002 requires
of a real destination.
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


@dataclass
class DerivedView:
    """A read model built by consuming the source's ordered history.

    consumed_position is the published position: how far into the source's
    history this view has been updated. None means the view publishes no
    position at all, which is a different state from being current and is
    why the field is nullable rather than defaulted to zero. max_lag is the
    freshness contract the view declares to its readers, in source
    positions.
    """

    view_id: str
    max_lag: int = 0
    consumed_position: int | None = None
    entries: list[str] = field(default_factory=list)
    indexer_started: bool = False


@dataclass
class Request:
    """One request as it crosses a command boundary.

    requested_effects is what the caller asked to be durably present.
    accepted records that the boundary durably took the request on; failed
    records that the application leg reported an error after acceptance;
    observation_ok records whether the boundary could read the destination
    back at classification time. outcome and terminal are what the boundary
    returned synchronously and what it later recorded, which are two
    different things and are stored separately for that reason.
    """

    request_id: str
    work_id: str
    requested_effects: list[str]
    accepted: bool = False
    failed: bool = False
    observation_ok: bool = True
    outcome: str | None = None
    terminal: dict | None = None


# Outcomes that describe a settled request. The other three
# (requested-and-queued, partially-applied,
# unknown-because-observation-failed) leave work owed to the caller.
TERMINAL_OUTCOMES = (
    "requested-and-applied",
    "rejected-before-mutation",
    "failed-after-mutation",
)

# Outcomes a query against a derived view can return. results-complete is
# the only one that answers the question; the other two say why the view
# cannot answer it within the freshness the query requires.
VIEW_QUERY_OUTCOMES = (
    "results-complete",
    "lag-exceeded",
    "lag-unknown",
)


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

    def compare_and_publish(self, artifact_id: str, generation: int,
                            current_generation: int) -> bool:
        """Atomic compare at the destination (fenced path): the presented
        generation must equal current authoritative ownership. Comparing
        against prior artifact history instead would accept a stale writer
        whenever no artifact exists yet."""
        if generation != current_generation:
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
        self.requests: dict[str, Request] = {}
        self.views: dict[str, DerivedView] = {}
        self.attempt_sessions: dict[int, str] = {}
        self.bus = EventBus()
        self.destination = Destination(destination_semantics)
        self.publisher_mode = publisher_mode
        self.reconciler_enabled = reconciler_enabled
        self._session_counter = 0
        self.attempt_ids: list[int] = []
        self.effect_ids: list[str] = []
        self.artifact_ids: list[str] = []
        self.request_ids: list[str] = []
        self.view_ids: list[str] = []
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

    def complete_work(self, work_id: str, session_id: str, attempt: int,
                      generation: int | None = None) -> bool:
        """Record completion. Fenced mode validates the presented generation
        against current ownership; completion is an authoritative mutation
        and the same fence applies to it as to publication."""
        item = self.work_items[work_id]
        presented = item.generation if generation is None else generation
        if self.publisher_mode == "fenced" and presented != item.generation:
            self.log("completion-rejected-stale", work_id=work_id,
                     session_id=session_id, attempt=attempt,
                     generation=presented,
                     current_generation=item.generation)
            return False
        item.status = "completed"
        self.log("outcome-accepted", work_id=work_id, session_id=session_id,
                 attempt=attempt, generation=presented)
        return True

    # command boundary

    def accept_request(self, request_id: str, work_id: str,
                       effect_ids: list[str]) -> Request:
        """Durably accept a request at the boundary.

        Acceptance is a record that the request exists and what it asked
        for. It is deliberately not a claim that anything reached the
        destination, which is the distinction the classification below is
        built to preserve.
        """
        request = Request(request_id=request_id, work_id=work_id,
                          requested_effects=list(effect_ids), accepted=True)
        self.requests[request_id] = request
        self._note(self.request_ids, request_id)
        self.log("request-accepted", request_id=request_id, work_id=work_id,
                 requested_effects=list(effect_ids))
        return request

    def reject_request(self, request_id: str, work_id: str,
                       effect_ids: list[str], reason: str) -> Request:
        """Refuse a request before any mutation reaches the destination.

        The record exists so the refusal is classifiable and countable; the
        boundary is entitled to rejected-before-mutation only because the
        refusal happened here, ahead of the application leg.
        """
        request = Request(request_id=request_id, work_id=work_id,
                          requested_effects=list(effect_ids), accepted=False)
        self.requests[request_id] = request
        self._note(self.request_ids, request_id)
        self.log("request-rejected", request_id=request_id, work_id=work_id,
                 requested_effects=list(effect_ids), reason=reason)
        return request

    def classify_by_readback(self, request_id: str) -> str:
        """Classify the request's postcondition by reading the destination.

        Six outcomes, in the order the boundary can rule them out. A
        refusal that never reached the destination is the only one that
        licenses an unchanged retry; a failed observation is a legitimate
        answer rather than a defect, and is returned in preference to a
        guess.
        """
        request = self.requests[request_id]
        applied = {m.effect_id for m in self.destination.mutations}
        landed = [e for e in request.requested_effects if e in applied]
        absent = [e for e in request.requested_effects if e not in applied]
        if not request.accepted:
            outcome = "rejected-before-mutation"
        elif not request.observation_ok:
            outcome = "unknown-because-observation-failed"
        elif not absent:
            outcome = "requested-and-applied"
        elif request.failed:
            # A reported failure that left part of the set at the
            # destination is not retryable unchanged. A reported failure
            # that left nothing there is still an accepted request owing a
            # terminal record, which is what queued means here.
            outcome = "failed-after-mutation" if landed else "requested-and-queued"
        elif landed:
            outcome = "partially-applied"
        else:
            outcome = "requested-and-queued"
        request.outcome = outcome
        self.log("outcome-classified", request_id=request_id, outcome=outcome,
                 basis="destination-readback", landed=landed, absent=absent)
        return outcome

    def classify_by_dispatch(self, request_id: str) -> str:
        """Classify from the boundary's own outbound call (the unsafe path).

        The boundary accepted the request, so it reports success. Nothing
        here reads the destination, which is why the returned outcome can
        be success-shaped over an empty destination.
        """
        request = self.requests[request_id]
        outcome = ("requested-and-applied" if request.accepted
                   else "rejected-before-mutation")
        request.outcome = outcome
        self.log("outcome-classified", request_id=request_id, outcome=outcome,
                 basis="own-dispatch-result")
        return outcome

    def record_request_terminal(self, request_id: str, state: str,
                                reason: str) -> dict:
        """Write the durable terminal record a returned outcome owes.

        The synchronous outcome is a return value the caller may have
        discarded; this is the record a later scan can find.
        """
        request = self.requests[request_id]
        record = {"state": state, "reason": reason}
        request.terminal = record
        self.log("request-terminal", request_id=request_id, state=state,
                 reason=reason)
        return record

    # derived views

    def source_position(self) -> int:
        """How far the source of truth has advanced.

        Derived from the destination's own mutation list on every call, so
        the source position cannot drift from the source. A view's position
        is a claim the view makes; this one is a count of what exists.
        """
        return len(self.destination.mutations)

    def add_view(self, view_id: str, max_lag: int = 0) -> DerivedView:
        view = DerivedView(view_id=view_id, max_lag=max_lag)
        self.views[view_id] = view
        self._note(self.view_ids, view_id)
        self.log("view-added", view_id=view_id, max_lag=max_lag)
        return view

    def index_view_entry(self, view_id: str, effect_id: str) -> DerivedView:
        """Consume one source record into the view.

        The entry and the published position advance together here. A real
        indexer that advances its position without writing the entry would
        satisfy every position check while answering queries wrongly, which
        is why the query oracle recounts entries against the destination
        rather than trusting the position.
        """
        view = self.views[view_id]
        view.indexer_started = True
        if effect_id not in view.entries:
            view.entries.append(effect_id)
        view.consumed_position = self.source_position()
        self.log("view-indexed", view_id=view_id, effect_id=effect_id,
                 consumed_position=view.consumed_position,
                 source_position=self.source_position())
        return view

    def view_lag(self, view_id: str) -> int | None:
        """Source position minus published position, or None if the view
        publishes no position."""
        view = self.views[view_id]
        if view.consumed_position is None:
            return None
        return self.source_position() - view.consumed_position

    def query_view_lag_checked(self, view_id: str, term: str,
                               max_lag: int | None = None) -> dict:
        """Answer a query only within the freshness the caller requires.

        The requirement defaults to the view's declared contract. A query
        that cannot be answered within it returns lag-exceeded carrying both
        positions, rather than the subset of the source the view happens to
        hold.
        """
        view = self.views[view_id]
        required = view.max_lag if max_lag is None else max_lag
        lag = self.view_lag(view_id)
        matches = [e for e in view.entries if e == term]
        if lag is None:
            outcome, answered = "lag-unknown", None
        elif lag > required:
            outcome, answered = "lag-exceeded", None
        else:
            outcome, answered = "results-complete", matches
        self.log("view-queried", view_id=view_id, term=term, outcome=outcome,
                 basis="published-position-compared-to-source",
                 matches=answered, lag=lag, required_lag=required,
                 consumed_position=view.consumed_position,
                 source_position=self.source_position())
        return {"outcome": outcome, "matches": answered, "lag": lag,
                "required_lag": required}

    def query_view_index_only(self, view_id: str, term: str) -> dict:
        """Answer from whatever the view holds (the unsafe path).

        Nothing here reads the source position, so the result set is
        complete with respect to the index and says nothing about the
        source. An empty answer is returned as a successful absence.
        """
        view = self.views[view_id]
        matches = [e for e in view.entries if e == term]
        self.log("view-queried", view_id=view_id, term=term,
                 outcome="results-complete", basis="index-contents-only",
                 matches=matches, lag=None, required_lag=None,
                 consumed_position=view.consumed_position,
                 source_position=self.source_position())
        return {"outcome": "results-complete", "matches": matches,
                "lag": None, "required_lag": None}

    def view_health_by_position(self, view_id: str) -> dict:
        """Report freshness from the same two positions the query path uses."""
        view = self.views[view_id]
        lag = self.view_lag(view_id)
        if lag is None:
            state = "unknown"
        else:
            state = "fresh" if lag <= view.max_lag else "stale"
        record = {"view_id": view_id, "state": state, "lag": lag,
                  "basis": "published-position-compared-to-source"}
        self.log("view-health-reported", **record)
        return record

    def view_health_by_liveness(self, view_id: str) -> dict:
        """Report freshness from the indexer running (the unsafe path).

        This surface never computes a lag, so it cannot report one. It is
        the second indicator that disagrees with the first while both look
        like health.
        """
        view = self.views[view_id]
        state = "fresh" if view.indexer_started else "unknown"
        record = {"view_id": view_id, "state": state, "lag": None,
                  "basis": "indexer-liveness"}
        self.log("view-health-reported", **record)
        return record

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

    def prepare_merge(self, parent_id: str, children: list[str],
                      generation: int, session_id: str) -> tuple[str, int | None]:
        """Derive a merged artifact's identity from the child set it folds.

        Two joins that folded the same children produce the same identity, and
        two joins that folded different children produce different ones, so a
        retried join is a duplicate write of a known identity rather than a
        second merged artifact. As with prepare_artifact, the destination
        generation the caller observed here is what the unfenced publication
        path later checks against.
        """
        folded = sorted(children)
        artifact_id = ("merge-" + "+".join(folded)) if folded else "merge-empty"
        self._note(self.artifact_ids, artifact_id)
        current = self.destination.artifact
        observed = current["generation"] if current else None
        self.log("merge-prepared", work_id=parent_id, session_id=session_id,
                 artifact_id=artifact_id, generation=generation,
                 folded_children=folded, observed_generation=observed)
        return artifact_id, observed

    def derive_dispositions(self, child_ids: list[str]) -> dict[str, str]:
        """Fold each child's own evidence into a disposition.

        published: the child's effect is committed at the destination and its
        completion is recorded. blocked: the child holds no lease and has not
        completed, so someone must act on it. undispositioned otherwise, which
        is the state that must block a join rather than be dropped from it.
        Derived on every call from the durable record, never cached.
        """
        applied = {m.effect_id for m in self.destination.mutations}
        dispositions = {}
        for child_id in sorted(child_ids):
            item = self.work_items[child_id]
            if item.status == "completed" and f"eff-{child_id}" in applied:
                dispositions[child_id] = "published"
            elif item.lease is None and item.status != "completed":
                dispositions[child_id] = "blocked"
            else:
                dispositions[child_id] = "undispositioned"
        self.log("dispositions-derived", dispositions=dispositions)
        return dispositions

    def publish(self, work_id: str, artifact_id: str, generation: int,
                session_id: str, observed_generation: int | None = None) -> bool:
        if self.publisher_mode == "fenced":
            current = self.work_items[work_id].generation
            accepted = self.destination.compare_and_publish(
                artifact_id, generation, current)
            kind = "publish-accepted" if accepted else "publish-rejected-stale"
            self.log(kind, work_id=work_id, session_id=session_id,
                     artifact_id=artifact_id, generation=generation,
                     current_generation=current)
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
            "requests": {
                rid: {"request_id": r.request_id, "work_id": r.work_id,
                      "requested_effects": list(r.requested_effects),
                      "accepted": r.accepted, "outcome": r.outcome,
                      "terminal": dict(r.terminal) if r.terminal else None}
                for rid, r in self.requests.items()
            },
            "views": {
                vid: {"view_id": v.view_id, "max_lag": v.max_lag,
                      "consumed_position": v.consumed_position,
                      "entries": list(v.entries),
                      "indexer_started": v.indexer_started,
                      "source_position": self.source_position()}
                for vid, v in self.views.items()
            },
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
            "request_ids": list(self.request_ids),
            "view_ids": list(self.view_ids),
            "mutation_ids": [m.mutation_id for m in self.destination.mutations],
            "receipt_ids": [m.receipt for m in self.destination.mutations],
            "attempt_to_session": {
                str(attempt): sid
                for attempt, sid in sorted(self.attempt_sessions.items())
            },
        }
